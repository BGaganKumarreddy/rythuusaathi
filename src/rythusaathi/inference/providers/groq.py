"""Groq adapter using Microsoft Agent Framework's OpenAI-compatible client."""

from __future__ import annotations

import asyncio
import email.utils
import json
import math
import os
import random
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from threading import Lock
from typing import Callable, TypeGuard

from agent_framework import ChatOptions, Content, Message
from openai import (
    APIResponseValidationError,
    APIStatusError,
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)
from agent_framework.openai import OpenAIChatCompletionClient

from ..contracts import (
    InferenceRequest,
    InferenceDiagnostic,
    InferenceDiagnosticCode,
    InferenceStage,
    InferenceTokenUsage,
    ProviderCredential,
    ProviderFailure,
    ProviderFailureKind,
    ProviderResponse,
    RateLimitScope,
    RateLimitSnapshot,
    sanitized_chat_response,
)


GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL_ID = "openai/gpt-oss-20b"
_MAX_RATE_LIMIT_RETRIES = 1
_MAX_AUTOMATIC_RETRY_WAIT_SECONDS = 2.0
_RETRY_BACKOFF_BASE_SECONDS = 0.25
_RETRY_JITTER_SECONDS = 0.10
_MAX_DIAGNOSTIC_DURATION_SECONDS = 31_536_000.0
_RATE_RESET_COMPONENT = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h|d)")


def _is_chat_options(value: object) -> TypeGuard[ChatOptions]:
    """Validate ChatOptions' documented TypedDict runtime representation.

    Agent Framework types ``ChatOptions`` as a TypedDict, which is an ordinary
    ``dict`` at runtime. TypedDict does not support isinstance checks; checking
    dict plus string keys is the corresponding runtime validation.
    """
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


@dataclass(frozen=True, slots=True)
class GroqProviderConfig:
    model_id: str = DEFAULT_MODEL_ID
    timeout_seconds: float = 30.0
    max_tool_iterations: int = 5
    max_tool_calls: int = 8
    tpm_limit_tokens: int = 8000
    tpm_headroom_tokens: int = 800
    default_max_output_tokens: int = 1024

    def __post_init__(self) -> None:
        if not isinstance(self.model_id, str) or not self.model_id.strip():
            raise ValueError("model_id must not be empty")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive")
        limits = (self.max_tool_iterations, self.max_tool_calls)
        if any(isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 for limit in limits):
            raise ValueError("tool invocation limits must be positive")
        if (isinstance(self.tpm_limit_tokens, bool)
                or not isinstance(self.tpm_limit_tokens, int)
                or self.tpm_limit_tokens < 1):
            raise ValueError("tpm_limit_tokens must be a positive integer")
        if (isinstance(self.tpm_headroom_tokens, bool)
                or not isinstance(self.tpm_headroom_tokens, int)
                or not 0 <= self.tpm_headroom_tokens < self.tpm_limit_tokens):
            raise ValueError("tpm_headroom_tokens must be smaller than the TPM limit")
        if (isinstance(self.default_max_output_tokens, bool)
                or not isinstance(self.default_max_output_tokens, int)
                or self.default_max_output_tokens < 1):
            raise ValueError("default_max_output_tokens must be positive")

    @classmethod
    def from_environment(cls) -> "GroqProviderConfig":
        """Load non-secret deployment configuration only; never reads API keys."""
        raw_timeout = os.environ.get("GROQ_TIMEOUT_SECONDS", "30")
        try:
            timeout = float(raw_timeout)
        except (TypeError, ValueError):
            raise ValueError("GROQ_TIMEOUT_SECONDS must be numeric") from None
        try:
            tpm_limit = int(os.environ.get("GROQ_TPM_LIMIT_TOKENS", "8000"))
            tpm_headroom = int(os.environ.get("GROQ_TPM_HEADROOM_TOKENS", "800"))
            default_output = int(os.environ.get("GROQ_MAX_OUTPUT_TOKENS", "1024"))
        except (TypeError, ValueError):
            raise ValueError("Groq token budget settings must be integers") from None
        return cls(
            model_id=os.environ.get("GROQ_MODEL_ID", DEFAULT_MODEL_ID),
            timeout_seconds=timeout,
            tpm_limit_tokens=tpm_limit,
            tpm_headroom_tokens=tpm_headroom,
            default_max_output_tokens=default_output,
        )


class GroqProvider:
    """One provider call with explicit credential injection and no fallback.

    The API key is not read from environment, included in request messages, or
    returned to callers. Credential authorization/retrieval is owned by the
    protected backend credential service, which is intentionally not part of
    this provider implementation.
    """

    provider_id = "groq"

    # The currently supported Agent Framework OpenAI client owns function
    # invocation when ``tools`` is enabled. That loop is therefore part of
    # this adapter call. Keeping it preserves the established tested behavior;
    # a separate tool-orchestration API would require changing its caller.
    _UNSAFE_OPTIONS = frozenset({
        "api_key", "authorization", "headers", "extra_headers", "default_headers",
        "base_url", "endpoint", "client", "client_kwargs", "client_config",
        "http_client", "async_client", "credential", "credentials",
    })
    _GROQ_UNSUPPORTED_OPTIONS = frozenset({"presence_penalty", "logit_bias"})

    def __init__(
        self,
        config: GroqProviderConfig | None = None,
        *,
        sdk_client_factory: Callable[[str, str, float], AsyncOpenAI] | None = None,
    ) -> None:
        self.config = config or GroqProviderConfig.from_environment()
        self._sdk_client_factory = sdk_client_factory or self._make_sdk_client
        self._budget_lock = Lock()
        self._budget_events: list[list[float | int]] = []
        self._budget_blocked_until = 0.0

    @staticmethod
    def _make_sdk_client(api_key: str, base_url: str, timeout: float) -> AsyncOpenAI:
        return AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=0,
        )

    async def complete(
        self, request: InferenceRequest, credential: ProviderCredential
    ) -> ProviderResponse:
        if not isinstance(request, InferenceRequest):
            raise ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE)
        if not isinstance(credential, ProviderCredential):
            raise ProviderFailure(ProviderFailureKind.AUTHENTICATION)
        if (
            not isinstance(request.messages, Sequence)
            or isinstance(request.messages, (str, bytes))
            or any(not isinstance(message, Message) for message in request.messages)
        ):
            raise ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE)
        options = request.options
        if options is not None:
            if not _is_chat_options(options):
                raise ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE)
            normalized_names = {key.lower().replace("-", "_") for key in options}
            if normalized_names & self._UNSAFE_OPTIONS:
                raise ProviderFailure(ProviderFailureKind.SENSITIVE_INPUT)
            if normalized_names & self._GROQ_UNSUPPORTED_OPTIONS:
                raise ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE)
            allowed_options = set(ChatOptions.__annotations__)
            if any(key not in allowed_options for key in options):
                raise ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE)
            requested_model = options.get("model", self.config.model_id)
            if not isinstance(requested_model, str) or not requested_model.strip():
                raise ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE)
            output_limit = options.get("max_tokens", self.config.default_max_output_tokens)
            if (isinstance(output_limit, bool) or not isinstance(output_limit, int)
                    or output_limit < 1):
                raise ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE)
            call_options = dict(options)
            call_options["max_tokens"] = output_limit
            resp_fmt = call_options.get("response_format")
            if isinstance(resp_fmt, dict) and resp_fmt.get("type") == "json_schema":
                call_options["response_format"] = {"type": "json_object"}
        else:
            requested_model = self.config.model_id
            output_limit = self.config.default_max_output_tokens
            call_options = {"max_tokens": output_limit}
        for message in request.messages:
            if not isinstance(message.contents, Sequence) or isinstance(message.contents, (str, bytes)):
                raise ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE)
            if any(not isinstance(item, (Content, str, Mapping)) for item in message.contents):
                raise ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE)
        if credential._request_contains_secret(request):
            raise ProviderFailure(ProviderFailureKind.SENSITIVE_INPUT)

        estimated_input_tokens = estimate_input_tokens(request.messages)
        budget_limit = self.config.tpm_limit_tokens - self.config.tpm_headroom_tokens
        budget_cost = estimated_input_tokens + output_limit
        reservation, remaining, cooldown = (
            (None, self._remaining_budget(), 0.0)
            if output_limit > self.config.default_max_output_tokens
            else self._reserve_budget(budget_cost)
        )
        if reservation is None:
            code = (InferenceDiagnosticCode.GROQ_RATE_LIMITED if cooldown > 0
                    else InferenceDiagnosticCode.GROQ_TPM_BUDGET_EXCEEDED)
            raise ProviderFailure(
                ProviderFailureKind.RATE_LIMIT,
                InferenceDiagnostic(
                    code=code,
                    stage=InferenceStage.REQUEST_VALIDATION,
                    model_id=requested_model,
                    rate_limit_scope=RateLimitScope.TOKENS_PER_MINUTE,
                    retry_after_seconds=cooldown or None,
                    agent_name=request.agent_name,
                    estimated_input_tokens=estimated_input_tokens,
                    output_token_limit=output_limit,
                    budget_limit_tokens=budget_limit,
                    budget_remaining_tokens=remaining,
                    budget_decision="rejected",
                    retry_decision="budget_rejected",
                    correlation_id=request.correlation_id,
                ),
            )
        reservations = [reservation]

        # Consume only after validating every caller-controlled field, directly
        # before constructing the provider client. No env fallback exists.
        secret = credential._consume_for_provider()
        sdk_client: AsyncOpenAI | None = None
        response = None
        failure_kind: ProviderFailureKind | None = None
        failure_diagnostic: InferenceDiagnostic | None = None
        correlation_id = request.correlation_id
        retries_attempted = 0
        prior_rate_limits: list[RateLimitSnapshot] = []
        try:
            sdk_client = self._sdk_client_factory(
                secret,
                GROQ_BASE_URL,
                self.config.timeout_seconds,
            )
            framework_client = OpenAIChatCompletionClient(
                model=requested_model,
                async_client=sdk_client,
                function_invocation_configuration={
                    "enabled": True,
                    "max_iterations": self.config.max_tool_iterations,
                    "max_function_calls": self.config.max_tool_calls,
                    "max_duration_seconds": self.config.timeout_seconds,
                    "include_detailed_errors": False,
                },
            )
            while True:
                try:
                    result = await framework_client.get_response(
                        request.messages,
                        options=call_options,
                    )
                    response = sanitized_chat_response(result, secret)
                    break
                except Exception as exc:
                    if isinstance(exc, ProviderFailure):
                        attempt_kind = exc.kind
                        attempt_diagnostic = replace(
                            exc.diagnostic,
                            correlation_id=correlation_id,
                            model_id=exc.diagnostic.model_id or requested_model,
                            retries_attempted=retries_attempted,
                            prior_rate_limits=tuple(prior_rate_limits),
                            agent_name=request.agent_name,
                            estimated_input_tokens=estimated_input_tokens,
                            output_token_limit=output_limit,
                            budget_limit_tokens=budget_limit,
                            budget_remaining_tokens=remaining,
                            budget_decision="provider_rejected",
                            retry_decision=("retried" if retries_attempted else "not_retried"),
                        )
                    else:
                        attempt_kind = _classify_exception_chain(exc)
                        attempt_diagnostic = _diagnostic_for_exception(
                            exc,
                            attempt_kind,
                            requested_model,
                            correlation_id=correlation_id,
                            retries_attempted=retries_attempted,
                            prior_rate_limits=tuple(prior_rate_limits),
                        )
                        attempt_diagnostic = replace(
                            attempt_diagnostic,
                            agent_name=request.agent_name,
                            estimated_input_tokens=estimated_input_tokens,
                            output_token_limit=output_limit,
                            budget_limit_tokens=budget_limit,
                            budget_remaining_tokens=remaining,
                            budget_decision="provider_rejected",
                            retry_decision=("retried" if retries_attempted else "not_retried"),
                        )

                    retry_delay = None
                    if (attempt_kind is ProviderFailureKind.RATE_LIMIT
                            and retries_attempted < _MAX_RATE_LIMIT_RETRIES
                            and _request_is_safe_to_retry(request)):
                        retry_delay = _rate_limit_retry_delay(attempt_diagnostic)
                    if retry_delay is not None:
                        current_snapshot = _snapshot_from_diagnostic(attempt_diagnostic)
                        if current_snapshot is not None:
                            prior_rate_limits.append(current_snapshot)
                        retries_attempted += 1
                        retry_reservation, retry_remaining, cooldown = self._reserve_budget(
                            budget_cost, ignore_cooldown=True
                        )
                        if retry_reservation is None:
                            failure_kind = ProviderFailureKind.RATE_LIMIT
                            failure_diagnostic = replace(
                                attempt_diagnostic,
                                retries_attempted=retries_attempted - 1,
                                prior_rate_limits=tuple(prior_rate_limits),
                                budget_remaining_tokens=retry_remaining,
                                budget_decision="rejected",
                                retry_decision="budget_rejected",
                            )
                            break
                        reservations.append(retry_reservation)
                        remaining = retry_remaining
                        await asyncio.sleep(retry_delay)
                        continue

                    failure_kind = attempt_kind
                    failure_diagnostic = attempt_diagnostic
                    break
        except Exception as exc:
            # Framework/provider errors are reduced to typed metadata. Never
            # export response bodies, SDK messages, or request headers.
            if isinstance(exc, ProviderFailure):
                failure_kind = exc.kind
                failure_diagnostic = replace(
                    exc.diagnostic,
                    correlation_id=correlation_id,
                    model_id=exc.diagnostic.model_id or requested_model,
                    retries_attempted=retries_attempted,
                    prior_rate_limits=tuple(prior_rate_limits),
                )
            else:
                failure_kind = _classify_exception_chain(exc)
                failure_diagnostic = _diagnostic_for_exception(
                    exc,
                    failure_kind,
                    requested_model,
                    correlation_id=correlation_id,
                    retries_attempted=retries_attempted,
                    prior_rate_limits=tuple(prior_rate_limits),
                )
        finally:
            if sdk_client is not None:
                try:
                    await sdk_client.close()
                except Exception:
                    if failure_kind is None:
                        failure_kind = ProviderFailureKind.PROVIDER_FAILURE
                        failure_diagnostic = InferenceDiagnostic(
                            code=InferenceDiagnosticCode.GROQ_PROVIDER_FAILURE,
                            stage=InferenceStage.PROVIDER_TRANSPORT,
                            model_id=requested_model,
                        )
            secret = ""

        if failure_diagnostic is not None:
            failure_diagnostic = replace(
                failure_diagnostic,
                correlation_id=correlation_id,
                model_id=failure_diagnostic.model_id or requested_model,
                agent_name=request.agent_name,
                estimated_input_tokens=estimated_input_tokens,
                output_token_limit=output_limit,
                budget_limit_tokens=budget_limit,
                budget_remaining_tokens=self._remaining_budget(),
                budget_decision=failure_diagnostic.budget_decision or "provider_rejected",
                retry_decision=failure_diagnostic.retry_decision or (
                    "retried" if retries_attempted else "not_retried"
                ),
            )

        # Raise only after leaving the exception handler, so the public error
        # has no __context__ or __cause__ referencing the SDK/framework error.
        if failure_kind is not None:
            for item in reservations:
                self._settle_budget(item, None)
            if failure_diagnostic is not None and failure_diagnostic.http_status == 429:
                self._set_cooldown(failure_diagnostic)
            raise ProviderFailure(failure_kind, failure_diagnostic) from None
        if response is None:
            raise ProviderFailure(
                ProviderFailureKind.MALFORMED_RESPONSE,
                InferenceDiagnostic(
                    code=InferenceDiagnosticCode.GROQ_MALFORMED_RESPONSE,
                    stage=InferenceStage.RESPONSE_VALIDATION,
                    model_id=requested_model,
                ),
            ) from None
        usage_values = extract_usage_counts(response)
        if usage_values[2] is not None:
            self._settle_budget(reservations[-1], usage_values[2])
        usage = InferenceTokenUsage(
            agent_name=request.agent_name,
            model_id=requested_model,
            correlation_id=correlation_id,
            estimated_input_tokens=estimated_input_tokens,
            actual_input_tokens=usage_values[0],
            actual_output_tokens=usage_values[1],
            actual_total_tokens=usage_values[2],
            output_token_limit=output_limit,
            budget_limit_tokens=budget_limit,
            budget_remaining_tokens=self._remaining_budget(),
            budget_decision="completed" if usage_values[2] is not None else "estimate_only",
            retries_attempted=retries_attempted,
            retry_decision="retried" if retries_attempted else "not_retried",
        )
        return ProviderResponse(
            provider=self.provider_id,
            model_id=requested_model,
            response=response,
            usage=usage,
        )

    def _reserve_budget(self, token_count: int, *,
                        ignore_cooldown: bool = False) -> tuple[list[float | int] | None, int, float]:
        now = time.monotonic()
        limit = self.config.tpm_limit_tokens - self.config.tpm_headroom_tokens
        with self._budget_lock:
            self._budget_events = [event for event in self._budget_events
                                   if now - float(event[0]) < 60.0]
            used = sum(int(event[1]) for event in self._budget_events)
            remaining = max(0, limit - used)
            cooldown = max(0.0, self._budget_blocked_until - now)
            if (not ignore_cooldown and cooldown > 0) or token_count > remaining:
                return None, remaining, cooldown
            event: list[float | int] = [now, token_count]
            self._budget_events.append(event)
            return event, remaining - token_count, 0.0

    def _settle_budget(self, reservation: list[float | int], actual_tokens: int | None) -> None:
        with self._budget_lock:
            if actual_tokens is None:
                if reservation in self._budget_events:
                    self._budget_events.remove(reservation)
            else:
                reservation[1] = actual_tokens

    def reserve_budget(self, token_count: int) -> list[float | int] | None:
        """Reserve a block of tokens in the rolling budget window."""
        if not isinstance(token_count, int) or isinstance(token_count, bool) or token_count < 1:
            return None
        res, _, _ = self._reserve_budget(token_count)
        return res

    def release_reservation(self, reservation: list[float | int] | None) -> None:
        """Release a previously acquired budget reservation."""
        if reservation is not None:
            self._settle_budget(reservation, None)

    def _remaining_budget(self) -> int:
        now = time.monotonic()
        limit = self.config.tpm_limit_tokens - self.config.tpm_headroom_tokens
        with self._budget_lock:
            self._budget_events = [event for event in self._budget_events
                                   if now - float(event[0]) < 60.0]
            return max(0, limit - sum(int(event[1]) for event in self._budget_events))

    def _set_cooldown(self, diagnostic: InferenceDiagnostic) -> None:
        waits = [value for value in (
            diagnostic.retry_after_seconds,
            diagnostic.rate_limit_reset_tokens_seconds
            if diagnostic.rate_limit_scope is RateLimitScope.TOKENS_PER_MINUTE else None,
        ) if value is not None]
        if not waits:
            return
        now = time.monotonic()
        with self._budget_lock:
            self._budget_blocked_until = max(self._budget_blocked_until, now + max(waits))


def estimate_input_tokens(messages: Sequence[Message]) -> int:
    """Conservative byte heuristic with extra allowance for non-ASCII text.

    This is a preflight guard, not Groq tokenization. The provider's usage
    counters replace the reservation as soon as they are available.
    """
    serialized = json.dumps(
        [message.to_dict(exclude_none=True) for message in messages],
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    ascii_bytes = sum(1 for char in serialized if char.isascii())
    non_ascii_bytes = len(serialized.encode("utf-8")) - ascii_bytes
    return math.ceil(ascii_bytes / 4.3) + math.ceil(non_ascii_bytes / 3.0) + 12 * len(messages)


def extract_usage_counts(response: object) -> tuple[int | None, int | None, int | None]:
    """Read only validated integer usage counters from the public response."""
    usage = getattr(response, "usage_details", None)
    if not isinstance(usage, Mapping):
        return None, None, None

    def read(*names: str) -> int | None:
        for name in names:
            value = usage.get(name)
            if (isinstance(value, int) and not isinstance(value, bool)
                    and 0 <= value <= 10**9):
                return value
        return None

    input_tokens = read("input_token_count", "prompt_tokens", "input_tokens")
    output_tokens = read("output_token_count", "completion_tokens", "output_tokens")
    total_tokens = read("total_token_count", "total_tokens")
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    return input_tokens, output_tokens, total_tokens


def _looks_like_invalid_model(exc: BaseException) -> bool:
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return False
    error = body.get("error", body)
    if not isinstance(error, dict):
        return False
    # Match provider codes/types exactly. Error text is neither stable nor safe
    # for classification and can contain user or credential material.
    return error.get("code") in {"model_not_found", "invalid_model"} or error.get("type") in {
        "model_not_found", "invalid_model_error"
    }


def _classify_status_error(exc: APIStatusError) -> ProviderFailureKind:
    if exc.status_code in (401, 403):
        return ProviderFailureKind.AUTHENTICATION
    if exc.status_code == 429:
        return ProviderFailureKind.RATE_LIMIT
    if exc.status_code in (400, 404) and _looks_like_invalid_model(exc):
        return ProviderFailureKind.INVALID_MODEL
    return ProviderFailureKind.PROVIDER_FAILURE


def _classify_exception_chain(exc: BaseException) -> ProviderFailureKind:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, AuthenticationError):
            return ProviderFailureKind.AUTHENTICATION
        if isinstance(current, RateLimitError):
            return ProviderFailureKind.RATE_LIMIT
        if isinstance(current, (APITimeoutError, TimeoutError)) or (
            type(current).__name__ in {
                "TimeoutException", "ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout"
            }
            and type(current).__module__.split(".")[0] in {"httpx", "httpx2"}
        ):
            return ProviderFailureKind.TIMEOUT
        if isinstance(current, APIConnectionError) or (
            type(current).__name__ in {
                "ConnectError", "NetworkError", "ProxyError", "RemoteProtocolError",
                "ProtocolError",
            }
            and type(current).__module__.split(".")[0] in {
                "httpx", "httpx2", "httpcore", "httpcore2",
            }
        ):
            return ProviderFailureKind.NETWORK
        if isinstance(current, (APIResponseValidationError, json.JSONDecodeError)):
            return ProviderFailureKind.MALFORMED_RESPONSE
        if isinstance(current, BadRequestError):
            return (
                ProviderFailureKind.INVALID_MODEL
                if _looks_like_invalid_model(current)
                else ProviderFailureKind.PROVIDER_FAILURE
            )
        if isinstance(current, APIStatusError):
            return _classify_status_error(current)
        current = current.__cause__ or current.__context__
    return ProviderFailureKind.PROVIDER_FAILURE


def _diagnostic_for_exception(exc: BaseException, kind: ProviderFailureKind,
                              model_id: str, *, correlation_id: str,
                              retries_attempted: int,
                              prior_rate_limits: tuple[RateLimitSnapshot, ...]
                              ) -> InferenceDiagnostic:
    status = None
    status_error: APIStatusError | None = None
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, APIStatusError):
            status = current.status_code
            status_error = current
            break
        current = current.__cause__ or current.__context__

    rate_snapshot = (
        _rate_limit_snapshot(status_error)
        if status == 429 and status_error is not None else None
    )
    scope = rate_snapshot.scope if rate_snapshot is not None else None
    if kind is ProviderFailureKind.AUTHENTICATION:
        code = InferenceDiagnosticCode.GROQ_AUTHENTICATION_FAILED
    elif kind is ProviderFailureKind.INVALID_MODEL:
        code = InferenceDiagnosticCode.GROQ_INVALID_MODEL
    elif kind is ProviderFailureKind.TIMEOUT:
        code = InferenceDiagnosticCode.GROQ_TIMEOUT
    elif kind is ProviderFailureKind.NETWORK:
        code = InferenceDiagnosticCode.GROQ_NETWORK_FAILURE
    elif kind is ProviderFailureKind.RATE_LIMIT:
        code = (
            InferenceDiagnosticCode.GROQ_QUOTA_EXHAUSTED
            if scope in {
                RateLimitScope.REQUESTS_PER_DAY,
                RateLimitScope.TOKENS_PER_DAY,
                RateLimitScope.ACCOUNT_QUOTA,
                RateLimitScope.MULTIPLE,
            }
            else InferenceDiagnosticCode.GROQ_RATE_LIMITED
        )
    elif kind is ProviderFailureKind.MALFORMED_RESPONSE:
        code = InferenceDiagnosticCode.GROQ_MALFORMED_RESPONSE
    elif status is not None and 400 <= status < 500:
        code = InferenceDiagnosticCode.GROQ_REQUEST_REJECTED
    else:
        code = InferenceDiagnosticCode.GROQ_PROVIDER_FAILURE

    stage = (InferenceStage.PROVIDER_RESPONSE if status is not None else
             InferenceStage.RESPONSE_VALIDATION
             if kind is ProviderFailureKind.MALFORMED_RESPONSE else
             InferenceStage.PROVIDER_TRANSPORT)
    return InferenceDiagnostic(
        code=code,
        stage=stage,
        model_id=model_id,
        http_status=status,
        rate_limit_scope=scope,
        retry_after_seconds=(rate_snapshot.retry_after_seconds
                             if rate_snapshot is not None else None),
        rate_limit_remaining_requests=(rate_snapshot.remaining_requests
                                       if rate_snapshot is not None else None),
        rate_limit_remaining_tokens=(rate_snapshot.remaining_tokens
                                    if rate_snapshot is not None else None),
        rate_limit_reset_requests_seconds=(rate_snapshot.reset_requests_seconds
                                           if rate_snapshot is not None else None),
        rate_limit_reset_tokens_seconds=(rate_snapshot.reset_tokens_seconds
                                         if rate_snapshot is not None else None),
        retries_attempted=retries_attempted,
        prior_rate_limits=prior_rate_limits,
        correlation_id=correlation_id,
    )


def _rate_limit_snapshot(exc: APIStatusError) -> RateLimitSnapshot:
    headers = exc.response.headers
    remaining_requests = _parse_nonnegative_int(
        headers.get("x-ratelimit-remaining-requests")
    )
    remaining_tokens = _parse_nonnegative_int(
        headers.get("x-ratelimit-remaining-tokens")
    )
    reset_requests = _parse_duration_seconds(
        headers.get("x-ratelimit-reset-requests")
    )
    reset_tokens = _parse_duration_seconds(
        headers.get("x-ratelimit-reset-tokens")
    )
    scope = _rate_limit_scope(
        exc.body,
        remaining_requests=remaining_requests,
        remaining_tokens=remaining_tokens,
    )
    return RateLimitSnapshot(
        scope=scope,
        retry_after_seconds=_parse_retry_after_seconds(headers),
        remaining_requests=remaining_requests,
        remaining_tokens=remaining_tokens,
        reset_requests_seconds=reset_requests,
        reset_tokens_seconds=reset_tokens,
    )


def _parse_nonnegative_int(value: str | None) -> int | None:
    if value is None or not re.fullmatch(r"\d+", value.strip()):
        return None
    try:
        parsed = int(value)
    except (ValueError, OverflowError):
        return None
    return parsed if parsed <= 10**15 else None


def _parse_retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    milliseconds = headers.get("retry-after-ms")
    if milliseconds is not None:
        try:
            parsed_ms = float(milliseconds)
        except (TypeError, ValueError, OverflowError):
            parsed_ms = math.nan
        if math.isfinite(parsed_ms) and 0 <= parsed_ms <= _MAX_DIAGNOSTIC_DURATION_SECONDS * 1000:
            return parsed_ms / 1000

    value = headers.get("retry-after")
    if value is None:
        return None
    try:
        parsed_seconds = float(value)
    except (TypeError, ValueError, OverflowError):
        parsed_seconds = math.nan
    if math.isfinite(parsed_seconds) and 0 <= parsed_seconds <= _MAX_DIAGNOSTIC_DURATION_SECONDS:
        return parsed_seconds

    try:
        retry_at = email.utils.parsedate_to_datetime(value)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        delay = (retry_at - datetime.now(timezone.utc)).total_seconds()
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    if not math.isfinite(delay):
        return None
    return min(_MAX_DIAGNOSTIC_DURATION_SECONDS, max(0.0, delay))


def _parse_duration_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    matches = list(_RATE_RESET_COMPONENT.finditer(normalized))
    if not matches or "".join(item.group(0) for item in matches) != normalized:
        return None
    scale = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
    try:
        seconds = sum(float(item.group(1)) * scale[item.group(2)] for item in matches)
    except (ValueError, OverflowError):
        return None
    if not math.isfinite(seconds) or not 0 <= seconds <= _MAX_DIAGNOSTIC_DURATION_SECONDS:
        return None
    return seconds


def _rate_limit_scope(body: object, *, remaining_requests: int | None,
                      remaining_tokens: int | None) -> RateLimitScope:
    error: Mapping[str, object] = {}
    if isinstance(body, Mapping):
        nested = body.get("error", body)
        if isinstance(nested, Mapping):
            error = nested
    for field in ("code", "type"):
        value = error.get(field)
        if isinstance(value, str):
            normalized = value.strip().lower().replace("-", "_")
            exact = {
                "rpm": RateLimitScope.REQUESTS_PER_MINUTE,
                "requests_per_minute": RateLimitScope.REQUESTS_PER_MINUTE,
                "rpd": RateLimitScope.REQUESTS_PER_DAY,
                "requests_per_day": RateLimitScope.REQUESTS_PER_DAY,
                "tpm": RateLimitScope.TOKENS_PER_MINUTE,
                "tokens_per_minute": RateLimitScope.TOKENS_PER_MINUTE,
                "tpd": RateLimitScope.TOKENS_PER_DAY,
                "tokens_per_day": RateLimitScope.TOKENS_PER_DAY,
                "insufficient_quota": RateLimitScope.ACCOUNT_QUOTA,
                "quota_exceeded": RateLimitScope.ACCOUNT_QUOTA,
                "billing_hard_limit_reached": RateLimitScope.ACCOUNT_QUOTA,
            }.get(normalized)
            if exact is not None:
                return exact

    message = error.get("message")
    if isinstance(message, str):
        normalized_message = message.casefold()
        message_scopes = (
            (r"\brequests?\s+per\s+minute\b|\brpm\b", RateLimitScope.REQUESTS_PER_MINUTE),
            (r"\brequests?\s+per\s+day\b|\brpd\b", RateLimitScope.REQUESTS_PER_DAY),
            (r"\btokens?\s+per\s+minute\b|\btpm\b|\bitpm\b|\botpm\b",
             RateLimitScope.TOKENS_PER_MINUTE),
            (r"\btokens?\s+per\s+day\b|\btpd\b", RateLimitScope.TOKENS_PER_DAY),
        )
        for pattern, scope in message_scopes:
            if re.search(pattern, normalized_message):
                return scope
        if (("quota" in normalized_message and any(term in normalized_message for term in (
                "insufficient", "exceed", "exhaust", "current quota",
            ))) or ("billing" in normalized_message and "limit" in normalized_message)):
            return RateLimitScope.ACCOUNT_QUOTA

    exhausted = []
    if remaining_requests == 0:
        exhausted.append(RateLimitScope.REQUESTS_PER_DAY)
    if remaining_tokens == 0:
        exhausted.append(RateLimitScope.TOKENS_PER_MINUTE)
    if len(exhausted) > 1:
        return RateLimitScope.MULTIPLE
    if exhausted:
        return exhausted[0]
    return RateLimitScope.UNKNOWN


def _snapshot_from_diagnostic(diagnostic: InferenceDiagnostic) -> RateLimitSnapshot | None:
    if diagnostic.http_status != 429 or diagnostic.rate_limit_scope is None:
        return None
    return RateLimitSnapshot(
        scope=diagnostic.rate_limit_scope,
        retry_after_seconds=diagnostic.retry_after_seconds,
        remaining_requests=diagnostic.rate_limit_remaining_requests,
        remaining_tokens=diagnostic.rate_limit_remaining_tokens,
        reset_requests_seconds=diagnostic.rate_limit_reset_requests_seconds,
        reset_tokens_seconds=diagnostic.rate_limit_reset_tokens_seconds,
    )


def _request_is_safe_to_retry(request: InferenceRequest) -> bool:
    if request.options and request.options.get("tools"):
        return False
    return not any(getattr(message, "role", None) == "tool" for message in request.messages)


def _rate_limit_retry_delay(diagnostic: InferenceDiagnostic) -> float | None:
    if diagnostic.code is InferenceDiagnosticCode.GROQ_QUOTA_EXHAUSTED:
        return None
    scope = diagnostic.rate_limit_scope or RateLimitScope.UNKNOWN
    if scope in {
        RateLimitScope.REQUESTS_PER_DAY,
        RateLimitScope.TOKENS_PER_DAY,
        RateLimitScope.ACCOUNT_QUOTA,
        RateLimitScope.MULTIPLE,
    }:
        return None

    required_wait = diagnostic.retry_after_seconds
    if scope is RateLimitScope.TOKENS_PER_MINUTE:
        reset = diagnostic.rate_limit_reset_tokens_seconds
        if reset is not None:
            required_wait = max(required_wait or 0.0, reset)
    if required_wait is None or required_wait > _MAX_AUTOMATIC_RETRY_WAIT_SECONDS:
        return None

    exponential = min(
        _RETRY_BACKOFF_BASE_SECONDS * (2 ** diagnostic.retries_attempted),
        _MAX_AUTOMATIC_RETRY_WAIT_SECONDS,
    )
    jitter = random.uniform(0.0, _RETRY_JITTER_SECONDS)
    delay = max(required_wait, exponential + jitter)
    return delay if delay <= _MAX_AUTOMATIC_RETRY_WAIT_SECONDS else None

"""Cerebras adapter using Microsoft Agent Framework's OpenAI-compatible client."""

from __future__ import annotations

import asyncio
import email.utils
import json
import math
import os
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from threading import Lock
from typing import Callable, TypeGuard

from agent_framework import ChatOptions, Content, Message
from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)
from agent_framework.openai import OpenAIChatCompletionClient

from ..contracts import (
    InferenceDiagnostic,
    InferenceDiagnosticCode,
    InferenceRequest,
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


CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"
DEFAULT_MODEL_ID = "gpt-oss-120b"
_MAX_DIAGNOSTIC_DURATION_SECONDS = 31_536_000.0
_RATE_RESET_COMPONENT = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h|d)")

_GROQ_TO_CEREBRAS_DIAGNOSTIC_CODES = {
    InferenceDiagnosticCode.GROQ_MALFORMED_RESPONSE: InferenceDiagnosticCode.CEREBRAS_MALFORMED_RESPONSE,
    InferenceDiagnosticCode.GROQ_TIMEOUT: InferenceDiagnosticCode.CEREBRAS_TIMEOUT,
    InferenceDiagnosticCode.GROQ_NETWORK_FAILURE: InferenceDiagnosticCode.CEREBRAS_NETWORK_FAILURE,
    InferenceDiagnosticCode.GROQ_RATE_LIMITED: InferenceDiagnosticCode.CEREBRAS_RATE_LIMITED,
    InferenceDiagnosticCode.GROQ_PROVIDER_FAILURE: InferenceDiagnosticCode.CEREBRAS_PROVIDER_FAILURE,
    InferenceDiagnosticCode.GROQ_AUTHENTICATION_FAILED: InferenceDiagnosticCode.CEREBRAS_AUTHENTICATION_FAILED,
    InferenceDiagnosticCode.GROQ_INVALID_MODEL: InferenceDiagnosticCode.CEREBRAS_INVALID_MODEL,
    InferenceDiagnosticCode.GROQ_QUOTA_EXHAUSTED: InferenceDiagnosticCode.CEREBRAS_QUOTA_EXHAUSTED,
    InferenceDiagnosticCode.GROQ_TPM_BUDGET_EXCEEDED: InferenceDiagnosticCode.CEREBRAS_TPM_BUDGET_EXCEEDED,
    InferenceDiagnosticCode.GROQ_REQUEST_REJECTED: InferenceDiagnosticCode.CEREBRAS_REQUEST_REJECTED,
}


def _remap_to_cerebras_code(
    code: InferenceDiagnosticCode,
    kind: ProviderFailureKind | None = None,
) -> InferenceDiagnosticCode:
    if code in _GROQ_TO_CEREBRAS_DIAGNOSTIC_CODES:
        return _GROQ_TO_CEREBRAS_DIAGNOSTIC_CODES[code]
    if isinstance(code, InferenceDiagnosticCode) and code.value.startswith("groq_"):
        if kind == ProviderFailureKind.MALFORMED_RESPONSE:
            return InferenceDiagnosticCode.CEREBRAS_MALFORMED_RESPONSE
        return InferenceDiagnosticCode.CEREBRAS_PROVIDER_FAILURE
    return code

_ALLOWED_DIAGNOSTIC_HEADERS = frozenset({
    "content-type",
    "date",
    "retry-after",
    "retry-after-ms",
    "x-ratelimit-limit-requests-day",
    "x-ratelimit-limit-requests-minute",
    "x-ratelimit-limit-tokens-day",
    "x-ratelimit-limit-tokens-minute",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-remaining-requests-day",
    "x-ratelimit-remaining-requests-minute",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-remaining-tokens-day",
    "x-ratelimit-remaining-tokens-minute",
    "x-ratelimit-reset-requests",
    "x-ratelimit-reset-requests-day",
    "x-ratelimit-reset-requests-minute",
    "x-ratelimit-reset-tokens",
    "x-ratelimit-reset-tokens-day",
    "x-ratelimit-reset-tokens-minute",
    "x-request-id",
})


def _is_chat_options(value: object) -> TypeGuard[ChatOptions]:
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


@dataclass(frozen=True, slots=True)
class CerebrasProviderConfig:
    model_id: str = DEFAULT_MODEL_ID
    timeout_seconds: float = 30.0
    max_tool_iterations: int = 5
    max_tool_calls: int = 8
    tpm_limit_tokens: int = 30_000
    tpm_headroom_tokens: int = 3_000
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
        if (
            isinstance(self.tpm_limit_tokens, bool)
            or not isinstance(self.tpm_limit_tokens, int)
            or self.tpm_limit_tokens < 1
        ):
            raise ValueError("tpm_limit_tokens must be a positive integer")
        if (
            isinstance(self.tpm_headroom_tokens, bool)
            or not isinstance(self.tpm_headroom_tokens, int)
            or not 0 <= self.tpm_headroom_tokens < self.tpm_limit_tokens
        ):
            raise ValueError("tpm_headroom_tokens must be smaller than the TPM limit")
        if (
            isinstance(self.default_max_output_tokens, bool)
            or not isinstance(self.default_max_output_tokens, int)
            or self.default_max_output_tokens < 1
        ):
            raise ValueError("default_max_output_tokens must be positive")

    @classmethod
    def from_environment(cls) -> "CerebrasProviderConfig":
        """Load non-secret deployment configuration only; never reads API keys."""
        raw_timeout = os.environ.get("CEREBRAS_TIMEOUT_SECONDS", "30")
        try:
            timeout = float(raw_timeout)
        except (TypeError, ValueError):
            raise ValueError("CEREBRAS_TIMEOUT_SECONDS must be numeric") from None
        try:
            tpm_limit = int(os.environ.get("CEREBRAS_TPM_LIMIT_TOKENS", "30000"))
            tpm_headroom = int(os.environ.get("CEREBRAS_TPM_HEADROOM_TOKENS", "3000"))
            default_output = int(os.environ.get("CEREBRAS_MAX_OUTPUT_TOKENS", "1024"))
        except (TypeError, ValueError):
            raise ValueError("Cerebras token budget settings must be integers") from None
        return cls(
            model_id=os.environ.get("CEREBRAS_MODEL_ID", DEFAULT_MODEL_ID),
            timeout_seconds=timeout,
            tpm_limit_tokens=tpm_limit,
            tpm_headroom_tokens=tpm_headroom,
            default_max_output_tokens=default_output,
        )


class CerebrasProvider:
    """Cerebras provider adapter with explicit credential injection and zero retries.

    The API key is passed transiently via ProviderCredential, never read from environment
    or persisted. Retries are strictly 0.
    """

    provider_id = "cerebras"

    _UNSAFE_OPTIONS = frozenset({
        "api_key", "authorization", "headers", "extra_headers", "default_headers",
        "base_url", "endpoint", "client", "client_kwargs", "client_config",
        "http_client", "async_client", "credential", "credentials",
    })
    _CEREBRAS_UNSUPPORTED_OPTIONS = frozenset({"presence_penalty", "logit_bias"})

    def __init__(
        self,
        config: CerebrasProviderConfig | None = None,
        *,
        sdk_client_factory: Callable[[str, str, float], AsyncOpenAI] | None = None,
    ) -> None:
        self.config = config or CerebrasProviderConfig.from_environment()
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
            if normalized_names & self._CEREBRAS_UNSUPPORTED_OPTIONS:
                raise ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE)
            allowed_options = set(ChatOptions.__annotations__)
            if any(key not in allowed_options for key in options):
                raise ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE)
            requested_model = options.get("model", self.config.model_id)
            if not isinstance(requested_model, str) or not requested_model.strip():
                raise ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE)
            output_limit = options.get("max_tokens", self.config.default_max_output_tokens)
            if (
                isinstance(output_limit, bool)
                or not isinstance(output_limit, int)
                or output_limit < 1
            ):
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

        estimated_input_tokens = estimate_cerebras_input_tokens(request.messages)
        budget_limit = self.config.tpm_limit_tokens - self.config.tpm_headroom_tokens
        budget_cost = estimated_input_tokens + output_limit

        reservation, remaining, cooldown = self._reserve_budget(budget_cost)
        if reservation is None:
            code = (
                InferenceDiagnosticCode.CEREBRAS_RATE_LIMITED
                if cooldown > 0
                else InferenceDiagnosticCode.CEREBRAS_TPM_BUDGET_EXCEEDED
            )
            raise ProviderFailure(
                ProviderFailureKind.RATE_LIMIT,
                InferenceDiagnostic(
                    code=code,
                    stage=InferenceStage.REQUEST_VALIDATION,
                    provider="cerebras",
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

        secret = credential._consume_for_provider()
        sdk_client: AsyncOpenAI | None = None
        raw_result = None
        response = None
        failure_kind: ProviderFailureKind | None = None
        failure_diagnostic: InferenceDiagnostic | None = None
        correlation_id = request.correlation_id

        try:
            sdk_client = self._sdk_client_factory(
                secret,
                CEREBRAS_BASE_URL,
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
            try:
                raw_result = await framework_client.get_response(
                    request.messages,
                    options=call_options,
                )
                response = sanitized_chat_response(raw_result, secret)
            except Exception as exc:
                if isinstance(exc, ProviderFailure):
                    failure_kind = exc.kind
                    diag_code = _remap_to_cerebras_code(exc.diagnostic.code, exc.kind)
                    failure_diagnostic = replace(
                        exc.diagnostic,
                        code=diag_code,
                        correlation_id=correlation_id,
                        model_id=exc.diagnostic.model_id or requested_model,
                        provider="cerebras",
                        agent_name=request.agent_name,
                        estimated_input_tokens=estimated_input_tokens,
                        output_token_limit=output_limit,
                        budget_limit_tokens=budget_limit,
                        budget_remaining_tokens=remaining,
                        budget_decision="provider_rejected",
                        retry_decision="not_retried",
                    )
                else:
                    failure_kind = _classify_exception_chain(exc)
                    failure_diagnostic = _diagnostic_for_exception(
                        exc,
                        failure_kind,
                        requested_model,
                        correlation_id=correlation_id,
                        secret=secret,
                    )
                    failure_diagnostic = replace(
                        failure_diagnostic,
                        agent_name=request.agent_name,
                        estimated_input_tokens=estimated_input_tokens,
                        output_token_limit=output_limit,
                        budget_limit_tokens=budget_limit,
                        budget_remaining_tokens=remaining,
                        budget_decision="provider_rejected",
                        retry_decision="not_retried",
                    )
        except Exception as exc:
            if isinstance(exc, ProviderFailure):
                failure_kind = exc.kind
                diag_code = _remap_to_cerebras_code(exc.diagnostic.code, exc.kind)
                failure_diagnostic = replace(
                    exc.diagnostic,
                    code=diag_code,
                    correlation_id=correlation_id,
                    model_id=exc.diagnostic.model_id or requested_model,
                    provider="cerebras",
                )
            else:
                failure_kind = _classify_exception_chain(exc)
                failure_diagnostic = _diagnostic_for_exception(
                    exc,
                    failure_kind,
                    requested_model,
                    correlation_id=correlation_id,
                    secret=secret if 'secret' in locals() else None,
                )
        finally:
            if sdk_client is not None:
                try:
                    await sdk_client.close()
                except Exception:
                    if failure_kind is None:
                        failure_kind = ProviderFailureKind.PROVIDER_FAILURE
                        failure_diagnostic = InferenceDiagnostic(
                            code=InferenceDiagnosticCode.CEREBRAS_PROVIDER_FAILURE,
                            stage=InferenceStage.PROVIDER_TRANSPORT,
                            provider="cerebras",
                            model_id=requested_model,
                        )
            secret = ""

        usage_source = response if response is not None else raw_result
        usage_values = (
            extract_cerebras_usage_counts(usage_source)
            if usage_source is not None
            else (None, None, None)
        )

        if failure_diagnostic is not None:
            diag_code = _remap_to_cerebras_code(failure_diagnostic.code, failure_kind)
            failure_diagnostic = replace(
                failure_diagnostic,
                code=diag_code,
                correlation_id=correlation_id,
                provider="cerebras",
                model_id=failure_diagnostic.model_id or requested_model,
                agent_name=request.agent_name,
                estimated_input_tokens=estimated_input_tokens,
                output_token_limit=output_limit,
                budget_limit_tokens=budget_limit,
                budget_remaining_tokens=self._remaining_budget(),
                budget_decision=failure_diagnostic.budget_decision or "provider_rejected",
                retry_decision="not_retried",
                http_status=failure_diagnostic.http_status or (200 if usage_source is not None else None),
                request_id=failure_diagnostic.request_id or (getattr(usage_source, "response_id", None) if usage_source is not None else None),
                finish_reason=failure_diagnostic.finish_reason or (getattr(usage_source, "finish_reason", None) if usage_source is not None else None),
            )

        if failure_kind is not None:
            if usage_values[2] is not None:
                self._settle_budget(reservation, usage_values[2])
            else:
                self._settle_budget(reservation, None)
            if failure_diagnostic is not None and failure_diagnostic.http_status == 429:
                self._set_cooldown(failure_diagnostic)
            raise ProviderFailure(failure_kind, failure_diagnostic) from None

        if response is None:
            if usage_values[2] is not None:
                self._settle_budget(reservation, usage_values[2])
            else:
                self._settle_budget(reservation, None)
            raise ProviderFailure(
                ProviderFailureKind.MALFORMED_RESPONSE,
                InferenceDiagnostic(
                    code=InferenceDiagnosticCode.CEREBRAS_MALFORMED_RESPONSE,
                    stage=InferenceStage.RESPONSE_VALIDATION,
                    provider="cerebras",
                    model_id=requested_model,
                    http_status=200 if usage_source is not None else None,
                    budget_remaining_tokens=self._remaining_budget(),
                    request_id=getattr(usage_source, "response_id", None) if usage_source is not None else None,
                    finish_reason=getattr(usage_source, "finish_reason", None) if usage_source is not None else None,
                ),
            ) from None

        if usage_values[2] is not None:
            self._settle_budget(reservation, usage_values[2])
        else:
            self._settle_budget(reservation, None)

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
            retries_attempted=0,
            retry_decision="not_retried",
            http_status=200,
            request_id=getattr(response, "response_id", None),
            finish_reason=getattr(response, "finish_reason", None),
        )
        return ProviderResponse(
            provider=self.provider_id,
            model_id=requested_model,
            response=response,
            usage=usage,
        )

    def _reserve_budget(
        self, token_count: int, *, ignore_cooldown: bool = False
    ) -> tuple[list[float | int] | None, int, float]:
        now = time.monotonic()
        limit = self.config.tpm_limit_tokens - self.config.tpm_headroom_tokens
        with self._budget_lock:
            self._budget_events = [
                event for event in self._budget_events if now - float(event[0]) < 60.0
            ]
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
                self._budget_events = [ev for ev in self._budget_events if ev is not reservation]
            else:
                reservation[1] = actual_tokens

    def reserve_budget(self, token_count: int) -> list[float | int] | None:
        """Reserve a block of tokens in the independent Cerebras rolling budget window."""
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
            self._budget_events = [
                event for event in self._budget_events if now - float(event[0]) < 60.0
            ]
            return max(0, limit - sum(int(event[1]) for event in self._budget_events))

    def _set_cooldown(self, diagnostic: InferenceDiagnostic) -> None:
        waits = [
            value
            for value in (
                diagnostic.retry_after_seconds,
                diagnostic.rate_limit_reset_tokens_seconds
                if diagnostic.rate_limit_scope is RateLimitScope.TOKENS_PER_MINUTE
                else None,
            )
            if value is not None
        ]
        if not waits:
            return
        now = time.monotonic()
        with self._budget_lock:
            self._budget_blocked_until = max(self._budget_blocked_until, now + max(waits))


def estimate_cerebras_input_tokens(messages: Sequence[Message]) -> int:
    """Conservative byte heuristic with extra allowance for non-ASCII text."""
    serialized = json.dumps(
        [message.to_dict(exclude_none=True) for message in messages],
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    ascii_bytes = sum(1 for char in serialized if char.isascii())
    non_ascii_bytes = len(serialized.encode("utf-8")) - ascii_bytes
    return math.ceil(ascii_bytes / 4.3) + math.ceil(non_ascii_bytes / 3.0) + 12 * len(messages)


def extract_cerebras_usage_counts(response: object) -> tuple[int | None, int | None, int | None]:
    """Read validated integer usage counters from response."""
    usage = getattr(response, "usage_details", None)
    if not isinstance(usage, Mapping):
        return None, None, None

    def read(*names: str) -> int | None:
        for name in names:
            value = usage.get(name)
            if (
                isinstance(value, int)
                and not isinstance(value, bool)
                and 0 <= value <= 10**9
            ):
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
    return error.get("code") in {"model_not_found", "invalid_model"} or error.get("type") in {
        "model_not_found", "invalid_model_error",
    }


def _classify_status_error(exc: APIStatusError) -> ProviderFailureKind:
    if exc.status_code in (401, 403):
        return ProviderFailureKind.AUTHENTICATION
    if exc.status_code == 402:
        return ProviderFailureKind.PAYMENT_REQUIRED
    if exc.status_code == 429:
        return ProviderFailureKind.RATE_LIMIT
    if exc.status_code in (400, 404) and _looks_like_invalid_model(exc):
        return ProviderFailureKind.INVALID_MODEL
    if exc.status_code == 504:
        return ProviderFailureKind.TIMEOUT
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
        if getattr(current, "status_code", None) == 402:
            return ProviderFailureKind.PAYMENT_REQUIRED
        if isinstance(current, (APITimeoutError, TimeoutError)) or (
            type(current).__name__ in {
                "TimeoutException", "ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout",
            }
            and type(current).__module__.split(".")[0] in {"httpx", "httpx2"}
        ):
            return ProviderFailureKind.TIMEOUT
        if isinstance(current, APIConnectionError) or (
            type(current).__name__ in {
                "ConnectError", "NetworkError", "ProxyError", "RemoteProtocolError", "ProtocolError",
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


def _diagnostic_for_exception(
    exc: BaseException,
    kind: ProviderFailureKind,
    model_id: str,
    *,
    correlation_id: str,
    secret: str | None = None,
) -> InferenceDiagnostic:
    status = None
    status_error: APIStatusError | None = None
    seen: set[int] = set()
    current: BaseException | None = exc
    type_names: list[str] = []

    while current is not None and id(current) not in seen:
        seen.add(id(current))
        type_names.append(type(current).__name__)
        if isinstance(current, APIStatusError) and status is None:
            status = current.status_code
            status_error = current
        current = current.__cause__ or current.__context__

    exception_type_chain = " > ".join(type_names[:4]) if type_names else None

    rate_snapshot = (
        _rate_limit_snapshot(status_error)
        if status == 429 and status_error is not None
        else None
    )
    scope = rate_snapshot.scope if rate_snapshot is not None else None

    provider_err_code: str | None = None
    provider_err_status: str | None = None
    provider_err_msg: str | None = None

    if status_error is not None:
        body = getattr(status_error, "body", None)
        if isinstance(body, dict):
            err_obj = body.get("error", body)
            if isinstance(err_obj, dict):
                code_val = err_obj.get("code")
                if isinstance(code_val, (str, int)):
                    provider_err_code = str(code_val)[:100]
                type_val = err_obj.get("type")
                if isinstance(type_val, str):
                    provider_err_status = type_val[:100]
                msg_val = err_obj.get("message")
                if isinstance(msg_val, str):
                    if secret and secret in msg_val:
                        msg_val = msg_val.replace(secret, "[REDACTED]")
                    provider_err_msg = msg_val[:280]

    sanitized_headers: list[tuple[str, str]] = []
    if status_error is not None:
        raw_headers = getattr(getattr(status_error, "response", None), "headers", {})
        if isinstance(raw_headers, Mapping):
            for hk, hv in raw_headers.items():
                if isinstance(hk, str) and hk.lower() in _ALLOWED_DIAGNOSTIC_HEADERS:
                    sanitized_headers.append((hk.lower(), str(hv)))

    if kind is ProviderFailureKind.AUTHENTICATION:
        code = InferenceDiagnosticCode.CEREBRAS_AUTHENTICATION_FAILED
    elif kind is ProviderFailureKind.PAYMENT_REQUIRED:
        code = InferenceDiagnosticCode.CEREBRAS_PAYMENT_REQUIRED
    elif kind is ProviderFailureKind.INVALID_MODEL:
        code = InferenceDiagnosticCode.CEREBRAS_INVALID_MODEL
    elif kind is ProviderFailureKind.TIMEOUT:
        chain_lower = (exception_type_chain or "").lower()
        if status == 504:
            code = InferenceDiagnosticCode.CEREBRAS_GATEWAY_TIMEOUT
        elif "connect" in chain_lower:
            code = InferenceDiagnosticCode.CEREBRAS_CONNECT_TIMEOUT
        elif "read" in chain_lower:
            code = InferenceDiagnosticCode.CEREBRAS_READ_TIMEOUT
        else:
            code = InferenceDiagnosticCode.CEREBRAS_TIMEOUT
    elif kind is ProviderFailureKind.NETWORK:
        code = InferenceDiagnosticCode.CEREBRAS_NETWORK_FAILURE
    elif kind is ProviderFailureKind.RATE_LIMIT:
        code = (
            InferenceDiagnosticCode.CEREBRAS_QUOTA_EXHAUSTED
            if scope in {
                RateLimitScope.REQUESTS_PER_DAY,
                RateLimitScope.TOKENS_PER_DAY,
                RateLimitScope.ACCOUNT_QUOTA,
                RateLimitScope.MULTIPLE,
            }
            else InferenceDiagnosticCode.CEREBRAS_RATE_LIMITED
        )
    elif kind is ProviderFailureKind.MALFORMED_RESPONSE:
        code = InferenceDiagnosticCode.CEREBRAS_MALFORMED_RESPONSE
    elif status == 502:
        code = InferenceDiagnosticCode.CEREBRAS_BAD_GATEWAY
    elif status == 503:
        code = InferenceDiagnosticCode.CEREBRAS_SERVICE_UNAVAILABLE
    elif status is not None and 400 <= status < 500:
        code = InferenceDiagnosticCode.CEREBRAS_REQUEST_REJECTED
    else:
        code = InferenceDiagnosticCode.CEREBRAS_PROVIDER_FAILURE

    stage = (
        InferenceStage.PROVIDER_RESPONSE
        if status is not None
        else (
            InferenceStage.RESPONSE_VALIDATION
            if kind is ProviderFailureKind.MALFORMED_RESPONSE
            else InferenceStage.PROVIDER_TRANSPORT
        )
    )

    return InferenceDiagnostic(
        code=code,
        stage=stage,
        provider="cerebras",
        model_id=model_id,
        http_status=status,
        rate_limit_scope=scope,
        retry_after_seconds=(
            rate_snapshot.retry_after_seconds if rate_snapshot is not None else None
        ),
        rate_limit_remaining_requests=(
            rate_snapshot.remaining_requests if rate_snapshot is not None else None
        ),
        rate_limit_remaining_tokens=(
            rate_snapshot.remaining_tokens if rate_snapshot is not None else None
        ),
        rate_limit_reset_requests_seconds=(
            rate_snapshot.reset_requests_seconds if rate_snapshot is not None else None
        ),
        rate_limit_reset_tokens_seconds=(
            rate_snapshot.reset_tokens_seconds if rate_snapshot is not None else None
        ),
        retries_attempted=0,
        correlation_id=correlation_id,
        provider_error_code=provider_err_code,
        provider_error_status=provider_err_status,
        provider_error_message=provider_err_msg,
        sanitized_response_headers=tuple(sanitized_headers),
        exception_type_chain=exception_type_chain,
    )


def _rate_limit_snapshot(exc: APIStatusError) -> RateLimitSnapshot:
    headers = exc.response.headers
    remaining_requests = _parse_nonnegative_int(
        headers.get("x-ratelimit-remaining-requests")
        or headers.get("x-ratelimit-remaining-requests-minute")
    )
    remaining_tokens = _parse_nonnegative_int(
        headers.get("x-ratelimit-remaining-tokens")
        or headers.get("x-ratelimit-remaining-tokens-minute")
    )
    reset_requests = _parse_duration_seconds(
        headers.get("x-ratelimit-reset-requests")
        or headers.get("x-ratelimit-reset-requests-minute")
    )
    reset_tokens = _parse_duration_seconds(
        headers.get("x-ratelimit-reset-tokens")
        or headers.get("x-ratelimit-reset-tokens-minute")
    )
    scope = _rate_limit_scope(
        getattr(exc, "body", None),
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


def _rate_limit_scope(
    body: object, *, remaining_requests: int | None, remaining_tokens: int | None
) -> RateLimitScope:
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
            (r"\btokens?\s+per\s+minute\b|\btpm\b", RateLimitScope.TOKENS_PER_MINUTE),
            (r"\btokens?\s+per\s+day\b|\btpd\b", RateLimitScope.TOKENS_PER_DAY),
        )
        for pattern, scope in message_scopes:
            if re.search(pattern, normalized_message):
                return scope
        if (
            ("quota" in normalized_message and any(
                term in normalized_message for term in ("insufficient", "exceed", "exhaust", "current quota")
            ))
            or ("billing" in normalized_message and "limit" in normalized_message)
        ):
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

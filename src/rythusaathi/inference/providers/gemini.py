"""Google Gemini 3.8 Flash adapter using Microsoft Agent Framework's OpenAI client."""

from __future__ import annotations

import asyncio
import email.utils
import json
import math
import os
import random
import re
import time

import httpx
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
    Timeout,
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


GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_MODEL_ID = "gemini-3.8-flash"
_MAX_RATE_LIMIT_RETRIES = 1
_MAX_AUTOMATIC_RETRY_WAIT_SECONDS = 2.0
_RETRY_BACKOFF_BASE_SECONDS = 0.25
_RETRY_JITTER_SECONDS = 0.10
_MAX_TRANSIENT_RETRIES = 1
_TRANSIENT_BACKOFF_MIN_SECONDS = 1.0
_TRANSIENT_BACKOFF_MAX_SECONDS = 2.0
_MAX_TRANSIENT_RETRY_WAIT_SECONDS = 5.0
_RETRY_STAGGER_INTERVAL_SECONDS = 0.25
_MAX_DIAGNOSTIC_DURATION_SECONDS = 31_536_000.0
_RATE_RESET_COMPONENT = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h|d)")


def _is_chat_options(value: object) -> TypeGuard[ChatOptions]:
    """Validate ChatOptions' documented TypedDict runtime representation."""
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


@dataclass(frozen=True, slots=True)
class GeminiProviderConfig:
    model_id: str = DEFAULT_MODEL_ID
    base_url: str = GEMINI_BASE_URL
    timeout_seconds: float = 60.0
    connect_timeout_seconds: float = 10.0
    read_timeout_seconds: float = 60.0
    write_timeout_seconds: float = 10.0
    pool_timeout_seconds: float = 10.0
    max_tool_iterations: int = 5
    max_tool_calls: int = 8
    # Architectural model capabilities (Gemini 3.8 Flash supports up to 65,536 output tokens)
    model_context_window: int = 1_048_576
    max_supported_output_tokens: int = 65_536
    # Bounded default output limit when a request does not supply max_tokens
    default_max_output_tokens: int = 1024
    # Gemini 3.8 thinking level / reasoning effort (e.g. "low", "minimal", "medium", "high", or None for disabled)
    thinking_level: str | None = None
    # Explicit Google AI Studio project limits (None represents unknown / unconfigured)
    rpm_limit: int | None = None
    tpm_limit_tokens: int | None = None
    tpm_headroom_tokens: int = 0
    rpd_limit: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.model_id, str) or not self.model_id.strip():
            raise ValueError("model_id must not be empty")
        if not isinstance(self.base_url, str) or not self.base_url.strip():
            raise ValueError("base_url must not be empty")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive")
        for name in (
            "connect_timeout_seconds",
            "read_timeout_seconds",
            "write_timeout_seconds",
            "pool_timeout_seconds",
        ):
            val = getattr(self, name)
            if (
                isinstance(val, bool)
                or not isinstance(val, (int, float))
                or not math.isfinite(val)
                or val <= 0
            ):
                raise ValueError(f"{name} must be positive")
        limits = (self.max_tool_iterations, self.max_tool_calls)
        if any(isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 for limit in limits):
            raise ValueError("tool invocation limits must be positive")
        if (
            isinstance(self.model_context_window, bool)
            or not isinstance(self.model_context_window, int)
            or self.model_context_window < 1024
        ):
            raise ValueError("model_context_window must be at least 1024 tokens")
        if (
            isinstance(self.max_supported_output_tokens, bool)
            or not isinstance(self.max_supported_output_tokens, int)
            or self.max_supported_output_tokens < 1
        ):
            raise ValueError("max_supported_output_tokens must be positive")
        if (
            isinstance(self.default_max_output_tokens, bool)
            or not isinstance(self.default_max_output_tokens, int)
            or not 1 <= self.default_max_output_tokens <= self.max_supported_output_tokens
        ):
            raise ValueError("default_max_output_tokens must be within model output limits")
        if self.rpm_limit is not None and (
            isinstance(self.rpm_limit, bool)
            or not isinstance(self.rpm_limit, int)
            or self.rpm_limit < 1
        ):
            raise ValueError("rpm_limit must be a positive integer when configured")
        if self.tpm_limit_tokens is not None and (
            isinstance(self.tpm_limit_tokens, bool)
            or not isinstance(self.tpm_limit_tokens, int)
            or self.tpm_limit_tokens < 1
        ):
            raise ValueError("tpm_limit_tokens must be a positive integer when configured")
        if (
            isinstance(self.tpm_headroom_tokens, bool)
            or not isinstance(self.tpm_headroom_tokens, int)
            or self.tpm_headroom_tokens < 0
        ):
            raise ValueError("tpm_headroom_tokens must be non-negative")
        if self.tpm_limit_tokens is not None and self.tpm_headroom_tokens >= self.tpm_limit_tokens:
            raise ValueError("tpm_headroom_tokens must be smaller than the TPM limit")
        if self.rpd_limit is not None and (
            isinstance(self.rpd_limit, bool)
            or not isinstance(self.rpd_limit, int)
            or self.rpd_limit < 1
        ):
            raise ValueError("rpd_limit must be a positive integer when configured")
        if (
            self.thinking_level is not None
            and (not isinstance(self.thinking_level, str) or not self.thinking_level.strip())
        ):
            raise ValueError("thinking_level must be non-empty string or None")

    @property
    def has_quota_limits(self) -> bool:
        """True if any project-level rate or volume limits are configured."""
        return (
            self.rpm_limit is not None
            or self.tpm_limit_tokens is not None
            or self.rpd_limit is not None
        )

    @classmethod
    def free_tier(
        cls,
        *,
        model_id: str = DEFAULT_MODEL_ID,
        base_url: str = GEMINI_BASE_URL,
        timeout_seconds: float = 60.0,
        connect_timeout_seconds: float = 10.0,
        read_timeout_seconds: float = 60.0,
        write_timeout_seconds: float = 10.0,
        pool_timeout_seconds: float = 10.0,
        rpm_limit: int | None = 15,
        tpm_limit_tokens: int | None = 1_000_000,
        tpm_headroom_tokens: int = 10_000,
        rpd_limit: int | None = 1500,
        default_max_output_tokens: int = 1024,
        thinking_level: str | None = None,
        **kwargs,
    ) -> "GeminiProviderConfig":
        """Configurable preset for Google AI Studio Free-Tier projects.

        IMPORTANT: Google AI Studio does not offer a single universal free-tier
        quota. Rate limits (RPM, TPM, RPD) vary by project, billing status,
        verification level, and geographic region. The project owner MUST inspect
        their actual project limits in Google AI Studio
        (https://aistudio.google.com/app/plan_information) and provide those
        explicit values rather than relying on assumed defaults.

        All quota parameters are fully overridable. If any quota limit is set to
        None, it remains unconstrained on the client side (unknown) and yields
        to provider-side rate enforcement.
        """
        if tpm_limit_tokens is None:
            tpm_headroom_tokens = 0
        return cls(
            model_id=model_id,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            write_timeout_seconds=write_timeout_seconds,
            pool_timeout_seconds=pool_timeout_seconds,
            rpm_limit=rpm_limit,
            tpm_limit_tokens=tpm_limit_tokens,
            tpm_headroom_tokens=tpm_headroom_tokens,
            rpd_limit=rpd_limit,
            default_max_output_tokens=default_max_output_tokens,
            thinking_level=thinking_level,
            **kwargs,
        )

    @classmethod
    def from_environment(cls) -> "GeminiProviderConfig":
        """Load configuration from environment; if quotas are unset, they remain None (unknown)."""
        raw_timeout = os.environ.get("GEMINI_TIMEOUT_SECONDS", "60")
        try:
            timeout = float(raw_timeout)
        except (TypeError, ValueError):
            raise ValueError("GEMINI_TIMEOUT_SECONDS must be numeric") from None

        try:
            connect_timeout = float(os.environ.get("GEMINI_CONNECT_TIMEOUT_SECONDS", "10"))
            read_timeout = float(os.environ.get("GEMINI_READ_TIMEOUT_SECONDS", str(timeout)))
            write_timeout = float(os.environ.get("GEMINI_WRITE_TIMEOUT_SECONDS", "10"))
            pool_timeout = float(os.environ.get("GEMINI_POOL_TIMEOUT_SECONDS", "10"))
        except (TypeError, ValueError):
            raise ValueError("Gemini timeout settings must be numeric") from None

        try:
            default_output = int(
                os.environ.get("GEMINI_MAX_OUTPUT_TOKENS", os.environ.get("GEMINI_DEFAULT_MAX_OUTPUT_TOKENS", "1024"))
            )
            context_window = int(os.environ.get("GEMINI_CONTEXT_WINDOW", "1048576"))
        except (TypeError, ValueError):
            raise ValueError("Gemini token settings must be integers") from None

        tier = os.environ.get("GEMINI_TIER", "").strip().lower()
        if tier == "free":
            rpm_limit: int | None = 15
            tpm_limit: int | None = 1_000_000
            tpm_headroom: int = 10_000
            rpd_limit: int | None = 1500
        else:
            rpm_limit = None
            tpm_limit = None
            tpm_headroom = 0
            rpd_limit = None

        if "GEMINI_RPM_LIMIT" in os.environ:
            try:
                rpm_limit = int(os.environ["GEMINI_RPM_LIMIT"])
            except (TypeError, ValueError):
                raise ValueError("GEMINI_RPM_LIMIT must be an integer") from None
        if "GEMINI_TPM_LIMIT_TOKENS" in os.environ:
            try:
                tpm_limit = int(os.environ["GEMINI_TPM_LIMIT_TOKENS"])
            except (TypeError, ValueError):
                raise ValueError("GEMINI_TPM_LIMIT_TOKENS must be an integer") from None
        if "GEMINI_TPM_HEADROOM_TOKENS" in os.environ:
            try:
                tpm_headroom = int(os.environ["GEMINI_TPM_HEADROOM_TOKENS"])
            except (TypeError, ValueError):
                raise ValueError("GEMINI_TPM_HEADROOM_TOKENS must be an integer") from None
        if "GEMINI_RPD_LIMIT" in os.environ:
            try:
                rpd_limit = int(os.environ["GEMINI_RPD_LIMIT"])
            except (TypeError, ValueError):
                raise ValueError("GEMINI_RPD_LIMIT must be an integer") from None

        raw_thinking = os.environ.get("GEMINI_THINKING_LEVEL", "").strip()
        thinking_level = None if not raw_thinking or raw_thinking.lower() in {"none", "off", "disabled"} else raw_thinking

        return cls(
            model_id=os.environ.get("GEMINI_MODEL_ID", DEFAULT_MODEL_ID),
            base_url=os.environ.get("GEMINI_BASE_URL", GEMINI_BASE_URL),
            timeout_seconds=timeout,
            connect_timeout_seconds=connect_timeout,
            read_timeout_seconds=read_timeout,
            write_timeout_seconds=write_timeout,
            pool_timeout_seconds=pool_timeout,
            model_context_window=context_window,
            default_max_output_tokens=default_output,
            thinking_level=thinking_level,
            rpm_limit=rpm_limit,
            tpm_limit_tokens=tpm_limit,
            tpm_headroom_tokens=tpm_headroom,
            rpd_limit=rpd_limit,
        )


class GeminiProvider:
    """One provider call with explicit credential injection and no fallback.

    The API key is not read from environment, included in request messages, or
    returned to callers. Credential authorization/retrieval is owned by the
    protected backend credential service, which is intentionally not part of
    this provider implementation.
    """

    provider_id = "gemini"

    _UNSAFE_OPTIONS = frozenset({
        "api_key", "authorization", "headers", "extra_headers", "default_headers",
        "base_url", "endpoint", "client", "client_kwargs", "client_config",
        "http_client", "async_client", "credential", "credentials",
    })
    _GEMINI_UNSUPPORTED_OPTIONS = frozenset({
        "logit_bias", "top_k", "candidate_count", "thinking_budget",
    })

    def __init__(
        self,
        config: GeminiProviderConfig | None = None,
        *,
        sdk_client_factory: Callable[[str, str, float | Timeout], AsyncOpenAI] | None = None,
    ) -> None:
        self.config = config or GeminiProviderConfig.from_environment()
        self._sdk_client_factory = sdk_client_factory or self._make_sdk_client
        self._budget_lock = Lock()
        self._minute_events: list[list[float | int]] = []
        self._day_events: list[float] = []
        self._budget_blocked_until = 0.0
        self._next_retry_earliest_time = 0.0

    @staticmethod
    def _make_sdk_client(api_key: str, base_url: str, timeout: float | Timeout) -> AsyncOpenAI:
        http_transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")
        http_client = httpx.AsyncClient(transport=http_transport, timeout=timeout)
        return AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=0,
            http_client=http_client,
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
            if normalized_names & self._GEMINI_UNSUPPORTED_OPTIONS:
                raise ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE)
            allowed_options = (
                set(ChatOptions.__annotations__)
                | {"reasoning_effort", "thinking_level", "extra_body", "deadline_seconds"}
            )
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
            call_options.pop("deadline_seconds", None)
            call_options["max_tokens"] = output_limit
            # Switch Gemini adapter to json_object format for structured output reliability
            resp_fmt = call_options.get("response_format")
            if isinstance(resp_fmt, dict) and resp_fmt.get("type") in {"json_schema", "json_object"}:
                call_options["response_format"] = {"type": "json_object"}
            if "thinking_level" in call_options:
                level = call_options.pop("thinking_level")
                if "reasoning_effort" not in call_options and level is not None:
                    call_options["reasoning_effort"] = level
            if "reasoning_effort" not in call_options and self.config.thinking_level is not None:
                call_options["reasoning_effort"] = self.config.thinking_level
            if call_options.get("reasoning_effort") is None:
                call_options.pop("reasoning_effort", None)
        else:
            requested_model = self.config.model_id
            output_limit = self.config.default_max_output_tokens
            call_options = {"max_tokens": output_limit}
            if self.config.thinking_level is not None:
                call_options["reasoning_effort"] = self.config.thinking_level

        for message in request.messages:
            if not isinstance(message.contents, Sequence) or isinstance(message.contents, (str, bytes)):
                raise ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE)
            if any(not isinstance(item, (Content, str, Mapping)) for item in message.contents):
                raise ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE)
        if credential._request_contains_secret(request):
            raise ProviderFailure(ProviderFailureKind.SENSITIVE_INPUT)

        # Architectural model validation
        estimated_input_tokens = estimate_input_tokens(request.messages)
        if output_limit > self.config.max_supported_output_tokens:
            raise ProviderFailure(
                ProviderFailureKind.MALFORMED_RESPONSE,
                InferenceDiagnostic(
                    code=InferenceDiagnosticCode.INVALID_REQUEST,
                    stage=InferenceStage.REQUEST_VALIDATION,
                    provider="gemini",
                    model_id=requested_model,
                ),
            )
        if estimated_input_tokens + output_limit > self.config.model_context_window:
            raise ProviderFailure(
                ProviderFailureKind.MALFORMED_RESPONSE,
                InferenceDiagnostic(
                    code=InferenceDiagnosticCode.INVALID_REQUEST,
                    stage=InferenceStage.REQUEST_VALIDATION,
                    provider="gemini",
                    model_id=requested_model,
                ),
            )

        budget_cost = estimated_input_tokens + output_limit
        reservation, remaining, cooldown, decision = self._reserve_quota(budget_cost)
        if reservation is None:
            if decision == "rpm_exceeded":
                raise ProviderFailure(
                    ProviderFailureKind.RATE_LIMIT,
                    InferenceDiagnostic(
                        code=InferenceDiagnosticCode.GEMINI_RATE_LIMITED,
                        stage=InferenceStage.REQUEST_VALIDATION,
                        provider="gemini",
                        model_id=requested_model,
                        rate_limit_scope=RateLimitScope.REQUESTS_PER_MINUTE,
                        retry_after_seconds=cooldown or None,
                        agent_name=request.agent_name,
                        estimated_input_tokens=estimated_input_tokens,
                        output_token_limit=output_limit,
                        budget_limit_tokens=self.config.rpm_limit,
                        budget_remaining_tokens=0,
                        budget_decision="rejected",
                        retry_decision="budget_rejected",
                        correlation_id=request.correlation_id,
                    ),
                )
            if decision == "rpd_exceeded":
                raise ProviderFailure(
                    ProviderFailureKind.RATE_LIMIT,
                    InferenceDiagnostic(
                        code=InferenceDiagnosticCode.GEMINI_QUOTA_EXHAUSTED,
                        stage=InferenceStage.REQUEST_VALIDATION,
                        provider="gemini",
                        model_id=requested_model,
                        rate_limit_scope=RateLimitScope.REQUESTS_PER_DAY,
                        retry_after_seconds=cooldown or None,
                        agent_name=request.agent_name,
                        estimated_input_tokens=estimated_input_tokens,
                        output_token_limit=output_limit,
                        budget_limit_tokens=self.config.rpd_limit,
                        budget_remaining_tokens=0,
                        budget_decision="rejected",
                        retry_decision="budget_rejected",
                        correlation_id=request.correlation_id,
                    ),
                )
            code = (
                InferenceDiagnosticCode.GEMINI_RATE_LIMITED
                if cooldown > 0
                else InferenceDiagnosticCode.GEMINI_TPM_BUDGET_EXCEEDED
            )
            tpm_budget_limit = (
                (self.config.tpm_limit_tokens - self.config.tpm_headroom_tokens)
                if self.config.tpm_limit_tokens is not None else None
            )
            raise ProviderFailure(
                ProviderFailureKind.RATE_LIMIT,
                InferenceDiagnostic(
                    code=code,
                    stage=InferenceStage.REQUEST_VALIDATION,
                    provider="gemini",
                    model_id=requested_model,
                    rate_limit_scope=RateLimitScope.TOKENS_PER_MINUTE,
                    retry_after_seconds=cooldown or None,
                    agent_name=request.agent_name,
                    estimated_input_tokens=estimated_input_tokens,
                    output_token_limit=output_limit,
                    budget_limit_tokens=tpm_budget_limit,
                    budget_remaining_tokens=remaining,
                    budget_decision="rejected",
                    retry_decision="budget_rejected",
                    correlation_id=request.correlation_id,
                ),
            )
        reservations = [reservation]
        admission_decision = decision

        secret = credential._consume_for_provider()
        sdk_client: AsyncOpenAI | None = None
        response = None
        failure_kind: ProviderFailureKind | None = None
        failure_diagnostic: InferenceDiagnostic | None = None
        correlation_id = request.correlation_id
        retries_attempted = 0
        prior_rate_limits: list[RateLimitSnapshot] = []
        timeout = Timeout(
            timeout=self.config.timeout_seconds,
            connect=self.config.connect_timeout_seconds,
            read=self.config.read_timeout_seconds,
            write=self.config.write_timeout_seconds,
            pool=self.config.pool_timeout_seconds,
        )
        request_start_time = time.monotonic()
        elapsed_seconds: float | None = None
        response_id: str | None = None
        finish_reason: str | None = None

        try:
            sdk_client = self._sdk_client_factory(
                secret,
                self.config.base_url,
                timeout,
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
            request_dispatched = False
            while True:
                try:
                    request_dispatched = True
                    result = await framework_client.get_response(
                        request.messages,
                        options=call_options,
                    )
                    elapsed_seconds = max(0.0, time.monotonic() - request_start_time)
                    response_id = getattr(result, "response_id", None)
                    finish_reason = getattr(result, "finish_reason", None)
                    response = sanitized_chat_response(result, secret)
                    msg_text = ""
                    if response.messages:
                        for m in response.messages:
                            if m.role == "assistant":
                                msg_text += "".join(
                                    item if isinstance(item, str) else getattr(item, "text", "")
                                    for item in m.contents
                                ).strip()
                    if finish_reason == "length" and not msg_text:
                        raise ProviderFailure(
                            ProviderFailureKind.MALFORMED_RESPONSE,
                            InferenceDiagnostic(
                                code=InferenceDiagnosticCode.GEMINI_MALFORMED_RESPONSE,
                                stage=InferenceStage.RESPONSE_VALIDATION,
                                provider="gemini",
                                model_id=requested_model,
                                finish_reason="length",
                                response_character_count=0,
                                parsing_outcome="length_terminated_empty",
                                elapsed_seconds=elapsed_seconds,
                                request_id=response_id,
                            ),
                        )
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    elapsed_seconds = max(0.0, time.monotonic() - request_start_time)
                    tpm_budget_limit = (
                        (self.config.tpm_limit_tokens - self.config.tpm_headroom_tokens)
                        if self.config.tpm_limit_tokens is not None else None
                    )
                    if isinstance(exc, ProviderFailure):
                        attempt_kind = exc.kind
                        attempt_diagnostic = replace(
                            exc.diagnostic,
                            provider="gemini",
                            correlation_id=correlation_id,
                            model_id=exc.diagnostic.model_id or requested_model,
                            retries_attempted=retries_attempted,
                            prior_rate_limits=tuple(prior_rate_limits),
                            agent_name=request.agent_name,
                            estimated_input_tokens=estimated_input_tokens,
                            output_token_limit=output_limit,
                            budget_limit_tokens=tpm_budget_limit,
                            budget_remaining_tokens=remaining,
                            budget_decision=(
                                "provider_rejected"
                                if getattr(exc.diagnostic, "http_status", None) == 429
                                else admission_decision
                            ),
                            retry_decision=("retried" if retries_attempted else "not_retried"),
                            elapsed_seconds=exc.diagnostic.elapsed_seconds or elapsed_seconds,
                            request_id=exc.diagnostic.request_id or response_id,
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
                            elapsed_seconds=elapsed_seconds,
                            request_id=response_id,
                        )
                        attempt_diagnostic = replace(
                            attempt_diagnostic,
                            provider="gemini",
                            agent_name=request.agent_name,
                            estimated_input_tokens=estimated_input_tokens,
                            output_token_limit=output_limit,
                            budget_limit_tokens=tpm_budget_limit,
                            budget_remaining_tokens=remaining,
                            budget_decision=(
                                "provider_rejected"
                                if getattr(attempt_diagnostic, "http_status", None) == 429
                                else admission_decision
                            ),
                            retry_decision=("retried" if retries_attempted else "not_retried"),
                            elapsed_seconds=attempt_diagnostic.elapsed_seconds or elapsed_seconds,
                            request_id=attempt_diagnostic.request_id or response_id,
                        )

                    retry_delay = None
                    is_rate_limit = attempt_kind is ProviderFailureKind.RATE_LIMIT
                    is_transient = (
                        getattr(attempt_diagnostic, "http_status", None) in (502, 503, 504)
                    )
                    if (
                        (is_rate_limit or is_transient)
                        and retries_attempted < 1
                        and _request_is_safe_to_retry(request)
                    ):
                        if is_rate_limit:
                            retry_delay = _rate_limit_retry_delay(attempt_diagnostic)
                        elif is_transient:
                            retry_delay = _transient_retry_delay(
                                attempt_diagnostic,
                                elapsed_seconds=elapsed_seconds,
                                timeout_seconds=self.config.timeout_seconds,
                                options=request.options,
                            )
                    if retry_delay is not None:
                        now = time.monotonic()
                        with self._budget_lock:
                            scheduled_time = max(now + retry_delay, self._next_retry_earliest_time)
                            self._next_retry_earliest_time = scheduled_time + _RETRY_STAGGER_INTERVAL_SECONDS
                            retry_delay = max(0.0, scheduled_time - now)

                        if is_rate_limit:
                            current_snapshot = _snapshot_from_diagnostic(attempt_diagnostic)
                            if current_snapshot is not None:
                                prior_rate_limits.append(current_snapshot)
                        retries_attempted += 1
                        retry_reservation, retry_remaining, cooldown, retry_decision = self._reserve_quota(
                            budget_cost, ignore_cooldown=True
                        )
                        if retry_reservation is None:
                            failure_kind = attempt_kind
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
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if isinstance(exc, ProviderFailure):
                failure_kind = exc.kind
                failure_diagnostic = replace(
                    exc.diagnostic,
                    provider="gemini",
                    correlation_id=correlation_id,
                    model_id=exc.diagnostic.model_id or requested_model,
                    retries_attempted=retries_attempted,
                    prior_rate_limits=tuple(prior_rate_limits),
                    elapsed_seconds=exc.diagnostic.elapsed_seconds or elapsed_seconds,
                    request_id=exc.diagnostic.request_id or response_id,
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
                    elapsed_seconds=elapsed_seconds,
                    request_id=response_id,
                )
        finally:
            if sdk_client is not None:
                try:
                    await sdk_client.close()
                except Exception:
                    if failure_kind is None:
                        failure_kind = ProviderFailureKind.PROVIDER_FAILURE
                        failure_diagnostic = InferenceDiagnostic(
                            code=InferenceDiagnosticCode.GEMINI_PROVIDER_FAILURE,
                            stage=InferenceStage.PROVIDER_TRANSPORT,
                            provider="gemini",
                            model_id=requested_model,
                            elapsed_seconds=attempt_diagnostic.elapsed_seconds or elapsed_seconds,
                            request_id=attempt_diagnostic.request_id or response_id,
                        )
            secret = ""

        if failure_diagnostic is not None:
            tpm_budget_limit = (
                (self.config.tpm_limit_tokens - self.config.tpm_headroom_tokens)
                if self.config.tpm_limit_tokens is not None else None
            )
            failure_diagnostic = replace(
                failure_diagnostic,
                provider="gemini",
                correlation_id=correlation_id,
                model_id=failure_diagnostic.model_id or requested_model,
                agent_name=request.agent_name,
                estimated_input_tokens=estimated_input_tokens,
                output_token_limit=output_limit,
                budget_limit_tokens=tpm_budget_limit,
                budget_remaining_tokens=self._remaining_budget(),
                budget_decision=failure_diagnostic.budget_decision or (
                    "provider_rejected"
                    if failure_diagnostic.http_status == 429
                    else admission_decision
                ),
                retry_decision=failure_diagnostic.retry_decision or (
                    "retried" if retries_attempted else "not_retried"
                ),
                elapsed_seconds=failure_diagnostic.elapsed_seconds or elapsed_seconds,
                request_id=failure_diagnostic.request_id or response_id,
            )

        if failure_kind is not None:
            if not request_dispatched:
                for item in reservations:
                    self._settle_budget(item, None)
            if failure_diagnostic is not None and failure_diagnostic.http_status == 429:
                self._set_cooldown(failure_diagnostic)
            raise ProviderFailure(failure_kind, failure_diagnostic) from None

        if response is None:
            raise ProviderFailure(
                ProviderFailureKind.MALFORMED_RESPONSE,
                InferenceDiagnostic(
                    code=InferenceDiagnosticCode.GEMINI_MALFORMED_RESPONSE,
                    stage=InferenceStage.RESPONSE_VALIDATION,
                    provider="gemini",
                    model_id=requested_model,
                    elapsed_seconds=elapsed_seconds,
                    request_id=response_id,
                ),
            ) from None

        usage_values = extract_usage_counts(response)
        if usage_values[2] is not None:
            self._settle_budget(reservations[-1], usage_values[2])
        tpm_budget_limit = (
            (self.config.tpm_limit_tokens - self.config.tpm_headroom_tokens)
            if self.config.tpm_limit_tokens is not None else None
        )
        usage = InferenceTokenUsage(
            agent_name=request.agent_name,
            model_id=requested_model,
            correlation_id=correlation_id,
            estimated_input_tokens=estimated_input_tokens,
            actual_input_tokens=usage_values[0],
            actual_output_tokens=usage_values[1],
            actual_total_tokens=usage_values[2],
            output_token_limit=output_limit,
            budget_limit_tokens=tpm_budget_limit,
            budget_remaining_tokens=self._remaining_budget(),
            budget_decision="completed" if usage_values[2] is not None else "estimate_only",
            retries_attempted=retries_attempted,
            retry_decision="retried" if retries_attempted else "not_retried",
            request_id=getattr(response, "response_id", None) or response_id,
            elapsed_seconds=elapsed_seconds,
            finish_reason=getattr(response, "finish_reason", None) or finish_reason,
            response_character_count=(
                len(response.text) if getattr(response, "text", None) is not None else 0
            ),
            http_status=200,
        )
        return ProviderResponse(
            provider=self.provider_id,
            model_id=requested_model,
            response=response,
            usage=usage,
        )

    def _reserve_quota(
        self, token_count: int, *, ignore_cooldown: bool = False
    ) -> tuple[list[float | int] | None, int | None, float, str]:
        """Atomically reserve RPM, TPM, and RPD capacity before provider dispatch."""
        now = time.monotonic()
        with self._budget_lock:
            self._prune_events(now)
            cooldown_remaining = max(0.0, self._budget_blocked_until - now)
            if not ignore_cooldown and cooldown_remaining > 0:
                remaining_tpm = self._remaining_tpm_unlocked(now)
                return None, remaining_tpm, cooldown_remaining, "cooldown"

            # 1. RPM check
            if self.config.rpm_limit is not None:
                current_rpm = len(self._minute_events)
                if current_rpm >= self.config.rpm_limit:
                    remaining_tpm = self._remaining_tpm_unlocked(now)
                    return None, remaining_tpm, 0.0, "rpm_exceeded"

            # 2. RPD check
            if self.config.rpd_limit is not None:
                current_rpd = len(self._day_events)
                if current_rpd >= self.config.rpd_limit:
                    remaining_tpm = self._remaining_tpm_unlocked(now)
                    return None, remaining_tpm, 0.0, "rpd_exceeded"

            # 3. TPM check
            if self.config.tpm_limit_tokens is not None:
                limit = self.config.tpm_limit_tokens - self.config.tpm_headroom_tokens
                active_tpm = sum(int(event[1]) for event in self._minute_events)
                if active_tpm + token_count > limit:
                    remaining_tpm = max(0, limit - active_tpm)
                    return None, remaining_tpm, 0.0, "tpm_exceeded"

            event = [now, int(token_count)]
            self._minute_events.append(event)
            self._day_events.append(now)
            remaining_tpm = self._remaining_tpm_unlocked(now)
            admission_decision = "reserved" if self.config.has_quota_limits else "provider_managed"
            return event, remaining_tpm, 0.0, admission_decision

    def _settle_budget(self, event: list[float | int] | None, actual_tokens: int | None) -> None:
        if event is None:
            return
        with self._budget_lock:
            if event not in self._minute_events:
                return
            if actual_tokens is None:
                self._minute_events.remove(event)
            else:
                event[1] = max(0, int(actual_tokens))

    def _set_cooldown(self, diagnostic: InferenceDiagnostic) -> None:
        retry_after = diagnostic.retry_after_seconds
        reset_tokens = diagnostic.rate_limit_reset_tokens_seconds
        cooldown = max(
            retry_after or 0.0,
            reset_tokens or 0.0,
        )
        if cooldown <= 0:
            return
        cooldown = min(cooldown, 60.0)
        with self._budget_lock:
            self._budget_blocked_until = max(self._budget_blocked_until, time.monotonic() + cooldown)

    def _remaining_budget(self) -> int | None:
        now = time.monotonic()
        with self._budget_lock:
            return self._remaining_tpm_unlocked(now)

    def _remaining_tpm_unlocked(self, now: float) -> int | None:
        if self.config.tpm_limit_tokens is None:
            return None
        self._prune_events(now)
        limit = self.config.tpm_limit_tokens - self.config.tpm_headroom_tokens
        active = sum(int(event[1]) for event in self._minute_events)
        return max(0, limit - active)

    def _prune_events(self, now: float) -> None:
        minute_start = now - 60.0
        day_start = now - 86400.0
        self._minute_events = [event for event in self._minute_events if event[0] >= minute_start]
        self._day_events = [t for t in self._day_events if t >= day_start]


def estimate_input_tokens(messages: Sequence[Message]) -> int:
    """Non-secret token estimation for Gemini chat messages."""
    serialized = "".join(
        (f"{message.role}:{message.author_name or ''}:" + "".join(
            item.to_dict(exclude_none=False).get("text", "")
            if isinstance(item, Content)
            else (
                item if isinstance(item, str)
                else json.dumps(item, separators=(",", ":"), ensure_ascii=False)
            )
            for item in message.contents
        ))
        for message in messages
    )
    ascii_bytes = sum(1 for char in serialized if char.isascii())
    non_ascii_bytes = len(serialized.encode("utf-8")) - ascii_bytes
    return math.ceil(ascii_bytes / 4.0) + math.ceil(non_ascii_bytes / 3.0) + 12 * len(messages)


def extract_usage_counts(response: object) -> tuple[int | None, int | None, int | None]:
    """Read validated integer usage counters from the public response."""
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
        "model_not_found", "invalid_model_error"
    }


def _classify_status_error(exc: APIStatusError) -> ProviderFailureKind:
    if exc.status_code in (401, 403):
        return ProviderFailureKind.AUTHENTICATION
    if exc.status_code == 429:
        return ProviderFailureKind.RATE_LIMIT
    if exc.status_code in (400, 404) and _looks_like_invalid_model(exc):
        return ProviderFailureKind.INVALID_MODEL
    if exc.status_code == 504:
        return ProviderFailureKind.TIMEOUT
    return ProviderFailureKind.PROVIDER_FAILURE


def _classify_exception_chain(exc: BaseException) -> ProviderFailureKind:
    # Pass 1: check for timeouts explicitly first.
    # APITimeoutError is a subclass of APIConnectionError, so it MUST be checked before APIConnectionError.
    # We also check timeouts before CancelledError so timeouts with suppressed cancel in context are not misclassified.
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (APITimeoutError, TimeoutError)) or (
            type(current).__name__ in {
                "TimeoutException", "ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout"
            }
            and type(current).__module__.split(".")[0] in {"httpx", "httpx2"}
        ):
            return ProviderFailureKind.TIMEOUT
        current = current.__cause__ or current.__context__

    # Pass 2: check remaining error kinds
    seen.clear()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, asyncio.CancelledError):
            return ProviderFailureKind.CANCELLED
        if isinstance(current, AuthenticationError):
            return ProviderFailureKind.AUTHENTICATION
        if isinstance(current, RateLimitError):
            return ProviderFailureKind.RATE_LIMIT
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


def _extract_google_error_info(exc: BaseException | None) -> tuple[str | None, str | None]:
    if exc is None:
        return None, None
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return None, None
    error = body.get("error", body)
    if not isinstance(error, dict):
        return None, None
    status_val = error.get("status")
    status_str = str(status_val).strip() if status_val is not None else None
    if status_str and not re.fullmatch(r"[A-Za-z0-9_]{1,64}", status_str):
        status_str = None
    msg_val = error.get("message")
    msg_str = str(msg_val).strip() if msg_val is not None else None
    if msg_str:
        if len(msg_str) > 200:
            msg_str = msg_str[:197] + "..."
    return status_str, msg_str


def _diagnostic_for_exception(
    exc: BaseException,
    kind: ProviderFailureKind,
    model_id: str,
    *,
    correlation_id: str,
    retries_attempted: int,
    prior_rate_limits: tuple[RateLimitSnapshot, ...],
    elapsed_seconds: float | None = None,
    request_id: str | None = None,
    parsing_outcome: str | None = None,
) -> InferenceDiagnostic:
    status = None
    status_error: APIStatusError | None = None
    is_connect_timeout = False
    is_read_timeout = False
    is_generic_timeout = False
    is_cancelled = False
    seen: set[int] = set()
    current: BaseException | None = exc
    names: list[str] = []
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        name = type(current).__name__
        names.append(name)
        if name == "ConnectTimeout" or (isinstance(current, APITimeoutError) and "connect" in str(current).lower()):
            is_connect_timeout = True
        elif name == "ReadTimeout" or (isinstance(current, APITimeoutError) and "read" in str(current).lower()):
            is_read_timeout = True
        elif isinstance(current, (TimeoutError, APITimeoutError)) or name in {
            "TimeoutException", "WriteTimeout", "PoolTimeout",
        }:
            is_generic_timeout = True
        elif isinstance(current, asyncio.CancelledError):
            is_cancelled = True
        if isinstance(current, APIStatusError) and status is None:
            status = current.status_code
            status_error = current
        current = current.__cause__ or current.__context__

    # If APITimeoutError without specific cause, default to read timeout (the default HTTP timeout)
    if not is_connect_timeout and not is_read_timeout and not is_generic_timeout:
        if isinstance(exc, APITimeoutError):
            is_read_timeout = True

    # Map timeouts FIRST so suppressed CancelledError in __context__ never masks a timeout
    if is_connect_timeout:
        code = InferenceDiagnosticCode.GEMINI_CONNECT_TIMEOUT
        stage = InferenceStage.PROVIDER_TRANSPORT
    elif is_read_timeout:
        code = InferenceDiagnosticCode.GEMINI_READ_TIMEOUT
        stage = InferenceStage.PROVIDER_TRANSPORT
    elif status == 504:
        code = InferenceDiagnosticCode.GEMINI_GATEWAY_TIMEOUT
        stage = InferenceStage.PROVIDER_RESPONSE
    elif is_generic_timeout or (kind is ProviderFailureKind.TIMEOUT and status is None):
        code = InferenceDiagnosticCode.GEMINI_TIMEOUT
        stage = InferenceStage.PROVIDER_TRANSPORT
    elif is_cancelled or kind is ProviderFailureKind.CANCELLED:
        code = InferenceDiagnosticCode.REQUEST_CANCELLED
        stage = InferenceStage.PROVIDER_TRANSPORT
    elif status == 503:
        code = InferenceDiagnosticCode.GEMINI_SERVICE_UNAVAILABLE
        stage = InferenceStage.PROVIDER_RESPONSE
    elif status == 502:
        code = InferenceDiagnosticCode.GEMINI_BAD_GATEWAY
        stage = InferenceStage.PROVIDER_RESPONSE
    elif status == 504:
        code = InferenceDiagnosticCode.GEMINI_GATEWAY_TIMEOUT
        stage = InferenceStage.PROVIDER_RESPONSE
    elif kind is ProviderFailureKind.AUTHENTICATION:
        code = InferenceDiagnosticCode.GEMINI_AUTHENTICATION_FAILED
        stage = InferenceStage.PROVIDER_RESPONSE if status is not None else InferenceStage.PROVIDER_TRANSPORT
    elif kind is ProviderFailureKind.INVALID_MODEL:
        code = InferenceDiagnosticCode.GEMINI_INVALID_MODEL
        stage = InferenceStage.PROVIDER_RESPONSE
    elif kind is ProviderFailureKind.NETWORK:
        code = InferenceDiagnosticCode.GEMINI_NETWORK_FAILURE
        stage = InferenceStage.PROVIDER_TRANSPORT
    elif kind is ProviderFailureKind.RATE_LIMIT:
        stage = InferenceStage.PROVIDER_RESPONSE
        code = (
            InferenceDiagnosticCode.GEMINI_QUOTA_EXHAUSTED
            if _looks_like_quota_exhausted(status_error)
            else InferenceDiagnosticCode.GEMINI_RATE_LIMITED
        )
    elif kind is ProviderFailureKind.MALFORMED_RESPONSE:
        code = InferenceDiagnosticCode.GEMINI_MALFORMED_RESPONSE
        stage = InferenceStage.RESPONSE_VALIDATION
    elif kind is ProviderFailureKind.SENSITIVE_INPUT:
        code = InferenceDiagnosticCode.INVALID_REQUEST
        stage = InferenceStage.REQUEST_VALIDATION
    elif status == 400:
        code = InferenceDiagnosticCode.GEMINI_REQUEST_REJECTED
        stage = InferenceStage.PROVIDER_RESPONSE
    else:
        code = InferenceDiagnosticCode.GEMINI_PROVIDER_FAILURE
        stage = InferenceStage.PROVIDER_RESPONSE if status is not None else InferenceStage.PROVIDER_TRANSPORT

    headers = getattr(status_error, "headers", None)
    if headers is None:
        response_obj = getattr(status_error, "response", None)
        headers = getattr(response_obj, "headers", None)
    err_req_id = getattr(status_error, "request_id", None)
    header_req_id = None
    if isinstance(headers, Mapping):
        lower_headers = {str(k).lower(): v for k, v in headers.items() if isinstance(v, str)}
        header_req_id = (
            lower_headers.get("x-goog-request-id")
            or lower_headers.get("goog-request-id")
            or lower_headers.get("x-request-id")
            or lower_headers.get("request-id")
        )
    final_request_id = request_id or err_req_id or header_req_id
    retry_after = _parse_retry_after(headers)
    scope = _classify_rate_limit_scope(status_error) if kind is ProviderFailureKind.RATE_LIMIT else None
    remaining_req, remaining_tok, reset_req, reset_tok = _parse_rate_limit_headers(headers)
    error_status, error_message = _extract_google_error_info(status_error)
    exception_type_chain = " -> ".join(names) if names else None
    if exception_type_chain and len(exception_type_chain) > 200:
        exception_type_chain = exception_type_chain[:197] + "..."

    return InferenceDiagnostic(
        code=code,
        stage=stage,
        provider="gemini",
        model_id=model_id,
        http_status=status,
        rate_limit_scope=scope,
        retry_after_seconds=retry_after,
        rate_limit_remaining_requests=remaining_req,
        rate_limit_remaining_tokens=remaining_tok,
        rate_limit_reset_requests_seconds=reset_req,
        rate_limit_reset_tokens_seconds=reset_tok,
        retries_attempted=retries_attempted,
        prior_rate_limits=prior_rate_limits,
        correlation_id=correlation_id,
        request_id=final_request_id,
        elapsed_seconds=elapsed_seconds,
        parsing_outcome=parsing_outcome,
        provider_error_status=error_status,
        provider_error_message=error_message,
        exception_type_chain=exception_type_chain,
    )


def _looks_like_quota_exhausted(exc: APIStatusError | None) -> bool:
    if exc is None:
        return False
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return False
    error = body.get("error", body)
    if not isinstance(error, dict):
        return False
    code = error.get("code")
    message = str(error.get("message", "")).lower()
    return (
        code in {"insufficient_quota", "quota_exhausted", "RESOURCE_EXHAUSTED"}
        or "quota" in message
        or "resource_exhausted" in message
    )


def _classify_rate_limit_scope(exc: APIStatusError | None) -> RateLimitScope:
    if exc is None:
        return RateLimitScope.UNKNOWN
    if _looks_like_quota_exhausted(exc):
        return RateLimitScope.ACCOUNT_QUOTA
    headers = getattr(exc, "headers", None)
    remaining_req, remaining_tok, reset_req, reset_tok = _parse_rate_limit_headers(headers)
    req_indicated = (remaining_req == 0) or (reset_req is not None and reset_req > 0)
    tok_indicated = (remaining_tok == 0) or (reset_tok is not None and reset_tok > 0)
    if req_indicated and tok_indicated:
        return RateLimitScope.MULTIPLE
    if req_indicated:
        return RateLimitScope.REQUESTS_PER_MINUTE
    if tok_indicated:
        return RateLimitScope.TOKENS_PER_MINUTE
    return RateLimitScope.UNKNOWN


def _parse_retry_after(headers: object) -> float | None:
    if not isinstance(headers, Mapping):
        return None
    raw = headers.get("retry-after")
    if not isinstance(raw, str):
        return None
    try:
        val = float(raw.strip())
        return val if (0.0 <= val <= _MAX_DIAGNOSTIC_DURATION_SECONDS) else None
    except ValueError:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(raw.strip())
        now = datetime.now(timezone.utc)
        diff = (parsed - now).total_seconds()
        return max(0.0, diff) if (0.0 <= diff <= _MAX_DIAGNOSTIC_DURATION_SECONDS) else None
    except Exception:
        return None


def _parse_duration_string(value: str | None) -> float | None:
    if not isinstance(value, str):
        return None
    clean = value.strip()
    match = _RATE_RESET_COMPONENT.fullmatch(clean)
    if not match:
        return None
    num_str, unit = match.groups()
    num = float(num_str)
    multipliers = {
        "ms": 0.001,
        "s": 1.0,
        "m": 60.0,
        "h": 3600.0,
        "d": 86400.0,
    }
    secs = num * multipliers.get(unit, 1.0)
    return secs if (0.0 <= secs <= _MAX_DIAGNOSTIC_DURATION_SECONDS) else None


def _parse_rate_limit_headers(headers: object) -> tuple[int | None, int | None, float | None, float | None]:
    if not isinstance(headers, Mapping):
        return None, None, None, None

    def read_int(name: str) -> int | None:
        raw = headers.get(name)
        if isinstance(raw, str):
            try:
                v = int(raw.strip())
                return v if v >= 0 else None
            except ValueError:
                return None
        return None

    def read_reset(name: str) -> float | None:
        raw = headers.get(name)
        if isinstance(raw, str):
            try:
                v = float(raw.strip())
                return v if (0.0 <= v <= _MAX_DIAGNOSTIC_DURATION_SECONDS) else None
            except ValueError:
                return _parse_duration_string(raw)
        return None

    remaining_req = read_int("x-ratelimit-remaining-requests")
    remaining_tok = read_int("x-ratelimit-remaining-tokens")
    reset_req = read_reset("x-ratelimit-reset-requests")
    reset_tok = read_reset("x-ratelimit-reset-tokens")
    return remaining_req, remaining_tok, reset_req, reset_tok


def _request_is_safe_to_retry(request: InferenceRequest) -> bool:
    options = request.options or {}
    return "tools" not in options and "functions" not in options


def _rate_limit_retry_delay(diagnostic: InferenceDiagnostic) -> float | None:
    delay = diagnostic.retry_after_seconds
    if delay is None and diagnostic.rate_limit_scope is RateLimitScope.REQUESTS_PER_MINUTE:
        delay = diagnostic.rate_limit_reset_requests_seconds
    if delay is None and diagnostic.rate_limit_scope is RateLimitScope.TOKENS_PER_MINUTE:
        delay = diagnostic.rate_limit_reset_tokens_seconds
    if delay is not None:
        if delay > _MAX_AUTOMATIC_RETRY_WAIT_SECONDS:
            return None
        return delay + random.uniform(0.0, _RETRY_JITTER_SECONDS)
    return _RETRY_BACKOFF_BASE_SECONDS + random.uniform(0.0, _RETRY_JITTER_SECONDS)


def _transient_retry_delay(
    diagnostic: InferenceDiagnostic,
    *,
    elapsed_seconds: float | None = None,
    timeout_seconds: float = 60.0,
    options: Mapping[str, object] | None = None,
) -> float | None:
    retry_after = diagnostic.retry_after_seconds
    if retry_after is not None:
        if retry_after > _MAX_TRANSIENT_RETRY_WAIT_SECONDS:
            return None
        delay = retry_after + random.uniform(0.1, 0.4)
    else:
        delay = random.uniform(_TRANSIENT_BACKOFF_MIN_SECONDS, _TRANSIENT_BACKOFF_MAX_SECONDS)

    opts = options or {}
    deadline_val = opts.get("deadline_seconds")
    if isinstance(deadline_val, (int, float)) and deadline_val > 0:
        budget_time = float(deadline_val)
    else:
        budget_time = float(opts.get("timeout", timeout_seconds))

    elapsed = elapsed_seconds or 0.0
    if elapsed + delay >= budget_time:
        return None
    return delay


def _snapshot_from_diagnostic(diagnostic: InferenceDiagnostic) -> RateLimitSnapshot | None:
    if diagnostic.http_status != 429:
        return None
    return RateLimitSnapshot(
        http_status=429,
        scope=diagnostic.rate_limit_scope or RateLimitScope.UNKNOWN,
        retry_after_seconds=diagnostic.retry_after_seconds,
        remaining_requests=diagnostic.rate_limit_remaining_requests,
        remaining_tokens=diagnostic.rate_limit_remaining_tokens,
        reset_requests_seconds=diagnostic.rate_limit_reset_requests_seconds,
        reset_tokens_seconds=diagnostic.rate_limit_reset_tokens_seconds,
    )

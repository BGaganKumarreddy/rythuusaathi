"""Provider-independent inference and credential contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum, StrEnum
import re
import uuid
import math
from threading import Lock
from types import ModuleType
from typing import Any, Protocol, Sequence, runtime_checkable

from agent_framework import ChatOptions, ChatResponse, Content, FunctionTool, Message


class ProviderFailureKind(StrEnum):
    AUTHENTICATION = "authentication"
    SENSITIVE_INPUT = "sensitive_input"
    INVALID_MODEL = "invalid_model"
    TIMEOUT = "timeout"
    NETWORK = "network_failure"
    RATE_LIMIT = "rate_limit"
    MALFORMED_RESPONSE = "malformed_response"
    CANCELLED = "cancelled"
    PAYMENT_REQUIRED = "payment_required"
    PROVIDER_FAILURE = "provider_failure"


class InferenceDiagnosticCode(StrEnum):
    SESSION_AUTHENTICATION_FAILED = "session_authentication_failed"
    IDENTITY_MISMATCH = "identity_mismatch"
    IDENTITY_SERVICE_FAILURE = "identity_service_failure"
    CREDENTIAL_UNAVAILABLE = "credential_unavailable"
    CREDENTIAL_DECRYPTION_FAILED = "credential_decryption_failed"
    CREDENTIAL_SERVICE_UNAVAILABLE = "credential_service_unavailable"
    GROQ_AUTHENTICATION_FAILED = "groq_authentication_failed"
    GROQ_INVALID_MODEL = "groq_invalid_model"
    GROQ_TIMEOUT = "groq_timeout"
    GROQ_NETWORK_FAILURE = "groq_network_failure"
    GROQ_RATE_LIMITED = "groq_rate_limited"
    GROQ_QUOTA_EXHAUSTED = "groq_quota_exhausted"
    GROQ_TPM_BUDGET_EXCEEDED = "groq_tpm_budget_exceeded"
    GROQ_MALFORMED_RESPONSE = "groq_malformed_response"
    GROQ_REQUEST_REJECTED = "groq_request_rejected"
    GROQ_PROVIDER_FAILURE = "groq_provider_failure"
    GEMINI_AUTHENTICATION_FAILED = "gemini_authentication_failed"
    GEMINI_INVALID_MODEL = "gemini_invalid_model"
    GEMINI_TIMEOUT = "gemini_timeout"
    GEMINI_CONNECT_TIMEOUT = "gemini_connect_timeout"
    GEMINI_READ_TIMEOUT = "gemini_read_timeout"
    GEMINI_NETWORK_FAILURE = "gemini_network_failure"
    GEMINI_RATE_LIMITED = "gemini_rate_limited"
    GEMINI_QUOTA_EXHAUSTED = "gemini_quota_exhausted"
    GEMINI_TPM_BUDGET_EXCEEDED = "gemini_tpm_budget_exceeded"
    GEMINI_MALFORMED_RESPONSE = "gemini_malformed_response"
    GEMINI_REQUEST_REJECTED = "gemini_request_rejected"
    GEMINI_SERVICE_UNAVAILABLE = "gemini_service_unavailable"
    GEMINI_BAD_GATEWAY = "gemini_bad_gateway"
    GEMINI_GATEWAY_TIMEOUT = "gemini_gateway_timeout"
    GEMINI_PROVIDER_FAILURE = "gemini_provider_failure"
    CEREBRAS_AUTHENTICATION_FAILED = "cerebras_authentication_failed"
    CEREBRAS_INVALID_MODEL = "cerebras_invalid_model"
    CEREBRAS_TIMEOUT = "cerebras_timeout"
    CEREBRAS_CONNECT_TIMEOUT = "cerebras_connect_timeout"
    CEREBRAS_READ_TIMEOUT = "cerebras_read_timeout"
    CEREBRAS_NETWORK_FAILURE = "cerebras_network_failure"
    CEREBRAS_RATE_LIMITED = "cerebras_rate_limited"
    CEREBRAS_QUOTA_EXHAUSTED = "cerebras_quota_exhausted"
    CEREBRAS_TPM_BUDGET_EXCEEDED = "cerebras_tpm_budget_exceeded"
    CEREBRAS_MALFORMED_RESPONSE = "cerebras_malformed_response"
    CEREBRAS_REQUEST_REJECTED = "cerebras_request_rejected"
    CEREBRAS_PAYMENT_REQUIRED = "cerebras_payment_required"
    CEREBRAS_SERVICE_UNAVAILABLE = "cerebras_service_unavailable"
    CEREBRAS_BAD_GATEWAY = "cerebras_bad_gateway"
    CEREBRAS_GATEWAY_TIMEOUT = "cerebras_gateway_timeout"
    CEREBRAS_PROVIDER_FAILURE = "cerebras_provider_failure"
    WORKFLOW_TIMEOUT = "workflow_timeout"
    REQUEST_CANCELLED = "request_cancelled"
    INVALID_REQUEST = "invalid_request"


class InferenceStage(StrEnum):
    SESSION_AUTHENTICATION = "session_authentication"
    IDENTITY_BINDING = "identity_binding"
    CREDENTIAL_LOOKUP = "credential_lookup"
    CREDENTIAL_DECRYPTION = "credential_decryption"
    REQUEST_VALIDATION = "request_validation"
    PROVIDER_TRANSPORT = "provider_transport"
    PROVIDER_RESPONSE = "provider_response"
    RESPONSE_VALIDATION = "response_validation"
    WORKFLOW_EXECUTION = "workflow_execution"
    CREDENTIAL_SERVICE = "credential_service"
    IDENTITY_SERVICE = "identity_service"


class RateLimitScope(StrEnum):
    REQUESTS_PER_MINUTE = "requests_per_minute"
    REQUESTS_PER_DAY = "requests_per_day"
    TOKENS_PER_MINUTE = "tokens_per_minute"
    TOKENS_PER_DAY = "tokens_per_day"
    ACCOUNT_QUOTA = "account_quota"
    MULTIPLE = "multiple_limits"
    UNKNOWN = "unknown"


_SAFE_METADATA = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,119}\Z")


@dataclass(frozen=True, slots=True)
class InferenceDiagnostic:
    """Sanitized inference failure metadata; never contains exception text."""

    code: InferenceDiagnosticCode
    stage: InferenceStage
    provider: str = "groq"
    model_id: str | None = None
    http_status: int | None = None
    rate_limit_scope: RateLimitScope | None = None
    retry_after_seconds: float | None = None
    rate_limit_remaining_requests: int | None = None
    rate_limit_remaining_tokens: int | None = None
    rate_limit_reset_requests_seconds: float | None = None
    rate_limit_reset_tokens_seconds: float | None = None
    retries_attempted: int = 0
    prior_rate_limits: tuple["RateLimitSnapshot", ...] = ()
    agent_name: str | None = None
    estimated_input_tokens: int | None = None
    output_token_limit: int | None = None
    budget_limit_tokens: int | None = None
    budget_remaining_tokens: int | None = None
    budget_decision: str | None = None
    retry_decision: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    request_id: str | None = None
    elapsed_seconds: float | None = None
    response_character_count: int | None = None
    finish_reason: str | None = None
    parsing_outcome: str | None = None
    validation_category: str | None = None
    provider_error_code: int | str | None = None
    provider_error_status: str | None = None
    provider_error_message: str | None = None
    trace_id: str | None = None
    sanitized_response_headers: tuple[tuple[str, str], ...] = ()
    exception_type_chain: str | None = None

    @property
    def response_headers_dict(self) -> dict[str, str]:
        """Mapping view of sanitized response headers."""
        return dict(self.sanitized_response_headers)

    def __post_init__(self) -> None:
        if not isinstance(self.code, InferenceDiagnosticCode):
            raise ValueError("diagnostic code must be typed")
        if not isinstance(self.stage, InferenceStage):
            raise ValueError("diagnostic stage must be typed")
        if not isinstance(self.provider, str) or not _SAFE_METADATA.fullmatch(self.provider):
            raise ValueError("diagnostic provider must be safe metadata")
        if self.model_id is not None and (
            not isinstance(self.model_id, str) or not _SAFE_METADATA.fullmatch(self.model_id)
        ):
            object.__setattr__(self, "model_id", None)
        if self.http_status is not None and (
            isinstance(self.http_status, bool) or not isinstance(self.http_status, int)
            or not 100 <= self.http_status <= 599
        ):
            raise ValueError("diagnostic HTTP status must be a valid status code")
        if self.rate_limit_scope is not None and not isinstance(
            self.rate_limit_scope, RateLimitScope
        ):
            raise ValueError("rate-limit scope must be typed")
        if isinstance(self.retries_attempted, bool) or self.retries_attempted not in (0, 1):
            raise ValueError("diagnostic retry count is outside the bounded retry policy")
        for name in (
            "retry_after_seconds", "rate_limit_reset_requests_seconds",
            "rate_limit_reset_tokens_seconds",
        ):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < 0 or value > 31_536_000
            ):
                raise ValueError(f"diagnostic {name} is invalid")
        for name in ("rate_limit_remaining_requests", "rate_limit_remaining_tokens"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"diagnostic {name} is invalid")
        if any(not isinstance(item, RateLimitSnapshot) for item in self.prior_rate_limits):
            raise ValueError("prior rate-limit metadata must be typed")
        if self.agent_name is not None and not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", self.agent_name):
            raise ValueError("diagnostic agent name must be safe metadata")
        for name in (
            "estimated_input_tokens", "output_token_limit", "budget_limit_tokens",
            "budget_remaining_tokens",
        ):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"diagnostic {name} is invalid")
        if self.budget_decision is not None and self.budget_decision not in {
            "reserved", "rejected", "provider_rejected", "completed", "estimate_only", "provider_managed",
        }:
            raise ValueError("diagnostic budget decision is invalid")
        if self.retry_decision is not None and self.retry_decision not in {
            "retried", "not_retried", "budget_rejected",
        }:
            raise ValueError("diagnostic retry decision is invalid")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("diagnostic timestamp must be timezone-aware")
        if not isinstance(self.correlation_id, str) or not re.fullmatch(
            r"[0-9a-f]{32}", self.correlation_id
        ):
            raise ValueError("diagnostic correlation ID must be a UUID hex value")
        if self.request_id is not None and (
            not isinstance(self.request_id, str) or not _SAFE_METADATA.fullmatch(self.request_id)
        ):
            object.__setattr__(self, "request_id", None)
        if self.elapsed_seconds is not None and (
            isinstance(self.elapsed_seconds, bool) or not isinstance(self.elapsed_seconds, (int, float))
            or not math.isfinite(self.elapsed_seconds) or self.elapsed_seconds < 0 or self.elapsed_seconds > 31_536_000
        ):
            raise ValueError("diagnostic elapsed_seconds is invalid")
        if self.response_character_count is not None and (
            isinstance(self.response_character_count, bool) or not isinstance(self.response_character_count, int)
            or self.response_character_count < 0
        ):
            raise ValueError("diagnostic response_character_count is invalid")
        if self.finish_reason is not None and (
            not isinstance(self.finish_reason, str) or not _SAFE_METADATA.fullmatch(self.finish_reason)
        ):
            object.__setattr__(self, "finish_reason", None)
        if self.parsing_outcome is not None and (
            not isinstance(self.parsing_outcome, str) or not _SAFE_METADATA.fullmatch(self.parsing_outcome)
        ):
            object.__setattr__(self, "parsing_outcome", None)
        if self.validation_category is not None and (
            not isinstance(self.validation_category, str) or not _SAFE_METADATA.fullmatch(self.validation_category)
        ):
            object.__setattr__(self, "validation_category", None)
        if self.provider_error_code is not None and (
            isinstance(self.provider_error_code, bool)
            or (not isinstance(self.provider_error_code, int) and (
                not isinstance(self.provider_error_code, str)
                or not _SAFE_METADATA.fullmatch(self.provider_error_code)
            ))
            or (isinstance(self.provider_error_code, int) and not (0 <= self.provider_error_code <= 99999))
        ):
            object.__setattr__(self, "provider_error_code", None)
        if self.provider_error_status is not None and (
            not isinstance(self.provider_error_status, str) or not _SAFE_METADATA.fullmatch(self.provider_error_status)
        ):
            object.__setattr__(self, "provider_error_status", None)
        if self.provider_error_message is not None and (
            not isinstance(self.provider_error_message, str) or len(self.provider_error_message) > 300
        ):
            object.__setattr__(self, "provider_error_message", None)
        if self.trace_id is not None and (
            not isinstance(self.trace_id, str) or not _SAFE_METADATA.fullmatch(self.trace_id)
        ):
            object.__setattr__(self, "trace_id", None)
        if any(
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], str)
            or not isinstance(item[1], str)
            for item in self.sanitized_response_headers
        ):
            object.__setattr__(self, "sanitized_response_headers", ())
        if self.exception_type_chain is not None and (
            not isinstance(self.exception_type_chain, str)
            or not re.fullmatch(r"[A-Za-z0-9_> -]{1,200}", self.exception_type_chain)
        ):
            object.__setattr__(self, "exception_type_chain", None)


class ProviderFailure(RuntimeError):
    """Safe public error that contains no provider response or secret material."""

    def __init__(self, kind: ProviderFailureKind,
                 diagnostic: InferenceDiagnostic | None = None):
        self.kind = kind
        self.diagnostic = diagnostic or _default_diagnostic(kind)
        super().__init__(f"Inference provider failed ({kind.value})")


def _default_diagnostic(kind: ProviderFailureKind) -> InferenceDiagnostic:
    code, stage = {
        ProviderFailureKind.AUTHENTICATION: (
            InferenceDiagnosticCode.GROQ_AUTHENTICATION_FAILED,
            InferenceStage.PROVIDER_TRANSPORT,
        ),
        ProviderFailureKind.INVALID_MODEL: (
            InferenceDiagnosticCode.GROQ_INVALID_MODEL,
            InferenceStage.PROVIDER_RESPONSE,
        ),
        ProviderFailureKind.TIMEOUT: (
            InferenceDiagnosticCode.GROQ_TIMEOUT,
            InferenceStage.PROVIDER_TRANSPORT,
        ),
        ProviderFailureKind.NETWORK: (
            InferenceDiagnosticCode.GROQ_NETWORK_FAILURE,
            InferenceStage.PROVIDER_TRANSPORT,
        ),
        ProviderFailureKind.RATE_LIMIT: (
            InferenceDiagnosticCode.GROQ_RATE_LIMITED,
            InferenceStage.PROVIDER_RESPONSE,
        ),
        ProviderFailureKind.MALFORMED_RESPONSE: (
            InferenceDiagnosticCode.GROQ_MALFORMED_RESPONSE,
            InferenceStage.RESPONSE_VALIDATION,
        ),
        ProviderFailureKind.SENSITIVE_INPUT: (
            InferenceDiagnosticCode.INVALID_REQUEST,
            InferenceStage.REQUEST_VALIDATION,
        ),
        ProviderFailureKind.CANCELLED: (
            InferenceDiagnosticCode.REQUEST_CANCELLED,
            InferenceStage.PROVIDER_TRANSPORT,
        ),
        ProviderFailureKind.PAYMENT_REQUIRED: (
            InferenceDiagnosticCode.CEREBRAS_PAYMENT_REQUIRED,
            InferenceStage.PROVIDER_RESPONSE,
        ),
        ProviderFailureKind.PROVIDER_FAILURE: (
            InferenceDiagnosticCode.GROQ_PROVIDER_FAILURE,
            InferenceStage.PROVIDER_TRANSPORT,
        ),
    }[kind]
    return InferenceDiagnostic(code=code, stage=stage)


@dataclass(frozen=True, slots=True)
class RateLimitSnapshot:
    """Sanitized rate-limit metadata retained from an earlier bounded attempt."""

    http_status: int = 429
    scope: RateLimitScope = RateLimitScope.UNKNOWN
    retry_after_seconds: float | None = None
    remaining_requests: int | None = None
    remaining_tokens: int | None = None
    reset_requests_seconds: float | None = None
    reset_tokens_seconds: float | None = None


class ProviderCredential:
    """Transient credential passed only from a protected backend service.

    This object is not persistence, encryption, authentication, or authorization.
    Python does not guarantee zeroization; callers must keep its lifetime short.
    """

    __slots__ = ("__value", "__lock")

    def __init__(self, value: str):
        if not value or not value.strip():
            raise ProviderFailure(ProviderFailureKind.AUTHENTICATION)
        self.__value = value
        self.__lock = Lock()

    def __repr__(self) -> str:
        return "ProviderCredential(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"

    def __reduce_ex__(self, _protocol: int):
        raise TypeError("provider credentials cannot be serialized")

    def _consume_for_provider(self) -> str:
        with self.__lock:
            value = self.__value
            if not value:
                raise ProviderFailure(ProviderFailureKind.AUTHENTICATION)
            self.__value = ""
            return value

    def _request_contains_secret(self, request: Any) -> bool:
        """Inspect request material under the credential lock without consuming it."""
        with self.__lock:
            if not self.__value:
                raise ProviderFailure(ProviderFailureKind.AUTHENTICATION)
            return contains_provider_secret(request, self.__value)


@dataclass(frozen=True, slots=True, repr=False)
class InferenceRequest:
    """A framework-native chat request. It deliberately has no credential field."""

    messages: Sequence[Message]
    options: ChatOptions | None = None
    agent_name: str = "unknown"
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def __post_init__(self) -> None:
        if not isinstance(self.agent_name, str) or not re.fullmatch(
            r"[a-z][a-z0-9_]{0,39}", self.agent_name
        ):
            raise ValueError("agent_name must be a safe lowercase identifier")
        if not isinstance(self.correlation_id, str) or not re.fullmatch(
            r"[0-9a-f]{32}", self.correlation_id
        ):
            raise ValueError("correlation_id must be a UUID hex value")

    def __repr__(self) -> str:
        return "InferenceRequest(<messages redacted>)"


@dataclass(frozen=True, slots=True)
class InferenceTokenUsage:
    """Sanitized token and local budget counters; never includes prompt text."""

    agent_name: str
    model_id: str
    correlation_id: str
    estimated_input_tokens: int
    actual_input_tokens: int | None = None
    actual_output_tokens: int | None = None
    actual_total_tokens: int | None = None
    output_token_limit: int = 0
    budget_limit_tokens: int | None = None
    budget_remaining_tokens: int | None = None
    budget_decision: str = "estimate_only"
    retries_attempted: int = 0
    retry_decision: str = "not_retried"
    request_id: str | None = None
    elapsed_seconds: float | None = None
    finish_reason: str | None = None
    response_character_count: int | None = None
    http_status: int | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", self.agent_name):
            raise ValueError("usage agent name must be safe metadata")
        if not _SAFE_METADATA.fullmatch(self.model_id):
            raise ValueError("usage model identifier must be safe metadata")
        if not re.fullmatch(r"[0-9a-f]{32}", self.correlation_id):
            raise ValueError("usage correlation ID must be a UUID hex value")
        for name in (
            "estimated_input_tokens", "actual_input_tokens", "actual_output_tokens",
            "actual_total_tokens", "output_token_limit", "budget_limit_tokens",
            "budget_remaining_tokens",
        ):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"usage {name} is invalid")
        if self.budget_decision not in {
            "reserved", "rejected", "provider_rejected", "completed", "estimate_only", "provider_managed",
        }:
            raise ValueError("usage budget decision is invalid")
        if isinstance(self.retries_attempted, bool) or self.retries_attempted not in (0, 1):
            raise ValueError("usage retry count is invalid")
        if self.retry_decision not in {"retried", "not_retried", "budget_rejected"}:
            raise ValueError("usage retry decision is invalid")
        if self.request_id is not None and (
            not isinstance(self.request_id, str) or not _SAFE_METADATA.fullmatch(self.request_id)
        ):
            object.__setattr__(self, "request_id", None)
        if self.elapsed_seconds is not None and (
            isinstance(self.elapsed_seconds, bool) or not isinstance(self.elapsed_seconds, (int, float))
            or not math.isfinite(self.elapsed_seconds) or self.elapsed_seconds < 0 or self.elapsed_seconds > 31_536_000
        ):
            raise ValueError("usage elapsed_seconds is invalid")
        if self.finish_reason is not None and (
            not isinstance(self.finish_reason, str) or not _SAFE_METADATA.fullmatch(self.finish_reason)
        ):
            object.__setattr__(self, "finish_reason", None)
        if self.response_character_count is not None and (
            isinstance(self.response_character_count, bool) or not isinstance(self.response_character_count, int)
            or self.response_character_count < 0
        ):
            raise ValueError("usage response_character_count is invalid")
        if self.http_status is not None and (
            isinstance(self.http_status, bool) or not isinstance(self.http_status, int)
            or not 100 <= self.http_status <= 599
        ):
            raise ValueError("usage http_status is invalid")


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    provider: str
    model_id: str
    response: ChatResponse = field(repr=False)
    usage: InferenceTokenUsage | None = None


def sanitized_chat_response(response: ChatResponse, secret: str) -> ChatResponse:
    """Return a copied framework response, fail-closed on unsupported objects.

    Microsoft Agent Framework's public ``to_dict``/``from_dict`` APIs define
    the supported response shape. SDK raw representations are intentionally
    omitted by those APIs. Unknown nested objects fail closed. Structured
    Pydantic output is handled separately because the framework serializer
    intentionally omits ``ChatResponse.value``.
    """
    if not isinstance(response, ChatResponse):
        raise ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE)

    def scrub(value: Any) -> Any:
        if value is None or isinstance(value, (bool, int, float, date, datetime, type)):
            return value
        if isinstance(value, Enum):
            return scrub(value.value)
        if isinstance(value, str):
            return value.replace(secret, "[REDACTED]")
        if isinstance(value, Content):
            return Content.from_dict(scrub(value.to_dict(exclude_none=False)))
        if isinstance(value, Message):
            # Message.to_dict skips unsupported values, so reject them before
            # invoking the serializer instead of allowing silent data loss.
            if not isinstance(value.contents, Sequence) or isinstance(value.contents, (str, bytes)):
                raise ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE)
            if any(not isinstance(item, (Content, str, Mapping)) for item in value.contents):
                raise ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE)
            safe_contents = [scrub(item) for item in value.contents]
            # Framework serializers may omit unsupported metadata values, so
            # validate public metadata independently before round-tripping.
            scrub(value.additional_properties or {})
            message_data = scrub(value.to_dict(exclude_none=False))
            message_data.pop("type", None)
            message_data["contents"] = [
                item.to_dict(exclude_none=False) if isinstance(item, Content) else item
                for item in safe_contents
            ]
            return Message.from_dict(message_data)
        if isinstance(value, Mapping):
            safe = {}
            for key, item in value.items():
                safe_key = scrub(key)
                if not isinstance(safe_key, (str, int, float, bool)):
                    raise ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE)
                safe[safe_key] = scrub(item)
            return safe
        if isinstance(value, list):
            return [scrub(item) for item in value]
        if isinstance(value, tuple):
            return tuple(scrub(item) for item in value)
        # Framework structured output may be a Pydantic model. Use its public
        # JSON API, recursively scrub, and retain only JSON-safe data.
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump(mode="json")
            if isinstance(dumped, Mapping):
                safe_dump = scrub(dumped)
                model_validate = getattr(type(value), "model_validate", None)
                if callable(model_validate):
                    return model_validate(safe_dump)
                return safe_dump
        raise ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE)

    messages = response.messages or []
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        raise ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE)
    if any(not isinstance(message, Message) for message in messages):
        raise ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE)
    safe_messages = [scrub(message) for message in messages]
    safe_data = scrub(response.to_dict(exclude_none=False))
    safe_data.pop("type", None)
    # A framework Message serializer may omit non-serializable nested values.
    # The prevalidated public Message round-trips above are authoritative.
    safe_data.pop("messages", None)
    try:
        # ChatResponse's public serializer intentionally omits value,
        # response_format, and additional_properties; pass those through the
        # public constructor after independently sanitizing them.
        parsed_value = None
        try:
            raw_value = getattr(response, "value", None)
            if raw_value is not None:
                parsed_value = scrub(raw_value)
        except Exception:
            parsed_value = None

        return ChatResponse(
            **safe_data,
            messages=safe_messages,
            value=parsed_value,
            response_format=(
                scrub(getattr(response, "response_format", None))
                if getattr(response, "response_format", None) is not None else None
            ),
            additional_properties=scrub(response.additional_properties or {}),
        )
    except ProviderFailure:
        raise
    except Exception:
        raise ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE) from None


def contains_provider_secret(value: Any, secret: str, seen: set[int] | None = None) -> bool:
    """Check supported request containers and tool closures before dispatch."""
    if not secret:
        return False
    try:
        return _contains_provider_secret(value, secret, seen)
    except Exception:
        # A failed inspection of caller-controlled or framework-supported tool
        # metadata must reject the request instead of allowing it through.
        return True


def _contains_provider_secret(value: Any, secret: str, seen: set[int] | None = None) -> bool:
    if isinstance(value, str):
        return secret in value
    if isinstance(value, ProviderCredential):
        return True
    if value is None or isinstance(value, (bool, int, float, date, datetime, Enum, type)):
        return False
    if isinstance(value, bytes):
        try:
            return secret in value.decode("utf-8")
        except UnicodeDecodeError:
            return False

    seen = seen if seen is not None else set()
    if id(value) in seen:
        return False
    seen.add(id(value))

    if isinstance(value, Message):
        fields = (
            value.role, value.contents, value.author_name, value.message_id,
            value.additional_properties, value.raw_representation,
        )
        return any(contains_provider_secret(item, secret, seen) for item in fields)
    if isinstance(value, InferenceRequest):
        return contains_provider_secret(value.messages, secret, seen) or contains_provider_secret(
            value.options, secret, seen
        )
    if isinstance(value, Content):
        try:
            fields = [value.to_dict(exclude_none=False)]
        except Exception:
            fields = []
        fields.append(getattr(value, "raw_representation", None))
        return any(contains_provider_secret(item, secret, seen) for item in fields)
    if isinstance(value, FunctionTool):
        # FunctionTool.to_dict() intentionally omits additional_properties and
        # the callable. Inspect those supported public surfaces explicitly.
        candidates = [
            value.name,
            value.description,
            value.additional_properties,
            value.func,
            value.result_parser,
            value.to_dict(),
        ]
        input_schema = getattr(value.input_model, "model_json_schema", None)
        if callable(input_schema):
            candidates.append(input_schema())
        return any(contains_provider_secret(item, secret, seen) for item in candidates)
    if isinstance(value, Mapping):
        return any(
            contains_provider_secret(key, secret, seen)
            or contains_provider_secret(item, secret, seen)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(contains_provider_secret(item, secret, seen) for item in value)

    # Tool callables may close over values or bind a credential-bearing object.
    if callable(value):
        closure = getattr(value, "__closure__", None) or ()
        defaults = getattr(value, "__defaults__", None) or ()
        kwdefaults = getattr(value, "__kwdefaults__", None) or {}
        bound_self = getattr(value, "__self__", None)
        candidates = []
        for cell in closure:
            try:
                candidates.append(cell.cell_contents)
            except ValueError:
                # A deleted local can leave a legitimate empty closure cell.
                continue
        candidates.extend(defaults)
        candidates.extend(kwdefaults.values())
        if bound_self is not None:
            candidates.append(bound_self)
        code = getattr(value, "__code__", None)
        globals_dict = getattr(value, "__globals__", {})
        if code is not None:
            for name in code.co_names:
                if name not in globals_dict:
                    continue
                global_value = globals_dict[name]
                # Imported modules/classes are executable dependencies, not
                # values passed with a tool. Every other referenced global is
                # recursively inspected, including nested maps and sequences.
                if not isinstance(global_value, (ModuleType, type)):
                    candidates.append(global_value)
        return any(contains_provider_secret(item, secret, seen) for item in candidates)

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return contains_provider_secret(model_dump(mode="python"), secret, seen)

    # Unknown option/tool wrappers are inspected through their instance fields;
    # a cycle guard prevents recursion through framework internals.
    values = getattr(value, "__dict__", None)
    if isinstance(values, Mapping):
        return contains_provider_secret(values, secret, seen)
    return False


@runtime_checkable
class InferenceProvider(Protocol):
    async def complete(
        self, request: InferenceRequest, credential: ProviderCredential
    ) -> ProviderResponse: ...

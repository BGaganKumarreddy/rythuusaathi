"""Strict structured LLM contracts and the authenticated workflow adapter."""

from __future__ import annotations

import json
from threading import Lock
from typing import Literal, Protocol

from agent_framework import Message
from openai.lib._parsing import type_to_response_format_param
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ..credentials import (CredentialDecryptionFailed, CredentialRecoveryRequired,
                           CredentialUnavailable)
from ..inference.contracts import (
    InferenceDiagnostic,
    InferenceDiagnosticCode,
    InferenceRequest,
    InferenceStage,
    InferenceTokenUsage,
    ProviderFailure,
    ProviderFailureKind,
    ProviderResponse,
)
from ..inference.service import (
    AuthenticatedCerebrasService,
    AuthenticatedGeminiService,
    AuthenticatedGroqService,
    AuthenticatedIdentityMismatch,
    AuthenticatedInferenceService,
)
from ..security import AuthorizationDenied


class WorkflowInference(Protocol):
    """Per-request inference access; identity comes from the caller's verified session."""

    async def complete(
        self,
        request: InferenceRequest,
        *,
        expected_user_id: str,
        expected_tenant_id: str,
    ) -> ProviderResponse: ...


class AuthenticatedWorkflowInference:
    """Redacted request-scoped access to an authenticated inference service."""

    __slots__ = ("__service", "__session_token", "__credential_id",
                 "__diagnostics", "__usage_records", "__diagnostic_lock",
                 "__dispatched_count")

    def __init__(self, service: AuthenticatedInferenceService, session_token: str,
                 credential_id: str):
        if not isinstance(session_token, str) or not session_token.strip():
            raise ValueError("An authenticated session is required")
        if not isinstance(credential_id, str) or not credential_id.strip():
            raise ValueError("An owner-scoped credential ID is required")
        self.__service = service
        self.__session_token = session_token
        self.__credential_id = credential_id
        self.__diagnostics: list[InferenceDiagnostic] = []
        self.__usage_records: list[InferenceTokenUsage] = []
        self.__diagnostic_lock = Lock()
        self.__dispatched_count = 0

    def __repr__(self) -> str:
        provider = getattr(self.__service, "provider_id", "workflow")
        return f"AuthenticatedWorkflowInference({provider=}, <session and credential redacted>)"

    def __str__(self) -> str:
        provider = getattr(self.__service, "provider_id", "workflow")
        return f"<authenticated {provider} inference access>"

    @property
    def model_id(self) -> str | None:
        return self.__service.model_id

    @property
    def remaining_budget_tokens(self) -> int | None:
        """Remaining tokens in rolling budget window, when tracked by provider."""
        return getattr(self.__service, "remaining_budget_tokens", None)

    def reserve_budget(self, token_count: int) -> list[float | int] | None:
        """Reserve a block of tokens on the active authenticated service."""
        fn = getattr(self.__service, "reserve_budget", None)
        if callable(fn):
            return fn(token_count)
        return None

    def release_reservation(self, reservation: list[float | int] | None) -> None:
        """Release a previously acquired reservation on the active authenticated service."""
        fn = getattr(self.__service, "release_reservation", None)
        if callable(fn):
            fn(reservation)

    @property
    def provider_id(self) -> str:
        return getattr(self.__service, "provider_id", "unknown")

    @property
    def diagnostics(self) -> tuple[InferenceDiagnostic, ...]:
        """Sanitized per-request failure details collected across workflow fallbacks."""
        with self.__diagnostic_lock:
            return tuple(self.__diagnostics)

    @property
    def usage_records(self) -> tuple[InferenceTokenUsage, ...]:
        """Per-call token counters only; prompts and credential data are excluded."""
        with self.__diagnostic_lock:
            return tuple(self.__usage_records)

    @property
    def dispatched_count(self) -> int:
        """Total number of complete calls initiated through this inference wrapper."""
        with self.__diagnostic_lock:
            return self.__dispatched_count

    async def complete(self, request: InferenceRequest, *, expected_user_id: str,
                       expected_tenant_id: str) -> ProviderResponse:
        with self.__diagnostic_lock:
            self.__dispatched_count += 1
        failure: ProviderFailure | None = None
        try:
            result = await self.__service.complete(
                self.__session_token,
                self.__credential_id,
                request,
                expected_user_id=expected_user_id,
                expected_tenant_id=expected_tenant_id,
            )
            if result.usage is not None:
                with self.__diagnostic_lock:
                    self.__usage_records.append(result.usage)
            return result
        except ProviderFailure as exc:
            failure = ProviderFailure(exc.kind, exc.diagnostic)
        except AuthenticatedIdentityMismatch:
            failure = ProviderFailure(
                ProviderFailureKind.AUTHENTICATION,
                InferenceDiagnostic(
                    code=InferenceDiagnosticCode.IDENTITY_MISMATCH,
                    stage=InferenceStage.IDENTITY_BINDING,
                    provider=self.provider_id,
                    model_id=self.model_id,
                    agent_name=request.agent_name,
                ),
            )
        except PermissionError:
            failure = ProviderFailure(
                ProviderFailureKind.AUTHENTICATION,
                InferenceDiagnostic(
                    code=InferenceDiagnosticCode.SESSION_AUTHENTICATION_FAILED,
                    stage=InferenceStage.SESSION_AUTHENTICATION,
                    provider=self.provider_id,
                    model_id=self.model_id,
                    agent_name=request.agent_name,
                ),
            )
        except CredentialDecryptionFailed:
            failure = ProviderFailure(
                ProviderFailureKind.AUTHENTICATION,
                InferenceDiagnostic(
                    code=InferenceDiagnosticCode.CREDENTIAL_DECRYPTION_FAILED,
                    stage=InferenceStage.CREDENTIAL_DECRYPTION,
                    provider=self.provider_id,
                    model_id=self.model_id,
                    agent_name=request.agent_name,
                ),
            )
        except (AuthorizationDenied, CredentialUnavailable):
            # Owner, existence, state and provider mismatch details stay private.
            failure = ProviderFailure(
                ProviderFailureKind.AUTHENTICATION,
                InferenceDiagnostic(
                    code=InferenceDiagnosticCode.CREDENTIAL_UNAVAILABLE,
                    stage=InferenceStage.CREDENTIAL_LOOKUP,
                    provider=self.provider_id,
                    model_id=self.model_id,
                    agent_name=request.agent_name,
                ),
            )
        except CredentialRecoveryRequired:
            failure = ProviderFailure(
                ProviderFailureKind.PROVIDER_FAILURE,
                InferenceDiagnostic(
                    code=InferenceDiagnosticCode.CREDENTIAL_SERVICE_UNAVAILABLE,
                    stage=InferenceStage.CREDENTIAL_SERVICE,
                    provider=self.provider_id,
                    model_id=self.model_id,
                    agent_name=request.agent_name,
                ),
            )
        except Exception:
            code = (
                InferenceDiagnosticCode.CEREBRAS_PROVIDER_FAILURE
                if self.provider_id == "cerebras"
                else (
                    InferenceDiagnosticCode.GEMINI_PROVIDER_FAILURE
                    if self.provider_id == "gemini"
                    else InferenceDiagnosticCode.GROQ_PROVIDER_FAILURE
                )
            )
            failure = ProviderFailure(
                ProviderFailureKind.PROVIDER_FAILURE,
                InferenceDiagnostic(
                    code=code,
                    stage=InferenceStage.PROVIDER_TRANSPORT,
                    provider=self.provider_id,
                    model_id=self.model_id,
                    agent_name=request.agent_name,
                ),
            )
        if failure is not None:
            self._record_diagnostic(failure.diagnostic)
            # Detach the internal authentication/store exception from this safe failure.
            raise failure from None

    def _record_diagnostic(self, diagnostic: InferenceDiagnostic) -> None:
        with self.__diagnostic_lock:
            self.__diagnostics.append(diagnostic)


class AuthenticatedGroqInference(AuthenticatedWorkflowInference):
    """Redacted request-scoped access to the existing authenticated Groq service."""

    def __repr__(self) -> str:
        return "AuthenticatedGroqInference(<session and credential redacted>)"

    def __str__(self) -> str:
        return "<authenticated Groq inference access>"


class AuthenticatedGeminiInference(AuthenticatedWorkflowInference):
    """Redacted request-scoped access to the authenticated Gemini service."""

    def __repr__(self) -> str:
        return "AuthenticatedGeminiInference(<session and credential redacted>)"

    def __str__(self) -> str:
        return "<authenticated Gemini inference access>"


class AuthenticatedCerebrasInference(AuthenticatedWorkflowInference):
    """Redacted request-scoped access to the authenticated Cerebras service."""

    def __repr__(self) -> str:
        return "AuthenticatedCerebrasInference(<session and credential redacted>)"

    def __str__(self) -> str:
        return "<authenticated Cerebras inference access>"



IntentName = Literal[
    "crop_advice", "disease_or_pest", "weather", "market", "scheme", "general"
]


class QuotedMention(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    value: str = Field(min_length=1, max_length=60)
    quote: str = Field(min_length=1, max_length=160)


class QuotedObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    label: str = Field(min_length=1, max_length=60)
    quote: str = Field(min_length=1, max_length=160)


class IntakeInterpretation(BaseModel):
    """Candidate mentions only; canonical matching stays in deterministic registries."""

    model_config = ConfigDict(extra="forbid", strict=True)

    crops: list[QuotedMention] = Field(default_factory=list, max_length=4)
    locations: list[QuotedMention] = Field(default_factory=list, max_length=4)
    varieties: list[QuotedMention] = Field(default_factory=list, max_length=4)
    growth_stages: list[QuotedMention] = Field(default_factory=list, max_length=4)
    intents: list[IntentName] = Field(default_factory=list, max_length=4)
    observations: list[QuotedObservation] = Field(default_factory=list, max_length=6)
    farming_problem: QuotedObservation | None = None


StatementKind = Literal["evidence", "interpretation", "limitation", "question"]


class GroundedStatement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: str = Field(min_length=1, max_length=360)
    kind: StatementKind
    evidence_ids: list[str] = Field(default_factory=list, max_length=6)
    uncertainty: str | None = Field(default=None, max_length=180)

    @field_validator("text")
    @classmethod
    def trim_text(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("statement text must be trimmed")
        return value

    @model_validator(mode="after")
    def validate_support_shape(self) -> "GroundedStatement":
        if self.kind in {"evidence", "interpretation"} and not self.evidence_ids:
            raise ValueError("evidence and interpretation statements require citations")
        if self.kind in {"limitation", "question"} and self.evidence_ids:
            raise ValueError("limitations and questions must not claim source support")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("duplicate citations are not allowed")
        return self


class SpecialistInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    summary: str = Field(min_length=1, max_length=280)
    statements: list[GroundedStatement] = Field(min_length=1, max_length=3)


class CoordinatorDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    statements: list[GroundedStatement] = Field(min_length=1, max_length=5)
    follow_up_questions: list[str] = Field(default_factory=list, max_length=3)


def make_json_request(system_prompt: str, payload: dict[str, object],
                      response_model: type[BaseModel], *, agent_name: str,
                      max_output_tokens: int) -> InferenceRequest:
    if not isinstance(max_output_tokens, int) or isinstance(max_output_tokens, bool) or max_output_tokens < 1:
        raise ValueError("max_output_tokens must be a positive integer")
    response_format = type_to_response_format_param(response_model)
    user_payload = {
        "task_input": payload,
        "required_json_schema": response_model.model_json_schema(),
    }
    return InferenceRequest(
        messages=[
            Message("system", [system_prompt]),
            Message("user", [json.dumps(user_payload, ensure_ascii=False, separators=(",", ":"))]),
        ],
        options={
            "temperature": 0,
            "max_tokens": max_output_tokens,
            "response_format": response_format,
        },
        agent_name=agent_name,
    )


async def call_structured(
    inference: WorkflowInference,
    request: InferenceRequest,
    *,
    expected_user_id: str,
    expected_tenant_id: str,
    response_model: type[BaseModel],
) -> BaseModel:
    result = await inference.complete(
        request,
        expected_user_id=expected_user_id,
        expected_tenant_id=expected_tenant_id,
    )
    if result.provider not in {"groq", "gemini", "cerebras"} or not result.model_id:
        raise ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE)
    try:
        val = getattr(result.response, "value", None)
        if isinstance(val, response_model):
            return val
    except Exception:
        pass
    finish_reason = getattr(result.response, "finish_reason", None)
    malformed_diag_code = (
        InferenceDiagnosticCode.CEREBRAS_MALFORMED_RESPONSE
        if result.provider == "cerebras"
        else (
            InferenceDiagnosticCode.GEMINI_MALFORMED_RESPONSE
            if result.provider == "gemini"
            else InferenceDiagnosticCode.GROQ_MALFORMED_RESPONSE
        )
    )
    try:
        text = _assistant_text(result)
    except ProviderFailure:
        diag = InferenceDiagnostic(
            code=malformed_diag_code,
            stage=InferenceStage.RESPONSE_VALIDATION,
            provider=result.provider,
            model_id=result.model_id,
            agent_name=request.agent_name,
            response_character_count=0,
            finish_reason=finish_reason or "length",
            request_id=getattr(result.response, "response_id", None),
            parsing_outcome=(
                "length_terminated_empty"
                if finish_reason == "length"
                else "empty_assistant_message"
            ),
            validation_category=(
                "provider_truncation"
                if finish_reason == "length"
                else "schema_validation_failure"
            ),
            elapsed_seconds=result.usage.elapsed_seconds if result.usage else None,
            http_status=result.usage.http_status if result.usage else 200,
            output_token_limit=result.usage.output_token_limit if result.usage else None,
            estimated_input_tokens=result.usage.estimated_input_tokens if result.usage else None,
            budget_limit_tokens=result.usage.budget_limit_tokens if result.usage else None,
            budget_remaining_tokens=result.usage.budget_remaining_tokens if result.usage else None,
        )
        raise ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE, diag) from None
    raw_json = text.strip()
    if raw_json.startswith("```"):
        lines = raw_json.splitlines()
        if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].strip().startswith("```"):
            raw_json = "\n".join(lines[1:-1]).strip()
    try:
        data = json.loads(raw_json)
    except (ValueError, TypeError, json.JSONDecodeError):
        diag = InferenceDiagnostic(
            code=malformed_diag_code,
            stage=InferenceStage.RESPONSE_VALIDATION,
            provider=result.provider,
            model_id=result.model_id,
            agent_name=request.agent_name,
            response_character_count=len(text),
            finish_reason=getattr(result.response, "finish_reason", None),
            request_id=getattr(result.response, "response_id", None),
            parsing_outcome="json_decode_failed",
            validation_category=(
                "provider_truncation"
                if getattr(result.response, "finish_reason", None) == "length"
                else "json_decode_failure"
            ),
            elapsed_seconds=result.usage.elapsed_seconds if result.usage else None,
            http_status=result.usage.http_status if result.usage else 200,
            output_token_limit=result.usage.output_token_limit if result.usage else None,
            estimated_input_tokens=result.usage.estimated_input_tokens if result.usage else None,
            budget_limit_tokens=result.usage.budget_limit_tokens if result.usage else None,
            budget_remaining_tokens=result.usage.budget_remaining_tokens if result.usage else None,
        )
        raise ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE, diag) from None
    try:
        return response_model.model_validate(data)
    except ValidationError as exc:
        sanitized_errors = "; ".join(
            f"{'.'.join(str(loc) for loc in err.get('loc', ()))}: {err.get('msg', '')}"
            for err in exc.errors()
        )
        diag = InferenceDiagnostic(
            code=malformed_diag_code,
            stage=InferenceStage.RESPONSE_VALIDATION,
            provider=result.provider,
            model_id=result.model_id,
            agent_name=request.agent_name,
            response_character_count=len(text),
            finish_reason=getattr(result.response, "finish_reason", None),
            request_id=getattr(result.response, "response_id", None),
            parsing_outcome="pydantic_validation_failed",
            validation_category="pydantic_schema_validation_failure",
            provider_error_message=sanitized_errors[:300],
            elapsed_seconds=result.usage.elapsed_seconds if result.usage else None,
            http_status=result.usage.http_status if result.usage else 200,
            output_token_limit=result.usage.output_token_limit if result.usage else None,
            estimated_input_tokens=result.usage.estimated_input_tokens if result.usage else None,
            budget_limit_tokens=result.usage.budget_limit_tokens if result.usage else None,
            budget_remaining_tokens=result.usage.budget_remaining_tokens if result.usage else None,
        )
        raise ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE, diag) from None


def _assistant_text(result: ProviderResponse) -> str:
    messages = result.response.messages or []
    for message in reversed(messages):
        if message.role != "assistant":
            continue
        fragments: list[str] = []
        for item in message.contents:
            if isinstance(item, str):
                fragments.append(item)
            else:
                value = getattr(item, "text", None)
                if isinstance(value, str):
                    fragments.append(value)
        text = "".join(fragments).strip()
        if text:
            return text
    raise ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE)


def validate_evidence_ids(statements: list[GroundedStatement],
                          allowed_ids: set[str],
                          *,
                          provider: str | None = None,
                          model_id: str | None = None,
                          agent_name: str | None = None) -> None:
    if any(not set(item.evidence_ids) <= allowed_ids for item in statements):
        malformed_diag_code = (
            InferenceDiagnosticCode.CEREBRAS_MALFORMED_RESPONSE
            if provider == "cerebras"
            else (
                InferenceDiagnosticCode.GEMINI_MALFORMED_RESPONSE
                if provider == "gemini"
                else InferenceDiagnosticCode.GROQ_MALFORMED_RESPONSE
            )
        )
        diag = InferenceDiagnostic(
            code=malformed_diag_code,
            stage=InferenceStage.RESPONSE_VALIDATION,
            provider=provider or "workflow",
            model_id=model_id,
            agent_name=agent_name,
            parsing_outcome="evidence_citation_failed",
            validation_category="evidence_citation_validation_failure",
        )
        raise ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE, diag)

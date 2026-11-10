"""Typed contracts used by the six Phase 4 workflow agents."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Protocol, TypeAlias

from ..domain import (
    AdvisoryBoard,
    AgentFinding,
    AgriculturalRecommendation,
    AgentId,
    BoardState,
    ContextResolutionResult,
    EvidenceMode,
    EvidenceRecord,
    EvidenceSource,
    RequestContext,
)


class AgentStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AdvisoryStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    NEEDS_CLARIFICATION = "needs_clarification"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowMode(StrEnum):
    DETERMINISTIC = "deterministic"
    GROQ = "groq"
    GEMINI = "gemini"


class FarmerIntent(StrEnum):
    CROP_ADVICE = "crop_advice"
    DISEASE_OR_PEST = "disease_or_pest"
    WEATHER = "weather"
    MARKET = "market"
    SCHEME = "scheme"
    GENERAL = "general"


@dataclass(frozen=True, slots=True)
class GeneratedStatement:
    """Validated model text, distinguished from source evidence and linked to citations."""

    text: str
    kind: str
    evidence_ids: tuple[str, ...] = ()
    uncertainty: str | None = None


@dataclass(frozen=True, slots=True)
class FarmerRequest:
    """Untrusted farmer text plus identity references supplied by the caller."""

    request_id: str
    user_id: str
    tenant_id: str
    query: str
    crop_query: str | None = None
    location_query: str | None = None
    variety_query: str | None = None
    growth_stage_query: str | None = None
    as_of: date | None = None

    def __post_init__(self) -> None:
        for name in ("request_id", "user_id", "tenant_id", "query"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError(f"{name} must be non-empty and trimmed")
        for name in ("crop_query", "location_query", "variety_query", "growth_stage_query"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value or value != value.strip()):
                raise ValueError(f"{name} must be trimmed when supplied")


@dataclass(frozen=True, slots=True)
class AgentError:
    code: str
    message: str
    retryable: bool = False


@dataclass(frozen=True, slots=True)
class IntakePayload:
    context: RequestContext
    resolution: ContextResolutionResult
    intents: tuple[FarmerIntent, ...]
    observations: tuple[str, ...]
    ambiguous_fields: tuple[str, ...] = ()
    interpretation_mode: str = "deterministic"
    inference_status: str | None = None
    farming_problem: str | None = None


@dataclass(frozen=True, slots=True)
class RetrievalPayload:
    result_count: int
    evidence_ids: tuple[str, ...]
    source_titles: tuple[str, ...]
    applied_filters: tuple[tuple[str, str], ...]
    note: str
    interpretation: str | None = None
    interpretation_mode: str | None = None
    inference_status: str | None = None
    generated_statements: tuple[GeneratedStatement, ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderPayload:
    available: bool
    mode: EvidenceMode | None
    location_id: str | None
    observed_at: datetime | None
    summary: str
    interpretation: str | None = None
    interpretation_mode: str | None = None
    inference_status: str | None = None
    generated_statements: tuple[GeneratedStatement, ...] = ()


@dataclass(frozen=True, slots=True)
class CoordinatorPayload:
    status: AdvisoryStatus
    response_text: str
    conflicts: tuple[str, ...]
    missing_information: tuple[str, ...]
    interpretation_mode: str = "deterministic"
    inference_status: str | None = None
    generated_statements: tuple[GeneratedStatement, ...] = ()


AgentPayload: TypeAlias = IntakePayload | RetrievalPayload | ProviderPayload | CoordinatorPayload


@dataclass(frozen=True, slots=True)
class AgentResult:
    agent_id: AgentId
    status: AgentStatus
    started_at: datetime
    completed_at: datetime
    payload: AgentPayload | None = None
    evidence_ids: tuple[str, ...] = ()
    finding_ids: tuple[str, ...] = ()
    error: AgentError | None = None

    def __post_init__(self) -> None:
        for value in (self.started_at, self.completed_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("agent execution timestamps must be timezone-aware")
        if self.completed_at < self.started_at:
            raise ValueError("agent completion cannot precede its start")
        if (self.status is AgentStatus.FAILED) != (self.error is not None):
            raise ValueError("failed agent results require an error and other statuses must not")

    @property
    def duration_ms(self) -> float:
        return max(0.0, (self.completed_at - self.started_at).total_seconds() * 1000)


@dataclass(frozen=True, slots=True)
class AgentExecutionInput:
    request: FarmerRequest
    resolution: ContextResolutionResult
    board: AdvisoryBoard
    intake_result: AgentResult
    cancellation_event: asyncio.Event | None = None

    @property
    def context(self) -> RequestContext:
        return self.resolution.context

    def __deepcopy__(self, memo):
        """Keep the shared, lock-protected Phase 2 board as one request object.

        Microsoft Agent Framework copies messages for its workflow event stream.
        The board intentionally owns an RLock and is not serializable; immutable
        request/resolution fields can be shared while board mutations remain
        safe through the existing board's agent-scoped writers.
        """
        memo[id(self)] = self
        return self


@dataclass(frozen=True, slots=True)
class AgentExecution:
    case: AgentExecutionInput
    result: AgentResult


@dataclass(frozen=True, slots=True)
class AdvisoryResponse:
    request_id: str
    status: AdvisoryStatus
    response_text: str
    context: RequestContext
    missing_information: tuple[str, ...]
    agent_results: tuple[AgentResult, ...]
    evidence: tuple[EvidenceRecord, ...]
    findings: tuple[AgentFinding, ...]
    recommendations: tuple[AgriculturalRecommendation, ...]
    conflicts: tuple[str, ...]
    board_state: BoardState
    execution_mode: WorkflowMode = WorkflowMode.DETERMINISTIC
    provider_id: str | None = None
    model_id: str | None = None

    def __post_init__(self) -> None:
        if not self.request_id or not isinstance(self.status, AdvisoryStatus):
            raise ValueError("advisory response requires a request ID and typed status")
        if not isinstance(self.execution_mode, WorkflowMode):
            raise ValueError("advisory execution mode must be typed")
        if self.execution_mode is WorkflowMode.DETERMINISTIC and (
            self.provider_id is not None or self.model_id is not None
        ):
            raise ValueError("deterministic responses cannot report an inference provider")
        if self.execution_mode is WorkflowMode.GROQ and self.provider_id != "groq":
            raise ValueError("Groq-mode responses must identify the selected provider")
        if self.execution_mode is WorkflowMode.GEMINI and self.provider_id != "gemini":
            raise ValueError("Gemini-mode responses must identify the selected provider")
        if any(item.request_id != self.request_id for item in self.evidence):
            raise ValueError("advisory evidence must belong to the current request")
        evidence_ids = {item.evidence_id for item in self.evidence}
        if any(not set(result.evidence_ids) <= evidence_ids for result in self.agent_results):
            raise ValueError("agent result citations must exist in advisory evidence")
        if any(not set(item.evidence_ids) <= evidence_ids for item in self.findings):
            raise ValueError("finding citations must exist in advisory evidence")
        if any(not set(item.evidence_ids) <= evidence_ids for item in self.recommendations):
            raise ValueError("recommendation citations must exist in advisory evidence")
        for result in self.agent_results:
            generated = getattr(result.payload, "generated_statements", ())
            if any(not set(item.evidence_ids) <= evidence_ids for item in generated):
                raise ValueError("generated statement citations must exist in advisory evidence")


class WeatherProvider(Protocol):
    async def get_observation(self, request_id: str, location_id: str) -> "WeatherObservation | None": ...


class MarketProvider(Protocol):
    async def get_quote(self, request_id: str, crop_id: str, location_id: str) -> "MarketQuote | None": ...


@dataclass(frozen=True, slots=True)
class WeatherObservation:
    request_id: str
    location_id: str
    observed_at: datetime
    retrieved_at: datetime
    source: EvidenceSource
    mode: EvidenceMode
    temperature_c: float | None = None
    rainfall_mm: float | None = None

    def __post_init__(self) -> None:
        _validate_aware(self.observed_at, self.retrieved_at)
        if self.temperature_c is None and self.rainfall_mm is None:
            raise ValueError("weather observation must contain a measured value")
        if self.rainfall_mm is not None and self.rainfall_mm < 0:
            raise ValueError("rainfall cannot be negative")
        if any(value is not None and not math.isfinite(value)
               for value in (self.temperature_c, self.rainfall_mm)):
            raise ValueError("weather values must be finite")


@dataclass(frozen=True, slots=True)
class MarketQuote:
    request_id: str
    crop_id: str
    location_id: str
    market_name: str
    price: float
    unit: str
    observed_at: datetime
    retrieved_at: datetime
    source: EvidenceSource
    mode: EvidenceMode

    def __post_init__(self) -> None:
        _validate_aware(self.observed_at, self.retrieved_at)
        if self.price <= 0 or not math.isfinite(self.price):
            raise ValueError("market price must be finite and positive")
        if not self.market_name.strip() or not self.unit.strip():
            raise ValueError("market name and price unit are required")


def _validate_aware(*values: datetime) -> None:
    if any(value.tzinfo is None or value.utcoffset() is None for value in values):
        raise ValueError("provider timestamps must be timezone-aware")
    if values[0] > values[1]:
        raise ValueError("provider retrieval timestamp cannot precede the observation")

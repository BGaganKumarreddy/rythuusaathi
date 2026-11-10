"""Request-scoped append-only in-process collaboration board."""

from __future__ import annotations

import threading
import unicodedata
import uuid
from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .evidence import EvidenceRecord, EvidenceStatus
from .models import RequestContext
from .recommendations import AgriculturalRecommendation, RecommendationSupport


class AgentId(StrEnum):
    INTAKE = "Intake"
    WEATHER = "Weather"
    CROP = "Crop"
    MARKET = "Market"
    SCHEME = "Scheme"
    COORDINATOR = "Coordinator"


class BoardSection(StrEnum):
    CASE = "case"
    WEATHER = "weather"
    CROP_GUIDANCE = "crop_guidance"
    MARKET = "market"
    SCHEMES = "schemes"
    ADVISORY = "advisory"


AGENT_WRITE_OWNERSHIP = {
    AgentId.INTAKE: BoardSection.CASE,
    AgentId.WEATHER: BoardSection.WEATHER,
    AgentId.CROP: BoardSection.CROP_GUIDANCE,
    AgentId.MARKET: BoardSection.MARKET,
    AgentId.SCHEME: BoardSection.SCHEMES,
    AgentId.COORDINATOR: BoardSection.ADVISORY,
}


class BoardState(StrEnum):
    OPEN = "open"
    COMPLETED = "completed"
    STOPPED = "stopped"


class BoardTraceAction(StrEnum):
    EVIDENCE_APPENDED = "evidence_appended"
    FINDING_APPENDED = "finding_appended"
    RECOMMENDATION_APPENDED = "recommendation_appended"
    STATE_TRANSITIONED = "state_transitioned"


class BoardWriteDenied(PermissionError):
    """Caller cannot write this request board or section."""

    def __init__(self):
        super().__init__("Advisory board write is not authorized")


class BoardConflict(RuntimeError):
    """Board state or references prevent the requested append."""


class AgentFinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    finding_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    agent_id: AgentId
    section: BoardSection
    topic: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = ()
    recorded_at: datetime
    confidence: float | None = Field(default=None, ge=0, le=1)
    uncertainty: str | None = None
    missing_information: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_finding(self):
        if self.topic != self.topic.strip() or self.claim != self.claim.strip():
            raise ValueError("finding topic and claim must be trimmed")
        if self.recorded_at.tzinfo is None or self.recorded_at.utcoffset() is None:
            raise ValueError("finding timestamp must be timezone-aware")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("finding evidence IDs must be unique")
        return self

    @field_validator("missing_information")
    @classmethod
    def validate_missing_information(cls, values):
        if any(not value or value != value.strip() for value in values):
            raise ValueError("missing information labels must be trimmed")
        return values


class FindingConflict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str
    topic: str
    finding_ids: tuple[str, ...]
    claims: tuple[str, ...]


class BoardTraceEvent(BaseModel):
    """Safe allow-listed board activity; contains no finding or evidence text."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    request_id: str
    trace_id: str | None = None
    agent_id: AgentId
    action: BoardTraceAction
    item_id: str | None = None
    from_state: BoardState | None = None
    to_state: BoardState | None = None
    occurred_at: datetime

    @model_validator(mode="after")
    def timezone_aware_time(self):
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("trace event time must be timezone-aware")
        return self


class AdvisoryBoard:
    """In-process state for one request; all collections are append-only."""

    def __init__(self, context: RequestContext):
        self.context = context
        self._lock = threading.RLock()
        self._state = BoardState.OPEN
        self._stop_reason: str | None = None
        self._findings: list[AgentFinding] = []
        self._evidence: list[EvidenceRecord] = []
        self._recommendations: list[AgriculturalRecommendation] = []
        self._trace_events: list[BoardTraceEvent] = []
        self._ids: set[str] = set()

    @property
    def state(self) -> BoardState:
        with self._lock:
            return self._state

    @property
    def stop_reason(self) -> str | None:
        with self._lock:
            return self._stop_reason

    @property
    def findings(self) -> tuple[AgentFinding, ...]:
        with self._lock:
            return tuple(self._findings)

    @property
    def evidence(self) -> tuple[EvidenceRecord, ...]:
        with self._lock:
            return tuple(self._evidence)

    @property
    def recommendations(self) -> tuple[AgriculturalRecommendation, ...]:
        with self._lock:
            return tuple(self._recommendations)

    @property
    def trace_events(self) -> tuple[BoardTraceEvent, ...]:
        with self._lock:
            return tuple(self._trace_events)

    def writer_for(self, agent_id: AgentId) -> "AgentBoardWriter":
        if not isinstance(agent_id, AgentId):
            raise BoardWriteDenied()
        return AgentBoardWriter(self, agent_id)

    def findings_for(self, agent_id: AgentId | None = None,
                     section: BoardSection | None = None) -> tuple[AgentFinding, ...]:
        with self._lock:
            return tuple(f for f in self._findings
                         if (agent_id is None or f.agent_id is agent_id)
                         and (section is None or f.section is section))

    def evidence_by_id(self, evidence_id: str) -> EvidenceRecord | None:
        with self._lock:
            return next((e for e in self._evidence if e.evidence_id == evidence_id), None)

    def conflicts(self) -> tuple[FindingConflict, ...]:
        with self._lock:
            by_topic: dict[str, list[AgentFinding]] = {}
            for finding in self._findings:
                by_topic.setdefault(_normalize(finding.topic), []).append(finding)
            conflicts = []
            for group in by_topic.values():
                claims: dict[str, list[AgentFinding]] = {}
                for finding in group:
                    claims.setdefault(_normalize(finding.claim), []).append(finding)
                if len(claims) > 1:
                    items = [finding for values in claims.values() for finding in values]
                    conflicts.append(FindingConflict(
                        request_id=self.context.request_id,
                        topic=items[0].topic,
                        finding_ids=tuple(f.finding_id for f in items),
                        claims=tuple(dict.fromkeys(f.claim for f in items)),
                    ))
            return tuple(conflicts)

    def _publish_evidence(self, agent_id: AgentId, evidence: EvidenceRecord) -> None:
        with self._lock:
            self._check_writer(agent_id)
            if evidence.request_id != self.context.request_id:
                raise BoardConflict("evidence belongs to another request")
            self._claim_id(evidence.evidence_id)
            self._evidence.append(evidence)
            self._record_trace(agent_id, BoardTraceAction.EVIDENCE_APPENDED, evidence.evidence_id)

    def _publish_finding(self, agent_id: AgentId, *, topic: str, claim: str,
                         evidence_ids: tuple[str, ...], confidence: float | None,
                         uncertainty: str | None, missing_information: tuple[str, ...]) -> AgentFinding:
        with self._lock:
            self._check_writer(agent_id)
            self._check_evidence(evidence_ids)
            finding = AgentFinding(
                finding_id=f"finding:{uuid.uuid4().hex}", request_id=self.context.request_id,
                agent_id=agent_id, section=AGENT_WRITE_OWNERSHIP[agent_id], topic=topic,
                claim=claim, evidence_ids=evidence_ids, recorded_at=datetime.now(timezone.utc),
                confidence=confidence, uncertainty=uncertainty,
                missing_information=missing_information,
            )
            self._claim_id(finding.finding_id)
            self._findings.append(finding)
            self._record_trace(agent_id, BoardTraceAction.FINDING_APPENDED, finding.finding_id)
            return finding

    def _publish_recommendation(self, agent_id: AgentId,
                                recommendation: AgriculturalRecommendation) -> None:
        with self._lock:
            self._check_writer(agent_id)
            if agent_id is not AgentId.COORDINATOR:
                raise BoardWriteDenied()
            if recommendation.request_id != self.context.request_id:
                raise BoardConflict("recommendation belongs to another request")
            self._check_evidence(recommendation.evidence_ids)
            if recommendation.support is RecommendationSupport.EVIDENCE_BACKED:
                records = {item.evidence_id: item for item in self._evidence}
                if any(records[item].status is not EvidenceStatus.AVAILABLE
                       for item in recommendation.evidence_ids):
                    raise BoardConflict("evidence-backed recommendations require available evidence")
            self._claim_id(recommendation.recommendation_id)
            self._recommendations.append(recommendation)
            self._record_trace(agent_id, BoardTraceAction.RECOMMENDATION_APPENDED,
                               recommendation.recommendation_id)

    def _transition(self, agent_id: AgentId, state: BoardState, reason: str | None = None) -> None:
        with self._lock:
            self._check_writer(agent_id)
            if state is BoardState.COMPLETED and agent_id is not AgentId.COORDINATOR:
                raise BoardWriteDenied()
            if state is BoardState.STOPPED and agent_id not in {AgentId.INTAKE, AgentId.COORDINATOR}:
                raise BoardWriteDenied()
            if self._state is not BoardState.OPEN:
                raise BoardConflict("board is already closed")
            if state is BoardState.STOPPED and not (reason and reason.strip()):
                raise ValueError("stopping the board requires a reason")
            previous = self._state
            self._state = state
            self._stop_reason = reason.strip() if reason else None
            self._record_trace(agent_id, BoardTraceAction.STATE_TRANSITIONED,
                               from_state=previous, to_state=state)

    def _record_trace(self, agent_id: AgentId, action: BoardTraceAction,
                      item_id: str | None = None, *, from_state: BoardState | None = None,
                      to_state: BoardState | None = None) -> None:
        self._trace_events.append(BoardTraceEvent(
            event_id=f"trace:{uuid.uuid4().hex}",
            request_id=self.context.request_id,
            trace_id=self.context.workflow.trace_id,
            agent_id=agent_id,
            action=action,
            item_id=item_id,
            from_state=from_state,
            to_state=to_state,
            occurred_at=datetime.now(timezone.utc),
        ))

    def _check_writer(self, agent_id: AgentId) -> None:
        if self._state is not BoardState.OPEN:
            raise BoardConflict("board is closed")
        if agent_id not in AGENT_WRITE_OWNERSHIP:
            raise BoardWriteDenied()

    def _claim_id(self, item_id: str) -> None:
        if item_id in self._ids:
            raise BoardConflict("board item ID already exists")
        self._ids.add(item_id)

    def _check_evidence(self, evidence_ids: tuple[str, ...]) -> None:
        known = {e.evidence_id for e in self._evidence}
        if any(item not in known for item in evidence_ids):
            raise BoardConflict("item references evidence outside this request board")


class AgentBoardWriter:
    """Agent-scoped capability; attribution and writable section are board-assigned."""

    __slots__ = ("__board", "__agent_id")

    def __init__(self, board: AdvisoryBoard, agent_id: AgentId):
        self.__board = board
        self.__agent_id = agent_id

    @property
    def agent_id(self) -> AgentId:
        return self.__agent_id

    def publish_evidence(self, evidence: EvidenceRecord) -> None:
        self.__board._publish_evidence(self.__agent_id, evidence)

    def publish_finding(self, *, topic: str, claim: str,
                        evidence_ids: tuple[str, ...] = (), confidence: float | None = None,
                        uncertainty: str | None = None,
                        missing_information: tuple[str, ...] = ()) -> AgentFinding:
        return self.__board._publish_finding(
            self.__agent_id, topic=topic, claim=claim, evidence_ids=evidence_ids,
            confidence=confidence, uncertainty=uncertainty,
            missing_information=missing_information,
        )

    def publish_recommendation(self, recommendation: AgriculturalRecommendation) -> None:
        self.__board._publish_recommendation(self.__agent_id, recommendation)

    def complete(self) -> None:
        self.__board._transition(self.__agent_id, BoardState.COMPLETED)

    def stop(self, reason: str) -> None:
        self.__board._transition(self.__agent_id, BoardState.STOPPED, reason)


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())

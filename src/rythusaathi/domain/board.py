"""Request-scoped append-only in-process collaboration board."""

from __future__ import annotations

import re
import threading
import unicodedata
import uuid
from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .evidence import EvidenceRecord, EvidenceStatus
from .models import RequestContext
from .recommendations import AgriculturalRecommendation, RecommendationSupport
from .risks import RiskAlertItem, RiskEvidenceSufficiency


class AgentId(StrEnum):
    INTAKE = "Intake"
    WEATHER = "Weather"
    CROP = "Crop"
    MARKET = "Market"
    SCHEME = "Scheme"
    RISK_ALERT = "RiskAlert"
    COORDINATOR = "Coordinator"


class BoardSection(StrEnum):
    CASE = "case"
    WEATHER = "weather"
    CROP_GUIDANCE = "crop_guidance"
    MARKET = "market"
    SCHEMES = "schemes"
    RISK_ALERTS = "risk_alerts"
    ADVISORY = "advisory"


AGENT_WRITE_OWNERSHIP = {
    AgentId.INTAKE: BoardSection.CASE,
    AgentId.WEATHER: BoardSection.WEATHER,
    AgentId.CROP: BoardSection.CROP_GUIDANCE,
    AgentId.MARKET: BoardSection.MARKET,
    AgentId.SCHEME: BoardSection.SCHEMES,
    AgentId.RISK_ALERT: BoardSection.RISK_ALERTS,
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
    RISK_ALERT_APPENDED = "risk_alert_appended"
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


class ClaimRelationship(StrEnum):
    DUPLICATE = "duplicate"
    INDEPENDENT = "independent"
    COMPATIBLE = "compatible"
    CONTRADICTORY = "contradictory"


_KNOWN_SCHEME_PATTERNS = {
    "rythu_bharosa": re.compile(r"\brythu\s+bharosa\b", re.I),
    "pm_kisan": re.compile(r"\bpm[- ]?kisan\b", re.I),
    "pmfby": re.compile(r"\b(?:pmfby|fasal\s+bima)\b", re.I),
    "kcc": re.compile(r"\b(?:kisan\s+credit\s+card|kcc)\b", re.I),
    "soil_health_card": re.compile(r"\bsoil\s+health\s+card\b", re.I),
    "paramparagat_krishi": re.compile(r"\bparamparagat\s+krishi\b", re.I),
    "rashtriya_krishi_vikas": re.compile(r"\brashtriya\s+krishi\s+vikas\b", re.I),
    "national_food_security": re.compile(r"\bnational\s+food\s+security\b", re.I),
}

_SCOPE_LIMITATION_PATTERN = re.compile(
    r"\b(?:"
    r"do(?:es)?\s+not\s+contain\s+(?:weather|market|crop|disease)|"
    r"no\s+(?:weather|market|crop|disease)\s+(?:information|data|advice)\s+is\s+contained|"
    r"(?:retrieved\s+)?(?:scheme\s+)?sources?\s+do\s+not\s+contain|"
    r"does\s+not\s+(?:determine|establish)\s+(?:individual|personal)\s+eligibility|"
    r"not\s+a\s+determination\s+of\s+(?:individual|personal)\s+eligibility|"
    r"confirm\s+current\s+rules\s+and\s+application\s+steps|"
    r"eligibility\s+criteria\s+in\s+source\s+excerpts\s+do\s+not\s+establish"
    r")\b",
    re.I,
)

_WEATHER_FORECAST_PATTERN = re.compile(
    r"\b(?:"
    r"forecast|3-day|next\s+\d+\s+days?|expected|upcoming|outlook|"
    r"(?:min|max)(?:imum)?\s+(?:temperature|temp|\d+)|"
    r"dry\s+weather|no\s+rain\s+forecast"
    r")\b",
    re.I,
)

_WEATHER_OBSERVATION_PATTERN = re.compile(
    r"\b(?:"
    r"observed|current\s+(?:weather|conditions?|temperature|observation)|"
    r"temperature\s+\d+|humidity\s+\d+|rainfall\s+\d+|relative\s+humidity|"
    r"wind\s+speed|\d+\s*(?:°c|c\b|mm\b|km/h|%)"
    r")\b",
    re.I,
)

_WEATHER_CAUTION_PATTERN = re.compile(
    r"\b(?:"
    r"does\s+not\s+(?:diagnose|confirm|rule\s+out)|"
    r"cannot\s+(?:diagnose|confirm|rule\s+out)|"
    r"field\s+inspection\s+(?:is\s+)?(?:required|recommended)|"
    r"favorable\s+for\s+fungal|environmental\s+favorability"
    r")\b",
    re.I,
)

_MARKET_CAUTION_PATTERN = re.compile(
    r"\b(?:"
    r"(?:a\s+)?(?:single|one|current)[- ]days?(?:'s)?\s+(?:price\s+)?(?:observation|quote|data|record)?\s+"
    r"(?:is\s+insufficient|is\s+not\s+sufficient|cannot|does\s+not|is\s+not|fails?\s+to)|"
    r"(?:cannot|does\s+not|not\s+sufficient|insufficient\s+(?:to|for)|unable\s+to|fails?\s+to)\s+"
    r"(?:establish|prove|determine|infer|indicate|guide|support|justify|predict|conclude)\s+"
    r"(?:a\s+)?(?:multi[- ]day\s+)?(?:price\s+|market\s+|demand\s+)?(?:trend|trends|demand|recommendation|recommendations)|"
    r"(?:does\s+not|cannot|insufficient\s+to|is\s+insufficient\s+to|unable\s+to)\s+justify\s+(?:a\s+)?(?:sell\s*[/,-]?\s*hold|sell\s+or\s+hold|selling\s+or\s+holding)|"
    r"(?:sell\s*[/,-]\s*hold|selling\s+or\s+holding)\s+recommendation|"
    r"consult\s+(?:local\s+)?(?:market|apmc|officials|traders)|"
    r"no\s+trend\s+can\s+be\s+inferred|"
    r"single[- ]day\s+data\s+cannot"
    r")\b",
    re.I,
)

_MARKET_OBSERVATION_PATTERN = re.compile(
    r"\b(?:"
    r"modal\s+price|price\s+(?:of\s+)?(?:[₹rs.]*\s*)?\d+|range\s+(?:of\s+)?(?:[₹rs.]*\s*)?\d+|observed\s+at|"
    r"inr/quintal|rs/quintal|₹\s*\d+|\d+\s*inr/quintal|arrival\s+date|apmc|commodity|variety|grade|"
    r"reported\s+a\s+modal\s+price"
    r")\b",
    re.I,
)

_NO_MARKET_EVIDENCE_PATTERN = re.compile(
    r"\b(?:"
    r"no\s+(?:current\s+)?(?:market[- ]price|market|price|quote)\s+evidence\s+(?:was|is)\s+(?:provided|available)|"
    r"no\s+market[- ]price\s+(?:was|is)\s+(?:provided|found|available)|"
    r"no\s+crop\s+price\s+is\s+asserted|"
    r"no\s+price\s+(?:information|data|quote)\s+(?:was|is)\s+(?:provided|available|found)"
    r")\b",
    re.I,
)


def _normalize_market_date(date_raw: str) -> str:
    parts = re.split(r"[-/]", date_raw)
    if len(parts) == 3:
        if len(parts[0]) == 4:
            return f"{parts[2].zfill(2)}/{parts[1].zfill(2)}/{parts[0]}"
        return f"{parts[0].zfill(2)}/{parts[1].zfill(2)}/{parts[2]}"
    return date_raw


def _extract_market_price_info(text: str) -> tuple[float | None, str | None, str | None, str | None]:
    price_m = re.search(
        r"\bmodal\s+price\s+(?:is\s+|of\s+|:\s*)?(?:(?:₹|rs\.?|inr)\s*)?(\d[\d,]*(?:\.\d+)?)",
        text,
        re.I,
    )
    modal_price = float(price_m.group(1).replace(",", "")) if price_m else None

    date_m = re.search(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})\b", text)
    date_str = _normalize_market_date(date_m.group(1)) if date_m else None

    mkt_m = re.search(r"\b([a-zA-Z\s]+(?:apmc|mandi|market))\b", text, re.I)
    mkt_str = " ".join(mkt_m.group(1).casefold().split()) if mkt_m else None

    comm_m = re.search(
        r"\b(cotton(?:\s*\([^)]+\))?|paddy(?:\s*\([^)]+\))?|chilli|maize|soybean|red\s+gram|bengal\s+gram|groundnut)\b",
        text,
        re.I,
    )
    comm_str = comm_m.group(1).casefold().split("(")[0].strip() if comm_m else None

    return modal_price, date_str, mkt_str, comm_str

_UNRESOLVED_CONFLICT_PATTERN = re.compile(
    r"\b(?:"
    r"unresolved\s+(?:causes?|findings?)|"
    r"conflicting\s+causes?|"
    r"keep\s+(?:the\s+)?possible\s+causes\s+unresolved"
    r")\b",
    re.I,
)


def classify_claim_relationship(f1: AgentFinding, f2: AgentFinding) -> ClaimRelationship:
    """Distinguish duplicate, independent, compatible, and genuinely contradictory claims."""
    c1_norm = _normalize(f1.claim)
    c2_norm = _normalize(f2.claim)
    if c1_norm == c2_norm:
        return ClaimRelationship.DUPLICATE

    # Explicit conflict / unresolved causes statement (e.g. conflicting crop statements)
    if _UNRESOLVED_CONFLICT_PATTERN.search(f1.claim) or _UNRESOLVED_CONFLICT_PATTERN.search(f2.claim):
        return ClaimRelationship.CONTRADICTORY

    # Affirmation vs Negation (e.g. available vs no ... available)
    if "available" in c1_norm and "available" in c2_norm:
        if ("no " in c1_norm or "not " in c1_norm) != ("no " in c2_norm or "not " in c2_norm):
            return ClaimRelationship.CONTRADICTORY

    # Numeric property conflict on same property (e.g. pH is 6.5 vs pH is 8.0)
    m1 = re.search(r"\b(ph\s+is\s+)(\d+(?:\.\d+)?)\b", f1.claim, re.I)
    m2 = re.search(r"\b(ph\s+is\s+)(\d+(?:\.\d+)?)\b", f2.claim, re.I)
    if m1 and m2 and m1.group(1).lower() == m2.group(1).lower():
        if m1.group(2) != m2.group(2):
            return ClaimRelationship.CONTRADICTORY

    topic_lower = f"{f1.topic} {f2.topic}".lower()

    # 1. Government schemes domain:
    is_scheme = (
        f1.agent_id is AgentId.SCHEME
        or f2.agent_id is AgentId.SCHEME
        or "scheme" in topic_lower
        or any(p.search(f1.claim) or p.search(f2.claim) for p in _KNOWN_SCHEME_PATTERNS.values())
    )
    if is_scheme:
        # Scope limitations and disclaimers are compatible with any scheme statement
        if _SCOPE_LIMITATION_PATTERN.search(f1.claim) or _SCOPE_LIMITATION_PATTERN.search(f2.claim):
            return ClaimRelationship.COMPATIBLE
        schemes1 = {name for name, pat in _KNOWN_SCHEME_PATTERNS.items() if pat.search(f1.claim)}
        schemes2 = {name for name, pat in _KNOWN_SCHEME_PATTERNS.items() if pat.search(f2.claim)}
        if schemes1 and schemes2 and not (schemes1 & schemes2):
            return ClaimRelationship.INDEPENDENT
        if f1.evidence_ids and f2.evidence_ids and not (set(f1.evidence_ids) & set(f2.evidence_ids)):
            return ClaimRelationship.INDEPENDENT
        return ClaimRelationship.INDEPENDENT

    # 2. Weather domain:
    is_weather = (
        f1.agent_id is AgentId.WEATHER
        or f2.agent_id is AgentId.WEATHER
        or "weather" in topic_lower
        or "rainfall" in topic_lower
    )
    if is_weather:
        if _WEATHER_CAUTION_PATTERN.search(f1.claim) or _WEATHER_CAUTION_PATTERN.search(f2.claim):
            return ClaimRelationship.COMPATIBLE
        is_f1_forecast = bool(_WEATHER_FORECAST_PATTERN.search(f1.claim))
        is_f2_forecast = bool(_WEATHER_FORECAST_PATTERN.search(f2.claim))
        is_f1_obs = bool(_WEATHER_OBSERVATION_PATTERN.search(f1.claim))
        is_f2_obs = bool(_WEATHER_OBSERVATION_PATTERN.search(f2.claim))
        if (is_f1_forecast and is_f2_obs) or (is_f2_forecast and is_f1_obs):
            return ClaimRelationship.COMPATIBLE
        if is_f1_forecast and is_f2_forecast:
            return ClaimRelationship.COMPATIBLE
        if is_f1_obs and is_f2_obs:
            return ClaimRelationship.COMPATIBLE

    # 3. Market domain:
    is_market = (
        f1.agent_id is AgentId.MARKET
        or f2.agent_id is AgentId.MARKET
        or "market" in topic_lower
        or "price" in topic_lower
    )
    if is_market:
        is_neg1 = bool(_NO_MARKET_EVIDENCE_PATTERN.search(f1.claim))
        is_neg2 = bool(_NO_MARKET_EVIDENCE_PATTERN.search(f2.claim))
        is_f1_obs = bool(_MARKET_OBSERVATION_PATTERN.search(f1.claim))
        is_f2_obs = bool(_MARKET_OBSERVATION_PATTERN.search(f2.claim))
        if (is_neg1 and is_f2_obs) or (is_neg2 and is_f1_obs):
            return ClaimRelationship.CONTRADICTORY

        p1, d1, m1, comm1 = _extract_market_price_info(f1.claim)
        p2, d2, m2, comm2 = _extract_market_price_info(f2.claim)

        if p1 is not None and p2 is not None:
            same_mkt = (m1 is None or m2 is None or m1 in m2 or m2 in m1)
            same_comm = (comm1 is None or comm2 is None or comm1 == comm2)
            if same_mkt and same_comm:
                if d1 is not None and d2 is not None:
                    if d1 == d2 and p1 != p2:
                        return ClaimRelationship.CONTRADICTORY
                    elif d1 != d2:
                        return ClaimRelationship.COMPATIBLE
                elif p1 != p2:
                    return ClaimRelationship.CONTRADICTORY

        is_f1_warn = bool(_MARKET_CAUTION_PATTERN.search(f1.claim))
        is_f2_warn = bool(_MARKET_CAUTION_PATTERN.search(f2.claim))

        if (is_f1_warn and is_f2_obs) or (is_f2_warn and is_f1_obs):
            return ClaimRelationship.COMPATIBLE
        if is_f1_warn and is_f2_warn:
            return ClaimRelationship.COMPATIBLE
        if is_f1_obs and is_f2_obs:
            return ClaimRelationship.COMPATIBLE

    # 4. Risk alert domain:
    is_risk = (
        f1.agent_id is AgentId.RISK_ALERT
        or f2.agent_id is AgentId.RISK_ALERT
        or "risk_alert" in topic_lower
        or "risk" in topic_lower
    )
    if is_risk:
        if f1.topic != f2.topic:
            return ClaimRelationship.INDEPENDENT
        if ("no " in c1_norm or "not " in c1_norm) != ("no " in c2_norm or "not " in c2_norm):
            return ClaimRelationship.CONTRADICTORY
        return ClaimRelationship.COMPATIBLE

    return ClaimRelationship.CONTRADICTORY


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
        self._risk_alerts: list[RiskAlertItem] = []
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
    def risk_alerts(self) -> tuple[RiskAlertItem, ...]:
        with self._lock:
            return tuple(self._risk_alerts)

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
                if len(group) <= 1:
                    continue
                contradictory_items: set[AgentFinding] = set()
                for i in range(len(group)):
                    for j in range(i + 1, len(group)):
                        rel = classify_claim_relationship(group[i], group[j])
                        if rel is ClaimRelationship.CONTRADICTORY:
                            contradictory_items.add(group[i])
                            contradictory_items.add(group[j])
                if contradictory_items:
                    items = sorted(contradictory_items, key=lambda f: f.finding_id)
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

    def _publish_risk_alert(self, agent_id: AgentId, item: RiskAlertItem) -> None:
        with self._lock:
            self._check_writer(agent_id)
            if agent_id is not AgentId.RISK_ALERT:
                raise BoardWriteDenied()
            if item.request_id != self.context.request_id:
                raise BoardConflict("risk alert belongs to another request")
            self._check_evidence(item.evidence_ids)
            self._claim_id(item.finding_id)
            self._risk_alerts.append(item)
            self._record_trace(agent_id, BoardTraceAction.RISK_ALERT_APPENDED, item.finding_id)

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

    def publish_risk_alert(self, item: RiskAlertItem) -> None:
        self.__board._publish_risk_alert(self.__agent_id, item)

    def complete(self) -> None:
        self.__board._transition(self.__agent_id, BoardState.COMPLETED)

    def stop(self, reason: str) -> None:
        self.__board._transition(self.__agent_id, BoardState.STOPPED, reason)


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())

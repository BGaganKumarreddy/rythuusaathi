"""Advisory request and response schemas exposing the seven-agent workflow."""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field

from ...agents.contracts import AdvisoryStatus, WorkflowMode


class AdvisoryCreateRequest(BaseModel):
    """Farmer request submitted to the seven-agent advisory workflow."""

    model_config = ConfigDict(frozen=True)

    query: str = Field(min_length=3, max_length=2000, description="Farmer question or agronomic inquiry")
    crop: str | None = Field(default=None, description="Crop name or ID (e.g. rice, cotton, maize)")
    location: str | None = Field(default=None, description="Location name or ID (e.g. Adilabad, Punjab, Warangal)")
    growth_stage: str | None = Field(default=None, description="Growth stage (e.g. tillering, flowering, vegetative)")
    variety: str | None = Field(default=None, description="Crop variety (e.g. Pusa Basmati 1121)")
    mode: WorkflowMode = Field(default=WorkflowMode.DETERMINISTIC, description="Inference mode: deterministic, cerebras, gemini, groq")
    credential_id: str | None = Field(default=None, description="BYOK vault credential ID for external inference")


class EvidenceItemSchema(BaseModel):
    """Detailed evidence record with full source provenance; never flattened."""

    model_config = ConfigDict(frozen=True)

    evidence_id: str
    source_id: str
    title: str
    reference_url: str | None
    source_type: str
    excerpt: str | None
    provenance: str | None
    mode: str | None
    units: str | None
    crop_applicability: list[str] = Field(default_factory=list)
    geographic_applicability: list[str] = Field(default_factory=list)
    retrieved_at: datetime | None = None


class RiskAlertSchema(BaseModel):
    """Structured evidence-backed risk alert or background vulnerability."""

    model_config = ConfigDict(frozen=True)

    finding_id: str
    category: str
    title: str
    severity: str
    explanation: str
    safe_action: str
    evidence_ids: list[str]
    is_active: bool = True
    confidence: float | None = None
    crop_id: str | None = None
    location_id: str | None = None


class AgentResultSchema(BaseModel):
    """Individual agent execution outcome within the Microsoft Agent Framework graph."""

    model_config = ConfigDict(frozen=True)

    agent_id: str
    status: str
    duration_ms: float
    summary: str | None = None
    error: str | None = None
    provider_id: str | None = None
    model_id: str | None = None


class AdvisoryResponseSchema(BaseModel):
    """Complete, structured advisory response from the seven-agent system."""

    model_config = ConfigDict(frozen=True)

    advisory_id: str
    status: AdvisoryStatus
    execution_mode: WorkflowMode
    provider_id: str | None = None
    model_id: str | None = None
    coordinator_synthesis: str
    agent_results: list[AgentResultSchema]
    weather_evidence: list[EvidenceItemSchema]
    market_evidence: list[EvidenceItemSchema]
    scheme_evidence: list[EvidenceItemSchema]
    active_risk_alerts: list[RiskAlertSchema]
    background_vulnerabilities: list[RiskAlertSchema]
    all_evidence: list[EvidenceItemSchema]
    uncertainty_and_limitations: list[str]
    suggested_next_steps: list[str]
    created_at: datetime


class AdvisoryTraceResponse(BaseModel):
    """Detailed diagnostic trace of the seven-agent workflow execution."""

    model_config = ConfigDict(frozen=True)

    advisory_id: str
    status: str
    findings: list[dict]
    conflicts: list[str]
    missing_information: list[str]
    agent_executions: list[dict]
    board_state: str


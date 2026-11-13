"""Pydantic schemas for persistent agricultural conversations and follow-up messaging."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel, Field

from ...agents.contracts import WorkflowMode
from .advisory import (
    AgentResultSchema,
    EvidenceItemSchema,
    RiskAlertSchema,
)


class ContextStateSchema(BaseModel):
    """Accumulated agricultural context preserved across conversational turns."""

    crop: str | None = None
    crop_id: str | None = None
    location: str | None = None
    location_id: str | None = None
    growth_stage: str | None = None
    variety: str | None = None
    soil_info: str | None = None
    observations: list[str] = Field(default_factory=list)
    farming_problem: str | None = None
    confirmed_fields: list[str] = Field(default_factory=list)


class ConversationMessageSchema(BaseModel):
    """A single conversational message within an agricultural consultation."""

    message_id: str
    conversation_id: str
    role: str = Field(description="'user' or 'assistant'")
    content: str
    intent: str | None = None
    advisory_id: str | None = None
    agent_telemetry: list[AgentResultSchema] | None = None
    weather_evidence: list[EvidenceItemSchema] | None = None
    market_evidence: list[EvidenceItemSchema] | None = None
    scheme_evidence: list[EvidenceItemSchema] | None = None
    active_risk_alerts: list[RiskAlertSchema] | None = None
    uncertainty_and_limitations: list[str] | None = None
    suggested_next_steps: list[str] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CreateConversationRequest(BaseModel):
    """Request payload to initiate a new persistent agricultural conversation."""

    title: str | None = Field(default=None, max_length=200)
    initial_message: str | None = Field(default=None, min_length=1)
    crop: str | None = None
    location: str | None = None
    growth_stage: str | None = None
    variety: str | None = None
    mode: WorkflowMode = WorkflowMode.DETERMINISTIC


class SendMessageRequest(BaseModel):
    """Request payload to continue an existing consultation with follow-up input."""

    content: str = Field(min_length=1, description="Farmer follow-up question or observation")
    crop: str | None = None
    location: str | None = None
    growth_stage: str | None = None
    variety: str | None = None
    mode: WorkflowMode = WorkflowMode.DETERMINISTIC


class ConversationSummarySchema(BaseModel):
    """Overview metadata for listing historical farmer conversations."""

    conversation_id: str
    title: str
    status: str
    crop: str | None = None
    location: str | None = None
    last_message_preview: str | None = None
    message_count: int = 0
    created_at: datetime
    updated_at: datetime


class ConversationDetailSchema(BaseModel):
    """Full conversational consultation details with messages and accumulated context."""

    conversation_id: str
    title: str
    status: str
    context: ContextStateSchema
    messages: list[ConversationMessageSchema] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ConversationListResponse(BaseModel):
    """Paginated response for conversation listings."""

    items: list[ConversationSummarySchema]
    total: int
    limit: int
    offset: int

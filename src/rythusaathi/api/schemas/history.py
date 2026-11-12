"""Advisory history schemas for local-first persistence."""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class HistoryItemSchema(BaseModel):
    """Summary of a past advisory consultation."""

    model_config = ConfigDict(frozen=True)

    advisory_id: str
    created_at: datetime
    query: str
    crop: str | None = None
    location: str | None = None
    growth_stage: str | None = None
    execution_mode: str
    status: str
    synthesis_preview: str
    active_alert_count: int


class HistoryListResponse(BaseModel):
    """Paginated list of historical advisory consultations."""

    model_config = ConfigDict(frozen=True)

    items: list[HistoryItemSchema]
    total: int
    limit: int
    offset: int


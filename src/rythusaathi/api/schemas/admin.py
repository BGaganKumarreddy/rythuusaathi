"""Schemas for administrative monitoring, tenant user management, and agent telemetry."""

from __future__ import annotations

from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field


class AdminUserItem(BaseModel):
    """User account summary within the tenant."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str
    username: str
    role: str
    created_at: str
    disabled: bool = False


class AdminDashboardResponse(BaseModel):
    """High-level metrics for administrative overview."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    total_users: int
    total_consultations: int
    active_credentials: int
    system_status: str
    providers_available: list[str]
    as_of: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AdminAgentTelemetryItem(BaseModel):
    """Telemetry item for agent execution monitoring."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_id: str
    total_executions: int
    successful_executions: int
    partial_executions: int
    failed_executions: int
    average_duration_ms: float


class AdminSystemInfoResponse(BaseModel):
    """Runtime configuration, security architecture, and registry statistics."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    environment: str
    framework: str
    registered_crops_count: int
    registered_locations_count: int
    coverage_pairs_count: int
    password_hash: str
    encryption_algorithm: str
    storage_engine: str

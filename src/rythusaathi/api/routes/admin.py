"""Administrative routes: tenant user management, system metrics, and agent activity telemetry."""

from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, status

from ...agents.contracts import AgentId
from ...credentials import CredentialService
from ...domain.seed import CROP_REGISTRY, LOCATION_REGISTRY, SEEDED_COVERAGE_PAIRS
from ...local_auth import LocalAuthentication, _db
from ...security import VerifiedIdentity
from ..dependencies import (
    get_auth_service,
    get_credential_service,
    get_history_store,
    require_admin_role,
)
from ..history import AdvisoryHistoryStore, _history_db
from ..schemas.admin import (
    AdminAgentTelemetryItem,
    AdminDashboardResponse,
    AdminSystemInfoResponse,
    AdminUserItem,
)

router = APIRouter(prefix="/admin", tags=["Administration"])


@router.get(
    "/dashboard",
    response_model=AdminDashboardResponse,
    summary="Retrieve tenant administrative dashboard metrics",
)
async def get_admin_dashboard(
    admin: Annotated[VerifiedIdentity, Depends(require_admin_role)],
    auth_service: Annotated[LocalAuthentication, Depends(get_auth_service)],
    credential_service: Annotated[CredentialService, Depends(get_credential_service)],
    history_store: Annotated[AdvisoryHistoryStore, Depends(get_history_store)],
) -> AdminDashboardResponse:
    with _db(auth_service.database) as db:
        user_count_row = db.execute(
            "SELECT COUNT(*) as count FROM memberships WHERE tenant_id = ? AND active = 1",
            (admin.tenant_id,),
        ).fetchone()
        user_count = user_count_row["count"] if user_count_row else 0

    with _history_db(history_store.path) as db:
        consultation_count_row = db.execute(
            "SELECT COUNT(*) as count FROM advisory_history WHERE tenant_id = ?",
            (admin.tenant_id,),
        ).fetchone()
        total_consultations = consultation_count_row["count"] if consultation_count_row else 0

    creds = credential_service.list_status(admin)
    active_creds = sum(1 for c in creds if c.state.value == "active")

    return AdminDashboardResponse(
        tenant_id=admin.tenant_id,
        total_users=user_count,
        total_consultations=total_consultations,
        active_credentials=active_creds,
        system_status="operational",
        providers_available=["deterministic", "cerebras", "gemini", "groq", "open-meteo", "data.gov.in"],
    )


@router.get(
    "/users",
    response_model=list[AdminUserItem],
    summary="List all registered user accounts within the administrator's tenant",
)
async def list_tenant_users(
    admin: Annotated[VerifiedIdentity, Depends(require_admin_role)],
    auth_service: Annotated[LocalAuthentication, Depends(get_auth_service)],
) -> list[AdminUserItem]:
    users: list[AdminUserItem] = []
    with _db(auth_service.database) as db:
        rows = db.execute(
            """
            SELECT u.user_id, u.username, m.role, u.created_at, u.disabled
            FROM users u
            JOIN memberships m ON u.user_id = m.user_id
            WHERE m.tenant_id = ? AND m.active = 1
            ORDER BY u.created_at DESC
            """,
            (admin.tenant_id,),
        ).fetchall()
        for r in rows:
            users.append(
                AdminUserItem(
                    user_id=r["user_id"],
                    username=r["username"],
                    role=r["role"],
                    created_at=r["created_at"],
                    disabled=bool(r["disabled"]),
                )
            )
    return users


@router.get(
    "/agents",
    response_model=list[AdminAgentTelemetryItem],
    summary="Retrieve agent execution telemetry and performance aggregations",
)
async def get_agent_telemetry(
    admin: Annotated[VerifiedIdentity, Depends(require_admin_role)],
    history_store: Annotated[AdvisoryHistoryStore, Depends(get_history_store)],
) -> list[AdminAgentTelemetryItem]:
    with _history_db(history_store.path) as db:
        rows = db.execute(
            "SELECT agent_results_json FROM advisory_history WHERE tenant_id = ? ORDER BY created_at DESC LIMIT 200",
            (admin.tenant_id,),
        ).fetchall()

    telemetry: dict[str, dict[str, float]] = {
        agent_id.value: {
            "total": 0,
            "success": 0,
            "partial": 0,
            "failed": 0,
            "duration_sum": 0.0,
        }
        for agent_id in AgentId
    }

    import json
    for r in rows:
        raw_results = json.loads(r["agent_results_json"]) if r["agent_results_json"] else []
        for res in raw_results:
            aid = res.get("agent_id")
            if aid in telemetry:
                dur = float(res.get("duration_ms") or 0.0)
                st = res.get("status")
                telemetry[aid]["total"] += 1
                telemetry[aid]["duration_sum"] += dur
                if st == "completed":
                    telemetry[aid]["success"] += 1
                elif st in {"partial", "skipped", "unavailable"}:
                    telemetry[aid]["partial"] += 1
                else:
                    telemetry[aid]["failed"] += 1

    result: list[AdminAgentTelemetryItem] = []
    for agent_id, stats in telemetry.items():
        total = int(stats["total"])
        avg_dur = round(stats["duration_sum"] / total, 1) if total > 0 else 0.0
        result.append(
            AdminAgentTelemetryItem(
                agent_id=agent_id,
                total_executions=total,
                successful_executions=int(stats["success"]),
                partial_executions=int(stats["partial"]),
                failed_executions=int(stats["failed"]),
                average_duration_ms=avg_dur,
            )
        )
    return result


@router.get(
    "/system",
    response_model=AdminSystemInfoResponse,
    summary="Inspect system runtime specifications and registry sizing",
)
async def get_system_specifications(
    admin: Annotated[VerifiedIdentity, Depends(require_admin_role)],
) -> AdminSystemInfoResponse:
    return AdminSystemInfoResponse(
        version="0.1.0-phase8.1",
        environment="local-first production",
        framework="Microsoft Agent Framework (Core 1.24.0)",
        registered_crops_count=len(CROP_REGISTRY.records),
        registered_locations_count=len(LOCATION_REGISTRY.records),
        coverage_pairs_count=len(SEEDED_COVERAGE_PAIRS),
        password_hash="Argon2id (time_cost=3, memory_cost=65536, parallelism=4)",
        encryption_algorithm="AES-256-GCM Envelope Encryption",
        storage_engine="SQLite WAL Mode",
    )

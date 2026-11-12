"""Owner-scoped SQLite persistence for advisory consultations and audit traces."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from ..security import VerifiedIdentity
from .schemas.advisory import (
    AdvisoryResponseSchema,
    AdvisoryTraceResponse,
    AgentResultSchema,
    EvidenceItemSchema,
    RiskAlertSchema,
)
from .schemas.history import HistoryItemSchema


@contextmanager
def _history_db(path: str | Path):
    conn = sqlite3.connect(str(path), timeout=10, isolation_level="IMMEDIATE")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize_history_schema(path: str | Path) -> None:
    """Initialize the SQLite advisory history schema with WAL mode and owner indexing."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with _history_db(path) as db:
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS advisory_history (
            advisory_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            query TEXT NOT NULL,
            crop TEXT,
            location TEXT,
            growth_stage TEXT,
            variety TEXT,
            execution_mode TEXT NOT NULL,
            provider_id TEXT,
            model_id TEXT,
            status TEXT NOT NULL,
            coordinator_synthesis TEXT NOT NULL,
            agent_results_json TEXT NOT NULL,
            weather_evidence_json TEXT NOT NULL,
            market_evidence_json TEXT NOT NULL,
            scheme_evidence_json TEXT NOT NULL,
            active_risk_alerts_json TEXT NOT NULL,
            background_vulnerabilities_json TEXT NOT NULL,
            all_evidence_json TEXT NOT NULL,
            uncertainty_and_limitations_json TEXT NOT NULL,
            suggested_next_steps_json TEXT NOT NULL,
            trace_json TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_history_owner
            ON advisory_history(tenant_id, user_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS ix_history_crop
            ON advisory_history(tenant_id, user_id, crop);
        """)


class AdvisoryHistoryStore:
    """Owner-scoped, persistent SQLite repository for past advisory consultations."""

    def __init__(self, database_path: str | Path):
        self.path = Path(database_path)
        initialize_history_schema(self.path)

    def save(
        self,
        *,
        advisory: AdvisoryResponseSchema,
        user_id: str,
        tenant_id: str,
        query: str,
        crop: str | None = None,
        location: str | None = None,
        growth_stage: str | None = None,
        variety: str | None = None,
        trace: AdvisoryTraceResponse | None = None,
    ) -> None:
        """Persist an executed advisory consultation scoped to the calling user and tenant."""
        with _history_db(self.path) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO advisory_history (
                    advisory_id, user_id, tenant_id, query, crop, location,
                    growth_stage, variety, execution_mode, provider_id, model_id,
                    status, coordinator_synthesis, agent_results_json,
                    weather_evidence_json, market_evidence_json, scheme_evidence_json,
                    active_risk_alerts_json, background_vulnerabilities_json,
                    all_evidence_json, uncertainty_and_limitations_json,
                    suggested_next_steps_json, trace_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    advisory.advisory_id,
                    user_id,
                    tenant_id,
                    query,
                    crop,
                    location,
                    growth_stage,
                    variety,
                    advisory.execution_mode.value if hasattr(advisory.execution_mode, "value") else str(advisory.execution_mode),
                    advisory.provider_id,
                    advisory.model_id,
                    advisory.status.value if hasattr(advisory.status, "value") else str(advisory.status),
                    advisory.coordinator_synthesis,
                    json.dumps([r.model_dump(mode="json") for r in advisory.agent_results]),
                    json.dumps([e.model_dump(mode="json") for e in advisory.weather_evidence]),
                    json.dumps([e.model_dump(mode="json") for e in advisory.market_evidence]),
                    json.dumps([e.model_dump(mode="json") for e in advisory.scheme_evidence]),
                    json.dumps([a.model_dump(mode="json") for a in advisory.active_risk_alerts]),
                    json.dumps([a.model_dump(mode="json") for a in advisory.background_vulnerabilities]),
                    json.dumps([e.model_dump(mode="json") for e in advisory.all_evidence]),
                    json.dumps(advisory.uncertainty_and_limitations),
                    json.dumps(advisory.suggested_next_steps),
                    json.dumps(trace.model_dump(mode="json")) if trace else None,
                    advisory.created_at.isoformat(),
                ),
            )

    def get(self, advisory_id: str, identity: VerifiedIdentity) -> AdvisoryResponseSchema | None:
        """Retrieve a specific advisory consultation if owned by the current identity."""
        with _history_db(self.path) as db:
            row = db.execute(
                """
                SELECT * FROM advisory_history
                WHERE advisory_id = ? AND tenant_id = ? AND user_id = ?
                """,
                (advisory_id, identity.tenant_id, identity.subject),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_advisory(row)

    def get_trace(self, advisory_id: str, identity: VerifiedIdentity) -> AdvisoryTraceResponse | None:
        """Retrieve diagnostic trace details for an owned advisory."""
        with _history_db(self.path) as db:
            row = db.execute(
                """
                SELECT trace_json, status, advisory_id FROM advisory_history
                WHERE advisory_id = ? AND tenant_id = ? AND user_id = ?
                """,
                (advisory_id, identity.tenant_id, identity.subject),
            ).fetchone()
            if row is None:
                return None
            trace_raw = row["trace_json"]
            if not trace_raw:
                return None
            data = json.loads(trace_raw)
            return AdvisoryTraceResponse(**data)

    def list(
        self,
        identity: VerifiedIdentity,
        limit: int = 20,
        offset: int = 0,
        crop: str | None = None,
    ) -> tuple[list[HistoryItemSchema], int]:
        """List historical advisory summaries with tenant and user isolation."""
        with _history_db(self.path) as db:
            if crop:
                count_query = (
                    "SELECT COUNT(*) FROM advisory_history WHERE tenant_id = ? AND user_id = ? AND crop = ?"
                )
                params_count = (identity.tenant_id, identity.subject, crop)
                list_query = """
                    SELECT advisory_id, created_at, query, crop, location, growth_stage,
                           execution_mode, status, coordinator_synthesis, active_risk_alerts_json
                    FROM advisory_history
                    WHERE tenant_id = ? AND user_id = ? AND crop = ?
                    ORDER BY created_at DESC
                    LIMIT ? OFFSET ?
                """
                params_list = (identity.tenant_id, identity.subject, crop, limit, offset)
            else:
                count_query = (
                    "SELECT COUNT(*) FROM advisory_history WHERE tenant_id = ? AND user_id = ?"
                )
                params_count = (identity.tenant_id, identity.subject)
                list_query = """
                    SELECT advisory_id, created_at, query, crop, location, growth_stage,
                           execution_mode, status, coordinator_synthesis, active_risk_alerts_json
                    FROM advisory_history
                    WHERE tenant_id = ? AND user_id = ?
                    ORDER BY created_at DESC
                    LIMIT ? OFFSET ?
                """
                params_list = (identity.tenant_id, identity.subject, limit, offset)

            total = db.execute(count_query, params_count).fetchone()[0]
            rows = db.execute(list_query, params_list).fetchall()

            items: list[HistoryItemSchema] = []
            for r in rows:
                synthesis = r["coordinator_synthesis"] or ""
                preview = (synthesis[:147] + "...") if len(synthesis) > 150 else synthesis
                active_alerts = json.loads(r["active_risk_alerts_json"]) if r["active_risk_alerts_json"] else []
                items.append(
                    HistoryItemSchema(
                        advisory_id=r["advisory_id"],
                        created_at=datetime.fromisoformat(r["created_at"]),
                        query=r["query"],
                        crop=r["crop"],
                        location=r["location"],
                        growth_stage=r["growth_stage"],
                        execution_mode=r["execution_mode"],
                        status=r["status"],
                        synthesis_preview=preview,
                        active_alert_count=len(active_alerts),
                    )
                )
            return items, total

    @staticmethod
    def _row_to_advisory(row: sqlite3.Row) -> AdvisoryResponseSchema:
        agent_results = [AgentResultSchema(**r) for r in json.loads(row["agent_results_json"])]
        weather_evidence = [EvidenceItemSchema(**e) for e in json.loads(row["weather_evidence_json"])]
        market_evidence = [EvidenceItemSchema(**e) for e in json.loads(row["market_evidence_json"])]
        scheme_evidence = [EvidenceItemSchema(**e) for e in json.loads(row["scheme_evidence_json"])]
        active_risks = [RiskAlertSchema(**a) for a in json.loads(row["active_risk_alerts_json"])]
        bg_risks = [RiskAlertSchema(**a) for a in json.loads(row["background_vulnerabilities_json"])]
        all_ev = [EvidenceItemSchema(**e) for e in json.loads(row["all_evidence_json"])]
        limitations = json.loads(row["uncertainty_and_limitations_json"])
        next_steps = json.loads(row["suggested_next_steps_json"])

        return AdvisoryResponseSchema(
            advisory_id=row["advisory_id"],
            status=row["status"],
            execution_mode=row["execution_mode"],
            provider_id=row["provider_id"],
            model_id=row["model_id"],
            coordinator_synthesis=row["coordinator_synthesis"],
            agent_results=agent_results,
            weather_evidence=weather_evidence,
            market_evidence=market_evidence,
            scheme_evidence=scheme_evidence,
            active_risk_alerts=active_risks,
            background_vulnerabilities=bg_risks,
            all_evidence=all_ev,
            uncertainty_and_limitations=limitations,
            suggested_next_steps=next_steps,
            created_at=datetime.fromisoformat(row["created_at"]),
        )


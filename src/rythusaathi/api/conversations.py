"""Owner-scoped persistent conversations and follow-up intelligence engine for RythuSaathi."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from ..agents.contracts import AgentStatus, WorkflowMode
from ..domain import (
    AgentId,
    EvidenceMode,
    EvidenceStatus,
    FactState,
)
from ..domain.seed import CROP_REGISTRY, LOCATION_REGISTRY
from ..security import VerifiedIdentity
from .history import _history_db
from .schemas.advisory import (
    AgentResultSchema,
    EvidenceItemSchema,
    RiskAlertSchema,
)
from .schemas.conversations import (
    ContextStateSchema,
    ConversationDetailSchema,
    ConversationListResponse,
    ConversationMessageSchema,
    ConversationSummarySchema,
    CreateConversationRequest,
    SendMessageRequest,
)

logger = logging.getLogger(__name__)

# Intent regex patterns
_CORRECTION_PATTERNS = [
    re.compile(r"\bnot\s+([a-zA-Z\s]+?)[,;\.]?\s*(?:but|it['’]?s|i['’]?m growing|actually)?\s+([a-zA-Z\s]+)", re.I),
    re.compile(r"\bactually[,]?\s+(?:i am growing|it is|it's|in)?\s*([a-zA-Z\s]+)", re.I),
    re.compile(r"\bchange\s+(?:crop|location|variety|stage)\s+to\s+([a-zA-Z\s]+)", re.I),
    re.compile(r"\b(?:switch|correct)\s+(?:to\s+)?([a-zA-Z\s]+)", re.I),
    re.compile(r"\bno[,]?\s+(?:it is|i am growing|in)?\s*([a-zA-Z\s]+)", re.I),
]

_CORRECTION_INDICATORS = re.compile(
    r"\b(?:actually|not\b|instead of|change|switch|correction|correct to|no[,]?\s+)\b", re.I
)

_RESET_PATTERNS = [
    re.compile(r"\b(?:start|begin)\s+(?:a\s+)?new\s+(?:consultation|advisory|chat|session)\b", re.I),
    re.compile(r"\b(?:reset|clear)\s+(?:context|consultation|chat)\b", re.I),
]

_FOLLOWUP_OBSERVATION_PATTERNS = [
    re.compile(r"\b(?:spreading|spreading fast|increasing|getting worse|more spots|turning brown|drying up|curling|wilting)\b", re.I),
    re.compile(r"\b(?:yellowing|yellow leaves|chlorosis)\b", re.I),
    re.compile(r"\b(?:brown leaf spots|brown spots|leaf spots|blast lesions)\b", re.I),
    re.compile(r"\b(?:pest|insects|caterpillar|borer|holes)\b", re.I),
]


def initialize_conversation_schema(path: str | Path) -> None:
    """Initialize persistent SQLite tables for multi-turn conversations and message threads."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with _history_db(path) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS conversations (
            conversation_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            crop TEXT,
            crop_id TEXT,
            location TEXT,
            location_id TEXT,
            growth_stage TEXT,
            variety TEXT,
            soil_info TEXT,
            observations_json TEXT NOT NULL DEFAULT '[]',
            farming_problem TEXT,
            confirmed_fields_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_conversations_owner
            ON conversations(tenant_id, user_id, updated_at DESC);

        CREATE TABLE IF NOT EXISTS conversation_messages (
            message_id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            intent TEXT,
            advisory_id TEXT,
            agent_telemetry_json TEXT,
            weather_evidence_json TEXT,
            market_evidence_json TEXT,
            scheme_evidence_json TEXT,
            active_risk_alerts_json TEXT,
            uncertainty_and_limitations_json TEXT,
            suggested_next_steps_json TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS ix_conversation_messages_thread
            ON conversation_messages(conversation_id, created_at ASC);
        """)


class ConversationStore:
    """Owner-scoped SQLite repository for multi-turn conversational consultations."""

    def __init__(self, database_path: str | Path):
        self.path = Path(database_path)
        initialize_conversation_schema(self.path)

    def create_conversation(
        self,
        *,
        identity: VerifiedIdentity,
        title: str | None = None,
        context: ContextStateSchema | None = None,
    ) -> ConversationDetailSchema:
        """Create a new conversational consultation scoped to the authenticated farmer."""
        conv_id = f"conv:{uuid.uuid4().hex}"
        now = datetime.now(timezone.utc)
        ctx = context or ContextStateSchema()
        resolved_title = title or "New Agricultural Consultation"

        with _history_db(self.path) as db:
            db.execute(
                """
                INSERT INTO conversations (
                    conversation_id, user_id, tenant_id, title, status,
                    crop, crop_id, location, location_id, growth_stage,
                    variety, soil_info, observations_json, farming_problem,
                    confirmed_fields_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conv_id,
                    identity.subject,
                    identity.tenant_id,
                    resolved_title,
                    ctx.crop,
                    ctx.crop_id,
                    ctx.location,
                    ctx.location_id,
                    ctx.growth_stage,
                    ctx.variety,
                    ctx.soil_info,
                    json.dumps(ctx.observations),
                    ctx.farming_problem,
                    json.dumps(ctx.confirmed_fields),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )

        return ConversationDetailSchema(
            conversation_id=conv_id,
            title=resolved_title,
            status="ACTIVE",
            context=ctx,
            messages=[],
            created_at=now,
            updated_at=now,
        )

    def get_conversation(
        self,
        conversation_id: str,
        identity: VerifiedIdentity,
    ) -> ConversationDetailSchema | None:
        """Retrieve conversation details and message history ensuring strict owner and tenant isolation."""
        with _history_db(self.path) as db:
            row = db.execute(
                """
                SELECT * FROM conversations
                WHERE conversation_id = ? AND tenant_id = ? AND user_id = ?
                """,
                (conversation_id, identity.tenant_id, identity.subject),
            ).fetchone()

            if row is None:
                return None

            msg_rows = db.execute(
                """
                SELECT * FROM conversation_messages
                WHERE conversation_id = ? AND tenant_id = ? AND user_id = ?
                ORDER BY created_at ASC
                """,
                (conversation_id, identity.tenant_id, identity.subject),
            ).fetchall()

            messages = [self._row_to_message(m) for m in msg_rows]
            context = ContextStateSchema(
                crop=row["crop"],
                crop_id=row["crop_id"],
                location=row["location"],
                location_id=row["location_id"],
                growth_stage=row["growth_stage"],
                variety=row["variety"],
                soil_info=row["soil_info"],
                observations=json.loads(row["observations_json"]) if row["observations_json"] else [],
                farming_problem=row["farming_problem"],
                confirmed_fields=json.loads(row["confirmed_fields_json"]) if row["confirmed_fields_json"] else [],
            )

            return ConversationDetailSchema(
                conversation_id=row["conversation_id"],
                title=row["title"],
                status=row["status"],
                context=context,
                messages=messages,
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )

    def list_conversations(
        self,
        identity: VerifiedIdentity,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[ConversationSummarySchema], int]:
        """List historical conversations for the authenticated farmer with pagination."""
        with _history_db(self.path) as db:
            count = db.execute(
                "SELECT COUNT(*) FROM conversations WHERE tenant_id = ? AND user_id = ?",
                (identity.tenant_id, identity.subject),
            ).fetchone()[0]

            rows = db.execute(
                """
                SELECT c.*,
                       (SELECT content FROM conversation_messages
                        WHERE conversation_id = c.conversation_id
                        ORDER BY created_at DESC LIMIT 1) as last_msg,
                       (SELECT COUNT(*) FROM conversation_messages
                        WHERE conversation_id = c.conversation_id) as msg_count
                FROM conversations c
                WHERE c.tenant_id = ? AND c.user_id = ?
                ORDER BY c.updated_at DESC
                LIMIT ? OFFSET ?
                """,
                (identity.tenant_id, identity.subject, limit, offset),
            ).fetchall()

            summaries = []
            for r in rows:
                last_msg = r["last_msg"] or ""
                preview = (last_msg[:97] + "...") if len(last_msg) > 100 else last_msg
                summaries.append(
                    ConversationSummarySchema(
                        conversation_id=r["conversation_id"],
                        title=r["title"],
                        status=r["status"],
                        crop=r["crop"],
                        location=r["location"],
                        last_message_preview=preview or None,
                        message_count=r["msg_count"],
                        created_at=datetime.fromisoformat(r["created_at"]),
                        updated_at=datetime.fromisoformat(r["updated_at"]),
                    )
                )

            return summaries, count

    def add_message(
        self,
        *,
        conversation_id: str,
        identity: VerifiedIdentity,
        role: str,
        content: str,
        intent: str | None = None,
        advisory_id: str | None = None,
        agent_telemetry: list[AgentResultSchema] | None = None,
        weather_evidence: list[EvidenceItemSchema] | None = None,
        market_evidence: list[EvidenceItemSchema] | None = None,
        scheme_evidence: list[EvidenceItemSchema] | None = None,
        active_risk_alerts: list[RiskAlertSchema] | None = None,
        uncertainty_and_limitations: list[str] | None = None,
        suggested_next_steps: list[str] | None = None,
    ) -> ConversationMessageSchema:
        """Append a message to an owned conversation and update timestamps."""
        msg_id = f"msg:{uuid.uuid4().hex}"
        now = datetime.now(timezone.utc)

        with _history_db(self.path) as db:
            db.execute(
                """
                INSERT INTO conversation_messages (
                    message_id, conversation_id, tenant_id, user_id,
                    role, content, intent, advisory_id,
                    agent_telemetry_json, weather_evidence_json,
                    market_evidence_json, scheme_evidence_json,
                    active_risk_alerts_json, uncertainty_and_limitations_json,
                    suggested_next_steps_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    msg_id,
                    conversation_id,
                    identity.tenant_id,
                    identity.subject,
                    role,
                    content,
                    intent,
                    advisory_id,
                    json.dumps([r.model_dump(mode="json") for r in agent_telemetry]) if agent_telemetry else None,
                    json.dumps([e.model_dump(mode="json") for e in weather_evidence]) if weather_evidence else None,
                    json.dumps([e.model_dump(mode="json") for e in market_evidence]) if market_evidence else None,
                    json.dumps([e.model_dump(mode="json") for e in scheme_evidence]) if scheme_evidence else None,
                    json.dumps([a.model_dump(mode="json") for a in active_risk_alerts]) if active_risk_alerts else None,
                    json.dumps(uncertainty_and_limitations) if uncertainty_and_limitations else None,
                    json.dumps(suggested_next_steps) if suggested_next_steps else None,
                    now.isoformat(),
                ),
            )

            db.execute(
                "UPDATE conversations SET updated_at = ? WHERE conversation_id = ? AND tenant_id = ? AND user_id = ?",
                (now.isoformat(), conversation_id, identity.tenant_id, identity.subject),
            )

        return ConversationMessageSchema(
            message_id=msg_id,
            conversation_id=conversation_id,
            role=role,
            content=content,
            intent=intent,
            advisory_id=advisory_id,
            agent_telemetry=agent_telemetry,
            weather_evidence=weather_evidence,
            market_evidence=market_evidence,
            scheme_evidence=scheme_evidence,
            active_risk_alerts=active_risk_alerts,
            uncertainty_and_limitations=uncertainty_and_limitations,
            suggested_next_steps=suggested_next_steps,
            created_at=now,
        )

    def update_context(
        self,
        conversation_id: str,
        identity: VerifiedIdentity,
        context: ContextStateSchema,
        title: str | None = None,
    ) -> None:
        """Update the accumulated agricultural context for an owned conversation."""
        now = datetime.now(timezone.utc)
        with _history_db(self.path) as db:
            if title:
                db.execute(
                    """
                    UPDATE conversations SET
                        title = ?,
                        crop = ?,
                        crop_id = ?,
                        location = ?,
                        location_id = ?,
                        growth_stage = ?,
                        variety = ?,
                        soil_info = ?,
                        observations_json = ?,
                        farming_problem = ?,
                        confirmed_fields_json = ?,
                        updated_at = ?
                    WHERE conversation_id = ? AND tenant_id = ? AND user_id = ?
                    """,
                    (
                        title,
                        context.crop,
                        context.crop_id,
                        context.location,
                        context.location_id,
                        context.growth_stage,
                        context.variety,
                        context.soil_info,
                        json.dumps(context.observations),
                        context.farming_problem,
                        json.dumps(context.confirmed_fields),
                        now.isoformat(),
                        conversation_id,
                        identity.tenant_id,
                        identity.subject,
                    ),
                )
            else:
                db.execute(
                    """
                    UPDATE conversations SET
                        crop = ?,
                        crop_id = ?,
                        location = ?,
                        location_id = ?,
                        growth_stage = ?,
                        variety = ?,
                        soil_info = ?,
                        observations_json = ?,
                        farming_problem = ?,
                        confirmed_fields_json = ?,
                        updated_at = ?
                    WHERE conversation_id = ? AND tenant_id = ? AND user_id = ?
                    """,
                    (
                        context.crop,
                        context.crop_id,
                        context.location,
                        context.location_id,
                        context.growth_stage,
                        context.variety,
                        context.soil_info,
                        json.dumps(context.observations),
                        context.farming_problem,
                        json.dumps(context.confirmed_fields),
                        now.isoformat(),
                        conversation_id,
                        identity.tenant_id,
                        identity.subject,
                    ),
                )

    def delete_conversation(self, conversation_id: str, identity: VerifiedIdentity) -> bool:
        """Delete an owned conversation and its cascade messages."""
        with _history_db(self.path) as db:
            cur = db.execute(
                "DELETE FROM conversations WHERE conversation_id = ? AND tenant_id = ? AND user_id = ?",
                (conversation_id, identity.tenant_id, identity.subject),
            )
            return cur.rowcount > 0

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> ConversationMessageSchema:
        telemetry = [AgentResultSchema(**r) for r in json.loads(row["agent_telemetry_json"])] if row["agent_telemetry_json"] else None
        weather = [EvidenceItemSchema(**e) for e in json.loads(row["weather_evidence_json"])] if row["weather_evidence_json"] else None
        market = [EvidenceItemSchema(**e) for e in json.loads(row["market_evidence_json"])] if row["market_evidence_json"] else None
        scheme = [EvidenceItemSchema(**e) for e in json.loads(row["scheme_evidence_json"])] if row["scheme_evidence_json"] else None
        active_risks = [RiskAlertSchema(**a) for a in json.loads(row["active_risk_alerts_json"])] if row["active_risk_alerts_json"] else None
        limitations = json.loads(row["uncertainty_and_limitations_json"]) if row["uncertainty_and_limitations_json"] else None
        next_steps = json.loads(row["suggested_next_steps_json"]) if row["suggested_next_steps_json"] else None

        return ConversationMessageSchema(
            message_id=row["message_id"],
            conversation_id=row["conversation_id"],
            role=row["role"],
            content=row["content"],
            intent=row["intent"],
            advisory_id=row["advisory_id"],
            agent_telemetry=telemetry,
            weather_evidence=weather,
            market_evidence=market,
            scheme_evidence=scheme,
            active_risk_alerts=active_risks,
            uncertainty_and_limitations=limitations,
            suggested_next_steps=next_steps,
            created_at=datetime.fromisoformat(row["created_at"]),
        )


def resolve_followup_context(
    existing: ContextStateSchema,
    message: str,
    explicit_overrides: dict[str, str | None] | None = None,
) -> tuple[ContextStateSchema, str]:
    """Apply follow-up intelligence and explicit precedence rules.

    Precedence order:
    1. Latest explicit user correction (e.g. "not rice, cotton" or explicit form override)
    2. Previously confirmed user information (retained across turns)
    3. Extracted from current turn via domain registry matching
    4. Unknown values
    """
    overrides = explicit_overrides or {}
    new_ctx = existing.model_copy(deep=True)
    confirmed_set = set(new_ctx.confirmed_fields)

    # 1. Detect Intent
    intent = "follow_up"
    for pat in _RESET_PATTERNS:
        if pat.search(message):
            return ContextStateSchema(), "new_consultation"

    is_correction = bool(_CORRECTION_INDICATORS.search(message))
    if is_correction:
        intent = "correction"

    # 2. Check for explicit overrides from user payload (highest priority)
    if overrides.get("crop"):
        res = CROP_REGISTRY.resolve(overrides["crop"])
        if res.canonical_id:
            rec = CROP_REGISTRY.get(res.canonical_id)
            canon = rec.common_names[0].value if rec and rec.common_names else (rec.scientific_name if rec else overrides["crop"])
            new_ctx.crop = canon.title()
            new_ctx.crop_id = res.canonical_id
            confirmed_set.add("crop")
        else:
            new_ctx.crop = overrides["crop"].strip().title()
            confirmed_set.add("crop")

    if overrides.get("location"):
        loc_res = LOCATION_REGISTRY.resolve(overrides["location"])
        if loc_res.canonical_id:
            loc_rec = LOCATION_REGISTRY.get(loc_res.canonical_id)
            new_ctx.location = loc_rec.display_name if loc_rec else overrides["location"]
            new_ctx.location_id = loc_res.canonical_id
            confirmed_set.add("location")
        else:
            new_ctx.location = overrides["location"].strip().title()
            confirmed_set.add("location")

    if overrides.get("growth_stage"):
        new_ctx.growth_stage = overrides["growth_stage"].strip().lower()
        confirmed_set.add("growth_stage")

    if overrides.get("variety"):
        new_ctx.variety = overrides["variety"].strip().title()
        confirmed_set.add("variety")

    # 3. If correction intent is detected, check for explicit crop/location replacement
    if is_correction:
        # Find any crops explicitly negated (e.g., "not rice", "instead of rice")
        negated_crops = set()
        for rec in CROP_REGISTRY.records:
            names = [rec.scientific_name] + [v.value for v in rec.common_names] + [v.value for v in rec.regional_names]
            for n in names:
                if re.search(rf"\b(?:not|instead of|neither)\s+{re.escape(n)}\b", message, re.I):
                    negated_crops.add(rec.crop_id)

        # Find any crops positively mentioned (not negated)
        for rec in CROP_REGISTRY.records:
            if rec.crop_id in negated_crops:
                continue
            names = [rec.scientific_name] + [v.value for v in rec.common_names] + [v.value for v in rec.regional_names]
            for n in names:
                if re.search(rf"\b{re.escape(n)}\b", message, re.I):
                    canon = rec.common_names[0].value if rec.common_names else rec.scientific_name
                    new_ctx.crop = canon.title()
                    new_ctx.crop_id = rec.crop_id
                    confirmed_set.add("crop")
                    break

        # Find any locations explicitly negated (e.g., "not Ludhiana")
        negated_locs = set()
        for rec in LOCATION_REGISTRY.records:
            if re.search(rf"\b(?:not|instead of|neither)\s+{re.escape(rec.name)}\b", message, re.I):
                negated_locs.add(rec.location_id)

        # Find any locations positively mentioned
        for rec in LOCATION_REGISTRY.records:
            if rec.location_id in negated_locs:
                continue
            if re.search(rf"\b{re.escape(rec.name)}\b", message, re.I):
                new_ctx.location = rec.name
                new_ctx.location_id = rec.location_id
                confirmed_set.add("location")
                break

    # 4. Extract entities from message if not already confirmed or not present
    if "crop" not in confirmed_set or not new_ctx.crop:
        for rec in CROP_REGISTRY.records:
            names = [rec.scientific_name] + [v.value for v in rec.common_names] + [v.value for v in rec.regional_names]
            for n in names:
                if re.search(rf"\b{re.escape(n)}\b", message, re.I):
                    if not re.search(rf"\b(?:not|instead of)\s+{re.escape(n)}\b", message, re.I):
                        canon = rec.common_names[0].value if rec.common_names else rec.scientific_name
                        new_ctx.crop = canon.title()
                        new_ctx.crop_id = rec.crop_id
                        confirmed_set.add("crop")
                        break

    if "location" not in confirmed_set or not new_ctx.location:
        for rec in LOCATION_REGISTRY.records:
            if re.search(rf"\b{re.escape(rec.name)}\b", message, re.I):
                if not re.search(rf"\b(?:not|instead of)\s+{re.escape(rec.name)}\b", message, re.I):
                    new_ctx.location = rec.name
                    new_ctx.location_id = rec.location_id
                    confirmed_set.add("location")
                    break

    if "growth_stage" not in confirmed_set or not new_ctx.growth_stage:
        stages = [
            "nursery", "tillering", "panicle initiation", "flowering", "grain filling",
            "seedling", "vegetative", "squaring", "boll formation", "maturity",
            "crown root initiation", "jointing", "heading", "ripening",
        ]
        for st in stages:
            if re.search(rf"\b{re.escape(st)}\b", message, re.I):
                new_ctx.growth_stage = st
                confirmed_set.add("growth_stage")
                break

    # 5. Accumulate observations
    obs_set = set(new_ctx.observations)
    for pat in _FOLLOWUP_OBSERVATION_PATTERNS:
        match = pat.search(message)
        if match:
            symptom = match.group(0).strip().lower()
            if symptom not in obs_set:
                obs_set.add(symptom)
                new_ctx.observations.append(symptom)

    new_ctx.confirmed_fields = sorted(list(confirmed_set))
    return new_ctx, intent

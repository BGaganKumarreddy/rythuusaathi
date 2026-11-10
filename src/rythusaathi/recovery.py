"""Offline, fail-closed SQLite restore and credential quarantine."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .credentials import initialize_credential_schema
from .local_auth import initialize_identity_schema


class RecoveryFailed(RuntimeError):
    """Restore could not complete safely; the prior live database remains in place."""

    def __init__(self):
        super().__init__("Database recovery failed")


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    quarantined_credentials: int
    preserved_tombstones: int
    invalidated_sessions: int
    reenrollment_required: int


def restore_from_backup(backup_database: str | Path,
                        live_database: str | Path) -> RecoveryReport:
    """Stage a SQLite backup, quarantine its credentials, then atomically install it.

    Stop the application before calling. All non-deleted credential envelopes
    are erased and marked for explicit validated re-enrollment. Revoked/deleted
    rows from the live database override stale backup state. No restored
    credential can dispatch until all pending reenrollment is completed and
    `CredentialService.complete_recovery()` clears the global recovery gate.
    """
    backup_path = Path(backup_database).resolve()
    live_path = Path(live_database).resolve()
    if backup_path == live_path or not backup_path.is_file() or not live_path.is_file():
        raise RecoveryFailed()

    temp_fd, temp_name = tempfile.mkstemp(
        prefix=f".{live_path.name}.recovery-", suffix=".sqlite", dir=live_path.parent
    )
    os.close(temp_fd)
    staged_path = Path(temp_name)
    try:
        current_tombstones, current_audit = _read_current_tombstones(live_path)
        with closing(sqlite3.connect(backup_path.as_uri() + "?mode=ro", uri=True)) as source, \
                closing(sqlite3.connect(staged_path)) as staged:
            source.backup(staged)
            staged.commit()

        with closing(sqlite3.connect(staged_path)) as staged:
            staged_tables = {r[0] for r in staged.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
        if not {"users", "tenants", "memberships", "sessions", "credentials", "credential_audit"} <= staged_tables:
            raise RecoveryFailed()

        initialize_identity_schema(staged_path)
        initialize_credential_schema(staged_path)
        with closing(sqlite3.connect(staged_path, timeout=10)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            tables = {r["name"] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"users", "tenants", "memberships", "sessions", "credentials", "credential_audit"} <= tables:
                raise RecoveryFailed()
            source_tombstones = [dict(r) for r in db.execute(
                "SELECT * FROM credentials WHERE state IN ('revoked','deleted')"
            )]
            session_count = db.execute("SELECT count(*) FROM sessions").fetchone()[0]
            credential_count = db.execute("SELECT count(*) FROM credentials").fetchone()[0]
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM sessions")
            db.execute("""UPDATE credentials SET
                state=CASE WHEN state='deleted' THEN 'deleted' ELSE 'revoked' END,
                generation=CASE WHEN state='deleted' THEN generation ELSE generation+1 END,
                nonce=x'',ciphertext=x'',wrapped_dek=x'',dek_nonce=x'',
                needs_reenrollment=CASE WHEN state='deleted' THEN 0 ELSE 1 END,
                updated_at=?""", (_now(),))
            tombstones = {r["credential_id"]: r for r in source_tombstones}
            for row in current_tombstones:
                previous = tombstones.get(row["credential_id"])
                chosen_state = (
                    "deleted" if row["state"] == "deleted" or
                    (previous and previous["state"] == "deleted") else "revoked"
                )
                tombstones[row["credential_id"]] = {
                    **(previous or {}), **row,
                    "state": chosen_state,
                    "generation": max(row["generation"], (previous or {}).get("generation", 0)),
                }
            for row in tombstones.values():
                exists = db.execute("SELECT 1 FROM credentials WHERE credential_id=?",
                                    (row["credential_id"],)).fetchone()
                values = (row["tenant_id"], row["issuer"], row["subject"], row["provider_id"],
                          row["state"], row["generation"], b"", b"", b"", b"",
                          row.get("key_version") or "local-os-v1", _now(),
                          int(row["state"] != "deleted"), row["credential_id"])
                if exists:
                    db.execute("""UPDATE credentials SET tenant_id=?,issuer=?,subject=?,provider_id=?,state=?,
                        generation=?,nonce=?,ciphertext=?,wrapped_dek=?,dek_nonce=?,key_version=?,updated_at=?,
                        needs_reenrollment=? WHERE credential_id=?""", values)
                else:
                    db.execute("""INSERT INTO credentials(tenant_id,issuer,subject,provider_id,state,generation,
                        nonce,ciphertext,wrapped_dek,dek_nonce,key_version,updated_at,needs_reenrollment,credential_id)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", values)
            for event in current_audit:
                db.execute("INSERT OR IGNORE INTO credential_audit(event_id,event_json) VALUES (?,?)",
                           (event["event_id"], event["event_json"]))
            db.execute("UPDATE recovery_state SET required=1,started_at=? WHERE singleton=1", (_now(),))
            pending = db.execute("SELECT count(*) FROM credentials WHERE needs_reenrollment=1").fetchone()[0]
            db.commit()
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RecoveryFailed()
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            db.execute("PRAGMA journal_mode=DELETE")

        _checkpoint_live(live_path)
        os.replace(staged_path, live_path)
        return RecoveryReport(credential_count, len(tombstones), session_count, pending)
    except RecoveryFailed:
        raise
    except Exception:
        raise RecoveryFailed() from None
    finally:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(staged_path) + suffix)
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass


def _read_current_tombstones(database: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    with closing(sqlite3.connect(database, timeout=10)) as db:
        db.row_factory = sqlite3.Row
        tables = {r["name"] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "credentials" not in tables or "credential_audit" not in tables:
            raise RecoveryFailed()
        rows = [dict(r) for r in db.execute(
            "SELECT * FROM credentials WHERE state IN ('revoked','deleted')"
        )]
        events = []
        for row in db.execute("SELECT event_id,event_json FROM credential_audit"):
            try:
                event = json.loads(row["event_json"])
            except (TypeError, ValueError):
                continue
            if event.get("action") in {"revoke", "delete"}:
                events.append({"event_id": row["event_id"], "event_json": row["event_json"]})
        return rows, events


def _checkpoint_live(database: Path) -> None:
    with closing(sqlite3.connect(database, timeout=10)) as db:
        result = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if result and result[0] != 0:
            raise RecoveryFailed()
    for suffix in ("-wal", "-shm"):
        Path(str(database) + suffix).unlink(missing_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

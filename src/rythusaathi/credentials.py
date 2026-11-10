"""Owner-scoped SQLite BYOK lifecycle with AES-256-GCM envelope encryption."""

from __future__ import annotations

import base64
import ctypes
import json
import secrets
import sqlite3
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Protocol, TypeVar, cast

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .local_auth import _db
from .security import (AuditOutcome, AuthorizationDenied, CredentialAction,
                       CredentialAuditEvent, CredentialAuthorizationPolicy,
                       CredentialOwner, CredentialState, CredentialStatus,
                       VerifiedIdentity)

_SCHEMA_VERSION = "1"
_KEY_VERSION = "local-os-v1"
_T = TypeVar("_T")


class KeyEncryptionInterface(Protocol):
    """Provider-neutral KEK boundary; keys never enter credential storage."""

    def get_or_create_key(self, key_version: str) -> bytes: ...

    def get_key(self, key_version: str) -> bytes: ...


class OsCredentialStoreUnavailable(RuntimeError):
    """OS-backed KEK storage is unavailable; operations must fail closed."""


class WindowsCredentialStore:
    """KEK storage backed only by Windows Credential Manager via keyring."""

    def __init__(self):
        if sys.platform != "win32":
            raise OsCredentialStoreUnavailable("OS credential store unavailable")
        try:
            from keyring.backends.Windows import WinVaultKeyring
            self._backend = WinVaultKeyring()
            if not isinstance(self._backend, WinVaultKeyring):
                raise RuntimeError
        except Exception as exc:
            raise OsCredentialStoreUnavailable("OS credential store unavailable") from exc
        self._service = "RythuSaathi local credential KEK"

    def get_or_create_key(self, key_version: str) -> bytes:
        try:
            with _windows_mutex("Local\\RythuSaathi-Kek-" + key_version):
                stored = self._backend.get_password(self._service, key_version)
                if stored is None:
                    key = secrets.token_bytes(32)
                    self._backend.set_password(self._service, key_version,
                                               base64.b64encode(key).decode("ascii"))
                    # Read back so a write failure cannot be mistaken for success.
                    stored = self._backend.get_password(self._service, key_version)
            key = base64.b64decode(stored, validate=True)
            if len(key) != 32:
                raise ValueError
            return key
        except Exception as exc:
            raise OsCredentialStoreUnavailable("OS credential store unavailable") from exc

    def get_key(self, key_version: str) -> bytes:
        try:
            stored = self._backend.get_password(self._service, key_version)
            key = base64.b64decode(stored, validate=True) if stored else b""
            if len(key) != 32:
                raise ValueError
            return key
        except Exception as exc:
            raise OsCredentialStoreUnavailable("OS credential store unavailable") from exc

    def delete_key(self, key_version: str) -> None:
        """Delete a named KEK entry, used for adapter lifecycle verification/retirement."""
        try:
            with _windows_mutex("Local\\RythuSaathi-Kek-" + key_version):
                stored = self._backend.get_password(self._service, key_version)
                if stored is not None:
                    self._backend.delete_password(self._service, key_version)
                if self._backend.get_password(self._service, key_version) is not None:
                    raise ValueError
        except Exception as exc:
            raise OsCredentialStoreUnavailable("OS credential store unavailable") from exc


class CredentialUnavailable(RuntimeError):
    """Credential is not available for the requested operation."""

    def __init__(self):
        super().__init__("Credential unavailable")


class CredentialDecryptionFailed(CredentialUnavailable):
    """Credential envelope or OS-backed decryption key could not be opened."""


class CredentialRecoveryRequired(RuntimeError):
    """Credential service is quarantined pending explicit post-restore recovery."""

    def __init__(self):
        super().__init__("Credential recovery is required")


class CredentialValidationFailed(RuntimeError):
    """A re-entered credential did not pass its protected validation callback."""

    def __init__(self):
        super().__init__("Credential validation failed")


def initialize_credential_schema(database: str | Path) -> None:
    with _db(database) as db:
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS credentials (
            credential_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
            issuer TEXT NOT NULL, subject TEXT NOT NULL, provider_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('active','revoked','deleted','unavailable')),
            generation INTEGER NOT NULL, nonce BLOB NOT NULL, ciphertext BLOB NOT NULL,
            wrapped_dek BLOB NOT NULL, dek_nonce BLOB NOT NULL,
            key_version TEXT NOT NULL, updated_at TEXT NOT NULL,
            needs_reenrollment INTEGER NOT NULL DEFAULT 0,
            UNIQUE(tenant_id,issuer,subject,provider_id,credential_id)
        );
        CREATE INDEX IF NOT EXISTS credential_owner ON credentials(tenant_id,issuer,subject,provider_id,state);
        CREATE TABLE IF NOT EXISTS credential_audit (
            event_id TEXT PRIMARY KEY, event_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS recovery_state (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), required INTEGER NOT NULL,
            started_at TEXT
        );
        INSERT OR IGNORE INTO recovery_state(singleton,required,started_at) VALUES (1,0,NULL);
        """)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(credentials)")}
        if "needs_reenrollment" not in columns:
            db.execute("ALTER TABLE credentials ADD COLUMN needs_reenrollment INTEGER NOT NULL DEFAULT 0")


class CredentialService:
    """Only this service decrypts keys. `dispatch_handoff` must return after transport handoff."""

    def __init__(self, database: str | Path, key_encryption: KeyEncryptionInterface,
                 policy: CredentialAuthorizationPolicy | None = None):
        self.database = Path(database)
        initialize_credential_schema(self.database)
        self.key_encryption = key_encryption
        self.policy = policy or CredentialAuthorizationPolicy()
        # SQLite's IMMEDIATE transaction serializes dispatch handoff against revoke/rotate,
        # including other local processes. Never call this with a full-response callback.
        self._dispatch_lock = threading.RLock()

    def create(self, identity: VerifiedIdentity, provider_id: str, secret: str) -> CredentialStatus:
        owner = self.policy.owner_for_creation(identity)
        if not provider_id or provider_id != provider_id.strip() or not isinstance(secret, str) or not secret:
            raise ValueError("Provider and credential are required")
        credential_id = secrets.token_urlsafe(18)
        with _db(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_normal_state(db)
            status = self._insert_generation(db, credential_id, owner, provider_id, secret, 1)
            self._audit(db, identity, credential_id, CredentialAction.CREATE, AuditOutcome.SUCCESS)
            return status

    def rotate(self, identity: VerifiedIdentity, credential_id: str, secret: str) -> CredentialStatus:
        with self._dispatch_lock, _db(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_normal_state(db)
            row = self._owned_row(db, identity, credential_id, CredentialAction.ROTATE)
            if row["state"] == CredentialState.DELETED.value:
                raise CredentialUnavailable()
            status = self._insert_generation(db, credential_id, _owner(row), row["provider_id"],
                                             secret, row["generation"] + 1, replace=True)
            self._audit(db, identity, credential_id, CredentialAction.ROTATE, AuditOutcome.SUCCESS)
            return status

    def status(self, identity: VerifiedIdentity, credential_id: str) -> CredentialStatus:
        with _db(self.database) as db:
            row = self._owned_row(db, identity, credential_id, CredentialAction.STATUS)
            return _status(row)

    def list_status(self, identity: VerifiedIdentity, provider_id: str | None = None) -> list[CredentialStatus]:
        owner = self.policy.owner_for_creation(identity)
        with _db(self.database) as db:
            if provider_id is None:
                rows = db.execute("""SELECT * FROM credentials WHERE tenant_id=? AND issuer=? AND subject=?
                    AND state!='deleted' ORDER BY updated_at""",
                                  (owner.tenant_id, owner.issuer, owner.subject)).fetchall()
            else:
                rows = db.execute("""SELECT * FROM credentials WHERE tenant_id=? AND issuer=? AND subject=?
                    AND provider_id=? AND state!='deleted' ORDER BY updated_at""",
                                  (owner.tenant_id, owner.issuer, owner.subject, provider_id)).fetchall()
            return [_status(row) for row in rows]

    def revoke(self, identity: VerifiedIdentity, credential_id: str) -> CredentialStatus:
        with self._dispatch_lock, _db(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._owned_row(db, identity, credential_id, CredentialAction.REVOKE)
            if row["state"] == CredentialState.DELETED.value:
                raise CredentialUnavailable()
            now = _now()
            db.execute("UPDATE credentials SET state='revoked',generation=generation+1,updated_at=? "
                       "WHERE credential_id=? AND generation=?", (now, credential_id, row["generation"]))
            self._audit(db, identity, credential_id, CredentialAction.REVOKE, AuditOutcome.SUCCESS)
            return CredentialStatus(credential_id, row["provider_id"], CredentialState.REVOKED,
                                    row["generation"] + 1, datetime.fromisoformat(now))

    def delete(self, identity: VerifiedIdentity, credential_id: str) -> None:
        with self._dispatch_lock, _db(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._owned_row(db, identity, credential_id, CredentialAction.DELETE)
            if row["state"] != CredentialState.DELETED.value:
                now = _now()
                # Tombstone and erase envelope bytes; backups must separately enforce tombstones/retention.
                db.execute("UPDATE credentials SET state='deleted',generation=generation+1,nonce=x'',ciphertext=x'',"
                           "wrapped_dek=x'',dek_nonce=x'',needs_reenrollment=0,updated_at=? "
                           "WHERE credential_id=? AND generation=?",
                           (now, credential_id, row["generation"]))
            self._audit(db, identity, credential_id, CredentialAction.DELETE, AuditOutcome.SUCCESS)

    def dispatch_handoff(self, identity: VerifiedIdentity, credential_id: str,
                         handoff: Callable[[str, int], _T], *,
                         expected_provider_id: str | None = None) -> _T:
        """Authorize, decrypt, and call a bounded provider transport handoff under the DB lock.

        `handoff` may return a request/future handle immediately; it must not await the
        provider response. Its exception must not include the supplied secret.
        """
        failed = False
        result: _T | None = None
        with self._dispatch_lock, _db(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_normal_state(db)
            row = self._owned_row(db, identity, credential_id, CredentialAction.USE)
            if expected_provider_id is not None and row["provider_id"] != expected_provider_id:
                raise CredentialUnavailable()
            if row["state"] != CredentialState.ACTIVE.value:
                raise CredentialUnavailable()
            secret = self._decrypt(row)
            try:
                result = handoff(secret, row["generation"])
            except Exception:
                self._audit(db, identity, credential_id, CredentialAction.USE, AuditOutcome.FAILED)
                failed = True
            finally:
                secret = ""
        if failed:
            raise CredentialUnavailable() from None
        return cast(_T, result)

    async def reenroll(self, identity: VerifiedIdentity, credential_id: str, secret: str,
                       validator: Callable[[str], Awaitable[bool]]) -> CredentialStatus:
        """Validate a user-entered key, then encrypt it with the current OS KEK.

        `validator` is a protected backend callback (normally the selected
        provider's authentication check). It must not log or retain `secret`.
        The generation is rechecked after validation to prevent stale activation.
        """
        if not isinstance(secret, str) or not secret or not callable(validator):
            raise CredentialValidationFailed()
        with _db(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._owned_row(db, identity, credential_id, CredentialAction.ROTATE)
            if not self._recovery_required(db) or not row["needs_reenrollment"]:
                raise CredentialUnavailable()
            if row["state"] == CredentialState.DELETED.value:
                raise CredentialUnavailable()
            expected_generation = row["generation"]
            owner = _owner(row)
            provider_id = row["provider_id"]
        try:
            valid = await validator(secret)
        except Exception:
            raise CredentialValidationFailed() from None
        if valid is not True:
            raise CredentialValidationFailed()
        with self._dispatch_lock, _db(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_recovery_state(db)
            row = self._owned_row(db, identity, credential_id, CredentialAction.ROTATE)
            if (not row["needs_reenrollment"] or row["generation"] != expected_generation
                    or row["state"] == CredentialState.DELETED.value):
                raise CredentialUnavailable()
            status = self._insert_generation(db, credential_id, owner, provider_id,
                                             secret, expected_generation + 1, replace=True)
            self._audit(db, identity, credential_id, CredentialAction.ROTATE, AuditOutcome.SUCCESS)
            return status

    def recovery_required(self) -> bool:
        with _db(self.database) as db:
            return self._recovery_required(db)

    def complete_recovery(self) -> None:
        with _db(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_recovery_state(db)
            pending = db.execute("SELECT count(*) FROM credentials WHERE needs_reenrollment=1").fetchone()[0]
            if pending:
                raise CredentialRecoveryRequired()
            db.execute("UPDATE recovery_state SET required=0,started_at=NULL WHERE singleton=1")

    def audit_events(self, identity: VerifiedIdentity) -> list[dict[str, str]]:
        owner = self.policy.owner_for_creation(identity)
        with _db(self.database) as db:
            rows = db.execute("""SELECT event_json FROM credential_audit
                WHERE json_extract(event_json,'$.actor_tenant_id')=?
                  AND json_extract(event_json,'$.actor_issuer')=?
                  AND json_extract(event_json,'$.actor_subject')=? ORDER BY rowid""",
                              (owner.tenant_id, owner.issuer, owner.subject)).fetchall()
            return [json.loads(r["event_json"]) for r in rows]

    def _owned_row(self, db: sqlite3.Connection, identity: VerifiedIdentity,
                   credential_id: str, action: CredentialAction) -> sqlite3.Row:
        if not isinstance(identity, VerifiedIdentity) or not identity.is_active:
            raise AuthorizationDenied()
        row = db.execute("SELECT * FROM credentials WHERE credential_id=? AND tenant_id=?",
                         (credential_id, identity.tenant_id)).fetchone()
        if row is None:
            raise CredentialUnavailable()
        owner = _owner(row)
        try:
            self.policy.require(identity, action, owner)
        except AuthorizationDenied:
            # Do not disclose whether another user's credential ID exists.
            raise CredentialUnavailable() from None
        return row

    @staticmethod
    def _recovery_required(db: sqlite3.Connection) -> bool:
        row = db.execute("SELECT required FROM recovery_state WHERE singleton=1").fetchone()
        return bool(row and row["required"])

    @classmethod
    def _require_normal_state(cls, db: sqlite3.Connection) -> None:
        if cls._recovery_required(db):
            raise CredentialRecoveryRequired()

    @classmethod
    def _require_recovery_state(cls, db: sqlite3.Connection) -> None:
        if not cls._recovery_required(db):
            raise CredentialUnavailable()

    def _insert_generation(self, db: sqlite3.Connection, credential_id: str, owner: CredentialOwner,
                           provider_id: str, secret: str, generation: int, replace: bool = False) -> CredentialStatus:
        if not isinstance(secret, str) or not secret:
            raise ValueError("Credential is required")
        nonce, ciphertext, dek_nonce, wrapped_dek = self._encrypt(owner, credential_id, provider_id,
                                                                  generation, secret)
        now = _now()
        values = (owner.tenant_id, owner.issuer, owner.subject, provider_id, "active", generation,
                  nonce, ciphertext, wrapped_dek, dek_nonce, _KEY_VERSION, now, credential_id)
        if replace:
            db.execute("""UPDATE credentials SET tenant_id=?,issuer=?,subject=?,provider_id=?,state=?,generation=?,
                nonce=?,ciphertext=?,wrapped_dek=?,dek_nonce=?,key_version=?,updated_at=?,needs_reenrollment=0
                WHERE credential_id=?""", values)
        else:
            db.execute("""INSERT INTO credentials(tenant_id,issuer,subject,provider_id,state,generation,
                nonce,ciphertext,wrapped_dek,dek_nonce,key_version,updated_at,credential_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", values)
        return CredentialStatus(credential_id, provider_id, CredentialState.ACTIVE, generation,
                                datetime.fromisoformat(now))

    def _encrypt(self, owner: CredentialOwner, credential_id: str, provider_id: str,
                 generation: int, secret: str) -> tuple[bytes, bytes, bytes, bytes]:
        dek = secrets.token_bytes(32)
        nonce, dek_nonce = secrets.token_bytes(12), secrets.token_bytes(12)
        aad = _aad(owner, credential_id, provider_id, generation)
        plaintext = secret.encode("utf-8")
        ciphertext = AESGCM(dek).encrypt(nonce, plaintext, aad)
        try:
            kek = self.key_encryption.get_or_create_key(_KEY_VERSION)
            if len(kek) != 32:
                raise OsCredentialStoreUnavailable("OS credential store unavailable")
            wrapped = AESGCM(kek).encrypt(dek_nonce, dek, aad)
            return nonce, ciphertext, dek_nonce, wrapped
        finally:
            # Python/crypto library copies cannot be guaranteed zeroized.
            dek = b""
            plaintext = b""

    def _decrypt(self, row: sqlite3.Row) -> str:
        try:
            owner = _owner(row)
            aad = _aad(owner, row["credential_id"], row["provider_id"], row["generation"])
            kek = self.key_encryption.get_key(row["key_version"])
            if len(kek) != 32:
                raise ValueError
            dek = AESGCM(kek).decrypt(row["dek_nonce"], row["wrapped_dek"], aad)
            return AESGCM(dek).decrypt(row["nonce"], row["ciphertext"], aad).decode("utf-8")
        except (OsCredentialStoreUnavailable, Exception):
            raise CredentialDecryptionFailed() from None

    @staticmethod
    def _audit(db: sqlite3.Connection, identity: VerifiedIdentity, credential_id: str,
               action: CredentialAction, outcome: AuditOutcome) -> None:
        event = CredentialAuditEvent(secrets.token_urlsafe(16), identity.tenant_id, identity.issuer,
                                     identity.subject, credential_id, action, outcome,
                                     datetime.now(timezone.utc))
        db.execute("INSERT INTO credential_audit(event_id,event_json) VALUES (?,?)",
                   (event.event_id, json.dumps(event.to_dict(), separators=(",", ":"))))


def _aad(owner: CredentialOwner, credential_id: str, provider_id: str, generation: int) -> bytes:
    return json.dumps([_SCHEMA_VERSION, owner.tenant_id, owner.issuer, owner.subject,
                       credential_id, provider_id, generation], separators=(",", ":")).encode()


def _owner(row: sqlite3.Row) -> CredentialOwner:
    return CredentialOwner(row["tenant_id"], row["issuer"], row["subject"])


def _status(row: sqlite3.Row) -> CredentialStatus:
    state = (CredentialState.UNAVAILABLE if row["needs_reenrollment"]
             else CredentialState(row["state"]))
    return CredentialStatus(row["credential_id"], row["provider_id"], state,
                            row["generation"], datetime.fromisoformat(row["updated_at"]))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _windows_mutex(name: str):
    """Serialize KEK bootstrap across local processes to prevent key replacement races."""
    try:
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel32.CreateMutexW(None, False, name)
        if not handle:
            raise OSError
        wait_result = kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
        if wait_result not in (0, 0x80):  # WAIT_OBJECT_0 / WAIT_ABANDONED
            kernel32.CloseHandle(handle)
            raise OSError
        try:
            yield
        finally:
            kernel32.ReleaseMutex(handle)
            kernel32.CloseHandle(handle)
    except Exception as exc:
        raise OsCredentialStoreUnavailable("OS credential store unavailable") from exc

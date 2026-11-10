"""Local password authentication and opaque, server-side sessions."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError, VerificationError

from .security import IdentityRole, VerifiedIdentity

LOCAL_ISSUER = "rythusaathi:local"
_INVALID_CREDENTIALS = "Authentication failed"


@contextmanager
def _db(path: str | Path):
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


def initialize_identity_schema(path: str | Path) -> None:
    with _db(path) as db:
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL, created_at TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS tenants (
            tenant_id TEXT PRIMARY KEY, name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS memberships (
            user_id TEXT NOT NULL REFERENCES users(user_id),
            tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
            role TEXT NOT NULL CHECK(role IN ('member','tenant_admin')),
            active INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY(user_id, tenant_id)
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(user_id),
            tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
            expires_at TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
        """)


class LocalAuthentication:
    """Username/password adapter. Only a SHA-256 digest of each session token is stored."""

    def __init__(self, database: str | Path, session_minutes: int = 60 * 12):
        if session_minutes < 1:
            raise ValueError("session_minutes must be positive")
        self.database = Path(database)
        initialize_identity_schema(self.database)
        self.session_minutes = session_minutes
        self._hasher = PasswordHasher()

    def create_tenant(self, tenant_id: str, name: str) -> None:
        if not tenant_id.strip() or not name.strip():
            raise ValueError("tenant ID and name are required")
        with _db(self.database) as db:
            db.execute("INSERT INTO tenants(tenant_id,name) VALUES (?,?)", (tenant_id, name))

    def register(self, username: str, password: str, tenant_id: str,
                 role: IdentityRole = IdentityRole.MEMBER) -> str:
        if not username or username != username.strip() or len(password) < 12:
            raise ValueError("Username must be trimmed and password must contain at least 12 characters")
        if not isinstance(role, IdentityRole):
            raise ValueError("Invalid role")
        user_id = secrets.token_urlsafe(24)
        with _db(self.database) as db:
            db.execute("INSERT INTO users(user_id,username,password_hash,created_at) VALUES (?,?,?,?)",
                       (user_id, username, self._hasher.hash(password), _now()))
            db.execute("INSERT INTO memberships(user_id,tenant_id,role) VALUES (?,?,?)",
                       (user_id, tenant_id, role.value))
        return user_id

    def add_membership(self, user_id: str, tenant_id: str, role: IdentityRole) -> None:
        if not isinstance(role, IdentityRole):
            raise ValueError("Invalid role")
        with _db(self.database) as db:
            db.execute("INSERT INTO memberships(user_id,tenant_id,role) VALUES (?,?,?)",
                       (user_id, tenant_id, role.value))

    def set_membership_active(self, user_id: str, tenant_id: str, active: bool) -> None:
        if not isinstance(active, bool):
            raise ValueError("active must be a boolean")
        with _db(self.database) as db:
            cursor = db.execute("UPDATE memberships SET active=? WHERE user_id=? AND tenant_id=?",
                                (int(active), user_id, tenant_id))
            if cursor.rowcount != 1:
                raise LookupError("Membership not found")

    def disable_user(self, user_id: str) -> None:
        with _db(self.database) as db:
            cursor = db.execute("UPDATE users SET disabled=1 WHERE user_id=?", (user_id,))
            if cursor.rowcount != 1:
                raise LookupError("User not found")

    def login(self, username: str, password: str, tenant_id: str) -> str:
        with _db(self.database) as db:
            row = db.execute("""SELECT u.* FROM users u JOIN memberships m USING(user_id)
                WHERE u.username=? AND m.tenant_id=? AND m.active=1 AND u.disabled=0""",
                             (username, tenant_id)).fetchone()
            try:
                valid = row is not None and self._hasher.verify(row["password_hash"], password)
            except (VerifyMismatchError, VerificationError, InvalidHashError):
                valid = False
            if not valid:
                # Verify against a dummy Argon2 hash to reduce account-enumeration timing differences.
                try:
                    self._hasher.verify(self._dummy_hash, password)
                except (VerifyMismatchError, VerificationError, InvalidHashError):
                    pass
                raise PermissionError(_INVALID_CREDENTIALS)
            if self._hasher.check_needs_rehash(row["password_hash"]):
                db.execute("UPDATE users SET password_hash=? WHERE user_id=?",
                           (self._hasher.hash(password), row["user_id"]))
            token = secrets.token_urlsafe(32)
            expiry = datetime.now(timezone.utc) + timedelta(minutes=self.session_minutes)
            db.execute("INSERT INTO sessions VALUES (?,?,?,?,?)",
                       (_token_hash(token), row["user_id"], tenant_id, expiry.isoformat(), _now()))
            return token

    @property
    def _dummy_hash(self) -> str:
        if not hasattr(self, "__dummy_hash"):
            self.__dummy_hash = self._hasher.hash(secrets.token_urlsafe(32))
        return self.__dummy_hash

    def authenticate(self, token: str) -> VerifiedIdentity:
        if not isinstance(token, str) or len(token) > 256:
            raise PermissionError("Invalid session")
        with _db(self.database) as db:
            row = db.execute("""SELECT u.user_id,m.tenant_id,m.role,s.expires_at
                FROM sessions s JOIN users u USING(user_id)
                JOIN memberships m ON m.user_id=u.user_id AND m.tenant_id=s.tenant_id
                WHERE s.token_hash=? AND m.active=1 AND u.disabled=0""",
                             (_token_hash(token),)).fetchone()
            if row is None:
                raise PermissionError("Invalid session")
            expiry = datetime.fromisoformat(row["expires_at"])
            if datetime.now(timezone.utc) >= expiry:
                raise PermissionError("Invalid session")
            return VerifiedIdentity(LOCAL_ISSUER, row["user_id"], row["tenant_id"],
                                    frozenset({IdentityRole(row["role"])}), expiry)

    def logout(self, token: str) -> None:
        if not isinstance(token, str):
            return
        with _db(self.database) as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))

    def revoke_user_sessions(self, user_id: str) -> None:
        with _db(self.database) as db:
            db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

"""FastAPI dependency injection providers for authentication, credentials, and workflow."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Header, status

from ..agents import (
    DataGovInMarketProvider,
    OpenMeteoWeatherProvider,
    RythuSaathiWorkflow,
    default_coverage_resolver,
)
from ..credentials import (
    CredentialService,
    KeyEncryptionInterface,
    OsCredentialStoreUnavailable,
    WindowsCredentialStore,
)
from ..domain.seed import CROP_REGISTRY
from ..knowledge import (
    DocumentProcessor,
    HashEmbeddingProvider,
    LocalKnowledgeBase,
    load_curated,
    load_schemes,
)
from ..local_auth import LocalAuthentication
from ..security import VerifiedIdentity
from .exceptions import APIError
from .history import AdvisoryHistoryStore

ROOT = Path(__file__).resolve().parents[3]


class InMemoryKeyStore:
    """In-memory KEK store used as fallback or in testing environments."""

    def __init__(self):
        self._keys: dict[str, bytes] = {}

    def get_or_create_key(self, key_version: str) -> bytes:
        if key_version not in self._keys:
            self._keys[key_version] = secrets.token_bytes(32)
        return self._keys[key_version]

    def get_key(self, key_version: str) -> bytes:
        if key_version not in self._keys:
            raise OsCredentialStoreUnavailable(f"Key {key_version} not found in memory store")
        return self._keys[key_version]


# Global singletons / caches for performance across API calls
_AUTH_SERVICE: LocalAuthentication | None = None
_CREDENTIAL_SERVICE: CredentialService | None = None
_HISTORY_STORE: AdvisoryHistoryStore | None = None
_KNOWLEDGE_BASE: LocalKnowledgeBase | None = None
_KEY_STORE: KeyEncryptionInterface | None = None


def get_auth_db_path() -> Path:
    env_path = os.getenv("RYTHUSAATHI_AUTH_DB")
    if env_path:
        return Path(env_path).resolve()
    return ROOT / "tmp" / "rythusaathi-auth.sqlite3"


def get_history_db_path() -> Path:
    env_path = os.getenv("RYTHUSAATHI_HISTORY_DB")
    if env_path:
        return Path(env_path).resolve()
    return ROOT / "tmp" / "rythusaathi-history.sqlite3"


def get_auth_service() -> LocalAuthentication:
    global _AUTH_SERVICE
    if _AUTH_SERVICE is None:
        db_path = get_auth_db_path()
        _AUTH_SERVICE = LocalAuthentication(db_path)
    return _AUTH_SERVICE


def set_auth_service(service: LocalAuthentication | None) -> None:
    global _AUTH_SERVICE
    _AUTH_SERVICE = service


def get_key_store() -> KeyEncryptionInterface:
    global _KEY_STORE
    if _KEY_STORE is None:
        try:
            _KEY_STORE = WindowsCredentialStore()
            _KEY_STORE.get_or_create_key("api-check-v1")
        except Exception as exc:
            # Production MUST fail closed: in-memory fallback is only permitted
            # when explicitly enabled for testing or local development.
            allow_in_memory = os.getenv("RYTHUSAATHI_ALLOW_IN_MEMORY_KEY_STORE", "").lower() in (
                "1",
                "true",
                "yes",
            )
            if allow_in_memory:
                _KEY_STORE = InMemoryKeyStore()
            else:
                raise OsCredentialStoreUnavailable(
                    "OS-backed credential store (Windows Credential Manager) is unavailable. "
                    "Production requires a persistent OS credential store to protect encrypted BYOK secrets. "
                    "Ephemeral in-memory fallback is disabled by default to prevent silent data loss upon restart. "
                    "Set RYTHUSAATHI_ALLOW_IN_MEMORY_KEY_STORE=1 only for isolated testing environments."
                ) from exc
    return _KEY_STORE


def set_key_store(store: KeyEncryptionInterface | None) -> None:
    global _KEY_STORE
    _KEY_STORE = store


def get_credential_service(
    auth_service: Annotated[LocalAuthentication, Depends(get_auth_service)],
    key_store: Annotated[KeyEncryptionInterface, Depends(get_key_store)],
) -> CredentialService:
    global _CREDENTIAL_SERVICE
    if _CREDENTIAL_SERVICE is None or _CREDENTIAL_SERVICE.database != auth_service.database:
        _CREDENTIAL_SERVICE = CredentialService(auth_service.database, key_store)
    return _CREDENTIAL_SERVICE


def set_credential_service(service: CredentialService | None) -> None:
    global _CREDENTIAL_SERVICE
    _CREDENTIAL_SERVICE = service


def get_history_store() -> AdvisoryHistoryStore:
    global _HISTORY_STORE
    if _HISTORY_STORE is None:
        db_path = get_history_db_path()
        _HISTORY_STORE = AdvisoryHistoryStore(db_path)
    return _HISTORY_STORE


def set_history_store(store: AdvisoryHistoryStore | None) -> None:
    global _HISTORY_STORE
    _HISTORY_STORE = store


def get_knowledge_base() -> LocalKnowledgeBase:
    """Cached knowledge index containing curated ICAR advisories and central schemes."""
    global _KNOWLEDGE_BASE
    if _KNOWLEDGE_BASE is None:
        processor = DocumentProcessor()
        icar_path = ROOT / "data" / "agricultural" / "icar_advisories.json"
        scheme_path = ROOT / "data" / "schemes" / "central_agricultural_schemes.json"

        agriculture = load_curated(icar_path, CROP_REGISTRY) if icar_path.exists() else ()
        schemes = load_schemes(scheme_path) if scheme_path.exists() else ()

        chunks = [c for doc in agriculture for c in processor.process_curated(doc)]
        chunks.extend(c for doc in schemes for c in processor.process_scheme(doc))

        kb = LocalKnowledgeBase(HashEmbeddingProvider())
        kb.add(chunks)
        _KNOWLEDGE_BASE = kb
    return _KNOWLEDGE_BASE


def set_knowledge_base(kb: LocalKnowledgeBase | None) -> None:
    global _KNOWLEDGE_BASE
    _KNOWLEDGE_BASE = kb


def get_workflow(
    knowledge: Annotated[LocalKnowledgeBase, Depends(get_knowledge_base)],
) -> RythuSaathiWorkflow:
    """Build the seven-agent Microsoft Agent Framework workflow instance."""
    return RythuSaathiWorkflow(
        resolver=default_coverage_resolver(),
        knowledge=knowledge,
        weather_provider=OpenMeteoWeatherProvider(),
        market_provider=None,
    )


def extract_bearer_token(authorization: str | None = Header(default=None)) -> str:
    """Extract and validate the Bearer token from the HTTP Authorization header."""
    if not authorization:
        raise APIError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="missing_credentials",
            message="Authorization header with Bearer token is required",
        )
    parts = authorization.strip().split(" ", 1)
    if len(parts) != 2 or parts[0].casefold() != "bearer":
        raise APIError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="invalid_authorization_format",
            message="Authorization header must follow 'Bearer <token>' format",
        )
    token = parts[1].strip()
    if not token:
        raise APIError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="empty_token",
            message="Bearer token cannot be empty",
        )
    return token


def get_current_identity(
    token: Annotated[str, Depends(extract_bearer_token)],
    auth_service: Annotated[LocalAuthentication, Depends(get_auth_service)],
) -> VerifiedIdentity:
    """Verify session token against the Argon2 local authentication database."""
    try:
        identity = auth_service.authenticate(token)
        if not identity.is_active:
            raise APIError(
                status_code=status.HTTP_401_UNAUTHORIZED,
                code="token_expired",
                message="Session token has expired",
            )
        return identity
    except PermissionError:
        raise APIError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="invalid_session",
            message="Invalid or expired session token",
        )


"""Provider-neutral identity and BYOK authorization contracts.

This module defines the trusted-principal boundary and permission policy. It
does not validate tokens or persist credentials; those implementations depend
on the approved identity provider, tenant-membership source, KMS, and store.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol


def _required_identifier(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field_name} must be a non-empty, trimmed identifier")


def _aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class VerifiedIdentity:
    """Backend-only identity produced after validating an API access token.

    The identity adapter must validate signature, issuer, audience, token use,
    expiration, and active tenant membership before constructing this value.
    Request fields and unverified token claims are never accepted as identity.
    ``issuer`` plus ``subject`` is the stable user key; email is deliberately
    excluded because it can change or be reassigned.
    """

    issuer: str
    subject: str
    tenant_id: str
    roles: frozenset[IdentityRole]
    expires_at: datetime

    def __post_init__(self) -> None:
        _required_identifier(self.issuer, "issuer")
        _required_identifier(self.subject, "subject")
        _required_identifier(self.tenant_id, "tenant_id")
        if not isinstance(self.roles, frozenset) or any(
            not isinstance(role, IdentityRole) for role in self.roles
        ):
            raise ValueError("roles must be a frozenset of IdentityRole values")
        _aware(self.expires_at, "expires_at")

    @property
    def is_active(self) -> bool:
        return datetime.now(timezone.utc) < self.expires_at.astimezone(timezone.utc)


class AccessTokenVerifier(Protocol):
    """Adapter contract; concrete issuer and verification library are pending."""

    async def verify_access_token(self, token: str) -> VerifiedIdentity: ...


class IdentityRole(StrEnum):
    MEMBER = "member"
    TENANT_ADMIN = "tenant_admin"


@dataclass(frozen=True, slots=True)
class CredentialOwner:
    """Immutable owner binding for a user credential record."""

    tenant_id: str
    issuer: str
    subject: str

    def __post_init__(self) -> None:
        _required_identifier(self.tenant_id, "tenant_id")
        _required_identifier(self.issuer, "issuer")
        _required_identifier(self.subject, "subject")


class CredentialAction(StrEnum):
    CREATE = "create"
    STATUS = "status"
    USE = "use"
    ROTATE = "rotate"
    REVOKE = "revoke"
    DELETE = "delete"


class AuthorizationDenied(PermissionError):
    """Safe authorization error with no request or credential details."""

    def __init__(self) -> None:
        super().__init__("Credential operation is not authorized")


class CredentialAuthorizationPolicy:
    """Fail-closed owner-only policy for BYOK lifecycle operations."""

    @staticmethod
    def owner_for_creation(identity: VerifiedIdentity | None) -> CredentialOwner:
        """Derive ownership from verified identity, never caller-supplied IDs."""
        if not isinstance(identity, VerifiedIdentity) or not identity.is_active:
            raise AuthorizationDenied()
        return CredentialOwner(
            tenant_id=identity.tenant_id,
            issuer=identity.issuer,
            subject=identity.subject,
        )

    def authorize(
        self,
        identity: VerifiedIdentity | None,
        action: CredentialAction,
        owner: CredentialOwner,
    ) -> bool:
        if not isinstance(action, CredentialAction) or not isinstance(owner, CredentialOwner):
            return False
        if not isinstance(identity, VerifiedIdentity) or not identity.is_active:
            return False
        if identity.tenant_id != owner.tenant_id:
            return False
        if (identity.issuer, identity.subject) == (owner.issuer, owner.subject):
            return True
        return False

    def require(
        self,
        identity: VerifiedIdentity | None,
        action: CredentialAction,
        owner: CredentialOwner,
    ) -> None:
        if not self.authorize(identity, action, owner):
            raise AuthorizationDenied()


class CredentialState(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"
    DELETED = "deleted"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class CredentialStatus:
    """Non-secret status response suitable for a web or CLI client."""

    credential_id: str
    provider_id: str
    state: CredentialState
    generation: int
    updated_at: datetime

    def __post_init__(self) -> None:
        _required_identifier(self.credential_id, "credential_id")
        _required_identifier(self.provider_id, "provider_id")
        if not isinstance(self.state, CredentialState):
            raise ValueError("state must be a CredentialState")
        if (
            isinstance(self.generation, bool)
            or not isinstance(self.generation, int)
            or self.generation < 1
        ):
            raise ValueError("generation must be a positive integer")
        _aware(self.updated_at, "updated_at")

    def to_dict(self) -> dict[str, str | int]:
        return {
            "credential_id": self.credential_id,
            "provider_id": self.provider_id,
            "state": self.state.value,
            "generation": self.generation,
            "updated_at": self.updated_at.isoformat(),
        }


class AuditOutcome(StrEnum):
    SUCCESS = "success"
    DENIED = "denied"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CredentialAuditEvent:
    """Allowlisted audit fields; deliberately has no credential payload field."""

    event_id: str
    actor_tenant_id: str
    actor_issuer: str
    actor_subject: str
    credential_id: str
    action: CredentialAction
    outcome: AuditOutcome
    occurred_at: datetime

    def __post_init__(self) -> None:
        for field_name in (
            "event_id", "actor_tenant_id", "actor_issuer", "actor_subject",
            "credential_id",
        ):
            _required_identifier(getattr(self, field_name), field_name)
        if not isinstance(self.action, CredentialAction):
            raise ValueError("action must be a CredentialAction")
        if not isinstance(self.outcome, AuditOutcome):
            raise ValueError("outcome must be an AuditOutcome")
        _aware(self.occurred_at, "occurred_at")

    def to_dict(self) -> dict[str, str]:
        return {
            "event_id": self.event_id,
            "actor_tenant_id": self.actor_tenant_id,
            "actor_issuer": self.actor_issuer,
            "actor_subject": self.actor_subject,
            "credential_id": self.credential_id,
            "action": self.action.value,
            "outcome": self.outcome.value,
            "occurred_at": self.occurred_at.isoformat(),
        }

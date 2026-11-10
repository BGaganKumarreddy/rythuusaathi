from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from rythusaathi.security import (
    AuditOutcome,
    AuthorizationDenied,
    CredentialAction,
    CredentialAuthorizationPolicy,
    CredentialAuditEvent,
    CredentialOwner,
    CredentialState,
    CredentialStatus,
    IdentityRole,
    VerifiedIdentity,
)


class IdentityAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.policy = CredentialAuthorizationPolicy()
        self.owner_identity = VerifiedIdentity(
            issuer="https://issuer.example",
            subject="user-01",
            tenant_id="tenant-a",
            roles=frozenset({IdentityRole.MEMBER}),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
        self.owner = CredentialOwner(
            tenant_id="tenant-a", issuer="https://issuer.example", subject="user-01"
        )

    def test_identity_requires_verified_claim_fields_and_expiration(self):
        with self.assertRaises(ValueError):
            VerifiedIdentity(
                issuer="",
                subject="user-01",
                tenant_id="tenant-a",
                roles=frozenset(),
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
            )
        with self.assertRaises(ValueError):
            VerifiedIdentity(
                issuer="https://issuer.example",
                subject="user-01",
                tenant_id="tenant-a",
                roles=frozenset(),
                expires_at=datetime.now(),
            )

    def test_owner_can_manage_own_credential(self):
        for action in CredentialAction:
            with self.subTest(action=action):
                self.assertTrue(self.policy.authorize(self.owner_identity, action, self.owner))

    def test_tenant_admin_is_not_a_credential_owner(self):
        admin = VerifiedIdentity(
            issuer="https://issuer.example",
            subject="admin-01",
            tenant_id="tenant-a",
            roles=frozenset({IdentityRole.TENANT_ADMIN}),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
        for action in CredentialAction:
            with self.subTest(action=action):
                self.assertFalse(self.policy.authorize(admin, action, self.owner))

    def test_peer_cross_tenant_expired_and_missing_identity_are_denied(self):
        peer = VerifiedIdentity(
            issuer="https://issuer.example",
            subject="user-02",
            tenant_id="tenant-a",
            roles=frozenset({IdentityRole.MEMBER}),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
        other_tenant_admin = VerifiedIdentity(
            issuer="https://issuer.example",
            subject="admin-02",
            tenant_id="tenant-b",
            roles=frozenset({IdentityRole.TENANT_ADMIN}),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
        expired = VerifiedIdentity(
            issuer="https://issuer.example",
            subject="user-01",
            tenant_id="tenant-a",
            roles=frozenset({IdentityRole.MEMBER}),
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        for principal in (peer, other_tenant_admin, expired, None):
            with self.subTest(principal=principal):
                self.assertFalse(self.policy.authorize(principal, CredentialAction.STATUS, self.owner))
                with self.assertRaises(AuthorizationDenied):
                    self.policy.require(principal, CredentialAction.STATUS, self.owner)

    def test_creation_owner_is_derived_from_identity_not_payload(self):
        self.assertEqual(self.policy.owner_for_creation(self.owner_identity), self.owner)
        with self.assertRaises(AuthorizationDenied):
            self.policy.owner_for_creation(None)

    def test_status_and_audit_contracts_only_serialize_allowlisted_fields(self):
        timestamp = datetime.now(timezone.utc)
        status = CredentialStatus(
            credential_id="credential-01",
            provider_id="groq",
            state=CredentialState.ACTIVE,
            generation=1,
            updated_at=timestamp,
        ).to_dict()
        event = CredentialAuditEvent(
            event_id="event-01",
            actor_tenant_id="tenant-a",
            actor_issuer="https://issuer.example",
            actor_subject="user-01",
            credential_id="credential-01",
            action=CredentialAction.ROTATE,
            outcome=AuditOutcome.SUCCESS,
            occurred_at=timestamp,
        ).to_dict()
        for record in (status, event):
            self.assertNotIn("credential", record)
            self.assertNotIn("secret", record)
            self.assertNotIn("ciphertext", record)
            self.assertNotIn("wrapped_dek", record)

    def test_status_rejects_invalid_generation(self):
        for generation in (0, -1, True, 1.5):
            with self.subTest(generation=generation), self.assertRaises(ValueError):
                CredentialStatus(
                    credential_id="credential-01",
                    provider_id="groq",
                    state=CredentialState.ACTIVE,
                    generation=generation,
                    updated_at=datetime.now(timezone.utc),
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)

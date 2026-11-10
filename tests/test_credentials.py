from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rythusaathi.credentials import (CredentialService, CredentialUnavailable,
                                    OsCredentialStoreUnavailable)
from rythusaathi.security import IdentityRole, VerifiedIdentity


class MemoryKeyStore:
    def __init__(self):
        self.key = None
        self.available = True

    def get_or_create_key(self, version):
        if not self.available:
            raise OsCredentialStoreUnavailable("unavailable")
        if self.key is None:
            import secrets
            self.key = secrets.token_bytes(32)
        return self.key

    def get_key(self, version):
        if not self.available or self.key is None:
            raise OsCredentialStoreUnavailable("unavailable")
        return self.key


class CredentialLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "app.db"
        self.keys = MemoryKeyStore()
        self.service = CredentialService(self.db, self.keys)
        self.owner = self.identity("user-a", "tenant-a")
        self.peer = self.identity("user-b", "tenant-a")
        self.cross_tenant = self.identity("user-a", "tenant-b")

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def identity(subject, tenant):
        return VerifiedIdentity("local", subject, tenant, frozenset({IdentityRole.MEMBER}),
                                datetime.now(timezone.utc) + timedelta(minutes=30))

    def test_encryption_at_rest_dispatch_and_secret_safe_status_audit(self):
        canary = "groq-canary-super-secret-value"
        status = self.service.create(self.owner, "groq", canary)
        with closing(sqlite3.connect(self.db)) as db, db:
            row = db.execute("SELECT ciphertext,wrapped_dek,nonce,dek_nonce FROM credentials").fetchone()
            all_db = db.execute("SELECT * FROM credential_audit").fetchall()
        self.assertNotIn(canary.encode(), b"".join(row))
        output = self.service.dispatch_handoff(self.owner, status.credential_id,
                                               lambda secret, generation: (secret, generation))
        self.assertEqual(output[0], canary)
        safe = json.dumps({"status": self.service.status(self.owner, status.credential_id).to_dict(),
                           "audit": self.service.audit_events(self.owner), "db_audit": all_db})
        self.assertNotIn(canary, safe)
        self.assertNotIn("ciphertext", safe)
        self.assertNotIn("wrapped_dek", safe)

    def test_tampering_or_missing_os_key_fails_closed(self):
        status = self.service.create(self.owner, "groq", "key-canary")
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE credentials SET ciphertext=?", (b"tampered",))
        with self.assertRaises(CredentialUnavailable):
            self.service.dispatch_handoff(self.owner, status.credential_id, lambda key, gen: key)
        self.keys.available = False
        with self.assertRaises(CredentialUnavailable):
            self.service.dispatch_handoff(self.owner, status.credential_id, lambda key, gen: key)

    def test_authenticated_context_binds_tenant_and_owner(self):
        status = self.service.create(self.owner, "groq", "context-bound-key")
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE credentials SET tenant_id='tenant-b' WHERE credential_id=?",
                       (status.credential_id,))
        with self.assertRaises(CredentialUnavailable):
            self.service.dispatch_handoff(self.cross_tenant, status.credential_id,
                                          lambda key, gen: key)

    def test_owner_authorization_and_tenant_isolation(self):
        status = self.service.create(self.owner, "groq", "private-key")
        for identity in (self.peer, self.cross_tenant):
            with self.subTest(identity=identity), self.assertRaises(CredentialUnavailable):
                self.service.status(identity, status.credential_id)
            with self.subTest(identity=identity), self.assertRaises(CredentialUnavailable):
                self.service.dispatch_handoff(identity, status.credential_id, lambda key, gen: key)

    def test_rotation_revoke_and_delete_lifecycle(self):
        first = self.service.create(self.owner, "groq", "first-secret")
        second = self.service.rotate(self.owner, first.credential_id, "second-secret")
        self.assertEqual(second.generation, first.generation + 1)
        self.assertEqual(self.service.dispatch_handoff(self.owner, first.credential_id,
                                                       lambda key, gen: (key, gen)),
                         ("second-secret", second.generation))
        revoked = self.service.revoke(self.owner, first.credential_id)
        self.assertEqual(revoked.state.value, "revoked")
        with self.assertRaises(CredentialUnavailable):
            self.service.dispatch_handoff(self.owner, first.credential_id, lambda key, gen: key)
        self.service.delete(self.owner, first.credential_id)
        with self.assertRaises(CredentialUnavailable):
            self.service.dispatch_handoff(self.owner, first.credential_id, lambda key, gen: key)
        with closing(sqlite3.connect(self.db)) as db, db:
            row = db.execute("SELECT state,ciphertext,wrapped_dek FROM credentials").fetchone()
        self.assertEqual(row, ("deleted", b"", b""))
        self.assertEqual([e["action"] for e in self.service.audit_events(self.owner)],
                         ["create", "rotate", "revoke", "delete"])

    def test_os_store_failure_cannot_create_plaintext_record(self):
        self.keys.available = False
        with self.assertRaises(OsCredentialStoreUnavailable):
            self.service.create(self.owner, "groq", "never-store-plaintext")
        with closing(sqlite3.connect(self.db)) as db, db:
            self.assertEqual(db.execute("SELECT count(*) FROM credentials").fetchone()[0], 0)

    def test_provider_handoff_error_is_generic_and_audited_without_canary(self):
        canary = "handoff-error-secret-canary"
        status = self.service.create(self.owner, "groq", canary)

        def fail(secret, generation):
            raise RuntimeError(f"transport captured {secret}")

        with self.assertRaises(CredentialUnavailable) as error:
            self.service.dispatch_handoff(self.owner, status.credential_id, fail)
        self.assertNotIn(canary, str(error.exception))
        events = self.service.audit_events(self.owner)
        self.assertEqual(events[-1]["outcome"], "failed")
        self.assertNotIn(canary, json.dumps(events))

    def test_revoke_waits_for_bounded_dispatch_handoff(self):
        status = self.service.create(self.owner, "groq", "dispatch-key")
        entered, release, revoked = threading.Event(), threading.Event(), threading.Event()

        def handoff(secret, generation):
            entered.set()
            self.assertEqual(secret, "dispatch-key")
            self.assertEqual(generation, status.generation)
            if not release.wait(5):
                raise TimeoutError("test handoff release timed out")
            return "handed-off"

        dispatch_thread = threading.Thread(
            target=lambda: self.service.dispatch_handoff(self.owner, status.credential_id, handoff)
        )
        revoke_thread = threading.Thread(
            target=lambda: (self.service.revoke(self.owner, status.credential_id), revoked.set())
        )
        dispatch_thread.start()
        self.assertTrue(entered.wait(2))
        revoke_thread.start()
        time.sleep(0.1)
        self.assertFalse(revoked.is_set())
        release.set()
        dispatch_thread.join(5)
        revoke_thread.join(5)
        self.assertFalse(dispatch_thread.is_alive())
        self.assertFalse(revoke_thread.is_alive())
        self.assertTrue(revoked.is_set())


if __name__ == "__main__":
    unittest.main()

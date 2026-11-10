from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from rythusaathi.credentials import (CredentialRecoveryRequired, CredentialService,
                                    CredentialState, CredentialUnavailable)
from rythusaathi.local_auth import LocalAuthentication
from rythusaathi.recovery import restore_from_backup
from rythusaathi.security import IdentityRole


class MemoryKeys:
    def __init__(self):
        import secrets
        self.key = secrets.token_bytes(32)

    def get_or_create_key(self, version):
        return self.key

    def get_key(self, version):
        return self.key


class SQLiteRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "app.sqlite3"
        self.backup = Path(self.temp.name) / "stale.sqlite3"
        self.auth = LocalAuthentication(self.database)
        self.auth.create_tenant("farm", "Farm")
        self.user_id = self.auth.register("farmer", "farmer-password-long", "farm")
        self.token = self.auth.login("farmer", "farmer-password-long", "farm")
        self.identity = self.auth.authenticate(self.token)
        self.keys = MemoryKeys()
        self.credentials = CredentialService(self.database, self.keys)

    def tearDown(self):
        self.temp.cleanup()

    def _backup_now(self):
        with closing(sqlite3.connect(self.database)) as source, \
                closing(sqlite3.connect(self.backup)) as destination:
            source.backup(destination)
            destination.commit()

    async def test_stale_restore_invalidates_sessions_and_quarantines_credentials(self):
        recoverable = self.credentials.create(self.identity, "groq", "old-backup-key")
        revoked = self.credentials.create(self.identity, "groq", "revoked-after-backup-key")
        deleted = self.credentials.create(self.identity, "groq", "deleted-after-backup-key")
        self._backup_now()

        self.credentials.revoke(self.identity, revoked.credential_id)
        self.credentials.delete(self.identity, deleted.credential_id)
        import secrets
        self.keys.key = secrets.token_bytes(32)
        report = restore_from_backup(self.backup, self.database)

        self.assertEqual(report.invalidated_sessions, 1)
        self.assertEqual(report.quarantined_credentials, 3)
        self.assertEqual(report.preserved_tombstones, 2)
        self.assertEqual(report.reenrollment_required, 2)
        with self.assertRaises(PermissionError):
            self.auth.authenticate(self.token)

        new_token = self.auth.login("farmer", "farmer-password-long", "farm")
        identity = self.auth.authenticate(new_token)
        service = CredentialService(self.database, self.keys)
        self.assertTrue(service.recovery_required())
        self.assertEqual(service.status(identity, recoverable.credential_id).state,
                         CredentialState.UNAVAILABLE)
        self.assertEqual(service.status(identity, revoked.credential_id).state,
                         CredentialState.UNAVAILABLE)
        self.assertEqual(service.status(identity, deleted.credential_id).state,
                         CredentialState.DELETED)
        self.assertEqual([event["action"] for event in service.audit_events(identity)][-2:],
                         ["revoke", "delete"])

        with closing(sqlite3.connect(self.database)) as db:
            row = db.execute("SELECT state,ciphertext,wrapped_dek,needs_reenrollment FROM credentials "
                             "WHERE credential_id=?", (recoverable.credential_id,)).fetchone()
        self.assertEqual(row, ("revoked", b"", b"", 1))
        with self.assertRaises(CredentialRecoveryRequired):
            service.dispatch_handoff(identity, recoverable.credential_id, lambda secret, gen: secret)
        with self.assertRaises(CredentialRecoveryRequired):
            service.complete_recovery()
        with self.assertRaises(CredentialRecoveryRequired):
            service.create(identity, "groq", "blocked-during-recovery")

        async def valid_key(value):
            self.assertEqual(value, "reenrolled-and-validated-key")
            return True

        active = await service.reenroll(identity, recoverable.credential_id,
                                        "reenrolled-and-validated-key", valid_key)
        self.assertEqual(active.state, CredentialState.ACTIVE)
        with self.assertRaises(CredentialRecoveryRequired):
            service.dispatch_handoff(identity, recoverable.credential_id, lambda secret, gen: secret)

        async def valid_revoked(value):
            self.assertEqual(value, "new-revoked-key")
            return True

        await service.reenroll(identity, revoked.credential_id, "new-revoked-key", valid_revoked)
        service.complete_recovery()
        self.assertFalse(service.recovery_required())
        self.assertEqual(service.dispatch_handoff(identity, recoverable.credential_id,
                                                  lambda secret, gen: secret),
                         "reenrolled-and-validated-key")
        with self.assertRaises(CredentialUnavailable):
            service.dispatch_handoff(identity, deleted.credential_id, lambda secret, gen: secret)

    async def test_failed_validation_cannot_activate_or_replace_recovered_key(self):
        status = self.credentials.create(self.identity, "groq", "stale-key")
        self._backup_now()
        restore_from_backup(self.backup, self.database)
        token = self.auth.login("farmer", "farmer-password-long", "farm")
        identity = self.auth.authenticate(token)
        service = CredentialService(self.database, self.keys)

        async def invalid(_value):
            return False

        with self.assertRaisesRegex(RuntimeError, "Credential validation failed"):
            await service.reenroll(identity, status.credential_id, "unvalidated-key", invalid)
        self.assertEqual(service.status(identity, status.credential_id).state,
                         CredentialState.UNAVAILABLE)
        with self.assertRaises(CredentialRecoveryRequired):
            service.dispatch_handoff(identity, status.credential_id, lambda secret, gen: secret)


if __name__ == "__main__":
    unittest.main()

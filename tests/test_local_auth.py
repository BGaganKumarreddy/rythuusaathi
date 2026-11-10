from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from rythusaathi.local_auth import LocalAuthentication
from rythusaathi.security import IdentityRole


class LocalAuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "identity.db"
        self.auth = LocalAuthentication(self.db, session_minutes=10)
        self.auth.create_tenant("farm-a", "Farm A")
        self.auth.create_tenant("farm-b", "Farm B")
        self.user_id = self.auth.register("farmer", "correct horse battery staple", "farm-a")

    def tearDown(self):
        self.temp.cleanup()

    def test_password_hash_and_opaque_server_side_session(self):
        token = self.auth.login("farmer", "correct horse battery staple", "farm-a")
        with closing(sqlite3.connect(self.db)) as db, db:
            password_hash = db.execute("SELECT password_hash FROM users").fetchone()[0]
            token_hash = db.execute("SELECT token_hash FROM sessions").fetchone()[0]
        self.assertTrue(password_hash.startswith("$argon2id$"))
        self.assertNotEqual(token_hash, token)
        identity = self.auth.authenticate(token)
        self.assertEqual(identity.subject, self.user_id)
        self.assertEqual(identity.tenant_id, "farm-a")

    def test_wrong_password_and_missing_membership_fail_closed(self):
        with self.assertRaisesRegex(PermissionError, "Authentication failed"):
            self.auth.login("farmer", "wrong password", "farm-a")
        with self.assertRaisesRegex(PermissionError, "Authentication failed"):
            self.auth.login("farmer", "correct horse battery staple", "farm-b")

    def test_logout_and_membership_removal_invalidate_sessions(self):
        token = self.auth.login("farmer", "correct horse battery staple", "farm-a")
        self.auth.logout(token)
        with self.assertRaises(PermissionError):
            self.auth.authenticate(token)
        token = self.auth.login("farmer", "correct horse battery staple", "farm-a")
        self.auth.set_membership_active(self.user_id, "farm-a", False)
        with self.assertRaises(PermissionError):
            self.auth.authenticate(token)


if __name__ == "__main__":
    unittest.main()

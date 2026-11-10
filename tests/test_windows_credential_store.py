from __future__ import annotations

import base64
import subprocess
import sys
import unittest
import uuid

from rythusaathi.credentials import OsCredentialStoreUnavailable, WindowsCredentialStore


@unittest.skipUnless(sys.platform == "win32", "Windows Credential Manager integration test")
class WindowsCredentialStoreIntegrationTests(unittest.TestCase):
    def test_create_retrieve_process_persistence_missing_and_delete(self):
        store = WindowsCredentialStore()
        version = "integration-test-" + uuid.uuid4().hex
        try:
            with self.assertRaises(OsCredentialStoreUnavailable):
                store.get_key(version)

            created = store.get_or_create_key(version)
            self.assertEqual(len(created), 32)
            self.assertEqual(store.get_key(version), created)

            # A fresh Python process proves the value persisted in the OS store.
            code = (
                "import base64, secrets, sys; "
                "from rythusaathi.credentials import WindowsCredentialStore; "
                "actual=WindowsCredentialStore().get_key(sys.argv[1]); "
                "expected=base64.b64decode(sys.stdin.buffer.read()); "
                "sys.exit(0 if secrets.compare_digest(actual, expected) else 9)"
            )
            result = subprocess.run(
                [sys.executable, "-c", code, version],
                input=base64.b64encode(created),
                capture_output=True,
                check=False,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, "fresh process could not retrieve the same OS key")
            self.assertEqual(result.stdout, b"")
            self.assertEqual(result.stderr, b"")

            store.delete_key(version)
            with self.assertRaises(OsCredentialStoreUnavailable):
                store.get_key(version)
        finally:
            # Cleanup is idempotent and never emits key material.
            store.delete_key(version)


if __name__ == "__main__":
    unittest.main()

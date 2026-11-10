"""Provision and manage local owner-scoped Gemini and Groq BYOK credentials on Windows."""

from __future__ import annotations

import argparse
import getpass
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rythusaathi.credentials import CredentialService, WindowsCredentialStore
from rythusaathi.local_auth import LocalAuthentication


def _login(auth: LocalAuthentication, tenant_id: str) -> tuple[str, object]:
    username = input("Username: ").strip()
    password = getpass.getpass("Password: ")
    token = auth.login(username, password, tenant_id)
    return token, auth.authenticate(token)


def _run(args: argparse.Namespace) -> int:
    auth = LocalAuthentication(args.database)
    if args.command == "register":
        tenant_id = input("New tenant ID: ").strip()
        tenant_name = input("Tenant display name: ").strip()
        auth.create_tenant(tenant_id, tenant_name)
        username = input("Username: ").strip()
        password = getpass.getpass("Password (minimum 12 characters): ")
        user_id = auth.register(username, password, tenant_id)
        print(f"Created local user {user_id} for tenant {tenant_id}.")
        return 0

    tenant_id = input("Tenant ID: ").strip()
    if args.command == "add-user":
        username = input("Username: ").strip()
        password = getpass.getpass("Password (minimum 12 characters): ")
        user_id = auth.register(username, password, tenant_id)
        print(f"Created local user {user_id} for tenant {tenant_id}.")
        return 0

    token, identity = _login(auth, tenant_id)
    try:
        credentials = CredentialService(args.database, WindowsCredentialStore())
        if args.command == "add-gemini":
            api_key = getpass.getpass("Gemini API key (input is hidden): ")
            status = credentials.create(identity, "gemini", api_key)
            api_key = ""
            print(f"Stored owner-scoped Gemini credential. Credential ID: {status.credential_id}")
            return 0
        if args.command == "list-gemini":
            statuses = credentials.list_status(identity, provider_id="gemini")
            if not statuses:
                print("No Gemini credentials are registered for this user.")
                return 0
            for status in statuses:
                print(f"{status.credential_id}  {status.state.value}  generation {status.generation}")
            return 0
        if args.command == "add-groq":
            api_key = getpass.getpass("Groq API key (input is hidden): ")
            status = credentials.create(identity, "groq", api_key)
            api_key = ""
            print(f"Stored owner-scoped Groq credential. Credential ID: {status.credential_id}")
            return 0
        statuses = credentials.list_status(identity, provider_id="groq")
        if not statuses:
            print("No Groq credentials are registered for this user.")
            return 0
        for status in statuses:
            print(f"{status.credential_id}  {status.state.value}  generation {status.generation}")
        return 0
    finally:
        auth.logout(token)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manage local RythuSaathi users and OS-protected Gemini / Groq BYOK credentials"
    )
    parser.add_argument("--database", type=Path,
                        default=ROOT / "tmp" / "phase5-local-auth.sqlite3",
                        help="SQLite identity and credential database")
    parser.add_argument(
        "command",
        choices=("register", "add-user", "add-gemini", "list-gemini", "add-groq", "list-groq"),
    )
    args = parser.parse_args()
    try:
        return _run(args)
    except Exception as exc:
        # Never print SDK, OS store or secret-bearing exception text.
        print(f"BYOK operation failed ({type(exc).__name__}); details were withheld.",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

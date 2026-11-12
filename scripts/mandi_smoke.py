"""Independent smoke test for data.gov.in Mandi Market Price provider."""

from __future__ import annotations

import argparse
import asyncio
import getpass
from pathlib import Path
import sqlite3
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rythusaathi.agents.data_gov_in import DataGovInMarketProvider, MandiDiagnosticCode
from rythusaathi.credentials import CredentialService, WindowsCredentialStore
from rythusaathi.domain.seed import CROP_REGISTRY, LOCATION_REGISTRY
from rythusaathi.local_auth import LocalAuthentication


def _discover_db_defaults(db_path: Path) -> tuple[str | None, str | None]:
    """Inspect local database for default tenant and user to streamline local CLI usage."""
    if not db_path.exists():
        return None, None
    try:
        with sqlite3.connect(db_path) as conn:
            tenants = conn.execute("SELECT tenant_id FROM tenants").fetchall()
            users = conn.execute("SELECT username FROM users WHERE disabled = 0").fetchall()
            default_tenant = tenants[0][0] if len(tenants) == 1 else None
            default_user = users[0][0] if len(users) == 1 else None
            return default_tenant, default_user
    except Exception:
        return None, None


async def async_main(args: argparse.Namespace) -> int:
    api_key = args.api_key
    credential_service = None
    identity = None
    credential_id = args.credential_id
    auth_db = args.database or (ROOT / "tmp" / "phase5-local-auth.sqlite3")

    # If no raw API key passed, look up via BYOK credential store
    if not api_key:
        if auth_db.exists():
            default_tenant, default_user = _discover_db_defaults(auth_db)
            tenant_id = args.tenant_id or default_tenant or "local-cli"
            username = args.username or default_user

            if username and not args.no_prompt:
                try:
                    auth = LocalAuthentication(auth_db)
                    password = args.password
                    if not password:
                        prompt_msg = f"Password for local BYOK user '{username}' (press Enter to skip to raw API key): "
                        password = getpass.getpass(prompt_msg).strip()

                    if password:
                        token = auth.login(username, password, tenant_id)
                        identity = auth.authenticate(token)
                        credential_service = CredentialService(auth_db, WindowsCredentialStore())
                        if not credential_id:
                            statuses = credential_service.list_status(identity, provider_id="data.gov.in")
                            active = [s for s in statuses if s.state.value == "active"]
                            if active:
                                credential_id = active[0].credential_id
                                print(f"[BYOK] Authenticated user '{username}'. Using encrypted credential {credential_id}.")
                except Exception as exc:
                    print(f"[BYOK] Authentication failed ({type(exc).__name__}: {exc}); falling back to API key prompt.", file=sys.stderr)

    if not api_key and not credential_id:
        if args.no_prompt:
            print("Error: No API key or BYOK credential available in non-interactive mode.", file=sys.stderr)
            return 2
        print("No active data.gov.in credential in session.")
        api_key = getpass.getpass("Enter data.gov.in API key (input is hidden): ").strip()
        if not api_key:
            print("API key is required to query data.gov.in Mandi prices.", file=sys.stderr)
            return 2

    # Crop and Location resolution
    crop_matches = CROP_REGISTRY.search(args.crop)
    crop_id = crop_matches[0].crop_id if crop_matches else args.crop

    location_matches = LOCATION_REGISTRY.search(args.location)
    location_id = location_matches[0].location_id if location_matches else args.location

    print("=" * 60)
    print("RythuSaathi — data.gov.in Mandi Market Price Smoke Test")
    print("=" * 60)
    print(f"Crop: {args.crop} (Resolved ID: {crop_id})")
    print(f"Location: {args.location} (Resolved ID: {location_id})")

    provider = DataGovInMarketProvider(
        api_key=api_key if api_key else None,
        credential_service=credential_service,
        identity=identity,
        credential_id=credential_id,
        timeout_seconds=args.timeout,
    )

    req_id = f"smoke-m-{uuid.uuid4().hex[:8]}"
    print(f"\nDispatching live Mandi price request (request_id={req_id})...")
    quote = await provider.get_quote(req_id, crop_id, location_id)

    diag = provider.last_diagnostic

    if quote is None:
        print("\nMarket outcome: NO MATCHING RECORDS / UNAVAILABLE")
        if diag:
            print(f"Diagnostic Code: {diag.code.value}")
            if diag.http_status is not None:
                print(f"HTTP Status: {diag.http_status}")
            if diag.provider_error_message:
                print(f"Provider Error Message: {diag.provider_error_message}")
            if diag.sanitized_response_body:
                print(f"Response Body Preview: {diag.sanitized_response_body}")
            if diag.sanitized_params:
                print(f"Sanitized Request Parameters: {diag.sanitized_params}")
            if diag.retry_after_seconds is not None:
                print(f"Retry-After: {diag.retry_after_seconds:.1f}s")
            if diag.exception_type_chain:
                print(f"Exception Chain: {diag.exception_type_chain}")
            print(f"Record Count: {diag.record_count}")
            print(f"Latency: {diag.latency_seconds:.3f}s")
        print("=" * 60)
        return 1

    print("\nMarket outcome: SUCCESS")
    if diag:
        print(f"Diagnostic Code: {diag.code.value}")
        print(f"HTTP Status: {diag.http_status or 200}")
        print(f"Record Count: {diag.record_count}")
        print(f"Latency: {diag.latency_seconds:.3f}s")
    print(f"  Market Name: {quote.market_name}")
    print(f"  Commodity: {quote.commodity}")
    if quote.variety:
        print(f"  Variety: {quote.variety}")
    if quote.grade:
        print(f"  Grade: {quote.grade}")
    print(f"  Modal Price: {quote.price:g} {quote.unit}")
    if quote.min_price is not None and quote.max_price is not None:
        print(f"  Price Range: {quote.min_price:g} - {quote.max_price:g} {quote.unit}")
    if quote.arrival_date:
        print(f"  Arrival Date: {quote.arrival_date}")
    if quote.state or quote.district:
        print(f"  Region: {quote.district or 'Unknown'}, {quote.state or 'Unknown'}")
    print(f"  Observed At: {quote.observed_at.isoformat()}")
    print(f"  Retrieved At: {quote.retrieved_at.isoformat()}")
    print(f"  Source Attribution: {quote.source.title}")
    print(f"  Publisher: {quote.source.publisher}")
    print("=" * 60)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Test data.gov.in Mandi integration independently")
    parser.add_argument("--crop", default="cotton", help="Crop name or ID (default: cotton)")
    parser.add_argument("--location", default="Warangal", help="Location name or ID (default: Warangal)")
    parser.add_argument("--api-key", help="Raw API key (omitted to use BYOK store or prompt)")
    parser.add_argument("--database", type=Path, help="SQLite credential database")
    parser.add_argument("--tenant-id", help="Tenant ID")
    parser.add_argument("--username", help="User username")
    parser.add_argument("--password", help="User password")
    parser.add_argument("--credential-id", help="Specific Mandi credential ID")
    parser.add_argument("--timeout", type=float, default=15.0, help="Request timeout in seconds (default: 15.0)")
    parser.add_argument("--no-prompt", action="store_true", help="Non-interactive mode (fail instead of prompt)")
    args = parser.parse_args()
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())

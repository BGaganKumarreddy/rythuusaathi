"""Local command-line entrypoint for the shared Phase 4 workflow."""

from __future__ import annotations

import argparse
import asyncio
import getpass
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rythusaathi.agents import (
    AuthenticatedCerebrasInference,
    AuthenticatedGeminiInference,
    AuthenticatedGroqInference,
    DataGovInMarketProvider,
    FarmerRequest,
    OpenMeteoWeatherProvider,
    RythuSaathiWorkflow,
    WorkflowMode,
    default_coverage_resolver,
    empty_coverage_resolver,
)
from rythusaathi.domain.seed import CROP_REGISTRY
from rythusaathi.credentials import CredentialService, WindowsCredentialStore
from rythusaathi.inference.providers.cerebras import CerebrasProvider, CerebrasProviderConfig
from rythusaathi.inference.providers.gemini import GeminiProvider, GeminiProviderConfig
from rythusaathi.inference.providers.groq import GroqProvider, GroqProviderConfig
from rythusaathi.inference.service import (
    AuthenticatedCerebrasService,
    AuthenticatedGeminiService,
    AuthenticatedGroqService,
)
from rythusaathi.local_auth import LocalAuthentication
from rythusaathi.knowledge import (
    DocumentProcessor,
    FastEmbedProvider,
    HashEmbeddingProvider,
    PersistentKnowledgeBase,
    load_curated,
    load_schemes,
)


def _build_index(database: Path, *, semantic: bool, model_cache: Path | None):
    provider = (FastEmbedProvider(cache_dir=model_cache or ROOT / "tmp" / "models")
                if semantic else HashEmbeddingProvider())
    processor = DocumentProcessor()
    documents = load_curated(ROOT / "data" / "agricultural" / "icar_advisories.json", CROP_REGISTRY)
    schemes = load_schemes(ROOT / "data" / "schemes" / "central_agricultural_schemes.json")
    chunks = [chunk for item in documents for chunk in processor.process_curated(item)]
    chunks.extend(chunk for item in schemes for chunk in processor.process_scheme(item))
    store = PersistentKnowledgeBase(database, provider)
    added = store.add(chunks)
    return store, added


async def _run(args: argparse.Namespace) -> int:
    query = " ".join(args.question).strip() if args.question else input("Farmer question: ").strip()
    if not query:
        print("A farmer question is required.", file=sys.stderr)
        return 2

    semantic = not args.lexical
    database = args.database or ROOT / "tmp" / (
        "phase4-advisory-hash.sqlite3" if args.lexical
        else "phase4-advisory-fastembed.sqlite3"
    )
    store, added = _build_index(database, semantic=semantic, model_cache=args.model_cache)
    mode = ("pinned FastEmbed embeddings" if semantic
            else "deterministic hash baseline (not semantic embeddings)")
    print(f"SQLite knowledge index: {database} ({store.size} chunks; {added} newly indexed; {mode})")
    session_token = None
    auth = None
    inference = None
    user_id = "local-cli-user"
    tenant_id = "local-cli"
    try:
        if args.mode == "gemini":
            auth_database = args.auth_database or ROOT / "tmp" / "phase5-local-auth.sqlite3"
            auth = LocalAuthentication(auth_database)
            tenant_id_input = input("Tenant ID: ").strip()
            username = input("Username: ").strip()
            password = getpass.getpass("Password: ")
            session_token = auth.login(username, password, tenant_id_input)
            identity = auth.authenticate(session_token)
            user_id = identity.subject
            tenant_id = identity.tenant_id
            credential_service = CredentialService(auth_database, WindowsCredentialStore())
            cid = args.credential_id
            statuses = credential_service.list_status(identity, provider_id="gemini")
            active = [s for s in statuses if s.state.value == "active"]
            if cid:
                valid_cids = {s.credential_id for s in active}
                if cid not in valid_cids:
                    cid = active[0].credential_id if active else None
            elif active:
                cid = active[0].credential_id
            if not cid:
                print("No active Gemini credential found. Run scripts/byok_cli.py add-gemini first.",
                      file=sys.stderr)
                auth.logout(session_token)
                return 2
            provider = GeminiProvider(GeminiProviderConfig.from_environment())
            service = AuthenticatedGeminiService(auth, credential_service, provider)
            inference = AuthenticatedGeminiInference(service, session_token, cid)
        elif args.mode == "groq":
            auth_database = args.auth_database or ROOT / "tmp" / "phase5-local-auth.sqlite3"
            auth = LocalAuthentication(auth_database)
            tenant_id_input = input("Tenant ID: ").strip()
            username = input("Username: ").strip()
            password = getpass.getpass("Password: ")
            session_token = auth.login(username, password, tenant_id_input)
            identity = auth.authenticate(session_token)
            user_id = identity.subject
            tenant_id = identity.tenant_id
            credential_service = CredentialService(auth_database, WindowsCredentialStore())
            cid = args.credential_id
            statuses = credential_service.list_status(identity, provider_id="groq")
            active = [s for s in statuses if s.state.value == "active"]
            if cid:
                valid_cids = {s.credential_id for s in active}
                if cid not in valid_cids:
                    cid = active[0].credential_id if active else None
            elif active:
                cid = active[0].credential_id
            if not cid:
                print("No active Groq credential found. Run scripts/byok_cli.py add-groq first.",
                      file=sys.stderr)
                auth.logout(session_token)
                return 2
            provider = GroqProvider(GroqProviderConfig.from_environment())
            service = AuthenticatedGroqService(auth, credential_service, provider)
            inference = AuthenticatedGroqInference(service, session_token, cid)
        elif args.mode == "cerebras":
            auth_database = args.auth_database or ROOT / "tmp" / "phase5-local-auth.sqlite3"
            auth = LocalAuthentication(auth_database)
            tenant_id_input = input("Tenant ID: ").strip()
            username = input("Username: ").strip()
            password = getpass.getpass("Password: ")
            session_token = auth.login(username, password, tenant_id_input)
            identity = auth.authenticate(session_token)
            user_id = identity.subject
            tenant_id = identity.tenant_id
            credential_service = CredentialService(auth_database, WindowsCredentialStore())
            cid = args.credential_id
            statuses = credential_service.list_status(identity, provider_id="cerebras")
            active = [s for s in statuses if s.state.value == "active"]
            if cid:
                valid_cids = {s.credential_id for s in active}
                if cid not in valid_cids:
                    cid = active[0].credential_id if active else None
            elif active:
                cid = active[0].credential_id
            if not cid:
                print("No active Cerebras credential found. Run scripts/byok_cli.py add-cerebras first.",
                      file=sys.stderr)
                auth.logout(session_token)
                return 2
            provider = CerebrasProvider(CerebrasProviderConfig.from_environment())
            service = AuthenticatedCerebrasService(auth, credential_service, provider)
            inference = AuthenticatedCerebrasInference(service, session_token, cid)
        weather_provider = OpenMeteoWeatherProvider()
        market_provider = None
        if "credential_service" in locals() and credential_service and "identity" in locals() and identity:
            mandi_statuses = credential_service.list_status(identity, provider_id="data.gov.in")
            mandi_active = [s for s in mandi_statuses if s.state.value == "active"]
            if mandi_active:
                market_provider = DataGovInMarketProvider(
                    credential_service=credential_service,
                    identity=identity,
                    credential_id=mandi_active[0].credential_id,
                )
        workflow = RythuSaathiWorkflow(
            resolver=default_coverage_resolver(),
            knowledge=store,
            weather_provider=weather_provider,
            market_provider=market_provider,
        )
        request = FarmerRequest(
            request_id=f"local-cli:{uuid.uuid4().hex}",
            user_id=user_id,
            tenant_id=tenant_id,
            query=query,
            crop_query=args.crop,
            location_query=args.location,
            variety_query=args.variety,
            growth_stage_query=args.growth_stage,
        )
        try:
            response = await workflow.run(
                request,
                mode=WorkflowMode(args.mode),
                inference=inference,
                on_progress=lambda msg: print(f"  [workflow] {msg}", flush=True),
            )
        except KeyboardInterrupt:
            dispatched = False
            if inference is not None:
                dispatched = bool(
                    getattr(inference, "dispatched_count", 0) > 0
                    or inference.usage_records
                    or inference.diagnostics
                )
            if dispatched:
                print(
                    "\n[interrupted] Advisory execution was cancelled by user (Ctrl+C). A provider request was already dispatched.",
                    file=sys.stderr,
                )
            else:
                print(
                    "\n[interrupted] Advisory execution was cancelled by user (Ctrl+C). No provider request was dispatched.",
                    file=sys.stderr,
                )
            return 130
        if inference is not None and inference.diagnostics:
            print("\nSanitized inference diagnostics:")
            for diagnostic in inference.diagnostics:
                status = (f" http_status={diagnostic.http_status}"
                          if diagnostic.http_status is not None else "")
                model = f" model={diagnostic.model_id}" if diagnostic.model_id else ""
                rate_fields = []
                if diagnostic.agent_name is not None:
                    rate_fields.append(f"agent={diagnostic.agent_name}")
                if diagnostic.request_id is not None:
                    rate_fields.append(f"request_id={diagnostic.request_id}")
                if diagnostic.elapsed_seconds is not None:
                    rate_fields.append(f"elapsed_seconds={diagnostic.elapsed_seconds:.2f}")
                if diagnostic.response_character_count is not None:
                    rate_fields.append(f"response_chars={diagnostic.response_character_count}")
                if diagnostic.finish_reason is not None:
                    rate_fields.append(f"finish_reason={diagnostic.finish_reason}")
                if diagnostic.parsing_outcome is not None:
                    rate_fields.append(f"parsing_outcome={diagnostic.parsing_outcome}")
                if diagnostic.provider_error_status is not None:
                    rate_fields.append(f"error_status={diagnostic.provider_error_status}")
                if diagnostic.provider_error_message is not None:
                    rate_fields.append(f"error_message={diagnostic.provider_error_message}")
                if diagnostic.rate_limit_scope is not None:
                    rate_fields.append(f"rate_limit_scope={diagnostic.rate_limit_scope.value}")
                for name, value in (
                    ("estimated_input_tokens", diagnostic.estimated_input_tokens),
                    ("output_token_limit", diagnostic.output_token_limit),
                    ("budget_limit_tokens", diagnostic.budget_limit_tokens),
                    ("budget_remaining_tokens", diagnostic.budget_remaining_tokens),
                    ("budget_decision", diagnostic.budget_decision),
                    ("retry_decision", diagnostic.retry_decision),
                ):
                    if value is not None:
                        rate_fields.append(f"{name}={value}")
                if diagnostic.retry_after_seconds is not None:
                    rate_fields.append(f"retry_after_seconds={diagnostic.retry_after_seconds:g}")
                for name, value in (
                    ("remaining_requests", diagnostic.rate_limit_remaining_requests),
                    ("remaining_tokens", diagnostic.rate_limit_remaining_tokens),
                    ("reset_requests_seconds", diagnostic.rate_limit_reset_requests_seconds),
                    ("reset_tokens_seconds", diagnostic.rate_limit_reset_tokens_seconds),
                ):
                    if value is not None:
                        rate_fields.append(f"{name}={value:g}" if isinstance(value, float)
                                           else f"{name}={value}")
                rate_summary = (" " + " ".join(rate_fields)) if rate_fields else ""
                print(
                    f"- code={diagnostic.code.value} provider={diagnostic.provider}"
                    f"{model} stage={diagnostic.stage.value}{status}"
                    f"{rate_summary} retries_attempted={diagnostic.retries_attempted}"
                    f" timestamp={diagnostic.timestamp.isoformat()}"
                    f" correlation_id={diagnostic.correlation_id}"
                )
                for prior in diagnostic.prior_rate_limits:
                    prior_fields = [f"scope={prior.scope.value}"]
                    if prior.retry_after_seconds is not None:
                        prior_fields.append(f"retry_after_seconds={prior.retry_after_seconds:g}")
                    if prior.remaining_requests is not None:
                        prior_fields.append(f"remaining_requests={prior.remaining_requests}")
                    if prior.remaining_tokens is not None:
                        prior_fields.append(f"remaining_tokens={prior.remaining_tokens}")
                    if prior.reset_requests_seconds is not None:
                        prior_fields.append(f"reset_requests_seconds={prior.reset_requests_seconds:g}")
                    if prior.reset_tokens_seconds is not None:
                        prior_fields.append(f"reset_tokens_seconds={prior.reset_tokens_seconds:g}")
                    print("  prior_429 " + " ".join(prior_fields))
                if diagnostic.code.value == "workflow_timeout":
                    print("  Overall workflow deadline was exceeded; deterministic fallback response was synthesized.")
                elif diagnostic.code.value == "gemini_connect_timeout":
                    print("  Connection to Gemini endpoint timed out; check network connectivity or proxy settings.")
                elif diagnostic.code.value == "gemini_read_timeout":
                    print("  Gemini response read timed out after connection established.")
                elif diagnostic.code.value == "request_cancelled":
                    print("  Request was cancelled before completion.")
                elif diagnostic.code.value == "groq_quota_exhausted":
                    print("  Provider quota is exhausted; wait for its reset or review Groq account limits/billing.")
                elif diagnostic.code.value == "groq_tpm_budget_exceeded":
                    print("  Local TPM budget rejected this request before provider dispatch; evidence was not truncated.")
                elif (diagnostic.rate_limit_scope is not None
                      and diagnostic.rate_limit_scope.value == "tokens_per_minute"
                      and diagnostic.retry_after_seconds is not None):
                    print("  Groq TPM reset exceeds the automatic wait cap; the workflow used deterministic fallback.")
                elif (diagnostic.code.value == "groq_rate_limited"
                      and diagnostic.rate_limit_scope is not None
                      and diagnostic.rate_limit_scope.value == "unknown"):
                    print("  Rate-limit scope is unknown from returned metadata; check Groq account limits before retrying.")
        if inference is not None and inference.usage_records:
            print("\nSanitized inference token usage:")
            for usage in inference.usage_records:
                actual = (
                    f" actual_input_tokens={usage.actual_input_tokens}"
                    f" actual_output_tokens={usage.actual_output_tokens}"
                    f" actual_total_tokens={usage.actual_total_tokens}"
                    if usage.actual_total_tokens is not None else " actual_usage=unavailable"
                )
                print(
                    f"- agent={usage.agent_name} provider={getattr(service, 'provider_id', 'unknown')} model={usage.model_id}"
                    f" estimated_input_tokens={usage.estimated_input_tokens}"
                    f" output_token_limit={usage.output_token_limit}{actual}"
                    f" budget_limit_tokens={usage.budget_limit_tokens}"
                    f" budget_remaining_tokens={usage.budget_remaining_tokens}"
                    f" budget_decision={usage.budget_decision}"
                    f" retries_attempted={usage.retries_attempted}"
                    f" retry_decision={usage.retry_decision}"
                    f" correlation_id={usage.correlation_id}"
                )
        print("\n" + response.response_text)
        print(f"\nExecution mode: {response.execution_mode.value}; provider: "
              f"{response.provider_id or 'none'}; model: {response.model_id or 'none'}")
        print("\nAgent execution:")
        for result in response.agent_results:
            prov_info = f" [provider={result.provider_id} model={result.model_id}]" if result.provider_id else ""
            print(f"- {result.agent_id.value}: {result.status.value} ({result.duration_ms:.1f} ms){prov_info}")
        return 0
    finally:
        if auth is not None and session_token is not None:
            auth.logout(session_token)
        store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local RythuSaathi six-agent advisory workflow")
    parser.add_argument("question", nargs="*", help="farmer question; prompt interactively when omitted")
    parser.add_argument("--crop", help="explicit crop query (e.g. cotton)")
    parser.add_argument("--location", help="explicit location query (e.g. Adilabad)")
    parser.add_argument("--growth-stage", help="explicit growth stage query (e.g. flowering)")
    parser.add_argument("--variety", help="explicit crop variety query")
    parser.add_argument("--database", type=Path, help="SQLite vector index path")
    parser.add_argument("--lexical", action="store_true",
                        help="use the deterministic hashing baseline instead of pinned FastEmbed embeddings")
    parser.add_argument("--model-cache", type=Path, help="FastEmbed model snapshot cache")
    parser.add_argument("--mode", choices=[item.value for item in WorkflowMode],
                        default=WorkflowMode.DETERMINISTIC.value,
                        help="deterministic by default; gemini uses Gemini 3.8 Flash; groq and cerebras also supported")
    parser.add_argument("--auth-database", type=Path,
                        help="local identity/credential database for Gemini / Groq / Cerebras mode")
    parser.add_argument("--credential-id", help="owner-scoped Gemini / Groq / Cerebras credential ID; never a raw API key")
    args = parser.parse_args()
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\n[interrupted] Advisory execution was cancelled by user (Ctrl+C). A provider request may already have been dispatched.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

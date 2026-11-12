"""Isolated smoke test and demo runner for Cerebras inference.

Requirements:
- Reuse existing BYOK credential lookup (LocalAuthentication + CredentialService + WindowsCredentialStore).
- Reuse CerebrasProvider adapter (gpt-oss-120b).
- Execute:
  1. Minimal authenticated live inference request.
  2. Coordinator prompt test with Pydantic validation (CoordinatorDraft).
  3. Six-agent advisory workflow with bounded token budget (only after Coordinator acceptance).
- Respect Cerebras TPM budget guard (30,000 limit, 3,000 headroom).
- Do not expose credentials or API keys.
- Print sanitized diagnostics, HTTP status, latency, token usage, and validation results.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import getpass
import json
import os
from pathlib import Path
import sys
import time
from typing import Any
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_framework import Message

from rythusaathi.agents import (
    FarmerRequest,
    RythuSaathiWorkflow,
    WorkflowMode,
    default_coverage_resolver,
    empty_coverage_resolver,
)
from rythusaathi.agents.contracts import MarketProvider, WeatherProvider
from rythusaathi.agents.data_gov_in import DataGovInMarketProvider
from rythusaathi.agents.open_meteo import OpenMeteoWeatherProvider
from rythusaathi.agents.llm import (
    AuthenticatedCerebrasInference,
    CoordinatorDraft,
    call_structured,
    make_json_request,
    validate_evidence_ids,
)
from rythusaathi.agents.workflow import _COORDINATOR_PROMPT
from rythusaathi.credentials import (
    CredentialService,
    CredentialUnavailable,
    WindowsCredentialStore,
)
from rythusaathi.domain import FactState
from rythusaathi.domain.seed import CROP_REGISTRY
from rythusaathi.inference.contracts import (
    InferenceDiagnostic,
    InferenceDiagnosticCode,
    InferenceRequest,
    InferenceStage,
    InferenceTokenUsage,
    ProviderFailure,
)
from rythusaathi.inference.providers.cerebras import (
    DEFAULT_MODEL_ID,
    CerebrasProvider,
    CerebrasProviderConfig,
)
from rythusaathi.inference.service import AuthenticatedCerebrasService
from rythusaathi.knowledge import (
    DocumentProcessor,
    FastEmbedProvider,
    HashEmbeddingProvider,
    PersistentKnowledgeBase,
    load_curated,
    load_schemes,
)
from rythusaathi.local_auth import LocalAuthentication


@dataclass(frozen=True)
class CerebrasExperimentResult:
    """Standardized result for each Cerebras inference stage."""

    stage_name: str
    model_id: str
    success: bool
    http_status: int | None
    elapsed_seconds: float
    response_text: str | None = None
    coordinator_draft: CoordinatorDraft | None = None
    workflow_response: Any | None = None
    diagnostic: InferenceDiagnostic | None = None
    usage: InferenceTokenUsage | None = None
    total_tokens_used: int = 0
    structured_validation_passed: bool = False
    validation_notes: str | None = None
    exception_type_chain: str | None = None


def _discover_db_defaults(database_path: Path) -> tuple[str | None, str | None]:
    if not database_path.exists():
        return None, None
    try:
        import sqlite3
        conn = sqlite3.connect(database_path)
        cur = conn.cursor()
        cur.execute("SELECT tenant_id FROM tenants LIMIT 1")
        row = cur.fetchone()
        tenant = row[0] if row else None
        cur.execute("SELECT username FROM users LIMIT 1")
        row = cur.fetchone()
        user = row[0] if row else None
        conn.close()
        return tenant, user
    except Exception:
        return None, None


def setup_authenticated_cerebras_inference(
    database_path: Path,
    *,
    tenant_id: str | None = None,
    username: str | None = None,
    password: str | None = None,
    credential_id: str | None = None,
    provider: CerebrasProvider | None = None,
    key_store: Any | None = None,
    timeout_seconds: float = 30.0,
    model_id: str | None = None,
) -> tuple[AuthenticatedCerebrasInference, str, str, str, LocalAuthentication]:
    """Authenticate local user and construct AuthenticatedCerebrasInference adapter."""
    if not database_path.is_file():
        raise FileNotFoundError(f"Local auth database not found at {database_path}")

    auth = LocalAuthentication(database_path)
    def_tenant, def_user = _discover_db_defaults(database_path)
    tid = tenant_id or def_tenant or input("Tenant ID: ").strip()
    user = username or def_user or input("Username: ").strip()
    pwd = password or getpass.getpass(f"Password for user '{user}': ")

    session_token = auth.login(user, pwd, tid)
    identity = auth.authenticate(session_token)

    store = key_store or WindowsCredentialStore()
    cred_service = CredentialService(database_path, store)

    cid = credential_id
    statuses = cred_service.list_status(identity, provider_id="cerebras")
    active = [s for s in statuses if s.state.value == "active"]
    if cid:
        valid_cids = {s.credential_id for s in active}
        if cid not in valid_cids:
            cid = active[0].credential_id if active else None
    elif active:
        cid = active[0].credential_id
    if not cid:
        auth.logout(session_token)
        raise CredentialUnavailable()

    if provider is not None:
        prov = provider
    else:
        effective_model = model_id or os.environ.get("CEREBRAS_MODEL_ID", DEFAULT_MODEL_ID)
        prov_config = CerebrasProviderConfig(
            model_id=effective_model,
            timeout_seconds=timeout_seconds,
            tpm_limit_tokens=int(os.environ.get("CEREBRAS_TPM_LIMIT_TOKENS", "30000")),
            tpm_headroom_tokens=int(os.environ.get("CEREBRAS_TPM_HEADROOM_TOKENS", "3000")),
            default_max_output_tokens=int(os.environ.get("CEREBRAS_MAX_OUTPUT_TOKENS", "1024")),
        )
        prov = CerebrasProvider(prov_config)

    service = AuthenticatedCerebrasService(auth, cred_service, prov)
    inference = AuthenticatedCerebrasInference(service, session_token, cid)
    return inference, identity.subject, identity.tenant_id, session_token, auth


async def run_cerebras_minimal_probe(
    inference: AuthenticatedCerebrasInference,
    *,
    expected_user_id: str,
    expected_tenant_id: str,
    prompt: str = "Respond with OK.",
    max_output_tokens: int = 16,
) -> CerebrasExperimentResult:
    """Send a minimal non-streaming chat-completion request using the authenticated Cerebras adapter."""
    request = InferenceRequest(
        messages=[Message("user", [prompt])],
        options={"max_tokens": max_output_tokens, "temperature": 0},
        agent_name="minimal_probe",
    )
    model_id = inference.model_id or DEFAULT_MODEL_ID
    start_time = time.monotonic()
    try:
        provider_resp = await inference.complete(
            request,
            expected_user_id=expected_user_id,
            expected_tenant_id=expected_tenant_id,
        )
        elapsed = max(0.0, time.monotonic() - start_time)
        usage = inference.usage_records[-1] if inference.usage_records else None
        resp_text = ""
        if provider_resp.response and provider_resp.response.messages:
            for m in provider_resp.response.messages:
                if m.role == "assistant":
                    resp_text += "".join(
                        item if isinstance(item, str) else getattr(item, "text", "")
                        for item in m.contents
                    ).strip()
        http_status = usage.http_status if usage and usage.http_status else 200
        tokens = usage.actual_total_tokens if usage and usage.actual_total_tokens else 0
        return CerebrasExperimentResult(
            stage_name="1. Minimal Provider Connectivity",
            model_id=model_id,
            success=True,
            http_status=http_status,
            elapsed_seconds=elapsed,
            response_text=resp_text,
            structured_validation_passed=True,
            validation_notes="Minimal probe response received.",
            usage=usage,
            total_tokens_used=tokens,
        )
    except ProviderFailure as exc:
        elapsed = max(0.0, time.monotonic() - start_time)
        diag = exc.diagnostic
        usage = inference.usage_records[-1] if inference.usage_records else None
        tokens = usage.actual_total_tokens if usage and usage.actual_total_tokens else 0
        http_status = getattr(diag, "http_status", None) or (usage.http_status if usage else None)
        return CerebrasExperimentResult(
            stage_name="1. Minimal Provider Connectivity",
            model_id=model_id,
            success=False,
            http_status=http_status,
            elapsed_seconds=elapsed,
            diagnostic=diag,
            usage=usage,
            total_tokens_used=tokens,
            exception_type_chain=getattr(diag, "exception_type_chain", None) if diag else None,
        )
    except Exception as exc:
        elapsed = max(0.0, time.monotonic() - start_time)
        diag = inference.diagnostics[-1] if inference.diagnostics else None
        http_status = getattr(diag, "http_status", None) if diag else None
        return CerebrasExperimentResult(
            stage_name="1. Minimal Provider Connectivity",
            model_id=model_id,
            success=False,
            http_status=http_status,
            elapsed_seconds=elapsed,
            diagnostic=diag,
            exception_type_chain=type(exc).__name__,
        )


async def run_cerebras_coordinator_probe(
    inference: AuthenticatedCerebrasInference,
    *,
    expected_user_id: str,
    expected_tenant_id: str,
    farmer_question: str = "My cotton crop in Adilabad has leaf spot, what should I do?",
) -> CerebrasExperimentResult:
    """Send a structured Coordinator prompt and validate the output against CoordinatorDraft."""
    evidence_id_1 = "ev:crop:icar-cotton-leafspot-01"
    evidence_id_2 = "ev:weather:open-meteo-adilabad-01"
    evidence_id_3 = "ev:market:data-gov-in-adilabad-cotton-01"
    allowed_ids = {evidence_id_1, evidence_id_2, evidence_id_3}

    payload = {
        "farmer_question": farmer_question,
        "crop": "Cotton",
        "location": "Adilabad",
        "growth_stage": "Vegetative",
        "intake_summary": "Cotton crop in Adilabad with reported leaf spot symptoms in vegetative stage.",
        "agent_results": {
            "crop": {
                "summary": f"Identified Cercospora leaf spot management practices under [{evidence_id_1}].",
                "evidence_ids": [evidence_id_1],
            },
            "weather": {
                "summary": f"Observed humidity 78% and temperature 29C favorable for fungal spread under [{evidence_id_2}].",
                "evidence_ids": [evidence_id_2],
            },
            "market": {
                "summary": f"Adilabad APMC modal price 7,100 INR/quintal reported under [{evidence_id_3}].",
                "evidence_ids": [evidence_id_3],
            },
            "scheme": {
                "summary": "No specific scheme subsidies identified.",
                "evidence_ids": [],
            },
        },
        "source_catalog": [
            {"source_id": "src:icar:cotton", "title": "ICAR Cotton IPM Advisory", "mode": "curated_document"},
            {"source_id": "src:weather:open-meteo", "title": "Open-Meteo Weather API", "mode": "live_provider"},
            {"source_id": "src:market:data-gov-in", "title": "data.gov.in Agmarknet", "mode": "live_provider"},
        ],
        "evidence_catalog": [
            {
                "evidence_id": evidence_id_1,
                "source_title": "ICAR Cotton IPM Advisory",
                "excerpt": "For leaf spot in cotton, spray copper oxychloride 50 WP @ 2.5 g/L or mancozeb 75 WP @ 2 g/L upon initial symptom appearance.",
                "mode": "live",
            },
            {
                "evidence_id": evidence_id_2,
                "source_title": "Open-Meteo Weather API",
                "excerpt": "Temperature 29.2 C, relative humidity 78%, precipitation probability 20%.",
                "mode": "live",
            },
            {
                "evidence_id": evidence_id_3,
                "source_title": "data.gov.in Agmarknet",
                "excerpt": "Adilabad APMC Cotton modal price 7,100 INR per quintal on current date.",
                "mode": "live",
            },
        ],
        "missing_information": [],
        "conflicts": [],
    }

    request = make_json_request(
        system_prompt=_COORDINATOR_PROMPT,
        payload=payload,
        response_model=CoordinatorDraft,
        agent_name="coordinator",
        max_output_tokens=2048,
    )
    model_id = inference.model_id or DEFAULT_MODEL_ID
    start_time = time.monotonic()
    try:
        raw_result = await call_structured(
            inference,
            request,
            expected_user_id=expected_user_id,
            expected_tenant_id=expected_tenant_id,
            response_model=CoordinatorDraft,
        )
        elapsed = max(0.0, time.monotonic() - start_time)
        usage = inference.usage_records[-1] if inference.usage_records else None
        tokens = usage.actual_total_tokens if usage and usage.actual_total_tokens else 0

        draft = CoordinatorDraft.model_validate(raw_result)
        citations_valid = True
        citation_note = "All citations belong to allowed evidence IDs."
        try:
            validate_evidence_ids(draft.statements, allowed_ids)
        except Exception as cite_err:
            citations_valid = False
            citation_note = f"Citation validation failed: {cite_err}"

        return CerebrasExperimentResult(
            stage_name="2. Coordinator Structured Synthesis Probe",
            model_id=model_id,
            success=citations_valid,
            http_status=usage.http_status if usage and usage.http_status else 200,
            elapsed_seconds=elapsed,
            coordinator_draft=draft,
            usage=usage,
            total_tokens_used=tokens,
            structured_validation_passed=citations_valid,
            validation_notes=citation_note,
        )
    except ProviderFailure as exc:
        elapsed = max(0.0, time.monotonic() - start_time)
        diag = exc.diagnostic
        usage = inference.usage_records[-1] if inference.usage_records else None
        tokens = usage.actual_total_tokens if usage and usage.actual_total_tokens else 0
        http_status = getattr(diag, "http_status", None) or (usage.http_status if usage else None)
        return CerebrasExperimentResult(
            stage_name="2. Coordinator Structured Synthesis Probe",
            model_id=model_id,
            success=False,
            http_status=http_status,
            elapsed_seconds=elapsed,
            diagnostic=diag,
            usage=usage,
            total_tokens_used=tokens,
            exception_type_chain=getattr(diag, "exception_type_chain", None) if diag else None,
        )
    except Exception as exc:
        elapsed = max(0.0, time.monotonic() - start_time)
        diag = inference.diagnostics[-1] if inference.diagnostics else None
        http_status = getattr(diag, "http_status", None) if diag else None
        return CerebrasExperimentResult(
            stage_name="2. Coordinator Structured Synthesis Probe",
            model_id=model_id,
            success=False,
            http_status=http_status,
            elapsed_seconds=elapsed,
            diagnostic=diag,
            exception_type_chain=type(exc).__name__,
        )


async def run_cerebras_workflow(
    inference: AuthenticatedCerebrasInference,
    *,
    expected_user_id: str,
    expected_tenant_id: str,
    knowledge_database: Path,
    crop_query: str = "cotton",
    location_query: str = "Adilabad",
    growth_stage_query: str = "flowering",
    farmer_question: str = "My cotton crop in Adilabad has leaf spot, what should I do?",
    weather_provider: WeatherProvider | None = None,
    market_provider: MarketProvider | None = None,
    resolver: Any | None = None,
) -> CerebrasExperimentResult:
    """Execute the full six-agent workflow in WorkflowMode.CEREBRAS."""
    kb_store = PersistentKnowledgeBase(knowledge_database, HashEmbeddingProvider())
    res = resolver or default_coverage_resolver()
    wp = weather_provider or OpenMeteoWeatherProvider()
    mp = market_provider or DataGovInMarketProvider()

    workflow = RythuSaathiWorkflow(
        resolver=res,
        knowledge=kb_store,
        weather_provider=wp,
        market_provider=mp,
    )

    req = FarmerRequest(
        query=farmer_question,
        crop_query=crop_query,
        location_query=location_query,
        growth_stage_query=growth_stage_query,
        user_id=expected_user_id,
        tenant_id=expected_tenant_id,
    )

    start_time = time.monotonic()
    model_id = inference.model_id or DEFAULT_MODEL_ID
    try:
        response = await workflow.run(
            req,
            mode=WorkflowMode.CEREBRAS,
            inference=inference,
        )
        elapsed = max(0.0, time.monotonic() - start_time)
        total_tokens = sum(
            rec.actual_total_tokens for rec in inference.usage_records if rec.actual_total_tokens
        )
        return CerebrasExperimentResult(
            stage_name="3. Six-Agent Advisory Workflow",
            model_id=model_id,
            success=response.status.value in {"completed", "partial"},
            http_status=200,
            elapsed_seconds=elapsed,
            workflow_response=response,
            total_tokens_used=total_tokens,
            structured_validation_passed=True,
            validation_notes=f"Workflow completed with status: {response.status.value}",
        )
    except ProviderFailure as exc:
        elapsed = max(0.0, time.monotonic() - start_time)
        diag = exc.diagnostic
        usage = inference.usage_records[-1] if inference.usage_records else None
        tokens = usage.actual_total_tokens if usage and usage.actual_total_tokens else 0
        http_status = getattr(diag, "http_status", None) or (usage.http_status if usage else None)
        return CerebrasExperimentResult(
            stage_name="3. Six-Agent Advisory Workflow",
            model_id=model_id,
            success=False,
            http_status=http_status,
            elapsed_seconds=elapsed,
            diagnostic=diag,
            usage=usage,
            total_tokens_used=tokens,
            exception_type_chain=getattr(diag, "exception_type_chain", None) if diag else None,
        )
    except Exception as exc:
        elapsed = max(0.0, time.monotonic() - start_time)
        diag = inference.diagnostics[-1] if inference.diagnostics else None
        http_status = getattr(diag, "http_status", None) if diag else None
        return CerebrasExperimentResult(
            stage_name="3. Six-Agent Advisory Workflow",
            model_id=model_id,
            success=False,
            http_status=http_status,
            elapsed_seconds=elapsed,
            diagnostic=diag,
            exception_type_chain=type(exc).__name__,
        )


def print_cerebras_experiment_report(result: CerebrasExperimentResult) -> None:
    """Print sanitized diagnostics, latency, tokens, and structured validation."""
    print("=" * 60)
    print(f"Stage: {result.stage_name}")
    print("=" * 60)
    print(f"Provider: cerebras")
    print(f"Model ID: {result.model_id}")
    print(f"Outcome: {'SUCCESS' if result.success else 'FAILED'}")
    print(f"HTTP Status: {result.http_status if result.http_status is not None else 'N/A'}")
    print(f"Latency: {result.elapsed_seconds:.3f}s")
    print(f"Total Tokens Used: {result.total_tokens_used}")

    if result.usage is not None:
        print(f"Input Tokens: {result.usage.actual_input_tokens}")
        print(f"Output Tokens: {result.usage.actual_output_tokens}")
        if result.usage.finish_reason:
            print(f"Finish Reason: {result.usage.finish_reason}")
        print(f"Remaining TPM Budget: {result.usage.budget_remaining_tokens}")

    if result.response_text:
        print(f"Response Preview: {result.response_text[:200]}")

    if result.coordinator_draft:
        print("\nStructured Output Validation:")
        print(f"- Pydantic Validation Passed: {result.structured_validation_passed}")
        print(f"- Validation Notes: {result.validation_notes}")
        print(f"- Statements Produced: {len(result.coordinator_draft.statements)}")
        for idx, stmt in enumerate(result.coordinator_draft.statements, 1):
            print(f"  [{idx}] ({stmt.kind}) {stmt.text[:120]}... Citing: {list(stmt.evidence_ids)}")
        if result.coordinator_draft.follow_up_questions:
            print(f"- Follow-up Questions: {result.coordinator_draft.follow_up_questions}")

    if result.workflow_response is not None:
        resp = result.workflow_response
        print("\nWorkflow Execution Summary:")
        print(f"- Status: {resp.status.value}")
        print(f"- Execution Mode: {resp.execution_mode.value}")
        print(f"- Provider ID: {resp.provider_id}")
        print(f"- Model ID: {resp.model_id}")
        print(f"- Evidence Records: {len(resp.evidence)}")
        print(f"- Findings: {len(resp.findings)}")
        print(f"- Recommendations: {len(resp.recommendations)}")
        print(f"- Agent Results: {len(resp.agent_results)}")
        for ar in resp.agent_results:
            print(f"  * {ar.agent_id.value}: {ar.status.value} ({ar.duration_ms:.1f}ms)")

    if result.diagnostic is not None:
        diag = result.diagnostic
        print("\nSanitized Diagnostics:")
        print(f"- Diagnostic Code: {diag.code.value if hasattr(diag.code, 'value') else diag.code}")
        print(f"- Stage: {diag.stage.value if hasattr(diag.stage, 'value') else diag.stage}")
        if diag.finish_reason:
            print(f"- Finish Reason: {diag.finish_reason}")
        if diag.parsing_outcome:
            print(f"- Parsing Outcome: {diag.parsing_outcome}")
        if diag.response_character_count is not None:
            print(f"- Response Character Count: {diag.response_character_count}")
        out_limit = diag.output_token_limit or (result.usage.output_token_limit if result.usage else None)
        out_actual = result.usage.actual_output_tokens if result.usage else None
        if out_limit is not None and out_actual is not None and out_actual >= out_limit:
            print(f"- Output Truncation: True (output reached limit of {out_limit} tokens)")
        elif diag.finish_reason == "length":
            print(f"- Output Truncation: True (finish_reason is length)")
        if diag.provider_error_code:
            print(f"- Provider Error Code: {diag.provider_error_code}")
        if diag.provider_error_status:
            print(f"- Provider Error Status: {diag.provider_error_status}")
        if diag.provider_error_message:
            print(f"- Provider Error Message: {diag.provider_error_message}")
        if diag.retry_after_seconds is not None:
            print(f"- Retry-After: {diag.retry_after_seconds:.2f}s")
        if diag.sanitized_response_headers:
            print(f"- Response Headers: {dict(diag.sanitized_response_headers)}")

    if result.exception_type_chain:
        print(f"- Exception Type Chain: {result.exception_type_chain}")
    print("=" * 60 + "\n")


async def main() -> int:
    parser = argparse.ArgumentParser(description="RythuSaathi Cerebras Live Smoke Test Runner")
    parser.add_argument(
        "--stage",
        choices=["minimal", "coordinator", "workflow", "all"],
        default="minimal",
        help="Inference stage to execute (default: minimal)",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=ROOT / "tmp" / "phase5-local-auth.sqlite3",
        help="Local SQLite identity/credential database path",
    )
    parser.add_argument(
        "--knowledge-db",
        type=Path,
        default=ROOT / "tmp" / "phase4-advisory-hash.sqlite3",
        help="Knowledge database for workflow stage",
    )
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID, help="Cerebras model ID")
    parser.add_argument("--tenant-id", help="Tenant ID for authentication")
    parser.add_argument("--username", help="Username for authentication")
    parser.add_argument("--credential-id", help="Explicit Cerebras credential ID")
    parser.add_argument("--timeout", type=float, default=30.0, help="Request timeout in seconds")
    parser.add_argument(
        "--allow-workflow",
        action="store_true",
        help="Explicitly permit the six-agent workflow stage after Coordinator acceptance",
    )
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("RythuSaathi — Cerebras Live Inference Smoke Test")
    print(f"Target Model: {args.model_id}")
    print("=" * 60)

    try:
        inference, user_id, tenant_id, session_token, auth = setup_authenticated_cerebras_inference(
            args.database,
            tenant_id=args.tenant_id,
            username=args.username,
            credential_id=args.credential_id,
            timeout_seconds=args.timeout,
            model_id=args.model_id,
        )
    except CredentialUnavailable:
        print(
            "ERROR: No active Cerebras credential found in local vault.\n"
            "Run 'python scripts/byok_cli.py add-cerebras' to store an owner-scoped Cerebras key first.",
            file=sys.stderr,
        )
        return 2
    except Exception as exc:
        print(f"ERROR: Failed to initialize authenticated inference: {type(exc).__name__}", file=sys.stderr)
        return 2

    try:
        # Stage 1: Minimal Connectivity
        if args.stage in {"minimal", "coordinator", "all"}:
            min_result = await run_cerebras_minimal_probe(
                inference,
                expected_user_id=user_id,
                expected_tenant_id=tenant_id,
            )
            print_cerebras_experiment_report(min_result)
            if not min_result.success:
                print("Minimal probe failed. Halting before subsequent stages.", file=sys.stderr)
                return 1

        # Stage 2: Coordinator Structured Synthesis
        if args.stage in {"coordinator", "all"}:
            coord_result = await run_cerebras_coordinator_probe(
                inference,
                expected_user_id=user_id,
                expected_tenant_id=tenant_id,
            )
            print_cerebras_experiment_report(coord_result)
            if not coord_result.success:
                print("Coordinator probe failed. Halting before workflow stage.", file=sys.stderr)
                return 1

        # Stage 3: Full Workflow (Only after Coordinator acceptance and with explicit confirmation)
        if args.stage in {"workflow", "all"}:
            if args.stage == "all" and not args.allow_workflow:
                print(
                    "NOTICE: Coordinator smoke test completed. Workflow migration is stopped.\n"
                    "To execute the full workflow after review, pass --stage workflow (or --stage all --allow-workflow)."
                )
                return 0

            mandi_provider = None
            try:
                cred_service = CredentialService(args.database, WindowsCredentialStore())
                ident = auth.authenticate(session_token)
                mandi_statuses = cred_service.list_status(ident, provider_id="data.gov.in")
                mandi_active = [s for s in mandi_statuses if s.state.value == "active"]
                if mandi_active:
                    mandi_provider = DataGovInMarketProvider(
                        credential_service=cred_service,
                        identity=ident,
                        credential_id=mandi_active[0].credential_id,
                    )
            except Exception:
                mandi_provider = None

            print("\nExecuting Six-Agent Advisory Workflow with Cerebras...")
            wf_result = await run_cerebras_workflow(
                inference,
                expected_user_id=user_id,
                expected_tenant_id=tenant_id,
                knowledge_database=args.knowledge_db,
                market_provider=mandi_provider,
            )
            print_cerebras_experiment_report(wf_result)
            if not wf_result.success:
                return 1

        return 0
    finally:
        auth.logout(session_token)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))


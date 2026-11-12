"""Isolated smoke test and demo runner for Groq inference.

Requirements:
- Reuse existing BYOK credential lookup (LocalAuthentication + CredentialService + WindowsCredentialStore).
- Reuse real GroqProvider adapter (openai/gpt-oss-20b).
- Execute:
  1. Minimal authenticated live inference request.
  2. Structured Intake request with Pydantic validation (IntakeInterpretation).
  3. Six-agent advisory workflow with bounded token budget.
- Respect local TPM budget guard (8000 limit, 800 headroom).
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
    empty_coverage_resolver,
)
from rythusaathi.agents.contracts import MarketProvider, WeatherProvider
from rythusaathi.agents.data_gov_in import DataGovInMarketProvider
from rythusaathi.agents.open_meteo import OpenMeteoWeatherProvider
from rythusaathi.agents.llm import (
    AuthenticatedGroqInference,
    IntakeInterpretation,
    call_structured,
    make_json_request,
)
from rythusaathi.agents.workflow import _INTAKE_PROMPT
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
from rythusaathi.inference.providers.groq import (
    DEFAULT_MODEL_ID,
    GroqProvider,
    GroqProviderConfig,
)
from rythusaathi.inference.service import AuthenticatedGroqService
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
class GroqExperimentResult:
    """Standardized result for each Groq inference stage."""

    stage_name: str
    model_id: str
    success: bool
    http_status: int | None
    elapsed_seconds: float
    response_text: str | None = None
    interpretation: IntakeInterpretation | None = None
    workflow_response: Any | None = None
    diagnostic: InferenceDiagnostic | None = None
    usage: InferenceTokenUsage | None = None
    total_tokens_used: int = 0
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


def setup_authenticated_groq_inference(
    database_path: Path,
    *,
    tenant_id: str | None = None,
    username: str | None = None,
    password: str | None = None,
    credential_id: str | None = None,
    provider: GroqProvider | None = None,
    key_store: Any | None = None,
    timeout_seconds: float = 30.0,
    model_id: str | None = None,
) -> tuple[AuthenticatedGroqInference, str, str, str, LocalAuthentication]:
    """Authenticate local user and construct AuthenticatedGroqInference adapter."""
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
    if not cid:
        statuses = cred_service.list_status(identity, provider_id="groq")
        active = [s for s in statuses if s.state.value == "active"]
        if not active:
            auth.logout(session_token)
            raise CredentialUnavailable()
        cid = active[0].credential_id

    if provider is not None:
        prov = provider
    else:
        effective_model = model_id or os.environ.get("GROQ_MODEL_ID", DEFAULT_MODEL_ID)
        prov_config = GroqProviderConfig(
            model_id=effective_model,
            timeout_seconds=timeout_seconds,
            tpm_limit_tokens=int(os.environ.get("GROQ_TPM_LIMIT_TOKENS", "8000")),
            tpm_headroom_tokens=int(os.environ.get("GROQ_TPM_HEADROOM_TOKENS", "800")),
            default_max_output_tokens=int(os.environ.get("GROQ_MAX_OUTPUT_TOKENS", "1024")),
        )
        prov = GroqProvider(prov_config)

    service = AuthenticatedGroqService(auth, cred_service, prov)
    inference = AuthenticatedGroqInference(service, session_token, cid)
    return inference, identity.subject, identity.tenant_id, session_token, auth


async def run_groq_minimal_probe(
    inference: AuthenticatedGroqInference,
    *,
    expected_user_id: str,
    expected_tenant_id: str,
    prompt: str = "Respond with OK.",
    max_output_tokens: int = 16,
) -> GroqExperimentResult:
    """Send a minimal non-streaming chat-completion request using the authenticated Groq adapter."""
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
        return GroqExperimentResult(
            stage_name="1. Minimal Chat Completion",
            model_id=model_id,
            success=True,
            http_status=http_status,
            elapsed_seconds=elapsed,
            response_text=resp_text,
            interpretation=None,
            diagnostic=None,
            usage=usage,
            total_tokens_used=tokens,
            exception_type_chain=None,
        )
    except ProviderFailure as exc:
        elapsed = max(0.0, time.monotonic() - start_time)
        diag = exc.diagnostic
        http_status = getattr(diag, "http_status", None) if diag else None
        return GroqExperimentResult(
            stage_name="1. Minimal Chat Completion",
            model_id=model_id,
            success=False,
            http_status=http_status,
            elapsed_seconds=elapsed,
            response_text=None,
            interpretation=None,
            diagnostic=diag,
            usage=None,
            total_tokens_used=0,
            exception_type_chain=getattr(diag, "exception_type_chain", None) if diag else None,
        )
    except Exception as exc:
        elapsed = max(0.0, time.monotonic() - start_time)
        diag = inference.diagnostics[-1] if inference.diagnostics else None
        http_status = getattr(diag, "http_status", None) if diag else None
        return GroqExperimentResult(
            stage_name="1. Minimal Chat Completion",
            model_id=model_id,
            success=False,
            http_status=http_status,
            elapsed_seconds=elapsed,
            response_text=None,
            interpretation=None,
            diagnostic=diag,
            usage=None,
            total_tokens_used=0,
            exception_type_chain=type(exc).__name__,
        )


async def run_groq_structured_intake(
    inference: AuthenticatedGroqInference,
    *,
    expected_user_id: str,
    expected_tenant_id: str,
    query: str = "My cotton crop in Warangal has leaf spot",
) -> GroqExperimentResult:
    """Send a structured intake request and validate output with Pydantic."""
    request = make_json_request(
        system_prompt=_INTAKE_PROMPT,
        payload={"query": query},
        response_model=IntakeInterpretation,
        agent_name="intake",
        max_output_tokens=1024,
    )
    model_id = inference.model_id or DEFAULT_MODEL_ID
    start_time = time.monotonic()
    try:
        result = await call_structured(
            inference,
            request,
            expected_user_id=expected_user_id,
            expected_tenant_id=expected_tenant_id,
            response_model=IntakeInterpretation,
        )
        elapsed = max(0.0, time.monotonic() - start_time)
        usage = inference.usage_records[-1] if inference.usage_records else None
        tokens = usage.actual_total_tokens if usage and usage.actual_total_tokens else 0
        return GroqExperimentResult(
            stage_name="2. Structured Intake Interpretation",
            model_id=model_id,
            success=True,
            http_status=usage.http_status if usage and usage.http_status else 200,
            elapsed_seconds=elapsed,
            response_text=None,
            interpretation=result,
            diagnostic=None,
            usage=usage,
            total_tokens_used=tokens,
            exception_type_chain=None,
        )
    except ProviderFailure as exc:
        elapsed = max(0.0, time.monotonic() - start_time)
        diag = exc.diagnostic
        http_status = getattr(diag, "http_status", None) if diag else None
        return GroqExperimentResult(
            stage_name="2. Structured Intake Interpretation",
            model_id=model_id,
            success=False,
            http_status=http_status,
            elapsed_seconds=elapsed,
            response_text=None,
            interpretation=None,
            diagnostic=diag,
            usage=None,
            total_tokens_used=0,
            exception_type_chain=getattr(diag, "exception_type_chain", None) if diag else None,
        )
    except Exception as exc:
        elapsed = max(0.0, time.monotonic() - start_time)
        diag = inference.diagnostics[-1] if inference.diagnostics else None
        http_status = getattr(diag, "http_status", None) if diag else None
        return GroqExperimentResult(
            stage_name="2. Structured Intake Interpretation",
            model_id=model_id,
            success=False,
            http_status=http_status,
            elapsed_seconds=elapsed,
            response_text=None,
            interpretation=None,
            diagnostic=diag,
            usage=None,
            total_tokens_used=0,
            exception_type_chain=type(exc).__name__,
        )


def _build_knowledge_base(database: Path, *, semantic: bool = False, model_cache: Path | None = None) -> PersistentKnowledgeBase:
    provider = (
        FastEmbedProvider(cache_dir=model_cache or ROOT / "tmp" / "models")
        if semantic
        else HashEmbeddingProvider()
    )
    processor = DocumentProcessor()
    documents = load_curated(ROOT / "data" / "agricultural" / "icar_advisories.json", CROP_REGISTRY)
    schemes = load_schemes(ROOT / "data" / "schemes" / "central_agricultural_schemes.json")
    chunks = [chunk for item in documents for chunk in processor.process_curated(item)]
    chunks.extend(chunk for item in schemes for chunk in processor.process_scheme(item))
    store = PersistentKnowledgeBase(database, provider)
    store.add(chunks)
    return store


async def run_groq_workflow(
    inference: AuthenticatedGroqInference,
    *,
    user_id: str,
    tenant_id: str,
    query: str = "My cotton crop in Adilabad has leaf spot",
    knowledge_db: Path | None = None,
    semantic: bool = False,
    model_cache: Path | None = None,
    weather_provider: WeatherProvider | None = None,
    market_provider: MarketProvider | None = None,
) -> GroqExperimentResult:
    """Execute the full six-agent advisory workflow with bounded Groq token budget."""
    rem_tokens = getattr(inference, "remaining_budget_tokens", None)
    if rem_tokens is not None and rem_tokens < 2500:
        return GroqExperimentResult(
            stage_name="3. Six-Agent Advisory Workflow",
            model_id=inference.model_id or DEFAULT_MODEL_ID,
            success=False,
            http_status=None,
            elapsed_seconds=0.0,
            response_text=None,
            workflow_response=None,
            diagnostic=InferenceDiagnostic(
                code=InferenceDiagnosticCode.GROQ_TPM_BUDGET_EXCEEDED,
                stage=InferenceStage.DISPATCH_PREFLIGHT,
                model_id=inference.model_id,
                budget_remaining_tokens=rem_tokens,
                budget_decision="rejected",
                retry_decision="budget_rejected",
            ),
            usage=None,
            total_tokens_used=0,
            exception_type_chain="LocalBudgetPreflightRejected",
        )

    kb_path = knowledge_db or (
        ROOT / "tmp" / ("phase4-advisory-fastembed.sqlite3" if semantic else "phase4-advisory-hash.sqlite3")
    )
    store = _build_knowledge_base(kb_path, semantic=semantic, model_cache=model_cache)
    workflow = RythuSaathiWorkflow(
        resolver=empty_coverage_resolver(),
        knowledge=store,
        weather_provider=weather_provider,
        market_provider=market_provider,
    )
    request = FarmerRequest(
        request_id=f"groq-smoke:{uuid.uuid4().hex[:12]}",
        user_id=user_id,
        tenant_id=tenant_id,
        query=query,
    )
    model_id = inference.model_id or DEFAULT_MODEL_ID
    start_time = time.monotonic()
    prior_usage_count = len(inference.usage_records)
    try:
        response = await workflow.run(
            request,
            mode=WorkflowMode.GROQ,
            inference=inference,
            on_progress=lambda msg: print(f"  [workflow progress] {msg}", flush=True),
        )
        elapsed = max(0.0, time.monotonic() - start_time)
        new_usages = inference.usage_records[prior_usage_count:]
        total_tokens = sum(u.actual_total_tokens or 0 for u in new_usages)
        latest_usage = new_usages[-1] if new_usages else None
        return GroqExperimentResult(
            stage_name="3. Six-Agent Advisory Workflow",
            model_id=model_id,
            success=True,
            http_status=200,
            elapsed_seconds=elapsed,
            response_text=response.response_text,
            workflow_response=response,
            diagnostic=None,
            usage=latest_usage,
            total_tokens_used=total_tokens,
            exception_type_chain=None,
        )
    except ProviderFailure as exc:
        elapsed = max(0.0, time.monotonic() - start_time)
        diag = exc.diagnostic
        http_status = getattr(diag, "http_status", None) if diag else None
        return GroqExperimentResult(
            stage_name="3. Six-Agent Advisory Workflow",
            model_id=model_id,
            success=False,
            http_status=http_status,
            elapsed_seconds=elapsed,
            response_text=None,
            workflow_response=None,
            diagnostic=diag,
            usage=None,
            total_tokens_used=0,
            exception_type_chain=getattr(diag, "exception_type_chain", None) if diag else None,
        )
    except Exception as exc:
        elapsed = max(0.0, time.monotonic() - start_time)
        diag = inference.diagnostics[-1] if inference.diagnostics else None
        http_status = getattr(diag, "http_status", None) if diag else None
        return GroqExperimentResult(
            stage_name="3. Six-Agent Advisory Workflow",
            model_id=model_id,
            success=False,
            http_status=http_status,
            elapsed_seconds=elapsed,
            response_text=None,
            workflow_response=None,
            diagnostic=diag,
            usage=None,
            total_tokens_used=0,
            exception_type_chain=type(exc).__name__,
        )


def print_groq_experiment_report(result: GroqExperimentResult) -> None:
    """Print structured, sanitized report for an individual Groq inference experiment."""
    print("-" * 60)
    print(f"Stage: {result.stage_name}")
    print("-" * 60)
    print(f"Model ID: {result.model_id}")
    print(f"Outcome: {'SUCCESS' if result.success else 'FAILURE'}")
    print(f"HTTP Status: {result.http_status if result.http_status is not None else 'none'}")
    print(f"Latency: {result.elapsed_seconds:.3f}s")
    if result.total_tokens_used:
        print(f"Total Tokens Used: {result.total_tokens_used}")
    if result.success:
        if result.response_text and not result.workflow_response:
            print(f"Response: {result.response_text.strip()!r}")
        if result.interpretation is not None:
            print("Pydantic Validation: PASSED")
            print(result.interpretation.model_dump_json(indent=2))
        if result.workflow_response is not None:
            resp = result.workflow_response
            status_val = getattr(getattr(resp, "status", None), "value", str(getattr(resp, "status", "unknown")))
            exec_mode = getattr(getattr(resp, "execution_mode", None), "value", str(getattr(resp, "execution_mode", "unknown")))
            provider = getattr(resp, "provider_id", None) or "none"
            model = getattr(resp, "model_id", None) or "none"
            print(f"Workflow Status: {status_val}")
            print(f"Execution Mode: {exec_mode} (provider: {provider}, model: {model})")

            resp_text = getattr(resp, "response_text", None)
            if resp_text:
                print(f"\nFarmer Advisory Response:\n{resp_text.strip()}")

            ctx = getattr(resp, "context", None)
            if ctx is not None:
                context_parts = []
                crop_fact = getattr(ctx, "crop", None)
                if crop_fact and getattr(crop_fact, "state", None) == FactState.SUPPLIED and getattr(crop_fact, "value", None):
                    crop_name = getattr(crop_fact.value, "display_name", str(crop_fact.value))
                    context_parts.append(f"Crop: {crop_name}")
                loc_fact = getattr(ctx, "location", None)
                if loc_fact and getattr(loc_fact, "state", None) == FactState.SUPPLIED and getattr(loc_fact, "value", None):
                    loc_name = getattr(loc_fact.value, "display_name", str(loc_fact.value))
                    context_parts.append(f"Location: {loc_name}")
                stage_fact = getattr(ctx, "growth_stage", None)
                if stage_fact and getattr(stage_fact, "state", None) == FactState.SUPPLIED and getattr(stage_fact, "value", None):
                    context_parts.append(f"Stage: {stage_fact.value}")
                if context_parts:
                    print(f"\nResolved Context: {'; '.join(context_parts)}")

            agent_results = getattr(resp, "agent_results", ()) or ()
            if agent_results:
                print(f"\nAgent Execution Results ({len(agent_results)} agents):")
                for ar in agent_results:
                    aid = getattr(getattr(ar, "agent_id", None), "value", str(getattr(ar, "agent_id", "unknown")))
                    astatus = getattr(getattr(ar, "status", None), "value", str(getattr(ar, "status", "unknown")))
                    duration = f" ({ar.duration_ms:.1f}ms)" if hasattr(ar, "duration_ms") else ""
                    details = []
                    payload = getattr(ar, "payload", None)
                    summary = None
                    if payload is not None:
                        mode = getattr(payload, "interpretation_mode", None)
                        if mode:
                            details.append(f"mode={mode}")
                        inf_status = getattr(payload, "inference_status", None)
                        if inf_status:
                            details.append(f"inference={inf_status}")
                        summary = getattr(payload, "note", None) or getattr(payload, "summary", None)
                    extra = f" [{', '.join(details)}]" if details else ""
                    ev_count = len(getattr(ar, "evidence_ids", ()) or ())
                    ev_info = f" [evidence={ev_count}]" if ev_count > 0 else ""
                    print(f"  - {aid}: {astatus}{duration}{extra}{ev_info}")
                    if summary:
                        clean_summary = str(summary).replace("\n", " ").strip()
                        if len(clean_summary) > 100:
                            clean_summary = clean_summary[:97] + "..."
                        print(f"      Summary: {clean_summary}")
                    if ar.error:
                        print(f"      Error: {ar.error.message}")

            recs = getattr(resp, "recommendations", ()) or ()
            if recs:
                print(f"\nRecommendations ({len(recs)}):")
                for idx, rec in enumerate(recs, 1):
                    cat = getattr(getattr(rec, "category", None), "value", str(getattr(rec, "category", "general")))
                    supp = getattr(getattr(rec, "support", None), "value", str(getattr(rec, "support", "unspecified")))
                    print(f"  [{idx}] ({cat}, {supp}) {rec.proposed_action}")
                    if getattr(rec, "reasoning_summary", None):
                        print(f"      Reasoning: {rec.reasoning_summary}")
                    if getattr(rec, "evidence_ids", ()):
                        print(f"      Citations: {', '.join(rec.evidence_ids)}")
                    if getattr(rec, "potential_risks", ()):
                        print(f"      Risks: {', '.join(rec.potential_risks)}")

            evidence = getattr(resp, "evidence", ()) or ()
            if evidence:
                print(f"\nEvidence Citations ({len(evidence)}):")
                for ev in evidence:
                    src = getattr(ev, "source", None)
                    title = getattr(src, "title", getattr(src, "source_identity", "Unknown Source"))
                    mode = getattr(ev, "mode", None)
                    excerpt = (getattr(ev, "excerpt", None) or "").replace("\n", " ").strip()
                    if len(excerpt) > 120:
                        excerpt = excerpt[:117] + "..."
                    print(f"  - [{ev.evidence_id}] {title}")
                    if mode:
                        print(f"      Mode: {mode.value}")
                    if excerpt:
                        print(f"      Excerpt: {excerpt}")

            missing = getattr(resp, "missing_information", ()) or ()
            if missing:
                print(f"\nMissing Information / Follow-up Questions ({len(missing)}):")
                for item in missing:
                    print(f"  - {item}")

            conflicts = getattr(resp, "conflicts", ()) or ()
            if conflicts:
                print(f"\nConflicts / Warnings ({len(conflicts)}):")
                for c in conflicts:
                    print(f"  - {c}")
    else:
        if result.diagnostic:
            diag = result.diagnostic
            code_str = getattr(diag.code, "value", str(diag.code)) if diag.code else "none"
            stage_str = getattr(diag.stage, "value", str(diag.stage)) if diag.stage else "none"
            print(f"Diagnostic Code: {code_str}")
            print(f"Diagnostic Stage: {stage_str}")
            if diag.exception_type_chain:
                print(f"Exception Type Chain: {diag.exception_type_chain}")
            if diag.rate_limit_scope:
                print(f"Rate Limit Scope: {diag.rate_limit_scope.value}")
            if diag.retry_after_seconds is not None:
                print(f"Retry-After: {diag.retry_after_seconds:.1f}s")
            print(f"Retry Decision: {diag.retry_decision or 'unknown'}")
            print(f"Budget Decision: {diag.budget_decision or 'unknown'}")
            if diag.budget_remaining_tokens is not None:
                print(f"Budget Remaining Tokens: {diag.budget_remaining_tokens}")
        elif result.exception_type_chain:
            print(f"Exception Type Chain: {result.exception_type_chain}")


async def async_main(args: argparse.Namespace) -> int:
    auth_db = args.database or (ROOT / "tmp" / "phase5-local-auth.sqlite3")
    try:
        inference, user_id, tenant_id, token, auth = setup_authenticated_groq_inference(
            auth_db,
            tenant_id=args.tenant_id,
            username=args.username,
            password=args.password,
            credential_id=args.credential_id,
            timeout_seconds=args.timeout,
            model_id=args.model_id,
        )
    except Exception as exc:
        print(f"Setup/Credential lookup failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 2

    try:
        mode = getattr(args, "mode", "all")
        results: list[GroqExperimentResult] = []

        print("=" * 60)
        print("RythuSaathi — Groq Live Demo Inference Runner")
        print("=" * 60)
        print(f"Model ID: {args.model_id or DEFAULT_MODEL_ID}")
        print("Groq Free-Tier Limits: TPM=8,000 (Headroom=800), RPM=30, RPD=14,400")
        print("=" * 60)

        # Stage 1: Minimal probe
        if mode in ("all", "minimal"):
            min_res = await run_groq_minimal_probe(
                inference,
                expected_user_id=user_id,
                expected_tenant_id=tenant_id,
            )
            results.append(min_res)
            print_groq_experiment_report(min_res)
            if not min_res.success:
                print("\n[STOP] Stage 1 failed. Aborting subsequent stages.")
                return 1

        # Stage 2: Structured Intake
        if mode in ("all", "intake"):
            intake_res = await run_groq_structured_intake(
                inference,
                expected_user_id=user_id,
                expected_tenant_id=tenant_id,
                query=args.query,
            )
            results.append(intake_res)
            print_groq_experiment_report(intake_res)
            if not intake_res.success:
                print("\n[STOP] Stage 2 failed. Aborting subsequent stages.")
                return 1

        # Stage 3: Six-agent advisory workflow
        if mode in ("all", "workflow"):
            weather_prov = OpenMeteoWeatherProvider()
            store = WindowsCredentialStore()
            cred_service = CredentialService(auth_db, store)
            identity = auth.authenticate(token)
            mandi_statuses = cred_service.list_status(identity, provider_id="data.gov.in")
            active_mandi = [s for s in mandi_statuses if s.state.value == "active"]
            market_prov = None
            if active_mandi:
                market_prov = DataGovInMarketProvider(
                    credential_service=cred_service,
                    identity=identity,
                    credential_id=active_mandi[0].credential_id,
                )
                print("[Workflow Providers] Weather: Open-Meteo (live), Market: data.gov.in (BYOK live)")
            else:
                print("[Workflow Providers] Weather: Open-Meteo (live), Market: unavailable")

            wf_res = await run_groq_workflow(
                inference,
                user_id=user_id,
                tenant_id=tenant_id,
                query=args.query,
                semantic=args.semantic,
                model_cache=args.model_cache,
                weather_provider=weather_prov,
                market_provider=market_prov,
            )
            results.append(wf_res)
            print_groq_experiment_report(wf_res)
            if not wf_res.success:
                print("\n[STOP] Stage 3 failed.")
                return 1

        print("=" * 60)
        total_tokens = sum(r.total_tokens_used for r in results)
        print(f"All executed stages PASSED. Cumulative tokens used: {total_tokens}")
        print("=" * 60)
        return 0
    finally:
        auth.logout(token)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Isolated smoke test and demo runner for Groq inference"
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help="Path to SQLite auth database (defaults to tmp/phase5-local-auth.sqlite3)",
    )
    parser.add_argument("--credential-id", type=str, default=None, help="BYOK credential ID (optional)")
    parser.add_argument("--tenant-id", type=str, default=None, help="Tenant ID (optional)")
    parser.add_argument("--username", type=str, default=None, help="Username (optional)")
    parser.add_argument("--password", type=str, default=None, help="Password (optional)")
    parser.add_argument(
        "--query",
        type=str,
        default="My cotton crop in Adilabad has leaf spot",
        help="Farmer query for intake structured test and workflow (default: My cotton crop in Adilabad has leaf spot)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Bounded timeout in seconds per attempt (default: 30.0)",
    )
    parser.add_argument(
        "--model-id",
        type=str,
        default=None,
        help="Groq model identifier override (defaults to GROQ_MODEL_ID or openai/gpt-oss-20b)",
    )
    parser.add_argument(
        "--mode",
        choices=["all", "minimal", "intake", "workflow"],
        default="all",
        help="Experiment mode: all (run minimal, intake, workflow sequentially), minimal, intake, or workflow. Default: all.",
    )
    parser.add_argument(
        "--semantic",
        action="store_true",
        help="Use FastEmbed semantic embeddings instead of deterministic lexical hash",
    )
    parser.add_argument(
        "--model-cache",
        type=Path,
        default=None,
        help="FastEmbed model cache directory (defaults to tmp/models)",
    )
    args = parser.parse_args()
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())


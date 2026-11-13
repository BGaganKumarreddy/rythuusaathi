"""Advisory consultation routes: execute the seven-agent workflow, inspect traces, and retrieve consultations."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from ...agents import (
    AuthenticatedCerebrasInference,
    AuthenticatedGeminiInference,
    AuthenticatedGroqInference,
    DataGovInMarketProvider,
    FarmerRequest,
    OpenMeteoWeatherProvider,
    RythuSaathiWorkflow,
    default_coverage_resolver,
)
from ...agents.contracts import AdvisoryStatus, AgentId, EvidenceRecord, WorkflowMode
from ...credentials import CredentialService
from ...inference.providers.cerebras import CerebrasProvider, CerebrasProviderConfig
from ...inference.providers.gemini import GeminiProvider, GeminiProviderConfig
from ...inference.providers.groq import GroqProvider, GroqProviderConfig
from ...inference.service import (
    AuthenticatedCerebrasService,
    AuthenticatedGeminiService,
    AuthenticatedGroqService,
)
from ...knowledge import LocalKnowledgeBase
from ...local_auth import LocalAuthentication
from ...security import VerifiedIdentity
from ..dependencies import (
    extract_bearer_token,
    get_auth_service,
    get_credential_service,
    get_current_identity,
    get_history_store,
    get_knowledge_base,
)
from ..exceptions import APIError
from ..history import AdvisoryHistoryStore
from ..schemas.advisory import (
    AdvisoryCreateRequest,
    AdvisoryResponseSchema,
    AdvisoryTraceResponse,
    AgentResultSchema,
    EvidenceItemSchema,
    RiskAlertSchema,
)
from ..schemas.history import HistoryListResponse

router = APIRouter(prefix="/advisories", tags=["Advisory"])


def _to_evidence_schema(ev: EvidenceRecord) -> EvidenceItemSchema:
    ref_val = None
    if ev.source and ev.source.reference:
        ref_val = ev.source.reference.value
    return EvidenceItemSchema(
        evidence_id=ev.evidence_id,
        source_id=ev.source.source_id if ev.source else "unknown",
        title=ev.source.title if ev.source else "Untitled Evidence",
        reference_url=ref_val,
        source_type=ev.source.source_type.value if (ev.source and hasattr(ev.source.source_type, "value")) else "external_data",
        excerpt=ev.excerpt,
        provenance=ev.provenance,
        mode=ev.mode.value if (ev.mode and hasattr(ev.mode, "value")) else str(ev.mode),
        units=ev.units,
        crop_applicability=list(ev.crop_applicability or ()),
        geographic_applicability=list(ev.geographic_applicability or ()),
        retrieved_at=ev.retrieved_at,
    )


def _extract_next_steps(response_text: str) -> list[str]:
    """Parse out individual items under Suggested next steps: section."""
    if "Suggested next steps:" not in response_text:
        return []
    section = response_text.split("Suggested next steps:")[1]
    lines = section.strip().split("\n")
    steps: list[str] = []
    for line in lines:
        line_clean = line.strip()
        if not line_clean or line_clean.startswith("This is a ") or line_clean.startswith("Understanding:"):
            break
        if line_clean.startswith("- "):
            steps.append(line_clean[2:].strip())
        elif line_clean.startswith("* "):
            steps.append(line_clean[2:].strip())
    return steps


@router.post(
    "",
    response_model=AdvisoryResponseSchema,
    status_code=status.HTTP_201_CREATED,
    summary="Submit an agronomic inquiry and execute the seven-agent workflow",
)
async def create_advisory(
    req: AdvisoryCreateRequest,
    identity: Annotated[VerifiedIdentity, Depends(get_current_identity)],
    token: Annotated[str, Depends(extract_bearer_token)],
    auth_service: Annotated[LocalAuthentication, Depends(get_auth_service)],
    credential_service: Annotated[CredentialService, Depends(get_credential_service)],
    history_store: Annotated[AdvisoryHistoryStore, Depends(get_history_store)],
    knowledge: Annotated[LocalKnowledgeBase, Depends(get_knowledge_base)],
) -> AdvisoryResponseSchema:
    inference = None

    if req.mode in {WorkflowMode.CEREBRAS, WorkflowMode.GEMINI, WorkflowMode.GROQ}:
        prov_key = req.mode.value
        statuses = credential_service.list_status(identity, provider_id=prov_key)
        active_creds = [s for s in statuses if s.state.value == "active"]

        cid = req.credential_id
        if cid:
            valid_cids = {s.credential_id for s in active_creds}
            if cid not in valid_cids:
                raise APIError(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    code="invalid_credential_id",
                    message=f"Credential ID '{cid}' is not active or not authorized for provider '{prov_key}'",
                )
        elif active_creds:
            cid = active_creds[0].credential_id
        else:
            raise APIError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="missing_provider_credential",
                message=f"No active BYOK credential found for provider '{prov_key}'. Please add one via /api/v1/credentials.",
            )

        if req.mode is WorkflowMode.CEREBRAS:
            provider = CerebrasProvider(CerebrasProviderConfig.from_environment())
            service = AuthenticatedCerebrasService(auth_service, credential_service, provider)
            inference = AuthenticatedCerebrasInference(service, token, cid)
        elif req.mode is WorkflowMode.GEMINI:
            provider = GeminiProvider(GeminiProviderConfig.from_environment())
            service = AuthenticatedGeminiService(auth_service, credential_service, provider)
            inference = AuthenticatedGeminiInference(service, token, cid)
        elif req.mode is WorkflowMode.GROQ:
            provider = GroqProvider(GroqProviderConfig.from_environment())
            service = AuthenticatedGroqService(auth_service, credential_service, provider)
            inference = AuthenticatedGroqInference(service, token, cid)

    # Optional Mandi Market provider if user has active data.gov.in key
    market_provider = None
    mandi_statuses = credential_service.list_status(identity, provider_id="data.gov.in")
    mandi_active = [s for s in mandi_statuses if s.state.value == "active"]
    if mandi_active:
        market_provider = DataGovInMarketProvider(
            credential_service=credential_service,
            identity=identity,
            credential_id=mandi_active[0].credential_id,
        )

    weather_provider = OpenMeteoWeatherProvider()

    workflow = RythuSaathiWorkflow(
        resolver=default_coverage_resolver(),
        knowledge=knowledge,
        weather_provider=weather_provider,
        market_provider=market_provider,
    )

    request_id = f"api:{uuid.uuid4().hex}"
    farmer_request = FarmerRequest(
        request_id=request_id,
        user_id=identity.subject,
        tenant_id=identity.tenant_id,
        query=req.query,
        crop_query=req.crop,
        location_query=req.location,
        growth_stage_query=req.growth_stage,
        variety_query=req.variety,
    )

    now = datetime.now(timezone.utc)
    try:
        response = await workflow.run(farmer_request, mode=req.mode, inference=inference)
    except Exception as exc:
        raise APIError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="workflow_execution_failed",
            message=f"Seven-agent workflow execution encountered an error: {type(exc).__name__}",
        )

    # Transform structured agent results
    agent_results: list[AgentResultSchema] = []
    active_risk_alerts: list[RiskAlertSchema] = []
    background_vulnerabilities: list[RiskAlertSchema] = []

    for ar in response.agent_results:
        summary_val = getattr(ar.payload, "summary", None) if ar.payload else None
        agent_results.append(
            AgentResultSchema(
                agent_id=ar.agent_id.value,
                status=ar.status.value,
                duration_ms=ar.duration_ms,
                summary=summary_val,
                error=ar.error.message if ar.error else None,
                provider_id=ar.provider_id,
                model_id=ar.model_id,
            )
        )

        if ar.agent_id == AgentId.RISK_ALERT and ar.payload is not None:
            raw_alerts = getattr(ar.payload, "alerts", ())
            for a in raw_alerts:
                active_risk_alerts.append(
                    RiskAlertSchema(
                        finding_id=a.finding_id,
                        category=a.category.value if hasattr(a.category, "value") else str(a.category),
                        title=a.title,
                        severity=a.severity.value if hasattr(a.severity, "value") else str(a.severity),
                        explanation=a.explanation,
                        safe_action=a.safe_action,
                        evidence_ids=list(a.evidence_ids),
                        is_active=getattr(a, "is_active", True),
                        confidence=getattr(a, "confidence", None),
                        crop_id=getattr(a, "crop_id", None),
                        location_id=getattr(a, "location_id", None),
                    )
                )

            raw_bg = getattr(ar.payload, "background_vulnerabilities", ())
            for bg in raw_bg:
                background_vulnerabilities.append(
                    RiskAlertSchema(
                        finding_id=bg.finding_id,
                        category=bg.category.value if hasattr(bg.category, "value") else str(bg.category),
                        title=bg.title,
                        severity=bg.severity.value if hasattr(bg.severity, "value") else str(bg.severity),
                        explanation=bg.explanation,
                        safe_action=bg.safe_action,
                        evidence_ids=list(bg.evidence_ids),
                        is_active=False,
                        confidence=getattr(bg, "confidence", None),
                        crop_id=getattr(bg, "crop_id", None),
                        location_id=getattr(bg, "location_id", None),
                    )
                )

    # Categorize evidence using agent evidence_ids and source identities
    all_evidence = [_to_evidence_schema(e) for e in response.evidence]
    weather_ev: list[EvidenceItemSchema] = []
    market_ev: list[EvidenceItemSchema] = []
    scheme_ev: list[EvidenceItemSchema] = []

    weather_ids = set()
    market_ids = set()
    scheme_ids = set()
    for ar in response.agent_results:
        if ar.agent_id == AgentId.WEATHER:
            weather_ids.update(ar.evidence_ids)
        elif ar.agent_id == AgentId.MARKET:
            market_ids.update(ar.evidence_ids)
        elif ar.agent_id == AgentId.SCHEME:
            scheme_ids.update(ar.evidence_ids)

    for ev_item in all_evidence:
        sid = ev_item.source_id.lower()
        if ev_item.evidence_id in weather_ids or "open-meteo" in sid or "weather" in sid:
            weather_ev.append(ev_item)
        elif ev_item.evidence_id in market_ids or "data-gov-in" in sid or "market" in sid:
            market_ev.append(ev_item)
        elif (
            ev_item.evidence_id in scheme_ids
            or "pm-kisan" in sid
            or "pmfby" in sid
            or "scheme" in sid
            or "bharosa" in sid
            or "rythu" in sid
        ):
            scheme_ev.append(ev_item)

    next_steps = _extract_next_steps(response.response_text)

    advisory_schema = AdvisoryResponseSchema(
        advisory_id=response.request_id,
        status=response.status,
        execution_mode=response.execution_mode,
        provider_id=response.provider_id,
        model_id=response.model_id,
        coordinator_synthesis=response.response_text,
        agent_results=agent_results,
        weather_evidence=weather_ev,
        market_evidence=market_ev,
        scheme_evidence=scheme_ev,
        active_risk_alerts=active_risk_alerts,
        background_vulnerabilities=background_vulnerabilities,
        all_evidence=all_evidence,
        uncertainty_and_limitations=list(response.missing_information),
        suggested_next_steps=next_steps,
        created_at=now,
    )

    trace_schema = AdvisoryTraceResponse(
        advisory_id=response.request_id,
        status=response.status.value,
        findings=[
            {
                "finding_id": f.finding_id,
                "agent_id": f.agent_id.value,
                "topic": f.topic,
                "claim": f.claim,
                "evidence_ids": list(f.evidence_ids),
                "uncertainty": f.uncertainty,
            }
            for f in response.findings
        ],
        conflicts=list(response.conflicts),
        missing_information=list(response.missing_information),
        agent_executions=[
            {
                "agent_id": ar.agent_id.value,
                "status": ar.status.value,
                "duration_ms": ar.duration_ms,
                "evidence_ids": list(ar.evidence_ids),
                "finding_ids": list(ar.finding_ids),
                "provider_id": ar.provider_id,
                "model_id": ar.model_id,
            }
            for ar in response.agent_results
        ],
        board_state=response.board_state.value if hasattr(response.board_state, "value") else str(response.board_state),
    )

    # Persist in SQLite history store
    history_store.save(
        advisory=advisory_schema,
        user_id=identity.subject,
        tenant_id=identity.tenant_id,
        query=req.query,
        crop=req.crop,
        location=req.location,
        growth_stage=req.growth_stage,
        variety=req.variety,
        trace=trace_schema,
    )

    return advisory_schema


@router.get(
    "/{advisory_id}",
    response_model=AdvisoryResponseSchema,
    summary="Retrieve an owned historical advisory consultation by ID",
)
async def get_advisory(
    advisory_id: str,
    identity: Annotated[VerifiedIdentity, Depends(get_current_identity)],
    history_store: Annotated[AdvisoryHistoryStore, Depends(get_history_store)],
) -> AdvisoryResponseSchema:
    advisory = history_store.get(advisory_id, identity)
    if advisory is None:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="advisory_not_found",
            message=f"Advisory consultation '{advisory_id}' was not found or is not authorized",
        )
    return advisory


@router.get(
    "/{advisory_id}/trace",
    response_model=AdvisoryTraceResponse,
    summary="Retrieve execution diagnostic trace and board findings for an advisory",
)
async def get_advisory_trace(
    advisory_id: str,
    identity: Annotated[VerifiedIdentity, Depends(get_current_identity)],
    history_store: Annotated[AdvisoryHistoryStore, Depends(get_history_store)],
) -> AdvisoryTraceResponse:
    trace = history_store.get_trace(advisory_id, identity)
    if trace is None:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="advisory_trace_not_found",
            message=f"Diagnostic trace for advisory '{advisory_id}' was not found or is not authorized",
        )
    return trace


@router.get(
    "",
    response_model=HistoryListResponse,
    summary="List past advisory consultations for the authenticated user and tenant",
)
async def list_advisories(
    identity: Annotated[VerifiedIdentity, Depends(get_current_identity)],
    history_store: Annotated[AdvisoryHistoryStore, Depends(get_history_store)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    crop: Annotated[str | None, Query()] = None,
) -> HistoryListResponse:
    items, total = history_store.list(identity, limit=limit, offset=offset, crop=crop)
    return HistoryListResponse(items=items, total=total, limit=limit, offset=offset)


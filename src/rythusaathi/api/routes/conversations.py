"""FastAPI routes for persistent conversational consultations and follow-up interaction."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from ...agents import (
    DataGovInMarketProvider,
    FarmerRequest,
    OpenMeteoWeatherProvider,
    RythuSaathiWorkflow,
    default_coverage_resolver,
)
from ...agents.contracts import WorkflowMode
from ...domain import AgentId
from ...credentials import CredentialService
from ...knowledge import LocalKnowledgeBase
from ...local_auth import LocalAuthentication
from ...security import VerifiedIdentity
from ..conversations import ConversationStore, resolve_followup_context
from ..dependencies import (
    extract_bearer_token,
    get_auth_service,
    get_conversation_store,
    get_credential_service,
    get_current_identity,
    get_history_store,
    get_knowledge_base,
)
from ..exceptions import APIError
from ..history import AdvisoryHistoryStore
from ..schemas.advisory import (
    AdvisoryResponseSchema,
    AgentResultSchema,
    EvidenceItemSchema,
    RiskAlertSchema,
)
from ..schemas.common import SuccessResponse
from ..schemas.conversations import (
    ContextStateSchema,
    ConversationDetailSchema,
    ConversationListResponse,
    ConversationMessageSchema,
    ConversationSummarySchema,
    CreateConversationRequest,
    SendMessageRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["Conversations"])


def _to_evidence_schema(ev) -> EvidenceItemSchema:
    ref_val = None
    if ev.source and ev.source.reference:
        ref_val = ev.source.reference.value
    elif ev.content_reference and hasattr(ev.content_reference, "value"):
        ref_val = str(ev.content_reference.value)
    elif ev.content_reference:
        ref_val = str(ev.content_reference)

    return EvidenceItemSchema(
        evidence_id=ev.evidence_id,
        source_id=ev.source.source_id if ev.source else "unknown",
        title=ev.source.title if ev.source else "Untitled Evidence",
        reference_url=ref_val,
        source_type=ev.source.source_type.value if (ev.source and hasattr(ev.source.source_type, "value")) else "external_data",
        excerpt=ev.excerpt or "",
        provenance=ev.provenance,
        mode=ev.mode.value if (ev.mode and hasattr(ev.mode, "value")) else str(getattr(ev, "mode", "unknown")),
        units=getattr(ev, "units", None),
        crop_applicability=list(getattr(ev, "crop_applicability", None) or ()),
        geographic_applicability=list(getattr(ev, "geographic_applicability", None) or ()),
        retrieved_at=ev.retrieved_at,
    )


async def _execute_advisory_for_conversation(
    *,
    user_query: str,
    context: ContextStateSchema,
    mode: WorkflowMode,
    identity: VerifiedIdentity,
    auth_service: LocalAuthentication,
    credential_service: CredentialService,
    knowledge: LocalKnowledgeBase,
    history_store: AdvisoryHistoryStore,
    token: str,
) -> tuple[AdvisoryResponseSchema, str]:
    """Execute the seven-agent advisory workflow with accumulated conversation context."""
    # Build provider-neutral inference if BYOK key is configured
    inference = None
    if mode in {WorkflowMode.CEREBRAS, WorkflowMode.GEMINI, WorkflowMode.GROQ}:
        from ...inference.contracts import AuthenticatedCerebrasInference, AuthenticatedGeminiInference, AuthenticatedGroqInference
        from ...inference.providers.cerebras import CerebrasProvider, CerebrasProviderConfig
        from ...inference.providers.gemini import GeminiProvider, GeminiProviderConfig
        from ...inference.providers.groq import GroqProvider, GroqProviderConfig
        from ...inference.service import AuthenticatedCerebrasService, AuthenticatedGeminiService, AuthenticatedGroqService

        cid = f"cid:{uuid.uuid4().hex}"
        if mode is WorkflowMode.CEREBRAS:
            provider = CerebrasProvider(CerebrasProviderConfig.from_environment())
            service = AuthenticatedCerebrasService(auth_service, credential_service, provider)
            inference = AuthenticatedCerebrasInference(service, token, cid)
        elif mode is WorkflowMode.GEMINI:
            provider = GeminiProvider(GeminiProviderConfig.from_environment())
            service = AuthenticatedGeminiService(auth_service, credential_service, provider)
            inference = AuthenticatedGeminiInference(service, token, cid)
        elif mode is WorkflowMode.GROQ:
            provider = GroqProvider(GroqProviderConfig.from_environment())
            service = AuthenticatedGroqService(auth_service, credential_service, provider)
            inference = AuthenticatedGroqInference(service, token, cid)

    # Optional mandi provider if key enrolled
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

    request_id = f"api:conv:{uuid.uuid4().hex}"
    farmer_request = FarmerRequest(
        request_id=request_id,
        user_id=identity.subject,
        tenant_id=identity.tenant_id,
        query=user_query,
        crop_query=context.crop,
        location_query=context.location,
        growth_stage_query=context.growth_stage,
        variety_query=context.variety,
    )

    now = datetime.now(timezone.utc)
    try:
        response = await workflow.run(farmer_request, mode=mode, inference=inference)
    except Exception as exc:
        logger.exception("Workflow execution error in conversation turn: %s", exc)
        raise APIError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="workflow_execution_failed",
            message=f"Seven-agent workflow execution error: {type(exc).__name__}",
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

    # Format next steps
    next_steps = [item if isinstance(item, str) else getattr(item, "question", str(item)) for item in response.missing_information] if response.missing_information else []
    if not next_steps:
        next_steps = ["Follow recommended agricultural practices and monitor field conditions daily."]

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

    # Also persist to regular advisory history store
    history_store.save(
        advisory=advisory_schema,
        user_id=identity.subject,
        tenant_id=identity.tenant_id,
        query=user_query,
        crop=context.crop,
        location=context.location,
        growth_stage=context.growth_stage,
        variety=context.variety,
    )

    return advisory_schema, response.response_text


@router.post(
    "",
    response_model=ConversationDetailSchema,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new conversation session",
)
async def create_conversation(
    req: CreateConversationRequest,
    identity: Annotated[VerifiedIdentity, Depends(get_current_identity)],
    conv_store: Annotated[ConversationStore, Depends(get_conversation_store)],
    auth_service: Annotated[LocalAuthentication, Depends(get_auth_service)],
    credential_service: Annotated[CredentialService, Depends(get_credential_service)],
    knowledge: Annotated[LocalKnowledgeBase, Depends(get_knowledge_base)],
    history_store: Annotated[AdvisoryHistoryStore, Depends(get_history_store)],
    token: Annotated[str, Depends(extract_bearer_token)],
) -> ConversationDetailSchema:
    initial_ctx = ContextStateSchema()
    if req.crop:
        initial_ctx.crop = req.crop
        initial_ctx.confirmed_fields.append("crop")
    if req.location:
        initial_ctx.location = req.location
        initial_ctx.confirmed_fields.append("location")
    if req.growth_stage:
        initial_ctx.growth_stage = req.growth_stage
        initial_ctx.confirmed_fields.append("growth_stage")
    if req.variety:
        initial_ctx.variety = req.variety
        initial_ctx.confirmed_fields.append("variety")

    title = req.title
    if not title and req.initial_message:
        title = (req.initial_message[:47] + "...") if len(req.initial_message) > 50 else req.initial_message

    conv = conv_store.create_conversation(
        identity=identity,
        title=title or "New Consultation",
        context=initial_ctx,
    )

    if req.initial_message:
        # Resolve follow-up context and run workflow
        updated_ctx, intent = resolve_followup_context(
            conv.context,
            req.initial_message,
            {
                "crop": req.crop,
                "location": req.location,
                "growth_stage": req.growth_stage,
                "variety": req.variety,
            },
        )
        conv_store.update_context(conv.conversation_id, identity, updated_ctx)

        # Add user message
        user_msg = conv_store.add_message(
            conversation_id=conv.conversation_id,
            identity=identity,
            role="user",
            content=req.initial_message,
            intent=intent,
        )

        # Run 7-agent advisory workflow
        adv_schema, synthesis_text = await _execute_advisory_for_conversation(
            user_query=req.initial_message,
            context=updated_ctx,
            mode=req.mode,
            identity=identity,
            auth_service=auth_service,
            credential_service=credential_service,
            knowledge=knowledge,
            history_store=history_store,
            token=token,
        )

        # Add assistant message
        asst_msg = conv_store.add_message(
            conversation_id=conv.conversation_id,
            identity=identity,
            role="assistant",
            content=synthesis_text,
            intent="advisory_synthesis",
            advisory_id=adv_schema.advisory_id,
            agent_telemetry=adv_schema.agent_results,
            weather_evidence=adv_schema.weather_evidence,
            market_evidence=adv_schema.market_evidence,
            scheme_evidence=adv_schema.scheme_evidence,
            active_risk_alerts=adv_schema.active_risk_alerts,
            uncertainty_and_limitations=adv_schema.uncertainty_and_limitations,
            suggested_next_steps=adv_schema.suggested_next_steps,
        )

        conv = conv_store.get_conversation(conv.conversation_id, identity)
        if conv is None:
            raise APIError(status_code=500, code="conversation_error", message="Failed to load created conversation")
        return conv

    return conv


@router.get(
    "",
    response_model=ConversationListResponse,
    summary="List authenticated user's conversations with owner and tenant isolation",
)
async def list_conversations(
    identity: Annotated[VerifiedIdentity, Depends(get_current_identity)],
    conv_store: Annotated[ConversationStore, Depends(get_conversation_store)],
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ConversationListResponse:
    items, total = conv_store.list_conversations(identity, limit=limit, offset=offset)
    return ConversationListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{conversation_id}",
    response_model=ConversationDetailSchema,
    summary="Retrieve an owned conversation by ID",
)
async def get_conversation(
    conversation_id: str,
    identity: Annotated[VerifiedIdentity, Depends(get_current_identity)],
    conv_store: Annotated[ConversationStore, Depends(get_conversation_store)],
) -> ConversationDetailSchema:
    conv = conv_store.get_conversation(conversation_id, identity)
    if conv is None:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="conversation_not_found",
            message=f"Conversation {conversation_id} not found or unauthorized.",
        )
    return conv


@router.post(
    "/{conversation_id}/messages",
    response_model=ConversationMessageSchema,
    status_code=status.HTTP_201_CREATED,
    summary="Send a follow-up message in an existing conversation",
)
async def send_message(
    conversation_id: str,
    req: SendMessageRequest,
    identity: Annotated[VerifiedIdentity, Depends(get_current_identity)],
    conv_store: Annotated[ConversationStore, Depends(get_conversation_store)],
    auth_service: Annotated[LocalAuthentication, Depends(get_auth_service)],
    credential_service: Annotated[CredentialService, Depends(get_credential_service)],
    knowledge: Annotated[LocalKnowledgeBase, Depends(get_knowledge_base)],
    history_store: Annotated[AdvisoryHistoryStore, Depends(get_history_store)],
    token: Annotated[str, Depends(extract_bearer_token)],
) -> ConversationMessageSchema:
    conv = conv_store.get_conversation(conversation_id, identity)
    if conv is None:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="conversation_not_found",
            message=f"Conversation {conversation_id} not found or unauthorized.",
        )

    # 1. Resolve follow-up context with explicit precedence rules
    updated_ctx, intent = resolve_followup_context(
        conv.context,
        req.content,
        {
            "crop": req.crop,
            "location": req.location,
            "growth_stage": req.growth_stage,
            "variety": req.variety,
        },
    )

    # Update conversation's persistent context
    conv_store.update_context(conversation_id, identity, updated_ctx)

    # 2. Append user follow-up message
    conv_store.add_message(
        conversation_id=conversation_id,
        identity=identity,
        role="user",
        content=req.content,
        intent=intent,
    )

    # 3. Execute seven-agent workflow with accumulated context
    adv_schema, synthesis_text = await _execute_advisory_for_conversation(
        user_query=req.content,
        context=updated_ctx,
        mode=req.mode,
        identity=identity,
        auth_service=auth_service,
        credential_service=credential_service,
        knowledge=knowledge,
        history_store=history_store,
        token=token,
    )

    # 4. Append assistant response message with telemetry and evidence
    asst_msg = conv_store.add_message(
        conversation_id=conversation_id,
        identity=identity,
        role="assistant",
        content=synthesis_text,
        intent="advisory_synthesis",
        advisory_id=adv_schema.advisory_id,
        agent_telemetry=adv_schema.agent_results,
        weather_evidence=adv_schema.weather_evidence,
        market_evidence=adv_schema.market_evidence,
        scheme_evidence=adv_schema.scheme_evidence,
        active_risk_alerts=adv_schema.active_risk_alerts,
        uncertainty_and_limitations=adv_schema.uncertainty_and_limitations,
        suggested_next_steps=adv_schema.suggested_next_steps,
    )

    return asst_msg


@router.delete(
    "/{conversation_id}",
    response_model=SuccessResponse,
    summary="Delete an owned conversation thread",
)
async def delete_conversation(
    conversation_id: str,
    identity: Annotated[VerifiedIdentity, Depends(get_current_identity)],
    conv_store: Annotated[ConversationStore, Depends(get_conversation_store)],
) -> SuccessResponse:
    success = conv_store.delete_conversation(conversation_id, identity)
    if not success:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="conversation_not_found",
            message=f"Conversation {conversation_id} not found or unauthorized.",
        )
    return SuccessResponse(success=True)

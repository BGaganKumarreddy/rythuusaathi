"""Phase 10.2 Regression Tests for AgroSage minimal root-cause fixes.

Covers:
1. Crop retrieval without location succeeds and returns ICAR guidance.
2. Nationwide ICAR retrieval with geography filter (location:country:in matches district/state).
3. State-specific retrieval with matching state vs mismatching state.
4. Unknown geographic metadata (None/empty) rejected when geography filter requested.
5. Central scheme retrieval without location when explicitly requested.
6. Non-scheme question produces accurate skip reason.
7. General risk vulnerability assessment without location succeeds.
8. No unsupported active risk alert issued when location or triggers missing.
9. LLM mode with a valid owner-scoped credential resolves real credential ID.
10. LLM mode without credential fails cleanly with HTTP 400 missing_provider_credential.
11. No synthetic credential UUID generated in conversation dispatch.
12. Deterministic mode is accurately identified in execution mode.
13. API preserves retrieval payload notes and skip reasons in AgentResultSchema.summary.
"""

from __future__ import annotations

from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from rythusaathi.agents import (
    RythuSaathiWorkflow,
    default_coverage_resolver,
    FarmerRequest,
)
from rythusaathi.agents.contracts import AgentStatus, WorkflowMode
from rythusaathi.domain import AgentId
from rythusaathi.knowledge.retrieval import _geography_applies as _local_geography_applies
from rythusaathi.knowledge.persistent import _geography_applies as _persistent_geography_applies
from rythusaathi.api.app import create_app
from rythusaathi.api.conversations import ConversationStore
from rythusaathi.api.dependencies import (
    set_auth_service,
    set_conversation_store,
    set_credential_service,
    set_history_store,
    set_key_store,
    set_knowledge_base,
    InMemoryKeyStore,
)
from rythusaathi.api.history import AdvisoryHistoryStore
from rythusaathi.domain.seed import CROP_REGISTRY
from rythusaathi.local_auth import LocalAuthentication
from rythusaathi.credentials import CredentialService
from rythusaathi.knowledge import (
    LocalKnowledgeBase,
    HashEmbeddingProvider,
    DocumentProcessor,
    load_curated,
    load_schemes,
)


@pytest.fixture
def env(tmp_path: Path):
    auth_db = tmp_path / "auth.sqlite3"
    hist_db = tmp_path / "history.sqlite3"

    auth_service = LocalAuthentication(auth_db)
    key_store = InMemoryKeyStore()
    cred_service = CredentialService(auth_db, key_store)
    hist_store = AdvisoryHistoryStore(hist_db)
    conv_store = ConversationStore(hist_db)

    processor = DocumentProcessor()
    root = Path(__file__).resolve().parents[1]
    icar_path = root / "data" / "agricultural" / "icar_advisories.json"
    scheme_path = root / "data" / "schemes" / "central_agricultural_schemes.json"

    agriculture = load_curated(icar_path, CROP_REGISTRY) if icar_path.exists() else ()
    schemes = load_schemes(scheme_path) if scheme_path.exists() else ()

    chunks = [c for doc in agriculture for c in processor.process_curated(doc)]
    chunks.extend(c for doc in schemes for c in processor.process_scheme(doc))

    kb = LocalKnowledgeBase(HashEmbeddingProvider())
    kb.add(chunks)

    set_auth_service(auth_service)
    set_key_store(key_store)
    set_credential_service(cred_service)
    set_history_store(hist_store)
    set_conversation_store(conv_store)
    set_knowledge_base(kb)

    auth_service.create_tenant("test-tenant", "Test Tenant")
    auth_service.register("farmer@test.org", "SecurePassword123!", tenant_id="test-tenant")
    token = auth_service.login("farmer@test.org", "SecurePassword123!", tenant_id="test-tenant")

    app = create_app()
    client = TestClient(app)

    yield {
        "client": client,
        "token": token,
        "auth_service": auth_service,
        "cred_service": cred_service,
        "kb": kb,
        "tenant_id": "test-tenant",
        "username": "farmer@test.org",
    }

    set_auth_service(None)
    set_key_store(None)
    set_credential_service(None)
    set_history_store(None)
    set_conversation_store(None)
    set_knowledge_base(None)


# 1. Crop retrieval without location succeeds and returns ICAR guidance
@pytest.mark.anyio
async def test_crop_retrieval_without_location_succeeds_and_returns_icar_guidance(env):
    workflow = RythuSaathiWorkflow(
        resolver=default_coverage_resolver(),
        knowledge=env["kb"],
    )
    req = FarmerRequest(
        request_id="req-p10-1",
        user_id="u1",
        tenant_id="t1",
        query="My rice leaves are showing brown leaf spots during tillering stage. What safe organic controls are available?",
        crop_query="rice",
        location_query=None,
        growth_stage_query="tillering",
    )
    res = await workflow.run(req, mode=WorkflowMode.DETERMINISTIC)
    crop_res = next(r for r in res.agent_results if r.agent_id is AgentId.CROP)

    assert crop_res.status is AgentStatus.COMPLETED
    assert len(crop_res.evidence_ids) > 0
    # Verify no localized state recommendations fabricated
    for eid in crop_res.evidence_ids:
        ev = next(e for e in res.evidence if e.evidence_id == eid)
        if ev.geographic_applicability:
            assert "location:country:in" in ev.geographic_applicability
    assert "without location refinement" in crop_res.payload.note


# 2. Nationwide ICAR retrieval with geography filter (location:country:in matches district/state)
def test_nationwide_icar_retrieval_with_geography_filter():
    scopes = ("location:country:in",)
    for fn in (_local_geography_applies, _persistent_geography_applies):
        assert fn(scopes, "location:district:in-pb-ludhiana") is True
        assert fn(scopes, "location:state:in-pb") is True
        assert fn(scopes, "location:country:in") is True
        assert fn(scopes, "location:district:in-tg-nalgonda") is True


# 3. State-specific retrieval with matching state vs mismatching state
def test_state_specific_retrieval_with_matching_state_vs_mismatching_state():
    pb_scopes = ("location:state:in-pb",)
    for fn in (_local_geography_applies, _persistent_geography_applies):
        assert fn(pb_scopes, "location:district:in-pb-ludhiana") is True
        assert fn(pb_scopes, "location:state:in-pb") is True
        assert fn(pb_scopes, "location:district:in-tg-nalgonda") is False
        assert fn(pb_scopes, "location:state:in-tg") is False


# 4. Unknown geographic metadata (None/empty) rejected when geography filter requested
def test_unknown_geographic_metadata_rejected_when_geography_filter_requested():
    for fn in (_local_geography_applies, _persistent_geography_applies):
        assert fn(None, "location:district:in-pb-ludhiana") is False
        assert fn((), "location:district:in-pb-ludhiana") is False
        assert fn([], "location:state:in-pb") is False


# 5. Central scheme retrieval without location when explicitly requested
@pytest.mark.anyio
async def test_central_scheme_retrieval_without_location_when_explicitly_requested(env):
    workflow = RythuSaathiWorkflow(
        resolver=default_coverage_resolver(),
        knowledge=env["kb"],
    )
    req = FarmerRequest(
        request_id="req-p10-5",
        user_id="u1",
        tenant_id="t1",
        query="What central government schemes like PM-KISAN are available for farmers?",
        crop_query=None,
        location_query=None,
    )
    res = await workflow.run(req, mode=WorkflowMode.DETERMINISTIC)
    scheme_res = next(r for r in res.agent_results if r.agent_id is AgentId.SCHEME)

    assert scheme_res.status is AgentStatus.COMPLETED
    assert len(scheme_res.evidence_ids) > 0
    for eid in scheme_res.evidence_ids:
        ev = next(e for e in res.evidence if e.evidence_id == eid)
        assert ev.source.source_type.value in {"primary_government", "official_scheme", "curated"}


# 6. Non-scheme question produces accurate skip reason
@pytest.mark.anyio
async def test_nonscheme_question_produces_accurate_skip_reason(env):
    workflow = RythuSaathiWorkflow(
        resolver=default_coverage_resolver(),
        knowledge=env["kb"],
    )
    req = FarmerRequest(
        request_id="req-p10-6",
        user_id="u1",
        tenant_id="t1",
        query="My rice leaves are showing brown leaf spots during tillering stage.",
        crop_query="rice",
        location_query=None,
        growth_stage_query="tillering",
    )
    res = await workflow.run(req, mode=WorkflowMode.DETERMINISTIC)
    scheme_res = next(r for r in res.agent_results if r.agent_id is AgentId.SCHEME)

    assert scheme_res.status is AgentStatus.SKIPPED
    assert scheme_res.payload.note == "No government scheme inquiry was requested; scheme lookup is skipped."
    assert "registered location" not in scheme_res.payload.note


# 7. General risk vulnerability assessment without location succeeds
@pytest.mark.anyio
async def test_general_risk_vulnerability_assessment_without_location_succeeds(env):
    workflow = RythuSaathiWorkflow(
        resolver=default_coverage_resolver(),
        knowledge=env["kb"],
    )
    req = FarmerRequest(
        request_id="req-p10-7",
        user_id="u1",
        tenant_id="t1",
        query="What disease threats affect rice crops?",
        crop_query="rice",
        location_query=None,
        growth_stage_query="tillering",
    )
    res = await workflow.run(req, mode=WorkflowMode.DETERMINISTIC)
    risk_res = next(r for r in res.agent_results if r.agent_id is AgentId.RISK_ALERT)

    assert risk_res.status is AgentStatus.COMPLETED
    assert len(risk_res.payload.background_vulnerabilities) > 0
    assert "background context" in risk_res.payload.summary.lower()


# 8. No unsupported active risk alert issued when location or triggers missing
@pytest.mark.anyio
async def test_no_unsupported_active_risk_alert_issued_when_location_or_triggers_missing(env):
    workflow = RythuSaathiWorkflow(
        resolver=default_coverage_resolver(),
        knowledge=env["kb"],
    )
    req = FarmerRequest(
        request_id="req-p10-8",
        user_id="u1",
        tenant_id="t1",
        query="What disease threats affect rice crops?",
        crop_query="rice",
        location_query=None,
        growth_stage_query="tillering",
    )
    res = await workflow.run(req, mode=WorkflowMode.DETERMINISTIC)
    risk_res = next(r for r in res.agent_results if r.agent_id is AgentId.RISK_ALERT)

    active_alerts = [a for a in risk_res.payload.alerts if getattr(a, "is_active", True)]
    assert active_alerts == []


# 9. LLM mode with a valid owner-scoped credential resolves real credential ID
def test_llm_mode_with_valid_owner_scoped_credential_resolves_real_credential_id(env):
    identity = env["auth_service"].authenticate(env["token"])

    # Enroll a real credential for user under provider 'groq'
    record = env["cred_service"].create(
        identity,
        provider_id="groq",
        secret="gsk_test_mock_valid_key_12345",
    )
    assert record.credential_id
    assert len(record.credential_id) > 10

    # Verify that list_status for this provider resolves the active credential
    statuses = env["cred_service"].list_status(identity, provider_id="groq")
    active = [s for s in statuses if s.state.value == "active"]
    assert len(active) == 1
    assert active[0].credential_id == record.credential_id


# 10. LLM mode without credential fails cleanly with HTTP 400 missing_provider_credential
def test_llm_mode_without_credential_fails_cleanly_with_http_400(env):
    # User has no credential enrolled for 'cerebras'
    resp = env["client"].post(
        "/api/v1/conversations",
        headers={"Authorization": f"Bearer {env['token']}"},
        json={
            "initial_message": "Tell me about rice blast",
            "mode": "cerebras",
        },
    )
    assert resp.status_code == 400
    data = resp.json()
    assert data["code"] == "missing_provider_credential"
    assert "No active BYOK credential found for provider 'cerebras'" in data["message"]


# 11. No synthetic credential UUID generated in conversation dispatch
def test_no_synthetic_credential_uuid_generated_in_conversation_dispatch(env):
    identity = env["auth_service"].authenticate(env["token"])
    # Enroll credential
    record = env["cred_service"].create(
        identity,
        provider_id="groq",
        secret="gsk_another_valid_key",
    )
    statuses = env["cred_service"].list_status(identity, provider_id="groq")
    active = [s for s in statuses if s.state.value == "active"]
    # Ensure real credential ID is returned and not a synthetic uuid hex
    assert active[0].credential_id == record.credential_id
    assert not active[0].credential_id.startswith("cid:")


# 12. Deterministic mode is accurately identified in execution mode
def test_deterministic_mode_is_accurately_identified_in_execution_mode(env):
    resp = env["client"].post(
        "/api/v1/conversations",
        headers={"Authorization": f"Bearer {env['token']}"},
        json={
            "initial_message": "My rice leaves are showing brown leaf spots",
            "mode": "deterministic",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert len(data["messages"]) >= 2
    assistant_msg = data["messages"][-1]
    assert assistant_msg["role"] == "assistant"
    assert "Execution mode: deterministic." in assistant_msg["content"]


# 13. API preserves retrieval payload notes and skip reasons in AgentResultSchema.summary
def test_api_preserves_retrieval_payload_notes_and_skip_reasons_in_agent_result_schema_summary(env):
    resp = env["client"].post(
        "/api/v1/conversations",
        headers={"Authorization": f"Bearer {env['token']}"},
        json={
            "initial_message": "Rice leaves showing brown leaf spots during tillering",
            "crop": "Rice",
            "growth_stage": "tillering",
            "mode": "deterministic",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assistant_msg = data["messages"][-1]
    telemetry = {t["agent_id"]: t for t in assistant_msg.get("agent_telemetry", [])}

    # Verify summaries are not null for specialist agents
    assert "Crop" in telemetry
    assert telemetry["Crop"]["summary"] is not None
    assert "general crop-scoped source excerpt" in telemetry["Crop"]["summary"]

    assert "Weather" in telemetry
    assert telemetry["Weather"]["summary"] is not None
    assert "Weather lookup requires a registered location" in telemetry["Weather"]["summary"]

    assert "Scheme" in telemetry
    assert telemetry["Scheme"]["summary"] is not None
    assert "No government scheme inquiry was requested" in telemetry["Scheme"]["summary"]

    assert "Market" in telemetry
    assert telemetry["Market"]["summary"] is not None
    assert "Market lookup requires registered crop and location" in telemetry["Market"]["summary"]

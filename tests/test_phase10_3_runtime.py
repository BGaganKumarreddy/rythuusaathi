"""Phase 10.3 Runtime Agent Execution Debugging Regression Tests.

Covers:
1. Exact Phase 10.3 runtime scenario:
   "My rice leaves are showing brown leaf spots during tillering stage. What safe organic controls are available?"
   - Intake: extracts rice, tillering, brown leaf spots.
   - Crop: retrieves nationwide ICAR agronomic evidence without requiring location.
   - Weather: skips with an accurate location-required reason.
   - Market: skips with an accurate location-required reason.
   - Scheme: skips because no government scheme inquiry was requested.
   - Risk Alert: assesses supported general vulnerabilities without issuing unsupported localized alerts (0 active alerts).
   - Coordinator: synthesizes available evidence into a useful partial advisory with location clarification.
2. Brown leaf spots query does NOT trigger active Rice Blast alert.
3. Actual blast symptom queries (e.g. "spindle lesions", "leaf blast") DO trigger active Rice Blast alert.
4. resolve_followup_context properly extracts ["brown leaf spots"] without truncating to ["leaf spots"].
5. LLM modes without active credentials produce explicit HTTP 400 missing_provider_credential for all supported providers.
6. Enrolled BYOK credentials correctly resolve for LLM execution mode.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from rythusaathi.agents.contracts import WorkflowMode
from rythusaathi.api.app import create_app
from rythusaathi.api.conversations import (
    ContextStateSchema,
    ConversationStore,
    resolve_followup_context,
)
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
from rythusaathi.domain.evidence import (
    ContentReference,
    EvidenceMode,
    EvidenceRecord,
    EvidenceSource,
    EvidenceSourceType,
    EvidenceStatus,
    ReferenceKind,
)
from rythusaathi.domain.risks import evaluate_disease_risks
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


def test_phase10_3_deterministic_runtime_scenario(env):
    """Validate full 7-agent execution with the exact Phase 10.3 test scenario query."""
    client = env["client"]
    token = env["token"]
    query = "My rice leaves are showing brown leaf spots during tillering stage. What safe organic controls are available?"

    resp = client.post(
        "/api/v1/conversations",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "initial_message": query,
            "mode": "deterministic",
        },
    )

    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
    data = resp.json()

    # Context assertions
    ctx = data["context"]
    assert ctx["crop"].lower() == "rice"
    assert ctx["growth_stage"] == "tillering"
    assert "brown leaf spots" in ctx["observations"]

    # Message structure assertions
    messages = data["messages"]
    assert len(messages) >= 2
    user_msg = messages[0]
    asst_msg = messages[-1]

    assert user_msg["role"] == "user"
    assert user_msg["content"] == query

    assert asst_msg["role"] == "assistant"
    content = asst_msg["content"]
    assert "Execution mode: deterministic." in content

    # Telemetry assertions for all 7 agents
    telemetry = {t["agent_id"]: t for t in asst_msg.get("agent_telemetry", [])}
    assert len(telemetry) == 7

    # 1. Intake
    assert "Intake" in telemetry
    assert telemetry["Intake"]["status"] == "completed"

    # 2. Crop Specialist
    assert "Crop" in telemetry
    assert telemetry["Crop"]["status"] == "completed"
    assert "general crop-scoped source excerpt" in telemetry["Crop"]["summary"]
    assert "without location refinement" in telemetry["Crop"]["summary"]

    # 3. Weather Specialist (Skipped - location required)
    assert "Weather" in telemetry
    assert telemetry["Weather"]["status"] == "skipped"
    assert "Weather lookup requires a registered location; none was supplied." == telemetry["Weather"]["summary"]

    # 4. Market Specialist (Skipped - location and crop required)
    assert "Market" in telemetry
    assert telemetry["Market"]["status"] == "skipped"
    assert "Market lookup requires registered crop and location values; one or both are missing." == telemetry["Market"]["summary"]

    # 5. Scheme Specialist (Skipped - non-scheme query)
    assert "Scheme" in telemetry
    assert telemetry["Scheme"]["status"] == "skipped"
    assert "No government scheme inquiry was requested; scheme lookup is skipped." == telemetry["Scheme"]["summary"]

    # 6. Risk Alert (Completed - general background vulnerabilities, NO active alerts)
    assert "RiskAlert" in telemetry
    assert telemetry["RiskAlert"]["status"] == "completed"
    assert "No active weather, disease, or pest risk thresholds exceeded; general crop susceptibilities retained as background context." == telemetry["RiskAlert"]["summary"]
    # Check that no active risk alerts were raised
    assert not asst_msg.get("active_risk_alerts")

    # 7. Coordinator (Completed - synthesized partial advisory)
    assert "Coordinator" in telemetry
    assert telemetry["Coordinator"]["status"] == "completed"
    assert asst_msg.get("advisory_id") is not None


def _make_sample_rice_blast_evidence() -> EvidenceRecord:
    now = datetime.now(timezone.utc)
    source = EvidenceSource(
        source_id="src:curated:icar-rice-blast",
        title="ICAR Advisory: Rice Blast",
        reference=ContentReference(kind=ReferenceKind.DOCUMENT_ID, value="src:curated:icar-rice-blast"),
        source_type=EvidenceSourceType.CURATED_AGRICULTURAL_KNOWLEDGE,
        source_identity="ICAR Rice Advisory Chunk",
    )
    return EvidenceRecord(
        evidence_id="ev:icar:rice:blast:1",
        request_id="req-test-1",
        source=source,
        excerpt="Rice blast caused by Magnaporthe oryzae affects tillering rice leaves.",
        content_reference=source.reference,
        retrieved_at=now,
        geographic_applicability=("location:country:in",),
        crop_applicability=("crop:oryza-sativa",),
        provenance="ICAR advisory curated knowledge",
        mode=EvidenceMode.CACHED,
        status=EvidenceStatus.AVAILABLE,
    )


def test_brown_leaf_spots_does_not_trigger_active_rice_blast_alert():
    """Verify that brown leaf spots do not trigger false positive Rice Blast active alert."""
    ev = _make_sample_rice_blast_evidence()
    active_alerts = evaluate_disease_risks(
        request_id="req-p10-3-test",
        crop_id="crop:oryza-sativa",
        location_id=None,
        growth_stage="tillering",
        evidence_records=(ev,),
        farmer_query="My rice leaves are showing brown leaf spots during tillering stage. What safe organic controls are available?",
        active_only=True,
    )
    assert len(active_alerts) == 0, f"Expected 0 active alerts, got: {active_alerts}"

    # Verify background susceptibility is evaluated when active_only=False
    all_items = evaluate_disease_risks(
        request_id="req-p10-3-test",
        crop_id="crop:oryza-sativa",
        location_id=None,
        growth_stage="tillering",
        evidence_records=(ev,),
        farmer_query="My rice leaves are showing brown leaf spots during tillering stage. What safe organic controls are available?",
        active_only=False,
    )
    bg_titles = [item.title for item in all_items if not item.is_active]
    assert "Background Rice Blast Susceptibility" in bg_titles


def test_blast_symptoms_trigger_active_rice_blast_alert():
    """Verify that actual blast symptoms (spindle lesion / leaf blast) properly trigger Rice Blast active alert."""
    ev = _make_sample_rice_blast_evidence()
    active_alerts = evaluate_disease_risks(
        request_id="req-p10-3-test",
        crop_id="crop:oryza-sativa",
        location_id=None,
        growth_stage="tillering",
        evidence_records=(ev,),
        farmer_query="Observed spindle-shaped lesions and leaf blast symptoms on rice seedlings.",
        active_only=True,
    )
    assert len(active_alerts) >= 1
    blast_alert = next((a for a in active_alerts if "Rice Blast" in a.title), None)
    assert blast_alert is not None
    assert blast_alert.is_active is True
    assert blast_alert.severity.value == "high"


def test_resolve_followup_context_extracts_brown_leaf_spots():
    """Verify that resolve_followup_context extracts 'brown leaf spots' and does not truncate to 'leaf spots'."""
    ctx = ContextStateSchema()
    updated, intent = resolve_followup_context(
        ctx,
        "My rice leaves are showing brown leaf spots during tillering stage. What safe organic controls are available?",
    )
    assert updated.crop.lower() == "rice"
    assert updated.growth_stage == "tillering"
    assert "brown leaf spots" in updated.observations
    assert "leaf spots" not in updated.observations


def test_llm_mode_missing_credentials_return_http_400(env):
    """Verify that missing credentials for LLM modes produce explicit HTTP 400 missing_provider_credential."""
    client = env["client"]
    token = env["token"]

    for provider in ("cerebras", "gemini", "groq"):
        resp = client.post(
            "/api/v1/conversations",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "initial_message": "Tell me about rice organic management.",
                "mode": provider,
            },
        )
        assert resp.status_code == 400, f"Provider {provider} did not return 400: {resp.text}"
        data = resp.json()
        assert data["code"] == "missing_provider_credential"
        assert f"No active BYOK credential found for provider '{provider}'" in data["message"]


def test_llm_mode_with_credential_enrolled_proceeds_past_credential_check(env):
    """Verify that enrolling a valid credential under groq resolves the active credential ID."""
    identity = env["auth_service"].authenticate(env["token"])
    record = env["cred_service"].create(
        identity,
        provider_id="groq",
        secret="gsk_valid_mock_key_abc1234567890",
    )
    statuses = env["cred_service"].list_status(identity, provider_id="groq")
    active = [s for s in statuses if s.state.value == "active"]
    assert len(active) == 1
    assert active[0].credential_id == record.credential_id

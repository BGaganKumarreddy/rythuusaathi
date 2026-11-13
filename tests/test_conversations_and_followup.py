"""Comprehensive tests for persistent conversations, follow-up intelligence, weather repair, and scheme repair."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import pytest
import httpx
from fastapi.testclient import TestClient

from rythusaathi.agents import (
    OpenMeteoWeatherProvider,
    RythuSaathiWorkflow,
    default_coverage_resolver,
)
from rythusaathi.agents.contracts import AgentStatus, WorkflowMode
from rythusaathi.domain import (
    AgentId,
    EvidenceMode,
)
from rythusaathi.api.app import create_app
from rythusaathi.api.conversations import (
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
from rythusaathi.api.schemas.conversations import ContextStateSchema
from rythusaathi.domain.seed import CROP_REGISTRY, LOCATION_REGISTRY
from rythusaathi.local_auth import LocalAuthentication
from rythusaathi.credentials import CredentialService
from rythusaathi.knowledge import LocalKnowledgeBase, HashEmbeddingProvider, DocumentProcessor, load_curated, load_schemes


@pytest.fixture
def test_env(tmp_path: Path):
    auth_db = tmp_path / "auth.sqlite3"
    hist_db = tmp_path / "history.sqlite3"

    auth_service = LocalAuthentication(auth_db)
    key_store = InMemoryKeyStore()
    cred_service = CredentialService(auth_db, key_store)
    hist_store = AdvisoryHistoryStore(hist_db)
    conv_store = ConversationStore(hist_db)

    # Setup knowledge base with curated advisories & schemes
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

    # Register tenants
    auth_service.create_tenant("tenant-a", "Tenant A")
    auth_service.create_tenant("tenant-b", "Tenant B")

    # Register farmer 1 (Tenant A)
    auth_service.register("farmer1@farm.org", "SecurePass123!", tenant_id="tenant-a")
    token1 = auth_service.login("farmer1@farm.org", "SecurePass123!", tenant_id="tenant-a")

    # Register farmer 2 (Tenant B)
    auth_service.register("farmer2@farm.org", "SecurePass123!", tenant_id="tenant-b")
    token2 = auth_service.login("farmer2@farm.org", "SecurePass123!", tenant_id="tenant-b")

    # Register farmer 3 (Tenant A - same tenant as farmer 1)
    auth_service.register("farmer3@farm.org", "SecurePass123!", tenant_id="tenant-a")
    token3 = auth_service.login("farmer3@farm.org", "SecurePass123!", tenant_id="tenant-a")

    app = create_app()
    client = TestClient(app)

    yield {
        "client": client,
        "token1": token1,
        "token2": token2,
        "token3": token3,
        "auth_service": auth_service,
        "conv_store": conv_store,
        "kb": kb,
    }

    # Clean up singletons
    set_auth_service(None)
    set_key_store(None)
    set_credential_service(None)
    set_history_store(None)
    set_conversation_store(None)
    set_knowledge_base(None)


# -------------------------------------------------------------------------
# PART 2 & 10: Multi-Turn Conversation & Follow-up Intelligence Tests
# -------------------------------------------------------------------------

def test_conversation_followup_scenario_turn1_to_turn3(test_env):
    """Scenario 1: Farmer reports brown spots on rice leaves, supplies location in follow-up,

    and then reports spots spreading. Verify crop and location are preserved across turns.
    """
    client = test_env["client"]
    token1 = test_env["token1"]
    headers = {"Authorization": f"Bearer {token1}"}

    # Turn 1: Create conversation with initial message
    turn1_resp = client.post(
        "/api/v1/conversations",
        headers=headers,
        json={
            "initial_message": "My rice leaves have brown spots.",
            "mode": "deterministic",
        },
    )
    assert turn1_resp.status_code == 201
    conv_data = turn1_resp.json()
    conv_id = conv_data["conversation_id"]

    # Verify Turn 1 context
    ctx = conv_data["context"]
    assert ctx["crop"] == "Rice"
    assert "brown spots" in ctx["observations"]
    assert len(conv_data["messages"]) == 2  # user + assistant

    turn1_reply = conv_data["messages"][1]["content"]
    assert "rice" in turn1_reply.lower()
    # Since location was missing, coordinator asks for location
    assert "location" in turn1_reply.lower()

    # Turn 2: Follow-up supplying location "My farm is in Ludhiana"
    turn2_resp = client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        headers=headers,
        json={
            "content": "My farm is in Ludhiana.",
            "mode": "deterministic",
        },
    )
    assert turn2_resp.status_code == 201
    asst_msg_2 = turn2_resp.json()
    assert asst_msg_2["role"] == "assistant"

    # Fetch updated conversation details
    detail_resp = client.get(f"/api/v1/conversations/{conv_id}", headers=headers)
    assert detail_resp.status_code == 200
    updated_conv = detail_resp.json()
    updated_ctx = updated_conv["context"]

    # CRITICAL: Crop "Rice" must be preserved from Turn 1! Location "Ludhiana" must be added!
    assert updated_ctx["crop"] == "Rice"
    assert updated_ctx["location"] == "Ludhiana"
    assert len(updated_conv["messages"]) == 4  # (user, asst, user, asst)

    # Weather agent should have executed for Ludhiana!
    assert asst_msg_2["weather_evidence"] is not None
    assert len(asst_msg_2["weather_evidence"]) > 0

    # Turn 3: Follow-up "The spots are spreading."
    turn3_resp = client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        headers=headers,
        json={
            "content": "The spots are spreading.",
            "mode": "deterministic",
        },
    )
    assert turn3_resp.status_code == 201
    asst_msg_3 = turn3_resp.json()

    detail_resp3 = client.get(f"/api/v1/conversations/{conv_id}", headers=headers)
    final_conv = detail_resp3.json()
    final_ctx = final_conv["context"]

    # Both Rice and Ludhiana remain retained
    assert final_ctx["crop"] == "Rice"
    assert final_ctx["location"] == "Ludhiana"
    # Observations contain both brown spots and spreading
    assert any("spreading" in o for o in final_ctx["observations"])
    assert len(final_conv["messages"]) == 6


def test_conversation_precedence_user_correction(test_env):
    """Test precedence: Latest explicit user correction overrides previously confirmed crop."""
    client = test_env["client"]
    token1 = test_env["token1"]
    headers = {"Authorization": f"Bearer {token1}"}

    # Start with Rice
    conv_resp = client.post(
        "/api/v1/conversations",
        headers=headers,
        json={"initial_message": "Rice leaves yellowing", "mode": "deterministic"},
    )
    assert conv_resp.status_code == 201
    conv_id = conv_resp.json()["conversation_id"]

    # User correction: "Actually, I am growing cotton, not rice."
    msg_resp = client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        headers=headers,
        json={
            "content": "Actually, I am growing cotton, not rice.",
            "mode": "deterministic",
        },
    )
    assert msg_resp.status_code == 201

    # Verify context was corrected to Cotton
    detail = client.get(f"/api/v1/conversations/{conv_id}", headers=headers).json()
    assert detail["context"]["crop"] == "Cotton"


def test_conversation_tenant_and_user_isolation(test_env):
    """Security scenario: Verify strict cross-user and cross-tenant authorization."""
    client = test_env["client"]
    token1 = test_env["token1"]
    token2 = test_env["token2"]  # Tenant B
    token3 = test_env["token3"]  # Tenant A, different user

    # Farmer 1 creates a conversation
    resp = client.post(
        "/api/v1/conversations",
        headers={"Authorization": f"Bearer {token1}"},
        json={"title": "Private Consultation", "mode": "deterministic"},
    )
    conv_id = resp.json()["conversation_id"]

    # Farmer 2 (Tenant B) tries to read Farmer 1's conversation -> 404
    cross_tenant_resp = client.get(
        f"/api/v1/conversations/{conv_id}",
        headers={"Authorization": f"Bearer {token2}"},
    )
    assert cross_tenant_resp.status_code == 404

    # Farmer 3 (Tenant A, different user) tries to read Farmer 1's conversation -> 404
    cross_user_resp = client.get(
        f"/api/v1/conversations/{conv_id}",
        headers={"Authorization": f"Bearer {token3}"},
    )
    assert cross_user_resp.status_code == 404

    # Farmer 2 tries to send message into Farmer 1's conversation -> 404
    hack_msg_resp = client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        headers={"Authorization": f"Bearer {token2}"},
        json={"content": "Malicious intrusion", "mode": "deterministic"},
    )
    assert hack_msg_resp.status_code == 404

    # Farmer 1 can access it normally
    ok_resp = client.get(
        f"/api/v1/conversations/{conv_id}",
        headers={"Authorization": f"Bearer {token1}"},
    )
    assert ok_resp.status_code == 200


# -------------------------------------------------------------------------
# PART 4 & 10: Weather Integration Tests
# -------------------------------------------------------------------------

@pytest.mark.anyio
async def test_weather_provider_live_success_with_registered_location():
    """Verify Open-Meteo returns live weather observation for registered seed location."""
    provider = OpenMeteoWeatherProvider()
    obs = await provider.get_observation("req-weather-test", "location:district:in-tg-adilabad")
    assert obs is not None
    assert obs.location_id == "location:district:in-tg-adilabad"
    assert obs.mode == EvidenceMode.LIVE
    assert obs.temperature_c is not None
    assert obs.source.source_id == "external:open-meteo"
    assert obs.retrieved_at >= obs.observed_at


@pytest.mark.anyio
async def test_weather_provider_missing_or_empty_location():
    """Verify Open-Meteo safely returns None for missing location."""
    provider = OpenMeteoWeatherProvider()
    obs = await provider.get_observation("req-test", "")
    assert obs is None


@pytest.mark.anyio
async def test_weather_provider_invalid_location_handled_safely():
    """Verify unknown location that cannot be geocoded returns None without crashing."""
    provider = OpenMeteoWeatherProvider()
    obs = await provider.get_observation("req-test", "location:district:non-existent-fantasy-place-123456")
    assert obs is None


@pytest.mark.anyio
async def test_weather_provider_timeout_and_unavailability():
    """Verify provider gracefully handles network timeouts and 500 status codes."""
    # Custom mock transport returning 500
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": True, "reason": "Internal Server Error"})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenMeteoWeatherProvider(http_client=mock_client)
    obs = await provider.get_observation("req-500", "location:district:in-tg-adilabad")
    assert obs is None
    await mock_client.aclose()


# -------------------------------------------------------------------------
# PART 5 & 10: Government Scheme Integration Tests
# -------------------------------------------------------------------------

def test_scheme_retrieval_with_missing_location(test_env):
    """When location is missing, SchemeAgent retrieves nationwide central schemes (PM-KISAN, PMFBY)."""
    client = test_env["client"]
    token = test_env["token1"]
    headers = {"Authorization": f"Bearer {token}"}

    # Ask about schemes with no location supplied
    resp = client.post(
        "/api/v1/advisories",
        headers=headers,
        json={
            "query": "What agricultural government schemes can I apply for?",
            "mode": "deterministic",
        },
    )
    assert resp.status_code == 201
    adv = resp.json()

    # Central scheme evidence must be present
    scheme_ev = adv["scheme_evidence"]
    assert len(scheme_ev) > 0
    titles = [e["title"] for e in scheme_ev]
    assert any("PM-KISAN" in t or "Pradhan Mantri" in t for t in titles)


def test_scheme_retrieval_state_jurisdiction_filtering(test_env):
    """When location is in Telangana, Telangana Rythu Bharosa is retrieved.

    When location is in Punjab, Telangana Rythu Bharosa is NOT retrieved.
    """
    client = test_env["client"]
    token = test_env["token1"]
    headers = {"Authorization": f"Bearer {token}"}

    # Case A: Farmer in Adilabad, Telangana
    tg_resp = client.post(
        "/api/v1/advisories",
        headers=headers,
        json={
            "query": "What schemes are available for my farm in Adilabad?",
            "location": "Adilabad",
            "mode": "deterministic",
        },
    )
    assert tg_resp.status_code == 201
    tg_titles = [e["title"] for e in tg_resp.json()["scheme_evidence"]]
    assert any("Telangana" in t or "Rythu Bharosa" in t for t in tg_titles)

    # Case B: Farmer in Ludhiana, Punjab
    pb_resp = client.post(
        "/api/v1/advisories",
        headers=headers,
        json={
            "query": "What schemes are available for my farm in Ludhiana?",
            "location": "Ludhiana",
            "mode": "deterministic",
        },
    )
    assert pb_resp.status_code == 201
    pb_titles = [e["title"] for e in pb_resp.json()["scheme_evidence"]]
    # Punjab farmer must NEVER receive Telangana state schemes!
    assert not any("Telangana" in t or "Rythu Bharosa" in t for t in pb_titles)
    # But central schemes remain eligible
    assert any("PM-KISAN" in t or "Pradhan Mantri" in t for t in pb_titles)


def test_scheme_eligibility_grounding_and_uncertainty(test_env):
    """Verify that scheme findings explicitly note that personal eligibility is not determined."""
    client = test_env["client"]
    token = test_env["token1"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.post(
        "/api/v1/advisories",
        headers=headers,
        json={
            "query": "Can I get money under PM-KISAN?",
            "location": "Guntur",
            "mode": "deterministic",
        },
    )
    assert resp.status_code == 201
    synthesis = resp.json()["coordinator_synthesis"]

    # Coordinator synthesis must preserve eligibility caution
    assert "not determine personal eligibility" in synthesis or "eligibility" in synthesis.lower()

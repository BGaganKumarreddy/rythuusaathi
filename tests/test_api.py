"""Comprehensive unit and integration test suite for the RythuSaathi FastAPI REST API.

Covers:
1. System health check & OpenAPI documentation schema.
2. Authentication lifecycle:
   - User registration & duplicate username rejection.
   - Password length validation.
   - Login, token issuance, and Argon2id verification.
   - User profile (/me) & token expiry/tampering rejection.
   - Logout & session invalidation.
3. BYOK Credential management:
   - Storing API keys in encrypted vault.
   - Secrecy: raw keys never returned in response or logs.
   - User and tenant ownership isolation.
   - Deletion of credentials.
4. Configuration endpoints:
   - Crops, growth stages, and varieties.
   - Administrative location scopes.
   - Inference and data provider catalog.
5. Seven-agent Advisory workflow execution:
   - Full deterministic execution across all 7 agents.
   - Coordinator synthesis presentation.
   - Weather, market, and scheme evidence preservation with full provenance.
   - Active risk alerts and background vulnerability distinction.
   - Missing credential rejection for external LLM modes.
   - Request schema validation (empty query, bad types).
6. Advisory history & persistence:
   - Durable SQLite persistence across requests.
   - Retrieving consultation by ID.
   - Retrieving full diagnostic execution trace.
   - Tenant & user history isolation (cross-tenant 404).
   - Paginated history listing and crop filtering.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
import unittest

from fastapi.testclient import TestClient

from rythusaathi.api.app import create_app
from rythusaathi.api.dependencies import (
    InMemoryKeyStore,
    set_auth_service,
    set_credential_service,
    set_history_store,
    set_key_store,
    set_knowledge_base,
)
from rythusaathi.api.history import AdvisoryHistoryStore
from rythusaathi.credentials import CredentialService
from rythusaathi.domain.seed import CROP_REGISTRY
from rythusaathi.knowledge import (
    DocumentProcessor,
    HashEmbeddingProvider,
    LocalKnowledgeBase,
    load_curated,
    load_schemes,
)
from rythusaathi.local_auth import LocalAuthentication

ROOT = Path(__file__).resolve().parents[1]


def build_test_knowledge() -> LocalKnowledgeBase:
    processor = DocumentProcessor()
    icar_path = ROOT / "data" / "agricultural" / "icar_advisories.json"
    scheme_path = ROOT / "data" / "schemes" / "central_agricultural_schemes.json"

    agriculture = load_curated(icar_path, CROP_REGISTRY) if icar_path.exists() else ()
    schemes = load_schemes(scheme_path) if scheme_path.exists() else ()

    chunks = [c for doc in agriculture for c in processor.process_curated(doc)]
    chunks.extend(c for doc in schemes for c in processor.process_scheme(doc))

    kb = LocalKnowledgeBase(HashEmbeddingProvider())
    kb.add(chunks)
    return kb


class APITestBase(unittest.TestCase):
    """Base setup configuring isolated databases and in-memory key storage per test."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

        self.auth_db = self.temp_path / "test_auth.sqlite3"
        self.history_db = self.temp_path / "test_history.sqlite3"

        self.auth_service = LocalAuthentication(self.auth_db)
        self.key_store = InMemoryKeyStore()
        self.credential_service = CredentialService(self.auth_db, self.key_store)
        self.history_store = AdvisoryHistoryStore(self.history_db)
        self.knowledge = build_test_knowledge()

        # Inject isolated dependencies into API singletons
        set_auth_service(self.auth_service)
        set_key_store(self.key_store)
        set_credential_service(self.credential_service)
        set_history_store(self.history_store)
        set_knowledge_base(self.knowledge)

        self.app = create_app()
        self.client = TestClient(self.app)

        # Pre-register default tenant and user
        self.auth_service.create_tenant("test-tenant", "Test Tenant")
        self.auth_service.register(
            username="farmer_test",
            password="StrongPassword123!",
            tenant_id="test-tenant",
        )
        self.token = self.auth_service.login("farmer_test", "StrongPassword123!", "test-tenant")
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def tearDown(self):
        set_auth_service(None)
        set_key_store(None)
        set_credential_service(None)
        set_history_store(None)
        set_knowledge_base(None)
        self.temp_dir.cleanup()


class HealthAndOpenAPITests(APITestBase):
    def test_health_check(self):
        res = self.client.get("/api/v1/health")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["agents"], 7)
        self.assertEqual(data["service"], "rythusaathi")

    def test_openapi_schema(self):
        res = self.client.get("/openapi.json")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("paths", data)
        self.assertIn("/api/v1/auth/register", data["paths"])
        self.assertIn("/api/v1/advisories", data["paths"])
        self.assertIn("/api/v1/credentials", data["paths"])


class AuthenticationLifecycleTests(APITestBase):
    def test_register_and_login_flow(self):
        # Register new user
        reg_res = self.client.post(
            "/api/v1/auth/register",
            json={
                "username": "ramesh_patel",
                "password": "SecurePassword987!",
                "tenant_id": "test-tenant",
            },
        )
        self.assertEqual(reg_res.status_code, 201)
        data = reg_res.json()
        self.assertEqual(data["username"], "ramesh_patel")
        self.assertIn("user_id", data)

        # Login with newly registered user
        login_res = self.client.post(
            "/api/v1/auth/login",
            json={
                "username": "ramesh_patel",
                "password": "SecurePassword987!",
                "tenant_id": "test-tenant",
            },
        )
        self.assertEqual(login_res.status_code, 200)
        l_data = login_res.json()
        self.assertIn("access_token", l_data)
        self.assertEqual(l_data["token_type"], "bearer")

        # Call /me with token
        me_res = self.client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {l_data['access_token']}"},
        )
        self.assertEqual(me_res.status_code, 200)
        m_data = me_res.json()
        self.assertEqual(m_data["user_id"], data["user_id"])
        self.assertEqual(m_data["tenant_id"], "test-tenant")

    def test_duplicate_username_rejected(self):
        res = self.client.post(
            "/api/v1/auth/register",
            json={
                "username": "farmer_test",
                "password": "Password123456!",
                "tenant_id": "test-tenant",
            },
        )
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["code"], "username_already_registered")

    def test_password_length_validation(self):
        res = self.client.post(
            "/api/v1/auth/register",
            json={
                "username": "short_pw_user",
                "password": "short",
                "tenant_id": "test-tenant",
            },
        )
        self.assertEqual(res.status_code, 422)

    def test_unauthorized_access_rejected(self):
        # No header
        res = self.client.get("/api/v1/auth/me")
        self.assertEqual(res.status_code, 401)

        # Invalid token
        res_bad = self.client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer invalid-tampered-token-123"},
        )
        self.assertEqual(res_bad.status_code, 401)

    def test_logout_invalidates_session(self):
        # Successful /me before logout
        me_res = self.client.get("/api/v1/auth/me", headers=self.headers)
        self.assertEqual(me_res.status_code, 200)

        # Logout
        out_res = self.client.post("/api/v1/auth/logout", headers=self.headers)
        self.assertEqual(out_res.status_code, 200)

        # /me should now fail with 401
        me_after = self.client.get("/api/v1/auth/me", headers=self.headers)
        self.assertEqual(me_after.status_code, 401)


class CredentialVaultTests(APITestBase):
    def test_create_and_list_credential_with_secrecy(self):
        # Add a credential
        secret_key = "csk-secret-test-key-9999"
        create_res = self.client.post(
            "/api/v1/credentials",
            headers=self.headers,
            json={"provider_id": "cerebras", "secret": secret_key},
        )
        self.assertEqual(create_res.status_code, 201)
        data = create_res.json()
        self.assertEqual(data["provider_id"], "cerebras")
        self.assertEqual(data["state"], "active")
        cid = data["credential_id"]

        # CRITICAL SECURITY CHECK: secret key must never be returned in JSON response
        self.assertNotIn(secret_key, create_res.text)

        # List credentials
        list_res = self.client.get("/api/v1/credentials", headers=self.headers)
        self.assertEqual(list_res.status_code, 200)
        l_data = list_res.json()
        self.assertEqual(l_data["total"], 1)
        self.assertEqual(l_data["items"][0]["credential_id"], cid)
        # Verify secret not in list response
        self.assertNotIn(secret_key, list_res.text)

    def test_cross_user_credential_isolation(self):
        # Store credential under user 1
        c_res = self.client.post(
            "/api/v1/credentials",
            headers=self.headers,
            json={"provider_id": "groq", "secret": "gsk-key-user1"},
        )
        self.assertEqual(c_res.status_code, 201)
        cid = c_res.json()["credential_id"]

        # Register User 2 in same tenant
        self.auth_service.register("user_two", "Password123456!", "test-tenant")
        token_u2 = self.auth_service.login("user_two", "Password123456!", "test-tenant")
        headers_u2 = {"Authorization": f"Bearer {token_u2}"}

        # User 2 list should be empty (owner isolation)
        list_u2 = self.client.get("/api/v1/credentials", headers=headers_u2)
        self.assertEqual(list_u2.status_code, 200)
        self.assertEqual(list_u2.json()["total"], 0)

        # User 2 attempting to delete User 1's credential must fail with 404 (do not disclose existence)
        del_res = self.client.delete(f"/api/v1/credentials/{cid}", headers=headers_u2)
        self.assertEqual(del_res.status_code, 404)

    def test_delete_credential(self):
        c_res = self.client.post(
            "/api/v1/credentials",
            headers=self.headers,
            json={"provider_id": "gemini", "secret": "AIzaSyTestSecret123"},
        )
        cid = c_res.json()["credential_id"]

        del_res = self.client.delete(f"/api/v1/credentials/{cid}", headers=self.headers)
        self.assertEqual(del_res.status_code, 200)

        # Subsequent list should be empty
        list_res = self.client.get("/api/v1/credentials", headers=self.headers)
        self.assertEqual(list_res.json()["total"], 0)


class ConfigurationCatalogTests(APITestBase):
    def test_list_crops(self):
        res = self.client.get("/api/v1/configuration/crops")
        self.assertEqual(res.status_code, 200)
        crops = res.json()
        self.assertGreater(len(crops), 0)
        rice = next((c for c in crops if c["crop_id"] == "crop:oryza-sativa"), None)
        self.assertIsNotNone(rice)
        self.assertIn("rice", rice["common_names"])
        self.assertGreater(len(rice["growth_stages"]), 0)

    def test_list_locations(self):
        res = self.client.get("/api/v1/configuration/locations")
        self.assertEqual(res.status_code, 200)
        locations = res.json()
        self.assertGreater(len(locations), 0)
        self.assertTrue(any("Punjab" in loc["display_name"] for loc in locations))

    def test_list_providers(self):
        res = self.client.get("/api/v1/configuration/providers")
        self.assertEqual(res.status_code, 200)
        providers = res.json()
        prov_ids = {p["provider_id"] for p in providers}
        self.assertIn("deterministic", prov_ids)
        self.assertIn("cerebras", prov_ids)
        self.assertIn("gemini", prov_ids)
        self.assertIn("groq", prov_ids)
        self.assertIn("open-meteo", prov_ids)
        self.assertIn("data.gov.in", prov_ids)


class AdvisoryWorkflowAPITests(APITestBase):
    def test_deterministic_advisory_execution_success(self):
        req_payload = {
            "query": "Rice crop in Punjab has yellowing leaves during tillering; what management practices are recommended?",
            "crop": "rice",
            "location": "Punjab",
            "growth_stage": "tillering",
            "mode": "deterministic",
        }
        res = self.client.post("/api/v1/advisories", headers=self.headers, json=req_payload)
        self.assertEqual(res.status_code, 201)
        data = res.json()

        # 1. Top-level response fields
        self.assertIn("advisory_id", data)
        self.assertIn(data["status"], ["completed", "partial"])
        self.assertEqual(data["execution_mode"], "deterministic")
        self.assertIn("Understanding:", data["coordinator_synthesis"])

        # 2. All 7 agents executed in Microsoft Agent Framework graph
        results = {r["agent_id"]: r for r in data["agent_results"]}
        expected_agents = {"Intake", "Weather", "Crop", "Market", "Scheme", "RiskAlert", "Coordinator"}
        self.assertEqual(set(results.keys()), expected_agents)
        self.assertEqual(results["Intake"]["status"], "completed")
        self.assertEqual(results["RiskAlert"]["status"], "completed")

        # 3. Evidence provenance preserved
        self.assertGreater(len(data["all_evidence"]), 0)
        for ev in data["all_evidence"]:
            self.assertIn("evidence_id", ev)
            self.assertIn("title", ev)
            self.assertIn("source_type", ev)

        # 4. Scheme and Weather evidence categorized
        self.assertGreater(len(data["scheme_evidence"]), 0)
        self.assertTrue(any("PM-KISAN" in s["title"] or "PMFBY" in s["title"] for s in data["scheme_evidence"]))

        # 5. Suggested next steps populated
        self.assertIsInstance(data["suggested_next_steps"], list)

    def test_active_and_background_risk_alerts_distinction(self):
        # Query without elevated microclimate/symptom indicators produces background vulnerabilities only
        req_quiet = {
            "query": "Rice crop in Punjab; what pest and disease risks should I watch out for?",
            "crop": "rice",
            "location": "Punjab",
            "growth_stage": "tillering",
            "mode": "deterministic",
        }
        res = self.client.post("/api/v1/advisories", headers=self.headers, json=req_quiet)
        self.assertEqual(res.status_code, 201)
        data = res.json()

        # 0 active alerts
        self.assertEqual(len(data["active_risk_alerts"]), 0)
        # Background vulnerabilities retained
        self.assertGreater(len(data["background_vulnerabilities"]), 0)
        self.assertFalse(data["background_vulnerabilities"][0]["is_active"])

    def test_missing_crop_and_location_advisory_honest_reporting(self):
        req = {
            "query": "What are the recommended fertilizer timings?",
            "mode": "deterministic",
        }
        res = self.client.post("/api/v1/advisories", headers=self.headers, json=req)
        self.assertEqual(res.status_code, 201)
        data = res.json()
        results = {r["agent_id"]: r for r in data["agent_results"]}
        # Risk alert skipped when crop/location missing
        self.assertEqual(results["RiskAlert"]["status"], "skipped")

    def test_advisory_request_validation_errors(self):
        # Query too short
        res = self.client.post("/api/v1/advisories", headers=self.headers, json={"query": "hi"})
        self.assertEqual(res.status_code, 422)

        # Invalid mode
        res_mode = self.client.post(
            "/api/v1/advisories",
            headers=self.headers,
            json={"query": "Valid query here", "mode": "unsupported_llm"},
        )
        self.assertEqual(res_mode.status_code, 422)

    def test_external_mode_without_credential_fails_safely(self):
        res = self.client.post(
            "/api/v1/advisories",
            headers=self.headers,
            json={
                "query": "Rice crop inquiry for testing",
                "crop": "rice",
                "location": "Punjab",
                "mode": "cerebras",
            },
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["code"], "missing_provider_credential")


class AdvisoryHistoryAndPersistenceTests(APITestBase):
    def test_save_and_retrieve_advisory_by_id(self):
        # Execute advisory
        res = self.client.post(
            "/api/v1/advisories",
            headers=self.headers,
            json={
                "query": "Cotton crop in Adilabad; what schemes are available?",
                "crop": "cotton",
                "location": "Adilabad",
                "mode": "deterministic",
            },
        )
        self.assertEqual(res.status_code, 201)
        advisory_id = res.json()["advisory_id"]

        # Retrieve by ID
        get_res = self.client.get(f"/api/v1/advisories/{advisory_id}", headers=self.headers)
        self.assertEqual(get_res.status_code, 200)
        g_data = get_res.json()
        self.assertEqual(g_data["advisory_id"], advisory_id)
        self.assertIn("Adilabad", g_data["coordinator_synthesis"])

        # Retrieve diagnostic trace
        trace_res = self.client.get(f"/api/v1/advisories/{advisory_id}/trace", headers=self.headers)
        self.assertEqual(trace_res.status_code, 200)
        t_data = trace_res.json()
        self.assertEqual(t_data["advisory_id"], advisory_id)
        self.assertGreater(len(t_data["findings"]), 0)
        self.assertEqual(len(t_data["agent_executions"]), 7)

    def test_history_tenant_and_user_isolation(self):
        # User 1 creates advisory
        res1 = self.client.post(
            "/api/v1/advisories",
            headers=self.headers,
            json={"query": "User 1 consultation query", "crop": "rice", "location": "Punjab"},
        )
        adv_id1 = res1.json()["advisory_id"]

        # Register User 2 in a different tenant
        self.auth_service.create_tenant("tenant-two", "Tenant Two")
        self.auth_service.register("user_other_tenant", "Password123456!", "tenant-two")
        token_u2 = self.auth_service.login("user_other_tenant", "Password123456!", "tenant-two")
        headers_u2 = {"Authorization": f"Bearer {token_u2}"}

        # User 2 attempting to retrieve User 1's advisory returns 404 (owner isolation)
        cross_res = self.client.get(f"/api/v1/advisories/{adv_id1}", headers=headers_u2)
        self.assertEqual(cross_res.status_code, 404)

        # User 2 trace returns 404
        cross_trace = self.client.get(f"/api/v1/advisories/{adv_id1}/trace", headers=headers_u2)
        self.assertEqual(cross_trace.status_code, 404)

        # User 2 history list is empty
        hist_u2 = self.client.get("/api/v1/history", headers=headers_u2)
        self.assertEqual(hist_u2.status_code, 200)
        self.assertEqual(hist_u2.json()["total"], 0)

    def test_history_pagination_and_crop_filtering(self):
        # Create 3 advisories under User 1
        for i in range(3):
            crop_name = "rice" if i < 2 else "cotton"
            self.client.post(
                "/api/v1/advisories",
                headers=self.headers,
                json={"query": f"Consultation query #{i}", "crop": crop_name, "location": "Punjab"},
            )

        # List all history (limit=2, offset=0)
        h_all = self.client.get("/api/v1/history?limit=2&offset=0", headers=self.headers)
        self.assertEqual(h_all.status_code, 200)
        d_all = h_all.json()
        self.assertEqual(d_all["total"], 3)
        self.assertEqual(len(d_all["items"]), 2)

        # Offset=2
        h_p2 = self.client.get("/api/v1/history?limit=2&offset=2", headers=self.headers)
        self.assertEqual(h_p2.status_code, 200)
        self.assertEqual(len(h_p2.json()["items"]), 1)

        # Filter by crop=cotton
        h_cotton = self.client.get("/api/v1/history?crop=cotton", headers=self.headers)
        self.assertEqual(h_cotton.status_code, 200)
        self.assertEqual(h_cotton.json()["total"], 1)
        self.assertEqual(h_cotton.json()["items"][0]["crop"], "cotton")


class SecurityAuditRegressionTests(APITestBase):
    """Regression tests verifying Phase 7 security fixes."""

    def test_privilege_escalation_to_tenant_admin_on_existing_tenant_rejected(self):
        """Unauthenticated clients cannot self-appoint as tenant_admin in an existing tenant."""
        res = self.client.post(
            "/api/v1/auth/register",
            json={
                "username": "attacker_user",
                "password": "AttackerPassword123!",
                "tenant_id": "test-tenant",
                "role": "tenant_admin",
            },
        )
        self.assertEqual(res.status_code, 403)
        data = res.json()
        self.assertEqual(data["code"], "privilege_escalation_denied")

    def test_new_tenant_registration_with_admin_role_allowed(self):
        """Registering a new tenant allows the creator to be registered as tenant_admin."""
        res = self.client.post(
            "/api/v1/auth/register",
            json={
                "username": "new_admin_user",
                "password": "ValidPassword999!",
                "tenant_id": "fresh-cooperative",
                "tenant_name": "Fresh Cooperative",
                "role": "tenant_admin",
            },
        )
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertEqual(data["role"], "tenant_admin")
        self.assertEqual(data["tenant_id"], "fresh-cooperative")

    def test_existing_tenant_member_registration_allowed(self):
        """Registering as a regular member into an existing tenant is permitted."""
        res = self.client.post(
            "/api/v1/auth/register",
            json={
                "username": "regular_member_user",
                "password": "ValidPassword999!",
                "tenant_id": "test-tenant",
                "role": "member",
            },
        )
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertEqual(data["role"], "member")

    def test_key_store_fails_closed_without_explicit_test_flag(self):
        """Production get_key_store must fail closed if OS credential store is unavailable."""
        from unittest.mock import patch
        import os
        from rythusaathi.api.dependencies import get_key_store
        from rythusaathi.credentials import OsCredentialStoreUnavailable

        set_key_store(None)
        with patch("rythusaathi.api.dependencies.WindowsCredentialStore", side_effect=RuntimeError("OS vault missing")):
            with patch.dict(os.environ, {"RYTHUSAATHI_ALLOW_IN_MEMORY_KEY_STORE": "0"}, clear=False):
                with self.assertRaises(OsCredentialStoreUnavailable):
                    get_key_store()

    def test_key_store_allows_in_memory_with_explicit_env_flag(self):
        """Explicit RYTHUSAATHI_ALLOW_IN_MEMORY_KEY_STORE=1 permits test fallback."""
        from unittest.mock import patch
        import os
        from rythusaathi.api.dependencies import get_key_store

        set_key_store(None)
        with patch("rythusaathi.api.dependencies.WindowsCredentialStore", side_effect=RuntimeError("OS vault missing")):
            with patch.dict(os.environ, {"RYTHUSAATHI_ALLOW_IN_MEMORY_KEY_STORE": "1"}, clear=False):
                ks = get_key_store()
                self.assertIsInstance(ks, InMemoryKeyStore)

    def test_secret_is_never_leaked_in_credential_listing_or_trace(self):
        """Verify provider secrets never leak in responses, trace objects, or history."""
        # 1. Enroll a credential with a distinct secret
        secret = "cerebras-sk-live-ultra-confidential-secret-999"
        res = self.client.post(
            "/api/v1/credentials",
            headers=self.headers,
            json={"provider_id": "cerebras", "secret": secret},
        )
        self.assertEqual(res.status_code, 201)
        cred_id = res.json()["credential_id"]

        # Ensure raw response does not contain secret
        self.assertNotIn(secret, res.text)

        # 2. List credentials
        list_res = self.client.get("/api/v1/credentials", headers=self.headers)
        self.assertEqual(list_res.status_code, 200)
        self.assertNotIn(secret, list_res.text)

        # 3. Create advisory and check trace
        adv_res = self.client.post(
            "/api/v1/advisories",
            headers=self.headers,
            json={"query": "Cotton pest check", "crop": "cotton", "location": "Adilabad"},
        )
        self.assertEqual(adv_res.status_code, 201)
        adv_id = adv_res.json()["advisory_id"]
        self.assertNotIn(secret, adv_res.text)

        # Trace check
        trace_res = self.client.get(f"/api/v1/advisories/{adv_id}/trace", headers=self.headers)
        self.assertEqual(trace_res.status_code, 200)
        self.assertNotIn(secret, trace_res.text)


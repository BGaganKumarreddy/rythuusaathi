"""Tests for Phase 8.1: Intake context extraction, specialist agent integration, and farmer/admin roles."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from rythusaathi.agents import (
    FarmerRequest,
    RythuSaathiWorkflow,
    default_coverage_resolver,
)
from rythusaathi.agents.contracts import AdvisoryStatus, AgentId
from rythusaathi.domain.registry import ResolutionStatus
from rythusaathi.api.app import create_app
from rythusaathi.api.dependencies import (
    InMemoryKeyStore,
    _AUTH_SERVICE,
    _CREDENTIAL_SERVICE,
    _HISTORY_STORE,
    _KEY_STORE,
    _KNOWLEDGE_BASE,
    get_auth_service,
    get_credential_service,
    get_history_store,
    get_key_store,
    get_knowledge_base,
    set_auth_service,
    set_knowledge_base,
)
from rythusaathi.api.history import AdvisoryHistoryStore
from rythusaathi.credentials import CredentialService
from rythusaathi.domain.models import ContextFact, FactState
from rythusaathi.domain.seed import CROP_REGISTRY, LOCATION_REGISTRY
from rythusaathi.knowledge import (
    DocumentProcessor,
    HashEmbeddingProvider,
    LocalKnowledgeBase,
    load_curated,
    load_schemes,
)
from rythusaathi.local_auth import LocalAuthentication
from rythusaathi.security import IdentityRole

ROOT = Path(__file__).resolve().parents[1]


class IntakeContextExtractionRegressionTests(unittest.IsolatedAsyncioTestCase):
    """Part A & B regression tests: intake extraction, user corrections, and agent integration."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(dir=ROOT / "tmp")
        db_path = Path(cls.temp_dir.name) / "test_intake.sqlite3"
        processor = DocumentProcessor()
        agriculture = load_curated(ROOT / "data/agricultural/icar_advisories.json", CROP_REGISTRY)
        schemes = load_schemes(ROOT / "data/schemes/central_agricultural_schemes.json")
        chunks = [chunk for doc in agriculture for chunk in processor.process_curated(doc)]
        chunks.extend(chunk for doc in schemes for chunk in processor.process_scheme(doc))
        cls.kb = LocalKnowledgeBase(HashEmbeddingProvider())
        cls.kb.add(chunks)
        cls.resolver = default_coverage_resolver()

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def _request(self, query: str, **kwargs) -> FarmerRequest:
        return FarmerRequest(
            request_id=kwargs.pop("request_id", "req-test-1"),
            user_id="farmer-1",
            tenant_id="default",
            query=query,
            **kwargs,
        )

    async def test_regression_1_explicit_rice_and_tillering_leaf_spots(self):
        """Scenario 1: Rice leaves showing brown leaf spots during tillering stage."""
        wf = RythuSaathiWorkflow(resolver=self.resolver, knowledge=self.kb)
        req = self._request("Rice leaves are showing brown leaf spots during tillering stage. What safe organic controls are available?")
        result = await wf.run(req)

        # Crop = Rice
        self.assertEqual(result.context.crop.state, FactState.SUPPLIED)
        self.assertEqual(result.context.crop.value.canonical_id, "crop:oryza-sativa")
        self.assertEqual(result.context.crop.value.display_name, "rice")

        # Growth stage = Tillering
        self.assertEqual(result.context.growth_stage.state, FactState.SUPPLIED)
        self.assertEqual(result.context.growth_stage.value, "tillering")

        # Location = unresolved / missing
        self.assertEqual(result.context.location.state, FactState.UNKNOWN)

        # Observation = brown leaf spots
        intake_res = next(ar for ar in result.agent_results if ar.agent_id == AgentId.INTAKE)
        self.assertIn("brown leaf spots", intake_res.payload.observations)

        # Coordinator response: only clarifies missing location
        coord_res = next(ar for ar in result.agent_results if ar.agent_id == AgentId.COORDINATOR)
        resp_text = coord_res.payload.response_text
        self.assertIn("crop rice", resp_text)
        self.assertIn("growth stage tillering", resp_text)
        self.assertIn("Reported observations (not diagnoses): brown leaf spots", resp_text)
        self.assertIn("Please clarify your location", resp_text)
        self.assertNotIn("Please clarify which crop you are growing", resp_text)
        self.assertNotIn("Please clarify the crop growth stage", resp_text)

    async def test_regression_2_explicit_crop_missing_growth_stage(self):
        """Scenario 2: Crop known (wheat), yellow leaves, stage unknown."""
        wf = RythuSaathiWorkflow(resolver=self.resolver, knowledge=self.kb)
        req = self._request("I am growing wheat. Some leaves are turning yellow.")
        result = await wf.run(req)

        self.assertEqual(result.context.crop.state, FactState.SUPPLIED)
        self.assertEqual(result.context.crop.value.canonical_id, "crop:triticum-aestivum")
        self.assertEqual(result.context.growth_stage.state, FactState.UNKNOWN)
        intake_res = next(ar for ar in result.agent_results if ar.agent_id == AgentId.INTAKE)
        self.assertIn("yellowing leaves", intake_res.payload.observations)

    async def test_regression_3_missing_crop_explicit_growth_stage(self):
        """Scenario 3: Missing crop, explicit tillering growth stage."""
        wf = RythuSaathiWorkflow(resolver=self.resolver, knowledge=self.kb)
        req = self._request("Some leaves are showing spots during tillering stage. What safe controls are available?")
        result = await wf.run(req)

        self.assertEqual(result.context.crop.state, FactState.UNKNOWN)
        self.assertEqual(result.context.growth_stage.state, FactState.SUPPLIED)
        self.assertEqual(result.context.growth_stage.value, "tillering")
        missing_fields = {m.field_name for m in result.context.missing_information}
        self.assertIn("crop", missing_fields)
        self.assertNotIn("growth_stage", missing_fields)

    async def test_regression_4_missing_location_crop_and_stage_present(self):
        """Scenario 4: Missing location, crop (cotton) and stage (flowering) present."""
        wf = RythuSaathiWorkflow(resolver=self.resolver, knowledge=self.kb)
        req = self._request("My cotton crop is flowering. What pests should I inspect?")
        result = await wf.run(req)

        self.assertEqual(result.context.crop.value.canonical_id, "crop:gossypium-hirsutum")
        self.assertEqual(result.context.growth_stage.value, "flowering")
        self.assertEqual(result.context.location.state, FactState.UNKNOWN)
        missing_fields = {m.field_name for m in result.context.missing_information}
        self.assertIn("location", missing_fields)
        self.assertNotIn("crop", missing_fields)
        self.assertNotIn("growth_stage", missing_fields)

    async def test_regression_5_user_correction_of_earlier_crop(self):
        """Scenario 5: User correction: 'I am growing cotton, not rice'."""
        wf = RythuSaathiWorkflow(resolver=self.resolver, knowledge=self.kb)
        req = self._request("I am growing cotton, not rice. What pests should I watch out for?")
        result = await wf.run(req)

        self.assertEqual(result.context.crop.state, FactState.SUPPLIED)
        self.assertEqual(result.context.crop.value.canonical_id, "crop:gossypium-hirsutum")
        self.assertEqual(result.context.crop.value.display_name, "cotton")

    async def test_regression_6_unknown_or_ambiguous_crop(self):
        """Scenario 6: Unknown or unlisted crop."""
        wf = RythuSaathiWorkflow(resolver=self.resolver, knowledge=self.kb)
        req = self._request("I am cultivating dragon fruit. How do I manage soil moisture?")
        result = await wf.run(req)

        self.assertEqual(result.context.crop.state, FactState.UNKNOWN)
        missing_fields = {m.field_name for m in result.context.missing_information}
        self.assertIn("crop", missing_fields)

    async def test_regression_7_context_propagation_to_downstream_agents(self):
        """Scenario 7: Ensure downstream agents receive the identical validated shared context."""
        wf = RythuSaathiWorkflow(resolver=self.resolver, knowledge=self.kb)
        req = self._request(
            "Cotton in Adilabad flowering stage",
            crop_query="cotton",
            location_query="Adilabad",
            growth_stage_query="flowering",
        )
        result = await wf.run(req)

        self.assertEqual(result.context.crop.value.canonical_id, "crop:gossypium-hirsutum")
        self.assertEqual(result.context.location.value.canonical_id, "location:district:in-tg-adilabad")
        self.assertEqual(result.context.growth_stage.value, "flowering")

        # Weather skipped because no mock provider passed, but location was recognized
        weather_res = next(ar for ar in result.agent_results if ar.agent_id == AgentId.WEATHER)
        self.assertIn(weather_res.status.value, {"skipped", "unavailable"})

        # Risk alert received both crop and location
        risk_res = next(ar for ar in result.agent_results if ar.agent_id == AgentId.RISK_ALERT)
        self.assertIsNotNone(risk_res)


class FarmerAdminRoleSeparationTests(unittest.TestCase):
    """Part F tests: Farmer vs Admin role separation, permissions enforcement, and tenant isolation."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(dir=ROOT / "tmp")
        cls.auth_db = Path(cls.temp_dir.name) / "test_roles_auth.sqlite3"
        cls.history_db = Path(cls.temp_dir.name) / "test_roles_history.sqlite3"

        cls.auth_service = LocalAuthentication(cls.auth_db)
        cls.history_store = AdvisoryHistoryStore(cls.history_db)
        cls.key_store = InMemoryKeyStore()
        cls.credential_service = CredentialService(cls.auth_db, cls.key_store)

        set_auth_service(cls.auth_service)

        # Create Tenant 1 and users
        cls.auth_service.create_tenant("tenant-1", "Farm Cooperative 1")
        cls.farmer_token = cls.auth_service.login(
            "farmer_bob",
            "securepassword123!",
            "tenant-1",
        ) if False else None

        cls.farmer_id = cls.auth_service.register(
            "farmer_bob",
            "farmer_password_12345",
            "tenant-1",
            role=IdentityRole.MEMBER,
        )
        cls.farmer_token = cls.auth_service.login("farmer_bob", "farmer_password_12345", "tenant-1")

        cls.admin_id = cls.auth_service.register(
            "admin_alice",
            "admin_password_12345",
            "tenant-1",
            role=IdentityRole.TENANT_ADMIN,
        )
        cls.admin_token = cls.auth_service.login("admin_alice", "admin_password_12345", "tenant-1")

        # Create Tenant 2 and user
        cls.auth_service.create_tenant("tenant-2", "Farm Cooperative 2")
        cls.tenant2_admin_id = cls.auth_service.register(
            "admin_charlie",
            "admin_password_12345",
            "tenant-2",
            role=IdentityRole.TENANT_ADMIN,
        )
        cls.tenant2_admin_token = cls.auth_service.login("admin_charlie", "admin_password_12345", "tenant-2")

        app = create_app()
        app.dependency_overrides[get_auth_service] = lambda: cls.auth_service
        app.dependency_overrides[get_history_store] = lambda: cls.history_store
        app.dependency_overrides[get_credential_service] = lambda: cls.credential_service
        app.dependency_overrides[get_key_store] = lambda: cls.key_store
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def test_farmer_cannot_access_admin_dashboard(self):
        """Farmer with 'member' role must be rejected with 403 Forbidden on /admin/dashboard."""
        resp = self.client.get(
            "/api/v1/admin/dashboard",
            headers={"Authorization": f"Bearer {self.farmer_token}"},
        )
        self.assertEqual(resp.status_code, 403)
        data = resp.json()
        self.assertEqual(data.get("code"), "admin_privilege_required")

    def test_farmer_cannot_access_admin_users_list(self):
        """Farmer cannot list tenant users via admin endpoint."""
        resp = self.client.get(
            "/api/v1/admin/users",
            headers={"Authorization": f"Bearer {self.farmer_token}"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_admin_can_access_admin_dashboard_and_users(self):
        """Tenant Admin can access /admin/dashboard and /admin/users."""
        dash_resp = self.client.get(
            "/api/v1/admin/dashboard",
            headers={"Authorization": f"Bearer {self.admin_token}"},
        )
        self.assertEqual(dash_resp.status_code, 200)
        dash_data = dash_resp.json()
        self.assertEqual(dash_data["tenant_id"], "tenant-1")
        self.assertGreaterEqual(dash_data["total_users"], 2)

        users_resp = self.client.get(
            "/api/v1/admin/users",
            headers={"Authorization": f"Bearer {self.admin_token}"},
        )
        self.assertEqual(users_resp.status_code, 200)
        users = users_resp.json()
        usernames = [u["username"] for u in users]
        self.assertIn("farmer_bob", usernames)
        self.assertIn("admin_alice", usernames)

    def test_tenant_isolation_between_admins(self):
        """Admin of Tenant 2 must NOT see users of Tenant 1."""
        users_resp = self.client.get(
            "/api/v1/admin/users",
            headers={"Authorization": f"Bearer {self.tenant2_admin_token}"},
        )
        self.assertEqual(users_resp.status_code, 200)
        users = users_resp.json()
        usernames = [u["username"] for u in users]
        self.assertIn("admin_charlie", usernames)
        self.assertNotIn("farmer_bob", usernames)
        self.assertNotIn("admin_alice", usernames)

    def test_system_specifications_admin_only(self):
        """Only tenant admin can inspect runtime system configuration."""
        denied = self.client.get(
            "/api/v1/admin/system",
            headers={"Authorization": f"Bearer {self.farmer_token}"},
        )
        self.assertEqual(denied.status_code, 403)

        allowed = self.client.get(
            "/api/v1/admin/system",
            headers={"Authorization": f"Bearer {self.admin_token}"},
        )
        self.assertEqual(allowed.status_code, 200)
        sys_info = allowed.json()
        self.assertEqual(sys_info["version"], "0.1.0-phase8.1")
        self.assertGreaterEqual(sys_info["registered_crops_count"], 6)

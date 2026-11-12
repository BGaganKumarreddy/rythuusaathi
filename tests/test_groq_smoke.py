"""Local unit and regression tests for scripts/groq_smoke.py."""

from __future__ import annotations

from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx as httpx2
from openai import AsyncOpenAI

from agent_framework import Message
from agent_framework.openai import OpenAIChatCompletionClient

from rythusaathi.agents.contracts import (
    AdvisoryResponse,
    AdvisoryStatus,
    AgentId,
    AgentResult,
    AgentStatus,
    AgriculturalRecommendation,
    BoardState,
    WorkflowMode,
)
from rythusaathi.agents.llm import (
    AuthenticatedGroqInference,
    IntakeInterpretation,
)
from rythusaathi.credentials import CredentialService
from rythusaathi.domain import (
    ContentReference,
    EvidenceMode,
    EvidenceRecord,
    EvidenceSource,
    EvidenceSourceType,
    EvidenceStatus,
    FactState,
    RecommendationCategory,
    RecommendationSupport,
    ReferenceKind,
)
from rythusaathi.domain.models import ContextFact, EntityReference, RequestContext
from rythusaathi.inference.contracts import (
    InferenceDiagnostic,
    InferenceDiagnosticCode,
    InferenceStage,
    InferenceTokenUsage,
    ProviderCredential,
    ProviderFailure,
    ProviderFailureKind,
)
from rythusaathi.inference.providers.groq import (
    DEFAULT_MODEL_ID,
    GroqProvider,
    GroqProviderConfig,
)
from rythusaathi.inference.service import AuthenticatedGroqService
from rythusaathi.local_auth import LocalAuthentication

from scripts.groq_smoke import (
    GroqExperimentResult,
    print_groq_experiment_report,
    run_groq_minimal_probe,
    run_groq_structured_intake,
    setup_authenticated_groq_inference,
)


class MemoryKeys:
    """In-memory key store for unit tests without DPAPI dependency."""

    def __init__(self):
        self.value = None

    def get_or_create_key(self, version):
        if self.value is None:
            import secrets
            self.value = secrets.token_bytes(32)
        return self.value

    def get_key(self, version):
        if self.value is None:
            raise RuntimeError("missing test KEK")
        return self.value


def _completion(content: str, model: str = DEFAULT_MODEL_ID) -> dict:
    return {
        "id": "chatcmpl-mock-groq",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
    }


def _make_mock_sdk_factory(handler):
    def factory(api_key: str, base_url: str, timeout: float) -> AsyncOpenAI:
        transport = httpx2.MockTransport(handler)
        http_client = httpx2.AsyncClient(transport=transport, timeout=timeout)
        return AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=0,
            http_client=http_client,
        )
    return factory


class GroqSmokeLocalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Path(self.tmp.name) / "test-auth.sqlite3"
        self.auth = LocalAuthentication(self.database)
        self.auth.create_tenant("tenant-test", "Test Tenant")
        self.owner_id = self.auth.register("groq_tester", "groq-password-123", "tenant-test")
        self.keys = MemoryKeys()
        self.cred_service = CredentialService(self.database, self.keys)
        identity = self.auth.authenticate(self.auth.login("groq_tester", "groq-password-123", "tenant-test"))
        self.status = self.cred_service.create(identity, "groq", "gsk-mock-key-for-test-0123456789")
        self.credential_id = self.status.credential_id

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _build_inference(self, handler) -> AuthenticatedGroqInference:
        prov = GroqProvider(
            GroqProviderConfig(tpm_limit_tokens=8000, tpm_headroom_tokens=800),
            sdk_client_factory=_make_mock_sdk_factory(handler),
        )
        service = AuthenticatedGroqService(self.auth, self.cred_service, prov)
        session_token = self.auth.login("groq_tester", "groq-password-123", "tenant-test")
        return AuthenticatedGroqInference(service, session_token, self.credential_id)

    # 1. Credential setup and auto-discovery
    def test_setup_authenticated_groq_inference(self):
        inference, user_id, tenant_id, token, auth = setup_authenticated_groq_inference(
            self.database,
            tenant_id="tenant-test",
            username="groq_tester",
            password="groq-password-123",
            key_store=self.keys,
        )
        try:
            self.assertEqual(user_id, self.owner_id)
            self.assertEqual(tenant_id, "tenant-test")
            self.assertEqual(inference.provider_id, "groq")
        finally:
            auth.logout(token)

    # 2. Minimal chat completion success
    async def test_run_groq_minimal_probe_success(self):
        async def handler(_request):
            return httpx2.Response(200, json=_completion("OK"))

        inference = self._build_inference(handler)
        result = await run_groq_minimal_probe(
            inference,
            expected_user_id=self.owner_id,
            expected_tenant_id="tenant-test",
        )
        self.assertTrue(result.success)
        self.assertEqual(result.http_status, 200)
        self.assertEqual(result.response_text, "OK")
        self.assertGreater(result.total_tokens_used, 0)
        self.assertIsNone(result.diagnostic)

    # 3. Minimal chat completion failure
    async def test_run_groq_minimal_probe_failure(self):
        async def handler(_request):
            return httpx2.Response(500, json={"error": {"code": 500, "message": "Groq error"}})

        inference = self._build_inference(handler)
        result = await run_groq_minimal_probe(
            inference,
            expected_user_id=self.owner_id,
            expected_tenant_id="tenant-test",
        )
        self.assertFalse(result.success)
        self.assertEqual(result.http_status, 500)
        self.assertIsNotNone(result.diagnostic)

    # 4. Structured intake success
    async def test_run_groq_structured_intake_success(self):
        valid_intake = {
            "crops": [{"value": "cotton", "quote": "cotton"}],
            "locations": [{"value": "Warangal", "quote": "Warangal"}],
            "varieties": [],
            "growth_stages": [],
            "intents": ["crop_advice"],
            "observations": [{"label": "leaf spot", "quote": "leaf spot"}],
            "farming_problem": None,
        }

        async def handler(_request):
            return httpx2.Response(200, json=_completion(json.dumps(valid_intake)))

        inference = self._build_inference(handler)
        result = await run_groq_structured_intake(
            inference,
            expected_user_id=self.owner_id,
            expected_tenant_id="tenant-test",
        )
        self.assertTrue(result.success)
        self.assertEqual(result.http_status, 200)
        self.assertIsNotNone(result.interpretation)
        self.assertEqual(result.interpretation.crops[0].value, "cotton")

    # 5. Structured intake malformed JSON
    async def test_run_groq_structured_intake_malformed(self):
        async def handler(_request):
            return httpx2.Response(200, json=_completion("Not JSON at all"))

        inference = self._build_inference(handler)
        result = await run_groq_structured_intake(
            inference,
            expected_user_id=self.owner_id,
            expected_tenant_id="tenant-test",
        )
        self.assertFalse(result.success)
        self.assertIsNone(result.interpretation)
        self.assertIsNotNone(result.diagnostic)
        self.assertEqual(result.diagnostic.code, InferenceDiagnosticCode.GROQ_MALFORMED_RESPONSE)

    # 6. Report formatting
    def test_print_groq_experiment_report_rendering(self):
        res = GroqExperimentResult(
            stage_name="1. Minimal Chat Completion",
            model_id="openai/gpt-oss-20b",
            success=True,
            http_status=200,
            elapsed_seconds=0.450,
            response_text="OK",
            total_tokens_used=18,
        )
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            print_groq_experiment_report(res)
        out = buf.getvalue()
        self.assertIn("1. Minimal Chat Completion", out)
        self.assertIn("openai/gpt-oss-20b", out)
        self.assertIn("Outcome: SUCCESS", out)
        self.assertIn("HTTP Status: 200", out)
        self.assertIn("Total Tokens Used: 18", out)

    # 7. CLI flags
    @patch("scripts.groq_smoke.setup_authenticated_groq_inference")
    @patch("scripts.groq_smoke.run_groq_minimal_probe")
    @patch("scripts.groq_smoke.run_groq_structured_intake")
    def test_main_cli_flags(self, mock_intake, mock_min, mock_setup):
        from scripts.groq_smoke import main
        mock_auth = MagicMock()
        mock_setup.return_value = (MagicMock(), "user", "tenant", "token", mock_auth)
        mock_min.return_value = GroqExperimentResult(
            stage_name="minimal",
            model_id="openai/gpt-oss-20b",
            success=True,
            http_status=200,
            elapsed_seconds=0.2,
            total_tokens_used=10,
        )
        with patch("sys.argv", ["groq_smoke.py", "--database", str(self.database), "--mode", "minimal"]):
            code = main()
            self.assertEqual(code, 0)
            mock_min.assert_called_once()
            mock_intake.assert_not_called()

    # 8. AdvisoryResponse report formatting (Task A regression tests)
    def test_print_groq_experiment_report_with_advisory_response(self):
        """Verify AdvisoryResponse does not raise AttributeError and renders actual contract fields."""
        now = datetime.now(timezone.utc)
        ev = EvidenceRecord(
            evidence_id="ev-1",
            request_id="req-123",
            source=EvidenceSource(
                source_id="src-1",
                source_identity="icar-cotton",
                source_type=EvidenceSourceType.CURATED_AGRICULTURAL_KNOWLEDGE,
                title="ICAR Cotton Advisory",
                reference=ContentReference(kind=ReferenceKind.URL, value="https://example.com/cotton"),
            ),
            excerpt="Apply recommended fungicide for cercospora leaf spot.",
            retrieved_at=now,
            provenance="ICAR knowledgebase",
            mode=EvidenceMode.LIVE,
            status=EvidenceStatus.AVAILABLE,
        )
        rec = AgriculturalRecommendation(
            recommendation_id="rec-1",
            request_id="req-123",
            category=RecommendationCategory.CROP_MANAGEMENT,
            proposed_action="Apply copper oxychloride at 2.5g per litre",
            evidence_ids=("ev-1",),
            reasoning_summary="Curated guidance for cercospora leaf spot management",
            support=RecommendationSupport.EVIDENCE_BACKED,
            confidence=0.85,
            potential_risks=("Avoid spray in heavy wind",),
        )
        ar = AgentResult(
            agent_id=AgentId.CROP,
            status=AgentStatus.COMPLETED,
            started_at=now,
            completed_at=now,
            evidence_ids=("ev-1",),
        )
        ctx = RequestContext(
            request_id="req-123",
            user_id="u1",
            tenant_id="t1",
            crop=ContextFact.supplied(EntityReference(canonical_id="crop:cotton", display_name="Cotton")),
            location=ContextFact.supplied(EntityReference(canonical_id="loc:warangal", display_name="Warangal")),
            growth_stage=ContextFact.supplied("flowering"),
        )
        resp = AdvisoryResponse(
            request_id="req-123",
            status=AdvisoryStatus.COMPLETED,
            response_text="Advisory text synthesized for farmer.",
            context=ctx,
            missing_information=("Please confirm soil moisture level",),
            agent_results=(ar,),
            evidence=(ev,),
            findings=(),
            recommendations=(rec,),
            conflicts=("Minor conflict between historical and seasonal spray recommendations",),
            board_state=BoardState.COMPLETED,
            execution_mode=WorkflowMode.GROQ,
            provider_id="groq",
            model_id="openai/gpt-oss-20b",
        )
        res = GroqExperimentResult(
            stage_name="3. Six-Agent Advisory Workflow",
            model_id="openai/gpt-oss-20b",
            success=True,
            http_status=200,
            elapsed_seconds=1.234,
            response_text=resp.response_text,
            workflow_response=resp,
            total_tokens_used=1200,
        )
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            print_groq_experiment_report(res)
        out = buf.getvalue()
        # Verify no crash, and actual fields are present
        self.assertIn("Workflow Status: completed", out)
        self.assertIn("Execution Mode: groq (provider: groq, model: openai/gpt-oss-20b)", out)
        self.assertIn("Farmer Advisory Response:\nAdvisory text synthesized for farmer.", out)
        self.assertIn("Resolved Context: Crop: Cotton; Location: Warangal; Stage: flowering", out)
        self.assertIn("Agent Execution Results (1 agents):", out)
        self.assertIn("Crop: completed", out)
        self.assertIn("Recommendations (1):", out)
        self.assertIn("Apply copper oxychloride at 2.5g per litre", out)
        self.assertIn("Citations: ev-1", out)
        self.assertIn("Evidence Citations (1):", out)
        self.assertIn("[ev-1] ICAR Cotton Advisory", out)
        self.assertIn("Missing Information / Follow-up Questions (1):", out)
        self.assertIn("Please confirm soil moisture level", out)
        self.assertIn("Conflicts / Warnings (1):", out)
        self.assertIn("Minor conflict between historical and seasonal spray recommendations", out)

    def test_print_groq_experiment_report_with_empty_and_optional_fields(self):
        """Verify AdvisoryResponse with empty collections and None fields renders safely without fabrication."""
        ctx = RequestContext(request_id="req-empty", user_id="u1", tenant_id="t1")
        resp = AdvisoryResponse(
            request_id="req-empty",
            status=AdvisoryStatus.NEEDS_CLARIFICATION,
            response_text="",
            context=ctx,
            missing_information=(),
            agent_results=(),
            evidence=(),
            findings=(),
            recommendations=(),
            conflicts=(),
            board_state=BoardState.OPEN,
            execution_mode=WorkflowMode.DETERMINISTIC,
        )
        res = GroqExperimentResult(
            stage_name="3. Six-Agent Advisory Workflow",
            model_id="openai/gpt-oss-20b",
            success=True,
            http_status=200,
            elapsed_seconds=0.100,
            response_text=resp.response_text,
            workflow_response=resp,
            total_tokens_used=0,
        )
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            print_groq_experiment_report(res)
        out = buf.getvalue()
        self.assertIn("Workflow Status: needs_clarification", out)
        self.assertIn("Execution Mode: deterministic (provider: none, model: none)", out)
        # Empty sections should not be printed
        self.assertNotIn("Recommendations (", out)
        self.assertNotIn("Evidence Citations (", out)
        self.assertNotIn("Missing Information / Follow-up Questions (", out)
        self.assertNotIn("Conflicts / Warnings (", out)

    def test_print_groq_experiment_report_failure_diagnostics(self):
        """Verify failure reporting prints sanitized diagnostic details."""
        diag = InferenceDiagnostic(
            code=InferenceDiagnosticCode.GROQ_RATE_LIMITED,
            provider="groq",
            model_id="openai/gpt-oss-20b",
            stage=InferenceStage.PROVIDER_TRANSPORT,
            http_status=429,
            retry_after_seconds=5.0,
            retry_decision="not_retried",
            budget_decision="reserved",
            budget_remaining_tokens=5000,
            exception_type_chain="RateLimitError",
        )
        res = GroqExperimentResult(
            stage_name="2. Structured Intake",
            model_id="openai/gpt-oss-20b",
            success=False,
            http_status=429,
            elapsed_seconds=0.550,
            diagnostic=diag,
        )
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            print_groq_experiment_report(res)
        out = buf.getvalue()
        self.assertIn("Outcome: FAILURE", out)
        self.assertIn("HTTP Status: 429", out)
        self.assertIn("Diagnostic Code: groq_rate_limited", out)
        self.assertIn("Diagnostic Stage: provider_transport", out)
        self.assertIn("Retry-After: 5.0s", out)
        self.assertIn("Retry Decision: not_retried", out)
        self.assertIn("Budget Decision: reserved", out)
        self.assertIn("Budget Remaining Tokens: 5000", out)


if __name__ == "__main__":
    unittest.main()

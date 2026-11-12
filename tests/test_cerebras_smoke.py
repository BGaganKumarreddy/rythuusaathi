"""Local unit and regression tests for scripts/cerebras_smoke.py."""

from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx as httpx2
from openai import AsyncOpenAI

from agent_framework import Message

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
    AuthenticatedCerebrasInference,
    CoordinatorDraft,
    GroundedStatement,
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
from rythusaathi.inference.providers.cerebras import (
    DEFAULT_MODEL_ID,
    CerebrasProvider,
    CerebrasProviderConfig,
)
from rythusaathi.inference.service import AuthenticatedCerebrasService
from rythusaathi.local_auth import LocalAuthentication

from scripts.cerebras_smoke import (
    CerebrasExperimentResult,
    print_cerebras_experiment_report,
    run_cerebras_coordinator_probe,
    run_cerebras_minimal_probe,
    run_cerebras_workflow,
    setup_authenticated_cerebras_inference,
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
        "id": "chatcmpl-mock-cerebras",
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
        "usage": {
            "prompt_tokens": 20,
            "completion_tokens": 10,
            "total_tokens": 30,
        },
    }


def _make_mock_client_factory(handler):
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


class CerebrasSmokeLocalUnitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._temp_dir.name) / "test-auth.sqlite3"
        self.auth = LocalAuthentication(self.db_path)
        self.key_store = MemoryKeys()
        self.creds = CredentialService(self.db_path, self.key_store)

        # Register tenant and user
        self.tenant_id = "test-tenant"
        self.username = "test-farmer"
        self.password = "ValidPassword123!"
        self.auth.create_tenant(self.tenant_id, "Test Tenant Display")
        self.user_id = self.auth.register(self.username, self.password, self.tenant_id)

        # Store test Cerebras key
        token = self.auth.login(self.username, self.password, self.tenant_id)
        ident = self.auth.authenticate(token)
        self.status = self.creds.create(ident, "cerebras", "csk-test-secret-key-12345")
        self.auth.logout(token)

    def tearDown(self):
        self._temp_dir.cleanup()

    def test_setup_authenticated_cerebras_inference(self):
        inference, user_id, tenant_id, session_token, auth = setup_authenticated_cerebras_inference(
            self.db_path,
            tenant_id=self.tenant_id,
            username=self.username,
            password=self.password,
            key_store=self.key_store,
        )
        try:
            self.assertEqual(user_id, self.user_id)
            self.assertEqual(tenant_id, self.tenant_id)
            self.assertEqual(inference.provider_id, "cerebras")
            self.assertEqual(inference.model_id, "gpt-oss-120b")
        finally:
            auth.logout(session_token)

    async def test_run_cerebras_minimal_probe_success(self):
        async def handle(_request):
            return httpx2.Response(200, json=_completion("OK"))

        prov = CerebrasProvider(sdk_client_factory=_make_mock_client_factory(handle))
        inference, user_id, tenant_id, session_token, auth = setup_authenticated_cerebras_inference(
            self.db_path,
            tenant_id=self.tenant_id,
            username=self.username,
            password=self.password,
            provider=prov,
            key_store=self.key_store,
        )
        try:
            result = await run_cerebras_minimal_probe(
                inference,
                expected_user_id=user_id,
                expected_tenant_id=tenant_id,
            )
            self.assertTrue(result.success)
            self.assertEqual(result.http_status, 200)
            self.assertEqual(result.response_text, "OK")
            self.assertEqual(result.total_tokens_used, 30)
            self.assertTrue(result.structured_validation_passed)
        finally:
            auth.logout(session_token)

    async def test_run_cerebras_minimal_probe_failure(self):
        async def handle(_request):
            return httpx2.Response(401, json={"error": {"message": "Unauthorized"}})

        prov = CerebrasProvider(sdk_client_factory=_make_mock_client_factory(handle))
        inference, user_id, tenant_id, session_token, auth = setup_authenticated_cerebras_inference(
            self.db_path,
            tenant_id=self.tenant_id,
            username=self.username,
            password=self.password,
            provider=prov,
            key_store=self.key_store,
        )
        try:
            result = await run_cerebras_minimal_probe(
                inference,
                expected_user_id=user_id,
                expected_tenant_id=tenant_id,
            )
            self.assertFalse(result.success)
            self.assertEqual(result.http_status, 401)
            self.assertIsNotNone(result.diagnostic)
            self.assertEqual(result.diagnostic.code, InferenceDiagnosticCode.CEREBRAS_AUTHENTICATION_FAILED)
        finally:
            auth.logout(session_token)

    async def test_run_cerebras_coordinator_probe_success(self):
        coord_draft_json = json.dumps({
            "statements": [
                {
                    "text": "Spray copper oxychloride 50 WP for cotton leaf spot.",
                    "kind": "interpretation",
                    "evidence_ids": ["ev:crop:icar-cotton-leafspot-01"],
                    "uncertainty": None,
                },
                {
                    "text": "Weather conditions in Adilabad show high relative humidity.",
                    "kind": "evidence",
                    "evidence_ids": ["ev:weather:open-meteo-adilabad-01"],
                    "uncertainty": None,
                },
            ],
            "follow_up_questions": ["What is the severity of the leaf lesions?"],
        })

        async def handle(_request):
            return httpx2.Response(200, json=_completion(coord_draft_json))

        prov = CerebrasProvider(sdk_client_factory=_make_mock_client_factory(handle))
        inference, user_id, tenant_id, session_token, auth = setup_authenticated_cerebras_inference(
            self.db_path,
            tenant_id=self.tenant_id,
            username=self.username,
            password=self.password,
            provider=prov,
            key_store=self.key_store,
        )
        try:
            result = await run_cerebras_coordinator_probe(
                inference,
                expected_user_id=user_id,
                expected_tenant_id=tenant_id,
            )
            self.assertTrue(result.success)
            self.assertTrue(result.structured_validation_passed)
            self.assertIsNotNone(result.coordinator_draft)
            self.assertEqual(len(result.coordinator_draft.statements), 2)
            self.assertEqual(len(result.coordinator_draft.follow_up_questions), 1)
        finally:
            auth.logout(session_token)

    async def test_run_cerebras_coordinator_probe_invalid_citation(self):
        # Emits a citation not in allowed_ids
        coord_draft_json = json.dumps({
            "statements": [
                {
                    "text": "Hallucinated advice.",
                    "kind": "interpretation",
                    "evidence_ids": ["ev:hallucinated:not-allowed"],
                    "uncertainty": None,
                }
            ],
            "follow_up_questions": [],
        })

        async def handle(_request):
            return httpx2.Response(200, json=_completion(coord_draft_json))

        prov = CerebrasProvider(sdk_client_factory=_make_mock_client_factory(handle))
        inference, user_id, tenant_id, session_token, auth = setup_authenticated_cerebras_inference(
            self.db_path,
            tenant_id=self.tenant_id,
            username=self.username,
            password=self.password,
            provider=prov,
            key_store=self.key_store,
        )
        try:
            result = await run_cerebras_coordinator_probe(
                inference,
                expected_user_id=user_id,
                expected_tenant_id=tenant_id,
            )
            self.assertFalse(result.success)
            self.assertFalse(result.structured_validation_passed)
            self.assertIn("Citation validation failed", result.validation_notes or "")
        finally:
            auth.logout(session_token)

    async def test_run_cerebras_coordinator_probe_fenced_markdown(self):
        coord_draft_fenced = (
            "```json\n"
            "{\n"
            '  "statements": [\n'
            '    {\n'
            '      "text": "Adilabad Cotton has leaf spot; spray copper oxychloride.",\n'
            '      "kind": "evidence",\n'
            '      "evidence_ids": ["ev:crop:icar-cotton-leafspot-01"],\n'
            '      "uncertainty": null\n'
            '    }\n'
            '  ],\n'
            '  "follow_up_questions": []\n'
            "}\n"
            "```"
        )
        async def handle(_request):
            return httpx2.Response(200, json=_completion(coord_draft_fenced))

        prov = CerebrasProvider(sdk_client_factory=_make_mock_client_factory(handle))
        inference, user_id, tenant_id, session_token, auth = setup_authenticated_cerebras_inference(
            self.db_path,
            tenant_id=self.tenant_id,
            username=self.username,
            password=self.password,
            provider=prov,
            key_store=self.key_store,
        )
        try:
            result = await run_cerebras_coordinator_probe(
                inference,
                expected_user_id=user_id,
                expected_tenant_id=tenant_id,
            )
            self.assertTrue(result.success)
            self.assertTrue(result.structured_validation_passed)
            self.assertIsNotNone(result.coordinator_draft)
            self.assertEqual(len(result.coordinator_draft.statements), 1)
        finally:
            auth.logout(session_token)

    async def test_run_cerebras_coordinator_probe_malformed_json_diagnostic(self):
        async def handle(_request):
            return httpx2.Response(200, json=_completion("Not JSON at all"))

        prov = CerebrasProvider(sdk_client_factory=_make_mock_client_factory(handle))
        inference, user_id, tenant_id, session_token, auth = setup_authenticated_cerebras_inference(
            self.db_path,
            tenant_id=self.tenant_id,
            username=self.username,
            password=self.password,
            provider=prov,
            key_store=self.key_store,
        )
        try:
            result = await run_cerebras_coordinator_probe(
                inference,
                expected_user_id=user_id,
                expected_tenant_id=tenant_id,
            )
            self.assertFalse(result.success)
            self.assertEqual(result.diagnostic.code, InferenceDiagnosticCode.CEREBRAS_MALFORMED_RESPONSE)
            self.assertEqual(result.diagnostic.stage, InferenceStage.RESPONSE_VALIDATION)
            self.assertEqual(result.diagnostic.parsing_outcome, "json_decode_failed")
            self.assertEqual(result.http_status, 200)
            self.assertGreater(result.total_tokens_used, 0)
        finally:
            auth.logout(session_token)

    async def test_run_cerebras_coordinator_probe_length_truncation_diagnostic(self):
        truncated_json = '{"statements": [{"text": "Incomplete text'

        async def handle(_request):
            return httpx2.Response(
                200,
                json={
                    "id": "chatcmpl-mock-cerebras",
                    "object": "chat.completion",
                    "created": 1,
                    "model": DEFAULT_MODEL_ID,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": truncated_json},
                            "finish_reason": "length",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1015,
                        "completion_tokens": 2048,
                        "total_tokens": 3063,
                    },
                },
            )

        prov = CerebrasProvider(sdk_client_factory=_make_mock_client_factory(handle))
        inference, user_id, tenant_id, session_token, auth = setup_authenticated_cerebras_inference(
            self.db_path,
            tenant_id=self.tenant_id,
            username=self.username,
            password=self.password,
            provider=prov,
            key_store=self.key_store,
        )
        try:
            result = await run_cerebras_coordinator_probe(
                inference,
                expected_user_id=user_id,
                expected_tenant_id=tenant_id,
            )
            self.assertFalse(result.success)
            self.assertEqual(result.diagnostic.code, InferenceDiagnosticCode.CEREBRAS_MALFORMED_RESPONSE)
            self.assertEqual(result.diagnostic.stage, InferenceStage.RESPONSE_VALIDATION)
            self.assertEqual(result.diagnostic.parsing_outcome, "json_decode_failed")
            self.assertEqual(result.diagnostic.finish_reason, "length")
            self.assertEqual(result.http_status, 200)
            self.assertEqual(result.usage.actual_output_tokens, 2048)
            self.assertEqual(result.usage.finish_reason, "length")

            buf = io.StringIO()
            with patch("sys.stdout", buf):
                print_cerebras_experiment_report(result)
            out = buf.getvalue()
            self.assertIn("Finish Reason: length", out)
            self.assertIn("Parsing Outcome: json_decode_failed", out)
            self.assertIn("Output Truncation: True", out)
        finally:
            auth.logout(session_token)

    def test_print_cerebras_experiment_report(self):
        draft = CoordinatorDraft(
            statements=[
                GroundedStatement(
                    text="Cotton leaf spot advice.",
                    kind="interpretation",
                    evidence_ids=["ev:1"],
                    uncertainty=None,
                )
            ],
            follow_up_questions=["Question 1?"],
        )
        usage = InferenceTokenUsage(
            agent_name="coordinator",
            model_id="gpt-oss-120b",
            correlation_id="c" * 32,
            estimated_input_tokens=100,
            actual_input_tokens=100,
            actual_output_tokens=50,
            actual_total_tokens=150,
            budget_limit_tokens=27000,
            budget_remaining_tokens=26850,
            budget_decision="completed",
        )
        result = CerebrasExperimentResult(
            stage_name="2. Coordinator Synthesis",
            model_id="gpt-oss-120b",
            success=True,
            http_status=200,
            elapsed_seconds=0.450,
            coordinator_draft=draft,
            usage=usage,
            total_tokens_used=150,
            structured_validation_passed=True,
            validation_notes="Validation passed.",
        )
        # Should format and print without raising
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            print_cerebras_experiment_report(result)
        out = buf.getvalue()
        self.assertIn("Provider: cerebras", out)
        self.assertIn("gpt-oss-120b", out)
        self.assertIn("SUCCESS", out)
        self.assertIn("Structured Output Validation:", out)


if __name__ == "__main__":
    unittest.main()


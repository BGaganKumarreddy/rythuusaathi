from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

import httpx2
from agent_framework import Message
from openai import AsyncOpenAI

from rythusaathi.agents import (
    AuthenticatedGeminiInference,
    FarmerRequest,
    RythuSaathiWorkflow,
    WorkflowMode,
    empty_coverage_resolver,
)
from rythusaathi.credentials import CredentialService
from rythusaathi.inference import (
    InferenceDiagnosticCode,
    InferenceRequest,
    ProviderFailure,
    ProviderFailureKind,
)
from rythusaathi.inference.providers.gemini import (
    DEFAULT_MODEL_ID,
    GEMINI_BASE_URL,
    GeminiProvider,
    GeminiProviderConfig,
)
from rythusaathi.inference.service import AuthenticatedGeminiService, AuthenticatedIdentityMismatch
from rythusaathi.local_auth import LocalAuthentication


class MemoryKeys:
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


def _completion(text: str, *, model: str = DEFAULT_MODEL_ID):
    return {
        "id": "chatcmpl-gemini-integration",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


class AuthenticatedGeminiIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "integration_gemini.sqlite3"
        self.auth = LocalAuthentication(self.database)
        self.auth.create_tenant("tenant-one", "Tenant One")
        self.auth.create_tenant("tenant-two", "Tenant Two")
        self.owner_id = self.auth.register("owner", "owner-password-long", "tenant-one")
        self.peer_id = self.auth.register("peer", "peer-password-long", "tenant-one")
        self.other_id = self.auth.register("other", "other-password-long", "tenant-two")
        self.owner_token = self.auth.login("owner", "owner-password-long", "tenant-one")
        self.peer_token = self.auth.login("peer", "peer-password-long", "tenant-one")
        self.other_token = self.auth.login("other", "other-password-long", "tenant-two")
        self.identity = self.auth.authenticate(self.owner_token)
        self.keys = MemoryKeys()
        self.credentials = CredentialService(self.database, self.keys)
        self.seen_authorization = []
        self.seen_payloads = []
        self.service = AuthenticatedGeminiService(
            self.auth, self.credentials, self._provider()
        )

    def tearDown(self):
        self.temp.cleanup()

    def _provider(self):
        async def handle(request):
            self.seen_authorization.append(request.headers.get("authorization"))
            self.seen_payloads.append(request.content.decode("utf-8"))
            return httpx2.Response(200, json=_completion('{"status": "ok"}'))

        def factory(api_key, base_url, timeout):
            client = httpx2.AsyncClient(
                transport=httpx2.MockTransport(handle), timeout=timeout
            )
            return AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
                max_retries=0,
                http_client=client,
            )

        return GeminiProvider(GeminiProviderConfig(), sdk_client_factory=factory)

    def _provider_with_router(self, router_fn):
        async def handle(request):
            self.seen_authorization.append(request.headers.get("authorization"))
            body = request.content.decode("utf-8")
            self.seen_payloads.append(body)
            response_json = router_fn(json.loads(body))
            return httpx2.Response(200, json=_completion(response_json))

        def factory(api_key, base_url, timeout):
            client = httpx2.AsyncClient(
                transport=httpx2.MockTransport(handle), timeout=timeout
            )
            return AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
                max_retries=0,
                http_client=client,
            )

        return GeminiProvider(GeminiProviderConfig(), sdk_client_factory=factory)

    def _request(self):
        return InferenceRequest([Message("user", ["safe prompt"])], agent_name="crop")

    async def test_authenticated_owner_uses_stored_gemini_credential(self):
        key = "test-only-gemini-key-owner-one"
        status = self.credentials.create(self.identity, "gemini", key)
        result = await self.service.complete(
            self.owner_token, status.credential_id, self._request()
        )
        self.assertEqual(result.provider, "gemini")
        self.assertEqual(self.seen_authorization, [f"Bearer {key}"])
        self.assertNotIn(key, json.dumps(result.response.to_dict(exclude_none=False)))
        self.assertNotIn(key, repr(vars(self.service)))

    async def test_identity_mismatch_prevents_dispatch(self):
        key = "test-only-gemini-mismatch-key"
        status = self.credentials.create(self.identity, "gemini", key)
        inference = AuthenticatedGeminiInference(
            self.service, self.owner_token, status.credential_id
        )
        with self.assertRaises(ProviderFailure) as caught:
            await inference.complete(
                self._request(),
                expected_user_id="different-user-id",
                expected_tenant_id="tenant-one",
            )
        self.assertEqual(caught.exception.kind, ProviderFailureKind.AUTHENTICATION)
        self.assertEqual(
            caught.exception.diagnostic.code,
            InferenceDiagnosticCode.IDENTITY_MISMATCH,
        )
        self.assertEqual(self.seen_authorization, [])

    async def test_gemini_workflow_execution_with_structured_output(self):
        class MockKnowledge:
            def search(self, *_args, **_kwargs):
                return ()

        key = "test-only-gemini-workflow-key"
        status = self.credentials.create(self.identity, "gemini", key)

        intake_json = json.dumps({
            "crops": [],
            "locations": [],
            "varieties": [],
            "growth_stages": [],
            "intents": ["weather"],
            "observations": [],
            "farming_problem": None,
        })

        def router(payload):
            # IntakeAgent request
            return intake_json

        service = AuthenticatedGeminiService(
            self.auth, self.credentials, self._provider_with_router(router)
        )
        inference = AuthenticatedGeminiInference(
            service, self.owner_token, status.credential_id
        )

        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(), knowledge=MockKnowledge()
        )
        response = await workflow.run(
            FarmerRequest(
                request_id="gemini-workflow-test-req",
                user_id=self.owner_id,
                tenant_id="tenant-one",
                query="Will it rain today?",
            ),
            mode=WorkflowMode.GEMINI,
            inference=inference,
        )

        self.assertEqual(response.execution_mode, WorkflowMode.GEMINI)
        self.assertEqual(response.provider_id, "gemini")
        self.assertEqual(response.model_id, "gemini-3.8-flash")
        intake_res = next(r for r in response.agent_results if r.agent_id.value == "Intake")
        self.assertEqual(intake_res.payload.interpretation_mode, "gemini")
        self.assertEqual(intake_res.payload.inference_status, "completed")
        self.assertEqual(self.seen_authorization, [f"Bearer {key}"])
        self.assertNotIn(key, response.response_text)
        self.assertNotIn(key, repr(response))
        self.assertNotIn(self.owner_token, response.response_text)

    async def test_gemini_workflow_fallback_on_malformed_llm_json(self):
        class MockKnowledge:
            def search(self, *_args, **_kwargs):
                return ()

        key = "test-only-gemini-malformed-key"
        status = self.credentials.create(self.identity, "gemini", key)

        def router(_payload):
            # Return invalid schema shape for IntakeInterpretation
            return json.dumps({"invalid_field": 12345})

        service = AuthenticatedGeminiService(
            self.auth, self.credentials, self._provider_with_router(router)
        )
        inference = AuthenticatedGeminiInference(
            service, self.owner_token, status.credential_id
        )

        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(), knowledge=MockKnowledge()
        )
        response = await workflow.run(
            FarmerRequest(
                request_id="gemini-malformed-test-req",
                user_id=self.owner_id,
                tenant_id="tenant-one",
                query="Will it rain today?",
            ),
            mode=WorkflowMode.GEMINI,
            inference=inference,
        )

        self.assertEqual(response.execution_mode, WorkflowMode.GEMINI)
        intake_res = next(r for r in response.agent_results if r.agent_id.value == "Intake")
        self.assertEqual(intake_res.payload.interpretation_mode, "deterministic_fallback")
        self.assertEqual(intake_res.payload.inference_status, "malformed_response")
        intake_finding = next(f for f in response.findings if f.agent_id.value == "Intake")
        self.assertIn("Interpretation mode: deterministic_fallback", intake_finding.claim)

    async def test_wrong_provider_credential_rejected(self):
        groq_status = self.credentials.create(self.identity, "groq", "some-groq-key")
        inference = AuthenticatedGeminiInference(
            self.service, self.owner_token, groq_status.credential_id
        )
        with self.assertRaises(ProviderFailure) as caught:
            await inference.complete(
                self._request(),
                expected_user_id=self.owner_id,
                expected_tenant_id="tenant-one",
            )
        self.assertEqual(caught.exception.kind, ProviderFailureKind.AUTHENTICATION)
        self.assertEqual(
            caught.exception.diagnostic.code,
            InferenceDiagnosticCode.CREDENTIAL_UNAVAILABLE,
        )
        self.assertEqual(self.seen_authorization, [])

    async def test_gemini_full_specialist_and_coordinator_synthesis(self):
        from rythusaathi.knowledge import (
            DocumentChunk,
            DocumentMetadata,
            RetrievedChunk,
            SourceReliability,
            SourceType,
        )

        metadata = DocumentMetadata(
            document_id="doc:pau:rice",
            source_id="pau-handbook",
            title="PAU Package of Practices",
            source_type=SourceType.CURATED,
            reliability=SourceReliability.PRIMARY,
            source_identity="pau-handbook",
            reference="https://pau.edu",
        )
        chunk = DocumentChunk(
            chunk_id="chunk:pau:rice:nitrogen",
            parent_document_id="doc:pau:rice",
            source=metadata,
            text="Nitrogen deficiency causes uniform yellowing of older leaves in rice in Punjab.",
            content_hash="hash123",
            crop_id="crop:oryza-sativa",
            variety_id=None,
            growth_stage=None,
            topic=None,
            geographic_applicability=("location:state:in-pb",),
            page=None,
            section=None,
        )
        retrieved_chunk = RetrievedChunk(chunk=chunk, similarity=0.95, retrieved_at=datetime.now(timezone.utc))

        class SingleHitKnowledge:
            def search(self, *_args, **_kwargs):
                return (retrieved_chunk,)

        key = "test-only-gemini-full-key"
        status = self.credentials.create(self.identity, "gemini", key)

        intake_json = json.dumps({
            "crops": [{"value": "rice", "quote": "rice"}],
            "locations": [{"value": "Punjab", "quote": "Punjab"}],
            "varieties": [],
            "growth_stages": [],
            "intents": ["disease_or_pest"],
            "observations": [{"label": "yellowing", "quote": "yellowing"}],
            "farming_problem": {"label": "yellowing", "quote": "yellowing"},
        })

        crop_json = json.dumps({
            "summary": "Nitrogen deficiency is suspected based on PAU evidence.",
            "statements": [
                {
                    "text": "Nitrogen deficiency causes uniform yellowing of older leaves in rice in Punjab.",
                    "kind": "interpretation",
                    "evidence_ids": ["chunk:pau:rice:nitrogen"],
                    "uncertainty": None,
                }
            ],
        })

        coordinator_json = json.dumps({
            "statements": [
                {
                    "text": "Rice crops with yellowing leaves in Punjab often show nitrogen deficiency.",
                    "kind": "interpretation",
                    "evidence_ids": ["chunk:pau:rice:nitrogen"],
                    "uncertainty": None,
                }
            ],
            "follow_up_questions": [
                "What growth stage is the crop currently in?"
            ],
        })

        def router(payload):
            schema_name = payload.get("response_format", {}).get("json_schema", {}).get("name")
            if not schema_name:
                user_msg = json.loads(payload["messages"][1]["content"])
                schema_name = user_msg["required_json_schema"]["title"]
            if schema_name == "IntakeInterpretation":
                return intake_json
            elif schema_name == "SpecialistInterpretation":
                return crop_json
            elif schema_name == "CoordinatorDraft":
                return coordinator_json
            raise ValueError(f"Unexpected schema: {schema_name}")

        service = AuthenticatedGeminiService(
            self.auth, self.credentials, self._provider_with_router(router)
        )
        inference = AuthenticatedGeminiInference(
            service, self.owner_token, status.credential_id
        )

        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(), knowledge=SingleHitKnowledge()
        )
        response = await workflow.run(
            FarmerRequest(
                request_id="gemini-full-synthesis-req",
                user_id=self.owner_id,
                tenant_id="tenant-one",
                query="My rice leaves are yellowing in Punjab. What should I check?",
            ),
            mode=WorkflowMode.GEMINI,
            inference=inference,
        )

        self.assertEqual(response.execution_mode, WorkflowMode.GEMINI)
        self.assertEqual(response.provider_id, "gemini")
        self.assertEqual(response.model_id, "gemini-3.8-flash")
        coordinator_res = next(r for r in response.agent_results if r.agent_id.value == "Coordinator")
        self.assertEqual(coordinator_res.payload.interpretation_mode, "gemini")
        self.assertEqual(coordinator_res.payload.inference_status, "completed")
        self.assertIn("Gemini-generated synthesis (interpretation, not a source statement):", response.response_text)
        self.assertIn("Rice crops with yellowing leaves in Punjab often show nitrogen deficiency. [chunk:pau:rice:nitrogen]", response.response_text)
        self.assertIn("- Clarification: What growth stage is the crop currently in?", response.response_text)

        # Verify output limits and structured output schema dispatched to endpoint:
        dispatched_payloads = [json.loads(p) for p in self.seen_payloads]
        self.assertEqual(len(dispatched_payloads), 3)
        self.assertEqual(dispatched_payloads[0].get("max_completion_tokens", dispatched_payloads[0].get("max_tokens")), 1024)
        self.assertNotIn("reasoning_effort", dispatched_payloads[0])
        self.assertEqual(dispatched_payloads[0]["response_format"], {"type": "json_object"})

        self.assertEqual(dispatched_payloads[1].get("max_completion_tokens", dispatched_payloads[1].get("max_tokens")), 1500)
        self.assertNotIn("reasoning_effort", dispatched_payloads[1])
        self.assertEqual(dispatched_payloads[1]["response_format"], {"type": "json_object"})

        self.assertEqual(dispatched_payloads[2].get("max_completion_tokens", dispatched_payloads[2].get("max_tokens")), 1500)
        self.assertNotIn("reasoning_effort", dispatched_payloads[2])
        self.assertEqual(dispatched_payloads[2]["response_format"], {"type": "json_object"})


if __name__ == "__main__":
    unittest.main()

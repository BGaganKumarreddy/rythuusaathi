"""Tests for real-time workflow progress, timeout configurations, and observability."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx2
from agent_framework import Message
from openai import AsyncOpenAI, Timeout

from rythusaathi.agents import (
    AdvisoryResponse,
    AdvisoryStatus,
    AgentResult,
    AgentStatus,
    AuthenticatedGeminiInference,
    DEFAULT_WORKFLOW_DEADLINE_SECONDS,
    DeterministicWeatherFixture,
    FarmerRequest,
    RythuSaathiWorkflow,
    WorkflowMode,
    empty_coverage_resolver,
)
from rythusaathi.agents.llm import (
    IntakeInterpretation,
    call_structured,
    make_json_request,
)
from rythusaathi.domain import AgentId
from rythusaathi.inference import (
    InferenceDiagnostic,
    InferenceDiagnosticCode,
    InferenceRequest,
    InferenceStage,
    ProviderCredential,
    ProviderFailure,
    ProviderFailureKind,
)
from rythusaathi.inference.providers.gemini import (
    DEFAULT_MODEL_ID,
    GEMINI_BASE_URL,
    GeminiProvider,
    GeminiProviderConfig,
)
from rythusaathi.inference.service import AuthenticatedGeminiService
from rythusaathi.local_auth import LocalAuthentication
from rythusaathi.knowledge import (
    DocumentProcessor,
    HashEmbeddingProvider,
    PersistentKnowledgeBase,
    load_curated,
    load_schemes,
)
from rythusaathi.domain.seed import CROP_REGISTRY
import scripts.advisory_cli as advisory_cli


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


def _mock_sdk_factory(handler, captures=None, clients=None):
    def create(api_key: str, base_url: str, timeout: float | Timeout) -> AsyncOpenAI:
        if captures is not None:
            captures.append({"auth_seen": bool(api_key), "base_url": base_url, "timeout": timeout})
        transport = httpx2.MockTransport(handler)
        http_client = httpx2.AsyncClient(transport=transport, timeout=timeout)
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=0,
            http_client=http_client,
        )
        if clients is not None:
            clients.append(client)
        return client
    return create


class WorkflowObservabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "observability.sqlite3"
        self.knowledge = PersistentKnowledgeBase(self.db_path, HashEmbeddingProvider())
        self.resolver = empty_coverage_resolver()

    async def asyncTearDown(self):
        self.knowledge.close()
        self.temp_dir.cleanup()

    def _sample_request(self, query: str = "My rice leaves are yellow in Punjab.") -> FarmerRequest:
        return FarmerRequest(
            request_id="obs-req-1",
            user_id="user-1",
            tenant_id="tenant-1",
            query=query,
        )

    async def test_workflow_progress_events_emitted_in_real_time(self):
        events: list[str] = []
        workflow = RythuSaathiWorkflow(
            resolver=self.resolver,
            knowledge=self.knowledge,
            weather_provider=DeterministicWeatherFixture(),
        )
        request = self._sample_request()
        response = await workflow.run(
            request,
            mode=WorkflowMode.DETERMINISTIC,
            on_progress=events.append,
        )
        self.assertIsInstance(response, AdvisoryResponse)

        # Intake lifecycle
        self.assertIn("Intake started", events)
        self.assertIn("Intake completed", events)

        # Branch agents lifecycles
        self.assertIn("Crop started", events)
        self.assertIn("Crop completed", events)
        self.assertIn("Scheme started", events)
        self.assertIn("Scheme completed", events)
        self.assertIn("Weather started", events)
        self.assertIn("Weather completed", events)
        self.assertIn("Market started", events)
        self.assertIn("Market unavailable", events)

        # Coordinator lifecycle
        self.assertIn("Coordinator started", events)
        self.assertIn("Coordinator completed", events)

        # Intake precedes branches, and branches precede Coordinator
        intake_idx = events.index("Intake completed")
        coordinator_idx = events.index("Coordinator started")
        self.assertLess(intake_idx, coordinator_idx)

        # Check no raw secrets or prompt leaked in progress messages
        for event_msg in events:
            self.assertNotIn("yellow", event_msg)
            self.assertNotIn("obs-req-1", event_msg)
            self.assertNotIn("secret", event_msg.lower())

    async def test_parallel_specialist_progress_ordering(self):
        events: list[str] = []
        workflow = RythuSaathiWorkflow(
            resolver=self.resolver,
            knowledge=self.knowledge,
        )
        request = self._sample_request()
        await workflow.run(
            request,
            mode=WorkflowMode.DETERMINISTIC,
            on_progress=events.append,
        )

        # Fan-out events for Crop and Scheme are logged as they arrive
        crop_start = events.index("Crop started")
        scheme_start = events.index("Scheme started")
        self.assertTrue(crop_start >= 0 and scheme_start >= 0)

    def test_granular_timeout_configuration_and_environment_overrides(self):
        # 1. Defaults
        config = GeminiProviderConfig()
        self.assertEqual(config.timeout_seconds, 60.0)
        self.assertEqual(config.connect_timeout_seconds, 10.0)
        self.assertEqual(config.read_timeout_seconds, 60.0)
        self.assertEqual(config.write_timeout_seconds, 10.0)
        self.assertEqual(config.pool_timeout_seconds, 10.0)

        # 2. Free tier preset retains timeouts
        free_config = GeminiProviderConfig.free_tier()
        self.assertEqual(free_config.connect_timeout_seconds, 10.0)
        self.assertEqual(free_config.read_timeout_seconds, 60.0)

        # 3. Environment overrides
        env = {
            "GEMINI_TIMEOUT_SECONDS": "45.0",
            "GEMINI_CONNECT_TIMEOUT_SECONDS": "12.5",
            "GEMINI_READ_TIMEOUT_SECONDS": "50.0",
            "GEMINI_WRITE_TIMEOUT_SECONDS": "8.0",
            "GEMINI_POOL_TIMEOUT_SECONDS": "5.0",
        }
        with patch.dict("os.environ", env):
            env_config = GeminiProviderConfig.from_environment()
            self.assertEqual(env_config.timeout_seconds, 45.0)
            self.assertEqual(env_config.connect_timeout_seconds, 12.5)
            self.assertEqual(env_config.read_timeout_seconds, 50.0)
            self.assertEqual(env_config.write_timeout_seconds, 8.0)
            self.assertEqual(env_config.pool_timeout_seconds, 5.0)

        # 4. Timeout object passed to SDK client factory
        captures = []
        async def handler(_request):
            return httpx2.Response(
                200,
                json={
                    "id": "chatcmpl-timeout-test",
                    "created": 1,
                    "model": DEFAULT_MODEL_ID,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                },
            )

        provider = GeminiProvider(
            GeminiProviderConfig(
                timeout_seconds=50.0,
                connect_timeout_seconds=7.0,
                read_timeout_seconds=40.0,
                write_timeout_seconds=6.0,
                pool_timeout_seconds=9.0,
            ),
            sdk_client_factory=_mock_sdk_factory(handler, captures),
        )

        async def run_req():
            return await provider.complete(
                InferenceRequest([Message("user", ["hi"])], agent_name="crop"),
                ProviderCredential("dummy-test-key"),
            )

        resp = asyncio.run(run_req())
        self.assertEqual(resp.provider, "gemini")
        self.assertEqual(len(captures), 1)
        passed_timeout = captures[0]["timeout"]
        self.assertIsInstance(passed_timeout, Timeout)
        self.assertEqual(passed_timeout.connect, 7.0)
        self.assertEqual(passed_timeout.read, 40.0)
        self.assertEqual(passed_timeout.write, 6.0)
        self.assertEqual(passed_timeout.pool, 9.0)

    async def test_connect_and_read_timeout_classification(self):
        # 1. Connect timeout
        async def connect_timeout_handler(_request):
            raise httpx2.ConnectTimeout("connection timed out")

        provider_connect = GeminiProvider(
            GeminiProviderConfig(),
            sdk_client_factory=_mock_sdk_factory(connect_timeout_handler),
        )
        with self.assertRaises(ProviderFailure) as caught_connect:
            await provider_connect.complete(
                InferenceRequest([Message("user", ["hi"])], agent_name="crop"),
                ProviderCredential("conn-key"),
            )
        self.assertEqual(caught_connect.exception.kind, ProviderFailureKind.TIMEOUT)
        self.assertEqual(
            caught_connect.exception.diagnostic.code,
            InferenceDiagnosticCode.GEMINI_CONNECT_TIMEOUT,
        )
        self.assertEqual(caught_connect.exception.diagnostic.stage, InferenceStage.PROVIDER_TRANSPORT)
        self.assertIsNotNone(caught_connect.exception.diagnostic.elapsed_seconds)
        self.assertGreaterEqual(caught_connect.exception.diagnostic.elapsed_seconds, 0.0)

        # 2. Read timeout
        async def read_timeout_handler(_request):
            raise httpx2.ReadTimeout("read timed out")

        provider_read = GeminiProvider(
            GeminiProviderConfig(),
            sdk_client_factory=_mock_sdk_factory(read_timeout_handler),
        )
        with self.assertRaises(ProviderFailure) as caught_read:
            await provider_read.complete(
                InferenceRequest([Message("user", ["hi"])], agent_name="scheme"),
                ProviderCredential("read-key"),
            )
        self.assertEqual(caught_read.exception.kind, ProviderFailureKind.TIMEOUT)
        self.assertEqual(
            caught_read.exception.diagnostic.code,
            InferenceDiagnosticCode.GEMINI_READ_TIMEOUT,
        )
        self.assertEqual(caught_read.exception.diagnostic.stage, InferenceStage.PROVIDER_TRANSPORT)

    async def test_overall_workflow_deadline_and_fallback(self):
        workflow = RythuSaathiWorkflow(
            resolver=self.resolver,
            knowledge=self.knowledge,
        )

        class HangingInference:
            def __init__(self):
                self.diagnostics = []
                self.model_id = "gemini-3.8-flash"

            async def complete(self, *args, **kwargs):
                await asyncio.sleep(5.0)
                raise asyncio.CancelledError()

            def _record_diagnostic(self, d):
                self.diagnostics.append(d)

        hanging_inf = HangingInference()
        request = self._sample_request()

        # Set tight deadline of 0.05 seconds
        start_t = asyncio.get_event_loop().time()
        response = await workflow.run(
            request,
            mode=WorkflowMode.GEMINI,
            inference=hanging_inf,
            deadline_seconds=0.05,
        )
        elapsed = asyncio.get_event_loop().time() - start_t
        self.assertLess(elapsed, 1.5)
        self.assertIsInstance(response, AdvisoryResponse)
        self.assertIn(response.status, {AdvisoryStatus.CANCELLED, AdvisoryStatus.PARTIAL, AdvisoryStatus.NEEDS_CLARIFICATION})

        # Check timeout diagnostic recorded
        self.assertEqual(len(hanging_inf.diagnostics), 1)
        self.assertEqual(hanging_inf.diagnostics[0].code, InferenceDiagnosticCode.WORKFLOW_TIMEOUT)
        self.assertEqual(hanging_inf.diagnostics[0].stage, InferenceStage.WORKFLOW_EXECUTION)
        self.assertEqual(hanging_inf.diagnostics[0].provider, "gemini")

    async def test_sanitized_token_usage_metadata_on_success(self):
        async def success_handler(_request):
            return httpx2.Response(
                200,
                headers={"x-request-id": "gemini-req-header-xyz"},
                json={
                    "id": "chatcmpl-gemini-success-999",
                    "created": 1,
                    "model": DEFAULT_MODEL_ID,
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": "Sample advice"},
                        "finish_reason": "stop",
                    }],
                    "usage": {"prompt_tokens": 42, "completion_tokens": 18, "total_tokens": 60},
                },
            )

        provider = GeminiProvider(
            GeminiProviderConfig(),
            sdk_client_factory=_mock_sdk_factory(success_handler),
        )
        resp = await provider.complete(
            InferenceRequest([Message("user", ["Need fertilizer help."])], agent_name="crop"),
            ProviderCredential("success-key"),
        )
        self.assertIsNotNone(resp.usage)
        self.assertEqual(resp.usage.request_id, "chatcmpl-gemini-success-999")
        self.assertEqual(resp.usage.finish_reason, "stop")
        self.assertEqual(resp.usage.actual_input_tokens, 42)
        self.assertEqual(resp.usage.actual_output_tokens, 18)
        self.assertEqual(resp.usage.actual_total_tokens, 60)
        self.assertEqual(resp.usage.http_status, 200)
        self.assertEqual(resp.usage.response_character_count, len("Sample advice"))
        self.assertIsNotNone(resp.usage.elapsed_seconds)
        self.assertGreaterEqual(resp.usage.elapsed_seconds, 0.0)

    async def test_http_400_request_rejected_diagnostic(self):
        async def bad_request_handler(_request):
            return httpx2.Response(
                400,
                headers={"x-request-id": "gemini-req-bad-request-400"},
                json={"error": {"code": "invalid_argument", "message": "Unknown argument in options"}},
            )

        provider = GeminiProvider(
            GeminiProviderConfig(),
            sdk_client_factory=_mock_sdk_factory(bad_request_handler),
        )
        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(
                InferenceRequest([Message("user", ["Hi"])], agent_name="scheme"),
                ProviderCredential("bad-request-key"),
            )
        self.assertEqual(caught.exception.kind, ProviderFailureKind.PROVIDER_FAILURE)
        self.assertEqual(
            caught.exception.diagnostic.code,
            InferenceDiagnosticCode.GEMINI_REQUEST_REJECTED,
        )
        self.assertEqual(caught.exception.diagnostic.http_status, 400)
        self.assertEqual(caught.exception.diagnostic.request_id, "gemini-req-bad-request-400")

    async def test_pydantic_parsing_outcome_diagnostics(self):
        class DummyInference:
            def __init__(self, response_text: str):
                self.model_id = "gemini-3.8-flash"
                self._response_text = response_text

            async def complete(self, *args, **kwargs):
                from agent_framework import ChatResponse, Content, Message as AFMessage
                from rythusaathi.inference.contracts import ProviderResponse, InferenceTokenUsage
                msg = AFMessage("assistant", [Content.from_text(text=self._response_text)])
                chat_resp = ChatResponse(
                    messages=[msg],
                    response_id="chatcmpl-test-json",
                    finish_reason="stop",
                )
                return ProviderResponse(
                    provider="gemini",
                    model_id="gemini-3.8-flash",
                    response=chat_resp,
                    usage=InferenceTokenUsage(
                        agent_name="intake",
                        model_id="gemini-3.8-flash",
                        correlation_id="12345678123456781234567812345678",
                        estimated_input_tokens=10,
                        elapsed_seconds=0.15,
                    ),
                )

        req = InferenceRequest([Message("user", ["hi"])], agent_name="intake")

        # 1. Invalid JSON string -> json_decode_failed
        dummy_syntax = DummyInference("NOT_VALID_JSON")
        with self.assertRaises(ProviderFailure) as caught:
            await call_structured(
                dummy_syntax,
                req,
                expected_user_id="u1",
                expected_tenant_id="t1",
                response_model=IntakeInterpretation,
            )
        self.assertEqual(caught.exception.kind, ProviderFailureKind.MALFORMED_RESPONSE)
        diag = caught.exception.diagnostic
        self.assertEqual(diag.code, InferenceDiagnosticCode.GEMINI_MALFORMED_RESPONSE)
        self.assertEqual(diag.parsing_outcome, "json_decode_failed")
        self.assertEqual(diag.response_character_count, len("NOT_VALID_JSON"))
        self.assertEqual(diag.finish_reason, "stop")
        self.assertEqual(diag.request_id, "chatcmpl-test-json")

        # 2. Valid JSON string but invalid schema -> pydantic_validation_failed
        dummy_schema = DummyInference('{"invalid_key": 123}')
        with self.assertRaises(ProviderFailure) as caught_pydantic:
            await call_structured(
                dummy_schema,
                req,
                expected_user_id="u1",
                expected_tenant_id="t1",
                response_model=IntakeInterpretation,
            )
        self.assertEqual(caught_pydantic.exception.kind, ProviderFailureKind.MALFORMED_RESPONSE)
        diag_pydantic = caught_pydantic.exception.diagnostic
        self.assertEqual(diag_pydantic.code, InferenceDiagnosticCode.GEMINI_MALFORMED_RESPONSE)
        self.assertEqual(diag_pydantic.parsing_outcome, "pydantic_validation_failed")
        self.assertEqual(diag_pydantic.response_character_count, len('{"invalid_key": 123}'))
        self.assertEqual(diag_pydantic.finish_reason, "stop")
        self.assertEqual(diag_pydantic.request_id, "chatcmpl-test-json")

    def test_cli_keyboard_interrupt_clean_exit(self):
        # 1. Test CLI main() catches KeyboardInterrupt cleanly
        with patch("scripts.advisory_cli._run", side_effect=KeyboardInterrupt):
            with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
                with patch("sys.argv", ["advisory_cli.py", "test question"]):
                    code = advisory_cli.main()
                    self.assertEqual(code, 130)
                    self.assertIn("[interrupted]", mock_stderr.getvalue())
                    self.assertNotIn("Traceback", mock_stderr.getvalue())

    async def test_progress_events_distinguish_llm_completion_from_deterministic_fallback(self):
        """Workflow progress events explicitly state '(fallback)' when an agent falls back, never masquerading as full LLM completion."""
        events: list[str] = []

        class FailingInference:
            def __init__(self):
                self.model_id = "gemini-3.8-flash"
                self.diagnostics = []

            async def complete(self, *args, **kwargs):
                diag = InferenceDiagnostic(
                    code=InferenceDiagnosticCode.GEMINI_SERVICE_UNAVAILABLE,
                    stage=InferenceStage.PROVIDER_RESPONSE,
                    provider="gemini",
                    http_status=503,
                )
                self.diagnostics.append(diag)
                raise ProviderFailure(ProviderFailureKind.PROVIDER_FAILURE, diag)

        root = Path(__file__).resolve().parents[1]
        processor = DocumentProcessor()
        agriculture = load_curated(root / "data/agricultural/icar_advisories.json", CROP_REGISTRY)
        schemes = load_schemes(root / "data/schemes/central_agricultural_schemes.json")
        chunks = [chunk for document in agriculture for chunk in processor.process_curated(document)]
        chunks.extend(chunk for document in schemes for chunk in processor.process_scheme(document))
        self.knowledge.add(chunks)

        workflow = RythuSaathiWorkflow(
            resolver=self.resolver,
            knowledge=self.knowledge,
        )
        request = self._sample_request()
        failing_inf = FailingInference()

        response = await workflow.run(
            request,
            mode=WorkflowMode.GEMINI,
            inference=failing_inf,
            on_progress=events.append,
        )

        self.assertIsInstance(response, AdvisoryResponse)
        # Verify fallback progress messages are emitted
        self.assertIn("Intake completed (fallback)", events)
        self.assertIn("Crop completed (fallback)", events)
        self.assertIn("Scheme completed (fallback)", events)
        self.assertIn("Coordinator completed (fallback)", events)
        self.assertIn("Market unavailable", events)

        # Verify misleading plain "completed" messages were NOT emitted for the LLM agents
        self.assertNotIn("Intake completed", events)
        self.assertNotIn("Crop completed", events)
        self.assertNotIn("Scheme completed", events)
        self.assertNotIn("Coordinator completed", events)


if __name__ == "__main__":
    unittest.main()

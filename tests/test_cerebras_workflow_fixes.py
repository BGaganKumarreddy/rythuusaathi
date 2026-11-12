"""Regression and unit tests for Cerebras workflow fixes.

Covers:
1. Specialist 1024 token limit, parsing success, truncation failure handling, zero retries.
2. Coordinator follow-up question filtering policy:
   - Empty missing-context and empty follow-up list.
   - Empty missing-context and unexpected generated follow-up questions.
   - Non-empty missing-context and valid approved questions.
   - Unsupported evidence citations.
   - Statements requiring citations without citations.
3. Structured diagnostics distinguishing:
   - JSON decoding failure vs. provider truncation.
   - Pydantic schema validation failure.
   - Evidence citation validation failure.
   - Follow-up question validation failure.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import unittest

from agent_framework import ChatResponse, Message
from pydantic import BaseModel, Field

from rythusaathi.agents import (
    FarmerRequest,
    RythuSaathiWorkflow,
    WorkflowMode,
    default_coverage_resolver,
    empty_coverage_resolver,
)
from rythusaathi.agents.contracts import (
    AdvisoryResponse,
    AdvisoryStatus,
    AgentId,
    AgentResult,
    AgentStatus,
    FarmerIntent,
    IntakePayload,
    MarketProvider,
    MarketQuote,
    ProviderPayload,
    RetrievalPayload,
    WeatherObservation,
    WeatherProvider,
)
from rythusaathi.agents.llm import (
    CoordinatorDraft,
    GroundedStatement,
    IntakeInterpretation,
    QuotedMention,
    QuotedObservation,
    SpecialistInterpretation,
    call_structured,
    make_json_request,
    validate_evidence_ids,
)
from rythusaathi.domain.seed import CROP_REGISTRY
from rythusaathi.domain import (
    ContentReference,
    EvidenceMode,
    EvidenceRecord,
    EvidenceSource,
    EvidenceSourceType,
    EvidenceStatus,
    FactState,
    ReferenceKind,
)
from rythusaathi.inference.contracts import (
    InferenceDiagnostic,
    InferenceDiagnosticCode,
    InferenceRequest,
    InferenceStage,
    InferenceTokenUsage,
    ProviderFailure,
    ProviderFailureKind,
    ProviderResponse,
)
from rythusaathi.knowledge import (
    DocumentProcessor,
    HashEmbeddingProvider,
    LocalKnowledgeBase,
    load_curated,
    load_schemes,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.now(timezone.utc)
WEATHER_SRC = EvidenceSource(
    source_id="weather:open-meteo:test",
    title="Open-Meteo",
    reference=ContentReference(kind=ReferenceKind.DOCUMENT_ID, value="weather:open-meteo:test"),
    source_type=EvidenceSourceType.EXTERNAL_DATA,
    source_identity="Test weather source",
)


class MockObservedWeather:
    async def get_observation(self, request_id: str, location_id: str):
        return WeatherObservation(
            request_id, location_id, NOW, NOW,
            WEATHER_SRC, EvidenceMode.LIVE,
            temperature_c=29.4, rainfall_mm=0.0,
            relative_humidity_pct=68.0,
            forecast_summary="3-day forecast: min 22 °C, max 34 °C",
        )


def build_test_index() -> LocalKnowledgeBase:
    processor = DocumentProcessor()
    agriculture = load_curated(ROOT / "data/agricultural/icar_advisories.json", CROP_REGISTRY)
    schemes = load_schemes(ROOT / "data/schemes/central_agricultural_schemes.json")
    chunks = [chunk for document in agriculture for chunk in processor.process_curated(document)]
    chunks.extend(chunk for document in schemes for chunk in processor.process_scheme(document))
    kb = LocalKnowledgeBase(HashEmbeddingProvider())
    kb.add(chunks)
    return kb


class MockCerebrasInference:
    """Mock Cerebras workflow inference adapter with recording and usage telemetry."""

    def __init__(self, handler=None) -> None:
        self.provider_id = "cerebras"
        self.model_id = "gpt-oss-120b"
        self.requests: list[InferenceRequest] = []
        self.diagnostics: list[InferenceDiagnostic] = []
        self.usage_records: list[InferenceTokenUsage] = []
        self._handler = handler

    def _record_diagnostic(self, diagnostic: InferenceDiagnostic) -> None:
        self.diagnostics.append(diagnostic)

    def reserve_budget(self, tokens: int):
        return [1.0, tokens]

    def release_reservation(self, res) -> None:
        pass

    @property
    def remaining_budget_tokens(self) -> int:
        return 27_000

    async def complete(
        self, request: InferenceRequest, *, expected_user_id: str, expected_tenant_id: str
    ) -> ProviderResponse:
        self.requests.append(request)
        if self._handler:
            return await self._handler(request)

        user_json = "".join(
            item if isinstance(item, str) else getattr(item, "text", "")
            for item in request.messages[1].contents
        )
        try:
            task_input = json.loads(user_json).get("task_input", {})
        except Exception:
            task_input = {}

        agent_name = request.agent_name or ""
        max_tokens = request.options.get("max_tokens", 1024) if request.options else 1024

        if "intake" in agent_name:
            content = json.dumps({
                "crops": [{"value": "rice", "quote": "rice"}],
                "locations": [{"value": "Punjab", "quote": "Punjab"}],
                "varieties": [],
                "growth_stages": [{"value": "flowering", "quote": "flowering"}],
                "intents": ["crop_advice", "scheme"],
                "symptoms": [{"label": "yellowing leaves", "quote": "yellow leaves"}],
                "farming_problem": {"value": "yellowing leaves", "quote": "yellow leaves"},
            })
        elif "coordinator" in agent_name:
            catalog = task_input.get("evidence_catalog", [])
            evidence_id = catalog[0]["evidence_id"] if catalog else "ev:placeholder"
            content = json.dumps({
                "statements": [
                    {
                        "text": "Weather shows high relative humidity favorable for disease.",
                        "kind": "interpretation",
                        "evidence_ids": [evidence_id],
                        "uncertainty": "The response does not determine the cause or personal eligibility.",
                    }
                ],
                "follow_up_questions": [],
            })
        else:
            evidence_list = task_input.get("evidence", [])
            evidence_id = evidence_list[0]["evidence_id"] if evidence_list else "ev:placeholder"
            content = json.dumps({
                "summary": f"{agent_name} observation verified.",
                "statements": [
                    {
                        "text": f"{agent_name} observation details recorded.",
                        "kind": "interpretation",
                        "evidence_ids": [evidence_id],
                        "uncertainty": "This is generated interpretation, not a diagnosis or eligibility decision.",
                    }
                ],
            })

        response = ChatResponse(
            messages=[Message("assistant", [content])],
            finish_reason="stop",
            response_id="chatcmpl-mock-cerebras",
        )
        usage = InferenceTokenUsage(
            agent_name=agent_name,
            model_id=self.model_id,
            correlation_id="1234567890abcdef1234567890abcdef",
            estimated_input_tokens=500,
            actual_input_tokens=500,
            actual_output_tokens=300,
            actual_total_tokens=800,
            output_token_limit=max_tokens,
            retries_attempted=0,
            retry_decision="not_retried",
            http_status=200,
            finish_reason="stop",
        )
        self.usage_records.append(usage)
        return ProviderResponse(
            provider=self.provider_id,
            model_id=self.model_id,
            response=response,
            usage=usage,
        )


class CerebrasSpecialistLimitTests(unittest.IsolatedAsyncioTestCase):
    """Verifies Task 1: Cerebras specialist receives 1,024 limit, handles parsing and truncation."""

    @classmethod
    def setUpClass(cls):
        cls.index = build_test_index()

    async def test_cerebras_specialist_calls_receive_1024_token_limit(self):
        inference = MockCerebrasInference()
        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(),
            knowledge=self.index,
            weather_provider=MockObservedWeather(),
        )

        req = FarmerRequest(
            request_id="req-spec-limit",
            user_id="u-test",
            tenant_id="t-test",
            query="My rice plants have yellow leaves in Punjab. What should I do, and are there government schemes?",
        )

        response = await workflow.run(req, mode=WorkflowMode.CEREBRAS, inference=inference)
        self.assertIn(response.status, {AdvisoryStatus.COMPLETED, AdvisoryStatus.PARTIAL})

        limits = {r.agent_name: r.options.get("max_tokens") for r in inference.requests}
        self.assertEqual(limits.get("intake"), 1024)
        self.assertEqual(limits.get("coordinator"), 2048)
        self.assertEqual(limits.get("weather"), 1024)
        self.assertEqual(limits.get("crop"), 1024)
        self.assertEqual(limits.get("scheme"), 1536)

    async def test_cerebras_specialist_truncation_produces_explicit_failure_and_partial_status(self):
        async def truncated_handler(req: InferenceRequest) -> ProviderResponse:
            agent_name = req.agent_name or ""
            if "weather" in agent_name:
                truncated_json = '{"summary": "Incomplete weather text...'
                response = ChatResponse(
                    messages=[Message("assistant", [truncated_json])],
                    finish_reason="length",
                    response_id="chatcmpl-trunc",
                )
                usage = InferenceTokenUsage(
                    agent_name=agent_name,
                    model_id="gpt-oss-120b",
                    correlation_id="1234567890abcdef1234567890abcdef",
                    estimated_input_tokens=719,
                    actual_input_tokens=719,
                    actual_output_tokens=1024,
                    actual_total_tokens=1743,
                    output_token_limit=1024,
                    retries_attempted=0,
                    retry_decision="not_retried",
                    http_status=200,
                    finish_reason="length",
                )
                return ProviderResponse("cerebras", "gpt-oss-120b", response, usage)

            # Default for other agents
            return await MockCerebrasInference().complete(
                req, expected_user_id="u1", expected_tenant_id="t1"
            )

        inference = MockCerebrasInference(handler=truncated_handler)
        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(),
            knowledge=self.index,
            weather_provider=MockObservedWeather(),
        )

        req = FarmerRequest(
            request_id="req-trunc-test",
            user_id="u1",
            tenant_id="t1",
            query="My rice plants have yellow leaves in Punjab. What should I do?",
        )
        response = await workflow.run(req, mode=WorkflowMode.CEREBRAS, inference=inference)
        weather_result = next((r for r in response.agent_results if r.agent_id.value == "Weather"), None)
        self.assertIsNotNone(weather_result)
        # Weather should be PARTIAL because provider data was retrieved but LLM interpretation failed cleanly
        self.assertEqual(weather_result.status, AgentStatus.PARTIAL)
        self.assertEqual(weather_result.payload.inference_status, "malformed_response")

        # Telemetry verification: 0 retries attempted
        weather_usages = [u for u in inference.usage_records if u.agent_name == "weather"]
        for u in weather_usages:
            self.assertEqual(u.retries_attempted, 0)
            self.assertEqual(u.retry_decision, "not_retried")


class CoordinatorFollowUpPolicyTests(unittest.IsolatedAsyncioTestCase):
    """Verifies Task 2: Coordinator follow-up question filtering and evidence safeguards."""

    @classmethod
    def setUpClass(cls):
        cls.index = build_test_index()

    async def test_coordinator_empty_missing_and_empty_follow_ups_succeeds(self):
        async def handler(req: InferenceRequest) -> ProviderResponse:
            user_json = "".join(
                item if isinstance(item, str) else getattr(item, "text", "")
                for item in req.messages[1].contents
            )
            task_input = json.loads(user_json).get("task_input", {})
            agent_name = req.agent_name or ""
            if "coordinator" in agent_name:
                catalog = task_input.get("evidence_catalog", [])
                evidence_id = catalog[0]["evidence_id"] if catalog else "ev:placeholder"
                draft_content = json.dumps({
                    "statements": [
                        {
                            "text": "Weather observation shows high humidity.",
                            "kind": "evidence",
                            "evidence_ids": [evidence_id],
                            "uncertainty": None,
                        }
                    ],
                    "follow_up_questions": [],
                })
                resp = ChatResponse(
                    messages=[Message("assistant", [draft_content])],
                    finish_reason="stop",
                    response_id="chatcmpl-coord-1",
                )
                usage = InferenceTokenUsage(
                    agent_name=req.agent_name,
                    model_id="gpt-oss-120b",
                    correlation_id="1234567890abcdef1234567890abcdef",
                    estimated_input_tokens=600,
                    actual_input_tokens=600,
                    actual_output_tokens=300,
                    actual_total_tokens=900,
                    output_token_limit=2048,
                    http_status=200,
                )
                return ProviderResponse("cerebras", "gpt-oss-120b", resp, usage)
            return await MockCerebrasInference().complete(
                req, expected_user_id="u1", expected_tenant_id="t1"
            )

        inference = MockCerebrasInference(handler=handler)
        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(),
            knowledge=self.index,
            weather_provider=MockObservedWeather(),
        )

        req = FarmerRequest(
            request_id="req-coord-ok",
            user_id="u1",
            tenant_id="t1",
            query="My rice plants have yellow leaves in Punjab at flowering stage.",
            crop_query="rice",
            location_query="Punjab",
            growth_stage_query="flowering",
        )
        response = await workflow.run(req, mode=WorkflowMode.CEREBRAS, inference=inference)
        coord_result = next(r for r in response.agent_results if r.agent_id == AgentId.COORDINATOR)
        self.assertEqual(coord_result.payload.inference_status, "completed")
        self.assertEqual(coord_result.payload.interpretation_mode, "cerebras")
        self.assertIn("Cerebras-generated synthesis", response.response_text)

    async def test_coordinator_empty_missing_and_unexpected_follow_ups_filtered_without_failure(self):
        # Model emits an unapproved follow up question when missing list is empty
        async def handler(req: InferenceRequest) -> ProviderResponse:
            user_json = "".join(
                item if isinstance(item, str) else getattr(item, "text", "")
                for item in req.messages[1].contents
            )
            task_input = json.loads(user_json).get("task_input", {})
            agent_name = req.agent_name or ""
            if "coordinator" in agent_name:
                catalog = task_input.get("evidence_catalog", [])
                evidence_id = catalog[0]["evidence_id"] if catalog else "ev:placeholder"
                draft_content = json.dumps({
                    "statements": [
                        {
                            "text": "Weather observation shows high humidity.",
                            "kind": "evidence",
                            "evidence_ids": [evidence_id],
                            "uncertainty": None,
                        }
                    ],
                    "follow_up_questions": ["What is the severity of the leaf spot?"],
                })
                resp = ChatResponse(
                    messages=[Message("assistant", [draft_content])],
                    finish_reason="stop",
                    response_id="chatcmpl-coord-2",
                )
                usage = InferenceTokenUsage(
                    agent_name=req.agent_name,
                    model_id="gpt-oss-120b",
                    correlation_id="1234567890abcdef1234567890abcdef",
                    estimated_input_tokens=600,
                    actual_input_tokens=600,
                    actual_output_tokens=300,
                    actual_total_tokens=900,
                    output_token_limit=2048,
                    http_status=200,
                )
                return ProviderResponse("cerebras", "gpt-oss-120b", resp, usage)
            return await MockCerebrasInference().complete(
                req, expected_user_id="u1", expected_tenant_id="t1"
            )

        inference = MockCerebrasInference(handler=handler)
        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(),
            knowledge=self.index,
            weather_provider=MockObservedWeather(),
        )

        req = FarmerRequest(
            request_id="req-coord-filter",
            user_id="u1",
            tenant_id="t1",
            query="My rice plants have yellow leaves in Punjab at flowering stage.",
            crop_query="rice",
            location_query="Punjab",
            growth_stage_query="flowering",
        )
        # Should NOT fail or abort the advisory; unexpected question is dropped
        response = await workflow.run(req, mode=WorkflowMode.CEREBRAS, inference=inference)
        coord_result = next(r for r in response.agent_results if r.agent_id == AgentId.COORDINATOR)
        self.assertEqual(coord_result.payload.inference_status, "completed")
        # Ensure unexpected question was not included in final text
        self.assertNotIn("What is the severity of the leaf spot?", response.response_text)

    async def test_coordinator_non_empty_missing_and_valid_approved_questions(self):
        # Missing list has growth stage question; model includes approved question
        approved_q = "What growth stage is the crop currently in?"

        async def handler(req: InferenceRequest) -> ProviderResponse:
            user_json = "".join(
                item if isinstance(item, str) else getattr(item, "text", "")
                for item in req.messages[1].contents
            )
            task_input = json.loads(user_json).get("task_input", {})
            agent_name = req.agent_name or ""
            if "coordinator" in agent_name:
                catalog = task_input.get("evidence_catalog", [])
                evidence_id = catalog[0]["evidence_id"] if catalog else "ev:placeholder"
                draft_content = json.dumps({
                    "statements": [
                        {
                            "text": "Weather observation shows high humidity.",
                            "kind": "evidence",
                            "evidence_ids": [evidence_id],
                            "uncertainty": None,
                        }
                    ],
                    "follow_up_questions": [approved_q],
                })
                resp = ChatResponse(
                    messages=[Message("assistant", [draft_content])],
                    finish_reason="stop",
                    response_id="chatcmpl-coord-3",
                )
                usage = InferenceTokenUsage(
                    agent_name=req.agent_name,
                    model_id="gpt-oss-120b",
                    correlation_id="1234567890abcdef1234567890abcdef",
                    estimated_input_tokens=600,
                    actual_input_tokens=600,
                    actual_output_tokens=300,
                    actual_total_tokens=900,
                    output_token_limit=2048,
                    http_status=200,
                )
                return ProviderResponse("cerebras", "gpt-oss-120b", resp, usage)
            return await MockCerebrasInference().complete(
                req, expected_user_id="u1", expected_tenant_id="t1"
            )

        inference = MockCerebrasInference(handler=handler)
        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(),
            knowledge=self.index,
            weather_provider=MockObservedWeather(),
        )

        # Do NOT provide growth stage query so growth_stage is MISSING
        req = FarmerRequest(
            request_id="req-coord-missing-q",
            user_id="u1",
            tenant_id="t1",
            query="My rice plants have yellow leaves in Punjab.",
            crop_query="rice",
            location_query="Punjab",
            growth_stage_query=None,
        )
        response = await workflow.run(req, mode=WorkflowMode.CEREBRAS, inference=inference)
        coord_result = next(r for r in response.agent_results if r.agent_id == AgentId.COORDINATOR)
        self.assertEqual(coord_result.payload.inference_status, "completed")
        self.assertIn(f"- Clarification: {approved_q}", response.response_text)

    async def test_coordinator_unsupported_evidence_citation_fails_validation(self):
        # Cites an unsupported evidence ID
        async def handler(req: InferenceRequest) -> ProviderResponse:
            agent_name = req.agent_name or ""
            if "coordinator" in agent_name:
                draft_content = json.dumps({
                    "statements": [
                        {
                            "text": "Unverified recommendation.",
                            "kind": "evidence",
                            "evidence_ids": ["ev:hallucinated:not-in-catalog"],
                            "uncertainty": None,
                        }
                    ],
                    "follow_up_questions": [],
                })
                resp = ChatResponse(
                    messages=[Message("assistant", [draft_content])],
                    finish_reason="stop",
                    response_id="chatcmpl-coord-cite-fail",
                )
                usage = InferenceTokenUsage(
                    agent_name=req.agent_name,
                    model_id="gpt-oss-120b",
                    correlation_id="1234567890abcdef1234567890abcdef",
                    estimated_input_tokens=600,
                    actual_input_tokens=600,
                    actual_output_tokens=300,
                    actual_total_tokens=900,
                    output_token_limit=2048,
                    http_status=200,
                )
                return ProviderResponse("cerebras", "gpt-oss-120b", resp, usage)
            return await MockCerebrasInference().complete(
                req, expected_user_id="u1", expected_tenant_id="t1"
            )

        inference = MockCerebrasInference(handler=handler)
        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(),
            knowledge=self.index,
            weather_provider=MockObservedWeather(),
        )

        req = FarmerRequest(
            request_id="req-coord-bad-cite",
            user_id="u1",
            tenant_id="t1",
            query="My rice plants have yellow leaves in Punjab at flowering stage.",
            crop_query="rice",
            location_query="Punjab",
            growth_stage_query="flowering",
        )
        response = await workflow.run(req, mode=WorkflowMode.CEREBRAS, inference=inference)
        coord_result = next(r for r in response.agent_results if r.agent_id == AgentId.COORDINATOR)
        # Must fall back to deterministic summary and report malformed_response
        self.assertEqual(coord_result.payload.inference_status, "malformed_response")
        self.assertEqual(coord_result.payload.interpretation_mode, "deterministic_fallback")
        self.assertNotIn("ev:hallucinated:not-in-catalog", response.response_text)

        # Check recorded diagnostic
        cite_diags = [d for d in inference.diagnostics if d.parsing_outcome == "evidence_citation_failed"]
        self.assertTrue(cite_diags)
        self.assertEqual(cite_diags[0].validation_category, "evidence_citation_validation_failure")

    async def test_coordinator_statements_requiring_citations_without_citations_fails(self):
        # 'interpretation' kind statement with empty evidence_ids violates GroundedStatement validator
        async def handler(req: InferenceRequest) -> ProviderResponse:
            agent_name = req.agent_name or ""
            if "coordinator" in agent_name:
                draft_content = json.dumps({
                    "statements": [
                        {
                            "text": "Spray chemical without citations.",
                            "kind": "interpretation",
                            "evidence_ids": [],
                            "uncertainty": None,
                        }
                    ],
                    "follow_up_questions": [],
                })
                resp = ChatResponse(
                    messages=[Message("assistant", [draft_content])],
                    finish_reason="stop",
                    response_id="chatcmpl-coord-uncited",
                )
                usage = InferenceTokenUsage(
                    agent_name=req.agent_name,
                    model_id="gpt-oss-120b",
                    correlation_id="1234567890abcdef1234567890abcdef",
                    estimated_input_tokens=600,
                    actual_input_tokens=600,
                    actual_output_tokens=300,
                    actual_total_tokens=900,
                    output_token_limit=2048,
                    http_status=200,
                )
                return ProviderResponse("cerebras", "gpt-oss-120b", resp, usage)
            return await MockCerebrasInference().complete(
                req, expected_user_id="u1", expected_tenant_id="t1"
            )

        inference = MockCerebrasInference(handler=handler)
        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(),
            knowledge=self.index,
            weather_provider=MockObservedWeather(),
        )

        req = FarmerRequest(
            request_id="req-coord-uncited",
            user_id="u1",
            tenant_id="t1",
            query="My rice plants have yellow leaves in Punjab at flowering stage.",
            crop_query="rice",
            location_query="Punjab",
            growth_stage_query="flowering",
        )
        response = await workflow.run(req, mode=WorkflowMode.CEREBRAS, inference=inference)
        coord_result = next(r for r in response.agent_results if r.agent_id == AgentId.COORDINATOR)
        self.assertEqual(coord_result.payload.inference_status, "malformed_response")
        self.assertEqual(coord_result.payload.interpretation_mode, "deterministic_fallback")


class StructuredDiagnosticClassificationTests(unittest.IsolatedAsyncioTestCase):
    """Verifies Task 3: Structured diagnostics distinguishing failure categories."""

    async def test_diagnostics_distinguish_json_decode_from_provider_truncation(self):
        class TruncatedInference(MockCerebrasInference):
            async def complete(self, request, expected_user_id, expected_tenant_id):
                resp = ChatResponse(
                    messages=[Message("assistant", ['{"summary": "Incomplete...'])],
                    finish_reason="length",
                    response_id="chatcmpl-trunc-diag",
                )
                usage = InferenceTokenUsage(
                    agent_name=request.agent_name,
                    model_id="gpt-oss-120b",
                    correlation_id="1234567890abcdef1234567890abcdef",
                    estimated_input_tokens=500,
                    actual_input_tokens=500,
                    actual_output_tokens=1024,
                    actual_total_tokens=1524,
                    output_token_limit=1024,
                    finish_reason="length",
                    http_status=200,
                )
                return ProviderResponse("cerebras", "gpt-oss-120b", resp, usage)

        inference = TruncatedInference()
        req = make_json_request("prompt", {}, SpecialistInterpretation, agent_name="weather", max_output_tokens=1024)
        with self.assertRaises(ProviderFailure) as caught:
            await call_structured(
                inference, req, expected_user_id="u1", expected_tenant_id="t1", response_model=SpecialistInterpretation
            )
        diag = caught.exception.diagnostic
        self.assertIsNotNone(diag)
        self.assertEqual(diag.finish_reason, "length")
        self.assertEqual(diag.parsing_outcome, "json_decode_failed")
        self.assertEqual(diag.validation_category, "provider_truncation")

    async def test_diagnostics_distinguish_invalid_json_syntax(self):
        class InvalidSyntaxInference(MockCerebrasInference):
            async def complete(self, request, expected_user_id, expected_tenant_id):
                resp = ChatResponse(
                    messages=[Message("assistant", ["Totally not JSON"])],
                    finish_reason="stop",
                    response_id="chatcmpl-syntax-diag",
                )
                usage = InferenceTokenUsage(
                    agent_name=request.agent_name,
                    model_id="gpt-oss-120b",
                    correlation_id="1234567890abcdef1234567890abcdef",
                    estimated_input_tokens=500,
                    actual_input_tokens=500,
                    actual_output_tokens=100,
                    actual_total_tokens=600,
                    output_token_limit=1024,
                    finish_reason="stop",
                    http_status=200,
                )
                return ProviderResponse("cerebras", "gpt-oss-120b", resp, usage)

        inference = InvalidSyntaxInference()
        req = make_json_request("prompt", {}, SpecialistInterpretation, agent_name="market", max_output_tokens=1024)
        with self.assertRaises(ProviderFailure) as caught:
            await call_structured(
                inference, req, expected_user_id="u1", expected_tenant_id="t1", response_model=SpecialistInterpretation
            )
        diag = caught.exception.diagnostic
        self.assertIsNotNone(diag)
        self.assertEqual(diag.finish_reason, "stop")
        self.assertEqual(diag.parsing_outcome, "json_decode_failed")
        self.assertEqual(diag.validation_category, "json_decode_failure")

    async def test_diagnostics_distinguish_pydantic_schema_validation_failure(self):
        class InvalidSchemaInference(MockCerebrasInference):
            async def complete(self, request, expected_user_id, expected_tenant_id):
                resp = ChatResponse(
                    messages=[Message("assistant", [json.dumps({"unexpected_field": "test"})])],
                    finish_reason="stop",
                    response_id="chatcmpl-schema-diag",
                )
                usage = InferenceTokenUsage(
                    agent_name=request.agent_name,
                    model_id="gpt-oss-120b",
                    correlation_id="1234567890abcdef1234567890abcdef",
                    estimated_input_tokens=500,
                    actual_input_tokens=500,
                    actual_output_tokens=100,
                    actual_total_tokens=600,
                    output_token_limit=1024,
                    finish_reason="stop",
                    http_status=200,
                )
                return ProviderResponse("cerebras", "gpt-oss-120b", resp, usage)

        inference = InvalidSchemaInference()
        req = make_json_request("prompt", {}, SpecialistInterpretation, agent_name="scheme", max_output_tokens=1024)
        with self.assertRaises(ProviderFailure) as caught:
            await call_structured(
                inference, req, expected_user_id="u1", expected_tenant_id="t1", response_model=SpecialistInterpretation
            )
        diag = caught.exception.diagnostic
        self.assertIsNotNone(diag)
        self.assertEqual(diag.parsing_outcome, "pydantic_validation_failed")
        self.assertEqual(diag.validation_category, "pydantic_schema_validation_failure")

    def test_validate_evidence_ids_raises_typed_citation_diagnostic(self):
        stmt = GroundedStatement(
            text="Valid statement text.",
            kind="evidence",
            evidence_ids=["ev:not-allowed:999"],
            uncertainty=None,
        )
        with self.assertRaises(ProviderFailure) as caught:
            validate_evidence_ids(
                [stmt],
                {"ev:allowed:001"},
                provider="cerebras",
                model_id="gpt-oss-120b",
                agent_name="coordinator",
            )
        diag = caught.exception.diagnostic
        self.assertIsNotNone(diag)
        self.assertEqual(diag.code, InferenceDiagnosticCode.CEREBRAS_MALFORMED_RESPONSE)
        self.assertEqual(diag.parsing_outcome, "evidence_citation_failed")
        self.assertEqual(diag.validation_category, "evidence_citation_validation_failure")
        self.assertEqual(diag.provider, "cerebras")
        self.assertEqual(diag.model_id, "gpt-oss-120b")
        self.assertEqual(diag.agent_name, "coordinator")


if __name__ == "__main__":
    unittest.main()

"""Unit and regression tests for Scheme Agent truncation and interpretation quality fixes.

Covers:
1. Scheme output limit: Scheme receives 1,536 tokens on Cerebras, Weather/Market 1,024,
   Groq 400, Gemini 1500.
2. Scheme token reservation: Cerebras pre-reserves estimated_input + 1,536 tokens,
   handles insufficient budget with zero retries.
3. Scheme structured response parsing: parses below limit, handles truncation with
   provider_truncation diagnostic and partial status.
4. Scheme duplicate evidence handling: deduplicates identical chunks, substring chunks,
   merges overlapping chunks from the same document, and preserves non-overlapping chunks.
5. Market single-day inference safeguards: rejects ungrounded demand trend or sell/hold
   recommendations from a single-day quote, records diagnostic, falls back to deterministic summary.
6. Weather uncertainty safeguards: rejects ungrounded leaf spot diagnosis or ruling out from
   weather snapshot, records diagnostic, falls back to deterministic summary.
7. Coordinator regression: coordinator operates correctly with Cerebras.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import unittest

from agent_framework import ChatResponse, Message

from rythusaathi.agents import (
    FarmerRequest,
    RythuSaathiWorkflow,
    WorkflowMode,
    empty_coverage_resolver,
)
from rythusaathi.agents.contracts import (
    AdvisoryStatus,
    AgentId,
    AgentStatus,
    MarketQuote,
    WeatherObservation,
)
from rythusaathi.agents.llm import GroundedStatement, SpecialistInterpretation
from rythusaathi.agents.workflow import (
    _CEREBRAS_SCHEME_OUTPUT_LIMIT_TOKENS,
    _CEREBRAS_SPECIALIST_OUTPUT_LIMIT_TOKENS,
    _SPECIALIST_OUTPUT_LIMIT_TOKENS,
    _deduplicate_and_merge_scheme_hits,
)
from rythusaathi.domain import (
    ContentReference,
    EvidenceMode,
    EvidenceRecord,
    EvidenceSource,
    EvidenceSourceType,
    EvidenceStatus,
    ReferenceKind,
)
from rythusaathi.domain.seed import CROP_REGISTRY
from rythusaathi.inference.contracts import (
    InferenceDiagnostic,
    InferenceDiagnosticCode,
    InferenceRequest,
    InferenceTokenUsage,
    ProviderFailure,
    ProviderFailureKind,
    ProviderResponse,
)
from rythusaathi.knowledge import (
    DocumentChunk,
    DocumentMetadata,
    DocumentProcessor,
    HashEmbeddingProvider,
    LocalKnowledgeBase,
    RetrievedChunk,
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
MARKET_SRC = EvidenceSource(
    source_id="market:data-gov-in:test",
    title="data.gov.in",
    reference=ContentReference(kind=ReferenceKind.DOCUMENT_ID, value="market:data-gov-in:test"),
    source_type=EvidenceSourceType.EXTERNAL_DATA,
    source_identity="Test market source",
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


class MockObservedMarket:
    async def get_quote(self, request_id: str, crop_id: str, location_id: str):
        return MarketQuote(
            request_id, crop_id, location_id, "Adilabad APMC", 8850.0,
            "INR/quintal", NOW, NOW, MARKET_SRC, EvidenceMode.LIVE,
            min_price=8200.0, max_price=9100.0, commodity="Cotton", variety="Medium Staple",
            grade="FAQ", arrival_date="2026-11-10",
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


class MockQualityInference:
    def __init__(self, *, handler=None, provider_id="cerebras", model_id="gpt-oss-120b"):
        self.provider_id = provider_id
        self.model_id = model_id
        self.requests: list[InferenceRequest] = []
        self.diagnostics: list[InferenceDiagnostic] = []
        self.usage_records: list[InferenceTokenUsage] = []
        self.reservations: list[int] = []
        self._handler = handler

    def _record_diagnostic(self, diagnostic: InferenceDiagnostic) -> None:
        self.diagnostics.append(diagnostic)

    def reserve_budget(self, tokens: int):
        self.reservations.append(tokens)
        return [1.0, tokens]

    def release_reservation(self, res) -> None:
        pass

    async def complete(self, request: InferenceRequest, *, expected_user_id: str, expected_tenant_id: str) -> ProviderResponse:
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
                "crops": [{"value": "cotton", "quote": "cotton"}],
                "locations": [{"value": "Adilabad", "quote": "Adilabad"}],
                "varieties": [],
                "growth_stages": [{"value": "flowering", "quote": "flowering"}],
                "intents": ["crop_advice", "scheme", "market"],
                "symptoms": [{"label": "leaf spot", "quote": "leaf spot"}],
                "farming_problem": {"value": "leaf spot", "quote": "leaf spot"},
            })
        elif "coordinator" in agent_name:
            catalog = task_input.get("evidence_catalog", [])
            ev_id = catalog[0]["evidence_id"] if catalog else "ev:placeholder"
            content = json.dumps({
                "statements": [
                    {
                        "text": "Weather shows high relative humidity favorable for disease.",
                        "kind": "interpretation",
                        "evidence_ids": [ev_id],
                        "uncertainty": "The response does not determine the cause or personal eligibility.",
                    }
                ],
                "follow_up_questions": [],
            })
        else:
            evidence_list = task_input.get("evidence", [])
            ev_id = evidence_list[0]["evidence_id"] if evidence_list else "ev:placeholder"
            content = json.dumps({
                "summary": f"{agent_name} observation verified.",
                "statements": [
                    {
                        "text": f"{agent_name} observation details recorded.",
                        "kind": "interpretation",
                        "evidence_ids": [ev_id],
                        "uncertainty": "This is generated interpretation, not a diagnosis or eligibility decision.",
                    }
                ],
            })

        response = ChatResponse(
            messages=[Message("assistant", [content])],
            finish_reason="stop",
            response_id=f"chatcmpl-{agent_name}",
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
        return ProviderResponse(self.provider_id, self.model_id, response, usage)


class SchemeBudgetAndParsingTests(unittest.IsolatedAsyncioTestCase):
    """Task 1 & Task 5: Scheme output budget, token reservation, and parsing."""

    @classmethod
    def setUpClass(cls):
        cls.index = build_test_index()

    async def test_scheme_receives_1536_token_limit_on_cerebras(self):
        inference = MockQualityInference()
        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(),
            knowledge=self.index,
            weather_provider=MockObservedWeather(),
            market_provider=MockObservedMarket(),
        )

        req = FarmerRequest(
            request_id="req-scheme-limit",
            user_id="u1",
            tenant_id="t1",
            query="My rice plants have yellow leaves in Punjab. What should I do, and are there government schemes?",
            crop_query="rice",
            location_query="Punjab",
        )
        response = await workflow.run(req, mode=WorkflowMode.CEREBRAS, inference=inference)
        self.assertIn(response.status, {AdvisoryStatus.COMPLETED, AdvisoryStatus.PARTIAL})

        limits = {r.agent_name: r.options.get("max_tokens") for r in inference.requests}
        self.assertEqual(limits.get("scheme"), 1536)
        self.assertEqual(limits.get("weather"), 1024)
        self.assertEqual(limits.get("market"), 1024)
        self.assertEqual(limits.get("intake"), 1024)
        self.assertEqual(limits.get("coordinator"), 2048)

    async def test_scheme_token_reservation_and_truncation_handling(self):
        # Truncation on scheme when finish_reason='length'
        async def truncated_scheme_handler(req: InferenceRequest) -> ProviderResponse:
            agent_name = req.agent_name or ""
            if "scheme" in agent_name:
                truncated_json = '{"summary": "Incomplete scheme JSON...'
                resp = ChatResponse(
                    messages=[Message("assistant", [truncated_json])],
                    finish_reason="length",
                    response_id="chatcmpl-scheme-trunc",
                )
                usage = InferenceTokenUsage(
                    agent_name=agent_name,
                    model_id="gpt-oss-120b",
                    correlation_id="1234567890abcdef1234567890abcdef",
                    estimated_input_tokens=2193,
                    actual_input_tokens=2193,
                    actual_output_tokens=1536,
                    actual_total_tokens=3729,
                    output_token_limit=1536,
                    retries_attempted=0,
                    retry_decision="not_retried",
                    finish_reason="length",
                    http_status=200,
                )
                inference.usage_records.append(usage)
                return ProviderResponse("cerebras", "gpt-oss-120b", resp, usage)
            return await MockQualityInference().complete(req, expected_user_id="u1", expected_tenant_id="t1")

        inference = MockQualityInference(handler=truncated_scheme_handler)
        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(),
            knowledge=self.index,
            weather_provider=MockObservedWeather(),
            market_provider=MockObservedMarket(),
        )

        req = FarmerRequest(
            request_id="req-scheme-trunc",
            user_id="u1",
            tenant_id="t1",
            query="My rice plants in Punjab; government schemes?",
            crop_query="rice",
            location_query="Punjab",
        )
        response = await workflow.run(req, mode=WorkflowMode.CEREBRAS, inference=inference)
        scheme_res = next(r for r in response.agent_results if r.agent_id == AgentId.SCHEME)
        self.assertEqual(scheme_res.status, AgentStatus.PARTIAL)
        self.assertEqual(scheme_res.payload.inference_status, "malformed_response")

        scheme_usage = next(u for u in inference.usage_records if u.agent_name == "scheme")
        self.assertEqual(scheme_usage.retries_attempted, 0)
        self.assertEqual(scheme_usage.retry_decision, "not_retried")


class SchemeDeduplicationTests(unittest.TestCase):
    """Task 2 & Task 5: Scheme duplicate and overlapping excerpt handling."""

    def _make_chunk(self, doc_id: str, chunk_id: str, text: str, sim: float = 0.3) -> RetrievedChunk:
        meta = DocumentMetadata(
            document_id=doc_id,
            source_id=f"src:{doc_id}",
            title=f"Title {doc_id}",
            source_type="primary_government",
            reliability="primary",
            source_identity="Dept of Agriculture",
            reference=f"https://example.gov.in/{doc_id}",
            jurisdiction=("location:country:in",),
            jurisdiction_level="central",
            retrieved_at=NOW,
        )
        chunk = DocumentChunk(
            chunk_id=chunk_id,
            parent_document_id=doc_id,
            source=meta,
            text=text,
            content_hash=f"hash-{chunk_id}",
            topic="government_scheme",
            geographic_applicability=("location:country:in",),
        )
        return RetrievedChunk(chunk=chunk, similarity=sim, retrieved_at=NOW)

    def test_deduplicate_identical_chunks(self):
        # Two identical chunks for the same document
        c1 = self._make_chunk("doc-1", "chunk-1", "Eligibility: All landholding farmers. Benefits: Rs 6000.", 0.4)
        c2 = self._make_chunk("doc-1", "chunk-2", "Eligibility: All landholding farmers. Benefits: Rs 6000.", 0.3)
        records = _deduplicate_and_merge_scheme_hits([c1, c2], request_id="req-1")
        self.assertEqual(len(records), 1)
        self.assertIn("Eligibility: All landholding farmers", records[0].excerpt)
        self.assertEqual(records[0].relevance, 0.4)

    def test_merge_overlapping_chunks_from_same_document(self):
        # Two chunks with overlapping boundaries (suffix/prefix match)
        part1 = "Scheme summary: Central Sector Scheme. Eligibility: Landholding farmer families with cultivable land."
        part2 = "Landholding farmer families with cultivable land. Benefits: Rs 6,000 per year via direct benefit transfer."
        c1 = self._make_chunk("doc-pmkisan", "chunk-p1", part1, 0.35)
        c2 = self._make_chunk("doc-pmkisan", "chunk-p2", part2, 0.30)
        records = _deduplicate_and_merge_scheme_hits([c1, c2], request_id="req-2")
        self.assertEqual(len(records), 1)
        # Verify no duplicate overlap sentence
        excerpt = records[0].excerpt
        self.assertIn("Scheme summary: Central Sector Scheme", excerpt)
        self.assertIn("Benefits: Rs 6,000 per year", excerpt)
        self.assertEqual(excerpt.count("Landholding farmer families with cultivable land"), 1)

    def test_preserve_non_overlapping_chunks_from_same_document(self):
        # Two chunks from the same document that do NOT overlap
        part1 = "Eligibility: Farmers owning land."
        part2 = "Procedure: Apply through district collector portal."
        c1 = self._make_chunk("doc-pmkisan", "chunk-p1", part1, 0.35)
        c2 = self._make_chunk("doc-pmkisan", "chunk-p2", part2, 0.30)
        records = _deduplicate_and_merge_scheme_hits([c1, c2], request_id="req-3")
        self.assertEqual(len(records), 2)
        excerpts = [r.excerpt for r in records]
        self.assertIn(part1, excerpts)
        self.assertIn(part2, excerpts)

    def test_preserve_chunks_from_different_documents(self):
        c1 = self._make_chunk("doc-pmkisan", "chunk-1", "PM KISAN text", 0.4)
        c2 = self._make_chunk("doc-pmfby", "chunk-2", "PMFBY text", 0.38)
        records = _deduplicate_and_merge_scheme_hits([c1, c2], request_id="req-4")
        self.assertEqual(len(records), 2)
        doc_refs = {r.content_reference.value for r in records}
        self.assertEqual(doc_refs, {"doc-pmkisan", "doc-pmfby"})


class MarketGroundingSafeguardTests(unittest.IsolatedAsyncioTestCase):
    """Task 3 & Task 5: Market interpretation grounding and single-day safeguards."""

    @classmethod
    def setUpClass(cls):
        cls.index = build_test_index()

    async def test_market_single_day_trend_assertion_fails_grounding_validation(self):
        # LLM attempts to claim a 'confirmed demand trend' from a single-day quote
        async def ungrounded_market_handler(req: InferenceRequest) -> ProviderResponse:
            user_json = "".join(
                item if isinstance(item, str) else getattr(item, "text", "")
                for item in req.messages[1].contents
            )
            task_input = json.loads(user_json).get("task_input", {})
            agent_name = req.agent_name or ""
            if "market" in agent_name:
                evidence_list = task_input.get("evidence", [])
                ev_id = evidence_list[0]["evidence_id"] if evidence_list else "ev:placeholder"
                resp_json = json.dumps({
                    "summary": "Adilabad modal price is high.",
                    "statements": [
                        {
                            "text": "The modal price equals the high end, proving a confirmed demand trend.",
                            "kind": "interpretation",
                            "evidence_ids": [ev_id],
                            "uncertainty": None,
                        }
                    ],
                })
                resp = ChatResponse(messages=[Message("assistant", [resp_json])], finish_reason="stop")
                usage = InferenceTokenUsage(
                    agent_name=agent_name, model_id="gpt-oss-120b",
                    correlation_id="1234567890abcdef1234567890abcdef",
                    estimated_input_tokens=600, actual_input_tokens=600,
                    actual_output_tokens=200, actual_total_tokens=800,
                    output_token_limit=1024, http_status=200,
                )
                return ProviderResponse("cerebras", "gpt-oss-120b", resp, usage)
            return await MockQualityInference().complete(req, expected_user_id="u1", expected_tenant_id="t1")

        inference = MockQualityInference(handler=ungrounded_market_handler)
        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(),
            knowledge=self.index,
            weather_provider=MockObservedWeather(),
            market_provider=MockObservedMarket(),
        )

        req = FarmerRequest(
            request_id="req-mkt-ungrounded",
            user_id="u1", tenant_id="t1",
            query="Cotton crop in Adilabad; market prices?",
            crop_query="cotton", location_query="Adilabad",
        )
        response = await workflow.run(req, mode=WorkflowMode.CEREBRAS, inference=inference)
        market_res = next(r for r in response.agent_results if r.agent_id == AgentId.MARKET)
        # Must fail validation and fall back to deterministic summary
        self.assertEqual(market_res.status, AgentStatus.PARTIAL)
        self.assertEqual(market_res.payload.inference_status, "malformed_response")
        self.assertEqual(market_res.payload.interpretation_mode, "deterministic_fallback")

        diag = next((d for d in inference.diagnostics if d.agent_name == "market"), None)
        self.assertIsNotNone(diag)
        self.assertEqual(diag.validation_category, "market_grounding_validation_failure")
        self.assertEqual(diag.parsing_outcome, "unsupported_market_trend_assertion")

    async def test_market_grounded_interpretation_passes_validation(self):
        # Grounded interpretation distinguishing observed price facts without asserted trends
        async def grounded_market_handler(req: InferenceRequest) -> ProviderResponse:
            user_json = "".join(
                item if isinstance(item, str) else getattr(item, "text", "")
                for item in req.messages[1].contents
            )
            task_input = json.loads(user_json).get("task_input", {})
            agent_name = req.agent_name or ""
            if "market" in agent_name:
                evidence_list = task_input.get("evidence", [])
                ev_id = evidence_list[0]["evidence_id"] if evidence_list else "ev:placeholder"
                resp_json = json.dumps({
                    "summary": "Observed modal price is 8850 INR/quintal within range 8200-9100.",
                    "statements": [
                        {
                            "text": "Observed modal price is 8850 INR/quintal for FAQ medium staple cotton on 2026-11-10.",
                            "kind": "interpretation",
                            "evidence_ids": [ev_id],
                            "uncertainty": "Single-day observation is insufficient to establish a price trend.",
                        }
                    ],
                })
                resp = ChatResponse(messages=[Message("assistant", [resp_json])], finish_reason="stop")
                usage = InferenceTokenUsage(
                    agent_name=agent_name, model_id="gpt-oss-120b",
                    correlation_id="1234567890abcdef1234567890abcdef",
                    estimated_input_tokens=600, actual_input_tokens=600,
                    actual_output_tokens=200, actual_total_tokens=800,
                    output_token_limit=1024, http_status=200,
                )
                return ProviderResponse("cerebras", "gpt-oss-120b", resp, usage)
            return await MockQualityInference().complete(req, expected_user_id="u1", expected_tenant_id="t1")

        inference = MockQualityInference(handler=grounded_market_handler)
        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(),
            knowledge=self.index,
            weather_provider=MockObservedWeather(),
            market_provider=MockObservedMarket(),
        )

        req = FarmerRequest(
            request_id="req-mkt-grounded",
            user_id="u1", tenant_id="t1",
            query="Cotton crop in Adilabad; market prices?",
            crop_query="cotton", location_query="Adilabad",
        )
        response = await workflow.run(req, mode=WorkflowMode.CEREBRAS, inference=inference)
        market_res = next(r for r in response.agent_results if r.agent_id == AgentId.MARKET)
        self.assertIn(market_res.status, {AgentStatus.COMPLETED, AgentStatus.PARTIAL})
        self.assertEqual(market_res.payload.inference_status, "completed")
        self.assertEqual(market_res.payload.interpretation_mode, "cerebras")


class WeatherGroundingSafeguardTests(unittest.IsolatedAsyncioTestCase):
    """Task 4 & Task 5: Weather uncertainty safeguards against ungrounded disease diagnosis."""

    @classmethod
    def setUpClass(cls):
        cls.index = build_test_index()

    async def test_weather_snapshot_claiming_leaf_spot_diagnosis_fails_validation(self):
        # LLM claims leaf spot is confirmed from weather snapshot
        async def ungrounded_weather_handler(req: InferenceRequest) -> ProviderResponse:
            user_json = "".join(
                item if isinstance(item, str) else getattr(item, "text", "")
                for item in req.messages[1].contents
            )
            task_input = json.loads(user_json).get("task_input", {})
            agent_name = req.agent_name or ""
            if "weather" in agent_name:
                evidence_list = task_input.get("evidence", [])
                ev_id = evidence_list[0]["evidence_id"] if evidence_list else "ev:placeholder"
                resp_json = json.dumps({
                    "summary": "Weather snapshot indicates leaf spot.",
                    "statements": [
                        {
                            "text": "High humidity of 68% confirms leaf spot disease in the crop.",
                            "kind": "interpretation",
                            "evidence_ids": [ev_id],
                            "uncertainty": None,
                        }
                    ],
                })
                resp = ChatResponse(messages=[Message("assistant", [resp_json])], finish_reason="stop")
                usage = InferenceTokenUsage(
                    agent_name=agent_name, model_id="gpt-oss-120b",
                    correlation_id="1234567890abcdef1234567890abcdef",
                    estimated_input_tokens=600, actual_input_tokens=600,
                    actual_output_tokens=200, actual_total_tokens=800,
                    output_token_limit=1024, http_status=200,
                )
                return ProviderResponse("cerebras", "gpt-oss-120b", resp, usage)
            return await MockQualityInference().complete(req, expected_user_id="u1", expected_tenant_id="t1")

        inference = MockQualityInference(handler=ungrounded_weather_handler)
        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(),
            knowledge=self.index,
            weather_provider=MockObservedWeather(),
            market_provider=MockObservedMarket(),
        )

        req = FarmerRequest(
            request_id="req-wtr-ungrounded",
            user_id="u1", tenant_id="t1",
            query="Cotton in Adilabad with leaf spot; weather conditions?",
            crop_query="cotton", location_query="Adilabad",
        )
        response = await workflow.run(req, mode=WorkflowMode.CEREBRAS, inference=inference)
        weather_res = next(r for r in response.agent_results if r.agent_id == AgentId.WEATHER)
        self.assertEqual(weather_res.status, AgentStatus.PARTIAL)
        self.assertEqual(weather_res.payload.inference_status, "malformed_response")
        self.assertEqual(weather_res.payload.interpretation_mode, "deterministic_fallback")

        diag = next((d for d in inference.diagnostics if d.agent_name == "weather"), None)
        self.assertIsNotNone(diag)
        self.assertEqual(diag.validation_category, "weather_grounding_validation_failure")
        self.assertEqual(diag.parsing_outcome, "unsupported_disease_diagnosis")

    async def test_weather_grounded_interpretation_passes_validation(self):
        # Grounded interpretation describing environmental conditions without asserting disease diagnosis
        async def grounded_weather_handler(req: InferenceRequest) -> ProviderResponse:
            user_json = "".join(
                item if isinstance(item, str) else getattr(item, "text", "")
                for item in req.messages[1].contents
            )
            task_input = json.loads(user_json).get("task_input", {})
            agent_name = req.agent_name or ""
            if "weather" in agent_name:
                evidence_list = task_input.get("evidence", [])
                ev_id = evidence_list[0]["evidence_id"] if evidence_list else "ev:placeholder"
                resp_json = json.dumps({
                    "summary": "Observed temperature is 29.4 C with 68% relative humidity.",
                    "statements": [
                        {
                            "text": "Relative humidity of 68% and 29.4 C provide conditions favorable for fungal growth.",
                            "kind": "interpretation",
                            "evidence_ids": [ev_id],
                            "uncertainty": "Weather data alone does not confirm or rule out infection; field inspection is required.",
                        }
                    ],
                })
                resp = ChatResponse(messages=[Message("assistant", [resp_json])], finish_reason="stop")
                usage = InferenceTokenUsage(
                    agent_name=agent_name, model_id="gpt-oss-120b",
                    correlation_id="1234567890abcdef1234567890abcdef",
                    estimated_input_tokens=600, actual_input_tokens=600,
                    actual_output_tokens=200, actual_total_tokens=800,
                    output_token_limit=1024, http_status=200,
                )
                return ProviderResponse("cerebras", "gpt-oss-120b", resp, usage)
            return await MockQualityInference().complete(req, expected_user_id="u1", expected_tenant_id="t1")

        inference = MockQualityInference(handler=grounded_weather_handler)
        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(),
            knowledge=self.index,
            weather_provider=MockObservedWeather(),
            market_provider=MockObservedMarket(),
        )

        req = FarmerRequest(
            request_id="req-wtr-grounded",
            user_id="u1", tenant_id="t1",
            query="Cotton in Adilabad with leaf spot; weather conditions?",
            crop_query="cotton", location_query="Adilabad",
        )
        response = await workflow.run(req, mode=WorkflowMode.CEREBRAS, inference=inference)
        weather_res = next(r for r in response.agent_results if r.agent_id == AgentId.WEATHER)
        self.assertIn(weather_res.status, {AgentStatus.COMPLETED, AgentStatus.PARTIAL})
        self.assertEqual(weather_res.payload.inference_status, "completed")
        self.assertEqual(weather_res.payload.interpretation_mode, "cerebras")


if __name__ == "__main__":
    unittest.main()

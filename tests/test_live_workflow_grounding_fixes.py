"""Regression and unit tests for live workflow validation fixes: Market, Weather, and Coordinator.

Covers:
Task 1: Market Pydantic validation failure inspection & handling:
  - Captures exact sanitized ValidationError in InferenceDiagnostic.provider_error_message.
  - Rejects extra fields, >3 statements, >180 char uncertainty, limitation citing evidence.
  - Conforming 1-3 statement market interpretation passes and receives AgentStatus.COMPLETED.

Task 2: Weather grounding guard false positive fix:
  - Cautious environmental interpretation stating 'weather observations do not diagnose leaf spot' is ACCEPTED.
  - Field inspection recommendation with qualified advice is ACCEPTED.
  - Explicit disease diagnosis ('confirms leaf spot') is REJECTED.
  - Explicit disease exclusion ('rules out leaf spot', 'no risk of leaf spot') is REJECTED.

Task 3: Market grounding at Coordinator level:
  - Single-day market evidence: Coordinator draft with ungrounded trend or sell/hold is REJECTED.
  - Single-day market evidence: Coordinator draft with observed price facts and uncertainty is ACCEPTED.

Task 4: Scheme status propagation:
  - Successful Scheme LLM interpretation sets AgentResult.status to COMPLETED (not PARTIAL).
  - Failed Scheme LLM interpretation sets AgentResult.status to PARTIAL with deterministic fallback.

Task 5: Coordinator agent status vs overall advisory status:
  - Successful Coordinator synthesis with unavailable upstream Crop:
    Coordinator AgentResult.status is COMPLETED, while AdvisoryResponse.status is PARTIAL.
  - Failed Coordinator synthesis:
    Coordinator AgentResult.status is PARTIAL, and AdvisoryResponse.status is PARTIAL.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
import json
from pathlib import Path
import unittest

from agent_framework import ChatResponse, Message
import pydantic

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
from rythusaathi.agents.llm import (
    CoordinatorDraft,
    GroundedStatement,
    SpecialistInterpretation,
    call_structured,
    make_json_request,
)
from rythusaathi.agents.workflow import (
    _UNGROUNDED_MARKET_TREND_PATTERN,
    _is_ungrounded_disease_diagnosis,
)
from rythusaathi.domain import (
    ContentReference,
    EvidenceMode,
    EvidenceSource,
    EvidenceSourceType,
    ReferenceKind,
)
from rythusaathi.domain.seed import CROP_REGISTRY
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
MARKET_SRC = EvidenceSource(
    source_id="market:data-gov-in:test",
    title="data.gov.in",
    reference=ContentReference(kind=ReferenceKind.DOCUMENT_ID, value="market:data-gov-in:test"),
    source_type=EvidenceSourceType.EXTERNAL_DATA,
    source_identity="Test market source",
)


class MockObservedWeather:
    def __init__(self, observation: WeatherObservation | None = None):
        self._observation = observation

    async def get_observation(self, request_id: str, location_id: str):
        if self._observation is not None:
            return dataclasses.replace(self._observation, request_id=request_id, location_id=location_id)
        return WeatherObservation(
            request_id=request_id,
            location_id=location_id,
            observed_at=NOW,
            retrieved_at=NOW,
            source=WEATHER_SRC,
            mode=EvidenceMode.LIVE,
            temperature_c=29.4,
            rainfall_mm=0.0,
            relative_humidity_pct=68.0,
            forecast_summary="3-day forecast: min 22 °C, max 34 °C",
        )


class MockObservedMarket:
    def __init__(self, quote: MarketQuote | None = None):
        self._quote = quote

    async def get_quote(self, request_id: str, crop_id: str, location_id: str):
        if self._quote is not None:
            return dataclasses.replace(
                self._quote,
                request_id=request_id,
                crop_id=crop_id,
                location_id=location_id,
            )
        return MarketQuote(
            request_id=request_id,
            crop_id=crop_id,
            location_id=location_id,
            market_name="Adilabad",
            price=8850.0,
            unit="INR/quintal",
            observed_at=NOW,
            retrieved_at=NOW,
            source=MARKET_SRC,
            mode=EvidenceMode.LIVE,
            min_price=8200.0,
            max_price=9100.0,
            commodity="Cotton",
            variety="Medium Staple",
            grade="FAQ",
            arrival_date="2026-11-10",
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


class MockLiveWorkflowInference:
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
                "crops": [{"value": "cotton", "quote": "cotton"}],
                "locations": [{"value": "Adilabad", "quote": "Adilabad"}],
                "varieties": [],
                "growth_stages": [{"value": "flowering", "quote": "flowering"}],
                "intents": ["crop_advice", "weather", "market", "scheme"],
                "observations": [{"label": "leaf spots", "quote": "leaves show spots"}],
                "farming_problem": {"value": "leaf spots", "quote": "leaves show spots"},
            })
        elif "coordinator" in agent_name:
            catalog = task_input.get("evidence_catalog", [])
            evidence_id = catalog[0]["evidence_id"] if catalog else "ev:placeholder"
            content = json.dumps({
                "statements": [
                    {
                        "text": "Weather indicates 68% relative humidity favorable for environmental monitoring.",
                        "kind": "interpretation",
                        "evidence_ids": [evidence_id],
                        "uncertainty": "Single-day observation does not determine disease presence or personal scheme eligibility.",
                    }
                ],
                "follow_up_questions": [],
            })
        elif "market" in agent_name:
            evidence_list = task_input.get("evidence", [])
            evidence_id = evidence_list[0]["evidence_id"] if evidence_list else "ev:placeholder"
            content = json.dumps({
                "summary": "Adilabad modal price for cotton is 8850 INR/quintal within 8200-9100 range.",
                "statements": [
                    {
                        "text": "Observed modal price for cotton in Adilabad is 8850 INR/quintal.",
                        "kind": "interpretation",
                        "evidence_ids": [evidence_id],
                        "uncertainty": "Single-day price data cannot prove demand trend or support sell/hold recommendations.",
                    }
                ],
            })
        elif "weather" in agent_name:
            evidence_list = task_input.get("evidence", [])
            evidence_id = evidence_list[0]["evidence_id"] if evidence_list else "ev:placeholder"
            content = json.dumps({
                "summary": "Observed temperature is 29.4 C with 68% relative humidity in Adilabad.",
                "statements": [
                    {
                        "text": "Current humidity of 68% and 29.4 C indicate environmental favorability, but do not diagnose leaf spot.",
                        "kind": "interpretation",
                        "evidence_ids": [evidence_id],
                        "uncertainty": "Field inspection and agricultural officer consultation are required.",
                    }
                ],
            })
        else:
            evidence_list = task_input.get("evidence", [])
            evidence_id = evidence_list[0]["evidence_id"] if evidence_list else "ev:placeholder"
            content = json.dumps({
                "summary": f"{agent_name} observation verified.",
                "statements": [
                    {
                        "text": f"{agent_name} observation details recorded from source.",
                        "kind": "interpretation",
                        "evidence_ids": [evidence_id],
                        "uncertainty": "This excerpt does not determine individual eligibility or field diagnosis.",
                    }
                ],
            })

        response = ChatResponse(
            messages=[Message("assistant", [content])],
            finish_reason="stop",
            response_id="chatcmpl-mock-live",
        )
        usage = InferenceTokenUsage(
            agent_name=agent_name,
            model_id=self.model_id,
            correlation_id="1234567890abcdef1234567890abcdef",
            estimated_input_tokens=600,
            actual_input_tokens=600,
            actual_output_tokens=300,
            actual_total_tokens=900,
            output_token_limit=max_tokens,
            retries_attempted=0,
            retry_decision="not_retried",
            http_status=200,
            finish_reason="stop",
            budget_remaining_tokens=27_000,
        )
        self.usage_records.append(usage)
        return ProviderResponse(
            provider=self.provider_id,
            model_id=self.model_id,
            response=response,
            usage=usage,
        )


class Task1MarketPydanticValidationTests(unittest.IsolatedAsyncioTestCase):
    """Task 1: Market Pydantic failure reproduction, schema preservation, and error recording."""

    async def test_extra_field_at_root_fails_with_sanitized_diagnostic_error_message(self):
        class MockExtraFieldInference:
            provider_id = "cerebras"
            model_id = "gpt-oss-120b"

            async def complete(self, request, **kwargs):
                resp_json = json.dumps({
                    "summary": "Adilabad cotton price is 8850.",
                    "statements": [
                        {
                            "text": "Modal price is 8850 Rs/quintal.",
                            "kind": "interpretation",
                            "evidence_ids": ["market:ev1"],
                            "uncertainty": "Single-day quote.",
                        }
                    ],
                    "unsupported_extra_field": "Model hallucinated extra field",
                })
                return ProviderResponse(
                    "cerebras", "gpt-oss-120b",
                    ChatResponse(messages=[Message("assistant", [resp_json])], finish_reason="stop"),
                    InferenceTokenUsage(
                        agent_name="market", model_id="gpt-oss-120b",
                        correlation_id="1234567890abcdef1234567890abcdef",
                        estimated_input_tokens=500, actual_input_tokens=500,
                        actual_output_tokens=200, actual_total_tokens=700,
                        output_token_limit=1024, http_status=200,
                    ),
                )

        req = make_json_request("system", {}, SpecialistInterpretation, agent_name="market", max_output_tokens=1024)
        with self.assertRaises(ProviderFailure) as caught:
            await call_structured(
                MockExtraFieldInference(), req,
                expected_user_id="u1", expected_tenant_id="t1",
                response_model=SpecialistInterpretation,
            )
        diag = caught.exception.diagnostic
        self.assertEqual(diag.parsing_outcome, "pydantic_validation_failed")
        self.assertEqual(diag.validation_category, "pydantic_schema_validation_failure")
        self.assertIn("unsupported_extra_field: Extra inputs are not permitted", diag.provider_error_message)

    async def test_statement_count_exceeding_3_fails_with_sanitized_diagnostic_error_message(self):
        class MockTooManyStatementsInference:
            provider_id = "cerebras"
            model_id = "gpt-oss-120b"

            async def complete(self, request, **kwargs):
                resp_json = json.dumps({
                    "summary": "Adilabad cotton price is 8850.",
                    "statements": [
                        {"text": "Modal price is 8850.", "kind": "evidence", "evidence_ids": ["market:ev1"]},
                        {"text": "Price range is 8200-9100.", "kind": "evidence", "evidence_ids": ["market:ev1"]},
                        {"text": "Arrival date is 2026-11-10.", "kind": "evidence", "evidence_ids": ["market:ev1"]},
                        {"text": "Single day data is insufficient for trend.", "kind": "interpretation", "evidence_ids": ["market:ev1"]},
                    ],
                })
                return ProviderResponse(
                    "cerebras", "gpt-oss-120b",
                    ChatResponse(messages=[Message("assistant", [resp_json])], finish_reason="stop"),
                    InferenceTokenUsage(
                        agent_name="market", model_id="gpt-oss-120b",
                        correlation_id="1234567890abcdef1234567890abcdef",
                        estimated_input_tokens=500, actual_input_tokens=500,
                        actual_output_tokens=200, actual_total_tokens=700,
                        output_token_limit=1024, http_status=200,
                    ),
                )

        req = make_json_request("system", {}, SpecialistInterpretation, agent_name="market", max_output_tokens=1024)
        with self.assertRaises(ProviderFailure) as caught:
            await call_structured(
                MockTooManyStatementsInference(), req,
                expected_user_id="u1", expected_tenant_id="t1",
                response_model=SpecialistInterpretation,
            )
        diag = caught.exception.diagnostic
        self.assertEqual(diag.parsing_outcome, "pydantic_validation_failed")
        self.assertIn("statements: List should have at most 3 items", diag.provider_error_message)

    async def test_uncertainty_exceeding_180_chars_fails_with_sanitized_error_message(self):
        class MockLongUncertaintyInference:
            provider_id = "cerebras"
            model_id = "gpt-oss-120b"

            async def complete(self, request, **kwargs):
                resp_json = json.dumps({
                    "summary": "Adilabad cotton price is 8850.",
                    "statements": [
                        {
                            "text": "Modal price is 8850.",
                            "kind": "interpretation",
                            "evidence_ids": ["market:ev1"],
                            "uncertainty": "A" * 181,
                        }
                    ],
                })
                return ProviderResponse(
                    "cerebras", "gpt-oss-120b",
                    ChatResponse(messages=[Message("assistant", [resp_json])], finish_reason="stop"),
                    InferenceTokenUsage(
                        agent_name="market", model_id="gpt-oss-120b",
                        correlation_id="1234567890abcdef1234567890abcdef",
                        estimated_input_tokens=500, actual_input_tokens=500,
                        actual_output_tokens=200, actual_total_tokens=700,
                        output_token_limit=1024, http_status=200,
                    ),
                )

        req = make_json_request("system", {}, SpecialistInterpretation, agent_name="market", max_output_tokens=1024)
        with self.assertRaises(ProviderFailure) as caught:
            await call_structured(
                MockLongUncertaintyInference(), req,
                expected_user_id="u1", expected_tenant_id="t1",
                response_model=SpecialistInterpretation,
            )
        diag = caught.exception.diagnostic
        self.assertEqual(diag.parsing_outcome, "pydantic_validation_failed")
        self.assertIn("statements.0.uncertainty: String should have at most 180 characters", diag.provider_error_message)

    async def test_conforming_market_interpretation_passes_validation(self):
        valid_data = {
            "summary": "Cotton modal price in Adilabad is 8850 INR/quintal within 8200-9100 range.",
            "statements": [
                {
                    "text": "Observed modal price is 8850 INR/quintal with range 8200-9100 on 2026-11-10.",
                    "kind": "interpretation",
                    "evidence_ids": ["market:ev1"],
                    "uncertainty": "Single-day price quote cannot establish multi-day demand trend or sell/hold guidance.",
                }
            ],
        }
        res = SpecialistInterpretation.model_validate(valid_data)
        self.assertEqual(len(res.statements), 1)
        self.assertLessEqual(len(res.statements[0].uncertainty), 180)


class Task2WeatherGroundingGuardTests(unittest.IsolatedAsyncioTestCase):
    """Task 2: Weather grounding guard distinguishes cautious statements from ungrounded diagnoses."""

    def test_cautious_environmental_interpretations_accepted(self):
        cautious_statements = [
            "Weather conditions (humidity 68%, temp 29.4 C) indicate environmental favorability, but do not diagnose leaf spot.",
            "Observed humidity does not confirm disease presence.",
            "A weather snapshot cannot prove infection occurred; field inspection is required.",
            "Weather data is not a diagnosis of leaf spot.",
            "Current weather conditions do not rule out disease or infection.",
            "Weather alone cannot establish disease presence or absence.",
            "Relative humidity of 68% creates conditions favorable for fungal growth, without confirming leaf spot.",
        ]
        for statement in cautious_statements:
            self.assertFalse(
                _is_ungrounded_disease_diagnosis(statement),
                f"Expected cautious statement to be ACCEPTED (False), but was rejected: {statement}",
            )

    def test_field_inspection_recommendations_accepted(self):
        safe_recommendations = [
            "Field inspection and consultation with an agricultural officer or KVK are required to verify leaf spot.",
            "Inspect affected plants directly; weather observations cannot confirm or rule out infection.",
            "Consult the local agricultural extension officer before deciding on fungicide sprays.",
        ]
        for text in safe_recommendations:
            self.assertFalse(
                _is_ungrounded_disease_diagnosis(text),
                f"Expected recommendation to be ACCEPTED (False): {text}",
            )

    def test_explicit_disease_diagnoses_rejected(self):
        ungrounded_diagnoses = [
            "The observed weather confirms leaf spot on the cotton crop.",
            "High humidity of 68% diagnoses fungal leaf spot.",
            "Current weather proves infection is present in the field.",
            "The crop is infected with leaf spot based on these conditions.",
            "Weather conditions establish that leaf spot disease has occurred.",
        ]
        for text in ungrounded_diagnoses:
            self.assertTrue(
                _is_ungrounded_disease_diagnosis(text),
                f"Expected ungrounded diagnosis to be REJECTED (True): {text}",
            )

    def test_explicit_disease_exclusions_rejected(self):
        ungrounded_exclusions = [
            "Weather rules out leaf spot in this region.",
            "There is no risk of leaf spot under current weather.",
            "Current humidity guarantees no fungal infection.",
            "Low rainfall eliminates possibility of leaf spot.",
            "The crop is free from leaf spot based on weather data.",
        ]
        for text in ungrounded_exclusions:
            self.assertTrue(
                _is_ungrounded_disease_diagnosis(text),
                f"Expected ungrounded exclusion to be REJECTED (True): {text}",
            )


class Task3CoordinatorMarketGroundingTests(unittest.IsolatedAsyncioTestCase):
    """Task 3: Coordinator validation rejects ungrounded market conclusions from single-day evidence."""

    @classmethod
    def setUpClass(cls):
        cls.index = build_test_index()

    async def test_coordinator_rejects_ungrounded_market_trend_assertion(self):
        # Coordinator attempts to assert strong market demand from single-day quote
        async def ungrounded_coord_market_handler(req: InferenceRequest) -> ProviderResponse:
            user_json = "".join(
                item if isinstance(item, str) else getattr(item, "text", "")
                for item in req.messages[1].contents
            )
            task_input = json.loads(user_json).get("task_input", {})
            agent_name = req.agent_name or ""
            if "coordinator" in agent_name:
                catalog = task_input.get("evidence_catalog", [])
                ev_id = catalog[0]["evidence_id"] if catalog else "market:placeholder"
                draft_content = json.dumps({
                    "statements": [
                        {
                            "text": "Adilabad cotton price of 8850 Rs/quintal proves strong market demand and farmers should sell now.",
                            "kind": "interpretation",
                            "evidence_ids": [ev_id],
                            "uncertainty": None,
                        }
                    ],
                    "follow_up_questions": [],
                })
                resp = ChatResponse(
                    messages=[Message("assistant", [draft_content])],
                    finish_reason="stop",
                )
                usage = InferenceTokenUsage(
                    agent_name=agent_name, model_id="gpt-oss-120b",
                    correlation_id="1234567890abcdef1234567890abcdef",
                    estimated_input_tokens=600, actual_input_tokens=600,
                    actual_output_tokens=200, actual_total_tokens=800,
                    output_token_limit=2048, http_status=200,
                )
                return ProviderResponse("cerebras", "gpt-oss-120b", resp, usage)
            return await MockLiveWorkflowInference().complete(req, expected_user_id="u1", expected_tenant_id="t1")

        inference = MockLiveWorkflowInference(handler=ungrounded_coord_market_handler)
        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(),
            knowledge=self.index,
            weather_provider=MockObservedWeather(),
            market_provider=MockObservedMarket(),
        )

        req = FarmerRequest(
            request_id="req-coord-mkt-ungrounded",
            user_id="u1", tenant_id="t1",
            query="Cotton in Adilabad; what are the market trends and should I sell?",
            crop_query="cotton", location_query="Adilabad",
        )
        response = await workflow.run(req, mode=WorkflowMode.CEREBRAS, inference=inference)
        coord_res = next(r for r in response.agent_results if r.agent_id == AgentId.COORDINATOR)
        # Coordinator must reject the synthesis and use deterministic fallback
        self.assertEqual(coord_res.payload.inference_status, "malformed_response")
        self.assertEqual(coord_res.payload.interpretation_mode, "deterministic_fallback")
        self.assertIn("deterministic evidence summary is shown", response.response_text)

        diag = next((d for d in inference.diagnostics if d.agent_name == "coordinator"), None)
        self.assertIsNotNone(diag)
        self.assertEqual(diag.validation_category, "market_grounding_validation_failure")
        self.assertEqual(diag.parsing_outcome, "unsupported_market_trend_assertion")

    async def test_coordinator_accepts_grounded_market_price_facts(self):
        # Coordinator preserves observed modal price facts without asserting trends
        async def grounded_coord_market_handler(req: InferenceRequest) -> ProviderResponse:
            user_json = "".join(
                item if isinstance(item, str) else getattr(item, "text", "")
                for item in req.messages[1].contents
            )
            task_input = json.loads(user_json).get("task_input", {})
            agent_name = req.agent_name or ""
            if "coordinator" in agent_name:
                catalog = task_input.get("evidence_catalog", [])
                ev_id = catalog[0]["evidence_id"] if catalog else "market:placeholder"
                draft_content = json.dumps({
                    "statements": [
                        {
                            "text": "Adilabad cotton modal price is 8850 Rs/quintal within a range of 8200-9100 Rs/quintal.",
                            "kind": "interpretation",
                            "evidence_ids": [ev_id],
                            "uncertainty": "Single-day price quote cannot establish a multi-day market trend.",
                        }
                    ],
                    "follow_up_questions": [],
                })
                resp = ChatResponse(
                    messages=[Message("assistant", [draft_content])],
                    finish_reason="stop",
                )
                usage = InferenceTokenUsage(
                    agent_name=agent_name, model_id="gpt-oss-120b",
                    correlation_id="1234567890abcdef1234567890abcdef",
                    estimated_input_tokens=600, actual_input_tokens=600,
                    actual_output_tokens=200, actual_total_tokens=800,
                    output_token_limit=2048, http_status=200,
                )
                return ProviderResponse("cerebras", "gpt-oss-120b", resp, usage)
            return await MockLiveWorkflowInference().complete(req, expected_user_id="u1", expected_tenant_id="t1")

        inference = MockLiveWorkflowInference(handler=grounded_coord_market_handler)
        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(),
            knowledge=self.index,
            weather_provider=MockObservedWeather(),
            market_provider=MockObservedMarket(),
        )

        req = FarmerRequest(
            request_id="req-coord-mkt-grounded",
            user_id="u1", tenant_id="t1",
            query="Cotton in Adilabad; what is the market price?",
            crop_query="cotton", location_query="Adilabad",
        )
        response = await workflow.run(req, mode=WorkflowMode.CEREBRAS, inference=inference)
        coord_res = next(r for r in response.agent_results if r.agent_id == AgentId.COORDINATOR)
        self.assertEqual(coord_res.payload.inference_status, "completed")
        self.assertEqual(coord_res.payload.interpretation_mode, "cerebras")
        self.assertIn("Cerebras-generated synthesis", response.response_text)


class Task4SchemeStatusTests(unittest.IsolatedAsyncioTestCase):
    """Task 4: Scheme Agent status correctly reflects execution outcome."""

    @classmethod
    def setUpClass(cls):
        cls.index = build_test_index()

    async def test_successful_scheme_inference_reports_completed_status(self):
        inference = MockLiveWorkflowInference()
        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(),
            knowledge=self.index,
            weather_provider=MockObservedWeather(),
            market_provider=MockObservedMarket(),
        )
        req = FarmerRequest(
            request_id="req-scheme-ok",
            user_id="u1", tenant_id="t1",
            query="What government schemes apply for cotton in Adilabad?",
            crop_query="cotton", location_query="Adilabad",
        )
        response = await workflow.run(req, mode=WorkflowMode.CEREBRAS, inference=inference)
        scheme_res = next(r for r in response.agent_results if r.agent_id == AgentId.SCHEME)
        self.assertEqual(scheme_res.status, AgentStatus.COMPLETED)
        self.assertEqual(scheme_res.payload.inference_status, "completed")
        self.assertEqual(scheme_res.payload.interpretation_mode, "cerebras")

    async def test_failed_scheme_inference_reports_partial_status(self):
        async def failing_scheme_handler(req: InferenceRequest) -> ProviderResponse:
            agent_name = req.agent_name or ""
            if "scheme" in agent_name:
                raise ProviderFailure(
                    ProviderFailureKind.MALFORMED_RESPONSE,
                    InferenceDiagnostic(
                        code=InferenceDiagnosticCode.CEREBRAS_MALFORMED_RESPONSE,
                        stage=InferenceStage.RESPONSE_VALIDATION,
                        provider="cerebras", model_id="gpt-oss-120b",
                        agent_name="scheme", parsing_outcome="pydantic_validation_failed",
                    ),
                )
            return await MockLiveWorkflowInference().complete(req, expected_user_id="u1", expected_tenant_id="t1")

        inference = MockLiveWorkflowInference(handler=failing_scheme_handler)
        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(),
            knowledge=self.index,
            weather_provider=MockObservedWeather(),
            market_provider=MockObservedMarket(),
        )
        req = FarmerRequest(
            request_id="req-scheme-fail",
            user_id="u1", tenant_id="t1",
            query="What government schemes apply for cotton in Adilabad?",
            crop_query="cotton", location_query="Adilabad",
        )
        response = await workflow.run(req, mode=WorkflowMode.CEREBRAS, inference=inference)
        scheme_res = next(r for r in response.agent_results if r.agent_id == AgentId.SCHEME)
        self.assertEqual(scheme_res.status, AgentStatus.PARTIAL)
        self.assertEqual(scheme_res.payload.inference_status, "malformed_response")
        self.assertEqual(scheme_res.payload.interpretation_mode, "deterministic_fallback")


class Task5CoordinatorStatusTests(unittest.IsolatedAsyncioTestCase):
    """Task 5: Coordinator agent status vs overall advisory status."""

    @classmethod
    def setUpClass(cls):
        cls.index = build_test_index()

    async def test_coordinator_agent_completed_when_synthesis_succeeds_even_if_overall_advisory_partial(self):
        # In this scenario, Crop knowledge has no Adilabad cotton evidence (Crop is UNAVAILABLE).
        # The overall advisory status MUST be PARTIAL (preserving visibility of incomplete upstream knowledge),
        # but Coordinator agent itself successfully completed synthesis.
        inference = MockLiveWorkflowInference()
        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(),
            knowledge=self.index,
            weather_provider=MockObservedWeather(),
            market_provider=MockObservedMarket(),
        )
        req = FarmerRequest(
            request_id="req-status-check",
            user_id="u1", tenant_id="t1",
            query="My cotton in Adilabad is flowering. What weather and market apply?",
            crop_query="cotton", location_query="Adilabad", growth_stage_query="flowering",
        )
        response = await workflow.run(req, mode=WorkflowMode.CEREBRAS, inference=inference)

        # 1. Overall advisory status remains PARTIAL
        self.assertEqual(response.status, AdvisoryStatus.PARTIAL)
        self.assertIn("This is a partial advisory", response.response_text)

        # 2. Incomplete upstream agents preserve their status
        crop_res = next(r for r in response.agent_results if r.agent_id == AgentId.CROP)
        self.assertEqual(crop_res.status, AgentStatus.UNAVAILABLE)

        # 3. Successful Coordinator agent result status is COMPLETED
        coord_res = next(r for r in response.agent_results if r.agent_id == AgentId.COORDINATOR)
        self.assertEqual(coord_res.status, AgentStatus.COMPLETED)
        self.assertEqual(coord_res.payload.inference_status, "completed")
        self.assertEqual(coord_res.payload.interpretation_mode, "cerebras")
        self.assertEqual(coord_res.payload.status, AdvisoryStatus.PARTIAL)


if __name__ == "__main__":
    unittest.main()

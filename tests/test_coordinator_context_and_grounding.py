"""Regression and unit tests for Coordinator context optimization, evidence conflict resolution,
weather grounding, and market evidence consistency.

Covers:
Task 1: Coordinator input optimization:
  - Context packing reduces estimated tokens safely below 3,500 tokens.
  - All relevant evidence IDs (weather, market, schemes) are retained without silent omission.
  - Verifies before/after token estimates and budget calculations.

Task 2: Correct evidence conflict classification:
  - PMFBY, Telangana Rythu Bharosa, and PM-KISAN findings are independent schemes (no conflict).
  - Market price observation and single-day trend warning are compatible claims (no conflict).
  - Conflicting numerical values (soil pH 6.5 vs 8.0) and direct contradictions (rainfall available vs unavailable) are genuinely contradictory.
  - Duplicate claims are recognized as duplicates.

Task 3: Weather grounding:
  - Rejects ungrounded agronomic evaluations ('moderate conditions for cotton flowering').
  - Rejects ungrounded disease pressure exclusions ('no immediate weather-driven increase in fungal disease pressure').
  - Rejects explicit disease diagnoses ('confirms leaf spot').
  - Accepts factual meteorological observations and cautious disease warnings.

Task 4: Market evidence consistency:
  - Uses Adilabad APMC, Cotton (Unginned), modal ₹8,870/quintal, range ₹8,231–₹8,870, arrival 12/11/2026.
  - Accepts conforming interpretation reporting observed price facts.
  - Rejects contradictory interpretation claiming 'no current market-price evidence was provided'.
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
    AgentResult,
    AgentStatus,
    GeneratedStatement,
    IntakePayload,
    MarketQuote,
    ProviderPayload,
    RetrievalPayload,
    WeatherObservation,
)
from rythusaathi.agents.llm import (
    CoordinatorDraft,
    GroundedStatement,
    SpecialistInterpretation,
    make_json_request,
)
from rythusaathi.agents.workflow import (
    _COORDINATOR_PROMPT,
    _UNGROUNDED_AGRONOMIC_CROP_PATTERN,
    _UNGROUNDED_MARKET_TREND_PATTERN,
    _RULING_OUT_DISEASE_PRESSURE_PATTERN,
    _CONTRADICTORY_MARKET_EVIDENCE_PATTERN,
    _UNSUPPORTED_AGRONOMIC_RECOMMENDATION_PATTERN,
    _build_coordinator_context,
    _is_ungrounded_disease_diagnosis,
)
from rythusaathi.domain import (
    AdvisoryBoard,
    AgentFinding,
    ClaimRelationship,
    ContentReference,
    EvidenceMode,
    EvidenceRecord,
    EvidenceSource,
    EvidenceSourceType,
    EvidenceStatus,
    FindingConflict,
    ReferenceKind,
    RequestContext,
    classify_claim_relationship,
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
from rythusaathi.inference.providers.groq import estimate_input_tokens
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
            market_name="Adilabad APMC",
            price=8870.0,
            unit="INR/quintal",
            observed_at=NOW,
            retrieved_at=NOW,
            source=MARKET_SRC,
            mode=EvidenceMode.LIVE,
            min_price=8231.0,
            max_price=8870.0,
            commodity="Cotton (Unginned)",
            variety="Medium Staple",
            grade="FAQ",
            arrival_date="12/11/2026",
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
            farmer_q = task_input.get("farmer_question", "")
            leaf_quote = "leaf spots" if "leaf" in farmer_q.lower() else None
            obs = [{"label": "leaf spots", "quote": leaf_quote}] if leaf_quote else []
            prob = {"value": "leaf spots", "quote": leaf_quote} if leaf_quote else None
            content = json.dumps({
                "crops": [{"value": "cotton", "quote": "cotton"}] if "cotton" in farmer_q.lower() else [],
                "locations": [{"value": "Adilabad", "quote": "Adilabad"}] if "adilabad" in farmer_q.lower() else [],
                "varieties": [],
                "growth_stages": [{"value": "flowering", "quote": "flowering"}] if "flowering" in farmer_q.lower() else [],
                "intents": ["crop_advice", "weather", "market", "scheme"],
                "observations": obs,
                "farming_problem": prob,
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
                "summary": "Adilabad APMC modal price for Cotton (Unginned) is 8870 INR/quintal within 8231-8870 range.",
                "statements": [
                    {
                        "text": "Observed modal price for Cotton (Unginned) in Adilabad APMC is 8870 INR/quintal.",
                        "kind": "interpretation",
                        "evidence_ids": [evidence_id],
                        "uncertainty": "Single-day price quote cannot establish a multi-day market trend.",
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
                        "text": "Weather observation shows temperature of 29.4 C with 68% relative humidity in Adilabad.",
                        "kind": "interpretation",
                        "evidence_ids": [evidence_id],
                        "uncertainty": "Single weather snapshot cannot diagnose crop diseases; inspect field for symptoms.",
                    }
                ],
            })
        elif "scheme" in agent_name:
            evidence_list = task_input.get("evidence", [])
            evidence_id = evidence_list[0]["evidence_id"] if evidence_list else "ev:placeholder"
            content = json.dumps({
                "summary": "Central and state scheme guidelines applicable for cotton farmers in Telangana.",
                "statements": [
                    {
                        "text": "Telangana Rythu Bharosa Scheme provides investment assistance of Rs. 12,000 per acre per year.",
                        "kind": "interpretation",
                        "evidence_ids": [evidence_id],
                        "uncertainty": "Does not determine individual eligibility; verify rules with issuing authority.",
                    }
                ],
            })
        else:
            content = json.dumps({"summary": "no action"})

        resp = ChatResponse(
            messages=[Message("assistant", [content])],
            finish_reason="stop",
        )
        usage = InferenceTokenUsage(
            agent_name=agent_name,
            model_id="gpt-oss-120b",
            correlation_id="1234567890abcdef1234567890abcdef",
            estimated_input_tokens=500,
            actual_input_tokens=500,
            actual_output_tokens=200,
            actual_total_tokens=700,
            output_token_limit=max_tokens,
            http_status=200,
        )
        return ProviderResponse("cerebras", "gpt-oss-120b", resp, usage)


class Task1CoordinatorContextOptimizationTests(unittest.TestCase):
    """Task 1: Optimize Coordinator input and reduce context tokens below 3,500."""

    @classmethod
    def setUpClass(cls):
        cls.index = build_test_index()

    def test_coordinator_context_tokens_reduced_safely_below_3500(self):
        # Build comprehensive multi-agent evidence matching live Cerebras scenario:
        # Weather + Market + 3 Scheme records (Telangana Rythu Bharosa, PM-KISAN, PMFBY)
        from rythusaathi.knowledge import RetrievalFilter
        from rythusaathi.agents.workflow import _deduplicate_and_merge_scheme_hits

        hits = self.index.search(
            "government schemes for cotton farmer in Adilabad",
            top_k=4,
            filters=RetrievalFilter(
                geography="location:district:in-tg-adilabad",
                topic="government_scheme",
                jurisdiction_level="central+state",
            ),
        )
        scheme_ev = _deduplicate_and_merge_scheme_hits(hits, "req-opt-test")

        weather_ev = EvidenceRecord(
            evidence_id="weather:1", request_id="req-opt-test", source=WEATHER_SRC,
            excerpt="temperature 29.4 °C, rainfall 0 mm, humidity 68%, forecast: 3-day forecast: min 22 °C, max 34 °C; observed at 2026-11-12T12:00:00Z.",
            content_reference=WEATHER_SRC.reference, retrieved_at=NOW,
            geographic_applicability=("location:district:in-tg-adilabad",), crop_applicability=(),
            provenance="Open-Meteo live observation", mode=EvidenceMode.LIVE, status=EvidenceStatus.AVAILABLE,
        )
        market_ev = EvidenceRecord(
            evidence_id="market:1", request_id="req-opt-test", source=MARKET_SRC,
            excerpt="Adilabad APMC: modal price 8870 INR/quintal; range 8231-8870 INR/quintal; commodity: Cotton (Unginned); variety: Medium Staple; arrival date: 12/11/2026; observed at 2026-11-12T12:00:00Z.",
            content_reference=MARKET_SRC.reference, retrieved_at=NOW,
            geographic_applicability=("location:district:in-tg-adilabad",), crop_applicability=("crop:gossypium-hirsutum",),
            provenance="data.gov.in AGMARKNET quote", mode=EvidenceMode.LIVE, status=EvidenceStatus.AVAILABLE,
        )

        all_evidence = [weather_ev, market_ev] + scheme_ev
        evidence_ids_before = {e.evidence_id for e in all_evidence}

        weather_res = AgentResult(
            agent_id=AgentId.WEATHER, status=AgentStatus.COMPLETED, started_at=NOW, completed_at=NOW,
            payload=ProviderPayload(True, None, "location:district:in-tg-adilabad", "live weather summary", None,
                "cerebras", "completed", (
                    GeneratedStatement(text="Observed temperature is 29.4 C with 68% relative humidity in Adilabad.", kind="interpretation", evidence_ids=("weather:1",), uncertainty="Observation covers current conditions only."),
                ),
            ),
            evidence_ids=("weather:1",),
        )
        market_res = AgentResult(
            agent_id=AgentId.MARKET, status=AgentStatus.COMPLETED, started_at=NOW, completed_at=NOW,
            payload=ProviderPayload(True, None, "location:district:in-tg-adilabad", "live market summary", None,
                "cerebras", "completed", (
                    GeneratedStatement(text="Adilabad APMC modal price for cotton is 8870 INR/quintal with range 8231-8870 INR/quintal.", kind="interpretation", evidence_ids=("market:1",), uncertainty="Single-day price quote cannot establish a trend."),
                ),
            ),
            evidence_ids=("market:1",),
        )
        crop_res = AgentResult(
            agent_id=AgentId.CROP, status=AgentStatus.UNAVAILABLE, started_at=NOW, completed_at=NOW,
            payload=RetrievalPayload(0, (), (), (), "No evidence met the crop, geographic and growth-stage filters; no recommendation was inferred.", None, "not_run_no_evidence", None, ()),
            evidence_ids=(),
        )
        scheme_res = AgentResult(
            agent_id=AgentId.SCHEME, status=AgentStatus.COMPLETED, started_at=NOW, completed_at=NOW,
            payload=RetrievalPayload(len(scheme_ev), tuple(e.evidence_id for e in scheme_ev), ("Telangana Rythu Bharosa", "PM-KISAN", "PMFBY"), (), "Retrieved scheme excerpts.",
                "Official scheme summary", "cerebras", "completed", tuple(
                    GeneratedStatement(text=f"Scheme statement for {e.source.title} covering eligibility and DBT guidelines.", kind="interpretation", evidence_ids=(e.evidence_id,), uncertainty="Verify eligibility with issuing authority.")
                    for e in scheme_ev
                ),
            ),
            evidence_ids=tuple(e.evidence_id for e in scheme_ev),
        )
        branch_results = (weather_res, crop_res, market_res, scheme_res)

        pack = _build_coordinator_context(
            farmer_question="My cotton crop in Adilabad is at flowering stage. Leaves show spots. What should I do?",
            crop="cotton", location="Adilabad", growth_stage="flowering",
            intake_payload=IntakePayload(None, None, (), ("leaf spots",), (), "deterministic", "completed", "leaves show spots", "cerebras", "gpt-oss-120b"),
            branch_results=branch_results, eligible_evidence=all_evidence,
            missing_fields=(), conflicts=(), remaining_tpm_tokens=27000,
            coordinator_preferred_input_tokens=3500,
            coordinator_output_reservation=2048,
        )

        # 1. Fits within 3,500 safe budget
        self.assertTrue(pack.fits_budget, f"Expected pack to fit budget, but estimated {pack.estimated_input_tokens} > {pack.input_limit_tokens}")
        self.assertLess(pack.estimated_input_tokens, 3500)

        # 2. Evidence IDs retained without silent removal
        evidence_ids_after = set(pack.selected_evidence_ids)
        self.assertEqual(evidence_ids_before, evidence_ids_after)
        self.assertEqual(len(pack.excluded_evidence), 0)

        # 3. Required catalogs and fields preserved
        self.assertIn("source_catalog", pack.payload)
        self.assertIn("evidence_catalog", pack.payload)
        self.assertIn("retrieved_evidence", pack.payload)
        for cat in pack.payload["evidence_catalog"]:
            self.assertIn("evidence_id", cat)
            self.assertIn("source_id", cat)
            self.assertIn("mode", cat)


class Task2EvidenceConflictClassificationTests(unittest.TestCase):
    """Task 2: Correct evidence conflict classification."""

    def setUp(self):
        ctx = RequestContext(request_id="req-conflict-test", user_id="u1", tenant_id="t1")
        self.board = AdvisoryBoard(ctx)
        self.writer = self.board.writer_for(AgentId.SCHEME)

    def test_independent_schemes_are_not_conflicting(self):
        # PMFBY, Telangana Rythu Bharosa, and PM-KISAN are independent schemes
        f1 = self.writer.publish_finding(
            topic="llm_interpretation:scheme",
            claim="Telangana Rythu Bharosa Scheme provides assistance of Rs. 12,000 per acre per year via direct benefit transfer.",
            evidence_ids=(),
        )
        f2 = self.writer.publish_finding(
            topic="llm_interpretation:scheme",
            claim="PM-KISAN Operational Guidelines provide income support of Rs. 6,000 per year in three installments.",
            evidence_ids=(),
        )
        f3 = self.writer.publish_finding(
            topic="llm_interpretation:scheme",
            claim="Pradhan Mantri Fasal Bima Yojana (PMFBY) provides comprehensive crop insurance coverage against natural risks.",
            evidence_ids=(),
        )

        self.assertEqual(classify_claim_relationship(f1, f2), ClaimRelationship.INDEPENDENT)
        self.assertEqual(classify_claim_relationship(f1, f3), ClaimRelationship.INDEPENDENT)
        self.assertEqual(classify_claim_relationship(f2, f3), ClaimRelationship.INDEPENDENT)

        conflicts = self.board.conflicts()
        self.assertEqual(len(conflicts), 0, "Independent schemes must NOT be labeled as conflicting!")

    def test_market_price_and_single_day_trend_warning_are_compatible(self):
        mkt_writer = self.board.writer_for(AgentId.MARKET)
        f_obs = mkt_writer.publish_finding(
            topic="market_quote",
            claim="Adilabad APMC: modal price 8870 INR/quintal; range 8231-8870 INR/quintal; commodity: Cotton (Unginned).",
        )
        f_caveat = mkt_writer.publish_finding(
            topic="market_quote",
            claim="Single-day price observation cannot establish a multi-day market trend or prove market demand.",
        )

        self.assertEqual(classify_claim_relationship(f_obs, f_caveat), ClaimRelationship.COMPATIBLE)
        conflicts = self.board.conflicts()
        self.assertEqual(len(conflicts), 0, "Market price observation and trend caveat must NOT be labeled as conflicting!")

    def test_genuinely_contradictory_claims_are_conflicts(self):
        crop_writer = self.board.writer_for(AgentId.CROP)
        f_a = crop_writer.publish_finding(topic="soil", claim="pH is 6.5")
        f_b = crop_writer.publish_finding(topic="soil", claim="pH is 8.0")

        self.assertEqual(classify_claim_relationship(f_a, f_b), ClaimRelationship.CONTRADICTORY)
        conflicts = self.board.conflicts()
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(set(conflicts[0].claims), {"pH is 6.5", "pH is 8.0"})

    def test_rainfall_available_vs_unavailable_is_contradictory(self):
        weather_writer = self.board.writer_for(AgentId.WEATHER)
        f_w1 = weather_writer.publish_finding(topic="weather_data", claim="Rainfall data is available for Adilabad.")
        f_w2 = weather_writer.publish_finding(topic="weather_data", claim="No rainfall data available for Adilabad.")

        self.assertEqual(classify_claim_relationship(f_w1, f_w2), ClaimRelationship.CONTRADICTORY)
        conflicts = self.board.conflicts()
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(set(conflicts[0].claims), {f_w1.claim, f_w2.claim})

    def test_duplicate_claims_are_duplicates_not_conflicts(self):
        f_orig = self.writer.publish_finding(topic="policy", claim="Aadhaar is mandatory for DBT enrollment.")
        f_dup = self.writer.publish_finding(topic="policy", claim="Aadhaar is mandatory for DBT enrollment.")

        self.assertEqual(classify_claim_relationship(f_orig, f_dup), ClaimRelationship.DUPLICATE)
        conflicts = self.board.conflicts()
        self.assertEqual(len(conflicts), 0)


class Task3WeatherGroundingTests(unittest.IsolatedAsyncioTestCase):
    """Task 3: Weather Agent grounding - no ungrounded agronomic evaluations or disease ruling-out."""

    @classmethod
    def setUpClass(cls):
        cls.index = build_test_index()

    def test_patterns_match_unsupported_agronomic_and_exclusion_claims(self):
        # 1. Negative cases
        self.assertTrue(bool(_UNGROUNDED_AGRONOMIC_CROP_PATTERN.search("Moderate conditions for cotton flowering.")))
        self.assertTrue(bool(_UNGROUNDED_AGRONOMIC_CROP_PATTERN.search("Optimal conditions for flowering stage.")))
        self.assertTrue(bool(_UNGROUNDED_AGRONOMIC_CROP_PATTERN.search("Weather favors boll formation in cotton.")))
        self.assertTrue(bool(_RULING_OUT_DISEASE_PRESSURE_PATTERN.search("No immediate weather-driven increase in fungal disease pressure.")))
        self.assertTrue(bool(_RULING_OUT_DISEASE_PRESSURE_PATTERN.search("No immediate risk of fungal disease.")))
        self.assertTrue(bool(_RULING_OUT_DISEASE_PRESSURE_PATTERN.search("Weather data rules out leaf spot infection.")))
        self.assertTrue(_is_ungrounded_disease_diagnosis("Weather observations confirm leaf spot disease."))

        # 2. Positive cases (factual / cautious)
        self.assertFalse(bool(_UNGROUNDED_AGRONOMIC_CROP_PATTERN.search("Observed temperature in Adilabad is 29.4 °C with 68% relative humidity.")))
        self.assertFalse(bool(_RULING_OUT_DISEASE_PRESSURE_PATTERN.search("A single weather snapshot cannot diagnose or rule out crop diseases; inspect field for symptoms.")))
        self.assertFalse(_is_ungrounded_disease_diagnosis("Weather observations do not diagnose leaf spot disease; inspect field."))

    async def test_weather_agent_rejects_moderate_flowering_assertion(self):
        async def ungrounded_agronomic_weather_handler(req: InferenceRequest) -> ProviderResponse:
            agent_name = req.agent_name or ""
            if "weather" in agent_name:
                user_json = "".join(
                    item if isinstance(item, str) else getattr(item, "text", "")
                    for item in req.messages[1].contents
                )
                try:
                    task_input = json.loads(user_json).get("task_input", {})
                except Exception:
                    task_input = {}
                evidence_list = task_input.get("evidence", [])
                evidence_id = evidence_list[0]["evidence_id"] if evidence_list else "weather:placeholder"
                content = json.dumps({
                    "summary": "Moderate conditions for cotton flowering.",
                    "statements": [
                        {
                            "text": "Weather measurements indicate moderate conditions for cotton flowering.",
                            "kind": "interpretation",
                            "evidence_ids": [evidence_id],
                            "uncertainty": None,
                        }
                    ],
                })
                resp = ChatResponse(messages=[Message("assistant", [content])], finish_reason="stop")
                usage = InferenceTokenUsage(
                    agent_name=agent_name, model_id="gpt-oss-120b",
                    correlation_id="1234567890abcdef1234567890abcdef",
                    estimated_input_tokens=400, actual_input_tokens=400,
                    actual_output_tokens=150, actual_total_tokens=550,
                    output_token_limit=1024, http_status=200,
                )
                return ProviderResponse("cerebras", "gpt-oss-120b", resp, usage)
            return await MockLiveWorkflowInference().complete(req, expected_user_id="u1", expected_tenant_id="t1")

        inference = MockLiveWorkflowInference(handler=ungrounded_agronomic_weather_handler)
        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(),
            knowledge=self.index,
            weather_provider=MockObservedWeather(),
            market_provider=MockObservedMarket(),
        )
        req = FarmerRequest(
            request_id="req-wtr-agronomic",
            user_id="u1", tenant_id="t1",
            query="Cotton flowering in Adilabad; how is the weather?",
            crop_query="cotton", location_query="Adilabad",
        )
        response = await workflow.run(req, mode=WorkflowMode.CEREBRAS, inference=inference)
        wtr_res = next(r for r in response.agent_results if r.agent_id == AgentId.WEATHER)
        self.assertEqual(wtr_res.payload.inference_status, "malformed_response")
        self.assertEqual(wtr_res.payload.interpretation_mode, "deterministic_fallback")

        diag = next((d for d in inference.diagnostics if d.agent_name == "weather"), None)
        self.assertIsNotNone(diag)
        self.assertEqual(diag.validation_category, "weather_grounding_validation_failure")
        self.assertEqual(diag.parsing_outcome, "unsupported_agronomic_interpretation")

    async def test_weather_agent_rejects_ruling_out_disease_pressure(self):
        async def ruling_out_weather_handler(req: InferenceRequest) -> ProviderResponse:
            agent_name = req.agent_name or ""
            if "weather" in agent_name:
                user_json = "".join(
                    item if isinstance(item, str) else getattr(item, "text", "")
                    for item in req.messages[1].contents
                )
                try:
                    task_input = json.loads(user_json).get("task_input", {})
                except Exception:
                    task_input = {}
                evidence_list = task_input.get("evidence", [])
                evidence_id = evidence_list[0]["evidence_id"] if evidence_list else "weather:placeholder"
                content = json.dumps({
                    "summary": "Current observations show no immediate weather-driven increase in fungal disease pressure.",
                    "statements": [
                        {
                            "text": "There is no immediate weather-driven increase in fungal disease pressure.",
                            "kind": "interpretation",
                            "evidence_ids": [evidence_id],
                            "uncertainty": None,
                        }
                    ],
                })
                resp = ChatResponse(messages=[Message("assistant", [content])], finish_reason="stop")
                usage = InferenceTokenUsage(
                    agent_name=agent_name, model_id="gpt-oss-120b",
                    correlation_id="1234567890abcdef1234567890abcdef",
                    estimated_input_tokens=400, actual_input_tokens=400,
                    actual_output_tokens=150, actual_total_tokens=550,
                    output_token_limit=1024, http_status=200,
                )
                return ProviderResponse("cerebras", "gpt-oss-120b", resp, usage)
            return await MockLiveWorkflowInference().complete(req, expected_user_id="u1", expected_tenant_id="t1")

        inference = MockLiveWorkflowInference(handler=ruling_out_weather_handler)
        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(),
            knowledge=self.index,
            weather_provider=MockObservedWeather(),
            market_provider=MockObservedMarket(),
        )
        req = FarmerRequest(
            request_id="req-wtr-exclusion",
            user_id="u1", tenant_id="t1",
            query="Cotton flowering in Adilabad; what is the weather?",
            crop_query="cotton", location_query="Adilabad",
        )
        response = await workflow.run(req, mode=WorkflowMode.CEREBRAS, inference=inference)
        wtr_res = next(r for r in response.agent_results if r.agent_id == AgentId.WEATHER)
        self.assertEqual(wtr_res.payload.inference_status, "malformed_response")
        self.assertEqual(wtr_res.payload.interpretation_mode, "deterministic_fallback")

        diag = next((d for d in inference.diagnostics if d.agent_name == "weather"), None)
        self.assertIsNotNone(diag)
        self.assertEqual(diag.validation_category, "weather_grounding_validation_failure")
        self.assertEqual(diag.parsing_outcome, "unsupported_disease_exclusion")


class Task4MarketEvidenceConsistencyTests(unittest.IsolatedAsyncioTestCase):
    """Task 4: Market Agent evidence consistency - no false claims that market evidence was not provided."""

    @classmethod
    def setUpClass(cls):
        cls.index = build_test_index()

    async def test_market_accepts_valid_adilkabad_apmc_cotton_quote(self):
        # Quote with: Adilabad APMC, Cotton (Unginned), modal 8870, range 8231-8870, arrival 12/11/2026
        inference = MockLiveWorkflowInference()
        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(),
            knowledge=self.index,
            weather_provider=MockObservedWeather(),
            market_provider=MockObservedMarket(),
        )
        req = FarmerRequest(
            request_id="req-mkt-ok",
            user_id="u1", tenant_id="t1",
            query="Cotton in Adilabad; what is the market price?",
            crop_query="cotton", location_query="Adilabad",
        )
        response = await workflow.run(req, mode=WorkflowMode.CEREBRAS, inference=inference)
        mkt_res = next(r for r in response.agent_results if r.agent_id == AgentId.MARKET)
        self.assertEqual(mkt_res.status, AgentStatus.COMPLETED)
        self.assertEqual(mkt_res.payload.inference_status, "completed")
        self.assertEqual(mkt_res.payload.interpretation_mode, "cerebras")
        self.assertIn("8870", mkt_res.payload.summary)

    async def test_market_rejects_contradictory_claim_that_no_evidence_was_provided(self):
        async def contradictory_market_handler(req: InferenceRequest) -> ProviderResponse:
            agent_name = req.agent_name or ""
            if "market" in agent_name:
                user_json = "".join(
                    item if isinstance(item, str) else getattr(item, "text", "")
                    for item in req.messages[1].contents
                )
                try:
                    task_input = json.loads(user_json).get("task_input", {})
                except Exception:
                    task_input = {}
                evidence_list = task_input.get("evidence", [])
                evidence_id = evidence_list[0]["evidence_id"] if evidence_list else "market:placeholder"
                content = json.dumps({
                    "summary": "No current market-price evidence was provided.",
                    "statements": [
                        {
                            "text": "No current market-price evidence was provided for Adilabad cotton.",
                            "kind": "interpretation",
                            "evidence_ids": [evidence_id],
                            "uncertainty": "No price information is available.",
                        }
                    ],
                })
                resp = ChatResponse(messages=[Message("assistant", [content])], finish_reason="stop")
                usage = InferenceTokenUsage(
                    agent_name=agent_name, model_id="gpt-oss-120b",
                    correlation_id="1234567890abcdef1234567890abcdef",
                    estimated_input_tokens=400, actual_input_tokens=400,
                    actual_output_tokens=150, actual_total_tokens=550,
                    output_token_limit=1024, http_status=200,
                )
                return ProviderResponse("cerebras", "gpt-oss-120b", resp, usage)
            return await MockLiveWorkflowInference().complete(req, expected_user_id="u1", expected_tenant_id="t1")

        inference = MockLiveWorkflowInference(handler=contradictory_market_handler)
        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(),
            knowledge=self.index,
            weather_provider=MockObservedWeather(),
            market_provider=MockObservedMarket(),
        )
        req = FarmerRequest(
            request_id="req-mkt-contradict",
            user_id="u1", tenant_id="t1",
            query="Cotton in Adilabad; what is the market price?",
            crop_query="cotton", location_query="Adilabad",
        )
        response = await workflow.run(req, mode=WorkflowMode.CEREBRAS, inference=inference)
        mkt_res = next(r for r in response.agent_results if r.agent_id == AgentId.MARKET)
        self.assertEqual(mkt_res.payload.inference_status, "malformed_response")
        self.assertEqual(mkt_res.payload.interpretation_mode, "deterministic_fallback")

        diag = next((d for d in inference.diagnostics if d.agent_name == "market"), None)
        self.assertIsNotNone(diag)
        self.assertEqual(diag.validation_category, "market_grounding_validation_failure")
        self.assertEqual(diag.parsing_outcome, "contradictory_evidence_assertion")


class PostLiveFalseConflictAuditTests(unittest.TestCase):
    """Task 1: Audit false conflict presentation with exact latest live outputs."""

    def setUp(self):
        ctx = RequestContext(request_id="req-postlive-audit", user_id="u1", tenant_id="t1")
        self.board = AdvisoryBoard(ctx)

    def _publish_market_evidence(self, evidence_id: str = "market:data-gov-in:test") -> EvidenceRecord:
        writer = self.board.writer_for(AgentId.MARKET)
        rec = EvidenceRecord(
            evidence_id=evidence_id,
            request_id="req-postlive-audit",
            source=EvidenceSource(
                source_id="market:data-gov-in",
                source_identity="data.gov.in",
                source_type=EvidenceSourceType.EXTERNAL_DATA,
                title="APMC Daily Mandi Price",
                reference={"kind": "url", "value": "https://data.gov.in/resource/market"},
            ),
            excerpt="Adilabad APMC cotton price record",
            retrieved_at=datetime.now(timezone.utc),
            provenance="Retrieved from APMC live feed",
            mode=EvidenceMode.LIVE,
            status=EvidenceStatus.AVAILABLE,
        )
        writer.publish_evidence(rec)
        return rec

    def test_scheme_findings_with_scope_limitation_are_not_conflicts(self):
        # Case 1: Scheme findings including statement that sources do not contain weather or market information
        writer = self.board.writer_for(AgentId.SCHEME)
        f_pmfby = writer.publish_finding(
            topic="llm_interpretation:scheme",
            claim="Pradhan Mantri Fasal Bima Yojana (PMFBY) provides comprehensive crop insurance coverage against non-preventable natural risks.",
            evidence_ids=(),
        )
        f_rb = writer.publish_finding(
            topic="llm_interpretation:scheme",
            claim="Telangana Rythu Bharosa Scheme provides assistance of Rs. 12,000 per acre per year via direct benefit transfer.",
            evidence_ids=(),
        )
        f_scope = writer.publish_finding(
            topic="llm_interpretation:scheme",
            claim="The retrieved scheme sources do not contain weather or market information.",
            evidence_ids=(),
        )

        self.assertEqual(classify_claim_relationship(f_pmfby, f_rb), ClaimRelationship.INDEPENDENT)
        self.assertEqual(classify_claim_relationship(f_pmfby, f_scope), ClaimRelationship.COMPATIBLE)
        self.assertEqual(classify_claim_relationship(f_rb, f_scope), ClaimRelationship.COMPATIBLE)

        conflicts = self.board.conflicts()
        self.assertEqual(len(conflicts), 0, "Scheme findings with scope limitation must NOT be labeled as conflicting!")

    def test_market_price_and_single_day_trend_caution_are_not_conflicts(self):
        # Case 2: Market price observation and single-day trend caution
        writer = self.board.writer_for(AgentId.MARKET)
        f_obs = writer.publish_finding(
            topic="llm_interpretation:market",
            claim="Observed modal price for cotton (unginned) in Adilabad APMC is 8870 INR/quintal with range 8231 to 8870 INR/quintal as of arrival date 12/11/2026.",
            evidence_ids=(),
        )
        f_caution = writer.publish_finding(
            topic="llm_interpretation:market",
            claim="A single day's price observation does not prove strong market demand or a multi-day price trend, and does not justify a sell or hold recommendation.",
            evidence_ids=(),
        )

        self.assertEqual(classify_claim_relationship(f_obs, f_caution), ClaimRelationship.COMPATIBLE)

        conflicts = self.board.conflicts()
        self.assertEqual(len(conflicts), 0, "Market price observation and trend caution must NOT be labeled as conflicting!")

    def test_weather_observation_and_forecast_are_not_conflicts(self):
        # Case 3: Weather current observation and forecast
        writer = self.board.writer_for(AgentId.WEATHER)
        f_obs = writer.publish_finding(
            topic="llm_interpretation:weather",
            claim="Current weather observation in Adilabad: temperature 29.4 °C, relative humidity 68%, rainfall 0.0 mm.",
            evidence_ids=(),
        )
        f_forecast = writer.publish_finding(
            topic="llm_interpretation:weather",
            claim="3-day forecast indicates dry weather with maximum temperature reaching 34 °C and minimum 22 °C.",
            evidence_ids=(),
        )

        self.assertEqual(classify_claim_relationship(f_obs, f_forecast), ClaimRelationship.COMPATIBLE)

        conflicts = self.board.conflicts()
        self.assertEqual(len(conflicts), 0, "Weather observation and forecast must NOT be labeled as conflicting!")

    def test_exact_live_market_observation_and_limitation_are_compatible_without_conflicts(self):
        # Criterion 1: Exact live Market observation and limitation statements produce no conflict
        # Criterion 2: Both findings remain stored and accessible after classification
        # Criterion 8: No conflict-resolution logic automatically selects a winner
        ev = self._publish_market_evidence("market:data-gov-in:test")
        writer = self.board.writer_for(AgentId.MARKET)
        f_obs = writer.publish_finding(
            topic="llm_interpretation:market",
            claim="Adilabad APMC reported a modal price of ₹8,870/quintal for Cotton (Unginned), with a range of ₹8,231–₹8,870, on 12/11/2026.",
            evidence_ids=(ev.evidence_id,),
        )
        f_lim = writer.publish_finding(
            topic="llm_interpretation:market",
            claim="A single day's price observation is insufficient to infer demand trends or justify a sell/hold recommendation.",
            evidence_ids=(ev.evidence_id,),
        )

        # 1. Classification is COMPATIBLE symmetrically
        self.assertEqual(classify_claim_relationship(f_obs, f_lim), ClaimRelationship.COMPATIBLE)
        self.assertEqual(classify_claim_relationship(f_lim, f_obs), ClaimRelationship.COMPATIBLE)

        # 2. No conflict generated on board
        conflicts = self.board.conflicts()
        self.assertEqual(len(conflicts), 0, "Compatible live market observation and limitation must not generate conflicts!")

        # 3. Both findings preserved and accessible on board without winner selection
        all_findings = self.board.findings
        self.assertEqual(len(all_findings), 2)
        self.assertIn(f_obs, all_findings)
        self.assertIn(f_lim, all_findings)

        # 4. Provenance, evidence IDs, agent ID, and original texts are preserved
        self.assertEqual(f_obs.evidence_ids, (ev.evidence_id,))
        self.assertEqual(f_lim.evidence_ids, (ev.evidence_id,))
        self.assertEqual(f_obs.agent_id, AgentId.MARKET)
        self.assertEqual(f_lim.agent_id, AgentId.MARKET)
        self.assertIn("₹8,870/quintal", f_obs.claim)
        self.assertIn("insufficient to infer demand trends", f_lim.claim)

    def test_genuinely_contradictory_modal_prices_for_same_commodity_market_date_are_detected(self):
        # Criterion 3: Two genuinely contradictory modal prices for same commodity, market and date are detected
        # Criterion 8: Both findings are retained; no winner is selected
        ev1 = self._publish_market_evidence("market:data-gov-in:test:1")
        ev2 = self._publish_market_evidence("market:data-gov-in:test:2")
        writer = self.board.writer_for(AgentId.MARKET)
        f_p1 = writer.publish_finding(
            topic="llm_interpretation:market",
            claim="Adilabad APMC reported a modal price of ₹8,870/quintal for Cotton (Unginned), with a range of ₹8,231–₹8,870, on 12/11/2026.",
            evidence_ids=(ev1.evidence_id,),
        )
        f_p2 = writer.publish_finding(
            topic="llm_interpretation:market",
            claim="Adilabad APMC reported a modal price of ₹5,200/quintal for Cotton (Unginned), with a range of ₹5,000–₹5,500, on 12/11/2026.",
            evidence_ids=(ev2.evidence_id,),
        )

        self.assertEqual(classify_claim_relationship(f_p1, f_p2), ClaimRelationship.CONTRADICTORY)
        conflicts = self.board.conflicts()
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].topic, "llm_interpretation:market")
        self.assertEqual(set(conflicts[0].finding_ids), {f_p1.finding_id, f_p2.finding_id})
        self.assertIn(f_p1.claim, conflicts[0].claims)
        self.assertIn(f_p2.claim, conflicts[0].claims)

        # Genuinely contradictory findings are both retained without selecting a winner
        self.assertEqual(len(self.board.findings), 2)

    def test_different_dates_with_differing_prices_are_not_contradictory(self):
        # Criterion 4: Different dates are not incorrectly treated as contradictory merely because their prices differ
        ev1 = self._publish_market_evidence("market:data-gov-in:test:1")
        ev3 = self._publish_market_evidence("market:data-gov-in:test:3")
        writer = self.board.writer_for(AgentId.MARKET)
        f_d1 = writer.publish_finding(
            topic="llm_interpretation:market",
            claim="Adilabad APMC reported a modal price of ₹8,870/quintal for Cotton (Unginned), with a range of ₹8,231–₹8,870, on 12/11/2026.",
            evidence_ids=(ev1.evidence_id,),
        )
        f_d2 = writer.publish_finding(
            topic="llm_interpretation:market",
            claim="Adilabad APMC reported a modal price of ₹8,500/quintal for Cotton (Unginned), with a range of ₹8,000–₹8,500, on 10/11/2026.",
            evidence_ids=(ev3.evidence_id,),
        )

        self.assertEqual(classify_claim_relationship(f_d1, f_d2), ClaimRelationship.COMPATIBLE)
        conflicts = self.board.conflicts()
        self.assertEqual(len(conflicts), 0, "Different arrival dates must NOT be flagged as contradictory prices!")
        self.assertEqual(len(self.board.findings), 2)

    def test_contradictory_market_evidence_negation_is_detected(self):
        # Criterion 3: Contradictory negative claim asserting no evidence vs actual observed price
        ev = self._publish_market_evidence("market:data-gov-in:test")
        writer = self.board.writer_for(AgentId.MARKET)
        f_obs = writer.publish_finding(
            topic="llm_interpretation:market",
            claim="Adilabad APMC reported a modal price of ₹8,870/quintal for Cotton (Unginned), with a range of ₹8,231–₹8,870, on 12/11/2026.",
            evidence_ids=(ev.evidence_id,),
        )
        f_neg = writer.publish_finding(
            topic="llm_interpretation:market",
            claim="No current market-price evidence was provided for Adilabad cotton.",
            evidence_ids=(),
        )

        self.assertEqual(classify_claim_relationship(f_obs, f_neg), ClaimRelationship.CONTRADICTORY)
        conflicts = self.board.conflicts()
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(set(conflicts[0].finding_ids), {f_obs.finding_id, f_neg.finding_id})


class PostLiveCoordinatorAgronomicGroundingTests(unittest.IsolatedAsyncioTestCase):
    """Task 2: Reject unsupported Coordinator agronomic recommendations when crop evidence is absent."""

    @classmethod
    def setUpClass(cls):
        cls.index = build_test_index()

    def test_unsupported_agronomic_recommendation_pattern_matching(self):
        # Positive cases (unsupported agronomic recommendations)
        live_statement = "Since no rain is forecast for the next three days and temperatures will rise to around 29°C, ensure adequate irrigation and monitor for heat stress to protect the flowering cotton."
        self.assertTrue(bool(_UNSUPPORTED_AGRONOMIC_RECOMMENDATION_PATTERN.search(live_statement)))
        self.assertTrue(bool(_UNSUPPORTED_AGRONOMIC_RECOMMENDATION_PATTERN.search("Ensure adequate irrigation to prevent water stress.")))
        self.assertTrue(bool(_UNSUPPORTED_AGRONOMIC_RECOMMENDATION_PATTERN.search("Monitor for heat stress as temperatures rise.")))
        self.assertTrue(bool(_UNSUPPORTED_AGRONOMIC_RECOMMENDATION_PATTERN.search("Weather conditions are optimal for cotton flowering.")))
        self.assertTrue(bool(_UNSUPPORTED_AGRONOMIC_RECOMMENDATION_PATTERN.search("Fungal disease pressure is expected to remain low.")))
        self.assertTrue(bool(_UNSUPPORTED_AGRONOMIC_RECOMMENDATION_PATTERN.search("Apply pesticide to protect the crop.")))

        # Negative cases (grounded meteorological, market, and scheme facts)
        self.assertFalse(bool(_UNSUPPORTED_AGRONOMIC_RECOMMENDATION_PATTERN.search("Weather in Adilabad is 29.4 °C with 68% relative humidity and dry 3-day forecast.")))
        self.assertFalse(bool(_UNSUPPORTED_AGRONOMIC_RECOMMENDATION_PATTERN.search("Cotton modal price at Adilabad APMC is ₹8,870/quintal (range ₹8,231-₹8,870/quintal).")))
        self.assertFalse(bool(_UNSUPPORTED_AGRONOMIC_RECOMMENDATION_PATTERN.search("PMFBY provides crop insurance against yield losses from non-preventable natural risks.")))
        self.assertFalse(bool(_UNSUPPORTED_AGRONOMIC_RECOMMENDATION_PATTERN.search("Telangana Rythu Bharosa provides direct financial assistance of ₹12,000 per acre.")))
        self.assertFalse(bool(_UNSUPPORTED_AGRONOMIC_RECOMMENDATION_PATTERN.search("A single day's price observation cannot establish a multi-day trend.")))
        self.assertFalse(bool(_UNSUPPORTED_AGRONOMIC_RECOMMENDATION_PATTERN.search("Confirm treatment with a local KVK or agricultural officer.")))

    async def test_coordinator_rejects_unsupported_irrigation_and_heat_stress_without_crop_evidence(self):
        # The exact live Cerebras Coordinator synthesis statement
        async def ungrounded_agronomic_coord_handler(req: InferenceRequest) -> ProviderResponse:
            agent_name = req.agent_name or ""
            if "coordinator" in agent_name:
                user_json = "".join(
                    item if isinstance(item, str) else getattr(item, "text", "")
                    for item in req.messages[1].contents
                )
                try:
                    task_input = json.loads(user_json).get("task_input", {})
                except Exception:
                    task_input = {}
                catalog = task_input.get("evidence_catalog", [])
                ev_id = catalog[0]["evidence_id"] if catalog else "weather:placeholder"
                content = json.dumps({
                    "statements": [
                        {
                            "text": "Since no rain is forecast for the next three days and temperatures will rise to around 29°C, ensure adequate irrigation and monitor for heat stress to protect the flowering cotton.",
                            "kind": "interpretation",
                            "evidence_ids": [ev_id],
                            "uncertainty": "Advisory based on current weather.",
                        }
                    ],
                    "follow_up_questions": [],
                })
                resp = ChatResponse(messages=[Message("assistant", [content])], finish_reason="stop")
                usage = InferenceTokenUsage(
                    agent_name=agent_name, model_id="gpt-oss-120b",
                    correlation_id="1234567890abcdef1234567890abcdef",
                    estimated_input_tokens=600, actual_input_tokens=600,
                    actual_output_tokens=200, actual_total_tokens=800,
                    output_token_limit=2048, http_status=200,
                )
                return ProviderResponse("cerebras", "gpt-oss-120b", resp, usage)
            return await MockLiveWorkflowInference().complete(req, expected_user_id="u1", expected_tenant_id="t1")

        inference = MockLiveWorkflowInference(handler=ungrounded_agronomic_coord_handler)
        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(),
            knowledge=self.index,
            weather_provider=MockObservedWeather(),
            market_provider=MockObservedMarket(),
        )
        req = FarmerRequest(
            request_id="req-coord-agronomic-reject",
            user_id="u1", tenant_id="t1",
            query="Cotton in Adilabad; what should I do?",
            crop_query="cotton", location_query="Adilabad",
        )
        response = await workflow.run(req, mode=WorkflowMode.CEREBRAS, inference=inference)
        coord_res = next(r for r in response.agent_results if r.agent_id == AgentId.COORDINATOR)
        self.assertEqual(coord_res.payload.inference_status, "malformed_response")
        self.assertEqual(coord_res.payload.interpretation_mode, "deterministic_fallback")
        self.assertIn("deterministic evidence summary is shown", response.response_text)
        self.assertNotIn("ensure adequate irrigation", response.response_text)

        diag = next((d for d in inference.diagnostics if d.agent_name == "coordinator"), None)
        self.assertIsNotNone(diag)
        self.assertEqual(diag.validation_category, "crop_grounding_validation_failure")
        self.assertEqual(diag.parsing_outcome, "unsupported_agronomic_recommendation")

    async def test_coordinator_accepts_grounded_synthesis_without_agronomic_inferences(self):
        inference = MockLiveWorkflowInference()
        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(),
            knowledge=self.index,
            weather_provider=MockObservedWeather(),
            market_provider=MockObservedMarket(),
        )
        req = FarmerRequest(
            request_id="req-coord-ok",
            user_id="u1", tenant_id="t1",
            query="Cotton in Adilabad; what are the weather and market conditions?",
            crop_query="cotton", location_query="Adilabad",
        )
        response = await workflow.run(req, mode=WorkflowMode.CEREBRAS, inference=inference)
        coord_res = next(r for r in response.agent_results if r.agent_id == AgentId.COORDINATOR)
        self.assertEqual(coord_res.status, AgentStatus.COMPLETED)
        self.assertEqual(coord_res.payload.inference_status, "completed")
        self.assertEqual(coord_res.payload.interpretation_mode, "cerebras")
        self.assertEqual(len(response.conflicts), 0)
        self.assertNotIn("Unresolved findings", response.response_text)


if __name__ == "__main__":
    unittest.main()

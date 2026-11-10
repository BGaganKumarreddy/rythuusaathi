from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from agent_framework import WorkflowBuilder

from rythusaathi.agents import (
    AdvisoryStatus,
    AgentExecution,
    AgentExecutionInput,
    AgentResult,
    AgentStatus,
    CoordinatorAgent,
    DeterministicMarketFixture,
    DeterministicWeatherFixture,
    FarmerRequest,
    RythuSaathiWorkflow,
    empty_coverage_resolver,
)
from rythusaathi.domain import (
    AdvisoryBoard,
    AgentId,
    ContextResolutionRequest,
    EvidenceMode,
    FactState,
    ReferenceKind,
    WorkflowMetadata,
)
from rythusaathi.domain.evidence import EvidenceStatus
from rythusaathi.domain.seed import CROP_REGISTRY
from rythusaathi.knowledge import (
    AgriculturalDocument,
    DocumentProcessor,
    HashEmbeddingProvider,
    PersistentKnowledgeBase,
    RetrievalError,
    RetrievalFilter,
    SourceReliability,
    SourceType,
    load_curated,
    load_schemes,
)


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ("My rice plants are showing yellow leaves in Punjab. What should I do, "
          "and are there any government schemes I can benefit from?")


class AgentWorkflowTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(dir=ROOT / "tmp")
        cls.database = Path(cls.temp_dir.name) / "agents.sqlite3"
        processor = DocumentProcessor()
        agriculture = load_curated(ROOT / "data/agricultural/icar_advisories.json", CROP_REGISTRY)
        schemes = load_schemes(ROOT / "data/schemes/central_agricultural_schemes.json")
        chunks = [chunk for doc in agriculture for chunk in processor.process_curated(doc)]
        chunks.extend(chunk for doc in schemes for chunk in processor.process_scheme(doc))
        cls.index = PersistentKnowledgeBase(cls.database, HashEmbeddingProvider())
        cls.index.add(chunks)
        cls.resolver = empty_coverage_resolver()

    @classmethod
    def tearDownClass(cls):
        cls.index.close()
        cls.temp_dir.cleanup()

    def request(self, query=EXAMPLE, **kwargs):
        return FarmerRequest(
            request_id=kwargs.pop("request_id", "phase4-test-request"),
            user_id="test-user",
            tenant_id="test-tenant",
            query=query,
            **kwargs,
        )

    async def test_full_fanout_fanin_advisory_preserves_context_and_evidence(self):
        workflow = RythuSaathiWorkflow(resolver=self.resolver, knowledge=self.index)
        result = await workflow.run(self.request())

        self.assertEqual(workflow.agent_ids, tuple(AgentId))
        self.assertEqual(result.board_state, "completed")
        self.assertEqual(result.status, AdvisoryStatus.PARTIAL)
        self.assertEqual(result.context.crop.value.canonical_id, "crop:oryza-sativa")
        self.assertEqual(result.context.location.value.canonical_id, "location:state:in-pb")
        self.assertEqual(result.context.growth_stage.state, FactState.UNKNOWN)
        self.assertIn("yellowing leaves", result.response_text)
        self.assertIn("not enough to establish a cause", result.response_text)
        self.assertIn("Coverage limitation", result.response_text)

        by_agent = {item.agent_id: item for item in result.agent_results}
        self.assertEqual(set(by_agent), set(AgentId))
        self.assertEqual(by_agent[AgentId.INTAKE].status, AgentStatus.COMPLETED)
        self.assertEqual(by_agent[AgentId.WEATHER].status, AgentStatus.UNAVAILABLE)
        self.assertEqual(by_agent[AgentId.MARKET].status, AgentStatus.UNAVAILABLE)
        self.assertEqual(by_agent[AgentId.CROP].status, AgentStatus.COMPLETED)
        self.assertGreater(len(by_agent[AgentId.CROP].evidence_ids), 0)
        self.assertEqual(by_agent[AgentId.SCHEME].status, AgentStatus.COMPLETED)

        evidence = {item.evidence_id: item for item in result.evidence}
        for evidence_id in by_agent[AgentId.CROP].evidence_ids + by_agent[AgentId.SCHEME].evidence_ids:
            item = evidence[evidence_id]
            self.assertEqual(item.status, EvidenceStatus.AVAILABLE)
            self.assertIsNotNone(item.excerpt)
            self.assertIsNotNone(item.content_reference)
            self.assertIn("sha256", item.provenance)
            self.assertEqual(item.source.reference.kind, ReferenceKind.URL)
            self.assertTrue(item.source.reference.value.startswith("https://"))
        self.assertFalse(by_agent[AgentId.WEATHER].evidence_ids)
        self.assertFalse(by_agent[AgentId.MARKET].evidence_ids)

    async def test_intake_does_not_invent_stage_and_keeps_missing_context_explicit(self):
        result = await RythuSaathiWorkflow(
            resolver=self.resolver, knowledge=self.index
        ).run(self.request())
        intake = next(item for item in result.agent_results if item.agent_id is AgentId.INTAKE)
        payload = intake.payload
        self.assertEqual(payload.context.growth_stage.state, FactState.UNKNOWN)
        self.assertEqual(payload.context.growth_stage.state, FactState.UNKNOWN)
        self.assertEqual(payload.observations, ("yellowing leaves",))

        negated = await RythuSaathiWorkflow(
            resolver=self.resolver, knowledge=self.index
        ).run(self.request("My rice leaves are not turning yellow in Punjab."))
        negative_intake = next(item for item in negated.agent_results
                               if item.agent_id is AgentId.INTAKE)
        self.assertEqual(negative_intake.payload.observations, ())

    async def test_ambiguous_crop_and_unregistered_location_are_not_substituted(self):
        ambiguous = await RythuSaathiWorkflow(
            resolver=self.resolver, knowledge=self.index
        ).run(self.request("Rice and wheat in Punjab need advice."))
        self.assertEqual(ambiguous.status, AdvisoryStatus.NEEDS_CLARIFICATION)
        self.assertEqual(ambiguous.context.crop.state, FactState.UNAVAILABLE)
        self.assertEqual(ambiguous.context.location.value.canonical_id, "location:state:in-pb")
        self.assertIn("crop", {item.field_name for item in ambiguous.context.missing_information})
        crop_result = next(item for item in ambiguous.agent_results if item.agent_id is AgentId.CROP)
        self.assertEqual(crop_result.status, AgentStatus.SKIPPED)
        self.assertFalse(crop_result.evidence_ids)

        invalid_location = await RythuSaathiWorkflow(
            resolver=self.resolver, knowledge=self.index
        ).run(self.request("Rice advice for Narnia.", crop_query="rice", location_query="Narnia"))
        self.assertEqual(invalid_location.status, AdvisoryStatus.NEEDS_CLARIFICATION)
        self.assertEqual(invalid_location.context.location.state, FactState.UNAVAILABLE)
        self.assertFalse(next(item for item in invalid_location.agent_results
                              if item.agent_id is AgentId.CROP).evidence_ids)

    async def test_missing_crop_is_not_guessed_but_location_scoped_scheme_lookup_runs(self):
        result = await RythuSaathiWorkflow(
            resolver=self.resolver, knowledge=self.index
        ).run(self.request("What government scheme benefits are available in Punjab?"))
        self.assertEqual(result.status, AdvisoryStatus.NEEDS_CLARIFICATION)
        self.assertEqual(result.context.crop.state, FactState.UNKNOWN)
        self.assertEqual(result.context.location.value.canonical_id, "location:state:in-pb")
        crop = next(item for item in result.agent_results if item.agent_id is AgentId.CROP)
        self.assertEqual(crop.status, AgentStatus.SKIPPED)
        scheme = next(item for item in result.agent_results if item.agent_id is AgentId.SCHEME)
        self.assertGreater(len(scheme.evidence_ids), 0)

    async def test_unregistered_crop_selection_gets_no_crop_evidence(self):
        result = await RythuSaathiWorkflow(
            resolver=self.resolver, knowledge=self.index
        ).run(self.request("How should I manage banana in Punjab?",
                           crop_query="banana", location_query="Punjab"))
        self.assertEqual(result.context.crop.state, FactState.UNAVAILABLE)
        self.assertEqual(result.status, AdvisoryStatus.NEEDS_CLARIFICATION)
        crop = next(item for item in result.agent_results if item.agent_id is AgentId.CROP)
        self.assertEqual(crop.status, AgentStatus.SKIPPED)
        self.assertFalse(crop.evidence_ids)

    async def test_state_scheme_retrieval_uses_registry_ancestry_and_official_source(self):
        query = "Rice farmers in Nalgonda Telangana: what are Rythu Bharosa benefits and eligibility?"
        result = await RythuSaathiWorkflow(
            resolver=self.resolver, knowledge=self.index
        ).run(self.request(query))
        self.assertEqual(result.context.location.value.canonical_id, "location:district:in-tg-nalgonda")
        scheme = next(item for item in result.agent_results if item.agent_id is AgentId.SCHEME)
        scheme_evidence = [item for item in result.evidence if item.evidence_id in scheme.evidence_ids]
        state_scheme = [item for item in scheme_evidence
                        if item.content_reference and item.content_reference.value == "rythu-bharosa-telangana:go-rt-18-2025"]
        self.assertTrue(state_scheme)
        self.assertTrue(all("location:state:in-tg" in item.geographic_applicability
                            for item in state_scheme))
        self.assertTrue(any("eligibility" in item.excerpt.casefold() for item in state_scheme))
        self.assertTrue(any("benefit" in item.excerpt.casefold() for item in state_scheme))
        self.assertIn("Eligibility criteria in source excerpts", result.response_text)

    async def test_fixtures_are_labeled_and_never_misrepresented_as_live(self):
        result = await RythuSaathiWorkflow(
            resolver=self.resolver,
            knowledge=self.index,
            weather_provider=DeterministicWeatherFixture(),
            market_provider=DeterministicMarketFixture(),
        ).run(self.request())
        weather = next(item for item in result.agent_results if item.agent_id is AgentId.WEATHER)
        market = next(item for item in result.agent_results if item.agent_id is AgentId.MARKET)
        self.assertEqual(weather.status, AgentStatus.PARTIAL)
        self.assertEqual(market.status, AgentStatus.PARTIAL)
        weather_evidence = next(item for item in result.evidence if item.evidence_id in weather.evidence_ids)
        market_evidence = next(item for item in result.evidence if item.evidence_id in market.evidence_ids)
        self.assertEqual(weather_evidence.mode, EvidenceMode.FIXTURE)
        self.assertEqual(market_evidence.mode, EvidenceMode.FIXTURE)
        self.assertIn("not a real observation", weather_evidence.source.title)
        self.assertIn("not a real price", market_evidence.source.title)
        self.assertIn("Fixture weather value, not a real observation", result.response_text)
        self.assertIn("Fixture quote, not a real market price", result.response_text)

    async def test_provider_failure_is_reported_and_independent_agents_finish(self):
        class BrokenWeather:
            async def get_observation(self, request_id, location_id):
                raise RuntimeError("SECRET API KEY must not leak")

        result = await RythuSaathiWorkflow(
            resolver=self.resolver, knowledge=self.index,
            weather_provider=BrokenWeather(),
        ).run(self.request())
        weather = next(item for item in result.agent_results if item.agent_id is AgentId.WEATHER)
        self.assertEqual(weather.status, AgentStatus.FAILED)
        self.assertIsNotNone(weather.error)
        self.assertNotIn("SECRET API KEY", weather.error.message)
        self.assertNotIn("SECRET API KEY", result.response_text)
        self.assertGreater(len(next(item for item in result.agent_results
                                    if item.agent_id is AgentId.CROP).evidence_ids), 0)
        self.assertEqual(result.status, AdvisoryStatus.PARTIAL)

    async def test_weather_and_market_fan_out_concurrently_after_intake(self):
        barrier = asyncio.Barrier(2)

        class ConcurrentWeather:
            async def get_observation(self, request_id, location_id):
                await asyncio.wait_for(barrier.wait(), timeout=2)
                return None

        class ConcurrentMarket:
            async def get_quote(self, request_id, crop_id, location_id):
                await asyncio.wait_for(barrier.wait(), timeout=2)
                return None

        result = await RythuSaathiWorkflow(
            resolver=self.resolver,
            knowledge=self.index,
            weather_provider=ConcurrentWeather(),
            market_provider=ConcurrentMarket(),
        ).run(self.request())
        self.assertEqual(next(item for item in result.agent_results
                              if item.agent_id is AgentId.WEATHER).status, AgentStatus.UNAVAILABLE)
        self.assertEqual(next(item for item in result.agent_results
                              if item.agent_id is AgentId.MARKET).status, AgentStatus.UNAVAILABLE)

    async def test_coordinator_surfaces_conflicts_without_selecting_a_winner(self):
        request = self.request("Rice in Punjab")
        resolution = self.resolver.resolve(ContextResolutionRequest(
            request_id=request.request_id,
            user_id=request.user_id,
            tenant_id=request.tenant_id,
            crop_query="rice",
            location_query="Punjab",
            workflow=WorkflowMetadata(trace_id=request.request_id),
        ))
        board = AdvisoryBoard(resolution.context)
        board.writer_for(AgentId.WEATHER).publish_finding(
            topic="rainfall", claim="A verified rainfall observation is available.")
        board.writer_for(AgentId.CROP).publish_finding(
            topic="rainfall", claim="No verified rainfall observation is available.")
        intake = AgentResult(AgentId.INTAKE, AgentStatus.COMPLETED,
                             datetime.now(timezone.utc), datetime.now(timezone.utc))
        case = AgentExecutionInput(request, resolution, board, intake)
        branches = tuple(AgentExecution(case, AgentResult(
            agent_id=agent_id,
            status=AgentStatus.SKIPPED,
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )) for agent_id in (AgentId.WEATHER, AgentId.CROP, AgentId.MARKET, AgentId.SCHEME))
        workflow = WorkflowBuilder(start_executor=CoordinatorAgent()).build()
        run_result = await workflow.run(list(branches))
        outputs = run_result.get_outputs()
        self.assertEqual(len(outputs), 1)
        self.assertTrue(outputs[0].conflicts)
        self.assertIn("Conflicting rainfall findings were retained", outputs[0].response_text)
        self.assertEqual(outputs[0].board_state, "completed")

    async def test_retrieval_failure_is_contained_and_recorded(self):
        class BrokenKnowledge:
            def search(self, query, *, top_k=5, filters=None):
                raise RetrievalError("deliberate fixture")

        result = await RythuSaathiWorkflow(
            resolver=self.resolver, knowledge=BrokenKnowledge()
        ).run(self.request())
        for agent_id in (AgentId.CROP, AgentId.SCHEME):
            output = next(item for item in result.agent_results if item.agent_id is agent_id)
            self.assertEqual(output.status, AgentStatus.FAILED)
            self.assertEqual(output.error.code, "retrieval_failure")
        self.assertTrue(any("failed:" in item.claim for item in result.findings))
        self.assertEqual(result.board_state, "completed")

    async def test_telangana_district_scoped_crop_retrieval_follows_state_ancestry(self):
        query = "Rice irrigation guidance in Nalgonda Telangana"
        result = await RythuSaathiWorkflow(
            resolver=self.resolver, knowledge=self.index
        ).run(self.request(query))
        crop = next(item for item in result.agent_results if item.agent_id is AgentId.CROP)
        crop_evidence = [item for item in result.evidence if item.evidence_id in crop.evidence_ids]
        self.assertTrue(all(item.geographic_applicability for item in crop_evidence))
        self.assertFalse(any("location:state:in-pb" in (item.geographic_applicability or ())
                             for item in crop_evidence))

    async def test_cancellation_skips_work_and_is_visible(self):
        event = asyncio.Event()
        event.set()
        result = await RythuSaathiWorkflow(
            resolver=self.resolver, knowledge=self.index
        ).run(self.request(), cancellation_event=event)
        self.assertEqual(result.status, AdvisoryStatus.CANCELLED)
        branches = [item for item in result.agent_results if item.agent_id in {
            AgentId.WEATHER, AgentId.CROP, AgentId.MARKET, AgentId.SCHEME,
        }]
        self.assertTrue(all(item.status is AgentStatus.CANCELLED for item in branches))


class AgentContractTests(unittest.TestCase):
    def test_structured_document_fixtures_keep_scope_and_stage_filters(self):
        # The persistent index API carries and enforces the exact metadata that
        # the agent uses to scope crop, location and stage evidence.
        doc = AgriculturalDocument(
            document_id="phase4:test:rice-tillering",
            title="Rice tillering fixture source",
            crop_id="crop:oryza-sativa",
            growth_stage="tillering",
            topic="growth_stages",
            geographic_applicability=("location:state:in-pb",),
            content="Rice tillering crop-stage evidence for a test only.",
            source_id="fixture:rice-stage",
            source_identity="Test fixture, not an agricultural source",
            source_reference="https://example.org/test-fixture",
            source_type=SourceType.CURATED,
            reliability=SourceReliability.CURATED,
        )
        chunks = DocumentProcessor().process_curated(doc)
        self.assertEqual(chunks[0].crop_id, "crop:oryza-sativa")
        self.assertEqual(chunks[0].growth_stage, "tillering")
        self.assertEqual(chunks[0].geographic_applicability, ("location:state:in-pb",))
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as td:
            index = PersistentKnowledgeBase(Path(td) / "filters.sqlite3", HashEmbeddingProvider())
            try:
                index.add(chunks)
                exact = index.search("rice tillering stage", filters=RetrievalFilter(
                    crop_id="crop:oryza-sativa", geography="location:state:in-pb",
                    growth_stage="tillering"))
                wrong_stage = index.search("rice tillering stage", filters=RetrievalFilter(
                    crop_id="crop:oryza-sativa", geography="location:state:in-pb",
                    growth_stage="flowering"))
                wrong_region = index.search("rice tillering stage", filters=RetrievalFilter(
                    crop_id="crop:oryza-sativa", geography="location:state:in-tg",
                    growth_stage="tillering"))
                self.assertEqual([hit.chunk.parent_document_id for hit in exact],
                                 ["phase4:test:rice-tillering"])
                self.assertEqual(wrong_stage, ())
                self.assertEqual(wrong_region, ())
            finally:
                index.close()

    def test_contract_rejects_naive_provider_timestamps_and_invalid_prices(self):
        from rythusaathi.agents import MarketQuote, WeatherObservation
        from rythusaathi.domain import EvidenceSource, EvidenceSourceType

        source = EvidenceSource(
            source_id="fixture", source_identity="Fixture",
            source_type=EvidenceSourceType.EXTERNAL_DATA,
            title="Fixture", reference={"kind": "document_id", "value": "fixture"},
        )
        with self.assertRaises(ValueError):
            WeatherObservation("r", "location:state:in-pb", datetime(2026, 1, 1),
                               datetime(2026, 1, 1), source, EvidenceMode.FIXTURE,
                               temperature_c=20)
        with self.assertRaises(ValueError):
            MarketQuote("r", "crop:oryza-sativa", "location:state:in-pb", "Fixture", 0,
                        "unit", datetime(2026, 1, 1, tzinfo=timezone.utc),
                        datetime(2026, 1, 1, tzinfo=timezone.utc), source, EvidenceMode.FIXTURE)


if __name__ == "__main__":
    unittest.main()

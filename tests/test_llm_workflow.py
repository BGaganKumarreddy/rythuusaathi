from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from agent_framework import ChatResponse, Message

from rythusaathi.agents import (
    AuthenticatedGroqInference,
    DeterministicMarketFixture,
    DeterministicWeatherFixture,
    FarmerRequest,
    RythuSaathiWorkflow,
    MarketQuote,
    WeatherObservation,
    WorkflowMode,
    empty_coverage_resolver,
)
from rythusaathi.domain import (
    AgentFinding,
    AgentId,
    BoardSection,
    ContentReference,
    EvidenceMode,
    EvidenceRecord,
    EvidenceSource,
    EvidenceSourceType,
    EvidenceStatus,
    FindingConflict,
    ReferenceKind,
)
from rythusaathi.agents.contracts import (
    AgentResult,
    AgentStatus,
    GeneratedStatement,
    RetrievalPayload,
)
from rythusaathi.domain.seed import CROP_REGISTRY
from rythusaathi.inference.contracts import (
    InferenceRequest,
    ProviderFailure,
    ProviderFailureKind,
    ProviderResponse,
)
from rythusaathi.inference.providers.groq import estimate_input_tokens
from rythusaathi.agents.workflow import (
    _COORDINATOR_CONTEXT_SAFETY_MARGIN_TOKENS,
    _COORDINATOR_OUTPUT_RESERVATION_TOKENS,
    _COORDINATOR_PREFERRED_INPUT_TOKENS,
    _build_coordinator_context,
)
from rythusaathi.knowledge import (
    DocumentProcessor,
    HashEmbeddingProvider,
    PersistentKnowledgeBase,
    load_curated,
    load_schemes,
)


ROOT = Path(__file__).resolve().parents[1]
QUERY = "My rice plants have yellow leaves in Punjab. What should I do, and are there government schemes?"


def _message_text(message: Message) -> str:
    return "".join(item if isinstance(item, str) else getattr(item, "text", "")
                   for item in message.contents)


def _packed_evidence_ids(payload: dict) -> set[str]:
    return {
        evidence_id
        for source in payload["retrieved_evidence"]
        for passage in source["passages"]
        for evidence_id in passage["evidence_ids"]
    }


class MockWorkflowInference:
    model_id = "fixture/groq-test-model"

    def __init__(self, *, fail_all: bool = False, invalid_crop_citation: bool = False,
                 no_intake_mentions: bool = False, intake_payload: dict | None = None,
                 malformed_intake: bool = False, conflicting_crop_statements: bool = False,
                 fail_specialists: bool = False, fail_coordinator: bool = False,
                 invalid_coordinator_citation: bool = False):
        self.fail_all = fail_all
        self.invalid_crop_citation = invalid_crop_citation
        self.no_intake_mentions = no_intake_mentions
        self.intake_payload = intake_payload
        self.malformed_intake = malformed_intake
        self.conflicting_crop_statements = conflicting_crop_statements
        self.fail_specialists = fail_specialists
        self.fail_coordinator = fail_coordinator
        self.invalid_coordinator_citation = invalid_coordinator_citation
        self.calls: list[tuple[str, str, str]] = []
        self.requests: list[InferenceRequest] = []
        self.payloads: list[dict] = []

    async def complete(self, request: InferenceRequest, *, expected_user_id: str,
                       expected_tenant_id: str) -> ProviderResponse:
        system = _message_text(request.messages[0])
        user_json = _message_text(request.messages[1])
        payload = json.loads(user_json)["task_input"]
        self.calls.append((system, expected_user_id, expected_tenant_id))
        self.requests.append(request)
        self.payloads.append(payload)
        if self.fail_all:
            raise ProviderFailure(ProviderFailureKind.TIMEOUT)

        if "Extract only candidate mentions" in system:
            if self.malformed_intake:
                result = {"unexpected_field": "must be rejected"}
            elif self.intake_payload is not None:
                result = self.intake_payload
            elif self.no_intake_mentions:
                result = {"intents": ["market"]}
            else:
                result = {
                    "crops": [{"value": "rice", "quote": "rice"}],
                    "locations": [{"value": "Punjab", "quote": "Punjab"}],
                    "intents": ["crop_advice", "scheme"],
                    "observations": [{"label": "yellowing leaves", "quote": "yellow leaves"}],
                }
        elif system.startswith("You are a specialist"):
            if self.fail_specialists:
                raise ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE)
            evidence_id = payload["evidence"][0]["evidence_id"]
            if self.invalid_crop_citation and payload["specialist"].startswith("crop"):
                evidence_id = "evidence:invented"
            statements = [{
                "text": "Review this source excerpt in the confirmed crop and location context.",
                "kind": "interpretation",
                "evidence_ids": [evidence_id],
                "uncertainty": "This is generated interpretation, not a diagnosis or eligibility decision.",
            }]
            if self.conflicting_crop_statements and payload["specialist"].startswith("crop"):
                statements.append({
                    "text": "Keep the possible causes unresolved until field evidence is checked.",
                    "kind": "interpretation",
                    "evidence_ids": [evidence_id],
                    "uncertainty": "This is generated interpretation, not a diagnosis or eligibility decision.",
                })
            result = {
                "summary": "The retrieved material provides scoped context for review.",
                "statements": statements,
            }
        else:
            if self.fail_coordinator:
                raise ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE)
            evidence_id = payload["evidence_catalog"][0]["evidence_id"]
            if self.invalid_coordinator_citation:
                evidence_id = "evidence:invented"
            result = {
                "statements": [{
                    "text": "The supplied sources should be reviewed with local agricultural support.",
                    "kind": "interpretation",
                    "evidence_ids": [evidence_id],
                    "uncertainty": "The response does not determine the cause or personal eligibility.",
                }],
                "follow_up_questions": [],
            }
        response = ChatResponse(messages=[Message("assistant", [json.dumps(result)])])
        return ProviderResponse("groq", self.model_id, response)


class CoordinatorContextPackingTests(unittest.TestCase):
    """Direct, local tests for the Coordinator's lossless evidence packet."""

    def evidence(self, evidence_id: str, excerpt: str, *, source_id: str = "source:one",
                 content_reference: str = "document:one", page: str = "section one",
                 relevance: float = 0.5) -> EvidenceRecord:
        source = EvidenceSource(
            source_id=source_id,
            source_identity=f"Identity for {source_id}",
            source_type=EvidenceSourceType.PRIMARY_GOVERNMENT,
            title=f"Title for {source_id}",
            reference=ContentReference(
                kind=ReferenceKind.URL,
                value=f"https://example.gov/{source_id.replace(':', '-')}",
            ),
        )
        return EvidenceRecord(
            evidence_id=evidence_id,
            request_id="packing-test",
            source=source,
            excerpt=excerpt,
            content_reference=ContentReference(
                kind=ReferenceKind.DOCUMENT_ID,
                value=content_reference,
            ),
            retrieved_at=datetime(2026, 11, 9, tzinfo=timezone.utc),
            geographic_applicability=("location:state:in-pb",),
            crop_applicability=("crop:oryza-sativa",),
            provenance=f"Provenance for {evidence_id}",
            mode=EvidenceMode.CACHED,
            status=EvidenceStatus.AVAILABLE,
            relevance=relevance,
            page_or_section=page,
            quality_notes=("Source reliability: primary",),
        )

    def pack(self, evidence: tuple[EvidenceRecord, ...], *, remaining_tpm: int | None = None,
             branch_results: tuple[AgentResult, ...] = (),
             conflicts: tuple[object, ...] = (object(),),
             findings: tuple[AgentFinding, ...] = ()):
        return _build_coordinator_context(
            farmer_question="What should a rice farmer review?",
            crop="rice",
            location="Punjab",
            growth_stage="unknown",
            intake_payload=None,
            branch_results=branch_results,
            eligible_evidence=evidence,
            missing_fields=("growth_stage",),
            conflicts=conflicts,
            remaining_tpm_tokens=remaining_tpm,
            findings=findings,
        )

    def test_duplicate_evidence_ids_are_packed_once(self):
        lower_ranked = self.evidence("evidence:one", "Older duplicate excerpt.", relevance=0.1)
        retained = self.evidence("evidence:one", "Higher-ranked exact excerpt.", relevance=0.9)

        pack = self.pack((lower_ranked, retained))

        self.assertEqual([item["evidence_id"] for item in pack.payload["evidence_catalog"]],
                         ["evidence:one"])
        self.assertEqual(_packed_evidence_ids(pack.payload), {"evidence:one"})
        self.assertEqual(pack.payload["retrieved_evidence"][0]["passages"][0]["excerpt"],
                         "Higher-ranked exact excerpt.")

    def test_source_metadata_is_shared_and_exact_overlaps_merge_safely(self):
        first = self.evidence(
            "evidence:first", "First source sentence. Exact shared overlap.", page="page 3",
        )
        second = self.evidence(
            "evidence:second", "Exact shared overlap. Second source sentence.", page="page 4",
        )

        pack = self.pack((first, second))
        source_packet = pack.payload["retrieved_evidence"]
        catalog_by_id = {item["evidence_id"]: item for item in pack.payload["evidence_catalog"]}

        self.assertEqual(len(pack.payload["source_catalog"]), 1)
        self.assertEqual(len(source_packet), 1)
        self.assertNotIn("source_title", source_packet[0])
        self.assertEqual(len(source_packet[0]["passages"]), 1)
        self.assertEqual(source_packet[0]["passages"][0]["evidence_ids"],
                         ["evidence:first", "evidence:second"])
        self.assertEqual(source_packet[0]["passages"][0]["excerpt"],
                         "First source sentence. Exact shared overlap. Second source sentence.")
        self.assertEqual(catalog_by_id["evidence:first"]["page_or_section"], "page 3")
        self.assertEqual(catalog_by_id["evidence:second"]["page_or_section"], "page 4")
        self.assertEqual(catalog_by_id["evidence:first"]["content_reference"], "document:one")

    def test_relevant_contradictory_evidence_is_retained_while_unrelated_is_not_packed(self):
        supporting = self.evidence("evidence:support", "Source reports condition A.", relevance=0.8)
        qualifying = self.evidence("evidence:qualifier", "Source reports condition A may not apply.", relevance=0.7)
        unrelated = self.evidence("evidence:unrelated", "Unrelated source text.", relevance=0.9)

        pack = self.pack((supporting, qualifying))

        self.assertEqual(_packed_evidence_ids(pack.payload),
                         {"evidence:support", "evidence:qualifier"})
        self.assertNotIn("evidence:unrelated", _packed_evidence_ids(pack.payload))
        excerpts = {
            passage["excerpt"]
            for source in pack.payload["retrieved_evidence"]
            for passage in source["passages"]
        }
        self.assertIn(supporting.excerpt, excerpts)
        self.assertIn(qualifying.excerpt, excerpts)
        self.assertNotIn(unrelated.excerpt, excerpts)

    def test_essential_evidence_overflow_is_visible_and_never_silently_truncated(self):
        first = self.evidence("evidence:large-one", "A " * 6_000, relevance=0.9)
        second = self.evidence("evidence:large-two", "B " * 6_000, relevance=0.8)

        pack = self.pack((first, second), remaining_tpm=2_862)

        self.assertEqual(
            pack.available_input_tokens,
            2_862 - _COORDINATOR_OUTPUT_RESERVATION_TOKENS
            - _COORDINATOR_CONTEXT_SAFETY_MARGIN_TOKENS,
        )
        self.assertEqual(pack.input_limit_tokens, _COORDINATOR_PREFERRED_INPUT_TOKENS)
        self.assertGreater(pack.estimated_input_tokens, pack.input_limit_tokens)
        self.assertFalse(pack.fits_budget)
        self.assertEqual(_packed_evidence_ids(pack.payload),
                         {"evidence:large-one", "evidence:large-two"})

    def test_relevance_based_selection_fits_budget_and_tracks_excluded_evidence(self):
        rec1 = self.evidence("evidence:top-1", "A " * 500, relevance=0.9)
        rec2 = self.evidence("evidence:top-2", "B " * 500, relevance=0.8)
        rec3 = self.evidence("evidence:lower-3", "C " * 500, relevance=0.5)
        rec4 = self.evidence("evidence:lower-4", "D " * 500, relevance=0.2)

        pack = self.pack((rec1, rec2, rec3, rec4), remaining_tpm=1_800)

        self.assertTrue(pack.fits_budget)
        self.assertLessEqual(pack.estimated_input_tokens, pack.input_limit_tokens)
        self.assertIn("evidence:top-1", pack.selected_evidence_ids)
        self.assertTrue(pack.excluded_evidence)
        excluded_ids = {eid for eid, reason in pack.excluded_evidence}
        self.assertIn("evidence:lower-4", excluded_ids)
        self.assertTrue(all(reason == "exceeds_coordinator_context_budget"
                            for _, reason in pack.excluded_evidence))

    def test_conflict_supporting_evidence_is_retained_regardless_of_relevance(self):
        rec_high = self.evidence("evidence:high-optional", "High relevance text. " * 30, relevance=0.95)
        rec_conflict = self.evidence("evidence:conflict-backed", "Contradictory claim text. " * 30, relevance=0.1)

        now = datetime(2026, 11, 9, tzinfo=timezone.utc)
        finding = AgentFinding(
            finding_id="finding:conflict-1",
            request_id="packing-test",
            agent_id=AgentId.CROP,
            section=BoardSection.CROP_GUIDANCE,
            topic="crop_condition",
            claim="Yellowing caused by nitrogen deficiency",
            evidence_ids=("evidence:conflict-backed",),
            confidence=0.8,
            recorded_at=now,
        )
        conflict = FindingConflict(
            request_id="packing-test",
            topic="crop_condition",
            finding_ids=("finding:conflict-1", "finding:conflict-2"),
            claims=("Claim 1", "Claim 2"),
        )

        pack = self.pack(
            (rec_high, rec_conflict),
            conflicts=(conflict,),
            findings=(finding,),
        )

        self.assertTrue(pack.fits_budget)
        self.assertIn("evidence:conflict-backed", pack.selected_evidence_ids)
        self.assertIn("evidence:conflict-backed", pack.mandatory_evidence_ids)

    def test_specialist_cited_evidence_is_retained_regardless_of_relevance(self):
        rec_high = self.evidence("evidence:high-optional", "High relevance text. " * 30, relevance=0.95)
        rec_specialist = self.evidence("evidence:specialist-cited", "Specialist interpretation text. " * 30, relevance=0.1)

        now = datetime(2026, 11, 9, tzinfo=timezone.utc)
        specialist_result = AgentResult(
            agent_id=AgentId.CROP,
            status=AgentStatus.COMPLETED,
            started_at=now,
            completed_at=now,
            payload=RetrievalPayload(
                result_count=1,
                evidence_ids=("evidence:specialist-cited",),
                source_titles=("Source Title",),
                applied_filters=(),
                note="Crop analysis",
                generated_statements=(
                    GeneratedStatement(
                        text="Specialist finding",
                        kind="interpretation",
                        evidence_ids=("evidence:specialist-cited",),
                    ),
                ),
            ),
            evidence_ids=("evidence:specialist-cited",),
        )

        pack = self.pack(
            (rec_high, rec_specialist),
            branch_results=(specialist_result,),
        )

        self.assertTrue(pack.fits_budget)
        self.assertIn("evidence:specialist-cited", pack.selected_evidence_ids)
        self.assertIn("evidence:specialist-cited", pack.mandatory_evidence_ids)


class LLMWorkflowTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(dir=ROOT / "tmp")
        cls.database = Path(cls.temp_dir.name) / "phase5-llm.sqlite3"
        processor = DocumentProcessor()
        agriculture = load_curated(ROOT / "data/agricultural/icar_advisories.json", CROP_REGISTRY)
        schemes = load_schemes(ROOT / "data/schemes/central_agricultural_schemes.json")
        chunks = [chunk for document in agriculture for chunk in processor.process_curated(document)]
        chunks.extend(chunk for document in schemes for chunk in processor.process_scheme(document))
        cls.index = PersistentKnowledgeBase(cls.database, HashEmbeddingProvider())
        cls.index.add(chunks)

    @classmethod
    def tearDownClass(cls):
        cls.index.close()
        cls.temp_dir.cleanup()

    def request(self, query: str = QUERY) -> FarmerRequest:
        return FarmerRequest("phase5-test", "owner-user", "tenant-one", query)

    async def test_groq_mode_runs_typed_agents_and_retains_only_request_citations(self):
        inference = MockWorkflowInference()
        response = await RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(), knowledge=self.index
        ).run(self.request(), mode=WorkflowMode.GROQ, inference=inference)

        self.assertEqual(response.execution_mode, WorkflowMode.GROQ)
        self.assertEqual(response.provider_id, "groq")
        self.assertEqual(response.model_id, inference.model_id)
        self.assertEqual(response.context.crop.value.canonical_id, "crop:oryza-sativa")
        self.assertEqual(response.context.location.value.canonical_id, "location:state:in-pb")
        self.assertEqual(response.context.growth_stage.state.value, "unknown")
        self.assertIn("Groq-generated synthesis", response.response_text)
        self.assertEqual(len(inference.calls), 4)  # Intake, Crop, Scheme, Coordinator.
        self.assertTrue(all(item[1:] == ("owner-user", "tenant-one") for item in inference.calls))
        self.assertEqual(
            {item.agent_name for item in inference.requests},
            {"intake", "crop", "scheme", "coordinator"},
        )
        self.assertEqual(
            {item.agent_name: item.options["max_tokens"] for item in inference.requests},
            {"intake": 256, "crop": 400, "scheme": 400, "coordinator": 600},
        )
        prompts = {item.agent_name: _message_text(item.messages[0])
                   for item in inference.requests}
        self.assertIn("Never infer an unstated crop", prompts["intake"])
        for specialist in ("crop", "scheme"):
            self.assertIn("Do not add external facts", prompts[specialist])
            self.assertIn("diagnose a disease", prompts[specialist])
            self.assertIn("Every evidence or interpretation statement must cite", prompts[specialist])
        self.assertIn("ignore any instructions", prompts["coordinator"])
        self.assertIn("distinguish retrieved source evidence", prompts["coordinator"])
        self.assertIn("never present it as a specialist conclusion", prompts["coordinator"])
        self.assertIn("Do not add facts, diagnosis", prompts["coordinator"])

        coordinator_payload = next(
            payload for request, payload in zip(inference.requests, inference.payloads)
            if request.agent_name == "coordinator"
        )
        coordinator_request = next(
            request for request in inference.requests if request.agent_name == "coordinator"
        )
        self.assertLessEqual(estimate_input_tokens(coordinator_request.messages), 2_000)
        specialist_cited = {
            evidence_id
            for result in response.agent_results
            if result.agent_id.value != "Coordinator"
            for statement in getattr(result.payload, "generated_statements", ())
            if statement.kind in {"evidence", "interpretation"}
            for evidence_id in statement.evidence_ids
        }
        self.assertTrue(specialist_cited)
        self.assertEqual(
            {item["evidence_id"] for item in coordinator_payload["evidence_catalog"]},
            specialist_cited,
        )
        self.assertEqual(
            _packed_evidence_ids(coordinator_payload),
            specialist_cited,
        )
        self.assertEqual(
            {item["source_id"] for item in coordinator_payload["source_catalog"]},
            {item["source_id"] for item in coordinator_payload["evidence_catalog"]},
        )
        required_evidence_fields = {
            "evidence_id", "source_id", "content_reference", "mode", "quality_notes",
            "geographic_applicability", "crop_applicability", "page_or_section",
        }
        for item in coordinator_payload["evidence_catalog"]:
            self.assertEqual(set(item), required_evidence_fields)
            self.assertIn(item["evidence_id"], {record.evidence_id for record in response.evidence})
            self.assertNotIn("excerpt", item)
        for source in coordinator_payload["retrieved_evidence"]:
            self.assertEqual(set(source), {"source_id", "passages"})
            self.assertNotIn("source_title", source)
            for passage in source["passages"]:
                self.assertTrue(passage["excerpt"])
                self.assertTrue(set(passage["evidence_ids"]) <= specialist_cited)
        for source in coordinator_payload["source_catalog"]:
            self.assertEqual(
                set(source),
                {"source_id", "source_title", "source_reference", "source_provenance"},
            )
            self.assertTrue(source["source_reference"])
            self.assertTrue(source["source_provenance"])
        for item in coordinator_payload["agent_results"]:
            if item.get("generated_statements"):
                self.assertNotIn("summary", item)
                self.assertNotIn("interpretation", item)

        # Coordinator receives relevant full excerpts separately from compact
        # provenance-preserving citation catalogs.
        compact = json.dumps({
            "source_catalog": coordinator_payload["source_catalog"],
            "evidence_catalog": coordinator_payload["evidence_catalog"],
        }, ensure_ascii=False)
        expanded_legacy = json.dumps([{
            "evidence_id": record.evidence_id,
            "source_title": record.source.title,
            "source_url_or_reference": record.source.reference.value,
            "excerpt": record.excerpt or "",
            "provenance": record.provenance or "",
            "mode": record.mode.value,
            "status": record.status.value,
            "quality_notes": list(record.quality_notes or ()),
            "geographic_applicability": list(record.geographic_applicability or ()),
            "crop_applicability": list(record.crop_applicability or ()),
        } for record in response.evidence], ensure_ascii=False)
        self.assertLess(len(compact), len(expanded_legacy))
        self.assertTrue(all(record.excerpt and record.provenance and record.source.reference.value
                            for record in response.evidence))
        supported_ids = {
            evidence_id
            for result in response.agent_results
            for statement in getattr(result.payload, "generated_statements", ())
            for evidence_id in statement.evidence_ids
        }
        self.assertTrue(supported_ids)
        self.assertTrue(all(
            set(statement.evidence_ids) <= supported_ids
            for statement in next(
                item.payload.generated_statements for item in response.agent_results
                if item.agent_id.value == "Coordinator"
            )
        ))

        evidence_ids = {item.evidence_id for item in response.evidence}
        for result in response.agent_results:
            payload = result.payload
            statements = getattr(payload, "generated_statements", ())
            for statement in statements:
                self.assertTrue(set(statement.evidence_ids) <= evidence_ids)
        self.assertTrue(any(item.evidence_ids for item in response.findings))

    async def test_coordinator_catalog_preserves_conflict_and_unknown_stage_constraints(self):
        inference = MockWorkflowInference(conflicting_crop_statements=True)
        response = await RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(), knowledge=self.index
        ).run(self.request(), mode=WorkflowMode.GROQ, inference=inference)

        self.assertEqual(response.context.growth_stage.state.value, "unknown")
        self.assertTrue(response.conflicts)
        coordinator_payload = next(
            payload for request, payload in zip(inference.requests, inference.payloads)
            if request.agent_name == "coordinator"
        )
        coordinator_request = next(
            request for request in inference.requests if request.agent_name == "coordinator"
        )
        self.assertLessEqual(estimate_input_tokens(coordinator_request.messages), 2_000)
        self.assertTrue(coordinator_payload["conflicts"])
        self.assertTrue(all("do not select a cause" in item
                            for item in coordinator_payload["conflicts"]))
        self.assertTrue(all("excerpt" not in item
                            for item in coordinator_payload["evidence_catalog"]))
        self.assertIn("Conflicting llm_interpretation:crop findings were retained",
                      response.response_text)

    async def test_invalid_specialist_citation_is_rejected_and_branch_is_partial(self):
        inference = MockWorkflowInference(invalid_crop_citation=True)
        response = await RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(), knowledge=self.index
        ).run(self.request(), mode=WorkflowMode.GROQ, inference=inference)

        crop = next(item for item in response.agent_results if item.agent_id.value == "Crop")
        self.assertEqual(crop.status.value, "partial")
        self.assertEqual(crop.payload.inference_status, "malformed_response")
        self.assertNotIn("evidence:invented", response.response_text)
        allowed = {item.evidence_id for item in response.evidence}
        self.assertTrue(all(set(statement.evidence_ids) <= allowed
                            for item in response.agent_results
                            for statement in getattr(item.payload, "generated_statements", ())))

    async def test_timeout_uses_visible_deterministic_fallback_without_changing_mode(self):
        inference = MockWorkflowInference(fail_all=True)
        response = await RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(), knowledge=self.index
        ).run(self.request(), mode=WorkflowMode.GROQ, inference=inference)

        intake = next(item for item in response.agent_results if item.agent_id.value == "Intake")
        coordinator = next(item for item in response.agent_results if item.agent_id.value == "Coordinator")
        self.assertEqual(response.execution_mode, WorkflowMode.GROQ)
        self.assertEqual(response.status.value, "partial")
        self.assertEqual(intake.payload.interpretation_mode, "deterministic_fallback")
        self.assertEqual(intake.payload.inference_status, "timeout")
        self.assertEqual(coordinator.payload.interpretation_mode, "deterministic_fallback")
        self.assertIn("deterministic evidence summary is shown", response.response_text)

    async def test_malformed_intake_output_is_rejected_and_fallback_is_observable(self):
        inference = MockWorkflowInference(malformed_intake=True)
        response = await RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(), knowledge=self.index
        ).run(self.request(), mode=WorkflowMode.GROQ, inference=inference)
        intake = next(item for item in response.agent_results if item.agent_id.value == "Intake")
        self.assertEqual(intake.payload.interpretation_mode, "deterministic_fallback")
        self.assertEqual(intake.payload.inference_status, "malformed_response")
        self.assertEqual(response.context.crop.value.canonical_id, "crop:oryza-sativa")

    async def test_no_evidence_query_does_not_generate_unsupported_answer(self):
        inference = MockWorkflowInference(no_intake_mentions=True)
        query = "Can you provide tomorrow's exact market price?"
        response = await RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(), knowledge=self.index
        ).run(self.request(query), mode=WorkflowMode.GROQ, inference=inference)

        self.assertEqual(response.execution_mode, WorkflowMode.GROQ)
        self.assertFalse(response.evidence)
        self.assertEqual(len(inference.calls), 1)  # Intake only; no evidence for synthesis.
        self.assertIn("no unsupported answer was generated", response.response_text)
        self.assertIsNone(response.context.crop.value)
        self.assertIsNone(response.context.location.value)

    async def test_registered_crop_or_location_is_not_selected_alongside_unregistered_alternative(self):
        query = "Rice or banana advice for Punjab or Haryana?"
        inference = MockWorkflowInference(intake_payload={
            "crops": [
                {"value": "Rice", "quote": "Rice"},
                {"value": "banana", "quote": "banana"},
            ],
            "locations": [
                {"value": "Punjab", "quote": "Punjab"},
                {"value": "Haryana", "quote": "Haryana"},
            ],
            "intents": ["crop_advice"],
        })
        response = await RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(), knowledge=self.index
        ).run(self.request(query), mode=WorkflowMode.GROQ, inference=inference)
        intake = next(item for item in response.agent_results if item.agent_id.value == "Intake")
        self.assertNotEqual(intake.payload.resolution.crop.status.value, "matched")
        self.assertNotEqual(intake.payload.resolution.location.status.value, "matched")
        self.assertFalse(response.evidence)

    async def test_deterministic_mode_never_calls_injected_inference(self):
        inference = MockWorkflowInference()
        response = await RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(), knowledge=self.index
        ).run(self.request(), inference=inference)
        self.assertEqual(response.execution_mode, WorkflowMode.DETERMINISTIC)
        self.assertIsNone(response.provider_id)
        self.assertEqual(inference.calls, [])

    async def test_fixture_weather_and_market_are_not_sent_as_live_llm_data(self):
        inference = MockWorkflowInference()
        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(),
            knowledge=self.index,
            weather_provider=DeterministicWeatherFixture(),
            market_provider=DeterministicMarketFixture(),
        )
        response = await workflow.run(
            self.request(), mode=WorkflowMode.GROQ, inference=inference
        )
        for name in ("Weather", "Market"):
            result = next(item for item in response.agent_results if item.agent_id.value == name)
            self.assertEqual(result.payload.interpretation_mode, "not_run_fixture")
            self.assertFalse(result.payload.generated_statements)
        self.assertEqual(len(inference.calls), 4)

    async def test_weather_and_market_interpret_only_configured_provider_records(self):
        now = datetime.now(timezone.utc)

        def source(source_id: str, title: str) -> EvidenceSource:
            return EvidenceSource(
                source_id=source_id,
                source_identity="Mock provider contract test; not an external observation.",
                source_type=EvidenceSourceType.EXTERNAL_DATA,
                title=title,
                reference={"kind": "document_id", "value": source_id},
            )

        class CachedWeatherProvider:
            async def get_observation(self, request_id: str, location_id: str):
                return WeatherObservation(
                    request_id, location_id, now - timedelta(minutes=5), now,
                    source("mock-weather", "Mock cached weather record"),
                    EvidenceMode.CACHED, temperature_c=24.0, rainfall_mm=2.0,
                )

        class HistoricalMarketProvider:
            async def get_quote(self, request_id: str, crop_id: str, location_id: str):
                return MarketQuote(
                    request_id, crop_id, location_id, "Mock market", 1000.0,
                    "INR per quintal", now - timedelta(days=1), now,
                    source("mock-market", "Mock historical market record"),
                    EvidenceMode.HISTORICAL,
                )

        inference = MockWorkflowInference()
        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(),
            knowledge=self.index,
            weather_provider=CachedWeatherProvider(),
            market_provider=HistoricalMarketProvider(),
        )
        response = await workflow.run(
            self.request(), mode=WorkflowMode.GROQ, inference=inference
        )
        for name in ("Weather", "Market"):
            result = next(item for item in response.agent_results if item.agent_id.value == name)
            self.assertEqual(result.payload.interpretation_mode, "groq")
            self.assertTrue(result.payload.generated_statements)
            self.assertTrue(set(result.payload.generated_statements[0].evidence_ids)
                            <= set(result.evidence_ids))
        self.assertEqual(len(inference.calls), 6)

    async def test_groq_mode_requires_explicit_inference_access(self):
        with self.assertRaisesRegex(ValueError, "requires authenticated"):
            await RythuSaathiWorkflow(
                resolver=empty_coverage_resolver(), knowledge=self.index
            ).run(self.request(), mode=WorkflowMode.GROQ)


    async def test_coordinator_synthesizes_from_board_evidence_when_specialist_interpretation_fails(self):
        """When specialists retrieve evidence but their LLM interpretation fails,
        the Coordinator should still attempt Groq synthesis using the board-published
        evidence IDs."""
        inference = MockWorkflowInference(fail_specialists=True)
        response = await RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(), knowledge=self.index
        ).run(self.request(), mode=WorkflowMode.GROQ, inference=inference)

        # Specialists retrieve evidence but produce no generated_statements.
        crop = next(item for item in response.agent_results if item.agent_id.value == "Crop")
        scheme = next(item for item in response.agent_results if item.agent_id.value == "Scheme")
        self.assertEqual(crop.status.value, "partial")
        self.assertEqual(scheme.status.value, "partial")
        self.assertEqual(crop.payload.inference_status, "malformed_response")
        self.assertEqual(scheme.payload.inference_status, "malformed_response")
        self.assertFalse(crop.payload.generated_statements)
        self.assertFalse(scheme.payload.generated_statements)
        self.assertTrue(crop.evidence_ids)
        self.assertTrue(scheme.evidence_ids)

        # Evidence IS present in the response.
        self.assertTrue(response.evidence)

        # Coordinator was called via Groq (4 calls: Intake, Crop fail, Scheme fail, Coordinator).
        self.assertEqual(len(inference.calls), 4)
        self.assertEqual(
            {item.agent_name for item in inference.requests},
            {"intake", "crop", "scheme", "coordinator"},
        )

        # Coordinator ran via Groq and produced statements.
        coordinator = next(item for item in response.agent_results if item.agent_id.value == "Coordinator")
        self.assertEqual(coordinator.payload.interpretation_mode, "groq")
        self.assertEqual(coordinator.payload.inference_status, "completed")
        self.assertTrue(coordinator.payload.generated_statements)

        # Coordinator citations reference actual board evidence retrieved by
        # specialists, despite the absence of validated interpretations.
        allowed = {item.evidence_id for item in response.evidence}
        for statement in coordinator.payload.generated_statements:
            self.assertTrue(set(statement.evidence_ids) <= allowed)

        # Groq synthesis text is present.
        self.assertIn("Groq-generated synthesis", response.response_text)

        # Growth stage remains unknown.
        self.assertEqual(response.context.growth_stage.state.value, "unknown")

        # Coordinator receives complete eligible excerpts alongside a compact
        # catalog, while specialist interpretations remain visibly absent.
        coordinator_payload = next(
            payload for request, payload in zip(inference.requests, inference.payloads)
            if request.agent_name == "coordinator"
        )
        coordinator_request = next(
            request for request in inference.requests if request.agent_name == "coordinator"
        )
        self.assertLessEqual(estimate_input_tokens(coordinator_request.messages), 2_000)
        packed_ids = _packed_evidence_ids(coordinator_payload)
        all_specialist_ids = set(crop.evidence_ids) | set(scheme.evidence_ids)
        self.assertTrue(packed_ids)
        self.assertTrue(packed_ids.issubset(all_specialist_ids))
        self.assertLess(len(packed_ids), len(all_specialist_ids))
        self.assertTrue(any(eid in crop.evidence_ids for eid in packed_ids))
        self.assertTrue(any(eid in scheme.evidence_ids for eid in packed_ids))
        self.assertEqual({item.evidence_id for item in response.evidence}, all_specialist_ids)
        self.assertTrue(all(
            passage["excerpt"]
            for source in coordinator_payload["retrieved_evidence"]
            for passage in source["passages"]
        ))
        for item in coordinator_payload["evidence_catalog"]:
            self.assertNotIn("excerpt", item)

        # Agent results sent to Coordinator have summaries (no generated_statements).
        for agent_result in coordinator_payload["agent_results"]:
            if agent_result["agent_id"] in {"Crop", "Scheme"}:
                self.assertIn("summary", agent_result)
                self.assertNotIn("generated_statements", agent_result)

    async def test_no_evidence_with_failed_specialists_still_skips_coordinator_synthesis(self):
        """When no specialist retrieves any evidence AND specialist interpretation
        fails, the Coordinator must NOT attempt synthesis."""
        inference = MockWorkflowInference(no_intake_mentions=True)
        query = "Can you provide tomorrow's exact market price?"
        response = await RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(), knowledge=self.index
        ).run(self.request(query), mode=WorkflowMode.GROQ, inference=inference)

        self.assertFalse(response.evidence)
        self.assertEqual(len(inference.calls), 1)  # Intake only.
        self.assertIn("no unsupported answer was generated", response.response_text)

    async def test_fixture_evidence_excluded_from_coordinator_synthesis_with_failed_specialists(self):
        """Even when specialists fail their interpretation, fixture evidence must
        not be available to the Coordinator as source material."""
        inference = MockWorkflowInference(fail_specialists=True)
        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(),
            knowledge=self.index,
            weather_provider=DeterministicWeatherFixture(),
            market_provider=DeterministicMarketFixture(),
        )
        response = await workflow.run(
            self.request(), mode=WorkflowMode.GROQ, inference=inference
        )

        # Weather and Market agents used fixture mode; their evidence is fixture.
        for name in ("Weather", "Market"):
            result = next(item for item in response.agent_results if item.agent_id.value == name)
            self.assertEqual(result.payload.interpretation_mode, "not_run_fixture")
            self.assertFalse(result.payload.generated_statements)

        coordinator = next(item for item in response.agent_results if item.agent_id.value == "Coordinator")
        fixture_ids = {item.evidence_id for item in response.evidence
                       if item.mode is EvidenceMode.FIXTURE}
        self.assertTrue(fixture_ids)
        self.assertEqual(coordinator.payload.interpretation_mode, "groq")
        for statement in getattr(coordinator.payload, "generated_statements", ()):
            self.assertFalse(set(statement.evidence_ids) & fixture_ids,
                             f"Coordinator cited fixture evidence: {statement.evidence_ids}")
        coordinator_payload = next(
            payload for request, payload in zip(inference.requests, inference.payloads)
            if request.agent_name == "coordinator"
        )
        prompt_ids = _packed_evidence_ids(coordinator_payload)
        self.assertFalse(prompt_ids & fixture_ids)
        self.assertFalse({item["evidence_id"] for item in coordinator_payload["evidence_catalog"]}
                         & fixture_ids)

    async def test_specialist_success_path_unchanged_after_correction(self):
        """The existing successful-specialist path must remain identical: Coordinator
        only cites IDs from specialist generated_statements, not broader board IDs."""
        inference = MockWorkflowInference()
        response = await RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(), knowledge=self.index
        ).run(self.request(), mode=WorkflowMode.GROQ, inference=inference)

        # Specialists succeeded with generated_statements.
        crop = next(item for item in response.agent_results if item.agent_id.value == "Crop")
        self.assertTrue(crop.payload.generated_statements)

        # Coordinator used groq.
        coordinator = next(item for item in response.agent_results if item.agent_id.value == "Coordinator")
        self.assertEqual(coordinator.payload.interpretation_mode, "groq")
        self.assertEqual(coordinator.payload.inference_status, "completed")

        # Coordinator citations are subset of specialist-cited evidence IDs.
        specialist_cited = {
            eid
            for result in response.agent_results
            for statement in getattr(result.payload, "generated_statements", ())
            if statement.kind in {"evidence", "interpretation"}
            for eid in statement.evidence_ids
            if result.agent_id.value != "Coordinator"
        }
        self.assertTrue(specialist_cited)
        for statement in coordinator.payload.generated_statements:
            self.assertTrue(set(statement.evidence_ids) <= specialist_cited)

    async def test_coordinator_synthesis_failure_with_board_evidence_triggers_deterministic_fallback(self):
        """When specialists fail AND the Coordinator Groq call also fails, the
        deterministic fallback must activate."""
        inference = MockWorkflowInference(fail_specialists=True, fail_coordinator=True)
        response = await RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(), knowledge=self.index
        ).run(self.request(), mode=WorkflowMode.GROQ, inference=inference)

        coordinator = next(item for item in response.agent_results if item.agent_id.value == "Coordinator")
        self.assertEqual(coordinator.payload.interpretation_mode, "deterministic_fallback")
        self.assertEqual(coordinator.payload.inference_status, "malformed_response")
        self.assertTrue(response.evidence)
        self.assertEqual(len(inference.calls), 4)
        self.assertIn("deterministic evidence summary is shown", response.response_text)

    async def test_invalid_coordinator_citation_is_rejected_against_eligible_request_evidence(self):
        inference = MockWorkflowInference(
            fail_specialists=True,
            invalid_coordinator_citation=True,
        )
        response = await RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(), knowledge=self.index
        ).run(self.request(), mode=WorkflowMode.GROQ, inference=inference)

        coordinator = next(item for item in response.agent_results if item.agent_id.value == "Coordinator")
        self.assertEqual(coordinator.payload.interpretation_mode, "deterministic_fallback")
        self.assertEqual(coordinator.payload.inference_status, "malformed_response")
        self.assertFalse(coordinator.payload.generated_statements)
        self.assertNotIn("evidence:invented", response.response_text)
        self.assertIn("deterministic evidence summary is shown", response.response_text)

    async def test_token_budget_accounting_with_failed_specialists(self):
        """Token budget reservations must remain correct when specialists fail:
        the Coordinator max_tokens must match its 600-token limit."""
        inference = MockWorkflowInference(fail_specialists=True)
        await RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(), knowledge=self.index
        ).run(self.request(), mode=WorkflowMode.GROQ, inference=inference)

        coordinator_request = next(
            item for item in inference.requests if item.agent_name == "coordinator"
        )
        self.assertEqual(coordinator_request.options["max_tokens"], 600)

        # Agent-specific output limits match approved scope (intake: 256, crop: 400, scheme: 400, coordinator: 600)
        budget_map = {item.agent_name: item.options["max_tokens"] for item in inference.requests}
        self.assertEqual(budget_map, {
            "intake": 256,
            "crop": 400,
            "scheme": 400,
            "coordinator": 600,
        })
        self.assertEqual(sum(budget_map.values()), 1_656)

    async def test_context_budget_overflow_uses_deterministic_fallback_without_omitting_evidence(self):
        """A complete essential packet that cannot fit must never be dispatched."""
        class BudgetConstrainedInference(MockWorkflowInference):
            @property
            def usage_records(self):
                # 600 output + 128 packing margin leaves 64 input tokens.
                return (SimpleNamespace(budget_remaining_tokens=792),)

        inference = BudgetConstrainedInference(fail_specialists=True)
        response = await RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(), knowledge=self.index
        ).run(self.request(), mode=WorkflowMode.GROQ, inference=inference)

        coordinator = next(item for item in response.agent_results if item.agent_id.value == "Coordinator")
        self.assertEqual(coordinator.payload.interpretation_mode, "deterministic_fallback")
        self.assertEqual(coordinator.payload.inference_status, "not_run_context_budget_exceeded")
        self.assertIn("no evidence was silently omitted", response.response_text)
        self.assertEqual({request.agent_name for request in inference.requests},
                         {"intake", "crop", "scheme"})
        self.assertTrue(response.evidence)

    async def test_concurrent_reservation_preserved_with_failed_specialists(self):
        """Both specialist calls should run (or attempt) before the Coordinator,
        maintaining the concurrent reservation pattern."""
        class ConcurrentSpecialistFailureInference(MockWorkflowInference):
            def __init__(self):
                super().__init__(fail_specialists=True)
                self._specialists_ready = asyncio.Event()
                self._specialist_call_count = 0

            async def complete(self, request, *, expected_user_id, expected_tenant_id):
                if request.agent_name in {"crop", "scheme"}:
                    self._specialist_call_count += 1
                    if self._specialist_call_count == 2:
                        self._specialists_ready.set()
                    await asyncio.wait_for(self._specialists_ready.wait(), timeout=1)
                return await super().complete(
                    request,
                    expected_user_id=expected_user_id,
                    expected_tenant_id=expected_tenant_id,
                )

        inference = ConcurrentSpecialistFailureInference()
        await RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(), knowledge=self.index
        ).run(self.request(), mode=WorkflowMode.GROQ, inference=inference)

        agent_order = [item.agent_name for item in inference.requests]
        # Intake first, then specialists (crop & scheme in either order), then coordinator.
        self.assertEqual(agent_order[0], "intake")
        self.assertIn("crop", agent_order[1:3])
        self.assertIn("scheme", agent_order[1:3])
        self.assertEqual(agent_order[3], "coordinator")

    async def test_truncated_json_length_termination_triggers_malformed_response_fallback(self):
        """When an agent response is truncated mid-stream (e.g. finish_reason='length'),
        the unclosed JSON must raise MALFORMED_RESPONSE and fall back cleanly."""
        class TruncatedResponseInference(MockWorkflowInference):
            async def complete(self, request: InferenceRequest, *, expected_user_id: str,
                               expected_tenant_id: str) -> ProviderResponse:
                self.calls.append((_message_text(request.messages[0]), expected_user_id, expected_tenant_id))
                self.requests.append(request)
                if request.agent_name == "intake":
                    # Truncated JSON midway through fields
                    truncated_json = '{"crops":[{"value":"rice","quote":"rice"}],"locations":'
                    return ProviderResponse(
                        "groq", self.model_id,
                        ChatResponse(messages=[Message("assistant", [truncated_json])]),
                    )
                return await super().complete(request, expected_user_id=expected_user_id, expected_tenant_id=expected_tenant_id)

        inference = TruncatedResponseInference()
        response = await RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(), knowledge=self.index
        ).run(self.request(), mode=WorkflowMode.GROQ, inference=inference)

        intake = next(item for item in response.agent_results if item.agent_id.value == "Intake")
        self.assertEqual(intake.status.value, "partial")
        self.assertEqual(intake.payload.inference_status, "malformed_response")
        self.assertEqual(intake.payload.interpretation_mode, "deterministic_fallback")
        # Deterministic resolver still resolved crop and location
        self.assertEqual(response.context.crop.value.canonical_id, "crop:oryza-sativa")
        self.assertEqual(response.context.location.value.canonical_id, "location:state:in-pb")

    def test_calibrated_token_estimator_conservative_across_captured_payloads(self):
        """Input token estimator must provide a conservative upper bound (>= actual tokens)
        across all four captured live execution payloads."""
        captured_payloads = {
            "intake": (2698, 600),
            "crop": (6467, 1391),
            "scheme": (8739, 1795),
            "coordinator": (9189, 2116),
        }
        for name, (char_count, actual_tokens) in captured_payloads.items():
            # Build representative structured messages of the exact serialized length
            sample_content = "x" * (char_count - 24)
            messages = [Message("system", ["system prompt"]), Message("user", [sample_content])]
            est = estimate_input_tokens(messages)
            self.assertGreaterEqual(
                est, actual_tokens,
                f"Estimator undercounted for {name}: estimated {est} < actual {actual_tokens}",
            )

    async def test_revised_crop_and_scheme_retrieval_coverage_topk4(self):
        """Crop and Scheme retrieval with top_k=4 must retain essential diagnostic and scheme evidence."""
        response = await RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(), knowledge=self.index
        ).run(self.request(), mode=WorkflowMode.DETERMINISTIC)

        crop = next(item for item in response.agent_results if item.agent_id.value == "Crop")
        scheme = next(item for item in response.agent_results if item.agent_id.value == "Scheme")

        # CropAgent retrieves at most 4 items per geography scope
        self.assertLessEqual(len(crop.evidence_ids), 4)
        self.assertGreater(len(crop.evidence_ids), 0)

        # Essential PAU guidance is present in retrieved titles
        crop_titles = crop.payload.source_titles
        self.assertTrue(any("micronutrient" in t.lower() or "dwarfing" in t.lower() or "kharif" in t.lower()
                            for t in crop_titles))

        # SchemeAgent retrieves central and state jurisdiction records (at most 4 each)
        self.assertGreater(len(scheme.evidence_ids), 0)
        scheme_titles = scheme.payload.source_titles
        self.assertTrue(any("pm-kisan" in t.lower() or "fasal bima" in t.lower()
                            for t in scheme_titles))

    async def test_output_limits_match_tpm_reservations_and_fit_7200_budget(self):
        """Verify output limits match reservations exactly (256/400/400/600) and
        total reserved tokens across all 4 agents stay strictly within 7,200 TPM."""
        inference = MockWorkflowInference()
        response = await RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(), knowledge=self.index
        ).run(self.request(), mode=WorkflowMode.GROQ, inference=inference)

        limits = {item.agent_name: item.options["max_tokens"] for item in inference.requests}
        self.assertEqual(limits, {
            "intake": 256,
            "crop": 400,
            "scheme": 400,
            "coordinator": 600,
        })
        total_output_limit = sum(limits.values())
        self.assertEqual(total_output_limit, 1_656)

        total_estimated_input = sum(estimate_input_tokens(r.messages) for r in inference.requests)
        total_reserved = total_estimated_input + total_output_limit
        self.assertLess(
            total_reserved, 7_200,
            f"Total reserved tokens {total_reserved} must be strictly less than 7,200 TPM limit",
        )
        # Verify safety margin is at least 400 tokens below TPM limit
        self.assertGreaterEqual(
            7_200 - total_reserved, 400,
            f"Remaining headroom {7_200 - total_reserved} must be at least 400 tokens",
        )


class InferenceAdapterRedactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_scoped_access_redacts_session_and_credential_identifiers(self):
        class Service:
            model_id = "fixture/model"

            async def complete(self, token, credential_id, request, **kwargs):
                self.seen = (token, credential_id, kwargs)
                return ProviderResponse("groq", "fixture/model", ChatResponse(messages=[]))

        service = Service()
        access = AuthenticatedGroqInference(service, "session-secret-canary", "credential-id-canary")
        self.assertNotIn("session-secret-canary", repr(access))
        self.assertNotIn("credential-id-canary", repr(access))
        await access.complete(
            InferenceRequest([Message("user", ["safe text"])]),
            expected_user_id="user-one",
            expected_tenant_id="tenant-one",
        )
        self.assertEqual(service.seen[0:2], ("session-secret-canary", "credential-id-canary"))
        self.assertEqual(service.seen[2], {
            "expected_user_id": "user-one", "expected_tenant_id": "tenant-one"
        })

    async def test_authentication_denial_is_classified_without_raw_details(self):
        class DeniedService:
            model_id = "fixture/model"

            async def complete(self, *_args, **_kwargs):
                raise PermissionError("denied session-secret-canary")

        access = AuthenticatedGroqInference(
            DeniedService(), "session-secret-canary", "credential-id-canary"
        )
        with self.assertRaises(ProviderFailure) as caught:
            await access.complete(
                InferenceRequest([Message("user", ["safe text"])]),
                expected_user_id="user-one",
                expected_tenant_id="tenant-one",
            )
        self.assertEqual(caught.exception.kind, ProviderFailureKind.AUTHENTICATION)
        self.assertNotIn("session-secret-canary", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

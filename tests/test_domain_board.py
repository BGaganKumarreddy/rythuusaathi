from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import unittest

from rythusaathi.domain import (
    AdvisoryBoard,
    AgentId,
    BoardConflict,
    BoardSection,
    BoardState,
    BoardTraceAction,
    BoardWriteDenied,
    ContextResolutionRequest,
    ContextResolver,
    CoverageRegistry,
    EvidenceMode,
    EvidenceRecord,
    EvidenceSource,
    EvidenceSourceType,
    EvidenceStatus,
    RecommendationCategory,
    RecommendationSupport,
    RegistryMetadata,
    WorkflowMetadata,
    AgriculturalRecommendation,
)
from rythusaathi.domain.seed import CROP_REGISTRY, LOCATION_REGISTRY


def make_board(request_id="request-a"):
    empty_coverage = CoverageRegistry(RegistryMetadata(
        registry_id="coverage", version="v1", effective_from=datetime(2026, 1, 1).date(),
        source_reference="https://example.org/coverage", scope_note="test fixture",
    ))
    context = ContextResolver(CROP_REGISTRY, LOCATION_REGISTRY, empty_coverage).resolve(
        ContextResolutionRequest(
            request_id=request_id, user_id="user-1", tenant_id="tenant-1",
            workflow=WorkflowMetadata(trace_id=f"trace-{request_id}"),
        )
    ).context
    return AdvisoryBoard(context)


def evidence(request_id, evidence_id="evidence-1", status=EvidenceStatus.AVAILABLE):
    return EvidenceRecord(
        evidence_id=evidence_id,
        request_id=request_id,
        source=EvidenceSource(
            source_id="icar-kharif-2025",
            source_identity="icar.gov.in",
            source_type=EvidenceSourceType.PRIMARY_GOVERNMENT,
            title="ICAR Kharif advisory",
            reference={"kind": "url", "value": "https://icar.gov.in/advisory"},
        ),
        excerpt="Crop guidance from a cited source." if status is EvidenceStatus.AVAILABLE else None,
        retrieved_at=datetime(2026, 11, 6, tzinfo=timezone.utc),
        provenance="Retrieved from the source document.",
        mode=EvidenceMode.FIXTURE,
        status=status,
    )


def recommendation(request_id, evidence_ids=("evidence-1",), support=RecommendationSupport.EVIDENCE_BACKED):
    return AgriculturalRecommendation(
        recommendation_id="recommendation-1",
        request_id=request_id,
        category=RecommendationCategory.CROP_MANAGEMENT,
        proposed_action="Inspect the affected plants and confirm symptoms.",
        evidence_ids=evidence_ids,
        reasoning_summary="A source-backed follow-up is appropriate.",
        support=support,
        confidence=0.6,
        uncertainty="The reported symptoms have not been independently diagnosed."
        if support is RecommendationSupport.HYPOTHESIS else None,
        missing_information=("clear symptom description",)
        if support is RecommendationSupport.NEEDS_INFORMATION else (),
    )


class AdvisoryBoardTests(unittest.TestCase):
    def test_request_isolation_and_fixed_agent_attribution(self):
        first = make_board("request-a")
        second = make_board("request-b")
        finding = first.writer_for(AgentId.WEATHER).publish_finding(
            topic="rainfall", claim="No verified observation is available.", confidence=0.3,
            uncertainty="Weather source is unavailable.",
        )
        self.assertEqual(finding.agent_id, AgentId.WEATHER)
        self.assertEqual(finding.section, BoardSection.WEATHER)
        self.assertEqual(finding.request_id, "request-a")
        self.assertEqual(len(second.findings), 0)
        self.assertEqual(first.findings_for(section=BoardSection.WEATHER), (finding,))
        self.assertEqual(first.trace_events[0].action, BoardTraceAction.FINDING_APPENDED)
        self.assertEqual(first.trace_events[0].trace_id, "trace-request-a")
        self.assertNotIn("claim", type(first.trace_events[0]).model_fields)

    def test_evidence_must_be_request_scoped_and_findings_are_append_only(self):
        board = make_board()
        crop_writer = board.writer_for(AgentId.CROP)
        with self.assertRaises(BoardConflict):
            crop_writer.publish_evidence(evidence("request-b"))
        with self.assertRaises(BoardConflict):
            crop_writer.publish_finding(topic="soil", claim="pH is 6.5", evidence_ids=("missing",))

        crop_writer.publish_evidence(evidence("request-a"))
        first = crop_writer.publish_finding(topic="soil", claim="pH is 6.5",
                                            evidence_ids=("evidence-1",))
        second = crop_writer.publish_finding(topic="soil", claim="pH is 8.0",
                                             evidence_ids=("evidence-1",))
        self.assertNotEqual(first.finding_id, second.finding_id)
        self.assertEqual(len(board.findings), 2)
        conflicts = board.conflicts()
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(set(conflicts[0].claims), {"pH is 6.5", "pH is 8.0"})
        self.assertEqual(
            [event.action for event in board.trace_events],
            [BoardTraceAction.EVIDENCE_APPENDED,
             BoardTraceAction.FINDING_APPENDED,
             BoardTraceAction.FINDING_APPENDED],
        )

    def test_concurrent_finding_appends_preserve_every_item(self):
        board = make_board()
        writer = board.writer_for(AgentId.MARKET)
        with ThreadPoolExecutor(max_workers=8) as pool:
            items = list(pool.map(
                lambda index: writer.publish_finding(topic=f"price-{index}", claim=f"claim-{index}"),
                range(80),
            ))
        self.assertEqual(len(items), 80)
        self.assertEqual(len(board.findings), 80)
        self.assertEqual(len({item.finding_id for item in board.findings}), 80)
        self.assertTrue(all(item.agent_id is AgentId.MARKET for item in board.findings))

    def test_state_transitions_enforce_owner_and_stop_reason(self):
        board = make_board()
        with self.assertRaises(BoardWriteDenied):
            board.writer_for(AgentId.CROP).complete()
        with self.assertRaises(ValueError):
            board.writer_for(AgentId.INTAKE).stop(" ")
        board.writer_for(AgentId.INTAKE).stop("insufficient case details")
        self.assertEqual(board.state, BoardState.STOPPED)
        self.assertEqual(board.stop_reason, "insufficient case details")
        self.assertEqual(board.trace_events[-1].action, BoardTraceAction.STATE_TRANSITIONED)
        self.assertEqual(board.trace_events[-1].to_state, BoardState.STOPPED)
        with self.assertRaises(BoardConflict):
            board.writer_for(AgentId.COORDINATOR).complete()

        completed = make_board("request-complete")
        completed.writer_for(AgentId.COORDINATOR).complete()
        self.assertEqual(completed.state, BoardState.COMPLETED)

    def test_recommendations_require_coordinator_and_available_scoped_evidence(self):
        board = make_board()
        crop_writer = board.writer_for(AgentId.CROP)
        crop_writer.publish_evidence(evidence("request-a"))
        with self.assertRaises(BoardWriteDenied):
            crop_writer.publish_recommendation(recommendation("request-a"))
        coordinator = board.writer_for(AgentId.COORDINATOR)
        with self.assertRaises(BoardConflict):
            coordinator.publish_recommendation(recommendation("request-b"))
        with self.assertRaises(BoardConflict):
            coordinator.publish_recommendation(recommendation("request-a", ("unknown",)))
        coordinator.publish_recommendation(recommendation("request-a"))
        self.assertEqual(len(board.recommendations), 1)

        stale_board = make_board("request-stale")
        stale_board.writer_for(AgentId.CROP).publish_evidence(
            evidence("request-stale", status=EvidenceStatus.STALE)
        )
        with self.assertRaises(BoardConflict):
            stale_board.writer_for(AgentId.COORDINATOR).publish_recommendation(
                recommendation("request-stale")
            )


if __name__ == "__main__":
    unittest.main()

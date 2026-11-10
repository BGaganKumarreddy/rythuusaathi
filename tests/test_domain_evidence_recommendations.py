from datetime import date, datetime, timezone
import unittest

from pydantic import ValidationError

from rythusaathi.domain import (
    AgriculturalRecommendation,
    ContentReference,
    EvidenceMode,
    EvidenceRecord,
    EvidenceSource,
    EvidenceSourceType,
    EvidenceStatus,
    RecommendationCategory,
    RecommendationSupport,
    ReferenceKind,
)


def make_evidence(*, mode=EvidenceMode.LIVE, status=EvidenceStatus.AVAILABLE,
                  excerpt="An official agricultural source excerpt.", content_reference=None,
                  publication_date=date(2026, 10, 1), retrieved_at=None, **changes):
    values = dict(
        evidence_id="evidence-1",
        request_id="request-1",
        source=EvidenceSource(
            source_id="icar-advisory-2025",
            source_identity="ICAR",
            source_type=EvidenceSourceType.PRIMARY_GOVERNMENT,
            title="Kharif advisory",
            reference=ContentReference(kind=ReferenceKind.URL,
                                       value="https://icar.gov.in/advisory"),
            publisher="Indian Council of Agricultural Research",
        ),
        excerpt=excerpt,
        content_reference=content_reference,
        retrieved_at=retrieved_at or datetime(2026, 11, 6, tzinfo=timezone.utc),
        publication_date=publication_date,
        geographic_applicability=("location:state:in-tg",),
        crop_applicability=("crop:oryza-sativa",),
        provenance="Downloaded from the official source page; page 12.",
        mode=mode,
        status=status,
        confidence=0.9,
        relevance=0.8,
        units="kg/ha",
        page_or_section="p. 12, Rice",
    )
    values.update(changes)
    return EvidenceRecord(**values)


class EvidenceContractTests(unittest.TestCase):
    def test_mode_and_status_are_independent_and_round_trip(self):
        for mode in EvidenceMode:
            for status in EvidenceStatus:
                item = make_evidence(
                    mode=mode,
                    status=status,
                    excerpt="A cited excerpt." if status is EvidenceStatus.AVAILABLE else None,
                    publication_date=date(2026, 10, 1),
                )
                restored = EvidenceRecord.model_validate_json(item.model_dump_json())
                self.assertEqual(restored.mode, mode)
                self.assertEqual(restored.status, status)
                self.assertEqual(restored.source.source_type, EvidenceSourceType.PRIMARY_GOVERNMENT)

    def test_available_evidence_requires_excerpt_or_content_reference(self):
        with self.assertRaises(ValidationError):
            make_evidence(excerpt=None, content_reference=None)
        item = make_evidence(
            excerpt=None,
            content_reference=ContentReference(kind=ReferenceKind.DOCUMENT_ID,
                                               value="doc:icar:2025:page-12"),
        )
        self.assertIsNotNone(item.content_reference)
        self.assertIsNone(item.excerpt)

    def test_invalid_references_timestamps_dates_and_scope_are_rejected(self):
        with self.assertRaises(ValidationError):
            ContentReference(kind=ReferenceKind.URL, value="http://example.org/source")
        with self.assertRaises(ValidationError):
            make_evidence(retrieved_at=datetime(2026, 11, 6))
        with self.assertRaises(ValidationError):
            make_evidence(publication_date=date(2026, 11, 7))
        with self.assertRaises(ValidationError):
            make_evidence(geographic_applicability=("location:state:in-tg", "location:state:in-tg"))


class RecommendationContractTests(unittest.TestCase):
    def _recommendation(self, **changes):
        values = dict(
            recommendation_id="recommendation-1",
            request_id="request-1",
            category=RecommendationCategory.CROP_MANAGEMENT,
            proposed_action="Inspect plants and share a clear symptom image.",
            evidence_ids=("evidence-1",),
            reasoning_summary="The cited evidence supports this limited next step.",
            support=RecommendationSupport.EVIDENCE_BACKED,
            confidence=0.7,
            applicability_conditions=("The reported crop is rice.",),
            potential_risks=("Visual diagnosis may be inconclusive.",),
            recommended_follow_up=("Consult a local extension officer if symptoms spread.",),
        )
        values.update(changes)
        return AgriculturalRecommendation(**values)

    def test_evidence_backed_hypothesis_and_needs_information_round_trip(self):
        items = (
            self._recommendation(),
            self._recommendation(support=RecommendationSupport.HYPOTHESIS,
                                 uncertainty="Symptoms are not specific enough to identify a cause."),
            self._recommendation(support=RecommendationSupport.NEEDS_INFORMATION,
                                 missing_information=("plant age",)),
        )
        for item in items:
            self.assertEqual(AgriculturalRecommendation.model_validate_json(item.model_dump_json()), item)

    def test_unsupported_evidence_and_missing_requirements_are_rejected(self):
        with self.assertRaises(ValidationError):
            self._recommendation(evidence_ids=())
        with self.assertRaises(ValidationError):
            self._recommendation(support=RecommendationSupport.HYPOTHESIS)
        with self.assertRaises(ValidationError):
            self._recommendation(support=RecommendationSupport.NEEDS_INFORMATION)
        with self.assertRaises(ValidationError):
            self._recommendation(evidence_ids=("evidence-1", "evidence-1"))
        with self.assertRaises(ValidationError):
            self._recommendation(potential_risks=(" ",))


if __name__ == "__main__":
    unittest.main()

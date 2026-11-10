from datetime import date, datetime, timezone
import unittest

from pydantic import ValidationError

from rythusaathi.domain import (
    ContextFact,
    ContextResolutionRequest,
    ContextResolver,
    CoverageRegistry,
    EnvironmentalObservation,
    FactState,
    IrrigationInformation,
    LocationRecord,
    LocationRegistry,
    MissingInformationReason,
    RegistryLevel,
    RegistryMetadata,
    ResolutionStatus,
    SoilInformation,
    WorkflowMetadata,
)
from rythusaathi.domain.seed import (
    CROP_REGISTRY,
    LOCATION_RECORDS,
    LOCATION_REGISTRY,
    LOCATION_REGISTRY_METADATA,
)


class ContextModelTests(unittest.TestCase):
    def test_partial_context_round_trip_and_fact_states(self):
        request = ContextResolutionRequest(
            request_id="req-1", user_id="user-1", tenant_id="tenant-1",
            crop_query="rice", location_query="Nalgonda",
            farmer_report=ContextFact[tuple[str, ...]].supplied(("yellow leaves",)),
            soil=ContextFact[SoilInformation].supplied(
                SoilInformation(ph=ContextFact[float].supplied(6.7))
            ),
            language_preference=ContextFact[str].supplied("te"),
            workflow=WorkflowMetadata(trace_id="trace-1", labels=(("source", "cli"),)),
        )
        coverage = CoverageRegistry(
            RegistryMetadata(
                registry_id="coverage-test", version="v1", effective_from=date(2026, 1, 1),
                source_reference="https://example.org/coverage", scope_note="test fixture",
            ),
            (("crop:oryza-sativa", "location:district:in-tg-nalgonda"),),
        )
        result = ContextResolver(CROP_REGISTRY, LOCATION_REGISTRY, coverage).resolve(request)
        restored = type(result.context).model_validate_json(result.context.model_dump_json())

        self.assertEqual(restored, result.context)
        self.assertEqual(restored.location.state, FactState.SUPPLIED)
        self.assertEqual(restored.crop.state, FactState.SUPPLIED)
        self.assertEqual(restored.crop_variety.state, FactState.UNKNOWN)
        self.assertEqual(restored.sowing_date.state, FactState.UNKNOWN)
        self.assertEqual(restored.farmer_report.value, ("yellow leaves",))
        self.assertEqual(restored.soil.value.ph.value, 6.7)
        self.assertTrue(result.can_proceed_with_local_advice)

    def test_unknown_unavailable_and_supplied_are_distinct(self):
        unknown = ContextFact[str].unknown()
        unavailable = ContextFact[str].unavailable("not in this registry")
        supplied = ContextFact[str].supplied("rice")
        self.assertEqual({unknown.state, unavailable.state, supplied.state}, set(FactState))
        self.assertEqual(unknown.value, None)
        self.assertEqual(unavailable.detail, "not in this registry")

    def test_fact_state_invariants_and_numeric_bounds(self):
        with self.assertRaises(ValidationError):
            ContextFact[str](state=FactState.SUPPLIED)
        with self.assertRaises(ValidationError):
            ContextFact[str](state=FactState.UNKNOWN, value="rice")
        with self.assertRaises(ValidationError):
            ContextFact[str].unavailable(" ")
        with self.assertRaises(ValidationError):
            SoilInformation(ph=ContextFact[float].supplied(14.1))
        with self.assertRaises(ValidationError):
            SoilInformation(nitrogen_mg_kg=ContextFact[float].supplied(float("nan")))
        with self.assertRaises(ValidationError):
            IrrigationInformation(interval_days=ContextFact[int].supplied(0))
        with self.assertRaises(ValidationError):
            EnvironmentalObservation(name="rain", value=2.0,
                                     observed_at=datetime(2026, 1, 1))


class ContextResolutionTests(unittest.TestCase):
    def _coverage(self, pairs=()):
        return CoverageRegistry(
            RegistryMetadata(
                registry_id="coverage-test", version="v1", effective_from=date(2026, 1, 1),
                source_reference="https://example.org/coverage", scope_note="test fixture",
            ),
            tuple(pairs),
        )

    def test_alias_variety_growth_stage_and_canonical_context(self):
        coverage = self._coverage((
            ("crop:oryza-sativa", "location:district:in-tg-nalgonda"),
        ))
        result = ContextResolver(CROP_REGISTRY, LOCATION_REGISTRY, coverage).resolve(
            ContextResolutionRequest(
                request_id="req-2", user_id="user-1", tenant_id="tenant-1",
                crop_query="  వరి  ", location_query="Nalgonda", variety_query="Pusa Basmati 1121",
                growth_stage_query="Tillering", as_of=date(2026, 11, 6),
            )
        )
        self.assertEqual(result.crop.status, ResolutionStatus.MATCHED)
        self.assertEqual(result.context.crop.value.canonical_id, "crop:oryza-sativa")
        self.assertEqual(result.context.location.value.canonical_id, "location:district:in-tg-nalgonda")
        self.assertEqual(result.variety.canonical_id, "variety:oryza-sativa:pusa-basmati-1121")
        self.assertEqual(result.context.growth_stage.value, "tillering")
        self.assertTrue(result.can_proceed_with_local_advice)

    def test_ambiguous_and_unsupported_selections_are_not_substituted(self):
        result = ContextResolver(CROP_REGISTRY, LOCATION_REGISTRY, self._coverage()).resolve(
            ContextResolutionRequest(
                request_id="req-3", user_id="user-1", tenant_id="tenant-1",
                crop_query="rice", location_query="Nalgonda", location_parent_id="location:state:in-ap",
            )
        )
        self.assertEqual(result.location.status, ResolutionStatus.UNSUPPORTED)
        self.assertEqual(result.context.location.state, FactState.UNAVAILABLE)
        self.assertFalse(result.can_proceed_with_local_advice)
        self.assertEqual(result.context.missing_information[0].reason,
                         MissingInformationReason.UNSUPPORTED)

    def test_missing_information_and_unregistered_coverage_are_explicit(self):
        result = ContextResolver(CROP_REGISTRY, LOCATION_REGISTRY, self._coverage()).resolve(
            ContextResolutionRequest(
                request_id="req-4", user_id="user-1", tenant_id="tenant-1",
                crop_query="rice", location_query="Nalgonda",
            )
        )
        self.assertEqual(result.coverage.status, ResolutionStatus.UNSUPPORTED)
        self.assertFalse(result.can_proceed_with_local_advice)
        self.assertIn("crop_location_coverage",
                      {item.field_name for item in result.context.missing_information})
        self.assertEqual(result.context.crop_variety.state, FactState.UNKNOWN)

        empty = ContextResolver(CROP_REGISTRY, LOCATION_REGISTRY, self._coverage()).resolve(
            ContextResolutionRequest(request_id="req-5", user_id="user-1", tenant_id="tenant-1")
        )
        self.assertEqual(empty.crop.status, ResolutionStatus.MISSING)
        self.assertEqual(empty.location.status, ResolutionStatus.MISSING)
        self.assertEqual(empty.context.crop.state, FactState.UNKNOWN)
        self.assertEqual(empty.context.location.state, FactState.UNKNOWN)
        self.assertEqual(
            {item.field_name for item in empty.context.missing_information},
            {"crop", "location"},
        )

    def test_ambiguous_location_stays_unavailable_and_requests_clarification(self):
        locations = LocationRegistry(
            LOCATION_REGISTRY_METADATA,
            LOCATION_RECORDS + (LocationRecord(
                location_id="location:district:in-ap-nalgonda-example",
                name="Nalgonda", level=RegistryLevel.DISTRICT,
                parent_id="location:state:in-ap",
                source_reference="https://example.org/ambiguous-fixture",
            ),),
        )
        result = ContextResolver(CROP_REGISTRY, locations, self._coverage()).resolve(
            ContextResolutionRequest(
                request_id="req-ambiguous", user_id="user-1", tenant_id="tenant-1",
                crop_query="rice", location_query="Nalgonda",
            )
        )
        self.assertEqual(result.location.status, ResolutionStatus.AMBIGUOUS)
        self.assertIsNone(result.context.location.value)
        self.assertEqual(result.context.location.state, FactState.UNAVAILABLE)
        location_question = next(item for item in result.context.missing_information
                                 if item.field_name == "location")
        self.assertEqual(location_question.reason, MissingInformationReason.AMBIGUOUS)

    def test_region_scoped_variety_uses_location_ancestors(self):
        result = ContextResolver(CROP_REGISTRY, LOCATION_REGISTRY, self._coverage()).resolve(
            ContextResolutionRequest(
                request_id="req-cotton", user_id="user-1", tenant_id="tenant-1",
                crop_query="cotton", location_query="Ludhiana", variety_query="F2228",
            )
        )
        self.assertEqual(result.location.status, ResolutionStatus.MATCHED)
        self.assertEqual(result.variety.status, ResolutionStatus.MATCHED)

        wrong_region = ContextResolver(CROP_REGISTRY, LOCATION_REGISTRY, self._coverage()).resolve(
            ContextResolutionRequest(
                request_id="req-cotton-wrong-region", user_id="user-1", tenant_id="tenant-1",
                crop_query="cotton", location_query="Nalgonda", variety_query="F2228",
            )
        )
        self.assertEqual(wrong_region.variety.status, ResolutionStatus.UNSUPPORTED)


if __name__ == "__main__":
    unittest.main()

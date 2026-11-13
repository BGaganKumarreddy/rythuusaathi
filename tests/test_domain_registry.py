from datetime import date
import unittest

from pydantic import ValidationError

from rythusaathi.domain import (
    CropCategory,
    CropRecord,
    CropRegistry,
    CoverageRegistry,
    LocationRecord,
    LocationRegistry,
    NameVariant,
    RegistryLevel,
    RegistryMetadata,
    Resolution,
    ResolutionStatus,
)
from rythusaathi.domain.seed import (
    CROP_REGISTRY,
    LOCATION_REGISTRY,
    LOCATION_REGISTRY_METADATA,
    RICE,
)


def metadata(registry_id="test", effective_from=date(2026, 1, 1), effective_to=None):
    return RegistryMetadata(
        registry_id=registry_id, version="v1", effective_from=effective_from,
        effective_to=effective_to, source_reference="https://example.org/catalog",
        scope_note="test fixture",
    )


class CropRegistryTests(unittest.TestCase):
    def test_stable_ids_common_regional_and_scientific_lookup(self):
        self.assertEqual(CROP_REGISTRY.get("crop:oryza-sativa"), RICE)
        self.assertEqual(CROP_REGISTRY.resolve("  RICE  ").canonical_id, "crop:oryza-sativa")
        self.assertEqual(CROP_REGISTRY.resolve("Oryza sativa L.").canonical_id,
                         "crop:oryza-sativa")
        self.assertEqual(CROP_REGISTRY.resolve("crop:oryza-sativa").canonical_id,
                         "crop:oryza-sativa")
        self.assertEqual(CROP_REGISTRY.resolve(
            "వరి", language="te", region_ids=("location:state:in-tg",)
        ).canonical_id,
                         "crop:oryza-sativa")
        self.assertEqual(CROP_REGISTRY.resolve("వరి", language="te").status,
                         ResolutionStatus.NOT_FOUND)
        self.assertEqual(CROP_REGISTRY.search("oryza")[0].crop_id, "crop:oryza-sativa")
        self.assertEqual(RICE.category, CropCategory.CEREAL)

    def test_variety_applicability_is_checked_against_resolved_region(self):
        self.assertEqual(CROP_REGISTRY.resolve_variety(
            "crop:gossypium-hirsutum", "F2228",
            region_id="location:state:in-pb",
        ).status, ResolutionStatus.MATCHED)
        self.assertEqual(CROP_REGISTRY.resolve_variety(
            "crop:gossypium-hirsutum", "F2228",
            region_id="location:state:in-tg",
        ).status, ResolutionStatus.UNSUPPORTED)

    def test_alias_and_effective_date_resolution(self):
        retired = CropRecord(
            crop_id="crop:retired", common_names=(NameVariant(value="old crop"),),
            scientific_name="Old crop L.", category=CropCategory.OTHER,
            source_references=("https://example.org/crop",),
            effective_from=date(2026, 1, 1), effective_to=date(2026, 3, 31),
        )
        catalog = CropRegistry(metadata(), (retired,))
        self.assertEqual(catalog.resolve("old crop", on_date=date(2026, 2, 1)).status,
                         ResolutionStatus.MATCHED)
        self.assertEqual(catalog.resolve("old crop", on_date=date(2026, 4, 1)).status,
                         ResolutionStatus.UNSUPPORTED)
        self.assertEqual(catalog.resolve("not listed").status, ResolutionStatus.NOT_FOUND)

    def test_registry_version_and_invalid_records(self):
        limited_metadata = metadata(effective_to=date(2026, 6, 30))
        active_record = RICE.model_copy(update={"effective_from": date(2026, 1, 1),
                                                "effective_to": date(2026, 6, 30)})
        limited = CropRegistry(limited_metadata, (active_record,))
        self.assertEqual(limited.resolve("rice", on_date=date(2026, 7, 1)).status,
                         ResolutionStatus.UNSUPPORTED)
        with self.assertRaises(ValueError):
            CropRegistry(metadata(), (RICE, RICE))
        with self.assertRaises(ValidationError):
            RegistryMetadata(registry_id="bad", version="v1", effective_from=date(2026, 2, 1),
                             effective_to=date(2026, 1, 1),
                             source_reference="https://example.org", scope_note="bad period")
        with self.assertRaises(ValidationError):
            Resolution(status=ResolutionStatus.MATCHED)


class LocationRegistryTests(unittest.TestCase):
    def test_hierarchy_alias_normalization_and_optional_coordinates(self):
        self.assertEqual(LOCATION_REGISTRY.resolve("  TG  ", level=RegistryLevel.STATE).canonical_id,
                         "location:state:in-tg")
        self.assertEqual(LOCATION_REGISTRY.resolve(
            "Nalgonda", parent_id="location:state:in-tg", level=RegistryLevel.DISTRICT
        ).canonical_id, "location:district:in-tg-nalgonda")
        self.assertEqual(LOCATION_REGISTRY.get("location:district:in-tg-nalgonda").latitude, 17.0577)
        self.assertEqual(len(LOCATION_REGISTRY.children("location:state:in-tg")), 2)
        self.assertEqual(LOCATION_REGISTRY.resolve("Adilabad").canonical_id,
                         "location:district:in-tg-adilabad")
        self.assertEqual(LOCATION_REGISTRY.search("nalg", level=RegistryLevel.DISTRICT)[0].name,
                         "Nalgonda")

    def test_ambiguous_names_and_parent_filter(self):
        records = (
            LocationRecord(location_id="country", name="Example", level=RegistryLevel.COUNTRY,
                           source_reference="https://example.org/country"),
            LocationRecord(location_id="state-a", name="State A", level=RegistryLevel.STATE,
                           parent_id="country", source_reference="https://example.org/a"),
            LocationRecord(location_id="state-b", name="State B", level=RegistryLevel.STATE,
                           parent_id="country", source_reference="https://example.org/b"),
            LocationRecord(location_id="district-a", name="Springfield", level=RegistryLevel.DISTRICT,
                           parent_id="state-a", source_reference="https://example.org/a"),
            LocationRecord(location_id="district-b", name="Springfield", level=RegistryLevel.DISTRICT,
                           parent_id="state-b", source_reference="https://example.org/b"),
        )
        registry = LocationRegistry(metadata(), records)
        ambiguous = registry.resolve("Springfield", level=RegistryLevel.DISTRICT)
        self.assertEqual(ambiguous.status, ResolutionStatus.AMBIGUOUS)
        self.assertEqual(set(ambiguous.candidate_ids), {"district-a", "district-b"})
        self.assertEqual(registry.resolve("Springfield", parent_id="state-b").canonical_id,
                         "district-b")
        self.assertEqual(registry.resolve("Springfield", parent_id="state-c").status,
                         ResolutionStatus.UNSUPPORTED)

    def test_version_parent_and_location_field_validation(self):
        records = (LocationRecord(location_id="country", name="Example",
                                  level=RegistryLevel.COUNTRY,
                                  source_reference="https://example.org/country"),)
        historical = LocationRegistry(
            metadata("historical", effective_from=date(2025, 1, 1),
                     effective_to=date(2025, 12, 31)), records,
        )
        self.assertEqual(historical.resolve("Example", on_date=date(2026, 1, 1)).status,
                         ResolutionStatus.UNSUPPORTED)
        with self.assertRaises(ValueError):
            LocationRegistry(metadata(), (
                LocationRecord(location_id="bad", name="District", level=RegistryLevel.DISTRICT,
                               parent_id="unknown", source_reference="https://example.org/district"),
            ))
        with self.assertRaises(ValidationError):
            LocationRecord(location_id="partial-coordinates", name="Town", level=RegistryLevel.COUNTRY,
                           latitude=12.0, source_reference="https://example.org/town")
        with self.assertRaises(ValidationError):
            LocationRecord(location_id="bad-timezone", name="Town", level=RegistryLevel.COUNTRY,
                           timezone="Asia//Kolkata", source_reference="https://example.org/town")


class CoverageRegistryTests(unittest.TestCase):
    def test_missing_coverage_is_unsupported_not_crop_absence(self):
        coverage = CoverageRegistry(metadata("coverage"))
        self.assertEqual(coverage.assess("crop:oryza-sativa", "location:district:in-tg-nalgonda").status,
                         ResolutionStatus.UNSUPPORTED)
        self.assertEqual(CROP_REGISTRY.resolve("rice").status, ResolutionStatus.MATCHED)


if __name__ == "__main__":
    unittest.main()

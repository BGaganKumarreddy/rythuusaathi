"""Versioned, extensible crop and location registries with exact resolution."""

from __future__ import annotations

import unicodedata
from datetime import date
from enum import StrEnum
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RegistryLevel(StrEnum):
    COUNTRY = "country"
    STATE = "state"
    DISTRICT = "district"
    SUBDISTRICT = "subdistrict"
    VILLAGE = "village"


class CropCategory(StrEnum):
    CEREAL = "cereal"
    PULSE = "pulse"
    OILSEED = "oilseed"
    FIBRE = "fibre"
    FRUIT = "fruit"
    VEGETABLE = "vegetable"
    SPICE = "spice"
    OTHER = "other"


class ResolutionStatus(StrEnum):
    MATCHED = "matched"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    UNSUPPORTED = "unsupported"
    MISSING = "missing"


class RegistryMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    registry_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    effective_from: date
    effective_to: date | None = None
    source_reference: str = Field(min_length=1)
    scope_note: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid_period(self):
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("registry effective_to precedes effective_from")
        return self


class NameVariant(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    value: str = Field(min_length=1)
    language: str = "en"
    region_id: str | None = None

    @field_validator("value")
    @classmethod
    def trimmed_name(cls, value):
        if value != value.strip():
            raise ValueError("registry names must be trimmed")
        return value


class VarietyRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    variety_id: str = Field(min_length=1)
    names: tuple[NameVariant, ...] = Field(min_length=1)
    description: str | None = None
    source_reference: str = Field(min_length=1)
    applicable_region_ids: tuple[str, ...] | None = None


class GrowthStageRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    stage_id: str = Field(min_length=1)
    names: tuple[NameVariant, ...] = Field(min_length=1)
    order: int = Field(ge=0)
    source_reference: str = Field(min_length=1)


class CropRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    crop_id: str = Field(min_length=1)
    common_names: tuple[NameVariant, ...] = Field(min_length=1)
    scientific_name: str = Field(min_length=1)
    category: CropCategory
    regional_names: tuple[NameVariant, ...] = ()
    varieties: tuple[VarietyRecord, ...] = ()
    growth_stages: tuple[GrowthStageRecord, ...] = ()
    source_references: tuple[str, ...] = Field(min_length=1)
    effective_from: date
    effective_to: date | None = None
    metadata: tuple[tuple[str, str], ...] = ()

    @model_validator(mode="after")
    def validate_record(self):
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("crop effective_to precedes effective_from")
        variety_ids = [v.variety_id for v in self.varieties]
        if len(variety_ids) != len(set(variety_ids)):
            raise ValueError("variety IDs must be unique within a crop")
        stage_ids = [s.stage_id for s in self.growth_stages]
        if len(stage_ids) != len(set(stage_ids)):
            raise ValueError("growth stage IDs must be unique within a crop")
        return self


class LocationRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    location_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    level: RegistryLevel
    parent_id: str | None = None
    aliases: tuple[NameVariant, ...] = ()
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    timezone: str | None = None
    source_reference: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_location(self):
        if self.level is RegistryLevel.COUNTRY and self.parent_id is not None:
            raise ValueError("country records cannot have a parent")
        if self.level is not RegistryLevel.COUNTRY and not self.parent_id:
            raise ValueError("non-country records require a parent")
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be supplied together")
        if self.timezone and not re.fullmatch(
            r"(?:UTC|[A-Za-z_+-]+(?:/[A-Za-z0-9_+.-]+)+)", self.timezone
        ):
            raise ValueError("timezone must be a trimmed IANA-style name")
        return self


class Resolution(BaseModel):
    """Resolution state never substitutes a nearby or approximate record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: ResolutionStatus
    canonical_id: str | None = None
    candidate_ids: tuple[str, ...] = ()
    message: str | None = None

    @model_validator(mode="after")
    def consistent_result(self):
        if len(self.candidate_ids) != len(set(self.candidate_ids)):
            raise ValueError("resolution candidate IDs must be unique")
        if self.status is ResolutionStatus.MATCHED and not self.canonical_id:
            raise ValueError("matched resolution requires a canonical ID")
        if self.status is not ResolutionStatus.MATCHED and self.canonical_id is not None:
            raise ValueError("only matched resolutions may contain a canonical ID")
        if self.status is ResolutionStatus.AMBIGUOUS and len(self.candidate_ids) < 2:
            raise ValueError("ambiguous resolution requires at least two candidates")
        return self


class CropRegistry:
    """Read-only versioned crop registry; records can be extended by new releases."""

    def __init__(self, metadata: RegistryMetadata, records: tuple[CropRecord, ...]):
        self.metadata = metadata
        self.records = tuple(records)
        self._by_id = {record.crop_id: record for record in self.records}
        if len(self._by_id) != len(self.records):
            raise ValueError("crop IDs must be unique")
        self._validate_effective_windows()

    def get(self, crop_id: str, on_date: date | None = None) -> CropRecord | None:
        day = on_date or date.today()
        if not _active(self.metadata.effective_from, self.metadata.effective_to, day):
            return None
        record = self._by_id.get(crop_id)
        if record is None or not _active(record.effective_from, record.effective_to,
                                         day):
            return None
        return record

    def search(self, query: str, *, language: str | None = None,
               region_ids: tuple[str, ...] = (),
               on_date: date | None = None) -> tuple[CropRecord, ...]:
        term = _normalize(query)
        if not term:
            return ()
        day = on_date or date.today()
        if not _active(self.metadata.effective_from, self.metadata.effective_to, day):
            return ()
        matches = []
        for crop in self.records:
            if not _active(crop.effective_from, crop.effective_to, day):
                continue
            regional = tuple(name for name in crop.regional_names
                             if name.region_id is None or name.region_id in region_ids)
            names = crop.common_names + regional + (
                NameVariant(value=crop.scientific_name),
            )
            if any((language is None or item.language.casefold() == language.casefold())
                   and term in _normalize(item.value) for item in names):
                matches.append(crop)
        return tuple(matches)

    def resolve(self, query: str | None, *, language: str | None = None,
                region_ids: tuple[str, ...] = (),
                on_date: date | None = None) -> Resolution:
        if query is None or not query.strip():
            return Resolution(status=ResolutionStatus.MISSING, message="crop selection is missing")
        day = on_date or date.today()
        if not _active(self.metadata.effective_from, self.metadata.effective_to, day):
            return Resolution(status=ResolutionStatus.UNSUPPORTED,
                              message="crop registry version is not effective on this date")
        term = _normalize(query)
        if term.startswith("crop:") and term not in self._by_id:
            candidate = term.removeprefix("crop:").strip()
            if any(_normalize(c.scientific_name) == candidate or any(_normalize(n.value) == candidate for n in c.common_names + c.regional_names) for c in self.records):
                term = candidate
        exact = [crop for crop in self.records if (
            term == _normalize(crop.crop_id) or any(
            (language is None or name.language.casefold() == language.casefold())
            and term == _normalize(name.value)
            for name in crop.common_names + tuple(
                variant for variant in crop.regional_names
                if variant.region_id is None or variant.region_id in region_ids
            ) + (
                NameVariant(value=crop.scientific_name),
            )
        ))]
        active = [crop for crop in exact if _active(crop.effective_from, crop.effective_to, day)]
        if len(active) == 1:
            return Resolution(status=ResolutionStatus.MATCHED, canonical_id=active[0].crop_id)
        if len(active) > 1:
            return Resolution(status=ResolutionStatus.AMBIGUOUS,
                              candidate_ids=tuple(sorted(c.crop_id for c in active)))
        if exact:
            return Resolution(status=ResolutionStatus.UNSUPPORTED,
                              candidate_ids=tuple(sorted(c.crop_id for c in exact)),
                              message="crop entry is outside its effective period")
        partial = self.search(query, language=language, region_ids=region_ids, on_date=day)
        if partial:
            return Resolution(status=ResolutionStatus.AMBIGUOUS,
                              candidate_ids=tuple(sorted(c.crop_id for c in partial)),
                              message="selection is not an exact match")
        return Resolution(status=ResolutionStatus.NOT_FOUND,
                          message="crop is not present in this registry version")

    def resolve_variety(self, crop_id: str, query: str | None,
                        *, region_id: str | None = None,
                        region_ids: tuple[str, ...] = ()) -> Resolution:
        if query is None or not query.strip():
            return Resolution(status=ResolutionStatus.MISSING, message="variety selection is missing")
        crop = self._by_id.get(crop_id)
        if crop is None:
            return Resolution(status=ResolutionStatus.NOT_FOUND, message="crop ID is not registered")
        term = _normalize(query)
        selected_regions = set(region_ids)
        if region_id:
            selected_regions.add(region_id)
        matches = [variety for variety in crop.varieties if any(
            term == _normalize(name.value) or term == _normalize(variety.variety_id)
            for name in variety.names
        ) and (variety.applicable_region_ids is None or not selected_regions
               or bool(selected_regions.intersection(variety.applicable_region_ids)))]
        if len(matches) == 1:
            return Resolution(status=ResolutionStatus.MATCHED, canonical_id=matches[0].variety_id)
        if len(matches) > 1:
            return Resolution(status=ResolutionStatus.AMBIGUOUS,
                              candidate_ids=tuple(sorted(v.variety_id for v in matches)))
        elsewhere = [v for v in crop.varieties if (
            term == _normalize(v.variety_id)
            or any(term == _normalize(n.value) for n in v.names)
        )]
        if elsewhere:
            return Resolution(status=ResolutionStatus.UNSUPPORTED,
                              candidate_ids=tuple(v.variety_id for v in elsewhere),
                              message="variety is not listed for the selected region")
        return Resolution(status=ResolutionStatus.NOT_FOUND, message="variety is not registered")

    def _validate_effective_windows(self) -> None:
        for record in self.records:
            if record.effective_from < self.metadata.effective_from:
                raise ValueError("crop entry starts before its registry version")
            if (self.metadata.effective_to is not None
                    and (record.effective_to is None or record.effective_to > self.metadata.effective_to)):
                raise ValueError("crop entry extends beyond its registry version")


class LocationRegistry:
    """Hierarchical India-capable location catalog with optional coordinates."""

    _PARENT_LEVEL = {
        RegistryLevel.STATE: RegistryLevel.COUNTRY,
        RegistryLevel.DISTRICT: RegistryLevel.STATE,
        RegistryLevel.SUBDISTRICT: RegistryLevel.DISTRICT,
        RegistryLevel.VILLAGE: RegistryLevel.SUBDISTRICT,
    }

    def __init__(self, metadata: RegistryMetadata, records: tuple[LocationRecord, ...]):
        self.metadata = metadata
        self.records = tuple(records)
        self._by_id = {record.location_id: record for record in self.records}
        if len(self._by_id) != len(self.records):
            raise ValueError("location IDs must be unique")
        for record in self.records:
            expected = self._PARENT_LEVEL.get(record.level)
            if expected:
                parent = self._by_id.get(record.parent_id)
                if parent is None or parent.level is not expected:
                    raise ValueError(f"invalid parent for {record.location_id}")

    def get(self, location_id: str, on_date: date | None = None) -> LocationRecord | None:
        day = on_date or date.today()
        if not _active(self.metadata.effective_from, self.metadata.effective_to, day):
            return None
        return self._by_id.get(location_id)

    def ancestors(self, location_id: str) -> tuple[LocationRecord, ...]:
        """Return the exact location and its parents, from nearest to farthest."""
        current = self._by_id.get(location_id)
        path = []
        while current is not None:
            path.append(current)
            current = self._by_id.get(current.parent_id) if current.parent_id else None
        return tuple(path)

    def children(self, parent_id: str, level: RegistryLevel | None = None,
                 on_date: date | None = None) -> tuple[LocationRecord, ...]:
        day = on_date or date.today()
        if not _active(self.metadata.effective_from, self.metadata.effective_to, day):
            return ()
        return tuple(record for record in self.records
                     if record.parent_id == parent_id and (level is None or record.level is level))

    def search(self, query: str, *, parent_id: str | None = None,
               level: RegistryLevel | None = None,
               on_date: date | None = None) -> tuple[LocationRecord, ...]:
        term = _normalize(query)
        day = on_date or date.today()
        if not term or not _active(self.metadata.effective_from, self.metadata.effective_to, day):
            return ()
        result = []
        for record in self.records:
            if level is not None and record.level is not level:
                continue
            if parent_id is not None and record.parent_id != parent_id:
                continue
            names = (record.name,) + tuple(alias.value for alias in record.aliases)
            if any(term in _normalize(name) for name in names):
                result.append(record)
        return tuple(result)

    def resolve(self, query: str | None, *, parent_id: str | None = None,
                level: RegistryLevel | None = None,
                on_date: date | None = None) -> Resolution:
        if query is None or not query.strip():
            return Resolution(status=ResolutionStatus.MISSING, message="location selection is missing")
        day = on_date or date.today()
        if not _active(self.metadata.effective_from, self.metadata.effective_to, day):
            return Resolution(status=ResolutionStatus.UNSUPPORTED,
                              message="location registry version is not effective on this date")
        term = _normalize(query)
        if term.startswith("loc:") and term not in self._by_id:
            candidate = term.removeprefix("loc:").strip()
            if any(_normalize(record.name) == candidate or any(_normalize(a.value) == candidate for a in record.aliases) for record in self.records):
                term = candidate
        elif term.startswith("location:") and term not in self._by_id:
            candidate = term.removeprefix("location:").strip()
            if any(_normalize(record.name) == candidate or any(_normalize(a.value) == candidate for a in record.aliases) for record in self.records):
                term = candidate
        all_exact = [record for record in self.records if term in {
            _normalize(record.location_id), _normalize(record.name),
            *(_normalize(alias.value) for alias in record.aliases),
        }]
        filtered = [record for record in all_exact
                    if (parent_id is None or record.parent_id == parent_id)
                    and (level is None or record.level is level)]
        if len(filtered) == 1:
            return Resolution(status=ResolutionStatus.MATCHED, canonical_id=filtered[0].location_id)
        if len(filtered) > 1:
            return Resolution(status=ResolutionStatus.AMBIGUOUS,
                              candidate_ids=tuple(sorted(r.location_id for r in filtered)))
        if all_exact:
            return Resolution(status=ResolutionStatus.UNSUPPORTED,
                              candidate_ids=tuple(sorted(r.location_id for r in all_exact)),
                              message="location does not match the supplied parent or level")
        partial = self.search(query, parent_id=parent_id, level=level, on_date=day)
        if partial:
            return Resolution(status=ResolutionStatus.AMBIGUOUS,
                              candidate_ids=tuple(sorted(r.location_id for r in partial)),
                              message="selection is not an exact match")
        return Resolution(status=ResolutionStatus.NOT_FOUND,
                          message="location is not present in this registry version")


class CoverageRegistry:
    """Explicit crop/location support pairs; absent coverage is unsupported coverage."""

    def __init__(self, metadata: RegistryMetadata, supported_pairs: tuple[tuple[str, str], ...] = ()):
        self.metadata = metadata
        self._supported = frozenset(supported_pairs)
        if len(self._supported) != len(supported_pairs):
            raise ValueError("coverage pairs must be unique")

    def assess(self, crop_id: str | None, location_id: str | None,
               on_date: date | None = None) -> Resolution:
        if not crop_id or not location_id:
            return Resolution(status=ResolutionStatus.MISSING,
                              message="crop and location must be resolved before coverage")
        day = on_date or date.today()
        if not _active(self.metadata.effective_from, self.metadata.effective_to, day):
            return Resolution(status=ResolutionStatus.UNSUPPORTED,
                              message="coverage registry version is not effective on this date")
        pair = (crop_id, location_id)
        if pair in self._supported:
            return Resolution(status=ResolutionStatus.MATCHED, canonical_id=f"{crop_id}@{location_id}")
        return Resolution(status=ResolutionStatus.UNSUPPORTED,
                          message="no validated system coverage is registered for this crop/location pair")


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _active(start: date, end: date | None, day: date) -> bool:
    return start <= day and (end is None or day <= end)

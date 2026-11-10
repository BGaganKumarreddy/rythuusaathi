"""Exact farmer-selection resolution into canonical shared workflow context."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import (
    ContextFact,
    EntityReference,
    EnvironmentalObservation,
    FactState,
    IrrigationInformation,
    MissingInformation,
    MissingInformationReason,
    RequestContext,
    SoilInformation,
    WorkflowMetadata,
)
from .registry import (
    CoverageRegistry,
    CropRegistry,
    LocationRegistry,
    RegistryLevel,
    Resolution,
    ResolutionStatus,
)


class ContextResolutionRequest(BaseModel):
    """Raw selections plus already supplied facts collected during intake."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    location_query: str | None = None
    location_parent_id: str | None = None
    location_level: RegistryLevel | None = None
    crop_query: str | None = None
    variety_query: str | None = None
    growth_stage_query: str | None = None
    sowing_date: ContextFact[date] = Field(default_factory=lambda: ContextFact[date].unknown())
    irrigation: ContextFact[IrrigationInformation] = Field(
        default_factory=lambda: ContextFact[IrrigationInformation].unknown()
    )
    soil: ContextFact[SoilInformation] = Field(
        default_factory=lambda: ContextFact[SoilInformation].unknown()
    )
    farmer_report: ContextFact[tuple[str, ...]] = Field(
        default_factory=lambda: ContextFact[tuple[str, ...]].unknown()
    )
    language_preference: ContextFact[str] = Field(
        default_factory=lambda: ContextFact[str].unknown()
    )
    environmental_context: ContextFact[tuple[EnvironmentalObservation, ...]] = Field(
        default_factory=lambda: ContextFact[tuple[EnvironmentalObservation, ...]].unknown()
    )
    workflow: WorkflowMetadata = Field(default_factory=WorkflowMetadata)
    as_of: date = Field(default_factory=date.today)


class ContextResolutionResult(BaseModel):
    """Resolutions keep registry status separate from the usable context."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    context: RequestContext
    location: Resolution
    crop: Resolution
    variety: Resolution
    growth_stage: Resolution
    coverage: Resolution

    @model_validator(mode="after")
    def context_matches_resolutions(self):
        entities = (
            (self.location, self.context.location),
            (self.crop, self.context.crop),
            (self.variety, self.context.crop_variety),
        )
        for result, fact in entities:
            if result.status is ResolutionStatus.MATCHED:
                if fact.state is not FactState.SUPPLIED or fact.value.canonical_id != result.canonical_id:
                    raise ValueError("resolved entity and canonical context do not agree")
            elif fact.state is FactState.SUPPLIED:
                raise ValueError("unresolved entity cannot be supplied to workflow context")
        if self.growth_stage.status is ResolutionStatus.MATCHED:
            if (self.context.growth_stage.state is not FactState.SUPPLIED
                    or self.context.growth_stage.value != self.growth_stage.canonical_id):
                raise ValueError("resolved growth stage and context do not agree")
        elif self.context.growth_stage.state is FactState.SUPPLIED:
            raise ValueError("unresolved growth stage cannot be supplied to workflow context")
        if self.coverage.status is ResolutionStatus.MATCHED and not (
            self.crop.status is ResolutionStatus.MATCHED
            and self.location.status is ResolutionStatus.MATCHED
        ):
            raise ValueError("coverage cannot be matched without canonical crop and location")
        return self

    @property
    def can_proceed_with_local_advice(self) -> bool:
        return all(result.status is ResolutionStatus.MATCHED for result in (
            self.location, self.crop, self.coverage
        ))


class ContextResolver:
    """Resolve only exact registry matches; never guess or substitute nearby values."""

    def __init__(self, crops: CropRegistry, locations: LocationRegistry,
                 coverage: CoverageRegistry):
        self._crops = crops
        self._locations = locations
        self._coverage = coverage

    def resolve(self, request: ContextResolutionRequest) -> ContextResolutionResult:
        location_result = self._locations.resolve(
            request.location_query,
            parent_id=request.location_parent_id,
            level=request.location_level,
            on_date=request.as_of,
        )
        region_ids = tuple(
            item.location_id for item in self._locations.ancestors(location_result.canonical_id)
        ) if location_result.status is ResolutionStatus.MATCHED else ()
        crop_result = self._crops.resolve(
            request.crop_query, region_ids=region_ids, on_date=request.as_of
        )

        location_record = (self._locations.get(location_result.canonical_id, request.as_of)
                           if location_result.canonical_id else None)
        crop_record = (self._crops.get(crop_result.canonical_id, request.as_of)
                       if crop_result.canonical_id else None)

        location_fact = _entity_fact(request.location_query, location_result,
                                     location_record.location_id if location_record else None,
                                     location_record.name if location_record else None)
        crop_fact = _entity_fact(request.crop_query, crop_result,
                                 crop_record.crop_id if crop_record else None,
                                 crop_record.common_names[0].value if crop_record else None)

        if request.variety_query is None or not request.variety_query.strip():
            variety_result = Resolution(status=ResolutionStatus.MISSING,
                                        message="variety selection is missing")
        elif crop_record is None:
            variety_result = Resolution(status=ResolutionStatus.UNSUPPORTED,
                                        message="crop must be resolved before its variety")
        else:
            variety_result = self._crops.resolve_variety(
                crop_record.crop_id, request.variety_query, region_ids=region_ids
            )
        variety_record = None
        if crop_record and variety_result.canonical_id:
            variety_record = next((item for item in crop_record.varieties
                                   if item.variety_id == variety_result.canonical_id), None)
        variety_fact = _entity_fact(request.variety_query, variety_result,
                                    variety_record.variety_id if variety_record else None,
                                    variety_record.names[0].value if variety_record else None)

        stage_result, stage_fact = self._resolve_stage(crop_record, request.growth_stage_query)
        coverage_result = self._coverage.assess(
            crop_result.canonical_id if crop_result.status is ResolutionStatus.MATCHED else None,
            location_result.canonical_id if location_result.status is ResolutionStatus.MATCHED else None,
            on_date=request.as_of,
        )

        missing = list(_selection_missing("location", "Please clarify your location.",
                                          request.location_query, location_result,
                                          required=True))
        missing.extend(_selection_missing("crop", "Please clarify which crop you are growing.",
                                          request.crop_query, crop_result,
                                          required=True))
        missing.extend(_selection_missing("crop_variety", "Please clarify the crop variety.",
                                          request.variety_query, variety_result))
        missing.extend(_selection_missing("growth_stage", "Please clarify the crop growth stage.",
                                          request.growth_stage_query, stage_result))
        if coverage_result.status is ResolutionStatus.UNSUPPORTED:
            missing.append(MissingInformation(
                field_name="crop_location_coverage",
                question="There is no validated local coverage for this crop and location combination.",
                reason=MissingInformationReason.UNSUPPORTED,
            ))

        context = RequestContext(
            request_id=request.request_id,
            user_id=request.user_id,
            tenant_id=request.tenant_id,
            location=location_fact,
            crop=crop_fact,
            crop_variety=variety_fact,
            growth_stage=stage_fact,
            sowing_date=request.sowing_date,
            irrigation=request.irrigation,
            soil=request.soil,
            farmer_report=request.farmer_report,
            language_preference=request.language_preference,
            environmental_context=request.environmental_context,
            missing_information=tuple(missing),
            workflow=request.workflow,
        )
        return ContextResolutionResult(
            context=context,
            location=location_result,
            crop=crop_result,
            variety=variety_result,
            growth_stage=stage_result,
            coverage=coverage_result,
        )

    def _resolve_stage(self, crop_record, query: str | None) -> tuple[Resolution, ContextFact[str]]:
        if query is None or not query.strip():
            result = Resolution(status=ResolutionStatus.MISSING,
                               message="growth stage selection is missing")
            return result, ContextFact[str].unknown()
        if crop_record is None:
            result = Resolution(status=ResolutionStatus.UNSUPPORTED,
                               message="crop must be resolved before its growth stage")
            return result, ContextFact[str].unavailable(result.message)
        term = _normalize(query)
        matches = [stage for stage in crop_record.growth_stages
                   if any(term == _normalize(name.value) for name in stage.names)]
        if len(matches) == 1:
            stage = matches[0]
            result = Resolution(status=ResolutionStatus.MATCHED, canonical_id=stage.stage_id)
            return result, ContextFact[str].supplied(stage.stage_id)
        if len(matches) > 1:
            result = Resolution(status=ResolutionStatus.AMBIGUOUS,
                               candidate_ids=tuple(sorted(stage.stage_id for stage in matches)))
            return result, ContextFact[str].unavailable("growth stage is ambiguous")
        result = Resolution(status=ResolutionStatus.NOT_FOUND,
                           message="growth stage is not registered for this crop")
        return result, ContextFact[str].unavailable(result.message)


def _entity_fact(query: str | None, resolution: Resolution, canonical_id: str | None,
                 display_name: str | None) -> ContextFact[EntityReference]:
    if resolution.status is ResolutionStatus.MATCHED and canonical_id and display_name:
        return ContextFact[EntityReference].supplied(
            EntityReference(canonical_id=canonical_id, display_name=display_name)
        )
    if query is None or not query.strip():
        return ContextFact[EntityReference].unknown()
    return ContextFact[EntityReference].unavailable(
        resolution.message or f"selection could not be resolved ({resolution.status.value})"
    )


def _selection_missing(field_name: str, question: str, query: str | None,
                       result: Resolution, *, required: bool = False) -> tuple[MissingInformation, ...]:
    if result.status is ResolutionStatus.MATCHED:
        return ()
    if result.status is ResolutionStatus.MISSING:
        if not required and (query is None or not query.strip()):
            return ()
        return (MissingInformation(field_name=field_name, question=question),)
    reason = (MissingInformationReason.AMBIGUOUS
              if result.status is ResolutionStatus.AMBIGUOUS
              else MissingInformationReason.UNSUPPORTED)
    return (MissingInformation(field_name=field_name, question=question, reason=reason),)


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())

"""Typed, request-scoped agricultural context contracts."""

from __future__ import annotations

import math
from datetime import date, datetime
from enum import StrEnum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

T = TypeVar("T")


class FactState(StrEnum):
    SUPPLIED = "supplied"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"


class ContextFact(BaseModel, Generic[T]):
    """A value plus an explicit distinction between missing and unavailable."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: FactState
    value: T | None = None
    detail: str | None = None

    @model_validator(mode="after")
    def validate_state_value(self):
        if self.state is FactState.SUPPLIED and self.value is None:
            raise ValueError("a supplied fact requires a value")
        if self.state is not FactState.SUPPLIED and self.value is not None:
            raise ValueError("unknown or unavailable facts cannot carry a value")
        if self.state is FactState.UNAVAILABLE and not (self.detail and self.detail.strip()):
            raise ValueError("an unavailable fact requires a reason")
        return self

    @classmethod
    def supplied(cls, value: T) -> "ContextFact[T]":
        return cls(state=FactState.SUPPLIED, value=value)

    @classmethod
    def unknown(cls, detail: str | None = None) -> "ContextFact[T]":
        return cls(state=FactState.UNKNOWN, detail=detail)

    @classmethod
    def unavailable(cls, reason: str) -> "ContextFact[T]":
        return cls(state=FactState.UNAVAILABLE, detail=reason)


class EntityReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    canonical_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)

    @field_validator("canonical_id", "display_name")
    @classmethod
    def trim_identifiers(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("reference fields must be trimmed")
        return value


class SoilInformation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    texture: ContextFact[str] = Field(default_factory=lambda: ContextFact[str].unknown())
    ph: ContextFact[float] = Field(default_factory=lambda: ContextFact[float].unknown())
    nitrogen_mg_kg: ContextFact[float] = Field(default_factory=lambda: ContextFact[float].unknown())
    phosphorus_mg_kg: ContextFact[float] = Field(default_factory=lambda: ContextFact[float].unknown())
    potassium_mg_kg: ContextFact[float] = Field(default_factory=lambda: ContextFact[float].unknown())

    @model_validator(mode="after")
    def validate_soil_values(self):
        numeric_values = (self.ph, self.nitrogen_mg_kg,
                          self.phosphorus_mg_kg, self.potassium_mg_kg)
        for fact in numeric_values:
            if fact.state is FactState.SUPPLIED and not math.isfinite(fact.value):
                raise ValueError("supplied soil measurements must be finite")
        if self.ph.state is FactState.SUPPLIED and not 0 <= self.ph.value <= 14:
            raise ValueError("soil pH must be between 0 and 14")
        for value in (self.nitrogen_mg_kg, self.phosphorus_mg_kg, self.potassium_mg_kg):
            if value.state is FactState.SUPPLIED and value.value < 0:
                raise ValueError("soil nutrient values cannot be negative")
        return self


class IrrigationInformation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    method: ContextFact[str] = Field(default_factory=lambda: ContextFact[str].unknown())
    water_availability: ContextFact[str] = Field(default_factory=lambda: ContextFact[str].unknown())
    interval_days: ContextFact[int] = Field(default_factory=lambda: ContextFact[int].unknown())

    @model_validator(mode="after")
    def validate_interval(self):
        if self.interval_days.state is FactState.SUPPLIED and self.interval_days.value < 1:
            raise ValueError("irrigation interval must be positive")
        return self


class EnvironmentalObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    value: float | str
    unit: str | None = None
    observed_at: datetime | None = None
    evidence_id: str | None = None

    @field_validator("value")
    @classmethod
    def finite_numeric_values(cls, value):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("environmental values must be finite")
        return value

    @field_validator("name")
    @classmethod
    def trimmed_name(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("observation names must be trimmed")
        return value

    @field_validator("observed_at")
    @classmethod
    def timezone_aware_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("observation timestamps must be timezone-aware")
        return value


class MissingInformationReason(StrEnum):
    NOT_PROVIDED = "not_provided"
    AMBIGUOUS = "ambiguous"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"


class MissingInformation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    field_name: str = Field(min_length=1)
    question: str = Field(min_length=1)
    reason: MissingInformationReason = MissingInformationReason.NOT_PROVIDED


class WorkflowMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: str = "intake"
    attempt: int = Field(default=1, ge=1)
    trace_id: str | None = None
    labels: tuple[tuple[str, str], ...] = ()

    @field_validator("labels")
    @classmethod
    def unique_label_keys(cls, labels):
        keys = [key for key, _ in labels]
        if any(not key or key != key.strip() for key in keys) or len(keys) != len(set(keys)):
            raise ValueError("workflow label keys must be unique, non-empty and trimmed")
        return labels


class RequestContext(BaseModel):
    """Canonical farmer request context shared by downstream workflow agents."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    location: ContextFact[EntityReference] = Field(
        default_factory=lambda: ContextFact[EntityReference].unknown()
    )
    crop: ContextFact[EntityReference] = Field(
        default_factory=lambda: ContextFact[EntityReference].unknown()
    )
    crop_variety: ContextFact[EntityReference] = Field(
        default_factory=lambda: ContextFact[EntityReference].unknown()
    )
    growth_stage: ContextFact[str] = Field(default_factory=lambda: ContextFact[str].unknown())
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
    language_preference: ContextFact[str] = Field(default_factory=lambda: ContextFact[str].unknown())
    environmental_context: ContextFact[tuple[EnvironmentalObservation, ...]] = Field(
        default_factory=lambda: ContextFact[tuple[EnvironmentalObservation, ...]].unknown()
    )
    missing_information: tuple[MissingInformation, ...] = ()
    workflow: WorkflowMetadata = Field(default_factory=WorkflowMetadata)

    @field_validator("request_id", "user_id", "tenant_id")
    @classmethod
    def trimmed_required_references(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("request and identity references must be trimmed")
        return value

    @field_validator("farmer_report")
    @classmethod
    def reject_empty_report_items(cls, fact):
        if fact.state is FactState.SUPPLIED and any(not item.strip() for item in fact.value):
            raise ValueError("farmer report items cannot be empty")
        return fact

    @field_validator("growth_stage", "language_preference")
    @classmethod
    def reject_blank_supplied_text(cls, fact):
        if fact.state is FactState.SUPPLIED and not fact.value.strip():
            raise ValueError("supplied text facts cannot be blank")
        return fact

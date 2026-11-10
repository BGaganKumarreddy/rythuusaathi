"""Structured, evidence-qualified agricultural recommendations."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RecommendationCategory(StrEnum):
    CROP_MANAGEMENT = "crop_management"
    WATER_MANAGEMENT = "water_management"
    NUTRIENT_MANAGEMENT = "nutrient_management"
    PEST_OR_DISEASE = "pest_or_disease"
    MARKET = "market"
    GOVERNMENT_SCHEME = "government_scheme"
    FOLLOW_UP = "follow_up"


class RecommendationSupport(StrEnum):
    EVIDENCE_BACKED = "evidence_backed"
    HYPOTHESIS = "hypothesis"
    NEEDS_INFORMATION = "needs_information"


class AgriculturalRecommendation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    recommendation_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    category: RecommendationCategory
    proposed_action: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = ()
    reasoning_summary: str = Field(min_length=1)
    support: RecommendationSupport
    confidence: float = Field(ge=0, le=1)
    uncertainty: str | None = None
    missing_information: tuple[str, ...] = ()
    applicability_conditions: tuple[str, ...] = ()
    potential_risks: tuple[str, ...] = ()
    recommended_follow_up: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_support_level(self):
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("evidence_ids must be unique")
        if self.support is RecommendationSupport.EVIDENCE_BACKED and not self.evidence_ids:
            raise ValueError("evidence-backed recommendations require evidence")
        if self.support is RecommendationSupport.HYPOTHESIS and not (
            self.uncertainty and self.uncertainty.strip()
        ):
            raise ValueError("hypotheses must state their uncertainty")
        if self.support is RecommendationSupport.NEEDS_INFORMATION and not self.missing_information:
            raise ValueError("recommendations needing information must name it")
        return self

    @field_validator("evidence_ids", "missing_information", "applicability_conditions",
                     "potential_risks", "recommended_follow_up")
    @classmethod
    def reject_blank_items(cls, values):
        if any(not value or value != value.strip() for value in values):
            raise ValueError("list values must be non-empty and trimmed")
        return values


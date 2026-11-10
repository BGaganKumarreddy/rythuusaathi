"""Validated source document models."""
from __future__ import annotations
from datetime import date, datetime
from enum import StrEnum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .adapters import DocumentMetadata, SourceReliability, SourceType


class KnowledgeTopic(StrEnum):
    CULTIVATION = "crop_cultivation"
    SOWING = "sowing_and_planting"
    IRRIGATION = "irrigation"
    FERTILIZER = "fertilizer_management"
    SOIL = "soil_preparation"
    GROWTH = "growth_stages"
    PEST = "pest_management"
    DISEASE = "disease_identification"
    NUTRIENT = "nutrient_deficiencies"
    HARVEST = "harvesting_postharvest"
    SCHEME = "government_scheme"


class AgriculturalDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    document_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    crop_id: str = Field(min_length=1)
    variety_id: str | None = None
    growth_stage: str | None = None
    topic: KnowledgeTopic
    geographic_applicability: tuple[str, ...] | None = None
    content: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_identity: str = Field(min_length=1)
    source_reference: str = Field(min_length=1)
    publisher: str | None = None
    publication_date: date | None = None
    revision: str | None = None
    revised_at: date | None = None
    section: str | None = None
    source_type: SourceType = SourceType.CURATED
    reliability: SourceReliability = SourceReliability.CURATED

    @field_validator("content")
    @classmethod
    def nonblank_content(cls, value):
        if not value.strip(): raise ValueError("content cannot be blank")
        return value.strip()


class SchemeDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    document_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_identity: str = Field(min_length=1)
    source_reference: str = Field(min_length=1)
    publisher: str = Field(min_length=1)
    jurisdiction: tuple[str, ...] = Field(min_length=1)
    jurisdiction_level: Literal["central", "state"]
    official: bool
    eligibility_text: str | None = None
    required_documents_text: str | None = None
    benefits_text: str | None = None
    limitations_text: str | None = None
    procedure_text: str | None = None
    publication_date: date | None = None
    revision: str | None = None
    revised_at: date | None = None
    retrieved_at: datetime | None = None
    section: str | None = None
    source_type: SourceType = SourceType.GOVERNMENT

    @model_validator(mode="after")
    def official_classification(self):
        if self.official and self.source_type is not SourceType.GOVERNMENT:
            raise ValueError("official documents must have government source type")
        if not self.official and self.source_type is SourceType.GOVERNMENT:
            raise ValueError("secondary explanations cannot be labeled government")
        return self


class DocumentEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    metadata: DocumentMetadata
    content: str = Field(min_length=1)

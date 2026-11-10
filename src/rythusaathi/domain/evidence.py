"""Provenance-rich agricultural evidence contracts."""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import StrEnum
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EvidenceSourceType(StrEnum):
    PRIMARY_GOVERNMENT = "primary_government"
    CURATED_AGRICULTURAL_KNOWLEDGE = "curated_agricultural_knowledge"
    EXTERNAL_DATA = "external_data"


class EvidenceMode(StrEnum):
    LIVE = "live"
    CACHED = "cached"
    HISTORICAL = "historical"
    FIXTURE = "fixture"


class EvidenceStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


class ReferenceKind(StrEnum):
    URL = "url"
    DOCUMENT_ID = "document_id"


class ContentReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ReferenceKind
    value: str = Field(min_length=1)

    @field_validator("value")
    @classmethod
    def valid_reference(cls, value, info):
        if value != value.strip():
            raise ValueError("references must be trimmed")
        if info.data.get("kind") is ReferenceKind.URL:
            parsed = urlparse(value)
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError("evidence URLs must use HTTPS")
        return value


class EvidenceSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(min_length=1)
    source_identity: str = Field(min_length=1)
    source_type: EvidenceSourceType
    title: str = Field(min_length=1)
    reference: ContentReference
    publisher: str | None = None


class EvidenceRecord(BaseModel):
    """A claim-supporting content record with explicit scope and provenance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    source: EvidenceSource
    excerpt: str | None = None
    content_reference: ContentReference | None = None
    retrieved_at: datetime
    publication_date: date | None = None
    geographic_applicability: tuple[str, ...] | None = None
    crop_applicability: tuple[str, ...] | None = None
    provenance: str = Field(min_length=1)
    mode: EvidenceMode
    status: EvidenceStatus
    confidence: float | None = Field(default=None, ge=0, le=1)
    relevance: float | None = Field(default=None, ge=0, le=1)
    units: str | None = None
    page_or_section: str | None = None
    quality_notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_evidence(self):
        if self.retrieved_at.tzinfo is None or self.retrieved_at.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware")
        if self.status is EvidenceStatus.AVAILABLE and not (
            (self.excerpt and self.excerpt.strip()) or self.content_reference
        ):
            raise ValueError("available evidence requires an excerpt or content reference")
        if self.publication_date and self.publication_date > self.retrieved_at.astimezone(timezone.utc).date():
            raise ValueError("publication_date cannot follow retrieval")
        for field_name in ("geographic_applicability", "crop_applicability"):
            values = getattr(self, field_name)
            if values is not None and (any(not item or item != item.strip() for item in values)
                                       or len(values) != len(set(values))):
                raise ValueError(f"{field_name} must contain unique trimmed identifiers")
        return self


"""Provider-neutral source adapter contracts."""
from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable
from pydantic import BaseModel, ConfigDict, Field, field_validator


class SourceType(StrEnum):
    CURATED = "curated_agricultural_knowledge"
    GOVERNMENT = "primary_government"
    SECONDARY = "secondary_explanation"
    EXTERNAL = "external_data"


class SourceReliability(StrEnum):
    PRIMARY = "primary"
    CURATED = "curated"
    SECONDARY = "secondary"
    UNVERIFIED = "unverified"


class DocumentMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    document_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source_type: SourceType
    reliability: SourceReliability
    source_identity: str = Field(min_length=1)
    reference: str = Field(min_length=1)
    publisher: str | None = None
    publication_date: date | None = None
    revision: str | None = None
    revised_at: date | None = None
    retrieved_at: datetime | None = None
    jurisdiction: tuple[str, ...] | None = None
    jurisdiction_level: str | None = None

    @field_validator("retrieved_at")
    @classmethod
    def aware_retrieved_at(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("retrieved_at must be timezone-aware")
        return value


class RetrievalError(RuntimeError):
    """A source failed to return a valid document."""


@runtime_checkable
class SourceAdapter(Protocol):
    """Minimal adapter interface shared by curated, official and external sources."""
    @property
    def source_id(self) -> str: ...

    def list_documents(self) -> tuple[DocumentMetadata, ...]: ...

    def get_content(self, document_id: str, revision: str | None = None) -> str: ...

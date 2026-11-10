"""Deterministic parsing, normalization, hashing and source-aware chunking."""
from __future__ import annotations
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from datetime import datetime, timezone
from .adapters import DocumentMetadata
from .models import AgriculturalDocument, SchemeDocument


@dataclass(frozen=True)
class DocumentChunk:
    chunk_id: str
    parent_document_id: str
    source: DocumentMetadata
    text: str
    content_hash: str
    crop_id: str | None = None
    variety_id: str | None = None
    growth_stage: str | None = None
    topic: str | None = None
    geographic_applicability: tuple[str, ...] | None = None
    page: int | None = None
    section: str | None = None


def normalize_text(text: str) -> str:
    text = text.replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(re.sub(r"[\t\f\v ]+", " ", line).strip() for line in text.splitlines()).strip()


class DocumentProcessor:
    def __init__(self, chunk_size: int = 900, overlap: int = 120):
        if chunk_size < 1 or overlap < 0 or overlap >= chunk_size:
            raise ValueError("require chunk_size > overlap >= 0")
        self.chunk_size, self.overlap = chunk_size, overlap

    def extract_pdf(self, path: str | Path) -> tuple[tuple[int, str], ...]:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("PDF parsing requires optional dependency pypdf") from exc
        try:
            reader = PdfReader(str(path))
            return tuple((i + 1, normalize_text(page.extract_text() or ""))
                         for i, page in enumerate(reader.pages))
        except Exception as exc:
            raise ValueError(f"unable to parse PDF: {exc}") from exc

    def extract_pdf_metadata(self, path: str | Path) -> dict[str, str]:
        """Return raw embedded PDF metadata without treating it as publication data."""
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("PDF parsing requires optional dependency pypdf") from exc
        try:
            metadata = PdfReader(str(path)).metadata
            if metadata is None:
                return {}
            return {
                str(key): str(value)
                for key, value in sorted(metadata.items(), key=lambda item: str(item[0]))
                if value is not None
            }
        except Exception as exc:
            raise ValueError(f"unable to parse PDF metadata: {exc}") from exc

    def process_text(self, metadata: DocumentMetadata, text: str, *, crop_id=None,
                     variety_id=None, growth_stage=None, topic=None, geography=None,
                     section=None, page=None) -> tuple[DocumentChunk, ...]:
        normalized = normalize_text(text)
        if not normalized: return ()
        stable_source = metadata.model_dump(mode="json")
        stable_source.pop("retrieved_at", None)
        source_fingerprint = hashlib.sha256(json.dumps(
            stable_source, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        chunks, start = [], 0
        while start < len(normalized):
            end = min(start + self.chunk_size, len(normalized))
            if end < len(normalized):
                boundary = normalized.rfind(" ", start, end)
                if boundary > start: end = boundary
            piece = normalized[start:end].strip()
            if piece:
                digest = hashlib.sha256(piece.encode("utf-8")).hexdigest()
                identity = (f"{metadata.document_id}:{metadata.revision or 'unversioned'}:"
                            f"{source_fingerprint}:{page or 0}:{start}:{digest}")
                chunk_id = "chunk:" + hashlib.sha256(identity.encode()).hexdigest()[:24]
                chunks.append(DocumentChunk(chunk_id, metadata.document_id, metadata, piece,
                    digest, crop_id, variety_id, growth_stage, topic, geography, page, section))
            if end >= len(normalized): break
            start = max(start + 1, end - self.overlap)
        return tuple(chunks)

    def process_curated(self, doc: AgriculturalDocument):
        meta = DocumentMetadata(document_id=doc.document_id, source_id=doc.source_id,
            title=doc.title, source_type=doc.source_type, reliability=doc.reliability,
            source_identity=doc.source_identity, reference=doc.source_reference,
            publisher=doc.publisher, publication_date=doc.publication_date,
            revision=doc.revision, revised_at=doc.revised_at)
        return self.process_text(meta, doc.content, crop_id=doc.crop_id, variety_id=doc.variety_id,
            growth_stage=doc.growth_stage, topic=doc.topic.value,
            geography=doc.geographic_applicability, section=doc.section)

    def process_scheme(self, doc: SchemeDocument):
        meta = DocumentMetadata(document_id=doc.document_id, source_id=doc.source_id,
            title=doc.title, source_type=doc.source_type,
            reliability="primary" if doc.official else "secondary",
            source_identity=doc.source_identity, reference=doc.source_reference,
            publisher=doc.publisher, publication_date=doc.publication_date,
            revision=doc.revision, revised_at=doc.revised_at,
            retrieved_at=doc.retrieved_at or datetime.now(timezone.utc),
            jurisdiction=doc.jurisdiction, jurisdiction_level=doc.jurisdiction_level)
        sections = (
            ("Scheme summary", doc.content),
            ("Eligibility", doc.eligibility_text),
            ("Required documents and records", doc.required_documents_text),
            ("Benefits", doc.benefits_text),
            ("Limitations", doc.limitations_text),
            ("Procedure", doc.procedure_text),
        )
        content = "\n\n".join(f"{label}: {text}" for label, text in sections if text)
        return self.process_text(meta, content, topic="government_scheme",
            geography=doc.jurisdiction, section=doc.section)

    def process_pdf(self, metadata: DocumentMetadata, path: str | Path, **scope):
        chunks = []
        for page, text in self.extract_pdf(path):
            chunks.extend(self.process_text(metadata, text, page=page, **scope))
        return tuple(chunks)

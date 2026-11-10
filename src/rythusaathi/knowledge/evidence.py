"""Map retrieval results onto frozen Phase 2 evidence contracts."""
from datetime import datetime
from ..domain.evidence import (ContentReference, EvidenceMode, EvidenceRecord,
    EvidenceSource, EvidenceSourceType, EvidenceStatus, ReferenceKind)
from .retrieval import RetrievedChunk


def to_evidence_record(result: RetrievedChunk, *, request_id: str) -> EvidenceRecord:
    chunk = result.chunk
    source_type = (EvidenceSourceType.EXTERNAL_DATA
                   if chunk.source.source_type.value == "secondary_explanation"
                   else EvidenceSourceType(chunk.source.source_type.value))
    reference_kind = (ReferenceKind.URL if chunk.source.reference.startswith("https://")
                      else ReferenceKind.DOCUMENT_ID)
    reference = ContentReference(kind=reference_kind, value=chunk.source.reference)
    page_section = ", ".join(x for x in (
        f"page {chunk.page}" if chunk.page else None,
        chunk.section or chunk.source.title) if x)
    return EvidenceRecord(
        evidence_id=chunk.chunk_id, request_id=request_id,
        source=EvidenceSource(source_id=chunk.source.source_id,
            source_identity=chunk.source.source_identity, source_type=source_type,
            title=chunk.source.title, reference=reference, publisher=chunk.source.publisher),
        excerpt=chunk.text, content_reference=ContentReference(
            kind=ReferenceKind.DOCUMENT_ID, value=chunk.parent_document_id),
        retrieved_at=result.retrieved_at, publication_date=chunk.source.publication_date,
        geographic_applicability=chunk.geographic_applicability or chunk.source.jurisdiction,
        crop_applicability=(chunk.crop_id,) if chunk.crop_id else None,
        provenance=(f"Parent document {chunk.parent_document_id}; chunk {chunk.chunk_id}; "
                    f"content sha256 {chunk.content_hash}; revision {chunk.source.revision or 'unspecified'}"
                    + (f"; revised at {chunk.source.revised_at.isoformat()}"
                       if chunk.source.revised_at else "")),
        mode=EvidenceMode.CACHED, status=EvidenceStatus.AVAILABLE,
        relevance=max(0.0, min(1.0, result.similarity)), page_or_section=page_section,
        quality_notes=tuple(x for x in (
            "Source classified as secondary explanation" if chunk.source.source_type.value == "secondary_explanation" else None,
            f"Source reliability: {chunk.source.reliability.value}",
            f"Revision: {chunk.source.revision}" if chunk.source.revision else None,
            f"Revised at: {chunk.source.revised_at.isoformat()}" if chunk.source.revised_at else None,
            f"Growth stage: {chunk.growth_stage}" if chunk.growth_stage else None,
            f"Topic: {chunk.topic}" if chunk.topic else None,
            f"Scheme jurisdiction level: {chunk.source.jurisdiction_level}"
            if chunk.source.jurisdiction_level else None) if x))

"""Explicit local-file adapters; no implicit network activity."""
from __future__ import annotations
from pathlib import Path
from .adapters import DocumentMetadata, RetrievalError, SourceReliability, SourceType
from .ingestion import load_curated, load_schemes


class CuratedFileAdapter:
    def __init__(self, path, crop_registry):
        self.path, self.crop_registry = Path(path), crop_registry
        self._documents = {(d.document_id, d.revision): d for d in load_curated(path, crop_registry)}
    @property
    def source_id(self): return "curated-local"
    def list_documents(self):
        return tuple(DocumentMetadata(document_id=d.document_id, source_id=d.source_id,
            title=d.title, source_type=d.source_type, reliability=d.reliability,
            source_identity=d.source_identity, reference=d.source_reference,
            publisher=d.publisher, publication_date=d.publication_date, revision=d.revision,
            revised_at=d.revised_at) for d in self._documents.values())
    def get_content(self, document_id, revision=None):
        matches = [d for (doc_id, rev), d in self._documents.items()
                   if doc_id == document_id and (revision is None or rev == revision)]
        if len(matches) == 1: return matches[0].content
        if len(matches) > 1: raise RetrievalError(f"document has multiple revisions: {document_id}")
        raise RetrievalError(f"unknown document: {document_id}")


class SchemeFileAdapter:
    def __init__(self, path):
        self._documents = {(d.document_id, d.revision): d for d in load_schemes(path)}
    @property
    def source_id(self): return "government-local"
    def list_documents(self):
        return tuple(DocumentMetadata(document_id=d.document_id, source_id=d.source_id,
            title=d.title, source_type=d.source_type,
            reliability=SourceReliability.PRIMARY if d.official else SourceReliability.SECONDARY,
            source_identity=d.source_identity, reference=d.source_reference,
            publisher=d.publisher, publication_date=d.publication_date, revision=d.revision,
            revised_at=d.revised_at, jurisdiction=d.jurisdiction,
            jurisdiction_level=d.jurisdiction_level) for d in self._documents.values())
    def get_content(self, document_id, revision=None):
        matches = [d for (doc_id, rev), d in self._documents.items()
                   if doc_id == document_id and (revision is None or rev == revision)]
        if len(matches) == 1: return matches[0].content
        if len(matches) > 1: raise RetrievalError(f"document has multiple revisions: {document_id}")
        raise RetrievalError(f"unknown document: {document_id}")

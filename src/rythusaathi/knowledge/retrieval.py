"""In-process vector retrieval with exact metadata filters."""
from __future__ import annotations
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable
from .embeddings import EmbeddingProvider
from .processing import DocumentChunk
from .adapters import RetrievalError


@dataclass(frozen=True)
class RetrievalFilter:
    crop_id: str | None = None
    geography: str | None = None
    growth_stage: str | None = None
    topic: str | None = None
    source_type: str | None = None
    jurisdiction_level: str | None = None


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: DocumentChunk
    similarity: float
    retrieved_at: datetime


class LocalKnowledgeBase:
    def __init__(self, embeddings: EmbeddingProvider, *, min_similarity: float = 0.08):
        if not -1.0 <= min_similarity <= 1.0:
            raise ValueError("min_similarity must be between -1 and 1")
        self.embeddings = embeddings
        self.min_similarity = min_similarity
        self._items: dict[str, tuple[DocumentChunk, tuple[float, ...]]] = {}

    @property
    def size(self): return len(self._items)

    def add(self, chunks: Iterable[DocumentChunk]) -> int:
        chunks = tuple(chunks)
        if not chunks: return 0
        ids = [c.chunk_id for c in chunks]
        if len(ids) != len(set(ids)): raise ValueError("duplicate chunk IDs in batch")
        existing = set(ids) & self._items.keys()
        if existing: raise ValueError(f"duplicate chunk ID: {sorted(existing)[0]}")
        try:
            vectors = self.embeddings.embed([c.text for c in chunks])
        except Exception as exc:
            raise RetrievalError("embedding failed while indexing documents") from exc
        if len(vectors) != len(chunks):
            raise RetrievalError("embedding provider returned the wrong batch size")
        pending = []
        revision = getattr(self.embeddings, "model_revision", "unknown")
        for chunk, emb in zip(chunks, vectors, strict=True):
            if (emb.model_id != self.embeddings.model_id or emb.model_revision != revision or
                len(emb.vector) != self.embeddings.dimensions or any(not math.isfinite(x) for x in emb.vector)):
                raise RetrievalError("embedding provider returned a malformed vector")
            pending.append((chunk.chunk_id, (chunk, emb.vector)))
        self._items.update(pending)
        return len(chunks)

    def search(self, query: str, *, top_k: int = 5, filters: RetrievalFilter | None = None):
        if top_k < 1: raise ValueError("top_k must be positive")
        try:
            vectors = self.embeddings.embed([query])
            if len(vectors) != 1 or len(vectors[0].vector) != self.embeddings.dimensions:
                raise ValueError("malformed query embedding")
            query_vector = vectors[0].vector
        except Exception as exc:
            raise RetrievalError("embedding failed during retrieval") from exc
        f = filters or RetrievalFilter()
        now = datetime.now(timezone.utc)
        results = []
        for chunk, vector in self._items.values():
            if f.crop_id and chunk.crop_id != f.crop_id: continue
            if f.geography and not _geography_applies(chunk.geographic_applicability, f.geography): continue
            if f.growth_stage and chunk.growth_stage != f.growth_stage: continue
            if f.topic and chunk.topic != f.topic: continue
            if f.source_type and chunk.source.source_type.value != f.source_type: continue
            if f.jurisdiction_level and chunk.source.jurisdiction_level != f.jurisdiction_level: continue
            score = sum(a*b for a,b in zip(query_vector, vector, strict=True))
            if score >= self.min_similarity:
                results.append(RetrievedChunk(chunk, score, now))
        return tuple(sorted(results, key=lambda r: (-r.similarity, r.chunk.chunk_id))[:top_k])


def _geography_applies(scopes, requested: str) -> bool:
    if scopes is None:
        return False
    if requested in scopes:
        return True
    return ("location:country:in" in scopes and requested.startswith("location:"))

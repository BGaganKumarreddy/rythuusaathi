"""SQLite-backed persistent vector store for small, single-user local corpora."""
from __future__ import annotations

import json
import math
import sqlite3
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .adapters import DocumentMetadata, RetrievalError
from .embeddings import EmbeddingProvider
from .processing import DocumentChunk
from .retrieval import RetrievalFilter, RetrievedChunk


class PersistentKnowledgeBase:
    """Durable flat vector index backed by SQLite; cosine ranking is exact scan."""

    def __init__(self, path: str | Path, embeddings: EmbeddingProvider, *,
                 min_similarity: float = 0.08):
        if not -1.0 <= min_similarity <= 1.0:
            raise ValueError("min_similarity must be between -1 and 1")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.embeddings, self.min_similarity = embeddings, min_similarity
        self._db = sqlite3.connect(self.path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS vector_store_meta (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                model_id TEXT NOT NULL,
                model_revision TEXT NOT NULL,
                dimensions INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS knowledge_chunks (
                chunk_id TEXT PRIMARY KEY,
                parent_document_id TEXT NOT NULL,
                revision TEXT,
                content_hash TEXT NOT NULL,
                text TEXT NOT NULL,
                source_json TEXT NOT NULL,
                crop_id TEXT,
                variety_id TEXT,
                growth_stage TEXT,
                topic TEXT,
                geography_json TEXT,
                page INTEGER,
                section TEXT,
                vector BLOB NOT NULL,
                model_id TEXT NOT NULL,
                model_revision TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_knowledge_parent ON knowledge_chunks(parent_document_id);
            CREATE INDEX IF NOT EXISTS ix_knowledge_crop ON knowledge_chunks(crop_id);
            CREATE INDEX IF NOT EXISTS ix_knowledge_stage ON knowledge_chunks(growth_stage);
            CREATE INDEX IF NOT EXISTS ix_knowledge_topic ON knowledge_chunks(topic);
            CREATE INDEX IF NOT EXISTS ix_knowledge_source_type ON knowledge_chunks(json_extract(source_json, '$.source_type'));
        """)
        self._bind_model()

    def _bind_model(self):
        row = self._db.execute("SELECT model_id, model_revision, dimensions FROM vector_store_meta WHERE singleton=1").fetchone()
        if row is None:
            revision = getattr(self.embeddings, "model_revision", "unknown")
            self._db.execute("INSERT INTO vector_store_meta VALUES (1, ?, ?, ?)",
                             (self.embeddings.model_id, revision, self.embeddings.dimensions))
            self._db.commit()
        elif (row["model_id"], row["model_revision"], row["dimensions"]) != (
            self.embeddings.model_id, getattr(self.embeddings, "model_revision", "unknown"),
            self.embeddings.dimensions):
            self._db.close()
            raise ValueError("database embedding model/revision/dimensions do not match provider")

    @property
    def size(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0])

    def close(self):
        self._db.close()

    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): self.close()

    def add(self, chunks: Iterable[DocumentChunk]) -> int:
        chunks = tuple(chunks)
        if not chunks: return 0
        ids = [chunk.chunk_id for chunk in chunks]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate chunk IDs in batch")
        pending = []
        unseen = []
        for chunk in chunks:
            row = self._db.execute("SELECT content_hash FROM knowledge_chunks WHERE chunk_id=?",
                                    (chunk.chunk_id,)).fetchone()
            if row:
                if row["content_hash"] != chunk.content_hash:
                    raise ValueError(f"chunk ID collision with changed content: {chunk.chunk_id}")
                continue
            unseen.append(chunk)
        if not unseen: return 0
        try:
            vectors = self.embeddings.embed([chunk.text for chunk in unseen])
        except Exception as exc:
            raise RetrievalError("embedding failed while indexing documents") from exc
        if len(vectors) != len(unseen):
            raise RetrievalError("embedding provider returned the wrong batch size")
        revision = getattr(self.embeddings, "model_revision", "unknown")
        for chunk, embedding in zip(unseen, vectors, strict=True):
            if (embedding.model_id != self.embeddings.model_id or
                embedding.model_revision != revision or
                embedding.dimensions != self.embeddings.dimensions or
                len(embedding.vector) != self.embeddings.dimensions or
                any(not math.isfinite(v) for v in embedding.vector)):
                raise RetrievalError("embedding provider returned a malformed vector")
            vector_blob = struct.pack(f"<{self.embeddings.dimensions}f", *embedding.vector)
            pending.append((chunk, vector_blob, revision))
        try:
            with self._db:
                for chunk, vector_blob, model_revision in pending:
                    source_json = chunk.source.model_dump_json()
                    geography_json = json.dumps(chunk.geographic_applicability)
                    self._db.execute("""INSERT INTO knowledge_chunks VALUES
                        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (chunk.chunk_id, chunk.parent_document_id, chunk.source.revision,
                         chunk.content_hash, chunk.text, source_json, chunk.crop_id,
                         chunk.variety_id, chunk.growth_stage, chunk.topic, geography_json,
                         chunk.page, chunk.section, vector_blob, self.embeddings.model_id,
                         model_revision))
        except sqlite3.Error as exc:
            raise RetrievalError("persistent vector index write failed") from exc
        return len(pending)

    def search(self, query: str, *, top_k: int = 5,
               filters: RetrievalFilter | None = None) -> tuple[RetrievedChunk, ...]:
        if top_k < 1: raise ValueError("top_k must be positive")
        try:
            result = self.embeddings.embed([query])
            if len(result) != 1 or len(result[0].vector) != self.embeddings.dimensions:
                raise ValueError("malformed query embedding")
            query_vector = result[0].vector
        except Exception as exc:
            raise RetrievalError("embedding failed during retrieval") from exc
        f = filters or RetrievalFilter()
        rows = self._db.execute("SELECT * FROM knowledge_chunks").fetchall()
        now = datetime.now(timezone.utc)
        hits = []
        for row in rows:
            geography = json.loads(row["geography_json"])
            source = DocumentMetadata.model_validate_json(row["source_json"])
            if f.crop_id and row["crop_id"] != f.crop_id: continue
            if f.geography and not _geography_applies(geography, f.geography): continue
            if f.growth_stage and row["growth_stage"] != f.growth_stage: continue
            if f.topic and row["topic"] != f.topic: continue
            if f.source_type and source.source_type.value != f.source_type: continue
            if f.jurisdiction_level and source.jurisdiction_level != f.jurisdiction_level: continue
            vector = struct.unpack(f"<{self.embeddings.dimensions}f", row["vector"])
            score = sum(a*b for a, b in zip(query_vector, vector, strict=True))
            if score < self.min_similarity: continue
            chunk = DocumentChunk(
                chunk_id=row["chunk_id"], parent_document_id=row["parent_document_id"],
                source=source, text=row["text"], content_hash=row["content_hash"],
                crop_id=row["crop_id"], variety_id=row["variety_id"],
                growth_stage=row["growth_stage"], topic=row["topic"],
                geographic_applicability=tuple(geography) if geography is not None else None,
                page=row["page"], section=row["section"])
            hits.append(RetrievedChunk(chunk, score, now))
        return tuple(sorted(hits, key=lambda hit: (-hit.similarity, hit.chunk.chunk_id))[:top_k])


def _geography_applies(scopes, requested: str) -> bool:
    if scopes is None or not scopes:
        return False
    if requested in scopes:
        return True
    if "location:country:in" in scopes and requested.startswith("location:"):
        return True
    for s in scopes:
        if s.startswith("location:state:") and requested.startswith("location:district:"):
            state_code = s.split("location:state:")[-1]
            if requested.startswith(f"location:district:{state_code}-"):
                return True
    return False

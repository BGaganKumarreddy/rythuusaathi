"""Local, provenance-preserving agricultural knowledge retrieval."""

from .adapters import (DocumentMetadata, RetrievalError, SourceAdapter,
                       SourceReliability, SourceType)
from .models import AgriculturalDocument, SchemeDocument, KnowledgeTopic
from .processing import DocumentChunk, DocumentProcessor
from .embeddings import EmbeddingProvider, FastEmbedProvider, HashEmbeddingProvider, Embedding
from .retrieval import LocalKnowledgeBase, RetrievalFilter, RetrievedChunk
from .persistent import PersistentKnowledgeBase
from .evidence import to_evidence_record
from .file_adapters import CuratedFileAdapter, SchemeFileAdapter
from .ingestion import IngestionError, load_curated, load_schemes

__all__ = ["AgriculturalDocument", "DocumentChunk", "DocumentMetadata",
           "DocumentProcessor", "Embedding", "EmbeddingProvider", "FastEmbedProvider",
           "HashEmbeddingProvider", "KnowledgeTopic", "LocalKnowledgeBase", "PersistentKnowledgeBase",
           "RetrievalError", "RetrievalFilter", "RetrievedChunk",
           "SchemeDocument", "SourceAdapter", "SourceReliability", "SourceType",
           "to_evidence_record", "CuratedFileAdapter", "SchemeFileAdapter",
           "IngestionError", "load_curated", "load_schemes"]

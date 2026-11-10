"""Replaceable local embedding contract and deterministic hashing vectorizer.

This dependency-free vectorizer is a reproducible lexical baseline, not a
pretrained semantic language model. Applications can inject a local model.
"""
from __future__ import annotations
import hashlib
import math
import re
from dataclasses import dataclass
from typing import Protocol, Sequence, runtime_checkable
from pathlib import Path
import threading


@dataclass(frozen=True)
class Embedding:
    vector: tuple[float, ...]
    model_id: str
    dimensions: int
    model_revision: str = "unknown"


@runtime_checkable
class EmbeddingProvider(Protocol):
    @property
    def model_id(self) -> str: ...
    @property
    def dimensions(self) -> int: ...
    def embed(self, texts: Sequence[str]) -> tuple[Embedding, ...]: ...


class HashEmbeddingProvider:
    def __init__(self, dimensions: int = 384, model_id: str = "local-feature-hash-v1"):
        if dimensions < 16: raise ValueError("dimensions must be at least 16")
        self._dimensions, self._model_id = dimensions, model_id
    @property
    def model_id(self): return self._model_id
    @property
    def model_revision(self): return "local-feature-hash-v1"
    @property
    def dimensions(self): return self._dimensions

    def embed(self, texts: Sequence[str]) -> tuple[Embedding, ...]:
        result = []
        for text in texts:
            if not isinstance(text, str): raise TypeError("embedding inputs must be strings")
            if not text.strip(): raise ValueError("embedding input cannot be blank")
            vector = [0.0] * self.dimensions
            tokens = re.findall(r"[\w]+", text.casefold())
            tokens += [text.casefold()[i:i+3] for i in range(max(0, len(text)-2))]
            for token in tokens:
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                index = int.from_bytes(digest[:4], "big") % self.dimensions
                sign = 1.0 if digest[4] & 1 else -1.0
                vector[index] += sign
            norm = math.sqrt(sum(x*x for x in vector))
            if norm: vector = [x/norm for x in vector]
            result.append(Embedding(tuple(vector), self.model_id, self.dimensions,
                                    "local-feature-hash-v1"))
        return tuple(result)


class FastEmbedProvider:
    """Lazy CPU inference through FastEmbed's quantized multilingual MiniLM ONNX model."""

    DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    DEFAULT_MODEL_REPOSITORY = "Qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q"
    DEFAULT_REVISION = "faf4aa4225822f3bc6376869cb1164e8e3feedd0"
    DEFAULT_DIMENSIONS = 384

    def __init__(self, *, model_name: str = DEFAULT_MODEL, model_revision: str = DEFAULT_REVISION,
                 cache_dir: str | Path | None = None, batch_size: int = 32, threads: int = 4):
        if not model_name.strip() or not model_revision.strip():
            raise ValueError("model name and revision are required")
        if batch_size < 1 or threads < 1:
            raise ValueError("batch_size and threads must be positive")
        self.model_name, self.model_revision = model_name, model_revision
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.batch_size, self.threads = batch_size, threads
        self._model = None
        self._lock = threading.Lock()

    @property
    def model_id(self): return self.model_name
    @property
    def dimensions(self): return self.DEFAULT_DIMENSIONS

    def _get_model(self):
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                try:
                    from fastembed import TextEmbedding
                except ImportError as exc:
                    raise RuntimeError("install the optional 'semantic' extra to use FastEmbedProvider") from exc
                try:
                    is_commit = len(self.model_revision) == 40 and all(
                        character in "0123456789abcdefABCDEF" for character in self.model_revision
                    )
                    model_kwargs = {}
                    if is_commit:
                        try:
                            from huggingface_hub import snapshot_download
                        except ImportError as exc:
                            raise RuntimeError(
                                "install huggingface-hub to load a pinned FastEmbed model snapshot"
                            ) from exc
                        model_repository = self.DEFAULT_MODEL_REPOSITORY
                        if self.model_name != self.DEFAULT_MODEL:
                            model_spec = next((item for item in TextEmbedding.list_supported_models()
                                               if item.get("model") == self.model_name), None)
                            model_repository = ((model_spec or {}).get("sources") or {}).get("hf")
                            if not model_repository:
                                raise ValueError(
                                    f"FastEmbed model {self.model_name!r} has no Hugging Face snapshot source"
                                )
                        snapshot_kwargs = {
                            "repo_id": model_repository,
                            "revision": self.model_revision,
                            "cache_dir": str(self.cache_dir) if self.cache_dir else None,
                        }
                        try:
                            snapshot_path = snapshot_download(
                                **snapshot_kwargs, local_files_only=True)
                        except Exception:
                            # A missing or incomplete local snapshot may be fetched on
                            # the first inference; a complete pinned cache works offline.
                            snapshot_path = snapshot_download(**snapshot_kwargs)
                        model_kwargs.update(
                            specific_model_path=str(snapshot_path),
                            local_files_only=True,
                        )
                    self._model = TextEmbedding(
                        model_name=self.model_name,
                        cache_dir=str(self.cache_dir) if self.cache_dir else None,
                        threads=self.threads,
                        providers=["CPUExecutionProvider"],
                        **model_kwargs,
                    )
                except Exception as exc:
                    raise RuntimeError(
                        f"could not load local embedding model {self.model_name}; "
                        "ensure the model is cached or allow its initial download"
                    ) from exc
        return self._model

    def embed(self, texts: Sequence[str]) -> tuple[Embedding, ...]:
        if not texts:
            return ()
        if any(not isinstance(text, str) for text in texts):
            raise TypeError("embedding inputs must be strings")
        if any(not text.strip() for text in texts):
            raise ValueError("embedding inputs cannot be blank")
        try:
            vectors = self._get_model().embed(list(texts), batch_size=self.batch_size)
            result = []
            for raw in vectors:
                vector = tuple(float(value) for value in raw)
                if len(vector) != self.dimensions or any(not math.isfinite(x) for x in vector):
                    raise ValueError("model returned a malformed vector")
                norm = math.sqrt(sum(x*x for x in vector))
                if not norm:
                    raise ValueError("model returned a zero vector")
                result.append(Embedding(tuple(x / norm for x in vector), self.model_id,
                                        self.dimensions, self.model_revision))
            if len(result) != len(texts):
                raise ValueError("model returned the wrong number of vectors")
            return tuple(result)
        except Exception as exc:
            if isinstance(exc, (TypeError, ValueError)):
                raise
            if isinstance(exc, RuntimeError) and (
                "optional 'semantic' extra" in str(exc) or
                "could not load local embedding model" in str(exc)
            ):
                raise
            raise RuntimeError("local embedding inference failed") from exc

"""Reproducible local curated and government document ingestion."""
from __future__ import annotations
import json
import hashlib
from pathlib import Path
from typing import Iterable
from pydantic import ValidationError
from .models import AgriculturalDocument, SchemeDocument
from ..domain.registry import CropRegistry


class IngestionError(ValueError): pass


def load_curated(path: str | Path, crop_registry: CropRegistry) -> tuple[AgriculturalDocument, ...]:
    return _load(path, AgriculturalDocument, lambda d: _validate_crop(d, crop_registry))


def load_schemes(path: str | Path) -> tuple[SchemeDocument, ...]:
    return _load(path, SchemeDocument)


def _load(path, model, validate=None):
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, list): raise IngestionError("dataset must be a JSON array")
        docs = tuple(model.model_validate(item) for item in raw)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise IngestionError(f"invalid dataset: {exc}") from exc
    identities = [(doc.document_id, doc.revision) for doc in docs]
    if len(identities) != len(set(identities)):
        raise IngestionError("duplicate document ID and revision in dataset")
    hashes = [hashlib.sha256(" ".join(doc.content.split()).casefold().encode("utf-8")).hexdigest()
              for doc in docs]
    if len(hashes) != len(set(hashes)): raise IngestionError("duplicate document content in dataset")
    if validate:
        for doc in docs: validate(doc)
    return docs


def _validate_crop(doc, registry):
    crop = registry.get(doc.crop_id)
    if crop is None: raise IngestionError(f"unknown crop_id: {doc.crop_id}")
    if doc.variety_id and doc.variety_id not in {v.variety_id for v in crop.varieties}:
        raise IngestionError(f"variety {doc.variety_id} is not registered for {doc.crop_id}")


def ingest_curated_directory(path: str | Path, crop_registry: CropRegistry):
    docs = []
    for file in sorted(Path(path).glob("*.json")):
        docs.extend(load_curated(file, crop_registry))
    _unique(docs)
    return tuple(docs)


def ingest_scheme_directory(path: str | Path):
    docs = []
    for file in sorted(Path(path).glob("*.json")):
        docs.extend(load_schemes(file))
    _unique(docs)
    return tuple(docs)


def _unique(docs):
    ids = [(d.document_id, d.revision) for d in docs]
    if len(ids) != len(set(ids)): raise IngestionError("duplicate document ID and revision across files")

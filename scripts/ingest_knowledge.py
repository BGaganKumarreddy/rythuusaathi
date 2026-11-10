"""Explicitly ingest checked-in corpora into a persistent local index."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rythusaathi.domain.seed import CROP_REGISTRY
from rythusaathi.knowledge import (DocumentProcessor, FastEmbedProvider,
    HashEmbeddingProvider, PersistentKnowledgeBase, load_curated, load_schemes)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lexical", action="store_true", help="use deterministic hashing baseline")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--model-cache", type=Path)
    args = parser.parse_args()
    provider = HashEmbeddingProvider() if args.lexical else FastEmbedProvider(
        cache_dir=args.model_cache or ROOT / "output" / "models")
    db_path = args.database or ROOT / "output" / (
        "rythusaathi-knowledge-hash.sqlite3" if args.lexical
        else "rythusaathi-knowledge-fastembed.sqlite3")
    processor = DocumentProcessor()
    agricultural = load_curated(ROOT / "data/agricultural/icar_advisories.json", CROP_REGISTRY)
    schemes = load_schemes(ROOT / "data/schemes/central_agricultural_schemes.json")
    chunks = [chunk for doc in agricultural for chunk in processor.process_curated(doc)]
    chunks.extend(chunk for doc in schemes for chunk in processor.process_scheme(doc))
    with PersistentKnowledgeBase(db_path, provider) as index:
        added = index.add(chunks)
        print(f"Loaded {len(agricultural)} agricultural documents and {len(schemes)} scheme documents")
        print(f"Persistent index contains {index.size} chunks; added {added} new chunks")
        print(f"Embedding provider: {provider.model_id} ({provider.dimensions} dimensions)")
        print(f"Embedding revision: {getattr(provider, 'model_revision', 'unknown')}")
        print(f"Database: {db_path}")
        print("A first FastEmbed call may download the configured model. No fetch occurs during import/startup.")


if __name__ == "__main__": main()

"""Local persistent retrieval demo; no LLM credentials or agents are used."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rythusaathi.domain.seed import CROP_REGISTRY, LOCATION_REGISTRY
from rythusaathi.knowledge import (DocumentProcessor, FastEmbedProvider,
    HashEmbeddingProvider, LocalKnowledgeBase, PersistentKnowledgeBase,
    RetrievalFilter, load_curated, load_schemes, to_evidence_record)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lexical", action="store_true", help="use deterministic hashing baseline instead of pretrained multilingual embeddings")
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
    chunks = [c for doc in agricultural for c in processor.process_curated(doc)]
    chunks.extend(c for doc in schemes for c in processor.process_scheme(doc))
    with PersistentKnowledgeBase(db_path, provider) as store:
        added = store.add(chunks)
        print(f"Persistent index: {db_path} ({store.size} chunks; {added} newly indexed)")
        query = "Rice leaves are turning yellow during vegetative growth in Punjab. What agricultural knowledge is relevant?"
        crop = CROP_REGISTRY.resolve("rice")
        location = LOCATION_REGISTRY.resolve("Punjab")
        if not crop.canonical_id or not location.canonical_id:
            raise RuntimeError("crop or location could not be resolved by the Phase 2 registry")
        print(f"Resolved crop: {crop.canonical_id}; location: {location.canonical_id}")
        stage_result = CROP_REGISTRY.get(crop.canonical_id)
        stages = {stage.stage_id for stage in stage_result.growth_stages} if stage_result else set()
        stage = "vegetative" if "vegetative" in stages else None
        if stage is None:
            print("Growth stage applicability: unknown (the crop registry does not define the phrase 'vegetative growth'); not asserted.")
        hits = store.search(query, top_k=4, filters=RetrievalFilter(
            crop_id=crop.canonical_id, geography=location.canonical_id,
            growth_stage=stage, topic="nutrient_deficiencies"))
        if hits:
            print("\nRelevant source excerpts (not a diagnosis):")
            for hit in hits:
                evidence = to_evidence_record(hit, request_id="local-demo")
                print(f"- {evidence.source.title} [{evidence.evidence_id}] similarity={hit.similarity:.3f}")
                print(f"  Source: {evidence.source.reference.value}")
                print(f"  Parent: {hit.chunk.parent_document_id}; page/section: {evidence.page_or_section}")
                print(f"  Excerpt: {evidence.excerpt}")
            print("These sources describe relevant information, but the symptoms alone are insufficient to diagnose the field.")
        else:
            print("\nInsufficient evidence: no source matched the crop, location, topic and retrieval threshold.")

        unsupported = "What is the recommended pruning schedule for banana in Kerala?"
        unsupported_crop = CROP_REGISTRY.resolve("banana")
        unsupported_hits = store.search(unsupported, top_k=4, filters=RetrievalFilter(
            crop_id=unsupported_crop.canonical_id or "crop:unregistered-banana"))
        print("\nUnsupported query:")
        print(f"- {unsupported} -> {len(unsupported_hits)} evidence items")
        if not unsupported_hits:
            print("  Insufficient evidence; no matching registered crop knowledge was found.")


if __name__ == "__main__": main()

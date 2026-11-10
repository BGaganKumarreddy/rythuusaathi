"""Small deterministic retrieval evaluation over the checked-in query set."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rythusaathi.knowledge import (DocumentProcessor, FastEmbedProvider,
    HashEmbeddingProvider, LocalKnowledgeBase, RetrievalFilter,
    load_curated, load_schemes)
from rythusaathi.domain.seed import CROP_REGISTRY


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lexical", action="store_true")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--model-cache", type=Path, default=ROOT / "output" / "models")
    args = parser.parse_args()
    provider = HashEmbeddingProvider() if args.lexical else FastEmbedProvider(
        cache_dir=args.model_cache)
    processor = DocumentProcessor()
    docs = load_curated(ROOT / "data/agricultural/icar_advisories.json", CROP_REGISTRY)
    schemes = load_schemes(ROOT / "data/schemes/central_agricultural_schemes.json")
    chunks = [c for d in docs for c in processor.process_curated(d)]
    chunks.extend(c for d in schemes for c in processor.process_scheme(d))
    index = LocalKnowledgeBase(provider)
    index.add(chunks)
    cases = json.loads((ROOT / "data/agricultural/retrieval_eval.json").read_text(encoding="utf-8"))
    hits, unsupported_ok, reciprocal_ranks = 0, 0, []
    for case in cases:
        result = index.search(case["query"], top_k=args.top_k,
                              filters=RetrievalFilter(**case["filters"]))
        found = [item.chunk.parent_document_id for item in result]
        expected = case["expected_document_ids"]
        if not expected:
            unsupported_ok += int(not found)
            reciprocal_ranks.append(0.0)
        else:
            hits += int(any(item in expected for item in found))
            reciprocal_ranks.append(next((1/(i+1) for i, item in enumerate(found)
                                          if item in expected), 0.0))
        print(f"{'PASS' if (not found if not expected else any(x in expected for x in found)) else 'MISS'} "
              f"expected={expected} retrieved={found}")
    supported = sum(bool(case["expected_document_ids"]) for case in cases)
    unsupported = len(cases) - supported
    print(f"Hit@{args.top_k}: {hits}/{supported}; unsupported-empty: {unsupported_ok}/{unsupported}; "
          f"MRR over all queries: {sum(reciprocal_ranks)/len(reciprocal_ranks):.3f}; "
          f"supported-query MRR: {sum(reciprocal_ranks)/(supported or 1):.3f}")


if __name__ == "__main__": main()

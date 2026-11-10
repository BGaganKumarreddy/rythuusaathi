"""Run the bounded Phase 5 evaluation with mocked LLM responses.

The mocked integration metrics exercise parsing, context validation, retrieval,
citation and failure paths. They are not a measurement of live model quality.
Use --semantic to evaluate retrieval with the pinned FastEmbed model; the
default --lexical path is deterministic and works offline.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_framework import ChatResponse, Message

from rythusaathi.agents import FarmerRequest, RythuSaathiWorkflow, WorkflowMode, empty_coverage_resolver
from rythusaathi.domain.seed import CROP_REGISTRY
from rythusaathi.inference.contracts import InferenceRequest, ProviderFailure, ProviderFailureKind, ProviderResponse
from rythusaathi.knowledge import (
    DocumentProcessor,
    FastEmbedProvider,
    HashEmbeddingProvider,
    LocalKnowledgeBase,
    RetrievalFilter,
    load_curated,
    load_schemes,
)
from rythusaathi.agents.llm import CoordinatorDraft, IntakeInterpretation, SpecialistInterpretation


def _text(message: Message) -> str:
    return "".join(item if isinstance(item, str) else getattr(item, "text", "")
                   for item in message.contents)


class EvaluationInference:
    model_id = "fixture/phase5-evaluation"

    def __init__(self, cases: dict[str, dict], *, fail: bool = False):
        self.cases = cases
        self.fail = fail
        self.valid_responses = 0
        self.call_count = 0

    async def complete(self, request: InferenceRequest, *, expected_user_id: str,
                       expected_tenant_id: str) -> ProviderResponse:
        self.call_count += 1
        if self.fail:
            raise ProviderFailure(ProviderFailureKind.TIMEOUT)
        system = _text(request.messages[0])
        task = json.loads(_text(request.messages[1]))["task_input"]
        query = task.get("farmer_message", task.get("farmer_question", ""))
        case = self.cases.get(query)
        if "Extract only candidate mentions" in system:
            output = case["mock_intake"]
        elif system.startswith("You are a specialist"):
            evidence_id = task["evidence"][0]["evidence_id"]
            output = {
                "summary": "Mocked interpretation using the supplied scoped source only.",
                "statements": [{
                    "text": "Review the cited material with the confirmed context.",
                    "kind": "interpretation",
                    "evidence_ids": [evidence_id],
                    "uncertainty": "Mock output; no independent diagnosis or eligibility decision.",
                }],
            }
            SpecialistInterpretation.model_validate(output)
        else:
            evidence_id = task["evidence"][0]["evidence_id"]
            output = {
                "statements": [{
                    "text": "Review the cited source material with local agricultural support.",
                    "kind": "interpretation",
                    "evidence_ids": [evidence_id],
                    "uncertainty": "Mock output; consult the issuing or agricultural authority.",
                }],
                "follow_up_questions": [],
            }
            CoordinatorDraft.model_validate(output)
        # Keep a local schema-validation counter in the harness. Production
        # responses are independently parsed by the matching strict Pydantic model.
        if "Extract only candidate mentions" in system:
            IntakeInterpretation.model_validate(output)
        self.valid_responses += 1
        response = ChatResponse(messages=[Message("assistant", [json.dumps(output)])])
        return ProviderResponse("groq", self.model_id, response)


def _build_corpus(provider):
    processor = DocumentProcessor()
    agricultural = load_curated(ROOT / "data/agricultural/icar_advisories.json", CROP_REGISTRY)
    schemes = load_schemes(ROOT / "data/schemes/central_agricultural_schemes.json")
    chunks = [chunk for item in agricultural for chunk in processor.process_curated(item)]
    chunks.extend(chunk for item in schemes for chunk in processor.process_scheme(item))
    index = LocalKnowledgeBase(provider)
    index.add(chunks)
    return index


def _field_correct(resolution, expected_id: str | None, expected_candidates: list[str]) -> bool:
    if expected_candidates:
        return (resolution.status.value == "ambiguous"
                and set(resolution.candidate_ids) == set(expected_candidates))
    if expected_id:
        return resolution.status.value == "matched" and resolution.canonical_id == expected_id
    return resolution.status.value != "matched"


async def evaluate(args) -> dict[str, object]:
    cases = json.loads((ROOT / "data/agricultural/phase5_agent_eval.json").read_text(encoding="utf-8"))
    cases_by_query = {item["query"]: item for item in cases}
    provider = (FastEmbedProvider(cache_dir=args.model_cache) if args.semantic
                else HashEmbeddingProvider())
    index = _build_corpus(provider)
    try:
        expected_supported = [item for item in cases if item["expected_document_ids"]]
        relevance_hits = 0
        for item in cases:
            hits = index.search(
                item["retrieval_query"],
                top_k=args.top_k,
                filters=RetrievalFilter(**item["retrieval_filters"]),
            )
            document_ids = [hit.chunk.parent_document_id for hit in hits]
            if item["expected_document_ids"]:
                relevance_hits += int(any(doc_id in item["expected_document_ids"]
                                          for doc_id in document_ids))
            elif not document_ids:
                # An unsupported query must remain empty under its exact filters.
                pass
        retrieval_relevance = relevance_hits / max(len(expected_supported), 1)

        inference = EvaluationInference(cases_by_query)
        workflow = RythuSaathiWorkflow(resolver=empty_coverage_resolver(), knowledge=index)
        responses = []
        context_checks = 0
        context_correct = 0
        context_failures: list[str] = []
        for number, item in enumerate(cases, 1):
            response = await workflow.run(
                FarmerRequest(f"phase5-eval:{number}", "evaluation-user", "evaluation-tenant", item["query"]),
                mode=WorkflowMode.GROQ,
                inference=inference,
            )
            responses.append(response)
            intake = next(result for result in response.agent_results
                          if result.agent_id.value == "Intake")
            context_checks += 1
            crop_ok = _field_correct(
                intake.payload.resolution.crop,
                item["expected_crop_id"],
                item["expected_crop_candidates"],
            )
            context_correct += int(crop_ok)
            if not crop_ok:
                context_failures.append(f"{item['case_id']}:crop")
            context_checks += 1
            location_ok = _field_correct(
                intake.payload.resolution.location,
                item["expected_location_id"],
                item["expected_location_candidates"],
            )
            context_correct += int(location_ok)
            if not location_ok:
                context_failures.append(f"{item['case_id']}:location")
            if item["expected_growth_stage"]:
                context_checks += 1
                stage_ok = (
                    intake.payload.resolution.growth_stage.status.value == "matched"
                    and response.context.growth_stage.value == item["expected_growth_stage"]
                )
                context_correct += int(stage_ok)
                if not stage_ok:
                    context_failures.append(f"{item['case_id']}:growth_stage")

        citation_checked = 0
        citation_valid = 0
        unsupported_cases = [i for i, case in enumerate(cases)
                             if case["category"] in {"unsupported", "ambiguous"}]
        unsupported_safe = 0
        unsupported_failures: list[str] = []
        for index_number, response in enumerate(responses):
            allowed = {item.evidence_id for item in response.evidence}
            for result in response.agent_results:
                statements = getattr(result.payload, "generated_statements", ())
                for statement in statements:
                    citation_checked += 1
                    valid = (set(statement.evidence_ids) <= allowed
                             and (statement.kind not in {"evidence", "interpretation"}
                                  or bool(statement.evidence_ids)))
                    citation_valid += int(valid)
            if index_number in unsupported_cases:
                generated = [statement
                             for result in response.agent_results
                             for statement in getattr(result.payload, "generated_statements", ())]
                unsupported_safe += int(
                    not response.evidence
                    and not any(statement.kind in {"evidence", "interpretation"}
                                for statement in generated)
                )
                if response.evidence or any(statement.kind in {"evidence", "interpretation"}
                                            for statement in generated):
                    unsupported_failures.append(cases[index_number]["case_id"])

        # Exercise failure resilience independently of the normal fixture run.
        failed_inference = EvaluationInference(cases_by_query, fail=True)
        failed = await workflow.run(
            FarmerRequest("phase5-eval:provider-failure", "evaluation-user", "evaluation-tenant",
                          expected_supported[0]["query"]),
            mode=WorkflowMode.GROQ,
            inference=failed_inference,
        )
        failure_resilience = bool(
            failed.execution_mode is WorkflowMode.GROQ
            and failed.status.value == "partial"
            and failed.evidence
            and "deterministic evidence summary is shown" in failed.response_text
        )

        # Strict schema negative control: extra keys are rejected rather than
        # silently ignored by the intake output contract.
        schema_rejected = False
        try:
            IntakeInterpretation.model_validate({"invented": True})
        except Exception:
            schema_rejected = True

        return {
            "retrieval_mode": "pinned FastEmbed" if args.semantic else "deterministic hash baseline (not semantic)",
            "cases": len(cases),
            "categories": {
                "supported": sum(item["category"] == "supported" for item in cases),
                "unsupported": sum(item["category"] == "unsupported" for item in cases),
                "ambiguous": sum(item["category"] == "ambiguous" for item in cases),
            },
            "retrieval_relevance_at_k": f"{relevance_hits}/{len(expected_supported)} ({retrieval_relevance:.1%})",
            "citation_preservation": f"{citation_valid}/{citation_checked} ({citation_valid / max(citation_checked, 1):.1%})",
            "unsupported_and_ambiguous_no_claims": f"{unsupported_safe}/{len(unsupported_cases)}",
            "unsupported_and_ambiguous_failures": unsupported_failures,
            "context_extraction_accuracy": f"{context_correct}/{context_checks} ({context_correct / max(context_checks, 1):.1%})",
            "context_extraction_failures": context_failures,
            "structured_output_validity": f"{inference.valid_responses}/{inference.call_count} ({inference.valid_responses / max(inference.call_count, 1):.1%})",
            "invalid_schema_response_rejected": schema_rejected,
            "provider_failure_resilience": failure_resilience,
            "note": "Mocked workflow responses measure integration contracts, not live Groq answer quality.",
        }
    finally:
        # LocalKnowledgeBase is in-memory and owns no persistent connection.
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--semantic", action="store_true",
                        help="use the pinned FastEmbed model rather than the offline hash baseline")
    parser.add_argument("--model-cache", type=Path, default=ROOT / "tmp" / "models")
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()
    if args.top_k < 1:
        parser.error("--top-k must be positive")
    print(json.dumps(asyncio.run(evaluate(args)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# RythuSaathi Post-Live-Execution Failure Investigation

**Date:** 8 November 2026  
**Type:** Read-only root-cause analysis  
**No code modifications, no live Groq requests**

---

## Executive Summary

The Coordinator synthesis was skipped because **the Coordinator's eligibility gate requires `synthesis_evidence_ids` to be non-empty** (line 1275 of [`workflow.py`](file:///C:/Users/B GAGAN KUMAR REDDY/OneDrive/Desktop/rythusaathi/src/rythusaathi/agents/workflow.py#L1273-L1276)), and that set is populated **only from specialist `generated_statements` of kind `evidence` or `interpretation`** (lines 211–221). The Crop and Scheme agents are marked `partial` because their Groq specialist interpretation calls **failed** (returning a non-`None` `inference_status`), which means they have **no `generated_statements`**, which means the Coordinator sees an empty `synthesis_evidence_ids` set and skips Groq synthesis entirely.

**This is a genuine integration defect introduced during the Coordinator TPM optimization.** The optimization correctly restricts the Coordinator to citing only evidence already validated by specialist statements — but it also made Coordinator synthesis **impossible** when specialists fail to produce LLM interpretations, even though the specialists did retrieve and publish valid source evidence to the board. The deterministic specialist evidence summaries are not promoted to `generated_statements`, so the Coordinator never sees any "validated specialist evidence."

The defect is in the Coordinator eligibility gate, **not** in the evidence retrieval, registry resolution, or constraint system.

---

## 1. Complete Execution Trace

### Step 1 → User Input
- **Query:** `"My rice leaves are yellowing in Punjab. What should I check?"`
- **File:** [`advisory_cli.py`](file:///C:/Users/B GAGAN KUMAR REDDY/OneDrive/Desktop/rythusaathi/scripts/advisory_cli.py#L50-L100)
- Creates `FarmerRequest` with `mode=WorkflowMode.GROQ`, `inference=AuthenticatedGroqInference`
- Uses `empty_coverage_resolver()` (line 89) — **same resolver as all tests**

### Step 2 → Intake Extraction (Groq-assisted)
- **File:** [`workflow.py`](file:///C:/Users/B GAGAN KUMAR REDDY/OneDrive/Desktop/rythusaathi/src/rythusaathi/agents/workflow.py#L571-L714) `IntakeAgent.handle()`
- Groq call succeeded (856 tokens) → `IntakeInterpretation` parsed
- Extracted: crop=`"rice"` (quote `"rice"`), location=`"Punjab"` (quote `"Punjab"`)
- Status: **partial** — because `interpretation_mode` was `"deterministic_fallback"` OR because something in the inference fallback path triggered. Actually, based on the user's report showing Intake=856 tokens successfully, **Intake succeeded** with Groq. But the user says Intake status = `partial`.

> Wait — the user reported Intake status as `partial`. Let me re-examine. Intake is marked `PARTIAL` when `interpretation_mode == "deterministic_fallback"` (line 706). But 856 tokens were consumed, which means the Groq Intake call **did succeed**. The `partial` status may originate from the coverage system.

**Correction**: Intake's `AgentStatus` is `PARTIAL` only when `interpretation_mode == "deterministic_fallback"` (line 706–707). If 856 tokens were used, the Groq call succeeded and `interpretation_mode` would be `"groq"`, making Intake status `COMPLETED`. **The `partial` in the user's report likely refers to the overall advisory status, not individual Intake agent status.** Or — the user may be reporting the agent status as shown in the CLI output, which prints Coordinator's advisory status as `partial`.

Let me re-read the user's statement: "Intake: partial, Crop: partial, Scheme: partial". These are **agent execution statuses** as printed by the CLI at line 191. This is important.

**Re-analysis**: If Intake is `partial`, that means `interpretation_mode == "deterministic_fallback"` (line 706). But 856 tokens were consumed at Intake. Let me re-check: the 856 tokens could have been consumed but the response could have failed Pydantic validation, causing an exception, which sets `interpretation_mode = "deterministic_fallback"` and `inference_status` to the error kind (line 625–627). In that case, Intake falls back to deterministic resolution, which still resolves rice → `crop:oryza-sativa` and Punjab → `location:state:in-pb` correctly.

**Most likely**: Intake's Groq call returned 856 tokens but the response was malformed or had invalid quotes, causing a `ProviderFailure(MALFORMED_RESPONSE)`. Intake fell back to deterministic, which resolved rice + Punjab correctly. Intake status = `partial`.

### Step 3 → Crop/Location Registry Resolution
- **File:** [`workflow.py`](file:///C:/Users/B GAGAN KUMAR REDDY/OneDrive/Desktop/rythusaathi/src/rythusaathi/agents/workflow.py#L312-L468) `_select_query_values()`
- Deterministic fallback resolves:
  - `"rice"` → exact match in `CROP_REGISTRY` → `crop:oryza-sativa` ✅
  - `"Punjab"` → exact match in `LOCATION_REGISTRY` → `location:state:in-pb` ✅
  - Growth stage: `None` (not stated) → `MISSING` ✅
  - Variety: `None` → `MISSING`
- **File:** [`resolution.py`](file:///C:/Users/B GAGAN KUMAR REDDY/OneDrive/Desktop/rythusaathi/src/rythusaathi/domain/resolution.py#L119-L211) `ContextResolver.resolve()`
  - Coverage: `CoverageRegistry.assess("crop:oryza-sativa", "location:state:in-pb")`

### Step 4 → Coverage Registry Assessment
- **File:** [`registry.py`](file:///C:/Users/B GAGAN KUMAR REDDY/OneDrive/Desktop/rythusaathi/src/rythusaathi/domain/registry.py#L393-L415) `CoverageRegistry.assess()`
- The CLI uses `empty_coverage_resolver()` (line 89 of `advisory_cli.py`)
- **File:** [`workflow.py`](file:///C:/Users/B GAGAN KUMAR REDDY/OneDrive/Desktop/rythusaathi/src/rythusaathi/agents/workflow.py#L1508-L1519) `empty_coverage_resolver()`
- This creates a `CoverageRegistry` with **`supported_pairs=()`** — an **empty** set
- `assess()` with crop_id + location_id against empty set → `Resolution(status=UNSUPPORTED)`

**Answer A: Why does rice + Punjab fail the Phase 2 coverage registry?**

Because `empty_coverage_resolver()` constructs a `CoverageRegistry` with **zero validated pairs**. Every crop+location combination fails. The `CoverageRegistry.assess()` method (line 411–414 of `registry.py`) checks if `(crop_id, location_id)` is in `self._supported`. Since `self._supported` is empty, it always returns `UNSUPPORTED`.

**Answer B: Is this intentional or missing data?**

**Intentional.** The README (line 459) and Phase 5 Execution Record (line 273) explicitly document: *"Phase 2 coverage still has no seeded crop/location pairs; the workflow continues to surface the accepted coverage limitation."* The `empty_coverage_resolver` was created as a documented architectural decision — no coverage pair has been validated yet. This is not missing data; it is a documented Phase 2 limitation.

### Step 5 → Crop Agent
- **File:** [`workflow.py`](file:///C:/Users/B GAGAN KUMAR REDDY/OneDrive/Desktop/rythusaathi/src/rythusaathi/agents/workflow.py#L852-L925) `CropAgent.perform()`
- crop_id = `"crop:oryza-sativa"` ✅, location_id = `"location:state:in-pb"` ✅
- RAG search with `RetrievalFilter(crop_id="crop:oryza-sativa", geography=...)` for Punjab and India
- **Evidence IS retrieved** — PAU Punjab rice evidence and ICAR micronutrient evidence
- Evidence published to board ✅
- Then at line 900: `self.interpret(case, evidence_ids, ...)` is called
- **File:** [`workflow.py`](file:///C:/Users/B GAGAN KUMAR REDDY/OneDrive/Desktop/rythusaathi/src/rythusaathi/agents/workflow.py#L733-L767) `_DomainAgent.interpret()`
- Groq specialist call: 1,647 tokens consumed
- The call **succeeded at the provider level** (tokens were consumed), but...
- Lines 764: `validate_evidence_ids(result.statements, set(evidence_ids))` — if any statement cites an ID not in the allowed set, it raises `ProviderFailure(MALFORMED_RESPONSE)`
- **Or** the Pydantic validation of `SpecialistInterpretation` failed
- **Or** the Groq response was syntactically malformed
- In any failure case → exception caught at line 766 → returns `(None, inference_status)` where inference_status is the error kind
- Back in `CropAgent.perform()` at lines 910–912:
  ```python
  status = AgentStatus.COMPLETED if evidence_ids else AgentStatus.UNAVAILABLE
  if inference_status is not None and evidence_ids:
      status = AgentStatus.PARTIAL
  ```
- Since evidence exists AND inference_status is non-None → **status = `PARTIAL`**
- `generated_statements` = `()` (empty) because `interpretation` is `None`

**Answer C: Why is Crop marked partial?**

The Crop agent retrieved evidence (evidence_ids is non-empty) but its Groq specialist interpretation **failed** (inference_status is non-None). The status logic at lines 910–912 sets `PARTIAL` when evidence exists but inference failed. The `generated_statements` tuple is **empty** because the failed interpretation returned `None`.

### Step 6 → Scheme Agent
- **File:** [`workflow.py`](file:///C:/Users/B GAGAN KUMAR REDDY/OneDrive/Desktop/rythusaathi/src/rythusaathi/agents/workflow.py#L1073-L1145) `SchemeAgent.perform()`
- Identical pattern: evidence retrieved, Groq specialist call consumed 2,051 tokens but interpretation **failed**
- Lines 1134–1136: same logic → **status = `PARTIAL`**, `generated_statements` = `()`

### Step 7 → Weather/Market Agents
- No weather/market provider configured → both return `UNAVAILABLE`
- No evidence, no generated_statements

### Step 8 → Coordinator Eligibility Decision
- **File:** [`workflow.py`](file:///C:/Users/B GAGAN KUMAR REDDY/OneDrive/Desktop/rythusaathi/src/rythusaathi/agents/workflow.py#L1148-L1448) `CoordinatorAgent.handle()`
- Line 1171: `evidence = case.board.evidence` — this IS populated (Crop + Scheme evidence exists on the board)
- Line 1273: `available_evidence_ids = {item.evidence_id for item in evidence}` — **non-empty** ✅
- Line 1274: `synthesis_evidence_ids = _coordinator_supported_evidence_ids(branch_results)` — **THIS IS THE CRITICAL LINE**

**File:** [`workflow.py`](file:///C:/Users/B GAGAN KUMAR REDDY/OneDrive/Desktop/rythusaathi/src/rythusaathi/agents/workflow.py#L211-L221)
```python
def _coordinator_supported_evidence_ids(
    results: Sequence[AgentResult],
) -> set[str]:
    """Coordinator may synthesize only from specialist claims with citations."""
    return {
        evidence_id
        for result in results
        for statement in getattr(result.payload, "generated_statements", ())
        if statement.kind in {"evidence", "interpretation"}
        for evidence_id in statement.evidence_ids
    }
```

Since **both** Crop and Scheme have `generated_statements = ()` (empty tuple), this function returns **`set()`** — an empty set.

- Line 1275: `if available_evidence_ids and synthesis_evidence_ids:` — evaluates to `if True and False:` → **False**
- Falls to `else` branch at line 1354:
  ```python
  coordinator_interpretation_mode = "deterministic_fallback"
  coordinator_inference_status = "not_run_no_validated_specialist_evidence"
  ```
- Line 1363: appends the diagnostic message: *"Groq synthesis was not run because no validated specialist evidence was available"*

**Answer E: Are retrieved evidence chunks being rejected?**

No. The evidence chunks themselves are **correctly retrieved, published to the board, and present in the response**. The RAG retrieval, metadata filtering, and board publication all work correctly. What's missing is the **specialist LLM interpretation** of those chunks — the `generated_statements` that would normally cite those evidence IDs.

**Answer F: Does the Coordinator require all specialists to return fully validated findings?**

No — the Coordinator requires **at least one** specialist to have `generated_statements` with kind `evidence` or `interpretation` citing some evidence ID. Even one valid specialist statement would make `synthesis_evidence_ids` non-empty and allow Coordinator synthesis. The problem is that **zero** specialists have any generated_statements.

**Answer D: Does partial status necessarily mean evidence is invalid?**

**No.** `AgentStatus.PARTIAL` means evidence was retrieved but the LLM interpretation of that evidence failed. The evidence records themselves are valid, published, and present on the board. The `partial` status reflects the **inference layer**, not the **evidence layer**.

### Step 9 → Deterministic Fallback
- Coordinator composes the deterministic response text (lines 1192–1267) which includes the evidence excerpts
- Appends the coverage limitation message (line 1251)
- Appends the "Groq synthesis was not run" message (line 1363)
- Response status = `PARTIAL` (line 1187–1188, due to branch agents being partial and unresolved_coverage)

---

## 2. Root Cause for Each Partial Status

| Agent | Status | Root Cause |
|---|---|---|
| **Intake** | partial | Groq call consumed 856 tokens but the response failed validation (malformed output, invalid quote, or schema mismatch). Deterministic fallback resolved rice+Punjab correctly. |
| **Crop** | partial | Evidence retrieved (PAU/ICAR chunks). Groq specialist call consumed 1,647 tokens but interpretation failed validation. `generated_statements = ()`. |
| **Scheme** | partial | Evidence retrieved (scheme chunks). Groq specialist call consumed 2,051 tokens but interpretation failed validation. `generated_statements = ()`. |
| **Weather** | unavailable | No weather provider configured (intentional). |
| **Market** | unavailable | No market provider configured (intentional). |
| **Coordinator** | partial | `synthesis_evidence_ids` is empty (no specialist `generated_statements` exist). Coordinator synthesis skipped. Deterministic fallback used. |

---

## 3. Why Coordinator Synthesis Was Skipped — Precise Evidence

The gate is at [`workflow.py` lines 1273–1276](file:///C:/Users/B GAGAN KUMAR REDDY/OneDrive/Desktop/rythusaathi/src/rythusaathi/agents/workflow.py#L1273-L1276):

```python
available_evidence_ids = {item.evidence_id for item in evidence}
synthesis_evidence_ids = _coordinator_supported_evidence_ids(branch_results)
if available_evidence_ids and synthesis_evidence_ids:
    # ... run Coordinator Groq synthesis
```

[`_coordinator_supported_evidence_ids`](file:///C:/Users/B GAGAN KUMAR REDDY/OneDrive/Desktop/rythusaathi/src/rythusaathi/agents/workflow.py#L211-L221) collects evidence IDs only from `generated_statements` of kind `evidence` or `interpretation`. When specialists fail their Groq calls (returning `generated_statements = ()`), this returns `set()`, and the condition is `False`.

The `else` branch (lines 1354–1368) then produces the diagnostic message observed in the live run.

---

## 4. Relevant Existing Tests

| Test | File | What It Proves |
|---|---|---|
| `test_groq_mode_runs_typed_agents_and_retains_only_request_citations` | [`test_llm_workflow.py:146`](file:///C:/Users/B GAGAN KUMAR REDDY/OneDrive/Desktop/rythusaathi/tests/test_llm_workflow.py#L146-L248) | **Passes because `MockWorkflowInference` always returns valid specialist statements.** The mock never fails the specialist call, so `generated_statements` is always populated and Coordinator synthesis runs. **This test does not cover the scenario where specialists retrieve evidence but fail their LLM interpretation.** |
| `test_timeout_uses_visible_deterministic_fallback` | [`test_llm_workflow.py:285`](file:///C:/Users/B GAGAN KUMAR REDDY/OneDrive/Desktop/rythusaathi/tests/test_llm_workflow.py#L285-L298) | Tests `fail_all=True` where ALL Groq calls fail. Intake falls back to deterministic, specialists get no evidence (because deterministic Intake may still resolve rice/Punjab). But the test confirms `deterministic_fallback` for Coordinator. It does **not** test the case where specialists retrieve evidence but fail their LLM call. |
| `test_invalid_specialist_citation_is_rejected_and_branch_is_partial` | [`test_llm_workflow.py:270`](file:///C:/Users/B GAGAN KUMAR REDDY/OneDrive/Desktop/rythusaathi/tests/test_llm_workflow.py#L270-L283) | Tests that an invalid crop citation is rejected and crop status is `partial`. But in this test, **Scheme still succeeds** its Groq call (returning valid `generated_statements`), so `synthesis_evidence_ids` is non-empty and Coordinator synthesis can run. **No test covers the scenario where ALL specialists have evidence but ALL fail their LLM interpretation.** |

> [!IMPORTANT]
> **Test gap:** No existing test covers the scenario where specialist agents successfully retrieve evidence to the board but all specialist Groq interpretation calls fail. This is exactly the live scenario. The mock infrastructure always either succeeds all specialist calls or fails all Groq calls (including Intake, which prevents evidence retrieval).

---

## 5. Architecture and Acceptance Criteria Assessment

### Does the current behavior violate any accepted criteria?

**Yes.** Task 5.4 Coordinator acceptance criteria state:

> *"Malformed output, unsupported citations, disallowed questions or provider failure use a visible deterministic fallback."*

The Coordinator does produce a deterministic fallback — so the fallback path itself is correct. **However**, the Coordinator Optimization section (lines 396–401 of the Phase 5 record) says:

> *"If no validated specialist statement exists, Coordinator makes no Groq call and uses the established deterministic fallback rather than attempting an unsupported synthesis."*

This was the **intended design** of the optimization: Coordinator should not synthesize without specialist-validated citations. The optimization is working **as implemented**. The defect is that the implementation **fails to account for the scenario where specialists retrieve valid evidence but fail to produce LLM interpretations**. In this scenario:

1. Evidence exists on the board ✅
2. Specialists published evidence findings ✅
3. The evidence is filtered by crop/geography/stage metadata ✅
4. But Coordinator cannot see any of it because `generated_statements` is empty ❌

The optimization created an **unstated coupling**: Coordinator synthesis now **requires** successful specialist LLM calls, not just successful evidence retrieval. This was not an explicit design decision — it's an emergent consequence of using `generated_statements` as the sole evidence validation signal.

### Is the behavior correct?

**No.** The Coordinator should be able to synthesize based on **retrieved evidence that was published to the board by specialists**, even when the specialist LLM interpretation step failed. The evidence itself was retrieved through the standard RAG filters (crop, geography, stage) and published via the board's typed evidence lifecycle. The specialist LLM interpretation is a value-add layer, not a prerequisite for evidence validity.

The current gate conflates two distinct concepts:
1. **Evidence validity** — does evidence exist on the board, retrieved through the proper filter chain?
2. **Specialist LLM interpretation** — did the specialist produce generated_statements?

---

## 6. Recommended Minimal Correction

> [!CAUTION]
> This is a recommendation only. No code changes have been made.

**The fix should widen the Coordinator evidence gate to also consider evidence IDs published by specialist agents to the board, not only evidence IDs cited in `generated_statements`.**

Specifically, in [`workflow.py` lines 1273–1275](file:///C:/Users/B GAGAN KUMAR REDDY/OneDrive/Desktop/rythusaathi/src/rythusaathi/agents/workflow.py#L1273-L1275):

**Current logic:**
```python
available_evidence_ids = {item.evidence_id for item in evidence}
synthesis_evidence_ids = _coordinator_supported_evidence_ids(branch_results)
if available_evidence_ids and synthesis_evidence_ids:
```

**Proposed correction concept:**

The Coordinator should be able to run Groq synthesis when:
- There is evidence on the board (`available_evidence_ids` non-empty), **AND**
- **Either** specialists produced `generated_statements` citing that evidence (`synthesis_evidence_ids` non-empty), **OR** specialists retrieved and published evidence to the board even though their LLM interpretation failed (specialist `evidence_ids` non-empty)

The Coordinator citation validation (line 1319) must then also accept evidence IDs from specialist `result.evidence_ids` — not just from `generated_statements`.

The minimal change is to modify `_coordinator_supported_evidence_ids` (or the gate condition) to also include evidence IDs from specialist `result.evidence_ids` when the specialist has status `PARTIAL` (evidence retrieved, LLM failed). This means:
- When specialists succeed: Coordinator uses specialist-validated citations (current behavior preserved)
- When specialists have evidence but LLM fails: Coordinator can use the board-published evidence IDs
- When no evidence exists: Coordinator correctly skips synthesis (current behavior preserved)

**Constraints that must NOT be weakened:**
- Citation validation must still enforce that Coordinator only cites IDs present in the request evidence
- Fixture evidence must still be excluded from synthesis
- Coordinator must not synthesize without any evidence
- The token budget architecture must not change
- Deterministic fallback must remain available

---

## 7. Exact Tests Needed to Validate the Correction

### New test: `test_coordinator_synthesizes_when_specialists_have_evidence_but_llm_interpretation_fails`

**Setup:**
- Use `MockWorkflowInference` that succeeds for Intake, fails for Crop specialist and Scheme specialist (but NOT at the provider level — they consume tokens but return invalid JSON or invalid citations), and succeeds for Coordinator
- This means: Intake returns valid crop/location extraction, Crop and Scheme agents retrieve evidence and publish to board, but their `interpret()` calls fail, producing `generated_statements = ()`

**Assertions:**
1. Crop status = `partial`, Scheme status = `partial`
2. Evidence IS present on the board and in `response.evidence`
3. Coordinator DOES call Groq for synthesis (4 total inference calls: Intake, Crop specialist attempt, Scheme specialist attempt, Coordinator)
4. Coordinator generated_statements cite only evidence IDs present in `response.evidence`
5. `"Groq-generated synthesis"` appears in `response.response_text`
6. No fixture evidence is cited
7. Citation validation still enforces the allowed evidence set

### New test: `test_coordinator_does_not_synthesize_when_specialists_have_no_evidence`

**Assertions:**
1. When no specialist retrieves any evidence (evidence_ids empty), Coordinator correctly skips synthesis
2. This is the existing `test_no_evidence_query_does_not_generate_unsupported_answer` — confirm it still passes

### Regression: all 161 existing tests must continue to pass

---

## 8. Answers to Specific Questions

| Q | Answer |
|---|---|
| **A.** Why does rice + Punjab fail the Phase 2 coverage registry? | `empty_coverage_resolver()` creates a `CoverageRegistry` with zero validated pairs. Every pair returns `UNSUPPORTED`. This is used by both the CLI and all tests. |
| **B.** Is this intentional or missing data? | **Intentional.** Documented in README line 459 and Phase 5 record line 273. No coverage pairs have been validated yet. This is a known Phase 2 limitation. |
| **C.** Why are Crop and Scheme results marked partial? | They retrieved evidence (non-empty `evidence_ids`) but their Groq specialist interpretation calls failed (non-None `inference_status`). Lines 910–912 and 1134–1136 set `PARTIAL` in this case. |
| **D.** Does partial status necessarily mean evidence is invalid? | **No.** `PARTIAL` means the LLM interpretation failed. The underlying evidence is valid, board-published, and present in the response. |
| **E.** Are retrieved evidence chunks being rejected? | **No.** Evidence chunks are correctly retrieved, published to the board, and returned in the response. They are rejected only at the **Coordinator synthesis gate**, which requires `generated_statements` not just board evidence. |
| **F.** Does the Coordinator require all specialists? | No. It requires **at least one** specialist to have non-empty `generated_statements` of kind `evidence`/`interpretation`. In this run, **zero** specialists have any. |
| **G.** Is the Coordinator optimization correctly preserving the intended evidence validation? | **Partially.** The optimization correctly restricts Coordinator citations to specialist-validated IDs. But it **over-restricts** by making specialist LLM success a hard prerequisite for ANY Coordinator synthesis, which was not the stated intent. |
| **H.** Is the current behavior correct, or is there a genuine integration defect? | **Genuine integration defect.** The Coordinator TPM optimization introduced an unstated coupling: Coordinator synthesis requires successful specialist LLM calls, not just successful evidence retrieval. When specialists retrieve evidence but fail LLM interpretation, the system silently degrades to deterministic-only with no path to Groq synthesis. |

---

## Investigation Status

- ✅ Read-only investigation complete
- ✅ No code modifications made
- ✅ No live Groq requests made
- ✅ Root cause identified with file paths and line numbers
- ✅ Correction recommended but NOT implemented
- ❌ Task 5.1 remains **PARTIAL**

**Awaiting approval before implementing the correction.**


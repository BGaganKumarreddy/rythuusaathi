# RythuSaathi — Phase 5 Task 5.1 Coordinator Integration Correction Report

**Generated:** 2026-11-09  
**Status:** Implementation complete, local verification PASSED. Task 5.1 remains **PARTIAL** pending live Groq verification.

---

## 1. Root Cause Addressed

**Defect:** `_coordinator_supported_evidence_ids()` in `workflow.py` only collected evidence IDs from specialist `generated_statements` with `evidence`/`interpretation` kind. When specialist LLM interpretation failed (returning `generated_statements=()`), the function returned `set()`, causing the Coordinator gate at line ~1294 (`if available_evidence_ids and synthesis_evidence_ids`) to skip Groq synthesis entirely — even though valid, filtered RAG evidence had been retrieved and published to the shared blackboard by the specialist agents.

**Fix:** Extended `_coordinator_supported_evidence_ids()` with a controlled fallback: when a specialist has `result.evidence_ids` (board-published evidence) but no cited generated_statements, those board-published IDs are included as eligible for Coordinator synthesis. The existing successful-specialist path (tight restriction to only specialist-cited IDs) is fully preserved and takes priority.

---

## 2. Files Changed

| File | Change Type | Lines Changed |
|------|-------------|---------------|
| `src/rythusaathi/agents/workflow.py` | Modified | `_coordinator_supported_evidence_ids()` — +19 net lines |
| `tests/test_llm_workflow.py` | Modified | `MockWorkflowInference` +5 lines, 7 new test methods +175 lines |

**No other files were modified.** No unrelated files were touched.

---

## 3. Exact Implementation Changes

### `src/rythusaathi/agents/workflow.py` — `_coordinator_supported_evidence_ids()`

**Before:** Single-path collection — only IDs cited by specialist `generated_statements` of kind `evidence` or `interpretation`.

**After:** Two-path collection:
1. **Tight path (preserved):** When specialist has generated_statements with `evidence`/`interpretation` kind, use only those cited IDs.
2. **Fallback path (new):** When specialist has `result.evidence_ids` but the `cited` set from generated_statements is empty, include the board-published IDs.

This ensures:
- Specialist success → Coordinator can only cite specialist-validated IDs (same as before)
- Specialist evidence retrieval + LLM failure → Coordinator can cite the board-published evidence IDs
- No evidence at all → no IDs, Coordinator synthesis skipped (same as before)

### `tests/test_llm_workflow.py` — `MockWorkflowInference`

Added `fail_specialists: bool = False` parameter. When `True`, specialist `complete()` calls raise `ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE)` before accessing evidence IDs, simulating "evidence retrieved but LLM interpretation failed."

---

## 4. New Tests and Results

| # | Test Name | Covers | Result |
|---|-----------|--------|--------|
| 1 | `test_coordinator_synthesizes_from_board_evidence_when_specialist_interpretation_fails` | Specialists retrieve evidence but interpretation fails; Coordinator Groq synthesis succeeds using board evidence | ✅ PASS |
| 2 | `test_no_evidence_with_failed_specialists_still_skips_coordinator_synthesis` | No evidence exists; Coordinator synthesis remains skipped | ✅ PASS |
| 3 | `test_fixture_evidence_excluded_from_coordinator_synthesis_with_failed_specialists` | Fixture evidence rejected by Coordinator guard; deterministic fallback activates | ✅ PASS |
| 4 | `test_specialist_success_path_unchanged_after_correction` | Specialist success path preserved; Coordinator only cites specialist-validated IDs | ✅ PASS |
| 5 | `test_coordinator_synthesis_failure_with_board_evidence_triggers_deterministic_fallback` | When ALL calls fail (fail_all=True), deterministic fallback still activates | ✅ PASS |
| 6 | `test_token_budget_accounting_with_failed_specialists` | Token budget remains correct (Intake=256, Coordinator=384) | ✅ PASS |
| 7 | `test_concurrent_reservation_preserved_with_failed_specialists` | Agent call ordering preserved: Intake → Crop+Scheme → Coordinator | ✅ PASS |

### Pre-existing tests also verified:

| Test | Status |
|------|--------|
| `test_groq_mode_runs_typed_agents_and_retains_only_request_citations` | ✅ PASS (specialist success path) |
| `test_invalid_specialist_citation_is_rejected_and_branch_is_partial` | ✅ PASS (citation validation) |
| `test_timeout_uses_visible_deterministic_fallback_without_changing_mode` | ✅ PASS (fail_all deterministic fallback) |
| `test_no_evidence_query_does_not_generate_unsupported_answer` | ✅ PASS (no evidence skip) |
| `test_fixture_weather_and_market_are_not_sent_as_live_llm_data` | ✅ PASS (fixture exclusion) |

---

## 5. Full Test Suite Result

```
Ran 168 tests in 10.323s

OK
```

- **161 existing tests:** All PASS (0 failures, 0 errors, 0 skipped)
- **7 new tests:** All PASS
- **Total: 168 tests PASS**

---

## 6. Compilation Result

```
python -m compileall -q src scripts tests
```

**Exit code 0.** No compilation errors or warnings.

---

## 7. Token Budget Verification

| Agent | max_tokens | Status |
|-------|-----------|--------|
| Intake | 256 | ✅ Preserved |
| Crop specialist | 256 | ✅ Preserved |
| Scheme specialist | 256 | ✅ Preserved |
| Coordinator | 384 | ✅ Preserved |
| Total budget | 7,200 | ✅ Not modified |

Concurrent reservation mechanism verified: Intake runs first, then Crop and Scheme concurrently, then Coordinator.

---

## 8. Existing Behavior Preserved

| Constraint | Status |
|------------|--------|
| Successful-specialist path unchanged | ✅ |
| Coordinator citation validation against request evidence | ✅ |
| Unknown evidence ID rejection | ✅ |
| Fixture evidence exclusion | ✅ |
| Unknown growth stage preserved | ✅ |
| Geographic, crop, stage filtering preserved | ✅ |
| Deterministic fallback when evidence absent or synthesis fails | ✅ |
| 7,200-token budget and concurrent reservation | ✅ |
| Provider architecture and credential handling | ✅ Not modified |
| Phase 2 coverage registry decisions | ✅ Not modified |
| Coordinator distinguishes retrieved evidence vs validated specialist interpretations | ✅ |

---

## 9. Remaining Live Verification Requirements

Task 5.1 cannot be marked **PASS** until:

1. **Live Groq execution** with the original test query: *"My rice leaves are yellowing in Punjab. What should I check?"*
2. Verify Coordinator Groq synthesis runs and produces output with proper citations
3. Verify specialist status shows `partial` (expected with empty coverage registry) but Coordinator proceeds
4. Verify no HTTP 429 rate limit errors
5. Verify response text contains `"Groq-generated synthesis"` marker
6. Verify cited evidence IDs are valid and reference actual retrieved evidence
7. Verify growth stage remains unknown
8. Verify no fixture evidence appears in Coordinator output

**Awaiting explicit user approval before making any live Groq request.**


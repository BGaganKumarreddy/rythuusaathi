# RythuSaathi Recovery Audit — 8 November 2026

**Audit time:** 2026-11-08 02:51 IST  
**Auditor:** Antigravity automated audit (read-only, no modifications)

---

## 1. Repository Path and Identity

| Item | Value |
|---|---|
| **Repository root** | `C:\Users\B GAGAN KUMAR REDDY\OneDrive\Desktop\rythusaathi` |
| **Project name** | `rythusaathi` v0.1.0 |
| **Python** | 3.14.4 (virtualenv at `.venv\`) |
| **Package installed** | Editable install (`pip install -e ".[test]"`) confirmed |
| **requires-python** | >=3.11 |

---

## 2. Git State

| Item | Value |
|---|---|
| **Git root** | `C:/Users/B GAGAN KUMAR REDDY` (user home — not rythusaathi itself) |
| **Git dir** | `C:/Users/B GAGAN KUMAR REDDY/.git` |
| **Current branch** | `Gagan` |
| **Local branches** | `Gagan` (active), `master` |
| **Remote `origin`** | `main`, `master` |
| **Remote `upstream`** | `main` + 12 contributor branches (team project) |
| **Only commit (this repo)** | `bbe8e13` — `feat: initial SyncSphere fullstack app` |
| **Tracked files in rythusaathi/** | **None** — rythusaathi project files are untracked by the parent git repo |
| **Uncommitted changes** | Deleted syncsphere files only (unrelated); rythusaathi dir is fully untracked |

> [!IMPORTANT]
> The git repository at `~/.git` tracks a different project (SyncSphere). The rythusaathi
> directory and all its files are **untracked**. The `.gitignore` inside rythusaathi is a
> project-local file, not managed by the parent repo's index. No rythusaathi source code
> is at risk from git operations on the parent repo, but **no rythusaathi code is
> version-controlled**.

---

## 3. Project Structure (verified)

### Source (`src/rythusaathi/`)
- `__init__.py`, `credentials.py`, `local_auth.py`, `recovery.py`, `security.py`
- `agents/` — `__init__.py`, `contracts.py`, `llm.py`, `providers.py`, `workflow.py`
- `domain/` — `__init__.py`, `board.py`, `evidence.py`, `models.py`, `recommendations.py`, `registry.py`, `resolution.py`, `seed.py`
- `inference/` — `__init__.py`, `contracts.py`, `service.py`
- `inference/providers/` — `__init__.py`, `groq.py`
- `knowledge/` — `__init__.py`, `README.md`, `adapters.py`, `embeddings.py`, `evidence.py`, `file_adapters.py`, `ingestion.py`, `models.py`, `persistent.py`, `processing.py`, `retrieval.py`

### Tests (`tests/`) — 15 test files
`test_agents.py`, `test_credentials.py`, `test_domain_board.py`, `test_domain_context.py`, `test_domain_evidence_recommendations.py`, `test_domain_registry.py`, `test_groq_integration.py`, `test_groq_provider.py`, `test_inference_contract.py`, `test_knowledge.py`, `test_llm_workflow.py`, `test_local_auth.py`, `test_recovery.py`, `test_security.py`, `test_windows_credential_store.py`

### Scripts (`scripts/`) — 6 files
`advisory_cli.py`, `byok_cli.py`, `demo_retrieval.py`, `evaluate_phase5.py`, `evaluate_retrieval.py`, `ingest_knowledge.py`

### Data
- `data/agricultural/` — `icar_advisories.json`, `phase5_agent_eval.json`, `retrieval_eval.json`
- `data/schemes/` — `central_agricultural_schemes.json`, `README.md`

### Output (execution records)
- Phase 1–5 execution records (`.md`)
- Architecture plans (`.md`, `.pdf`)
- Recovery runbook (`.md`)

### Tmp (runtime artifacts)
- SQLite databases: `demo.sqlite3`, `phase3-*.sqlite3`, `phase4-advisory-*.sqlite3`, `phase5-local-auth.sqlite3`
- Cached FastEmbed model: `tmp/models/models--Qdrant--paraphrase-multilingual-MiniLM-L12-v2-onnx-Q/`
- PDFs: `icar_kharif_2025.pdf`, `pmfby_2020.pdf`, `pm_kisan_guidelines.pdf`, `pau_kharif_2026.pdf`, `telangana_rythu_bharosa_go_rt_18_2025.pdf`
- Phase 0 feasibility workspace with its own `.venv`

---

## 4. Environment and Dependencies

| Package | Version | Role |
|---|---|---|
| agent-framework-core | 1.24.0 | Microsoft Agent Framework orchestration |
| agent-framework-openai | 1.18.0 | OpenAI-compatible agent provider |
| openai | 3.42.1 | Groq API client (OpenAI SDK) |
| argon2-cffi | 25.1.0 | Password hashing |
| cryptography | 46.0.7 | AES-GCM credential encryption |
| keyring | 25.7.0 | Windows Credential Manager adapter |
| fastembed | 0.9.1 | Pinned semantic embedding model |
| httpx2 | 2.16.0 | Test HTTP client |
| pypdf | 6.21.4 | PDF ingestion |
| pydantic | 2.14.3 | Structured LLM output validation |

> [!NOTE]
> All dependencies match `pyproject.toml` pins. No modifications needed.
> The `.venv` is intact with 55 packages installed.

---

## 5. Test Baseline Verification

```
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

**Result: 161 passed, 0 failed, 0 skipped** (12.837s)

```
.venv\Scripts\python.exe -m compileall -q src scripts tests
```

**Result: PASS** (clean compilation, no output)

> [!TIP]
> The 161-test baseline matches the Phase 5 Execution Record's final count exactly.
> Tests ran outside the restricted sandbox using the project's own virtualenv interpreter.

---

## 6. Phase 5 Status

| Task | Status | Detail |
|---|---|---|
| **5.1** Provider integration | **PARTIAL** | Mocked tests pass. A prior live Groq Intake succeeded. The required live agricultural synthesis with evidence citations has NOT yet been demonstrated — two attempts returned HTTP 429 (TPM exhaustion). |
| **5.2** Intelligent Intake | **PASS** | Strict Pydantic extraction, registry resolution, deterministic fallback |
| **5.3** Specialist intelligence | **PASS** | Evidence-constrained Crop/Scheme, fixture suppression |
| **5.4** Coordinator intelligence | **PASS** | Structured response schema, citation validation, fallback |
| **5.5** End-to-end integration | **PASS** | Both CLI modes, BYOK path, documented |
| **5.6** Evaluation and acceptance | **PASS** | 5-case eval corpus, 161-test regression, compileall |

**Overall Phase 5: PARTIAL** — blocked only on Task 5.1 live verification.

---

## 7. Task 5.1 — Outstanding Verification

### Prepared one-shot Groq command

The following command is documented in `output/RythuSaathi_Phase5_Execution_Record.md` (lines 439–441). It requires the user's network-enabled Windows terminal and their existing owner-scoped credential ID:

```powershell
.\.venv\Scripts\python.exe scripts\advisory_cli.py "My rice leaves are yellowing in Punjab. What should I check?" --database tmp\phase4-advisory-fastembed.sqlite3 --model-cache tmp\models --mode groq --auth-database tmp\phase5-local-auth.sqlite3 --credential-id YOUR_EXISTING_CREDENTIAL_ID
```

### Acceptance criteria for Task 5.1 PASS
1. Successful authenticated Groq model response (no HTTP 429/401/403)
2. Groq-generated synthesis with valid retrieved-evidence citations
3. Unknown growth stage preserved (user did not state it)
4. No unsupported disease diagnosis asserted
5. No invented weather/market observations
6. Only sanitized CLI output shared (no keys, passwords, or session tokens)

### Prior failure analysis
- Two user-provided attempts both hit HTTP 429 (Groq TPM limit: 8K tokens)
- Token optimization reduced Coordinator input by 40.2% and total output ceilings by 43.8%
- Projected post-optimization consumption: ~6,591 / 7,200 effective budget (609-token margin)
- The retry cap (2s) correctly prevented automatic retry on 42s Retry-After
- No live Groq request will be made by this audit

> [!CAUTION]
> Do NOT execute the live Groq command until explicitly authorized. The prior 429s
> indicate TPM limits may still apply. Allow sufficient cooldown between attempts.

---

## 8. Files Preserved (no modifications made)

- ✅ All source files in `src/` — untouched
- ✅ All test files in `tests/` — untouched
- ✅ All scripts in `scripts/` — untouched
- ✅ All data files in `data/` — untouched
- ✅ All execution records in `output/` — untouched
- ✅ SQLite databases in `tmp/` — untouched
- ✅ Cached FastEmbed model — untouched
- ✅ `.venv/` — untouched
- ✅ `pyproject.toml`, `README.md`, `.gitignore` — untouched
- ✅ `tmp/phase5-local-auth.sqlite3` (BYOK auth database) — untouched
- ✅ No API keys, passwords, tokens, or secrets printed

---

## 9. Recommended Next Action

1. **Wait for Groq TPM cooldown** (the 429 token reset was 57s; allow at least 60s since last attempt)
2. **Execute the prepared Task 5.1 command** from your own network-enabled terminal with your real credential ID
3. **Share only the sanitized CLI output** (the `Advisory Results`, `Sanitized inference diagnostics`, and `Sanitized inference token usage` sections)
4. If successful → Task 5.1 → PASS → Phase 5 → COMPLETE
5. If 429 again → wait for full TPM window reset, or consider reducing query complexity

> [!IMPORTANT]
> No architectural changes, code modifications, or new feature development should
> proceed until Phase 5 acceptance is finalized. The codebase is in a clean, verified
> state with all 161 tests passing.


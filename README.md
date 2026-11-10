# RythuSaathi

Production code uses a `src/` package layout. The accepted foundation includes
the backend inference provider boundary, local-first Phase 1 identity and BYOK
credential lifecycle, Phase 2 shared-intelligence contracts, Phase 3 local
SQLite RAG, and the Phase 4 six-agent advisory workflow. Phase 0 feasibility artifacts remain isolated under
`tmp/phase0_feasibility/`.

```text
src/rythusaathi/inference/       Provider contracts and sanitized errors
src/rythusaathi/inference/providers/  Provider adapters (Groq first)
src/rythusaathi/inference/service.py Authenticated stored-credential inference boundary
src/rythusaathi/security.py      Verified-identity and BYOK authorization contracts
src/rythusaathi/local_auth.py   Argon2id local auth and opaque SQLite sessions
src/rythusaathi/credentials.py  AES-GCM envelope and SQLite credential lifecycle
src/rythusaathi/recovery.py    Fail-closed offline SQLite restore
src/rythusaathi/domain/         Request context, registries, resolver, board, evidence and recommendations
src/rythusaathi/agents/         Six typed agents and Microsoft Agent Framework orchestration
scripts/advisory_cli.py         Local CLI over the shared six-agent workflow
scripts/byok_cli.py             Local user and Windows-protected Groq BYOK setup
scripts/evaluate_phase5.py      Bounded mock-based Phase 5 evaluation
tests/                           Provider, identity, crypto and lifecycle tests
tests/test_agents.py             Phase 4 workflow, provider and evidence integration tests
tests/test_domain_*.py           Phase 2 context, registry, board and contract tests
tmp/phase0_feasibility/          Disposable runtime, graph and provider spikes
output/                          Approved architecture and execution plans
```

The provider slice uses the versions exercised by the Phase 0 compatibility
workspace. To install it and run the unit tests in a clean environment:

```powershell
python -m pip install -e ".[test]"
python -m unittest discover -s tests -v
```

The Phase 4 CLI builds a local SQLite index with the pinned Phase 3 FastEmbed
model and uses unavailable-by-default weather and market adapters:

```powershell
python scripts/advisory_cli.py "My rice leaves are turning yellow in Punjab. What should I do, and are there government schemes?"
```

Use `--lexical` for a deterministic offline hashing baseline. Hash embeddings
are not semantic embeddings. The pinned model must be cached for offline
FastEmbed use; its first install may require network access. Deterministic
weather/market fixtures are available for tests only and are clearly labeled
as synthetic.

Phase 5 keeps deterministic behavior as the default. Groq mode is opt-in and
uses the same Phase 4 graph, Phase 3 SQLite index, board and source filters. On
Windows, create a local tenant/user and store a Groq key through the existing
credential service and Windows Credential Manager adapter:

```powershell
python scripts/byok_cli.py --database tmp/phase5-local-auth.sqlite3 register
python scripts/byok_cli.py --database tmp/phase5-local-auth.sqlite3 add-groq
python scripts/byok_cli.py --database tmp/phase5-local-auth.sqlite3 list-groq
python scripts/advisory_cli.py --mode groq `
  --auth-database tmp/phase5-local-auth.sqlite3 `
  --credential-id YOUR_OWNER_SCOPED_CREDENTIAL_ID `
  "My rice leaves are yellowing in Punjab. What should I check?"
```

The setup commands prompt for passwords and the API key without echoing them.
The advisory command prompts for the user's tenant, username and password. It
accepts a credential ID, never a raw API key. The local session is logged out
after a run. The authenticated service checks that the session owner and tenant
match the advisory request before dispatching the matching user's Groq key.
Model, timeout and TPM-budget settings are non-secret configuration
(`GROQ_MODEL_ID`, `GROQ_TIMEOUT_SECONDS`, `GROQ_TPM_LIMIT_TOKENS`,
`GROQ_TPM_HEADROOM_TOKENS`, and `GROQ_MAX_OUTPUT_TOKENS`). Defaults for the
current model are 8,000 TPM, an 800-token headroom, and a 1,024-token global
output ceiling. The workflow uses smaller role-specific output ceilings:
Intake 256, Crop/Scheme 256 each, and Coordinator 384. These settings do not
provide API-key authentication; there is no environment API-key fallback.
Deterministic mode remains the default and does not call an inference provider.
The provider reserves a rolling local TPM budget using a byte-based estimate
before dispatch, then replaces that reservation with validated provider usage
when it is returned. This estimate is a guardrail, not exact Groq tokenization.
The CLI reports only sanitized agent, correlation, estimated/actual token and
budget-decision metadata. Provider exception text, authorization headers and
credentials are never included.
For HTTP 429, diagnostics may also show parsed `Retry-After` and rate-limit
remaining/reset metadata. The client makes at most one automatic retry, only
when a valid provider delay/reset is at most two seconds, the response does not
indicate a daily/account quota exhaustion, and the request has no tools. Longer,
missing/invalid wait metadata, and explicit daily/account quota exhaustion return
immediately to deterministic fallback; account quota exhaustion is reported as
requiring provider quota availability or account-side action.
An over-budget request is rejected before it reaches Groq and keeps the same
deterministic fallback. Specialist prompts retain their scoped source excerpts.
Coordinator receives only validated specialist findings plus a de-duplicated
source/evidence citation catalog, so it does not receive those excerpts again.
Source records returned to the farmer retain full URLs, excerpts and per-chunk
provenance; Coordinator retains source references, provenance identity, evidence
IDs, mode, and crop/geographic applicability.

Run the bounded evaluation with mocked Groq responses:

```powershell
python scripts/evaluate_phase5.py
python scripts/evaluate_phase5.py --semantic
```

The default retrieval backend is explicitly labeled as a deterministic hash
baseline, not semantic embeddings. `--semantic` uses the pinned FastEmbed
model. Mocked workflow results measure retrieval relevance, context validation,
structured parsing, citation integrity, unsupported-case behavior and failure
handling; they do not measure live Groq answer quality. Live tests remain
opt-in and require a locally configured BYOK credential; no key belongs in
the repository.

Microsoft Agent Framework remains the orchestration framework. Local auth
uses database memberships and server-side sessions; credential KEKs use the
Windows Credential Manager adapter and fail closed when unavailable. No cloud
KMS or external identity provider is used. See
`output/RythuSaathi_Phase1_Execution_Record.md` for implementation scope,
recovery limitations, and current verification status. See
`output/RythuSaathi_Phase2_Execution_Record.md` for Phase 2 scope, test results,
seed-catalog sources and limitations. See
`output/RythuSaathi_Phase3_Execution_Record.md` and
`output/RythuSaathi_Phase4_Execution_Record.md` for RAG and agent integration
scope, verification and limitations. See
`output/RythuSaathi_Phase5_Execution_Record.md` for LLM integration, evaluation
results and the exact Phase 5 acceptance assessment.

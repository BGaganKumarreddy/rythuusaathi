# RythuSaathi Phase 5 Execution Record

**Updated: 8 November 2026. Phase 5 status: PARTIAL.** Tasks 5.2–5.6 pass.
Task 5.1 remains PARTIAL under the current acceptance check: a prior basic live
Groq Intake request succeeded, but the latest request was rate-limited and the
required live agricultural synthesis with evidence has not been demonstrated.
The rate-limit cause is not established from the metadata supplied so far.
Live weather/market services are unconfigured and are not claimed as live.
Phase 0–4 are accepted baselines and remain frozen except for narrowly scoped,
backward-compatible integration changes required to connect inference to the
existing Phase 4 workflow. Phase 4 remains the default deterministic behavior.
Phase 6 has not started.

## Pre-implementation audit and plan

This breakdown and its measurable acceptance criteria were established before
Phase 5 implementation, as required by the Phase 5 execution protocol.

### Existing components to reuse

- `src/rythusaathi/agents/` contains the six-executor Microsoft Agent Framework
  graph, typed request/results, deterministic Intake, branch-isolated agent
  handlers, Coordinator, and Phase 2 `AdvisoryBoard` integration.
- `src/rythusaathi/knowledge/` provides crop/geography/stage/jurisdiction-filtered
  SQLite RAG, evidence conversion and the pinned FastEmbed provider.
- `src/rythusaathi/inference/` provides the provider contracts,
  `AuthenticatedGroqService`, and `GroqProvider`. The authenticated service
  authenticates a session and dispatches only the credential owned by that
  verified identity. The provider accepts credentials only through its
  transient `ProviderCredential` argument and has no environment-key fallback.
- `src/rythusaathi/local_auth.py` and `src/rythusaathi/credentials.py` provide
  local session and OS-backed BYOK storage. The usable local adapter is
  `WindowsCredentialStore`; automated tests use an in-memory key adapter.
- `scripts/advisory_cli.py` is the existing workflow entry point. It defaults
  to FastEmbed + deterministic behavior and can use the existing lexical
  baseline explicitly.
- `tests/test_agents.py`, `tests/test_groq_integration.py`, and
  `tests/test_groq_provider.py` cover the accepted graph and provider security
  boundary. Phase 4 recorded 15 focused tests and 126 total tests passing.

The workflow currently has no inference parameter or execution-mode field.
Intake always uses deterministic phrase rules; Crop and Scheme return evidence
excerpts without LLM interpretation; Weather and Market are unavailable unless
a verified data provider is injected; Coordinator deterministically composes
the response. The CLI does not configure local authentication or a BYOK
credential. No Phase 5 implementation or evaluation dataset existed at audit.

### Task breakdown and acceptance criteria

| Task | Scope | Acceptance criteria | Final status |
|---|---|---|---|
| 5.1 Provider integration | Add an explicit deterministic/Groq execution mode and a request-scoped adapter to the existing authenticated service. Extend only the necessary identity binding at that service boundary. Wire an opt-in CLI path to local authentication and owner-scoped credentials. | Default remains deterministic and makes no provider request. Groq mode requires authenticated access; every call goes through `AuthenticatedGroqService`; session identity must match request identity; no alternate provider/key fallback. Mocked integration proves owner credential routing, denial on identity mismatch, typed auth/timeout/rate-limit/outage handling, and no key/session/credential ID in provider request payloads or adapter repr. Mode/provider/model are observable. A successful authenticated live agricultural request must produce validated model synthesis grounded in retrieved evidence before the current Task 5.1 check can pass. | **PARTIAL** — a prior user-provided Groq-mode request completed Intake successfully, but it had no evidence for Coordinator synthesis. The latest user-provided request returned HTTP 429. The required agricultural query and evidence-grounded live synthesis remain unverified. |
| 5.2 Intelligent Intake | Add strict Pydantic extraction schema and Groq-assisted extraction; retain canonical registry resolution, ambiguity handling and deterministic selectable mode. | Valid model fields are only candidates; registry resolution determines canonical crop/location/stage/variety. Candidate values must occur in exact quoted user text. Unknown or unregistered alternatives prevent selecting a single known crop/place. Malformed output and provider failures trigger a visible deterministic fallback. Existing deterministic Intake tests continue to pass. | **PASS** — focused tests cover valid extraction, unknown/ambiguous/unregistered alternatives, malformed output and timeout fallback; mock evaluation context checks 11/11. |
| 5.3 Specialist intelligence | Add evidence-constrained Crop and Scheme interpretations; interpret Weather/Market only when existing providers supply validated observations/quotes. Keep retrieval/provider behavior and provenance intact. | Generated agricultural/scheme interpretations cite only request evidence and are appended to the shared board. No diagnosis or personal eligibility decision is asserted. Weather/Market do not call Groq without data; synthetic fixtures are not sent as live data. Cached/historical provider contract records are interpreted with mode and timestamp. | **PASS** — focused tests cover Crop/Scheme citations, unavailable defaults, fixture suppression and mocked cached weather/historical market contracts. No live weather/market provider is configured. |
| 5.4 Coordinator intelligence | Add strict structured response schema; feed typed Intake/branch results and request evidence only; retain deterministic composition for explicit deterministic mode and observable fallback. | Pydantic validates statements and citations; citation IDs must exist in current request evidence. Coordinator generated interpretation is labeled separately from evidence and recorded on the board. Malformed output, unsupported citations, disallowed questions or provider failure use a visible deterministic fallback. No-evidence requests do not invoke synthesis. | **PASS** — workflow tests cover valid synthesis, invalid citations, timeout/schema fallback and unsupported/no-evidence behavior; final response validates all agent/finding/recommendation citations. |
| 5.5 End-to-end integration | Wire the per-request inference adapter through the existing six-agent graph and CLI, document secure setup/configuration, and verify both modes. | One existing Microsoft Agent Framework graph and blackboard are used. Default CLI remains deterministic. Opt-in Groq CLI obtains a local session and owner-scoped credential; it never accepts a raw key as an argument or prompt. Both CLI help commands and mocked graph execution pass. | **PASS** — deterministic default, mocked Groq path, and one live Groq CLI Intake request are verified; local setup and usage are documented in `README.md`. |
| 5.6 Evaluation and acceptance | Add a bounded supported/unsupported/ambiguous agricultural evaluation set, deterministic metric runner, focused tests, full regression and final record. | Evaluation reports retrieval relevance, citation integrity, unsupported-case behavior, context extraction accuracy, schema validity and simulated failure resilience. Tests use mocked inference; live calls are opt-in. Full repository suite and compile check pass without modifying accepted tests to weaken them. | **PASS** — five-case dataset and final metrics are recorded below; latest regression passes 161 repository tests, compileall passes. |

### Dependencies, risks, and controls

- **Identity and secret handling:** workflow request identity is caller data;
  credential authorization must use the independently authenticated local
  session and reject a mismatch. Session token, credential ID and key stay in
  a private request-scoped adapter and never enter model messages, graph events,
  board records or response data. No raw-key argument or environment fallback.
- **Model output:** Groq output is untrusted. Parse strict JSON into Pydantic
  models, reject extra/invalid fields and reject citation IDs absent from the
  current request evidence. Provider/parse failures must be surfaced as typed
  mode/status information; no silent success or provider switching.
- **Agricultural safety:** model interpretation is constrained to retrieved
  records or verified provider measurements. Deterministic registry matching
  remains authoritative; unsupported claims and diagnosis are not promoted as
  verified facts. Weather and market remain unavailable absent a configured
  provider.
- **External availability:** the sandbox may block outbound DNS, Windows
  Credential Manager and asyncio operations. Mocked tests remain reproducible;
  a live provider call and native Windows key-store verification are reported
  separately and never fabricated. Task 5.1 requires a live inference before
  it can be marked PASS.
- **Regression:** Phase 0–4 is frozen. Any changes to their production files
  must be minimal, additive and covered by baseline regression tests.

## Task execution and evidence

### Implementation

- `src/rythusaathi/agents/llm.py` — request-scoped redacted Groq access,
  strict Pydantic intake/specialist/coordinator schemas, JSON parsing and
  request-evidence citation validation.
- `src/rythusaathi/agents/contracts.py` — explicit workflow mode, generated
  statement and interpretation status fields, and final response invariants.
- `src/rythusaathi/agents/workflow.py` — Groq-assisted Intake, Crop, Scheme and
  Coordinator; conditional Weather/Market interpretation for configured
  non-fixture observations; board publication; visible fallback; guarded
  unknown/ambiguous registry handling. The Phase 4 graph and RAG remain the
  same.
- `src/rythusaathi/inference/service.py` — backward-compatible optional
  authenticated identity matching and non-secret model metadata.
- `scripts/advisory_cli.py` — unchanged deterministic default plus opt-in Groq
  mode using local session login and a credential ID.
- `scripts/byok_cli.py` — hidden-password/key setup backed by the existing
  Windows Credential Manager key-encryption boundary.
- `data/agricultural/phase5_agent_eval.json` and
  `scripts/evaluate_phase5.py` — bounded five-case evaluation corpus and
  deterministic metric runner with mocked provider outputs.
- `tests/test_llm_workflow.py` and `tests/test_groq_integration.py` — focused
  mode, parsing, citation, unsupported-input, provider failure, weather/market
  contract and BYOK isolation tests.
- `README.md` — setup, execution mode, evaluation and limitations.

No new package dependency was introduced. The existing graph, shared blackboard,
RAG index, Phase 0–4 datasets, and deterministic behavior remain in place. No
accepted production tests were removed or weakened.

### Verification results

- Focused workflow/provider regression:
  `.venv\Scripts\python.exe -m unittest tests.test_llm_workflow tests.test_agents tests.test_groq_integration tests.test_groq_provider -q`
  — **63 passed**.
- Full repository suite:
  `.venv\Scripts\python.exe -m unittest discover -s tests -q`
  — **141 passed, 0 failed, 0 skipped**.
- Compilation:
  `.venv\Scripts\python.exe -m compileall -q src scripts tests`
  — passed.
- Pinned FastEmbed evaluation:
  `scripts/evaluate_phase5.py --semantic --model-cache tmp\\models` — **5 cases**
  (2 supported, 2 unsupported, 1 ambiguous); Hit@3 **2/2 (100%)**; citation
  preservation **4/4 (100%)**; unsupported/ambiguous no-claim behavior **3/3**;
  context extraction **11/11 (100%)**; structured response validity **9/9
  (100%)**; invalid schema rejected; simulated provider-failure resilience
  passed. The existing pinned local FastEmbed snapshot was used; no model
  download was needed.
- `scripts/advisory_cli.py --help` and `scripts/byok_cli.py --help` — passed.
- Deterministic CLI end-to-end run against the accepted
  `tmp/phase4-advisory-hash.sqlite3` index — **24 chunks reused, 0 newly
  indexed**. The six-agent graph completed in deterministic mode and reported
  that no crop evidence met the exact crop/location/stage filters for the test
  query; it did not invent a recommendation.
- The same evaluation with the offline hash baseline also passed. Hash
  embeddings are not semantic; these results are separately labeled.
- Full regression ran outside the restricted sandbox because it cannot resolve
  the virtualenv Python installation and blocks Windows credential-store and
  asyncio operations. No tests were skipped or relaxed.

### Task 5.1 sanitized failure diagnostics — 8 November 2026

- `ProviderFailure` now carries typed, sanitized diagnostic metadata: stable
  error code, execution stage, provider, safe model identifier, optional HTTP
  status, UTC timestamp, and random correlation ID. It contains no SDK error
  message, request body, credential, header, password, or session token.
- Local session authentication, caller/session identity mismatch, credential
  lookup/unavailability, and credential decryption now produce separate
  diagnostic codes while retaining the existing public authentication failure
  kind and existing owner-scoped checks. Lookup diagnostics intentionally do
  not reveal whether an ID exists, is owned by another user, inactive, or for a
  different provider.
- Groq 401/403, invalid-model response, rate limit, timeout, network connection,
  malformed response, and other provider failures have distinct safe codes.
  HTTP status is retained only when the provider supplied one. Network
  connection errors use the explicit `network_failure` kind; deterministic
  fallback behavior remains unchanged.
- Diagnostics are collected by the request-scoped workflow adapter, so they
  remain available after agent and Coordinator fallback handlers catch the
  failure. The CLI displays only the sanitized fields after the workflow.
- Focused Groq/provider/workflow regression:
  `.venv\Scripts\python.exe -m unittest tests.test_groq_provider tests.test_groq_integration tests.test_llm_workflow -q`
  — **60 passed**.
- Full repository regression:
  `.venv\Scripts\python.exe -m unittest discover -s tests -q`
  — **153 passed, 0 failed, 0 skipped**.
- Compilation:
  `.venv\Scripts\python.exe -m compileall -q src scripts tests`
  — passed.
- `scripts/advisory_cli.py --help` — passed. It continues to accept only an
  owner-scoped credential ID, not a raw key.
- New focused coverage checks identity mismatch, missing/inactive/wrong-provider
  credentials, decryption failure, HTTP 401 and 403, invalid model, timeout,
  network failure, malformed response, secret leakage prevention, and a
  deterministic workflow fallback that retains sanitized diagnostics.

### Groq HTTP 429 investigation and bounded recovery — 8 November 2026

- The first user-provided live diagnostic was `groq_rate_limited`, provider
  `groq`, model `openai/gpt-oss-20b`, stage `provider_response`, HTTP 429,
  correlation ID `f337a9c669604660bfe37529da823330`. It did not include
  `Retry-After` or rate-limit counters/reset values, so that occurrence's
  specific limit was **unknown**.
- This checkout installs `openai==3.42.1` and does not install the separate
  `groq` SDK. Its `RateLimitError` inherits `APIStatusError`, which exposes the
  response, status, body and request ID; `response.headers` contains the HTTP
  metadata. The existing adapter had been discarding all response headers and
  body fields except status and had `max_retries=0`.
- The Groq documentation says `retry-after` is present on 429 responses when
  provided, `x-ratelimit-remaining-requests` / `x-ratelimit-reset-requests`
  describe RPD, and the corresponding token headers describe TPM. The adapter
  now parses those numeric/duration fields and a small allowlist of error
  scope codes/phrases (RPM, RPD, TPM, TPD, account quota). Raw body/header text
  is never retained or printed. Missing or unrecognized scope remains
  `unknown`.
- Retry policy: the OpenAI SDK's own retry remains disabled. RythuSaathi allows
  at most **one** additional attempt with exponential backoff plus jitter, and
  a maximum automatic wait of **2 seconds**. It respects valid `Retry-After`
  and recognized TPM reset metadata, does not retry identified daily/account
  quota exhaustion, does not retry tool-bearing requests, and returns directly
  to deterministic fallback for longer or insufficient wait metadata. A
  failed retry preserves the original rate-limit snapshot and the same
  correlation ID. Exhausted quota diagnostics say to wait for reset or review
  provider account limits/billing.
- `openai/gpt-oss-20b` remains unchanged. Groq's current model page lists this
  model and its structured-output capability, but that does not establish
  account-specific availability. A previous user-provided live Intake request
  succeeded. No model switch was made.
- Focused tests cover 401/403/429 classification, RPM/RPD/TPM/TPD/account/unknown
  scope handling, numeric/date/malformed/missing retry headers, reset parsing,
  one bounded retry followed by success, retry exhaustion with metadata and
  correlation preservation, quota/permanent failure no-retry behavior,
  tool-bearing no-retry behavior, secret leakage prevention, and rate-limit
  fallback.
- References: [Groq rate limits](https://console.groq.com/docs/rate-limits),
  [Groq API errors](https://console.groq.com/docs/errors), and
  [GPT-OSS 20B model page](https://console.groq.com/docs/model/openai/gpt-oss-20b).

#### Final post-change verification — 8 November 2026

- After the final quota-scope classifier and focused test adjustment, the
  complete repository suite was rerun:
  `.venv\Scripts\python.exe -m unittest discover -s tests -q` — **153 passed,
  0 failed, 0 skipped**.
- `.venv\Scripts\python.exe -m compileall -q src tests scripts` — passed.
- `scripts\advisory_cli.py --help` — passed; CLI initialization and options
  remain available.
- Focused provider, integration and workflow tests had passed immediately
  before the final classifier adjustment (**60 passed**); the full suite above
  includes that adjustment and its test.
- The virtualenv interpreter required execution outside the restricted sandbox;
  no test was skipped or relaxed. No live Groq request was made during this
  final verification because the account's supplied response was HTTP 429 and
  its rate-limit metadata did not establish a safe retry window.

#### User-provided agricultural live attempt and TPM diagnosis — 8 November 2026

- The user supplied a sanitized run for the required agricultural query. Its
  diagnostic was HTTP 429 at `provider_response`, model `openai/gpt-oss-20b`,
  correlation ID `9d54ead697344c4cbbd1c21bd8d9229e`, timestamp
  `2026-11-07T19:37:33Z` (01:07 IST). It reported `retry_after_seconds=42`,
  `remaining_requests=997`, `remaining_tokens=361`, request reset 259.2s, and
  token reset 57.292s. The CLI reported zero retries and used deterministic
  fallback; the Coordinator did not produce validated Groq synthesis.
- The user's matching Groq Dashboard screenshot at 1:07am shows 5.6K input,
  2.9K output, 8.6K total tokens against the model's 8K rate-limit line, with
  a 429 visible for the same model. Together with the response's remaining
  token count/reset, this establishes a tokens-per-minute limit event during
  the run. The `account_quota` label in the CLI diagnostic is inconsistent
  with the dashboard evidence and is treated as a misclassification for this
  run; the screenshot does not establish separate billing/quota state.
  Groq documents that `x-ratelimit-remaining-tokens` and
  `x-ratelimit-reset-tokens` refer to TPM, while the request counters refer to
  RPD. [Groq rate-limit headers](https://console.groq.com/docs/rate-limits).
- The prior classifier checked generic “quota exceeded” wording before
  checking explicit RPM/TPM wording. This could mislabel a token-window error
  as account quota. It now gives explicit request/token-window scope text
  precedence; exact quota error codes and explicit billing-limit wording still
  classify as account quota. No raw provider response body was displayed or
  added to diagnostics. Focused tests cover messages containing both token
  quota and TPM, and verify that a 42-second Retry-After is reported as TPM
  without attempting a retry beyond the 2-second cap.
- The 42-second Retry-After exceeds the application's 2-second automatic-wait
  cap, so no retry was attempted. The CLI retains deterministic fallback and
  the live acceptance remains **PARTIAL** until the same evidence-grounded
  synthesis succeeds. This attempt did retrieve PAU/ICAR agricultural excerpts
  and official scheme source material, preserved citations, kept growth stage
  unknown, and reported weather/market as unavailable. It also surfaced the
  existing unseeded crop/location-pair limitation.
- After the classification correction, focused provider/integration/workflow
  tests passed (**61 passed**); the complete suite passed (**154 passed, 0
  failed, 0 skipped**); `compileall` and CLI `--help` passed. No live request
  was issued during this code/test verification.

#### Token optimization and TPM budget management — 8 November 2026

- **Root cause:** the supplied dashboard evidence showed 5.6K input and 2.9K
  output tokens (8.6K total) for a model with an 8K TPM limit. The Groq 429
  was therefore a token-window event. Before this change, Groq mode had no
  role-specific output caps, no local request budget, and no per-call token
  usage observability. The previous workflow could call Intake, Crop, Scheme
  and Coordinator; Weather and Market make no LLM call when their live
  providers are unavailable, as in the supplied run.
- **Repeated context audit:** Intake sends only farmer text and explicit
  fields. Crop and Scheme each send scoped excerpts. The Coordinator then
  receives all evidence again, plus duplicated specialist summary,
  interpretation, generated statements, provenance/URL fields and conflict
  claim text. There is no conversation-history payload. The supplied run had
  five crop excerpts and six scheme excerpts; RAG chunks remain intact at up
  to 900 characters, and are not truncated during retrieval.
- **Initial prompt/output changes:** specialist model context retains each
  evidence ID, source title, excerpt, evidence mode and crop/geographic
  applicability. Full URLs, provenance, revision details and all quality notes
  remain in the returned `EvidenceRecord`, but duplicate prompt serialization
  is omitted. Safety relevant source-reliability/secondary-source notes remain
  in model context. The Coordinator receives specialist generated statements
  *or* their fallback summary, never both copies; it receives a concise conflict
  warning rather than repeating conflict claim text. Source citations, request
  filtering, unknown values, structured validation and all safety instructions
  remain.
- Output ceilings are now Intake **256**, Crop **256**, Scheme **256** and
  Coordinator **384** tokens (1,152 total maximum), compared with the prior
  2,048 total role ceilings introduced during the initial audit. Schema bounds
  were aligned to the concise structured tasks: Intake up to four candidates
  per field and six observations; specialists up to three 360-character
  statements; Coordinator up to five 360-character statements and three
  questions. These are output ceilings, not evidence truncation.
- **Initial measured prompt comparison:** using the existing agricultural test
  corpus (four Groq calls, eleven evidence records), prompt bytes were Intake
  2,175, Crop 5,980, Scheme 8,202 and Coordinator 13,252: 29,609 total. A
  controlled Coordinator payload retaining the prior repeated metadata was
  19,681 bytes, so that intermediate Coordinator context was 6,429 bytes
  (32.7%) smaller. The whole-workflow output ceiling fell 43.8% from 2,048 to
  1,152 tokens. These are prompt-byte measurements and output reservations,
  **not** claimed provider token counts. Actual Groq token accounting remains
  provider authoritative.
- **TPM guard:** `GroqProviderConfig` adds non-secret configuration for
  `GROQ_TPM_LIMIT_TOKENS` (default 8,000),
  `GROQ_TPM_HEADROOM_TOKENS` (default 800), and
  `GROQ_MAX_OUTPUT_TOKENS` (default 1,024). Before dispatch it makes a
  byte-based estimate with additional non-ASCII allowance, reserves that
  estimate plus the role output ceiling from a thread-safe rolling one-minute
  budget, and rejects clear over-budget requests with sanitized
  `groq_tpm_budget_exceeded` at `request_validation`. Provider returned usage
  counters replace the reservation when available. A 429 records its TPM reset
  as a local cooldown. The existing one-retry/2-second maximum remains; no
  long or unbounded retry was added.
- **Observability:** successful calls expose only agent name, provider/model,
  correlation ID, estimated input, actual input/output/total when the provider
  returns validated counters, output ceiling, budget counters/decision, and
  retry decision. Diagnostics never contain prompt text, raw response bodies,
  credentials, session data or authorization headers.
- **Files changed:** `agents/llm.py` (role caps and sanitized usage records),
  `agents/workflow.py` (compact evidence/Coordinator payload), inference
  contracts and Groq provider (budget/usage contracts and guard), CLI (safe
  reporting), README, and focused workflow/provider/integration tests. No
  Phase 0–4 production behavior, RAG retrieval, owner-scoped credential path,
  six-agent graph or deterministic fallback was redesigned.
- Focused verification after this change:
  `.venv\Scripts\python.exe -m unittest tests.test_llm_workflow tests.test_groq_provider tests.test_groq_integration -q`
  — **67 passed**. It includes compact-payload and safety-instruction checks,
  evidence/citation preservation, structured output, output reservation,
  local pre-dispatch budget rejection, actual-usage telemetry, TPM 429/reset,
  bounded retry, deterministic fallback and secret-leak prevention. No live
  Groq request was made during token optimization.
- Final verification after the optimization:
  `.venv\Scripts\python.exe -m unittest discover -s tests -q` — **160 passed,
  0 failed, 0 skipped**;
  `.venv\Scripts\python.exe -m compileall -q src tests scripts` — passed;
  `scripts\advisory_cli.py --help` — passed. The virtualenv test invocation
  ran outside the restricted sandbox; no test was skipped, removed or relaxed.

#### User-provided post-optimization live attempt — 8 November 2026

- The user supplied another Groq-mode run of the required rice/Punjab question.
  It reused the agricultural and scheme evidence, preserved source citations,
  left growth stage unknown, made no weather/market assertion, and retained
  the deterministic partial-advisory fallback. Intake, Crop, Scheme and
  Coordinator were all `partial`; Weather and Market were unavailable.
- The output says `Groq synthesis unavailable or invalid` with inference status
  `rate_limit`; it contains no Groq-generated synthesis. This attempt therefore
  does **not** meet Task 5.1 live acceptance.
- The supplied transcript did not include the CLI's `Sanitized inference
  diagnostics` or `Sanitized inference token usage` sections. Consequently it
  does not establish whether the failure was Groq TPM exhaustion or a local
  pre-dispatch budget rejection, and it provides no new actual provider usage
  counters. No cause is inferred from the agent status alone.

#### Coordinator TPM budget optimization — 8 November 2026

- The next controlled diagnostic established the local budget cause precisely:
  Intake used 856 provider tokens, Scheme 2,051 and Crop 1,647, for **4,554**
  tokens. The Coordinator's prior local estimate was 2,766 input tokens with a
  384-token output reservation. `4,554 + 2,766 + 384 = 7,704`, which exceeds
  the configured 7,200-token effective budget by 504; rejecting it at
  `request_validation` was correct. Actual successful provider totals replace
  their provisional reservations, so these tokens were not double-counted.
- Audit of the matching mocked rice/Punjab request found 7,204 raw-excerpt
  bytes inside the Coordinator's 10,425-byte evidence payload. Its prompt and
  schema were 639 and 869 bytes respectively; agent results were 914 bytes.
  Repeating raw excerpts after Crop and Scheme had already interpreted them was
  the dominant avoidable Coordinator cost.
- Coordinator now receives: (1) validated specialist statements with their
  citations; (2) a de-duplicated **four-source** catalog retaining source URL
  and provenance identity; and (3) an **eleven-entry** evidence catalog
  retaining every evidence ID, content reference, mode, safety-relevant source
  quality, crop scope and geographic scope. Full excerpts and per-chunk
  provenance remain unmodified on the board and in the farmer-facing
  `EvidenceRecord`. Conflicts remain explicit, as concise non-selection
  warnings. Unknown growth stage is preserved.
- The Coordinator prompt now forbids claims beyond validated specialist
  statements. It can cite only IDs already cited by such statements; citation
  validation enforces this restriction after response parsing. If no validated
  specialist statement exists, Coordinator makes no Groq call and uses the
  established deterministic fallback rather than attempting an unsupported
  synthesis.
- Final mocked estimate for the same query is **1,653** Coordinator input
  tokens (down 1,113, or 40.2%, from the supplied 2,766 estimate). With the
  observed 4,554 tokens already used and the unchanged 384-token output
  reservation, projected consumption is **6,591 / 7,200**, leaving a
  **609-token** local margin. This is an estimate, not a provider-token claim.
- Focused workflow tests now cover Coordinator catalog prompt reduction,
  source URL/provenance identity retention, full board evidence retention,
  citation restriction, conflict warning, unknown stage, successful structured
  synthesis and deterministic no-validated-specialist fallback. Provider and
  integration tests continue to cover output reservation, local admission/
  rejection, concurrent-safe accounting, HTTP 429 handling and secret
  leakage. No live Groq request was made during this change.
- Verification after the Coordinator change:
  `.venv\Scripts\python.exe -m unittest tests.test_llm_workflow tests.test_groq_provider tests.test_groq_integration -q`
  — **68 passed**;
  `.venv\Scripts\python.exe -m unittest discover -s tests -q` — **161 passed,
  0 failed, 0 skipped**;
  `.venv\Scripts\python.exe -m compileall -q src tests scripts` — passed.
  The virtualenv test invocation ran outside the restricted sandbox; no test
  was skipped, removed or relaxed.

### Limitations and external verification

- A basic live Groq Intake request was verified on 8 November 2026 using the
  local authenticated BYOK path. The user-provided transcript for “Will it rain
  tomorrow?” shows Groq mode/provider, model `openai/gpt-oss-20b`, and Intake
  completion in 1192.5 ms. That request had no evidence, so Coordinator
  synthesis correctly did not run. The later agricultural attempt returned
  429 and, when correlated with the user's dashboard screenshot, demonstrated
  the 8K TPM limit was exceeded; the account billing/quota state was not
  separately checked.
- The current Task 5.1 live acceptance requires successful synthesis on the
  specified agricultural question with evidence citations. This has not yet
  been verified. Run the following in the user's network-enabled Windows
  terminal, replacing the placeholder with the existing owner-scoped credential
  ID. The command uses the accepted FastEmbed SQLite index and cached model:

  ```powershell
  .\.venv\Scripts\python.exe scripts\advisory_cli.py "My rice leaves are yellowing in Punjab. What should I check?" --database tmp\phase4-advisory-fastembed.sqlite3 --model-cache tmp\models --mode groq --auth-database tmp\phase5-local-auth.sqlite3 --credential-id YOUR_EXISTING_CREDENTIAL_ID
  ```

  Acceptance requires a successful authenticated model response, Groq-generated
  synthesis with valid retrieved-evidence citations, unknown growth stage when
  unstated, no unsupported disease diagnosis, and no invented weather/market
  observations. If retrieval yields no evidence, the Coordinator will
  correctly skip synthesis and Task 5.1 remains PARTIAL. Share only the
  sanitized CLI output; never share the key, password, or session token.
- No live weather or market adapter is configured. The two agents continue to
  report unavailable when those adapters are absent. Tests cover synthetic
  fixture suppression and cached/historical data contracts only.
- Evaluation provider outputs are mocked. The 100% structured/citation/context
  results measure integration behavior for these five cases, not production
  model quality, agronomic correctness or broad language coverage.
- Citation integrity verifies that IDs belong to the current response; it
  cannot automatically prove that every free-text interpretation is entailed
  by its cited excerpt. Generated text is labeled and paired with source
  material for human verification.
- Phase 2 coverage still has no seeded crop/location pairs; the workflow
  continues to surface the accepted coverage limitation.

**Current Phase 5 acceptance:** Tasks **5.2–5.6 PASS**; Task **5.1 PARTIAL**
pending the authenticated, evidence-grounded agricultural live synthesis above.
Focused rate-limit diagnostics and deterministic fallback are verified; all
**161** repository regression tests pass. Live weather and market remain
unconfigured. Phase 6 has not started.

### Addendum — supplied coordinator-budget diagnostic reviewed 9 November 2026

- The user supplied sanitized output for the rice/Punjab scenario bearing the
  execution timestamp **2026-11-08T19:39:57Z**. It is preserved here as
  diagnostic evidence only. Because it predates the 9 November Coordinator
  correction, it cannot serve as the required live verification of that
  correction.
- Intake, Crop and Scheme completed provider calls with authoritative totals
  of **856**, **1,647** and **2,051** tokens respectively (**4,554** total).
  The effective rolling local TPM budget was **7,200** tokens, leaving
  **2,646** before the Coordinator attempt.
- The Coordinator was rejected at `request_validation` with
  `groq_tpm_budget_exceeded`: its estimated input was **3,793** tokens and its
  output reservation was **384**, requiring **4,177** tokens against the
  remaining **2,646**. It was never dispatched to Groq and made **zero**
  retries. The deterministic evidence summary was therefore correctly used.
- The output preserves unknown growth stage, explicitly avoids an individual
  disease diagnosis, and makes no live weather or market assertion. The source
  excerpts shown are request-scoped. However, because the Coordinator was not
  called, the trace cannot establish that the corrected `retrieved_evidence`
  payload was received, that Coordinator citations were valid, that no unknown
  IDs were emitted, or that fixture evidence was excluded from its prompt.
- **Acceptance result:** Task **5.1 remains PARTIAL**. No additional live
  request, code modification, or automatic retry was made after reviewing this
  failed pre-dispatch attempt. The current local regression baseline is
  **169 passing tests**.

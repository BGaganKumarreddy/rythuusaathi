# RythuSaathi Complete Implementation Plan v2

**Planning revision:** 03 November 2026  
**Purpose:** Dependency-aware engineering breakdown and acceptance plan for the approved RythuSaathi architecture.  
**Schedule:** No fixed duration is assumed. Estimate each task against the actual team, source access, target platform, and approval lead times before committing dates.

## 1. Preserved scope and rules

- Microsoft Agent Framework remains mandatory. Groq remains the primary LLM provider through a modular provider abstraction with configurable models.
- Exactly six agents remain: Intake, Weather, Crop, Market, Scheme, and Coordinator.
- The product is all-India and multi-crop, with dynamically selected state, district, and crop through extensible registries. No fixed demonstration crop or region constrains the architecture.
- Advice is evidence-grounded. Live, cached, historical, and fixture data are distinguished. Ambiguous, stale, unavailable, conflicting, or unsupported evidence is surfaced; missing facts are never fabricated.
- Multi-user BYOK keys are remembered and stored as authenticated user/tenant-bound records using KMS-backed envelope encryption. Plaintext is decrypted just in time inside the protected backend credential/provider layer. A BYOK failure never silently falls back to deployment credentials.
- The web application and CLI both call the same authenticated backend services and the same Microsoft Agent Framework workflow. Neither client implements agent orchestration or calls Groq directly.
- Every implementation phase includes unit, contract, and/or security testing. Phase 6 remains integrated end-to-end verification and release hardening.

## 2. Phase order and exit gates

| Phase | Outcome | Exit gate |
|---|---|---|
| 0. Readiness spike | Verify Microsoft Agent Framework and Groq compatibility. | All established spike acceptance criteria pass; candidate runtime and package versions are recorded. |
| 1. Identity and BYOK security | Approve identity/tenant boundaries and implement the protected credential lifecycle. | Identity/KMS decisions approved; authorization, envelope, concurrency, and non-disclosure tests pass. |
| 2. Contracts and registries | Define canonical context, extensible mappings, evidence records, and the shared board. | Unit and contract tests pass across multiple crops, regions, ambiguity, and unsupported coverage. |
| 3. Data adapters | Integrate weather, market, crop knowledge, and central/state schemes. | Adapters preserve provenance, freshness, failure states, and citations. |
| 4. Six-agent workflow | Implement six separate agents on the approved graph. | Context/write-scope contracts and agent-level tests pass. |
| 5. Shared backend, web, and CLI | Expose the same backend workflow through both approved interfaces. | API contracts and interface parity/security tests pass. |
| 6. Integrated verification and hardening | Verify full behavior and prepare controlled release. | Security, E2E, operational, monitoring, rollback, and release gates pass. |

## 3. Phase 0 - Readiness and framework compatibility spike

### Task 0.1 - Runtime and package feasibility

- **Objective:** Select candidate Python 3.11+ runtime/platform and verify current Microsoft Agent Framework API and package constraints.
- **Dependencies:** None.
- **Deliverables:** Compatibility matrix; candidate package versions and lock strategy.
- **Acceptance:** Candidate packages install and import together on the intended platform without unresolved conflicts; exact versions are recorded.
- **Tests:** Clean-environment installation/import smoke; Python/platform and dependency constraint checks.

### Task 0.2 - Six-agent graph proof

- **Objective:** Exercise the current supported graph workflow with six distinct identities, required order, one canonical context, and a board fixture.
- **Dependencies:** Task 0.1.
- **Deliverables:** Disposable spike harness and workflow event/error notes.
- **Acceptance:** Intake -> Weather -> Crop -> Market -> Scheme -> Coordinator executes in order; context is preserved and unauthorized board mutations are rejected.
- **Tests:** Workflow unit/contract checks for ordering, context propagation, write boundaries, and failure events.

### Task 0.3 - Groq and protected credential proof

- **Objective:** Call Groq through the selected Microsoft Agent Framework OpenAI-compatible client configuration, test a configurable model and function tool, and inject a canary only through a protected backend credential boundary.
- **Dependencies:** Tasks 0.1 and 0.2.
- **Deliverables:** Provider compatibility report; sanitized event/error samples; canary scan results.
- **Acceptance:** Success, timeout, rate-limit, malformed-response, and authentication-failure paths are mapped; the canary is absent from agents, prompts, board, logs, traces, queues, caches, analytics, and responses.
- **Tests:** Provider contract/function-call checks, provider error mapping, and secret surface scanning.

### Task 0.4 - Spike gate review

- **Objective:** Review the compatibility evidence before implementation.
- **Dependencies:** Tasks 0.1-0.3.
- **Deliverables:** Go/no-go record and candidate dependency set.
- **Acceptance:** All spike criteria below pass. Microsoft Agent Framework remains mandatory; replacement requires a concrete blocker and explicit approval.
- **Tests:** Checklist review; reproducible blocker test if a failure is found.

**Preserved spike criteria:** (1) Python 3.11+ runtime and candidate Microsoft Agent Framework core/provider packages install/import together on the target platform; versions and lock strategy are recorded. (2) The current supported graph runs six distinct agents in order with shared context/board. (3) Groq works through the selected compatible client with configurable model; required tools, events, timeouts, and errors are verified. (4) User credential injection stays in the protected backend path and plaintext is absent from forbidden surfaces. (5) No required feature depends on an unsupported/preview API or package limitation unless explicitly accepted as risk. Any framework change requires a concrete blocker and approval.

## 4. Phase 1 - Authentication, tenant identity, and BYOK security

### Task 1.1 - Identity and tenant decision

- **Objective:** Evaluate identity options and decide issuer, immutable user subject and tenant claims, account linking, membership changes, administrative boundaries, and deprovisioning. Do not assume an identity vendor.
- **Dependencies:** Phase 0 gate.
- **Deliverables:** Identity decision record, verified-claims contract, updated threat model.
- **Acceptance:** Tenant and subject IDs derive only from verified claims; lifecycle transitions are specified.
- **Tests:** Unit tests for claims; contract/security tests for absent, invalid, changed, or mismatched tenant claims.

### Task 1.2 - Authorization boundaries

- **Objective:** Define permission rules for credential create, status, use, rotate, revoke, and delete, including tenant administration.
- **Dependencies:** Task 1.1.
- **Deliverables:** Authorization policy and backend service boundary contracts.
- **Acceptance:** Request payload IDs cannot override authenticated identity; agents and clients have no credential access.
- **Tests:** Owner, peer, cross-tenant, admin, and unauthenticated access matrix.

### Task 1.3 - KMS and credential-store evaluation

- **Objective:** Evaluate options for authenticated encryption/context binding, key versions and rewrap, IAM, region/residency, availability, audit, recovery, deletion guarantees, and cost. Do not presume a KMS vendor.
- **Dependencies:** Task 1.1; security/platform approval.
- **Deliverables:** Approved envelope schema, KMS/store decision, key lifecycle policy.
- **Acceptance:** Security review approves the selected controls and least-privilege design before persistence.
- **Tests:** Envelope schema validation, negative crypto tests, and KMS permission review.

### Task 1.4 - Credential lifecycle service

- **Objective:** Implement user/tenant-authorized create, status, retrieval for inference, rotate, revoke, and delete with generation-based concurrency.
- **Dependencies:** Tasks 1.2 and 1.3.
- **Deliverables:** Credential service/repository, protected provider credential interface, audit event contract, lifecycle runbook.
- **Acceptance:** Only an active caller-owned generation can be used; rotation is atomic; revocation blocks new use; no silent fallback to deployment credentials.
- **Tests:** Lifecycle unit/contract tests; authorization, concurrency, and non-disclosure tests in Section 11.

### Task 1.5 - Secret-safe telemetry

- **Objective:** Redact secret-bearing request fields before logging, tracing, metrics, errors, analytics, and support tooling.
- **Dependencies:** Tasks 1.2-1.4.
- **Deliverables:** Redaction middleware and non-secret frontend credential status contract.
- **Acceptance:** Frontend receives status only; no plaintext, ciphertext, or reconstructive key-derived data reaches forbidden systems.
- **Tests:** Canary scans for success, error, retry, and provider failure paths.

## 5. Phase 2 - Context, registries, board, and evidence contracts

### Task 2.1 - Registry schemas and versioning

- **Objective:** Define versioned crop, state/district, crop-region-season/stage, and crop-to-commodity mappings with provenance and validity dates.
- **Dependencies:** Phase 0; registry/source ownership decision.
- **Deliverables:** Registry schemas and versioning contract.
- **Acceptance:** Workflow code has no fixed crop-region branch. Missing coverage means unsupported system coverage, not that the crop is absent from a region.
- **Tests:** Alias, parent-child, effective date, version, and invalid reference unit tests.

### Task 2.2 - Context resolution service

- **Objective:** Resolve user selections to canonical IDs and report ambiguous, missing, and unsupported selections distinctly.
- **Dependencies:** Task 2.1.
- **Deliverables:** Backend resolution service and request/response contract.
- **Acceptance:** Ambiguous selection requests clarification; no nearby crop/location is substituted; every agent receives exact canonical context.
- **Tests:** Multiple crop-region pairs, aliases, ambiguous location, invalid IDs, and unsupported coverage contracts.

### Task 2.3 - Evidence contracts

- **Objective:** Define source, time, geographic/crop scope, units, page citation, data mode, freshness, quality, and typed payload fields.
- **Dependencies:** Task 2.1.
- **Deliverables:** EvidenceRecord schema and status vocabulary.
- **Acceptance:** `live`, `cached`, `historical`, and `fixture` are distinct from `available`, `unavailable`, `stale`, `failed`, and `unsupported`.
- **Tests:** Schema round-trip and invalid provenance/status combination tests.

### Task 2.4 - Shared AdvisoryBoard

- **Objective:** Implement typed append-only sections, case IDs, trace events, agent write ownership, and immutable context.
- **Dependencies:** Tasks 2.2 and 2.3.
- **Deliverables:** AdvisoryBoard service and schema.
- **Acceptance:** Writes are scoped; findings append; credential material, ciphertext, and key-derived values are structurally excluded.
- **Tests:** Append/authorization, immutability, trace, and schema unit tests.

## 6. Phase 3 - Source adapters and retrieval

### Task 3.1 - Source readiness inventory

- **Objective:** Evaluate candidate weather, market, crop knowledge, and central/state scheme sources, access terms, coverage, freshness, and operational owners.
- **Dependencies:** Phase 2 contracts.
- **Deliverables:** Source register and coverage matrix.
- **Acceptance:** Endpoint, scope, access, and rights are verified before connector implementation.
- **Tests:** Source response fixtures and coverage review.

### Task 3.2 - Weather and market adapters

- **Objective:** Normalize canonical geography/commodity, timestamps, units, cache/freshness, and provider failures.
- **Dependencies:** Task 3.1 and Phase 2 contracts.
- **Deliverables:** Weather and market adapters with deterministic fixtures.
- **Acceptance:** No invented observations; stale, unavailable, malformed, and ambiguous mapping states remain explicit.
- **Tests:** Unit/contract tests for timeout, rate limit, empty, malformed, historical, stale, and fixture-only cases.

### Task 3.3 - Crop and scheme retrieval

- **Objective:** Ingest regional agricultural knowledge and official central/state scheme documents with revision and page metadata.
- **Dependencies:** Task 3.1 and evidence contracts.
- **Deliverables:** Ingestion/retrieval modules, citations, supersession metadata.
- **Acceptance:** Crop/geography relevance and source scope are retained; scheme criteria remain met/unmet/unknown, never a guarantee.
- **Tests:** Parser/retrieval contract tests, page citation round-trip, scope and unknown-criterion tests.

### Task 3.4 - Cache and fixture behavior

- **Objective:** Implement the approved freshness, invalidation, and fixture policies.
- **Dependencies:** Tasks 3.2-3.3; freshness decision.
- **Deliverables:** Cache policy and fixture providers.
- **Acceptance:** Fixtures never become live; cached age is visible; errors do not conceal provenance.
- **Tests:** Expiry/invalidation, fixture isolation, and mode-transition tests.

## 7. Phase 4 - Six-agent workflow

### Task 4.1 - Workflow shell and Intake

- **Objective:** Bind case context and board; resolve selection or stop for clarification.
- **Dependencies:** Phase 0 gate and Phase 2 contracts.
- **Deliverables:** Microsoft Agent Framework graph and Intake agent/tools.
- **Acceptance:** Ambiguous or unsupported case cannot proceed as falsely local-specific advice.
- **Tests:** Agent unit and graph ordering/context/stop contracts.

### Task 4.2 - Weather, Crop, and Market agents

- **Objective:** Implement these three separate agents over the corresponding adapters and scoped tools.
- **Dependencies:** Task 4.1; relevant Phase 3 adapters.
- **Deliverables:** Three agent modules and tool contracts.
- **Acceptance:** Findings retain context IDs, provenance, source status, scope, and authorized write section.
- **Tests:** Per-agent unit tests, adapter contracts, context and write-scope checks.

### Task 4.3 - Scheme and Coordinator agents

- **Objective:** Implement central/state scheme evidence and Coordinator evidence/safety/completeness behavior.
- **Dependencies:** Task 4.2 and scheme retrieval.
- **Deliverables:** Scheme/Coordinator modules and final response schema.
- **Acceptance:** Unknown criteria remain unknown; Coordinator qualifies or withholds dependent claims.
- **Tests:** Criteria/safety unit tests and complete/partial/failure workflow contracts.

### Task 4.4 - Workflow resilience

- **Objective:** Add bounded timeout/retry classification, events, partial results, trace redaction, and request isolation.
- **Dependencies:** Tasks 4.1-4.3; approved provider limits.
- **Deliverables:** Workflow middleware and failure policy.
- **Acceptance:** Six identities remain traceable; retries reauthorize BYOK and never persist credentials.
- **Tests:** Failure, duplicate event, concurrent case, retry, and canary tests.

## 8. Phase 5 - Shared backend, web application, and CLI

### Task 5.1 - Shared backend APIs

- **Objective:** Expose authenticated context search, credential lifecycle, advisory run, and response services.
- **Dependencies:** Phases 1-4.
- **Deliverables:** Backend service composition, API routes/schemas.
- **Acceptance:** All runs invoke the same backend graph and enforce server-side authorization.
- **Tests:** API unit/contracts, authorization matrix, workflow invocation contract.

### Task 5.2 - Web application

- **Objective:** Build dynamic context selection, secure BYOK entry/status, and evidence-aware advisory view.
- **Dependencies:** Task 5.1; interface/locale decisions.
- **Deliverables:** Web UI and backend API client only.
- **Acceptance:** No provider key or orchestration graph in frontend; no browser key persistence; citations/status are visible.
- **Tests:** UI/API tests, browser storage and telemetry canary checks.

### Task 5.3 - CLI

- **Objective:** Build authenticated CLI flows for context, credential lifecycle, and advice using shared backend APIs.
- **Dependencies:** Task 5.1; CLI authentication decision.
- **Deliverables:** CLI and backend API client only.
- **Acceptance:** CLI never runs agents/framework/Groq directly; no secret echo or unencrypted credential persistence.
- **Tests:** Command/API contracts, shell history/output/config secret checks.

### Task 5.4 - Interface parity

- **Objective:** Verify equivalent web and CLI cases use the same service and output contracts.
- **Dependencies:** Tasks 5.2 and 5.3.
- **Deliverables:** Parity scenarios and results.
- **Acceptance:** Differences are presentation-only; no duplicated business/orchestration logic.
- **Tests:** Equivalent fixture contract cases through both clients.

## 9. Phase 6 - Integrated end-to-end verification and release hardening

### Task 6.1 - Cross-product E2E matrix

- **Objective:** Exercise multiple crop-region pairs, ambiguity, unsupported coverage, partial data, conflict, and every source mode.
- **Dependencies:** Phases 1-5.
- **Deliverables:** Deterministic E2E suite/report.
- **Acceptance:** No context leakage/fabrication; provenance survives web and CLI output.
- **Tests:** E2E matrix with no live network dependency.

### Task 6.2 - BYOK security acceptance

- **Objective:** Execute encryption, identity binding, key version, rotation, revocation, deletion, concurrency, and non-disclosure criteria.
- **Dependencies:** Phase 1; approved identity/KMS decisions.
- **Deliverables:** Security report and remediation records.
- **Acceptance:** All Section 11 checks pass; no unresolved high-severity issue.
- **Tests:** Negative, tampering, race, KMS, cross-tenant, and canary tests.

### Task 6.3 - Operations hardening

- **Objective:** Validate outages, monitoring, backup/restore, incident response, and rollback.
- **Dependencies:** Phases 3-5; operational approvals.
- **Deliverables:** Runbooks, dashboards/alerts, rollback procedure.
- **Acceptance:** Operators can diagnose status/freshness without secrets and rehearse recovery.
- **Tests:** Failure injection, restore and runbook rehearsal.

### Task 6.4 - Staged release gate

- **Objective:** Enable only verified registry/source coverage and roll out with controlled flags/traffic.
- **Dependencies:** Tasks 6.1-6.3; explicit release approval.
- **Deliverables:** Coverage manifest, thresholds, rollback owner.
- **Acceptance:** Smoke and rollback pass; unsupported coverage is explicit; source and credential signals monitored.
- **Tests:** Deployment smoke, synthetic check, rollback verification.

## 10. BYOK encryption envelope specification

### Encryption and metadata

- Use an approved authenticated-encryption-with-associated-data (AEAD) construction; AES-256-GCM is the provisional candidate, subject to security review and selected KMS/store capabilities.
- Generate each DEK using a cryptographically secure random number generator (CSPRNG) or an approved KMS data-key operation. Generate each 96-bit GCM nonce from the operating system CSPRNG using the approved crypto library; do not use timestamps, counters without durable coordination, UUIDs, or application PRNGs. Generate a fresh DEK for every credential generation. Never reuse a nonce under the same DEK, including after retries, process restarts, restores, or concurrent writes. Rewrapping a DEK does not encrypt the credential again and therefore preserves the original ciphertext/nonce/tag; any re-encryption uses a fresh DEK and nonce.
- Enforce the invariant in code and storage: a DEK is used for at most one credential-encryption operation. Persist a non-secret DEK generation identifier and a uniqueness constraint over `(DEK generation, nonce)` in the credential envelope store. Register the tuple atomically with ciphertext persistence; a collision or duplicate registration fails closed, discards the candidate DEK, and retries with a new DEK and nonce. Never retry encryption with the same DEK/nonce pair.
- Use the full 128-bit GCM authentication tag. Store as a separate explicit field or an unambiguous documented suffix. Reject missing, truncated, malformed, or invalid tags. Do not release plaintext before tag verification succeeds.
- Canonical, versioned AAD binds immutable tenant ID, immutable user subject ID, credential record UUID, provider ID (`Groq`), and envelope/schema version. Define deterministic encoding, field boundaries, and UTF-8 normalization. Use identical AAD at decrypt; any change must fail authentication.
- Where supported, pass tenant, subject, record UUID, provider, and envelope version as KMS encryption context and verify it during unwrap. Database row filtering alone is insufficient identity binding.
- Persist access-controlled metadata: ciphertext, nonce, tag, wrapped DEK, AEAD algorithm/version, DEK generation, KMS key identifier/version, context/schema version, record generation/state, and created/rotated timestamps. Do not store secret fingerprints.
- Never put plaintext, ciphertext, wrapped DEKs, nonce/tag tuples, request bodies, or reconstructive key-derived values in agents, prompts, board, logs, traces, metrics labels, queues, caches, analytics, crash reports, or responses.

### Python plaintext memory boundary

Python does not provide a reliable guarantee that immutable strings, byte strings, garbage-collected objects, or copies made by a provider SDK are zeroized. Deallocation or overwriting one mutable buffer cannot prove that all copies have been erased. Do not claim guaranteed plaintext zeroization.

Keep credential handling in a narrowly scoped backend credential/provider component. Only that component may obtain the decrypted credential and pass it directly to the Groq provider client over TLS. Agents receive neither credential values nor credential-bearing callbacks, prompts, tools, environment variables, or board fields. Do not serialize plaintext, place it in general session objects, exceptions, retry queues, persistent storage, debug dumps, or telemetry. Avoid unnecessary conversions/copies; keep the lifetime to the single outbound request and release references promptly after dispatch. Disable request/header/body capture for the provider client and use secret-safe exception handling. Runtime/SDK memory behavior and process-dump controls require operational verification.

### KMS and key-version rotation

Every credential generation receives a new DEK and nonce. For KEK rotation, rewrap DEKs under the new KMS key version without changing credential ciphertext where supported. Verify context and result, then atomically update wrapped-DEK/key-version metadata using record-generation compare-and-swap. Keep prior KMS versions enabled for decrypt/rewrap until every active record and retained backup is migrated, restore tests pass, and retirement evidence is approved. If rewrap is unsupported, evaluate protected in-service DEK re-encryption. Never export DEKs into general application code, logs, or persistent temporary files.

If a KMS key version is unavailable, deny decryption and fail closed. Do not bypass KMS, fall back to another user's key, or silently use a deployment credential. Follow the KMS outage/recovery runbook: confirm key state/version and permissions, restore service or authorized key-version access, verify KMS audit records, and retry only after the envelope context and generation are reauthorized. If a key version is permanently lost, affected credentials cannot be recovered cryptographically; notify users through the approved incident process and require them to re-enter their key after service recovery. Recovery may not reactivate revoked/deleted credentials.

Before retiring any old KMS key version, retain an approval packet containing: inventory of active records and retained backups that referenced the version; evidence all eligible records were rewrapped/migrated; source/target key version and envelope counts reconciled; decrypt/rewrap integrity verification results; backup restore test results; proof no retained record or backup requires the old version; completion of the approved retention window; KMS audit evidence; and security/platform owner signoff. Keep the old version enabled if any item is missing.

### Rotation, revocation, inference concurrency

- **Required guarantee:** after revocation commits in the authoritative credential store, no new provider dispatch may begin for that credential. A request whose outbound dispatch was handed to the provider transport before revocation commits is in flight; it may finish or be processed later and cannot necessarily be recalled. There is no atomic commit spanning the credential store and Groq.
- **One egress boundary:** all Groq calls using user credentials pass through the protected credential/provider dispatch gate. Backend workers, agents, web, and CLI cannot call Groq directly or hold reusable dispatch capabilities. The gate serializes authorization, rotation, revocation, and dispatch initiation per credential. Groq does not validate our fencing tokens, so tokens alone are insufficient; network egress controls must prevent stale workers from bypassing the gate.
- **Concurrency control:** prefer a strongly consistent database transaction/row lock on the credential record, with monotonic generation/state and serialized dispatch. An equivalent distributed lock is acceptable only if lease, ownership, fencing, and lock-loss behavior meet this specification. An in-process Python lock alone is insufficient.
- **Authorization linearization point:** under the authoritative per-credential serialization boundary, the gate checks authenticated tenant/user, active state, and current generation and creates a one-use attempt. It retains ownership through KMS unwrap and the bounded handoff of the outbound request to the provider transport. Dispatch is ordered before revocation only if handoff completes while ownership is valid. Revocation commits only after it acquires the same boundary, updates state/generation, and fences or drains all earlier dispatch critical sections. If revocation wins first, authorization fails and no request is dispatched.
- **Lease, pause, crash, and stale-worker behavior:** a lease lock requires bounded renewal and an unambiguous ownership token. Validate ownership, generation, and fencing token immediately before unwrap and immediately before transport handoff; set a bounded dispatch timeout. If a lease expires, renewal fails, a process pauses beyond its lease, a DB transaction/connection is lost, or ownership cannot be proved, the worker must stop and cannot hand off. Because Groq will not check internal tokens, expired/stale workers must be fenced from network egress by the gate boundary. Revocation cannot commit until the prior owner is confirmed quiesced/fenced. If ownership, fencing, or strong consistency is uncertain, fail closed: deny dispatch and leave revocation pending/unavailable rather than report it committed.
- **Database row-lock alternative:** a strongly consistent DB transaction/row lock may serialize credential state and dispatch initiation. Keep its critical section limited to authorization, KMS unwrap, and bounded provider transport handoff. Never hold a transaction while waiting for the full LLM response. Loss of lock/transaction ownership before handoff aborts the call. All requests must still pass through the egress gate so a stale process cannot bypass a lost DB lock.
- **Rotation:** stage and encrypt a new generation, then atomically activate it under the same serialization boundary using compare-and-swap. Concurrent rotations conflict; one wins and the other reloads/reauthorizes. Once activation commits, the old generation cannot authorize any new inference or retry. Previously dispatched requests are in flight and may complete.
- **Revocation:** serialize with dispatch and rotation, mark revoked and increment generation, then commit. No later dispatch gate operation can hand off a request. An already dispatched request cannot necessarily be recalled; attempt cancellation if supported without claiming success. Never commit revocation while an earlier owner could resume and dispatch.
- **Retries:** each retry is a new attempt that reacquires ownership, checks active state/current generation, unwraps just in time, and validates ownership at handoff. Stale generations/tokens cannot authorize retries. No credential is persisted in retry state or queues.
- **Deletion:** complete revocation under this protocol first, then tombstone and apply the backup lifecycle below. Restore cannot reactivate revoked/deleted credentials.

### Backup, deletion, and recovery lifecycle

Define an approved retention period for encrypted credential-store backups before production persistence. Backups may contain ciphertext and wrapped DEKs, but never plaintext or unwrapped DEKs. Propagate revocation/deletion as a tombstone to the authoritative store, replicas, backup catalogs, and restore filters. Purge backup copies at expiry under the documented retention schedule; where immediate physical deletion from immutable backups is impossible, cryptographic erasure may be used only if all usable wrapping paths are destroyed and verified. Restore procedures must replay tombstones or compare with a current revocation/deletion ledger before making any record available. A deleted credential is not recoverable or reactivated; its user must submit a new key. A revoked credential remains unusable; only an explicit user-authorized new credential generation may replace it.

Operational evidence: approved retention schedule; backup inventory and expiry reports; deletion/tombstone propagation logs; restore test showing revoked/deleted credentials remain unavailable; key-destruction/cryptographic-erasure evidence where used; KMS outage/recovery drill; old-key retirement packet described above.

## 11. Security acceptance checklist

| Security requirement | Automated test or operational evidence |
|---|---|
| CSPRNG nonce generation; no nonce reuse under one DEK; one encryption operation per DEK | Inject deterministic CSPRNG output in tests to force duplicate nonce; verify atomic `(DEK generation, nonce)` uniqueness constraint rejects it and retries with a fresh DEK/nonce. Concurrency test simultaneous registration. Operational crypto-library/runtime review. |
| AEAD tag and AAD/KMS context integrity | Tamper/truncation tests for ciphertext, nonce, tag, AAD fields, KMS context, tenant, subject, provider, record ID, and envelope version; assert fail closed and no plaintext release. |
| Identity and tenant authorization | Owner/peer/cross-tenant/claim-change/guessed-ID tests for all lifecycle operations; periodic authorization review evidence. |
| KMS access, key versions, DEK rewrap and retirement | KMS IAM policy review; rewrap count reconciliation; interrupted migration/recovery test; backup restore test; signed key-retirement evidence packet with inventory, no-reference proof, restore results, audit and approvals. |
| No new provider dispatch after revocation commit; concurrency and ownership loss | Multi-instance tests with the production-equivalent DB lock/dispatch gate: authorize-vs-revoke, unwrap-vs-rotate, handoff-vs-revoke, dual rotations, retries after rotation/revoke, pause beyond lease, renewal failure, crash/restart, stale worker resume, DB transaction loss, gate loss, fencing mismatch, and timeout. Assert no handoff after committed revoke; uncertain ownership denies dispatch and leaves revocation pending until fenced. Operational evidence: egress restriction review, lock/lease/fencing configuration review, bounded dispatch-timeout and revocation runbook. |
| Python plaintext boundary and no guaranteed zeroization claim | Static/config review forbids serialization/logging/persistent storage and provider body/header capture; secret-canary tests inspect agents, prompts, board, logs, traces, metrics, queues, caches, analytics, errors and responses. Operational review documents Python/SDK memory-copy limits, process dump controls and transient lifetime. |
| Backup retention, deletion propagation, and recovery | Backup/replica tombstone propagation test; restore test proves revoked/deleted key is not reactivated; retention expiry/purge report; cryptographic-erasure proof where used; KMS unavailable-version drill and recovery runbook evidence. |
| No silent BYOK fallback; safe user interface/audit | Provider-mode failure tests assert no deployment credential use; API/UI tests ensure keys are never returned/prefilled; audit schema test verifies non-secret fields only. |

## 12. Dependency map and execution order

## 12. Dependency map and execution order

```text
0.1 runtime/package check -> 0.2 graph proof -> 0.3 Groq/credential proof -> 0.4 spike gate
  -> 1.1 identity decision -> 1.2 authorization ----\
                           -> 1.3 KMS/store decision -> 1.4 credential service -> 1.5 redaction
  -> 2.1 registries -> 2.2 context + 2.3 evidence -> 2.4 board
  -> 3.1 source readiness -> 3.2 weather/market + 3.3 crop/schemes -> 3.4 cache/fixtures
  -> 4.1 graph/Intake -> 4.2 Weather/Crop/Market -> 4.3 Scheme/Coordinator -> 4.4 resilience
  -> 5.1 shared backend -> 5.2 web + 5.3 CLI -> 5.4 parity
  -> 6.1 E2E + 6.2 security + 6.3 ops -> 6.4 staged release (approval)
```

Parallelize independent tasks only after interfaces stabilize. BYOK persistence is blocked on identity, authorization, and KMS/store approvals. Each live adapter is blocked on source endpoint/access verification. Do not establish dates until actual task estimates and access lead times are known.

## 13. Continuous test strategy

| Test level | When | Coverage |
|---|---|---|
| Unit | Every phase/task | Mappings, schemas, crypto parsing, lifecycle transitions, freshness, calculations, citations, safety predicates. |
| Contract | Every boundary | Identity claims, tenant policy, registry/evidence, adapters, Groq provider, board writes, agents, backend APIs, web/CLI parity. |
| Security | From Phase 1 onward | AAD/context binding, KMS permissions, tenant isolation, tampering, nonce/tag, rotations/races, redaction, no fallback. |
| Integrated E2E | Phase 6 | Both interfaces; six-agent workflow; multiple crop-regions; all data modes; ambiguity, unsupported and failure cases; BYOK races. |
| Release hardening | Phase 6 | Failure injection, restore, release smoke, monitoring, incident and rollback rehearsals. |

CI uses deterministic fixtures, no live network calls, and no production credentials. Any approved live provider check uses isolated test credentials and secret-safe telemetry.

## 14. Decisions requiring explicit approval

1. **Authentication/tenant identity:** compare options and approve issuer, immutable subject/tenant claims, account linking, membership/admin boundaries, and user deprovisioning. No provider is assumed.
2. **KMS and credential store:** compare options for AEAD/context support, key versions/rewrap, IAM, residency, availability, audit, recovery, deletion, and cost; approve products and lifecycle policy.
3. **Framework spike and versions:** approve passing spike report and exact Python/platform/package pins before broad implementation. Microsoft Agent Framework remains mandatory; substitution requires concrete blocker and approval.
4. **Groq configuration:** initial model IDs, quotas, timeouts/retries, and model/configuration ownership.
5. **Data operations:** source endpoints/rights/owners/update cadence; registry stewards; regional applicability; cache/freshness rules.
6. **Product/privacy operations:** farmer data/consent/retention, interface languages/locales, monitoring thresholds, staged rollout, and rollback approval.

## 15. Framework references to recheck at implementation time

- Microsoft Agent Framework releases: https://github.com/microsoft/agent-framework/releases
- Workflow concepts: https://learn.microsoft.com/en-us/agent-framework/concepts/workflows/
- OpenAI-compatible endpoint configuration: https://learn.microsoft.com/en-us/agent-framework/hosting/self-hosting/openai-endpoints
- Groq OpenAI compatibility: https://console.groq.com/docs/openai

Current API and compatibility details must be rechecked when the spike is performed.

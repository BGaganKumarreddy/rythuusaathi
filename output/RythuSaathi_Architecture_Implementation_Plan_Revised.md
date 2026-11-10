# RythuSaathi Architecture Specification

**Architecture freeze review** · 03 November 2026  
**Status:** Finalized architecture; implementation has not started.

## 1. Product scope and operating principles

RythuSaathi is an all-India agricultural advisory system. Farmers dynamically select their state, district, and crop. The architecture supports multiple crops through an extensible crop registry and does not prescribe a fixed demonstration crop, state, district, or crop-region combination.

Coverage is evidence- and provider-dependent. Registry absence does not mean that a crop is not grown in a location; it means the system cannot currently assert verified coverage there. The system must identify ambiguous and unsupported selections, preserve the actual scope of each source, and never fabricate missing facts, forecasts, prices, agronomic guidance, or scheme eligibility.

## 2. Fixed six-agent architecture

Retain six distinct agents in a Microsoft Agent Framework graph workflow. Each request uses one validated, append-only `AdvisoryBoard` and a sequential workflow:

```text
Request -> Intake -> Weather -> Crop -> Market -> Scheme -> Coordinator
                        shared case context and evidence board
```

Every agent receives the same canonical selected crop and geographic context. Findings carry case/context identifiers, provenance, and availability status. Agents append only to their authorized board sections. The Coordinator reviews evidence completeness, uncertainty, and safety, then renders sourced guidance or a clear insufficient-evidence result.

| Agent | Responsibility | Board output |
|---|---|---|
| **Intake** | Validate and resolve farmer input against versioned registries; ask for clarification where needed. | Canonical state/district/crop IDs; resolution evidence; missing, ambiguous, or unsupported flags. |
| **Weather** | Retrieve weather and warning information for the selected geography. | Forecasts/warnings with source, issue/observation/retrieval time, and data status. |
| **Crop** | Retrieve regionally relevant crop guidance and assess supported weather-stage implications. | Guidance, supporting evidence IDs, applicability limits, and uncertainty. |
| **Market** | Map the selected crop to relevant market commodities and retrieve dated observations. | Market/commodity observations with units, dates, source status, and any reproducible trend. |
| **Scheme** | Find central and applicable state-specific schemes and compare supplied farmer facts with documented criteria. | Dated source passages and criterion-level met/unmet/unknown evidence; no eligibility guarantee. |
| **Coordinator** | Check the whole board for evidence, safety, and completeness. | Final sourced advisory, qualifications, safety notices, and missing/unsupported evidence. |

## 3. Crop, geography, and regional knowledge

Use data-driven, versioned registries and mappings rather than fixed workflow branches:

- **Crop registry:** stable crop IDs, names/aliases and language metadata, taxonomy where available, references, and version/effective-date metadata.
- **Geography registry:** canonical state and district IDs, names/aliases, parent-child relationships, provenance, and version/effective-date metadata.
- **Coverage mappings:** explicit supported crop-region-season or crop-region-stage scope, evidence references, source provenance, effective dates, and limitations.
- **Commodity mappings:** separate crop-to-market commodity and market/district relationships with provenance and ambiguity handling.

Intake resolves selections to canonical IDs. Ambiguous aliases require clarification before location- or crop-specific advice. A missing mapping is recorded as `unsupported` for the affected capability; no nearby location, crop, or commodity is silently substituted. Other agents may still provide independently supported information, labeled with its actual scope.

Regional knowledge retrieval must condition on canonical crop and geography and retain publisher, title, URL, publication/revision date, retrieval date, page/section, geographic scope, and applicability metadata. National guidance is not represented as district-specific unless the source supports that claim.

## 4. Microsoft Agent Framework and workflow

Microsoft Agent Framework is the mandatory orchestration framework. Use its supported graph-based workflow API for the fixed sequential six-agent flow. Do not substitute another framework unless a concrete blocker is demonstrated and the user approves the change.

Before selecting package versions or beginning implementation, complete a bounded compatibility and integration feasibility spike. Current Microsoft release notes advise Python 3.11 or later as the supported baseline; compatibility can vary across packages, optional integrations, and platforms. Target Python 3.11+ provisionally, verify the exact package set/platform, and pin versions only after validation. The Python functional workflow API is documented as experimental and is not the selected workflow API.

### Required feasibility spike and acceptance criteria

The spike is a pre-implementation validation gate, not permission to begin product implementation. It must establish that:

1. The selected Python 3.11+ runtime and candidate Microsoft Agent Framework core/provider packages install and import together on the intended platform without unresolved dependency conflicts.
2. The current supported graph workflow API can run six distinct agent identities in the required order and pass the same canonical case context and board reference through the flow.
3. Groq can be called through the selected Microsoft Agent Framework OpenAI-compatible client configuration and a configurable model ID. Tool/function calls used by the design work end to end; provider errors, timeouts, and workflow events are observable and map to the system's error/status model.
4. A request can use a user-scoped credential through the protected provider/credential layer, while proving that plaintext is absent from agent inputs, prompts, board records, logs, traces, queues, caches, and returned responses.
5. No unsupported framework API, preview feature, or package-specific Python/platform limitation blocks the required flow. If a blocker is found, document reproducible evidence and proposed resolution; framework substitution still requires approval.

Microsoft documents a configurable `base_url` for compatible Python endpoints, and Groq documents an OpenAI-compatible endpoint. This establishes a plausible integration route, not successful compatibility; the spike must verify behavior and API details against the versions selected at that time. [Microsoft Agent Framework release notes](https://github.com/microsoft/agent-framework/releases), [Microsoft workflow concepts](https://learn.microsoft.com/en-us/agent-framework/concepts/workflows/), [Microsoft OpenAI-compatible endpoints](https://learn.microsoft.com/en-us/agent-framework/hosting/self-hosting/openai-endpoints), [Groq OpenAI compatibility](https://console.groq.com/docs/openai).

## 5. Groq provider abstraction

Groq API is the primary hosted inference provider. Keep provider access behind a modular backend abstraction that accepts a configurable model identifier and normalizes request/response, timeout, rate-limit, and provider-error behavior. Model/provider configuration must not change the six agent contracts. Groq-specific request objects and credentials do not enter agents or the board.

Deployment-owned credentials may be supplied by environment variables for local development or deployment secret management for deployment use. Environment templates contain variable names only. These credentials are distinct from individual user BYOK credentials. Deterministic fixture/offline runs require no hosted LLM credentials.

## 6. Multi-user BYOK security architecture

**Selected policy: remember and securely store each user's API key.** Credential handling exists only in the authenticated backend credential/provider layer; agents handle agricultural tasks and never receive credential material.

### Credential lifecycle and isolation

1. An authenticated user submits a key over TLS to a backend endpoint protected by the application's authentication, authorization, CSRF protections where applicable, and request-size controls. Frontend code may hold the key only transiently for entry and submission; never persist it in browser local/session storage, URLs, analytics, or client logs. The backend response contains only a non-secret credential status, never the key.
2. The backend binds each credential record to immutable authenticated tenant and user IDs. Every create, use, rotate, revoke, and delete operation authorizes the caller against that identity. A request cannot select another user's record by supplying an arbitrary ID. Tenant isolation is enforced in the data access layer and checked in tests.
3. Encrypt each user key using envelope encryption: generate/use a data-encryption key (DEK) to encrypt the credential with authenticated encryption; protect the DEK with a KMS-managed key-encryption key (KEK). Store ciphertext, wrapped DEK, key/version metadata, nonce, and integrity metadata in the credential store, separate from the advisory board and general application data. KMS permissions are limited to the backend credential service identity; key administration is separated from routine application access.
4. For an authorized inference request, the backend credential layer fetches the caller's tenant/user-bound record, verifies active status and ownership, asks KMS to unwrap the DEK, and decrypts just in time inside the protected backend inference path. The provider adapter receives the plaintext only as a transient secret input. Clear references/buffers as soon as practical after the provider call; do not serialize the credential or include it in retries persisted to queues.
5. Rotation replaces the active credential through the same authenticated, encrypted flow. Revocation immediately prevents future retrieval/use. Deletion removes the encrypted record and associated metadata according to the documented retention/backup policy; KMS key rotation rewraps DEKs or follows the selected KMS re-encryption procedure without exposing plaintext to agents.
6. Record secret-management audit events (actor, tenant/user, action, time, result, credential record ID) without recording plaintext, ciphertext contents, request bodies, or key-derived values that enable reconstruction. Access to audit events follows least privilege.

### Strict no-exposure boundary and fallback behavior

Plaintext keys must never enter agent instructions or inputs, prompts, the `AdvisoryBoard`, logs, traces, metrics labels, queues, caches, analytics, crash reports, or API responses. Do not use a user credential as a shared cache key or store it in general session state. Secret-bearing request fields must be redacted before telemetry and error handling.

Resolve credential source explicitly for every request. When a user selects BYOK, only that authenticated user's credential may be used. If it is absent, revoked, invalid, or fails, return an appropriate credential/provider error; do not silently fall back to a deployment-level key. Use deployment-owned credentials only under an explicit deployment policy, never as an undisclosed substitute for user BYOK. Never share one user's key with another user.

Credential persistence, KMS integration, and provider credential injection are design requirements only; this document does not implement them.

## 7. Data provenance and reliability

Weather, market, crop-knowledge, and scheme sources use explicit adapters. Central and state-specific government schemes are in scope. Preserve source identity, retrieval time, observation/issue/publication time as applicable, units, geographic/crop scope, and citation location.

Every record distinguishes source mode from availability/freshness status:

- Source mode: `live`, `cached`, `historical`, or `fixture`.
- Status: `available`, `unavailable`, `stale`, `failed`, or `unsupported` (with quality/conflict indicators kept separate where needed).

Fixtures are never represented as live. Cached data includes its original timestamps and cache age. Stale, unavailable, failed, conflicting, or unsupported evidence is made explicit; the Coordinator suppresses dependent claims or qualifies them. Scheme matching records `met`, `unmet`, or `unknown` for each evidenced criterion and never guarantees award/eligibility. The final response cites the evidence behind material claims and states when evidence is insufficient.

The board is a validated typed record of the case, evidence, findings, advisory, and trace. It is append-only for agent findings, enforces each agent's write scope, and contains credential status only if needed, never credential values, ciphertext, or key-derived material.

## 8. Testing and validation plan

Automated tests use deterministic fixtures and no live network calls. Test a matrix of crop-region combinations rather than treating one combination as an architectural limit.

| Test area | Required cases and expected behavior |
|---|---|
| Dynamic context | Multiple supported crops across states/districts; same crop in different regions; multiple crops in one region; all six agents receive exact canonical IDs with no cross-case context leakage. |
| Resolution and coverage | Ambiguous aliases request clarification; unsupported crop-region mappings are explicit; no substitution or unsupported location-specific advice. |
| Regional evidence | Retrieval filters by crop/geography; national and local evidence preserve actual scope; citations and page/section metadata survive to final output. |
| Data reliability | Live provider failure, malformed results, stale cache, historical values, conflicting sources, and no data retain correct source mode/status; dependent claims are withheld or qualified. |
| Schemes | Central and state scheme retrieval; dated citations; each criterion met/unmet/unknown; unknown facts remain unknown. |
| Fixtures and offline | No credentials required; fixtures are clearly labeled and never appear as live observations. |
| BYOK authorization | Cross-user and cross-tenant reads/uses/rotate/delete attempts are denied; arbitrary record IDs cannot bypass owner binding; deployment credentials are not an implicit fallback. |
| BYOK lifecycle | Authenticated encryption integrity checks; KMS unwrap access restricted; rotation, revocation, deletion, and backup/retention behavior verified. |
| BYOK non-disclosure | Secret canary strings do not appear in agent inputs, prompts, board, logs, traces, metrics, queues, caches, analytics, errors, or responses; transient browser handling and redaction are checked. |
| Framework/provider integration | All five acceptance criteria in Section 4 pass with the pinned candidate versions, including graph events/errors, Groq function calls, and user-scoped credential injection. |
| Safety and output | Complete and partial cases; unsupported claims suppressed; uncertainty, source dates, safety notices, and qualified-professional disclaimer appear when appropriate. |

## 9. Genuine unresolved implementation decisions and blockers

These decisions do not alter the frozen architecture, but must be settled or validated before the affected implementation begins:

1. **Framework compatibility spike result:** exact Microsoft Agent Framework package versions, Python patch version, and target OS/platform remain unvalidated. Passing the Section 4 acceptance criteria is a blocker to implementation. Framework replacement requires a concrete blocker and explicit approval.
2. **KMS and credential-store selection:** choose the deployment's KMS/secret manager, encrypted-record store, backup/deletion retention behavior, and key-rotation procedure. The envelope-encryption design and user-bound record model are finalized; the vendor/configuration is not.
3. **Identity integration:** choose the authentication/tenant identity system and define its claims and authorization enforcement points so credential records can be securely bound to user and tenant.
4. **Groq operation settings:** select initial model ID(s), quotas, timeout/retry limits, and configuration ownership. Any retry behavior must preserve per-user credential isolation and must not persist secrets in queues.
5. **Agricultural coverage and source operations:** select initial authoritative crop/geography registries, regional knowledge sources, weather and market endpoints, central/state scheme sources, maintainers, update cadence, and access terms. Coverage remains dynamic and extensible, with no fixed demonstration geography.
6. **Freshness/cache policy:** set retention and category-specific freshness thresholds, invalidation/conflict rules, and which cached data may be presented during provider failure.
7. **Regional applicability threshold:** define evidence criteria for state-, district-, season-, and stage-specific claims and the user-facing explanation for unsupported coverage.
8. **Scheme and data governance:** decide which farmer attributes may be collected, consent/retention rules, and permitted language/output locales and interfaces for first delivery.

## 10. Finalized decisions

- Microsoft Agent Framework is the mandatory orchestration framework; no substitution without a concrete blocker and approval.
- Groq API is the primary hosted LLM provider behind a modular, configurable provider abstraction.
- Multi-user BYOK keys are remembered and securely stored as authenticated user/tenant-bound records using KMS-backed envelope encryption; decryption is just in time in the protected backend inference path.
- Credential plaintext never reaches agents, prompts, logs, traces, queues, caches, analytics, the shared board, or another user; user BYOK never silently falls back to deployment credentials.
- Exactly six agents remain: Intake, Weather, Crop, Market, Scheme, and Coordinator.
- Product scope is all-India and multi-crop, with dynamic state/district/crop selection and extensible registries; there is no fixed demonstration crop or region.
- Recommendations are evidence-grounded, sourced, explicit about data status and geographic/crop applicability, and withheld or qualified when data is unavailable or coverage is unsupported.

## 11. Architecture-only boundary

This is the final architecture freeze review. No application implementation files or dependencies are created, installed, or changed; Git is not initialized; credential storage and the compatibility spike are not executed here.

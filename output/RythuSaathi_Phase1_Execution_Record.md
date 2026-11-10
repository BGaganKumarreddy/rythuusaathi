# RythuSaathi Phase 1 Execution Record

**Status: ACCEPTED.** Tasks 1.1–1.5 are implemented as a local-first backend
slice with the limitations below. The Windows OS-key adapter, authenticated
Groq service integration, and fail-closed SQLite restore were verified. Phase
2 has not started.

## Approved implementation baseline

The project owner selected local authentication, Argon2id password hashing,
opaque server-side sessions, SQLite users/tenant membership and credential
records, owner-only credential access, AES-256-GCM envelope encryption, and an
OS credential store protected KEK. A provider-neutral key-encryption interface
keeps the envelope independent of Windows Credential Manager. No cloud KMS or
external identity service is used.

## Task implementation

| Task | Implementation |
|---|---|
| 1.1 Identity and tenant | `local_auth.py` stores Argon2id hashes, tenant memberships, and only SHA-256 hashes of random opaque session tokens. Authentication derives `VerifiedIdentity` from a live session and active database membership. Login errors are generic; logout, membership removal, account disablement, and expiry invalidate access. |
| 1.2 Authorization | The existing `CredentialAuthorizationPolicy` now permits credential operations only to the owning `(tenant, issuer, subject)`. Credential queries scope tenant before authorization; unknown and cross-tenant records share a generic unavailable result. |
| 1.3 Store and envelope | `credentials.py` uses a fresh random 256-bit DEK and 96-bit nonce per generation. AES-256-GCM encrypts the provider key and wraps the DEK with the KEK returned by `KeyEncryptionInterface`. AAD binds schema, tenant, issuer, subject, credential ID, provider, and generation. `WindowsCredentialStore` explicitly uses the Windows Vault keyring backend, serializes KEK bootstrap across local processes, and fails closed on unsupported platforms, missing keys, malformed keys, or store errors. |
| 1.4 Credential lifecycle | SQLite persists create, status/list, rotate, revoke, and delete tombstones. Rotation creates a fresh envelope and increments generation. Revoke and delete increment generation and deny further use. `AuthenticatedGroqService` authenticates the session and owner-checks the active Groq credential before passing a one-use `ProviderCredential` to the existing provider. |
| 1.5 Audit and disclosure | Audit rows serialize only the existing `CredentialAuditEvent` allowlist. Client status serializes only `CredentialStatus`. Decryption, envelope bytes, and provider keys are never returned by the service; provider exceptions are converted to safe failures. Existing Groq provider files remain unchanged. |

## Revocation semantics

- Revocation prevents every subsequent credential retrieval and dispatch
  attempt; regression tests cover rejection after revoke.
- An external provider request already handed off before revocation commits may
  complete.
- In-flight HTTP cancellation is not guaranteed. RythuSaathi cannot recall an
  HTTP request once the provider has received it.

## Verification

New tests cover Argon2id/session storage and revocation, ownership and tenant
isolation, create/rotate/revoke/delete, AES-GCM tampering, missing OS KEK,
plaintext absence from database/audit/status output, and audit allowlisting.
The Windows integration test created a random test KEK, read the same key from
a fresh Python process, checked missing-key behavior, and deleted the entry.
It printed no key and left no test entry behind. Groq integration tests use
synthetic credentials and a mock HTTP transport; existing Groq provider tests
were not changed.

The accepted implementation entered this bounded update with a **58-test
baseline**. Focused recovery/lifecycle tests:
`python -m unittest tests.test_recovery tests.test_credentials -v` — **10
passed**; the OS-store and authenticated Groq integration tests — **8 passed**.
Final `python -m unittest discover -s tests -v`: **60 passed**.
Syntax compilation with `python -m compileall -q src tests`: **passed**. No real
provider key or live provider request was used.

## SQLite backup and recovery policy

`src/rythusaathi/recovery.py` restores through a staged SQLite backup and
integrity check while the application is stopped. It invalidates restored
sessions, disables all restored non-deleted credentials, erases their old
envelopes, and sets a global recovery gate. Current revoke/delete tombstones
and their audit events override stale backup state. Normal create, rotation,
and dispatch remain blocked until every credential is either explicitly
validated and re-enrolled with the current OS-protected KEK or deleted. Failed
validation cannot activate a credential; deleted IDs cannot be reactivated.
The operator procedure is documented in
`RythuSaathi_Local_SQLite_Recovery_Runbook.md`.

## Local deployment limitations and recovery

- The supplied OS adapter currently supports Windows Credential Manager only.
  Other operating systems fail closed until an explicit adapter is implemented;
  there is no plaintext file or alternate-key fallback.
- A Windows user-profile reset, Credential Manager loss, or corrupted/missing
  KEK makes stored credential envelopes undecryptable. There is no password
  reset recovery of provider keys, escrow, or cloud recovery path; users must
  enter their provider key again.
- Python strings, bytes, SQLite bindings, and cryptographic/provider libraries
  can make copies that the application cannot reliably zeroize. No guaranteed
  memory-zeroization claim is made. Keep process dumps and provider request
  capture disabled operationally.
- The serialization boundary covers service calls using this SQLite database.
  The deployment must ensure all provider dispatches use this service and must
  not invoke the Groq adapter directly with persisted credentials.
- The current Groq interface exposes an async full-call method, not a
  transport-handoff callback. The inference service owner-checks and decrypts
  transactionally, creates the provider coroutine, then awaits it. A separate
  process could theoretically commit revocation in the very small interval
  before that coroutine begins its transport work. Strict cross-process
  revocation-versus-network-handoff fencing therefore remains unproven without
  a provider transport hook; the frozen provider implementation was not changed.
- Backup scheduling, encrypted retention, and the OS key-store profile backup
  remain operator-owned. Recovery is an offline procedure and requires the
  current database to preserve tombstones; no separate remote tombstone ledger
  or cloud escrow exists.
- This implementation is a local backend component, not a complete HTTP API,
  registration policy, password reset system, rate limiter, or production
  deployment. Those interfaces are downstream work and have not been added.

## Phase boundary

No Phase 2 registries, evidence contracts, agents, or workflow were started.
The implementation satisfies the bounded Phase 1 acceptance criteria with
the documented in-flight cancellation and offline recovery limits. The frozen
provider API exposes a full async call rather than a transport-handoff hook;
no transport hooks or provider changes were introduced.

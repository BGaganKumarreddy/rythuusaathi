# RythuSaathi — Phase 7 Security Audit Report

**Date**: 2026-11-12  
**Auditor**: Antigravity Autonomous Security Inspection  
**Repository**: RythuSaathi — Your Intelligent Farming Companion  
**Scope**: FastAPI REST API, Local Authentication, BYOK Credential Vault, Seven-Agent Advisory History, and Diagnostic Tracing  
**Final Verdict**: **APPROVED WITH DOCUMENTED LIMITATIONS** (Ready for Frontend Integration)

---

## 1. Executive Summary

A comprehensive security audit of the RythuSaathi Phase 7 REST API and underlying services was conducted prior to frontend handover. The audit evaluated credential storage, envelope encryption, tenant and user isolation, secret leakage across responses and logs, diagnostic error sanitization, and session lifecycle management.

Two genuine defects were identified and resolved with verified regression tests:
1. **Silent Fallback to Ephemeral Key Storage (High)**: Resolved by enforcing a strict fail-closed policy requiring persistent OS key storage (`WindowsCredentialStore`) in production, with in-memory fallback restricted to explicit test configurations.
2. **Unauthenticated Tenant Privilege Escalation (High)**: Resolved by preventing unauthenticated registrants from self-appointing as `tenant_admin` within existing tenants.

All other evaluated areas—including AES-256-GCM envelope encryption, Argon2id password hashing, SHA-256 session token hashing, user-scoped advisory persistence, and stack trace sanitization—passed inspection with verified controls.

---

## 2. Files Inspected

| Category | Source File | Inspection Purpose |
| :--- | :--- | :--- |
| **Security & Auth** | `src/rythusaathi/security.py` | Identity roles, capability tokens, and policy definitions |
| **Security & Auth** | `src/rythusaathi/local_auth.py` | Argon2id hashing, timing mitigation, session token digests |
| **Vault & Keys** | `src/rythusaathi/credentials.py` | AES-256-GCM envelope encryption, nonces, tenant AAD, OS KEK |
| **API Wiring** | `src/rythusaathi/api/dependencies.py` | Dependency injection, KEK resolution, Bearer authentication |
| **API Wiring** | `src/rythusaathi/api/exceptions.py` | Global exception handlers and information leak prevention |
| **Persistence** | `src/rythusaathi/api/history.py` | SQLite WAL persistence, tenant/user isolation, trace storage |
| **API Endpoints** | `src/rythusaathi/api/routes/auth.py` | Registration, login, logout, identity profile |
| **API Endpoints** | `src/rythusaathi/api/routes/credentials.py` | BYOK credential enrollment, listing, deletion |
| **API Endpoints** | `src/rythusaathi/api/routes/advisory.py` | Advisory consultation execution, trace retrieval |
| **API Endpoints** | `src/rythusaathi/api/routes/history.py` | Paginated consultation history retrieval |
| **API Endpoints** | `src/rythusaathi/api/routes/configuration.py` | Crops, locations, and provider catalog endpoints |

---

## 3. Findings, Evidence, and Resolutions

### Finding 1: Silent Fallback to Ephemeral In-Memory KEK
- **Severity**: **High**
- **Affected File**: `src/rythusaathi/api/dependencies.py`
- **Description**:  
  When `get_key_store()` initialized the Key Encryption Key (KEK) store, it caught all exceptions from `WindowsCredentialStore()` and silently instantiated `InMemoryKeyStore()`. In production environments where OS credential storage failed (or was misconfigured), Data Encryption Keys (DEKs) were encrypted using volatile in-memory keys. Upon application restart, all in-memory keys were lost, rendering previously encrypted BYOK credentials permanently undecryptable.
- **Code Evidence Before Fix**:
  ```python
  def get_key_store() -> KeyEncryptionInterface:
      global _KEY_STORE
      if _KEY_STORE is None:
          try:
              _KEY_STORE = WindowsCredentialStore()
              _KEY_STORE.get_or_create_key("api-check-v1")
          except Exception:
              _KEY_STORE = InMemoryKeyStore()
      return _KEY_STORE
  ```
- **Resolution**:  
  Enforced a fail-closed policy. If `WindowsCredentialStore()` fails, the system raises `OsCredentialStoreUnavailable` unless the environment variable `RYTHUSAATHI_ALLOW_IN_MEMORY_KEY_STORE=1` is explicitly set. The exception is caught by FastAPI's global handler, returning HTTP 503 (`os_key_vault_unavailable`).
- **Verification**:  
  Verified via regression tests:
  - `test_key_store_fails_closed_without_explicit_test_flag`: Passes and raises `OsCredentialStoreUnavailable`.
  - `test_key_store_allows_in_memory_with_explicit_env_flag`: Passes when flag is explicitly `"1"`.

---

### Finding 2: Unauthenticated Privilege Escalation to Tenant Admin
- **Severity**: **High**
- **Affected File**: `src/rythusaathi/api/routes/auth.py`
- **Description**:  
  During registration via `POST /api/v1/auth/register`, if a caller passed an existing `tenant_id` alongside `role="tenant_admin"`, `auth_service.create_tenant(...)` raised a handled exception, and the code proceeded to register the user directly into that existing tenant with the requested `tenant_admin` role. An unauthenticated external user could thereby hijack administrative authority over an existing tenant.
- **Code Evidence Before Fix**:
  ```python
  # Ensure tenant exists; create if registering a new tenant
  try:
      auth_service.create_tenant(req.tenant_id, req.tenant_name or req.tenant_id)
  except Exception:
      pass

  user_id = auth_service.register(
      username=req.username,
      password=req.password,
      tenant_id=req.tenant_id,
      role=req.role,
  )
  ```
- **Resolution**:  
  Added a pre-check query on the `tenants` table. If the tenant already exists and the unauthenticated registrant requests `role=IdentityRole.TENANT_ADMIN`, the request is immediately rejected with HTTP 403 Forbidden (`code="privilege_escalation_denied"`). Creating a brand new tenant with `role=tenant_admin` or joining an existing tenant as a standard `member` remains supported.
- **Verification**:  
  Verified via regression tests:
  - `test_privilege_escalation_to_tenant_admin_on_existing_tenant_rejected`: Returns HTTP 403.
  - `test_new_tenant_registration_with_admin_role_allowed`: Returns HTTP 201 for fresh tenants.
  - `test_existing_tenant_member_registration_allowed`: Returns HTTP 201 for `member` role.

---

### Finding 3: Open Tenant Self-Registration Without Invitation Verification
- **Severity**: **Medium / Architectural Consideration**
- **Affected File**: `src/rythusaathi/api/routes/auth.py`
- **Description**:  
  In the current implementation, any user can self-register as a `member` of any known existing tenant ID without presenting an invitation code or administrative approval token.
- **Risk Assessment**:  
  Because all operational data (credentials in `CredentialService`, past consultations in `AdvisoryHistoryStore`, and traces) is partitioned with composite keys `(tenant_id, user_id)`, a rogue member in an existing tenant **cannot** access or view any other member's credentials, advisories, or diagnostic traces. Cross-user data isolation is strictly enforced.
- **Status**:  
  Documented as a known architecture limitation. For single-deployment and local cooperative use, open member registration is acceptable; future multi-enterprise deployments should introduce signed invite tokens.

---

### Finding 4: Credential and Secret Secrecy
- **Severity**: **Informational (Passed)**
- **Files**: `src/rythusaathi/credentials.py`, `src/rythusaathi/api/routes/credentials.py`
- **Analysis**:
  1. Enrolled secrets are encrypted using AES-256-GCM with a random 96-bit nonce and Additional Authenticated Data (AAD) binding the ciphertext to `tenant_id` and `credential_id`.
  2. The `CredentialResponse` schema exposes only `credential_id`, `provider_id`, `state`, `generation`, and `updated_at`.
  3. Raw secrets are never included in API responses or logs.
  4. Deletion zeroizes/removes the ciphertext from SQLite.
- **Verification**:  
  `test_create_and_list_credential_with_secrecy` and `test_secret_is_never_leaked_in_credential_listing_or_trace` verified zero leakage in responses or traces.

---

### Finding 5: Session Token and Password Security
- **Severity**: **Informational (Passed)**
- **File**: `src/rythusaathi/local_auth.py`
- **Analysis**:
  1. Passwords are hash-verified using Argon2id with memory-hard parameters.
  2. Failed login attempts execute a dummy Argon2 verification against a precomputed hash to mitigate account-enumeration timing side channels.
  3. Session tokens are 256-bit URL-safe tokens (`secrets.token_urlsafe(32)`).
  4. The SQLite database stores only the `SHA-256` digest of session tokens (`token_hash`), ensuring that a database compromise does not leak active session tokens.
  5. Logout immediately purges the session record.

---

### Finding 6: Diagnostic Error Sanitization
- **Severity**: **Informational (Passed)**
- **File**: `src/rythusaathi/api/exceptions.py`
- **Analysis**:
  1. All unhandled server exceptions return a sanitized JSON body:  
     `{"code": "internal_server_error", "message": "An internal error occurred while processing the request", "details": {}}`
  2. Internal filesystem paths, tracebacks, and database connection strings are never exposed to clients.
  3. Validation errors map Pydantic error locations cleanly without revealing environmental details.

---

## 4. Verification Matrix

| Test Case | Component | Expected Outcome | Result |
| :--- | :--- | :--- | :--- |
| `test_key_store_fails_closed_without_explicit_test_flag` | Dependencies | `OsCredentialStoreUnavailable` raised | **PASSED** |
| `test_key_store_allows_in_memory_with_explicit_env_flag` | Dependencies | `InMemoryKeyStore` returned | **PASSED** |
| `test_privilege_escalation_to_tenant_admin_on_existing_tenant_rejected` | Auth API | HTTP 403 Forbidden | **PASSED** |
| `test_new_tenant_registration_with_admin_role_allowed` | Auth API | HTTP 201 Created | **PASSED** |
| `test_existing_tenant_member_registration_allowed` | Auth API | HTTP 201 Created | **PASSED** |
| `test_secret_is_never_leaked_in_credential_listing_or_trace` | Credentials & Advisory | Secret absent in all payloads | **PASSED** |
| `test_cross_user_credential_isolation` | Credentials API | Cross-user 404/Empty | **PASSED** |
| `test_history_tenant_and_user_isolation` | History API | Cross-tenant 404 | **PASSED** |
| `test_unauthorized_access_rejected` | Auth API | HTTP 401 Unauthorized | **PASSED** |

---

## 5. Remaining Limitations

1. **OS-Specific Key Store**: The persistent KEK relies on Windows Credential Manager (`WindowsCredentialStore`). Deployment to Linux/macOS requires implementing an OS-specific key backend (e.g., Linux Secret Service / Keyutils) or providing an HSM/KMS adapter.
2. **Tenant Membership Approval**: Open registration into existing tenants allows anyone knowing the `tenant_id` to join as `member`. An invite-token mechanism should be introduced when transitioning from local cooperative to public cloud SaaS.
3. **In-Flight Revocation of Credentials**: Credential deletion immediately removes keys from the database, but active background tasks already holding decrypted inference clients retain access until task completion.

---

## 6. Final Verdict

**APPROVED WITH DOCUMENTED LIMITATIONS**  
The backend implementation meets production security requirements for local-first deployment, protects user credentials with AES-256-GCM envelope encryption, enforces strict tenant and user isolation, and prevents secret leakage across all REST endpoints. It is approved for frontend development handover.

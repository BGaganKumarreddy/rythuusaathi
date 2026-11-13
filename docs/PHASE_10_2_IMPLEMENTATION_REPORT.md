# AgroSage — Phase 10.2: Minimal Root-Cause Implementation Report
**Evidence-Grounded Agricultural Multi-Agent System & Conversational AI Experience**

**Date**: November 2026  
**Status**: IMPLEMENTATION COMPLETE & VERIFIED  
**Target Query Verified**: *"My rice leaves are showing brown leaf spots during tillering stage. What safe organic controls are available?"*  

---

## 1. Executive Summary

Phase 10.2 resolves the root causes identified in Phase 10.1 without redesigning the 7-agent Microsoft Agent Framework architecture or weakening BYOK envelope encryption security.

Previously, running the query *"My rice leaves are showing brown leaf spots during tillering stage. What safe organic controls are available?"* caused all specialist agents (Crop, Weather, Market, Scheme, Risk Alert) to be skipped due to unnecessary preconditions (e.g., hard-requiring a registered location for general crop science), while the chat UI silently defaulted to deterministic mode with synthetic credential UUIDs and concealed agent skip reasons behind null summaries.

With Phase 10.2:
1. **Crop Agent** retrieves nationwide ICAR agronomic guidance when location is unconfirmed, clarifying that local advice can be refined if location is provided, while never fabricating local recommendations.
2. **Knowledge Retrieval** correctly enforces geographic hierarchy: nationwide documents apply across India, state documents apply to their districts, district documents apply strictly locally, and unknown metadata is rejected under location filters.
3. **Scheme Agent** accurately reports that scheme lookup was skipped because no government scheme inquiry was requested, rather than misleadingly citing a missing location.
4. **Risk Alert Agent** screens background crop and stage vulnerabilities without requiring a registered location, reserving active alerts for evidence-backed acute triggers.
5. **Execution Mode & Provider Selection** is explicit in the frontend and API; active BYOK credentials are resolved via authenticated user/tenant ownership; synthetic UUID generation is eliminated; requests for un-enrolled LLMs fail cleanly with HTTP 400 `missing_provider_credential` without silent downgrade.
6. **Telemetry & Visibility** preserves retrieval notes in `AgentResultSchema.summary` and surfaces structured reasons in the conversational UI.

---

## 2. Detailed Root-Cause Fixes

### 2.1 Execution Mode, BYOK Resolution & Error Handling
- **Explicit Mode in UI & API**: Added an execution mode dropdown in the chat header allowing farmers to select between `Deterministic Baseline`, `Groq Cloud LPU`, `Cerebras Wafer-Scale`, and `Google Gemini AI Studio`.
- **Elimination of Synthetic UUIDs**: In `src/rythusaathi/api/routes/conversations.py`, removed `cid = f"cid:{uuid.uuid4().hex}"`. The API now looks up active credentials owned by the verified user and tenant via `credential_service.list_status(identity, provider_id=prov_key)`.
- **Clean Failure Without Downgrade**: If an LLM mode is selected but no active BYOK credential exists, the API raises HTTP 400 with `code="missing_provider_credential"`. The frontend displays an actionable toast notification directing the farmer to the BYOK Vault (`/credentials`).
- **Deterministic Transparency**: When deterministic mode is executed, it is explicitly identified in the request, response metadata, and synthesis text.

### 2.2 Crop Agent Decoupling & Clarification
- **Precondition Decoupling**: In `src/rythusaathi/agents/workflow.py`, changed `CropAgent.perform` precondition from `if not crop_id or not location_id:` to `if not crop_id:`.
- **Nationwide Scoping**: When `location_id` is unconfirmed, retrieval queries nationwide ICAR guidance (`geographic_applicability is None` or includes `"location:country:in"`), filtering out regional state bulletins to prevent fabricating localized recommendations.
- **Location Refinement Notice**: Added explicit advisory text:
  *"Local agronomic recommendations can be refined if you provide your location."*
- **Clarification Protection**: If a farmer specifies an unregistered location (e.g. "Narnia", `FactState.UNAVAILABLE`), `CropAgent` cleanly skips and asks for location clarification instead of falling back to unrelated nationwide data.

### 2.3 Knowledge Retrieval Geographic Hierarchy
In `src/rythusaathi/knowledge/retrieval.py` and `src/rythusaathi/knowledge/persistent.py`:
- `_geography_applies(scopes, requested)` enforces:
  1. Nationwide (`"location:country:in"`): Applies to all states and districts in India.
  2. State-scoped (`"location:state:{state}"`): Applies to that state and all districts within it (`"location:district:{state}-*"`).
  3. District-scoped: Matches exact district scope only.
  4. Unknown / Empty (`None` or `()`): Evaluates to `False` whenever a location filter is requested, preventing unvalidated documents from masquerading as nationwide knowledge.

### 2.4 Scheme Agent Skip Reason Accuracy
- **Accurate Skip Reason**: In `SchemeAgent.perform`, when `not scheme_requested and not location_id`:
  `note = "No government scheme inquiry was requested; scheme lookup is skipped."`
- **Nationwide Central Schemes**: When a scheme inquiry is explicitly made without a location (e.g. *"What central government schemes like PM-KISAN are available?"*), `SchemeAgent` retrieves central programs using `geography="location:country:in"` and `jurisdiction_level="central"`.

### 2.5 Risk Alert Agent General Vulnerability Screening
- **Background Vulnerability Screening**: Allowed `evaluate_disease_risks` and `evaluate_pest_risks` to execute when `crop_id` is present even without a `location_id`.
- **Acute Weather Guard**: Acute weather-dependent alerts (`evaluate_weather_risks`) remain strictly gated on `location_id is not None` with live sensor observations.
- **Two-Tier Separation**: Background vulnerabilities (e.g. general blast susceptibility) are recorded as low-severity informational context and never promoted to active emergency alerts without supporting field triggers.

### 2.6 Telemetry Serialization & Reason Exposure
- **Summary Preservation**: Updated `AgentResultSchema` serialization in `src/rythusaathi/api/routes/conversations.py` and `advisory.py` to extract `getattr(ar.payload, "summary", getattr(ar.payload, "note", None))`, preserving retrieval notes for `CropAgent` and `SchemeAgent`.
- **Conversational UI Rendering**: Updated `ConversationalAdvisoryPage.jsx` to render `ar.summary` inside the 7-agent execution trace accordion with dedicated `data-testid` attributes.

---

## 3. Regression Test Verification

### 3.1 Backend Regression Suite (`tests/test_phase10_regression.py`)
All 13 required test cases passed:

| # | Test Name | Target Behavior | Result |
|---|---|---|---|
| 1 | `test_crop_retrieval_without_location_succeeds_and_returns_icar_guidance` | CropAgent retrieves ICAR guidance without location; no local recommendations fabricated | **PASSED** |
| 2 | `test_nationwide_icar_retrieval_with_geography_filter` | Nationwide scope (`location:country:in`) applies to states & districts | **PASSED** |
| 3 | `test_state_specific_retrieval_with_matching_state_vs_mismatching_state` | State documents apply to state & child districts; rejected for other states | **PASSED** |
| 4 | `test_unknown_geographic_metadata_rejected_when_geography_filter_requested` | `None` and empty scopes rejected under location filter | **PASSED** |
| 5 | `test_central_scheme_retrieval_without_location_when_explicitly_requested` | Central schemes retrieved nationwide when explicitly queried | **PASSED** |
| 6 | `test_nonscheme_question_produces_accurate_skip_reason` | Non-scheme question reports skip reason without blaming missing location | **PASSED** |
| 7 | `test_general_risk_vulnerability_assessment_without_location_succeeds` | Background risk screening succeeds without location | **PASSED** |
| 8 | `test_no_unsupported_active_risk_alert_issued_when_location_or_triggers_missing` | Zero speculative active alerts issued when triggers absent | **PASSED** |
| 9 | `test_llm_mode_with_valid_owner_scoped_credential_resolves_real_credential_id` | Valid BYOK credential resolved via authenticated identity | **PASSED** |
| 10 | `test_llm_mode_without_credential_fails_cleanly_with_http_400` | Un-enrolled provider fails with HTTP 400 `missing_provider_credential` | **PASSED** |
| 11 | `test_no_synthetic_credential_uuid_generated_in_conversation_dispatch` | Real credential ID used in dispatch; no synthetic UUIDs | **PASSED** |
| 12 | `test_deterministic_mode_is_accurately_identified_in_execution_mode` | Deterministic execution explicitly declared in API and response | **PASSED** |
| 13 | `test_api_preserves_retrieval_payload_notes_and_skip_reasons_in_agent_result_schema_summary` | AgentResultSchema contains non-null descriptive summaries | **PASSED** |

### 3.2 Frontend Vitest Suite (`frontend/src/test/conversations.test.jsx`)
Added test cases 5 and 6 covering:
- Changing execution mode and verifying `mode` passed in `conversationService.createConversation`.
- Expanding the 7-agent execution trace and verifying `ar.summary` skip reasons rendered in UI.
- **Result**: 26 passed across 5 test suites.

### 3.3 Production Frontend Build
- `npm run build`: Succeeded in 949ms without TypeScript or Rollup bundling errors.

---

## 4. Security & Isolation Compliance

- **No Secret Leakage**: No API keys, decrypted credentials, or session tokens are printed in logs, serialized in API responses, or stored in plaintext.
- **Owner & Tenant Scoping**: BYOK credentials remain encrypted with AES-256-GCM. Credential resolution strictly filters by authenticated `identity.subject` and `identity.tenant_id`.
- **Clean Failure Protocol**: Requested LLM modes without active credentials fail with HTTP 400; no silent downgrade occurs.


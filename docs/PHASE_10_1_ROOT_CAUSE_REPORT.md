# AgroSage — Phase 10.1: Investigation & Root-Cause Analysis Report
**Deterministic Execution, Specialist Agent Skipping, and Telemetry Deficiencies**

**Date**: November 2026  
**Status**: INVESTIGATION COMPLETE — AWAITING REVIEW BEFORE IMPLEMENTATION  
**Target Query**: *"My rice leaves are showing brown leaf spots during tillering stage. What safe organic controls are available?"*  

---

## 1. Executive Summary

When executing the query *"My rice leaves are showing brown leaf spots during tillering stage. What safe organic controls are available?"*, the system returned a partial deterministic advisory where all five specialist agents (Crop, Weather, Market, Scheme, and Risk Alert) were skipped, leaving the Coordinator with zero domain evidence.

Our runtime investigation identified four distinct root causes:
1. **Deterministic Mode by Default**: The frontend conversational interface does not provide a provider selector or detect enrolled BYOK credentials, defaulting the API payload to `mode: "deterministic"`. Furthermore, the active user account in `tmp/rythusaathi-auth.sqlite3` had zero enrolled BYOK credentials.
2. **Crop Agent Unnecessary Location Requirement**: `CropAgent.perform` hard-requires `location_id` (`if not crop_id or not location_id: skip`). It completely skips general ICAR agronomic retrieval even when a valid crop (`Rice`), stage (`tillering`), and symptom (`brown leaf spots`) are supplied, despite nationwide ICAR bulletins existing in the database without geographical restrictions.
3. **Knowledge Retrieval Geographic Filtering Flaw**: In `src/rythusaathi/knowledge/retrieval.py`, `_geography_applies(None, requested_geography)` evaluates to `False`. When any location filter is provided, all nationwide documents (`geographic_applicability=None`) are rejected.
4. **Telemetry Loss for Skipped Agents**: `AgentResultSchema.summary` extracts `getattr(ar.payload, "summary", None)`. However, `RetrievalPayload` (used by CropAgent and SchemeAgent) stores its message in `.note`, causing the skip reason to be serialized as `null` in API responses and omitted in the frontend.

---

## 2. Investigation 1: Provider Selection & Deterministic Execution

### 2.1 Trace from API Request to Provider Dispatch
1. **Frontend Request**: In `frontend/src/pages/chat/ConversationalAdvisoryPage.jsx` (lines 163–171), `conversationService.createConversation()` is called without specifying `mode`.
2. **Client Default**: In `frontend/src/services/conversationService.js` (line 17), `mode` defaults to `"deterministic"`.
3. **API Schema Default**: In `src/rythusaathi/api/schemas/conversations.py` (lines 60, 71), `CreateConversationRequest` and `SendMessageRequest` default to `WorkflowMode.DETERMINISTIC`.
4. **Database State**: Inspection of `tmp/rythusaathi-auth.sqlite3` shows user `test1` has **0 active credentials** in table `credentials`.
5. **Environment State**: No fallback provider keys are present in environment variables (`CEREBRAS_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY` are all unset).
6. **Provider Dispatch**: In `src/rythusaathi/api/routes/conversations.py` (lines 98–99):
   ```python
   inference = None
   if mode in {WorkflowMode.CEREBRAS, WorkflowMode.GEMINI, WorkflowMode.GROQ}:
       ...
   ```
   Because `mode == WorkflowMode.DETERMINISTIC`, `inference` remains `None`.
7. **Execution**: `workflow.run(farmer_request, mode=WorkflowMode.DETERMINISTIC, inference=None)` executes the deterministic heuristic synthesis path.
8. **Secondary Discovery (Latent Defect)**: In `src/rythusaathi/api/routes/conversations.py` (line 106), if an LLM mode *had* been passed, it generated a synthetic `cid = f"cid:{uuid.uuid4().hex}"` rather than looking up the active enrolled `credential_id` from `credential_service.list_status(identity, provider_id=...)`. This would have caused `CredentialNotFound` inside `dispatch_handoff`.

### 2.2 Answers to Investigation 1 Questions

| # | Question | Finding | Evidence / Source |
|---|---|---|---|
| **1** | Which provider is configured for this user and tenant? | None. Table `credentials` in `tmp/rythusaathi-auth.sqlite3` is empty for tenant `default` and user `test1`. | `SELECT * FROM credentials` returned 0 rows. |
| **2** | Which model is selected? | None (`model_id: None`). Deterministic execution does not use an LLM model. | Stored in `advisory_history` with `model_id: NULL`. |
| **3** | Is an encrypted BYOK credential available? | No. Neither in database nor in environment variables. | `credentials` table empty; `os.getenv` returned `None`. |
| **4** | Is credential decryption successful? | N/A (not attempted due to deterministic mode). | No credential to decrypt. |
| **5** | Is provider selection reaching Microsoft Agent Framework? | Yes, reaching `RythuSaathiWorkflow.run()` with `mode=WorkflowMode.DETERMINISTIC`. | `src/rythusaathi/agents/workflow.py:2708` |
| **6** | Does the workflow call the LLM? | No. `inference` is `None` in deterministic mode. | Workflow branches directly to deterministic heuristic executors. |
| **7** | Which exact condition selects deterministic execution? | `mode: WorkflowMode = WorkflowMode.DETERMINISTIC` in request schemas and default parameter in `conversationService.js`. | `frontend/src/services/conversationService.js:17` |
| **8** | Is deterministic mode intentional or fallback? | Intentional default when no mode is requested in the payload. | No provider failure occurred. |
| **9** | Is there a provider failure, quota guard, or missing configuration? | Missing configuration: frontend chat does not select or pass an active provider, and user has not enrolled BYOK keys. | `ConversationalAdvisoryPage.jsx:163` |
| **10**| Is the execution mode correctly reported by the API? | Yes. API reports `execution_mode: "deterministic"`. | `advisory_history.execution_mode == 'deterministic'`. |

---

## 3. Investigation 2: Agent Routing & Skip Reasons

### 3.1 Specialist Predicate Audit

| Agent | File & Function | Exact Predicate Triggered | Skip Reason Reported | Correctness Assessment |
|---|---|---|---|---|
| **Crop Agent** | `src/rythusaathi/agents/workflow.py`<br>`CropAgent.perform` (L1539) | `if not crop_id or not location_id:` | *"Crop and registered location are required for scoped agricultural retrieval."* | **INCORRECT**. Location is NOT required for general ICAR crop agronomy, nutrient management, or organic symptom controls. |
| **Weather Agent** | `src/rythusaathi/agents/workflow.py`<br>`WeatherAgent.perform` (L1618) | `if not location_id:` | *"Weather lookup requires a registered location; none was supplied."* | **CORRECT**. Live weather observations require latitude and longitude coordinates. |
| **Market Agent** | `src/rythusaathi/agents/workflow.py`<br>`MarketAgent.perform` (L1752) | `if not crop_id or not location_id:` | *"Market lookup requires registered crop and location values; one or both are missing."* | **CORRECT**. Agmarknet APMC quotes are tied to specific physical mandis in registered districts. |
| **Scheme Agent** | `src/rythusaathi/agents/workflow.py`<br>`SchemeAgent.perform` (L1891) | `if not location_id and not scheme_requested:` | *"Scheme lookup requires a registered location to filter state and national programs."* | **MISLEADING REASON**. The agent skipped because no scheme was requested AND no location was provided. The message incorrectly implies location was the sole blocker. |
| **Risk Alert Agent** | `src/rythusaathi/agents/workflow.py`<br>`RiskAlertAgent.perform` (L2199) | `if not evidence_ids and (not crop_id or not location_id):` | *"Crop and registered location are required for evidence-backed disease and pest risk assessment."* | **PARTIALLY INCORRECT**. While acute weather hazards require location, general disease and insect pest vulnerability assessment is crop- and stage-specific and can operate from ICAR bulletins without location. |

### 3.2 Deep-Dive: Why Crop Agent Incorrectly Required Location
In `CropAgent.perform`:
```python
crop_id = _resolved_id(case.context.crop)
location_id = _resolved_id(case.context.location)
if not crop_id or not location_id:
    detail = "Crop and registered location are required for scoped agricultural retrieval."
    return _AgentWork(AgentStatus.SKIPPED,
                      RetrievalPayload(0, (), (), (), detail), detail)
```
- **Consequence**: When a farmer asks *"My rice leaves are showing brown leaf spots during tillering stage. What safe organic controls are available?"*, `crop_id` resolves to `crop:oryza-sativa`, but `location_id` is `None`. The agent immediately aborts.
- **Data Reality**: `data/agricultural/icar_advisories.json` contains nationwide ICAR documents (`icar-kharif-2025:rice-protection`, `icar-kharif-2025:rice-nutrients`, `icar-kharif-2025:rice-nursery`) with `geographic_applicability: None`. These documents provide official scientific guidance for all Indian farmers regardless of state or district.
- **Secondary Retrieval Defect**: In `src/rythusaathi/knowledge/retrieval.py` (lines 88–93):
  ```python
  def _geography_applies(scopes, requested: str) -> bool:
      if scopes is None:
          return False
      if requested in scopes:
          return True
      return ("location:country:in" in scopes and requested.startswith("location:"))
  ```
  If a document has `geographic_applicability: None` (nationwide), querying with `geography="location:district:in-pb-ludhiana"` returns `False`! Nationwide knowledge is rejected whenever a specific geography filter is applied.

---

## 4. Investigation 3: Scheme Retrieval Verification

### 4.1 Standalone Test Results
We verified retrieval of nationwide central schemes without a location using:
`filters = RetrievalFilter(geography="location:country:in", topic="government_scheme", jurisdiction_level="central")`

Results:
- **5 chunks retrieved**:
  - `PM-KISAN Operational Guidelines (Revised)` (Source: `pmkisan.gov.in`)
  - `Pradhan Mantri Fasal Bima Yojana — Revamped Operational Guidelines (2020)` (Source: `pmfby.gov.in`, 3 excerpts)
  - `PM-KISAN Operational Guidelines (Revised)` (Eligibility cut-off date: 1 February 2019)
- **Provenance & Dates**: All documents contain valid publication dates, official URLs, and explicit disclaimer statements.
- **Behavior in Query**: In the target query, SchemeAgent skipped because `scheme_requested` was `False` (the query contained no scheme keywords like *"subsidy"*, *"scheme"*, *"pm-kisan"*, etc.). This is correct domain behavior (a pest question should not trigger financial subsidy advice), but the skip reason given (*"requires a registered location"*) misled the user into thinking location was the missing factor.

---

## 5. Investigation 4: Context & Registry Alignment

### 5.1 End-to-End Field Mapping

| Parameter | User Query Extraction | Context State (`ContextStateSchema`) | Request Entity (`FarmerRequest`) | Registry Resolution | Status |
|---|---|---|---|---|---|
| **Crop** | `"rice"` | `"Rice"` | `crop_query="Rice"` | Canonical ID: `crop:oryza-sativa` | **Aligned** |
| **Growth Stage** | `"tillering"` | `"tillering"` | `growth_stage_query="tillering"` | Stage ID: `tillering` (Order 20) | **Aligned** |
| **Observations** | `"brown leaf spots"` | `["brown leaf spots"]` | Query string | Stored in `IntakePayload.observations` | **Aligned** |
| **Location** | None (unspecified) | `None` | `location_query=None` | `FactState.UNCONFIRMED` | **Aligned** |

- **Conclusion**: There is no contract disagreement between frontend payloads, API schemas, Intake parsing, and domain registry canonical IDs. The context pipeline functions as designed. The failure originates inside the downstream agent execution guards.

---

## 6. Investigation 5: Telemetry & Reason Exposure

### 6.1 Serialization Defect in `AgentResultSchema`
In `src/rythusaathi/api/routes/conversations.py` (line 169) and `src/rythusaathi/api/routes/advisory.py`:
```python
summary_val = getattr(ar.payload, "summary", None) if ar.payload else None
```
- For `ProviderPayload` (Weather, Market) and `RiskAlertPayload`, the field is named `.summary`.
- For `RetrievalPayload` (CropAgent, SchemeAgent), the field is named `.note`.
- **Result**: `summary_val` evaluates to `None` for CropAgent and SchemeAgent. The API sends `summary: null`.

### 6.2 Frontend Display Defect
In `frontend/src/pages/chat/ConversationalAdvisoryPage.jsx` (lines 460–480):
- The telemetry tray renders only `ar.agent_id`, latency, and a status badge (`Skipped`).
- It does not render `ar.summary` or any detail field.
- The farmer cannot see why an agent was skipped.

---

## 7. Expected Behavior vs. Actual Behavior

| Component | Target Inquiry | Actual Behavior | Expected Behavior |
|---|---|---|---|
| **Crop Agent** | Rice + Tillering + Brown Spots (no location) | **SKIPPED**: *"Crop and registered location are required..."* | **COMPLETED**: Retrieve ICAR national rice nutrient and disease protection bulletins. Advise organic controls (e.g. neem formulations, balanced fertilizer, seed treatment) and request location only to refine regional recommendations. |
| **Risk Alert Agent** | Rice + Tillering + Brown Spots (no location) | **SKIPPED**: *"Crop and registered location are required..."* | **COMPLETED**: Screen ICAR disease vulnerabilities for Rice at Tillering stage. Report background vulnerability to Brown Spot / Blast, advise monitoring, and skip only weather-dependent acute alerts. |
| **Weather Agent** | No location | **SKIPPED**: *"Weather lookup requires a registered location..."* | **SKIPPED**: Retain clear explanation that agrometeorological sensors require a specific district. |
| **Market Agent** | Rice (no location) | **SKIPPED**: *"Market lookup requires registered crop and location..."* | **SKIPPED**: Retain clear explanation that APMC mandi trade rates are district-specific. |
| **Scheme Agent** | Pure crop symptom inquiry (no location) | **SKIPPED**: *"Scheme lookup requires a registered location..."* | **SKIPPED**: Clarify that no government scheme was queried and no location was provided. |
| **Coordinator** | Synthesis | Partial advisory stating location is needed for everything. | Synthesis addressing the farmer's question using ICAR crop knowledge, noting that weather and mandi rates are unavailable without a location. |
| **Telemetry** | Execution Trace | Generic "Skipped" badge with `null` summary. | Structured reason displayed for every skipped agent (e.g. *"Location required for local APMC mandi quotes"*). |

---

## 8. Proposed Minimal Fixes (For Review)

### Fix 1: Decouple CropAgent from Mandatory Location
- In `src/rythusaathi/agents/workflow.py` (`CropAgent.perform`):
  - Change precondition to: `if not crop_id: return _AgentWork(SKIPPED, ...)`
  - When `location_id` is `None`, search knowledge using `crop_id` and nationwide/unrestricted scope.
  - When `location_id` is present, search both localized scopes (`_location_scopes(location_id)`) and nationwide scope.

### Fix 2: Repair Nationwide Knowledge Filtering
- In `src/rythusaathi/knowledge/retrieval.py` (`_geography_applies`):
  - If a document chunk has `geographic_applicability is None` or includes `"location:country:in"`, it must apply to all queries across India.
- In `CropAgent.perform`:
  - When filtering by `growth_stage`, allow chunks with `growth_stage is None` (general guidance) to match alongside stage-specific chunks.

### Fix 3: Enable Background Disease & Pest Screening in RiskAlertAgent
- In `src/rythusaathi/agents/workflow.py` (`RiskAlertAgent.perform`):
  - Allow `evaluate_disease_risks` and `evaluate_pest_risks` to execute when `crop_id` is present, evaluating background crop vulnerabilities even if `location_id` is `None`.
  - Gate only weather-based acute risk alerts (`evaluate_weather_risks`) behind `location_id is not None`.

### Fix 4: Fix Telemetry Summary Extraction
- In `src/rythusaathi/api/routes/conversations.py` and `advisory.py`:
  - Extract summary using:
    ```python
    summary_val = getattr(ar.payload, "summary", getattr(ar.payload, "note", None)) if ar.payload else None
    ```
- In `ConversationalAdvisoryPage.jsx`:
  - Display `ar.summary` inside the telemetry item when an agent is skipped or partial.

### Fix 5: Provider Selection in Chat
- In `ConversationalAdvisoryPage.jsx`:
  - Check available active credentials via `credentialService.listCredentials()`.
  - Pass the active provider mode (e.g. `groq`, `cerebras`, `gemini`) if enrolled, or provide a clean mode toggle rather than hardcoding deterministic.
- In `src/rythusaathi/api/routes/conversations.py`:
  - When `mode in {CEREBRAS, GEMINI, GROQ}`, look up the actual enrolled `credential_id` from `credential_service.list_status(identity, provider_id=...)` instead of generating a synthetic UUID.

---

## 9. Required Regression Tests

1. `test_crop_agent_executes_without_location`: Verify CropAgent retrieves ICAR rice bulletins and returns `AgentStatus.COMPLETED` when crop is Rice and location is None.
2. `test_nationwide_documents_apply_to_district_queries`: Verify `_geography_applies(None, "location:district:in-pb-ludhiana")` returns `True`.
3. `test_risk_alert_background_screening_without_location`: Verify RiskAlertAgent identifies background rice disease susceptibility without a location.
4. `test_telemetry_exposes_structured_skip_reasons`: Verify `AgentResultSchema.summary` contains non-null descriptive text for all skipped agents.
5. `test_conversations_with_enrolled_byok_provider`: Verify that when an active BYOK credential exists, conversation execution dispatches to the authenticated provider service.

---

*Report ready for review. No code modifications have been applied.*


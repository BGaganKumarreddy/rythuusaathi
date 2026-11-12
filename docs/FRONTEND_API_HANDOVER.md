# RythuSaathi — Frontend Developer API Handover Guide

**Version**: 0.1.0  
**Backend Framework**: FastAPI (Python 3.12+ / 3.14)  
**Interactive Docs**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) (Swagger UI) / [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)  
**OpenAPI Specification**: `http://127.0.0.1:8000/openapi.json`  
**Base URL**: `http://127.0.0.1:8000/api/v1`

---

## 1. Quickstart & Local Server Launch

### Prerequisites
- Python 3.12+ or 3.14
- `uv` installed (`pip install uv` or via PowerShell installer)

### Start the Backend Server
Run the following in the project root:
```powershell
uv run uvicorn rythusaathi.api.app:app --reload --host 127.0.0.1 --port 8000
```

The server starts at `http://127.0.0.1:8000`. Cross-Origin Resource Sharing (CORS) is enabled with `allow_origins=["*"]` and `allow_credentials=True`, allowing any React Vite dev server (e.g. `http://localhost:5173`) to call the API directly without proxy configurations.

---

## 2. Authentication & Session Management

RythuSaathi uses opaque, high-entropy server-side session tokens authenticated via Argon2id.

### Authentication Flow
1. **Register**: `POST /api/v1/auth/register` creates the user account and initial tenant.
2. **Login**: `POST /api/v1/auth/login` verifies password and returns `access_token` and `expires_at`.
3. **Authorized Requests**: Pass the token in all subsequent requests:
   ```http
   Authorization: Bearer <access_token>
   ```
4. **Inspect Identity**: `GET /api/v1/auth/me` checks session validity and role profile.
5. **Logout**: `POST /api/v1/auth/logout` invalidates the server session token immediately.

---

## 3. Complete Endpoints Reference

### Group A: System Health

#### `GET /api/v1/health`
Check if the API and agents are online.
- **Auth**: None
- **Response `200 OK`**:
```json
{
  "status": "healthy",
  "service": "rythusaathi",
  "agents": 7,
  "version": "0.1.0"
}
```

---

### Group B: Authentication

#### 1. Register User
`POST /api/v1/auth/register`
- **Auth**: None
- **Request Body**:
```json
{
  "username": "ramesh_farmer",
  "password": "StrongPassword123!",
  "tenant_id": "coop-adilabad",
  "tenant_name": "Adilabad Farmer Cooperative",
  "role": "tenant_admin"
}
```
*Note: If the `tenant_id` already exists, unauthenticated users cannot specify `role: "tenant_admin"` (returns `403 Forbidden`). They must use the default `role: "member"`.*
- **Response `201 Created`**:
```json
{
  "user_id": "u-4f8e2a1b9c",
  "username": "ramesh_farmer",
  "tenant_id": "coop-adilabad",
  "role": "tenant_admin",
  "message": "User account successfully registered"
}
```
- **Error Codes**:
  - `400 invalid_registration`: Password shorter than 12 chars or untrimmed username.
  - `403 privilege_escalation_denied`: Attempting to claim `tenant_admin` on an existing tenant.
  - `409 username_already_registered`: Username already exists.

#### 2. User Login
`POST /api/v1/auth/login`
- **Auth**: None
- **Request Body**:
```json
{
  "username": "ramesh_farmer",
  "password": "StrongPassword123!",
  "tenant_id": "coop-adilabad"
}
```
- **Response `200 OK`**:
```json
{
  "access_token": "E4f8b9...vK90L",
  "token_type": "bearer",
  "expires_at": "2026-11-13T08:30:00Z",
  "user_id": "u-4f8e2a1b9c",
  "tenant_id": "coop-adilabad",
  "roles": ["tenant_admin"]
}
```
- **Error Codes**:
  - `401 authentication_failed`: Invalid username, password, or tenant ID.

#### 3. Current User Profile
`GET /api/v1/auth/me`
- **Auth**: `Bearer <token>`
- **Response `200 OK`**:
```json
{
  "user_id": "u-4f8e2a1b9c",
  "tenant_id": "coop-adilabad",
  "roles": ["tenant_admin"],
  "expires_at": "2026-11-13T08:30:00Z",
  "issuer": "rythusaathi:local"
}
```

#### 4. Logout
`POST /api/v1/auth/logout`
- **Auth**: `Bearer <token>`
- **Response `200 OK`**:
```json
{
  "success": true,
  "message": "Session successfully terminated"
}
```

---

### Group C: BYOK Credentials Vault

RythuSaathi uses Bring-Your-Own-Key (BYOK) envelope encryption. Secrets are encrypted with AES-256-GCM and bound to the user's tenant. Raw keys are never stored in plaintext and **never** returned to the frontend.

#### 1. Store API Key
`POST /api/v1/credentials`
- **Auth**: `Bearer <token>`
- **Request Body**:
```json
{
  "provider_id": "cerebras",
  "secret": "csk-live-api-key-here"
}
```
Supported `provider_id` values: `cerebras`, `gemini`, `groq`, `data.gov.in`.
- **Response `201 Created`**:
```json
{
  "credential_id": "cred-7a91bf...",
  "provider_id": "cerebras",
  "state": "active",
  "generation": 1,
  "updated_at": "2026-11-12T15:00:00Z"
}
```

#### 2. List Enrolled Credentials
`GET /api/v1/credentials`
- **Auth**: `Bearer <token>`
- **Response `200 OK`**:
```json
{
  "items": [
    {
      "credential_id": "cred-7a91bf...",
      "provider_id": "cerebras",
      "state": "active",
      "generation": 1,
      "updated_at": "2026-11-12T15:00:00Z"
    }
  ],
  "total": 1
}
```

#### 3. Delete Credential
`DELETE /api/v1/credentials/{credential_id}`
- **Auth**: `Bearer <token>`
- **Response `200 OK`**:
```json
{
  "success": true,
  "message": "Credential 'cred-7a91bf...' successfully deleted"
}
```

---

### Group D: Advisory Consultations

The core advisory workflow executes seven specialized agents using Microsoft Agent Framework:
1. **Intake Agent**: Query normalization, intent tagging, crop/location resolution.
2. **Weather Agent**: Open-Meteo observations, soil moisture, precipitation.
3. **Crop Knowledge Agent**: Curated ICAR advisories and growth stage guidelines.
4. **Market Agent**: Agmarknet APMC mandi prices and modal trends.
5. **Scheme Agent**: Central schemes (PM-KISAN, PMFBY, Rythu Bharosa).
6. **Risk Alert Agent**: Active, evidence-grounded risk detection.
7. **Coordinator Agent**: Multi-perspective synthesis, uncertainty, and next steps.

#### 1. Submit Advisory Inquiry
`POST /api/v1/advisories`
- **Auth**: `Bearer <token>`
- **Request Body**:
```json
{
  "query": "My cotton crop is flowering. What pest risks should I watch for and what are current mandi prices?",
  "crop": "cotton",
  "location": "Adilabad",
  "growth_stage": "flowering",
  "variety": "Bt Cotton",
  "mode": "deterministic",
  "credential_id": null
}
```
*Note on `mode`: Use `"deterministic"` for instant rule-based synthesis without LLM credentials. For live LLM synthesis, pass `"cerebras"`, `"gemini"`, or `"groq"` with an active enrolled `credential_id` (or omit `credential_id` to use your active default).*

- **Response `201 Created`**:
```json
{
  "advisory_id": "api:a1b2c3d4e5f6",
  "status": "COMPLETED",
  "execution_mode": "deterministic",
  "provider_id": "deterministic",
  "model_id": "rule-based-baseline",
  "coordinator_synthesis": "### Executive Summary\nFor flowering cotton in Adilabad...\n\n### Suggested next steps:\n- Monitor weekly for bollworm flare-up.\n- Review APMC Adilabad prices before marketing.",
  "agent_results": [
    {
      "agent_id": "Intake",
      "status": "COMPLETED",
      "duration_ms": 12.4,
      "summary": "Resolved cotton (flowering) in Adilabad",
      "error": null,
      "provider_id": "deterministic",
      "model_id": null
    },
    {
      "agent_id": "Weather",
      "status": "COMPLETED",
      "duration_ms": 234.1,
      "summary": "Open-Meteo observations retrieved",
      "error": null,
      "provider_id": "open-meteo",
      "model_id": null
    },
    {
      "agent_id": "Risk Alert",
      "status": "COMPLETED",
      "duration_ms": 45.2,
      "summary": "Identified 1 active weather risk",
      "error": null,
      "provider_id": "deterministic",
      "model_id": null
    },
    {
      "agent_id": "Coordinator",
      "status": "COMPLETED",
      "duration_ms": 80.5,
      "summary": "Full seven-agent synthesis completed",
      "error": null,
      "provider_id": "deterministic",
      "model_id": null
    }
  ],
  "active_risk_alerts": [
    {
      "finding_id": "finding:weather:excess_rain",
      "category": "weather_risk",
      "title": "Excess Rainfall Warning",
      "severity": "HIGH",
      "explanation": "Predicted rainfall > 50mm within 48h during flowering stage.",
      "safe_action": "Ensure adequate field drainage; postpone spray applications.",
      "evidence_ids": ["ev:open-meteo:hourly"],
      "is_active": true,
      "confidence": 0.92,
      "crop_id": "crop:cotton",
      "location_id": "loc:adilabad"
    }
  ],
  "background_vulnerabilities": [
    {
      "finding_id": "finding:crop:bollworm_susceptibility",
      "category": "pest_risk",
      "title": "General Bollworm Susceptibility",
      "severity": "LOW",
      "explanation": "Cotton crops are agronomically susceptible to bollworm during flowering.",
      "safe_action": "Set up pheromone traps for early population scouting.",
      "evidence_ids": ["ev:icar:cotton"],
      "is_active": false,
      "confidence": 0.5,
      "crop_id": "crop:cotton",
      "location_id": "loc:adilabad"
    }
  ],
  "weather_evidence": [
    {
      "evidence_id": "ev:open-meteo:current",
      "source_id": "open-meteo",
      "title": "Open-Meteo Weather Observation",
      "reference_url": "https://open-meteo.com",
      "source_type": "external_data",
      "excerpt": "Temperature: 28.5C, Relative humidity: 72%",
      "provenance": "Open-Meteo API",
      "mode": "live",
      "units": "celsius, percent",
      "crop_applicability": [],
      "geographic_applicability": ["loc:adilabad"],
      "retrieved_at": "2026-11-12T15:00:00Z"
    }
  ],
  "market_evidence": [],
  "scheme_evidence": [],
  "all_evidence": [],
  "uncertainty_and_limitations": [
    "Adilabad APMC market data was unavailable for today's trade date."
  ],
  "suggested_next_steps": [
    "Monitor weekly for bollworm flare-up.",
    "Review APMC Adilabad prices before marketing."
  ],
  "created_at": "2026-11-12T15:00:00Z"
}
```

#### 2. Get Past Advisory by ID
`GET /api/v1/advisories/{advisory_id}`
- **Auth**: `Bearer <token>`
- **Response**: Same full schema as `AdvisoryResponseSchema`. Cross-tenant / cross-user requests return `404 Not Found`.

#### 3. Get Diagnostic Trace by ID
`GET /api/v1/advisories/{advisory_id}/trace`
- **Auth**: `Bearer <token>`
- **Response `200 OK`**:
```json
{
  "advisory_id": "api:a1b2c3d4e5f6",
  "status": "COMPLETED",
  "findings": [
    {
      "finding_id": "finding:weather:obs",
      "agent_id": "Weather",
      "topic": "rainfall",
      "claim": "Excess rain forecast for Adilabad",
      "evidence_ids": ["ev:open-meteo:hourly"],
      "uncertainty": "Medium variance in forecast model"
    }
  ],
  "conflicts": [],
  "missing_information": [],
  "agent_executions": [
    {
      "agent_id": "Weather",
      "status": "COMPLETED",
      "duration_ms": 234.1,
      "evidence_ids": ["ev:open-meteo:hourly"],
      "finding_ids": ["finding:weather:obs"],
      "provider_id": "open-meteo",
      "model_id": null
    }
  ],
  "board_state": "SYNTHESIZED"
}
```

---

### Group E: History & Persistence

#### `GET /api/v1/history`
Retrieve paginated consultation history for the authenticated user and tenant.
- **Auth**: `Bearer <token>`
- **Query Parameters**:
  - `limit` (int, default: 20, min: 1, max: 100): Page size.
  - `offset` (int, default: 0): Items to skip.
  - `crop` (str, optional): Filter by crop identifier or common name (e.g. `cotton`, `rice`).
- **Response `200 OK`**:
```json
{
  "items": [
    {
      "advisory_id": "api:a1b2c3d4e5f6",
      "created_at": "2026-11-12T15:00:00Z",
      "query": "My cotton crop is flowering...",
      "crop": "cotton",
      "location": "Adilabad",
      "growth_stage": "flowering",
      "execution_mode": "deterministic",
      "status": "COMPLETED",
      "synthesis_preview": "### Executive Summary\nFor flowering cotton...",
      "active_alert_count": 1
    }
  ],
  "total": 1,
  "limit": 20,
  "offset": 0
}
```

---

### Group F: Configuration Catalogs

Pre-populate dropdowns, autocomplete inputs, and filters in the frontend UI using these static configuration catalogs:

#### 1. List Registered Crops
`GET /api/v1/configuration/crops`
- **Auth**: None
- **Response `200 OK`**:
```json
[
  {
    "crop_id": "crop:cotton",
    "common_names": ["Cotton", "Kapas", "Patti"],
    "scientific_name": "Gossypium hirsutum",
    "category": "fiber",
    "growth_stages": [
      {
        "stage_id": "flowering",
        "names": ["Flowering", "Square Formation"],
        "order": 3
      }
    ],
    "varieties": [
      {
        "variety_id": "bt-cotton",
        "names": ["Bt Cotton", "Bollgard II"]
      }
    ]
  }
]
```

#### 2. List Administrative Locations
`GET /api/v1/configuration/locations`
- **Auth**: None
- **Response `200 OK`**:
```json
[
  {
    "location_id": "loc:adilabad",
    "display_name": "Adilabad",
    "level": "district",
    "parent_id": "loc:telangana"
  }
]
```

#### 3. List Supported Providers
`GET /api/v1/configuration/providers`
- **Auth**: None
- **Response `200 OK`**:
```json
[
  {
    "provider_id": "deterministic",
    "name": "Deterministic Baseline",
    "kind": "inference",
    "requires_credential": false,
    "supported_models": [],
    "description": "Local deterministic fallback without external LLM calls."
  },
  {
    "provider_id": "cerebras",
    "name": "Cerebras Cloud Inference",
    "kind": "inference",
    "requires_credential": true,
    "supported_models": ["gpt-oss-120b", "llama-3.3-70b", "llama3.1-8b"],
    "description": "Ultra-fast wafer-scale engine inference with 30,000 TPM limit."
  },
  {
    "provider_id": "gemini",
    "name": "Google Gemini API",
    "kind": "inference",
    "requires_credential": true,
    "supported_models": ["gemini-2.5-flash", "gemini-2.5-pro"],
    "description": "Google DeepMind multimodal reasoning models via Google AI Studio."
  },
  {
    "provider_id": "groq",
    "name": "Groq Cloud LPU",
    "kind": "inference",
    "requires_credential": true,
    "supported_models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
    "description": "Groq LPU hardware inference engine with 7,200 TPM limits."
  }
]
```

---

## 4. UI Design & State Best Practices for React

### 1. Risk Alerts vs. Background Vulnerabilities
- Display `active_risk_alerts` prominently in an alert banner or card with appropriate severity tags (`CRITICAL`: Red, `HIGH`: Orange, `MEDIUM`: Yellow, `LOW`: Blue). These represent evidence-backed current hazards.
- Display `background_vulnerabilities` in a collapsible "General Agronomic Considerations" section. These represent long-term crop susceptibilities, NOT immediate threats.

### 2. Evidence Traceability
- Render chips or cards for `weather_evidence`, `market_evidence`, and `scheme_evidence`.
- Every evidence item includes `title`, `source_id`, `reference_url`, and `retrieved_at` for transparency.

### 3. Coordinator Status Indicators
- `COMPLETED`: Green badge. Full synthesis with evidence across all domains.
- `PARTIAL`: Amber badge. Some agent domains had no evidence (e.g. mandi closed or missing crop advisory), but the remaining evidence was synthesized honestly.
- `FAILED`: Red badge.

### 4. Suggested Next Steps
- The backend parses out bullet points under `suggested_next_steps` as an array of strings, ready to render directly into interactive checkboxes or action checklists.


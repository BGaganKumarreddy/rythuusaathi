# AgroSage — Intelligence for Every Acre.

[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.14-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-19-61DAFB.svg)](https://react.dev/)
[![Tailwind CSS](https://img.shields.io/badge/TailwindCSS-v4-38B2AC.svg)](https://tailwindcss.com/)

> **AgroSage** (formerly RythuSaathi) is an AI-powered agricultural intelligence platform built using Microsoft Agent Framework and React. It coordinates specialized AI agents to assist farmers with crop guidance, weather information, market insights, government schemes and agricultural risk assessment. By combining curated agricultural knowledge, retrieval-augmented generation (RAG), external data sources and conversational context, AgroSage aims to provide evidence-grounded farming assistance while communicating uncertainty and information gaps.

---

## 1. Problem Statement & Objective

Smallholder farmers frequently face fragmented, conflicting, and unverified agricultural advice across weather forecasts, pest outbreaks, APMC mandi trading, and welfare scheme eligibility. Generic commercial LLMs frequently hallucinate pesticide dosages, misquote government deadlines, or recommend inappropriate regional treatments.

**AgroSage** is designed to address these challenges through a local-first, multi-agent architecture built on the **Microsoft Agent Framework** connected to a React 19 dashboard. It models agronomic claims as evidence-backed assertions, cross-examining multi-domain evidence on a shared agricultural blackboard before generating structured farmer advisories.

---

## 2. Core Features

- **Multi-Turn Conversational Assistant**: Dialogue interface with multi-turn context retention, explicit correction handling, and follow-up interaction.
- **Seven-Agent Orchestrated Graph**: Specialized domain agents execute collaboratively to evaluate agricultural inquiries across domains.
- **Evidence-Grounded RAG**: Curated ICAR (*Indian Council of Agricultural Research*) guidelines and central government scheme documentation.
- **Dual-Tier Risk Alerting**: Distinction between **active, evidence-backed hazards** (e.g. excess rainfall within 48h) and **background crop vulnerabilities** (e.g. general pest susceptibility).
- **BYOK Credential Vault**: Bring-Your-Own-Key model with AES-256-GCM envelope encryption backed by Windows Credential Manager.
- **Provider-Neutral LLM Engine**: Supports execution across **Cerebras**, **Google Gemini**, **Groq**, and an offline **Deterministic Baseline**. Cloud provider integrations require valid user-enrolled BYOK credentials and available provider services; live inference has not been universally verified across all environments.
- **Modern React Frontend**: Conversational chatbot UI and agricultural dashboard with agent execution telemetry visualization, suggested next steps, and evidence traceability.
- **FastAPI REST API**: Local-first FastAPI service with interactive OpenAPI docs, SQLite WAL persistence, and implemented tenant/user isolation checks.

---

## 3. Seven-Agent Architecture

```
                    ┌─────────────────────────┐
                    │      Farmer Inquiry     │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │      Intake Agent       │
                    └────────────┬────────────┘
                                 │
         ┌───────────────────────┼───────────────────────┐
         │                       │                       │
┌────────▼────────┐     ┌────────▼────────┐     ┌────────▼────────┐
│  Weather Agent  │     │   Crop Agent    │     │  Market Agent   │
│  (Open-Meteo)   │     │ (Curated ICAR)  │     │  (data.gov.in)  │
└────────┬────────┘     └────────┬────────┘     └────────┬────────┘
         │                       │                       │
         └───────────────────────┼───────────────────────┘
                                 │
         ┌───────────────────────┴───────────────────────┐
         │                                               │
┌────────▼────────┐                             ┌────────▼────────┐
│  Scheme Agent   │                             │ Risk Alert Agent│
│(PM-KISAN/PMFBY) │                             │(Active Hazards) │
└────────┬────────┘                             └────────┬────────┘
         │                                               │
         └───────────────────────┬───────────────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │    Coordinator Agent    │
                    │   (Synthesis & Board)   │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │   Structured Advisory   │
                    └─────────────────────────┘
```

| # | Agent Name | Primary Responsibility | Data Source |
|---|---|---|---|
| **1** | **Intake Agent** | Query normalization, entity extraction (crop, growth stage, district), and intent routing. | Domain Seed Catalogs |
| **2** | **Weather Agent** | Weather metrics, precipitation accumulation, soil moisture indicators, and short-term forecasts. | Open-Meteo REST API |
| **3** | **Crop Knowledge Agent** | Stage-specific agronomic advice, nutrient guidelines, and scientific pest management. | Curated ICAR Bulletins |
| **4** | **Market Agent** | Daily APMC mandi commodity prices, modal rates, and market trade trends. | data.gov.in Agmarknet |
| **5** | **Scheme Agent** | Subsidy eligibility, documentation requirements, and application cycles. | PM-KISAN, PMFBY, State Schemes |
| **6** | **Risk Alert Agent** | Evaluates active weather and price anomalies to flag potential hazards and mitigations. | Weather & Mandi Feeds |
| **7** | **Coordinator Agent** | Cross-domain synthesis, evidence provenance compilation, conflict detection, and next steps. | Shared Advisory Blackboard |

---

## 4. Full-Stack Technology Stack

### Backend
- **Core & Runtime**: Python 3.12+ / 3.14, Microsoft Agent Framework, Pydantic v2
- **API Framework**: FastAPI, Uvicorn, Starlette
- **Retrieval & RAG**: FastEmbed (`bge-small-en-v1.5`), Lexical Hash Embeddings, SQLite
- **Security & Cryptography**: Argon2id (`argon2-cffi`), AES-256-GCM (`cryptography`), Windows Credential Manager (`keyring`)
- **Storage**: SQLite 3 with Write-Ahead Logging (WAL) and foreign key enforcement
- **External Providers**: Cerebras Cloud SDK, Google GenAI SDK, Groq SDK, Open-Meteo, data.gov.in

### Frontend
- **Framework & Build**: React 19, Vite, JavaScript (JSX)
- **Styling & Design System**: Tailwind CSS v4, Lucide React icons
- **State & Routing**: React Context, React Router 7, Axios
- **Testing**: Vitest, React Testing Library, JSDOM

---

## 5. Prerequisites & Installation

### Prerequisites
- Windows 10/11 with PowerShell 5.1+ or PowerShell 7+
- Node.js 18+ (tested on Node v22.20.0, npm 11.6.4)
- Python 3.12+ (tested through Python 3.14)
- `uv` package manager (`pip install uv`)

### 1. Clone & Backend Setup
```powershell
git clone https://github.com/BGaganKumarreddy/agrosage.git
cd agrosage

# Sync Python virtual environment and dependencies
uv sync
```

### 2. Frontend Setup
```powershell
cd frontend
npm install
cd ..
```

---

## 6. Running the Application

### Step 1: Start the FastAPI Backend
In your first terminal:
```powershell
uv run python -m uvicorn rythusaathi.api.app:app --reload --host 127.0.0.1 --port 8000
```
- **API Base URL**: `http://127.0.0.1:8000/api/v1`
- **Swagger UI (Interactive Docs)**: `http://127.0.0.1:8000/docs`
- **ReDoc Documentation**: `http://127.0.0.1:8000/redoc`

### Step 2: Start the React Frontend
In your second terminal:
```powershell
cd frontend
npm run dev
```
- **Application URL**: `http://localhost:5173`

---

## 7. Configuration & BYOK Setup

AgroSage uses a Bring-Your-Own-Key (BYOK) architecture. User API keys are encrypted at rest using AES-256-GCM envelope encryption with a Key Encryption Key (KEK) managed by Windows Credential Manager. Cloud model integrations (Cerebras, Gemini, and Groq) require valid user-enrolled credentials and active upstream provider service availability, and live inference has not been universally verified.

You can enroll your provider keys directly in the web UI under **BYOK Vault** (`/credentials`) or via the interactive CLI:
```powershell
# 1. Register a local user and tenant
uv run python scripts/byok_cli.py register

# 2. Add your provider API key (e.g. Cerebras, Gemini, or Groq)
uv run python scripts/byok_cli.py add-cerebras
uv run python scripts/byok_cli.py add-groq
uv run python scripts/byok_cli.py add-gemini

# 3. List active encrypted credentials (raw keys are never displayed)
uv run python scripts/byok_cli.py list
```

---

## 8. Verification & Testing

Both backend and frontend maintain unit, integration, and security regression test suites:

```powershell
# 1. Run the Frontend Vitest Suite
cd frontend
npm run test
npm run build
cd ..

# 2. Run the Backend FastAPI API & Security Tests
uv run python -m pytest tests/test_api.py -v

# 3. Run the Full Backend Test Suite
uv run python -m pytest -q
```
*Verification and test results are documented in dated implementation reports under `docs/` and may change as the project evolves. Execute the test commands above to verify current pass rates on your environment.*

---

## 9. Project Structure

```text
agrosage/
├── data/                         # Curated ICAR bulletins and central scheme catalogs
│   ├── agricultural/             # ICAR advisories by crop and growth stage
│   └── schemes/                  # PM-KISAN, PMFBY, and state welfare definitions
├── docs/                         # Architecture, audit reports, and handover guides
│   ├── FRONTEND_API_HANDOVER.md  # Complete REST API specification for React frontend
│   ├── PHASE_7_SECURITY_AUDIT.md # Security audit and vulnerability verification report
│   ├── PHASE_8_FRONTEND_REPORT.md# Frontend implementation and verification report
│   └── PHASE_9_CONVERSATIONAL_AI_REPORT.md # Conversational AI & integration repair report
├── frontend/                     # Modern React 19 + Tailwind CSS Frontend
│   ├── src/
│   │   ├── components/           # UI design system, layout, and advisory panels
│   │   ├── context/              # AuthContext, ToastContext, ConfigContext
│   │   ├── pages/                # Dashboard, Advisory, History, Credentials, Settings
│   │   ├── services/             # Axios API clients for all backend endpoints
│   │   └── test/                 # Vitest and React Testing Library test suites
│   ├── package.json
│   └── vite.config.js
├── scripts/                      # Operational and evaluation command-line scripts
│   ├── advisory_cli.py           # CLI interface for the seven-agent advisory workflow
│   └── byok_cli.py               # BYOK encrypted credential enrollment CLI
├── src/rythusaathi/              # Core backend package (internal namespace)
│   ├── agents/                   # Seven agents and Microsoft Agent Framework workflow
│   ├── api/                      # FastAPI REST API implementation
│   ├── domain/                   # Crop/location registries, advisory board, and risk rules
│   ├── inference/                # Provider-neutral LLM boundary (Cerebras, Gemini, Groq)
│   ├── knowledge/                # Document processor and SQLite vector/hash embeddings
│   ├── credentials.py            # AES-256-GCM envelope encryption & Windows KEK store
│   ├── local_auth.py             # Argon2id password authentication & hashed sessions
│   └── security.py               # Identity, role, and capability policies
└── tests/                        # Unit, integration, and security test suites
```

---

## 10. Security & Privacy

1. **Secret Redaction**: Raw API keys and passwords are designed not to be persisted in diagnostic traces, returned in HTTP responses, stored in frontend storage, or written to application logs.
2. **Fail-Closed KEK Protection**: Persistent credential storage relies on Windows Credential Manager (`WindowsCredentialStore`). Ephemeral in-memory KEK fallback is disabled by default to prevent silent credential loss upon restart.
3. **Argon2id & Token Hashing**: User passwords are hashed using memory-hard Argon2id parameters. Session tokens are 256-bit URL-safe strings whose SHA-256 hashes only are stored in SQLite.
4. **Tenant & User Isolation**: Owner and tenant isolation checks (`tenant_id` + `user_id`) are implemented in relevant API paths for credentials, advisory lookups, and diagnostic traces, while comprehensive API security verification remains pending.

---

## 11. Known Limitations

- **Operating System KEK**: The persistent KEK adapter currently targets Windows Credential Manager (`win32`). Linux deployments currently require an explicit test environment flag or an OS Keyutils/SecretService adapter.
- **Tenant Membership**: Standard `member` registration into existing tenants is open by tenant ID; signed invite tokens and administrator approvals are planned for enterprise multi-tenancy.

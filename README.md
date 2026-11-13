# RythuSaathi — Your Intelligent Farming Companion

[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.14-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-19-61DAFB.svg)](https://react.dev/)
[![Tailwind CSS](https://img.shields.io/badge/TailwindCSS-v4-38B2AC.svg)](https://tailwindcss.com/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/backend%20tests-512%20passed-brightgreen.svg)]()
[![Frontend Tests](https://img.shields.io/badge/frontend%20tests-24%20passed-brightgreen.svg)]()

> **RythuSaathi** (*Farmer's Companion*) is an academic, production-grade agricultural multi-agent AI system with a modern React SaaS frontend. It delivers verified, multi-domain agronomic intelligence—integrating real-time weather observations, localized crop protection guidelines, official mandi commodity prices, and government welfare schemes—grounded in scientific research to eliminate model hallucinations.

---

## 1. Problem Statement & Objective

Smallholder farmers frequently face fragmented, conflicting, and unverified agricultural advice across weather forecasts, pest outbreaks, APMC mandi trading, and welfare scheme eligibility. Generic commercial LLMs frequently hallucinate pesticide dosages, misquote government deadlines, or recommend inappropriate regional treatments.

**RythuSaathi** solves this through a local-first, multi-agent architecture built on the **Microsoft Agent Framework** connected to a modern React 19 dashboard. It treats every agronomic claim as an evidence-backed assertion, cross-examining multi-domain evidence on a shared agricultural blackboard before generating actionable farmer advisories.

---

## 2. Core Features

- **Multi-Turn Conversational Assistant**: Natural dialogue with persistent multi-turn context retention, explicit correction handling, and interactive follow-up questions.
- **Seven-Agent Orchestrated Graph**: Specialized domain agents execute collaboratively to validate agricultural inquiries.
- **Evidence-Grounded RAG**: Curated ICAR (*Indian Council of Agricultural Research*) scientific guidelines and central government scheme documentation.
- **Dual-Tier Risk Alerting**: Clear distinction between **active, evidence-backed hazards** (e.g. excess rainfall within 48h) and **background crop vulnerabilities** (e.g. general pest susceptibility).
- **Zero-Trust BYOK Security**: Bring-Your-Own-Key model with AES-256-GCM envelope encryption backed by Windows Credential Manager.
- **Provider-Neutral LLM Engine**: Seamless execution across **Cerebras** (wafer-scale engine, 30k TPM), **Google Gemini**, **Groq** (LPU), and an offline **Deterministic Baseline**.
- **Modern React SaaS Frontend**: Conversational chatbot UI and agricultural dashboard with real-time agent execution visualization, interactive next steps, and evidence traceability.
- **Production REST API**: Local-first FastAPI service with interactive OpenAPI docs, SQLite WAL persistence, and strict tenant/user isolation.

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
| **2** | **Weather Agent** | Real-time weather observations, soil moisture, precipitation accumulation, and 3-day forecast. | Open-Meteo REST API |
| **3** | **Crop Knowledge Agent** | Stage-specific agronomic advice, nutrient guidelines, and scientific pest management. | Curated ICAR Bulletins |
| **4** | **Market Agent** | Daily APMC mandi commodity prices, modal rates, and market trade trends. | data.gov.in Agmarknet |
| **5** | **Scheme Agent** | Subsidy eligibility, documentation requirements, and application cycles. | PM-KISAN, PMFBY, State Schemes |
| **6** | **Risk Alert Agent** | Detects active, immediate hazards (severe weather, pest threats) with actionable mitigations. | Real-time Weather & Mandi |
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
git clone https://github.com/BGaganKumarreddy/rythuusaathi.git
cd rythusaathi

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
uv run uvicorn rythusaathi.api.app:app --reload --host 127.0.0.1 --port 8000
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

RythuSaathi uses a Bring-Your-Own-Key (BYOK) architecture. User API keys are encrypted at rest using AES-256-GCM envelope encryption with a Key Encryption Key (KEK) managed by Windows Credential Manager.

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

Both backend and frontend maintain comprehensive unit, integration, and security regression test suites:

```powershell
# 1. Run the Frontend Vitest Suite (24 tests)
cd frontend
npm run test
npm run build
cd ..

# 2. Run the Backend FastAPI API & Security Tests (27 tests)
uv run python -m pytest tests/test_api.py -v

# 3. Run the Full Backend Test Suite (512 tests)
uv run python -m pytest -q
```
*Current test status: **512 backend tests passing**, **24 frontend tests passing**, 0 warnings.*

---

## 9. Project Structure

```text
rythusaathi/
├── data/                         # Curated ICAR bulletins and central scheme catalogs
│   ├── agricultural/             # ICAR advisories by crop and growth stage
│   └── schemes/                  # PM-KISAN, PMFBY, and state welfare definitions
├── docs/                         # Architecture, audit reports, and handover guides
│   ├── FRONTEND_API_HANDOVER.md  # Complete REST API specification for React frontend
│   ├── PHASE_7_SECURITY_AUDIT.md # Security audit and vulnerability verification report
│   ├── PHASE_8_FRONTEND_REPORT.md# Frontend implementation and verification report
│   └── PHASE_9_CONVERSATIONAL_AI_REPORT.md # Conversational AI & integration repair report
├── frontend/                     # Modern React 19 + Tailwind CSS SaaS Frontend
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
├── src/rythusaathi/              # Core production backend package
│   ├── agents/                   # Seven agents and Microsoft Agent Framework workflow
│   ├── api/                      # Production FastAPI REST API
│   ├── domain/                   # Crop/location registries, advisory board, and risk rules
│   ├── inference/                # Provider-neutral LLM boundary (Cerebras, Gemini, Groq)
│   ├── knowledge/                # Document processor and SQLite vector/hash embeddings
│   ├── credentials.py            # AES-256-GCM envelope encryption & Windows KEK store
│   ├── local_auth.py             # Argon2id password authentication & hashed sessions
│   └── security.py               # Verified identity, roles, and capability policies
└── tests/                        # Comprehensive unit, integration, and security test suites
```

---

## 10. Security & Privacy

1. **Zero Secret Leakage**: Raw API keys and passwords are never persisted in traces, returned in HTTP responses, stored in frontend storage, or logged.
2. **Fail-Closed KEK Protection**: Production credential storage relies on Windows Credential Manager (`WindowsCredentialStore`). Ephemeral in-memory KEK fallback is disabled by default to prevent silent credential loss upon restart.
3. **Argon2id & Token Hashing**: User passwords are encrypted with memory-hard Argon2id parameters. Session tokens are 256-bit URL-safe strings whose SHA-256 hashes only are stored in SQLite.
4. **Tenant & User Isolation**: All credential queries, advisory lookups, and diagnostic traces strictly enforce composite owner checks (`tenant_id` + `user_id`).

---

## 11. Known Limitations

- **Operating System KEK**: The persistent KEK adapter currently targets Windows Credential Manager (`win32`). Linux deployments currently require an explicit test environment flag or an OS Keyutils/SecretService adapter.
- **Tenant Membership**: Standard `member` registration into existing tenants is open by tenant ID; signed invite tokens and administrator approvals are planned for enterprise multi-tenancy.

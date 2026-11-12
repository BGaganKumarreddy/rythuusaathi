# RythuSaathi — Your Intelligent Farming Companion

[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.14-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-489%20passed-brightgreen.svg)]()

> **RythuSaathi** (*Farmer's Companion*) is an academic, production-grade agricultural multi-agent AI system. It provides verified, multi-domain agronomic intelligence—integrating real-time weather observations, localized crop protection guidelines, official mandi commodity prices, and government welfare schemes—grounded in scientific research to eliminate model hallucinations.

---

## 1. Problem Statement & Objective

Smallholder farmers frequently face fragmented, conflicting, and unverified agricultural advice across weather forecasts, pest outbreaks, APMC mandi trading, and welfare scheme eligibility. Generic commercial LLMs frequently hallucinate pesticide dosages, misquote government deadlines, or recommend inappropriate regional treatments.

**RythuSaathi** solves this through a local-first, multi-agent architecture built on the **Microsoft Agent Framework**. It treats every agronomic claim as an evidence-backed assertion, cross-examining multi-domain evidence on a shared agricultural blackboard before generating actionable farmer advisories.

---

## 2. Core Features

- **Seven-Agent Orchestrated Graph**: Specialized domain agents execute collaboratively to validate agricultural inquiries.
- **Evidence-Grounded RAG**: Curated ICAR (*Indian Council of Agricultural Research*) scientific guidelines and central government scheme documentation.
- **Dual-Tier Risk Alerting**: Clear distinction between **active, evidence-backed hazards** (e.g. excess rainfall within 48h) and **background crop vulnerabilities** (e.g. general pest susceptibility).
- **Zero-Trust BYOK Security**: Bring-Your-Own-Key model with AES-256-GCM envelope encryption backed by Windows Credential Manager.
- **Provider-Neutral LLM Engine**: Seamless execution across **Cerebras** (wafer-scale engine, 30k TPM), **Google Gemini**, **Groq** (LPU), and an offline **Deterministic Baseline**.
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

## 4. Technology Stack

- **Core & Runtime**: Python 3.12+ / 3.14, Microsoft Agent Framework, Pydantic v2
- **API Framework**: FastAPI, Uvicorn, Starlette
- **Retrieval & RAG**: FastEmbed (`bge-small-en-v1.5`), Lexical Hash Embeddings, SQLite
- **Security & Cryptography**: Argon2id (`argon2-cffi`), AES-256-GCM (`cryptography`), Windows Credential Manager (`keyring`)
- **Storage**: SQLite 3 with Write-Ahead Logging (WAL) and foreign key enforcement
- **External Providers**: Cerebras Cloud SDK, Google GenAI SDK, Groq SDK, Open-Meteo, data.gov.in

---

## 5. Prerequisites & Installation

### Prerequisites
- Windows 10/11 with PowerShell 5.1+ or PowerShell 7+
- Python 3.12+ (tested through Python 3.14)
- `uv` package manager (`pip install uv` or official standalone installer)

### Quick Installation
```powershell
# Clone the repository
git clone https://github.com/bgagankumarreddy/rythusaathi.git
cd rythusaathi

# Sync virtual environment and install all dependencies
uv sync
```

---

## 6. Configuration & BYOK Setup

RythuSaathi uses a Bring-Your-Own-Key (BYOK) architecture. User API keys are encrypted at rest using AES-256-GCM envelope encryption with a Key Encryption Key (KEK) managed by Windows Credential Manager.

### Option A: Setup via Interactive CLI
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

### Option B: Setup via REST API
Credentials can also be enrolled programmatically via `POST /api/v1/credentials` once authenticated.

---

## 7. Running the Application

### 1. Launch the FastAPI Server
```powershell
uv run uvicorn rythusaathi.api.app:app --reload --host 127.0.0.1 --port 8000
```
- **Base URL**: `http://127.0.0.1:8000/api/v1`
- **Swagger UI (Interactive Docs)**: `http://127.0.0.1:8000/docs`
- **ReDoc Documentation**: `http://127.0.0.1:8000/redoc`
- **OpenAPI JSON**: `http://127.0.0.1:8000/openapi.json`

### 2. Run the Command-Line Interface (CLI)
```powershell
# Run deterministic offline baseline advisory
uv run python scripts/advisory_cli.py "My cotton crop is flowering in Adilabad. What pest risks should I watch for and what are mandi prices?"

# Run with an external LLM provider using stored BYOK credentials
uv run python scripts/advisory_cli.py --mode cerebras "My cotton crop is flowering in Adilabad."
```

---

## 8. Verification & Testing

The repository maintains rigorous unit, integration, and security regression test suites:

```powershell
# Run the FastAPI REST API and security test suite
uv run python -m pytest tests/test_api.py -v

# Run the complete repository test suite
uv run python -m pytest -q
```
*Current test status: **489 tests passing**, 1 skipped, 91 subtests passed, 0 warnings.*

---

## 9. Project Structure

```text
rythusaathi/
├── data/                         # Curated ICAR bulletins and central scheme catalogs
│   ├── agricultural/             # ICAR advisories by crop and growth stage
│   └── schemes/                  # PM-KISAN, PMFBY, and state welfare definitions
├── docs/                         # Architecture, audit reports, and handover guides
│   ├── FRONTEND_API_HANDOVER.md  # Complete REST API specification for React frontend
│   └── PHASE_7_SECURITY_AUDIT.md # Security audit and vulnerability verification report
├── scripts/                      # Operational and evaluation command-line scripts
│   ├── advisory_cli.py           # CLI interface for the seven-agent advisory workflow
│   └── byok_cli.py               # BYOK encrypted credential enrollment CLI
├── src/rythusaathi/              # Core production package
│   ├── agents/                   # Seven agents and Microsoft Agent Framework workflow
│   │   ├── workflow.py           # Seven-agent workflow graph & coordinator synthesis
│   │   └── contracts.py          # Typed evidence, findings, and board state contracts
│   ├── api/                      # Production FastAPI REST API
│   │   ├── routes/               # Modular routes: auth, credentials, advisories, history, config
│   │   ├── schemas/              # Pydantic v2 request and response contracts
│   │   ├── dependencies.py       # Fail-closed auth and KEK dependency injection
│   │   ├── history.py            # SQLite WAL advisory consultation persistence
│   │   └── exceptions.py         # Diagnostic-safe sanitized exception handlers
│   ├── domain/                   # Crop/location registries, advisory board, and risk rules
│   │   ├── board.py              # Agricultural advisory blackboard and claim conflict detection
│   │   └── risks.py              # Active risk detection vs background vulnerability logic
│   ├── inference/                # Provider-neutral LLM boundary (Cerebras, Gemini, Groq)
│   ├── knowledge/                # Document processor and SQLite vector/hash embeddings
│   ├── credentials.py            # AES-256-GCM envelope encryption & Windows KEK store
│   ├── local_auth.py             # Argon2id password authentication & hashed sessions
│   └── security.py               # Verified identity, roles, and capability policies
└── tests/                        # Comprehensive unit, integration, and security test suites
```

---

## 10. Security & Privacy

1. **Local-First Zero Leakage**: All farmer consultations, diagnostic traces, and identity records reside locally in SQLite databases (`PRAGMA journal_mode = WAL`).
2. **Fail-Closed KEK Protection**: Production credential storage relies on Windows Credential Manager (`WindowsCredentialStore`). Ephemeral in-memory KEK fallback is disabled by default to prevent silent credential loss upon restart.
3. **Argon2id & Token Hashing**: User passwords are encrypted with memory-hard Argon2id parameters. Session tokens are 256-bit URL-safe strings whose SHA-256 hashes only are stored in SQLite.
4. **Tenant & User Isolation**: All credential queries, advisory lookups, and diagnostic traces strictly enforce composite owner checks (`tenant_id` + `user_id`).
5. **No Secret In Traces**: Raw API keys, passwords, and decrypted provider secrets are explicitly excluded from API responses, serialized traces, and logs.

---

## 11. Known Limitations & Frontend Status

- **Operating System KEK**: The persistent KEK adapter currently targets Windows Credential Manager (`win32`). Linux deployments currently require an explicit test environment flag or an HSM/Keyutils adapter.
- **Tenant Membership**: Standard `member` registration into existing tenants is open by tenant ID; signed invite tokens and administrator approvals are planned for enterprise multi-tenancy.
- **Frontend Status**: The React + Vite + Tailwind frontend is developed independently in a separate workspace using the endpoints documented in [`docs/FRONTEND_API_HANDOVER.md`](docs/FRONTEND_API_HANDOVER.md).

# RythuSaathi Phase 2 Execution Record

**Status: IMPLEMENTED; ready for Phase 2 acceptance.** This bounded
Phase 2 update adds shared agricultural context, registries, exact context
resolution, an in-process advisory board, evidence contracts, and structured
recommendation contracts. Phase 0 and Phase 1 implementation files were left
unchanged. Phase 3 and Phase 4 have not started.

## Task results

| Task | Status | Result |
|---|---|---|
| 2.1 Request context | PASS | Immutable typed context represents identity and tenant IDs, crop/location, variety, growth stage, sowing date, irrigation, soil, farmer report, language, environmental observations, missing information, and workflow metadata. `ContextFact` distinguishes supplied, unknown, and unavailable values. |
| 2.2 Crop registry | PASS | Versioned extensible registry supports stable IDs, common/regional/scientific names, categories, varieties, growth stages, source references, validity windows, search, and exact lookup. Seed data is explicitly partial. |
| 2.3 Location registry | PASS | Versioned hierarchy supports country/state/district/subdistrict/village levels, aliases, optional coordinate pairs, internal IDs, and timezone labels. Parent filtering and ambiguity are explicit; no GPS point is required. |
| 2.4 Advisory board | PASS | Request-scoped, thread-safe in-process board appends typed evidence, findings, recommendations, and allow-listed trace events. Agent attribution and section ownership are assigned by the writer; conflicts and lifecycle transitions are explicit. |
| 2.5 Evidence contracts | PASS | Evidence identifies source type, source, title/reference, excerpt or content reference, retrieval/publication time, scope, provenance, mode, status, quality, confidence, and relevance. Mode and status are independent dimensions. |
| 2.6 Recommendation contracts | PASS | Recommendations include action/category, evidence references, reasoning, confidence, uncertainty, missing information, applicability, risk, and follow-up. Evidence-backed, hypothesis, and needs-information support levels have separate validation rules. |
| 2.7 Integration and tests | PASS | 26 focused Phase 2 tests, the full Phase 1 + Phase 2 suite, the isolated Phase 0 proof tests, and syntax compilation pass. The contracts do not instantiate production agents or a workflow graph. |

## Implementation files

- `src/rythusaathi/domain/models.py` — request, facts, and partial-information models.
- `src/rythusaathi/domain/registry.py` — versioned crop, location, and coverage registries.
- `src/rythusaathi/domain/seed.py` — small source-attributed starter catalog.
- `src/rythusaathi/domain/resolution.py` — exact selection resolution and canonical context creation.
- `src/rythusaathi/domain/board.py` — request-scoped board, agent write sections, conflict detection, lifecycle, and safe trace events.
- `src/rythusaathi/domain/evidence.py` — evidence/source/provenance contracts.
- `src/rythusaathi/domain/recommendations.py` — evidence-qualified recommendation contract.
- `src/rythusaathi/domain/__init__.py` — package exports.
- `tests/test_domain_context.py`, `tests/test_domain_registry.py`, `tests/test_domain_board.py`, and `tests/test_domain_evidence_recommendations.py` — focused Phase 2 coverage.

## Seed catalog and provenance

The seed is illustrative and not an exhaustive crop or geographic database.
Crop taxonomy references use the Royal Botanic Gardens, Kew Plants of the World
Online: [rice](https://powo.science.kew.org/taxon/urn:lsid:ipni.org:names:316812-2),
[bread wheat](https://powo.science.kew.org/taxon/332110-2), and
[cotton](https://powo.science.kew.org/taxon/urn:lsid:ipni.org:names:30042843-2).
Seeded variety references include ICAR material for [Pusa Basmati 1121](https://icar.org.in/en/crop-science/basmati-rice-varieties),
[HD 3226](https://icar.org.in/en/node/12081), and the 2025 [ICAR Kharif advisory](https://www.icar.gov.in/sites/default/files/Circulars/ICAR-En-Kharif-Agro-Advisories-for-Farmers-2025.pdf).
The Telugu rice name and growth-stage references use the [ICAR multilingual advisory](https://www.icar.gov.in/sites/default/files/Circulars/ICAR%20Kharif%20Agro-Advisories%20for%20Farmers%202025__multi%20language%2B%2BNew.pdf)
and [ICAR rice-stage research](https://epubs.icar.org.in/index.php/JISAS/article/view/171327).

Location examples are attributed to the [Government of India state portal](https://knowindia.india.gov.in/states-uts/),
[Telangana district directory](https://www.telangana.gov.in/about/districts/),
[Guntur district](https://guntur.ap.gov.in/),
[Mysuru district](https://mysore.nic.in/en/),
[Ludhiana district](https://ludhiana.nic.in/en/), and
[Nalgonda district](https://nalgonda.telangana.gov.in/).
The internal `location:*` IDs are RythuSaathi catalog keys; they are not official
government geographic codes. Catalog presence alone is not a recommendation or
an assertion that an advisory applies to a location.

## Verification

Focused Phase 2 tests:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_domain_*.py' -v
```

Result: **26 passed**. The complete regression and syntax compilation results
were:

- `.venv\Scripts\python.exe -m unittest discover -s tests -v` — **86 passed**.
- `.venv\Scripts\python.exe -m unittest discover -s tmp\phase0_feasibility -v` — **14 passed, 1 skipped**. The live Groq smoke test was skipped with `GROQ_API_KEY` removed from the test process; no provider request was made.
- `.venv\Scripts\python.exe -m compileall -q src tests tmp\phase0_feasibility` — **passed**.

The 86-test suite includes the actual Windows Credential Manager persistence
test, existing Groq provider tests, authenticated credential integration, and
Phase 1 recovery/security tests. No Phase 0 or Phase 1 source file was modified.

## Limits and handoff boundary

- Crop seed data currently contains rice, wheat, and cotton, a small number of
  names/varieties/stages, and one Telugu regional crop name. The location seed
  contains India, four states, and four districts; it has no subdistrict or
  village entries. These catalogs are extensible and must not be treated as
  complete.
- No crop/location pair is enabled by default. A pair must be explicitly added
  to `CoverageRegistry`; an absent pair is reported as unsupported system
  coverage, not proof that a crop is absent from that place.
- Location normalization handles Unicode normalization, case, and whitespace.
  Timezone values are checked for an IANA-style name format, but the Windows
  development environment has no timezone database installed to verify that a
  zone exists. Seed timezone labels are based on the attributed location
  sources; runtime timezone conversion is not implemented here.
- The advisory board is in-memory and scoped to one process. Its lock protects
  concurrent threads in that process; it is not a durable or cross-process
  coordination service. Agent-specific writers must be injected by the future
  workflow orchestrator. The current domain package does not bind writers to
  authenticated principals.
- Board trace events contain only request/trace IDs, agent ID, action, item ID,
  state, and timestamp. Findings, evidence excerpts, and recommendations are
  typed but their text fields are not secret-scanned; callers must never put
  provider credentials or other secrets into those fields.
- Provenance is represented and validated for shape, but a source adapter must
  retrieve and verify external content. Evidence-backed recommendation
  references must exist and be available on the board; geographic/crop
  applicability and agricultural correctness still require agent or source
  policy checks.
- This phase adds no live weather, market, scheme, or crop retrieval; no RAG
  indexing; no production Intake/Weather/Crop/Market/Scheme/Coordinator agents;
  no application HTTP or CLI integration; and no production Microsoft Agent
  Framework graph. The Phase 0 graph proof remains isolated under
  `tmp/phase0_feasibility/`.

## Phase boundary

Phase 2 establishes shared typed contracts and an in-process collaboration
foundation only. Do not begin Phase 3 retrieval or Phase 4 production agent and
workflow work until Phase 2 acceptance. This record is ready for the project
owner's Phase 2 acceptance decision; no Phase 3 or Phase 4 implementation was
started in this update.

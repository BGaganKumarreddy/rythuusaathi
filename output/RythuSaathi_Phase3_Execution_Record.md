# RythuSaathi Phase 3 Execution Record

**Updated: 7 November 2026. Overall status: PASS for the bounded Phase 3
acceptance corpus. Phase 4 has not started.**

This is a continuation of the existing Phase 3 implementation. The SQLite
retrieval path, accepted datasets, and Phase 0–2 production contracts were
preserved. Work in this update addressed the two remaining source-coverage
gaps, pinned the already-verified embedding model snapshot, added focused tests,
and repeated semantic and full-regression verification.

## Final task assessment

| Task | Status | Acceptance evidence |
|---|---|---|
| 3.1 Source adapter interfaces | PASS | Typed source/document contracts, reliability classification, explicit local JSON adapters, retrieval error handling, and import-without-network behavior remain covered by tests. |
| 3.2 Curated agricultural knowledge | PASS | The bounded corpus has 15 documents across six registered crops and represents all ten agricultural topic categories. Five new PAU entries cover rice and cotton, with five explicit growth-stage descriptions. Seven records have explicit Punjab scope; entries without source-supported geography remain unscoped and fail closed under location filters. |
| 3.3 Official scheme ingestion | PASS | Added Telangana Rythu Bharosa Scheme 2025 from official G.O.Rt.No.18. Issuer, source URL, state jurisdiction, date/version, retrieval timestamp, eligibility, benefits, limitations, records and procedure are indexed. The GO does not state an individual application route or document checklist; these are explicitly recorded as unspecified. |
| 3.4 Document processing | PASS | pypdf 6.21.4 extracted all 208 pages of the PAU Kharif 2026 PDF (523,525 characters); real PDF page references were checked. The scanned Telangana GO returned no text from pypdf and was reviewed from rendered page images without claiming parser extraction. Existing real ICAR and central scheme PDF processing checks remain passed. |
| 3.5 Embedding generation | PASS | FastEmbed 0.9.1 / ONNX Runtime 1.31.1 performed actual CPU inference using the pinned 384-dimensional multilingual MiniLM snapshot `faf4aa4225822f3bc6376869cb1164e8e3feedd0`. Hash embeddings were used only for the separate lexical baseline and deterministic tests. |
| 3.6 Vector retrieval | PASS | SQLite persistence, reopen, model/dimension binding, exact filters, and idempotent ingestion passed. A 24-chunk index reopened in a separate process with the pinned revision; a second ingestion added zero chunks. |
| 3.7 Evidence attribution | PASS | New PAU and Telangana retrievals were mapped to Phase 2 evidence and checked for source URL/identity/publisher, excerpt, parent document, crop/geography, section, revision, hash, query retrieval timestamp, and scheme jurisdiction level. |
| 3.8 Integration and testing | PASS | Phase 3 focused tests: 25 passed. Full repository suite: 111 passed, 0 failed, 0 skipped in the unrestricted Windows execution environment. Python compileall passed. |

## Source coverage and data decisions

### Agricultural knowledge

The curated set contains 15 documents across six registered crop IDs:

| Crop | Documents |
|---|---:|
| Rice | 8 |
| Maize | 2 |
| Wheat | 1 |
| Cotton | 2 |
| Black gram | 1 |
| Chickpea | 1 |

The represented agricultural topics are all ten categories: crop cultivation,
sowing and planting, irrigation, fertilizer management, soil preparation,
growth stages, pest management, disease identification, nutrient deficiencies,
and harvesting/post-harvest. The five new entries add the previously missing
irrigation, soil preparation, growth-stage, disease, and harvest categories.

Five records have explicit stage metadata across rice and cotton: pre-sowing
field preparation; transplant establishment through pre-maturity; vegetative,
flowering and grain development; nursery and transplanted crop; and harvest
maturity and post-harvest storage. Seven of the 15 records explicitly apply to
Punjab: two previously scoped entries and the five PAU Kharif 2026 entries.
Eight older entries have no geography explicitly supported by their source and
remain unscoped. A mention of a crop or state was not treated as evidence of
jurisdiction. These eight items are excluded from strict location-filtered
retrieval. No nationwide coverage is claimed.

The five new agricultural summaries use the official Punjab Agricultural
University *Package of Practices for Crops of Punjab*, Kharif 2026, Volume 43,
March 2026: `https://pau.edu/content/ccil/pf/pp_kharif.pdf`. The package says
its recommendations are intended for Punjab farmers and extension workers.
The dataset records `March 2026` in the revision label and leaves
`publication_date` unset because the source supplies a month, not a day. It
does not generalize the package beyond its stated Punjab scope.

The added source sections and facts are:

- Rice irrigation and drainage, printed pages 11–12 (PDF pages 19–20): the
  first two weeks of standing water after transplanting, subsequent irrigation
  after ponded water infiltrates, a 10 cm depth limit, and stopping irrigation
  about a fortnight before maturity.
- Cotton production techniques (PDF page 5): incorporating wheat straw left
  after combine harvesting into the field before sowing with a rotavator or
  super seeder.
- Rice climatic requirements, printed page 2 (PDF page 10): the source's
  temperature and sunshine ranges for vegetative, flowering, and grain
  development stages. The summary labels these as PAU's Punjab conditions for
  expected high productivity, not universal thresholds or guarantees.
- Rice dwarfing disease, printed page 16 (PDF page 24): source-described
  symptoms of Southern rice black-streaked dwarf virus and preventive
  sanitation/inspection measures. The text labels these as inspection signs,
  not a field diagnosis, and requires current pesticide label and local
  extension advice for chemical action.
- Rice harvest and storage, printed page 12 (PDF page 20): harvest at near-ripe
  ears/yellow straw, shattering and milling-quality risk from delayed harvest,
  and the package's stated 17% marketing and 12% storage moisture figures.

The downloaded PAU PDF was 1,995,991 bytes, SHA-256
`30142d84b17afce725fe48bc5458b03bc029f3d57734e322bf5290a84f7215c0`; it was
retrieved on 6 November 2026 at 15:16:50.900 UTC. Its embedded PDF creation
metadata was not used as publication metadata.

### Government schemes

The local scheme dataset now has three official summaries: central PM-KISAN
(2020 historical guidelines), central PMFBY (2020 historical guidelines), and
state-level Telangana Rythu Bharosa Scheme 2025. The state source is G.O.Rt.No.
18 from the Agriculture and Cooperation (Agriculture-II) Department,
Government of Telangana. The official GOs/Circulars index lists “GO Rt No. 18_
Rythu Bharosa Scheme 2025” at
`https://rythubharosa.telangana.gov.in/gos_circulars.aspx`; the linked order is
`https://rythubharosa.telangana.gov.in/assets/files/GO%20Rt%20No.%2018_%20Rythu%20Bharosa%20Scheme%202025.pdf`.

The two-page order is dated 10 January 2025 and states implementation from
26 January 2025. The structured record carries:

- Issuer/publisher: Agriculture and Cooperation (Agriculture-II) Department,
  Government of Telangana.
- Jurisdiction: Telangana (`location:state:in-tg`), level `state`, official
  government source classification.
- Eligibility: extent of cultivable agricultural land recorded in the
  Bhoobharati (Dharani) portal; non-agricultural land is excluded; ROFR
  pattadars are expressly included.
- Benefit: Rs. 12,000 per acre per year. The order does not state a per-season
  installment amount or payment calendar.
- Procedure: DBT to farmers' bank accounts and implementation steps by
  Agriculture Department officials. The order does not describe a farmer-led
  application route, form/portal, office sequence, deadline, or document list.
- Limitations: no additional exclusions, acreage cap, appeal procedure, or
  unstated eligibility conditions are inferred from the two-page order.
- Publication/revision metadata: publication date `2025-01-10`; revision label
  `G.O.Rt.No. 18 dated 10 January 2025; Rythu Bharosa Scheme 2025`.
- Source retrieval metadata: `2026-11-06T15:16:52.179Z`.

The order PDF is an image scan. pypdf 6.21.4 found two pages and zero extracted
text; both pages were rendered with Poppler and visually inspected. Its
embedded scan creation date was not used as the Government Order date. The PDF
was 109,110 bytes, SHA-256
`9cce5a6933823b23b5c58446aabe17e309c29ee026a7a3ac6f7f9ba5faddc08b`. The
English content is a summary of the scanned Telugu order. Where the order
does not give an application route or document checklist, the corpus states
that absence directly; it does not infer process details from unrelated or
inconsistent district pages.

Scheme records remain local JSON data consumed through the existing ingestion
contracts. Retrieval is not a live source refresh, and historical central
guidelines still require the current official notification before use.

## Model pin and semantic evaluation

The default `FastEmbedProvider` now pins the model source
`Qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q` to immutable snapshot
commit `faf4aa4225822f3bc6376869cb1164e8e3feedd0`. `huggingface-hub` is declared
in the semantic optional dependencies. The provider first loads a complete
local snapshot and downloads the exact commit only when needed. The embedding
revision is stored in returned embeddings and the SQLite model binding. A
complete cache works offline; a clean machine needs network access for the
initial download.

The checked-in retrieval evaluation contains 16 cases: 12 supported
agricultural/scheme queries and four unsupported queries. Actual FastEmbed CPU
inference with the pinned commit returned the expected document in the top
three for 12/12 supported cases and no results for 4/4 unsupported cases.
Supported-query MRR was 1.000; overall MRR was 0.750 because unsupported cases
contribute zero. The separate hashing lexical baseline produced the same
Hit@3 and unsupported-empty counts, but it is not semantic evidence.

The actual FastEmbed pipeline ingested 15 agricultural and three scheme
documents into `tmp/phase3-acceptance.sqlite3`, producing 24 chunks. A separate
process reopened the database, verified the stored model revision, and
retrieved scoped Punjab rice and Telangana scheme evidence. A repeated
ingestion added zero chunks. Evidence retained the expected parent document,
source URL and identity, excerpt, scope, section, revision, chunk ID and
content hash.

## Regression and focused verification

- The project venv runs Python 3.14.4 and prints a warning that its original
  base-interpreter path is unavailable. The interpreter still ran inference,
  PDF parsing, compilation and tests successfully.
- Focused knowledge/Phase 3 suite: `python -m unittest discover -s tests -p
  test_knowledge.py -v` — 25 passed.
- Full repository suite: `python -m unittest discover -s tests -v` — 111
  passed, 0 failed, 0 skipped in 7.639 seconds, run through unrestricted
  Windows execution. Credential Manager lifecycle, Windows credential-store,
  async recovery and Groq integration tests all completed.
- The ordinary restricted sandbox run errored in OS credential lifecycle tests
  and stalled at a credential-backed async integration test; that run was
  stopped. The full suite passed outside that sandbox. No test was weakened or
  removed.
- Retrieval evaluation with the real pinned FastEmbed model: Hit@3 12/12,
  unsupported-empty 4/4, supported-query MRR 1.000.
- Lexical baseline evaluation: Hit@3 12/12 and unsupported-empty 4/4; this is
  reported separately from semantic inference.
- SQLite FastEmbed ingestion/reopen/idempotency and evidence provenance:
  passed, 24 chunks, pinned model revision recorded.
- Python compileall over `src` and `scripts`: passed.
- Source integrity: official PAU and Telangana source URLs were checked; source
  PDFs were signature checked and SHA-256 recorded above. The official
  Telangana listing identifies the linked state Government Order.

## Files changed in this continuation

- `data/agricultural/icar_advisories.json` — five PAU Kharif 2026 regional
  summaries with Punjab scope, exact source sections, topics, stage metadata,
  and month-precision revision labels.
- `data/agricultural/retrieval_eval.json` — five supported PAU cases and one
  supported Telangana scheme case; existing unsupported cases retained.
- `data/schemes/central_agricultural_schemes.json` — the official Telangana
  state order with structured source/eligibility/benefit/limitation/procedure
  metadata; the two central records preserved.
- `src/rythusaathi/knowledge/embeddings.py` and `pyproject.toml` — immutable
  FastEmbed snapshot resolution and explicit Hugging Face Hub optional
  dependency.
- `scripts/evaluate_retrieval.py` — configurable model cache path.
- `scripts/ingest_knowledge.py` — prints the exact model revision bound to the
  SQLite index.
- `tests/test_knowledge.py` — coverage, source integrity, filtering, pinned
  snapshot, scheme fields, and end-to-end evidence provenance tests.
- `src/rythusaathi/knowledge/README.md` and `data/schemes/README.md` — current
  corpus scope, model pin, source limitations and verification results.
- `output/RythuSaathi_Phase3_Execution_Record.md` — this final Phase 3 record.

The workspace has no `.git` directory, so Git status/diff could not be
produced. No Phase 0–2 production code was changed. No Phase 4 work was started.

## Remaining scope limits

- This is a bounded 15-document agricultural corpus, not a complete crop or
  state knowledge base. Eight older agricultural entries remain geographically
  unspecified and are excluded by strict geography filters.
- Explicit state-specific agriculture guidance in this corpus is Punjab-only;
  the state scheme example is Telangana-only. No other state applicability is
  inferred.
- The Telangana scheme source is a scanned Telugu order. pypdf cannot extract
  its text; this record notes visual review and preserves the official PDF.
  Application-channel and document-checklist details remain unknown because
  the order does not state them.
- The PM-KISAN and PMFBY summaries are historical 2020 documents. Current
  eligibility and enrollment require the applicable official notification.
- A first-time model install needs network access to fetch the pinned snapshot.
  Once cached, the provider can load it offline. The full regression suite
  requires Windows OS credential-store access and therefore ran outside the
  restricted sandbox.
- Similarity is a ranking signal, not a factual confidence score. Retrieved
  evidence should not be treated as a field diagnosis or a guarantee of scheme
  eligibility.

**Final Phase 3 decision: Tasks 3.1–3.8 PASS for the bounded acceptance corpus
and verified local implementation. Phase 4 has not started.**

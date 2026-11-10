# Local agricultural knowledge retrieval

The package provides typed source/document contracts, explicit local JSON
ingestion, deterministic chunking and content hashes, replaceable embeddings,
in-memory retrieval, SQLite persistence, metadata filters, and mapping to the
Phase 2 `EvidenceRecord`. Importing the package does not fetch data or model
files.

## Persistent index and embeddings

Run corpus ingestion explicitly:

```powershell
.venv\Scripts\python.exe scripts\ingest_knowledge.py --model-cache output\models
```

The default index is `output/rythusaathi-knowledge-fastembed.sqlite3`. Install
the optional local runtime with `.venv\Scripts\python.exe -m pip install -e
".[semantic]"`. FastEmbed 0.9.1 uses ONNX Runtime on CPU and the
multilingual MiniLM model `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
(384 dimensions). The provider resolves the FastEmbed model to
`Qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q` and pins immutable snapshot
commit `faf4aa4225822f3bc6376869cb1164e8e3feedd0`. It looks for that complete
snapshot locally first and can download that exact commit when it is not cached.
The emitted embedding metadata and SQLite model binding record this commit,
not a moving model-registry label. The model is loaded lazily. No Groq
dependency is used.

The dependency-free `HashEmbeddingProvider` is a deterministic lexical
baseline, not pretrained semantic understanding. Keep lexical and neural
indexes in separate databases. A database rejects provider/model/dimension
mismatches.

`PersistentKnowledgeBase` stores source metadata, scope, original text, hashes,
revision identifiers and float vectors in SQLite. Reopening preserves indexed
chunks. Identical chunks are idempotent; revised documents receive
revision-sensitive IDs. Search uses an exact cosine scan with crop, geography,
stage, topic, source-type and scheme-jurisdiction filters. This is suitable for
the current small corpus, not a high-volume ANN index. Scores rank results and
are not factual confidence.

## PDF processing

Install `pypdf` with `.venv\Scripts\python.exe -m pip install -e ".[pdf]"`.
PDF text is extracted page by page, raw embedded metadata is available through
`DocumentProcessor.extract_pdf_metadata`, and chunks retain page numbers.
Empty PDFs produce no chunks; malformed PDFs raise a clear parse error.

The official PAU Kharif 2026 Punjab package was parsed with pypdf 6.21.4:
208/208 pages yielded text (523,525 characters), including the rice irrigation,
growth-stage, disease, harvest and cotton soil-preparation source passages used
in the curated set. The Telangana Rythu Bharosa G.O.Rt.No.18 PDF is a two-page
image scan; pypdf returned no text. Its rendered pages were visually inspected.
Other real ICAR and central government PDFs from the earlier Phase 3
verification were also parsed and page-aware chunking was checked. PDF
creation/modification metadata is not substituted for a source publication
date.

## Demonstration and evaluation

Run the end-to-end demonstration (no Groq credentials required):

```powershell
.venv\Scripts\python.exe scripts\demo_retrieval.py
```

Evaluate the checked-in query set with the pinned model or lexical baseline:

```powershell
.venv\Scripts\python.exe scripts\evaluate_retrieval.py --model-cache tmp\models
.venv\Scripts\python.exe scripts\evaluate_retrieval.py --lexical
```

The 16-case evaluation covers 12 supported crop/scheme queries and 4
unsupported queries. Both actual FastEmbed inference and the separate lexical
baseline returned the expected document at Hit@3 for 12/12 supported queries
and empty results for 4/4 unsupported queries. Supported-query MRR was 1.000;
overall MRR including intentionally empty unsupported cases was 0.750. The
lexical result is only a baseline and is not evidence of semantic quality.

## Implemented data coverage

The curated agricultural corpus has 15 documents across six registered crops:
rice (8), maize (2), wheat (1), cotton (2), black gram (1), and chickpea (1).
All ten agricultural topic categories are represented: cultivation,
sowing/planting, irrigation, fertilizer management, soil preparation, growth
stages, pest management, disease identification, nutrient deficiencies, and
harvesting/post-harvest. Five entries carry explicit growth-stage metadata
across rice and cotton. Seven of 15 entries have explicit Punjab
geographic applicability: two previously scoped entries and five PAU Kharif
2026 entries. The other eight remain geographically unspecified and are not
included in strict location-filtered retrieval. No nationwide applicability or
coverage of other states is implied.

Sources include ICAR advisories, an ICAR-IARI wheat variety page, an ICAR
article attributing rice deficiency information to Punjab Agricultural
University scientists, and the PAU Kharif 2026 `Package of Practices for Crops
of Punjab` (Volume 43, March 2026). The PAU package is explicitly written for
Punjab. The month is retained in its revision label; no unsupported day is
assigned as `publication_date`. These are bounded topic summaries, not
complete crop packages or individual-field diagnoses.

Government data has two central historical guideline summaries (PM-KISAN and
PMFBY) and one state-level Telangana summary: Rythu Bharosa Scheme 2025,
G.O.Rt.No.18 dated 10 January 2025. Its official source URL, Telangana
jurisdiction, exact issue date, revision label, retrieval timestamp, eligibility,
benefits, limitations, required-records and procedure fields are retained. The
GO is a scanned Telugu document: its own text does not state a farmer-led
application route or document checklist, and the corpus says so explicitly.
Current application steps are not inferred. Scheme summaries do not replace
the original orders or current notifications.

The Phase 2 crop catalog additions from earlier work remain limited to crop
taxonomy references; taxonomy sources were not used as agronomic guidance. No
Phase 0–2 production contracts were changed in this source-coverage update.

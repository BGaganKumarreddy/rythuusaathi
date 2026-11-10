from datetime import date, datetime, timezone
from pathlib import Path
import tempfile
import sys
import types
import unittest
import json
from unittest.mock import patch

from pydantic import ValidationError

from rythusaathi.domain.seed import CROP_REGISTRY
from rythusaathi.knowledge import (
    AgriculturalDocument, DocumentMetadata, DocumentProcessor, Embedding, FastEmbedProvider, HashEmbeddingProvider,
    IngestionError, KnowledgeTopic, LocalKnowledgeBase, RetrievalFilter, SchemeDocument,
    PersistentKnowledgeBase, SourceReliability, SourceType, load_curated, load_schemes, to_evidence_record,
)
from rythusaathi.knowledge.adapters import RetrievalError
from rythusaathi.knowledge.file_adapters import CuratedFileAdapter, SchemeFileAdapter

ROOT = Path(__file__).resolve().parents[1]


def rice_doc(**kw):
    d = dict(document_id="rice-test", title="Rice test", crop_id="crop:oryza-sativa",
        topic=KnowledgeTopic.IRRIGATION, content="Rice irrigation guidance at tillering.",
        source_id="icar-test", source_identity="ICAR", source_reference="https://icar.gov.in/doc",
        publisher="ICAR", publication_date=date(2025, 1, 1), revision="v1",
        growth_stage="tillering", geographic_applicability=("location:state:in-tg",))
    d.update(kw)
    return AgriculturalDocument(**d)


class KnowledgeIngestionTests(unittest.TestCase):
    def test_starter_curated_dataset_uses_crop_registry_and_preserves_attribution(self):
        docs = load_curated(ROOT / "data/agricultural/icar_advisories.json", CROP_REGISTRY)
        self.assertEqual(len(docs), 15)
        self.assertTrue(all(CROP_REGISTRY.get(d.crop_id) for d in docs))
        self.assertIn("icar.gov.in", docs[0].source_reference)
        self.assertIsNone(docs[0].publication_date)
        self.assertEqual(docs[0].revision, "May 2025 edition")
        self.assertEqual({CROP_REGISTRY.get(d.crop_id).category.value for d in docs},
                         {"cereal", "pulse", "fibre"})
        rice_scope = next(d for d in docs if d.document_id.endswith("rice-micronutrients-punjab"))
        self.assertEqual(rice_scope.geographic_applicability, ("location:state:in-pb",))

    def test_phase3_agricultural_topic_stage_geography_and_source_coverage(self):
        docs = load_curated(ROOT / "data/agricultural/icar_advisories.json", CROP_REGISTRY)
        expected_topics = {topic.value for topic in KnowledgeTopic if topic is not KnowledgeTopic.SCHEME}
        self.assertEqual({doc.topic.value for doc in docs}, expected_topics)
        self.assertGreaterEqual(len({doc.crop_id for doc in docs}), 6)
        scoped = [doc for doc in docs if doc.geographic_applicability]
        self.assertEqual(len(scoped), 7)
        staged = [doc for doc in docs if doc.growth_stage]
        self.assertGreaterEqual(len(staged), 5)
        self.assertGreaterEqual(len({doc.crop_id for doc in staged}), 2)
        pau_docs = [doc for doc in docs if doc.source_id == "pau-kharif-2026"]
        self.assertEqual(len(pau_docs), 5)
        self.assertTrue(all(doc.publisher == "Punjab Agricultural University, Ludhiana" for doc in pau_docs))
        self.assertTrue(all(doc.source_reference == "https://pau.edu/content/ccil/pf/pp_kharif.pdf" for doc in pau_docs))
        self.assertTrue(all(doc.geographic_applicability == ("location:state:in-pb",) for doc in pau_docs))
        self.assertTrue(all(doc.revision.endswith("March 2026)") for doc in pau_docs))
        self.assertTrue(all(doc.publication_date is None for doc in pau_docs))
        by_id = {doc.document_id: doc for doc in pau_docs}
        self.assertIn("two weeks", by_id["pau-kharif-2026:rice-irrigation"].content)
        self.assertIn("34–40°C", by_id["pau-kharif-2026:rice-growth-stages"].content)
        self.assertIn("Southern rice black-streaked dwarf virus",
                      by_id["pau-kharif-2026:rice-dwarfing-disease"].content)
        self.assertIn("17%", by_id["pau-kharif-2026:rice-harvest-storage"].content)
        self.assertIn("wheat straw", by_id["pau-kharif-2026:cotton-soil-preparation"].content)

    def test_unknown_crop_duplicate_and_bad_schema_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as td:
            path = Path(td) / "bad.json"
            path.write_text('[{"document_id":"x","title":"x","crop_id":"unknown",'
                '"topic":"irrigation","content":"x","source_id":"s",'
                '"source_identity":"s","source_reference":"https://icar.gov.in/x"}]')
            with self.assertRaises(IngestionError): load_curated(path, CROP_REGISTRY)
            path.write_text('[{"document_id":"x"},{"document_id":"x"}]')
            with self.assertRaises(IngestionError): load_curated(path, CROP_REGISTRY)
            path.write_text('[{"document_id":"x","title":"x","crop_id":"crop:oryza-sativa",'
                '"topic":"irrigation","content":"same","source_id":"s","source_identity":"s",'
                '"source_reference":"https://icar.gov.in/a"},{"document_id":"y","title":"y",'
                '"crop_id":"crop:oryza-sativa","topic":"irrigation","content":" SAME ",'
                '"source_id":"s","source_identity":"s","source_reference":"https://icar.gov.in/b"}]')
            with self.assertRaises(IngestionError): load_curated(path, CROP_REGISTRY)

    def test_same_document_id_can_keep_distinct_explicit_revisions(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as td:
            path = Path(td) / "revisions.json"
            one = rice_doc(document_id="stable-id", revision="v1", content="Old wording.").model_dump(mode="json")
            two = rice_doc(document_id="stable-id", revision="v2", content="Revised wording.").model_dump(mode="json")
            path.write_text(__import__("json").dumps([one, two]), encoding="utf-8")
            docs = load_curated(path, CROP_REGISTRY)
            self.assertEqual([d.revision for d in docs], ["v1", "v2"])

    def test_curated_contract_requires_source_and_nonblank_content(self):
        with self.assertRaises(ValidationError): rice_doc(content=" ")


class SchemeTests(unittest.TestCase):
    def test_official_scheme_metadata_and_classification(self):
        docs = load_schemes(ROOT / "data/schemes/central_agricultural_schemes.json")
        self.assertEqual(len(docs), 3)
        doc = docs[0]
        self.assertTrue(doc.official)
        self.assertEqual(doc.source_type, SourceType.GOVERNMENT)
        self.assertEqual(doc.jurisdiction, ("location:country:in",))
        self.assertIsNone(doc.publication_date)
        self.assertEqual(doc.revised_at, date(2020, 3, 29))
        self.assertTrue(doc.retrieved_at and doc.retrieved_at.tzinfo)
        self.assertIn("State/UT", doc.required_documents_text)
        self.assertIn("1 February 2019", doc.limitations_text)
        self.assertIn("PM-KISAN portal", doc.procedure_text)
        self.assertIn("pmkisan.gov.in/Documents", doc.source_reference)
        crop_insurance = docs[1]
        self.assertEqual(crop_insurance.jurisdiction_level, "central")
        self.assertEqual(crop_insurance.publication_date, date(2020, 8, 17))
        self.assertTrue(crop_insurance.retrieved_at and crop_insurance.retrieved_at.tzinfo)
        self.assertIn("sharecroppers", crop_insurance.eligibility_text)
        self.assertIn("State/UT notifications", crop_insurance.limitations_text)

        telangana = next(doc for doc in docs if doc.document_id == "rythu-bharosa-telangana:go-rt-18-2025")
        self.assertTrue(telangana.official)
        self.assertEqual(telangana.source_type, SourceType.GOVERNMENT)
        self.assertEqual(telangana.source_identity,
            "Agriculture and Cooperation (Agriculture-II) Department, Government of Telangana")
        self.assertEqual(telangana.publisher, "Government of Telangana, Agriculture and Cooperation Department")
        self.assertEqual(telangana.jurisdiction, ("location:state:in-tg",))
        self.assertEqual(telangana.jurisdiction_level, "state")
        self.assertEqual(telangana.publication_date, date(2025, 1, 10))
        self.assertIn("G.O.Rt.No. 18", telangana.revision)
        self.assertTrue(telangana.retrieved_at and telangana.retrieved_at.tzinfo)
        self.assertEqual(telangana.retrieved_at.isoformat(), "2026-11-06T15:16:52.179000+00:00")
        self.assertIn("rythubharosa.telangana.gov.in/assets/files/", telangana.source_reference)
        self.assertTrue(all((telangana.eligibility_text, telangana.required_documents_text,
                             telangana.benefits_text, telangana.limitations_text,
                             telangana.procedure_text)))
        self.assertIn("Bhoobharati (Dharani)", telangana.eligibility_text)
        self.assertIn("ROFR pattadars", telangana.eligibility_text)
        self.assertIn("12,000 per acre", telangana.benefits_text)
        self.assertIn("does not specify a farmer-submitted document checklist", telangana.required_documents_text)
        self.assertIn("excludes non-agricultural land", telangana.limitations_text)
        self.assertIn("does not describe a farmer-led application route", telangana.procedure_text)

    def test_scheme_structured_fields_are_included_in_processed_evidence_text(self):
        doc = load_schemes(ROOT / "data/schemes/central_agricultural_schemes.json")[0]
        chunks = DocumentProcessor(chunk_size=4000, overlap=120).process_scheme(doc)
        text = "\n".join(chunk.text for chunk in chunks)
        self.assertIn(doc.eligibility_text, text)
        self.assertIn(doc.required_documents_text, text)
        self.assertIn(doc.benefits_text, text)
        self.assertIn(doc.limitations_text, text)
        self.assertIn(doc.procedure_text, text)
        self.assertTrue(all(chunk.source.retrieved_at == doc.retrieved_at for chunk in chunks))

    def test_official_and_secondary_must_not_be_mislabeled(self):
        with self.assertRaises(ValidationError):
            SchemeDocument(document_id="bad", title="bad", content="text", source_id="s",
                source_identity="s", source_reference="https://example.org", publisher="p",
                jurisdiction=("location:country:in",), official=False)


class ProcessingAndRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.processor = DocumentProcessor(chunk_size=35, overlap=5)
        self.chunks = self.processor.process_curated(rice_doc())

    def test_normalization_chunk_stability_hash_and_parent_refs(self):
        meta = DocumentMetadata(document_id="d", source_id="s", title="Doc",
            source_type=SourceType.CURATED, reliability=SourceReliability.CURATED,
            source_identity="ICAR", reference="https://icar.gov.in/doc")
        a = self.processor.process_text(meta, "A  line\r\nwith\t spaces. More text.", section="Section 1")
        b = self.processor.process_text(meta, "A  line\r\nwith\t spaces. More text.", section="Section 1")
        self.assertEqual([c.chunk_id for c in a], [c.chunk_id for c in b])
        self.assertEqual(a[0].parent_document_id, "d")
        self.assertTrue(all(c.content_hash for c in a))
        self.assertEqual(a[0].section, "Section 1")
        self.assertEqual(self.processor.process_text(meta, "   "), ())
        retrieved_one = meta.model_copy(update={"retrieved_at": datetime(2026, 1, 1, tzinfo=timezone.utc)})
        retrieved_two = meta.model_copy(update={"retrieved_at": datetime(2026, 2, 1, tzinfo=timezone.utc)})
        self.assertEqual(
            self.processor.process_text(retrieved_one, "same content")[0].chunk_id,
            self.processor.process_text(retrieved_two, "same content")[0].chunk_id)
        changed_title = meta.model_copy(update={"title": "Updated title"})
        self.assertNotEqual(
            self.processor.process_text(meta, "same content")[0].chunk_id,
            self.processor.process_text(changed_title, "same content")[0].chunk_id)

    def test_embedding_batch_validation_and_determinism(self):
        model = HashEmbeddingProvider(dimensions=64)
        x = model.embed(["rice irrigation", "wheat soil"])
        self.assertEqual(len(x), 2)
        self.assertEqual(x, model.embed(["rice irrigation", "wheat soil"]))
        self.assertEqual(x[0].dimensions, 64)
        with self.assertRaises(ValueError): model.embed([""])
        with self.assertRaises(TypeError): model.embed([None])

    def test_fastembed_provider_is_lazy_normalizes_and_records_model_metadata(self):
        calls = []
        snapshot_calls = []
        class FakeModel:
            def embed(self, texts, batch_size):
                calls.append((list(texts), batch_size))
                return [[3.0] + [0.0] * 383 for _ in texts]
        class FakeTextEmbedding:
            def __init__(self, **kwargs):
                calls.append(kwargs)
            def embed(self, texts, batch_size):
                return FakeModel().embed(texts, batch_size)
        def fake_snapshot_download(**kwargs):
            snapshot_calls.append(kwargs)
            return Path("tmp/fake-fastembed-snapshot")
        provider = FastEmbedProvider(batch_size=2)
        self.assertEqual(calls, [])
        with patch.dict(sys.modules, {
            "fastembed": types.SimpleNamespace(TextEmbedding=FakeTextEmbedding),
            "huggingface_hub": types.SimpleNamespace(snapshot_download=fake_snapshot_download),
        }):
            results = provider.embed(["rice query", "wheat query"])
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].dimensions, 384)
        self.assertEqual(results[0].model_id, provider.DEFAULT_MODEL)
        self.assertEqual(results[0].model_revision, provider.DEFAULT_REVISION)
        self.assertEqual(provider.DEFAULT_REVISION, "faf4aa4225822f3bc6376869cb1164e8e3feedd0")
        self.assertAlmostEqual(sum(x*x for x in results[0].vector), 1.0)
        self.assertIn("CPUExecutionProvider", calls[0]["providers"])
        self.assertEqual(snapshot_calls[0]["repo_id"], provider.DEFAULT_MODEL_REPOSITORY)
        self.assertEqual(snapshot_calls[0]["revision"], provider.DEFAULT_REVISION)
        self.assertEqual(calls[0]["specific_model_path"], str(Path("tmp/fake-fastembed-snapshot")))
        self.assertTrue(calls[0]["local_files_only"])
        self.assertEqual(calls[1][1], 2)

    def test_fastembed_missing_dependency_is_explained(self):
        provider = FastEmbedProvider()
        with patch.dict(sys.modules, {"fastembed": None}):
            with self.assertRaisesRegex(RuntimeError, "optional 'semantic' extra"):
                provider.embed(["rice"])

    def test_metadata_filters_rank_and_empty_results(self):
        kb = LocalKnowledgeBase(HashEmbeddingProvider())
        kb.add(self.chunks)
        results = kb.search("rice irrigation tillering", filters=RetrievalFilter(
            crop_id="crop:oryza-sativa", geography="location:state:in-tg", topic="irrigation"))
        self.assertTrue(results)
        self.assertGreaterEqual(results[0].similarity, 0)
        self.assertEqual(kb.search("rice", filters=RetrievalFilter(crop_id="crop:wrong")), ())
        self.assertEqual(kb.search("rice", filters=RetrievalFilter(geography="location:state:in-ap")), ())
        self.assertEqual(kb.search("unrelatedxyz"), ())
        unknown_scope = self.processor.process_curated(
            rice_doc(document_id="unknown-scope", geographic_applicability=None))
        kb2 = LocalKnowledgeBase(HashEmbeddingProvider())
        kb2.add(unknown_scope)
        self.assertEqual(kb2.search("rice irrigation", filters=RetrievalFilter(
            geography="location:state:in-pb")), ())

    def test_pdf_page_extraction_and_empty_or_malformed_handling(self):
        class Page:
            def __init__(self, text): self.text = text
            def extract_text(self): return self.text
        class Reader:
            pages = (Page("first page"), Page("second page"))
            metadata = {"/Title": "Agricultural guide", "/Author": "ICAR"}
            def __init__(self, path): pass
        fake = types.SimpleNamespace(PdfReader=Reader)
        processor = DocumentProcessor()
        with patch.dict(sys.modules, {"pypdf": fake}):
            self.assertEqual(processor.extract_pdf("mock.pdf"), ((1, "first page"), (2, "second page")))
            self.assertEqual(processor.extract_pdf_metadata("mock.pdf"),
                             {"/Author": "ICAR", "/Title": "Agricultural guide"})
            class EmptyReader:
                pages = ()
                metadata = None
                def __init__(self, path): pass
            sys.modules["pypdf"] = types.SimpleNamespace(PdfReader=EmptyReader)
            self.assertEqual(processor.extract_pdf("empty.pdf"), ())
            self.assertEqual(processor.extract_pdf_metadata("empty.pdf"), {})
            class BadReader:
                def __init__(self, path): raise ValueError("malformed")
            sys.modules["pypdf"] = types.SimpleNamespace(PdfReader=BadReader)
            with self.assertRaisesRegex(ValueError, "unable to parse PDF"):
                processor.extract_pdf("bad.pdf")
            with self.assertRaisesRegex(ValueError, "unable to parse PDF metadata"):
                processor.extract_pdf_metadata("bad.pdf")

    def test_chunk_duplicate_detection(self):
        kb = LocalKnowledgeBase(HashEmbeddingProvider())
        kb.add(self.chunks)
        with self.assertRaises(ValueError): kb.add(self.chunks)

    def test_embedding_provider_failure_is_reported_without_partial_index(self):
        class Broken:
            model_id = "broken"
            dimensions = 16
            def embed(self, texts): raise RuntimeError("private provider detail")
        kb = LocalKnowledgeBase(Broken())
        with self.assertRaisesRegex(RetrievalError, "embedding failed while indexing"):
            kb.add(self.chunks)
        self.assertEqual(kb.size, 0)
        with self.assertRaisesRegex(RetrievalError, "embedding failed during retrieval"):
            kb.search("rice")

    def test_evidence_keeps_source_excerpt_parent_and_retrieval_time(self):
        kb = LocalKnowledgeBase(HashEmbeddingProvider())
        chunks = self.processor.process_curated(rice_doc(revised_at=date(2025, 1, 2)))
        kb.add(chunks)
        result = kb.search("rice irrigation tillering")[0]
        evidence = to_evidence_record(result, request_id="request-1")
        self.assertEqual(evidence.source.reference.value, "https://icar.gov.in/doc")
        self.assertEqual(evidence.content_reference.value, "rice-test")
        self.assertEqual(evidence.excerpt, result.chunk.text)
        self.assertEqual(evidence.crop_applicability, ("crop:oryza-sativa",))
        self.assertEqual(evidence.retrieved_at, result.retrieved_at)
        self.assertEqual(evidence.evidence_id, result.chunk.chunk_id)
        self.assertIn("Revised at: 2025-01-02", evidence.quality_notes)
        self.assertIn("revised at 2025-01-02", evidence.provenance)

    def test_new_agricultural_and_state_scheme_retrieval_maps_source_provenance(self):
        processor = DocumentProcessor()
        agricultural = next(d for d in load_curated(
            ROOT / "data/agricultural/icar_advisories.json", CROP_REGISTRY)
            if d.document_id == "pau-kharif-2026:rice-harvest-storage")
        scheme = next(d for d in load_schemes(ROOT / "data/schemes/central_agricultural_schemes.json")
            if d.document_id == "rythu-bharosa-telangana:go-rt-18-2025")
        agricultural_chunks = processor.process_curated(agricultural)
        scheme_chunks = processor.process_scheme(scheme)
        self.assertTrue(scheme_chunks)
        self.assertTrue(all(chunk.source.retrieved_at == scheme.retrieved_at for chunk in scheme_chunks))

        index = LocalKnowledgeBase(HashEmbeddingProvider())
        index.add((*agricultural_chunks, *scheme_chunks))
        pau_hit = index.search("Punjab paddy harvest straw yellow ripe storage moisture",
            filters=RetrievalFilter(crop_id="crop:oryza-sativa", geography="location:state:in-pb",
                                    topic="harvesting_postharvest"))[0]
        pau_evidence = to_evidence_record(pau_hit, request_id="phase3-pau-provenance")
        self.assertEqual(pau_evidence.source.reference.value, agricultural.source_reference)
        self.assertEqual(pau_evidence.source.source_identity, agricultural.source_identity)
        self.assertEqual(pau_evidence.source.publisher, agricultural.publisher)
        self.assertEqual(pau_evidence.content_reference.value, agricultural.document_id)
        self.assertEqual(pau_evidence.excerpt, pau_hit.chunk.text)
        self.assertEqual(pau_evidence.geographic_applicability, ("location:state:in-pb",))
        self.assertIn("Rice — Harvesting and Threshing", pau_evidence.page_or_section)
        self.assertIn(agricultural.revision, pau_evidence.provenance)
        self.assertIn("Growth stage: harvest maturity and post-harvest storage", pau_evidence.quality_notes)

        scheme_hit = index.search("Telangana Rythu Bharosa 12000 per acre pattadar DBT",
            filters=RetrievalFilter(geography="location:state:in-tg", topic="government_scheme",
                                    jurisdiction_level="state", source_type="primary_government"))[0]
        scheme_evidence = to_evidence_record(scheme_hit, request_id="phase3-telangana-provenance")
        self.assertEqual(scheme_evidence.source.reference.value, scheme.source_reference)
        self.assertEqual(scheme_evidence.source.source_identity, scheme.source_identity)
        self.assertEqual(scheme_evidence.source.publisher, scheme.publisher)
        self.assertEqual(scheme_evidence.content_reference.value, scheme.document_id)
        self.assertIn("12,000 per acre", scheme_evidence.excerpt)
        self.assertEqual(scheme_evidence.geographic_applicability, ("location:state:in-tg",))
        self.assertIn("Scheme jurisdiction level: state", scheme_evidence.quality_notes)
        self.assertIn(scheme.revision, scheme_evidence.provenance)

    def test_pdf_parser_clear_when_optional_parser_missing(self):
        meta = DocumentMetadata(document_id="pdf", source_id="s", title="PDF",
            source_type=SourceType.GOVERNMENT, reliability=SourceReliability.PRIMARY,
            source_identity="Gov", reference="https://gov.in/doc.pdf")
        with patch.dict(sys.modules, {"pypdf": None}):
            with self.assertRaisesRegex(RuntimeError, "pypdf"):
                self.processor.process_pdf(meta, "absent.pdf")
            with self.assertRaisesRegex(RuntimeError, "pypdf"):
                self.processor.extract_pdf_metadata("absent.pdf")


class AdapterTests(unittest.TestCase):
    def test_local_adapters_are_explicit_and_report_unknown_ids(self):
        curated = CuratedFileAdapter(ROOT / "data/agricultural/icar_advisories.json", CROP_REGISTRY)
        scheme = SchemeFileAdapter(ROOT / "data/schemes/central_agricultural_schemes.json")
        self.assertEqual(len(curated.list_documents()), 15)
        self.assertEqual(len(scheme.list_documents()), 3)
        with self.assertRaises(RetrievalError): curated.get_content("missing")
        self.assertIn("PM-KISAN", scheme.get_content("pm-kisan:operational-guidelines-revised"))
        self.assertEqual(scheme.list_documents()[1].jurisdiction_level, "central")


class PersistentStoreTests(unittest.TestCase):
    def test_new_agricultural_source_filters_by_crop_location_stage_and_topic(self):
        docs = load_curated(ROOT / "data/agricultural/icar_advisories.json", CROP_REGISTRY)
        processor = DocumentProcessor()
        chunks = tuple(chunk for doc in docs for chunk in processor.process_curated(doc))
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as td:
            path = Path(td) / "pau.sqlite3"
            with PersistentKnowledgeBase(path, HashEmbeddingProvider()) as store:
                store.add(chunks)
                filtered = store.search("Punjab rice irrigation after transplanting",
                    filters=RetrievalFilter(crop_id="crop:oryza-sativa", geography="location:state:in-pb",
                        growth_stage="transplant establishment through pre-maturity", topic="irrigation"))
                self.assertTrue(filtered)
                self.assertEqual(filtered[0].chunk.parent_document_id, "pau-kharif-2026:rice-irrigation")
                excluded = store.search("rice irrigation after transplanting",
                    filters=RetrievalFilter(crop_id="crop:oryza-sativa", geography="location:state:in-tg",
                        growth_stage="transplant establishment through pre-maturity", topic="irrigation"))
                self.assertEqual(excluded, ())

    def test_reopen_is_idempotent_and_preserves_metadata_and_filters(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as td:
            path = Path(td) / "knowledge.sqlite3"
            provider = HashEmbeddingProvider()
            first = PersistentKnowledgeBase(path, provider)
            source_chunk = self_processor().process_curated(rice_doc())
            self.assertEqual(first.add(source_chunk), 1)
            self.assertEqual(first.add(source_chunk), 0)
            first.close()
            reopened = PersistentKnowledgeBase(path, provider)
            self.assertEqual(reopened.size, 1)
            hits = reopened.search("rice irrigation tillering", filters=RetrievalFilter(
                crop_id="crop:oryza-sativa", geography="location:state:in-tg",
                growth_stage="tillering", topic="irrigation", source_type="curated_agricultural_knowledge"))
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0].chunk.parent_document_id, "rice-test")
            self.assertEqual(hits[0].chunk.source.revision, "v1")
            self.assertEqual(reopened.search("rice", filters=RetrievalFilter(geography="location:state:in-pb")), ())
            reopened.close()

    def test_persistent_scheme_level_filter_is_separate_from_geography(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as td:
            path = Path(td) / "schemes.sqlite3"
            docs = load_schemes(ROOT / "data/schemes/central_agricultural_schemes.json")
            processor = DocumentProcessor()
            chunks = tuple(c for doc in docs for c in processor.process_scheme(doc))
            with PersistentKnowledgeBase(path, HashEmbeddingProvider()) as store:
                store.add(chunks)
                central = store.search("PM KISAN landholding farmer eligibility",
                    filters=RetrievalFilter(topic="government_scheme", jurisdiction_level="central"))
                telangana = store.search("Rythu Bharosa Telangana pattadar DBT amount per acre",
                    filters=RetrievalFilter(topic="government_scheme", geography="location:state:in-tg",
                                            jurisdiction_level="state", source_type="primary_government"))
                state = store.search("Punjab farmer state scheme eligibility",
                    filters=RetrievalFilter(geography="location:state:in-pb",
                                            jurisdiction_level="state"))
                self.assertTrue(central)
                self.assertTrue(telangana)
                self.assertEqual(telangana[0].chunk.parent_document_id,
                                 "rythu-bharosa-telangana:go-rt-18-2025")
                self.assertEqual(state, ())


class RetrievalEvaluationTests(unittest.TestCase):
    def test_deterministic_eval_suite_has_supported_hits_and_safe_empty_cases(self):
        processor = DocumentProcessor()
        agricultural = load_curated(ROOT / "data/agricultural/icar_advisories.json", CROP_REGISTRY)
        schemes = load_schemes(ROOT / "data/schemes/central_agricultural_schemes.json")
        chunks = [c for d in agricultural for c in processor.process_curated(d)]
        chunks.extend(c for d in schemes for c in processor.process_scheme(d))
        index = LocalKnowledgeBase(HashEmbeddingProvider())
        index.add(chunks)
        cases = json.loads((ROOT / "data/agricultural/retrieval_eval.json").read_text(encoding="utf-8"))
        supported_hits = unsupported_empty = supported_total = unsupported_total = 0
        for case in cases:
            results = index.search(case["query"], top_k=3, filters=RetrievalFilter(**case["filters"]))
            found = {result.chunk.parent_document_id for result in results}
            expected = set(case["expected_document_ids"])
            if expected:
                supported_total += 1
                supported_hits += bool(found & expected)
            else:
                unsupported_total += 1
                unsupported_empty += not found
        self.assertEqual((supported_hits, supported_total), (12, 12))
        self.assertEqual((unsupported_empty, unsupported_total), (4, 4))

    def test_revision_is_additive_and_model_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as td:
            path = Path(td) / "knowledge.sqlite3"
            processor = DocumentProcessor()
            v1 = processor.process_curated(rice_doc())
            v2 = processor.process_curated(rice_doc(revision="v2", content="Revised rice irrigation guidance."))
            store = PersistentKnowledgeBase(path, HashEmbeddingProvider())
            self.assertEqual(store.add(v1), 1)
            self.assertEqual(store.add(v2), 1)
            self.assertEqual(store.size, 2)
            store.close()
            with self.assertRaisesRegex(ValueError, "do not match provider"):
                PersistentKnowledgeBase(path, HashEmbeddingProvider(dimensions=64))


def self_processor():
    return DocumentProcessor()


if __name__ == "__main__": unittest.main()

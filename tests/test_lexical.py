"""Permanent regressions for lexical identity/date lookup; all evidence synthetic."""
import json

import pytest
from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document

from app.retrieval.lexical import LEXICAL_PROFILE_VERSION, lexical_tokens, normalize_party_name
from app.retrieval.indexer import _sanitize_metadata_for_chroma


def test_names_unicode_accents_case_punctuation_and_negation():
    assert lexical_tokens("JOÃO, D'ÁVILA não rescindirá!") == lexical_tokens("joao d avila NAO rescindira")
    assert "nao" in lexical_tokens("NÃO")
    assert normalize_party_name("  João, D’Ávila  ") == "joao d avila"
    assert LEXICAL_PROFILE_VERSION


@pytest.mark.parametrize("literal,plain", [
    ("123.456.789-01", "12345678901"),
    ("11.222.333/0001-44", "11222333000144"),
    ("AB.CDE.FGH/IJKL-12", "ABCDEFGHIJKL12"),
])
def test_identifier_aliases(literal, plain):
    expected = "identifier:" + plain.casefold()
    assert expected in lexical_tokens(literal)
    assert expected in lexical_tokens(plain)


@pytest.mark.parametrize("literal", ["10/05/2024", "10 de maio de 2024", "2024-05-10"])
def test_calendar_date_aliases(literal):
    assert "date:2024-05-10" in lexical_tokens(literal)


def test_invalid_date_never_receives_date_alias():
    assert not any(token.startswith("date:") for token in lexical_tokens("31/02/2024"))
    assert "2024" in lexical_tokens("31/02/2024")


def test_metadata_only_is_not_indexed_and_literal_does_not_change():
    docs = [
        Document(page_content="Contratante: EMPRESA ÁLVARES. Vencimento 10 de maio de 2024", metadata={"chunk_id": "hit"}),
        Document(page_content="Objeto sem nomes", metadata={"chunk_id": "metadata_only", "party_names": ["Álvares"]}),
        Document(page_content="Locação de máquinas", metadata={"chunk_id": "other"}),
    ]
    before = docs[0].page_content
    bm25 = BM25Retriever.from_documents(docs, k=1, preprocess_func=lexical_tokens)
    assert bm25.invoke("alvares 2024-05-10")[0].metadata["chunk_id"] == "hit"
    assert docs[0].page_content == before
    assert "alvares" not in bm25.vectorizer.doc_freqs[1]


def test_chroma_scalar_projection_keeps_canonical_nested_metadata():
    metadata = {"party_names": ["João Ávila"], "normalized_party_names": ["joao avila"],
                "temporal_mentions": [{"kind": "due", "normalized_date": "2024-05-10", "start": 5}],
                "uncertainty_flags": [], "optional": None, "approved": False}
    projection = _sanitize_metadata_for_chroma(metadata)
    assert json.loads(projection["party_names"]) == metadata["party_names"]
    assert json.loads(projection["temporal_mentions"]) == metadata["temporal_mentions"]
    assert json.loads(projection["uncertainty_flags"]) == []
    assert "optional" not in projection
    assert projection["approved"] is False
    assert isinstance(metadata["party_names"], list)
    with pytest.raises(ValueError):
        _sanitize_metadata_for_chroma({"score": float("nan")})


def _candidate_document():
    from app.ingestion.schemas import ContractMetadata, ContractParty, DocumentBlock, ParsedDocument, TemporalMention
    from app.ingestion.temporal import TEMPORAL_EXTRACTOR_VERSION
    due = "  Pagamento em 10/05/2024.  "
    date_start = due.index("10/05/2024")
    blocks = [DocumentBlock(block_id="due", doc_id=1, doc_version=1, block_type="clause", hierarchy_level="clause",
                            order_index=0, text_raw=due, text_search=due),
              DocumentBlock(block_id="other", doc_id=1, doc_version=1, block_type="clause", hierarchy_level="clause",
                            order_index=1, text_raw="Outra obrigação", text_search="Outra obrigação")]
    metadata = ContractMetadata(formal_title="Contrato", instrument_type="Contrato",
                                temporal_extractor_version=TEMPORAL_EXTRACTOR_VERSION,
                                parties=[ContractParty(name="JOÃO D'ÁVILA")],
                                temporal_mentions=[TemporalMention(normalized_date="2024-05-10", kind="due", block_id="due",
                                                    start=date_start, end=date_start+10, raw_text="10/05/2024")])
    return ParsedDocument(doc_id=1, doc_version=1, file_path="synthetic.docx", file_hash="synthetic",
                          parser_name="synthetic", parser_version="1", metadata=metadata, blocks=blocks)


def test_candidate_dates_locality_original_name_whitespace_and_no_legacy_signature():
    from app.ingestion.chunker import create_candidate_chunks as create_legal_chunks
    doc = _candidate_document()
    chunks = create_legal_chunks([doc])
    assert chunks[0].metadata["verbatim_text"] == doc.blocks[0].text_raw
    assert chunks[0].metadata["party_names"] == ["JOÃO D'ÁVILA"]
    assert chunks[0].metadata["normalized_party_names"] == ["joao d avila"]
    assert "Data candidata não homologada: due = 2024-05-10" in chunks[0].page_content
    assert "Data candidata" not in chunks[1].page_content
    assert chunks[1].metadata["temporal_mentions"] == []
    assert "2024-01-01" not in chunks[0].page_content
    assert "Data candidata" not in chunks[0].metadata["verbatim_text"]
    assert chunks[0].metadata["temporal_mentions"][0]["review_state"] == "pending_review"
    assert chunks[0].metadata["eligibility"] == "pending_review"

    legacy_doc = _candidate_document()
    legacy_doc.metadata.temporal_mentions = []
    legacy_doc.metadata.temporal_extractor_version = None
    legacy_doc.metadata.execution_date = "2024-01-01"
    assert all("2024-01-01" not in chunk.page_content for chunk in create_legal_chunks([legacy_doc]))


def test_mismatched_candidate_span_fails_closed():
    from app.ingestion.chunker import create_candidate_chunks as create_legal_chunks
    doc = _candidate_document()
    mention = doc.metadata.temporal_mentions[0]
    doc.metadata.temporal_mentions[0] = mention.model_copy(update={"start": mention.start+1, "end": mention.end+1})
    with pytest.raises(ValueError):
        create_legal_chunks([doc])


def test_mutated_document_cannot_forge_signature_from_due_candidate():
    from app.ingestion.chunker import create_candidate_chunks as create_legal_chunks
    from app.ingestion.schemas import DateKind
    doc = _candidate_document()
    mention = doc.metadata.temporal_mentions[0]
    doc.metadata.temporal_mentions[0] = mention.model_copy(update={"kind": DateKind.SIGNATURE})
    doc.metadata.execution_date = "2024-05-10"
    with pytest.raises(ValueError, match="Temporal labels"):
        create_legal_chunks([doc])


def test_identifier_subset_bm25_uses_same_profile():
    from app.retrieval.hybrid import LegalHybridRetriever
    docs = [Document(page_content="ÁLVARES", metadata={"chunk_id": "hit", "clean_identifiers": ["12345678901"]}),
            Document(page_content="Objeto distinto", metadata={"chunk_id": "other", "clean_identifiers": ["12345678901"]}),
            Document(page_content="Terceira evidência", metadata={"chunk_id": "third", "clean_identifiers": ["12345678901"]})]
    retriever = LegalHybridRetriever(docs=docs, k=1)
    assert retriever.invoke("alvares CPF 123.456.789-01")[0].metadata["chunk_id"] == "hit"


def test_persisted_profile_roundtrip_and_old_index_rejection(tmp_path):
    from app.retrieval.indexer import build_and_save_hybrid_index, load_hybrid_retriever
    docs = [Document(page_content="Contratante ÁLVARES em 10/05/2024", metadata={"chunk_id": "hit", "party_names": ["ÁLVARES"],
                    "temporal_mentions": [{"normalized_date": "2024-05-10", "kind": "due"}]}),
            Document(page_content="Outro cliente", metadata={"chunk_id": "other"}),
            Document(page_content="Demais objetos", metadata={"chunk_id": "third"})]
    built = build_and_save_hybrid_index(docs, "synthetic", tmp_path, k=1)
    loaded = load_hybrid_retriever("synthetic", tmp_path, k=1)
    assert loaded.bm25.invoke("alvares 10 de maio de 2024")[0].metadata == docs[0].metadata
    stored = built.chroma.get(ids=["hit"])["metadatas"][0]
    assert json.loads(stored["temporal_mentions"]) == docs[0].metadata["temporal_mentions"]
    marker = tmp_path / "synthetic" / "lexical.json"
    marker.unlink()
    with pytest.raises(ValueError, match="lexical incompatível"):
        load_hybrid_retriever("synthetic", tmp_path)


def test_artifact_hash_rejected_before_joblib_load(tmp_path, monkeypatch):
    from app.retrieval.indexer import build_and_save_hybrid_index, load_hybrid_retriever
    docs = [Document(page_content="Conteúdo sintético", metadata={"chunk_id": "one"})]
    build_and_save_hybrid_index(docs, "synthetic", tmp_path)
    artifact = tmp_path / "synthetic" / "bm25.joblib"
    artifact.write_bytes(artifact.read_bytes()+b"tamper")
    def forbidden_load(*args, **kwargs):
        raise AssertionError("Unverified pickle must not load")
    monkeypatch.setattr("app.retrieval.indexer.joblib.load", forbidden_load)
    with pytest.raises(ValueError, match="lexical incompatível"):
        load_hybrid_retriever("synthetic", tmp_path)


def test_normalization_code_identity_change_requires_rebuild(tmp_path):
    from app.retrieval.indexer import build_and_save_hybrid_index, load_hybrid_retriever
    docs = [Document(page_content="Conteúdo sintético", metadata={"chunk_id": "one"})]
    build_and_save_hybrid_index(docs, "synthetic", tmp_path)
    marker = tmp_path / "synthetic" / "lexical.json"
    profile = json.loads(marker.read_text(encoding="utf-8"))
    assert len(profile["profile"]["code_sha256"]) == 64
    assert profile["profile"]["temporal_extractor_version"] == "temporal-candidates-v1"
    profile["profile"]["code_sha256"] = "0"*64
    marker.write_text(json.dumps(profile), encoding="utf-8")
    with pytest.raises(ValueError, match="lexical incompatível"):
        load_hybrid_retriever("synthetic", tmp_path)


def test_identifier_normalizer_change_rejected_before_joblib_load(tmp_path, monkeypatch):
    from app import identifiers
    from app.retrieval.indexer import build_and_save_hybrid_index, load_hybrid_retriever
    docs = [Document(page_content="CPF 123.456.789-01", metadata={"chunk_id": "one"})]
    build_and_save_hybrid_index(docs, "synthetic", tmp_path)
    synthetic_dependency = tmp_path / "synthetic_identifiers.py"
    synthetic_dependency.write_text("# synthetic changed identity lookup implementation", encoding="utf-8")
    monkeypatch.setattr(identifiers, "__file__", str(synthetic_dependency))
    def forbidden_load(*args, **kwargs):
        raise AssertionError("Incompatible identifier dependency must not load pickle")
    monkeypatch.setattr("app.retrieval.indexer.joblib.load", forbidden_load)
    with pytest.raises(ValueError, match="lexical incompatível"):
        load_hybrid_retriever("synthetic", tmp_path)

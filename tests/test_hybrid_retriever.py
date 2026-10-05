"""
Tests for Atomic Hybrid Indexer (ChromaDB + BM25) and LegalHybridRetriever.

Verifies:
1. build_and_save_hybrid_index creates ChromaDB directory and BM25 joblib file atomically.
2. Query with formatted and unformatted CNPJ strictly pre-filters eligible chunks by metadata["clean_identifiers"].
3. Query with formatted and unformatted CPF strictly pre-filters eligible chunks.
4. Query with non-existent CNPJ/CPF returns empty list (zero cross-client contamination).
5. Generic thematic query executes hybrid search (BM25 + Chroma) fused with deterministic RRF.
6. Verbatim text, chunk_id, and full metadata are preserved intact in all retrieved documents.
7. load_hybrid_retriever reloads persisted indices from disk.
"""

from pathlib import Path
import pytest
from langchain_core.documents import Document

from app.retrieval.hybrid import (
    LegalHybridRetriever,
    extract_clean_identifiers_from_query,
)
from app.retrieval.indexer import (
    DeterministicHashEmbeddings,
    build_and_save_hybrid_index,
    load_hybrid_retriever,
)


@pytest.fixture
def sample_legal_chunks() -> list[Document]:
    return [
        Document(
            page_content="[Instrumento: Contrato de Prestação de Serviços - Andrade & Silva Advogados]\nContratante: Alpha Serviços Ltda (11.222.333/0001-44)\n[Localização: Cláusula 1ª]\n\nCláusula 1ª - Os honorários advocatícios serão de 20% sobre o valor da causa.",
            metadata={
                "chunk_id": "doc_101_v1_chunk_0",
                "doc_id": 101,
                "doc_version": 1,
                "formal_title": "Contrato de Prestação de Serviços Advocatícios",
                "instrument_type": "Contrato de Prestação de Serviços",
                "clean_identifiers": ["11222333000144"],
                "verbatim_text": "Cláusula 1ª - Os honorários advocatícios serão de 20% sobre o valor da causa.",
                "uncertainty_flags": [],
            },
        ),
        Document(
            page_content="[Instrumento: Contrato de Prestação de Serviços - Andrade & Silva Advogados]\nContratante: Alpha Serviços Ltda (11.222.333/0001-44)\n[Localização: Cláusula 2ª]\n\nCláusula 2ª - Em caso de rescisão imotivada, multa de 10% sobre o saldo remanescente.",
            metadata={
                "chunk_id": "doc_101_v1_chunk_1",
                "doc_id": 101,
                "doc_version": 1,
                "formal_title": "Contrato de Prestação de Serviços Advocatícios",
                "instrument_type": "Contrato de Prestação de Serviços",
                "clean_identifiers": ["11222333000144"],
                "verbatim_text": "Cláusula 2ª - Em caso de rescisão imotivada, multa de 10% sobre o saldo remanescente.",
                "uncertainty_flags": [],
            },
        ),
        Document(
            page_content="[Instrumento: Contrato de Locação Comercial - Edifício Central]\nLocatário: Beta Participações S.A. (99.888.777/0001-66)\n[Localização: Cláusula 1ª]\n\nCláusula 1ª - O valor mensal do aluguel comercial é fixado em R$ 15.000,00.",
            metadata={
                "chunk_id": "doc_202_v1_chunk_0",
                "doc_id": 202,
                "doc_version": 1,
                "formal_title": "Contrato de Locação Comercial",
                "instrument_type": "Contrato de Locação",
                "clean_identifiers": ["99888777000166"],
                "verbatim_text": "Cláusula 1ª - O valor mensal do aluguel comercial é fixado em R$ 15.000,00.",
                "uncertainty_flags": [],
            },
        ),
        Document(
            page_content="[Instrumento: Instrumento Particular de Confissão de Dívida]\nDevedor: João da Silva (123.456.789-01)\n[Localização: Cláusula 3ª]\n\nCláusula 3ª - O devedor confessa a dívida líquida de R$ 50.000,00 com vencimento em 30 dias.",
            metadata={
                "chunk_id": "doc_303_v1_chunk_0",
                "doc_id": 303,
                "doc_version": 1,
                "formal_title": "Termo de Confissão de Dívida",
                "instrument_type": "Confissão de Dívida",
                "clean_identifiers": ["12345678901"],
                "verbatim_text": "Cláusula 3ª - O devedor confessa a dívida líquida de R$ 50.000,00 com vencimento em 30 dias.",
                "uncertainty_flags": ["review_needed"],
            },
        ),
    ]


def test_extract_clean_identifiers_from_query():
    """Verify CNPJ and CPF regex detection with formatted and raw inputs."""
    # CNPJ formatted
    ids = extract_clean_identifiers_from_query("Qual a multa no CNPJ 11.222.333/0001-44?")
    assert ids == ["11222333000144"]

    # CNPJ raw digits
    ids = extract_clean_identifiers_from_query("Buscar contrato 11222333000144 honorarios")
    assert ids == ["11222333000144"]

    # CPF formatted
    ids = extract_clean_identifiers_from_query("Qual a divida do CPF 123.456.789-01?")
    assert ids == ["12345678901"]

    # CPF raw digits
    ids = extract_clean_identifiers_from_query("Consultar devedor 12345678901 vencimento")
    assert ids == ["12345678901"]

    # No identifier
    ids = extract_clean_identifiers_from_query("Qual o prazo padrão para rescisão de contratos?")
    assert ids == []

    # Non-CPF/CNPJ numbers (e.g. laws, dates, values)
    ids = extract_clean_identifiers_from_query("Lei nº 11.101/2005 e R$ 15.000,00 em 2026")
    assert ids == []


def test_build_and_save_hybrid_index_creates_files(tmp_path: Path, sample_legal_chunks: list[Document]):
    """Verify atomic hybrid index creation stores ChromaDB and BM25 files."""
    gen_id = "test_gen_v1"
    retriever = build_and_save_hybrid_index(
        chunks=sample_legal_chunks,
        generation_id=gen_id,
        base_dir=tmp_path,
    )

    assert isinstance(retriever, LegalHybridRetriever)
    chroma_dir = tmp_path / gen_id / "chroma"
    bm25_file = tmp_path / gen_id / "bm25.joblib"

    assert chroma_dir.is_dir()
    assert bm25_file.is_file()
    assert (chroma_dir / "chroma.sqlite3").is_file()


def test_exact_cnpj_prefilter_prevents_client_cross_contamination(
    tmp_path: Path,
    sample_legal_chunks: list[Document],
):
    """When query specifies a CNPJ, only documents for that CNPJ are returned."""
    retriever = build_and_save_hybrid_index(
        chunks=sample_legal_chunks,
        generation_id="gen_cnpj_test",
        base_dir=tmp_path,
    )

    # Query targeting Alpha (11.222.333/0001-44)
    results = retriever.invoke("Qual o percentual de honorários do CNPJ 11.222.333/0001-44?")
    assert len(results) >= 1
    # All returned chunks must belong strictly to Alpha
    for doc in results:
        assert "11222333000144" in doc.metadata["clean_identifiers"]
        assert "99888777000166" not in doc.metadata.get("clean_identifiers", [])

    # The most relevant chunk for honorários should be chunk 0
    assert results[0].metadata["chunk_id"] == "doc_101_v1_chunk_0"
    assert "honorários advocatícios serão de 20%" in results[0].metadata["verbatim_text"]


def test_exact_cpf_prefilter_returns_only_matching_debtor(
    tmp_path: Path,
    sample_legal_chunks: list[Document],
):
    """When query specifies a CPF, only documents for that individual are returned."""
    retriever = build_and_save_hybrid_index(
        chunks=sample_legal_chunks,
        generation_id="gen_cpf_test",
        base_dir=tmp_path,
    )

    results = retriever.invoke("Qual o valor confessado pelo devedor CPF 123.456.789-01?")
    assert len(results) == 1
    assert results[0].metadata["chunk_id"] == "doc_303_v1_chunk_0"
    assert results[0].metadata["clean_identifiers"] == ["12345678901"]
    assert "R$ 50.000,00" in results[0].metadata["verbatim_text"]


def test_nonexistent_identifier_returns_empty_results(
    tmp_path: Path,
    sample_legal_chunks: list[Document],
):
    """If a CNPJ is queried that does not exist in the corpus, returns empty list (zero contamination)."""
    retriever = build_and_save_hybrid_index(
        chunks=sample_legal_chunks,
        generation_id="gen_empty_test",
        base_dir=tmp_path,
    )

    results = retriever.invoke("Contrato do CNPJ 00.111.222/0001-33 rescisão")
    assert results == []


def test_generic_query_executes_hybrid_rrf_with_deterministic_tie_breaking(
    tmp_path: Path,
    sample_legal_chunks: list[Document],
):
    """Thematic query without identifier executes BM25 + Chroma hybrid search."""
    retriever = build_and_save_hybrid_index(
        chunks=sample_legal_chunks,
        generation_id="gen_generic_test",
        base_dir=tmp_path,
    )

    # Generic search for aluguel comercial
    results = retriever.invoke("Qual o valor do aluguel comercial mensal?")
    assert len(results) >= 1
    # Top result should be the lease chunk
    assert results[0].metadata["chunk_id"] == "doc_202_v1_chunk_0"
    assert "15.000,00" in results[0].metadata["verbatim_text"]


def test_metadata_and_verbatim_text_preservation(
    tmp_path: Path,
    sample_legal_chunks: list[Document],
):
    """Every returned document must preserve chunk_id, verbatim_text, and formal_title intact."""
    retriever = build_and_save_hybrid_index(
        chunks=sample_legal_chunks,
        generation_id="gen_meta_test",
        base_dir=tmp_path,
    )

    results = retriever.invoke("rescisão imotivada multa 11.222.333/0001-44")
    assert len(results) >= 1
    target = next((d for d in results if d.metadata["chunk_id"] == "doc_101_v1_chunk_1"), None)
    assert target is not None
    assert target.metadata["chunk_id"] == "doc_101_v1_chunk_1"
    assert target.metadata["verbatim_text"] == "Cláusula 2ª - Em caso de rescisão imotivada, multa de 10% sobre o saldo remanescente."
    assert target.metadata["formal_title"] == "Contrato de Prestação de Serviços Advocatícios"
    assert target.metadata["instrument_type"] == "Contrato de Prestação de Serviços"


def test_load_hybrid_retriever_from_disk(
    tmp_path: Path,
    sample_legal_chunks: list[Document],
):
    """load_hybrid_retriever loads existing indices and reproduces retrieval."""
    gen_id = "gen_persist_test"
    build_and_save_hybrid_index(
        chunks=sample_legal_chunks,
        generation_id=gen_id,
        base_dir=tmp_path,
    )

    # Reload from disk
    loaded_retriever = load_hybrid_retriever(
        generation_id=gen_id,
        base_dir=tmp_path,
    )
    assert isinstance(loaded_retriever, LegalHybridRetriever)

    # Execute search on reloaded retriever
    results = loaded_retriever.invoke("CNPJ 99.888.777/0001-66 aluguel")
    assert len(results) >= 1
    assert results[0].metadata["chunk_id"] == "doc_202_v1_chunk_0"
    assert results[0].metadata["clean_identifiers"] == ["99888777000166"]


def test_load_hybrid_retriever_nonexistent_directory_raises(tmp_path: Path):
    """Loading from a non-existent directory raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_hybrid_retriever(generation_id="nonexistent_gen", base_dir=tmp_path)


def test_build_index_with_empty_chunks_raises_value_error(tmp_path: Path):
    """Attempting to build an index with empty chunks list raises ValueError."""
    with pytest.raises(ValueError, match="vazia"):
        build_and_save_hybrid_index(chunks=[], generation_id="gen_empty", base_dir=tmp_path)


def test_chunk_missing_chunk_id_raises_value_error(tmp_path: Path):
    """A chunk without chunk_id violates the atomic contract and must raise ValueError."""
    bad_chunk = Document(page_content="teste sem chunk_id", metadata={})
    with pytest.raises(ValueError, match="carece de 'chunk_id'"):
        build_and_save_hybrid_index(chunks=[bad_chunk], generation_id="gen_bad", base_dir=tmp_path)


@pytest.mark.anyio
async def test_async_ainvoke_supported(tmp_path: Path, sample_legal_chunks: list[Document]):
    """LegalHybridRetriever supports async ainvoke execution."""
    retriever = build_and_save_hybrid_index(
        chunks=sample_legal_chunks,
        generation_id="gen_async_test",
        base_dir=tmp_path,
    )

    results = await retriever.ainvoke("Qual o valor do aluguel comercial?")
    assert len(results) >= 1
    assert results[0].metadata["chunk_id"] == "doc_202_v1_chunk_0"


def test_multiple_clean_identifiers_in_query(tmp_path: Path, sample_legal_chunks: list[Document]):
    """Query referencing multiple client identifiers matches both without leaking third parties."""
    retriever = build_and_save_hybrid_index(
        chunks=sample_legal_chunks,
        generation_id="gen_multi_id_test",
        base_dir=tmp_path,
    )

    results = retriever.invoke(
        "Comparar contratos 11.222.333/0001-44 e 99.888.777/0001-66"
    )
    assert len(results) >= 2
    matched_ids = {doc.metadata["chunk_id"] for doc in results}
    # Alpha and Beta should be present
    assert "doc_101_v1_chunk_0" in matched_ids or "doc_101_v1_chunk_1" in matched_ids
    assert "doc_202_v1_chunk_0" in matched_ids
    # Individual debtor João da Silva (CPF 12345678901) must NOT be present
    assert "doc_303_v1_chunk_0" not in matched_ids


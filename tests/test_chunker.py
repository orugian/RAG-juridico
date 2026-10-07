"""
Tests for Legal Hierarchical Chunker (app.ingestion.chunker).

Validates:
- Strict preservation of metadata["verbatim_text"] without synthetic search header.
- Injection of formal instrument, qualified parties, and location into page_content.
- Extraction and deduplication of clean_identifiers for lexical pre-filtering.
- Subordinate paragraph and item grouping under parent clauses.
- Inaugural chunk generation (TITLE + PREAMBLE).
- Final signature chunk generation (SIGNATURE).
- Filtering out documents without status == "success" (quarantine and failed).
- Roundtrip integrity and schema adherence of LangChain Document instances.
"""
import logging
import pytest
from langchain_core.documents import Document

from app.ingestion.chunker import create_candidate_chunks as create_legal_chunks
from app.ingestion.schemas import (
    BlockType,
    ContractMetadata,
    ContractParty,
    DocumentBlock,
    HierarchyLevel,
    ParsedDocument,
    PartyRole,
    UncertaintyFlag,
)


def test_chunker_injects_reconciled_header_and_preserves_verbatim_text():
    """Verify search header injection in page_content and strict verbatim_text segregation in metadata."""
    parties = [
        ContractParty(
            name="Andrade Advogados",
            role=PartyRole.CONTRATADA,
            clean_identifier="11222333000144",
            is_law_firm=True,
        ),
        ContractParty(
            name="Tech Soluções",
            role=PartyRole.CONTRATANTE,
            clean_identifier="55666777000188",
        ),
    ]
    meta = ContractMetadata(
        formal_title="CONTRATO DE HONORÁRIOS",
        instrument_type="Honorários",
        subject_area="Contencioso Cível",
        parties=parties,
    )
    raw_clause = "Cláusula 3ª - O valor mensal fixo será de R$ 15.000,00 (quinze mil reais)."
    blocks = [
        DocumentBlock(
            block_id="b1",
            doc_id=50,
            doc_version=1,
            block_type=BlockType.CLAUSE,
            hierarchy_level=HierarchyLevel.CLAUSE,
            hierarchy_label="Cláusula 3ª",
            order_index=1,
            text_raw=raw_clause,
            text_search=raw_clause,
        )
    ]
    doc = ParsedDocument(
        doc_id=50,
        doc_version=1,
        file_path="c.docx",
        file_hash="h50",
        parser_name="docx",
        parser_version="1",
        metadata=meta,
        blocks=blocks,
        status="success",
    )

    chunks = create_legal_chunks([doc])
    assert len(chunks) == 1
    chunk = chunks[0]

    # Verify LangChain Document instance
    assert isinstance(chunk, Document)

    # page_content contains synthetic search header
    assert "[Instrumento: Honorários - CONTRATO DE HONORÁRIOS]" in chunk.page_content
    assert "Contratada: Andrade Advogados (11222333000144)" in chunk.page_content
    assert "Contratante: Tech Soluções (55666777000188)" in chunk.page_content
    assert "[Localização: Cláusula 3ª]" in chunk.page_content
    assert raw_clause in chunk.page_content

    # verbatim_text is STRICTLY the pure raw clause without synthetic search header
    assert chunk.metadata["verbatim_text"] == raw_clause
    assert "[Instrumento:" not in chunk.metadata["verbatim_text"]
    assert "[Localização:" not in chunk.metadata["verbatim_text"]

    # Metadata attributes
    assert "11222333000144" in chunk.metadata["clean_identifiers"]
    assert "55666777000188" in chunk.metadata["clean_identifiers"]
    assert chunk.metadata["chunk_id"] == "doc_50_v1_chunk_0"
    assert chunk.metadata["doc_id"] == 50
    assert chunk.metadata["doc_version"] == 1
    assert chunk.metadata["file_path"] == "c.docx"
    assert chunk.metadata["formal_title"] == "CONTRATO DE HONORÁRIOS"
    assert chunk.metadata["instrument_type"] == "Honorários"
    assert chunk.metadata["subject_area"] == "Contencioso Cível"
    assert chunk.metadata["hierarchy_label"] == "Cláusula 3ª"
    assert chunk.metadata["block_type"] == "clause"
    assert chunk.metadata["uncertainty_flags"] == []


def test_chunker_groups_subordinate_paragraphs_under_parent_clause():
    """Verify subordinate paragraphs and items are grouped into parent clause chunk."""
    meta = ContractMetadata(
        formal_title="CONTRATO DE PRESTAÇÃO DE SERVIÇOS",
        instrument_type="Prestação de Serviços",
        parties=[
            ContractParty(name="Empresa A", role=PartyRole.CONTRATANTE, clean_identifier="11111111000111"),
            ContractParty(name="Empresa B", role=PartyRole.CONTRATADA, clean_identifier="22222222000122"),
        ],
    )
    clause_text = "Cláusula 1ª - Do Objeto. O presente contrato tem por objeto a prestação de assessoria."
    p1_text = "§ 1º - Os serviços incluem elaboração de pareceres e acompanhamento processual."
    p2_text = "§ 2º - Fica expressamente vedada a subcontratação de terceiros sem anuência prévia."
    item_text = "a) Relatórios mensais de atividades deverão ser enviados até o quinto dia útil."

    blocks = [
        DocumentBlock(
            block_id="b1",
            doc_id=10,
            doc_version=1,
            block_type=BlockType.CLAUSE,
            hierarchy_level=HierarchyLevel.CLAUSE,
            hierarchy_label="Cláusula 1ª",
            order_index=1,
            text_raw=clause_text,
            text_search=clause_text,
        ),
        DocumentBlock(
            block_id="b2",
            doc_id=10,
            doc_version=1,
            block_type=BlockType.PARAGRAPH,
            hierarchy_level=HierarchyLevel.PARAGRAPH,
            hierarchy_label="§ 1º",
            parent_clause_id="b1",
            order_index=2,
            text_raw=p1_text,
            text_search=p1_text,
        ),
        DocumentBlock(
            block_id="b3",
            doc_id=10,
            doc_version=1,
            block_type=BlockType.PARAGRAPH,
            hierarchy_level=HierarchyLevel.PARAGRAPH,
            hierarchy_label="§ 2º",
            parent_clause_id="b1",
            order_index=3,
            text_raw=p2_text,
            text_search=p2_text,
        ),
        DocumentBlock(
            block_id="b4",
            doc_id=10,
            doc_version=1,
            block_type=BlockType.ITEM,
            hierarchy_level=HierarchyLevel.ITEM,
            hierarchy_label="Item a",
            parent_clause_id=None,  # Contiguous subordinate order
            order_index=4,
            text_raw=item_text,
            text_search=item_text,
        ),
    ]
    doc = ParsedDocument(
        doc_id=10,
        doc_version=1,
        file_path="servicos.docx",
        file_hash="h10",
        parser_name="docx",
        parser_version="1",
        metadata=meta,
        blocks=blocks,
        status="success",
    )

    chunks = create_legal_chunks([doc])
    assert len(chunks) == 1

    chunk = chunks[0]
    expected_verbatim = f"{clause_text}\n\n{p1_text}\n\n{p2_text}\n\n{item_text}"
    assert chunk.metadata["verbatim_text"] == expected_verbatim
    assert chunk.metadata["hierarchy_label"] == "Cláusula 1ª"
    assert chunk.metadata["block_type"] == "clause"

    # Search header in page_content
    assert "[Instrumento: Prestação de Serviços - CONTRATO DE PRESTAÇÃO DE SERVIÇOS]" in chunk.page_content
    assert "[Localização: Cláusula 1ª]" in chunk.page_content
    assert expected_verbatim in chunk.page_content


def test_chunker_inaugural_chunk_preamble_and_title():
    """Verify TITLE and PREAMBLE form an inaugural contextual chunk."""
    meta = ContractMetadata(
        formal_title="ACORDO DE CONFIDENCIALIDADE",
        instrument_type="NDA",
        parties=[
            ContractParty(name="Reveladora S.A.", role=PartyRole.PARTE_GENERICA, clean_identifier="12345678000199"),
            ContractParty(name="Receptora Ltda.", role=PartyRole.PARTE_GENERICA, clean_identifier="98765432000111"),
        ],
    )
    title_text = "ACORDO DE CONFIDENCIALIDADE E NÃO DIVULGAÇÃO"
    preamble_text = "Pelo presente instrumento, de um lado Reveladora S.A., e de outro Receptora Ltda., resolvem celebrar o presente acordo."
    clause_text = "Cláusula 1ª - Das Informações Confidenciais. Consideram-se confidenciais todos os dados."

    blocks = [
        DocumentBlock(
            block_id="b0",
            doc_id=20,
            doc_version=1,
            block_type=BlockType.TITLE,
            hierarchy_level=HierarchyLevel.TITLE,
            order_index=0,
            text_raw=title_text,
            text_search=title_text,
        ),
        DocumentBlock(
            block_id="b1",
            doc_id=20,
            doc_version=1,
            block_type=BlockType.PREAMBLE,
            hierarchy_level=HierarchyLevel.PREAMBLE,
            order_index=1,
            text_raw=preamble_text,
            text_search=preamble_text,
        ),
        DocumentBlock(
            block_id="b2",
            doc_id=20,
            doc_version=1,
            block_type=BlockType.CLAUSE,
            hierarchy_level=HierarchyLevel.CLAUSE,
            hierarchy_label="Cláusula 1ª",
            order_index=2,
            text_raw=clause_text,
            text_search=clause_text,
        ),
    ]
    doc = ParsedDocument(
        doc_id=20,
        doc_version=1,
        file_path="nda.pdf",
        file_hash="h20",
        parser_name="pdf",
        parser_version="1",
        metadata=meta,
        blocks=blocks,
        status="success",
    )

    chunks = create_legal_chunks([doc])
    assert len(chunks) == 2

    # Chunk 0: Inaugural chunk
    inaugural_chunk = chunks[0]
    assert inaugural_chunk.metadata["block_type"] == "preamble"
    assert inaugural_chunk.metadata["chunk_id"] == "doc_20_v1_chunk_0"
    assert f"{title_text}\n\n{preamble_text}" == inaugural_chunk.metadata["verbatim_text"]
    assert "[Localização: Preâmbulo]" in inaugural_chunk.page_content

    # Chunk 1: Clause chunk
    clause_chunk = chunks[1]
    assert clause_chunk.metadata["block_type"] == "clause"
    assert clause_chunk.metadata["chunk_id"] == "doc_20_v1_chunk_1"
    assert clause_chunk.metadata["verbatim_text"] == clause_text
    assert "[Localização: Cláusula 1ª]" in clause_chunk.page_content


def test_chunker_signature_final_chunk():
    """Verify SIGNATURE blocks are grouped into a final signature chunk."""
    meta = ContractMetadata(
        formal_title="CONTRATO DE LOCAÇÃO",
        instrument_type="Locação",
        parties=[
            ContractParty(name="Imobiliária Central", role=PartyRole.LOCADOR, clean_identifier="33444555000166"),
            ContractParty(name="João da Silva", role=PartyRole.LOCATARIO, clean_identifier="12345678909"),
        ],
    )
    c1_text = "Cláusula 1ª - O aluguel mensal será de R$ 3.000,00."
    sig1_text = "São Paulo/SP, 15 de janeiro de 2025."
    sig2_text = "Imobiliária Central (Locador) / João da Silva (Locatário)"
    sig3_text = "Testemunhas: 1. Maria Santos (CPF 111.222.333-44) 2. Pedro Lima (CPF 555.666.777-88)"

    blocks = [
        DocumentBlock(
            block_id="b1",
            doc_id=30,
            doc_version=1,
            block_type=BlockType.CLAUSE,
            hierarchy_level=HierarchyLevel.CLAUSE,
            hierarchy_label="Cláusula 1ª",
            order_index=1,
            text_raw=c1_text,
            text_search=c1_text,
        ),
        DocumentBlock(
            block_id="b2",
            doc_id=30,
            doc_version=1,
            block_type=BlockType.SIGNATURE,
            hierarchy_level=HierarchyLevel.SIGNATURE,
            order_index=2,
            text_raw=sig1_text,
            text_search=sig1_text,
        ),
        DocumentBlock(
            block_id="b3",
            doc_id=30,
            doc_version=1,
            block_type=BlockType.SIGNATURE,
            hierarchy_level=HierarchyLevel.SIGNATURE,
            order_index=3,
            text_raw=sig2_text,
            text_search=sig2_text,
        ),
        DocumentBlock(
            block_id="b4",
            doc_id=30,
            doc_version=1,
            block_type=BlockType.SIGNATURE,
            hierarchy_level=HierarchyLevel.SIGNATURE,
            order_index=4,
            text_raw=sig3_text,
            text_search=sig3_text,
        ),
    ]
    doc = ParsedDocument(
        doc_id=30,
        doc_version=1,
        file_path="locacao.docx",
        file_hash="h30",
        parser_name="docx",
        parser_version="1",
        metadata=meta,
        blocks=blocks,
        status="success",
    )

    chunks = create_legal_chunks([doc])
    assert len(chunks) == 2

    clause_chunk = chunks[0]
    assert clause_chunk.metadata["block_type"] == "clause"

    sig_chunk = chunks[1]
    assert sig_chunk.metadata["block_type"] == "signature"
    assert sig_chunk.metadata["chunk_id"] == "doc_30_v1_chunk_1"
    assert "[Localização: Assinaturas]" in sig_chunk.page_content
    expected_sig_verbatim = f"{sig1_text}\n\n{sig2_text}\n\n{sig3_text}"
    assert sig_chunk.metadata["verbatim_text"] == expected_sig_verbatim


def test_chunker_filters_non_success_documents(caplog):
    """Verify quarantine and failed documents are strictly ignored with warning log."""
    meta = ContractMetadata(
        formal_title="CONTRATO X",
        instrument_type="Acordo",
        parties=[ContractParty(name="Parte A", clean_identifier="11223344000155")],
    )
    raw_block = DocumentBlock(
        block_id="b1",
        doc_id=1,
        doc_version=1,
        block_type=BlockType.CLAUSE,
        hierarchy_level=HierarchyLevel.CLAUSE,
        order_index=1,
        text_raw="Cláusula 1ª - Validade.",
        text_search="Cláusula 1ª - Validade.",
    )

    doc_quarantine = ParsedDocument(
        doc_id=101,
        doc_version=1,
        file_path="quarantine.docx",
        file_hash="hq",
        parser_name="docx",
        parser_version="1",
        metadata=meta,
        blocks=[raw_block],
        status="review_metadata_mismatch",
    )
    doc_failed = ParsedDocument(
        doc_id=102,
        doc_version=1,
        file_path="failed.docx",
        file_hash="hf",
        parser_name="docx",
        parser_version="1",
        metadata=meta,
        blocks=[raw_block],
        status="failed",
    )
    doc_success = ParsedDocument(
        doc_id=103,
        doc_version=1,
        file_path="success.docx",
        file_hash="hs",
        parser_name="docx",
        parser_version="1",
        metadata=meta,
        blocks=[raw_block],
        status="success",
    )

    with caplog.at_level(logging.WARNING):
        chunks = create_legal_chunks([doc_quarantine, doc_failed, doc_success])

    assert len(chunks) == 1
    assert chunks[0].metadata["doc_id"] == 103

    # Check warnings were logged for non-success documents
    warning_messages = [record.message for record in caplog.records if record.levelno == logging.WARNING]
    assert any("101" in msg for msg in warning_messages)
    assert any("102" in msg for msg in warning_messages)


def test_chunker_collects_clean_identifiers_and_uncertainty_flags():
    """Verify clean_identifiers are deduplicated and uncertainty_flags are aggregated as strings."""
    parties = [
        ContractParty(name="Empresa Alfa", role=PartyRole.CONTRATANTE, clean_identifier="11111111000111"),
        ContractParty(name="Empresa Beta", role=PartyRole.CONTRATADA, clean_identifier="22222222000122"),
        ContractParty(name="Empresa Gama", role=PartyRole.ANUENTE, clean_identifier="11111111000111"),  # Duplicate
        ContractParty(name="Testemunha sem CNPJ", role=PartyRole.PARTE_GENERICA, clean_identifier=None),
    ]
    meta = ContractMetadata(
        formal_title="CONTRATO COMPLEXO",
        instrument_type="Complexo",
        parties=parties,
    )
    blocks = [
        DocumentBlock(
            block_id="b1",
            doc_id=40,
            doc_version=1,
            block_type=BlockType.CLAUSE,
            hierarchy_level=HierarchyLevel.CLAUSE,
            hierarchy_label="Cláusula 1ª",
            order_index=1,
            text_raw="Cláusula 1ª - Cláusula com revisão pendente.",
            text_search="Cláusula 1ª - Cláusula com revisão pendente.",
            uncertainty_flags=[UncertaintyFlag.TRACK_CHANGES_PRESENT],
        ),
        DocumentBlock(
            block_id="b2",
            doc_id=40,
            doc_version=1,
            block_type=BlockType.PARAGRAPH,
            hierarchy_level=HierarchyLevel.PARAGRAPH,
            hierarchy_label="§ 1º",
            parent_clause_id="b1",
            order_index=2,
            text_raw="§ 1º - OCR de baixa confiança e comentários.",
            text_search="§ 1º - OCR de baixa confiança e comentários.",
            uncertainty_flags=[UncertaintyFlag.LOW_CONFIDENCE_OCR, UncertaintyFlag.HAS_COMMENTS],
        ),
    ]
    doc = ParsedDocument(
        doc_id=40,
        doc_version=1,
        file_path="complex.docx",
        file_hash="h40",
        parser_name="docx",
        parser_version="1",
        metadata=meta,
        blocks=blocks,
        status="success",
    )

    chunks = create_legal_chunks([doc])
    assert len(chunks) == 1
    chunk = chunks[0]

    # Clean identifiers deduplicated and only digits
    assert chunk.metadata["clean_identifiers"] == ["11111111000111", "22222222000122"]

    # Uncertainty flags aggregated as strings
    flags = chunk.metadata["uncertainty_flags"]
    assert "track_changes_present" in flags
    assert "low_confidence_ocr" in flags
    assert "has_comments" in flags
    assert len(flags) == 3


def test_chunker_handles_standalone_table_and_annex():
    """Verify autonomous TABLE and ANNEX blocks produce correct chunks and block_type."""
    meta = ContractMetadata(
        formal_title="CONTRATO COM ANEXO E TABELA",
        instrument_type="Comercial",
        parties=[ContractParty(name="Parte Única", clean_identifier="55555555000155")],
    )
    blocks = [
        DocumentBlock(
            block_id="t1",
            doc_id=60,
            doc_version=1,
            block_type=BlockType.TABLE,
            hierarchy_level=HierarchyLevel.TABLE,
            hierarchy_label="Tabela de Preços",
            order_index=1,
            text_raw="Item | Quantidade | Preço\nA | 10 | R$ 100",
            text_search="Item Quantidade Preço A 10 100",
        ),
        DocumentBlock(
            block_id="a1",
            doc_id=60,
            doc_version=1,
            block_type=BlockType.ANNEX,
            hierarchy_level=HierarchyLevel.ANNEX,
            hierarchy_label="Anexo I",
            order_index=2,
            text_raw="ANEXO I - Cronograma de Entregas e Prazos.",
            text_search="ANEXO I Cronograma de Entregas e Prazos.",
        ),
    ]
    doc = ParsedDocument(
        doc_id=60,
        doc_version=1,
        file_path="anexos.docx",
        file_hash="h60",
        parser_name="docx",
        parser_version="1",
        metadata=meta,
        blocks=blocks,
        status="success",
    )

    chunks = create_legal_chunks([doc])
    assert len(chunks) == 2

    # Table chunk
    assert chunks[0].metadata["block_type"] == "table"
    assert chunks[0].metadata["hierarchy_label"] == "Tabela de Preços"
    assert "[Localização: Tabela de Preços]" in chunks[0].page_content

    # Annex chunk
    assert chunks[1].metadata["block_type"] == "annex"
    assert chunks[1].metadata["hierarchy_label"] == "Anexo I"
    assert "[Localização: Anexo I]" in chunks[1].page_content


def test_chunker_empty_input():
    """Verify empty document list returns empty chunks."""
    assert create_legal_chunks([]) == []


def test_chunker_metadata_schema_adherence_and_types():
    """Verify all 12 mandatory metadata keys are present and have correct types."""
    meta = ContractMetadata(
        formal_title="CONTRATO PADRÃO",
        instrument_type="Honorários",
        subject_area="Trabalhista",
        parties=[
            ContractParty(name="Advocacia X", role=PartyRole.CONTRATADA, clean_identifier="12345678000100"),
        ],
    )
    blocks = [
        DocumentBlock(
            block_id="b1",
            doc_id=70,
            doc_version=2,
            block_type=BlockType.CLAUSE,
            hierarchy_level=HierarchyLevel.CLAUSE,
            hierarchy_label="Cláusula 1ª",
            order_index=1,
            text_raw="Cláusula 1ª - Disposições gerais e vigência.",
            text_search="Cláusula 1ª - Disposições gerais e vigência.",
        )
    ]
    doc = ParsedDocument(
        doc_id=70,
        doc_version=2,
        file_path="padrao.docx",
        file_hash="h70",
        parser_name="docx",
        parser_version="1",
        metadata=meta,
        blocks=blocks,
        status="success",
    )

    chunks = create_legal_chunks([doc])
    assert len(chunks) == 1
    m = chunks[0].metadata

    # 12 mandatory keys
    mandatory_keys = {
        "chunk_id",
        "doc_id",
        "doc_version",
        "file_path",
        "formal_title",
        "instrument_type",
        "subject_area",
        "clean_identifiers",
        "hierarchy_label",
        "block_type",
        "verbatim_text",
        "uncertainty_flags",
    }
    assert mandatory_keys.issubset(m.keys())

    assert isinstance(m["chunk_id"], str)
    assert isinstance(m["doc_id"], int)
    assert isinstance(m["doc_version"], int)
    assert isinstance(m["file_path"], str)
    assert isinstance(m["formal_title"], str)
    assert isinstance(m["instrument_type"], str)
    assert isinstance(m["subject_area"], str)
    assert isinstance(m["clean_identifiers"], list)
    assert isinstance(m["hierarchy_label"], str)
    assert isinstance(m["block_type"], str)
    assert isinstance(m["verbatim_text"], str)
    assert isinstance(m["uncertainty_flags"], list)


def test_chunker_batch_multiple_documents():
    """Verify batch processing of multiple documents produces sequential chunk_ids per document."""
    meta1 = ContractMetadata(
        formal_title="CONTRATO 1",
        instrument_type="Prestação",
        parties=[ContractParty(name="Parte 1", clean_identifier="11111111000111")],
    )
    doc1 = ParsedDocument(
        doc_id=1,
        doc_version=1,
        file_path="doc1.docx",
        file_hash="h1",
        parser_name="docx",
        parser_version="1",
        metadata=meta1,
        blocks=[
            DocumentBlock(
                block_id="b1_1", doc_id=1, doc_version=1, block_type=BlockType.CLAUSE,
                hierarchy_level=HierarchyLevel.CLAUSE, hierarchy_label="Cláusula 1ª",
                order_index=1, text_raw="Texto do doc 1.", text_search="Texto do doc 1.",
            )
        ],
        status="success",
    )

    meta2 = ContractMetadata(
        formal_title="CONTRATO 2",
        instrument_type="Honorários",
        parties=[ContractParty(name="Parte 2", clean_identifier="22222222000122")],
    )
    doc2 = ParsedDocument(
        doc_id=2,
        doc_version=1,
        file_path="doc2.docx",
        file_hash="h2",
        parser_name="docx",
        parser_version="1",
        metadata=meta2,
        blocks=[
            DocumentBlock(
                block_id="b2_1", doc_id=2, doc_version=1, block_type=BlockType.CLAUSE,
                hierarchy_level=HierarchyLevel.CLAUSE, hierarchy_label="Cláusula 1ª",
                order_index=1, text_raw="Texto do doc 2.", text_search="Texto do doc 2.",
            )
        ],
        status="success",
    )

    chunks = create_legal_chunks([doc1, doc2])
    assert len(chunks) == 2
    assert chunks[0].metadata["chunk_id"] == "doc_1_v1_chunk_0"
    assert chunks[0].metadata["clean_identifiers"] == ["11111111000111"]
    assert chunks[1].metadata["chunk_id"] == "doc_2_v1_chunk_0"
    assert chunks[1].metadata["clean_identifiers"] == ["22222222000122"]


def test_chunker_parties_without_identifiers():
    """Verify header formatting when parties have no CNPJ/CPF."""
    meta = ContractMetadata(
        formal_title="TERMO DE ACORDO",
        instrument_type="Acordo",
        parties=[
            ContractParty(name="Fulano", role=PartyRole.CONTRATANTE),
            ContractParty(name="Beltrano", role=PartyRole.CONTRATADA, raw_identifier="RG 123.456-SSP/SP"),
        ],
    )
    blocks = [
        DocumentBlock(
            block_id="b1", doc_id=80, doc_version=1, block_type=BlockType.CLAUSE,
            hierarchy_level=HierarchyLevel.CLAUSE, hierarchy_label="Cláusula Única",
            order_index=1, text_raw="Acordo amigável.", text_search="Acordo amigável.",
        )
    ]
    doc = ParsedDocument(
        doc_id=80, doc_version=1, file_path="acordo.docx", file_hash="h80",
        parser_name="docx", parser_version="1", metadata=meta, blocks=blocks, status="success",
    )

    chunks = create_legal_chunks([doc])
    assert len(chunks) == 1
    content = chunks[0].page_content
    assert "Contratante: Fulano\n" in content
    assert "Contratada: Beltrano (RG 123.456-SSP/SP)\n" in content
    assert chunks[0].metadata["clean_identifiers"] == []


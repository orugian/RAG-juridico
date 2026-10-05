"""
Unit tests for app.ingestion.parsing_pipeline.

Covers:
- Reconciliador Documental (reconcile_document):
  - Client mismatch quarantine (DISCREPANCY_CLIENT_MISMATCH -> review_metadata_mismatch)
  - Client match preservation (flat and nested properties)
  - CNPJ and corporate suffix normalization matching
  - Instrument type refinement (FLAG_INSTRUMENT_TYPE_REFINED)
  - Missing parties in bilateral contract (FLAG_NO_PARTIES_FOUND)
  - Unilateral instruments without parties
  - Failed document preservation
- Orquestrador de Parsing Unificado (parse_single_document):
  - DOCX route extraction, sha256 calculation, and reconciliation
  - PDF route extraction and reconciliation
  - Legacy (.doc, .rtf, .wpd) route routing
  - Corrupted, empty, or missing file defensive error handling (status="failed")
  - Unsupported file extension handling
- Batch Parsing Pipeline (run_parsing_pipeline):
  - Processing list of manifest/curation records against raw_dir
  - Heterogeneous batch with success, quarantine, and failed items
"""

import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.ingestion.parsing_pipeline import (
    parse_single_document,
    reconcile_document,
    run_parsing_pipeline,
)
from app.ingestion.schemas import (
    BlockType,
    ContractMetadata,
    ContractParty,
    DocumentBlock,
    HierarchyLevel,
    ParsedDocument,
    PartyRole,
)


# ============================================================================
# Helpers
# ============================================================================


def _create_sample_block(
    block_id: str = "b1",
    doc_id: int = 101,
    doc_version: int = 1,
    block_type: BlockType = BlockType.CLAUSE,
    text: str = "Texto da cláusula",
) -> DocumentBlock:
    return DocumentBlock(
        block_id=block_id,
        doc_id=doc_id,
        doc_version=doc_version,
        block_type=block_type,
        hierarchy_level=HierarchyLevel.CLAUSE,
        order_index=1,
        text_raw=text,
        text_search=text,
    )


def _build_docx(
    tmp_path: Path,
    filename: str,
    body_paragraphs: list[str],
) -> Path:
    docx_path = tmp_path / filename
    p_xml = "".join(
        f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in body_paragraphs
    )
    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
        <w:body>
            {p_xml}
        </w:body>
    </w:document>"""

    with zipfile.ZipFile(docx_path, "w") as zf:
        zf.writestr("word/document.xml", document_xml)
        zf.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
    <Default Extension="xml" ContentType="application/xml"/>
</Types>""",
        )
    return docx_path


def _create_synthetic_pdf(pages_lines: list[list[str]]) -> bytes:
    """Generate a valid PDF byte string with 1 or more pages of text (matches test_pdf_parser)."""
    page_count = len(pages_lines)
    objects = []

    # Obj 1: Catalog
    objects.append("1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj")

    # Obj 2: Pages
    kids_refs = " ".join(f"{3 + 2 * i} 0 R" for i in range(page_count))
    objects.append(f"2 0 obj << /Type /Pages /Kids [{kids_refs}] /Count {page_count} >> endobj")

    font_obj_id = 3 + 2 * page_count

    for i, lines in enumerate(pages_lines):
        page_id = 3 + 2 * i
        content_id = 4 + 2 * i

        stream = "BT /F1 12 Tf 50 750 Td 15 TL\n"
        for line in lines:
            esc = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream += f"({esc}) '\n"
        stream += "ET"
        stream_bytes = stream.encode("latin1")

        objects.append(
            f"{page_id} 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Contents {content_id} 0 R /Resources << /Font << /F1 {font_obj_id} 0 R >> >> >> endobj"
        )
        objects.append(
            f"{content_id} 0 obj << /Length {len(stream_bytes)} >>\nstream\n{stream}\nendstream\nendobj"
        )

    objects.append(f"{font_obj_id} 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj")

    body = "\n".join(objects) + "\n"
    header = "%PDF-1.4\n"
    pdf_text = header + body
    xref_offset = len(pdf_text.encode("latin1"))

    total_objs = font_obj_id + 1
    xref = f"xref\n0 {total_objs}\n0000000000 65535 f \n"

    cur_offset = len(header.encode("latin1"))
    for obj_str in objects:
        xref += f"{cur_offset:010d} 00000 n \n"
        cur_offset += len((obj_str + "\n").encode("latin1"))

    trailer = f"trailer << /Size {total_objs} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
    return (header + body + xref + trailer).encode("latin1")


def _build_pdf(tmp_path: Path, filename: str, lines: list[str]) -> Path:
    pdf_path = tmp_path / filename
    pdf_bytes = _create_synthetic_pdf([lines])
    pdf_path.write_bytes(pdf_bytes)
    return pdf_path


# ============================================================================
# Reconciler Unit Tests
# ============================================================================


def test_reconciler_flags_client_mismatch_and_quarantines():
    """M-Files reports a client not matching any contract party -> quarantine."""
    mfiles_record = {
        "mfiles_id": 999,
        "title": "Contrato Locacao.docx",
        "class_name": "Contrato",
        "properties": {"Cliente": "Banco Santander S/A"},
    }
    parties = [
        ContractParty(
            name="Imobiliária Alfa Ltda",
            role=PartyRole.LOCADOR,
            clean_identifier="11111111000100",
        ),
        ContractParty(
            name="Comércio Beta Ltda",
            role=PartyRole.LOCATARIO,
            clean_identifier="22222222000100",
        ),
    ]
    meta = ContractMetadata(
        formal_title="CONTRATO DE LOCAÇÃO",
        instrument_type="Locação",
        parties=parties,
    )
    blocks = [_create_sample_block(doc_id=999)]
    doc = ParsedDocument(
        doc_id=999,
        doc_version=1,
        file_path="c.docx",
        file_hash="h1",
        parser_name="docx",
        parser_version="1",
        metadata=meta,
        blocks=blocks,
    )

    reconciled = reconcile_document(mfiles_record, doc)
    assert reconciled.status == "review_metadata_mismatch"
    assert (
        "DISCREPANCY_CLIENT_MISMATCH"
        in reconciled.metadata.mfiles_divergence_flags
    )


def test_reconciler_client_match_preserves_success_flat_and_nested():
    """Client matching by substring/corporate normalization keeps status='success'."""
    # Test flat format
    mfiles_flat = {
        "mfiles_id": 101,
        "cliente": "Imobiliária Alfa Ltda",
        "class_name": "Locação",
        "title": "Locacao Alfa.docx",
    }
    parties = [
        ContractParty(
            name="Imobiliária Alfa Ltda",
            role=PartyRole.LOCADOR,
            clean_identifier="11111111000100",
        ),
        ContractParty(
            name="Comércio Beta Ltda",
            role=PartyRole.LOCATARIO,
            clean_identifier="22222222000100",
        ),
    ]
    meta = ContractMetadata(
        formal_title="CONTRATO DE LOCAÇÃO",
        instrument_type="Locação",
        parties=parties,
    )
    blocks = [_create_sample_block(doc_id=101)]
    doc = ParsedDocument(
        doc_id=101,
        doc_version=1,
        file_path="loc.docx",
        file_hash="h101",
        parser_name="docx_parser",
        parser_version="1.0.0",
        metadata=meta,
        blocks=blocks,
    )

    reconciled = reconcile_document(mfiles_flat, doc)
    assert reconciled.status == "success"
    assert (
        "DISCREPANCY_CLIENT_MISMATCH"
        not in reconciled.metadata.mfiles_divergence_flags
    )

    # Test nested properties format with corporate variation (e.g. S.A. vs S/A)
    mfiles_nested = {
        "mfiles_id": 101,
        "properties": {"Cliente": "Comércio Beta S.A."},
    }
    doc_nested = doc.model_copy(deep=True)
    reconciled_nested = reconcile_document(mfiles_nested, doc_nested)
    assert reconciled_nested.status == "success"
    assert (
        "DISCREPANCY_CLIENT_MISMATCH"
        not in reconciled_nested.metadata.mfiles_divergence_flags
    )


def test_reconciler_client_match_via_cnpj():
    """Client matches contract party via clean CNPJ even if text name varies."""
    mfiles_record = {
        "mfiles_id": 202,
        "properties": {
            "Cliente": "Alfa Negócios 11.222.333/0001-44",
            "CNPJ": "11.222.333/0001-44",
        },
    }
    parties = [
        ContractParty(
            name="Alfa Empreendimentos e Participações",
            role=PartyRole.CONTRATANTE,
            clean_identifier="11222333000144",
        )
    ]
    meta = ContractMetadata(
        formal_title="PRESTAÇÃO DE SERVIÇOS",
        instrument_type="Prestação de Serviços",
        parties=parties,
    )
    blocks = [_create_sample_block(doc_id=202)]
    doc = ParsedDocument(
        doc_id=202,
        doc_version=1,
        file_path="srv.docx",
        file_hash="h202",
        parser_name="docx_parser",
        parser_version="1.0.0",
        metadata=meta,
        blocks=blocks,
    )

    reconciled = reconcile_document(mfiles_record, doc)
    assert reconciled.status == "success"
    assert (
        "DISCREPANCY_CLIENT_MISMATCH"
        not in reconciled.metadata.mfiles_divergence_flags
    )


def test_reconciler_instrument_type_refinement():
    """Generic M-Files class (Contrato) refined by text (Aditivo) -> FLAG_INSTRUMENT_TYPE_REFINED."""
    mfiles_record = {
        "mfiles_id": 303,
        "class_name": "Contrato",
        "title": "Primeiro Termo Aditivo.docx",
        "cliente": "Empresa Alfa Ltda",
    }
    parties = [
        ContractParty(name="Empresa Alfa Ltda", role=PartyRole.CONTRATANTE)
    ]
    meta = ContractMetadata(
        formal_title="PRIMEIRO TERMO ADITIVO AO CONTRATO",
        instrument_type="Aditivo",
        parties=parties,
    )
    blocks = [_create_sample_block(doc_id=303)]
    doc = ParsedDocument(
        doc_id=303,
        doc_version=1,
        file_path="adit.docx",
        file_hash="h303",
        parser_name="docx_parser",
        parser_version="1.0.0",
        metadata=meta,
        blocks=blocks,
    )

    reconciled = reconcile_document(mfiles_record, doc)
    assert (
        "FLAG_INSTRUMENT_TYPE_REFINED"
        in reconciled.metadata.mfiles_divergence_flags
    )
    assert reconciled.metadata.instrument_type == "Aditivo"
    assert reconciled.status == "success"


def test_reconciler_no_parties_in_bilateral_contract_flags_and_quarantines():
    """Missing parties in bilateral contract flags FLAG_NO_PARTIES_FOUND and quarantines."""
    mfiles_record = {
        "mfiles_id": 404,
        "class_name": "Contrato",
        "title": "Contrato Sem Partes.docx",
    }
    meta = ContractMetadata(
        formal_title="CONTRATO DE PRESTAÇÃO DE SERVIÇOS",
        instrument_type="Prestação de Serviços",
        parties=[],
    )
    blocks = [_create_sample_block(doc_id=404)]
    doc = ParsedDocument(
        doc_id=404,
        doc_version=1,
        file_path="noparties.docx",
        file_hash="h404",
        parser_name="docx_parser",
        parser_version="1.0.0",
        metadata=meta,
        blocks=blocks,
    )

    reconciled = reconcile_document(mfiles_record, doc)
    assert (
        "FLAG_NO_PARTIES_FOUND" in reconciled.metadata.mfiles_divergence_flags
    )
    assert reconciled.status == "review_metadata_mismatch"


def test_reconciler_unilateral_instrument_without_parties_no_quarantine():
    """Unilateral instrument (Declaração) without parties does not trigger FLAG_NO_PARTIES_FOUND."""
    mfiles_record = {
        "mfiles_id": 505,
        "class_name": "Declaração",
        "title": "Declaracao Residencia.docx",
    }
    meta = ContractMetadata(
        formal_title="DECLARAÇÃO",
        instrument_type="Declaração",
        parties=[],
    )
    blocks = [_create_sample_block(doc_id=505)]
    doc = ParsedDocument(
        doc_id=505,
        doc_version=1,
        file_path="decl.docx",
        file_hash="h505",
        parser_name="docx_parser",
        parser_version="1.0.0",
        metadata=meta,
        blocks=blocks,
    )

    reconciled = reconcile_document(mfiles_record, doc)
    assert (
        "FLAG_NO_PARTIES_FOUND"
        not in reconciled.metadata.mfiles_divergence_flags
    )
    assert reconciled.status == "success"


def test_reconciler_preserves_failed_status():
    """A failed document remains failed regardless of reconciliation flags."""
    mfiles_record = {"mfiles_id": 606, "cliente": "Empresa X"}
    meta = ContractMetadata(
        formal_title="Não identificado",
        instrument_type="Outro",
        parties=[],
    )
    doc = ParsedDocument(
        doc_id=606,
        doc_version=1,
        file_path="err.docx",
        file_hash="errhash",
        parser_name="docx_parser",
        parser_version="1.0.0",
        metadata=meta,
        blocks=[],
        status="failed",
        error_message="Arquivo corrompido",
    )

    reconciled = reconcile_document(mfiles_record, doc)
    assert reconciled.status == "failed"
    assert reconciled.error_message == "Arquivo corrompido"


def test_reconciler_no_parties_quarantine_disabled():
    """Missing parties flags FLAG_NO_PARTIES_FOUND but does not quarantine when disabled."""
    mfiles_record = {"mfiles_id": 405, "class_name": "Contrato"}
    meta = ContractMetadata(
        formal_title="CONTRATO DE PARCERIA",
        instrument_type="Parceria",
        parties=[],
    )
    blocks = [_create_sample_block(doc_id=405)]
    doc = ParsedDocument(
        doc_id=405,
        doc_version=1,
        file_path="parc.docx",
        file_hash="h405",
        parser_name="docx_parser",
        parser_version="1.0.0",
        metadata=meta,
        blocks=blocks,
    )

    reconciled = reconcile_document(
        mfiles_record, doc, quarantine_on_no_parties=False
    )
    assert "FLAG_NO_PARTIES_FOUND" in reconciled.metadata.mfiles_divergence_flags
    assert reconciled.status == "success"


def test_reconciler_mfiles_display_value_and_no_client():
    """Supports nested DisplayValue dict and handles absence of client cleanly."""
    mfiles_record = {
        "mfiles_id": 406,
        "properties": {
            "Cliente": {"DisplayValue": "Alpha Holding S.A."},
            "Classe": {"DisplayValue": "Contrato"},
        },
    }
    parties = [
        ContractParty(
            name="Alpha Holding S/A",
            role=PartyRole.CONTRATANTE,
            clean_identifier="12345678000199",
        )
    ]
    meta = ContractMetadata(
        formal_title="CONTRATO DE COMODATO",
        instrument_type="Comodato",
        parties=parties,
    )
    blocks = [_create_sample_block(doc_id=406)]
    doc = ParsedDocument(
        doc_id=406,
        doc_version=1,
        file_path="comodato.docx",
        file_hash="h406",
        parser_name="docx_parser",
        parser_version="1.0.0",
        metadata=meta,
        blocks=blocks,
    )

    reconciled = reconcile_document(mfiles_record, doc)
    assert reconciled.status == "success"
    assert "FLAG_INSTRUMENT_TYPE_REFINED" in reconciled.metadata.mfiles_divergence_flags

    # When no client is specified in mfiles_record
    doc_no_client = doc.model_copy(deep=True)
    reconciled_no_client = reconcile_document({}, doc_no_client)
    assert reconciled_no_client.status == "success"
    assert (
        "DISCREPANCY_CLIENT_MISMATCH"
        not in reconciled_no_client.metadata.mfiles_divergence_flags
    )


# ============================================================================
# Parsing Orchestrator Unit Tests (parse_single_document)
# ============================================================================


def test_parse_single_document_docx(tmp_path: Path):
    """End-to-end parsing of .docx: blocks, metadata, sha256, and reconciliation."""
    docx_file = _build_docx(
        tmp_path,
        "contrato_locacao.docx",
        [
            "CONTRATO DE LOCAÇÃO COMERCIAL",
            (
                "Pelo presente instrumento particular, de um lado, IMOBILIÁRIA ALFA LTDA, "
                "inscrita no CNPJ sob o nº 12.345.678/0001-90, doravante denominada LOCADOR; "
                "e, de outro lado, BETA COMÉRCIO LTDA, inscrita no CNPJ sob o nº 98.765.432/0001-10, "
                "doravante denominada LOCATÁRIO; têm entre si justo e acordado o seguinte:"
            ),
            "CLÁUSULA PRIMEIRA - DO OBJETO: O presente contrato tem por objeto a locação do imóvel comercial situado na Av. Paulista, 1000.",
            "CLÁUSULA SEGUNDA - DO VALOR: O aluguel mensal é de R$ 10.000,00.",
            "São Paulo, 15 de março de 2024.",
        ],
    )

    mfiles_record = {
        "mfiles_id": 707,
        "title": "contrato_locacao.docx",
        "cliente": "Imobiliária Alfa Ltda",
        "class_name": "Contrato",
    }

    doc = parse_single_document(docx_file, mfiles_record)
    assert doc.status == "success"
    assert doc.doc_id == 707
    assert doc.parser_name == "docx_parser"
    assert len(doc.file_hash) == 64
    assert len(doc.blocks) >= 4
    assert doc.metadata.formal_title == "CONTRATO DE LOCAÇÃO COMERCIAL"
    assert doc.metadata.instrument_type == "Locação"
    assert len(doc.metadata.parties) == 2
    assert "FLAG_INSTRUMENT_TYPE_REFINED" in doc.metadata.mfiles_divergence_flags
    assert "DISCREPANCY_CLIENT_MISMATCH" not in doc.metadata.mfiles_divergence_flags


def test_parse_single_document_pdf(tmp_path: Path):
    """End-to-end parsing of .pdf file with metadata extraction and reconciliation."""
    pdf_file = _build_pdf(
        tmp_path,
        "contrato_servicos.pdf",
        [
            "CONTRATO DE PRESTACAO DE SERVICOS",
            "Pelo presente instrumento, de um lado, EMPRESA ALFA LTDA, inscrita no CNPJ sob o n 11.222.333/0001-44, doravante denominada CONTRATANTE; e, de outro lado, TECH BETA LTDA, inscrita no CNPJ sob o n 55.666.777/0001-88, doravante denominada CONTRATADA; tem entre si justo e acordado:",
            "CLAUSULA PRIMEIRA - DO OBJETO: Prestacao de servicos de consultoria em TI para a contratante.",
            "Belo Horizonte, 10 de maio de 2024.",
        ],
    )

    mfiles_record = {
        "mfiles_id": 808,
        "title": "contrato_servicos.pdf",
        "properties": {"Cliente": "Empresa Alfa Ltda", "Classe": "Prestação de Serviços"},
    }

    doc = parse_single_document(pdf_file, mfiles_record)
    assert doc.status == "success"
    assert doc.doc_id == 808
    assert doc.parser_name == "pdf_parser"
    assert len(doc.blocks) >= 3
    assert doc.metadata.instrument_type == "Prestação de Serviços"


def test_parse_single_document_legacy_route(tmp_path: Path):
    """Verifies that legacy extensions (.doc, .rtf, .wpd) route to extract_legacy_blocks."""
    legacy_file = tmp_path / "minuta.doc"
    legacy_file.write_text("dummy binary legacy content")

    dummy_blocks = [
        DocumentBlock(
            block_id="b1",
            doc_id=909,
            doc_version=1,
            block_type=BlockType.TITLE,
            hierarchy_level=HierarchyLevel.TITLE,
            order_index=0,
            text_raw="CONTRATO DE PARCERIA",
            text_search="CONTRATO DE PARCERIA",
        ),
        DocumentBlock(
            block_id="b2",
            doc_id=909,
            doc_version=1,
            block_type=BlockType.PREAMBLE,
            hierarchy_level=HierarchyLevel.PREAMBLE,
            order_index=1,
            text_raw="Pelo presente instrumento, de um lado, PARCEIRO A LTDA, CNPJ 11.111.111/0001-11, doravante denominada CONTRATANTE; e, de outro lado, PARCEIRO B LTDA, CNPJ 22.222.222/0001-22, doravante denominada CONTRATADA; têm justo e acordado:",
            text_search="Pelo presente instrumento, de um lado, PARCEIRO A LTDA, CNPJ 11.111.111/0001-11, doravante denominada CONTRATANTE; e, de outro lado, PARCEIRO B LTDA, CNPJ 22.222.222/0001-22, doravante denominada CONTRATADA; têm justo e acordado:",
        ),
    ]

    with patch(
        "app.ingestion.parsing_pipeline.extract_legacy_blocks",
        return_value=dummy_blocks,
    ) as mock_extract:
        mfiles_record = {"mfiles_id": 909, "cliente": "PARCEIRO A LTDA"}
        doc = parse_single_document(legacy_file, mfiles_record)
        assert mock_extract.called
        assert doc.parser_name == "legacy_parser"
        assert doc.status == "success"
        assert len(doc.blocks) == 2


def test_parse_single_document_corrupted_file_defensive_handling(tmp_path: Path):
    """A corrupted .docx returns status='failed' with error_message and blocks=[]."""
    corrupt_docx = tmp_path / "corrupt.docx"
    corrupt_docx.write_bytes(b"not a valid zip file header")

    mfiles_record = {"mfiles_id": 1111, "title": "corrupt.docx"}
    doc = parse_single_document(corrupt_docx, mfiles_record)

    assert doc.status == "failed"
    assert doc.blocks == []
    assert doc.error_message is not None
    assert "corrompido" in doc.error_message.lower() or "zip" in doc.error_message.lower() or "inválido" in doc.error_message.lower()


def test_parse_single_document_missing_file_defensive_handling(tmp_path: Path):
    """A non-existent file returns status='failed'."""
    missing = tmp_path / "does_not_exist.docx"
    doc = parse_single_document(missing, {"mfiles_id": 2222})
    assert doc.status == "failed"
    assert doc.blocks == []
    assert doc.error_message is not None


def test_parse_single_document_unsupported_extension(tmp_path: Path):
    """Unsupported extension returns status='failed' with descriptive error."""
    unsupported = tmp_path / "arquivo.unknown_ext"
    unsupported.write_text("hello")
    doc = parse_single_document(unsupported, {"mfiles_id": 3333})
    assert doc.status == "failed"
    assert "não suportada" in doc.error_message.lower() or "unsupported" in doc.error_message.lower()


# ============================================================================
# Batch Pipeline Unit Tests (run_parsing_pipeline)
# ============================================================================


def test_run_parsing_pipeline_batch(tmp_path: Path):
    """Batch parsing processes multiple records across success, mismatch, and error."""
    # Create file 1 (valid docx matching client)
    f1 = _build_docx(
        tmp_path,
        "doc1.docx",
        [
            "CONTRATO DE HONORÁRIOS ADVOCATÍCIOS",
            (
                "Pelo presente instrumento particular de prestação de serviços advocatícios, "
                "de um lado, CLIENTE ALFA S.A., inscrita no CNPJ sob o nº 12.345.678/0001-90, "
                "doravante denominada CONTRATANTE; e, de outro lado, ANDRADE ADVOGADOS ASSOCIADOS, "
                "sociedade de advogados inscrita no CNPJ sob o nº 01.234.567/0001-89, "
                "doravante denominada CONTRATADA; têm entre si justo e acordado o seguinte:"
            ),
            "CLÁUSULA 1 - OBJETO: Serviços jurídicos consultivos e contenciosos.",
            "São Paulo, 10 de maio de 2024.",
        ],
    )
    # Create file 2 (valid docx with client mismatch)
    f2 = _build_docx(
        tmp_path,
        "doc2.docx",
        [
            "CONTRATO DE LOCAÇÃO",
            (
                "Pelo presente instrumento particular, de um lado, LOCADOR BETA LTDA, "
                "inscrita no CNPJ sob o nº 11.222.333/0001-44, doravante denominada LOCADOR; "
                "e, de outro lado, LOCATÁRIO GAMA LTDA, inscrita no CNPJ sob o nº 55.666.777/0001-88, "
                "doravante denominada LOCATÁRIO; têm entre si justo e acordado:"
            ),
            "CLÁUSULA 1 - OBJETO: Locação comercial.",
            "São Paulo, 10 de maio de 2024.",
        ],
    )
    # Create file 3 (corrupt docx)
    f3 = tmp_path / "doc3.docx"
    f3.write_bytes(b"corrupt")

    records = [
        {
            "mfiles_id": 1,
            "title": "doc1.docx",
            "cliente": "CLIENTE ALFA S.A.",
            "files": [{"path": "doc1.docx"}],
        },
        {
            "mfiles_id": 2,
            "title": "doc2.docx",
            "properties": {"Cliente": "DELTA INEXISTENTE LTDA"},
            "file": {"path": "doc2.docx"},
        },
        {
            "mfiles_id": 3,
            "title": "doc3.docx",
            "cliente": "QUALQUER UM",
            "file_path": "doc3.docx",
        },
    ]

    results = run_parsing_pipeline(records, raw_dir=tmp_path)
    assert len(results) == 3

    r1, r2, r3 = results
    # Doc 1: success
    assert r1.doc_id == 1
    assert r1.status == "success"
    assert "DISCREPANCY_CLIENT_MISMATCH" not in r1.metadata.mfiles_divergence_flags

    # Doc 2: review_metadata_mismatch
    assert r2.doc_id == 2
    assert r2.status == "review_metadata_mismatch"
    assert "DISCREPANCY_CLIENT_MISMATCH" in r2.metadata.mfiles_divergence_flags

    # Doc 3: failed
    assert r3.doc_id == 3
    assert r3.status == "failed"
    assert r3.blocks == []

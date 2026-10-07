"""
Unit tests for the PDF parsing engine with pypdf and pypdfium2 rasterization for OCR.

Covers:
- Physical file and header validation (FileNotFoundError, empty file, corrupt header).
- Rejection of encrypted PDFs.
- Native digital text extraction without OCR flags.
- Surgical OCR activation for scanned/low-text pages (< 50 chars) via pypdfium2 and pytesseract.
- Hybrid multi-page documents (mix of digital native and scanned pages).
- Structural classification (Title, Preamble, Clause, Paragraph, Item, Signature).
- Parent clause traceability and hierarchy labels.
- Graceful handling when Tesseract is missing without crashing.
- Integration and roundtrip with ParsedDocument model.
- Resource cleanup for pypdfium2 instances.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from app.ingestion.schemas import (
    BlockType,
    ContractMetadata,
    HierarchyLevel,
    ParsedDocument,
    UncertaintyFlag,
)


def _create_synthetic_pdf(pages_lines: list[list[str]]) -> bytes:
    """Helper to generate a valid PDF byte string with 1 or more pages of text."""
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


def _write_pdf_file(tmp_path: Path, filename: str, pages_lines: list[list[str]]) -> Path:
    pdf_path = tmp_path / filename
    pdf_bytes = _create_synthetic_pdf(pages_lines)
    pdf_path.write_bytes(pdf_bytes)
    return pdf_path


def test_pdf_physical_locator_preserves_page_and_block_origin(tmp_path):
    from app.ingestion.pdf_parser import extract_pdf_blocks
    path = _write_pdf_file(tmp_path, "locations.pdf", [["CLÁUSULA PRIMEIRA - O pagamento sintético segue as obrigações expressamente pactuadas."], ["CLÁUSULA SEGUNDA - A obrigação sintética tem vencimento expressamente previsto no instrumento."]])
    blocks = extract_pdf_blocks(path, doc_id=1)
    assert [block.source_locator["page"] for block in blocks] == [1, 2]
    assert all(block.source_locator["page_block_index"] == 0 for block in blocks)
    assert all(block.source_locator["extraction"] == "native" for block in blocks)
    assert all(block.source_locator["verification"] == "unreviewed" for block in blocks)


# --- Step 1: Physical file and header validation ---


def test_pdf_file_not_found():
    from app.ingestion.pdf_parser import extract_pdf_blocks

    with pytest.raises(FileNotFoundError, match="Arquivo PDF inexistente"):
        extract_pdf_blocks("caminho_inexistente_12345.pdf", doc_id=1)


def test_pdf_empty_file(tmp_path: Path):
    from app.ingestion.pdf_parser import extract_pdf_blocks

    empty_pdf = tmp_path / "vazio.pdf"
    empty_pdf.write_bytes(b"")

    with pytest.raises(ValueError, match="Arquivo PDF vazio"):
        extract_pdf_blocks(empty_pdf, doc_id=2)


def test_pdf_invalid_header(tmp_path: Path):
    from app.ingestion.pdf_parser import extract_pdf_blocks

    corrupt_pdf = tmp_path / "corrupto.pdf"
    corrupt_pdf.write_bytes(b"NOT A VALID PDF HEADER CONTENT")

    with pytest.raises(ValueError, match="Arquivo PDF corrompido ou cabeçalho inválido"):
        extract_pdf_blocks(corrupt_pdf, doc_id=3)


def test_pdf_encrypted_rejected(tmp_path: Path):
    from app.ingestion.pdf_parser import extract_pdf_blocks
    import pypdf

    pdf_path = tmp_path / "encrypted.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("minha_senha_123")
    with open(pdf_path, "wb") as f:
        writer.write(f)

    with pytest.raises(ValueError, match="PDF criptografado não suportado"):
        extract_pdf_blocks(pdf_path, doc_id=4)


# --- Step 2: Native digital text extraction ---


def test_native_digital_pdf_extraction(tmp_path: Path):
    from app.ingestion.pdf_parser import extract_pdf_blocks

    lines = [
        "CONTRATO DE PRESTACAO DE SERVICOS ADVOCATICIOS",
        "Pelo presente instrumento particular, de um lado Contratante e de outro Andrade Advogados.",
        "CLAUSULA PRIMEIRA - DO OBJETO",
        "O presente contrato tem por objeto a prestacao de servicos juridicos contenciosos.",
        "PARAGRAFO PRIMEIRO - O foro eleito e o da comarca de Sao Paulo.",
    ]
    pdf_path = _write_pdf_file(tmp_path, "digital.pdf", [lines])

    blocks = extract_pdf_blocks(pdf_path, doc_id=10, doc_version=1, ocr_min_chars=50)

    assert len(blocks) >= 4
    # All blocks should have empty uncertainty_flags (no OCR used)
    for b in blocks:
        assert UncertaintyFlag.LOW_CONFIDENCE_OCR not in b.uncertainty_flags

    assert blocks[0].block_type == BlockType.TITLE
    assert blocks[0].hierarchy_level == HierarchyLevel.TITLE
    assert "CONTRATO" in blocks[0].text_raw

    assert blocks[1].block_type == BlockType.PREAMBLE
    assert blocks[1].hierarchy_level == HierarchyLevel.PREAMBLE

    assert blocks[2].block_type == BlockType.CLAUSE
    assert blocks[2].hierarchy_level == HierarchyLevel.CLAUSE
    assert blocks[2].hierarchy_label is not None
    assert "CLAUSULA" in blocks[2].hierarchy_label

    assert blocks[3].block_type == BlockType.PARAGRAPH
    assert blocks[3].parent_clause_id == blocks[2].block_id


# --- Step 3: Surgical OCR activation for scanned/short pages ---


def test_scanned_pdf_triggers_ocr(tmp_path: Path, monkeypatch):
    from app.ingestion.pdf_parser import extract_pdf_blocks

    # Page with only 10 characters (below default ocr_min_chars=50)
    pdf_path = _write_pdf_file(tmp_path, "scanned.pdf", [["Scan 123"]])

    mock_doc_ium = MagicMock()
    mock_page_ium = MagicMock()
    mock_render_result = MagicMock()
    mock_pil_image = MagicMock()

    mock_doc_ium.__getitem__.return_value = mock_page_ium
    mock_page_ium.render.return_value = mock_render_result
    mock_render_result.to_pil.return_value = mock_pil_image

    ocr_mock_output = (
        "CONTRATO DE HONORARIOS ADVOCATICIOS\n\n"
        "Pelo presente instrumento particular...\n\n"
        "CLAUSULA PRIMEIRA - DO OBJETO\n\n"
        "Prestacao de servicos advocaticios em juizo.\n"
    )

    with patch("pypdfium2.PdfDocument", return_value=mock_doc_ium) as mock_pdf_doc, \
         patch("pytesseract.image_to_string", return_value=ocr_mock_output) as mock_ocr:

        blocks = extract_pdf_blocks(pdf_path, doc_id=20, doc_version=1, ocr_min_chars=50, ocr_lang="por")

        # Verify pypdfium2 was invoked and closed
        mock_pdf_doc.assert_called_once_with(str(pdf_path))
        mock_doc_ium.__getitem__.assert_called_once_with(0)
        mock_page_ium.render.assert_called_once_with(scale=2.0)
        mock_doc_ium.close.assert_called_once()

        # Verify pytesseract was invoked with the rendered PIL image and lang
        mock_ocr.assert_called_once_with(mock_pil_image, lang="por", timeout=30)

        assert len(blocks) >= 3
        # ALL blocks from the scanned page must carry LOW_CONFIDENCE_OCR
        for b in blocks:
            assert UncertaintyFlag.LOW_CONFIDENCE_OCR in b.uncertainty_flags


# --- Step 4: Hybrid PDF with mixed digital and scanned pages ---


def test_hybrid_pdf_mixed_digital_and_scanned(tmp_path: Path):
    from app.ingestion.pdf_parser import extract_pdf_blocks

    # Page 1: digital native (> 50 chars)
    page1_lines = [
        "CONTRATO DE PRESTACAO DE SERVICOS",
        "Pelo presente instrumento particular, as partes ajustam o objeto deste contrato que supera cinquenta caracteres com folga.",
        "CLAUSULA PRIMEIRA - DO OBJETO",
        "O objeto sera rigorosamente cumprido pelas partes envolvidas.",
    ]
    # Page 2: scanned / short text (< 50 chars)
    page2_lines = [""]

    pdf_path = _write_pdf_file(tmp_path, "hybrid.pdf", [page1_lines, page2_lines])

    mock_doc_ium = MagicMock()
    mock_page_ium = MagicMock()
    mock_doc_ium.__getitem__.return_value = mock_page_ium
    mock_page_ium.render.return_value.to_pil.return_value = MagicMock()

    ocr_page2_text = (
        "CLAUSULA SEGUNDA - DO PRECO\n\n"
        "O valor pactuado e de R$ 10.000,00.\n\n"
        "E por estarem justos e acordados, assinam.\n\n"
        "TESTEMUNHAS:\n"
    )

    with patch("pypdfium2.PdfDocument", return_value=mock_doc_ium), \
         patch("pytesseract.image_to_string", return_value=ocr_page2_text):

        blocks = extract_pdf_blocks(pdf_path, doc_id=30, doc_version=1, ocr_min_chars=50)

        # Page 1 blocks (digital): NO LOW_CONFIDENCE_OCR
        page1_blocks = [b for b in blocks if "CLAUSULA PRIMEIRA" in b.text_raw or "CONTRATO" in b.text_raw]
        assert len(page1_blocks) >= 2
        for b in page1_blocks:
            assert UncertaintyFlag.LOW_CONFIDENCE_OCR not in b.uncertainty_flags

        # Page 2 blocks (OCR): MUST have LOW_CONFIDENCE_OCR
        page2_blocks = [b for b in blocks if "CLAUSULA SEGUNDA" in b.text_raw or "TESTEMUNHAS" in b.text_raw]
        assert len(page2_blocks) >= 2
        for b in page2_blocks:
            assert UncertaintyFlag.LOW_CONFIDENCE_OCR in b.uncertainty_flags


# --- Step 5: Structural block classification & hierarchy tracking ---


def test_pdf_structural_classification_and_hierarchy(tmp_path: Path):
    from app.ingestion.pdf_parser import extract_pdf_blocks

    lines = [
        "CONTRATO DE HONORARIOS ADVOCATICIOS",
        "Pelo presente instrumento particular, de um lado Andrade Advogados...",
        "CLAUSULA PRIMEIRA - DO OBJETO",
        "O presente contrato tem por objeto a prestacao de servicos advocaticios.",
        "PARAGRAFO PRIMEIRO - Os servicos englobam atuacao no tribunal.",
        "a) Atuacao na fase recursal ordinaria.",
        "b) Despacho com relatores.",
        "CLAUSULA SEGUNDA - DOS HONORARIOS",
        "O cliente pagara o valor acordado.",
        "E por estarem de pleno acordo, firmam o presente.",
        "TESTEMUNHAS:",
        "1. Fulano de Tal",
    ]
    pdf_path = _write_pdf_file(tmp_path, "contrato_completo.pdf", [lines])

    blocks = extract_pdf_blocks(pdf_path, doc_id=40, doc_version=1, ocr_min_chars=20)

    # 1. Title
    assert blocks[0].block_type == BlockType.TITLE
    assert blocks[0].hierarchy_level == HierarchyLevel.TITLE
    assert blocks[0].parent_clause_id is None
    assert blocks[0].order_index == 0

    # 2. Preamble
    assert blocks[1].block_type == BlockType.PREAMBLE
    assert blocks[1].hierarchy_level == HierarchyLevel.PREAMBLE
    assert blocks[1].parent_clause_id is None

    # 3. Clause 1
    c1 = blocks[2]
    assert c1.block_type == BlockType.CLAUSE
    assert c1.hierarchy_level == HierarchyLevel.CLAUSE
    assert c1.hierarchy_label == "CLAUSULA PRIMEIRA"
    assert c1.parent_clause_id is None

    # 4. Paragraph under Clause 1
    assert blocks[3].block_type == BlockType.PARAGRAPH
    assert blocks[3].hierarchy_level == HierarchyLevel.PARAGRAPH
    assert blocks[3].parent_clause_id == c1.block_id

    # 5. Parágrafo Primeiro under Clause 1
    assert blocks[4].block_type == BlockType.PARAGRAPH
    assert blocks[4].hierarchy_level == HierarchyLevel.PARAGRAPH
    assert blocks[4].hierarchy_label == "PARAGRAFO PRIMEIRO"
    assert blocks[4].parent_clause_id == c1.block_id

    # 6. Item a) under Clause 1
    assert blocks[5].block_type == BlockType.ITEM
    assert blocks[5].hierarchy_level == HierarchyLevel.ITEM
    assert blocks[5].hierarchy_label == "a)"
    assert blocks[5].parent_clause_id == c1.block_id

    # 7. Item b) under Clause 1
    assert blocks[6].block_type == BlockType.ITEM
    assert blocks[6].hierarchy_level == HierarchyLevel.ITEM
    assert blocks[6].hierarchy_label == "b)"
    assert blocks[6].parent_clause_id == c1.block_id

    # 8. Clause 2
    c2 = blocks[7]
    assert c2.block_type == BlockType.CLAUSE
    assert c2.hierarchy_level == HierarchyLevel.CLAUSE
    assert c2.hierarchy_label == "CLAUSULA SEGUNDA"
    assert c2.parent_clause_id is None

    # 9. Paragraph under Clause 2
    assert blocks[8].block_type == BlockType.PARAGRAPH
    assert blocks[8].parent_clause_id == c2.block_id

    # 10. Signature closing phrase
    assert blocks[9].block_type == BlockType.SIGNATURE
    assert blocks[9].hierarchy_level == HierarchyLevel.SIGNATURE
    assert blocks[9].parent_clause_id is None

    # 11. Testemunhas header
    assert blocks[10].block_type == BlockType.SIGNATURE
    assert blocks[10].hierarchy_level == HierarchyLevel.SIGNATURE
    assert blocks[10].parent_clause_id is None

    # 12. Witness line in signature section
    assert blocks[11].block_type == BlockType.SIGNATURE
    assert blocks[11].hierarchy_level == HierarchyLevel.SIGNATURE
    assert blocks[11].parent_clause_id is None


# --- Step 6: Tesseract missing / error graceful fallback ---


def test_ocr_tesseract_missing_fallback(tmp_path: Path):
    from app.ingestion.pdf_parser import extract_pdf_blocks
    import pytesseract

    # Page with low text count, triggers OCR attempt
    pdf_path = _write_pdf_file(tmp_path, "ocr_fail.pdf", [["Minimo"]])

    mock_doc_ium = MagicMock()
    mock_page_ium = MagicMock()
    mock_doc_ium.__getitem__.return_value = mock_page_ium
    mock_page_ium.render.return_value.to_pil.return_value = MagicMock()

    with patch("pypdfium2.PdfDocument", return_value=mock_doc_ium), \
         patch("pytesseract.image_to_string", side_effect=pytesseract.TesseractNotFoundError()):

        # Should NOT raise exception, must handle gracefully
        blocks = extract_pdf_blocks(pdf_path, doc_id=50, doc_version=1, ocr_min_chars=50)

        # Fallback retains whatever short native text was present, tagged with LOW_CONFIDENCE_OCR
        assert len(blocks) >= 1
        assert "Minimo" in blocks[0].text_raw
        assert UncertaintyFlag.LOW_CONFIDENCE_OCR in blocks[0].uncertainty_flags


# --- Step 7: ParsedDocument integration and roundtrip ---


def test_extract_pdf_blocks_integrates_with_parsed_document(tmp_path: Path):
    from app.ingestion.pdf_parser import extract_pdf_blocks

    lines = [
        "CONTRATO SOCIAL",
        "Pelo presente instrumento particular...",
        "CLAUSULA 1 - DO CAPITAL SOCIAL",
        "O capital social da sociedade e de R$ 100.000,00.",
    ]
    pdf_path = _write_pdf_file(tmp_path, "integracao.pdf", [lines])

    blocks = extract_pdf_blocks(pdf_path, doc_id=100, doc_version=1)

    parsed_doc = ParsedDocument(
        doc_id=100,
        doc_version=1,
        file_path=str(pdf_path),
        file_hash="hash_pdf_123456",
        parser_name="pdf_parser",
        parser_version="1.0.0",
        metadata=ContractMetadata(
            formal_title="CONTRATO SOCIAL",
            instrument_type="Contrato Social",
        ),
        blocks=blocks,
        status="success",
    )

    assert parsed_doc.status == "success"
    assert len(parsed_doc.blocks) >= 3
    # Check serialization roundtrip
    dumped = parsed_doc.model_dump()
    assert dumped["parser_name"] == "pdf_parser"
    assert len(dumped["blocks"]) == len(blocks)
    reloaded = ParsedDocument.model_validate(dumped)
    assert reloaded.doc_id == 100


# --- Step 8: Custom parameters test ---


def test_custom_ocr_min_chars_and_lang(tmp_path: Path):
    from app.ingestion.pdf_parser import extract_pdf_blocks

    # Text length is 40 chars. With ocr_min_chars=30, it is considered native digital (no OCR).
    # With ocr_min_chars=50, it triggers OCR.
    lines = ["Texto com trinta e poucos caracteres."]
    pdf_path = _write_pdf_file(tmp_path, "threshold.pdf", [lines])

    # 1. Below threshold -> digital native, no OCR called
    with patch("pytesseract.image_to_string") as mock_ocr:
        blocks = extract_pdf_blocks(pdf_path, doc_id=60, ocr_min_chars=30)
        assert mock_ocr.call_count == 0
        assert len(blocks) == 1
        assert UncertaintyFlag.LOW_CONFIDENCE_OCR not in blocks[0].uncertainty_flags

    # 2. Above threshold -> triggers OCR with custom lang
    mock_doc_ium = MagicMock()
    mock_page_ium = MagicMock()
    mock_doc_ium.__getitem__.return_value = mock_page_ium
    mock_page_ium.render.return_value.to_pil.return_value = MagicMock()

    with patch("pypdfium2.PdfDocument", return_value=mock_doc_ium), \
         patch("pytesseract.image_to_string", return_value="English OCR result text") as mock_ocr:
        blocks = extract_pdf_blocks(pdf_path, doc_id=61, ocr_min_chars=50, ocr_lang="eng")
        assert mock_ocr.call_count == 1
        assert mock_ocr.call_args.kwargs.get("lang") == "eng"
        assert len(blocks) >= 1
        assert UncertaintyFlag.LOW_CONFIDENCE_OCR in blocks[0].uncertainty_flags


# --- Step 9: Edge cases: zero pages, cleanup on failure, cross-page state ---


def test_pdf_zero_pages_rejected(tmp_path: Path):
    from app.ingestion.pdf_parser import extract_pdf_blocks
    import pypdf

    # Create empty PDF without pages
    pdf_path = tmp_path / "zero_pages.pdf"
    writer = pypdf.PdfWriter()
    with open(pdf_path, "wb") as f:
        writer.write(f)

    with pytest.raises(ValueError, match="PDF não contém páginas"):
        extract_pdf_blocks(pdf_path, doc_id=70)


def test_ocr_resource_cleanup_even_on_failure(tmp_path: Path):
    from app.ingestion.pdf_parser import extract_pdf_blocks
    import pytesseract

    pdf_path = _write_pdf_file(tmp_path, "cleanup_fail.pdf", [["Scan"]])

    mock_doc_ium = MagicMock()
    mock_page_ium = MagicMock()
    mock_doc_ium.__getitem__.return_value = mock_page_ium
    mock_page_ium.render.return_value.to_pil.return_value = MagicMock()

    with patch("pypdfium2.PdfDocument", return_value=mock_doc_ium), \
         patch("pytesseract.image_to_string", side_effect=RuntimeError("OCR crash")):

        extract_pdf_blocks(pdf_path, doc_id=80, ocr_min_chars=50)

        # doc_ium.close() must still be called even when OCR raises an exception
        mock_doc_ium.close.assert_called_once()


def test_pdf_multi_page_state_continuity(tmp_path: Path):
    from app.ingestion.pdf_parser import extract_pdf_blocks

    page1 = [
        "CONTRATO DE PRESTACAO DE SERVICOS",
        "CLAUSULA PRIMEIRA - DO OBJETO",
    ]
    page2 = [
        "O presente contrato tem por objeto a prestacao de servicos juridicos continuados na segunda pagina deste termo.",
    ]
    pdf_path = _write_pdf_file(tmp_path, "cross_page.pdf", [page1, page2])

    blocks = extract_pdf_blocks(pdf_path, doc_id=90, ocr_min_chars=20)

    assert len(blocks) == 3
    assert blocks[0].block_type == BlockType.TITLE
    assert blocks[1].block_type == BlockType.CLAUSE
    assert blocks[1].hierarchy_label == "CLAUSULA PRIMEIRA"
    clause_id = blocks[1].block_id

    # Block on Page 2 must have parent_clause_id linked to Clause on Page 1
    assert blocks[2].block_type == BlockType.PARAGRAPH
    assert blocks[2].parent_clause_id == clause_id


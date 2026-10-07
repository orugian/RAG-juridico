"""Synthetic local artifacts: physical provenance is never candidate approval."""
import hashlib
from pathlib import Path
import zipfile

import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from app.contracts import SourceIdentity
from app.ingestion.docx_parser import extract_docx_blocks
from app.ingestion.pdf_parser import extract_pdf_blocks
from app.ingestion.provenance import ProvenanceError, verify_document_provenance
from app.ingestion.schemas import ContractMetadata, ParsedDocument


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def docx(tmp_path, *, content=None, numbering=None):
    path = tmp_path / "synthetic.docx"
    content = content or '<w:p/><w:p><w:r><w:t>CONTRATO SINTÉTICO</w:t></w:r></w:p><w:p><w:r><w:t>Não pagar, salvo condição expressa.</w:t></w:r></w:p>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", f'<w:document xmlns:w="{W}"><w:body>{content}</w:body></w:document>')
        if numbering:
            archive.writestr("word/numbering.xml", numbering)
    return path


def pdf(tmp_path, *, continuation="A exceção permanece literalmente preservada."):
    path = tmp_path / "synthetic.pdf"
    writer = PdfWriter()
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica"), NameObject("/Encoding"): NameObject("/WinAnsiEncoding")})
    for number in (1, 2):
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 50 750 Td (CLÁUSULA {number} - Não pagar, salvo condição expressa no contrato sintético.) Tj 0 -20 Td ({continuation}) Tj ET".encode("latin1"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(path)
    return path


def candidate(path, *, parser="docx_parser", blocks=None):
    blocks = blocks or (extract_docx_blocks(path, 17, 2) if parser == "docx_parser" else extract_pdf_blocks(path, 17, 2))
    return ParsedDocument(doc_id=17, doc_version=2, file_id="file:7", file_path=str(path), file_hash=hashlib.sha256(path.read_bytes()).hexdigest(), parser_name=parser, parser_version="1.0.0", metadata=ContractMetadata(formal_title="Contrato sintético", instrument_type="Contrato"), blocks=blocks)


def identity(document, **changes):
    return SourceIdentity(source_id=f"doc:{document.doc_id}:v{document.doc_version}:file:{document.file_id}", instrument_id=f"doc:{document.doc_id}", doc_version=document.doc_version, file_id=document.file_id, file_hash=document.file_hash, snapshot="snapshot:test", configuration_version="config:test", parser_name=document.parser_name, parser_version=document.parser_version).model_copy(update=changes)


def verify(document, **kwargs):
    source = identity(document, conversion_sha256=kwargs.get("conversion_sha256"))
    return verify_document_provenance(document, source, original_path=document.file_path, **kwargs)


def test_docx_physical_child_offsets_ignore_empty_paragraphs(tmp_path):
    document = candidate(docx(tmp_path))
    result = verify(document)
    assert list(result) == [block.block_id for block in document.blocks]
    first = result[document.blocks[0].block_id]
    assert first.literal_text == "CONTRATO SINTÉTICO"
    assert first.synthetic_context == ""
    assert first.span.body_child_index == 1
    assert first.span.xml_part == "word/document.xml"
    assert first.span.start == 0 and first.span.end == len(first.literal_text)


@pytest.mark.parametrize("field,value", [("instrument_id", "doc:99"), ("source_id", "source:fake"), ("doc_version", 3), ("file_id", "file:8"), ("file_hash", "0" * 64), ("parser_name", "unknown"), ("parser_version", "fake")])
def test_exact_source_identity_is_required(tmp_path, field, value):
    document = candidate(docx(tmp_path))
    with pytest.raises(ProvenanceError, match="identity"):
        verify_document_provenance(document, identity(document, **{field: value}), original_path=document.file_path)


@pytest.mark.parametrize("mutation", ["text", "locator", "span", "order", "missing", "duplicate", "block_id", "block_version", "structure", "source_text"])
def test_candidate_approved_marker_cannot_hide_mutation(tmp_path, mutation):
    document = candidate(docx(tmp_path))
    block = document.blocks[0]
    block.source_locator["verification"] = "approved"
    if mutation == "text":
        block.text_raw = "Texto adulterado"
    elif mutation == "locator":
        block.source_locator["body_child_index"] = 0
    elif mutation == "span":
        block.spans[0]["end"] += 1
    elif mutation == "order":
        block.order_index = 99
    elif mutation == "missing":
        document.blocks.pop()
    elif mutation == "duplicate":
        document.blocks.append(block.model_copy(deep=True))
    elif mutation == "block_id":
        block.block_id = "invented:block"
    elif mutation == "block_version":
        block.doc_version = 99
    elif mutation == "structure":
        block.parent_clause_id = "invented:parent"
    elif mutation == "source_text":
        block.source_locator["source_text"] = "Fonte inventada"
    with pytest.raises(ProvenanceError):
        verify(document)


def test_artifact_path_and_actual_hash_are_required(tmp_path):
    document = candidate(docx(tmp_path))
    other = tmp_path / "other.docx"
    other.write_bytes(Path(document.file_path).read_bytes())
    with pytest.raises(ProvenanceError, match="path"):
        verify_document_provenance(document, identity(document), original_path=other)
    Path(document.file_path).write_bytes(b"modified artifact")
    with pytest.raises(ProvenanceError, match="hash"):
        verify(document)


def test_numbering_and_table_headers_are_context_not_literal(tmp_path):
    numbering = f'<w:numbering xmlns:w="{W}"><w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl></w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num></w:numbering>'
    content = '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>Não pagar salvo condição.</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Valor</w:t></w:r></w:p></w:tc></w:tr><w:tr><w:tc><w:p><w:r><w:t>100</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
    document = candidate(docx(tmp_path, content=content, numbering=numbering))
    result = verify(document)
    numbered = result[document.blocks[0].block_id]
    assert numbered.literal_text == "Não pagar salvo condição."
    assert numbered.synthetic_context == "1. - "
    assert numbered.span.end == len(numbered.literal_text)
    cell = result[document.blocks[-1].block_id]
    assert cell.literal_text == "100"
    assert cell.synthetic_context == "Valor: "
    assert (cell.span.body_child_index, cell.span.row_index, cell.span.column_index) == (1, 1, 0)
    assert cell.span.end == 3


def test_synthetic_only_table_cell_is_explicitly_blocked(tmp_path):
    content = '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Valor</w:t></w:r></w:p></w:tc></w:tr><w:tr><w:tc><w:p/></w:tc></w:tr></w:tbl>'
    document = candidate(docx(tmp_path, content=content))
    with pytest.raises(ProvenanceError, match="synthetic_only"):
        verify(document)


def test_native_pdf_pages_and_no_invented_bbox(tmp_path):
    document = candidate(pdf(tmp_path), parser="pdf_parser")
    result = verify(document)
    assert [item.span.page for item in result.values()] == [1, 1, 2, 2]
    assert [item.span.page_block_index for item in result.values()] == [0, 1, 0, 1]
    assert all(item.synthetic_context == "" for item in result.values())
    assert all(item.span.end == len(item.literal_text) for item in result.values())


def test_pdf_ocr_without_retained_proof_is_blocked_even_if_approved(tmp_path):
    document = candidate(pdf(tmp_path), parser="pdf_parser")
    document.blocks[0].source_locator.update(extraction="ocr", verification="approved")
    with pytest.raises(ProvenanceError, match="ocr_proof_unavailable"):
        verify(document)


def test_pdf_wrong_page_and_unverified_bbox_are_blocked(tmp_path):
    document = candidate(pdf(tmp_path), parser="pdf_parser")
    document.blocks[0].source_locator["page"] = 2
    with pytest.raises(ProvenanceError):
        verify(document)
    document = candidate(pdf(tmp_path), parser="pdf_parser")
    document.blocks[0].source_locator["bbox"] = [0, 0, 1, 1]
    with pytest.raises(ProvenanceError, match="bbox"):
        verify(document)


def converted(tmp_path):
    derived = docx(tmp_path)
    original = tmp_path / "synthetic.rtf"
    original.write_bytes(b"{\\rtf1 Synthetic original fixture}")
    document = candidate(derived)
    document.file_path = str(original)
    document.file_hash = hashlib.sha256(original.read_bytes()).hexdigest()
    document.parser_name = "legacy_parser"
    for block in document.blocks:
        block.source_locator.update(format="converted_docx", original_format=".rtf", conversion_artifact_retained=True)
    return document, derived, hashlib.sha256(derived.read_bytes()).hexdigest()


@pytest.mark.parametrize("mode", ["absent", "missing", "wrong_hash", "not_retained"])
def test_conversion_requires_retained_hash_verified_derivative(tmp_path, mode):
    document, derived, digest = converted(tmp_path)
    kwargs = {"conversion_path": derived, "conversion_sha256": digest}
    if mode == "absent":
        kwargs = {}
    elif mode == "missing":
        kwargs["conversion_path"] = tmp_path / "absent.docx"
    elif mode == "wrong_hash":
        kwargs["conversion_sha256"] = "0" * 64
    elif mode == "not_retained":
        document.blocks[0].source_locator["conversion_artifact_retained"] = False
    with pytest.raises(ProvenanceError, match="conversion"):
        verify(document, **kwargs)


def test_conversion_keeps_original_identity_and_physical_derivative(tmp_path):
    document, derived, digest = converted(tmp_path)
    result = verify(document, conversion_path=derived, conversion_sha256=digest)
    assert result[document.blocks[0].block_id].span.source_id == identity(document).source_id
    assert result[document.blocks[0].block_id].span.xml_part == "word/document.xml"
    assert result[document.blocks[0].block_id].literal_text == "CONTRATO SINTÉTICO"


def test_conversion_digest_must_be_bound_into_source_identity(tmp_path):
    document, derived, digest = converted(tmp_path)
    with pytest.raises(ProvenanceError, match="conversion_identity_missing"):
        verify_document_provenance(document, identity(document), original_path=document.file_path, conversion_path=derived, conversion_sha256=digest)


def test_docx_whitespace_tabs_and_cell_paragraphs_are_not_lost(tmp_path):
    content = '<w:p><w:r><w:t xml:space="preserve">  Não </w:t><w:tab/><w:t>pagar</w:t><w:br/><w:t xml:space="preserve"> salvo condição.  </w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Condições</w:t></w:r></w:p></w:tc></w:tr><w:tr><w:tc><w:p><w:r><w:t xml:space="preserve">  Primeira  </w:t></w:r></w:p><w:p/><w:p><w:r><w:t xml:space="preserve">  Segunda  </w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
    document = candidate(docx(tmp_path, content=content))
    result = verify(document)
    paragraph = result[document.blocks[0].block_id]
    assert paragraph.literal_text == "  Não \tpagar\n salvo condição.  "
    assert paragraph.span.end == len(paragraph.literal_text)
    cell = result[document.blocks[-1].block_id]
    assert cell.literal_text == "  Primeira  \n\n  Segunda  "
    assert cell.synthetic_context == "Condições: "


def test_measured_pdf_bbox_is_accepted_and_differs_from_page_dimensions(tmp_path):
    document = candidate(pdf(tmp_path), parser="pdf_parser")
    result = verify(document)
    for block in document.blocks:
        actual = result[block.block_id].span.bbox
        assert actual is not None
        assert actual[0] > 0 and actual[2] < 612
        block.source_locator["bbox"] = list(actual)
    assert verify(document) == result


def test_native_pdf_wrapped_lines_remain_literal_instead_of_joined_spaces(tmp_path):
    document = candidate(pdf(tmp_path, continuation="a exceção permanece literalmente preservada."), parser="pdf_parser")
    result = verify(document)
    assert len(result) == 2
    for block in document.blocks:
        physical = result[block.block_id]
        assert "\na exceção" in physical.literal_text
        assert " a exceção" in block.text_raw
        assert physical.literal_text.replace("\n", " ") == block.text_raw
        assert physical.span.end == len(physical.literal_text)


@pytest.mark.parametrize("value", [3, "invented", [1], [float("nan"), 0, 1, 2]])
def test_malformed_candidate_pdf_bbox_has_controlled_error(tmp_path, value):
    document = candidate(pdf(tmp_path), parser="pdf_parser")
    document.blocks[0].source_locator["bbox"] = value
    with pytest.raises(ProvenanceError, match="bbox"):
        verify(document)

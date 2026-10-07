"""Verify local physical artifacts; candidate locators never grant approval.

Offsets describe the physical literal returned here, not reconstructed numbering
or table labels. The complete parser output is compared before any block escapes.
OCR requires a retained proof contract which this gate does not yet implement.
"""
from dataclasses import dataclass
import hashlib
import io
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

import pypdf
import pypdfium2

from app.contracts import SourceIdentity, SourceSpan
from app.ingestion.docx_parser import (
    W_NS, _extract_cell_text, _extract_paragraph_body, _extract_text_excluding_del, _local_tag,
    extract_docx_blocks,
)
from app.ingestion.pdf_parser import (
    ParserState, _classify_block, _split_page_into_blocks,
)
from app.ingestion.schemas import BlockType, DocumentBlock, ParsedDocument


class ProvenanceError(ValueError):
    """Controlled failure: no partial verified document is returned."""

    def __init__(self, reason_code: str, detail: str):
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}")


@dataclass(frozen=True)
class VerifiedBlock:
    block_id: str
    literal_text: str
    synthetic_context: str
    span: SourceSpan


def _fail(code: str, detail: str):
    raise ProvenanceError(code, detail)


def _read_artifact(path: Path, *, code: str) -> bytes:
    try:
        if not path.is_file():
            _fail(code, "local artifact is missing or is not a file")
        content = path.read_bytes()
    except OSError as exc:
        _fail(code, f"local artifact cannot be read: {exc}")
    if not content:
        _fail(code, "local artifact is empty")
    return content


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _compare_blocks(document: ParsedDocument, expected: list[DocumentBlock]):
    if not document.blocks or len({b.block_id for b in document.blocks}) != len(document.blocks):
        _fail("block_set_mismatch", "missing or repeated block identifiers")
    if [b.block_id for b in document.blocks] != [b.block_id for b in expected]:
        _fail("block_set_mismatch", "complete ordered block set differs from physical artifact")
    for candidate, physical in zip(document.blocks, expected, strict=True):
        if candidate.model_dump(exclude={"source_locator"}) != physical.model_dump(exclude={"source_locator"}):
            _fail("block_content_mismatch", f"physical text/identity/structure/spans differ in {candidate.block_id}")
        supplied = dict(candidate.source_locator or {})
        locator = physical.source_locator or {}
        # This label is never an authorization input.
        supplied.pop("verification", None)
        wanted = {k: v for k, v in locator.items() if k != "verification"}
        bbox = supplied.pop("bbox", None)
        if supplied != wanted:
            _fail("locator_mismatch", f"physical locator differs in {candidate.block_id}")
        if bbox is not None:
            _fail("bbox_unverified", "coordinates are checked only after native PDF measurement")


def _paragraph_literal(element: ET.Element) -> str:
    # Reuse the parser's revision-aware extraction but preserve outer whitespace.
    return "".join(_extract_text_excluding_del(child) for child in element if _local_tag(child) != "pPr")


def _cell_literal(element: ET.Element) -> str:
    paragraphs = [_paragraph_literal(child) for child in element if _local_tag(child) == "p"]
    return "\n".join(paragraphs)


def _docx_verified(document, identity, path, content, *, converted=False):
    try:
        expected = extract_docx_blocks(path, document.doc_id, document.doc_version)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
        body = root.find(f"{{{W_NS}}}body")
        if body is None:
            _fail("docx_body_unavailable", "document has no physical XML body")
    except ProvenanceError:
        raise
    except (OSError, ValueError, KeyError, ET.ParseError, zipfile.BadZipFile) as exc:
        _fail("docx_artifact_invalid", str(exc))
    if converted:
        for block in expected:
            block.source_locator.update(format="converted_docx", original_format=Path(document.file_path).suffix.lower(), conversion_artifact_retained=True)
    _compare_blocks(document, expected)
    result = {}
    for block in expected:
        locator = block.source_locator
        body_index = locator["body_child_index"]
        element = body[body_index]
        coordinates = {"xml_part": "word/document.xml", "body_child_index": body_index}
        if _local_tag(element) == "p":
            literal = _paragraph_literal(element)
            canonical_literal = _extract_paragraph_body(element)
        else:
            rows = [child for child in element if _local_tag(child) == "tr"]
            if not rows:
                rows = element.findall(f".//{{{W_NS}}}tr")
            row, column = locator["row_index"], locator["column_index"]
            cells = [child for child in rows[row] if _local_tag(child) == "tc"]
            literal = _cell_literal(cells[column])
            canonical_literal = _extract_cell_text(cells[column])
            coordinates.update(row_index=row, column_index=column)
        if not literal.strip():
            _fail("synthetic_only_block", f"{block.block_id} has no literal physical text")
        # Parser canonicalization trims outer whitespace; retain those bytes in
        # the physical text. Any inserted prefix remains separate context.
        if locator["source_text"] != canonical_literal:
            _fail("physical_text_mismatch", f"XML text differs in {block.block_id}")
        if block.text_raw == canonical_literal:
            context = ""
        elif block.text_raw.endswith(canonical_literal):
            context = block.text_raw[:-len(canonical_literal)]
        else:
            _fail("physical_text_mismatch", f"canonical text is not an attributable literal in {block.block_id}")
        result[block.block_id] = VerifiedBlock(block.block_id, literal, context, SourceSpan(source_id=identity.source_id, block_id=block.block_id, start=0, end=len(literal), **coordinates))
    return result


def _native_literals(page_text: str, canonical: list[str]) -> list[str]:
    """Map logical parser blocks to whole physical extraction lines, in order."""
    lines = page_text.splitlines(keepends=True)
    cursor = 0
    literals = []
    for block in canonical:
        while cursor < len(lines) and not lines[cursor].strip():
            cursor += 1
        start = cursor
        joined = ""
        while cursor < len(lines):
            line = lines[cursor].strip()
            if line:
                joined = f"{joined} {line}" if joined else line
            cursor += 1
            if joined == block:
                break
            if not block.startswith(joined):
                _fail("pdf_literal_mapping_failed", "native text cannot be mapped without changing characters")
        if joined != block:
            _fail("pdf_literal_mapping_failed", "incomplete physical native block")
        # Newline at a block boundary is layout separation; newlines inside a
        # block are retained rather than replacing them with canonical spaces.
        literals.append("".join(lines[start:cursor]).rstrip("\r\n"))
    if any(line.strip() for line in lines[cursor:]):
        _fail("pdf_literal_mapping_failed", "unattributed physical page text remains")
    return literals


def _measure_pdf_boxes(content: bytes, page_index: int, literals: list[str]):
    """Return measured glyph bounds only when PDFium character mapping is exact.

    If engines disagree or a glyph cannot be measured, page/block identity still
    proves origin and bbox remains absent. No bounding box is guessed.
    """
    document = page = textpage = None
    unavailable = [None] * len(literals)
    try:
        document = pypdfium2.PdfDocument(content)
        page = document[page_index]
        textpage = page.get_textpage()
        raw = textpage.get_text_range()
        if len(raw) != textpage.count_chars():
            return unavailable
        positions = [i for i, char in enumerate(raw) if not char.isspace()]
        dense = "".join(raw[i] for i in positions)
        cursor = 0
        result = []
        for literal in literals:
            token = "".join(char for char in literal if not char.isspace())
            if not token or not dense.startswith(token, cursor):
                return unavailable
            boxes = [textpage.get_charbox(positions[i]) for i in range(cursor, cursor + len(token))]
            if any(not all(math.isfinite(coordinate) for coordinate in box) for box in boxes):
                return unavailable
            result.append((min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)))
            cursor += len(token)
        return result if cursor == len(dense) else unavailable
    except Exception:
        return unavailable
    finally:
        for resource in (textpage, page, document):
            if resource is not None:
                resource.close()


def _pdf_verified(document, identity, content):
    if any((block.source_locator or {}).get("extraction") == "ocr" for block in document.blocks):
        _fail("ocr_proof_unavailable", "OCR artifact and character-level proof were not retained")
    try:
        reader = pypdf.PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            _fail("pdf_artifact_invalid", "encrypted PDF cannot be verified")
        state = ParserState()
        expected, verified = [], {}
        for page_index, page in enumerate(reader.pages):
            page_text = page.extract_text() or ""
            canonical = _split_page_into_blocks(page_text, state)
            literals = _native_literals(page_text, canonical)
            boxes = _measure_pdf_boxes(content, page_index, literals)
            for page_block_index, (text, literal, bbox) in enumerate(zip(canonical, literals, boxes, strict=True)):
                order = len(expected)
                kind, level, label = _classify_block(text, state, order)
                block_id = f"doc_{document.doc_id}_blk_{order}"
                if kind == BlockType.CLAUSE:
                    state.current_clause_id = block_id
                    parent = None
                elif kind in (BlockType.PARAGRAPH, BlockType.ITEM, BlockType.SUBITEM):
                    parent = state.current_clause_id
                else:
                    parent = None
                expected.append(DocumentBlock(block_id=block_id, doc_id=document.doc_id, doc_version=document.doc_version, block_type=kind, hierarchy_level=level, hierarchy_label=label, parent_clause_id=parent, order_index=order, text_raw=text, text_search=re.sub(r"\s+", " ", text).strip(), spans=[{"start": 0, "end": len(text)}], source_locator={"format": "pdf", "page": page_index + 1, "page_block_index": page_block_index, "extraction": "native", "verification": "unreviewed"}))
                verified[block_id] = VerifiedBlock(block_id, literal, "", SourceSpan(source_id=identity.source_id, block_id=block_id, start=0, end=len(literal), page=page_index + 1, page_block_index=page_block_index, bbox=bbox))
    except ProvenanceError:
        raise
    except Exception as exc:
        _fail("pdf_artifact_invalid", str(exc))
    # Bbox is optional in legacy parser candidates. When supplied it must match
    # an actual measurement, not a field supplied by a caller.
    checked = document.model_copy(deep=True)
    for block in checked.blocks:
        bbox = (block.source_locator or {}).pop("bbox", None)
        if bbox is not None:
            physical = verified.get(block.block_id)
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4 or physical is None or physical.span.bbox is None or tuple(bbox) != physical.span.bbox:
                _fail("bbox_unverified", f"candidate bbox differs from measured glyphs in {block.block_id}")
    _compare_blocks(checked, expected)
    return verified


def verify_document_provenance(document: ParsedDocument, identity: SourceIdentity, *, original_path: Path | str, conversion_path: Path | str | None = None, conversion_sha256: str | None = None) -> dict[str, VerifiedBlock]:
    """Verify complete source against retained local bytes, or fail atomically.

    For conversions the original identity includes the derivative SHA-256. The
    caller must bind that identity into its scoped review digest and chunk key.
    """
    expected_source = f"doc:{document.doc_id}:v{document.doc_version}:file:{document.file_id}"
    if (not document.file_id or identity.instrument_id != f"doc:{document.doc_id}" or identity.source_id != expected_source or identity.doc_version != document.doc_version or identity.file_id != document.file_id or identity.file_hash != document.file_hash or identity.parser_name != document.parser_name or identity.parser_version != document.parser_version):
        _fail("source_identity_mismatch", "document and source identity differ")
    if document.parser_version != "1.0.0" or document.parser_name not in {"docx_parser", "pdf_parser", "legacy_parser"}:
        _fail("parser_identity_unsupported", "no verifier for the declared parser version")
    path = Path(original_path)
    if path.resolve() != Path(document.file_path).resolve():
        _fail("source_path_mismatch", "declared source path differs from original artifact")
    content = _read_artifact(path, code="source_artifact_missing")
    if _digest(content) != identity.file_hash:
        _fail("source_hash_mismatch", "original bytes differ from source identity")
    if document.status != "success":
        _fail("document_not_success", "technical parse did not succeed")
    if document.parser_name == "legacy_parser":
        if conversion_path is None or conversion_sha256 is None or identity.conversion_sha256 != conversion_sha256:
            _fail("conversion_identity_missing", "retained derivative and its identity SHA-256 are required")
        derived_path = Path(conversion_path)
        if derived_path.resolve() == path.resolve() or derived_path.suffix.lower() != ".docx":
            _fail("conversion_artifact_invalid", "derivative must be a distinct retained DOCX")
        derived = _read_artifact(derived_path, code="conversion_artifact_missing")
        if _digest(derived) != conversion_sha256:
            _fail("conversion_hash_mismatch", "retained derivative bytes differ from declared hash")
        if any((block.source_locator or {}).get("conversion_artifact_retained") is not True for block in document.blocks):
            _fail("conversion_not_retained", "candidate conversion was not retained")
        result = _docx_verified(document, identity, derived_path, derived, converted=True)
        if _read_artifact(derived_path, code="conversion_artifact_missing") != derived:
            _fail("conversion_hash_mismatch", "derivative changed during verification")
    else:
        if conversion_path is not None or conversion_sha256 is not None or identity.conversion_sha256 is not None:
            _fail("conversion_identity_unexpected", "native sources cannot declare a conversion")
        result = _docx_verified(document, identity, path, content) if document.parser_name == "docx_parser" else _pdf_verified(document, identity, content)
    if _read_artifact(path, code="source_artifact_missing") != content:
        _fail("source_hash_mismatch", "original changed during verification")
    return result

"""
PDF parsing engine with pypdf and surgical pypdfium2 rasterization for OCR.

Extracts canonical DocumentBlock instances from PDF documents using:
- pypdf for digital text extraction and encryption/header validation.
- pypdfium2 for high-resolution in-memory rasterization (scale=2.0) of scanned pages (< 50 chars).
- pytesseract for surgical OCR execution on rasterized pages, marking blocks with
  UncertaintyFlag.LOW_CONFIDENCE_OCR.
- Structural hierarchy and parent-child clause traceability.
"""

from dataclasses import dataclass
import logging
from pathlib import Path
import re
from typing import List, Optional, Tuple

import pypdf
import pypdfium2
import pytesseract

from app.ingestion.schemas import (
    BlockType,
    DocumentBlock,
    HierarchyLevel,
    UncertaintyFlag,
)

logger = logging.getLogger(__name__)

# --- Regular expressions for legal block classification ---

_SIG_REGEX = re.compile(
    r"^(?:E\s*,?\s*por\s+estarem\b|"
    r"E\s*,?\s*por\s+assim\s+estarem\b|"
    r"E\s*,?\s*por\s+estarem\s+de\s+pleno\s+acordo\b|"
    r"E\s*,?\s*por\s+estarem\s+justos?\s+e\s+contratados?\b|"
    r"E\s*,?\s*por\s+estarem\s+justas?\s+e\s+contratadas?\b|"
    r"Por\s+estarem\s+assim\s+justos\b|"
    r"Em\s+testemunho\s+do\s+que\b|"
    r"TESTEMUNHAS?:?|"
    r"ASSINATURAS?:?)",
    re.IGNORECASE,
)

_CLAUSE_REGEX = re.compile(
    r"^(?:CL[ÁA]USULA\s+(?:PRIMEIRA|SEGUNDA|TERCEIRA|QUARTA|QUINTA|SEXTA|SÉTIMA|SETIMA|OITAVA|NONA|DÉCIMA|DECIMA|[A-Z0-9ªº\.\-]+(?:\s+[A-Z0-9ªº\.\-]+)?)|CL[ÁA]USULA\b|[0-9]+[ªº]?\s*[\.\-–]\s*CL[ÁA]USULA)",
    re.IGNORECASE,
)

_PARA_REGEX = re.compile(
    r"^(?:PAR[ÁA]GRAFO\s+(?:[ÚU]NICO|[A-Z0-9ªº\.\-]+(?:\s+[A-Z0-9ªº\.\-]+)?)|§\s*[0-9]+[ºª\.]?|PAR[ÁA]GRAFO\b)",
    re.IGNORECASE,
)

_ITEM_REGEX = re.compile(
    r"^(?:[a-z]\)|[ivxlcdm]+\.|\([a-z0-9]\)|[0-9]+\.[0-9]+(?:\.[0-9]+)*)",
    re.IGNORECASE,
)

_PREAMBLE_REGEX = re.compile(
    r"^(?:Pelo\s+presente|Entre\s+as\s+partes|De\s+um\s+lado|Por\s+este\s+instrumento|São\s+partes|As\s+partes\s+acima)",
    re.IGNORECASE,
)

_TITLE_REGEX = re.compile(
    r"^(?:CONTRATO\b|INSTRUMENTO\s+PARTICULAR\b|TERMO\s+ADITIVO\b|ACORDO\b|PROCURAÇÃO\b|PROCURACAO\b|CONVÊNIO\b|CONVENIO\b|DECLARAÇÃO\b|DECLARACAO\b|ADITIVO\b|ESTATUTO\b|DISTRATO\b)",
    re.IGNORECASE,
)


@dataclass
class ParserState:
    """Tracks parser progression across document pages for hierarchy attribution."""

    has_seen_title: bool = False
    has_seen_preamble: bool = False
    has_seen_clause: bool = False
    in_signature_section: bool = False
    current_clause_id: Optional[str] = None


def _normalize_search_text(text: str) -> str:
    """Normalize text for lexical search by collapsing whitespaces."""
    return re.sub(r"\s+", " ", text).strip()


def _is_heading(line: str) -> bool:
    """Check if line is a structural heading that should stand as its own block."""
    stripped = line.strip()
    if _CLAUSE_REGEX.match(stripped):
        return True
    if _PARA_REGEX.match(stripped):
        return True
    if _SIG_REGEX.search(stripped):
        return True
    if _TITLE_REGEX.match(stripped):
        return True
    return False


def _split_page_into_blocks(page_text: str, state: ParserState) -> List[str]:
    """
    Split a page's extracted text into logical paragraph/clause blocks.

    Respects blank lines and structural headers, grouping wrapped lines.
    """
    lines = [ln.strip() for ln in page_text.splitlines()]
    blocks: List[str] = []
    current_lines: List[str] = []

    def flush_current():
        if current_lines:
            text = " ".join(current_lines).strip()
            if text:
                blocks.append(text)
            current_lines.clear()

    prev_was_heading = False

    for line in lines:
        if not line:
            # Blank line is an explicit block separator
            flush_current()
            prev_was_heading = False
            continue

        is_heading = _is_heading(line)
        is_item = bool(_ITEM_REGEX.match(line))
        is_sig = bool(_SIG_REGEX.search(line)) or state.in_signature_section
        is_preamble = (
            not state.has_seen_clause
            and (
                bool(_PREAMBLE_REGEX.match(line))
                or "inscrita no cnpj" in line.lower()
                or "inscrito no cpf" in line.lower()
            )
        )

        starts_lowercase = line[0].islower() if line else False

        # Lowercase line is a continuation unless it's an item or preamble
        if starts_lowercase and not is_item and not is_preamble:
            current_lines.append(line)
            prev_was_heading = False
            continue

        # If previous line was a heading, flush it before starting new block
        if prev_was_heading:
            flush_current()
            prev_was_heading = False

        if is_heading or is_item or is_sig or is_preamble:
            flush_current()
            current_lines.append(line)
            if is_heading:
                prev_was_heading = True
            else:
                prev_was_heading = False
        else:
            current_lines.append(line)

    flush_current()
    return blocks


def _classify_block(
    text: str,
    state: ParserState,
    order_index: int,
) -> Tuple[BlockType, HierarchyLevel, Optional[str]]:
    """Determine BlockType, HierarchyLevel, and hierarchy_label for a text block."""
    # 1. Signatures
    if state.in_signature_section:
        return BlockType.SIGNATURE, HierarchyLevel.SIGNATURE, None

    if _SIG_REGEX.search(text):
        state.in_signature_section = True
        state.current_clause_id = None
        return BlockType.SIGNATURE, HierarchyLevel.SIGNATURE, None

    # 2. Clauses
    m_clause = _CLAUSE_REGEX.match(text)
    if m_clause:
        state.has_seen_clause = True
        label = m_clause.group(0).strip().rstrip("-:–").strip()
        return BlockType.CLAUSE, HierarchyLevel.CLAUSE, label

    # 3. Explicit paragraphs (§, Parágrafo)
    m_para = _PARA_REGEX.match(text)
    if m_para:
        label = m_para.group(0).strip().rstrip("-:–").strip()
        return BlockType.PARAGRAPH, HierarchyLevel.PARAGRAPH, label

    # 4. Items (a), 1.1, (i))
    m_item = _ITEM_REGEX.match(text)
    if m_item:
        label = m_item.group(0).strip()
        return BlockType.ITEM, HierarchyLevel.ITEM, label

    # 5. Pre-clause preamble or title
    if not state.has_seen_clause:
        if (
            _PREAMBLE_REGEX.match(text)
            or "inscrita no cnpj" in text.lower()
            or "inscrito no cpf" in text.lower()
        ):
            state.has_seen_preamble = True
            return BlockType.PREAMBLE, HierarchyLevel.PREAMBLE, None

        if not state.has_seen_preamble and (_TITLE_REGEX.match(text) or order_index == 0):
            state.has_seen_title = True
            return BlockType.TITLE, HierarchyLevel.TITLE, None

        if state.has_seen_preamble:
            return BlockType.PREAMBLE, HierarchyLevel.PREAMBLE, None

    # 6. Default paragraph
    return BlockType.PARAGRAPH, HierarchyLevel.PARAGRAPH, None


def extract_pdf_blocks(
    pdf_path: Path | str,
    doc_id: int,
    doc_version: int = 1,
    ocr_min_chars: int = 50,
    ocr_lang: str = "por",
) -> List[DocumentBlock]:
    """
    Extract canonical DocumentBlock instances from a PDF file.

    Features:
    - Validates file existence and PDF header integrity (%PDF-).
    - Rejects encrypted / password-protected PDFs.
    - Native digital extraction via pypdf when text length >= ocr_min_chars.
    - Surgical OCR fallback via pypdfium2 rasterization (scale=2.0) and pytesseract
      when text length < ocr_min_chars, tagging blocks with UncertaintyFlag.LOW_CONFIDENCE_OCR.
    - Proper resource cleanup ensuring pypdfium2 instances are closed.
    - Logical splitting and hierarchical classification (TITLE, PREAMBLE, CLAUSE,
      PARAGRAPH, ITEM, SIGNATURE) with parent clause tracking.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If file is empty, corrupted, missing %PDF- header, or encrypted.
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"Arquivo PDF inexistente: {pdf_path}")
    if not path.is_file():
        raise ValueError(f"O caminho informado não é um arquivo: {pdf_path}")
    if path.stat().st_size == 0:
        raise ValueError(f"Arquivo PDF vazio: {pdf_path}")

    # Validate PDF header integrity
    try:
        with open(path, "rb") as f:
            header = f.read(1024)
    except OSError as e:
        raise ValueError(f"Erro ao ler cabeçalho do arquivo PDF: {e}") from e

    if not header.lstrip().startswith(b"%PDF-"):
        raise ValueError(f"Arquivo PDF corrompido ou cabeçalho inválido: {pdf_path}")

    # Load via pypdf
    try:
        reader = pypdf.PdfReader(str(path))
        if reader.is_encrypted:
            raise ValueError("PDF criptografado não suportado")
    except pypdf.errors.FileNotDecryptedError as e:
        raise ValueError("PDF criptografado não suportado") from e
    except ValueError:
        raise
    except Exception as e:
        if "encrypt" in str(e).lower():
            raise ValueError("PDF criptografado não suportado") from e
        raise ValueError(f"Arquivo PDF corrompido ou inválido: {e}") from e

    if len(reader.pages) == 0:
        raise ValueError("PDF não contém páginas")

    blocks: List[DocumentBlock] = []
    state = ParserState()
    current_order_index = 0

    for page_idx, page in enumerate(reader.pages):
        try:
            native_text = page.extract_text() or ""
        except Exception as e:
            logger.warning("Falha ao extrair texto nativo da página %d: %s", page_idx, e)
            native_text = ""

        is_scanned = len(native_text.strip()) < ocr_min_chars
        page_text = native_text
        page_uncertainty_flags: List[UncertaintyFlag] = []

        if is_scanned:
            # Trigger surgical OCR via pypdfium2 and pytesseract
            page_uncertainty_flags.append(UncertaintyFlag.LOW_CONFIDENCE_OCR)
            ocr_text = ""
            doc_ium = None
            try:
                doc_ium = pypdfium2.PdfDocument(str(path))
                page_ium = doc_ium[page_idx]
                pil_image = page_ium.render(scale=2.0).to_pil()
                ocr_text = pytesseract.image_to_string(pil_image, lang=ocr_lang) or ""
            except (pytesseract.TesseractNotFoundError, Exception) as e:
                logger.warning(
                    "OCR falhou ou Tesseract indisponível na página %d de '%s': %s",
                    page_idx,
                    path,
                    e,
                )
                ocr_text = native_text
            finally:
                if doc_ium is not None:
                    try:
                        doc_ium.close()
                    except Exception:
                        pass

            page_text = ocr_text if ocr_text.strip() else native_text

        # Split page text into blocks
        page_block_texts = _split_page_into_blocks(page_text, state)

        for text_raw in page_block_texts:
            if not text_raw.strip():
                continue

            block_type, hier_level, detected_label = _classify_block(
                text_raw, state, current_order_index
            )

            block_id = f"doc_{doc_id}_blk_{current_order_index}"

            if block_type == BlockType.CLAUSE:
                state.current_clause_id = block_id
                parent_clause_id = None
            elif block_type in (
                BlockType.PARAGRAPH,
                BlockType.ITEM,
                BlockType.SUBITEM,
            ):
                parent_clause_id = state.current_clause_id
            else:
                parent_clause_id = None

            text_search = _normalize_search_text(text_raw)

            block = DocumentBlock(
                block_id=block_id,
                doc_id=doc_id,
                doc_version=doc_version,
                block_type=block_type,
                hierarchy_level=hier_level,
                hierarchy_label=detected_label,
                parent_clause_id=parent_clause_id,
                order_index=current_order_index,
                text_raw=text_raw,
                text_search=text_search,
                table_metadata=None,
                spans=[{"start": 0, "end": len(text_raw)}],
                uncertainty_flags=list(page_uncertainty_flags),
            )
            blocks.append(block)
            current_order_index += 1

    return blocks


__all__ = ["ParserState", "extract_pdf_blocks"]

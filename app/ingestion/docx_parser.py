"""
Native DOCX parsing engine with numbering resolution, track changes, comments, and table extraction.

Extracts canonical DocumentBlock instances from OOXML documents using Python's
built-in zipfile and xml.etree.ElementTree. Reconstructs visible numbering from
word/numbering.xml and detects revisions (w:ins, w:del) and comments.
"""

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from app.ingestion.schemas import (
    BlockType,
    DocumentBlock,
    HierarchyLevel,
    UncertaintyFlag,
)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NAMESPACES = {"w": W_NS}


def _local_tag(elem: ET.Element) -> str:
    """Return local tag name stripped of XML namespace."""
    if elem.tag.startswith("{"):
        return elem.tag.split("}", 1)[1]
    return elem.tag


def _get_w_attr(elem: ET.Element, attr_name: str) -> Optional[str]:
    """Retrieve attribute value supporting namespaced or unnamespaced variations."""
    val = elem.get(f"{{{W_NS}}}{attr_name}")
    if val is not None:
        return val
    val = elem.get(f"w:{attr_name}")
    if val is not None:
        return val
    return elem.get(attr_name)


def _to_roman(n: int) -> str:
    """Convert a positive integer to Roman numerals."""
    if n <= 0:
        return str(n)
    val = [1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1]
    syb = ["M", "CM", "D", "CD", "C", "XC", "L", "XL", "X", "IX", "V", "IV", "I"]
    roman_num = ""
    i = 0
    while n > 0 and i < len(val):
        for _ in range(n // val[i]):
            roman_num += syb[i]
            n -= val[i]
        i += 1
    return roman_num


def _to_letter(n: int) -> str:
    """Convert a positive integer to bijective base-26 letters (A, B... Z, AA...)."""
    if n <= 0:
        return str(n)
    result = []
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result.append(chr(ord("A") + rem))
    return "".join(reversed(result))


def _format_number(val: int, num_fmt: str) -> str:
    """Format an integer counter according to OOXML numFmt string."""
    fmt = (num_fmt or "decimal").lower().strip()
    if fmt == "decimal":
        return str(val)
    elif fmt == "upperroman":
        return _to_roman(val)
    elif fmt == "lowerroman":
        return _to_roman(val).lower()
    elif fmt == "upperletter":
        return _to_letter(val)
    elif fmt == "lowerletter":
        return _to_letter(val).lower()
    elif fmt == "ordinal":
        return f"{val}º"
    elif fmt in ("bullet", "none"):
        return ""
    return str(val)


def _normalize_search_text(text: str) -> str:
    """Normalize text for lexical search by collapsing whitespaces."""
    return re.sub(r"\s+", " ", text).strip()


class NumberingResolver:
    """Reconstructs hierarchical numbering states from word/numbering.xml."""

    def __init__(self, numbering_xml_bytes: Optional[bytes] = None):
        self.num_map: Dict[str, str] = {}  # numId -> abstractNumId
        # abstractNumId -> {ilvl: {"start": int, "numFmt": str, "lvlText": str}}
        self.abstract_nums: Dict[str, Dict[int, Dict[str, Any]]] = {}
        # numId -> {ilvl: current_int_value}
        self.counters: Dict[str, Dict[int, int]] = {}
        # numId -> active initialized ilvls
        self.active_levels: Dict[str, Set[int]] = {}

        if numbering_xml_bytes:
            self._parse(numbering_xml_bytes)

    def _parse(self, xml_bytes: bytes) -> None:
        try:
            root = ET.fromstring(xml_bytes)
        except Exception:
            return

        for child in root:
            tag = _local_tag(child)
            if tag == "abstractNum":
                abs_id = _get_w_attr(child, "abstractNumId")
                if not abs_id:
                    continue
                levels: Dict[int, Dict[str, Any]] = {}
                for lvl in child:
                    if _local_tag(lvl) == "lvl":
                        ilvl_str = _get_w_attr(lvl, "ilvl")
                        if ilvl_str is None:
                            continue
                        try:
                            ilvl = int(ilvl_str)
                        except ValueError:
                            continue

                        start_val = 1
                        num_fmt = "decimal"
                        lvl_text = f"%{ilvl + 1}."

                        for prop in lvl:
                            ptag = _local_tag(prop)
                            if ptag == "start":
                                s_val = _get_w_attr(prop, "val")
                                if s_val and s_val.isdigit():
                                    start_val = int(s_val)
                            elif ptag == "numFmt":
                                f_val = _get_w_attr(prop, "val")
                                if f_val:
                                    num_fmt = f_val
                            elif ptag == "lvlText":
                                t_val = _get_w_attr(prop, "val")
                                if t_val is not None:
                                    lvl_text = t_val

                        levels[ilvl] = {
                            "start": start_val,
                            "numFmt": num_fmt,
                            "lvlText": lvl_text,
                        }
                self.abstract_nums[abs_id] = levels

            elif tag == "num":
                num_id = _get_w_attr(child, "numId")
                if not num_id:
                    continue
                for elem in child:
                    if _local_tag(elem) == "abstractNumId":
                        abs_ref = _get_w_attr(elem, "val")
                        if abs_ref:
                            self.num_map[num_id] = abs_ref

    def resolve_number(
        self, num_id: str, ilvl: int
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Increment state counter and format visible numbering string.
        Returns:
            (success, resolved_prefix, hierarchy_label)
        """
        if num_id not in self.num_map:
            return False, None, None

        abs_id = self.num_map[num_id]
        if abs_id not in self.abstract_nums or ilvl not in self.abstract_nums[abs_id]:
            return False, None, None

        if num_id not in self.counters:
            self.counters[num_id] = {}
            self.active_levels[num_id] = set()

        lvl_def = self.abstract_nums[abs_id][ilvl]
        start_val = lvl_def["start"]

        if ilvl not in self.active_levels[num_id]:
            self.counters[num_id][ilvl] = start_val
            self.active_levels[num_id].add(ilvl)
        else:
            self.counters[num_id][ilvl] += 1

        # Reset deeper levels (> ilvl)
        to_reset = [l for l in self.active_levels[num_id] if l > ilvl]
        for l in to_reset:
            self.active_levels[num_id].remove(l)
            l_start = self.abstract_nums[abs_id].get(l, {}).get("start", 1)
            self.counters[num_id][l] = l_start

        # Reconstruct formatted text
        lvl_text = lvl_def["lvlText"]
        placeholders = re.findall(r"%([1-9])", lvl_text)
        single_pct1_under_sublevel = placeholders == ["1"] and ilvl > 0

        resolved = lvl_text
        for ph in set(placeholders):
            k = int(ph)
            target_lvl = ilvl if (single_pct1_under_sublevel and k == 1) else (k - 1)
            val = self.counters[num_id].get(
                target_lvl,
                self.abstract_nums[abs_id].get(target_lvl, {}).get("start", 1),
            )
            fmt = self.abstract_nums[abs_id].get(target_lvl, {}).get(
                "numFmt", "decimal"
            )
            fmt_str = _format_number(val, fmt)
            resolved = resolved.replace(f"%{ph}", fmt_str)

        hierarchy_label = resolved.strip().rstrip("-:–").strip()
        return True, resolved, hierarchy_label


def _extract_text_excluding_del(elem: ET.Element) -> str:
    """Extract text from an element while ignoring deleted content (w:del, w:moveFrom)."""
    tag = _local_tag(elem)
    if tag in ("del", "moveFrom"):
        return ""
    parts = []
    if tag == "t":
        if elem.text:
            parts.append(elem.text)
    elif tag == "tab":
        parts.append("\t")
    elif tag == "br":
        parts.append("\n")

    for child in elem:
        parts.append(_extract_text_excluding_del(child))
    return "".join(parts)


def _extract_paragraph_body(p_elem: ET.Element) -> str:
    """Extract body text from paragraph children, skipping paragraph properties."""
    parts = []
    for child in p_elem:
        if _local_tag(child) == "pPr":
            continue
        parts.append(_extract_text_excluding_del(child))
    return "".join(parts).strip()


def _extract_element_uncertainty_flags(elem: ET.Element) -> List[UncertaintyFlag]:
    """Detect revision tags (w:ins, w:del) and comment anchors in element tree."""
    flags: Set[UncertaintyFlag] = set()
    for node in elem.iter():
        tag = _local_tag(node)
        if tag in ("ins", "del", "moveFrom", "moveTo", "pPrChange", "rPrChange"):
            flags.add(UncertaintyFlag.TRACK_CHANGES_PRESENT)
        elif tag in ("commentRangeStart", "commentRangeEnd", "commentReference"):
            flags.add(UncertaintyFlag.HAS_COMMENTS)
    return list(flags)


@dataclass
class ParserState:
    current_clause_id: Optional[str] = None
    has_seen_title: bool = False
    has_seen_preamble: bool = False
    has_seen_clause: bool = False
    in_signature_section: bool = False


_SIG_REGEX = re.compile(
    r"^(?:E\s*,?\s*por\s+estarem\b|"
    r"E\s*,?\s*por\s+assim\s+estarem\b|"
    r"E\s*,?\s*por\s+estarem\s+de\s+pleno\s+acordo\b|"
    r"E\s*,?\s*por\s+estarem\s+justos\s+e\s+contratados\b|"
    r"E\s*,?\s*por\s+estarem\s+justas\s+e\s+contratadas\b|"
    r"Por\s+estarem\s+assim\s+justos\b|"
    r"Em\s+testemunho\s+do\s+que\b|"
    r"TESTEMUNHAS?:?|"
    r"ASSINATURAS?:?)",
    re.IGNORECASE,
)

_CLAUSE_REGEX = re.compile(
    r"^(?:CL[ÁA]USULA\s+[A-Z0-9ªº\.\-]+|CL[ÁA]USULA\b|[0-9]+[ªº]?\s*[\.\-–]\s*CL[ÁA]USULA)",
    re.IGNORECASE,
)

_PARA_REGEX = re.compile(
    r"^(?:PAR[ÁA]GRAFO\s+(?:[A-Z0-9ªº\.\-]+|ÚNICO|UNICO)|§\s*[0-9]+[ºª\.]?)",
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
    r"^(?:CONTRATO\b|INSTRUMENTO\s+PARTICULAR\b|TERMO\s+ADITIVO\b|ACORDO\b|PROCURAÇÃO\b|CONVÊNIO\b|DECLARAÇÃO\b|ADITIVO\b|ESTATUTO\b|DISTRATO\b)",
    re.IGNORECASE,
)


def _classify_paragraph(
    text: str,
    ilvl: Optional[int],
    resolved_label: Optional[str],
    state: ParserState,
    order_index: int,
) -> Tuple[BlockType, HierarchyLevel, Optional[str]]:
    """Determine BlockType, HierarchyLevel, and hierarchy_label for a paragraph."""
    # 1. Signatures
    if state.in_signature_section:
        return BlockType.SIGNATURE, HierarchyLevel.SIGNATURE, None

    if _SIG_REGEX.search(text):
        state.in_signature_section = True
        state.current_clause_id = None
        return BlockType.SIGNATURE, HierarchyLevel.SIGNATURE, None

    # 2. Numbered via numbering.xml
    if ilvl is not None:
        if ilvl == 0:
            state.has_seen_clause = True
            return BlockType.CLAUSE, HierarchyLevel.CLAUSE, resolved_label
        elif ilvl == 1:
            return BlockType.PARAGRAPH, HierarchyLevel.PARAGRAPH, resolved_label
        elif ilvl == 2:
            return BlockType.ITEM, HierarchyLevel.ITEM, resolved_label
        else:
            return BlockType.SUBITEM, HierarchyLevel.SUBITEM, resolved_label

    # 3. Explicit patterns in unnumbered text
    m_clause = _CLAUSE_REGEX.match(text)
    if m_clause:
        state.has_seen_clause = True
        label = m_clause.group(0).strip().rstrip("-:–").strip()
        return BlockType.CLAUSE, HierarchyLevel.CLAUSE, label

    m_para = _PARA_REGEX.match(text)
    if m_para:
        label = m_para.group(0).strip().rstrip("-:–").strip()
        return BlockType.PARAGRAPH, HierarchyLevel.PARAGRAPH, label

    m_item = _ITEM_REGEX.match(text)
    if m_item:
        label = m_item.group(0).strip()
        return BlockType.ITEM, HierarchyLevel.ITEM, label

    if not state.has_seen_clause:
        if _PREAMBLE_REGEX.match(text) or "inscrita no cnpj" in text.lower() or "inscrito no cpf" in text.lower():
            state.has_seen_preamble = True
            return BlockType.PREAMBLE, HierarchyLevel.PREAMBLE, None

        if not state.has_seen_preamble and (_TITLE_REGEX.match(text) or order_index == 0):
            state.has_seen_title = True
            return BlockType.TITLE, HierarchyLevel.TITLE, None

        if state.has_seen_preamble:
            return BlockType.PREAMBLE, HierarchyLevel.PREAMBLE, None

    return BlockType.PARAGRAPH, HierarchyLevel.PARAGRAPH, None


def _extract_cell_text(tc_elem: ET.Element) -> str:
    """Extract and join all paragraph texts inside a table cell."""
    p_texts = []
    for child in tc_elem:
        if _local_tag(child) == "p":
            t = _extract_paragraph_body(child)
            if t:
                p_texts.append(t)
    return "\n".join(p_texts).strip()


def extract_docx_blocks(
    docx_path: Path | str, doc_id: int, doc_version: int = 1
) -> List[DocumentBlock]:
    """
    Extract canonical DocumentBlock instances from a DOCX file.

    Performs:
    - Integrity and zip validation.
    - Multilevel numbering reconstruction from word/numbering.xml.
    - Revision detection (w:ins, w:del) with text exclusion for deletions.
    - Comment detection (w:commentRangeStart and word/comments.xml).
    - Structured table cell extraction with column header linking.

    Raises:
        ValueError: If file is missing, empty, not a zip, or missing document.xml.
    """
    path = Path(docx_path)
    if not path.exists():
        raise ValueError(f"Arquivo DOCX inexistente: {docx_path}")
    if path.stat().st_size == 0:
        raise ValueError(f"Arquivo DOCX vazio: {docx_path}")
    if not zipfile.is_zipfile(path):
        raise ValueError(f"Arquivo DOCX corrompido ou inválido: {docx_path}")

    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = set(zf.namelist())
            if "word/document.xml" not in names:
                raise ValueError(f"Arquivo DOCX sem word/document.xml: {docx_path}")
            doc_xml_bytes = zf.read("word/document.xml")
            num_xml_bytes = zf.read("word/numbering.xml") if "word/numbering.xml" in names else None
            comments_xml_bytes = zf.read("word/comments.xml") if "word/comments.xml" in names else None
    except (zipfile.BadZipFile, OSError, EOFError) as e:
        raise ValueError(f"Arquivo DOCX corrompido ou inválido: {e}") from e

    # Check presence of comments in word/comments.xml
    has_comments_file = False
    if comments_xml_bytes:
        try:
            c_root = ET.fromstring(comments_xml_bytes)
            if any(_local_tag(c) == "comment" for c in c_root):
                has_comments_file = True
        except Exception:
            pass

    resolver = NumberingResolver(num_xml_bytes)

    try:
        doc_root = ET.fromstring(doc_xml_bytes)
    except Exception as e:
        raise ValueError(f"Erro ao analisar word/document.xml: {e}") from e

    body = None
    for child in doc_root:
        if _local_tag(child) == "body":
            body = child
            break
    if body is None:
        return []

    blocks: List[DocumentBlock] = []
    state = ParserState()
    current_order_index = 0

    for body_child_index, elem in enumerate(body):
        tag = _local_tag(elem)

        if tag == "p":
            body_text = _extract_paragraph_body(elem)
            flags = _extract_element_uncertainty_flags(elem)

            # Check numbering properties
            num_id: Optional[str] = None
            ilvl: Optional[int] = None
            for p_child in elem:
                if _local_tag(p_child) == "pPr":
                    for pr_elem in p_child:
                        if _local_tag(pr_elem) == "numPr":
                            for n_elem in pr_elem:
                                ntag = _local_tag(n_elem)
                                if ntag == "numId":
                                    num_id = _get_w_attr(n_elem, "val")
                                elif ntag == "ilvl":
                                    i_val = _get_w_attr(n_elem, "val")
                                    if i_val and i_val.isdigit():
                                        ilvl = int(i_val)

            resolved_num: Optional[str] = None
            hierarchy_label: Optional[str] = None

            if num_id and num_id != "0":
                if ilvl is None:
                    ilvl = 0
                success, resolved_num, hierarchy_label = resolver.resolve_number(num_id, ilvl)
                if not success:
                    flags.append(UncertaintyFlag.UNRESOLVED_NUMBERING)
                    ilvl = None

            # Build text_raw with numbering prefix
            if resolved_num:
                if not body_text:
                    text_raw = resolved_num.strip()
                elif resolved_num.endswith(" "):
                    text_raw = f"{resolved_num}{body_text}"
                elif resolved_num.endswith(("-", "–", ":")):
                    text_raw = f"{resolved_num} {body_text}"
                else:
                    if body_text.startswith(("-", "–", ":", ".")):
                        text_raw = f"{resolved_num} {body_text}"
                    else:
                        text_raw = f"{resolved_num} - {body_text}"
            else:
                text_raw = body_text

            if not text_raw.strip():
                # Skip empty paragraphs
                continue

            block_type, hier_level, detected_label = _classify_paragraph(
                text_raw, ilvl, hierarchy_label, state, current_order_index
            )
            final_hierarchy_label = hierarchy_label or detected_label

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
                hierarchy_label=final_hierarchy_label,
                parent_clause_id=parent_clause_id,
                order_index=current_order_index,
                text_raw=text_raw,
                text_search=text_search,
                table_metadata=None,
                spans=[{"start": 0, "end": len(text_raw)}],
                source_locator={"format": "docx", "xml_part": "word/document.xml", "body_child_index": body_child_index, "verification": "unreviewed", "source_text": body_text, "numbering_prefix": resolved_num},
                uncertainty_flags=flags,
            )
            blocks.append(block)
            current_order_index += 1

        elif tag == "tbl":
            # Extract rows
            rows = [c for c in elem if _local_tag(c) == "tr"]
            if not rows:
                rows = elem.findall(f".//{{{W_NS}}}tr")
            if not rows:
                continue

            # Determine header row
            header_row_idx = 0
            for idx, r in enumerate(rows):
                for tr_child in r:
                    if _local_tag(tr_child) == "trPr":
                        for pr in tr_child:
                            if _local_tag(pr) == "tblHeader":
                                header_row_idx = idx
                                break

            header_cells = [c for c in rows[header_row_idx] if _local_tag(c) == "tc"]
            col_names = [_extract_cell_text(tc).strip() for tc in header_cells]

            for r_idx, row in enumerate(rows):
                cells = [c for c in row if _local_tag(c) == "tc"]
                for c_idx, tc in enumerate(cells):
                    cell_text = _extract_cell_text(tc).strip()
                    col_name = col_names[c_idx] if c_idx < len(col_names) else f"Coluna {c_idx + 1}"
                    flags = _extract_element_uncertainty_flags(tc)

                    if r_idx == header_row_idx:
                        text_raw = cell_text
                    else:
                        if col_name and cell_text:
                            text_raw = (
                                f"{col_name}: {cell_text}"
                                if not cell_text.startswith(f"{col_name}:")
                                else cell_text
                            )
                        else:
                            text_raw = cell_text or (f"{col_name}:" if col_name else "")

                    if not text_raw.strip():
                        continue

                    block_id = f"doc_{doc_id}_blk_{current_order_index}"
                    text_search = _normalize_search_text(text_raw)

                    table_meta = {
                        "row": r_idx,
                        "col": c_idx,
                        "header": col_name,
                    }

                    block = DocumentBlock(
                        block_id=block_id,
                        doc_id=doc_id,
                        doc_version=doc_version,
                        block_type=BlockType.TABLE,
                        hierarchy_level=HierarchyLevel.TABLE,
                        hierarchy_label=None,
                        parent_clause_id=state.current_clause_id,
                        order_index=current_order_index,
                        text_raw=text_raw,
                        text_search=text_search,
                        table_metadata=table_meta,
                        spans=[{"start": 0, "end": len(text_raw)}],
                        source_locator={"format": "docx", "xml_part": "word/document.xml", "body_child_index": body_child_index, "row_index": r_idx, "column_index": c_idx, "header_row_index": header_row_idx, "verification": "unreviewed", "source_text": cell_text, "header_text": col_name},
                        uncertainty_flags=flags,
                    )
                    blocks.append(block)
                    current_order_index += 1

    # If document has comments.xml but no block received HAS_COMMENTS, apply to all blocks
    if has_comments_file:
        if not any(UncertaintyFlag.HAS_COMMENTS in b.uncertainty_flags for b in blocks):
            for b in blocks:
                b.uncertainty_flags.append(UncertaintyFlag.HAS_COMMENTS)

    return blocks


__all__ = ["NumberingResolver", "extract_docx_blocks"]

"""
Legal Metadata Extractor Engine.

Deterministic and semantic extraction of canonical contract metadata from DocumentBlock sequences:
- Formal document title (epígrafe) and instrument type classification
- Contract parties qualification, role identification, and law firm detection
- Legal representative anti-pollution isolation (CNPJ preservation over director CPFs)
- Object summary extraction and length normalization (<= 250 chars)
- Execution date extraction from closure/signature blocks with ISO normalization
- Defensive resilience against atypical or non-standard document structures
"""

import re
from typing import List, Optional, Tuple

from app.ingestion.schemas import (
    BlockType,
    ContractMetadata,
    ContractParty,
    DocumentBlock,
    PartyRole,
)

# ---------------------------------------------------------------------------
# Regular Expressions: Identifiers (CNPJ / CPF)
# ---------------------------------------------------------------------------
_CNPJ_FORMATTED = re.compile(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b")
_CPF_FORMATTED = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")

_CNPJ_UNFORMATTED = re.compile(
    r"(?:CNPJ|C\.N\.P\.J\.)(?:\/MF)?\s*(?:sob\s+o\s+n[º°\.]*)?\s*(\d{14})\b",
    re.IGNORECASE,
)
_CPF_UNFORMATTED = re.compile(
    r"(?:CPF|C\.P\.F\.)(?:\/MF)?\s*(?:sob\s+o\s+n[º°\.]*)?\s*(\d{11})\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Regular Expressions: Representation markers (Anti-Pollution Boundary)
# ---------------------------------------------------------------------------
_REP_MARKER_REGEX = re.compile(
    r"\b(?:"
    r"neste\s+ato\s+representad[ao]"
    r"|representad[ao]\s+por"
    r"|representad[ao]\s+pel[ao]"
    r"|por\s+seu\s+(?:representante|procurador|administrador|sócio|diretor|presidente)"
    r"|por\s+seus\s+(?:representantes|procuradores|administradores|sócios|diretores)"
    r"|intermédio\s+de\s+seu"
    r")\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Regular Expressions: Preamble Closing & Splitters
# ---------------------------------------------------------------------------
_PREAMBLE_CLOSING_REGEX = re.compile(
    r"\b(?:"
    r"têm,?\s+entre\s+si,?\s+justo\s+e\s+(?:acordado|avençado|contratado)"
    r"|resolvem\s+celebrar"
    r"|celebram\s+o\s+presente"
    r"|acordam\s+o\s+seguinte"
    r"|mediante\s+as\s+seguintes\s+cláusulas"
    r"|que\s+se\s+regerá"
    r"|passam\s+a\s+pactuar"
    r")\b.*$",
    re.IGNORECASE | re.DOTALL,
)

_PARTY_SPLIT_REGEX = re.compile(
    r"(?:"
    r";\s*(?:e,?\s*(?:de\s+outro\s+lado|como\s+(?:interveniente(?:\s+anuente)?|anuente))?|de\s+outro\s+lado|do\s+outro\s+lado)?\s*,?\s*"
    r"|,\s*(?:e,?\s*)?(?:de\s+outro\s+lado|do\s+outro\s+lado)\s*,?\s*"
    r"|[,;]\s*(?:e,?\s*)?(?:como\s+)?(?:interveniente(?:\s+anuente)?|anuente)\s*,?\s*"
    r"|;\s*\n\s*"
    r"|\n\s*(?:E\b|E:\b|DE\s+OUTRO\s+LADO|DO\s+OUTRO\s+LADO|SEGUND[OA]:?|[0-9]+[.)\-–]|[IVX]+[.)\-–])\s*"
    r"|(?<=\bCONTRATANTE\b),\s*e\s+"
    r"|(?<=\bCONTRATADA\b),\s*e\s+"
    r"|(?<=\bLOCADOR\b),\s*e\s+"
    r"|(?<=\bLOCATÁRIA\b),\s*e\s+"
    r"|(?<=\bLOCATARIA\b),\s*e\s+"
    r"|(?<=\bLOCATÁRIO\b),\s*e\s+"
    r"|(?<=\bLOCATARIO\b),\s*e\s+"
    r"|(?<=\bVENDEDOR\b),\s*e\s+"
    r"|(?<=\bCOMPRADOR\b),\s*e\s+"
    r"|(?<=\bCEDENTE\b),\s*e\s+"
    r"|(?<=\bCESSIONÁRIA\b),\s*e\s+"
    r"|(?<=\bCESSIONARIA\b),\s*e\s+"
    r")",
    re.IGNORECASE,
)

_LEADING_NOISE_PATTERNS = [
    re.compile(r"^pelo\s+presente\s+(?:instrumento|contrato|termo)(?:\s+particular)?(?:,?\s*de\s+[^,]+)?,?\s*", re.IGNORECASE),
    re.compile(r"^(?:o\s+presente\s+contrato\s+é\s+firmado\s+entre\s*)", re.IGNORECASE),
    re.compile(r"^(?:são\s+partes\s+(?:neste|do|deste)\s+(?:aditivo|contrato|instrumento|acordo):?\s*)", re.IGNORECASE),
    re.compile(r"^(?:entre:?\s*|\be:?\s+)", re.IGNORECASE),
    re.compile(r"^(?:de\s+um\s+lado,?\s*|de\s+outro\s+lado,?\s*|do\s+outro\s+lado,?\s*|e,?\s+de\s+outro\s+lado,?\s*)", re.IGNORECASE),
    re.compile(r"^(?:(?:e,?\s*)?(?:como\s+)?(?:interveniente(?:\s+anuente)?|anuente),?\s*)", re.IGNORECASE),
    re.compile(r"^(?:primeir[oa](?:\s+parte)?:?\s*|segund[oa](?:\s+parte)?:?\s*|terceir[oa](?:\s+parte)?:?\s*)", re.IGNORECASE),
    re.compile(r"^(?:[0-9]+[.)\-–]\s*|[ivxIVX]+[.)\-–]\s*)", re.IGNORECASE),
    re.compile(r"^(?:como\s+(?:contratante|contratada),?\s*)", re.IGNORECASE),
    re.compile(r"^(?:cláusula\s+[0-9a-záéíóúâêôãõçªº\.\-–]+\s*[-–:]?\s*(?:das\s+partes)?\s*[-–:]?\s*)", re.IGNORECASE),
]

_NAME_DELIMITER_REGEX = re.compile(
    r"(?:"
    r"\s*[,–\-]\s*(?:"
    r"pessoa\s+jurídica"
    r"|pessoa\s+física"
    r"|inscrit[ao]"
    r"|com\s+inscrição"
    r"|com\s+sede"
    r"|sediad[ao]"
    r"|sociedade\s+simples"
    r"|sociedade\s+de\s+advogados"
    r"|sociedade\s+anônima"
    r"|sociedade\s+por\s+ações"
    r"|sociedade\s+limitada"
    r"|associação"
    r"|fundação"
    r"|brasileir[ao]"
    r"|portador[ao]"
    r"|neste\s+ato"
    r"|residente"
    r"|domiciliad[ao]"
    r"|maior"
    r"|solteir[ao]"
    r"|casad[ao]"
    r"|divorciad[ao]"
    r"|viúv[ao]"
    r"|empresári[ao]"
    r"|engenheir[ao]"
    r"|advogad[ao]"
    r"|médic[ao]"
    r"|professor[a]?"
    r"|cnpj"
    r"|c\.n\.p\.j\."
    r"|cpf"
    r"|c\.p\.f\."
    r"|doravante"
    r"|adiante"
    r")\b"
    r"|\s*\((?:CNPJ|CPF)"
    r")",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Regular Expressions: Role Extraction
# ---------------------------------------------------------------------------
_ROLE_EXPLICIT_PATTERNS = [
    (
        re.compile(
            r"(?:doravante\s+)?(?:denominad[ao]|designad[ao]|chamad[ao]|intitulad[ao]|tratad[ao])\s+"
            r"(?:simplesmente\s+|tão\s+somente\s+)?(?:de\s+|como\s+)?[\"“']?([a-záéíóúâêôãõç_-]+)[\"”']?",
            re.IGNORECASE,
        ),
        1,
    ),
    (
        re.compile(
            r"\bdoravante\s+(?:simplesmente\s+|tão\s+somente\s+)?[\"“']?([a-záéíóúâêôãõç_-]+)[\"”']?",
            re.IGNORECASE,
        ),
        1,
    ),
    (
        re.compile(
            r"\bna\s+qualidade\s+de\s+[\"“']?([a-záéíóúâêôãõç_-]+)[\"”']?",
            re.IGNORECASE,
        ),
        1,
    ),
    (
        re.compile(
            r"\bcomo\s+[\"“']?(contratante|contratada|locador|locatário|locatario|vendedor|comprador|anuente|interveniente)[\"”']?\b",
            re.IGNORECASE,
        ),
        1,
    ),
]

# ---------------------------------------------------------------------------
# Regular Expressions: Execution Date
# ---------------------------------------------------------------------------
_MONTHS_MAP = {
    "janeiro": 1,
    "fevereiro": 2,
    "março": 3,
    "marco": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}

_DATE_TEXT_PATTERN = re.compile(
    r"\b(?:aos\s+)?(\d{1,2})(?:º)?(?:\s+dias)?\s+(?:do\s+mês\s+)?de\s+([a-zA-ZçÇ]+)\s+de\s+(\d{4})\b",
    re.IGNORECASE,
)
_DATE_NUM_PATTERN = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b")
_DATE_ISO_PATTERN = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")

# ---------------------------------------------------------------------------
# Regular Expressions: Object Extraction
# ---------------------------------------------------------------------------
_OBJECT_HEADING_STRIP = re.compile(
    r"^(?:(?:CLÁUSULA\s+[A-Z0-9ªº\.\-–]+|DO\s+OBJETO|OBJETO(?:\s+DO\s+CONTRATO)?)\s*[-–:]?\s*)+",
    re.IGNORECASE,
)


def _clean_spaces(text: str) -> str:
    """Normalize repeated whitespace and newlines to a single space."""
    return re.sub(r"\s+", " ", text).strip()


def _extract_formal_title(blocks: List[DocumentBlock]) -> str:
    """Locate and return formal title from TITLE blocks or document top."""
    title_blocks = [b for b in blocks if b.block_type == BlockType.TITLE]
    if title_blocks:
        consecutive_titles = []
        for i, b in enumerate(title_blocks):
            if i == 0 or b.order_index == title_blocks[i - 1].order_index + 1:
                consecutive_titles.append(b.text_raw.strip())
            else:
                break
        combined = " ".join(consecutive_titles)
        return _clean_spaces(combined)

    # Fallback to first block if uppercase or contract header
    if blocks:
        first_raw = blocks[0].text_raw.strip()
        first_line = first_raw.splitlines()[0].strip()
        upper_line = first_line.upper()
        if any(h in upper_line for h in ("CONTRATO", "TERMO", "INSTRUMENTO", "ACORDO", "DISTRATO", "DECLARAÇÃO")):
            return _clean_spaces(first_line)
        if blocks[0].hierarchy_label:
            return blocks[0].hierarchy_label.strip()

    return "Não identificado"


def _classify_instrument_type(formal_title: str, object_summary: Optional[str], preamble_text: str) -> str:
    """Classify contract instrument type according to hierarchical taxonomy."""
    search_space = f"{formal_title} {object_summary or ''} {preamble_text[:300]}".upper()

    # Priority 1: Aditivo / Distrato
    if "ADITIVO" in search_space or "ADITAMENTO" in search_space:
        return "Aditivo"
    if "DISTRATO" in search_space or "RESCISÃO" in search_space or "RESCISAO" in search_space:
        return "Distrato"

    # Priority 2: Honorários / Serviços Advocatícios (check before generic services)
    if any(k in search_space for k in ("HONORÁRIO", "HONORARIO", "ADVOCATÍCI", "ADVOCATIC", "SERVIÇOS JURÍDICOS", "SERVICOS JURIDICOS")):
        return "Honorários"

    # Priority 3: Specific Contract Types
    if "ACORDO" in search_space or "TRANSAÇÃO" in search_space or "TRANSACAO" in search_space or "COMPOSIÇÃO" in search_space or "COMPOSICAO" in search_space:
        return "Acordo"
    if "LOCAÇÃO" in search_space or "LOCACAO" in search_space or "ALUGUEL" in search_space:
        return "Locação"
    if "COMODATO" in search_space:
        return "Comodato"
    if "COMPRA E VENDA" in search_space or "PROMESSA DE COMPRA" in search_space or "COMPROMISSO DE COMPRA" in search_space:
        return "Compra e Venda"
    if "PARCERIA" in search_space or "COOPERAÇÃO" in search_space or "COOPERACAO" in search_space or "JOINT VENTURE" in search_space:
        return "Parceria"

    # Priority 4: Prestação de Serviços
    if "PRESTAÇÃO DE SERVIÇOS" in search_space or "PRESTACAO DE SERVICOS" in search_space or "PRESTAÇÃO DE SERVIÇO" in search_space:
        return "Prestação de Serviços"

    return "Outro"


def _extract_identifiers(segment_text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract CNPJ or CPF preserving legal entity against representative contamination.

    Anti-Pollution Rule:
    1. If a CNPJ exists in the entity section (before representation markers) or anywhere
       in the party's qualification, the CNPJ is the primary identifier.
    2. Representatives' CPFs are strictly prevented from polluting the clean_identifier.
    3. If NO CNPJ is found, check for the party's own CPF (natural persons).
    """
    # 1. Split segment at representation marker if present
    rep_match = _REP_MARKER_REGEX.search(segment_text)
    if rep_match:
        principal_text = segment_text[: rep_match.start()]
        rep_text = segment_text[rep_match.start() :]
    else:
        principal_text = segment_text
        rep_text = ""

    # 2. Look for CNPJ in principal text first
    m_cnpj = _CNPJ_FORMATTED.search(principal_text)
    if m_cnpj:
        raw_val = m_cnpj.group(0)
        clean_val = re.sub(r"\D", "", raw_val)
        return clean_val, raw_val

    m_cnpj_unf = _CNPJ_UNFORMATTED.search(principal_text)
    if m_cnpj_unf:
        clean_val = m_cnpj_unf.group(1)
        return clean_val, clean_val

    # 3. If no CNPJ in principal text, search whole segment for CNPJ
    m_cnpj_seg = _CNPJ_FORMATTED.search(segment_text)
    if m_cnpj_seg:
        raw_val = m_cnpj_seg.group(0)
        clean_val = re.sub(r"\D", "", raw_val)
        return clean_val, raw_val

    m_cnpj_seg_unf = _CNPJ_UNFORMATTED.search(segment_text)
    if m_cnpj_seg_unf:
        clean_val = m_cnpj_seg_unf.group(1)
        return clean_val, clean_val

    # 4. If NO CNPJ exists anywhere, check for natural person CPF
    m_cpf = _CPF_FORMATTED.search(principal_text)
    if m_cpf:
        raw_val = m_cpf.group(0)
        clean_val = re.sub(r"\D", "", raw_val)
        return clean_val, raw_val

    m_cpf_unf = _CPF_UNFORMATTED.search(principal_text)
    if m_cpf_unf:
        clean_val = m_cpf_unf.group(1)
        return clean_val, clean_val

    m_cpf_seg = _CPF_FORMATTED.search(segment_text)
    if m_cpf_seg:
        raw_val = m_cpf_seg.group(0)
        clean_val = re.sub(r"\D", "", raw_val)
        return clean_val, raw_val

    m_cpf_seg_unf = _CPF_UNFORMATTED.search(segment_text)
    if m_cpf_seg_unf:
        clean_val = m_cpf_seg_unf.group(1)
        return clean_val, clean_val

    return None, None


def _map_role(role_word: str) -> PartyRole:
    """Map Portuguese role descriptor to canonical PartyRole enum."""
    norm = role_word.lower().strip()
    if "contratante" in norm:
        return PartyRole.CONTRATANTE
    if "contratad" in norm:
        return PartyRole.CONTRATADA
    if "locador" in norm:
        return PartyRole.LOCADOR
    if "locatari" in norm or "locatári" in norm:
        return PartyRole.LOCATARIO
    if "vendedor" in norm or "alienante" in norm:
        return PartyRole.VENDEDOR
    if "comprador" in norm or "adquirente" in norm:
        return PartyRole.COMPRADOR
    if "anuente" in norm or "interveniente" in norm:
        return PartyRole.ANUENTE
    return PartyRole.PARTE_GENERICA


def _extract_role(segment_text: str) -> PartyRole:
    """Detect PartyRole from party qualification segment."""
    for pattern, grp in _ROLE_EXPLICIT_PATTERNS:
        m = pattern.search(segment_text)
        if m:
            extracted = m.group(grp)
            role = _map_role(extracted)
            if role != PartyRole.PARTE_GENERICA:
                return role

    # Fallback: scan for standalone uppercase or keyword mentions
    lower = segment_text.lower()
    if re.search(r"\bcontratante\b", lower):
        return PartyRole.CONTRATANTE
    if re.search(r"\bcontratad[ao]s?\b", lower):
        return PartyRole.CONTRATADA
    if re.search(r"\blocador[a]?s?\b", lower):
        return PartyRole.LOCADOR
    if re.search(r"\blocatári[ao]s?\b|\blocatari[ao]s?\b", lower):
        return PartyRole.LOCATARIO
    if re.search(r"\bvendedor[a]?s?\b", lower):
        return PartyRole.VENDEDOR
    if re.search(r"\bcomprador[a]?s?\b", lower):
        return PartyRole.COMPRADOR
    if re.search(r"\banuente\b|\binterveniente\b", lower):
        return PartyRole.ANUENTE

    return PartyRole.PARTE_GENERICA


def _is_andrade_law_firm(name: str, segment_text: str) -> bool:
    """Check if party explicitly refers to Andrade Advogados."""
    name_lower = name.lower()
    if "andrade" not in name_lower:
        return False

    if any(t in name_lower for t in ("advogad", "advocac", "sociedade de advogad")):
        return True

    seg_lower = segment_text.lower()
    if "andrade advogad" in seg_lower or "andrade silva advogad" in seg_lower:
        return True

    return False


def _clean_party_name(raw_name: str) -> str:
    """Clean extracted party name from punctuation, legal noise, and quotes."""
    name = raw_name.strip().strip("\"'“”«»")
    name = name.strip(",-:–; ").strip()

    # Strip trailing period unless standard abbreviation like LTDA. or S.A.
    if name.endswith(".") and not any(name.upper().endswith(sfx) for sfx in ("LTDA.", "S.A.", "S/A.", "C.A.")):
        name = name[:-1].strip()

    return _clean_spaces(name)


def _extract_single_party(segment: str) -> Optional[ContractParty]:
    """Parse an individual preamble segment into a structured ContractParty."""
    cleaned_seg = _clean_spaces(segment).lstrip(",;:–- ").strip()
    if not cleaned_seg or len(cleaned_seg) < 3:
        return None

    # 1. Strip leading boilerplate noise
    text = cleaned_seg
    changed = True
    while changed:
        changed = False
        text = text.lstrip(",;:–- ").strip()
        for p in _LEADING_NOISE_PATTERNS:
            m = p.match(text)
            if m:
                text = text[m.end() :].lstrip(",;:–- ").strip()
                changed = True

    if not text:
        return None

    # 2. Extract Name up to first qualifying delimiter
    delimiter_match = _NAME_DELIMITER_REGEX.search(text)
    if delimiter_match:
        candidate_name = text[: delimiter_match.start()]
    else:
        # Fallback: take up to first comma or newline
        first_comma = text.find(",")
        if first_comma != -1:
            candidate_name = text[:first_comma]
        else:
            candidate_name = text.splitlines()[0]

    name = _clean_party_name(candidate_name)
    if not name or len(name) < 2:
        return None

    # 3. Extract Identifiers with Anti-Pollution rule
    clean_id, raw_id = _extract_identifiers(cleaned_seg)

    # 4. Extract Role
    role = _extract_role(cleaned_seg)

    # 5. Check Law Firm
    is_law = _is_andrade_law_firm(name, cleaned_seg)

    return ContractParty(
        name=name,
        role=role,
        clean_identifier=clean_id,
        raw_identifier=raw_id,
        is_law_firm=is_law,
    )


def _extract_parties(blocks: List[DocumentBlock]) -> List[ContractParty]:
    """Extract and qualify all contract parties from PREAMBLE or introductory blocks."""
    # Find preamble text
    preamble_blocks = [b for b in blocks if b.block_type == BlockType.PREAMBLE]
    if preamble_blocks:
        preamble_text = "\n".join(b.text_raw for b in preamble_blocks)
    else:
        # Fallback: inspect introductory blocks before first clause or first 2 blocks
        candidates = []
        for b in blocks[:3]:
            raw_low = b.text_raw.lower()
            if any(
                k in raw_low
                for k in (
                    "de um lado",
                    "entre as partes",
                    "inscrita no cnpj",
                    "inscrito no cpf",
                    "doravante denominada",
                    "são partes",
                    "das partes",
                )
            ):
                candidates.append(b.text_raw)
        preamble_text = "\n".join(candidates) if candidates else ""

    if not preamble_text.strip():
        return []

    # Strip preamble closing clause (e.g. "têm entre si justo e acordado...")
    preamble_body = _PREAMBLE_CLOSING_REGEX.sub("", preamble_text).strip()

    # Split into party segments
    raw_segments = _PARTY_SPLIT_REGEX.split(preamble_body)
    parties: List[ContractParty] = []

    for seg in raw_segments:
        if not seg or not seg.strip():
            continue
        party = _extract_single_party(seg)
        if party:
            parties.append(party)

    # Ensure parties with missing roles in standard bilateral are reasonably defaulted
    if len(parties) == 2:
        if parties[0].role == PartyRole.PARTE_GENERICA and parties[1].role == PartyRole.CONTRATADA:
            parties[0].role = PartyRole.CONTRATANTE
        elif parties[1].role == PartyRole.PARTE_GENERICA and parties[0].role == PartyRole.CONTRATANTE:
            parties[1].role = PartyRole.CONTRATADA

    return parties


def _extract_object_summary(blocks: List[DocumentBlock]) -> Optional[str]:
    """Locate the object clause and extract summary (up to 250 clean characters)."""
    candidate_text: Optional[str] = None

    # Pass 1: Look specifically for clauses containing "OBJETO" (excluding "DAS PARTES")
    for i, b in enumerate(blocks):
        if b.block_type not in (BlockType.CLAUSE, BlockType.PARAGRAPH):
            continue

        label_up = (b.hierarchy_label or "").upper()
        raw_start = b.text_raw[:120].upper()

        if "OBJETO" in label_up or "OBJETO" in raw_start:
            text = b.text_raw.strip()
            # If block is only a clause heading, take the following block
            if len(text) < 40 or re.match(r"^CLÁUSULA\s+[A-Z0-9ªº\.\-–\s:]+$", text, re.IGNORECASE):
                if i + 1 < len(blocks):
                    text = blocks[i + 1].text_raw.strip()

            candidate_text = text
            break

    # Pass 2: If no block explicitly titled "OBJETO", look for Cláusula 1ª / Primeira (unless it is about parties)
    if not candidate_text:
        for i, b in enumerate(blocks):
            if b.block_type not in (BlockType.CLAUSE, BlockType.PARAGRAPH):
                continue
            label_up = (b.hierarchy_label or "").upper()
            raw_start = b.text_raw[:120].upper()
            if ("PRIMEIRA" in label_up or "1ª" in label_up or "1" in label_up) and "PARTES" not in label_up and "PARTES" not in raw_start:
                text = b.text_raw.strip()
                if len(text) < 40 or re.match(r"^CLÁUSULA\s+[A-Z0-9ªº\.\-–\s:]+$", text, re.IGNORECASE):
                    if i + 1 < len(blocks):
                        text = blocks[i + 1].text_raw.strip()
                candidate_text = text
                break

    # Pass 3: Search for phrasing anywhere
    if not candidate_text:
        for b in blocks:
            if b.block_type in (BlockType.CLAUSE, BlockType.PARAGRAPH):
                if any(k in b.text_raw.lower() for k in ("tem por objeto", "constitui objeto", "objeto deste")):
                    candidate_text = b.text_raw.strip()
                    break

    if not candidate_text:
        return None

    # Strip heading prefixes like "CLÁUSULA PRIMEIRA - DO OBJETO:"
    cleaned = _OBJECT_HEADING_STRIP.sub("", candidate_text).strip()
    cleaned = _clean_spaces(cleaned)

    if not cleaned:
        return None

    # Limit to 250 characters
    if len(cleaned) > 250:
        truncated = cleaned[:247]
        last_space = truncated.rfind(" ")
        if last_space > 180:
            cleaned = truncated[:last_space] + "..."
        else:
            cleaned = truncated + "..."

    return cleaned


def _extract_execution_date(blocks: List[DocumentBlock]) -> Optional[str]:
    """Locate closing/signature blocks and extract execution date normalized to ISO YYYY-MM-DD."""
    # Priority 1: SIGNATURE blocks
    candidate_blocks = [b for b in blocks if b.block_type == BlockType.SIGNATURE]

    # Priority 2: Last 5 blocks of the document (reverse order)
    if not candidate_blocks:
        candidate_blocks = list(reversed(blocks[-5:]))
    else:
        candidate_blocks = list(reversed(candidate_blocks))

    for b in candidate_blocks:
        text = b.text_raw

        # 1. Por extenso: "10 de maio de 2024" or "aos 15 dias do mês de março de 2023"
        m_txt = _DATE_TEXT_PATTERN.search(text)
        if m_txt:
            day_str, month_str, year_str = m_txt.groups()
            month_num = _MONTHS_MAP.get(month_str.lower())
            if month_num:
                try:
                    day = int(day_str)
                    year = int(year_str)
                    if 1 <= day <= 31 and 1900 <= year <= 2100:
                        return f"{year:04d}-{month_num:02d}-{day:02d}"
                except ValueError:
                    pass

        # 2. DD/MM/YYYY
        m_num = _DATE_NUM_PATTERN.search(text)
        if m_num:
            day_str, month_str, year_str = m_num.groups()
            try:
                day = int(day_str)
                month = int(month_str)
                year = int(year_str)
                if 1 <= day <= 31 and 1 <= month <= 12 and 1900 <= year <= 2100:
                    return f"{year:04d}-{month:02d}-{day:02d}"
            except ValueError:
                pass

        # 3. ISO YYYY-MM-DD
        m_iso = _DATE_ISO_PATTERN.search(text)
        if m_iso:
            year_str, month_str, day_str = m_iso.groups()
            try:
                year = int(year_str)
                month = int(month_str)
                day = int(day_str)
                if 1 <= day <= 31 and 1 <= month <= 12 and 1900 <= year <= 2100:
                    return f"{year:04d}-{month:02d}-{day:02d}"
            except ValueError:
                pass

    return None


def extract_contract_metadata(blocks: List[DocumentBlock]) -> ContractMetadata:
    """
    Extract canonical structured ContractMetadata from parsed DocumentBlocks.

    Attends to strict legal fidelity:
    - Formal title and instrument taxonomy classification
    - Party segmentation and role attribution
    - Representative isolation (preventing director CPF pollution of entity CNPJ)
    - Law firm presence detection
    - Object clause summarization (<= 250 characters)
    - Execution date extraction normalized to ISO YYYY-MM-DD
    """
    if not blocks:
        return ContractMetadata(
            formal_title="Não identificado",
            instrument_type="Outro",
            parties=[],
            execution_date=None,
            object_summary=None,
            mfiles_divergence_flags=[],
        )

    # 1. Extract Formal Title
    formal_title = _extract_formal_title(blocks)

    # 2. Extract Parties
    parties = _extract_parties(blocks)

    # 3. Extract Object Summary
    object_summary = _extract_object_summary(blocks)

    # 4. Extract Execution Date
    execution_date = _extract_execution_date(blocks)

    # 5. Classify Instrument Type
    preamble_snip = " ".join(b.text_raw for b in blocks if b.block_type == BlockType.PREAMBLE)
    instrument_type = _classify_instrument_type(formal_title, object_summary, preamble_snip)

    # 6. Divergence / Anomaly Flags
    divergence_flags: List[str] = []
    if not parties:
        divergence_flags.append("parties_not_identified")

    return ContractMetadata(
        formal_title=formal_title,
        instrument_type=instrument_type,
        parties=parties,
        execution_date=execution_date,
        object_summary=object_summary,
        mfiles_divergence_flags=divergence_flags,
    )

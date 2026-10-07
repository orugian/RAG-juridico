"""
Unified Parsing Orchestrator and Document Reconciliation Engine.

Orchestrates multi-format parsing (.docx, .doc, .rtf, .wpd, .pdf) into canonical
ParsedDocument aggregates and reconciles M-Files metadata against the real legal
text extracted from contracts.

Enforces the Quarantine Gate: documents with severe discrepancies (such as client
mismatches or unresolvable missing parties in bilateral agreements) receive
status='review_metadata_mismatch', preventing unverified data from auto-indexing.
"""

import hashlib
import re
import unicodedata
from app.identifiers import normalize_identifier, extract_identifier_candidates
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.ingestion.docx_parser import extract_docx_blocks
from app.ingestion.legacy_parser import extract_legacy_blocks
from app.ingestion.metadata_extractor import extract_contract_metadata
from app.ingestion.pdf_parser import extract_pdf_blocks
from app.ingestion.schemas import (
    ContractMetadata,
    ContractParty,
    ParsedDocument,
    PartyRole,
)

# ---------------------------------------------------------------------------
# Divergence Flag Constants
# ---------------------------------------------------------------------------
DISCREPANCY_CLIENT_MISMATCH = "DISCREPANCY_CLIENT_MISMATCH"
FLAG_INSTRUMENT_TYPE_REFINED = "FLAG_INSTRUMENT_TYPE_REFINED"
FLAG_NO_PARTIES_FOUND = "FLAG_NO_PARTIES_FOUND"

# Generic M-Files class categories subject to textual refinement
GENERIC_MFILES_CLASSES = {
    "contrato",
    "contratos",
    "documento",
    "documentos",
    "outro documento",
    "outros documentos",
    "geral",
    "minuta",
    "instrumento particular",
    "instrumento",
}

# Specific contract types that prevail over generic M-Files classes
REFINED_INSTRUMENT_TYPES = {
    "Aditivo",
    "Distrato",
    "Acordo",
    "Locação",
    "Honorários",
    "Comodato",
    "Compra e Venda",
    "Parceria",
    "Prestação de Serviços",
}

# Unilateral instruments where absence of bilateral parties is expected
UNILATERAL_INSTRUMENTS = {
    "declaracao",
    "declaração",
    "procuracao",
    "procuração",
    "notificacao",
    "notificação",
    "certidao",
    "certidão",
    "relatorio",
    "relatório",
}

# Stopwords and generic corporate suffixes
STOPWORDS = {
    "de",
    "da",
    "do",
    "das",
    "dos",
    "e",
    "em",
    "para",
    "com",
    "o",
    "a",
    "os",
    "as",
    "no",
    "na",
    "nos",
    "nas",
    "sociedade",
    "advogados",
    "brasil",
}

GENERIC_CORPORATE_WORDS = {
    "banco",
    "comercio",
    "comércio",
    "industria",
    "indústria",
    "servicos",
    "serviços",
    "participacoes",
    "participações",
    "investimentos",
    "distribuidora",
    "empresa",
    "associacao",
    "associação",
    "holding",
}


# ---------------------------------------------------------------------------
# Helper Extraction & Normalization Functions
# ---------------------------------------------------------------------------


def _clean_spaces(text: str) -> str:
    """Normalize whitespace."""
    return re.sub(r"\s+", " ", text).strip()


def _normalize_name(name: str) -> str:
    """
    Normalize company / party name for robust reconciliation.

    Removes accents, lowercase, removes legal suffixes (LTDA, S/A, etc.)
    and punctuation.
    """
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_text = nfkd.encode("ASCII", "ignore").decode("utf-8").lower()
    # Strip common corporate suffixes
    ascii_text = re.sub(
        r"\b(s[\./\s]*a|s[\./\s]*\/[\./\s]*a|ltda|eireli|me|epp|cia|companhia|sociedade\s+anonima|sociedade\s+limitada)\b",
        " ",
        ascii_text,
    )
    # Strip non-alphanumeric characters
    ascii_text = re.sub(r"[^\w\s]", " ", ascii_text)
    return _clean_spaces(ascii_text)


def _extract_digits(text: Optional[str]) -> str:
    """Return only numeric digits from text."""
    if not text:
        return ""
    return re.sub(r"\D", "", text)


def _extract_mfiles_client(mfiles_record: Dict[str, Any]) -> Optional[str]:
    """Extract client name from M-Files record supporting flat or nested formats."""
    props = mfiles_record.get("properties")
    val = None
    if isinstance(props, dict):
        val = props.get("Cliente") or props.get("cliente")
    if not val:
        val = mfiles_record.get("cliente") or mfiles_record.get("Cliente")

    if isinstance(val, dict):
        val = (
            val.get("DisplayValue")
            or val.get("title")
            or val.get("name")
            or str(val)
        )
    if val and isinstance(val, str) and val.strip():
        return val.strip()
    return None


def _extract_mfiles_class_name(mfiles_record: Dict[str, Any]) -> Optional[str]:
    """Extract class name from M-Files record supporting flat or nested formats."""
    props = mfiles_record.get("properties")
    val = None
    if isinstance(props, dict):
        val = (
            props.get("Classe")
            or props.get("classe")
            or props.get("class_name")
        )
    if not val:
        val = (
            mfiles_record.get("class_name")
            or mfiles_record.get("class")
            or mfiles_record.get("Classe")
            or mfiles_record.get("classe")
        )

    if isinstance(val, dict):
        val = (
            val.get("DisplayValue")
            or val.get("title")
            or val.get("name")
            or str(val)
        )
    if val and isinstance(val, str) and val.strip():
        return val.strip()
    return None


def _extract_mfiles_cnpj(mfiles_record: Dict[str, Any]) -> Optional[str]:
    """Extract CNPJ digits from M-Files record properties if explicitly present."""
    props = mfiles_record.get("properties")
    val = None
    if isinstance(props, dict):
        val = props.get("CNPJ") or props.get("cnpj") or props.get("CPF/CNPJ")
    if not val:
        val = mfiles_record.get("cnpj") or mfiles_record.get("CNPJ")

    if isinstance(val, dict):
        val = val.get("DisplayValue") or val.get("value")
    try:
        return normalize_identifier(str(val)) if val else None
    except ValueError:
        return None


def _is_client_match(
    mfiles_client: str, party: ContractParty, mfiles_record: Dict[str, Any]
) -> bool:
    """
    Check if a ContractParty matches the client indicated by M-Files.

    Matches by:
    1. Clean CNPJ/CPF matching.
    2. Exact or substring match of normalized entity name.
    3. Significant token set overlap.
    """
    # 1. Clean CNPJ/CPF check
    if party.clean_identifier:
        mfiles_explicit_cnpj = _extract_mfiles_cnpj(mfiles_record)
        if mfiles_explicit_cnpj:
            return party.clean_identifier == mfiles_explicit_cnpj

        if party.clean_identifier in extract_identifier_candidates(mfiles_client):
            return True

    # 2. Case-insensitive raw substring match
    raw_c = mfiles_client.strip().lower()
    raw_p = party.name.strip().lower()
    if len(raw_c) >= 3 and (raw_c in raw_p or raw_p in raw_c):
        return True

    # 3. Normalized string matching
    c_norm = _normalize_name(mfiles_client)
    p_norm = _normalize_name(party.name)

    if not c_norm or not p_norm:
        return False

    if c_norm == p_norm:
        return True

    if len(c_norm) >= 3 and (c_norm in p_norm or p_norm in c_norm):
        return True

    # 4. Token overlap matching
    c_tokens = set(w for w in c_norm.split() if w not in STOPWORDS and len(w) >= 2)
    p_tokens = set(w for w in p_norm.split() if w not in STOPWORDS and len(w) >= 2)

    if not c_tokens or not p_tokens:
        return False

    # Check subset relationship
    if c_tokens.issubset(p_tokens) or p_tokens.issubset(c_tokens):
        # Must have at least one non-generic word or be identical
        if (c_tokens & p_tokens) - GENERIC_CORPORATE_WORDS or c_tokens == p_tokens:
            return True

    # Significant non-generic overlap
    overlap = (c_tokens & p_tokens) - GENERIC_CORPORATE_WORDS
    if overlap:
        c_sig = c_tokens - GENERIC_CORPORATE_WORDS
        p_sig = p_tokens - GENERIC_CORPORATE_WORDS
        min_sig = min(len(c_sig), len(p_sig))
        if min_sig <= 1 or len(overlap) >= min_sig or len(overlap) >= 2:
            return True

    return False


def _compute_sha256(path: Path) -> str:
    """Calculate SHA256 hexadecimal hash of a file."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _resolve_file_path(record: Dict[str, Any], raw_dir: Path) -> Path:
    """Resolve physical Path on disk from curation/manifest record dictionary."""
    # 1. Direct file_path key
    if "file_path" in record and record["file_path"]:
        p = Path(record["file_path"])
        if p.is_absolute() and p.exists():
            return p
        if (raw_dir / p).exists():
            return raw_dir / p

    # 2. Curated single file dict: record["file"]["path"]
    if (
        "file" in record
        and isinstance(record["file"], dict)
        and record["file"].get("path")
    ):
        return raw_dir / record["file"]["path"]

    # 3. Manifest files list: record["files"][0]["path"]
    if "files" in record and isinstance(record["files"], list) and record["files"]:
        first = record["files"][0]
        if isinstance(first, dict) and first.get("path"):
            return raw_dir / first["path"]
        if isinstance(first, (str, Path)):
            p = Path(first)
            return p if (p.is_absolute() and p.exists()) else raw_dir / p

    # 4. Direct path key
    if "path" in record and record["path"]:
        p = Path(record["path"])
        if p.is_absolute() and p.exists():
            return p
        if (raw_dir / p).exists():
            return raw_dir / p

    # 5. Look inside files/<mfiles_id>/...
    doc_id = record.get("mfiles_id") or record.get("doc_id")
    if doc_id:
        doc_dir = raw_dir / "files" / str(doc_id)
        if doc_dir.exists():
            for f in doc_dir.iterdir():
                if f.is_file():
                    return f

    raise FileNotFoundError(
        f"Não foi possível determinar o caminho do arquivo para o registro: {record}"
    )


# ---------------------------------------------------------------------------
# Reconciler & Quarantine Gate
# ---------------------------------------------------------------------------


def reconcile_document(
    mfiles_record: Dict[str, Any],
    parsed_doc: ParsedDocument,
    quarantine_on_no_parties: bool = True,
) -> ParsedDocument:
    """
    Reconcile external M-Files cadastral metadata against real extracted legal text.

    Applies three core reconciliation gates:
    1. Client Mismatch Gate:
       If M-Files reports a client and none of the contract parties match,
       flags DISCREPANCY_CLIENT_MISMATCH and sets status='review_metadata_mismatch'.
    2. Instrument Type Refinement:
       If M-Files generically classifies document as 'Contrato' or 'Documento',
       but text reveals a specific instrument ('Aditivo', 'Locação', etc.),
       records FLAG_INSTRUMENT_TYPE_REFINED. Textual truth prevails.
    3. Missing Parties Gate:
       If no parties were identified in a bilateral/multilateral agreement,
       records FLAG_NO_PARTIES_FOUND and quarantines if configured.

    Preserves status='failed' if the document previously suffered fatal parsing errors.
    """
    if parsed_doc.status == "failed":
        return parsed_doc

    divergence_flags = list(parsed_doc.metadata.mfiles_divergence_flags)

    # 1. Client Mismatch Check
    mfiles_client = _extract_mfiles_client(mfiles_record)
    if mfiles_client:
        matched = any(
            _is_client_match(mfiles_client, p, mfiles_record)
            for p in parsed_doc.metadata.parties
        )
        if not matched:
            if DISCREPANCY_CLIENT_MISMATCH not in divergence_flags:
                divergence_flags.append(DISCREPANCY_CLIENT_MISMATCH)
            parsed_doc.status = "review_metadata_mismatch"

    # 2. Instrument Type Refinement Check
    mfiles_class = _extract_mfiles_class_name(mfiles_record)
    if mfiles_class:
        norm_class = mfiles_class.lower().strip()
        if norm_class in GENERIC_MFILES_CLASSES:
            text_type = parsed_doc.metadata.instrument_type
            if text_type in REFINED_INSTRUMENT_TYPES or (
                text_type not in ("Outro", "Não identificado")
                and text_type.lower() != norm_class
            ):
                if FLAG_INSTRUMENT_TYPE_REFINED not in divergence_flags:
                    divergence_flags.append(FLAG_INSTRUMENT_TYPE_REFINED)

    # 3. Missing Parties Check (in bilateral/multilateral agreements)
    if not parsed_doc.metadata.parties:
        instrument_norm = (
            parsed_doc.metadata.instrument_type or ""
        ).strip().lower()
        if instrument_norm not in UNILATERAL_INSTRUMENTS:
            if FLAG_NO_PARTIES_FOUND not in divergence_flags:
                divergence_flags.append(FLAG_NO_PARTIES_FOUND)
            if quarantine_on_no_parties:
                parsed_doc.status = "review_metadata_mismatch"

    parsed_doc.metadata.mfiles_divergence_flags = divergence_flags
    return parsed_doc


# ---------------------------------------------------------------------------
# Unified Parsing Orchestrator
# ---------------------------------------------------------------------------


def parse_single_document(
    file_path: Path | str,
    mfiles_record: Optional[Dict[str, Any]] = None,
    doc_id: Optional[int] = None,
    doc_version: int = 1,
    quarantine_on_no_parties: bool = True,
    parse_route: Optional[str] = None,
) -> ParsedDocument:
    """
    Parse a single legal document file and reconcile its metadata against M-Files.

    Dispatches to the appropriate format route:
    - .docx: Native OOXML extractor (docx_parser)
    - .doc, .rtf, .wpd: Isolated LibreOffice bridge (legacy_parser)
    - .pdf: Digital text with surgical OCR fallback (pdf_parser)

    Defensive error handling:
    Catches fatal parsing errors (missing file, corrupted archive, unsupported format)
    and safely returns a ParsedDocument with status='failed', error_message, and blocks=[].
    """
    mfiles_record = mfiles_record or {}
    path = Path(file_path)

    effective_doc_id = (
        doc_id
        if doc_id is not None
        else int(mfiles_record.get("mfiles_id") or mfiles_record.get("doc_id") or 0)
    )
    effective_doc_version = (
        doc_version
        if doc_version != 1
        else int(
            mfiles_record.get("mfiles_version")
            or mfiles_record.get("doc_version")
            or doc_version
        )
    )

    parser_name = "unknown"
    parser_version = "1.0.0"

    try:
        if not path.exists():
            raise FileNotFoundError(f"Arquivo inexistente: {path}")
        if not path.is_file():
            raise ValueError(f"O caminho informado não é um arquivo: {path}")
        if path.stat().st_size == 0:
            raise ValueError(f"Arquivo vazio (tamanho zero): {path}")

        file_hash = _compute_sha256(path)
        suffix = path.suffix.lower()

        if parse_route == "docx" or (parse_route is None and suffix == ".docx"):
            parser_name = "docx_parser"
            blocks = extract_docx_blocks(
                path, doc_id=effective_doc_id, doc_version=effective_doc_version
            )
        elif parse_route == "libreoffice" or (parse_route is None and suffix in (".doc", ".rtf", ".wpd")):
            parser_name = "legacy_parser"
            blocks = extract_legacy_blocks(
                path, doc_id=effective_doc_id, doc_version=effective_doc_version
            )
        elif parse_route == "pdf" or (parse_route is None and suffix == ".pdf"):
            parser_name = "pdf_parser"
            blocks = extract_pdf_blocks(
                path, doc_id=effective_doc_id, doc_version=effective_doc_version
            )
        else:
            raise ValueError(
                f"Extensão de arquivo não suportada para parsing: '{suffix}'"
            )

        if not blocks:
            raise ValueError(f"Nenhum bloco extraído do documento: {path}")

        metadata = extract_contract_metadata(blocks)

        parsed_doc = ParsedDocument(
            doc_id=effective_doc_id,
            doc_version=effective_doc_version,
            file_path=str(path),
            file_hash=file_hash,
            parser_name=parser_name,
            parser_version=parser_version,
            metadata=metadata,
            blocks=blocks,
            status="success",
        )

        return reconcile_document(
            mfiles_record=mfiles_record,
            parsed_doc=parsed_doc,
            quarantine_on_no_parties=quarantine_on_no_parties,
        )

    except Exception as e:
        default_meta = extract_contract_metadata([])
        return ParsedDocument(
            doc_id=effective_doc_id,
            doc_version=effective_doc_version,
            file_path=str(path),
            file_hash="",
            parser_name=parser_name,
            parser_version=parser_version,
            metadata=default_meta,
            blocks=[],
            status="failed",
            error_message=str(e),
        )


# ---------------------------------------------------------------------------
# Batch Parsing Pipeline
# ---------------------------------------------------------------------------


def run_parsing_pipeline(
    records: List[Dict[str, Any]],
    raw_dir: Path | str,
    quarantine_on_no_parties: bool = True,
) -> List[ParsedDocument]:
    """
    Execute parsing and metadata reconciliation across a batch of curation/manifest records.

    Args:
        records: List of document records from M-Files sync manifest or curation.
        raw_dir: Root directory holding raw sync files.
        quarantine_on_no_parties: Whether to quarantine bilateral contracts without identified parties.

    Returns:
        List of canonical ParsedDocument aggregates ready for downstream chunking or quarantine audit.
    """
    raw_path = Path(raw_dir)
    results: List[ParsedDocument] = []

    for record in records:
        doc_id = record.get("mfiles_id") or record.get("doc_id")
        doc_version = (
            record.get("mfiles_version") or record.get("doc_version") or 1
        )

        try:
            file_path = _resolve_file_path(record, raw_path)
            parsed_doc = parse_single_document(
                file_path=file_path,
                mfiles_record=record,
                doc_id=doc_id,
                doc_version=doc_version,
                quarantine_on_no_parties=quarantine_on_no_parties,
            )
        except Exception as e:
            parsed_doc = ParsedDocument(
                doc_id=doc_id or 0,
                doc_version=doc_version or 1,
                file_path=str(record.get("file_path") or ""),
                file_hash="",
                parser_name="unknown",
                parser_version="1.0.0",
                metadata=extract_contract_metadata([]),
                blocks=[],
                status="failed",
                error_message=str(e),
            )

        results.append(parsed_doc)

    return results

"""
Canonical Intermediate Schemas and Structured Legal Entities.

Provides strict Pydantic v2 data models for:
- Contract parties and metadata (ContractParty, PartyRole, ContractMetadata)
- Document blocks and hierarchy (DocumentBlock, BlockType, HierarchyLevel, UncertaintyFlag)
- Parsed document aggregate (ParsedDocument)
"""
from enum import Enum
from datetime import date
import re
from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.identifiers import normalize_identifier
from app.ingestion.temporal import TEMPORAL_EXTRACTOR_VERSION, extract_temporal_mentions, iter_date_candidates, summarize_execution_date


class PartyRole(str, Enum):
    """Normalized legal contract roles."""

    CONTRATANTE = "contratante"
    CONTRATADA = "contratada"
    LOCADOR = "locador"
    LOCATARIO = "locatario"
    VENDEDOR = "vendedor"
    COMPRADOR = "comprador"
    ANUENTE = "anuente"
    PARTE_GENERICA = "parte"

    @classmethod
    def _missing_(cls, value: object):
        if isinstance(value, str):
            val_norm = value.lower().strip()
            if val_norm in ("parte_generica", "parte"):
                return cls.PARTE_GENERICA
            for member in cls:
                if member.value == val_norm or member.name.lower() == val_norm:
                    return member
        return None


class ContractParty(BaseModel):
    """Structured contract party entity."""

    name: str
    role: PartyRole = PartyRole.PARTE_GENERICA
    clean_identifier: Optional[str] = None  # CPF/CNPJ canônico, preservando letras
    raw_identifier: Optional[str] = None    # Literal original com pontuação
    is_law_firm: bool = False               # True se for Andrade Advogados

    @field_validator("clean_identifier")
    @classmethod
    def validate_clean_identifier(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v_stripped = v.strip()
            if not v_stripped:
                return None
            canonical = normalize_identifier(v_stripped)
            if canonical != v_stripped:
                raise ValueError("clean_identifier deve estar na forma canônica")
            return canonical
        return v


class DateKind(str, Enum):
    """Candidate event labels; do not encode approved contractual legal effects."""

    SIGNATURE = "signature"
    EFFECTIVE_START = "effective_start"
    EFFECTIVE_END = "effective_end"
    DUE = "due"
    TERMINATION = "termination"
    RENEWAL = "renewal"
    UNKNOWN = "unknown"


def _validate_iso_calendar(value: str) -> str:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("Date must be canonical YYYY-MM-DD")
    date.fromisoformat(value)
    return value


class TemporalMention(BaseModel):
    """Unreviewed date evidence in a canonical block, not a physical SourceSpan."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    normalized_date: Optional[str] = None
    kind: DateKind = DateKind.UNKNOWN
    block_id: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    raw_text: str = Field(min_length=1)
    review_state: Literal["pending_review"] = "pending_review"
    uncertainty_flags: List[str] = Field(default_factory=list)

    @field_validator("normalized_date")
    @classmethod
    def validate_calendar(cls, value):
        return None if value is None else _validate_iso_calendar(value)

    @model_validator(mode="after")
    def validate_local_evidence(self):
        if self.end <= self.start or self.end - self.start != len(self.raw_text):
            raise ValueError("Temporal offsets must cover the exact raw token")
        candidates = list(iter_date_candidates(self.raw_text))
        if len(candidates) != 1 or candidates[0].start != 0 or candidates[0].end != len(self.raw_text):
            raise ValueError("Temporal evidence must contain one complete date token")
        if candidates[0].normalized_date != self.normalized_date:
            raise ValueError("Normalized date must agree with raw temporal evidence")
        if self.normalized_date is None and (
            self.kind != DateKind.UNKNOWN or "invalid_calendar_date" not in self.uncertainty_flags
        ):
            raise ValueError("Invalid calendar dates must remain unknown and flagged")
        return self


class ContractMetadata(BaseModel):
    """High-fidelity metadata extracted from contract body and header."""

    formal_title: str
    instrument_type: str                    # Honorários, Prestação de Serviços, Acordo, Locação, etc.
    subject_area: Optional[str] = None      # Matéria jurídica temática / assunto
    parties: List[ContractParty] = Field(default_factory=list)
    execution_date: Optional[str] = None
    temporal_mentions: List[TemporalMention] = Field(default_factory=list)
    temporal_extractor_version: Optional[Literal["temporal-candidates-v1"]] = None
    object_summary: Optional[str] = None
    mfiles_divergence_flags: List[str] = Field(default_factory=list)

    @field_validator("execution_date")
    @classmethod
    def validate_execution_calendar(cls, value):
        return None if value is None else _validate_iso_calendar(value)

    @model_validator(mode="after")
    def validate_execution_summary(self):
        # Historical metadata without mention evidence is readable, unqualified.
        if self.temporal_mentions and self.temporal_extractor_version != TEMPORAL_EXTRACTOR_VERSION:
            raise ValueError("Temporal candidates require an identified extractor version")
        if self.temporal_extractor_version is not None and self.execution_date != summarize_execution_date(self.temporal_mentions):
            raise ValueError("Execution summary must agree with non-conflicting signature candidates")
        by_block = {}
        for mention in self.temporal_mentions:
            ranges = by_block.setdefault(mention.block_id, [])
            if any(mention.start < end and start < mention.end for start, end in ranges):
                raise ValueError("Temporal evidence must not contain duplicate or overlapping mentions")
            ranges.append((mention.start, mention.end))
        return self


class BlockType(str, Enum):
    """Type categorization of document blocks."""

    TITLE = "title"
    PREAMBLE = "preamble"
    CLAUSE = "clause"
    PARAGRAPH = "paragraph"
    ITEM = "item"
    SUBITEM = "subitem"
    TABLE = "table"
    ANNEX = "annex"
    SIGNATURE = "signature"

    @classmethod
    def _missing_(cls, value: object):
        if isinstance(value, str):
            val_norm = value.lower().strip()
            for member in cls:
                if member.value == val_norm or member.name.lower() == val_norm:
                    return member
        return None


class HierarchyLevel(str, Enum):
    """Structural hierarchy level for document blocks."""

    TITLE = "title"
    PREAMBLE = "preamble"
    CLAUSE = "clause"
    PARAGRAPH = "paragraph"
    ITEM = "item"
    SUBITEM = "subitem"
    TABLE = "table"
    ANNEX = "annex"
    SIGNATURE = "signature"

    @classmethod
    def _missing_(cls, value: object):
        if isinstance(value, str):
            val_norm = value.lower().strip()
            for member in cls:
                if member.value == val_norm or member.name.lower() == val_norm:
                    return member
        return None


class UncertaintyFlag(str, Enum):
    """Flags indicating degradation, ambiguity, or potential parsing artifacts."""

    TRACK_CHANGES_PRESENT = "track_changes_present"
    LOW_CONFIDENCE_OCR = "low_confidence_ocr"
    UNRESOLVED_NUMBERING = "unresolved_numbering"
    CORRUPTED_TABLE = "corrupted_table"
    HAS_COMMENTS = "has_comments"

    @classmethod
    def _missing_(cls, value: object):
        if isinstance(value, str):
            val_norm = value.lower().strip()
            for member in cls:
                if member.value == val_norm or member.name.lower() == val_norm:
                    return member
        return None


class DocumentBlock(BaseModel):
    """Canonical intermediate block representation preserving proof and hierarchy."""

    block_id: str
    doc_id: int
    doc_version: int
    block_type: BlockType
    hierarchy_level: HierarchyLevel
    hierarchy_label: Optional[str] = None   # ex.: "Cláusula 4ª", "§ 2º"
    parent_clause_id: Optional[str] = None
    order_index: int
    text_raw: str                           # Literal original para prova jurídica
    text_search: str                        # Normalizado para indexação léxica
    table_metadata: Optional[Dict[str, Any]] = None
    spans: List[Dict[str, int]] = Field(default_factory=list)
    source_locator: Optional[Dict[str, Any]] = None  # candidato físico, não aceite humano
    uncertainty_flags: List[UncertaintyFlag] = Field(default_factory=list)


class ParsedDocument(BaseModel):
    """Parsed document aggregate holding canonical blocks and metadata."""

    doc_id: int
    doc_version: int
    file_path: str
    file_hash: str
    parser_name: str
    parser_version: str
    metadata: ContractMetadata
    blocks: List[DocumentBlock] = Field(default_factory=list)
    status: str = "success"                 # success | review_metadata_mismatch | failed
    error_message: Optional[str] = None
    # Legacy status is technical; old artifacts never acquire approval.
    eligibility: Literal["pending_review", "approved", "quarantined", "excluded", "failed"] = "pending_review"
    approval_record_id: Optional[str] = None
    file_id: Optional[str] = None

    @model_validator(mode="after")
    def validate_blocks_when_success(self) -> "ParsedDocument":
        if self.eligibility == "approved" and (self.status != "success" or not self.approval_record_id):
            raise ValueError("Elegibilidade aprovada exige sucesso técnico e decisão registrada")
        if self.status == "success" and not self.blocks:
            raise ValueError(
                "Documentos com status 'success' devem conter ao menos um bloco (blocks não pode ser vazio)"
            )
        if self.metadata.temporal_extractor_version is not None:
            by_id = {block.block_id: block for block in self.blocks}
            if len(by_id) != len(self.blocks):
                raise ValueError("Temporal evidence requires unambiguous block identifiers")
            expected = {(m.block_id, m.start, m.end): m for m in extract_temporal_mentions(self.blocks)}
            supplied = {(m.block_id, m.start, m.end): m for m in self.metadata.temporal_mentions}
            if supplied.keys() != expected.keys():
                raise ValueError("Versioned temporal extraction must retain every date candidate")
            for mention in self.metadata.temporal_mentions:
                block = by_id.get(mention.block_id)
                if block is None or block.text_raw[mention.start:mention.end] != mention.raw_text:
                    raise ValueError("Temporal evidence must match its actual canonical block")
                if block.doc_id != self.doc_id or block.doc_version != self.doc_version:
                    raise ValueError("Temporal evidence must belong to the declared document version")
                candidate = expected.get((mention.block_id, mention.start, mention.end))
                if candidate is None or candidate.model_dump() != mention.model_dump():
                    raise ValueError("Temporal labels must agree with the identified extractor and local context")
        return self

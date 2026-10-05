"""
Canonical Intermediate Schemas and Structured Legal Entities.

Provides strict Pydantic v2 data models for:
- Contract parties and metadata (ContractParty, PartyRole, ContractMetadata)
- Document blocks and hierarchy (DocumentBlock, BlockType, HierarchyLevel, UncertaintyFlag)
- Parsed document aggregate (ParsedDocument)
"""
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


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
    clean_identifier: Optional[str] = None  # CNPJ ou CPF limpo (somente dígitos)
    raw_identifier: Optional[str] = None    # Literal original com pontuação
    is_law_firm: bool = False               # True se for Andrade Advogados

    @field_validator("clean_identifier")
    @classmethod
    def validate_clean_identifier(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v_stripped = v.strip()
            if not v_stripped:
                return None
            if not v_stripped.isdigit():
                raise ValueError("clean_identifier deve conter apenas dígitos numéricos (CPF/CNPJ)")
            return v_stripped
        return v


class ContractMetadata(BaseModel):
    """High-fidelity metadata extracted from contract body and header."""

    formal_title: str
    instrument_type: str                    # Honorários, Prestação de Serviços, Acordo, Locação, etc.
    subject_area: Optional[str] = None      # Matéria jurídica temática / assunto
    parties: List[ContractParty] = Field(default_factory=list)
    execution_date: Optional[str] = None
    object_summary: Optional[str] = None
    mfiles_divergence_flags: List[str] = Field(default_factory=list)


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

    @model_validator(mode="after")
    def validate_blocks_when_success(self) -> "ParsedDocument":
        if self.status == "success" and not self.blocks:
            raise ValueError(
                "Documentos com status 'success' devem conter ao menos um bloco (blocks não pode ser vazio)"
            )
        return self

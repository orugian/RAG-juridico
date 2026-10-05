"""
Hierarchical Legal Chunker for Document Ingestion Pipeline.

Transforms approved ParsedDocument instances (status == 'success') into LangChain
Document objects. Generates a synthetic search header in `page_content` combining
formal instrument, qualified parties with roles and identifiers, and location,
while strictly segregating the pure original text in `metadata["verbatim_text"]`
for unambiguous legal citation under the 4 Elements Rule.
"""
import logging
from typing import Any, Dict, List, Optional, Sequence
from langchain_core.documents import Document

from app.ingestion.schemas import (
    BlockType,
    ContractMetadata,
    ContractParty,
    DocumentBlock,
    ParsedDocument,
    PartyRole,
    UncertaintyFlag,
)

logger = logging.getLogger(__name__)

# User-facing location labels for document block types
BLOCK_TYPE_DISPLAY: Dict[str, str] = {
    "clause": "Cláusula",
    "preamble": "Preâmbulo",
    "title": "Título",
    "paragraph": "Parágrafo",
    "item": "Item",
    "subitem": "Subitem",
    "table": "Tabela",
    "annex": "Anexo",
    "signature": "Assinaturas",
}

# Display labels for contract party roles
ROLE_DISPLAY_MAP: Dict[PartyRole, str] = {
    PartyRole.CONTRATANTE: "Contratante",
    PartyRole.CONTRATADA: "Contratada",
    PartyRole.LOCADOR: "Locador",
    PartyRole.LOCATARIO: "Locatário",
    PartyRole.VENDEDOR: "Vendedor",
    PartyRole.COMPRADOR: "Comprador",
    PartyRole.ANUENTE: "Anuente",
    PartyRole.PARTE_GENERICA: "Parte",
}


def _format_party_role(role: Any) -> str:
    """Format party role for search header display."""
    if isinstance(role, PartyRole):
        return ROLE_DISPLAY_MAP.get(role, role.value.capitalize())
    if isinstance(role, str):
        role_clean = role.strip().lower()
        if role_clean in ("parte_generica", "parte"):
            return "Parte"
        for member in PartyRole:
            if member.value == role_clean:
                return ROLE_DISPLAY_MAP.get(member, role_clean.capitalize())
        return role.strip().capitalize()
    return str(role).capitalize()


def _format_block_type_label(block_type_str: str) -> str:
    """Return Portuguese display label for block type."""
    return BLOCK_TYPE_DISPLAY.get(block_type_str, block_type_str.capitalize())


def _build_search_header(
    metadata: ContractMetadata,
    hierarchy_label: Optional[str],
    block_type_str: str,
) -> str:
    """Build synthetic search header formatted for hybrid retrieval."""
    instrument = metadata.instrument_type or "Instrumento Não Identificado"
    title = metadata.formal_title or "Documento"

    lines = [f"[Instrumento: {instrument} - {title}]"]

    for party in metadata.parties:
        role_label = _format_party_role(party.role)
        identifier = party.clean_identifier or party.raw_identifier
        ident_str = f" ({identifier})" if identifier else ""
        lines.append(f"{role_label}: {party.name}{ident_str}")

    location = hierarchy_label if hierarchy_label else _format_block_type_label(block_type_str)
    lines.append(f"[Localização: {location}]")

    return "\n".join(lines)


def _extract_clean_identifiers(parties: Sequence[ContractParty]) -> List[str]:
    """Collect deduplicated numeric CNPJ/CPF identifiers from parties."""
    clean_ids: List[str] = []
    for party in parties:
        if party.clean_identifier:
            cid = party.clean_identifier.strip()
            if cid and cid not in clean_ids:
                clean_ids.append(cid)
    return clean_ids


def _extract_uncertainty_flags(blocks: Sequence[DocumentBlock]) -> List[str]:
    """Aggregate uncertainty flags from all component blocks as string values."""
    flags: List[str] = []
    for block in blocks:
        for flag in block.uncertainty_flags:
            flag_str = flag.value if hasattr(flag, "value") else str(flag)
            if flag_str and flag_str not in flags:
                flags.append(flag_str)
    return flags


def _group_blocks(blocks: Sequence[DocumentBlock]) -> List[Dict[str, Any]]:
    """
    Group DocumentBlocks into cohesive chunks by legal structure.

    Rules:
    - TITLE and PREAMBLE blocks preceding clauses form a single inaugural chunk.
    - CLAUSE blocks initiate new chunks.
    - PARAGRAPH, ITEM, SUBITEM, TABLE blocks attach to their parent clause (via parent_clause_id
      or contiguous ordering).
    - ANNEX blocks initiate distinct chunks.
    - SIGNATURE blocks are aggregated into a final signature chunk.
    - Standalone tables or other autonomous blocks form independent chunks.
    """
    if not blocks:
        return []

    sorted_blocks = sorted(blocks, key=lambda b: b.order_index)

    inaugural_blocks: List[DocumentBlock] = []
    clause_groups: List[Dict[str, Any]] = []
    clause_map: Dict[str, Dict[str, Any]] = {}
    signature_blocks: List[DocumentBlock] = []
    current_group: Optional[Dict[str, Any]] = None

    has_clause_or_annex_started = False

    for block in sorted_blocks:
        b_type = block.block_type

        # 1. Signatures - collect for final chunk
        if b_type == BlockType.SIGNATURE:
            current_group = None
            signature_blocks.append(block)
            continue

        # 2. Inaugural blocks (TITLE / PREAMBLE) before any clause or annex
        if b_type in (BlockType.TITLE, BlockType.PREAMBLE) and not has_clause_or_annex_started:
            inaugural_blocks.append(block)
            continue

        # 3. New CLAUSE block starts a new clause group
        if b_type == BlockType.CLAUSE:
            has_clause_or_annex_started = True
            grp = {
                "blocks": [block],
                "block_type": "clause",
                "hierarchy_label": block.hierarchy_label,
            }
            clause_groups.append(grp)
            clause_map[block.block_id] = grp
            current_group = grp
            continue

        # 4. New ANNEX block starts an annex group
        if b_type == BlockType.ANNEX:
            has_clause_or_annex_started = True
            grp = {
                "blocks": [block],
                "block_type": "annex",
                "hierarchy_label": block.hierarchy_label,
            }
            clause_groups.append(grp)
            clause_map[block.block_id] = grp
            current_group = grp
            continue

        # 5. Subordinate blocks (PARAGRAPH, ITEM, SUBITEM, TABLE)
        if b_type in (BlockType.PARAGRAPH, BlockType.ITEM, BlockType.SUBITEM, BlockType.TABLE):
            if block.parent_clause_id and block.parent_clause_id in clause_map:
                clause_map[block.parent_clause_id]["blocks"].append(block)
            elif current_group is not None:
                current_group["blocks"].append(block)
            elif (
                not has_clause_or_annex_started
                and inaugural_blocks
                and b_type in (BlockType.PARAGRAPH, BlockType.ITEM)
            ):
                inaugural_blocks.append(block)
            else:
                b_type_str = b_type.value if hasattr(b_type, "value") else str(b_type).lower()
                grp = {
                    "blocks": [block],
                    "block_type": b_type_str,
                    "hierarchy_label": block.hierarchy_label,
                }
                clause_groups.append(grp)
                current_group = grp
            continue

        # 6. Any other autonomous block type
        b_type_str = b_type.value if hasattr(b_type, "value") else str(b_type).lower()
        grp = {
            "blocks": [block],
            "block_type": b_type_str,
            "hierarchy_label": block.hierarchy_label,
        }
        clause_groups.append(grp)
        current_group = grp

    final_groups: List[Dict[str, Any]] = []

    # Inaugural chunk (Title + Preamble)
    if inaugural_blocks:
        preamble_label = next((b.hierarchy_label for b in inaugural_blocks if b.hierarchy_label), None)
        final_groups.append({
            "blocks": inaugural_blocks,
            "block_type": "preamble",
            "hierarchy_label": preamble_label,
        })

    # Body groups (Clauses, Annexes, Standalone blocks)
    for grp in clause_groups:
        final_groups.append(grp)

    # Final signature chunk
    if signature_blocks:
        sig_label = next((b.hierarchy_label for b in signature_blocks if b.hierarchy_label), None)
        final_groups.append({
            "blocks": signature_blocks,
            "block_type": "signature",
            "hierarchy_label": sig_label,
        })

    return final_groups


def create_legal_chunks(documents: List[ParsedDocument]) -> List[Document]:
    """
    Convert approved ParsedDocument instances into LangChain Document chunks.

    Only documents with status == 'success' are processed. Any documents in quarantine
    ('review_metadata_mismatch') or failed ('failed') are ignored with a warning log.

    Args:
        documents: List of ParsedDocument instances to chunk.

    Returns:
        List of LangChain Document instances with search header in page_content
        and isolated pure text in metadata['verbatim_text'].
    """
    chunks: List[Document] = []

    for doc in documents:
        if doc.status != "success":
            logger.warning(
                "Ignorando documento doc_id=%s com status '%s' "
                "(apenas status 'success' é processado pelo chunker).",
                doc.doc_id,
                doc.status,
            )
            continue

        if not doc.blocks:
            logger.warning("Ignorando documento doc_id=%s: lista de blocos vazia.", doc.doc_id)
            continue

        clean_identifiers = _extract_clean_identifiers(doc.metadata.parties)
        groups = _group_blocks(doc.blocks)

        for chunk_idx, grp in enumerate(groups):
            raw_texts = [
                b.text_raw.strip()
                for b in grp["blocks"]
                if b.text_raw and b.text_raw.strip()
            ]
            if not raw_texts:
                continue

            verbatim_text = "\n\n".join(raw_texts)
            header = _build_search_header(
                metadata=doc.metadata,
                hierarchy_label=grp["hierarchy_label"],
                block_type_str=grp["block_type"],
            )
            page_content = f"{header}\n\n{verbatim_text}"

            chunk_metadata = {
                "chunk_id": f"doc_{doc.doc_id}_v{doc.doc_version}_chunk_{chunk_idx}",
                "doc_id": doc.doc_id,
                "doc_version": doc.doc_version,
                "file_path": doc.file_path,
                "formal_title": doc.metadata.formal_title,
                "instrument_type": doc.metadata.instrument_type,
                "subject_area": doc.metadata.subject_area,
                "clean_identifiers": clean_identifiers,
                "hierarchy_label": grp["hierarchy_label"],
                "block_type": grp["block_type"],
                "verbatim_text": verbatim_text,
                "uncertainty_flags": _extract_uncertainty_flags(grp["blocks"]),
            }

            chunks.append(Document(page_content=page_content, metadata=chunk_metadata))

    return chunks

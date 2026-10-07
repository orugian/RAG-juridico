"""
Hierarchical Legal Chunker for Document Ingestion Pipeline.

Transforms technically successful ParsedDocument candidates into LangChain
Document objects. Generates a synthetic search header in `page_content` combining
formal instrument, qualified parties with roles and identifiers, and location,
while strictly segregating the pure original text in `metadata["verbatim_text"]`
for unambiguous legal citation under the 4 Elements Rule.

This legacy helper does not verify human approvals or authorize publication.
Governed citation units and index promotion remain separate pending stages.
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
    TemporalMention,
    UncertaintyFlag,
)
from app.ingestion.temporal import iter_date_candidates
from app.retrieval.lexical import LEXICAL_PROFILE_VERSION, normalize_party_name

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
    temporal_mentions: Sequence[TemporalMention] = (),
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

    for mention in temporal_mentions:
        value = mention.normalized_date
        if value is not None:
            lines.append(f"[Data candidata não homologada: {mention.kind.value} = {value}; literal: {mention.raw_text}]")

    return "\n".join(lines)


def _local_temporal_mentions(metadata: ContractMetadata, blocks: Sequence[DocumentBlock]) -> List[TemporalMention]:
    """Keep candidate dates attached to their exact local source span only."""
    block_map = {block.block_id: block for block in blocks}
    mentions = []
    for mention in metadata.temporal_mentions:
        block = block_map.get(mention.block_id)
        if block is None:
            continue
        mention = TemporalMention.model_validate(mention.model_dump())
        if block.text_raw[mention.start:mention.end] != mention.raw_text:
            raise ValueError("Temporal candidate does not match its source span")
        candidates = list(iter_date_candidates(mention.raw_text))
        if (len(candidates) != 1 or candidates[0].start != 0 or candidates[0].end != len(mention.raw_text)
                or candidates[0].normalized_date != mention.normalized_date):
            raise ValueError("Temporal candidate does not match its literal date")
        mentions.append(mention)
    return mentions


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


def create_candidate_chunks(documents: List[ParsedDocument]) -> List[Document]:
    """
    Convert technically successful candidates into legacy search chunks.

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

        # Models/lists may have changed since construction. Rebuild the complete
        # snapshot so temporal labels are checked against actual local context,
        # not only against a still-valid date token and span.
        doc = ParsedDocument.model_validate(doc.model_dump())

        if not doc.blocks:
            logger.warning("Ignorando documento doc_id=%s: lista de blocos vazia.", doc.doc_id)
            continue

        clean_identifiers = _extract_clean_identifiers(doc.metadata.parties)
        party_names = list(dict.fromkeys(party.name for party in doc.metadata.parties))
        normalized_party_names = list(dict.fromkeys(normalize_party_name(name) for name in party_names))
        groups = _group_blocks(doc.blocks)

        for chunk_idx, grp in enumerate(groups):
            raw_texts = [
                b.text_raw
                for b in grp["blocks"]
                if b.text_raw and b.text_raw.strip()
            ]
            if not raw_texts:
                continue

            verbatim_text = "\n\n".join(raw_texts)
            temporal_mentions = _local_temporal_mentions(doc.metadata, grp["blocks"])
            header = _build_search_header(
                metadata=doc.metadata,
                hierarchy_label=grp["hierarchy_label"],
                block_type_str=grp["block_type"],
                temporal_mentions=temporal_mentions,
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
                "party_names": party_names,
                "normalized_party_names": normalized_party_names,
                "temporal_mentions": [mention.model_dump(mode="json") for mention in temporal_mentions],
                "temporal_extractor_version": doc.metadata.temporal_extractor_version,
                "block_ids": [block.block_id for block in grp["blocks"]],
                "lexical_profile_version": LEXICAL_PROFILE_VERSION,
                "eligibility": doc.eligibility,
                "hierarchy_label": grp["hierarchy_label"],
                "block_type": grp["block_type"],
                "verbatim_text": verbatim_text,
                "uncertainty_flags": _extract_uncertainty_flags(grp["blocks"]),
            }

            chunks.append(Document(page_content=page_content, metadata=chunk_metadata))

    return chunks


def create_legal_chunks(documents: List[ParsedDocument], *, ledger=None, budget=None,
                        build_requests=None, relations=(), resolutions=()) -> List[Document]:
    """Governed boundary: physical proof and current scoped decisions are required.

    Returns search projections of canonical EvidenceChunks. This neither publishes
    an index nor resolves query-time closure; P4/P5/P6 consume canonical storage.
    Use create_candidate_chunks explicitly for unqualified local diagnostics.
    """
    from copy import deepcopy
    from pathlib import Path
    from app.embeddings.artifacts import digest_file
    from app.ingestion.evidence import build_evidence_chunks, validate_unit_decisions
    if ledger is None or budget is None or build_requests is None:
        raise ValueError("governed chunking requires ledger, Qwen budget and physical build requests")
    if len(documents) != len(build_requests):
        raise ValueError("governed build request coverage must be exact")
    documents = tuple(ParsedDocument.model_validate(doc.model_dump()) for doc in documents)
    build_requests = tuple(deepcopy(build_requests))
    batch_ledger_digest = ledger.state_digest()
    chunks = []
    built_units = []
    seen_sources = set()
    for document, request in zip(documents, build_requests):
        if request.source.source_id in seen_sources:
            raise ValueError("governed build contains duplicate sources")
        seen_sources.add(request.source.source_id)
        result = build_evidence_chunks(document, request.source, ledger=ledger, budget=budget,
            original_path=request.original_path, unit_review_ids=request.unit_review_ids,
            dependencies=request.dependencies, conversion_path=request.conversion_path,
            reference_bindings=request.reference_bindings,
            relations=relations, resolutions=resolutions)
        built_units.extend(result.units)
        for chunk in result.chunks:
            canonical = chunk.model_dump(mode="json")
            # Complex records remain available canonically; scalar projection
            # compatibility and generation promotion are P4's responsibility.
            chunks.append(Document(page_content=chunk.text_search, metadata={
                **canonical, "evidence_contract_version": "canonical-proof-v1",
                "doc_id": document.doc_id, "formal_title": document.metadata.formal_title,
                "clean_identifiers": _extract_clean_identifiers(chunk.parties),
                "party_names": [party.name for party in chunk.parties],
                "lexical_profile_version": LEXICAL_PROFILE_VERSION}))
    # End the public batch, not only each file's builder, before returning any
    # projection: an earlier source may change during a later file's split.
    for request in build_requests:
        if digest_file(Path(request.original_path)) != request.source.file_hash:
            raise ValueError("Physical source changed during governed batch")
        if request.source.conversion_sha256 and (request.conversion_path is None or
                digest_file(Path(request.conversion_path)) != request.source.conversion_sha256):
            raise ValueError("Retained conversion changed during governed batch")
    for unit in built_units:
        validate_unit_decisions(unit, ledger, all_units={u.unit_id: u for u in built_units})
    if ledger.state_digest() != batch_ledger_digest:
        raise ValueError("Review ledger changed during governed batch")
    return chunks

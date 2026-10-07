"""Local canonical proof builders. Approval comes only from the scoped ledger.

Preparing candidates writes no decisions. Search slices are never citation units;
query-time closure, corpus homologation and index publication are separate gates.
"""
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from importlib.metadata import version
from pathlib import Path
from threading import Lock
from typing import Mapping, Sequence
import hashlib
import json

from app.contracts import CitationLocation, CitationUnit, EvidenceChunk, SourceIdentity, UnitTextSpan
from app.embeddings.artifacts import digest_file, verify_artifact
from app.embeddings.qwen import QwenProfile, split_candidate_text, token_count
from app.ingestion.chunker import _group_blocks
from app.ingestion.schemas import ParsedDocument
from app.retrieval.lexical import lexical_profile


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class QwenTokenBudget:
    """Verified local Qwen tokenizer, independent of the inference runtime mutex."""
    def __init__(self, artifact_directory: Path, profile: QwenProfile | None = None):
        self.profile = profile or QwenProfile()
        artifact_hash = verify_artifact(Path(artifact_directory))
        from transformers import AutoTokenizer
        self._tokenizer = AutoTokenizer.from_pretrained(
            artifact_directory, local_files_only=True, trust_remote_code=False, use_fast=True,
            padding_side="left")
        self._lock = Lock()
        self._identity = {"artifact_sha256": artifact_hash, "profile": self.profile.specification,
                          "versions": {name: version(name) for name in ("transformers", "tokenizers")},
                          "qwen_code_sha256": hashlib.sha256(
                              (Path(__file__).parents[1] / "embeddings/qwen.py").read_bytes()).hexdigest()}

    @property
    def identity(self):
        return deepcopy(self._identity)

    def count(self, text: str) -> int:
        if not isinstance(text, str):
            raise ValueError("Invalid token counting text")
        with self._lock:
            return token_count(self._tokenizer, text)

    def split(self, text: str, context: str):
        # Uses the already-tested diagnostic boundary algorithm, then promotes
        # only slices of independently verified, approved canonical units.
        with self._lock:
            return split_candidate_text(text, self._tokenizer,
                                        max_tokens=self.profile.max_input_tokens,
                                        synthetic_context=context,
                                        max_characters=max(len(text), self.profile.max_text_characters))


@dataclass(frozen=True)
class EvidenceBuildRequest:
    source: SourceIdentity
    original_path: Path
    unit_review_ids: Mapping[str, str]
    dependencies: Mapping[str, Sequence[str]] | None = None
    conversion_path: Path | None = None
    reference_bindings: Mapping[str, Sequence[str]] | None = None


@dataclass(frozen=True)
class EvidenceBuildResult:
    units: tuple[CitationUnit, ...]
    chunks: tuple[EvidenceChunk, ...]
    derivation_key: str
    tokenizer_identity: dict


def prepare_citation_units(document: ParsedDocument, identity: SourceIdentity, *, original_path: Path,
                           dependencies: Mapping[str, Sequence[str]] | None = None,
                           conversion_path: Path | None = None,
                           reference_bindings: Mapping[str, Sequence[str]] | None = None) -> list[CitationUnit]:
    """Read-only candidates with deterministic IDs and physical literal maps.

    Dependencies are explicit reviewed block-root references, never inferred
    contractual effects. Every input block must occur exactly once in a unit.
    """
    from app.ingestion.provenance import verify_document_provenance
    from app.ingestion.references import bind_references, reference_labels_for_blocks
    document = ParsedDocument.model_validate(document.model_dump())
    identity = SourceIdentity.model_validate(identity.model_dump())
    if (document.status != "success" or not document.metadata.parties or
            not document.metadata.formal_title.strip() or
            any(not party.name.strip() for party in document.metadata.parties)):
        raise ValueError("Canonical units require successful parsing and actual parties")
    physical = verify_document_provenance(document, identity, original_path=original_path,
                                          conversion_path=conversion_path,
                                          conversion_sha256=identity.conversion_sha256)
    ids = [b.block_id for b in document.blocks]
    orders = [b.order_index for b in document.blocks]
    if len(set(ids)) != len(ids) or len(set(orders)) != len(orders):
        raise ValueError("Ambiguous block identity or order")
    for block in document.blocks:
        if block.parent_clause_id and block.parent_clause_id not in ids:
            raise ValueError("Missing structural parent")
    groups = _group_blocks(document.blocks)
    flattened = [b.block_id for g in groups for b in g["blocks"]]
    if sorted(flattened) != sorted(ids):
        raise ValueError("Incomplete canonical structural grouping")
    candidates, roots = [], {}
    for group in groups:
        blocks = sorted(group["blocks"], key=lambda b: b.order_index)
        text, mapping, spans, context = "", [], [], []
        for block in blocks:
            verified = physical[block.block_id]
            if not verified.literal_text:
                raise ValueError("Synthetic-only block cannot become a citation")
            if text:
                mapping.append(UnitTextSpan(unit_start=len(text), unit_end=len(text) + 2))
                text += "\n\n"
            mapping.append(UnitTextSpan(unit_start=len(text), unit_end=len(text) + len(verified.literal_text),
                                       source_span=verified.span))
            text += verified.literal_text
            spans.append(verified.span)
            if verified.synthetic_context:
                context.append(f"[{block.block_id}: {verified.synthetic_context}]")
        first = spans[0]
        # Physical label is always available, even without a reviewed clause label.
        label = group["hierarchy_label"] or (
            f"Página {first.page}, bloco {first.page_block_index}" if first.page else
            f"{first.xml_part}, elemento {first.body_child_index}")
        base = dict(instrument_id=identity.instrument_id, verbatim_text=text,
                    source_ids=[identity.source_id], block_ids=[b.block_id for b in blocks], spans=spans,
                    location=CitationLocation(label=label, clause=group["hierarchy_label"]
                                              if group["block_type"] == "clause" else None, page=first.page),
                    source_identities=[identity], contract_title=document.metadata.formal_title,
                    parties=document.metadata.parties, synthetic_context="\n".join(context), text_map=mapping,
                    reference_labels=reference_labels_for_blocks(blocks),
                    risk_codes=sorted({flag.value for b in blocks for flag in b.uncertainty_flags}))
        serial = {k: [v.model_dump(mode="json") for v in value] if isinstance(value, list) and value
                  and hasattr(value[0], "model_dump") else value.model_dump(mode="json")
                  if hasattr(value, "model_dump") else value for k, value in base.items()}
        unit = CitationUnit(unit_id="unit:" + _digest(serial), **base)
        candidates.append(unit)
        for block in blocks:
            roots[block.block_id] = unit.unit_id
    by_unit = {u.unit_id: u for u in candidates}
    for root, targets in (dependencies or {}).items():
        if root not in roots or any(t not in roots for t in targets):
            raise ValueError("Unresolved unit dependency")
        unit = by_unit[roots[root]]
        unit.closure_unit_ids = sorted(set(unit.closure_unit_ids) | {roots[t] for t in targets})
    return bind_references([CitationUnit.model_validate(u.model_dump()) for u in candidates],
                           reference_bindings=reference_bindings)


def validate_unit_decisions(unit: CitationUnit, ledger, all_units=None):
    """Validate exact scoped approvals, including current source/parties decisions."""
    from app.ingestion.review_store import review_digest, unit_review_digest
    from app.ingestion.references import validate_references
    validate_references(unit, all_units=all_units)
    if not unit.source_identities or not unit.parties or not unit.contract_title or not unit.text_map:
        raise ValueError("Incomplete governed citation unit")
    if set(unit.source_ids) != {s.source_id for s in unit.source_identities}:
        raise ValueError("Source identities disagree with citation unit")
    records = []
    for source in unit.source_identities:
        if source.instrument_id != unit.instrument_id:
            raise ValueError("Unit assigned to wrong instrument")
        args = dict(source_id=source.source_id, snapshot=source.snapshot, file_hash=source.file_hash,
                    configuration_version=source.configuration_version)
        for scope, subject, digest in (("source", None, None), ("parties", source.source_id,
                review_digest({"contract_title": unit.contract_title,
                               "parties": [p.model_dump(mode="json") for p in unit.parties]}))):
            current = ledger.latest(**args, scope=scope, subject_id=subject, subject_digest=digest)
            if current is None:
                raise ValueError("Missing current scoped approval")
            records.append(ledger.require_current(record_id=current.record_id, **args, scope=scope,
                                                  subject_id=subject, subject_digest=digest))
        records.append(ledger.require_current(record_id=unit.review_record_id, **args, scope="unit",
                                              subject_id=unit.unit_id, subject_digest=unit_review_digest(unit)))
    return records


def _implementation_identity():
    root = Path(__file__).parents[2]
    files = ("app/contracts.py", "app/ingestion/evidence.py", "app/ingestion/provenance.py",
             "app/ingestion/review_store.py", "app/ingestion/closure.py", "app/ingestion/chunker.py",
             "app/ingestion/references.py",
             "app/ingestion/schemas.py", "app/ingestion/docx_parser.py", "app/ingestion/pdf_parser.py",
             "app/ingestion/temporal.py", "app/embeddings/qwen.py", "uv.lock")
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in files}


def _slice_spans(unit, start, end):
    spans = []
    for entry in unit.text_map:
        low, high = max(start, entry.unit_start), min(end, entry.unit_end)
        if low < high and entry.source_span:
            data = entry.source_span.model_dump()
            data.update(start=entry.source_span.start + low - entry.unit_start,
                        end=entry.source_span.start + high - entry.unit_start)
            spans.append(type(entry.source_span).model_validate(data))
    return spans


def build_evidence_chunks(document: ParsedDocument, identity: SourceIdentity, *, ledger,
                          budget: QwenTokenBudget, original_path: Path, unit_review_ids: Mapping[str, str],
                          dependencies=None, conversion_path=None, relations=(), resolutions=(),
                          reference_bindings=None) -> EvidenceBuildResult:
    """Build approved search evidence; query-time relation closure remains mandatory."""
    from app.ingestion.closure import render_unit
    if not isinstance(budget, QwenTokenBudget):
        raise ValueError("Governed chunking requires a verified Qwen token budget")
    ledger_digest = ledger.state_digest()
    units = prepare_citation_units(document, identity, original_path=original_path,
                                   dependencies=dependencies, conversion_path=conversion_path,
                                   reference_bindings=reference_bindings)
    unit_map = {u.unit_id: u for u in units}
    if set(unit_review_ids) != {u.unit_id for u in units}:
        raise ValueError("Unit review coverage must be exact")
    decisions = []
    for unit in units:
        unit.review_record_id = unit_review_ids[unit.unit_id]
        decisions.extend(validate_unit_decisions(unit, ledger, all_units=unit_map))
        unit.approval_state = "approved"
    key = _digest({"sources": [identity.model_dump(mode="json")],
                   "units": [u.model_dump(mode="json") for u in units],
                   "decisions": [r.model_dump(mode="json") for r in decisions],
                   "relations": [r.model_dump(mode="json") for r in relations],
                   "resolutions": [r.model_dump(mode="json") for r in resolutions],
                   "qwen": budget.identity, "lexical": lexical_profile(),
                   "ledger_state": ledger_digest,
                   "implementation": _implementation_identity(), "chunking": "canonical-proof-v1"})
    chunks = []
    for unit in units:
        rendered = render_unit(unit)
        if not rendered.endswith(unit.verbatim_text):
            raise ValueError("Canonical renderer must preserve the final complete literal")
        header = rendered[:-len(unit.verbatim_text)]
        for segment in budget.split(unit.verbatim_text, header):
            spans = _slice_spans(unit, segment.start, segment.end)
            if not spans:
                raise ValueError("A separator alone cannot become search evidence")
            search = header + segment.text
            count = budget.count(search)
            if count > budget.profile.max_input_tokens or len(search) > budget.profile.max_text_characters:
                raise ValueError("Final search evidence exceeds Qwen input budget")
            chunk_id = "chunk:" + _digest({"derivation": key, "unit": unit.unit_id,
                                           "start": segment.start, "end": segment.end, "search": search})
            chunks.append(EvidenceChunk(chunk_id=chunk_id, unit_id=unit.unit_id,
                source_id=identity.source_id, instrument_id=identity.instrument_id,
                doc_version=identity.doc_version, file_id=identity.file_id, file_hash=identity.file_hash,
                parser_name=identity.parser_name, parser_version=identity.parser_version,
                configuration_version=identity.configuration_version, derivation_key=key,
                block_ids=list(dict.fromkeys(s.block_id for s in spans)), spans=spans,
                verbatim_text=segment.text, text_search=search, synthetic_context=header,
                parties=unit.parties, location=unit.location, parent_id=unit.unit_id,
                approval_state="approved", review_record_id=unit.review_record_id,
                unit_start=segment.start, unit_end=segment.end, token_count=count))
    # A concurrent scoped revocation cannot be silently accepted during a build.
    for unit in units:
        validate_unit_decisions(unit, ledger, all_units=unit_map)
    if ledger.state_digest() != ledger_digest:
        raise ValueError("Review ledger changed during governed build")
    if digest_file(Path(original_path)) != identity.file_hash:
        raise ValueError("Physical source changed during governed build")
    if identity.conversion_sha256 and (conversion_path is None or
            digest_file(Path(conversion_path)) != identity.conversion_sha256):
        raise ValueError("Retained conversion changed during governed build")
    return EvidenceBuildResult(tuple(units), tuple(chunks), key, budget.identity)

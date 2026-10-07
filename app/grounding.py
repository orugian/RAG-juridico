"""Grounding of generation candidates against the governed offer and canonical proof (P6).

The model may only select IDs of offered CitationUnits. Everything shown to the user is
resolved here from the canonical bundle; no model text, quote or span is ever accepted.
Validation proves structure and fidelity of the proof, not legal relevance or effect.
"""
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from pydantic import ValidationError

from app.answer_contracts import ClarificationCode, PublicReason, ValidationFinding
from app.contracts import CitationUnit, EvidenceChunk, GenerationCandidate, InstrumentRelation, QueryPlan
from app.ingestion.closure import resolve_closure
from app.retrieval.hybrid import GovernedRetrievalResult
from app.terms import Term, absent_terms, content_terms

VALIDATOR_VERSION = "p6-validator-v1"
MAX_RAW_CANDIDATE_CHARS = 100_000
_FENCE = re.compile(r"^```(?:json)?[ \t]*\r?\n(.*?)\r?\n?```$", re.DOTALL)
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}(:[A-Za-z0-9:_./-]{1,200})?$")
_ACCEPTED = ValidationFinding(code="candidate_accepted")


def safe_code(value: str) -> str:
    """Keep conforming codes; canonicalize anything else to a bounded hash, never drop it."""
    if _CODE.fullmatch(value):
        return value
    head = value.partition(":")[0]
    head = head if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", head) else "warning"
    return f"{head}:sha256-{hashlib.sha256(value.encode()).hexdigest()[:16]}"


@dataclass(frozen=True)
class OfferedContext:
    units: tuple[CitationUnit, ...]
    chunks_by_unit: Mapping[str, tuple[EvidenceChunk, ...]]
    question_item_ids: tuple[str, ...]
    evidence_scope: str
    warnings: tuple[str, ...]

    @property
    def unit_ids(self) -> frozenset:
        return frozenset(unit.unit_id for unit in self.units)


def build_offered_context(result: GovernedRetrievalResult, plan: QueryPlan) -> OfferedContext:
    units = tuple(sorted(result.citation_units, key=lambda unit: unit.unit_id))
    ids = [unit.unit_id for unit in units]
    if len(set(ids)) != len(ids):
        raise ValueError("offered_units_duplicated")
    grouped: dict[str, list[EvidenceChunk]] = {unit_id: [] for unit_id in ids}
    for chunk in result.evidence_chunks:
        if chunk.unit_id not in grouped:
            raise ValueError("offered_chunk_outside_units")
        grouped[chunk.unit_id].append(chunk)
    return OfferedContext(
        units=units,
        chunks_by_unit={key: tuple(sorted(value, key=lambda c: (c.unit_start, c.chunk_id))) for key, value in grouped.items()},
        question_item_ids=tuple(plan.question_item_ids), evidence_scope=plan.evidence_scope,
        warnings=tuple(dict.fromkeys(safe_code(w) for w in result.warnings)),
    )


@dataclass(frozen=True)
class CandidateValidation:
    candidate: GenerationCandidate | None
    findings: tuple[ValidationFinding, ...]


def _parse(raw: Any) -> GenerationCandidate:
    if isinstance(raw, GenerationCandidate):
        data = raw.model_dump()
    elif isinstance(raw, dict):
        data = raw
    elif isinstance(raw, str):
        text = raw.strip()
        if len(text) > MAX_RAW_CANDIDATE_CHARS:
            raise ValueError("candidate_too_large")
        fenced = _FENCE.match(text)
        data = json.loads(fenced.group(1) if fenced else text)
    else:
        raise ValueError("candidate_type_unsupported")
    return GenerationCandidate.model_validate(data)


def validate_candidate(raw: Any, context: OfferedContext) -> CandidateValidation:
    """Structure, offer, item and coverage checks; never repairs or completes a candidate."""
    try:
        candidate = _parse(raw)
    except (ValueError, ValidationError, TypeError, AttributeError, RecursionError):
        return CandidateValidation(None, (ValidationFinding(code="candidate_schema_invalid"),))
    findings: list[ValidationFinding] = []
    if candidate.action == "select":
        offered, items = context.unit_ids, set(context.question_item_ids)
        covered: set[str] = set()
        for selection in candidate.selections:
            if selection.unit_id not in offered:
                findings.append(ValidationFinding(code="unit_not_offered"))
            for item in selection.question_item_ids:
                if item not in items:
                    findings.append(ValidationFinding(code="question_item_unknown"))
            covered.update(selection.question_item_ids)
        if candidate.approved_fact_ids:
            findings.append(ValidationFinding(code="approved_fact_unsupported"))
        findings.extend(ValidationFinding(code="question_item_uncovered", subject_id=item)
                        for item in context.question_item_ids if item not in covered)
    if findings:
        unique = {(f.code, f.subject_id): f for f in findings}
        return CandidateValidation(None, tuple(unique.values()))
    return CandidateValidation(candidate, (_ACCEPTED,))


@dataclass(frozen=True)
class RetrievalDecision:
    status: str
    reason_code: PublicReason | None
    detail_code: str | None
    clarification_code: ClarificationCode | None
    answerable: bool


def _detail(prefix: str, value: str | None) -> str:
    candidate = f"{prefix}:{value}" if value else prefix
    return candidate if _CODE.fullmatch(candidate) else f"{prefix}:unclassified"


def classify_retrieval_outcome(result: GovernedRetrievalResult) -> RetrievalDecision:
    """Map P5 outcomes to the public vocabulary; any non-answer never reaches the generator."""
    reason = result.reason_code
    if result.status == "needs_clarification":
        code = result.clarification_code or "selection_ambiguous"
        public = "ambiguous_time" if code == "temporal_ambiguous" else "ambiguous_instrument"
        return RetrievalDecision("needs_clarification", public, _detail("retrieval", reason), code, False)
    if result.status == "blocked":
        if reason == "closure_over_budget":
            public = "budget_exceeded"
        elif reason == "temporal_effect_unresolved":
            return RetrievalDecision("needs_clarification", "ambiguous_time", _detail("retrieval", reason),
                                     "temporal_ambiguous", False)
        elif reason and (reason.startswith(("relation_", "family_"))):
            public = "unresolved_relation"
        else:
            public = "insufficient_evidence"
        return RetrievalDecision("abstained", public, _detail("retrieval", reason), None, False)
    if result.status == "answered" and result.citation_units and result.evidence_chunks:
        return RetrievalDecision("answered", None, None, None, True)
    return RetrievalDecision("abstained", "insufficient_evidence",
                             _detail("retrieval", reason or "no_units"), None, False)


def classify_authority_error(error: BaseException) -> tuple[str, str, str]:
    """(kind, code, detail): kind 'error' maps to ServiceErrorCode, 'abstained' to PublicReason."""
    if not isinstance(error, ValueError):
        return "error", "service_unavailable", "authority:backend_unavailable"
    message = str(error)
    detail = _detail("authority", message if _CODE.fullmatch(message) else None)
    if message == "generation_access_denied_or_stale":
        return "error", "forbidden", detail
    if message.startswith(("policy_", "generation_active", "generation_not_ready", "generation_blocked_family",
                           "generation_family_anchor", "generation_evidence_scope")):
        return "error", "service_unavailable", detail
    if "registry" in message or message.startswith(("family_", "relation_")):
        return "abstained", "unresolved_relation", detail
    if message in {"generation_result_not_authorized", "generation_review_changed_during_return"} \
            or message.startswith("unit_") or message.endswith("review_unavailable"):
        return "abstained", "insufficient_evidence", detail
    return "error", "internal_error", detail


class ProofBlocked(Exception):
    def __init__(self, finding: ValidationFinding, public_reason: PublicReason = "insufficient_evidence"):
        super().__init__(finding.code)
        self.finding, self.public_reason = finding, public_reason


@dataclass(frozen=True)
class ResolvedProof:
    units: tuple[CitationUnit, ...]
    roles: Mapping[str, str]
    item_ids: Mapping[str, tuple[str, ...]]
    relation_ids: Mapping[str, tuple[str, ...]]
    chunks: Mapping[str, tuple[EvidenceChunk, ...]]
    relations: tuple[InstrumentRelation, ...]
    warnings: tuple[str, ...]
    token_count: int


def _blocked(code: str, subject: str | None = None, reason: PublicReason = "insufficient_evidence") -> ProofBlocked:
    return ProofBlocked(ValidationFinding(code=code, subject_id=subject), reason)


def resolve_proof(candidate: GenerationCandidate, context: OfferedContext, pinned, plan: QueryPlan, *,
                  count_tokens: Callable[[str], int], max_tokens: int) -> ResolvedProof:
    """Re-resolve the selection through the P3B closure authority and the canonical bundle."""
    bundle = pinned.loaded.artifacts.bundle
    canonical = {unit.unit_id: unit for unit in bundle.units}
    closure = resolve_closure(
        [selection.unit_id for selection in candidate.selections], units=canonical,
        sources={source.source_id: source for source in bundle.sources}, ledger=pinned.manager.ledger,
        relations=bundle.relations, resolutions=bundle.resolutions,
        relation_review_sources=bundle.relation_review_sources, family_review_sources=bundle.family_review_sources,
        evidence_scope=plan.evidence_scope, reference_date=plan.reference_date,
        count_tokens=count_tokens, max_tokens=max_tokens)
    if closure.status == "blocked":
        reason = closure.reason_code or "closure_blocked"
        public = ("budget_exceeded" if reason == "closure_over_budget"
                  else "unresolved_relation" if reason.startswith(("relation_", "family_")) else "insufficient_evidence")
        raise _blocked(_detail("closure_blocked", reason), None, public)
    selected = {selection.unit_id: tuple(selection.question_item_ids) for selection in candidate.selections}
    offered, chunks = context.unit_ids, {}
    final_units = tuple(closure.units)
    if plan.evidence_scope == "original_text":
        instruments = {canonical[unit_id].instrument_id for unit_id in selected}
        final_units = tuple(unit for unit in final_units if unit.instrument_id in instruments)
    for unit in final_units:
        if unit.unit_id not in offered:
            raise _blocked("closure_outside_offer", unit.unit_id)
        if unit.model_dump() != canonical[unit.unit_id].model_dump() or unit.approval_state != "approved":
            raise _blocked("unit_not_canonical", unit.unit_id)
        unit_chunks = context.chunks_by_unit.get(unit.unit_id, ())
        if not unit_chunks:
            raise _blocked("evidence_missing", unit.unit_id)
        for chunk in unit_chunks:
            if (chunk.source_id not in unit.source_ids or chunk.instrument_id != unit.instrument_id
                    or chunk.parent_id != unit.unit_id):
                raise _blocked("evidence_unit_mismatch", unit.unit_id)
            if chunk.unit_end is None or unit.verbatim_text[chunk.unit_start:chunk.unit_end] != chunk.verbatim_text:
                raise _blocked("quote_mismatch", unit.unit_id)
            if chunk.parties != unit.parties:
                raise _blocked("parties_inconsistent", unit.unit_id)
        chunks[unit.unit_id] = unit_chunks
    rendered = {unit.unit_id for unit in final_units}
    approved = tuple(relation for relation in bundle.relations if relation.state == "approved"
                     and relation.from_instrument_id in {u.instrument_id for u in final_units}
                     and relation.to_instrument_id in {u.instrument_id for u in final_units}
                     and rendered & set(relation.affected_unit_ids + relation.support_unit_ids))
    return ResolvedProof(
        units=final_units, roles={u.unit_id: "selected" if u.unit_id in selected else "closure" for u in final_units},
        item_ids={u.unit_id: selected.get(u.unit_id, ()) for u in final_units},
        relation_ids={u.unit_id: tuple(sorted(r.relation_id for r in approved
                                              if u.unit_id in set(r.affected_unit_ids + r.support_unit_ids)))
                      for u in final_units},
        chunks=chunks, relations=approved,
        warnings=tuple(dict.fromkeys(safe_code(w) for w in closure.warnings)),
        token_count=closure.token_count)


def _identifying_terms(question: str, units) -> set[str]:
    """Question terms found in titles/party names: they select the instrument, not its content."""
    meta = [" ".join([unit.contract_title, *(p.name for p in unit.parties)]) for unit in units]
    return {t.folded for t in content_terms(question)} - {t.folded for t in absent_terms(question, meta)}


def term_coverage(question: str, proof) -> tuple[list[Term], list[Term]]:
    """(checkable terms, absent terms) against the literal text of the units in `proof.units`.

    Terms that only identify the instrument (title/party names) are neither required in the
    literal nor counted as support for it.
    """
    units = proof.units
    identifying = _identifying_terms(question, units)
    absent = absent_terms(question, [unit.verbatim_text for unit in units])
    return ([t for t in content_terms(question) if t.folded not in identifying],
            [t for t in absent if t.folded not in identifying])


def unsupported_items(question: str, labels: Mapping[str, str], proof: ResolvedProof) -> list[str]:
    """Plan items whose own selected units (plus mandatory closure) carry none of the item's content terms."""
    identifying = _identifying_terms(question, proof.units)
    closure = [u for u in proof.units if proof.roles[u.unit_id] == "closure"]
    items = dict.fromkeys(item for ids in proof.item_ids.values() for item in ids)
    unsupported = []
    for item in items:
        units = [u for u in proof.units if item in proof.item_ids[u.unit_id]] + closure
        terms = [t for t in content_terms(labels.get(item) or question) if t.folded not in identifying]
        absent = {t.folded for t in absent_terms(" ".join(t.display for t in terms), [u.verbatim_text for u in units])}
        if terms and all(t.folded in absent for t in terms):
            unsupported.append(item)
    return unsupported

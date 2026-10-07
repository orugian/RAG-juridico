"""P6 grounding: candidate validation is closed, offer-bound and never accepts free text."""
import json

import pytest

from app.answer_contracts import ValidationFinding
from app.contracts import (
    CitationLocation, CitationUnit, EvidenceChunk, GenerationCandidate, QueryPlan, SourceSpan, UnitSelection,
)
from app.grounding import (
    OfferedContext, build_offered_context, term_coverage, classify_authority_error, classify_retrieval_outcome,
    validate_candidate,
)
from app.ingestion.schemas import ContractParty, PartyRole
from app.retrieval.hybrid import GovernedRetrievalResult
from app.contracts import RetrievalResult, SelectionFilters

SHA = "b" * 64
PARTIES = [ContractParty(name="Alpha Serviços Ltda", role=PartyRole.CONTRATANTE, clean_identifier="11222333000144")]


def make_unit(unit_id, text, instrument="doc:101", source="doc:101:v1:file:f101", block="b1"):
    return CitationUnit(
        unit_id=unit_id, instrument_id=instrument, verbatim_text=text, source_ids=[source], block_ids=[block],
        spans=[SourceSpan(source_id=source, block_id=block, start=0, end=len(text))],
        location=CitationLocation(label="Cláusula 1ª", clause="Cláusula 1ª"), contract_title="Contrato Alpha",
        parties=PARTIES,
    )


def make_chunk(unit, chunk_id):
    return EvidenceChunk(
        chunk_id=chunk_id, unit_id=unit.unit_id, source_id=unit.source_ids[0], instrument_id=unit.instrument_id,
        doc_version=1, file_id="f101", file_hash=SHA, parser_name="docx_parser", parser_version="1.0.0",
        configuration_version="cfg1", derivation_key=SHA, block_ids=unit.block_ids, spans=unit.spans,
        verbatim_text=unit.verbatim_text, text_search=unit.verbatim_text, parties=unit.parties,
        location=unit.location, parent_id=unit.unit_id, unit_start=0, unit_end=len(unit.verbatim_text),
    )


def retrieval(units, status="answered", **extra):
    chunks = [make_chunk(u, f"chunk:{u.unit_id}") for u in units]
    result = RetrievalResult(
        generation_id="gen-1", query_plan_version="query-plan-v1", access_scope_digest="scope",
        retrieval_config_version="cfg1", relation_registry_version="v1", evidence_ids=[c.chunk_id for c in chunks],
        effective_filters=SelectionFilters(), requested_question_item_ids=["q1", "q2"], retrieval_mode="hybrid",
    )
    return GovernedRetrievalResult(
        status=status, retrieval_result=result, evidence_chunks=chunks, citation_units=list(units), **extra,
    )


@pytest.fixture
def context():
    units = [make_unit("unit:b", "Cláusula 2ª - multa de 10%."), make_unit("unit:a", "Cláusula 1ª - honorários de R$ 50.000,00.")]
    plan = QueryPlan(question_item_ids=["q1", "q2"])
    return build_offered_context(retrieval(units), plan)


def select(*pairs, **extra):
    return {"action": "select", "selections": [{"unit_id": u, "question_item_ids": items} for u, items in pairs],
            "approved_fact_ids": [], "clarification_code": None} | extra


def codes(validation):
    return {f.code for f in validation.findings}


def test_offered_context_is_sorted_and_bound_to_chunks(context):
    assert isinstance(context, OfferedContext)
    assert [u.unit_id for u in context.units] == ["unit:a", "unit:b"]
    assert context.unit_ids == frozenset({"unit:a", "unit:b"})
    assert [c.chunk_id for c in context.chunks_by_unit["unit:a"]] == ["chunk:unit:a"]
    assert context.question_item_ids == ("q1", "q2")


def test_valid_selection_covering_all_items_is_accepted(context):
    validation = validate_candidate(select(("unit:a", ["q1"]), ("unit:b", ["q2"])), context)
    assert validation.candidate and validation.candidate.action == "select"
    assert codes(validation) == {"candidate_accepted"}


@pytest.mark.parametrize("raw", [
    select(("unit:a", ["q1", "q2"]), claim="Os honorários são exatamente R$ 1,00"),
    select(("unit:a", ["q1", "q2"]), quote="texto livre"),
    {"action": "select", "selections": [{"unit_id": "unit:a", "question_item_ids": ["q1"], "span": [0, 5]}]},
    {"response": "Os honorários são R$ 1,00"},
    "Os honorários são R$ 1,00 conforme a cláusula 1ª.",
    "{not json",
    None, 42, [],
    {"action": "select", "selections": [], "approved_fact_ids": [], "clarification_code": None},
    {"action": "abstain", "selections": [{"unit_id": "unit:a", "question_item_ids": ["q1"]}]},
    {"action": "clarify", "clarification_code": None},
])
def test_free_text_extra_fields_and_malformed_candidates_are_rejected(context, raw):
    validation = validate_candidate(raw, context)
    assert validation.candidate is None and "candidate_schema_invalid" in codes(validation)


def test_json_string_and_single_markdown_fence_are_parsed_strictly(context):
    payload = json.dumps(select(("unit:a", ["q1", "q2"])))
    assert validate_candidate(payload, context).candidate
    assert validate_candidate("```json\n" + payload + "\n```", context).candidate
    assert validate_candidate("Segue:\n" + payload, context).candidate is None


def test_instance_is_revalidated_never_trusted(context):
    forged = GenerationCandidate.model_construct(action="select", selections=[], approved_fact_ids=[], clarification_code=None)
    assert validate_candidate(forged, context).candidate is None
    good = GenerationCandidate(action="select", selections=[UnitSelection(unit_id="unit:a", question_item_ids=["q1", "q2"])])
    assert validate_candidate(good, context).candidate == good


def test_invented_unit_unknown_item_and_facts_are_rejected(context):
    assert "unit_not_offered" in codes(validate_candidate(select(("unit:invented", ["q1", "q2"])), context))
    assert "question_item_unknown" in codes(validate_candidate(select(("unit:a", ["q1", "q9"])), context))
    assert "approved_fact_unsupported" in codes(
        validate_candidate(select(("unit:a", ["q1", "q2"]), approved_fact_ids=["fact:1"]), context))


def test_uncovered_question_item_is_not_answerable(context):
    validation = validate_candidate(select(("unit:a", ["q1"])), context)
    assert validation.candidate is None
    assert ValidationFinding(code="question_item_uncovered", subject_id="q2") in validation.findings
    unknown = validate_candidate(select(("unit:ghost-123.456.789-01", ["q9"])), context)
    assert {f.subject_id for f in unknown.findings if f.code in {"unit_not_offered", "question_item_unknown"}} == {None}


def test_abstain_and_clarify_actions_are_valid_without_selections(context):
    assert validate_candidate({"action": "abstain"}, context).candidate.action == "abstain"
    clarify = validate_candidate({"action": "clarify", "clarification_code": "instrument_ambiguous"}, context)
    assert clarify.candidate.clarification_code == "instrument_ambiguous"


@pytest.mark.parametrize("reason,expected", [
    ("closure_over_budget", ("abstained", "budget_exceeded", None)),
    ("temporal_effect_unresolved", ("needs_clarification", "ambiguous_time", "temporal_ambiguous")),
    ("relation_unresolved", ("abstained", "unresolved_relation", None)),
    ("family_unresolved", ("abstained", "unresolved_relation", None)),
    ("family_registry_stale", ("abstained", "unresolved_relation", None)),
    ("relation_support_missing", ("abstained", "unresolved_relation", None)),
    ("unit_review_unavailable", ("abstained", "insufficient_evidence", None)),
    ("review_changed_during_resolution", ("abstained", "insufficient_evidence", None)),
    ("closure_cycle", ("abstained", "insufficient_evidence", None)),
    ("something_new", ("abstained", "insufficient_evidence", None)),
])
def test_blocked_retrieval_maps_to_controlled_public_outcomes(reason, expected):
    decision = classify_retrieval_outcome(retrieval([], status="blocked", reason_code=reason))
    assert (decision.status, decision.reason_code, decision.clarification_code) == expected
    assert decision.detail_code == f"retrieval:{reason}"


@pytest.mark.parametrize("reason", ["no_allowed_sources", "no_eligible_instruments", "no_eligible_chunks", "insufficient_evidence", "no_ranked_evidence"])
def test_abstained_retrieval_is_insufficient_evidence(reason):
    decision = classify_retrieval_outcome(retrieval([], status="abstained", reason_code=reason))
    assert (decision.status, decision.reason_code) == ("abstained", "insufficient_evidence")


def test_clarification_from_retrieval_keeps_typed_code():
    decision = classify_retrieval_outcome(
        retrieval([], status="needs_clarification", reason_code="clarification_needed", clarification_code="selection_ambiguous"))
    assert (decision.status, decision.reason_code, decision.clarification_code) == (
        "needs_clarification", "ambiguous_instrument", "selection_ambiguous")


def test_answered_retrieval_without_units_is_never_answerable():
    decision = classify_retrieval_outcome(retrieval([], status="answered"))
    assert (decision.status, decision.reason_code) == ("abstained", "insufficient_evidence")


@pytest.mark.parametrize("message,expected", [
    ("generation_access_denied_or_stale", ("error", "forbidden")),
    ("policy_authority_identity_changed", ("error", "service_unavailable")),
    ("generation_active_unavailable", ("error", "service_unavailable")),
    ("generation_not_ready", ("error", "service_unavailable")),
    ("generation_relation_registry_stale", ("abstained", "unresolved_relation")),
    ("family_review_unavailable", ("abstained", "unresolved_relation")),
    ("generation_result_not_authorized", ("abstained", "insufficient_evidence")),
    ("generation_review_changed_during_return", ("abstained", "insufficient_evidence")),
    ("unit_review_unavailable", ("abstained", "insufficient_evidence")),
    ("anything_unknown", ("error", "internal_error")),
])
def test_authority_errors_are_classified_without_leaking_details(message, expected):
    assert classify_authority_error(ValueError(message))[:2] == expected
    assert classify_authority_error(RuntimeError("chroma exploded with CNPJ 11222333000144"))[:2] == ("error", "service_unavailable")


def test_term_coverage_ignores_instrument_identifying_terms_but_checks_literal_content():
    clause = make_unit("unit:a", "Cláusula 1ª - A confidencialidade perdura por cinco anos.")
    ctx = build_offered_context(retrieval([clause]), QueryPlan(question_item_ids=["q1"]))
    checkable, absent = term_coverage("Qual a multa do contrato Alpha?", ctx_proof(ctx))
    assert [t.folded for t in checkable] == ["multa"] and [t.folded for t in absent] == ["multa"]
    checkable, absent = term_coverage("Qual o prazo de confidencialidade?", ctx_proof(ctx))
    assert [t.folded for t in absent] == ["prazo"] and len(checkable) == 2
    assert term_coverage("Quais as cláusulas da Alpha?", ctx_proof(ctx)) == ([], [])


def ctx_proof(ctx):
    class Offered:
        units = ctx.units
    return Offered()


def test_warnings_that_do_not_fit_the_code_alphabet_are_canonicalized_not_dropped():
    clause = make_unit("unit:a", "Cláusula 1ª - honorários.")
    long_id = "rel:" + "x" * 230
    result = retrieval([clause], warnings=["known_modifier:" + long_id, "historical_text_not_effect", "Texto livre com CPF 111.222.333-44"])
    ctx = build_offered_context(result, QueryPlan(question_item_ids=["q1"]))
    assert "historical_text_not_effect" in ctx.warnings
    assert len(ctx.warnings) == 3 and all(len(w) <= 90 for w in ctx.warnings)
    assert any(w.startswith("known_modifier:sha256-") for w in ctx.warnings)
    assert not any("111.222" in w for w in ctx.warnings)


def test_term_coverage_does_not_count_party_names_as_content_support():
    clause = make_unit("unit:p", "Cláusula 1ª - A Contratante Alpha pagará.")
    ctx = build_offered_context(retrieval([clause]), QueryPlan(question_item_ids=["q1"]))
    checkable, absent = term_coverage("Qual a multa da Alpha?", ctx_proof(ctx))
    assert [t.folded for t in checkable] == ["multa"] and [t.folded for t in absent] == ["multa"]

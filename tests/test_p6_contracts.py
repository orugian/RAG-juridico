"""P6 contracts: ResponsePayload and AnswerAudit are closed, internally consistent and ChatResponse-compatible."""
import pytest
from pydantic import ValidationError

from app.answer_contracts import (
    AnswerAudit, AttemptRecord, ClarificationOption, ProofItem, RenderedUnitAudit, ResponsePayload,
    ValidationFinding,
)
from app.contracts import Citation, CitationLocation, CitationParty, SourceSpan
from app.models import ChatResponse

SHA = "a" * 64


def citation(**override):
    base = dict(
        citation_id="cit:u1", contract_id="doc:101", contract_title="Contrato de Honorários", document_version=1,
        parties=[CitationParty(name="Alpha Serviços Ltda", role="contratante")],
        location=CitationLocation(label="Cláusula 1ª", clause="Cláusula 1ª"),
        quote="Cláusula 1ª - Os honorários são de R$ 50.000,00.", source_id="doc:101:v1:file:f101", evidence_id="chunk:1",
    )
    return Citation(**(base | override))


def proof(**override):
    base = dict(unit_id="unit:1", role="selected", question_item_ids=["q1"], relation_ids=[], citation=citation())
    return ProofItem(**(base | override))


def answered(**override):
    base = dict(
        request_id="req-1", status="answered", corpus_generation_id="gen-1", response_text="Resposta extrativa.",
        proof=[proof()],
    )
    return ResponsePayload(**(base | override))


def abstained(**override):
    base = dict(
        request_id="req-1", status="abstained", reason_code="insufficient_evidence", corpus_generation_id="gen-1",
        response_text="A informação solicitada sobre multa não foi localizada nos contratos disponíveis na base de dados do escritório Andrade Advogados.",
    )
    return ResponsePayload(**(base | override))


def test_answered_requires_selected_proof_and_no_reason():
    payload = answered()
    assert [c.citation_id for c in payload.citations] == ["cit:u1"]
    for bad in (dict(proof=[]), dict(reason_code="insufficient_evidence"), dict(proof=[proof(role="closure")])):
        with pytest.raises(ValidationError):
            answered(**bad)


def test_controlled_responses_have_no_proof_and_require_a_reason():
    assert abstained().citations == []
    with pytest.raises(ValidationError):
        abstained(proof=[proof()])
    with pytest.raises(ValidationError):
        abstained(reason_code=None)
    with pytest.raises(ValidationError):
        abstained(clarification_code="instrument_ambiguous")


def test_clarification_requires_code_reason_and_bounded_authorized_options():
    options = [ClarificationOption(instrument_id=f"doc:{n}", contract_title=f"Contrato {n}") for n in range(10)]
    payload = ResponsePayload(
        request_id="req-1", status="needs_clarification", reason_code="ambiguous_instrument",
        corpus_generation_id="gen-1", response_text="Esclareça o instrumento.",
        clarification_code="instrument_ambiguous", clarification_options=options,
    )
    assert len(payload.clarification_options) == 10
    for bad in (
        dict(clarification_code=None),
        dict(reason_code="insufficient_evidence"),
        dict(clarification_options=options + [ClarificationOption(instrument_id="doc:x", contract_title="X")]),
    ):
        with pytest.raises(ValidationError):
            ResponsePayload(**(dict(
                request_id="req-1", status="needs_clarification", reason_code="ambiguous_instrument",
                corpus_generation_id="gen-1", response_text="Esclareça.", clarification_code="instrument_ambiguous",
            ) | bad))


def test_payload_rejects_unknown_fields_duplicates_and_free_reason_codes():
    with pytest.raises(ValidationError):
        answered(raw_model_text="invented")
    with pytest.raises(ValidationError):
        answered(proof=[proof(), proof(unit_id="unit:2")])
    with pytest.raises(ValidationError):
        abstained(reason_code="model_said_so")
    with pytest.raises(ValidationError):
        answered(proof=[proof(), proof(unit_id="unit:1", citation=citation(citation_id="cit:other"))])


@pytest.mark.parametrize("make", [answered, abstained])
def test_chat_fields_are_accepted_by_existing_chat_response(make):
    payload = make()
    fields = payload.chat_fields()
    chat = ChatResponse(thread_id="t", processing_time_ms=1.0, **fields)
    assert chat.status == payload.status and chat.corpus_generation_id == "gen-1"
    assert [c.quote for c in chat.citations] == [c.quote for c in payload.citations]


def audit(**override):
    span = SourceSpan(source_id="doc:101:v1:file:f101", block_id="b1", start=0, end=10)
    base = dict(
        request_id="req-1", question_sha256=SHA, generation_id="gen-1", query_plan_version="query-plan-v1",
        retrieval_config_version="cfg1", relation_registry_version="v1", access_scope_digest="scope",
        evidence_scope="linked_instruments", prompt_version="p6-extractive-v1", validator_version="p6-validator-v1",
        renderer_version="p6-renderer-v1", generator_names=["deterministic-synthetic-v1"],
        retrieval_status="answered", final_status="answered",
        attempts=[AttemptRecord(stage="primary", outcome="candidate", latency_ms=1.0)],
        selected_unit_ids=["unit:1"],
        rendered_units=[RenderedUnitAudit(
            unit_id="unit:1", instrument_id="doc:101", source_ids=["doc:101:v1:file:f101"],
            file_hashes={"doc:101:v1:file:f101": SHA}, block_ids=["b1"], spans=[span], chunk_ids=["chunk:1"],
            review_record_id="rec:1", quote_sha256=SHA, role="selected",
        )],
        evidence_ids=["chunk:1"], findings=[ValidationFinding(code="candidate_accepted")], response_sha256=SHA,
    )
    return AnswerAudit(**(base | override))


def test_audit_is_closed_and_never_carries_question_or_quote_text():
    record = audit()
    dumped = record.model_dump_json()
    assert "Cláusula" not in dumped and "honorários" not in dumped
    for forbidden in ("question", "quote", "response_text", "chain_of_thought", "reasoning"):
        assert not any(forbidden == name for name in AnswerAudit.model_fields)
    with pytest.raises(ValidationError):
        audit(question="Qual a multa?")
    with pytest.raises(ValidationError):
        audit(question_sha256="not-a-digest")
    with pytest.raises(ValidationError):
        audit(final_status="invented")


def test_audit_finding_codes_are_fixed_vocabulary_not_free_text():
    ValidationFinding(code="closure_blocked:closure_over_budget")
    for bad in ("Qual a multa de 10%?", "x" * 200, ""):
        with pytest.raises(ValidationError):
            ValidationFinding(code=bad)

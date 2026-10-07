"""Executable boundary contracts; all examples are synthetic."""
import importlib
import socket
from unittest.mock import patch

import pytest
from pydantic import ValidationError


def contracts():
    return importlib.import_module("app.contracts")


@pytest.mark.parametrize("action,payload", [
    ("select", {}),
    ("abstain", {"selections": [{"unit_id": "u1", "question_item_ids": ["q1"]}]}),
    ("clarify", {}),
    ("select", {"selections": [{"unit_id": "u1", "question_item_ids": ["q1"]}], "quote": "inventado"}),
    ("select", {"selections": [{"unit_id": "u1", "question_item_ids": ["q1"]}] * 2}),
    ("select", {"selections": [{"unit_id": "u1", "question_item_ids": []}]}),
    ("abstain", {"approved_fact_ids": ["f1"]}),
    ("abstain", {"clarification_code": "instrument_ambiguous"}),
])
def test_generation_candidate_rejects_unproved_or_inconsistent_payload(action, payload):
    with pytest.raises(ValidationError):
        contracts().GenerationCandidate(action=action, **payload)


def test_generation_candidate_can_only_select_references():
    c = contracts()
    candidate = c.GenerationCandidate(action="select", selections=[{"unit_id": "u1", "question_item_ids": ["q1"]}])
    assert candidate.selections[0].unit_id == "u1"
    assert c.GenerationCandidate(action="abstain").selections == []
    assert c.GenerationCandidate(action="clarify", clarification_code="instrument_ambiguous").action == "clarify"
    assert set(candidate.model_json_schema()["properties"]) == {"action", "selections", "approved_fact_ids", "clarification_code"}


@pytest.mark.parametrize("value,expected", [
    ("123.456.789-00", "12345678900"), ("12.345.678/0001-90", "12345678000190"),
    ("ab.cde.123/xy45-67", "ABCDE123XY4567"),
])
def test_identifier_normalization_preserves_letters(value, expected):
    from app.identifiers import normalize_identifier
    assert normalize_identifier(value) == expected


@pytest.mark.parametrize("value", ["123", "1234567890O", "AB.CDE.123/XY45-ZZ", "12@345678000190", "１２３４５６７８９００", "123/456.789--00", "12/345.678.0001--90", "AB/CD.E123XY45--67"])
def test_identifier_normalization_rejects_ambiguous_or_invalid_input(value):
    from app.identifiers import normalize_identifier
    with pytest.raises(ValueError):
        normalize_identifier(value)


def test_parsing_success_does_not_grant_eligibility():
    from app.ingestion.schemas import ParsedDocument
    parsed = ParsedDocument(doc_id=1, doc_version=1, file_path="synthetic.docx", file_hash="hash", parser_name="synthetic", parser_version="1", metadata={"formal_title": "Synthetic", "instrument_type": "Contrato"}, blocks=[{"block_id": "b1", "doc_id": 1, "doc_version": 1, "block_type": "clause", "hierarchy_level": "clause", "order_index": 0, "text_raw": "Synthetic", "text_search": "Synthetic"}])
    assert parsed.eligibility == "pending_review"
    assert parsed.approval_record_id is None


def test_query_filter_does_not_accept_authorization_fields():
    from app.models import ChatRequest
    with pytest.raises(ValidationError):
        ChatRequest(message="consulta", filters={"principal_id": "admin"})
    with pytest.raises(ValidationError):
        ChatRequest(message="consulta", access_context={"operator": True})


def test_query_plan_defaults_to_linked_instruments():
    c = contracts()
    plan = c.QueryPlan(question_item_ids=["q1"])
    assert plan.evidence_scope == "linked_instruments"
    assert plan.reference_date is None
    with pytest.raises(ValidationError):
        c.QueryPlan(question_item_ids=["q1", "q1"])


def test_relation_approval_requires_review_and_supported_effect():
    c = contracts()
    relation = dict(relation_id="r1", from_instrument_id="addendum", to_instrument_id="base", relation_type="amends", snapshot="s1", configuration_version="v1")
    assert c.InstrumentRelation(**relation).state == "proposed"
    with pytest.raises(ValidationError):
        c.InstrumentRelation(**relation, state="approved")
    with pytest.raises(ValidationError):
        c.InstrumentRelation(**(relation | {"to_instrument_id": "addendum"}))
    approved = c.InstrumentRelation(**relation, state="approved", affected_unit_ids=["base:u1"], support_unit_ids=["addendum:u1"], review_record_id="review1")
    assert approved.review_record_id == "review1"


def test_approved_citation_unit_requires_provenance_and_decision():
    c = contracts()
    minimal = dict(unit_id="u1", instrument_id="i1", verbatim_text="Cláusula sintética.", source_ids=["s1"], block_ids=["b1"], spans=[{"source_id": "s1", "block_id": "b1", "start": 0, "end": 19}], location={"label": "Cláusula 1"})
    with pytest.raises(ValidationError):
        c.CitationUnit(**minimal, approval_state="approved")
    unit = c.CitationUnit(**minimal, approval_state="approved", review_record_id="decision1")
    assert unit.closure_unit_ids == []
    with pytest.raises(ValidationError):
        c.CitationUnit(**(minimal | {"spans": [{"source_id": "s1", "block_id": "b1", "start": 2, "end": 1}]}))


def test_settings_do_not_require_provider_secret_for_local_work():
    from app.config import Settings
    settings = Settings(_env_file=None)
    assert settings.langsmith_tracing_v2 is False
    assert settings.embedding_model is None
    assert settings.request_timeout_seconds == 30


def test_settings_secrets_are_redacted_and_limits_validated():
    from app.config import Settings
    settings = Settings(_env_file=None, openai_api_key="synthetic-secret", api_secret_key="synthetic-api-secret")
    assert "synthetic-secret" not in repr(settings)
    assert "synthetic-api-secret" not in settings.model_dump_json()
    with pytest.raises(ValidationError):
        Settings(_env_file=None, request_timeout_seconds=-1)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="production", embedding_backend="hash")


def test_agent_import_does_not_construct_provider_clients():
    with patch("langchain_openai.ChatOpenAI", side_effect=AssertionError("client created on import")):
        module = importlib.import_module("app.agent")
        importlib.reload(module)


def test_offline_profile_blocks_external_network():
    with socket.socket() as connection:
        with pytest.raises(RuntimeError, match="offline"):
            connection.connect(("203.0.113.1", 443))


def test_settings_read_neither_host_secrets_nor_dotenv():
    from app.config import get_settings
    assert get_settings().openai_api_key.get_secret_value() == "offline-test-key"
    assert not get_settings().mfiles_password.get_secret_value()
    assert not get_settings().langsmith_api_key.get_secret_value()


def test_access_context_is_internal_and_has_finite_permissions():
    c = contracts()
    context = dict(principal_id="p1", credential_id="k1", permissions=["query"], policy_epoch=0, access_scope_digest="digest")
    assert c.AccessContext(**context).corpus_scope == "internal_common"
    for alteration in ({"permissions": ["admin"]}, {"permissions": []}, {"policy_epoch": -1}, {"corpus_scope": "client1"}):
        with pytest.raises(ValidationError):
            c.AccessContext(**(context | alteration))


def test_public_response_never_exposes_private_provenance():
    from app.models import ChatResponse
    controlled = dict(response="Sem prova suficiente", thread_id="t1", request_id="r1", processing_time_ms=1, corpus_generation_id="g1", status="abstained", reason_code="insufficient_evidence")
    assert ChatResponse(**controlled).model_used is None
    with pytest.raises(ValidationError):
        ChatResponse(**(controlled | {"status": "answered"}))
    assert not {"file_path", "file_hash", "spans"} & set(contracts().Citation.model_fields)


def test_public_citation_and_error_follow_approved_json_contract():
    from app.models import ErrorResponse
    citation = contracts().Citation(citation_id="c1", contract_id="i1", contract_title="Instrumento sintético", document_version=1, parties=[{"name": "Empresa Sintética", "role": "contratante"}], location={"label": "Cláusula 1"}, quote="Texto sintético", source_id="s1", evidence_id="e1")
    assert set(citation.model_dump()) == {"citation_id", "contract_id", "contract_title", "document_version", "parties", "location", "quote", "source_id", "evidence_id"}
    assert ErrorResponse(code="service_unavailable", message="Indisponível", request_id="r1").code == "service_unavailable"
    for payload in ({"message": "Falha"}, {"code": "invalid", "message": "Falha", "request_id": "r1"}, {"code": "service_unavailable", "message": "Falha", "request_id": "r1", "stack_trace": "privado"}):
        with pytest.raises(ValidationError):
            ErrorResponse(**payload)


def test_retrieval_ranks_and_coverage_must_reference_declared_ids():
    c = contracts()
    result = dict(generation_id="g1", query_plan_version="v1", access_scope_digest="a1", retrieval_config_version="v1", relation_registry_version="v1", evidence_ids=["e1"], effective_filters={}, requested_question_item_ids=["q1"], retrieval_mode="hybrid")
    assert c.RetrievalResult(**result).covered_question_item_ids == []
    for alteration in ({"covered_question_item_ids": ["q2"]}, {"ranks": [{"evidence_id": "e2", "component": "rrf", "rank": 1, "score": .5}]}):
        with pytest.raises(ValidationError):
            c.RetrievalResult(**(result | alteration))

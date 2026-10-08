"""Declarative synthetic oracles, not human G5/G8 or model-quality evidence."""
from dataclasses import FrozenInstanceError, replace
import pytest

from app.contracts import Citation
from app.models import ChatResponse
from app.evaluation.e2e import E2EExpectation, evaluate_response, equivalent_answers, aggregate_results


def literal_citation():
    return Citation(
        citation_id="cit:unit-1", contract_id="synthetic-contract-1",
        contract_title="Contrato sintético de terceiros", document_version=2,
        parties=[{"name": "Cliente Sintético", "role": "locatário"},
                 {"name": "Terceiro Sintético", "role": "locador"}],
        location={"label": "Cláusula 4ª, § 2º", "clause": "4ª", "paragraph": "2º"},
        quote="O pagamento será devido se houver entrega; não será devido em caso de perda.",
        source_id="synthetic-source-1", evidence_id="synthetic-evidence-1",
    )


def oracle(case_id="synthetic-answered"):
    # Created from independent literals BEFORE any response.
    return E2EExpectation(
        case_id=case_id, expected_status="answered", expected_reason=None,
        expected_citations=[literal_citation()], expected_generation_id="synthetic-generation-1",
    )


def valid_answer():
    # Fake response built from separate literals; never read the expectation.
    return ChatResponse(
        response="Apresentação extrativa sintética.", thread_id="thread-1",
        model_used="synthetic-selector-v1", processing_time_ms=1.0,
        request_id="request-1", status="answered", corpus_generation_id="synthetic-generation-1",
        citations=[Citation(
            citation_id="cit:unit-1", contract_id="synthetic-contract-1",
            contract_title="Contrato sintético de terceiros", document_version=2,
            parties=[{"name": "Cliente Sintético", "role": "locatário"},
                     {"name": "Terceiro Sintético", "role": "locador"}],
            location={"label": "Cláusula 4ª, § 2º", "clause": "4ª", "paragraph": "2º"},
            quote="O pagamento será devido se houver entrega; não será devido em caso de perda.",
            source_id="synthetic-source-1", evidence_id="synthetic-evidence-1",
        )],
    )


def test_exact_known_declarative_proof_passes():
    expectation = oracle()
    result = evaluate_response(expectation, valid_answer())
    assert result.passed
    assert result.case_id == "synthetic-answered"
    assert result.findings == ()


@pytest.mark.parametrize("field,value,code", [
    ("contract_id", "other-contract", "instrument_mismatch"),
    ("contract_title", "Outro título", "title_mismatch"),
    ("document_version", 3, "version_mismatch"),
    ("parties", [{"name": "Andrade Advogados", "role": "locador"}], "parties_mismatch"),
    ("location", {"label": "Cláusula 5ª", "clause": "5ª"}, "location_mismatch"),
    ("quote", "O pagamento será devido.", "quote_mismatch"),
    ("source_id", "other-source", "source_mismatch"),
    ("evidence_id", "other-evidence", "evidence_mismatch"),
    ("citation_id", "cit:other-unit", "citation_id_mismatch"),
])
def test_rejects_one_at_a_time_changes_to_proof(field, value, code):
    expectation = oracle()
    response = valid_answer()
    changed = response.citations[0].model_dump()
    changed[field] = value
    response.citations = [Citation.model_validate(changed)]
    result = evaluate_response(expectation, response)
    assert not result.passed
    assert code in result.findings
    assert response.citations[0].quote not in repr(result)


CONTROLLED_LITERAL = "A prova necessária excede o orçamento de contexto disponível; nenhuma resposta parcial foi emitida."


def controlled_oracle(case_id="synthetic-controlled", status="abstained", reason="budget_exceeded"):
    return E2EExpectation(
        case_id, status, reason, [], "synthetic-generation-1",
        expected_response=CONTROLLED_LITERAL,
    )


def controlled_answer(status="abstained", reason="budget_exceeded"):
    return ChatResponse(
        response=CONTROLLED_LITERAL, thread_id="thread-1", processing_time_ms=2.0,
        request_id="request-2", status=status, reason_code=reason,
        corpus_generation_id="synthetic-generation-1", citations=[],
    )


@pytest.mark.parametrize("status,reason", [
    ("abstained", "budget_exceeded"), ("needs_clarification", "ambiguous_instrument"),
])
def test_controlled_action_requires_exact_predeclared_text_and_no_proof(status, reason):
    expectation = controlled_oracle(status=status, reason=reason)
    response = controlled_answer(status=status, reason=reason)
    assert evaluate_response(expectation, response).passed
    response.response = "SECRET: A cláusula determina um pagamento não comprovado."
    finding = evaluate_response(expectation, response)
    assert not finding.passed
    assert finding.findings == ("response_text_mismatch",)
    assert "SECRET" not in repr(finding)


def test_controlled_oracle_cannot_omit_known_literal():
    with pytest.raises(ValueError, match="invalid_expectation"):
        E2EExpectation("synthetic-controlled", "abstained", "budget_exceeded", [], "generation-1")


@pytest.mark.parametrize("kind", ["duplicate", "duplicate_new_id", "duplicate_new_ids", "extraneous", "omitted"])
def test_duplicate_extraneous_and_omitted_proofs_fail_closed(kind):
    expectation = oracle()
    response = valid_answer()
    if kind == "omitted":
        response.citations.clear()  # bypasses ChatResponse's constructor validator
    else:
        other = literal_citation()
        if kind in {"duplicate_new_id", "duplicate_new_ids"}:
            other.citation_id = "cit:forged-duplicate"
            if kind == "duplicate_new_ids":
                other.evidence_id = "forged-evidence"
        elif kind == "extraneous":
            other.citation_id = "cit:unrelated"
            other.evidence_id = "unrelated-evidence"
            other.quote = "Conteúdo sem relação com a consulta."
        response.citations.append(other)
    result = evaluate_response(expectation, response)
    assert not result.passed
    assert ("response_invalid" if kind == "omitted" else "citation_count_mismatch") in result.findings
    if kind.startswith("duplicate"):
        assert "duplicate_proof" in result.findings


@pytest.mark.parametrize("field,value,code", [
    ("status", "abstained", "response_invalid"),
    ("reason_code", "budget_exceeded", "response_invalid"),
    ("corpus_generation_id", "other-generation", "generation_mismatch"),
    ("corpus_generation_id", "", "response_invalid"),
])
def test_response_is_revalidated_not_trusted_after_mutation(field, value, code):
    expectation = oracle()
    response = valid_answer().model_copy(update={field: value})
    result = evaluate_response(expectation, response)
    assert not result.passed
    assert code in result.findings


def test_party_order_is_not_a_set_and_unicode_literal_is_not_normalized():
    expectation = oracle()
    response = valid_answer()
    response.citations[0].parties.reverse()
    assert "parties_mismatch" in evaluate_response(expectation, response).findings
    response = valid_answer()
    response.citations[0].quote = response.citations[0].quote.replace("não", "na\u0303o")
    assert "quote_mismatch" in evaluate_response(expectation, response).findings


def test_expected_proof_cannot_be_changed_through_input_or_returned_aliases():
    citation = literal_citation()
    original_list = [citation]
    expectation = E2EExpectation("immutable-oracle", "answered", None, original_list, "synthetic-generation-1")
    citation.quote = "Outro literal"
    original_list.clear()
    expectation.expected_citations[0].parties[0].name = "Outra parte"
    expectation.expected_citations.clear()
    assert evaluate_response(expectation, valid_answer()).passed
    wrong = valid_answer()
    wrong.citations[0].quote = "Outro literal"
    assert not evaluate_response(expectation, wrong).passed
    with pytest.raises(AttributeError):
        expectation.expected_generation_id = "other-generation"


@pytest.mark.parametrize("kind", ["duplicate", "bad_nested", "unsafe_id", "reason", "empty"])
def test_invalid_declarative_oracles_are_rejected_without_content_dump(kind):
    citations = [literal_citation()]
    case_id = "safe-id"
    reason = None
    if kind == "duplicate":
        citations.append(literal_citation())
    elif kind == "bad_nested":
        citations[0].__dict__["quote"] = ""
    elif kind == "unsafe_id":
        case_id = "SECRET-content\n"
    elif kind == "reason":
        reason = "budget_exceeded"
    elif kind == "empty":
        citations = []
    with pytest.raises(ValueError, match="^invalid_expectation$") as error:
        E2EExpectation(case_id, "answered", reason, citations, "generation-1")
    assert "SECRET" not in str(error.value)
    assert "Contrato" not in str(error.value)


@pytest.mark.parametrize("field,value", [
    ("cached", True), ("request_id", "request-99"), ("timestamp", "different-timestamp"),
    ("processing_time_ms", 200.0), ("thread_id", "another-thread"),
])
def test_cache_equivalence_ignores_only_runtime_envelope(field, value):
    a = valid_answer()
    b = a.model_copy(deep=True, update={field: value})
    assert equivalent_answers(a, b)
    assert equivalent_answers(b, a)


@pytest.mark.parametrize("kind", ["response", "model", "generation", "citation_id", "quote", "parties", "controlled", "invalid"])
def test_cache_equivalence_keeps_all_answer_and_proof_fields(kind):
    a = valid_answer()
    b = a.model_copy(deep=True)
    if kind == "response":
        b.response += " Uma conclusão extra."
    elif kind == "model":
        b.model_used = "other-selector"
    elif kind == "generation":
        b.corpus_generation_id = "other-generation"
    elif kind == "citation_id":
        b.citations[0].citation_id = "cit:other-id"
    elif kind == "quote":
        b.citations[0].quote += " Outro trecho."
    elif kind == "parties":
        b.citations[0].parties.reverse()
    elif kind == "controlled":
        b = controlled_answer()
    elif kind == "invalid":
        a.citations.clear()
        b.citations.clear()
    assert not equivalent_answers(a, b)
    assert not equivalent_answers(b, a)


def test_aggregate_reports_separate_denominators_and_synthetic_only_gate():
    answered = oracle()
    controlled = controlled_oracle()
    results = [evaluate_response(answered, valid_answer()), evaluate_response(controlled, controlled_answer())]
    report = aggregate_results([answered, controlled], results)
    assert report.complete and report.passed
    assert report.expected_cases == report.evaluated_cases == 2
    assert report.answered_cases == report.controlled_cases == 1
    assert report.fidelity == report.action_accuracy == report.complete_answer_accuracy == 1.0
    assert report.citations_expected == report.citations_emitted == report.citations_correct == 1
    assert report.origin == "synthetic"
    assert report.human_acceptance is False
    assert report.findings == ()


@pytest.mark.parametrize("kind", ["empty", "missing", "duplicate_result", "duplicate_oracle", "unexpected", "wrong_oracle"])
def test_aggregate_fails_closed_on_incomplete_or_mismatched_runs(kind):
    expectation = oracle()
    cases = [expectation]
    results = [evaluate_response(expectation, valid_answer())]
    if kind == "empty":
        cases, results = [], []
    elif kind == "missing":
        results = []
    elif kind == "duplicate_result":
        results *= 2
    elif kind == "duplicate_oracle":
        cases *= 2
    elif kind == "unexpected":
        results.append(evaluate_response(oracle("not-requested"), valid_answer()))
    elif kind == "wrong_oracle":
        altered = literal_citation()
        altered.quote = "Outro literal declarado."
        cases = [E2EExpectation(expectation.case_id, "answered", None, [altered], "synthetic-generation-1")]
    report = aggregate_results(cases, results)
    assert not report.passed
    assert not report.complete
    assert report.findings


def test_zero_denominators_are_na_and_respondible_abstention_is_not_success():
    expected = oracle()
    result = evaluate_response(expected, controlled_answer())
    report = aggregate_results([expected], [result])
    assert report.complete and not report.passed
    assert report.answered_cases == 1
    assert report.controlled_cases == 0
    assert report.fidelity == report.complete_answer_accuracy == 0.0
    assert report.action_accuracy is None
    assert report.citation_fidelity is None
    controlled = controlled_oracle()
    controlled_only = aggregate_results([controlled], [evaluate_response(controlled, controlled_answer())])
    assert controlled_only.passed
    assert controlled_only.fidelity is None
    assert controlled_only.action_accuracy == 1.0
    empty = aggregate_results([], [])
    assert empty.fidelity is empty.action_accuracy is empty.complete_answer_accuracy is None


@pytest.mark.parametrize("field,value", [
    ("findings", ()), ("citations_correct", 1), ("citations_emitted", 2),
    ("case_id", "other-case"), ("expected_status", "abstained"),
])
def test_mutating_or_replacing_a_result_cannot_forge_aggregate_success(field, value):
    expected = oracle()
    response = valid_answer()
    response.citations[0].quote = "Literal errado"
    bad = evaluate_response(expected, response)
    assert not bad.passed
    with pytest.raises(FrozenInstanceError):
        bad.findings = ()
    forged = replace(bad, **{field: value})
    report = aggregate_results([expected], [forged])
    assert not report.passed
    assert not report.complete
    assert "invalid_result" in report.findings


def test_a_result_for_another_oracle_cannot_be_relabelled():
    expected = oracle()
    other = oracle("other-case")
    relabelled = replace(evaluate_response(other, valid_answer()), case_id=expected.case_id)
    assert not aggregate_results([expected], [relabelled]).passed


def test_invalid_values_and_duplicates_do_not_leak_into_aggregate_diagnostics():
    report = aggregate_results(["SECRET arbitrary oracle"], ["SECRET arbitrary result"])
    assert not report.passed and not report.complete
    assert report.findings == ("invalid_expectation", "invalid_result")
    assert "SECRET" not in repr(report)


def test_invalid_evaluator_oracle_errors_are_fixed_and_sanitized():
    with pytest.raises(ValueError, match="^invalid_expectation$"):
        evaluate_response("SECRET arbitrary oracle", valid_answer())


@pytest.mark.parametrize("expectations,results", [(None, None), ("SECRET", "SECRET"), ([], None)])
def test_invalid_aggregate_collections_fail_closed(expectations, results):
    report = aggregate_results(expectations, results)
    assert not report.passed and not report.complete
    assert "SECRET" not in repr(report)


def test_replacing_aggregate_fields_cannot_forge_technical_or_human_acceptance():
    expected = oracle()
    wrong = valid_answer()
    wrong.citations[0].quote = "Texto errado"
    report = aggregate_results([expected], [evaluate_response(expected, wrong)])
    forged = replace(report, findings=(), answered_correct=1, citations_correct=1)
    assert not forged.passed
    assert forged.human_acceptance is False


def test_duplicate_oracle_proof_with_new_evidence_and_citation_ids_is_rejected():
    one = literal_citation()
    duplicated = literal_citation()
    duplicated.citation_id = "cit:duplicate"
    duplicated.evidence_id = "duplicated-evidence"
    with pytest.raises(ValueError, match="^invalid_expectation$"):
        E2EExpectation("duplicate-literal", "answered", None, [one, duplicated], "generation-1")


@pytest.mark.parametrize("field,value,code", [
    ("status", "needs_clarification", "response_invalid"),
    ("reason_code", "invalid_candidate", "reason_mismatch"),
    ("corpus_generation_id", "other-generation", "generation_mismatch"),
])
def test_controlled_status_reason_and_generation_are_exact(field, value, code):
    expected = controlled_oracle()
    response = controlled_answer().model_copy(update={field: value})
    result = evaluate_response(expected, response)
    assert not result.passed
    assert code in result.findings
    report = aggregate_results([expected], [result])
    assert report.complete and not report.passed
    assert report.action_accuracy == 0.0


def test_controlled_output_with_proof_is_invalid_even_after_mutation():
    expected = controlled_oracle()
    response = controlled_answer()
    response.citations.append(literal_citation())
    assert evaluate_response(expected, response).findings == ("response_invalid",)


@pytest.mark.parametrize("field,value", [
    ("label", "Cláusula 4ª, § 3º"), ("clause", "5ª"), ("paragraph", "3º"),
    ("annex", "II"), ("page", 9),
])
def test_each_location_component_is_exact(field, value):
    expected = oracle()
    response = valid_answer()
    setattr(response.citations[0].location, field, value)
    assert "location_mismatch" in evaluate_response(expected, response).findings


@pytest.mark.parametrize("field,value", [("name", "Outra pessoa"), ("role", "contratante")])
def test_each_party_component_is_exact(field, value):
    expected = oracle()
    response = valid_answer()
    setattr(response.citations[0].parties[0], field, value)
    assert "parties_mismatch" in evaluate_response(expected, response).findings


@pytest.mark.parametrize("quote", [
    "O pagamento será devido se houver entrega; será devido em caso de perda.",
    " O pagamento será devido se houver entrega; não será devido em caso de perda.",
    "O pagamento será devido se houver entrega; não será devido em caso de perda.\n",
])
def test_literal_never_trims_whitespace_or_drops_negation(quote):
    expected = oracle()
    response = valid_answer()
    response.citations[0].quote = quote
    assert "quote_mismatch" in evaluate_response(expected, response).findings


def test_optional_complete_answer_literal_rejects_extra_unsupported_prose():
    expected = E2EExpectation("full-literal", "answered", None, [literal_citation()],
                              "synthetic-generation-1", expected_response="Apresentação extrativa sintética.")
    response = valid_answer()
    assert evaluate_response(expected, response).passed
    response.response += " O contrato está vigente."
    assert evaluate_response(expected, response).findings == ("response_text_mismatch",)


def test_missing_controlled_case_stays_in_its_denominator_and_blocks_pass():
    expected = oracle()
    controlled = controlled_oracle()
    report = aggregate_results([expected, controlled], [evaluate_response(expected, valid_answer())])
    assert report.fidelity == 1.0
    assert report.action_accuracy == 0.0
    assert report.controlled_cases == 1
    assert not report.complete and not report.passed


def test_equal_words_from_distinct_sources_are_not_duplicate_and_order_is_exact():
    second_literal = literal_citation()
    second_literal.citation_id = "cit:unit-2"
    second_literal.contract_id = "synthetic-contract-2"
    second_literal.contract_title = "Outro instrumento sintético"
    second_literal.source_id = "synthetic-source-2"
    second_literal.evidence_id = "synthetic-evidence-2"
    expected = E2EExpectation("two-proofs", "answered", None,
                              [literal_citation(), second_literal], "synthetic-generation-1")
    response = valid_answer()
    second_response = Citation.model_validate({
        "citation_id": "cit:unit-2", "contract_id": "synthetic-contract-2",
        "contract_title": "Outro instrumento sintético", "document_version": 2,
        "parties": [{"name": "Cliente Sintético", "role": "locatário"},
                    {"name": "Terceiro Sintético", "role": "locador"}],
        "location": {"label": "Cláusula 4ª, § 2º", "clause": "4ª", "paragraph": "2º"},
        "quote": "O pagamento será devido se houver entrega; não será devido em caso de perda.",
        "source_id": "synthetic-source-2", "evidence_id": "synthetic-evidence-2",
    })
    response.citations.append(second_response)
    assert evaluate_response(expected, response).passed
    response.citations.reverse()
    reversed_result = evaluate_response(expected, response)
    assert not reversed_result.passed
    assert "instrument_mismatch" in reversed_result.findings


@pytest.mark.parametrize("field,value", [("document_version", "2"), ("document_version", True), ("quote", 123)])
def test_nested_oracle_schema_revalidation_never_coerces_bad_types(field, value):
    citation = literal_citation().model_copy(update={field: value})
    with pytest.raises(ValueError, match="^invalid_expectation$"):
        E2EExpectation("strict-nested", "answered", None, [citation], "generation-1")


def test_identically_duplicated_answers_are_not_valid_cache_equivalents():
    a = valid_answer()
    a.citations.append(literal_citation())
    b = a.model_copy(deep=True)
    assert not equivalent_answers(a, b)


@pytest.mark.parametrize("status,reason", [
    ("abstained", "ambiguous_instrument"), ("abstained", "ambiguous_time"),
    ("needs_clarification", "insufficient_evidence"), ("needs_clarification", "invalid_candidate"),
    ("needs_clarification", "unresolved_relation"), ("needs_clarification", "budget_exceeded"),
])
def test_incompatible_oracle_status_reason_is_rejected(status, reason):
    with pytest.raises(ValueError, match="^invalid_expectation$"):
        E2EExpectation("invalid-action", status, reason, [], "generation-1",
                       expected_response=CONTROLLED_LITERAL)


@pytest.mark.parametrize("status,reason", [
    ("abstained", "insufficient_evidence"), ("abstained", "invalid_candidate"),
    ("abstained", "unresolved_relation"), ("abstained", "budget_exceeded"),
    ("needs_clarification", "ambiguous_instrument"), ("needs_clarification", "ambiguous_time"),
])
def test_each_p6_admissible_status_reason_can_be_declared(status, reason):
    expected = controlled_oracle(status=status, reason=reason)
    response = controlled_answer(status=status, reason=reason)
    assert evaluate_response(expected, response).passed


@pytest.mark.parametrize("status,reason", [
    ("abstained", "ambiguous_instrument"), ("abstained", "ambiguous_time"),
    ("needs_clarification", "insufficient_evidence"), ("needs_clarification", "invalid_candidate"),
    ("needs_clarification", "unresolved_relation"), ("needs_clarification", "budget_exceeded"),
])
def test_incompatible_response_status_reason_is_invalid_not_cache_equivalent(status, reason):
    valid_reason = "budget_exceeded" if status == "abstained" else "ambiguous_instrument"
    expected = controlled_oracle(status=status, reason=valid_reason)
    response = controlled_answer(status=status, reason=reason)  # ChatResponse admits these crossed pairs
    result = evaluate_response(expected, response)
    assert not result.passed
    assert result.findings == ("response_invalid",)
    assert not aggregate_results([expected], [result]).passed
    assert not equivalent_answers(response, response.model_copy(deep=True))

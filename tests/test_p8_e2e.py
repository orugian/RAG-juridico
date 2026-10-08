"""P8 synthetic qualification; local fixed Qwen and maintained Chroma, never human acceptance."""
from pathlib import Path
from dataclasses import asdict
import hashlib
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.answer_runtime import GovernedAnswerRuntime
from app.cache import GovernedResponseCache
from app.http_contracts import HttpConfig, authenticate
from app.main import create_app
from app.retrieval.vector_store import ChromaVectorStore
from tests.test_api import configuration, QUERY_KEY
from tests.test_p4_vector_store import LocalServer
from tests.p8_corpus import (build_p8_environment, case_request, select_units, expected_citations, FAMILY_FEES,
                             CASES, MATRIX, expected_response_text, unit_id)
from app.models import ChatResponse
from app.generation import AnswerConfig, AnswerRequest
from tests.test_p6_pipeline import Scripted
from app.evaluation.e2e import E2EExpectation, evaluate_response, equivalent_answers, aggregate_results


@pytest.fixture(scope="module")
def qwen(request):
    artifact = request.config.getoption("--qwen-artifact")
    if not artifact:
        pytest.fail("P8 qualification requires explicit existing fixed local Qwen artifact")
    from app.embeddings.qwen import QwenEmbeddings
    from app.ingestion.evidence import QwenTokenBudget
    return QwenEmbeddings(Path(artifact)), QwenTokenBudget(Path(artifact))


@pytest.fixture(scope="module", params=["embedded", "server"])
def physical(request, tmp_path_factory, qwen):
    mode = request.param
    root = tmp_path_factory.mktemp("p8-" + mode)
    server = LocalServer(root) if mode == "server" else None
    try:
        if server:
            server.start()
        encoder, budget = qwen
        store = ChromaVectorStore(mode=mode, dimension=1024,
                                  **({"host": "127.0.0.1", "port": server.port} if server else {}))
        yield build_p8_environment(root / "env", encoder=encoder, budget=budget, vector_store=store), server
    finally:
        if server:
            server.stop()


def test_family_preserves_base_two_addenda_and_termination(physical):
    env, _ = physical
    runtime = GovernedAnswerRuntime(env.manager, select_units(env, (801, 0)),
                                   cache=GovernedResponseCache(), token_counter=env.budget.count)
    with TestClient(create_app(runtime=runtime, settings=configuration(),
                               http_config=HttpConfig(rate_requests=100))) as client:
        result = client.post("/v1/answer", headers={"X-API-Key": QUERY_KEY},
                             json=case_request("linked_fees"))
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["status"] == "answered", data
    expected = expected_citations(env, FAMILY_FEES)
    assert {c["contract_id"] for c in data["citations"]} == {"doc:801", "doc:802", "doc:803", "doc:804"}
    by_id = {c["citation_id"]: c for c in data["citations"]}
    assert by_id == {c.citation_id: c.model_dump(mode="json") for c in expected}
    assert "não indica prevalência" in data["response"]


def test_controlled_oracle_is_declared_without_system_output():
    assert expected_response_text(None, CASES["missing_term"]) == (
        "A informação solicitada sobre regra e criptomoedas não foi localizada nos contratos "
        "disponíveis na base de dados do escritório Andrade Advogados.")


@pytest.fixture(scope="module")
def matrix_records():
    return {}


@pytest.mark.parametrize("case", MATRIX, ids=lambda case: case.case_id)
def test_matrix_json_miss_hit_and_buffered_sse_are_exact(physical, case, matrix_records):
    env, _ = physical
    # Freeze the literal oracle before constructing/executing the answer runtime.
    transports = ("json_miss", "json_hit", "sse_hit")
    expectations = [E2EExpectation(case.case_id + "." + transport, case.status, case.reason,
                                   expected_citations(env, case.expected), env.generation_id,
                                   expected_response=expected_response_text(env, case)) for transport in transports]
    runtime = GovernedAnswerRuntime(env.manager, select_units(env, *case.selection),
                                   cache=GovernedResponseCache(), token_counter=env.budget.count)
    evaluations, responses = [], []
    with TestClient(create_app(runtime=runtime, settings=configuration(),
                               http_config=HttpConfig(rate_requests=100))) as client:
        for expectation, route in zip(expectations, ("/v1/answer", "/v1/answer", "/v1/answer/stream")):
            reply = client.post(route, headers={"X-API-Key": QUERY_KEY}, json=case_request(case.case_id))
            assert reply.status_code == 200, reply.text
            if route.endswith("stream"):
                assert reply.text.startswith("event: answer\ndata: ")
                assert reply.text.count("event: answer") == 1
                data = json.loads(reply.text.split("data: ", 1)[1])
            else:
                data = reply.json()
            response = ChatResponse.model_validate(data)
            evaluation = evaluate_response(expectation, response)
            assert evaluation.passed, evaluation.findings
            assert response.request_id == reply.headers["X-Request-ID"]
            responses.append(response)
            evaluations.append(evaluation)
    assert [r.cached for r in responses] == [False, True, True]
    assert len({r.request_id for r in responses}) == 3
    assert all(equivalent_answers(responses[0], r) for r in responses[1:])
    # The public prepared-answer port exposes the audit without exposing it over HTTP.
    prepared = runtime.execute(AnswerRequest.model_validate(case_request(case.case_id)),
                               authenticate(configuration(), QUERY_KEY), thread_id="p8-audit",
                               request_id="p8-audit-" + case.case_id, deadline=time.monotonic() + 30)
    assert equivalent_answers(responses[0], prepared.response)
    assert prepared.response.cached is True and prepared.audit.attempts == []
    assert set(prepared.audit.selected_unit_ids) == ({unit_id(env, doc, i) for doc, i in case.selection}
                                                     if case.status == "answered" else set())
    assert len(prepared.audit.rendered_units) == len(case.expected)
    assert {u.unit_id for u in prepared.audit.rendered_units} == {unit_id(env, doc, i) for doc, i in case.expected}
    for rendered, citation in zip(sorted(prepared.audit.rendered_units, key=lambda u: u.unit_id),
                                  sorted(expected_citations(env, case.expected), key=lambda c: c.citation_id[4:])):
        assert rendered.quote_sha256 == hashlib.sha256(citation.quote.encode()).hexdigest()
        assert rendered.source_ids == [citation.source_id]
        assert rendered.file_hashes[citation.source_id] == hashlib.sha256(env.paths[citation.source_id].read_bytes()).hexdigest()
        assert citation.evidence_id in rendered.chunk_ids
        assert rendered.spans
        assert all(span.source_id == citation.source_id and span.start == 0 and span.end == len(citation.quote)
                   for span in rendered.spans)
    emitted = []
    runtime.commit(prepared, emitted.append)
    assert ChatResponse.model_validate_json(emitted[0]) == prepared.response
    matrix_records.setdefault(env.vector_store.mode, {})[case.case_id] = (expectations, evaluations, responses)


def test_matrix_report_requires_every_declared_case_and_transport(physical, matrix_records):
    env, _ = physical
    records = matrix_records.get(env.vector_store.mode, {})
    expectations = [E2EExpectation(case.case_id + "." + transport, case.status, case.reason,
                                   expected_citations(env, case.expected), env.generation_id,
                                   expected_response=expected_response_text(env, case))
                    for case in MATRIX for transport in ("json_miss", "json_hit", "sse_hit")]
    results = [result for _, evaluations, _ in records.values() for result in evaluations]
    report = aggregate_results(expectations, results)
    assert report.passed and report.complete, report.findings
    assert (report.expected_cases, report.answered_cases, report.controlled_cases) == (48, 27, 21)
    assert report.fidelity == report.action_accuracy == report.citation_fidelity == 1
    assert report.origin == "synthetic" and report.human_acceptance is False
    safe = {k: v for k, v in asdict(report).items() if not k.startswith("_")}
    safe.update(backend=env.vector_store.mode, logical_cases=len(MATRIX), profile="local_fixed_qwen_1024_synthetic",
                fidelity=report.fidelity, complete_answer_accuracy=report.complete_answer_accuracy,
                action_accuracy=report.action_accuracy, citation_fidelity=report.citation_fidelity)
    print("P8_MATRIX " + json.dumps(safe, sort_keys=True))


@pytest.mark.parametrize("budget_field", ["max_prompt_tokens", "retrieval_max_tokens"])
def test_context_budgets_refuse_without_truncation_and_recover(physical, budget_field):
    env, _ = physical
    generator = Scripted(select_units(env, (801, 0)).generate(None))
    cache = GovernedResponseCache()
    limited = GovernedAnswerRuntime(env.manager, generator, cache=cache, token_counter=env.budget.count,
                                    config=AnswerConfig(**{budget_field: 1}))
    with TestClient(create_app(runtime=limited, settings=configuration())) as client:
        reply = client.post("/v1/answer", headers={"X-API-Key": QUERY_KEY}, json=case_request("linked_fees"))
    assert reply.status_code == 200
    response = ChatResponse.model_validate(reply.json())
    expectation = E2EExpectation("budget." + budget_field, "abstained", "budget_exceeded", [], env.generation_id,
                                expected_response="A prova necessária excede o orçamento de contexto disponível; "
                                                  "nenhuma resposta parcial foi emitida.")
    assert evaluate_response(expectation, response).passed
    assert generator.requests == [], "budget must precede the candidate generation call"
    unrestricted = GovernedAnswerRuntime(env.manager, generator, cache=cache, token_counter=env.budget.count)
    with TestClient(create_app(runtime=unrestricted, settings=configuration())) as client:
        miss = ChatResponse.model_validate(client.post("/v1/answer", headers={"X-API-Key": QUERY_KEY},
                                                        json=case_request("linked_fees")).json())
        hit = ChatResponse.model_validate(client.post("/v1/answer", headers={"X-API-Key": QUERY_KEY},
                                                       json=case_request("linked_fees")).json())
    assert miss.status == "answered" and miss.cached is False and hit.cached is True
    assert equivalent_answers(miss, hit) and len(generator.requests) == 1
    assert evaluate_response(E2EExpectation("budget.recovery", "answered", None, expected_citations(env, FAMILY_FEES),
                                           env.generation_id, expected_response=expected_response_text(env, CASES["linked_fees"])), miss).passed


@pytest.mark.parametrize("scenario", ["pending", "conflicted"])
def test_unresolved_modifier_blocks_applicable_condition_but_preserves_history(physical, tmp_path, scenario):
    base_env, server = physical
    mode = base_env.vector_store.mode
    store = ChromaVectorStore(mode=mode, dimension=1024,
                              **({"host": "127.0.0.1", "port": server.port} if server else {}))
    env = build_p8_environment(tmp_path, encoder=base_env.encoder, budget=base_env.budget,
                               vector_store=store, scenario=scenario)
    # Both oracles are declared before either request, including historical warning order.
    linked = E2EExpectation("relation." + scenario, "abstained", "unresolved_relation", [], env.generation_id,
                            expected_response="Existe relação documental entre instrumentos ainda não resolvida ou revisada; "
                                              "sem fechamento aprovado não é possível apresentar o trecho como condição aplicável.")
    old_warning = ("- Existe modificador registrado (p8rel:803:amends:801) que não integra esta apresentação histórica; "
                   "o texto é histórico e não indica regra vigente.\n")
    historical_text = expected_response_text(env, CASES["historical_fees"]).replace(old_warning, "")
    historical_text = historical_text.replace("Avisos:\n", "Avisos:\n- A família documental p8fam:801 não está "
                                              "resolvida; não há fechamento aprovado.\n")
    historical_text += "\n- A relação p8rel:803:amends:801 está pendente ou em conflito; não há prova de efeito."
    historic = E2EExpectation("relation.historic." + scenario, "answered", None,
                              expected_citations(env, CASES["historical_fees"].expected), env.generation_id,
                              expected_response=historical_text)
    generator = Scripted(select_units(env, (801, 0)).generate(None))
    runtime = GovernedAnswerRuntime(env.manager, generator, cache=GovernedResponseCache(), token_counter=env.budget.count)
    with TestClient(create_app(runtime=runtime, settings=configuration())) as client:
        # History is tested independently, never a fallback for the linked request.
        historical = client.post("/v1/answer", headers={"X-API-Key": QUERY_KEY}, json=case_request("historical_fees"))
        assert historical.status_code == 200
        result = evaluate_response(historic, ChatResponse.model_validate(historical.json()))
        assert result.passed, result.findings
        assert len(generator.requests) == 1
        blocked = client.post("/v1/answer", headers={"X-API-Key": QUERY_KEY}, json=case_request("linked_fees"))
        assert blocked.status_code == 200, {"body": blocked.json(), "readiness": env.manager.readiness()}
        result = evaluate_response(linked, ChatResponse.model_validate(blocked.json()))
        assert result.passed, result.findings
        assert len(generator.requests) == 1, "unresolved linked proof must not reach the candidate generator"


def test_multiple_question_items_keep_each_instrument_and_item_separate(physical):
    env, _ = physical
    case = CASES["union"]
    text = expected_response_text(env, case)
    text = text.replace("Prova 1.1 — Localização: Cláusula 1ª (selecionada; indicada para: q1)",
                        "Prova 1.1 — Localização: Cláusula 1ª (selecionada; indicada para: Locação Gamma)")
    text = text.replace("Prova 2.1 — Localização: Cláusula 1ª (selecionada; indicada para: q1)",
                        "Prova 2.1 — Localização: Cláusula 1ª (selecionada; indicada para: Honorários Alpha)")
    expectation = E2EExpectation("multi.items", "answered", None, expected_citations(env, case.expected),
                                 env.generation_id, expected_response=text)
    request = case_request("union")
    request["question"] = "Qual o valor dos honorários de Alpha? Qual o valor da locação de Gamma?"
    request["plan"]["question_item_ids"] = ["q1", "q2"]
    request["item_labels"] = {"q1": "Honorários Alpha", "q2": "Locação Gamma"}
    generator = Scripted({"action": "select", "approved_fact_ids": [], "clarification_code": None,
                          "selections": [{"unit_id": unit_id(env, 801, 0), "question_item_ids": ["q1"]},
                                         {"unit_id": unit_id(env, 1001, 0), "question_item_ids": ["q2"]}]})
    runtime = GovernedAnswerRuntime(env.manager, generator, cache=GovernedResponseCache(), token_counter=env.budget.count)
    with TestClient(create_app(runtime=runtime, settings=configuration())) as client:
        miss = client.post("/v1/answer", headers={"X-API-Key": QUERY_KEY}, json=request)
        hit = client.post("/v1/answer", headers={"X-API-Key": QUERY_KEY}, json=request)
    assert miss.status_code == hit.status_code == 200
    first, second = ChatResponse.model_validate(miss.json()), ChatResponse.model_validate(hit.json())
    result = evaluate_response(expectation, first)
    assert result.passed, result.findings
    assert equivalent_answers(first, second) and first.cached is False and second.cached is True
    assert len(generator.requests) == 1 and generator.requests[0].question_item_ids == ("q1", "q2")

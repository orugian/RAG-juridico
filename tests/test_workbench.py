"""Workbench: traces, feedback, evaluation and review decisions over a governed synthetic corpus."""
import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.service import ServiceSettings, build_runtime, compose
from app.workbench.store import WorkbenchStore
from app.workbench.tracing import check_case
from tests.p6_corpus import build_p6_environment
from tests.test_api import OPERATOR_KEY, QUERY_KEY
from tests.test_p6_pipeline import pick
from tests.test_service import FakeProviderClient, provider_settings

OPERATOR = {"X-API-Key": OPERATOR_KEY, "X-Reviewer": "revisora"}


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    root = tmp_path_factory.mktemp("workbench")
    env = build_p6_environment(root)
    config_path = root / "generation_config.json"
    config_path.write_text(env.manager.configuration.model_dump_json(), encoding="utf-8")
    service = ServiceSettings(_env_file=None, index_root=root / "indexes", authority_dir=root / "authority",
                              generation_config=config_path, journal_id="p6-test", qwen_artifact=root / "unused")
    workbench = WorkbenchStore(root / "workbench.sqlite")
    fake = FakeProviderClient(json.dumps(pick(env, "Cláusula 1ª")))
    settings = provider_settings()
    runtime = build_runtime(service, settings, encoder=env.encoder, budget=env.budget, vector_store=env.vector_store,
                            client_factory=lambda model, s: fake, workbench=workbench)
    app = compose(service, settings, runtime, workbench)
    with TestClient(app) as client:
        yield SimpleNamespace(env=env, client=client, workbench=workbench, fake=fake, runtime=runtime)


def ask(stack, message="Qual o valor dos honorários?", instruments=("doc:101",)):
    response = stack.client.post("/chat", headers={"X-API-Key": QUERY_KEY},
                                 json={"message": message, "filters": {"instrument_ids": list(instruments)}})
    assert response.status_code == 200, response.text
    return response.json()


def test_every_answer_is_traced_with_offered_selected_and_cited_units(stack):
    data = ask(stack)
    for _ in range(50):  # delivery is marked right after the guarded send returns
        trace = stack.workbench.trace(data["request_id"])
        if trace["delivered"]:
            break
        time.sleep(0.02)
    assert trace["status"] == data["status"] == "answered" and trace["delivered"]
    assert trace["offered"] and set(trace["selected"]) <= set(trace["offered"])
    assert [c["citation_id"] for c in trace["citations"]] == [c["citation_id"] for c in data["citations"]]
    assert trace["llm_raw"] and "select" in trace["llm_raw"]
    assert {"llm_s", "total_s"} <= set(trace["timings"]) and trace["response"] == data["response"]


def test_workbench_api_requires_operator_key_and_ui_is_public(stack):
    assert stack.client.get("/ui").status_code == 200
    assert "Bancada" in stack.client.get("/ui").text
    assert stack.client.get("/wb/api/overview").status_code == 401
    assert stack.client.get("/wb/api/overview", headers={"X-API-Key": QUERY_KEY}).status_code == 403
    overview = stack.client.get("/wb/api/overview", headers=OPERATOR).json()
    assert overview["documents"] == 5 and overview["reviewer"] == "revisora"
    assert stack.client.get("/health").json() == {"status": "ok"}  # P7 routes untouched


def test_trace_detail_shows_offered_unit_text_and_feedback_creates_case(stack):
    data = ask(stack, "Qual o valor dos honorários contratados?")
    detail = stack.client.get("/wb/api/traces/" + data["request_id"], headers=OPERATOR).json()
    assert detail["offered_units"] and all(u.get("text") for u in detail["offered_units"])
    body = {"request_id": data["request_id"], "rating": "bad", "category": "missing_clause", "comment": "faltou aditivo",
            "case": {"question": "Qual o valor dos honorários contratados?", "instrument_ids": ["doc:101"],
                     "expected_status": "answered", "expected_instrument_ids": ["doc:101", "doc:102"],
                     "expected_terms": ["75.000"]}}
    result = stack.client.post("/wb/api/feedback", headers=OPERATOR, json=body).json()
    assert result["case_id"]
    negatives = stack.client.get("/wb/api/traces?feedback=negative", headers=OPERATOR).json()
    assert [t["request_id"] for t in negatives] == [data["request_id"]]
    assert stack.client.post("/wb/api/feedback", headers=OPERATOR,
                             json=body | {"request_id": "nao-existe", "case": None}).status_code == 404


def test_evaluation_run_uses_governed_runtime_and_records_checks(stack):
    stack.workbench.add_case(question="Qual o valor dos honorários?", instrument_ids=["doc:101"],
                             expected_status="answered", expected_instrument_ids=["doc:101"], expected_terms=["50.000"])
    run_id = stack.client.post("/wb/api/eval/run", headers=OPERATOR).json()["run_id"]
    for _ in range(100):
        runs = stack.client.get("/wb/api/eval/runs", headers=OPERATOR).json()
        if not runs["busy"]:
            break
        time.sleep(0.1)
    [run] = [r for r in runs["runs"] if r["id"] == run_id]
    assert run["state"] == "finished" and run["summary"]["cases"] >= 1
    results = stack.client.get(f"/wb/api/eval/runs/{run_id}", headers=OPERATOR).json()
    assert all("status" in r["checks"] for r in results)
    assert any(r["passed"] for r in results)
    eval_traces = stack.workbench.traces(include_eval=True)
    assert any(t["request_id"].startswith("eval-") for t in eval_traces)
    assert not any(t["request_id"].startswith("eval-") for t in stack.workbench.traces())


def test_document_review_saves_parties_references_and_relations(stack):
    catalog = stack.client.get("/wb/api/catalog?q=alpha", headers=OPERATOR).json()
    assert catalog and all("alpha" in (d["title"] + " ".join(p["name"] for p in d["parties"])).lower() for d in catalog)
    source = next(d["source_id"] for d in catalog if d["instrument_id"] == "doc:101")
    document = stack.client.get("/wb/api/document/" + source, headers=OPERATOR).json()
    assert document["units"] and document["parties"]
    saved = stack.client.post("/wb/api/document/" + source, headers=OPERATOR, json={
        "parties": [{"name": "Alpha Serviços Ltda", "role": "contratante", "clean_identifier": "11.222.333/0001-44"}]})
    assert saved.status_code == 200
    assert stack.workbench.document_decision(source)["parties"][0]["clean_identifier"] == "11222333000144"
    unit = document["units"][0]
    reference = {"source_id": source, "block_id": unit["first_block"], "start": 0, "end": 5, "literal": "x"}
    assert stack.client.post("/wb/api/reference", headers=OPERATOR, json=reference | {"kind": "bind", "target_block_ids": []}).status_code == 400
    assert stack.client.post("/wb/api/reference", headers=OPERATOR,
                             json=reference | {"kind": "bind", "target_block_ids": [unit["first_block"]]}).status_code == 200
    amendment = stack.client.get("/wb/api/document/" + next(
        s.source_id for s in stack.env.bundle.sources if s.instrument_id == "doc:102"), headers=OPERATOR).json()
    relation = {"from_instrument_id": "doc:102", "to_instrument_id": "doc:101", "relation_type": "amends",
                "affected_block_ids": [unit["first_block"]], "support_block_ids": [amendment["units"][0]["first_block"]]}
    assert stack.client.post("/wb/api/relations", headers=OPERATOR, json=relation).status_code == 200
    assert stack.client.post("/wb/api/relations", headers=OPERATOR, json=relation | {"relation_type": "x"}).status_code == 400
    decisions = stack.workbench.decisions()
    assert source in decisions.parties and len(decisions.references) == 1 and len(decisions.relations) == 1
    pending = stack.client.get("/wb/api/overview", headers=OPERATOR).json()["pending_changes"]
    assert pending == {"document_decisions": 1, "reference_decisions": 1, "relation_decisions": 1}


def test_publish_endpoint_spawns_isolated_unit_with_argument_list(tmp_path, stack):
    from app.workbench.api import create_workbench_app
    calls = []
    app = create_workbench_app(runtime=stack.runtime, settings=provider_settings(), workbench=stack.workbench,
                               evaluation=None, data_root=tmp_path, pub="pub-x",
                               spawn=lambda command, **kwargs: calls.append(command) or SimpleNamespace(returncode=0))
    client = TestClient(app)
    assert client.post("/wb/api/publish", headers=OPERATOR, json={"reason": "ok"}).status_code == 422
    result = client.post("/wb/api/publish", headers=OPERATOR, json={"reason": "correções de partes; $(rm -rf /)"})
    assert result.status_code == 200 and result.json()["started"]
    [command] = calls
    assert command[:2] == ["systemd-run", "--user"] and "--activate" in command
    assert command[command.index("--reason") + 1] == "correções de partes; $(rm -rf /)"  # argv, never a shell
    assert command[command.index("--reviewer") + 1] == "revisora"
    assert command[command.index("--name") + 1].startswith("pub-") and "--publication-id" in command
    assert client.post("/wb/api/publish", headers=OPERATOR, json={"reason": "segunda tentativa"}).status_code == 409
    assert client.post("/wb/api/publish", headers=OPERATOR, json={"reason": "--strict"}).status_code == 400
    for row in stack.workbench.publications():
        if row["state"] == "running":
            stack.workbench.finish_publication(row["id"], "failed", {})


def test_check_case_compares_status_contracts_and_terms():
    response = {"status": "answered", "citations": [{"contract_id": "doc:1", "quote": "Honorários de R$ 50.000,00"}]}
    case = {"expected_status": "answered", "expected_instrument_ids": ["doc:1"], "expected_terms": ["honorarios", "50.000"]}
    assert check_case(case, response) == (True, {"status": True, "contracts": True, "terms": True})
    assert check_case(case | {"expected_instrument_ids": ["doc:2"]}, response)[0] is False
    assert check_case(case | {"expected_status": "abstained"}, response)[1]["status"] is False


def test_trace_recording_failure_never_breaks_the_answer(stack, monkeypatch):
    monkeypatch.setattr(stack.workbench, "record_trace", lambda trace: (_ for _ in ()).throw(RuntimeError("disk")))
    assert ask(stack, "Qual o valor dos honorários devidos?")["status"] == "answered"

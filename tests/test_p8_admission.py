"""P8 admission delta: documentary unresolved state is not a technical outage.

Receipt/fault tests use REAL private loopback Chroma, SYNTHETIC P6 3-D encoder
and budget; they do not claim Qwen proof. P8 E2E independently covers fixed
Qwen/1024-D Chroma in both modes. No embedded rename retries/sleeps/skips.
"""
import time

import pytest
from fastapi.testclient import TestClient

from app.answer_runtime import GovernedAnswerRuntime
from app.cache import GovernedResponseCache
from app.generation import AnswerRequest
from app.main import create_app
from tests.p6_corpus import build_p6_environment
from tests.test_api import QUERY_KEY, configuration
from tests.test_p6_pipeline import NeverCalled as P6NeverCalled, Q_FEES, plan
from tests.test_p4_vector_store import LocalServer
from app.retrieval.vector_store import ChromaVectorStore


class NeverCalled(P6NeverCalled):
    """Record forbidden physical calls even when the pipeline catches the failure."""
    def generate(self, request):
        self.requests.append(request)
        return super().generate(request)

HEADERS = {"X-API-Key": QUERY_KEY}
CONTROLLED = ("Existe relação documental entre instrumentos ainda não resolvida ou revisada; sem fechamento "
              "aprovado não é possível apresentar o trecho como condição aplicável.")


@pytest.fixture(scope="module")
def vector_server(tmp_path_factory):
    server = LocalServer(tmp_path_factory.mktemp("p8-admission-private"))
    try:
        server.start()
        yield server
    finally:
        server.stop()  # Own only this child, including start/setup failure.


def environment(root, server, scenario="standard"):
    vector = ChromaVectorStore(mode="server", dimension=3, host="127.0.0.1", port=server.port)
    return build_p6_environment(root, scenario=scenario, vector_store=vector)


@pytest.fixture(params=["pending_relation", "conflicted_relation"])
def unresolved(request, tmp_path, vector_server):
    return environment(tmp_path, vector_server, request.param)


def test_known_unresolved_admission_is_http_200_without_proof_generator_or_cache(unresolved):
    env = unresolved
    cache = GovernedResponseCache()
    runtime = GovernedAnswerRuntime(env.manager, NeverCalled(), cache=cache)
    request = AnswerRequest(question=Q_FEES, plan=plan()).model_dump(mode="json")
    with TestClient(create_app(runtime=runtime, settings=configuration())) as client:
        first = client.post("/v1/answer", headers=HEADERS, json=request)
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["status"] == "abstained" and body["reason_code"] == "unresolved_relation"
        assert body["response"] == CONTROLLED and body["citations"] == []
        assert body["model_used"] is None and body["cached"] is False
        assert body["corpus_generation_id"] == env.generation_id
        assert body["request_id"] == first.headers["x-request-id"]
        second = client.post("/v1/answer", headers=HEADERS, json=request)
        assert second.status_code == 200 and second.json()["request_id"] != body["request_id"]
        assert second.json()["cached"] is False
        assert cache.stats["cached_entries"] == cache.stats["hits"] == cache.stats["misses"] == 0
        assert "50.000" not in first.text and "75.000" not in first.text


def prepare(runtime, env, request_id="req-admission"):
    return runtime.execute(AnswerRequest(question=Q_FEES, plan=plan()), env.query_access(),
                           thread_id="only-correlation", request_id=request_id, deadline=time.monotonic()+30)


def test_admission_public_audit_json_sse_and_metrics_are_controlled(unresolved):
    import hashlib
    import json
    runtime = GovernedAnswerRuntime(unresolved.manager, NeverCalled(), cache=GovernedResponseCache())
    prepared = prepare(runtime, unresolved)
    audit = prepared.audit
    assert audit.retrieval_status == "blocked" and audit.retrieval_reason_code == "family_unresolved"
    assert audit.final_status == "abstained" and audit.final_reason_code == "unresolved_relation"
    assert audit.evidence_scope == "linked_instruments" and audit.generation_id == unresolved.generation_id
    assert audit.attempts == audit.rendered_units == audit.evidence_ids == audit.selected_unit_ids == []
    assert audit.prompt_tokens is None and audit.generator_names == [] and audit.error_code is None
    assert audit.response_sha256 == hashlib.sha256(CONTROLLED.encode()).hexdigest()
    assert runtime.metrics()["abstained_count"] == 0
    accepted = []
    runtime.commit(prepared, accepted.append, stream=True)
    assert len(accepted) == 1 and accepted[0].startswith(b"event: answer\ndata: ")
    body = json.loads(accepted[0].split(b"data: ", 1)[1])
    assert body["response"] == CONTROLLED and body["citations"] == []
    assert runtime.metrics()["abstained_count"] == 1
    again = prepare(runtime, unresolved, "req-admission-next")
    assert again.response.request_id != prepared.response.request_id
    assert again.response.timestamp != prepared.response.timestamp


def test_promotion_after_fresh_active_before_lease_rejects_stale_negative_receipt(unresolved, monkeypatch):
    from app.answer_contracts import AnswerServiceError
    env = unresolved
    runtime = GovernedAnswerRuntime(env.manager, NeverCalled())
    prepared = prepare(runtime, env)
    env.manager.build("gen-next", env.bundle, source_paths=env.paths, reuse_from=env.generation_id)
    original = env.manager._active
    calls = []

    def load_then_promote():
        loaded = original()
        calls.append(loaded.manifest.generation_id)
        env.manager.promote("gen-next", access=env.access)
        return loaded

    monkeypatch.setattr(env.manager, "_active", load_then_promote)
    emitted = []
    with pytest.raises(AnswerServiceError) as failure:
        runtime.commit(prepared, emitted.append)
    assert calls == [env.generation_id], "promotion must follow the fresh read, not precede commit"
    assert failure.value.code == "service_unavailable"
    assert emitted == [] and runtime.metrics()["abstained_count"] == 0


def test_every_prepared_admission_capability_field_and_other_runtime_reject_tamper(unresolved):
    from dataclasses import replace
    from app.answer_contracts import AnswerServiceError
    runtime = GovernedAnswerRuntime(unresolved.manager, NeverCalled())
    prepared = prepare(runtime, unresolved)
    mutations = [replace(prepared, body=b"private forged body"),
                 replace(prepared, response=prepared.response.model_copy(update={"response": "private text"})),
                 replace(prepared, audit=prepared.audit.model_copy(update={"final_reason_code": "invalid_candidate"})),
                 replace(prepared, access=prepared.access.model_copy(update={"principal_id": "foreign-principal"})),
                 replace(prepared, admission_policy=prepared.admission_policy.model_copy(update={"policy_epoch": 900})),
                 replace(prepared, ledger_digest="0"*64), replace(prepared, receipt=("foreign-generation",)),
                 replace(prepared, reason="family_registry_stale"), replace(prepared, deadline=prepared.deadline+10),
                 replace(prepared, integrity="0"*64)]
    for mutated in mutations:
        emitted = []
        with pytest.raises(AnswerServiceError) as failure:
            runtime.commit(mutated, emitted.append)
        assert failure.value.code == "service_unavailable" and emitted == []
    foreign = GovernedAnswerRuntime(unresolved.manager, NeverCalled())
    with pytest.raises(AnswerServiceError) as failure:
        foreign.commit(prepared, lambda body: pytest.fail("foreign runtime cannot accept the capability"))
    assert failure.value.code == "service_unavailable"
    assert runtime.metrics()["abstained_count"] == foreign.metrics()["abstained_count"] == 0


def test_arbitrary_or_spoofed_admission_errors_never_become_documentary_success(tmp_path, monkeypatch, vector_server):
    from app.ingestion.closure import _Blocked
    env = environment(tmp_path, vector_server)  # Resolved: no documentary negative is present.
    generator = NeverCalled()
    runtime = GovernedAnswerRuntime(env.manager, generator)
    payload = AnswerRequest(question=Q_FEES, plan=plan()).model_dump(mode="json")
    errors = [ValueError("family_unresolved"), _Blocked("family_unresolved"),
              _Blocked("family_fake_pending"), ValueError("relation_registry_secret"),
              OSError("private backend 12345678901"), RuntimeError("private generation error")]
    with TestClient(create_app(runtime=runtime, settings=configuration())) as client:
        for error in errors:
            def fail(*args, **kwargs):
                raise error
            with monkeypatch.context() as patched:
                patched.setattr(env.manager, "pin", fail)
                denied = client.post("/v1/answer", headers=HEADERS, json=payload)
            assert denied.status_code == 503, denied.text
            assert denied.json()["code"] == "service_unavailable"
            assert set(denied.json()) == {"code", "message", "request_id", "timestamp"}
            assert "12345678901" not in denied.text and "50.000" not in denied.text
        assert client.post("/v1/answer", headers={"X-API-Key": "invalid"}, json=payload).status_code == 401
    assert generator.requests == []


def test_expired_sealed_admission_deadline_remains_timeout(unresolved, monkeypatch):
    from app.answer_contracts import AnswerServiceError
    runtime = GovernedAnswerRuntime(unresolved.manager, NeverCalled())
    prepared = prepare(runtime, unresolved)
    monkeypatch.setattr("app.answer_runtime.time.monotonic", lambda: prepared.deadline+1)
    with pytest.raises(AnswerServiceError) as failure:
        runtime.commit(prepared, lambda body: pytest.fail("expired result cannot be emitted"))
    assert failure.value.code == "request_timeout"
    assert runtime.metrics()["abstained_count"] == 0


@pytest.mark.parametrize("mutation", ["credential", "source", "registry", "artifact", "backend", "root"])
def test_authority_storage_registry_or_root_change_cannot_emit_negative(unresolved, mutation, monkeypatch, tmp_path):
    from app.answer_contracts import AnswerServiceError
    env = unresolved
    runtime = GovernedAnswerRuntime(env.manager, NeverCalled())
    prepared = prepare(runtime, env)
    if mutation == "credential":
        env.journal.block("credential", prepared.access.credential_id, "controlled-credential-revoke")
    elif mutation == "source":
        env.block_source("doc:101")
    elif mutation == "registry":
        source = env.bundle.sources[0]
        env.ledger.record(source_id=source.source_id, snapshot=source.snapshot, file_hash=source.file_hash,
                          configuration_version=source.configuration_version, scope="relation", subject_id="rel:new",
                          subject_digest="e"*64, decision="approved", reviewer="p8-synthetic", reason="controlled registry update")
        # Even a stale/faulty general state_digest getter cannot hide the actual registry change.
        monkeypatch.setattr(env.manager.ledger, "state_digest", lambda: prepared.ledger_digest)
    elif mutation == "artifact":
        path = env.manager.root / env.generation_id / "bundle.json"
        path.write_bytes(path.read_bytes() + b" ")
    elif mutation == "backend":
        def offline(*args, **kwargs):
            raise OSError("controlled backend error with private content")
        monkeypatch.setattr(env.vector_store, "read", offline)
    else:
        loaded = env.manager._active()
        original_root = env.manager.root
        alias = tmp_path / "foreign-root"
        alias.mkdir()
        (alias / "active.json").write_bytes((original_root / "active.json").read_bytes())
        # Hold the same verified loaded snapshot while changing only its owning root.
        monkeypatch.setattr(env.manager, "root", alias)
        monkeypatch.setattr(env.manager, "_active", lambda: loaded)
    emitted = []
    with pytest.raises(AnswerServiceError) as failure:
        runtime.commit(prepared, emitted.append)
    assert failure.value.code == ("forbidden" if mutation == "credential" else "service_unavailable")
    assert emitted == [] and runtime.metrics()["abstained_count"] == 0


@pytest.mark.parametrize("authority", ["policy", "ledger"])
def test_admission_callback_exception_retains_authorities_until_unwind(unresolved, authority):
    import threading
    from app.answer_contracts import AnswerServiceError
    env = unresolved
    runtime = GovernedAnswerRuntime(env.manager, NeverCalled())
    prepared = prepare(runtime, env)
    entered, intent, acknowledged = threading.Event(), threading.Event(), threading.Event()
    errors = []

    def writer():
        try:
            assert entered.wait(10)
            intent.set()
            if authority == "policy":
                env.journal.block("source", env.bundle.sources[0].source_id, "p8-controlled-writer")
            else:
                source = env.bundle.sources[0]
                env.ledger.record(source_id=source.source_id, snapshot=source.snapshot, file_hash=source.file_hash,
                                  configuration_version=source.configuration_version, decision="approved",
                                  reviewer="p8-synthetic", reason="controlled source review update")
        except BaseException as error:
            errors.append(error)
        finally:
            acknowledged.set()

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()

    def aborted_emit(body):
        assert CONTROLLED.encode() in body and b"50.000" not in body
        entered.set()
        assert intent.wait(10)
        assert not acknowledged.wait(0.1), "writer cannot acknowledge while emission still holds its authority"
        raise RuntimeError("controlled private send failure")

    try:
        with pytest.raises(AnswerServiceError) as failure:
            runtime.commit(prepared, aborted_emit)
        assert failure.value.code == "internal_error"
        assert acknowledged.wait(10), "callback unwind must release both authorities"
        assert errors == [] and runtime.metrics()["abstained_count"] == 0
        assert runtime.generator.requests == []
    finally:
        entered.set()
        thread.join(10)
        assert not thread.is_alive(), "fixture-owned writer must not be orphaned"

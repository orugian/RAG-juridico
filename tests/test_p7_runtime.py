"""P7 runtime public boundary over physically governed P6 synthetic proof."""
import json
import time

import pytest

from app.answer_contracts import AnswerServiceError
from app.answer_runtime import GovernedAnswerRuntime
from app.generation import AnswerRequest
from tests.p6_corpus import build_p6_environment
from tests.test_p6_pipeline import Q_FEES, Scripted, pick, plan


@pytest.fixture(scope="module")
def shared(tmp_path_factory):
    return build_p6_environment(tmp_path_factory.mktemp("p7-runtime"))


def run(runtime, env, *, request_id="req-p7", request=None):
    return runtime.execute(request or AnswerRequest(question=Q_FEES, plan=plan()),
                           env.query_access(), thread_id="thread-correlation", request_id=request_id,
                           deadline=time.monotonic() + 30)


def test_governed_import_does_not_load_implicit_settings_or_dotenv():
    import subprocess
    import sys
    script = """
import app.config

def denied():
    raise AssertionError('implicit configuration load at import')
app.config.get_settings = denied
import app.generation
"""
    result = subprocess.run([sys.executable, "-c", script], check=False)
    assert result.returncode == 0


def test_runtime_prepares_and_commits_four_element_http_response(shared):
    runtime = GovernedAnswerRuntime(shared.manager, Scripted(pick(shared, "Cláusula 1ª")))
    prepared = run(runtime, shared)
    assert prepared.response.status == "answered"
    assert prepared.response.request_id == "req-p7"
    assert prepared.response.thread_id == "thread-correlation"
    sent = []
    runtime.commit(prepared, sent.append)
    response = json.loads(sent[0])
    assert {c["contract_id"] for c in response["citations"]} == {"doc:101", "doc:102"}
    canonical = {u.unit_id: u for u in shared.bundle.units}
    quotes = {u.verbatim_text for u in canonical.values()}
    assert all(c["quote"] in quotes and c["parties"] and c["location"] for c in response["citations"])
    assert response["cached"] is False


def test_admission_refreshes_epoch_and_rejects_revoked_credentials(tmp_path):
    env = build_p6_environment(tmp_path)
    runtime = GovernedAnswerRuntime(env.manager, Scripted(pick(env, "Cláusula 1ª")))
    old = env.query_access()
    env.journal.block("source", "unrelated-synthetic-source", "p7-test")
    current = runtime.authenticate_epoch(old)
    assert current.policy_epoch == 1
    assert current.access_scope_digest != old.access_scope_digest
    env.journal.block("credential", old.credential_id, "p7-test")
    with pytest.raises(AnswerServiceError) as failure:
        runtime.authenticate_epoch(old)
    assert failure.value.code == "forbidden"


def test_cache_hit_reuses_proof_but_not_request_correlation(shared):
    from app.cache import GovernedResponseCache
    generator = Scripted(pick(shared, "Cláusula 1ª"))
    runtime = GovernedAnswerRuntime(shared.manager, generator, cache=GovernedResponseCache())
    first = run(runtime, shared, request_id="req-first")
    runtime.commit(first, lambda body: None)
    second = run(runtime, shared, request_id="req-second")
    sent = []
    runtime.commit(second, sent.append, stream=True)
    assert first.response.cached is False
    assert second.response.cached is True
    assert len(generator.requests) == 1
    assert second.response.request_id == "req-second"
    assert sent[0].startswith(b"event: answer\ndata: ")
    assert b"req-first" not in sent[0]
    assert second.response.citations == first.response.citations


def test_control_prepared_bytes_revoke_and_positive_readiness_ledger_gate(tmp_path):
    from tests.p6_corpus import build_p6_environment
    env = build_p6_environment(tmp_path)
    runtime = GovernedAnswerRuntime(env.manager, Scripted(pick(env, "Cláusula 1ª")))
    encode = lambda raw: (200, json.dumps({"status": "ready"} if raw is True else raw).encode())
    prepared = runtime.prepare_control("ready", env.access, request_id="req-control-ready",
                                       deadline=time.monotonic()+30, encode=encode)
    assert prepared.status_code == 200 and prepared.ledger_digest == env.ledger.state_digest()
    env.journal.block("credential", env.access.credential_id, "control-test")
    sent = []
    with pytest.raises(AnswerServiceError) as error:
        runtime.commit_control(prepared, sent.append)
    assert error.value.code == "forbidden" and sent == []


def control_encode(raw):
    if type(raw) is bool:
        return (200 if raw else 503), json.dumps({"status": "ready" if raw else "unavailable"}).encode()
    return 200, json.dumps(raw).encode()


@pytest.mark.parametrize("operation", ["ready", "metrics", "policy"])
def test_control_success_encodes_once_before_commit_and_tamper_never_emits(shared, operation):
    from dataclasses import replace
    runtime = GovernedAnswerRuntime(shared.manager, Scripted(pick(shared, "Cláusula 1ª")))
    encodings = []

    def encode(raw):
        encodings.append(raw)
        return control_encode(raw)

    prepared = runtime.prepare_control(operation, shared.access, request_id="req-control-success",
                                       deadline=time.monotonic()+30, encode=encode)
    assert len(encodings) == 1
    assert (prepared.ledger_digest is not None) is (operation == "ready")
    emitted = []
    runtime.commit_control(prepared, emitted.append)
    assert emitted == [prepared.body] and len(encodings) == 1
    for changed in (replace(prepared, body=b'{"untrusted":true}'),
                    replace(prepared, status_code=503 if prepared.status_code == 200 else 200),
                    replace(prepared, deadline=prepared.deadline+100)):
        with pytest.raises(AnswerServiceError):
            runtime.commit_control(changed, emitted.append)
    assert emitted == [prepared.body]


@pytest.mark.parametrize("operation", ["metrics", "policy"])
def test_control_query_only_context_cannot_prepare_operator_output(shared, operation):
    runtime = GovernedAnswerRuntime(shared.manager, Scripted(pick(shared, "Cláusula 1ª")))
    with pytest.raises(AnswerServiceError) as error:
        runtime.prepare_control(operation, shared.query_access(), request_id="req-control-permission",
                                deadline=time.monotonic()+30, encode=control_encode)
    assert error.value.code == "forbidden"


def test_controls_do_not_pin_corpus_and_negative_readiness_keeps_policy_guard(shared, monkeypatch):
    runtime = GovernedAnswerRuntime(shared.manager, Scripted(pick(shared, "Cláusula 1ª")))

    def no_pin(*args, **kwargs):
        raise AssertionError("control output cannot depend on corpus pin")

    def no_ledger():
        raise RuntimeError("synthetic-ledger-unavailable")

    monkeypatch.setattr(shared.manager, "pin", no_pin)
    monkeypatch.setattr(shared.manager.ledger, "state_digest", no_ledger)
    for operation in ("metrics", "policy"):
        prepared = runtime.prepare_control(operation, shared.access, request_id="req-no-corpus",
                                           deadline=time.monotonic()+30, encode=control_encode)
        assert prepared.status_code == 200 and prepared.ledger_digest is None
        runtime.commit_control(prepared, lambda body: None)
    monkeypatch.setattr(runtime, "readiness", lambda: False)
    negative = runtime.prepare_control("ready", shared.access, request_id="req-negative-ready",
                                      deadline=time.monotonic()+30, encode=control_encode)
    assert negative.status_code == 503 and negative.ledger_digest is None
    sent = []
    runtime.commit_control(negative, sent.append)
    assert sent == [negative.body]


@pytest.mark.parametrize("operation", ["ready", "metrics", "policy"])
def test_control_serialization_deadline_is_not_ignored(shared, operation, monkeypatch):
    runtime = GovernedAnswerRuntime(shared.manager, Scripted(pick(shared, "Cláusula 1ª")))
    deadline = time.monotonic()+30

    def encode(raw):
        result = control_encode(raw)
        monkeypatch.setattr("app.answer_runtime.time.monotonic", lambda: deadline+1)
        return result

    with pytest.raises(AnswerServiceError) as error:
        runtime.prepare_control(operation, shared.access, request_id="req-control-timeout",
                                deadline=deadline, encode=encode)
    assert error.value.code == "request_timeout"


def test_control_errors_retain_current_server_request_correlation(shared, monkeypatch):
    runtime = GovernedAnswerRuntime(shared.manager, Scripted(pick(shared, "Cláusula 1ª")))

    def denied(*args):
        raise AnswerServiceError("forbidden", request_id="req-inner-policy")

    monkeypatch.setattr(runtime, "policy_status", denied)
    with pytest.raises(AnswerServiceError) as error:
        runtime.prepare_control("policy", shared.access, request_id="req-control-current",
                                deadline=time.monotonic()+30, encode=control_encode)
    assert error.value.request_id == "req-control-current"


@pytest.mark.parametrize("operation", ["ready", "metrics", "policy"])
@pytest.mark.parametrize("disconnect", [False, True])
def test_control_policy_lease_retains_writer_until_emit_unwinds(tmp_path, operation, disconnect):
    import threading
    env = build_p6_environment(tmp_path)
    runtime = GovernedAnswerRuntime(env.manager, Scripted(pick(env, "Cláusula 1ª")))
    prepared = runtime.prepare_control(operation, env.access, request_id="req-control-lease",
                                       deadline=time.monotonic()+30, encode=control_encode)
    attempted, acknowledged = threading.Event(), threading.Event()
    errors = []

    def revoke():
        attempted.set()
        try:
            env.journal.block("credential", env.access.credential_id, "control-lease")
            acknowledged.set()
        except Exception as error:
            errors.append(type(error).__name__)

    writer = threading.Thread(target=revoke)

    def emit(body):
        assert body == prepared.body
        writer.start()
        assert attempted.wait(3)
        assert not acknowledged.wait(0.5), "revocation cannot ACK before the held emission unwinds"
        if disconnect:
            raise RuntimeError("synthetic-control-disconnect")

    try:
        if disconnect:
            with pytest.raises(AnswerServiceError) as error:
                runtime.commit_control(prepared, emit)
            assert error.value.code == "internal_error"
        else:
            runtime.commit_control(prepared, emit)
    finally:
        if writer.ident is not None:
            writer.join(5)
    assert not writer.is_alive() and acknowledged.is_set() and not errors
    with pytest.raises(AnswerServiceError):
        runtime.commit_control(prepared, lambda body: pytest.fail("revoked control cannot emit again"))
    assert runtime.metrics()["answered_count"] == 0


@pytest.mark.parametrize("authority", ["ledger", "policy"])
def test_control_prepared_authority_change_discards_snapshot_without_output(tmp_path, authority):
    from app.ingestion.review_store import unit_review_digest
    env = build_p6_environment(tmp_path)
    runtime = GovernedAnswerRuntime(env.manager, Scripted(pick(env, "Cláusula 1ª")))
    operation = "ready" if authority == "ledger" else "metrics"
    prepared = runtime.prepare_control(operation, env.access, request_id="req-control-authority",
                                       deadline=time.monotonic()+30, encode=control_encode)
    if authority == "ledger":
        unit = env.bundle.units[0]
        source = next(s for s in env.bundle.sources if s.source_id == unit.source_ids[0])
        env.ledger.record(source_id=source.source_id, snapshot=source.snapshot, file_hash=source.file_hash,
                          configuration_version=source.configuration_version, scope="unit", subject_id=unit.unit_id,
                          subject_digest=unit_review_digest(unit), decision="excluded",
                          reviewer="control-snapshot-test", reason="control-snapshot-test")
        assert env.journal.snapshot().policy_epoch == 0
    else:
        env.journal.block("source", env.bundle.sources[0].source_id, "control-snapshot-test")
    sent = []
    with pytest.raises(AnswerServiceError) as error:
        runtime.commit_control(prepared, sent.append)
    assert error.value.code == "service_unavailable" and sent == []


@pytest.mark.parametrize("kind", ["query", "control"])
def test_commit_keeps_explicit_telemetry_context_through_emit(shared, kind, monkeypatch):
    import app.telemetry as telemetry

    def denied():
        raise AssertionError("emit cannot load ambient dotenv configuration")

    monkeypatch.setattr(telemetry, "get_settings", denied)
    runtime = GovernedAnswerRuntime(shared.manager, Scripted(pick(shared, "Cláusula 1ª")))
    sent = []

    @telemetry.safe_trace(name="chain")
    def emit(body):
        sent.append(body)

    if kind == "control":
        prepared = runtime.prepare_control("metrics", shared.access, request_id="req-scoped-commit",
                                           deadline=time.monotonic()+30, encode=control_encode)
        runtime.commit_control(prepared, emit)
    else:
        prepared = run(runtime, shared)
        runtime.commit(prepared, emit)
    assert sent == [prepared.body]


def test_runtime_never_loads_ambient_telemetry_settings_during_execution(shared, monkeypatch):
    import app.telemetry as telemetry

    def denied():
        raise AssertionError("ambient dotenv/configuration lookup prohibited")

    monkeypatch.setattr(telemetry, "get_settings", denied)
    runtime = GovernedAnswerRuntime(shared.manager, Scripted(pick(shared, "Cláusula 1ª")))
    prepared = run(runtime, shared)
    sent = []
    runtime.commit(prepared, sent.append)
    assert prepared.response.status == "answered" and sent


def test_telemetry_context_is_thread_isolated_and_restored_without_global_patch(monkeypatch):
    import concurrent.futures
    import threading
    import app.telemetry as telemetry
    from app.config import Settings

    barrier = threading.Barrier(2)
    built, exported = [], []
    lock = threading.Lock()

    class Client:
        def create_run(self, name, inputs, run_type, **kwargs):
            with lock:
                exported.append(inputs)

        def update_run(self, *args, **kwargs):
            with lock:
                exported.append(kwargs.get("error"))

        def close(self):
            pass

    def factory(*, settings):
        with lock:
            built.append(settings.langsmith_project)
        return Client()

    def denied():
        raise AssertionError("global lookup forbidden")

    monkeypatch.setattr(telemetry, "get_settings", denied)
    monkeypatch.setattr(telemetry, "create_protected_client", factory)

    @telemetry.safe_trace(name="chain")
    def observed():
        barrier.wait(3)
        return "private synthetic quote"

    def worker(label):
        config = Settings(_env_file=None, langsmith_tracing_v2=True,
                          langsmith_api_key="synthetic-only", langsmith_project=label)
        with telemetry.telemetry_configuration(config):
            assert observed() == "private synthetic quote"
        # Scope must restore default; this calls denied rather than either sibling config.
        with pytest.raises(AssertionError, match="global lookup forbidden"):
            observed()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(worker, label) for label in ("scope-alpha", "scope-beta")]
        for future in results:
            future.result(5)
    assert sorted(built) == ["scope-alpha", "scope-beta"]
    assert all(value in ({}, None) for value in exported)


def test_runtime_closes_inherited_exporter_and_restores_outer_context_on_error(shared, monkeypatch):
    import app.telemetry as telemetry
    from app.config import Settings
    factories, runs = [], []

    class Client:
        def create_run(self, *args, **kwargs):
            runs.append(True)

        def update_run(self, *args, **kwargs):
            pass

        def close(self):
            pass

    def factory(*, settings):
        factories.append(settings.langsmith_project)
        return Client()

    monkeypatch.setattr(telemetry, "create_protected_client", factory)
    outer = Settings(_env_file=None, langsmith_tracing_v2=True,
                     langsmith_api_key="synthetic-only", langsmith_project="outer-context")

    @telemetry.safe_trace(name="chain")
    def inspect_after():
        return True

    runtime = GovernedAnswerRuntime(shared.manager, Scripted(pick(shared, "Cláusula 1ª")))
    @telemetry.safe_trace(name="chain")
    def inside_active_exporter():
        assert factories == ["outer-context"] and runs == [True]
        prepared = run(runtime, shared)
        assert prepared.response.status == "answered"
        with pytest.raises(AnswerServiceError):
            run(runtime, shared, request=AnswerRequest(question="  "))
        # Even an already active parent exporter must not receive P7 child runs.
        assert factories == ["outer-context"] and runs == [True]

    with telemetry.telemetry_configuration(outer):
        inside_active_exporter()
        assert inspect_after()
    assert factories == ["outer-context", "outer-context"] and runs == [True, True]


def test_prepared_audit_correlates_current_request_and_cache_origin(shared):
    from app.cache import GovernedResponseCache
    runtime = GovernedAnswerRuntime(shared.manager, Scripted(pick(shared, "Cláusula 1ª")),
                                   cache=GovernedResponseCache())
    first = run(runtime, shared, request_id="req-audit-first")
    second = run(runtime, shared, request_id="req-audit-second")
    assert first.audit.request_id == "req-audit-first"
    assert second.audit.request_id == "req-audit-second"
    assert second.cache_origin_request_id == "req-audit-first"
    assert second.audit.attempts == []
    assert Q_FEES not in second.audit.model_dump_json()
    assert "50.000" not in second.audit.model_dump_json()
    assert second.audit.rendered_units


def test_cache_hit_cannot_invent_literal_even_with_recomputed_hashes(shared):
    import copy
    import hashlib
    from app.answer_contracts import AnswerOutcome

    generator = Scripted(pick(shared, "Cláusula 1ª"))
    truthful = GovernedAnswerRuntime(shared.manager, generator)
    original = run(truthful, shared)
    # Public cache protocol is replaceable. It must never become an evidence authority.
    from app.generation import GovernedAnswerService
    honest = GovernedAnswerService(shared.pin(), generator=generator).answer(
        AnswerRequest(question=Q_FEES, plan=plan()))
    forged = copy.deepcopy(honest)
    item = forged.payload.proof[0]
    item.citation.quote = "Honorários inexistentes R$ 999.999,00"
    forged.audit.rendered_units[0].quote_sha256 = hashlib.sha256(item.citation.quote.encode()).hexdigest()
    forged.payload.response_text = "Prova falsa: " + item.citation.quote
    forged.audit.response_sha256 = hashlib.sha256(forged.payload.response_text.encode()).hexdigest()

    class ForgedCache:
        def get(self, identity):
            forged.audit.access_scope_digest = identity.access_digest
            return forged

        def set(self, identity, outcome):
            return False

    runtime = GovernedAnswerRuntime(shared.manager, generator, cache=ForgedCache())
    sent = []
    with pytest.raises(AnswerServiceError):
        prepared = run(runtime, shared)
        runtime.commit(prepared, sent.append)
    assert sent == [] and original.response.status == "answered"


@pytest.mark.parametrize("tamper", ["literal", "response", "audit"])
def test_valid_cache_signature_does_not_accept_tampering(shared, tamper):
    from app.cache import GovernedResponseCache

    class Tampered(GovernedResponseCache):
        corrupt = False

        def get(self, identity):
            outcome = super().get(identity)
            if outcome is not None and self.corrupt:
                if tamper == "literal":
                    outcome.payload.proof[0].citation.quote = "Transcrição inventada"
                elif tamper == "response":
                    outcome.payload.response_text = "Efeito jurídico inventado"
                else:
                    outcome.audit.rendered_units[0].instrument_id = "doc:invented"
            return outcome

    cache = Tampered()
    runtime = GovernedAnswerRuntime(shared.manager, Scripted(pick(shared, "Cláusula 1ª")), cache=cache)
    run(runtime, shared)
    cache.corrupt = True
    with pytest.raises(AnswerServiceError) as failure:
        run(runtime, shared, request_id="req-tampered")
    assert failure.value.code == "service_unavailable"


def test_scopes_and_provider_runtime_do_not_share_cached_proof(shared):
    from app.cache import GovernedResponseCache
    cache = GovernedResponseCache()
    generator = Scripted(pick(shared, "Cláusula 1ª"))
    runtime = GovernedAnswerRuntime(shared.manager, generator, cache=cache)
    linked = run(runtime, shared)
    original = run(runtime, shared, request=AnswerRequest(question=Q_FEES, plan=plan("original_text")))
    assert not original.response.cached and len(generator.requests) == 2
    assert {c.contract_id for c in linked.response.citations} == {"doc:101", "doc:102"}
    assert {c.contract_id for c in original.response.citations} == {"doc:101"}
    other = GovernedAnswerRuntime(shared.manager, generator, cache=cache)
    assert run(other, shared).response.cached is False
    assert len(generator.requests) == 3


def test_health_inspection_requires_operator_and_never_calls_generator(shared):
    generator = Scripted(pick(shared, "Cláusula 1ª"))
    runtime = GovernedAnswerRuntime(shared.manager, generator)
    assert runtime.readiness() is True
    with pytest.raises(AnswerServiceError) as failure:
        runtime.policy_status(shared.query_access())
    assert failure.value.code == "forbidden"
    status = runtime.policy_status(shared.access)
    assert status == {"policy_epoch": 0, "authority_current": True,
                      "blocked_sources": 0, "blocked_credentials": 0, "blocked_families": 0}
    assert not generator.requests


def test_abandoned_provider_call_keeps_physical_concurrency_slot(shared):
    import threading
    from app.generation import AnswerConfig

    entered, release = threading.Event(), threading.Event()
    calls = []

    class Blocking:
        name = "p7-blocking-test"

        def generate(self, request):
            calls.append(True)
            entered.set()
            release.wait(60)
            return pick(shared, "Cláusula 1ª")

    runtime = GovernedAnswerRuntime(shared.manager, Blocking(),
                                   config=AnswerConfig(total_timeout_seconds=10.0, max_primary_attempts=1),
                                   max_generator_calls=1)
    try:
        with pytest.raises(AnswerServiceError) as first:
            run(runtime, shared)
        assert first.value.code == "request_timeout" and entered.is_set()
        with pytest.raises(AnswerServiceError) as second:
            run(runtime, shared, request_id="req-second")
        assert second.value.code == "service_unavailable"
        assert calls == [True]
    finally:
        release.set()


def test_cleaned_empty_question_is_invalid_request_without_echo(shared):
    runtime = GovernedAnswerRuntime(shared.manager, Scripted(pick(shared, "Cláusula 1ª")))
    with pytest.raises(AnswerServiceError) as failure:
        run(runtime, shared, request=AnswerRequest(question="  \t\n\x00  "))
    assert failure.value.code == "invalid_request"
    assert str(failure.value) == "invalid_request"


def test_metrics_count_only_committed_states_and_real_miss_prompt_tokens(shared):
    from app.cache import GovernedResponseCache
    runtime = GovernedAnswerRuntime(shared.manager, Scripted(pick(shared, "Cláusula 1ª")),
                                   cache=GovernedResponseCache())
    first = run(runtime, shared)
    assert runtime.metrics()["answered_count"] == 0
    runtime.commit(first, lambda body: None)
    second = run(runtime, shared, request_id="req-metrics-second")
    runtime.commit(second, lambda body: None)
    stats = runtime.metrics()
    assert stats["answered_count"] == 2
    assert stats["total_input_tokens"] == first.audit.prompt_tokens
    assert stats["total_output_tokens"] is None and stats["provider_cost_usd"] is None
    assert 0 <= stats["latency_p50_ms"] <= stats["latency_p95_ms"] <= stats["latency_p99_ms"]
    assert Q_FEES not in json.dumps(stats) and "50.000" not in json.dumps(stats)


def test_elapsed_deadline_never_starts_generator(shared):
    generator = Scripted(pick(shared, "Cláusula 1ª"))
    runtime = GovernedAnswerRuntime(shared.manager, generator)
    with pytest.raises(AnswerServiceError) as failure:
        runtime.execute(AnswerRequest(question=Q_FEES, plan=plan()), shared.query_access(),
                        thread_id="t", request_id="req-expired", deadline=time.monotonic()-1)
    assert failure.value.code == "request_timeout"
    assert not generator.requests


@pytest.mark.parametrize("scope", ["source", "family"])
def test_policy_change_after_preparation_discards_json_and_sse(tmp_path, scope):
    env = build_p6_environment(tmp_path)
    runtime = GovernedAnswerRuntime(env.manager, Scripted(pick(env, "Cláusula 1ª")))
    prepared = run(runtime, env)
    subject = env.bundle.sources[0].source_id if scope == "source" else "fam:101"
    env.journal.block(scope, subject, "p7-test")
    for stream in (False, True):
        sent = []
        with pytest.raises(AnswerServiceError) as failure:
            runtime.commit(prepared, sent.append, stream=stream)
        assert failure.value.code == "service_unavailable" and sent == []


@pytest.mark.parametrize("scope", ["unit", "relation", "family"])
def test_ledger_change_without_epoch_also_discards_prepared_output(tmp_path, scope):
    from app.ingestion.review_store import unit_review_digest, review_digest
    env = build_p6_environment(tmp_path)
    runtime = GovernedAnswerRuntime(env.manager, Scripted(pick(env, "Cláusula 1ª")))
    prepared = run(runtime, env)
    unit = next(u for u in env.bundle.units if u.unit_id == env.unit("doc:101", "Cláusula 1ª"))
    if scope == "unit":
        subject, digest, source_id = unit.unit_id, unit_review_digest(unit), unit.source_ids[0]
    elif scope == "relation":
        value = env.bundle.relations[0]
        subject, digest = value.relation_id, review_digest(value.model_dump(mode="json", exclude={"review_record_id"}))
        source_id = env.bundle.relation_review_sources[subject]
    else:
        value = env.bundle.resolutions[0]
        subject, digest = value.family_id, review_digest(value.model_dump(mode="json", exclude={"review_record_id"}))
        source_id = env.bundle.family_review_sources[subject]
    source = next(s for s in env.bundle.sources if s.source_id == source_id)
    env.ledger.record(source_id=source.source_id, snapshot=source.snapshot, file_hash=source.file_hash,
                      configuration_version=source.configuration_version, scope=scope, subject_id=subject,
                      subject_digest=digest, decision="excluded", reviewer="p7-test", reason="p7-test")
    assert env.journal.snapshot().policy_epoch == 0
    sent = []
    with pytest.raises(AnswerServiceError):
        runtime.commit(prepared, sent.append)
    assert sent == []


def test_revocation_during_serialization_never_sends_prepared_proof(tmp_path, monkeypatch):
    from app.models import ChatResponse
    env = build_p6_environment(tmp_path)
    runtime = GovernedAnswerRuntime(env.manager, Scripted(pick(env, "Cláusula 1ª")))
    original = ChatResponse.model_dump_json

    def serialize(response, *args, **kwargs):
        env.journal.block("source", env.bundle.sources[0].source_id, "p7-test")
        return original(response, *args, **kwargs)

    monkeypatch.setattr(ChatResponse, "model_dump_json", serialize)
    prepared = run(runtime, env)
    sent = []
    with pytest.raises(AnswerServiceError):
        runtime.commit(prepared, sent.append)
    assert sent == []


def test_send_boundary_retains_policy_writer_until_emit_completed(tmp_path):
    import threading
    env = build_p6_environment(tmp_path)
    runtime = GovernedAnswerRuntime(env.manager, Scripted(pick(env, "Cláusula 1ª")))
    prepared = run(runtime, env)
    attempted, acknowledged = threading.Event(), threading.Event()
    failures = []

    def revoke():
        attempted.set()
        try:
            env.journal.block("credential", env.access.credential_id, "p7-test")
            acknowledged.set()
        except Exception as error:
            failures.append(type(error).__name__)

    writer = threading.Thread(target=revoke)

    def emit(body):
        assert body == prepared.body
        writer.start()
        assert attempted.wait(2)
        assert not acknowledged.wait(0.1)

    runtime.commit(prepared, emit)
    writer.join(5)
    assert not writer.is_alive() and acknowledged.is_set() and not failures
    with pytest.raises(AnswerServiceError):
        runtime.commit(prepared, lambda body: pytest.fail("second send prohibited"))


def test_failed_emit_releases_authorities_for_future_revoke(tmp_path):
    env = build_p6_environment(tmp_path)
    runtime = GovernedAnswerRuntime(env.manager, Scripted(pick(env, "Cláusula 1ª")))
    prepared = run(runtime, env)

    def disconnected(body):
        raise RuntimeError("synthetic disconnected no real secret")

    with pytest.raises(AnswerServiceError):
        runtime.commit(prepared, disconnected)
    assert env.journal.block("credential", env.access.credential_id, "p7-test").policy_epoch == 1


def test_revoked_credential_after_prepare_discards_all_bytes(tmp_path):
    env = build_p6_environment(tmp_path)
    runtime = GovernedAnswerRuntime(env.manager, Scripted(pick(env, "Cláusula 1ª")))
    prepared = run(runtime, env)
    env.journal.block("credential", env.access.credential_id, "p7-test")
    sent = []
    with pytest.raises(AnswerServiceError) as failure:
        runtime.commit(prepared, sent.append)
    assert failure.value.code == "forbidden"
    assert sent == []

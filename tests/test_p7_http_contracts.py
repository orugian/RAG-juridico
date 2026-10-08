"""P7 public errors, ASGI emission, lifecycle and bounded admission regressions."""
import asyncio
import json
import threading

import httpx
import pytest
from fastapi.testclient import TestClient
from app.answer_contracts import AnswerServiceError
from app.http_contracts import HttpConfig
from app.main import create_app
from tests.test_api import QUERY_KEY, OPERATOR_KEY, RuntimeDouble, configuration


@pytest.mark.parametrize("stage", ["execute", "commit"])
@pytest.mark.parametrize("code,status", [("invalid_request",400), ("forbidden",403), ("service_unavailable",503),
                                         ("request_timeout",504), ("internal_error",500)])
def test_errors_before_start_are_normal_http_without_exception_or_input(stage, code, status):
    class Failed(RuntimeDouble):
        def execute(self, *args, **kwargs):
            if stage == "execute":
                raise AnswerServiceError(code, request_id="untrusted-exception-id")
            return super().execute(*args, **kwargs)

        def commit(self, *args, **kwargs):
            raise AnswerServiceError(code, request_id="untrusted-exception-id")

    with TestClient(create_app(runtime=Failed(), settings=configuration())) as client:
        result = client.post("/v1/answer/stream", headers={"X-API-Key":QUERY_KEY}, json={"question":"CPF 123.456.789-01"})
        assert result.status_code == status
        assert result.headers["content-type"].startswith("application/json")
        assert set(result.json()) == {"code", "message", "request_id", "timestamp"}
        assert result.json()["request_id"] == result.headers["x-request-id"] != "untrusted-exception-id"
        assert "123.456.789-01" not in result.text
        assert "event:" not in result.text
        if status == 503:
            assert result.headers["retry-after"] == "1"


def test_payload_and_unexpected_errors_never_echo_pii():
    class Failed(RuntimeDouble):
        def execute(self, *args, **kwargs):
            raise RuntimeError("secret CPF 123.456.789-01")

    with TestClient(create_app(runtime=Failed(), settings=configuration())) as client:
        for body in ({"question": {"private": "123.456.789-01"}}, {"question": "x", "private": "123.456.789-01"}):
            result = client.post("/v1/answer", headers={"X-API-Key":QUERY_KEY}, json=body)
            assert result.status_code == 422
            assert "123.456.789-01" not in result.text and "input" not in result.text
        malformed = client.post("/v1/answer", headers={"X-API-Key":QUERY_KEY}, content='{"question":"CPF 123.456.789-01",')
        assert malformed.status_code == 422
        assert "123.456.789-01" not in malformed.text
        result = client.post("/v1/answer", headers={"X-API-Key":QUERY_KEY}, json={"question":"x"})
        assert result.status_code == 500
        assert "secret" not in result.text


def test_client_request_id_is_ignored_before_validation():
    with TestClient(create_app(runtime=RuntimeDouble(), settings=configuration())) as client:
        result = client.post("/v1/answer", headers={"X-API-Key":QUERY_KEY}, json={"question":"x", "request_id":{"private":"untrusted"}})
        assert result.status_code == 200
        assert result.json()["request_id"] == result.headers["x-request-id"]


def test_readiness_operator_policy_metrics_closed_and_aggregated():
    with TestClient(create_app(runtime=RuntimeDouble(), settings=configuration())) as client:
        assert client.get("/ready").status_code == 401
        assert client.get("/ready", headers={"X-API-Key":QUERY_KEY}).json() == {"status":"ready"}
        assert client.get("/metrics", headers={"X-API-Key":QUERY_KEY}).status_code == 403
        assert client.get("/admin/policy", headers={"X-API-Key":QUERY_KEY}).status_code == 403
        metrics = client.get("/metrics", headers={"X-API-Key":OPERATOR_KEY})
        assert metrics.status_code == 200
        assert metrics.json()["cache_hits"] == 0
        assert "question" not in metrics.text and "must-not-escape" not in metrics.text
        assert metrics.json()["total_requests"] >= 5
        assert metrics.json()["total_errors"] >= 3
        assert metrics.json()["avg_latency_ms"] >= 0
        policy = client.get("/admin/policy", headers={"X-API-Key":OPERATOR_KEY})
        assert policy.json() == {"ready":True, "policy_epoch":1}


def test_no_runtime_or_invalid_auth_never_ready_or_bypassed():
    from app.config import Settings
    for settings in (configuration(), Settings(_env_file=None, app_env="development", development_auth_bypass=True)):
        with TestClient(create_app(settings=settings)) as client:
            assert client.get("/health").json() == {"status":"ok"}
            result = client.get("/ready", headers={"X-API-Key":QUERY_KEY})
            assert result.status_code == 503
            assert result.json()["code"] == "service_unavailable"


def test_timed_out_worker_keeps_capacity_and_health_ready_are_independent():
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    class Blocking(RuntimeDouble):
        def execute(self, *args, **kwargs):
            entered.set()
            try:
                assert release.wait(3)
                return super().execute(*args, **kwargs)
            finally:
                exited.set()

    config = HttpConfig(workers=1, max_queue=0, deadline_seconds=0.08)
    with TestClient(create_app(runtime=Blocking(), settings=configuration(), http_config=config)) as client:
        try:
            first = client.post("/v1/answer", headers={"X-API-Key":QUERY_KEY}, json={"question":"x"})
            assert entered.is_set() and first.status_code == 504
            second = client.post("/v1/answer", headers={"X-API-Key":QUERY_KEY}, json={"question":"x"})
            assert second.status_code == 429
            assert second.headers["retry-after"] == "1"
            assert client.get("/health").status_code == 200
            assert client.get("/ready", headers={"X-API-Key":QUERY_KEY}).status_code == 200
        finally:
            release.set()
            assert exited.wait(3)


def test_body_limit_rejects_before_runtime_execute():
    runtime = RuntimeDouble()
    with TestClient(create_app(runtime=runtime, settings=configuration(), http_config=HttpConfig(max_body_bytes=64))) as client:
        result = client.post("/v1/answer", headers={"X-API-Key":QUERY_KEY}, json={"question":"private" * 30})
        assert result.status_code == 400
        assert result.json()["code"] == "invalid_request"
        assert runtime.calls == []


def test_principal_and_perimeter_rate_limits_do_not_trust_forwarded_for():
    config = HttpConfig(rate_requests=1, perimeter_requests=20)
    with TestClient(create_app(runtime=RuntimeDouble(), settings=configuration(), http_config=config)) as client:
        for index, key in enumerate((QUERY_KEY, QUERY_KEY, OPERATOR_KEY)):
            result = client.post("/v1/answer", headers={"X-API-Key":key, "X-Forwarded-For":str(index)}, json={"question":"x"})
            assert result.status_code == (429 if index == 1 else 200)
        assert result.status_code == 200  # distinct server principal
    config = HttpConfig(rate_requests=20, perimeter_requests=1)
    with TestClient(create_app(runtime=RuntimeDouble(), settings=configuration(), http_config=config)) as client:
        assert client.post("/v1/answer", headers={"X-API-Key":QUERY_KEY}, json={"question":"x"}).status_code == 200
        result = client.post("/v1/answer", headers={"X-API-Key":OPERATOR_KEY, "X-Forwarded-For":"new-client"}, json={"question":"x"})
        assert result.status_code == 429
        assert result.headers["retry-after"] == "1"
        assert client.get("/health").status_code == 200


def test_cors_default_closed_and_explicit_origin_only():
    origin = "https://internal.example"
    with TestClient(create_app(runtime=RuntimeDouble(), settings=configuration())) as client:
        assert "access-control-allow-origin" not in client.get("/health", headers={"Origin":origin}).headers
    config = HttpConfig(allowed_origins=(origin,))
    with TestClient(create_app(runtime=RuntimeDouble(), settings=configuration(), http_config=config)) as client:
        allowed = client.get("/health", headers={"Origin":origin})
        assert allowed.headers["access-control-allow-origin"] == origin
        blocked = client.get("/health", headers={"Origin":"https://other.example"})
        assert "access-control-allow-origin" not in blocked.headers
    with pytest.raises(ValueError):
        HttpConfig(allowed_origins=("*",))


def asgi_scope(path="/v1/answer", method="POST"):
    return {"type":"http", "asgi":{"version":"3.0"}, "http_version":"1.1", "method":method,
            "scheme":"http", "path":path, "raw_path":path.encode(), "query_string":b"",
            "root_path":"", "headers":[(b"x-api-key", QUERY_KEY.encode()), (b"content-type", b"application/json")],
            "client":("127.0.0.1",12345), "server":("localhost",80)}


def test_shutdown_unwinds_asgi_emission_and_releases_worker_guard():
    async def scenario():
        lock = threading.Lock()
        released = threading.Event()
        entered = asyncio.Event()
        block_send = asyncio.Event()
        class Guarded(RuntimeDouble):
            def commit(self, prepared, emit, *, stream=False):
                with lock:
                    try:
                        emit(prepared.body)
                    finally:
                        released.set()
        app = create_app(runtime=Guarded(), settings=configuration(),
                         http_config=HttpConfig(deadline_seconds=10, shutdown_seconds=0.05))
        messages = asyncio.Queue()
        await messages.put({"type":"http.request", "body":b'{"question":"x"}', "more_body":False})
        async def send(message):
            if message["type"] == "http.response.body":
                entered.set()
                await block_send.wait()
        lifecycle = app.router.lifespan_context(app)
        await lifecycle.__aenter__()
        query = asyncio.create_task(app(asgi_scope(), messages.get, send))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            assert not lock.acquire(blocking=False)
            await lifecycle.__aexit__(None, None, None)
            assert await asyncio.to_thread(released.wait, 0.5)
            await asyncio.wait_for(query, 1)
            assert lock.acquire(blocking=False)
            lock.release()
        finally:
            query.cancel()
            block_send.set()
            await asyncio.gather(query, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize("code,status", [("forbidden",403), ("service_unavailable",503)])
def test_epoch_auth_fails_closed_before_execute_or_cache(code, status):
    class Revoked(RuntimeDouble):
        def authenticate_epoch(self, access):
            raise AnswerServiceError(code, request_id="untrusted")
    runtime = Revoked()
    with TestClient(create_app(runtime=runtime, settings=configuration())) as client:
        for path in ("/chat", "/v1/answer", "/v1/answer/stream"):
            result = client.post(path, headers={"X-API-Key":QUERY_KEY}, json={"question":"x"})
            assert result.status_code == status
            assert runtime.calls == []


def test_current_epoch_cannot_drop_operation_permission_and_still_serve_metrics():
    class Reduced(RuntimeDouble):
        def authenticate_epoch(self, access):
            return access.model_copy(update={"permissions":["query"]})
    with TestClient(create_app(runtime=Reduced(), settings=configuration())) as client:
        assert client.get("/metrics", headers={"X-API-Key":OPERATOR_KEY}).status_code == 403


def test_auth_pool_timeout_does_not_release_live_authority_worker():
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    class Blocking(RuntimeDouble):
        def authenticate_epoch(self, access):
            entered.set()
            try:
                assert release.wait(3)
                return access
            finally:
                exited.set()
    config = HttpConfig(control_workers=1, control_queue=0, deadline_seconds=0.06)
    with TestClient(create_app(runtime=Blocking(), settings=configuration(), http_config=config)) as client:
        try:
            assert client.get("/ready", headers={"X-API-Key":QUERY_KEY}).status_code == 504
            assert entered.is_set()
            assert client.get("/ready", headers={"X-API-Key":QUERY_KEY}).status_code == 429
            assert client.get("/health").status_code == 200
        finally:
            release.set()
            assert exited.wait(3)


def test_openapi_declares_internal_api_key_and_fixed_errors():
    with TestClient(create_app(runtime=RuntimeDouble(), settings=configuration())) as client:
        schema = client.get("/openapi.json", headers={"X-API-Key":QUERY_KEY}).json()
        security = schema["components"]["securitySchemes"]["APIKeyHeader"]
        assert security == {"type":"apiKey", "in":"header", "name":"X-API-Key"}
        assert "security" not in schema["paths"]["/health"]["get"]
        for path, method in (("/chat","post"), ("/v1/answer","post"), ("/v1/answer/stream","post"),
                             ("/ready","get"), ("/metrics","get"), ("/admin/policy","get")):
            operation = schema["paths"][path][method]
            assert operation["security"] == [{"APIKeyHeader":[]}]
            for status in (400,401,403,422,429,503,504,500):
                assert operation["responses"][str(status)]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorResponse")


def test_disconnect_unwinds_real_send_before_guard_release_and_never_blocks_loop():
    async def scenario():
        main_thread = threading.get_ident()
        released = threading.Event()
        entered = asyncio.Event()
        send_unwound = threading.Event()
        messages = asyncio.Queue()
        captured = []
        class Guarded(RuntimeDouble):
            def execute(self, *args, **kwargs):
                assert threading.get_ident() != main_thread
                return super().execute(*args, **kwargs)
            def commit(self, prepared, emit, *, stream=False):
                assert threading.get_ident() != main_thread
                try:
                    emit(prepared.body)
                finally:
                    assert send_unwound.is_set()
                    released.set()
        app = create_app(runtime=Guarded(), settings=configuration())
        async def send(message):
            if message["type"] == "http.response.body":
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    send_unwound.set()
            captured.append(message)
        await messages.put({"type":"http.request", "body":b'{"question":"x"}', "more_body":False})
        async with app.router.lifespan_context(app):
            query = asyncio.create_task(app(asgi_scope(), messages.get, send))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    assert (await client.get("/health")).status_code == 200
                    assert (await client.get("/ready", headers={"X-API-Key":QUERY_KEY})).status_code == 200
                assert not released.is_set()
                await messages.put({"type":"http.disconnect"})
                await asyncio.wait_for(query, 1)
                assert await asyncio.to_thread(released.wait, 1)
                assert [message["type"] for message in captured] == ["http.response.start"]
            finally:
                query.cancel()
                await asyncio.gather(query, return_exceptions=True)
    asyncio.run(scenario())


def test_chat_planning_is_offloop(monkeypatch):
    from app.retrieval import hybrid
    original = hybrid.plan_query
    async def scenario():
        main_thread = threading.get_ident()
        calls = []
        def checked(*args, **kwargs):
            calls.append(threading.get_ident())
            assert threading.get_ident() != main_thread
            return original(*args, **kwargs)
        monkeypatch.setattr(hybrid, "plan_query", checked)
        app = create_app(runtime=RuntimeDouble(), settings=configuration())
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                result = await client.post("/chat", headers={"X-API-Key":QUERY_KEY}, json={"message":"x"})
                assert result.status_code == 200
                assert calls and calls[0] != main_thread
    asyncio.run(scenario())


def test_chat_implicit_plan_limit_is_invalid_request_not_internal_error():
    runtime = RuntimeDouble()
    question = " ".join(str(10000000000 + index) for index in range(21))
    with TestClient(create_app(runtime=runtime, settings=configuration())) as client:
        result = client.post("/chat", headers={"X-API-Key":QUERY_KEY}, json={"message":question})
        assert result.status_code == 400
        assert result.json()["code"] == "invalid_request"
        assert runtime.calls == []


def test_chunked_body_cannot_bypass_declared_size_limit():
    async def scenario():
        runtime = RuntimeDouble()
        app = create_app(runtime=runtime, settings=configuration(), http_config=HttpConfig(max_body_bytes=32))
        messages = asyncio.Queue()
        await messages.put({"type":"http.request", "body":b'{"question":"' + b'x'*16, "more_body":True})
        await messages.put({"type":"http.request", "body":b'x'*16 + b'"}', "more_body":False})
        output = []
        async def send(message):
            output.append(message)
        async with app.router.lifespan_context(app):
            await app(asgi_scope(), messages.get, send)
        assert output[0]["status"] == 400
        assert runtime.calls == []
    asyncio.run(scenario())


def test_query_queue_is_finite_and_work_runs_serially_at_one_worker():
    async def scenario():
        entered, release, authenticated_second = threading.Event(), threading.Event(), threading.Event()
        class Blocking(RuntimeDouble):
            def authenticate_epoch(self, access):
                result = super().authenticate_epoch(access)
                if len(self.authenticated) == 2:
                    authenticated_second.set()
                return result
            def execute(self, *args, **kwargs):
                if not self.calls:
                    entered.set()
                    assert release.wait(3)
                return super().execute(*args, **kwargs)
        runtime = Blocking()
        app = create_app(runtime=runtime, settings=configuration(), http_config=HttpConfig(workers=1, max_queue=1, deadline_seconds=2))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                async def post():
                    return await client.post("/v1/answer", headers={"X-API-Key":QUERY_KEY}, json={"question":"x"})
                first = asyncio.create_task(post())
                assert await asyncio.to_thread(entered.wait, 1)
                second = asyncio.create_task(post())
                try:
                    assert await asyncio.to_thread(authenticated_second.wait, 1)
                    # Public aggregate gauge proves admission, not merely completion
                    # of authentication in another thread (which races route parsing).
                    for _ in range(10):
                        gauges = (await client.get("/metrics", headers={"X-API-Key":OPERATOR_KEY})).json()
                        if gauges["query_pending"] == 2:
                            break
                    assert gauges["query_pending"] == 2
                    assert gauges["query_active"] == 1 and gauges["query_queued"] == 1
                    third = await post()
                    assert third.status_code == 429
                    assert not second.done()
                finally:
                    release.set()
                    results = await asyncio.gather(first, second, return_exceptions=True)
                assert [result.status_code for result in results] == [200,200]
                assert len(runtime.calls) == 2
    asyncio.run(scenario())


@pytest.mark.parametrize("path", ["/v1/answer", "/v1/answer/stream"])
@pytest.mark.parametrize("change", ["expiry", "removal", "permissions"])
def test_key_rotation_is_revalidated_under_commit_before_any_answer_bytes(path, change):
    from pydantic import SecretStr
    previous = "p" * 40
    settings = configuration(previous_api_secret_key=previous, previous_key_valid_until="9999-01-01T00:00:00+00:00")
    class Rotated(RuntimeDouble):
        def execute(self, *args, **kwargs):
            prepared = super().execute(*args, **kwargs)
            if change == "expiry":
                settings.previous_key_valid_until = "2000-01-01T00:00:00+00:00"
            else:
                settings.previous_api_secret_key = SecretStr("")
                if change == "permissions":
                    settings.operator_api_secret_key = SecretStr(previous)
            return prepared
    runtime = Rotated()
    with TestClient(create_app(runtime=runtime, settings=settings)) as client:
        result = client.post(path, headers={"X-API-Key":previous}, json={"question":"x"})
        assert len(runtime.authenticated) == 1  # key was valid at admission
        assert len(runtime.calls) == 1
        assert result.status_code == (403 if change == "permissions" else 401)
        assert result.headers["content-type"].startswith("application/json")
        assert result.json()["code"] == ("forbidden" if change == "permissions" else "unauthorized")
        assert set(result.json()) == {"code", "message", "request_id", "timestamp"}
        assert "event:" not in result.text and "corpus_generation_id" not in result.text


@pytest.mark.parametrize("path", ["/v1/answer", "/v1/answer/stream"])
def test_previous_key_expiring_between_worker_gate_and_asgi_start_still_fails_closed(monkeypatch, path):
    from datetime import datetime, timezone
    from app import http_contracts
    class Clock(datetime):
        calls = 0
        @classmethod
        def now(cls, tz=None):
            cls.calls += 1
            # Admission and worker emit gate are valid; actual ASGI start is not.
            return datetime(2026 if cls.calls <= 2 else 2028, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(http_contracts, "datetime", Clock)
    previous = "p" * 40
    settings = configuration(previous_api_secret_key=previous, previous_key_valid_until="2027-01-01T00:00:00+00:00")
    with TestClient(create_app(runtime=RuntimeDouble(), settings=settings)) as client:
        result = client.post(path, headers={"X-API-Key":previous}, json={"question":"x"})
        assert result.status_code == 401
        assert Clock.calls == 3
        assert result.headers["content-type"].startswith("application/json")
        assert result.json()["code"] == "unauthorized"
        assert "corpus_generation_id" not in result.text and "event:" not in result.text


@pytest.mark.parametrize("path,method", [("/ready","readiness"), ("/metrics","metrics"), ("/admin/policy","policy_status")])
def test_c2_control_getter_removing_operator_key_sends_no_administrative_bytes(path, method):
    from pydantic import SecretStr
    settings = configuration()
    runtime = RuntimeDouble()
    original = getattr(runtime, method)
    def removed(*args, **kwargs):
        raw = original(*args, **kwargs)
        settings.operator_api_secret_key = SecretStr("")
        return raw
    setattr(runtime, method, removed)
    with TestClient(create_app(runtime=runtime, settings=settings)) as client:
        result = client.get(path, headers={"X-API-Key":OPERATOR_KEY})
        assert result.status_code == 401
        assert result.json()["code"] == "unauthorized"
        assert set(result.json()) == {"code","message","request_id","timestamp"}
        assert result.headers["x-request-id"] == result.json()["request_id"]


def test_c2_control_receive_deadline_is_error_not_disconnect_or_internal_error():
    async def scenario():
        from starlette.exceptions import HTTPException
        entered, release, exited = threading.Event(), threading.Event(), threading.Event()
        class Blocking(RuntimeDouble):
            def readiness(self):
                entered.set()
                try:
                    assert release.wait(5)
                    return True
                finally:
                    exited.set()
        app = create_app(runtime=Blocking(), settings=configuration(), http_config=HttpConfig(deadline_seconds=5))
        output = []
        async def receive():
            assert await asyncio.to_thread(entered.wait, 2)
            raise HTTPException(504)
        async def send(message):
            output.append(message)
        async with app.router.lifespan_context(app):
            try:
                await asyncio.wait_for(app(asgi_scope("/ready", "GET"), receive, send), 3)
                assert output[0]["status"] == 504
                assert json.loads(output[1]["body"])["code"] == "request_timeout"
            finally:
                release.set()
                assert await asyncio.to_thread(exited.wait, 2)
    asyncio.run(scenario())


@pytest.mark.parametrize("path,operation,method", [("/ready","ready","readiness"), ("/metrics","metrics","metrics"), ("/admin/policy","policy","policy_status")])
@pytest.mark.parametrize("stage", ["getter", "serialization", "prepared"])
@pytest.mark.parametrize("change,status", [("removal",401), ("expiry",401), ("permissions",403)])
def test_c2_control_rotation_at_every_precommit_stage_discards_output(path, operation, method, stage, change, status):
    from pydantic import SecretStr
    settings = configuration()
    def rotate():
        settings.operator_api_secret_key = SecretStr("")
        if change != "removal":
            settings.previous_api_secret_key = SecretStr(OPERATOR_KEY)
            settings.previous_key_valid_until = "2000-01-01T00:00:00+00:00" if change == "expiry" else "9999-01-01T00:00:00+00:00"
    class Rotated(RuntimeDouble):
        def prepare_control(self, operation, access, *, request_id, deadline, encode):
            def encoded(raw):
                result = encode(raw)
                if stage == "serialization":
                    rotate()
                return result
            prepared = super().prepare_control(operation, access, request_id=request_id, deadline=deadline, encode=encoded)
            if stage == "prepared":
                rotate()
            return prepared
    runtime = Rotated()
    original = getattr(runtime, method)
    def getter(*args, **kwargs):
        raw = original(*args, **kwargs)
        if stage == "getter":
            rotate()
        return raw
    setattr(runtime, method, getter)
    with TestClient(create_app(runtime=runtime, settings=settings)) as client:
        result = client.get(path, headers={"X-API-Key":OPERATOR_KEY})
        assert runtime.control_preparations == [operation]
        assert runtime.control_commits == []
        assert result.status_code == status
        assert set(result.json()) == {"code","message","request_id","timestamp"}
        assert result.json()["code"] == ("unauthorized" if status == 401 else "forbidden")


@pytest.mark.parametrize("path,operation,method", [("/ready","ready","readiness"), ("/metrics","metrics","metrics"), ("/admin/policy","policy","policy_status")])
@pytest.mark.parametrize("revoked,status", [(True,403), (False,503)])
def test_c2_control_calls_final_policy_commit_not_legacy_getter_only(path, operation, method, revoked, status):
    runtime = RuntimeDouble()
    original = getattr(runtime, method)
    def getter(*args, **kwargs):
        raw = original(*args, **kwargs)
        runtime.policy_epoch += 1
        if revoked:
            runtime.blocked_credentials.add(runtime.authenticated[0].credential_id)
        return raw
    setattr(runtime, method, getter)
    with TestClient(create_app(runtime=runtime, settings=configuration())) as client:
        result = client.get(path, headers={"X-API-Key":OPERATOR_KEY})
        assert runtime.control_preparations == [operation]
        assert runtime.control_commits == []
        assert result.status_code == status
        assert set(result.json()) == {"code","message","request_id","timestamp"}
        assert "policy_epoch" not in result.text and "cache_hits" not in result.text


@pytest.mark.parametrize("path", ["/ready", "/metrics", "/admin/policy"])
@pytest.mark.parametrize("change,status", [("removal",401), ("permissions",403)])
def test_c2_control_rotation_between_worker_gate_and_asgi_start(monkeypatch, path, change, status):
    from pydantic import SecretStr
    from app import main
    settings = configuration()
    original_asyncio = main.asyncio
    scheduled = []
    def rotate():
        settings.operator_api_secret_key = SecretStr("")
        if change == "permissions":
            settings.previous_api_secret_key = SecretStr(OPERATOR_KEY)
            settings.previous_key_valid_until = "9999-01-01T00:00:00+00:00"
    class SchedulerBoundary:
        def __getattr__(self, name):
            return getattr(original_asyncio, name)
        def run_coroutine_threadsafe(self, coroutine, loop):
            scheduled.append(True)
            # Worker has reauthenticated; mutate before this emission coroutine
            # is scheduled. This replaces only main's scheduling boundary, not
            # the global asyncio module used by TestClient/other app components.
            loop.call_soon_threadsafe(rotate)
            return original_asyncio.run_coroutine_threadsafe(coroutine, loop)
    monkeypatch.setattr(main, "asyncio", SchedulerBoundary())
    with TestClient(create_app(runtime=RuntimeDouble(), settings=settings)) as client:
        result = client.get(path, headers={"X-API-Key":OPERATOR_KEY})
        assert scheduled == [True]
        assert result.status_code == status
        assert set(result.json()) == {"code","message","request_id","timestamp"}


def test_c2_control_getter_timeout_keeps_actual_pool_capacity_until_it_returns():
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    class Blocking(RuntimeDouble):
        def readiness(self):
            entered.set()
            try:
                assert release.wait(5)
                return True
            finally:
                exited.set()
    config = HttpConfig(control_workers=1, control_queue=0, deadline_seconds=1)
    with TestClient(create_app(runtime=Blocking(), settings=configuration(), http_config=config)) as client:
        try:
            first = client.get("/ready", headers={"X-API-Key":QUERY_KEY})
            assert entered.is_set() and first.status_code == 504
            assert not exited.is_set()
            assert client.get("/metrics", headers={"X-API-Key":OPERATOR_KEY}).status_code == 429
            assert client.get("/health").json() == {"status":"ok"}
        finally:
            release.set()
            assert exited.wait(3)


@pytest.mark.parametrize("phase", ["http.response.start", "http.response.body"])
@pytest.mark.parametrize("finish", ["complete", "disconnect", "cancel", "deadline", "shutdown"])
def test_c2_control_lease_covers_send_and_worker_queue_until_unwind(phase, finish):
    async def scenario():
        main_thread = threading.get_ident()
        lease = threading.Lock()
        writer_started, writer_ack, guard_released = threading.Event(), threading.Event(), threading.Event()
        entered, release_send = asyncio.Event(), asyncio.Event()
        send_unwound = threading.Event()
        captured = []
        class Guarded(RuntimeDouble):
            def prepare_control(self, *args, **kwargs):
                self.worker = threading.get_ident()
                assert self.worker != main_thread
                original = kwargs["encode"]
                def encoded(raw):
                    assert threading.get_ident() == self.worker
                    return original(raw)
                kwargs["encode"] = encoded
                prepared = super().prepare_control(*args, **kwargs)
                self.prepared = prepared
                return prepared
            def commit_control(self, prepared, emit):
                assert threading.get_ident() == self.worker
                with lease:
                    try:
                        super().commit_control(prepared, emit)
                    finally:
                        assert send_unwound.is_set()
                        guard_released.set()
        runtime = Guarded()
        config = HttpConfig(control_workers=1, control_queue=0, deadline_seconds=1 if finish == "deadline" else 5,
                            shutdown_seconds=0.2)
        app = create_app(runtime=runtime, settings=configuration(), http_config=config)
        incoming = asyncio.Queue()
        await incoming.put({"type":"http.request", "body":b"", "more_body":False})
        scope = asgi_scope("/metrics", "GET")
        scope["headers"][0] = (b"x-api-key", OPERATOR_KEY.encode())
        async def send(message):
            if message["type"] == phase:
                entered.set()
                try:
                    await release_send.wait()
                finally:
                    send_unwound.set()
            captured.append(message)
        def writer():
            writer_started.set()
            with lease:
                writer_ack.set()
        lifecycle = app.router.lifespan_context(app)
        await lifecycle.__aenter__()
        stopped_lifecycle = False
        output = asyncio.create_task(app(scope, incoming.get, send))
        writer_task = None
        try:
            await asyncio.wait_for(entered.wait(), 2)
            writer_task = asyncio.create_task(asyncio.to_thread(writer))
            assert await asyncio.to_thread(writer_started.wait, 1)
            assert not writer_ack.is_set() and not guard_released.is_set()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                assert (await client.get("/health")).json() == {"status":"ok"}
                assert (await client.get("/ready", headers={"X-API-Key":QUERY_KEY})).status_code == 429
            if finish == "complete":
                release_send.set()
            elif finish == "disconnect":
                await incoming.put({"type":"http.disconnect"})
            elif finish == "cancel":
                output.cancel()
            elif finish == "shutdown":
                await lifecycle.__aexit__(None, None, None)
                stopped_lifecycle = True
            result = await asyncio.wait_for(asyncio.gather(output, return_exceptions=True), 2)
            if finish == "cancel":
                assert isinstance(result[0], asyncio.CancelledError)
            assert await asyncio.to_thread(guard_released.wait, 1)
            await asyncio.wait_for(writer_task, 1)
            assert writer_ack.is_set()
            if finish == "complete":
                assert [message["type"] for message in captured] == ["http.response.start", "http.response.body"]
                assert captured[1]["body"] == runtime.prepared.body
                assert "question" not in captured[1]["body"].decode()
            else:
                assert [message["type"] for message in captured] == ([] if phase == "http.response.start" else ["http.response.start"])
                assert runtime.control_commits == []
        finally:
            output.cancel()
            release_send.set()
            await asyncio.gather(output, return_exceptions=True)
            if writer_task is not None:
                await asyncio.gather(writer_task, return_exceptions=True)
            if not stopped_lifecycle:
                await lifecycle.__aexit__(None, None, None)
    asyncio.run(scenario())


def test_c2_control_cancelled_http_waiter_retains_slot_until_slow_send_cleanup_finishes():
    async def scenario():
        lease = threading.Lock()
        entered, cleanup_entered, cleanup_release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        guard_released, send_unwound = threading.Event(), threading.Event()
        class Guarded(RuntimeDouble):
            def commit_control(self, prepared, emit):
                with lease:
                    try:
                        super().commit_control(prepared, emit)
                    finally:
                        assert send_unwound.is_set()
                        guard_released.set()
        app = create_app(runtime=Guarded(), settings=configuration(),
                         http_config=HttpConfig(control_workers=1, control_queue=0, deadline_seconds=5))
        incoming = asyncio.Queue()
        await incoming.put({"type":"http.request", "body":b"", "more_body":False})
        async def send(message):
            if message["type"] == "http.response.body":
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cleanup_entered.set()
                    await cleanup_release.wait()  # ASGI finally has not actually completed yet.
                    raise
                finally:
                    send_unwound.set()
        async with app.router.lifespan_context(app):
            output = asyncio.create_task(app(asgi_scope("/ready", "GET"), incoming.get, send))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                output.cancel()
                result = await asyncio.gather(output, return_exceptions=True)
                assert isinstance(result[0], asyncio.CancelledError)
                await asyncio.wait_for(cleanup_entered.wait(), 2)
                assert not guard_released.is_set() and not lease.acquire(blocking=False)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    assert (await client.get("/ready", headers={"X-API-Key":QUERY_KEY})).status_code == 429
                    assert (await client.get("/health")).status_code == 200
                cleanup_release.set()
                assert await asyncio.to_thread(guard_released.wait, 2)
            finally:
                cleanup_release.set()
                output.cancel()
                await asyncio.gather(output, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize("path", ["/ready", "/metrics", "/admin/policy"])
def test_c2_missing_control_ports_never_fall_back_to_legacy_getters(path):
    class Legacy:
        called = False
        def authenticate_epoch(self, access):
            return access
        def readiness(self):
            self.called = True
            return True
        def metrics(self):
            self.called = True
            return {"cache_hits":42}
        def policy_status(self, access):
            self.called = True
            return {"policy_epoch":42}
    runtime = Legacy()
    with TestClient(create_app(runtime=runtime, settings=configuration())) as client:
        result = client.get(path, headers={"X-API-Key":OPERATOR_KEY})
        assert result.status_code == 500
        assert result.json()["code"] == "internal_error"
        assert not runtime.called


def test_c2_negative_ready_is_prepared_503_sent_only_under_authorized_commit():
    class NotReady(RuntimeDouble):
        def readiness(self):
            return False
    runtime = NotReady()
    with TestClient(create_app(runtime=runtime, settings=configuration())) as client:
        result = client.get("/ready", headers={"X-API-Key":QUERY_KEY})
        assert result.status_code == 503
        assert result.headers["retry-after"] == "1"
        assert result.json()["code"] == "service_unavailable"
        assert runtime.control_preparations == ["ready"]
        assert runtime.control_commit_finished.wait(2)
        assert len(runtime.control_commits) == 1
        prepared = runtime.control_commits[0]
        assert prepared.status_code == 503 and prepared.body == result.content
        assert result.json()["request_id"] == result.headers["x-request-id"] == prepared.request_id


@pytest.mark.parametrize("path", ["/ready", "/metrics", "/admin/policy"])
@pytest.mark.parametrize("stage", ["prepare", "commit"])
@pytest.mark.parametrize("code,status", [("invalid_request",400), ("forbidden",403), ("service_unavailable",503),
                                          ("request_timeout",504), ("internal_error",500)])
def test_c2_control_failures_before_start_are_fixed_normal_http(path, stage, code, status):
    class Failed(RuntimeDouble):
        def prepare_control(self, *args, **kwargs):
            if stage == "prepare":
                raise AnswerServiceError(code, request_id="untrusted-error-id")
            return super().prepare_control(*args, **kwargs)
        def commit_control(self, prepared, emit):
            raise AnswerServiceError(code, request_id="untrusted-error-id")
    with TestClient(create_app(runtime=Failed(), settings=configuration())) as client:
        result = client.get(path, headers={"X-API-Key":OPERATOR_KEY})
        assert result.status_code == status
        assert set(result.json()) == {"code","message","request_id","timestamp"}
        assert result.json()["request_id"] == result.headers["x-request-id"] != "untrusted-error-id"
        assert result.headers["content-type"].startswith("application/json")


def test_c2_control_projection_serializes_once_before_commit_and_preserves_metric_unknowns():
    class Projected(RuntimeDouble):
        def metrics(self):
            self.raw = {"cache_hits":3, "answered_count":2, "abstained_count":1, "needs_clarification_count":0,
                        "total_input_tokens":50, "total_output_tokens":None, "provider_cost_usd":None,
                        "latency_p50_ms":1, "latency_p95_ms":2, "latency_p99_ms":3,
                        "cache_misses":float("nan"), "cache_bytes":-1,
                        "question":"private-getter-data", "credential_id":"private-id", "sources":["private"]}
            return self.raw
        def commit_control(self, prepared, emit):
            self.raw["cache_hits"] = 999  # Changing raw data cannot alter serialized prepared bytes.
            super().commit_control(prepared, emit)
    runtime = Projected()
    with TestClient(create_app(runtime=runtime, settings=configuration())) as client:
        result = client.get("/metrics", headers={"X-API-Key":OPERATOR_KEY})
        assert result.status_code == 200
        assert runtime.control_commit_finished.wait(2)  # ASGI acceptance can precede worker return.
        assert result.content == runtime.control_commits[0].body
        fields = result.json()
        assert fields["cache_hits"] == 3 and fields["total_input_tokens"] == 50
        assert fields["answered_count"] == 2 and fields["abstained_count"] == 1
        assert fields["needs_clarification_count"] == 0
        assert fields["total_output_tokens"] is None and fields["provider_cost_usd"] is None
        assert [fields[name] for name in ("latency_p50_ms", "latency_p95_ms", "latency_p99_ms")] == [1,2,3]
        assert "cache_misses" not in fields and "cache_bytes" not in fields
        assert "private" not in result.text and "question" not in result.text


def test_c2_receive_failure_stops_worker_before_sending_fallback_error():
    async def scenario():
        entered, release, commit_started = threading.Event(), threading.Event(), threading.Event()
        observed = []
        class Blocking(RuntimeDouble):
            def readiness(self):
                entered.set()
                assert release.wait(5)
                return True
            def commit_control(self, prepared, emit):
                commit_started.set()
                super().commit_control(prepared, emit)
        app = create_app(runtime=Blocking(), settings=configuration(), http_config=HttpConfig(deadline_seconds=5))
        output = []
        async def receive():
            assert await asyncio.to_thread(entered.wait, 2)
            raise RuntimeError("receive_failed")
        async def send(message):
            output.append(message)
            if message["type"] == "http.response.start" and message["status"] == 500:
                # Sending a fallback error may yield the loop while original
                # synchronous preparation finishes. It must already be stopped.
                release.set()
                observed.append(await asyncio.to_thread(commit_started.wait, 1))
        async with app.router.lifespan_context(app):
            try:
                await asyncio.wait_for(app(asgi_scope("/ready", "GET"), receive, send), 3)
                assert observed == [False]
                assert [message["type"] for message in output] == ["http.response.start", "http.response.body"]
                assert output[0]["status"] == 500
            finally:
                release.set()
    asyncio.run(scenario())


def test_c2_shutdown_then_http_cancel_never_detaches_still_unwinding_send():
    async def scenario():
        entered, cleanup_entered, cleanup_release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        send_unwound, worker_done = threading.Event(), threading.Event()
        class Guarded(RuntimeDouble):
            def commit_control(self, prepared, emit):
                try:
                    super().commit_control(prepared, emit)
                finally:
                    worker_done.set()
        app = create_app(runtime=Guarded(), settings=configuration(),
                         http_config=HttpConfig(control_workers=1, control_queue=0, deadline_seconds=5, shutdown_seconds=0.05))
        incoming = asyncio.Queue()
        await incoming.put({"type":"http.request", "body":b"", "more_body":False})
        async def send(message):
            if message["type"] == "http.response.body":
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cleanup_entered.set()
                    await cleanup_release.wait()
                    raise
                finally:
                    send_unwound.set()
        lifecycle = app.router.lifespan_context(app)
        await lifecycle.__aenter__()
        output = asyncio.create_task(app(asgi_scope("/ready", "GET"), incoming.get, send))
        closed = False
        try:
            await asyncio.wait_for(entered.wait(), 2)
            await lifecycle.__aexit__(None, None, None)
            closed = True
            await asyncio.wait_for(cleanup_entered.wait(), 2)
            output.cancel()  # Real server cancellation following bounded shutdown.
            await asyncio.gather(output, return_exceptions=True)
            assert not await asyncio.to_thread(worker_done.wait, 0.2)
            assert not send_unwound.is_set()
            cleanup_release.set()
            assert await asyncio.to_thread(worker_done.wait, 2)
            assert send_unwound.is_set()
        finally:
            cleanup_release.set()
            output.cancel()
            await asyncio.gather(output, return_exceptions=True)
            if not closed:
                await lifecycle.__aexit__(None, None, None)
    asyncio.run(scenario())


def test_body_read_deadline_is_504_not_parser_error():
    async def scenario():
        app = create_app(runtime=RuntimeDouble(), settings=configuration(), http_config=HttpConfig(deadline_seconds=0.04))
        never = asyncio.Event()
        messages = []
        async def receive():
            await never.wait()
        async def send(message):
            messages.append(message)
        async with app.router.lifespan_context(app):
            await asyncio.wait_for(app(asgi_scope(), receive, send), 1)
        assert messages[0]["status"] == 504
        assert json.loads(messages[1]["body"])["code"] == "request_timeout"
    asyncio.run(scenario())

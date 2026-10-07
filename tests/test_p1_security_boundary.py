import asyncio
import copy
import json
import logging
from uuid import uuid4

import pytest
from fastapi import HTTPException
from langchain_core.outputs import Generation, LLMResult

from app.config import Settings


@pytest.mark.parametrize("key", [None, "arbitrary"])
def test_missing_auth_configuration_never_authenticates(monkeypatch, key):
    import app.security as security
    monkeypatch.setattr(security, "settings", Settings(_env_file=None, app_env="production"))
    with pytest.raises(HTTPException) as error:
        asyncio.run(security.verify_api_key(key))
    assert error.value.status_code == 503


def test_shared_key_has_technical_principal_and_operator_is_separate(monkeypatch):
    import app.security as security
    key = "q" * 32
    operator = "o" * 32
    monkeypatch.setattr(security, "settings", Settings(_env_file=None, api_secret_key=key, operator_api_secret_key=operator))
    access = asyncio.run(security.verify_api_key(key))
    assert access.permissions == ["query"]
    assert key not in access.model_dump_json()
    assert access.principal_id.startswith("credential-")
    with pytest.raises(HTTPException) as error:
        asyncio.run(security.verify_operator_key(key))
    assert error.value.status_code == 403
    assert "operate" in asyncio.run(security.verify_operator_key(operator)).permissions
    with pytest.raises(HTTPException) as error:
        asyncio.run(security.verify_api_key("invalid"))
    assert error.value.status_code == 401


def test_explicit_development_bypass_is_never_a_production_profile(monkeypatch):
    import app.security as security
    monkeypatch.setattr(security, "settings", Settings(_env_file=None, app_env="development", development_auth_bypass=True))
    assert asyncio.run(security.verify_api_key(None)).principal_id == "synthetic-development"
    with pytest.raises(ValueError):
        Settings(_env_file=None, app_env="production", development_auth_bypass=True)


def test_production_auth_startup_validation_rejects_weak_or_duplicate_keys():
    from app.security import validate_auth_configuration
    for changes in ({}, {"api_secret_key": "short"}, {"api_secret_key": "a" * 32, "operator_api_secret_key": "a" * 32}):
        with pytest.raises(ValueError):
            validate_auth_configuration(Settings(_env_file=None, app_env="production", **changes))


def test_rotation_rejects_expired_previous_key_and_accepts_current(monkeypatch):
    import app.security as security
    from datetime import datetime, timezone, timedelta
    current, previous = "q" * 32, "p" * 32
    settings = Settings(_env_file=None, api_secret_key=current, previous_api_secret_key=previous, previous_key_valid_until=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
    monkeypatch.setattr(security, "settings", settings)
    assert asyncio.run(security.verify_api_key(previous)).credential_id != asyncio.run(security.verify_api_key(current)).credential_id
    expired = settings.model_copy(update={"previous_key_valid_until": "2020-01-01T00:00:00+00:00"})
    monkeypatch.setattr(security, "settings", expired)
    with pytest.raises(HTTPException) as error:
        asyncio.run(security.verify_api_key(previous))
    assert error.value.status_code == 401
    assert "query" in asyncio.run(security.verify_api_key(current)).permissions


def test_auth_uses_constant_time_comparison(monkeypatch):
    import app.security as security
    calls = []
    import secrets
    original = secrets.compare_digest
    def capture(a, b):
        calls.append((a, b))
        return original(a, b)
    monkeypatch.setattr(secrets, "compare_digest", capture)
    monkeypatch.setattr(security, "settings", Settings(_env_file=None, api_secret_key="q" * 32))
    asyncio.run(security.verify_api_key("q" * 32))
    assert calls


def test_callback_does_not_mutate_any_original_and_drops_unknown_fields():
    from app.monitoring import SafeLangSmithCallbackHandler
    handler = SafeLangSmithCallbackHandler()
    run_id = uuid4()
    inputs = {"query": "Nome Sigiloso CPF 123.456.789-00", "nested": {"text_raw": "Cláusula confidencial"}}
    outputs = {"answer": "Nome Sigiloso", "status_code": 200}
    originals = copy.deepcopy((inputs, outputs))
    handler.on_chain_start({"name": "Nome Sigiloso", "secret": "Nome Sigiloso"}, inputs, run_id=run_id, tags=["Nome Sigiloso"], metadata={"name": "Nome Sigiloso", "tokens": "Nome Sigiloso"})
    handler.on_chain_end(outputs, run_id=run_id)
    assert (inputs, outputs) == originals
    assert "Nome Sigiloso" not in json.dumps(handler.runs)


def test_callback_preserves_generations_and_error_is_category_only():
    from app.monitoring import SafeLangSmithCallbackHandler
    handler = SafeLangSmithCallbackHandler()
    run_id = uuid4()
    prompts = ["Nome Sigiloso"]
    handler.on_llm_start({"name": "process"}, prompts, run_id=run_id)
    result = LLMResult(generations=[[Generation(text="Nome Sigiloso")]], llm_output={"token_usage": {"total_tokens": 3, "secret": "Nome Sigiloso"}})
    original = result.model_dump()
    handler.on_llm_end(result, run_id=run_id)
    assert prompts == ["Nome Sigiloso"]
    assert result.model_dump() == original
    handler.on_chain_error(ValueError("Nome Sigiloso"), run_id=run_id)
    assert "Nome Sigiloso" not in json.dumps(handler.runs)
    assert handler.get_run(run_id)["error"] == "validation_error"


def test_log_formatter_drops_arbitrary_text_pii_and_exceptions():
    from app.monitoring import JSONFormatter
    record = logging.LogRecord("test", logging.ERROR, "file", 1, "Nome Sigiloso AB.CDE.123/XY45-67", (), None)
    record.contract_payload = "Nome Sigiloso"
    record.request_id = str(uuid4())
    text = JSONFormatter().format(record)
    assert "Nome Sigiloso" not in text
    assert "AB.CDE.123/XY45-67" not in text
    assert record.request_id in text


def test_protected_exporter_http_body_contains_no_source_data_or_attachments():
    import requests
    from app.telemetry import create_protected_client
    captured = []

    class Capture(requests.adapters.BaseAdapter):
        def send(self, request, **kwargs):
            captured.append(request)
            response = requests.Response()
            response.status_code = 200
            response._content = b"{}"
            response.request = request
            return response

        def close(self):
            pass

    session = requests.Session()
    session.mount("http://", Capture())
    client = create_protected_client(settings=Settings(_env_file=None, langsmith_api_key="synthetic"), session=session, api_url="http://127.0.0.1:9999")
    # The installed SDK replaces adapters at construction; inject at its final boundary.
    session.mount("http://", Capture())
    parent, child = uuid4(), uuid4()
    secret = "Nome Sigiloso AB.CDE.123/XY45-67 texto contratual"
    for run_id, parent_id in ((parent, None), (child, parent)):
        client.create_run(secret, {"query": secret}, "chain", id=run_id, parent_run_id=parent_id, extra={"metadata": {"secret": secret}}, tags=[secret], serialized={"secret": secret}, attachments={"secret": ("text/plain", secret.encode())}, error=secret)
        client.update_run(run_id, outputs={"answer": secret}, error=secret, extra={"metadata": {"secret": secret}}, tags=[secret])
    assert len(captured) == 4
    bodies = [(request.body.decode() if isinstance(request.body, bytes) else request.body) for request in captured]
    assert all(secret not in body and "Nome Sigiloso" not in body and "attachments" not in body for body in bodies)
    assert str(child) in bodies[2] and str(parent) in bodies[2]
    assert not hasattr(client, "batch_ingest_runs")


def test_tracing_does_not_change_pipeline_result(monkeypatch):
    import app.telemetry as telemetry
    import app.security as security
    query = "Qual a multa da Empresa Sintética CPF 123.456.789-00?"
    monkeypatch.setattr(telemetry, "get_settings", lambda: Settings(_env_file=None, langsmith_tracing_v2=False))
    expected = security.security_pipeline.run(query)
    monkeypatch.setattr(telemetry, "get_settings", lambda: Settings(_env_file=None, langsmith_tracing_v2=True))
    monkeypatch.setattr(telemetry, "create_protected_client", lambda **kw: None)
    assert security.security_pipeline.run(query) == expected


def test_enabled_tracing_exports_parent_child_projection_and_preserves_query(monkeypatch):
    import app.telemetry as telemetry
    import app.security as security
    writes = []
    class Capture:
        def create_run(self, name, inputs, run_type, **kwargs):
            writes.append((name, inputs, kwargs))
        def update_run(self, run_id, **kwargs):
            writes.append((str(run_id), kwargs))
        def close(self):
            pass
    monkeypatch.setattr(telemetry, "get_settings", lambda: Settings(_env_file=None, langsmith_tracing_v2=True, langsmith_api_key="synthetic"))
    monkeypatch.setattr(telemetry, "create_protected_client", lambda **kw: Capture())
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    query = "Nome Sigiloso CNPJ AB.CDE.123/XY45-67"
    assert security.security_pipeline.process(query) == query
    assert len(writes) == 4
    assert writes[1][2]["parent_run_id"] == writes[0][2]["id"]
    assert query not in str(writes)


def test_exporter_failure_does_not_change_pipeline_result(monkeypatch):
    import app.telemetry as telemetry
    import app.security as security
    class Broken:
        def create_run(self, *args, **kwargs):
            raise RuntimeError("Unavailable")
        def update_run(self, *args, **kwargs):
            raise RuntimeError("Unavailable")
        def close(self):
            raise RuntimeError("Unavailable")
    monkeypatch.setattr(telemetry, "get_settings", lambda: Settings(_env_file=None, langsmith_tracing_v2=True, langsmith_api_key="synthetic"))
    monkeypatch.setattr(telemetry, "create_protected_client", lambda **kw: Broken())
    assert security.security_pipeline.process("Texto sintético") == "Texto sintético"


def test_callback_is_bounded_and_returns_detached_snapshots():
    from app.monitoring import SafeLangSmithCallbackHandler
    handler = SafeLangSmithCallbackHandler(max_runs=2)
    ids = [uuid4() for _ in range(3)]
    for run_id in ids:
        handler.on_chain_start({}, {}, run_id=run_id)
    assert len(handler.runs) == 2
    assert handler.get_run(ids[0]) is None
    copied = handler.get_run(ids[-1])
    copied["metadata"]["secret"] = "sensitive"
    assert "secret" not in handler.get_run(ids[-1])["metadata"]


def test_alphanumeric_cnpj_redaction_does_not_change_source():
    from app.monitoring import sanitize_telemetry_payload
    original = {"identifier": "AB.CDE.123/XY45-67 abcde123xy4567"}
    projected = sanitize_telemetry_payload(original)
    assert "AB.CDE.123/XY45-67" not in projected["identifier"]
    assert "abcde123xy4567" not in projected["identifier"]
    assert original["identifier"] == "AB.CDE.123/XY45-67 abcde123xy4567"

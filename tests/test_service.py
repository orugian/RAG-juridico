"""Operator service composition over a physically governed synthetic generation."""
import json

import pytest
from fastapi.testclient import TestClient

from app.generation import AnswerRequest
from app.main import create_app
from app.service import ZDR_PROVIDER_PREFERENCES, ServiceSettings, build_runtime, openrouter_zdr_client
from tests.p6_corpus import build_p6_environment
from tests.test_api import QUERY_KEY, configuration
from tests.test_p6_pipeline import Q_FEES, pick, plan

OPENROUTER = "https://openrouter.ai/api/v1"


class FakeProviderClient:
    """Stands in for the remote chat model; records what would leave the host."""

    def __init__(self, content):
        self.content, self.calls = content, []

    def invoke(self, messages, timeout=None):
        self.calls.append(messages)
        return type("Message", (), {"content": self.content})()


@pytest.fixture(scope="module")
def environment(tmp_path_factory):
    root = tmp_path_factory.mktemp("service")
    env = build_p6_environment(root)
    config_path = root / "generation_config.json"
    config_path.write_text(env.manager.configuration.model_dump_json(), encoding="utf-8")
    service = ServiceSettings(_env_file=None, index_root=root / "indexes", authority_dir=root / "authority",
                              generation_config=config_path, journal_id="p6-test", qwen_artifact=root / "unused")
    return env, service


def provider_settings(**updates):
    values = dict(provider_policy_approved=True, openai_api_key="k" * 40, openai_base_url=OPENROUTER,
                  primary_llm_model="deepseek/deepseek-v4.1-flash") | updates
    return configuration(**values)


def test_zdr_client_routes_only_to_zero_retention_openrouter_endpoints():
    client = openrouter_zdr_client("deepseek/deepseek-v4.1-flash", provider_settings())
    assert client.extra_body == {"provider": {"zdr": True, "data_collection": "deny"}}
    assert ZDR_PROVIDER_PREFERENCES == {"zdr": True, "data_collection": "deny"}
    assert client.max_retries == 0 and client.temperature == 0
    with pytest.raises(RuntimeError):
        openrouter_zdr_client("m", provider_settings(openai_base_url="https://api.openai.com/v1"))


def test_service_refuses_unapproved_provider_policy_and_missing_authorities(environment, tmp_path):
    env, service = environment
    with pytest.raises(RuntimeError):
        build_runtime(service, configuration(openai_api_key="k" * 40), encoder=env.encoder, budget=env.budget,
                      vector_store=env.vector_store, client_factory=lambda model, settings: None)
    with pytest.raises(RuntimeError):
        build_runtime(service.model_copy(update={"authority_dir": tmp_path}), provider_settings(),
                      encoder=env.encoder, budget=env.budget, vector_store=env.vector_store)


def test_composed_service_answers_extractively_through_injected_provider(environment):
    env, service = environment
    fake = FakeProviderClient(json.dumps(pick(env, "Cláusula 1ª")))
    runtime = build_runtime(service, provider_settings(), encoder=env.encoder, budget=env.budget,
                            vector_store=env.vector_store, client_factory=lambda model, settings: fake)
    assert runtime.generator.name == "provider:deepseek/deepseek-v4.1-flash"
    body = AnswerRequest(question=Q_FEES, plan=plan()).model_dump(mode="json")
    with TestClient(create_app(runtime=runtime, settings=provider_settings())) as client:
        headers = {"X-API-Key": QUERY_KEY}
        assert client.get("/ready", headers=headers).status_code == 200
        answer = client.post("/v1/answer", headers=headers, json=body)
    assert answer.status_code == 200, answer.text
    data = answer.json()
    assert data["status"] == "answered" and len(fake.calls) == 1
    quotes = {unit.verbatim_text for unit in env.bundle.units}
    assert data["citations"] and all(c["quote"] in quotes and c["parties"] for c in data["citations"])

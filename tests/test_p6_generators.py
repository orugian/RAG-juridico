import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel, GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import SecretStr

import app.generators as generators
from app.config import Settings
from app.contracts import CitationLocation, CitationUnit, GenerationCandidate, SourceSpan
from app.generators import (DeterministicSyntheticGenerator, GeneratorRequest, GeneratorUnavailable,
                            LangChainCandidateGenerator, build_provider_generator)

SENSITIVE = "CNPJ 11.222.333/0001-44 segredo"


def unit(unit_id, text, title=""):
    return CitationUnit(unit_id=unit_id, instrument_id="i1", verbatim_text=text, source_ids=["s1"], block_ids=["b1"],
                        spans=[SourceSpan(source_id="s1", block_id="b1", start=0, end=len(text))],
                        location=CitationLocation(label="Cláusula 1"), contract_title=title)


def request(question, units, items=("q1",), timeout_seconds=5):
    return GeneratorRequest(messages=(HumanMessage(content="x"),), question=question, question_item_ids=tuple(items),
                            units=tuple(units), timeout_seconds=timeout_seconds)


UNITS = [unit("u3", "A multa por rescisão é de 10%."), unit("u1", "Os honorários mensais são de R$ 1.000,00."),
         unit("u2", "O foro é o de São Paulo.", "Contrato de honorários e multa")]


def test_synthetic_selects_by_terms_and_orders_by_score_then_id():
    result = DeterministicSyntheticGenerator().generate(request("Qual a multa e os honorários?", UNITS, ("q1", "q2")))
    assert isinstance(result, dict)
    assert [s["unit_id"] for s in result["selections"]] == ["u2", "u1", "u3"]
    assert all(s["question_item_ids"] == ["q1", "q2"] for s in result["selections"])
    assert result["action"] == "select" and result["approved_fact_ids"] == [] and result["clarification_code"] is None
    GenerationCandidate.model_validate(result)


def test_synthetic_respects_max_units_and_zero_scores():
    result = DeterministicSyntheticGenerator(max_units=1).generate(request("Qual a multa?", UNITS))
    assert [s["unit_id"] for s in result["selections"]] == ["u2"]
    assert DeterministicSyntheticGenerator().generate(request("foro", [UNITS[0]]))["selections"] == []


def test_synthetic_abstains_without_matching_terms():
    result = DeterministicSyntheticGenerator().generate(request("Qual o valor do seguro?", UNITS))
    assert result == {"action": "abstain", "selections": [], "approved_fact_ids": [], "clarification_code": None}
    assert GenerationCandidate.model_validate(result).action == "abstain"
    assert DeterministicSyntheticGenerator().generate(request("Qual o?", UNITS))["action"] == "abstain"


def test_synthetic_is_deterministic_and_named():
    generator = DeterministicSyntheticGenerator()
    req = request("multa honorários", UNITS)
    assert generator.generate(req) == generator.generate(req)
    assert generator.name == "deterministic-synthetic-v1"


def fake_json(payload):
    return FakeListChatModel(responses=[payload])


def test_langchain_generator_returns_string_content():
    payload = json.dumps({"action": "abstain", "selections": [], "approved_fact_ids": [], "clarification_code": None})
    generator = LangChainCandidateGenerator(fake_json(payload), name="fake")
    assert generator.name == "fake"
    assert GenerationCandidate.model_validate_json(generator.generate(request("q", UNITS))).action == "abstain"


def test_langchain_generator_concatenates_text_blocks_only():
    blocks = [{"type": "text", "text": '{"action":'}, {"type": "image", "url": "x"}, {"type": "text", "text": '"abstain"}'}]
    client = GenericFakeChatModel(messages=iter([AIMessage(content=blocks)]))
    result = LangChainCandidateGenerator(client, name="fake").generate(request("q", UNITS))
    assert result == '{"action":"abstain"}'


def test_langchain_generator_passes_request_messages():
    seen = []

    class Client(FakeListChatModel):
        def _call(self, messages, *args, **kwargs):
            seen.append(messages)
            return "{}"

    LangChainCandidateGenerator(Client(responses=["{}"]), name="fake").generate(request("q", UNITS))
    assert [m.content for m in seen[0]] == ["x"]


def test_unsupported_output_type_is_value_error_not_technical():
    class Client:
        def invoke(self, messages):
            return AIMessage(content=[])

    class Weird:
        def invoke(self, messages):
            class Msg:
                content = 42
            return Msg()

    assert LangChainCandidateGenerator(Client(), name="fake").generate(request("q", UNITS)) == ""
    with pytest.raises(ValueError, match="generator_output_unsupported"):
        LangChainCandidateGenerator(Weird(), name="fake").generate(request("q", UNITS))


@pytest.mark.parametrize("error", [RuntimeError(SENSITIVE), TimeoutError(SENSITIVE), ValueError(SENSITIVE)])
def test_client_failure_becomes_generic_unavailable_without_leak(error):
    class Client:
        def invoke(self, messages):
            raise error

    with pytest.raises(GeneratorUnavailable) as caught:
        LangChainCandidateGenerator(Client(), name="fake").generate(request(SENSITIVE, UNITS))
    exc = caught.value
    assert exc.__cause__ is None and exc.__context__ is None
    assert "segredo" not in f"{exc!s}{exc!r}{exc.args}" and str(exc) == "generator_unavailable"
    assert exc.category in {"timeout", "service_error", "validation_error"}


def test_callback_boundary_is_enforced_at_init_and_generate():
    from langchain_core.callbacks.base import BaseCallbackHandler

    class Foreign(BaseCallbackHandler):
        pass

    with pytest.raises(ValueError, match="Unapproved callback"):
        LangChainCandidateGenerator(FakeListChatModel(responses=["{}"], callbacks=[Foreign()]), name="fake")
    client = FakeListChatModel(responses=["{}"])
    generator = LangChainCandidateGenerator(client, name="fake")
    client.callbacks = [Foreign()]
    with pytest.raises(ValueError, match="Unapproved callback"):
        generator.generate(request("q", UNITS))


def settings(**values):
    return Settings(**{"provider_policy_approved": True, "openai_api_key": SecretStr("k"), **values})


def test_provider_policy_gate_precedes_any_client(monkeypatch):
    calls = []
    with pytest.raises(RuntimeError, match="Provider data policy has not been approved"):
        build_provider_generator(settings(provider_policy_approved=False), client_factory=lambda *a: calls.append(a))
    assert calls == []


def test_provider_requires_credential():
    calls = []
    with pytest.raises(RuntimeError, match="Provider credential is not configured"):
        build_provider_generator(settings(openai_api_key=SecretStr("")), client_factory=lambda *a: calls.append(a))
    assert calls == []


@pytest.mark.parametrize("fallback", [False, True])
def test_provider_generator_uses_selected_model_once(fallback):
    calls = []
    config = settings()

    def factory(model, received):
        calls.append((model, received))
        return FakeListChatModel(responses=["{}"])

    generator = build_provider_generator(config, fallback=fallback, client_factory=factory)
    expected = config.fallback_llm_model if fallback else config.primary_llm_model
    assert calls == [(expected, config)]
    assert isinstance(generator, LangChainCandidateGenerator) and expected in generator.name
    assert generator.generate(request("q", UNITS)) == "{}"


def test_nothing_constructed_at_import(monkeypatch):
    import importlib
    import sys

    import langchain_openai

    import app

    def boom(*args, **kwargs):
        raise AssertionError("provider constructed")

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", boom)
    monkeypatch.setattr(app, "generators", generators)
    monkeypatch.delitem(sys.modules, "app.generators")
    fresh = importlib.import_module("app.generators")
    assert fresh is not generators
    with pytest.raises(RuntimeError, match="not been approved"):
        fresh.build_provider_generator(settings(provider_policy_approved=False))


def test_default_factory_builds_chat_openai_lazily_with_safe_options(monkeypatch):
    import langchain_openai

    captured = {}

    class Fake(FakeListChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            super().__init__(responses=["{}"])

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", Fake)
    config = settings()
    build_provider_generator(config)
    assert captured["model"] == config.primary_llm_model and captured["temperature"] == 0
    assert captured["max_retries"] == 0 and captured["timeout"] == config.request_timeout_seconds


def test_unsupported_content_raises_the_explicit_invalid_output_type():
    from app.generators import GeneratorOutputInvalid, GeneratorRequest, LangChainCandidateGenerator

    class Client:
        def invoke(self, messages):
            return type("Response", (), {"content": 42})()
    assert issubclass(GeneratorOutputInvalid, ValueError)
    request = GeneratorRequest(messages=(), question="q", question_item_ids=("q1",), units=(), timeout_seconds=1.0)
    with pytest.raises(GeneratorOutputInvalid):
        LangChainCandidateGenerator(Client(), name="n").generate(request)


def test_langchain_generator_propagates_timeout_seconds():
    seen = {}

    class TimedClient:
        def invoke(self, messages, timeout=None):
            seen["timeout"] = timeout
            return "{}"

    generator = LangChainCandidateGenerator(TimedClient(), name="timed")
    generator.generate(request("q", UNITS, timeout_seconds=12.5))
    assert seen["timeout"] == 12.5

import asyncio
from contextvars import ContextVar

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.documents import Document
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.agent import ProductionAgent
from app.retrieval.hybrid import LegalHybridRetriever


class Exporter(BaseCallbackHandler):
    def __init__(self):
        self.writes = []

    def on_retriever_start(self, serialized, query, **kwargs):
        self.writes.append(query)

    def on_retriever_end(self, documents, **kwargs):
        self.writes.append(documents)

    def on_chain_start(self, serialized, inputs, **kwargs):
        self.writes.append(inputs)

    def on_chain_end(self, outputs, **kwargs):
        self.writes.append(outputs)


def retriever():
    return LegalHybridRetriever(docs=[Document(page_content="Prova sigilosa", metadata={"chunk_id": "a", "clean_identifiers": ["12345678900"]})])


@pytest.mark.parametrize("asynchronous", [False, True])
def test_unapproved_supplied_callbacks_rejected_before_source_events(asynchronous):
    exporter = Exporter()
    instance = retriever()
    with pytest.raises(ValueError, match="callback"):
        if asynchronous:
            asyncio.run(instance.ainvoke("CPF 12345678900", {"callbacks": [exporter]}))
        else:
            instance.invoke("CPF 12345678900", {"callbacks": [exporter]})
    assert exporter.writes == []


@pytest.mark.parametrize("fallback", [False, True])
def test_persisted_provider_callback_rejected_before_graph_or_provider(fallback):
    exporter = Exporter()
    calls = []
    primary = RunnableLambda(lambda messages: calls.append(messages) or AIMessage(content="sigiloso"))
    bound = primary.with_config(callbacks=[exporter])
    agent = ProductionAgent(primary=primary if fallback else bound, fallback=bound if fallback else primary)
    with pytest.raises(ValueError, match="callback"):
        agent.invoke("Pergunta sigilosa")
    assert exporter.writes == [] and calls == []
    assert bound.config["callbacks"] == [exporter]


def test_inherited_callback_context_rejected_and_restored():
    from langchain_core.runnables.config import var_child_runnable_config
    exporter = Exporter()
    config = {"callbacks": [exporter]}
    token = var_child_runnable_config.set(config)
    try:
        with pytest.raises(ValueError, match="callback"):
            retriever().invoke("CPF 12345678900")
        assert var_child_runnable_config.get() is config
    finally:
        var_child_runnable_config.reset(token)
    assert exporter.writes == []


def test_active_generic_context_hook_is_rejected(monkeypatch):
    from langchain_core.tracers import context
    exporter = Exporter()
    hook = ContextVar("unapproved_exporter", default=exporter)
    monkeypatch.setattr(context, "_configure_hooks", [(hook, True, None, None)])
    with pytest.raises(ValueError, match="callback"):
        retriever().invoke("CPF 12345678900")
    assert exporter.writes == []


def test_callback_configuration_factory_is_rejected_without_executing_it():
    calls = []
    component = RunnableLambda(lambda messages: AIMessage(content="sigiloso")).with_config({})
    component.config_factories = [lambda config: calls.append(config) or {}]
    with pytest.raises(ValueError, match="callback"):
        ProductionAgent(primary=component).invoke("Pergunta sigilosa")
    assert calls == []


def test_debug_stdout_is_rejected_before_data(capsys):
    from langchain_core.globals import get_debug, set_debug
    old = get_debug()
    set_debug(True)
    try:
        with pytest.raises(ValueError, match="callback"):
            retriever().invoke("CPF 12345678900")
    finally:
        set_debug(old)
    assert "12345678900" not in capsys.readouterr().out


def test_approved_callback_preserves_verbatim_result_and_has_no_raw_snapshot():
    from app.telemetry import SafeCallbackHandler
    handler = SafeCallbackHandler()
    component = RunnableLambda(lambda messages: AIMessage(content="Prova sigilosa")).with_config(callbacks=[handler])
    result = ProductionAgent(primary=component).invoke("Pergunta sigilosa")
    assert result["response"] == "Prova sigilosa"
    assert handler.runs and "sigilosa" not in str(handler.runs)


@pytest.mark.parametrize("observer_in_condition", [False, True])
def test_branch_condition_and_body_callbacks_are_inspected(observer_in_condition):
    from langchain_core.runnables import RunnableBranch
    exporter = Exporter()
    calls = []
    condition = RunnableLambda(lambda messages: calls.append("condition") or True)
    body = RunnableLambda(lambda messages: calls.append("body") or AIMessage(content="Sigiloso"))
    if observer_in_condition:
        condition = condition.with_config(callbacks=[exporter])
    else:
        body = body.with_config(callbacks=[exporter])
    branch = RunnableBranch((condition, body), body)
    with pytest.raises(ValueError, match="callback"):
        ProductionAgent(primary=branch).invoke("Pergunta sigilosa")
    assert calls == [] and exporter.writes == []


@pytest.mark.parametrize("field,value", [("callbacks", "observer"), ("verbose", "stdout")])
def test_dynamic_model_fields_rejected_before_processing(field, value):
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langchain_core.runnables import ConfigurableField
    exporter = Exporter()
    model = FakeListChatModel(responses=["Sigiloso"])
    dynamic = model.configurable_fields(**{field: ConfigurableField(id=value)})
    bound = dynamic.with_config(configurable={value: [exporter] if field == "callbacks" else True})
    with pytest.raises(ValueError, match="callback"):
        ProductionAgent(primary=bound).invoke("Pergunta sigilosa")
    assert model.i == 0 and exporter.writes == []


@pytest.mark.parametrize("use_factory", [False, True])
def test_dynamic_alternatives_rejected_without_running_factory(use_factory):
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langchain_core.runnables import ConfigurableField
    exporter = Exporter()
    calls = []
    model = FakeListChatModel(responses=["Sigiloso"], callbacks=[exporter])
    def factory():
        calls.append("factory")
        return model
    primary = FakeListChatModel(responses=["Default"])
    dynamic = primary.configurable_alternatives(ConfigurableField(id="provider"), alternative=factory if use_factory else model).with_config(configurable={"provider": "alternative"})
    with pytest.raises(ValueError, match="callback"):
        ProductionAgent(primary=dynamic).invoke("Pergunta sigilosa")
    assert calls == [] and exporter.writes == []


def test_deep_composed_branch_inside_parallel_sequence_rejected():
    from langchain_core.runnables import RunnableBranch, RunnableParallel
    exporter = Exporter()
    safe = RunnableLambda(lambda value: value)
    unknown = safe.with_config(callbacks=[exporter])
    branch = RunnableBranch((safe, unknown), safe)
    composed = safe | RunnableParallel(left=branch, right=safe) | safe
    with pytest.raises(ValueError, match="callback"):
        ProductionAgent(primary=composed).invoke("Pergunta sigilosa")
    assert exporter.writes == []

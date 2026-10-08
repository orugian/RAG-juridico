"""Explicit telemetry projection and protected LangSmith transport boundary.

Native automatic tracing is disabled inside instrumented application calls. Only
this projection reaches the exporter; unknown fields, attachments and events are
dropped before the SDK receives them. The pipeline's original values are untouched.
"""
from contextvars import ContextVar
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
import math
import copy
import threading
import time
import os
from uuid import UUID, uuid4

from langsmith import Client, tracing_context
from langchain_core.callbacks.base import BaseCallbackHandler
from urllib3.util.retry import Retry

from app.config import get_settings

FIXED_NODES = frozenset({"chain", "llm", "process", "fallback", "error", "retrieve", "validate", "render", "SecurityPipeline.run", "SecurityPipeline.process", "ProductionAgent.invoke"})
FIXED_TAGS = frozenset({"privacy_redacted", "internal", "llm_generation"})
_parent = ContextVar("safe_telemetry_parent", default=None)
_client = ContextVar("safe_telemetry_client", default=None)
_configuration = ContextVar("safe_telemetry_configuration", default=None)


@contextmanager
def telemetry_configuration(settings):
    """Explicit per-request configuration; no global settings or inherited exporter.

    Legacy callers outside this scope retain their existing configuration behavior.
    Context variables follow P6's copy_context boundaries and unwind on every exit.
    """
    configuration_token = _configuration.set(settings)
    client_token = _client.set(None)
    try:
        yield
    finally:
        _client.reset(client_token)
        _configuration.reset(configuration_token)


def error_category(error):
    if error is None:
        return None
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, ValueError):
        return "validation_error"
    return "service_error"


def fixed_node(name):
    return name if isinstance(name, str) and name in FIXED_NODES else "chain"


def safe_metadata(data):
    """Values as well as keys are constrained; arbitrary strings never pass."""
    if not isinstance(data, dict):
        return {}
    result = {}
    for key in ("latency_ms", "input_tokens", "output_tokens", "total_tokens", "evidence_count"):
        value = data.get(key)
        if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1_000_000_000:
            result[key] = value
    if type(data.get("cache_hit")) is bool:
        result["cache_hit"] = data["cache_hit"]
    if isinstance(data.get("status"), str) and data["status"] in {"running", "success", "error", "answered", "abstained", "needs_clarification"}:
        result["status"] = data["status"]
    return result


def _uuid(value):
    return UUID(str(value)) if value is not None else None


class ProtectedRunClient:
    """Restricts run writes before create/update reaches the SDK/HTTP layer.

This client is composed only for telemetry; it is not a general LangSmith client.
    Batch ingestion is disabled, and application code never attaches a native tracer.
    The facade intentionally exposes no SDK batch/attachment/unknown write APIs.
"""
    def __init__(self, **kwargs):
        self._sdk = Client(**kwargs)

    def close(self):
        self._sdk.session.close()

    def create_run(self, name, inputs, run_type, **kwargs):
        run_id = _uuid(kwargs.get("id")) or uuid4()
        parent = _uuid(kwargs.get("parent_run_id"))
        return self._sdk.create_run(
            fixed_node(name), {}, run_type if isinstance(run_type, str) and run_type in {"chain", "llm", "retriever"} else "chain",
            id=run_id, parent_run_id=parent, project_name="AndradeAdvogados",
            start_time=datetime.now(timezone.utc), tags=["privacy_redacted"],
            extra={"metadata": safe_metadata(kwargs.get("telemetry"))},
        )

    def update_run(self, run_id, **kwargs):
        return self._sdk.update_run(
            _uuid(run_id), outputs={},
            error="service_error" if kwargs.get("error") is not None else None,
            end_time=datetime.now(timezone.utc),
            extra={"metadata": safe_metadata(kwargs.get("telemetry"))},
            tags=["privacy_redacted"],
        )


def create_protected_client(*, settings=None, session=None, api_url=None):
    settings = settings or get_settings()
    # Explicit destination prevents endpoint overrides inherited from workstation env.
    if api_url is not None and session is None:
        raise ValueError("Alternate telemetry endpoint requires injected transport")
    if not settings.langsmith_api_key.get_secret_value():
        raise ValueError("Telemetry credential is not configured")
    return ProtectedRunClient(
        api_url=api_url or "https://api.smith.langchain.com",
        api_key=settings.langsmith_api_key.get_secret_value(), session=session,
        auto_batch_tracing=False, hide_inputs=True, hide_outputs=True,
        hide_metadata=safe_metadata, omit_traced_runtime_info=True,
        timeout_ms=1000, retry_config=Retry(total=0),
    )


def safe_trace(*, name, run_type="chain"):
    """Trace only fixed metadata, suppressing native tracing throughout nested calls."""
    name = fixed_node(name)
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            assert_callback_boundary()
            settings = _configuration.get()
            if settings is None:
                settings = get_settings()
            client = _client.get()
            created = False
            if client is None and settings.langsmith_tracing_v2 and settings.langsmith_api_key.get_secret_value():
                try:
                    client = create_protected_client(settings=settings)
                    created = True
                except Exception:
                    client = None
            run_id = uuid4()
            parent_token = _parent.set(run_id)
            client_token = _client.set(client)
            error = None
            try:
                if client:
                    try:
                        client.create_run(name, {}, run_type, id=run_id, parent_run_id=parent_token.old_value if isinstance(parent_token.old_value, UUID) else None)
                    except Exception:
                        pass
                with tracing_context(enabled=False):
                    return function(*args, **kwargs)
            except BaseException as exc:
                error = error_category(exc)
                raise
            finally:
                if client:
                    try:
                        client.update_run(run_id, error=error)
                    except Exception:
                        pass
                _parent.reset(parent_token)
                _client.reset(client_token)
                if created:
                    # No background batching threads; close per-operation HTTP resources.
                    try:
                        client.close()
                    except Exception:
                        pass
        return wrapped
    return decorate


def assert_callback_boundary(*components, config=None):
    """Fail closed before source events for unapproved LangChain observers.

    Checks request, inherited, stored and context-hook callbacks. This is a
    composition boundary, not a sandbox for arbitrary Python component code.
    No shared component/config/global is mutated. Dynamic config factories and
    stdout tracing cannot be safely qualified, so are rejected before processing.
    """
    from langchain_core.callbacks.manager import CallbackManager, AsyncCallbackManager
    from langchain_core.globals import get_debug, get_verbose
    from langchain_core.runnables import Runnable
    from langchain_core.runnables.configurable import DynamicRunnable
    from langchain_core.runnables.config import var_child_runnable_config
    from langchain_core.tracers.context import _configure_hooks

    def reject():
        raise ValueError("Unapproved callback configuration")

    def callbacks(value):
        if value is None:
            return
        if type(value) in (CallbackManager, AsyncCallbackManager):
            callbacks(value.handlers)
            callbacks(value.inheritable_handlers)
        elif type(value) in (list, tuple):
            if any(type(handler) is not SafeCallbackHandler for handler in value):
                reject()
        else:
            reject()

    def configuration(value):
        if value is not None:
            if not isinstance(value, dict):
                reject()
            callbacks(value.get("callbacks"))

    if get_debug() or get_verbose():
        reject()
    configuration(var_child_runnable_config.get())
    configuration(config)
    for variable, _, handler_class, environment_key in tuple(_configure_hooks):
        active = variable.get()
        if active is not None:
            callbacks([active])
        if environment_key and os.environ.get(environment_key) and handler_class is not SafeCallbackHandler:
            reject()
    visited = set()
    def inspect_component(component):
        if id(component) in visited:
            return
        if isinstance(component, (list, tuple, dict)):
            visited.add(id(component))
            for item in component.values() if isinstance(component, dict) else component:
                inspect_component(item)
            return
        if not isinstance(component, Runnable):
            return
        visited.add(id(component))
        # DynamicRunnable may replace fields or construct an alternative *after*
        # inspection. Reject every dynamic wrapper before executing any factory.
        if isinstance(component, DynamicRunnable):
            reject()
        # Use actual stored attributes, never execute configuration factories.
        values = vars(component)
        callbacks(values.get("callbacks"))
        configuration(values.get("config"))
        if values.get("verbose") or values.get("config_factories"):
            reject()
        configuration(values.get("kwargs"))
        for value in values.values():
            inspect_component(value)
    for component in components:
        inspect_component(component)


class SafeCallbackHandler(BaseCallbackHandler):
    """Bounded local projection. Disabling hide flags never enables raw export."""
    def __init__(self, hide_inputs=True, hide_outputs=True, *, max_runs=1024):
        super().__init__()
        if max_runs < 1:
            raise ValueError("max_runs must be positive")
        self.hide_inputs = True
        self.hide_outputs = True
        self._runs = {}
        self._lock = threading.RLock()
        self.max_runs = max_runs

    @property
    def runs(self):
        with self._lock:
            return copy.deepcopy(self._runs)

    def get_run(self, run_id):
        with self._lock:
            return copy.deepcopy(self._runs.get(str(run_id)))

    def _start(self, run_id, parent_run_id, name, run_type, metadata):
        with self._lock:
            if len(self._runs) >= self.max_runs:
                self._runs.pop(next(iter(self._runs)))
            self._runs[str(_uuid(run_id))] = {
                "run_id": str(_uuid(run_id)), "parent_run_id": str(_uuid(parent_run_id)) if parent_run_id else None,
                "name": fixed_node(name), "step_name": fixed_node(name), "run_type": run_type,
                "tags": ["privacy_redacted"], "metadata": safe_metadata(metadata),
                "start_perf": time.perf_counter(), "start_time": time.time(),
                "inputs": {"redacted": "[REDACTED_INPUT_PII_PROTECTED]"},
                "outputs": None, "status": "running", "latency_ms": None, "tokens": {},
                "error": None, "error_type": None,
            }

    def _finish(self, run_id, *, error=None, tokens=None):
        with self._lock:
            run = self._runs.get(str(run_id))
            if run is None:
                return
            run.update(end_time=time.time(), latency_ms=round((time.perf_counter() - run["start_perf"]) * 1000, 2),
                       outputs={"redacted": "[REDACTED_OUTPUT_PII_PROTECTED]"}, status="error" if error else "success", error=error_category(error))
            run["error_type"] = type(error).__name__ if type(error) in {ValueError, RuntimeError, TimeoutError, TypeError} else "Exception" if error else None
            if isinstance(tokens, dict):
                run["tokens"] = {k: v for k, v in tokens.items() if k in {"prompt_tokens", "completion_tokens", "total_tokens"} and type(v) is int and 0 <= v <= 1_000_000_000}

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, metadata=None, **kwargs):
        self._start(run_id, parent_run_id, (serialized or {}).get("name", "chain"), "chain", metadata)

    def on_chain_end(self, outputs, *, run_id, **kwargs):
        self._finish(run_id)

    def on_chain_error(self, error, *, run_id, **kwargs):
        self._finish(run_id, error=error)

    def on_llm_start(self, serialized, prompts, *, run_id, parent_run_id=None, metadata=None, **kwargs):
        self._start(run_id, parent_run_id, (serialized or {}).get("name", "llm"), "llm", metadata)

    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None, metadata=None, **kwargs):
        self._start(run_id, parent_run_id, "llm", "llm", metadata)

    def on_llm_end(self, response, *, run_id, **kwargs):
        output = getattr(response, "llm_output", None) or {}
        self._finish(run_id, tokens=output.get("token_usage", output.get("usage")))

    def on_llm_error(self, error, *, run_id, **kwargs):
        self._finish(run_id, error=error)

"""
Monitoring & Structured Logging
Production-grade metrics collection and JSON logging for Andrade Advogados RAG API.

Hardened with adversarial review:
- Native Python logging `extra={...}` extraction (avoids dropping metadata).
- Thread-safe MetricsCollector supporting concurrent EC2 workloads.
- Strict mapping to app.models.MetricsResponse.
- Zero-latency handling for instant in-memory cache hits.
- Payload length truncation to prevent CloudWatch/ELK log bloat.
- Context manager `track_latency` for frictionless timing in FastAPI/LangGraph.
"""

import copy
import json
import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Generator, List, Optional, Union
from uuid import UUID

from langchain_core.callbacks.base import BaseCallbackHandler

from app.config import get_settings
from app.models import MetricsResponse

settings = get_settings()

# Standard LogRecord attributes to ignore when extracting custom extra fields
_STANDARD_LOG_RECORD_ATTRS = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "extra_data"
})

_MAX_STRING_LOG_LENGTH = 1500


class JSONFormatter(logging.Formatter):
    """
    Format log records as structured JSON for log aggregators (CloudWatch, Datadog, ELK).
    Safely captures standard and extra logging fields while truncating oversized payloads.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_obj: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "module": record.module,
            "function": record.funcName,
            "lineno": record.lineno,
            "process": record.process,
            "thread": record.thread,
        }

        # 1. Extrai campos extras explícitos passados via extra_data (compatibilidade legada)
        if hasattr(record, "extra_data") and isinstance(record.extra_data, dict):
            for k, v in record.extra_data.items():
                log_obj[k] = self._truncate_value(v)

        # 2. Extrai campos extras passados via padrão nativo logger.info("...", extra={"user": "...", ...})
        for key, val in record.__dict__.items():
            if key not in _STANDARD_LOG_RECORD_ATTRS and not key.startswith("_"):
                log_obj[key] = self._truncate_value(val)

        # 3. Tratamento de exceções
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_obj, default=str, ensure_ascii=False)

    @classmethod
    def _truncate_value(cls, val: Any) -> Any:
        """Evita payload bloat truncando strings gigantes (ex.: contratos inteiros colados em logs)."""
        if isinstance(val, str) and len(val) > _MAX_STRING_LOG_LENGTH:
            return val[:_MAX_STRING_LOG_LENGTH] + f"... [truncated {len(val)} chars]"
        return val


def get_logger(name: str = "andrade-advogados-api") -> logging.Logger:
    """Creates or returns a centralized structured JSON logger configured with app settings."""
    logger = logging.getLogger(name)

    # Configura nível de log com base nas configurações da aplicação
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logger.setLevel(log_level)

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
        logger.propagate = False

    return logger


class MetricsCollector:
    """
    Thread-safe collector for real-time API metrics, latencies, cache performance,
    and LLM token consumption (Qwen / DeepSeek).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.total_requests: int = 0
        self.total_errors: int = 0
        self.latency_sum_ms: float = 0.0
        self.latency_count: int = 0
        self.cache_hits: int = 0
        self.cache_misses: int = 0
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.last_updated: float = time.time()

    def record_request(
        self,
        latency_ms: float,
        success: bool,
        cache_hit: bool = False,
        cache_miss: bool = False,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        """Atomically record metrics for a processed chat, search, or tool request."""
        with self._lock:
            self.total_requests += 1

            if not success:
                self.total_errors += 1

            # Suporta latências ultra-rápidas de cache (>= 0)
            if latency_ms >= 0:
                self.latency_sum_ms += latency_ms
                self.latency_count += 1

            if cache_hit:
                self.cache_hits += 1
            elif cache_miss:
                self.cache_misses += 1

            self.total_input_tokens += max(0, int(input_tokens))
            self.total_output_tokens += max(0, int(output_tokens))
            self.last_updated = time.time()

    def get_metrics_response(self) -> MetricsResponse:
        """
        Export accumulated metrics strictly formatted as the Pydantic MetricsResponse model.
        """
        with self._lock:
            total_req = self.total_requests
            total_err = self.total_errors
            lat_count = self.latency_count
            lat_sum = self.latency_sum_ms
            c_hits = self.cache_hits
            c_misses = self.cache_misses
            in_tokens = self.total_input_tokens
            out_tokens = self.total_output_tokens

        # Cálculos derivados com tratamento de divisão por zero
        error_rate = f"{(total_err / total_req * 100):.1f}%" if total_req > 0 else "0.0%"
        avg_lat = round(lat_sum / lat_count, 2) if lat_count > 0 else 0.0

        total_cache = c_hits + c_misses
        cache_hit_rate = f"{(c_hits / total_cache * 100):.1f}%" if total_cache > 0 else "0.0%"
        token_eff = round(out_tokens / in_tokens, 2) if in_tokens > 0 else 0.0

        return MetricsResponse(
            total_requests=total_req,
            total_erros=total_err,  # Mapeado com o atributo do modelo (app/models.py)
            error_rate=error_rate,
            avg_latency_ms=avg_lat,
            cache_hit_rate=cache_hit_rate,
            total_input_tokens=in_tokens,
            total_output_tokens=out_tokens,
            token_efficiency=token_eff,
        )

    def reset(self) -> None:
        """Reset all metrics to initial state (useful for test isolation)."""
        with self._lock:
            self.total_requests = 0
            self.total_errors = 0
            self.latency_sum_ms = 0.0
            self.latency_count = 0
            self.cache_hits = 0
            self.cache_misses = 0
            self.total_input_tokens = 0
            self.total_output_tokens = 0
            self.last_updated = time.time()


# Instâncias globais exportadas
logger = get_logger()
metrics_collector = MetricsCollector()


@contextmanager
def track_latency() -> Generator[Dict[str, float], None, None]:
    """
    Context manager to easily measure latency in milliseconds.
    
    Usage:
        with track_latency() as tracker:
            # perform operation
            pass
        print(f"Elapsed: {tracker['latency_ms']}ms")
    """
    tracker = {"latency_ms": 0.0}
    start = time.perf_counter()
    try:
        yield tracker
    finally:
        tracker["latency_ms"] = round((time.perf_counter() - start) * 1000, 2)


# ==============================================================================
# LangSmith PII Redaction & Observability Protection (Task 10)
# ==============================================================================

REDACTED_INPUT_PII_PROTECTED = "[REDACTED_INPUT_PII_PROTECTED]"
REDACTED_OUTPUT_PII_PROTECTED = "[REDACTED_OUTPUT_PII_PROTECTED]"
REDACTED_PII_PROTECTED = "[REDACTED_PII_PROTECTED]"
REDACTED_CPF = "[REDACTED_CPF]"
REDACTED_CNPJ = "[REDACTED_CNPJ]"

# Regex patterns for Brazilian Tax Identifiers (formatted and raw digits)
_CNPJ_PATTERN = re.compile(r"(?<!\d)(\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2})(?!\d)")
_CPF_PATTERN = re.compile(r"(?<!\d)(\d{3}\.?\d{3}\.?\d{3}-?\d{2})(?!\d)")

# Known contractual keys containing sensitive legal text
KNOWN_CONTRACTUAL_KEYS = frozenset({
    "verbatim_text",
    "page_content",
    "text_raw",
    "contract_payload",
    "contract_text",
    "contract_body",
    "contract",
    "minuta",
    "raw_content",
    "full_text",
    "document",
    "documents",
})

INPUT_CONTRACTUAL_KEYS = frozenset(KNOWN_CONTRACTUAL_KEYS | {
    "input",
    "inputs",
})

OUTPUT_CONTRACTUAL_KEYS = frozenset(KNOWN_CONTRACTUAL_KEYS | {
    "output",
    "outputs",
    "result",
    "results",
    "answer",
    "generation",
    "generations",
    "retrieved_documents",
    "retrieved_chunks",
})


def configure_langsmith_redaction(
    hide_inputs: bool = True,
    hide_outputs: bool = True,
    project_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Configure global PII masking and privacy controls for LangSmith observability.
    Enforces OAB professional secrecy and LGPD compliance by redacting contractual
    payloads while preserving analytical execution telemetry.
    """
    if project_name is None:
        project_name = (
            os.environ.get("LANGCHAIN_PROJECT")
            or getattr(settings, "langsmith_project", "AndradeAdvogados")
        )

    # Set project environment variables
    os.environ["LANGCHAIN_PROJECT"] = project_name
    os.environ["LANGSMITH_PROJECT"] = project_name

    # Set redaction flags for LangSmith client/tracer
    if hide_inputs:
        os.environ["LANGSMITH_HIDE_INPUTS"] = "true"
        os.environ["LANGCHAIN_HIDE_INPUTS"] = "true"
    else:
        os.environ["LANGSMITH_HIDE_INPUTS"] = "false"
        os.environ["LANGCHAIN_HIDE_INPUTS"] = "false"

    if hide_outputs:
        os.environ["LANGSMITH_HIDE_OUTPUTS"] = "true"
        os.environ["LANGCHAIN_HIDE_OUTPUTS"] = "true"
    else:
        os.environ["LANGSMITH_HIDE_OUTPUTS"] = "false"
        os.environ["LANGCHAIN_HIDE_OUTPUTS"] = "false"

    # Reset any cached env lookups in langsmith.utils
    try:
        import langsmith.utils
        if hasattr(langsmith.utils.get_env_var, "cache_clear"):
            langsmith.utils.get_env_var.cache_clear()
    except Exception:
        pass

    return {
        "hide_inputs": hide_inputs,
        "hide_outputs": hide_outputs,
        "project_name": project_name,
        "tags": ["privacy_redacted", "lgpd_compliant"],
    }


def sanitize_telemetry_payload(
    data: Any,
    redaction_placeholder: str = REDACTED_PII_PROTECTED,
) -> Any:
    """
    Removes or masks CPFs, CNPJs and known contractual fields (verbatim_text,
    page_content, text_raw) from payloads before emitting telemetry or logging.
    Supports nested dicts, lists, tuples, and strings.
    """
    if data is None:
        return None

    if isinstance(data, str):
        # 1. Mask CNPJs (formatted and raw 14 digits)
        cleaned = _CNPJ_PATTERN.sub(REDACTED_CNPJ, data)
        # 2. Mask CPFs (formatted and raw 11 digits)
        cleaned = _CPF_PATTERN.sub(REDACTED_CPF, cleaned)
        return cleaned

    if isinstance(data, dict):
        sanitized_dict: Dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(key, str) and key.lower() in KNOWN_CONTRACTUAL_KEYS:
                sanitized_dict[key] = redaction_placeholder
            else:
                sanitized_dict[key] = sanitize_telemetry_payload(value, redaction_placeholder)
        return sanitized_dict

    if isinstance(data, list):
        return [sanitize_telemetry_payload(item, redaction_placeholder) for item in data]

    if isinstance(data, tuple):
        return tuple(sanitize_telemetry_payload(item, redaction_placeholder) for item in data)

    if hasattr(data, "page_content") and hasattr(data, "metadata"):
        new_doc = copy.deepcopy(data)
        new_doc.page_content = redaction_placeholder
        if isinstance(new_doc.metadata, dict):
            new_doc.metadata = sanitize_telemetry_payload(new_doc.metadata, redaction_placeholder)
        return new_doc

    return data


class SafeLangSmithCallbackHandler(BaseCallbackHandler):
    """
    Safe LangSmith callback handler ensuring LGPD compliance and OAB secrecy.
    Intercepts on_chain_start/end and on_llm_start/end events:
    - Redacts contractual inputs with [REDACTED_INPUT_PII_PROTECTED] when hide_inputs=True.
    - Redacts contractual outputs with [REDACTED_OUTPUT_PII_PROTECTED] when hide_outputs=True.
    - Preserves critical analytical metadata: latencies, tokens, step names, timestamps, error types.
    """

    def __init__(
        self,
        hide_inputs: bool = True,
        hide_outputs: bool = True,
    ):
        super().__init__()
        self.hide_inputs: bool = hide_inputs
        self.hide_outputs: bool = hide_outputs
        self._runs: Dict[str, Dict[str, Any]] = {}

    @property
    def runs(self) -> Dict[str, Dict[str, Any]]:
        return self._runs

    def get_run(self, run_id: Union[UUID, str]) -> Optional[Dict[str, Any]]:
        return self._runs.get(str(run_id))

    def _sanitize_inputs(self, inputs: Any) -> Any:
        if not self.hide_inputs:
            return inputs
        if isinstance(inputs, dict):
            sanitized: Dict[str, Any] = {}
            for k, v in inputs.items():
                if isinstance(k, str) and k.lower() in INPUT_CONTRACTUAL_KEYS:
                    sanitized[k] = REDACTED_INPUT_PII_PROTECTED
                else:
                    sanitized[k] = sanitize_telemetry_payload(
                        v, redaction_placeholder=REDACTED_INPUT_PII_PROTECTED
                    )
            return sanitized
        return sanitize_telemetry_payload(inputs, redaction_placeholder=REDACTED_INPUT_PII_PROTECTED)

    def _sanitize_outputs(self, outputs: Any) -> Any:
        if not self.hide_outputs:
            return outputs
        if isinstance(outputs, dict):
            sanitized: Dict[str, Any] = {}
            for k, v in outputs.items():
                if isinstance(k, str) and k.lower() in OUTPUT_CONTRACTUAL_KEYS:
                    sanitized[k] = REDACTED_OUTPUT_PII_PROTECTED
                else:
                    sanitized[k] = sanitize_telemetry_payload(
                        v, redaction_placeholder=REDACTED_OUTPUT_PII_PROTECTED
                    )
            return sanitized
        return sanitize_telemetry_payload(outputs, redaction_placeholder=REDACTED_OUTPUT_PII_PROTECTED)

    def on_chain_start(
        self,
        serialized: Optional[Dict[str, Any]],
        inputs: Dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Any:
        step_name = ""
        if serialized and isinstance(serialized, dict):
            step_name = serialized.get("name") or (serialized.get("id") or ["chain"])[-1]
        if not step_name:
            step_name = kwargs.get("name", "chain")

        sanitized_inputs = self._sanitize_inputs(inputs)
        if self.hide_inputs and isinstance(inputs, dict):
            inputs.clear()
            inputs.update(sanitized_inputs)

        self._runs[str(run_id)] = {
            "run_id": str(run_id),
            "parent_run_id": str(parent_run_id) if parent_run_id else None,
            "step_name": step_name,
            "name": step_name,
            "run_type": "chain",
            "tags": list(tags) if tags else [],
            "metadata": dict(metadata) if metadata else {},
            "start_time": time.time(),
            "start_perf": time.perf_counter(),
            "inputs": sanitized_inputs,
            "outputs": None,
            "latency_ms": None,
            "tokens": None,
            "error": None,
            "error_type": None,
            "status": "running",
        }

    def on_chain_end(
        self,
        outputs: Dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> Any:
        sanitized_outputs = self._sanitize_outputs(outputs)
        if self.hide_outputs and isinstance(outputs, dict):
            outputs.clear()
            outputs.update(sanitized_outputs)

        run = self._runs.get(str(run_id))
        end_perf = time.perf_counter()
        if run:
            run["end_time"] = time.time()
            run["latency_ms"] = round((end_perf - run["start_perf"]) * 1000, 2)
            run["outputs"] = sanitized_outputs
            run["status"] = "success"
        else:
            self._runs[str(run_id)] = {
                "run_id": str(run_id),
                "parent_run_id": str(parent_run_id) if parent_run_id else None,
                "step_name": kwargs.get("name", "chain"),
                "run_type": "chain",
                "end_time": time.time(),
                "latency_ms": 0.0,
                "outputs": sanitized_outputs,
                "status": "success",
            }

    def on_chain_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> Any:
        run = self._runs.get(str(run_id))
        end_perf = time.perf_counter()
        if run:
            run["end_time"] = time.time()
            if "start_perf" in run:
                run["latency_ms"] = round((end_perf - run["start_perf"]) * 1000, 2)
            run["status"] = "error"
            run["error"] = str(error)
            run["error_type"] = type(error).__name__

    def on_llm_start(
        self,
        serialized: Optional[Dict[str, Any]],
        prompts: List[str],
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Any:
        step_name = ""
        if serialized and isinstance(serialized, dict):
            step_name = serialized.get("name") or (serialized.get("id") or ["llm"])[-1]
        if not step_name:
            step_name = kwargs.get("name", "llm")

        if self.hide_inputs:
            sanitized_prompts = [REDACTED_INPUT_PII_PROTECTED for _ in prompts]
            if isinstance(prompts, list):
                for i in range(len(prompts)):
                    prompts[i] = REDACTED_INPUT_PII_PROTECTED
        else:
            sanitized_prompts = list(prompts)

        self._runs[str(run_id)] = {
            "run_id": str(run_id),
            "parent_run_id": str(parent_run_id) if parent_run_id else None,
            "step_name": step_name,
            "name": step_name,
            "run_type": "llm",
            "tags": list(tags) if tags else [],
            "metadata": dict(metadata) if metadata else {},
            "start_time": time.time(),
            "start_perf": time.perf_counter(),
            "inputs": {"prompts": sanitized_prompts},
            "outputs": None,
            "latency_ms": None,
            "tokens": None,
            "error": None,
            "error_type": None,
            "status": "running",
        }

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        tags: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Any:
        end_perf = time.perf_counter()
        token_usage = {}
        if hasattr(response, "llm_output") and isinstance(response.llm_output, dict):
            token_usage = (
                response.llm_output.get("token_usage")
                or response.llm_output.get("usage")
                or {}
            )

        if self.hide_outputs and hasattr(response, "generations") and response.generations:
            for gen_list in response.generations:
                for gen in gen_list:
                    if hasattr(gen, "text"):
                        gen.text = REDACTED_OUTPUT_PII_PROTECTED

        run = self._runs.get(str(run_id))
        if run:
            run["end_time"] = time.time()
            if "start_perf" in run:
                run["latency_ms"] = round((end_perf - run["start_perf"]) * 1000, 2)
            run["tokens"] = token_usage
            run["status"] = "success"
            run["outputs"] = {"generations": REDACTED_OUTPUT_PII_PROTECTED if self.hide_outputs else str(response)}

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        tags: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Any:
        run = self._runs.get(str(run_id))
        end_perf = time.perf_counter()
        if run:
            run["end_time"] = time.time()
            if "start_perf" in run:
                run["latency_ms"] = round((end_perf - run["start_perf"]) * 1000, 2)
            run["status"] = "error"
            run["error"] = str(error)
            run["error_type"] = type(error).__name__


__all__ = [
    "JSONFormatter",
    "get_logger",
    "logger",
    "MetricsCollector",
    "metrics_collector",
    "track_latency",
    "configure_langsmith_redaction",
    "SafeLangSmithCallbackHandler",
    "sanitize_telemetry_payload",
    "REDACTED_INPUT_PII_PROTECTED",
    "REDACTED_OUTPUT_PII_PROTECTED",
    "REDACTED_PII_PROTECTED",
    "REDACTED_CPF",
    "REDACTED_CNPJ",
]
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

import json
import logging
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Generator, Optional

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


__all__ = [
    "JSONFormatter",
    "get_logger",
    "logger",
    "MetricsCollector",
    "metrics_collector",
    "track_latency",
]
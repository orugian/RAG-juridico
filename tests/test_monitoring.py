"""
Testes de unidade para o módulo de monitoramento (app/monitoring.py)
Cenários de produção para Andrade Advogados:
- Formatação estruturada em JSON para auditoria (LGPD e sigilo OAB)
- Captura de metadados nativos via extra={...}
- Truncamento de contratos massivos para evitar log bloat
- Coleta atômica e thread-safe de métricas (requisições, erros, latência, tokens, cache)
- Conformidade estrita com o modelo Pydantic MetricsResponse
- Demonstração visual em CLI quando executado diretamente
"""

import sys
from pathlib import Path

# Permite executar o script diretamente de qualquer diretório
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import json
import logging
import threading
import time
import pytest
from app.models import MetricsResponse
from app.monitoring import (
    JSONFormatter,
    MetricsCollector,
    get_logger,
    metrics_collector,
    track_latency,
)


@pytest.fixture
def collector() -> MetricsCollector:
    """Retorna uma instância limpa do MetricsCollector."""
    c = MetricsCollector()
    c.reset()
    return c


def test_json_formatter_standard_fields():
    """Valida se o JSONFormatter serializa corretamente os campos obrigatórios."""
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="test-logger",
        level=logging.INFO,
        pathname="app/agent.py",
        lineno=42,
        msg="Consulta contratual iniciada",
        args=(),
        exc_info=None,
    )
    formatted = formatter.format(record)
    data = json.loads(formatted)

    assert data["level"] == "INFO"
    assert data["message"] == "Consulta contratual iniciada"
    assert data["logger"] == "test-logger"
    assert "timestamp" in data
    assert data["module"] == "agent"


def test_json_formatter_extra_metadata_and_truncation():
    """Garante que metadados de auditoria (user, thread_id) são capturados e textos gigantes são truncados."""
    formatter = JSONFormatter()
    giant_contract_text = "Cláusula 1ª: " + ("Texto longo de minuta " * 150)  # > 1500 caracteres

    record = logging.LogRecord(
        name="test-logger",
        level=logging.INFO,
        pathname="app/agent.py",
        lineno=55,
        msg="Contrato processado com sucesso",
        args=(),
        exc_info=None,
    )
    # Simula o uso nativo: logger.info(..., extra={"user": "advogado_1", ...})
    record.user = "advogado_1"
    record.thread_id = "contrato-114-2024"
    record.contract_payload = giant_contract_text

    formatted = formatter.format(record)
    data = json.loads(formatted)

    assert data["user"] == "advogado_1"
    assert data["thread_id"] == "contrato-114-2024"
    assert "... [truncated" in data["contract_payload"]
    assert len(data["contract_payload"]) < len(giant_contract_text)


def test_metrics_collector_empty_state(collector: MetricsCollector):
    """Garante cálculos seguros de divisão por zero em estado inicial."""
    res = collector.get_metrics_response()

    assert isinstance(res, MetricsResponse)
    assert res.total_requests == 0
    assert res.total_erros == 0
    assert res.error_rate == "0.0%"
    assert res.avg_latency_ms == 0.0
    assert res.cache_hit_rate == "0.0%"
    assert res.total_input_tokens == 0
    assert res.total_output_tokens == 0
    assert res.token_efficiency == 0.0


def test_metrics_collector_recording(collector: MetricsCollector):
    """Valida o acúmulo de requisições, latências, tokens e cache hits."""
    # 1. Requisição bem-sucedida via LLM
    collector.record_request(
        latency_ms=250.5,
        success=True,
        cache_hit=False,
        cache_miss=True,
        input_tokens=1000,
        output_tokens=200,
    )

    # 2. Requisição resolvida via Cache (latência ultra-rápida 0.0ms)
    collector.record_request(
        latency_ms=0.0,
        success=True,
        cache_hit=True,
        cache_miss=False,
        input_tokens=0,
        output_tokens=0,
    )

    # 3. Requisição com erro
    collector.record_request(
        latency_ms=100.0,
        success=False,
        cache_hit=False,
        cache_miss=False,
        input_tokens=500,
        output_tokens=0,
    )

    res = collector.get_metrics_response()

    assert res.total_requests == 3
    assert res.total_erros == 1
    assert res.error_rate == "33.3%"
    # avg_latency = (250.5 + 0.0 + 100.0) / 3 = 116.83
    assert res.avg_latency_ms == pytest.approx(116.83, rel=1e-2)
    # cache_hit_rate = 1 hit em 2 consultas ao cache = 50.0%
    assert res.cache_hit_rate == "50.0%"
    assert res.total_input_tokens == 1500
    assert res.total_output_tokens == 200
    # token_efficiency = 200 / 1500 = 0.13
    assert res.token_efficiency == 0.13


def test_metrics_collector_thread_safety(collector: MetricsCollector):
    """Verifica que o acúmulo concorrente em threads não gera condições de corrida."""
    num_threads = 10
    requests_per_thread = 50

    def worker():
        for _ in range(requests_per_thread):
            collector.record_request(
                latency_ms=10.0,
                success=True,
                cache_hit=True,
                input_tokens=10,
                output_tokens=2,
            )

    threads = [threading.Thread(target=worker) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    res = collector.get_metrics_response()
    expected_total = num_threads * requests_per_thread

    assert res.total_requests == expected_total
    assert res.total_input_tokens == expected_total * 10
    assert res.total_output_tokens == expected_total * 2
    assert res.total_erros == 0


def test_track_latency_context_manager():
    """Valida o cronômetro do context manager track_latency."""
    with track_latency() as tracker:
        time.sleep(0.05)  # 50 ms

    assert tracker["latency_ms"] >= 45.0


def run_monitoring_demo():
    """Execução demonstrativa em CLI das métricas e logs estruturados."""
    print("\n=== MONITORING & STRUCTURED LOGGING DEMO ===\n")

    demo_collector = MetricsCollector()
    demo_logger = get_logger("demo-andrade")

    # 1. Registro de Log de Auditoria
    print("1. Gerando log estruturado JSON para auditoria:")
    demo_logger.info(
        "Consulta contratual processada",
        extra={
            "user": "advogado_roberto",
            "thread_id": "contrato-849-2024",
            "contract_searched": "Andrade Advogados vs Construtora Rocha",
            "status": "COMPLETED",
        },
    )

    # 2. Simulando requisição 1: LLM Search
    with track_latency() as t1:
        time.sleep(0.04)
    demo_collector.record_request(
        latency_ms=t1["latency_ms"],
        success=True,
        cache_hit=False,
        cache_miss=True,
        input_tokens=1250,
        output_tokens=320,
    )
    print(f"\n2. Requisição 1 registrada (LLM): {t1['latency_ms']}ms, 1250 in / 320 out tokens")

    # 3. Simulando requisição 2: Cache Hit
    with track_latency() as t2:
        time.sleep(0.002)
    demo_collector.record_request(
        latency_ms=t2["latency_ms"],
        success=True,
        cache_hit=True,
        cache_miss=False,
        input_tokens=0,
        output_tokens=0,
    )
    print(f"3. Requisição 2 registrada (Cache Hit): {t2['latency_ms']}ms")

    # 4. Simulando requisição 3: Erro de Timeout
    with track_latency() as t3:
        time.sleep(0.01)
    demo_collector.record_request(
        latency_ms=t3["latency_ms"],
        success=False,
        input_tokens=500,
        output_tokens=0,
    )
    print(f"4. Requisição 3 registrada (Falha): {t3['latency_ms']}ms")

    # 5. Exportando métricas consolidadas (Pydantic MetricsResponse)
    res = demo_collector.get_metrics_response()
    print("\n5. Métricas Consolidadas (Schema MetricsResponse):")
    print(json.dumps(res.model_dump(), indent=2, ensure_ascii=False))
    print()


if __name__ == "__main__":
    run_monitoring_demo()
    print("=" * 60)
    print("Executando testes automatizados com pytest:\n")
    pytest.main([__file__, "-v"])

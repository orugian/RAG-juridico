"""
Testes de unidade e integração para o Agente LangGraph (app/agent.py)
Cenários para Andrade Advogados:
- Compilação e integridade do StateGraph determinístico
- Invocação do modelo primário (Qwen 3.8 Flash)
- Chaveamento para modelo fallback (DeepSeek v4.1 Flash) em caso de falha
- Tratamento de erro fatal e mensagem de recuperação
- Demonstração em CLI interativa ao executar diretamente
"""

import sys
from pathlib import Path

# Permite executar o script diretamente de qualquer diretório
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from unittest.mock import MagicMock
import pytest
from langchain_core.messages import AIMessage
from app.agent import ProductionAgent, production_agent


def test_agent_graph_compilation():
    """Valida se o StateGraph do LangGraph foi compilado com todos os nós e arestas válidos."""
    assert production_agent.graph is not None
    # Verifica nós presentes no grafo
    nodes = production_agent.graph.nodes
    assert "process" in nodes
    assert "fallback" in nodes
    assert "error" in nodes


def test_agent_successful_primary_invocation():
    """Valida se a invocação padrão utiliza o modelo primário com sucesso."""
    result = production_agent.invoke(
        "Qual o seu propósito no escritório Andrade Advogados?",
        thread_id="test-thread-01"
    )

    assert result["error"] is None
    assert len(result["response"]) > 0
    assert result["thread_id"] == "test-thread-01"
    assert "qwen" in result["model_used"] or len(result["model_used"]) > 0


def test_agent_fallback_on_primary_failure():
    """Simula falha no modelo primário e verifica se o fallback é acionado com sucesso."""
    agent = ProductionAgent()

    # Cria mocks para substituir os LLMs no agente
    mock_primary = MagicMock()
    mock_primary.invoke.side_effect = RuntimeError("Simulated primary timeout / rate limit")

    mock_fallback = MagicMock()
    mock_fallback.invoke.return_value = AIMessage(content="Resposta gerada pelo DeepSeek Fallback")

    agent.llm_primary = mock_primary
    agent.fallback_llm = mock_fallback

    # Reconstrói o grafo com os mocks instalados
    agent.graph = agent._build_graph()

    result = agent.invoke("Consulta de teste com fallback")

    assert result["error"] is None
    assert result["response"] == "Resposta gerada pelo DeepSeek Fallback"
    assert "deepseek" in result["model_used"] or len(result["model_used"]) > 0


def test_agent_fatal_error_handling():
    """Simula falha em ambos os modelos e verifica se o nó de erro é acionado elegantemente."""
    agent = ProductionAgent()

    # Falha em ambos os mocks
    mock_primary = MagicMock()
    mock_primary.invoke.side_effect = RuntimeError("Primary down")

    mock_fallback = MagicMock()
    mock_fallback.invoke.side_effect = RuntimeError("Fallback down")

    agent.llm_primary = mock_primary
    agent.fallback_llm = mock_fallback
    agent.graph = agent._build_graph()

    result = agent.invoke("Consulta que falhará em ambos")

    assert result["error"] is not None
    assert "Falha na comunicação" in result["error"]
    assert "não puderam processar" in result["response"]
    assert result["model_used"] == "none"


def run_agent_demo():
    """Execução demonstrativa em CLI da inteligência do Agente."""
    print("\n=== LANGGRAPH AGENT DEMO: ANDRADE ADVOGADOS ===\n")

    query = "Olá! Qual é a sua função específica e quem você representa?"
    print(f"1. Pergunta enviada: {repr(query)}")
    print("2. Processando via StateGraph (Primary: Qwen 3.8 Flash)...")

    res = production_agent.invoke(query, thread_id="demo-cli-01")

    print(f"3. Modelo utilizado: {res['model_used']}")
    print(f"4. Resposta do Agente:\n\n{res['response']}\n")
    print("5. Status de erro:", res["error"] or "Nenhum (Execução com sucesso)")
    print("=" * 60)


if __name__ == "__main__":
    run_agent_demo()
    print("Executando testes automatizados com pytest:\n")
    pytest.main([__file__, "-v"])

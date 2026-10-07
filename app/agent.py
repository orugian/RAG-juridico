"""
LangGraph Production Agent for Andrade Advogados RAG System.

Features:
- Deterministic StateGraph with primary LLM (Qwen 3.8 Flash) and fallback LLM (DeepSeek v4.1 Flash).
- Automatic model fallback on failure, timeout, or rate-limits.
- Strict anti-hallucination and 4-element citation prompt for contract audits (CONTEXT.md).
- End-to-end tracing via LangSmith.
"""

from functools import lru_cache
from typing import Optional
from typing_extensions import Annotated, TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langchain_openai import ChatOpenAI
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from app.telemetry import safe_trace, error_category, assert_callback_boundary

from app.config import get_settings

SYSTEM_PROMPT = """Você é o Assistente Especialista em Contratos do escritório Andrade Advogados.
Sua função exclusiva é atuar como auditor e consultor do acervo contratual da banca jurídica.

DIRETRIZES FUNDAMENTAIS (CONTEXT.MD):
1. FIDELIDADE DOCUMENTAL E ANTI-ALUCINAÇÃO:
   - Responda estritamente com base nos fatos, dados e cláusulas expressas nos contratos.
   - É terminantemente proibido especular, deduzir termos não pactuados ou utilizar conhecimento genérico como se fosse parte do contrato.
   - Se a informação solicitada (cláusula, valor, data ou condição) não estiver localizada, responda categoricamente:
     "A informação solicitada sobre [assunto/termo] não foi localizada nos contratos disponíveis na base de dados do escritório Andrade Advogados."

2. REGRA DOS 4 ELEMENTOS DE CITAÇÃO (OBRIGATÓRIO):
   Sempre que citar uma regra contratual, forneça:
   a) Identificador do Contrato (Nome, número ou proposta).
   b) Partes Qualificadas e seus papéis efetivamente documentados.
   c) Localização Exata (Cláusula, parágrafo, inciso ou anexo).
   d) Transcrição Literal do Trecho-Chave (em destaque ou aspas).

3. PRESERVAÇÃO E PRIVACIDADE DE DADOS (LGPD / SIGILO OAB):
   - Mantenha a integridade de nomes, CPFs, CNPJs e valores econômicos citados nos contratos.
   - Mantenha o tom estritamente formal, técnico, objetivo e confidencial.
"""


class AgentState(TypedDict):
    """
    State for the LangGraph agent, tracking messages and execution metadata.
    """
    messages: Annotated[list[BaseMessage], add_messages]
    error: Optional[str]
    retry_count: int
    model_used: str


class ProductionAgent:
    """
    Production LangGraph agent with:
    - Primary LLM: Qwen 3.8 Flash via OpenRouter
    - Fallback LLM: DeepSeek v4.1 Flash via OpenRouter
    - Deterministic graph routing and error handling
    - LangSmith tracing
    """

    def __init__(self, *, primary=None, fallback=None, settings=None):
        self.settings = settings or get_settings()
        self.llm_primary = primary
        self.fallback_llm = fallback
        self.max_retries = self.settings.max_retries
        self.graph = self._build_graph()

    def _provider(self, fallback=False):
        existing = self.fallback_llm if fallback else self.llm_primary
        if existing is not None:
            assert_callback_boundary(existing)
            return existing
        if not self.settings.provider_policy_approved:
            raise RuntimeError("Provider data policy has not been approved")
        if not self.settings.openai_api_key.get_secret_value():
            raise RuntimeError("Provider credential is not configured")
        client = ChatOpenAI(model=self.settings.fallback_llm_model if fallback else self.settings.primary_llm_model,
                            api_key=self.settings.openai_api_key,
                            base_url=self.settings.openai_base_url, temperature=0,
                            timeout=self.settings.request_timeout_seconds, max_retries=0)
        if fallback:
            self.fallback_llm = client
        else:
            self.llm_primary = client
        return client

    def _build_graph(self):
        """
        Build the deterministic LangGraph state machine.
        """

        def process_message(state: AgentState) -> dict:
            """Process the user query using the primary LLM (Qwen 3.8 Flash)."""
            try:
                prompt_messages = [SystemMessage(content=SYSTEM_PROMPT)] + list(state["messages"])
                response = self._provider().invoke(prompt_messages)

                return {
                    "messages": [response],
                    "model_used": self.settings.primary_llm_model,
                    "error": None,
                }
            except Exception as e:
                return {
                    "error": error_category(e),
                    "retry_count": state.get("retry_count", 0) + 1,
                    "model_used": "",
                }

        def try_fallback(state: AgentState) -> dict:
            """Attempt fallback LLM (DeepSeek v4.1 Flash) when primary fails."""
            try:
                prompt_messages = [SystemMessage(content=SYSTEM_PROMPT)] + list(state["messages"])
                response = self._provider(fallback=True).invoke(prompt_messages)

                return {
                    "messages": [response],
                    "model_used": self.settings.fallback_llm_model,
                    "error": None,
                }
            except Exception as e:
                return {
                    "error": error_category(e),
                    "retry_count": state.get("retry_count", 0) + 1,
                    "model_used": "",
                }

        def handle_error(state: AgentState) -> dict:
            """Handle fatal errors after all LLM attempts have failed."""
            error_msg = f"Falha na comunicação com os modelos de IA: {state.get('error', 'Erro desconhecido')}"
            fallback_message = AIMessage(
                content="Desculpe, os provedores de linguagem não puderam processar a consulta contratual no momento. "
                        "Por favor, tente novamente em alguns instantes."
            )
            return {
                "error": error_msg,
                "messages": [fallback_message],
                "model_used": "none",
            }

        def route_after_process(state: AgentState) -> str:
            """Route from primary processing to success, fallback, or error."""
            if state.get("error") is None:
                return "done"
            if state.get("retry_count", 0) <= self.max_retries:
                return "fallback"
            return "error"

        def route_after_fallback(state: AgentState) -> str:
            """Route from fallback to success or terminal error."""
            if state.get("error") is None:
                return "done"
            return "error"

        # Constrói o StateGraph
        workflow = StateGraph(AgentState)

        # Adiciona os nós de processamento
        workflow.add_node("process", process_message)
        workflow.add_node("fallback", try_fallback)
        workflow.add_node("error", handle_error)

        # Arestas determinísticas
        workflow.add_edge(START, "process")

        workflow.add_conditional_edges(
            "process",
            route_after_process,
            {
                "done": END,
                "fallback": "fallback",
                "error": "error",
            },
        )

        workflow.add_conditional_edges(
            "fallback",
            route_after_fallback,
            {
                "done": END,
                "error": "error",
            },
        )

        workflow.add_edge("error", END)

        return workflow.compile()

    @safe_trace(name="ProductionAgent.invoke", run_type="chain")
    def invoke(self, message: str, thread_id: str = "default") -> dict:
        """
        Invoke the LangGraph agent with a message.
        Returns: {"response": str, "model_used": str, "error": str | None, "thread_id": str}
        """
        assert_callback_boundary(self.graph, self.llm_primary, self.fallback_llm)
        result = self.graph.invoke({
            "messages": [HumanMessage(content=message)],
            "error": None,
            "retry_count": 0,
            "model_used": "",
        })

        last_message = result["messages"][-1] if result.get("messages") else None
        response_content = last_message.content if last_message else ""

        return {
            "response": response_content,
            "model_used": result.get("model_used", ""),
            "error": result.get("error"),
            "thread_id": thread_id,
        }


@lru_cache
def get_production_agent() -> ProductionAgent:
    """Explicit composition; never constructs provider clients at import time."""
    return ProductionAgent()

__all__ = ["AgentState", "ProductionAgent", "get_production_agent", "SYSTEM_PROMPT"]

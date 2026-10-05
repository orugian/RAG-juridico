"""
LangGraph Production Agent for Andrade Advogados RAG System.

Features:
- Deterministic StateGraph with primary LLM (Qwen 3.8 Flash) and fallback LLM (DeepSeek v4.1 Flash).
- Automatic model fallback on failure, timeout, or rate-limits.
- Strict anti-hallucination and 4-element citation prompt for contract audits (CONTEXT.md).
- End-to-end tracing via LangSmith.
"""

import os
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
from langsmith import traceable

from app.config import get_settings

settings = get_settings()

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
   b) Partes Qualificadas (Contratante e Contratada Andrade Advogados).
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

    def __init__(self):
        openai_base_url = os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")

        self.llm_primary = ChatOpenAI(
            model=settings.primary_llm_model,
            api_key=settings.openai_api_key,
            base_url=openai_base_url,
            temperature=0,
            timeout=30,
            max_retries=settings.max_retries,
        )

        self.fallback_llm = ChatOpenAI(
            model=settings.fallback_llm_model,
            api_key=settings.openai_api_key,
            base_url=openai_base_url,
            temperature=0,
            timeout=30,
            max_retries=settings.max_retries,
        )

        self.max_retries = settings.max_retries
        self.graph = self._build_graph()

    def _build_graph(self):
        """
        Build the deterministic LangGraph state machine.
        """

        def process_message(state: AgentState) -> dict:
            """Process the user query using the primary LLM (Qwen 3.8 Flash)."""
            try:
                prompt_messages = [SystemMessage(content=SYSTEM_PROMPT)] + list(state["messages"])
                response = self.llm_primary.invoke(prompt_messages)

                return {
                    "messages": [response],
                    "model_used": settings.primary_llm_model,
                    "error": None,
                }
            except Exception as e:
                return {
                    "error": str(e),
                    "retry_count": state.get("retry_count", 0) + 1,
                    "model_used": "",
                }

        def try_fallback(state: AgentState) -> dict:
            """Attempt fallback LLM (DeepSeek v4.1 Flash) when primary fails."""
            try:
                prompt_messages = [SystemMessage(content=SYSTEM_PROMPT)] + list(state["messages"])
                response = self.fallback_llm.invoke(prompt_messages)

                return {
                    "messages": [response],
                    "model_used": settings.fallback_llm_model,
                    "error": None,
                }
            except Exception as e:
                return {
                    "error": str(e),
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

    @traceable(name="ProductionAgent.invoke", run_type="chain")
    def invoke(self, message: str, thread_id: str = "default") -> dict:
        """
        Invoke the LangGraph agent with a message.
        Returns: {"response": str, "model_used": str, "error": str | None, "thread_id": str}
        """
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


# Instância exportada
production_agent = ProductionAgent()

__all__ = ["AgentState", "ProductionAgent", "production_agent", "SYSTEM_PROMPT"]
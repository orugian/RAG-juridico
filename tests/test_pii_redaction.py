"""
Tests for LangSmith PII Redaction and Safe Observability (Task 10).
Ensures LGPD compliance and OAB professional secrecy:
- configure_langsmith_redaction hides inputs/outputs and sets compliance tags
- SafeLangSmithCallbackHandler intercepts on_chain_start/end and on_llm_start/end
- Contractual payloads (verbatim_text, page_content, text_raw) are sanitized
- Essential analytical telemetry (latencies, token counts, step names, errors) is preserved
- sanitize_telemetry_payload masks CPFs, CNPJs, and sensitive contractual keys
"""

import os
import time
from uuid import uuid4
import pytest
from langchain_core.outputs import LLMResult, Generation

from app.monitoring import (
    configure_langsmith_redaction,
    SafeLangSmithCallbackHandler,
    sanitize_telemetry_payload,
    REDACTED_INPUT_PII_PROTECTED,
    REDACTED_OUTPUT_PII_PROTECTED,
)


def test_configure_langsmith_redaction_defaults():
    """Verify default redaction configuration sets privacy flags and compliance tags."""
    config = configure_langsmith_redaction()

    assert config["hide_inputs"] is True
    assert config["hide_outputs"] is True
    assert config["project_name"] == "AndradeAdvogados"
    assert "privacy_redacted" in config["tags"]
    assert "lgpd_compliant" in config["tags"]

    assert os.environ.get("LANGSMITH_HIDE_INPUTS") == "true"
    assert os.environ.get("LANGCHAIN_HIDE_INPUTS") == "true"
    assert os.environ.get("LANGSMITH_HIDE_OUTPUTS") == "true"
    assert os.environ.get("LANGCHAIN_HIDE_OUTPUTS") == "true"
    assert os.environ.get("LANGCHAIN_PROJECT") == "AndradeAdvogados"


def test_configure_langsmith_redaction_custom():
    """Verify custom project name and toggled redaction parameters."""
    config = configure_langsmith_redaction(
        hide_inputs=False,
        hide_outputs=False,
        project_name="CustomOABProject",
    )

    assert config["hide_inputs"] is False
    assert config["hide_outputs"] is False
    assert config["project_name"] == "CustomOABProject"
    assert os.environ.get("LANGSMITH_HIDE_INPUTS") == "false"
    assert os.environ.get("LANGSMITH_HIDE_OUTPUTS") == "false"
    assert os.environ.get("LANGCHAIN_PROJECT") == "CustomOABProject"


def test_sanitize_telemetry_payload_cpf_and_cnpj():
    """Verify CPF and CNPJ masking in strings while preserving other alphanumeric data."""
    text = (
        "Cliente João Silva (CPF: 123.456.789-01 e unformatted 98765432100) "
        "e Empresa XPTO (CNPJ: 12.345.678/0001-90 e unformatted 11222333000144)"
    )
    sanitized = sanitize_telemetry_payload(text)

    # Raw identifiers must NOT exist in the sanitized text
    assert "123.456.789-01" not in sanitized
    assert "98765432100" not in sanitized
    assert "12.345.678/0001-90" not in sanitized
    assert "11222333000144" not in sanitized

    # Must contain masking tokens
    assert "[REDACTED_CPF]" in sanitized
    assert "[REDACTED_CNPJ]" in sanitized
    assert "Cliente João Silva" in sanitized


def test_sanitize_telemetry_payload_contractual_fields():
    """Verify known contractual keys (verbatim_text, page_content, text_raw) are redacted."""
    payload = {
        "step_name": "retrieve_clauses",
        "verbatim_text": "Cláusula 4ª: O devedor pagará a quantia de R$ 500.000,00...",
        "page_content": "Texto confidencial de minuta de acordo judicial.",
        "text_raw": "Contrato social registrado na Junta Comercial.",
        "metadata": {
            "document_id": "doc-998",
            "client_cpf": "123.456.789-00",
            "page_content": "Cláusula interna restrita",
        },
    }

    sanitized = sanitize_telemetry_payload(payload)

    # Contractual fields replaced with redaction placeholder
    assert sanitized["verbatim_text"] == "[REDACTED_PII_PROTECTED]"
    assert sanitized["page_content"] == "[REDACTED_PII_PROTECTED]"
    assert sanitized["text_raw"] == "[REDACTED_PII_PROTECTED]"
    assert sanitized["metadata"]["page_content"] == "[REDACTED_PII_PROTECTED]"

    # PII inside metadata masked
    assert "123.456.789-00" not in sanitized["metadata"]["client_cpf"]
    assert "[REDACTED_CPF]" in sanitized["metadata"]["client_cpf"]

    # Analytical and non-sensitive fields preserved
    assert sanitized["step_name"] == "retrieve_clauses"
    assert sanitized["metadata"]["document_id"] == "doc-998"


def test_safe_callback_redacts_on_chain_start():
    """Verify SafeLangSmithCallbackHandler redacts contractual inputs on chain start."""
    handler = SafeLangSmithCallbackHandler(hide_inputs=True, hide_outputs=True)
    run_id = uuid4()
    inputs = {
        "verbatim_text": "Cláusula 1ª - Das Obrigações do Contratante",
        "contract_payload": "Texto integral confidencial de contrato de honorários",
        "query": "Qual o prazo de pagamento do cliente com CPF 111.222.333-44?",
        "node_id": "clause_retriever",
    }

    handler.on_chain_start(
        serialized={"name": "clause_extraction_node"},
        inputs=inputs,
        run_id=run_id,
        tags=["production", "oab_confidential"],
        metadata={"model": "qwen3.8"},
    )

    # In-place and internal run inputs must be redacted
    assert inputs["verbatim_text"] == REDACTED_INPUT_PII_PROTECTED
    assert inputs["contract_payload"] == REDACTED_INPUT_PII_PROTECTED
    assert "111.222.333-44" not in inputs["query"]
    assert "[REDACTED_CPF]" in inputs["query"]
    assert inputs["node_id"] == "clause_retriever"

    run = handler.get_run(run_id)
    assert run is not None
    assert run["step_name"] == "clause_extraction_node"
    assert run["inputs"]["verbatim_text"] == REDACTED_INPUT_PII_PROTECTED
    assert run["inputs"]["contract_payload"] == REDACTED_INPUT_PII_PROTECTED
    assert run["status"] == "running"
    assert "oab_confidential" in run["tags"]


def test_safe_callback_redacts_on_chain_end():
    """Verify SafeLangSmithCallbackHandler redacts contractual outputs and records latency."""
    handler = SafeLangSmithCallbackHandler(hide_inputs=True, hide_outputs=True)
    run_id = uuid4()
    inputs = {"node": "analyzer"}
    handler.on_chain_start(serialized={"name": "contract_analyzer"}, inputs=inputs, run_id=run_id)

    time.sleep(0.01)  # Simulate 10ms processing

    outputs = {
        "verbatim_text": "Cláusula de rescisão contratual no valor de 100 mil.",
        "answer": "O contrato prevê multa rescisória para o CPF 999.888.777-66.",
        "status_code": 200,
    }

    handler.on_chain_end(outputs=outputs, run_id=run_id)

    assert outputs["verbatim_text"] == REDACTED_OUTPUT_PII_PROTECTED
    assert outputs["answer"] == REDACTED_OUTPUT_PII_PROTECTED
    assert outputs["status_code"] == 200

    run = handler.get_run(run_id)
    assert run is not None
    assert run["status"] == "success"
    assert run["latency_ms"] is not None
    assert run["latency_ms"] >= 8.0
    assert run["outputs"]["verbatim_text"] == REDACTED_OUTPUT_PII_PROTECTED


def test_safe_callback_preserves_data_when_unredacted():
    """Verify that when hide_inputs=False and hide_outputs=False, payloads are preserved."""
    handler = SafeLangSmithCallbackHandler(hide_inputs=False, hide_outputs=False)
    run_id = uuid4()
    raw_input_text = "Cláusula 1ª - Sem sigilo para ambiente interno de testes."
    inputs = {"verbatim_text": raw_input_text}

    handler.on_chain_start(serialized={"name": "test_node"}, inputs=inputs, run_id=run_id)
    assert inputs["verbatim_text"] == raw_input_text

    raw_output_text = "Resposta do modelo preservada."
    outputs = {"answer": raw_output_text}
    handler.on_chain_end(outputs=outputs, run_id=run_id)
    assert outputs["answer"] == raw_output_text


def test_safe_callback_records_chain_errors():
    """Verify error types and messages are captured with analytical latency."""
    handler = SafeLangSmithCallbackHandler()
    run_id = uuid4()
    inputs = {"step": "parser"}
    handler.on_chain_start(serialized={"name": "failing_node"}, inputs=inputs, run_id=run_id)

    time.sleep(0.005)
    test_error = ValueError("Invalid document encoding encountered")
    handler.on_chain_error(error=test_error, run_id=run_id)

    run = handler.get_run(run_id)
    assert run is not None
    assert run["status"] == "error"
    assert run["error_type"] == "ValueError"
    assert "Invalid document encoding" in run["error"]
    assert run["latency_ms"] is not None
    assert run["latency_ms"] >= 4.0


def test_safe_callback_on_llm_events_and_token_tracking():
    """Verify LLM start/end hooks redact prompts/generations and preserve token usage."""
    handler = SafeLangSmithCallbackHandler(hide_inputs=True, hide_outputs=True)
    run_id = uuid4()
    prompts = [
        "Analise a minuta do contrato do cliente CPF 123.456.789-00 referente à dívida bancária."
    ]

    handler.on_llm_start(
        serialized={"name": "qwen_flash"},
        prompts=prompts,
        run_id=run_id,
        tags=["llm_generation"],
    )

    # Prompt list mutated and run inputs redacted
    assert prompts[0] == REDACTED_INPUT_PII_PROTECTED
    run = handler.get_run(run_id)
    assert run["inputs"]["prompts"][0] == REDACTED_INPUT_PII_PROTECTED

    # Simulate LLM end with token usage
    response = LLMResult(
        generations=[
            [Generation(text="A cláusula de juros moratórios é de 1% ao mês.")]
        ],
        llm_output={
            "token_usage": {
                "prompt_tokens": 150,
                "completion_tokens": 45,
                "total_tokens": 195,
            }
        },
    )

    handler.on_llm_end(response=response, run_id=run_id)

    # Generation text redacted
    assert response.generations[0][0].text == REDACTED_OUTPUT_PII_PROTECTED
    updated_run = handler.get_run(run_id)
    assert updated_run["status"] == "success"
    assert updated_run["tokens"]["total_tokens"] == 195
    assert updated_run["tokens"]["prompt_tokens"] == 150
    assert updated_run["tokens"]["completion_tokens"] == 45
    assert updated_run["latency_ms"] is not None

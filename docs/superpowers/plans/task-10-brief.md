# Task 10 Brief: Observabilidade Segura e Mascaramento Global de PII no LangSmith

## Goal
Implementar utilitários de observabilidade segura e mascaramento global de PII no LangSmith em `app/monitoring.py` com testes em `tests/test_pii_redaction.py`. O objetivo inegociável é assegurar conformidade com a LGPD e o sigilo profissional da OAB, garantindo que `hide_inputs=True` e `hide_outputs=True` sejam aplicados aos nós do LangChain/LangGraph que manipulam texto integral e cláusulas dos contratos, impedindo que dados sensíveis de clientes trafeguem para a nuvem da LangSmith enquanto se preservam métricas vitais (latência, contagem de tokens, nomes de nós e status de erro).

## Files to Modify/Create
- Modify: `app/monitoring.py`
- Create: `tests/test_pii_redaction.py`

## Requirements
1. **Configurador de Mascaramento do LangSmith (`configure_langsmith_redaction`)**:
   `configure_langsmith_redaction(hide_inputs: bool = True, hide_outputs: bool = True, project_name: Optional[str] = None) -> Dict[str, Any]`
   - Configura parâmetros de privacidade:
     - Retorna dicionário contendo as chaves `hide_inputs: True`, `hide_outputs: True`, `project_name: str` e tags de conformidade (`["privacy_redacted", "lgpd_compliant"]`).
     - Atualiza variáveis de ambiente ou configurações do LangSmith caso estejam ativas (`LANGCHAIN_PROJECT`, etc.).
2. **Callback Seguro de Mascaramento (`SafeLangSmithCallbackHandler`)**:
   - Classe herdando de `BaseCallbackHandler` (`langchain_core.callbacks.base.BaseCallbackHandler`).
   - Intercepta eventos de início de cadeia/LLM (`on_chain_start`, `on_llm_start`) e término (`on_chain_end`, `on_llm_end`).
   - Se `hide_inputs` for `True`, higieniza inputs substituindo payloads contratuais por `[REDACTED_INPUT_PII_PROTECTED]`.
   - Se `hide_outputs` for `True`, higieniza outputs substituindo payloads gerados/recuperados por `[REDACTED_OUTPUT_PII_PROTECTED]`.
   - Preserva metadados analíticos essenciais: latência, tokens, step names, timestamps, error types.
3. **Função Utilitária de Sanitização (`sanitize_telemetry_payload`)**:
   - `sanitize_telemetry_payload(data: Any) -> Any`: remove ou mascara CPFs, CNPJs e campos conhecidos de conteúdo contratual (`verbatim_text`, `page_content`, `text_raw`) de qualquer payload antes de emissão de telemetria.

## Testing & TDD
- Escrever `tests/test_pii_redaction.py` cobrindo:
  - `configure_langsmith_redaction()` retornando `hide_inputs=True` e `hide_outputs=True`.
  - `SafeLangSmithCallbackHandler` higienizando inputs e outputs contendo texto contratual em eventos `on_chain_start` e `on_chain_end`.
  - Preservação de metadados não sensíveis (tempos de execução, nomes de nós, erros).
  - Sanitização de `sanitize_telemetry_payload` mascarando campos `verbatim_text` e `page_content`.
- TDD Red: executar teste antes da implementação e verificar falha.
- TDD Green: implementar código em `app/monitoring.py` e passar todos os testes.
- Regressão global: `uv run pytest` deve passar 100% verde em todo o repositório.
- Commit: `git add app/monitoring.py tests/test_pii_redaction.py; git commit -m "feat(monitoring): configurar mascaramento global de inputs/outputs para proteger PII no LangSmith"`.
- Gravar relatório em `docs/superpowers/plans/task-10-report.md`.

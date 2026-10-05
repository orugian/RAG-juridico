# Task 2 Brief: Seletor da Amostra de Referência Estratificada (Golden Batch)

## Goal
Implementar o seletor determinístico da amostra de referência (Golden Batch) em `app/ingestion/reference_sample.py` e testes associados em `tests/test_reference_sample.py`. A amostra calibrará os parsers nas etapas seguintes (DOCX, LibreOffice legado e PDF/OCR) cobrindo todos os formatos suportados, extratos de risco e casos patológicos identificados na auditoria (como IDs 5991 e 5707).

## Files to Create
- Create: `app/ingestion/reference_sample.py`
- Create: `tests/test_reference_sample.py`

## Requirements
1. **Função Principal**:
   `select_reference_sample(records: List[Dict[str, Any]], target_count: int = 40, seed: int = 42) -> List[Dict[str, Any]]`
   - Suporta tanto registros simples (com chaves no topo: `route`, `subformat`, `is_empty_risk`, etc.) quanto o schema aninhado de `data/curation/curation.jsonl` (ex.: `record['file']['parse_route']`, `record['file']['detected_format']`).
2. **Priorização Obrigatória (Casos de Borda e Risco)**:
   - Casos com IDs específicos de risco (ex.: `mfiles_id == 5991` - arquivo vazio/risco de texto nulo; `mfiles_id == 5707` - documento com track changes) DEVEM ser obrigatoriamente incluídos na amostra final.
   - Registros com flags explícitas de risco (`is_empty_risk=True`, `has_track_changes=True`, `has_comments=True`) devem ser incluídos prioritariamente.
3. **Estratificação por Rota e Formato**:
   - Garantir representatividade dos formatos disponíveis entre os registros elegíveis (`decision == 'include'` prioritariamente, com fallback ou inclusão opcional de quarentena/review se especificado):
     - `docx`: documentos normais e com numeração/tabelas.
     - `libreoffice`: subtipos `ole_doc`, `rtf`, `wordperfect`.
     - `pdf`: subtipos digital/nativo e escaneado (`is_scanned=True`).
4. **Determinismo e Estabilidade**:
   - Para um mesmo conjunto de entrada e mesma seed/target_count, o retorno DEVE ser 100% determinístico e reproduzível (ordenação estável por `mfiles_id` ou critério semântico).
5. **Utilitários de Suporte**:
   - `load_curated_records(curation_path: Path | str) -> List[Dict[str, Any]]`: leitura de `curation.jsonl`.
   - `save_reference_sample(sample: List[Dict[str, Any]], output_path: Path | str) -> None`: exportação em formato JSONL ou JSON.
   - `get_sample_distribution(sample: List[Dict[str, Any]]) -> Dict[str, Any]`: estatísticas de distribuição por rota, formato e riscos para relatórios de conformidade.

## Testing & TDD
- Escrever `tests/test_reference_sample.py` cobrindo:
  - Inclusão mandatória dos casos 5991 e 5707 mesmo com `target_count` reduzido.
  - Cobertura de múltiplos formatos (`docx`, `ole_doc`, `rtf`, `pdf` escaneado e digital).
  - Determinismo absoluto em chamadas repetidas.
  - Compatibilidade com schema plano e aninhado (`curation.jsonl`).
  - Função `get_sample_distribution` e roundtrip de carga/salvamento.
- TDD Red: executar teste antes da implementação e verificar falha.
- TDD Green: implementar `app/ingestion/reference_sample.py` e passar todos os testes.
- Regressão global: `uv run pytest` deve passar sem falhas em todo o repositório.
- Commit: `git add app/ingestion/reference_sample.py tests/test_reference_sample.py; git commit -m "feat(ingestion): implementar amostragem estratificada para calibracao dos parsers"`
- Gravar relatório em `docs/superpowers/plans/task-2-report.md`.

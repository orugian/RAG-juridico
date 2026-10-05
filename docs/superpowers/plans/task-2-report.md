# Relatório de Conclusão - Tarefa 2: Seletor da Amostra de Referência Estratificada (Golden Batch)

## 1. Visão Geral
- **Objetivo**: Implementar o seletor determinístico da amostra de referência (Golden Batch) em `app/ingestion/reference_sample.py` e testes associados em `tests/test_reference_sample.py`, calibrando os parsers para DOCX, LibreOffice legado e PDF/OCR com cobertura de formatos raros, extratos de risco e casos patológicos identificados na auditoria (como IDs 5991, 5707 e 5829).
- **Branch**: `feat/data-prep-pipeline`
- **Commit**: `c87fe40` (`feat(ingestion): implementar amostragem estratificada para calibracao dos parsers`)
- **Status**: Concluído com sucesso. Suíte de testes 100% verde (168 testes no total, 11 novos testes dedicados).

---

## 2. Arquivos Criados e Implementações

### `app/ingestion/reference_sample.py`
1. **Normalização de Schemas Heterogêneos (`_extract_record_meta`)**:
   - Compatibilidade bidirecional com schema plano (chaves no topo: `route`, `subformat`, `is_empty_risk`, etc.) e schema aninhado do pipeline de curadoria (`data/curation/curation.jsonl`, ex.: `file.parse_route`, `file.detected_format`, `flags`).
   - Resolução canônica de subformatos (`doc`/`ole2` -> `ole_doc`, `wpd` -> `wordperfect`, `docx`, `rtf`, `pdf`, etc.) e rotas (`docx`, `libreoffice`, `pdf`, `unsupported`).

2. **Priorização Obrigatória de Casos Patológicos**:
   - `DEFAULT_MANDATORY_RISK_IDS = {5991, 5707, 5829}`:
     - **ID 5991**: DOCX sem texto `w:t` e sem mídia (risco de texto nulo / chunk vazio).
     - **ID 5707**: DOCX com 149 inserções e 147 exclusões de track changes (exige adjudicação).
     - **ID 5829**: DOCX com revisões e alterações controladas.
   - Casos com IDs mandatórios são obrigatoriamente incluídos mesmo com `target_count` reduzido e independentemente de restrições de decisão.

3. **Estratificação por Rota e Formato**:
   - Divisão em estratos funcionais:
     - `docx`: `docx:standard` e `docx:complex` (com numeração e/ou tabelas).
     - `libreoffice`: subtipos `ole_doc`, `rtf`, `wordperfect`.
     - `pdf`: subtipos digital/nativo (`pdf:digital`) e escaneado (`pdf:scanned`).
   - Algoritmo de amostragem em 4 fases:
     - **Fase 1**: Inclusão de casos de risco mandatórios.
     - **Fase 2**: Garantia de cobertura mínima universal para todas as rotas e formatos/estratos presentes no pool elegível (impedindo que formatos raros como `wordperfect` com 1 documento ou `rtf` sejam suprimidos por formatos majoritários).
     - **Fase 3**: Priorização de registros com sinalizadores explícitos de risco (`is_empty_risk`, `has_track_changes`, `has_comments`, `is_scanned`).
     - **Fase 4**: Preenchimento das vagas remanescentes via *round-robin* balanceado entre todos os estratos.

4. **Determinismo e Estabilidade**:
   - Ordenação e desempate determinísticos por chave canônica `(mfiles_id, original_index)`.
   - Garantia de resultados 100% reproduzíveis entre chamadas repetidas com mesma seed e parâmetros.

5. **Utilitários de Suporte**:
   - `load_curated_records(curation_path: Path | str) -> List[Dict[str, Any]]`: leitura robusta de arquivos JSONL ou arrays JSON.
   - `save_reference_sample(sample: List[Dict[str, Any]], output_path: Path | str) -> None`: exportação em formato `.jsonl` ou `.json` com criação automática de diretórios parentes.
   - `get_sample_distribution(sample: List[Dict[str, Any]]) -> Dict[str, Any]`: estatísticas detalhadas de conformidade por rota (`by_route`), formato (`by_format`), decisão (`by_decision`) e riscos (`by_risk`: vazios, revisões, comentários, escaneados, casos mandatórios).

---

### `tests/test_reference_sample.py`
Suíte abrangente com 11 testes unitários:
1. `test_reference_sample_stratification`: Validação da especificação do brief com amostra reduzida e presença obrigatória de 5991 e 5707.
2. `test_mandatory_risk_cases_with_reduced_target_count`: Verificação da priorização estrita de casos mandatórios quando `target_count` é 2 ou 1.
3. `test_format_and_route_coverage`: Garantia de cobertura simultânea de todas as rotas (`docx`, `libreoffice`, `pdf`) e formatos (`docx`, `ole_doc`, `rtf`, `wordperfect`, `pdf`).
4. `test_priority_risk_flags`: Priorização de documentos com flags de risco explícitas (`is_empty_risk`, `has_track_changes`, `has_comments`) sobre itens padrão.
5. `test_absolute_determinism`: Verificação de 100% de estabilidade e identidade de saída entre execuções repetidas.
6. `test_nested_curation_schema_compatibility`: Validação da extração e amostragem correta sobre schemas aninhados idênticos ao `curation.jsonl`.
7. `test_real_curation_file_sample_selection`: Execução real sobre `data/curation/curation.jsonl` com `target_count=40`, confirmando inclusão de 5991, 5707, 5829 e representação dos 5 formatos disponíveis no corpus real (incluindo o documento único em `wordperfect`).
8. `test_get_sample_distribution`: Cálculo correto de métricas e distribuições estatísticas.
9. `test_save_and_load_roundtrip_jsonl`: Roundtrip de gravação e leitura em formato JSONL.
10. `test_save_and_load_roundtrip_json`: Roundtrip de gravação e leitura em formato JSON array.
11. `test_edge_cases`: Tratamento de listas vazias, `target_count <= 0`, contagens superiores ao pool e fallback gradual para fila de revisão (`review`).

---

## 3. Ciclo TDD e Verificação de Testes

1. **Fase Vermelha (Red)**:
   - Execução: `uv run pytest tests/test_reference_sample.py`
   - Resultado: **FALHA** (`ModuleNotFoundError: No module named 'app.ingestion.reference_sample'`).

2. **Fase Verde (Green)**:
   - Execução: `uv run pytest tests/test_reference_sample.py -v`
   - Resultado: **11 passed in 0.43s**.

3. **Regressão Global**:
   - Execução: `uv run pytest`
   - Resultado: **168 passed in 14.98s** (zero regressões em todo o projeto, englobando agente, cache, curadoria, monitoramento, segurança, schemas intermediários e amostragem de referência).

# Task 0 Brief: Hardening da Curadoria e Preservação de Anotações

## Goal
Corrigir a preservação de anotações humanas no `qa_sample.csv` para que reexecuções de `write_outputs()` em `app/ingestion/curation.py` não sobrescrevam colunas preenchidas por humanos (`decisao_final`, `categoria_final`, `revisado_por`, `observacao`), similar ao que já é feito para `review_queue.csv`.
Além disso, ampliar a geração de `doc_key` em `app/ingestion/curation.py` para contemplar metadados e hashes de arquivos secundários se existirem, e garantir que a cobertura de quase-duplicatas não silencie erros de identificadores.

## Files to Touch
- Modify: `app/ingestion/curation.py:535-610` e `:230-250`
- Modify: `app/ingestion/near_dup.py:70-80`
- Test: `tests/test_curation_hardening.py`

## Requirements
1. Em `curation.py`:
   - Em `write_outputs()`:
     - Carregar anotações prévias também de `out_dir / QA_FILE` via `_previous_human_input()`.
     - Ao escrever `tmp[QA_FILE]`, passar os dados humanos preservados para cada linha de amostra (`human_qa.get((r["mfiles_id"], r["mfiles_version"]))`), assim como é feito na linha 592 para o `REVIEW_FILE`.
   - Em `_base_row()` ou onde `doc_key` é construído:
     - Garantir que `doc_key` incorpore todos os hashes de arquivos (`files`) se houver mais de um arquivo, além da versão de regras `RULES_VERSION`.
2. Em `near_dup.py`:
   - Em `same_instrument()`:
     - Tratar com rigor o caso de ausência de partes: se `not a.parties and not b.parties`, não presumir imediatamente igualdade se a similaridade de shingles não for alta o suficiente, evitando falsos positivos entre minutas não preenchidas de clientes distintos.
3. Testes:
   - Adicionar o teste `test_qa_sample_preserves_human_annotations_across_reruns` em `tests/test_curation_hardening.py`.
   - Garantir que toda a suíte de curadoria existente (`tests/test_curation.py` e `tests/test_curation_hardening.py`) continue 100% verde.

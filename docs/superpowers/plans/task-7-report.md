# Relatório de Conclusão - Tarefa 7: Reconciliação Documental (M-Files vs Texto) e Portão de Quarentena

## 1. Visão Geral
- **Objetivo**: Implementar o motor de reconciliação documental e o orquestrador unificado de parsing em `app/ingestion/parsing_pipeline.py` com sua suíte de testes em `tests/test_parsing_pipeline.py`. O pipeline confronta os metadados cadastrais externos do M-Files contra o `ContractMetadata` extraído do texto real dos contratos. Divergências críticas de titularidade ou partes enviam o documento para o portão de quarentena (`status='review_metadata_mismatch'`), garantindo que apenas dados auditados e 100% fidedignos avancem para indexação automática no RAG.
- **Branch**: `feat/data-prep-pipeline`
- **Commit**: `2c365e8` (`feat(ingestion): implementar reconciliador M-Files vs Texto e portao de quarentena de metadados`)
- **Status**: Concluído com sucesso. Suíte global 100% verde (245 testes aprovados no total, sendo 16 novos testes dedicados ao orquestrador e reconciliador).

---

## 2. Arquivos Criados e Implementações

### `app/ingestion/parsing_pipeline.py`
1. **Reconciliador Documental e Portão de Quarentena (`reconcile_document`)**:
   - `reconcile_document(mfiles_record: Dict[str, Any], parsed_doc: ParsedDocument, quarantine_on_no_parties: bool = True) -> ParsedDocument`:
     - Suporta registros M-Files tanto em formato plano (`cliente`, `class_name`, `title`) quanto com propriedades aninhadas (`record['properties']['Cliente']`), incluindo valores encapsulados em dicionários (`DisplayValue`).
     - **Regra de Discrepância de Cliente (`DISCREPANCY_CLIENT_MISMATCH`)**:
       - Quando o M-Files informa um cliente e nenhuma das partes do contrato (`parsed_doc.metadata.parties`) corresponder ao cliente informado por CNPJ, substring direta, razão social normalizada ou sobreposição de tokens não genéricos:
         - Registra a flag `"DISCREPANCY_CLIENT_MISMATCH"` em `parsed_doc.metadata.mfiles_divergence_flags`.
         - Altera `parsed_doc.status = "review_metadata_mismatch"` (quarentena ativa).
     - **Regra de Refinamento de Tipo de Instrumento (`FLAG_INSTRUMENT_TYPE_REFINED`)**:
       - Quando o M-Files categoriza genericamente como "Contrato" ou "Documento", mas o texto real extraído pelo parser revela especificamente um tipo derivado ("Aditivo", "Distrato", "Acordo", "Locação", "Honorários", etc.):
         - Registra a flag `"FLAG_INSTRUMENT_TYPE_REFINED"` em `parsed_doc.metadata.mfiles_divergence_flags`.
         - A verdade do texto prevalece (`parsed_doc.metadata.instrument_type` mantido) e o documento não é barrado se o cliente for compatível.
     - **Regra de Ausência de Partes (`FLAG_NO_PARTIES_FOUND`)**:
       - Se nenhuma parte for encontrada no texto em instrumento bilateral/multilateral (excluindo instrumentos unilaterais como Declarações, Procurações ou Certidões):
         - Registra a flag `"FLAG_NO_PARTIES_FOUND"`.
         - Encaminha o documento para quarentena (`status="review_metadata_mismatch"`) quando `quarantine_on_no_parties=True`.
     - Preservação de falhas: se o documento já apresentava `status="failed"`, o status é rigorosamente mantido.

2. **Matching Semântico e Cadastral (`_is_client_match`)**:
   - Casamento estrito por CNPJ/CPF limpo (`clean_identifier`), confrontando contra CNPJ informado nas propriedades do M-Files ou contido no literal do cliente.
   - Normalização textual (`_normalize_name`): remoção de acentos via decomposição NFKD, eliminação de pontuação e stripping cirúrgico de sufixos societários (`LTDA`, `S/A`, `S.A.`, `EIRELI`, `ME`, `EPP`, `Companhia`).
   - Casamento por subconjunto e intersecção de tokens significativos, expurgando termos societários genéricos como "banco", "comercio", "industria", "participacoes", "investimentos", prevenindo falsos positivos.

3. **Orquestrador de Parsing Unificado (`parse_single_document`)**:
   - `parse_single_document(file_path: Path | str, mfiles_record: Optional[Dict[str, Any]] = None, doc_id: Optional[int] = None, doc_version: int = 1, quarantine_on_no_parties: bool = True) -> ParsedDocument`:
     - Roteamento por extensão:
       - `.docx`: invoca `extract_docx_blocks` de `app.ingestion.docx_parser`.
       - `.doc`, `.rtf`, `.wpd`: invoca `extract_legacy_blocks` de `app.ingestion.legacy_parser`.
       - `.pdf`: invoca `extract_pdf_blocks` de `app.ingestion.pdf_parser`.
       - Extensões desconhecidas: rejeição graciosa.
     - Extrai metadados reais via `extract_contract_metadata(blocks)` de `app.ingestion.metadata_extractor`.
     - Calcula sha256 real do arquivo físico.
     - Cria instância canônica `ParsedDocument` e submete ao `reconcile_document`.
     - Tratamento defensivo universal: qualquer erro fatal (arquivo ausente, tamanho zero, arquivo corrompido, formato inválido) é interceptado, retornando com segurança `ParsedDocument` com `status="failed"`, `error_message` explicativo e `blocks=[]`.

4. **Pipeline em Lote (`run_parsing_pipeline`)**:
   - `run_parsing_pipeline(records: List[Dict[str, Any]], raw_dir: Path | str, quarantine_on_no_parties: bool = True) -> List[ParsedDocument]`:
     - Processa múltiplos registros de manifest ou curadoria (`data/raw/manifest.jsonl` ou `curation.py`).
     - Resolve dinamicamente os caminhos físicos a partir de chaves `file_path`, `file.path`, `files[0].path` ou diretórios `files/<mfiles_id>/`.
     - Retorna a lista agregada de documentos parseados, reconciliados ou falhados.

---

## 3. Testes Criados em `tests/test_parsing_pipeline.py`

Suíte completa com 16 testes cobrindo todas as rotas e regras de negócio:
1. `test_reconciler_flags_client_mismatch_and_quarantines`: M-Files apontando Banco Santander e texto contendo Alfa e Beta -> quarentena `status="review_metadata_mismatch"` com flag `DISCREPANCY_CLIENT_MISMATCH`.
2. `test_reconciler_client_match_preserves_success_flat_and_nested`: validação de sucesso com cliente alinhado em formato plano e formato de propriedades aninhadas com variações societárias (S.A. vs S/A).
3. `test_reconciler_client_match_via_cnpj`: cliente reconciliado via CNPJ idêntico mesmo com divergência de razão social fantasia.
4. `test_reconciler_instrument_type_refinement`: M-Files categorizado genericamente como "Contrato" refinado pelo texto para "Aditivo" (`FLAG_INSTRUMENT_TYPE_REFINED`), mantendo `status="success"`.
5. `test_reconciler_no_parties_in_bilateral_contract_flags_and_quarantines`: contrato bilateral sem partes identificadas gera `FLAG_NO_PARTIES_FOUND` e quarentena.
6. `test_reconciler_unilateral_instrument_without_parties_no_quarantine`: instrumento unilateral (Declaração) sem partes não aciona quarentena nem flag de falta de partes.
7. `test_reconciler_preserves_failed_status`: integridade de status `failed` preservada sem sobreposição por regras de reconciliação.
8. `test_reconciler_no_parties_quarantine_disabled`: flag `FLAG_NO_PARTIES_FOUND` registrada sem barrar o documento quando `quarantine_on_no_parties=False`.
9. `test_reconciler_mfiles_display_value_and_no_client`: suporte a dicionários `DisplayValue` e ausência de cliente no M-Files sem geração de divergência espúria.
10. `test_parse_single_document_docx`: orquestração end-to-end de `.docx` com extração de blocos, cálculo de sha256 e reconciliação.
11. `test_parse_single_document_pdf`: orquestração end-to-end de `.pdf` com extração digital e reconciliação.
12. `test_parse_single_document_legacy_route`: roteamento de formatos legados (`.doc`, `.rtf`, `.wpd`) para `extract_legacy_blocks`.
13. `test_parse_single_document_corrupted_file_defensive_handling`: arquivo corrompido tratado defensivamente gerando `status="failed"`, `blocks=[]` e `error_message`.
14. `test_parse_single_document_missing_file_defensive_handling`: arquivo inexistente gerando `status="failed"`.
15. `test_parse_single_document_unsupported_extension`: arquivo com extensão desconhecida gerando `status="failed"`.
16. `test_run_parsing_pipeline_batch`: processamento em lote com arquivos heterogêneos (sucesso, quarentena por mismatch e falha de parsing).

---

## 4. Ciclo TDD e Verificação de Regressão

1. **Fase Vermelha (Red)**:
   - Execução: `uv run pytest tests/test_parsing_pipeline.py`
   - Resultado: **FALHA** (`ModuleNotFoundError: No module named 'app.ingestion.parsing_pipeline'`).

2. **Fase Verde (Green)**:
   - Implementação de `app/ingestion/parsing_pipeline.py`.
   - Execução: `uv run pytest tests/test_parsing_pipeline.py -v`
   - Resultado: **16 passed in 0.70s**.

3. **Verificação de Regressão Global**:
   - Execução: `uv run pytest`
   - Resultado: **245 passed in 17.47s** (229 testes pré-existentes mantidos com 100% de aprovação + 16 novos testes; zero regressões).

---

## 5. Resumo de Commits
- Commit: `2c365e8`
- Mensagem: `feat(ingestion): implementar reconciliador M-Files vs Texto e portao de quarentena de metadados`
- Arquivos comitados:
  - `app/ingestion/parsing_pipeline.py`
  - `tests/test_parsing_pipeline.py`

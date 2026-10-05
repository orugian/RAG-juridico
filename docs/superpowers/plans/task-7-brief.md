# Task 7 Brief: Reconciliação Documental (M-Files vs Texto) e Portão de Quarentena

## Goal
Implementar o motor de reconciliação documental e orquestrador de parsing em `app/ingestion/parsing_pipeline.py` com testes em `tests/test_parsing_pipeline.py`. O pipeline confronta os metadados cadastrais externos do M-Files contra o `ContractMetadata` extraído do texto real dos contratos. Divergências críticas de titularidade ou partes enviam o documento para o portão de quarentena (`status='review_metadata_mismatch'`), garantindo que apenas dados 100% fidedignos e auditados avancem para indexação automática no RAG.

## Files to Create
- Create: `app/ingestion/parsing_pipeline.py`
- Create: `tests/test_parsing_pipeline.py`

## Requirements
1. **Reconciliador Documental (`reconcile_document`)**:
   `reconcile_document(mfiles_record: Dict[str, Any], parsed_doc: ParsedDocument) -> ParsedDocument`
   - Suporta `mfiles_record` tanto em formato plano (`cliente`, `class_name`, `title`) quanto com propriedades aninhadas (`record['properties']['Cliente']`).
   - **Regra de Discrepância de Cliente (`DISCREPANCY_CLIENT_MISMATCH`)**:
     - Se o M-Files informar um cliente e nenhuma das partes do contrato (`parsed_doc.metadata.parties`) corresponder ao cliente informado (por substring, razão social normalizada ou CNPJ):
       - Adiciona flag `"DISCREPANCY_CLIENT_MISMATCH"` em `parsed_doc.metadata.mfiles_divergence_flags`.
       - Altera `parsed_doc.status = "review_metadata_mismatch"`.
   - **Regra de Refinamento de Tipo de Instrumento (`FLAG_INSTRUMENT_TYPE_REFINED`)**:
     - Se o M-Files categorizar genericamente como "Contrato" ou "Documento", mas o texto real indicar especificamente "Aditivo", "Distrato", "Acordo" ou "Locação":
       - Registra flag `"FLAG_INSTRUMENT_TYPE_REFINED"` em `parsed_doc.metadata.mfiles_divergence_flags`.
       - A verdade do texto prevalece.
   - **Regra de Ausência de Partes (`FLAG_NO_PARTIES_FOUND`)**:
     - Se nenhuma parte for encontrada no texto em instrumento bilateral/multilateral:
       - Registra `"FLAG_NO_PARTIES_FOUND"` e encaminha para quarentena se configurado.
2. **Orquestrador de Parsing Unificado (`parse_single_document`)**:
   `parse_single_document(file_path: Path | str, mfiles_record: Dict[str, Any], doc_id: Optional[int] = None, doc_version: int = 1) -> ParsedDocument`
   - Identifica a extensão e rota adequada:
     - `.docx`: invoca `extract_docx_blocks` de `app.ingestion.docx_parser`.
     - `.doc`, `.rtf`, `.wpd` (formatos legados): invoca `extract_legacy_blocks` de `app.ingestion.legacy_parser`.
     - `.pdf`: invoca `extract_pdf_blocks` de `app.ingestion.pdf_parser`.
   - Extrai os metadados reais via `extract_contract_metadata(blocks)` de `app.ingestion.metadata_extractor`.
   - Constrói a instância canônica `ParsedDocument` com cálculo do sha256 do arquivo.
   - Submete ao `reconcile_document(mfiles_record, parsed_doc)`.
   - Trata exceções em bloco seguro: se houver falha fatal (arquivo corrompido, vazio, etc.), retorna `ParsedDocument` com `status="failed"`, `error_message=str(e)` e `blocks=[]`.
3. **Pipeline em Lote (`run_parsing_pipeline`)**:
   `run_parsing_pipeline(records: List[Dict[str, Any]], raw_dir: Path | str) -> List[ParsedDocument]`
   - Processa múltiplos registros de curadoria/manifest e retorna a lista de documentos parseados e reconciliados.

## Testing & TDD
- Escrever `tests/test_parsing_pipeline.py` cobrindo:
  - Divergência de cliente: M-Files indicando cliente X e texto contendo partes Y e Z -> quarentena `status="review_metadata_mismatch"` com flag `DISCREPANCY_CLIENT_MISMATCH`.
  - Concordância de cliente: M-Files e texto alinhados -> `status="success"`.
  - Refinamento de tipo de instrumento: M-Files "Contrato" e texto "Termo Aditivo" -> `FLAG_INSTRUMENT_TYPE_REFINED`.
  - Orquestração de parsing por rota (.docx, .doc legado, .pdf) com extração de metadados e reconciliação.
  - Tratamento defensivo de falhas de parsing: arquivo corrompido ou vazio gerando documento com `status="failed"`.
- TDD Red: executar teste antes da implementação e verificar falha.
- TDD Green: implementar `app/ingestion/parsing_pipeline.py` e passar todos os testes.
- Regressão global: `uv run pytest` deve passar 100% verde em todo o repositório.
- Commit: `git add app/ingestion/parsing_pipeline.py tests/test_parsing_pipeline.py; git commit -m "feat(ingestion): implementar reconciliador M-Files vs Texto e portao de quarentena de metadados"`.
- Gravar relatório em `docs/superpowers/plans/task-7-report.md`.

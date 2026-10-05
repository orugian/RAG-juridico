# Task 1 Brief: Schema Intermediário Canônico e Entidades Jurídicas Estruturadas

## Goal
Implementar o contrato de dados canônico em `app/ingestion/schemas.py` utilizando Pydantic v2, definindo a modelagem estrita para blocos de documentos (`DocumentBlock`), metadados e partes contratuais (`ContractParty`, `PartyRole`, `ContractMetadata`) e o agregador de documento parseado (`ParsedDocument`).

## Files to Create
- Create: `app/ingestion/schemas.py`
- Create: `tests/test_schemas.py`

## Requirements
1. `PartyRole`: Enum string contendo `CONTRATANTE`, `CONTRATADA`, `LOCADOR`, `LOCATARIO`, `VENDEDOR`, `COMPRADOR`, `ANUENTE`, `PARTE_GENERICA`.
2. `ContractParty`: Modelo com:
   - `name`: str
   - `role`: PartyRole = PartyRole.PARTE_GENERICA
   - `clean_identifier`: Optional[str] = None (CNPJ ou CPF limpo, somente dígitos)
   - `raw_identifier`: Optional[str] = None (literal original)
   - `is_law_firm`: bool = False (True se for Andrade Advogados)
3. `ContractMetadata`: Modelo com:
   - `formal_title`: str (epígrafe/título real extraído)
   - `instrument_type`: str (Honorários, Prestação de Serviços, Acordo, Locação, Aditivo, Distrato, etc.)
   - `subject_area`: Optional[str] = None (Matéria jurídica temática / assunto)
   - `parties`: List[ContractParty] = []
   - `execution_date`: Optional[str] = None
   - `object_summary`: Optional[str] = None
   - `mfiles_divergence_flags`: List[str] = []
4. `BlockType` e `HierarchyLevel`: Enums com `TITLE`, `PREAMBLE`, `CLAUSE`, `PARAGRAPH`, `ITEM`, `SUBITEM`, `TABLE`, `ANNEX`, `SIGNATURE`.
5. `UncertaintyFlag`: Enum com `TRACK_CHANGES_PRESENT`, `LOW_CONFIDENCE_OCR`, `UNRESOLVED_NUMBERING`, `CORRUPTED_TABLE`, `HAS_COMMENTS`.
6. `DocumentBlock`:
   - `block_id`: str
   - `doc_id`: int
   - `doc_version`: int
   - `block_type`: BlockType
   - `hierarchy_level`: HierarchyLevel
   - `hierarchy_label`: Optional[str] = None (ex.: "Cláusula 4ª", "§ 2º")
   - `parent_clause_id`: Optional[str] = None
   - `order_index`: int
   - `text_raw`: str (texto literal fiel à prova)
   - `text_search`: str (texto limpo/normalizado para indexação léxica)
   - `table_metadata`: Optional[Dict[str, Any]] = None
   - `spans`: List[Dict[str, int]] = []
   - `uncertainty_flags`: List[UncertaintyFlag] = []
7. `ParsedDocument`:
   - `doc_id`: int
   - `doc_version`: int
   - `file_path`: str
   - `file_hash`: str
   - `parser_name`: str
   - `parser_version`: str
   - `metadata`: ContractMetadata
   - `blocks`: List[DocumentBlock]
   - `status`: str = "success" ("success" | "review_metadata_mismatch" | "failed")
   - `error_message`: Optional[str] = None
   - Validador: se `status == 'success'`, `blocks` não pode ser vazio.

## Testing & TDD
- Escrever `tests/test_schemas.py` cobrindo criação válida, invariantes, validação de partes com `clean_identifier` e rejeição de documento de status `success` com blocos vazios.
- TDD Red: executar teste antes de criar `app/ingestion/schemas.py` e verificar falha.
- TDD Green: implementar código e rodar teste garantindo aprovação.
- Commit: `git add app/ingestion/schemas.py tests/test_schemas.py; git commit -m "feat(ingestion): implementar schema canonico DocumentBlock e entidades ContractParty e ContractMetadata"`
- Gravar relatório em `docs/superpowers/plans/task-1-report.md`.

# Relatório de Conclusão - Tarefa 1: Schema Intermediário Canônico e Entidades Jurídicas Estruturadas

## 1. Visão Geral
- **Objetivo**: Implementar o contrato de dados canônico intermediário em `app/ingestion/schemas.py` com Pydantic v2, definindo a modelagem jurídica estrita para entidades contratuais (`ContractParty`, `PartyRole`, `ContractMetadata`), blocos de documentos estruturados (`DocumentBlock`, `BlockType`, `HierarchyLevel`, `UncertaintyFlag`) e o agregador de documento parseado (`ParsedDocument`).
- **Branch**: `feat/data-prep-pipeline`
- **Commit**: `2b7e686` (`feat(ingestion): implementar schema canonico DocumentBlock e entidades ContractParty e ContractMetadata`)
- **Status**: Concluído com sucesso. Suíte de testes 100% verde (157 testes no total, 10 novos testes dedicados).

---

## 2. Arquivos Criados e Implementações

### `app/ingestion/schemas.py`
1. **`PartyRole` (Enum[str])**:
   - Papéis contratuais canônicos: `CONTRATANTE`, `CONTRATADA`, `LOCADOR`, `LOCATARIO`, `VENDEDOR`, `COMPRADOR`, `ANUENTE`, `PARTE_GENERICA`.
   - Implementação de `_missing_` para resolução resiliente insensível a maiúsculas/minúsculas e compatibilidade de apelidos (ex.: `"parte"` e `"parte_generica"` mapeados para `PARTE_GENERICA`).

2. **`ContractParty` (BaseModel)**:
   - Campos: `name` (str), `role` (PartyRole com default `PARTE_GENERICA`), `clean_identifier` (Optional[str]), `raw_identifier` (Optional[str]), `is_law_firm` (bool com default `False`).
   - Validador rigoroso em `clean_identifier`: assegura que, quando informado, contenha exclusivamente dígitos numéricos (rejeitando pontuações de CNPJ/CPF, caracteres alfanuméricos e espaços internos).

3. **`ContractMetadata` (BaseModel)**:
   - Campos: `formal_title` (str), `instrument_type` (str), `subject_area` (Optional[str]), `parties` (List[ContractParty]), `execution_date` (Optional[str]), `object_summary` (Optional[str]), `mfiles_divergence_flags` (List[str]).

4. **`BlockType` e `HierarchyLevel` (Enum[str])**:
   - Elementos estruturais canônicos: `TITLE`, `PREAMBLE`, `CLAUSE`, `PARAGRAPH`, `ITEM`, `SUBITEM`, `TABLE`, `ANNEX`, `SIGNATURE`.
   - Implementação de `_missing_` para aceitação de variantes em caixa alta ou baixa.

5. **`UncertaintyFlag` (Enum[str])**:
   - Sinalizadores de incerteza e degradação: `TRACK_CHANGES_PRESENT`, `LOW_CONFIDENCE_OCR`, `UNRESOLVED_NUMBERING`, `CORRUPTED_TABLE`, `HAS_COMMENTS`.

6. **`DocumentBlock` (BaseModel)**:
   - Campos: `block_id` (str), `doc_id` (int), `doc_version` (int), `block_type` (BlockType), `hierarchy_level` (HierarchyLevel), `hierarchy_label` (Optional[str]), `parent_clause_id` (Optional[str]), `order_index` (int), `text_raw` (str, literal fiel à prova jurídica), `text_search` (str, normalizado para busca léxica), `table_metadata` (Optional[Dict[str, Any]]), `spans` (List[Dict[str, int]]), `uncertainty_flags` (List[UncertaintyFlag]).

7. **`ParsedDocument` (BaseModel)**:
   - Agregador com campos: `doc_id`, `doc_version`, `file_path`, `file_hash`, `parser_name`, `parser_version`, `metadata`, `blocks`, `status` ("success" | "review_metadata_mismatch" | "failed"), `error_message`.
   - Invariante estrutural via `@model_validator(mode="after")`: documentos com `status == 'success'` exigem obrigatoriamente `blocks` não vazio, levantando `ValueError` caso contrário. Status de falha ou divergência admitem lista de blocos vazia.

### `tests/test_schemas.py`
Suíte com 10 testes unitários abrangentes:
1. `test_party_role_enum`: Validação dos membros e resolução flexível de strings.
2. `test_contract_party_valid`: Validação de criação de partes com CNPJ, CPF, escritório de advocacia e valores padrão.
3. `test_contract_party_clean_identifier_validation`: Rejeição estrita de pontuação, letras ou espaços em `clean_identifier`.
4. `test_contract_metadata_validation`: Validação de metadados completos e com valores padrão mínimos.
5. `test_block_enums`: Validação da completude dos enums de tipo de bloco, nível hierárquico e incerteza.
6. `test_document_block_validation`: Criação de blocos com fidelidade textual e metadados estruturais.
7. `test_parsed_document_success_valid`: Criação válida de documento com sucesso e blocos preenchidos.
8. `test_parsed_document_success_with_empty_blocks_rejected`: Verificação da rejeição do invariante de documento com sucesso sem blocos.
9. `test_parsed_document_non_success_allows_empty_blocks`: Aceitação de blocos vazios para status `failed` e `review_metadata_mismatch`.
10. `test_parsed_document_roundtrip_serialization`: Serialização e desserialização completa via `model_dump_json()` e `model_validate_json()`.

---

## 3. Ciclo TDD e Verificação de Testes

1. **Fase Vermelha (Red)**:
   - Execução: `uv run pytest tests/test_schemas.py`
   - Resultado: **FALHA** (`ModuleNotFoundError: No module named 'app.ingestion.schemas'`).
2. **Fase Verde (Green)**:
   - Execução: `uv run pytest tests/test_schemas.py`
   - Resultado: **10 passed in 0.10s**.
3. **Regressão Global**:
   - Execução: `uv run pytest`
   - Resultado: **157 passed in 18.31s** (zero regressões em todo o projeto, contemplando agente, cache, curadoria, monitoramento, segurança e schemas).

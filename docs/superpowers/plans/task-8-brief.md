# Task 8 Brief: Chunker Hierárquico Jurídico com Cabeçalho e `verbatim_text` Segregados

## Goal
Implementar o motor de chunking hierárquico jurídico em `app/ingestion/chunker.py` com testes em `tests/test_chunker.py`. O chunker converte instâncias de `ParsedDocument` aprovadas (`status == 'success'`) em objetos `Document` da LangChain, construindo um cabeçalho contextual sintético para potencializar a recuperação no `page_content`, enquanto segrega estritamente o texto literal exato da cláusula em `metadata["verbatim_text"]` para viabilizar a prova jurídica e a citação limpa exigida pela Regra dos 4 Elementos.

## Files to Create
- Create: `app/ingestion/chunker.py`
- Create: `tests/test_chunker.py`

## Requirements
1. **Filtro de Status**:
   - `create_legal_chunks(documents: List[ParsedDocument]) -> List[Document]`:
   - Processa estritamente documentos com `doc.status == "success"`. Documentos em quarentena (`review_metadata_mismatch`) ou falhos (`failed`) são ignorados com log de aviso.
2. **Estratégia de Chunking por Cláusula**:
   - Agrupa blocos subordinados (`PARAGRAPH`, `ITEM`, `SUBITEM`, `TABLE`) à sua respectiva cláusula mãe (`parent_clause_id` ou ordem contígua).
   - Preâmbulo (`PREAMBLE`) e Título (`TITLE`) formam um chunk contextual inaugural do contrato.
   - Fecho e assinaturas (`SIGNATURE`) formam o chunk final de datação e signatários.
3. **Formatação do Cabeçalho Contextual em `page_content`**:
   - Cada chunk recebe um cabeçalho de busca padronizado no início de `page_content`:
     ```text
     [Instrumento: {instrument_type} - {formal_title}]
     {Linhas com Partes e Papéis, ex.: Contratada: Andrade Advogados (11222333000144)}
     [Localização: {hierarchy_label ou tipo de bloco}]

     {Corpo da cláusula e parágrafos subordinados}
     ```
4. **Metadados Rastreáveis e Segregação do `verbatim_text`**:
   - No dicionário `metadata` de cada `Document`:
     - `chunk_id`: identificador determinístico estável, ex.: `doc_{doc_id}_v{doc_version}_chunk_{idx}`.
     - `doc_id`: int
     - `doc_version`: int
     - `file_path`: str
     - `formal_title`: str
     - `instrument_type`: str
     - `subject_area`: Optional[str]
     - `clean_identifiers`: Lista com todos os CNPJs e CPFs limpos das partes (`clean_identifier`), utilizada para o pré-filtro léxico determinístico.
     - `hierarchy_label`: Optional[str] (ex.: "Cláusula 3ª", "Parágrafo Primeiro").
     - `block_type`: str (ex.: "clause", "preamble", "table").
     - **`verbatim_text`**: O texto literal original e puro da cláusula (`text_raw`), SEM qualquer cabeçalho sintético de busca. Esta é a fonte de verdade inegociável para citação na resposta RAG.
     - `uncertainty_flags`: Lista de strings com quaisquer incertezas registradas nos blocos componentes.
5. **Robustez**:
   - Chunking idempotente, determinístico e compatível com documentos pequenos, tabelas isoladas e cláusulas extensas.

## Testing & TDD
- Escrever `tests/test_chunker.py` cobrindo:
  - Preservação estrita de `verbatim_text` no metadata sem poluição do cabeçalho sintético de busca.
  - Injeção das partes, papéis e CNPJs no cabeçalho textual do `page_content`.
  - Coleta correta de `clean_identifiers` para pré-filtro.
  - Agrupamento de parágrafos subordinados (`§ 1º`, `§ 2º`) sob a cláusula mãe.
  - Rejeição de documentos que não tenham `status == 'success'` (quarentena e failed).
  - Roundtrip e integridade dos atributos do LangChain `Document`.
- TDD Red: executar teste antes da implementação e verificar falha.
- TDD Green: implementar `app/ingestion/chunker.py` e passar todos os testes.
- Regressão global: `uv run pytest` deve passar 100% verde em todo o repositório.
- Commit: `git add app/ingestion/chunker.py tests/test_chunker.py; git commit -m "feat(ingestion): implementar chunker com cabecalho contextual e verbatim_text para citacao"`.
- Gravar relatório em `docs/superpowers/plans/task-8-report.md`.

# Relatório de Conclusão - Tarefa 8: Chunker Hierárquico Jurídico com Cabeçalho e `verbatim_text` Segregados

## 1. Visão Geral
- **Objetivo**: Implementar o motor de chunking hierárquico jurídico em `app/ingestion/chunker.py` e sua suíte de testes em `tests/test_chunker.py`. O módulo converte instâncias de `ParsedDocument` aprovadas (`status == 'success'`) em objetos `Document` da LangChain, construindo um cabeçalho contextual sintético para potencializar a recuperação no `page_content`, enquanto segrega estritamente o texto literal original da cláusula em `metadata["verbatim_text"]` para viabilizar a prova jurídica e a citação limpa exigida pela Regra dos 4 Elementos.
- **Branch**: `feat/data-prep-pipeline`
- **Commit**: `26c6118` (`feat(ingestion): implementar chunker com cabecalho contextual e verbatim_text para citacao`)
- **Status**: Concluído com sucesso. Suíte global 100% verde (256 testes aprovados no total, sendo 11 novos testes dedicados ao chunker).

---

## 2. Arquivos Criados e Implementações

### `app/ingestion/chunker.py`
1. **Filtro Estrito de Status Documental (`create_legal_chunks`)**:
   - Processa estritamente documentos com `doc.status == "success"`.
   - Documentos em quarentena (`review_metadata_mismatch`) ou com falha (`failed`) são sumariamente ignorados com emissão de warning no log do sistema (`logger.warning`), garantindo que apenas dados 100% aprovados avancem para a camada de indexação e recuperação.
   - Trata defensivamente coleções vazias ou documentos com blocos ausentes.

2. **Agrupamento Hierárquico por Cláusula (`_group_blocks`)**:
   - **Chunk Inaugural Contextual**: Título (`TITLE`) e Preâmbulo (`PREAMBLE`), juntamente com parágrafos introdutórios que antecedem a primeira cláusula, são consolidados em um chunk inaugural inicial com `block_type="preamble"`.
   - **Agrupamento por Cláusula Mãe**: Cláusulas (`CLAUSE`) iniciam chunks autônomos. Blocos subordinados (`PARAGRAPH`, `ITEM`, `SUBITEM`, `TABLE`) são agrupados à sua respectiva cláusula por referência explícita (`parent_clause_id`) ou por ordem de contiguidade posicional.
   - **Chunk Final de Assinaturas e Fecho**: Blocos de assinatura (`SIGNATURE`) são agregados em um chunk final de datação e signatários com `block_type="signature"`.
   - **Blocos Autônomos**: Tabelas isoladas (`TABLE`) e Anexos (`ANNEX`) formam chunks próprios preservando seus metadados de localização e tipo.

3. **Formatação do Cabeçalho Contextual de Busca (`page_content`)**:
   - Cada chunk recebe um cabeçalho padronizado no início de `page_content`:
     ```text
     [Instrumento: {instrument_type} - {formal_title}]
     {Linhas com Partes e Papéis, ex.: Contratada: Andrade Advogados (11222333000144)}
     [Localização: {hierarchy_label ou tipo de bloco}]

     {Corpo da cláusula e parágrafos subordinados}
     ```
   - Normaliza os papéis contratuais (`PartyRole` ou string) para apresentação formal em português (ex.: Contratante, Contratada, Locador, Locatário).
   - Resolve dinamicamente o rótulo de localização: utiliza o `hierarchy_label` (ex.: "Cláusula 3ª", "§ 1º", "Anexo I") ou converte o tipo do bloco em rótulo jurídico ("Preâmbulo", "Assinaturas", "Tabela", "Cláusula").

4. **Segregação Estrita de `metadata["verbatim_text"]` (Regra dos 4 Elementos)**:
   - O campo `metadata["verbatim_text"]` contém **estritamente o texto literal original** da cláusula/bloco (`text_raw`), totalmente livre de qualquer cabeçalho de busca sintético.
   - Garante que a resposta RAG execute citações literais invioláveis, sem contaminação do texto probatório pelo prompt ou pelos identificadores sintéticos de indexação.

5. **Metadados Rastreáveis e Pré-Filtro Léxico**:
   - `chunk_id`: identificador determinístico estável no padrão `doc_{doc_id}_v{doc_version}_chunk_{idx}`.
   - `doc_id` (int), `doc_version` (int), `file_path` (str), `formal_title` (str), `instrument_type` (str), `subject_area` (Optional[str]).
   - `clean_identifiers`: lista contendo todos os CNPJs e CPFs limpos das partes (somente dígitos numéricos deduplicados), fundamental para o pré-filtro léxico determinístico no retriever híbrido.
   - `hierarchy_label`: rótulo estrutural da cláusula (ex.: "Cláusula 1ª", "Anexo I") ou `None`.
   - `block_type`: tipo primário canônico em string minúscula (ex.: "clause", "preamble", "signature", "table", "annex").
   - `uncertainty_flags`: agregação deduplicada de flags de incerteza (ex.: `track_changes_present`, `low_confidence_ocr`, `has_comments`) presentes nos blocos componentes do chunk.

---

## 3. Testes Criados em `tests/test_chunker.py`

Suíte abrangente com 11 testes cobrindo todas as especificações e cenários de borda:
1. `test_chunker_injects_reconciled_header_and_preserves_verbatim_text`: validação da injeção do cabeçalho sintético em `page_content` e segregação estrita de `verbatim_text` no metadata sem poluição.
2. `test_chunker_groups_subordinate_paragraphs_under_parent_clause`: agrupamento de cláusula com múltiplos parágrafos subordinados (`§ 1º`, `§ 2º`) e itens, via `parent_clause_id` e contiguidade.
3. `test_chunker_inaugural_chunk_preamble_and_title`: criação do chunk inaugural contextual unindo `TITLE` e `PREAMBLE`.
4. `test_chunker_signature_final_chunk`: consolidação de múltiplos blocos de assinatura (`SIGNATURE`) no chunk final de datação e signatários.
5. `test_chunker_filters_non_success_documents`: rejeição estrita de documentos com status `review_metadata_mismatch` (quarentena) e `failed`, garantindo a emissão de logs de warning.
6. `test_chunker_collects_clean_identifiers_and_uncertainty_flags`: coleta de CNPJs limpos e deduplicação, além da agregação de flags de incerteza dos blocos em strings.
7. `test_chunker_handles_standalone_table_and_annex`: integridade e classificação correta de tabelas autônomas e blocos de anexo.
8. `test_chunker_empty_input`: tratamento defensivo para lista vazia de documentos.
9. `test_chunker_metadata_schema_adherence_and_types`: verificação de presença e conformidade de tipo para todas as 12 chaves obrigatórias do dicionário de metadados.
10. `test_chunker_batch_multiple_documents`: processamento em lote de múltiplos documentos garantindo indexação sequencial de `chunk_id` por documento.
11. `test_chunker_parties_without_identifiers`: formatação de cabeçalho quando partes não possuem CNPJ/CPF informado.

---

## 4. Ciclo TDD e Verificação de Regressão

1. **Fase Vermelha (Red)**:
   - Execução: `uv run pytest tests/test_chunker.py`
   - Resultado: **FALHA** (`ModuleNotFoundError: No module named 'app.ingestion.chunker'`).

2. **Fase Verde (Green)**:
   - Implementação de `app/ingestion/chunker.py`.
   - Execução: `uv run pytest tests/test_chunker.py -v`
   - Resultado: **11 passed in 0.19s**.

3. **Verificação de Regressão Global**:
   - Execução: `uv run pytest`
   - Resultado: **256 passed in 15.29s** (245 testes pré-existentes mantidos com 100% de aprovação + 11 novos testes; zero regressões em todo o repositório).

---

## 5. Resumo de Commits
- Commit: `26c6118`
- Mensagem: `feat(ingestion): implementar chunker com cabecalho contextual e verbatim_text para citacao`
- Arquivos comitados:
  - `app/ingestion/chunker.py`
  - `tests/test_chunker.py`

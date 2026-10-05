# Relatório de Conclusão - Tarefa 9: Indexação Híbrida Atômica (ChromaDB + BM25) com Pré-Filtro de CNPJ

## 1. Visão Geral
- **Objetivo**: Implementar o motor de indexação sincronizada atômica em `app/retrieval/indexer.py` e o recuperador híbrido especializado `LegalHybridRetriever` em `app/retrieval/hybrid.py`, acompanhados da suíte de testes unitários e de integração em `tests/test_hybrid_retriever.py`. O sistema gera e persiste atomicamente sob a mesma chave `corpus_generation_id` os índices vetorial (ChromaDB) e léxico (BM25 serializado via `joblib`), aplicando pré-filtragem determinística por CNPJ/CPF quando detectados na query para erradicar alucinações e contaminação cruzada entre clientes.
- **Branch**: `feat/data-prep-pipeline`
- **Commit**: `9d70385` (`feat(retrieval): implementar indexacao sincrona Chroma+BM25 e LegalHybridRetriever com pre-filtro de CNPJ`)
- **Status**: Concluído com sucesso. Suíte global 100% verde (269 testes aprovados no total, sendo 13 novos testes dedicados ao módulo de recuperação).

---

## 2. Arquivos Criados e Implementações

### `app/retrieval/indexer.py`
1. **`DeterministicHashEmbeddings` (LangChain `Embeddings`)**:
   - Gerador determinístico de embeddings locais e leves baseado em SHA-256 sobre 1-grams e 2-grams de tokens normalizados para vetores unitários de 128 dimensões.
   - Permite execução 100% offline, rápida (< 3s) e totalmente reprodutível em testes automatizados, sem consumir APIs externas nem exigir download de modelos pesados.

2. **Indexação Sincronizada Atômica (`build_and_save_hybrid_index`)**:
   - `build_and_save_hybrid_index(chunks, generation_id, base_dir, embeddings=None, k=4) -> LegalHybridRetriever`
   - Armazena no diretório `{base_dir}/{generation_id}`:
     - Vetores: ChromaDB em `{base_dir}/{generation_id}/chroma`.
     - BM25: serializado via `joblib.dump` em `{base_dir}/{generation_id}/bm25.joblib`.
   - **Garantia Atômica**: valida e assegura que todos os chunks possuam `chunk_id` em seus metadados, indexando sob o mesmo ID exato em ambos os índices.
   - **Sanitização de Metadados para ChromaDB**: omite listas vazias em metadados repassados ao ChromaDB (pois o Chroma rejeita listas vazias com `ValueError`), preservando os chunks e metadados originais intactos no BM25 e no mapa canônico em memória.

3. **Carregamento e Persistência (`load_hybrid_retriever`)**:
   - `load_hybrid_retriever(generation_id, base_dir, embeddings=None, k=4) -> LegalHybridRetriever`
   - Valida a integridade dos artefatos persistidos (`chroma` e `bm25.joblib`), recarregando o índice BM25 via `joblib.load` e o vetor Chroma persistido, reconstruindo o `LegalHybridRetriever` de forma idêntica.

### `app/retrieval/hybrid.py`
1. **Detecção e Extração Canônica de Identificadores (`extract_clean_identifiers_from_query`)**:
   - Utiliza expressões regulares com lookarounds para capturar tanto máscaras formatadas quanto dígitos numéricos puros de:
     - CNPJ (14 dígitos): `(?<!\d)(\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2})(?!\d)`
     - CPF (11 dígitos): `(?<!\d)(\d{3}\.?\d{3}\.?\d{3}-?\d{2})(?!\d)`
   - Limpa pontuações e valida comprimento exato de dígitos, retornando lista deduplicada preservando ordem. Não confunde números de leis (ex.: Lei 11.101/2005) ou valores com CPFs/CNPJs.

2. **`LegalHybridRetriever` (LangChain `BaseRetriever`)**:
   - Herda diretamente de `langchain_core.retrievers.BaseRetriever`, integrando suporte síncrono (`invoke`) e assíncrono (`ainvoke`).
   - **Pré-filtro Determinístico de Cliente**:
     - Se detectado CNPJ/CPF na query, filtra estritamente os chunks elegíveis onde `clean_identifier` pertence a `metadata["clean_identifiers"]`.
     - Se nenhum chunk corresponder, retorna `[]` (isolamento absoluto, impedindo contaminação entre clientes).
     - Se houver múltiplos chunks elegíveis do cliente, executa ranking BM25 e Chroma restrito ao subconjunto elegível.
   - **Fusão Híbrida (RRF - Reciprocal Rank Fusion)**:
     - Quando a consulta é temática e genérica (sem CNPJ/CPF), combina rankings do BM25 e do ChromaDB usando $RRF(d) = \sum \frac{1}{60 + \text{rank}(d) + 1}$.
     - **Desempate Determinístico**: ordenação primária por pontuação decrescente e secundária alfabética por `chunk_id`, garantindo 100% de estabilidade de ranking em execuções repetidas.
   - **Preservação de Metadados e `verbatim_text`**:
     - Utiliza `doc_map` canônico para garantir que todo `Document` retornado preserve integralmente `chunk_id`, `verbatim_text`, `clean_identifiers`, `formal_title`, `instrument_type`, etc.

### `app/retrieval/__init__.py`
- Exporta publicamente `LegalHybridRetriever`, `DeterministicHashEmbeddings`, `build_and_save_hybrid_index`, `load_hybrid_retriever` e `extract_clean_identifiers_from_query`.

---

## 3. Testes Criados em `tests/test_hybrid_retriever.py`

Suíte completa com 13 testes cobrindo todas as especificações e cenários de borda:
1. `test_extract_clean_identifiers_from_query`: detecção e extração de CNPJs e CPFs formatados e brutos, rejeição de leis/datas.
2. `test_build_and_save_hybrid_index_creates_files`: criação atômica dos diretórios ChromaDB (`chroma.sqlite3`) e arquivo BM25 (`bm25.joblib`).
3. `test_exact_cnpj_prefilter_prevents_client_cross_contamination`: consulta com CNPJ formatado retorna exclusivamente chunks do cliente, prevenindo contaminação com terceiros.
4. `test_exact_cpf_prefilter_returns_only_matching_debtor`: consulta com CPF formatado retorna estritamente a dívida do devedor correspondente.
5. `test_nonexistent_identifier_returns_empty_results`: consulta com CNPJ não cadastrado retorna lista vazia (zero contaminação).
6. `test_generic_query_executes_hybrid_rrf_with_deterministic_tie_breaking`: consulta temática sem CNPJ executa busca híbrida (BM25 + Chroma) com desempate determinístico por `chunk_id`.
7. `test_metadata_and_verbatim_text_preservation`: integridade total de `chunk_id`, `verbatim_text`, `formal_title` e `instrument_type`.
8. `test_load_hybrid_retriever_from_disk`: recarregamento de índices persistidos e execução idêntica de buscas.
9. `test_load_hybrid_retriever_nonexistent_directory_raises`: validação de `FileNotFoundError` para diretórios inexistentes.
10. `test_build_index_with_empty_chunks_raises_value_error`: rejeição defensiva para listas vazias de chunks.
11. `test_chunk_missing_chunk_id_raises_value_error`: garantia contratual de que todo chunk deve possuir `chunk_id`.
12. `test_async_ainvoke_supported`: verificação de execução assíncrona com `ainvoke`.
13. `test_multiple_clean_identifiers_in_query`: consulta com múltiplos CNPJs recupera partes envolvidas sem vazar contratos de terceiros não citados.

---

## 4. Ciclo TDD e Verificação de Regressão

1. **Fase Vermelha (Red)**:
   - Execução: `uv run pytest tests/test_hybrid_retriever.py`
   - Resultado: **FALHA** (`ModuleNotFoundError: No module named 'app.retrieval'`).

2. **Fase Verde (Green)**:
   - Implementação de `app/retrieval/__init__.py`, `app/retrieval/indexer.py` e `app/retrieval/hybrid.py`.
   - Execução: `uv run pytest tests/test_hybrid_retriever.py -v`
   - Resultado: **13 passed in 2.96s**.

3. **Verificação de Regressão Global**:
   - Execução: `uv run pytest`
   - Resultado: **269 passed in 22.32s** (256 testes pré-existentes mantidos com 100% de aprovação + 13 novos testes; zero regressões em todo o repositório).

---

## 5. Resumo de Commits
- Commit: `9d70385`
- Mensagem: `feat(retrieval): implementar indexacao sincrona Chroma+BM25 e LegalHybridRetriever com pre-filtro de CNPJ`
- Arquivos comitados:
  - `pyproject.toml`
  - `uv.lock`
  - `app/retrieval/__init__.py`
  - `app/retrieval/indexer.py`
  - `app/retrieval/hybrid.py`
  - `tests/test_hybrid_retriever.py`

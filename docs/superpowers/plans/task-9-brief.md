# Task 9 Brief: Indexação Híbrida Atômica (ChromaDB + BM25) com Pré-Filtro de CNPJ

## Goal
Implementar o motor de indexação sincronizada e atômica (`app/retrieval/indexer.py`) e o recuperador híbrido especializado (`app/retrieval/hybrid.py`) com testes em `tests/test_hybrid_retriever.py`. O sistema gera e persiste sob a mesma chave `corpus_generation_id` os índices vetorial (ChromaDB) e léxico (BM25 com joblib), aplicando pré-filtragem determinística por CNPJ/CPF quando detectado na query para erradicar alucinações e contaminação entre clientes.

## Dependencies
- `chromadb`
- `rank-bm25`
- `joblib`
- `langchain-core`
- `langchain-community`

## Files to Create
- Create: `app/retrieval/indexer.py`
- Create: `app/retrieval/hybrid.py`
- Create: `tests/test_hybrid_retriever.py`

## Requirements
1. **Indexação Sincronizada Atômica (`indexer.py`)**:
   `build_and_save_hybrid_index(chunks: List[Document], generation_id: str, base_dir: Path | str, embeddings: Optional[Embeddings] = None) -> LegalHybridRetriever`
   - Armazena no diretório `{base_dir}/{generation_id}`:
     - Vetores: ChromaDB em `{base_dir}/{generation_id}/chroma`.
     - BM25: serializado via `joblib.dump` em `{base_dir}/{generation_id}/bm25.joblib`.
   - Garantia atômica: todos os chunks devem ser indexados sob o mesmo `chunk_id` (`id=chunk.metadata["chunk_id"]`) em ambos os índices.
   - Fornecer suporte a embeddings customizados ou fallback offline determinístico para permitir execução rápida e 100% isolada nos testes automatizados sem rede.
2. **`LegalHybridRetriever` (`hybrid.py`)**:
   - Classe herdando de `BaseRetriever` da `langchain_core.retrievers`.
   - **Detecção de CNPJ/CPF na Query**:
     - Regex para identificar máscara ou dígitos de CNPJ (`\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}`) e CPF (`\d{3}\.?\d{3}\.?\d{3}-?\d{2}`).
     - Extrai os dígitos limpos (`clean_identifier`).
   - **Pré-filtro Determinístico**:
     - Se detectado CNPJ/CPF na pergunta, filtra os chunks elegíveis que contenham o respectivo identificador em `metadata["clean_identifiers"]`.
     - Executa a busca sobre esse subconjunto filtrado, garantindo 100% de precisão de isolamento de cliente e eliminando contaminação entre contratos de diferentes partes.
   - **Fusão Híbrida (RRF - Reciprocal Rank Fusion)**:
     - Quando não houver CNPJ na query, combina os rankings do BM25 e do ChromaDB com desempate determinístico por `chunk_id`.
   - **Preservação de Metadados**:
     - Todo `Document` retornado preserva `page_content`, `metadata["chunk_id"]`, `metadata["verbatim_text"]`, `metadata["formal_title"]`, etc.
3. **Carregamento e Persistência**:
   - `load_hybrid_retriever(generation_id: str, base_dir: Path | str, embeddings: Optional[Embeddings] = None) -> LegalHybridRetriever`: permite carregar índices previamente gerados em disco.

## Testing & TDD
- Escrever `tests/test_hybrid_retriever.py` cobrindo:
  - Criação de índice híbrido com `build_and_save_hybrid_index`.
  - Pré-filtro determinístico com consulta contendo CNPJ formatado: retorno exclusivo do contrato correspondente.
  - Pré-filtro com CPF formatado.
  - Consulta temática genérica (sem CNPJ) executando busca híbrida (BM25 + Chroma).
  - Preservação intacta de `verbatim_text` e `chunk_id` em todos os resultados.
  - Carregamento de índice persistido via `load_hybrid_retriever`.
- TDD Red: executar teste antes da implementação e verificar falha.
- TDD Green: implementar `indexer.py` e `hybrid.py` e passar todos os testes.
- Regressão global: `uv run pytest` deve passar 100% verde em todo o repositório.
- Commit: `git add app/retrieval/ tests/test_hybrid_retriever.py; git commit -m "feat(retrieval): implementar indexacao sincrona Chroma+BM25 e LegalHybridRetriever com pre-filtro de CNPJ"`.
- Gravar relatório em `docs/superpowers/plans/task-9-report.md`.

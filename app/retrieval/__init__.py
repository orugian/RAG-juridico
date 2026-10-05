"""
Retrieval Module for Production Legal RAG Pipeline.

Provides atomic hybrid indexing (ChromaDB dense vectors + BM25 sparse lexical)
and LegalHybridRetriever with deterministic client CNPJ/CPF isolation.
"""

from app.retrieval.hybrid import (
    LegalHybridRetriever,
    extract_clean_identifiers_from_query,
)
from app.retrieval.indexer import (
    DeterministicHashEmbeddings,
    build_and_save_hybrid_index,
    load_hybrid_retriever,
)

__all__ = [
    "LegalHybridRetriever",
    "extract_clean_identifiers_from_query",
    "DeterministicHashEmbeddings",
    "build_and_save_hybrid_index",
    "load_hybrid_retriever",
]

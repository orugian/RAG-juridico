"""
Legal Hybrid Retriever with Deterministic CNPJ/CPF Pre-Filtering.

Combines BM25 lexical search with ChromaDB dense vector retrieval using
Reciprocal Rank Fusion (RRF) with deterministic tie-breaking by chunk_id.
Enforces strict customer isolation by detecting CNPJ or CPF in the user query
and pre-filtering chunks before ranking, preventing cross-client data contamination.
"""

import logging
import re
from typing import Any, Dict, List, Optional

from langchain_community.retrievers import BM25Retriever
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict, Field

logger = logging.getLogger(__name__)

# CNPJ: 14 digits, optional standard punctuation (xx.xxx.xxx/xxxx-xx)
_CNPJ_QUERY_PATTERN = re.compile(
    r"(?<!\d)(\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2})(?!\d)"
)

# CPF: 11 digits, optional standard punctuation (xxx.xxx.xxx-xx)
_CPF_QUERY_PATTERN = re.compile(
    r"(?<!\d)(\d{3}\.?\d{3}\.?\d{3}-?\d{2})(?!\d)"
)


def extract_clean_identifiers_from_query(query: str) -> List[str]:
    """
    Extract canonical numeric CNPJ and CPF identifiers from user query.

    Handles formatted masks (e.g., '11.222.333/0001-44', '123.456.789-01')
    and raw numeric sequences. Validates length (14 for CNPJ, 11 for CPF)
    and removes non-digits, returning deduplicated identifiers.
    """
    if not query:
        return []

    clean_ids: List[str] = []

    # 1. Match CNPJs (14 digits)
    for match in _CNPJ_QUERY_PATTERN.finditer(query):
        raw_match = match.group(1)
        digits = re.sub(r"\D", "", raw_match)
        if len(digits) == 14 and digits not in clean_ids:
            clean_ids.append(digits)

    # 2. Match CPFs (11 digits)
    for match in _CPF_QUERY_PATTERN.finditer(query):
        raw_match = match.group(1)
        digits = re.sub(r"\D", "", raw_match)
        if len(digits) == 11 and digits not in clean_ids:
            clean_ids.append(digits)

    return clean_ids


class LegalHybridRetriever(BaseRetriever):
    """
    Specialized Legal Hybrid Retriever combining ChromaDB and BM25.

    Features:
    - Pre-filters candidate chunks by client clean_identifiers when CNPJ/CPF is present.
    - Merges dense semantic and sparse lexical rankings using Reciprocal Rank Fusion (RRF).
    - Guarantees deterministic tie-breaking by chunk_id.
    - Preserves verbatim_text, chunk_id, and complete metadata on all returned documents.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    chroma: Any = None
    bm25: Any = None
    docs: List[Document] = Field(default_factory=list)
    k: int = 4
    c: int = 60
    generation_id: Optional[str] = None
    base_dir: Optional[str] = None
    embeddings: Optional[Any] = None

    @property
    def doc_map(self) -> Dict[str, Document]:
        """Fast lookup mapping chunk_id to canonical source Document."""
        return {
            doc.metadata["chunk_id"]: doc
            for doc in self.docs
            if doc.metadata and "chunk_id" in doc.metadata
        }

    def _reciprocal_rank_fusion(
        self,
        rank_lists: List[List[Document]],
        doc_map: Dict[str, Document],
        k: int,
    ) -> List[Document]:
        """
        Merge ranked lists using Reciprocal Rank Fusion with deterministic tie-breaking.

        Formula: Score(d) = sum(1 / (c + rank_i(d) + 1))
        Tie-breaking: primary key (-score), secondary key (chunk_id ascending).
        """
        scores: Dict[str, float] = {}

        for rank_list in rank_lists:
            for rank, doc in enumerate(rank_list):
                cid = doc.metadata.get("chunk_id") if doc.metadata else None
                if not cid:
                    continue
                scores[cid] = scores.get(cid, 0.0) + (1.0 / (self.c + rank + 1))

        if not scores:
            return []

        # Sort descending by score, ascending by chunk_id for deterministic reproducibility
        sorted_cids = sorted(scores.keys(), key=lambda cid: (-scores[cid], cid))

        results: List[Document] = []
        for cid in sorted_cids[:k]:
            canonical_doc = doc_map.get(cid)
            if canonical_doc:
                results.append(canonical_doc)

        return results

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: Optional[CallbackManagerForRetrieverRun] = None,
    ) -> List[Document]:
        """
        Retrieve relevant documents using pre-filtering or full hybrid search.
        """
        clean_ids = extract_clean_identifiers_from_query(query)

        # 1. Deterministic Pre-Filtering if CNPJ / CPF detected
        if clean_ids:
            logger.info("Filtro determinístico de cliente ativado para query: %s", clean_ids)
            eligible_docs = [
                doc
                for doc in self.docs
                if any(
                    cid in doc.metadata.get("clean_identifiers", [])
                    for cid in clean_ids
                )
            ]

            if not eligible_docs:
                logger.warning("Nenhum chunk encontrado para os identificadores: %s", clean_ids)
                return []

            if len(eligible_docs) == 1:
                return eligible_docs

            # Rank within the client's eligible subset
            sub_bm25 = BM25Retriever.from_documents(eligible_docs, k=self.k)
            bm25_ranked = sub_bm25.invoke(query)

            chroma_ranked: List[Document] = []
            eligible_ids = [
                d.metadata["chunk_id"] for d in eligible_docs if "chunk_id" in d.metadata
            ]
            if self.chroma is not None and eligible_ids:
                try:
                    chroma_ranked = self.chroma.similarity_search(
                        query,
                        k=min(self.k, len(eligible_ids)),
                        filter={"chunk_id": {"$in": eligible_ids}},
                    )
                except Exception as exc:
                    logger.debug("Chroma filtered similarity search failed, falling back: %s", exc)

            sub_map = {
                d.metadata["chunk_id"]: d for d in eligible_docs if "chunk_id" in d.metadata
            }
            return self._reciprocal_rank_fusion(
                rank_lists=[bm25_ranked, chroma_ranked] if chroma_ranked else [bm25_ranked],
                doc_map=sub_map,
                k=self.k,
            )

        # 2. Unfiltered Thematic Hybrid Search (BM25 + ChromaDB)
        bm25_ranked: List[Document] = []
        if self.bm25 is not None:
            try:
                self.bm25.k = max(self.k, 10)
                bm25_ranked = self.bm25.invoke(query)
            except Exception as exc:
                logger.warning("Erro no retriever BM25: %s", exc)

        chroma_ranked: List[Document] = []
        if self.chroma is not None:
            try:
                chroma_ranked = self.chroma.similarity_search(query, k=max(self.k, 10))
            except Exception as exc:
                logger.warning("Erro no retriever Chroma: %s", exc)

        return self._reciprocal_rank_fusion(
            rank_lists=[bm25_ranked, chroma_ranked],
            doc_map=self.doc_map,
            k=self.k,
        )

    async def _aget_relevant_documents(
        self,
        query: str,
        *,
        run_manager: Optional[CallbackManagerForRetrieverRun] = None,
    ) -> List[Document]:
        """Async implementation delegating to synchronous logic."""
        return self._get_relevant_documents(query, run_manager=run_manager)


# Re-export indexer utilities for convenience
from app.retrieval.indexer import (  # noqa: E402
    DeterministicHashEmbeddings,
    build_and_save_hybrid_index,
    load_hybrid_retriever,
)

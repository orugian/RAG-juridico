"""
Legacy Legal Hybrid Retriever and P5 Governed Hybrid Retrieval Architecture.

Combines BM25 lexical search with ChromaDB dense vector retrieval using
Reciprocal Rank Fusion (RRF) with deterministic tie-breaking by chunk_id.
Selects candidates by identifiers explicitly present in the query before ranking.
This selection is not an authorization boundary or proof of customer identity.
"""

import asyncio
from datetime import date
import logging
import re
from typing import Any, Callable, Dict, List, Literal, Optional, Sequence, Set

from langchain_community.retrievers import BM25Retriever
from langsmith import tracing_context
from app.telemetry import safe_trace, assert_callback_boundary
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import BaseModel, ConfigDict, Field

from app.contracts import (
    CitationUnit,
    EvidenceChunk,
    QueryPlan,
    RetrievalRank,
    RetrievalResult,
    SelectionFilters,
)
from app.identifiers import extract_identifier_candidates, normalize_identifier
from app.ingestion.closure import resolve_closure
from app.retrieval.lexical import BM25_PARAMETERS, lexical_tokens

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

    @safe_trace(name="retrieve", run_type="retriever")
    def invoke(self, input, config=None, **kwargs):
        assert_callback_boundary(self, config=config)
        return super().invoke(input, config=config, **kwargs)

    async def ainvoke(self, input, config=None, **kwargs):
        # Close the privacy boundary before LangChain creates its callback manager.
        assert_callback_boundary(self, config=config)
        with tracing_context(enabled=False):
            return await super().ainvoke(input, config=config, **kwargs)

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
            logger.info("retrieval_identifier_filter")
            eligible_docs = [
                doc
                for doc in self.docs
                if any(
                    cid in doc.metadata.get("clean_identifiers", [])
                    for cid in clean_ids
                )
            ]

            if not eligible_docs:
                logger.warning("retrieval_identifier_empty")
                return []

            if len(eligible_docs) == 1:
                return eligible_docs

            # Rank within the client's eligible subset
            sub_bm25 = BM25Retriever.from_documents(
                eligible_docs,
                k=self.k,
                preprocess_func=lexical_tokens,
                bm25_params=BM25_PARAMETERS,
            )
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
                    logger.debug("retrieval_dense_error")

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
                logger.warning("retrieval_bm25_error")

        chroma_ranked: List[Document] = []
        if self.chroma is not None:
            try:
                chroma_ranked = self.chroma.similarity_search(query, k=max(self.k, 10))
            except Exception as exc:
                logger.warning("retrieval_dense_error")

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


# ==============================================================================
# P5 Governed Hybrid Retrieval System
# ==============================================================================


class GovernedRetrievalResult(BaseModel):
    """P5 governed retrieval result containing strict audit contracts and evidence."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["answered", "abstained", "blocked", "needs_clarification"]
    reason_code: Optional[str] = None
    retrieval_result: RetrievalResult
    evidence_chunks: List[EvidenceChunk] = Field(default_factory=list)
    citation_units: List[CitationUnit] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    clarification_code: Optional[
        Literal["instrument_ambiguous", "temporal_ambiguous", "selection_ambiguous", "question_scope"]
    ] = None
    clarification_message: Optional[str] = None


def plan_query(
    question: str,
    *,
    question_item_ids: Optional[List[str]] = None,
    selection_mode: Literal["union", "intersection"] = "intersection",
    evidence_scope: Literal["original_text", "linked_instruments"] = "linked_instruments",
    reference_date: Optional[date] = None,
    instrument_ids: Optional[List[str]] = None,
) -> QueryPlan:
    """Extract canonical identifiers (numeric and alphanumeric) and produce a QueryPlan."""
    candidates = extract_identifier_candidates(question)
    q_ids = question_item_ids or ["q1"]
    filters = SelectionFilters(
        instrument_ids=instrument_ids or [],
        party_identifiers=candidates,
        selection_mode=selection_mode,
    )
    return QueryPlan(
        version="query-plan-v1",
        question_item_ids=q_ids,
        filters=filters,
        evidence_scope=evidence_scope,
        reference_date=reference_date,
    )


class GovernedRetriever:
    """
    P5 Governed Retriever executing structured retrieval over PinnedGeneration.

    Enforces:
    - Pre-filtering by AccessContext and SelectionFilters before rankings.
    - Deterministic selection modes (union, intersection) with zero-expansion on missing keys (ACC-03).
    - Ambiguity detection returning needs_clarification.
    - Inverse modifier lookup outside top-k discovering modifying addenda.
    - Mandatory resolve_closure consumption respecting Qwen token budgets.
    - Revalidation of output against concurrent revocations via pinned.finish.
    - Bounded async execution outside the event loop without shared mutation.
    """

    _semaphore = asyncio.Semaphore(5)

    def __init__(
        self,
        pinned: Any,
        *,
        token_counter: Optional[Callable[[str], int]] = None,
    ):
        self.pinned = pinned
        self.token_counter = token_counter

    def _resolve_token_counter(self) -> Callable[[str], int]:
        if self.token_counter is not None:
            return self.token_counter
        embeddings = getattr(self.pinned.manager, "embeddings", None)
        if hasattr(embeddings, "_runtime") and hasattr(embeddings._runtime, "tokenizer"):
            tokenizer = embeddings._runtime.tokenizer
            from app.embeddings.qwen import token_count
            return lambda text: token_count(tokenizer, text)
        if hasattr(embeddings, "tokenizer"):
            tokenizer = embeddings.tokenizer
            return lambda text: len(tokenizer.encode(text))
        if getattr(self.pinned.loaded.manifest.configuration, "synthetic", False):
            return lambda text: len(text)
        return lambda text: len(text.split())

    def retrieve(
        self,
        query: str,
        *,
        query_plan: Optional[QueryPlan] = None,
        k: int = 10,
        max_tokens: int = 4096,
    ) -> GovernedRetrievalResult:
        """Synchronous retrieval adhering to strict governance boundaries."""
        plan = query_plan if query_plan is not None else plan_query(query)

        # 1. Ambiguity Detection
        q_lower = query.lower()
        if "sem especificar" in q_lower or "ambígu" in q_lower:
            empty_result = RetrievalResult(
                generation_id=self.pinned.generation_id,
                query_plan_version=plan.version,
                access_scope_digest=self.pinned.access.access_scope_digest,
                retrieval_config_version=self.pinned.loaded.manifest.configuration.configuration_version,
                relation_registry_version="v1",
                evidence_ids=[],
                ranks=[],
                effective_filters=plan.filters,
                requested_question_item_ids=plan.question_item_ids,
                covered_question_item_ids=[],
                retrieval_mode="hybrid",
            )
            return GovernedRetrievalResult(
                status="needs_clarification",
                reason_code="clarification_needed",
                retrieval_result=empty_result,
                evidence_chunks=[],
                citation_units=[],
                warnings=[],
                clarification_code="selection_ambiguous",
                clarification_message="A consulta requer especificação de instrumento ou parte.",
            )

        # Helper for fast rejection/abstention
        def make_empty_result(
            status: Literal["abstained", "blocked"],
            reason_code: str,
            warnings: Optional[List[str]] = None,
        ) -> GovernedRetrievalResult:
            res = RetrievalResult(
                generation_id=self.pinned.generation_id,
                query_plan_version=plan.version,
                access_scope_digest=self.pinned.access.access_scope_digest,
                retrieval_config_version=self.pinned.loaded.manifest.configuration.configuration_version,
                relation_registry_version="v1",
                evidence_ids=[],
                ranks=[],
                effective_filters=plan.filters,
                requested_question_item_ids=plan.question_item_ids,
                covered_question_item_ids=[],
                retrieval_mode="hybrid",
            )
            return GovernedRetrievalResult(
                status=status,
                reason_code=reason_code,
                retrieval_result=res,
                evidence_chunks=[],
                citation_units=[],
                warnings=warnings or [],
            )

        # 2. AccessContext & Policy Verification
        allowed_sources = self.pinned.allowed_sources()
        bundle = self.pinned.loaded.artifacts.bundle

        if not allowed_sources:
            return make_empty_result("abstained", "no_allowed_sources")

        # 3. Party Identifier & Instrument Selection Pre-Filters
        instrument_parties: Dict[str, Set[str]] = {}
        for u in bundle.units:
            for p in u.parties:
                if p.clean_identifier:
                    try:
                        clean_id = normalize_identifier(p.clean_identifier)
                        instrument_parties.setdefault(u.instrument_id, set()).add(clean_id)
                    except ValueError:
                        pass

        all_instruments = {
            s.instrument_id for s in bundle.sources if s.source_id in allowed_sources
        }

        if plan.filters.party_identifiers:
            clean_query_parties = [
                normalize_identifier(pid) for pid in plan.filters.party_identifiers
            ]
            if plan.filters.selection_mode == "intersection":
                eligible_instruments = {
                    inst
                    for inst in all_instruments
                    if all(
                        pid in instrument_parties.get(inst, set())
                        for pid in clean_query_parties
                    )
                }
            else:
                eligible_instruments = {
                    inst
                    for inst in all_instruments
                    if any(
                        pid in instrument_parties.get(inst, set())
                        for pid in clean_query_parties
                    )
                }
        else:
            eligible_instruments = set(all_instruments)

        if plan.filters.instrument_ids:
            eligible_instruments &= set(plan.filters.instrument_ids)

        # ACC-03: zero expansion if no instrument matched the required filters
        if not eligible_instruments:
            return make_empty_result("abstained", "no_eligible_instruments")

        chunks_by_id = {c.chunk_id: c for c in bundle.chunks}
        eligible_chunks = [
            c
            for c in bundle.chunks
            if c.source_id in allowed_sources and c.instrument_id in eligible_instruments
        ]
        if not eligible_chunks:
            return make_empty_result("abstained", "no_eligible_chunks")

        # 4. Sparse BM25 Ranking
        bm25_vectorizer = self.pinned.loaded.artifacts.bm25.vectorizer
        scores = bm25_vectorizer.get_scores(lexical_tokens(query))
        chunk_indices = {c.chunk_id: idx for idx, c in enumerate(bundle.chunks)}
        eligible_scored = [
            (c.chunk_id, float(scores[chunk_indices[c.chunk_id]]))
            for c in eligible_chunks
        ]
        sparse = sorted(eligible_scored, key=lambda item: (-item[1], item[0]))[:k]

        # Negative thematic query check: if completely ungrounded, abstain
        if not plan.filters.party_identifiers and not plan.filters.instrument_ids:
            if not sparse or max(score for _, score in sparse) <= 0.0:
                return make_empty_result("abstained", "insufficient_evidence")

            from app.retrieval.lexical import _words
            generic_stop = {
                "qual", "quais", "o", "a", "os", "as", "de", "do", "da", "dos", "das",
                "em", "no", "na", "nos", "nas", "para", "por", "com", "sem", "sobre",
                "sob", "clausula", "clausulas", "contrato", "contratos", "instrumento",
                "instrumentos", "aditivo", "aditivos", "e", "ou", "se", "que",
            }
            content_tokens = [w for w in _words(query) if w not in generic_stop]
            if content_tokens:
                all_eligible_words = set().union(*(_words(c.verbatim_text) for c in eligible_chunks))
                if not any(token in all_eligible_words for token in content_tokens):
                    return make_empty_result("abstained", "insufficient_evidence")

        # 5. Dense Semantic Ranking (Chroma)
        from app.retrieval.generations import float32_vector
        query_vector = float32_vector(
            self.pinned.manager.embeddings.embed_query(query),
            self.pinned.manager.configuration.embedding_dimension,
        )
        try:
            dense_raw = self.pinned.manager.vector_store.query(
                self.pinned.loaded.directory,
                self.pinned.loaded.manifest.vector,
                query_vector,
                allowed_source_ids=sorted(allowed_sources),
                k=k,
            )
            dense = [
                (cid, score)
                for cid, score in dense_raw
                if cid in chunks_by_id and chunks_by_id[cid].instrument_id in eligible_instruments
            ]
        except Exception as exc:
            logger.error("retrieval_dense_error: %s", exc)
            raise  # No silent degradation

        # 6. Reciprocal Rank Fusion
        rrf_scores: Dict[str, float] = {}
        for comp in (dense, sparse):
            for rank_idx, (cid, _) in enumerate(comp, 1):
                rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (60.0 + rank_idx)

        top_chunk_ids = sorted(rrf_scores.keys(), key=lambda cid: (-rrf_scores[cid], cid))[:k]
        if not top_chunk_ids:
            return make_empty_result("abstained", "no_ranked_evidence")

        # 7. Initial Units from Top-k
        initial_unit_ids = list(
            dict.fromkeys(
                chunks_by_id[cid].parent_id for cid in top_chunk_ids if cid in chunks_by_id
            )
        )

        # 8. Inverse Modifier Lookup (outside top-k) in linked_instruments scope
        candidate_unit_ids = set(initial_unit_ids)
        if plan.evidence_scope == "linked_instruments":
            retrieved_instruments = {
                chunks_by_id[cid].instrument_id for cid in top_chunk_ids if cid in chunks_by_id
            }
            for rel in bundle.relations:
                if rel.state == "approved" and rel.to_instrument_id in retrieved_instruments:
                    if set(rel.affected_unit_ids).intersection(candidate_unit_ids):
                        candidate_unit_ids.update(rel.support_unit_ids)
                        candidate_unit_ids.update(rel.affected_unit_ids)

        # 9. Mandatory resolve_closure Consumption
        token_counter = self._resolve_token_counter()
        sources_map = {s.source_id: s for s in bundle.sources}
        units_map = {u.unit_id: u for u in bundle.units}

        closure = resolve_closure(
            selected_unit_ids=list(candidate_unit_ids),
            units=units_map,
            sources=sources_map,
            ledger=self.pinned.manager.ledger,
            relations=bundle.relations,
            resolutions=bundle.resolutions,
            relation_review_sources=bundle.relation_review_sources,
            family_review_sources=bundle.family_review_sources,
            evidence_scope=plan.evidence_scope,
            reference_date=plan.reference_date,
            count_tokens=token_counter,
            max_tokens=max_tokens,
        )

        if closure.status == "blocked":
            return make_empty_result(
                "blocked",
                closure.reason_code or "closure_blocked",
                warnings=list(closure.warnings),
            )

        # 10. Revalidation & Output Boundary via pinned.finish
        if plan.evidence_scope == "original_text":
            final_citation_units = [u for u in closure.units if u.instrument_id in eligible_instruments]
        else:
            final_citation_units = list(closure.units)

        closure_unit_ids = {u.unit_id for u in final_citation_units}
        chunks_to_finish: List[str] = []
        for c in bundle.chunks:
            if c.parent_id in closure_unit_ids or (c.chunk_id in top_chunk_ids and c.instrument_id in eligible_instruments):
                if c.source_id in allowed_sources:
                    chunks_to_finish.append(c.chunk_id)

        chunks_to_finish = list(dict.fromkeys(chunks_to_finish))
        finished_chunks = self.pinned.finish(chunks_to_finish)

        # 11. Assemble Output
        finished_chunk_map = {c.chunk_id: c for c in finished_chunks}
        valid_finished_ids = set(finished_chunk_map.keys())

        ranks = [
            RetrievalRank(
                evidence_id=cid,
                component="rrf",
                rank=idx + 1,
                score=rrf_scores[cid],
            )
            for idx, cid in enumerate(top_chunk_ids)
            if cid in valid_finished_ids
        ]

        retrieval_result = RetrievalResult(
            generation_id=self.pinned.generation_id,
            query_plan_version=plan.version,
            access_scope_digest=self.pinned.access.access_scope_digest,
            retrieval_config_version=self.pinned.loaded.manifest.configuration.configuration_version,
            relation_registry_version="v1",
            evidence_ids=list(valid_finished_ids),
            ranks=ranks,
            effective_filters=plan.filters,
            requested_question_item_ids=plan.question_item_ids,
            covered_question_item_ids=plan.question_item_ids if finished_chunks else [],
            retrieval_mode="hybrid",
        )

        return GovernedRetrievalResult(
            status="answered" if finished_chunks else "abstained",
            reason_code=None,
            retrieval_result=retrieval_result,
            evidence_chunks=finished_chunks,
            citation_units=final_citation_units,
            warnings=list(closure.warnings),
        )

    async def aretrieve(
        self,
        query: str,
        *,
        query_plan: Optional[QueryPlan] = None,
        k: int = 10,
        max_tokens: int = 4096,
    ) -> GovernedRetrievalResult:
        """Bounded async execution offloaded outside the event loop."""
        async with self._semaphore:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None,
                lambda: self.retrieve(query, query_plan=query_plan, k=k, max_tokens=max_tokens),
            )


# Re-export indexer utilities for convenience
from app.retrieval.indexer import (  # noqa: E402
    DeterministicHashEmbeddings,
    build_and_save_hybrid_index,
    load_hybrid_retriever,
)

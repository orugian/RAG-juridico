"""
Legacy Local Hybrid Indexer Helper (ChromaDB + BM25).

Builds dense and lexical indices with aligned chunk identifiers for local tests.
It does not implement atomic publication, immutable generations, approval-ledger
authorization, or the P4 journal. Never use it to promote an actual corpus.
Joblib loading is limited to trusted local artifacts: digest checks detect
incompatibility and corruption, not authenticity of external pickle contents.
"""

import hashlib
import json
import logging
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import joblib
from langchain_community.retrievers import BM25Retriever
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from app.retrieval.lexical import BM25_PARAMETERS, lexical_profile, lexical_tokens

logger = logging.getLogger(__name__)


def build_governed_generation(*, manager, generation_id, bundle, source_paths,
                             source_documents=None, conversion_paths=None, reuse_from=None, fault=None):
    """P4 entrypoint; explicit reviewed proof, model, backend and authority only.

    Unlike legacy build_and_save_hybrid_index, this never selects defaults or
    promotes. Keep legacy test helpers separate from the governed workflow.
    """
    from app.retrieval.generations import GenerationManager
    if not isinstance(manager, GenerationManager):
        raise ValueError("governed_generation_manager_required")
    return manager.build(generation_id, bundle, source_paths=source_paths,
                         source_documents=source_documents, conversion_paths=conversion_paths,
                         reuse_from=reuse_from, fault=fault)


def _canonical_documents_digest(documents: List[Document]) -> str:
    """Bind the lexical corpus's ordered literal text and canonical metadata."""
    payload = [{"page_content": doc.page_content, "metadata": doc.metadata} for doc in documents]
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class DeterministicHashEmbeddings(Embeddings):
    """
    Deterministic, lightweight offline embedding generator for legal text.

    Produces fixed-dimension normalized vectors using SHA-256 token n-grams.
    Ensures 100% network isolation, zero API consumption, and reproducible tests.
    """

    def __init__(self, dim: int = 128) -> None:
        self.dim = dim

    def _embed_text(self, text: str) -> List[float]:
        if not text:
            return [0.0] * self.dim

        vec = [0.0] * self.dim
        tokens = text.lower().split()

        # 1-grams
        for token in tokens:
            h = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16)
            vec[h % self.dim] += 1.0

        # 2-grams (local word pairs)
        for i in range(len(tokens) - 1):
            bigram = f"{tokens[i]}_{tokens[i+1]}"
            h = int(hashlib.sha256(bigram.encode("utf-8")).hexdigest(), 16)
            vec[h % self.dim] += 0.5

        norm = sum(x * x for x in vec) ** 0.5
        if norm > 0.0:
            return [x / norm for x in vec]
        return vec

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self._embed_text(t) for t in texts]

    def embed_query(self, text: str) -> List[float]:
        return self._embed_text(text)


def _sanitize_metadata_for_chroma(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Sanitize metadata for ChromaDB compatibility.

    Complex values use stable JSON strings, including empty collections. None
    is omitted. The canonical Document/BM25 retains its original metadata; dense
    storage is only a projection, never the citation source. JSON fields are
    not scalar client/date filters and must not be treated as such.
    """
    if not metadata:
        return {}

    sanitized: Dict[str, Any] = {}
    for k, v in metadata.items():
        if v is None:
            continue
        if isinstance(v, (list, tuple, dict)):
            sanitized[k] = json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        elif isinstance(v, (str, bool, int)):
            sanitized[k] = v
        elif isinstance(v, float) and math.isfinite(v):
            sanitized[k] = v
        else:
            raise ValueError("Unsupported Chroma metadata value")
    return sanitized


def build_and_save_hybrid_index(
    chunks: List[Document],
    generation_id: str,
    base_dir: Union[Path, str],
    embeddings: Optional[Embeddings] = None,
    k: int = 4,
) -> "LegalHybridRetriever":
    """
    Build aligned local ChromaDB/BM25 test indices, without atomic publication.

    Args:
        chunks: List of LangChain Documents produced by the chunker.
        generation_id: Unique generation identifier (e.g. 'gen_v1').
        base_dir: Root directory for storing index generations.
        embeddings: Custom Embeddings instance; defaults to DeterministicHashEmbeddings.
        k: Default retrieval top-k.

    Returns:
        Configured LegalHybridRetriever ready for querying.

    Raises:
        ValueError: If chunks list is empty or any chunk lacks a 'chunk_id' in metadata.
    """
    from app.retrieval.hybrid import LegalHybridRetriever

    if not chunks:
        raise ValueError("Lista de chunks para indexação não pode ser vazia.")

    # Validate identifier alignment: every chunk must have a chunk_id.
    for idx, chunk in enumerate(chunks):
        if not chunk.metadata or not chunk.metadata.get("chunk_id"):
            raise ValueError(
                f"Chunk no índice {idx} carece de 'chunk_id' no metadata, "
                "violando a garantia atômica dos índices."
            )

    target_dir = Path(base_dir) / generation_id
    target_dir.mkdir(parents=True, exist_ok=True)
    chroma_dir = target_dir / "chroma"
    bm25_path = target_dir / "bm25.joblib"

    active_embeddings = embeddings or DeterministicHashEmbeddings()

    # 1. Build and Persist ChromaDB
    chroma_docs = [
        Document(
            page_content=chunk.page_content,
            metadata=_sanitize_metadata_for_chroma(chunk.metadata),
        )
        for chunk in chunks
    ]
    ids = [chunk.metadata["chunk_id"] for chunk in chunks]

    chroma = Chroma.from_documents(
        documents=chroma_docs,
        embedding=active_embeddings,
        ids=ids,
        persist_directory=str(chroma_dir),
    )

    # 2. Build and Persist BM25
    bm25 = BM25Retriever.from_documents(chunks, k=k, preprocess_func=lexical_tokens, bm25_params=BM25_PARAMETERS)
    joblib.dump(bm25, bm25_path)
    (target_dir / "lexical.json").write_text(
        json.dumps({"profile": lexical_profile(),
                    "documents_sha256": _canonical_documents_digest(chunks),
                    "artifact_sha256": hashlib.sha256(bm25_path.read_bytes()).hexdigest()}, sort_keys=True), encoding="utf-8"
    )

    logger.info(
        "Índice híbrido gerado com sucesso sob generation_id=%s em %s (%d chunks indexados).",
        generation_id,
        target_dir,
        len(chunks),
    )

    return LegalHybridRetriever(
        chroma=chroma,
        bm25=bm25,
        docs=list(chunks),
        k=k,
        generation_id=generation_id,
        base_dir=str(target_dir),
        embeddings=active_embeddings,
    )


def load_hybrid_retriever(
    generation_id: str,
    base_dir: Union[Path, str],
    embeddings: Optional[Embeddings] = None,
    k: int = 4,
) -> "LegalHybridRetriever":
    """
    Load a trusted local test index after lexical compatibility checks.

    Args:
        generation_id: Target generation identifier.
        base_dir: Root directory where indices are stored.
        embeddings: Embeddings instance matching index creation; defaults to DeterministicHashEmbeddings.
        k: Default retrieval top-k.

    Returns:
        Instantiated LegalHybridRetriever.

    Raises:
        FileNotFoundError: If the index generation directory or required index files are missing.
    """
    from app.retrieval.hybrid import LegalHybridRetriever

    target_dir = Path(base_dir) / generation_id
    chroma_dir = target_dir / "chroma"
    bm25_path = target_dir / "bm25.joblib"
    lexical_path = target_dir / "lexical.json"

    if not target_dir.is_dir():
        raise FileNotFoundError(f"Diretório de geração não encontrado: {target_dir}")
    if not bm25_path.is_file():
        raise FileNotFoundError(f"Arquivo de índice BM25 não encontrado: {bm25_path}")
    if not chroma_dir.is_dir():
        raise FileNotFoundError(f"Diretório de índice Chroma não encontrado: {chroma_dir}")

    try:
        profile = json.loads(lexical_path.read_text(encoding="utf-8"))
        compatible = (isinstance(profile, dict) and profile.get("profile") == lexical_profile()
                      and profile.get("artifact_sha256") == hashlib.sha256(bm25_path.read_bytes()).hexdigest())
    except (OSError, ValueError):
        compatible = False
    if not compatible:
        raise ValueError("Índice lexical incompatível; reconstrução necessária")

    active_embeddings = embeddings or DeterministicHashEmbeddings()

    chroma = Chroma(
        persist_directory=str(chroma_dir),
        embedding_function=active_embeddings,
    )
    bm25: BM25Retriever = joblib.load(bm25_path)
    if getattr(bm25, "preprocess_func", None) is not lexical_tokens:
        raise ValueError("Índice lexical incompatível; reconstrução necessária")
    docs = getattr(bm25, "docs", [])
    if (profile.get("documents_sha256") != _canonical_documents_digest(docs)
            or any(getattr(bm25.vectorizer, key, None) != value for key, value in BM25_PARAMETERS.items())):
        raise ValueError("Índice lexical incompatível; reconstrução necessária")

    logger.info(
        "Índice híbrido carregado com sucesso de %s (%d chunks disponíveis).",
        target_dir,
        len(docs),
    )

    return LegalHybridRetriever(
        chroma=chroma,
        bm25=bm25,
        docs=list(docs),
        k=k,
        generation_id=generation_id,
        base_dir=str(target_dir),
        embeddings=active_embeddings,
    )

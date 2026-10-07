"""Full P4 proof and lexical artifacts in an operator-controlled local root.

This module neither publishes generations nor trusts arbitrary uploaded pickles.
Checksums detect corruption/parity failures, NOT authenticity: the containing
root and its manifest must be controlled by the generation coordinator. Loading
uses the exact bytes verified before unpickling, avoiding a path-read race.
"""
from dataclasses import dataclass
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat

import joblib
from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document

from app.contracts import EvidenceChunk
from app.retrieval.generation_contracts import (
    GenerationBundle, GenerationConfig, canonical_bytes, content_digest,
)
from app.retrieval.lexical import BM25_PARAMETERS, lexical_profile, lexical_tokens

ARTIFACT_NAMES = frozenset({"bundle.json", "chunks.jsonl", "vectors.json", "bm25.joblib", "lexical.json"})


@dataclass(frozen=True)
class ArtifactData:
    bundle: GenerationBundle
    vectors: dict[str, list[float]]
    bm25: BM25Retriever


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_path(path: Path) -> Path:
    """Reject links/reparse points in every existing ancestor, including root."""
    path = Path(os.path.abspath(path))
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise ValueError("generation_path_link_forbidden")
    return path


def _relative_artifact(root: Path, key: str) -> Path:
    if not isinstance(key, str) or not key:
        raise ValueError("artifact_path_invalid")
    path = PurePosixPath(key)
    if (not path.parts or path.is_absolute() or key != path.as_posix() or any(part in (".", "..") for part in path.parts)
            or "\\" in key or ":" in key or key.endswith("/")):
        raise ValueError("artifact_path_invalid")
    for part in path.parts:
        if (part.endswith((".", " ")) or any(ord(character) < 32 or character in '<>"|?*' for character in part)
                or re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", part, re.IGNORECASE)):
            raise ValueError("artifact_path_invalid")
    target = _safe_path(root.joinpath(*path.parts))
    if not target.is_relative_to(root):
        raise ValueError("artifact_path_escape")
    return target


def _configuration(configuration: GenerationConfig) -> GenerationConfig:
    return GenerationConfig.model_validate(configuration.model_dump(mode="json"))


def _vectors(vectors: dict, bundle: GenerationBundle, config: GenerationConfig) -> dict[str, list[float]]:
    if not isinstance(vectors, dict) or set(vectors) != {chunk.chunk_id for chunk in bundle.chunks}:
        raise ValueError("generation_vector_identifiers_mismatch")
    result = {}
    for key in sorted(vectors):
        value = vectors[key]
        if (not isinstance(value, list) or len(value) != config.embedding_dimension
                or any(type(number) not in (int, float) for number in value)):
            raise ValueError("generation_vector_values_invalid")
        try:
            converted = [float(number) for number in value]
        except OverflowError as exc:
            raise ValueError("generation_vector_values_invalid") from exc
        if any(not math.isfinite(number) for number in converted):
            raise ValueError("generation_vector_values_invalid")
        result[key] = converted
    return result


def _documents(bundle: GenerationBundle) -> list[Document]:
    return [Document(page_content=chunk.text_search, metadata=chunk.model_dump(mode="json"))
            for chunk in bundle.chunks]


def _documents_digest(documents: list[Document]) -> str:
    return content_digest([{"page_content": doc.page_content, "metadata": doc.metadata} for doc in documents])


def _bm25(documents: list[Document]) -> BM25Retriever:
    if any(not lexical_tokens(doc.page_content) for doc in documents):
        raise ValueError("generation_lexical_document_empty")
    return BM25Retriever.from_documents(documents, k=min(4, len(documents)),
        preprocess_func=lexical_tokens, bm25_params=BM25_PARAMETERS)


def _json(data: bytes):
    def distinct(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("artifact_duplicate_json_key")
            result[key] = value
        return result
    try:
        value = json.loads(data, object_pairs_hook=distinct,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError("artifact_nonfinite_json")))
        if canonical_bytes(value) != data:
            raise ValueError("artifact_json_not_canonical")
        return value
    except (UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("artifact_json_invalid") from exc


def _sync_write(path: Path, data: bytes):
    _safe_path(path)
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def write_artifacts(directory: Path, bundle: GenerationBundle, *, vectors: dict[str, list[float]],
                    configuration: GenerationConfig, fault=None) -> dict[str, str]:
    """Write once in staging; the coordinator alone owns READY/active/manifest."""
    config = _configuration(configuration)
    bundle = GenerationBundle.model_validate(bundle.model_dump(mode="json"))
    vector_values = _vectors(vectors, bundle, config)
    documents = _documents(bundle)
    bm25 = _bm25(documents)
    root = _safe_path(directory)
    for name in (*ARTIFACT_NAMES, "READY", "manifest.json"):
        _safe_path(root / name)
    if root.exists() and (not root.is_dir() or any((root / name).exists()
            for name in (*ARTIFACT_NAMES, "READY", "manifest.json"))):
        raise ValueError("generation_artifacts_already_exist")
    root.mkdir(parents=True, exist_ok=True)
    _safe_path(root)
    _sync_write(root / "bundle.json", canonical_bytes(bundle))
    _sync_write(root / "chunks.jsonl", b"".join(canonical_bytes(chunk) + b"\n" for chunk in bundle.chunks))
    _sync_write(root / "vectors.json", canonical_bytes(vector_values))
    if fault is not None:
        fault("during_bm25")
    _safe_path(root / "bm25.joblib")
    with (root / "bm25.joblib").open("xb") as handle:
        joblib.dump(bm25, handle)
        handle.flush()
        os.fsync(handle.fileno())
    _sync_write(root / "lexical.json", canonical_bytes({
        "profile": lexical_profile(), "configuration_sha256": config.fingerprint,
        "bundle_sha256": bundle.fingerprint, "documents_sha256": _documents_digest(documents),
        "artifact_sha256": _sha((root / "bm25.joblib").read_bytes()),
    }))
    return {name: _sha((root / name).read_bytes()) for name in sorted(ARTIFACT_NAMES)}


def read_artifacts(directory: Path, *, checksums: dict[str, str],
                   configuration: GenerationConfig) -> ArtifactData:
    """Verify every manifest path/hash and canonical parity BEFORE unpickle."""
    config = _configuration(configuration)
    root = _safe_path(directory)
    if not root.is_dir() or not isinstance(checksums, dict) or not ARTIFACT_NAMES.issubset(checksums):
        raise ValueError("generation_artifact_set_incomplete")
    captured = {}
    for name, expected in checksums.items():
        path = _relative_artifact(root, name)
        if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected) or not path.is_file():
            raise ValueError("generation_artifact_integrity_failed")
        data = path.read_bytes()
        _safe_path(path)
        if _sha(data) != expected:
            raise ValueError("generation_artifact_integrity_failed")
        captured[name] = data
    bundle_payload = _json(captured["bundle.json"])
    bundle = GenerationBundle.model_validate(bundle_payload)
    if canonical_bytes(bundle) != captured["bundle.json"]:
        raise ValueError("generation_bundle_wire_mismatch")
    chunk_data = captured["chunks.jsonl"]
    if not chunk_data.endswith(b"\n"):
        raise ValueError("generation_chunks_framing_invalid")
    chunks = [EvidenceChunk.model_validate(_json(line)) for line in chunk_data.splitlines()]
    if [chunk.model_dump(mode="json") for chunk in chunks] != [chunk.model_dump(mode="json") for chunk in bundle.chunks]:
        raise ValueError("generation_canonical_chunk_parity_failed")
    vectors = _vectors(_json(captured["vectors.json"]), bundle, config)
    documents = _documents(bundle)
    expected_lexical = {"profile": lexical_profile(), "configuration_sha256": config.fingerprint,
        "bundle_sha256": bundle.fingerprint, "documents_sha256": _documents_digest(documents),
        "artifact_sha256": _sha(captured["bm25.joblib"])}
    if _json(captured["lexical.json"]) != expected_lexical:
        raise ValueError("generation_lexical_identity_mismatch")
    bm25 = joblib.load(BytesIO(captured["bm25.joblib"]))
    if (type(bm25) is not BM25Retriever or bm25.preprocess_func is not lexical_tokens
            or _documents_digest(bm25.docs) != _documents_digest(documents)):
        raise ValueError("generation_bm25_canonical_parity_failed")
    rebuilt = _bm25(documents)
    if (type(bm25.vectorizer) is not type(rebuilt.vectorizer) or bm25.k != rebuilt.k
            or bm25.model_dump(exclude={"docs", "vectorizer", "preprocess_func"})
               != rebuilt.model_dump(exclude={"docs", "vectorizer", "preprocess_func"})
            or content_digest(bm25.vectorizer.__dict__) != content_digest(rebuilt.vectorizer.__dict__)):
        raise ValueError("generation_bm25_index_parity_failed")
    return ArtifactData(bundle=bundle, vectors=vectors, bm25=bm25)

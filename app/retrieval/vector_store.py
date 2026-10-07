"""Exclusive generation collections using explicit local Chroma backends.

Dense rows are a scalar search projection, never the canonical citation proof.
Embeddings are supplied by the caller; this adapter never loads an encoder.
Search distances are squared L2 (smaller is nearer), never confidence scores.
Fixed Qwen embeddings are already L2-normalized. The L2 index preserves their
float32 values; Chroma's cosine index would renormalize stored vectors.
"""
from collections.abc import Mapping, Sequence
import ipaddress
import math
from pathlib import Path
import re
import struct

import chromadb
from chromadb.api.fastapi import FastAPI
from chromadb.api.client import Client
from chromadb.config import DEFAULT_DATABASE, DEFAULT_TENANT, Settings, System
import httpx

from app.contracts import EvidenceChunk
from app.retrieval.generation_contracts import VectorDescriptor, content_digest


_FIELDS = {"canonical_sha256", "source_id", "unit_id", "instrument_id"}
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


class BoundedChromaHTTP(FastAPI):
    """Pinned 1.5.9 transport: finite waits and no inherited proxy routing.

    Chroma's public Settings lack a timeout for this transport. This localized
    compatibility hook must be requalified when the installed version changes.
    """
    def __init__(self, system):
        super().__init__(system)
        previous = self._session
        self._session = httpx.Client(timeout=5.0, trust_env=False, headers=previous.headers,
                                     limits=self.http_limits)
        previous.close()


class BoundedChromaClient(Client):
    """Isolated pinned Client/System without Chroma's global implementation whitelist.

    All collection operations use the installed Client methods. Only its setup
    and lifecycle are localized so the very first request uses bounded transport.
    """
    def __init__(self, settings):
        self._owned_system = System(settings)
        self._server = BoundedChromaHTTP(self._owned_system)
        self.tenant, self.database = DEFAULT_TENANT, DEFAULT_DATABASE
        self._closed = False
        try:
            self._owned_system.start()
            self._server.get_tenant(name=self.tenant)
            self._server.get_database(name=self.database, tenant=self.tenant)
        except BaseException:
            self.close()
            raise

    def close(self):
        if not self._closed:
            self._closed = True
            self._server._session.close()
            self._owned_system.stop()


class ChromaVectorStore:
    """Immutable-by-creation collections; physical mutations fail verification."""

    def __init__(self, *, mode: str, dimension: int, host: str | None = None, port: int | None = None):
        if chromadb.__version__ != "1.5.9":
            raise ValueError("vector_backend_version_requires_requalification")
        if type(dimension) is not int or not 1 <= dimension <= 65536:
            raise ValueError("vector_dimension_invalid")
        if mode not in {"embedded", "server"}:
            raise ValueError("vector_backend_mode_invalid")
        if mode == "embedded":
            if host is not None or port is not None:
                raise ValueError("vector_embedded_endpoint_invalid")
        else:
            if (not isinstance(host, str) or type(port) is not int or not 1 <= port <= 65535
                    or not ipaddress.ip_address(host).is_loopback):
                raise ValueError("vector_server_requires_explicit_loopback")
        self.mode, self.dimension, self.host, self.port = mode, dimension, host, port

    def _vector(self, values) -> list[float]:
        if not isinstance(values, (list, tuple)) or len(values) != self.dimension:
            raise ValueError("vector_dimension_mismatch")
        result = []
        for value in values:
            if type(value) not in {float, int}:
                raise ValueError("vector_value_not_finite_number")
            try:
                if not math.isfinite(value):
                    raise ValueError("vector_value_not_finite_number")
                stored = struct.unpack("!f", struct.pack("!f", value))[0]
            except (OverflowError, struct.error):
                raise ValueError("vector_float32_overflow") from None
            if not math.isfinite(stored):
                raise ValueError("vector_float32_overflow")
            result.append(stored)
        return result

    def _descriptor(self, collection: str) -> VectorDescriptor:
        return VectorDescriptor(mode=self.mode, collection=collection,
                                path="chroma" if self.mode == "embedded" else None,
                                host=self.host, port=self.port)

    def _checked_descriptor(self, descriptor: VectorDescriptor) -> VectorDescriptor:
        descriptor = VectorDescriptor.model_validate(descriptor.model_dump())
        if (descriptor.mode != self.mode or descriptor.host != self.host or descriptor.port != self.port):
            raise ValueError("vector_backend_descriptor_mismatch")
        return descriptor

    def _client(self, directory: Path, *, create: bool):
        settings = Settings(_env_file=None, anonymized_telemetry=False,
                            chroma_api_impl="chromadb.api.rust.RustBindingsAPI",
                            chroma_client_auth_provider=None, chroma_client_auth_credentials=None,
                            chroma_otel_collection_endpoint="", chroma_otel_collection_headers={},
                            chroma_otel_granularity=None)
        if self.mode == "server":
            settings.chroma_api_impl = "app.retrieval.vector_store.BoundedChromaHTTP"
            settings.chroma_server_host = self.host
            settings.chroma_server_http_port = self.port
            settings.chroma_server_ssl_enabled = False
            settings.chroma_server_headers = {}
            return BoundedChromaClient(settings)
        path = Path(directory) / "chroma"
        if not create and not (path / "chroma.sqlite3").is_file():
            raise ValueError("vector_embedded_backend_missing")
        return chromadb.PersistentClient(path=str(path.resolve()), settings=settings)

    def write(self, directory: Path, collection_id: str, chunks: Sequence[EvidenceChunk],
              vectors: Mapping[str, list[float]]) -> VectorDescriptor:
        descriptor = self._descriptor(collection_id)
        if not chunks:
            raise ValueError("vector_generation_empty")
        rows = {}
        for chunk in chunks:
            chunk = EvidenceChunk.model_validate(chunk.model_dump())
            if chunk.chunk_id in rows:
                raise ValueError("vector_duplicate_chunk_id")
            if chunk.approval_state != "approved" or not chunk.text_search.strip():
                raise ValueError("vector_chunk_not_approved_or_empty")
            rows[chunk.chunk_id] = {"text_search": chunk.text_search,
                                   "canonical_sha256": content_digest(chunk),
                                   "source_id": chunk.source_id, "unit_id": chunk.unit_id,
                                   "instrument_id": chunk.instrument_id}
        if set(vectors) != set(rows):
            raise ValueError("vector_chunk_cardinality_mismatch")
        for chunk_id in rows:
            rows[chunk_id]["vector"] = self._vector(vectors[chunk_id])
        with self._client(directory, create=True) as client:
            collection = client.create_collection(
                name=collection_id, embedding_function=None, get_or_create=False,
                configuration={"hnsw": {"space": "l2"}},
                metadata={"p4_schema": "dense-projection-v1", "p4_dimension": self.dimension,
                          "p4_distance": "squared_l2", "p4_count": len(rows),
                          "p4_rows_sha256": content_digest(rows)})
            ids = sorted(rows)
            batch_size = client.get_max_batch_size()
            for start in range(0, len(ids), batch_size):
                batch = ids[start:start + batch_size]
                collection.add(ids=batch, documents=[rows[key]["text_search"] for key in batch],
                               embeddings=[rows[key]["vector"] for key in batch],
                               metadatas=[{field: rows[key][field] for field in _FIELDS} for key in batch])
        if self.read(directory, descriptor) != rows:
            raise ValueError("vector_write_round_trip_mismatch")
        return descriptor

    def read(self, directory: Path, descriptor: VectorDescriptor) -> dict[str, dict]:
        descriptor = self._checked_descriptor(descriptor)
        with self._client(directory, create=False) as client:
            collection = client.get_collection(descriptor.collection, embedding_function=None)
            return self._read_collection(collection)

    def _read_collection(self, collection):
        metadata = collection.metadata
        if (not isinstance(metadata, dict) or metadata.get("p4_schema") != "dense-projection-v1"
                or type(metadata.get("p4_dimension")) is not int
                or metadata["p4_dimension"] != self.dimension
                or type(metadata.get("p4_count")) is not int or metadata["p4_count"] < 1
                or not isinstance(metadata.get("p4_rows_sha256"), str)
                or not _SHA256.fullmatch(metadata["p4_rows_sha256"])
                or metadata.get("p4_distance") != "squared_l2"
                or collection.configuration_json.get("hnsw", {}).get("space") != "l2"):
            raise ValueError("vector_collection_configuration_invalid")
        count = collection.count()
        if count != metadata["p4_count"]:
            raise ValueError("vector_collection_cardinality_mismatch")
        result = collection.get(include=["documents", "metadatas", "embeddings"])
        rows = {}
        ids, docs, metas, vectors = (result.get(field) for field in ("ids", "documents", "metadatas", "embeddings"))
        if (ids is None or docs is None or metas is None or vectors is None
                or any(len(values) != count for values in (ids, docs, metas, vectors))):
            raise ValueError("vector_read_cardinality_mismatch")
        for chunk_id, text, projection, vector in zip(ids, docs, metas, vectors, strict=True):
            if (not isinstance(chunk_id, str) or not chunk_id or chunk_id in rows
                    or not isinstance(text, str) or not text.strip()
                    or not isinstance(projection, dict) or set(projection) != _FIELDS
                    or any(not isinstance(value, str) or not value for value in projection.values())
                    or not _SHA256.fullmatch(projection["canonical_sha256"])):
                raise ValueError("vector_row_projection_invalid")
            rows[chunk_id] = {"text_search": text, **projection, "vector": self._vector(vector.tolist())}
        if content_digest(rows) != metadata["p4_rows_sha256"] or collection.count() != count:
            raise ValueError("vector_rows_integrity_mismatch")
        return rows

    def query(self, directory: Path, descriptor: VectorDescriptor, query_vector: list[float], *,
              allowed_source_ids, k: int) -> list[tuple[str, float]]:
        descriptor = self._checked_descriptor(descriptor)
        vector = self._vector(query_vector)
        if type(k) is not int or k < 1:
            raise ValueError("vector_query_k_invalid")
        if isinstance(allowed_source_ids, (str, bytes)) or allowed_source_ids is None:
            raise ValueError("vector_query_allowed_sources_invalid")
        allowed = set(allowed_source_ids)
        if any(not isinstance(source_id, str) or not source_id for source_id in allowed):
            raise ValueError("vector_query_allowed_sources_invalid")
        rows = self.read(directory, descriptor)
        eligible = {chunk_id for chunk_id, row in rows.items() if row["source_id"] in allowed}
        if not eligible:
            return []
        with self._client(directory, create=False) as client:
            collection = client.get_collection(descriptor.collection, embedding_function=None)
            results = collection.query(query_embeddings=[vector], n_results=min(k, len(eligible)),
                                       where={"source_id": {"$in": sorted(allowed)}},
                                       include=["distances", "metadatas", "documents"])
        output = []
        ids, distances, projections, texts = (results[field][0] for field in ("ids", "distances", "metadatas", "documents"))
        if any(len(values) != len(ids) for values in (distances, projections, texts)):
            raise ValueError("vector_query_cardinality_invalid")
        for chunk_id, distance, projection, text in zip(ids, distances, projections, texts, strict=True):
            if (chunk_id not in eligible or chunk_id in {key for key, _ in output}
                    or type(distance) not in {float, int} or not math.isfinite(distance)
                    or projection != {field: rows[chunk_id][field] for field in _FIELDS}
                    or text != rows[chunk_id]["text_search"]):
                raise ValueError("vector_query_projection_or_scope_invalid")
            output.append((chunk_id, float(distance)))
        return sorted(output, key=lambda pair: (pair[1], pair[0]))

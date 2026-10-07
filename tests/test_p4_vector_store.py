"""Dense parity with real embedded and private loopback Chroma backends."""
from pathlib import Path
from contextlib import contextmanager
import os
import socket
import subprocess
import sys
import threading
import time
from urllib.request import urlopen
from uuid import uuid4

import chromadb
from chromadb.config import Settings
import pytest

from app.contracts import EvidenceChunk
from app.retrieval.generation_contracts import VectorDescriptor, content_digest
from app.retrieval.vector_store import ChromaVectorStore


def chunk(name, source="s1", text="Não pagar, salvo após condição 🧾."):
    return EvidenceChunk(chunk_id=name, unit_id="u:" + name, source_id=source,
                         instrument_id="i:" + source, doc_version=1, file_id=source,
                         file_hash="a" * 64, parser_name="synthetic", parser_version="1",
                         configuration_version="synthetic-v1", derivation_key="b" * 64,
                         block_ids=["b:" + name], spans=[dict(source_id=source, block_id="b:" + name,
                                                             start=0, end=len(text))],
                         verbatim_text=text, text_search="Contrato sintético\n" + text,
                         parties=[dict(name="Parte sintética")], location=dict(label="Local sintético"),
                         approval_state="approved", review_record_id="synthetic-review")


class LocalServer:
    """Own only this fixture's child; never stop unrelated services."""
    def __init__(self, directory):
        self.directory = directory
        with socket.socket() as available:
            available.bind(("127.0.0.1", 0))
            self.port = available.getsockname()[1]
        self.process = None
        self.log = None

    def start(self):
        cli = Path(sys.executable).with_name("chroma.exe" if os.name == "nt" else "chroma")
        self.log = (self.directory / "server.log").open("ab")
        self.process = subprocess.Popen(
            [str(cli), "run", "--path", str(self.directory / "db"), "--host", "127.0.0.1",
             "--port", str(self.port)], stdout=self.log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            env={**os.environ, "ANONYMIZED_TELEMETRY": "FALSE"})
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AssertionError("Owned Chroma server failed: " +
                                     (self.directory / "server.log").read_text(errors="replace"))
            try:
                with urlopen(f"http://127.0.0.1:{self.port}/api/v2/heartbeat", timeout=0.5) as ready:
                    if ready.status == 200:
                        return
            except OSError:
                time.sleep(0.05)
        raise AssertionError("Owned Chroma server did not become ready within 30 seconds")

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=10)
        if self.log is not None:
            self.log.close()


@pytest.fixture(scope="module")
def real_server(tmp_path_factory):
    server = LocalServer(tmp_path_factory.mktemp("p4-chroma-private"))
    try:
        server.start()
        yield server
    finally:
        server.stop()


@pytest.fixture(params=["embedded", "server"])
def backend(request, tmp_path, real_server):
    mode = request.param
    kwargs = dict(mode=mode, dimension=3)
    if mode == "server":
        kwargs.update(host="127.0.0.1", port=real_server.port)
    return ChromaVectorStore(**kwargs), tmp_path, "p4-" + uuid4().hex


@contextmanager
def raw_collection(directory, descriptor):
    settings = Settings(anonymized_telemetry=False, _env_file=None)
    if descriptor.mode == "embedded":
        client = chromadb.PersistentClient(path=str(directory / "chroma"), settings=settings)
    else:
        client = chromadb.HttpClient(host=descriptor.host, port=descriptor.port, settings=settings)
    try:
        yield client.get_collection(descriptor.collection, embedding_function=None)
    finally:
        client.close()


def test_exact_round_trip_reload_and_projection_digest(backend):
    store, directory, collection_id = backend
    chunks = [chunk("c1"), chunk("c2", "s2", "Condição integral e exceção.")]
    vectors = {"c1": [1.0, 0.0, 0.0], "c2": [0.0, 1.0, 0.0]}
    descriptor = store.write(directory, collection_id, chunks, vectors)
    reloaded = ChromaVectorStore(mode=store.mode, dimension=3, host=store.host, port=store.port)
    values = reloaded.read(directory, descriptor)
    assert set(values) == {"c1", "c2"}
    for value in chunks:
        assert values[value.chunk_id] == {
            "text_search": value.text_search, "canonical_sha256": content_digest(value),
            "vector": vectors[value.chunk_id], "source_id": value.source_id,
            "unit_id": value.unit_id, "instrument_id": value.instrument_id}


def test_source_filter_runs_before_rank_and_never_falls_back(backend):
    store, directory, collection_id = backend
    descriptor = store.write(directory, collection_id, [chunk("near", "prohibited"), chunk("far", "allowed")],
                             {"near": [1.0, 0.0, 0.0], "far": [0.0, 1.0, 0.0]})
    assert store.query(directory, descriptor, [1.0, 0.0, 0.0], allowed_source_ids={"allowed"}, k=1) == [("far", 2.0)]
    assert store.query(directory, descriptor, [1.0, 0.0, 0.0], allowed_source_ids=set(), k=1) == []
    assert store.query(directory, descriptor, [1.0, 0.0, 0.0], allowed_source_ids={"missing"}, k=1) == []


def test_write_never_reuses_existing_generation_collection(backend):
    store, directory, collection_id = backend
    descriptor = store.write(directory, collection_id, [chunk("c1")], {"c1": [1.0, 0.0, 0.0]})
    before = store.read(directory, descriptor)
    with pytest.raises(Exception):
        store.write(directory, collection_id, [chunk("c2")], {"c2": [0.0, 1.0, 0.0]})
    assert store.read(directory, descriptor) == before


@pytest.mark.parametrize("mutation", ["document", "digest", "source", "vector", "extra", "missing", "collection_metadata"])
def test_backend_tampering_fails_read_and_query(backend, mutation):
    store, directory, collection_id = backend
    descriptor = store.write(directory, collection_id, [chunk("c1")], {"c1": [1.0, 0.0, 0.0]})
    with raw_collection(directory, descriptor) as collection:
        if mutation == "document":
            collection.update(ids=["c1"], documents=["Pagar sempre."], embeddings=[[1.0, 0.0, 0.0]])
        elif mutation in {"digest", "source"}:
            collection.update(ids=["c1"], metadatas=[{"canonical_sha256": "c" * 64} if mutation == "digest"
                                                    else {"source_id": "forged"}])
        elif mutation == "vector":
            collection.update(ids=["c1"], embeddings=[[0.0, 1.0, 0.0]])
        elif mutation == "extra":
            collection.add(ids=["forged"], embeddings=[[0.0, 1.0, 0.0]], documents=["Fabricado"],
                           metadatas=[{"source_id": "synthetic"}])
        elif mutation == "missing":
            collection.delete(ids=["c1"])
        else:
            collection.modify(metadata={"p4_dimension": 999})
    with pytest.raises(ValueError):
        store.read(directory, descriptor)
    with pytest.raises(ValueError):
        store.query(directory, descriptor, [1.0, 0.0, 0.0], allowed_source_ids={"s1"}, k=1)


@pytest.mark.parametrize("vectors", [
    {}, {"extra": [1.0, 0.0, 0.0]}, {"c1": [1.0, 0.0]},
    {"c1": [float("nan"), 0.0, 0.0]}, {"c1": [float("inf"), 0.0, 0.0]},
    {"c1": [True, 0.0, 0.0]}, {"c1": [1e100, 0.0, 0.0]},
    {"c1": [10 ** 1000, 0.0, 0.0]},
])
def test_invalid_vector_sets_fail_before_creation(tmp_path, vectors):
    store = ChromaVectorStore(mode="embedded", dimension=3)
    with pytest.raises(ValueError):
        store.write(tmp_path, "invalid-vectors", [chunk("c1")], vectors)
    assert not (tmp_path / "chroma").exists()


def test_duplicate_chunks_and_empty_generations_fail_before_creation(tmp_path):
    store = ChromaVectorStore(mode="embedded", dimension=3)
    for chunks in ([], [chunk("c1"), chunk("c1")]):
        with pytest.raises(ValueError):
            store.write(tmp_path, "invalid-chunks", chunks, {"c1": [1.0, 0.0, 0.0]})
    assert not (tmp_path / "chroma").exists()


@pytest.mark.parametrize("kwargs", [dict(mode="auto", dimension=3), dict(mode="embedded", dimension=True),
                                  dict(mode="embedded", dimension=0), dict(mode="server", dimension=3),
                                  dict(mode="server", dimension=3, host="example.com", port=8000),
                                  dict(mode="embedded", dimension=3, host="127.0.0.1", port=8000)])
def test_backend_settings_are_explicit_and_local(kwargs):
    with pytest.raises(ValueError):
        ChromaVectorStore(**kwargs)


def test_incompatible_dimension_or_descriptor_never_creates_backend(backend):
    store, directory, collection_id = backend
    descriptor = store.write(directory, collection_id, [chunk("c1")], {"c1": [1.0, 0.0, 0.0]})
    wrong = ChromaVectorStore(mode=store.mode, dimension=4, host=store.host, port=store.port)
    with pytest.raises(ValueError):
        wrong.read(directory, descriptor)
    changed = descriptor.model_copy(update={"mode": "embedded" if store.mode == "server" else "server"})
    with pytest.raises(ValueError):
        store.read(directory, changed)


def test_real_server_restart_preserves_collection_and_unavailability_fails_closed(tmp_path):
    server = LocalServer(tmp_path)
    try:
        server.start()
        store = ChromaVectorStore(mode="server", dimension=3, host="127.0.0.1", port=server.port)
        descriptor = store.write(tmp_path, "restart-" + uuid4().hex, [chunk("c1")], {"c1": [1.0, 0.0, 0.0]})
        before = store.read(tmp_path, descriptor)
        server.stop()
        with pytest.raises(Exception):
            store.read(tmp_path, descriptor)
        with pytest.raises(Exception):
            store.query(tmp_path, descriptor, [1.0, 0.0, 0.0], allowed_source_ids={"s1"}, k=1)
        server.start()
        assert store.read(tmp_path, descriptor) == before
    finally:
        server.stop()


def test_float32_round_trip_is_explicit_and_equal_in_both_backends(backend):
    store, directory, collection_id = backend
    descriptor = store.write(directory, collection_id, [chunk("c1")], {"c1": [0.1, 0.2, 0.3]})
    assert store.read(directory, descriptor)["c1"]["vector"] == [
        0.10000000149011612, 0.20000000298023224, 0.30000001192092896]


def test_missing_embedded_reload_never_materializes_database(tmp_path):
    store = ChromaVectorStore(mode="embedded", dimension=3)
    with pytest.raises(ValueError):
        store.read(tmp_path, VectorDescriptor(mode="embedded", collection="missing-store", path="chroma"))
    assert not (tmp_path / "chroma").exists()


def test_actual_stalled_loopback_peer_times_out_before_any_fallback(tmp_path):
    release = threading.Event()
    accepted = threading.Event()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]

        def stalled_peer():
            with listener.accept()[0] as connection:
                connection.recv(65536)
                accepted.set()
                release.wait(timeout=10)

        worker = threading.Thread(target=stalled_peer, daemon=True)
        worker.start()
        store = ChromaVectorStore(mode="server", dimension=3, host="127.0.0.1", port=port)
        descriptor = VectorDescriptor(mode="server", collection="stalled-server", host="127.0.0.1", port=port)
        started = time.monotonic()
        try:
            with pytest.raises(Exception):
                store.read(tmp_path, descriptor)
            assert accepted.is_set()
            assert 4.5 <= time.monotonic() - started < 8.0
            assert not (tmp_path / "chroma").exists()
        finally:
            release.set()
            worker.join(timeout=2)


def test_embedded_and_real_server_queries_have_exact_filtered_parity(tmp_path, real_server):
    chunks = [chunk("nearest", "allowed"), chunk("middle", "blocked"), chunk("farthest", "allowed")]
    vectors = {"nearest": [1.0, 0.0, 0.0], "middle": [0.0, 1.0, 0.0], "farthest": [-1.0, 0.0, 0.0]}
    backends = [ChromaVectorStore(mode="embedded", dimension=3),
                ChromaVectorStore(mode="server", dimension=3, host="127.0.0.1", port=real_server.port)]
    outputs, rows = [], []
    for store in backends:
        descriptor = store.write(tmp_path, "parity-" + uuid4().hex, chunks, vectors)
        rows.append(store.read(tmp_path, descriptor))
        outputs.append(store.query(tmp_path, descriptor, [1.0, 0.0, 0.0], allowed_source_ids={"allowed"}, k=3))
    assert rows[0] == rows[1]
    assert outputs == [[("nearest", 0.0), ("farthest", 4.0)]] * 2


def test_real_server_adapter_ignores_environment_proxy_routing(tmp_path, real_server, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("CHROMA_SERVER_HOST", "invalid.example")
    monkeypatch.setenv("CHROMA_CLIENT_AUTH_PROVIDER", "invalid.injected.Provider")
    store = ChromaVectorStore(mode="server", dimension=3, host="127.0.0.1", port=real_server.port)
    descriptor = store.write(tmp_path, "proxy-" + uuid4().hex, [chunk("c1")], {"c1": [1.0, 0.0, 0.0]})
    assert store.query(tmp_path, descriptor, [1.0, 0.0, 0.0], allowed_source_ids={"s1"}, k=1) == [("c1", 0.0)]


@pytest.mark.parametrize("k,allowed,vector", [(True, {"s1"}, [1.0, 0.0, 0.0]),
                                           (0, {"s1"}, [1.0, 0.0, 0.0]),
                                           (1, "s1", [1.0, 0.0, 0.0]),
                                           (1, {""}, [1.0, 0.0, 0.0]),
                                           (1, {"s1"}, [1.0, 0.0]),
                                           (1, {"s1"}, [float("nan"), 0.0, 0.0])])
def test_invalid_query_contract_is_rejected_before_backend_creation(tmp_path, k, allowed, vector):
    store = ChromaVectorStore(mode="embedded", dimension=3)
    descriptor = VectorDescriptor(mode="embedded", collection="query-contract", path="chroma")
    with pytest.raises(ValueError):
        store.query(tmp_path, descriptor, vector, allowed_source_ids=allowed, k=k)
    assert not (tmp_path / "chroma").exists()


def test_chroma_version_change_requires_adapter_requalification(monkeypatch):
    monkeypatch.setattr(chromadb, "__version__", "unqualified-version")
    with pytest.raises(ValueError, match="version_requires_requalification"):
        ChromaVectorStore(mode="embedded", dimension=3)

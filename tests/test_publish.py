"""Publication pipeline on synthetic staging: fresh ledger, embedding cache, switch."""
import hashlib
import json
import shutil
from types import SimpleNamespace

import pytest

from app.embeddings.qwen import QwenProfile
from app.publish import CachedEmbeddings, publish, read_current
from app.workbench.store import WorkbenchStore
from tests.p6_corpus import SyntheticBudget
from tests.test_corpus_build import _stage

IDENTITY = {"artifact_sha256": "a" * 64, "implementation_sha256": "b" * 64,
            "versions": {"torch": "t", "transformers": "x", "tokenizers": "k", "safetensors": "s"},
            "profile": QwenProfile().specification}


class FakeEncoder:
    identity = IDENTITY

    def __init__(self):
        self.calls = 0

    def embed_documents(self, texts):
        self.calls += len(texts)
        return [self._vector(text) for text in texts]

    def embed_query(self, text):
        return self._vector(text)

    @staticmethod
    def _vector(text):
        seed = hashlib.sha256(text.encode()).digest()
        raw = [(seed[i % 32] - 128) / 128 for i in range(1024)]
        norm = sum(v * v for v in raw) ** .5
        return [v / norm for v in raw]


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    monkeypatch.setattr("app.corpus_build.qwen_identity", lambda artifact: dict(IDENTITY))
    root = tmp_path / "data"
    staging, raw = _stage(tmp_path / "src")
    shutil.copytree(staging, root / "staging" / "s1")
    shutil.copytree(raw, root / "raw" / "files")
    return root


def run(root, encoder, calls, **extra):
    embeddings = CachedEmbeddings(root / "models", root / "embedding-cache", factory=lambda: encoder,
                                  identity=dict(IDENTITY))
    return publish(root, reviewer="revisor", reason="publicação de teste", journal_id="j-test",
                   budget=SyntheticBudget(), embeddings=embeddings,
                   service=lambda action: calls.append(action) or SimpleNamespace(returncode=0), **extra), embeddings


def test_first_publication_promotes_and_activates_with_pointer(data_root):
    calls = []
    report, embeddings = run(data_root, FakeEncoder(), calls, activate=True, name="pub-a")
    assert calls == ["stop", "start"]
    assert read_current(data_root) == {"pub": "pub-a", "journal_id": "j-test"}
    assert (data_root / "pubs" / "pub-a" / "indexes" / "active.json").is_file()
    assert report["eligible_sources"] == 3 and [r["instrument_id"] for r in report["reserved"]] == ["doc:103"]
    assert embeddings.misses == report["chunks"] and embeddings.hits == 0
    assert WorkbenchStore(data_root / "workbench.sqlite").publications()[0]["state"] == "published"


def test_second_publication_reuses_embeddings_and_keeps_live_journal_blocks(data_root):
    from app.retrieval.policy import SQLitePolicyJournal
    calls, encoder = [], FakeEncoder()
    run(data_root, encoder, calls, activate=True, name="pub-a")
    first_calls = encoder.calls
    live = SQLitePolicyJournal(data_root / "pubs" / "pub-a" / "authority" / "policy.sqlite", journal_id="j-test")
    live.block("source", "doc:102:v1:file:f102", "retirado")
    store = WorkbenchStore(data_root / "workbench.sqlite")
    store.save_document("doc:103:v1:file:f103", reviewer="revisor", parties=[
        {"name": "Gama Comércio Ltda", "role": "contratante", "clean_identifier": "22333444000155"},
        {"name": "Andrade Advogados", "role": "contratada"}])
    report, embeddings = run(data_root, encoder, calls, activate=True, name="pub-b")
    assert report["reserved"] == [] and "doc:103" not in {r["instrument_id"] for r in report["rejected"]}
    assert report["eligible_sources"] == 3  # 103 joined, 102 left (blocked in the live journal)
    assert [e["source_id"] for e in report["excluded"]] == ["doc:102:v1:file:f102"]
    assert embeddings.hits > 0 and encoder.calls - first_calls == embeddings.misses < report["chunks"]
    copied = SQLitePolicyJournal(data_root / "pubs" / "pub-b" / "authority" / "policy.sqlite", journal_id="j-test")
    assert "doc:102:v1:file:f102" in copied.snapshot().blocked_source_ids
    assert read_current(data_root)["pub"] == "pub-b"


def test_failed_publication_is_recorded_and_pointer_unchanged(data_root, monkeypatch):
    calls = []
    run(data_root, FakeEncoder(), calls, activate=True, name="pub-a")
    monkeypatch.setattr("app.publish.build_reviewed_bundle", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    with pytest.raises(ValueError):
        run(data_root, FakeEncoder(), calls, activate=True, name="pub-b")
    assert read_current(data_root)["pub"] == "pub-a"
    assert WorkbenchStore(data_root / "workbench.sqlite").publications()[0]["state"] == "failed"


def test_embedding_cache_refuses_encoder_with_different_identity(tmp_path):
    other = FakeEncoder()
    other.identity = dict(IDENTITY, artifact_sha256="c" * 64)
    cache = CachedEmbeddings(tmp_path, tmp_path / "c", factory=lambda: other, identity=dict(IDENTITY))
    with pytest.raises(ValueError):
        cache.embed_documents(["texto"])


def test_current_pointer_rejects_path_traversal(tmp_path):
    (tmp_path / "current.json").write_text(json.dumps({"pub": "../x", "journal_id": "j"}), encoding="utf-8")
    with pytest.raises(ValueError):
        read_current(tmp_path)



def test_service_start_failure_is_reported_not_silent(data_root):
    calls = []
    embeddings = CachedEmbeddings(data_root / "models", data_root / "embedding-cache", factory=FakeEncoder,
                                  identity=dict(IDENTITY))
    failing = lambda action: calls.append(action) or SimpleNamespace(returncode=1 if action == "start" else 0)
    with pytest.raises(RuntimeError):
        publish(data_root, reviewer="revisor", reason="teste", journal_id="j-test", budget=SyntheticBudget(),
                embeddings=embeddings, service=failing, activate=True, name="pub-a")
    assert calls == ["stop", "start"]
    assert WorkbenchStore(data_root / "workbench.sqlite").publications()[0]["state"] == "activation_failed"


def test_activation_requires_a_promoted_publication(data_root):
    from app.publish import activate_publication
    for name in ("pub-inexistente", "../fora"):
        with pytest.raises(ValueError):
            activate_publication(data_root, name, "j", service=lambda action: SimpleNamespace(returncode=0))
    assert read_current(data_root) is None


def test_publication_claim_is_exclusive(tmp_path):
    store = WorkbenchStore(tmp_path / "wb.sqlite")
    first = store.claim_publication("pub-1", "r")
    assert first and store.claim_publication("pub-2", "r") is None
    store.finish_publication(first, "published", {})
    assert store.claim_publication("pub-3", "r")

"""P6 end to end on real local backends: private loopback Chroma server and the fixed local Qwen artifact."""
from pathlib import Path

import pytest

from app.retrieval.vector_store import ChromaVectorStore
from tests.p6_corpus import build_p6_environment
from tests.test_p4_vector_store import LocalServer
from tests.test_p6_pipeline import BASE, Q_FEES, Scripted, ask, pick, plan, service


@pytest.fixture
def chroma_server(tmp_path):
    server = LocalServer(tmp_path / "server")
    (tmp_path / "server").mkdir()
    server.start()
    try:
        yield server
    finally:
        server.stop()


def assert_governed_answer(env, *, scope="linked_instruments"):
    out = ask(service(env, Scripted(pick(env, "Cláusula 1ª")), scope=scope), Q_FEES, plan(scope))
    assert out.payload.status == "answered"
    canonical = {u.unit_id: u for u in env.bundle.units}
    assert all(p.citation.quote == canonical[p.unit_id].verbatim_text for p in out.payload.proof)
    expected = {"doc:101"} if scope == "original_text" else {"doc:101", "doc:102"}
    assert {p.citation.contract_id for p in out.payload.proof} == expected
    env.block_source("doc:101")
    return out


def test_synthetic_encoder_with_real_private_chroma_server(tmp_path, chroma_server):
    env = build_p6_environment(
        tmp_path / "env", vector_store=ChromaVectorStore(mode="server", dimension=3, host="127.0.0.1", port=chroma_server.port))
    assert_governed_answer(env)


@pytest.fixture(scope="module")
def qwen(request):
    artifact = request.config.getoption("--qwen-artifact")
    if artifact is None:
        pytest.skip("explicit existing local Qwen artifact required; never downloads")
    from app.embeddings.qwen import QwenEmbeddings
    from app.ingestion.evidence import QwenTokenBudget
    return QwenEmbeddings(Path(artifact)), QwenTokenBudget(Path(artifact))


def test_real_qwen_embedded_backend_answers_and_revokes(tmp_path, qwen):
    encoder, budget = qwen
    env = build_p6_environment(tmp_path / "env", encoder=encoder, budget=budget, embedding_dimension=1024,
                               vector_store=ChromaVectorStore(mode="embedded", dimension=1024))
    out = assert_governed_answer(env)
    assert out.audit.prompt_tokens > 0 and out.audit.closure_tokens > 0
    after = ask(service(env, Scripted(pick(env, "Cláusula 1ª"))), Q_FEES, plan())
    assert (after.payload.status, after.payload.reason_code) == ("abstained", "insufficient_evidence")
    assert "50.000" not in after.payload.model_dump_json() and "75.000" not in after.payload.model_dump_json()


def test_real_qwen_with_real_private_chroma_server_original_text_scope(tmp_path, qwen, chroma_server):
    encoder, budget = qwen
    env = build_p6_environment(
        tmp_path / "env", encoder=encoder, budget=budget, embedding_dimension=1024,
        vector_store=ChromaVectorStore(mode="server", dimension=1024, host="127.0.0.1", port=chroma_server.port))
    out = assert_governed_answer(env, scope="original_text")
    assert any(w.startswith("known_modifier:") for w in out.payload.warnings)
    assert BASE in {p.citation.contract_id for p in out.payload.proof}

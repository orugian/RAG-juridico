import pytest

from app.config import Settings
from app.embeddings.artifacts import MODEL_ID, REVISION
from app.embeddings.factory import build_local_embeddings


@pytest.mark.parametrize('changes', [
    {'embedding_backend': 'unconfigured'}, {'embedding_backend': 'hash'},
    {'embedding_model': 'other/model'}, {'embedding_revision': 'main'},
    {'embedding_dimension': 768}, {'embedding_artifact_dir': None},
])
def test_factory_rejects_inconsistent_selection_without_loading(monkeypatch, tmp_path, changes):
    def forbidden(*args, **kwargs):
        pytest.fail('Model must not load for an incompatible selection')
    monkeypatch.setattr('app.embeddings.factory.QwenEmbeddings', forbidden)
    values = dict(embedding_backend='local', embedding_model=MODEL_ID,
                  embedding_revision=REVISION, embedding_dimension=1024,
                  embedding_artifact_dir=tmp_path)
    settings = Settings(**(values | changes))
    with pytest.raises(ValueError):
        build_local_embeddings(settings)


def test_factory_binds_settings_and_has_no_hash_fallback(monkeypatch, tmp_path):
    calls = []
    sentinel = object()
    def capture(path, profile):
        calls.append((path, profile))
        return sentinel
    monkeypatch.setattr('app.embeddings.factory.QwenEmbeddings', capture)
    settings = Settings(embedding_backend='local', embedding_model=MODEL_ID,
                        embedding_revision=REVISION, embedding_dimension=1024,
                        embedding_artifact_dir=tmp_path, embedding_max_input_tokens=512)
    assert build_local_embeddings(settings) is sentinel
    assert calls[0][0] == tmp_path and calls[0][1].max_input_tokens == 512

import hashlib
import io
import json
from pathlib import Path

import pytest

from app.embeddings import artifacts


@pytest.fixture
def tiny_artifact(monkeypatch, tmp_path):
    body = b'synthetic-safe-weights'
    monkeypatch.setattr(artifacts, 'FILES', {'model.safetensors': (len(body), hashlib.sha256(body).hexdigest())})
    root = tmp_path / 'model'
    root.mkdir()
    (root / 'model.safetensors').write_bytes(body)
    (root / 'artifact.json').write_text(json.dumps(artifacts.expected_manifest()), encoding='utf-8')
    return root, body


def test_valid_artifact_digest_and_read_only(tiny_artifact):
    path, _ = tiny_artifact
    before = {p.name: p.read_bytes() for p in path.iterdir()}
    assert len(artifacts.verify_artifact(path)) == 64
    assert before == {p.name: p.read_bytes() for p in path.iterdir()}


def test_rehashed_manifest_cannot_approve_modified_weights(tiny_artifact):
    path, body = tiny_artifact
    modified = body.replace(b'safe', b'evil')
    (path / 'model.safetensors').write_bytes(modified)
    manifest = artifacts.expected_manifest()
    manifest['files']['model.safetensors']['sha256'] = hashlib.sha256(modified).hexdigest()
    (path / 'artifact.json').write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(artifacts.ArtifactError):
        artifacts.verify_artifact(path)


@pytest.mark.parametrize('mutation', ['truncated', 'same_size', 'extra_code', 'wrong_revision', 'missing_manifest', 'invalid_json'])
def test_artifact_corruption_fails_closed(tiny_artifact, mutation):
    path, body = tiny_artifact
    if mutation == 'truncated':
        (path / 'model.safetensors').write_bytes(body[:-1])
    elif mutation == 'same_size':
        (path / 'model.safetensors').write_bytes(b'x' * len(body))
    elif mutation == 'extra_code':
        (path / 'modeling_custom.py').write_text('pass')
    elif mutation == 'wrong_revision':
        manifest = artifacts.expected_manifest()
        manifest['revision'] = 'main'
        (path / 'artifact.json').write_text(json.dumps(manifest))
    elif mutation == 'missing_manifest':
        (path / 'artifact.json').unlink()
    else:
        (path / 'artifact.json').write_text('{')
    with pytest.raises(artifacts.ArtifactError):
        artifacts.verify_artifact(path)


def test_explicit_download_uses_only_pinned_public_url(monkeypatch, tiny_artifact, tmp_path):
    _, body = tiny_artifact
    urls = []
    def fake_open(url, timeout):
        urls.append(url)
        assert timeout == 60
        return io.BytesIO(body)
    monkeypatch.setattr(artifacts, 'urlopen', fake_open)
    output = artifacts.download_artifact(tmp_path / 'download')
    assert urls == [f'https://huggingface.co/{artifacts.MODEL_ID}/resolve/{artifacts.REVISION}/model.safetensors']
    assert artifacts.verify_artifact(output)
    artifacts.download_artifact(tmp_path / 'download')
    assert len(urls) == 1  # Existing complete artifact: offline reuse.


def test_bad_download_remains_unpublished(monkeypatch, tiny_artifact, tmp_path):
    _, body = tiny_artifact
    monkeypatch.setattr(artifacts, 'urlopen', lambda *args, **kwargs: io.BytesIO(body + b'excess'))
    destination = tmp_path / 'download'
    with pytest.raises(artifacts.ArtifactError):
        artifacts.download_artifact(destination)
    assert not (destination / f'qwen3-embedding-0.6b-{artifacts.REVISION}').exists()
    assert len(list(destination.glob('.download-*'))) == 1

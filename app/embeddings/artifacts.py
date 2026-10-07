"""Explicit download of public, pinned files; runtime verification stays offline."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.request import urlopen
from uuid import uuid4

MODEL_ID = 'Qwen/Qwen3-Embedding-0.6B'
REVISION = '97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3'
# SHA-256 of the selected revision, not hashes supplied by a local manifest.
FILES = {
    'config.json': (727, 'b5bf1f51fc45be473a54718cef92448d90a1be001bf9b9a44b8c7f10a19feaa9'),
    'tokenizer_config.json': (9706, '253153d0738ceb4c668d2eff957714dd2bea0b56de772a9fdccd96cbf517e6a0'),
    'vocab.json': (2776833, 'ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910'),
    'merges.txt': (1671853, '8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5'),
    'tokenizer.json': (11423705, 'def76fb086971c7867b829c23a26261e38d9d74e02139253b38aeb9df8b4b50a'),
    'model.safetensors': (1191586416, '0437e45c94563b09e13cb7a64478fc406947a93cb34a7e05870fc8dcd48e23fd'),
    'README.md': (17237, 'c34d9b7e5a267ad3fdd13227a253686bc90844ff4744a2a6a86c7c905e3d06f3'),
    '1_Pooling/config.json': (313, '37bf193fa101f19101bfad9c31d3eb0f786e247b7b1e5cb7f007d730eed1ddbd'),
}


class ArtifactError(ValueError):
    """Model files are absent or incompatible; never includes source text."""


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def expected_manifest() -> dict:
    return {
        'schema': 'qwen-artifact-v1', 'model_id': MODEL_ID, 'revision': REVISION,
        'license': 'Apache-2.0',
        'files': {name: {'bytes': size, 'sha256': digest} for name, (size, digest) in FILES.items()},
    }


def verify_artifact(directory: Path) -> str:
    """Read-only verification against pinned hashes. No Hub access or repair."""
    directory = Path(directory)
    try:
        manifest_path = directory / 'artifact.json'
        if directory.is_symlink() or manifest_path.is_symlink() or manifest_path.stat().st_size > 16384:
            raise ArtifactError('Invalid model artifact')
        manifest_bytes = manifest_path.read_bytes()
        if json.loads(manifest_bytes) != expected_manifest():
            raise ArtifactError('Model manifest differs from pinned revision')
        expected_paths = {*FILES, 'artifact.json'}
        actual = {p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_file()}
        if actual != expected_paths or any(p.is_symlink() for p in directory.rglob('*')):
            raise ArtifactError('Unexpected model files')
        for name, (size, digest) in FILES.items():
            path = directory / name
            if path.stat().st_size != size or digest_file(path) != digest:
                raise ArtifactError('Model artifact integrity check failed')
        return hashlib.sha256(manifest_bytes).hexdigest()
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError('Model artifact missing or unreadable') from exc


def download_artifact(output_root: Path, progress=None) -> Path:
    """Only callable explicitly: receives no questions, credentials or corpus."""
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / f'qwen3-embedding-0.6b-{REVISION}'
    if target.exists():
        verify_artifact(target)
        return target
    stage = root / f'.download-{uuid4()}'
    stage.mkdir()
    for name, (size, expected_hash) in FILES.items():
        path = stage / name
        path.parent.mkdir(parents=True, exist_ok=True)
        url = f'https://huggingface.co/{MODEL_ID}/resolve/{REVISION}/{name}'
        digest, count = hashlib.sha256(), 0
        with urlopen(url, timeout=60) as response, path.open('xb') as stream:
            for block in iter(lambda: response.read(1024 * 1024), b''):
                count += len(block)
                if count > size:
                    raise ArtifactError('Downloaded file exceeds pinned size')
                digest.update(block)
                stream.write(block)
                if progress:
                    progress(name, count, size)
        if count != size or digest.hexdigest() != expected_hash:
            raise ArtifactError('Downloaded file differs from pinned revision')
    (stage / 'artifact.json').write_text(json.dumps(expected_manifest(), sort_keys=True, indent=2) + '\n', encoding='utf-8')
    verify_artifact(stage)
    # Both paths are resolved children of the explicitly named root; never replace/delete.
    if stage.resolve().parent != root or target.resolve().parent != root:
        raise ArtifactError('Model publication path escaped destination')
    stage.rename(target)
    return target

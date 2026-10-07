"""Adversarial regression: fast tokenizer state must share the inference lock."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time

import pytest

from app.embeddings import qwen


class BorrowingTokenizer:
    def __init__(self):
        self.borrowed = Event()

    def encode(self, text, **kwargs):
        if self.borrowed.is_set():
            raise RuntimeError('Already borrowed')
        return list(text)


def test_validation_cannot_borrow_tokenizer_during_inference(monkeypatch, tmp_path):
    entered, release, second_started = Event(), Event(), Event()
    tokenizer = BorrowingTokenizer()
    class Runtime:
        def encode(self, texts):
            tokenizer.borrowed.set()
            entered.set()
            assert release.wait(2)
            tokenizer.borrowed.clear()
            return [[1.0] + [0.0] * 1023 for _ in texts]
    runtime = Runtime()
    runtime.tokenizer = tokenizer
    monkeypatch.setattr(qwen, 'load_runtime', lambda *args: runtime)
    encoder = qwen.QwenEmbeddings(tmp_path)
    def second_call():
        second_started.set()
        return encoder.embed_documents(['valid second request'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(encoder.embed_documents, ['valid first request'])
        try:
            assert entered.wait(2)
            second = pool.submit(second_call)
            assert second_started.wait(2)
            time.sleep(.05)
        finally:
            release.set()
        assert first.result(timeout=2) == second.result(timeout=2)


def test_inventory_identity_includes_grouping_and_schema_code(monkeypatch, tmp_path):
    from app.embeddings import experiments
    package = tmp_path / 'app' / 'embeddings'
    package.mkdir(parents=True)
    for name in ('qwen.py', 'artifacts.py', 'experiments.py', 'factory.py', '__main__.py'):
        (package / name).write_text('synthetic')
    (tmp_path / 'app' / 'config.py').write_text('synthetic')
    (tmp_path / 'app' / 'identifiers.py').write_text('synthetic')
    ingestion = tmp_path / 'app' / 'ingestion'
    ingestion.mkdir()
    for name in ('chunker.py', 'schemas.py', 'temporal.py', 'metadata_extractor.py'):
        (ingestion / name).write_text('original')
    retrieval = tmp_path / 'app' / 'retrieval'
    retrieval.mkdir()
    (retrieval / 'lexical.py').write_text('original')
    (tmp_path / 'uv.lock').write_text('synthetic lock')
    monkeypatch.setattr(experiments, '__file__', str(package / 'experiments.py'))
    before = experiments._code_identity()
    expected = {'app/ingestion/chunker.py', 'app/ingestion/schemas.py', 'app/ingestion/temporal.py',
                'app/ingestion/metadata_extractor.py', 'app/retrieval/lexical.py'}
    assert expected <= before['code_sha256'].keys()
    (ingestion / 'chunker.py').write_text('changed grouping')
    (ingestion / 'schemas.py').write_text('changed source schema')
    (ingestion / 'temporal.py').write_text('changed date policy')
    (retrieval / 'lexical.py').write_text('changed lexical policy')
    after = experiments._code_identity()
    assert after['code_sha256']['app/ingestion/chunker.py'] != before['code_sha256']['app/ingestion/chunker.py']
    assert after['code_sha256']['app/ingestion/schemas.py'] != before['code_sha256']['app/ingestion/schemas.py']
    assert after['code_sha256']['app/ingestion/temporal.py'] != before['code_sha256']['app/ingestion/temporal.py']
    assert after['code_sha256']['app/retrieval/lexical.py'] != before['code_sha256']['app/retrieval/lexical.py']


def test_token_count_interface_does_not_expose_mutable_tokenizer(monkeypatch, tmp_path):
    class Runtime:
        tokenizer = BorrowingTokenizer()
    monkeypatch.setattr(qwen, 'load_runtime', lambda *args: Runtime())
    encoder = qwen.QwenEmbeddings(tmp_path)
    assert not hasattr(encoder, 'tokenizer')
    assert encoder.count_document_tokens('abc') == 3


def test_parallel_encoding_with_cached_real_tokenizer(monkeypatch, request):
    from pathlib import Path
    artifact = request.config.getoption('--qwen-artifact')
    if not artifact:
        pytest.skip('Requires explicitly supplied cached Qwen artifact; never downloads')
    transformers = pytest.importorskip('transformers')
    from app.embeddings.artifacts import verify_artifact
    directory = Path(artifact)
    verify_artifact(directory)
    tokenizer = transformers.AutoTokenizer.from_pretrained(
        directory, local_files_only=True, trust_remote_code=False, padding_side='left')
    class Runtime:
        def encode(self, texts):
            tokenizer(texts, padding=True, truncation=False, add_special_tokens=True)
            return [[1.0] + [0.0] * 1023 for _ in texts]
    runtime = Runtime()
    runtime.tokenizer = tokenizer
    monkeypatch.setattr(qwen, 'load_runtime', lambda *args: runtime)
    encoder = qwen.QwenEmbeddings(directory)
    texts = ['Uma cláusula válida prevê uma condição expressa. ' * 45, 'curto']
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: encoder.embed_documents(texts), range(80)))
    assert len(results) == 80 and all(len(rows) == 2 for rows in results)

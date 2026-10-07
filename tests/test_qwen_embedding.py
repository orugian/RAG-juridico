"""Synthetic TDD cases; no downloads, real contracts or human approvals."""
import math
from dataclasses import replace

import pytest

from app.embeddings.qwen import (
    QwenEmbeddings, QwenProfile, EmbeddingInputError, EmbeddingOutputError,
    MODEL_ID, REVISION, QUERY_INSTRUCTION, split_candidate_text,
)
from app.embeddings.artifacts import ArtifactError, verify_artifact


class CharacterTokenizer:
    def encode(self, text, *, add_special_tokens, truncation):
        assert add_special_tokens and not truncation
        return list(text)


class FakeRuntime:
    tokenizer = CharacterTokenizer()

    def __init__(self):
        self.batches = []
        self.bad_output = None

    def encode(self, texts):
        self.batches.append(texts)
        return self.bad_output if self.bad_output is not None else [[1.0] + [0.0] * 1023 for _ in texts]


@pytest.fixture
def encoder(monkeypatch, tmp_path):
    runtime = FakeRuntime()
    monkeypatch.setattr('app.embeddings.qwen.load_runtime', lambda *args: runtime)
    profile = QwenProfile(max_input_tokens=200, max_batch_tokens=300, max_batch_size=2)
    return QwenEmbeddings(tmp_path, profile), runtime


def test_profile_pins_choice_and_changes_fingerprint_with_budgets():
    p = QwenProfile()
    assert p.model_id == MODEL_ID and p.revision == REVISION and p.dimension == 1024
    assert p.device == 'cpu' and p.dtype == 'float32'
    assert p.fingerprint != replace(p, max_input_tokens=512).fingerprint
    with pytest.raises(ValueError):
        QwenProfile(max_input_tokens=32769)


def test_documents_plain_queries_instruction_once(encoder):
    e, r = encoder
    assert len(e.embed_documents(['Texto da cláusula.'])) == 1
    e.embed_query('Qual é o prazo?')
    assert r.batches == [['Texto da cláusula.'], [f'Instruct: {QUERY_INSTRUCTION}\nQuery:Qual é o prazo?']]


def test_no_silent_truncation_or_partial_encoding(encoder):
    e, r = encoder
    with pytest.raises(EmbeddingInputError):
        e.embed_documents(['ok', 'x' * 201])
    assert r.batches == []


@pytest.mark.parametrize('text', ['', '   ', None, 7])
def test_empty_or_non_text_rejected(encoder, text):
    e, r = encoder
    with pytest.raises(EmbeddingInputError):
        e.embed_query(text)
    assert r.batches == []


def test_empty_collection_and_request_cap(encoder):
    e, r = encoder
    assert e.embed_documents([]) == []
    with pytest.raises(EmbeddingInputError):
        e.embed_documents(['a'] * 65)
    assert not r.batches


def test_padding_budget_is_used_not_just_sum_of_lengths(encoder):
    e, r = encoder
    e.embed_documents(['x' * 200, 'short', 'a', 'b'])
    assert list(map(len, r.batches)) == [1, 2, 1]


@pytest.mark.parametrize('output', [[], [[0.0] * 1024], [[math.nan] * 1024], [[1.0] * 1023], [[2.0] + [0.0] * 1023]])
def test_bad_vectors_rejected(encoder, output):
    e, r = encoder
    r.bad_output = output
    with pytest.raises(EmbeddingOutputError):
        e.embed_documents(['a'])


def test_missing_artifact_does_not_attempt_download(tmp_path):
    with pytest.raises(ArtifactError):
        verify_artifact(tmp_path)


def test_candidate_split_keeps_unicode_whitespace_and_exact_offsets():
    raw = ' Cláusula 1.\n\nA obrigação vale.  Exceto: ação 😀.\n'
    slices = split_candidate_text(raw, CharacterTokenizer(), max_tokens=14, synthetic_context='H:')
    assert ''.join(s.text for s in slices) == raw
    assert all(raw[s.start:s.end] == s.text for s in slices)
    assert all(len('H:' + s.text) <= 14 for s in slices)
    assert slices[0].start == 0 and slices[-1].end == len(raw)
    assert all(a.end == b.start for a, b in zip(slices, slices[1:]))


def test_header_that_exhausts_budget_fails_instead_of_dropping_text():
    with pytest.raises(EmbeddingInputError):
        split_candidate_text('cláusula', CharacterTokenizer(), max_tokens=3, synthetic_context='long')


def test_split_is_deterministic_and_not_public_evidence():
    args = ('abcdefghijk', CharacterTokenizer())
    a = split_candidate_text(*args, max_tokens=4)
    b = split_candidate_text(*args, max_tokens=4)
    assert a == b and all(s.eligibility == 'pending_review' for s in a)


def test_complete_candidate_that_fits_is_not_split_by_nonmonotonic_prefix_counts():
    class NonmonotonicTokenizer:
        def encode(self, text, **kwargs):
            return list(range(2 if text == 'abcd' else len(text)))
    slices = split_candidate_text('abcd', NonmonotonicTokenizer(), max_tokens=2)
    assert len(slices) == 1 and slices[0].text == 'abcd'

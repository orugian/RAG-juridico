"""Bounded local Qwen encoder and diagnostic slices, never public legal evidence."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from importlib.metadata import version
import json
import math
from copy import deepcopy
from pathlib import Path
from threading import Lock

from langchain_core.embeddings import Embeddings

from app.embeddings.artifacts import MODEL_ID, REVISION, verify_artifact, digest_file

QUERY_INSTRUCTION = 'Given a question about a legal contract in Portuguese, retrieve the clauses, conditions and exceptions that answer the question.'


class EmbeddingInputError(ValueError):
    pass


class EmbeddingOutputError(ValueError):
    pass


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True)
class QwenProfile:
    max_input_tokens: int = 1024
    max_batch_size: int = 2
    max_batch_tokens: int = 2048
    max_request_documents: int = 64
    max_text_characters: int = 65536
    cpu_threads: int = 6

    def __post_init__(self):
        limits = ((self.max_input_tokens, 1, 32768), (self.max_batch_size, 1, 16),
                  (self.max_batch_tokens, self.max_input_tokens, 32768),
                  (self.max_request_documents, 1, 256), (self.max_text_characters, 1, 262144),
                  (self.cpu_threads, 1, 16))
        if any(type(v) is not int or not low <= v <= high for v, low, high in limits):
            raise ValueError('Invalid Qwen resource profile')

    @property
    def model_id(self):
        return MODEL_ID

    @property
    def revision(self):
        return REVISION

    @property
    def dimension(self):
        return 1024

    @property
    def device(self):
        return 'cpu'

    @property
    def dtype(self):
        return 'float32'

    @property
    def specification(self):
        return dict(asdict(self), schema='qwen-encoder-v1', model_id=MODEL_ID, revision=REVISION,
                    dimension=1024, dtype='float32', device='cpu', pooling='last_nonpadding_token',
                    normalize='L2', padding_side='left', query_instruction=QUERY_INSTRUCTION,
                    query_template='Instruct: {instruction}\nQuery:{query}', document_prefix='',
                    add_special_tokens=True, truncation=False, attention='sdpa')

    @property
    def fingerprint(self):
        return _digest(self.specification)


def token_count(tokenizer, text: str) -> int:
    return len(tokenizer.encode(text, add_special_tokens=True, truncation=False))


def last_token_pool(hidden, mask):
    """Select actual final nonpadding position for left, right or mixed padding."""
    import torch
    if hidden.ndim != 3 or mask.ndim != 2 or hidden.shape[:2] != mask.shape:
        raise EmbeddingOutputError('Invalid encoder tensor shape')
    if not torch.all((mask == 0) | (mask == 1)) or not torch.all(mask.sum(dim=1) > 0):
        raise EmbeddingOutputError('Invalid attention mask')
    positions = torch.arange(mask.shape[1], device=mask.device).expand_as(mask)
    last = positions.masked_fill(mask == 0, -1).max(dim=1).values
    return hidden[torch.arange(hidden.shape[0], device=hidden.device), last]


class _Runtime:
    def __init__(self, directory: Path, profile: QwenProfile):
        artifact_digest = verify_artifact(directory)
        import torch
        from transformers import AutoTokenizer, AutoModel
        torch.set_num_threads(profile.cpu_threads)
        self.tokenizer = AutoTokenizer.from_pretrained(
            directory, local_files_only=True, trust_remote_code=False, padding_side='left', use_fast=True)
        self.model = AutoModel.from_pretrained(
            directory, local_files_only=True, trust_remote_code=False,
            use_safetensors=True, dtype=torch.float32, attn_implementation='sdpa')
        self.model.to('cpu').eval()
        self.identity = {'artifact_sha256': artifact_digest,
                         'implementation_sha256': digest_file(Path(__file__)),
                         'versions': {name: version(name) for name in ('torch', 'transformers', 'tokenizers', 'safetensors')},
                         'profile': profile.specification}

    def encode(self, texts):
        import torch
        with torch.inference_mode():
            batch = self.tokenizer(texts, padding=True, truncation=False, add_special_tokens=True, return_tensors='pt')
            output = self.model(**batch)
            pooled = last_token_pool(output.last_hidden_state, batch['attention_mask'])
            normalized = torch.nn.functional.normalize(pooled, p=2, dim=1)
            return normalized.tolist()


def load_runtime(directory: Path, profile: QwenProfile):
    return _Runtime(directory, profile)


class QwenEmbeddings(Embeddings):
    """Single synchronous encoder per process; caller controls process concurrency."""
    def __init__(self, artifact_directory: Path, profile: QwenProfile | None = None):
        self._profile = profile or QwenProfile()
        self._lock = Lock()
        self._runtime = load_runtime(Path(artifact_directory), self.profile)

    @property
    def profile(self):
        return self._profile

    def count_document_tokens(self, text: str) -> int:
        """Diagnostic count without truncation; shares the inference mutex.

        Do not expose the mutable fast tokenizer to chunking/benchmark callers.
        An oversized token count may be inspected here, but never embedded.
        """
        self._check_text(text)
        with self._lock:
            return token_count(self._runtime.tokenizer, text)

    @property
    def identity(self):
        return deepcopy(self._runtime.identity)

    @property
    def fingerprint(self):
        return _digest(self.identity)

    def _check_text(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > self.profile.max_text_characters:
            raise EmbeddingInputError('Empty, invalid or oversized embedding text')

    def _encode(self, texts):
        if len(texts) > self.profile.max_request_documents:
            raise EmbeddingInputError('Embedding request exceeds document limit')
        with self._lock:
            # Fast tokenizer validation changes padding/truncation state too.
            # Serialize all of it, not only the model forward pass. Validate the
            # entire request before inference; header/prompt are counted.
            lengths = []
            for text in texts:
                self._check_text(text)
                length = token_count(self._runtime.tokenizer, text)
                if not 0 < length <= self.profile.max_input_tokens:
                    raise EmbeddingInputError('Embedding token budget exceeded')
                lengths.append(length)
            result, batch, maximum = [], [], 0
            for text, length in zip(texts, lengths):
                next_maximum = max(maximum, length)
                if batch and (len(batch) >= self.profile.max_batch_size or
                              next_maximum * (len(batch) + 1) > self.profile.max_batch_tokens):
                    result.extend(self._validated_batch(batch))
                    batch, maximum = [], 0
                batch.append(text)
                maximum = max(maximum, length)
            if batch:
                result.extend(self._validated_batch(batch))
        return result

    def _validated_batch(self, texts):
        output = self._runtime.encode(texts)
        if not isinstance(output, list) or len(output) != len(texts):
            raise EmbeddingOutputError('Encoder returned incorrect row count')
        for row in output:
            if not isinstance(row, list) or len(row) != 1024 or not all(type(v) in (int, float) and math.isfinite(v) for v in row):
                raise EmbeddingOutputError('Encoder returned invalid vector')
            if not math.isclose(math.sqrt(sum(v * v for v in row)), 1.0, abs_tol=1e-5):
                raise EmbeddingOutputError('Encoder returned unnormalized vector')
        return output

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not isinstance(texts, list):
            raise EmbeddingInputError('Embedding documents must be a list')
        return self._encode(texts)

    def embed_query(self, text: str) -> list[float]:
        self._check_text(text)
        return self._encode([f'Instruct: {QUERY_INSTRUCTION}\nQuery:{text}'])[0]


@dataclass(frozen=True)
class CandidateSlice:
    start: int
    end: int
    text: str
    token_count: int
    eligibility: str = 'pending_review'


def split_candidate_text(text: str, tokenizer, *, max_tokens: int,
                         synthetic_context: str = '', max_characters: int = 65536) -> list[CandidateSlice]:
    """Diagnostic character spans; no source verification, approval or CitationUnit.

    Keeps every character, prefers line endings and measures the final search text.
    Canonical legal units/closures are resolved separately in the governed chunker.
    """
    if not isinstance(text, str) or not text or len(text) > max_characters:
        raise EmbeddingInputError('Invalid candidate text')
    if type(max_tokens) is not int or not 1 <= max_tokens <= 32768 or not isinstance(synthetic_context, str):
        raise EmbeddingInputError('Invalid candidate token budget')
    if len(synthetic_context) > max_characters or token_count(tokenizer, synthetic_context) >= max_tokens:
        raise EmbeddingInputError('Synthetic context exhausts token budget')
    full_count = token_count(tokenizer, synthetic_context + text)
    if full_count <= max_tokens:
        return [CandidateSlice(0, len(text), text, full_count)]
    slices, start = [], 0
    while start < len(text):
        low, high, best = 1, len(text) - start, 0
        while low <= high:
            middle = (low + high) // 2
            if token_count(tokenizer, synthetic_context + text[start:start + middle]) <= max_tokens:
                best, low = middle, middle + 1
            else:
                high = middle - 1
        if best == 0:
            raise EmbeddingInputError('One character exceeds remaining token budget')
        # Binary search only proposes a boundary: token counts need not be monotonic.
        # The final slice is always measured again and the text is never decoded back.
        segment = text[start:start + best]
        newline = segment.rfind('\n') + 1
        if start + best < len(text) and newline >= max(1, best // 2):
            best = newline
        end = start + best
        count = token_count(tokenizer, synthetic_context + text[start:end])
        if count > max_tokens:
            raise EmbeddingInputError('Candidate boundary exceeds token budget')
        slices.append(CandidateSlice(start, end, text[start:end], count))
        start = end
    return slices

"""Local diagnostics. Reports cannot certify legal quality or publish a corpus."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
from importlib.metadata import version
from threading import Event, Thread
from time import perf_counter

from app.embeddings.artifacts import digest_file, verify_artifact
from app.embeddings.qwen import QwenEmbeddings, QwenProfile, token_count, split_candidate_text

DOCUMENTS = [
    'Cláusula 1. O pagamento mensal vence no dia dez. Em caso de atraso, aplica-se multa de dois por cento.',
    'Cláusula 2. O locatário deverá avisar a rescisão com antecedência mínima de trinta dias, salvo descumprimento comprovado do locador.',
    'Cláusula 3. As informações confidenciais não podem ser divulgadas, exceto quando exigidas por ordem judicial.',
    'Cláusula 4. A entrega do equipamento ocorrerá em até quinze dias após a assinatura, condicionada ao pagamento inicial.',
    'Cláusula 5. O valor dos serviços será reajustado anualmente pelo IPCA, na data de aniversário do contrato.',
    'Cláusula 6. A contratada é responsável por reparar defeitos do produto durante os doze meses de garantia.',
]
QUERIES = [
    'Qual é o dia de vencimento e a penalidade por atraso?',
    'Com quanto tempo de antecedência posso encerrar a locação e qual a exceção?',
    'Quando a obrigação de sigilo permite divulgação?',
    'Qual o prazo para entrega e sua condição?',
    'Qual índice e periodicidade do reajuste?',
    'Quem repara os defeitos e por quanto tempo?',
]


def percentile(values, q):
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, int(len(ordered) * q + 0.999999) - 1))] if ordered else None


class MemorySampler:
    """Sample process-tree RSS; misses peaks between samples and is not a limit."""
    def __enter__(self):
        import psutil
        self.process = psutil.Process()
        self.stop = Event()
        self.peak_bytes = self._rss()
        self.baseline_bytes = self.peak_bytes
        self.worker = Thread(target=self._sample, daemon=True)
        self.worker.start()
        return self

    def _rss(self):
        import psutil
        total = 0
        for process in [self.process, *self.process.children(recursive=True)]:
            try:
                total += process.memory_info().rss
            except psutil.NoSuchProcess:
                pass
        return total

    def _sample(self):
        while not self.stop.wait(0.01):
            self.peak_bytes = max(self.peak_bytes, self._rss())

    def __exit__(self, *args):
        self.peak_bytes = max(self.peak_bytes, self._rss())
        self.stop.set()
        self.worker.join()


def _code_identity():
    root = Path(__file__).resolve().parents[2]
    files = [Path(__file__).with_name(name) for name in ('qwen.py', 'artifacts.py', 'experiments.py', 'factory.py', '__main__.py')]
    files.append(root / 'app' / 'config.py')
    files.extend(root / 'app' / 'ingestion' / name for name in ('chunker.py', 'schemas.py', 'temporal.py', 'metadata_extractor.py'))
    files.append(root / 'app' / 'retrieval' / 'lexical.py')
    files.append(root / 'app' / 'identifiers.py')
    return {'code_sha256': {p.relative_to(root).as_posix(): digest_file(p) for p in files},
            'uv_lock_sha256': digest_file(root / 'uv.lock')}


def synthetic_benchmark(artifact_directory: Path, profile=None) -> dict:
    profile = profile or QwenProfile()
    with MemorySampler() as memory:
        started = perf_counter()
        encoder = QwenEmbeddings(artifact_directory, profile)
        load_seconds = perf_counter() - started
        started = perf_counter()
        document_vectors = encoder.embed_documents(DOCUMENTS)
        documents_seconds = perf_counter() - started
        encoder.embed_query(QUERIES[0])  # Warm-up excluded from query timing.
        timings, ranks = [], []
        for _ in range(3):
            for expected, question in enumerate(QUERIES):
                started = perf_counter()
                query = encoder.embed_query(question)
                timings.append(perf_counter() - started)
                scores = [sum(a * b for a, b in zip(query, row)) for row in document_vectors]
                ordering = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
                ranks.append(ordering.index(expected) + 1)
        # Probe close to the actual token budget, with no hidden truncation.
        probe = 'Cláusula de teste sintético. A obrigação depende da condição expressa. '
        text = probe
        while encoder.count_document_tokens(text + probe) <= profile.max_input_tokens:
            text += probe
        started = perf_counter()
        encoder.embed_documents([text])
        long_seconds = perf_counter() - started
    return dict(schema='qwen-synthetic-benchmark-v1', created_at=datetime.now(timezone.utc).isoformat(),
                dataset_type='synthetic', legal_quality_certified=False, published=False,
                encoder=encoder.identity, encoder_fingerprint=encoder.fingerprint,
                environment={'platform': platform.platform(), 'python': platform.python_version()},
                dataset_sha256=hashlib.sha256(json.dumps([DOCUMENTS, QUERIES], ensure_ascii=False).encode()).hexdigest(),
                **_code_identity(), documents=6, unique_queries=6, repetitions=3,
                expected_document_ranks=ranks, query_samples=18,
                load_seconds=load_seconds, document_batch_seconds=documents_seconds,
                query_p50_seconds=percentile(timings, .5), query_p95_seconds=percentile(timings, .95),
                query_latencies_seconds=timings, budget_probe_tokens=encoder.count_document_tokens(text),
                budget_probe_seconds=long_seconds, rss_baseline_bytes=memory.baseline_bytes,
                sampled_peak_process_tree_rss_bytes=memory.peak_bytes, rss_sample_interval_seconds=.01,
                limitations=['Windows CPU, not AWS qualification', 'No API/index/concurrency load',
                             'Synthetic ranks are not legal recall or answer accuracy', 'RSS sampling may miss transient peaks'])


def token_inventory(artifact_directory: Path, staging_directory: Path, profile=None) -> dict:
    """Only aggregate lengths of local candidates. Never encodes or changes eligibility."""
    from transformers import AutoTokenizer
    from app.ingestion.schemas import ParsedDocument
    from app.ingestion.chunker import _group_blocks, _build_search_header
    profile = profile or QwenProfile()
    artifact_digest = verify_artifact(artifact_directory)
    tokenizer = AutoTokenizer.from_pretrained(artifact_directory, local_files_only=True,
                                             trust_remote_code=False, padding_side='left', use_fast=True)
    inputs, blocks, units, split_count, states = {}, [], [], 0, Counter()
    files = sorted(p for p in Path(staging_directory).glob('*.json')
                   if len(p.stem) == 64 and all(c in '0123456789abcdef' for c in p.stem))
    if not files:
        raise ValueError('Staging candidate artifacts not found')
    for path in files:
        inputs[path.name] = digest_file(path)
        candidate = json.loads(path.read_text(encoding='utf-8'))
        states[candidate['eligibility']] += 1
        if candidate.get('parsed_document') is None:
            continue
        document = ParsedDocument.model_validate(candidate['parsed_document'])
        for block in document.blocks:
            if block.text_raw:
                blocks.append(token_count(tokenizer, block.text_raw))
        for group in _group_blocks(document.blocks):
            raw = '\n\n'.join(block.text_raw for block in group['blocks'] if block.text_raw)
            if not raw:
                continue
            block_ids = {block.block_id for block in group['blocks']}
            local_dates = [mention for mention in document.metadata.temporal_mentions if mention.block_id in block_ids]
            header = _build_search_header(document.metadata, group['hierarchy_label'], group['block_type'],
                                          temporal_mentions=local_dates) + '\n\n'
            units.append(token_count(tokenizer, header + raw))
            slices = split_candidate_text(raw, tokenizer, max_tokens=profile.max_input_tokens, synthetic_context=header)
            if ''.join(s.text for s in slices) != raw:
                raise ValueError('Candidate text coverage failed')
            split_count += len(slices)
    return dict(schema='qwen-token-inventory-v1', created_at=datetime.now(timezone.utc).isoformat(),
                dataset_type='unreviewed_staging', published=False, eligibility_changed=False,
                artifact_sha256=artifact_digest, profile=profile.specification,
                tokenizer_versions={name: version(name) for name in ('transformers', 'tokenizers')},
                staging_control_sha256={name: digest_file(Path(staging_directory) / name)
                                       for name in ('preflight.json', 'summary.json')
                                       if (Path(staging_directory) / name).is_file()},
                input_artifacts_digest=hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest(),
                **_code_identity(), files=len(files), eligibility_counts=dict(states), blocks=len(blocks),
                block_tokens={'p50': percentile(blocks, .5), 'p95': percentile(blocks, .95), 'max': max(blocks, default=0)},
                candidate_groups=len(units), search_tokens={'p50': percentile(units, .5), 'p95': percentile(units, .95), 'max': max(units, default=0)},
                groups_above_budget=sum(n > profile.max_input_tokens for n in units), diagnostic_slices=split_count,
                limitations=['Legacy heuristic grouping for inventory only', 'No human-verified canonical units or closure',
                             'Header is synthetic; offsets refer to grouped candidate text, not physical source'])

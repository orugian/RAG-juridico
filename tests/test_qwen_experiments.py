import json
from types import SimpleNamespace

import pytest

from app.embeddings import experiments
from app.embeddings.qwen import QwenProfile


class Tokenizer:
    def encode(self, text, **kwargs):
        assert kwargs == {'add_special_tokens': True, 'truncation': False}
        return list(text)


def test_inventory_counts_candidates_without_approval_or_encoding(monkeypatch, tmp_path):
    import transformers
    monkeypatch.setattr(experiments, 'verify_artifact', lambda p: 'a' * 64)
    monkeypatch.setattr(transformers.AutoTokenizer, 'from_pretrained', lambda *a, **kw: Tokenizer())
    marker = 'CLIENTE_CONFIDENCIAL_TESTE'
    raw = f'  Cláusula 1. {marker}.\nExceção expressa.  '
    candidate = {
        'eligibility': 'quarantined',
        'parsed_document': {
            'doc_id': 1, 'doc_version': 1, 'file_path': 'synthetic.docx', 'file_hash': 'a' * 64,
            'parser_name': 'synthetic', 'parser_version': '1', 'status': 'success',
            'metadata': {'formal_title': 'Fixture', 'instrument_type': 'Contrato'},
            'blocks': [{'block_id': 'b1', 'doc_id': 1, 'doc_version': 1, 'block_type': 'clause',
                        'hierarchy_level': 'clause', 'order_index': 0, 'text_raw': raw, 'text_search': raw}],
        },
    }
    file = tmp_path / ('f' * 64 + '.json')
    file.write_text(json.dumps(candidate), encoding='utf-8')
    before = file.read_bytes()
    report = experiments.token_inventory(tmp_path, tmp_path, QwenProfile(max_input_tokens=80))
    assert report['blocks'] == report['candidate_groups'] == 1
    assert report['eligibility_counts'] == {'quarantined': 1}
    assert report['groups_above_budget'] == 1 and report['diagnostic_slices'] > 1
    assert not report['published'] and not report['eligibility_changed']
    assert marker not in json.dumps(report) and file.read_bytes() == before


def test_cli_failure_does_not_expose_contract_validation_input(monkeypatch, capsys, tmp_path):
    from app.embeddings import __main__ as cli
    monkeypatch.setattr('sys.argv', ['qwen', 'inventory', '--artifact', str(tmp_path), '--staging', str(tmp_path), '--output', str(tmp_path / 'out.json')])
    def failure(*args):
        raise ValueError('CLIENTE_CONFIDENCIAL_TESTE')
    monkeypatch.setattr(cli, 'token_inventory', failure)
    assert cli.run_cli() == 2
    captured = capsys.readouterr()
    assert 'CLIENTE_CONFIDENCIAL_TESTE' not in captured.out + captured.err
    assert not (tmp_path / 'out.json').exists()


def test_last_token_pool_respects_masks():
    torch = pytest.importorskip('torch')
    from app.embeddings.qwen import last_token_pool, EmbeddingOutputError
    hidden = torch.arange(24).reshape(3, 4, 2)
    mask = torch.tensor([[0, 0, 1, 1], [1, 1, 0, 0], [0, 1, 0, 1]])
    assert torch.equal(last_token_pool(hidden, mask), hidden[torch.arange(3), torch.tensor([3, 1, 3])])
    with pytest.raises(EmbeddingOutputError):
        last_token_pool(hidden, torch.zeros(3, 4))


def test_loader_is_offline_safetensors_and_disables_remote_code(monkeypatch, tmp_path):
    pytest.importorskip('torch')
    import transformers
    import torch
    from app.embeddings import qwen
    calls = []
    monkeypatch.setattr(qwen, 'verify_artifact', lambda p: 'b' * 64)
    monkeypatch.setattr(torch, 'set_num_threads', lambda n: None)
    class Model:
        def to(self, device):
            assert device == 'cpu'
            return self
        def eval(self):
            return self
    def tokenizer(*args, **kwargs):
        calls.append(kwargs)
        return Tokenizer()
    def model(*args, **kwargs):
        calls.append(kwargs)
        return Model()
    monkeypatch.setattr(transformers.AutoTokenizer, 'from_pretrained', tokenizer)
    monkeypatch.setattr(transformers.AutoModel, 'from_pretrained', model)
    encoder = qwen.QwenEmbeddings(tmp_path)
    assert all(c['local_files_only'] and c['trust_remote_code'] is False for c in calls)
    assert calls[1]['use_safetensors'] is True and calls[1]['dtype'] == torch.float32
    assert encoder.identity['artifact_sha256'] == 'b' * 64 and len(encoder.fingerprint) == 64


@pytest.mark.parametrize('output', [None, [None], {'0': [1.0]}, [['secret'] * 1024]])
def test_malformed_output_has_controlled_error(monkeypatch, tmp_path, output):
    from app.embeddings import qwen
    runtime = SimpleNamespace(tokenizer=Tokenizer(), encode=lambda texts: output)
    monkeypatch.setattr(qwen, 'load_runtime', lambda *args: runtime)
    with pytest.raises(qwen.EmbeddingOutputError):
        qwen.QwenEmbeddings(tmp_path).embed_documents(['a'])

"""P4 wire contracts preserve P3B proof; synthetic configuration is explicit."""
import pytest


def test_generation_configuration_has_no_embedding_or_lexical_defaults():
    from app.retrieval.generation_contracts import GenerationConfig
    with pytest.raises(ValueError):
        GenerationConfig()
    with pytest.raises(ValueError):
        GenerationConfig(embedding_identity={"provider": "test-hash"}, embedding_dimension=8,
                         lexical_identity={"profile": "fixture"}, configuration_version="cfg")


def test_generation_names_are_single_safe_components():
    from app.retrieval.generation_contracts import validate_generation_id
    assert validate_generation_id("g-synthetic_1") == "g-synthetic_1"
    for value in ("../data", "a/b", "a\\b", "active.json", ".", "..", "CON", "g:x"):
        with pytest.raises(ValueError):
            validate_generation_id(value)


def test_bundle_requires_complete_explicit_sources_units_chunks_and_decisions():
    from app.retrieval.generation_contracts import GenerationBundle
    with pytest.raises(ValueError):
        GenerationBundle(sources=[], units=[], chunks=[], reviews=[])


def test_policy_snapshot_requires_authority_identity_and_epoch():
    from app.retrieval.generation_contracts import PolicySnapshot
    with pytest.raises(ValueError):
        PolicySnapshot()
    value = PolicySnapshot(journal_id="synthetic-authority", policy_epoch=0, digest="a" * 64)
    assert value.blocked_source_ids == [] and value.blocked_credential_ids == []


def test_family_reconciliation_is_specific_and_requires_a_block():
    from app.retrieval.generation_contracts import PolicySnapshot
    with pytest.raises(ValueError):
        PolicySnapshot(journal_id="authority", policy_epoch=1, digest="a" * 64,
                       family_required_registry_digests={"f1": "b" * 64})
    snapshot = PolicySnapshot(journal_id="authority", policy_epoch=1, digest="a" * 64,
                              blocked_family_ids=["f1"],
                              family_required_registry_digests={"f1": "b" * 64})
    assert snapshot.family_required_registry_digests["f1"] == "b" * 64


def test_production_configuration_accepts_actual_qwen_runtime_identity_shape():
    from app.embeddings.qwen import QwenProfile
    from app.retrieval.generation_contracts import GenerationConfig
    from app.retrieval.lexical import lexical_profile
    identity = {"artifact_sha256": "a" * 64, "implementation_sha256": "b" * 64,
                "versions": {"torch": "fixture", "transformers": "fixture",
                             "tokenizers": "fixture", "safetensors": "fixture"},
                "profile": QwenProfile().specification}
    config = GenerationConfig(configuration_version="fixture", embedding_identity=identity,
                              embedding_dimension=1024, lexical_identity=lexical_profile())
    assert config.synthetic is False

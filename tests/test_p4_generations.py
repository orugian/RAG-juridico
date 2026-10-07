"""P4 coordinator regressions, exclusively physically backed synthetic fixtures."""
import hashlib
from pathlib import Path
import shutil
import json
import os
import subprocess
import sys
from zipfile import ZipFile

import pytest

from app.contracts import AccessContext, SourceIdentity
from app.ingestion.docx_parser import extract_docx_blocks
from app.ingestion.evidence import QwenTokenBudget, prepare_citation_units, build_evidence_chunks
from app.ingestion.review_store import ReviewStore, review_digest, unit_review_digest
from app.ingestion.schemas import ParsedDocument, ContractMetadata, ContractParty
from app.retrieval.generation_contracts import GenerationBundle, GenerationConfig, content_digest
from app.retrieval.lexical import lexical_profile
from app.retrieval.policy import SQLitePolicyJournal


class SyntheticEncoder:
    identity = {"provider": "synthetic-sha256", "dimension": 3}
    def __init__(self):
        self.calls = []
    def embed_documents(self, texts):
        self.calls.extend(texts)
        return [[byte / 255 for byte in hashlib.sha256(text.encode()).digest()[:3]] for text in texts]
    def embed_query(self, text):
        return self.embed_documents([text])[0]


class SyntheticBudget(QwenTokenBudget):
    def __init__(self):
        from threading import Lock
        from app.embeddings.qwen import QwenProfile
        class Tokenizer:
            def encode(self, text, **kwargs):
                return list(text)
        self._tokenizer = Tokenizer()
        self._lock = Lock()
        self.profile = QwenProfile()
        self._identity = {"synthetic_test_only": True, "profile": self.profile.specification}


@pytest.fixture
def setup_generation(tmp_path):
    ledger = ReviewStore(tmp_path / "authority" / "reviews.sqlite")
    sources, units, chunks, paths = [], [], [], {}
    for number, text in enumerate(("Cláusula 1ª - Não pagar, salvo condição expressa.",
                                   "Cláusula 1ª - Exceção: pagar somente após aceite."), 1):
        path = tmp_path / f"synthetic{number}.docx"
        with ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                + f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')
        parsed = ParsedDocument(doc_id=number, doc_version=1, file_id=f"f{number}", file_path=str(path),
            file_hash=hashlib.sha256(path.read_bytes()).hexdigest(), parser_name="docx_parser", parser_version="1.0.0",
            blocks=extract_docx_blocks(path, doc_id=number, doc_version=1),
            metadata=ContractMetadata(formal_title=f"Contrato sintético {number}", instrument_type="Contrato",
                                      parties=[ContractParty(name=f"Parte Sintética {number}")]))
        source = SourceIdentity(source_id=f"doc:{number}:v1:file:f{number}", instrument_id=f"doc:{number}", file_id=parsed.file_id,
            doc_version=1, file_hash=parsed.file_hash, snapshot="synthetic", configuration_version="cfg1",
            parser_name=parsed.parser_name, parser_version=parsed.parser_version)
        candidates = prepare_citation_units(parsed, source, original_path=path)
        base = dict(source_id=source.source_id, snapshot=source.snapshot, file_hash=source.file_hash,
            configuration_version="cfg1", decision="approved", reviewer="synthetic-test-only",
            reason="synthetic fixture; not human approval")
        ledger.record(**base)
        ledger.record(**base, scope="parties", subject_id=source.source_id,
            subject_digest=review_digest({"contract_title": candidates[0].contract_title,
                "parties": [party.model_dump(mode="json") for party in candidates[0].parties]}))
        ids = {unit.unit_id: ledger.record(**base, scope="unit", subject_id=unit.unit_id,
                                           subject_digest=unit_review_digest(unit)).record_id for unit in candidates}
        built = build_evidence_chunks(parsed, source, ledger=ledger, budget=SyntheticBudget(),
                                      original_path=path, unit_review_ids=ids)
        sources.append(source)
        units.extend(built.units)
        chunks.extend(built.chunks)
        paths[source.source_id] = path
    bundle = GenerationBundle(sources=sources, units=units, chunks=chunks,
                              reviews=[record for source in sources for record in ledger.history(source.source_id)])
    config = GenerationConfig(configuration_version="cfg1", synthetic=True, embedding_identity=SyntheticEncoder.identity,
                              embedding_dimension=3, lexical_identity=lexical_profile())
    journal = SQLitePolicyJournal(tmp_path / "authority" / "policy.sqlite", initialize=True, journal_id="synthetic")
    from app.retrieval.generations import GenerationManager
    from app.retrieval.vector_store import ChromaVectorStore
    encoder = SyntheticEncoder()
    manager = GenerationManager(tmp_path / "indexes", configuration=config, embeddings=encoder,
        vector_store=ChromaVectorStore(mode="embedded", dimension=3), ledger=ledger, journal=journal,
        journal_id="synthetic")
    return manager, bundle, paths, encoder, journal, ledger


def _access(journal, permissions=None):
    snapshot = journal.snapshot()
    return AccessContext(principal_id="synthetic", credential_id="credential1", permissions=permissions or ["query", "operate"],
                         policy_epoch=snapshot.policy_epoch, access_scope_digest="synthetic")


def _build(setup, generation_id="g1", **kwargs):
    manager, bundle, paths, *_ = setup
    return manager.build(generation_id, bundle, source_paths=paths, **kwargs)


def test_build_is_not_promotion_and_roundtrip_is_complete(setup_generation):
    manager, bundle, _, _, journal, _ = setup_generation
    manifest = _build(setup_generation)
    assert not (manager.root / "active.json").exists()
    loaded = manager.verify("g1")
    assert loaded.artifacts.bundle == bundle and manifest.chunk_ids == [c.chunk_id for c in bundle.chunks]
    manager.promote("g1", access=_access(journal))
    pinned = manager.pin(_access(journal))
    assert pinned.generation_id == "g1"
    assert {c.chunk_id for c in pinned.finish(manifest.chunk_ids)} == set(manifest.chunk_ids)


def test_ids_immutable_and_reuse_equals_rebuild(setup_generation):
    manager, bundle, _, encoder, journal, _ = setup_generation
    _build(setup_generation)
    first_calls = len(encoder.calls)
    reused = _build(setup_generation, "g2", reuse_from="g1")
    assert len(encoder.calls) == first_calls and set(reused.reused_chunk_ids) == {c.chunk_id for c in bundle.chunks}
    _build(setup_generation, "g3")
    assert manager.verify("g1").artifacts.vectors == manager.verify("g2").artifacts.vectors == manager.verify("g3").artifacts.vectors
    with pytest.raises(ValueError, match="immutable"):
        _build(setup_generation)


@pytest.mark.parametrize("point", ["after_dense", "during_bm25", "before_ready", "after_ready", "before_pointer", "after_pointer"])
def test_faults_never_expose_partial_generation(setup_generation, point):
    manager, _, _, _, journal, _ = setup_generation
    _build(setup_generation)
    manager.promote("g1", access=_access(journal))
    def fault(at):
        if at == point:
            raise RuntimeError("injected")
    if "pointer" in point:
        _build(setup_generation, "g2")
        with pytest.raises(RuntimeError):
            manager.promote("g2", access=_access(journal), fault=fault)
    else:
        with pytest.raises(RuntimeError):
            _build(setup_generation, "g2", fault=fault)
        with pytest.raises(ValueError, match="immutable"):
            _build(setup_generation, "g2")
    pinned = manager.pin(_access(journal))
    assert pinned.generation_id == ("g2" if point == "after_pointer" else "g1")
    assert pinned.finish([pinned.loaded.manifest.chunk_ids[0]])


def test_pin_survives_promotion_but_not_revocation(setup_generation):
    manager, bundle, _, _, journal, _ = setup_generation
    _build(setup_generation)
    manager.promote("g1", access=_access(journal))
    old = manager.pin(_access(journal))
    _build(setup_generation, "g2")
    manager.promote("g2", access=_access(journal))
    assert old.generation_id == "g1"
    journal.block("source", bundle.sources[0].source_id, "synthetic_revocation")
    with pytest.raises(ValueError):
        old.finish([bundle.chunks[0].chunk_id])
    current = manager.pin(_access(journal))
    assert current.finish([bundle.chunks[-1].chunk_id])
    assert all(c.source_id != bundle.sources[0].source_id for c in current.probe("condição", k=10))
    manager.rollback("g1", access=_access(journal))
    assert all(c.source_id != bundle.sources[0].source_id for c in manager.pin(_access(journal)).probe("condição", k=10))


def test_restore_old_snapshot_uses_current_authority(setup_generation, tmp_path):
    manager, bundle, _, _, journal, _ = setup_generation
    _build(setup_generation)
    manager.promote("g1", access=_access(journal))
    backup = tmp_path / "backup"
    shutil.copytree(manager.root, backup)
    journal.block("source", bundle.sources[0].source_id, "synthetic_revocation")
    # Identified pytest-owned root only: simulate disk/root loss recoverably.
    manager.root.rename(tmp_path / "synthetic-lost-disk")
    assert not manager.readiness()
    restored = tmp_path / "restored"
    shutil.copytree(backup, restored)
    from app.retrieval.generations import GenerationManager
    restored_manager = GenerationManager(restored, configuration=manager.configuration, embeddings=manager.embeddings,
        vector_store=manager.vector_store, ledger=manager.ledger, journal=journal, journal_id="synthetic")
    pinned = restored_manager.pin(_access(journal))
    with pytest.raises(ValueError):
        pinned.finish([bundle.chunks[0].chunk_id])
    assert pinned.finish([bundle.chunks[-1].chunk_id])
    # Simulate loss only of identified pytest-owned authority file; never recreate.
    journal.path.rename(journal.path.with_suffix(".lost"))
    assert not restored_manager.readiness()
    with pytest.raises(ValueError):
        restored_manager.pin(_access(journal))


def test_canonical_tamper_or_backend_mutation_is_not_ready(setup_generation):
    manager, _, _, _, journal, _ = setup_generation
    _build(setup_generation)
    manager.promote("g1", access=_access(journal))
    (manager.root / "g1" / "chunks.jsonl").write_bytes(b"tampered synthetic")
    assert not manager.readiness()


def test_unapproved_current_unit_and_credential_return_fail_closed(setup_generation):
    manager, bundle, _, _, journal, ledger = setup_generation
    _build(setup_generation)
    manager.promote("g1", access=_access(journal))
    pinned = manager.pin(_access(journal))
    journal.block("credential", "credential1", "synthetic_revocation")
    with pytest.raises(ValueError):
        pinned.finish([bundle.chunks[0].chunk_id])
    source = bundle.sources[0]
    ledger.record(source_id=source.source_id, snapshot=source.snapshot, file_hash=source.file_hash,
        configuration_version=source.configuration_version, decision="quarantined", reviewer="synthetic",
        reason="synthetic revocation", scope="unit", subject_id=bundle.units[0].unit_id,
        subject_digest=unit_review_digest(bundle.units[0]))
    with pytest.raises(ValueError):
        _build(setup_generation, "revoked")


def test_current_authority_must_be_outside_generation_root(setup_generation):
    manager, _, _, _, _, _ = setup_generation
    from app.retrieval.generations import GenerationManager
    inside = SQLitePolicyJournal(manager.root / "inside.sqlite", initialize=True, journal_id="inside")
    with pytest.raises(ValueError, match="independent"):
        GenerationManager(manager.root, configuration=manager.configuration, embeddings=manager.embeddings,
                          vector_store=manager.vector_store, ledger=manager.ledger, journal=inside, journal_id="inside")


@pytest.mark.parametrize("mutation", ["unit_map", "chunk_span", "chunk_parent", "search_context", "source_set"])
def test_missing_or_changed_proof_rejected_before_dense(setup_generation, mutation, monkeypatch):
    manager, bundle, _, _, _, _ = setup_generation
    changed = bundle.model_dump(mode="json")
    if mutation == "unit_map":
        changed["units"][0]["text_map"] = []
    elif mutation == "chunk_span":
        changed["chunks"][0]["spans"][0]["body_child_index"] = 123
    elif mutation == "chunk_parent":
        changed["chunks"][0]["parent_id"] = "different"
    elif mutation == "search_context":
        changed["chunks"][0]["synthetic_context"] = "fabricated"
    else:
        changed["units"][0]["source_identities"] = []
    monkeypatch.setattr(manager.vector_store, "write", lambda *a: pytest.fail("invalid proof reached dense"))
    with pytest.raises(ValueError):
        altered = GenerationBundle.model_validate(changed)
        manager.build("invalid", altered, source_paths=setup_generation[2])


def test_ready_staging_recovery_is_explicit_and_no_partial_recovery(setup_generation):
    manager, _, _, _, journal, _ = setup_generation
    def fault(at):
        if at == "after_ready":
            raise RuntimeError("injected")
    with pytest.raises(RuntimeError):
        _build(setup_generation, fault=fault)
    assert not manager.readiness()
    recovered = manager.recover("g1", access=_access(journal))
    assert recovered.generation_id == "g1" and not manager.readiness()
    manager.promote("g1", access=_access(journal))
    assert manager.readiness()


def test_readiness_does_not_ignore_revoked_reviews_or_all_sources(setup_generation):
    manager, bundle, _, _, journal, _ = setup_generation
    _build(setup_generation)
    manager.promote("g1", access=_access(journal))
    for source in bundle.sources:
        journal.block("source", source.source_id, "synthetic_all_revoked")
    assert not manager.readiness()


def test_real_original_change_before_build_is_rejected(setup_generation):
    setup_generation[2][setup_generation[1].sources[0].source_id].write_bytes(b"synthetic corruption")
    with pytest.raises(ValueError):
        _build(setup_generation)


def _linked_bundle(setup):
    from app.contracts import InstrumentRelation, RelationResolution
    manager, bundle, _, _, _, ledger = setup
    relation = InstrumentRelation(relation_id="relation1", from_instrument_id=bundle.sources[1].instrument_id,
        to_instrument_id=bundle.sources[0].instrument_id, relation_type="amends",
        affected_unit_ids=[bundle.units[0].unit_id], support_unit_ids=[bundle.units[1].unit_id],
        support_spans=bundle.units[1].spans, snapshot="synthetic", configuration_version="cfg1", state="proposed")
    payload = relation.model_dump(mode="json") | {"state": "approved", "review_record_id": "synthetic-placeholder"}
    relation = InstrumentRelation.model_validate(payload)
    source = bundle.sources[1]
    base = dict(source_id=source.source_id, snapshot=source.snapshot, file_hash=source.file_hash,
        configuration_version=source.configuration_version, reviewer="synthetic-test-only", reason="synthetic family", decision="approved")
    relation.review_record_id = ledger.record(**base, scope="relation", subject_id=relation.relation_id,
        subject_digest=review_digest(relation.model_dump(mode="json", exclude={"review_record_id"}))).record_id
    resolution = RelationResolution(family_id="family1", snapshot="synthetic", registry_version="registry1",
        registry_digest=ledger.relation_state_digest(), relation_ids=[relation.relation_id], state="resolved",
        review_record_id="synthetic-placeholder")
    resolution.review_record_id = ledger.record(**base, scope="family", subject_id="family1",
        subject_digest=review_digest(resolution.model_dump(mode="json", exclude={"review_record_id"}))).record_id
    return GenerationBundle(sources=bundle.sources, units=bundle.units, chunks=bundle.chunks,
        reviews=[record for source in bundle.sources for record in ledger.history(source.source_id)],
        relations=[relation], resolutions=[resolution], relation_review_sources={"relation1": source.source_id},
        family_review_sources={"family1": source.source_id})


def test_family_block_current_resolution_and_original_text(setup_generation):
    manager, _, paths, _, journal, ledger = setup_generation
    bundle = _linked_bundle(setup_generation)
    manager.build("linked", bundle, source_paths=paths)
    manager.promote("linked", access=_access(journal))
    assert manager.pin(_access(journal)).finish([bundle.chunks[0].chunk_id])
    journal.block("family", "family1", "synthetic_family_block")
    with pytest.raises(ValueError):
        manager.pin(_access(journal)).finish([bundle.chunks[0].chunk_id])
    assert manager.pin(_access(journal), evidence_scope="original_text").finish([bundle.chunks[0].chunk_id])
    journal.require_family_registry("family1", ledger.relation_state_digest(), "synthetic_reconcile")
    assert manager.pin(_access(journal)).finish([bundle.chunks[0].chunk_id])
    journal.block("family", "family1", "synthetic_hard_block_again")
    with pytest.raises(ValueError):
        manager.pin(_access(journal)).finish([bundle.chunks[0].chunk_id])


def test_current_family_review_required_even_with_matching_registry(setup_generation):
    manager, _, paths, _, journal, ledger = setup_generation
    bundle = _linked_bundle(setup_generation)
    manager.build("linked", bundle, source_paths=paths)
    manager.promote("linked", access=_access(journal))
    source = bundle.sources[1]
    resolution = bundle.resolutions[0]
    ledger.record(source_id=source.source_id, snapshot=source.snapshot, file_hash=source.file_hash,
        configuration_version=source.configuration_version, decision="quarantined", reviewer="synthetic",
        reason="synthetic family revoked", scope="family", subject_id=resolution.family_id,
        subject_digest=review_digest(resolution.model_dump(mode="json", exclude={"review_record_id"})))
    with pytest.raises(ValueError):
        manager.pin(_access(journal)).finish([bundle.chunks[0].chunk_id])
    assert manager.pin(_access(journal), evidence_scope="original_text").finish([bundle.chunks[0].chunk_id])


def test_complete_manager_server_reload_and_revoke(setup_generation, tmp_path):
    from tests.test_p4_vector_store import LocalServer
    from app.retrieval.vector_store import ChromaVectorStore
    from app.retrieval.generations import GenerationManager
    manager, bundle, paths, encoder, journal, ledger = setup_generation
    server_path = tmp_path / "private-server"
    server_path.mkdir()
    server = LocalServer(server_path)
    try:
        server.start()
        manager = GenerationManager(tmp_path / "server-generations", configuration=manager.configuration,
            embeddings=encoder, vector_store=ChromaVectorStore(mode="server", dimension=3, host="127.0.0.1", port=server.port),
            journal=journal, ledger=ledger, journal_id="synthetic")
        manager.build("g1", bundle, source_paths=paths)
        manager.promote("g1", access=_access(journal))
        assert manager.pin(_access(journal)).probe("condição")
        server.stop()
        assert not manager.readiness()
        server.start()
        assert manager.readiness()
        journal.block("source", bundle.sources[0].source_id, "synthetic_revocation")
        assert all(chunk.source_id != bundle.sources[0].source_id for chunk in manager.pin(_access(journal)).probe("condição"))
    finally:
        server.stop()


def test_cli_requires_explicit_configuration_and_has_no_corpus_defaults():
    from app.retrieval.generations import main
    with pytest.raises(SystemExit) as error:
        main(["build"])
    assert error.value.code == 2


def test_indexer_governed_entrypoint_requires_manager(setup_generation):
    from app.retrieval.indexer import build_governed_generation
    manager, bundle, paths, *_ = setup_generation
    manifest = build_governed_generation(manager=manager, generation_id="batch", bundle=bundle, source_paths=paths)
    assert manifest.generation_id == "batch" and not manager.readiness()


def test_batch_governed_entrypoint_never_rewrites_staging(setup_generation):
    from app.ingestion.batch import build_reviewed_generation
    manager, bundle, paths, *_ = setup_generation
    before = {key: path.read_bytes() for key, path in paths.items()}
    manifest = build_reviewed_generation(manager=manager, generation_id="batch", bundle=bundle, source_paths=paths)
    assert manifest.generation_id == "batch" and not manager.readiness()
    assert before == {key: path.read_bytes() for key, path in paths.items()}


_CHILD = r'''
import json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "tests"))
from test_p4_generations import SyntheticEncoder, _access
from app.ingestion.review_store import ReviewStore
from app.retrieval.policy import SQLitePolicyJournal
from app.retrieval.generation_contracts import GenerationConfig, GenerationBundle
from app.retrieval.generations import GenerationManager
from app.retrieval.vector_store import ChromaVectorStore
payload = json.loads(Path(sys.argv[1]).read_bytes())
journal = SQLitePolicyJournal(Path(payload["journal"]), journal_id="synthetic")
manager = GenerationManager(Path(payload["root"]), configuration=GenerationConfig.model_validate(payload["config"]),
    embeddings=SyntheticEncoder(), vector_store=ChromaVectorStore(mode="embedded", dimension=3),
    journal=journal, journal_id="synthetic", ledger=ReviewStore(Path(payload["ledger"])))
def fault(point):
    if point == payload.get("fault"):
        os._exit(57)
try:
    if payload.get("promote"):
        manager.promote("g2", access=_access(journal), fault=fault)
    else:
        manager.build("g2", GenerationBundle.model_validate(payload["bundle"]), source_paths=payload["paths"], fault=fault)
except ValueError as error:
    print(str(error))
    sys.exit(58)
'''


def _child_payload(setup, tmp_path, **extra):
    manager, bundle, paths, _, journal, ledger = setup
    path = tmp_path / "synthetic-child-input.json"
    path.write_text(json.dumps({"root": str(manager.root), "journal": str(journal.path), "ledger": str(ledger.path),
        "bundle": bundle.model_dump(mode="json"), "config": manager.configuration.model_dump(mode="json"),
        "paths": {key: str(value) for key, value in paths.items()}, **extra}), encoding="utf-8")
    return path


def _child(path):
    return subprocess.Popen([sys.executable, "-c", _CHILD, str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


@pytest.mark.parametrize("point", ["after_dense", "during_bm25", "before_ready", "after_ready", "before_pointer", "after_pointer"])
def test_process_death_releases_lock_and_never_activates_partial(setup_generation, tmp_path, point):
    manager, _, _, _, journal, _ = setup_generation
    _build(setup_generation)
    manager.promote("g1", access=_access(journal))
    promote = "pointer" in point
    if promote:
        _build(setup_generation, "g2")
    child = _child(_child_payload(setup_generation, tmp_path, fault=point, promote=promote))
    try:
        _, errors = child.communicate(timeout=40)
        assert child.returncode == 57, errors.decode(errors="replace")
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
    assert manager.pin(_access(journal)).generation_id == ("g2" if point == "after_pointer" else "g1")
    if point == "after_ready":
        manager.recover("g2", access=_access(journal))
    _build(setup_generation, "after-death")


def test_cross_process_writer_serializes_same_generation_id(setup_generation, tmp_path):
    manager, *_ = setup_generation
    path = _child_payload(setup_generation, tmp_path)
    children = [_child(path), _child(path)]
    try:
        reports = [child.communicate(timeout=40) for child in children]
        assert sorted(child.returncode for child in children) == [0, 58], reports
        assert any(b"generation_id_immutable" in out for out, _ in reports)
        assert manager.verify("g2") and not manager.readiness()
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)


def test_relation_change_requires_new_compatible_generation_and_rollback_cannot_reopen(setup_generation):
    manager, _, paths, _, journal, ledger = setup_generation
    initial = _linked_bundle(setup_generation)
    manager.build("before-relation", initial, source_paths=paths)
    manager.promote("before-relation", access=_access(journal))
    current = _linked_bundle(setup_generation)  # New reviewed relation head and exact family resolution.
    journal.require_family_registry("family1", ledger.relation_state_digest(), "synthetic_relation_change")
    with pytest.raises(ValueError):
        manager.pin(_access(journal)).finish([initial.chunks[0].chunk_id])
    assert manager.pin(_access(journal), evidence_scope="original_text").finish([initial.chunks[0].chunk_id])
    rebuilt = manager.build("after-relation", current, source_paths=paths, reuse_from="before-relation")
    assert not rebuilt.reused_chunk_ids
    manager.promote("after-relation", access=_access(journal))
    assert manager.pin(_access(journal)).finish([current.chunks[0].chunk_id])
    manager.rollback("before-relation", access=_access(journal))
    with pytest.raises(ValueError):
        manager.pin(_access(journal)).finish([initial.chunks[0].chunk_id])


def test_source_removal_and_configuration_change_use_explicit_new_generation(setup_generation):
    manager, bundle, paths, encoder, journal, _ = setup_generation
    _build(setup_generation)
    kept = bundle.sources[1]
    subset = GenerationBundle(sources=[kept], units=[unit for unit in bundle.units if kept.source_id in unit.source_ids],
        chunks=[chunk for chunk in bundle.chunks if chunk.source_id == kept.source_id],
        reviews=[record for record in bundle.reviews if record.source_id == kept.source_id])
    removed = manager.build("removed", subset, source_paths={kept.source_id: paths[kept.source_id]}, reuse_from="g1")
    assert set(removed.reused_chunk_ids) == {chunk.chunk_id for chunk in subset.chunks}
    assert len(manager.verify("removed").artifacts.bundle.sources) == 1
    from app.retrieval.generations import GenerationManager
    changed_config = manager.configuration.model_copy(update={"configuration_version": "cfg2"})
    other = GenerationManager(manager.root, configuration=changed_config, embeddings=encoder,
        vector_store=manager.vector_store, ledger=manager.ledger, journal=journal, journal_id="synthetic")
    with pytest.raises(ValueError, match="configuration"):
        other.verify("g1")
    before = len(encoder.calls)
    rebuilt = other.build("different-config", bundle, source_paths=paths)
    assert not rebuilt.reused_chunk_ids and len(encoder.calls) > before


def test_real_local_fixed_qwen_generates_and_reloads_synthetic_evidence(setup_generation, request, tmp_path):
    artifact = request.config.getoption("--qwen-artifact")
    if artifact is None:
        pytest.skip("explicit existing local Qwen artifact required; never downloads")
    from app.embeddings.qwen import QwenEmbeddings
    from app.retrieval.generations import GenerationManager
    from app.retrieval.vector_store import ChromaVectorStore
    manager, bundle, paths, _, journal, ledger = setup_generation
    encoder = QwenEmbeddings(Path(artifact))
    configuration = GenerationConfig(configuration_version="synthetic-real-qwen", embedding_identity=encoder.identity,
        embedding_dimension=1024, lexical_identity=lexical_profile())
    real = GenerationManager(tmp_path / "real-qwen-synthetic", configuration=configuration, embeddings=encoder,
        vector_store=ChromaVectorStore(mode="embedded", dimension=1024), journal=journal, ledger=ledger, journal_id="synthetic")
    manifest = real.build("qwen-synthetic", bundle, source_paths=paths)
    assert all(len(vector) == 1024 for vector in real.verify("qwen-synthetic").artifacts.vectors.values())
    real.promote("qwen-synthetic", access=_access(journal))
    assert real.pin(_access(journal)).probe("condição expressa", k=1)
    assert manifest.configuration.embedding_identity == encoder.identity


def test_revocation_during_final_slow_review_is_checked_before_return(setup_generation, monkeypatch):
    manager, bundle, _, _, journal, _ = setup_generation
    _build(setup_generation)
    manager.promote("g1", access=_access(journal))
    pinned = manager.pin(_access(journal))
    original = manager._reviews
    def racing_review(*args, **kwargs):
        result = original(*args, **kwargs)
        journal.block("source", bundle.sources[0].source_id, "synthetic_race")
        return result
    monkeypatch.setattr(manager, "_reviews", racing_review)
    with pytest.raises(ValueError):
        pinned.finish([bundle.chunks[0].chunk_id])


@pytest.mark.parametrize("operation", ["promote", "rollback"])
def test_current_unit_revoked_before_pointer_preserves_valid_previous(setup_generation, operation):
    manager, bundle, paths, _, journal, ledger = setup_generation
    own = bundle.sources[0]
    previous = GenerationBundle(sources=[own], units=[bundle.units[0]], chunks=[bundle.chunks[0]],
        reviews=[record for record in bundle.reviews if record.source_id == own.source_id])
    manager.build("candidate", bundle, source_paths=paths)
    manager.build("previous", previous, source_paths={own.source_id: paths[own.source_id]})
    manager.promote("previous", access=_access(journal))
    before = (manager.root / "active.json").read_bytes()
    policy_before = journal.snapshot()
    def revoke(at):
        if at == "before_pointer":
            source = bundle.sources[1]
            ledger.record(source_id=source.source_id, snapshot=source.snapshot, file_hash=source.file_hash,
                configuration_version=source.configuration_version, decision="quarantined", reviewer="synthetic-test-only",
                reason="synthetic race", scope="unit", subject_id=bundle.units[1].unit_id,
                subject_digest=unit_review_digest(bundle.units[1]))
    with pytest.raises(ValueError):
        getattr(manager, operation)("candidate", access=_access(journal), fault=revoke)
    assert journal.snapshot() == policy_before
    assert (manager.root / "active.json").read_bytes() == before
    assert manager.readiness() and manager.pin(_access(journal)).generation_id == "previous"


def test_missing_review_authority_is_not_ready_and_never_recreated(setup_generation, tmp_path):
    manager, _, _, _, journal, ledger = setup_generation
    _build(setup_generation)
    manager.promote("g1", access=_access(journal))
    # Only the fixture's identified authority is moved to a recovery name.
    ledger.path.rename(ledger.path.with_suffix(".retained"))
    assert not manager.readiness()
    assert not ledger.path.exists()
    with pytest.raises(ValueError):
        manager.pin(_access(journal))
    assert not ledger.path.exists()
    from app.retrieval.generations import GenerationManager
    with pytest.raises(ValueError):
        GenerationManager(manager.root, configuration=manager.configuration, embeddings=manager.embeddings,
            vector_store=manager.vector_store, ledger=ledger, journal=journal, journal_id="synthetic")
    assert not ledger.path.exists()


@pytest.mark.parametrize("scope", ["unit", "policy"])
def test_actual_pointer_swap_holds_current_authorities_until_commit(setup_generation, monkeypatch, scope):
    from threading import Event, Thread
    manager, bundle, _, _, journal, ledger = setup_generation
    _build(setup_generation)
    source = bundle.sources[1]
    acknowledged, started = Event(), Event()
    errors = []
    def writer():
        started.set()
        try:
            if scope == "unit":
                ledger.record(source_id=source.source_id, snapshot=source.snapshot, file_hash=source.file_hash,
                    configuration_version=source.configuration_version, decision="quarantined", reviewer="synthetic-test-only",
                    reason="synthetic race", scope="unit", subject_id=bundle.units[1].unit_id,
                    subject_digest=unit_review_digest(bundle.units[1]))
            else:
                journal.block("source", source.source_id, "synthetic_race")
            acknowledged.set()
        except Exception as error:
            errors.append(error)
    real_replace = os.replace
    threads = []
    def replacing(origin, destination):
        if Path(destination) == manager.root / "active.json":
            thread = Thread(target=writer)
            threads.append(thread)
            thread.start()
            assert started.wait(2)
            assert not acknowledged.wait(0.25), "authority committed before atomic pointer swap"
        return real_replace(origin, destination)
    monkeypatch.setattr(os, "replace", replacing)
    try:
        manager.promote("g1", access=_access(journal))
    finally:
        for thread in threads:
            thread.join(15)
    assert not errors and acknowledged.is_set()
    assert manager._active().manifest.generation_id == "g1"


def test_review_authority_must_not_be_part_of_restored_generation_root(setup_generation):
    manager, _, _, _, journal, _ = setup_generation
    inside = ReviewStore(manager.root / "review-snapshot.sqlite")
    from app.retrieval.generations import GenerationManager
    with pytest.raises(ValueError, match="independent"):
        GenerationManager(manager.root, configuration=manager.configuration, embeddings=manager.embeddings,
            vector_store=manager.vector_store, ledger=inside, journal=journal, journal_id="synthetic")


def test_recovery_revocation_before_rename_preserves_staging_and_active(setup_generation):
    manager, bundle, paths, _, journal, ledger = setup_generation
    own = bundle.sources[0]
    previous = GenerationBundle(sources=[own], units=[bundle.units[0]], chunks=[bundle.chunks[0]],
        reviews=[record for record in bundle.reviews if record.source_id == own.source_id])
    manager.build("previous", previous, source_paths={own.source_id: paths[own.source_id]})
    manager.promote("previous", access=_access(journal))
    def crash(at):
        if at == "after_ready":
            raise RuntimeError("injected")
    with pytest.raises(RuntimeError):
        manager.build("recovering", bundle, source_paths=paths, fault=crash)
    before = (manager.root / "active.json").read_bytes()
    def revoke(at):
        if at == "before_recovery":
            source = bundle.sources[1]
            ledger.record(source_id=source.source_id, snapshot=source.snapshot, file_hash=source.file_hash,
                configuration_version=source.configuration_version, decision="quarantined", reviewer="synthetic-test-only",
                reason="synthetic race", scope="unit", subject_id=bundle.units[1].unit_id,
                subject_digest=unit_review_digest(bundle.units[1]))
    with pytest.raises(ValueError):
        manager.recover("recovering", access=_access(journal), fault=revoke)
    assert (manager.root / ".staging" / "recovering" / "READY").exists()
    assert not (manager.root / "recovering").exists()
    assert (manager.root / "active.json").read_bytes() == before and manager.readiness()


def _revoke_return_authority(setup, scope):
    _, bundle, _, _, journal, ledger = setup
    source = bundle.sources[0]
    if scope == "unit":
        return ledger.record(source_id=source.source_id, snapshot=source.snapshot, file_hash=source.file_hash,
            configuration_version=source.configuration_version, decision="quarantined", reviewer="synthetic-test-only",
            reason="synthetic return race", scope="unit", subject_id=bundle.units[0].unit_id,
            subject_digest=unit_review_digest(bundle.units[0]))
    subject = source.source_id if scope == "source" else "credential1"
    return journal.block(scope, subject, "synthetic_return_race")


@pytest.mark.parametrize("scope", ["source", "credential", "unit"])
def test_revocation_during_defensive_copy_discards_entire_return(setup_generation, monkeypatch, scope):
    from app.contracts import EvidenceChunk
    manager, bundle, _, _, journal, _ = setup_generation
    _build(setup_generation)
    manager.promote("g1", access=_access(journal))
    pinned = manager.pin(_access(journal))
    original = EvidenceChunk.model_copy
    acknowledgements = []
    target = bundle.chunks[0].chunk_id
    def copy_with_confirmed_revocation(chunk, **kwargs):
        if kwargs.get("deep") and chunk.chunk_id == target and not acknowledgements:
            acknowledgements.append(_revoke_return_authority(setup_generation, scope))
        return original(chunk, **kwargs)
    monkeypatch.setattr(EvidenceChunk, "model_copy", copy_with_confirmed_revocation)
    with pytest.raises(ValueError):
        pinned.finish([chunk.chunk_id for chunk in bundle.chunks])
    assert len(acknowledgements) == 1


@pytest.mark.parametrize("scope", ["unit", "source"])
def test_final_return_checkpoint_holds_both_authorities(setup_generation, monkeypatch, scope):
    from threading import Event, Thread
    manager, bundle, _, _, journal, _ = setup_generation
    _build(setup_generation)
    manager.promote("g1", access=_access(journal))
    pinned = manager.pin(_access(journal))
    started, acknowledged, errors = Event(), Event(), []
    def writer():
        started.set()
        try:
            _revoke_return_authority(setup_generation, scope)
            acknowledged.set()
        except BaseException as error:
            errors.append(error)
    worker = Thread(target=writer)
    original = manager.ledger.state_digest
    calls = 0
    def checkpoint_with_writer():
        nonlocal calls
        calls += 1
        if calls == 2:
            worker.start()
            assert started.wait(2)
            assert not acknowledged.wait(0.25), "revoke acknowledged during final return checkpoint"
        return original()
    monkeypatch.setattr(manager.ledger, "state_digest", checkpoint_with_writer)
    try:
        result = pinned.finish([bundle.chunks[0].chunk_id])
        assert result[0] == bundle.chunks[0] and result[0] is not bundle.chunks[0]
        assert started.is_set()
    finally:
        if started.is_set():
            worker.join(5)
    assert not worker.is_alive() and not errors and acknowledged.is_set()


def test_finish_needs_only_query_permission_and_returns_defensive_proof(setup_generation):
    manager, bundle, _, _, journal, _ = setup_generation
    _build(setup_generation)
    manager.promote("g1", access=_access(journal))
    pinned = manager.pin(_access(journal, permissions=["query"]))
    result = pinned.finish([bundle.chunks[0].chunk_id])
    original_name = pinned.loaded.artifacts.bundle.chunks[0].parties[0].name
    result[0].parties[0].name = "synthetic changed copy"
    assert pinned.loaded.artifacts.bundle.chunks[0].parties[0].name == original_name


def test_final_return_failure_releases_both_authorities(setup_generation, monkeypatch):
    manager, bundle, _, _, journal, _ = setup_generation
    _build(setup_generation)
    manager.promote("g1", access=_access(journal))
    pinned = manager.pin(_access(journal))
    original = manager.ledger.state_digest
    calls = 0
    def fail_checkpoint():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic return failure")
        return original()
    monkeypatch.setattr(manager.ledger, "state_digest", fail_checkpoint)
    with pytest.raises(RuntimeError, match="synthetic return failure"):
        pinned.finish([bundle.chunks[0].chunk_id])
    assert _revoke_return_authority(setup_generation, "source").policy_epoch == 1
    assert _revoke_return_authority(setup_generation, "unit").decision == "quarantined"

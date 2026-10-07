"""Synthetic permanent regressions for canonical P4 proof and lexical parity."""
from datetime import datetime, timezone
import hashlib
import json
import os
import subprocess

import joblib
import pytest

from app.contracts import CitationUnit, EvidenceChunk, SourceIdentity, SourceSpan, UnitReference, UnitTextSpan
from app.ingestion.review_store import ReviewRecord, review_digest, unit_review_digest
from app.retrieval.generation_contracts import GenerationBundle, GenerationConfig, canonical_bytes
from app.retrieval.lexical import lexical_profile
from app.retrieval.generation_store import read_artifacts, write_artifacts


@pytest.fixture
def artifact_fixture():
    source = SourceIdentity(source_id="s1", instrument_id="i1", file_id="f1", doc_version=1,
        file_hash="a" * 64, snapshot="synthetic", configuration_version="cfg1",
        parser_name="docx_parser", parser_version="1.0.0")
    parties = [{"name": "Parte Sintética", "role": "contratante"}]
    units, chunks, records = [], [], []
    def review(record_id, scope="source", subject_id=None, subject_digest=None):
        return ReviewRecord(record_id=record_id, scope=scope, subject_id=subject_id,
            subject_digest=subject_digest, source_id="s1", snapshot="synthetic", file_hash="a" * 64,
            configuration_version="cfg1", decision="approved", reviewer="synthetic reviewer",
            reason="synthetic fixture only", recorded_at=datetime(2026, 10, 7, tzinfo=timezone.utc))
    records.append(review("source-review"))
    records.append(review("parties-review", "parties", "s1", review_digest({
        "contract_title": "Contrato sintético", "parties": parties})))
    for index, text in enumerate(("Não pagar, salvo cláusula 2.", "Exceção: pagar somente após aceite."), 1):
        span = SourceSpan(source_id="s1", block_id=f"b{index}", start=0, end=len(text),
            xml_part="word/document.xml", body_child_index=index - 1)
        unit = CitationUnit(unit_id=f"u{index}", instrument_id="i1", verbatim_text=text,
            source_ids=["s1"], block_ids=[span.block_id], spans=[span],
            source_identities=[source], location={"label": f"Cláusula {index}", "clause": str(index)},
            contract_title="Contrato sintético", parties=parties, reference_labels=[f"clause:{index}"],
            text_map=[UnitTextSpan(unit_start=0, unit_end=len(text), source_span=span)])
        if index == 1:
            literal = "cláusula 2"
            start = text.index(literal)
            unit.references = [UnitReference(reference_id="ref1", block_id="b1", start=start,
                end=start + len(literal), literal=literal, kind="clause", normalized_label="clause:2",
                state="bound", target_unit_ids=["u2"], binding_origin="exact_label")]
            unit.closure_unit_ids = ["u2"]
        record = review(f"review{index}", "unit", unit.unit_id, unit_review_digest(unit))
        unit.review_record_id = record.record_id
        unit.approval_state = "approved"
        units.append(unit)
        records.append(record)
        chunks.append(EvidenceChunk(chunk_id=f"c{index}", unit_id=unit.unit_id, source_id="s1",
            instrument_id="i1", doc_version=1, file_id="f1", file_hash="a" * 64,
            parser_name="docx_parser", parser_version="1.0.0", configuration_version="cfg1",
            derivation_key="b" * 64, block_ids=[span.block_id], spans=[span], verbatim_text=text,
            text_search="Contrato sintético\n" + text, parties=parties, location=unit.location,
            parent_id=unit.unit_id, approval_state="approved", review_record_id=record.record_id,
            unit_start=0, unit_end=len(text), token_count=30))
    bundle = GenerationBundle(sources=[source], units=units, chunks=chunks, reviews=records,
        relations=[dict(relation_id="r1", from_instrument_id="i2", to_instrument_id="i1",
            relation_type="amends", affected_unit_ids=["u1"], support_unit_ids=["u2"],
            support_spans=[units[1].spans[0]], snapshot="synthetic", configuration_version="cfg1")],
        resolutions=[dict(family_id="family1", snapshot="synthetic", registry_version="r-v1",
            registry_digest="c" * 64, relation_ids=["r1"], state="pending")],
        relation_review_sources={"r1": "s1"}, family_review_sources={"family1": "s1"})
    config = GenerationConfig(configuration_version="cfg1", synthetic=True,
        embedding_identity={"provider": "synthetic", "dimension": 3}, embedding_dimension=3,
        lexical_identity=lexical_profile())
    return bundle, {"c1": [1.0, 0.0, 0.0], "c2": [0.0, 1.0, 0.0]}, config


def _write(tmp_path, fixture):
    bundle, vectors, config = fixture
    directory = tmp_path / "generation"
    return directory, write_artifacts(directory, bundle, vectors=vectors, configuration=config)


def test_roundtrip_retains_every_canonical_field_and_bm25(artifact_fixture, tmp_path):
    bundle, vectors, config = artifact_fixture
    directory, checksums = _write(tmp_path, artifact_fixture)
    assert set(checksums) == {"bundle.json", "chunks.jsonl", "vectors.json", "bm25.joblib", "lexical.json"}
    result = read_artifacts(directory, checksums=checksums, configuration=config)
    assert canonical_bytes(result.bundle) == canonical_bytes(bundle)
    assert result.vectors == vectors
    assert [doc.metadata for doc in result.bm25.docs] == [chunk.model_dump(mode="json") for chunk in bundle.chunks]
    assert [doc.page_content for doc in result.bm25.docs] == [chunk.text_search for chunk in bundle.chunks]
    assert {doc.metadata["chunk_id"] for doc in result.bm25.invoke("NÃO cláusula")} == {"c1", "c2"}


@pytest.mark.parametrize("filename", ["bundle.json", "chunks.jsonl", "vectors.json", "bm25.joblib", "lexical.json"])
def test_all_hashes_validated_before_unpickle(artifact_fixture, tmp_path, monkeypatch, filename):
    directory, checksums = _write(tmp_path, artifact_fixture)
    (directory / filename).write_bytes(b"corrupt")
    monkeypatch.setattr(joblib, "load", lambda *a, **k: pytest.fail("unpickle before integrity"))
    with pytest.raises(ValueError):
        read_artifacts(directory, checksums=checksums, configuration=artifact_fixture[2])


@pytest.mark.parametrize("vectors", [{"c1": [1, 0, 0]}, {"c1": [1, 0, 0], "c2": [0, 1, 0], "extra": [1, 0, 0]},
    {"c1": [1, 0], "c2": [0, 1, 0]}, {"c1": [float("nan"), 0, 0], "c2": [0, 1, 0]},
    {"c1": [float("inf"), 0, 0], "c2": [0, 1, 0]}, {"c1": [True, 0, 0], "c2": [0, 1, 0]}])
def test_invalid_vectors_fail_before_artifacts(artifact_fixture, tmp_path, vectors):
    directory = tmp_path / "generation"
    with pytest.raises(ValueError):
        write_artifacts(directory, artifact_fixture[0], vectors=vectors, configuration=artifact_fixture[2])
    assert not directory.exists() or not list(directory.iterdir())


def test_mutated_model_is_revalidated_before_write(artifact_fixture, tmp_path):
    artifact_fixture[0].units[0].__dict__["approval_state"] = "unknown"
    with pytest.raises(ValueError):
        _write(tmp_path, artifact_fixture)


def test_refuses_overwriting_existing_generation(artifact_fixture, tmp_path):
    directory, checksums = _write(tmp_path, artifact_fixture)
    before = {name: (directory / name).read_bytes() for name in checksums}
    with pytest.raises(ValueError):
        write_artifacts(directory, artifact_fixture[0], vectors=artifact_fixture[1], configuration=artifact_fixture[2])
    assert before == {name: (directory / name).read_bytes() for name in checksums}


@pytest.mark.parametrize("filename", ["bundle.json", "chunks.jsonl", "vectors.json", "lexical.json"])
def test_rehashed_semantic_tamper_rejected_before_unpickle(artifact_fixture, tmp_path, monkeypatch, filename):
    directory, checksums = _write(tmp_path, artifact_fixture)
    path = directory / filename
    if filename == "bundle.json":
        content = json.loads(path.read_text("utf-8")); content["units"][0]["verbatim_text"] = "text changed"
    elif filename == "chunks.jsonl":
        content = json.loads(path.read_text("utf-8").splitlines()[0]); content["chunk_id"] = "extra"
    elif filename == "vectors.json":
        content = {"extra": [1, 0, 0]}
    else:
        content = {"profile": {"legacy": True}}
    path.write_bytes(canonical_bytes(content))
    checksums[filename] = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(joblib, "load", lambda *a, **k: pytest.fail("unpickle before semantic validation"))
    with pytest.raises(ValueError):
        read_artifacts(directory, checksums=checksums, configuration=artifact_fixture[2])


def test_rehashed_bm25_document_mismatch_rejected(artifact_fixture, tmp_path):
    directory, checksums = _write(tmp_path, artifact_fixture)
    path = directory / "bm25.joblib"
    bm25 = joblib.load(path)
    bm25.docs[0].metadata["unit_id"] = "arbitrary"
    joblib.dump(bm25, path)
    checksums[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    lexical = json.loads((directory / "lexical.json").read_text("utf-8"))
    lexical["artifact_sha256"] = checksums[path.name]
    (directory / "lexical.json").write_bytes(canonical_bytes(lexical))
    checksums["lexical.json"] = hashlib.sha256((directory / "lexical.json").read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        read_artifacts(directory, checksums=checksums, configuration=artifact_fixture[2])


@pytest.mark.parametrize("bad_key", ["../escape", "/absolute", "C:/escape", "a\\b", "./bundle.json"])
def test_manifest_path_escape_rejected_without_loading(artifact_fixture, tmp_path, monkeypatch, bad_key):
    directory, checksums = _write(tmp_path, artifact_fixture)
    checksums[bad_key] = "a" * 64
    monkeypatch.setattr(joblib, "load", lambda *a, **k: pytest.fail("unpickle escaped path"))
    with pytest.raises(ValueError):
        read_artifacts(directory, checksums=checksums, configuration=artifact_fixture[2])


def test_fault_during_bm25_leaves_no_loadable_partial(artifact_fixture, tmp_path):
    def fault(stage):
        assert stage == "during_bm25"
        raise RuntimeError("injected crash")
    directory = tmp_path / "generation"
    with pytest.raises(RuntimeError, match="injected"):
        write_artifacts(directory, artifact_fixture[0], vectors=artifact_fixture[1],
            configuration=artifact_fixture[2], fault=fault)
    assert not (directory / "bm25.joblib").exists()
    assert not (directory / "READY").exists()


@pytest.mark.parametrize("bad_key", ["bundle.json.", "bundle.json ", "nested/CON", "nested/name?", "nested/\x01name"])
def test_windows_path_aliases_rejected_before_unpickle(artifact_fixture, tmp_path, monkeypatch, bad_key):
    directory, checksums = _write(tmp_path, artifact_fixture)
    checksums[bad_key] = "a" * 64
    monkeypatch.setattr(joblib, "load", lambda *a, **k: pytest.fail("unpickle Windows alias"))
    with pytest.raises(ValueError, match="artifact_path_invalid"):
        read_artifacts(directory, checksums=checksums, configuration=artifact_fixture[2])


def test_read_refuses_directory_symlink_or_junction_before_unpickle(artifact_fixture, tmp_path, monkeypatch):
    directory, checksums = _write(tmp_path, artifact_fixture)
    alias = tmp_path / "alias"
    if os.name == "nt":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(directory)],
            capture_output=True, check=False)
        assert result.returncode == 0
    else:
        alias.symlink_to(directory, target_is_directory=True)
    monkeypatch.setattr(joblib, "load", lambda *a, **k: pytest.fail("unpickle link"))
    with pytest.raises(ValueError, match="link"):
        read_artifacts(alias, checksums=checksums, configuration=artifact_fixture[2])


@pytest.mark.parametrize("attribute,value", [("k1", 9.0), ("idf", {"pagar": 999.0})])
def test_rehashed_bm25_internal_index_mutation_rejected(artifact_fixture, tmp_path, attribute, value):
    directory, checksums = _write(tmp_path, artifact_fixture)
    path = directory / "bm25.joblib"
    bm25 = joblib.load(path)
    setattr(bm25.vectorizer, attribute, value)
    joblib.dump(bm25, path)
    checksums[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    lexical_path = directory / "lexical.json"
    lexical = json.loads(lexical_path.read_text("utf-8"))
    lexical["artifact_sha256"] = checksums[path.name]
    lexical_path.write_bytes(canonical_bytes(lexical))
    checksums[lexical_path.name] = hashlib.sha256(lexical_path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="index_parity"):
        read_artifacts(directory, checksums=checksums, configuration=artifact_fixture[2])


def test_extra_manifest_hash_is_verified_before_unpickle(artifact_fixture, tmp_path, monkeypatch):
    directory, checksums = _write(tmp_path, artifact_fixture)
    extra = directory / "extra.json"
    extra.write_bytes(b"external to canonical artifacts")
    checksums[extra.name] = "a" * 64
    monkeypatch.setattr(joblib, "load", lambda *a, **k: pytest.fail("unpickle before extra checksum"))
    with pytest.raises(ValueError):
        read_artifacts(directory, checksums=checksums, configuration=artifact_fixture[2])


def test_configuration_change_blocks_reload_before_unpickle(artifact_fixture, tmp_path, monkeypatch):
    directory, checksums = _write(tmp_path, artifact_fixture)
    config = artifact_fixture[2].model_copy(update={"configuration_version": "cfg2"})
    monkeypatch.setattr(joblib, "load", lambda *a, **k: pytest.fail("unpickle incompatible configuration"))
    with pytest.raises(ValueError, match="identity"):
        read_artifacts(directory, checksums=checksums, configuration=config)


def test_multiple_writes_are_order_deterministic(artifact_fixture, tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    bundle, vectors, config = artifact_fixture
    first_checksums = write_artifacts(first, bundle, vectors=vectors, configuration=config)
    second_checksums = write_artifacts(second, bundle,
        vectors=dict(reversed(list(vectors.items()))), configuration=config)
    assert first_checksums == second_checksums


def test_huge_integer_vector_fails_closed_before_write(artifact_fixture, tmp_path):
    with pytest.raises(ValueError, match="vector_values"):
        write_artifacts(tmp_path / "generation", artifact_fixture[0],
            vectors={"c1": [10 ** 400, 0, 0], "c2": [0, 1, 0]}, configuration=artifact_fixture[2])


@pytest.mark.parametrize("payload", [b'{"c1":[1,0,0],"c1":[1,0,0],"c2":[0,1,0]}',
    b'{"c1":[NaN,0,0],"c2":[0,1,0]}', b' {"c1":[1,0,0],"c2":[0,1,0]}', b'not-json'])
def test_rehashed_invalid_json_rejected_before_unpickle(artifact_fixture, tmp_path, monkeypatch, payload):
    directory, checksums = _write(tmp_path, artifact_fixture)
    (directory / "vectors.json").write_bytes(payload)
    checksums["vectors.json"] = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(joblib, "load", lambda *a, **k: pytest.fail("unpickle malformed JSON"))
    with pytest.raises(ValueError):
        read_artifacts(directory, checksums=checksums, configuration=artifact_fixture[2])


def test_bm25_tags_and_metadata_cannot_add_hidden_runtime_context(artifact_fixture, tmp_path):
    directory, checksums = _write(tmp_path, artifact_fixture)
    path = directory / "bm25.joblib"
    bm25 = joblib.load(path)
    bm25.metadata = {"untrusted": "hidden callback context"}
    joblib.dump(bm25, path)
    checksums[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    lexical_path = directory / "lexical.json"
    lexical = json.loads(lexical_path.read_text("utf-8"))
    lexical["artifact_sha256"] = checksums[path.name]
    lexical_path.write_bytes(canonical_bytes(lexical))
    checksums[lexical_path.name] = hashlib.sha256(lexical_path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="index_parity"):
        read_artifacts(directory, checksums=checksums, configuration=artifact_fixture[2])

"""Permanent synthetic regressions for the governed P3B builders."""
import pytest
import hashlib
from pathlib import Path
from threading import Lock


def test_physical_coordinates_are_separate_from_literal_offsets():
    from app.contracts import SourceSpan
    span = SourceSpan(source_id="s", block_id="b", start=0, end=5,
                      xml_part="word/document.xml", body_child_index=8,
                      row_index=2, column_index=3)
    assert (span.start, span.end) == (0, 5)
    assert (span.body_child_index, span.row_index, span.column_index) == (8, 2, 3)


def test_unit_keeps_source_identity_and_parent_context():
    from app.contracts import CitationUnit, SourceIdentity, SourceSpan
    source = SourceIdentity(source_id="s", instrument_id="i", doc_version=1,
                            file_id="f", file_hash="a" * 64, snapshot="snapshot",
                            configuration_version="cfg", parser_name="docx", parser_version="v1")
    unit = CitationUnit(unit_id="u", instrument_id="i", verbatim_text="salvo condição",
                        source_ids=["s"], block_ids=["b"],
                        spans=[SourceSpan(source_id="s", block_id="b", start=0, end=14)],
                        location={"label": "Parágrafo 2"}, source_identities=[source],
                        contract_title="Contrato sintético", parent_unit_id="parent",
                        synthetic_context="Numeração reconstruída")
    assert unit.parent_unit_id == "parent"
    assert unit.source_identities[0].doc_version == 1


def test_reference_candidate_records_exact_literal_and_pending_binding():
    from app.contracts import UnitReference
    reference = UnitReference(reference_id="ref:b:2:12", block_id="b", start=2, end=12,
                              literal="Cláusula 2", normalized_label="clause:2", kind="clause")
    assert reference.state == "pending" and not reference.target_unit_ids
    with pytest.raises(ValueError):
        UnitReference(**(reference.model_dump() | {"state": "bound"}))


@pytest.fixture
def evidence_fixture(tmp_path):
    from zipfile import ZipFile
    from app.contracts import SourceIdentity
    from app.ingestion.docx_parser import extract_docx_blocks
    from app.ingestion.schemas import ParsedDocument, ContractMetadata, ContractParty
    path = tmp_path / "synthetic.docx"
    paragraphs = ["Cláusula 1ª - Não pagar, salvo condição expressa. " * 12,
                  "§ 1º Exceção: somente após aprovação expressa."]
    xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
    xml += "".join(f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p>' for text in paragraphs)
    xml += '</w:body></w:document>'
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)
    blocks = extract_docx_blocks(path, doc_id=77, doc_version=1)
    parsed = ParsedDocument(doc_id=77, doc_version=1, file_id="f1", file_path=str(path),
                            file_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
                            parser_name="docx_parser", parser_version="1.0.0", blocks=blocks,
                            metadata=ContractMetadata(formal_title="Contrato sintético",
                                instrument_type="Contrato", parties=[ContractParty(name="Parte Sintética")]))
    source = SourceIdentity(source_id="doc:77:v1:file:f1", instrument_id="doc:77",
                            doc_version=1, file_id="f1", file_hash=parsed.file_hash,
                            snapshot="s1", configuration_version="cfg1", parser_name="docx_parser", parser_version="1.0.0")
    return parsed, source, path


@pytest.fixture
def tiny_budget():
    from app.ingestion.evidence import QwenTokenBudget
    from app.embeddings.qwen import QwenProfile
    class Tokenizer:
        def encode(self, text, **kwargs):
            return list(text)
    budget = object.__new__(QwenTokenBudget)
    budget._tokenizer, budget._lock, budget.profile = Tokenizer(), Lock(), QwenProfile(max_input_tokens=250)
    budget._identity = {"synthetic_test_only": True, "profile": budget.profile.specification}
    return budget


def _approve(ledger, source, units):
    from app.ingestion.review_store import review_digest, unit_review_digest
    base = dict(source_id=source.source_id, snapshot=source.snapshot, file_hash=source.file_hash,
                configuration_version=source.configuration_version, decision="approved",
                reviewer="synthetic-reviewer", reason="synthetic fixture; no real approval")
    ledger.record(**base)
    parties_digest = review_digest({"contract_title": units[0].contract_title,
                                  "parties": [p.model_dump(mode="json") for p in units[0].parties]})
    ledger.record(**base, scope="parties", subject_id=source.source_id, subject_digest=parties_digest)
    return {u.unit_id: ledger.record(**base, scope="unit", subject_id=u.unit_id,
                                   subject_digest=unit_review_digest(u)).record_id for u in units}


def test_builders_require_current_scoped_decisions(evidence_fixture, tmp_path, tiny_budget):
    from app.ingestion.evidence import prepare_citation_units, build_evidence_chunks
    from app.ingestion.review_store import ReviewStore
    document, source, path = evidence_fixture
    units = prepare_citation_units(document, source, original_path=path)
    ledger = ReviewStore(tmp_path / "reviews.sqlite")
    with pytest.raises(ValueError):
        build_evidence_chunks(document, source, ledger=ledger, budget=tiny_budget,
                              original_path=path, unit_review_ids={u.unit_id: "invented" for u in units})
    reviews = _approve(ledger, source, units)
    built = build_evidence_chunks(document, source, ledger=ledger, budget=tiny_budget,
                                  original_path=path, unit_review_ids=reviews)
    assert built.units and len(built.chunks) > 1
    assert all(c.token_count <= 250 for c in built.chunks)
    for unit in built.units:
        chunks = [c for c in built.chunks if c.unit_id == unit.unit_id]
        assert "".join(c.verbatim_text for c in chunks) == unit.verbatim_text
        assert all(c.parent_id == unit.unit_id for c in chunks)
    ledger.record(source_id=source.source_id, snapshot=source.snapshot,
                  file_hash=source.file_hash, configuration_version=source.configuration_version,
                  decision="quarantined", reviewer="synthetic", reason="revocation fixture")
    with pytest.raises(ValueError):
        build_evidence_chunks(document, source, ledger=ledger, budget=tiny_budget,
                              original_path=path, unit_review_ids=reviews)


def test_public_chunking_refuses_unverified_legacy_candidates(evidence_fixture):
    from app.ingestion.chunker import create_legal_chunks
    with pytest.raises(ValueError, match="governed"):
        create_legal_chunks([evidence_fixture[0]])


def test_incomplete_units_and_changed_text_are_not_approved(evidence_fixture, tmp_path, tiny_budget):
    from app.ingestion.evidence import prepare_citation_units, build_evidence_chunks
    from app.ingestion.review_store import ReviewStore
    document, source, path = evidence_fixture
    ledger = ReviewStore(tmp_path / "reviews.sqlite")
    units = prepare_citation_units(document, source, original_path=path)
    reviews = _approve(ledger, source, units)
    document.blocks[-1].text_raw = "Pagar sem condição."
    with pytest.raises(ValueError):
        build_evidence_chunks(document, source, ledger=ledger, budget=tiny_budget,
                              original_path=path, unit_review_ids=reviews)


def test_ledger_change_alters_derivation_and_public_projection(evidence_fixture, tmp_path, tiny_budget):
    from app.ingestion.evidence import prepare_citation_units, build_evidence_chunks, EvidenceBuildRequest
    from app.ingestion.review_store import ReviewStore
    from app.ingestion.chunker import create_legal_chunks
    document, source, path = evidence_fixture
    ledger = ReviewStore(tmp_path / "reviews.sqlite")
    units = prepare_citation_units(document, source, original_path=path)
    reviews = _approve(ledger, source, units)
    first = build_evidence_chunks(document, source, ledger=ledger, budget=tiny_budget,
                                 original_path=path, unit_review_ids=reviews)
    ledger.record(source_id="synthetic-modifier", snapshot="s1", file_hash="b" * 64,
                  configuration_version="cfg1", scope="relation", subject_id="relation:r1",
                  subject_digest="c" * 64, decision="quarantined", reviewer="synthetic",
                  reason="new material risk in synthetic registry")
    second = build_evidence_chunks(document, source, ledger=ledger, budget=tiny_budget,
                                  original_path=path, unit_review_ids=reviews)
    assert first.derivation_key != second.derivation_key
    projected = create_legal_chunks([document], ledger=ledger, budget=tiny_budget,
        build_requests=[EvidenceBuildRequest(source, path, reviews)])
    assert all(p.metadata["parent_id"] in {u.unit_id for u in units} for p in projected)
    assert "".join(p.metadata["verbatim_text"] for p in projected) == "".join(u.verbatim_text for u in units)


def test_revocation_during_split_returns_no_partial_build(evidence_fixture, tmp_path, tiny_budget, monkeypatch):
    from app.ingestion.evidence import prepare_citation_units, build_evidence_chunks
    from app.ingestion.review_store import ReviewStore
    document, source, path = evidence_fixture
    ledger = ReviewStore(tmp_path / "reviews.sqlite")
    units = prepare_citation_units(document, source, original_path=path)
    reviews = _approve(ledger, source, units)
    original_split = tiny_budget.split
    def split_and_revoke(text, context):
        ledger.record(source_id=source.source_id, snapshot=source.snapshot, file_hash=source.file_hash,
                      configuration_version=source.configuration_version, decision="quarantined",
                      reviewer="synthetic", reason="revocation during build")
        return original_split(text, context)
    monkeypatch.setattr(tiny_budget, "split", split_and_revoke)
    with pytest.raises(ValueError):
        build_evidence_chunks(document, source, ledger=ledger, budget=tiny_budget,
                              original_path=path, unit_review_ids=reviews)


def test_physical_change_during_split_blocks_return(evidence_fixture, tmp_path, tiny_budget, monkeypatch):
    from app.ingestion.evidence import prepare_citation_units, build_evidence_chunks
    from app.ingestion.review_store import ReviewStore
    document, source, path = evidence_fixture
    ledger = ReviewStore(tmp_path / "reviews.sqlite")
    units = prepare_citation_units(document, source, original_path=path)
    reviews = _approve(ledger, source, units)
    original_split = tiny_budget.split
    def split_and_change(text, context):
        path.write_bytes(b"changed synthetic physical artifact")
        return original_split(text, context)
    monkeypatch.setattr(tiny_budget, "split", split_and_change)
    with pytest.raises(ValueError):
        build_evidence_chunks(document, source, ledger=ledger, budget=tiny_budget,
                              original_path=path, unit_review_ids=reviews)


@pytest.mark.parametrize("field,value", [("configuration_version", "other"), ("doc_version", 2),
                                          ("file_hash", "f" * 64)])
def test_source_identity_changes_require_new_proof(evidence_fixture, tmp_path, tiny_budget, field, value):
    from app.ingestion.evidence import prepare_citation_units, build_evidence_chunks
    from app.ingestion.review_store import ReviewStore
    document, source, path = evidence_fixture
    ledger = ReviewStore(tmp_path / "reviews.sqlite")
    units = prepare_citation_units(document, source, original_path=path)
    reviews = _approve(ledger, source, units)
    changed = source.model_copy(update={field: value})
    with pytest.raises(ValueError):
        build_evidence_chunks(document, changed, ledger=ledger, budget=tiny_budget,
                              original_path=path, unit_review_ids=reviews)


def test_real_qwen_unicode_split_and_header_budget(request):
    from app.ingestion.evidence import QwenTokenBudget
    from app.embeddings.qwen import QwenProfile
    artifact = request.config.getoption("--qwen-artifact")
    if not artifact:
        pytest.skip("requires explicit installed local Qwen artifact")
    budget = QwenTokenBudget(Path(artifact), QwenProfile(max_input_tokens=48))
    text = ("§ 2º NÃO pagar 👩🏽‍⚖️, salvo exceção e condição expressa. e\u0301\n\n" * 20)
    header = "Contrato sintético; partes e localização\n"
    segments = budget.split(text, header)
    assert "".join(s.text for s in segments) == text
    assert all(budget.count(header + s.text) <= 48 for s in segments)
    assert [(s.start, s.end) for s in segments][0][0] == 0
    assert segments[-1].end == len(text)
    with pytest.raises(ValueError):
        budget.split("Não pagar", "Cabeçalho excessivo " * 100)


def test_build_to_closure_requires_family_then_preserves_whole_proof(evidence_fixture, tmp_path, tiny_budget):
    from app.contracts import RelationResolution
    from app.ingestion.evidence import prepare_citation_units, build_evidence_chunks
    from app.ingestion.review_store import ReviewStore, review_digest
    from app.ingestion.closure import resolve_closure, render_units
    document, source, path = evidence_fixture
    ledger = ReviewStore(tmp_path / "reviews.sqlite")
    units = prepare_citation_units(document, source, original_path=path)
    reviews = _approve(ledger, source, units)
    built = build_evidence_chunks(document, source, ledger=ledger, budget=tiny_budget,
                                  original_path=path, unit_review_ids=reviews)
    args = dict(units={u.unit_id: u for u in built.units}, sources={source.source_id: source},
                ledger=ledger, count_tokens=tiny_budget.count, max_tokens=10000)
    selected = [built.chunks[0].unit_id]
    blocked = resolve_closure(selected, **args)
    assert blocked.status == "blocked" and not blocked.units
    family = RelationResolution(family_id="family:s1", snapshot=source.snapshot, registry_version="registry:v1",
                                registry_digest=ledger.relation_state_digest())
    family = family.model_copy(update={"state": "resolved"})
    review = ledger.record(source_id=source.source_id, snapshot=source.snapshot,
        file_hash=source.file_hash, configuration_version=source.configuration_version,
        scope="family", subject_id=family.family_id,
        subject_digest=review_digest(family.model_dump(mode="json", exclude={"review_record_id"})),
        decision="approved", reviewer="synthetic", reason="synthetic complete registry fixture")
    family = family.model_copy(update={"review_record_id": review.record_id})
    resolved_args = args | dict(resolutions=[family], family_review_sources={family.family_id: source.source_id})
    complete = resolve_closure(selected, **resolved_args)
    assert complete.status == "complete"
    assert complete.units[0].verbatim_text == built.units[0].verbatim_text
    assert complete.token_count == tiny_budget.count(render_units(complete.units))
    over = resolve_closure(selected, **(resolved_args | {"max_tokens": complete.token_count - 1}))
    assert over.reason_code == "closure_over_budget" and not over.units


@pytest.mark.parametrize("target", ["original", "conversion", "source", "parties", "unit"])
def test_public_batch_revalidates_every_earlier_source(tmp_path, tiny_budget, monkeypatch, target):
    from app.ingestion.evidence import prepare_citation_units, EvidenceBuildRequest
    from app.ingestion.review_store import ReviewStore, review_digest, unit_review_digest
    from app.ingestion.chunker import create_legal_chunks
    from app.ingestion.docx_parser import extract_docx_blocks
    ledger = ReviewStore(tmp_path / "reviews.sqlite")
    documents, requests = [], []
    for offset in range(2):
        directory = tmp_path / f"fixture{offset}"
        directory.mkdir()
        document, source, path = evidence_fixture.__wrapped__(directory)
        document.doc_id += offset
        document.blocks = extract_docx_blocks(path, doc_id=document.doc_id, doc_version=1)
        source = source.model_copy(update={"source_id": f"doc:{document.doc_id}:v1:file:f1",
                                          "instrument_id": f"doc:{document.doc_id}"})
        conversion = None
        if offset == 0 and target == "conversion":
            conversion = path
            original = directory / "original.doc"
            original.write_bytes(b"synthetic retained legacy original")
            document.file_path, document.file_hash = str(original), hashlib.sha256(original.read_bytes()).hexdigest()
            document.parser_name = "legacy_parser"
            for block in document.blocks:
                block.source_locator.update(format="converted_docx", original_format=".doc",
                                             conversion_artifact_retained=True)
            source = source.model_copy(update={"file_hash": document.file_hash,
                "parser_name": "legacy_parser", "conversion_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            path = original
        units = prepare_citation_units(document, source, original_path=path, conversion_path=conversion)
        reviews = _approve(ledger, source, units)
        documents.append(document)
        requests.append(EvidenceBuildRequest(source, path, reviews, conversion_path=conversion))
    count = 0
    original_split = tiny_budget.split
    def mutate_earlier_source(text, context):
        nonlocal count
        count += 1
        if count == 2:
            request = requests[0]
            if target in {"original", "conversion"}:
                (request.original_path if target == "original" else request.conversion_path).write_bytes(b"changed fixture")
            else:
                first = prepare_citation_units(documents[0], request.source, original_path=request.original_path)[0]
                digest = unit_review_digest(first) if target == "unit" else review_digest({
                    "contract_title": first.contract_title, "parties": [p.model_dump(mode="json") for p in first.parties]})
                ledger.record(source_id=request.source.source_id, snapshot=request.source.snapshot,
                    file_hash=request.source.file_hash, configuration_version=request.source.configuration_version,
                    scope=target, subject_id=first.unit_id if target == "unit" else request.source.source_id,
                    subject_digest=None if target == "source" else digest, decision="quarantined",
                    reviewer="synthetic", reason="revocation during later file")
        return original_split(text, context)
    monkeypatch.setattr(tiny_budget, "split", mutate_earlier_source)
    with pytest.raises(ValueError):
        create_legal_chunks(documents, ledger=ledger, budget=tiny_budget, build_requests=requests)


def _reference_fixture(tmp_path, target="2ª"):
    from zipfile import ZipFile
    from app.ingestion.docx_parser import extract_docx_blocks
    document, source, path = evidence_fixture.__wrapped__(tmp_path)
    texts = [f"Cláusula 1ª - Pagar honorários, ressalvada a exceção da Cláusula {target}.",
             "Cláusula 2ª - Não pagar se o serviço não for concluído."]
    xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
    xml += "".join(f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p>' for text in texts)
    xml += '</w:body></w:document>'
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)
    document.blocks = extract_docx_blocks(path, document.doc_id, document.doc_version)
    document.file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    source = source.model_copy(update={"file_hash": document.file_hash})
    assert len(document.blocks) == 2
    return document, source, path


def test_express_reference_preserves_exception_without_optional_dependencies(tmp_path, tiny_budget):
    from app.ingestion.evidence import prepare_citation_units, build_evidence_chunks
    from app.ingestion.review_store import ReviewStore
    from app.ingestion.closure import resolve_closure
    document, source, path = _reference_fixture(tmp_path)
    units = prepare_citation_units(document, source, original_path=path)
    assert len(units) == 2
    assert units[1].unit_id in units[0].closure_unit_ids
    assert any(r.normalized_label == "clause:2" and r.state == "bound" for r in units[0].references)
    ledger = ReviewStore(tmp_path / "reviews.sqlite")
    reviews = _approve(ledger, source, units)
    built = build_evidence_chunks(document, source, ledger=ledger, budget=tiny_budget,
                                  original_path=path, unit_review_ids=reviews)
    args = dict(units={u.unit_id: u for u in built.units}, sources={source.source_id: source},
                ledger=ledger, count_tokens=tiny_budget.count, max_tokens=10000, evidence_scope="original_text")
    complete = resolve_closure([built.units[0].unit_id], **args)
    assert complete.status == "complete" and len(complete.units) == 2
    assert any("Não pagar" in u.verbatim_text for u in complete.units)
    changed = built.units[0].model_copy(update={"references": [], "closure_unit_ids": []})
    blocked = resolve_closure([changed.unit_id], **(args | {"units": args["units"] | {changed.unit_id: changed}}))
    assert not blocked.units


def test_unresolved_reference_cannot_be_promoted_by_generic_unit_review(tmp_path, tiny_budget):
    from app.ingestion.evidence import prepare_citation_units, build_evidence_chunks
    from app.ingestion.review_store import ReviewStore
    document, source, path = _reference_fixture(tmp_path, target="9ª")
    units = prepare_citation_units(document, source, original_path=path)
    assert any(r.state == "pending" and r.normalized_label == "clause:9" for r in units[0].references)
    ledger = ReviewStore(tmp_path / "reviews.sqlite")
    reviews = _approve(ledger, source, units)
    with pytest.raises(ValueError):
        build_evidence_chunks(document, source, ledger=ledger, budget=tiny_budget,
                              original_path=path, unit_review_ids=reviews)
    pending = next(r for r in units[0].references if r.state == "pending")
    bindings = {pending.reference_id: [units[1].unit_id]}
    resolved = prepare_citation_units(document, source, original_path=path, reference_bindings=bindings)
    with pytest.raises(ValueError):
        build_evidence_chunks(document, source, ledger=ledger, budget=tiny_budget,
                              original_path=path, unit_review_ids=reviews, reference_bindings=bindings)
    new_reviews = _approve(ledger, source, resolved)
    built = build_evidence_chunks(document, source, ledger=ledger, budget=tiny_budget,
                                  original_path=path, unit_review_ids=new_reviews, reference_bindings=bindings)
    assert built.units[1].unit_id in built.units[0].closure_unit_ids
    assert built.units[0].verbatim_text == units[0].verbatim_text  # mapping never rewrites the reference


@pytest.mark.parametrize("connector", ["e/ou", "e / ou", ";", ", bem como"])
def test_compound_reference_physical_build_closure_keeps_every_exception(tmp_path, tiny_budget, connector):
    from zipfile import ZipFile
    from app.ingestion.docx_parser import extract_docx_blocks
    from app.ingestion.evidence import prepare_citation_units, build_evidence_chunks
    from app.ingestion.review_store import ReviewStore, review_digest
    from app.ingestion.closure import resolve_closure
    from app.contracts import RelationResolution
    document, source, path = evidence_fixture.__wrapped__(tmp_path)
    texts = [f"Cláusula 1ª - Pagar honorários, ressalvadas as exceções das cláusulas 2ª {connector} 3ª.",
             "Cláusula 2ª - Pagar somente após emissão da nota fiscal.",
             "Cláusula 3ª - Não pagar se o serviço não for concluído."]
    xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
    xml += "".join(f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p>' for text in texts)
    xml += '</w:body></w:document>'
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)
    document.blocks = extract_docx_blocks(path, document.doc_id, document.doc_version)
    document.file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    source = source.model_copy(update={"file_hash": document.file_hash})
    assert len(document.blocks) == 3
    units = prepare_citation_units(document, source, original_path=path)
    assert set(units[0].closure_unit_ids) == {units[1].unit_id, units[2].unit_id}
    ledger = ReviewStore(tmp_path / "reviews.sqlite")
    reviews = _approve(ledger, source, units)
    built = build_evidence_chunks(document, source, ledger=ledger, budget=tiny_budget,
                                  original_path=path, unit_review_ids=reviews)
    resolution = RelationResolution(family_id="synthetic-family", snapshot=source.snapshot,
        registry_version="synthetic-registry", registry_digest=ledger.relation_state_digest(),
        relation_ids=[])
    resolution = resolution.model_copy(update={"state": "resolved"})
    record = ledger.record(source_id=source.source_id, snapshot=source.snapshot, file_hash=source.file_hash,
        configuration_version=source.configuration_version, scope="family", subject_id=resolution.family_id,
        subject_digest=review_digest(resolution.model_dump(mode="json", exclude={"review_record_id"})),
        decision="approved", reviewer="synthetic-fixture", reason="synthetic complete family")
    resolution = resolution.model_copy(update={"review_record_id": record.record_id})
    closure = resolve_closure([built.units[0].unit_id], units={u.unit_id: u for u in built.units},
        sources={source.source_id: source}, ledger=ledger, resolutions=[resolution],
        family_review_sources={resolution.family_id: source.source_id}, evidence_scope="linked_instruments",
        count_tokens=tiny_budget.count, max_tokens=10000)
    assert closure.status == "complete" and len(closure.units) == 3
    assert {u.verbatim_text for u in closure.units} == set(texts)

"""Declared bulk approval -> reviewed bundle, on synthetic staged documents only."""
import hashlib
import json
from pathlib import Path

import pytest

from app.contracts import AccessContext
from app.corpus_build import Approval, build_reviewed_bundle, load_staged_sources, main
from app.ingestion.docx_parser import extract_docx_blocks
from app.ingestion.review_store import ReviewStore
from app.ingestion.schemas import ContractMetadata, ContractParty, ParsedDocument, PartyRole
from app.retrieval.generation_contracts import GenerationConfig
from app.retrieval.generations import GenerationManager
from app.retrieval.lexical import lexical_profile
from app.retrieval.policy import SQLitePolicyJournal
from app.retrieval.vector_store import ChromaVectorStore
from tests.p6_corpus import SyntheticBudget, SyntheticEncoder, _write_docx

SNAPSHOT, CONFIGURATION = "snap-mvp", "cfg-mvp"
PARTIES = [ContractParty(name="Alpha Serviços Ltda", role=PartyRole.CONTRATANTE, clean_identifier="11222333000144"),
           ContractParty(name="Andrade Advogados", role=PartyRole.CONTRATADA, is_law_firm=True)]
DOCS = {
    101: (["Cláusula 1ª - Os honorários são de R$ 50.000,00, observada a Cláusula 9ª.",
           "Cláusula 2ª - Foro de Belo Horizonte."], PARTIES, "success"),
    102: (["Cláusula 1ª - Prestação de consultoria mensal.", "Cláusula 2ª - Vigência de doze meses."],
          PARTIES, "success"),
    103: (["Cláusula 1ª - Texto sem partes extraídas."], [], "success"),
    104: (["Cláusula 1ª - Contrato com divergência de cliente no M-Files."], PARTIES, "review_metadata_mismatch"),
}
APPROVAL = Approval("revisor-teste", "aprovação em lote sintética", self_bind_unresolved_references=True)


def _stage(tmp_path):
    staging, raw = tmp_path / "staging", tmp_path / "raw" / "files"
    staging.mkdir(parents=True)
    for doc_id, (paragraphs, parties, status) in DOCS.items():
        folder = raw / str(doc_id)
        folder.mkdir(parents=True)
        original = folder / f"{doc_id}_v1.docx"
        _write_docx(original, paragraphs)
        digest = hashlib.sha256(original.read_bytes()).hexdigest()
        blocks = [block.model_dump(mode="json") | {"block_id": "stale-" + block.block_id}
                  for block in extract_docx_blocks(original, doc_id=doc_id, doc_version=1)]
        parsed = ParsedDocument.model_construct(doc_id=doc_id, doc_version=1, file_id=f"f{doc_id}",
                                file_path=f"C:\\old\\data\\raw\\files\\{doc_id}\\{doc_id}_v1.docx", file_hash=digest,
                                parser_name="docx_parser", parser_version="1.0.0", blocks=[],
                                metadata=ContractMetadata(formal_title=f"Contrato {doc_id}",
                                                          instrument_type="Contrato", parties=parties),
                                status=status, error_message=None, eligibility="quarantined",
                                approval_record_id=None).model_dump(mode="json") | {"blocks": blocks}
        _entry(staging, doc_id, digest, parsed)
    _entry(staging, 105, "0" * 64, {"blocks": [], "parser_name": None})
    return staging, raw


def _entry(staging, doc_id, digest, parsed):
    name = hashlib.sha256(str(doc_id).encode()).hexdigest() + ".json"
    (staging / name).write_text(json.dumps({
        "source_id": name[:-5], "mfiles_id": doc_id, "mfiles_version": 1, "file_id": f"f{doc_id}",
        "file_hash": digest, "snapshot": SNAPSHOT, "configuration_version": CONFIGURATION,
        "technical_status": parsed.get("status") or "failed", "eligibility": "quarantined",
        "parse_route": "docx", "risks": [], "parsed_document": parsed}), encoding="utf-8")


def test_staging_status_never_approves_and_relaxations_are_opt_in(tmp_path):
    staging, raw = _stage(tmp_path)
    strict, skipped = load_staged_sources(staging, raw)
    reasons = {item["mfiles_id"]: item["reason"] for item in skipped}
    assert reasons == {104: "metadata_mismatch_not_adjudicated", 105: "no_parsed_text"}
    assert {s.identity.instrument_id for s in strict} == {"doc:101", "doc:102", "doc:103"}
    accepted, _ = load_staged_sources(staging, raw, accept_metadata_mismatch=True)
    assert "doc:104" in {s.identity.instrument_id for s in accepted}
    source = next(s for s in strict if s.identity.instrument_id == "doc:101")
    assert source.identity.source_id == "doc:101:v1:file:f101"
    assert source.original_path == (raw / "101" / "101_v1.docx").resolve()
    assert not any(block.block_id.startswith("stale-") for block in source.document.blocks)
    with pytest.raises(ValueError):
        Approval(" ", "motivo")


def test_rehearsal_failures_leave_no_ledger_record_and_approvals_are_attributed(tmp_path):
    staging, raw = _stage(tmp_path)
    staged, _ = load_staged_sources(staging, raw)
    ledger = ReviewStore(tmp_path / "authority" / "reviews.sqlite")
    strict = Approval("revisor-teste", "sem auto-vínculo")
    result = build_reviewed_bundle(staged, ledger=ledger, budget=SyntheticBudget(), approval=strict)
    bundle, paths, rejected = result.bundle, result.source_paths, result.rejected
    assert {r["instrument_id"]: r["reason"] for r in rejected} == {"doc:101": "ValueError:reference_binding_unresolved"}
    assert [r["instrument_id"] for r in result.reserved] == ["doc:103"]  # no parties: reserved, never approved
    assert result.self_bound == 0 and [s.instrument_id for s in bundle.sources] == ["doc:102"]
    assert ledger.history("doc:101:v1:file:f101") == [] and ledger.history("doc:103:v1:file:f103") == []
    records = ledger.history("doc:102:v1:file:f102")
    assert {r.scope for r in records} == {"source", "parties", "unit", "family"}
    assert all(r.reviewer == "revisor-teste" and r.decision == "approved" for r in records)
    assert set(paths) == {"doc:102:v1:file:f102"}


def test_self_bound_reference_keeps_literal_without_expanding_closure(tmp_path):
    staging, raw = _stage(tmp_path)
    staged, _ = load_staged_sources(staging, raw)
    ledger = ReviewStore(tmp_path / "authority" / "reviews.sqlite")
    result = build_reviewed_bundle(staged, ledger=ledger, budget=SyntheticBudget(), approval=APPROVAL)
    bundle = result.bundle
    assert result.self_bound == 1 and result.rejected == [] and [r["instrument_id"] for r in result.reserved] == ["doc:103"]
    unit = next(u for u in bundle.units if "Cláusula 9ª" in u.verbatim_text)
    [reference] = [ref for ref in unit.references if "9" in ref.literal]
    assert (reference.state, reference.binding_origin, reference.target_unit_ids) == (
        "bound", "review_mapping", [unit.unit_id])
    assert all(ref.binding_origin == "exact_label" for ref in unit.references if ref is not reference)
    assert unit.closure_unit_ids == []


def test_reviewed_bundle_builds_and_promotes_through_accepted_p4_manager(tmp_path):
    staging, raw = _stage(tmp_path)
    staged, _ = load_staged_sources(staging, raw)
    ledger = ReviewStore(tmp_path / "authority" / "reviews.sqlite")
    journal = SQLitePolicyJournal(tmp_path / "authority" / "policy.sqlite", initialize=True, journal_id="mvp-test")
    result = build_reviewed_bundle(staged, ledger=ledger, budget=SyntheticBudget(), approval=APPROVAL)
    bundle, paths = result.bundle, result.source_paths
    encoder = SyntheticEncoder()
    configuration = GenerationConfig(configuration_version=CONFIGURATION, embedding_identity=encoder.identity,
                                     embedding_dimension=3, lexical_identity=lexical_profile(), synthetic=True)
    manager = GenerationManager(tmp_path / "indexes", configuration=configuration, embeddings=encoder,
                                vector_store=ChromaVectorStore(mode="embedded", dimension=3), ledger=ledger,
                                journal=journal, journal_id="mvp-test")
    manager.build("gen-mvp-01", bundle, source_paths=paths)
    access = AccessContext(principal_id="op", credential_id="op", permissions=["query", "operate"],
                           policy_epoch=journal.snapshot().policy_epoch, access_scope_digest="op")
    manager.promote("gen-mvp-01", access=access)
    pinned = manager.pin(access.model_copy(update={"permissions": ["query"]}))
    assert pinned.generation_id == "gen-mvp-01"
    assert {s.instrument_id for s in pinned.loaded.artifacts.bundle.sources} == {"doc:101", "doc:102"}


def test_cli_refuses_to_append_bulk_approval_to_existing_ledger(tmp_path):
    staging, raw = _stage(tmp_path)
    authority = tmp_path / "authority"
    ReviewStore(authority / "reviews.sqlite")
    with pytest.raises(SystemExit):
        main(["--staging", str(staging), "--raw-files", str(raw), "--qwen-artifact", str(tmp_path / "none"),
              "--authority-dir", str(authority), "--output-dir", str(tmp_path / "out"), "--journal-id", "j",
              "--reviewer", "r", "--reason", "m"])



def test_failure_codes_never_echo_free_text():
    from app.corpus_build import _failure
    assert _failure(ValueError("reference_binding_unresolved")) == "ValueError:reference_binding_unresolved"
    assert _failure(ValueError("Cláusula confidencial: o cliente paga honorários")) == "ValueError:detail_withheld"
    assert _failure(KeyError("Nome do Cliente Secreto")) == "KeyError:detail_withheld"
    assert _failure(ValueError("Canonical units require successful parsing and actual parties")).endswith("parties")


# ----- reviewer decisions (workbench) ----------------------------------------
from app.corpus_build import Decisions, LegacyConverter, RelationDecision  # noqa: E402

BASE_PARTIES = [ContractParty(name="Gama Comércio Ltda", role=PartyRole.CONTRATANTE, clean_identifier="22333444000155"),
                ContractParty(name="Andrade Advogados", role=PartyRole.CONTRATADA, is_law_firm=True)]


def _units(bundle, instrument):
    return [u for u in bundle.units if u.instrument_id == instrument]


def test_party_correction_unreserves_document_and_is_what_gets_approved(tmp_path):
    staging, raw = _stage(tmp_path)
    staged, _ = load_staged_sources(staging, raw)
    decisions = Decisions(parties={"doc:103:v1:file:f103": BASE_PARTIES}, titles={"doc:103:v1:file:f103": "Contrato Gama"})
    result = build_reviewed_bundle(staged, ledger=ReviewStore(tmp_path / "a" / "reviews.sqlite"),
                                   budget=SyntheticBudget(), approval=APPROVAL, decisions=decisions)
    assert result.reserved == []
    units = _units(result.bundle, "doc:103")
    assert units and all(u.contract_title == "Contrato Gama" for u in units)
    assert [p.name for p in units[0].parties] == ["Gama Comércio Ltda", "Andrade Advogados"]


def test_excluded_document_is_never_recorded(tmp_path):
    staging, raw = _stage(tmp_path)
    staged, _ = load_staged_sources(staging, raw)
    ledger = ReviewStore(tmp_path / "a" / "reviews.sqlite")
    result = build_reviewed_bundle(staged, ledger=ledger, budget=SyntheticBudget(), approval=APPROVAL,
                                   decisions=Decisions(excluded={"doc:102:v1:file:f102": "minuta"}))
    assert [e["instrument_id"] for e in result.excluded] == ["doc:102"]
    assert "doc:102" not in {s.instrument_id for s in result.bundle.sources}
    assert ledger.history("doc:102:v1:file:f102") == []


def test_reviewer_link_expands_closure_to_the_referenced_clause(tmp_path):
    staging, raw = _stage(tmp_path)
    staged, _ = load_staged_sources(staging, raw)
    first = build_reviewed_bundle(staged, ledger=ReviewStore(tmp_path / "a" / "reviews.sqlite"),
                                  budget=SyntheticBudget(), approval=APPROVAL)
    unit = next(u for u in first.bundle.units if "Cláusula 9ª" in u.verbatim_text)
    target = next(u for u in _units(first.bundle, "doc:101") if u.unit_id != unit.unit_id)
    reference = next(r for r in unit.references if "9" in r.literal)
    coordinate = (unit.source_ids[0], reference.block_id, reference.start, reference.end)
    second = build_reviewed_bundle(staged, ledger=ReviewStore(tmp_path / "b" / "reviews.sqlite"), budget=SyntheticBudget(),
                                   approval=APPROVAL, decisions=Decisions(references={coordinate: ("bind", (target.block_ids[0],))}))
    linked = next(u for u in second.bundle.units if "Cláusula 9ª" in u.verbatim_text)
    [bound] = [r for r in linked.references if "9" in r.literal]
    assert bound.binding_origin == "review_mapping" and bound.target_unit_ids != [linked.unit_id]
    assert linked.closure_unit_ids == bound.target_unit_ids and second.reviewer_links == 1 and second.self_bound == 0


def test_reviewed_relation_links_amendment_to_base_and_closure_includes_both(tmp_path):
    from app.ingestion.closure import resolve_closure
    staging, raw = _stage(tmp_path)
    staged, _ = load_staged_sources(staging, raw)
    probe = build_reviewed_bundle(staged, ledger=ReviewStore(tmp_path / "p" / "reviews.sqlite"),
                                  budget=SyntheticBudget(), approval=APPROVAL)
    base, amendment = _units(probe.bundle, "doc:101")[0], _units(probe.bundle, "doc:102")[0]
    relation = RelationDecision("doc:102", "doc:101", "amends", (base.block_ids[0],), (amendment.block_ids[0],))
    ledger = ReviewStore(tmp_path / "r" / "reviews.sqlite")
    result = build_reviewed_bundle(staged, ledger=ledger, budget=SyntheticBudget(), approval=APPROVAL,
                                   decisions=Decisions(relations=[relation]))
    bundle = result.bundle
    assert result.relations == 1 and len(bundle.relations) == 1
    assert {r.family_id for r in bundle.resolutions} == {"fam:101"}  # one family for base + amendment
    selected = next(u for u in _units(bundle, "doc:101") if u.block_ids[0] == base.block_ids[0])
    closure = resolve_closure([selected.unit_id], units={u.unit_id: u for u in bundle.units},
                              sources={s.source_id: s for s in bundle.sources}, ledger=ledger,
                              relations=bundle.relations, resolutions=bundle.resolutions,
                              relation_review_sources=bundle.relation_review_sources,
                              family_review_sources=bundle.family_review_sources,
                              count_tokens=len, max_tokens=100000)
    assert closure.status == "complete", closure.reason_code
    assert {u.instrument_id for u in closure.units} == {"doc:101", "doc:102"}


def test_relation_to_unpublished_instrument_is_skipped_and_reported(tmp_path):
    staging, raw = _stage(tmp_path)
    staged, _ = load_staged_sources(staging, raw)
    result = build_reviewed_bundle(staged, ledger=ReviewStore(tmp_path / "a" / "reviews.sqlite"), budget=SyntheticBudget(),
                                   approval=APPROVAL, decisions=Decisions(relations=[
                                       RelationDecision("doc:102", "doc:999", "amends", ("x",), ("y",))]))
    assert result.relations == 0 and result.skipped_relations[0]["reason"] == "instrument_not_published"


def test_legacy_document_is_cited_from_retained_conversion_with_bound_hash(tmp_path, monkeypatch):
    staging, raw = _stage(tmp_path)
    folder = raw / "201"
    folder.mkdir()
    original = folder / "201_v1.doc"
    original.write_bytes(b"{\\rtf1 legado}")
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    name = hashlib.sha256(b"201").hexdigest() + ".json"
    (staging / name).write_text(json.dumps({
        "source_id": name[:-5], "mfiles_id": 201, "mfiles_version": 1, "file_id": "f201", "file_hash": digest,
        "snapshot": SNAPSHOT, "configuration_version": CONFIGURATION, "technical_status": "unavailable",
        "eligibility": "failed", "parse_route": "libreoffice", "risks": ["libreoffice_unavailable"],
        "parsed_document": None}), encoding="utf-8")
    rows = {201: {"mfiles_id": 201, "files": [{"file_id": "f201", "path": "files/201/201_v1.doc"}]}}

    def fake_convert(input_path, output_dir, timeout_sec, libreoffice_cmd):
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        target = Path(output_dir) / (Path(input_path).stem + ".docx")
        _write_docx(target, ["CONTRATO DE PRESTAÇÃO DE SERVIÇOS",
                             "Pelo presente instrumento, Alpha Serviços Ltda, inscrita no CNPJ 11.222.333/0001-44, "
                             "doravante CONTRATANTE, e Andrade Advogados, doravante CONTRATADA.",
                             "Cláusula 1ª - O prazo é de doze meses."])
        return target
    monkeypatch.setattr("app.ingestion.legacy_parser.convert_legacy_to_docx", fake_convert)
    legacy = LegacyConverter(rows, tmp_path / "conversions", "soffice")
    staged, skipped = load_staged_sources(staging, raw, accept_metadata_mismatch=True, legacy=legacy)
    item = next(s for s in staged if s.identity.instrument_id == "doc:201")
    assert item.identity.parser_name == "legacy_parser" and item.conversion_path.is_file()
    assert item.identity.conversion_sha256 == hashlib.sha256(item.conversion_path.read_bytes()).hexdigest()
    again, _ = load_staged_sources(staging, raw, accept_metadata_mismatch=True, legacy=legacy)
    assert next(s for s in again if s.identity.instrument_id == "doc:201").conversion_path == item.conversion_path
    if not item.document.metadata.parties:
        pytest.skip("synthetic preamble did not yield parties")
    result = build_reviewed_bundle(staged, ledger=ReviewStore(tmp_path / "a" / "reviews.sqlite"),
                                   budget=SyntheticBudget(), approval=APPROVAL)
    assert "doc:201:v1:file:f201" in result.conversion_paths and "doc:201:v1:file:f201" in result.source_documents
    unit = next(u for u in result.bundle.units if u.instrument_id == "doc:201" and "doze meses" in u.verbatim_text)
    assert unit.source_identities[0].conversion_sha256 == item.identity.conversion_sha256

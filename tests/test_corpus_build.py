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
    bundle, paths, rejected, self_bound = build_reviewed_bundle(staged, ledger=ledger, budget=SyntheticBudget(),
                                                                approval=strict)
    assert {r["instrument_id"]: r["reason"] for r in rejected} == {
        "doc:101": "ValueError:reference_binding_unresolved",
        "doc:103": "ValueError:Canonical units require successful parsing and actual parties"}
    assert self_bound == 0 and [s.instrument_id for s in bundle.sources] == ["doc:102"]
    assert ledger.history("doc:101:v1:file:f101") == [] and ledger.history("doc:103:v1:file:f103") == []
    records = ledger.history("doc:102:v1:file:f102")
    assert {r.scope for r in records} == {"source", "parties", "unit", "family"}
    assert all(r.reviewer == "revisor-teste" and r.decision == "approved" for r in records)
    assert set(paths) == {"doc:102:v1:file:f102"}


def test_self_bound_reference_keeps_literal_without_expanding_closure(tmp_path):
    staging, raw = _stage(tmp_path)
    staged, _ = load_staged_sources(staging, raw)
    ledger = ReviewStore(tmp_path / "authority" / "reviews.sqlite")
    bundle, _, rejected, self_bound = build_reviewed_bundle(staged, ledger=ledger, budget=SyntheticBudget(),
                                                            approval=APPROVAL)
    assert self_bound == 1 and [r["instrument_id"] for r in rejected] == ["doc:103"]
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
    bundle, paths, _, _ = build_reviewed_bundle(staged, ledger=ledger, budget=SyntheticBudget(), approval=APPROVAL)
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

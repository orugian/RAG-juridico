"""Preparatory gate P2A: every file recorded; no human approval synthesized."""
import hashlib
from pathlib import Path

import pytest


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def record(tmp_path, files):
    entries = []
    for i, content in enumerate(files):
        path = tmp_path / f"file{i}.docx"
        path.write_bytes(content)
        entries.append({"file_id": i + 1, "path": path.name, "sha256": digest(path), "parse_route": "docx", "detected_format": "docx", "parseable": True})
    return {"mfiles_id": 1, "mfiles_version": 1, "decision": "include", "doc_key": "1@1:synthetic", "files": entries}


def test_review_decision_never_follows_changed_file_hash(tmp_path):
    from app.ingestion.review_store import ReviewStore
    store = ReviewStore(tmp_path / "review.sqlite3")
    decision = store.record(source_id="s1", snapshot="snapshot", file_hash="a" * 64, configuration_version="v1", decision="approved", reviewer="synthetic-reviewer", reason="Verificação sintética")
    assert store.latest(source_id="s1", snapshot="snapshot", file_hash="a" * 64, configuration_version="v1").record_id == decision.record_id
    assert store.latest(source_id="s1", snapshot="snapshot", file_hash="b" * 64, configuration_version="v1") is None
    assert store.latest(source_id="s1", snapshot="new", file_hash="a" * 64, configuration_version="v1") is None
    assert store.latest(source_id="s1", snapshot="snapshot", file_hash="a" * 64, configuration_version="v2") is None


def test_review_store_is_append_only_and_exports_formula_safe_csv(tmp_path):
    from app.ingestion.review_store import ReviewStore
    store = ReviewStore(tmp_path / "review.sqlite3")
    values = dict(source_id="s1", snapshot="snapshot", file_hash="a" * 64, configuration_version="v1", reviewer="synthetic", reason="=formula")
    first = store.record(**values, decision="approved")
    second = store.record(**values, decision="quarantined")
    assert len(store.history("s1")) == 2
    assert second.supersedes_record_id == first.record_id
    exported = tmp_path / "review.csv"
    store.export_csv(exported)
    assert "'=formula" in exported.read_text(encoding="utf-8")
    imported = ReviewStore(tmp_path / "imported.sqlite3")
    imported.import_csv(exported)
    assert len(imported.history("s1")) == 2
    imported.import_csv(exported)
    assert len(imported.history("s1")) == 2


def test_content_flags_never_grant_auto_approval():
    from app.ingestion.content_validation import validate_content
    from app.ingestion.schemas import ParsedDocument
    doc = ParsedDocument(doc_id=1, doc_version=1, file_path="synthetic", file_hash="a" * 64, parser_name="docx", parser_version="1", metadata={"formal_title": "Synthetic", "instrument_type": "Contrato"}, blocks=[{"block_id": "b1", "doc_id": 1, "doc_version": 1, "block_type": "clause", "hierarchy_level": "clause", "order_index": 0, "text_raw": "Cláusula 1: [NOME DO CLIENTE]", "text_search": "Cláusula 1: [NOME DO CLIENTE]"}])
    report = validate_content(doc)
    assert "placeholder" in report.risks
    assert report.eligibility == "quarantined"
    assert doc.eligibility == "pending_review"


def test_batch_must_visit_every_file_and_reject_unverified_entry(tmp_path, monkeypatch):
    from app.ingestion import batch
    row = record(tmp_path, [b"file1", b"file2"])
    seen = []
    monkeypatch.setattr(batch, "parse_verified_file", lambda entry, **kwargs: seen.append(entry["file_id"]) or {"technical_status": "success", "eligibility": "pending_review", "risks": []})
    result = batch.stage_records([row], raw_dir=tmp_path, snapshot="snapshot", configuration_version="v1")
    assert seen == [1, 2]
    assert len(result["files"]) == 2
    assert result["published"] is False
    row["files"][1]["sha256"] = "a" * 64
    with pytest.raises(ValueError, match="hash"):
        batch.stage_records([row], raw_dir=tmp_path, snapshot="snapshot", configuration_version="v1")


def test_batch_rejects_paths_outside_raw_and_review_decisions(tmp_path):
    from app.ingestion import batch
    row = record(tmp_path, [b"file"])
    with pytest.raises(ValueError, match="include"):
        batch.stage_records([row | {"decision": "review"}], raw_dir=tmp_path, snapshot="s", configuration_version="v")
    row["files"][0]["path"] = "../outside.docx"
    with pytest.raises(ValueError, match="diretório"):
        batch.stage_records([row], raw_dir=tmp_path, snapshot="s", configuration_version="v")


def test_pilot_is_deterministic_and_stratified_by_file_route():
    from app.ingestion.batch import select_pilot
    rows = [{"mfiles_id": i, "files": [{"file_id": i, "parse_route": route}]} for i, route in enumerate(["docx"] * 10 + ["pdf"] * 2 + ["libreoffice"] * 3)]
    pilot = select_pilot(rows, sample_size=6)
    assert len(pilot) == 6
    assert {f["parse_route"] for row in pilot for f in row["files"]} == {"docx", "pdf", "libreoffice"}
    assert pilot == select_pilot(list(reversed(rows)), sample_size=6)


def test_alphanumeric_identifier_extraction_keeps_principal_separate_from_representative():
    from app.ingestion.metadata_extractor import _extract_identifiers
    text = "EMPRESA SINTÉTICA, CNPJ AB.CDE.123/XY45-67, neste ato representada por Pessoa Sintética, CPF 123.456.789-00"
    assert _extract_identifiers(text) == ("ABCDE123XY4567", "AB.CDE.123/XY45-67")
    assert _extract_identifiers("EMPRESA SINTÉTICA CNPJ abcde123xy4567")[0] == "ABCDE123XY4567"
    assert _extract_identifiers("EMPRESA SEM IDENTIFICADOR, neste ato representada por Pessoa Sintética CPF 123.456.789-00") == (None, None)


def test_alphanumeric_cnpj_reconciliation_and_near_duplicate_fingerprint():
    from app.ingestion.parsing_pipeline import _extract_mfiles_cnpj, _is_client_match
    from app.ingestion.near_dup import party_ids
    from app.ingestion.schemas import ContractParty
    assert _extract_mfiles_cnpj({"CNPJ": "AB.CDE.123/XY45-67"}) == "ABCDE123XY4567"
    party = ContractParty(name="Empresa Sintética", clean_identifier="ABCDE123XY4567")
    assert _is_client_match("Nome de cadastro distinto", party, {"CNPJ": "AB.CDE.123/XY45-67"})
    assert not _is_client_match("Empresa Sintética", party, {"CNPJ": "AB.CDE.123/XY45-68"})
    assert party_ids("AB.CDE.123/XY45-67 123.456.789-00") == {"ABCDE123XY4567", "12345678900"}


def test_review_import_rejects_fork_and_reordered_history_atomically(tmp_path):
    import csv
    from app.ingestion.review_store import ReviewStore
    source = ReviewStore(tmp_path / "source.sqlite3")
    values = dict(source_id="s", snapshot="snap", file_hash="a" * 64, configuration_version="v", reviewer="synthetic", reason="synthetic")
    source.record(**values, decision="approved")
    source.record(**values, decision="quarantined")
    path = tmp_path / "reviews.csv"
    source.export_csv(path)
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        fields, rows = reader.fieldnames, list(reader)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(list(reversed(rows)))
    target = ReviewStore(tmp_path / "target.sqlite3")
    with pytest.raises(ValueError, match="histórico"):
        target.import_csv(path)
    assert target.history("s") == []


def test_review_records_reject_blank_reviewer_and_naive_time():
    from app.ingestion.review_store import ReviewRecord
    from datetime import datetime, timezone
    values = dict(record_id="r", source_id="s", snapshot="snap", file_hash="a" * 64, configuration_version="v", decision="approved", reviewer="human", reason="reason", recorded_at=datetime.now(timezone.utc))
    with pytest.raises(ValueError):
        ReviewRecord(**(values | {"reviewer": "   "}))
    with pytest.raises(ValueError):
        ReviewRecord(**(values | {"recorded_at": datetime(2026, 1, 1)}))


def test_batch_invalid_worker_output_has_failure_record(tmp_path, monkeypatch):
    from app.ingestion import batch
    row = record(tmp_path, [b"synthetic"])
    monkeypatch.setattr(batch, "parse_verified_file", lambda *args, **kw: ["malformed"])
    summary = batch.stage_records([row], raw_dir=tmp_path, snapshot="s", configuration_version="v")
    assert summary["files"][0]["risks"] == ["invalid_worker_result"]
    assert summary["files"][0]["eligibility"] == "failed"


def test_actual_offline_worker_preserves_unicode_and_namespaces_blocks(tmp_path, monkeypatch):
    from zipfile import ZipFile
    from app.ingestion import batch
    path = tmp_path / "unicode.docx"
    with ZipFile(path, "w") as document:
        document.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        document.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>CONTRATO SINTÉTICO</w:t></w:r></w:p><w:p><w:r><w:t>CLÁUSULA PRIMEIRA: obrigação sintética</w:t></w:r></w:p><w:p><w:r><w:t>CLÁUSULA SEGUNDA: vencimento em 10/05/2024.</w:t></w:r></w:p></w:body></w:document>')
    entry = {"file_id": 7, "path": path.name, "sha256": digest(path), "parse_route": "docx", "detected_format": "docx"}
    row = {"mfiles_id": 1, "mfiles_version": 1, "decision": "include", "files": [entry]}
    monkeypatch.setenv("PYTHONIOENCODING", "ascii")
    result = batch.parse_verified_file(entry, path=path, row=row, raw_dir=tmp_path, timeout_seconds=30)
    assert result["eligibility"] != "approved"
    parsed = result["parsed_document"]
    assert "obrigação" in str(parsed)
    assert all(block["block_id"].startswith("file-7:") for block in parsed["blocks"])
    assert parsed["blocks"][1]["source_locator"]["body_child_index"] == 1
    assert parsed["blocks"][1]["source_locator"]["xml_part"] == "word/document.xml"
    assert parsed["blocks"][1]["source_locator"]["verification"] == "unreviewed"
    mention, = parsed["metadata"]["temporal_mentions"]
    assert mention["block_id"].startswith("file-7:")
    by_id = {block["block_id"]: block for block in parsed["blocks"]}
    assert by_id[mention["block_id"]]["text_raw"][mention["start"]:mention["end"]] == mention["raw_text"]
    assert mention["normalized_date"] == "2024-05-10" and mention["kind"] == "due"


def test_cross_format_duplicate_candidates_never_approve_or_merge():
    from app.ingestion.batch_diagnostics import diagnose_batch
    text = "Contrato sintético estabelece obrigação de pagamento mensal pactuada pelas partes."
    def item(source, route, title, kind="Contrato"):
        return {"source_id": source, "parse_route": route, "eligibility": "pending_review", "technical_status": "success", "risks": [], "parsed_document": {"metadata": {"formal_title": title, "instrument_type": kind, "parties": [{"clean_identifier": "12345678900", "is_law_firm": False}]}, "blocks": [{"block_id": source+"b1", "text_raw": text}]}}
    files = [item("a", "docx", "Título 1"), item("b", "pdf", "Título distinto"), item("c", "pdf", "Aditivo", "Aditivo"), {"source_id": "d", "technical_status": "failed", "risks": [], "eligibility": "failed"}]
    files[2]["parsed_document"]["blocks"][0]["text_raw"] = "Aditivo: " + text
    report = diagnose_batch(files)
    assert report["near_duplicates"]["compared_pairs"] == 3
    assert report["near_duplicates"]["unchecked_source_ids"] == ["d"]
    candidate = next(pair for pair in report["near_duplicates"]["candidates"] if pair["source_ids"] == ["a", "b"])
    assert candidate["state"] == "proposed" and candidate["routes"] == ["docx", "pdf"]
    assert report["relations"][0]["modifier_source_id"] == "c"
    assert report["relations"][0]["state"] == "proposed"
    assert report["relations"][0]["candidate_base_source_ids"] == ["a", "b"]
    assert report["relations"][0]["support_block_ids"] == ["cb1"]
    assert files[0]["eligibility"] == "pending_review"


def test_duplicate_comparison_marks_punctuation_only_text_unchecked():
    from app.ingestion.batch_diagnostics import diagnose_batch
    files = [{"source_id": source, "parse_route": "docx", "risks": [], "parsed_document": {"metadata": {}, "blocks": [{"block_id": source, "text_raw": "!!!"}]}} for source in ("a", "b")]
    result = diagnose_batch(files)
    assert result["near_duplicates"]["unchecked_source_ids"] == ["a", "b"]
    assert result["near_duplicates"]["compared_pairs"] == 0


def test_review_database_rejects_update_delete_and_prior_scope_after_new_record(tmp_path):
    import sqlite3
    from app.ingestion.review_store import ReviewStore
    path = tmp_path / "reviews.sqlite3"
    store = ReviewStore(path)
    values = dict(source_id="s", snapshot="snap", file_hash="a" * 64, configuration_version="v", reviewer="synthetic", reason="synthetic")
    store.record(**values, decision="approved")
    store.record(**(values | {"file_hash": "b" * 64}), decision="quarantined")
    assert store.latest(source_id="s", snapshot="snap", file_hash="a" * 64, configuration_version="v") is None
    with sqlite3.connect(path) as database:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            database.execute("DELETE FROM reviews")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            database.execute("UPDATE reviews SET payload='{}'")
    assert len(store.history("s")) == 2


@pytest.mark.parametrize("reason", ["'=literal", "''=literal", "'ordinary", "=formula", "@formula", "ordinary"])
def test_review_csv_preserves_apostrophes_and_formula_markers(tmp_path, reason):
    from app.ingestion.review_store import ReviewStore
    source = ReviewStore(tmp_path / "source.sqlite3")
    original = source.record(source_id="s", snapshot="snap", file_hash="a" * 64, configuration_version="v", decision="quarantined", reviewer="synthetic", reason=reason)
    path = tmp_path / "review.csv"
    source.export_csv(path)
    source.import_csv(path)
    target = ReviewStore(tmp_path / "target.sqlite3")
    target.import_csv(path)
    assert target.history("s") == [original]


@pytest.mark.parametrize("component", ["uv.lock", "file_format.py", "config.py", "curation.jsonl", "manifest.jsonl", "temporal.py"])
def test_preflight_fingerprint_changes_with_dependencies_code_and_inputs(tmp_path, monkeypatch, component):
    from app.ingestion import batch, legacy_parser
    monkeypatch.setattr(legacy_parser, "find_libreoffice_binary", lambda: None)
    monkeypatch.setattr(batch.shutil, "which", lambda name: None)
    changes = {}
    monkeypatch.setattr(batch, "_hash", lambda path: changes.get(Path(path).name, "a" * 64))
    rows = [{"files": [{"parse_route": "docx"}]}]
    first = batch.preflight(rows, raw_dir=tmp_path, curation_dir=tmp_path)
    changes[component] = "b" * 64
    second = batch.preflight(rows, raw_dir=tmp_path, curation_dir=tmp_path)
    assert first["configuration_version"] != second["configuration_version"]


@pytest.mark.parametrize("environment", ["python", "platform", "packages"])
def test_preflight_fingerprint_changes_with_runtime(tmp_path, monkeypatch, environment):
    from app.ingestion import batch, legacy_parser
    monkeypatch.setattr(legacy_parser, "find_libreoffice_binary", lambda: None)
    monkeypatch.setattr(batch.shutil, "which", lambda name: None)
    monkeypatch.setattr(batch, "_hash", lambda path: "a" * 64)
    monkeypatch.setattr(batch, "_installed_packages", lambda: {"synthetic-parser": "1"}, raising=False)
    rows = [{"files": [{"parse_route": "docx"}]}]
    first = batch.preflight(rows, raw_dir=tmp_path, curation_dir=tmp_path)
    if environment == "python":
        monkeypatch.setattr(batch.platform, "python_version", lambda: "99.0.0")
    elif environment == "platform":
        monkeypatch.setattr(batch.platform, "system", lambda: "SyntheticOS")
    else:
        monkeypatch.setattr(batch, "_installed_packages", lambda: {"synthetic-parser": "2"}, raising=False)
    second = batch.preflight(rows, raw_dir=tmp_path, curation_dir=tmp_path)
    assert first["configuration_version"] != second["configuration_version"]

"""
Testes de endurecimento da curadoria (regressões apontadas na revisão adversarial — Rodada 1):
taxonomia, coerência mãe×aditivo, snapshot do modelo, quase-duplicatas por conteúdo+partes,
multi-arquivo, PDF criptografado, overrides robustos, saídas atômicas, contrato das Etapas 2-4,
CLI e resiliência do cliente Jev.
"""

import hashlib
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from app.ingestion import curation as cur
from app.ingestion.curation import CurationStaleError, combine, curate, load_curated, load_overrides, write_outputs
from app.ingestion.curation_rules import Category, Decision, classify
from app.ingestion.file_format import detect_format
from app.ingestion.jev_classifier import JevClassifier, build_state
from tests.test_curation import (  # noqa: F401  (corpus é fixture)
    CFG,
    JEV_RESPONSE,
    STRONG_CONTRACT,
    WEAK_CONTRACT,
    StubClassifier,
    _answers,
    _by_id,
    _doc,
    _docx,
    _jev,
    _op,
    corpus,
)


# --------------------------------------------------------------------------- taxonomia
@pytest.mark.parametrize("title, expected", [
    # Coerência mãe × aditivo: CCT e seus aditivos ficam fora do escopo, juntos.
    ("Convenção Coletiva do Trabalho - 2012 a 2014", Category.DOCUMENTO_ADMINISTRATIVO),
    ("Termo aditivo da Convenção Coletiva do Trabalho - 2012 a 2014", Category.DOCUMENTO_ADMINISTRATIVO),
    # Ambíguos sempre em revisão.
    ("Homologação de Acordo", Category.INDETERMINADO),
    ("Nota promissória", Category.INDETERMINADO),
    ("Proposta de compra do imóvel", Category.INDETERMINADO),
    ("Proposta AJUR - Rede Pró", Category.PROPOSTA_HONORARIOS),
    # Lacunas de taxonomia.
    ("Carta de Intenção - Aquisição Biosul", Category.ACORDO),
    ("Termo de Ajustamento de Conduta - MPT", Category.ACORDO),
    ("Rescisão antecipada de locação", Category.DISTRATO),
    ("Termo de Compromisso de Estágio", Category.INSTRUMENTO_CONTRATUAL),
    ("Termo de Entrega de Equipamentos", Category.INSTRUMENTO_CONTRATUAL),
])
def test_taxonomy_gaps(title, expected):
    assert classify(title, "").category is expected


def test_ambiguous_rule_routes_to_review():
    assert combine(classify("Homologação de Acordo", ""), _op(Category.ACORDO, 0.6), CFG)[0] is Decision.REVIEW


def test_untrusted_snapshot_never_decides_alone():
    assert combine(None, _op(Category.DOCUMENTO_ADMINISTRATIVO, 0.99), CFG, model_trusted=False)[0] is Decision.REVIEW
    assert combine(WEAK_CONTRACT, _op(Category.INSTRUMENTO_CONTRATUAL, 0.99), CFG, model_trusted=False)[0] is Decision.REVIEW
    decision, _, _, reasons = combine(STRONG_CONTRACT, _op(Category.PECA_PROCESSUAL, 0.95), CFG, model_trusted=False)
    assert decision is Decision.REVIEW and "CONFLICT_MODEL:peca_processual" in reasons


def test_untrusted_snapshot_is_flagged_in_curation(tmp_path):
    raw = tmp_path / "raw"
    records = [_doc(raw, 1, "Tela AA", class_name="Documento")]
    stub = StubClassifier({"Tela AA": _op(Category.DOCUMENTO_ADMINISTRATIVO, 0.99, snapshot="typesafe/jev-1.14-x")})
    row = curate(records, raw, stub)[0]
    assert row["decision"] == "review" and "MODEL_SNAPSHOT_UNTRUSTED:typesafe/jev-1.14-x" in row["reasons"]


# --------------------------------------------------------------------------- quase-duplicatas e famílias
def _docx_with(raw: Path, mfiles_id: int, title: str, body: str, **kw):
    record = _doc(raw, mfiles_id, title, **kw)
    path = raw / record["files"][0]["path"]
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", f"<w:document><w:p>{body}</w:p></w:document>")
    data = path.read_bytes()
    record["files"][0].update(sha256=hashlib.sha256(data).hexdigest(), size=len(data), downloaded_bytes=len(data))
    return record


_CLAUSES = " ".join(f"Cláusula {i}ª O locatário pagará o aluguel mensal até o quinto dia útil do mês." for i in range(1, 40))


def test_content_near_dup_same_parties_is_family_but_other_parties_are_distinct(tmp_path):
    raw = tmp_path / "raw"
    rda = {"Cliente": "RDA"}
    records = [
        _docx_with(raw, 1, "Contrato de Locação", f"Locador CPF 111.222.333-44 {_CLAUSES} versão A", props=rda),
        _docx_with(raw, 2, "Contrato de Locação", f"Locador CPF 111.222.333-44 {_CLAUSES} versão B", props=rda,
                   modified="2025-06-01T00:00:00Z"),
        _docx_with(raw, 3, "Contrato de Locação", f"Locador CPF 999.888.777-66 {_CLAUSES} versão A", props=rda),
    ]
    rows = _by_id(curate(records, raw, None))
    assert rows[1]["family"]["kind"] == "content_near_dup" and rows[1]["family"]["id"] == rows[2]["family"]["id"]
    assert (rows[1]["family"]["suggested"], rows[2]["family"]["suggested"]) == ("exclude", "include")
    assert rows[3]["decision"] == "include" and rows[3]["family"] is None  # mesmo texto-base, partes diferentes
    assert all(rows[i]["flags"]["near_dup_checked"] for i in (1, 2, 3))


def test_same_instrument_empty_parties_requires_higher_threshold():
    from app.ingestion.near_dup import Fingerprint, same_instrument
    common = frozenset(f"word_{i}" for i in range(90))
    a_shingles = common | frozenset(f"a_{i}" for i in range(10))
    b_shingles = common  # jaccard = 90 / 100 = 0.90

    fp_a = Fingerprint(shingles=a_shingles, parties=frozenset())
    fp_b = Fingerprint(shingles=b_shingles, parties=frozenset())

    # Com threshold 0.85, mas sem partes identificadas, 0.90 não atinge 0.95 -> False
    assert not same_instrument(fp_a, fp_b, threshold=0.85)

    # Com partes idênticas identificadas, 0.90 >= 0.85 -> True
    fp_a_parties = Fingerprint(shingles=a_shingles, parties=frozenset({"11122233344"}))
    fp_b_parties = Fingerprint(shingles=b_shingles, parties=frozenset({"11122233344"}))
    assert same_instrument(fp_a_parties, fp_b_parties, threshold=0.85)

    # Com partes ausentes mas similaridade quase idêntica (>= 0.95) -> True
    high_common = frozenset(f"w_{i}" for i in range(98))
    fp_high_a = Fingerprint(shingles=high_common | frozenset({"extra_a"}), parties=frozenset())
    fp_high_b = Fingerprint(shingles=high_common | frozenset({"extra_b"}), parties=frozenset())
    assert same_instrument(fp_high_a, fp_high_b, threshold=0.85)


def test_title_marker_family_without_final_has_no_suggestion(tmp_path):
    raw = tmp_path / "raw"
    folder = {"Caminho original (1/3)": "C:\\AACloud\\Consultivo\\Contratos\\Fornecimento\\a.doc|"}
    records = [
        _doc(raw, 1, "Contrato de Aquisição - Versão revisada pela Canguru", class_name="Documento", props=folder),
        _doc(raw, 2, "Contrato de Aquisição - Versão enviada pelo Paulo", class_name="Documento", props=folder),
    ]
    rows = _by_id(curate(records, raw, None))
    assert rows[1]["decision"] == "review"
    assert rows[1]["family"]["suggested"] == "" == rows[2]["family"]["suggested"]


def test_family_with_two_finals_suggests_single_canonical(tmp_path):
    raw = tmp_path / "raw"
    folder = {"Caminho original (1/3)": "C:\\AACloud\\Consultivo\\Contratos\\MOU\\a.doc|"}
    records = [
        _doc(raw, 1, "MOU Biosul - Versão final", class_name="Documento", props=folder),
        _doc(raw, 2, "MOU Biosul - Versão final.rtf", class_name="Documento", props=folder,
             content=b"{\\rtf1 MOU}", ext="doc"),
        _doc(raw, 3, "MOU - Biosul", class_name="Documento", props=folder),
    ]
    rows = _by_id(curate(records, raw, None))
    assert [rows[i]["family"]["suggested"] for i in (1, 2, 3)] == ["include", "exclude", "exclude"]


# --------------------------------------------------------------------------- sinais de risco
def test_multi_file_and_encrypted_pdf_go_to_review(tmp_path):
    raw = tmp_path / "raw"
    multi = _doc(raw, 1, "Contrato de Mútuo")
    extra = dict(multi["files"][0], id=99, path="files/1/99_v1.docx")
    _docx(raw / extra["path"])
    multi["files"].append(extra)
    enc = _doc(raw, 2, "Contrato de Comodato", content=b"%PDF-1.4\n/Encrypt 1 0 R\n", ext="pdf")
    rows = _by_id(curate([multi, enc], raw, None))
    assert rows[1]["decision"] == "review" and "MULTI_FILE" in rows[1]["reasons"] and len(rows[1]["files"]) == 2
    assert rows[2]["decision"] == "review" and "ENCRYPTED_PDF" in rows[2]["reasons"]


def test_doc_key_incorporates_secondary_files_and_rules_version(tmp_path):
    raw = tmp_path / "raw"
    multi = _doc(raw, 1, "Contrato de Mútuo")
    extra = dict(multi["files"][0], id=99, path="files/1/99_v1.docx")
    _docx(raw / extra["path"])
    multi["files"].append(extra)
    row = curate([multi], raw, None)[0]

    f1_hash = row["files"][0]["sha256"][:16]
    f2_hash = row["files"][1]["sha256"][:16]
    assert row["doc_key"] == f"1@1:{f1_hash}+{f2_hash}:r{cur.RULES_VERSION}"


def test_possible_template_without_client_goes_to_review(tmp_path):
    raw = tmp_path / "raw"
    records = [_doc(raw, 1, "Contrato de comodato de veículo", class_name="Documento"),
               _doc(raw, 2, "Contrato de Mútuo", props={"Cliente": "Starmkt"})]
    opinion = _op(Category.INSTRUMENTO_CONTRATUAL, 0.99, p_template=0.72)
    rows = _by_id(curate(records, raw, StubClassifier({"Contrato de comodato de veículo": opinion,
                                                       "Contrato de Mútuo": opinion})))
    assert rows[1]["decision"] == "review" and "POSSIBLE_TEMPLATE" in rows[1]["reasons"]
    assert rows[2]["decision"] == "include"  # cliente identificado no M-Files


def test_model_is_called_once_per_distinct_state(tmp_path):
    raw = tmp_path / "raw"
    records = [_doc(raw, i, "Contrato de Honorários - AJUR", content=str(i).encode()) for i in (1, 2, 3)]
    stub = StubClassifier({})
    curate(records, raw, stub, workers=3)
    assert len(stub.states) == 1


def test_detect_format_missing_file_is_corrupt_not_crash(tmp_path):
    info = detect_format(tmp_path / "sumiu.docx", "docx")
    assert info.corrupt and not info.parseable


# --------------------------------------------------------------------------- overrides robustos
def _overrides_file(tmp_path, header: str, row: str) -> Path:
    path = tmp_path / "overrides.csv"
    path.write_text(f"{header}\n{row}\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("header, row, message", [
    ("mfiles_id;titulo;decisao_final;revisado_por", "80;x;include;rdib", "colunas obrigatórias"),
    ("mfiles_id;versao;decisao_final;revisado_por", "abc;1;include;rdib", "mfiles_id"),
    ("mfiles_id;versao;decisao_final;revisado_por", "80;;include;rdib", "versao"),
])
def test_load_overrides_contextual_errors(tmp_path, header, row, message):
    with pytest.raises(ValueError, match=message):
        load_overrides(_overrides_file(tmp_path, header, row))


def test_load_overrides_normalizes_headers_and_comma_delimiter(tmp_path):
    path = _overrides_file(tmp_path, "MFILES_ID , versao,decisao_final ,revisado_por", "80,2,EXCLUDE,rdib")
    assert load_overrides(path)[80] == {"decision": "exclude", "category": None, "reviewed_by": "rdib",
                                        "note": "", "mfiles_version": 2}


def test_load_overrides_rejects_duplicate_ids_and_casefolds_category(tmp_path):
    header = "mfiles_id;versao;decisao_final;categoria_final;revisado_por"
    path = _overrides_file(tmp_path, header, "80;1;include;Instrumento_Contratual;rdib")
    assert load_overrides(path)[80]["category"] == "instrumento_contratual"
    path.write_text(f"{header}\n80;1;include;;rdib\n80;1;exclude;;rdib\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mais de uma vez"):
        load_overrides(path)


def test_primary_file_is_first_parseable_by_file_id(tmp_path):
    raw = tmp_path / "raw"
    record = _doc(raw, 1, "Contrato de Mútuo")
    image = dict(record["files"][0], id=1, path="files/1/1_v1.png", extension="png", sha256="0" * 64)
    (raw / image["path"]).write_bytes(b"\x89PNG\r\n\x1a\n")
    record["files"] = [record["files"][0], image]  # ordem "da API" invertida
    row = curate([record], raw, None)[0]
    assert [f["file_id"] for f in row["files"]] == [1, 2]
    assert row["file"]["file_id"] == 2 and row["file"]["parse_route"] == "docx"


def test_unknown_override_ids_are_reported(corpus, tmp_path):
    raw, records = corpus
    overrides = {12345: {"decision": "include", "category": None, "reviewed_by": "rdib", "note": "", "mfiles_version": 1}}
    summary = write_outputs(curate(records, raw, None, overrides), tmp_path / "c", raw, CFG, None, overrides)
    assert summary["overrides_unknown_ids"] == [12345]


# --------------------------------------------------------------------------- saídas robustas
def test_review_queue_preserves_human_input_between_runs(corpus, tmp_path):
    raw, records = corpus
    out = tmp_path / "c"
    write_outputs(curate(records, raw, None), out, raw, CFG, None)
    text = (out / cur.REVIEW_FILE).read_text(encoding="utf-8-sig")
    line = next(l for l in text.splitlines() if ";80;" in l)
    assert line.endswith(";;;;")
    (out / cur.REVIEW_FILE).write_text(text.replace(line, line[:-4] + ";exclude;;rdib;tela"), encoding="utf-8-sig")

    write_outputs(curate(records, raw, None), out, raw, CFG, None)
    again = (out / cur.REVIEW_FILE).read_text(encoding="utf-8-sig")
    assert next(l for l in again.splitlines() if ";80;" in l).endswith(";exclude;;rdib;tela")


def test_qa_sample_preserves_human_annotations_across_reruns(corpus, tmp_path):
    raw, records = corpus
    out = tmp_path / "c"
    rows = curate(records, raw, None)
    write_outputs(rows, out, raw, CFG, None)
    text = (out / cur.QA_FILE).read_text(encoding="utf-8-sig")
    lines = [l for l in text.splitlines() if l and not l.startswith("prioridade")]
    assert len(lines) > 0, "qa_sample.csv deve conter linhas amostradas"
    target_line = lines[0]
    target_id = target_line.split(";")[2]
    assert target_line.endswith(";;;;")

    annotated = target_line[:-4] + ";include;instrumento_contratual;auditor_qa;validado_amostra"
    (out / cur.QA_FILE).write_text(text.replace(target_line, annotated), encoding="utf-8-sig")

    write_outputs(curate(records, raw, None), out, raw, CFG, None)
    again = (out / cur.QA_FILE).read_text(encoding="utf-8-sig")
    again_line = next(l for l in again.splitlines() if f";{target_id};" in l)
    assert again_line.endswith(";include;instrumento_contratual;auditor_qa;validado_amostra")


def test_locked_output_raises_clear_error(corpus, tmp_path, monkeypatch):
    raw, records = corpus
    original = Path.replace

    def locked(self, target):
        if Path(target).name == cur.REVIEW_FILE:
            raise PermissionError("locked")
        return original(self, target)

    monkeypatch.setattr(Path, "replace", locked)
    with pytest.raises(PermissionError, match="aberto"):
        write_outputs(curate(records, raw, None), tmp_path / "c", raw, CFG, None)


def test_review_priority_and_qa_sample(corpus):
    raw, records = corpus
    rows = curate(records, raw, StubClassifier(_answers()))
    by = _by_id(rows)
    assert cur.review_priority(by[60]) == 3 and cur.review_priority(by[80]) == 6
    sample = cur.qa_sample(rows, CFG)
    assert sample == cur.qa_sample(rows, CFG) and sample and all(r["decision"] != "review" for r in sample)


def test_summary_reports_agreement_by_decision_and_category(corpus, tmp_path):
    raw, records = corpus
    summary = write_outputs(curate(records, raw, StubClassifier(_answers())), tmp_path / "c", raw, CFG, None)
    agreement = summary["rule_model_agreement"]
    assert set(agreement) == {"decision", "category", "n"} and agreement["n"] >= 1
    assert summary["model_snapshot_drift"] is False


# --------------------------------------------------------------------------- contrato Etapas 2-4
def test_load_curated_detects_modified_file_and_interrupted_write(corpus, tmp_path):
    raw, records = corpus
    out = tmp_path / "c"
    write_outputs(curate(records, raw, None), out, raw, CFG, None)
    (raw / records[0]["files"][0]["path"]).write_bytes(b"PK\x03\x04alterado")
    assert load_curated(out, raw, verify_files=False)  # opt-out explícito
    with pytest.raises(CurationStaleError, match="alterado em disco"):
        load_curated(out, raw)  # verificação de integridade é o padrão
    with (out / cur.CURATION_FILE).open("a", encoding="utf-8") as fh:
        fh.write("{}\n")
    with pytest.raises(CurationStaleError, match="summary"):
        load_curated(out, raw)


def test_load_curated_detects_secondary_file_modification(tmp_path):
    raw = tmp_path / "raw"
    multi = _doc(raw, 1, "Contrato de Mútuo")
    extra = dict(multi["files"][0], id=99, path="files/1/99_v1.docx")
    _docx(raw / extra["path"])
    extra_data = (raw / extra["path"]).read_bytes()
    extra.update(sha256=hashlib.sha256(extra_data).hexdigest(), size=len(extra_data))
    multi["files"].append(extra)
    from app.ingestion.sync import write_manifest
    write_manifest(raw, [multi])
    overrides = {1: {"mfiles_id": 1, "mfiles_version": 1, "decision": "include",
                     "category": "instrumento_contratual", "reviewed_by": "auditor", "notes": ""}}
    out = tmp_path / "c"
    write_outputs(curate([multi], raw, None, overrides), out, raw, CFG, None, overrides)
    assert load_curated(out, raw)

    (raw / extra["path"]).write_bytes(b"PK\x03\x04alterado_secundario")
    with pytest.raises(CurationStaleError, match="alterado em disco"):
        load_curated(out, raw)


def test_index_plan_upsert_delete_unchanged(corpus, tmp_path):
    raw, records = corpus
    out = tmp_path / "c"
    rows = _by_id(curate(records, raw, None))
    write_outputs(list(rows.values()), out, raw, CFG, None)
    indexed = {10: rows[10]["doc_key"], 40: "40@0:velho", 20: "20@1:x"}  # 20 é exclude: deve sair do índice
    plan = cur.index_plan(indexed, out, raw)
    assert [r["mfiles_id"] for r in plan["upsert"]] == [40, 100]  # 40: doc_key mudou; 100: novo
    assert plan["delete"] == [20] and plan["unchanged"] == [10]


def test_cli_main_end_to_end(corpus, tmp_path, monkeypatch, capsys):
    raw, _ = corpus
    settings = cur.get_settings().model_copy(update={"raw_data_dir": raw, "curation_dir": tmp_path / "c"})
    monkeypatch.setattr(cur, "get_settings", lambda: settings)
    monkeypatch.setattr("sys.argv", ["curation", "--no-model"])
    cur.main()
    assert json.loads(capsys.readouterr().out)["totals"]
    assert load_curated(tmp_path / "c", raw)


# --------------------------------------------------------------------------- Jev robusto
def test_jev_corrupted_cache_lines_are_discarded(tmp_path):
    (tmp_path / "cache.jsonl").write_text('{"key": "a", "response": {"answers": {}}}\n{"key": "b", "resp',
                                          encoding="utf-8")
    jev = _jev(tmp_path, lambda request: httpx.Response(200, json=JEV_RESPONSE))
    assert jev._cache == {}
    assert jev.classify(build_state("x", "Contrato", "", "")) is not None


def test_jev_cache_write_failure_is_not_fatal(tmp_path, monkeypatch):
    jev = _jev(tmp_path, lambda request: httpx.Response(200, json=JEV_RESPONSE))

    def broken_open(*args, **kwargs):
        raise OSError("disco cheio")

    monkeypatch.setattr(Path, "open", broken_open)
    assert jev.classify(build_state("x", "Contrato", "", "")) is not None and jev.calls == 1


def test_jev_retries_timeout_and_honors_retry_after(tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr("app.ingestion.jev_classifier.time.sleep", sleeps.append)
    responses = iter([httpx.ReadTimeout("t"), httpx.Response(429, headers={"Retry-After": "7"}),
                      httpx.Response(200, json=JEV_RESPONSE)])

    def handler(request):
        item = next(responses)
        if isinstance(item, Exception):
            raise item
        return item

    jev = JevClassifier("sk", tmp_path / "c.jsonl", transport=httpx.MockTransport(handler), backoff_seconds=1)
    assert jev.classify(build_state("x", "Contrato", "", "")) is not None
    assert len(sleeps) == 2 and 7 <= sleeps[1] <= 7 * 1.25


def test_jev_error_log_never_contains_state(tmp_path, caplog):
    jev = _jev(tmp_path, lambda request: httpx.Response(500))
    jev.max_retries = 0
    with caplog.at_level("ERROR"):
        assert jev.classify(build_state("Contrato ACME Ltda - Fulano de Tal", "Contrato", "", "")) is None
    assert "Fulano" not in caplog.text and "sk-test" not in caplog.text

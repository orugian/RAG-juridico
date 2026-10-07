"""
Testes da curadoria do corpus (Etapa 1): regras de domínio, detecção de formato, combinação
regra × Jev, duplicatas, famílias de versões, overrides, saídas e contrato com a Etapa 2.
Nenhuma chamada real à rede: o Jev é simulado (stub ou httpx.MockTransport).
"""

import json
import zipfile
from pathlib import Path

import httpx
import pytest

from app.ingestion import curation as cur
from app.ingestion.curation import (
    CurationConfig,
    CurationStaleError,
    combine,
    curate,
    load_curated,
    load_overrides,
    write_outputs,
)
from app.ingestion.curation_rules import (
    RULES_VERSION,
    Category,
    Decision,
    RuleMatch,
    classify,
    family_stem,
    has_version_marker,
    looks_final,
    normalize,
    original_folder,
)
from app.ingestion.file_format import DetectedFormat, ParseRoute, detect_format
from app.ingestion.jev_classifier import JevClassifier, JevOpinion, build_state
from app.ingestion.sync import MANIFEST_NAME, write_manifest

CFG = CurationConfig()


# --------------------------------------------------------------------------- regras de domínio
def test_normalize_strips_accents_extension_and_separators():
    assert normalize("Contrato_de Locação – Imóvel.DOCX") == "contrato de locacao imovel"


@pytest.mark.parametrize("title, folder, expected, rule_id", [
    # Núcleo do corpus: objeto do contrato cita termos processuais, mas é contrato.
    ("Contrato de Honorários - Ação Judicial Avulsa - Inventário", "", Category.INSTRUMENTO_CONTRATUAL, "T13_INSTRUMENTO"),
    ("Contrato de Honorários - Impugnação Auto de Infração MTE", "", Category.INSTRUMENTO_CONTRATUAL, "T13_INSTRUMENTO"),
    # Modelo/minuta/parecer vencem o tipo contratual.
    ("Modelo - Contrato de Hospedagem - Pagamento diário", "", Category.MODELO, "T01_MODELO"),
    ("Minuta de Acordo.", "", Category.MINUTA, "T02_MINUTA"),
    ("Análise do Contrato de Fornecimento", "", Category.PARECER_ANALISE, "T03_PARECER"),
    # Peças e documentos não contratuais, inclusive dentro de classes contratuais.
    ("Petição intermediária", "", Category.PECA_PROCESSUAL, "T04_PECA"),
    ("Execução Honorários - Federzoni vs UF", "", Category.PECA_PROCESSUAL, "T04_PECA"),
    ("Mandado de Segurança", "", Category.PECA_PROCESSUAL, "T04_PECA"),  # não confundir com "nda"
    ("Matricula Imóvel", "", Category.DOCUMENTO_PESSOAL, "T05_PESSOAL"),
    ("Ficha Médica", "", Category.DOCUMENTO_ADMINISTRATIVO, "T06_ADMIN"),
    ("Subst. Anion", "", Category.DOCUMENTO_ADMINISTRATIVO, "T06_ADMIN"),
    # Tipos contratuais.
    ("10ª Alteração Contrato Social AA", "", Category.ATO_SOCIETARIO, "T07_SOCIETARIO"),
    ("Termo_consórcio (2)", "", Category.ATO_SOCIETARIO, "T07_SOCIETARIO"),
    ("2 - Ata de Assembleia - Anion", "", Category.ATO_SOCIETARIO, "T07_SOCIETARIO"),
    ("CONTRATO PRESTAÇÃO SERVIÇOS - PADRÃO - STARMKT", "", Category.CONTRATO_PADRAO, "T08_PADRAO"),
    ("Aditivo contrato de locação - ZAGA e Anion", "", Category.ADITIVO, "T09_ADITIVO"),
    ("Termo de Rescisão do Contrato de Trabalho", "", Category.DISTRATO, "T10_DISTRATO"),
    ("Encerramento Prestação de Serviços", "", Category.DISTRATO, "T10_DISTRATO"),
    ("Proposta AJUR - Rede Pró", "", Category.PROPOSTA_HONORARIOS, "T11_PROPOSTA"),
    ("Termo de Acordo Extrajudicial", "", Category.ACORDO, "T12_ACORDO"),
    ("Contrato de mútuo.doc", "", Category.INSTRUMENTO_CONTRATUAL, "T13_INSTRUMENTO"),
    # Pasta processual vence título contratual; Docs Representação não é processual.
    ("Acordo cumprido - Renato Norteiro vs Anion", "Contencioso Judicial\\Manifestações intermediárias",
     Category.PECA_PROCESSUAL, "F01_PECA"),
    ("EMPENHO - Contrato Social", "Contencioso Judicial\\Docs Representação e Atos Constitutivos\\ANION",
     Category.ATO_SOCIETARIO, "T07_SOCIETARIO"),
    ("Acordo extrajudicial - Trabalhista", "Contencioso Judicial\\Acordos", Category.ACORDO, "T12_ACORDO"),
])
def test_classify_title_and_folder(title, folder, expected, rule_id):
    match = classify(title, folder)
    assert match is not None and (match.category, match.rule_id) == (expected, rule_id)


def test_folder_override_is_recorded_for_audit():
    match = classify("Acordo cumprido - X vs Y", "Contencioso Judicial\\Manifestações intermediárias")
    assert match.overridden == "T12_ACORDO" and match.strong


def test_folder_only_contract_rule_is_weak():
    match = classify("Estruturação Societária", "Consultivo\\Contratos\\Memorando de Entendimentos")
    assert match.rule_id == "F04_CONTRATO" and not match.strong


def test_unknown_title_without_folder_has_no_rule():
    assert classify("Tela AA", "") is None


def test_original_folder_from_split_properties():
    props = {
        "Caminho original (1/3)": "C:\\Users\\Rdib17\\Desktop\\AACloud\\Consultivo\\Contratos\\Contrato de Co",
        "Caminho original (2/3)": "modato\\Contrato de comodato.doc|",
    }
    assert original_folder(props) == "Consultivo\\Contratos\\Contrato de Comodato"
    assert original_folder({}) == ""


def test_version_family_helpers():
    a = "Contrato de Comodato de Equipamentos - ArtBrasil - Versão com correções pela ArtBrasil"
    b = "Contrato de Comodato de Equipamentos - ArtBrasil - Versão finalizada"
    assert family_stem(a) == family_stem(b) == "contrato comodato equipamentos artbrasil"
    assert family_stem("Termo_consórcio (2)") == family_stem("TERMO DE CONSÓRCIO rev final") == family_stem("Termo_consórcio")
    assert has_version_marker("Distrato Social - Hersa (2)") and not has_version_marker("Contrato de Honorários - AJUR")
    assert looks_final(b) and not looks_final(a)


# --------------------------------------------------------------------------- formato real
def _docx(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(zipfile.ZipInfo("[Content_Types].xml"), "<Types/>")
        zf.writestr(zipfile.ZipInfo("word/document.xml"), "<w:document/>")
    return path


@pytest.mark.parametrize("content, ext, fmt, route, mismatch", [
    (b"{\\rtf1\\ansi Contrato", "doc", DetectedFormat.RTF, ParseRoute.LIBREOFFICE, True),
    (b"\xffWPC\xff#\x00\x00rest", "doc", DetectedFormat.WORDPERFECT, ParseRoute.LIBREOFFICE, True),
    (bytes.fromhex("D0CF11E0A1B11AE1") + "WordDocument".encode("utf-16-le"), "doc", DetectedFormat.OLE_DOC,
     ParseRoute.LIBREOFFICE, False),
    (b"%PDF-1.7\n...", "pdf", DetectedFormat.PDF, ParseRoute.PDF, False),
    (b"\x89PNG\r\n\x1a\n", "png", DetectedFormat.IMAGE, ParseRoute.UNSUPPORTED, False),
    (b"", "docx", DetectedFormat.EMPTY, ParseRoute.UNSUPPORTED, True),
])
def test_detect_format_by_magic_bytes(tmp_path, content, ext, fmt, route, mismatch):
    path = tmp_path / f"f.{ext}"
    path.write_bytes(content)
    info = detect_format(path, ext)
    assert (info.detected, info.route, info.extension_mismatch) == (fmt, route, mismatch)


def test_detect_docx_and_corrupt_zip_and_encrypted_pdf(tmp_path):
    assert detect_format(_docx(tmp_path / "a.docx"), "docx").route is ParseRoute.DOCX
    (tmp_path / "b.docx").write_bytes(b"PK\x03\x04garbage")
    assert detect_format(tmp_path / "b.docx", "docx").corrupt
    (tmp_path / "c.pdf").write_bytes(b"%PDF-1.4\n/Encrypt 5 0 R\n%%EOF")
    info = detect_format(tmp_path / "c.pdf", "pdf")
    assert info.encrypted and info.parseable


# --------------------------------------------------------------------------- combinação regra × modelo
def _op(category: Category, confidence: float, p_template=0.1, p_draft=0.1,
        snapshot: str = CFG.calibrated_snapshot) -> JevOpinion:
    return JevOpinion(category, confidence, {category.value: confidence}, p_template, p_draft, snapshot)


STRONG_CONTRACT = RuleMatch(Category.INSTRUMENTO_CONTRATUAL, "T13_INSTRUMENTO", "title", "")
STRONG_PECA = RuleMatch(Category.PECA_PROCESSUAL, "T04_PECA", "title", "")
POLICY_MINUTA = RuleMatch(Category.MINUTA, "T02_MINUTA", "title", "")
WEAK_CONTRACT = RuleMatch(Category.INSTRUMENTO_CONTRATUAL, "F04_CONTRATO", "folder", "", strong=False)


@pytest.mark.parametrize("rule, opinion, expected_decision, expected_source, reason", [
    (STRONG_CONTRACT, _op(Category.ADITIVO, 0.99), Decision.INCLUDE, "rule", "T13_INSTRUMENTO"),
    (STRONG_CONTRACT, _op(Category.PECA_PROCESSUAL, 0.85), Decision.REVIEW, "rule", "CONFLICT_MODEL:peca_processual"),
    (STRONG_CONTRACT, _op(Category.PECA_PROCESSUAL, 0.70), Decision.INCLUDE, "rule", "T13_INSTRUMENTO"),
    (STRONG_PECA, _op(Category.ACORDO, 0.60), Decision.EXCLUDE, "rule", "T04_PECA"),
    (POLICY_MINUTA, _op(Category.INSTRUMENTO_CONTRATUAL, 0.99), Decision.EXCLUDE, "rule", "T02_MINUTA"),
    (WEAK_CONTRACT, _op(Category.INSTRUMENTO_CONTRATUAL, 0.65), Decision.INCLUDE, "rule+model", "MODEL_CONFIRMED"),
    (WEAK_CONTRACT, _op(Category.ACORDO, 0.58), Decision.REVIEW, "rule", "WEAK_RULE_UNCONFIRMED"),
    (WEAK_CONTRACT, _op(Category.PARECER_ANALISE, 0.9), Decision.REVIEW, "rule", "WEAK_RULE_UNCONFIRMED"),
    (WEAK_CONTRACT, None, Decision.REVIEW, "rule", "WEAK_RULE_UNCONFIRMED"),
    (None, _op(Category.DOCUMENTO_ADMINISTRATIVO, 0.99), Decision.EXCLUDE, "model", "MODEL_CONFIDENT"),
    (None, _op(Category.INSTRUMENTO_CONTRATUAL, 0.93), Decision.REVIEW, "model", "NO_RULE_LOW_CONFIDENCE"),
    (None, _op(Category.INDETERMINADO, 0.99), Decision.REVIEW, "model", "NO_RULE_LOW_CONFIDENCE"),
    (None, None, Decision.REVIEW, "none", "NO_RULE_MODEL_UNAVAILABLE"),
])
def test_combine(rule, opinion, expected_decision, expected_source, reason):
    decision, _, source, reasons = combine(rule, opinion, CFG)
    assert (decision, source) == (expected_decision, expected_source) and reason in reasons


# --------------------------------------------------------------------------- curadoria ponta a ponta
class StubClassifier:
    """Responde por título; registra os estados recebidos (para verificar minimização de dados)."""

    model = "typesafe/jev-test"
    calls = errors = 0
    cost_usd = 0.0

    def __init__(self, answers: dict[str, JevOpinion]):
        self.answers = answers
        self.states: list[dict] = []

    def classify(self, state):
        self.states.append(state)
        return self.answers.get(state["titulo"])


def _doc(raw: Path, mfiles_id: int, title: str, class_name: str = "Contrato", content: bytes = b"",
         ext: str = "docx", props: dict | None = None, version: int = 1, modified: str = "2025-01-01T00:00:00Z"):
    rel = f"files/{mfiles_id}/{mfiles_id + 1}_v{version}.{ext}"
    path = raw / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if ext == "docx":
        with zipfile.ZipFile(path, "w") as zf:
            # Exact-duplicate fixtures need identical bytes across ZIP clock ticks.
            zf.writestr(zipfile.ZipInfo("word/document.xml"), f"<w:document>{title}{content.decode()}</w:document>")
    else:
        path.write_bytes(content)
    import hashlib
    data = path.read_bytes()
    return {
        "mfiles_id": mfiles_id, "mfiles_version": version, "class_id": 13, "class_name": class_name,
        "title": title, "last_modified_utc": modified, "properties": props or {}, "status": "ok", "error": None,
        "files": [{"id": mfiles_id + 1, "version": 1, "name": title, "extension": ext, "size": len(data),
                   "path": rel, "sha256": hashlib.sha256(data).hexdigest(), "downloaded_bytes": len(data)}],
    }


@pytest.fixture
def corpus(tmp_path):
    raw = tmp_path / "raw"
    folder = {"Caminho original (1/3)": "C:\\Users\\X\\AACloud\\Consultivo\\Contratos\\Contrato de Comodato\\a.doc|"}
    records = [
        _doc(raw, 10, "Contrato de Honorários - AJUR", props={"Cliente": "ACME Ltda"}),
        _doc(raw, 20, "Petição intermediária", class_name="Acordo"),
        _doc(raw, 30, "Contrato de Honorários - AJUR", class_name="Documento", content=b"igual"),
        _doc(raw, 40, "Contrato de Honorários - AJUR", content=b"igual"),  # duplicata exata de 30
        _doc(raw, 50, "Capa - Relatório", class_name="Documento", content=b"\x89PNG\r\n\x1a\n", ext="png"),
        _doc(raw, 60, "Contrato de Comodato - ArtBrasil - Versão com correções", class_name="Documento", props=folder),
        _doc(raw, 70, "Contrato de Comodato - ArtBrasil - Versão finalizada", class_name="Documento", props=folder),
        _doc(raw, 80, "Tela AA", class_name="Documento"),
        _doc(raw, 90, "Contrato de Locação de Veículos", props={"Palavras-chave": "Minuta; locação"}),
        _doc(raw, 100, "Termo de Adesão", class_name="Contrato"),
    ]
    write_manifest(raw, records)
    return raw, records


def _answers():
    return {
        "Petição intermediária": _op(Category.ACORDO, 0.6),
        "Tela AA": _op(Category.DOCUMENTO_ADMINISTRATIVO, 0.4),
        "Termo de Adesão": _op(Category.INSTRUMENTO_CONTRATUAL, 0.95, p_template=0.9),
    }


def _by_id(rows):
    return {r["mfiles_id"]: r for r in rows}


def test_curate_end_to_end_decisions(corpus):
    raw, records = corpus
    stub = StubClassifier(_answers())
    rows = _by_id(curate(records, raw, stub, workers=2))

    assert rows[10]["decision"] == "include" and rows[10]["category"] == "instrumento_contratual"
    assert rows[20]["decision"] == "exclude" and rows[20]["category"] == "peca_processual"
    # Duplicata exata: canônico é o da classe contratual (40), não o de "Documento" (30).
    assert rows[30]["decision"] == "exclude" and rows[30]["duplicate_of"] == 40
    assert rows[40]["decision"] == "include"
    assert rows[50]["decision"] == "exclude" and rows[50]["reasons"] == ["UNSUPPORTED_FORMAT:image"]
    # Família de versões -> revisão, sugerindo a versão final.
    assert rows[60]["decision"] == rows[70]["decision"] == "review"
    assert rows[60]["family"]["id"] == rows[70]["family"]["id"]
    assert (rows[60]["family"]["suggested"], rows[70]["family"]["suggested"]) == ("exclude", "include")
    # Sem regra e Jev pouco confiante -> revisão.
    assert rows[80]["decision"] == "review" and "NO_RULE_LOW_CONFIDENCE" in rows[80]["reasons"]
    # Include com sinal de minuta nas palavras-chave / modelo pelo Jev -> revisão.
    assert rows[90]["decision"] == "review" and "K02_KW_MINUTA" in rows[90]["reasons"]
    assert rows[100]["decision"] == "review" and "POSSIBLE_TEMPLATE" in rows[100]["reasons"]
    # Rota de parsing pronta para a Etapa 2.
    assert rows[10]["file"]["parse_route"] == "docx"


def test_curate_sends_only_minimized_metadata_to_model(corpus):
    raw, records = corpus
    stub = StubClassifier({})
    curate(records, raw, stub, workers=1)
    assert stub.states and all(set(s) == {"titulo", "classe_mfiles", "pasta_origem", "palavras_chave"} for s in stub.states)
    assert all("ACME" not in json.dumps(s) for s in stub.states)
    # Duplicatas e formatos não textuais não são enviados ao modelo.
    assert {s["titulo"] for s in stub.states}.isdisjoint({"Capa - Relatório"})


def test_curate_without_model_is_conservative(corpus):
    raw, records = corpus
    rows = _by_id(curate(records, raw, classifier=None))
    assert rows[80]["decision"] == "review" and "NO_RULE_MODEL_UNAVAILABLE" in rows[80]["reasons"]
    assert rows[10]["decision"] == "include"


def test_curate_is_deterministic(corpus):
    raw, records = corpus
    first = curate(records, raw, StubClassifier(_answers()), workers=4)
    second = curate(list(reversed(records)), raw, StubClassifier(_answers()), workers=1)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_overrides_apply_and_respect_structural_and_staleness(corpus):
    raw, records = corpus
    overrides = {
        80: {"decision": "include", "category": "instrumento_contratual", "reviewed_by": "rdib", "note": "", "mfiles_version": 1},
        60: {"decision": "include", "category": None, "reviewed_by": "rdib", "note": "", "mfiles_version": 7},
        30: {"decision": "include", "category": None, "reviewed_by": "rdib", "note": "", "mfiles_version": None},
    }
    rows = _by_id(curate(records, raw, StubClassifier(_answers()), overrides))
    assert rows[80]["decision"] == "include" and rows[80]["decision_source"] == "override"
    assert rows[60]["decision"] == "review" and "OVERRIDE_STALE_NEW_VERSION" in rows[60]["reasons"]
    assert rows[30]["decision"] == "exclude" and "OVERRIDE_IGNORED_STRUCTURAL" in rows[30]["reasons"]


# --------------------------------------------------------------------------- overrides CSV
def test_load_overrides_reads_excel_semicolon_csv(tmp_path):
    path = tmp_path / "overrides.csv"
    path.write_text(
        "\ufeffmfiles_id;versao;titulo;decisao_final;categoria_final;revisado_por;observacao\n"
        "80;1;Tela AA;exclude;documento_administrativo;rdib;print de tela\n"
        "81;1;Pendente;;;;\n",
        encoding="utf-8",
    )
    overrides = load_overrides(path)
    assert list(overrides) == [80]
    assert overrides[80]["decision"] == "exclude" and overrides[80]["mfiles_version"] == 1


@pytest.mark.parametrize("row, message", [
    ("80;1;x;talvez;;rdib;", "decisao_final"),
    ("80;1;x;include;inexistente;rdib;", "categoria_final"),
    ("80;1;x;include;;;", "revisado_por"),
])
def test_load_overrides_rejects_invalid_rows(tmp_path, row, message):
    path = tmp_path / "overrides.csv"
    path.write_text(f"mfiles_id;versao;titulo;decisao_final;categoria_final;revisado_por;observacao\n{row}\n", encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_overrides(path)


# --------------------------------------------------------------------------- saídas e Etapa 2
def test_outputs_and_load_curated_contract(corpus, tmp_path):
    raw, records = corpus
    out = tmp_path / "curation"
    rows = curate(records, raw, StubClassifier(_answers()))
    summary = write_outputs(rows, out, raw, CFG, None)

    assert summary["rules_version"] == RULES_VERSION and summary["duplicates_removed"] == 1
    review_csv = (out / cur.REVIEW_FILE).read_text(encoding="utf-8-sig")
    assert review_csv.splitlines()[0].startswith("prioridade;motivo_revisao;mfiles_id")
    # O próprio review_queue.csv (sem decisões preenchidas) é um overrides válido e vazio.
    assert load_overrides(out / cur.REVIEW_FILE) == {}

    included = load_curated(out, raw)
    assert {r["mfiles_id"] for r in included} == {10, 40}
    assert all(r["file"]["parse_route"] in {"docx", "libreoffice", "pdf"} for r in included)


def test_review_csv_neutralizes_formula_injection_and_roundtrips(tmp_path):
    row = {"mfiles_id": 1, "mfiles_version": 1, "title": "=HYPERLINK(\"http://x\")", "class_name": "Documento",
           "folder": "", "cliente": "@cliente", "palavras_chave": "", "decision": "review", "category": None,
           "reasons": ["NO_RULE_MODEL_UNAVAILABLE"], "rule": None, "model": None, "family": None}
    path = tmp_path / "review.csv"
    cur._write_csv(path, [cur._review_row(row, 5)])
    text = path.read_text(encoding="utf-8-sig")
    assert "'=HYPERLINK" in text and ";'@cliente;" in text
    filled = text.replace(";;;;\n", ";include;;rdib;'-ok\n", 1)
    path.write_text(filled, encoding="utf-8-sig")
    assert load_overrides(path)[1]["note"] == "-ok"


def test_load_curated_refuses_stale_manifest(corpus, tmp_path):
    raw, records = corpus
    out = tmp_path / "curation"
    write_outputs(curate(records, raw, None), out, raw, CFG, None)
    records[0]["mfiles_version"] = 2
    write_manifest(raw, records)
    with pytest.raises(CurationStaleError, match="manifest"):
        load_curated(out, raw)


def test_load_curated_refuses_other_rules_version(corpus, tmp_path, monkeypatch):
    raw, records = corpus
    out = tmp_path / "curation"
    write_outputs(curate(records, raw, None), out, raw, CFG, None)
    monkeypatch.setattr(cur, "RULES_VERSION", "outra")
    with pytest.raises(CurationStaleError, match="regras"):
        load_curated(out, raw)


# --------------------------------------------------------------------------- cliente Jev
JEV_RESPONSE = {
    "model": "typesafe/jev-1.13-20260917",
    "answers": {
        "categoria": {"type": "choice", "choice": "instrumento_contratual", "confidence": 0.97,
                      "probabilities": {"instrumento_contratual": 0.985, "outro": 0.015}},
        "modelo": {"type": "noul", "noul": 0.12},
        "minuta": {"type": "noul", "noul": 0.2},
    },
    "usage": {"input_tokens": 500, "output_tokens": 70, "cost": 0.00002},
}


def _jev(tmp_path, handler) -> JevClassifier:
    return JevClassifier("sk-test", tmp_path / "cache.jsonl", transport=httpx.MockTransport(handler), backoff_seconds=0)


def test_jev_parses_response_and_caches(tmp_path):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=JEV_RESPONSE)

    state = build_state("Contrato de Mútuo", "Contrato", "", "")
    jev = _jev(tmp_path, handler)
    opinion = jev.classify(state)
    assert opinion.category is Category.INSTRUMENTO_CONTRATUAL and opinion.confidence == 0.97
    assert opinion.model_version == "typesafe/jev-1.13-20260917" and not opinion.cached
    assert calls[0]["model"] == "typesafe/jev-1.13" and set(calls[0]["questions"]) == {"categoria", "modelo", "minuta"}

    again = _jev(tmp_path, handler).classify(state)  # nova instância lê o cache em disco
    assert again.cached and len(calls) == 1


def test_jev_retries_then_succeeds(tmp_path):
    responses = iter([httpx.Response(429), httpx.Response(503), httpx.Response(200, json=JEV_RESPONSE)])
    jev = _jev(tmp_path, lambda request: next(responses))
    assert jev.classify(build_state("x", "Contrato", "", "")) is not None


def test_jev_failure_returns_none_and_counts_error(tmp_path):
    jev = _jev(tmp_path, lambda request: httpx.Response(400, json={"error": {"code": 400}}))
    assert jev.classify(build_state("x", "Contrato", "", "")) is None and jev.errors == 1


def test_jev_unknown_choice_maps_to_indeterminado():
    payload = json.loads(json.dumps(JEV_RESPONSE))
    payload["answers"]["categoria"]["choice"] = "outro"
    assert JevOpinion.from_answers(payload).category is Category.INDETERMINADO


def test_manifest_name_constant_is_shared():
    assert MANIFEST_NAME == "manifest.jsonl"

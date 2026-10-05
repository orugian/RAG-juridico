"""
Curadoria do corpus RAG (Etapa 1): decide, para cada documento extraído do M-Files, se ele entra
no índice (include), fica fora (exclude) ou precisa de revisão humana (review) — sempre com motivo.

Fases (CONTEXT.md §3.1.5):
  1. Fatos estruturais (determinísticos): sem arquivo, formato não textual/corrompido, duplicata exata.
  2. Regras de domínio versionadas (curation_rules): título > pasta de origem.
  3. Segunda opinião calibrada (Jev via OpenRouter), com cache e controle de snapshot.
  4. Sinais de risco em includes: multi-arquivo, PDF criptografado, possível modelo/minuta.
  5. Famílias de versões (por marcador no título ou por conteúdo + partes) -> revisão.
  6. Decisões humanas (overrides.csv) prevalecem, exceto sobre fatos estruturais.

Saídas em data/curation/: curation.jsonl, review_queue.csv, qa_sample.csv e summary.json (gravado por
último, como marcador de commit). A Etapa 2 consome APENAS via `load_curated()` / `index_plan()`.

Uso:
    uv run python -m app.ingestion.curation [--no-model] [--workers 8]
"""

import argparse
import csv
import hashlib
import io
import json
import random
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from app.config import get_settings
from app.ingestion.curation_rules import (
    RULES_VERSION,
    Category,
    Decision,
    INCLUDED_CATEGORIES,
    RuleMatch,
    classify,
    decision_for,
    family_stem,
    has_version_marker,
    is_contract_class,
    keyword_flags,
    looks_final,
    normalize,
    original_folder,
)
from app.ingestion.file_format import ParseRoute, detect_format
from app.ingestion.jev_classifier import JevClassifier, JevOpinion, build_state
from app.ingestion.near_dup import fingerprint, same_instrument
from app.ingestion.sync import MANIFEST_NAME, load_manifest
from app.monitoring import logger

SCHEMA_VERSION = "curation.v1"
CURATION_FILE = "curation.jsonl"
REVIEW_FILE = "review_queue.csv"
OVERRIDES_FILE = "overrides.csv"
QA_FILE = "qa_sample.csv"
SUMMARY_FILE = "summary.json"
CSV_DELIMITER = ";"  # Excel pt-BR abre/salva CSV com ';'
HUMAN_COLUMNS = ("decisao_final", "categoria_final", "revisado_por", "observacao")

# Regras de política explícita do escritório (decisões de escopo): o modelo não as contesta.
POLICY_RULES = frozenset({"T01_MODELO", "T02_MINUTA", "T08_PADRAO"})


class CurationStaleError(RuntimeError):
    """A curadoria não corresponde ao manifest/regras atuais (rodar a curadoria novamente)."""


@dataclass(frozen=True)
class CurationConfig:
    # Sem regra: Jev decide sozinho acima disto. Calibrado empiricamente (2026-10-05, snapshot
    # jev-1.13-20260917): vendo só metadados, o Jev atribuiu 0,93 a erros verificados.
    model_auto_threshold: float = 0.98
    conflict_threshold: float = 0.80        # regra forte × Jev discordante acima disto -> revisão
    weak_rule_agree_threshold: float = 0.60  # regra fraca só decide se Jev concordar acima disto
    flag_threshold: float = 0.80            # p(modelo)/p(minuta) acima disto em um include -> revisão
    template_no_party_threshold: float = 0.70  # p(modelo) acima disto + sem Cliente no M-Files -> revisão
    near_dup_threshold: float = 0.85        # Jaccard (5-shingles) para "mesmo instrumento"
    calibrated_snapshot: str = "typesafe/jev-1.13-20260917"
    qa_sample_per_stratum: int = 15
    qa_seed: int = 20261005


# --------------------------------------------------------------------------------------------
# Combinação regra × modelo (função pura)
# --------------------------------------------------------------------------------------------
def combine(
    rule: Optional[RuleMatch], opinion: Optional[JevOpinion], cfg: CurationConfig, model_trusted: bool = True,
) -> tuple[Decision, Category, str, list[str]]:
    """
    Retorna (decisão, categoria, fonte, motivos). Um modelo não confiável (snapshot diferente do
    calibrado) ainda pode GERAR revisão por conflito, mas nunca decide sozinho nem confirma regra fraca.
    """
    model_decision = decision_for(opinion.category) if opinion else None

    if rule and rule.strong:
        decision = decision_for(rule.category)
        reasons = [rule.rule_id] + ([f"TITLE_OVERRIDDEN_BY_FOLDER:{rule.overridden}"] if rule.overridden else [])
        disagrees = (
            opinion is not None
            and rule.rule_id not in POLICY_RULES
            and model_decision not in (decision, Decision.REVIEW)
            and opinion.confidence >= cfg.conflict_threshold
        )
        if disagrees:
            return Decision.REVIEW, rule.category, "rule", reasons + [f"CONFLICT_MODEL:{opinion.category.value}"]
        return decision, rule.category, "rule", reasons

    if rule:  # regra fraca (pasta não estrutural)
        decision = decision_for(rule.category)
        if model_trusted and opinion and model_decision == decision and opinion.confidence >= cfg.weak_rule_agree_threshold:
            return decision, rule.category, "rule+model", [rule.rule_id, "MODEL_CONFIRMED"]
        return Decision.REVIEW, rule.category, "rule", [rule.rule_id, "WEAK_RULE_UNCONFIRMED"]

    if (model_trusted and opinion and opinion.category is not Category.INDETERMINADO
            and opinion.confidence >= cfg.model_auto_threshold):
        return model_decision, opinion.category, "model", ["MODEL_CONFIDENT"]
    if opinion:
        return Decision.REVIEW, opinion.category, "model", ["NO_RULE_LOW_CONFIDENCE"]
    return Decision.REVIEW, Category.INDETERMINADO, "none", ["NO_RULE_MODEL_UNAVAILABLE"]


# --------------------------------------------------------------------------------------------
# CSV (Excel pt-BR) e overrides humanos
# --------------------------------------------------------------------------------------------
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: Any) -> Any:
    """Neutraliza injeção de fórmula (CSV/Excel injection) em células de texto livre."""
    return f"'{value}" if isinstance(value, str) and value.lstrip(" ").startswith(_FORMULA_PREFIXES) else value


def _csv_unsafe(value: Optional[str]) -> str:
    value = (value or "").strip()
    return value[1:] if value.startswith("'") and value[1:].lstrip(" ").startswith(_FORMULA_PREFIXES) else value


def _read_csv(path: Path) -> list[dict[str, str]]:
    """Lê CSV gravado pelo Excel (';' ou ','), com cabeçalhos normalizados e células multilinha preservadas."""
    text = path.read_text(encoding="utf-8-sig")
    header = text.split("\n", 1)[0]
    if not header.strip():
        return []
    try:
        delimiter = csv.Sniffer().sniff(header, delimiters=";,").delimiter
    except csv.Error:
        delimiter = CSV_DELIMITER
    return [
        {(k or "").strip().casefold(): _csv_unsafe(v) for k, v in raw.items() if k}
        for raw in csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)
    ]


def _int_field(row: dict[str, str], field: str, line: int) -> int:
    value = row.get(field, "")
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"overrides linha {line}: '{field}' deve ser inteiro (valor: '{value}').") from None


def load_overrides(path: Path) -> dict[int, dict[str, Any]]:
    """
    Lê decisões humanas. Aceita o próprio review_queue.csv preenchido (mesmo layout).
    Erros de preenchimento falham com mensagem contextual — nunca são ignorados em silêncio.
    """
    if not path.exists():
        return {}
    rows = _read_csv(path)
    if rows and not {"mfiles_id", "versao", "decisao_final", "revisado_por"} <= set(rows[0]):
        raise ValueError(f"overrides: colunas obrigatórias ausentes em {path.name} "
                         "(mfiles_id, versao, decisao_final, revisado_por).")
    overrides: dict[int, dict[str, Any]] = {}
    for line, row in enumerate(rows, start=2):
        decision = row.get("decisao_final", "").casefold()
        if not decision:
            continue
        mfiles_id = _int_field(row, "mfiles_id", line)
        if decision not in (Decision.INCLUDE.value, Decision.EXCLUDE.value):
            raise ValueError(f"overrides linha {line} (mfiles_id={mfiles_id}): decisao_final inválida "
                             f"'{decision}'; use include ou exclude.")
        if mfiles_id in overrides:
            raise ValueError(f"overrides linha {line}: mfiles_id={mfiles_id} aparece mais de uma vez.")
        category = row.get("categoria_final", "").casefold() or None
        if category and category not in Category._value2member_map_:
            raise ValueError(f"overrides linha {line} (mfiles_id={mfiles_id}): categoria_final inválida '{category}'.")
        if not row.get("revisado_por"):
            raise ValueError(f"overrides linha {line} (mfiles_id={mfiles_id}): revisado_por é obrigatório.")
        overrides[mfiles_id] = {
            "decision": decision,
            "category": category,
            "reviewed_by": row["revisado_por"],
            "note": row.get("observacao", ""),
            "mfiles_version": _int_field(row, "versao", line),
        }
    return overrides


# --------------------------------------------------------------------------------------------
# Fases da curadoria
# --------------------------------------------------------------------------------------------
def _ts(value: Optional[str]) -> float:
    try:
        return datetime.fromisoformat((value or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _file_entry(f: dict[str, Any], raw_dir: Path) -> dict[str, Any]:
    info = detect_format(raw_dir / f["path"], f["extension"])
    return {
        "file_id": f["id"],
        "path": f["path"],
        "sha256": f["sha256"],
        "size": f["size"],
        "declared_extension": f["extension"],
        "detected_format": info.detected.value,
        "parse_route": info.route.value,
        "extension_mismatch": info.extension_mismatch,
        "corrupt": info.corrupt,
        "encrypted": info.encrypted,
        "parseable": info.parseable,
    }


def _base_row(record: dict[str, Any], raw_dir: Path) -> dict[str, Any]:
    props = record.get("properties", {})
    ordered = sorted(record.get("files", []), key=lambda f: f["id"])  # ordem da API não é garantida
    files = [_file_entry(f, raw_dir) for f in ordered] if record.get("status") == "ok" else []
    primary = next((f for f in files if f["parseable"]), files[0] if files else None)
    return {
        "schema_version": SCHEMA_VERSION,
        "rules_version": RULES_VERSION,
        "mfiles_id": record["mfiles_id"],
        "mfiles_version": record["mfiles_version"],
        # Chave estável de upsert no índice: muda se a versão M-Files ou o conteúdo mudarem.
        "doc_key": f"{record['mfiles_id']}@{record['mfiles_version']}:{primary['sha256'][:16]}" if primary else None,
        "title": record["title"],
        "class_id": record["class_id"],
        "class_name": record["class_name"],
        "folder": original_folder(props),
        "cliente": props.get("Cliente", ""),
        "caso": props.get("Caso", ""),
        "palavras_chave": props.get("Palavras-chave", ""),
        "last_modified_utc": record.get("last_modified_utc", ""),
        "sync_status": record.get("status"),
        "file": primary,
        "files": files,
        "duplicate_of": None,
        "family": None,
        "override": None,
        "rule": None,
        "model": None,
        "flags": {},
    }


def _canonical_order(row: dict[str, Any]) -> tuple:
    """Entre duplicatas exatas: classe contratual (curada pelo escritório) > mais recente > menor ID."""
    return (not is_contract_class(row["class_name"]), -_ts(row["last_modified_utc"]), row["mfiles_id"])


def _mark_exact_duplicates(rows: list[dict[str, Any]]) -> None:
    by_sha: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["file"] and len(row["files"]) == 1:
            by_sha[row["file"]["sha256"]].append(row)
    for group in by_sha.values():
        if len(group) > 1:
            canonical = min(group, key=_canonical_order)
            for row in group:
                if row is not canonical:
                    row["duplicate_of"] = canonical["mfiles_id"]


def _structural_decision(row: dict[str, Any]) -> Optional[tuple[str, Optional[Category]]]:
    if not row["file"]:
        return ("SYNC_FAILED" if row["sync_status"] == "failed" else "NO_FILE"), Category.NAO_TEXTUAL
    if row["duplicate_of"]:
        return f"DUPLICATE_OF:{row['duplicate_of']}", None
    if not any(f["parseable"] for f in row["files"]):
        primary = row["file"]
        reason = "CORRUPT_FILE" if primary["corrupt"] else f"UNSUPPORTED_FORMAT:{primary['detected_format']}"
        return reason, Category.NAO_TEXTUAL
    return None


def _classify_with_model(rows: list[dict[str, Any]], classifier: Optional[JevClassifier], workers: int) -> dict[int, Optional[JevOpinion]]:
    """Uma chamada por estado DISTINTO (títulos repetidos não geram custo duplicado)."""
    if not classifier or not rows:
        return {}
    states = {row["mfiles_id"]: build_state(row["title"], row["class_name"], row["folder"], row["palavras_chave"])
              for row in rows}
    unique = {json.dumps(s, sort_keys=True, ensure_ascii=False): s for s in states.values()}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        answers = dict(zip(unique, pool.map(classifier.classify, unique.values())))
    return {mid: answers[json.dumps(s, sort_keys=True, ensure_ascii=False)] for mid, s in states.items()}


def _apply_rules_and_model(row: dict[str, Any], opinion: Optional[JevOpinion], cfg: CurationConfig) -> None:
    rule = classify(row["title"], row["folder"])
    trusted = opinion is None or opinion.model_version == cfg.calibrated_snapshot
    decision, category, source, reasons = combine(rule, opinion, cfg, model_trusted=trusted)
    if opinion and not trusted:
        reasons.append(f"MODEL_SNAPSHOT_UNTRUSTED:{opinion.model_version}")

    kw_flags = keyword_flags(row["palavras_chave"])
    primary = row["file"]
    flags = {
        "is_standard_form": category is Category.CONTRATO_PADRAO,
        "possible_template": bool(opinion and (
            opinion.p_template >= cfg.flag_threshold
            or (opinion.p_template >= cfg.template_no_party_threshold and not row["cliente"]))),
        "possible_draft": bool(opinion and opinion.p_draft >= cfg.flag_threshold),
        "keyword_flags": kw_flags,
        "multi_file": len(row["files"]) > 1,
        "extension_mismatch": primary["extension_mismatch"],
        "encrypted_pdf": primary["encrypted"],
        "near_dup_checked": False,
    }
    if decision is Decision.INCLUDE:
        risks = [name for name, on in (
            ("MULTI_FILE", flags["multi_file"]),
            ("ENCRYPTED_PDF", flags["encrypted_pdf"]),
            ("POSSIBLE_TEMPLATE", flags["possible_template"] and not flags["is_standard_form"]),
            ("POSSIBLE_DRAFT", flags["possible_draft"] and not flags["is_standard_form"]),
        ) if on] + ([] if flags["is_standard_form"] else kw_flags)
        if risks:
            decision, reasons = Decision.REVIEW, reasons + risks

    row.update(
        decision=decision, category=category, decision_source=source, reasons=reasons, flags=flags,
        rule=None if rule is None else {"rule_id": rule.rule_id, "category": rule.category.value, "source": rule.source,
                                         "strong": rule.strong, "overridden": rule.overridden,
                                         "description": rule.description},
        model=opinion.as_dict() if opinion else None,
    )


def _best_final(members: list[dict[str, Any]]) -> dict[str, Any]:
    """Uma única sugestão por família: rota DOCX (melhor parsing) > mais recente > maior ID."""
    return max(members, key=lambda m: (m["file"]["parse_route"] == ParseRoute.DOCX.value,
                                       _ts(m["last_modified_utc"]), m["mfiles_id"]))


def _set_family(members: list[dict[str, Any]], kind: str, key: str, suggested: Optional[dict[str, Any]]) -> None:
    family_id = hashlib.sha1(key.encode()).hexdigest()[:10]
    for m in members:
        m["family"] = {
            "id": family_id, "kind": kind, "size": len(members),
            "suggested": "" if suggested is None else (
                Decision.INCLUDE.value if m is suggested else Decision.EXCLUDE.value),
        }
        m["decision"] = Decision.REVIEW
        m["reasons"] = m["reasons"] + [f"VERSION_FAMILY:{family_id}"]


def _scope(row: dict[str, Any]) -> str:
    return row["folder"] or f"{row['class_name']}|{row['cliente']}"


def _apply_version_families(rows: list[dict[str, Any]], raw_dir: Path, cfg: CurationConfig) -> None:
    # (a) Marcador de versão no título: "(2)", "Versão revisada", "rev final"... (inclui minutas: o escritório escolhe)
    contractual = INCLUDED_CATEGORIES | {Category.MINUTA}
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["category"] in contractual:
            groups[(_scope(row), family_stem(row["title"]))].append(row)
    for (scope, stem), members in groups.items():
        if len(members) < 2 or not stem or not any(has_version_marker(m["title"]) for m in members):
            continue
        finals = [m for m in members if looks_final(m["title"])]
        # Sem versão marcada como final, não há sugestão (evita sugerir uma minuta como versão final).
        _set_family(members, "title_marker", f"{scope}|{stem}", _best_final(finals) if finals else None)

    # (b) Títulos idênticos sem marcador: compara CONTEÚDO + PARTES (CPF/CNPJ), localmente.
    twins: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["decision"] == Decision.INCLUDE and not row["family"]:
            twins[(_scope(row), normalize(row["title"]))].append(row)
    for (scope, title), members in twins.items():
        if len(members) < 2:
            continue
        prints = {m["mfiles_id"]: fingerprint(raw_dir / m["file"]["path"])
                  if m["file"]["parse_route"] == ParseRoute.DOCX.value else None for m in members}
        for m in members:
            m["flags"]["near_dup_checked"] = prints[m["mfiles_id"]] is not None
        parent = {m["mfiles_id"]: m["mfiles_id"] for m in members}

        def root(x: int) -> int:
            while parent[x] != x:
                x = parent[x]
            return x

        for i, a in enumerate(members):
            for b in members[i + 1:]:
                fa, fb = prints[a["mfiles_id"]], prints[b["mfiles_id"]]
                if fa and fb and same_instrument(fa, fb, cfg.near_dup_threshold):
                    parent[root(a["mfiles_id"])] = root(b["mfiles_id"])
        clusters: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for m in members:
            clusters[root(m["mfiles_id"])].append(m)
        for cluster in clusters.values():
            if len(cluster) > 1:
                ids = "|".join(str(m["mfiles_id"]) for m in sorted(cluster, key=lambda m: m["mfiles_id"]))
                _set_family(cluster, "content_near_dup", f"{scope}|{title}|{ids}", _best_final(cluster))


def _apply_overrides(rows: list[dict[str, Any]], overrides: dict[int, dict[str, Any]]) -> None:
    for row in rows:
        ov = overrides.get(row["mfiles_id"])
        if not ov:
            continue
        if row["decision_source"] == "structural":
            row["reasons"] = row["reasons"] + ["OVERRIDE_IGNORED_STRUCTURAL"]
            continue
        stale = ov["mfiles_version"] != row["mfiles_version"]
        row["override"] = {**ov, "stale": stale}
        if stale:
            row["decision"], row["reasons"] = Decision.REVIEW, row["reasons"] + ["OVERRIDE_STALE_NEW_VERSION"]
        else:
            row["decision"], row["decision_source"] = Decision(ov["decision"]), "override"
            if ov["category"]:
                row["category"] = Category(ov["category"])
            row["reasons"] = row["reasons"] + [f"OVERRIDE:{ov['reviewed_by']}"]


def curate(
    manifest: Iterable[dict[str, Any]],
    raw_dir: Path,
    classifier: Optional[JevClassifier] = None,
    overrides: Optional[dict[int, dict[str, Any]]] = None,
    cfg: CurationConfig = CurationConfig(),
    workers: int = 8,
) -> list[dict[str, Any]]:
    rows = [_base_row(record, raw_dir) for record in sorted(manifest, key=lambda r: r["mfiles_id"])]
    _mark_exact_duplicates(rows)

    pending = []
    for row in rows:
        structural = _structural_decision(row)
        if structural:
            row.update(decision=Decision.EXCLUDE, decision_source="structural",
                       reasons=[structural[0]], category=structural[1])
        else:
            pending.append(row)

    opinions = _classify_with_model(pending, classifier, workers)
    for row in pending:
        _apply_rules_and_model(row, opinions.get(row["mfiles_id"]), cfg)

    _apply_version_families(pending, raw_dir, cfg)
    _apply_overrides(rows, overrides or {})

    for row in rows:
        row["decision"] = Decision(row["decision"]).value
        row["category"] = row["category"].value if isinstance(row["category"], Category) else row["category"]
    return rows


# --------------------------------------------------------------------------------------------
# Saídas
# --------------------------------------------------------------------------------------------
_REVIEW_PRIORITY = (
    ("OVERRIDE_STALE", 1), ("CONFLICT_MODEL", 2), ("VERSION_FAMILY", 3), ("MULTI_FILE", 4), ("ENCRYPTED_PDF", 4),
    ("WEAK_RULE", 5), ("NO_RULE", 6), ("T03C_AMBIGUO", 6), ("POSSIBLE_", 7), ("K0", 7),
)


def review_priority(row: dict[str, Any]) -> int:
    for prefix, priority in _REVIEW_PRIORITY:
        if any(r.startswith(prefix) for r in row["reasons"]):
            return priority
    return 9


def suggested_decision(row: dict[str, Any]) -> str:
    if row["family"]:
        return row["family"]["suggested"]
    if row["category"] and row["category"] != Category.INDETERMINADO.value:
        return decision_for(Category(row["category"])).value
    return ""


REVIEW_COLUMNS = [
    "prioridade", "motivo_revisao", "mfiles_id", "versao", "titulo", "classe", "pasta_origem", "cliente",
    "palavras_chave", "decisao_sugerida", "categoria_sugerida", "regra", "jev_categoria", "jev_confianca",
    "familia_id", *HUMAN_COLUMNS,
]


def _review_row(row: dict[str, Any], prioridade: Any, human: Optional[dict[str, str]] = None) -> dict[str, Any]:
    human = human or {}
    return {
        "prioridade": prioridade,
        "motivo_revisao": ", ".join(row["reasons"]),
        "mfiles_id": row["mfiles_id"],
        "versao": row["mfiles_version"],
        "titulo": row["title"],
        "classe": row["class_name"],
        "pasta_origem": row["folder"],
        "cliente": row["cliente"],
        "palavras_chave": row["palavras_chave"],
        "decisao_sugerida": suggested_decision(row) if row["decision"] == Decision.REVIEW.value else row["decision"],
        "categoria_sugerida": row["category"] or "",
        "regra": (row["rule"] or {}).get("rule_id", ""),
        "jev_categoria": (row["model"] or {}).get("category", ""),
        "jev_confianca": (row["model"] or {}).get("confidence", ""),
        "familia_id": (row["family"] or {}).get("id", ""),
        **{col: human.get(col, "") for col in HUMAN_COLUMNS},
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=REVIEW_COLUMNS, delimiter=CSV_DELIMITER)
        writer.writeheader()
        writer.writerows({k: _csv_safe(v) for k, v in row.items()} for row in rows)


def _previous_human_input(path: Path) -> dict[tuple[int, int], dict[str, str]]:
    """Preserva o que o revisor já digitou no review_queue.csv anterior (mesmo documento e versão)."""
    if not path.exists():
        return {}
    try:
        rows = _read_csv(path)
    except PermissionError as exc:
        raise PermissionError(f"Não foi possível ler {path.name}: o arquivo está aberto (ex.: no Excel)? "
                              "Feche-o e rode a curadoria novamente.") from exc
    kept = {}
    for row in rows:
        if any(row.get(col) for col in HUMAN_COLUMNS) and row.get("mfiles_id", "").isdigit() and row.get("versao", "").isdigit():
            kept[(int(row["mfiles_id"]), int(row["versao"]))] = {col: row.get(col, "") for col in HUMAN_COLUMNS}
    return kept


def qa_sample(rows: list[dict[str, Any]], cfg: CurationConfig) -> list[dict[str, Any]]:
    """Amostra estratificada e reprodutível das decisões AUTOMÁTICAS, para estimar a taxa de erro."""
    rng = random.Random(cfg.qa_seed)
    strata: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["decision"] != Decision.REVIEW.value and row["decision_source"] in {"rule", "model", "rule+model"}:
            strata[(row["decision"], row["decision_source"])].append(row)
    sample = []
    for key in sorted(strata):
        members = sorted(strata[key], key=lambda r: r["mfiles_id"])
        sample += rng.sample(members, min(cfg.qa_sample_per_stratum, len(members)))
    return sample


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_fingerprint(raw_dir: Path) -> str:
    return _sha256(raw_dir / MANIFEST_NAME)


def _agreement(rows: list[dict[str, Any]]) -> dict[str, Optional[float]]:
    pairs = [(Category(r["rule"]["category"]), Category(r["model"]["category"])) for r in rows if r["rule"] and r["model"]]
    if not pairs:
        return {"decision": None, "category": None, "n": 0}
    return {
        "decision": round(sum(decision_for(a) is decision_for(b) for a, b in pairs) / len(pairs), 4),
        "category": round(sum(a is b for a, b in pairs) / len(pairs), 4),
        "n": len(pairs),
    }


def write_outputs(
    rows: list[dict[str, Any]], out_dir: Path, raw_dir: Path, cfg: CurationConfig,
    classifier: Optional[JevClassifier], overrides: Optional[dict[int, dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Grava tudo em .tmp e só então substitui; summary.json é gravado por último (marcador de commit)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    human = _previous_human_input(out_dir / REVIEW_FILE)
    review = sorted((r for r in rows if r["decision"] == Decision.REVIEW.value),
                    key=lambda r: (review_priority(r), (r["family"] or {}).get("id", ""), r["mfiles_id"]))

    tmp = {name: out_dir / f"{name}.tmp" for name in (CURATION_FILE, REVIEW_FILE, QA_FILE, SUMMARY_FILE)}
    tmp[CURATION_FILE].write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows),
                                  encoding="utf-8")
    _write_csv(tmp[REVIEW_FILE], [_review_row(r, review_priority(r), human.get((r["mfiles_id"], r["mfiles_version"])))
                                  for r in review])
    _write_csv(tmp[QA_FILE], [_review_row(r, "controle_qualidade") for r in qa_sample(rows, cfg)])

    known = {r["mfiles_id"] for r in rows}
    unknown_overrides = sorted(set(overrides or {}) - known)
    if unknown_overrides:
        logger.error("Overrides para documentos inexistentes no manifest", extra={"mfiles_ids": unknown_overrides})
    snapshots = Counter(r["model"]["model_version"] for r in rows if r["model"])
    summary = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rules_version": RULES_VERSION,
        "manifest_sha256": manifest_fingerprint(raw_dir),
        "curation_sha256": _sha256(tmp[CURATION_FILE]),
        "model": classifier.model if classifier else None,
        "model_snapshots": dict(snapshots),
        "model_snapshot_drift": any(s != cfg.calibrated_snapshot for s in snapshots),
        "config": asdict(cfg),
        "totals": dict(Counter(r["decision"] for r in rows)),
        "by_category": dict(Counter(f"{r['decision']}:{r['category'] or ('duplicate' if r['duplicate_of'] else 'none')}"
                                    for r in rows)),
        # Obrigação da Etapa 2: completar a verificação de quase-duplicatas sobre o texto parseado.
        "near_dup_unchecked_includes": sorted(r["mfiles_id"] for r in rows if r["decision"] == "include"
                                              and not r["flags"].get("near_dup_checked")
                                              and r["file"]["parse_route"] != ParseRoute.DOCX.value),
        "by_source": dict(Counter(f"{r['decision']}:{r['decision_source']}" for r in rows)),
        "by_class": dict(Counter(f"{r['class_name']}:{r['decision']}" for r in rows)),
        "review_reasons": dict(Counter(reason.split(":")[0] for r in review for reason in r["reasons"])),
        "parse_routes_included": dict(Counter(r["file"]["parse_route"] for r in rows if r["decision"] == "include")),
        "duplicates_removed": sum(1 for r in rows if r["duplicate_of"]),
        "version_families": dict(Counter(r["family"]["kind"] for r in rows if r["family"])),
        "overrides_applied": sum(1 for r in rows if r["decision_source"] == "override"),
        "overrides_unknown_ids": unknown_overrides,
        "rule_model_agreement": _agreement(rows),
        "model_calls": classifier.calls if classifier else 0,
        "model_errors": classifier.errors if classifier else 0,
        "model_cost_usd": round(classifier.cost_usd, 6) if classifier else 0.0,
    }
    tmp[SUMMARY_FILE].write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    for name in (CURATION_FILE, REVIEW_FILE, QA_FILE, SUMMARY_FILE):
        try:
            tmp[name].replace(out_dir / name)
        except PermissionError as exc:
            raise PermissionError(f"Não foi possível gravar {name}: o arquivo está aberto (ex.: no Excel)? "
                                  f"Feche-o e rode a curadoria novamente. Saída nova em {tmp[name].name}.") from exc
    return summary


# --------------------------------------------------------------------------------------------
# Interface das Etapas 2-4
# --------------------------------------------------------------------------------------------
def _load_rows(curation_dir: Path, raw_dir: Path, verify_files: bool) -> list[dict[str, Any]]:
    summary = json.loads((curation_dir / SUMMARY_FILE).read_text(encoding="utf-8"))
    if summary.get("schema_version") != SCHEMA_VERSION:
        raise CurationStaleError(f"Esquema {summary.get('schema_version')} ≠ {SCHEMA_VERSION}. Rode a curadoria.")
    if summary["rules_version"] != RULES_VERSION:
        raise CurationStaleError(f"Curadoria gerada com regras {summary['rules_version']}; vigente: {RULES_VERSION}.")
    if summary["manifest_sha256"] != manifest_fingerprint(raw_dir):
        raise CurationStaleError("O manifest mudou desde a última curadoria. Rode: uv run python -m app.ingestion.curation")
    if summary["curation_sha256"] != _sha256(curation_dir / CURATION_FILE):
        raise CurationStaleError("curation.jsonl não corresponde ao summary.json (gravação interrompida?).")
    rows = [json.loads(line) for line in (curation_dir / CURATION_FILE).read_text(encoding="utf-8").splitlines() if line]
    manifest = load_manifest(raw_dir)
    for r in rows:
        if r["decision"] != Decision.INCLUDE.value:
            continue
        if manifest.get(r["mfiles_id"], {}).get("mfiles_version") != r["mfiles_version"]:
            raise CurationStaleError(f"Documento {r['mfiles_id']} divergente do manifest.")
        if verify_files and _sha256(raw_dir / r["file"]["path"]) != r["file"]["sha256"]:
            raise CurationStaleError(f"Arquivo do documento {r['mfiles_id']} foi alterado em disco.")
    return rows


def load_curated(
    curation_dir: Optional[Path] = None,
    raw_dir: Optional[Path] = None,
    decisions: tuple[str, ...] = (Decision.INCLUDE.value,),
    verify_files: bool = True,
) -> list[dict[str, Any]]:
    """
    Registros curados para a Etapa 2 (parsing). Recusa (CurationStaleError) curadoria gerada a partir de
    outro manifest, outras regras, outro esquema, gravação incompleta ou documento divergente.
    Somente `include` por padrão: documentos em `review` NUNCA devem ser indexados.
    """
    settings = get_settings()
    rows = _load_rows(curation_dir or settings.curation_dir, raw_dir or settings.raw_data_dir, verify_files)
    return [r for r in rows if r["decision"] in decisions]


def index_plan(indexed: dict[int, str], curation_dir: Optional[Path] = None,
               raw_dir: Optional[Path] = None) -> dict[str, list]:
    """
    Plano de reindexação incremental para as Etapas 2-4, a partir do estado atual do índice
    ({mfiles_id: doc_key indexado}):
      upsert    -> registros include novos ou com doc_key diferente (nova versão/conteúdo);
      delete    -> mfiles_ids indexados que não são mais include (exclude, review, override, removido do vault);
      unchanged -> mfiles_ids include já indexados com o mesmo doc_key.
    """
    included = {r["mfiles_id"]: r for r in load_curated(curation_dir, raw_dir)}
    return {
        "upsert": [r for mid, r in sorted(included.items()) if indexed.get(mid) != r["doc_key"]],
        "delete": sorted(mid for mid in indexed if mid not in included),
        "unchanged": sorted(mid for mid, r in included.items() if indexed.get(mid) == r["doc_key"]),
    }


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Curadoria do corpus RAG (Etapa 1).")
    parser.add_argument("--no-model", action="store_true", help="Somente regras (casos sem regra vão para revisão).")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    out_dir = settings.curation_dir
    cfg = CurationConfig(calibrated_snapshot=settings.jev_calibrated_snapshot)
    overrides = load_overrides(out_dir / OVERRIDES_FILE)
    classifier = None if args.no_model else JevClassifier(
        api_key=settings.openai_api_key,
        cache_path=out_dir / "jev_cache.jsonl",
        model=settings.jev_model,
        url=settings.jev_decisions_url,
    )
    try:
        manifest = load_manifest(settings.raw_data_dir).values()
        rows = curate(manifest, settings.raw_data_dir, classifier, overrides, cfg, workers=args.workers)
        summary = write_outputs(rows, out_dir, settings.raw_data_dir, cfg, classifier, overrides)
    finally:
        if classifier:
            classifier.close()
    logger.info("Curadoria concluída", extra={"totals": summary["totals"], "rules_version": RULES_VERSION})
    print(json.dumps({k: summary[k] for k in ("totals", "review_reasons", "rule_model_agreement", "duplicates_removed",
                                               "version_families", "overrides_unknown_ids", "model_snapshot_drift",
                                               "model_cost_usd")}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

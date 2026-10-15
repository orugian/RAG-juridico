"""Minimal console client for the MVP API.

    python -m app.client "Qual o valor dos honorários?" --instrument doc:4889
    python -m app.client --buscar "Alpha"          # acha o doc:NNNN pelo título/parte/CNPJ

Reads RAG_MVP_URL / RAG_MVP_QUERY_KEY / RAG_MVP_CATALOG from the environment or
the local .env (ignored by Git). The key is sent only in the X-API-Key header,
never printed. The catalog is a local file (titles/parties), never sent anywhere.
"""
import argparse
import json
from pathlib import Path
import sys
import unicodedata

import httpx
from pydantic import SecretStr
from pydantic_settings import BaseSettings

from app.config import _PROJECT_ROOT


class ClientSettings(BaseSettings):
    model_config = {"env_file": _PROJECT_ROOT / ".env", "extra": "ignore"}
    rag_mvp_url: str = "http://100.118.65.94:8790"
    rag_mvp_query_key: SecretStr = SecretStr("")
    rag_mvp_catalog: Path = _PROJECT_ROOT / "data" / "mvp-20261015" / "catalog.json"


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


def catalog_from_bundle(bundle_path: Path) -> list[dict]:
    """One entry per instrument: id, title and parties exactly as in the reviewed bundle."""
    bundle = json.loads(Path(bundle_path).read_text(encoding="utf-8"))
    entries = {}
    for unit in bundle["units"]:
        entries.setdefault(unit["instrument_id"], {
            "instrument_id": unit["instrument_id"], "title": unit["contract_title"],
            "parties": [{"name": p["name"], "identifier": p.get("clean_identifier")} for p in unit["parties"]]})
    return sorted(entries.values(), key=lambda entry: int(entry["instrument_id"].split(":")[1]))


def search_catalog(entries: list[dict], term: str) -> list[dict]:
    wanted = _fold(term).strip()
    digits = "".join(c for c in term if c.isalnum()).upper()
    def match(entry):
        names = " ".join(p["name"] for p in entry["parties"])
        identifiers = {(p["identifier"] or "").upper() for p in entry["parties"]}
        return (wanted in _fold(entry["title"]) or wanted in _fold(names)
                or wanted.removeprefix("doc:") == entry["instrument_id"].split(":", 1)[1]
                or (len(digits) >= 8 and digits in identifiers))
    return [entry for entry in entries if wanted and match(entry)]


def format_answer(data: dict) -> str:
    lines = [f"Status: {data['status']}" + (f" ({data['reason_code']})" if data.get("reason_code") else ""),
             "", data["response"]]
    for number, citation in enumerate(data.get("citations") or [], 1):
        parties = "; ".join(party.get("name", "") for party in citation.get("parties", []))
        location = citation.get("location") or {}
        lines += ["", f"[{number}] {citation['contract_title']} ({citation['contract_id']}, v{citation['document_version']})",
                  f"    Partes: {parties}", f"    Local: {location.get('label', '')}",
                  "    Trecho: " + citation["quote"].replace("\n", "\n            ")]
    lines += ["", f"Geração: {data.get('corpus_generation_id')} | request_id: {data.get('request_id')}"]
    return "\n".join(lines)


def main(argv=None, *, transport=None, settings=None) -> int:
    parser = argparse.ArgumentParser(description="Consulta o RAG Andrade Advogados (MVP)")
    parser.add_argument("question", nargs="?")
    parser.add_argument("--instrument", action="append", default=[], help="Filtrar por contrato, ex.: doc:5831")
    parser.add_argument("--buscar", metavar="TERMO", help="Procura contratos no catálogo local (título, parte, CNPJ/CPF)")
    arguments = parser.parse_args(argv)
    settings = settings or ClientSettings()
    sys.stdout.reconfigure(encoding="utf-8")
    if arguments.buscar is not None:
        if not settings.rag_mvp_catalog.is_file():
            print(f"Catálogo local não encontrado: {settings.rag_mvp_catalog}", file=sys.stderr)
            return 2
        found = search_catalog(json.loads(settings.rag_mvp_catalog.read_text(encoding="utf-8")), arguments.buscar)
        for entry in found[:50]:
            print(f"{entry['instrument_id']:>10}  {entry['title'][:90]}  —  " + "; ".join(p["name"] for p in entry["parties"]))
        print(f"{len(found)} contrato(s) encontrado(s)." + (" Mostrando 50." if len(found) > 50 else ""))
        return 0
    if not arguments.question:
        parser.error("informe a pergunta ou --buscar TERMO")
    key = settings.rag_mvp_query_key.get_secret_value()
    if not key:
        print("RAG_MVP_QUERY_KEY não configurada no .env local.", file=sys.stderr)
        return 2
    body = {"message": arguments.question, "filters": {"instrument_ids": arguments.instrument}}
    with httpx.Client(base_url=settings.rag_mvp_url, timeout=60, transport=transport) as client:
        response = client.post("/chat", json=body, headers={"X-API-Key": key})
    if response.status_code != 200:
        print(f"Erro HTTP {response.status_code}: {response.json().get('code', 'desconhecido')}", file=sys.stderr)
        return 1
    print(format_answer(response.json()))
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--gerar-catalogo":
        target = ClientSettings().rag_mvp_catalog
        target.write_text(json.dumps(catalog_from_bundle(Path(sys.argv[2])), ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"catálogo gravado em {target}")
        raise SystemExit(0)
    raise SystemExit(main())

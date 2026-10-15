"""Console client: key only in header, never echoed; answer and citations rendered."""
import json

import httpx

from app.client import ClientSettings, format_answer, main

KEY = "k" * 40
ANSWER = {"status": "answered", "reason_code": None, "response": "Os honorários são de R$ 50.000,00.",
          "corpus_generation_id": "gen-x", "request_id": "r1", "citations": [{
              "contract_title": "Contrato de Honorários", "contract_id": "doc:101", "document_version": 1,
              "parties": [{"name": "Alpha Ltda"}, {"name": "Andrade Advogados"}],
              "location": {"label": "Cláusula 1ª"}, "quote": "Os honorários são de R$ 50.000,00."}]}


def settings():
    return ClientSettings(_env_file=None, rag_mvp_url="http://rag.test", rag_mvp_query_key=KEY)


def test_client_sends_key_header_and_prints_citation(capsys):
    seen = {}

    def handler(request):
        seen["key"], seen["body"] = request.headers["X-API-Key"], json.loads(request.content)
        return httpx.Response(200, json=ANSWER)

    code = main(["Qual o valor?", "--instrument", "doc:101"], transport=httpx.MockTransport(handler),
                settings=settings())
    output = capsys.readouterr().out
    assert code == 0
    assert seen == {"key": KEY, "body": {"message": "Qual o valor?", "filters": {"instrument_ids": ["doc:101"]}}}
    assert "Cláusula 1ª" in output and "Alpha Ltda; Andrade Advogados" in output and KEY not in output


def test_client_reports_http_error_code_without_body_echo(capsys):
    transport = httpx.MockTransport(lambda request: httpx.Response(401, json={"code": "unauthorized"}))
    assert main(["pergunta"], transport=transport, settings=settings()) == 1
    assert "401" in capsys.readouterr().err


def test_client_refuses_to_run_without_key(capsys):
    empty = ClientSettings(_env_file=None, rag_mvp_query_key="")
    assert main(["pergunta"], settings=empty) == 2


def test_abstention_has_reason_and_no_citations():
    text = format_answer({"status": "abstained", "reason_code": "insufficient_evidence", "response": "Não localizado.",
                          "citations": [], "corpus_generation_id": "g", "request_id": "r"})
    assert text.startswith("Status: abstained (insufficient_evidence)") and "[1]" not in text



def test_catalog_search_by_title_party_accents_and_identifier(tmp_path, capsys):
    from app.client import catalog_from_bundle, search_catalog
    bundle = tmp_path / "bundle.json"
    party = lambda name, ident=None: {"name": name, "clean_identifier": ident}
    bundle.write_text(json.dumps({"units": [
        {"instrument_id": "doc:12", "contract_title": "Contrato de Honorários", "parties": [party("Alpha Serviços Ltda", "11222333000144")]},
        {"instrument_id": "doc:12", "contract_title": "Contrato de Honorários", "parties": [party("Alpha Serviços Ltda", "11222333000144")]},
        {"instrument_id": "doc:3", "contract_title": "Locação Comercial", "parties": [party("Beta S.A.")]}]}), encoding="utf-8")
    entries = catalog_from_bundle(bundle)
    assert [e["instrument_id"] for e in entries] == ["doc:3", "doc:12"]
    assert [e["instrument_id"] for e in search_catalog(entries, "honorarios")] == ["doc:12"]
    assert [e["instrument_id"] for e in search_catalog(entries, "SERVIÇOS")] == ["doc:12"]
    assert [e["instrument_id"] for e in search_catalog(entries, "11.222.333/0001-44")] == ["doc:12"]
    assert [e["instrument_id"] for e in search_catalog(entries, "12")] == ["doc:12"]
    assert [e["instrument_id"] for e in search_catalog(entries, "doc:3")] == ["doc:3"]
    assert search_catalog(entries, "") == [] and search_catalog(entries, "inexistente") == []
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps(entries), encoding="utf-8")
    local = ClientSettings(_env_file=None, rag_mvp_query_key="", rag_mvp_catalog=catalog)
    assert main(["--buscar", "beta"], settings=local) == 0  # no key or network needed
    assert "doc:3" in capsys.readouterr().out

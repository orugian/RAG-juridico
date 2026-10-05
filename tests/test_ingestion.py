"""
Testes do cliente M-Files e da sincronização incremental (app/ingestion).
Usa um vault simulado via httpx.MockTransport — nenhuma chamada real à rede.
"""

import json

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.ingestion.mfiles_client import MFilesClient, MFilesError
from app.ingestion.sync import load_manifest, sync


class FakeVault:
    """Simula os endpoints REST do M-Files usados na extração."""

    def __init__(self):
        self.version = 3
        self.fail_next: list[int] = []
        self.expire_token_once = False
        self.calls: list[str] = []
        self.logins = 0
        self.documents = {
            13: [self._doc(5607, 13, "Contrato de Honorários nº 104/2023", "docx")],
            34: [self._doc(6000, 34, "Acordo Starmkt", "pdf")],
        }

    def _doc(self, obj_id, class_id, title, ext):
        return {
            "ObjVer": {"ID": obj_id, "Version": self.version, "Type": 0},
            "Class": class_id,
            "Title": title,
            "LastModifiedUtc": "2025-02-06T18:13:23Z",
            "Files": [{"ID": obj_id + 1, "Version": 1, "Name": title, "Extension": ext, "Size": 10}],
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/REST")
        self.calls.append(f"{request.method} {path}")
        if self.fail_next:
            return httpx.Response(self.fail_next.pop(0))
        if path == "/server/authenticationtokens":
            self.logins += 1
            body = json.loads(request.content)
            if body["Password"] != "segredo":
                return httpx.Response(403)
            return httpx.Response(200, json={"Value": f"token-{self.logins}"})
        if self.expire_token_once:
            self.expire_token_once = False
            return httpx.Response(401)
        if path == "/structure/classes":
            return httpx.Response(200, json=[
                {"ID": 13, "Name": "Contrato"}, {"ID": 152, "Name": "Contrato"},
                {"ID": 34, "Name": "Acordo"}, {"ID": 81, "Name": "Petição"},
                {"ID": 0, "Name": "Documento"}, {"ID": 1, "Name": "Outro documento"},
            ])
        if path == "/structure/properties":
            return httpx.Response(200, json=[{"ID": 0, "Name": "Nome ou título"}, {"ID": 1021, "Name": "Cliente"}])
        if path == "/objects/0":
            class_id = int(request.url.params["p100"])
            return httpx.Response(200, json={"Items": self.documents.get(class_id, []), "MoreResults": False})
        if path.endswith("/properties"):
            return httpx.Response(200, json=[
                {"PropertyDef": 0, "TypedValue": {"DisplayValue": "Contrato de Honorários nº 104/2023"}},
                {"PropertyDef": 1021, "TypedValue": {"DisplayValue": "Starmkt Advertising Ltda"}},
                {"PropertyDef": 1105, "TypedValue": {"DisplayValue": ""}},
            ])
        if path.endswith("/content"):
            return httpx.Response(200, content="Cláusula 4ª, § 2º — R$ 10.000,00".encode())
        return httpx.Response(404)


@pytest.fixture
def vault() -> FakeVault:
    return FakeVault()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        openai_api_key="test",
        mfiles_base_url="https://vault.test/REST",
        mfiles_vault_guid="{GUID}",
        mfiles_username="user@test",
        mfiles_password="segredo",
    )


@pytest.fixture
def client(settings, vault):
    with MFilesClient(settings, transport=httpx.MockTransport(vault), backoff_seconds=0) as c:
        yield c


def test_missing_credentials_raises(settings):
    with pytest.raises(MFilesError):
        MFilesClient(settings.model_copy(update={"mfiles_username": ""}))


def test_authentication_failure_raises(settings, vault):
    bad = settings.model_copy(update={"mfiles_password": SecretStr("errada")})
    with MFilesClient(bad, transport=httpx.MockTransport(vault)) as c, pytest.raises(MFilesError, match="autenticação"):
        c.get_document_classes()


def test_resolve_class_ids_returns_all_matching_ids(client):
    assert client.resolve_class_ids(["contrato", "Acordo", "Inexistente"]) == {13: "Contrato", 152: "Contrato", 34: "Acordo"}


def test_resolve_class_ids_matches_exact_name_only(client):
    assert client.resolve_class_ids(["Documento"]) == {0: "Documento"}


def test_reauthenticates_on_expired_token(client, vault):
    client.authenticate()
    vault.expire_token_once = True
    assert client.get_document_classes()
    assert vault.logins == 2


def test_retries_on_server_error(client, vault):
    client.authenticate()
    vault.fail_next = [503, 502]
    assert client.get_document_classes()


def test_gives_up_after_max_retries(client, vault):
    client.authenticate()
    vault.fail_next = [500] * 10
    with pytest.raises(MFilesError):
        client.get_document_classes()


def test_sync_downloads_files_and_writes_manifest(client, tmp_path):
    stats = sync(client, tmp_path, ["Contrato", "Acordo"])

    assert stats["listed"] == 2 and stats["downloaded"] == 2
    manifest = load_manifest(tmp_path)
    record = manifest[5607]
    assert record["class_name"] == "Contrato"
    assert record["properties"] == {"Nome ou título": "Contrato de Honorários nº 104/2023", "Cliente": "Starmkt Advertising Ltda"}
    content = (tmp_path / record["files"][0]["path"]).read_bytes().decode()
    assert "Cláusula 4ª, § 2º" in content
    assert record["files"][0]["path"] == "files/5607/5608_v3.docx"


def test_sync_is_incremental(client, vault, tmp_path):
    sync(client, tmp_path, ["Contrato", "Acordo"])
    vault.calls.clear()

    stats = sync(client, tmp_path, ["Contrato", "Acordo"])

    assert stats["unchanged"] == 2 and stats["downloaded"] == 0
    assert not any(c.endswith("/content") for c in vault.calls)


def test_sync_redownloads_new_version_and_removes_old_file(client, vault, tmp_path):
    sync(client, tmp_path, ["Contrato"])
    vault.version = 4
    vault.documents[13] = [vault._doc(5607, 13, "Contrato de Honorários nº 104/2023", "docx")]

    stats = sync(client, tmp_path, ["Contrato"])

    assert stats["downloaded"] == 1
    files = sorted(p.name for p in (tmp_path / "files" / "5607").iterdir())
    assert files == ["5608_v4.docx"]


def test_sync_removes_documents_deleted_from_vault(client, vault, tmp_path):
    sync(client, tmp_path, ["Contrato", "Acordo"])
    vault.documents[34] = []

    stats = sync(client, tmp_path, ["Contrato", "Acordo"])

    assert stats["removed"] == 1
    assert 6000 not in load_manifest(tmp_path)
    assert not (tmp_path / "files" / "6000").exists()

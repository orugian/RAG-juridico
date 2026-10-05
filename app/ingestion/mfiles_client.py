"""
M-Files REST API Client (somente leitura) para extração do acervo contratual.

- Autenticação por token (X-Authentication) com re-autenticação automática em 401/403.
- Retry com backoff exponencial para timeouts, erros de rede e HTTP 429/5xx.
- Estrutura do vault (classes/propriedades) resolvida em tempo de execução:
  os IDs variam entre vaults e nunca devem ser fixados no código.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from app.config import Settings, get_settings
from app.monitoring import logger

DOCUMENT_OBJECT_TYPE = 0
CLASS_PROPERTY_DEF = 100
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class MFilesError(RuntimeError):
    """Falha não recuperável na comunicação com o M-Files."""


@dataclass(frozen=True)
class MFilesFile:
    id: int
    version: int
    name: str
    extension: str
    size: int


@dataclass(frozen=True)
class MFilesDocument:
    id: int
    version: int
    class_id: int
    title: str
    last_modified_utc: str
    files: list[MFilesFile] = field(default_factory=list)

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> "MFilesDocument":
        return cls(
            id=item["ObjVer"]["ID"],
            version=item["ObjVer"]["Version"],
            class_id=item["Class"],
            title=item.get("Title", ""),
            last_modified_utc=item.get("LastModifiedUtc", ""),
            files=[
                MFilesFile(
                    id=f["ID"],
                    version=f.get("Version", 0),
                    name=f.get("Name", ""),
                    extension=(f.get("Extension") or "").lower(),
                    size=f.get("Size", 0),
                )
                for f in item.get("Files", [])
            ],
        )


class MFilesClient:
    def __init__(
        self,
        settings: Optional[Settings] = None,
        transport: Optional[httpx.BaseTransport] = None,
        max_retries: int = 3,
        backoff_seconds: float = 1.0,
    ):
        self.settings = settings or get_settings()
        if not (self.settings.mfiles_username and self.settings.mfiles_vault_guid):
            raise MFilesError("Credenciais M-Files ausentes (MFILES_USERNAME / MFILES_VAULT_GUID no .env).")
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self._token: Optional[str] = None
        self._http = httpx.Client(
            base_url=self.settings.mfiles_base_url,
            timeout=self.settings.mfiles_timeout_seconds,
            transport=transport,
        )

    def __enter__(self) -> "MFilesClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def authenticate(self) -> None:
        response = self._http.post(
            "/server/authenticationtokens",
            json={
                "Username": self.settings.mfiles_username,
                "Password": self.settings.mfiles_password.get_secret_value(),
                "VaultGuid": self.settings.mfiles_vault_guid,
            },
        )
        if response.status_code != 200:
            raise MFilesError(f"Falha de autenticação no M-Files (HTTP {response.status_code}).")
        self._token = response.json()["Value"]

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if self._token is None:
            self.authenticate()
        reauthenticated = False
        for attempt in range(self.max_retries + 1):
            try:
                response = self._http.request(method, path, headers={"X-Authentication": self._token}, **kwargs)
            except httpx.TransportError as exc:
                if attempt == self.max_retries:
                    raise MFilesError(f"Erro de rede em {method} {path}: {exc}") from exc
                time.sleep(self.backoff_seconds * 2**attempt)
                continue

            if response.status_code in (401, 403) and not reauthenticated:
                reauthenticated = True
                self.authenticate()
                continue
            if response.status_code in _RETRYABLE_STATUS and attempt < self.max_retries:
                logger.warning("M-Files retry", extra={"path": path, "status": response.status_code, "attempt": attempt + 1})
                time.sleep(self.backoff_seconds * 2**attempt)
                continue
            if response.is_error:
                raise MFilesError(f"{method} {path} retornou HTTP {response.status_code}.")
            return response
        raise MFilesError(f"{method} {path} falhou após {self.max_retries} tentativas.")

    def get_document_classes(self) -> dict[int, str]:
        classes = self._request("GET", "/structure/classes", params={"objtype": DOCUMENT_OBJECT_TYPE}).json()
        return {c["ID"]: c["Name"] for c in classes}

    def get_property_definitions(self) -> dict[int, str]:
        return {p["ID"]: p["Name"] for p in self._request("GET", "/structure/properties").json()}

    def resolve_class_ids(self, class_names: list[str]) -> dict[int, str]:
        """Mapeia nomes de classes para IDs reais do vault (um nome pode ter vários IDs)."""
        wanted = {n.strip().casefold() for n in class_names}
        return {cid: name for cid, name in self.get_document_classes().items() if name.strip().casefold() in wanted}

    def list_documents(self, class_id: int, limit: int = 100_000) -> list[MFilesDocument]:
        payload = self._request(
            "GET", f"/objects/{DOCUMENT_OBJECT_TYPE}", params={f"p{CLASS_PROPERTY_DEF}": class_id, "limit": limit}
        ).json()
        if payload.get("MoreResults"):
            logger.warning("M-Files truncou a listagem", extra={"class_id": class_id, "limit": limit})
        return [MFilesDocument.from_api(item) for item in payload.get("Items", []) if not item.get("Deleted")]

    def get_properties(self, document: MFilesDocument) -> list[dict[str, Any]]:
        return self._request(
            "GET", f"/objects/{DOCUMENT_OBJECT_TYPE}/{document.id}/{document.version}/properties"
        ).json()

    def download_file(self, document: MFilesDocument, file: MFilesFile) -> bytes:
        return self._request(
            "GET", f"/objects/{DOCUMENT_OBJECT_TYPE}/{document.id}/{document.version}/files/{file.id}/content"
        ).content


__all__ = ["MFilesClient", "MFilesDocument", "MFilesFile", "MFilesError"]

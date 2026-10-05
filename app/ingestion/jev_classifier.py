"""
Classificador de segunda opinião: Jev (TypeSafe) via OpenRouter Decisions API.

- Modelo de decisão (não generativo): devolve um rótulo dentro do enum declarado, com
  probabilidades calibradas e confiança — nunca texto livre.
- Versão do modelo FIXADA (typesafe/jev-1.13): limiares calibrados não podem "andar" sozinhos.
- Cache persistente por hash(modelo + state + perguntas): reexecuções são determinísticas e
  gratuitas; mudar perguntas ou modelo invalida apenas as entradas afetadas.
- Minimização de dados (LGPD): envia apenas título, classe, pasta de origem relativa e
  palavras-chave. Nunca envia conteúdo dos arquivos nem a propriedade "Cliente".
"""

import hashlib
import json
import random
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx

from app.ingestion.curation_rules import Category
from app.monitoring import logger

_RETRYABLE = frozenset({408, 429, 500, 502, 503, 504})

CATEGORY_CRITERIA: dict[str, str] = {
    Category.INSTRUMENTO_CONTRATUAL.value: "Contrato, instrumento particular, termo, cessão, comodato, locação, mútuo, "
                                          "confissão de dívida ou compromisso firmado entre partes.",
    Category.ADITIVO.value: "Aditivo ou termo complementar que altera um contrato existente.",
    Category.DISTRATO.value: "Distrato, rescisão contratual, termo de quitação ou encerramento de contrato.",
    Category.ACORDO.value: "Acordo judicial ou extrajudicial, transação ou memorando de entendimentos entre partes.",
    Category.PROPOSTA_HONORARIOS.value: "Proposta comercial ou de honorários advocatícios enviada a um cliente.",
    Category.ATO_SOCIETARIO.value: "Contrato social, alteração contratual de sociedade, acordo de quotistas, "
                                   "constituição ou distrato de sociedade/consórcio.",
    Category.MODELO.value: "Modelo genérico ou template, sem partes reais identificadas.",
    Category.MINUTA.value: "Minuta, rascunho ou versão em negociação de um documento, ainda não final.",
    Category.PECA_PROCESSUAL.value: "Petição, recurso, contestação, manifestação ou outro ato de processo judicial "
                                    "ou administrativo.",
    Category.PARECER_ANALISE.value: "Parecer, opinião legal, análise, estudo ou relatório sobre um tema ou contrato.",
    Category.DOCUMENTO_PESSOAL.value: "Documento pessoal, certidão, matrícula de imóvel ou comprovante.",
    Category.DOCUMENTO_ADMINISTRATIVO.value: "Procuração, substabelecimento, carta, notificação, declaração, "
                                             "regulamento, planilha, fatura ou outro documento administrativo.",
    "outro": "Nenhuma das categorias anteriores.",
}

QUESTIONS: dict[str, Any] = {
    "categoria": {
        "type": "choice",
        "instructions": "Documento do acervo de um escritório de advocacia brasileiro. "
                        "Com base no título, na classe e na pasta de origem, qual é o tipo do documento?",
        "criteria": CATEGORY_CRITERIA,
    },
    "modelo": {
        "type": "noul",
        "instructions": "O documento é um modelo genérico (template), sem partes reais identificadas?",
        "criteria": {
            "true": "Modelo, template ou formulário-padrão para ser preenchido.",
            "false": "Documento referente a partes, casos ou negócios reais.",
        },
    },
    "minuta": {
        "type": "noul",
        "instructions": "O documento é uma minuta, rascunho ou versão intermediária em negociação?",
        "criteria": {
            "true": "Minuta, rascunho, versão revisada por uma das partes ou ainda não final.",
            "false": "Versão final, assinada ou definitiva do documento.",
        },
    },
}


@dataclass(frozen=True)
class JevOpinion:
    category: Category                 # INDETERMINADO quando Jev escolhe "outro"
    confidence: float
    probabilities: dict[str, float]
    p_template: float
    p_draft: float
    model_version: str
    cost_usd: float = 0.0
    cached: bool = False

    @classmethod
    def from_answers(cls, payload: dict[str, Any], cached: bool = False) -> "JevOpinion":
        answers = payload["answers"]
        choice = answers["categoria"]["choice"]
        return cls(
            category=Category(choice) if choice in Category._value2member_map_ else Category.INDETERMINADO,
            confidence=float(answers["categoria"].get("confidence", 0.0)),
            probabilities={k: float(v) for k, v in answers["categoria"].get("probabilities", {}).items()},
            p_template=float(answers["modelo"]["noul"]),
            p_draft=float(answers["minuta"]["noul"]),
            model_version=payload.get("model", ""),
            cost_usd=float(payload.get("usage", {}).get("cost", 0.0)),
            cached=cached,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "confidence": round(self.confidence, 4),
            "probabilities": {k: round(v, 4) for k, v in self.probabilities.items()},
            "p_template": round(self.p_template, 4),
            "p_draft": round(self.p_draft, 4),
            "model_version": self.model_version,
        }


def build_state(title: str, class_name: str, folder: str, keywords: str) -> dict[str, str]:
    return {
        "titulo": title.strip(),
        "classe_mfiles": class_name,
        "pasta_origem": folder or "(desconhecida)",
        "palavras_chave": keywords or "(nenhuma)",
    }


class JevClassifier:
    def __init__(
        self,
        api_key: str,
        cache_path: Path,
        model: str = "typesafe/jev-1.13",
        url: str = "https://openrouter.ai/api/alpha/decisions",
        timeout: float = 30.0,
        max_retries: int = 4,
        backoff_seconds: float = 1.0,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        if not api_key:
            raise ValueError("Chave do OpenRouter ausente para o classificador Jev.")
        self.model = model
        self.url = url
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.cache_path = cache_path
        self._lock = threading.Lock()
        self._cache = self._load_cache()
        self._http = httpx.Client(timeout=timeout, transport=transport, headers={"Authorization": f"Bearer {api_key}"})
        self.calls = 0
        self.errors = 0
        self.cost_usd = 0.0

    def close(self) -> None:
        self._http.close()

    def _load_cache(self) -> dict[str, dict[str, Any]]:
        """Linhas corrompidas (ex.: escrita interrompida por crash) são descartadas e reclassificadas."""
        if not self.cache_path.exists():
            return {}
        cache, discarded = {}, 0
        with self.cache_path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    JevOpinion.from_answers(entry["response"])
                    cache[entry["key"]] = entry["response"]
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    discarded += 1
        if discarded:
            logger.warning("Cache Jev: entradas inválidas descartadas", extra={"discarded": discarded})
        return cache

    def cache_key(self, state: dict[str, str]) -> str:
        blob = json.dumps({"model": self.model, "state": state, "questions": QUESTIONS}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _wait(self, attempt: int, response: Optional[httpx.Response] = None) -> None:
        retry_after = response.headers.get("Retry-After") if response is not None else None
        delay = float(retry_after) if retry_after and retry_after.isdigit() else self.backoff_seconds * 2**attempt
        time.sleep(min(delay, 60.0) * (1 + random.random() * 0.25))  # jitter evita rajadas sincronizadas

    def _post(self, state: dict[str, str]) -> dict[str, Any]:
        body = {"model": self.model, "state": state, "questions": QUESTIONS}
        for attempt in range(self.max_retries + 1):
            try:
                response = self._http.post(self.url, json=body)
            except httpx.TransportError as exc:  # inclui timeouts
                if attempt == self.max_retries:
                    raise
                logger.warning("Jev erro de rede, nova tentativa", extra={"attempt": attempt + 1, "error": type(exc).__name__})
                self._wait(attempt)
                continue
            if response.status_code == 200:
                return response.json()
            if response.status_code not in _RETRYABLE or attempt == self.max_retries:
                response.raise_for_status()
            self._wait(attempt, response)
        raise RuntimeError("inalcançável")

    def _persist(self, key: str, payload: dict[str, Any]) -> None:
        """Falha de escrita no cache (disco cheio, antivírus, permissão) não é fatal: só perde o reaproveitamento."""
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with self.cache_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"key": key, "response": payload}, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.error("Falha ao gravar cache Jev (não fatal)", extra={"error": type(exc).__name__})

    def classify(self, state: dict[str, str]) -> Optional[JevOpinion]:
        """Retorna None em caso de falha (o documento segue para revisão humana, nunca é decidido às cegas)."""
        key = self.cache_key(state)
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return JevOpinion.from_answers(cached, cached=True)  # validado em _load_cache / ao gravar
        try:
            payload = self._post(state)
            opinion = JevOpinion.from_answers(payload)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            with self._lock:
                self.errors += 1
            # Nunca logar o state (contém títulos com nomes de clientes) nem cabeçalhos (chave de API).
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            logger.error("Falha na classificação Jev", extra={"error": type(exc).__name__, "status": status})
            return None
        with self._lock:
            self.calls += 1
            self.cost_usd += opinion.cost_usd
            self._cache[key] = payload
            self._persist(key, payload)
        return opinion


__all__ = ["JevClassifier", "JevOpinion", "QUESTIONS", "CATEGORY_CRITERIA", "build_state"]

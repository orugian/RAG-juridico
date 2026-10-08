"""Servidor local de teste empírico para o RAG Andrade Advogados (P7).

Inicializa a aplicação FastAPI com um GovernedAnswerRuntime sintético real,
credenciais de teste configuradas (QUERY_KEY e OPERATOR_KEY) e CORS liberado
para o navegador. Também serve o console docs/p7_teste_empirico.html na rota raiz.
"""
import sys
from pathlib import Path

# Adiciona a raiz do projeto ao sys.path
root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

import tempfile
import uvicorn
from app.main import create_app
from app.config import Settings
from app.http_contracts import HttpConfig
from app.answer_runtime import GovernedAnswerRuntime
from app.cache import GovernedResponseCache
from tests.p6_corpus import build_p6_environment
from tests.test_p6_pipeline import Scripted, pick
from tests.test_api import QUERY_KEY, OPERATOR_KEY


def create_dev_app():
    """Factory que monta o backend governado com runtime real sintético e CORS."""
    tmp_path = Path(tempfile.mkdtemp(prefix="rag_dev_server_"))
    env = build_p6_environment(tmp_path)
    generator = Scripted(pick(env, "Cláusula 1ª"))
    runtime = GovernedAnswerRuntime(env.manager, generator, cache=GovernedResponseCache())

    settings = Settings(
        _env_file=None,
        app_env="development",
        api_secret_key=QUERY_KEY,
        operator_api_secret_key=OPERATOR_KEY
    )

    http_config = HttpConfig(
        allowed_origins=(
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:5500",
            "http://127.0.0.1:5500",
            "http://localhost:8080",
            "http://127.0.0.1:8080"
        ),
        deadline_seconds=30.0,
        max_body_bytes=65536
    )

    fastapi_app = create_app(runtime=runtime, settings=settings, http_config=http_config)
    html_path = root / "docs" / "p7_teste_empirico.html"

    # Wrapper ASGI para servir o console HTML sem exigir X-API-Key na tela de abertura
    async def asgi_dev_app(scope, receive, send):
        if scope["type"] == "http":
            if scope["path"] in {"/", "/console"}:
                body = html_path.read_bytes()
                headers = [
                    (b"content-type", b"text/html; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ]
                await send({"type": "http.response.start", "status": 200, "headers": headers})
                await send({"type": "http.response.body", "body": body})
                return
            if scope["path"] == "/favicon.ico":
                await send({"type": "http.response.start", "status": 204, "headers": []})
                await send({"type": "http.response.body", "body": b""})
                return
        await fastapi_app(scope, receive, send)

    return asgi_dev_app


if __name__ == "__main__":
    print("\n" + "="*75)
    print(" Andrade Advogados — Servidor de Testes Empíricos RAG (P7)")
    print("="*75)
    print(f" URL do Console no Navegador: http://127.0.0.1:8000/ (ou /console)")
    print(f" Chave de Consulta (Header):  {QUERY_KEY}")
    print(f" Chave de Operador (Header):  {OPERATOR_KEY}")
    print(" Endpoints ativos:            /v1/answer, /v1/answer/stream, /chat,")
    print("                              /ready, /metrics, /admin/policy, /health")
    print("="*75 + "\n")
    uvicorn.run(create_dev_app, host="127.0.0.1", port=8000, factory=True)

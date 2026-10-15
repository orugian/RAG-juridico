"""Operator-composed HTTP service over an already promoted governed generation.

``uvicorn app.service:create_service_app --factory`` composes, explicitly from the
operator's environment/.env, the accepted parts: GenerationManager (P4) over the
existing ledger/journal, local Qwen encoder, embedded Chroma, OpenRouter
generator (P6) restricted to Zero Data Retention routing, and the P7 app.

Nothing is built, promoted, repaired or downloaded here. Missing or inconsistent
authorities fail at startup; the LLM only selects reviewed units (extractive).
"""
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
# OpenRouter routing restriction: only endpoints with zero data retention and
# no data collection may serve a request; otherwise OpenRouter fails it.
ZDR_PROVIDER_PREFERENCES = {"zdr": True, "data_collection": "deny"}


class ServiceSettings(BaseSettings):
    model_config = {"env_prefix": "RAG_", "env_file": _PROJECT_ROOT / ".env", "extra": "ignore"}
    index_root: Path
    authority_dir: Path
    generation_config: Path
    journal_id: str = Field(min_length=1)
    qwen_artifact: Path
    vector_mode: Literal["embedded"] = "embedded"
    http_workers: int = Field(default=2, ge=1, le=16)
    http_queue: int = Field(default=4, ge=0, le=64)
    allowed_origins: tuple[str, ...] = ()
    # Real contracts have long canonical units; the P6 default (10 units within
    # 4,096 tokens) abstains on most real questions instead of truncating.
    retrieval_k: int = Field(default=6, ge=1, le=100)
    retrieval_max_tokens: int = Field(default=10000, ge=1)
    max_prompt_tokens: int = Field(default=14000, ge=1)


def openrouter_zdr_client(model: str, settings):
    from langchain_openai import ChatOpenAI
    if not settings.openai_base_url.rstrip("/").startswith("https://openrouter.ai/api"):
        raise RuntimeError("ZDR routing is enforced only for the OpenRouter API")
    return ChatOpenAI(model=model, api_key=settings.openai_api_key, base_url=settings.openai_base_url,
                      temperature=0, timeout=settings.request_timeout_seconds, max_retries=0,
                      extra_body={"provider": dict(ZDR_PROVIDER_PREFERENCES)})


def build_runtime(service: ServiceSettings, settings, *, encoder=None, budget=None, vector_store=None,
                  client_factory=openrouter_zdr_client):
    from app.answer_runtime import GovernedAnswerRuntime
    from app.cache import GovernedResponseCache
    from app.generation import AnswerConfig
    from app.generators import build_provider_generator
    from app.retrieval.generation_contracts import GenerationConfig
    from app.retrieval.policy import SQLitePolicyJournal
    from app.retrieval.review_authority import ReadOnlyReviewAuthority
    from app.retrieval.vector_store import ChromaVectorStore
    from app.serving import ServingGenerationManager
    ledger_path = Path(service.authority_dir) / "reviews.sqlite"
    journal_path = Path(service.authority_dir) / "policy.sqlite"
    if not ledger_path.is_file() or not journal_path.is_file():
        raise RuntimeError("existing review ledger and policy journal required")
    configuration = GenerationConfig.model_validate_json(Path(service.generation_config).read_bytes())
    if encoder is None:
        from app.embeddings.qwen import QwenEmbeddings
        encoder = QwenEmbeddings(Path(service.qwen_artifact))
    if budget is None:
        from app.ingestion.evidence import QwenTokenBudget
        budget = QwenTokenBudget(Path(service.qwen_artifact))
    vector_store = vector_store or ChromaVectorStore(mode=service.vector_mode,
                                                     dimension=configuration.embedding_dimension)
    manager = ServingGenerationManager(Path(service.index_root), configuration=configuration, embeddings=encoder,
                                vector_store=vector_store, ledger=ReadOnlyReviewAuthority(ledger_path),
                                journal=SQLitePolicyJournal(journal_path, journal_id=service.journal_id),
                                journal_id=service.journal_id)
    generator = build_provider_generator(settings, client_factory=client_factory)
    config = AnswerConfig(retrieval_k=service.retrieval_k, retrieval_max_tokens=service.retrieval_max_tokens,
                          max_prompt_tokens=service.max_prompt_tokens)
    return GovernedAnswerRuntime(manager, generator, config=config, cache=GovernedResponseCache(),
                                 token_counter=budget.count)


def create_service_app():
    from app.config import Settings
    from app.http_contracts import HttpConfig
    from app.main import create_app
    settings, service = Settings(), ServiceSettings()
    runtime = build_runtime(service, settings)
    # Warm the verified artifact load and review validation before serving.
    if not runtime.manager.readiness():
        raise RuntimeError("active generation is not ready")
    return create_app(runtime=runtime, settings=settings,
                      http_config=HttpConfig(workers=service.http_workers, max_queue=service.http_queue,
                                             allowed_origins=service.allowed_origins))

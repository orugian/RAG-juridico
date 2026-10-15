"""Operator-composed HTTP service over an already promoted governed generation.

``uvicorn app.service:create_service_app --factory`` composes, explicitly from the
operator's environment/.env, the accepted parts: GenerationManager (P4) over the
existing ledger/journal, local Qwen encoder, embedded Chroma, OpenRouter
generator (P6) restricted to Zero Data Retention routing, the P7 app and the
reviewer workbench (/ui, /wb/api) with local request tracing.

With ``RAG_DATA_ROOT`` the published generation is read from
``<data_root>/current.json`` (written by ``app.publish``); otherwise the explicit
RAG_INDEX_ROOT/RAG_AUTHORITY_DIR/RAG_GENERATION_CONFIG paths are used.

Nothing is built, promoted, repaired or downloaded here. Missing or inconsistent
authorities fail at startup; the LLM only selects reviewed units (extractive).
"""
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
# OpenRouter routing restriction: only endpoints with zero data retention and
# no data collection may serve a request; otherwise OpenRouter fails it.
ZDR_PROVIDER_PREFERENCES = {"zdr": True, "data_collection": "deny"}


class ServiceSettings(BaseSettings):
    model_config = {"env_prefix": "RAG_", "env_file": _PROJECT_ROOT / ".env", "extra": "ignore"}
    data_root: Path | None = None
    index_root: Path | None = None
    authority_dir: Path | None = None
    generation_config: Path | None = None
    journal_id: str | None = None
    qwen_artifact: Path | None = None
    workbench: Path | None = None
    vector_mode: Literal["embedded"] = "embedded"
    # One shared key for the whole team in the MVP: the per-credential rate
    # limit must cover everyone. Workers mostly wait on the LLM (I/O); the
    # encoder itself is serialized by its own lock.
    http_workers: int = Field(default=3, ge=1, le=16)
    http_queue: int = Field(default=6, ge=0, le=64)
    http_rate_requests: int = Field(default=120, ge=1, le=10000)
    allowed_origins: tuple[str, ...] = ()
    # Real contracts have long canonical units; the P6 default (10 units within
    # 4,096 tokens) abstains on most real questions instead of truncating.
    retrieval_k: int = Field(default=6, ge=1, le=100)
    retrieval_max_tokens: int = Field(default=10000, ge=1)
    max_prompt_tokens: int = Field(default=14000, ge=1)
    publication: str | None = None

    @model_validator(mode="after")
    def resolve_paths(self):
        if self.data_root is not None:
            from app.publish import QWEN_DIR, read_current
            current = read_current(self.data_root)
            if current is None:
                raise ValueError("data_root_without_published_generation")
            pub = Path(self.data_root) / "pubs" / current["pub"]
            self.publication = current["pub"]
            self.index_root = self.index_root or pub / "indexes"
            self.authority_dir = self.authority_dir or pub / "authority"
            self.generation_config = self.generation_config or pub / "bundle" / "configuration.json"
            self.journal_id = self.journal_id or current["journal_id"]
            self.qwen_artifact = self.qwen_artifact or Path(self.data_root) / "models" / QWEN_DIR
            self.workbench = self.workbench or Path(self.data_root) / "workbench.sqlite"
        missing = [name for name in ("index_root", "authority_dir", "generation_config", "journal_id", "qwen_artifact")
                   if getattr(self, name) is None]
        if missing:
            raise ValueError("service_paths_missing:" + ",".join(missing))
        return self


def openrouter_zdr_client(model: str, settings):
    from langchain_openai import ChatOpenAI
    if not settings.openai_base_url.rstrip("/").startswith("https://openrouter.ai/api"):
        raise RuntimeError("ZDR routing is enforced only for the OpenRouter API")
    return ChatOpenAI(model=model, api_key=settings.openai_api_key, base_url=settings.openai_base_url,
                      temperature=0, timeout=settings.request_timeout_seconds, max_retries=0,
                      extra_body={"provider": dict(ZDR_PROVIDER_PREFERENCES)})


def build_runtime(service: ServiceSettings, settings, *, encoder=None, budget=None, vector_store=None,
                  client_factory=openrouter_zdr_client, workbench=None):
    from app.answer_runtime import GovernedAnswerRuntime
    from app.cache import GovernedResponseCache
    from app.generation import AnswerConfig
    from app.generators import build_provider_generator
    from app.retrieval.generation_contracts import GenerationConfig
    from app.retrieval.policy import SQLitePolicyJournal
    from app.retrieval.review_authority import ReadOnlyReviewAuthority
    from app.serving import ServingGenerationManager, ServingVectorStore
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
    vector_store = vector_store or ServingVectorStore(mode=service.vector_mode,
                                                      dimension=configuration.embedding_dimension)
    if workbench is not None:
        from app.workbench.tracing import time_queries
        encoder = time_queries(encoder)
    manager = ServingGenerationManager(Path(service.index_root), configuration=configuration, embeddings=encoder,
                                       vector_store=vector_store, ledger=ReadOnlyReviewAuthority(ledger_path),
                                       journal=SQLitePolicyJournal(journal_path, journal_id=service.journal_id),
                                       journal_id=service.journal_id)
    generator = build_provider_generator(settings, client_factory=client_factory)
    config = AnswerConfig(retrieval_k=service.retrieval_k, retrieval_max_tokens=service.retrieval_max_tokens,
                          max_prompt_tokens=service.max_prompt_tokens)
    if workbench is not None:
        from app.workbench.tracing import TracingRuntime
        return TracingRuntime(manager, generator, workbench=workbench, config=config,
                              cache=GovernedResponseCache(), token_counter=budget.count)
    return GovernedAnswerRuntime(manager, generator, config=config, cache=GovernedResponseCache(),
                                 token_counter=budget.count)


def compose(service: ServiceSettings, settings, runtime, workbench=None):
    from app.http_contracts import HttpConfig, authenticate
    from app.main import create_app
    api = create_app(runtime=runtime, settings=settings,
                     http_config=HttpConfig(workers=service.http_workers, max_queue=service.http_queue,
                                            rate_requests=service.http_rate_requests,
                                            allowed_origins=service.allowed_origins))
    if workbench is None:
        return api
    from app.workbench.api import WorkbenchDispatcher, create_workbench_app
    from app.workbench.tracing import EvaluationRunner
    evaluation = EvaluationRunner(runtime, lambda: runtime.authenticate_epoch(
        authenticate(settings, settings.api_secret_key.get_secret_value())))
    panel = create_workbench_app(runtime=runtime, settings=settings, workbench=workbench, evaluation=evaluation,
                                 data_root=service.data_root, pub=service.publication)
    return WorkbenchDispatcher(api, panel)


def create_service_app():
    from app.config import Settings
    settings, service = Settings(), ServiceSettings()
    workbench = None
    if service.workbench is not None:
        from app.workbench.store import WorkbenchStore
        workbench = WorkbenchStore(service.workbench)
    runtime = build_runtime(service, settings, workbench=workbench)
    # Warm the verified artifact load and review validation before serving.
    if not runtime.manager.readiness():
        raise RuntimeError("active generation is not ready")
    return compose(service, settings, runtime, workbench)

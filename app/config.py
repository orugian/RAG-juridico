"""Centralized config using pydanthic-settings. This is a patern that we gonna use in every production python app
    Use pydantic-settings for validated environment variables    It will load variables from .env file and validate them
"""
from pathlib import Path
from typing import Literal
from pydantic import SecretStr, Field, model_validator
from pydantic_settings import BaseSettings
from functools import lru_cache

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    # LLM Providers
    openai_api_key: SecretStr = SecretStr("")
    openai_base_url: str = "https://openrouter.ai/api/v1"
    provider_policy_approved: bool = False
    primary_llm_model: str = "qwen/qwen3.8-flash"
    fallback_llm_model: str = "deepseek/deepseek-v4.1-flash"

    #LangSmith
    langsmith_tracing_v2: bool = False
    langsmith_project: str = "AndradeAdvogados"
    langsmith_api_key: SecretStr = SecretStr("")

    # Application
    app_env: Literal["development", "test", "production"] = "development"
    api_secret_key: SecretStr = SecretStr("")
    operator_api_secret_key: SecretStr = SecretStr("")
    previous_api_secret_key: SecretStr = SecretStr("")
    previous_key_valid_until: str | None = None
    development_auth_bypass: bool = False
    log_level: str = "INFO"
    rate_limit: str = "20/minute"
    cache_ttl_seconds: int = Field(default=300, ge=1, le=3600)
    max_retries: int = Field(default=3, ge=0, le=5)
    request_timeout_seconds: float = Field(default=30, gt=0, le=30)
    max_input_tokens: int = Field(default=12000, ge=1)
    max_output_tokens: int = Field(default=2000, ge=1)
    max_request_cost_usd: float = Field(default=0.10, gt=0)
    max_concurrent_requests: int = Field(default=5, ge=1, le=100)
    index_dir: Path = _PROJECT_ROOT / "data" / "indices"
    review_db_path: Path = _PROJECT_ROOT / "data" / "reviews" / "reviews.sqlite3"
    staging_dir: Path = _PROJECT_ROOT / "data" / "staging"
    embedding_backend: Literal["unconfigured", "local", "hash"] = "unconfigured"
    embedding_model: str | None = None
    embedding_revision: str | None = None
    embedding_dimension: int | None = Field(default=None, gt=0)
    embedding_artifact_dir: Path | None = None
    embedding_max_input_tokens: int = Field(default=1024, ge=1, le=32768)
    embedding_max_batch_size: int = Field(default=2, ge=1, le=16)
    embedding_max_batch_tokens: int = Field(default=2048, ge=1, le=32768)
    embedding_cpu_threads: int = Field(default=6, ge=1, le=16)
    chroma_mode: Literal["embedded", "server"] = "embedded"
    chroma_host: str = "127.0.0.1"
    chroma_port: int = Field(default=8001, ge=1, le=65535)
    parser_timeout_seconds: float = Field(default=120, gt=0, le=600)
    max_parser_processes: int = Field(default=1, ge=1, le=4)

    # M-Files (M4Law) - fonte documental do RAG
    mfiles_base_url: str = "https://andrade.cloudvault.m-files.com/REST"
    mfiles_vault_guid: str = ""
    mfiles_username: str = ""
    mfiles_password: SecretStr = SecretStr("")
    mfiles_timeout_seconds: float = 60.0
    mfiles_sync_classes: list[str] = [
        "Contrato",
        "Acordo",
        "Acordo Extrajudicial",
        "Proposta / Orçamento",
        "Documento",
    ]
    raw_data_dir: Path = _PROJECT_ROOT / "data" / "raw"

    # Curadoria do corpus (Etapa 1) - classificador Jev via OpenRouter Decisions API
    curation_dir: Path = _PROJECT_ROOT / "data" / "curation"
    jev_model: str = "typesafe/jev-1.13"
    jev_decisions_url: str = "https://openrouter.ai/api/alpha/decisions"
    # Snapshot sobre o qual os limiares da curadoria foram calibrados. Resposta de outro snapshot
    # vira opinião consultiva (não decide sozinha) até nova calibração.
    jev_calibrated_snapshot: str = "typesafe/jev-1.13-20260917"

    model_config = {"env_file": _PROJECT_ROOT / ".env", "extra": "ignore"}

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @model_validator(mode="after")
    def validate_runtime_profile(self):
        if self.is_production and (self.embedding_backend == "hash" or self.development_auth_bypass):
            raise ValueError("Produção não admite embedding hash ou bypass de autenticação")
        if self.embedding_backend == "local" and not all((self.embedding_model, self.embedding_revision, self.embedding_dimension)):
            raise ValueError("Embedding local exige modelo, revisão e dimensão")
        return self

@lru_cache
def get_settings() -> Settings:
    """Cached settings instance - loaded once, reused everywhere"""
    return Settings()

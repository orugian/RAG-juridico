"""Centralized config using pydanthic-settings. This is a patern that we gonna use in every production python app
    Use pydantic-settings for validated environment variables    It will load variables from .env file and validate them
"""
from pathlib import Path
from pydantic_settings import BaseSettings
from functools import lru_cache

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    # LLM Providers
    openai_api_key: str
    primary_llm_model: str = "qwen/qwen3.8-flash"
    fallback_llm_model: str = "deepseek/deepseek-v4.1-flash"

    #LangSmith
    langsmith_tracing_v2: bool = True
    langsmith_project: str = "AndradeAdvogados"
    langsmith_api_key: str = ""

    # Application
    app_env: str = "development"
    log_level: str = "INFO"
    rate_limit: str = "20/minute"
    cache_ttl_seconds: int = 300
    max_retries: int = 3

    model_config = {"env_file": _PROJECT_ROOT / ".env", "extra": "ignore"}

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

@lru_cache
def get_settings() -> Settings:
    """Cached settings instance - loaded once, reused everywhere"""
    return Settings()
    
    # Vector Database
    pinecone_api_key: str
    pinecone_index_name: str

    # Redis
    


""" Comando de verificação usando pydanthic-settings

uv run python -c "
from app.config import get_settings
settings = get_settings()
print(f'Environment: {settings.app_env}')
print(f'Primary model: {settings.primary_llm_model}')
print(f'Rate limit: {settings.rate_limit}')
print(f'Cache TTL: {settings.cache_ttl_seconds}s')
print(f'Max retries: {settings.max_retries}')
print(f'Is production: {settings.is_production}')
print('Config loaded successfully!')
"
"""



"""Injectable candidate generators. Output is never validated here and no network exists at import."""
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from langchain_core.messages import BaseMessage

from app.config import get_settings
from app.contracts import CitationUnit
from app.telemetry import assert_callback_boundary, error_category
from app.terms import absent_terms, content_terms


class GeneratorUnavailable(RuntimeError):
    """Technical failure only; carries a category, never question, contract or provider text."""

    def __init__(self, message: str = "generator_unavailable", *, category: str = "service_error"):
        super().__init__(message)
        self.category = category


class GeneratorOutputInvalid(ValueError):
    """The provider answered, but not with usable text; distinct from any technical failure."""


@dataclass(frozen=True)
class GeneratorRequest:
    messages: tuple[BaseMessage, ...]
    question: str
    question_item_ids: tuple[str, ...]
    units: tuple[CitationUnit, ...]
    timeout_seconds: float


class CandidateGenerator(Protocol):
    name: str

    def generate(self, request: GeneratorRequest) -> object: ...


class DeterministicSyntheticGenerator:
    """Offline double that counts shared question terms; not a quality baseline."""
    name = "deterministic-synthetic-v1"

    def __init__(self, *, max_units: int = 3):
        self.max_units = max_units

    def generate(self, request: GeneratorRequest) -> dict:
        total = len(content_terms(request.question))
        scored = sorted(((total - len(absent_terms(request.question, [unit.verbatim_text, unit.contract_title])), unit.unit_id)
                         for unit in request.units), key=lambda item: (-item[0], item[1]))
        chosen = [unit_id for score, unit_id in scored if score > 0][:self.max_units]
        return {"action": "select" if chosen else "abstain",
                "selections": [{"unit_id": unit_id, "question_item_ids": list(request.question_item_ids)} for unit_id in chosen],
                "approved_fact_ids": [], "clarification_code": None}


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(block if isinstance(block, str) else block.get("text", "")
                       for block in content
                       if isinstance(block, str) or (isinstance(block, dict) and block.get("type") == "text"
                                                     and isinstance(block.get("text"), str)))
    raise GeneratorOutputInvalid("generator_output_unsupported")


class LangChainCandidateGenerator:
    def __init__(self, client, *, name: str):
        assert_callback_boundary(client)
        self.client = client
        self.name = name

    def generate(self, request: GeneratorRequest) -> str:
        assert_callback_boundary(self.client)
        timeout = max(request.timeout_seconds, 0.001) if request.timeout_seconds is not None else None
        try:
            try:
                response = self.client.invoke(list(request.messages), timeout=timeout)
            except TypeError:
                response = self.client.invoke(list(request.messages))
        except Exception as error:
            category = error_category(error)
        else:
            return _text(getattr(response, "content", response))
        raise GeneratorUnavailable(category=category)


def _chat_openai(model: str, settings):
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=model, api_key=settings.openai_api_key, base_url=settings.openai_base_url, temperature=0,
                      timeout=settings.request_timeout_seconds, max_retries=0)


def build_provider_generator(settings=None, *, fallback: bool = False, client_factory=None) -> LangChainCandidateGenerator:
    settings = settings or get_settings()
    if not settings.provider_policy_approved:
        raise RuntimeError("Provider data policy has not been approved")
    if not settings.openai_api_key.get_secret_value():
        raise RuntimeError("Provider credential is not configured")
    model = settings.fallback_llm_model if fallback else settings.primary_llm_model
    return LangChainCandidateGenerator((client_factory or _chat_openai)(model, settings), name=f"provider:{model}")


__all__ = ["CandidateGenerator", "DeterministicSyntheticGenerator", "GeneratorOutputInvalid", "GeneratorRequest", "GeneratorUnavailable",
           "LangChainCandidateGenerator", "build_provider_generator"]

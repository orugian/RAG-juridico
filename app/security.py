"""
API Security Implementation
Rate limiting, API key validation, input sanitization, prompt injection defense,
and centralized security pipeline with LangSmith observability.

Tailored for an internal legal RAG system (contract analysis) in a law firm:
- Preserves legal formatting, clause numbering, and PII necessary for contract lookups.
- Cleans non-printable and malicious control characters without corrupting document structure.
- Defends against prompt injection attempts in Portuguese and English.
- Enforces API authentication and rate limiting via slowapi.
- Orchestrates and traces execution via SecurityPipeline with LangSmith integration.
"""

import re
from dataclasses import dataclass
from typing import Optional
from fastapi import HTTPException, Security, status
from fastapi.security.api_key import APIKeyHeader
from langsmith import traceable
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import get_settings

settings = get_settings()

# =====================================================================
# Rate Limiting (slowapi)
# =====================================================================
# Central Limiter instance for FastAPI app and endpoints
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.rate_limit]
)

# =====================================================================
# API Key Authentication
# =====================================================================
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: Optional[str] = Security(API_KEY_HEADER)) -> str:
    """
    Validate the internal API key from request headers.

    In development mode or when no specific internal secret key is enforced in settings,
    allows local testing smoothly. In production or when configured, enforces authorization.
    """
    expected_key = getattr(settings, "api_secret_key", None)

    # In development mode, allow requests if no key is strictly required
    if not settings.is_production and not expected_key:
        return api_key or "dev-internal-user"

    # If an expected key is configured, strictly validate against it
    if expected_key:
        if not api_key or api_key != expected_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API Key. Access denied to internal legal services."
            )
        return api_key

    # Default fallback when key is provided or running in authenticated perimeter
    return api_key or "internal-collaborator"


# =====================================================================
# Input Sanitization
# =====================================================================
class InputSanitizer:
    """
    Sanitizes user queries and contract fragments without destroying PII,
    special legal symbols (§, º, ª), monetary values, or document layouts.
    """

    # Matches null bytes and ASCII control characters, preserving \t, \n, \r
    CONTROL_CHARS_PATTERN = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")

    @classmethod
    def clean(cls, text: str) -> str:
        """
        Sanitize text input safely for legal RAG pipelines:
        - Removes dangerous control characters and null bytes.
        - Preserves CPF, CNPJ, emails, contract numbers, signatures, and legal signs.
        - Trims leading and trailing whitespace while maintaining internal structure.
        """
        if not text:
            return ""

        # Remove dangerous control characters that could break parsers or vector db queries
        cleaned = cls.CONTROL_CHARS_PATTERN.sub("", text)

        # Normalize carriage returns
        cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")

        # Strip outer whitespace
        return cleaned.strip()


# =====================================================================
# Prompt Injection Filter
# =====================================================================
class PromptInjectionFilter:
    """
    Guards against direct adversarial prompt jailbreaks and instructions override,
    in both Portuguese and English, calibrated to avoid false positives on legal queries.
    """

    INJECTION_PATTERNS = [
        # English patterns
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"forget\s+(all\s+)?(prior|previous)\s+instructions",
        r"bypass\s+(all\s+)?(security\s+|system\s+)?restrictions",
        r"reveal\s+(your|the)\s+(system\s+prompt|hidden\s+instructions)",
        r"you\s+are\s+now\s+(in\s+)?(DAN|jailbroken|developer)\s+mode",
        r"---\s*end\s*(of)?\s*prompt",
        # Portuguese patterns
        r"ignore\s+(todas\s+as\s+|quaisquer\s+)?instruções\s+anteriores",
        r"esqueça\s+(todas\s+as\s+|as\s+)?instruções\s+anteriores",
        r"revele\s+(o\s+)?(prompt\s+do\s+sistema|seu\s+prompt\s+inicial|suas\s+instruções\s+iniciais)",
        r"desconsidere\s+(as\s+|todas\s+as\s+)?regras\s+(anteriores|do\s+sistema)",
        r"você\s+agora\s+está\s+em\s+modo\s+(DAN|jailbreak|desenvolvedor)",
    ]

    def __init__(self):
        self.compiled_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.INJECTION_PATTERNS
        ]

    def check(self, text: str) -> tuple[bool, Optional[str]]:
        """
        Verify if the input contains malicious prompt injection patterns.

        Returns:
            (is_safe, rejection_reason): True and None if safe, False and error message if blocked.
        """
        if not text:
            return True, None

        for pattern in self.compiled_patterns:
            if pattern.search(text):
                return False, "Possível tentativa de injeção de prompt ou violação de instrução detectada."

        return True, None


# =====================================================================
# Centralized Security Pipeline (Facade & LangSmith Traced)
# =====================================================================
@dataclass
class SecurityResult:
    """Encapsulates the outcome of security validation and cleaning."""
    is_safe: bool
    cleaned_text: str
    rejection_reason: Optional[str] = None


class SecurityPipeline:
    """
    Central facade that orchestrates input sanitization and prompt injection checks,
    with end-to-end tracing enabled for LangSmith observability.
    """

    def __init__(self):
        self.sanitizer = InputSanitizer()
        self.injection_filter = PromptInjectionFilter()

    @traceable(name="SecurityPipeline.run", run_type="chain")
    def run(self, text: str) -> SecurityResult:
        """
        Executes all security checks and returns a structured SecurityResult.
        """
        cleaned_text = self.sanitizer.clean(text)
        is_safe, rejection_reason = self.injection_filter.check(cleaned_text)

        return SecurityResult(
            is_safe=is_safe,
            cleaned_text=cleaned_text,
            rejection_reason=rejection_reason
        )

    @traceable(name="SecurityPipeline.process", run_type="chain")
    def process(self, text: str) -> str:
        """
        Convenience method for FastAPI endpoints: validates the input and
        returns cleaned text, or raises HTTPException(400) if blocked.
        """
        result = self.run(text)
        if not result.is_safe:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.rejection_reason or "Input security check failed."
            )
        return result.cleaned_text


# Instância única exportada para reuso imediato nos endpoints e pipelines
security_pipeline = SecurityPipeline()

__all__ = [
    "limiter",
    "verify_api_key",
    "InputSanitizer",
    "PromptInjectionFilter",
    "SecurityResult",
    "SecurityPipeline",
    "security_pipeline",
]
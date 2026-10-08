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
import hashlib
import secrets
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional
from fastapi import HTTPException, Security, status
from fastapi.security.api_key import APIKeyHeader
from app.telemetry import safe_trace
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import Settings
from app.contracts import AccessContext

# Legacy helpers use process environment, never implicit .env on import.
# P7 supplies explicit application configuration for HTTP authentication.
settings = Settings(_env_file=None)

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


def _expiration(configuration):
    value = configuration.previous_key_valid_until
    if not value:
        return None
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Rotação exige prazo com timezone")
    return result


def validate_auth_configuration(configuration):
    """Call at API startup/readiness in P7; requests also enforce this gate now."""
    query = configuration.api_secret_key.get_secret_value()
    operator = configuration.operator_api_secret_key.get_secret_value()
    previous = configuration.previous_api_secret_key.get_secret_value()
    if not query:
        if configuration.development_auth_bypass and configuration.app_env == "development":
            return
        raise ValueError("Credencial de consulta ausente")
    keys = [key for key in (query, operator, previous) if key]
    if any(not re.fullmatch(r"[!-~]{32,256}", key) for key in keys):
        raise ValueError("Credencial deve conter 32 a 256 caracteres ASCII sem espaços")
    if len(keys) != len(set(keys)):
        raise ValueError("Credenciais devem ser distintas")
    if previous and _expiration(configuration) is None:
        raise ValueError("Rotação exige prazo explícito")


def _context(key, permissions):
    digest = hashlib.sha256(key.encode()).hexdigest()
    credential = f"credential-{digest}"
    scope = hashlib.sha256(f"internal_common:{credential}:{','.join(permissions)}:0".encode()).hexdigest()
    return AccessContext(principal_id=credential, credential_id=credential, permissions=permissions, policy_epoch=0, access_scope_digest=scope)


async def verify_api_key(api_key: Optional[str] = Security(API_KEY_HEADER)) -> AccessContext:
    try:
        validate_auth_configuration(settings)
    except ValueError:
        raise HTTPException(status_code=503, detail="Autenticação indisponível") from None
    if settings.development_auth_bypass and settings.app_env == "development" and not settings.api_secret_key.get_secret_value():
        return AccessContext(principal_id="synthetic-development", credential_id="synthetic-development", permissions=["query"], policy_epoch=0, access_scope_digest="synthetic")
    if not isinstance(api_key, str) or not api_key or len(api_key) > 256:
        raise HTTPException(status_code=401, detail="Credencial ausente ou inválida")
    provided = api_key.encode("utf-8")
    for configured, permissions in ((settings.api_secret_key.get_secret_value(), ["query"]), (settings.operator_api_secret_key.get_secret_value(), ["query", "operate"])):
        if configured and secrets.compare_digest(provided, configured.encode()):
            return _context(configured, permissions)
    previous = settings.previous_api_secret_key.get_secret_value()
    if previous and secrets.compare_digest(provided, previous.encode()) and _expiration(settings) > datetime.now(timezone.utc):
        return _context(previous, ["query"])
    raise HTTPException(status_code=401, detail="Credencial ausente ou inválida")


async def verify_operator_key(api_key: Optional[str] = Security(API_KEY_HEADER)) -> AccessContext:
    access = await verify_api_key(api_key)
    if "operate" not in access.permissions:
        raise HTTPException(status_code=403, detail="Operação não autorizada")
    return access


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

    @safe_trace(name="SecurityPipeline.run", run_type="chain")
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

    @safe_trace(name="SecurityPipeline.process", run_type="chain")
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

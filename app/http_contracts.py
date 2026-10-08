"""Closed, explicitly injected HTTP configuration and fixed public errors."""
from datetime import datetime, timezone
import hashlib
import re
import secrets
import json
import math

from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.contracts import AccessContext
from app.models import ErrorResponse


class HttpConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    allowed_origins: tuple[str, ...] = ()
    max_body_bytes: int = Field(default=65536, ge=1, le=1048576)
    workers: int = Field(default=5, ge=1, le=100)
    max_queue: int = Field(default=5, ge=0, le=100)
    control_workers: int = Field(default=2, ge=1, le=8)
    control_queue: int = Field(default=2, ge=0, le=16)
    deadline_seconds: float = Field(default=30, gt=0, le=30, allow_inf_nan=False)
    shutdown_seconds: float = Field(default=1, gt=0, le=30, allow_inf_nan=False)
    rate_requests: int = Field(default=20, ge=1, le=10000)
    perimeter_requests: int = Field(default=100, ge=1, le=10000)
    rate_window_seconds: float = Field(default=60, gt=0, le=3600, allow_inf_nan=False)
    rate_max_keys: int = Field(default=1024, ge=1, le=100000)

    @field_validator("allowed_origins")
    @classmethod
    def exact_origins(cls, values):
        from urllib.parse import urlsplit
        for value in values:
            parsed = urlsplit(value)
            if (not value.isascii() or any(ord(char) < 33 for char in value) or "*" in value
                    or parsed.scheme not in {"http", "https"} or not parsed.netloc
                    or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password):
                raise ValueError("Explicit origins required")
        return tuple(dict.fromkeys(values))


ERRORS = {
    400: ("invalid_request", "Requisição inválida."),
    401: ("unauthorized", "Credencial ausente ou inválida."),
    403: ("forbidden", "Operação não autorizada."),
    422: ("invalid_request", "Payload inválido."),
    429: ("rate_limited", "Limite de requisições excedido."),
    503: ("service_unavailable", "Serviço indisponível."),
    504: ("request_timeout", "Prazo da requisição excedido."),
    500: ("internal_error", "Erro interno."),
}
CODE_STATUS = {code: status for status, (code, _) in ERRORS.items() if status != 422}


class HttpFailure(Exception):
    def __init__(self, status: int):
        self.status = status if status in ERRORS else 500
        super().__init__(ERRORS[self.status][0])


def error_body(status: int, request_id: str) -> bytes:
    code, message = ERRORS[status]
    return ErrorResponse(code=code, message=message, request_id=request_id).model_dump_json().encode()


def validate_http_auth(settings) -> None:
    """Equivalent P1 gate with no global settings and no development bypass."""
    keys = [settings.api_secret_key.get_secret_value(), settings.operator_api_secret_key.get_secret_value(),
            settings.previous_api_secret_key.get_secret_value()]
    if settings.development_auth_bypass or not keys[0]:
        raise ValueError("auth_configuration_invalid")
    configured = [key for key in keys if key]
    if any(not re.fullmatch(r"[!-~]{32,256}", key) for key in configured) or len(set(configured)) != len(configured):
        raise ValueError("auth_configuration_invalid")
    if keys[2]:
        if expiration(settings) is None:
            raise ValueError("auth_configuration_invalid")


def expiration(settings):
    if not settings.previous_key_valid_until:
        return None
    value = datetime.fromisoformat(settings.previous_key_valid_until.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("auth_configuration_invalid")
    return value


def authenticate(settings, key: str | None) -> AccessContext:
    try:
        validate_http_auth(settings)
    except (ValueError, TypeError):
        raise HttpFailure(503) from None
    if not isinstance(key, str) or not key or len(key) > 256 or not key.isascii():
        raise HttpFailure(401)
    matches = []
    for configured, permissions in ((settings.api_secret_key.get_secret_value(), ["query"]),
                                    (settings.operator_api_secret_key.get_secret_value(), ["query", "operate"])):
        matches.append((bool(configured) and secrets.compare_digest(key, configured), permissions))
    previous = settings.previous_api_secret_key.get_secret_value()
    matches.append((bool(previous) and secrets.compare_digest(key, previous)
                    and expiration(settings) > datetime.now(timezone.utc), ["query"]))
    permissions = next((perms for matched, perms in matches if matched), None)
    if permissions is None:
        raise HttpFailure(401)
    credential = "credential-" + hashlib.sha256(key.encode()).hexdigest()
    scope = hashlib.sha256(f"internal_common:{credential}:{','.join(permissions)}:0".encode()).hexdigest()
    return AccessContext(principal_id=credential, credential_id=credential, permissions=permissions,
                         policy_epoch=0, access_scope_digest=scope)


def encode_control(operation, raw, *, request_id, local_metrics=None, pool_metrics=None) -> tuple[int, bytes]:
    """Project and serialize control output in the worker BEFORE final authority guard."""
    if operation == "ready":
        if raw is not True:
            return 503, error_body(503, request_id)
        result = {"status": "ready"}
    elif operation == "metrics":
        if not isinstance(raw, dict):
            raise HttpFailure(500)
        allowed = {"cache_hits", "cache_misses", "cache_entries", "cache_bytes", "cache_evictions", "cache_expirations",
                   "total_requests", "total_errors", "total_input_tokens", "total_output_tokens", "avg_latency_ms",
                   "answered_count", "abstained_count", "needs_clarification_count",
                   "latency_p50_ms", "latency_p95_ms", "latency_p99_ms"}
        result = {name: value for name, value in raw.items() if name in allowed and type(value) in (int, float)
                  and value >= 0 and math.isfinite(value)}
        for name in ("total_output_tokens", "provider_cost_usd"):
            value = raw.get(name)
            result[name] = value if type(value) in (int, float) and value >= 0 and math.isfinite(value) else None
        local = local_metrics or {}
        result.update(total_requests=local.get("total_requests", 0), total_errors=local.get("total_errors", 0),
                      avg_latency_ms=local.get("latency_total_ms", 0.0) / max(1, local.get("completed_requests", 0)))
        for namespace in ("query", "control"):
            for name in ("pending", "active", "queued"):
                value = (pool_metrics or {}).get(namespace, {}).get(name)
                if type(value) is int and value >= 0:
                    result[f"{namespace}_{name}"] = value
    elif operation == "policy":
        if not isinstance(raw, dict):
            raise HttpFailure(500)
        result = {name: raw[name] for name in ("ready", "authority_current") if type(raw.get(name)) is bool}
        for name in ("policy_epoch", "blocked_sources", "blocked_credentials", "blocked_families"):
            if type(raw.get(name)) is int and raw[name] >= 0:
                result[name] = raw[name]
    else:
        raise HttpFailure(400)
    return 200, json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def reauthenticate_context(settings, access: AccessContext) -> AccessContext:
    """Final credential/rotation gate without retaining the presented request key.

    Runtime owns epoch/scope recapture under ledger→policy guards; HTTP checks
    current configured credentials, expiration and permissions, not the initial
    epoch-zero digest. Call under commit and again immediately before ASGI start.
    """
    try:
        validate_http_auth(settings)
    except (ValueError, TypeError):
        raise HttpFailure(503) from None
    matched = None
    for secret in (settings.api_secret_key, settings.operator_api_secret_key, settings.previous_api_secret_key):
        candidate = secret.get_secret_value()
        if not candidate:
            continue
        credential = "credential-" + hashlib.sha256(candidate.encode()).hexdigest()
        if secrets.compare_digest(credential, access.credential_id):
            matched = candidate
    if matched is None:
        raise HttpFailure(401)
    current = authenticate(settings, matched)
    if (current.principal_id != access.principal_id or current.credential_id != access.credential_id
            or current.corpus_scope != access.corpus_scope
            or set(current.permissions) != set(access.permissions)):
        raise HttpFailure(403)
    return access

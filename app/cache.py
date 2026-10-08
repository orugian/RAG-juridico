import hashlib
import time
from typing import Optional 

class ResponseCache:
    """
    In-memory response cache with TTL (time-to-live).

    In production, replace this with Redis for:
    - Persistence across restarts
    - Shared cache across multiple instances
    - Built-in TTL management

    """

    def __init__(self, ttl_seconds: int = 300):
        self.ttl = ttl_seconds
        self._cache: dict[str, dict] = {}
        self._hits = 0
        self._misses = 0
    
    def make_key(self, query: str) -> str:
        """Create cache key from the normalized query."""
        normalized = query.lower().strip()
        return hashlib.sha256(normalized.encode()).hexdigest()

        """ 'Qual multa aplicada para o cliente X no contrato Y' and 'qUaL mUlTa aPlIcAdA pArA o ClIeNtE x No CoNtrAtO y' deve gerar a mesma chave
         mesmo se a API quiser verificar a chave, ou seja, independentemente de maiúsculas ou minúsculas
         e espaços em branco 
        """
    
    def get(self, query: str) -> Optional[dict]:
        """Get cached response for query if it exists and hasn't expired.
            Returns None on cache miss.
        """
        key = self.make_key(query)

        if key in self._cache:
            entry = self._cache[key]
            
            if time.time() - entry["timestamp"] < self.ttl:
                self._hits += 1
                return entry["response"]
            else:
                del self._cache[key] 
        
        self._misses += 1
        return None

    def set(self, query: str, response: str) -> None:
        """Store a response in the cache."""
        key = self.make_key(query)
        self._cache[key] = {
            "response": response,
            "timestamp": time.time(),
            "query": query
        }
    
    @property
    def stats(self) -> dict:
        """Return cache statistics."""
        total = self._hits + self._misses
        hit_rate = (self._hits / total) if total > 0 else 0.0
        return {
            "hits": self._hits,
            "misses": self._misses,
            "total": total,
            "hit_rate": hit_rate,
            "cached_entries": len(self._cache),
        }

    def clear(self) -> None:
        """Clear the cache."""
        self._cache.clear()
        self._hits = 0
        self._misses = 0


# The legacy helper above is intentionally independent of the governed P7 cache.
from dataclasses import dataclass, fields
import re
import json
import math
from collections import OrderedDict
from functools import wraps
from threading import Lock
from pydantic import BaseModel
from app.answer_contracts import AnswerAudit, AnswerOutcome, ResponsePayload


@dataclass(frozen=True)
class CacheIdentity:
    """Complete opaque identity built by the runtime, never by an HTTP caller."""

    question_sha256: str
    access_digest: str
    plan_digest: str
    generation_id: str
    versions_digest: str
    journal_id: str
    policy_epoch: int
    ledger_digest: str

    def __post_init__(self):
        for name in ("question_sha256", "access_digest", "plan_digest", "versions_digest", "ledger_digest"):
            value = getattr(self, name)
            if type(value) is not str or re.fullmatch(r"[a-f0-9]{64}", value) is None:
                raise ValueError("invalid_cache_identity")
        for name in ("generation_id", "journal_id"):
            value = getattr(self, name)
            if type(value) is not str or re.fullmatch(r"[A-Za-z0-9:_./-]{1,256}", value) is None:
                raise ValueError("invalid_cache_identity")
        if type(self.policy_epoch) is not int or self.policy_epoch < 0:
            raise ValueError("invalid_cache_identity")


def _validated_identity(value):
    if type(value) is not CacheIdentity:
        return None
    try:
        return CacheIdentity(**{field.name: getattr(value, field.name) for field in fields(CacheIdentity)})
    except (ValueError, TypeError, AttributeError):
        return None


def _contract_data(value):
    """Revalidate actual fields, including forged extras that model_dump drops."""
    if isinstance(value, BaseModel):
        data = dict(value.__dict__)
        data.update(value.__pydantic_extra__ or {})
        return {key: _contract_data(item) for key, item in data.items()}
    if isinstance(value, dict):
        return {key: _contract_data(item) for key, item in list(value.items())}
    if isinstance(value, (list, tuple)):
        return [_contract_data(item) for item in list(value)]
    return value


def _synchronized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return call


class GovernedResponseCache:
    """Bounded, thread-safe snapshots; a hit never grants release authorization.

    The trusted runtime supplies an identity freshly read from the authorities and
    must revalidate at commit, including after a hit. Epochs are monotonic only for
    retained journal states (at most max_entries); evicting state also evicts its
    outcomes. Ledger digests are not ordered: observing a different digest discards
    all prior outcomes for that journal. A stale caller may cause conservative
    misses or insert under its stale identity, never make that identity current in
    the actual authority. This cache does not detect authority outages itself.

    max_bytes counts UTF-8 identity/payload/audit snapshots, not Python overhead;
    entries and journal states separately bound object overhead. Snapshots retain
    the original request_id: the runtime rebuilds request correlation on each hit.
    clock must be non-reentrant; all public operations serialize under one lock.
    """

    def __init__(self, ttl_seconds=300, max_entries=128, max_bytes=8388608, clock=time.monotonic):
        try:
            valid_ttl = type(ttl_seconds) in (int, float) and math.isfinite(ttl_seconds) and ttl_seconds >= 0
        except OverflowError:
            valid_ttl = False
        if (not valid_ttl or type(max_entries) is not int or max_entries < 0
                or type(max_bytes) is not int or max_bytes < 0 or not callable(clock)):
            raise ValueError("invalid_cache_configuration")
        self._lock = Lock()
        self._enabled = ttl_seconds > 0 and max_entries > 0 and max_bytes > 0
        self._entries = OrderedDict()
        self._authorities = OrderedDict()
        self._max_entries, self._max_bytes = max_entries, max_bytes
        self._cached_bytes = 0
        self._hits = self._misses = 0
        self._ttl, self._clock = float(ttl_seconds), clock
        self._last_now = None

    def _now(self):
        try:
            now = self._clock()
            if type(now) not in (int, float):
                raise ValueError("invalid_cache_clock")
            now = float(now)
            if (not math.isfinite(now) or not math.isfinite(now + self._ttl)
                    or (self._last_now is not None and now < self._last_now)):
                raise ValueError("invalid_cache_clock")
            self._last_now = now
            return now
        except Exception:
            # Clock failure cannot prolong a cached authorization snapshot.
            self._entries.clear()
            self._cached_bytes = 0
            return None

    def _drop(self, key):
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._cached_bytes -= entry[2]

    def _expire(self, now):
        for key, (expiry, _, _) in list(self._entries.items()):
            if now >= expiry:
                self._drop(key)

    def _drop_journal(self, journal_id):
        for key in list(self._entries):
            if key.journal_id == journal_id:
                self._drop(key)

    def _observe(self, identity):
        current = self._authorities.get(identity.journal_id)
        if current is not None:
            if identity.policy_epoch < current[0]:
                return False
            if (identity.policy_epoch, identity.ledger_digest) != current:
                self._drop_journal(identity.journal_id)
        self._authorities[identity.journal_id] = (identity.policy_epoch, identity.ledger_digest)
        self._authorities.move_to_end(identity.journal_id)
        while len(self._authorities) > max(1, self._max_entries):
            journal_id, _ = self._authorities.popitem(last=False)
            self._drop_journal(journal_id)
        return True

    @_synchronized
    def get(self, identity: CacheIdentity) -> AnswerOutcome | None:
        identity = _validated_identity(identity)
        now = self._now()
        if now is not None:
            self._expire(now)
        entry = self._entries.get(identity) if now is not None and identity is not None and self._observe(identity) else None
        answer = entry[1] if entry is not None else None
        if answer is None:
            self._misses += 1
        else:
            self._hits += 1
            self._entries.move_to_end(identity)
        if answer is None:
            return None
        payload, audit = answer
        return AnswerOutcome(ResponsePayload.model_validate_json(payload), AnswerAudit.model_validate_json(audit))

    @staticmethod
    def _proof_matches(payload, audit):
        rendered = {unit.unit_id: unit for unit in audit.rendered_units}
        if (len(rendered) != len(audit.rendered_units)
                or set(rendered) != {item.unit_id for item in payload.proof}):
            return False
        if payload.status != "answered":
            return not audit.rendered_units
        selected = {item.unit_id for item in payload.proof if item.role == "selected"}
        if selected != set(audit.selected_unit_ids) or len(selected) != len(audit.selected_unit_ids):
            return False
        if set(audit.evidence_ids) != {chunk_id for unit in rendered.values() for chunk_id in unit.chunk_ids}:
            return False
        for item in payload.proof:
            unit, citation = rendered[item.unit_id], item.citation
            if (unit.role != item.role or unit.instrument_id != citation.contract_id
                    or citation.source_id not in unit.source_ids
                    or citation.evidence_id not in unit.chunk_ids
                    or unit.quote_sha256 != hashlib.sha256(citation.quote.encode()).hexdigest()):
                return False
        return True

    @_synchronized
    def set(self, identity: CacheIdentity, outcome: AnswerOutcome) -> bool:
        identity = _validated_identity(identity)
        if (not self._enabled or identity is None or type(outcome) is not AnswerOutcome
                or type(outcome.payload) is not ResponsePayload or type(outcome.audit) is not AnswerAudit):
            return False
        try:
            payload = ResponsePayload.model_validate(_contract_data(outcome.payload))
            audit = AnswerAudit.model_validate(_contract_data(outcome.audit))
            if (audit.question_sha256 != identity.question_sha256
                    or audit.access_scope_digest != identity.access_digest
                    or audit.generation_id != identity.generation_id
                    or payload.corpus_generation_id != identity.generation_id
                    or audit.request_id != payload.request_id
                    or audit.final_status != payload.status
                    or audit.final_reason_code != payload.reason_code
                    or audit.detail_code != payload.detail_code
                    or audit.error_code is not None
                    or payload.reason_code == "invalid_candidate"
                    or not self._proof_matches(payload, audit)
                    or audit.response_sha256 != hashlib.sha256(payload.response_text.encode()).hexdigest()):
                return False
            snapshot = (payload.model_dump_json().encode(), audit.model_dump_json().encode())
        except (ValueError, TypeError, AttributeError, RuntimeError):
            return False
        try:
            identity_bytes = json.dumps({field.name: getattr(identity, field.name) for field in fields(CacheIdentity)},
                                        sort_keys=True, separators=(",", ":")).encode()
        except (ValueError, TypeError, OverflowError):
            return False
        size = len(identity_bytes) + sum(map(len, snapshot))
        if size > self._max_bytes:
            return False
        now = self._now()
        if now is None:
            return False
        self._expire(now)
        if not self._observe(identity):
            return False
        self._drop(identity)
        self._entries[identity] = (now + self._ttl, snapshot, size)
        self._cached_bytes += size
        self._entries.move_to_end(identity)
        while len(self._entries) > self._max_entries or self._cached_bytes > self._max_bytes:
            self._drop(next(iter(self._entries)))
        return True

    @property
    @_synchronized
    def stats(self) -> dict:
        now = self._now()
        if now is not None:
            self._expire(now)
        total = self._hits + self._misses
        return {"hits": self._hits, "misses": self._misses, "total": total,
                "hit_rate": self._hits / total if total else 0.0,
                "cached_entries": len(self._entries), "cached_bytes": self._cached_bytes,
                "tracked_journals": len(self._authorities)}

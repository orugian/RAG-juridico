"""P7 composition of P6 and HTTP: prepared bytes, fresh pin, governed send boundary.

Nothing is loaded at import. The caller offloads this synchronous runtime to bounded
workers. No HTTP client input can supply AccessContext or the prepared proof.
"""
from dataclasses import dataclass, replace
from contextlib import nullcontext
import math
import hashlib
import json
import time
import uuid
import threading
import secrets
import hmac
from collections import deque

from app.generators import GeneratorUnavailable

from app.cache import CacheIdentity
from app.grounding import VALIDATOR_VERSION
from app.prompts import PROMPT_VERSION
from app.rendering import RENDERER_VERSION
from app.security import InputSanitizer

from app.answer_contracts import AnswerServiceError, AnswerAudit, AnswerOutcome, ValidationFinding
from app.http_contracts import HttpFailure
from app.contracts import AccessContext
from app.config import Settings
from app.telemetry import telemetry_configuration
from app.generation import AnswerConfig, AnswerRequest, GovernedAnswerService, _safe_text
from app.models import ChatResponse
from app.retrieval.hybrid import plan_query
from app.grounding import classify_authority_error
from app.rendering import controlled_text
from app.ingestion.closure import _Blocked, _components, _family_review, _relation_review, _relation_units
from app.retrieval.generations import PinnedGeneration
from app.retrieval.generation_store import _safe_path
from app.retrieval.generation_contracts import canonical_bytes


@dataclass(frozen=True)
class PreparedAnswer:
    response: ChatResponse
    body: bytes
    pinned: object
    ledger_digest: str
    deadline: float
    audit: AnswerAudit
    cache_origin_request_id: str | None


@dataclass(frozen=True)
class PreparedControl:
    operation: str
    body: bytes
    status_code: int
    access: AccessContext
    admission_policy: object
    ledger_digest: str | None
    deadline: float
    request_id: str
    integrity: str = ""


@dataclass(frozen=True)
class PreparedAdmission:
    """Sealed zero-proof documentary result; never a successful generation pin."""
    response: ChatResponse
    body: bytes
    audit: AnswerAudit
    access: AccessContext
    admission_policy: object
    ledger_digest: str
    receipt: tuple[str, ...]
    reason: str
    deadline: float
    integrity: str = ""


_ADMISSION_REASONS = frozenset({"family_unresolved", "relation_unresolved"})


class _BoundedGenerator:
    """Physical calls retain capacity even if P6 abandons its waiting thread."""

    def __init__(self, delegate, limit):
        self.delegate, self._slots = delegate, threading.BoundedSemaphore(limit)

    @property
    def name(self):
        return self.delegate.name

    def generate(self, request):
        if not self._slots.acquire(blocking=False):
            raise GeneratorUnavailable("service_error")
        try:
            return self.delegate.generate(request)
        finally:
            self._slots.release()


class GovernedAnswerRuntime:
    def __init__(self, manager, generator, *, config=None, cache=None, token_counter=None,
                 max_generator_calls=5):
        if type(max_generator_calls) is not int or not 1 <= max_generator_calls <= 100:
            raise ValueError("invalid_generator_concurrency")
        self.manager, self.generator = manager, generator
        self._bounded_generator = _BoundedGenerator(generator, max_generator_calls)
        self.config = config or AnswerConfig()
        self.cache, self.token_counter = cache, token_counter
        # Never share cached provider configuration between separately injected runtimes.
        self._runtime_version = uuid.uuid4().hex
        self._cache_secret = secrets.token_bytes(32)
        self._metrics_lock = threading.Lock()
        self._states = {"answered": 0, "abstained": 0, "needs_clarification": 0}
        self._input_tokens = 0
        self._latencies = deque(maxlen=1024)
        self._telemetry_settings = Settings(_env_file=None, langsmith_tracing_v2=False, langsmith_api_key="")

    def _cache_mac(self, identity, outcome):
        from dataclasses import asdict
        audit = outcome.audit.model_dump(mode="json")
        audit["findings"] = [finding for finding in audit["findings"]
                             if not finding["code"].startswith("cache_integrity:")]
        content = {"identity": asdict(identity), "payload": outcome.payload.model_dump(mode="json"), "audit": audit}
        return hmac.new(self._cache_secret, self._digest(content).encode(), hashlib.sha256).hexdigest()

    def _seal_cache(self, identity, outcome):
        findings = [finding for finding in outcome.audit.findings
                    if not finding.code.startswith("cache_integrity:")]
        if len(findings) >= 100:
            return None
        audit = AnswerAudit.model_validate(outcome.audit.model_dump(mode="python") | {"findings": findings + [
            ValidationFinding(code="cache_integrity:" + self._cache_mac(identity, outcome))]})
        return AnswerOutcome(outcome.payload, audit)

    def _verify_cache(self, identity, outcome, request_id):
        tokens = [finding.code.partition(":")[2] for finding in outcome.audit.findings
                  if finding.code.startswith("cache_integrity:")]
        if len(tokens) != 1 or not hmac.compare_digest(tokens[0], self._cache_mac(identity, outcome)):
            raise AnswerServiceError("service_unavailable", request_id=request_id)

    @staticmethod
    def _digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                         ensure_ascii=False).encode()).hexdigest()

    def _cache_identity(self, request, plan, pinned, ledger_digest):
        return CacheIdentity(
            question_sha256=hashlib.sha256(request.question.encode()).hexdigest(),
            access_digest=pinned.access.access_scope_digest,
            plan_digest=self._digest({"plan": plan.model_dump(mode="json"), "labels": request.item_labels}),
            generation_id=pinned.generation_id,
            versions_digest=self._digest({"runtime": self._runtime_version,
                "config": self.config.model_dump(mode="json"), "manifest": pinned.loaded.manifest_sha256,
                "generator": self.generator.name, "prompt": PROMPT_VERSION,
                "validator": VALIDATOR_VERSION, "renderer": RENDERER_VERSION}),
            journal_id=pinned.admission_policy.journal_id,
            policy_epoch=pinned.admission_policy.policy_epoch, ledger_digest=ledger_digest)

    def authenticate_epoch(self, access: AccessContext) -> AccessContext:
        try:
            access = AccessContext.model_validate(access.model_dump(mode="json"))
            snapshot = self.manager._policy()
        except Exception:
            raise AnswerServiceError("service_unavailable", request_id="req-auth") from None
        return self._access_at(access, snapshot, "req-auth")

    @staticmethod
    def _access_at(access, snapshot, request_id):
        if access.credential_id in snapshot.blocked_credential_ids:
            raise AnswerServiceError("forbidden", request_id=request_id)
        fields = access.model_dump(mode="json") | {"policy_epoch": snapshot.policy_epoch}
        fields.pop("access_scope_digest")
        digest = hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return AccessContext(**fields, access_scope_digest=digest)

    def _control_mac(self, prepared):
        content = {"domain": "p7-control-v1", "runtime": self._runtime_version,
                   "operation": prepared.operation, "status": prepared.status_code,
                   "body_sha256": hashlib.sha256(prepared.body).hexdigest(),
                   "access": prepared.access.model_dump(mode="json"),
                   "policy": prepared.admission_policy.model_dump(mode="json"),
                   "ledger_digest": prepared.ledger_digest, "deadline": prepared.deadline,
                   "request_id": prepared.request_id}
        return hmac.new(self._cache_secret, self._digest(content).encode(), hashlib.sha256).hexdigest()

    def prepare_control(self, operation, access, *, request_id, deadline, encode):
        try:
            with telemetry_configuration(self._telemetry_settings):
                if operation not in ("ready", "metrics", "policy"):
                    raise AnswerServiceError("invalid_request", request_id=request_id)
                if type(deadline) not in (int, float) or not math.isfinite(deadline):
                    raise AnswerServiceError("invalid_request", request_id=request_id)
                if time.monotonic() >= deadline:
                    raise AnswerServiceError("request_timeout", request_id=request_id)
                try:
                    snapshot = self.manager._policy()
                    access = AccessContext.model_validate(access.model_dump(mode="json"))
                except Exception:
                    raise AnswerServiceError("service_unavailable", request_id=request_id) from None
                access = self._access_at(access, snapshot, request_id)
                permission = "query" if operation == "ready" else "operate"
                if permission not in access.permissions:
                    raise AnswerServiceError("forbidden", request_id=request_id)
                ledger_digest = None
                if operation == "ready":
                    try:
                        ledger_digest = self.manager.ledger.state_digest()
                    except Exception:
                        pass  # Negative readiness remains useful with an unavailable ledger.
                    raw = self.readiness()
                elif operation == "metrics":
                    raw = self.metrics()
                else:
                    raw = self.policy_status(access)
                status, body = encode(raw)
                if type(status) is not int or status not in (200, 503) or type(body) is not bytes:
                    raise ValueError("invalid_control_encoding")
                if not isinstance(json.loads(body), dict):
                    raise ValueError("invalid_control_json")
                if operation == "ready" and status == 200:
                    if raw is not True or ledger_digest is None:
                        raise AnswerServiceError("service_unavailable", request_id=request_id)
                else:
                    ledger_digest = None
                if time.monotonic() >= deadline:
                    raise AnswerServiceError("request_timeout", request_id=request_id)
                prepared = PreparedControl(operation, body, status, access, snapshot,
                                           ledger_digest, deadline, request_id)
                return replace(prepared, integrity=self._control_mac(prepared))
        except AnswerServiceError as error:
            failure = AnswerServiceError(error.code, request_id=request_id, audit=error.audit)
        except HttpFailure:
            raise
        except Exception:
            failure = AnswerServiceError("internal_error", request_id=request_id)
        raise failure from None

    def commit_control(self, prepared, emit):
        with telemetry_configuration(self._telemetry_settings):
            return self._commit_control(prepared, emit)

    def _commit_control(self, prepared, emit):
        emitting = False
        try:
            if not hmac.compare_digest(prepared.integrity, self._control_mac(prepared)):
                raise AnswerServiceError("service_unavailable", request_id=prepared.request_id)
            ledger_guard = (self.manager.ledger.hold_current() if prepared.ledger_digest is not None
                            else nullcontext())
            with ledger_guard, self.manager.journal.hold_snapshot() as current:
                if prepared.access.credential_id in current.blocked_credential_ids:
                    raise AnswerServiceError("forbidden", request_id=prepared.request_id)
                if (current.journal_id != self.manager.journal_id or current != prepared.admission_policy
                        or (prepared.ledger_digest is not None
                            and self.manager.ledger.state_digest() != prepared.ledger_digest)):
                    raise AnswerServiceError("service_unavailable", request_id=prepared.request_id)
                if time.monotonic() >= prepared.deadline:
                    raise AnswerServiceError("request_timeout", request_id=prepared.request_id)
                emitting = True
                emit(prepared.body)
        except (HttpFailure, AnswerServiceError):
            raise
        except Exception:
            raise AnswerServiceError("internal_error" if emitting else "service_unavailable",
                                     request_id=prepared.request_id) from None

    def readiness(self):
        try:
            return bool(self.manager.readiness())
        except Exception:
            return False

    def policy_status(self, access):
        access = self.authenticate_epoch(access)
        if "operate" not in access.permissions:
            raise AnswerServiceError("forbidden", request_id="req-policy")
        try:
            current = self.manager._policy()
        except Exception:
            raise AnswerServiceError("service_unavailable", request_id="req-policy") from None
        if access.credential_id in current.blocked_credential_ids:
            raise AnswerServiceError("forbidden", request_id="req-policy")
        return {"policy_epoch": current.policy_epoch, "authority_current": True,
                "blocked_sources": len(current.blocked_source_ids),
                "blocked_credentials": len(current.blocked_credential_ids),
                "blocked_families": len(current.blocked_family_ids)}

    def metrics(self):
        stats = self.cache.stats if self.cache is not None else {}
        result = {"cache_hits": stats.get("hits", 0), "cache_misses": stats.get("misses", 0),
                  "cache_entries": stats.get("cached_entries", 0), "cache_bytes": stats.get("cached_bytes", 0)}
        with self._metrics_lock:
            samples = sorted(self._latencies)
            result.update({f"{state}_count": count for state, count in self._states.items()})
            result["total_input_tokens"] = self._input_tokens
        result.update(total_output_tokens=None, provider_cost_usd=None)
        if samples:
            import math
            for quantile in (50, 95, 99):
                result[f"latency_p{quantile}_ms"] = samples[max(0, math.ceil(len(samples)*quantile/100)-1)]
        return result

    def _admission_pointer(self):
        return _safe_path(self.manager.root / "active.json").read_bytes()

    def _admission_receipt(self, loaded):
        manifest = loaded.manifest
        raw = self._admission_pointer()
        pointer = json.loads(raw)
        if (raw != canonical_bytes(pointer) or pointer.get("generation_id") != manifest.generation_id
                or pointer.get("manifest_sha256") != loaded.manifest_sha256):
            raise ValueError("admission_active_pointer_changed")
        return (str(_safe_path(self.manager.root).resolve()), manifest.generation_id, loaded.manifest_sha256,
                loaded.artifacts.bundle.fingerprint, manifest.configuration.fingerprint,
                manifest.relation_registry_digest, hashlib.sha256(raw).hexdigest())

    def _admission_mac(self, prepared):
        content = {"domain": "p8-admission-v1", "runtime": self._runtime_version,
                   "body_sha256": hashlib.sha256(prepared.body).hexdigest(),
                   "response": prepared.response.model_dump(mode="json"),
                   "audit": prepared.audit.model_dump(mode="json"),
                   "access": prepared.access.model_dump(mode="json"),
                   "policy": prepared.admission_policy.model_dump(mode="json"),
                   "ledger": prepared.ledger_digest, "receipt": prepared.receipt,
                   "reason": prepared.reason, "deadline": prepared.deadline}
        return hmac.new(self._cache_secret, self._digest(content).encode(), hashlib.sha256).hexdigest()

    def _negative_admission(self, loaded, access, policy, reason, *, held_policy=None):
        """Recognize documentary state only after all source/registry checks.

        Replaying the negative check never grants a pin or any source content.
        Do not call _active/_policy here: commit invokes this under both leases.
        """
        if reason not in _ADMISSION_REASONS:
            raise ValueError("admission_reason_not_supported")
        bundle = loaded.artifacts.bundle
        registry = self.manager.ledger.relation_state_digest()
        if registry != loaded.manifest.relation_registry_digest:
            raise ValueError("admission_registry_changed")
        sources = {source.source_id: source for source in bundle.sources}
        allowed = set(sources) - set(policy.blocked_source_ids)
        if not allowed:
            raise ValueError("admission_no_authorized_sources")
        self.manager._reviews(bundle, source_ids=allowed)
        units = {unit.unit_id: unit for unit in bundle.units}
        components = _components({source.instrument_id for source in sources.values()}, bundle.relations)
        for component in components:
            resolutions = [resolution for resolution in bundle.resolutions
                           if bundle.family_review_sources.get(resolution.family_id) in sources
                           and sources[bundle.family_review_sources[resolution.family_id]].instrument_id in component]
            relevant = [relation for relation in bundle.relations if relation.from_instrument_id in component]
            if (len(resolutions) != 1 or resolutions[0].material_risk_source_ids
                    or resolutions[0].registry_digest != registry
                    or set(resolutions[0].relation_ids) != {relation.relation_id for relation in relevant}):
                raise ValueError("admission_family_identity_invalid")
            try:
                _family_review(component, relevant, resolutions, bundle.family_review_sources,
                               sources, self.manager.ledger, {})
            except _Blocked as error:
                if str(error) != "family_unresolved" or resolutions[0].state not in {"pending", "conflicted"}:
                    raise
            for relation in relevant:
                try:
                    _relation_review(relation, bundle.relation_review_sources, sources, self.manager.ledger, {}, {})
                    if relation.state == "approved":
                        _relation_units(relation, units)
                except _Blocked as error:
                    if str(error) != "relation_unresolved" or relation.state not in {"proposed", "conflicted"}:
                        raise
        negative = PinnedGeneration(self.manager, loaded, access, policy, "linked_instruments")
        try:
            negative._allowed_sources(held_policy=held_policy)
        except _Blocked as error:
            if type(error) is _Blocked and str(error) == reason:
                return
            raise
        raise ValueError("admission_is_no_longer_negative")

    def _prepare_admission(self, request, plan, access, reason, *, thread_id, request_id, deadline, start):
        try:
            policy = self.manager._access(access, "query")
            ledger_digest = self.manager.ledger.state_digest()
            loaded = self.manager._active()  # full storage/config/backend identity, not a fallback pin
            self._negative_admission(loaded, access, policy, reason)
            if (self.manager._policy() != policy or self.manager.ledger.state_digest() != ledger_digest):
                raise ValueError("admission_authority_changed")
            if time.monotonic() >= deadline:
                raise AnswerServiceError("request_timeout", request_id=request_id)
            kind, code, detail = classify_authority_error(_Blocked(reason))
            if (kind, code) != ("abstained", "unresolved_relation"):
                raise ValueError("admission_classification_invalid")
            text = controlled_text(code, question=request.question)
            response = ChatResponse(response=text, thread_id=thread_id, request_id=request_id,
                                    status="abstained", reason_code=code, corpus_generation_id=loaded.manifest.generation_id,
                                    processing_time_ms=(time.monotonic()-start)*1000, cached=False, model_used=None)
            audit = AnswerAudit(request_id=request_id, question_sha256=hashlib.sha256(request.question.encode()).hexdigest(),
                generation_id=loaded.manifest.generation_id, query_plan_version=plan.version,
                retrieval_config_version=loaded.manifest.configuration.configuration_version,
                relation_registry_version="v1", access_scope_digest=access.access_scope_digest,
                evidence_scope=plan.evidence_scope, reference_date=plan.reference_date,
                prompt_version=PROMPT_VERSION, validator_version=VALIDATOR_VERSION, renderer_version=RENDERER_VERSION,
                retrieval_status="blocked", retrieval_reason_code=reason, final_status="abstained", final_reason_code=code,
                detail_code=detail, response_sha256=hashlib.sha256(text.encode()).hexdigest(),
                elapsed_ms=response.processing_time_ms)
            prepared = PreparedAdmission(response, response.model_dump_json().encode(), audit, access, policy,
                                         ledger_digest, self._admission_receipt(loaded), reason, deadline)
            return replace(prepared, integrity=self._admission_mac(prepared))
        except AnswerServiceError:
            raise
        except Exception:
            self.authenticate_epoch(access)  # a concurrent credential revoke remains 403, not a semantic 200
            raise AnswerServiceError("service_unavailable", request_id=request_id) from None

    def _commit_admission(self, prepared, emit, *, stream):
        emitting = False
        try:
            if not hmac.compare_digest(prepared.integrity, self._admission_mac(prepared)):
                raise AnswerServiceError("service_unavailable", request_id=prepared.response.request_id)
            if time.monotonic() >= prepared.deadline:
                raise AnswerServiceError("request_timeout", request_id=prepared.response.request_id)
            # Full fresh storage/backend verification must precede the non-reentrant policy lease.
            loaded = self.manager._active()
            body = b"event: answer\ndata: " + prepared.body + b"\n\n" if stream else prepared.body
            with self.manager.ledger.hold_current(), self.manager.journal.hold_snapshot() as current:
                if prepared.access.credential_id in current.blocked_credential_ids:
                    raise AnswerServiceError("forbidden", request_id=prepared.response.request_id)
                if (current.journal_id != self.manager.journal_id or current != prepared.admission_policy
                        or self.manager.ledger.state_digest() != prepared.ledger_digest
                        # Cheap canonical active-pointer stamp under the publication leases:
                        # a promotion after the fresh _active read must not emit an old receipt.
                        or self._admission_receipt(loaded) != prepared.receipt):
                    raise AnswerServiceError("service_unavailable", request_id=prepared.response.request_id)
                self._negative_admission(loaded, prepared.access, current, prepared.reason, held_policy=current)
                if time.monotonic() >= prepared.deadline:
                    raise AnswerServiceError("request_timeout", request_id=prepared.response.request_id)
                emitting = True
                emit(body)
                with self._metrics_lock:
                    self._states["abstained"] += 1
                    self._latencies.append(prepared.response.processing_time_ms)
        except (HttpFailure, AnswerServiceError):
            raise
        except Exception:
            raise AnswerServiceError("internal_error" if emitting else "service_unavailable",
                                     request_id=prepared.response.request_id) from None

    def execute(self, request: AnswerRequest, access: AccessContext, *, thread_id, request_id, deadline):
        failure = None
        try:
            with telemetry_configuration(self._telemetry_settings):
                return self._execute(request, access, thread_id=thread_id, request_id=request_id, deadline=deadline)
        except AnswerServiceError as error:
            failure = AnswerServiceError(error.code, request_id=request_id, audit=error.audit)
        except Exception:
            failure = AnswerServiceError("internal_error", request_id=request_id)
        raise failure from None

    def _execute(self, request, access, *, thread_id, request_id, deadline):
        start = time.monotonic()
        if start >= deadline:
            raise AnswerServiceError("request_timeout", request_id=request_id)
        access = self.authenticate_epoch(access)
        if "query" not in access.permissions:
            raise AnswerServiceError("forbidden", request_id=request_id)
        request = AnswerRequest.model_validate(request.model_dump(mode="python"))
        question = InputSanitizer.clean(request.question)
        if not question or not _safe_text(question):
            raise AnswerServiceError("invalid_request", request_id=request_id)
        request = AnswerRequest(question=question, request_id=request_id, plan=request.plan,
                                item_labels=request.item_labels)
        plan = request.plan or plan_query(question)
        try:
            pinned = self.manager.pin(access, evidence_scope=plan.evidence_scope)
            ledger_digest = self.manager.ledger.state_digest()
        except _Blocked as error:
            if (type(error) is _Blocked and plan.evidence_scope == "linked_instruments"
                    and str(error) in _ADMISSION_REASONS):
                return self._prepare_admission(request, plan, access, str(error), thread_id=thread_id,
                                               request_id=request_id, deadline=deadline, start=start)
            raise AnswerServiceError("service_unavailable", request_id=request_id) from None
        except Exception:
            raise AnswerServiceError("service_unavailable", request_id=request_id) from None
        remaining = min(self.config.total_timeout_seconds, deadline - time.monotonic())
        if remaining <= 0:
            raise AnswerServiceError("request_timeout", request_id=request_id)
        config = AnswerConfig.model_validate(self.config.model_dump() | {"total_timeout_seconds": remaining})
        identity = self._cache_identity(request, plan, pinned, ledger_digest)
        outcome = self.cache.get(identity) if self.cache is not None else None
        cached = outcome is not None
        if cached:
            self._verify_cache(identity, outcome, request_id)
        if outcome is None:
            service = GovernedAnswerService(pinned, generator=self._bounded_generator, config=config,
                                           token_counter=self.token_counter)
            outcome = service.answer(AnswerRequest(question=request.question, request_id=request_id,
                                                  plan=plan, item_labels=request.item_labels))
            if self.cache is not None:
                sealed = self._seal_cache(identity, outcome)
                if sealed is not None:
                    self.cache.set(identity, sealed)
        if time.monotonic() >= deadline:
            raise AnswerServiceError("request_timeout", request_id=request_id)
        fields = outcome.payload.chat_fields() | {"request_id": request_id}
        response = ChatResponse(thread_id=thread_id, processing_time_ms=(time.monotonic()-start)*1000,
                                cached=cached, **fields)
        audit_fields = outcome.audit.model_dump(mode="python") | {
            "request_id": request_id, "elapsed_ms": (time.monotonic()-start)*1000}
        if cached:
            audit_fields["attempts"] = []
        audit = AnswerAudit.model_validate(audit_fields)
        if not cached and audit.prompt_tokens is not None:
            with self._metrics_lock:
                self._input_tokens += audit.prompt_tokens
        return PreparedAnswer(response, response.model_dump_json().encode(), pinned, ledger_digest, deadline,
                              audit, outcome.audit.request_id if cached else None)

    def commit(self, prepared, emit, *, stream=False):
        with telemetry_configuration(self._telemetry_settings):
            return self._commit(prepared, emit, stream=stream)

    def _commit(self, prepared, emit, *, stream=False):
        if type(prepared) is PreparedAdmission:
            return self._commit_admission(prepared, emit, stream=stream)
        body = prepared.body
        if stream:
            body = b"event: answer\ndata: " + body + b"\n\n"
        failure = None
        emitting = False
        try:
            with self.manager.ledger.hold_current(), self.manager.journal.hold_snapshot() as current:
                if prepared.pinned.access.credential_id in current.blocked_credential_ids:
                    raise AnswerServiceError("forbidden", request_id=prepared.response.request_id)
                if (current.journal_id != self.manager.journal_id
                        or current != prepared.pinned.admission_policy
                        or self.manager.ledger.state_digest() != prepared.ledger_digest):
                    raise AnswerServiceError("service_unavailable", request_id=prepared.response.request_id)
                prepared.pinned._allowed_sources(held_policy=current)
                if time.monotonic() >= prepared.deadline:
                    raise AnswerServiceError("request_timeout", request_id=prepared.response.request_id)
                emitting = True
                emit(body)
                with self._metrics_lock:
                    self._states[prepared.response.status] += 1
                    self._latencies.append(prepared.response.processing_time_ms)
                return
        except HttpFailure:
            raise
        except AnswerServiceError as error:
            failure = error
        except Exception:
            failure = AnswerServiceError("internal_error" if emitting else "service_unavailable",
                                         request_id=prepared.response.request_id)
        raise failure from None

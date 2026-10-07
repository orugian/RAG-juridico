"""Governed extractive answer service (P6): retrieve -> generate -> validate -> render over a pinned generation.

Dependencies are injected; nothing is constructed at import. The generator only selects offered
unit IDs, the backend resolves, validates and renders the proof, and every non-answer is a typed,
controlled outcome. Technical failures raise AnswerServiceError and are never turned into abstentions.
The graph has four nodes; the specs' fallback step runs inside `generate` (attempt stage `fallback`)
and the final response/audit assembly runs after the graph.
"""
import contextvars
import hashlib
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Annotated, Any, Callable, Mapping

from langgraph.graph import END, START, StateGraph
from pydantic import Field, field_validator
from typing_extensions import TypedDict

from app.answer_contracts import (
    AnswerAudit, AnswerOutcome, AnswerServiceError, AttemptRecord, ClarificationOption, MAX_CLARIFICATION_OPTIONS,
    RenderedUnitAudit, ResponsePayload, ValidationFinding,
)
from app.contracts import Identifier, QueryPlan, StrictContract
from app.generators import CandidateGenerator, GeneratorOutputInvalid, GeneratorRequest, GeneratorUnavailable
from app.grounding import (
    OfferedContext, ProofBlocked, VALIDATOR_VERSION, build_offered_context, classify_authority_error,
    classify_retrieval_outcome, resolve_proof, term_coverage, unsupported_items, validate_candidate,
)
from app.prompts import PROMPT_VERSION, build_messages
from app.rendering import RENDERER_VERSION, controlled_text, render_answer
from app.retrieval.hybrid import GovernedRetriever, plan_query
from app.security import InputSanitizer, PromptInjectionFilter
from app.telemetry import assert_callback_boundary, safe_trace

_INJECTION_FILTER = PromptInjectionFilter()
_PT_INJECTION = tuple(re.compile(pattern, re.IGNORECASE | re.MULTILINE) for pattern in (
    r"ignore\s+(?:todas\s+)?(?:as\s+)?instru[cç][oõ]es\s+(?:anteriores|acima)",
    r"desconsidere\s+(?:todas\s+)?(?:as\s+)?(?:regras|instru[cç][oõ]es)\s+(?:anteriores|acima|do\s+sistema)",
    r"revele\s+(?:o\s+)?(?:prompt|instru[cç][oõ]es)\s+(?:do\s+sistema|iniciais)",
    r"^\s*#{1,6}\s*system\b",
))


def _safe_text(text: str) -> bool:
    """Defense in depth for question/labels; regexes alone never guarantee protection."""
    return _INJECTION_FILTER.check(text)[0] and not any(pattern.search(text) for pattern in _PT_INJECTION)


class _CallTimeout(Exception):
    pass


class AnswerConfig(StrictContract):
    retrieval_k: int = Field(default=10, ge=1, le=100)
    retrieval_max_tokens: int = Field(default=4096, ge=1)
    max_prompt_tokens: int = Field(default=12000, ge=1)
    total_timeout_seconds: float = Field(default=30.0, gt=0, le=30)
    max_primary_attempts: int = Field(default=2, ge=1, le=3)
    fallback_approved: bool = False


class AnswerRequest(StrictContract):
    question: str = Field(min_length=1, max_length=10000)
    request_id: Identifier | None = None
    plan: QueryPlan | None = None
    item_labels: dict[Identifier, Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=dict, max_length=20)

    @field_validator("item_labels")
    @classmethod
    def single_line_safe_labels(cls, labels):
        if any(not label.isprintable() or not _safe_text(label) for label in labels.values()):
            raise ValueError("item_label_invalid")
        return labels


@dataclass(frozen=True)
class _Terminal:
    status: str
    reason_code: str
    detail_code: str | None = None
    clarification_code: str | None = None
    options: tuple[ClarificationOption, ...] = ()


@dataclass
class _Trace:
    request_id: str
    question_sha256: str
    plan: QueryPlan
    attempts: list[AttemptRecord] = field(default_factory=list)
    generator_names: list[str] = field(default_factory=list)
    findings: list[ValidationFinding] = field(default_factory=list)
    retrieval: Any = None
    candidate_action: str | None = None
    selected_unit_ids: list[str] = field(default_factory=list)
    prompt_tokens: int | None = None
    closure_tokens: int | None = None


class _State(TypedDict, total=False):
    trace: _Trace
    question: str
    labels: Mapping[str, str]
    context: OfferedContext
    raw: Any
    proof: Any
    absent: Any
    terminal: _Terminal
    outcome: AnswerOutcome
    generator_name: str
    deadline: float
    started: float


class GovernedAnswerService:
    def __init__(self, pinned, *, generator: CandidateGenerator, fallback_generator: CandidateGenerator | None = None,
                 config: AnswerConfig | None = None, retriever: GovernedRetriever | None = None,
                 token_counter: Callable[[str], int] | None = None, clock: Callable[[], float] = time.monotonic):
        self.pinned, self.generator, self.fallback_generator = pinned, generator, fallback_generator
        self.config, self._clock = config or AnswerConfig(), clock
        self.retriever = retriever or GovernedRetriever(pinned, token_counter=lambda text: self._count_tokens(text))
        self._count_tokens = token_counter or GovernedRetriever(pinned)._resolve_token_counter()
        self.graph = self._build_graph()

    def _build_graph(self):
        graph = StateGraph(_State)
        for name, node in (("retrieve", self._retrieve), ("generate", self._generate),
                           ("validate", self._validate), ("render", self._render)):
            graph.add_node(name, node)
        route = lambda target: (lambda state: END if state.get("terminal") or state.get("outcome") else target)
        graph.add_edge(START, "retrieve")
        graph.add_conditional_edges("retrieve", route("generate"), {"generate": "generate", END: END})
        graph.add_conditional_edges("generate", route("validate"), {"validate": "validate", END: END})
        graph.add_conditional_edges("validate", route("render"), {"render": "render", END: END})
        graph.add_edge("render", END)
        return graph.compile()

    def _deadline_check(self, trace, deadline):
        if self._clock() >= deadline:
            raise self._error("request_timeout", trace)

    def _error(self, code, trace, *, detail=None):
        return AnswerServiceError(code, request_id=trace.request_id,
                                  audit=self._audit(trace, "error", error_code=code, detail=detail))

    def _audit(self, trace, final_status, *, reason=None, detail=None, error_code=None, response_text=None, proof=None,
               elapsed=None):
        retrieval = trace.retrieval.retrieval_result if trace.retrieval is not None else None
        manifest = self.pinned.loaded.manifest
        rendered = []
        for unit in (proof.units if proof else ()):
            rendered.append(RenderedUnitAudit(
                unit_id=unit.unit_id, instrument_id=unit.instrument_id, role=proof.roles[unit.unit_id],
                source_ids=unit.source_ids, file_hashes={i.source_id: i.file_hash for i in unit.source_identities},
                block_ids=unit.block_ids, spans=unit.spans, chunk_ids=[c.chunk_id for c in proof.chunks[unit.unit_id]],
                review_record_id=unit.review_record_id,
                quote_sha256=hashlib.sha256(unit.verbatim_text.encode()).hexdigest()))
        return AnswerAudit(
            request_id=trace.request_id, question_sha256=trace.question_sha256, generation_id=self.pinned.generation_id,
            query_plan_version=trace.plan.version,
            retrieval_config_version=retrieval.retrieval_config_version if retrieval else manifest.configuration.configuration_version,
            relation_registry_version=retrieval.relation_registry_version if retrieval else "v1",
            access_scope_digest=self.pinned.access.access_scope_digest, evidence_scope=trace.plan.evidence_scope,
            reference_date=trace.plan.reference_date, prompt_version=PROMPT_VERSION, validator_version=VALIDATOR_VERSION,
            renderer_version=RENDERER_VERSION, generator_names=trace.generator_names, attempts=trace.attempts,
            retrieval_status=trace.retrieval.status if trace.retrieval is not None else "not_run",
            retrieval_reason_code=trace.retrieval.reason_code if trace.retrieval is not None else None,
            candidate_action=trace.candidate_action, selected_unit_ids=trace.selected_unit_ids, rendered_units=rendered,
            evidence_ids=[cid for item in rendered for cid in item.chunk_ids], findings=trace.findings,
            warnings=list(proof.warnings) if proof else [], final_status=final_status, final_reason_code=reason,
            detail_code=detail, error_code=error_code,
            response_sha256=hashlib.sha256(response_text.encode()).hexdigest() if response_text else None,
            prompt_tokens=trace.prompt_tokens, closure_tokens=trace.closure_tokens,
            elapsed_ms=elapsed)

    def _blocked_credentials(self):
        try:
            return set(self.pinned.manager.journal.snapshot().blocked_credential_ids)
        except Exception:
            return {self.pinned.access.credential_id}

    def _authority(self, error, trace):
        kind, code, detail = classify_authority_error(error)
        if code == "forbidden" and self.pinned.access.credential_id not in self._blocked_credentials():
            kind, code, detail = "error", "service_unavailable", "authority:policy_epoch_changed"
        if kind == "error":
            raise self._error(code, trace, detail=detail) from None
        return _Terminal("abstained", code, detail)

    def _options(self) -> tuple[ClarificationOption, ...]:
        allowed = self.pinned.allowed_sources()
        bundle = self.pinned.loaded.artifacts.bundle
        instruments = {s.instrument_id for s in bundle.sources if s.source_id in allowed}
        titles: dict[str, str] = {}
        for unit in bundle.units:
            if unit.instrument_id in instruments and unit.contract_title:
                titles.setdefault(unit.instrument_id, unit.contract_title)
        return tuple(ClarificationOption(instrument_id=key, contract_title=titles[key])
                     for key in sorted(titles)[:MAX_CLARIFICATION_OPTIONS])

    def _retrieve(self, state: _State):
        trace = state["trace"]
        try:
            result = self.retriever.retrieve(state["question"], query_plan=trace.plan, k=self.config.retrieval_k,
                                             max_tokens=self.config.retrieval_max_tokens)
        except Exception as error:
            return {"terminal": self._authority(error, trace)}
        trace.retrieval = result
        decision = classify_retrieval_outcome(result)
        if not decision.answerable:
            options: tuple[ClarificationOption, ...] = ()
            if decision.clarification_code and decision.clarification_code != "temporal_ambiguous":
                try:
                    options = self._options()
                except Exception as error:
                    return {"terminal": self._authority(error, trace)}
            return {"terminal": _Terminal(decision.status, decision.reason_code, decision.detail_code,
                                          decision.clarification_code, options)}
        context = build_offered_context(result, trace.plan)
        content, absent = term_coverage(state["question"], _AsOffered(context))
        if content and len(absent) == len(content):
            return {"terminal": _Terminal("abstained", "insufficient_evidence", "terms_absent")}
        return {"context": context}

    def _call(self, generator, request, remaining):
        """Run one blocking generator call under the remaining budget; an overrun call is abandoned."""
        outcome: dict = {}
        context = contextvars.copy_context()

        def run():
            try:
                outcome["value"] = context.run(generator.generate, request)
            except BaseException as error:
                outcome["error"] = error
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(max(remaining, 0.001))
        if worker.is_alive():
            raise _CallTimeout()
        if "error" in outcome:
            raise outcome["error"]
        return outcome["value"]

    def _attempt(self, generator, request, remaining):
        """('ok', raw) | ('timeout',) | ('invalid',) | ('failed', category); no exception text escapes."""
        try:
            return "ok", self._call(generator, request, remaining)
        except _CallTimeout:
            return ("timeout",)
        except GeneratorOutputInvalid:
            return ("invalid",)
        except GeneratorUnavailable as error:
            return "failed", error.category
        except Exception:
            return "failed", "service_error"

    def _generate(self, state: _State):
        trace, context, deadline = state["trace"], state["context"], state["deadline"]
        self._deadline_check(trace, deadline)
        messages = build_messages(state["question"], context.units, context.question_item_ids,
                                  item_labels=state["labels"], evidence_scope=context.evidence_scope)
        trace.prompt_tokens = self._count_tokens("\n".join(str(m.content) for m in messages))
        if trace.prompt_tokens > self.config.max_prompt_tokens:
            return {"terminal": _Terminal("abstained", "budget_exceeded", "prompt_over_budget")}
        stages = [("primary", self.generator)] * self.config.max_primary_attempts
        if self.config.fallback_approved and self.fallback_generator is not None:
            stages.append(("fallback", self.fallback_generator))
        for stage, generator in stages:
            self._deadline_check(trace, deadline)
            start = self._clock()
            request = GeneratorRequest(messages=tuple(messages), question=state["question"],
                                       question_item_ids=context.question_item_ids, units=context.units,
                                       timeout_seconds=max(deadline - start, 0.001))
            if generator.name not in trace.generator_names:
                trace.generator_names.append(generator.name)
            result = self._attempt(generator, request, deadline - start)
            latency = (self._clock() - start) * 1000
            if result[0] == "timeout" or (result[0] == "ok" and self._clock() >= deadline):
                trace.attempts.append(AttemptRecord(stage=stage, outcome="timeout", latency_ms=latency, error_category="timeout"))
                raise self._error("request_timeout", trace)
            if result[0] == "invalid":
                trace.attempts.append(AttemptRecord(stage=stage, outcome="invalid_output", latency_ms=latency,
                                                    error_category="validation_error"))
                trace.findings.append(ValidationFinding(code="candidate_schema_invalid"))
                return {"terminal": _Terminal("abstained", "invalid_candidate", "generator_output_unsupported")}
            if result[0] == "failed":
                trace.attempts.append(AttemptRecord(stage=stage, outcome="technical_failure", latency_ms=latency,
                                                    error_category=result[1]))
                continue
            trace.attempts.append(AttemptRecord(stage=stage, outcome="candidate", latency_ms=latency))
            return {"raw": result[1], "generator_name": generator.name}
        self._deadline_check(trace, deadline)
        raise self._error("service_unavailable", trace)

    def _validate(self, state: _State):
        trace, context = state["trace"], state["context"]
        validation = validate_candidate(state["raw"], context)
        trace.findings.extend(validation.findings)
        candidate = validation.candidate
        if candidate is None:
            return {"terminal": _Terminal("abstained", "invalid_candidate", "candidate_invalid")}
        trace.candidate_action = candidate.action
        if candidate.action == "abstain":
            return {"terminal": _Terminal("abstained", "insufficient_evidence", "generator_abstained")}
        if candidate.action == "clarify":
            options: tuple[ClarificationOption, ...] = ()
            if candidate.clarification_code != "temporal_ambiguous":
                try:
                    options = self._options()
                except Exception as error:
                    return {"terminal": self._authority(error, trace)}
            reason = "ambiguous_time" if candidate.clarification_code == "temporal_ambiguous" else "ambiguous_instrument"
            return {"terminal": _Terminal("needs_clarification", reason, "generator_clarified", candidate.clarification_code, options)}
        trace.selected_unit_ids = [s.unit_id for s in candidate.selections]
        try:
            proof = resolve_proof(candidate, context, self.pinned, trace.plan, count_tokens=self._count_tokens,
                                  max_tokens=self.config.retrieval_max_tokens)
        except ProofBlocked as blocked:
            trace.findings.append(blocked.finding)
            return {"terminal": _Terminal("abstained", blocked.public_reason, blocked.finding.code)}
        except Exception as error:
            return {"terminal": self._authority(error, trace)}
        trace.closure_tokens = proof.token_count
        content, absent = term_coverage(state["question"], proof)
        if content and len(absent) == len(content):
            trace.findings.append(ValidationFinding(code="terms_absent"))
            return {"terminal": _Terminal("abstained", "insufficient_evidence", "terms_absent")}
        unsupported = unsupported_items(state["question"], state["labels"], proof)
        if unsupported:
            trace.findings.extend(ValidationFinding(code="item_unsupported", subject_id=item) for item in unsupported)
            return {"terminal": _Terminal("abstained", "insufficient_evidence", "item_unsupported")}
        self._deadline_check(trace, state["deadline"])
        return {"proof": proof, "absent": absent}

    def _render(self, state: _State):
        trace, proof, start = state["trace"], state["proof"], state["started"]
        text, items = render_answer(proof, generation_id=self.pinned.generation_id, evidence_scope=trace.plan.evidence_scope,
                                    item_labels=state["labels"], absent=state["absent"])
        chunks = [chunk for unit in proof.units for chunk in proof.chunks[unit.unit_id]]
        try:
            finished = self.pinned.finish([chunk.chunk_id for chunk in chunks])
        except Exception as error:
            return {"terminal": self._authority(error, trace)}
        if [c.model_dump() for c in finished] != [c.model_dump() for c in chunks]:
            trace.findings.append(ValidationFinding(code="evidence_changed"))
            return {"terminal": _Terminal("abstained", "insufficient_evidence", "evidence_changed")}
        self._deadline_check(trace, state["deadline"])
        payload = ResponsePayload(
            request_id=trace.request_id, status="answered", corpus_generation_id=self.pinned.generation_id,
            response_text=text, proof=items, warnings=list(proof.warnings), model_used=state["generator_name"])
        elapsed = (self._clock() - start) * 1000
        return {"outcome": AnswerOutcome(payload, self._audit(trace, "answered", response_text=text, proof=proof, elapsed=elapsed))}

    def _controlled(self, terminal: _Terminal, trace: _Trace, question: str, start: float) -> AnswerOutcome:
        text = controlled_text(terminal.reason_code, question=question, options=terminal.options)
        payload = ResponsePayload(
            request_id=trace.request_id, status=terminal.status, reason_code=terminal.reason_code,
            detail_code=terminal.detail_code, corpus_generation_id=self.pinned.generation_id, response_text=text,
            clarification_code=terminal.clarification_code, clarification_options=list(terminal.options),
            model_used=trace.generator_names[-1] if any(a.outcome == "candidate" for a in trace.attempts) else None)
        audit = self._audit(trace, terminal.status, reason=terminal.reason_code, detail=terminal.detail_code,
                            response_text=text, elapsed=(self._clock() - start) * 1000)
        return AnswerOutcome(payload, audit)

    def _answer(self, request: AnswerRequest, box: dict) -> AnswerOutcome:
        start = self._clock()
        question = InputSanitizer.clean(request.question)
        plan = request.plan or plan_query(question)
        trace = box["trace"] = _Trace(request_id=request.request_id or f"req-{uuid.uuid4().hex}", plan=plan,
                                      question_sha256=hashlib.sha256(question.encode()).hexdigest())
        if not question or not _safe_text(question) or plan.evidence_scope != self.pinned.evidence_scope:
            raise self._error("invalid_request", trace)
        assert_callback_boundary(self.graph)
        state = self.graph.invoke({"trace": trace, "question": question, "labels": request.item_labels,
                                   "deadline": start + self.config.total_timeout_seconds, "started": start})
        if state.get("terminal"):
            return self._controlled(state["terminal"], trace, question, start)
        return state["outcome"]

    @safe_trace(name="chain")
    def answer(self, request: AnswerRequest) -> AnswerOutcome:
        box: dict = {}
        failure, unexpected = None, False
        try:
            return self._answer(request, box)
        except AnswerServiceError as error:
            failure = error
        except Exception:
            unexpected = True
        if unexpected:
            trace, audit = box.get("trace"), None
            if trace is not None:
                try:
                    audit = self._audit(trace, "error", error_code="internal_error")
                except Exception:
                    audit = None
            failure = AnswerServiceError("internal_error", request_id=trace.request_id if trace else
                                         (request.request_id or "req-unknown"), audit=audit)
        failure.__cause__ = failure.__context__ = None
        failure.__suppress_context__ = True
        raise failure


class _AsOffered:
    """Adapter exposing offered units to the term check with the ResolvedProof surface it needs."""

    def __init__(self, context: OfferedContext):
        self.units = context.units


__all__ = ["AnswerConfig", "AnswerRequest", "GovernedAnswerService"]

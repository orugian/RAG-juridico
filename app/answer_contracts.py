"""Closed contracts of the governed extractive answer (P6).

ResponsePayload carries only backend-rendered proof; AnswerAudit carries identifiers,
hashes and fixed codes, never the question, quotes, response text or model reasoning.
Schema validity is not legal adjudication, human approval or release authorization.
"""
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.contracts import Citation, Identifier, SourceSpan, StrictContract

PublicReason = Literal[
    "insufficient_evidence", "invalid_candidate", "unresolved_relation",
    "ambiguous_instrument", "ambiguous_time", "budget_exceeded",
]
ResponseStatus = Literal["answered", "abstained", "needs_clarification"]
ClarificationCode = Literal["instrument_ambiguous", "temporal_ambiguous", "selection_ambiguous", "question_scope"]
ServiceErrorCode = Literal["invalid_request", "forbidden", "service_unavailable", "request_timeout", "internal_error"]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Code = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}(:[A-Za-z0-9:_./-]{1,200})?$")]
MAX_CLARIFICATION_OPTIONS = 10
ABSTAINABLE = {"insufficient_evidence", "invalid_candidate", "unresolved_relation", "budget_exceeded"}


class ClarificationOption(StrictContract):
    instrument_id: Identifier
    contract_title: str = Field(min_length=1, max_length=500)


class ProofItem(StrictContract):
    unit_id: Identifier
    role: Literal["selected", "closure"]
    question_item_ids: list[Identifier] = Field(default_factory=list, max_length=20)
    relation_ids: list[Identifier] = Field(default_factory=list, max_length=40)
    citation: Citation


class ResponsePayload(StrictContract):
    request_id: Identifier
    status: ResponseStatus
    reason_code: PublicReason | None = None
    detail_code: Code | None = None
    corpus_generation_id: Identifier
    response_text: str = Field(min_length=1)
    proof: list[ProofItem] = Field(default_factory=list, max_length=200)
    warnings: list[Code] = Field(default_factory=list, max_length=100)
    clarification_code: ClarificationCode | None = None
    clarification_options: list[ClarificationOption] = Field(default_factory=list, max_length=MAX_CLARIFICATION_OPTIONS)
    model_used: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def status_invariants(self):
        if len({item.unit_id for item in self.proof}) != len(self.proof) or \
                len({item.citation.citation_id for item in self.proof}) != len(self.proof):
            raise ValueError("Prova repetida na resposta")
        if self.status == "answered":
            if (not any(item.role == "selected" for item in self.proof) or self.reason_code is not None
                    or self.clarification_code or self.clarification_options):
                raise ValueError("Resposta exige prova selecionada e nenhum motivo de abstenção")
            return self
        if self.proof or self.reason_code is None:
            raise ValueError("Resposta controlada exige motivo e nenhuma prova")
        if self.status == "abstained":
            if self.reason_code not in ABSTAINABLE or self.clarification_code or self.clarification_options:
                raise ValueError("Abstenção exige motivo próprio e nenhum esclarecimento")
        elif self.reason_code not in {"ambiguous_instrument", "ambiguous_time"} or self.clarification_code is None:
            raise ValueError("Esclarecimento exige código e motivo de ambiguidade")
        return self

    @property
    def citations(self) -> list[Citation]:
        return [item.citation for item in self.proof]

    def chat_fields(self) -> dict:
        """Fields accepted by the existing ChatResponse; HTTP envelope remains P7."""
        return {
            "response": self.response_text, "request_id": self.request_id, "status": self.status,
            "reason_code": self.reason_code, "citations": self.citations,
            "corpus_generation_id": self.corpus_generation_id, "model_used": self.model_used,
        }


class ValidationFinding(StrictContract):
    code: Code
    subject_id: Identifier | None = None


class AttemptRecord(StrictContract):
    stage: Literal["primary", "fallback"]
    outcome: Literal["candidate", "technical_failure", "invalid_output", "timeout", "skipped"]
    latency_ms: float = Field(ge=0, allow_inf_nan=False)
    error_category: Literal["timeout", "validation_error", "service_error"] | None = None


class RenderedUnitAudit(StrictContract):
    unit_id: Identifier
    instrument_id: Identifier
    role: Literal["selected", "closure"]
    source_ids: list[Identifier] = Field(min_length=1)
    file_hashes: dict[Identifier, Sha256]
    block_ids: list[Identifier] = Field(min_length=1)
    spans: list[SourceSpan] = Field(min_length=1)
    chunk_ids: list[Identifier] = Field(min_length=1)
    review_record_id: Identifier | None = None
    quote_sha256: Sha256


class AnswerAudit(StrictContract):
    audit_version: Literal["p6-audit-v1"] = "p6-audit-v1"
    request_id: Identifier
    question_sha256: Sha256
    generation_id: Identifier
    query_plan_version: Identifier
    retrieval_config_version: Identifier
    relation_registry_version: Identifier
    access_scope_digest: Identifier
    evidence_scope: Literal["original_text", "linked_instruments"]
    reference_date: date | None = None
    prompt_version: Identifier
    validator_version: Identifier
    renderer_version: Identifier
    generator_names: list[Identifier] = Field(default_factory=list, max_length=2)
    attempts: list[AttemptRecord] = Field(default_factory=list, max_length=20)
    retrieval_status: Literal["answered", "abstained", "blocked", "needs_clarification", "not_run"]
    retrieval_reason_code: Code | None = None
    candidate_action: Literal["select", "abstain", "clarify"] | None = None
    selected_unit_ids: list[Identifier] = Field(default_factory=list)
    rendered_units: list[RenderedUnitAudit] = Field(default_factory=list)
    evidence_ids: list[Identifier] = Field(default_factory=list)
    findings: list[ValidationFinding] = Field(default_factory=list, max_length=100)
    warnings: list[Code] = Field(default_factory=list, max_length=100)
    final_status: Literal["answered", "abstained", "needs_clarification", "error"]
    final_reason_code: PublicReason | None = None
    detail_code: Code | None = None
    error_code: ServiceErrorCode | None = None
    response_sha256: Sha256 | None = None
    prompt_tokens: int | None = Field(default=None, ge=0)
    closure_tokens: int | None = Field(default=None, ge=0)
    elapsed_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)


@dataclass(frozen=True)
class AnswerOutcome:
    payload: ResponsePayload
    audit: AnswerAudit


class AnswerServiceError(Exception):
    """Technical failure; distinct from abstention. Message is the fixed code only."""

    def __init__(self, code: ServiceErrorCode, *, request_id: str, audit: AnswerAudit | None = None):
        super().__init__(code)
        self.code, self.request_id, self.audit = code, request_id, audit

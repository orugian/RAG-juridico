"""Exact synthetic P8 comparison. Not a runner, legal judge or G5/G8 approval.

Declare the oracle before invoking the system. This module cannot establish when
an oracle was authored or whether a human reviewed it. No input content is exposed
in findings. Citation IDs are stable (P6: ``cit:`` + unit ID), so they are compared.
"""
from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
import hmac
import json
from secrets import token_bytes
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.answer_contracts import ABSTAINABLE, PublicReason, ResponseStatus
from app.contracts import Citation, Identifier
from app.models import ChatResponse

Status = ResponseStatus
Reason = PublicReason
_ABSTAINABLE = frozenset(ABSTAINABLE)
_AMBIGUOUS = frozenset({"ambiguous_instrument", "ambiguous_time"})


def _status_reason_valid(status: Status, reason: Reason | None) -> bool:
    """Mirror P6 ResponsePayload invariants, without changing HTTP contracts."""
    if status == "answered":
        return reason is None
    if status == "abstained":
        return reason in _ABSTAINABLE
    return status == "needs_clarification" and reason in _AMBIGUOUS

# In-process integrity only: not a durable signature, access control or a defense
# against hostile Python with module/key introspection. No environment/secrets IO.
_INTEGRITY_KEY = token_bytes(32)


def _seal(payload: str) -> str:
    return hmac.new(_INTEGRITY_KEY, payload.encode("utf-8"), sha256).hexdigest()


def _duplicate_proofs(citations: list[Citation]) -> bool:
    # One canonical presentation cannot be inflated by fresh runtime/chunk IDs.
    # Identical words at distinct instruments/locations/sources remain distinct.
    keys = [c.model_dump_json(exclude={"citation_id", "evidence_id"}) for c in citations]
    return (len(set(keys)) != len(citations)
            or len({c.evidence_id for c in citations}) != len(citations)
            or len({c.citation_id for c in citations}) != len(citations))


class _OracleInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    case_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
    expected_status: Status
    expected_reason: Reason | None
    expected_citations: list[Citation]
    expected_generation_id: Identifier
    expected_response: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def consistent_oracle(self):
        answered = self.expected_status == "answered"
        if not _status_reason_valid(self.expected_status, self.expected_reason):
            raise ValueError("invalid_expectation")
        if answered != bool(self.expected_citations) or answered != (self.expected_reason is None):
            raise ValueError("invalid_expectation")
        if not answered and self.expected_response is None:
            raise ValueError("invalid_expectation")
        if _duplicate_proofs(self.expected_citations):
            raise ValueError("invalid_expectation")
        return self


@dataclass(frozen=True, init=False, repr=False)
class E2EExpectation:
    """Independent literal oracle; returned citations are defensive copies.

    ``expected_response`` is mandatory for controlled cases. For answered cases
    it can optionally check an independently declared complete renderer template;
    without it only structured proof, action and generation are evaluated.
    """
    _snapshot: str

    def __init__(self, case_id: str, expected_status: Status, expected_reason: Reason | None,
                 expected_citations: list[Citation], expected_generation_id: str, *,
                 expected_response: str | None = None):
        try:
            if type(expected_citations) is not list or any(type(c) is not Citation for c in expected_citations):
                raise ValueError("invalid_expectation")
            data = _OracleInput.model_validate(dict(
                case_id=case_id, expected_status=expected_status, expected_reason=expected_reason,
                expected_citations=[c.model_dump(mode="python", warnings=False) for c in expected_citations],
                expected_generation_id=expected_generation_id, expected_response=expected_response), strict=True)
        except (ValueError, TypeError, AttributeError):
            raise ValueError("invalid_expectation") from None
        object.__setattr__(self, "_snapshot", data.model_dump_json())

    @property
    def case_id(self) -> str:
        return self._data().case_id

    @property
    def expected_status(self) -> Status:
        return self._data().expected_status

    @property
    def expected_reason(self) -> Reason | None:
        return self._data().expected_reason

    @property
    def expected_citations(self) -> list[Citation]:
        return self._data().expected_citations

    @property
    def expected_generation_id(self) -> str:
        return self._data().expected_generation_id

    @property
    def expected_response(self) -> str | None:
        return self._data().expected_response

    def _data(self) -> _OracleInput:
        return _OracleInput.model_validate_json(self._snapshot, strict=True)

    def __repr__(self) -> str:
        return "E2EExpectation(<restricted synthetic oracle>)"


@dataclass(frozen=True)
class E2EResult:
    """Content-free record bound to one oracle, valid only in this process.

    Frozen fields and a process-local seal prevent ordinary mutation/replacement
    from forging success. This is not a portable/independently signed attestation.
    """
    case_id: str
    findings: tuple[str, ...]
    expectation_digest: str
    expected_status: Status
    citations_emitted: int
    citations_correct: int
    _integrity: str = field(default="", repr=False)

    @property
    def passed(self) -> bool:
        return _valid_result(self) and not self.findings


def _record_payload(record) -> str:
    return json.dumps({k: v for k, v in asdict(record).items() if k != "_integrity"},
                      ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _valid_result(result: E2EResult) -> bool:
    try:
        return type(result) is E2EResult and hmac.compare_digest(result._integrity, _seal(_record_payload(result)))
    except (ValueError, TypeError, AttributeError):
        return False


def _make_result(case_id: str, findings: tuple[str, ...], digest: str, status: Status,
                 emitted: int, correct: int) -> E2EResult:
    record = E2EResult(case_id, findings, digest, status, emitted, correct)
    return replace(record, _integrity=_seal(_record_payload(record)))


def evaluate_response(expectation: E2EExpectation, response: ChatResponse) -> E2EResult:
    """Compare one response with an already declared synthetic expectation."""
    try:
        if type(expectation) is not E2EExpectation:
            raise ValueError("invalid_expectation")
        expected = expectation._data()
    except (ValueError, TypeError, AttributeError):
        raise ValueError("invalid_expectation") from None
    try:
        if type(response) is not ChatResponse:
            raise ValueError("response_invalid")
        actual = ChatResponse.model_validate(response.model_dump(mode="python", warnings=False), strict=True)
        if not _status_reason_valid(actual.status, actual.reason_code):
            raise ValueError("response_invalid")
    except (ValueError, TypeError, AttributeError):
        return _make_result(expected.case_id, ("response_invalid",), sha256(expectation._snapshot.encode()).hexdigest(),
                         expected.expected_status, 0, 0)
    findings = []
    if actual.status != expected.expected_status:
        findings.append("status_mismatch")
    if actual.reason_code != expected.expected_reason:
        findings.append("reason_mismatch")
    if actual.corpus_generation_id != expected.expected_generation_id:
        findings.append("generation_mismatch")
    if expected.expected_response is not None and actual.response != expected.expected_response:
        findings.append("response_text_mismatch")
    if len(actual.citations) != len(expected.expected_citations):
        findings.append("citation_count_mismatch")
    if _duplicate_proofs(actual.citations):
        findings.append("duplicate_proof")
    fields = {"citation_id": "citation_id", "contract_id": "instrument", "contract_title": "title",
              "document_version": "version", "parties": "parties", "location": "location",
              "quote": "quote", "source_id": "source", "evidence_id": "evidence"}
    for wanted, emitted in zip(expected.expected_citations, actual.citations):
        for field, code in fields.items():
            if getattr(wanted, field) != getattr(emitted, field):
                findings.append(code + "_mismatch")
    correct = sum(wanted == emitted for wanted, emitted in zip(expected.expected_citations, actual.citations))
    return _make_result(expected.case_id, tuple(dict.fromkeys(findings)),
                     sha256(expectation._snapshot.encode()).hexdigest(), expected.expected_status,
                     len(actual.citations), correct)


def equivalent_answers(a: ChatResponse, b: ChatResponse) -> bool:
    """Exact semantic equality; thread ID is correlation, not response content.

    Only cached, request_id, timestamp, processing_time_ms and thread_id are
    excluded. Invalid inputs are never equivalent, even to themselves.
    """
    try:
        if type(a) is not ChatResponse or type(b) is not ChatResponse:
            return False
        envelope = {"cached", "request_id", "timestamp", "processing_time_ms", "thread_id"}
        left = ChatResponse.model_validate(a.model_dump(mode="python", warnings=False), strict=True)
        right = ChatResponse.model_validate(b.model_dump(mode="python", warnings=False), strict=True)
        if (not _status_reason_valid(left.status, left.reason_code)
                or not _status_reason_valid(right.status, right.reason_code)
                or _duplicate_proofs(left.citations) or _duplicate_proofs(right.citations)):
            return False
        return left.model_dump(exclude=envelope) == right.model_dump(exclude=envelope)
    except (ValueError, TypeError, AttributeError):
        return False


@dataclass(frozen=True)
class E2EAggregate:
    """Synthetic gates only; empty strata are N/A, never inferred successes.

    The pass gate is sealed for in-process integrity, not portable attestation.
    G5/G8, human reserved quotas, legal quality and operational SLO remain outside
    this report regardless of the number of synthetic fixtures or repetitions.
    """
    expected_cases: int
    observed_cases: int
    evaluated_cases: int
    answered_cases: int
    controlled_cases: int
    answered_correct: int
    controlled_correct: int
    citations_expected: int
    citations_emitted: int
    citations_correct: int
    complete: bool
    findings: tuple[str, ...]
    origin: Literal["synthetic"] = "synthetic"
    human_acceptance: Literal[False] = False
    _integrity: str = field(default="", repr=False)

    @property
    def fidelity(self) -> float | None:
        return self.answered_correct / self.answered_cases if self.answered_cases else None

    @property
    def action_accuracy(self) -> float | None:
        return self.controlled_correct / self.controlled_cases if self.controlled_cases else None

    @property
    def complete_answer_accuracy(self) -> float | None:
        return self.fidelity

    @property
    def citation_fidelity(self) -> float | None:
        return self.citations_correct / self.citations_emitted if self.citations_emitted else None

    @property
    def passed(self) -> bool:
        try:
            intact = hmac.compare_digest(self._integrity, _seal(_record_payload(self)))
        except (ValueError, TypeError, AttributeError):
            return False
        return (intact and self.complete and not self.findings and self.expected_cases > 0
                and self.answered_correct + self.controlled_correct == self.expected_cases)


def aggregate_results(expectations: Sequence[E2EExpectation], results: Sequence[E2EResult]) -> E2EAggregate:
    """Require exactly one bound result for every declared case, and no others.

    Missing results stay in the expected-status denominators. Fidelity is exact
    complete answered cases / expected answered cases; action_accuracy is exact
    controlled cases / expected controlled cases (including literal safe text).
    Citation fidelity has its own emitted-proof denominator. These are synthetic
    diagnostics, not ACC-13 human relevance, G5/G8 or the reserved >=100 corpus.
    """
    findings = []
    if not isinstance(expectations, Sequence) or isinstance(expectations, (str, bytes)):
        findings.append("invalid_expectation_collection")
        expectations = ()
    if not isinstance(results, Sequence) or isinstance(results, (str, bytes)):
        findings.append("invalid_result_collection")
        results = ()
    cases = {}
    if not expectations:
        findings.append("empty_expectations")
    for expectation in expectations:
        if type(expectation) is not E2EExpectation:
            findings.append("invalid_expectation")
            continue
        try:
            data = expectation._data()
        except (ValueError, TypeError, AttributeError):
            findings.append("invalid_expectation")
            continue
        if data.case_id in cases:
            findings.append("duplicate_expectation")
        cases[data.case_id] = (data, sha256(expectation._snapshot.encode()).hexdigest())
    grouped = {}
    for result in results:
        if not _valid_result(result):
            findings.append("invalid_result")
            continue
        if result.case_id not in cases:
            findings.append("unexpected_result")
            continue
        grouped.setdefault(result.case_id, []).append(result)
    accepted = {}
    for case_id, (data, digest) in cases.items():
        records = grouped.get(case_id, [])
        if not records:
            findings.append("missing_result")
        elif len(records) != 1:
            findings.append("duplicate_result")
        elif records[0].expectation_digest != digest or records[0].expected_status != data.expected_status:
            findings.append("expectation_mismatch")
        else:
            accepted[case_id] = records[0]
    complete = not findings
    if any(not result.passed for result in accepted.values()):
        findings.append("case_failed")
    answered_cases = sum(data.expected_status == "answered" for data, _ in cases.values())
    controlled_cases = len(cases) - answered_cases
    report = E2EAggregate(
        expected_cases=len(expectations), observed_cases=len(results), evaluated_cases=len(accepted),
        answered_cases=answered_cases, controlled_cases=controlled_cases,
        answered_correct=sum(r.passed and r.expected_status == "answered" for r in accepted.values()),
        controlled_correct=sum(r.passed and r.expected_status != "answered" for r in accepted.values()),
        citations_expected=sum(len(data.expected_citations) for data, _ in cases.values()),
        citations_emitted=sum(r.citations_emitted for r in accepted.values()),
        citations_correct=sum(r.citations_correct for r in accepted.values()),
        complete=complete, findings=tuple(dict.fromkeys(findings)),
    )
    return replace(report, _integrity=_seal(_record_payload(report)))

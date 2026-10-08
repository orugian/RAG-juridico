"""Public behavior of the P7 governed cache, with synthetic validated P6 contracts."""
from dataclasses import FrozenInstanceError, replace
import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Thread, current_thread

import pytest

from app.answer_contracts import AnswerAudit, AnswerOutcome, AnswerServiceError, ResponsePayload, ProofItem, RenderedUnitAudit
from app.contracts import Citation, CitationLocation, CitationParty, SourceSpan
from app.cache import CacheIdentity, GovernedResponseCache


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def identity(**changes):
    return CacheIdentity(**(dict(
        question_sha256=digest("Qual o prazo?"), access_digest=digest("principal:synthetic"),
        plan_digest=digest("plan"), generation_id="gen:synthetic", versions_digest=digest("versions"),
        journal_id="journal:synthetic", policy_epoch=1, ledger_digest=digest("ledger:1"),
    ) | changes))


def outcome(key=None, **payload_changes):
    key = key or identity()
    payload = ResponsePayload(**(dict(
        request_id="request:1", status="abstained", reason_code="insufficient_evidence",
        corpus_generation_id=key.generation_id, response_text="Informação não localizada.",
    ) | payload_changes))
    audit = AnswerAudit(
        request_id=payload.request_id, question_sha256=key.question_sha256,
        generation_id=key.generation_id, query_plan_version="query-plan-v1",
        retrieval_config_version="retrieval:v1", relation_registry_version="relations:v1",
        access_scope_digest=key.access_digest, evidence_scope="original_text",
        prompt_version="prompt:v1", validator_version="validator:v1", renderer_version="renderer:v1",
        retrieval_status="abstained", final_status=payload.status, final_reason_code=payload.reason_code,
        response_sha256=digest(payload.response_text),
    )
    return AnswerOutcome(payload=payload, audit=audit)


def test_validated_outcome_roundtrip_and_aggregate_stats():
    cache = GovernedResponseCache()
    key, answer = identity(), outcome()
    assert cache.get(key) is None
    assert cache.set(key, answer) is True
    assert cache.get(key) == answer
    assert cache.stats["hits"] == 1
    assert cache.stats["misses"] == 1
    assert cache.stats["cached_entries"] == 1
    assert "Qual o prazo?" not in repr(cache.stats)


def test_input_and_returned_nested_models_are_independent_validated_snapshots():
    cache, key, answer = GovernedResponseCache(), identity(), outcome()
    assert cache.set(key, answer)
    answer.payload.warnings.append("mutated_input")
    answer.audit.warnings.append("mutated_audit")
    hit = cache.get(key)
    assert hit.payload.warnings == [] and hit.audit.warnings == []
    hit.payload.warnings.append("mutated_hit")
    hit.audit.warnings.append("mutated_hit")
    assert cache.get(key).payload.warnings == []
    assert cache.get(key).audit.warnings == []


@pytest.mark.parametrize("change", [
    "invalid_candidate", "forged_schema", "error", "status", "reason", "request", "hash",
    "question", "access", "generation", "payload_generation", "detail",
])
def test_rejects_unvalidated_or_mismatched_outcomes_without_caching(change):
    key, answer = identity(), outcome()
    payload_changes, audit_changes = {}, {}
    if change == "invalid_candidate":
        payload_changes["reason_code"] = "invalid_candidate"
        audit_changes["final_reason_code"] = "invalid_candidate"
    elif change == "forged_schema":
        payload_changes["status"] = "answered"  # model_copy does not validate required proof.
    elif change == "error":
        audit_changes["error_code"] = "internal_error"
    elif change == "status":
        audit_changes["final_status"] = "error"
    elif change == "reason":
        audit_changes["final_reason_code"] = "budget_exceeded"
    elif change == "request":
        audit_changes["request_id"] = "other:request"
    elif change == "hash":
        audit_changes["response_sha256"] = digest("different text")
    elif change == "question":
        audit_changes["question_sha256"] = digest("another question")
    elif change == "access":
        audit_changes["access_scope_digest"] = digest("other principal")
    elif change == "generation":
        audit_changes["generation_id"] = "other:generation"
    elif change == "payload_generation":
        payload_changes["corpus_generation_id"] = "other:generation"
    elif change == "detail":
        audit_changes["detail_code"] = "different_detail"
    forged = AnswerOutcome(answer.payload.model_copy(update=payload_changes), answer.audit.model_copy(update=audit_changes))
    cache = GovernedResponseCache()
    assert cache.set(key, forged) is False
    assert cache.get(key) is None
    assert cache.set(key, None) is False


@pytest.mark.parametrize("field,value", [
    ("question_sha256", "question text"), ("access_digest", "API key"),
    ("plan_digest", "bad"), ("versions_digest", "F" * 64), ("ledger_digest", "0" * 63),
    ("generation_id", ""), ("journal_id", "invalid space"),
    ("policy_epoch", -1), ("policy_epoch", True), ("policy_epoch", 1.5),
])
def test_identity_rejects_noncanonical_values(field, value):
    with pytest.raises(ValueError):
        identity(**{field: value})


def test_identity_is_frozen_complete_and_does_not_collapse_question_case():
    key = identity()
    with pytest.raises(FrozenInstanceError):
        key.policy_epoch = 2
    cache = GovernedResponseCache()
    assert cache.set(key, outcome(key))
    for field in ("question_sha256", "access_digest", "plan_digest", "versions_digest", "generation_id", "journal_id"):
        value = "other:opaque" if field in {"generation_id", "journal_id"} else digest(field)
        assert cache.get(replace(key, **{field: value})) is None
    assert cache.get(replace(key, question_sha256=digest("QUAL O PRAZO?"))) is None
    assert cache.get(key) is not None
    assert cache.get({"question": "raw"}) is None
    assert cache.set("bad identity", outcome()) is False


class Clock:
    def __init__(self):
        self.now = 100.0
        self.broken = False

    def __call__(self):
        if self.broken:
            raise RuntimeError("synthetic clock failure")
        return self.now


def test_ttl_uses_monotonic_clock_and_expires_at_exact_boundary():
    clock = Clock()
    cache, key = GovernedResponseCache(ttl_seconds=3, clock=clock), identity()
    assert cache.set(key, outcome(key))
    clock.now += 2.999
    assert cache.get(key) is not None
    clock.now += 0.001
    assert cache.get(key) is None
    assert cache.stats["cached_entries"] == 0
    assert cache.set(key, outcome(key))
    clock.now += 3
    assert cache.stats["cached_entries"] == 0


def test_lru_bounds_entries_and_get_refreshes_recency_not_ttl():
    clock = Clock()
    cache = GovernedResponseCache(ttl_seconds=10, max_entries=2, clock=clock)
    keys = [identity(question_sha256=digest(str(i))) for i in range(3)]
    assert cache.set(keys[0], outcome(keys[0]))
    assert cache.set(keys[1], outcome(keys[1]))
    clock.now += 5
    assert cache.get(keys[0]) is not None
    assert cache.set(keys[2], outcome(keys[2]))
    assert cache.get(keys[1]) is None
    assert cache.get(keys[0]) is not None
    assert cache.stats["cached_entries"] == 2
    clock.now += 5
    assert cache.get(keys[0]) is None
    assert cache.get(keys[2]) is not None


def test_byte_budget_counts_utf8_snapshots_and_replacements_without_leaks():
    key = identity()
    probe = GovernedResponseCache()
    assert probe.set(key, outcome(key))
    size = probe.stats["cached_bytes"]
    assert size > len(outcome().payload.response_text.encode("utf-8"))
    cache = GovernedResponseCache(max_bytes=size, max_entries=10)
    assert cache.set(key, outcome(key))
    assert cache.set(key, outcome(key))
    assert cache.stats["cached_bytes"] == size
    other = identity(question_sha256=digest("other"))
    assert cache.set(other, outcome(other))
    assert cache.get(key) is None and cache.get(other) is not None
    assert cache.stats["cached_bytes"] == size
    assert cache.set(other, outcome(other, response_text="é" * size)) is False
    assert cache.get(other).payload.response_text == "Informação não localizada."
    assert cache.stats["cached_bytes"] <= size
    assert GovernedResponseCache(max_bytes=size - 1).set(key, outcome(key)) is False


def test_new_policy_epoch_invalidates_all_scopes_in_journal_and_rejects_rollback():
    cache = GovernedResponseCache()
    old = identity()
    other_scope = replace(old, access_digest=digest("other principal"))
    independent = replace(old, journal_id="independent:journal")
    for key in (old, other_scope, independent):
        assert cache.set(key, outcome(key))
    new = replace(old, policy_epoch=2)
    assert cache.get(new) is None  # Even a miss observes the current authority.
    assert cache.stats["cached_entries"] == 1
    assert cache.get(independent) is not None
    assert cache.set(old, outcome(old)) is False
    assert cache.get(old) is None
    assert cache.get(other_scope) is None
    assert cache.set(new, outcome(new))
    assert cache.get(new) is not None


def test_changed_ledger_discards_old_results_before_lookup_without_granting_authority():
    cache, old = GovernedResponseCache(), identity()
    old_scope = replace(old, access_digest=digest("other principal"))
    assert cache.set(old, outcome(old))
    assert cache.set(old_scope, outcome(old_scope))
    current = replace(old, ledger_digest=digest("ledger:2"))
    assert cache.get(current) is None
    assert cache.stats["cached_entries"] == 0
    assert cache.get(old) is None
    assert cache.set(old, outcome(old))  # Caller can be stale; cache is not an authority.
    assert cache.get(current) is None   # Current identity invalidates the stale snapshot.
    assert cache.set(current, outcome(current))
    assert cache.get(current) is not None


@pytest.mark.parametrize("argument,value", [
    ("ttl_seconds", float("nan")), ("ttl_seconds", float("inf")), ("ttl_seconds", -1),
    ("ttl_seconds", True), ("max_entries", -1), ("max_entries", 1.5),
    ("max_entries", True), ("max_bytes", float("nan")), ("max_bytes", -1), ("clock", None),
])
def test_invalid_resource_configuration_is_rejected(argument, value):
    with pytest.raises(ValueError):
        GovernedResponseCache(**{argument: value})


@pytest.mark.parametrize("limits", [dict(ttl_seconds=0), dict(max_entries=0), dict(max_bytes=0)])
def test_zero_limits_disable_cache_without_claiming_insert(limits):
    cache = GovernedResponseCache(**limits)
    assert cache.set(identity(), outcome()) is False
    assert cache.get(identity()) is None
    assert cache.stats["cached_entries"] == 0


@pytest.mark.parametrize("failure", ["nan", "infinite", "backwards", "exception", "overflow"])
def test_unreliable_clock_discards_results_and_fails_closed(failure):
    clock, key = Clock(), identity()
    cache = GovernedResponseCache(clock=clock)
    assert cache.set(key, outcome(key))
    if failure == "exception":
        clock.broken = True
    else:
        clock.now = {"nan": float("nan"), "infinite": float("inf"), "backwards": 99,
                     "overflow": 10**1000}[failure]
    assert cache.get(key) is None
    assert cache.set(key, outcome(key)) is False
    assert cache.stats["cached_entries"] == 0
    assert cache.stats["cached_bytes"] == 0


def test_cache_operations_serialize_across_threads_and_release_after_completion():
    reader_clock_entered, release_reader, setter_started, setter_done = Event(), Event(), Event(), Event()

    def clock():
        if current_thread().name == "cache-reader":
            reader_clock_entered.set()
            assert release_reader.wait(5)
        return 100.0

    cache, key = GovernedResponseCache(clock=clock), identity()
    assert cache.set(key, outcome(key))
    results = []
    reader = Thread(target=lambda: results.append(cache.get(key)), name="cache-reader")

    def write():
        setter_started.set()
        results.append(cache.set(key, outcome(key)))
        setter_done.set()

    writer = Thread(target=write, name="cache-writer")
    reader.start()
    try:
        assert reader_clock_entered.wait(5)
        writer.start()
        assert setter_started.wait(5)
        assert not setter_done.wait(0.2)
    finally:
        release_reader.set()
        reader.join(5)
        if writer.ident is not None:
            writer.join(5)
    assert not reader.is_alive() and not writer.is_alive()
    assert setter_done.is_set()
    assert any(isinstance(result, AnswerOutcome) for result in results)
    assert cache.get(key) is not None


def answered_outcome():
    answer = outcome()
    quote = "Cláusula 1ª — Prazo de 30 dias, salvo acordo escrito."
    citation = Citation(
        citation_id="citation:synthetic", contract_id="instrument:synthetic",
        contract_title="Contrato sintético", document_version=1,
        parties=[CitationParty(name="Parte Sintética", role="contratante")],
        location=CitationLocation(label="Cláusula 1ª", clause="1ª"), quote=quote,
        source_id="source:synthetic", evidence_id="chunk:synthetic",
    )
    payload = ResponsePayload(
        request_id=answer.payload.request_id, status="answered", corpus_generation_id=identity().generation_id,
        response_text=quote, proof=[ProofItem(unit_id="unit:synthetic", role="selected", citation=citation)],
    )
    audit = answer.audit.model_copy(update=dict(
        retrieval_status="answered", final_status="answered", final_reason_code=None,
        response_sha256=digest(quote), selected_unit_ids=["unit:synthetic"], evidence_ids=["chunk:synthetic"],
        rendered_units=[RenderedUnitAudit(
            unit_id="unit:synthetic", instrument_id="instrument:synthetic", role="selected",
            source_ids=["source:synthetic"], file_hashes={"source:synthetic": digest("synthetic file")},
            block_ids=["block:synthetic"], spans=[SourceSpan(source_id="source:synthetic", block_id="block:synthetic", start=0, end=len(quote))],
            chunk_ids=["chunk:synthetic"], quote_sha256=digest(quote), review_record_id="review:synthetic",
        )],
    ))
    return AnswerOutcome(payload, audit)


@pytest.mark.parametrize("change", ["missing", "duplicate", "quote", "instrument", "role", "source", "chunk", "selected", "evidence"])
def test_answered_proof_must_match_audit_and_nested_citations_are_isolated(change):
    cache, key, answer = GovernedResponseCache(), identity(), answered_outcome()
    assert cache.set(key, answer)
    answer.payload.proof[0].citation.parties[0].name = "Mutated"
    hit = cache.get(key)
    assert hit.payload.proof[0].citation.parties[0].name == "Parte Sintética"
    hit.payload.proof[0].citation.location.clause = "changed"
    assert cache.get(key).payload.proof[0].citation.location.clause == "1ª"
    answer = answered_outcome()
    rendered = answer.audit.rendered_units[0]
    changes = {}
    if change == "missing":
        changes["rendered_units"] = []
    elif change == "duplicate":
        changes["rendered_units"] = [rendered, rendered]
    elif change == "selected":
        changes["selected_unit_ids"] = []
    elif change == "evidence":
        changes["evidence_ids"] = []
    else:
        field, value = {
            "quote": ("quote_sha256", digest("forged quote")), "instrument": ("instrument_id", "other:instrument"),
            "role": ("role", "closure"), "source": ("source_ids", ["other:source"]),
            "chunk": ("chunk_ids", ["other:chunk"]),
        }[change]
        changes["rendered_units"] = [rendered.model_copy(update={field: value})]
    forged = AnswerOutcome(answer.payload, answer.audit.model_copy(update=changes))
    assert cache.set(key, forged) is False


@pytest.mark.parametrize("where", ["payload", "audit", "citation"])
def test_forged_unknown_fields_are_rejected_not_silently_projected_away(where):
    cache, answer = GovernedResponseCache(), answered_outcome()
    if where == "payload":
        answer = replace(answer, payload=answer.payload.model_copy(update={"raw_model_text": "forged"}))
    elif where == "audit":
        answer = replace(answer, audit=answer.audit.model_copy(update={"raw_model_text": "forged"}))
    else:
        answer.payload.proof[0].citation = answer.payload.proof[0].citation.model_copy(update={"raw_model_text": "forged"})
    assert cache.set(identity(), answer) is False
    assert cache.get(identity()) is None


def test_concurrent_operations_preserve_budgets_accounting_and_aggregate_stats():
    cache = GovernedResponseCache(max_entries=8, max_bytes=10000)

    def worker(number):
        for step in range(50):
            key = identity(question_sha256=digest(f"synthetic:{number}:{step % 4}"))
            assert cache.set(key, outcome(key))
            hit = cache.get(key)
            if hit is not None:
                assert hit.audit.question_sha256 == key.question_sha256
            stats = cache.stats
            assert 0 <= stats["cached_entries"] <= 8
            assert 0 <= stats["cached_bytes"] <= 10000

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(worker, range(8)))
    stats = cache.stats
    assert stats["hits"] + stats["misses"] == 400
    assert stats["total"] == 400
    assert stats["hit_rate"] == stats["hits"] / 400
    stats["hits"] = -1
    assert cache.stats["hits"] >= 0


def test_authority_tracking_is_bounded_and_eviction_discards_related_snapshots():
    cache = GovernedResponseCache(max_entries=2)
    keys = [identity(journal_id=f"journal:{i}") for i in range(4)]
    for key in keys[:2]:
        assert cache.set(key, outcome(key))
    for key in keys[2:]:
        assert cache.get(key) is None
    assert cache.stats["tracked_journals"] <= 2
    assert cache.stats["cached_entries"] == 0
    assert cache.get(keys[0]) is None


def test_unserializable_identity_is_rejected_without_leaking_exception():
    key = identity(policy_epoch=10**5000)
    cache = GovernedResponseCache()
    assert cache.set(key, outcome(key)) is False
    assert cache.stats["cached_entries"] == 0


def test_clarification_is_cacheable_with_independent_structured_options():
    answer = outcome(status="needs_clarification", reason_code="ambiguous_instrument",
                     clarification_code="instrument_ambiguous", response_text="Esclareça o instrumento.",
                     clarification_options=[dict(instrument_id="instrument:synthetic", contract_title="Contrato sintético")])
    cache = GovernedResponseCache()
    assert cache.set(identity(), answer)
    answer.payload.clarification_options[0].contract_title = "Mutated"
    assert cache.get(identity()).payload.clarification_options[0].contract_title == "Contrato sintético"


def test_real_service_error_is_not_a_cacheable_outcome():
    cache = GovernedResponseCache()
    error = AnswerServiceError("service_unavailable", request_id="request:synthetic", audit=outcome().audit)
    assert cache.set(identity(), error) is False
    assert cache.get(identity()) is None


def test_even_forced_identity_mutation_cannot_mutate_stored_key():
    cache, key = GovernedResponseCache(), identity()
    assert cache.set(key, outcome(key))
    object.__setattr__(key, "question_sha256", "forged")
    assert cache.get(key) is None
    assert cache.set(key, outcome()) is False
    assert cache.get(identity()) is not None

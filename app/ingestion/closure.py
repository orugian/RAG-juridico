"""Governed, finite closure of complete approved documentary units.

This local resolver neither publishes evidence nor infers contractual effects.
Historical text remains separately attributed to its original instrument.
"""
from dataclasses import dataclass
from datetime import date
from typing import Callable, Mapping, Sequence

from app.contracts import CitationUnit, InstrumentRelation, RelationResolution, SourceIdentity
from app.ingestion.review_store import ReviewStore, review_digest, unit_review_digest


@dataclass(frozen=True)
class ClosureResult:
    status: str
    reason_code: str | None
    units: tuple[CitationUnit, ...]
    warnings: tuple[str, ...]
    token_count: int
    fingerprint: str


class _Blocked(ValueError):
    pass


def render_unit(unit: CitationUnit) -> str:
    """Render the entire canonical literal plus its attributed metadata."""
    parties = "; ".join(p.name + " (" + p.role.value +
                         (", " + p.clean_identifier if p.clean_identifier else "") + ")"
                         for p in unit.parties)
    locations = "; ".join(str(value) for value in unit.location.model_dump(exclude_none=True).values())
    header = ("Contrato: " + unit.contract_title + " [" + unit.instrument_id + "]\n"
              "Partes: " + parties + "\nLocalização: " + locations)
    if unit.synthetic_context:
        header += "\nContexto sintético: " + unit.synthetic_context
    return header + "\n" + unit.verbatim_text


def render_units(units: Sequence[CitationUnit]) -> str:
    return "\n\n".join(render_unit(unit) for unit in sorted(units, key=lambda item: item.unit_id))


def _source_key(source: SourceIdentity) -> dict:
    return {"source_id": source.source_id, "snapshot": source.snapshot,
            "file_hash": source.file_hash, "configuration_version": source.configuration_version}


def _current(ledger, source, *, scope, subject_id, digest=None, record_id=None):
    key = _source_key(source) | {"scope": scope, "subject_id": subject_id}
    try:
        if record_id is None:
            latest = ledger.latest(**key, subject_digest=digest)
            if latest is None:
                raise ValueError("Absent decision")
            record_id = latest.record_id
        return ledger.require_current(**key, record_id=record_id, subject_digest=digest)
    except ValueError as error:
        raise _Blocked(scope + "_review_unavailable") from error


def _review_unit(unit, sources, ledger, decisions, all_units):
    """Never trust approval fields or identity/spans without current decisions."""
    try:
        unit = CitationUnit.model_validate(unit.model_dump())
        if (unit.approval_state != "approved" or not unit.review_record_id or
                not unit.contract_title.strip() or not unit.parties or
                any(not p.name.strip() for p in unit.parties) or not unit.text_map):
            raise ValueError("Incomplete unit")
        identities = {identity.source_id: identity for identity in unit.source_identities}
        if (len(identities) != len(unit.source_identities) or set(identities) != set(unit.source_ids)
                or set(unit.block_ids) != {span.block_id for span in unit.spans}
                or set(unit.source_ids) != {span.source_id for span in unit.spans}):
            raise ValueError("Incomplete provenance")
        for source_id in unit.source_ids:
            source = sources.get(source_id)
            if source is None or source != identities[source_id] or source.instrument_id != unit.instrument_id:
                raise ValueError("Changed source identity")
            source_record = _current(ledger, source, scope="source", subject_id=source_id)
            parties_record = _current(ledger, source, scope="parties", subject_id=source_id,
                                      digest=review_digest({"contract_title": unit.contract_title,
                                                            "parties": [p.model_dump(mode="json") for p in unit.parties]}))
            unit_record = _current(ledger, source, scope="unit", subject_id=unit.unit_id,
                                   digest=unit_review_digest(unit), record_id=unit.review_record_id)
            decisions.update((record.record_id, record.model_dump(mode="json")) for record in
                             (source_record, parties_record, unit_record))
    except (ValueError, KeyError, AttributeError) as error:
        raise _Blocked("unit_review_unavailable") from error
    # Re-extract the physical reference candidates; an old scoped approval alone
    # cannot authorize omitted exceptions or an incomplete dependency declaration.
    from app.ingestion.references import validate_references
    try:
        validate_references(unit, all_units=all_units)
    except ValueError as error:
        raise _Blocked("unit_reference_unresolved") from error
    return unit


def _structural(selected, units, sources, ledger, decisions):
    """Iterative DFS avoids recursion depth failures on long legal chains."""
    visited, active, output = set(), set(), {}
    stack = [(unit_id, False) for unit_id in reversed(sorted(set(selected)))]
    while stack:
        unit_id, leaving = stack.pop()
        if leaving:
            active.remove(unit_id)
            visited.add(unit_id)
            continue
        if unit_id in active:
            raise _Blocked("closure_cycle")
        if unit_id in visited:
            continue
        candidate = units.get(unit_id)
        if candidate is None or candidate.unit_id != unit_id:
            raise _Blocked("closure_unit_missing")
        unit = _review_unit(candidate, sources, ledger, decisions, units)
        output[unit_id] = unit
        active.add(unit_id)
        dependencies = set(unit.closure_unit_ids)
        if unit.parent_unit_id:
            dependencies.add(unit.parent_unit_id)
        stack.append((unit_id, True))
        stack.extend((dependency, False) for dependency in reversed(sorted(dependencies)))
    return output


def _components(instruments, relations):
    adjacency = {}
    for relation in relations:
        a, b = relation.from_instrument_id, relation.to_instrument_id
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)
    visited, components = set(), []
    for seed in sorted(instruments):
        if seed in visited:
            continue
        component, pending = set(), [seed]
        while pending:
            instrument = pending.pop()
            if instrument in component:
                continue
            component.add(instrument)
            pending.extend(adjacency.get(instrument, set()) - component)
        visited.update(component)
        components.append(component)
    return components


def _anchor(subject_id, review_sources, sources):
    source = sources.get(review_sources.get(subject_id))
    if source is None:
        raise ValueError("Missing review anchor")
    return source


def _relation_review(relation, review_sources, sources, ledger, decisions, source_heads):
    if relation.state not in {"approved", "rejected"} or not relation.review_record_id:
        raise _Blocked("relation_unresolved")
    try:
        source = _anchor(relation.relation_id, review_sources, sources)
        if (source.instrument_id != relation.from_instrument_id or source.snapshot != relation.snapshot or
                source.configuration_version != relation.configuration_version):
            raise ValueError("Changed relation identity")
        if relation.state == "rejected":
            # Association rejection is usable without publishing quarantined source text.
            source_record = ledger.latest(**_source_key(source), scope="source", subject_id=source.source_id)
            if source_record is None:
                raise ValueError("Rejected association has stale source identity")
            source_heads[source.source_id] = source_record.model_dump(mode="json")
        else:
            source_record = _current(ledger, source, scope="source", subject_id=source.source_id)
            decisions[source_record.record_id] = source_record.model_dump(mode="json")
        record = _current(ledger, source, scope="relation", subject_id=relation.relation_id,
                          record_id=relation.review_record_id,
                          digest=review_digest(relation.model_dump(mode="json", exclude={"review_record_id"})))
        decisions[record.record_id] = record.model_dump(mode="json")
    except ValueError as error:
        raise _Blocked("relation_review_unavailable") from error


def _family_review(component, component_relations, resolutions, review_sources, sources, ledger, decisions):
    candidates = [resolution for resolution in resolutions
                  if review_sources.get(resolution.family_id) in sources
                  and sources[review_sources[resolution.family_id]].instrument_id in component]
    if len(candidates) != 1:
        raise _Blocked("family_unresolved")
    resolution = candidates[0]
    if resolution.state != "resolved" or resolution.material_risk_source_ids:
        raise _Blocked("family_unresolved")
    if set(resolution.relation_ids) != {r.relation_id for r in component_relations}:
        raise _Blocked("family_relation_mismatch")
    if not resolution.registry_digest or resolution.registry_digest != ledger.relation_state_digest():
        raise _Blocked("family_registry_stale")
    try:
        source = _anchor(resolution.family_id, review_sources, sources)
        if source.snapshot != resolution.snapshot or any(
                relation.snapshot != resolution.snapshot for relation in component_relations):
            raise ValueError("Changed family snapshot")
        source_record = _current(ledger, source, scope="source", subject_id=source.source_id)
        record = _current(ledger, source, scope="family", subject_id=resolution.family_id,
                          record_id=resolution.review_record_id,
                          digest=review_digest(resolution.model_dump(mode="json", exclude={"review_record_id"})))
        decisions.update((r.record_id, r.model_dump(mode="json")) for r in (source_record, record))
    except ValueError as error:
        raise _Blocked("family_review_unavailable") from error


def _relation_cycles(relations):
    edges = {}
    for relation in relations:
        if relation.state == "approved":
            edges.setdefault(relation.from_instrument_id, set()).add(relation.to_instrument_id)
    complete, active = set(), set()
    for seed in sorted(edges):
        stack = [(seed, False)]
        while stack:
            item, leaving = stack.pop()
            if leaving:
                active.remove(item)
                complete.add(item)
            elif item in active:
                raise _Blocked("relation_cycle")
            elif item not in complete:
                active.add(item)
                stack.append((item, True))
                stack.extend((target, False) for target in sorted(edges.get(item, ()), reverse=True))


def _relation_units(relation, units):
    for unit_id in relation.affected_unit_ids:
        if unit_id not in units or units[unit_id].instrument_id != relation.to_instrument_id:
            raise _Blocked("relation_support_missing")
    support_spans = []
    for unit_id in relation.support_unit_ids:
        if unit_id not in units or units[unit_id].instrument_id != relation.from_instrument_id:
            raise _Blocked("relation_support_missing")
        support_spans.extend(units[unit_id].spans)
    if (not relation.affected_unit_ids or not relation.support_unit_ids or not relation.support_spans
            or any(span not in support_spans for span in relation.support_spans)):
        raise _Blocked("relation_support_missing")
    return set(relation.affected_unit_ids) | set(relation.support_unit_ids)


def resolve_closure(selected_unit_ids, *, units: Mapping[str, CitationUnit],
                    sources: Mapping[str, SourceIdentity], ledger: ReviewStore,
                    relations: Sequence[InstrumentRelation] = (), resolutions: Sequence[RelationResolution] = (),
                    relation_review_sources=None, family_review_sources=None,
                    evidence_scope="linked_instruments", reference_date: date | None = None,
                    count_tokens: Callable[[str], int], max_tokens: int) -> ClosureResult:
    """Resolve or return an empty controlled result; never shorten proof to fit."""
    relation_review_sources = relation_review_sources or {}
    family_review_sources = family_review_sources or {}
    selected = tuple(sorted(set(selected_unit_ids)))
    decisions, source_heads, warnings = {}, {}, set()
    ledger_state = ledger.state_digest()
    # Include all known states/discoveries in identity, even historical warnings.
    fingerprint_context = {
        "version": "governed-closure-v1", "selected": selected,
        "ledger_state": ledger_state,
        "evidence_scope": evidence_scope, "reference_date": str(reference_date) if reference_date else None,
        "max_tokens": max_tokens,
        "relations": sorted([r.model_dump(mode="json") for r in relations], key=lambda r: r["relation_id"]),
        "resolutions": sorted([r.model_dump(mode="json") for r in resolutions], key=lambda r: r["family_id"]),
        "relation_review_sources": relation_review_sources, "family_review_sources": family_review_sources,
        "sources": {key: value.model_dump(mode="json") for key, value in sorted(sources.items())},
    }

    def result(status, reason, proof=(), tokens=0):
        fingerprint = review_digest(fingerprint_context | {
            "status": status, "reason_code": reason, "warnings": sorted(warnings),
            "decisions": decisions, "source_heads": source_heads,
            "units": [u.model_dump(mode="json") for u in proof],
            "token_count": tokens})
        return ClosureResult(status, reason, tuple(proof), tuple(sorted(warnings)), tokens, fingerprint)

    try:
        if evidence_scope not in {"original_text", "linked_instruments"}:
            raise _Blocked("invalid_scope")
        if not selected:
            raise _Blocked("no_selection")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
            raise _Blocked("token_budget_invalid")
        if len({r.relation_id for r in relations}) != len(relations):
            raise _Blocked("relation_registry_ambiguous")
        if len({r.family_id for r in resolutions}) != len(resolutions):
            raise _Blocked("family_unresolved")
        if any(key != source.source_id for key, source in sources.items()):
            raise _Blocked("source_identity_mismatch")
        proof = _structural(selected, units, sources, ledger, decisions)
        if reference_date and evidence_scope == "linked_instruments":
            raise _Blocked("temporal_effect_unresolved")
        if evidence_scope == "original_text":
            warnings.add("historical_text_not_effect")
        reviewed_families, included_relations = set(), set()
        while True:
            initial_ids = set(proof)
            components = _components({u.instrument_id for u in proof.values()}, relations)
            for component in components:
                relevant = [r for r in relations if r.from_instrument_id in component]
                if evidence_scope == "linked_instruments":
                    component_key = frozenset(component)
                    if component_key not in reviewed_families:
                        _family_review(component, relevant, resolutions, family_review_sources, sources, ledger, decisions)
                        for relation in relevant:
                            _relation_review(relation, relation_review_sources, sources, ledger, decisions, source_heads)
                            if relation.state == "approved":
                                _relation_units(relation, units)
                        _relation_cycles(relevant)
                        reviewed_families.add(component_key)
                else:
                    for resolution in resolutions:
                        anchor_id = family_review_sources.get(resolution.family_id)
                        if anchor_id in sources and sources[anchor_id].instrument_id in component:
                            if resolution.state != "resolved" or resolution.material_risk_source_ids:
                                warnings.add("family_unresolved:" + resolution.family_id)
                for relation in relevant:
                    if relation.relation_id in included_relations:
                        continue
                    if evidence_scope == "original_text":
                        try:
                            _relation_review(relation, relation_review_sources, sources, ledger, decisions, source_heads)
                        except _Blocked:
                            code = "relation_unresolved:" if relation.state in {"proposed", "conflicted"} else "relation_review_unavailable:"
                            warnings.add(code + relation.relation_id)
                            included_relations.add(relation.relation_id)
                            continue
                    if relation.state == "rejected":
                        included_relations.add(relation.relation_id)
                        continue
                    if evidence_scope == "original_text":
                        warnings.add("known_modifier:" + relation.relation_id)
                    # Partial amendments expand only when their affected/support units intersect closure.
                    if not set(relation.affected_unit_ids + relation.support_unit_ids).intersection(proof):
                        continue
                    try:
                        required = _relation_units(relation, units)
                        expansion = _structural(required, units, sources, ledger, decisions)
                    except _Blocked:
                        if evidence_scope == "linked_instruments":
                            raise
                        warnings.add("modifier_proof_unavailable:" + relation.relation_id)
                        included_relations.add(relation.relation_id)
                        continue
                    proof.update(expansion)
                    included_relations.add(relation.relation_id)
            if set(proof) == initial_ids:
                break
        ordered = tuple(proof[key] for key in sorted(proof))
        try:
            token_count = count_tokens(render_units(ordered))
        except Exception as error:
            raise _Blocked("token_count_unavailable") from error
        if isinstance(token_count, bool) or not isinstance(token_count, int) or token_count < 1:
            raise _Blocked("token_count_invalid")
        # A tokenization callback may take time; refuse decisions revoked during assembly.
        for record in decisions.values():
            try:
                ledger.require_current(**{field: record[field] for field in
                    ("record_id", "source_id", "snapshot", "file_hash", "configuration_version",
                     "scope", "subject_id", "subject_digest")})
            except ValueError as error:
                raise _Blocked("review_changed_during_resolution") from error
        for previous in source_heads.values():
            latest = ledger.latest(**{field: previous[field] for field in
                ("source_id", "snapshot", "file_hash", "configuration_version", "scope", "subject_id", "subject_digest")})
            if latest is None or latest.model_dump(mode="json") != previous:
                raise _Blocked("review_changed_during_resolution")
        if ledger.state_digest() != ledger_state:
            raise _Blocked("review_changed_during_resolution")
        if token_count > max_tokens:
            return result("blocked", "closure_over_budget", tokens=token_count)
        return result("complete", None, ordered, token_count)
    except _Blocked as error:
        return result("blocked", str(error))

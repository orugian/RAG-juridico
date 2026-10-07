"""Synthetic scoped-ledger closure cases; no real corpus approval."""
from datetime import date

import pytest

from app.contracts import CitationUnit, InstrumentRelation, RelationResolution, SourceIdentity, UnitTextSpan
from app.ingestion.closure import render_units, resolve_closure
from app.ingestion.review_store import ReviewStore, review_digest, unit_review_digest


def identity(source="base"):
    return SourceIdentity(source_id=source, instrument_id=source, doc_version=1,
                          file_id=source, file_hash="a" * 64, snapshot="snapshot-1",
                          configuration_version="cfg-1", parser_name="fixture", parser_version="1")


def key(source):
    return dict(source_id=source.source_id, snapshot=source.snapshot,
                file_hash=source.file_hash, configuration_version=source.configuration_version)


def approve(ledger, source, scope="source", subject_id=None, subject_digest=None):
    return ledger.record(**key(source), scope=scope, subject_id=subject_id,
                         subject_digest=subject_digest, decision="approved",
                         reviewer="synthetic-test", reason="Explicit synthetic fixture")


def unit(ledger, source, name="base:u1", text="Não pagar, salvo cumprimento da condição.", **changes):
    span = dict(source_id=source.source_id, block_id=name + ":b", start=0, end=len(text),
                xml_part="word/document.xml", body_child_index=0)
    value = CitationUnit(unit_id=name, instrument_id=source.instrument_id, verbatim_text=text,
                         source_ids=[source.source_id], block_ids=[name + ":b"], spans=[span],
                         location={"label": "Cláusula sintética"}, source_identities=[source],
                         contract_title="Contrato sintético", parties=[{"name": "Parte sintética"}],
                         **changes)
    # Complete physical coverage; the fixture never pretends this is parser proof.
    value = value.model_copy(update={"text_map": [UnitTextSpan(unit_start=0, unit_end=len(text),
                                                               source_span=value.spans[0])]})
    value = CitationUnit.model_validate(value.model_dump())
    approve(ledger, source)
    approve(ledger, source, "parties", source.source_id,
            review_digest({"contract_title": value.contract_title,
                           "parties": [p.model_dump(mode="json") for p in value.parties]}))
    record = approve(ledger, source, "unit", name, unit_review_digest(value))
    return value.model_copy(update={"approval_state": "approved", "review_record_id": record.record_id})


def family(ledger, source, relations=(), **changes):
    resolution = RelationResolution(family_id="family:" + source.source_id,
                                    snapshot=source.snapshot, registry_version="registry-1",
                                    registry_digest=ledger.relation_state_digest(),
                                    relation_ids=[r.relation_id for r in relations], **changes)
    resolution = resolution.model_copy(update={"state": "resolved"})
    record = approve(ledger, source, "family", resolution.family_id,
                     review_digest(resolution.model_dump(mode="json", exclude={"review_record_id"})))
    return resolution.model_copy(update={"state": "resolved", "review_record_id": record.record_id})


def relation(ledger, modifier, base_unit, support, name="r1"):
    candidate = InstrumentRelation(relation_id=name, from_instrument_id=modifier.instrument_id,
                                  to_instrument_id=base_unit.instrument_id, relation_type="amends",
                                  affected_unit_ids=[base_unit.unit_id], support_unit_ids=[support.unit_id],
                                  support_spans=support.spans, snapshot=modifier.snapshot,
                                  configuration_version=modifier.configuration_version)
    candidate = candidate.model_copy(update={"state": "approved"})
    record = approve(ledger, modifier, "relation", name,
                     review_digest(candidate.model_dump(mode="json", exclude={"review_record_id"})))
    return candidate.model_copy(update={"state": "approved", "review_record_id": record.record_id})


@pytest.fixture
def setup(tmp_path):
    ledger = ReviewStore(tmp_path / "synthetic.sqlite")
    source = identity()
    base = unit(ledger, source)
    resolved = family(ledger, source)
    return dict(units={base.unit_id: base}, sources={source.source_id: source}, ledger=ledger,
                resolutions=[resolved], family_review_sources={resolved.family_id: source.source_id},
                count_tokens=len, max_tokens=100000)


def run(setup, selected=("base:u1",), **changes):
    return resolve_closure(selected, **(setup | changes))


def test_approved_whole_unit_and_exact_render_budget(setup):
    result = run(setup)
    assert result.status == "complete"
    assert result.token_count == len(render_units(result.units))
    assert result.units[0].verbatim_text == "Não pagar, salvo cumprimento da condição."
    assert "Contrato sintético" in render_units(result.units)
    assert "Parte sintética" in render_units(result.units)
    assert "Cláusula sintética" in render_units(result.units)
    assert run(setup, max_tokens=result.token_count).status == "complete"
    blocked = run(setup, max_tokens=result.token_count - 1)
    assert blocked.reason_code == "closure_over_budget" and blocked.units == ()


@pytest.mark.parametrize("scope", ["source", "parties", "unit"])
def test_latest_revocation_blocks_all_derived_proof(setup, scope):
    source, base = setup["sources"]["base"], setup["units"]["base:u1"]
    subject = base.unit_id if scope == "unit" else source.source_id
    digest = unit_review_digest(base) if scope == "unit" else review_digest({
        "contract_title": base.contract_title, "parties": [p.model_dump(mode="json") for p in base.parties]})
    setup["ledger"].record(**key(source), scope=scope, subject_id=subject,
                          subject_digest=None if scope == "source" else digest,
                          decision="quarantined", reviewer="synthetic-test", reason="Synthetic revocation")
    assert run(setup).units == ()
    assert run(setup).reason_code == "unit_review_unavailable"


@pytest.mark.parametrize("field,value", [("review_record_id", "forged"),
                                            ("contract_title", "Alterado"),
                                            ("synthetic_context", "Não omitir contexto"),
                                            ("verbatim_text", "Pagar sempre.")])
def test_forged_or_stale_content_rejected(setup, field, value):
    base = setup["units"]["base:u1"].model_copy(update={field: value})
    assert run(setup, units={base.unit_id: base}).units == ()


def test_no_relations_does_not_imply_family_registry_complete(setup):
    result = run(setup, resolutions=[])
    assert result.reason_code == "family_unresolved" and result.units == ()


def test_parent_and_structural_dependencies_are_transitive(setup):
    source = setup["sources"]["base"]
    parent = unit(setup["ledger"], source, "base:parent", "Condição integral.")
    dependency = unit(setup["ledger"], source, "base:annex", "Exceção integral.")
    base = unit(setup["ledger"], source, parent_unit_id=parent.unit_id,
                closure_unit_ids=[dependency.unit_id])
    result = run(setup, units={u.unit_id: u for u in [base, parent, dependency]})
    assert {u.unit_id for u in result.units} == {base.unit_id, parent.unit_id, dependency.unit_id}


def test_structural_cycle_and_missing_are_controlled(setup):
    source = setup["sources"]["base"]
    base = unit(setup["ledger"], source, closure_unit_ids=["base:other"])
    assert run(setup, units={base.unit_id: base}).reason_code == "closure_unit_missing"
    other = unit(setup["ledger"], source, "base:other", closure_unit_ids=[base.unit_id])
    result = run(setup, units={base.unit_id: base, other.unit_id: other})
    assert result.reason_code == "closure_cycle" and result.units == ()


def linked(setup):
    modifier = identity("addendum")
    support = unit(setup["ledger"], modifier, "addendum:u1", "A condição foi complementada.")
    link = relation(setup["ledger"], modifier, setup["units"]["base:u1"], support)
    resolution = family(setup["ledger"], setup["sources"]["base"], [link])
    return setup | {"units": setup["units"] | {support.unit_id: support},
                    "sources": setup["sources"] | {modifier.source_id: modifier},
                    "relations": [link], "resolutions": [resolution],
                    "relation_review_sources": {link.relation_id: modifier.source_id}}


def test_reverse_relation_adds_modifier_independent_of_top_k(setup):
    values = linked(setup)
    result = run(values)
    assert result.status == "complete"
    assert {u.unit_id for u in result.units} == {"base:u1", "addendum:u1"}
    assert result.fingerprint != run(setup).fingerprint


@pytest.mark.parametrize("state", ["proposed", "conflicted"])
def test_pending_and_conflicting_modifiers_never_emit_base_alone(setup, state):
    values = linked(setup)
    values["relations"] = [values["relations"][0].model_copy(update={"state": state})]
    result = run(values)
    assert result.reason_code == "relation_unresolved" and result.units == ()


def test_quarantined_modifier_blocks_conditions_but_preserves_history(setup):
    values = linked(setup)
    values["units"]["addendum:u1"] = values["units"]["addendum:u1"].model_copy(
        update={"approval_state": "quarantined"})
    assert run(values).units == ()
    result = run(values, evidence_scope="original_text")
    assert result.status == "complete" and [u.unit_id for u in result.units] == ["base:u1"]
    assert "modifier_proof_unavailable:r1" in result.warnings


def test_relation_change_and_stale_family_decision_fail_closed(setup):
    values = linked(setup)
    values["relations"] = [values["relations"][0].model_copy(update={"relation_type": "terminates"})]
    assert run(values).reason_code == "relation_review_unavailable"
    values = linked(setup)
    values["resolutions"] = [values["resolutions"][0].model_copy(update={"registry_version": "registry-2"})]
    assert run(values).reason_code == "family_review_unavailable"


def test_family_exact_set_and_risks(setup):
    values = linked(setup)
    resolution = values["resolutions"][0]
    values["resolutions"] = [resolution.model_copy(update={"relation_ids": []})]
    assert run(values).reason_code == "family_relation_mismatch"
    values["resolutions"] = [resolution.model_copy(update={"material_risk_source_ids": ["withheld"]})]
    assert run(values).reason_code == "family_unresolved"


def test_reference_date_never_adjudicates_effect(setup):
    assert run(setup, reference_date=date(2026, 10, 6)).reason_code == "temporal_effect_unresolved"
    result = run(setup, evidence_scope="original_text", reference_date=date(2026, 10, 6))
    assert result.status == "complete" and "historical_text_not_effect" in result.warnings


def test_invalid_scope_and_bad_token_count_are_controlled(setup):
    assert run(setup, evidence_scope="invented").reason_code == "invalid_scope"
    for count in [-1, 0, 1.2, True]:
        assert run(setup, count_tokens=lambda _: count).reason_code == "token_count_invalid"


def test_incomplete_identity_and_text_map_are_never_emitted(setup):
    for field, value in [("source_identities", []), ("text_map", []), ("block_ids", ["unmapped"])]:
        changed = setup["units"]["base:u1"].model_copy(update={field: value})
        assert run(setup, units={changed.unit_id: changed}).units == ()


def test_rejection_cannot_be_fabricated_by_flipping_reviewed_state(setup):
    values = linked(setup)
    link = values["relations"][0].model_copy(update={"state": "rejected"})
    values["relations"] = [link]
    resolution = family(values["ledger"], values["sources"]["base"], [link])
    values["resolutions"] = [resolution]
    assert run(values).reason_code == "relation_review_unavailable"
    decision = approve(values["ledger"], values["sources"]["addendum"], "relation", link.relation_id,
                       review_digest(link.model_dump(mode="json", exclude={"review_record_id"})))
    values["relations"] = [link.model_copy(update={"review_record_id": decision.record_id})]
    values["resolutions"] = [family(values["ledger"], values["sources"]["base"], values["relations"])]
    result = run(values)
    assert result.status == "complete" and [u.unit_id for u in result.units] == ["base:u1"]


def test_relation_cycles_and_missing_support_are_controlled(setup):
    values = linked(setup)
    reverse = relation(values["ledger"], values["sources"]["base"], values["units"]["addendum:u1"],
                       values["units"]["base:u1"], name="reverse")
    values["relations"].append(reverse)
    values["relation_review_sources"]["reverse"] = "base"
    values["resolutions"] = [family(values["ledger"], values["sources"]["base"], values["relations"])]
    result = run(values)
    assert result.reason_code == "relation_cycle" and result.units == ()
    values = linked(setup)
    values["units"].pop("addendum:u1")
    assert run(values).reason_code == "relation_support_missing"


@pytest.mark.parametrize("kind", ["terminates", "supersedes", "supplements"])
def test_other_reviewed_relation_types_preserve_base_and_modifier(setup, kind):
    values = linked(setup)
    link = values["relations"][0].model_copy(update={"relation_type": kind})
    record = approve(values["ledger"], values["sources"]["addendum"], "relation", link.relation_id,
                     review_digest(link.model_dump(mode="json", exclude={"review_record_id"})))
    values["relations"] = [link.model_copy(update={"review_record_id": record.record_id})]
    values["resolutions"] = [family(values["ledger"], values["sources"]["base"], values["relations"])]
    assert {u.unit_id for u in run(values).units} == {"base:u1", "addendum:u1"}


def test_partial_amendment_does_not_emit_unaffected_modifier(setup):
    values = linked(setup)
    other = unit(values["ledger"], values["sources"]["base"], "base:other", "Outro dispositivo integral.")
    values["units"][other.unit_id] = other
    result = run(values, selected=[other.unit_id])
    assert result.status == "complete" and [u.unit_id for u in result.units] == [other.unit_id]


def test_reviewed_relation_with_unknown_affected_unit_blocks_family(setup):
    values = linked(setup)
    link = values["relations"][0].model_copy(update={"affected_unit_ids": ["base:missing"]})
    record = approve(values["ledger"], values["sources"]["addendum"], "relation", link.relation_id,
                     review_digest(link.model_dump(mode="json", exclude={"review_record_id"})))
    values["relations"] = [link.model_copy(update={"review_record_id": record.record_id})]
    values["resolutions"] = [family(values["ledger"], values["sources"]["base"], values["relations"])]
    result = run(values)
    assert result.reason_code == "relation_support_missing" and result.units == ()


def test_unicode_and_context_enter_whole_render_budget(setup):
    source = setup["sources"]["base"]
    base = unit(setup["ledger"], source, text="Não 🧑🏿‍⚖️ pagar\u0301 sem aprovação.",
                synthetic_context="Rótulo reconstruído 🧾")
    result = run(setup, units={base.unit_id: base})
    assert "Contexto sintético: Rótulo reconstruído 🧾" in render_units(result.units)
    assert result.token_count == len(render_units(result.units))
    blocked = run(setup, units={base.unit_id: base}, max_tokens=result.token_count - 1)
    assert blocked.reason_code == "closure_over_budget" and blocked.units == ()


@pytest.mark.parametrize("field,value", [("snapshot", "other"),
                                           ("configuration_version", "other"),
                                           ("doc_version", 2), ("file_hash", "b" * 64)])
def test_source_identity_changes_invalidate_closure(setup, field, value):
    source = setup["sources"]["base"].model_copy(update={field: value})
    assert run(setup, sources={source.source_id: source}).reason_code == "unit_review_unavailable"


def test_reapproval_with_new_id_invalidates_cached_unit_record(setup):
    base, source = setup["units"]["base:u1"], setup["sources"]["base"]
    approve(setup["ledger"], source, "unit", base.unit_id, unit_review_digest(base))
    assert run(setup).reason_code == "unit_review_unavailable"


def test_history_checks_rejected_state_and_warns_unknown_relation_scope(setup):
    values = linked(setup)
    values["relations"] = [values["relations"][0].model_copy(update={"state": "rejected"})]
    result = run(values, evidence_scope="original_text")
    assert result.status == "complete" and "relation_review_unavailable:r1" in result.warnings
    values["relations"] = [values["relations"][0].model_copy(update={"state": "proposed", "affected_unit_ids": []})]
    result = run(values, evidence_scope="original_text")
    assert result.status == "complete" and "relation_unresolved:r1" in result.warnings


def test_revocation_during_tokenization_cannot_emit_proof(setup):
    def revoke(text):
        source = setup["sources"]["base"]
        setup["ledger"].record(**key(source), decision="quarantined", reviewer="synthetic-test",
                               reason="Synthetic concurrent revocation")
        return len(text)
    result = run(setup, count_tokens=revoke)
    assert result.reason_code == "review_changed_during_resolution" and result.units == ()


def test_family_cannot_combine_different_registry_snapshots(setup):
    values = linked(setup)
    resolution = values["resolutions"][0].model_copy(update={"snapshot": "snapshot-2"})
    values["resolutions"] = [resolution]
    assert run(values).reason_code == "family_review_unavailable"


def test_forged_relation_and_family_record_ids_are_rejected(setup):
    values = linked(setup)
    values["relations"] = [values["relations"][0].model_copy(update={"review_record_id": "forged"})]
    assert run(values).reason_code == "relation_review_unavailable"
    values = linked(setup)
    values["resolutions"] = [values["resolutions"][0].model_copy(update={"review_record_id": "forged"})]
    assert run(values).reason_code == "family_review_unavailable"


def rejected_quarantined(setup):
    values = linked(setup)
    source = values["sources"]["addendum"]
    values["ledger"].record(**key(source), decision="quarantined", reviewer="synthetic-test",
                            reason="Synthetic source retained in quarantine")
    link = values["relations"][0].model_copy(update={"state": "rejected"})
    record = approve(values["ledger"], source, "relation", link.relation_id,
                     review_digest(link.model_dump(mode="json", exclude={"review_record_id"})))
    values["relations"] = [link.model_copy(update={"review_record_id": record.record_id})]
    values["resolutions"] = [family(values["ledger"], values["sources"]["base"], values["relations"])]
    return values


def test_current_rejection_releases_base_without_quarantined_text(setup):
    values = rejected_quarantined(setup)
    result = run(values)
    assert result.status == "complete" and [u.unit_id for u in result.units] == ["base:u1"]
    source = values["sources"]["addendum"].model_copy(update={"file_hash": "b" * 64})
    values["sources"][source.source_id] = source
    assert run(values).reason_code == "relation_review_unavailable"


def test_quarantined_rejection_source_head_revalidated_after_tokenization(setup):
    values = rejected_quarantined(setup)
    def re_quarantine(text):
        values["ledger"].record(**key(values["sources"]["addendum"]), decision="quarantined",
                                reviewer="synthetic-test", reason="Synthetic changed head")
        return len(text)
    result = run(values, count_tokens=re_quarantine)
    assert result.reason_code == "review_changed_during_resolution" and result.units == ()


def test_new_registry_decision_during_tokenization_blocks_closure(setup):
    def discovered_risk(text):
        approve(setup["ledger"], identity("unknown"), "relation", "new-relation", review_digest({"fixture": "risk"}))
        return len(text)
    result = run(setup, count_tokens=discovered_risk)
    assert result.reason_code == "review_changed_during_resolution" and result.units == ()


def test_registry_discovery_cannot_be_omitted_by_caller_with_old_family(setup):
    before = run(setup)
    approve(setup["ledger"], identity("unknown"), "relation", "new-relation", review_digest({"fixture": "risk"}))
    after = run(setup)
    assert before.status == "complete" and after.status == "blocked"
    assert after.reason_code == "family_registry_stale" and after.units == ()
    assert before.fingerprint != after.fingerprint


def test_missing_registry_digest_is_not_governed_family_acceptance(setup):
    resolution = setup["resolutions"][0].model_copy(update={"registry_digest": None})
    assert run(setup, resolutions=[resolution]).reason_code == "family_registry_stale"


def test_history_warns_pending_family_risk_even_without_visible_relations(setup):
    resolution = setup["resolutions"][0].model_copy(update={"state": "pending",
                                                          "material_risk_source_ids": ["withheld"]})
    result = run(setup, resolutions=[resolution], evidence_scope="original_text")
    assert result.status == "complete" and "family_unresolved:family:base" in result.warnings


@pytest.mark.parametrize("literal", ["A regra aplica-se, ressalvadas as exceções da Cláusula 7.",
                                      "Não pagar antes das condições do Anexo II.",
                                      "O dever depende do art. 8.",
                                      "Não pagar antes da condição da cláusula anterior.",
                                      "Ressalvam-se as exceções do referido anexo."])
def test_review_does_not_authorize_omitted_literal_reference_candidates(setup, literal):
    source = setup["sources"]["base"]
    base = unit(setup["ledger"], source, text=literal)
    # Even a current synthetic unit review cannot make an undeclared reference complete.
    assert base.references == [] and base.closure_unit_ids == []
    result = run(setup, units={base.unit_id: base})
    assert result.reason_code == "unit_reference_unresolved" and result.units == ()


def review_bound_units(setup, values, mappings=None):
    from app.ingestion.references import bind_references
    bound = bind_references(values, reference_bindings=mappings)
    reviewed = {}
    for value in bound:
        source = setup["sources"][value.source_ids[0]]
        record = approve(setup["ledger"], source, "unit", value.unit_id, unit_review_digest(value))
        reviewed[value.unit_id] = value.model_copy(update={"approval_state": "approved",
                                                         "review_record_id": record.record_id})
    return reviewed


def reference_fixture(setup):
    source = setup["sources"]["base"]
    base = unit(setup["ledger"], source, text="Honorários devidos, ressalvadas as exceções da Cláusula 7.")
    exception = unit(setup["ledger"], source, "base:exception",
                     "Não aplicar honorários antes das condições do Anexo II.", reference_labels=["clause:7"])
    condition = unit(setup["ledger"], source, "base:conditions",
                     "Sem autorização prévia não há incidência.", reference_labels=["annex:2"])
    return review_bound_units(setup, [base, exception, condition])


def test_bound_references_include_transitive_conditions_and_never_shorten_literals(setup):
    values = reference_fixture(setup)
    result = run(setup, units=values)
    assert result.status == "complete"
    assert {u.unit_id for u in result.units} == {"base:u1", "base:exception", "base:conditions"}
    assert "Sem autorização prévia não há incidência." in render_units(result.units)
    assert "Não aplicar honorários antes das condições do Anexo II." in render_units(result.units)


def test_ambiguous_reference_requires_review_mapping_before_approval(setup):
    from app.ingestion.references import reference_candidates
    source = setup["sources"]["base"]
    base = unit(setup["ledger"], source, text="Aplicar conforme a Cláusula 7.")
    first = unit(setup["ledger"], source, "base:first", "Primeira exceção.", reference_labels=["clause:7"])
    second = unit(setup["ledger"], source, "base:second", "Outra exceção.", reference_labels=["clause:7"])
    values = review_bound_units(setup, [base, first, second])
    assert run(setup, units=values).reason_code == "unit_reference_unresolved"
    reference_id = reference_candidates(base)[0].reference_id
    values = review_bound_units(setup, [base, first, second], {reference_id: [first.unit_id]})
    result = run(setup, units=values)
    assert result.status == "complete"
    assert {u.unit_id for u in result.units} == {base.unit_id, first.unit_id}


@pytest.mark.parametrize("mutation", ["candidate_omitted", "dependency_omitted", "missing_target", "target_label_omitted"])
def test_even_new_review_cannot_authorize_incomplete_reference_closure(setup, mutation):
    values = reference_fixture(setup)
    base = values["base:u1"]
    if mutation == "candidate_omitted":
        base = base.model_copy(update={"references": []})
    elif mutation == "dependency_omitted":
        base = base.model_copy(update={"closure_unit_ids": []})
    elif mutation == "missing_target":
        values.pop("base:exception")
    else:
        target = values["base:exception"].model_copy(update={"reference_labels": []})
        target_review = approve(setup["ledger"], setup["sources"]["base"], "unit", target.unit_id,
                                unit_review_digest(target))
        values[target.unit_id] = target.model_copy(update={"review_record_id": target_review.record_id})
    source = setup["sources"]["base"]
    record = approve(setup["ledger"], source, "unit", base.unit_id, unit_review_digest(base))
    values[base.unit_id] = base.model_copy(update={"review_record_id": record.record_id})
    result = run(setup, units=values)
    assert result.reason_code == "unit_reference_unresolved" and result.units == ()


def test_bound_reference_cycles_still_fail_finitely(setup):
    source = setup["sources"]["base"]
    base = unit(setup["ledger"], source, text="A condição depende da Cláusula 7.", reference_labels=["clause:1"])
    other = unit(setup["ledger"], source, "base:other", "A exceção depende da Cláusula 1.",
                 reference_labels=["clause:7"])
    values = review_bound_units(setup, [base, other])
    result = run(setup, units=values)
    assert result.reason_code == "closure_cycle" and result.units == ()


def test_own_literal_heading_binds_without_self_dependency(setup):
    source = setup["sources"]["base"]
    base = unit(setup["ledger"], source, text="Cláusula 1. Não pagar sem autorização.",
                reference_labels=["clause:1"])
    values = review_bound_units(setup, [base])
    assert values[base.unit_id].closure_unit_ids == []
    result = run(setup, units=values)
    assert result.status == "complete" and result.units[0].verbatim_text == base.verbatim_text

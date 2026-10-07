"""Synthetic reference evidence; lexical binding never approves legal effects."""
import pytest

from app.contracts import CitationUnit, SourceSpan, UnitTextSpan
from app.ingestion.references import (
    bind_references,
    reference_candidates,
    reference_labels_for_blocks,
    validate_references,
)
from app.ingestion.review_store import unit_review_digest
from app.ingestion.schemas import DocumentBlock


def unit(name="u1", text="Remuneração conforme a Cláusula 2.", labels=(), offset=0,
         source="synthetic", instrument="synthetic"):
    span = SourceSpan(source_id=source, block_id=f"{name}:b", start=offset,
                      end=offset + len(text), xml_part="word/document.xml",
                      body_child_index=0)
    return CitationUnit(unit_id=name, instrument_id=instrument, verbatim_text=text,
                        source_ids=[source], block_ids=[span.block_id], spans=[span],
                        location={"label": "Local sintético"},
                        text_map=[UnitTextSpan(unit_start=0, unit_end=len(text), source_span=span)],
                        reference_labels=list(labels))


def block(label, kind="clause", text="Condição segundo Cláusula 99."):
    return DocumentBlock(block_id="b1", doc_id=1, doc_version=1, block_type=kind,
                         hierarchy_level=kind, hierarchy_label=label, order_index=0,
                         text_raw=text, text_search=text)


@pytest.mark.parametrize("literal,label", [
    ("Cláusula 2", "clause:2"), ("cl. 2ª", "clause:2"),
    ("CLÁUSULA SEGUNDA", "clause:2"), ("Clausula décima primeira", "clause:11"),
    ("parágrafo terceiro", "paragraph:3"), ("§ 2º", "paragraph:2"),
    ("Parágrafo único", "paragraph:unico"), ("Anexo II", "annex:2"),
    ("Anexo A", "annex:a"), ("item 3.2", "item:3.2"),
    ("artigo 8º", "article:8"), ("art. IV", "article:4"),
])
def test_candidates_preserve_literal_and_physical_offsets(literal, label):
    prefix = "Não 🧑🏿‍⚖️ pagar\u0301, salvo "
    value = unit(text=prefix + literal + ".", offset=31)
    candidates = reference_candidates(value)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.literal == literal and candidate.normalized_label == label
    assert candidate.start == 31 + len(prefix)
    assert candidate.end == candidate.start + len(literal)
    assert candidate.block_id == "u1:b"
    assert candidate.state == "pending" and candidate.target_unit_ids == []
    assert candidates == reference_candidates(value)


def test_physical_segments_exclude_synthetic_context_and_map_each_block():
    first, second = "Conforme Cláusula 2.", "Salvo Anexo III."
    spans = [SourceSpan(source_id="synthetic", block_id="b1", start=17, end=17 + len(first)),
             SourceSpan(source_id="synthetic", block_id="b2", start=8, end=8 + len(second))]
    value = unit(text=first)
    value = value.model_copy(update={
        "verbatim_text": first + "\n\n" + second, "block_ids": ["b1", "b2"], "spans": spans,
        "synthetic_context": "Cláusula 999",
        "text_map": [UnitTextSpan(unit_start=0, unit_end=len(first), source_span=spans[0]),
                     UnitTextSpan(unit_start=len(first), unit_end=len(first) + 2),
                     UnitTextSpan(unit_start=len(first) + 2, unit_end=len(first) + 2 + len(second),
                                  source_span=spans[1])]})
    refs = reference_candidates(value)
    assert [(r.block_id, r.start, r.literal) for r in refs] == [
        ("b1", 26, "Cláusula 2"), ("b2", 14, "Anexo III")]


@pytest.mark.parametrize("label,kind,expected", [
    ("Cláusula 2ª", "clause", ["clause:2"]),
    ("1", "clause", ["clause:1"]),
    ("§ 2º", "paragraph", ["paragraph:2"]),
    ("ANEXO IV", "annex", ["annex:4"]),
    ("ITEM A", "item", ["item:a"]),
    (None, "clause", []),
    ("Contrato com referência Cláusula 2", "title", []),
])
def test_structural_label_index_never_uses_body_mentions(label, kind, expected):
    assert reference_labels_for_blocks([block(label, kind)]) == expected


def test_exact_unique_label_adds_exception_dependency_without_approval():
    base = unit(text="Cláusula 1. Não pagar, salvo a Cláusula 2.", labels=["clause:1"])
    exception = unit("u2", "Cláusula 2. Exceção: pagar após a condição.", ["clause:2"])
    values = bind_references([base, exception])
    assert [u.unit_id for u in values] == ["u1", "u2"]
    assert values[0].closure_unit_ids == ["u2"]
    assert values[1].closure_unit_ids == []
    assert base.references == [] and base.closure_unit_ids == []
    for value in values:
        assert value.approval_state == "pending_review" and value.review_record_id is None
        assert all(r.state == "bound" and r.binding_origin == "exact_label" for r in value.references)
        validate_references(value, values)


@pytest.mark.parametrize("targets", [[], ["u2", "u3"]])
def test_missing_or_ambiguous_annex_stays_pending_until_explicit_review_mapping(targets):
    base = unit(text="Salvo o Anexo II.")
    others = [unit(name, "Exceção integral.", ["annex:2"]) for name in targets]
    values = bind_references([base, *others])
    assert values[0].references[0].state == "pending"
    assert values[0].closure_unit_ids == []
    with pytest.raises(ValueError):
        validate_references(values[0], values)
    reviewed = unit("reviewed", "Exceção integral.")
    values = bind_references([base, *others, reviewed], {
        reference_candidates(base)[0].reference_id: ["reviewed"]})
    assert values[0].references[0].binding_origin == "review_mapping"
    assert values[0].references[0].target_unit_ids == ["reviewed"]
    assert values[0].closure_unit_ids == ["reviewed"]
    validate_references(values[0], values)


def test_body_mention_is_never_a_structural_target():
    values = bind_references([unit(), unit("u2", "Foi mencionada Cláusula 2.")])
    assert values[0].references[0].state == "pending"


def test_binding_mapping_targets_units_not_blocks():
    base = unit()
    ref_id = reference_candidates(base)[0].reference_id
    with pytest.raises(ValueError):
        bind_references([base], {ref_id: [base.block_ids[0]]})
    with pytest.raises(ValueError):
        bind_references([base], {"unknown-reference": [base.unit_id]})
    with pytest.raises(ValueError):
        bind_references([base], {ref_id: []})


@pytest.mark.parametrize("mutation", ["candidate", "target", "closure", "literal", "id", "origin"])
def test_complete_reference_validation_rejects_removed_or_forged_evidence(mutation):
    base, target = bind_references([unit(), unit("u2", "Exceção integral.", ["clause:2"])])
    original_digest = unit_review_digest(base)
    refs = base.references
    if mutation == "candidate":
        changed = base.model_copy(update={"references": []})
    elif mutation == "closure":
        changed = base.model_copy(update={"closure_unit_ids": []})
    else:
        changes = {"target": {"target_unit_ids": ["missing"]},
                   "literal": {"literal": "Clausula 2"},
                   "id": {"reference_id": "forged"},
                   "origin": {"binding_origin": "candidate"}}[mutation]
        changed = base.model_copy(update={"references": [refs[0].model_copy(update=changes)]})
    assert unit_review_digest(changed) != original_digest
    with pytest.raises(ValueError):
        validate_references(changed, [changed, target])


def test_exact_binding_is_invalidated_by_new_duplicate_label_or_removed_label():
    values = bind_references([unit(), unit("u2", "Exceção integral.", ["clause:2"])])
    base, target = values
    with pytest.raises(ValueError):
        validate_references(base, [base, target, unit("u3", "Exceção alternativa.", ["clause:2"])])
    with pytest.raises(ValueError):
        validate_references(base, [base, target.model_copy(update={"reference_labels": []})])


def test_same_clause_paragraph_binding_creates_no_self_dependency():
    value = unit(text="Cláusula 1. § 2º: Condição do parágrafo segundo.",
                 labels=["clause:1", "paragraph:2"])
    bound = bind_references([value])[0]
    assert len(bound.references) == 3
    assert bound.closure_unit_ids == []
    assert all(r.target_unit_ids == [value.unit_id] for r in bound.references)
    validate_references(bound, {bound.unit_id: bound})


def test_exact_binding_is_local_to_instrument_and_source_version():
    values = bind_references([
        unit(), unit("u2", "Exceção integral.", ["clause:2"], source="other-source"),
        unit("u3", "Exceção integral.", ["clause:2"], instrument="other-instrument")])
    assert values[0].references[0].state == "pending"


def test_missing_text_map_cannot_claim_no_candidates():
    value = unit().model_copy(update={"text_map": []})
    with pytest.raises(ValueError):
        reference_candidates(value)


def test_duplicate_unit_ids_or_duplicate_candidates_fail_closed():
    value = unit()
    with pytest.raises(ValueError):
        bind_references([value, value])
    values = bind_references([value, unit("u2", "Condição.", ["clause:2"])])
    changed = values[0].model_copy(update={"references": values[0].references * 2})
    with pytest.raises(ValueError):
        validate_references(changed, values)


@pytest.mark.parametrize("literal,label", [
    ("cláusula anterior", "clause:relative:anterior"),
    ("cláusula seguinte", "clause:relative:seguinte"),
    ("parágrafo anterior", "paragraph:relative:anterior"),
    ("anexo", "annex:unidentified"),
    ("cláusula", "clause:unidentified"),
])
def test_relative_and_unidentified_references_require_explicit_mapping(literal, label):
    value = unit(text="Não pagar, conforme " + literal + ".", offset=5)
    ref = reference_candidates(value)[0]
    assert ref.literal == literal and ref.normalized_label == label
    assert ref.start == 5 + len("Não pagar, conforme ")
    bound = bind_references([value])[0]
    assert bound.references[0].state == "pending"
    with pytest.raises(ValueError):
        validate_references(bound, [bound])
    mapped = bind_references([value], {ref.reference_id: [value.unit_id]})[0]
    assert mapped.closure_unit_ids == []
    validate_references(mapped, [mapped])


@pytest.mark.parametrize("text", ["esta cláusula", "presente cláusula", "Anexo desconhecido"])
def test_bare_noun_is_never_silently_discarded(text):
    value = unit(text=text)
    refs = reference_candidates(value)
    assert len(refs) == 1 and refs[0].normalized_label.endswith(":unidentified")


def test_unidentified_label_cannot_suggest_exact_binding():
    value = unit(text="Conforme anexo.", labels=["annex:unidentified"])
    assert bind_references([value])[0].references[0].state == "pending"
    assert reference_labels_for_blocks([block("Cláusula anterior")]) == []


def test_nouns_inside_other_words_are_not_references():
    assert reference_candidates(unit(text="Itemsomething anexosomente paragrafotexto.")) == []


def test_mapping_crossinstrument_cannot_replace_relation_review():
    value = unit()
    foreign = unit("foreign", "Condição integral.", instrument="foreign-instrument")
    ref = reference_candidates(value)[0]
    with pytest.raises(ValueError):
        bind_references([value, foreign], {ref.reference_id: [foreign.unit_id]})


def test_decomposed_accents_keep_codepoint_offsets():
    literal = "CLA\u0301USULA DE\u0301CIMA PRIMEIRA"
    value = unit(text="🧾 " + literal + ".", offset=12)
    ref = reference_candidates(value)[0]
    assert ref.literal == literal and ref.normalized_label == "clause:11"
    assert ref.start == 14 and ref.end == 14 + len(literal)


def test_reference_lists_keep_every_explicit_label_and_offset():
    text = "Salvo as cláusulas 2, 3 e quarta."
    refs = reference_candidates(unit(text=text, offset=7))
    assert [(r.literal, r.normalized_label) for r in refs] == [
        ("cláusulas 2", "clause:2"), ("3", "clause:3"), ("quarta", "clause:4")]
    for ref in refs:
        assert text[ref.start - 7:ref.end - 7] == ref.literal
    values = bind_references([unit(text=text), *[
        unit(f"u{n}", "Condição integral.", [f"clause:{n}"]) for n in (2, 3, 4)]])
    assert values[0].closure_unit_ids == ["u2", "u3", "u4"]
    validate_references(values[0], values)


@pytest.mark.parametrize("text,expected", [
    ("Conforme §§ 1 a 3.", [("§§ 1 a 3", "paragraph:range:1:3")]),
    ("Salvo anexos I, II e III.", [("anexos I", "annex:1"), ("II", "annex:2"), ("III", "annex:3")]),
    ("Salvo cláusulas 1, 2 até 4 e 5.", [("cláusulas 1", "clause:1"), ("2 até 4", "clause:range:2:4"), ("5", "clause:5")]),
])
def test_multiple_reference_forms_keep_ranges_pending_and_literal_offsets(text, expected):
    refs = reference_candidates(unit(text=text, offset=11))
    assert [(ref.literal, ref.normalized_label) for ref in refs] == expected
    for ref in refs:
        assert text[ref.start - 11:ref.end - 11] == ref.literal


def test_range_label_cannot_be_bound_as_an_exact_target():
    values = bind_references([unit(text="Salvo cláusulas 2 a 4."),
                              unit("target", "Condição integral.", ["clause:range:2:4"])])
    assert values[0].references[0].state == "pending"
    forged = values[0].model_copy(update={"references": [values[0].references[0].model_copy(
        update={"state": "bound", "binding_origin": "exact_label", "target_unit_ids": ["target"]})],
        "closure_unit_ids": ["target"]})
    with pytest.raises(ValueError, match="reference_exact_label_not_unique"):
        validate_references(forged, [forged, values[1]])


@pytest.mark.parametrize("literal", ["cláusulas 2 a 4", "cláusulas 2 até 4", "cláusulas 2–4"])
def test_ranges_cannot_drop_unexpressed_middle_units(literal):
    base = unit(text="Salvo " + literal + ".")
    others = [unit(f"u{n}", "Condição integral.", [f"clause:{n}"]) for n in (2, 3, 4)]
    refs = reference_candidates(base)
    assert len(refs) == 1 and refs[0].literal == literal
    assert refs[0].normalized_label == "clause:range:2:4"
    bound = bind_references([base, *others])[0]
    assert bound.references[0].state == "pending"
    with pytest.raises(ValueError):
        validate_references(bound, [bound, *others])
    values = bind_references([base, *others], {refs[0].reference_id: ["u2", "u3", "u4"]})
    validate_references(values[0], values)
    assert values[0].closure_unit_ids == ["u2", "u3", "u4"]


@pytest.mark.parametrize("connector", ["e/ou", "e / ou", "ou/e", "E/OU", ";", ", bem como", "bem como"])
@pytest.mark.parametrize("kind,labels,expected", [
    ("cláusulas", ["2ª", "3ª"], ["clause:2", "clause:3"]),
    ("cláusulas", ["segunda", "terceira"], ["clause:2", "clause:3"]),
    ("anexos", ["II", "III"], ["annex:2", "annex:3"]),
    ("artigos", ["2", "3"], ["article:2", "article:3"]),
])
def test_compound_and_punctuated_connectors_keep_all_named_targets(connector, kind, labels, expected):
    text = f"Salvo {kind} {labels[0]} {connector} {labels[1]}."
    base = unit(text=text, offset=9)
    refs = reference_candidates(base)
    assert [ref.normalized_label for ref in refs] == expected
    for ref in refs:
        assert text[ref.start - 9:ref.end - 9] == ref.literal
    targets = [unit(f"target{n}", "Condição integral.", [label]) for n, label in enumerate(expected)]
    values = bind_references([base, *targets])
    assert values[0].closure_unit_ids == [target.unit_id for target in targets]
    validate_references(values[0], values)


def test_compound_connector_with_range_keeps_entire_interval_pending():
    text = "Salvo cláusulas 1, 2ª e/ou 3ª até 5ª, bem como 6ª."
    refs = reference_candidates(unit(text=text))
    assert [ref.normalized_label for ref in refs] == ["clause:1", "clause:2", "clause:range:3:5", "clause:6"]
    assert refs[2].literal == "3ª até 5ª"
    assert all(ref.state == "pending" for ref in refs)


@pytest.mark.parametrize("text", ["Salvo cláusulas 2ª e/ou seguintes.",
    "Salvo cláusulas 2ª, especialmente a 3ª.", "Salvo cláusulas 2ª e/ou\n\n",
    "Salvo cláusulas 2ª e/ou"])
def test_explicit_connector_without_recognized_label_remains_pending(text):
    base = unit(text=text)
    refs = reference_candidates(base)
    assert len(refs) == 2 and refs[1].normalized_label == "clause:unidentified"
    values = bind_references([base, unit("u2", "Condição integral.", ["clause:2"])])
    assert values[0].references[0].state == "bound"
    assert values[0].references[1].state == "pending"
    with pytest.raises(ValueError, match="reference_binding_unresolved"):
        validate_references(values[0], values)


@pytest.mark.parametrize("connector", ["e/ou", "e / ou", ";", ", bem como"])
@pytest.mark.parametrize("state", ["absent", "ambiguous"])
def test_compound_list_cannot_hide_absent_or_ambiguous_second_target(connector, state):
    base = unit(text=f"Salvo cláusulas 2ª {connector} 3ª.")
    targets = [unit("u2", "Condição.", ["clause:2"])]
    if state == "ambiguous":
        targets += [unit("u3", "Exceção.", ["clause:3"]), unit("u4", "Outra exceção.", ["clause:3"])]
    values = bind_references([base, *targets])
    assert len(values[0].references) == 2
    assert values[0].references[1].state == "pending"
    with pytest.raises(ValueError, match="reference_binding_unresolved"):
        validate_references(values[0], values)

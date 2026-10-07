import pytest
from datetime import date


def case(case_id, split, family="family1", answerable=True, **changes):
    from app.evaluation.dataset import EvaluationCase
    payload = dict(case_id=case_id, split=split, family_id=family, question=f"Pergunta sintética {case_id}?", answerable=answerable, expected_action="select" if answerable else "abstain", primary_unit_ids=["u1"] if answerable else [], source_route="docx")
    return EvaluationCase(**(payload | changes))


def test_related_or_duplicate_family_cannot_leak_between_splits():
    from app.evaluation.dataset import validate_dataset
    with pytest.raises(ValueError, match="família"):
        validate_dataset([case("c1", "development"), case("c2", "heldout")])


def test_unreviewed_skeleton_is_not_a_homologation_dataset():
    from app.evaluation.dataset import validate_dataset
    cases = [case("c1", "development"), case("c2", "heldout", family="family2")]
    validate_dataset(cases)
    with pytest.raises(ValueError, match="revisão"):
        validate_dataset(cases, acceptance=True)


def test_reviewed_but_insufficient_dataset_is_rejected():
    from app.evaluation.dataset import validate_dataset
    with pytest.raises(ValueError, match="50.*100"):
        validate_dataset([case("c1", "development", review_record_id="r1")], acceptance=True)


def test_complete_reserved_dataset_has_composition_gates():
    from app.evaluation.dataset import validate_dataset
    dev, heldout = complete_cases()
    validate_dataset(dev + heldout, acceptance=True)
    with pytest.raises(ValueError, match="identificadores"):
        validate_dataset(dev + [c.model_copy(update={"tags": []}) for c in heldout], acceptance=True)


def complete_cases():
    context = dict(review_record_id="synthetic-review", snapshot="snapshot1", authorized_scope="internal_common", expected_query_plan={"question_item_ids": ["q1"], "filters": {"instrument_ids": ["instrument1"]}}, instrument_ids=["instrument1"], origin="documental", required_evidence_spans=[{"source_id": "s1", "block_id": "b1", "start": 0, "end": 10}, {"source_id": "s1", "block_id": "b2", "start": 0, "end": 10}])
    dev = [case(f"d{i}", "development", family="dev", **context) for i in range(50)]
    heldout = []
    for i in range(100):
        tags = ["negative"] if i < 20 else ["ambiguous"] if i < 30 else ["conditions", "identifier"]
        payload = context | {"tags": tags, "required_closure_unit_ids": ["u2"] if i >= 30 else [], "required_evidence_spans": context["required_evidence_spans"] if i >= 30 else []}
        if 20 <= i < 30:
            payload["expected_action"] = "clarify"
        heldout.append(case(f"h{i}", "heldout", family="heldout", answerable=i >= 30, **payload))
    # Obligatory relation scenarios: assertions concern schema only, not real ground truth.
    from app.evaluation.dataset import REQUIRED_RELATION_SCENARIOS
    for scenario, position in zip(sorted(REQUIRED_RELATION_SCENARIOS), range(10, 20)):
        heldout[position] = heldout[position].model_copy(update={"scenario": scenario, "pertinent_relation_ids": ["rel1"]})
    temporal = next(i for i, c in enumerate(heldout) if c.scenario == "unproved_temporal_effect")
    heldout[temporal] = heldout[temporal].model_copy(update={"expected_query_plan": heldout[temporal].expected_query_plan.model_copy(update={"reference_date": date(2026, 1, 1)})})
    historical = next(i for i, c in enumerate(heldout) if c.scenario == "historical_request")
    heldout[historical] = case("historical", "heldout", family="heldout", **(context | {"scenario": "historical_request", "pertinent_relation_ids": ["rel1"], "expected_query_plan": {"question_item_ids": ["q1"], "evidence_scope": "original_text"}, "tags": []}))
    for scenario in ("addendum_no_reverse_reference", "partial_amendment", "termination", "relation_changed"):
        position = next(i for i, c in enumerate(heldout) if c.scenario == scenario)
        heldout[position] = case(scenario, "heldout", family="heldout", **(context | {"scenario": scenario, "pertinent_relation_ids": ["rel1"], "required_closure_unit_ids": ["u2"], "tags": []}))
    # Replacement of five negatives above preserves twenty independent negatives.
    for i in range(30, 35):
        heldout[i] = case(f"negative{i}", "heldout", family="heldout", answerable=False, **(context | {"tags": ["negative"], "required_evidence_spans": []}))
    return dev, heldout


def test_repeated_question_in_same_context_is_rejected():
    from app.evaluation.dataset import validate_dataset
    a, b = case("a", "development"), case("b", "development", question="Pergunta sintética a?")
    with pytest.raises(ValueError, match="repetida"):
        validate_dataset([a, b])


@pytest.mark.parametrize("changes", [{"snapshot": None}, {"authorized_scope": None}, {"expected_query_plan": None}, {"required_evidence_spans": []}])
def test_acceptance_requires_frozen_ground_truth_context(changes):
    from app.evaluation.dataset import validate_dataset
    dev, heldout = complete_cases()
    heldout[-1] = heldout[-1].model_copy(update=changes)
    with pytest.raises(ValueError):
        validate_dataset(dev + heldout, acceptance=True)


def test_critical_ambiguity_must_expect_clarification():
    from app.evaluation.dataset import validate_dataset
    dev, heldout = complete_cases()
    heldout[-1] = heldout[-1].model_copy(update={"tags": ["ambiguous"]})
    with pytest.raises(ValueError, match="ambiguidade"):
        validate_dataset(dev + heldout, acceptance=True)


def test_missing_mandatory_relation_scenario_rejects_acceptance():
    from app.evaluation.dataset import validate_dataset
    dev, heldout = complete_cases()
    with pytest.raises(ValueError, match="cenários"):
        validate_dataset(dev + [c.model_copy(update={"scenario": None}) for c in heldout], acceptance=True)


@pytest.mark.parametrize("a,b", [
    ({"instrument_ids": ["i1", "i2"]}, {"instrument_ids": ["i2", "i1"]}),
    ({"party_identifiers": ["AB.CDE.123/XY45-67", "123.456.789-00"]}, {"party_identifiers": ["12345678900", "abcde123xy4567"]}),
])
def test_equivalent_filters_cannot_inflate_distinct_questions(a, b):
    from app.evaluation.dataset import validate_dataset
    first = case("a", "development", expected_query_plan={"question_item_ids": ["q1"], "filters": a})
    second = case("b", "development", question=first.question, expected_query_plan={"question_item_ids": ["q2"], "version": "v2", "filters": b})
    with pytest.raises(ValueError, match="repetida"):
        validate_dataset([first, second])


def test_materially_different_selection_or_temporal_context_is_distinct():
    from app.evaluation.dataset import validate_dataset
    cases = [case(f"c{i}", "development", question="Pergunta igual?", expected_query_plan={"question_item_ids": ["q1"], **plan}) for i, plan in enumerate([
        {"filters": {"instrument_ids": ["i1"]}},
        {"filters": {"instrument_ids": ["i2"]}},
        {"filters": {"instrument_ids": ["i1"]}, "evidence_scope": "original_text"},
        {"filters": {"instrument_ids": ["i1"]}, "reference_date": "2026-01-01"},
    ])]
    validate_dataset(cases)


def test_absent_documental_route_can_have_separate_synthetic_fixture():
    from app.evaluation.dataset import validate_dataset
    dev, heldout = complete_cases()
    fixture = case("fixture-ocr", "heldout", family="fixture", source_route="ocr", origin="synthetic", review_record_id="fixture-review", snapshot="snapshot1", authorized_scope="internal_common", expected_query_plan={"question_item_ids": ["q1"]}, instrument_ids=["synthetic"], required_evidence_spans=[{"source_id": "synth", "block_id": "b1", "start": 0, "end": 10}])
    validate_dataset(dev + heldout + [fixture], acceptance=True, required_documental_routes={"docx"})


def test_declared_corpus_route_cannot_be_omitted_by_dataset():
    from app.evaluation.dataset import validate_dataset
    dev, heldout = complete_cases()
    with pytest.raises(ValueError, match="rota"):
        validate_dataset(dev + heldout, acceptance=True, required_documental_routes={"docx", "pdf"})


def test_documental_route_requires_five_reserved_cases():
    from app.evaluation.dataset import validate_dataset
    dev, heldout = complete_cases()
    dev[0] = dev[0].model_copy(update={"source_route": "pdf"})
    with pytest.raises(ValueError, match="rota"):
        validate_dataset(dev + heldout, acceptance=True)


def test_case_cannot_expect_selection_without_relevant_units():
    from app.evaluation.dataset import EvaluationCase
    with pytest.raises(ValueError):
        EvaluationCase(case_id="c1", split="development", family_id="f1", question="Q?", source_route="docx", answerable=True, expected_action="select")

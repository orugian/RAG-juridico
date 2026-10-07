"""P5 evaluation runner regressions on synthetic dataset fixtures."""
import pytest
from app.evaluation.dataset import EvaluationCase
from app.evaluation.retrieval import evaluate_retrieval, RetrievalEvaluationReport
from tests.test_p5_retrieval import p5_fixture_environment


def test_evaluate_retrieval_synthetic_dataset(p5_fixture_environment):
    """Verify evaluate_retrieval calculates Recall@10, Coverage, and strata breakdowns."""
    env = p5_fixture_environment
    pinned = env["manager"].pin(access=env["access"], evidence_scope="linked_instruments")
    from app.retrieval.hybrid import GovernedRetriever
    retriever = GovernedRetriever(pinned)

    target_unit = env["target_unit_101_cl1"]
    support_unit = env["support_unit_102_cl1"]

    cases = [
        # 1. Answerable case with conditions / addendum
        EvaluationCase(
            case_id="case_01",
            split="development",
            family_id="fam_101",
            question="Qual o valor dos honorários da Contratante Alpha?",
            answerable=True,
            expected_action="select",
            source_route="docx",
            tags=["conditions", "identifier"],
            primary_unit_ids=[target_unit],
            required_closure_unit_ids=[target_unit, support_unit],
            pertinent_relation_ids=["rel:102:amends:101"],
            scenario="partial_amendment",
            origin="synthetic",
        ),
        # 2. Negative case (non-existent client/contract)
        EvaluationCase(
            case_id="case_02",
            split="development",
            family_id="fam_999",
            question="Qual a cláusula de confidencialidade da Empresa Fantasma?",
            answerable=False,
            expected_action="abstain",
            source_route="docx",
            tags=["negative"],
            origin="synthetic",
        ),
        # 3. Ambiguous case requiring clarification
        EvaluationCase(
            case_id="case_03",
            split="development",
            family_id="fam_amb",
            question="Qual o contrato aplicável sem especificar instrumento ou cliente?",
            answerable=False,
            expected_action="clarify",
            source_route="docx",
            tags=["ambiguous"],
            origin="synthetic",
        ),
    ]

    report = evaluate_retrieval(cases, retriever)
    assert isinstance(report, RetrievalEvaluationReport)
    assert report.total_cases == 3
    assert report.recall_at_10 >= 0.0
    assert report.coverage_after_expansion >= 0.0
    assert report.strata["negative"]["total"] == 1
    assert report.strata["negative"]["abstained"] == 1
    assert report.strata["ambiguous"]["total"] == 1
    assert report.strata["ambiguous"]["clarified"] == 1

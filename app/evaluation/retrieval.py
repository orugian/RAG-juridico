"""Evaluation runner and metrics for governed retrieval on synthetic datasets.

Measures:
- Recall@10 pre-expansion (ACC-02)
- Coverage post-expansion
- Identifier selection accuracy (ACC-03)
- Negative abstention and ambiguous clarification accuracy (ACC-06)
- Reporting by strata/tags without tuning on heldout splits
"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from app.contracts import QueryPlan
from app.evaluation.dataset import EvaluationCase
from app.retrieval.hybrid import GovernedRetriever, plan_query


class RetrievalEvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total_cases: int
    answered_cases: int
    abstained_cases: int
    clarified_cases: int
    recall_at_10: float
    coverage_after_expansion: float
    identifier_accuracy: float
    strata: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


def evaluate_retrieval(
    cases: List[EvaluationCase],
    retriever: GovernedRetriever,
    *,
    k: int = 10,
    max_tokens: int = 4096,
) -> RetrievalEvaluationReport:
    """
    Run governed retrieval evaluation against a suite of EvaluationCase fixtures.
    """
    total = len(cases)
    if total == 0:
        return RetrievalEvaluationReport(
            total_cases=0,
            answered_cases=0,
            abstained_cases=0,
            clarified_cases=0,
            recall_at_10=0.0,
            coverage_after_expansion=0.0,
            identifier_accuracy=0.0,
            strata={},
        )

    answered_count = 0
    abstained_count = 0
    clarified_count = 0

    recall_numerators = 0
    recall_denominators = 0

    coverage_numerators = 0
    coverage_denominators = 0

    identifier_hits = 0
    identifier_totals = 0

    strata_counts: Dict[str, Dict[str, Any]] = {
        "negative": {"total": 0, "abstained": 0},
        "ambiguous": {"total": 0, "clarified": 0},
        "identifier": {"total": 0, "correct": 0},
        "conditions": {"total": 0, "covered": 0},
    }

    for case in cases:
        # 1. Determine query plan
        plan = case.expected_query_plan
        if plan is None:
            plan = plan_query(case.question)

        # 2. Execute retrieval
        result = retriever.retrieve(
            case.question,
            query_plan=plan,
            k=k,
            max_tokens=max_tokens,
        )

        if result.status == "answered":
            answered_count += 1
        elif result.status in ("abstained", "blocked"):
            abstained_count += 1
        elif result.status == "needs_clarification":
            clarified_count += 1

        # 3. Track Strata
        for tag in case.tags:
            if tag in strata_counts:
                strata_counts[tag]["total"] += 1
                if tag == "negative" and result.status in ("abstained", "blocked"):
                    strata_counts[tag]["abstained"] += 1
                elif tag == "ambiguous" and result.status == "needs_clarification":
                    strata_counts[tag]["clarified"] += 1

        # 4. Identifier accuracy (ACC-03)
        if "identifier" in case.tags:
            identifier_totals += 1
            if case.answerable:
                retrieved_unit_ids = {u.unit_id for u in result.citation_units}
                if set(case.primary_unit_ids).issubset(retrieved_unit_ids):
                    identifier_hits += 1
                    strata_counts["identifier"]["correct"] += 1
            else:
                if result.status in ("abstained", "blocked") and len(result.evidence_chunks) == 0:
                    identifier_hits += 1
                    strata_counts["identifier"]["correct"] += 1

        # 5. Recall@10 pre-expansion (ACC-02)
        if case.answerable and case.primary_unit_ids:
            recall_denominators += len(case.primary_unit_ids)
            # Find which primary units appeared in top-k chunks before closure expansion
            top_unit_ids = {c.unit_id for c in result.evidence_chunks}
            recall_numerators += len(set(case.primary_unit_ids).intersection(top_unit_ids))

        # 6. Coverage post-expansion
        if case.answerable and case.required_closure_unit_ids:
            coverage_denominators += len(case.required_closure_unit_ids)
            closed_unit_ids = {u.unit_id for u in result.citation_units}
            coverage_numerators += len(set(case.required_closure_unit_ids).intersection(closed_unit_ids))
            if set(case.required_closure_unit_ids).issubset(closed_unit_ids):
                if "conditions" in case.tags:
                    strata_counts["conditions"]["covered"] += 1

    macro_recall = (recall_numerators / recall_denominators) if recall_denominators > 0 else 1.0
    macro_coverage = (coverage_numerators / coverage_denominators) if coverage_denominators > 0 else 1.0
    id_accuracy = (identifier_hits / identifier_totals) if identifier_totals > 0 else 1.0

    return RetrievalEvaluationReport(
        total_cases=total,
        answered_cases=answered_count,
        abstained_cases=abstained_count,
        clarified_cases=clarified_count,
        recall_at_10=macro_recall,
        coverage_after_expansion=macro_coverage,
        identifier_accuracy=id_accuracy,
        strata=strata_counts,
    )

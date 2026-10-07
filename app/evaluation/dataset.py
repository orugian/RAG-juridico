"""Dataset skeleton and acceptance gates, not generated legal ground truth."""
from typing import Literal
import re

from pydantic import Field, model_validator

from app.contracts import Identifier, StrictContract, QueryPlan, SourceSpan

REQUIRED_RELATION_SCENARIOS = frozenset({"addendum_no_reverse_reference", "partial_amendment", "termination", "conflicting_modifiers", "unresolved_association", "quarantined_modifier", "historical_request", "unproved_temporal_effect", "closure_over_budget", "relation_changed"})


class EvaluationCase(StrictContract):
    case_id: Identifier
    split: Literal["development", "heldout"]
    # Human grouping includes near duplicates and every linked instrument.
    family_id: Identifier
    question: str = Field(min_length=1, max_length=10000)
    answerable: bool
    expected_action: Literal["select", "abstain", "clarify"]
    source_route: Literal["docx", "libreoffice", "pdf", "ocr"]
    tags: list[Literal["negative", "conditions", "ambiguous", "identifier", "addendum", "termination", "historical", "conflict", "quarantined_modifier"]] = Field(default_factory=list)
    primary_unit_ids: list[Identifier] = Field(default_factory=list)
    required_closure_unit_ids: list[Identifier] = Field(default_factory=list)
    expected_fact_ids: list[Identifier] = Field(default_factory=list)
    review_record_id: Identifier | None = None
    origin: Literal["synthetic", "documental"] = "synthetic"
    snapshot: Identifier | None = None
    authorized_scope: Literal["internal_common"] | None = None
    expected_query_plan: QueryPlan | None = None
    instrument_ids: list[Identifier] = Field(default_factory=list)
    required_evidence_spans: list[SourceSpan] = Field(default_factory=list)
    pertinent_relation_ids: list[Identifier] = Field(default_factory=list)
    scenario: Literal["addendum_no_reverse_reference", "partial_amendment", "termination", "conflicting_modifiers", "unresolved_association", "quarantined_modifier", "historical_request", "unproved_temporal_effect", "closure_over_budget", "relation_changed"] | None = None

    @model_validator(mode="after")
    def expected_support(self):
        if self.answerable != (self.expected_action == "select"):
            raise ValueError("Ação esperada incompatível com resposta suportada")
        if self.answerable and not self.primary_unit_ids:
            raise ValueError("Gabarito respondível exige unidades relevantes")
        if not self.answerable and (self.primary_unit_ids or self.required_closure_unit_ids or self.expected_fact_ids):
            raise ValueError("Caso controlado não pode trazer prova para seleção")
        return self


def validate_dataset(cases: list[EvaluationCase], *, acceptance: bool = False, required_documental_routes: set[str] | None = None) -> None:
    cases = [EvaluationCase.model_validate(case.model_dump()) for case in cases]
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("IDs de casos duplicados")
    families = {}
    distinct = set()
    for case in cases:
        plan = case.expected_query_plan
        query_context = (plan.filters.selection_mode, tuple(sorted(plan.filters.instrument_ids)), tuple(sorted(plan.filters.party_identifiers)), plan.evidence_scope, plan.reference_date) if plan else None
        key = (case.family_id, case.snapshot, query_context, re.sub(r"\s+", " ", case.question).strip().casefold())
        if key in distinct:
            raise ValueError("Pergunta repetida no mesmo contexto/família")
        distinct.add(key)
        if case.family_id in families and families[case.family_id] != case.split:
            raise ValueError("Vazamento de família entre desenvolvimento e teste reservado")
        families[case.family_id] = case.split
    if not acceptance:
        return
    if any(not case.review_record_id for case in cases):
        raise ValueError("Gabarito exige revisão independente registrada")
    development = [case for case in cases if case.split == "development" and case.origin == "documental"]
    heldout = [case for case in cases if case.split == "heldout" and case.origin == "documental"]
    if len(development) < 50 or len(heldout) < 100:
        raise ValueError("Dataset exige >=50 desenvolvimento e >=100 teste reservado")
    if any(not case.snapshot or not case.authorized_scope or not case.expected_query_plan for case in cases):
        raise ValueError("Aceite exige snapshot, escopo autorizado e plano esperado")
    if len({case.snapshot for case in cases}) != 1:
        raise ValueError("Snapshot congelado deve coincidir entre os conjuntos")
    for case in cases:
        if case.answerable and (not case.instrument_ids or not case.required_evidence_spans):
            raise ValueError("Gabarito respondível exige instrumentos e spans de suporte")
        if "ambiguous" in case.tags and case.expected_action != "clarify":
            raise ValueError("ambiguidade crítica exige esclarecimento")
        if "negative" in case.tags and case.expected_action != "abstain":
            raise ValueError("Caso negativo exige abstenção")
        distinct_spans = {(span.source_id, span.block_id, span.start, span.end) for span in case.required_evidence_spans}
        if "conditions" in case.tags and (not case.answerable or not case.required_closure_unit_ids or len(distinct_spans) < 2):
            raise ValueError("Condições exigem fechamento e múltiplos spans")
        if "identifier" in case.tags and not (case.expected_query_plan.filters.instrument_ids or case.expected_query_plan.filters.party_identifiers):
            raise ValueError("Seleção por identificador exige filtro esperado")
        if case.scenario in REQUIRED_RELATION_SCENARIOS and not case.pertinent_relation_ids:
            raise ValueError("Cenário de relação exige vínculos pertinentes")
        if case.scenario in {"conflicting_modifiers", "unresolved_association", "quarantined_modifier", "unproved_temporal_effect", "closure_over_budget"} and case.expected_action == "select":
            raise ValueError("Relação/efeito material pendente exige resposta controlada")
        if case.scenario == "historical_request" and case.expected_query_plan.evidence_scope != "original_text":
            raise ValueError("Consulta histórica exige escopo original_text")
        if case.scenario == "unproved_temporal_effect" and case.expected_query_plan.reference_date is None:
            raise ValueError("Cenário temporal exige data explícita esperada")
        if case.scenario in {"addendum_no_reverse_reference", "partial_amendment", "termination", "relation_changed"} and case.answerable and not case.required_closure_unit_ids:
            raise ValueError("Efeito relacionado respondível exige fechamento material")
    scenarios = {case.scenario for case in cases if case.split == "heldout"}
    if not REQUIRED_RELATION_SCENARIOS <= scenarios:
        raise ValueError("Faltam cenários obrigatórios de relações")
    requirements = {"identifier": (20, "identificadores"), "negative": (20, "negativos"), "conditions": (20, "condições"), "ambiguous": (10, "ambiguidades")}
    for tag, (minimum, label) in requirements.items():
        if sum(tag in case.tags for case in heldout) < minimum:
            raise ValueError(f"Dataset insuficiente para {label}")
    if sum(case.answerable for case in heldout) < 60:
        raise ValueError("Teste reservado exige >=60 casos respondíveis")
    routes = {case.source_route for case in cases if case.origin == "documental"}
    if required_documental_routes is not None:
        if not required_documental_routes <= {"docx", "libreoffice", "pdf", "ocr"}:
            raise ValueError("Inventário de rotas inválido")
        routes |= required_documental_routes
    if any(sum(case.source_route == route for case in heldout) < 5 for route in routes):
        raise ValueError("Cada rota presente exige >=5 casos reservados")

"""Versioned internal contracts. References are validated against the corpus in P6.

Schema validation is not human approval, source verification or legal adjudication.
No model-generated text is admitted by GenerationCandidate.
"""
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.identifiers import normalize_identifier
from app.ingestion.schemas import ContractParty

Identifier = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9:_./-]+$")]
ApprovalState = Literal["pending_review", "approved", "quarantined", "excluded", "failed"]


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    @model_validator(mode="after")
    def unique_references(self):
        for key, value in self.__dict__.items():
            if isinstance(value, list) and value and all(isinstance(item, str) for item in value):
                if len(value) != len(set(value)):
                    raise ValueError(f"Referências repetidas em {key}")
        return self


class AccessContext(StrictContract):
    """Server-created technical principal; shared credentials do not name a person."""
    principal_id: Identifier
    credential_id: Identifier
    corpus_scope: Literal["internal_common"] = "internal_common"
    permissions: list[Literal["query", "operate"]] = Field(min_length=1, max_length=2)
    policy_epoch: int = Field(ge=0)
    access_scope_digest: Identifier


class SelectionFilters(StrictContract):
    instrument_ids: list[Identifier] = Field(default_factory=list, max_length=20)
    party_identifiers: list[str] = Field(default_factory=list, max_length=20)
    selection_mode: Literal["union", "intersection"] = "intersection"

    @field_validator("party_identifiers")
    @classmethod
    def canonical_identifiers(cls, values):
        return [normalize_identifier(value) for value in values]


class QueryPlan(StrictContract):
    version: Identifier = "query-plan-v1"
    question_item_ids: list[Identifier] = Field(min_length=1, max_length=20)
    filters: SelectionFilters = Field(default_factory=SelectionFilters)
    evidence_scope: Literal["original_text", "linked_instruments"] = "linked_instruments"
    reference_date: date | None = None


class SourceIdentity(StrictContract):
    """Exact local source/derivation identity; never an approval by itself."""
    source_id: Identifier
    instrument_id: Identifier
    doc_version: int = Field(ge=1)
    file_id: Identifier
    file_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    snapshot: Identifier
    configuration_version: Identifier
    parser_name: Identifier
    parser_version: Identifier
    conversion_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class SourceSpan(StrictContract):
    source_id: Identifier
    block_id: Identifier
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    page: int | None = Field(default=None, ge=1)
    xml_part: str | None = Field(default=None, max_length=256)
    paragraph_index: int | None = Field(default=None, ge=0)
    cell_index: int | None = Field(default=None, ge=0)
    body_child_index: int | None = Field(default=None, ge=0)
    row_index: int | None = Field(default=None, ge=0)
    column_index: int | None = Field(default=None, ge=0)
    page_block_index: int | None = Field(default=None, ge=0)
    bbox: tuple[float, float, float, float] | None = None

    @model_validator(mode="after")
    def valid_interval(self):
        if self.end <= self.start:
            raise ValueError("Span vazio ou invertido")
        return self


class CitationLocation(StrictContract):
    label: str = Field(min_length=1, max_length=500)
    clause: str | None = None
    paragraph: str | None = None
    annex: str | None = None
    page: int | None = Field(default=None, ge=1)


class UnitTextSpan(StrictContract):
    """Map complete canonical text; a null origin denotes a block separator."""
    unit_start: int = Field(ge=0)
    unit_end: int = Field(gt=0)
    source_span: SourceSpan | None = None

    @model_validator(mode="after")
    def interval(self):
        if self.unit_end <= self.unit_start:
            raise ValueError("Mapa textual vazio ou invertido")
        if self.source_span and self.unit_end - self.unit_start != self.source_span.end - self.source_span.start:
            raise ValueError("Mapa textual deve preservar todos os caracteres da origem")
        return self


class UnitReference(StrictContract):
    """Literal reference candidate; a binding still requires scoped unit review."""
    reference_id: Identifier
    block_id: Identifier
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    literal: str = Field(min_length=1)
    kind: Literal["clause", "paragraph", "annex", "item", "article"]
    normalized_label: str = Field(min_length=1)
    state: Literal["pending", "bound"] = "pending"
    target_unit_ids: list[Identifier] = Field(default_factory=list)
    binding_origin: Literal["candidate", "exact_label", "review_mapping"] = "candidate"

    @model_validator(mode="after")
    def reference_interval_and_binding(self):
        if self.end - self.start != len(self.literal):
            raise ValueError("Referência deve conservar literal e offsets exatos")
        if (self.state == "bound") != bool(self.target_unit_ids):
            raise ValueError("Referência vinculada exige alvos; pendente não admite alvos")
        return self


class CitationUnit(StrictContract):
    unit_id: Identifier
    instrument_id: Identifier
    verbatim_text: str = Field(min_length=1)
    source_ids: list[Identifier] = Field(min_length=1)
    block_ids: list[Identifier] = Field(min_length=1)
    spans: list[SourceSpan] = Field(min_length=1)
    location: CitationLocation
    closure_unit_ids: list[Identifier] = Field(default_factory=list)
    approval_state: ApprovalState = "pending_review"
    review_record_id: Identifier | None = None
    source_identities: list[SourceIdentity] = Field(default_factory=list)
    contract_title: str = ""
    parties: list[ContractParty] = Field(default_factory=list)
    synthetic_context: str = ""
    parent_unit_id: Identifier | None = None
    text_map: list[UnitTextSpan] = Field(default_factory=list)
    risk_codes: list[str] = Field(default_factory=list)
    references: list[UnitReference] = Field(default_factory=list)
    reference_labels: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def provenance_and_decision(self):
        if self.approval_state == "approved" and not self.review_record_id:
            raise ValueError("Unidade aprovada exige decisão registrada")
        if self.unit_id in self.closure_unit_ids:
            raise ValueError("Dependência direta da própria unidade")
        if any(span.source_id not in self.source_ids or span.block_id not in self.block_ids for span in self.spans):
            raise ValueError("Span fora das fontes/blocos declarados")
        if self.text_map:
            cursor = 0
            mapped = []
            for entry in self.text_map:
                if entry.unit_start != cursor or entry.unit_end > len(self.verbatim_text):
                    raise ValueError("Mapa textual deve cobrir integralmente a unidade")
                if entry.source_span is None:
                    if self.verbatim_text[entry.unit_start:entry.unit_end] != "\n\n":
                        raise ValueError("Separador sem origem deve ser explícito")
                else:
                    mapped.append(entry.source_span)
                cursor = entry.unit_end
            if cursor != len(self.verbatim_text) or mapped != self.spans:
                raise ValueError("Mapa textual diverge do literal ou spans")
        return self


class EvidenceChunk(StrictContract):
    chunk_id: Identifier
    unit_id: Identifier
    source_id: Identifier
    instrument_id: Identifier
    doc_version: int = Field(ge=1)
    file_id: Identifier
    file_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    parser_name: Identifier
    parser_version: Identifier
    configuration_version: Identifier
    derivation_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    block_ids: list[Identifier] = Field(min_length=1)
    spans: list[SourceSpan] = Field(min_length=1)
    verbatim_text: str = Field(min_length=1)
    text_search: str = Field(min_length=1)
    synthetic_context: str = ""
    parties: list[ContractParty] = Field(min_length=1)
    location: CitationLocation
    parent_id: Identifier | None = None
    approval_state: ApprovalState = "pending_review"
    review_record_id: Identifier | None = None
    unit_start: int = Field(default=0, ge=0)
    unit_end: int | None = Field(default=None, gt=0)
    token_count: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def source_and_approval(self):
        if any(span.source_id != self.source_id or span.block_id not in self.block_ids for span in self.spans):
            raise ValueError("Proveniência inconsistente")
        if self.approval_state == "approved" and not self.review_record_id:
            raise ValueError("Chunk aprovado exige decisão")
        return self


class InstrumentRelation(StrictContract):
    relation_id: Identifier
    from_instrument_id: Identifier
    to_instrument_id: Identifier
    relation_type: Literal["amends", "supplements", "terminates", "supersedes"]
    affected_unit_ids: list[Identifier] = Field(default_factory=list)
    support_unit_ids: list[Identifier] = Field(default_factory=list)
    support_spans: list[SourceSpan] = Field(default_factory=list)
    expressed_date: date | None = None
    state: Literal["proposed", "approved", "rejected", "conflicted"] = "proposed"
    review_record_id: Identifier | None = None
    snapshot: Identifier
    configuration_version: Identifier

    @model_validator(mode="after")
    def reviewed_effect(self):
        if self.from_instrument_id == self.to_instrument_id:
            raise ValueError("Relação não pode ligar instrumento a si mesmo")
        if self.state in {"approved", "rejected"} and not self.review_record_id:
            raise ValueError("Decisão de relação exige registro de revisão")
        if self.state == "approved" and (not self.affected_unit_ids or not self.support_unit_ids):
            raise ValueError("Efeito aprovado exige alcance e suporte")
        return self


class RelationResolution(StrictContract):
    family_id: Identifier
    snapshot: Identifier
    registry_version: Identifier
    registry_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    relation_ids: list[Identifier] = Field(default_factory=list)
    state: Literal["pending", "resolved", "conflicted"] = "pending"
    material_risk_source_ids: list[Identifier] = Field(default_factory=list)
    review_record_id: Identifier | None = None

    @model_validator(mode="after")
    def adjudicated(self):
        if self.state == "resolved" and (not self.review_record_id or self.material_risk_source_ids):
            raise ValueError("Família resolvida exige decisão e ausência de risco pendente")
        return self


class RetrievalRank(StrictContract):
    evidence_id: Identifier
    component: Literal["dense", "bm25", "rrf", "exact", "closure"]
    rank: int = Field(ge=1)
    score: float = Field(allow_inf_nan=False)


class RetrievalResult(StrictContract):
    generation_id: Identifier
    query_plan_version: Identifier
    access_scope_digest: Identifier
    retrieval_config_version: Identifier
    relation_registry_version: Identifier
    evidence_ids: list[Identifier] = Field(default_factory=list)
    ranks: list[RetrievalRank] = Field(default_factory=list)
    effective_filters: SelectionFilters
    requested_question_item_ids: list[Identifier] = Field(min_length=1)
    covered_question_item_ids: list[Identifier] = Field(default_factory=list)
    retrieval_mode: Literal["hybrid", "exact", "degraded"]

    @model_validator(mode="after")
    def covered_references(self):
        if not set(self.covered_question_item_ids) <= set(self.requested_question_item_ids):
            raise ValueError("Cobertura contém item não solicitado")
        if any(rank.evidence_id not in self.evidence_ids for rank in self.ranks):
            raise ValueError("Rank de evidência ausente")
        return self


class UnitSelection(StrictContract):
    unit_id: Identifier
    question_item_ids: list[Identifier] = Field(min_length=1, max_length=20)


class GenerationCandidate(StrictContract):
    action: Literal["select", "abstain", "clarify"]
    selections: list[UnitSelection] = Field(default_factory=list, max_length=40)
    approved_fact_ids: list[Identifier] = Field(default_factory=list, max_length=40)
    clarification_code: Literal["instrument_ambiguous", "temporal_ambiguous", "selection_ambiguous", "question_scope"] | None = None

    @model_validator(mode="after")
    def consistent_action(self):
        if self.action == "select" and not self.selections:
            raise ValueError("Seleção exige unidade citável")
        if self.action != "select" and (self.selections or self.approved_fact_ids):
            raise ValueError("Abstenção/esclarecimento não admitem seleções")
        if (self.action == "clarify") != (self.clarification_code is not None):
            raise ValueError("Código de esclarecimento exclusivo e obrigatório em clarify")
        if len({selection.unit_id for selection in self.selections}) != len(self.selections):
            raise ValueError("Unidade selecionada mais de uma vez")
        return self


class CitationParty(StrictContract):
    name: str = Field(min_length=1)
    role: str = Field(min_length=1)


class Citation(StrictContract):
    citation_id: Identifier
    contract_id: Identifier
    contract_title: str = Field(min_length=1)
    document_version: int = Field(ge=1)
    parties: list[CitationParty] = Field(min_length=1)
    location: CitationLocation
    quote: str = Field(min_length=1)
    source_id: Identifier
    evidence_id: Identifier

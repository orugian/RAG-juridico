"""P6 rendering: four elements from canonical fields only, instruments kept separate, no free text."""
import pytest

from app.contracts import (
    CitationLocation, CitationUnit, EvidenceChunk, InstrumentRelation, SourceIdentity, SourceSpan,
)
from app.grounding import ResolvedProof
from app.ingestion.schemas import ContractParty, PartyRole
from app.rendering import (
    ABSTENTION_TEMPLATE, build_citation, controlled_text, render_answer, subject_of,
)
from app.terms import Term

SHA = "c" * 64


def identity(doc, source):
    return SourceIdentity(source_id=source, instrument_id=f"doc:{doc}", doc_version=2, file_id=f"f{doc}", file_hash=SHA,
                          snapshot="snap", configuration_version="cfg1", parser_name="docx_parser", parser_version="1.0.0")


def unit(doc, label, text, parties, *, title, order):
    source = f"doc:{doc}:v2:file:f{doc}"
    return CitationUnit(
        unit_id=f"unit:{doc}:{order}", instrument_id=f"doc:{doc}", verbatim_text=text, source_ids=[source],
        block_ids=[f"b{order}"], spans=[SourceSpan(source_id=source, block_id=f"b{order}", start=0, end=len(text),
                                                   body_child_index=order)],
        location=CitationLocation(label=label, clause=label), contract_title=title, parties=parties,
        source_identities=[identity(doc, source)],
    )


def chunk_of(u):
    return EvidenceChunk(
        chunk_id=f"chunk:{u.unit_id}", unit_id=u.unit_id, source_id=u.source_ids[0], instrument_id=u.instrument_id,
        doc_version=2, file_id="f", file_hash=SHA, parser_name="docx_parser", parser_version="1.0.0",
        configuration_version="cfg1", derivation_key=SHA, block_ids=u.block_ids, spans=u.spans,
        verbatim_text=u.verbatim_text, text_search=u.verbatim_text, parties=u.parties, location=u.location,
        parent_id=u.unit_id, unit_start=0, unit_end=len(u.verbatim_text),
    )


ALPHA = ContractParty(name="Alpha Serviços Ltda", role=PartyRole.CONTRATANTE, clean_identifier="11222333000144")
BETA = ContractParty(name="Beta Participações", role=PartyRole.CONTRATADA)
GAMMA = ContractParty(name="Gamma Inovações Ltda", role=PartyRole.LOCADOR, clean_identifier="12ABC34501DE67")
DELTA = ContractParty(name="Delta Locadora S.A.", role=PartyRole.LOCATARIO)
EXCEPTION_TEXT = "Cláusula 3ª - O foro é o da comarca de São Paulo, salvo se a parte não residir no Brasil.\n\nParágrafo único - Não se aplica à rescisão."


def proof_for(units, *, selected, relations=(), warnings=()):
    return ResolvedProof(
        units=tuple(sorted(units, key=lambda u: u.unit_id)),
        roles={u.unit_id: "selected" if u.unit_id in selected else "closure" for u in units},
        item_ids={u.unit_id: ("q1",) if u.unit_id in selected else () for u in units},
        relation_ids={u.unit_id: tuple(r.relation_id for r in relations if u.unit_id in r.affected_unit_ids + r.support_unit_ids)
                      for u in units},
        chunks={u.unit_id: (chunk_of(u),) for u in units}, relations=tuple(relations), warnings=tuple(warnings), token_count=10)


@pytest.fixture
def base_and_amendment():
    base = unit(101, "Cláusula 1ª", "Cláusula 1ª - Os honorários são de R$ 50.000,00.", [ALPHA, BETA],
                title="Contrato de Honorários Alpha", order=1)
    amendment = unit(102, "Cláusula 1ª", "Cláusula 1ª - Os honorários passam a R$ 75.000,00.", [ALPHA, BETA],
                     title="Primeiro Aditivo", order=1)
    relation = InstrumentRelation(
        relation_id="rel:102:101", from_instrument_id="doc:102", to_instrument_id="doc:101", relation_type="amends",
        affected_unit_ids=[base.unit_id], support_unit_ids=[amendment.unit_id], support_spans=amendment.spans,
        state="approved", review_record_id="rec:1", snapshot="snap", configuration_version="cfg1")
    return base, amendment, relation


def test_four_elements_are_rendered_per_unit_and_quote_is_exact_canonical_text():
    third = unit(201, "Cláusula 3ª", EXCEPTION_TEXT, [GAMMA, DELTA], title="Locação Gamma Delta", order=3)
    text, items = render_answer(proof_for([third], selected={third.unit_id}), generation_id="gen-1", evidence_scope="linked_instruments")
    assert "Locação Gamma Delta" in text and "doc:201" in text and "versão 2" in text
    assert "Gamma Inovações Ltda (locador; identificador 12ABC34501DE67)" in text and "Delta Locadora S.A. (locatario)" in text
    assert "Localização: Cláusula 3ª" in text
    assert "> Cláusula 3ª - O foro é o da comarca de São Paulo, salvo se a parte não residir no Brasil." in text
    assert "> Parágrafo único - Não se aplica à rescisão." in text and ">\n" in text
    assert "Andrade Advogados" not in text
    [item] = items
    citation = item.citation
    assert citation.quote == EXCEPTION_TEXT and citation.contract_id == "doc:201" and citation.document_version == 2
    assert [(p.name, p.role) for p in citation.parties] == [("Gamma Inovações Ltda", "locador"), ("Delta Locadora S.A.", "locatario")]
    assert citation.location.label == "Cláusula 3ª" and citation.evidence_id == "chunk:unit:201:3" and citation.source_id.startswith("doc:201")
    assert "Origem: doc:201:v2:file:f201; blocos: b3" in text


def test_base_and_modifier_are_separate_sections_without_consolidation(base_and_amendment):
    base, amendment, relation = base_and_amendment
    text, items = render_answer(
        proof_for([base, amendment], selected={base.unit_id}, relations=[relation]),
        generation_id="gen-1", evidence_scope="linked_instruments")
    first, second = text.index("INSTRUMENTO 1"), text.index("INSTRUMENTO 2")
    assert first < text.index("R$ 50.000,00") < second < text.index("R$ 75.000,00")
    assert "Contrato de Honorários Alpha [doc:101" in text[first:second] and "Primeiro Aditivo [doc:102" in text[second:]
    assert "tipo registrado: amends" in text and "cada instrumento permanece separado" in text
    assert "não consolida" in text and "prevalência" in text
    assert [(i.unit_id, i.role) for i in items] == [(base.unit_id, "selected"), (amendment.unit_id, "closure")]
    assert items[0].relation_ids == ["rel:102:101"] and items[1].relation_ids == ["rel:102:101"]
    for forbidden in ("passa a valer", "vigente a partir", "prevalece", "substitui a cláusula", "total de R$"):
        assert forbidden not in text


def test_historical_scope_carries_factual_warnings_only(base_and_amendment):
    base, *_ = base_and_amendment
    text, _ = render_answer(
        proof_for([base], selected={base.unit_id}, warnings=["historical_text_not_effect", "known_modifier:rel:102:101"]),
        generation_id="gen-1", evidence_scope="original_text")
    assert "texto histórico do instrumento" in text
    assert "Existe modificador registrado (rel:102:101)" in text and "não indica regra vigente" in text


def test_partial_term_absence_is_reported_for_presented_excerpts_only(base_and_amendment):
    base, *_ = base_and_amendment
    text, _ = render_answer(proof_for([base], selected={base.unit_id}), generation_id="gen-1",
                            evidence_scope="linked_instruments", absent=[Term("plágio", "plagio")])
    assert "«plágio» não constam nos trechos apresentados" in text


def test_item_labels_are_used_without_changing_ids(base_and_amendment):
    base, *_ = base_and_amendment
    text, items = render_answer(proof_for([base], selected={base.unit_id}), generation_id="gen-1",
                                evidence_scope="linked_instruments", item_labels={"q1": "valor dos honorários"})
    assert "indicada para: valor dos honorários" in text and items[0].question_item_ids == ["q1"]


def test_third_party_citation_never_presumes_the_firm_as_party():
    third = unit(201, "Cláusula 1ª", "Cláusula 1ª - Locação.", [GAMMA, DELTA], title="Locação", order=1)
    citation = build_citation(third, chunk_of(third))
    assert all("Andrade" not in p.name for p in citation.parties)


def test_citation_requires_a_resolvable_source_identity():
    bare = unit(201, "Cláusula 1ª", "Cláusula 1ª - Locação.", [GAMMA], title="Locação", order=1)
    bare = bare.model_copy(update={"source_identities": []})
    with pytest.raises(ValueError, match="unit_source_identity_missing"):
        build_citation(bare, chunk_of(bare))


def test_abstention_uses_the_categorical_template_with_question_subject():
    text = controlled_text("insufficient_evidence", question="Qual a multa por plágio contratual?")
    assert text == ABSTENTION_TEMPLATE.format(subject=subject_of("Qual a multa por plágio contratual?"))
    assert text.startswith("A informação solicitada sobre ") and text.endswith(
        "não foi localizada nos contratos disponíveis na base de dados do escritório Andrade Advogados.")
    assert "multa" in text and "plágio" in text
    assert "o assunto consultado" in controlled_text("insufficient_evidence", question="Qual o que existe?")


@pytest.mark.parametrize("reason", ["invalid_candidate", "unresolved_relation", "budget_exceeded", "ambiguous_instrument", "ambiguous_time"])
def test_controlled_messages_never_echo_document_or_question_text(reason):
    text = controlled_text(reason, question="CNPJ 11.222.333/0001-44 honorários R$ 50.000,00")
    assert "11.222.333" not in text and "50.000" not in text and len(text) > 40


def test_subject_is_joined_in_portuguese_not_with_a_dangling_comma():
    assert subject_of("Qual a arbitragem internacional?") == "arbitragem e internacional"
    assert subject_of("Existe multa, foro e plágio?") == "multa, foro e plágio"
    assert subject_of("Qual a multa?") == "multa"

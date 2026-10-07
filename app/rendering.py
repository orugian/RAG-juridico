"""Backend rendering of the Regra dos 4 Elementos (P6).

Every sentence is a fixed template or a canonical field; no model text is ever rendered.
Instruments stay in separate sections: no consolidation, precedence, validity or effect is stated.
"""
from typing import Mapping, Sequence

from app.answer_contracts import ClarificationOption, ProofItem
from app.contracts import Citation, CitationParty, CitationUnit, EvidenceChunk
from app.grounding import ResolvedProof
from app.terms import Term, content_terms

RENDERER_VERSION = "p6-renderer-v1"
ABSTENTION_TEMPLATE = ("A informação solicitada sobre {subject} não foi localizada nos contratos "
                       "disponíveis na base de dados do escritório Andrade Advogados.")
GENERIC_SUBJECT = "o assunto consultado"
CONTROLLED_TEXT = {
    "invalid_candidate": "A seleção de trechos não pôde ser validada contra a prova documental e foi retida; "
                         "nenhuma conclusão contratual foi emitida.",
    "unresolved_relation": "Existe relação documental entre instrumentos ainda não resolvida ou revisada; sem fechamento "
                           "aprovado não é possível apresentar o trecho como condição aplicável.",
    "budget_exceeded": "A prova necessária excede o orçamento de contexto disponível; nenhuma resposta parcial foi emitida.",
    "ambiguous_instrument": "A consulta admite mais de um instrumento ou seleção; indique o instrumento ou as partes desejadas.",
    "ambiguous_time": "A consulta traz referência temporal sem prova de efeitos documentais; indique se deseja o texto "
                      "histórico do instrumento, que não indica vigência ou efeitos atuais.",
}
WARNING_TEXT = {
    "known_modifier": "Existe modificador registrado ({0}) que não integra esta apresentação histórica; o texto é "
                      "histórico e não indica regra vigente.",
    "historical_text_not_effect": "Texto histórico do instrumento; não indica vigência nem efeitos atuais.",
    "relation_unresolved": "A relação {0} está pendente ou em conflito; não há prova de efeito.",
    "relation_review_unavailable": "A revisão da relação {0} não está disponível; não há prova de efeito.",
    "modifier_proof_unavailable": "A prova do modificador {0} não está disponível.",
    "family_unresolved": "A família documental {0} não está resolvida; não há fechamento aprovado.",
}


def _join(words: Sequence[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " e " + words[-1]


def subject_of(question: str, *, limit: int = 5) -> str:
    terms = content_terms(question)[:limit]
    return _join([term.display for term in terms]) if terms else GENERIC_SUBJECT


def abstention_text(question: str) -> str:
    return ABSTENTION_TEMPLATE.format(subject=subject_of(question))


def controlled_text(reason_code: str, *, question: str, options: Sequence[ClarificationOption] = ()) -> str:
    if reason_code == "insufficient_evidence":
        return abstention_text(question)
    text = CONTROLLED_TEXT[reason_code]
    if options:
        text += "\nOpções disponíveis no acervo autorizado:\n" + "\n".join(
            f"- {option.contract_title} [{option.instrument_id}]" for option in options)
    return text


def _identity(unit: CitationUnit, source_id: str):
    return next((i for i in unit.source_identities if i.source_id == source_id), None)


def build_citation(unit: CitationUnit, chunk: EvidenceChunk) -> Citation:
    source_id = unit.source_ids[0]
    identity = _identity(unit, source_id)
    if identity is None:
        raise ValueError("unit_source_identity_missing")
    return Citation(
        citation_id="cit:" + unit.unit_id, contract_id=unit.instrument_id, contract_title=unit.contract_title,
        document_version=identity.doc_version,
        parties=[CitationParty(name=p.name, role=p.role.value) for p in unit.parties],
        location=unit.location, quote=unit.verbatim_text, source_id=source_id, evidence_id=chunk.chunk_id)


def _position(unit: CitationUnit):
    span = unit.spans[0]
    return (span.page or 0, span.body_child_index or 0, span.paragraph_index or 0, span.page_block_index or 0,
            span.start, unit.unit_id)


def _quote(text: str) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in text.split("\n"))


def _party(party) -> str:
    identifier = f"; identificador {party.clean_identifier}" if party.clean_identifier else ""
    return f"{party.name} ({party.role.value}{identifier})"


def _warning(code: str) -> str:
    key, _, argument = code.partition(":")
    template = WARNING_TEXT.get(key)
    return template.format(argument) if template else f"Aviso do sistema: {code}."


def render_answer(proof: ResolvedProof, *, generation_id: str, evidence_scope: str,
                  item_labels: Mapping[str, str] | None = None,
                  absent: Sequence[Term] = ()) -> tuple[str, list[ProofItem]]:
    labels = item_labels or {}
    by_instrument: dict[str, list[CitationUnit]] = {}
    for unit in proof.units:
        by_instrument.setdefault(unit.instrument_id, []).append(unit)
    order = sorted(by_instrument, key=lambda key: (
        not any(proof.roles[u.unit_id] == "selected" for u in by_instrument[key]), key))
    lines = [
        f"Resposta extrativa fundamentada no acervo contratual (geração {generation_id}; "
        f"escopo {'texto histórico do instrumento' if evidence_scope == 'original_text' else 'instrumentos vinculados'}).",
        "Os trechos abaixo são transcrições literais, apresentadas por instrumento; o sistema não consolida "
        "instrumentos, não indica prevalência entre eles nem declara vigência, assinatura ou efeitos jurídicos.",
    ]
    items: list[ProofItem] = []
    titles = {}
    for number, instrument_id in enumerate(order, 1):
        units = sorted(by_instrument[instrument_id], key=_position)
        first = units[0]
        titles[instrument_id] = first.contract_title
        identity = _identity(first, first.source_ids[0])
        lines += ["", f"INSTRUMENTO {number} — {first.contract_title} [{instrument_id}, versão {identity.doc_version}]",
                  "Partes: " + "; ".join(_party(p) for p in first.parties)]
        for index, unit in enumerate(units, 1):
            role = proof.roles[unit.unit_id]
            citation = build_citation(unit, proof.chunks[unit.unit_id][0])
            items.append(ProofItem(unit_id=unit.unit_id, role=role, question_item_ids=list(proof.item_ids[unit.unit_id]),
                                   relation_ids=list(proof.relation_ids[unit.unit_id]), citation=citation))
            answered = ", ".join(labels.get(i, i) for i in proof.item_ids[unit.unit_id])
            lines += ["", f"Prova {number}.{index} — Localização: {unit.location.label} "
                          f"({'selecionada' if role == 'selected' else 'fechamento obrigatório'}"
                          f"{'; indicada para: ' + answered if answered else ''})",
                      f"Origem: {', '.join(unit.source_ids)}; blocos: {', '.join(unit.block_ids)}",
                      "Transcrição literal:", _quote(unit.verbatim_text)]
    if proof.relations:
        lines += ["", "Relações documentais revisadas entre os instrumentos apresentados (cada instrumento permanece separado):"]
        locations = {u.unit_id: u.location.label for u in proof.units}
        for relation in sorted(proof.relations, key=lambda r: r.relation_id):
            affected = ", ".join(locations.get(uid, "unidade não apresentada") for uid in relation.affected_unit_ids)
            lines.append(f"- {titles.get(relation.from_instrument_id, relation.from_instrument_id)} "
                         f"[{relation.from_instrument_id}] — tipo registrado: {relation.relation_type} — "
                         f"{titles.get(relation.to_instrument_id, relation.to_instrument_id)} "
                         f"[{relation.to_instrument_id}]; unidades afetadas: {affected}.")
    notes = [_warning(code) for code in proof.warnings]
    if absent:
        notes.append("Os termos da consulta «" + ", ".join(t.display for t in absent) + "» não constam nos trechos apresentados.")
    if notes:
        lines += ["", "Avisos:"] + [f"- {note}" for note in notes]
    return "\n".join(lines), items

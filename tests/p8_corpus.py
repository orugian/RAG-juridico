"""Physical P8 oracle fixed before parsing: synthetic, no documentary/human ground truth.

Only DOCX serialization is reused from the accepted fixture. Ingestion, publication,
retrieval, grounding and transport are the actual public P3B–P7 interfaces.
"""
from dataclasses import dataclass
from pathlib import Path

from app.contracts import (AccessContext, Citation, CitationLocation, CitationParty, InstrumentRelation,
                           QueryPlan, RelationResolution, SelectionFilters, SourceIdentity)
from app.generation import AnswerRequest
from app.ingestion.docx_parser import extract_docx_blocks
from app.ingestion.evidence import prepare_citation_units, build_evidence_chunks
from app.ingestion.review_store import ReviewStore, review_digest, unit_review_digest
from app.ingestion.schemas import ContractMetadata, ContractParty, ParsedDocument, PartyRole
from app.retrieval.generation_contracts import GenerationBundle, GenerationConfig
from app.retrieval.generations import GenerationManager
from app.retrieval.lexical import lexical_profile
from app.retrieval.policy import SQLitePolicyJournal
from tests.p6_corpus import P6Environment, _write_docx
import hashlib

SNAPSHOT, CONFIG = "p8-synthetic-snapshot", "p8-synthetic-v1"


@dataclass(frozen=True)
class InstrumentOracle:
    doc: int
    title: str
    kind: str
    paragraphs: tuple[str, ...]
    parties: tuple[tuple[str, str, str | None], ...]
    dependencies: tuple[tuple[int, int], ...] = ()


# These literals/roles are the oracle, not read back from parser or answer.
ALPHA = ("Alpha Cliente Sintético Ltda", "contratante", "11222333000144")
FIRM = ("Andrade Advogados", "contratada", None)
THIRD = ("Omega Tecnologia Terceira S.A.", "contratada", "55666777000188")
INSTRUMENTS = (
    InstrumentOracle(801, "Contrato Sintético de Honorários Alpha", "Contrato de Honorários", (
        "Cláusula 1ª - Os honorários devidos por Alpha são de R$ 50.000,00.",
        "Cláusula 2ª - Em caso de rescisão imotivada, multa de 10% sobre o saldo remanescente.",
        "Cláusula 3ª - As partes manterão confidencialidade, salvo a exceção da Cláusula 4ª.",
        "Cláusula 4ª - Constitui exceção à confidencialidade a divulgação exigida por autoridade judicial.",
    ), (ALPHA, FIRM), ((2, 3),)),
    InstrumentOracle(802, "Primeiro Aditivo Sintético Alpha", "Aditivo", (
        "Cláusula 1ª - Fica ajustado o valor dos honorários para R$ 75.000,00.",
    ), (ALPHA, FIRM)),
    InstrumentOracle(803, "Segundo Aditivo Sintético Alpha", "Aditivo", (
        "Cláusula 1ª - Fica ajustado o pagamento dos honorários em três parcelas iguais.",
    ), (ALPHA, FIRM)),
    InstrumentOracle(804, "Distrato Sintético Alpha", "Distrato", (
        "Cláusula 1ª - As partes declaram distratado o contrato de honorários identificado neste instrumento.",
    ), (ALPHA, FIRM)),
    InstrumentOracle(901, "Contrato Sintético Alpha com Terceiro Omega", "Contrato de Prestação", (
        "Cláusula 1ª - Omega prestará serviços de tecnologia à Contratante Alpha por R$ 12.000,00 mensais.",
        "Cláusula 2ª - Em caso de indisponibilidade, Omega notificará Alpha por escrito.",
    ), (ALPHA, THIRD)),
    InstrumentOracle(1001, "Contrato Sintético de Locação Gamma Delta", "Contrato de Locação", (
        "Cláusula 1ª - Locação comercial Gamma pelo valor mensal de R$ 8.500,00.",
    ), (("Gamma Cliente Sintético Ltda", "locatario", "12ABC34501DE67"),
        ("Delta Locadora Sintética S.A.", "locador", "77666555000122"))),
)
ORACLE = {spec.doc: spec for spec in INSTRUMENTS}
LINKS = ((802, 801, "amends", (0,)), (803, 801, "amends", (0,)),
         (804, 801, "terminates", (0, 1, 2, 3)))


def _review(ledger, source, scope="source", subject_id=None, subject_digest=None):
    return ledger.record(source_id=source.source_id, snapshot=source.snapshot, file_hash=source.file_hash,
                         configuration_version=source.configuration_version, decision="approved",
                         reviewer="p8-synthetic-fixture-not-human", reason="p8 synthetic test only",
                         scope=scope, subject_id=subject_id, subject_digest=subject_digest)


def build_p8_environment(root, *, encoder, budget, vector_store, scenario="approved"):
    if scenario not in {"approved", "pending", "conflicted"}:
        raise ValueError("unknown synthetic relation scenario")
    root = Path(root)
    directory = root / "sources"
    directory.mkdir(parents=True)
    ledger = ReviewStore(root / "authority" / "reviews.sqlite")
    journal = SQLitePolicyJournal(root / "authority" / "policy.sqlite", initialize=True, journal_id="p8-test")
    built_by_doc, sources, paths = {}, {}, {}
    for spec in INSTRUMENTS:
        path = directory / f"synthetic_{spec.doc}.docx"
        _write_docx(path, spec.paragraphs)
        blocks = extract_docx_blocks(path, doc_id=spec.doc, doc_version=1)
        parsed = ParsedDocument(doc_id=spec.doc, doc_version=1, file_id=f"p8f{spec.doc}", file_path=str(path),
                                file_hash=hashlib.sha256(path.read_bytes()).hexdigest(), parser_name="docx_parser",
                                parser_version="1.0.0", blocks=blocks,
                                metadata=ContractMetadata(formal_title=spec.title, instrument_type=spec.kind,
                                                          parties=[ContractParty(name=name, role=PartyRole(role),
                                                                                 clean_identifier=identifier,
                                                                                 is_law_firm=(name == "Andrade Advogados"))
                                                                   for name, role, identifier in spec.parties]))
        source = SourceIdentity(source_id=f"doc:{spec.doc}:v1:file:p8f{spec.doc}", instrument_id=f"doc:{spec.doc}",
                                doc_version=1, file_id=parsed.file_id, file_hash=parsed.file_hash, snapshot=SNAPSHOT,
                                configuration_version=CONFIG, parser_name=parsed.parser_name,
                                parser_version=parsed.parser_version)
        deps = {blocks[a].block_id: [blocks[b].block_id] for a, b in spec.dependencies}
        candidates = prepare_citation_units(parsed, source, original_path=path, dependencies=deps)
        _review(ledger, source)
        _review(ledger, source, "parties", source.source_id, review_digest({
            "contract_title": spec.title, "parties": [p.model_dump(mode="json") for p in parsed.metadata.parties]}))
        ids = {u.unit_id: _review(ledger, source, "unit", u.unit_id, unit_review_digest(u)).record_id for u in candidates}
        built = build_evidence_chunks(parsed, source, ledger=ledger, budget=budget, original_path=path,
                                      unit_review_ids=ids, dependencies=deps)
        assert [u.verbatim_text for u in built.units] == list(spec.paragraphs)
        assert [u.location.label for u in built.units] == [f"Cláusula {i + 1}ª" for i in range(len(spec.paragraphs))]
        built_by_doc[spec.doc], sources[spec.doc], paths[source.source_id] = built, source, path
    relations, relation_sources = [], {}
    for origin, target, kind, affected in LINKS:
        state = scenario if origin == 803 else "approved"
        relation = InstrumentRelation(relation_id=f"p8rel:{origin}:{kind}:{target}",
                                      from_instrument_id=f"doc:{origin}", to_instrument_id=f"doc:{target}",
                                      relation_type=kind, state="proposed" if state == "pending" else state,
                                      snapshot=SNAPSHOT, configuration_version=CONFIG,
                                      affected_unit_ids=[built_by_doc[target].units[i].unit_id for i in affected],
                                      support_unit_ids=[built_by_doc[origin].units[0].unit_id],
                                      support_spans=built_by_doc[origin].units[0].spans,
                                      review_record_id="placeholder" if state == "approved" else None)
        if state == "approved":
            relation.review_record_id = _review(ledger, sources[origin], "relation", relation.relation_id,
                                                review_digest(relation.model_dump(mode="json", exclude={"review_record_id"}))).record_id
        relations.append(relation)
        relation_sources[relation.relation_id] = sources[origin].source_id
    resolutions, family_sources = [], {}
    for anchor, members in ((801, (801, 802, 803, 804)), (901, (901,)), (1001, (1001,))):
        family_relations = [r.relation_id for r in relations if r.from_instrument_id in {f"doc:{d}" for d in members}]
        state = "resolved" if anchor != 801 or scenario == "approved" else scenario
        resolution = RelationResolution(family_id=f"p8fam:{anchor}", snapshot=SNAPSHOT, registry_version="v1",
                                        registry_digest=ledger.relation_state_digest(), relation_ids=family_relations,
                                        state=state, review_record_id="placeholder" if state == "resolved" else None)
        if state == "resolved":
            resolution.review_record_id = _review(ledger, sources[anchor], "family", resolution.family_id,
                                                  review_digest(resolution.model_dump(mode="json", exclude={"review_record_id"}))).record_id
        resolutions.append(resolution)
        family_sources[resolution.family_id] = sources[anchor].source_id
    bundle = GenerationBundle(sources=list(sources.values()),
                              units=[u for b in built_by_doc.values() for u in b.units],
                              chunks=[c for b in built_by_doc.values() for c in b.chunks],
                              reviews=[r for s in sources.values() for r in ledger.history(s.source_id)],
                              relations=relations, resolutions=resolutions, relation_review_sources=relation_sources,
                              family_review_sources=family_sources)
    config = GenerationConfig(configuration_version=CONFIG, embedding_identity=encoder.identity,
                              embedding_dimension=1024, lexical_identity=lexical_profile(), synthetic=False)
    manager = GenerationManager(root / "indexes", configuration=config, embeddings=encoder, vector_store=vector_store,
                                ledger=ledger, journal=journal, journal_id="p8-test")
    manager.build("gen-p8-01", bundle, source_paths=paths)
    access = AccessContext(principal_id="p8-fixture-operator", credential_id="p8-fixture-credential",
                           permissions=["query", "operate"], policy_epoch=journal.snapshot().policy_epoch,
                           access_scope_digest="p8-fixture-scope")
    manager.promote("gen-p8-01", access=access)
    return P6Environment(manager, "gen-p8-01", bundle, ledger, journal, access, paths, encoder, vector_store, budget)


@dataclass(frozen=True)
class MatrixCase:
    case_id: str
    question: str
    selection: tuple[tuple[int, int], ...]
    expected: tuple[tuple[int, int], ...]
    instruments: tuple[str, ...] = ("doc:801",)
    scope: str = "linked_instruments"
    status: str = "answered"
    reason: str | None = None
    party_ids: tuple[str, ...] = ()
    selection_mode: str = "intersection"
    reference_date: str | None = None


# The declared termination affects all four base units. Its mandatory closure
# therefore includes them, plus both amendments that affect the fee unit. This
# expectation follows LINKS, never the resolver/response under evaluation.
FAMILY_FEES = ((801, 0), (801, 1), (801, 2), (801, 3), (802, 0), (803, 0), (804, 0))
MATRIX = (
    MatrixCase("linked_fees", "Qual o valor dos honorários de Alpha?", ((801, 0),), FAMILY_FEES),
    MatrixCase("historical_fees", "Qual o valor dos honorários de Alpha?", ((801, 0),),
               ((801, 0), (801, 1), (801, 2), (801, 3)), scope="original_text"),
    MatrixCase("conditions", "Qual a multa em caso de rescisão?", ((801, 1),), FAMILY_FEES),
    MatrixCase("exception", "Qual a confidencialidade e a exceção?", ((801, 2),), FAMILY_FEES),
    MatrixCase("third_party", "Qual o valor dos serviços de tecnologia de Omega?", ((901, 0),), ((901, 0),), ("doc:901",)),
    MatrixCase("party_identifier", "Qual o valor dos serviços de tecnologia de Omega?", ((901, 0),), ((901, 0),), (),
               party_ids=("55666777000188",)),
    # Primary universe: authorized U ∩ parties(ANY/ALL) ∩ instruments(I).
    # Linked closure may add material reviewed modifiers outside I, not arbitrary roots.
    MatrixCase("union", "Quais os valores dos honorários de Alpha e da locação de Gamma?",
               ((801, 0), (1001, 0)),
               ((1001, 0), (801, 0), (801, 1), (801, 2), (801, 3), (802, 0), (803, 0), (804, 0)), (),
               party_ids=("11222333000144", "12ABC34501DE67"), selection_mode="union"),
    MatrixCase("party_intersection_absent", "Quais os valores dos honorários de Alpha e da locação de Gamma?",
               (), (), (), status="abstained", reason="insufficient_evidence",
               party_ids=("11222333000144", "12ABC34501DE67")),
    MatrixCase("cross_dimension_absent", "Qual o valor dos serviços de tecnologia de Omega?", (), (), ("doc:missing",),
               status="abstained", reason="insufficient_evidence", party_ids=("55666777000188",), selection_mode="union"),
    MatrixCase("instrument_clamp_union", "Qual o valor dos honorários de Alpha?", ((801, 0),), FAMILY_FEES,
               party_ids=("11222333000144", "12ABC34501DE67"), selection_mode="union"),
    MatrixCase("alphanumeric_party", "Qual o valor da locação de Gamma?", ((1001, 0),), ((1001, 0),), (),
               party_ids=("12ABC34501DE67",)),
    MatrixCase("intersection_absent", "Qual o valor dos serviços de tecnologia de Omega?", (), (), ("doc:missing",),
               status="abstained", reason="insufficient_evidence", party_ids=("55666777000188",)),
    MatrixCase("missing_instrument", "Qual o valor dos honorários?", (), (), ("doc:missing",),
               status="abstained", reason="insufficient_evidence"),
    MatrixCase("missing_term", "Qual a regra de criptomoedas?", ((801, 0),), (),
               status="abstained", reason="insufficient_evidence"),
    MatrixCase("ambiguous", "Qual o contrato sem especificar as partes?", (), (), (),
               status="needs_clarification", reason="ambiguous_instrument"),
    MatrixCase("temporal", "Qual o valor dos honorários de Alpha?", (), (),
               status="needs_clarification", reason="ambiguous_time", reference_date="2026-01-01"),
)
CASES = {case.case_id: case for case in MATRIX}


def case_request(case_id):
    case = CASES[case_id]
    plan = QueryPlan(question_item_ids=["q1"], evidence_scope=case.scope, reference_date=case.reference_date,
                     filters=SelectionFilters(instrument_ids=list(case.instruments), party_identifiers=list(case.party_ids),
                                              selection_mode=case.selection_mode))
    return AnswerRequest(question=case.question, plan=plan).model_dump(mode="json")


def unit_id(env, doc, paragraph):
    return env.unit(f"doc:{doc}", f"Cláusula {paragraph + 1}ª")


def expected_citations(env, refs):
    result = []
    for doc, index in refs:
        spec = ORACLE[doc]
        uid = unit_id(env, doc, index)
        result.append(Citation(citation_id="cit:" + uid, contract_id=f"doc:{doc}", contract_title=spec.title,
                               document_version=1, parties=[CitationParty(name=n, role=r) for n, r, _ in spec.parties],
                               location=CitationLocation(label=f"Cláusula {index + 1}ª", clause=f"Cláusula {index + 1}ª"),
                               quote=spec.paragraphs[index], source_id=f"doc:{doc}:v1:file:p8f{doc}",
                               evidence_id=env.chunk_ids(uid)[0]))
    return result


class OracleSelector:
    name = "p8-deterministic-synthetic-selector"

    def __init__(self, selections):
        self.selections = tuple(selections)

    def generate(self, request):
        return {"action": "select", "approved_fact_ids": [], "clarification_code": None,
                "selections": [{"unit_id": uid, "question_item_ids": ["q1"]} for uid in self.selections]}


def select_units(env, *refs):
    return OracleSelector([unit_id(env, doc, paragraph) for doc, paragraph in refs])


def expected_response_text(env, case):
    """Independent golden template from declared literals; never invokes renderer.

    Technical unit/block IDs locate generated physical proof. None of the returned
    answer text, citations, resolver output or parsed quote/parties defines this oracle.
    """
    if case.status != "answered":
        subjects = {"missing_term": "regra e criptomoedas", "missing_instrument": "honorários",
                    "intersection_absent": "serviços, tecnologia e Omega",
                    "cross_dimension_absent": "serviços, tecnologia e Omega",
                    "party_intersection_absent": "honorários, Alpha, locação e Gamma"}
        if case.reason == "insufficient_evidence":
            return (f"A informação solicitada sobre {subjects[case.case_id]} não foi localizada nos contratos "
                    "disponíveis na base de dados do escritório Andrade Advogados.")
        if case.reason == "ambiguous_time":
            return ("A consulta traz referência temporal sem prova de efeitos documentais; indique se deseja o texto "
                    "histórico do instrumento, que não indica vigência ou efeitos atuais.")
        if case.reason == "ambiguous_instrument":
            return ("A consulta admite mais de um instrumento ou seleção; indique o instrumento ou as partes desejadas."
                    "\nOpções disponíveis no acervo autorizado:\n" + "\n".join(
                        f"- {spec.title} [doc:{spec.doc}]" for spec in sorted(INSTRUMENTS, key=lambda s: f"doc:{s.doc}")))
        raise ValueError("unknown controlled oracle")
    historical = case.scope == "original_text"
    lines = [
        f"Resposta extrativa fundamentada no acervo contratual (geração {env.generation_id}; "
        f"escopo {'texto histórico do instrumento' if historical else 'instrumentos vinculados'}).",
        "Os trechos abaixo são transcrições literais, apresentadas por instrumento; o sistema não consolida "
        "instrumentos, não indica prevalência entre eles nem declara vigência, assinatura ou efeitos jurídicos.",
    ]
    selected = set(case.selection)
    docs = sorted({doc for doc, _ in case.expected},
                  key=lambda doc: (not any(d == doc for d, _ in selected), f"doc:{doc}"))
    for number, doc in enumerate(docs, 1):
        spec = ORACLE[doc]
        parties = [f"{name} ({role}{'; identificador ' + identifier if identifier else ''})"
                   for name, role, identifier in spec.parties]
        lines += ["", f"INSTRUMENTO {number} — {spec.title} [doc:{doc}, versão 1]", "Partes: " + "; ".join(parties)]
        for index, (_, paragraph) in enumerate(sorted(ref for ref in case.expected if ref[0] == doc), 1):
            uid = unit_id(env, doc, paragraph)
            unit = next(u for u in env.bundle.units if u.unit_id == uid)
            primary = (doc, paragraph) in selected
            role = "selecionada; indicada para: q1" if primary else "fechamento obrigatório"
            lines += ["", f"Prova {number}.{index} — Localização: Cláusula {paragraph + 1}ª ({role})",
                      f"Origem: doc:{doc}:v1:file:p8f{doc}; blocos: {', '.join(unit.block_ids)}",
                      "Transcrição literal:", "> " + spec.paragraphs[paragraph]]
    present_relations = [link for link in LINKS if link[0] in docs and link[1] in docs]
    if present_relations and not historical:
        lines += ["", "Relações documentais revisadas entre os instrumentos apresentados (cada instrumento permanece separado):"]
        for origin, target, kind, affected in present_relations:
            labels = ", ".join(f"Cláusula {i + 1}ª" for i in affected)
            lines.append(f"- {ORACLE[origin].title} [doc:{origin}] — tipo registrado: {kind} — "
                         f"{ORACLE[target].title} [doc:{target}]; unidades afetadas: {labels}.")
    if historical:
        lines += ["", "Avisos:", "- Texto histórico do instrumento; não indica vigência nem efeitos atuais."]
        for origin, target, kind, _ in LINKS:
            lines.append(f"- Existe modificador registrado (p8rel:{origin}:{kind}:{target}) que não integra esta "
                         "apresentação histórica; o texto é histórico e não indica regra vigente.")
    return "\n".join(lines)

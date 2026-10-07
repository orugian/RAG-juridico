"""Reusable physical governed generation for P6 tests (synthetic, offline, deterministic)."""
from dataclasses import dataclass, field
import hashlib
from pathlib import Path
from threading import Lock
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZipInfo

from app.contracts import AccessContext, InstrumentRelation, RelationResolution, SourceIdentity
from app.ingestion.docx_parser import extract_docx_blocks
from app.ingestion.evidence import QwenTokenBudget, build_evidence_chunks, prepare_citation_units
from app.ingestion.review_store import ReviewStore, review_digest, unit_review_digest
from app.ingestion.schemas import ContractMetadata, ContractParty, ParsedDocument, PartyRole
from app.retrieval.generation_contracts import GenerationBundle, GenerationConfig
from app.retrieval.generations import GenerationManager
from app.retrieval.lexical import lexical_profile
from app.retrieval.policy import SQLitePolicyJournal
from app.retrieval.vector_store import ChromaVectorStore

SNAPSHOT, CONFIGURATION = "p6-snap", "cfg1"
_BODY = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{}'
         '</w:body></w:document>')
_RESOLUTION_STATE = {"approved": "resolved", "proposed": "pending", "conflicted": "conflicted"}
_SCENARIOS = {"standard": "approved", "pending_relation": "proposed", "conflicted_relation": "conflicted"}


class SyntheticEncoder:
    identity = {"provider": "synthetic-sha256", "dimension": 3}

    def __init__(self):
        self.calls = []

    def embed_documents(self, texts):
        self.calls.extend(texts)
        return [[byte / 255 for byte in hashlib.sha256(text.encode()).digest()[:3]] for text in texts]

    def embed_query(self, text):
        return self.embed_documents([text])[0]


class SyntheticBudget(QwenTokenBudget):
    def __init__(self):
        from app.embeddings.qwen import QwenProfile

        class Tokenizer:
            def encode(self, text, **kwargs):
                return list(text)

        self._tokenizer = Tokenizer()
        self._lock = Lock()
        self.profile = QwenProfile()
        self._identity = {"synthetic_test_only": True, "profile": self.profile.specification}


@dataclass
class InstrumentSpec:
    doc_id: int
    title: str
    instrument_type: str
    paragraphs: list[str]
    parties: list[ContractParty]
    dependencies: dict[int, list[int]] = field(default_factory=dict)


@dataclass
class RelationSpec:
    from_doc: int
    to_doc: int
    relation_type: str
    affected: list[int]
    support: list[int]


_ALPHA = ContractParty(name="Alpha Serviços Ltda", role=PartyRole.CONTRATANTE, clean_identifier="11222333000144")
_BETA = ContractParty(name="Beta Participações", role=PartyRole.ANUENTE, clean_identifier="12345678901")
_FIRM = ContractParty(name="Andrade Advogados", role=PartyRole.CONTRATADA, is_law_firm=True)
_EPSILON = ContractParty(name="Epsilon Consultoria Ltda", role=PartyRole.CONTRATANTE, clean_identifier="55666777000188")
_ZETA = ContractParty(name="Zeta Tributos S.A.", role=PartyRole.CONTRATADA, clean_identifier="66777888000199")


def _corpus():
    specs = [
        InstrumentSpec(101, "Contrato de Honorários Alpha Beta", "Contrato de Honorários", [
            "Cláusula 1ª - Os honorários devidos pela Contratante Alpha são de R$ 50.000,00.",
            "Cláusula 2ª - Em caso de rescisão imotivada, multa de 10% sobre o saldo remanescente.",
            "Cláusula 3ª - Fica eleito o foro de Belo Horizonte; as partes manterão confidencialidade, "
            "salvo se a divulgação for exigida por lei, não se admitindo outra exceção senão a da Cláusula 4ª.",
            "Cláusula 4ª - Constitui exceção à confidencialidade a divulgação exigida por autoridade judicial.",
        ], [_ALPHA, _BETA, _FIRM], {2: [3]}),
        InstrumentSpec(102, "Primeiro Aditivo ao Contrato Alpha Beta", "Aditivo", [
            "Cláusula 1ª - Fica ajustado o valor dos honorários para R$ 75.000,00.",
        ], [_ALPHA, _BETA]),
        InstrumentSpec(201, "Contrato de Locação Comercial Gamma Delta", "Contrato de Locação", [
            "Cláusula 1ª - Locação comercial do imóvel pelo valor mensal de R$ 12.000,00.",
            "Cláusula 2ª - Os encargos do imóvel ficam a cargo da locatária.",
        ], [ContractParty(name="Gamma Inovações Ltda", role=PartyRole.LOCATARIO, clean_identifier="12ABC34501DE67"),
            ContractParty(name="Delta Locadora S.A.", role=PartyRole.LOCADOR)]),
        InstrumentSpec(301, "Contrato de Prestação Epsilon Zeta", "Contrato de Prestação", [
            "Cláusula 1ª - Prestação de serviços de consultoria tributária à Contratante.",
            "Cláusula 2ª - Pagamento mensal conforme proposta comercial anexa ao processo.",
        ], [_EPSILON, _ZETA]),
        InstrumentSpec(302, "Distrato do Contrato Epsilon Zeta", "Distrato", [
            "Cláusula 1ª - Ficam extintas, por distrato, as obrigações de consultoria tributária.",
        ], [_EPSILON, _ZETA]),
    ]
    relations = [RelationSpec(102, 101, "amends", [0], [0]), RelationSpec(302, 301, "terminates", [0], [0])]
    return specs, relations


def _write_docx(path, paragraphs):
    info = ZipInfo("word/document.xml", date_time=(1980, 1, 1, 0, 0, 0))
    body = "".join(f"<w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p>" for text in paragraphs)
    with ZipFile(path, "w") as archive:
        archive.writestr(info, _BODY.format(body))


def _record(ledger, source, *, scope="source", subject_id=None, subject_digest=None, reason="p6 synthetic"):
    return ledger.record(
        source_id=source.source_id, snapshot=source.snapshot, file_hash=source.file_hash,
        configuration_version=source.configuration_version, decision="approved", reviewer="p6-reviewer",
        reason=reason, scope=scope, subject_id=subject_id, subject_digest=subject_digest)


def _ingest(spec, directory, ledger, budget):
    path = directory / f"contract_{spec.doc_id}.docx"
    _write_docx(path, spec.paragraphs)
    blocks = extract_docx_blocks(path, doc_id=spec.doc_id, doc_version=1)
    parsed = ParsedDocument(
        doc_id=spec.doc_id, doc_version=1, file_id=f"f{spec.doc_id}", file_path=str(path),
        file_hash=hashlib.sha256(path.read_bytes()).hexdigest(), parser_name="docx_parser", parser_version="1.0.0",
        blocks=blocks, metadata=ContractMetadata(
            formal_title=spec.title, instrument_type=spec.instrument_type, parties=spec.parties))
    source = SourceIdentity(
        source_id=f"doc:{spec.doc_id}:v1:file:f{spec.doc_id}", instrument_id=f"doc:{spec.doc_id}",
        file_id=parsed.file_id, doc_version=1, file_hash=parsed.file_hash, snapshot=SNAPSHOT,
        configuration_version=CONFIGURATION, parser_name=parsed.parser_name, parser_version=parsed.parser_version)
    dependencies = {blocks[root].block_id: [blocks[t].block_id for t in targets]
                    for root, targets in spec.dependencies.items()}
    candidates = prepare_citation_units(parsed, source, original_path=path, dependencies=dependencies)
    _record(ledger, source)
    _record(ledger, source, scope="parties", subject_id=source.source_id, subject_digest=review_digest({
        "contract_title": candidates[0].contract_title,
        "parties": [p.model_dump(mode="json") for p in candidates[0].parties]}))
    review_ids = {u.unit_id: _record(ledger, source, scope="unit", subject_id=u.unit_id,
                                     subject_digest=unit_review_digest(u)).record_id for u in candidates}
    built = build_evidence_chunks(parsed, source, ledger=ledger, budget=budget, original_path=path,
                                  unit_review_ids=review_ids, dependencies=dependencies)
    return source, built, path


def _components(doc_ids, relations):
    parent = {doc: doc for doc in doc_ids}

    def root(doc):
        while parent[doc] != doc:
            doc = parent[doc]
        return doc

    for relation in relations:
        parent[root(relation.from_doc)] = root(relation.to_doc)
    groups = {}
    for doc in sorted(doc_ids):
        groups.setdefault(root(doc), []).append(doc)
    return [sorted(group) for group in groups.values()]


@dataclass
class P6Environment:
    manager: GenerationManager
    generation_id: str
    bundle: GenerationBundle
    ledger: ReviewStore
    journal: SQLitePolicyJournal
    access: AccessContext
    paths: dict
    encoder: object
    vector_store: ChromaVectorStore
    budget: QwenTokenBudget

    def unit(self, instrument: str, label: str) -> str:
        for unit in self.bundle.units:
            if unit.instrument_id == instrument and unit.location.label == label:
                return unit.unit_id
        raise KeyError((instrument, label))

    def chunk_ids(self, unit_id: str) -> list[str]:
        chunks = [c for c in self.bundle.chunks if c.unit_id == unit_id]
        return [c.chunk_id for c in sorted(chunks, key=lambda c: c.unit_start)]

    def query_access(self) -> AccessContext:
        return AccessContext(principal_id=self.access.principal_id, credential_id=self.access.credential_id,
                             permissions=["query"], policy_epoch=self.journal.snapshot().policy_epoch,
                             access_scope_digest=self.access.access_scope_digest)

    def pin(self, evidence_scope="linked_instruments", *, access=None):
        return self.manager.pin(access or self.query_access(), evidence_scope=evidence_scope)

    def block_source(self, instrument_id_or_source_id: str, reason="p6-revocation"):
        matches = [s.source_id for s in self.bundle.sources
                   if instrument_id_or_source_id in (s.instrument_id, s.source_id)]
        if not matches:
            raise KeyError(instrument_id_or_source_id)
        for source_id in matches:
            self.journal.block("source", source_id, reason)


def build_p6_environment(tmp_path, *, scenario="standard", encoder=None, vector_store=None,
                         embedding_dimension=3, configuration=None, generation_id="gen-p6-01",
                         budget=None) -> P6Environment:
    if scenario not in _SCENARIOS:
        raise ValueError(f"unknown P6 scenario: {scenario}")
    tmp_path = Path(tmp_path)
    (tmp_path / "sources").mkdir(parents=True, exist_ok=True)
    encoder = encoder or SyntheticEncoder()
    budget = budget or SyntheticBudget()
    vector_store = vector_store or ChromaVectorStore(mode="embedded", dimension=embedding_dimension)
    configuration = configuration or GenerationConfig(
        configuration_version=CONFIGURATION, embedding_identity=encoder.identity,
        embedding_dimension=embedding_dimension, lexical_identity=lexical_profile(),
        synthetic=str(encoder.identity.get("provider", "")).startswith("synthetic"))
    ledger = ReviewStore(tmp_path / "authority" / "reviews.sqlite")
    journal = SQLitePolicyJournal(tmp_path / "authority" / "policy.sqlite", initialize=True, journal_id="p6-test")

    specs, relation_specs = _corpus()
    ingested = {spec.doc_id: _ingest(spec, tmp_path / "sources", ledger, budget) for spec in specs}
    sources = {doc: source for doc, (source, _, _) in ingested.items()}
    units = {doc: built.units for doc, (_, built, _) in ingested.items()}

    relations, relation_sources = [], {}
    for spec in relation_specs:
        state = _SCENARIOS[scenario] if (spec.from_doc, spec.to_doc) == (102, 101) else "approved"
        relation = InstrumentRelation(
            relation_id=f"rel:{spec.from_doc}:{spec.relation_type}:{spec.to_doc}",
            from_instrument_id=f"doc:{spec.from_doc}", to_instrument_id=f"doc:{spec.to_doc}",
            relation_type=spec.relation_type, state=state, snapshot=SNAPSHOT, configuration_version=CONFIGURATION,
            affected_unit_ids=[units[spec.to_doc][i].unit_id for i in spec.affected],
            support_unit_ids=[units[spec.from_doc][i].unit_id for i in spec.support],
            support_spans=[s for i in spec.support for s in units[spec.from_doc][i].spans],
            review_record_id="placeholder" if state == "approved" else None)
        if state == "approved":
            relation.review_record_id = _record(
                ledger, sources[spec.from_doc], scope="relation", subject_id=relation.relation_id,
                subject_digest=review_digest(relation.model_dump(mode="json", exclude={"review_record_id"})),
                reason="p6 relation").record_id
        relations.append(relation)
        relation_sources[relation.relation_id] = sources[spec.from_doc].source_id

    resolutions, family_sources = [], {}
    registry = ledger.relation_state_digest()
    for component in _components(list(sources), relation_specs):
        members = {f"doc:{doc}" for doc in component}
        component_relations = [r for r in relations if r.from_instrument_id in members]
        worst = {r.state for r in component_relations}
        state = "resolved" if worst <= {"approved"} else "conflicted" if "conflicted" in worst else "pending"
        resolution = RelationResolution(
            family_id=f"fam:{component[0]}", snapshot=SNAPSHOT, registry_version="v1", registry_digest=registry,
            relation_ids=[r.relation_id for r in component_relations], state=state,
            review_record_id="placeholder" if state == "resolved" else None)
        anchor = sources[component[0]]
        if state == "resolved":
            resolution.review_record_id = _record(
                ledger, anchor, scope="family", subject_id=resolution.family_id,
                subject_digest=review_digest(resolution.model_dump(mode="json", exclude={"review_record_id"})),
                reason="p6 resolution").record_id
        resolutions.append(resolution)
        family_sources[resolution.family_id] = anchor.source_id

    bundle = GenerationBundle(
        sources=list(sources.values()), units=[u for built in units.values() for u in built],
        chunks=[c for _, built, _ in ingested.values() for c in built.chunks],
        reviews=[r for source in sources.values() for r in ledger.history(source.source_id)],
        relations=relations, resolutions=resolutions,
        relation_review_sources=relation_sources, family_review_sources=family_sources)
    manager = GenerationManager(tmp_path / "indexes", configuration=configuration, embeddings=encoder,
                                vector_store=vector_store, ledger=ledger, journal=journal, journal_id="p6-test")
    paths = {source.source_id: ingested[doc][2] for doc, source in sources.items()}
    manager.build(generation_id, bundle, source_paths=paths)
    access = AccessContext(principal_id="p6-operator", credential_id="cred-p6", permissions=["query", "operate"],
                           policy_epoch=journal.snapshot().policy_epoch, access_scope_digest="p6-operator")
    manager.promote(generation_id, access=access)
    return P6Environment(manager, generation_id, bundle, ledger, journal, access, paths, encoder,
                         vector_store, budget)

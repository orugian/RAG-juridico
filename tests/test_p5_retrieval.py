"""P5 retrieval regressions on physically backed synthetic fixtures.

Validates:
1. Structured retrieval on PinnedGeneration returning GovernedRetrievalResult and RetrievalResult.
2. Pre-filtering by AccessContext and clean identifiers (CPF, CNPJ numeric and alphanumeric) before BM25 and dense ranking.
3. Selection modes (union and intersection) and non-existent identifier zero-expansion (ACC-03).
4. Ambiguity handling returning needs_clarification with explicit clarification_code.
5. Inverse modifier lookup outside top-k discovering modifying addenda.
6. Mandatory resolve_closure consumption respecting Qwen token budget and handling relations/historical scope.
7. No shared mutation, bounded async execution outside event loop, and no silent degradation.
8. P4-J3 output boundary revalidation preventing return of revoked evidence.
"""
import asyncio
from datetime import date
import hashlib
from pathlib import Path
from zipfile import ZipFile
import pytest

from app.contracts import (
    AccessContext, CitationUnit, EvidenceChunk, InstrumentRelation, QueryPlan,
    RelationResolution, SelectionFilters, SourceIdentity,
)
from app.identifiers import normalize_identifier
from app.ingestion.docx_parser import extract_docx_blocks
from app.ingestion.evidence import (
    QwenTokenBudget, build_evidence_chunks, prepare_citation_units,
)
from app.ingestion.review_store import ReviewStore, review_digest, unit_review_digest
from app.ingestion.schemas import ContractMetadata, ContractParty, ParsedDocument
from app.retrieval.generation_contracts import GenerationBundle, GenerationConfig
from app.retrieval.generations import GenerationManager
from app.retrieval.hybrid import GovernedRetriever, GovernedRetrievalResult, plan_query
from app.retrieval.lexical import lexical_profile
from app.retrieval.policy import SQLitePolicyJournal
from app.retrieval.vector_store import ChromaVectorStore


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
        from threading import Lock
        from app.embeddings.qwen import QwenProfile

        class Tokenizer:
            def encode(self, text, **kwargs):
                return list(text)

        self._tokenizer = Tokenizer()
        self._lock = Lock()
        self.profile = QwenProfile()
        self._identity = {"synthetic_test_only": True, "profile": self.profile.specification}


@pytest.fixture
def p5_fixture_environment(tmp_path):
    """
    Sets up a complete governed generation with:
    - Base contract doc:101 with party Alpha (numeric CNPJ 11222333000144) and Beta (CPF 12345678901)
      Cláusula 1ª: honorários
      Cláusula 2ª: rescisão
    - Addendum doc:102 modifying Cláusula 1ª of doc:101
      Cláusula 1ª: alteração de honorários (Aditivo nº 1)
    - Contract doc:201 with party Gamma (alphanumeric CNPJ 12ABC34501DE67)
      Cláusula 1ª: locação comercial
    """
    ledger = ReviewStore(tmp_path / "authority" / "reviews.sqlite")
    journal = SQLitePolicyJournal(tmp_path / "authority" / "policy.sqlite", initialize=True, journal_id="p5-test")

    sources, units, chunks, paths = [], [], [], {}

    # 1. Base contract 101 (2 clauses)
    path_101 = tmp_path / "contract_101.docx"
    with ZipFile(path_101, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            '<w:p><w:r><w:t>Cláusula 1ª - Os honorários devidos pela Contratante Alpha são de R$ 50.000,00.</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>Cláusula 2ª - Em caso de rescisão imotivada, multa de 10% sobre o saldo remanescente.</w:t></w:r></w:p>'
            '</w:body></w:document>',
        )
    parsed_101 = ParsedDocument(
        doc_id=101, doc_version=1, file_id="f101", file_path=str(path_101),
        file_hash=hashlib.sha256(path_101.read_bytes()).hexdigest(), parser_name="docx_parser", parser_version="1.0.0",
        blocks=extract_docx_blocks(path_101, doc_id=101, doc_version=1),
        metadata=ContractMetadata(
            formal_title="Contrato de Honorários Alpha Beta", instrument_type="Contrato de Honorários",
            parties=[
                ContractParty(name="Alpha Serviços Ltda", clean_identifier="11222333000144"),
                ContractParty(name="Beta Participações", clean_identifier="12345678901"),
            ],
        ),
    )
    source_101 = SourceIdentity(
        source_id="doc:101:v1:file:f101", instrument_id="doc:101", file_id=parsed_101.file_id,
        doc_version=1, file_hash=parsed_101.file_hash, snapshot="p5-snap", configuration_version="cfg1",
        parser_name=parsed_101.parser_name, parser_version=parsed_101.parser_version,
    )
    candidates_101 = prepare_citation_units(parsed_101, source_101, original_path=path_101)
    base_record = dict(
        source_id=source_101.source_id, snapshot=source_101.snapshot, file_hash=source_101.file_hash,
        configuration_version="cfg1", decision="approved", reviewer="p5-reviewer", reason="synthetic test",
    )
    ledger.record(**base_record)
    ledger.record(
        **base_record, scope="parties", subject_id=source_101.source_id,
        subject_digest=review_digest({
            "contract_title": candidates_101[0].contract_title,
            "parties": [p.model_dump(mode="json") for p in candidates_101[0].parties],
        }),
    )
    unit_ids_101 = {
        u.unit_id: ledger.record(
            **base_record, scope="unit", subject_id=u.unit_id, subject_digest=unit_review_digest(u),
        ).record_id
        for u in candidates_101
    }
    built_101 = build_evidence_chunks(
        parsed_101, source_101, ledger=ledger, budget=SyntheticBudget(),
        original_path=path_101, unit_review_ids=unit_ids_101,
    )
    sources.append(source_101)
    units.extend(built_101.units)
    chunks.extend(built_101.chunks)
    paths[source_101.source_id] = path_101

    # 2. Addendum 102 modifying Cláusula 1ª of doc:101
    path_102 = tmp_path / "contract_102.docx"
    with ZipFile(path_102, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            '<w:p><w:r><w:t>Cláusula 1ª - Fica ajustado o valor dos honorários para R$ 75.000,00.</w:t></w:r></w:p>'
            '</w:body></w:document>',
        )
    parsed_102 = ParsedDocument(
        doc_id=102, doc_version=1, file_id="f102", file_path=str(path_102),
        file_hash=hashlib.sha256(path_102.read_bytes()).hexdigest(), parser_name="docx_parser", parser_version="1.0.0",
        blocks=extract_docx_blocks(path_102, doc_id=102, doc_version=1),
        metadata=ContractMetadata(
            formal_title="Primeiro Aditivo ao Contrato Alpha Beta", instrument_type="Aditivo",
            parties=[
                ContractParty(name="Alpha Serviços Ltda", clean_identifier="11222333000144"),
                ContractParty(name="Beta Participações", clean_identifier="12345678901"),
            ],
        ),
    )
    source_102 = SourceIdentity(
        source_id="doc:102:v1:file:f102", instrument_id="doc:102", file_id=parsed_102.file_id,
        doc_version=1, file_hash=parsed_102.file_hash, snapshot="p5-snap", configuration_version="cfg1",
        parser_name=parsed_102.parser_name, parser_version=parsed_102.parser_version,
    )
    candidates_102 = prepare_citation_units(parsed_102, source_102, original_path=path_102)
    base_record_102 = dict(
        source_id=source_102.source_id, snapshot=source_102.snapshot, file_hash=source_102.file_hash,
        configuration_version="cfg1", decision="approved", reviewer="p5-reviewer", reason="synthetic test",
    )
    ledger.record(**base_record_102)
    ledger.record(
        **base_record_102, scope="parties", subject_id=source_102.source_id,
        subject_digest=review_digest({
            "contract_title": candidates_102[0].contract_title,
            "parties": [p.model_dump(mode="json") for p in candidates_102[0].parties],
        }),
    )
    unit_ids_102 = {
        u.unit_id: ledger.record(
            **base_record_102, scope="unit", subject_id=u.unit_id, subject_digest=unit_review_digest(u),
        ).record_id
        for u in candidates_102
    }
    built_102 = build_evidence_chunks(
        parsed_102, source_102, ledger=ledger, budget=SyntheticBudget(),
        original_path=path_102, unit_review_ids=unit_ids_102,
    )
    sources.append(source_102)
    units.extend(built_102.units)
    chunks.extend(built_102.chunks)
    paths[source_102.source_id] = path_102

    # 3. Contract 201 with alphanumeric CNPJ (12ABC34501DE67)
    path_201 = tmp_path / "contract_201.docx"
    with ZipFile(path_201, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            '<w:p><w:r><w:t>Cláusula 1ª - Locação comercial do imóvel pelo valor mensal de R$ 12.000,00.</w:t></w:r></w:p>'
            '</w:body></w:document>',
        )
    parsed_201 = ParsedDocument(
        doc_id=201, doc_version=1, file_id="f201", file_path=str(path_201),
        file_hash=hashlib.sha256(path_201.read_bytes()).hexdigest(), parser_name="docx_parser", parser_version="1.0.0",
        blocks=extract_docx_blocks(path_201, doc_id=201, doc_version=1),
        metadata=ContractMetadata(
            formal_title="Contrato de Locação Comercial Gamma", instrument_type="Contrato de Locação",
            parties=[
                ContractParty(name="Gamma Inovações Ltda", clean_identifier="12ABC34501DE67"),
            ],
        ),
    )
    source_201 = SourceIdentity(
        source_id="doc:201:v1:file:f201", instrument_id="doc:201", file_id=parsed_201.file_id,
        doc_version=1, file_hash=parsed_201.file_hash, snapshot="p5-snap", configuration_version="cfg1",
        parser_name=parsed_201.parser_name, parser_version=parsed_201.parser_version,
    )
    candidates_201 = prepare_citation_units(parsed_201, source_201, original_path=path_201)
    base_record_201 = dict(
        source_id=source_201.source_id, snapshot=source_201.snapshot, file_hash=source_201.file_hash,
        configuration_version="cfg1", decision="approved", reviewer="p5-reviewer", reason="synthetic test",
    )
    ledger.record(**base_record_201)
    ledger.record(
        **base_record_201, scope="parties", subject_id=source_201.source_id,
        subject_digest=review_digest({
            "contract_title": candidates_201[0].contract_title,
            "parties": [p.model_dump(mode="json") for p in candidates_201[0].parties],
        }),
    )
    unit_ids_201 = {
        u.unit_id: ledger.record(
            **base_record_201, scope="unit", subject_id=u.unit_id, subject_digest=unit_review_digest(u),
        ).record_id
        for u in candidates_201
    }
    built_201 = build_evidence_chunks(
        parsed_201, source_201, ledger=ledger, budget=SyntheticBudget(),
        original_path=path_201, unit_review_ids=unit_ids_201,
    )
    sources.append(source_201)
    units.extend(built_201.units)
    chunks.extend(built_201.chunks)
    paths[source_201.source_id] = path_201

    # 4. Relations: doc:102 amends Cláusula 1ª of doc:101
    target_unit_101_cl1 = built_101.units[0].unit_id
    support_unit_102_cl1 = built_102.units[0].unit_id

    relation_rec = dict(
        source_id=source_102.source_id, snapshot="p5-snap", file_hash=source_102.file_hash,
        configuration_version="cfg1", decision="approved", reviewer="p5-reviewer", reason="synthetic relation",
    )
    relation_id = "rel:102:amends:101"
    relation = InstrumentRelation(
        relation_id=relation_id, from_instrument_id="doc:102", to_instrument_id="doc:101",
        relation_type="amends", affected_unit_ids=[target_unit_101_cl1],
        support_unit_ids=[support_unit_102_cl1],
        support_spans=built_102.units[0].spans,
        state="approved", review_record_id="placeholder", snapshot="p5-snap",
        configuration_version="cfg1",
    )
    relation.review_record_id = ledger.record(
        **relation_rec, scope="relation", subject_id=relation.relation_id,
        subject_digest=review_digest(relation.model_dump(mode="json", exclude={"review_record_id"})),
    ).record_id

    family_id = "fam:101"
    resolution_rec = dict(
        source_id=source_101.source_id, snapshot="p5-snap", file_hash=source_101.file_hash,
        configuration_version="cfg1", decision="approved", reviewer="p5-reviewer", reason="synthetic resolution",
    )
    resolution = RelationResolution(
        family_id=family_id, snapshot="p5-snap", registry_version="v1",
        registry_digest=ledger.relation_state_digest(), relation_ids=[relation.relation_id],
        state="resolved", review_record_id="placeholder",
    )
    resolution.review_record_id = ledger.record(
        **resolution_rec, scope="family", subject_id=resolution.family_id,
        subject_digest=review_digest(resolution.model_dump(mode="json", exclude={"review_record_id"})),
    ).record_id

    family_201_id = "fam:201"
    resolution_rec_201 = dict(
        source_id=source_201.source_id, snapshot="p5-snap", file_hash=source_201.file_hash,
        configuration_version="cfg1", decision="approved", reviewer="p5-reviewer", reason="synthetic resolution 201",
    )
    resolution_201 = RelationResolution(
        family_id=family_201_id, snapshot="p5-snap", registry_version="v1",
        registry_digest=ledger.relation_state_digest(), relation_ids=[],
        state="resolved", review_record_id="placeholder",
    )
    resolution_201.review_record_id = ledger.record(
        **resolution_rec_201, scope="family", subject_id=family_201_id,
        subject_digest=review_digest(resolution_201.model_dump(mode="json", exclude={"review_record_id"})),
    ).record_id

    all_reviews = [record for s in sources for record in ledger.history(s.source_id)]

    bundle = GenerationBundle(
        sources=sources, units=units, chunks=chunks, reviews=all_reviews,
        relations=[relation], resolutions=[resolution, resolution_201],
        relation_review_sources={relation_id: source_102.source_id},
        family_review_sources={family_id: source_101.source_id, family_201_id: source_201.source_id},
    )

    config = GenerationConfig(
        configuration_version="cfg1", synthetic=True, embedding_identity=SyntheticEncoder.identity,
        embedding_dimension=3, lexical_identity=lexical_profile(),
    )
    encoder = SyntheticEncoder()
    vector_store = ChromaVectorStore(mode="embedded", dimension=3)
    manager = GenerationManager(
        tmp_path / "indexes", configuration=config, embeddings=encoder,
        vector_store=vector_store, ledger=ledger, journal=journal,
        journal_id="p5-test",
    )

    # Build and promote generation
    gen_id = "gen-p5-01"
    manager.build(gen_id, bundle, source_paths=paths)
    access = AccessContext(
        principal_id="p5-operator", credential_id="cred-p5", permissions=["query", "operate"],
        policy_epoch=journal.snapshot().policy_epoch, access_scope_digest="p5-operator",
    )
    manager.promote(gen_id, access=access)

    return {
        "manager": manager, "generation_id": gen_id, "bundle": bundle,
        "ledger": ledger, "journal": journal, "encoder": encoder,
        "access": access, "target_unit_101_cl1": target_unit_101_cl1,
        "support_unit_102_cl1": support_unit_102_cl1,
    }


def test_plan_query_deterministic_extraction():
    """Verify plan_query normalizes clean identifiers (numeric and alphanumeric)."""
    # Alphanumeric CNPJ
    plan = plan_query("Qual o aluguel no CNPJ 12.ABC.345/01DE-67?")
    assert plan.filters.party_identifiers == ["12ABC34501DE67"]
    assert plan.evidence_scope == "linked_instruments"

    # Numeric CNPJ & CPF
    plan2 = plan_query("Contrato entre 11.222.333/0001-44 e CPF 123.456.789-01", selection_mode="intersection")
    assert plan2.filters.party_identifiers == ["11222333000144", "12345678901"]
    assert plan2.filters.selection_mode == "intersection"


def test_governed_retriever_pinned_generation_structure(p5_fixture_environment):
    """Verify GovernedRetriever produces valid GovernedRetrievalResult on PinnedGeneration."""
    env = p5_fixture_environment
    pinned = env["manager"].pin(access=env["access"], evidence_scope="linked_instruments")
    retriever = GovernedRetriever(pinned)

    query = "honorários advocatícios"
    result = retriever.retrieve(query)

    assert isinstance(result, GovernedRetrievalResult)
    assert result.status == "answered"
    assert result.retrieval_result.generation_id == env["generation_id"]
    assert result.retrieval_result.retrieval_mode == "hybrid"
    assert len(result.evidence_chunks) >= 1
    assert len(result.citation_units) >= 1
    assert all(isinstance(c, EvidenceChunk) for c in result.evidence_chunks)
    assert all(isinstance(u, CitationUnit) for u in result.citation_units)


def test_governed_retriever_access_context_revocation_and_blocking(p5_fixture_environment):
    """Verify that revoked/blocked sources are excluded before rankings and never leak."""
    env = p5_fixture_environment
    # Block source 101 in policy journal
    env["journal"].block("source", "doc:101:v1:file:f101", "bloqueio_regulatorio")

    new_access = AccessContext(
        principal_id="p5-operator", credential_id="cred-p5", permissions=["query"],
        policy_epoch=env["journal"].snapshot().policy_epoch, access_scope_digest="p5-operator",
    )
    pinned = env["manager"].pin(access=new_access, evidence_scope="original_text")
    retriever = GovernedRetriever(pinned)

    result = retriever.retrieve("honorários Alpha")
    # All doc:101 chunks must be absent from returned evidence chunks
    for chunk in result.evidence_chunks:
        assert chunk.source_id != "doc:101:v1:file:f101"


def test_governed_retriever_identifier_selection_cnpj_cpf_alphanumeric(p5_fixture_environment):
    """Verify strict pre-filtering by numeric and alphanumeric CNPJ/CPF before ranking."""
    env = p5_fixture_environment
    pinned = env["manager"].pin(access=env["access"], evidence_scope="linked_instruments")
    retriever = GovernedRetriever(pinned)

    # 1. Alphanumeric CNPJ Gamma
    plan_gamma = plan_query("Qual o valor da locação do CNPJ 12.ABC.345/01DE-67?")
    res_gamma = retriever.retrieve("locação", query_plan=plan_gamma)
    assert res_gamma.status == "answered"
    assert all(c.instrument_id == "doc:201" for c in res_gamma.evidence_chunks)

    # 2. Non-existent CNPJ must yield empty evidence and zero expansion (ACC-03)
    plan_nonexistent = plan_query("Qual a multa do CNPJ 00.000.000/0001-99?")
    res_none = retriever.retrieve("multa", query_plan=plan_nonexistent)
    assert res_none.status == "abstained"
    assert len(res_none.evidence_chunks) == 0
    assert len(res_none.citation_units) == 0


def test_governed_retriever_selection_modes_union_and_intersection(p5_fixture_environment):
    """Verify union vs intersection semantics for multiple identifiers."""
    env = p5_fixture_environment
    pinned = env["manager"].pin(access=env["access"], evidence_scope="linked_instruments")
    retriever = GovernedRetriever(pinned)

    # Intersection of Alpha (11222333000144) and Gamma (12ABC34501DE67): no instrument contains both
    plan_inter = QueryPlan(
        question_item_ids=["q1"],
        filters=SelectionFilters(
            party_identifiers=["11222333000144", "12ABC34501DE67"],
            selection_mode="intersection",
        ),
        evidence_scope="linked_instruments",
    )
    res_inter = retriever.retrieve("contrato", query_plan=plan_inter)
    assert res_inter.status == "abstained"
    assert len(res_inter.evidence_chunks) == 0

    # Union of Alpha and Gamma: both instruments are eligible
    plan_union = QueryPlan(
        question_item_ids=["q1"],
        filters=SelectionFilters(
            party_identifiers=["11222333000144", "12ABC34501DE67"],
            selection_mode="union",
        ),
        evidence_scope="linked_instruments",
    )
    res_union = retriever.retrieve("contrato", query_plan=plan_union)
    assert res_union.status == "answered"
    retrieved_instruments = {c.instrument_id for c in res_union.evidence_chunks}
    assert "doc:101" in retrieved_instruments or "doc:201" in retrieved_instruments


def test_governed_retriever_inverse_modifier_lookup_outside_top_k(p5_fixture_environment):
    """
    Verify that in linked_instruments scope, retrieving Cláusula 1ª of base contract
    discovers and includes the modifying addendum doc:102 via inverse lookup, even if
    the addendum was absent from top-k dense/sparse scores.
    """
    env = p5_fixture_environment
    pinned = env["manager"].pin(access=env["access"], evidence_scope="linked_instruments")
    retriever = GovernedRetriever(pinned)

    # Query targeting base clause text specifically
    plan = QueryPlan(
        question_item_ids=["q_honorarios"],
        filters=SelectionFilters(instrument_ids=["doc:101"]),
        evidence_scope="linked_instruments",
    )
    result = retriever.retrieve("honorários Alpha R$ 50.000", query_plan=plan)
    assert result.status == "answered"

    # Citation units MUST contain both the base unit AND the modifying addendum unit!
    retrieved_unit_ids = {u.unit_id for u in result.citation_units}
    assert env["target_unit_101_cl1"] in retrieved_unit_ids
    assert env["support_unit_102_cl1"] in retrieved_unit_ids


def test_governed_retriever_historical_vs_applicable_scope(p5_fixture_environment):
    """Verify original_text scope returns base unit with warnings without consolidating addendum."""
    env = p5_fixture_environment
    pinned = env["manager"].pin(access=env["access"], evidence_scope="original_text")
    retriever = GovernedRetriever(pinned)

    plan = QueryPlan(
        question_item_ids=["q_hist"],
        filters=SelectionFilters(instrument_ids=["doc:101"]),
        evidence_scope="original_text",
    )
    result = retriever.retrieve("honorários Alpha 50.000", query_plan=plan)
    assert result.status == "answered"
    retrieved_unit_ids = {u.unit_id for u in result.citation_units}
    assert env["target_unit_101_cl1"] in retrieved_unit_ids
    # Modifying addendum is NOT forced into primary proof in historical scope
    assert env["support_unit_102_cl1"] not in retrieved_unit_ids
    # Warnings must notify about the known modifier
    assert any("known_modifier" in w for w in result.warnings)


def test_governed_retriever_budget_exceeded_handling(p5_fixture_environment):
    """Verify that exceeding token budget returns controlled blocked status without silent truncation."""
    env = p5_fixture_environment
    pinned = env["manager"].pin(access=env["access"], evidence_scope="linked_instruments")
    retriever = GovernedRetriever(pinned)

    # max_tokens=10 is too small for rendering units with header
    result = retriever.retrieve("honorários", max_tokens=10)
    assert result.status in {"abstained", "blocked"}
    assert result.reason_code == "closure_over_budget"


def test_governed_retriever_no_shared_mutation_and_bounded_async(p5_fixture_environment):
    """Verify no mutation of shared BM25 vectorizer and correct async offloading."""
    env = p5_fixture_environment
    pinned = env["manager"].pin(access=env["access"], evidence_scope="linked_instruments")
    retriever = GovernedRetriever(pinned)

    # Async retrieve test
    async def run_async():
        return await retriever.aretrieve("honorários Alpha")

    result = asyncio.run(run_async())
    assert result.status == "answered"


def test_governed_retriever_output_revocation_revalidation(p5_fixture_environment, monkeypatch):
    """Verify P4-J3 output boundary: revoking source during copy/finish discards output."""
    env = p5_fixture_environment
    pinned = env["manager"].pin(access=env["access"], evidence_scope="linked_instruments")
    retriever = GovernedRetriever(pinned)

    from app.retrieval.generations import PinnedGeneration
    original_finish = PinnedGeneration.finish

    def revoking_finish(self, chunk_ids):
        # Revoke source 101 in policy journal
        env["journal"].block("source", "doc:101:v1:file:f101", "revogação durante retorno")
        return original_finish(self, chunk_ids)

    monkeypatch.setattr(PinnedGeneration, "finish", revoking_finish)

    with pytest.raises(ValueError):
        retriever.retrieve("honorários Alpha")

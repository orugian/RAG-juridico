import pytest

from app.contracts import QueryPlan, SelectionFilters
from app.ingestion.closure import resolve_closure
from app.retrieval.hybrid import GovernedRetriever, plan_query
from app.retrieval.vector_store import ChromaVectorStore
from tests.p6_corpus import P6Environment, SyntheticEncoder, build_p6_environment


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    return build_p6_environment(tmp_path_factory.mktemp("p6-standard"))


def _plan(scope, *instruments):
    return QueryPlan(question_item_ids=["q1"], filters=SelectionFilters(instrument_ids=list(instruments)),
                     evidence_scope=scope)


def _units(result):
    return {unit.unit_id for unit in result.citation_units}


def test_standard_environment_builds_promotes_and_pins_both_scopes(env):
    assert isinstance(env, P6Environment) and isinstance(env.encoder, SyntheticEncoder) and env.encoder.calls
    assert env.manager.verify(env.generation_id).manifest.generation_id == "gen-p6-01"
    for scope in ("linked_instruments", "original_text"):
        pinned = env.pin(scope)
        assert pinned.evidence_scope == scope and pinned.generation_id == env.generation_id
        assert pinned.access.permissions == ["query"]
    assert {unit.approval_state for unit in env.bundle.units} == {"approved"}
    assert {source.instrument_id for source in env.bundle.sources} == {
        "doc:101", "doc:102", "doc:201", "doc:301", "doc:302"}


def test_linked_instruments_returns_base_clause_and_modifying_addendum(env):
    result = GovernedRetriever(env.pin()).retrieve(
        "honorários Alpha R$ 50.000", query_plan=_plan("linked_instruments", "doc:101"))
    assert result.status == "answered"
    assert {env.unit("doc:101", "Cláusula 1ª"), env.unit("doc:102", "Cláusula 1ª")} <= _units(result)
    assert "75.000,00" in " ".join(unit.verbatim_text for unit in result.citation_units)


def test_original_text_excludes_addendum_with_known_modifier_warning(env):
    result = GovernedRetriever(env.pin("original_text")).retrieve(
        "honorários Alpha R$ 50.000", query_plan=_plan("original_text", "doc:101"))
    assert result.status == "answered"
    assert env.unit("doc:101", "Cláusula 1ª") in _units(result)
    assert env.unit("doc:102", "Cláusula 1ª") not in _units(result)
    assert any(warning.startswith("known_modifier:") for warning in result.warnings)


def test_exception_clause_is_delivered_whole_with_its_closure(env):
    third, fourth = env.unit("doc:101", "Cláusula 3ª"), env.unit("doc:101", "Cláusula 4ª")
    units = {unit.unit_id: unit for unit in env.bundle.units}
    assert fourth in units[third].closure_unit_ids
    assert any(word in units[third].verbatim_text for word in ("salvo", "exceto")) and "não" in units[third].verbatim_text
    closure = resolve_closure(
        [third], units=units, sources={s.source_id: s for s in env.bundle.sources}, ledger=env.ledger,
        relations=env.bundle.relations, resolutions=env.bundle.resolutions,
        relation_review_sources=env.bundle.relation_review_sources,
        family_review_sources=env.bundle.family_review_sources,
        evidence_scope="linked_instruments", count_tokens=len, max_tokens=10_000)
    assert closure.status != "blocked" and {u.unit_id for u in closure.units} == {third, fourth}
    result = GovernedRetriever(env.pin()).retrieve(
        "foro confidencialidade salvo exceção", query_plan=_plan("linked_instruments", "doc:101"))
    assert {third, fourth} <= _units(result)


def test_alphanumeric_cnpj_selects_third_party_lease_only(env):
    plan = plan_query("Qual o aluguel do CNPJ 12.ABC.345/01DE-67?")
    assert plan.filters.party_identifiers == ["12ABC34501DE67"]
    result = GovernedRetriever(env.pin()).retrieve("locação valor mensal", query_plan=plan)
    assert result.status == "answered"
    assert {chunk.instrument_id for chunk in result.evidence_chunks} == {"doc:201"}
    assert all(not party.is_law_firm for unit in result.citation_units for party in unit.parties)


def test_law_firm_is_party_only_in_its_own_instrument(env):
    firm = {unit.instrument_id for unit in env.bundle.units if any(p.is_law_firm for p in unit.parties)}
    assert firm == {"doc:101"}


def test_unit_and_chunk_lookup_are_consistent(env):
    chunks = {chunk.chunk_id: chunk for chunk in env.bundle.chunks}
    units = {unit.unit_id: unit for unit in env.bundle.units}
    for unit_id, unit in units.items():
        ids = env.chunk_ids(unit_id)
        assert ids and {chunks[c].unit_id for c in ids} == {unit_id}
        assert env.unit(unit.instrument_id, unit.location.label) == unit_id
    with pytest.raises(KeyError):
        env.unit("doc:101", "Cláusula 99ª")


@pytest.mark.parametrize("scenario,state", [("pending_relation", "proposed"), ("conflicted_relation", "conflicted")])
def test_unresolved_relation_scenarios_only_serve_original_text(tmp_path, scenario, state):
    env = build_p6_environment(tmp_path, scenario=scenario)
    assert {relation.state for relation in env.bundle.relations if relation.from_instrument_id == "doc:102"} == {state}
    with pytest.raises(ValueError):
        env.pin("linked_instruments")
    result = GovernedRetriever(env.pin("original_text")).retrieve(
        "honorários Alpha R$ 50.000", query_plan=_plan("original_text", "doc:101"))
    assert result.status == "answered"
    assert any(w.startswith(("relation_unresolved:", "family_unresolved:")) for w in result.warnings)


def test_unknown_scenario_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        build_p6_environment(tmp_path, scenario="nope")


def test_block_source_revokes_finish_for_pinned_chunks(tmp_path):
    env = build_p6_environment(tmp_path)
    pinned = env.pin()
    chunk_ids = env.chunk_ids(env.unit("doc:101", "Cláusula 1ª"))
    assert pinned.finish(chunk_ids)
    env.block_source("doc:101")
    with pytest.raises(ValueError):
        pinned.finish(chunk_ids)
    fresh = env.pin()
    with pytest.raises(ValueError):
        fresh.finish(chunk_ids)
    source_id = next(s.source_id for s in env.bundle.sources if s.instrument_id == "doc:201")
    env.block_source(source_id)
    assert source_id in env.journal.snapshot().blocked_source_ids


def test_explicit_embedded_vector_store_equals_default(tmp_path, env):
    explicit = build_p6_environment(tmp_path, vector_store=ChromaVectorStore(mode="embedded", dimension=3))
    assert [u.unit_id for u in explicit.bundle.units] == [u.unit_id for u in env.bundle.units]
    assert explicit.vector_store.dimension == env.vector_store.dimension == 3
    result = GovernedRetriever(explicit.pin()).retrieve(
        "honorários Alpha R$ 50.000", query_plan=_plan("linked_instruments", "doc:101"))
    assert result.status == "answered"

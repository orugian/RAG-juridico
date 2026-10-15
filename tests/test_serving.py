"""Serving caches must keep every revocation/tamper decision of the accepted P4 manager."""
import pytest

from app.ingestion.review_store import ReviewStore
from app.serving import ServingGenerationManager, SnapshotReviewAuthority
from tests.p6_corpus import build_p6_environment


@pytest.fixture
def env(tmp_path):
    return build_p6_environment(tmp_path)


def serving(env):
    return ServingGenerationManager(env.manager.root, configuration=env.manager.configuration,
                                    embeddings=env.encoder, vector_store=env.vector_store,
                                    ledger=env.ledger, journal=env.journal, journal_id="p6-test")


def _unit_key(unit):
    source = unit.source_identities[0]
    return dict(source_id=source.source_id, snapshot=source.snapshot, file_hash=source.file_hash,
                configuration_version=source.configuration_version)


def test_snapshot_authority_matches_ledger_and_sees_commits_from_other_connections(env):
    authority = SnapshotReviewAuthority(env.ledger.path)
    unit = env.bundle.units[0]
    from app.ingestion.review_store import unit_review_digest
    scope = dict(_unit_key(unit), scope="unit", subject_id=unit.unit_id, subject_digest=unit_review_digest(unit))
    assert authority.latest(**scope) == env.ledger.latest(**scope)
    assert authority.state_digest() == env.ledger.state_digest()
    assert authority.relation_state_digest() == env.ledger.relation_state_digest()
    before = authority.state_digest()
    revoked = env.ledger.record(**{k: v for k, v in scope.items()}, decision="excluded",
                                reviewer="revisor", reason="revogação")
    assert authority.latest(**scope) == revoked
    with pytest.raises(ValueError):
        authority.require_current(record_id=unit.review_record_id, **scope)
    assert authority.state_digest() == env.ledger.state_digest() != before
    with pytest.raises(ValueError):
        authority.record(**scope, decision="approved", reviewer="x", reason="y")


def test_snapshot_authority_detects_ledger_file_replacement(env, tmp_path):
    import os
    authority = SnapshotReviewAuthority(env.ledger.path)
    authority.state_digest()
    replacement = tmp_path / "other.sqlite"
    ReviewStore(replacement)
    if os.name == "nt":  # Windows refuses to replace the ledger while it is open.
        with pytest.raises(PermissionError):
            env.ledger.path.unlink()
        return
    env.ledger.path.unlink()
    replacement.rename(env.ledger.path)
    with pytest.raises(ValueError):
        authority.state_digest()


def test_cached_serving_manager_still_rejects_unit_revocation_on_the_next_request(env):
    manager = serving(env)
    access = env.query_access()
    first = manager.pin(access)
    assert first.finish([env.bundle.chunks[0].chunk_id])
    assert manager.pin(access).loaded is first.loaded  # artifact load is cached
    unit = next(u for u in env.bundle.units if u.unit_id == env.bundle.chunks[0].unit_id)
    from app.ingestion.review_store import unit_review_digest
    env.ledger.record(**_unit_key(unit), scope="unit", subject_id=unit.unit_id,
                      subject_digest=unit_review_digest(unit), decision="excluded",
                      reviewer="revisor", reason="revogação")
    with pytest.raises(ValueError):
        manager.pin(access)
    with pytest.raises(ValueError):
        first.finish([env.bundle.chunks[0].chunk_id])


def test_cached_serving_manager_honours_journal_blocks_and_credential_revocation(env):
    manager = serving(env)
    pinned = manager.pin(env.query_access())
    chunk = next(c for c in env.bundle.chunks if c.instrument_id == "doc:201")
    assert pinned.finish([chunk.chunk_id])
    env.block_source("doc:201")
    with pytest.raises(ValueError):
        pinned.finish([chunk.chunk_id])
    fresh = manager.pin(env.query_access())
    assert chunk.source_id not in fresh.allowed_sources()
    env.journal.block("credential", env.access.credential_id, "revogada")
    with pytest.raises(ValueError):
        manager.pin(env.query_access())


def test_cached_artifact_load_is_reverified_after_any_artifact_change(env):
    manager = serving(env)
    loaded = manager.pin(env.query_access()).loaded
    bundle_file = loaded.directory / "bundle.json"
    bundle_file.write_bytes(bundle_file.read_bytes() + b" ")
    with pytest.raises(ValueError):
        manager.pin(env.query_access())

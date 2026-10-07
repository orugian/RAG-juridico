"""Explicit local generations. No build publishes, no restore recreates policy.

The root is operator-controlled, never an uploaded pickle/manifest directory.
P4 probes validate persistence/authorization, not P5 retrieval quality or SLOs.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import tempfile
import time
from uuid import uuid4

from chromadb.errors import ChromaError
from httpx import HTTPError

from app.contracts import AccessContext
from app.embeddings.artifacts import digest_file
from app.ingestion.evidence import validate_unit_decisions
from app.retrieval.generation_contracts import (
    GenerationBundle, GenerationConfig, GenerationManifest, canonical_bytes,
    content_digest, validate_generation_id,
)
from app.retrieval.generation_store import ArtifactData, _safe_path, read_artifacts, write_artifacts
from app.retrieval.lexical import lexical_tokens
from app.retrieval.review_authority import ReadOnlyReviewAuthority


def _sync_directory(path):
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _write(path, data):
    with _safe_path(path).open("xb") as target:
        target.write(data)
        target.flush()
        os.fsync(target.fileno())
    _sync_directory(path.parent)


def _fault(callback, point):
    if callback is not None:
        callback(point)


def float32_vector(vector, dimension):
    if (not isinstance(vector, (list, tuple)) or len(vector) != dimension
            or any(type(value) not in (int, float) for value in vector)):
        raise ValueError("embedding_vector_invalid")
    try:
        result = [struct.unpack("!f", struct.pack("!f", value))[0] for value in vector]
    except (OverflowError, struct.error) as error:
        raise ValueError("embedding_vector_invalid") from error
    if not all(math.isfinite(value) for value in result):
        raise ValueError("embedding_vector_invalid")
    return result


@dataclass(frozen=True)
class LoadedGeneration:
    manifest: GenerationManifest
    artifacts: ArtifactData
    directory: Path
    manifest_sha256: str


class GenerationManager:
    def __init__(self, root: Path, *, configuration: GenerationConfig, embeddings, vector_store,
                 ledger, journal, journal_id: str):
        self.root = _safe_path(root)
        self.configuration = GenerationConfig.model_validate(configuration.model_dump(mode="json"))
        self.embeddings, self.vector_store = embeddings, vector_store
        if _safe_path(ledger.path).is_relative_to(self.root):
            raise ValueError("review_authority_must_be_independent_of_generation_root")
        self.ledger = ReadOnlyReviewAuthority(ledger.path)
        self.journal, self.journal_id = journal, journal_id
        if hasattr(journal, "path") and _safe_path(journal.path).is_relative_to(self.root):
            raise ValueError("policy_authority_must_be_independent_of_generation_root")
        self._configuration()
        self._policy()

    def _configuration(self):
        GenerationConfig.model_validate(self.configuration.model_dump(mode="json"))
        if self.embeddings.identity != self.configuration.embedding_identity:
            raise ValueError("embedding_runtime_identity_incompatible")
        if self.vector_store.dimension != self.configuration.embedding_dimension:
            raise ValueError("vector_backend_dimension_incompatible")

    def _policy(self):
        snapshot = self.journal.snapshot()
        if snapshot.journal_id != self.journal_id:
            raise ValueError("policy_authority_identity_changed")
        return snapshot

    @contextmanager
    def _writer(self):
        self.root.mkdir(parents=True, exist_ok=True)
        path = _safe_path(self.root / "writer.lock")
        try:
            _write(path, b"1")
        except FileExistsError:
            pass
        with path.open("r+b") as target:
            deadline = time.monotonic() + 10
            while True:
                try:
                    target.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(target.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(target.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise ValueError("generation_writer_busy")
                    time.sleep(0.01)
            try:
                yield
            finally:
                target.seek(0)
                if os.name == "nt":
                    msvcrt.locking(target.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(target.fileno(), fcntl.LOCK_UN)

    def _reviews(self, bundle, *, source_ids=None):
        units = {unit.unit_id: unit for unit in bundle.units}
        records = {record.record_id: record for record in bundle.reviews}
        for unit in bundle.units:
            if source_ids is not None and not set(unit.source_ids).issubset(source_ids):
                continue
            for current in validate_unit_decisions(unit, self.ledger, all_units=units):
                if records.get(current.record_id) != current:
                    raise ValueError("generation_current_review_missing_or_changed")

    @contextmanager
    def _commit_guard(self, bundle, *, ledger_digest, policy=None, access=None):
        """Local linearization boundary: both current authorities cover the swap.

        Ledger writers reserve the same SQLite write transaction; policy writers
        use its OS lock. No review/policy data is written by this lease. Keep
        model/backend/file preparation outside this short critical section.
        """
        with self.ledger.hold_current(), self.journal.hold_snapshot() as current:
            if current.journal_id != self.journal_id:
                raise ValueError("policy_authority_identity_changed")
            if policy is not None and current != policy:
                raise ValueError("generation_policy_changed_before_commit")
            if access is not None:
                self._access(access, "operate", snapshot=current)
            if self.ledger.state_digest() != ledger_digest:
                raise ValueError("generation_review_changed_before_commit")
            allowed = {source.source_id for source in bundle.sources} - set(current.blocked_source_ids)
            if not allowed:
                raise ValueError("generation_has_no_authorized_sources")
            self._reviews(bundle, source_ids=allowed)
            yield current

    def _physical(self, bundle, source_paths, source_documents=None, conversion_paths=None):
        from app.ingestion.docx_parser import extract_docx_blocks
        from app.ingestion.pdf_parser import extract_pdf_blocks
        from app.ingestion.provenance import verify_document_provenance
        from app.ingestion.schemas import ParsedDocument, ContractMetadata
        source_documents, conversion_paths = source_documents or {}, conversion_paths or {}
        if set(source_paths) != {source.source_id for source in bundle.sources}:
            raise ValueError("generation_original_paths_incomplete")
        verified = {}
        for source in bundle.sources:
            path = _safe_path(source_paths[source.source_id])
            if source.source_id in source_documents:
                document = source_documents[source.source_id]
            else:
                match = re.fullmatch(r"doc:(\d+)", source.instrument_id)
                if match is None or source.parser_name not in {"docx_parser", "pdf_parser"}:
                    raise ValueError("generation_verified_parsed_document_required")
                extractor = extract_docx_blocks if source.parser_name == "docx_parser" else extract_pdf_blocks
                document = ParsedDocument(doc_id=int(match[1]), doc_version=source.doc_version,
                    file_id=source.file_id, file_path=str(path), file_hash=source.file_hash,
                    parser_name=source.parser_name, parser_version=source.parser_version,
                    blocks=extractor(path, doc_id=int(match[1]), doc_version=source.doc_version),
                    metadata=ContractMetadata(formal_title="Verification only", instrument_type="Contract"))
            verified[source.source_id] = verify_document_provenance(document, source, original_path=path,
                conversion_path=conversion_paths.get(source.source_id), conversion_sha256=source.conversion_sha256)
        for unit in bundle.units:
            for entry in unit.text_map:
                span = entry.source_span
                if span is None:
                    continue
                block = verified.get(span.source_id, {}).get(span.block_id)
                if block is None:
                    raise ValueError("generation_physical_block_missing")
                # Coordinates and literal are the same proof accepted by P3B.
                expected = block.span.model_dump() | {"start": span.start, "end": span.end}
                if span.model_dump() != expected or block.literal_text[span.start:span.end] != unit.verbatim_text[entry.unit_start:entry.unit_end]:
                    raise ValueError("generation_physical_span_mismatch")

    def build(self, generation_id, bundle, *, source_paths, source_documents=None,
              conversion_paths=None, reuse_from=None, fault=None):
        validate_generation_id(generation_id)
        bundle = GenerationBundle.model_validate(bundle.model_dump(mode="json"))
        self._configuration()
        with self._writer():
            reservations = _safe_path(self.root / ".reservations")
            reservations.mkdir(exist_ok=True)
            destination = _safe_path(self.root / generation_id)
            reservation = reservations / generation_id
            if destination.exists() or reservation.exists():
                raise ValueError("generation_id_immutable")
            reservation.mkdir()
            _sync_directory(reservations)
            stage_root = _safe_path(self.root / ".staging")
            stage_root.mkdir(exist_ok=True)
            stage = stage_root / generation_id
            stage.mkdir()
            policy, ledger_digest = self._policy(), self.ledger.state_digest()
            if set(policy.blocked_source_ids).intersection(source.source_id for source in bundle.sources):
                raise ValueError("generation_contains_blocked_source")
            self._reviews(bundle)
            self._physical(bundle, source_paths, source_documents, conversion_paths)
            reused, vectors = [], {}
            if reuse_from is not None:
                old = self.verify(reuse_from)
                old_chunks = {chunk.chunk_id: chunk for chunk in old.artifacts.bundle.chunks}
                compatible = (old.manifest.ledger_digest == ledger_digest
                    and old.manifest.relation_registry_digest == self.ledger.relation_state_digest()
                    and old.manifest.policy_digest == policy.digest
                    and old.artifacts.bundle.relations == bundle.relations
                    and old.artifacts.bundle.resolutions == bundle.resolutions
                    and old.artifacts.bundle.relation_review_sources == bundle.relation_review_sources
                    and old.artifacts.bundle.family_review_sources == bundle.family_review_sources)
                for chunk in bundle.chunks:
                    if compatible and old_chunks.get(chunk.chunk_id) == chunk:
                        vectors[chunk.chunk_id] = old.artifacts.vectors[chunk.chunk_id]
                        reused.append(chunk.chunk_id)
            pending = [chunk for chunk in bundle.chunks if chunk.chunk_id not in vectors]
            for offset in range(0, len(pending), 64):
                batch = pending[offset:offset + 64]
                encoded = self.embeddings.embed_documents([chunk.text_search for chunk in batch])
                if len(encoded) != len(batch):
                    raise ValueError("embedding_batch_cardinality_invalid")
                vectors.update({chunk.chunk_id: float32_vector(vector, self.configuration.embedding_dimension)
                                for chunk, vector in zip(batch, encoded)})
            descriptor = self.vector_store.write(stage, "g" + uuid4().hex, bundle.chunks, vectors)
            _fault(fault, "after_dense")
            checksums = write_artifacts(stage, bundle, vectors=vectors, configuration=self.configuration, fault=fault)
            manifest = GenerationManifest(generation_id=generation_id, configuration=self.configuration,
                bundle_sha256=bundle.fingerprint, artifact_checksums=checksums, vector=descriptor,
                journal_id=self.journal_id, policy_epoch=policy.policy_epoch, policy_digest=policy.digest,
                ledger_digest=ledger_digest, relation_registry_digest=self.ledger.relation_state_digest(),
                chunk_ids=[chunk.chunk_id for chunk in bundle.chunks], unit_ids=[unit.unit_id for unit in bundle.units],
                reused_chunk_ids=reused)
            _write(stage / "manifest.json", canonical_bytes(manifest))
            self._load(stage, manifest, content_digest(manifest))
            self._reviews(bundle)
            self._physical(bundle, source_paths, source_documents, conversion_paths)
            if self._policy() != policy or self.ledger.state_digest() != ledger_digest:
                raise ValueError("generation_authority_changed_during_build")
            _fault(fault, "before_ready")
            with self._commit_guard(bundle, ledger_digest=ledger_digest, policy=policy):
                _write(stage / "READY", canonical_bytes({"schema_version": "ready-v1", "manifest_sha256": content_digest(manifest)}))
                _fault(fault, "after_ready")
                os.rename(stage, destination)
                _sync_directory(self.root)
            return manifest

    def _load(self, directory, manifest, manifest_hash):
        self._configuration()
        snapshot = self._policy()
        if (snapshot.policy_epoch < manifest.policy_epoch
                or (snapshot.policy_epoch == manifest.policy_epoch and snapshot.digest != manifest.policy_digest)):
            raise ValueError("generation_policy_authority_behind_or_divergent")
        if manifest.configuration != self.configuration or manifest.journal_id != self.journal_id:
            raise ValueError("generation_configuration_or_authority_incompatible")
        artifacts = read_artifacts(directory, checksums=manifest.artifact_checksums, configuration=self.configuration)
        if (artifacts.bundle.fingerprint != manifest.bundle_sha256
                or [chunk.chunk_id for chunk in artifacts.bundle.chunks] != manifest.chunk_ids
                or [unit.unit_id for unit in artifacts.bundle.units] != manifest.unit_ids):
            raise ValueError("generation_canonical_manifest_mismatch")
        try:
            projection = self.vector_store.read(directory, manifest.vector)
        except (ChromaError, HTTPError) as error:
            raise ValueError("generation_dense_backend_unavailable") from error
        expected = {chunk.chunk_id: {"text_search": chunk.text_search, "canonical_sha256": content_digest(chunk),
                    "vector": artifacts.vectors[chunk.chunk_id], "source_id": chunk.source_id,
                    "unit_id": chunk.unit_id, "instrument_id": chunk.instrument_id} for chunk in artifacts.bundle.chunks}
        if projection != expected:
            raise ValueError("generation_dense_canonical_parity_mismatch")
        return LoadedGeneration(manifest, artifacts, directory, manifest_hash)

    def verify(self, generation_id):
        validate_generation_id(generation_id)
        self._policy()
        directory = _safe_path(self.root / generation_id)
        try:
            raw = _safe_path(directory / "manifest.json").read_bytes()
            manifest = GenerationManifest.model_validate_json(raw)
            manifest_hash = hashlib.sha256(raw).hexdigest()
            ready = _safe_path(directory / "READY").read_bytes()
            if (manifest.generation_id != generation_id or raw != canonical_bytes(manifest)
                    or ready != canonical_bytes({"schema_version": "ready-v1", "manifest_sha256": manifest_hash})):
                raise ValueError("generation_ready_identity_invalid")
            return self._load(directory, manifest, manifest_hash)
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("generation_not_ready") from error

    def _access(self, access, permission, *, snapshot=None):
        access = AccessContext.model_validate(access.model_dump(mode="json"))
        snapshot = self._policy() if snapshot is None else snapshot
        if (permission not in access.permissions or access.credential_id in snapshot.blocked_credential_ids
                or access.policy_epoch != snapshot.policy_epoch):
            raise ValueError("generation_access_denied_or_stale")
        return snapshot

    def promote(self, generation_id, *, access, fault=None):
        with self._writer():
            snapshot = self._access(access, "operate")
            ledger_digest = self.ledger.state_digest()
            loaded = self.verify(generation_id)
            allowed = {source.source_id for source in loaded.artifacts.bundle.sources} - set(snapshot.blocked_source_ids)
            if not allowed:
                raise ValueError("generation_has_no_authorized_sources")
            self._reviews(loaded.artifacts.bundle, source_ids=allowed)
            _fault(fault, "before_pointer")
            self._access(access, "operate")
            pointer = {"schema_version": "active-v1", "generation_id": generation_id,
                       "manifest_sha256": loaded.manifest_sha256, "journal_id": snapshot.journal_id,
                       "policy_epoch": snapshot.policy_epoch, "policy_digest": snapshot.digest}
            descriptor, temporary = tempfile.mkstemp(prefix="active-", dir=self.root)
            try:
                with os.fdopen(descriptor, "wb") as target:
                    target.write(canonical_bytes(pointer))
                    target.flush()
                    os.fsync(target.fileno())
                _safe_path(self.root / "active.json")
                with self._commit_guard(loaded.artifacts.bundle, ledger_digest=ledger_digest, access=access):
                    os.replace(temporary, self.root / "active.json")
                    _sync_directory(self.root)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            _fault(fault, "after_pointer")
            return loaded.manifest

    def rollback(self, generation_id, *, access, fault=None):
        return self.promote(generation_id, access=access, fault=fault)

    def recover(self, generation_id, *, access, fault=None):
        """Finish an already verified READY staging directory, without promotion."""
        validate_generation_id(generation_id)
        with self._writer():
            snapshot = self._access(access, "operate")
            ledger_digest = self.ledger.state_digest()
            destination = _safe_path(self.root / generation_id)
            if destination.exists():
                raise ValueError("generation_id_immutable")
            stage = _safe_path(self.root / ".staging" / generation_id)
            if not (self.root / ".reservations" / generation_id).is_dir():
                raise ValueError("generation_recovery_reservation_missing")
            raw = _safe_path(stage / "manifest.json").read_bytes()
            manifest = GenerationManifest.model_validate_json(raw)
            manifest_hash = hashlib.sha256(raw).hexdigest()
            if (manifest.generation_id != generation_id or raw != canonical_bytes(manifest)
                    or _safe_path(stage / "READY").read_bytes() != canonical_bytes({"schema_version": "ready-v1", "manifest_sha256": manifest_hash})):
                raise ValueError("generation_recovery_not_ready")
            loaded = self._load(stage, manifest, manifest_hash)
            allowed = {source.source_id for source in loaded.artifacts.bundle.sources} - set(snapshot.blocked_source_ids)
            if not allowed:
                raise ValueError("generation_recovery_no_authorized_sources")
            self._reviews(loaded.artifacts.bundle, source_ids=allowed)
            _fault(fault, "before_recovery")
            with self._commit_guard(loaded.artifacts.bundle, ledger_digest=ledger_digest, access=access):
                os.rename(stage, destination)
                _sync_directory(self.root)
            return manifest

    def _active(self):
        try:
            raw = _safe_path(self.root / "active.json").read_bytes()
            pointer = json.loads(raw)
            if (set(pointer) != {"schema_version", "generation_id", "manifest_sha256", "journal_id", "policy_epoch", "policy_digest"}
                    or raw != canonical_bytes(pointer) or pointer["schema_version"] != "active-v1"
                    or pointer["journal_id"] != self.journal_id or type(pointer["policy_epoch"]) is not int
                    or pointer["policy_epoch"] < 0):
                raise ValueError("generation_active_pointer_invalid")
            loaded = self.verify(pointer["generation_id"])
            snapshot = self._policy()
            if loaded.manifest_sha256 != pointer["manifest_sha256"] or snapshot.policy_epoch < pointer["policy_epoch"]:
                raise ValueError("generation_active_identity_invalid")
            if snapshot.policy_epoch == pointer["policy_epoch"] and snapshot.digest != pointer["policy_digest"]:
                raise ValueError("generation_active_policy_mismatch")
            return loaded
        except (OSError, TypeError, KeyError, json.JSONDecodeError) as error:
            raise ValueError("generation_active_unavailable") from error

    def pin(self, access, *, evidence_scope="linked_instruments"):
        snapshot = self._access(access, "query")
        if evidence_scope not in {"linked_instruments", "original_text"}:
            raise ValueError("generation_evidence_scope_invalid")
        loaded = self._active()
        pin = PinnedGeneration(self, loaded, access.model_copy(deep=True), snapshot, evidence_scope)
        pin.allowed_sources()
        return pin

    def readiness(self):
        try:
            loaded = self._active()
            allowed = {source.source_id for source in loaded.artifacts.bundle.sources} - set(self._policy().blocked_source_ids)
            if not allowed:
                return False
            self._reviews(loaded.artifacts.bundle, source_ids=allowed)
            return True
        except (ValueError, OSError):
            return False


@dataclass(frozen=True)
class PinnedGeneration:
    manager: GenerationManager
    loaded: LoadedGeneration
    access: AccessContext
    admission_policy: object
    evidence_scope: str

    @property
    def generation_id(self):
        return self.loaded.manifest.generation_id

    def allowed_sources(self):
        return self._allowed_sources()

    def _allowed_sources(self, *, held_policy=None):
        # Only finish supplies a snapshot yielded under the journal's lease.
        # Ordinary admission must still recapture policy after slow reads.
        snapshot = self.manager._access(self.access, "query", snapshot=held_policy)
        bundle = self.loaded.artifacts.bundle
        allowed = {source.source_id for source in bundle.sources} - set(snapshot.blocked_source_ids)
        self.manager._reviews(bundle, source_ids=allowed)
        if self.evidence_scope == "linked_instruments":
            registry = self.manager.ledger.relation_state_digest()
            if registry != self.loaded.manifest.relation_registry_digest:
                raise ValueError("generation_relation_registry_stale")
            for resolution in bundle.resolutions:
                from app.ingestion.closure import _components, _family_review, _relation_review, _relation_units
                sources = {source.source_id: source for source in bundle.sources}
                anchor = bundle.family_review_sources.get(resolution.family_id)
                if anchor not in sources:
                    raise ValueError("generation_family_anchor_missing")
                component = _components({sources[anchor].instrument_id}, bundle.relations)[0]
                relevant = [relation for relation in bundle.relations if relation.from_instrument_id in component]
                _family_review(component, relevant, [resolution], bundle.family_review_sources, sources, self.manager.ledger, {})
                for relation in relevant:
                    _relation_review(relation, bundle.relation_review_sources, sources, self.manager.ledger, {}, {})
                    if relation.state == "approved":
                        _relation_units(relation, {unit.unit_id: unit for unit in bundle.units})
                if any(source.instrument_id in component and source.source_id not in allowed for source in bundle.sources):
                    allowed -= {source.source_id for source in bundle.sources if source.instrument_id in component}
                if resolution.family_id in snapshot.blocked_family_ids:
                    required = snapshot.family_required_registry_digests.get(resolution.family_id)
                    if required is None or resolution.state != "resolved" or resolution.registry_digest != required or required != registry:
                        anchor = bundle.family_review_sources.get(resolution.family_id)
                        instruments = {source.instrument_id for source in bundle.sources if source.source_id == anchor}
                        changed = True
                        while changed:
                            previous = set(instruments)
                            for relation in bundle.relations:
                                if {relation.from_instrument_id, relation.to_instrument_id} & instruments:
                                    instruments.update((relation.from_instrument_id, relation.to_instrument_id))
                            changed = previous != instruments
                        allowed -= {source.source_id for source in bundle.sources if source.instrument_id in instruments}
            if set(snapshot.blocked_family_ids) - {resolution.family_id for resolution in bundle.resolutions}:
                raise ValueError("generation_blocked_family_mapping_unavailable")
        # Ledger/registry reads can be slow. Recheck admission at their end,
        # not only before them; a confirmed concurrent revoke must win.
        self.manager._access(self.access, "query", snapshot=held_policy)
        return allowed

    def finish(self, chunk_ids):
        chunk_ids = tuple(chunk_ids)
        ledger_before = self.manager.ledger.state_digest()
        allowed = self.allowed_sources()
        chunks = {chunk.chunk_id: chunk for chunk in self.loaded.artifacts.bundle.chunks}
        if len(set(chunk_ids)) != len(chunk_ids) or any(key not in chunks or chunks[key].source_id not in allowed for key in chunk_ids):
            raise ValueError("generation_result_not_authorized")
        # Complete potentially slow defensive copies BEFORE committing the
        # prepared local output. A revoke acknowledged during copying must win.
        prepared = [chunks[key].model_copy(deep=True) for key in chunk_ids]
        # Same ledger -> policy order as publication; do not recapture policy
        # under its non-reentrant lease. No copy/model/backend work follows the
        # final authorization. This is a local output boundary, not an HTTP one.
        with self.manager.ledger.hold_current(), self.manager.journal.hold_snapshot() as current:
            if current.journal_id != self.manager.journal_id:
                raise ValueError("policy_authority_identity_changed")
            if self.manager.ledger.state_digest() != ledger_before:
                raise ValueError("generation_review_changed_during_return")
            allowed = self._allowed_sources(held_policy=current)
            if any(chunks[key].source_id not in allowed for key in chunk_ids):
                raise ValueError("generation_result_not_authorized")
            # Proof records, never the lossy Chroma projection. P5/P6 must also
            # resolve closure with its real tokenizer/budget before citing text.
            return prepared

    def probe(self, query, *, k=10):
        """Diagnostic hybrid persistence probe; not a quality-qualified retriever."""
        if type(k) is not int or k < 1 or k > 100:
            raise ValueError("generation_probe_k_invalid")
        allowed = self.allowed_sources()
        if not allowed:
            return self.finish([])
        query_vector = float32_vector(self.manager.embeddings.embed_query(query), self.manager.configuration.embedding_dimension)
        dense = self.manager.vector_store.query(self.loaded.directory, self.loaded.manifest.vector,
            query_vector, allowed_source_ids=sorted(allowed), k=k)
        scores = self.loaded.artifacts.bm25.vectorizer.get_scores(lexical_tokens(query))
        eligible = [(chunk.chunk_id, float(scores[index])) for index, chunk in enumerate(self.loaded.artifacts.bundle.chunks)
                    if chunk.source_id in allowed]
        sparse = sorted(eligible, key=lambda item: (-item[1], item[0]))[:k]
        ranks = {}
        for component in (dense, sparse):
            for rank, (key, _) in enumerate(component, 1):
                ranks[key] = ranks.get(key, 0) + 1 / (60 + rank)
        return self.finish(sorted(ranks, key=lambda key: (-ranks[key], key))[:k])


def main(argv=None):
    """Operator-only local CLI: no corpus/model/policy/backend default or bootstrap."""
    import argparse
    from app.embeddings.qwen import QwenEmbeddings
    from app.retrieval.policy import SQLitePolicyJournal
    from app.retrieval.vector_store import ChromaVectorStore
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["build", "verify", "promote", "rollback", "recover"])
    for name in ("root", "generation", "configuration", "qwen-artifact", "ledger", "journal", "journal-id"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--mode", choices=["embedded", "server"], required=True)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--bundle")
    parser.add_argument("--source-paths")
    parser.add_argument("--source-documents")
    parser.add_argument("--conversion-paths")
    parser.add_argument("--reuse-from")
    arguments = parser.parse_args(argv)
    if arguments.command == "build" and (not arguments.bundle or not arguments.source_paths):
        parser.error("build requires --bundle and --source-paths; no implicit staging approval")
    config = GenerationConfig.model_validate_json(Path(arguments.configuration).read_bytes())
    if config.synthetic:
        parser.error("CLI requires fixed verified local Qwen; synthetic encoders are test-only explicit injection")
    # Never create a missing review authority through ReviewStore's constructor.
    if not _safe_path(arguments.ledger).is_file():
        parser.error("existing explicit review ledger required")
    journal = SQLitePolicyJournal(Path(arguments.journal), journal_id=arguments.journal_id)
    manager = GenerationManager(Path(arguments.root), configuration=config,
        embeddings=QwenEmbeddings(Path(arguments.qwen_artifact)),
        vector_store=ChromaVectorStore(mode=arguments.mode, dimension=config.embedding_dimension,
                                      host=arguments.host, port=arguments.port),
        ledger=ReadOnlyReviewAuthority(Path(arguments.ledger)), journal=journal, journal_id=arguments.journal_id)
    snapshot = journal.snapshot()
    access = AccessContext(principal_id="local-operator", credential_id="local-cli", permissions=["operate"],
                           policy_epoch=snapshot.policy_epoch, access_scope_digest="local-operator")
    if arguments.command == "build":
        bundle = GenerationBundle.model_validate_json(Path(arguments.bundle).read_bytes())
        paths = json.loads(Path(arguments.source_paths).read_bytes())
        documents = None
        if arguments.source_documents:
            from app.ingestion.schemas import ParsedDocument
            documents = {key: ParsedDocument.model_validate(value) for key, value in
                         json.loads(Path(arguments.source_documents).read_bytes()).items()}
        conversions = json.loads(Path(arguments.conversion_paths).read_bytes()) if arguments.conversion_paths else None
        manifest = manager.build(arguments.generation, bundle, source_paths=paths,
            source_documents=documents, conversion_paths=conversions, reuse_from=arguments.reuse_from)
    elif arguments.command == "verify":
        manifest = manager.verify(arguments.generation).manifest
    else:
        manifest = getattr(manager, arguments.command)(arguments.generation, access=access)
    print(json.dumps({"generation_id": manifest.generation_id, "manifest_sha256": content_digest(manifest),
                      "operation": arguments.command, "published_corpus": False}, sort_keys=True))


if __name__ == "__main__":
    main()

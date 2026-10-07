"""P4 wire records: configuration is explicit; schema validation is not approval."""
import hashlib
import ipaddress
import json
import re
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts import CitationUnit, EvidenceChunk, InstrumentRelation, RelationResolution, SourceIdentity
from app.ingestion.review_store import ReviewRecord
from app.retrieval.lexical import lexical_profile

Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


def canonical_bytes(value) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def content_digest(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def validate_generation_id(value: str) -> str:
    if (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value)
            or value.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                                 *(f"LPT{i}" for i in range(1, 10))}):
        raise ValueError("generation_id_invalid")
    return value


class WireRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class GenerationConfig(WireRecord):
    configuration_version: str = Field(min_length=1, max_length=128)
    embedding_identity: dict
    embedding_dimension: int = Field(ge=1, le=65536, strict=True)
    lexical_identity: dict
    synthetic: bool = False

    @model_validator(mode="after")
    def explicit_identity(self):
        canonical_bytes(self.embedding_identity)
        if self.lexical_identity != lexical_profile():
            raise ValueError("lexical_configuration_incompatible")
        profile = self.embedding_identity.get("profile", self.embedding_identity)
        if profile.get("dimension") != self.embedding_dimension:
            raise ValueError("embedding_dimension_identity_mismatch")
        if not self.synthetic:
            from app.embeddings.qwen import QwenProfile
            expected = QwenProfile().specification
            if (profile != expected or not re.fullmatch(r"[a-f0-9]{64}", self.embedding_identity.get("artifact_sha256", ""))
                    or not re.fullmatch(r"[a-f0-9]{64}", self.embedding_identity.get("implementation_sha256", ""))
                    or set(self.embedding_identity.get("versions", {})) != {"torch", "transformers", "tokenizers", "safetensors"}):
                raise ValueError("production_requires_fixed_verified_qwen_identity")
        return self

    @property
    def fingerprint(self) -> str:
        return content_digest(self)


class PolicySnapshot(WireRecord):
    journal_id: str = Field(min_length=1, max_length=128)
    policy_epoch: int = Field(ge=0, strict=True)
    digest: Sha256
    blocked_source_ids: list[str] = Field(default_factory=list)
    blocked_credential_ids: list[str] = Field(default_factory=list)
    blocked_family_ids: list[str] = Field(default_factory=list)
    family_required_registry_digests: dict[str, Sha256] = Field(default_factory=dict)

    @model_validator(mode="after")
    def distinct_blocks(self):
        if not set(self.family_required_registry_digests).issubset(self.blocked_family_ids):
            raise ValueError("policy_family_registry_requires_block")
        for values in (self.blocked_source_ids, self.blocked_credential_ids, self.blocked_family_ids):
            if len(set(values)) != len(values) or any(not value.strip() for value in values):
                raise ValueError("policy_block_identifiers_invalid")
        return self


class GenerationBundle(WireRecord):
    sources: list[SourceIdentity] = Field(min_length=1)
    units: list[CitationUnit] = Field(min_length=1)
    chunks: list[EvidenceChunk] = Field(min_length=1)
    reviews: list[ReviewRecord] = Field(min_length=1)
    relations: list[InstrumentRelation] = Field(default_factory=list)
    resolutions: list[RelationResolution] = Field(default_factory=list)
    relation_review_sources: dict[str, str] = Field(default_factory=dict)
    family_review_sources: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def complete_proof(self):
        for values, key in ((self.sources, "source_id"), (self.units, "unit_id"), (self.chunks, "chunk_id"),
                            (self.reviews, "record_id"), (self.relations, "relation_id"), (self.resolutions, "family_id")):
            ids = [getattr(value, key) for value in values]
            if len(set(ids)) != len(ids):
                raise ValueError("generation_duplicate_" + key)
        sources = {source.source_id: source for source in self.sources}
        units = {unit.unit_id: unit for unit in self.units}
        reviews = {review.record_id: review for review in self.reviews}
        grouped = {unit_id: [] for unit_id in units}
        for unit in units.values():
            if unit.approval_state != "approved" or unit.review_record_id not in reviews:
                raise ValueError("generation_unit_not_reviewed")
            if any(source_id not in sources for source_id in unit.source_ids):
                raise ValueError("generation_source_missing")
            if any(identity != sources.get(identity.source_id) for identity in unit.source_identities):
                raise ValueError("generation_source_identity_mismatch")
            if (not unit.text_map or set(unit.source_ids) != {identity.source_id for identity in unit.source_identities}
                    or any(target not in units for target in unit.closure_unit_ids)
                    or (unit.parent_unit_id is not None and unit.parent_unit_id not in units)):
                raise ValueError("generation_unit_proof_incomplete")
        for chunk in self.chunks:
            unit = units.get(chunk.unit_id)
            source = sources.get(chunk.source_id)
            if (unit is None or source is None or chunk.approval_state != "approved"
                    or chunk.review_record_id != unit.review_record_id or not chunk.token_count
                    or chunk.unit_end is None or chunk.unit_end - chunk.unit_start != len(chunk.verbatim_text)
                    or chunk.unit_end > len(unit.verbatim_text)
                    or unit.verbatim_text[chunk.unit_start:chunk.unit_end] != chunk.verbatim_text
                    or not chunk.text_search.endswith(chunk.verbatim_text)
                    or chunk.parties != unit.parties or chunk.location != unit.location
                    or chunk.instrument_id != unit.instrument_id or chunk.file_hash != source.file_hash
                    or chunk.file_id != source.file_id or chunk.doc_version != source.doc_version
                    or chunk.parser_name != source.parser_name or chunk.parser_version != source.parser_version
                    or chunk.configuration_version != source.configuration_version):
                raise ValueError("generation_chunk_proof_mismatch")
            from app.ingestion.evidence import _slice_spans
            if (chunk.spans != _slice_spans(unit, chunk.unit_start, chunk.unit_end)
                    or chunk.parent_id != unit.unit_id or chunk.source_id not in unit.source_ids
                    or chunk.block_ids != list(dict.fromkeys(span.block_id for span in chunk.spans))
                    or (chunk.synthetic_context and chunk.text_search != chunk.synthetic_context + chunk.verbatim_text)):
                raise ValueError("generation_chunk_physical_mapping_mismatch")
            grouped[chunk.unit_id].append(chunk)
        for unit_id, chunks in grouped.items():
            cursor = 0
            for chunk in sorted(chunks, key=lambda value: value.unit_start):
                if chunk.unit_start != cursor:
                    raise ValueError("generation_chunk_coverage_gap_or_overlap")
                cursor = chunk.unit_end
            if cursor != len(units[unit_id].verbatim_text):
                raise ValueError("generation_unit_not_fully_indexed")
        return self

    @property
    def fingerprint(self) -> str:
        return content_digest(self)


class VectorDescriptor(WireRecord):
    mode: Literal["embedded", "server"]
    collection: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,62}$")
    path: str | None = None
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535, strict=True)

    @model_validator(mode="after")
    def local_backend(self):
        if self.mode == "embedded":
            if self.path != "chroma" or self.host is not None or self.port is not None:
                raise ValueError("embedded_backend_descriptor_invalid")
        elif (self.path is not None or self.host is None or self.port is None
              or not ipaddress.ip_address(self.host).is_loopback):
            raise ValueError("server_must_be_explicit_loopback_for_local_gate")
        return self


class GenerationManifest(WireRecord):
    schema_version: Literal["generation-v1"] = "generation-v1"
    generation_id: str
    configuration: GenerationConfig
    bundle_sha256: Sha256
    artifact_checksums: dict[str, Sha256]
    vector: VectorDescriptor
    journal_id: str = Field(min_length=1)
    policy_epoch: int = Field(ge=0, strict=True)
    policy_digest: Sha256
    ledger_digest: Sha256
    relation_registry_digest: Sha256
    chunk_ids: list[str] = Field(min_length=1)
    unit_ids: list[str] = Field(min_length=1)
    reused_chunk_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def safe_manifest(self):
        validate_generation_id(self.generation_id)
        for key in self.artifact_checksums:
            path = PurePosixPath(key)
            if path.is_absolute() or ".." in path.parts or "\\" in key or ":" in key:
                raise ValueError("artifact_checksum_path_invalid")
        for values in (self.chunk_ids, self.unit_ids, self.reused_chunk_ids):
            if len(set(values)) != len(values):
                raise ValueError("manifest_duplicate_identifiers")
        if not set(self.reused_chunk_ids).issubset(self.chunk_ids):
            raise ValueError("manifest_reuse_ids_invalid")
        return self

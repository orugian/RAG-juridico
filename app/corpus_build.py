"""Operator step: staged documents + human decisions -> reviewed P4 bundle.

Staging status never approves anything. Approval comes only from an explicit
operator declaration (reviewer and reason are mandatory, no defaults) and is
recorded in the append-only ledger, scope by scope (source, parties, unit,
relation, family), bound to the exact digests of what is being approved.

Reviewer decisions captured in the workbench (``Decisions``) are applied first:
title/party corrections, document exclusions, reference links (by physical
coordinate, so they survive party corrections) and base/amendment relations.

Each document is rehearsed against a scratch ledger; only documents whose
complete governed proof builds are recorded in the real ledger. Documents
without extracted parties are *reserved*: listed for the reviewer, never
approved until parties are supplied. Legacy DOC/RTF/WordPerfect files are
converted with LibreOffice; the DOCX derivative is retained and its SHA-256 is
part of the source identity. Nothing here builds vectors or calls a provider.
"""
import argparse
from dataclasses import dataclass, field
import hashlib
from importlib.metadata import version
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import tempfile
import zipfile

from pydantic import ValidationError

from app.contracts import InstrumentRelation, RelationResolution, SourceIdentity
from app.ingestion.evidence import QwenTokenBudget, build_evidence_chunks, prepare_citation_units
from app.ingestion.review_store import ReviewStore, review_digest, unit_review_digest
from app.ingestion.schemas import ContractParty, ParsedDocument

_STAGED_NAME = re.compile(r"^[a-f0-9]{64}\.json$")
_SUPPORTED_PARSERS = {"docx_parser", "pdf_parser"}
REGISTRY_VERSION = "v1"
NO_PARTIES = "ValueError:Canonical units require successful parsing and actual parties"


@dataclass(frozen=True)
class Approval:
    """Explicit reviewer declaration; every relaxation is opt-in and reported.

    ``self_bind_unresolved_references``: references without a reviewer link that
    the extractor cannot bind to a unique local label are recorded as reviewed
    mappings to their own unit. The literal reference stays in the cited text,
    but the referenced clause is not pulled into the closure automatically.
    """
    reviewer: str
    reason: str
    self_bind_unresolved_references: bool = False

    def __post_init__(self):
        if not self.reviewer.strip() or not self.reason.strip():
            raise ValueError("approval_requires_explicit_reviewer_and_reason")


@dataclass(frozen=True)
class RelationDecision:
    from_instrument_id: str
    to_instrument_id: str
    relation_type: str
    affected_block_ids: tuple[str, ...]
    support_block_ids: tuple[str, ...]


@dataclass
class Decisions:
    """Reviewer decisions to apply at publication; empty by default."""
    titles: dict = field(default_factory=dict)        # source_id -> title
    parties: dict = field(default_factory=dict)       # source_id -> [ContractParty]
    excluded: dict = field(default_factory=dict)      # source_id -> reason
    references: dict = field(default_factory=dict)    # (source_id, block_id, start, end) -> (kind, [block_ids])
    relations: list = field(default_factory=list)     # [RelationDecision]


@dataclass(frozen=True)
class StagedSource:
    document: ParsedDocument
    identity: SourceIdentity
    original_path: Path
    conversion_path: Path | None = None
    mfiles_id: int | None = None


@dataclass
class BuildResult:
    bundle: object
    source_paths: dict
    conversion_paths: dict
    source_documents: dict
    rejected: list
    reserved: list
    excluded: list
    self_bound: int
    reviewer_links: int
    relations: int
    skipped_relations: list


def _sha256(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


_FIXED_CODE = re.compile(r"^[a-z][a-z0-9_]*(:[a-z0-9_ ]+)?$|^Canonical units require successful parsing and actual parties$"
                         r"|^Synthetic context exhausts token budget$")


def _failure(error: BaseException) -> str:
    """Fixed-shape failure code; never free text that might quote a contract."""
    if isinstance(error, ValidationError):
        return "validation:" + ",".join(sorted({item["type"] for item in error.errors()}))
    message = str(error)
    if not _FIXED_CODE.fullmatch(message):
        message = "detail_withheld"
    return f"{type(error).__name__}:{message}"


def _original_path(raw_files_root: Path, recorded: str) -> Path:
    """Re-anchor a recorded original under the local raw/files root (<doc>/<file>)."""
    parts = (PureWindowsPath(recorded) if "\\" in recorded else PurePosixPath(recorded)).parts
    if len(parts) < 2:
        raise ValueError("staged_original_path_invalid")
    root = Path(raw_files_root).resolve()
    candidate = (root / parts[-2] / parts[-1]).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("staged_original_outside_raw_root")
    return candidate


def _current_blocks(document: ParsedDocument, original: Path):
    from app.ingestion.docx_parser import extract_docx_blocks
    from app.ingestion.pdf_parser import extract_pdf_blocks
    extractor = extract_docx_blocks if document.parser_name == "docx_parser" else extract_pdf_blocks
    return extractor(original, doc_id=document.doc_id, doc_version=document.doc_version)


class LegacyConverter:
    """LibreOffice DOC/RTF/WPD -> retained DOCX derivative, reused across publications."""

    def __init__(self, curation_rows: dict, conversions_dir: Path, soffice: str, *, timeout_seconds: int = 120):
        self.rows, self.directory, self.soffice, self.timeout = curation_rows, Path(conversions_dir), soffice, timeout_seconds
        self.directory.mkdir(parents=True, exist_ok=True)

    def entry(self, staged):
        row = self.rows.get(staged["mfiles_id"])
        if row is None:
            raise ValueError("legacy_curation_row_missing")
        matches = [item for item in row.get("files", []) if str(item.get("file_id")) == str(staged["file_id"])]
        if len(matches) != 1:
            raise ValueError("legacy_curation_file_missing")
        return row, matches[0]

    def convert(self, original: Path, digest: str, stem: str) -> Path:
        from app.ingestion.legacy_parser import convert_legacy_to_docx
        target = self.directory / f"{stem}-{digest[:16]}.docx"
        if target.is_file() and zipfile.is_zipfile(target):
            return target
        with tempfile.TemporaryDirectory(prefix="legacy-convert-") as scratch:
            work = Path(scratch) / f"{stem}{original.suffix.lower()}"
            work.write_bytes(original.read_bytes())
            converted = convert_legacy_to_docx(work, Path(scratch) / "out", timeout_sec=self.timeout,
                                               libreoffice_cmd=self.soffice)
            if not zipfile.is_zipfile(converted):
                raise ValueError("legacy_conversion_not_docx")
            target.write_bytes(converted.read_bytes())
        return target

    def document(self, staged, raw_files_root: Path):
        from app.ingestion.docx_parser import extract_docx_blocks
        from app.ingestion.metadata_extractor import extract_contract_metadata
        from app.ingestion.parsing_pipeline import reconcile_document
        row, entry = self.entry(staged)
        original = _original_path(raw_files_root, entry["path"])
        if not original.is_file() or _sha256(original) != staged["file_hash"]:
            raise ValueError("staged_original_missing_or_changed")
        derivative = self.convert(original, staged["file_hash"], f"{staged['mfiles_id']}_{staged['file_id']}")
        blocks = extract_docx_blocks(derivative, doc_id=staged["mfiles_id"], doc_version=staged["mfiles_version"])
        if not blocks:
            raise ValueError("legacy_conversion_without_text")
        suffix = original.suffix.lower()
        for block in blocks:
            block.source_locator = {**(block.source_locator or {}), "format": "converted_docx",
                                    "original_format": suffix, "conversion_artifact_retained": True}
        document = ParsedDocument(doc_id=staged["mfiles_id"], doc_version=staged["mfiles_version"],
                                  file_id=str(staged["file_id"]), file_path=str(original), file_hash=staged["file_hash"],
                                  parser_name="legacy_parser", parser_version="1.0.0", blocks=blocks,
                                  metadata=extract_contract_metadata(blocks), status="success")
        return reconcile_document(mfiles_record=row, parsed_doc=document), original, derivative


def load_staged_sources(staging_dir: Path, raw_files_root: Path, *, accept_metadata_mismatch: bool = False,
                        legacy: LegacyConverter | None = None):
    """Return (sources, skipped). Skips only what can never become a citation.

    ``accept_metadata_mismatch`` is the reviewer's explicit decision that the
    M-Files/extracted-parties discrepancy flagged by staging was adjudicated.
    It is never implied; documents without extracted parties still fail later.
    """
    sources, skipped, seen = [], [], set()
    for path in sorted(Path(staging_dir).iterdir()):
        if not _STAGED_NAME.fullmatch(path.name):
            continue
        entry = json.loads(path.read_text(encoding="utf-8"))
        parsed = entry.get("parsed_document") or {}
        key = {"staged": path.name, "mfiles_id": entry.get("mfiles_id"),
               "technical_status": entry.get("technical_status")}
        conversion = None
        try:
            if entry.get("parse_route") == "libreoffice" and not parsed.get("blocks"):
                if legacy is None:
                    skipped.append(key | {"reason": "no_parsed_text"})
                    continue
                document, original, conversion = legacy.document(entry, raw_files_root)
                status = document.status
            else:
                if not parsed.get("blocks"):
                    skipped.append(key | {"reason": "no_parsed_text"})
                    continue
                if parsed.get("parser_name") not in _SUPPORTED_PARSERS:
                    skipped.append(key | {"reason": "parser_not_supported"})
                    continue
                status = parsed.get("status")
                original = _original_path(raw_files_root, parsed["file_path"])
                if not original.is_file() or _sha256(original) != entry["file_hash"]:
                    raise ValueError("staged_original_missing_or_changed")
                document = ParsedDocument.model_validate(parsed | {"file_path": str(original), "status": "success"})
                if document.file_hash != entry["file_hash"]:
                    raise ValueError("staged_hash_mismatch")
                # Staged blocks predate the current verified parser identifiers; the
                # literal proof is always re-derived from the hash-checked original.
                document = document.model_copy(update={"blocks": _current_blocks(document, original)})
            if status == "review_metadata_mismatch":
                if not accept_metadata_mismatch:
                    skipped.append(key | {"reason": "metadata_mismatch_not_adjudicated"})
                    continue
                document = document.model_copy(update={"status": "success"})
            identity = SourceIdentity(
                source_id=f"doc:{document.doc_id}:v{document.doc_version}:file:{document.file_id}",
                instrument_id=f"doc:{document.doc_id}",
                doc_version=document.doc_version, file_id=document.file_id,
                file_hash=entry["file_hash"], snapshot=entry["snapshot"],
                configuration_version=entry["configuration_version"],
                parser_name=document.parser_name, parser_version=document.parser_version,
                conversion_sha256=_sha256(conversion) if conversion else None)
        except Exception as error:  # Conversion/parsing failures skip only this document.
            skipped.append(key | {"reason": _failure(error)})
            continue
        if identity.source_id in seen:
            raise ValueError("staged_duplicate_source")
        seen.add(identity.source_id)
        sources.append(StagedSource(document, identity, original, conversion, entry.get("mfiles_id")))
    return sources, skipped


def apply_document_decisions(staged: StagedSource, decisions: Decisions) -> StagedSource:
    """Reviewer title/party corrections replace the extracted metadata."""
    source_id = staged.identity.source_id
    updates = {}
    if source_id in decisions.titles:
        updates["formal_title"] = decisions.titles[source_id]
    if source_id in decisions.parties:
        updates["parties"] = [ContractParty.model_validate(p.model_dump() if hasattr(p, "model_dump") else p)
                              for p in decisions.parties[source_id]]
    if not updates:
        return staged
    metadata = staged.document.metadata.model_copy(update=updates)
    return StagedSource(staged.document.model_copy(update={"metadata": metadata}), staged.identity,
                        staged.original_path, staged.conversion_path, staged.mfiles_id)


def _record(ledger, identity, approval, **scope):
    return ledger.record(source_id=identity.source_id, snapshot=identity.snapshot,
                         file_hash=identity.file_hash, configuration_version=identity.configuration_version,
                         decision="approved", reviewer=approval.reviewer, reason=approval.reason, **scope)


def _bindings(units, decisions: Decisions, approval: Approval):
    """Reviewer links first (by physical coordinate); then the opt-in self-binding."""
    by_block = {block_id: unit.unit_id for unit in units for block_id in unit.block_ids}
    bindings, reviewer_links, self_bound = {}, 0, 0
    for unit in units:
        for reference in unit.references:
            if reference.state == "bound":
                continue
            coordinate = (unit.source_ids[0], reference.block_id, reference.start, reference.end)
            decision = decisions.references.get(coordinate)
            if decision and decision[0] == "bind":
                targets = list(dict.fromkeys(by_block[block] for block in decision[1] if block in by_block))
                if not targets:
                    raise ValueError("reference_decision_target_missing")
                bindings[reference.reference_id] = targets
                reviewer_links += 1
            elif decision or approval.self_bind_unresolved_references:
                bindings[reference.reference_id] = [unit.unit_id]
                self_bound += 1
    return bindings, reviewer_links, self_bound


def _prepare(staged: StagedSource, bindings=None):
    return prepare_citation_units(staged.document, staged.identity, original_path=staged.original_path,
                                  conversion_path=staged.conversion_path, reference_bindings=bindings)


def approve_source(staged: StagedSource, *, ledger, budget, approval: Approval, decisions: Decisions | None = None):
    """Record source/parties/unit approvals for one document and build its proof.

    Returns (EvidenceBuildResult, self-bound references, reviewer links).
    """
    decisions = decisions or Decisions()
    units = _prepare(staged)
    bindings, reviewer_links, self_bound = _bindings(units, decisions, approval)
    if bindings:
        units = _prepare(staged, bindings)
    identity = staged.identity
    _record(ledger, identity, approval)
    _record(ledger, identity, approval, scope="parties", subject_id=identity.source_id,
            subject_digest=review_digest({"contract_title": units[0].contract_title,
                                          "parties": [p.model_dump(mode="json") for p in units[0].parties]}))
    review_ids = {unit.unit_id: _record(ledger, identity, approval, scope="unit", subject_id=unit.unit_id,
                                        subject_digest=unit_review_digest(unit)).record_id for unit in units}
    built = build_evidence_chunks(staged.document, identity, ledger=ledger, budget=budget,
                                  original_path=staged.original_path, unit_review_ids=review_ids,
                                  conversion_path=staged.conversion_path, reference_bindings=bindings or None)
    return built, self_bound, reviewer_links


def rehearse(staged: StagedSource, *, budget, approval: Approval, decisions: Decisions | None = None):
    """Build the full governed proof against a throwaway ledger; returns failure code or None."""
    with tempfile.TemporaryDirectory(prefix="rag-rehearsal-") as scratch:
        try:
            approve_source(staged, ledger=ReviewStore(Path(scratch) / "reviews.sqlite"),
                           budget=budget, approval=approval, decisions=decisions)
        except Exception as error:  # Any failure excludes the document; nothing is recorded.
            return _failure(error)
    return None


def _relations(decisions, built, sources_by_instrument, ledger, approval):
    """Approved base/amendment relations; both instruments must be in the bundle."""
    relations, review_sources, skipped = [], {}, []
    units_by_instrument = {}
    for result in built.values():
        for unit in result.units:
            units_by_instrument.setdefault(unit.instrument_id, []).append(unit)
    for item in decisions.relations:
        source = sources_by_instrument.get(item.from_instrument_id)
        if source is None or item.to_instrument_id not in sources_by_instrument:
            skipped.append({"from": item.from_instrument_id, "to": item.to_instrument_id, "reason": "instrument_not_published"})
            continue
        def units_for(instrument, blocks):
            chosen = [unit for unit in units_by_instrument[instrument] if set(unit.block_ids) & set(blocks)]
            return sorted(chosen, key=lambda unit: unit.unit_id)
        affected = units_for(item.to_instrument_id, item.affected_block_ids)
        support = units_for(item.from_instrument_id, item.support_block_ids)
        if not affected or not support:
            skipped.append({"from": item.from_instrument_id, "to": item.to_instrument_id, "reason": "relation_blocks_missing"})
            continue
        relation = InstrumentRelation(
            relation_id=f"rel:{item.from_instrument_id.removeprefix('doc:')}:{item.relation_type}:"
                        f"{item.to_instrument_id.removeprefix('doc:')}",
            from_instrument_id=item.from_instrument_id, to_instrument_id=item.to_instrument_id,
            relation_type=item.relation_type, state="approved", snapshot=source.snapshot,
            configuration_version=source.configuration_version,
            affected_unit_ids=[unit.unit_id for unit in affected], support_unit_ids=[unit.unit_id for unit in support],
            support_spans=[span for unit in support for span in unit.spans], review_record_id="pending-record")
        relation.review_record_id = _record(
            ledger, source, approval, scope="relation", subject_id=relation.relation_id,
            subject_digest=review_digest(relation.model_dump(mode="json", exclude={"review_record_id"}))).record_id
        relations.append(relation)
        review_sources[relation.relation_id] = source.source_id
    return relations, review_sources, skipped


def _components(instruments, relations):
    parent = {instrument: instrument for instrument in instruments}

    def root(item):
        while parent[item] != item:
            item = parent[item]
        return item
    for relation in relations:
        parent[root(relation.from_instrument_id)] = root(relation.to_instrument_id)
    groups = {}
    for instrument in sorted(instruments, key=lambda value: int(value.split(":")[1])):
        groups.setdefault(root(instrument), []).append(instrument)
    return list(groups.values())


def build_reviewed_bundle(staged_sources, *, ledger, budget, approval: Approval, decisions: Decisions | None = None):
    """Rehearse every document, then record approvals for the eligible ones only."""
    from app.retrieval.generation_contracts import GenerationBundle
    decisions = decisions or Decisions()
    eligible, rejected, reserved, excluded = [], [], [], []
    for staged in staged_sources:
        if staged.identity.source_id in decisions.excluded:
            excluded.append({"source_id": staged.identity.source_id, "instrument_id": staged.identity.instrument_id,
                             "reason": decisions.excluded[staged.identity.source_id]})
            continue
        staged = apply_document_decisions(staged, decisions)
        code = rehearse(staged, budget=budget, approval=approval, decisions=decisions)
        item = {"source_id": staged.identity.source_id, "instrument_id": staged.identity.instrument_id,
                "mfiles_id": staged.mfiles_id, "title": staged.document.metadata.formal_title,
                "parser": staged.identity.parser_name}
        if code is None:
            eligible.append(staged)
        elif code == NO_PARTIES:
            reserved.append(item | {"reason": "parties_missing"})
        else:
            rejected.append(item | {"reason": code})
    if not eligible:
        raise ValueError("no_document_passed_governed_rehearsal")
    built, self_bound, reviewer_links = {}, 0, 0
    for staged in eligible:
        built[staged.identity.source_id], bound, links = approve_source(
            staged, ledger=ledger, budget=budget, approval=approval, decisions=decisions)
        self_bound, reviewer_links = self_bound + bound, reviewer_links + links
    sources_by_instrument = {staged.identity.instrument_id: staged.identity for staged in eligible}
    relations, relation_sources, skipped_relations = _relations(decisions, built, sources_by_instrument, ledger, approval)
    registry = ledger.relation_state_digest()
    resolutions, family_sources = [], {}
    for component in _components(set(sources_by_instrument), relations):
        anchor = sources_by_instrument[component[0]]
        family_id = "fam:" + component[0].removeprefix("doc:")
        members = set(component)
        resolution = RelationResolution(family_id=family_id, snapshot=anchor.snapshot,
                                        registry_version=REGISTRY_VERSION, registry_digest=registry,
                                        relation_ids=[r.relation_id for r in relations if r.from_instrument_id in members],
                                        state="resolved", review_record_id="pending-record")
        resolution.review_record_id = _record(
            ledger, anchor, approval, scope="family", subject_id=family_id,
            subject_digest=review_digest(resolution.model_dump(mode="json", exclude={"review_record_id"}))).record_id
        resolutions.append(resolution)
        family_sources[family_id] = anchor.source_id
    bundle = GenerationBundle(
        sources=[staged.identity for staged in eligible],
        units=[unit for result in built.values() for unit in result.units],
        chunks=[chunk for result in built.values() for chunk in result.chunks],
        reviews=[record for staged in eligible for record in ledger.history(staged.identity.source_id)],
        relations=relations, resolutions=resolutions, relation_review_sources=relation_sources,
        family_review_sources=family_sources)
    paths = {staged.identity.source_id: str(staged.original_path) for staged in eligible}
    conversions = {staged.identity.source_id: str(staged.conversion_path) for staged in eligible if staged.conversion_path}
    documents = {staged.identity.source_id: staged.document for staged in eligible if staged.conversion_path}
    return BuildResult(bundle, paths, conversions, documents, rejected, reserved, excluded, self_bound,
                       reviewer_links, len(relations), skipped_relations)


def qwen_identity(artifact: Path) -> dict:
    """Same identity the runtime encoder reports, computed without loading weights."""
    from app.embeddings import qwen
    from app.embeddings.artifacts import digest_file, verify_artifact
    return {"artifact_sha256": verify_artifact(Path(artifact)),
            "implementation_sha256": digest_file(Path(qwen.__file__)),
            "versions": {name: version(name) for name in ("torch", "transformers", "tokenizers", "safetensors")},
            "profile": qwen.QwenProfile().specification}


def load_curation_rows(path: Path) -> dict:
    rows = {}
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                row = json.loads(line)
                rows[row["mfiles_id"]] = row
    return rows


def write_outputs(output: Path, result: BuildResult, *, qwen_artifact: Path, approval: Approval,
                  accept_metadata_mismatch: bool, skipped: list, staged_count: int, journal_id: str) -> dict:
    from app.retrieval.generation_contracts import GenerationConfig
    from app.retrieval.lexical import lexical_profile
    versions = {source.configuration_version for source in result.bundle.sources}
    if len(versions) != 1:
        raise ValueError("bundle_mixed_configuration_versions")
    configuration = GenerationConfig(configuration_version=versions.pop(),
                                     embedding_identity=qwen_identity(Path(qwen_artifact)),
                                     embedding_dimension=1024, lexical_identity=lexical_profile())
    output.mkdir(parents=True, exist_ok=True)
    (output / "bundle.json").write_text(result.bundle.model_dump_json(), encoding="utf-8")
    (output / "source_paths.json").write_text(json.dumps(result.source_paths, ensure_ascii=False, indent=1), encoding="utf-8")
    (output / "conversion_paths.json").write_text(json.dumps(result.conversion_paths, ensure_ascii=False, indent=1), encoding="utf-8")
    (output / "source_documents.json").write_text(json.dumps(
        {key: value.model_dump(mode="json") for key, value in result.source_documents.items()}, ensure_ascii=False), encoding="utf-8")
    (output / "configuration.json").write_text(configuration.model_dump_json(), encoding="utf-8")
    report = {"schema": "reviewed-bundle-report-v2",
              "approval": {"reviewer": approval.reviewer, "reason": approval.reason,
                           "accept_metadata_mismatch": accept_metadata_mismatch,
                           "self_bind_unresolved_references": approval.self_bind_unresolved_references},
              "staged_documents": staged_count, "skipped": skipped, "rejected": result.rejected,
              "reserved": result.reserved, "excluded": result.excluded,
              "self_bound_references": result.self_bound, "reviewer_reference_links": result.reviewer_links,
              "relations": result.relations, "skipped_relations": result.skipped_relations,
              "legacy_converted": len(result.conversion_paths),
              "eligible_sources": len(result.bundle.sources), "units": len(result.bundle.units),
              "chunks": len(result.bundle.chunks), "families": len(result.bundle.resolutions),
              "journal_id": journal_id, "bundle_fingerprint": result.bundle.fingerprint, "published": False}
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def main(argv=None):
    from app.retrieval.policy import SQLitePolicyJournal
    parser = argparse.ArgumentParser(description="Record declared approvals and write a reviewed P4 bundle")
    for name in ("staging", "raw-files", "qwen-artifact", "authority-dir", "output-dir", "journal-id",
                 "reviewer", "reason"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--accept-metadata-mismatch", action="store_true",
                        help="Reviewer adjudicated the staging client/parties discrepancy flags")
    parser.add_argument("--self-bind-unresolved-references", action="store_true",
                        help="Reviewer accepts unresolved references without closure expansion")
    parser.add_argument("--curation", help="curation.jsonl, required for legacy conversion")
    parser.add_argument("--conversions-dir", help="Retained LibreOffice derivatives")
    parser.add_argument("--soffice", help="LibreOffice binary; enables DOC/RTF/WPD documents")
    parser.add_argument("--workbench", help="Reviewer decisions (workbench.sqlite)")
    arguments = parser.parse_args(argv)
    approval = Approval(arguments.reviewer, arguments.reason,
                        self_bind_unresolved_references=arguments.self_bind_unresolved_references)
    output = Path(arguments.output_dir)
    output.mkdir(parents=True, exist_ok=False)
    authority = Path(arguments.authority_dir)
    authority.mkdir(parents=True, exist_ok=True)
    ledger_path, journal_path = authority / "reviews.sqlite", authority / "policy.sqlite"
    if ledger_path.exists():
        parser.error("refusing to append a new bulk approval to an existing ledger; use a new authority dir")
    journal = SQLitePolicyJournal(journal_path, initialize=not journal_path.exists(), journal_id=arguments.journal_id)
    legacy = None
    if arguments.soffice:
        if not arguments.curation or not arguments.conversions_dir:
            parser.error("--soffice requires --curation and --conversions-dir")
        legacy = LegacyConverter(load_curation_rows(Path(arguments.curation)), Path(arguments.conversions_dir),
                                 arguments.soffice)
    decisions = Decisions()
    if arguments.workbench:
        from app.workbench.store import WorkbenchStore
        decisions = WorkbenchStore(Path(arguments.workbench)).decisions()
    staged, skipped = load_staged_sources(Path(arguments.staging), Path(arguments.raw_files),
                                          accept_metadata_mismatch=arguments.accept_metadata_mismatch, legacy=legacy)
    budget = QwenTokenBudget(Path(arguments.qwen_artifact))
    result = build_reviewed_bundle(staged, ledger=ReviewStore(ledger_path), budget=budget,
                                   approval=approval, decisions=decisions)
    report = write_outputs(output, result, qwen_artifact=Path(arguments.qwen_artifact), approval=approval,
                           accept_metadata_mismatch=arguments.accept_metadata_mismatch, skipped=skipped,
                           staged_count=len(staged) + len(skipped), journal_id=journal.snapshot().journal_id)
    print(json.dumps({key: report[key] for key in ("staged_documents", "eligible_sources", "units", "chunks",
                                                   "families", "relations", "legacy_converted", "bundle_fingerprint")}
                     | {"skipped": len(skipped), "rejected": len(result.rejected), "reserved": len(result.reserved),
                        "self_bound_references": result.self_bound}))
    return report


if __name__ == "__main__":
    main()

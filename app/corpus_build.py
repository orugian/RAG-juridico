"""Operator step: staged documents + declared human approval -> reviewed P4 bundle.

Staging status never approves anything. Approval comes only from an explicit
operator declaration (reviewer and reason are mandatory, no defaults) and is
recorded in the append-only ledger, scope by scope (source, parties, unit,
family), bound to the exact digests of what is being approved.

Each document is first rehearsed against a scratch ledger; only documents whose
complete governed proof builds are recorded in the real ledger. Nothing here
builds vectors, promotes a generation or calls a provider: the accepted P4 CLI
(``python -m app.retrieval.generations build/promote``) consumes the outputs.

No relations between instruments are declared: every instrument is its own
adjudicated family. Amendments therefore do not modify their base contracts in
answers until relations are reviewed explicitly.
"""
import argparse
from dataclasses import dataclass
import hashlib
from importlib.metadata import version
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import tempfile

from pydantic import ValidationError

from app.contracts import RelationResolution, SourceIdentity
from app.ingestion.evidence import QwenTokenBudget, build_evidence_chunks, prepare_citation_units
from app.ingestion.review_store import ReviewStore, review_digest, unit_review_digest
from app.ingestion.schemas import ParsedDocument

_STAGED_NAME = re.compile(r"^[a-f0-9]{64}\.json$")
_SUPPORTED_PARSERS = {"docx_parser", "pdf_parser"}
REGISTRY_VERSION = "v1"


@dataclass(frozen=True)
class Approval:
    """Explicit reviewer declaration; every relaxation is opt-in and reported.

    ``self_bind_unresolved_references``: references that the extractor cannot
    bind to a unique local label are recorded as reviewed mappings to their own
    unit. The literal reference stays in the cited text, but the referenced
    clause is not pulled into the closure automatically.
    """
    reviewer: str
    reason: str
    self_bind_unresolved_references: bool = False

    def __post_init__(self):
        if not self.reviewer.strip() or not self.reason.strip():
            raise ValueError("approval_requires_explicit_reviewer_and_reason")


@dataclass(frozen=True)
class StagedSource:
    document: ParsedDocument
    identity: SourceIdentity
    original_path: Path


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


def load_staged_sources(staging_dir: Path, raw_files_root: Path, *, accept_metadata_mismatch: bool = False):
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
        if not parsed.get("blocks"):
            skipped.append(key | {"reason": "no_parsed_text"})
            continue
        if parsed.get("parser_name") not in _SUPPORTED_PARSERS:
            skipped.append(key | {"reason": "parser_not_supported"})
            continue
        if parsed.get("status") == "review_metadata_mismatch":
            if not accept_metadata_mismatch:
                skipped.append(key | {"reason": "metadata_mismatch_not_adjudicated"})
                continue
            parsed = parsed | {"status": "success"}
        try:
            original = _original_path(raw_files_root, parsed["file_path"])
            if not original.is_file() or _sha256(original) != entry["file_hash"]:
                raise ValueError("staged_original_missing_or_changed")
            document = ParsedDocument.model_validate(parsed | {"file_path": str(original)})
            if document.file_hash != entry["file_hash"]:
                raise ValueError("staged_hash_mismatch")
            # Staged blocks predate the current verified parser identifiers; the
            # literal proof is always re-derived from the hash-checked original.
            document = document.model_copy(update={"blocks": _current_blocks(document, original)})
            identity = SourceIdentity(
                source_id=f"doc:{document.doc_id}:v{document.doc_version}:file:{document.file_id}",
                instrument_id=f"doc:{document.doc_id}",
                doc_version=document.doc_version, file_id=document.file_id,
                file_hash=entry["file_hash"], snapshot=entry["snapshot"],
                configuration_version=entry["configuration_version"],
                parser_name=document.parser_name, parser_version=document.parser_version)
        except (ValueError, KeyError, OSError) as error:
            skipped.append(key | {"reason": _failure(error)})
            continue
        if identity.source_id in seen:
            raise ValueError("staged_duplicate_source")
        seen.add(identity.source_id)
        sources.append(StagedSource(document, identity, original))
    return sources, skipped


def _record(ledger, identity, approval, **scope):
    return ledger.record(source_id=identity.source_id, snapshot=identity.snapshot,
                         file_hash=identity.file_hash, configuration_version=identity.configuration_version,
                         decision="approved", reviewer=approval.reviewer, reason=approval.reason, **scope)


def _self_bindings(units):
    return {reference.reference_id: [unit.unit_id] for unit in units
            for reference in unit.references if reference.state != "bound"}


def approve_source(staged: StagedSource, *, ledger, budget, approval: Approval):
    """Record source/parties/unit approvals for one document and build its proof.

    Returns (EvidenceBuildResult, number of self-bound references).
    """
    units = prepare_citation_units(staged.document, staged.identity, original_path=staged.original_path)
    bindings = _self_bindings(units) if approval.self_bind_unresolved_references else {}
    if bindings:
        units = prepare_citation_units(staged.document, staged.identity, original_path=staged.original_path,
                                       reference_bindings=bindings)
    identity = staged.identity
    _record(ledger, identity, approval)
    _record(ledger, identity, approval, scope="parties", subject_id=identity.source_id,
            subject_digest=review_digest({"contract_title": units[0].contract_title,
                                          "parties": [p.model_dump(mode="json") for p in units[0].parties]}))
    review_ids = {unit.unit_id: _record(ledger, identity, approval, scope="unit", subject_id=unit.unit_id,
                                        subject_digest=unit_review_digest(unit)).record_id for unit in units}
    built = build_evidence_chunks(staged.document, identity, ledger=ledger, budget=budget,
                                  original_path=staged.original_path, unit_review_ids=review_ids,
                                  reference_bindings=bindings or None)
    return built, len(bindings)


def rehearse(staged: StagedSource, *, budget, approval: Approval):
    """Build the full governed proof against a throwaway ledger; returns failure code or None."""
    with tempfile.TemporaryDirectory(prefix="rag-rehearsal-") as scratch:
        try:
            approve_source(staged, ledger=ReviewStore(Path(scratch) / "reviews.sqlite"),
                           budget=budget, approval=approval)
        except Exception as error:  # Any failure excludes the document; nothing is recorded.
            return _failure(error)
    return None


def build_reviewed_bundle(staged_sources, *, ledger, budget, approval: Approval):
    """Rehearse every document, then record approvals for the eligible ones only."""
    from app.retrieval.generation_contracts import GenerationBundle
    eligible, rejected = [], []
    for staged in staged_sources:
        code = rehearse(staged, budget=budget, approval=approval)
        if code is None:
            eligible.append(staged)
        else:
            rejected.append({"source_id": staged.identity.source_id,
                             "instrument_id": staged.identity.instrument_id, "reason": code})
    if not eligible:
        raise ValueError("no_document_passed_governed_rehearsal")
    built, self_bound = {}, 0
    for staged in eligible:
        built[staged.identity.source_id], count = approve_source(staged, ledger=ledger, budget=budget,
                                                                 approval=approval)
        self_bound += count
    registry = ledger.relation_state_digest()
    resolutions, family_sources = [], {}
    for staged in eligible:
        family_id = "fam:" + staged.identity.instrument_id.removeprefix("doc:")
        if family_id in family_sources:
            continue
        resolution = RelationResolution(family_id=family_id, snapshot=staged.identity.snapshot,
                                        registry_version=REGISTRY_VERSION, registry_digest=registry,
                                        relation_ids=[], state="resolved", review_record_id="pending-record")
        resolution.review_record_id = _record(
            ledger, staged.identity, approval, scope="family", subject_id=family_id,
            subject_digest=review_digest(resolution.model_dump(mode="json", exclude={"review_record_id"}))).record_id
        resolutions.append(resolution)
        family_sources[family_id] = staged.identity.source_id
    bundle = GenerationBundle(
        sources=[staged.identity for staged in eligible],
        units=[unit for result in built.values() for unit in result.units],
        chunks=[chunk for result in built.values() for chunk in result.chunks],
        reviews=[record for staged in eligible for record in ledger.history(staged.identity.source_id)],
        resolutions=resolutions, family_review_sources=family_sources)
    paths = {staged.identity.source_id: str(staged.original_path) for staged in eligible}
    return bundle, paths, rejected, self_bound


def qwen_identity(artifact: Path) -> dict:
    """Same identity the runtime encoder reports, computed without loading weights."""
    from app.embeddings import qwen
    from app.embeddings.artifacts import digest_file, verify_artifact
    return {"artifact_sha256": verify_artifact(Path(artifact)),
            "implementation_sha256": digest_file(Path(qwen.__file__)),
            "versions": {name: version(name) for name in ("torch", "transformers", "tokenizers", "safetensors")},
            "profile": qwen.QwenProfile().specification}


def main(argv=None):
    from app.retrieval.generation_contracts import GenerationConfig
    from app.retrieval.lexical import lexical_profile
    from app.retrieval.policy import SQLitePolicyJournal
    parser = argparse.ArgumentParser(description="Record declared approvals and write a reviewed P4 bundle")
    for name in ("staging", "raw-files", "qwen-artifact", "authority-dir", "output-dir", "journal-id",
                 "reviewer", "reason"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--accept-metadata-mismatch", action="store_true",
                        help="Reviewer adjudicated the staging client/parties discrepancy flags")
    parser.add_argument("--self-bind-unresolved-references", action="store_true",
                        help="Reviewer accepts unresolved references without closure expansion")
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
    staged, skipped = load_staged_sources(Path(arguments.staging), Path(arguments.raw_files),
                                          accept_metadata_mismatch=arguments.accept_metadata_mismatch)
    budget = QwenTokenBudget(Path(arguments.qwen_artifact))
    bundle, paths, rejected, self_bound = build_reviewed_bundle(staged, ledger=ReviewStore(ledger_path),
                                                                budget=budget, approval=approval)
    versions = {source.configuration_version for source in bundle.sources}
    if len(versions) != 1:
        raise ValueError("bundle_mixed_configuration_versions")
    configuration = GenerationConfig(configuration_version=versions.pop(),
                                     embedding_identity=qwen_identity(Path(arguments.qwen_artifact)),
                                     embedding_dimension=1024, lexical_identity=lexical_profile())
    (output / "bundle.json").write_text(bundle.model_dump_json(), encoding="utf-8")
    (output / "source_paths.json").write_text(json.dumps(paths, ensure_ascii=False, indent=1), encoding="utf-8")
    (output / "configuration.json").write_text(configuration.model_dump_json(), encoding="utf-8")
    report = {"schema": "reviewed-bundle-report-v1", "approval": {"reviewer": approval.reviewer, "reason": approval.reason,
              "accept_metadata_mismatch": arguments.accept_metadata_mismatch,
              "self_bind_unresolved_references": approval.self_bind_unresolved_references},
              "self_bound_references": self_bound,
              "staged_documents": len(staged) + len(skipped), "skipped": skipped, "rejected": rejected,
              "eligible_sources": len(bundle.sources), "units": len(bundle.units), "chunks": len(bundle.chunks),
              "families": len(bundle.resolutions), "relations": 0, "journal_id": journal.snapshot().journal_id,
              "bundle_fingerprint": bundle.fingerprint, "published": False}
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("staged_documents", "eligible_sources", "units", "chunks",
                                                   "families", "bundle_fingerprint")}
                     | {"skipped": len(skipped), "rejected": len(rejected), "self_bound_references": self_bound}))


if __name__ == "__main__":
    main()

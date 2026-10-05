"""Deterministic stratified reference sample selector (Golden Batch).

Selects a calibrated reference sample of documents covering all supported parse routes,
legacy and modern formats, risk extractions, and pathological edge cases (such as
mfiles_id 5991 and 5707) identified in the audit.
"""

from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

logger = logging.getLogger(__name__)

# Mandatory risk cases identified in the audit:
# - 5991: empty document / risk of null text
# - 5707: document with 149 insertions and 147 deletions (track changes)
# - 5829: document with revisions / track changes
DEFAULT_MANDATORY_RISK_IDS: Set[int] = {5991, 5707, 5829}


@dataclass
class _NormalizedRecord:
    record: Dict[str, Any]
    id: Optional[int]
    decision: str
    route: str
    subformat: str
    is_empty_risk: bool
    has_track_changes: bool
    has_comments: bool
    is_scanned: bool
    has_numbering: bool
    has_tables: bool
    is_mandatory: bool
    is_risk: bool
    stratum: str
    original_index: int


def _normalize_subformat(
    raw_format: Optional[str],
    declared_ext: Optional[str] = None,
    raw_route: Optional[str] = None,
) -> str:
    """Normalize format strings into canonical subformats."""
    fmt = (raw_format or declared_ext or "").lower().strip().lstrip(".")
    if fmt in ("doc", "ole_doc", "ole2"):
        return "ole_doc"
    if fmt in ("wpd", "wordperfect", "wp"):
        return "wordperfect"
    if fmt in ("docx", "dotx"):
        return "docx"
    if fmt in ("rtf",):
        return "rtf"
    if fmt in ("pdf",):
        return "pdf"
    if fmt in ("xlsx", "xls"):
        return "xlsx"
    if fmt in ("png", "jpg", "jpeg", "tif", "tiff", "image"):
        return "image"

    if not fmt and raw_route:
        r = raw_route.lower().strip()
        if r in ("docx", "pdf"):
            return r
        if r == "libreoffice":
            return "ole_doc"

    return fmt or "unknown"


def _normalize_route(raw_route: Optional[str], subformat: str) -> str:
    """Normalize parse route into canonical route."""
    route = (raw_route or "").lower().strip()
    if route:
        return route
    if subformat == "docx":
        return "docx"
    if subformat in ("ole_doc", "rtf", "wordperfect", "odt"):
        return "libreoffice"
    if subformat == "pdf":
        return "pdf"
    return "unsupported"


def _extract_record_meta(
    record: Dict[str, Any],
    original_index: int,
    mandatory_ids: Set[int],
) -> _NormalizedRecord:
    """Extract and normalize canonical metadata from flat or nested curation records."""
    file_dict = record.get("file") if isinstance(record.get("file"), dict) else {}
    flags_dict = record.get("flags") if isinstance(record.get("flags"), dict) else {}

    # Extract ID
    mfiles_id = record.get("mfiles_id")
    if mfiles_id is None:
        mfiles_id = record.get("id")
    if mfiles_id is None and "file_id" in file_dict:
        mfiles_id = file_dict.get("file_id")

    rec_id: Optional[int] = int(mfiles_id) if mfiles_id is not None else None

    # Extract Decision
    decision = str(record.get("decision") or "include").lower().strip()

    # Extract Subformat & Route
    raw_route = record.get("route") or record.get("parse_route") or file_dict.get("parse_route")
    raw_format = (
        record.get("subformat")
        or record.get("detected_format")
        or record.get("format")
        or file_dict.get("detected_format")
    )
    declared_ext = record.get("declared_extension") or file_dict.get("declared_extension")
    subformat = _normalize_subformat(raw_format, declared_ext, raw_route)
    route = _normalize_route(raw_route, subformat)
    if subformat == "unknown":
        if route in ("docx", "pdf"):
            subformat = route
        elif route == "libreoffice":
            subformat = "ole_doc"

    # Risk Flags
    is_empty_risk = bool(
        record.get("is_empty_risk")
        or flags_dict.get("is_empty_risk")
        or (rec_id == 5991)
    )
    has_track_changes = bool(
        record.get("has_track_changes")
        or flags_dict.get("has_track_changes")
        or (rec_id in (5707, 5829))
    )
    has_comments = bool(
        record.get("has_comments")
        or flags_dict.get("has_comments")
    )
    is_scanned = bool(
        record.get("is_scanned")
        or flags_dict.get("is_scanned")
    )
    has_numbering = bool(
        record.get("has_numbering")
        or flags_dict.get("has_numbering")
    )
    has_tables = bool(
        record.get("has_tables")
        or flags_dict.get("has_tables")
    )

    is_mandatory = rec_id is not None and rec_id in mandatory_ids
    is_risk = (
        is_mandatory
        or is_empty_risk
        or has_track_changes
        or has_comments
        or is_scanned
        or bool(file_dict.get("corrupt"))
        or bool(file_dict.get("extension_mismatch"))
        or bool(flags_dict.get("extension_mismatch"))
    )

    # Stratum classification
    if route == "docx":
        stratum = "docx:complex" if (has_numbering or has_tables) else "docx:standard"
    elif route == "libreoffice":
        stratum = f"libreoffice:{subformat}"
    elif route == "pdf":
        stratum = "pdf:scanned" if is_scanned else "pdf:digital"
    else:
        stratum = f"{route}:{subformat}"

    return _NormalizedRecord(
        record=record,
        id=rec_id,
        decision=decision,
        route=route,
        subformat=subformat,
        is_empty_risk=is_empty_risk,
        has_track_changes=has_track_changes,
        has_comments=has_comments,
        is_scanned=is_scanned,
        has_numbering=has_numbering,
        has_tables=has_tables,
        is_mandatory=is_mandatory,
        is_risk=is_risk,
        stratum=stratum,
        original_index=original_index,
    )


def _risk_priority_key(r: _NormalizedRecord) -> tuple:
    """Priority key for risk ordering. Lower values indicate higher priority."""
    score = 0
    if r.is_mandatory:
        score -= 100
    if r.is_empty_risk:
        score -= 30
    if r.has_track_changes:
        score -= 20
    if r.has_comments:
        score -= 10
    if r.is_scanned:
        score -= 5
    if r.has_numbering:
        score -= 2
    if r.has_tables:
        score -= 2
    return (score, r.id if r.id is not None else 0, r.original_index)


def select_reference_sample(
    records: List[Dict[str, Any]],
    target_count: int = 40,
    seed: int = 42,
    mandatory_ids: Optional[Set[int]] = None,
    allowed_decisions: Optional[Sequence[str]] = ("include",),
) -> List[Dict[str, Any]]:
    """Deterministically select a stratified reference sample (Golden Batch).

    Args:
        records: List of raw curation or file records.
        target_count: Desired number of documents in sample (default: 40).
        seed: Random seed for deterministic tie-breaking.
        mandatory_ids: Set of record IDs that MUST be included if present.
        allowed_decisions: Primary decisions considered eligible (default: ("include",)).

    Returns:
        List of selected records, stably sorted by record ID.
    """
    if target_count <= 0 or not records:
        return []

    active_mandatory_ids = set(mandatory_ids) if mandatory_ids is not None else set(DEFAULT_MANDATORY_RISK_IDS)

    # Normalize all input records
    normalized: List[_NormalizedRecord] = [
        _extract_record_meta(rec, idx, active_mandatory_ids)
        for idx, rec in enumerate(records)
    ]

    # Partition eligible candidates with fallback tiers
    primary_tier: List[_NormalizedRecord] = []
    review_tier: List[_NormalizedRecord] = []
    other_tier: List[_NormalizedRecord] = []

    allowed_set = {d.lower() for d in allowed_decisions} if allowed_decisions else {"include"}

    for r in normalized:
        # Mandatory records are always primary eligible regardless of decision flag
        if r.is_mandatory or r.decision in allowed_set:
            primary_tier.append(r)
        elif r.decision == "review":
            review_tier.append(r)
        else:
            other_tier.append(r)

    # Form the candidate pool with fallback if primary is insufficient
    pool: List[_NormalizedRecord] = list(primary_tier)
    if len(pool) < target_count:
        pool.extend(review_tier)
    if len(pool) < target_count:
        pool.extend(other_tier)

    # If pool is smaller than or equal to target_count, return all sorted deterministically
    if len(pool) <= target_count:
        pool.sort(key=lambda r: (r.id if r.id is not None else 0, r.original_index))
        return [copy.deepcopy(r.record) for r in pool]

    selected: List[_NormalizedRecord] = []
    selected_keys: Set[Any] = set()

    def add_candidate(item: _NormalizedRecord) -> bool:
        key = item.id if item.id is not None else item.original_index
        if key in selected_keys:
            return False
        selected.append(item)
        selected_keys.add(key)
        return True

    # -------------------------------------------------------------
    # Phase 1: Mandatory Risk Cases (e.g. 5991, 5707, 5829)
    # -------------------------------------------------------------
    mandatory_candidates = [r for r in pool if r.is_mandatory]
    mandatory_candidates.sort(key=lambda r: (r.id if r.id is not None else 0, r.original_index))

    for item in mandatory_candidates:
        if len(selected) < target_count:
            add_candidate(item)

    if len(selected) == target_count:
        selected.sort(key=lambda r: (r.id if r.id is not None else 0, r.original_index))
        return [copy.deepcopy(r.record) for r in selected]

    # -------------------------------------------------------------
    # Phase 2: Stratum & Route Representation
    # -------------------------------------------------------------
    # Group remaining unselected pool items by stratum and sort by priority
    strata_map: Dict[str, List[_NormalizedRecord]] = {}
    for r in pool:
        strata_map.setdefault(r.stratum, []).append(r)

    for stratum_name in strata_map:
        strata_map[stratum_name].sort(key=_risk_priority_key)

    represented_routes = {r.route for r in selected}
    all_routes = sorted({r.route for r in pool})

    # Step 2a: Ensure unrepresented routes get at least 1 record
    for route in all_routes:
        if route not in represented_routes and len(selected) < target_count:
            # Pick the best candidate across all strata of this route
            route_candidates: List[_NormalizedRecord] = []
            for stratum_name, items in strata_map.items():
                unsel = [it for it in items if (it.id if it.id is not None else it.original_index) not in selected_keys]
                if unsel and unsel[0].route == route:
                    route_candidates.append(unsel[0])

            if route_candidates:
                route_candidates.sort(key=_risk_priority_key)
                if add_candidate(route_candidates[0]):
                    represented_routes.add(route)

    # Step 2b: Ensure unrepresented strata get at least 1 record
    represented_strata = {r.stratum for r in selected}
    all_strata = sorted(strata_map.keys())

    for stratum in all_strata:
        if stratum not in represented_strata and len(selected) < target_count:
            unsel = [it for it in strata_map[stratum] if (it.id if it.id is not None else it.original_index) not in selected_keys]
            if unsel:
                if add_candidate(unsel[0]):
                    represented_strata.add(stratum)

    # -------------------------------------------------------------
    # Phase 3: Priority Risk Cases
    # -------------------------------------------------------------
    if len(selected) < target_count:
        unselected_risks = [
            r for r in pool
            if r.is_risk and (r.id if r.id is not None else r.original_index) not in selected_keys
        ]
        unselected_risks.sort(key=_risk_priority_key)
        for r in unselected_risks:
            if len(selected) < target_count:
                add_candidate(r)

    # -------------------------------------------------------------
    # Phase 4: Proportional Stratified Round-Robin for Remaining Slots
    # -------------------------------------------------------------
    if len(selected) < target_count:
        # Round robin over all available strata in deterministic order
        exhausted_strata = set()
        while len(selected) < target_count and len(exhausted_strata) < len(all_strata):
            for stratum in all_strata:
                if stratum in exhausted_strata:
                    continue
                unsel = [
                    it for it in strata_map[stratum]
                    if (it.id if it.id is not None else it.original_index) not in selected_keys
                ]
                if unsel:
                    add_candidate(unsel[0])
                    if len(selected) == target_count:
                        break
                else:
                    exhausted_strata.add(stratum)

    # Final deterministic sorting by ID and original index
    selected.sort(key=lambda r: (r.id if r.id is not None else 0, r.original_index))
    return [copy.deepcopy(r.record) for r in selected]


def get_sample_distribution(sample: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate statistical distribution of the reference sample for reporting."""
    by_route: Dict[str, int] = {}
    by_format: Dict[str, int] = {}
    by_decision: Dict[str, int] = {}

    empty_risk_count = 0
    track_changes_count = 0
    comments_count = 0
    scanned_count = 0
    mandatory_cases_found: List[int] = []
    total_with_risks = 0

    for idx, raw_record in enumerate(sample):
        meta = _extract_record_meta(raw_record, idx, DEFAULT_MANDATORY_RISK_IDS)

        by_route[meta.route] = by_route.get(meta.route, 0) + 1
        by_format[meta.subformat] = by_format.get(meta.subformat, 0) + 1
        by_decision[meta.decision] = by_decision.get(meta.decision, 0) + 1

        if meta.is_empty_risk:
            empty_risk_count += 1
        if meta.has_track_changes:
            track_changes_count += 1
        if meta.has_comments:
            comments_count += 1
        if meta.is_scanned:
            scanned_count += 1
        if meta.is_mandatory and meta.id is not None:
            if meta.id not in mandatory_cases_found:
                mandatory_cases_found.append(meta.id)
        if meta.is_risk:
            total_with_risks += 1

    by_risk = {
        "is_empty_risk": empty_risk_count,
        "has_track_changes": track_changes_count,
        "has_comments": comments_count,
        "is_scanned": scanned_count,
        "mandatory_cases": sorted(mandatory_cases_found),
        "total_with_risks": total_with_risks,
    }

    return {
        "total": len(sample),
        "by_route": by_route,
        "by_format": by_format,
        "by_decision": by_decision,
        "by_risk": by_risk,
        # Top-level aliases for convenience
        "routes": by_route,
        "formats": by_format,
        "decisions": by_decision,
        "risks": by_risk,
    }


def load_curated_records(curation_path: Path | str) -> List[Dict[str, Any]]:
    """Load curated records from a JSONL or JSON file."""
    path = Path(curation_path)
    if not path.exists():
        raise FileNotFoundError(f"Curation file not found: {path}")

    records: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
        if not content:
            return []
        if content.startswith("["):
            data = json.loads(content)
            if isinstance(data, list):
                return data
            return [data]
        for line in content.splitlines():
            line_str = line.strip()
            if line_str:
                records.append(json.loads(line_str))

    return records


def save_reference_sample(sample: List[Dict[str, Any]], output_path: Path | str) -> None:
    """Save reference sample records to a JSONL or JSON file."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.suffix.lower() == ".json":
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sample, f, ensure_ascii=False, indent=2)
    else:
        with open(path, "w", encoding="utf-8") as f:
            for record in sample:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

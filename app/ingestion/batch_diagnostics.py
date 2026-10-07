"""Local, advisory comparisons over all available parsed formats/titles.

Candidates never merge documents, infer legal precedence or grant eligibility.
Identifiers/texts are noisy parser output; absent text remains explicitly unchecked.
"""
from collections import Counter
from itertools import combinations

from app.ingestion.near_dup import shingles, jaccard


def diagnose_batch(files, *, threshold=0.90):
    if not 0 < threshold <= 1:
        raise ValueError("threshold must be within (0, 1]")
    available = []
    unchecked = []
    for item in sorted(files, key=lambda entry: entry["source_id"]):
        parsed = item.get("parsed_document") or {}
        blocks = parsed.get("blocks") or []
        text = "\n".join(block["text_raw"] for block in blocks)
        token_shingles = shingles(text)
        if not token_shingles:
            unchecked.append(item["source_id"])
            continue
        metadata = parsed["metadata"]
        parties = {party["clean_identifier"] for party in metadata.get("parties", []) if party.get("clean_identifier") and not party.get("is_law_firm")}
        available.append((item, metadata, token_shingles, parties, blocks))
    candidates = []
    compared = 0
    for left, right in combinations(available, 2):
        compared += 1
        a, b = left[2], right[2]
        if min(len(a), len(b)) / max(len(a), len(b)) < threshold:
            continue
        similarity = jaccard(a, b)
        if similarity >= threshold:
            candidates.append({"source_ids": [left[0]["source_id"], right[0]["source_id"]], "routes": [left[0]["parse_route"], right[0]["parse_route"]], "similarity": round(similarity, 6), "party_candidate_match": bool(left[3]) and left[3] == right[3], "state": "proposed", "reason": "text_similarity_requires_review"})
    relations = []
    for item, metadata, _, parties, blocks in available:
        if metadata.get("instrument_type") not in {"Aditivo", "Distrato"}:
            continue
        # Same candidate party can narrow a review queue, but proves no linkage.
        bases = [base[0]["source_id"] for base in available if base[0]["source_id"] != item["source_id"] and base[1].get("instrument_type") not in {"Aditivo", "Distrato"} and parties and parties & base[3]]
        support = [block["block_id"] for block in blocks if any(word in block["text_raw"].casefold() for word in ("aditivo", "distrato", "altera", "rescis", "substitu"))]
        relations.append({"modifier_source_id": item["source_id"], "candidate_base_source_ids": bases, "support_block_ids": support, "state": "proposed", "reason": "parser_type_and_party_candidates_only", "legal_effect_resolved": False})
    return {"schema_version": "batch-diagnostics-v1", "published": False, "near_duplicates": {"threshold": threshold, "available_sources": len(available), "compared_pairs": compared, "unchecked_source_ids": unchecked, "candidates": candidates}, "relations": relations, "risk_counts": dict(sorted(Counter(risk for item in files for risk in item["risks"]).items()))}

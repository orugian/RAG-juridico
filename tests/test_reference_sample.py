from pathlib import Path
import json
import pytest

from app.ingestion.reference_sample import (
    select_reference_sample,
    load_curated_records,
    save_reference_sample,
    get_sample_distribution,
)


def test_reference_sample_stratification():
    """Verify basic stratification and mandatory risk case inclusion from the brief."""
    records = [
        {"mfiles_id": 1, "decision": "include", "route": "docx", "has_numbering": True},
        {"mfiles_id": 2, "decision": "include", "route": "libreoffice", "subformat": "ole_doc"},
        {"mfiles_id": 3, "decision": "include", "route": "libreoffice", "subformat": "rtf"},
        {"mfiles_id": 4, "decision": "include", "route": "pdf", "is_scanned": False},
        {"mfiles_id": 5, "decision": "include", "route": "pdf", "is_scanned": True},
        {"mfiles_id": 5991, "decision": "include", "route": "docx", "is_empty_risk": True},
        {"mfiles_id": 5707, "decision": "include", "route": "docx", "has_track_changes": True},
    ]
    sample = select_reference_sample(records, target_count=5)
    sample_ids = {r["mfiles_id"] for r in sample}
    assert 5991 in sample_ids
    assert 5707 in sample_ids
    assert len(sample) == 5


def test_mandatory_risk_cases_with_reduced_target_count():
    """Verify mandatory cases 5991 and 5707 are strictly prioritized even with small target_count."""
    records = [
        {"mfiles_id": 10, "decision": "include", "route": "docx"},
        {"mfiles_id": 20, "decision": "include", "route": "libreoffice", "subformat": "ole_doc"},
        {"mfiles_id": 30, "decision": "include", "route": "pdf"},
        {"mfiles_id": 5991, "decision": "include", "route": "docx", "is_empty_risk": True},
        {"mfiles_id": 5707, "decision": "include", "route": "docx", "has_track_changes": True},
    ]

    # Target count = 2: exactly both mandatory cases
    sample_2 = select_reference_sample(records, target_count=2)
    ids_2 = {r["mfiles_id"] for r in sample_2}
    assert len(sample_2) == 2
    assert ids_2 == {5991, 5707}

    # Target count = 1: one mandatory case selected without exceeding target_count
    sample_1 = select_reference_sample(records, target_count=1)
    assert len(sample_1) == 1
    assert sample_1[0]["mfiles_id"] in {5991, 5707}


def test_format_and_route_coverage():
    """Verify that when target_count allows, all routes and formats are represented."""
    records = [
        # docx variants
        {"mfiles_id": 101, "decision": "include", "route": "docx"},
        {"mfiles_id": 102, "decision": "include", "route": "docx", "has_numbering": True},
        {"mfiles_id": 103, "decision": "include", "route": "docx", "has_tables": True},
        # libreoffice variants
        {"mfiles_id": 201, "decision": "include", "route": "libreoffice", "subformat": "ole_doc"},
        {"mfiles_id": 202, "decision": "include", "route": "libreoffice", "subformat": "rtf"},
        {"mfiles_id": 203, "decision": "include", "route": "libreoffice", "subformat": "wordperfect"},
        # pdf variants
        {"mfiles_id": 301, "decision": "include", "route": "pdf", "is_scanned": False},
        {"mfiles_id": 302, "decision": "include", "route": "pdf", "is_scanned": True},
        # mandatory risk documents
        {"mfiles_id": 5991, "decision": "include", "route": "docx", "is_empty_risk": True},
        {"mfiles_id": 5707, "decision": "include", "route": "docx", "has_track_changes": True},
    ]

    sample = select_reference_sample(records, target_count=8, seed=42)
    assert len(sample) == 8

    dist = get_sample_distribution(sample)
    # All routes represented
    assert "docx" in dist["by_route"] and dist["by_route"]["docx"] >= 1
    assert "libreoffice" in dist["by_route"] and dist["by_route"]["libreoffice"] >= 1
    assert "pdf" in dist["by_route"] and dist["by_route"]["pdf"] >= 1

    # Key formats represented
    sample_formats = set(dist["by_format"].keys())
    assert "docx" in sample_formats
    assert "ole_doc" in sample_formats
    assert "rtf" in sample_formats
    assert "wordperfect" in sample_formats
    assert "pdf" in sample_formats


def test_priority_risk_flags():
    """Verify records with explicit risk flags (is_empty_risk, has_track_changes, has_comments) are prioritized."""
    records = [
        {"mfiles_id": 1, "decision": "include", "route": "docx"},
        {"mfiles_id": 2, "decision": "include", "route": "docx"},
        {"mfiles_id": 3, "decision": "include", "route": "docx"},
        {"mfiles_id": 4, "decision": "include", "route": "docx", "has_comments": True},
        {"mfiles_id": 5, "decision": "include", "route": "docx", "has_track_changes": True},
        {"mfiles_id": 6, "decision": "include", "route": "docx", "is_empty_risk": True},
    ]

    # Target count = 3: should pick the 3 risk items before the 3 non-risk items
    sample = select_reference_sample(records, target_count=3, seed=42)
    sample_ids = {r["mfiles_id"] for r in sample}
    assert sample_ids == {4, 5, 6}


def test_absolute_determinism():
    """Verify deterministic and stable output across multiple runs with same seed."""
    records = [
        {"mfiles_id": i, "decision": "include", "route": "docx" if i % 2 == 0 else "pdf"}
        for i in range(1, 50)
    ]
    records.append({"mfiles_id": 5991, "decision": "include", "route": "docx", "is_empty_risk": True})
    records.append({"mfiles_id": 5707, "decision": "include", "route": "docx", "has_track_changes": True})

    run_1 = select_reference_sample(records, target_count=15, seed=123)
    run_2 = select_reference_sample(records, target_count=15, seed=123)

    assert [r["mfiles_id"] for r in run_1] == [r["mfiles_id"] for r in run_2]
    assert run_1 == run_2


def test_nested_curation_schema_compatibility():
    """Verify compatibility with the nested data structure used in curation.jsonl."""
    records = [
        {
            "mfiles_id": 2012,
            "decision": "include",
            "file": {
                "parse_route": "docx",
                "detected_format": "docx",
                "path": "files/2012/2012_v1.docx",
            },
            "flags": {"is_empty_risk": False, "has_track_changes": False},
        },
        {
            "mfiles_id": 2013,
            "decision": "include",
            "file": {
                "parse_route": "libreoffice",
                "detected_format": "ole_doc",
                "path": "files/2013/2013_v1.doc",
            },
            "flags": {},
        },
        {
            "mfiles_id": 5991,
            "decision": "include",
            "file": {
                "parse_route": "docx",
                "detected_format": "docx",
                "path": "files/5991/5987_v1.docx",
            },
            "flags": {"is_empty_risk": True},
        },
        {
            "mfiles_id": 5707,
            "decision": "include",
            "file": {
                "parse_route": "docx",
                "detected_format": "docx",
                "path": "files/5707/5704_v1.docx",
            },
            "flags": {"has_track_changes": True},
        },
    ]

    sample = select_reference_sample(records, target_count=3, seed=42)
    sample_ids = {r["mfiles_id"] for r in sample}

    assert 5991 in sample_ids
    assert 5707 in sample_ids
    assert len(sample) == 3


def test_real_curation_file_sample_selection(tmp_path: Path):
    """Verify loading real curation.jsonl and sampling target_count=40 with all format coverage."""
    curation_path = Path("data/curation/curation.jsonl")
    if not curation_path.exists():
        pytest.skip("data/curation/curation.jsonl not present")

    records = load_curated_records(curation_path)
    assert len(records) > 0

    sample = select_reference_sample(records, target_count=40, seed=42)
    assert len(sample) == 40

    sample_ids = {r["mfiles_id"] for r in sample}
    # Mandatory cases from audit
    assert 5991 in sample_ids
    assert 5707 in sample_ids
    assert 5829 in sample_ids

    dist = get_sample_distribution(sample)
    # Check routes
    assert dist["by_route"].get("docx", 0) > 0
    assert dist["by_route"].get("libreoffice", 0) > 0
    assert dist["by_route"].get("pdf", 0) > 0

    # Check that rare formats like wordperfect (only 1 in include) and rtf are captured
    assert dist["by_format"].get("wordperfect", 0) >= 1
    assert dist["by_format"].get("rtf", 0) >= 1
    assert dist["by_format"].get("ole_doc", 0) >= 1
    assert dist["by_format"].get("docx", 0) >= 1
    assert dist["by_format"].get("pdf", 0) >= 1


def test_get_sample_distribution():
    """Verify stats computation across routes, formats, and risks."""
    sample = [
        {"mfiles_id": 5991, "route": "docx", "subformat": "docx", "is_empty_risk": True, "decision": "include"},
        {"mfiles_id": 5707, "route": "docx", "subformat": "docx", "has_track_changes": True, "decision": "include"},
        {"mfiles_id": 100, "route": "libreoffice", "subformat": "ole_doc", "decision": "include"},
        {"mfiles_id": 200, "route": "pdf", "subformat": "pdf", "is_scanned": True, "decision": "review"},
    ]

    dist = get_sample_distribution(sample)
    assert dist["total"] == 4
    assert dist["by_route"]["docx"] == 2
    assert dist["by_route"]["libreoffice"] == 1
    assert dist["by_route"]["pdf"] == 1

    assert dist["by_format"]["docx"] == 2
    assert dist["by_format"]["ole_doc"] == 1
    assert dist["by_format"]["pdf"] == 1

    assert dist["by_risk"]["is_empty_risk"] == 1
    assert dist["by_risk"]["has_track_changes"] == 1
    assert dist["by_risk"]["is_scanned"] == 1
    assert 5991 in dist["by_risk"]["mandatory_cases"]
    assert 5707 in dist["by_risk"]["mandatory_cases"]

    assert dist["by_decision"]["include"] == 3
    assert dist["by_decision"]["review"] == 1


def test_save_and_load_roundtrip_jsonl(tmp_path: Path):
    """Verify roundtrip saving and loading as JSONL."""
    sample = [
        {"mfiles_id": 1, "route": "docx", "decision": "include"},
        {"mfiles_id": 2, "route": "pdf", "decision": "include"},
    ]
    out_file = tmp_path / "reference_sample.jsonl"
    save_reference_sample(sample, out_file)

    assert out_file.exists()
    loaded = load_curated_records(out_file)
    assert loaded == sample


def test_save_and_load_roundtrip_json(tmp_path: Path):
    """Verify roundtrip saving and loading as JSON array."""
    sample = [
        {"mfiles_id": 1, "route": "docx", "decision": "include"},
        {"mfiles_id": 2, "route": "pdf", "decision": "include"},
    ]
    out_file = tmp_path / "reference_sample.json"
    save_reference_sample(sample, out_file)

    assert out_file.exists()
    loaded = load_curated_records(out_file)
    assert loaded == sample


def test_edge_cases():
    """Verify robust handling of edge cases (empty list, target_count <= 0, fallback)."""
    assert select_reference_sample([], target_count=10) == []
    assert select_reference_sample([{"mfiles_id": 1}], target_count=0) == []
    assert select_reference_sample([{"mfiles_id": 1}], target_count=-5) == []

    # When target_count is larger than available records
    records = [
        {"mfiles_id": 1, "decision": "include", "route": "docx"},
        {"mfiles_id": 2, "decision": "include", "route": "pdf"},
    ]
    sample = select_reference_sample(records, target_count=10)
    assert len(sample) == 2

    # Fallback to review records when include is insufficient
    mixed_records = [
        {"mfiles_id": 1, "decision": "include", "route": "docx"},
        {"mfiles_id": 2, "decision": "review", "route": "pdf"},
        {"mfiles_id": 3, "decision": "exclude", "route": "docx"},
    ]
    sample_mixed = select_reference_sample(mixed_records, target_count=2)
    mixed_ids = {r["mfiles_id"] for r in sample_mixed}
    assert mixed_ids == {1, 2}

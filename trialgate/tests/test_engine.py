"""Acceptance tests (Milestones 1-2 from the requirements doc)."""
import json
from pathlib import Path

import pytest

import engine

DATA_DIR = Path(__file__).parent.parent / "data"
DIRTY_CSV = DATA_DIR / "clinical_records_dirty.csv"

CLEAN_PATIENT_IDS = {"P001", "P002", "P006", "P009", "P014", "P019", "P024", "P028"}


def test_validate_detects_all_29_known_errors_and_no_false_positives():
    result = engine.validate(str(DIRTY_CSV))

    assert result["total_rows"] == 31
    assert result["clean_rows"] == 8

    patient_ids_with_issues = {issue["patient_id"] for issue in result["issues"]}

    assert not (patient_ids_with_issues & CLEAN_PATIENT_IDS), (
        f"False positive on a clean patient: {patient_ids_with_issues & CLEAN_PATIENT_IDS}"
    )
    assert len(result["issues"]) == 29
    assert result["summary"]["error"] + result["summary"]["warning"] + result["summary"]["info"] == 29


def test_validate_specific_known_issues():
    result = engine.validate(str(DIRTY_CSV))
    issues_by_row = {}
    for issue in result["issues"]:
        issues_by_row.setdefault(issue["row"], []).append(issue)

    # P004 has an age outlier of 250 (matches the requirements doc example)
    row12_issues = issues_by_row[12]
    assert any(i["issue_type"] == "age_outlier" and i["patient_id"] == "P004" for i in row12_issues)

    # Duplicate patient ID (P003) has conflicting attributes, so it's an error
    dup_issues = [i for i in result["issues"] if i["issue_type"] == "duplicate_id_conflict"]
    assert len(dup_issues) == 1
    assert dup_issues[0]["severity"] == "error"


def test_file_not_found_returns_file_error_without_raising():
    with pytest.raises(engine.FileError):
        engine.validate("nonexistent.csv")


def test_clean_dataset_fixes_normalizations_and_changelog_matches(tmp_path):
    result = engine.clean(str(DIRTY_CSV), str(tmp_path))

    cleaned_csv = Path(result["cleaned_csv"])
    changelog_path = Path(result["changelog"])
    needs_review_path = Path(result["needs_review"])

    assert cleaned_csv.exists()
    assert changelog_path.exists()
    assert needs_review_path.exists()

    changelog = json.loads(changelog_path.read_text(encoding="utf-8"))
    needs_review = json.loads(needs_review_path.read_text(encoding="utf-8"))

    # changelog is 1:1 with all auto-fixable issues from validate_dataset
    validate_result = engine.validate(str(DIRTY_CSV))
    auto_fixable_count = sum(1 for i in validate_result["issues"] if i["auto_fixable"])
    not_fixable_count = sum(1 for i in validate_result["issues"] if not i["auto_fixable"])

    assert len(changelog) == auto_fixable_count
    assert len(needs_review) == not_fixable_count
    assert result["summary"]["fixed"] == len(changelog)
    assert result["summary"]["needs_review"] == len(needs_review)


def test_cleaned_csv_revalidation_only_shows_needs_review_issues(tmp_path):
    clean_result = engine.clean(str(DIRTY_CSV), str(tmp_path))
    cleaned_csv = clean_result["cleaned_csv"]

    revalidated = engine.validate(cleaned_csv)

    # Normalization issues (auto_fixable=True) should be gone
    assert all(not i["auto_fixable"] for i in revalidated["issues"])

    # The remaining issue count should match needs_review
    assert len(revalidated["issues"]) == clean_result["summary"]["needs_review"]

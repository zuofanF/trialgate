"""Phase 2 tests: raw record ingestion, clean profile building, and trial
eligibility matching for the two demo patients (DEMO-001 eligible,
DEMO-002 ineligible)."""
from pathlib import Path

import pytest

from patient_intake import clean_profile, parsers, trials

DATA_DIR = Path(__file__).parent.parent / "data" / "patients"
DEMO_001_DIR = DATA_DIR / "DEMO-001"
DEMO_002_DIR = DATA_DIR / "DEMO-002"


def test_load_records_reads_all_five_kinds_for_demo_001():
    records = parsers.load_records(str(DEMO_001_DIR))
    assert set(records.keys()) == {
        "medical_history", "prescriptions", "labs", "urine_test", "daily_log",
    }


def test_build_profile_demo_001_eligible_patient():
    records = parsers.load_records(str(DEMO_001_DIR))
    profile = clean_profile.build_profile(records)

    assert profile["patient_id"] == "DEMO-001"
    assert profile["age"] == 58
    assert profile["gender"] == "F"
    assert profile["pregnant"] is False
    assert profile["diagnoses"][0]["icd10"] == "E11.9"

    assert len(profile["current_medications"]) == 1
    assert profile["current_medications"][0]["drug_name"] == "Metformin"

    # Latest lab values must win over older rows in the same file
    assert profile["latest_labs"]["hba1c_pct"] == 8.1
    assert profile["latest_labs"]["hba1c_date"] == "2026-06-01"
    assert profile["latest_labs"]["egfr"] == 68
    assert profile["latest_labs"]["creatinine_mg_dl"] == 0.9

    assert profile["latest_urine_test"]["date"] == "2026-05-20"
    assert profile["latest_urine_test"]["protein"] == "negative"

    assert profile["glucose_log_summary"]["severe_hypo_events"] == 0
    assert profile["glucose_log_summary"]["readings_count"] == 21


def test_build_profile_demo_002_ineligible_patient():
    records = parsers.load_records(str(DEMO_002_DIR))
    profile = clean_profile.build_profile(records)

    assert profile["patient_id"] == "DEMO-002"
    assert len(profile["current_medications"]) == 2
    drug_names = {m["drug_name"] for m in profile["current_medications"]}
    assert drug_names == {"Metformin", "Insulin glargine"}

    # Latest eGFR (38, from 2026-06-10) must win over the older, better one (45, from 2026-01-15)
    assert profile["latest_labs"]["egfr"] == 38
    assert profile["latest_labs"]["egfr_date"] == "2026-06-10"
    assert profile["latest_labs"]["hba1c_pct"] == 9.5

    assert profile["glucose_log_summary"]["severe_hypo_events"] == 0


def test_load_records_missing_directory_raises_records_error():
    with pytest.raises(parsers.RecordsError):
        parsers.load_records(str(DATA_DIR / "NONEXISTENT"))


def test_json_and_csv_sources_produce_identical_profile(tmp_path):
    """DEMO-001 ships labs/urine_test as CSV. Rebuild an equivalent
    directory with those same two record kinds as JSON instead, and
    confirm build_profile produces an identical result either way --
    proving the pipeline is genuinely format-agnostic, not just
    CSV-shaped rules ported over from phase 1."""
    import json
    import shutil

    json_dir = tmp_path / "DEMO-001-json"
    json_dir.mkdir()
    for name in ("medical_history.json", "prescriptions.json", "daily_log.json"):
        shutil.copy(DEMO_001_DIR / name, json_dir / name)

    csv_records = parsers.load_records(str(DEMO_001_DIR))
    (json_dir / "labs.json").write_text(json.dumps(csv_records["labs"]), encoding="utf-8")
    (json_dir / "urine_test.json").write_text(json.dumps(csv_records["urine_test"]), encoding="utf-8")

    json_records = parsers.load_records(str(json_dir))
    csv_profile = clean_profile.build_profile(csv_records)
    json_profile = clean_profile.build_profile(json_records)

    assert csv_profile == json_profile


def test_demo_001_is_eligible_for_glycontrol_x():
    records = parsers.load_records(str(DEMO_001_DIR))
    profile = clean_profile.build_profile(records)
    result = trials.evaluate_eligibility(profile, "glycontrol_x")

    assert result["eligible"] is True
    assert all(c["passed"] for c in result["criteria"])
    assert len(result["criteria"]) == 8


def test_demo_002_is_ineligible_for_glycontrol_x_with_exactly_two_failures():
    records = parsers.load_records(str(DEMO_002_DIR))
    profile = clean_profile.build_profile(records)
    result = trials.evaluate_eligibility(profile, "glycontrol_x")

    assert result["eligible"] is False
    failed_ids = {c["id"] for c in result["criteria"] if not c["passed"]}
    assert failed_ids == {"metformin_monotherapy", "egfr_min"}


def test_evaluate_eligibility_unknown_trial_raises():
    with pytest.raises(trials.TrialNotFoundError):
        trials.evaluate_eligibility({"patient_id": "DEMO-001"}, "nonexistent_trial")

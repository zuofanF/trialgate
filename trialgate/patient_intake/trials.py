"""Deterministic trial-eligibility checks, mirroring the style of
rules/detect.py: explicit functions rather than a generic rule DSL,
since there is exactly one trial today and a generic engine would be
premature abstraction. Add more trials/functions here as they come up.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional


class TrialNotFoundError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


# ---------------------------------------------------------------------------
# GlyControl-X criteria
# ---------------------------------------------------------------------------

def check_age_range(profile: dict) -> tuple:
    age = profile.get("age")
    if age is None:
        return False, "Age is missing from the patient profile"
    passed = 18 <= age <= 75
    return passed, f"Age is {age}" + ("" if passed else " (outside the required 18-75 range)")


def check_t2dm_diagnosis(profile: dict) -> tuple:
    diagnoses = profile.get("diagnoses", [])
    if any(d.get("icd10") == "E11.9" for d in diagnoses):
        return True, "Confirmed Type 2 Diabetes Mellitus diagnosis (E11.9) found"
    return False, "No Type 2 Diabetes Mellitus diagnosis (E11.9) found in medical history"


def check_metformin_monotherapy(profile: dict, today: Optional[date] = None) -> tuple:
    today = today or date.today()
    meds = profile.get("current_medications", [])
    on_insulin = any("insulin" in (m.get("drug_name") or "").lower() for m in meds)
    if on_insulin:
        return False, "Patient is currently on insulin, which disqualifies them from the metformin-monotherapy requirement"

    metformin = next((m for m in meds if m.get("drug_name") == "Metformin"), None)
    if metformin is None:
        return False, "Patient is not currently on Metformin"

    days_on_drug = (today - _parse_date(metformin["start_date"])).days
    if days_on_drug < 90:
        return False, f"Patient has only been on Metformin for {days_on_drug} days (requires at least 90)"
    return True, f"On stable Metformin monotherapy for {days_on_drug} days, not on insulin"


def check_hba1c_range(profile: dict) -> tuple:
    hba1c = profile.get("latest_labs", {}).get("hba1c_pct")
    if hba1c is None:
        return False, "No HbA1c result found"
    passed = 7.0 <= hba1c <= 10.0
    return passed, f"Latest HbA1c is {hba1c}%" + ("" if passed else " (outside the required 7.0-10.0% range)")


def check_egfr_min(profile: dict) -> tuple:
    egfr = profile.get("latest_labs", {}).get("egfr")
    if egfr is None:
        return False, "No eGFR result found"
    passed = egfr >= 45
    return passed, f"Latest eGFR is {egfr}" + ("" if passed else " (below the required minimum of 45)")


def check_urine_protein_ok(profile: dict) -> tuple:
    protein = profile.get("latest_urine_test", {}).get("protein")
    if protein is None:
        return False, "No urine protein result found"
    passed = protein.lower() in ("negative", "trace")
    return passed, f"Latest urine protein result is '{protein}'" + ("" if passed else " (proteinuria detected)")


def check_no_severe_hypo(profile: dict) -> tuple:
    severe = profile.get("glucose_log_summary", {}).get("severe_hypo_events", 0)
    passed = severe == 0
    detail = (
        "No severe hypoglycemia events in the glucose log" if passed
        else f"{severe} severe hypoglycemia event(s) (glucose < 54 mg/dL) found in the glucose log"
    )
    return passed, detail


def check_not_pregnant(profile: dict) -> tuple:
    pregnant = profile.get("pregnant", False)
    return (not pregnant), ("Patient is pregnant" if pregnant else "Not pregnant")


GLYCONTROL_X_CRITERIA = [
    {"id": "age_range", "description": "Age 18-75", "check": check_age_range},
    {"id": "t2dm_diagnosis", "description": "Confirmed Type 2 Diabetes Mellitus diagnosis", "check": check_t2dm_diagnosis},
    {"id": "metformin_monotherapy", "description": "On stable Metformin monotherapy for >= 90 days, not on insulin", "check": check_metformin_monotherapy},
    {"id": "hba1c_range", "description": "HbA1c between 7.0% and 10.0%", "check": check_hba1c_range},
    {"id": "egfr_min", "description": "eGFR >= 45 mL/min/1.73m2", "check": check_egfr_min},
    {"id": "urine_protein_ok", "description": "Urine protein negative or trace (no proteinuria)", "check": check_urine_protein_ok},
    {"id": "no_severe_hypo", "description": "No severe hypoglycemia events in the glucose log", "check": check_no_severe_hypo},
    {"id": "not_pregnant", "description": "Not pregnant", "check": check_not_pregnant},
]

TRIALS = {
    "glycontrol_x": {
        "trial_id": "glycontrol_x",
        "title": (
            "GlyControl-X: A Phase 3 Study of Investigational Agent GLX-40 in "
            "Adults with Type 2 Diabetes Inadequately Controlled on Metformin Monotherapy"
        ),
        "criteria": GLYCONTROL_X_CRITERIA,
    },
}


def evaluate_eligibility(profile: dict, trial_id: str) -> dict:
    """Run every criterion for `trial_id` against a clean patient profile
    and return a structured, per-criterion eligibility verdict."""
    trial = TRIALS.get(trial_id)
    if trial is None:
        raise TrialNotFoundError(f"Unknown trial_id '{trial_id}'. Known trials: {', '.join(TRIALS)}")

    results = []
    for criterion in trial["criteria"]:
        passed, detail = criterion["check"](profile)
        results.append({
            "id": criterion["id"],
            "description": criterion["description"],
            "passed": passed,
            "detail": detail,
        })

    eligible = all(r["passed"] for r in results)
    failed_ids = [r["id"] for r in results if not r["passed"]]
    patient_id = profile.get("patient_id", "unknown")

    if eligible:
        summary = f"Patient {patient_id} is ELIGIBLE for {trial['title']}. All {len(results)} criteria are met."
    else:
        summary = (
            f"Patient {patient_id} is INELIGIBLE for {trial['title']}. "
            f"Failed {len(failed_ids)} of {len(results)} criteria: {', '.join(failed_ids)}."
        )

    return {
        "patient_id": patient_id,
        "trial_id": trial_id,
        "trial_title": trial["title"],
        "eligible": eligible,
        "criteria": results,
        "summary": summary,
    }

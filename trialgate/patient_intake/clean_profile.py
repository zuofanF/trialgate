"""Builds a normalized PatientProfile (the "clean data" for trial matching)
from the raw records dict produced by parsers.load_records.

Deterministic only -- no LLM calls. Picks the most recent value per lab
test / urine test by date, and summarizes the daily glucose log.
"""
from __future__ import annotations

SEVERE_HYPO_THRESHOLD_MG_DL = 54

# Maps a raw lab test_name to the (value_key, date_key) pair used in the
# clean profile's "latest_labs" dict.
_LAB_KEY_MAP = {
    "HbA1c": ("hba1c_pct", "hba1c_date"),
    "eGFR": ("egfr", "egfr_date"),
    "Creatinine": ("creatinine_mg_dl", "creatinine_date"),
}


def _latest_labs(labs: list) -> dict:
    latest_row_by_test: dict = {}
    for row in labs:
        name = row["test_name"]
        if name not in latest_row_by_test or row["date"] > latest_row_by_test[name]["date"]:
            latest_row_by_test[name] = row

    result: dict = {}
    for test_name, row in latest_row_by_test.items():
        value_key, date_key = _LAB_KEY_MAP.get(test_name, (test_name.lower(), f"{test_name.lower()}_date"))
        result[value_key] = row["value"]
        result[date_key] = row["date"]
    return result


def _latest_by_date(rows: list) -> dict:
    if not rows:
        return {}
    return max(rows, key=lambda r: r["date"])


def _summarize_glucose_log(entries: list) -> dict:
    if not entries:
        return {"severe_hypo_events": 0, "avg_glucose_mg_dl": None, "readings_count": 0}
    values = [entry["glucose_mg_dl"] for entry in entries]
    severe_hypo_events = sum(1 for v in values if v < SEVERE_HYPO_THRESHOLD_MG_DL)
    avg_glucose = round(sum(values) / len(values), 1)
    return {
        "severe_hypo_events": severe_hypo_events,
        "avg_glucose_mg_dl": avg_glucose,
        "readings_count": len(entries),
    }


def build_profile(records: dict) -> dict:
    medical_history = records.get("medical_history", {}) or {}
    prescriptions = records.get("prescriptions", []) or []
    labs = records.get("labs", []) or []
    urine_tests = records.get("urine_test", []) or []
    daily_log = records.get("daily_log", []) or []

    return {
        "patient_id": medical_history.get("patient_id"),
        "age": medical_history.get("age"),
        "gender": medical_history.get("gender"),
        "pregnant": medical_history.get("pregnant", False),
        "diagnoses": medical_history.get("diagnoses", []),
        "current_medications": [m for m in prescriptions if m.get("status") == "active"],
        "latest_labs": _latest_labs(labs),
        "latest_urine_test": _latest_by_date(urine_tests),
        "glucose_log_summary": _summarize_glucose_log(daily_log),
    }

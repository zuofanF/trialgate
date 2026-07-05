"""Normalization layer for a patient's raw records, applied before
clean_profile.build_profile. Reuses rules/normalize.py's schema-agnostic
functions (dates, gender codes, drug names, number formatting) rather
than duplicating that logic -- phase 1 already solved "how do you clean
one messy string field," and that doesn't change just because the field
now lives in a JSON/CSV record instead of a CSV column.

Same "no silent edits" principle as phase 1: every change is recorded in
a changelog list, {"record_kind", "field", "before", "after", "rule", "message"}.
"""
from __future__ import annotations

from rules import normalize

_CATEGORICAL_URINE_FIELDS = ("protein", "glucose", "ketones", "blood")


def _log(changelog: list, record_kind: str, field: str, before, result) -> None:
    changelog.append({
        "record_kind": record_kind,
        "field": field,
        "before": before,
        "after": result.new_value,
        "rule": result.rule,
        "message": result.message,
    })


def _to_number(value):
    if isinstance(value, (int, float)):
        return value
    text = str(value)
    try:
        return float(text) if "." in text else int(text)
    except (TypeError, ValueError):
        return value


def _clean_date(value, record_kind: str, field: str, changelog: list):
    if value is None:
        return value
    result = normalize.normalize_date(value)
    if result.changed:
        _log(changelog, record_kind, field, value, result)
    return result.new_value


def _clean_number(value, record_kind: str, field: str, changelog: list):
    if value is None:
        return value
    result = normalize.normalize_number_format(value)
    if result.changed:
        _log(changelog, record_kind, field, value, result)
    return _to_number(result.new_value)


def _clean_categorical(value, record_kind: str, field: str, changelog: list):
    if value is None:
        return value
    original = str(value)
    cleaned = original.strip().lower()
    if cleaned != original:
        changelog.append({
            "record_kind": record_kind,
            "field": field,
            "before": original,
            "after": cleaned,
            "rule": "categorical_case",
            "message": f"Normalized '{original}' to '{cleaned}'",
        })
    return cleaned


def _normalize_medical_history(medical_history: dict, changelog: list) -> dict:
    result = dict(medical_history)
    if "age" in result:
        result["age"] = _clean_number(result["age"], "medical_history", "age", changelog)
    if "gender" in result:
        gender_result = normalize.normalize_gender(result["gender"])
        if gender_result.changed:
            _log(changelog, "medical_history", "gender", result["gender"], gender_result)
        result["gender"] = gender_result.new_value
    diagnoses = []
    for i, diagnosis in enumerate(result.get("diagnoses", [])):
        d = dict(diagnosis)
        if "onset_date" in d:
            d["onset_date"] = _clean_date(d["onset_date"], "medical_history", f"diagnoses[{i}].onset_date", changelog)
        diagnoses.append(d)
    result["diagnoses"] = diagnoses
    return result


def _normalize_prescriptions(prescriptions: list, changelog: list) -> list:
    normalized = []
    for i, entry in enumerate(prescriptions):
        e = dict(entry)
        if e.get("drug_name") is not None:
            drug_result = normalize.normalize_drug_name(e["drug_name"])
            if drug_result.changed:
                _log(changelog, "prescriptions", f"[{i}].drug_name", e["drug_name"], drug_result)
            e["drug_name"] = drug_result.new_value
        if e.get("start_date") is not None:
            e["start_date"] = _clean_date(e["start_date"], "prescriptions", f"[{i}].start_date", changelog)
        if e.get("end_date") is not None:
            e["end_date"] = _clean_date(e["end_date"], "prescriptions", f"[{i}].end_date", changelog)
        normalized.append(e)
    return normalized


def _normalize_labs(labs: list, changelog: list) -> list:
    normalized = []
    for i, row in enumerate(labs):
        r = dict(row)
        if r.get("date") is not None:
            r["date"] = _clean_date(r["date"], "labs", f"[{i}].date", changelog)
        if r.get("value") is not None:
            r["value"] = _clean_number(r["value"], "labs", f"[{i}].value", changelog)
        normalized.append(r)
    return normalized


def _normalize_urine_test(urine_test: list, changelog: list) -> list:
    normalized = []
    for i, row in enumerate(urine_test):
        r = dict(row)
        if r.get("date") is not None:
            r["date"] = _clean_date(r["date"], "urine_test", f"[{i}].date", changelog)
        for field in _CATEGORICAL_URINE_FIELDS:
            if r.get(field) is not None:
                r[field] = _clean_categorical(r[field], "urine_test", f"[{i}].{field}", changelog)
        normalized.append(r)
    return normalized


def _normalize_daily_log(daily_log: list, changelog: list) -> list:
    normalized = []
    for i, entry in enumerate(daily_log):
        e = dict(entry)
        if e.get("date") is not None:
            e["date"] = _clean_date(e["date"], "daily_log", f"[{i}].date", changelog)
        if e.get("glucose_mg_dl") is not None:
            e["glucose_mg_dl"] = _clean_number(e["glucose_mg_dl"], "daily_log", f"[{i}].glucose_mg_dl", changelog)
        normalized.append(e)
    return normalized


def normalize_records(records: dict) -> tuple:
    """Normalize every record kind present in `records`. Returns
    (normalized_records, changelog). Missing record kinds are left absent;
    unrecognized fields are passed through untouched."""
    changelog: list = []
    normalized: dict = {}

    if "medical_history" in records:
        normalized["medical_history"] = _normalize_medical_history(records["medical_history"], changelog)
    if "prescriptions" in records:
        normalized["prescriptions"] = _normalize_prescriptions(records["prescriptions"], changelog)
    if "labs" in records:
        normalized["labs"] = _normalize_labs(records["labs"], changelog)
    if "urine_test" in records:
        normalized["urine_test"] = _normalize_urine_test(records["urine_test"], changelog)
    if "daily_log" in records:
        normalized["daily_log"] = _normalize_daily_log(records["daily_log"], changelog)

    return normalized, changelog

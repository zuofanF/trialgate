"""Detect-only rules (section 4.2 of the requirements doc). Auto-fixing is
forbidden here -- these functions only report problems, never change
values. Every issue found here is always auto_fixable=False and goes to
needs_review. No LLM calls; only deterministic numeric/date/string checks.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional


ISO_FMT = "%Y-%m-%d"

TABLET_DRUGS = {"Aspirin", "Metformin", "Acetaminophen"}
REMARK_KEYWORDS = ["duplicate", "void", "invalid", "discontinued", "cancelled"]


def _to_float(value) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_iso_date(value) -> Optional[date]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, ISO_FMT).date()
    except ValueError:
        return None


def check_invalid_date(value) -> Optional[dict]:
    """Detect a date that looks ISO-shaped but doesn't exist on the calendar,
    or doesn't match any recognized format."""
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    if _parse_iso_date(text) is not None:
        return None
    return {
        "issue_type": "invalid_date",
        "severity": "error",
        "message": f"Date '{text}' does not exist or could not be parsed",
        "auto_fixable": False,
    }


def check_future_date(value, today: date) -> Optional[dict]:
    d = _parse_iso_date(value)
    if d is None:
        return None
    if d > today:
        return {
            "issue_type": "future_date",
            "severity": "warning",
            "message": f"Visit date '{value}' is after today ({today.isoformat()})",
            "auto_fixable": False,
        }
    return None


def check_age_outlier(value) -> Optional[dict]:
    n = _to_float(value)
    if n is None:
        return None
    if n < 0 or n > 120:
        return {
            "issue_type": "age_outlier",
            "severity": "error",
            "message": f"Age {value} is not physiologically plausible",
            "auto_fixable": False,
        }
    return None


def check_systolic_outlier(value) -> Optional[dict]:
    n = _to_float(value)
    if n is None:
        return None
    if n < 50 or n > 250:
        return {
            "issue_type": "systolic_outlier",
            "severity": "error",
            "message": f"Systolic BP {value} is not physiologically plausible",
            "auto_fixable": False,
        }
    return None


def check_weight_outlier(value) -> Optional[dict]:
    n = _to_float(value)
    if n is None:
        return None
    if n < 20 or n > 300:
        return {
            "issue_type": "weight_outlier",
            "severity": "error",
            "message": f"Weight {value}kg is implausible for an adult trial",
            "auto_fixable": False,
        }
    return None


def check_diastolic_gt_systolic(systolic, diastolic) -> Optional[dict]:
    sys_n = _to_float(systolic)
    dia_n = _to_float(diastolic)
    if sys_n is None or dia_n is None:
        return None
    if dia_n > sys_n:
        return {
            "issue_type": "diastolic_gt_systolic",
            "severity": "error",
            "message": f"Diastolic BP ({diastolic}) exceeds systolic BP ({systolic}). Possibly transposed",
            "auto_fixable": False,
        }
    return None


def check_dose_invalid(value) -> Optional[dict]:
    n = _to_float(value)
    if n is None:
        return None
    if n <= 0:
        return {
            "issue_type": "dose_invalid",
            "severity": "error",
            "message": f"Dose {value} must be a positive value",
            "auto_fixable": False,
        }
    return None


def check_death_before_visit(death_date, visit_date) -> Optional[dict]:
    d = _parse_iso_date(death_date)
    v = _parse_iso_date(visit_date)
    if d is None or v is None:
        return None
    if d < v:
        return {
            "issue_type": "death_before_visit",
            "severity": "error",
            "message": f"Death date ({death_date}) is before the visit date ({visit_date})",
            "auto_fixable": False,
        }
    return None


def check_ae_before_visit(ae_date, visit_date) -> Optional[dict]:
    a = _parse_iso_date(ae_date)
    v = _parse_iso_date(visit_date)
    if a is None or v is None:
        return None
    if a < v:
        return {
            "issue_type": "ae_before_visit",
            "severity": "error",
            "message": f"Adverse event onset date ({ae_date}) is before the visit date ({visit_date})",
            "auto_fixable": False,
        }
    return None


def check_text_in_numeric(value) -> Optional[dict]:
    """Detect text entered in a numeric field (value is kept as-is, no
    outlier check is performed on it)."""
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    if _to_float(text) is not None:
        return None
    return {
        "issue_type": "text_in_numeric",
        "severity": "warning",
        "message": f"Text '{text}' found in a numeric field. Possibly a placeholder like 'N/A' or 'unable to measure'",
        "auto_fixable": False,
    }


def check_unit_validity(drug_name, unit) -> Optional[dict]:
    """Detect an implausible unit for the drug (e.g. mL for a tablet drug)."""
    if not drug_name or not unit:
        return None
    if str(drug_name).strip() in TABLET_DRUGS and str(unit).strip().lower() == "ml":
        return {
            "issue_type": "unit_validity",
            "severity": "warning",
            "message": f"Unit '{unit}' looks wrong for tablet drug '{drug_name}'",
            "auto_fixable": False,
        }
    return None


def check_remark_keywords(remark) -> Optional[dict]:
    """Detect operational notes in the remarks field (e.g. 'duplicate', 'void')."""
    text = "" if remark is None else str(remark).strip()
    if not text:
        return None
    lowered = text.lower()
    for keyword in REMARK_KEYWORDS:
        if keyword in lowered:
            return {
                "issue_type": "remark_keyword",
                "severity": "info",
                "message": f"Remarks field contains operational keyword '{keyword}': {text}",
                "auto_fixable": False,
            }
    return None


def check_missing_required(value) -> Optional[dict]:
    text = "" if value is None else str(value).strip()
    if text:
        return None
    return {
        "issue_type": "missing_required",
        "severity": "error",
        "message": "Required field is empty",
        "auto_fixable": False,
    }


def check_duplicate_patient_ids(effective_patient_ids: list, compare_attrs: list) -> dict:
    """Detect duplicate patient IDs.

    Args:
        effective_patient_ids: normalized patient ID per row, in row order
            (index 0 == row 1).
        compare_attrs: a comparison tuple (e.g. gender) per row, same order.

    Returns:
        {row_index (0-based): issue_dict}. For a duplicate group with
        conflicting attributes, only the last-occurring row gets an issue
        (so the same group isn't counted twice).
    """
    groups: dict = {}
    for idx, pid in enumerate(effective_patient_ids):
        if not pid:
            continue
        groups.setdefault(pid, []).append(idx)

    results = {}
    for pid, indices in groups.items():
        if len(indices) < 2:
            continue
        attrs = [compare_attrs[i] for i in indices]
        conflict = any(a != attrs[0] for a in attrs)
        last_idx = indices[-1]
        if conflict:
            results[last_idx] = {
                "issue_type": "duplicate_id_conflict",
                "severity": "error",
                "message": f"Patient ID '{pid}' is duplicated with conflicting attributes (e.g. gender)",
                "auto_fixable": False,
            }
        else:
            results[last_idx] = {
                "issue_type": "duplicate_id",
                "severity": "warning",
                "message": f"Patient ID '{pid}' is duplicated (attributes match)",
                "auto_fixable": False,
            }
    return results

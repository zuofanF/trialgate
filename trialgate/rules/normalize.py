"""Auto-fixable normalization rules (section 4.1 of the requirements doc).

Each function performs a normalization that does not change the *meaning*
of a value. Every change returns
(new_value, changed, rule, message, severity) so the caller (engine.py)
can record it in the changelog. No LLM calls are used anywhere here --
everything is deterministic string/number processing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class NormalizeResult:
    new_value: object
    changed: bool
    rule: str = ""
    message: str = ""
    severity: str = "info"


def _unchanged(value):
    return NormalizeResult(new_value=value, changed=False)


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SLASH_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_MONTH_NAME_RE = re.compile(r"^([A-Za-z]+)\s+(\d{1,2}),?\s*(\d{4})$")

MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}


def _is_valid_date(year: int, month: int, day: int) -> bool:
    try:
        date(year, month, day)
        return True
    except ValueError:
        return False


def normalize_date(raw: str) -> NormalizeResult:
    """Unify date strings to ISO 8601 (YYYY-MM-DD).

    Handles:
      - Already ISO 8601 -> unchanged (validity is checked in detect.py)
      - US format (MM/DD/YYYY) and European format (DD/MM/YYYY), both with a
        4-digit year last. When one component is > 12 the format is
        unambiguous; when both are <= 12 the US convention (MM/DD) is
        assumed as the default for US-based trial data.
      - Spelled-out month names (e.g. "June 4, 2026")
      - Anything else (or an invalid calendar date) is left unchanged --
        that is left to detect.py's invalid-date check.
    """
    if raw is None:
        return _unchanged(raw)
    value = str(raw).strip()
    if not value:
        return _unchanged(raw)

    if _ISO_RE.match(value):
        return _unchanged(raw)

    m = _SLASH_DATE_RE.match(value)
    if m:
        first, second, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if first > 12:
            day, month, fmt = first, second, "European (DD/MM/YYYY)"
        elif second > 12:
            month, day, fmt = first, second, "US (MM/DD/YYYY)"
        else:
            month, day, fmt = first, second, "US (MM/DD/YYYY, assumed)"
        if _is_valid_date(year, month, day):
            iso = f"{year:04d}-{month:02d}-{day:02d}"
            return NormalizeResult(
                new_value=iso,
                changed=True,
                rule="date_format",
                message=f"Converted {fmt} date '{value}' to ISO 8601 '{iso}'",
            )

    m = _MONTH_NAME_RE.match(value)
    if m:
        month_name, day, year = m.group(1).lower(), int(m.group(2)), int(m.group(3))
        month = MONTH_NAMES.get(month_name)
        if month and _is_valid_date(year, month, day):
            iso = f"{year:04d}-{month:02d}-{day:02d}"
            return NormalizeResult(
                new_value=iso,
                changed=True,
                rule="date_format",
                message=f"Converted spelled-out date '{value}' to ISO 8601 '{iso}'",
            )

    return _unchanged(raw)


# ---------------------------------------------------------------------------
# Numeric formatting (stray whitespace / thousands-separator commas)
# ---------------------------------------------------------------------------

_THOUSANDS_RE = re.compile(r"^\d{1,3}(,\d{3})+(\.\d+)?$")


def normalize_number_format(raw: str) -> NormalizeResult:
    """Clean up stray whitespace and thousands-separator commas in a numeric
    field (e.g. a copy-pasted spreadsheet value like ' 45 ' or '1,000').
    """
    if raw is None:
        return _unchanged(raw)
    original = str(raw)
    stripped = original.strip()
    cleaned = stripped.replace(",", "") if _THOUSANDS_RE.match(stripped) else stripped

    if cleaned == original:
        return _unchanged(raw)
    try:
        float(cleaned)
    except ValueError:
        return _unchanged(raw)

    return NormalizeResult(
        new_value=cleaned,
        changed=True,
        rule="number_format",
        message=f"Cleaned up numeric formatting: '{original}' -> '{cleaned}'",
    )


# ---------------------------------------------------------------------------
# Drug name canonicalization
# ---------------------------------------------------------------------------

DRUG_NAME_MAP = {
    "asa": "Aspirin",
    "aspirin": "Aspirin",
    "tylenol": "Acetaminophen",
    "acetaminophen": "Acetaminophen",
    "paracetamol": "Acetaminophen",
    "metformin": "Metformin",
    "glucophage": "Metformin",
}


def normalize_drug_name(raw: str) -> NormalizeResult:
    """Canonicalize drug name variants: brand names and abbreviations are
    mapped to the generic name, and leading/trailing whitespace is removed.
    """
    if raw is None:
        return _unchanged(raw)
    original = str(raw)
    stripped = original.strip()
    canonical = DRUG_NAME_MAP.get(stripped.lower(), stripped)

    if canonical != original:
        return NormalizeResult(
            new_value=canonical,
            changed=True,
            rule="drug_name_variant",
            message=f"Unified drug name '{original}' to '{canonical}'",
        )
    return _unchanged(raw)


# ---------------------------------------------------------------------------
# Gender code unification
# ---------------------------------------------------------------------------

GENDER_MAP = {
    "m": "M",
    "1": "M",
    "male": "M",
    "f": "F",
    "2": "F",
    "female": "F",
}


def normalize_gender(raw: str) -> NormalizeResult:
    """Unify gender codes to 'M'/'F'."""
    if raw is None:
        return _unchanged(raw)
    value = str(raw).strip()
    if value in ("M", "F"):
        return _unchanged(raw)
    canonical = GENDER_MAP.get(value.lower())
    if canonical:
        return NormalizeResult(
            new_value=canonical,
            changed=True,
            rule="gender_code",
            message=f"Unified gender code '{value}' to '{canonical}'",
        )
    return _unchanged(raw)


# ---------------------------------------------------------------------------
# Patient ID format
# ---------------------------------------------------------------------------

_PATIENT_ID_CANONICAL_RE = re.compile(r"^P\d{3}$")


def normalize_patient_id(raw: str) -> NormalizeResult:
    """Unify patient IDs to 'P' + 3 digits (e.g. 'P-011' -> 'P011', '12' -> 'P012')."""
    if raw is None:
        return _unchanged(raw)
    value = str(raw).strip()
    if _PATIENT_ID_CANONICAL_RE.match(value):
        return _unchanged(raw)

    digits = re.sub(r"\D", "", value)
    if digits and len(digits) <= 3:
        canonical = f"P{int(digits):03d}"
        return NormalizeResult(
            new_value=canonical,
            changed=True,
            rule="patient_id_format",
            message=f"Unified patient ID '{value}' to '{canonical}'",
        )
    return _unchanged(raw)


# ---------------------------------------------------------------------------
# Unit conversion (g -> mg) / dose-unit split / temperature unit
# ---------------------------------------------------------------------------

_DOSE_WITH_UNIT_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(mg|g|mL|ml)$", re.IGNORECASE)


def normalize_unit_conversion(dose_raw, unit_raw) -> Optional[NormalizeResult]:
    """Convert dose to mg when the unit is 'g'. Returns new_value as a
    (dose, unit) tuple."""
    if unit_raw is None:
        return None
    unit = str(unit_raw).strip()
    if unit.lower() != "g":
        return None
    try:
        dose_value = float(str(dose_raw).strip())
    except (TypeError, ValueError):
        return None
    new_dose = dose_value * 1000
    new_dose_str = str(int(new_dose)) if new_dose == int(new_dose) else str(new_dose)
    return NormalizeResult(
        new_value=(new_dose_str, "mg"),
        changed=True,
        rule="unit_conversion",
        message=f"Converted dose '{dose_raw} g' to '{new_dose_str} mg'",
    )


def normalize_dose_unit_split(dose_raw, unit_raw) -> Optional[NormalizeResult]:
    """Split a dose field that has a unit mixed in (e.g. '500mg') into
    separate dose and unit values."""
    if dose_raw is None:
        return None
    dose_str = str(dose_raw).strip()
    m = _DOSE_WITH_UNIT_RE.match(dose_str)
    if not m:
        return None
    number, unit = m.group(1), m.group(2)
    # If the unit column is already filled with something else, don't guess --
    # leave it for detect.py to flag.
    existing_unit = str(unit_raw).strip() if unit_raw not in (None, "") else ""
    if existing_unit and existing_unit.lower() != unit.lower():
        return None
    return NormalizeResult(
        new_value=(number, unit.lower() if unit.lower() != "ml" else "mL"),
        changed=True,
        rule="dose_unit_mixed",
        message=f"Split dose field '{dose_str}' into dose '{number}' + unit '{unit}'",
    )


def normalize_temperature(raw) -> Optional[NormalizeResult]:
    """The canonical temperature unit is Fahrenheit (US convention). A value
    in the 34.0-42.0 range looks like it was recorded in Celsius by mistake,
    so convert it to Fahrenheit (flagged as a warning, since it's worth a
    second look even though the conversion is mechanical).
    """
    if raw is None:
        return None
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if not (34.0 <= value <= 42.0):
        return None
    fahrenheit = round(value * 9 / 5 + 32, 1)
    return NormalizeResult(
        new_value=str(fahrenheit),
        changed=True,
        rule="temperature_unit",
        message=f"Temperature '{raw}' looks like Celsius; converted to Fahrenheit '{fahrenheit}'",
        severity="warning",
    )

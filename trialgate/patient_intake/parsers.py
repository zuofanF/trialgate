"""Loads a patient's raw record files into plain Python data structures.

Each raw file is classified by its extension and dispatched to the
matching parser in `_PARSERS`. Every parser returns the same shape
regardless of source format (a dict for medical_history, a list of
row-dicts for everything else), so clean_profile.py never needs to know
whether a given record kind came from JSON or CSV.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

RECORD_KINDS = ["medical_history", "prescriptions", "labs", "urine_test", "daily_log"]


class RecordsError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _parse_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _coerce_value(value: str):
    """CSV cells are always strings; recover int/float where possible so
    downstream comparisons (e.g. eGFR >= 45) work the same as with JSON."""
    text = value.strip()
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def _parse_csv(path: Path):
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return [{k: _coerce_value(v) for k, v in row.items()} for row in reader]


# extension -> parser function.
_PARSERS = {
    ".json": _parse_json,
    ".csv": _parse_csv,
}


def load_records(records_dir: str) -> dict:
    """Read every raw record file in a patient's directory, classify each
    by its file extension, and parse it into a records dict keyed by
    record kind (medical_history, prescriptions, labs, urine_test,
    daily_log).
    """
    path = Path(records_dir)
    if not path.exists() or not path.is_dir():
        raise RecordsError(f"Patient records directory not found: {records_dir}")

    records: dict = {}
    for kind in RECORD_KINDS:
        matches = sorted(p for p in path.glob(f"{kind}.*") if p.suffix.lower() in _PARSERS)
        if not matches:
            continue
        file_path = matches[0]
        parser = _PARSERS[file_path.suffix.lower()]
        try:
            records[kind] = parser(file_path)
        except Exception as exc:  # noqa: BLE001 - surfaced as a RecordsError, never raised raw
            raise RecordsError(f"Failed to parse {file_path.name}: {exc}") from exc

    return records

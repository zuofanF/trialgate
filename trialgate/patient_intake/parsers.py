"""Loads a patient's raw record files into plain Python data structures.

Each raw file is classified by its extension and dispatched to the
matching parser in `_PARSERS`. Every parser returns the same shape
regardless of source format (a dict for medical_history, a list of
row-dicts for everything else), so clean_profile.py never needs to know
whether a given record kind came from JSON or CSV.

Two entry points are provided:
  - `load_records(records_dir)` -- reads files from disk (Claude Code,
    or any client where the MCP server can reach the filesystem).
  - `load_records_from_content(files)` -- takes file content already in
    hand, e.g. a file the user pasted/attached directly in a Claude
    Desktop conversation, which the MCP subprocess can't read off disk.
Both funnel through the same text-based parsing primitives so a given
record kind behaves identically regardless of how it arrived.
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

RECORD_KINDS = ["medical_history", "prescriptions", "labs", "urine_test", "daily_log"]


class RecordsError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _parse_json_text(text: str):
    return json.loads(text)


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


def _parse_csv_text(text: str):
    reader = csv.DictReader(io.StringIO(text))
    return [{k: _coerce_value(v) for k, v in row.items()} for row in reader]


def _parse_json(path: Path):
    return _parse_json_text(path.read_text(encoding="utf-8"))


def _parse_csv(path: Path):
    return _parse_csv_text(path.read_text(encoding="utf-8"))


# extension -> path-based parser function.
_PARSERS = {
    ".json": _parse_json,
    ".csv": _parse_csv,
}

# extension -> text-based parser function.
_TEXT_PARSERS = {
    ".json": _parse_json_text,
    ".csv": _parse_csv_text,
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


def load_records_from_content(files: dict) -> dict:
    """Same result shape as load_records, but from file content already in
    hand instead of a directory on disk.

    Args:
        files: {"medical_history.json": "<file text>", "labs.csv": "<file text>", ...}
            -- filename-with-extension keys, exactly matching the name and
            text of a file the user pasted or attached in the conversation.
    """
    records: dict = {}
    for filename, content in files.items():
        stem = Path(filename).stem
        ext = Path(filename).suffix.lower()
        if stem not in RECORD_KINDS:
            continue
        parser = _TEXT_PARSERS.get(ext)
        if parser is None:
            raise RecordsError(f"Unsupported file extension for '{filename}': {ext}")
        try:
            records[stem] = parser(content)
        except Exception as exc:  # noqa: BLE001 - surfaced as a RecordsError, never raised raw
            raise RecordsError(f"Failed to parse {filename}: {exc}") from exc

    return records

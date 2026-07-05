"""Orchestrates the full patient-intake flow for server.py: ingest raw
records (from disk or inline content) -> normalize -> build the clean
profile. Kept separate from clean_profile.py so build_profile() itself
stays a pure, directly-testable function that assumes already-normalized
input.
"""
from __future__ import annotations

from typing import Optional

from patient_intake import clean_profile, normalize_records, parsers


def ingest_and_clean(records_dir: Optional[str] = None, records: Optional[dict] = None) -> tuple:
    """Load a patient's raw records (by directory path or inline file
    content), normalize them, and build the clean profile.

    Args:
        records_dir: path to a directory of raw record files on disk
        records: {"medical_history.json": "<content>", ...} -- file
            content already in hand, e.g. from a Claude Desktop attachment

    Returns:
        (profile, changelog) -- changelog is the list of normalizations
        applied (empty if the input was already clean).
    """
    if records is not None:
        raw = parsers.load_records_from_content(records)
    elif records_dir is not None:
        raw = parsers.load_records(records_dir)
    else:
        raise parsers.RecordsError("Provide either records_dir or records")

    normalized, changelog = normalize_records.normalize_records(raw)
    profile = clean_profile.build_profile(normalized)
    return profile, changelog

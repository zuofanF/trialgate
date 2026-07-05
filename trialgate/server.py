"""TrialGate MCP entry point.

Thin layer only: the actual logic lives in engine.py / report.py. This
file just exposes the 3 @mcp.tool() functions as JSON strings.
print() is never used (it would corrupt the stdio transport) -- logging
goes to stderr instead.
"""
from __future__ import annotations

import json

from fastmcp import FastMCP

import engine
import report
from engine import FileError
from patient_intake import clean_profile, parsers, trials
from patient_intake.parsers import RecordsError
from patient_intake.trials import TrialNotFoundError

mcp = FastMCP(name="trialgate")


def _error_json(issue_type: str, message: str) -> str:
    return json.dumps(
        {
            "issues": [
                {
                    "issue_type": issue_type,
                    "severity": "error",
                    "message": message,
                    "auto_fixable": False,
                }
            ]
        },
        ensure_ascii=False,
        indent=2,
    )


def _file_error_json(exc: FileError) -> str:
    return _error_json("file_error", exc.message)


@mcp.tool()
def validate_dataset(csv_path: str) -> str:
    """Detect every problem in a clinical trial CSV without changing any data.

    When to use:
      Call this first whenever a user asks to "check this data," "find
      problems in this CSV," or similar. Always call this before
      clean_dataset so you know what's actually wrong before fixing anything.

    Args:
      csv_path: Absolute path to the CSV file to validate. Columns are
        expected to be: patient_id, age, gender, visit_date, drug_name,
        dose, unit, systolic_bp, diastolic_bp, weight_kg, temperature,
        adverse_event, ae_onset_date, death_date, remarks.

    Returns (JSON string, parseable with json.loads):
      {
        "total_rows": int,       # total number of data rows
        "clean_rows": int,       # rows with zero issues
        "issues": [
          {
            "row": int,              # 1-indexed row number
            "patient_id": str,       # normalized patient ID
            "column": str,           # column with the problem
            "value": str,            # the offending (original) value
            "issue_type": str,       # e.g. "age_outlier", "date_format"
            "severity": "error"|"warning"|"info",
            "message": str,          # human-readable explanation
            "auto_fixable": bool     # true if clean_dataset can fix it
          }, ...
        ],
        "summary": {"error": int, "warning": int, "info": int}
      }

    When to use:
      - Before submitting/advancing data to the next step, as a pre-check
      - After running clean_dataset, to confirm nothing was missed
      - If the file doesn't exist or is corrupt, this never raises --
        it returns JSON with issue_type="file_error" instead

    Note:
      This tool never modifies the file. Use clean_dataset to fix issues.
    """
    try:
        result = engine.validate(csv_path)
    except FileError as exc:
        return _file_error_json(exc)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def clean_dataset(csv_path: str, output_dir: str) -> str:
    """Fix every auto-fixable problem and write out the cleaned data plus a
    full record of what changed.

    When to use:
      Call this when a user asks to "get this CSV ready to submit," "clean
      this data," or similar. Only "information-preserving" normalizations
      (date formats, drug name variants, number formatting, gender codes,
      etc.) are fixed automatically. Anything that requires a judgment call
      about the *meaning* of a value (outliers, date logic contradictions,
      missing required fields, etc.) is never auto-fixed -- it always goes
      to needs_review.json instead (no silent edits, ever).

    Args:
      csv_path: Absolute path to the CSV file to clean
      output_dir: Absolute path to the output directory (created if it
        doesn't exist)

    Output files (all inside output_dir):
      1. cleaned.csv       — the data after auto-fixes are applied
      2. changelog.json    — a record of every change that was made
                              [{"row", "patient_id", "column", "before",
                                "after", "rule", "message"}, ...]
                              Every auto-fixed value is guaranteed to
                              appear here -- even one missing entry would
                              be considered a bug
      3. needs_review.json — items that could not be auto-fixed and need
                              human judgment (same shape as validate_dataset's
                              issues, always with auto_fixable=false)

    Returns (JSON string):
      {
        "cleaned_csv": str, "changelog": str, "needs_review": str,  # absolute paths
        "summary": {"fixed": int, "needs_review": int}
      }

    When to use:
      - After validate_dataset has shown what's wrong, to actually fix it
      - Afterward, it's good practice to call validate_dataset again on
        cleaned.csv to confirm the auto-fixable issues are gone
      - If the file doesn't exist or is corrupt, this never raises --
        it returns JSON with issue_type="file_error" instead
    """
    try:
        result = engine.clean(csv_path, output_dir)
    except FileError as exc:
        return _file_error_json(exc)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def quality_report(csv_path: str) -> str:
    """Generate a plain-language quality summary report (Markdown) for
    non-technical readers.

    When to use:
      Call this when you need to explain "the state of this data" to a
      data manager or their manager -- someone who won't parse raw column
      names or error codes. Show/quote this Markdown instead of the raw
      validate_dataset JSON.

    Args:
      csv_path: Absolute path to the CSV file

    Returns:
      A Markdown string (safe to paste directly into chat or a document).
      Includes total record count, clean-record count, a breakdown by
      severity, a breakdown by category (auto-fixable vs. needs review)
      with representative examples, and recommended next steps.

    When to use:
      - When you need to report the situation to a user in plain language
      - Before/after clean_dataset, to explain the change to a non-technical audience
    """
    try:
        return report.generate_report(csv_path)
    except FileError as exc:
        return f"# Data Quality Report\n\nError: {exc.message}"


@mcp.tool()
def build_patient_profile(records_dir: str) -> str:
    """Ingest a patient's raw records (from any mix of supported file
    formats) and return a single clean, structured patient profile.

    When to use:
      Call this to see a normalized view of one patient's data pulled
      together from multiple source documents. It's also useful as a
      debugging step before calling check_trial_eligibility, to sanity
      check what values were actually extracted.

    Args:
      records_dir: Absolute path to a directory containing one patient's
        raw record files. Each file must be named `<kind>.<ext>` where
        kind is one of: medical_history, prescriptions, labs, urine_test,
        daily_log. Supported extensions: .json, .csv (classified
        automatically by extension; medical_history/prescriptions/
        daily_log are typically JSON, labs/urine_test are typically CSV,
        but any supported kind can be either format).

    Returns (JSON string):
      {
        "patient_id": str, "age": int, "gender": str, "pregnant": bool,
        "diagnoses": [{"condition", "icd10", "onset_date"}, ...],
        "current_medications": [{"drug_name", "dose", "frequency", "start_date", "status"}, ...],
        "latest_labs": {"hba1c_pct", "hba1c_date", "egfr", "egfr_date", "creatinine_mg_dl", "creatinine_date"},
        "latest_urine_test": {"date", "protein", "glucose", "ketones", "blood"},
        "glucose_log_summary": {"severe_hypo_events", "avg_glucose_mg_dl", "readings_count"}
      }
      Only the most recent result (by date) is kept for labs and urine
      tests, even if the source file has multiple historical rows.

    When to use:
      - Whenever an agent needs a single normalized snapshot of a patient
        before reasoning about them
      - If the directory or a required file is missing/corrupt, this never
        raises -- it returns JSON with issue_type="file_error" instead
    """
    try:
        records = parsers.load_records(records_dir)
        profile = clean_profile.build_profile(records)
    except RecordsError as exc:
        return _error_json("file_error", exc.message)
    return json.dumps(profile, ensure_ascii=False, indent=2)


@mcp.tool()
def check_trial_eligibility(records_dir: str, trial_id: str) -> str:
    """Determine whether a specific patient is eligible for a specific
    clinical trial, from their raw records straight through to a verdict.

    When to use:
      This is the main phase-2 demo tool: call it when a user asks
      "is this patient eligible for <trial>?" or presses an equivalent
      "check eligibility" action for one patient. It runs the full
      pipeline (ingest raw records -> build clean profile -> evaluate
      every eligibility criterion) in one call.

    Args:
      records_dir: Absolute path to the patient's raw records directory
        (same format as build_patient_profile's records_dir)
      trial_id: Which trial to check against. Currently available:
        "glycontrol_x" (a Type 2 Diabetes trial for adults inadequately
        controlled on Metformin monotherapy).

    Returns (JSON string):
      {
        "patient_id": str, "trial_id": str, "trial_title": str,
        "eligible": bool,
        "criteria": [
          {"id": str, "description": str, "passed": bool, "detail": str}, ...
        ],
        "summary": str   # one-line plain-language verdict, safe to quote directly
      }
      Every criterion is always included (not just the failing ones), so
      the full picture -- what passed and what didn't -- is visible at a
      glance.

    When to use:
      - To answer "does this patient match this trial?" for one named patient
      - If the trial_id or records_dir/files are invalid, this never
        raises -- it returns JSON with issue_type="file_error" or
        issue_type="trial_not_found" instead
    """
    try:
        records = parsers.load_records(records_dir)
        profile = clean_profile.build_profile(records)
        result = trials.evaluate_eligibility(profile, trial_id)
    except RecordsError as exc:
        return _error_json("file_error", exc.message)
    except TrialNotFoundError as exc:
        return _error_json("trial_not_found", exc.message)
    return json.dumps(result, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run()

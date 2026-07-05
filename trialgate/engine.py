"""Rule pipeline: loads a CSV and applies normalization (4.1) and
detection (4.2) rules in sequence to build issues / changelog /
needs_review.

Deterministic processing only (no LLM calls). Both validate_dataset and
clean_dataset are built on top of `build_row_results`, so Milestone 2's
property -- "re-validating the cleaned file makes normalization issues
disappear, leaving only needs_review issues" -- falls out naturally.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

from rules import detect, normalize

COLUMNS = [
    "patient_id",
    "age",
    "gender",
    "visit_date",
    "drug_name",
    "dose",
    "unit",
    "systolic_bp",
    "diastolic_bp",
    "weight_kg",
    "temperature",
    "adverse_event",
    "ae_onset_date",
    "death_date",
    "remarks",
]

REQUIRED_COLUMNS = ["age", "gender", "unit", "diastolic_bp"]
NUMERIC_COLUMNS = ["age", "dose", "systolic_bp", "diastolic_bp", "weight_kg", "temperature"]


def log(message: str) -> None:
    """print is banned for debugging (it pollutes the stdio transport).
    Log to stderr instead."""
    print(message, file=sys.stderr)


@dataclass
class RowResult:
    raw: dict
    effective: dict
    changes: list = field(default_factory=list)  # list[(column, NormalizeResult)]


class FileError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def load_dataset(csv_path: str) -> pd.DataFrame:
    path = Path(csv_path)
    if not path.exists():
        raise FileError(f"File not found: {csv_path}")
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    except Exception as exc:  # noqa: BLE001 - a broken CSV becomes a file_error
        raise FileError(f"Failed to read CSV: {exc}") from exc

    missing_cols = [c for c in COLUMNS if c not in df.columns]
    if missing_cols:
        raise FileError(f"Missing required columns: {', '.join(missing_cols)}")
    return df


def _apply_normalization(raw: dict) -> RowResult:
    effective = dict(raw)
    changes: list = []

    r = normalize.normalize_patient_id(raw["patient_id"])
    if r.changed:
        changes.append(("patient_id", r))
        effective["patient_id"] = r.new_value

    r = normalize.normalize_number_format(raw["age"])
    if r.changed:
        changes.append(("age", r))
        effective["age"] = r.new_value

    r = normalize.normalize_gender(raw["gender"])
    if r.changed:
        changes.append(("gender", r))
        effective["gender"] = r.new_value

    r = normalize.normalize_date(raw["visit_date"])
    if r.changed:
        changes.append(("visit_date", r))
        effective["visit_date"] = r.new_value

    r = normalize.normalize_drug_name(raw["drug_name"])
    if r.changed:
        changes.append(("drug_name", r))
        effective["drug_name"] = r.new_value

    # dose/unit: prefer splitting an embedded unit, then g->mg conversion
    dose_raw, unit_raw = raw["dose"], raw["unit"]
    split_result = normalize.normalize_dose_unit_split(dose_raw, unit_raw)
    if split_result is not None and split_result.changed:
        changes.append(("dose", split_result))
        effective["dose"], effective["unit"] = split_result.new_value
    else:
        conv_result = normalize.normalize_unit_conversion(dose_raw, unit_raw)
        if conv_result is not None and conv_result.changed:
            changes.append(("dose", conv_result))
            effective["dose"], effective["unit"] = conv_result.new_value

    r = normalize.normalize_temperature(raw["temperature"])
    if r is not None and r.changed:
        changes.append(("temperature", r))
        effective["temperature"] = r.new_value

    return RowResult(raw=raw, effective=effective, changes=changes)


def build_row_results(df: pd.DataFrame) -> list:
    return [_apply_normalization(df.iloc[i].to_dict()) for i in range(len(df))]


def _issue(row_no: int, patient_id: str, column: str, value, issue_type: str,
           severity: str, message: str, auto_fixable: bool) -> dict:
    return {
        "row": row_no,
        "patient_id": patient_id,
        "column": column,
        "value": value,
        "issue_type": issue_type,
        "severity": severity,
        "message": message,
        "auto_fixable": auto_fixable,
    }


def collect_issues(row_results: list, today: Optional[date] = None) -> list:
    """Merge normalization issues (auto_fixable=True) and detection issues
    (auto_fixable=False) into a single list."""
    today = today or date.today()
    issues: list = []

    effective_patient_ids = [rr.effective["patient_id"] for rr in row_results]
    compare_attrs = [
        (rr.effective.get("gender", ""),) for rr in row_results
    ]
    dup_issues = detect.check_duplicate_patient_ids(effective_patient_ids, compare_attrs)

    for i, rr in enumerate(row_results):
        row_no = i + 1
        pid = rr.effective["patient_id"]
        eff = rr.effective

        for column, result in rr.changes:
            raw_value = rr.raw[column]
            if column == "dose" and isinstance(result.new_value, tuple):
                raw_display = f"{raw_value} / unit:{rr.raw.get('unit', '')}"
            else:
                raw_display = raw_value
            issues.append(_issue(
                row_no, pid, column, raw_display, result.rule,
                result.severity, result.message, True,
            ))

        checks = [
            ("age", detect.check_age_outlier(eff["age"])),
            ("systolic_bp", detect.check_systolic_outlier(eff["systolic_bp"])),
            ("weight_kg", detect.check_weight_outlier(eff["weight_kg"])),
            ("diastolic_bp", detect.check_diastolic_gt_systolic(eff["systolic_bp"], eff["diastolic_bp"])),
            ("dose", detect.check_dose_invalid(eff["dose"])),
            ("death_date", detect.check_death_before_visit(eff["death_date"], eff["visit_date"])),
            ("ae_onset_date", detect.check_ae_before_visit(eff["ae_onset_date"], eff["visit_date"])),
            ("visit_date", detect.check_invalid_date(eff["visit_date"])),
            ("ae_onset_date", detect.check_invalid_date(eff["ae_onset_date"])),
            ("death_date", detect.check_invalid_date(eff["death_date"])),
            ("visit_date", detect.check_future_date(eff["visit_date"], today)),
            ("unit", detect.check_unit_validity(eff["drug_name"], eff["unit"])),
            ("remarks", detect.check_remark_keywords(eff["remarks"])),
        ]
        for column, result in checks:
            if result:
                issues.append(_issue(
                    row_no, pid, column, eff.get(column, ""), result["issue_type"],
                    result["severity"], result["message"], result["auto_fixable"],
                ))

        for column in NUMERIC_COLUMNS:
            text_issue = detect.check_text_in_numeric(eff[column])
            if text_issue:
                issues.append(_issue(
                    row_no, pid, column, eff[column], text_issue["issue_type"],
                    text_issue["severity"], text_issue["message"], text_issue["auto_fixable"],
                ))

        for column in REQUIRED_COLUMNS:
            missing_issue = detect.check_missing_required(eff[column])
            if missing_issue:
                issues.append(_issue(
                    row_no, pid, column, eff[column], missing_issue["issue_type"],
                    missing_issue["severity"], missing_issue["message"], missing_issue["auto_fixable"],
                ))

        if i in dup_issues:
            dup = dup_issues[i]
            issues.append(_issue(
                row_no, pid, "patient_id", pid, dup["issue_type"],
                dup["severity"], dup["message"], dup["auto_fixable"],
            ))

    return issues


def summarize(issues: list, total_rows: int) -> dict:
    rows_with_issues = {issue["row"] for issue in issues}
    summary = {"error": 0, "warning": 0, "info": 0}
    for issue in issues:
        summary[issue["severity"]] = summary.get(issue["severity"], 0) + 1
    return {
        "total_rows": total_rows,
        "clean_rows": total_rows - len(rows_with_issues),
        "issues": issues,
        "summary": summary,
    }


def validate(csv_path: str) -> dict:
    df = load_dataset(csv_path)
    row_results = build_row_results(df)
    issues = collect_issues(row_results)
    return summarize(issues, len(df))


def clean(csv_path: str, output_dir: str) -> dict:
    df = load_dataset(csv_path)
    row_results = build_row_results(df)
    issues = collect_issues(row_results)

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cleaned_rows = []
    for rr in row_results:
        row = dict(rr.effective)
        cleaned_rows.append(row)
    cleaned_df = pd.DataFrame(cleaned_rows, columns=COLUMNS)

    changelog = [issue for issue in issues if issue["auto_fixable"]]
    needs_review = [issue for issue in issues if not issue["auto_fixable"]]

    changelog_entries = []
    for i, rr in enumerate(row_results):
        row_no = i + 1
        pid = rr.effective["patient_id"]
        for column, result in rr.changes:
            before = rr.raw[column]
            after = result.new_value
            if column == "dose" and isinstance(after, tuple):
                before = f"dose:{rr.raw.get('dose', '')} / unit:{rr.raw.get('unit', '')}"
                after = f"dose:{after[0]} / unit:{after[1]}"
            changelog_entries.append({
                "row": row_no,
                "patient_id": pid,
                "column": column,
                "before": before,
                "after": after,
                "rule": result.rule,
                "message": result.message,
            })

    cleaned_csv_path = out_dir / "cleaned.csv"
    changelog_path = out_dir / "changelog.json"
    needs_review_path = out_dir / "needs_review.json"

    cleaned_df.to_csv(cleaned_csv_path, index=False)

    import json
    changelog_path.write_text(
        json.dumps(changelog_entries, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    needs_review_path.write_text(
        json.dumps(needs_review, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {
        "cleaned_csv": str(cleaned_csv_path),
        "changelog": str(changelog_path),
        "needs_review": str(needs_review_path),
        "summary": {
            "fixed": len(changelog_entries),
            "needs_review": len(needs_review),
        },
    }

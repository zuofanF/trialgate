"""Generates a plain-language quality summary report (Markdown string) for
non-technical readers."""
from __future__ import annotations

from pathlib import Path

import engine

SEVERITY_LABEL = {
    "error": "Critical (must fix)",
    "warning": "Needs attention",
    "info": "For reference",
}

# issue_type -> category name
CATEGORY_MAP = {
    "date_format": "Formatting inconsistencies (auto-fixable)",
    "number_format": "Formatting inconsistencies (auto-fixable)",
    "drug_name_variant": "Formatting inconsistencies (auto-fixable)",
    "gender_code": "Formatting inconsistencies (auto-fixable)",
    "patient_id_format": "Formatting inconsistencies (auto-fixable)",
    "unit_conversion": "Formatting inconsistencies (auto-fixable)",
    "dose_unit_mixed": "Formatting inconsistencies (auto-fixable)",
    "temperature_unit": "Formatting inconsistencies (auto-fixable)",
    "age_outlier": "Numeric outliers (needs review)",
    "systolic_outlier": "Numeric outliers (needs review)",
    "weight_outlier": "Numeric outliers (needs review)",
    "diastolic_gt_systolic": "Numeric outliers (needs review)",
    "dose_invalid": "Numeric outliers (needs review)",
    "invalid_date": "Date logic contradictions (needs review)",
    "future_date": "Date logic contradictions (needs review)",
    "death_before_visit": "Date logic contradictions (needs review)",
    "ae_before_visit": "Date logic contradictions (needs review)",
    "missing_required": "Missing required fields (needs review)",
    "duplicate_id_conflict": "Duplicate patient IDs (needs review)",
    "duplicate_id": "Duplicate patient IDs (needs review)",
    "text_in_numeric": "Other (needs review)",
    "unit_validity": "Other (needs review)",
    "remark_keyword": "Other (for reference)",
}

# category name -> plain-language description of the whole category
CATEGORY_DESCRIPTIONS = {
    "Formatting inconsistencies (auto-fixable)": "Dates, numbers, drug names, and gender codes are written inconsistently. These are fixed automatically without losing any information.",
    "Numeric outliers (needs review)": "Age, blood pressure, weight, or dose values that are clinically or physiologically implausible. These require a human to review.",
    "Date logic contradictions (needs review)": "Dates are out of order, or a date that doesn't exist / is in the future was entered.",
    "Missing required fields (needs review)": "Required fields such as age, gender, unit, or diastolic BP are blank.",
    "Duplicate patient IDs (needs review)": "The same patient ID appears more than once. If the attributes conflict, this is a critical issue.",
    "Other (needs review)": "Text entered in a numeric field, or an unusual drug/unit combination, among other items that need individual review.",
    "Other (for reference)": "The remarks field contains an operational note. For reference only.",
}

CATEGORY_ORDER = [
    "Formatting inconsistencies (auto-fixable)",
    "Numeric outliers (needs review)",
    "Date logic contradictions (needs review)",
    "Missing required fields (needs review)",
    "Duplicate patient IDs (needs review)",
    "Other (needs review)",
    "Other (for reference)",
]


def generate_report(csv_path: str) -> str:
    result = engine.validate(csv_path)

    total_rows = result["total_rows"]
    clean_rows = result["clean_rows"]
    issues = result["issues"]
    summary = result["summary"]

    lines = []
    lines.append("# Data Quality Report")
    lines.append("")
    lines.append(f"File: `{Path(csv_path).name}`")
    lines.append("")

    if total_rows == 0:
        lines.append("No records were found in this file. Please check its contents.")
        return "\n".join(lines)

    clean_pct = round(clean_rows / total_rows * 100, 1)
    lines.append("## Overview")
    lines.append("")
    lines.append(f"- Total records: **{total_rows}**")
    lines.append(f"- Clean (ready to submit as-is): **{clean_rows}** ({clean_pct}%)")
    lines.append(f"- Records with at least one issue: **{total_rows - clean_rows}**")
    lines.append("")
    lines.append("### Breakdown by severity")
    lines.append("")
    lines.append(f"- {SEVERITY_LABEL['error']}: {summary.get('error', 0)}")
    lines.append(f"- {SEVERITY_LABEL['warning']}: {summary.get('warning', 0)}")
    lines.append(f"- {SEVERITY_LABEL['info']}: {summary.get('info', 0)}")
    lines.append("")

    if not issues:
        lines.append("No issues were found in any record. This data is ready to submit.")
        return "\n".join(lines)

    by_category: dict = {}
    for issue in issues:
        category = CATEGORY_MAP.get(issue["issue_type"], "Other (needs review)")
        explanation = CATEGORY_DESCRIPTIONS.get(category, "See the message field of each issue for details.")
        bucket = by_category.setdefault(category, {"count": 0, "explanation": explanation, "examples": []})
        bucket["count"] += 1
        if len(bucket["examples"]) < 3:
            bucket["examples"].append(issue)

    lines.append("## Issues by category")
    lines.append("")

    ordered_categories = [c for c in CATEGORY_ORDER if c in by_category]
    ordered_categories += [c for c in by_category if c not in CATEGORY_ORDER]

    for idx, category in enumerate(ordered_categories, start=1):
        bucket = by_category[category]
        auto_fixable = "auto-fixable" in category
        lines.append(f"### {idx}. {category} — {bucket['count']}")
        lines.append("")
        lines.append(bucket["explanation"])
        lines.append("")
        if auto_fixable:
            lines.append("→ Run `clean_dataset` to resolve these automatically.")
        else:
            lines.append("→ Cannot be fixed automatically. A data manager should review these.")
        lines.append("")
        lines.append("Examples:")
        for issue in bucket["examples"]:
            lines.append(
                f"- Patient {issue['patient_id']} ({issue['column']}): {issue['message']}"
            )
        lines.append("")

    lines.append("## Next steps")
    lines.append("")
    lines.append("1. Run `clean_dataset` to resolve all auto-fixable issues at once.")
    lines.append("2. Review the items in the resulting `needs_review.json` -- these require human judgment.")
    lines.append("3. Once everything is resolved, run `validate_dataset` again to confirm no issues remain.")

    return "\n".join(lines)

# TrialGate

A **deterministic** MCP server for clinical trial data, built in two parts:

1. **Data cleaning** (`validate_dataset` / `clean_dataset` / `quality_report`)
   — cleans a flat CSV of trial visit records (the kind exported from an EHR).
2. **Patient-trial matching** (`build_patient_profile` / `check_trial_eligibility`)
   — ingests one patient's raw records (medical history, prescriptions, labs,
   urine tests, daily glucose logs), normalizes them into a clean profile,
   and checks that patient against a specific trial's eligibility criteria.

No LLM calls are used anywhere in the rule logic — the same input always
produces the same output.

## Setup

```bash
cd trialgate
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Run the tests:

```bash
pytest
```

## MCP Tools

TrialGate exposes 5 tools: 3 for CSV cleaning, 2 for patient-trial matching.

### 1. `validate_dataset(csv_path: str) -> str`

Detects every problem in the data **without changing it**, returning a
structured JSON string.

```json
{
  "total_rows": 31,
  "clean_rows": 8,
  "issues": [
    {
      "row": 12,
      "patient_id": "P004",
      "column": "age",
      "value": "250",
      "issue_type": "outlier",
      "severity": "error",
      "message": "Age 250 is not physiologically plausible",
      "auto_fixable": false
    }
  ],
  "summary": {"error": 15, "warning": 10, "info": 4}
}
```

### 2. `clean_dataset(csv_path: str, output_dir: str) -> str`

Fixes every auto-fixable problem (information-preserving normalizations
only) and writes 3 files to `output_dir`:

1. `cleaned.csv` — the cleaned data
2. `changelog.json` — a record of every change (row, column, before, after, rule applied)
3. `needs_review.json` — items that couldn't be auto-fixed and need human judgment

The return value is the absolute path to each file plus a summary
(`fixed` count, `needs_review` count).

### 3. `quality_report(csv_path: str) -> str`

Returns a plain-language Markdown summary for non-technical readers:
issue counts by category, severity, and whether each category is
auto-fixable.

### 4. `build_patient_profile(records_dir: str) -> str`

Ingests one patient's raw records (any mix of `.json`/`.csv` files named
`medical_history`, `prescriptions`, `labs`, `urine_test`, `daily_log`) and
returns a single clean, normalized profile: demographics, diagnoses,
current medications, latest lab values, latest urine test, and a glucose
log summary. Files are classified and parsed by extension, so labs/urine
tests can arrive as CSV while everything else is JSON (or any other
combination) — the output is identical either way.

### 5. `check_trial_eligibility(records_dir: str, trial_id: str) -> str`

The main demo tool: runs `build_patient_profile` under the hood, then
evaluates the resulting profile against a named trial's eligibility
criteria (currently `"glycontrol_x"`, a mock Type 2 Diabetes trial).
Returns `eligible: true/false` plus a criterion-by-criterion breakdown
(`passed` + a plain-language `detail` for every criterion, not just the
failing ones) and a one-line `summary`.

## Cleaning Rules

### Safe to auto-fix (normalization -- always recorded in changelog)

- Unify dates to ISO 8601 (US MM/DD/YYYY, European DD/MM/YYYY, spelled-out month names)
- Clean up numeric formatting (stray whitespace, thousands-separator commas)
- Unify drug name variants (brand names/abbreviations -> generic name, trailing whitespace removed)
- Unify gender codes (Male/male/1 -> M, Female/female/2 -> F)
- Unify patient ID format (`P` + 3 digits)
- Unit conversion (g -> mg), split a dose field with an embedded unit
- Convert a stray Celsius reading to Fahrenheit (the canonical unit is
  Fahrenheit; a value in the 34-42 range looks like Celsius by mistake,
  flagged as a warning)

### Detect only (never auto-fixed -- goes to needs_review)

Age/blood-pressure/weight outliers, diastolic > systolic, dose <= 0,
death date or adverse-event onset date before the visit date, a date
that doesn't exist, a future date, duplicate patient IDs (error if
attributes conflict), missing required fields, text in a numeric field,
an implausible unit for the drug, operational notes in the remarks
field.

### Design principles

1. **Deterministic**: the same input always produces the same output. No LLM calls in the rule logic.
2. **No silent edits**: every change is recorded in the changelog.
3. **When in doubt, don't fix it**: anything that changes the *meaning* of a value goes to needs_review instead.

## Patient-Trial Matching

### The trial: GlyControl-X

A mock Phase 3 study for adults with Type 2 Diabetes inadequately
controlled on Metformin monotherapy. Every criterion is checked
deterministically against the clean patient profile (see
`patient_intake/trials.py`):

1. Age 18–75
2. Confirmed Type 2 Diabetes Mellitus diagnosis (ICD-10 E11.9)
3. On stable Metformin monotherapy for ≥ 90 days, not on insulin
4. Latest HbA1c between 7.0% and 10.0%
5. Latest eGFR ≥ 45 mL/min/1.73m²
6. Latest urine protein result negative or trace (no proteinuria)
7. No severe hypoglycemia event (glucose < 54 mg/dL) in the daily glucose log
8. Not pregnant

### The two demo patients

`data/patients/DEMO-001/` and `data/patients/DEMO-002/` each contain one
patient's raw records (`medical_history.json`, `prescriptions.json`,
`labs.csv`, `urine_test.csv`, `daily_log.json`):

- **DEMO-001** passes all 8 criteria → `eligible: true`
- **DEMO-002** is on insulin (fails the monotherapy requirement) and has
  a low eGFR of 38 (fails the renal-function requirement) →
  `eligible: false`, with those 2 criteria's `detail` explaining why

### Demo script

```
check_trial_eligibility(records_dir=".../data/patients/DEMO-001", trial_id="glycontrol_x")
  -> eligible: true, all 8 criteria pass

check_trial_eligibility(records_dir=".../data/patients/DEMO-002", trial_id="glycontrol_x")
  -> eligible: false, fails "metformin_monotherapy" (on insulin) and "egfr_min" (eGFR 38 < 45)
```

Running both back-to-back is the clearest way to show the tool actually
reasons about the data rather than always returning the same answer.

## Registering with Claude Desktop / Claude Code

Add this to `~/Library/Application Support/Claude/claude_desktop_config.json`
(or the equivalent Claude Code MCP config):

```json
{
  "mcpServers": {
    "trialgate": {
      "command": "/absolute/path/trialgate/.venv/bin/python",
      "args": ["/absolute/path/trialgate/server.py"]
    }
  }
}
```

For Claude Code, use `claude mcp add` or create a `.mcp.json` at the repo
root with the same block. `.mcp.json` is gitignored (the paths are
machine-specific — point them at your own clone's `.venv`), so each
teammate creates their own after running Setup above.

## Example agent workflows

**Data cleaning** — when a user asks:

> "Get `clinical_records_dirty.csv` ready to submit."

1. The agent calls `validate_dataset` first to see what's wrong
2. Then calls `clean_dataset` to fix everything that's auto-fixable
3. Presents the contents of `needs_review.json` to the user for manual review
4. Optionally calls `quality_report` to give a plain-language summary

**Trial matching** — when a user asks:

> "Is this patient eligible for the GlyControl-X trial?"

1. The agent calls `check_trial_eligibility` with the patient's records
   directory and `trial_id="glycontrol_x"`
2. It reports the `eligible` verdict and quotes the failing criteria's
   `detail` (if any) directly from the response

All 5 tools are meant to be composed autonomously by the agent -- each
docstring in `server.py` spells out its purpose, arguments, return shape,
and when to call it.

## File layout

```
trialgate/
├── server.py          # MCP entry point. 5 thin @mcp.tool() wrappers
├── rules/
│   ├── normalize.py   # auto-fix rules (normalization)
│   └── detect.py      # detect-only rules
├── engine.py           # CSV-cleaning rule pipeline + changelog generation
├── report.py           # plain-language report generation
├── patient_intake/
│   ├── parsers.py      # per-extension raw record loading (.json, .csv)
│   ├── clean_profile.py # builds a normalized PatientProfile from raw records
│   └── trials.py        # deterministic eligibility criteria + evaluator
├── tests/
│   ├── test_engine.py         # CSV-cleaning acceptance tests
│   └── test_patient_intake.py # patient-profile + trial-matching tests
├── data/
│   ├── clinical_records_dirty.csv  # CSV-cleaning test data (29 known errors)
│   └── patients/
│       ├── DEMO-001/   # eligible demo patient's raw records
│       └── DEMO-002/   # ineligible demo patient's raw records
├── pyproject.toml
└── README.md
```

## Out of scope

CDISC SDTM conversion, a web UI, database persistence, auth/billing,
input formats other than CSV/JSON, and trials other than GlyControl-X are
all out of scope for this build.

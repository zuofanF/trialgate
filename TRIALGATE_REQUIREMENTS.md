# TrialGate — Clinical Trial Data Cleaning MCP Server Requirements

## 1. Background & Purpose

Clinical trial data sourced from electronic health records (CSV exports) is
full of inconsistent formatting, data-entry mistakes, and logical
contradictions. Today this cleanup is done by hand at CRO companies, which
is expensive.

This project builds a **deterministic data-cleaning tool (MCP server) that
an AI agent can call directly**.

- User story: a data manager at a pharma company or research institution
  asks an agent (Claude, etc.) to "get this data ready to submit" -> the
  agent calls this tool.
- Built for a hackathon (YC RFS "Software for Agents"), with roughly 5
  hours of dev time. **A working demo matters more than covering every
  possible rule — reliability first.**

## 2. Tech Stack

- Python 3.11+
- `fastmcp` (MCP server framework)
- `pandas` (CSV processing)
- Prefer the standard library; keep dependencies minimal
- No database. All I/O is files (CSV / JSON)

## 3. MCP Tools (exactly 3 are exposed)

### 3.1 `validate_dataset(csv_path: str) -> str`

Detects every problem **without modifying the data**, returning structured JSON.

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

### 3.2 `clean_dataset(csv_path: str, output_dir: str) -> str`

Fixes every auto-fixable problem and writes 3 files:

1. `cleaned.csv` — the cleaned data
2. `changelog.json` — **a record of every change** (row, column, before, after, rule applied)
3. `needs_review.json` — items that can't be auto-fixed and need human judgment

The return value is the 3 file paths plus a summary (N fixed, M needing review).

### 3.3 `quality_report(csv_path: str) -> str`

Returns a plain-language Markdown summary for non-technical readers:
problem counts by category, severity, and whether each is fixable.

## 4. Cleaning Rule Spec

### 4.1 Safe to auto-fix (normalization) — always recorded in the changelog

| Rule | Example |
|---|---|
| Unify dates to ISO 8601 | US `06/14/2026` -> `2026-06-14`; European `14/06/2026` -> `2026-06-14`; spelled-out `June 4, 2026` -> `2026-06-04` |
| Clean up numeric formatting | Stray whitespace `'45 '` -> `'45'`; thousands separators `1,000` -> `1000` |
| Unify drug name variants | `ASA` / `Tylenol ` -> `Aspirin` / `Acetaminophen` (mapping-dictionary approach), trailing whitespace removed |
| Unify gender codes -> `M`/`F` | `Male`/`1` -> `M`, `Female`/`2` -> `F` |
| Unify patient ID format -> `P` + 3 digits | `P-011` -> `P011`, `12` -> `P012` |
| Unit conversion: g -> mg | `0.5 g` -> `500 mg` |
| Split an embedded unit out of the dose field | dose `500mg` -> dose `500` + unit `mg` |
| Convert a stray Celsius reading to Fahrenheit | canonical unit is Fahrenheit; a value in 34.0-42.0 is assumed to be Celsius by mistake: `36.9` -> `98.4` (flagged as a warning) |

### 4.2 Detect only (never auto-fixed — goes to needs_review)

| Rule | Threshold / condition |
|---|---|
| Age outlier | < 0 or > 120 |
| Systolic BP outlier | < 50 or > 250 |
| Diastolic > systolic | possibly transposed |
| Weight outlier | < 20 or > 300 (assumes an adult trial) |
| Dose negative or zero | |
| Death date < visit date | logical date contradiction |
| Adverse event onset date < visit date | logical date contradiction |
| Nonexistent date | e.g. `2026-02-30`, fails to parse |
| Future date | after today |
| Duplicate patient ID | severity=error if attributes (e.g. gender) conflict for the same ID |
| Missing required field | age, gender, unit, diastolic BP, etc. |
| Text in a numeric field | e.g. `N/A` -> detected only, value kept as-is |
| Implausible unit | `mL` for a tablet drug (Aspirin/Metformin) -> warning |
| Operational notes in remarks | keywords like "duplicate", "void" -> info |

### 4.3 Design Principles

1. **Deterministic**: the same input always produces the same output. No LLM calls in the rule logic.
2. **No silent edits**: every change must be recorded in the changelog. Even one missed entry is a bug.
3. **When in doubt, don't fix it**: auto-fixing is limited to "normalization that loses no information." Anything that changes the *meaning* of a value goes to needs_review instead.
4. Columns assume an English header (`patient_id, age, gender, visit_date, drug_name, dose, unit, systolic_bp, diastolic_bp, weight_kg, temperature, adverse_event, ae_onset_date, death_date, remarks`).

## 5. File Layout

```
trialgate/
├── server.py          # MCP entry point. Just 3 thin @mcp.tool() wrappers
├── rules/
│   ├── normalize.py   # 4.1 normalization rules
│   └── detect.py      # 4.2 detect-only rules
├── engine.py          # applies rules in sequence + builds the changelog
├── report.py          # plain-language report generation
├── tests/
│   └── test_engine.py # acceptance tests (below)
├── data/
│   └── clinical_records_dirty.csv  # test data (29 known errors)
├── pyproject.toml
└── README.md          # install instructions, tool docs, agent usage examples
```

## 6. Acceptance Criteria (implement & verify in this order)

1. **Milestone 1**: `validate_dataset` detects all 29 known errors in
   `clinical_records_dirty.csv`, with zero false positives on the 8 clean
   rows (P001, P002, P006, P009, P014, P019, P024, P028).
2. **Milestone 2**: `clean_dataset` fixes every normalization issue, with
   the changelog matching all changes 1:1. Running `validate_dataset` again
   on `cleaned.csv` shows the normalization issues gone, leaving only the
   needs_review-type issues.
3. **Milestone 3**: `quality_report` returns a Markdown report readable by
   a non-technical reader.
4. **Milestone 4**: register the server with Claude Desktop / Claude Code
   and confirm that a single natural-language request ("get this CSV ready
   to submit") autonomously drives calls to all 3 tools.

## 7. How to Build This (instructions to Claude Code)

- Build `engine.py` + `rules/` as pure Python first and verify detection
  counts against the test data with `pytest`, before layering the MCP
  server on top (debugging MCP is a pain, so get the logic right without
  it first).
- Each tool's docstring is the manual an agent will read. **Be concrete
  about purpose, arguments, return shape, and when to call it** — this
  challenge is partly judged on machine-readable documentation.
- Error handling: if the file doesn't exist or the CSV is corrupt, never
  raise — return JSON with `issue_type="file_error"` instead.
- No `print()` debugging (it pollutes the stdio transport). Log to stderr instead.

## 8. MCP Registration (reference)

```json
{
  "mcpServers": {
    "trialgate": {
      "command": "python",
      "args": ["/absolute/path/trialgate/server.py"]
    }
  }
}
```

## 9. Out of Scope (not today)

- CDISC SDTM conversion (mention only, a future direction discussed in the pitch)
- Web UI / frontend
- Database persistence
- Auth / billing
- Input formats other than CSV (Excel support could be added in ~30 minutes via `pd.read_excel` if there's time to spare)

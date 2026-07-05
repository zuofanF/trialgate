# TrialGate

A **deterministic** toolkit for clinical trial data: clean the messy CSV exports that come out of an EHR, and check whether a patient matches a trial's eligibility criteria. The same input always produces the same output — no LLM sits inside the rule logic, so nothing drifts between runs.

It ships in two forms:

- **An MCP server** — 5 tools an agent (Claude, etc.) calls directly to validate, clean, and report on data, and to match patients to trials.
- **A web demo** — a landing page plus a live chat where you upload a file, type an instruction, and watch the real engine run in the browser.

## What it actually does

Point it at the sample dirty dataset and the two demo patients, and this is the real output:

```text
validate_dataset(clinical_records_dirty.csv)
  31 rows  →  8 clean, 29 issues  (13 error, 4 warning, 12 info)

clean_dataset(...)
  12 auto-fixed, 17 sent to needs_review.json    (nothing guessed, every fix logged)

check_trial_eligibility(DEMO-001, glycontrol_x)  →  ELIGIBLE    (8 / 8 criteria pass)
check_trial_eligibility(DEMO-002, glycontrol_x)  →  INELIGIBLE  (fails metformin_monotherapy, egfr_min)
```

Cleaning only touches things that can be normalized without losing information (date formats, drug-name variants, gender codes, unit conversions). Anything that would change the *meaning* of a value — an age of 250, a diastolic higher than systolic, a death date before the visit date — is never touched. It goes to `needs_review.json` for a human, and every automatic fix is written to `changelog.json`. No silent edits.

## Two ways to run it

### 1. As an MCP server

```bash
cd trialgate
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest                     # acceptance tests: detects all 29 known errors, 0 false positives
```

Register it with Claude Desktop or Claude Code:

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

Then ask an agent *"get `clinical_records_dirty.csv` ready to submit"* or *"is DEMO-002 eligible for GlyControl-X?"* and it drives the tools autonomously. Full tool reference, cleaning-rule spec, and the eligibility criteria live in **[`trialgate/README.md`](trialgate/README.md)**.

### 2. As a web demo

The backend is a single stdlib `http.server` script that adds **zero new dependencies** and calls the exact same engine the MCP tools use — so the browser demo is as deterministic as the server. It does import the `trialgate` package, so install its deps first (the MCP setup above, or just `pip install pandas`).

```bash
# from the repo root
python3 web/server.py            # http://127.0.0.1:8820
#   landing page  ->  /
#   chat demo     ->  /chat.html
```

Open `/chat.html`, drop in `trialgate/data/clinical_records_dirty.csv` (or hit a quick-start chip), type an instruction, and each tool call streams back as a card with the real result — issues found, values fixed, criteria passed or failed.

**Real-LLM mode (optional).** By default the chat uses a deterministic keyword router, so it works offline with no key. Set an OpenAI-compatible key and the chat is driven by a real LLM doing tool/function calling instead — the model only decides *which* tool to call; the tools themselves stay deterministic.

```bash
export OPENAI_API_KEY=sk-...                                   # env only — never commit a key
export OPENAI_BASE_URL=https://api.stepfun.com/step_plan/v1    # default shown
export OPENAI_MODEL=step-3.7-flash                             # default shown
python3 web/server.py
```

`GET /api/health` reports which mode is live (`{"llm": true, "model": "step-3.7-flash"}` when a key is set).

## The 5 tools

| Tool | What it does |
|---|---|
| `validate_dataset(csv_path)` | Detects every issue **without changing the file**. Returns structured JSON: per-row issues + a severity summary. |
| `clean_dataset(csv_path, output_dir)` | Applies information-preserving fixes only. Writes `cleaned.csv`, `changelog.json`, `needs_review.json`. |
| `quality_report(csv_path)` | A plain-language Markdown summary for a non-technical reader. |
| `build_patient_profile(records_dir)` | Normalizes one patient's mixed `.json`/`.csv` records into a single clean profile. |
| `check_trial_eligibility(records_dir, trial_id)` | Runs the full pipeline and returns an `eligible` verdict with a per-criterion breakdown. |

The trial today is **GlyControl-X**, a mock Phase 3 Type 2 Diabetes study with 8 deterministic criteria (age, T2DM diagnosis, stable Metformin monotherapy, HbA1c, eGFR, urine protein, no severe hypoglycemia, not pregnant). DEMO-001 passes all 8; DEMO-002 is on insulin and has an eGFR of 38, so it fails two — a back-to-back run of the two is the clearest proof the tool reasons about the data rather than returning a canned answer.

## Design principles

1. **Deterministic** — the same input always produces the same output. No LLM calls in the rule logic.
2. **No silent edits** — every automatic change is recorded in `changelog.json`. One missing entry is a bug.
3. **When in doubt, don't fix it** — auto-fixing is limited to normalization that loses no information; anything judgment-dependent goes to `needs_review.json`.

## Repo layout

```
.
├── trialgate/                 # the MCP server package (see its own README for the deep reference)
│   ├── server.py              # 5 @mcp.tool() wrappers
│   ├── engine.py              # CSV validate/clean pipeline + changelog
│   ├── rules/                 # normalize.py (auto-fix) + detect.py (detect-only)
│   ├── report.py              # plain-language report
│   ├── patient_intake/        # raw-record parsers, profile builder, trial criteria
│   ├── tests/                 # engine + patient-intake acceptance tests
│   ├── data/                  # clinical_records_dirty.csv + DEMO-001 / DEMO-002 patients
│   └── README.md              # full tool docs, rule spec, eligibility criteria
├── web/                       # the demo
│   ├── index.html             # landing page (built from real engine output)
│   ├── chat.html              # live upload-and-run chat demo
│   └── server.py              # stdlib backend; deterministic router or real-LLM agent
└── TRIALGATE_REQUIREMENTS.md  # the original spec
```

## Status and scope

Built for a hackathon (YC RFS "Software for Agents") with reliability as the first priority. Out of scope for this build: CDISC SDTM conversion, database persistence, auth/billing, input formats beyond CSV/JSON, and trials other than GlyControl-X. The `web/` demo is a showcase, not a production surface — it binds to `127.0.0.1` and is meant to run locally.

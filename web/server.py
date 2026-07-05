#!/usr/bin/env python3
"""TrialGate live-demo backend (Python standard library only).

Serves the static `web/` pages and exposes one endpoint, POST /api/agent,
that runs the REAL TrialGate tools (validate_dataset / clean_dataset /
quality_report / build_patient_profile / check_trial_eligibility) on an
uploaded file or a bundled preset, and returns a step-by-step transcript
of the tool calls the "agent" made.

No cleaning or eligibility rules are re-implemented here. This is a thin
HTTP wrapper around the same engine.py the MCP server uses, so results are
identical to what an MCP client (Claude) would get. Uploaded content is
written to a temp directory, processed, and deleted -- nothing is kept.

Run:
    python3 web/server.py            # then open http://127.0.0.1:8820
    python3 web/server.py 9000       # custom port
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent          # web/
REPO = HERE.parent                               # repo root
PKG = REPO / "trialgate"                          # the python package dir
sys.path.insert(0, str(PKG))

import engine                                     # noqa: E402
import report                                     # noqa: E402
from engine import FileError                      # noqa: E402
from patient_intake import clean_profile, parsers, trials  # noqa: E402
from patient_intake.parsers import RecordsError   # noqa: E402
from patient_intake.trials import TrialNotFoundError  # noqa: E402

SAMPLE_CSV = PKG / "data" / "clinical_records_dirty.csv"
PATIENTS = PKG / "data" / "patients"
PATIENT_KINDS = {"medical_history", "prescriptions", "labs", "urine_test", "daily_log"}
MAX_BODY = 4 * 1024 * 1024   # 4 MB cap on uploads
DEFAULT_PORT = 8820


# ---------------------------------------------------------------------------
# Intent routing (deterministic): map a plain-language instruction to the
# tool sequence an agent would run. Mirrors the workflow in the README.
# ---------------------------------------------------------------------------
def classify(instruction: str) -> str:
    t = (instruction or "").lower()
    if any(k in t for k in ("eligib", "trial", "match", "qualify", "glycontrol", "enroll")):
        return "eligibility"
    if any(k in t for k in ("report", "summary", "summarize", "plain", "non-technical", "manager", "explain")):
        return "report"
    if any(k in t for k in ("clean", "fix", "submit", "ready", "prepare", "normali", "tidy", "standardi")):
        return "clean"
    if any(k in t for k in ("validate", "check", "inspect", "wrong", "problem", "issue", "find", "audit", "scan")):
        return "validate"
    return "clean"   # default: run the full get-it-ready pipeline


def _summarize_validate(v: dict) -> str:
    s = v["summary"]
    dirty = v["total_rows"] - v["clean_rows"]
    return (
        f"I scanned {v['total_rows']} records with validate_dataset. "
        f"{v['clean_rows']} are already clean; {dirty} have at least one issue "
        f"({s.get('error', 0)} errors, {s.get('warning', 0)} warnings, {s.get('info', 0)} info). "
        "Nothing was modified. Ask me to “clean it” and I'll fix the safe ones."
    )


def run_csv(instruction: str, csv_text: str, filename: str) -> dict:
    intent = classify(instruction)
    tmp = Path(tempfile.mkdtemp(prefix="trialgate_"))
    try:
        src = tmp / (filename or "upload.csv")
        src.write_text(csv_text, encoding="utf-8")

        try:
            v = engine.validate(str(src))
        except FileError as exc:
            return {"error": exc.message,
                    "hint": "TrialGate expects a flat visit-records CSV with the standard columns "
                            "(patient_id, age, gender, visit_date, drug_name, dose, unit, ...)."}

        steps = [{
            "kind": "validate", "tool": "validate_dataset",
            "args": {"csv_path": filename}, "result": v,
        }]
        intro = f"Got {filename}. Running validate_dataset first so I know what's wrong before touching anything."

        if intent == "validate":
            reply = _summarize_validate(v)

        elif intent == "report":
            rep = report.generate_report(str(src))
            steps.append({"kind": "report", "tool": "quality_report",
                          "args": {"csv_path": filename}, "result": {"markdown": rep}})
            reply = "Here's a plain-language quality report you can hand to a non-technical reviewer."

        else:  # clean -> full pipeline
            out = tmp / "out"
            c = engine.clean(str(src), str(out))
            changelog = json.loads(Path(c["changelog"]).read_text(encoding="utf-8"))
            needs = json.loads(Path(c["needs_review"]).read_text(encoding="utf-8"))
            steps.append({
                "kind": "clean", "tool": "clean_dataset",
                "args": {"csv_path": filename, "output_dir": "./out"},
                "result": {"summary": c["summary"], "changelog": changelog, "needs_review": needs},
            })
            rep = report.generate_report(str(src))
            steps.append({"kind": "report", "tool": "quality_report",
                          "args": {"csv_path": filename}, "result": {"markdown": rep}})
            reply = (
                f"Done. I auto-fixed {c['summary']['fixed']} information-preserving issues "
                f"(all recorded in changelog.json) and flagged {c['summary']['needs_review']} that need a "
                "human decision (needs_review.json). Nothing was edited silently."
            )

        return {"mode": intent, "source": "live", "reply_intro": intro, "steps": steps, "reply": reply}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_eligibility(files: list, trial_id: str = "glycontrol_x", label: str | None = None) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="trialgate_pt_"))
    try:
        for f in files:
            name = Path(f["name"]).name  # strip any path component
            (tmp / name).write_text(f["content"], encoding="utf-8")

        try:
            records = parsers.load_records(str(tmp))
            profile = clean_profile.build_profile(records)
        except RecordsError as exc:
            return {"error": exc.message,
                    "hint": "A patient folder needs files named medical_history, prescriptions, labs, "
                            "urine_test, and daily_log (.json or .csv)."}

        steps = [{
            "kind": "profile", "tool": "build_patient_profile",
            "args": {"records_dir": label or "patient/"}, "result": profile,
        }]
        try:
            res = trials.evaluate_eligibility(profile, trial_id)
        except TrialNotFoundError as exc:
            return {"error": exc.message}

        steps.append({
            "kind": "eligibility", "tool": "check_trial_eligibility",
            "args": {"records_dir": label or "patient/", "trial_id": trial_id}, "result": res,
        })
        intro = "Let me merge these records into one clean profile, then check every GlyControl-X criterion."
        return {"mode": "eligibility", "source": "live", "reply_intro": intro,
                "steps": steps, "reply": res["summary"]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def resolve_preset(preset: str):
    if preset == "sample":
        return {"kind": "csv", "filename": "clinical_records_dirty.csv",
                "content": SAMPLE_CSV.read_text(encoding="utf-8")}
    if preset in ("DEMO-001", "DEMO-002"):
        d = PATIENTS / preset
        if not d.is_dir():
            return None
        files = [{"name": p.name, "content": p.read_text(encoding="utf-8")}
                 for p in sorted(d.iterdir()) if p.is_file()]
        return {"kind": "patient", "label": f"data/patients/{preset}/", "files": files}
    return None


def handle_agent(payload: dict) -> dict:
    instruction = payload.get("instruction", "") or ""
    preset = payload.get("preset")
    files = payload.get("files") or []

    if preset:
        r = resolve_preset(preset)
        if not r:
            return {"error": f"Unknown preset '{preset}'."}
        if r["kind"] == "csv":
            return run_csv(instruction or "Get this CSV ready to submit.", r["content"], r["filename"])
        return run_eligibility(r["files"], "glycontrol_x", r["label"])

    if not files:
        return {"error": "No file attached. Upload a CSV or a patient's record files, or pick a quick-start below."}

    kinds = {Path(f.get("name", "")).stem.lower() for f in files}
    if kinds & PATIENT_KINDS:
        return run_eligibility(files, "glycontrol_x", "uploaded patient/")

    csv = next((f for f in files if str(f.get("name", "")).lower().endswith(".csv")), files[0])
    return run_csv(instruction or "Get this CSV ready to submit.", csv.get("content", ""), csv.get("name", "upload.csv"))


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------
CTYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
          ".js": "application/javascript; charset=utf-8", ".svg": "image/svg+xml",
          ".json": "application/json; charset=utf-8", ".ico": "image/x-icon"}


class Handler(BaseHTTPRequestHandler):
    server_version = "TrialGateDemo/1.0"

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", ""):
            path = "/index.html"
        if path == "/chat":
            path = "/chat.html"
        if path == "/api/health":
            self._json(200, {"ok": True, "service": "trialgate-demo"})
            return

        target = (HERE / path.lstrip("/")).resolve()
        if not str(target).startswith(str(HERE)) or not target.is_file():
            self._json(404, {"error": "Not found"})
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", CTYPES.get(target.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/api/agent":
            self._json(404, {"error": "Not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self._json(413, {"error": "That file is too large for the demo (4 MB max)."})
            return
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except Exception:
            self._json(400, {"error": "Invalid JSON in request body."})
            return
        try:
            result = handle_agent(payload)
        except Exception as exc:  # never 500 the demo; surface it as an agent error
            result = {"error": f"The tool raised an unexpected error: {exc}"}
        self._json(200, result)

    def log_message(self, fmt, *args):  # concise stderr logging
        sys.stderr.write("· %s\n" % (fmt % args))


def main():
    port = DEFAULT_PORT
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"TrialGate demo running:  http://127.0.0.1:{port}")
    print(f"  landing page  ->  http://127.0.0.1:{port}/")
    print(f"  chat demo     ->  http://127.0.0.1:{port}/chat.html")
    print("Ctrl-C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
        httpd.shutdown()


if __name__ == "__main__":
    main()

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

# ---- Real-LLM agent config (OpenAI-compatible). The API key comes from the
# environment only; it is never written to a file. When OPENAI_API_KEY is set,
# a model plans the tool calls; otherwise the deterministic keyword router runs.
import os
import urllib.error
import urllib.request

LLM_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
LLM_BASE = os.environ.get("OPENAI_BASE_URL", "https://api.stepfun.com/step_plan/v1").rstrip("/")
LLM_MODEL = os.environ.get("OPENAI_MODEL", "step-3.7-flash")
LLM_ENABLED = bool(LLM_KEY)


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


# ---------------------------------------------------------------------------
# Real-LLM agent (OpenAI-compatible tool calling). The model only decides
# WHICH tools to call and in what order; each tool runs the same real engine,
# so every value shown still comes from the deterministic code, not the model.
# ---------------------------------------------------------------------------
def _tools_for(is_patient: bool) -> list:
    csv_tools = [
        {"type": "function", "function": {"name": "validate_dataset",
            "description": "Detect every problem in the attached clinical-trial CSV without changing it. Returns issue counts and the full issue list. Call this first.",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {"name": "clean_dataset",
            "description": "Fix every auto-fixable, information-preserving issue in the attached CSV and return a changelog plus the items that need human review. Never makes silent judgment calls.",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {"name": "quality_report",
            "description": "Generate a plain-language Markdown quality report of the attached CSV for a non-technical reader.",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
    ]
    patient_tools = [
        {"type": "function", "function": {"name": "build_patient_profile",
            "description": "Build one normalized profile (demographics, diagnoses, medications, latest labs, urine test, glucose summary) from the attached patient records.",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {"name": "check_trial_eligibility",
            "description": "Evaluate the attached patient against a trial's eligibility criteria and return a per-criterion verdict. Builds the profile internally.",
            "parameters": {"type": "object", "properties": {
                "trial_id": {"type": "string", "enum": ["glycontrol_x"],
                             "description": "Which trial to check (only glycontrol_x is available)."}},
                "required": []}}},
    ]
    return patient_tools if is_patient else csv_tools


def _exec_tool(name: str, args: dict, ctx: dict) -> dict:
    """Run one tool against the uploaded files. Returns a transcript step."""
    try:
        if name == "validate_dataset":
            if "csv_path" not in ctx:
                raise ValueError("No dataset CSV is attached.")
            return {"kind": "validate", "tool": name, "args": {"csv_path": ctx.get("csv_name", "dataset.csv")},
                    "result": engine.validate(ctx["csv_path"])}
        if name == "quality_report":
            if "csv_path" not in ctx:
                raise ValueError("No dataset CSV is attached.")
            return {"kind": "report", "tool": name, "args": {"csv_path": ctx.get("csv_name", "dataset.csv")},
                    "result": {"markdown": report.generate_report(ctx["csv_path"])}}
        if name == "clean_dataset":
            if "csv_path" not in ctx:
                raise ValueError("No dataset CSV is attached.")
            out = tempfile.mkdtemp(prefix="trialgate_out_")
            try:
                c = engine.clean(ctx["csv_path"], out)
                changelog = json.loads(Path(c["changelog"]).read_text(encoding="utf-8"))
                needs = json.loads(Path(c["needs_review"]).read_text(encoding="utf-8"))
            finally:
                shutil.rmtree(out, ignore_errors=True)
            return {"kind": "clean", "tool": name,
                    "args": {"csv_path": ctx.get("csv_name", "dataset.csv"), "output_dir": "./out"},
                    "result": {"summary": c["summary"], "changelog": changelog, "needs_review": needs}}
        if name == "build_patient_profile":
            if "records_dir" not in ctx:
                raise ValueError("No patient records are attached.")
            recs = parsers.load_records(ctx["records_dir"])
            return {"kind": "profile", "tool": name, "args": {"records_dir": ctx.get("label", "patient/")},
                    "result": clean_profile.build_profile(recs)}
        if name == "check_trial_eligibility":
            if "records_dir" not in ctx:
                raise ValueError("No patient records are attached.")
            recs = parsers.load_records(ctx["records_dir"])
            prof = clean_profile.build_profile(recs)
            tid = args.get("trial_id") or "glycontrol_x"
            return {"kind": "eligibility", "tool": name,
                    "args": {"records_dir": ctx.get("label", "patient/"), "trial_id": tid},
                    "result": trials.evaluate_eligibility(prof, tid)}
        raise ValueError(f"Unknown tool '{name}'")
    except Exception as exc:  # feed the error back to the model so it can recover
        return {"kind": "error", "tool": name, "args": args, "result": {"error": str(exc)}}


def _llm_feedback(step: dict) -> str:
    """A compact but sufficient result summary fed back to the model (keeps
    tokens small while giving it enough to answer; the frontend still receives
    the full result for rich rendering)."""
    r, k = step["result"], step["kind"]
    if k == "validate":
        order = {"error": 0, "warning": 1, "info": 2}
        top = sorted(r.get("issues", []), key=lambda i: order.get(i.get("severity"), 3))[:10]
        return json.dumps({
            "total_rows": r["total_rows"], "clean_rows": r["clean_rows"], "summary": r["summary"],
            "issues_sample": [{"row": i["row"], "patient_id": i["patient_id"], "column": i["column"],
                               "severity": i["severity"], "message": i["message"]} for i in top]})
    if k == "clean":
        nr = r.get("needs_review", [])
        return json.dumps({
            "summary": r["summary"], "changelog_count": len(r.get("changelog", [])),
            "needs_review_count": len(nr),
            "needs_review_sample": [{"patient_id": i["patient_id"], "column": i["column"],
                                     "severity": i["severity"], "message": i["message"]} for i in nr[:8]]})
    if k == "report":
        return "Plain-language Markdown report generated and shown to the user."
    if k == "profile":
        return json.dumps({"patient_id": r.get("patient_id"), "age": r.get("age"),
                           "current_medications": r.get("current_medications"), "latest_labs": r.get("latest_labs")})
    if k == "eligibility":
        return json.dumps({
            "eligible": r["eligible"],
            "criteria": [{"id": c["id"], "passed": c["passed"], "detail": c["detail"]} for c in r["criteria"]]})
    if k == "error":
        return "ERROR: " + r.get("error", "")
    return "done"


def _llm_call(messages: list, tools: list) -> dict:
    body = json.dumps({
        "model": LLM_MODEL,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "temperature": 0.2,
    }).encode("utf-8")
    req = urllib.request.Request(
        LLM_BASE + "/chat/completions", data=body,
        headers={"Authorization": "Bearer " + LLM_KEY, "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))


SYSTEM_PROMPT = (
    "You are TrialGate, an assistant that prepares clinical-trial data by calling deterministic tools. "
    "You never guess, estimate, or fabricate values; you only report what the tools return. "
    "Decide which tools to call to satisfy the user's request, and call them. "
    "For 'get this ready to submit' or similar, the usual order is validate_dataset, then clean_dataset, then quality_report. "
    "For an eligibility question, call check_trial_eligibility (it builds the profile internally). "
    "Each tool is deterministic and only needs to be called once; never call the same tool twice. "
    "As soon as you have the results you need, stop calling tools and write your answer. "
    "When the tools are done, write a concise plain-language summary (at most 4 sentences) of what you found and did. "
    "Never invent numbers that are not in a tool result."
)


def run_llm_agent(instruction: str, files: list, label: str = None) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="trialgate_llm_"))
    try:
        for f in files:
            (tmp / Path(f["name"]).name).write_text(f["content"], encoding="utf-8")
        kinds = {Path(f["name"]).stem.lower() for f in files}
        is_patient = bool(kinds & PATIENT_KINDS)
        if is_patient:
            ctx = {"records_dir": str(tmp), "label": label or "patient/"}
            attach = "one patient's raw record files (medical history, prescriptions, labs, urine test, daily glucose log)"
        else:
            csv = next((f for f in files if f["name"].lower().endswith(".csv")), files[0])
            ctx = {"csv_path": str(tmp / Path(csv["name"]).name), "csv_name": csv["name"]}
            attach = "a clinical-trial visit-records CSV named " + csv["name"]

        tools = _tools_for(is_patient)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": (instruction or "Get this ready to submit.") + "\n\n(Attached: " + attach + ".)"},
        ]
        steps, intro, reply = [], None, ""
        executed = {}
        for _ in range(8):
            data = _llm_call(messages, tools)
            msg = data["choices"][0]["message"]
            assistant = {"role": "assistant", "content": msg.get("content") or ""}
            if msg.get("tool_calls"):
                assistant["tool_calls"] = msg["tool_calls"]
            messages.append(assistant)

            tcs = msg.get("tool_calls")
            if not tcs:
                reply = msg.get("content") or ""
                break
            if msg.get("content") and not intro:
                intro = msg["content"]
            for tc in tcs:
                fn = tc.get("function", {})
                try:
                    fargs = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    fargs = {}
                key = fn.get("name", "") + ":" + json.dumps(fargs, sort_keys=True)
                if key in executed:
                    step = executed[key]           # deterministic: reuse, don't re-run or re-show a duplicate card
                else:
                    step = _exec_tool(fn.get("name", ""), fargs, ctx)
                    executed[key] = step
                    steps.append(step)
                messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": _llm_feedback(step)})
        else:
            reply = reply or "I have gathered the tool results above."

        return {"mode": "llm", "source": "llm", "model": LLM_MODEL,
                "reply_intro": intro or ("Planning tool calls with " + LLM_MODEL + "."),
                "steps": steps, "reply": reply or "Done."}
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8")[:400]
        except Exception:
            detail = ""
        return {"error": "LLM API error " + str(exc.code) + ": " + detail}
    except Exception as exc:
        return {"error": "LLM agent failed: " + str(exc)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def handle_agent(payload: dict) -> dict:
    instruction = payload.get("instruction", "") or ""
    preset = payload.get("preset")
    files = payload.get("files") or []
    label = None

    if preset:
        r = resolve_preset(preset)
        if not r:
            return {"error": f"Unknown preset '{preset}'."}
        if r["kind"] == "csv":
            files = [{"name": r["filename"], "content": r["content"]}]
        else:
            files, label = r["files"], r["label"]

    if not files:
        return {"error": "No file attached. Upload a CSV or a patient's record files, or pick a quick-start below."}

    if LLM_ENABLED:
        return run_llm_agent(instruction, files, label)

    # deterministic fallback (keyword routing)
    kinds = {Path(f.get("name", "")).stem.lower() for f in files}
    if kinds & PATIENT_KINDS:
        return run_eligibility(files, "glycontrol_x", label or "uploaded patient/")
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
            self._json(200, {"ok": True, "service": "trialgate-demo",
                             "llm": LLM_ENABLED, "model": LLM_MODEL if LLM_ENABLED else None})
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

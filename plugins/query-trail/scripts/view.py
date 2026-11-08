"""view.py: the run viewer, one self-contained HTML page derived from a run folder (design spec v5 section 2.2 P11
artifacts, round 13; build brief r13).

Reads only the run folder (never the env file, never the network) and writes one file, RUN/view.html by default.
The page has inline CSS and JS, loads nothing, and carries no generation time: the same run folder gives the same
bytes (every time on the page is an artifact's own). Sections, in order:

Layout: the browser window decides the width (Max, 2026-11-08): no fixed pixel width on the page or its containers;
the step-graph SVG scales with its box (viewBox, width 100%, preserveAspectRatio) while that keeps it at 0.8 of its
actual size or more, else it opens at actual size (12 px text) and scrolls inside its own box ("Fit to window" /
"Actual size" switch it); columns are as wide as their longest node label with gaps of about half a node width;
whole B and the deliverable sit under the final step; the detail panel sits beside the graph and moves under it below 900 px; tables and SQL
scroll inside their own boxes.

1. header: run id, attempt, plugin_version, level, author mode, phase and status (with blocked_reason), the verdict
   quoted verbatim from report_status.json (with its labels, never paraphrased), independence, pin, reference,
   budget, as_of_utc;
2. gates and phases: the P0 to P11 strip (done / current / not reached, from run_state.json, the rule the run
   skill resumes by), each phase's own files found or not, its decisions.csv rows (a superseded row struck through),
   gate requests: gate_request.json open, gates/NN_<gate>_request.json answered, gates/stale_* stale;
3. the step graph: an inline SVG built from plan.json `inputs` (source tables on the left, prior_step edges between
   steps), layered left to right by longest path, the final step on the right and whole B under it pointing at the
   deliverable. A node shows name, verb, P6 check status and rows in -> out (checks/run_steps_checks.csv rows_in_out,
   run_steps_report.json jobs, manifest.json steps); an edge shows the upstream step's rows out. Clicking a node
   (or Enter on it) fills the detail panel: G11 sentence, kind/verb, inputs, columns, grain, pushed filters, note,
   rows, the job line, the step's P6 checks, its SQL and (collapsed) its executed SQL. A plain steps table follows;
4. whole query B: B.sql, executed/B.run.sql, the output contract, the deliverable (first 200 rows, "first 200 of
   N"), whole-B checks and the P7 reconciliation (mismatch rows: first 50);
5. checks: checks_manifest.csv plus the P6 step rows, classified as report.py classifies them (runctx.classify_
   checks), with a filter box; trace, logic and lint files;
6. cards and inputs: the question verbatim, the cards, value map, traps, shortlist, pin plan (as text, no Markdown
   engine);
7. files: every manifest.json files[] entry as a relative link, then the run files not in the manifest;
8. footer: how to regenerate, and the manifest seal (manifest.sha256, first 12) the page was built from.

CSV files are read through their sidecars (lib csv_contract, so sidecar types are honoured); a CSV without a usable
sidecar is shown as text with the reason. What is missing is named ("P7 not reached", "not found in the run
folder"); a run stopped at P2 renders too. Text is HTML-escaped, and "//" in text is written as an entity, so the
page bytes hold no URL even when a card quotes one.

Derived and unsealed: view.html is never added to manifest.json (manifest.late_outputs does not list it), the seal
does not cover it and close.py never touches it (it is unlisted). Regenerate it after any change to the run.

Fix r15 (cold review of rounds 13 and 14):
- M1: the verdict is labelled "From an earlier pass, not current" (and the footer says why) when the run is before
  P11, the seal no longer matches manifest.json, the seal is gone, or the report is from another attempt;
- M2: secret_scan.py scans view.html like any run file (lines quoting 00_question.txt word for word are Max's own
  words, INFO); every <pre> starts its text on a new line so a quoted question line stands alone. A banner "Not yet
  secret-scanned: do not share this page" tops the page when there is no secret_scan_report.json or a run file is
  newer than it. A scan hit only in view.html itself does not block regenerating it;
- M3: the per-run gate line uses state.missing_gates (round 14): "closed" or "no closing row";
- L1: --out may not point inside another run folder or under QT_HOME/runs;
- L6: node colours come from classify_checks, so a check not run at this level is not a gap.

CLI:  view.py --run-dir RUN [--out PATH]
Exit: 0 written (prints VIEW=<path> with forward slashes); 2 refused, nothing written: run_config.json or
      run_state.json missing or invalid; secret_scan_report.json lists hits in a run file other than view.html (the
      page would copy them) or is unreadable; --out is not an .html file, names a file inside the run folder other
      than RUN/view.html, or points inside another run folder or under QT_HOME/runs.
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import quote as url_quote

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import csv_contract as cc  # noqa: E402
from lib import runctx as c2  # noqa: E402
from lib import state  # noqa: E402
from lib.exit_codes import ExitCode, RefusedError  # noqa: E402

OUT_NAME = "view.html"
PHASES: tuple[str, ...] = state.PHASES
PHASE_NAMES = {"P0": "Preflight", "P1": "Cards", "P2": "Discovery", "P3": "Pin plan", "P4": "Author",
               "P5": "Decompose", "P6": "Run steps", "P7": "Reconcile", "P8": "Sample and trace", "P9": "Logic tests",
               "P10": "Rename", "P11": "Report and close"}
# The files each phase leaves (README phase table); shown as found or not, never as a verdict on the phase.
PHASE_FILES: dict[str, tuple[str, ...]] = {
    "P0": ("00_preflight.json", "00_capability_probe.csv"),
    "P1": ("00_question.txt", "01_question_card.md", "02_metric_card.md", "card_hashes.json"),
    "P2": ("05_catalog.csv", "06_profiles.csv", "07_candidates.csv", "08_shortlist.csv", "09_traps.csv",
           "11_value_map.csv", "13_table_decision.md", "14_discovery_lock.json", "03_output_contract.json"),
    "P3": ("15_pin_plan.json",),
    "P4": ("B.sql", "packet_freeze.json", "packet.sha256"),
    "P5": ("plan.json", "lint_report.csv", "step_sentences.json", "executed/compile_report.json"),
    "P6": ("run_steps_report.json", "checks/run_steps_checks.csv", "manifest.json", "outputs/B_whole.csv"),
    "P7": ("checks/reconcile_status.json", "checks/reconcile_B_vs_chain.csv"),
    "P8": ("samples/sample_manifest.json", "trace/trace_results.csv", "trace_report.md"),
    "P9": ("checks/logic_chain.csv", "checks/logic_reference.json"),
    "P10": ("renames.csv",),
    "P11": ("report.md", "report_status.json", "secret_scan_report.json", "manifest.sha256"),
}
STATUS_WORDS = {"done": "done", "in_progress": "in progress", "pending": "pending (current phase)",
                "waiting_gate": "waiting for a gate answer", "failed": "failed"}
BY_DESIGN = {"view.html": "the run viewer: derived, regenerate with view.py", "manifest.json": "the manifest itself", "manifest.sha256": "the seal of manifest.json",
             "run_state.json": "the orchestrator's position", "decisions.csv": "the gate log stays outside the seal",
             "decisions.schema.json": "sidecar of the gate log", "gate_request.json": "an open gate request",
             "deletions.log": "close.py's log"}
TABLE_ROWS = 200
DELIVERABLE_ROWS = 200
MISMATCH_ROWS = 50
TEXT_CAP = 400_000
NODE_H, ROW_H, PAD, BAND = 64, 84, 20, 34
NODE_MIN_W, NODE_PAD_X, GAP_MIN = 120, 22, 64  # a column gap is about half its node width, never under GAP_MIN
CHAR_W, LABEL_CHAR_W = 7.2, 6.6  # px per character of 12 px and 11 px monospace at actual size
NAME_CHARS = 40
FIT_MIN = 0.8  # the graph fits the window only while that keeps it at 0.8 of actual size or more


# ---------------------------------------------------------------- text helpers
def esc(value: Any) -> str:
    """HTML-escape text (quotes included); '//' becomes entities so the bytes never hold a URL."""
    text = "" if value is None else str(value)
    return html.escape(text.replace("\r\n", "\n"), quote=True).replace("//", "&#47;&#47;")


def num(n: Any) -> str:
    """Thousands separators for an integer, else the value as text, else 'not recorded'."""
    if isinstance(n, bool) or n is None:
        return "not recorded"
    if isinstance(n, int):
        return f"{n:,}"
    if isinstance(n, float) and n.is_integer():
        return f"{int(n):,}"
    return str(n)


def clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n - 1] + "…"


def cell(v: Any) -> str:
    """One table cell: NULL and the empty string are marked; floats by repr; dates in ISO form."""
    if v is None:
        return '<span class="null">NULL</span>'
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return esc(repr(v))
    if hasattr(v, "isoformat"):
        return esc(v.isoformat())
    if isinstance(v, str) and v == "":
        return '<span class="null">""</span>'
    return esc(v)


def status_class(status: Any) -> str:
    """Colour class for a check status (the word is always printed beside the colour)."""
    s = str(status or "").upper()
    if s == "PASS":
        return "s-pass"
    if s == "FAIL-EXPLAINED" or s == "INCONCLUSIVE":
        return "s-part"
    if s.startswith("FAIL"):
        return "s-fail"
    return "s-nr"


def badge(status: Any) -> str:
    return f'<span class="badge {status_class(status)}">{esc(status)}</span>'


def para(text: str, cls: str = "") -> str:
    return f'<p{f" class={chr(34)}{cls}{chr(34)}" if cls else ""}>{text}</p>'


def table(headers: Sequence[str], rows: Sequence[Sequence[str]], row_classes: Sequence[str] | None = None,
          tid: str | None = None) -> str:
    """A table from pre-rendered cell HTML."""
    head = "".join(f'<th scope="col">{h}</th>' for h in headers)
    body = []
    for i, r in enumerate(rows):
        cls = (row_classes or [""] * len(rows))[i]
        body.append(f'<tr{f" class={chr(34)}{cls}{chr(34)}" if cls else ""}>'
                    + "".join(f"<td>{c}</td>" for c in r) + "</tr>")
    tid_attr = f' id="{tid}"' if tid else ""
    return f'<div class="tw"><table{tid_attr}><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def kv(pairs: Sequence[tuple[str, str]]) -> str:
    return '<dl class="kv">' + "".join(f"<dt>{esc(k)}</dt><dd>{v}</dd>" for k, v in pairs) + "</dl>"


def plain_list(values: Any) -> str:
    if not values:
        return '<span class="null">none listed</span>'
    if isinstance(values, (list, tuple)):
        return ", ".join(f"<code>{esc(v)}</code>" for v in values)
    return esc(values)


# ---------------------------------------------------------------- the run folder
class Run:
    """The run folder as the page reads it: state, config, cached JSON, CSV through sidecars."""

    def __init__(self, run_dir: Path, out: Path) -> None:
        self.dir = Path(run_dir)
        self.out = out
        self.state = state.read_state(state.state_path(self.dir))
        cfg = c2.read_input_json(self.dir / c2.RUN_CONFIG, c2.RUN_CONFIG)
        self.cfg: dict[str, Any] = cfg if isinstance(cfg, dict) else {}
        self.cur = PHASES.index(self.state.phase)
        self.notes: list[str] = []
        self._json: dict[str, Any] = {}

    # -- paths
    def has(self, rel: str) -> bool:
        return (self.dir / rel).is_file()

    def clean(self, msg: str) -> str:
        """An error message without the absolute run path (the page stays the same wherever the run sits)."""
        for p in {str(self.dir.resolve()), self.dir.resolve().as_posix(), str(self.dir)}:
            msg = msg.replace(p, "RUN")
        return msg

    def href(self, rel: str) -> str | None:
        """Relative link from the page to a run file, or None when no relative path exists (another drive)."""
        try:
            r = os.path.relpath(self.dir.resolve() / rel, self.out.parent)
        except ValueError:
            return None
        return url_quote(r.replace(os.sep, "/"), safe="/._-~")

    def link(self, rel: str, text: str | None = None) -> str:
        h = self.href(rel)
        label = f"<code>{esc(text if text is not None else rel)}</code>"
        return f'<a class="file" href="{esc(h)}">{label}</a>' if h and self.has(rel) else label

    # -- phase wording
    def where(self, phase: str) -> str:
        i = PHASES.index(phase)
        if i > self.cur:
            return "not reached"
        if i == self.cur and self.state.phase_status != "done":
            return "current"
        return "done"

    def absent(self, what: str, phase: str) -> str:
        """What is missing, said plainly: the phase that writes it is not reached, in progress, or the file is gone."""
        w = self.where(phase)
        if w == "not reached":
            return f"{esc(what)}: {phase} not reached."
        if w == "current":
            return f"{esc(what)}: not written yet ({phase} is the current phase)."
        return f"{esc(what)}: not found in the run folder."

    # -- readers
    def text(self, rel: str) -> str | None:
        p = self.dir / rel
        if not p.is_file():
            return None
        return p.read_bytes().decode("utf-8", errors="replace").replace("\r\n", "\n")

    def json(self, rel: str) -> Any:
        if rel not in self._json:
            p = self.dir / rel
            val = None
            if p.is_file():
                try:
                    val = json.loads(p.read_text(encoding="utf-8-sig"))
                except (OSError, ValueError) as exc:
                    self.notes.append(f"{rel}: not readable as JSON ({type(exc).__name__})")
            self._json[rel] = val
        return self._json[rel]

    def mapping(self, rel: str) -> dict[str, Any]:
        v = self.json(rel)
        return v if isinstance(v, dict) else {}

    def csv(self, rel: str, limit: int | None) -> dict[str, Any] | None:
        """Rows of a contract CSV through its sidecar (types honoured); {'raw', 'note'} when that is not possible."""
        p = self.dir / rel
        if not p.is_file():
            return None
        try:
            sc = cc.read_sidecar(p)
        except cc.CsvContractError as exc:
            return {"raw": self.text(rel), "note": f"no usable sidecar ({self.clean(str(exc))}); shown as text"}
        problems = cc.contract_problems(p.read_bytes())
        if problems:
            return {"raw": self.text(rel), "note": "not a contract CSV (" + "; ".join(problems) + "); shown as text"}
        note = None
        if cc.sha256_file(p) != sc.csv_sha256:
            note = "the file's sha256 differs from its sidecar: values shown as read"
        import duckdb

        con = duckdb.connect()
        try:
            cc.run_sql(con, "SET TimeZone = 'UTC'")
            cc.run_sql(con, "SET threads = 1")
            sql = "SELECT * FROM " + cc.read_csv_sql(p, sc.columns) + (f" LIMIT {int(limit)}" if limit else "")
            names, rows = cc.fetch_dicts(con, sql)
        except duckdb.Error as exc:
            return {"raw": self.text(rel), "note": f"not readable through its sidecar ({type(exc).__name__}); "
                                                   "shown as text"}
        finally:
            con.close()
        types = {c.name: c.type for c in sc.columns}
        return {"cols": names, "rows": rows, "total": sc.rows, "note": note, "types": types}

    def rows(self, rel: str) -> list[dict[str, Any]]:
        d = self.csv(rel, None)
        return list(d["rows"]) if d and "rows" in d else []

    # -- blocks
    def pre(self, text: str, cls: str = "sql") -> str:
        extra = ""
        if len(text) > TEXT_CAP:
            extra = para(f"First {TEXT_CAP:,} of {len(text):,} characters; open the file for the rest.", "muted")
            text = text[:TEXT_CAP]
        body = esc(text)
        return f'<pre class="{cls}">\n{body}' + ("" if body.endswith("\n") else "\n") + "</pre>" + extra

    def text_block(self, rel: str, title: str, phase: str, cls: str = "doc", collapsed: bool = False) -> str:
        t = self.text(rel)
        head = f"{esc(title)} {self.link(rel)}"
        if t is None:
            return f"<h4>{head}</h4>" + para(self.absent(rel, phase), "muted")
        if collapsed:
            return f"<details><summary>{head}</summary>{self.pre(t, cls)}</details>"
        return f"<h4>{head}</h4>" + self.pre(t, cls)

    def csv_table(self, d: Mapping[str, Any]) -> str:
        heads = [f'{esc(c)}<br><small>{esc(d["types"].get(c, ""))}</small>' for c in d["cols"]]
        return table(heads, [[cell(r.get(c)) for c in d["cols"]] for r in d["rows"]])

    def csv_block(self, rel: str, title: str, phase: str, limit: int = TABLE_ROWS) -> str:
        d = self.csv(rel, limit)
        head = f"<h4>{esc(title)} {self.link(rel)}</h4>"
        if d is None:
            return head + para(self.absent(rel, phase), "muted")
        if "raw" in d:
            return head + para(esc(d["note"]), "warn") + self.pre(d["raw"] or "", "doc")
        shown, total = len(d["rows"]), int(d["total"])
        if total == 0:
            count = "no rows"
        elif shown < total:
            count = f"first {shown:,} of {total:,} rows"
        else:
            count = f"all {total:,} rows"
        out = head + para(count + (" (row count from the sidecar)" if shown < total else ""), "muted")
        if d.get("note"):
            out += para(esc(d["note"]), "warn")
        return out + (self.csv_table(d) if d["rows"] else "")


# ---------------------------------------------------------------- shared lookups
def manifest_of(run: Run) -> dict[str, Any]:
    return run.mapping("manifest.json")


def jobs_by_name(run: Run) -> dict[str, dict[str, Any]]:
    rep = run.mapping("run_steps_report.json")
    return {str(j.get("name")): j for j in rep.get("jobs") or [] if isinstance(j, dict)}


def p6_rows(run: Run) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for r in run.rows("checks/run_steps_checks.csv"):
        out.setdefault(str(r.get("job")), []).append(r)
    return out


def p6_finals(run: Run) -> dict[str, str]:
    """Fix r15 L6: check_id -> spec 6.4 result of every P6 step row, classified as report.py and the Checks section
    classify them (runctx.classify_checks); {} when they cannot be classified (the raw status is used then)."""
    try:
        rows = c2.steps_check_rows(run.dir)
        dec = c2.load_decisions(run.dir) if run.has("decisions.csv") else []
        return {c["check_id"]: c["final"] for c in c2.classify_checks(rows, run.cfg.get("required_checks"), dec,
                                                                       attempt=run.state.attempt)}
    except (RefusedError, OSError, ValueError, KeyError):
        return {}


def p6_id(r: Mapping[str, Any]) -> str:
    return "p6_" + "_".join(str(r.get(k) or "").replace(":", "_") for k in ("job", "check"))


def p6_state(rows: Sequence[Mapping[str, Any]], finals: Mapping[str, str] | None = None) -> str:
    """A step's P6 rows as one word: FAIL (any FAIL not explained), explained (a FAIL explained at G8), PASS (every
    row PASS or NOT-RUN-at-level: a check this level does not run is not a gap), partial (a required check that did
    not run), not run (no row). Fix r15 L6: by the classify_checks result when there is one."""
    if not rows:
        return "not run"
    res = [(finals or {}).get(p6_id(r)) or {"PASS": "PASS", "FAIL": "FAIL-unexplained"}.get(
        str(r.get("status")), "NOT_RUN") for r in rows]
    if any(x == "FAIL-unexplained" and str(r.get("status")) == "FAIL" for x, r in zip(res, rows)):
        return "FAIL"
    if any(x == "FAIL-explained" for x in res):
        return "explained"
    if all(x in ("PASS", "NOT-RUN-at-level") for x in res):
        return "PASS"
    return "partial"


NODE_CLASS = {"PASS": "pass", "FAIL": "fail", "not run": "notrun", "partial": "partial", "explained": "partial"}
IN_OUT = re.compile(r"\s*(\d+)\s*->\s*(\d+)")


def rows_in_out(rows: Sequence[Mapping[str, Any]], man_step: Mapping[str, Any], job: Mapping[str, Any]
                ) -> tuple[Any, Any]:
    """(rows in, rows out): the rows_in_out check detail first, then manifest.json, then the job's rows."""
    for r in rows:
        if str(r.get("check")) == "rows_in_out":
            m = IN_OUT.match(str(r.get("detail") or ""))
            if m:
                return int(m.group(1)), int(m.group(2))
    rin = man_step.get("rows_in")
    rout = job.get("rows") if job.get("rows") is not None else man_step.get("rows_out")
    return rin, rout


def in_out_text(rin: Any, rout: Any) -> str:
    if rin is None and rout is None:
        return "not run"
    return f"{num(rin) if rin is not None else 'rows in not recorded'} → {num(rout)}"


def job_line(job: Mapping[str, Any]) -> str:
    if not job:
        return '<span class="null">no job recorded (run_steps_report.json)</span>'
    return esc(f"dry-run {num(job.get('dry_run_bytes'))} bytes; billed {num(job.get('bytes_billed'))} bytes; "
               f"{num(job.get('rows'))} rows; stream route {job.get('stream_route') or 'not recorded'}"
               + (f"; label {job.get('label')}" if job.get("label") else ""))


def checks_table(rows: Sequence[Mapping[str, Any]]) -> str:
    if not rows:
        return para("no P6 check rows for this job (checks/run_steps_checks.csv)", "muted")
    return table(["check", "status", "detail"],
                 [[esc(r.get("check")), badge(r.get("status")), esc(r.get("detail"))] for r in rows])


# ---------------------------------------------------------------- 1. header
def seal_state(run: Run) -> str | None:
    """'matches', 'changed', or None when there is no manifest.sha256."""
    seal = run.text("manifest.sha256")
    if seal is None:
        return None
    parts = seal.split()
    now = cc.sha256_file(run.dir / "manifest.json") if run.has("manifest.json") else None
    return "matches" if parts and now == parts[0] else "changed"


def verdict_stale(run: Run) -> list[str]:
    """Fix r15 M1: why report_status.json's verdict is not the run's current one ([] when it is): the run stepped
    back before P11 (state.py allows it in the same attempt), the seal no longer matches manifest.json, or the report
    is from another attempt."""
    rs = run.mapping("report_status.json")
    if rs.get("verdict") is None:
        return []
    why = []
    if PHASES.index(run.state.phase) < PHASES.index("P11"):
        why.append(f"the run is at {run.state.phase}, before P11: the report was written on an earlier pass")
    seal = seal_state(run)
    if seal == "changed":
        why.append("manifest.json has changed since the seal (manifest.sha256) the report wrote")
    elif seal is None:
        why.append("manifest.sha256 is missing: the report's seal is gone")
    if rs.get("attempt") is not None and str(rs.get("attempt")) != str(run.state.attempt):
        why.append(f"report_status.json is from attempt {rs.get('attempt')}; run_state.json is at attempt "
                   f"{run.state.attempt}")
    return why


def header(run: Run) -> str:
    st, cfg = run.state, run.cfg
    rs = run.mapping("report_status.json")
    man = manifest_of(run)
    if rs.get("verdict") is not None:
        labels = " ".join(str(x) for x in rs.get("labels") or [])
        stale_why = verdict_stale(run)
        lead = ('<span class="stale-tag">From an earlier pass, not current:</span> ' if stale_why else "")
        verdict = (f'<p class="verdict{" stale" if stale_why else ""}">{lead}<span class="muted">Verdict, quoted from '
                   f'report_status.json:</span> <strong id="verdict">{esc(rs["verdict"])}</strong>'
                   + (f" {esc(labels)}" if labels else "")
                   + (f'<br><span class="stale-why">{esc("; ".join(stale_why))}.</span>' if stale_why else "")
                   + "</p>")
    else:
        why = {"not reached": "P11 not reached", "current": "P11 is the current phase"}.get(
            run.where("P11"), "report_status.json not found")
        verdict = f'<p class="verdict none"><strong id="verdict">No report yet</strong> ({esc(why)}).</p>'
    phase = f"{st.phase} {PHASE_NAMES.get(st.phase, '')}: {STATUS_WORDS.get(st.phase_status, st.phase_status)}"
    if st.blocked_reason:
        phase += f"; blocked: {st.blocked_reason} ({st.blocked_utc})"
    pin = man.get("pin") if isinstance(man.get("pin"), dict) else {}
    pp = run.mapping("15_pin_plan.json")
    if pin:
        pin_txt = f"{pin.get('pin_status')} (route {pin.get('route')}, as_of_utc {pin.get('as_of_utc')})"
        reasons = list(pin.get("provisional_reasons") or []) + [f"unpinnable: {u}" for u in pin.get("unpinnable") or []]
        if reasons:
            pin_txt += "; reasons: " + "; ".join(map(str, reasons))
    elif pp:
        pin_txt = f"{pp.get('pin_status')} planned (route {pp.get('route')}; as_of fixed at P6)"
    else:
        pin_txt = "no pin plan yet (" + ("P3 not reached" if run.where("P3") == "not reached" else
                                         "15_pin_plan.json not found") + ")"
    ind = rs.get("independence") or (man.get("independence") or {}).get("level") or "not recorded yet"
    ref = rs.get("reference")
    if ref is None:
        rcref = cfg.get("reference")
        ref = (f"{rcref.get('type')} (run_config.json)" if isinstance(rcref, dict) else
               "no reference block in run_config.json")
    b = man.get("budget") if isinstance(man.get("budget"), dict) else {}
    rep = run.mapping("run_steps_report.json")
    if b.get("spent_bytes") is not None:
        budget = f"spent {num(b.get('spent_bytes'))} / budget {num(b.get('budget_bytes'))} bytes (manifest.json budget)"
        if b.get("held_bytes"):
            budget += f"; held {num(b.get('held_bytes'))} bytes for unreconciled jobs"
    elif rep.get("run_spent_bytes") is not None:
        budget = (f"spent {num(rep.get('run_spent_bytes'))} / budget {num(rep.get('run_budget_bytes'))} bytes "
                  "(run_steps_report.json)")
    else:
        cap = (cfg.get("caps") or {}).get("warehouse_bytes_per_run")
        budget = f"spent: not recorded yet; budget {num(cap)} bytes (run_config.json caps)"
    as_of = pin.get("as_of_utc") or rep.get("as_of_utc") or "not fixed yet (P6 fixes it)"
    pairs = [("Run id", esc(cfg.get("run_id") or st.run_id)), ("Attempt", esc(st.attempt)),
             ("Plugin version", esc(cfg.get("plugin_version") or "not stamped in run_config.json")),
             ("Level", esc(st.level)), ("Author mode", esc(cfg.get("author_mode"))), ("Phase", esc(phase)),
             ("Independence", esc(ind)), ("Pin", esc(pin_txt)), ("Reference", esc(ref)), ("Budget", esc(budget)),
             ("as_of_utc", esc(as_of))]
    if rs:
        pairs += [("Comparison", esc(rs.get("comparison"))), ("Sampling", esc(rs.get("sampling"))),
                  ("Unexplained", esc(", ".join(map(str, rs.get("unexplained") or [])) or "none")),
                  ("G16", esc(("needed: " + "; ".join(map(str, rs.get("g16_reasons") or [])))
                              if rs.get("g16_needed") else "not needed")),
                  ("Reported", esc(f"{rs.get('reported_utc')} (attempt {rs.get('attempt')})"))]
    stale = ""
    notes = "".join(para(esc(n), "warn") for n in run.notes)
    return (f'<section class="sec" id="sec-header"><h1>query-trail run {esc(cfg.get("run_id") or st.run_id)}</h1>'
            + verdict + stale + kv(pairs) + notes + "</section>")


# ---------------------------------------------------------------- 2. gates and phases
def gate_requests(run: Run) -> list[dict[str, Any]]:
    out = []
    if run.has(c2.GATE_REQUEST):
        req = run.mapping(c2.GATE_REQUEST)
        out.append({"state": "open", "rel": c2.GATE_REQUEST, "req": req, "extra": "waiting for Max's answer"})
    gdir = run.dir / "gates"
    if gdir.is_dir():
        for f in sorted(gdir.glob("*.json")):
            rel = f"gates/{f.name}"
            m = re.fullmatch(r"(\d+)_(.+)_request\.json", f.name)
            if f.name.startswith("stale_"):
                req = run.mapping(rel)
                out.append({"state": "stale", "rel": rel, "req": req,
                            "extra": f"archived {req.get('archived_utc')}: {req.get('archived_by')}"})
            elif m:
                ans = f"gates/{m.group(1)}_{m.group(2)}_answer.json"
                out.append({"state": "answered", "rel": rel, "req": run.mapping(rel), "answer": ans,
                            "extra": f"decisions.csv row {int(m.group(1))}"})
    return out


def phases(run: Run) -> str:
    dec = run.csv("decisions.csv", None)
    rows = list(dec["rows"]) if dec and "rows" in dec else []
    superseded: dict[int, int] = {}
    for n, r in enumerate(rows, 1):
        try:
            superseded[int(r.get("supersedes"))] = n
        except (TypeError, ValueError):
            pass
    by_phase: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for n, r in enumerate(rows, 1):
        by_phase.setdefault(str(r.get("phase")) if str(r.get("phase")) in PHASES else "other", []).append((n, r))
    reqs: dict[str, list[dict[str, Any]]] = {}
    for q in gate_requests(run):
        ph = str(q["req"].get("phase"))
        reqs.setdefault(ph if ph in PHASES else "other", []).append(q)
    strip = []
    for ph in PHASES:
        w = run.where(ph)
        word = {"done": "done", "not reached": "not reached"}.get(
            w, STATUS_WORDS.get(run.state.phase_status, run.state.phase_status))
        if w != "not reached" and ph == run.state.phase and run.state.phase_status == "done":
            word = "done"
        strip.append(f'<li class="ph-{w.replace(" ", "-")}"><a href="#phase-{ph}"><b>{ph}</b> '
                     f'<span>{esc(PHASE_NAMES[ph])}</span><em>{esc(word)}</em></a></li>')
    out = ['<section class="sec" id="sec-gates"><h2>Gates and phases</h2>',
           para("From run_state.json: phases before the current one are done; the run resumes at the current one. "
                "Under each phase: the files it leaves (found or not), its gate rows from decisions.csv (Max's "
                "answer verbatim; a superseded row is struck through) and its gate requests.", "muted"),
           f'<ol class="strip">{"".join(strip)}</ol>']
    if dec and "raw" in dec:
        out.append(para("decisions.csv: " + esc(dec["note"]), "warn") + run.pre(dec["raw"] or "", "doc"))
    elif dec is None:
        out.append(para("decisions.csv: no gate decision recorded yet.", "muted"))
    per_run = state.PER_RUN_GATES
    for ph in list(PHASES) + ["other"]:
        if ph == "other" and not (by_phase.get("other") or reqs.get("other")):
            continue
        w = run.where(ph) if ph in PHASES else "done"
        body = []
        if ph in PHASES:
            files = [f'{run.link(f)} <span class="{"ok" if run.has(f) else "null"}">'
                     f'{"found" if run.has(f) else "not found"}</span>' for f in PHASE_FILES[ph]]
            body.append(para("Files: " + "; ".join(files)))
            if per_run.get(ph):
                try:  # fix r15 M3: the round-14 rule (closing outcome, no check_id, not superseded)
                    missing = set(state.missing_gates(run.dir, ph, run.state.attempt))
                    body.append(para("Per-run gates (this attempt): " + "; ".join(
                        f'{g} {"no closing row" if g in missing else "closed"}' for g in per_run[ph])
                        + esc(" (a closing row: " + "; ".join(f"{g} {' or '.join(sorted(state.CLOSING_OUTCOMES[g]))}"
                                                               for g in per_run[ph]) + ", no check_id, not superseded)")))
                except (RefusedError, OSError, ValueError) as exc:
                    body.append(para(esc(f"Per-run gates: not readable ({type(exc).__name__})"), "warn"))
        drows = by_phase.get(ph, [])
        if drows:
            cls, cells = [], []
            for n, r in drows:
                by = superseded.get(n)
                cls.append("superseded" if by else "")
                cells.append([esc(n), f"<b>{esc(r.get('gate'))}</b>", esc(r.get("outcome")), esc(r.get("decided_by")),
                              esc(r.get("time_utc")), f'<span class="answer">{esc(r.get("answer_text"))}</span>',
                              cell(r.get("check_id")), cell(r.get("supersedes")), esc(r.get("attempt")),
                              esc(f"superseded by row {by}") if by else ""])
            body.append(table(["row", "gate", "outcome", "decided_by", "time_utc", "answer_text (verbatim)",
                               "check_id", "supersedes", "attempt", "note"], cells, cls))
        for q in reqs.get(ph, []):
            req = q["req"]
            ans = f" answer {run.link(q['answer'])}" if q.get("answer") else ""
            body.append(para(f'<span class="badge g-{q["state"]}">{esc(q["state"])}</span> '
                             f'{esc(req.get("gate"))} request {run.link(q["rel"])}{ans}: {esc(req.get("reason"))} '
                             f'({esc(req.get("script"))}, {esc(req.get("created_utc"))}); {esc(q["extra"])}'))
        title = f"{ph} {PHASE_NAMES.get(ph, 'Other gates')}" if ph in PHASES else "Other gate rows"
        out.append(f'<div class="phase ph-{w.replace(" ", "-")}" id="phase-{ph}"><h3>{esc(title)} '
                   f'<span class="badge b-{w.replace(" ", "-")}">{esc(w)}</span></h3>{"".join(body)}</div>')
    out.append("</section>")
    return "".join(out)


# ---------------------------------------------------------------- 3. step graph
def step_inputs(s: Mapping[str, Any]) -> list[tuple[str, str]]:
    ins = s.get("inputs")
    if isinstance(ins, list) and ins:
        return [(str(i.get("kind")), str(i.get("ref"))) for i in ins if isinstance(i, Mapping)]
    return ([("prior_step", str(d)) for d in s.get("deps") or []]
            + [("source_table", str(t)) for t in s.get("source_tables") or []])


def step_key(s: Mapping[str, Any], i: int) -> str:
    n = s.get("step")
    return f"{int(n):02d}" if isinstance(n, int) and not isinstance(n, bool) else f"{i + 1:02d}"


class Graph:
    """plan.json as a layered DAG: source tables in column 0, each step one column right of its deepest input."""

    def __init__(self, plan: Mapping[str, Any]) -> None:
        raw = [s for s in plan.get("steps") or [] if isinstance(s, Mapping) and s.get("name")]
        self.steps = sorted(raw, key=lambda s: (s.get("step") if isinstance(s.get("step"), int) else 10 ** 6,
                                                str(s.get("name"))))
        self.key = {str(s["name"]): step_key(s, i) for i, s in enumerate(self.steps)}
        self.inputs = {str(s["name"]): step_inputs(s) for s in self.steps}
        self.sources: list[str] = []
        for s in self.steps:
            for kind, ref in self.inputs[str(s["name"])]:
                if kind != "prior_step" and ref not in self.sources:
                    self.sources.append(ref)
        self.col: dict[str, int] = {f"src:{r}": 0 for r in self.sources}
        for s in self.steps:
            self._col(str(s["name"]), set())
        final = next((str(s["name"]) for s in self.steps if s.get("step") == plan.get("final_step")), None)
        self.final = final or (str(self.steps[-1]["name"]) if self.steps else None)
        others = [self.col[str(s["name"])] for s in self.steps if str(s["name"]) != self.final]
        if self.final and others and self.col[self.final] <= max(others):
            self.col[self.final] = max(others) + 1  # the final step sits alone on the right
        self.row: dict[str, int] = {}
        for i, r in enumerate(self.sources):
            self.row[f"src:{r}"] = i
        for c in range(1, (max(self.col.values()) if self.col else 0) + 1):
            here = [str(s["name"]) for s in self.steps if self.col[str(s["name"])] == c]
            want = {n: self._mean(n) for n in here}
            last = -1
            for n in sorted(here, key=lambda n: (want[n], self.key[n])):
                last = max(int(want[n] + 0.5), last + 1)
                self.row[n] = last

    def node_of(self, kind: str, ref: str) -> str | None:
        if kind == "prior_step":
            return ref if ref in self.key else None
        return f"src:{ref}"

    def _col(self, name: str, stack: set[str]) -> int:
        if name in self.col:
            return self.col[name]
        if name in stack:  # a cycle cannot come from split.py; guard anyway
            return 1
        stack.add(name)
        deepest = 0
        for kind, ref in self.inputs[name]:
            if kind == "prior_step" and ref in self.key:
                deepest = max(deepest, self._col(ref, stack))
        self.col[name] = deepest + 1
        return self.col[name]

    def _mean(self, name: str) -> float:
        """Inputs' rows, weighted by how many columns the edge spans: a long edge then runs straight along its own
        row instead of crossing the nodes between (the short edge bends instead)."""
        pairs = [(self.row[n], self.col[name] - self.col[n]) for n in
                 (self.node_of(k, r) for k, r in self.inputs[name]) if n in self.row]
        total = sum(w for _, w in pairs)
        return sum(r * w for r, w in pairs) / total if total else 0.0



def svg_text(x: int, y: int, cls: str, text: str) -> str:
    return f'<text x="{x}" y="{y}" class="{cls}">{esc(text)}</text>'


def graph_section(run: Run) -> str:
    out = ['<section class="sec" id="sec-graph"><h2>Step graph</h2>']
    plan = run.json("plan.json")
    if not isinstance(plan, dict) or not isinstance(plan.get("steps"), list) or not plan["steps"]:
        out.append(para(run.absent("plan.json (the step plan)", "P5") + " The graph is drawn from it.", "muted"))
        out.append("</section>")
        return "".join(out)
    g = Graph(plan)
    man = manifest_of(run)
    man_steps = {str(s.get("name")): s for s in man.get("steps") or [] if isinstance(s, dict)}
    jobs = jobs_by_name(run)
    p6 = p6_rows(run)
    sentences = run.mapping("step_sentences.json")
    finals = p6_finals(run)
    info: dict[str, dict[str, Any]] = {}
    for s in g.steps:
        name = str(s["name"])
        job = jobs.get(name, {})
        label = str(job.get("label") or f"step:{g.key[name]}")
        rows = p6.get(label, [])
        rin, rout = rows_in_out(rows, man_steps.get(name, {}), job)
        info[name] = {"job": job, "rows": rows, "in": rin, "out": rout, "p6": p6_state(rows, finals)}
    svg, details = draw(run, g, info), step_details(run, g, info, man_steps, sentences)
    legend = ('<p class="legend">Node colour: P6 checks of the step: <span class="badge s-pass">PASS</span> all '
              'passed (or not run at this level), <span class="badge s-fail">FAIL</span> a failure not explained, '
              '<span class="badge s-part">explained / partial</span> a failure explained at G8, or a required check '
              'that did not run, <span class="badge s-nr">not run</span> no check row. Edge label: rows out of the '
              'upstream step. Click a step, or Tab to it and press Enter.</p>')
    out.append(legend)
    out.append('<div class="graph-layout"><div><p class="graph-tools" role="group" aria-label="Graph size">'
               '<button type="button" data-mode="fit" aria-pressed="false">Fit to window</button> '
               '<button type="button" data-mode="actual" aria-pressed="false">Actual size</button> '
               f'<span class="muted">Fit is the default while the graph stays at {int(FIT_MIN * 100)}% of its size '
               'or more; otherwise it opens at actual size and scrolls inside its box.</span></p>'
               f'<div class="graph-box" data-fit-min="{FIT_MIN}">' + svg + "</div></div>"
               '<aside class="panel" id="detail-panel" aria-label="Step detail" aria-live="polite">'
               '<p id="detail-hint" class="muted">Select a step in the graph or in the table below to read its '
               "sentence, SQL, rows and checks.</p>" + details + "</aside></div>")
    out.append(steps_table(run, g, info, man_steps))
    out.append("</section>")
    return "".join(out)


def node_width(lines: Sequence[str], char_w: float = CHAR_W) -> int:
    """Width that holds the longest line at actual size (12 px monospace) plus the padding."""
    return max(NODE_MIN_W, int(max(len(t) for t in lines) * char_w + 0.999) + NODE_PAD_X)


def draw(run: Run, g: Graph, info: Mapping[str, Mapping[str, Any]]) -> str:
    """The SVG: each column as wide as its longest node label, gaps of about half a node width, whole B and the
    deliverable stacked under the final step so they add height, not width."""
    maxcol = max(g.col.values())
    maxrow = max(g.row.values()) if g.row else 0
    lines: dict[str, list[str]] = {}
    for ref in g.sources:
        lines[f"src:{ref}"] = ["source table", clip(ref.split(".")[-1], NAME_CHARS)]
    for s in g.steps:
        name, k = str(s["name"]), g.key[str(s["name"])]
        label = name if name.startswith(k) else f"{k} {name}"
        it = info[name]
        lines[name] = [clip(label, NAME_CHARS), f"{s.get('verb') or s.get('kind') or ''} · P6 {it['p6']}",
                       in_out_text(it["in"], it["out"])]
    bman = next((d for d in manifest_of(run).get("deliverables") or [] if isinstance(d, dict)
                 and d.get("side") == "B"), {})
    whole = (run.has("B.sql") or bool(bman)) and g.final is not None
    colw = [0] * (maxcol + 1)
    for node, c in g.col.items():
        colw[c] = max(colw[c], node_width(lines[node]))
    if whole:
        bjob = jobs_by_name(run).get("B_whole", {})
        brows = p6_rows(run).get(str(bjob.get("label") or "whole:b"), [])
        bst = p6_state(brows, p6_finals(run))
        drows = bjob.get("rows") if bjob.get("rows") is not None else bman.get("rows_out")
        drel = deliverable_rel(run)
        p7 = p7_word(run)
        blines = ["B (whole query)", f"whole B · P6 {bst}",
                  f"→ {num(drows)} rows" if drows is not None else "not run"]
        dlines = ["deliverable", clip(drel, NAME_CHARS), f"{num(drows)} rows" if drows is not None else "not written"]
        fc = g.col[g.final]
        colw[fc] = max(colw[fc], node_width(blines + dlines), node_width([p7], LABEL_CHAR_W) + 30)
    colx, x = [], PAD
    for c in range(maxcol + 1):
        colx.append(x)
        x += colw[c] + max(GAP_MIN, colw[c] // 2)
    width = colx[-1] + colw[-1] + PAD

    def xy(node: str) -> tuple[int, int]:
        return colx[g.col[node]], PAD + g.row[node] * ROW_H

    def wid(node: str) -> int:
        return colw[g.col[node]]

    def box(x0: int, y0: int, w: int, texts: Sequence[str], classes: Sequence[str] = ("t1", "t2", "t3"),
            first_y: int = 18) -> str:
        out = f'<rect x="{x0}" y="{y0}" width="{w}" height="{NODE_H}" rx="6"></rect>'
        for i, (cls, t) in enumerate(zip(classes, texts)):
            out += svg_text(x0 + 10, y0 + first_y + 18 * i, cls, t)
        return out

    parts: list[str] = []
    edges: list[str] = []
    for s in g.steps:
        name = str(s["name"])
        x2, y2 = xy(name)
        ins = [(k, r, g.node_of(k, r)) for k, r in g.inputs[name]]
        ins = [t for t in ins if t[2] is not None]
        for i, (kind, ref, node) in enumerate(ins):
            x1, y1 = xy(node)
            x1, y1 = x1 + wid(node), y1 + NODE_H // 2
            py = y2 + (NODE_H * (i + 1)) // (len(ins) + 1)
            cx = (x1 + x2) // 2
            d = f"M{x1},{y1} C{cx},{y1} {cx},{py} {x2 - 2},{py}"
            if kind == "prior_step":
                rout = info[ref]["out"]
                lab = num(rout) if rout is not None else "not run"
                edges.append(f'<path class="edge step-edge" d="{d}" marker-end="url(#arrow)"><title>'
                             f'{esc(ref)} → {esc(name)}: {esc(lab)} rows</title></path>'
                             + svg_text(x1 + 6, y1 - 6, "elabel", lab))
            else:
                edges.append(f'<path class="edge src-edge" d="{d}" marker-end="url(#arrow)"><title>{esc(ref)} '
                             f'→ {esc(name)}</title></path>')
    for ref in g.sources:
        node = f"src:{ref}"
        x, y = xy(node)
        parts.append(f'<g class="node source" data-kind="source"><title>source table {esc(ref)}</title>'
                     + box(x, y, wid(node), lines[node], ("t2", "t1"), 24) + "</g>")
    for s in g.steps:
        name = str(s["name"])
        x, y = xy(name)
        k = g.key[name]
        it = info[name]
        aria = f"Step {lines[name][0]}, {s.get('verb')}, rows {lines[name][2]}, P6 checks {it['p6']}"
        parts.append(f'<a href="#step-{k}" class="node {NODE_CLASS[it["p6"]]}" data-kind="step" data-step="{k}" '
                     f'aria-label="{esc(aria)}"><title>{esc(name)}</title>' + box(x, y, wid(name), lines[name])
                     + "</a>")
    height = PAD + (maxrow + 1) * ROW_H
    if whole:
        fx, fy = xy(g.final)
        fw = wid(g.final)
        dy = fy + NODE_H + BAND
        by = dy + NODE_H + BAND // 2
        parts.append(f'<a href="#sec-whole" class="node deliv" data-kind="deliverable" '
                     f'aria-label="Deliverable {esc(drel)}"><title>{esc(drel)}</title>' + box(fx, dy, fw, dlines)
                     + "</a>")
        parts.append(f'<a href="#step-B" class="node bnode {NODE_CLASS[bst]}" data-kind="whole" '
                     f'aria-label="{esc(f"Whole query B, P6 checks {bst}")}"><title>B.sql run whole</title>'
                     + box(fx, by, fw, blines) + "</a>")
        lx = fx + fw - 16
        edges.append(f'<path class="edge p7-edge" d="M{lx},{fy + NODE_H} L{lx},{dy - 2}" marker-end="url(#arrow)">'
                     f'<title>{esc(p7)}</title></path>' + svg_text(fx + 4, fy + NODE_H + BAND // 2 + 4, "elabel", p7))
        edges.append(f'<path class="edge b-edge" d="M{fx + fw // 2},{by} L{fx + fw // 2},{dy + NODE_H + 2}" '
                     f'marker-end="url(#arrow)"><title>B → deliverable</title></path>')
        height = max(height, by + NODE_H + PAD)
    defs = ('<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
            'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z"></path></marker></defs>')
    return (f'<svg class="dag" width="100%" viewBox="0 0 {width} {height}" preserveAspectRatio="xMinYMin meet" '
            f'data-width="{width}" role="group" '
            f'aria-label="Step graph: {len(g.steps)} steps, {len(g.sources)} source tables">{defs}'
            + "".join(edges) + "".join(parts) + "</svg>")


def p7_word(run: Run) -> str:
    rs = run.mapping("checks/reconcile_status.json")
    comps = [c for c in rs.get("comparisons") or [] if isinstance(c, dict)]
    if comps:
        return "; ".join(f"P7 {c.get('comparison')}: {c.get('status')}" for c in comps)
    return {"not reached": "P7 not reached", "current": "P7 in progress"}.get(run.where("P7"), "P7: no result file")


def deliverable_rel(run: Run) -> str:
    for d in manifest_of(run).get("deliverables") or []:
        if isinstance(d, dict) and d.get("side") == "B" and isinstance(d.get("csv"), dict) and d["csv"].get("path"):
            if run.has(str(d["csv"]["path"])):
                return str(d["csv"]["path"])
    return "outputs/B_whole.csv"


def step_details(run: Run, g: Graph, info: Mapping[str, Mapping[str, Any]], man_steps: Mapping[str, Any],
                 sentences: Mapping[str, Any]) -> str:
    out = []
    for s in g.steps:
        name = str(s["name"])
        k = g.key[name]
        it = info[name]
        ms = man_steps.get(name, {})
        sent = sentences.get(name)
        if sent is None and ms.get("one_sentence"):
            sent_html = f'{esc(ms["one_sentence"])} <span class="muted">(manifest.json one_sentence)</span>'
        elif sent is None:
            sent_html = '<span class="null">' + run.absent("step_sentences.json", "P5") + "</span>"
        else:
            sent_html = esc(sent)
        ins = []
        for kind, ref in g.inputs[name]:
            if kind == "prior_step" and ref in g.key:
                ins.append(f'<li>step <a class="pick" href="#step-{g.key[ref]}">{esc(ref)}</a></li>')
            else:
                ins.append(f"<li>{esc(kind.replace('_', ' '))} <code>{esc(ref)}</code></li>")
        cols = ms.get("columns") if isinstance(ms.get("columns"), list) and ms.get("columns") else s.get("columns")
        if isinstance(cols, list) and cols and isinstance(cols[0], dict):
            col_html = ", ".join(f"<code>{esc(c.get('name'))}</code> {esc(c.get('type'))}" for c in cols)
        else:
            col_html = plain_list(cols)
        basis = "; ".join(str(x) for x in (ms.get("row_delta_reason"), ms.get("rows_in_basis")) if x)
        pairs = [("kind / verb", esc(f"{s.get('kind')} / {s.get('verb')}")),
                 ("inputs", f"<ul>{''.join(ins)}</ul>" if ins else '<span class="null">none listed</span>'),
                 ("columns", col_html), ("grain", plain_list(s.get("grain"))),
                 ("pushed filters", plain_list(s.get("pushed_filters"))),
                 ("note", esc(s.get("note")) if s.get("note") else '<span class="null">none</span>'),
                 ("rows in → out", esc(in_out_text(it["in"], it["out"]) + (f" ({basis})" if basis else ""))),
                 ("job", job_line(it["job"]))]
        if ms:
            pairs.append(("level, replay, status", esc(f"{ms.get('requested_level')} → "
                                                       f"{ms.get('effective_level')}; replay "
                                                       f"{ms.get('replay_status', 'NONE')}; {ms.get('status')}")))
        sql_rel = str(s.get("file") or ms.get("sql_path") or "")
        ex_rel = str(ms.get("executed_sql_path") or f"executed/{k}.run.sql")
        sql = run.text(sql_rel) if sql_rel else None
        ex = run.text(ex_rel)
        body = [f'<h3 id="h-step-{k}">{esc(name)}</h3>',
                f'<p class="sentence"><span class="muted">G11 sentence:</span> {sent_html}</p>', kv(pairs),
                f"<h4>P6 checks ({esc(it['p6'])})</h4>", checks_table(it["rows"]),
                f"<h4>Step SQL {run.link(sql_rel) if sql_rel else ''}</h4>",
                run.pre(sql) if sql is not None else para(run.absent(sql_rel or "step SQL", "P5"), "muted"),
                (f"<details><summary>Executed SQL, with its upstream steps and the pin {run.link(ex_rel)}</summary>"
                 + run.pre(ex) + "</details>") if ex is not None else para(run.absent(ex_rel, "P6"), "muted")]
        out.append(f'<section class="detail" id="step-{k}" aria-labelledby="h-step-{k}">{"".join(body)}</section>')
    # whole B
    jobs = jobs_by_name(run)
    bjob = jobs.get("B_whole", {})
    brows = p6_rows(run).get(str(bjob.get("label") or "whole:b"), [])
    out.append('<section class="detail" id="step-B" aria-labelledby="h-step-B"><h3 id="h-step-B">B (whole query)</h3>'
               + para("B.sql as written, run whole; its result is the deliverable. P7 compares it with the chain of "
                      "steps (B vs chain(B)).")
               + kv([("job", job_line(bjob)), ("P7", esc(p7_word(run)))])
               + f"<h4>P6 checks ({esc(p6_state(brows, p6_finals(run)))})</h4>" + checks_table(brows)
               + para('<a href="#sec-whole">B.sql, the deliverable and the reconciliation</a>') + "</section>")
    return "".join(out)


def steps_table(run: Run, g: Graph, info: Mapping[str, Mapping[str, Any]], man_steps: Mapping[str, Any]) -> str:
    rows = []
    for s in g.steps:
        name = str(s["name"])
        it, ms = info[name], man_steps.get(name, {})
        why = ms.get("row_delta_reason")
        rows.append([f'<a class="pick" href="#step-{g.key[name]}">{esc(name)}</a>', esc(s.get("verb")),
                     esc(s.get("kind")), esc(in_out_text(it["in"], it["out"]) + (f" ({why})" if why else "")),
                     badge(it["p6"]),
                     esc(f"{ms.get('requested_level')} → {ms.get('effective_level')}") if ms else "",
                     esc(ms.get("replay_status", "")), esc(ms.get("status", "not run"))])
    return ("<h3>Steps as a table</h3>"
            + table(["step", "verb", "kind", "rows in → out (reason)", "P6 checks", "level (requested → "
                     "effective)", "replay", "status"], rows))


# ---------------------------------------------------------------- 4. whole query B
def whole_section(run: Run) -> str:
    out = ['<section class="sec" id="sec-whole"><h2>Whole query B</h2>']
    out.append(run.text_block("B.sql", "B as written", "P4", "sql"))
    out.append(run.text_block("executed/B.run.sql", "B as run (pin applied)", "P6", "sql", collapsed=True)
               if run.has("executed/B.run.sql") else para(run.absent("executed/B.run.sql", "P6"), "muted"))
    oc = run.json("03_output_contract.json")
    out.append(f"<h4>Output contract {run.link('03_output_contract.json')}</h4>")
    if isinstance(oc, dict):
        cols = [c for c in oc.get("columns") or [] if isinstance(c, dict)]
        if cols:
            keys = list(dict.fromkeys(k for c in cols for k in c))
            out.append(table([esc(k) for k in keys], [[cell(c.get(k)) for k in keys] for c in cols]))
        rest = [(k, f"<code>{esc(json.dumps(v, ensure_ascii=False))}</code>") for k, v in oc.items() if k != "columns"]
        if rest:
            out.append(kv(rest))
    else:
        out.append(para(run.absent("03_output_contract.json", "P2"), "muted"))
    rel = deliverable_rel(run)
    out.append(run.csv_block(rel, "Deliverable", "P6", DELIVERABLE_ROWS))
    whole = [r for job, rows in p6_rows(run).items() if job.startswith("whole:") for r in rows]
    out.append("<h4>Whole-B checks (P6)</h4>" + (checks_table(whole) if whole else
                                                  para(run.absent("checks/run_steps_checks.csv", "P6"), "muted")))
    out.append("<h3>Reconciliation (P7)</h3>")
    rs = run.json("checks/reconcile_status.json")
    if isinstance(rs, dict):
        comps = [c for c in rs.get("comparisons") or [] if isinstance(c, dict)]
        out.append(table(["comparison", "left", "right", "status", "method", "classes", "not run at this level",
                          "mismatch rows"],
                         [[esc(c.get("comparison")), esc(c.get("left")), esc(c.get("right")), badge(c.get("status")),
                           esc(c.get("method")), esc(", ".join(map(str, c.get("classes") or [])) or "none"),
                           esc("; ".join(map(str, c.get("not_run_at_level") or [])) or "none"),
                           esc(c.get("n_mismatch_rows"))] for c in comps]))
        extra = [("checked_utc", esc(rs.get("checked_utc"))), ("attempt", esc(rs.get("attempt"))),
                 ("route", esc(rs.get("route"))),
                 ("adequacy counters not run", esc(", ".join(map(str, rs.get("adequacy_counters_not_run") or []))
                                                   or "none"))]
        out.append(kv(extra) + para(run.link("checks/reconcile_status.json")))
    else:
        out.append(para(run.absent("checks/reconcile_status.json", "P7"), "muted"))
    cdir = run.dir / "checks"
    for f in sorted(cdir.glob("reconcile_*.csv")) if cdir.is_dir() else []:
        out.append(run.csv_block(f"checks/{f.name}", "Comparison checks", "P7"))
    out.append(run.csv_block("checks/mismatch_rows.csv", "Mismatch rows", "P7", MISMATCH_ROWS))
    out.append("</section>")
    return "".join(out)


# ---------------------------------------------------------------- 5. checks
def checks_section(run: Run) -> str:
    out = ['<section class="sec" id="sec-checks"><h2>Checks</h2>']
    rows: list[dict[str, Any]] = []
    try:
        checks = c2.load_checks(run.dir)
        known = {str(c.get("check_id")) for c in checks}
        checks += [c for c in c2.steps_check_rows(run.dir) if c["check_id"] not in known]
        dec = c2.load_decisions(run.dir) if run.has("decisions.csv") else []
        plain = [c for c in checks if c.get("family") not in ("reference", "comparison")]
        classified = {c["check_id"]: c for c in c2.classify_checks(plain, run.cfg.get("required_checks"), dec,
                                                                  attempt=run.state.attempt)}
        for c in checks:
            cid = str(c.get("check_id"))
            k = classified.get(cid)
            rows.append({"phase": c.get("phase"), "check": cid, "required": (k or {}).get("required"),
                         "status": c.get("status"), "final": (k or {}).get("final") or c.get("status"),
                         "detail": (k or {}).get("detail") or c.get("detail") or c.get("not_run_reason")})
        for cid, k in classified.items():  # required checks with no row at all
            if cid not in {r["check"] for r in rows}:
                rows.append({"phase": k.get("phase"), "check": cid, "required": True, "status": "no row",
                             "final": k["final"], "detail": k["detail"]})
    except (RefusedError, OSError, ValueError, KeyError) as exc:
        out.append(para(esc(f"The checks could not be classified ({type(exc).__name__}: {run.clean(str(exc))}); "
                            "the files follow as written."), "warn"))
        rows = []
        out.append(run.csv_block("checks/checks_manifest.csv", "checks_manifest.csv", "P6"))

    def order(r: Mapping[str, Any]) -> tuple[int, str]:
        ph = str(r.get("phase"))
        return (PHASES.index(ph) if ph in PHASES else 99, str(r.get("check")))

    if rows:
        out.append(para("checks_manifest.csv with the P6 step rows of checks/run_steps_checks.csv, classified as "
                        "report.py classifies them (spec 6.4): status is what the check wrote, result is PASS, "
                        "FAIL-explained, FAIL-unexplained or NOT-RUN-at-level.", "muted"))
        out.append('<p><label for="check-filter">Filter checks: </label><input type="search" id="check-filter" '
                   'placeholder="type a check, phase or word"></p>')
        out.append(table(["phase", "check", "required", "status", "result", "detail"],
                         [[esc(r["phase"] or "-"), f"<code>{esc(r['check'])}</code>",
                           "" if r["required"] is None else ("yes" if r["required"] else "no"), esc(r["status"]),
                           badge(r["final"]), esc(r["detail"])] for r in sorted(rows, key=order)], tid="checks-table"))
    elif not run.has("checks/checks_manifest.csv") and not run.has("checks/run_steps_checks.csv"):
        out.append(para(run.absent("checks/checks_manifest.csv", "P6"), "muted"))
    if run.has("checks/replay_status.csv"):
        out.append(run.csv_block("checks/replay_status.csv", "Replay (P6)", "P6"))
    out.append("<h3>Trace (P8)</h3>")
    out.append(run.csv_block("trace/trace_results.csv", "Trace results", "P8"))
    cov = run.json("trace/trace_coverage.json")
    out.append(f"<h4>Trace coverage {run.link('trace/trace_coverage.json')}</h4>"
               + (run.pre(json.dumps(cov, ensure_ascii=False, indent=2), "doc") if cov is not None
                  else para(run.absent("trace/trace_coverage.json", "P8"), "muted")))
    tr = "trace_report.md" if run.has("trace_report.md") else "trace/trace_report.md"
    out.append(run.text_block(tr, "Trace report, with the sample sentences", "P8"))
    out.append("<h3>Logic (P9)</h3>")
    cdir = run.dir / "checks"
    logic = sorted(f.name for f in cdir.glob("logic_*.csv")) if cdir.is_dir() else []
    out += [run.csv_block(f"checks/{n}", n.rsplit(".", 1)[0].replace("_", " "), "P9") for n in logic]
    if not logic:
        out.append(para(run.absent("checks/logic_chain.csv", "P9"), "muted"))
    out.append("<h3>Lint (P5)</h3>")
    out.append(run.csv_block("lint_report.csv", "Lint report", "P5"))
    out.append("</section>")
    return "".join(out)


# ---------------------------------------------------------------- 6. cards and inputs
def cards_section(run: Run) -> str:
    out = ['<section class="sec" id="sec-cards"><h2>Cards and inputs</h2>']
    out.append(run.text_block("00_question.txt", "Max's question, verbatim", "P1"))
    out.append(run.text_block("01_question_card.md", "Question card", "P1"))
    out.append(run.text_block("02_metric_card.md", "Metric card", "P1"))
    out.append(run.text_block("13_table_decision.md", "Table decision", "P2"))
    out.append(run.csv_block("11_value_map.csv", "Value map", "P2"))
    out.append(run.csv_block("09_traps.csv", "Traps", "P2"))
    out.append(run.csv_block("08_shortlist.csv", "Shortlist", "P2"))
    pp = run.json("15_pin_plan.json")
    out.append(f"<h4>Pin plan {run.link('15_pin_plan.json')}</h4>")
    if isinstance(pp, dict):
        out.append(kv([("route", esc(pp.get("route"))), ("status", esc(pp.get("pin_status"))),
                       ("as_of_utc", esc(pp.get("as_of_utc") or pp.get("as_of_rule"))),
                       ("pin deadline", esc(pp.get("pin_deadline_utc") or pp.get("pin_deadline_rule"))),
                       ("smallest window (hours)", esc(pp.get("smallest_window_hours"))),
                       ("unpinnable", plain_list(pp.get("unpinnable"))),
                       ("provisional reasons", plain_list(pp.get("provisional_reasons"))),
                       ("open bands", plain_list(pp.get("open_bands")))]))
        tabs = [t for t in pp.get("tables") or [] if isinstance(t, dict)]
        if tabs:
            out.append(table(["table", "type", "pin method", "window hours", "late arrival"],
                             [[f"<code>{esc(t.get('ref'))}</code>", esc(t.get("table_type")), esc(t.get("pin_method")),
                               esc(t.get("window_hours")), cell(t.get("late_arrival"))] for t in tabs]))
    else:
        out.append(para(run.absent("15_pin_plan.json", "P3"), "muted"))
    out.append("</section>")
    return "".join(out)


# ---------------------------------------------------------------- 7. files
def files_section(run: Run) -> str:
    man = manifest_of(run)
    files = [f for f in man.get("files") or [] if isinstance(f, dict) and f.get("path")]
    listed = {str(f["path"]) for f in files}
    out = ['<section class="sec" id="sec-files"><h2>Files</h2>',
           para("Links are relative to this page. From a file:// page the browser opens .sql, .csv, .json and .md "
                "files as text (some browsers download .csv instead; open it from the run folder then).", "muted")]
    if files:
        out.append(f"<h3>In manifest.json ({len(files):,} files)</h3>")
        out.append(table(["path", "kind", "bytes", "sha256 (first 12)", "created_utc", "retention_until"],
                         [[run.link(str(f["path"])), esc(f.get("kind")), esc(num(f.get("bytes"))),
                           f"<code>{esc(str(f.get('sha256') or '')[:12])}</code>", esc(f.get("created_utc")),
                           esc(f.get("retention_until"))] for f in sorted(files, key=lambda f: str(f["path"]))]))
    else:
        out.append(para(run.absent("manifest.json files[]", "P6"), "muted"))
    root = run.dir.resolve()
    page = run.out.resolve()
    page_rel = page.relative_to(root).as_posix() if root in page.parents else None
    unlisted = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        rel = p.relative_to(root).as_posix()
        if rel in listed or rel == page_rel or ".tmp" in p.name and p.name.startswith(OUT_NAME + "."):
            continue
        unlisted.append(rel)
    rows = []
    if page_rel is not None:
        rows.append([f"<code>{esc(page_rel)}</code>", "this page: derived, regenerate with view.py"])
    for rel in unlisted:
        name = rel.split("/")[-1]
        why = BY_DESIGN.get(name) or ("gate requests and answers" if rel.startswith("gates/") else "")
        rows.append([run.link(rel), esc(why)])
    out.append(f"<h3>Not in manifest ({len(rows):,} files)</h3>"
               + (table(["path", "note"], rows) if rows else para("none", "muted")))
    out.append(para("close.py deletes by manifest list only: files not in the manifest are never touched.", "muted"))
    out.append("</section>")
    return "".join(out)


# ---------------------------------------------------------------- 8. footer and page
def footer(run: Run) -> str:
    seal = run.text("manifest.sha256")
    if seal is None:
        sealed = "no seal yet (report.py writes manifest.sha256 at P11)"
    else:
        parts = seal.split()
        state_word = ("manifest.json matches this seal" if seal_state(run) == "matches"
                      else "manifest.json has changed since this seal")
        sealed = f"manifest.sha256 {parts[0][:12] if parts else '(empty)'}; {state_word}"
    why = verdict_stale(run)
    stale = (f'<p class="stale-why">The verdict at the top is from an earlier pass, not current: '
             f'{esc("; ".join(why))}. Rerun report.py at P11 for a current one.</p>' if why else "")
    return ('<footer><p>Rendered by view.py from the run folder; regenerate after any change: '
            '<code>view.py --run-dir RUN</code> (RUN is the run folder). The page is derived: it is not in '
            'manifest.json and the seal does not cover it.</p>'
            f'<p>Seal: {esc(sealed)}.</p>{stale}</footer>')


def guarded(run: Run, title: str, fn: Any) -> str:
    """A section that fails to render says so in its place; the rest of the page still renders."""
    try:
        return fn(run)
    except Exception as exc:  # noqa: BLE001 - one bad artifact must not hide the rest of the run
        return (f'<section class="sec"><h2>{esc(title)}</h2>'
                + para(esc(f"This section could not be rendered: {type(exc).__name__}: {run.clean(str(exc))}"),
                       "warn") + "</section>")


NAV = (("sec-header", "Run"), ("sec-gates", "Gates and phases"), ("sec-graph", "Step graph"),
       ("sec-whole", "Whole B"), ("sec-checks", "Checks"), ("sec-cards", "Cards and inputs"), ("sec-files", "Files"))

CSS = """
:root{--bg:#ffffff;--fg:#1c2330;--muted:#596273;--line:#d4d9e2;--panel:#f4f6f9;--accent:#2353c2;
--pass-bg:#e1f3e6;--pass-fg:#16663a;--fail-bg:#fbe2e2;--fail-fg:#a32222;--nr-bg:#eaecf0;--nr-fg:#4a5363;
--part-bg:#fff0d2;--part-fg:#875700;--src-bg:#ebf1ff;color-scheme:light dark}
@media (prefers-color-scheme: dark){:root{--bg:#13161b;--fg:#e3e7ee;--muted:#9ba4b3;--line:#333a47;
--panel:#1b1f26;--accent:#86abff;--pass-bg:#173a25;--pass-fg:#93e2ad;--fail-bg:#4a1e21;--fail-fg:#ffb4b4;
--nr-bg:#262a32;--nr-fg:#b7beca;--part-bg:#3c3115;--part-fg:#f2cf7c;--src-bg:#1d2740}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,"Segoe UI",Roboto,sans-serif}
main,footer{width:100%;margin:0;padding:0 16px}
footer{border-top:1px solid var(--line);padding:12px 16px 32px;color:var(--muted)}
nav.top{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line)}
nav.top ol{list-style:none;display:flex;flex-wrap:wrap;gap:2px 16px;margin:0;padding:8px 16px}
a{color:var(--accent)}
a:focus-visible,input:focus-visible,summary:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
h1{font-size:22px;margin:16px 0 8px}h2{font-size:19px;margin:8px 0}h3{font-size:16px;margin:16px 0 6px}
h4{font-size:14px;margin:14px 0 4px}
section.sec{border-top:1px solid var(--line);padding:8px 0 24px;scroll-margin-top:44px}
.muted{color:var(--muted)}.warn{color:var(--part-fg);background:var(--part-bg);padding:4px 8px}
.verdict{font-size:17px;padding:10px 12px;background:var(--panel);border-left:4px solid var(--accent)}
.verdict.stale{border-left-color:var(--fail-fg);background:var(--fail-bg)}
.stale-tag{font-weight:700;color:var(--fail-fg)}.stale-why{font-size:13px;color:var(--fail-fg)}
.banner{margin:12px 0 0;padding:8px 12px;background:var(--fail-bg);color:var(--fail-fg);border:2px solid var(--fail-fg)}
dl.kv{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:3px 14px;margin:8px 0}
dl.kv dt{color:var(--muted)}dl.kv dd{margin:0;overflow-wrap:anywhere}dl.kv ul{margin:0;padding-left:18px}
.tw{overflow-x:auto;width:100%;margin:4px 0 8px}
table{border-collapse:collapse;font-size:13px}
th,td{border:1px solid var(--line);padding:3px 6px;text-align:left;vertical-align:top}
th{background:var(--panel)}th small{color:var(--muted);font-weight:normal}
tr.superseded td{text-decoration:line-through;color:var(--muted)}tr.superseded td:last-child{text-decoration:none}
pre{background:var(--panel);border:1px solid var(--line);padding:8px;overflow:auto;max-height:520px;
font:12.5px/1.45 ui-monospace,Consolas,"Cascadia Mono",monospace;white-space:pre;margin:4px 0 8px}
pre.doc{white-space:pre-wrap;overflow-wrap:anywhere}
code{font-family:ui-monospace,Consolas,monospace;font-size:12.5px;overflow-wrap:anywhere}
.null{color:var(--muted);font-style:italic}.ok{color:var(--pass-fg)}
.badge{display:inline-block;padding:0 6px;border-radius:4px;font-size:12px;white-space:nowrap}
.s-pass,.b-done,.g-answered{background:var(--pass-bg);color:var(--pass-fg)}
.s-fail,.g-open{background:var(--fail-bg);color:var(--fail-fg)}
.s-part,.b-current{background:var(--part-bg);color:var(--part-fg)}
.s-nr,.b-not-reached,.g-stale{background:var(--nr-bg);color:var(--nr-fg)}
ol.strip{list-style:none;padding:0;margin:8px 0 12px;display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:4px}
ol.strip a{display:block;padding:6px;border-radius:4px;text-decoration:none;color:inherit;min-height:64px}
ol.strip span,ol.strip em{display:block;font-size:12px}ol.strip em{font-style:normal}
.ph-done a{background:var(--pass-bg)}.ph-current a{background:var(--part-bg);outline:2px solid var(--part-fg)}
.ph-not-reached a{background:var(--nr-bg);color:var(--nr-fg)}
div.phase{border-left:3px solid var(--line);padding:2px 0 2px 12px;margin:10px 0;scroll-margin-top:44px}
div.phase.ph-not-reached{opacity:.75}
.answer{white-space:pre-wrap}
.legend{font-size:13px}
.graph-layout{display:grid;grid-template-columns:minmax(0,2fr) minmax(0,1fr);gap:16px;align-items:start}
.graph-box{overflow:auto;border:1px solid var(--line);background:var(--panel);max-height:72vh}
.panel{position:sticky;top:44px;max-height:calc(100vh - 56px);overflow:auto;border:1px solid var(--line);
padding:4px 12px 12px;background:var(--bg)}
.panel pre{max-height:360px}
.detail{display:none}.detail.on{display:block}html:not(.js) .detail:target{display:block}
.sentence{background:var(--panel);padding:6px 8px;border-left:3px solid var(--accent)}
svg.dag{display:block;width:100%;height:auto}
.graph-tools{margin:0 0 6px}.graph-tools button[aria-pressed="true"]{font-weight:700}html:not(.js) .graph-tools{display:none}
svg text{font:12px ui-monospace,Consolas,monospace;fill:var(--fg)}
svg .t1{font-weight:700}svg .t2,svg .t3{fill:var(--fg)}
.node rect{fill:var(--nr-bg);stroke:var(--nr-fg);stroke-width:1.5}
.node.pass rect{fill:var(--pass-bg);stroke:var(--pass-fg)}.node.fail rect{fill:var(--fail-bg);stroke:var(--fail-fg)}
.node.partial rect{fill:var(--part-bg);stroke:var(--part-fg)}
.node.source rect{fill:var(--src-bg);stroke:var(--line)}.node.deliv rect{fill:var(--bg);stroke:var(--accent)}
.node.bnode rect{stroke-dasharray:6 3}
a.node{cursor:pointer}a.node:focus{outline:none}a.node:focus rect,a.node:hover rect{stroke:var(--accent);stroke-width:3}
a.node.selected rect{stroke:var(--accent);stroke-width:4}
.edge{fill:none;stroke:var(--muted);stroke-width:1.4}.edge.p7-edge{stroke-dasharray:5 4}
.elabel{font-size:11px;fill:var(--muted);paint-order:stroke;stroke:var(--panel);stroke-width:4px}
#arrow path{fill:var(--muted)}
@media (max-width:900px){.graph-layout{grid-template-columns:minmax(0,1fr)}.panel{position:static;max-height:none}
ol.strip{grid-template-columns:repeat(4,minmax(0,1fr))}}
"""

JS = """
(function () {
  var d = document;
  d.documentElement.classList.add('js');
  var hint = d.getElementById('detail-hint');
  var gbox = d.querySelector('.graph-box'), dag = d.querySelector('svg.dag'), chosen = null;
  function pick(id, reveal) {
    var el = d.getElementById(id);
    if (!el || !el.classList.contains('detail')) { return false; }
    var i, on = d.querySelectorAll('.detail.on, .node.selected');
    for (i = 0; i < on.length; i++) { on[i].classList.remove('on'); on[i].classList.remove('selected');
      on[i].removeAttribute('aria-current'); }
    el.classList.add('on');
    if (hint) { hint.hidden = true; }
    var node = d.querySelector('a.node[href="#' + id + '"]');
    if (node) { node.classList.add('selected'); node.setAttribute('aria-current', 'true'); }
    if (reveal && node && gbox) {
      var r = node.getBoundingClientRect(), b = gbox.getBoundingClientRect();
      gbox.scrollLeft += (r.left - b.left) - (b.width - r.width) / 2;
    }
    return true;
  }
  d.addEventListener('click', function (e) {
    var a = e.target.closest ? e.target.closest('a.node, a.pick') : null;
    if (!a) { return; }
    var href = a.getAttribute('href') || '';
    if (href.charAt(0) !== '#' || !pick(href.slice(1), a.classList.contains('pick'))) { return; }
    e.preventDefault();
    if (a.classList.contains('pick')) {
      var p = d.getElementById('detail-panel');
      if (p && p.scrollIntoView) { p.scrollIntoView({block: 'nearest'}); }
    }
    if (history.replaceState) { history.replaceState(null, '', href); }
  });
  var modes = d.querySelectorAll('.graph-tools button');
  function size(mode) {
    if (!gbox || !dag) { return; }
    var natural = parseFloat(dag.getAttribute('data-width')) || 1;
    var fitMin = parseFloat(gbox.getAttribute('data-fit-min')) || 0.8;
    if (!mode) { mode = gbox.clientWidth / natural >= fitMin ? 'fit' : 'actual'; }
    dag.style.width = mode === 'actual' ? natural + 'px' : '';
    dag.style.maxWidth = mode === 'actual' ? 'none' : '';
    for (var i = 0; i < modes.length; i++) {
      modes[i].setAttribute('aria-pressed', modes[i].getAttribute('data-mode') === mode ? 'true' : 'false');
    }
  }
  for (var m = 0; m < modes.length; m++) {
    modes[m].addEventListener('click', function () { chosen = this.getAttribute('data-mode'); size(chosen); });
  }
  size(null);
  if (location.hash) { pick(location.hash.slice(1), true); }
  window.addEventListener('resize', function () { if (!chosen) { size(null); } });
  var f = d.getElementById('check-filter');
  if (f) {
    f.addEventListener('input', function () {
      var q = f.value.toLowerCase(), rows = d.querySelectorAll('#checks-table tbody tr'), i;
      for (i = 0; i < rows.length; i++) { rows[i].hidden = !!q && rows[i].textContent.toLowerCase().indexOf(q) < 0; }
    });
  }
})();
"""


def scan_banner(run: Run) -> str:
    """Fix r15 M2: a banner when no secret_scan_report.json exists, or when a run file is newer than it: the text
    below has not all been scanned. Fix r15b: state, not content, is ignored: run_state.json, gates/, view.html (this
    page, wherever --out put it) and the report itself; decisions.csv counts (Max's answers are text)."""
    rp = run.dir / "secret_scan_report.json"
    why = None
    if not rp.is_file():
        why = "no secret_scan_report.json yet"
    else:
        t = rp.stat().st_mtime_ns
        out = run.out.resolve()
        skip = {"secret_scan_report.json", state.STATE_FILE, OUT_NAME}
        newer = sorted(rel for p in run.dir.rglob("*") if p.is_file() and p.resolve() != out
                       and (rel := p.relative_to(run.dir).as_posix()) not in skip and not rel.startswith("gates/")
                       and "__pycache__" not in p.parts and p.stat().st_mtime_ns > t)
        if newer:
            why = f"{len(newer)} run file(s) changed after the last scan, e.g. {newer[0]}"
    if why is None:
        return ""
    return (f'<p class="banner" role="alert"><strong>Not yet secret-scanned: do not share this page.</strong> '
            f'{esc(why)}; run secret_scan.py, then view.py again.</p>')


def render(run: Run) -> str:
    """The whole page as text."""
    body = [scan_banner(run), guarded(run, "Run", header), guarded(run, "Gates and phases", phases),
            guarded(run, "Step graph", graph_section), guarded(run, "Whole query B", whole_section),
            guarded(run, "Checks", checks_section), guarded(run, "Cards and inputs", cards_section),
            guarded(run, "Files", files_section)]
    nav = "".join(f'<li><a href="#{i}">{esc(t)}</a></li>' for i, t in NAV)
    title = f"query-trail run {run.cfg.get('run_id') or run.state.run_id}"
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{esc(title)}</title><style>{CSS}</style></head><body>"
            f'<nav class="top" aria-label="Sections"><ol>{nav}</ol></nav><main>{"".join(body)}</main>'
            f"{footer(run)}<script>{JS}</script></body></html>\n")


# ---------------------------------------------------------------- CLI
def out_path(run_dir: Path, out: Any) -> Path:
    """RUN/view.html by default; --out must be an .html file and, inside the run folder, only RUN/view.html."""
    root = Path(run_dir).resolve()
    p = (Path(out) if out else root / OUT_NAME).resolve()
    if p.suffix.lower() != ".html":
        raise RefusedError(f"--out must name an .html file, got {p.name}; nothing written")
    if (p == root or root in p.parents) and p != root / OUT_NAME:
        raise RefusedError(f"--out inside the run folder may only be {OUT_NAME} at its top (the other run files "
                           "belong to the scripts); nothing written")
    if root not in p.parents:  # fix r15 L1: never inside another run folder (it would show the wrong run)
        runs = (state.qt_home() / "runs").resolve()
        other = next((d for d in p.parents if (d / state.STATE_FILE).is_file()), None)
        if other is not None or runs == p.parent or runs in p.parents:
            raise RefusedError(f"--out may not point inside another run folder or under {runs.as_posix()} (only "
                               f"this run's own {OUT_NAME}); nothing written")
    if p.is_dir():
        raise RefusedError(f"--out is a folder: {p}; nothing written")
    return p


def refuse_inputs(run_dir: Path) -> None:
    """Exit 2, nothing written: not a run folder, or a secret scan with hits (the page would copy them)."""
    for name in (c2.RUN_CONFIG, state.STATE_FILE):
        if not (Path(run_dir) / name).is_file():
            raise RefusedError(f"{name} missing in {Path(run_dir).name}: not a run folder; nothing written")
    sp = Path(run_dir) / "secret_scan_report.json"
    if sp.is_file():
        rep = c2.read_input_json(sp, "secret_scan_report.json")
        if not isinstance(rep, dict):
            raise RefusedError("secret_scan_report.json is not an object; rerun secret_scan.py; nothing written")
        hits = [h for h in rep.get("hits") or [] if not (isinstance(h, dict) and h.get("file") == OUT_NAME)]
        if hits:  # fix r15 M2: a hit only in the old page itself does not block regenerating it from the run files
            raise RefusedError(f"secret_scan_report.json lists {len(hits)} hit(s): the page would copy them; "
                               "fix the run and rerun secret_scan.py first; nothing written")


def build(run_dir: Path, out: Any = None) -> Path:
    """Render the page for `run_dir` and write it; return the path written."""
    run_dir = Path(run_dir)
    refuse_inputs(run_dir)
    target = out_path(run_dir, out)
    run = Run(run_dir, target)
    page = render(run)
    cc.write_text_lf(target, page)
    return target


def _main(a: Any) -> int:
    target = build(a.run_dir, a.out)
    print(f"VIEW={target.as_posix()}")
    print("open it in a browser; regenerate after any change to the run")
    return int(ExitCode.PASS)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    ap = c2.base_parser("view.py", "run viewer: one self-contained HTML page from a run folder (spec 2.2 P11)")
    ap.add_argument("--out", help=f"output .html (default RUN/{OUT_NAME})")
    return c2.run_cli_args(_main, ap, argv)


if __name__ == "__main__":
    sys.exit(main())

"""audit_on_write.py: PostToolUse and PostToolUseFailure hook on Bash and PowerShell (design 13.5; spec 2.1a
"audit on write"). Owner: M6.

For each model folder the command concerned (its cwd, the paths it names, and the folders raw_guard.py
recorded before the call), every workbook directly in the folder whose (mtime_ns, size) differs from the
record taken before the call is audited:
    python -P -X utf8 -B SKILL/scripts/rotm.py audit <workbook> --strict --json - --quiet
(interpreter ROTM_PYTHON, else this hook's own Python; 90 s per workbook, 100 s in all). With no record from
before the call, a workbook counts as changed when it differs from the last signature this session saw, or,
when the session never saw it, when it changed in the last 600 s.

When the command itself already ran a strict audit of that exact file (reports/status.json holds a strict
audit whose sha256 is the file's), that result is used and the audit is not run again.

v9 (round 8 verdict C-sol-5): a status record counts only while the model folder's inputs are as they were when
it was recorded: a record whose field "inputs" (modelfolder.inputs_fingerprint: the sha256 of each raw/<t>.csv
and the agreement hash of each agreed contract) differs from the inputs now is not reused and does not make the
state "recalculated"; a record without "inputs" (written before v9) counts as in v8. The session record of the
hook's own audit stores "inputs" as they were when that audit ran (for a reused audit: the reused record's).

Output (JSON on stdout, exit 0):
- errors or warnings: {"decision": "block", "reason": "rule-of-thumb-model audit on write: model.xlsx: 1 error,
  2 warnings ..." with up to 15 finding lines and a fix line}; the model sees it, the tool call has already run;
- clean: hookSpecificOutput.additionalContext "... model.xlsx: 0 errors, 0 warnings (<state>)";
- an audit that could not run: additionalContext saying so and how to run it by hand.
<state> (v5, A-aera-18): "recalculated" when reports/status.json holds, for the workbook, a passing check
(exit_code 0, recalculated true) or a passing recalc (exit_code 0) whose output_sha256 is the workbook's current
sha256; otherwise "the audit read the saved values; recalculation was not verified by this hook". The session
record's field "recalculated" follows the same rule.
PostToolUseFailure is wired as well (added to the design): a command that exits non-zero, such as
`rotm.py build --strict` with warnings, still writes the workbook, and Claude Code reports it as a failed call.
Cannot see: workbooks written outside the model folder; changes made while a timed-out audit ran.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import json
    import subprocess
    import time
    from dataclasses import dataclass, field
    from pathlib import Path
    from typing import Any

    import _hooklib as hl
    from lib import common, modelfolder
except Exception:  # noqa: BLE001 - the hook library could not load: run_hook.sh reports it (exit 3)
    sys.exit(3)

NAME = "audit_on_write"
WINDOW_S = 600              # design 13.5: no pre-call record and changed in the last 600 s
AUDIT_TIMEOUT_S = 90        # design 13.5
TOTAL_BUDGET_S = 100        # hooks.json allows 120 s for this hook
MAX_LINES = 15              # design 13.5: up to 15 finding lines
ROTM_ENV = "ROTM_HOOK_ROTM"     # tests only: another script in place of rotm.py
TAGS = {"error": "ERR ", "warning": "warn", "info": "info"}
STATE_RECALCULATED = "recalculated"
STATE_NOT_VERIFIED = "the audit read the saved values; recalculation was not verified by this hook"
RECALC_STEPS = ("check", "recalc")   # the steps that recalculate the workbook in Excel and record output_sha256


@dataclass
class Result:
    wb: Path
    root: Path
    rel: str
    sha256: str = ""
    ran: bool = False
    reused: bool = False
    exit_code: int | None = None
    errors: int = 0
    warnings: int = 0
    info: int = 0
    recalculated: bool = False
    lines: list[str] = field(default_factory=list)
    problem: str = ""
    inputs: dict[str, Any] | None = None    # v9 (C-sol-5): the model folder's inputs when the audit ran


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def finding_line(f: dict[str, Any]) -> str:
    """One console line per finding (design 3.3)."""
    where = ""
    if f.get("sheet"):
        where = f"{f['sheet']}!{f['cell']}" if f.get("cell") else str(f["sheet"])
    count = f.get("count") or 1
    if count > 1:
        where += f" ({count} cells)"
    tag = TAGS.get(str(f.get("severity")), "    ")
    text = f"{tag} {where}  {f.get('rule', '')}: {str(f.get('message', '')).rstrip('.')}"
    if f.get("fix"):
        text += f". Fix: {f['fix']}"
    return text.replace("\n", " ")


def recalculated_on_record(steps: dict[str, Any], sha256: str, inputs: dict[str, Any] | None = None) -> bool:
    """True when the status records of one workbook hold a passing check that recalculated it, or a passing
    recalc, whose output_sha256 is `sha256` (v5, A-aera-18). A recalc record has no field "recalculated": the
    step always recalculates; a check record must say recalculated true. v9 (C-sol-5): with `inputs` (the model
    folder's fingerprint now), a record whose own "inputs" differ does not count."""
    for step in RECALC_STEPS:
        rec = steps.get(step)
        if not isinstance(rec, dict) or not sha256:
            continue
        if rec.get("exit_code") == 0 and rec.get("output_sha256") == sha256 \
                and rec.get("recalculated", step == "recalc") is True and hl.inputs_hold(rec, inputs):
            return True
    return False


def run_audit(wb: Path, root: Path, budget_s: float) -> Result:
    rel = hl.rel_in(root, wb) or wb.name
    res = Result(wb, root, rel)
    try:
        res.sha256 = common.file_sha256(wb)
    except OSError as exc:
        res.problem = f"the workbook could not be read ({type(exc).__name__})"
        return res
    steps = modelfolder.read_status(root)["workbooks"].get(rel) or {}
    inputs = hl.model_inputs(root)
    res.recalculated = recalculated_on_record(steps, res.sha256, inputs)
    rec = steps.get("audit") or {}
    if rec.get("sha256") == res.sha256 and rec.get("strict") is True and rec.get("exit_code") == 0 \
            and hl.inputs_hold(rec, inputs):
        # a clean strict audit of this exact file is on record (the command ran it): not run twice. A record
        # with findings is not reused, so that the finding lines can be shown; nor is a record made before a later
        # landing or agreement (v9, C-sol-5).
        res.reused = True
        res.ran = True
        res.exit_code = 0
        res.inputs = rec.get("inputs") if isinstance(rec.get("inputs"), dict) else None
        return res
    res.inputs = inputs
    if budget_s < 5:
        res.problem = "not audited: the hook's time budget was used up by other workbooks"
        return res
    python = os.environ.get("ROTM_PYTHON") or sys.executable
    script = os.environ.get(ROTM_ENV) or str(hl.SCRIPTS_DIR / "rotm.py")
    cmd = [python, "-P", "-X", "utf8", "-B", script, "audit", str(wb), "--strict", "--json", "-", "--quiet"]
    env = dict(os.environ, PYTHONUTF8="1")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(cmd, cwd=str(root), env=env, stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=min(AUDIT_TIMEOUT_S, budget_s), creationflags=flags)
    except subprocess.TimeoutExpired:
        res.problem = f"the audit did not finish within {int(min(AUDIT_TIMEOUT_S, budget_s))} s"
        return res
    except OSError as exc:
        res.problem = f"the audit could not start ({type(exc).__name__}: {exc})"
        return res
    res.exit_code = proc.returncode
    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace").strip()
    report = None
    a, b = out.find("{"), out.rfind("}")
    if a >= 0 and b > a:
        try:
            report = json.loads(out[a:b + 1])
        except ValueError:
            report = None
    if proc.returncode not in (0, 1) or not isinstance(report, dict):
        last = err.splitlines()[-1] if err else "no message"
        res.problem = f"the audit did not run (exit {proc.returncode}: {last[:300]})"
        return res
    res.ran = True
    summary = report.get("summary") or {}
    res.errors = int(summary.get("errors") or 0)
    res.warnings = int(summary.get("warnings") or 0)
    res.info = int(summary.get("info") or 0)
    findings = [f for f in report.get("findings") or [] if isinstance(f, dict)]
    order ={"error": 0, "warning": 1, "info": 2}
    shown = sorted((f for f in findings if f.get("severity") in ("error", "warning")),
                   key=lambda f: order.get(str(f.get("severity")), 3))
    res.lines = [finding_line(f) for f in shown[:MAX_LINES]]
    if len(shown) > MAX_LINES:
        res.lines.append(f"... and {len(shown) - MAX_LINES} more findings")
    return res


def changed_workbooks(ctx: hl.Context, roots: dict[str, Path]) -> list[tuple[Path, Path]]:
    """Workbooks the call changed; records them in the session record (a new write sequence number)."""
    changed: list[tuple[Path, Path]] = []
    with hl.session(ctx.session_id) as s:
        pre = s["pre"].pop(ctx.tool_use_id or "_last", None)
        pre_roots = {hl.key(Path(r)) for r in (pre or {}).get("roots", [])}
        now = time.time()
        for rk, root in roots.items():
            for wb in hl.workbooks_in(root):
                k = hl.key(wb)
                sig = hl.signature(wb)
                if sig is None:
                    continue
                if rk in pre_roots:
                    before = ((pre or {}).get("workbooks", {}).get(k) or {}).get("sig")
                    is_changed = before != sig
                else:
                    last = (s["written"].get(k) or {}).get("sig") or (s["baseline"].get(k) or {}).get("sig")
                    is_changed = (last != sig) if last is not None else (now - sig[0] / 1e9 <= WINDOW_S)
                if is_changed:
                    changed.append((wb, root))
        for i, (wb, root) in enumerate(changed):
            hl.mark_written(s, wb, root, new_seq=(i == 0))
        hl.note_models(s, list(roots.values()))
    return changed


def handle(ctx: hl.Context) -> hl.Emit | None:
    if not ctx.is_shell:
        return None
    roots = {hl.key(r): r for r in ctx.roots()}
    known = hl.read_session(ctx.session_id)
    if known is not None:
        pre = known["pre"].get(ctx.tool_use_id or "_last") or {}
        for r in pre.get("roots", []):
            rp = Path(r)
            if modelfolder.is_model_root(rp):
                roots.setdefault(hl.key(rp), rp)
    if not roots:
        return None
    ctx.mark_model()
    changed = changed_workbooks(ctx, roots)
    if not changed:
        return None
    started = time.monotonic()
    results = []
    for wb, root in changed:
        results.append(run_audit(wb, root, TOTAL_BUDGET_S - (time.monotonic() - started)))
    with hl.session(ctx.session_id) as s:
        for r in results:
            if r.ran:
                own = {"path": r.wb.as_posix(), "sha256": r.sha256, "strict": True, "exit_code": r.exit_code,
                       "errors": r.errors, "warnings": r.warnings, "info": r.info, "recalculated": r.recalculated,
                       "reused": r.reused, "at": common.now_iso()}
                if r.inputs is not None:
                    own["inputs"] = r.inputs            # v9 (C-sol-5): the inputs when the audit ran
                s["audits"][hl.key(r.wb)] = own
    several = len({hl.key(r.root) for r in results}) > 1
    texts = []
    block = False
    for r in results:
        name = f"{r.root.name}/{r.rel}" if several else r.rel
        head = f"rule-of-thumb-model audit on write: {name}: "
        if not r.ran:
            texts.append(head + r.problem + f'. Run {hl.skill_command()} audit "{r.wb.as_posix()}" --strict yourself.')
            continue
        state = STATE_RECALCULATED if r.recalculated else STATE_NOT_VERIFIED
        texts.append(head + f"{_plural(r.errors, 'error')}, {_plural(r.warnings, 'warning')} ({state})"
                     + (" (from the strict audit this command ran)" if r.reused else ""))
        if r.errors or r.warnings:
            block = True
            texts.extend(r.lines)
            texts.append("Fix: change the specification (or run the command each fix names), build again, and run "
                         f'{hl.skill_command()} audit "{r.wb.as_posix()}" --strict until it exits 0.')
    text = "\n".join(texts)
    if block:
        return hl.Emit({"decision": "block", "reason": text})
    event = ctx.event if ctx.event in ("PostToolUse", "PostToolUseFailure") else "PostToolUse"
    return hl.Emit({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}})


if __name__ == "__main__":
    hl.run(NAME, handle)

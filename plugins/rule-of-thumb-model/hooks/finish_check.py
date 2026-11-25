"""finish_check.py: Stop hook (design 13.6; spec 2.1a "finish check"; procedure P7). Owner: M6.

The agent cannot end with "done" unless, for every workbook this session wrote in a model folder, the audit and
the recalculation passed, or its last message says plainly that the workbook was not recalculated.

- Workbooks checked: only those this session wrote: the session record from audit_on_write.py, plus any
  workbook whose signature differs from the one raw_guard.py recorded before an earlier call (a write that no
  PostToolUse hook saw). None: exit 0, no output, even when cwd is a model folder.
- For each such workbook with current sha256 S (reports/status.json of its model folder):
  audit passed: the last audit is strict with exit 0 (0 errors, 0 warnings) and its sha256 is S; or the last
    check has input_sha256 equal to that audit's sha256 and output_sha256 S (check recalculated and saved the
    audited file); or this session's own strict audit of S (audit_on_write.py) was clean;
  recalculated: the last check has recalculated true, failed 0, master 0 and output_sha256 S; or the last
    assistant message contains "not recalculated" (case-insensitive).
- Not both: {"decision": "block", "reason": ...} once per write sequence, and finish_refused_at_seq records it.
  A later stop with no new workbook write goes through, so the check refuses at most once per session end
  (plan F16 c). The record is per session id, so a second session in the same folder is checked again.
- A stop that is let through while a workbook is still not both (stop_hook_active true: this stop follows a
  refusal by a Stop hook; or this write sequence was refused already): {"systemMessage": one line naming each
  workbook and what is missing}, exit 0, no decision (round 1 verdict, A-aera-12 and C-sol-7). The words "not
  recalculated" in the last message excuse the missing recalculation only, as in the refusal.
- The audit and check commands in the refusal and in the notice name the workbook file itself
  (`check "<workbook path>"`; round 5 verdict, C-sol-5), so a second workbook built with -o at the top level of a
  model folder is checked as that file and not as the marker's workbook. Workbooks in a subfolder of the model
  folder are not watched (design 13.5), as decided in earlier rounds.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import re
    from pathlib import Path
    from typing import Any

    import _hooklib as hl
    from lib import common, modelfolder
except Exception:  # noqa: BLE001 - the hook library could not load: run_hook.sh reports it (exit 3)
    sys.exit(3)

NAME = "finish_check"
NOT_RECALC_RE = re.compile(r"(?i)\bnot\s+(?:been\s+|yet\s+)?recalculated\b")


def _clean(rec: dict[str, Any]) -> bool:
    """A strict audit record with no error and no warning."""
    return (rec.get("strict") is True and rec.get("exit_code") == 0
            and (rec.get("errors") in (0, None)) and (rec.get("warnings") in (0, None)))


def audit_passed(steps: dict[str, Any], sha: str, own: dict[str, Any] | None) -> bool:
    a = steps.get("audit") or {}
    c = steps.get("check") or {}
    if _clean(a) and a.get("sha256") == sha:
        return True
    if _clean(a) and a.get("sha256") and c.get("input_sha256") == a.get("sha256") and c.get("output_sha256") == sha:
        return True
    return bool(own and own.get("sha256") == sha and _clean(own))


def recalculated(steps: dict[str, Any], sha: str) -> bool:
    c = steps.get("check") or {}
    return (c.get("recalculated") is True and c.get("output_sha256") == sha
            and c.get("exit_code", 0) == 0 and c.get("failed", 0) == 0 and c.get("master", 0) == 0)


def audit_words(steps: dict[str, Any], sha: str, own: dict[str, Any] | None) -> str:
    for rec in (own, steps.get("audit")):
        if rec and rec.get("sha256") == sha and rec.get("strict") is True:
            return (f"the strict audit of this version found {rec.get('errors', '?')} errors and "
                    f"{rec.get('warnings', '?')} warnings")
    return "no strict audit of this version is on record"


def unseen_writes(s: dict[str, Any]) -> list[tuple[Path, Path]]:
    """Workbooks changed since the session last saw them, or created in a model folder it saw."""
    out = []
    for k, b in s["baseline"].items():
        p = Path(b["path"])
        sig = hl.signature(p)
        last = (s["written"].get(k) or {}).get("sig") or b.get("sig")
        if sig is not None and sig != last:
            out.append((p, Path(b["root"])))
    for r in s["models"]:
        root = Path(r)
        for wb in hl.workbooks_in(root):
            k = hl.key(wb)
            if k not in s["baseline"] and k not in s["written"]:
                out.append((wb, root))
    return out


def notice(problems: list[tuple[Path, Path, str, bool, bool, dict, dict | None]], several: bool) -> str:
    """The one line the user sees when a stop is let through with these workbooks unverified. The commands name
    the workbook file itself (round 5 verdict, C-sol-5)."""
    parts = []
    for wb, root, rel, a_ok, r_ok, _steps, _own in problems:
        missing = [w for w, ok in ((f'no clean strict audit (rotm.py audit "{wb.as_posix()}" --strict)', a_ok),
                                   (f'no passing recalculation (rotm.py check "{wb.as_posix()}"), and the last '
                                    "message does not say 'not recalculated'", r_ok)) if not ok]
        parts.append(f"{root.name}/{rel}" if several else rel)
        parts[-1] += f": {' and '.join(missing)}"
    return "rule-of-thumb-model finish check: not verified at the end of the session: " + "; ".join(parts) + "."


def handle(ctx: hl.Context) -> hl.Emit | None:
    active = bool(ctx.payload.get("stop_hook_active"))
    if hl.read_session(ctx.session_id) is None:
        return None
    with hl.session(ctx.session_id) as s:
        for i, (wb, root) in enumerate(unseen_writes(s)):
            hl.mark_written(s, wb, root, new_seq=(i == 0))
            s["baseline"].setdefault(hl.key(wb), {"path": wb.as_posix(), "root": root.as_posix(), "sig": None})
        written = [dict(w) for w in s["written"].values()]
        seq = int(s.get("write_seq") or 0)
        refused_at = s.get("finish_refused_at_seq")
        audits = dict(s["audits"])
    hl.prune_sessions()
    if not written:
        return None
    ctx.mark_model()
    last_text: str | None = None
    problems: list[tuple[Path, Path, str, bool, bool, dict, dict | None]] = []
    for w in written:
        wb, root = Path(w["path"]), Path(w["root"])
        if not wb.is_file() or not modelfolder.is_model_root(root):
            continue
        sha = common.file_sha256(wb)
        rel = hl.rel_in(root, wb) or w.get("rel") or wb.name
        steps = modelfolder.read_status(root)["workbooks"].get(rel) or {}
        own = audits.get(hl.key(wb))
        a_ok = audit_passed(steps, sha, own)
        r_ok = recalculated(steps, sha)
        if not r_ok:
            if last_text is None:
                last_text = hl.last_assistant_text(ctx.payload)
            r_ok = bool(NOT_RECALC_RE.search(last_text or ""))
        if not (a_ok and r_ok):
            problems.append((wb, root, rel, a_ok, r_ok, steps, own))
    if not problems:
        return None
    several = len({hl.key(p[1]) for p in problems}) > 1
    if active or refused_at == seq:
        # Let through (plan F16 c: at most one refusal per session end), but the user is told in one line
        # (round 1 verdict, A-aera-12 and C-sol-7). systemMessage is shown to the user; no decision is given.
        return hl.Emit({"systemMessage": notice(problems, several)})
    with hl.session(ctx.session_id) as s:
        s["finish_refused_at_seq"] = seq
        s["finish_refusals"] = int(s.get("finish_refusals") or 0) + 1
    names = ", ".join(f"{root.name}/{rel}" if several else rel for _wb, root, rel, *_ in problems)
    lines = [f"rule-of-thumb-model finish check: {names} changed after its last passing audit or recalculation."]
    rotm = hl.skill_command()
    for wb, root, rel, a_ok, r_ok, steps, own in problems:
        name = f"{root.name}/{rel}" if several else rel
        if not a_ok:
            sha = common.file_sha256(wb)
            lines.append(f"- {name}: audit not passed: {audit_words(steps, sha, own)}. "
                         f'Run {rotm} audit "{wb.as_posix()}" --strict and fix what it reports.')
        if not r_ok:
            # the workbook file itself, not the model folder: `check <model folder>` checks the marker's workbook,
            # also when this is a second workbook built with -o (round 5 verdict, C-sol-5)
            lines.append(f"- {name}: not recalculated: no passing check of this version. "
                         f'Run {rotm} check "{wb.as_posix()}".')
    lines.append("If Excel is not available, say in your report that the workbook was not recalculated. "
                 "If a finding cannot be fixed, say which one and why in your report.")
    return hl.Emit({"decision": "block", "reason": "\n".join(lines)})


if __name__ == "__main__":
    hl.run(NAME, handle)

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
    assistant message contains "not recalculated" (case-insensitive); or an earlier stop of this session that was
    let through excused this workbook's recalculation with those words while it had sha256 S, and the session has
    not written it since (the record "disclosed": workbook key -> sha256 and write sequence; round 7 verdict,
    C-sol-5). A workbook written after that stop (another write sequence, or another sha256) is judged as before.
    The record is written only by a stop that is let through (silently or with the notice), never by a refusal.
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
- v9 (round 8 verdict C-sol-5): a passing audit or check counts only while the model folder's inputs are as they
  were when it was recorded. Each audit, check and recalc record in reports/status.json, and the session record of
  the hook's own audit, holds the field "inputs" (modelfolder.inputs_fingerprint: per contracts/<t>.contract.json,
  the sha256 of raw/<t>.csv and the agreement hash when the contract is agreed). Every record a pass rests on (the
  audit, the check whose input was the audited file, the hook's own audit, the check for the recalculation) must
  hold the inputs as they are now; a record without "inputs" (written before v9) counts as in v8. A pass that
  would count by its sha256 but whose inputs differ is stale: after a landing (raw/<t>.csv changed) or an
  agreement of other content (or a contract no longer agreed), the refusal and the notice name each such table and
  the command that brings the workbook up to date: `build "<model folder>" --force` (with -o "<workbook>" for a
  workbook that is not the specification's output), then audit --strict and check; or, when only raw/<t>.csv of
  tables the workbook's manifest records as loaded by a query (landing power_query) changed,
  `check "<workbook>" --refresh`, then audit --strict. The one refusal per session end, the notice and the
  disclosure rule are as above.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import json
    import re
    from dataclasses import dataclass, field
    from pathlib import Path
    from typing import Any

    import _hooklib as hl
    from lib import common, modelfolder
except Exception:  # noqa: BLE001 - the hook library could not load: run_hook.sh reports it (exit 3)
    sys.exit(3)

NAME = "finish_check"
NOT_RECALC_RE = re.compile(r"(?i)\bnot\s+(?:been\s+|yet\s+)?recalculated\b")
SPEC_FILE = "model.spec.json"              # lib.spec.SPEC_FILE: the specification build reads from a model folder
MANIFEST_SUFFIX = ".manifest.json"         # lib.manifest.MANIFEST_SUFFIX: model.xlsx -> model.manifest.json


def _clean(rec: dict[str, Any]) -> bool:
    """A strict audit record with no error and no warning."""
    return (rec.get("strict") is True and rec.get("exit_code") == 0
            and (rec.get("errors") in (0, None)) and (rec.get("warnings") in (0, None)))


def audit_ways(steps: dict[str, Any], sha: str, own: dict[str, Any] | None) -> list[list[dict[str, Any]]]:
    """The ways the audit passes by the workbook's sha256 alone (the v8 rule), each as the records it rests on: the
    clean strict audit of S; that audit and the check that recalculated and saved the audited file as S; this
    session's own clean strict audit of S."""
    a = steps.get("audit") or {}
    c = steps.get("check") or {}
    ways: list[list[dict[str, Any]]] = []
    if _clean(a) and a.get("sha256") == sha:
        ways.append([a])
    if _clean(a) and a.get("sha256") and c.get("input_sha256") == a.get("sha256") and c.get("output_sha256") == sha:
        ways.append([a, c])
    if own and own.get("sha256") == sha and _clean(own):
        ways.append([own])
    return ways


def audit_passed(steps: dict[str, Any], sha: str, own: dict[str, Any] | None,
                 inputs: dict[str, Any] | None = None) -> bool:
    """A way of audit_ways whose every record holds the inputs as they are now (v9, C-sol-5; `inputs` None: the
    v8 rule)."""
    return any(all(hl.inputs_hold(r, inputs) for r in way) for way in audit_ways(steps, sha, own))


def recalc_record(steps: dict[str, Any], sha: str) -> dict[str, Any] | None:
    """The last check when it recalculated S with exit 0, no failed check and the master at 0 (the v8 rule)."""
    c = steps.get("check") or {}
    ok = (c.get("recalculated") is True and c.get("output_sha256") == sha
          and c.get("exit_code", 0) == 0 and c.get("failed", 0) == 0 and c.get("master", 0) == 0)
    return c if ok else None


def recalculated(steps: dict[str, Any], sha: str, inputs: dict[str, Any] | None = None) -> bool:
    """recalc_record exists and holds the inputs as they are now (v9, C-sol-5; `inputs` None: the v8 rule)."""
    c = recalc_record(steps, sha)
    return c is not None and hl.inputs_hold(c, inputs)


def stale_changes(records: list[dict[str, Any]], inputs: dict[str, Any] | None) -> list[tuple[str, str]]:
    """(table, "raw" or "contract") for each input that differs between these records and the inputs now."""
    if inputs is None:
        return []
    found: set[tuple[str, str]] = set()
    for r in records:
        found.update(modelfolder.inputs_changes(r.get("inputs"), inputs))
    return sorted(found)


def _json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def query_loaded(wb: Path, table: str) -> bool:
    """The workbook's manifest records the raw sheet of `table` as loaded by a query (landing power_query)."""
    for sh in _json(wb.with_name(wb.stem + MANIFEST_SUFFIX)).get("sheets") or []:
        if isinstance(sh, dict) and sh.get("type") == "raw" and sh.get("table") == table:
            return sh.get("landing") == "power_query"
    return False


def rebuild_args(wb: Path, root: Path) -> str:
    """The arguments of the build that writes this workbook again: the model folder (or the specification the
    manifest names, when it is not model.spec.json), -o "<workbook>" when the workbook is not the specification's
    output, and --force (accepted for an existing workbook; build keeps a backup)."""
    spec_rel = str(_json(wb.with_name(wb.stem + MANIFEST_SUFFIX)).get("spec") or SPEC_FILE)
    spec = root / spec_rel
    out = root / str(_json(spec).get("output") or "model.xlsx")
    extra = f' -o "{wb.as_posix()}"' if hl.key(out) != hl.key(wb) else ""
    target = root if spec_rel == SPEC_FILE else spec
    return f'"{target.as_posix()}"{extra} --force'


def update_command(wb: Path, root: Path, changes: list[tuple[str, str]], rotm: str) -> str:
    """The command that brings the workbook up to date after these changes: a refresh when only raw snapshots of
    tables loaded by a query changed, else a rebuild (a new agreement always needs a rebuild: CT-VERSION)."""
    if changes and all(what == "raw" and query_loaded(wb, t) for t, what in changes):
        return f'{rotm} check "{wb.as_posix()}" --refresh, then audit --strict'
    return f"{rotm} build {rebuild_args(wb, root)}, then audit --strict and check it"


def change_text(table: str, what: str) -> str:
    """The words for one changed input (plan v9 5.1)."""
    if what == "raw":
        return f"raw/{table}.csv changed after the last passing audit or check of this version (a landing)"
    return (f"contract '{table}' was agreed again, or is no longer agreed, after the last passing audit or check of "
            "this version")


@dataclass
class Problem:
    """One workbook that is not both audited and recalculated at a stop."""
    wb: Path
    root: Path
    rel: str
    a_ok: bool
    r_ok: bool
    steps: dict[str, Any]
    own: dict[str, Any] | None
    a_stale: bool = False        # v9 (C-sol-5): the audit passes by sha256, but on other inputs than now
    r_stale: bool = False        # v9 (C-sol-5): the recalculation counts by sha256, but on other inputs than now
    stale: list[tuple[str, str]] = field(default_factory=list)       # (table, "raw" or "contract")

    def name(self, several: bool) -> str:
        return f"{self.root.name}/{self.rel}" if several else self.rel

    @property
    def changed(self) -> bool:
        """A v8 reason: the audit or the recalculation is missing for this version (not only stale)."""
        return (not self.a_ok and not self.a_stale) or (not self.r_ok and not self.r_stale)


def audit_words(steps: dict[str, Any], sha: str, own: dict[str, Any] | None) -> str:
    for rec in (own, steps.get("audit")):
        if rec and rec.get("sha256") == sha and rec.get("strict") is True:
            return (f"the strict audit of this version found {rec.get('errors', '?')} errors and "
                    f"{rec.get('warnings', '?')} warnings")
    return "no strict audit of this version is on record"


def disclosed(rec: dict[str, Any] | None, sha: str, written_seq: int) -> bool:
    """An earlier stop that was let through excused this version's recalculation with the words "not
    recalculated", and the session has not written the workbook since (round 7 verdict, C-sol-5)."""
    return bool(rec and rec.get("sha256") == sha and written_seq <= int(rec.get("seq") or 0))


def remember_disclosures(ctx: hl.Context, excused: list[tuple[str, str]], seq: int) -> None:
    """Record, for a stop that is let through, each workbook whose recalculation the words excused: its key, its
    sha256 and the present write sequence."""
    if not excused:
        return
    with hl.session(ctx.session_id) as s:
        book = s.setdefault("disclosed", {})
        for k, sha in excused:
            book[k] = {"sha256": sha, "seq": seq, "at": common.now_iso()}


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


def notice(problems: list[Problem], several: bool) -> str:
    """The one line the user sees when a stop is let through with these workbooks unverified. The commands name
    the workbook file itself (round 5 verdict, C-sol-5). v9 (round 8 verdict C-sol-5): a stale pass is named by its
    changed tables and the command that brings the workbook up to date."""
    parts = []
    for p in problems:
        wb = p.wb
        missing = [w for w, ok in ((f'no clean strict audit (rotm.py audit "{wb.as_posix()}" --strict)',
                                    p.a_ok or p.a_stale),
                                   (f'no passing recalculation (rotm.py check "{wb.as_posix()}"), and the last '
                                    "message does not say 'not recalculated'", p.r_ok or p.r_stale)) if not ok]
        if p.stale:
            missing.append(", ".join(f"{t}: {change_text(t, what)}" for t, what in p.stale)
                           + f" ({update_command(wb, p.root, p.stale, 'rotm.py')})")
        parts.append(f"{p.name(several)}: {' and '.join(missing)}")
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
        memory = dict(s.get("disclosed") or {})
    hl.prune_sessions()
    if not written:
        return None
    ctx.mark_model()
    last_text: str | None = None
    problems: list[Problem] = []
    excused: list[tuple[str, str]] = []          # (workbook key, sha256) whose recalculation the words excused
    inputs_of: dict[str, dict[str, Any] | None] = {}     # model folder key -> its inputs now (v9, C-sol-5)
    for w in written:
        wb, root = Path(w["path"]), Path(w["root"])
        if not wb.is_file() or not modelfolder.is_model_root(root):
            continue
        sha = common.file_sha256(wb)
        rel = hl.rel_in(root, wb) or w.get("rel") or wb.name
        steps = modelfolder.read_status(root)["workbooks"].get(rel) or {}
        k = hl.key(wb)
        own = audits.get(k)
        rk = hl.key(root)
        if rk not in inputs_of:
            inputs_of[rk] = hl.model_inputs(root)
        inputs = inputs_of[rk]
        a_ok = audit_passed(steps, sha, own, inputs)
        r_ok = recalculated(steps, sha, inputs) or disclosed(memory.get(k), sha, int(w.get("seq") or 0))
        if not r_ok:
            if last_text is None:
                last_text = hl.last_assistant_text(ctx.payload)
            r_ok = bool(NOT_RECALC_RE.search(last_text or ""))
            if r_ok:
                excused.append((k, sha))
        if not (a_ok and r_ok):
            p = Problem(wb, root, rel, a_ok, r_ok, steps, own)
            # v9 (C-sol-5): a pass that counts by sha256 but rests on other inputs than now is stale
            stale_records: list[dict[str, Any]] = []
            if not a_ok:
                ways = audit_ways(steps, sha, own)
                p.a_stale = bool(ways)
                stale_records += [r for way in ways for r in way]
            if not r_ok:
                rec = recalc_record(steps, sha)
                p.r_stale = rec is not None
                stale_records += [rec] if rec is not None else []
            p.stale = stale_changes(stale_records, inputs)
            problems.append(p)
    if not problems:
        remember_disclosures(ctx, excused, seq)
        return None
    several = len({hl.key(p.root) for p in problems}) > 1
    if active or refused_at == seq:
        # Let through (plan F16 c: at most one refusal per session end), but the user is told in one line
        # (round 1 verdict, A-aera-12 and C-sol-7). systemMessage is shown to the user; no decision is given.
        remember_disclosures(ctx, excused, seq)
        return hl.Emit({"systemMessage": notice(problems, several)})
    with hl.session(ctx.session_id) as s:
        s["finish_refused_at_seq"] = seq
        s["finish_refusals"] = int(s.get("finish_refusals") or 0) + 1
    changed = [p.name(several) for p in problems if p.changed]
    stale_only = [p.name(several) for p in problems if not p.changed]
    head = []
    if changed:
        head.append(f"{', '.join(changed)} changed after its last passing audit or recalculation.")
    if stale_only:
        head.append(f"{', '.join(stale_only)}: the raw data or contracts of its model folder changed after its last "
                    "passing audit or check.")
    lines = ["rule-of-thumb-model finish check: " + " ".join(head)]
    rotm = hl.skill_command()
    for p in problems:
        name = p.name(several)
        if not p.a_ok and not p.a_stale:
            sha = common.file_sha256(p.wb)
            lines.append(f"- {name}: audit not passed: {audit_words(p.steps, sha, p.own)}. "
                         f'Run {rotm} audit "{p.wb.as_posix()}" --strict and fix what it reports.')
        if not p.r_ok and not p.r_stale:
            # the workbook file itself, not the model folder: `check <model folder>` checks the marker's workbook,
            # also when this is a second workbook built with -o (round 5 verdict, C-sol-5)
            lines.append(f"- {name}: not recalculated: no passing check of this version. "
                         f'Run {rotm} check "{p.wb.as_posix()}".')
        if p.stale:
            # v9 (round 8 verdict C-sol-5): each changed table, and the command that brings the workbook up to date
            command = update_command(p.wb, p.root, p.stale, rotm)
            for t, what in p.stale:
                lines.append(f"- {name}: {t}: {change_text(t, what)}. Run {command}.")
    lines.append("If Excel is not available, say in your report that the workbook was not recalculated. "
                 "If a finding cannot be fixed, say which one and why in your report.")
    return hl.Emit({"decision": "block", "reason": "\n".join(lines)})


if __name__ == "__main__":
    hl.run(NAME, handle)

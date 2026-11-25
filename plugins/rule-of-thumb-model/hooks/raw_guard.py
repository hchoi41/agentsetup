"""raw_guard.py: PreToolUse hook on Write, Edit, MultiEdit, NotebookEdit, Bash and PowerShell (design 13.4;
spec 2.1a "raw-sheet guard"; procedure P2: a raw sheet holds landed data only). Owner: M6.

Inside a model folder it refuses, with exit 2 and the reason on stderr:
1. a text-tool write or edit of raw/* (the landed snapshots: raw data changes only by `rotm.py land`);
2. a text-tool write or edit of a workbook (.xlsx, .xlsm);
3. a Bash or PowerShell command, other than rotm.py, that writes into raw/ (redirect, tee, cp, mv,
   Copy-Item, Move-Item, xcopy, robocopy, Set-Content, Add-Content, Out-File, New-Item, Export-Csv, sed -i,
   Python or .NET write calls with a literal path);
4. a Bash or PowerShell command, other than rotm.py and formula_audit.py, that copies or moves a file onto a
   workbook in the model folder that exists, moves a file onto a new workbook name there, redirects into a
   workbook, or names a workbook in the model folder and holds a workbook write pattern (.save(, SaveAs,
   .SaveCopyAs(, to_excel(, ExcelWriter, openpyxl.Workbook(, xlsxwriter). Copying a model workbook out of the
   folder (for reading) is let through, and so is a copy (cp, Copy-Item or its alias copy, xcopy, robocopy,
   Python's shutil.copy, copy2 or copyfile) to a workbook name in the model folder that does not exist yet, for
   example a downloaded workbook saved into a copy of the model folder (round 2 verdict, C-sol-14). A destination
   name with * or ? counts as an existing workbook.
It also records, for every Bash or PowerShell call that concerns a model folder, the (mtime_ns, size) of each
workbook in that folder before the call (the session record; audit_on_write.py compares after the call).

Cannot see: what a script does when it builds the workbook path at run time or reads it from another file;
edits made in Excel outside Claude Code; MCP tools (not matched). The audit's R-CHANGED rule (raw digest
against the manifest) catches a changed raw sheet at the next audit.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import time
    from pathlib import Path

    import _hooklib as hl
except Exception:  # noqa: BLE001 - the hook library could not load: run_hook.sh reports it (exit 3)
    sys.exit(3)

NAME = "raw_guard"
PRE_MAX = 100          # pre-call records kept per session
# How _hooklib names the copy commands (Write.how) whose copy to a workbook name that does not exist yet is let
# through (round 2 verdict, C-sol-14); moves (mv, Move-Item, shutil.move, robocopy /MOV) are not copies.
COPY_HOWS = frozenset({"cp", "Copy-Item", "xcopy", "robocopy", "python shutil.copy"})


def new_workbook(path: Path) -> bool:
    """Whether a copy creates this workbook: no file of that name exists yet, and the name holds no wildcard."""
    return not any(ch in path.name for ch in "*?[") and not os.path.lexists(path)


def raw_message(root: Path, rel: str, how: str = "") -> str:
    parts = rel.split("/")
    name = parts[1] if len(parts) > 1 else ""
    table = name.split(".")[0] if name else "<table>"
    by = f" ({how})" if how else ""
    return (f"rule-of-thumb-model raw-sheet guard: {rel}{by}: raw/{table}.csv is the landed snapshot of raw table "
            f"'{table}'; raw data changes only by landing: {hl.skill_command()} land \"{root.as_posix()}\" "
            f"--table {table} --csv <file>. A raw sheet holds landed data only (no formulas, no edits).")


def workbook_text_message(rel: str) -> str:
    return (f"rule-of-thumb-model raw-sheet guard: {rel}: a workbook is written by rotm.py build, theme, compat, "
            "split or extend; a text tool cannot edit it. Change the specification and run rotm.py build.")


def workbook_command_message(rel: str, how: str) -> str:
    copy = (" A copy to a workbook name that does not exist yet in the folder is let through; this one would "
            "overwrite an existing workbook." if how in COPY_HOWS else "")
    return (f"rule-of-thumb-model raw-sheet guard: this command writes {rel} outside rotm.py ({how}), which can "
            "change its raw sheets. Change the specification and run rotm.py build, or land new raw data with "
            "rotm.py land. To read the workbook, open it without saving; to try something, copy it outside the "
            f"model folder first and work on the copy.{copy}")


def record_pre(ctx: hl.Context, roots: list[Path]) -> None:
    """Workbook signatures before the call, keyed by tool_use_id (design 13.4, for audit_on_write)."""
    with hl.session(ctx.session_id) as s:
        hl.note_models(s, roots)
        wbs = {}
        for r in roots:
            for wb in hl.workbooks_in(r):
                wbs[hl.key(wb)] = {"path": wb.as_posix(), "sig": hl.signature(wb)}
        now = time.time()
        pre = {k: v for k, v in s["pre"].items() if now - float(v.get("at", 0)) < hl.PRE_KEEP_S}
        if len(pre) >= PRE_MAX:
            for k in sorted(pre, key=lambda k: pre[k].get("at", 0))[: len(pre) - PRE_MAX + 1]:
                pre.pop(k)
        pre[ctx.tool_use_id or "_last"] = {"at": now, "roots": [r.as_posix() for r in roots], "workbooks": wbs}
        s["pre"] = pre


def handle(ctx: hl.Context) -> hl.Deny | None:
    if ctx.tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        target = ctx.file_target()
        root = hl.root_of(target)
        if root is None or target is None:
            return None
        ctx.mark_model()
        rel = hl.rel_in(root, target)
        if rel is None:
            return None
        if rel.lower() == "raw" or rel.lower().startswith("raw/"):
            return hl.Deny(raw_message(root, rel))
        if target.suffix.lower() in hl.WORKBOOK_EXT:
            return hl.Deny(workbook_text_message(rel))
        return None
    if not ctx.is_shell:
        return None
    roots = ctx.roots()
    if not roots:
        return None
    ctx.mark_model()
    record_pre(ctx, roots)
    named: list[tuple[Path, Path]] = []                 # model workbooks named anywhere in the command
    savers: list[tuple[str, list[tuple[Path, Path]], bool]] = []   # (pattern, model workbooks named, names any)
    for seg in ctx.segments():
        if seg.rotm is not None:
            continue
        for w in seg.writes:
            root = hl.root_of(w.path)
            if root is None:
                continue
            rel = hl.rel_in(root, w.path)
            if rel is None:
                continue
            if rel.lower() == "raw" or rel.lower().startswith("raw/"):
                return hl.Deny(raw_message(root, rel, w.how))
            if w.path.suffix.lower() in hl.WORKBOOK_EXT:
                if w.kind == "write" and w.how in COPY_HOWS and new_workbook(w.path):
                    continue                           # a copy that creates a new workbook (C-sol-14)
                return hl.Deny(workbook_command_message(rel, w.how))
        if seg.spawns:
            continue                                   # the inner segments carry the paths and patterns
        mine: list[tuple[Path, Path]] = []
        any_wb = False
        for p in seg.paths:
            if p.suffix.lower() in hl.WORKBOOK_EXT:
                any_wb = True
                root = hl.root_of(p)
                if root is not None and hl.rel_in(root, p) is not None:
                    mine.append((p, root))
        named.extend(mine)
        m = hl.WB_WRITE_RE.search(seg.raw) or hl.WB_WRITE_RE.search(seg.code)
        if m:
            savers.append((m.group(0).strip(), mine, any_wb))
    # A segment that saves a workbook: refused when it names a model workbook, or when it names no workbook at
    # all (COM: $wb.Save() after $xl.Workbooks.Open('model.xlsx') in an earlier statement) and another segment
    # of the command names one. A segment that saves only workbooks outside model folders is let through.
    for pattern, mine, any_wb in savers:
        hit = mine[0] if mine else (named[0] if named and not any_wb else None)
        if hit is not None:
            p, root = hit
            return hl.Deny(workbook_command_message(hl.rel_in(root, p) or p.name, f"'{pattern}'"))
    return None


if __name__ == "__main__":
    hl.run(NAME, handle)

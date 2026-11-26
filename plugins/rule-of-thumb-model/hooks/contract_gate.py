"""contract_gate.py: PreToolUse hook on Write, Edit, MultiEdit, Bash and PowerShell (design 13.3; spec 2.1a
"contract gate"; procedure P1). Owner: M6.

Inside a model folder (one holding rotm_model.json) it refuses, with exit 2 and the reason on stderr:
1. SQL or Power Query M for raw table T (queries/T.sql, queries/T.pq.m) while T's contract is not agreed
   (missing, invalid, proposed, or stale: edited after the agreement);
2. any other .sql or .m file in the model folder (SQL goes in queries/<table>.sql, named after its raw table);
3. a text edit of an agreed contracts/T.contract.json;
4. a text edit that would mark a contract agreed (added to the design: only `rotm.py contract agree` records an
   agreement, with the user's own words);
5. `rotm.py contract agree` whose --reply reads as a refusal or a change request (lib.contract.reads_as_refusal: a
   negative first word, a negated agreement anywhere, such as "not agreed" or "동의하지 않", a request for a
   change, such as "please change" or "바꿔 주세요", rule R-CHANGE, or a reply that postpones the decision, such as
   "I will review it tomorrow" or "검토 후 알려줄게": no agreement was given, wait for the user's reply; or a reply
   in which every sentence is a question, such as "What does the amount column include?": answer the question and
   wait for the user's reply; any channel), or, with channel
   session (the default), a call in the same command line that proposes or writes that table's contract first,
   or a --reply that is not in any message the user typed in this session's transcript, or that is only in
   messages that come before the latest assistant tool call in the transcript that ran `rotm.py contract propose`
   for that table or wrote contracts/<table>.contract.json (Write, Edit, MultiEdit, or a shell write the command
   line shows; round 1 verdict C-sol-13). When the transcript shows no such call, the reply must have been typed
   after the contract's proposed_at (live test 1). A reply shorter than the user message that holds it is read
   in that message (lib.contract.reply_refusal, round 4 verdict A-aera-4 and C-sol-2): the sentence that holds
   it and the whole message must not read as a refusal, except a sentence that names another contract table of
   the model and not this one ('agreed' taken from 'I have not agreed.' is refused). If the transcript cannot be
   read, the reply cannot be parsed from the command, or a time cannot be read, the call is let through.
Every other rotm.py command is let through (the scripts enforce their own rules: land refuses an unagreed
contract), and so is everything outside model folders, with no output.
Bash and PowerShell writes are seen when the command line shows them: >, >>, tee, cp, mv, Copy-Item,
Move-Item, Set-Content, Add-Content, Out-File, New-Item, sed -i, and Python or .NET write calls whose path is a
literal. A script that builds the path at run time is not seen.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import json
    import re
    from pathlib import Path
    from typing import Any

    import _hooklib as hl
    from lib import modelfolder
except Exception:  # noqa: BLE001 - the hook library could not load: run_hook.sh reports it (exit 3)
    sys.exit(3)

NAME = "contract_gate"
SQL_RE = re.compile(r"^queries/(?P<t>[^/]+?)(?:\.sql|\.pq\.m)$", re.IGNORECASE)
CONTRACT_RE = re.compile(r"^contracts/(?P<t>[^/]+)\.contract\.json$", re.IGNORECASE)
AGREED_RE = re.compile(r"""status['"]?\s*\]?\s*[:=]\s*['"]agreed['"]""")   # JSON, or Python dict code


def _q(path: Path) -> str:
    return '"' + path.as_posix() + '"'


def sql_message(root: Path, rel: str, table: str, state: str) -> str:
    rotm = hl.skill_command()
    agree = (f'{rotm} contract agree {_q(root)} --table {table} --reply "<their words>" --by <name>')
    why = {
        "missing": f"which has no contract yet (no contracts/{table}.contract.json)",
        "invalid": f"whose contract file contracts/{table}.contract.json is not valid",
        "proposed": "whose contract is proposed, not agreed",
        "stale": "whose contract was edited after it was agreed, so it counts as not agreed",
    }.get(state, f"whose contract is {state}")
    first = {
        "missing": f"Propose it first: {rotm} contract propose {_q(root)} --table {table} --csv <sample.csv>. Then show",
        "invalid": f"Run {rotm} contract show {_q(root)} --table {table} to see the problems, fix them, then show",
    }.get(state, "Show")
    return (f"rule-of-thumb-model contract gate: {rel} is SQL for raw table '{table}', {why}. "
            f"{first} the user contracts/{table}.contract.md, get their reply, record it with {agree}, "
            "then write the SQL.")


def check_target(root: Path, path: Path, content: "callable") -> hl.Deny | None:
    """The rules 1 to 4 for one file the call writes."""
    rel = hl.rel_in(root, path)
    if rel is None:
        return None
    m = SQL_RE.match(rel)
    if m:
        table = m.group("t")
        state = modelfolder.contract_state(root, table) if hl.TABLE_RE.match(table) else "missing"
        if state != "agreed":
            return hl.Deny(sql_message(root, rel, table, state))
        return None
    if rel.lower().endswith(hl.SQL_EXT):
        return hl.Deny(f"rule-of-thumb-model contract gate: {rel}: SQL in a model folder goes in "
                       "queries/<table>.sql and Power Query M in queries/<table>.pq.m, named after its raw table, "
                       "and only after that table's contract is agreed. Write it there instead.")
    m = CONTRACT_RE.match(rel)
    if m:
        table = m.group("t")
        state, data, _problems = modelfolder.read_contract(root, table)
        if state == "agreed":
            ag = (data or {}).get("agreement") or {}
            return hl.Deny(f"rule-of-thumb-model contract gate: {rel}: this contract was agreed by {ag.get('by')} "
                           f"on {ag.get('at')}; to change it run {hl.skill_command()} contract propose {_q(root)} "
                           f"--table {table} --force (with the changed columns), show the user "
                           f"contracts/{table}.contract.md, and record their agreement again with contract agree.")
        text = content()
        if text is not None and sets_agreed(text):
            return hl.Deny(f"rule-of-thumb-model contract gate: {rel}: this edit would mark the contract agreed. "
                           "An agreement is recorded only by rotm.py contract agree with the user's own words: "
                           f"show the user contracts/{table}.contract.md, get their reply, then run "
                           f'{hl.skill_command()} contract agree {_q(root)} --table {table} --reply "<their words>" '
                           "--by <name>.")
    return None


def sets_agreed(text: str) -> bool:
    """True when the new contract text has agreement.status 'agreed'."""
    try:
        data = json.loads(text)
    except ValueError:
        return bool(AGREED_RE.search(text))
    if isinstance(data, dict):
        ag = data.get("agreement")
        return isinstance(ag, dict) and ag.get("status") == "agreed"
    return False


def edited_text(ctx: hl.Context, path: Path) -> str | None:
    """The file text after a Write, Edit or MultiEdit (the new strings alone when the old text is not found)."""
    ti = ctx.tool_input
    if ctx.tool == "Write":
        c = ti.get("content")
        return c if isinstance(c, str) else None
    edits = ti.get("edits") if ctx.tool == "MultiEdit" else [ti]
    if not isinstance(edits, list):
        return None
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        text = None
    news = []
    for e in edits:
        if not isinstance(e, dict):
            continue
        old, new = e.get("old_string"), e.get("new_string")
        if not isinstance(new, str):
            continue
        news.append(new)
        if text is not None and isinstance(old, str) and old in text:
            text = text.replace(old, new) if e.get("replace_all") else text.replace(old, new, 1)
        else:
            text = None
    return text if text is not None else "\n".join(news)


def _is_contract_action(seg: hl.Segment, action: str) -> bool:
    args = seg.rotm or []
    return seg.rotm_script == "rotm.py" and len(args) >= 2 and args[0] == "contract" and args[1] == action


def _dir_arg(args: list[str]) -> str | None:
    """The model folder argument of `contract <action> DIR ...`, read by the subcommand table of _hooklib
    (ROTM_ARGS, v7 C-sol-1), the same reading that puts the folder in Context.roots."""
    return hl.rotm_call("rotm.py", args).positionals.get("dir")


def _same_model(seg: hl.Segment, root: Path | None) -> bool:
    """Whether a `contract` segment names the model folder `root`; True when either folder is not known, so that
    a call the hook cannot place counts against the reply, never for it."""
    if root is None:
        return True
    d = _dir_arg(seg.rotm or [])
    other = hl.root_of(hl.to_path(d, seg.cwd)) if d else None
    return other is None or hl.key(other) == hl.key(root)


def _writes_contract(path: Path | None, table: str, root: Path | None) -> bool:
    """Whether `path` is contracts/<table>.contract.json (of `root`, when the folder of `path` is a model folder)."""
    if path is None:
        return False
    if path.name.lower() != f"{table}.contract.json" or path.parent.name.lower() != "contracts":
        return False
    other = hl.root_of(path)
    return root is None or other is None or hl.key(other) == hl.key(root)


def changes_contract(segs: list[hl.Segment], table: str, root: Path | None) -> bool:
    """Whether these command segments propose the table's contract (`rotm.py contract propose --table T`) or
    write contracts/T.contract.json (a shell write the command line shows)."""
    for seg in segs:
        if seg.rotm is not None:
            if _is_contract_action(seg, "propose") and hl.option(seg.rotm, "--table") == table \
                    and _same_model(seg, root):
                return True
            continue
        if any(w.kind == "write" and _writes_contract(w.path, table, root) for w in seg.writes):
            return True
    return False


def last_change(events: list[hl.Event], table: str, root: Path | None, cwd: Path) -> int | None:
    """Transcript position of the latest assistant tool call that proposed the table's contract or edited its
    contract file (Write, Edit, MultiEdit, or a shell write), or None when the transcript shows none."""
    latest = None
    for e in events:
        if e.kind != "tool":
            continue
        here = hl.to_path(e.cwd, cwd) if e.cwd else cwd
        if e.name in ("Write", "Edit", "MultiEdit"):
            fp = e.input.get("file_path")
            hit = isinstance(fp, str) and _writes_contract(hl.to_path(fp, here or cwd), table, root)
        elif e.name in ("Bash", "PowerShell") and isinstance(e.input.get("command"), str):
            shell = "powershell" if e.name == "PowerShell" else "bash"
            hit = changes_contract(hl.analyze(e.input["command"], here or cwd, shell), table, root)
        else:
            hit = False
        if hit:
            latest = e.pos
    return latest


def agreement_guard(ctx: hl.Context, seg: hl.Segment, before: list[hl.Segment]) -> hl.Deny | None:
    """Rule 5: the reply passed to `contract agree` must not read as a refusal (lib.contract.reads_as_refusal,
    the rule rotm.py applies), and must be words the user typed in this session after the table's contract was
    last proposed or edited. `before`: the segments of the same command line that run before this one."""
    if not _is_contract_action(seg, "agree"):
        return None
    args = seg.rotm or []
    reply = hl.option(args, "--reply")
    if reply is None or not reply.strip():
        return None                                   # rotm.py refuses an empty reply itself
    from lib import contract                          # standard library only; loaded for an agree call alone
    table = hl.option(args, "--table")
    d = _dir_arg(args)
    root = hl.root_of(hl.to_path(d, seg.cwd)) if d else None
    root = root or next(iter(ctx.roots()), None)
    why = contract.reads_as_refusal(reply)
    if why:
        return hl.Deny(f"rule-of-thumb-model contract gate: the reply passed to contract agree {why}, so it reads "
                       f"as a refusal or a change request{contract.refusal_message(why)}; no agreement may be "
                       f"recorded with it{_refusal_fix(contract, why, root, table)}")
    channel = hl.option(args, "--channel")
    if channel is not None and channel != "session":
        return None                                   # task-file: rotm.py checks the file itself
    if table and changes_contract(before, table, root):
        return hl.Deny(f"rule-of-thumb-model contract gate: this command proposes or changes the contract of table "
                       f"'{table}' and records an agreement for it in the same call. Show the user "
                       f"contracts/{table}.contract.md first, wait for their reply, then record it.")
    events = hl.transcript_events(ctx.payload.get("transcript_path"))
    if events is None:
        return None
    want = hl.collapse(reply)
    found = [e for e in events if e.kind == "user" and want in hl.collapse(e.text)]
    if not found:
        return hl.Deny("rule-of-thumb-model contract gate: the reply passed to contract agree is not in any message "
                       "from the user in this session; ask the user and pass their words exactly "
                       f"(--reply \"{reply[:120]}\" was not found).")
    # The reply must come after the latest proposal or edit of this table's contract that the transcript shows
    # (round 1 verdict, C-sol-13): one reply after several proposals counts for each of them.
    latest = last_change(events, table, root, ctx.cwd) if table else None
    if latest is not None:
        eligible = [e for e in found if e.pos > latest]
        if not eligible:
            return hl.Deny(f"rule-of-thumb-model contract gate: the reply passed to contract agree --table {table} "
                           f"was typed before the latest proposal or edit of contracts/{table}.contract.json in this "
                           "session; show the user the contract as it is now (rotm.py contract show) and pass the "
                           "words of the reply they give to it.")
    else:
        # No proposal in the transcript: an agreement belongs to the contract version it was given for (live test
        # 1), so the words must have been typed after this version was proposed. Skipped when a time cannot be read.
        proposed = _proposed_at(ctx, table)
        eligible = found
        if proposed is not None:
            stamps = [(e, hl.parse_time(e.stamp)) for e in found]
            eligible = [e for e, t in stamps if t is None or t >= proposed]
            if not eligible:
                return hl.Deny("rule-of-thumb-model contract gate: the reply passed to contract agree was typed "
                               f"before this version of the contract was proposed ({proposed.isoformat()}); show the "
                               "user the contract again (its section 'Changes since version N' lists what changed) "
                               "and pass the words of their new reply.")
    # Round 4 verdict (A-aera-4, C-sol-2): a reply taken from a longer message is read in the sentence that holds it
    # and in the whole message (lib.contract.reply_refusal, the function of the task-file channel); a refusing
    # sentence that names another contract table of the model does not count against this table. One message that
    # holds the reply and reads as an agreement is enough.
    tables = modelfolder.tables(root) if root is not None else []
    held = None
    for e in eligible:
        held = contract.reply_refusal(e.text, reply, table=table, tables=tables)
        if held is None:
            return None
    text, why = held
    text = re.sub(r"\s+", " ", text).strip()
    text = text if len(text) <= 200 else text[:197] + "..."
    return hl.Deny(f"rule-of-thumb-model contract gate: the reply passed to contract agree is held in the user's "
                   f"message '{text}', which {why}, so it reads as a refusal or a change request"
                   f"{contract.refusal_message(why)}; no agreement may be recorded with it"
                   f"{_refusal_fix(contract, why, root, table)}")


def _refusal_fix(contract: Any, why: str, root: Path | None, table: str | None) -> str:
    """The end of a deny message for a reply that reads as a refusal: the fix rotm.py prints for a change request,
    a postponement or a question, else the words to pass the reply in which the user agrees."""
    if why.startswith((contract.CHANGE_REASON, contract.POSTPONE_REASON, contract.QUESTION_REASON)):
        return "; " + contract.refusal_fix(why, hl.skill_command(), _q(root) if root else "<model folder>",
                                           table or "<t>") + "."
    return (". Make the changes the user asked for, show the contract again, and pass the words of the reply in "
            "which they agree.")


def _proposed_at(ctx: hl.Context, table: str | None) -> Any:
    """When the current version of the table's contract was proposed (proposed_at), or None."""
    if not table:
        return None
    for root in ctx.roots():
        path = root / "contracts" / f"{table}.contract.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else None
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict):
            return hl.parse_time(data.get("proposed_at"))
    return None


def handle(ctx: hl.Context) -> hl.Deny | None:
    if ctx.tool in ("Write", "Edit", "MultiEdit"):
        target = ctx.file_target()
        root = hl.root_of(target)
        if root is None or target is None:
            return None
        ctx.mark_model()
        return check_target(root, target, lambda: edited_text(ctx, target))
    if not ctx.is_shell or not ctx.roots():
        return None
    ctx.mark_model()
    segs = ctx.segments()
    for i, seg in enumerate(segs):
        if seg.rotm is not None:
            refusal = agreement_guard(ctx, seg, segs[:i])
            if refusal:
                return refusal
            continue
        body = "\n".join([seg.raw, seg.code, *seg.heredocs])
        for w in seg.writes:
            if w.kind != "write":
                continue
            root = hl.root_of(w.path)
            if root is None:
                continue
            refusal = check_target(root, w.path, lambda body=body: body)
            if refusal:
                return refusal
    return None


if __name__ == "__main__":
    hl.run(NAME, handle)

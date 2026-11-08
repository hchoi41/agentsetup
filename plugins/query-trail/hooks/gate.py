"""gate.py: PreToolUse hook for Bash / PowerShell and mcp__.* calls (design spec v1 section 1.5; D-08; allowlist.json).

Blocks (exit 2, reason on stderr):
1. a query-trail script call without a valid run_state.json in its --run-dir (state.py init/activate,
   compat_suite.py, run_init.py (it creates the run; fix r2 B2-M2) and pytest excepted), or for a run other than
   the active one (view.py and secret_scan.py excepted, rounds 13 and 15b: they read a run and write only that
   run's derived view.html or its secret_scan_report.json, so /query-trail:view may scan and show any run);
2. a warehouse call that bypasses the scripts: `bq query` (only `bq query --dry_run` with one SELECT or
   WITH statement that passes the statement and function allow-list is let through), the bq write and
   job-control verbs, and the Python BigQuery client driven from the command line (`python -c`, a heredoc,
   or a .py file outside the plugin's scripts/ that imports it);
3. a vendor CLI (codex, copilot, gh copilot, and any claude, gemini or aider invocation; fix r1 B-LOW) not
   wrapped by scripts/spawn_scrubbed.py, the environment scrub that removes session tokens (spec 7.3;
   project memory 2026-10-11). Detected as the first word of each command segment, also inside
   `bash -c '...'`, so a commit message that mentions a vendor name is not blocked;
4. an MCP tool on config/allowlist.json hook_mcp_denied_tools (BigQuery execute_sql / cancel_job always;
   any BigQuery MCP tool while a run is active);
5. a Bash or PowerShell write to a protected run file (fix r1 B-M7, fix r2 B2-M5; spec v3 1.5): a `>` or `>>`
   redirection, `sed -i`, `tee`, `cp` or `mv` whose target, or `rm` / `mv` whose operand, and the PowerShell
   cmdlets Set-Content, Add-Content, Clear-Content, Out-File, New-Item, Copy-Item, Move-Item, Rename-Item and
   Remove-Item (named, positional, abbreviated or colon-joined parameters; aliases such as ac, ni, ri, del),
   whose target is a file edit_guard.py protects (script-owned, or frozen by a gate) or a whole run folder; also
   inside `bash -c '...'` and `pwsh -Command "..."`. Only what the command line shows is seen; scripts still
   refuse a changed file by hash.

The Bash matcher cannot see inside a Python script, so every script also refuses without a valid state
(spec 1.5); this hook is the policy layer, the identity's grant is the boundary (D-08).
"""
from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _hooklib as hl  # noqa: E402
import edit_guard as eg  # noqa: E402

SCRIPTS = hl.PLUGIN_ROOT / "scripts"
NO_STATE_OK = {"compat_suite.py", "state.py", "run_init.py"}
ANY_RUN_OK = {"view.py", "secret_scan.py"}  # round 13 / 15b: they read a run and write only view.html or
# secret_scan_report.json (both outside the seal), so /query-trail:view may scan and show any run, not just the active one
VENDOR_CLIS = {"codex", "copilot", "claude", "gemini", "aider"}
SHELLS = {"bash", "sh", "zsh", "dash", "pwsh", "powershell", "cmd"}
SEGMENT_OPS = {";", "&&", "||", "|", "&", "|&", "\n"}
REDIRECTS = {">", ">>", ">|", "&>", "&>>", ">&"}
PREFIX_WORDS = {"sudo", "env", "command", "exec", "nohup", "time", "xargs"}
PY_CLIENT = re.compile(r"(?is)\b(python[0-9.]*|py)(\.exe)?\b.*\bgoogle\.cloud\b.*\bbigquery\b|from\s+google\.cloud\s+import\s+bigquery")
PY_FILE_IMPORT = re.compile(r"(?m)^\s*(from\s+google\.cloud\s+import\s+bigquery|import\s+google\.cloud\.bigquery|from\s+google\.cloud\.bigquery\b)")
BQ_QUERY = re.compile(r"(?i)(^|[\s;&|(\"'])bq(\.cmd)?\s+(--?[^\s]+\s+)*query\b")
BQ_WRITE = re.compile(r"(?i)(^|[\s;&|(\"'])bq(\.cmd)?\s+(--?[^\s]+\s+)*(mk|rm|cp|load|extract|insert|update|truncate|cancel|undelete)\b")
LEADING_COMMENT = re.compile(r"^\s*(--[^\n]*\n|/\*.*?\*/|#[^\n]*\n|\s+)*", re.S)


def _tokens(cmd: str) -> list[str]:
    try:
        return shlex.split(cmd, posix=True)
    except ValueError:
        return cmd.split()


def sql_problems(sql: str, allowlist: dict[str, Any]) -> list[str]:
    """Statement and function allow-list on one SQL text (D-08; spec 2.2 P4, P5)."""
    body = LEADING_COMMENT.sub("", sql, count=1).strip().rstrip(";").strip()
    out = []
    first = (re.match(r"(?i)[a-z]+", body) or [""])[0].upper() if body else ""
    if first not in {s.upper() for s in allowlist.get("allowed_statements", ["SELECT", "WITH"])}:
        out.append(f"statement {first or '(empty)'} is not SELECT or WITH")
    for pat in allowlist.get("forbidden_statement_patterns", []) + allowlist.get("forbidden_function_patterns", []):
        if re.search(pat["regex"], body):
            out.append(f"{pat['id']}: {pat['reason']}")
    return out


def _bq_sql(tokens: list[str]) -> tuple[bool, str | None]:
    """(dry_run flag, SQL argument) of a `bq query` token list."""
    if "query" not in tokens:
        return False, None
    rest = tokens[tokens.index("query") + 1:]
    dry = any(t.lower() in ("--dry_run", "--dry_run=true", "--dryrun") for t in tokens)
    sql = [t for t in rest if not t.startswith("-")]
    return dry, (sql[0] if sql else None)


def check_warehouse(cmd: str, allowlist: dict[str, Any], cwd: str | None) -> str | None:
    """Reason to block a warehouse call that bypasses the scripts, or None."""
    if BQ_WRITE.search(cmd):
        return "bq write or job-control verb: the run is read-only and jobs go through the scripts (spec 0.2, 1.5)"
    if BQ_QUERY.search(cmd):
        dry, sql = _bq_sql(_tokens(cmd))
        if not dry:
            return "`bq query` bypasses the scripts; the bq CLI is for dry-run, show and metadata only (spec 0.2, 1.5)"
        if sql is None:
            return "`bq query --dry_run` without an inline SQL argument cannot be checked against the allow-list"
        probs = sql_problems(sql, allowlist)
        if probs:
            return "SQL fails the statement and function allow-list: " + "; ".join(probs)
    if PY_CLIENT.search(cmd):
        return "the Python BigQuery client driven from the command line bypasses the scripts (spec 1.5)"
    for t in _tokens(cmd):
        if t.lower().endswith(".py"):
            p = hl.resolve(t, cwd)
            if p.is_file() and not hl.inside(p, SCRIPTS):
                try:
                    if PY_FILE_IMPORT.search(p.read_text(encoding="utf-8", errors="replace")):
                        return f"{p.name} imports the BigQuery client outside the plugin scripts (spec 1.5)"
                except OSError:
                    pass
    return None


def segments(cmd: str, posix: bool = True) -> list[tuple[list[str], list[str]]]:
    """(words, redirection targets) per command segment; shell operators split segments (shlex, quotes kept)."""
    lex = shlex.shlex(cmd, posix=posix, punctuation_chars=";&|<>")
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        toks = list(lex)
    except ValueError:
        toks = cmd.split()
    out: list[tuple[list[str], list[str]]] = []
    words: list[str] = []
    targets: list[str] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in SEGMENT_OPS:
            out.append((words, targets))
            words, targets = [], []
        elif t in REDIRECTS or (t.endswith((">", ">>")) and t.rstrip(">").isdigit()):
            nxt = toks[i + 1] if i + 1 < len(toks) else ""
            if t == ">&" and nxt.isdigit():
                i += 1
            elif nxt and nxt not in SEGMENT_OPS:
                targets.append(nxt)
                i += 1
        elif t in ("<", "<<", "<<<"):
            i += 1
        elif t.isdigit() and i + 1 < len(toks) and toks[i + 1] in REDIRECTS:
            pass  # a file descriptor number before a redirection
        else:
            words.append(t)
        i += 1
    out.append((words, targets))
    return [(w, r) for w, r in out if w or r]


def _command(words: list[str]) -> tuple[str, list[str]]:
    """(command name without path or .exe/.cmd, its arguments), skipping VAR=value and prefix words."""
    i = 0
    while i < len(words) and (re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[i]) or words[i] in PREFIX_WORDS):
        i += 1
    if i >= len(words):
        return "", []
    name = re.sub(r"(?i)\.(exe|cmd|bat|ps1)$", "", words[i].replace("\\", "/").rsplit("/", 1)[-1]).lower()
    return name, words[i + 1:]


def _vendor_in(cmd: str, depth: int = 0) -> str | None:
    for words, _ in segments(cmd):
        if "spawn_scrubbed.py" in " ".join(words):
            continue
        name, args = _command(words)
        if name in VENDOR_CLIS or (name == "gh" and args[:1] == ["copilot"]) or \
                (name in ("npx", "pnpx", "bunx") and args and _command(args)[0] in VENDOR_CLIS):
            return name
        if name in SHELLS and depth < 3:
            for flag in ("-c", "/c", "-Command", "-command", "-lc"):
                if flag in args and args.index(flag) + 1 < len(args):
                    inner = _vendor_in(args[args.index(flag) + 1], depth + 1)
                    if inner:
                        return inner
    return None


def check_vendor(cmd: str) -> str | None:
    """Reason to block an unwrapped vendor CLI spawn, or None."""
    name = _vendor_in(cmd)
    if name:
        return (f"vendor CLI `{name}` not wrapped by scripts/spawn_scrubbed.py: the environment scrub (session tokens "
                "removed, packet-only cwd) is mandatory before any spawned CLI (spec 1.5, 7.3)")
    return None


# ---------------------------------------------------------------- Bash writes to protected run files (fix r1 B-M7)
def _operands(args: list[str]) -> list[str]:
    return [a for a in args if not a.startswith("-") or a == "-"]


# ---------------------------------------------------------------- PowerShell writes (fix r2 B2-M5)
PS_ALIASES = {"ac": "add-content", "sc": "set-content", "ni": "new-item", "cpi": "copy-item", "mi": "move-item",
              "ri": "remove-item", "rni": "rename-item", "ren": "rename-item", "clc": "clear-content"}
PS_AS_POSIX_NAMES = {"cp": "copy-item", "copy": "copy-item", "mv": "move-item", "move": "move-item",
                     "rm": "remove-item", "del": "remove-item", "erase": "remove-item", "rd": "remove-item",
                     "rmdir": "remove-item"}  # under PowerShell these aliases take PowerShell parameters
PS_VALUE_PARAMS = {
    "set-content": ("path", "literalpath", "value", "encoding", "filter", "include", "exclude", "credential", "stream"),
    "add-content": ("path", "literalpath", "value", "encoding", "filter", "include", "exclude", "credential", "stream"),
    "clear-content": ("path", "literalpath", "filter", "include", "exclude", "credential", "stream"),
    "out-file": ("filepath", "literalpath", "path", "encoding", "width", "inputobject"),
    "new-item": ("path", "name", "itemtype", "type", "value", "credential"),
    "copy-item": ("path", "literalpath", "destination", "filter", "include", "exclude", "credential", "fromsession",
                  "tosession"),
    "move-item": ("path", "literalpath", "destination", "filter", "include", "exclude", "credential"),
    "rename-item": ("path", "literalpath", "newname", "credential"),
    "remove-item": ("path", "literalpath", "filter", "include", "exclude", "credential", "stream"),
}


def _unquote(t: str) -> str:
    return t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in "'\"" else t


def _ps_params(cmdlet: str, args: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    """(named parameter values by full name, positional values) of one PowerShell cmdlet call."""
    known = PS_VALUE_PARAMS[cmdlet]
    named: dict[str, list[str]] = {}
    pos: list[str] = []
    i = 0
    while i < len(args):
        t = args[i]
        if t.startswith("-") and len(t) > 1 and not re.fullmatch(r"-[0-9.]+", t):
            name, colon, inline = t[1:].partition(":")
            low = name.lower()
            full = low if low in known else next((k for k in known if k.startswith(low)), None)
            if full is not None:
                if colon:
                    named.setdefault(full, []).append(_unquote(inline))
                elif i + 1 < len(args):
                    named.setdefault(full, []).append(_unquote(args[i + 1]))
                    i += 1
            # anything else is a switch (-Force, -Recurse, -NoNewline, -Confirm:$false, ...)
        else:
            pos.append(_unquote(t))
        i += 1
    return named, pos


def _paths(values: list[str]) -> list[str]:
    return [p.strip() for v in values for p in v.split(",") if p.strip()]


def ps_write_targets(cmdlet: str, args: list[str]) -> list[tuple[str, str]]:
    """(path, cmdlet) for each file a PowerShell write cmdlet writes, moves or removes."""
    named, pos = _ps_params(cmdlet, args)
    src = _paths(named.get("path", []) + named.get("literalpath", []) + named.get("filepath", []))
    if cmdlet in ("set-content", "add-content", "out-file"):
        return [(p, cmdlet) for p in (src or _paths(pos[:1]))]
    if cmdlet in ("clear-content", "remove-item"):
        return [(p, cmdlet) for p in (src or _paths(pos))]
    if cmdlet == "new-item":
        base = src or _paths(pos[:1]) or ["."]
        names = named.get("name", [])
        return [((b.rstrip("/\\") + "/" + names[0]) if names else b, cmdlet) for b in base]
    if cmdlet == "rename-item":
        srcs = src or _paths(pos[:1])
        rest = pos if src else pos[1:]
        new = named.get("newname", []) or rest[:1]
        out = [(s, cmdlet) for s in srcs]
        out += [(str(Path(s).parent / n), cmdlet) for s in srcs for n in new]
        return out
    # copy-item, move-item
    srcs = src or _paths(pos[:1])
    rest = pos if src else pos[1:]
    dests = _paths(named.get("destination", []) or rest[:1])
    out = []
    for d in dests:
        out.append((d, cmdlet))
        out += [(d.rstrip("/\\") + "/" + Path(s.replace("\\", "/")).name, cmdlet) for s in srcs]
    if cmdlet == "move-item":
        out += [(s, cmdlet) for s in srcs]
    return out


def _nested(name: str, args: list[str]) -> tuple[str, bool] | None:
    """(inner command, posix) of `bash -c '...'` or `pwsh -Command "..."`, else None."""
    if name not in SHELLS:
        return None
    for flag in ("-c", "/c", "-Command", "-command", "-lc", "-C"):
        if flag in args and args.index(flag) + 1 < len(args):
            return _unquote(args[args.index(flag) + 1]), name not in ("pwsh", "powershell", "cmd")
    return None


def write_targets(cmd: str, posix: bool = True, depth: int = 0) -> list[tuple[str, str]]:
    """(path as written, verb) for every file the command line writes, moves or removes."""
    out: list[tuple[str, str]] = []
    for words, redirs in segments(cmd, posix):
        out += [(_unquote(r), "redirect") for r in redirs]
        name, args = _command(words)
        inner = _nested(name, args) if depth < 3 else None
        if inner is not None:
            out += write_targets(inner[0], inner[1], depth + 1)
            continue
        cmdlet = PS_ALIASES.get(name, name)
        if not posix:
            cmdlet = PS_AS_POSIX_NAMES.get(cmdlet, cmdlet)
        if cmdlet in PS_VALUE_PARAMS:
            out += ps_write_targets(cmdlet, args)
            continue
        if name == "sed" and any(a == "--in-place" or re.fullmatch(r"-[a-zA-Z]*i\S*", a) for a in args):
            files, script_seen, skip = [], any(a in ("-e", "-f") or a.startswith("--expression") for a in args), False
            for a in args:
                if skip:
                    skip = False
                    continue
                if a in ("-e", "-f"):
                    skip = True
                    continue
                if a.startswith("-"):
                    continue
                if not script_seen:
                    script_seen = True
                    continue
                files.append(a)
            out += [(f, "sed -i") for f in files]
        elif name == "tee":
            out += [(f, "tee") for f in _operands(args)]
        elif name in ("cp", "mv", "copy", "move", "copy-item", "move-item"):
            ops = _operands(args)
            if len(ops) >= 2:
                dest, srcs = ops[-1], ops[:-1]
                out += [(dest, name) for _ in [0]]
                out += [(dest.rstrip("/\\") + "/" + Path(s).name, name) for s in srcs]
                if name in ("mv", "move", "move-item"):
                    out += [(s, name) for s in srcs]
        elif name in ("rm", "del", "erase", "rmdir", "remove-item", "unlink", "truncate"):
            out += [(f, name) for f in _operands(args)]
    return out


def check_bash_writes(cmd: str, cwd: str | None, posix: bool = True) -> str | None:
    """Reason to block a command-line write to a protected run file, or None."""
    active = hl.active_run()
    shell = "Bash" if posix else "PowerShell"
    for raw, verb in write_targets(cmd, posix):
        if not raw or raw.startswith("$") or raw in ("/dev/null", "NUL", "nul", "$null"):
            continue
        p = hl.resolve(raw, cwd)
        if verb not in ("redirect", "tee", "sed -i", "set-content", "add-content", "out-file", "clear-content") \
                and (p / "run_state.json").is_file():
            return f"{shell} write ({verb}) to {p.name}: it is a run folder; close.py deletes by manifest only"
        if p.is_dir() and verb in ("cp", "mv", "copy", "move", "copy-item", "move-item", "new-item"):
            continue  # the directory itself as a destination; its new entries are checked separately
        run = active if active is not None and hl.inside(p, active) else hl.run_root_of(p)
        if run is None:
            continue
        why = eg.protected(run, Path(hl.norm(p)).relative_to(hl.norm(run)))
        if why:
            return f"{shell} write ({verb}) to {p.name}: {why}; scripts write it, Max decides at the gate"
    return None


def check_script_state(cmd: str, cwd: str | None) -> str | None:
    """Reason to block a plugin script call without a valid run state, or None."""
    toks = _tokens(cmd)
    active = hl.active_run()
    for i, t in enumerate(toks):
        if not t.lower().endswith(".py"):
            continue
        p = hl.resolve(t, cwd)
        if not hl.inside(p, SCRIPTS):
            continue
        name = p.name
        rest = toks[i + 1:]
        if name in NO_STATE_OK and (name != "state.py" or (rest[:1] and rest[0] in ("init", "activate", "deactivate",
                                                                                      "check", "show"))
                                    or "--state" in rest):
            continue
        run_dir = None
        for j, r in enumerate(rest):
            if r == "--run-dir" and j + 1 < len(rest):
                run_dir = rest[j + 1]
            elif r.startswith("--run-dir="):
                run_dir = r.split("=", 1)[1]
        if run_dir is None:
            return f"{name} called without --run-dir: every script needs a valid run_state.json (spec 1.5)"
        rd = hl.resolve(run_dir, cwd)
        if hl.run_state(rd) is None:
            return f"{name}: no valid run_state.json in {rd.name}; start or resume the run with /query-trail:run"
        if active is not None and hl.norm(active) != hl.norm(rd) and name not in ANY_RUN_OK:
            return f"{name}: {rd.name} is not the active run ({active.name}); resume it with /query-trail:run"
    return None


def check_mcp(tool: str, allowlist: dict[str, Any]) -> str | None:
    """Reason to deny an MCP tool, or None."""
    running = hl.active_run() is not None
    for rule in allowlist.get("hook_mcp_denied_tools", []):
        if rule.get("only_during_run") and not running:
            continue
        if re.search(rule["regex"], tool):
            return f"MCP tool {tool}: {rule['reason']}"
    return None


def main() -> None:
    d = hl.read_payload()
    tool = str(d.get("tool_name") or "")
    allowlist = hl.load_allowlist()
    if tool.startswith("mcp__"):
        why = check_mcp(tool, allowlist)
        hl.deny(why) if why else hl.allow()
    cmd = str((d.get("tool_input") or {}).get("command") or "")
    if not cmd.strip():
        hl.allow()
    cwd = d.get("cwd")
    posix = tool != "PowerShell"
    for check in (lambda: check_vendor(cmd), lambda: check_warehouse(cmd, allowlist, cwd),
                  lambda: check_script_state(cmd, cwd), lambda: check_bash_writes(cmd, cwd, posix)):
        why = check()
        if why:
            hl.deny(why)
    hl.allow()


if __name__ == "__main__":
    main()

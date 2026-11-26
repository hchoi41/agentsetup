"""_hooklib.py: shared code of the four rule-of-thumb-model hooks (design section 13). Owner: M6.

Standard library only. hooks.json runs every hook with `python -I -X utf8 -B`, which hides the user
site-packages, so this file and the two core modules it imports (lib.common, lib.modelfolder, plus
lib.schemas through modelfolder) must never need openpyxl (design D2).

What it holds:
- reading the payload Claude Code sends on stdin (design 13.2);
- the model-folder lookup (a folder holding rotm_model.json, design D3 and 4.1);
- a reading of Bash and PowerShell command lines: segments, the files a segment writes, the Python code it
  runs, the paths it names (design 13.2 to 13.4); for rotm.py and formula_audit.py, the arguments each subcommand
  defines as a model folder or a file, by the table ROTM_ARGS (v7, C-sol-1);
- the session record in <state dir>/sessions/<session_id>.json (design 13.2);
- whether a status or session record still holds the model folder's inputs (model_inputs, inputs_hold; v9, round
  8 verdict C-sol-5);
- the runner: one decision per call, and the fail-open rule (design 13.1, plan F16 d): a failure before a
  model folder is identified exits 0 with no output; after, one line on stderr and exit 1, never a traceback.

These hooks are not a security boundary. They read what the command line shows. A script that builds a path at
run time, edits made in Excel outside Claude Code, and MCP tools are not seen; the audit's R-CHANGED rule
catches a changed raw sheet at the next audit (design 13.4).
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import sys
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

HOOKS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = HOOKS_DIR.parent
SKILL_DIR = PLUGIN_ROOT / "skills" / "rule-of-thumb-model"
SCRIPTS_DIR = SKILL_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from lib import common, modelfolder  # noqa: E402  (both standard library only, design 13.1)

TOOL = "rule-of-thumb-model"
MARKER = modelfolder.MARKER
WORKBOOK_EXT = (".xlsx", ".xlsm")
PATH_EXT = (".xlsx", ".xlsm", ".sql", ".m", ".csv", ".json")       # design 13.2
SQL_EXT = (".sql", ".m")
SCRIPT_SCAN_BYTES = 1_000_000                                       # design 13.2: up to 1 MB of a .py file
SESSION_KEEP_S = 7 * 24 * 3600                                      # session records older than 7 days go
PRE_KEEP_S = 6 * 3600                                               # pre-call records older than 6 hours go
NESTED_DEPTH = 2                                                    # bash -c / pwsh -Command levels read
FAIL_ENV = "ROTM_HOOK_FAIL_TEST"                                    # forces a failure, for the tests
LOG_ENV = "ROTM_HOOK_LOG"                                           # optional: one JSON line per hook run
ROTM_SCRIPTS = ("rotm.py", "formula_audit.py")
TABLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,19}$")                    # design 5: table names
PY_NAME_RE = re.compile(r"^(python[0-9.]*|pythonw|py)$")
BASH_NAMES = {"bash", "sh", "zsh", "dash"}
PS_NAMES = {"pwsh", "powershell"}
PREFIX_WORDS = {"sudo", "env", "command", "exec", "nohup", "time", "nice", "builtin", "call"}
NULL_TARGETS = {"/dev/null", "$null", "nul", "/dev/stderr", "/dev/stdout", "con"}
# Workbook write patterns (design 13.4): Python libraries and Excel COM. Case-insensitive: COM is.
WB_WRITE_RE = re.compile(r"(?i)(\.save\s*\(|\bsaveas\b|\.savecopyas\s*\(|\bto_excel\s*\(|\bexcelwriter\b|"
                         r"\bopenpyxl\s*\.\s*workbook\s*\(|\bxlsxwriter\b)")


def skill_command() -> str:
    """The rotm.py command line as an agent should type it under Git Bash (design section 2, 'Names')."""
    return f'python -P -X utf8 -B "{SKILL_DIR.as_posix()}/scripts/rotm.py"'


# ----------------------------------------------------------------------------- paths and model folders

def key(path: Path | str) -> str:
    """Comparison key of a path: absolute, normalised, case-folded on Windows."""
    return os.path.normcase(os.path.normpath(os.path.abspath(str(path))))


def to_path(text: str, cwd: Path) -> Path | None:
    """A path argument as an absolute Path (relative to `cwd`; Git Bash /c/... and ~ understood), or None."""
    text = (text or "").strip()
    if not text or "\x00" in text or len(text) > 1000:
        return None
    try:
        if text.startswith("~"):
            text = os.path.expanduser(text)
        p = common.native_path(text)
        if not p.is_absolute():
            p = Path(cwd) / p
        return Path(os.path.normpath(str(p)))
    except (OSError, ValueError):
        return None


_ROOT_CACHE: dict[str, Path | None] = {}


def root_of(path: Path | str | None) -> Path | None:
    """The model folder at or above `path` (modelfolder.find_model_root), cached per folder; None outside."""
    if path is None:
        return None
    try:
        p = Path(path)
        folder = p if p.is_dir() else p.parent
        k = key(folder)
        if k not in _ROOT_CACHE:
            _ROOT_CACHE[k] = modelfolder.find_model_root(folder)
        return _ROOT_CACHE[k]
    except (OSError, ValueError):
        return None


def rel_in(root: Path, path: Path) -> str | None:
    """`path` relative to `root` with forward slashes, or None when it lies outside."""
    rk, pk = key(root), key(path)
    if pk == rk:
        return ""
    if not pk.startswith(rk.rstrip("\\/") + os.sep):
        return None
    return os.path.relpath(os.path.abspath(str(path)), os.path.abspath(str(root))).replace("\\", "/")


def marker_above(path: str | Path | None) -> bool:
    """Minimal marker lookup that does not use lib (for the failure path, when lib itself may be the failure)."""
    if not path:
        return False
    try:
        cur = Path(os.path.abspath(common_native(str(path))))
        if not cur.is_dir():
            cur = cur.parent
        for _ in range(41):
            if (cur / MARKER).is_file():
                return True
            if cur.parent == cur:
                return False
            cur = cur.parent
    except (OSError, ValueError):
        return False
    return False


def common_native(text: str) -> str:
    m = re.match(r"^/(?:cygdrive/|mnt/)?([A-Za-z])(?:/(.*))?$", text.replace("\\", "/")) if os.name == "nt" else None
    return f"{m.group(1).upper()}:/{m.group(2) or ''}" if m else text


def workbooks_in(root: Path) -> list[Path]:
    """Workbooks directly in the model folder (design 13.5): .xlsx and .xlsm, not Excel's ~$ lock files and not
    the .bak_ copies that --in-place keeps."""
    out = []
    try:
        for p in sorted(root.iterdir()):
            n = p.name.lower()
            if p.is_file() and n.endswith(WORKBOOK_EXT) and not n.startswith("~$") and ".bak_" not in n:
                out.append(p)
    except OSError:
        pass
    return out


def signature(path: Path) -> list[int] | None:
    """[mtime_ns, size] of a file, or None when it does not exist."""
    try:
        st = path.stat()
    except OSError:
        return None
    return [st.st_mtime_ns, st.st_size]


def model_inputs(root: Path) -> dict[str, Any] | None:
    """The model folder's inputs now (modelfolder.inputs_fingerprint; v9, round 8 verdict C-sol-5), or None when
    they cannot be read (then every record counts, as in v8)."""
    try:
        return modelfolder.inputs_fingerprint(root)
    except Exception:  # noqa: BLE001 - an unreadable folder: the records are judged as in v8
        return None


def inputs_hold(rec: dict[str, Any] | None, inputs: dict[str, Any] | None) -> bool:
    """A status or session record counts for the inputs now (v9, C-sol-5): it holds no field "inputs" (written
    before v9), `inputs` is None (unknown), or its "inputs" show no change of a raw snapshot or an agreement."""
    if not isinstance(rec, dict) or inputs is None:
        return True
    return not modelfolder.inputs_changes(rec.get("inputs"), inputs)


# ----------------------------------------------------------------------------- command lines

@dataclass
class Tok:
    text: str
    kind: str = "word"          # word, redir, op, heredoc, group
    quoted: bool = False


@dataclass
class Write:
    path: Path
    kind: str                   # write or remove
    how: str                    # e.g. "redirect", "cp", "Set-Content", "python open()"


@dataclass
class Segment:
    raw: str
    shell: str
    cwd: Path
    words: list[str] = field(default_factory=list)
    name: str = ""              # the command word, lower case, without .exe
    rotm: list[str] | None = None     # arguments after rotm.py / formula_audit.py when the segment runs one
    rotm_script: str = ""
    writes: list[Write] = field(default_factory=list)
    code: str = ""              # Python (or .ps1) code the segment runs, for pattern scans
    paths: list[Path] = field(default_factory=list)
    nested: bool = False
    heredocs: list[str] = field(default_factory=list)
    spawns: bool = False        # bash -c / pwsh -Command / a group: the inner segments carry the detail


_HEREDOC_RE = re.compile(r"(?<!<)<<(-?)[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")
_STR_RE = re.compile(r"""(?:\b[rRbBuUfF]{1,2})?(['"])((?:(?!\1)[^\n]){1,400})\1""")


def _extract_heredocs(cmd: str, shell: str) -> tuple[str, list[str]]:
    """Take here-document bodies (bash) and here-strings (PowerShell) out of the command text; each is replaced
    by a placeholder \x01<n>\x01 so that a '>' inside a body is not read as a redirect."""
    lines = cmd.split("\n")
    out: list[str] = []
    bodies: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if shell == "powershell":
            m = re.search(r"@(['\"])\s*$", line)
            if m:
                q = m.group(1)
                body = []
                j = i + 1
                while j < len(lines) and not lines[j].lstrip().startswith(q + "@"):
                    body.append(lines[j])
                    j += 1
                bodies.append("\n".join(body))
                rest = lines[j].lstrip()[2:] if j < len(lines) else ""
                out.append(line[:m.start()] + f" \x01{len(bodies) - 1}\x01 " + rest)
                i = j + 1
                continue
            out.append(line)
            i += 1
            continue
        matches = list(_HEREDOC_RE.finditer(line))
        if not matches:
            out.append(line)
            i += 1
            continue
        new = []
        last = 0
        delims = []
        for m in matches:
            new.append(line[last:m.start()])
            new.append(f" \x01{len(bodies) + len(delims)}\x01 ")
            delims.append((m.group(3), m.group(1) == "-"))
            last = m.end()
        new.append(line[last:])
        out.append("".join(new))
        j = i + 1
        for word, strip_tabs in delims:
            body = []
            while j < len(lines):
                cand = lines[j].lstrip("\t") if strip_tabs else lines[j]
                if cand.strip() == word:
                    j += 1
                    break
                body.append(lines[j])
                j += 1
            bodies.append("\n".join(body))
        i = j
    return "\n".join(out), bodies


def _read_balanced(cmd: str, i: int) -> int:
    """Index of the bracket that closes cmd[i] ('(' or '{'), skipping quoted text; len(cmd) when unclosed."""
    open_c = cmd[i]
    close_c = ")" if open_c == "(" else "}"
    depth = 0
    j = i
    n = len(cmd)
    while j < n:
        c = cmd[j]
        if c in "'\"":
            k = cmd.find(c, j + 1)
            j = n if k < 0 else k + 1
            continue
        if c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return n


def tokenize(cmd: str, shell: str) -> list[tuple[list[Tok], str]]:
    """Split a command line into segments (at ; && || | & and new lines) of tokens, each with its raw text.
    Quotes are removed from words; a backslash is literal (Windows paths), except \\" inside bash double quotes.
    ( ... ) and { ... } groups become one token of kind 'group' holding the inner text."""
    segs: list[tuple[list[Tok], int, int]] = []
    cur: list[Tok] = []
    start = 0
    buf: list[str] = []
    state = {"in": False, "quoted": False}

    def flush() -> None:
        if state["in"]:
            cur.append(Tok("".join(buf), "word", bool(state["quoted"])))
        buf.clear()
        state["in"] = False
        state["quoted"] = False

    def end(at: int) -> None:
        nonlocal cur, start
        flush()
        if cur:
            segs.append((cur, start, at))
        cur = []

    i, n = 0, len(cmd)
    while i < n:
        c = cmd[i]
        if c == "\x01":
            j = cmd.find("\x01", i + 1)
            j = n if j < 0 else j
            flush()
            cur.append(Tok(cmd[i + 1:j], "heredoc"))
            i = j + 1
            continue
        if c in "'\"":
            j = i + 1
            chars = []
            while j < n and cmd[j] != c:
                ch = cmd[j]
                if c == '"' and shell == "bash" and ch == "\\" and j + 1 < n and cmd[j + 1] in '"\\$`':
                    chars.append(cmd[j + 1])
                    j += 2
                    continue
                if c == '"' and shell == "powershell" and ch == "`" and j + 1 < n:
                    chars.append(cmd[j + 1])
                    j += 2
                    continue
                chars.append(ch)
                j += 1
            buf.extend(chars)
            state["in"] = True
            state["quoted"] = True
            i = j + 1
            continue
        if (c == "\\" and shell == "bash" or c == "`" and shell == "powershell") and i + 1 < n and cmd[i + 1] == "\n":
            i += 2
            continue
        if c == "$" and i + 1 < n and cmd[i + 1] in "({":
            j = _read_balanced(cmd, i + 1)
            buf.append(cmd[i:j + 1])
            state["in"] = True
            i = j + 1
            continue
        if c in "\n;":
            end(i)
            start = i + 1
            i += 1
            continue
        if c in "({":
            j = _read_balanced(cmd, i)
            flush()
            cur.append(Tok(cmd[i + 1:j], "group"))
            i = j + 1
            continue
        if c in ")}":
            flush()
            i += 1
            continue
        if c == "&":
            if i + 1 < n and cmd[i + 1] == ">":
                flush()
                j = i + 2
                if j < n and cmd[j] == ">":
                    j += 1
                cur.append(Tok(cmd[i:j], "redir"))
                i = j
                continue
            end(i)
            i += 1
            while i < n and cmd[i] == "&":
                i += 1
            start = i
            continue
        if c == "|":
            end(i)
            i += 1
            while i < n and cmd[i] in "|&":
                i += 1
            start = i
            continue
        if c == ">" or (c in "0123456789*" and not state["in"] and i + 1 < n and cmd[i + 1] == ">"):
            flush()
            j = i if c == ">" else i + 1
            j += 1
            if j < n and cmd[j] in ">|":
                j += 1
            op = cmd[i:j]
            if j < n and cmd[j] == "&":                      # 2>&1, >&2: a stream copy, not a file
                j += 1
                while j < n and (cmd[j].isdigit() or cmd[j] == "-"):
                    j += 1
                i = j
                continue
            cur.append(Tok(op, "redir"))
            i = j
            continue
        if c == "<":
            flush()
            j = i
            while j < n and cmd[j] == "<":
                j += 1
            cur.append(Tok(cmd[i:j], "op"))
            i = j
            continue
        if c.isspace():
            flush()
            i += 1
            continue
        buf.append(c)
        state["in"] = True
        i += 1
    end(n)
    return [(toks, cmd[a:b]) for toks, a, b in segs]


def _cmd_name(word: str) -> str:
    base = re.split(r"[\\/]", word)[-1].lower()
    return base[:-4] if base.endswith(".exe") else base


def _looks_like_path(text: str) -> bool:
    t = text.strip()
    if not t or len(t) > 1000 or "\n" in t or t.startswith("-") and "/" not in t and "\\" not in t:
        return False
    return t.lower().endswith(PATH_EXT) or "/" in t or "\\" in t or t in (".", "..")


# ----------------------------------------------------------------------------- rotm.py arguments (v7, C-sol-1)
#
# The arguments of each subcommand as its parser defines them (scripts/rotm.py and the add_<cmd>_arguments
# functions of scripts/lib; formula_audit.py takes the arguments of audit). The hooks run with -I and cannot import
# those modules, so the table is written here; tests/test_v7_hooks.py compares it with the parsers. A positional
# named in ROTM_PATH_POSITIONALS and an option in ROTM_PATH_OPTIONS name a model folder or a file: analyze resolves
# each against the segment's working directory and adds it to the segment's paths whatever it looks like, so that
# a bare folder name (`rotm.py build mymodel`) puts that model folder in scope, as ./mymodel and the full path do.

@dataclass(frozen=True)
class RotmArgs:
    positionals: tuple[str, ...]            # destination names in order; each takes one word
    values: tuple[str, ...] = ()            # options that take one value (every option string)
    flags: tuple[str, ...] = ()             # options that take no value
    lists: tuple[str, ...] = ()             # options that take any number of values (nargs "*")


_OUT = ("-o", "--out")
_AUDIT_ARGS = RotmArgs(("workbook",), ("--json", "--limit", "--max-length", "--dictionary", "--use", "--spec",
                                       "--rules", "--raw-sheets", "--product"), ("--strict", "--quiet", "--no-record"))
ROTM_ARGS: dict[str, RotmArgs] = {
    "init": RotmArgs(("dir",), ("--name", "--product", "--template"), ("--force", "--list-templates")),
    "status": RotmArgs(("dir",), ("--json",)),
    "contract propose": RotmArgs(("dir",), ("--table", "--csv", "--columns", "--key", "--grain", "--source", "--rows",
                                            "--purpose", "--connector", "--system", "--where", "--unit", "--type",
                                            "--null", "--total", "--key-repeats", "--known-nulls", "--capacity",
                                            "--max-rows", "--json"), ("--force", "--allow-empty")),
    "contract agree": RotmArgs(("dir",), ("--table", "--reply", "--by", "--channel", "--source-file", "--json")),
    "contract show": RotmArgs(("dir",), ("--table", "--json")),
    "contract status": RotmArgs(("dir",), ("--json",)),
    "contract remove": RotmArgs(("dir",), ("--table", "--json")),
    "land": RotmArgs(("dir",), ("--table", "--csv", "--control", "--encoding", "--delimiter", "--pq-source", "--json"),
                     ("--power-query", "--force", "--rewrite-queries", "--keep-queries", "--strict")),
    "build": RotmArgs(("spec",), _OUT + ("--capacity", "--json"),
                      ("--force", "--strict", "--no-power-query", "--drop-hand-queries", "--quiet")),
    "theme": RotmArgs(("workbook",), _OUT + ("--dictionary", "--use", "--raw-sheets", "--calc-tolerance", "--json"),
                      ("--in-place", "--number-formats", "--borders", "--views", "--align", "--no-verify", "--dry-run",
                       "--strict", "--quiet")),
    "audit": _AUDIT_ARGS,
    "check": RotmArgs(("target",), ("--json", "--control"),
                      ("--strict", "--no-excel", "--refresh", "--accept-refresh", "--quiet")),
    "extend": RotmArgs(("target",), ("--table", "--rows", "--json"), ("--quiet",)),
    "compat": RotmArgs(("workbook",), _OUT + ("--product", "--calc-tolerance", "--json"),
                       ("--in-place", "--report-only", "--strict", "--quiet")),
    "trace": RotmArgs(("workbook", "ref"), ("--depth", "--json", "--raw-sheets")),
    "split": RotmArgs(("workbook", "ref"), _OUT + ("--limit", "--spec", "--calc-tolerance", "--json"),
                      ("--in-place", "--cell-only", "--quiet")),
    "lift": RotmArgs(("target",), _OUT + ("--lang", "--json"), ("--run", "--strict")),
    "diff": RotmArgs(("old", "new"), ("--md", "--json", "--calc-tolerance"), ("--fail-on-change", "--quiet")),
    "document": RotmArgs(("target",), _OUT + ("--json",)),
    "recalc": RotmArgs(("workbook",), ("--out", "--json"), ("--no-save", "--quiet"), ("--read",)),
    "render": RotmArgs(("workbook",), ("--out", "--sheets", "--max-rows", "--max-cols", "--json"), ("--quiet",)),
    "formula": RotmArgs(("formula",), ("--limit", "--max-length", "--product", "--json", "--allow",
                                       "--allowed-literals", "--sheet"), ("--strict",)),
    "selftest": RotmArgs((), ("--foreign", "--json"), ("--strict", "--quick", "--no-excel", "--keep", "--quiet")),
    "formula_audit.py": _AUDIT_ARGS,
}
ROTM_PATH_POSITIONALS = frozenset({"dir", "spec", "workbook", "target", "old", "new"})   # not: ref, formula
ROTM_PATH_OPTIONS = frozenset({"-o", "--out", "--csv", "--source-file"})


@dataclass
class RotmCall:
    """One rotm.py or formula_audit.py call read by the table: its table key ("" when unknown), the positionals
    by name and the values of each option given (by the option string of the table)."""
    command: str
    positionals: dict[str, str] = field(default_factory=dict)
    options: dict[str, list[str]] = field(default_factory=dict)

    def paths(self) -> list[str]:
        """The arguments that name a model folder or a file: positionals first, then the options in the order
        given; '-' (standard input or output) and empty words are left out."""
        out = [v for k, v in self.positionals.items() if k in ROTM_PATH_POSITIONALS]
        out += [v for k, vals in self.options.items() if k in ROTM_PATH_OPTIONS for v in vals]
        return [v for v in out if v.strip() and v.strip() != "-"]


def _rotm_option(spec: RotmArgs, word: str) -> tuple[str | None, str | None]:
    """(option string of the table, value given in the same word or None) of an option word; (None, None) for an
    option the subcommand does not have. Understood as argparse understands them: --name=value, a unique prefix of
    a long option, and a short option with its value attached (-oFILE)."""
    known = spec.values + spec.flags + spec.lists + ("-h", "--help")
    name, eq, val = word.partition("=")
    value = val if eq else None
    if name in known:
        return name, value
    if name.startswith("--"):
        cands = [o for o in known if o.startswith(name)]
        return (cands[0], value) if len(cands) == 1 else (None, None)
    if len(word) > 2 and word[:2] in spec.values:
        return word[:2], word[2:]
    return None, None


def rotm_call(script: str, args: list[str]) -> RotmCall:
    """Read the arguments of one rotm.py or formula_audit.py call (the words after the script) by ROTM_ARGS."""
    if script == "formula_audit.py":
        key, rest = "formula_audit.py", list(args)
    elif len(args) >= 2 and args[0] == "contract" and f"contract {args[1]}" in ROTM_ARGS:
        key, rest = f"contract {args[1]}", list(args[2:])
    elif args and args[0] != "contract" and args[0] in ROTM_ARGS and args[0] != "formula_audit.py":
        key, rest = args[0], list(args[1:])
    else:
        return RotmCall("")
    spec = ROTM_ARGS[key]
    call = RotmCall(key)
    pos: list[str] = []
    i = 0
    only_pos = False
    while i < len(rest):
        w = rest[i]
        if not only_pos and w == "--":
            only_pos = True
        elif not only_pos and w.startswith("-") and len(w) > 1:
            opt, value = _rotm_option(spec, w)
            if opt in spec.values:
                if value is None and i + 1 < len(rest):
                    i += 1
                    value = rest[i]
                if value is not None:
                    call.options.setdefault(opt, []).append(value)
            elif opt in spec.lists:
                vals = [] if value is None else [value]
                while i + 1 < len(rest) and not (rest[i + 1].startswith("-") and len(rest[i + 1]) > 1):
                    i += 1
                    vals.append(rest[i])
                call.options.setdefault(opt, []).extend(vals)
        else:
            pos.append(w)
        i += 1
    call.positionals = dict(zip(spec.positionals, pos))
    return call


def _python_parts(args: list[str]) -> tuple[str | None, list[str], str | None, bool]:
    """(script, script args, -c code, reads stdin) of the words after a python interpreter."""
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-X", "-W", "--check-hash-based-pycs"):
            i += 2
            continue
        if a == "-c":
            return None, [], args[i + 1] if i + 1 < len(args) else "", False
        if a.startswith("-c") and len(a) > 2:
            return None, [], a[2:], False
        if a == "-m":
            return None, args[i + 2:], None, False
        if a == "-":
            return None, args[i + 1:], None, True
        if a.startswith("-") and len(a) > 1:
            i += 1
            continue
        return a, args[i + 1:], None, False
    return None, [], None, True


PS_VALUE_PARAMS = ("path", "literalpath", "filepath", "destination", "value", "name", "encoding", "itemtype",
                   "filter", "include", "exclude", "delimiter", "inputobject", "credential", "stream", "newname")


def _ps_params(args: list[str], switches: set[str]) -> tuple[dict[str, str], list[str]]:
    """PowerShell-style parameters: ({name lower: value}, positionals). -Name:value is understood; a known
    switch (-Force, -Append, ...) takes no value."""
    named: dict[str, str] = {}
    pos: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("-") and len(a) > 1 and re.match(r"^-[A-Za-z]", a):
            nm, _, val = a[1:].partition(":")
            nm = nm.lower()
            if val:
                named[nm] = val
            elif any(s.startswith(nm) for s in switches) and not any(p.startswith(nm) for p in PS_VALUE_PARAMS):
                pass
            elif i + 1 < len(args):
                named[nm] = args[i + 1]
                i += 1
            i += 1
            continue
        pos.append(a)
        i += 1
    return named, pos


def _param(named: dict[str, str], *full: str) -> str | None:
    for nm, val in named.items():
        if any(f.startswith(nm) for f in full) and len(nm) >= 2:
            return val
    return None


PS_SWITCHES = {"force", "recurse", "passthru", "whatif", "confirm", "nonewline", "append", "noclobber",
               "container", "asbytestream", "notypeinformation", "useculture", "verbose", "debug"}


def _dest_targets(dest_text: str, sources: list[str], cwd: Path) -> list[Path]:
    """Files a copy or move creates: dest itself, or dest/<name of each source> when dest is a folder (an
    existing folder, or a destination written with a trailing slash)."""
    dest = to_path(dest_text, cwd)
    if dest is None:
        return []
    is_dir = dest.is_dir() or dest_text.rstrip().endswith(("/", "\\"))
    out = []
    if is_dir:
        for s in sources:
            sp = to_path(s, cwd)
            if sp is not None:
                out.append(dest / sp.name)
    return out or [dest]


_WIN_SWITCH_RE = re.compile(r"^/[A-Za-z?]+(?::\S*)?$")     # /Y, /E, /R:3, /LOG:file (xcopy, robocopy)


def _robocopy_names(src_dir: Path | None, names: list[str]) -> list[str]:
    """The file names a robocopy call copies: each name given; a name with * or ? stands for the files of the
    source folder it matches now (kept as written when none matches or the folder cannot be read)."""
    out: list[str] = []
    for n in names:
        if not any(ch in n for ch in "*?"):
            out.append(n)
            continue
        try:
            found = sorted(p.name for p in (src_dir.iterdir() if src_dir is not None else [])
                           if p.is_file() and fnmatch.fnmatch(p.name.lower(), n.lower()))
        except OSError:
            found = []
        out.extend(found or [n])
    return out


def _shell_writes(name: str, words: list[str], cwd: Path, shell: str) -> list[Write]:
    """Files a segment's own command writes or removes, by the command name (design 13.3 and 13.4)."""
    out: list[Write] = []
    args = words[1:]
    ps = shell == "powershell"

    def add(text: str | None, kind: str, how: str) -> None:
        if text is None or text.strip().lower() in NULL_TARGETS:
            return
        p = to_path(text, cwd)
        if p is not None:
            out.append(Write(p, kind, how))

    if name in ("tee", "tee-object"):
        if ps or name == "tee-object":
            named, pos = _ps_params(args, PS_SWITCHES)
            add(_param(named, "filepath", "literalpath", "path") or (pos[0] if pos else None), "write", name)
        else:
            for a in args:
                if not a.startswith("-"):
                    add(a, "write", "tee")
    elif name in ("cp", "mv", "install") and not ps:
        files = []
        target_dir = None
        i = 0
        while i < len(args):
            a = args[i]
            if a in ("-t", "--target-directory") and i + 1 < len(args):
                target_dir = args[i + 1]
                i += 2
                continue
            if a.startswith("--target-directory="):
                target_dir = a.split("=", 1)[1]
            elif a in ("-S", "--suffix", "-m", "--mode", "-o", "-g") and i + 1 < len(args):
                i += 2
                continue
            elif not a.startswith("-") or a == "-":
                files.append(a)
            i += 1
        if target_dir is not None:
            dest, sources = target_dir, files
        elif len(files) >= 2:
            dest, sources = files[-1], files[:-1]
        else:
            dest, sources = None, []
        if dest is not None:
            for t in _dest_targets(dest, sources, cwd):
                out.append(Write(t, "write", name))
        if name == "mv":
            for s in sources:
                add(s, "remove", "mv")
    elif name in ("copy-item", "move-item", "cp", "mv", "copy", "move", "cpi", "mi", "cpp"):
        named, pos = _ps_params(args, PS_SWITCHES)
        src = _param(named, "path", "literalpath") or (pos.pop(0) if pos else None)
        dest = _param(named, "destination") or (pos[0] if pos else None)
        how = "Move-Item" if name in ("move-item", "mv", "move", "mi") else "Copy-Item"
        if dest is not None:
            for t in _dest_targets(dest, [src] if src else [], cwd):
                out.append(Write(t, "write", how))
        if how == "Move-Item":
            add(src, "remove", how)
    elif name == "xcopy":                               # xcopy SOURCE [DESTINATION] [/switches]
        files = [a for a in args if not _WIN_SWITCH_RE.match(a)]
        if files:
            for t in _dest_targets(files[1] if len(files) > 1 else ".", files[:1], cwd):
                out.append(Write(t, "write", "xcopy"))
    elif name == "robocopy":                            # robocopy SOURCE_DIR DEST_DIR [FILE ...] [/switches]
        pos = [a for a in args if not _WIN_SWITCH_RE.match(a)]
        moves = any(a.lower() in ("/mov", "/move") for a in args)
        how = "robocopy /MOV" if moves else "robocopy"
        src_dir = to_path(pos[0], cwd) if pos else None
        dest_dir = to_path(pos[1], cwd) if len(pos) > 1 else None
        if dest_dir is not None:
            for n in _robocopy_names(src_dir, pos[2:] or ["*.*"]):
                out.append(Write(dest_dir / n, "write", how))
                if moves and src_dir is not None:
                    out.append(Write(src_dir / n, "remove", how))
    elif name in ("set-content", "add-content", "out-file", "new-item", "ni", "ac", "clear-content", "clc",
                  "export-csv", "epcsv"):
        named, pos = _ps_params(args, PS_SWITCHES)
        path = _param(named, "path", "literalpath", "filepath") or (pos[0] if pos else None)
        if name in ("new-item", "ni"):
            nm = _param(named, "name")
            if nm and path:
                path = path.rstrip("/\\") + "/" + nm
            elif nm and not path:
                path = nm
        how = {"ni": "New-Item", "ac": "Add-Content", "clc": "Clear-Content", "epcsv": "Export-Csv"}.get(
            name, "-".join(w.capitalize() for w in name.split("-")))
        add(path, "write", how)
    elif name == "sed" and any(a == "-i" or a.startswith("-i") or a.startswith("--in-place") for a in args):
        script_given = any(a in ("-e", "-f", "--expression", "--file") for a in args)
        nonopt = []
        skip = False
        for a in args:
            if skip:
                skip = False
                continue
            if a in ("-e", "-f", "--expression", "--file"):
                skip = True
                continue
            if not a.startswith("-"):
                nonopt.append(a)
        for a in (nonopt if script_given else nonopt[1:]):
            add(a, "write", "sed -i")
    return out


_PY_WRITE_PATTERNS = [
    # open('<path>', 'w'...) and open('<path>', mode='a')
    (re.compile(r"""\bopen\(\s*[rRbBuU]?(['"])(?P<p>[^'"\n]+)\1\s*,\s*(?:mode\s*=\s*)?[rRbBuU]?(['"])(?P<m>[^'"\n]*)\3"""),
     "python open()"),
    (re.compile(r"""\bPath\(\s*[rRbBuU]?(['"])(?P<p>[^'"\n]+)\1\s*\)\s*\.\s*(?:write_text|write_bytes|touch)\s*\("""),
     "python Path.write_text()"),
    (re.compile(r"""\.to_csv\(\s*[rRbBuU]?(['"])(?P<p>[^'"\n]+)\1"""), "pandas to_csv()"),
    (re.compile(r"""\bshutil\s*\.\s*(?:copy|copy2|copyfile)\(\s*[^,]+,\s*[rRbBuU]?(['"])(?P<p>[^'"\n]+)\1"""),
     "python shutil.copy"),
    (re.compile(r"""\bshutil\s*\.\s*move\(\s*[^,]+,\s*[rRbBuU]?(['"])(?P<p>[^'"\n]+)\1"""), "python shutil.move"),
    (re.compile(r"""\bos\s*\.\s*(?:replace|rename)\(\s*[^,]+,\s*[rRbBuU]?(['"])(?P<p>[^'"\n]+)\1"""), "python os.replace()"),
    (re.compile(r"""(?i)\[(?:system\.)?io\.file\]::(?:writeall\w*|appendall\w*)\(\s*(['"])(?P<p>[^'"\n]+)\1"""),
     ".NET File.WriteAll"),
    (re.compile(r"""(?i)\[(?:system\.)?io\.file\]::(?:copy|move)\(\s*[^,]+,\s*(['"])(?P<p>[^'"\n]+)\1"""),
     ".NET File.Copy"),
]


def code_writes(code: str, cwd: Path) -> list[Write]:
    """Text files that Python (or .NET) code writes, when the path is a literal in the call (design 13.2)."""
    out = []
    for rx, how in _PY_WRITE_PATTERNS:
        for m in rx.finditer(code):
            if how == "python open()" and not re.search(r"[wax+]", m.group("m") or ""):
                continue
            p = to_path(m.group("p"), cwd)
            if p is not None:
                out.append(Write(p, "write", how))
    return out


def code_paths(code: str, cwd: Path) -> list[Path]:
    """Path-like string literals in code (design 13.2: a .py file's text is scanned as part of the command)."""
    out = []
    for m in _STR_RE.finditer(code):
        s = m.group(2)
        if _looks_like_path(s):
            p = to_path(s, cwd)
            if p is not None:
                out.append(p)
    return out


def _read_text(path: Path, limit: int = SCRIPT_SCAN_BYTES) -> str:
    try:
        with open(path, "rb") as f:
            return f.read(limit).decode("utf-8", "replace")
    except OSError:
        return ""


def analyze(command: str, cwd: Path, shell: str = "bash", depth: int = 0) -> list[Segment]:
    """Every segment of a command line, with what it writes, runs and names. `cd` moves the folder that later
    segments resolve against; bash -c '...' and pwsh -Command '...' are read too (two levels)."""
    text, bodies = _extract_heredocs(command or "", shell)
    segments: list[Segment] = []
    here = Path(cwd)
    for toks, raw in tokenize(text, shell):
        seg = Segment(raw=raw, shell=shell, cwd=here, nested=depth > 0)
        words = [t.text for t in toks if t.kind == "word"]
        heredocs = [bodies[int(t.text)] for t in toks if t.kind == "heredoc" and t.text.isdigit()
                    and int(t.text) < len(bodies)]
        groups = [t.text for t in toks if t.kind == "group"]
        seg.heredocs = heredocs
        # command word: skip VAR=value, prefixes such as env or sudo, and PowerShell's & and . call operators
        k = 0
        while k < len(words) and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[k]) or
                                  words[k].lower() in PREFIX_WORDS or words[k] in (".",)):
            k += 1
        cmd_words = words[k:]
        seg.words = cmd_words
        seg.name = _cmd_name(cmd_words[0]) if cmd_words else ""
        # redirects: the word after > or >> (in token order)
        for idx, t in enumerate(toks):
            if t.kind == "redir":
                nxt = next((u for u in toks[idx + 1:] if u.kind == "word"), None)
                if nxt is not None and nxt.text.strip().lower() not in NULL_TARGETS:
                    p = to_path(nxt.text, here)
                    if p is not None:
                        seg.writes.append(Write(p, "write", "redirect"))
        seg.writes.extend(_shell_writes(seg.name, cmd_words, here, shell))
        # Python: script, -c code, or code on stdin (a here-document)
        code_parts: list[str] = []
        if PY_NAME_RE.match(seg.name):
            script, sargs, ccode, stdin = _python_parts(cmd_words[1:])
            if script is not None and _cmd_name(script) in ROTM_SCRIPTS:
                seg.rotm = sargs
                seg.rotm_script = _cmd_name(script)
            elif script is not None:
                sp = to_path(script, here)
                if sp is not None:
                    seg.paths.append(sp)
                    if sp.suffix.lower() == ".py":
                        code_parts.append(_read_text(sp))
            if ccode:
                code_parts.append(ccode)
            if stdin or ccode is None and script is None:
                code_parts.extend(heredocs)
        elif seg.name in ROTM_SCRIPTS:
            seg.rotm = cmd_words[1:]
            seg.rotm_script = seg.name
        elif seg.name.endswith(".ps1"):
            ps1 = to_path(cmd_words[0], here)
            if ps1 is not None:
                seg.paths.append(ps1)
                code_parts.append(_read_text(ps1))
        seg.code = "\n".join(c for c in code_parts if c)
        # nested shells: bash -c '...', pwsh -Command '...', pwsh -File x.ps1
        if depth < NESTED_DEPTH and seg.rotm is None:
            inner: list[tuple[str, str]] = []
            if seg.name in BASH_NAMES:
                for i, w in enumerate(cmd_words[1:-1], start=1):
                    if w in ("-c", "-lc", "-ic") or re.match(r"^-[a-z]*c$", w):
                        inner.append((cmd_words[i + 1], "bash"))
                        break
            elif seg.name in PS_NAMES:
                for i, w in enumerate(cmd_words[1:-1], start=1):
                    lw = w.lower()
                    if lw in ("-c", "-command", "-com", "-comm", "-comma", "-comman"):
                        inner.append((" ".join(cmd_words[i + 1:]), "powershell"))
                        break
                    if lw in ("-f", "-file"):
                        fp = to_path(cmd_words[i + 1], here)
                        if fp is not None:
                            seg.paths.append(fp)
                            inner.append((_read_text(fp), "powershell"))
                        break
            if seg.name.endswith(".ps1") and seg.code:
                inner.append((seg.code, "powershell"))
            seg.spawns = bool(inner)            # a nested shell; a ( ) or { } group stays part of its segment
            for g in groups:
                inner.append((g, shell))
            for body, sh in inner:
                for sub in analyze(body, here, sh, depth + 1):
                    segments.append(sub)
        # paths named: words that look like paths, and path literals in the code
        for w in cmd_words:
            if _looks_like_path(w):
                p = to_path(w, here)
                if p is not None:
                    seg.paths.append(p)
        # rotm.py and formula_audit.py: every argument that names a model folder or a file, whatever it looks like
        # (v7, C-sol-1: a bare folder name)
        if seg.rotm is not None:
            for text in rotm_call(seg.rotm_script, seg.rotm).paths():
                p = to_path(text, here)
                if p is not None and p not in seg.paths:
                    seg.paths.append(p)
        if seg.code:
            seg.paths.extend(code_paths(seg.code, here))
            seg.writes.extend(code_writes(seg.code, here))
        if shell == "powershell" or seg.name in ("powershell", "pwsh") or not PY_NAME_RE.match(seg.name):
            seg.writes.extend(code_writes(raw, here))
            seg.paths.extend(code_paths(raw, here))
        seg.paths.extend(w.path for w in seg.writes)
        segments.append(seg)
        # cd / Set-Location / pushd move the folder for the segments after this one
        if seg.name in ("cd", "set-location", "sl", "pushd", "chdir") and len(cmd_words) >= 2:
            target = next((w for w in cmd_words[1:] if not w.startswith("-")), None)
            p = to_path(target, here) if target else None
            if p is not None:
                here = p
    return segments


def option(args: list[str], name: str) -> str | None:
    """The value of --name X or --name=X in an argument list, else None."""
    for i, a in enumerate(args):
        if a == name and i + 1 < len(args):
            return args[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return None


# ----------------------------------------------------------------------------- transcript

def _iter_transcript(path: str | None) -> Iterator[dict[str, Any]]:
    if not path:
        return
    p = common.native_path(path)
    if not p.is_file():
        return
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                yield obj


# User-role transcript entries that the user did not type (measured on Claude Code 2.1.331 transcripts):
# background-task notices, the output of local slash commands and of ! commands, hook feedback.
NOT_TYPED_PREFIXES = ("<task-notification>", "<local-command-stdout>", "<local-command-stderr>", "<bash-stdout>",
                      "<bash-stderr>", "<system-reminder>", "Stop hook feedback:", "PreToolUse:", "PostToolUse:")


def user_texts(transcript_path: str | None) -> list[str] | None:
    """The text of every message the user typed in this session, or None when the transcript cannot be read
    (design 13.3). Left out: tool results (toolUseResult), skill text and other meta messages (isMeta), compact
    summaries, entries whose origin is not human or whose promptSource is system (task notifications), and
    entries that start with one of NOT_TYPED_PREFIXES."""
    msgs = user_messages(transcript_path)
    return None if msgs is None else [t for _ts, t in msgs]


def _typed_texts(obj: dict[str, Any]) -> list[str]:
    """The texts of one transcript entry that the user typed (the rules of user_texts); [] for any other entry."""
    if obj.get("type") != "user" or obj.get("isMeta") or obj.get("isCompactSummary") \
            or obj.get("toolUseResult") is not None or obj.get("isVisibleInTranscriptOnly"):
        return []
    origin = obj.get("origin")
    kind = origin.get("kind") if isinstance(origin, dict) else origin
    if kind not in (None, "human") or obj.get("promptSource") == "system":
        return []
    msg = obj.get("message") or {}
    if not isinstance(msg, dict) or msg.get("role", "user") != "user":
        return []
    content = msg.get("content")
    texts = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        if any(isinstance(i, dict) and i.get("type") == "tool_result" for i in content):
            return []
        texts = [i["text"] for i in content
                 if isinstance(i, dict) and i.get("type") == "text" and isinstance(i.get("text"), str)]
    return [t for t in texts if not t.lstrip().startswith(NOT_TYPED_PREFIXES)]


def user_messages(transcript_path: str | None) -> list[tuple[str | None, str]] | None:
    """(timestamp or None, text) of every message the user typed in this session (the rules of user_texts), or
    None when the transcript cannot be read."""
    events = transcript_events(transcript_path)
    if events is None:
        return None
    return [(e.stamp, e.text) for e in events if e.kind == "user"]


@dataclass
class Event:
    """One entry of the session transcript, in transcript order (`pos`): a message the user typed (kind "user",
    `text`) or a tool call the assistant made (kind "tool", `name` and `input`; `cwd` is the entry's cwd)."""
    pos: int
    kind: str
    stamp: str | None = None
    text: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    cwd: str | None = None


def transcript_events(transcript_path: str | None) -> list[Event] | None:
    """The messages the user typed and the assistant's tool calls (tool_use items) of this session, in the
    order the transcript holds them, or None when the transcript cannot be read."""
    try:
        p = common.native_path(transcript_path) if transcript_path else None
        if p is None or not p.is_file():
            return None
        out: list[Event] = []
        for pos, obj in enumerate(_iter_transcript(transcript_path)):
            stamp = obj.get("timestamp") if isinstance(obj.get("timestamp"), str) else None
            cwd = obj.get("cwd") if isinstance(obj.get("cwd"), str) else None
            if obj.get("type") == "assistant":
                content = (obj.get("message") or {}).get("content")
                for item in content if isinstance(content, list) else []:
                    if isinstance(item, dict) and item.get("type") == "tool_use" and isinstance(item.get("name"), str):
                        ti = item.get("input")
                        out.append(Event(pos, "tool", stamp, name=item["name"], input=ti if isinstance(ti, dict)
                                         else {}, cwd=cwd))
                continue
            out.extend(Event(pos, "user", stamp, text=t, cwd=cwd) for t in _typed_texts(obj))
        return out
    except (OSError, ValueError):
        return None


def parse_time(text: Any) -> Any:
    """An aware datetime from an ISO-8601 text (Z or an offset), or None."""
    import datetime as _dt
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        d = _dt.datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo is not None else None


def last_assistant_text(payload: dict[str, Any]) -> str:
    """payload['last_assistant_message'] when present, else the text of the last assistant message of the
    transcript (design 13.6)."""
    msg = payload.get("last_assistant_message")
    if isinstance(msg, str) and msg:
        return msg
    last = ""
    try:
        for obj in _iter_transcript(payload.get("transcript_path")):
            if obj.get("type") != "assistant":
                continue
            content = (obj.get("message") or {}).get("content")
            texts = []
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                texts = [i.get("text", "") for i in content if isinstance(i, dict) and i.get("type") == "text"]
            joined = "\n".join(t for t in texts if t)
            if joined:
                last = joined
    except (OSError, ValueError):
        return last
    return last


def collapse(text: str) -> str:
    """Whitespace runs as one space, ends stripped."""
    return re.sub(r"\s+", " ", text or "").strip()


# ----------------------------------------------------------------------------- session record

def sessions_dir() -> Path:
    return common.state_dir() / "sessions"


def session_path(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "unknown")[:120] or "unknown"
    return sessions_dir() / f"{safe}.json"


def _empty_session(session_id: str) -> dict[str, Any]:
    return {"rotm_hook_session": 1, "session_id": session_id, "created": common.now_iso(), "updated": None,
            "models": {}, "baseline": {}, "pre": {}, "written": {}, "write_seq": 0, "audits": {},
            "finish_refused_at_seq": None, "finish_refusals": 0}


def read_session(session_id: str) -> dict[str, Any] | None:
    """The session record, or None when this session has none yet."""
    p = session_path(session_id)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("rotm_hook_session") != 1:
        return None
    base = _empty_session(session_id)
    base.update(data)
    return base


@contextmanager
def session(session_id: str) -> Iterator[dict[str, Any]]:
    """Read, change and write back the session record under a lock file (parallel tool calls run their hooks
    at the same time). A lock older than 15 s is taken over; after 5 s of waiting the record is written anyway."""
    path = session_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".lock")
    fd = None
    deadline = time.monotonic() + 5.0
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 15:
                    lock.unlink()
                    continue
            except OSError:
                pass
            if time.monotonic() > deadline:
                break
            time.sleep(0.03)
    try:
        data = read_session(session_id) or _empty_session(session_id)
        yield data
        data["updated"] = common.now_iso()
        common.write_json(path, data)
    finally:
        if fd is not None:
            os.close(fd)
            try:
                lock.unlink()
            except OSError:
                pass


def prune_sessions() -> None:
    """Delete session records older than 7 days (design 13.2)."""
    d = sessions_dir()
    if not d.is_dir():
        return
    now = time.time()
    for p in d.glob("*.json"):
        try:
            if now - p.stat().st_mtime > SESSION_KEEP_S:
                p.unlink()
        except OSError:
            pass


def note_models(sess: dict[str, Any], roots: list[Path]) -> None:
    """Record the model folders seen and the first signature of each of their workbooks (the baseline)."""
    for r in roots:
        sess["models"].setdefault(r.as_posix(), common.now_iso())
        for wb in workbooks_in(r):
            sess["baseline"].setdefault(key(wb), {"path": wb.as_posix(), "root": r.as_posix(),
                                                  "sig": signature(wb)})


def mark_written(sess: dict[str, Any], wb: Path, root: Path, new_seq: bool) -> int:
    """Record that this session wrote `wb`; returns the write sequence number."""
    if new_seq:
        sess["write_seq"] = int(sess.get("write_seq") or 0) + 1
    seq = int(sess.get("write_seq") or 0)
    sess["written"][key(wb)] = {"path": wb.as_posix(), "root": root.as_posix(),
                                "rel": rel_in(root, wb) or wb.name, "seq": seq, "at": common.now_iso(),
                                "sig": signature(wb)}
    return seq


# ----------------------------------------------------------------------------- decisions and the runner

@dataclass
class Deny:
    """PreToolUse refusal: exit 2, the message on stderr (the model sees it)."""
    message: str


@dataclass
class Emit:
    """PostToolUse and Stop: a JSON object on stdout, exit 0."""
    obj: dict[str, Any]


class Context:
    """One hook call: the payload and what has been learnt about it."""

    def __init__(self, hook: str, payload: dict[str, Any]):
        self.hook = hook
        self.payload = payload
        self.event = str(payload.get("hook_event_name") or "")
        self.tool = str(payload.get("tool_name") or "")
        ti = payload.get("tool_input")
        self.tool_input: dict[str, Any] = ti if isinstance(ti, dict) else {}
        cwd = payload.get("cwd")
        self.cwd = (to_path(cwd, Path(os.getcwd())) if isinstance(cwd, str) and cwd else None) or Path(os.getcwd())
        self.session_id = str(payload.get("session_id") or "unknown")
        self.tool_use_id = str(payload.get("tool_use_id") or "")
        self.in_model = False
        self._segments: list[Segment] | None = None

    # the hook calls this as soon as it knows the call concerns a model folder
    def mark_model(self) -> None:
        self.in_model = True
        forced = os.environ.get(FAIL_ENV, "")
        if forced and forced.removesuffix(".py") in (self.hook, "all"):
            raise RuntimeError(f"forced failure ({FAIL_ENV})")

    @property
    def is_shell(self) -> bool:
        return self.tool in ("Bash", "PowerShell")

    def file_target(self) -> Path | None:
        """tool_input.file_path (Write, Edit, MultiEdit) or notebook_path (NotebookEdit)."""
        raw = self.tool_input.get("file_path") or self.tool_input.get("notebook_path")
        return to_path(raw, self.cwd) if isinstance(raw, str) else None

    def segments(self) -> list[Segment]:
        if self._segments is None:
            cmd = self.tool_input.get("command")
            shell = "powershell" if self.tool == "PowerShell" else "bash"
            self._segments = analyze(cmd, self.cwd, shell) if isinstance(cmd, str) else []
        return self._segments

    def roots(self) -> list[Path]:
        """Model folders of every considered path and of cwd (design 13.2), in a stable order."""
        found: dict[str, Path] = {}
        cands: list[Path | None] = [self.cwd]
        if self.is_shell:
            for s in self.segments():
                cands.append(s.cwd)
                cands.extend(s.paths)
        else:
            cands.append(self.file_target())
        for c in cands:
            r = root_of(c)
            if r is not None:
                found.setdefault(key(r), r)
        return list(found.values())


def read_payload() -> dict[str, Any]:
    """The hook input JSON from stdin (UTF-8 bytes); {} when empty or not JSON."""
    raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def _log(hook: str, ctx: Context | None, outcome: str, started: float) -> None:
    target = os.environ.get(LOG_ENV)
    if not target:
        return
    try:
        rec = {"at": common.now_iso(), "hook": hook, "event": ctx.event if ctx else "", "tool": ctx.tool if ctx else "",
               "session": ctx.session_id if ctx else "", "in_model": bool(ctx and ctx.in_model), "outcome": outcome,
               "ms": int((time.monotonic() - started) * 1000)}
        if ctx is not None:
            rec["tool_input"] = {k: (v[:300] if isinstance(v, str) else v) for k, v in ctx.tool_input.items()
                                 if k in ("command", "file_path", "notebook_path")}
            rec["payload_keys"] = sorted(ctx.payload)
        with open(target, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 - logging never changes the outcome
        pass


def _save_traceback(hook: str) -> None:
    try:
        d = common.state_dir(create=True)
        (d / "hook_last_error.txt").write_text(f"{common.now_iso()} {hook}\n{traceback.format_exc()}",
                                              encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def run(hook: str, handler: Callable[[Context], Deny | Emit | None]) -> None:
    """Run one hook and exit: 0 (allow, or JSON on stdout), 2 (refusal on stderr) or, after a failure inside
    a model folder, 1 with one line on stderr. Never a traceback (design 13.1)."""
    started = time.monotonic()
    ctx: Context | None = None
    try:
        payload = read_payload()
        ctx = Context(hook, payload)
        outcome = handler(ctx)
    except Exception as exc:  # noqa: BLE001 - fail open
        inside = bool(ctx and ctx.in_model)
        if not inside and ctx is not None:
            inside = marker_above(ctx.cwd) or marker_above(ctx.file_target() if not ctx.is_shell else None)
        _save_traceback(hook)
        _log(hook, ctx, f"error {type(exc).__name__}", started)
        if inside:
            sys.stderr.write(f"{TOOL} hook {hook}: internal error ({type(exc).__name__}); "
                             "the action was let through.\n")
            sys.stderr.flush()
            os._exit(1)
        os._exit(0)
    if isinstance(outcome, Deny):
        _log(hook, ctx, "deny", started)
        sys.stderr.write(outcome.message.rstrip() + "\n")
        sys.stderr.flush()
        sys.exit(2)
    if isinstance(outcome, Emit):
        _log(hook, ctx, "emit " + ("block" if outcome.obj.get("decision") == "block" else "context"), started)
        sys.stdout.write(json.dumps(outcome.obj, ensure_ascii=True) + "\n")
        sys.stdout.flush()
        sys.exit(0)
    _log(hook, ctx, "allow", started)
    sys.exit(0)

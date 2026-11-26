"""Shared constants, errors, findings, reports and small helpers (design sections 3.2, 3.3, 19).

Owner: CORE-IO. First version written by the architect; CORE-IO tests it and may change
internals, not the names or signatures that other modules use.

Standard library only: the hooks import this module under `python -I` (design D2).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any, Callable, Iterable

TOOL = "rule-of-thumb-model"
VERSION = "0.9.0"

# Exit codes shared by every subcommand (design section 3.2).
EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_REFUSED = 2
EXIT_NOT_AVAILABLE = 3
EXIT_INTERNAL = 4
EXIT_BUSY = 5

SEVERITIES = ("error", "warning", "info")
_SEV_TAG = {"error": "ERR ", "warning": "warn", "info": "info"}

STATE_ENV = "ROTM_STATE_DIR"


# ----------------------------------------------------------------------------- errors

class RotmError(Exception):
    """Base error. `message` says what happened; `fix` says what to change; `key` names the input key or file;
    `details` lists one problem per line (for example every schema error of a specification)."""

    exit_code = EXIT_INTERNAL

    def __init__(self, message: str, *, key: str | None = None, fix: str | None = None,
                 details: Iterable[str] | None = None):
        super().__init__(message)
        self.message = message
        self.key = key
        self.fix = fix
        self.details = [str(d) for d in details] if details else []

    def __str__(self) -> str:
        return self.line()

    def line(self) -> str:
        """One line (hooks and JSON): message, fix, then the details joined by '; '."""
        text = f"{self.key}: {self.message}" if self.key else self.message
        if self.fix:
            sep = " " if text.rstrip().endswith((".", ":", ";", "?", "!")) else ". "
            text += f"{sep}Fix: {self.fix}"
        if self.details:
            text += " | " + "; ".join(self.details)
        return text.replace("\n", " ")

    def lines(self) -> list[str]:
        """Several lines for the console: the message (and fix), then one indented line per detail."""
        head = f"{self.key}: {self.message}" if self.key else self.message
        if self.fix:
            sep = " " if head.rstrip().endswith((".", ":", ";", "?", "!")) else ". "
            head += f"{sep}Fix: {self.fix}"
        return [head.replace("\n", " ")] + ["  " + d.replace("\n", " ") for d in self.details]


class Refusal(RotmError):
    """Bad input; nothing written (exit 2)."""

    exit_code = EXIT_REFUSED


class EnvUnavailable(RotmError):
    """Something needed is missing (Excel, openpyxl, a renderer) or the result could not be verified (exit 3)."""

    exit_code = EXIT_NOT_AVAILABLE


class ExcelBusy(RotmError):
    """The Excel lock was not acquired within the timeout (exit 5)."""

    exit_code = EXIT_BUSY


# ----------------------------------------------------------------------------- time, hashing, JSON

def now_iso() -> str:
    """Local time with UTC offset, to the second, e.g. 2026-11-25T02:10:04+09:00."""
    return _dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def canonical_json(obj: Any) -> str:
    """Canonical JSON text: sorted keys, no spaces, non-ASCII kept (design section 5)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_hash(obj: Any) -> str:
    """sha256 hex of canonical_json(obj) encoded as UTF-8."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def file_sha256(path: str | os.PathLike) -> str:
    """sha256 hex of a file's bytes."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path: str | os.PathLike) -> Any:
    """Parse a JSON file (UTF-8, a BOM is accepted). Raises OSError, UnicodeDecodeError or json.JSONDecodeError."""
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def load_json_file(path: str | os.PathLike, what: str = "file") -> Any:
    """read_json for user-facing files: a missing file, bad encoding or bad JSON raises Refusal naming the file,
    the line and the column, and saying what to change."""
    p = Path(path)
    if not p.is_file():
        raise Refusal(f"{p.as_posix()}: {what} not found", fix=f"check the path of the {what}")
    try:
        return read_json(p)
    except UnicodeDecodeError as exc:
        raise Refusal(f"{p.name}: not UTF-8 text ({exc.reason} at byte {exc.start})",
                      fix="save the file as UTF-8") from None
    except json.JSONDecodeError as exc:
        raise Refusal(f"{p.name}: not valid JSON: {exc.msg} at line {exc.lineno} column {exc.colno}",
                      fix="correct the JSON at that line (a missing comma, quote or bracket is the usual cause)") from None
    except OSError as exc:
        raise Refusal(f"{p.as_posix()}: cannot be read ({type(exc).__name__}: {exc.strerror or exc})",
                      fix="close any program that holds the file, or check its permissions") from None


MARKER_FILE = "rotm_model.json"      # the model folder's marker (lib.modelfolder.MARKER); found here by a file test
PORTABLE_MODEL = "<model folder>"
PORTABLE_SKILL = "<SKILL_DIR>"
PORTABLE_STATE = "<state folder>"
# Values of these keys are hashes or digests: never changed by the portable form.
_HASH_KEY = re.compile(r"(?i)sha256|digest")
# Characters that end a path inside a message (a path may hold spaces only in its folder prefix).
_PATH_REST = r"[^\s\"'<>|*?;,()\[\]{}:]*"


def _report_model_root(target: Path) -> Path | None:
    """The model folder when `target` lies inside <model folder>/reports/ (the model folder is the nearest folder
    at or above the target that holds rotm_model.json), else None."""
    try:
        p = Path(os.path.abspath(native_path(target)))
    except (OSError, ValueError):
        return None
    for folder in p.parents:
        if (folder / MARKER_FILE).is_file():
            reports = folder / "reports"
            return folder if is_within(p, reports) and p != reports else None
    return None


def _prefix_variants(folder: Path) -> list[str]:
    """The texts a folder may appear as: resolved and as given, both slash styles, and the Git Bash form /c/..."""
    out: list[str] = []
    for base in {os.path.abspath(folder), str(Path(folder).resolve())}:
        fwd = base.replace("\\", "/").rstrip("/")
        out += [fwd, fwd.replace("/", "\\")]
        m = re.match(r"^([A-Za-z]):/(.*)$", fwd)
        if m:
            out.append(f"/{m.group(1).lower()}/{m.group(2)}")
    return [v for v in dict.fromkeys(out) if len(v) > 3]


def portable_replacer(model_root: str | os.PathLike) -> Callable[[str], str]:
    """A function that writes a text without this PC's folders: a path inside the model folder becomes relative
    to it with forward slashes, the model folder itself <model folder>, the skill folder <SKILL_DIR>, the state
    folder <state folder>. Both slash styles match; on Windows case does not matter; the longest folder first."""
    folders = [(Path(model_root), PORTABLE_MODEL, True), (skill_dir(), PORTABLE_SKILL, False),
               (state_dir(), PORTABLE_STATE, False)]
    rules: list[tuple[int, re.Pattern[str], str, bool]] = []
    flags = re.IGNORECASE if os.name == "nt" else 0
    for folder, label, relative in folders:
        for text in _prefix_variants(folder):
            parts = [re.escape(x) for x in re.split(r"[\\/]", text)]
            body = r"[\\/]".join(parts) if not text.startswith("/") else "/" + r"[\\/]".join(parts[1:])
            pattern = re.compile(r"(?<![\w.\-])" + body + r"(?:[\\/](?P<rest>" + _PATH_REST + r")|(?![\w.\-]))",
                                 flags)
            rules.append((len(text), pattern, label, relative))
    rules.sort(key=lambda r: -r[0])

    def replace(value: str) -> str:
        for _n, pattern, label, relative in rules:
            def sub(m: re.Match[str], label: str = label, relative: bool = relative) -> str:
                rest = m.group("rest")
                if rest is None:
                    return label
                rest = rest.replace("\\", "/")
                if relative and rest:
                    return rest
                return f"{label}/{rest}"
            value = pattern.sub(sub, value)
        return value

    return replace


def portable(obj: Any, model_root: str | os.PathLike) -> Any:
    """`obj` with every string (keys too) in the portable form of portable_replacer(); values of keys that name a
    hash or digest are kept as they are."""
    replace = portable_replacer(model_root)

    def walk(o: Any, key: str = "") -> Any:
        if isinstance(o, str):
            return o if _HASH_KEY.search(key) else replace(o)
        if isinstance(o, dict):
            return {(replace(k) if isinstance(k, str) else k): walk(v, str(k)) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [walk(v, key) for v in o]
        return o

    return walk(obj)


def write_json(path: str | os.PathLike, obj: Any) -> Path:
    """Write JSON (UTF-8, indent 1, trailing newline) atomically: temp file in the same folder, then replace.
    A file inside <model folder>/reports/ (reports, status.json, render.json) is written in the portable form
    (portable()): no folder of this PC is stored there. Every other file is written as given."""
    target = Path(path)
    root = _report_model_root(target)
    if root is not None:
        obj = portable(obj, root)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
            f.write("\n")
        # Windows: a reader holding the target open for a moment makes os.replace fail with PermissionError;
        # retry for up to about 2 s before giving up.
        for attempt in range(40):
            try:
                os.replace(tmp, target)
                break
            except PermissionError:
                if attempt == 39:
                    raise
                time.sleep(0.05)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return target


# ----------------------------------------------------------------------------- locations

def skill_dir() -> Path:
    """The main skill folder (scripts/lib/common.py -> scripts -> skill)."""
    return Path(__file__).resolve().parents[2]


def scripts_dir() -> Path:
    """SKILL/scripts."""
    return Path(__file__).resolve().parents[1]


def references_dir() -> Path:
    """SKILL/references."""
    return skill_dir() / "references"


def plugin_dir() -> Path | None:
    """The plugin folder when the skill sits inside it (PLUGIN/skills/<skill>), else None (skill copied alone, F02)."""
    candidate = skill_dir().parents[1]
    return candidate if (candidate / ".claude-plugin" / "plugin.json").is_file() else None


def state_dir(create: bool = False) -> Path:
    """State outside model folders (design D14): ROTM_STATE_DIR, else %LOCALAPPDATA%/rule-of-thumb-model,
    else ~/.rule-of-thumb-model. Created only when `create` is true."""
    if os.environ.get(STATE_ENV):
        base = Path(os.environ[STATE_ENV])
    elif os.environ.get("LOCALAPPDATA"):
        base = Path(os.environ["LOCALAPPDATA"]) / TOOL
    else:
        base = Path.home() / ("." + TOOL)
    if create:
        base.mkdir(parents=True, exist_ok=True)
    return base


_MSYS_RE = re.compile(r"^/(?:cygdrive/|mnt/)?([A-Za-z])(?:/(.*))?$")


def native_path(path: str | os.PathLike) -> Path:
    """A Path for this OS. On Windows a Git Bash or Cygwin path such as /c/models/x or /cygdrive/c/models/x
    becomes C:/models/x (hook payloads and agents may pass either form)."""
    text = os.fspath(path)
    if os.name == "nt":
        m = _MSYS_RE.match(text.replace("\\", "/"))
        if m:
            return Path(f"{m.group(1).upper()}:/{m.group(2) or ''}")
    return Path(text)


def norm_path(path: str | os.PathLike) -> str:
    """Absolute path with forward slashes (for messages and JSON)."""
    return native_path(path).resolve().as_posix()


def is_within(path: str | os.PathLike, folder: str | os.PathLike) -> bool:
    """True when `path` is `folder` or lies inside it (both resolved; case-insensitive on Windows)."""
    try:
        p = os.path.normcase(str(native_path(path).resolve()))
        f = os.path.normcase(str(native_path(folder).resolve()))
    except OSError:
        return False
    return p == f or p.startswith(f.rstrip("\\/") + os.sep)


# ----------------------------------------------------------------------------- A1 helpers

_CELL_RE = re.compile(r"^\$?([A-Za-z]{1,3})\$?(\d+)$")


def col_letter(n: int) -> str:
    """1 -> A, 27 -> AA."""
    if n < 1:
        raise ValueError(f"column number must be 1 or more, got {n}")
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def col_index(letters: str) -> int:
    """A -> 1, AA -> 27; a leading '$' is accepted."""
    text = letters.strip().lstrip("$")
    if not text:
        raise ValueError(f"not a column: {letters!r}")
    n = 0
    for ch in text.upper():
        if not "A" <= ch <= "Z":
            raise ValueError(f"not a column: {letters!r}")
        n = n * 26 + (ord(ch) - 64)
    return n


def split_cell(coord: str) -> tuple[int, int]:
    """'D7' or '$D$7' -> (column 4, row 7)."""
    m = _CELL_RE.match(coord.strip())
    if not m:
        raise ValueError(f"not a cell address: {coord!r}")
    return col_index(m.group(1)), int(m.group(2))


def a1(col: int, row: int, *, abs_col: bool = False, abs_row: bool = False) -> str:
    """(4, 7) -> 'D7'; with anchors '$D$7'."""
    return f"{'$' if abs_col else ''}{col_letter(col)}{'$' if abs_row else ''}{row}"


_R1C1_RE = re.compile(r"^[Rr]\d*[Cc]\d*$|^[Rr]\d*$|^[Cc]\d*$")


def quote_sheet(name: str) -> str:
    """Sheet prefix for a formula: plain when it matches [A-Za-z_][A-Za-z0-9_.]* and cannot be read as a cell
    address (D7) or an R1C1 address (R1C1, R2, C5), else quoted with '' doubling."""
    if (re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", name) and not _CELL_RE.match(name)
            and not _R1C1_RE.match(name) and name.upper() not in ("TRUE", "FALSE")):
        return name
    return "'" + name.replace("'", "''") + "'"


def range_bounds(ref: str) -> tuple[int, int, int, int]:
    """'B6:D9' or '$B$6:$D$9' or 'D7' -> (min_col, min_row, max_col, max_row). Whole columns or rows are refused."""
    text = ref.strip()
    if "!" in text:
        text = parse_ref(text)[1]
    parts = text.split(":")
    if len(parts) == 1:
        c, r = split_cell(parts[0])
        return c, r, c, r
    if len(parts) != 2:
        raise ValueError(f"not a range: {ref!r}")
    c1, r1 = split_cell(parts[0])
    c2, r2 = split_cell(parts[1])
    return min(c1, c2), min(r1, r2), max(c1, c2), max(r1, r2)


def iter_range(ref: str) -> Iterable[tuple[int, int]]:
    """Every (row, col) of a range such as 'B6:D9', row by row."""
    c1, r1, c2, r2 = range_bounds(ref)
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            yield r, c


def relpath_posix(path: str | os.PathLike, root: str | os.PathLike) -> str:
    """`path` relative to `root` with forward slashes; the absolute posix path when it lies outside `root`."""
    p = native_path(path).resolve()
    r = native_path(root).resolve()
    try:
        return p.relative_to(r).as_posix()
    except ValueError:
        return p.as_posix()


def parse_ref(ref: str) -> tuple[str | None, str]:
    """'calc!D7' -> ('calc', 'D7'); "'my sheet'!A1" -> ('my sheet', 'A1'); 'D7' -> (None, 'D7')."""
    if "!" not in ref:
        return None, ref.strip()
    sheet, cell = ref.rsplit("!", 1)
    sheet = sheet.strip()
    if sheet.startswith("'") and sheet.endswith("'"):
        sheet = sheet[1:-1].replace("''", "'")
    return sheet, cell.strip()


# ----------------------------------------------------------------------------- keys, control totals, query stamps

def key_tuple(parts: Iterable[Any]) -> tuple[Any, ...]:
    """The one key equality of the plugin (land, check, contract propose): each part tagged with its kind.
    Text is compared exactly: case-sensitive, no wildcard, never read as a number ('001' and '1' differ, 'Case'
    and 'CASE' differ, 'M*' does not match 'Monday'). Numbers compare by value (1 equals 1.0; Python compares
    int, float and Decimal exactly), as do dates, datetimes, times and booleans; a boolean is not a number; text
    '1' and number 1 differ. None stays None (an empty key part)."""
    out: list[Any] = []
    for v in parts:
        if v is None:
            out.append(None)
        elif isinstance(v, bool):
            out.append(("bool", v))
        elif isinstance(v, str):
            out.append(("text", v))
        elif isinstance(v, (int, float, Decimal)):
            out.append(("number", v))
        elif isinstance(v, _dt.datetime):
            out.append(("datetime", v))
        elif isinstance(v, _dt.date):
            out.append(("date", v))
        elif isinstance(v, _dt.time):
            out.append(("time", v))
        else:
            out.append((type(v).__name__, str(v)))
    return tuple(out)


def _decimal(v: Any) -> Decimal:
    try:
        d = v if isinstance(v, Decimal) else Decimal(v.strip() if isinstance(v, str) else str(v))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"not a number: {v!r}") from None
    if not d.is_finite():
        raise ValueError(f"not a number: {v!r}")
    return d


def control_total_ok(values: Iterable[Any], expected: Any, tolerance: Any = 0) -> tuple[bool, Decimal, Decimal]:
    """(ok, total, difference) of a control total. The sum is exact: each value as Decimal(str(value)), landed
    text as the Decimal of its text; None, empty text and booleans are left out; text that is not a number raises
    ValueError. ok when abs(total - expected) <= tolerance (both as Decimal of their text): the tolerance is the
    contract's, in the column's unit, with no term that grows with the total."""
    with localcontext() as ctx:
        ctx.prec = 100
        total = Decimal(0)
        for v in values:
            if v is None or isinstance(v, bool) or (isinstance(v, str) and not v.strip()):
                continue
            total += _decimal(v)
        difference = total - _decimal(expected)
        ok = abs(difference) <= abs(_decimal(tolerance))
    return ok, total, difference


QUERY_COMMENTS = ("--", "//")       # .sql, .pq.m
_STAMP_RE = re.compile(r"^(--|//) rotm-stamp: contract sha256:([0-9a-f]{64}); body sha256:([0-9a-f]{64})[ \t]*$")


def _query_lines(text: str) -> str:
    text = text[1:] if text.startswith("﻿") else text
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _body_sha(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def stamp_query(text: str, contract_sha256: str, comment: str) -> str:
    """The query text with the stamp as its first line:
    '<comment> rotm-stamp: contract sha256:<64 hex>; body sha256:<64 hex>'. `comment` is '--' (.sql) or '//'
    (.pq.m). The body is the text without a stamp line it already had, with LF line ends, hashed as UTF-8."""
    if comment not in QUERY_COMMENTS:
        raise ValueError(f"comment {comment!r}: use '--' (.sql) or '//' (.pq.m)")
    if not isinstance(contract_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", contract_sha256):
        raise ValueError(f"contract_sha256 {contract_sha256!r} is not 64 lower-case hexadecimal digits")
    body = _query_lines(text)
    first, sep, rest = body.partition("\n")
    if _STAMP_RE.match(first):
        body = rest
    return f"{comment} rotm-stamp: contract sha256:{contract_sha256}; body sha256:{_body_sha(body)}\n{body}"


def read_query_stamp(text: str) -> dict[str, Any] | None:
    """None when the first line is not a stamp; else {"contract_sha256", "body_sha256", "edited"}: edited is true
    when the body (the text after the stamp line, LF line ends) no longer has the recorded hash."""
    body = _query_lines(text or "")
    first, _sep, rest = body.partition("\n")
    m = _STAMP_RE.match(first)
    if not m:
        return None
    return {"contract_sha256": m.group(2), "body_sha256": m.group(3), "edited": _body_sha(rest) != m.group(3)}


# ----------------------------------------------------------------------------- findings and reports

@dataclass
class Finding:
    """One finding (design section 3.3). Findings of one rule on one sheet with one message and fix are grouped."""

    rule: str
    severity: str
    message: str
    fix: str = ""
    sheet: str | None = None
    cell: str | None = None
    count: int = 1
    cells: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES}, got {self.severity!r}")
        if self.cell and not self.cells:
            self.cells = [self.cell]

    def where(self) -> str:
        """'calc!D7 (48 cells)', 'calc', or '-'."""
        if self.sheet and self.cell:
            text = f"{self.sheet}!{self.cell}"
        elif self.sheet:
            text = self.sheet
        elif self.cell:
            text = self.cell
        else:
            text = "-"
        if self.count > 1:
            text += f" ({self.count} cells)"
        return text

    def line(self) -> str:
        """Console line: 'ERR  calc!D7 (48 cells)  F-DEPTH: <message>. Fix: <fix>'."""
        text = f"{_SEV_TAG[self.severity]} {self.where()}  {self.rule}: {self.message}"
        if self.fix:
            sep = " " if self.message.rstrip().endswith((".", ":", ";", "?", "!")) else ". "
            text += f"{sep}Fix: {self.fix}"
        return text

    def to_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "severity": self.severity, "sheet": self.sheet, "cell": self.cell,
                "count": self.count, "cells": self.cells[:20], "message": self.message, "fix": self.fix,
                "data": self.data}


def exit_code_for(errors: int, warnings: int, strict: bool) -> int:
    """Design section 3.2: errors -> 1; warnings -> 1 only with --strict; else 0."""
    if errors > 0 or (strict and warnings > 0):
        return EXIT_FINDINGS
    return EXIT_OK


@dataclass
class Report:
    """The result of one command (design section 3.3)."""

    command: str
    target: str
    strict: bool = False
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    verified: dict[str, bool] = field(default_factory=lambda: {"recalculated": False, "rendered": False})
    data: dict[str, Any] = field(default_factory=dict)
    status: str = ""
    exit_code: int = EXIT_OK
    at: str = field(default_factory=now_iso)

    def add(self, finding: Finding) -> Finding:
        """Add a finding; merge it into an existing one with the same rule, severity, sheet, message and fix."""
        for f in self.findings:
            if (f.rule, f.severity, f.sheet, f.message, f.fix) == (
                    finding.rule, finding.severity, finding.sheet, finding.message, finding.fix):
                f.count += finding.count
                for c in finding.cells:
                    if len(f.cells) < 20 and c not in f.cells:
                        f.cells.append(c)
                return f
        self.findings.append(finding)
        return finding

    def error(self, rule: str, message: str, fix: str = "", **kw: Any) -> Finding:
        return self.add(Finding(rule, "error", message, fix, **kw))

    def warning(self, rule: str, message: str, fix: str = "", **kw: Any) -> Finding:
        return self.add(Finding(rule, "warning", message, fix, **kw))

    def info(self, rule: str, message: str, fix: str = "", **kw: Any) -> Finding:
        return self.add(Finding(rule, "info", message, fix, **kw))

    def extend(self, findings: Iterable[Finding]) -> None:
        """Add several findings (grouped as add() does)."""
        for f in findings:
            self.add(f)

    def note(self, text: str) -> None:
        self.notes.append(text)

    def counts(self) -> dict[str, int]:
        out = {"errors": 0, "warnings": 0, "info": 0}
        key = {"error": "errors", "warning": "warnings", "info": "info"}
        for f in self.findings:
            out[key[f.severity]] += 1
        return out

    def finish(self, strict: bool | None = None, *, refused: bool = False, not_available: bool = False) -> int:
        """Set status and exit code; returns the exit code."""
        if strict is not None:
            self.strict = strict
        c = self.counts()
        if refused:
            self.exit_code, self.status = EXIT_REFUSED, "refused"
        elif not_available:
            self.exit_code, self.status = EXIT_NOT_AVAILABLE, "not_verified"
        else:
            self.exit_code = exit_code_for(c["errors"], c["warnings"], self.strict)
            self.status = "pass" if self.exit_code == EXIT_OK else "fail"
        return self.exit_code

    def to_dict(self) -> dict[str, Any]:
        return {"rotm_report": 1, "tool": TOOL, "version": VERSION, "command": self.command,
                "target": self.target, "at": self.at, "strict": self.strict, "exit_code": self.exit_code,
                "status": self.status, "summary": self.counts(),
                "findings": [f.to_dict() for f in self.findings], "notes": self.notes,
                "verified": self.verified, "data": self.data}

    def write_json(self, path: str | os.PathLike | None) -> None:
        """Write the report as JSON to `path`; '-' writes to stdout; None does nothing."""
        if path is None:
            return
        if str(path) == "-":
            sys.stdout.write(json.dumps(self.to_dict(), ensure_ascii=False, indent=1) + "\n")
        else:
            write_json(path, self.to_dict())

    def print_text(self, stream: Any = None, *, quiet: bool = False) -> None:
        """One line per finding, the notes, then 'errors: N  warnings: M  info: K'."""
        out = stream or sys.stdout
        if not quiet:
            for f in self.findings:
                out.write(f.line() + "\n")
            for n in self.notes:
                out.write(f"note {n}\n")
        c = self.counts()
        out.write(f"errors: {c['errors']}  warnings: {c['warnings']}  info: {c['info']}\n")

    def emit(self, json_path: str | os.PathLike | None = None, *, quiet: bool = False) -> int:
        """Print the console lines (to stderr when the JSON goes to stdout, design 3.3), write the JSON,
        return the exit code. Call finish() first."""
        stream = sys.stderr if json_path is not None and str(json_path) == "-" else sys.stdout
        self.print_text(stream, quiet=quiet)
        self.write_json(json_path)
        return self.exit_code


# ----------------------------------------------------------------------------- command wrapper

def save_traceback(exc: BaseException) -> Path | None:
    """Write the traceback of `exc` to <state dir>/last_error.txt; None when that is not possible."""
    try:
        path = state_dir(create=True) / "last_error.txt"
        text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        path.write_text(f"{now_iso()} {TOOL} {VERSION}\n{text}", encoding="utf-8")
        return path
    except OSError:
        return None


def _origin(exc: BaseException) -> str:
    """'lib.build' style name of the module where the exception was raised."""
    tb = exc.__traceback__
    last = None
    while tb is not None:
        last = tb
        tb = tb.tb_next
    if last is None:
        return "?"
    name = Path(last.tb_frame.f_code.co_filename).stem
    return f"lib.{name}" if Path(last.tb_frame.f_code.co_filename).parent.name == "lib" else name


def run_command(fn: Callable[[Any], int | None], args: Any, *, name: str = "") -> int:
    """Run a subcommand and map exceptions to exit codes (design section 3.2). Never prints a traceback."""
    label = f"rotm.py {name}".strip()
    try:
        code = fn(args)
        return EXIT_OK if code is None else int(code)
    except RotmError as exc:
        lines = exc.lines()
        sys.stderr.write(f"{label}: {lines[0]}\n")
        for extra in lines[1:]:
            sys.stderr.write(extra + "\n")
        return exc.exit_code
    except NotImplementedError as exc:
        sys.stderr.write(f"{label}: not implemented yet ({exc})\n")
        return EXIT_INTERNAL
    except KeyboardInterrupt:
        sys.stderr.write(f"{label}: interrupted\n")
        return EXIT_INTERNAL
    except Exception as exc:  # noqa: BLE001 - every other error becomes one line (F06)
        saved = save_traceback(exc)
        where = f" Details: {saved.as_posix()}" if saved else ""
        msg = str(exc).replace("\n", " ")[:300]
        sys.stderr.write(f"{label}: internal error in {_origin(exc)}: {type(exc).__name__}: {msg}.{where}\n")
        return EXIT_INTERNAL


def chunks(items: Iterable[Any], size: int) -> Iterable[list[Any]]:
    """Split an iterable into lists of at most `size` items."""
    batch: list[Any] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch

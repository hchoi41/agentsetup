"""`selftest`: pytest, strict build and audit of every example and template, the scan for machine-specific
text, Excel recalculation and check readout, renders, the orphan-Excel check, F02, F08a and F27 (design
section 16).

Steps (each runs the plugin's own commands as subprocesses, the way a user or an agent runs them):
1 pytest           python -P -m pytest scripts/tests -rfE (Excel tests skipped with --no-excel or --quick); the node
                   ids of the failing tests are in the report's data (pytest_failed) and in the step's line
2 examples         every folder of SKILL/examples, SKILL/templates, PLUGIN/templates copied to a temporary folder;
                   build --strict --force, then audit --strict; both exit 0
3 portable         every file under the plugin folder (the skill folder when it was copied alone) scanned for a
                   Windows user folder, the hub folder, an OneDrive folder, an e-mail address, a phone number,
                   and in a workbook an author other than the plugin's creator text; text files by content, each
                   zip member of .xlsx and .xlsm files by content, other files by their bytes; each hit names the
                   file, the zip member and the line; a hit is a warning, and with --strict a failure
4 recalc           check --strict on each copy: recalculated in Excel, every check 0, master 0, no error value
5 render           render on each copy: one PNG per sheet, none blank ("rendered, not looked at by code")
6 orphans          every Excel PID the job log recorded since the start is gone
7 f02_skill_alone  SKILL copied alone into <temp>/skills/rule-of-thumb-model: its selftest --quick (with --json; a
                   failure names the copy's failed steps and the failing tests of its pytest step), then a strict
                   build and audit of templates/starter there
8 f08a_existing    theme, compat and split on copies of workbooks the plugin did not make (--foreign DIR or
                   ROTM_FOREIGN_DIR, plus a fixture with a chart, a table, names, conditional formats and
                   validation): no unintended difference by code against an Excel-saved copy of the original
                   (cells that hold a volatile function or depend on one keep their formula compared, not their
                   value), and the parts kept; or the command refused and said why. A run that wrote nothing is
                   a verified no-op (no finding of severity error), an expected refusal (the fixture names the
                   rule id of each split it makes, F08A_FIXTURE_SPLITS; for a foreign workbook it comes from the
                   formula, f08a_split_expectation), or a failure that names the rule id (v4, A-aera-17)
9 f27_power_query  a model from the starter template whose raw sheet is a Power Query table over a local CSV:
                   landed, built and audited in strict mode; the CSV grown, refreshed (uncovered rows named),
                   extended, accepted with a control total, audited; then shrunk, refreshed (helper rows below
                   the raw rows named), extended, accepted, audited; then rebuilt and checked in strict mode (the
                   accepted total survives the rebuild) and audited; one report line per command. After each
                   accepted refresh, the spend block's total on calc (the cell the manifest names), read from the
                   workbook Excel saved, must equal the total computed in Python from the new CSV over the block's
                   categories and months (v3, [A-aera-12, C-sol-9, C-sol-10] part 4)

Exit: 0 when every step that ran passed (and, with --strict, no Excel step was skipped); 1 when a step failed;
3 when an Excel step was skipped under --strict, and when pytest is not installed (one line, N_PYTEST, before any
step). --quick runs steps 1 to 3 only (the others are "not run", which never changes the exit code). The JSON
report's "strict" field is the --strict option (v3, J-6). Owner: INTEGRATOR.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from . import common

STEPS = ("pytest", "examples", "portable", "recalc", "render", "orphans", "f02_skill_alone", "f08a_existing",
         "f27_power_query")
QUICK_STEPS = ("pytest", "examples", "portable")
EXCEL_STEPS = ("recalc", "render", "orphans", "f08a_existing", "f27_power_query")
COPY_SKIP = ("__pycache__", ".pytest_cache")
F08A_FOREIGN_COUNT = 5            # foreign workbooks used by step 8 (plan F08a: at least 5)
F27_BLOCK = "spend"               # step 9 reads this block's total: the starter's grid by category and month
F27_TOLERANCE = Decimal("0.001")  # the default check_tolerance, in the unit of the amounts
# v3, [C-sol-16]: the one line `rotm.py selftest` prints when pytest is missing (exit 3, no step runs)
N_PYTEST = ("rotm.py selftest needs the Python package pytest, which is not installed (python -m pip install pytest); "
            "the other commands do not need it")


# ----------------------------------------------------------------------------- small helpers

class StepFailed(Exception):
    """A step's pass condition does not hold; the message says which and why."""


def _skill() -> Path:
    return common.skill_dir()


def _plugin() -> Path | None:
    p = _skill().parents[1]
    return p if (p / ".claude-plugin" / "plugin.json").is_file() else None


def _env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["ROTM_STATE_DIR"] = str(common.state_dir())          # one job log for the selftest and its children
    if extra:
        env.update(extra)
    return env


def _rotm(args: list[str], *, env: dict[str, str] | None = None, timeout: float = 1800,
          skill: Path | None = None) -> subprocess.CompletedProcess[str]:
    script = (skill or _skill()) / "scripts" / "rotm.py"
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(script), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", env=env or _env(), timeout=timeout)


def _tail(proc: subprocess.CompletedProcess[str], n: int = 6) -> str:
    lines = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
    return " | ".join(line.strip() for line in lines[-n:])


def _read_report(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


NAMES_SHOWN = 10                  # failing tests named in a step's line; the rest are counted


def node_name(node: str) -> str:
    """A pytest node id with the part before the first '::' (the test file) written with forward slashes; the part
    after it is kept as printed. pytest prints the file part relative to the current folder with the system's
    separator when its root folder is another folder (a pytest configuration file above the temporary folder), so
    the same test can be printed as scripts\\tests\\x.py::t or scripts/tests/x.py::t (v5, A-aera-8 and C-sol-8)."""
    file_part, sep, rest = node.partition("::")
    return file_part.replace("\\", "/") + sep + rest


def failing_tests(output: str) -> list[str]:
    """The node ids of the "FAILED <node id> - ..." and "ERROR <node id> - ..." lines of pytest -rfE output
    (v4, A-aera-7), in the order pytest printed them, each written by node_name (v5: the file part with forward
    slashes) and listed once."""
    out: list[str] = []
    for line in output.splitlines():
        for word in ("FAILED ", "ERROR "):
            if line.startswith(word):
                node = node_name(line[len(word):].split(" - ", 1)[0].strip())
                if node and node not in out:
                    out.append(node)
    return out


def _names(items: list[str]) -> str:
    shown = ", ".join(items[:NAMES_SHOWN])
    return shown + (f" and {len(items) - NAMES_SHOWN} more" if len(items) > NAMES_SHOWN else "")


def _copy_failures(report_path: Path, proc: subprocess.CompletedProcess[str]) -> str:
    """Step f02 (v4, A-aera-7): the failed steps of the copy's selftest, the failing tests of its pytest step and
    the first failure's message, read from the copy's JSON report; the copy's last output lines when there is none."""
    rep = _read_report(report_path)
    data = rep.get("data") or {}
    steps = data.get("steps") or {}
    if not isinstance(steps, dict) or not steps:
        return _tail(proc, 10)
    failed_steps = [k for k, v in steps.items() if v == "fail"]
    tests: list[str] = []
    for t in data.get("pytest_failed") or []:
        if isinstance(t, str) and node_name(t) not in tests:
            tests.append(node_name(t))
    first = next((f.get("message") or "" for f in rep.get("findings") or [] if f.get("rule") == "SELFTEST-FAIL"), "")
    text = f"failed steps: {', '.join(failed_steps) or 'none'}; failing tests of its pytest step: "
    text += _names(tests) if tests else ("none" if "pytest" not in failed_steps else "not named (see its line)")
    return text + (f"; first failure: {first[:400]}" if first else "")


def _copytree(src: Path, dest: Path) -> Path:
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns(*COPY_SKIP))
    return dest


def model_folders() -> list[Path]:
    """Every shipped example and template: folders with a model.spec.json and a rotm_model.json."""
    roots = [_skill() / "examples", _skill() / "templates"]
    plugin = _plugin()
    if plugin is not None:
        roots.append(plugin / "templates")
    out: list[Path] = []
    for root in roots:
        if root.is_dir():
            out += sorted(p for p in root.iterdir() if (p / "model.spec.json").is_file() and (p / "rotm_model.json").is_file())
    return out


def default_foreign_dir() -> Path | None:
    """ROTM_FOREIGN_DIR, else the project's build/foreign folder when the plugin sits in its build tree."""
    env = os.environ.get("ROTM_FOREIGN_DIR")
    if env:
        return Path(env)
    candidate = _skill().parents[2] / "build" / "foreign"
    return candidate if candidate.is_dir() else None


# ----------------------------------------------------------------------------- the portable scan (O-1)
#
# The plugin is published to a public repository whose publish gate refuses files that hold a machine-specific
# path, an e-mail address or a phone number. The patterns are written as character classes and joined parts, so
# that this source file does not match them itself (a test scans this file).

PORTABLE_SKIP_DIRS = (".git",) + COPY_SKIP          # version-control data and bytecode caches are not shipped
PORTABLE_ZIP_SUFFIXES = (".xlsx", ".xlsm")
PORTABLE_MAX_FINDINGS = 200                         # findings written to the report; every hit is in data
PORTABLE_KINDS = {
    "user_folder": "a Windows user folder",
    "hub_folder": "the hub folder",
    "onedrive_folder": "an OneDrive folder",
    "email": "an e-mail address",
    "phone": "a phone number",
    "workbook_author": "a workbook author other than the plugin's creator text",
    "unreadable": "a file that cannot be read",
}
_SLASHES = r"[\\/]+"
_PORTABLE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # a drive letter and a colon, then the Users folder and a name; a placeholder such as <name> is not a name
    ("user_folder", re.compile(r"(?<![A-Za-z])[a-z]:" + _SLASHES + "users" + _SLASHES
                               + r"[^\\/\s\"'<>%$*?|:;,()\[\]{}]+", re.A | re.I)),
    # drive C, a colon, then a folder whose name starts with three zeros and a dot
    ("hub_folder", re.compile(r"(?<![A-Za-z])c:" + _SLASHES + "0{3}" + r"\.", re.A | re.I)),
    # the OneDrive folder: the word followed by a path separator, or by a space, a hyphen and a space
    ("onedrive_folder", re.compile("one" + "drive" + r"(?:[\\/]| - )", re.A | re.I)),
    ("email", re.compile(r"(?<![\w.%+-])[A-Za-z0-9][\w.%+-]*" + "@" + r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*"
                         r"\.[A-Za-z]{2,}(?![\w-])", re.A)),
    # Phone numbers in the written forms: with a country code; with a leading 0 and two separators; an area code
    # in brackets; three, three and four digits; a Korean mobile number without separators. Dates (four digits
    # first), times, versions, sha256 digests and amounts (no separators, or commas) do not match.
    ("phone", re.compile(r"(?<![\w.+/-])(?:"
                         r"\+\d{1,3}[ -]?\d{1,4}[ -]\d{3,4}[ -]\d{4}"
                         r"|0\d{1,2}[ -]\d{3,4}[ -]\d{4}"
                         r"|\(\d{2,4}\) ?\d{3,4}-\d{4}"
                         r"|\d{3}-\d{3}-\d{4}"
                         r"|01[016789]\d{7,8}"
                         r")(?![\w.-])", re.A)),
)
_CORE_FIELDS = re.compile(r"<(dc:creator|cp:lastModifiedBy)(?:\s[^>]*)?(?:/>|>(.*?)</\1>)", re.S)


@dataclass
class PortableHit:
    """One hit of the portable scan: the file (relative to the scanned folder, forward slashes), the zip member
    of a workbook, the line (text) or the byte offset (binary), the kind and the matched text."""

    file: str
    member: str | None
    line: int | None
    offset: int | None
    kind: str
    text: str

    def where(self) -> str:
        place = self.file + (f" [{self.member}]" if self.member else "")
        if self.line is not None:
            return f"{place} line {self.line}"
        return f"{place} byte {self.offset}" if self.offset is not None else place

    def as_dict(self) -> dict[str, Any]:
        return {"file": self.file, "member": self.member, "line": self.line, "offset": self.offset,
                "kind": self.kind, "text": self.text}


def _decode_text(data: bytes) -> str | None:
    """The bytes as text (UTF-8, or UTF-16 with a byte-order mark), or None for binary data."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            return None
    if b"\x00" in data[:8192]:
        return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _binary_parts(data: bytes) -> list[tuple[int, bytes]]:
    """(byte offset, bytes) parts of a binary file to scan. A PNG file is read chunk by chunk: the compressed
    image data (IDAT) holds pixels, not text, and random bytes there can look like an address, so it is left out;
    a compressed text chunk (zTXt, or iTXt with its compression flag set) is scanned after decompression, at the
    offset of its chunk. Any other binary file is scanned whole."""
    import zlib
    if not data.startswith(_PNG_SIGNATURE):
        return [(0, data)]
    parts: list[tuple[int, bytes]] = [(0, data[:8])]
    pos = 8
    while pos + 8 <= len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if kind != b"IDAT":
            parts.append((pos, data[pos:pos + 12 + length]))
            try:
                if kind == b"zTXt" and b"\x00" in body:
                    parts.append((pos, zlib.decompress(body[body.index(b"\x00") + 2:])))
                elif kind == b"iTXt" and b"\x00" in body and body[body.index(b"\x00") + 1:][:1] == b"\x01":
                    rest = body[body.index(b"\x00") + 3:]     # language tag, NUL, translated keyword, NUL, text
                    rest = rest.split(b"\x00", 2)[-1] if rest.count(b"\x00") >= 2 else rest
                    parts.append((pos, zlib.decompress(rest)))
            except zlib.error:
                pass
        pos += 12 + length
        if kind == b"IEND":
            break
    if pos < len(data):
        parts.append((pos, data[pos:]))                  # bytes after IEND, or after the last chunk read
    return parts


def _scan_data(data: bytes, file: str, member: str | None) -> list[PortableHit]:
    text = _decode_text(data)
    hits: list[PortableHit] = []
    if text is not None:
        for kind, pattern in _PORTABLE_PATTERNS:
            for m in pattern.finditer(text):
                hits.append(PortableHit(file, member, text.count("\n", 0, m.start()) + 1, None, kind,
                                        m.group(0)[:120]))
        return sorted(hits, key=lambda h: h.line or 0)
    for base, part in _binary_parts(data):
        chars = part.decode("latin-1")          # one character per byte: a match position is a byte offset
        for kind, pattern in _PORTABLE_PATTERNS:
            for m in pattern.finditer(chars):
                hits.append(PortableHit(file, member, None, base + m.start(), kind, m.group(0)[:120]))
    return sorted(hits, key=lambda h: h.offset or 0)


def _scan_core_properties(data: bytes, file: str, member: str) -> list[PortableHit]:
    """docProps/core.xml: dc:creator and cp:lastModifiedBy must be empty or the plugin's creator text."""
    text = data.decode("utf-8", errors="replace")
    hits = []
    for m in _CORE_FIELDS.finditer(text):
        value = (m.group(2) or "").strip()
        if value and not (value == common.TOOL or value.startswith(common.TOOL + " ")):
            hits.append(PortableHit(file, member, text.count("\n", 0, m.start()) + 1, None, "workbook_author",
                                    m.group(1)))           # the value itself is not repeated: it may be a name
    return hits


def portable_files(root: str | Path) -> list[Path]:
    """Every file under `root`, without the folders in PORTABLE_SKIP_DIRS, sorted."""
    base = Path(root)
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in PORTABLE_SKIP_DIRS)
        out += [Path(dirpath) / f for f in sorted(filenames)]
    return out


def portable_scan_file(path: str | Path, root: str | Path | None = None) -> list[PortableHit]:
    """The hits in one file: a workbook (.xlsx, .xlsm) member by member, any other file as text or bytes."""
    p = Path(path)
    rel = p.relative_to(root).as_posix() if root is not None else p.name
    try:
        data = p.read_bytes()
    except OSError as exc:
        return [PortableHit(rel, None, None, None, "unreadable", f"{type(exc).__name__}: {exc}")]
    if p.suffix.lower() in PORTABLE_ZIP_SUFFIXES and zipfile.is_zipfile(p):
        hits: list[PortableHit] = []
        with zipfile.ZipFile(p) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                member = z.read(info)
                hits += _scan_data(member, rel, info.filename)
                if info.filename == "docProps/core.xml":
                    hits += _scan_core_properties(member, rel, info.filename)
        return hits
    return _scan_data(data, rel, None)


def portable_scan(root: str | Path) -> list[PortableHit]:
    """Every hit under `root` (see portable_files and portable_scan_file)."""
    hits: list[PortableHit] = []
    for f in portable_files(root):
        hits += portable_scan_file(f, root)
    return hits


def _portable_root() -> Path:
    """The plugin folder; the skill folder when it was copied alone (F02)."""
    return common.plugin_dir() or _skill()


def _portable_fix(kind: str) -> str:
    if kind in ("user_folder", "hub_folder", "onedrive_folder"):
        return ("write the path relative to the model folder or the plugin folder, or as a placeholder such as "
                "<model folder> or <plugin folder>; a shipped model folder is regenerated with the plugin's commands")
    if kind == "workbook_author":
        return ("save the workbook through the plugin's commands (build, or an Excel job such as recalc), which "
                "write the plugin's creator text, or clear the author fields of the file")
    if kind == "unreadable":
        return "check the file's permissions; an unreadable file cannot be checked before publishing"
    return "remove it, or replace it with a placeholder"


def exit_code_for_steps(results: dict[str, str], *, strict: bool) -> int:
    """results: step -> "pass" | "fail" | "skipped" | "not run"."""
    if any(v == "fail" for v in results.values()):
        return common.EXIT_FINDINGS
    if strict and any(v == "skipped" and k in EXCEL_STEPS + ("f02_skill_alone",) for k, v in results.items()):
        return common.EXIT_NOT_AVAILABLE
    return common.EXIT_OK


# ----------------------------------------------------------------------------- the steps

class _Run:
    def __init__(self, report: Any, work: Path, *, strict: bool, quick: bool, excel_ok: bool, excel_why: str,
                 foreign_dir: Path | None):
        self.report = report
        self.work = work
        self.strict = strict
        self.quick = quick
        self.excel_ok = excel_ok
        self.excel_why = excel_why
        self.foreign_dir = foreign_dir
        self.started = common.now_iso()
        self.copies: list[Path] = []
        self.results: dict[str, str] = {}
        # step 8: workbook -> the cells to split, each with the rule id of the refusal it must end with (or None)
        self.f08a_splits: dict[Path, tuple[tuple[str, str, str | None], ...]] = {}

    # --- 1
    def step_pytest(self) -> str:
        env = _env({"ROTM_NO_EXCEL": "1"} if (self.quick or not self.excel_ok) else None)
        # -rfE: one "FAILED <node id> - ..." or "ERROR <node id> - ..." line per failing test (v4, A-aera-7)
        proc = subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", "-m", "pytest", "scripts/tests", "-q",
                               "-p", "no:cacheprovider", "-rfE"], cwd=_skill(), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env, timeout=7200)
        last = (proc.stdout or "").strip().splitlines()[-1:] or [""]
        failed = failing_tests(proc.stdout or "")
        self.report.data["pytest_failed"] = failed
        if proc.returncode != 0:
            raise StepFailed(f"pytest exit {proc.returncode}: {last[0]}"
                             + (f"; failing tests: {_names(failed)}" if failed else ""))
        excel_note = "Excel tests skipped" if (self.quick or not self.excel_ok) else "Excel tests included"
        return f"pytest: {last[0]} ({excel_note})"

    # --- 2
    def step_examples(self) -> str:
        folders = model_folders()
        if not folders:
            raise StepFailed("no example or template folder found (SKILL/examples, SKILL/templates, PLUGIN/templates)")
        root = self.work / "models"
        root.mkdir(parents=True, exist_ok=True)
        env = _env({"ROTM_EXAMPLE_ROOTS": str(root)})
        done = []
        for src in folders:
            dest = _copytree(src, root / f"{src.parent.name}__{src.name}")
            self.copies.append(dest)
            for args, what in ((["build", str(dest), "--strict", "--force", "--quiet"], "build --strict"),
                               (["audit", str(dest), "--strict", "--quiet"], "audit --strict")):
                proc = _rotm(args, env=env)
                if proc.returncode != 0:
                    raise StepFailed(f"{src.parent.name}/{src.name}: {what} exit {proc.returncode}: {_tail(proc)}")
            done.append(f"{src.parent.name}/{src.name}")
        return f"{len(done)} folders built and audited in strict mode, exit 0 each: {', '.join(done)}"

    # --- 3
    def step_portable(self) -> str:
        root = _portable_root()
        files = portable_files(root)
        hits: list[PortableHit] = []
        for f in files:
            hits += portable_scan_file(f, root)
        self.report.data["portable_hits"] = [h.as_dict() for h in hits]
        scope = (f"{len(files)} files under {root.name}/ (text by content, each zip member of .xlsx and .xlsm, "
                 f"other files by their bytes; folders {', '.join(PORTABLE_SKIP_DIRS)} left out)")
        if not hits:
            return (f"{scope}: no Windows user folder, hub folder, OneDrive folder, e-mail address, phone number "
                    "or workbook author other than the plugin's creator text")
        add = self.report.error if self.strict else self.report.warning
        for h in hits[:PORTABLE_MAX_FINDINGS]:
            add("SELFTEST-PORTABLE", f"{h.where()}: {PORTABLE_KINDS.get(h.kind, h.kind)}: {h.text}",
                _portable_fix(h.kind))
        if len(hits) > PORTABLE_MAX_FINDINGS:
            self.report.note(f"portable: {len(hits) - PORTABLE_MAX_FINDINGS} more hits are listed in the report's "
                             "data (portable_hits) only")
        kinds: dict[str, int] = {}
        for h in hits:
            kinds[h.kind] = kinds.get(h.kind, 0) + 1
        hit_files = len({h.file for h in hits})
        summary = (f"{scope}: {len(hits)} hits in {hit_files} files ("
                   + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())) + f"); first: {hits[0].where()}")
        if self.strict:
            raise StepFailed(summary)
        return summary + "; warnings only (with --strict this step fails)"

    # --- 4
    def step_recalc(self) -> str:
        if not self.copies:
            raise StepFailed("step 2 made no copies to recalculate")
        env = _env({"ROTM_EXAMPLE_ROOTS": str(self.work / "models")})
        lines = []
        for dest in self.copies:
            out = self.work / f"check_{dest.name}.json"
            proc = _rotm(["check", str(dest), "--strict", "--quiet", "--json", str(out)], env=env)
            rep = _read_report(out)
            data = rep.get("data") or {}
            master = (data.get("master") or {}).get("value")
            checks = data.get("checks") or []
            failed = [c.get("id") for c in checks if c.get("state") != "pass"]
            errors = data.get("error_cells") or []
            if proc.returncode != 0 or master != 0 or failed or errors or not (rep.get("verified") or {}).get("recalculated"):
                raise StepFailed(f"{dest.name}: check exit {proc.returncode}, master {master!r}, failed {failed}, "
                                 f"error cells {errors[:5]}: {_tail(proc)}")
            # An independent recalculation in Python (M8): lift reproduces every helper column, block and check from
            # raw/*.csv and ties out against the values Excel saved.
            lout = self.work / f"lift_{dest.name}.json"
            lp = _rotm(["lift", str(dest), "--run", "--json", str(lout)], env=env)
            lrep = _read_report(lout)
            tie = next((n for n in lrep.get("notes") or [] if n.startswith("tie-out:")), "")
            if lp.returncode != 0:
                raise StepFailed(f"{dest.name}: lift --run exit {lp.returncode} ({tie or _tail(lp)})")
            cells = tie.split(" of ")[0].replace("tie-out: ", "") if tie else "?"
            lines.append(f"{dest.name.split('__')[-1]} {len(checks)} checks, lift {cells} cells tie")
        return ("recalculated in Excel; every check 0 and master 0, no error value; the lift script's Python "
                "recalculation ties out: " + ", ".join(lines))

    # --- 5
    def step_render(self) -> str:
        from . import excel
        if not self.copies:
            raise StepFailed("step 2 made no copies to render")
        total = 0
        for dest in self.copies:
            out = self.work / f"render_{dest.name}.json"
            proc = _rotm(["render", str(dest / "model.xlsx"), "--quiet", "--json", str(out)])
            rep = _read_report(out)
            files = [Path(f) for f in ((rep.get("data") or {}).get("files") or [])]
            if proc.returncode != 0 or not files:
                raise StepFailed(f"{dest.name}: render exit {proc.returncode}: {_tail(proc)}")
            import openpyxl
            wb = openpyxl.load_workbook(dest / "model.xlsx", read_only=True)
            sheets = list(wb.sheetnames)
            wb.close()
            if len(files) != len(sheets):
                raise StepFailed(f"{dest.name}: {len(files)} PNG files for {len(sheets)} sheets")
            for f in files:
                if not f.is_file() or f.stat().st_size == 0:
                    raise StepFailed(f"{dest.name}: {f.name} is missing or empty")
                if excel.png_is_blank(f):
                    raise StepFailed(f"{dest.name}: {f.name} is blank (one colour)")
            total += len(files)
        return f"{total} PNG files, one per sheet, none blank; rendered, not looked at by code"

    # --- 6
    def step_orphans(self) -> str:
        from . import excel
        entries = excel.read_job_log(since=self.started)
        left = excel.orphan_pids(entries)
        if left:
            raise StepFailed(f"Excel processes started by this selftest are still running: PIDs {left}")
        pids = sorted({int(e['pid']) for e in entries if e.get('pid')})
        return f"{len(entries)} Excel jobs since the start ({len(pids)} PIDs); none is still running"

    # --- 7
    def step_f02(self) -> str:
        root = self.work / "f02"
        skill = _copytree(_skill(), root / "skills" / "rule-of-thumb-model")
        env = _env({"ROTM_NO_EXCEL": "1"})
        env.pop("ROTM_EXAMPLE_ROOTS", None)
        env.pop("ROTM_FOREIGN_DIR", None)
        out = root / "selftest_quick.json"
        proc = _rotm(["selftest", "--quick", "--json", str(out)], env=env, skill=skill, timeout=7200)
        if proc.returncode != 0:
            raise StepFailed(f"selftest --quick in the copy exit {proc.returncode}: {_copy_failures(out, proc)}")
        starter = skill / "templates" / "starter"
        for args, what in ((["build", str(starter), "--strict", "--force", "--quiet"], "build --strict"),
                           (["audit", str(starter), "--strict", "--quiet"], "audit --strict")):
            p = _rotm(args, env=env, skill=skill)
            if p.returncode != 0:
                raise StepFailed(f"starter in the copy: {what} exit {p.returncode}: {_tail(p)}")
        if (root / ".claude-plugin").exists():
            raise StepFailed("the copy sits inside a plugin folder")
        return (f"the skill folder alone in {skill.as_posix()}: selftest --quick exit 0 "
                "(pytest without Excel, every example and template built and audited in strict mode); "
                "templates/starter built and audited in strict mode there")

    # --- 8
    def step_f08a(self) -> str:
        from . import workbook as wbk
        inputs = self._f08a_inputs()
        lines = []
        for src in inputs:
            lines.append(self._f08a_one(src, wbk))
        return f"{len(inputs)} workbooks the plugin did not make: " + "; ".join(lines)

    def _f08a_inputs(self) -> list[Path]:
        from . import workbook as wbk
        chosen: list[Path] = []
        if self.foreign_dir is not None and self.foreign_dir.is_dir():
            cands = sorted((p for p in self.foreign_dir.iterdir() if p.suffix.lower() in (".xlsx", ".xlsm")),
                           key=lambda p: p.stat().st_size)
            readable: list[tuple[Path, Any]] = []
            for p in cands:
                try:
                    pk = wbk.package_parts(p)
                except Exception:                     # noqa: BLE001 - an unreadable file is not a candidate
                    continue
                readable.append((p, pk))
            with_query = next((p for p, pk in readable if getattr(pk, "queries", 0)), None)
            with_chart = next((p for p, pk in readable if getattr(pk, "charts", 0)), None)
            for p in (with_query, with_chart):
                if p is not None and p not in chosen:
                    chosen.append(p)
            for p, _pk in readable:
                if len(chosen) >= F08A_FOREIGN_COUNT:
                    break
                if p not in chosen:
                    chosen.append(p)
        fixture = self.work / "f08a" / "fixture_chart_table_names_cf.xlsx"
        make_f08a_fixture(fixture)
        self.f08a_splits[fixture] = F08A_FIXTURE_SPLITS       # the fixture names the outcome of each split
        chosen.append(fixture)
        return chosen

    def _f08a_one(self, src: Path, wbk: Any) -> str:
        from . import excel, formula as fx
        base = self.work / "f08a" / src.stem.replace(" ", "_")[:40]
        base.mkdir(parents=True, exist_ok=True)
        orig = base / ("orig" + src.suffix.lower())
        shutil.copy2(src, orig)
        resaved = base / ("orig_excel" + src.suffix.lower())
        res = excel.recalc(orig, save=True, out=resaved)
        if not res.ok:
            raise StepFailed(f"{src.name}: Excel could not recalculate the copy of the original: {res.errors}")
        before = wbk.snapshot(resaved)
        before_parts = excel.inventory_counts(excel.inventory(resaved))
        results = []
        # theme: styles only
        results.append(self._f08a_command(src, base, "theme", ["theme", str(orig)], wbk.Intended("styles_only"),
                                          before, before_parts, wbk))
        # compat: the replaced cells
        results.append(self._f08a_command(src, base, "compat", ["compat", str(orig)], None, before, before_parts, wbk))
        # split: the cells the fixture names with their outcome, else the first formula over the limit, if any;
        # for a foreign workbook the expected refusal comes from the formula (v4, A-aera-17)
        targets: list[tuple[str, str, str | None, str | None]] = []
        if src in self.f08a_splits:
            targets = [(s, c, expect, None) for s, c, expect in self.f08a_splits[src]]
        else:
            info = wbk.open_workbook(orig)
            for s in info.sheets:
                for c in s.cells.values():
                    if c.formula and getattr(c, "formula_kind", "normal") == "normal" and fx.depth(c.formula) > 2:
                        targets = [(s.name, c.coord, f08a_split_expectation(c.formula),
                                    f08a_split_possible(c.formula))]
                        break
                if targets:
                    break
        if not targets:
            results.append("split: no formula over the limit (nothing to split)")
        for sheet, cell, expect, possible in targets:
            ref = (sheet, cell)
            where = f"{common.quote_sheet(sheet)}!{cell}"
            intended = wbk.Intended("cells", cells={ref}, new_sheets={"split_helpers"})
            results.append(self._f08a_command(src, base, "split", ["split", str(orig), where, "--cell-only"],
                                              intended, before, before_parts, wbk, expect=expect, possible=possible,
                                              label=f"split {where}"))
        return f"{src.name}: " + ", ".join(results)

    def _f08a_command(self, src: Path, base: Path, name: str, args: list[str], intended: Any, before: Any,
                      before_parts: dict[str, int], wbk: Any, *, expect: str | None = None,
                      possible: str | None = None, label: str | None = None) -> str:
        """One command of step f08a on a copy. `expect`: the rule id of the error finding the run must end with
        (the run then writes nothing); `possible`: a rule id the run may end with (a refusal that depends on the
        values); `label`: the words that start the step's line (default: the command's name). A run that writes no
        output is a verified no-op (exit 0 or 1, no finding of severity error), an expected refusal, or a failure
        whose message names the rule id (v4, A-aera-17)."""
        from . import excel
        label = label or name
        stem = re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_")
        out = base / f"out_{stem}{Path(args[1]).suffix}"
        rep_path = base / f"{stem}.json"
        proc = _rotm(args + ["-o", str(out), "--json", str(rep_path), "--quiet"])
        rep = _read_report(rep_path)
        findings = rep.get("findings") or []
        rules = {f.get("rule") for f in findings}
        unintended = rules & {"T-UNINTENDED", "W-UNINTENDED"}
        if unintended:
            raise StepFailed(f"{src.name} {label}: the command found an unintended change ({', '.join(sorted(unintended))})")
        if not out.is_file():
            errors = [f for f in findings if f.get("severity") == "error"]
            error_rules = sorted({str(f.get("rule")) for f in errors})
            if proc.returncode in (common.EXIT_REFUSED, common.EXIT_NOT_AVAILABLE) and not errors:
                return f"{label}: refused and said why ({_tail(proc, 2)[:120]})"
            if errors:
                first = errors[0]
                text = f"{first.get('rule')}: {(first.get('message') or '')[:120]}"
                if expect is not None and error_rules == [expect]:
                    return f"{label}: refused as expected ({text})"
                if possible is not None and error_rules == [possible]:
                    return f"{label}: refused, which the values can cause ({text})"
                wanted = f"; the run expected {expect}" if expect else ""
                raise StepFailed(f"{src.name} {label}: exit {proc.returncode}, nothing written, and the error finding "
                                 f"{', '.join(error_rules)} was not expected{wanted}: {text}")
            if expect is not None:
                raise StepFailed(f"{src.name} {label}: expected {expect}, and the run (exit {proc.returncode}) "
                                 f"reported no error finding and wrote nothing: {_tail(proc, 3)}")
            if name in ("compat", "split") and proc.returncode in (0, 1):
                return f"{label}: nothing to change, nothing written"
            raise StepFailed(f"{src.name} {label}: exit {proc.returncode} and no output: {_tail(proc)}")
        if expect is not None:
            raise StepFailed(f"{src.name} {label}: expected {expect}, and the command wrote {out.name} "
                             f"(exit {proc.returncode})")
        if intended is None:                               # compat: the cells it replaced
            cells = {(r["sheet"], r["cell"]) for r in (rep.get("data") or {}).get("replaced") or []}
            intended = wbk.Intended("cells", cells=cells)
        after = wbk.snapshot(out)
        diffs, statement = f08a_compare(before, after, intended, wbk)
        if diffs:
            raise StepFailed(f"{src.name} {label}: {len(diffs)} differences outside the intended change, first: "
                             f"{diffs[0].message}; {statement}")
        after_parts = excel.inventory_counts(excel.inventory(out))
        lost = {k: (v, after_parts.get(k, 0)) for k, v in before_parts.items()
                if k not in ("sheets", "names") and after_parts.get(k, 0) < v}
        if after_parts.get("sheets", 0) < before_parts.get("sheets", 0):
            lost["sheets"] = (before_parts.get("sheets", 0), after_parts.get("sheets", 0))
        if lost:
            raise StepFailed(f"{src.name} {label}: parts lost (Excel's counts before, after): {lost}")
        verified = "recalculated" if (rep.get("verified") or {}).get("recalculated") else "compared without recalculation"
        return f"{label}: no unintended difference, parts kept ({verified}; {statement})"

    # --- 9
    def step_f27(self) -> str:
        root = self.work / "f27" / "model"
        root.parent.mkdir(parents=True, exist_ok=True)
        env = _env()
        steps: list[str] = []
        run = self._f27_run
        run(root, env, steps, "init --template starter",
            ["init", str(root), "--template", "starter", "--name", "f27_starter"], json_out=False)
        reply = "agreed for the selftest F27"
        task = root.parent / "task.md"
        task.write_text(f"The user's reply to the contract: {reply}\n", encoding="utf-8")
        run(root, env, steps, "contract agree (task-file channel)",
            ["contract", "agree", str(root), "--table", "lines", "--reply", reply, "--by", "selftest",
             "--channel", "task-file", "--source-file", str(task)])
        # init copies no raw/ folder: the sample extract under data/ is landed per the agreed contract
        sample = root / "data" / "lines.csv"
        run(root, env, steps, "land --table lines --csv data/lines.csv",
            ["land", str(root), "--table", "lines", "--csv", str(sample)])
        spec_path = root / "model.spec.json"
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        for sh in spec["sheets"]:
            if sh.get("type") == "raw":
                sh["landing"] = "power_query"
        spec_path.write_text(json.dumps(spec, indent=1) + "\n", encoding="utf-8")
        steps.append('landing "power_query" set on raw_lines in model.spec.json')
        raw_csv = root / "raw" / "lines.csv"           # the file the generated query reads
        base_rows = _read_csv(raw_csv)
        run(root, env, steps, "build --strict", ["build", str(root), "--strict", "--force"])
        man = json.loads((root / "model.manifest.json").read_text(encoding="utf-8"))
        entry = next(e for e in man["sheets"] if e.get("type") == "raw")
        if entry.get("landing") != "power_query":
            raise StepFailed("the built raw sheet is not a Power Query table (manifest landing is not power_query)")
        self._f27_check(root, env, [], "check after the build", steps, expect=0)
        run(root, env, steps, "audit --strict", ["audit", str(root), "--strict"])
        # more rows: the source (the CSV the query reads) grows by 15 rows
        grown = base_rows + _more_lines(base_rows, 15)
        _write_csv(raw_csv, grown)
        self._f27_check(root, env, ["--refresh"], f"{len(grown)} rows: check --refresh", steps, expect=1,
                        rule="H-COVERAGE", fix_word="extend")
        run(root, env, steps, "extend", ["extend", str(root)])
        self._f27_check(root, env, ["--accept-refresh", "--control", f"lines.amount={_amount_total(grown)}"],
                        "check --accept-refresh --control", steps, expect=0)
        # v3, [A-aera-12, C-sol-9, C-sol-10] part 4: the changed source reached an output cell
        grown_total = self._f27_output(root, spec, grown, f"{len(grown)} rows accepted", steps,
                                       earlier=f27_block_total(spec, base_rows))
        run(root, env, steps, "audit --strict", ["audit", str(root), "--strict"])
        # fewer rows: the helper rows below the last raw row are named, and extend clears them
        shrunk = base_rows[:40]
        _write_csv(raw_csv, shrunk)
        self._f27_check(root, env, ["--refresh"], f"{len(shrunk)} rows: check --refresh", steps, expect=1,
                        rule="H-BEYOND", fix_word="extend")
        run(root, env, steps, "extend (clears the helper rows below the raw rows)", ["extend", str(root)])
        self._f27_check(root, env, ["--accept-refresh", "--control", f"lines.amount={_amount_total(shrunk)}"],
                        "check --accept-refresh --control", steps, expect=0, absent="H-BEYOND")
        self._f27_output(root, spec, shrunk, f"{len(shrunk)} rows accepted", steps, earlier=grown_total)
        run(root, env, steps, "audit --strict", ["audit", str(root), "--strict"])
        # the accepted control total (raw/lines.controls.json) is used by the rebuild
        run(root, env, steps, "build --strict (rebuild)", ["build", str(root), "--strict"])
        self._f27_check(root, env, ["--strict"], "check --strict after the rebuild", steps, expect=0)
        run(root, env, steps, "audit --strict", ["audit", str(root), "--strict"])
        return "Power Query table over a local CSV: " + "; ".join(steps)

    def _f27_run(self, root: Path, env: dict[str, str], steps: list[str], label: str, args: list[str], *,
                 expect: int = 0, json_out: bool = True) -> dict[str, Any]:
        """One command of step f27; exit `expect` or StepFailed. Appends one report line."""
        out = root.parent / f"f27_{len(steps):02d}_{args[0]}.json"
        p = _rotm(args + (["--json", str(out)] if json_out else []), env=env)
        rep = _read_report(out) if json_out else {}
        if p.returncode != expect:
            raise StepFailed(f"{label}: exit {p.returncode}, expected {expect}: {_tail(p, 8)}")
        summary = rep.get("summary") or {}
        counts = (f" (errors {summary.get('errors', 0)}, warnings {summary.get('warnings', 0)})"
                  if isinstance(summary, dict) and summary else "")
        steps.append(f"{label}: exit {p.returncode}{counts}")
        return rep

    def _f27_check(self, root: Path, env: dict[str, str], extra: list[str], label: str, steps: list[str], *,
                   expect: int, rule: str | None = None, fix_word: str | None = None,
                   absent: str | None = None) -> dict[str, Any]:
        """One check command of step f27: exit `expect`; no #REF!; with exit 0 the master check is 0 and no cell
        holds an error value; `rule` must be reported (its fix naming `fix_word`); `absent` must not be."""
        out = root.parent / f"f27_{len(steps):02d}_check.json"
        p = _rotm(["check", str(root), "--quiet", "--json", str(out), *extra], env=env)
        rep = _read_report(out)
        data = rep.get("data") or {}
        errors = data.get("error_cells") or []
        refs = [e for e in errors if "#REF!" in str(e)]
        if refs:
            raise StepFailed(f"{label}: #REF! after the refresh: {refs[:3]}")
        if p.returncode != expect:
            raise StepFailed(f"{label}: check exit {p.returncode}, expected {expect}: {_tail(p, 8)}")
        findings = rep.get("findings") or []
        line = f"{label}: exit {p.returncode}"
        if rule is not None:
            named = [f for f in findings if f.get("rule") == rule]
            if not named or (fix_word and fix_word not in (named[0].get("fix") or "")):
                raise StepFailed(f"{label}: check did not report {rule} with the fix {fix_word!r}; rules reported: "
                                 f"{sorted({f.get('rule') for f in findings})}")
            line += f", {rule}: " + (named[0].get("message") or "")[:90].replace(";", ",")   # "; " parts the lines
        if absent is not None and any(f.get("rule") == absent for f in findings):
            raise StepFailed(f"{label}: check still reports {absent}")
        if expect == 0:
            master = (data.get("master") or {}).get("value")
            if master != 0 or errors:
                raise StepFailed(f"{label}: master {master!r}, error cells {errors[:3]}")
            line += ", every check holds"
        steps.append(line)
        return rep

    def _f27_output(self, root: Path, spec: dict[str, Any], rows: list[list[str]], label: str, steps: list[str], *,
                    earlier: Decimal) -> Decimal:
        """v3, [A-aera-12, C-sol-9, C-sol-10] part 4: after an accepted refresh, the total cell of block F27_BLOCK
        (named by the manifest's blocks) in the workbook Excel saved holds the total computed in Python from the new
        CSV rows over the block's categories and months. `earlier` is that total before this change of the CSV: an
        equal total could not show that the change reached the output. Appends one report line."""
        man = json.loads((root / "model.manifest.json").read_text(encoding="utf-8"))
        where = next(((e.get("name"), b.get("total")) for e in man.get("sheets", []) for b in e.get("blocks") or []
                      if b.get("id") == F27_BLOCK and b.get("total")), None)
        if where is None:
            raise StepFailed(f"{label}: the manifest names no total cell for block {F27_BLOCK}")
        sheet, cell = where
        expected = f27_block_total(spec, rows)
        if expected == earlier:
            raise StepFailed(f"{label}: the changed CSV gives the same {F27_BLOCK} total as before ({expected}), so "
                             "the step cannot show that the change reached the output")
        value = _saved_value(root / (man.get("workbook") or "model.xlsx"), sheet, cell)
        if (not isinstance(value, (int, float)) or isinstance(value, bool)
                or abs(Decimal(str(value)) - expected) > F27_TOLERANCE):
            raise StepFailed(f"{label}: {sheet}!{cell} (the total of block {F27_BLOCK}) holds {value!r}; computed "
                             f"from raw/lines.csv: {expected}; the refreshed rows did not reach the output")
        steps.append(f"{label}: {sheet}!{cell} (total of block {F27_BLOCK}) = {_plain(value)}, equal to the total "
                     f"computed from the CSV (before: {_plain(earlier)})")
        return expected

    # --- driver
    def run(self, name: str, fn: Callable[[], str]) -> None:
        t0 = time.monotonic()
        try:
            text = fn()
        except StepFailed as exc:
            self.results[name] = "fail"
            self.report.error("SELFTEST-FAIL", f"step {name}: {exc}", "fix the cause named and run selftest again")
            return
        except Exception as exc:  # noqa: BLE001 - a crash in a step is a failed step, the others still run
            self.results[name] = "fail"
            common.save_traceback(exc)
            self.report.error("SELFTEST-FAIL", f"step {name}: {type(exc).__name__}: {exc}",
                              "see the traceback in the state folder's last_error.txt")
            return
        self.results[name] = "pass"
        self.report.info("SELFTEST-PASS", f"step {name} passed in {time.monotonic() - t0:.0f} s: {text}")
        self.report.note(f"{name}: pass: {text}")

    def skip(self, name: str, why: str, *, quick: bool = False) -> None:
        self.results[name] = "not run" if quick else "skipped"
        if quick:
            self.report.note(f"{name}: not run ({why})")
        else:
            self.report.warning("SELFTEST-SKIPPED", f"step {name} skipped: {why}; not verified",
                                "run selftest on a machine with Excel")


# ----------------------------------------------------------------------------- data for F27 and F08a

def _read_csv(path: Path) -> list[list[str]]:
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    return rows[1:]


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["line_id", "line_date", "category", "amount"])
        w.writerows(rows)


def _more_lines(rows: list[list[str]], n: int) -> list[list[str]]:
    cats = sorted({r[2] for r in rows})
    out = []
    for i in range(n):
        out.append([f"L9{i + 1:03d}", "2026-03-15", cats[i % len(cats)], str(10000 + 500 * i)])
    return out


def _amount_total(rows: list[list[str]]) -> str:
    """The exact total of the amount column (the fourth), as text for --control lines.amount=<total>."""
    total = sum((Decimal(r[3]) for r in rows), Decimal(0))
    return format(total, "f")


def _plain(value: Any) -> str:
    """A number as text without a trailing .0 and without '; ' (the report joins its lines with '; ')."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return format(value, "f") if isinstance(value, Decimal) else str(value)


def f27_block_total(spec: dict[str, Any], rows: list[list[str]], block_id: str = F27_BLOCK) -> Decimal:
    """The total of block `block_id` computed in Python from CSV rows (line_id, line_date, category, amount): the
    sum of amount over the rows whose category is in the block's row list (an input list) and whose date falls in a
    month of the block's monthly timeline (start: an input date; periods: the timeline's count). A block of
    another shape raises StepFailed."""
    sheets = spec.get("sheets", [])
    items = {i.get("id"): i for s in sheets if s.get("type") == "inputs" for i in s.get("items", [])}
    block = next((b for s in sheets for b in s.get("blocks") or [] if b.get("id") == block_id), None)
    if block is None:
        raise StepFailed(f"model.spec.json has no block {block_id}")
    try:
        cats = set(items[block["rows"]["in"]]["values"])
        timeline = block["cols"]["timeline"]
        if timeline.get("step", "month") != "month":
            raise ValueError(f"timeline step {timeline.get('step')!r}")
        start = date.fromisoformat(str(items[timeline["start"]]["value"]))
        months = set()
        for i in range(int(timeline["periods"])):
            years, month0 = divmod(start.month - 1 + i, 12)
            months.add((start.year + years, month0 + 1))
    except (KeyError, TypeError, ValueError) as exc:
        raise StepFailed(f"block {block_id}: not a grid of an input list by a monthly timeline ({exc})") from None
    total = Decimal(0)
    for r in rows:
        day = date.fromisoformat(r[1])
        if r[2] in cats and (day.year, day.month) in months:
            total += Decimal(r[3])
    return total


def _saved_value(path: Path, sheet: str, cell: str) -> Any:
    """The value Excel saved in sheet!cell (openpyxl, data_only); None when the file or the sheet cannot be read."""
    import warnings

    import openpyxl
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            book = openpyxl.load_workbook(path, read_only=True, data_only=True, keep_links=False)
        except Exception:  # noqa: BLE001 - an unreadable workbook fails the comparison with value None
            return None
    try:
        return book[sheet][cell].value if sheet in book.sheetnames else None
    finally:
        book.close()


def f08a_compare(before: Any, after: Any, intended: Any, wbk: Any) -> tuple[list[Any], str]:
    """F08a's comparison (A-aera-16): the cells that hold a volatile function or depend on one
    (workbook.volatile_cells) keep their formula compared, not their cached value. Returns the differences
    (conditional-format differences left out: Excel merges duplicate rules when it opens a file) and
    workbook.preservation_statement, the sentence that says what was compared."""
    skip, _first = wbk.volatile_cells(before)
    stats: dict[str, Any] = {}
    diffs = [d for d in wbk.compare(before, after, intended, skip_values=skip, stats=stats) if d.kind != "cf"]
    return diffs, wbk.preservation_statement(stats)


# The cells of the f08a fixture that step 8 splits, each with the outcome the fixture names (v4, A-aera-17): Data!G2,
# a lookup inside IFERROR, is split with the pattern H5 (None: written, values proved); Data!E2, ROUND inside
# IFERROR, ends with the refusal SPLIT-GUARD and nothing is written.
F08A_FIXTURE_SPLITS: tuple[tuple[str, str, str | None], ...] = (("Data", "G2", None), ("Data", "E2", "SPLIT-GUARD"))


def f08a_split_expectation(formula: str, limit: int = 2) -> str | None:
    """The refusal step 8 expects `rotm.py split` to end with for `formula`, worked out from the formula alone (v4,
    A-aera-17): "SPLIT-GUARD" when a part split must move (lib.formula.over_limit_parts) sits inside IFERROR or IFNA
    (split.guard_name) and is not the INDEX or MATCH call of a guarded lookup whose pattern H5 fits the limit; a
    part that is not under a guard is looked at again the same way, since split splits a moved part again. None
    when no such refusal is expected."""
    from . import formula as fx
    from . import split
    try:
        parts = fx.over_limit_parts(formula, limit)
    except ValueError:
        return None
    for p in parts:
        if split.guard_name(formula, p.text) in ("IFERROR", "IFNA"):
            pattern = split.lookup_pattern(formula, p)
            if pattern is None:
                return split.GUARD_RULE
            _call, level, position, host_part = pattern
            d_if = fx.depth("=" + host_part)
            if limit < 2 or ((level - 1) + d_if > limit and d_if > limit):
                return split.GUARD_RULE
            # the position helper is split again the same way when it is over the limit
            if fx.depth(position) > limit and f08a_split_expectation(position, limit) is not None:
                return split.GUARD_RULE
            continue
        if f08a_split_expectation("=" + p.text, limit) is not None:
            return split.GUARD_RULE
    return None


def f08a_split_possible(formula: str, limit: int = 2) -> str | None:
    """A refusal `rotm.py split` may end with for `formula` depending on the values (v4, A-aera-17): "SPLIT-GUARD"
    when a part split moves sits inside another function that can keep an error out of the result (IF, CHOOSE, the
    IS functions and the others of split.guard_name), since a helper that then holds an error value is refused
    after the recalculation, and a part in a branch of IF whose condition a helper cannot carry is refused before
    (v6); None otherwise."""
    from . import formula as fx
    from . import split
    try:
        parts = fx.over_limit_parts(formula, limit)
    except ValueError:
        return None
    calls = fx.analyze(formula).calls
    for p in parts:
        around = {c.name for c in calls if c.start <= p.start and p.end <= c.end and (c.start, c.end) != (p.start, p.end)}
        if around & set(split._OTHER_GUARDS):
            return split.GUARD_RULE
        if not around & set(split._ERROR_GUARDS) and f08a_split_possible("=" + p.text, limit) is not None:
            return split.GUARD_RULE
    return None


def make_f08a_fixture(path: Path) -> Path:
    """A workbook the plugin did not make: a table, a bar chart, defined names, conditional formats, a data
    validation and two formulas nested three deep for split, whose outcomes F08A_FIXTURE_SPLITS names."""
    import openpyxl
    from openpyxl.chart import BarChart, Reference
    from openpyxl.formatting.rule import CellIsRule
    from openpyxl.workbook.defined_name import DefinedName
    from openpyxl.worksheet.datavalidation import DataValidation
    from openpyxl.worksheet.table import Table
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(["Region", "Q1", "Q2", "Total", "Share"])
    for i, region in enumerate(("East", "North", "South", "West"), start=2):
        ws.append([region, 100 * i, 120 * i, f"=SUM(B{i}:C{i})", f"=IFERROR(ROUND(D{i}/SUM($D$2:$D$5),3),0)"])
    ws["G1"], ws["G2"] = "Lookup", '=IFERROR(INDEX(D2:D5,MATCH("South",A2:A5,0)),0)'
    ws.add_table(Table(displayName="tbl_regions", ref="A1:E5"))
    chart = BarChart()
    chart.add_data(Reference(ws, min_col=2, min_row=1, max_row=5, max_col=3), titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=1, min_row=2, max_row=5))
    ws.add_chart(chart, "I2")
    ws.conditional_formatting.add("D2:D5", CellIsRule(operator="greaterThan", formula=["500"], font=None))
    dv = DataValidation(type="whole", operator="between", formula1="0", formula2="10000")
    dv.add("B2:C5")
    ws.add_data_validation(dv)
    dn = DefinedName("total_q1", attr_text="Data!$B$2:$B$5")
    if hasattr(wb.defined_names, "add"):
        wb.defined_names.add(dn)
    else:
        wb.defined_names[dn.name] = dn
    wb.save(path)
    return path


# ----------------------------------------------------------------------------- entry points

def selftest(*, strict: bool = False, quick: bool = False, excel: bool = True,
             foreign_dir: str | Path | None = None, keep: bool = False) -> Any:
    """Run the steps; returns a common.Report with one entry per step (pass, fail, skipped and why)."""
    report = common.Report("selftest", _skill().as_posix())
    report.strict = strict
    excel_ok, why = False, "--no-excel"
    if excel and not quick:
        try:
            from . import excel as ex
            st = ex.excel_status()
            excel_ok, why = bool(st.available), (st.reason or "")
        except Exception as exc:  # noqa: BLE001
            excel_ok, why = False, f"{type(exc).__name__}: {exc}"
    fdir = Path(common.native_path(foreign_dir)) if foreign_dir else default_foreign_dir()
    work = Path(tempfile.mkdtemp(prefix="rotm_selftest_"))
    run = _Run(report, work, strict=strict, quick=quick, excel_ok=excel_ok, excel_why=why, foreign_dir=fdir)
    report.note(f"working folder: {work.as_posix()}" + (" (kept)" if keep else " (removed at the end)"))
    try:
        run.run("pytest", run.step_pytest)
        run.run("examples", run.step_examples)
        run.run("portable", run.step_portable)
        for name, fn in (("recalc", run.step_recalc), ("render", run.step_render)):
            if quick:
                run.skip(name, "--quick", quick=True)
            elif not excel_ok:
                run.skip(name, f"Excel is not available ({why})")
            elif run.results.get("examples") != "pass":
                run.skip(name, "step examples failed, so there is nothing to recalculate", quick=True)
                run.results[name] = "fail"
            else:
                run.run(name, fn)
        if quick:
            for name in ("orphans", "f02_skill_alone", "f08a_existing", "f27_power_query"):
                run.skip(name, "--quick", quick=True)
        else:
            run.run("f02_skill_alone", run.step_f02)
            for name, fn in (("f08a_existing", run.step_f08a), ("f27_power_query", run.step_f27)):
                if excel_ok:
                    run.run(name, fn)
                else:
                    run.skip(name, f"Excel is not available ({why})")
            if excel_ok:
                run.run("orphans", run.step_orphans)
            else:
                run.skip("orphans", f"Excel is not available ({why})")
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
    report.data["steps"] = dict(run.results)
    report.data["foreign_dir"] = fdir.as_posix() if fdir else None
    report.verified["recalculated"] = run.results.get("recalc") == "pass"
    report.verified["rendered"] = run.results.get("render") == "pass"
    code = exit_code_for_steps(run.results, strict=strict)
    report.finish(strict)                     # v3, J-6: the report's "strict" field is the --strict option
    report.exit_code = code                   # the exit code and status come from the steps, not from the findings
    report.status = {0: "pass", 1: "fail", 3: "not_verified"}.get(code, "fail")
    passed = sum(1 for v in run.results.values() if v == "pass")
    report.note(f"steps: {passed} passed, {sum(1 for v in run.results.values() if v == 'fail')} failed, "
                f"{sum(1 for v in run.results.values() if v == 'skipped')} skipped, "
                f"{sum(1 for v in run.results.values() if v == 'not run')} not run")
    return report


def add_selftest_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--strict", action="store_true",
                   help="a skipped Excel step makes the exit code 3; a hit of the portable scan fails the step")
    p.add_argument("--quick", action="store_true",
                   help="pytest (without Excel tests), the strict builds and the portable scan only")
    p.add_argument("--no-excel", action="store_true", help="skip the steps that need Excel")
    p.add_argument("--foreign", metavar="DIR", help="copies of workbooks the plugin did not make (F08a)")
    p.add_argument("--keep", action="store_true", help="keep the temporary folders")
    p.add_argument("--json", metavar="FILE", help="write the report as JSON ('-' = stdout)")
    p.add_argument("--quiet", action="store_true", help="print only the last line")


def run_selftest(args: argparse.Namespace) -> int:
    import importlib.util
    if importlib.util.find_spec("pytest") is None:        # v3, [C-sol-16]: one line, exit 3, before any step
        sys.stderr.write(N_PYTEST + "\n")
        return common.EXIT_NOT_AVAILABLE
    report = selftest(strict=args.strict, quick=args.quick, excel=not args.no_excel, foreign_dir=args.foreign,
                      keep=args.keep)
    code = report.exit_code
    report.emit(args.json, quiet=getattr(args, "quiet", False))
    return code

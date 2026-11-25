"""The Excel bridge: one PID-tracked Excel instance per job behind a machine-wide lock; recalculation,
read-back, renders, Power Query attach and refresh, edits to existing workbooks (design section 12).

Owner: CORE-X. Mechanism: a job JSON run by scripts/lib/excel_job.ps1 through
`powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File excel_job.ps1 -Job <job.json> -Result <result.json>`.
No pywin32. Never attach to or close an Excel instance this job did not start.

Rules this module keeps (design section 12, judge plan approval seq 1 advice 6, seq 2 F27 advice):
- One Excel job at a time on the machine: the lock file ROTM_EXCEL_LOCK (default <state dir>/excel.lock) is
  created with exclusive create and holds the owner's PID, host and start time. A waiter gives up after
  ROTM_EXCEL_TIMEOUT seconds (default 600) with common.ExcelBusy (exit 5). A lock whose owner PID is gone is
  removed (under a second, OS-level guard lock, so two waiters cannot both remove it).
- Each job starts a new Excel instance, records its PID (from the window handle), and after the job that PID
  must be gone: the job script quits Excel and stops that PID if it is still alive after 5 s; Python stops it
  too if PowerShell timed out. A PID that was already running before the job is never closed or stopped.
- A Power Query refresh runs with BackgroundQuery off and waits until Excel reports it finished before
  anything is read.
- Excel's open-path limit (218 characters) is handled by working on a copy in the job folder.
- Render jobs use the clipboard (Range.CopyPicture); the clipboard's previous content is lost.
- Every job appends one line to <state dir>/excel_jobs.jsonl (time, PID, workbook, ok, killed), so a caller
  that ran commands in subprocesses can check afterwards that no recorded PID is still alive (design 16 step 5).

Operation shapes (run_job, apply_edits; checked by normalize_operations, which says what to change):
  {"op": "style", "sheet": S, "ranges": ["B4:J4", "B7"], "font": {"name", "size", "bold", "italic", "underline",
      "color": "RRGGBB" | None (automatic)}, "fill": "RRGGBB" | None (no fill; leave the key out to keep the fill),
      "number_format": "#,##0", "border": "none" | "top thin 000000" | {"top"|"bottom"|"left"|"right"|"all":
      "thin" | {"style": "thin", "color": "000000"}}, "align": "right" | {"horizontal", "vertical", "wrap", "indent"}}
      Colours may also be "#RRGGBB" or ARGB "FFRRGGBB" (openpyxl). A border side applies to every cell of the range
      (outer edge and inside lines); on a multi-row range top and bottom share the inside lines.
  {"op": "formula", "sheet": S, "cell": "D7", "formula": "=SUM(D4:D6)", "dynamic": False}   (_xlfn. prefixes removed)
  {"op": "value", "sheet": S, "cell": "D7", "value": text | number | bool | date | None (clears)}
      Text Excel would convert (leading zeros, a number, a date, TRUE, a leading - + @) is written as text.
  {"op": "fill_down", "sheet": S, "from_row": 601, "to_row": 900, "first_col": "A", "last_col": "H"}
  {"op": "add_sheet", "name": N, "after": S | None}
  {"op": "clear", "sheet": S, "ranges": [...], "what": "contents" | "all" | "formats"}
  {"op": "recalc", "mode": "full_rebuild" | "full" | "calculate"}
  {"op": "read", "refs": ["checks!C2", "calc!D7:I10"]}
  {"op": "errors", "limit": 5000, "sheets": [S, ...] | None}
  {"op": "inventory"}
  {"op": "render", "out_dir": DIR, "sheets": [...] | None, "max_rows": 200, "max_cols": 40}
  {"op": "pq_attach", "sheet": S, "table_name": T, "query_name": Q, "m_text": M, "formats": F}
  {"op": "pq_refresh", "wait_s": 300, "formats": F}
      F = {sheet: {"columns": [look per table column], "header": look}} (looks as in "style"). Both operations set
      PreserveFormatting on and AdjustColumnWidth off on every query table they touch, and apply F to each raw
      table's whole loaded range after the load or the refresh. run_job adds F from the workbook's manifest
      (raw_formats) when the key is absent; "formats": None applies nothing (a refresh as Excel does it by hand).
  The "errors" step also lists the circular reference Excel reports on each sheet ("no value (circular
  reference)") and, after a save, the formula cells the saved file holds without a value: "no value (circular
  reference)" for a cell on a cycle of references through a cell Excel named, else "no value". A cell that holds
  a volatile function or depends on one is not listed when it is an empty-text result whose references show no
  cycle, or when Excel names it as circular and its references show no cycle or the file holds a value for it
  (v3, A-aera-15; workbook.cells_without_value).
  Every save is followed by clean_saved_workbook (no folder path or user name of this PC in the file).
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import io
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Sequence

from . import common

LOCK_ENV = "ROTM_EXCEL_LOCK"         # lock file path; default <state dir>/excel.lock
TIMEOUT_ENV = "ROTM_EXCEL_TIMEOUT"   # seconds to wait for the lock and for a job; default 600
NO_EXCEL_ENV = "ROTM_NO_EXCEL"       # "1": treat Excel as unavailable
PROGID_ENV = "ROTM_EXCEL_PROGID"     # tests only: a COM ProgID other than Excel.Application
KEEP_JOBS_ENV = "ROTM_EXCEL_KEEP_JOBS"   # "1": keep every job folder (default: kept only after a failure)
DEFAULT_TIMEOUT_S = 600
JOB_SCRIPT = "excel_job.ps1"
PATH_LIMIT = 200                     # longer paths are worked on as a copy (Excel's limit is 218 characters)
STALE_UNREADABLE_LOCK_S = 30         # an empty or unreadable lock file older than this is stale
JOB_FOLDER_MAX_AGE_S = 2 * 24 * 3600
# v4 (C-sol-10): the note for each automation Excel that appeared during a job that timed out with no PID file
TIMEOUT_NO_PID_NOTE = ("Excel PID {pid} (an automation Excel that appeared during this job) was not stopped: the job "
                       "timed out before it recorded its Excel PID, so the PID is not known to be this job's")
# v4 (C-sol-3): Excel started but the job failed (rule X-OPEN, error, exit 1; check uses the same texts)
X_OPEN_ERROR_MAX = 200               # characters of Excel's first error shown in the X-OPEN message
X_OPEN_NOTE = "Excel could not open the workbook; the check cells were not read and nothing was saved"

EXCEL_SUFFIXES = {".xlsx": 51, ".xlsm": 52, ".xlsb": 50, ".xls": 56}   # Excel FileFormat numbers
MACRO_SUFFIXES = {".xlsm", ".xlsb", ".xls"}

READ_OPS = ("recalc", "read", "errors", "inventory", "render", "sleep")
EDIT_OPS = ("style", "formula", "value", "fill_down", "add_sheet", "clear", "pq_attach", "pq_refresh")
OPS = READ_OPS + EDIT_OPS

_CREATE_NO_WINDOW = 0x08000000


# ----------------------------------------------------------------------------- results

class ExcelError(str):
    """An Excel error value read back from a cell, e.g. ExcelError('#DIV/0!'). It equals its text."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"ExcelError({str.__repr__(self)})"


def is_error(value: Any) -> bool:
    return isinstance(value, ExcelError)


class ExcelJobFailed(common.EnvUnavailable):
    """An Excel job ran but did not finish its work; nothing was verified (exit 3)."""


@dataclass(frozen=True)
class ExcelStatus:
    available: bool
    version: str | None = None       # e.g. "16.0"
    reason: str | None = None        # why it is not available
    executable: str | None = None    # EXCEL.EXE registered for automation


@dataclass
class JobResult:
    ok: bool
    pid: int | None                  # the EXCEL.EXE this job started
    steps: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0
    killed: bool = False             # the PID had to be stopped after Quit
    attached: bool = False           # Excel handed back an instance this job did not start; it was left alone
    excel_version: str | None = None
    saved_path: Path | None = None
    notes: list[str] = field(default_factory=list)
    timed_out: bool = False

    def step(self, op: str) -> dict[str, Any]:
        """The first step of operation `op` ({} when there is none)."""
        for s in self.steps:
            if s.get("op") == op:
                return s
        return {}

    def message(self) -> str:
        """The job's errors on one line."""
        return "; ".join(self.errors) if self.errors else "no error"


@dataclass
class RecalcResult:
    ok: bool
    saved_path: Path | None
    input_sha256: str
    output_sha256: str | None
    error_cells: list[tuple[str, str, str]]   # (sheet, cell, error text)
    values: dict[str, Any]                    # "sheet!A1" -> value for the refs asked
    pid: int | None
    elapsed_s: float
    errors: list[str] = field(default_factory=list)       # why the job failed, when ok is False
    error_count: int = 0                                  # all error cells, also beyond the listed ones
    problems: dict[str, str] = field(default_factory=dict)  # ref -> why it could not be read
    excel_version: str | None = None
    calc_mode: str | None = None              # automatic | manual | semiautomatic
    killed: bool = False
    attached: bool = False                    # v4 (C-sol-3): Excel handed back an instance this job did not start
    timed_out: bool = False                   # v4 (C-sol-3): the job did not finish within its time limit


@dataclass
class RenderedSheet:
    sheet: str
    path: Path | None
    range: str | None = None                  # rendered cells, e.g. "A1:H40"
    last_row: int | None = None
    last_col: int | None = None
    truncated: bool = False                   # the used range is larger than the rendered range
    width_px: int | None = None
    height_px: int | None = None
    problem: str | None = None


# ----------------------------------------------------------------------------- settings

def _timeout(value: float | None = None) -> float:
    if value is not None:
        return float(value)
    raw = os.environ.get(TIMEOUT_ENV, "").strip()
    if raw:
        try:
            return max(1.0, float(raw))
        except ValueError:
            pass
    return float(DEFAULT_TIMEOUT_S)


def lock_path() -> Path:
    """ROTM_EXCEL_LOCK, else one fixed path per Windows user: %LOCALAPPDATA%/rule-of-thumb-model/excel.lock (else
    ~/.rule-of-thumb-model/excel.lock), whatever ROTM_STATE_DIR says. Every seat on the machine that runs Excel
    jobs (evidence runs, reviewers, user testers, Max) shares this one lock (design D14); a test harness that moves
    the state folder no longer gets a lock of its own (integrator, seen with parallel live sessions)."""
    env = os.environ.get(LOCK_ENV, "").strip()
    if env:
        return Path(env)
    if os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / common.TOOL / "excel.lock"
    return Path.home() / ("." + common.TOOL) / "excel.lock"


def _powershell() -> str | None:
    root = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    candidate = Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if candidate.is_file():
        return str(candidate)
    return shutil.which("powershell") or shutil.which("pwsh")


def _job_script() -> Path:
    return Path(__file__).resolve().parent / JOB_SCRIPT


# ----------------------------------------------------------------------------- processes (standard library only)

def _kernel32() -> Any:
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    k.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    k.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    k.CloseHandle.argtypes = (wintypes.HANDLE,)
    return k


def pid_alive(pid: int | None) -> bool:
    """True when a process with this id is running."""
    if not pid or pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    import ctypes
    from ctypes import wintypes
    k = _kernel32()
    handle = k.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.get_last_error() == 5             # access denied: it exists
    try:
        code = wintypes.DWORD()
        if not k.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == 259                        # STILL_ACTIVE
    finally:
        k.CloseHandle(handle)


def _process_created(pid: int) -> int | None:
    """Creation time of a process (FILETIME as an integer), to tell a reused PID from the original."""
    if os.name != "nt" or not pid:
        return None
    import ctypes
    from ctypes import wintypes
    k = _kernel32()
    handle = k.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not k.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
            return None
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        k.CloseHandle(handle)


def _kill(pid: int) -> bool:
    """Stop one process by id. Returns True when it is gone afterwards."""
    if not pid_alive(pid):
        return True
    if os.name == "nt":
        k = _kernel32()
        handle = k.OpenProcess(0x0001 | 0x1000, False, pid)   # PROCESS_TERMINATE | QUERY_LIMITED_INFORMATION
        if handle:
            try:
                k.TerminateProcess(handle, 1)
            finally:
                k.CloseHandle(handle)
        else:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True,
                           creationflags=_CREATE_NO_WINDOW, timeout=30)
    else:  # pragma: no cover - Excel runs on Windows only
        try:
            os.kill(pid, 9)
        except OSError:
            pass
    for _ in range(50):
        if not pid_alive(pid):
            return True
        time.sleep(0.1)
    return not pid_alive(pid)


def _parse_tasklist(text: str) -> set[int]:
    """PIDs from `tasklist /FO CSV /NH` output (lines like "EXCEL.EXE","1234","Console","1","90,000 K")."""
    pids: set[int] = set()
    for row in csv.reader(io.StringIO(text)):
        if len(row) >= 2 and row[0].strip().lower() == "excel.exe":
            try:
                pids.add(int(row[1].strip()))
            except ValueError:
                continue
    return pids


def excel_pids() -> set[int]:
    """PIDs of running EXCEL.EXE processes (tasklist)."""
    if os.name != "nt":
        return set()
    try:
        proc = subprocess.run(["tasklist", "/FI", "IMAGENAME eq EXCEL.EXE", "/FO", "CSV", "/NH"],
                              capture_output=True, timeout=60, creationflags=_CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return set()
    return _parse_tasklist(proc.stdout.decode("utf-8", errors="replace"))


def _automation_excel_pids() -> dict[int, str]:
    """EXCEL.EXE processes started for automation (command line has /automation or -Embedding)."""
    ps = _powershell()
    if not ps:
        return {}
    cmd = ("Get-CimInstance Win32_Process -Filter \"Name='EXCEL.EXE'\" | "
           "ForEach-Object { '' + $_.ProcessId + [char]9 + $_.CommandLine }")
    try:
        proc = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-Command", cmd], capture_output=True,
                              timeout=60, creationflags=_CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return {}
    out: dict[int, str] = {}
    for line in proc.stdout.decode("utf-8", errors="replace").splitlines():
        pid_text, _, cmdline = line.partition("\t")
        if pid_text.strip().isdigit() and re.search(r"(?i)/automation|-embedding", cmdline):
            out[int(pid_text.strip())] = cmdline.strip()
    return out


# ----------------------------------------------------------------------------- the lock

_THREAD_LOCK = threading.RLock()
_DEPTH = 0
_HELD: dict[str, Any] = {}


@contextmanager
def _guard(lock_file: Path) -> Iterator[None]:
    """An OS-level lock on <lock>.guard, held only while a stale lock is checked and removed."""
    guard = lock_file.with_name(lock_file.name + ".guard")
    fd = os.open(guard, os.O_CREAT | os.O_RDWR)
    try:
        if os.name == "nt":
            import msvcrt
            deadline = time.monotonic() + 30
            while True:
                try:
                    os.lseek(fd, 0, 0)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(0.05)
            try:
                yield
            finally:
                os.lseek(fd, 0, 0)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:  # pragma: no cover
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def read_lock(path: Path | None = None) -> dict[str, Any] | None:
    """The lock file's owner record, or None when there is no readable lock."""
    target = path or lock_path()
    try:
        text = target.read_text(encoding="utf-8")
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _lock_is_stale(path: Path, owner: dict[str, Any] | None) -> bool:
    if owner is None:
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            return False
        return age > STALE_UNREADABLE_LOCK_S
    if owner.get("host") and owner.get("host") != socket.gethostname():
        return False                                     # another machine's process cannot be checked
    pid = owner.get("pid")
    if not isinstance(pid, int) or not pid_alive(pid):
        return True
    created = owner.get("created")
    now_created = _process_created(pid)
    return isinstance(created, int) and now_created is not None and created != now_created


def _acquire_file_lock(path: Path, timeout_s: float) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    me = {"rotm_excel_lock": 1, "pid": os.getpid(), "created": _process_created(os.getpid()),
          "host": socket.gethostname(), "started": common.now_iso(), "token": uuid.uuid4().hex,
          "command": " ".join(sys.argv)[:200]}
    start = time.monotonic()
    announced = False
    denied = 0
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except (FileExistsError, PermissionError) as exc:
            if isinstance(exc, PermissionError) and not path.exists():
                denied += 1                              # a lock being deleted right now, or a folder we cannot write
                if denied > 40:
                    raise common.EnvUnavailable(f"the Excel lock {path.as_posix()} cannot be created: permission denied",
                                                fix=f"set {LOCK_ENV} to a file path in a folder you can write") from None
                time.sleep(0.25)
                continue
            owner = read_lock(path)
            if _lock_is_stale(path, owner):
                with _guard(path):
                    again = read_lock(path)
                    if again == owner and _lock_is_stale(path, again):
                        try:
                            path.unlink()
                        except FileNotFoundError:
                            pass
                        except PermissionError:
                            time.sleep(0.2)
                continue
            waited = time.monotonic() - start
            if waited >= timeout_s:
                who = (f"PID {owner.get('pid')} on {owner.get('host')} since {owner.get('started')}"
                       f" ({owner.get('command') or 'no command recorded'})") if owner else "a process that wrote no owner record"
                raise common.ExcelBusy(
                    f"the Excel lock {path.as_posix()} is held by {who}; waited {waited:.0f} s",
                    fix=f"run again when that job has finished, or set {TIMEOUT_ENV} to wait longer (seconds)")
            if not announced:
                who = f"PID {owner.get('pid')} since {owner.get('started')}" if owner else "another process"
                sys.stderr.write(f"rule-of-thumb-model: waiting for the Excel lock held by {who} "
                                 f"(up to {timeout_s:.0f} s)\n")
                announced = True
            time.sleep(0.5)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(me, f)
        return me


def _release_file_lock(path: Path, me: dict[str, Any]) -> None:
    owner = read_lock(path)
    if owner is not None and owner.get("token") != me.get("token"):
        return                                           # not ours any more; leave it
    try:
        path.unlink()
    except FileNotFoundError:
        pass


@contextmanager
def excel_lock(timeout_s: float | None = None) -> Iterator[Path]:
    """Hold the machine-wide Excel lock; raise common.ExcelBusy after the timeout; remove a lock whose owner is gone.
    Re-entrant inside one process (a job run inside a held lock does not wait for itself)."""
    global _DEPTH
    timeout = _timeout(timeout_s)
    with _THREAD_LOCK:
        if _DEPTH > 0:
            _DEPTH += 1
            try:
                yield _HELD["path"]
            finally:
                _DEPTH -= 1
            return
        path = lock_path()
        me = _acquire_file_lock(path, timeout)
        _DEPTH = 1
        _HELD.update(path=path, me=me)
        try:
            yield path
        finally:
            _DEPTH = 0
            _HELD.clear()
            _release_file_lock(path, me)


# ----------------------------------------------------------------------------- status

def excel_status() -> ExcelStatus:
    """Whether Excel can be started here (registry check, no Excel is started); ROTM_NO_EXCEL=1 forces unavailable.
    A job that then cannot start Excel raises common.EnvUnavailable naming the reason."""
    if os.environ.get(NO_EXCEL_ENV, "").strip() == "1":
        return ExcelStatus(False, None, f"{NO_EXCEL_ENV}=1 is set")
    if os.name != "nt":
        return ExcelStatus(False, None, f"Excel is driven through COM, which needs Windows; this is {sys.platform}")
    if not _powershell():
        return ExcelStatus(False, None, "Windows PowerShell (powershell.exe) was not found")
    if not _job_script().is_file():
        return ExcelStatus(False, None, f"the job script {_job_script().as_posix()} is missing")
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Excel.Application\CurVer") as key:
            curver = str(winreg.QueryValue(key, None))
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Excel.Application\CLSID") as key:
            clsid = str(winreg.QueryValue(key, None))
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"CLSID\{clsid}\LocalServer32") as key:
            server = str(winreg.QueryValue(key, None))
    except OSError:
        return ExcelStatus(False, None, "Microsoft Excel is not registered for automation on this PC "
                                        "(no Excel.Application in the registry)")
    exe = server.split(" /")[0].strip().strip('"')
    if exe and not Path(exe).is_file():
        return ExcelStatus(False, None, f"Excel is registered at {exe}, but that file does not exist")
    m = re.search(r"\.(\d+)$", curver)
    version = f"{m.group(1)}.0" if m else None
    return ExcelStatus(True, version, None, exe or None)


def _require_excel() -> ExcelStatus:
    status = excel_status()
    if not status.available:
        raise common.EnvUnavailable(f"Excel is not available: {status.reason}",
                                    fix="run this on a Windows PC with Microsoft Excel installed")
    return status


# ----------------------------------------------------------------------------- operations (checked here)

_HEX6 = re.compile(r"^[0-9A-Fa-f]{6}$")
_ADDR = re.compile(r"^(\$?[A-Za-z]{1,3}\$?\d+(:\$?[A-Za-z]{1,3}\$?\d+)?|\$?[A-Za-z]{1,3}:\$?[A-Za-z]{1,3}|\$?\d+:\$?\d+)$")
_SHEET_BAD = re.compile(r"[\[\]:*?/\\]")
_XL_PREFIX = re.compile(r"(?i)_xl(?:fn|ws|pm)\.")
_TEXT_RISK = re.compile(r"^\s*([-+=@']|\d|\.\d|true\s*$|false\s*$|#)", re.I)

_BORDER_STYLES = {   # name -> (LineStyle, Weight)
    "thin": (1, 2), "medium": (1, -4138), "thick": (1, 4), "hair": (1, 1), "dotted": (-4118, 2),
    "dashed": (-4115, 2), "double": (-4119, 4), "dashdot": (4, 2), "dashdotdot": (5, 2),
    "mediumdashed": (-4115, -4138), "none": (-4142, 2),
}
_BORDER_SIDES = {"top": (8, 12), "bottom": (9, 12), "left": (7, 11), "right": (10, 11)}
_H_ALIGN = {"general": 1, "left": -4131, "center": -4108, "centre": -4108, "right": -4152, "fill": 5,
            "justify": -4130, "center_across": 7, "centercontinuous": 7, "centeracrossselection": 7,
            "distributed": -4117}
_V_ALIGN = {"top": -4160, "center": -4108, "centre": -4108, "bottom": -4107, "justify": -4130,
            "distributed": -4117}


def _bad(op_index: int, op: str, text: str) -> common.Refusal:
    return common.Refusal(f"operation {op_index} ({op}): {text}", key="operations")


def hex_color(value: Any, *, where: str = "colour") -> str | None:
    """'FFF2CC', '#FFF2CC' or 'FFFFF2CC' (ARGB) -> 'FFF2CC'; None or 'none' -> None."""
    if value is None:
        return None
    text = str(value).strip().lstrip("#")
    if text.lower() in ("none", ""):
        return None
    if len(text) == 8 and re.fullmatch(r"[0-9A-Fa-f]{8}", text):
        text = text[2:]
    if not _HEX6.match(text):
        raise ValueError(f"{where} {value!r} is not a colour; write six hex digits such as FFF2CC")
    return text.upper()


def address_groups(ranges: Sequence[str], limit: int = 255) -> list[str]:
    """Join range addresses with commas into strings shorter than `limit` characters (Excel's Range() limit)."""
    groups: list[str] = []
    current = ""
    for r in ranges:
        r = r.strip()
        if not r:
            continue
        candidate = f"{current},{r}" if current else r
        if len(candidate) < limit:
            current = candidate
        else:
            if current:
                groups.append(current)
            current = r
    if current:
        groups.append(current)
    return groups


def _ranges(op: dict[str, Any], i: int, name: str) -> list[str]:
    raw = op.get("ranges", op.get("range"))
    if raw is None:
        raise _bad(i, name, "give 'ranges' (a list of addresses such as ['B4:J4', 'B7'])")
    items = [raw] if isinstance(raw, str) else list(raw)
    out: list[str] = []
    for item in items:
        for part in str(item).split(","):
            part = part.strip()
            if "!" in part:
                sheet, part = common.parse_ref(part)
                if sheet != op.get("sheet"):
                    raise _bad(i, name, f"range {item!r} names sheet {sheet!r}, but the operation is for sheet "
                                        f"{op.get('sheet')!r}; give the sheet once in 'sheet'")
            if not _ADDR.match(part):
                raise _bad(i, name, f"{part!r} is not a range address; write it as A1, B4:J4, C:C or 4:4")
            out.append(part.replace("$", "").upper())
    if not out:
        raise _bad(i, name, "'ranges' is empty")
    return out


def _border(value: Any, i: int) -> dict[str, Any]:
    """'none', 'top thin 000000', {'top': 'thin'}, {'top': {'style': 'thin', 'color': '000000'}} -> sides."""
    sides: dict[str, Any] = {}
    if isinstance(value, str):
        words = value.split()
        if words == ["none"]:
            value = {s: "none" for s in _BORDER_SIDES}
        else:
            if len(words) < 2:
                raise _bad(i, "style", f"border {value!r}: write 'none' or '<side> <style> [RRGGBB]', e.g. 'top thin 000000'")
            value = {words[0]: {"style": words[1], "color": words[2] if len(words) > 2 else None}}
    if not isinstance(value, dict):
        raise _bad(i, "style", f"border {value!r} is not a border description")
    for side, spec in value.items():
        keys = ["top", "bottom", "left", "right"] if side == "all" else [side]
        for key in keys:
            if key not in _BORDER_SIDES:
                raise _bad(i, "style", f"border side {side!r}: use top, bottom, left, right or all")
            style, color = (spec, None) if isinstance(spec, str) or spec is None else (spec.get("style"), spec.get("color"))
            style_key = str(style or "none").lower().replace("_", "")
            if style_key not in _BORDER_STYLES:
                raise _bad(i, "style", f"border style {style!r}: use one of {', '.join(sorted(_BORDER_STYLES))}")
            line, weight = _BORDER_STYLES[style_key]
            try:
                col = hex_color(color, where="border colour")
            except ValueError as exc:
                raise _bad(i, "style", str(exc)) from None
            sides[key] = {"indices": list(_BORDER_SIDES[key]), "line": line, "weight": weight, "color": col}
    return sides


def _align(value: Any, i: int) -> dict[str, Any]:
    if isinstance(value, str):
        value = {"horizontal": value}
    if not isinstance(value, dict):
        raise _bad(i, "style", f"align {value!r}: write 'left', 'center', 'right' or a dict")
    out: dict[str, Any] = {}
    h = value.get("horizontal")
    if h is not None:
        key = str(h).lower().replace(" ", "_")
        if key not in _H_ALIGN:
            raise _bad(i, "style", f"horizontal alignment {h!r}: use one of {', '.join(sorted(_H_ALIGN))}")
        out["horizontal"] = _H_ALIGN[key]
    v = value.get("vertical")
    if v is not None:
        key = str(v).lower()
        if key not in _V_ALIGN:
            raise _bad(i, "style", f"vertical alignment {v!r}: use one of {', '.join(sorted(_V_ALIGN))}")
        out["vertical"] = _V_ALIGN[key]
    if value.get("wrap") is not None:
        out["wrap"] = bool(value["wrap"])
    if value.get("indent") is not None:
        out["indent"] = int(value["indent"])
    return out


def strip_prefixes(formula: str) -> str:
    """Remove the file-format prefixes _xlfn., _xlws., _xlpm. outside text literals: Excel's COM Formula
    property takes the plain function names (design 6.6 keeps the prefixes only in the file)."""
    parts = formula.split('"')
    for k in range(0, len(parts), 2):
        parts[k] = _XL_PREFIX.sub("", parts[k])
    return '"'.join(parts)


def excel_serial(value: _dt.date | _dt.datetime) -> float:
    """A date or datetime as an Excel serial number (1900 date system)."""
    base = _dt.datetime(1899, 12, 30)
    if isinstance(value, _dt.datetime):
        moment = value.replace(tzinfo=None)
    else:
        moment = _dt.datetime(value.year, value.month, value.day)
    delta = moment - base
    return delta.days + delta.seconds / 86400 + delta.microseconds / 86400e6


def _col(value: Any, i: int, name: str) -> int:
    if isinstance(value, int) and value >= 1:
        return value
    try:
        return common.col_index(str(value).strip().replace("$", ""))
    except ValueError:
        raise _bad(i, name, f"{value!r} is not a column; write a letter such as H or a number such as 8") from None


def _look(spec: dict[str, Any], i: int, name: str) -> dict[str, Any]:
    """The font, fill, number_format, border and align keys of a style operation (or of one look in a
    'formats' key) in the form excel_job.ps1 expects."""
    o: dict[str, Any] = {}
    if spec.get("font") is not None:
        font = dict(spec["font"])
        f: dict[str, Any] = {}
        for key in ("name", "size", "bold", "italic", "underline"):
            if font.get(key) is not None:
                f[key] = font[key]
        if "color" in font:
            try:
                f["color"] = hex_color(font["color"], where="font colour")
            except ValueError as exc:
                raise _bad(i, name, str(exc)) from None
        o["font"] = f
    if "fill" in spec:
        try:
            o["fill"] = hex_color(spec["fill"], where="fill")
        except ValueError as exc:
            raise _bad(i, name, str(exc)) from None
    if spec.get("number_format") is not None:
        o["number_format"] = str(spec["number_format"])
    if spec.get("border") is not None:
        o["border"] = _border(spec["border"], i)
    if spec.get("align") is not None:
        o["align"] = _align(spec["align"], i)
    return o


def _formats(value: Any, i: int, name: str) -> dict[str, Any] | None:
    """The 'formats' key of pq_attach and pq_refresh (v2, A-aera-8): {sheet: {"columns": [look per table column,
    in order; None leaves a column as it is], "header": look}}, each look with the keys of a style operation.
    None or {} applies no format (used to see what a refresh done by hand in Excel does)."""
    if not value:
        return None
    if not isinstance(value, dict):
        raise _bad(i, name, "'formats' is {sheet: {\"columns\": [...], \"header\": {...}}}")
    out: dict[str, Any] = {}
    for sheet, fmt in value.items():
        if not isinstance(fmt, dict):
            raise _bad(i, name, f"formats of sheet {sheet!r}: give {{\"columns\": [...], \"header\": {{...}}}}")
        cols = [(_look(c, i, name) if isinstance(c, dict) else None) for c in (fmt.get("columns") or [])]
        header = _look(fmt["header"], i, name) if isinstance(fmt.get("header"), dict) else None
        out[str(sheet)] = {"columns": cols, "header": header}
    return out


def raw_formats(workbook: str | Path, *, sheets: Sequence[str] = ()) -> dict[str, Any] | None:
    """The look of each Power Query raw table, from the workbook's sidecar manifest (v2, A-aera-8): for the raw
    entries with landing power_query and for `sheets` (the sheet a pq_attach loads, whose manifest entry may
    still say static): per column the number format of its recorded 'formats' key (converted with the model
    folder's dictionary, style_overrides.json included; the column type's format when no key is recorded) and the
    dictionary's raw-cell look (font name, size, colour, bold, italic, fill, alignment) at the sheet's size; for
    the header row the col_header look. None when the workbook has no manifest or no such raw entry."""
    from . import dictionary as dmod
    from . import manifest as mmod
    from . import styles
    p = Path(os.path.abspath(common.native_path(workbook)))
    data = mmod.load_manifest(p)
    if not data:
        return None
    entries = [e for e in data.get("sheets") or [] if e.get("type") == "raw"
               and (e.get("landing") == "power_query" or e.get("name") in set(sheets))]
    if not entries:
        return None
    root = _model_root(p)
    spec_dict = None
    spec_name = data.get("spec")
    if spec_name:
        for base in (p.parent, root):
            if base is not None and (Path(base) / spec_name).is_file():
                try:
                    spec_dict = (common.read_json(Path(base) / spec_name) or {}).get("dictionary")
                except (OSError, ValueError, AttributeError):
                    spec_dict = None
                break
    d = dmod.load_for_model(root, spec_override=spec_dict)
    cell_attrs = ("font_name", "font_size", "font_color", "bold", "italic", "fill", "number_format", "align")
    out: dict[str, Any] = {}
    for e in entries:
        size = e.get("font_size") if e.get("font_size") in (8, 9) else d.sheet_size("raw")
        types = list(e.get("types") or [])
        keys = list(e.get("formats") or []) if isinstance(e.get("formats"), list) else []
        cols = []
        for ci, ctype in enumerate(types):
            key = keys[ci] if ci < len(keys) and isinstance(keys[ci], str) and keys[ci] else \
                d.format_for_column_type(ctype)
            sample = "text" if ctype == "text" else 0
            cols.append(styles.style_spec(d, "raw", size=size, format_key=key, value=sample, attributes=cell_attrs))
        header = styles.style_spec(d, "col_header", size=size, format_key="general", value="header")
        out[str(e["name"])] = {"columns": cols, "header": header}
    return out


def _with_raw_formats(workbook: Path, operations: list[dict[str, Any]], notes: list[str]) -> list[dict[str, Any]]:
    """Add the 'formats' key (raw_formats()) to every pq_attach and pq_refresh operation that has none. Without a
    manifest the operations stay as they are. A manifest that cannot be read leaves them as they are, with a note."""
    need = [k for k, o in enumerate(operations) if isinstance(o, dict) and o.get("op") in ("pq_attach", "pq_refresh")
            and "formats" not in o]
    if not need:
        return operations
    sheets = [str(o.get("sheet")) for o in operations if isinstance(o, dict) and o.get("op") == "pq_attach"]
    try:
        formats = raw_formats(workbook, sheets=sheets)
    except (common.RotmError, OSError, ValueError, KeyError) as exc:
        notes.append(f"the raw tables' formats were not applied after the query load: {exc}")
        return operations
    if not formats:
        return operations
    out = list(operations)
    for k in need:
        out[k] = dict(out[k], formats=formats)
    return out


def normalize_operations(operations: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Check every operation and put it in the form excel_job.ps1 expects. Raises common.Refusal naming the
    operation and the key to change."""
    if isinstance(operations, dict):
        operations = [operations]
    out: list[dict[str, Any]] = []
    for i, op in enumerate(operations, 1):
        if not isinstance(op, dict) or "op" not in op:
            raise _bad(i, "?", "each operation is a dict with an 'op' key")
        name = str(op["op"])
        if name not in OPS:
            raise _bad(i, name, f"unknown operation; use one of {', '.join(OPS)}")
        o: dict[str, Any] = {"op": name}
        if name in ("style", "formula", "value", "fill_down", "clear", "pq_attach"):
            sheet = op.get("sheet")
            if not isinstance(sheet, str) or not sheet:
                raise _bad(i, name, "give 'sheet' (the worksheet name)")
            o["sheet"] = sheet
        if name == "recalc":
            mode = op.get("mode", "full_rebuild")
            if mode not in ("full_rebuild", "full", "calculate"):
                raise _bad(i, name, f"mode {mode!r}: use full_rebuild, full or calculate")
            o["mode"] = mode
        elif name == "read":
            refs = op.get("refs") or []
            if isinstance(refs, str):
                refs = [refs]
            o["refs"] = []
            for ref in refs:
                sheet, addr = common.parse_ref(str(ref))
                o["refs"].append({"ref": str(ref), "sheet": sheet, "address": addr.replace("$", "")})
        elif name == "errors":
            o["limit"] = int(op.get("limit", 5000))
            if op.get("sheets") is not None:
                o["sheets"] = [str(s) for s in op["sheets"]]
        elif name == "inventory":
            pass
        elif name == "style":
            o["addresses"] = address_groups(_ranges(op, i, name))
            o.update(_look(op, i, name))
            if len(o) <= 3:
                raise _bad(i, name, "give at least one of font, fill, number_format, border, align")
        elif name == "formula":
            cell = str(op.get("cell", "")).replace("$", "")
            if not _ADDR.match(cell):
                raise _bad(i, name, f"cell {op.get('cell')!r} is not an address such as D7")
            formula = op.get("formula")
            if not isinstance(formula, str) or not formula.strip():
                raise _bad(i, name, "give 'formula', for example '=SUM(D4:D6)'")
            formula = formula.strip()
            if not formula.startswith("="):
                formula = "=" + formula
            o.update(cell=cell, formula=strip_prefixes(formula), dynamic=bool(op.get("dynamic", False)))
        elif name == "value":
            cell = str(op.get("cell", "")).replace("$", "")
            if not _ADDR.match(cell):
                raise _bad(i, name, f"cell {op.get('cell')!r} is not an address such as D7")
            value = op.get("value")
            as_text = False
            if isinstance(value, _dt.date):
                value = excel_serial(value)
            elif isinstance(value, str):
                if value.startswith("="):
                    raise _bad(i, name, "a value starting with '=' is a formula; use a 'formula' operation")
                as_text = bool(_TEXT_RISK.match(value))
            elif value is not None and not isinstance(value, (bool, int, float)):
                raise _bad(i, name, f"value {value!r} must be text, a number, true/false, a date or null")
            o.update(cell=cell, value=value, as_text=as_text)
        elif name == "fill_down":
            try:
                from_row, to_row = int(op["from_row"]), int(op["to_row"])
            except (KeyError, TypeError, ValueError):
                raise _bad(i, name, "give 'from_row' and 'to_row' as row numbers") from None
            if from_row < 1 or to_row < from_row:
                raise _bad(i, name, f"rows {from_row} to {to_row}: to_row must be at least from_row, both 1 or more")
            first, last = _col(op.get("first_col", 1), i, name), _col(op.get("last_col"), i, name)
            if last < first:
                raise _bad(i, name, "last_col is left of first_col")
            o.update(from_row=from_row, to_row=to_row, first_col=first, last_col=last)
        elif name == "add_sheet":
            sheet = str(op.get("name", ""))
            if not sheet or len(sheet) > 31 or _SHEET_BAD.search(sheet) or sheet.startswith("'"):
                raise _bad(i, name, f"sheet name {sheet!r}: 1 to 31 characters, none of [ ] : * ? / \\")
            o.update(name=sheet, after=op.get("after"))
        elif name == "clear":
            o["addresses"] = address_groups(_ranges(op, i, name))
            what = op.get("what", "contents")
            if what not in ("contents", "all", "formats"):
                raise _bad(i, name, f"what {what!r}: use contents, all or formats")
            o["what"] = what
        elif name == "render":
            o["out_dir"] = str(op.get("out_dir", ""))
            if op.get("sheets") is not None:
                o["sheets"] = [str(s) for s in op["sheets"]]
            o["max_rows"] = int(op.get("max_rows", 200))
            o["max_cols"] = int(op.get("max_cols", 40))
        elif name == "pq_attach":
            for key in ("table_name", "query_name", "m_text"):
                if not isinstance(op.get(key), str) or not op[key].strip():
                    raise _bad(i, name, f"give '{key}'")
            if not re.fullmatch(r"[A-Za-z_\\][A-Za-z0-9_.]{0,254}", op["table_name"]):
                raise _bad(i, name, f"table name {op['table_name']!r}: letters, digits, '_' and '.', no spaces")
            o.update(table_name=op["table_name"], query_name=op["query_name"], m_text=op["m_text"])
            o["formats"] = _formats(op.get("formats"), i, name)
        elif name == "pq_refresh":
            o["wait_s"] = float(op.get("wait_s", 300))
            o["formats"] = _formats(op.get("formats"), i, name)
        elif name == "sleep":
            o["seconds"] = float(op.get("seconds", 1))
        out.append(o)
    return out


# ----------------------------------------------------------------------------- jobs

def _jobs_root() -> Path:
    return common.state_dir(create=True) / "jobs"


def _prune_jobs(root: Path) -> None:
    try:
        now = time.time()
        for child in root.iterdir():
            if child.is_dir() and now - child.stat().st_mtime > JOB_FOLDER_MAX_AGE_S:
                shutil.rmtree(child, ignore_errors=True)
    except OSError:
        pass


def _new_job_dir() -> Path:
    root = _jobs_root()
    root.mkdir(parents=True, exist_ok=True)
    _prune_jobs(root)
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = root / f"{stamp}_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    path.mkdir()
    return path


def _py_value(v: Any) -> Any:
    if isinstance(v, dict) and set(v) == {"error"}:
        return ExcelError(v["error"])
    if isinstance(v, list):
        return [_py_value(x) for x in v]
    return v


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "mbcs"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("latin-1", errors="replace")


def _check_workbook(path: str | Path, *, role: str = "workbook") -> Path:
    p = Path(path).expanduser()
    p = p.resolve() if p.exists() else Path(os.path.abspath(p))
    if not p.is_file():
        raise common.Refusal(f"{p.as_posix()}: no such file", key=role, fix="give the path of an existing workbook")
    if p.suffix.lower() not in EXCEL_SUFFIXES:
        raise common.Refusal(f"{p.as_posix()}: not an Excel workbook ({p.suffix or 'no extension'})", key=role,
                             fix="give an .xlsx, .xlsm, .xlsb or .xls file")
    return p


def _check_output(source: Path, out: str | Path) -> Path:
    o = Path(out).expanduser()
    o = Path(os.path.abspath(o))
    suffix = o.suffix.lower()
    if suffix not in EXCEL_SUFFIXES:
        raise common.Refusal(f"{o.as_posix()}: the output must end in .xlsx, .xlsm, .xlsb or .xls", key="out")
    if source.suffix.lower() in MACRO_SUFFIXES and suffix == ".xlsx" and source.suffix.lower() != ".xlsx":
        raise common.Refusal(f"{source.name} may hold macros, and saving it as {o.name} (.xlsx) would drop them",
                             key="out", fix=f"save it with the same extension ({source.suffix})")
    return o


def run_job(workbook: str | Path, operations: Sequence[dict[str, Any]], *, save: bool = False,
            save_as: str | Path | None = None, timeout_s: float | None = None,
            read_only: bool | None = None, _treat_as_foreign: bool = False) -> JobResult:
    """Run operations (design 12: style, formula, value, fill_down, add_sheet, clear, recalc, read, errors,
    inventory, render, pq_attach, pq_refresh) on one workbook in one new Excel instance.

    - `save`: save the workbook in place; `save_as`: save to that path instead (the original is not changed).
    - Without save or save_as the workbook is opened read-only and nothing is written.
    - Raises common.Refusal for bad input, common.EnvUnavailable when Excel is not available or cannot start,
      common.ExcelBusy when the lock is not free in time. A job that ran but failed returns ok=False with errors;
      when an operation fails nothing is saved.
    """
    src = _check_workbook(workbook)
    pre_notes: list[str] = []
    listed = [operations] if isinstance(operations, dict) else list(operations)
    ops = normalize_operations(_with_raw_formats(src, listed, pre_notes))
    out = _check_output(src, save_as) if save_as is not None else None
    if out is not None and out == src:
        out, save = None, True
    # The source is opened for writing only when it is saved in place; SaveAs to another file works on a
    # read-only copy in Excel, so the original is never locked or changed by Excel.
    if read_only is None:
        read_only = not (save and out is None)
    if read_only and save and out is None:
        raise common.Refusal("read_only and save in place cannot both be set", key="read_only")
    _require_excel()
    job_timeout = _timeout(timeout_s)
    started = time.monotonic()
    with excel_lock():
        job_dir = _new_job_dir()
        result = JobResult(ok=False, pid=None)
        result.notes += pre_notes
        keep = os.environ.get(KEEP_JOBS_ENV, "").strip() == "1"
        try:
            work_src = src
            if len(str(src)) > PATH_LIMIT:
                work_src = job_dir / ("book" + src.suffix.lower())
                shutil.copy2(src, work_src)
                result.notes.append(f"the path is longer than {PATH_LIMIT} characters, so Excel worked on a copy")
            work_out = out
            if out is not None:
                out.parent.mkdir(parents=True, exist_ok=True)
                if len(str(out)) > PATH_LIMIT:
                    work_out = job_dir / ("out" + out.suffix.lower())
            render_dirs: dict[int, tuple[Path, Path]] = {}
            for n, o in enumerate(ops):
                if o["op"] == "render":
                    final = Path(o["out_dir"]) if o["out_dir"] else job_dir / "render_out"
                    tmp = job_dir / f"render_{n + 1}"
                    tmp.mkdir()
                    render_dirs[n] = (tmp, final)
                    o["out_dir"] = str(tmp)
            before = excel_pids()
            pid_file = job_dir / "pid.txt"
            result_file = job_dir / "result.json"
            job = {"rotm_excel_job": 1, "workbook": str(work_src), "read_only": bool(read_only),
                   "save": bool(save and work_out is None), "save_as": str(work_out) if work_out else None,
                   "file_format": EXCEL_SUFFIXES[(out or src).suffix.lower()], "pid_file": str(pid_file),
                   "progid": os.environ.get(PROGID_ENV) or "Excel.Application",
                   "treat_as_foreign": bool(_treat_as_foreign), "before_pids": sorted(before),
                   "operations": ops}
            job_file = job_dir / "job.json"
            job_file.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
            cmd = [_powershell() or "powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                   "-File", str(_job_script()), "-Job", str(job_file), "-Result", str(result_file)]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    creationflags=_CREATE_NO_WINDOW if os.name == "nt" else 0)
            try:
                stdout, stderr = proc.communicate(timeout=job_timeout)
            except subprocess.TimeoutExpired:
                result.timed_out = True
                proc.kill()
                stdout, stderr = proc.communicate()
            pid = None
            try:
                pid_text = pid_file.read_text(encoding="utf-8").strip()
                pid = int(pid_text) if pid_text.isdigit() else None
            except OSError:
                pid = None
            data: dict[str, Any] = {}
            if result_file.is_file():
                try:
                    data = json.loads(result_file.read_text(encoding="utf-8-sig"))
                except ValueError:
                    data = {}
            result.pid = data.get("pid") or pid
            result.attached = bool(data.get("attached"))
            result.killed = bool(data.get("killed"))
            result.excel_version = data.get("excel_version")
            result.steps = list(data.get("steps") or [])
            result.errors = [str(e) for e in (data.get("errors") or [])]
            result.notes += [str(n) for n in (data.get("notes") or [])]
            if data.get("start_failed"):
                raise common.EnvUnavailable(
                    "Excel is not available: " + (result.errors[0] if result.errors else "Excel could not be started"),
                    fix="check that Microsoft Excel starts on this PC (open it once by hand), then run again")
            if result.timed_out:
                result.errors.append(f"the Excel job did not finish within {job_timeout:.0f} s and was stopped")
            elif not data:
                tail = (_decode(stderr) or _decode(stdout)).strip().replace("\r", " ").replace("\n", " ")[-400:]
                result.errors.append("the Excel job wrote no result" + (f": {tail}" if tail else "")
                                     + f" (PowerShell exit code {proc.returncode})")
            # Orphan rule (design 12): the PID this job started must be gone afterwards.
            if result.pid and not result.attached and result.pid not in before and pid_alive(result.pid):
                result.killed = _kill(result.pid) or result.killed
                result.notes.append(f"Excel PID {result.pid} was still running after the job and was stopped")
            if result.timed_out and not result.pid:
                # v4 (C-sol-10): excel_job.ps1 writes its Excel PID to the PID file as its first action after Excel
                # starts. Without that file no automation Excel that appeared during the job is known to be this
                # job's, so none is stopped; each is named.
                for extra in sorted(set(_automation_excel_pids()) - before):
                    result.notes.append(TIMEOUT_NO_PID_NOTE.format(pid=extra))
            result.ok = bool(data.get("ok")) and not result.timed_out and not result.errors
            if result.ok:
                saved = data.get("saved")
                if saved:
                    saved_path = Path(saved)
                    # v2 (O-1): no absolute path or user name of this PC in the saved file
                    note = clean_saved_workbook(saved_path)
                    if note:
                        result.notes.append(note)
                    # v2 (A-aera-5): formula cells the saved file holds without a value; v3 (A-aera-15): volatile
                    # empty-text results and volatile cells Excel names as circular that are not on a cycle are
                    # left out
                    _merge_cells_without_value(result.steps, ops, saved_path)
                    if work_out is not None and out is not None and work_out != out:
                        _replace(saved_path, out)
                        saved_path = out
                    elif work_src != src and save:
                        _replace(saved_path, src)
                        saved_path = src
                    result.saved_path = saved_path
                else:
                    # v3 (A-aera-15): without a save, a volatile cell Excel names as circular that is not on a cycle
                    # is left out
                    _drop_named_volatile_cells(result.steps, work_src)
                for n, (tmp, final) in render_dirs.items():
                    _move_renders(result.steps, n + 1, tmp, final)
        finally:
            result.elapsed_s = round(time.monotonic() - started, 3)
            _log_job(src, ops, result)
            if result.ok and not keep:
                shutil.rmtree(job_dir, ignore_errors=True)
            elif not result.ok:
                result.notes.append(f"job folder kept for diagnosis: {job_dir.as_posix()}")
    return result


def job_log_path() -> Path:
    """<state dir>/excel_jobs.jsonl: one JSON line per Excel job."""
    return common.state_dir() / "excel_jobs.jsonl"


def _log_job(workbook: Path, ops: list[dict[str, Any]], result: JobResult) -> None:
    line = {"at": common.now_iso(), "pid": result.pid, "workbook": workbook.as_posix(),
            "ops": [o["op"] for o in ops], "ok": result.ok, "killed": result.killed, "attached": result.attached,
            "timed_out": result.timed_out, "elapsed_s": result.elapsed_s, "caller_pid": os.getpid()}
    try:
        path = job_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read_job_log(since: str | None = None) -> list[dict[str, Any]]:
    """The job log's entries (at or after the ISO time `since` when given). Unreadable lines are skipped."""
    out: list[dict[str, Any]] = []
    try:
        lines = job_log_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for text in lines:
        try:
            entry = json.loads(text)
        except ValueError:
            continue
        if isinstance(entry, dict) and (since is None or str(entry.get("at", "")) >= since):
            out.append(entry)
    return out


def orphan_pids(entries: Sequence[dict[str, Any]] | None = None) -> list[int]:
    """PIDs recorded in the job log (or in `entries`) that are still running: Excel instances left behind."""
    rows = read_job_log() if entries is None else entries
    running = excel_pids()                       # a recorded PID reused by another program is not an orphan
    return sorted({int(e["pid"]) for e in rows if e.get("pid") and not e.get("attached") and int(e["pid"]) in running})


def _replace(src: Path, dest: Path) -> None:
    """Copy `src` over `dest` through a temporary file in dest's folder, then replace."""
    tmp = dest.with_name(f".{dest.name}.{uuid.uuid4().hex[:6]}.tmp")
    shutil.copy2(src, tmp)
    try:
        os.replace(tmp, dest)
    except OSError:
        if tmp.exists():
            tmp.unlink()
        raise common.RotmError(f"{dest.as_posix()} could not be replaced; it may be open in another program",
                               fix="close it there and run again")


NO_VALUE_TEXT = "no value"                                   # a formula cell the saved file holds without a value
CIRCULAR_TEXT = "no value (circular reference)"              # the circular reference Excel reports on a sheet
_ABS_PATH_ALT = re.compile(rb"<mc:AlternateContent\b[^>]*>(?:(?!</mc:AlternateContent>).)*?<x15ac:absPath\b"
                           rb"(?:(?!</mc:AlternateContent>).)*?</mc:AlternateContent>", re.S)
_ABS_PATH = re.compile(rb"<x15ac:absPath\b[^>]*/>|<x15ac:absPath\b[^>]*>.*?</x15ac:absPath>", re.S)
_LAST_BY = re.compile(rb"<cp:lastModifiedBy\s*/>|<cp:lastModifiedBy>.*?</cp:lastModifiedBy>", re.S)


def creator_text() -> str:
    """The creator text the plugin writes into docProps/core.xml (build sets dc:creator to it)."""
    return f"{common.TOOL} {common.VERSION}"


def clean_saved_workbook(path: str | Path) -> str | None:
    """Remove what Excel writes about this PC on save (v2, O-1): the x15ac:absPath element (the folder the file was
    saved in) and its mc:AlternateContent wrapper from xl/workbook.xml, and the user name in docProps/core.xml
    cp:lastModifiedBy (set to creator_text()). Every other zip member keeps its bytes, name, order and time.
    Returns None when the file is clean afterwards (also when nothing had to change), else a note saying why it
    was not cleaned. A file that is not a zip package (.xls, .xlsb) is left as it is."""
    import zipfile
    p = Path(path)
    if not zipfile.is_zipfile(p):
        return None
    try:
        with zipfile.ZipFile(p) as z:
            infos = z.infolist()
            names = {i.filename for i in infos}
            wbx = z.read("xl/workbook.xml") if "xl/workbook.xml" in names else None
            core = z.read("docProps/core.xml") if "docProps/core.xml" in names else None
            new_wbx = wbx
            if wbx is not None and b"absPath" in wbx:
                new_wbx = _ABS_PATH.sub(b"", _ABS_PATH_ALT.sub(b"", wbx))
            new_core = core
            if core is not None:
                who = creator_text().encode("utf-8")
                new_core = _LAST_BY.sub(b"<cp:lastModifiedBy>" + who + b"</cp:lastModifiedBy>", core)
            if new_wbx == wbx and new_core == core:
                return None
            fd, tmp = tempfile.mkstemp(prefix=p.stem + ".", suffix=".clean" + p.suffix, dir=str(p.parent))
            os.close(fd)
            try:
                with zipfile.ZipFile(tmp, "w") as zo:
                    for info in infos:
                        data = z.read(info.filename)
                        if info.filename == "xl/workbook.xml":
                            data = new_wbx
                        elif info.filename == "docProps/core.xml":
                            data = new_core
                        zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                        zi.compress_type = info.compress_type
                        zi.external_attr = info.external_attr
                        zi.create_system = info.create_system
                        zo.writestr(zi, data)
            except BaseException:
                os.unlink(tmp)
                raise
        try:
            os.replace(tmp, p)
        except OSError:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        return f"the saved workbook was not cleaned of the folder path and user name Excel writes: {exc}"
    return None


def _named_circular(err_steps: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """The cells the 'errors' steps list as the circular reference Excel reports on a sheet (CIRCULAR_TEXT)."""
    return list(dict.fromkeys((str(c[0]), str(c[1]).replace("$", "")) for s in err_steps for c in s.get("cells") or []
                              if len(c) >= 3 and str(c[2]).startswith(CIRCULAR_TEXT)))


def _drop_cells(err_steps: list[dict[str, Any]], drop: set[tuple[str, str]]) -> None:
    """Remove the CIRCULAR_TEXT cells in `drop` from each 'errors' step; the step's count shrinks by them."""
    if not drop:
        return
    for s in err_steps:
        cells = [list(c) for c in s.get("cells") or []]
        kept = [c for c in cells if not (len(c) >= 3 and str(c[2]).startswith(CIRCULAR_TEXT)
                                         and (str(c[0]), str(c[1]).replace("$", "")) in drop)]
        if len(kept) != len(cells):
            s["cells"] = kept
            s["count"] = max(0, int(s.get("count") or 0) - (len(cells) - len(kept)))
            s["truncated"] = s["count"] > len(kept)


def _drop_named_volatile_cells(steps: list[dict[str, Any]], workbook_path: Path) -> None:
    """v3 (A-aera-15), for a job that did not save: remove from each 'errors' step a cell Excel named as the
    circular reference of its sheet that is not circular by workbook.cells_without_value (`not_circular`: it holds
    a volatile function or depends on one, and its references show no cycle or the file holds a value for it).
    Excel 16 names such a cell when another sheet holds a cycle (measured 2026-11-25). The formulas and values are
    read from the workbook the job opened."""
    err_steps = [s for s in steps if s.get("op") == "errors" and s.get("ok")]
    named = _named_circular(err_steps)
    if not named:
        return
    from . import workbook as wbmod
    try:
        found = wbmod.cells_without_value(workbook_path, named=named)
    except Exception:  # noqa: BLE001 - an unreadable package is reported by the commands that read it
        return
    _drop_cells(err_steps, set(found.not_circular))


def _merge_cells_without_value(steps: list[dict[str, Any]], ops: list[dict[str, Any]], saved: Path) -> None:
    """Complete each 'errors' step from the saved file (workbook.cells_without_value; v3, A-aera-15):

    - a cell Excel named as the circular reference of its sheet (CIRCULAR_TEXT) that holds a volatile function or
      depends on one, and whose references show no cycle or that holds a value in the saved file, is removed
      (Excel 16 names such a cell when another sheet holds a cycle; measured 2026-11-25);
    - each formula cell the saved file holds without a value is added: with the text CIRCULAR_TEXT when it lies on
      a cycle of references through a cell Excel named, else with NO_VALUE_TEXT. A cell the step lists already
      (for example as the circular reference) is not added again.

    The step's count changes by the cells removed and added."""
    err_steps = [s for s in steps if s.get("op") == "errors" and s.get("ok")]
    if not err_steps:
        return
    from . import workbook as wbmod
    try:
        found = wbmod.cells_without_value(saved, named=_named_circular(err_steps))
    except Exception:  # noqa: BLE001 - an unreadable package is reported by the commands that read it
        return
    _drop_cells(err_steps, set(found.not_circular))
    if not found.listed:
        return
    for s in err_steps:
        k = int(s.get("index") or 0) - 1
        op = ops[k] if 0 <= k < len(ops) else {}
        only = set(op.get("sheets") or []) or None
        limit = int(op.get("limit") or 5000)
        cells = [list(c) for c in s.get("cells") or []]
        seen = {(str(c[0]), str(c[1]).replace("$", "")) for c in cells if len(c) >= 2}
        added = 0
        for sheet, coord in found.listed:
            if (only is not None and sheet not in only) or (sheet, coord) in seen:
                continue
            seen.add((sheet, coord))
            added += 1
            if len(cells) < limit:
                text = CIRCULAR_TEXT if (sheet, coord) in found.on_named_cycle else NO_VALUE_TEXT
                cells.append([sheet, coord, text])
        s["cells"] = cells
        s["count"] = int(s.get("count") or 0) + added
        s["truncated"] = s["count"] > len(cells)


def _safe_name(sheet: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", sheet).strip().rstrip(".")
    return name or "sheet"


def png_size(path: str | Path) -> tuple[int, int] | None:
    """(width, height) in pixels from a PNG header, or None when the file is not a PNG."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", head[16:24])


def png_is_blank(path: str | Path, *, scan_rows: int = 80) -> bool | None:
    """True when the first `scan_rows` pixel rows of a PNG are one single colour (a blank render: every sheet
    shows its title or header row at the top, so a real render has more than one colour there); False when they
    are not; None when the file cannot be read as an 8-bit PNG. Standard library only (zlib)."""
    import zlib
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos, idat, width, height, bpp, stride, depth, palette = 8, bytearray(), 0, 0, 0, 0, 8, None
    try:
        while pos + 8 <= len(data):
            length, ctype = struct.unpack(">I4s", data[pos:pos + 8])
            chunk = data[pos + 8:pos + 8 + length]
            if ctype == b"IHDR":
                width, height, depth, color, _c, _f, interlace = struct.unpack(">IIBBBBB", chunk)
                channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color)
                if channels is None or interlace or depth not in (1, 2, 4, 8):
                    return None
                bpp = max(1, depth * channels // 8)
                stride = (width * depth * channels + 7) // 8
            elif ctype == b"PLTE":
                palette = len(chunk) // 3
            elif ctype == b"IDAT":
                idat += chunk
            elif ctype == b"IEND":
                break
            pos += 12 + length
        if palette == 1:
            return True                      # a palette of one colour: every pixel is that colour (Excel's blank export)
        rows = min(scan_rows, height)
        need = rows * (stride + 1)
        d = zlib.decompressobj()
        raw = d.decompress(bytes(idat), need)
    except (struct.error, zlib.error, ValueError):
        return None
    if len(raw) < need or not width:
        return None
    prev = bytearray(stride)
    first: bytes | None = None
    for r in range(rows):
        f = raw[r * (stride + 1)]
        line = bytearray(raw[r * (stride + 1) + 1:(r + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            if f == 1:
                line[i] = (line[i] + a) & 0xFF
            elif f == 2:
                line[i] = (line[i] + b) & 0xFF
            elif f == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 0xFF
            elif f == 4:
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
        px = bytes(line[:bpp])
        if first is None:
            first = px
        if depth < 8:                         # packed pixels: all bytes but the padded last one are equal
            if line[:-1] != bytearray(first * (stride - 1)):
                return False
        elif line != bytearray(first * width):
            return False
        prev = line
    return True


def _move_renders(steps: list[dict[str, Any]], index: int, tmp: Path, final: Path) -> None:
    """Move the job's sheet_<n>.png files to <final>/<sheet>.png and update the step's file paths."""
    step = next((s for s in steps if s.get("index") == index), None)
    if step is None:
        return
    final.mkdir(parents=True, exist_ok=True)
    used: set[str] = set()
    for item in step.get("files") or []:
        base = _safe_name(str(item.get("sheet", "sheet")))
        name = base
        k = 2
        while name.lower() in used:
            name = f"{base}_{k}"
            k += 1
        used.add(name.lower())
        src = Path(str(item.get("file", "")))
        dest = final / f"{name}.png"
        if item.get("ok") and src.is_file():
            shutil.move(str(src), str(dest))
            item["file"] = str(dest)
            size = png_size(dest)
            if size:
                item["width_px"], item["height_px"] = size
            else:
                item["ok"] = False
                item["problem"] = "the exported file is not a PNG image"
        else:
            item["file"] = None


# ----------------------------------------------------------------------------- the public operations

def recalc(workbook: str | Path, *, save: bool = True, out: str | Path | None = None,
           read: Sequence[str] = (), mode: str = "full_rebuild", errors_limit: int = 5000,
           timeout_s: float | None = None) -> RecalcResult:
    """CalculateFullRebuild, save (in place or to `out`), list error cells, read the given refs.
    `save=False` recalculates in memory only: the file keeps its old cached values."""
    src = _check_workbook(workbook)
    input_sha = common.file_sha256(src)
    ops: list[dict[str, Any]] = [{"op": "recalc", "mode": mode}, {"op": "errors", "limit": errors_limit}]
    if read:
        ops.append({"op": "read", "refs": list(read)})
    res = run_job(src, ops, save=save and out is None, save_as=out if save and out is not None else None,
                  timeout_s=timeout_s)
    errors_step = res.step("errors")
    cells = [tuple(str(x) for x in c) for c in errors_step.get("cells") or []]
    values: dict[str, Any] = {}
    problems: dict[str, str] = {}
    for item in res.step("read").get("values") or []:
        ref = str(item.get("ref"))
        if item.get("problem"):
            problems[ref] = str(item["problem"])
            values[ref] = None
        else:
            values[ref] = _py_value(item.get("value"))
    saved = res.saved_path
    return RecalcResult(ok=res.ok, saved_path=saved, input_sha256=input_sha,
                        output_sha256=common.file_sha256(saved) if res.ok and saved else None,
                        error_cells=cells, values=values, pid=res.pid, elapsed_s=res.elapsed_s,
                        errors=list(res.errors), error_count=int(errors_step.get("count") or len(cells)),
                        problems=problems, excel_version=res.excel_version,
                        calc_mode=res.step("recalc").get("calc_mode"), killed=res.killed, attached=res.attached,
                        timed_out=res.timed_out)


def read_cells(workbook: str | Path, refs: Sequence[str], *, recalc_first: bool = False) -> dict[str, Any]:
    """Values of `refs` ("sheet!A1", "sheet!A1:C3") as Excel holds them after opening (Range.Value2: numbers,
    text, booleans, None, ExcelError; dates come back as Excel serial numbers; ranges as lists of rows).
    Nothing is saved. Raises ExcelJobFailed when the job fails, common.Refusal for a reference that does not exist."""
    ops: list[dict[str, Any]] = [{"op": "recalc"}] if recalc_first else []
    ops.append({"op": "read", "refs": list(refs)})
    res = run_job(workbook, ops)
    if not res.ok:
        raise ExcelJobFailed(f"cells were not read: {res.message()}")
    out: dict[str, Any] = {}
    for item in res.step("read").get("values") or []:
        if item.get("problem"):
            raise common.Refusal(f"{item.get('ref')}: {item['problem']}", key="refs",
                                 fix="write each reference as sheet!A1 or sheet!A1:C3 with an existing sheet")
        out[str(item.get("ref"))] = _py_value(item.get("value"))
    return out


def find_errors(workbook: str | Path, *, recalc_first: bool = False, limit: int = 5000) -> list[tuple[str, str, str]]:
    """(sheet, cell, error text) for every cell holding an error value, up to `limit`. Nothing is saved."""
    ops: list[dict[str, Any]] = [{"op": "recalc"}] if recalc_first else []
    ops.append({"op": "errors", "limit": limit})
    res = run_job(workbook, ops)
    if not res.ok:
        raise ExcelJobFailed(f"error cells were not listed: {res.message()}")
    return [tuple(str(x) for x in c) for c in res.step("errors").get("cells") or []]


def inventory(workbook: str | Path) -> dict[str, Any]:
    """What Excel sees in the workbook: sheets (tables, charts, shapes, conditional formats, validation cells,
    query tables, pivot tables), defined names, Power Query queries, connections. Nothing is saved."""
    res = run_job(workbook, [{"op": "inventory"}])
    if not res.ok:
        raise ExcelJobFailed(f"the workbook was not inspected: {res.message()}")
    step = dict(res.step("inventory"))
    step.pop("op", None)
    step.pop("index", None)
    step.pop("ok", None)
    step.pop("ms", None)
    return step


def inventory_counts(inv: dict[str, Any]) -> dict[str, int]:
    """Counts to compare two inventories (design 11 step 6): sheets, tables, query tables, charts, shapes,
    conditional formats, validation cells, pivot tables, names, queries, connections."""
    sheets = [s for s in inv.get("sheets", [])]
    ws = [s for s in sheets if s.get("kind") == "Worksheet"]
    return {"sheets": len(sheets),
            "tables": sum(len(s.get("tables") or []) for s in ws),
            "query_tables": sum(sum(1 for t in s.get("tables") or [] if t.get("query")) + int(s.get("query_tables") or 0)
                                for s in ws),
            "charts": sum(int(s.get("charts") or 0) for s in ws) + sum(1 for s in sheets if s.get("kind") == "Chart"),
            "shapes": sum(int(s.get("shapes") or 0) for s in ws),
            "format_conditions": sum(int(s.get("format_conditions") or 0) for s in ws),
            "validation_cells": sum(int(s.get("validation_cells") or 0) for s in ws),
            "pivot_tables": sum(int(s.get("pivot_tables") or 0) for s in ws),
            "names": len(inv.get("names") or []),
            "queries": len(inv.get("queries") or []),
            "connections": len(inv.get("connections") or [])}


def render_sheets(workbook: str | Path, out_dir: str | Path, *, sheets: Sequence[str] | None = None,
                  max_rows: int = 200, max_cols: int = 40, recalc_first: bool = True) -> list[RenderedSheet]:
    """One PNG per sheet in `out_dir` (named <sheet>.png), with the rendered range and its size.
    The rendered range starts at A1 and ends at the used range, at most `max_rows` rows and `max_cols` columns.
    Writes <out_dir>/render.json and removes PNG files a previous render.json there listed and this one did not."""
    src = _check_workbook(workbook)
    final = Path(os.path.abspath(Path(out_dir)))

    def _job(only: Sequence[str] | None) -> list[RenderedSheet]:
        ops: list[dict[str, Any]] = [{"op": "recalc", "mode": "calculate"}] if recalc_first else []
        ops.append({"op": "render", "out_dir": str(final), "sheets": list(only) if only else None,
                    "max_rows": max_rows, "max_cols": max_cols})
        res = run_job(src, ops)
        step = res.step("render")
        if not res.ok:
            raise ExcelJobFailed(f"not rendered: {res.message()}")
        out: list[RenderedSheet] = []
        for item in step.get("files") or []:
            path = Path(item["file"]) if item.get("file") else None
            out.append(RenderedSheet(sheet=str(item.get("sheet")), path=path, range=item.get("range"),
                                     last_row=item.get("last_row"), last_col=item.get("last_col"),
                                     truncated=bool(item.get("truncated")), width_px=item.get("width_px"),
                                     height_px=item.get("height_px"),
                                     problem=None if item.get("ok") else str(item.get("problem") or "not rendered")))
        return out

    items = _job(sheets)
    # A PNG that is one colour at the top is a blank render (seen on this PC, 2026-11-25: one tall sheet of ten
    # exported white). Render those sheets again, at most twice; a sheet still blank is reported as a problem.
    for _attempt in range(2):
        blank = [i.sheet for i in items if i.path is not None and i.problem is None and png_is_blank(i.path)]
        if not blank:
            break
        again = {i.sheet: i for i in _job(blank)}
        items = [again.get(i.sheet, i) if i.sheet in blank else i for i in items]
    for i in items:
        if i.path is not None and i.problem is None and png_is_blank(i.path):
            i.problem = "the PNG is blank (one colour) after three renders; open the sheet in Excel and look at it there"
    if sheets:
        missing = [s for s in sheets if s not in {i.sheet for i in items}]
        if missing:
            raise common.Refusal(f"the workbook has no sheet named {', '.join(repr(m) for m in missing)}",
                                 key="sheets", fix="give sheet names exactly as the tabs show them")
    _write_render_index(final, src, items, all_sheets=not sheets)
    return items


def _write_render_index(folder: Path, workbook: Path, items: list[RenderedSheet], *, all_sheets: bool) -> None:
    """A render of every sheet replaces the index and deletes the PNG files the old index listed and this render
    did not write (for example a sheet that was removed). A render of some sheets merges into the index."""
    index = folder / "render.json"
    try:
        old = common.read_json(index) if index.is_file() else None
    except (OSError, ValueError):
        old = None
    old_entries = [e for e in (old.get("files") or []) if isinstance(e, dict)] if isinstance(old, dict) else []
    new_entries = [{"sheet": i.sheet, "file": i.path.name if i.path else None, "range": i.range,
                    "last_row": i.last_row, "last_col": i.last_col, "truncated": i.truncated,
                    "width_px": i.width_px, "height_px": i.height_px, "problem": i.problem} for i in items]
    written = {e["file"] for e in new_entries if e["file"]}
    if all_sheets:
        for entry in old_entries:
            name = str(entry.get("file") or "")
            if name and name not in written and "/" not in name and "\\" not in name and name.endswith(".png"):
                try:
                    (folder / name).unlink()
                except OSError:
                    pass
        entries = new_entries
    else:
        rendered = {i.sheet for i in items}
        entries = [e for e in old_entries if e.get("sheet") not in rendered] + new_entries
    common.write_json(index, {
        "rotm_render": 1, "workbook": workbook.as_posix(), "sha256": common.file_sha256(workbook),
        "at": common.now_iso(), "note": "rendered, not looked at by code", "files": entries})


def render(workbook: str | Path, out_dir: str | Path, *, sheets: Sequence[str] | None = None) -> list[Path]:
    """One PNG per sheet (Range.CopyPicture into a chart object, then Chart.Export). Returns the PNG paths."""
    return [i.path for i in render_sheets(workbook, out_dir, sheets=sheets) if i.path is not None]


def pq_attach(workbook: str | Path, *, sheet: str, table_name: str, query_name: str, m_text: str,
              save: bool = True, out: str | Path | None = None, timeout_s: float | None = None) -> JobResult:
    """Replace the static raw table on `sheet` with query `query_name` loaded as table `table_name` at A1;
    BackgroundQuery off; refresh and wait until done (design 7.3). An existing query of that name gets the new
    M text; a table already loaded from it is kept and refreshed. PreserveFormatting on, AdjustColumnWidth off;
    after the load the raw table's look from the workbook's manifest (raw_formats) is applied to its whole
    loaded range (v2, A-aera-8)."""
    op = {"op": "pq_attach", "sheet": sheet, "table_name": table_name, "query_name": query_name, "m_text": m_text}
    return run_job(workbook, [op], save=save and out is None, save_as=out if save else None, timeout_s=timeout_s)


def pq_refresh(workbook: str | Path, *, save: bool = True, wait_s: float = 300,
               out: str | Path | None = None, recalc_after: bool = True) -> JobResult:
    """BackgroundQuery off on every query table, refresh each one and every other connection, wait until Excel
    reports none is refreshing (at most `wait_s` seconds), recalculate, save. A failed refresh fails the job
    and nothing is saved; the step lists each table with its row count or its problem. PreserveFormatting on and
    AdjustColumnWidth off on every query table; after the refresh each raw table's look from the manifest is
    applied again to its whole loaded range, more rows or fewer (v2, A-aera-8)."""
    ops: list[dict[str, Any]] = [{"op": "pq_refresh", "wait_s": wait_s}]
    if recalc_after:
        ops.append({"op": "recalc"})
    return run_job(workbook, ops, save=save and out is None, save_as=out if save else None,
                   timeout_s=max(_timeout(), wait_s + 120))


def apply_edits(workbook: str | Path, edits: Sequence[dict[str, Any]], *, out: str | Path,
                recalc_after: bool = False, timeout_s: float | None = None) -> JobResult:
    """Apply edit operations through Excel and save as `out` (keeps queries, tables, charts, names, formats).
    `out` equal to the workbook saves in place. Edits: style, formula, value, fill_down, add_sheet, clear
    (shapes in normalize_operations). With `recalc_after`, Excel recalculates fully before saving."""
    ops = list(edits)
    if not ops:
        raise common.Refusal("no edits were given", key="edits")
    if recalc_after:
        ops.append({"op": "recalc"})
    src = _check_workbook(workbook)
    target = Path(os.path.abspath(Path(out)))
    if target == src:
        return run_job(src, ops, save=True, timeout_s=timeout_s)
    return run_job(src, ops, save_as=target, timeout_s=timeout_s)


# ----------------------------------------------------------------------------- subcommands

def _record(workbook: Path, step: str, entry: dict[str, Any]) -> tuple[Path | None, str | None]:
    """Record a step in <model>/reports/status.json when the workbook sits in a model folder.
    Returns (model root or None, a note when recording was not possible)."""
    try:
        from . import modelfolder
        root = modelfolder.find_model_root(workbook)
        if root is None:
            return None, None
        rel = workbook.resolve().relative_to(Path(root).resolve()).as_posix()
        modelfolder.record_step(root, rel, step, entry)
        return Path(root), None
    except (NotImplementedError, common.RotmError, ValueError, OSError) as exc:
        return None, f"status not recorded in reports/status.json: {exc}"


def _model_root(workbook: Path) -> Path | None:
    try:
        from . import modelfolder
        root = modelfolder.find_model_root(workbook)
        return Path(root) if root else None
    except (NotImplementedError, common.RotmError, OSError):
        return None


def add_recalc_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("workbook")
    p.add_argument("--read", nargs="*", default=[], metavar="REF", help="cells to read back, e.g. checks!C2")
    p.add_argument("--no-save", action="store_true", help="recalculate without saving")
    p.add_argument("--out", metavar="FILE", help="save the recalculated workbook here instead of in place")
    p.add_argument("--json", metavar="FILE", help="write the report as JSON ('-' = stdout)")
    p.add_argument("--quiet", action="store_true", help="print only the last line")


def _fmt(value: Any) -> str:
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return repr(value) if isinstance(value, str) and not isinstance(value, ExcelError) else str(value)


# v5 (A-aera-8, C-sol-8): an absolute path inside an error text: a drive (C:) or a share (\\server\share), then
# folders and a name. The first form may hold spaces and ends at a file extension; the second ends at a space.
_SEG = r"[^\\/:*?\"<>|\r\n;]"
_ROOT = r"(?<![A-Za-z0-9])(?:[A-Za-z]:|\\\\[^\\/\s:*?\"<>|]+[\\/][^\\/\s:*?\"<>|]+)[\\/]"
_ABS_PATH_TEXT = re.compile(
    _ROOT + rf"(?:{_SEG}*[\\/])*{_SEG}*?\.[A-Za-z][A-Za-z0-9]{{0,4}}(?=$|[\s'\",;:)\]]|\.(?:\s|$))"
    + "|" + _ROOT + r"[^\s:*?\"<>|;,'()\[\]]*")
_JOB_FOLDER = re.compile(r"^\d{8}_\d{6}_\d+_[0-9a-f]{6}$")    # _new_job_dir: <date>_<time>_<pid>_<6 hex digits>


def _path_pattern(path: Path) -> str:
    """A regular expression for `path` written with either separator (case-insensitive on Windows: see the caller)."""
    parts = [p for p in re.split(r"[\\/]+", str(path)) if p]
    head = r"[\\/]{2}" if str(path).startswith(("\\\\", "//")) else ""
    return (r"(?<![A-Za-z0-9])" + head + r"[\\/]+".join(re.escape(p) for p in parts)
            + r"(?=$|[\s:;,'\")\]]|\.(?:\s|$))")


def _is_working_copy(parts: list[str], book: Path) -> bool:
    """`parts` (a path split at its separators) name the copy run_job makes in a job folder for a workbook path longer
    than PATH_LIMIT: <state dir>/jobs/<job folder>/book<suffix>."""
    return (len(parts) >= 3 and parts[-3].lower() == "jobs" and bool(_JOB_FOLDER.match(parts[-2]))
            and parts[-1].lower() == "book" + (book.suffix.lower() or ".xlsx"))


def x_open_reason(workbook: str | Path, error: str) -> str:
    """v5 (A-aera-8, C-sol-8): the reason of the X-OPEN message, from one error of the job. Every absolute path of the
    workbook (as given, absolute or resolved, with either separator) and of its working copy in the job folder is
    replaced by the workbook's file name; any other absolute path is shortened to '.../<parent folder>/<file name>'.
    Only then is the reason cut to X_OPEN_ERROR_MAX characters, so a long path never causes the cut."""
    book = Path(workbook)
    text = str(error).replace("\r", " ").replace("\n", " ")
    flags = re.I if os.name == "nt" else 0
    forms: list[Path] = [book]
    for make in (lambda: Path(os.path.abspath(book)), lambda: book.resolve()):
        try:
            p = make()
        except (OSError, RuntimeError, ValueError):
            continue
        if p not in forms:
            forms.append(p)
    for p in sorted(forms, key=lambda q: len(str(q)), reverse=True):
        if p.is_absolute():
            text = re.sub(_path_pattern(p), lambda _m: book.name, text, flags=flags)

    def short(m: re.Match[str]) -> str:
        found = m.group(0)
        trail = ""
        while found and found[-1] in ".,;:)":
            found, trail = found[:-1], found[-1] + trail
        parts = [p for p in re.split(r"[\\/]+", found) if p]
        if _is_working_copy(parts, book):
            return book.name + trail
        rest = parts[2:] if found.startswith(("\\\\", "//")) else parts[1:]   # without the drive or the share
        if not rest:
            return m.group(0)
        return ".../" + "/".join(rest[-2:]) + trail

    text = _ABS_PATH_TEXT.sub(short, text)
    if len(text) > X_OPEN_ERROR_MAX:
        text = text[:X_OPEN_ERROR_MAX - 3] + "..."
    return text


def x_open_finding(workbook: str | Path, errors: Sequence[str], model_root: str | Path | None = None) -> common.Finding:
    """v4 (C-sol-3) rule X-OPEN (error): Excel started, but the job could not open or calculate `workbook` (Excel
    refused the file, an operation failed, or the job timed out). `errors`: the job's errors (JobResult.errors).
    The fix names the command to run again.
    v5 (A-aera-8, C-sol-8): the message is 'Excel started but could not open or calculate <file name>: <reason>',
    with the reason of x_open_reason (paths replaced or shortened, then cut); data.errors keeps the full texts.
    v6 (r5 verdict A-aera-6): the fix says which causes the strict audit finds (an incomplete formula, F-PARSE, and a
    listed function with too few or too many arguments, F-ARGS) and that for another cause the file is opened in
    Excel. With a model folder it names the rebuild between the correction and the check (v6 integrator, request
    v6_P8 R1): check does not build, so a corrected model.spec.json reaches the workbook only through build."""
    book = Path(workbook)
    first = x_open_reason(book, str(errors[0]) if errors else "no error was reported")
    rotm = f'python -P -X utf8 -B "{(common.scripts_dir() / "rotm.py").as_posix()}"'
    find = ("the most likely cause is a formula Excel rejects; the strict audit finds two such causes, an incomplete "
            "formula (F-PARSE) and a listed function with too few or too many arguments (F-ARGS): run "
            f'{rotm} audit "{book.as_posix()}" --strict')
    other = f"; for another cause, open {book.name} in Excel and read the message Excel shows"
    if model_root is not None:
        root = Path(model_root).as_posix()
        fix = (f'{find}, correct the expression in model.spec.json, rebuild ({rotm} build "{root}" --force), then run '
               f'{rotm} check "{root}" again{other}')
    else:
        fix = f'{find}, correct that formula, then run {rotm} recalc "{book.as_posix()}" again{other}'
    return common.Finding("X-OPEN", "error", f"Excel started but could not open or calculate {book.name}: {first}", fix,
                          data={"errors": [str(e) for e in errors]})


def run_recalc(args: argparse.Namespace) -> int:
    src = _check_workbook(args.workbook)
    report = common.Report("recalc", src.as_posix())
    status = excel_status()
    if not status.available:
        report.note(f"not recalculated: Excel is not available ({status.reason}); the workbook was not changed")
        report.finish(not_available=True)
        return report.emit(args.json, quiet=getattr(args, "quiet", False))
    try:
        res = recalc(src, save=not args.no_save, out=args.out, read=args.read or ())
    except common.EnvUnavailable as exc:
        report.note(f"not recalculated: {exc.message}; the workbook was not changed")
        report.finish(not_available=True)
        return report.emit(args.json, quiet=getattr(args, "quiet", False))
    report.data.update(pid=res.pid, elapsed_s=res.elapsed_s, excel=res.excel_version, calc_mode=res.calc_mode,
                       input_sha256=res.input_sha256, output_sha256=res.output_sha256,
                       saved=res.saved_path.as_posix() if res.saved_path else None,
                       values={k: (str(v) if isinstance(v, ExcelError) else v) for k, v in res.values.items()},
                       error_cells=[list(c) for c in res.error_cells], error_count=res.error_count)
    if not res.ok and res.attached:
        report.note("not recalculated: " + "; ".join(res.errors) + "; nothing was saved")
        report.finish(not_available=True)
        return report.emit(args.json, quiet=getattr(args, "quiet", False))
    if not res.ok:
        # v4 (C-sol-3): Excel started, but it could not open or calculate the workbook (a timeout too): exit 1
        report.add(x_open_finding(src, res.errors, _model_root(src)))
        report.note(X_OPEN_NOTE)
        report.finish(False)
        return report.emit(args.json, quiet=getattr(args, "quiet", False))
    report.verified["recalculated"] = True
    where = f"saved to {res.saved_path.as_posix()}" if res.saved_path else "not saved (--no-save): the file keeps its old cached values"
    report.note(f"recalculated in Excel {res.excel_version or ''} in {res.elapsed_s:.1f} s; {where}; "
                f"Excel PID {res.pid} {'was stopped after Quit' if res.killed else 'closed'}".replace("  ", " "))
    if res.calc_mode and res.calc_mode != "automatic":
        report.note(f"the workbook's calculation mode is {res.calc_mode}")
    for ref, value in res.values.items():
        if ref in res.problems:
            report.error("X-READ", f"{ref} could not be read: {res.problems[ref]}",
                         "write the reference as sheet!A1 with an existing sheet name")
        else:
            report.note(f"read {ref} = {_fmt(value)}")
    rotm = f'python -P -X utf8 -B "{(common.scripts_dir() / "rotm.py").as_posix()}"'
    book = (res.saved_path or src).as_posix()
    root = _model_root(src)
    again = f'{rotm} check "{root.as_posix()}"' if root else f'{rotm} recalc "{book}"'
    for sheet, cell, text in res.error_cells:
        ref = f"{common.quote_sheet(sheet)}!{cell}"
        if text.startswith(CIRCULAR_TEXT):
            # v2 (A-aera-5) rule V-NO-VALUE, as in check; v3 (A-aera-15) fix C-CYCLE: only for a cell on the cycle
            # Excel names as circular
            report.error("V-NO-VALUE", f"{text}: the formula has no value after the recalculation",
                         "a formula that refers to itself through other cells has no value: find the cycle with "
                         f'{rotm} trace "{book}" {ref} and break it', sheet=sheet, cell=cell)
            continue
        if text.startswith(NO_VALUE_TEXT):
            # v3 (A-aera-15) fix N-RECALC: a formula without a value that Excel did not name as circular
            report.error("V-NO-VALUE", f"{text}: the formula has no value after the recalculation",
                         "Excel saved this formula without a value and named no circular reference for it: "
                         f'recalculate the workbook ({again}) and look at the formula and its inputs '
                         f'({rotm} trace "{book}" {ref})', sheet=sheet, cell=cell)
            continue
        report.error("V-ERROR", f"error value {text}", "trace the cell (rotm.py trace) and correct the formula or its inputs",
                     sheet=sheet, cell=cell)
    if res.error_count > len(res.error_cells):
        report.note(f"{res.error_count} error cells in all; the first {len(res.error_cells)} are listed")
    code = report.finish(False)
    if res.saved_path:
        _root, note = _record(res.saved_path, "recalc", {
            "at": report.at, "exit_code": code, "input_sha256": res.input_sha256,
            "output_sha256": res.output_sha256, "excel": res.excel_version, "error_cells": res.error_count})
        if note:
            report.note(note)
    return report.emit(args.json, quiet=getattr(args, "quiet", False))


RENDER_LIMITS_HELP = (
    "A render covers rows 1 to 200 and columns A to AN (40 columns) of each sheet by default; --max-rows and "
    "--max-cols render more. render.json marks a sheet that was cut off with \"truncated\": true and gives its "
    "last row and last column (last_row, last_col), so rows and columns beyond the picture are named.")


def add_render_arguments(p: argparse.ArgumentParser) -> None:
    p.epilog = RENDER_LIMITS_HELP
    p.add_argument("workbook")
    p.add_argument("--out", help="folder for the PNG files (default <model>/reports/render/)")
    p.add_argument("--sheets", help="comma-separated sheet names (default all)")
    p.add_argument("--max-rows", type=int, default=200, help="rows rendered per sheet, from row 1 (default 200: "
                   "rows 1 to 200)")
    p.add_argument("--max-cols", type=int, default=40, help="columns rendered per sheet, from column A (default 40: "
                   "columns A to AN)")
    p.add_argument("--json", metavar="FILE", help="write the report as JSON ('-' = stdout)")
    p.add_argument("--quiet", action="store_true", help="print only the last line")


def default_render_dir(workbook: Path) -> Path:
    """<model>/reports/render/ for the model's workbook, <model>/reports/render/<stem>/ for another workbook in
    a model folder, else <folder>/<stem>_render/."""
    root = _model_root(workbook)
    if root is None:
        return workbook.parent / f"{workbook.stem}_render"
    main = "model.xlsx"
    try:
        from . import modelfolder
        main = str(modelfolder.read_marker(root).get("workbook") or main)
    except (NotImplementedError, common.RotmError, OSError, ValueError):
        pass
    base = root / "reports" / "render"
    return base if workbook.resolve() == (root / main).resolve() else base / workbook.stem


def run_render(args: argparse.Namespace) -> int:
    src = _check_workbook(args.workbook)
    report = common.Report("render", src.as_posix())
    quiet = getattr(args, "quiet", False)
    status = excel_status()
    if not status.available:
        report.note(f"not rendered: the sheets were not looked at; Excel is not available ({status.reason})")
        report.finish(not_available=True)
        return report.emit(args.json, quiet=quiet)
    out = Path(args.out) if args.out else default_render_dir(src)
    sheets = [s.strip() for s in args.sheets.split(",") if s.strip()] if args.sheets else None
    if args.max_rows < 1 or args.max_cols < 1:
        raise common.Refusal("--max-rows and --max-cols must be 1 or more")
    try:
        items = render_sheets(src, out, sheets=sheets, max_rows=args.max_rows, max_cols=args.max_cols)
    except common.EnvUnavailable as exc:
        report.note(f"not rendered: the sheets were not looked at; {exc.message}")
        report.finish(not_available=True)
        return report.emit(args.json, quiet=quiet)
    files = []
    for i in items:
        if i.problem or i.path is None:
            report.error("X-RENDER", f"sheet {i.sheet!r} was not rendered: {i.problem}",
                         "run render again; if it fails again, open the sheet in Excel and look at it there",
                         sheet=i.sheet)
            continue
        files.append(i.path)
        cut = f"; the sheet's used range ends at row {i.last_row}, column {i.last_col}: rows or columns beyond " \
              f"the rendered range are not shown" if i.truncated else ""
        report.note(f"render {i.sheet}: {i.path.as_posix()} ({i.width_px} x {i.height_px} px, cells {i.range}){cut}")
    report.verified["rendered"] = bool(files)
    report.note("rendered, not looked at by code: open each PNG file and look at it")
    report.data["files"] = [p.as_posix() for p in files]
    report.data["out_dir"] = Path(os.path.abspath(out)).as_posix()
    code = report.finish(False)
    root = _model_root(src)
    if root is not None:
        rel_files = []
        for p in files:
            try:
                rel_files.append(p.resolve().relative_to(root.resolve()).as_posix())
            except ValueError:
                rel_files.append(p.as_posix())
        _r, note = _record(src, "render", {"at": report.at, "exit_code": code, "sha256": common.file_sha256(src),
                                           "files": rel_files})
        if note:
            report.note(note)
    return report.emit(args.json, quiet=quiet)

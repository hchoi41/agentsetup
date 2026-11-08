"""Tool-setup guards (criteria v3 §4). Each lesson from the 2026-11-07 run is a check here.

- Codex sandbox: the runner passes an explicit sandbox; --ignore-user-config never leaves it implicit.
- Elevated run -> next step in a new process: every seat call is its own subprocess (seats.py); nothing is reused.
- gcloud / gws-company login expiry: checked before a job starts.
- 32K command-line limit: prompts go on stdin; argv is length-checked.
- claude.exe shadowed by a shell function/alias: the resolved command must be an application file.
- Two runners writing one file: an exclusive run lock per output path.
- A watcher whose stop condition is already true: refused at start.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ARGV_LIMIT = 8000          # far below the Windows 32,767-char CreateProcess limit
CODEX_REVIEW_SANDBOX = "read-only"


class PreflightError(RuntimeError):
    pass


def check_argv(argv: list):
    n = len(subprocess.list2cmdline(argv))
    if n > ARGV_LIMIT:
        raise PreflightError(f"command line is {n} chars (> {ARGV_LIMIT}); pass large inputs on stdin or in a file")


def codex_sandbox_args(role: str, sandbox: str | None, platform: str = sys.platform) -> list:
    """Explicit sandbox for a Codex seat. Review/QA seats are read-only. On Windows the elevated helper
    fails under workspace-write (2026-10-17), so that combination is refused."""
    sb = sandbox or CODEX_REVIEW_SANDBOX
    if role in ("accuracy_reviewer", "cold_reviewer", "accuracy_reviewer_2", "cold_reviewer_2", "judge", "qa_qc", "auditor") and sb != "read-only":
        raise PreflightError(f"{role}: review seats must run read-only, not {sb!r}")
    if platform.startswith("win") and sb == "workspace-write":
        raise PreflightError("workspace-write with the Windows elevated sandbox helper is known to fail; use read-only")
    args = ["-s", sb]
    if platform.startswith("win"):
        args += ["-c", 'windows.sandbox="elevated"']
    return args


def resolve_application(name: str) -> str:
    """Resolve a CLI to an application file; refuse shell functions/aliases that shadow it."""
    p = shutil.which(name)
    if not p or not Path(p).is_file():
        raise PreflightError(f"{name}: not found as an application on PATH (a shell function or alias may be shadowing it)")
    if sys.platform.startswith("win"):
        try:
            r = subprocess.run(["pwsh", "-NoProfile", "-Command", f"(Get-Command {name} -ErrorAction Stop).CommandType"],
                               capture_output=True, text=True, timeout=30)
            kind = r.stdout.strip()
            if r.returncode == 0 and kind and kind not in ("Application", "ExternalScript"):
                raise PreflightError(f"{name}: resolves to a PowerShell {kind}, not the application")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    return p


def check_login(name: str, argv: list, timeout: int = 60) -> dict:
    """Run a cheap authenticated command; non-zero exit = expired/missing login."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return {"name": name, "ok": False, "detail": f"{argv[0]} not installed"}
    except subprocess.TimeoutExpired:
        return {"name": name, "ok": False, "detail": "timed out"}
    return {"name": name, "ok": r.returncode == 0,
            "detail": "ok" if r.returncode == 0 else (r.stderr or r.stdout).strip()[:300]}


DEFAULT_LOGINS = {
    "gcloud": ["gcloud", "auth", "print-access-token", "--quiet"],
    "gws-company": ["gcloud", "auth", "print-access-token", "--quiet", "--configuration=gws-company"],
}


def run_logins(required: list, commands: dict | None = None) -> list:
    cmds = {**DEFAULT_LOGINS, **(commands or {})}
    results = [check_login(n, cmds[n]) for n in required]
    bad = [r for r in results if not r["ok"]]
    if bad:
        raise PreflightError("login expired or missing: " + "; ".join(f"{r['name']}: {r['detail']}" for r in bad))
    return results


class RunLock:
    """Exclusive lock so two runners never write one file."""

    def __init__(self, target: Path):
        self.path = Path(str(target) + ".lock")
        self.fd = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise PreflightError(f"another runner holds {self.path}") from None
        os.write(self.fd, str(os.getpid()).encode())
        return self

    def __exit__(self, *exc):
        os.close(self.fd)
        self.path.unlink(missing_ok=True)


def start_watcher(stop_condition, label: str = "watcher"):
    """Refuse a watcher whose stop condition is already true before it starts."""
    if stop_condition():
        raise PreflightError(f"{label}: stop condition is already true at start; the watcher would exit immediately")
    return True

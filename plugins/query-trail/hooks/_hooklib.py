"""Shared helpers for the query-trail hooks (design spec v1 section 1.5; T14 Windows findings).

Stdlib only, so any Python 3.10+ runs them (the hooks never import duckdb or the BigQuery client).
Hook stdout on Windows is cp1252 (T14 F11), so hooks.json sets PYTHONUTF8=1 and -X utf8, and every
message here is ASCII anyway. PreToolUse exit 2 blocks the call and shows stderr to the model (T14 F4);
PostToolUse and Stop print a JSON decision on stdout and exit 0.

The active run: scripts/lib/state.py `activate --run-dir R` writes <QT_HOME>/active_run.json
(QT_HOME defaults to %USERPROFILE%/.query-trail, the folder that already holds the env files, D-12).
With no active run the hooks protect only files inside folders that hold a run_state.json.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PHASES = tuple(f"P{i}" for i in range(12))
STATUSES = ("pending", "in_progress", "waiting_gate", "done", "failed")


def read_payload() -> dict[str, Any]:
    """The hook input JSON from stdin (UTF-8 bytes, T14 _common.read_input)."""
    raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def deny(message: str) -> None:
    """Block a PreToolUse call: reason on stderr, exit 2."""
    sys.stderr.write("query-trail hook: BLOCKED: " + message + "\n")
    sys.stderr.flush()
    sys.exit(2)


def allow() -> None:
    """Let the call proceed."""
    sys.exit(0)


def qt_home() -> Path:
    """<QT_HOME> or %USERPROFILE%/.query-trail."""
    if os.environ.get("QT_HOME"):
        return Path(os.environ["QT_HOME"])
    return Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(Path.home())) / ".query-trail"


def active_run() -> Path | None:
    """The active run folder, or None (pointer missing, unreadable, or its folder has no run_state.json)."""
    p = qt_home() / "active_run.json"
    try:
        rd = Path(json.loads(p.read_text(encoding="utf-8-sig"))["run_dir"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return rd if (rd / "run_state.json").is_file() else None


def run_state(run_dir: Path) -> dict[str, Any] | None:
    """run_state.json when present and well formed (the same fields lib.state validates), else None."""
    try:
        st = json.loads((Path(run_dir) / "run_state.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(st, dict) or not st.get("run_id") or st.get("phase") not in PHASES \
            or st.get("phase_status") not in STATUSES:
        return None
    return st


def load_allowlist() -> dict[str, Any]:
    """config/allowlist.json of this plugin."""
    return json.loads((PLUGIN_ROOT / "config" / "allowlist.json").read_text(encoding="utf-8"))


def resolve(path: str, cwd: str | None) -> Path:
    """Absolute, normalised path of a tool path argument (relative paths against the payload cwd)."""
    p = Path(path)
    if not p.is_absolute():
        p = Path(cwd or os.getcwd()) / p
    return Path(os.path.normcase(os.path.abspath(str(p))))


def norm(p: Path) -> Path:
    """Normalised absolute path for comparisons (case-insensitive on Windows)."""
    return Path(os.path.normcase(os.path.abspath(str(p))))


def inside(child: Path, parent: Path) -> bool:
    """True when `child` is `parent` or below it."""
    c, p = norm(child), norm(parent)
    return c == p or p in c.parents


def run_root_of(path: Path) -> Path | None:
    """The nearest ancestor folder holding a run_state.json."""
    for d in [path.parent, *path.parent.parents]:
        if (d / "run_state.json").is_file():
            return d
    return None


def read_json(path: Path) -> Any:
    """JSON or None."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None

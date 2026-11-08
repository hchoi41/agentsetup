"""Exit-2 wording: what a refusal did and did not do (design spec v4 1.4; fix r3 B3-4, aera r3 M2).

Exit 2 means "refused". It does not by itself prove that nothing happened, so every package-B script runs its body
through `guard`: on a RefusedError the message gains

  "; refused: no warehouse call was made; local effects: none"
  "; refused: warehouse jobs on the budget ledger during this call: N; local effects: <files written or changed>"

The warehouse part counts the budget-ledger entries added during the call (every billed or authorised job is one;
dry runs and metadata reads are free and are not counted, scripts that make them say so in their docstring). The
local part compares the run folder before and after (paths whose size or modification time changed, new paths,
removed paths). A script that never talks to the warehouse passes warehouse=False and always says "no warehouse
call was made".
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from lib.exit_codes import RefusedError

MAX_LISTED = 12


def _snapshot(run_dir: Path | None) -> dict[str, tuple[int, int]]:
    if run_dir is None or not Path(run_dir).is_dir():
        return {}
    out: dict[str, tuple[int, int]] = {}
    for p in Path(run_dir).rglob("*"):
        try:
            if p.is_file():
                st = p.stat()
                out[p.relative_to(run_dir).as_posix()] = (st.st_size, st.st_mtime_ns)
        except OSError:
            continue
    return out


def _ledger_len(run_dir: Path | None) -> int:
    if run_dir is None:
        return 0
    try:
        from lib import manifest

        m = manifest.load(Path(run_dir)) or {}
    except Exception:  # noqa: BLE001 - an unreadable manifest counts as no ledger
        return 0
    return len((m.get("budget") or {}).get("ledger") or [])


def effects(before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]]) -> list[str]:
    """Run-relative paths written, changed or removed between two snapshots."""
    new = sorted(set(after) - set(before))
    changed = sorted(k for k in set(after) & set(before) if after[k] != before[k])
    gone = sorted(set(before) - set(after))
    return [f"wrote {p}" for p in new] + [f"changed {p}" for p in changed] + [f"removed {p}" for p in gone]


def statement(jobs: int, local: list[str], warehouse: bool) -> str:
    calls = (f"warehouse jobs on the budget ledger during this call: {jobs}" if warehouse and jobs
             else "no warehouse call was made" if not warehouse
             else "no warehouse job was run (dry runs and metadata reads, which bill nothing, may have been made)")
    shown = ", ".join(local[:MAX_LISTED]) + (f" and {len(local) - MAX_LISTED} more" if len(local) > MAX_LISTED else "")
    return f"refused: {calls}; local effects: {shown or 'none'}"


def guard(run_dir: Any, body: Callable[[], int], warehouse: bool = False) -> int:
    """Run `body`; on a RefusedError re-raise it with the warehouse calls and local effects appended (B3-4)."""
    rd = Path(run_dir) if run_dir is not None else None
    before, jobs0 = _snapshot(rd), _ledger_len(rd)
    try:
        return body()
    except RefusedError as exc:
        text = statement(_ledger_len(rd) - jobs0, effects(before, _snapshot(rd)), warehouse)
        try:
            new = type(exc)(f"{exc}; {text}")
        except TypeError:
            new = RefusedError(f"{exc}; {text}")
        raise new from exc

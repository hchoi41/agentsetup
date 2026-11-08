"""The G2 lock bound to Max's G2 decision (design spec v5 2.2 P2; fix r4 C4-4, aera r4 H4).

`decide.py --gate G2 --outcome approve --tables a,b` records input_hash = table_set_sha256(a, b): the sha256 of the
sorted table list in placeholder form (`{{project}}.{{<alias>}}.<table>`), joined by LF. `discover.py lock --tables`
then requires that the run's current G2 decision (its last G2 row) is an approve with exactly that hash, and every
downstream P2/P3 step that reads the lock (the second profiling pass, warehouse_jobs.py discovery profiles,
trap_scan.py, discover.py build after the lock, discover.py decide, pin_plan.py) calls `require_bound(run_dir, who)`:
no lock, no G2 approve row, or a lock whose table set differs from the G2 decision is refused (exit 2). A changed
table set needs a new G2 answer (`decide.py --gate G2 --supersede --tables ...`) and a new lock.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from lib import runctx as c2
from lib.exit_codes import RefusedError

LOCK = "14_discovery_lock.json"
APPROVE = frozenset({"approve", "approved", "accept", "accepted"})
DIFFERS = "table set differs from the G2 decision: ask G2 again with --supersede"


def placeholder_ref(ref: str) -> str:
    """`ds.t`, `{{ds}}.t`, `{{project}}.{{ds}}.t` or a backticked form -> `{{project}}.{{ds}}.t`."""
    parts = [p.strip("`{} ") for p in str(ref).replace("`", "").split(".") if p.strip("`{} ")]
    if len(parts) == 3 and parts[0] == "project":
        parts = parts[1:]
    if len(parts) != 2:
        raise RefusedError(f"table {ref!r} is not <dataset alias>.<table> (placeholder form "
                           "{{project}}.{{<alias>}}.<table>)")
    return "{{project}}.{{" + parts[0].lower() + "}}." + parts[1].lower()


def table_set_sha256(refs: Iterable[str]) -> str:
    """sha256 of the sorted, de-duplicated table list in placeholder form, LF-joined (UTF-8)."""
    return c2.sha256_text("\n".join(sorted({placeholder_ref(r) for r in refs})))


def split_tables(text: str) -> list[str]:
    return [x.strip() for x in str(text or "").split(",") if x.strip()]


def g2_decision(run_dir: Path) -> tuple[int, dict[str, Any]] | None:
    """(1-based row number, row) of the run's current G2 decision: its last G2 row (a --supersede row is later)."""
    rows = c2.load_decisions(Path(run_dir))
    hits = [(i, r) for i, r in enumerate(rows, 1) if str(r.get("gate")) == "G2"]
    return hits[-1] if hits else None


def approved_hash(run_dir: Path) -> tuple[int, str]:
    """(row, input_hash) of the current G2 approve; RefusedError when there is none or it carries no table set."""
    d = g2_decision(run_dir)
    if d is None:
        raise RefusedError("no G2 decision in decisions.csv: record Max's G2 answer with decide.py --gate G2 "
                           "--outcome approve --tables <the tables he kept> before the lock")
    n, row = d
    if str(row.get("outcome", "")).strip().lower() not in APPROVE:
        raise RefusedError(f"the current G2 decision (decisions.csv row {n}) is {row.get('outcome')!r}, not an "
                           "approve: nothing is locked")
    h = str(row.get("input_hash") or "").strip()
    if not h:
        raise RefusedError(f"the G2 approve (decisions.csv row {n}) names no table set: ask G2 again with "
                           "--supersede --tables <the tables Max kept>")
    return n, h


def check_tables(run_dir: Path, tables: Iterable[str]) -> tuple[int, str]:
    """Refuse a lock whose table set is not the one the current G2 approve names; return (row, hash)."""
    n, h = approved_hash(run_dir)
    if table_set_sha256(tables) != h:
        raise RefusedError(f"{DIFFERS} (decisions.csv row {n})")
    return n, h


def load(run_dir: Path) -> dict[str, Any] | None:
    p = Path(run_dir) / LOCK
    return c2.read_input_json(p, LOCK) if p.is_file() else None


def require_bound(run_dir: Path, who: str) -> Mapping[str, Any]:
    """The lock, after checking it is bound to the current G2 approve; RefusedError (exit 2) otherwise."""
    lock = load(run_dir)
    if lock is None:
        raise RefusedError(f"{who}: no G2 lock ({LOCK}): after Max's G2 answer run decide.py --gate G2 --outcome "
                           "approve --tables <kept tables>, then discover.py --run-dir RUN lock --tables <the same tables>; nothing "
                           "is profiled, scanned, decided or pinned before the lock")
    try:
        check_tables(run_dir, list(lock.get("tables") or {}))
    except RefusedError as exc:
        raise RefusedError(f"{who}: {exc}") from exc
    return lock

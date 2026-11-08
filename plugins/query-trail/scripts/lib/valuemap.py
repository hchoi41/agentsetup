"""11_value_map.csv: the G3 value map, its row shape and its checks (design spec v4 2.2 P2; fix r3 B3-9, B3-10, B3-11).

One row per decision Max confirms at G3. Columns (all text, written through the lib CSV writer, never by hand):

  row_id       vm:<kind>:<table_ref or run>:<column or *>[:<8 hex of the value> for filter, map, derive]
  kind         timezone | period | filter | derive | map | not_used (fix r4 C4-9: the column is not used by the query)
  scope        column (the row names its table and column) | run (a run-wide row: table and column empty)
  table_ref    <dataset alias>.<table>, a locked table (14_discovery_lock.json) once G2 is locked
  column_name  a column of that table (05_catalog.csv)
  value        timezone: an IANA name or UTC (e.g. Asia/Seoul); period: YYYY-MM-DD..YYYY-MM-DD, optionally followed
               by "; <rule>" (e.g. "2025-01-01..2025-09-30; rows after the period end excluded"); filter: the exact
               condition or value list (e.g. status IN ('Complete','Shipped')); derive: the expression; map: raw=label;
               not_used: optional (defaults to "not used"). Date boundaries (fix r4 LOW): YYYY-MM-DD..YYYY-MM-DD names
               whole days, the start day and the end day both included.
  reason       why, in words (Max's or the session's proposal he confirmed)
  added_utc    when the row was added

What clears what (fix r3): a timezone trap on table T column C clears only with a timezone row naming T and C, or a
run-wide timezone row (scope run) whose row_id has its own G3 decision row (`decide.py --gate G3 --check-id <row_id>`)
with an approving outcome. A freshness disagreement on a locked table is settled only by a period row naming that
table and its date column (else G13 opens).

Fix r4: a `not_used` row (C4-9; scope column, a table and a column, a reason) says the query does not use that
column: discover.py build counts it as settling a freshness disagreement on that date column, trap_scan.py clears
that column's traps with it, and nothing that builds filters reads it (13_table_decision.md lists it under
Freshness, never under the filters; C4-12). A period row that settles freshness is likewise listed under Freshness,
"settled; not a filter", unless it names the bindings' date column. `touched_columns` (C4-8) is the set of
(table, column) the value map's filter, derive, map and period rows name.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from lib import runctx as c2

VALUE_MAP = "11_value_map.csv"
KINDS = ("timezone", "period", "filter", "derive", "map", "not_used")
FILTER_KINDS = ("filter", "derive", "map")  # the kinds a query applies as written (never not_used)
NOT_USED_DEFAULT = "not used"
SCOPES = ("column", "run")
COLUMNS = (("row_id", "VARCHAR"), ("kind", "VARCHAR"), ("scope", "VARCHAR"), ("table_ref", "VARCHAR"),
           ("column_name", "VARCHAR"), ("value", "VARCHAR"), ("reason", "VARCHAR"), ("added_utc", "VARCHAR"))
NAMES = tuple(n for n, _ in COLUMNS)
TZ = re.compile(r"^(UTC|GMT|[A-Za-z_]+(/[A-Za-z0-9_+\-]+)+|UTC[+-]\d{2}:\d{2})$")
PERIOD = re.compile(r"^\d{4}-\d{2}-\d{2}\.\.\d{4}-\d{2}-\d{2}(\s*;.*)?$")
APPROVE = frozenset({"approve", "approved", "accept", "accepted"})


def row_id(kind: str, table_ref: str | None, column: str | None, value: str) -> str:
    base = f"vm:{kind}:{table_ref or 'run'}:{column or '*'}"
    if kind in ("filter", "map", "derive"):
        base += ":" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return base


def read(run_dir: Path) -> list[dict[str, Any]]:
    """Rows of 11_value_map.csv ([] when absent); a row written before fix r3 lacks scope, table and reason."""
    return [dict(r) for r in c2.read_records_if_exists(Path(run_dir) / VALUE_MAP)]


def write(run_dir: Path, rows: Sequence[Mapping[str, Any]]) -> Path:
    p = Path(run_dir) / VALUE_MAP
    c2.write_records(p, COLUMNS, [tuple(r.get(n) for n in NAMES) for r in rows])
    return p


def _bare(ref: Any) -> str:
    parts = [p.strip("`{} ") for p in str(ref or "").replace("`", "").split(".")]
    if len(parts) == 3 and parts[0] == "project":
        parts = parts[1:]
    return ".".join(parts).lower()


def problems(rows: Sequence[Mapping[str, Any]], run_dir: Path | None = None) -> list[str]:
    """Every shape problem of the value map, one line each (empty: valid)."""
    out: list[str] = []
    locked = None
    cols: dict[str, set[str]] = {}
    if run_dir is not None:
        lp = Path(run_dir) / "14_discovery_lock.json"
        if lp.is_file():
            locked = {_bare(r) for r in c2.read_input_json(lp, lp.name).get("tables", {})}
        for c in c2.read_records_if_exists(Path(run_dir) / "05_catalog.csv"):
            if c.get("table_ref") and c.get("column_name"):
                cols.setdefault(_bare(c["table_ref"]), set()).add(str(c["column_name"]).lower())
    seen: set[str] = set()
    for i, r in enumerate(rows, 1):
        tag = f"row {i} ({r.get('row_id') or r.get('kind')})"
        kind, scope = str(r.get("kind") or "").lower(), str(r.get("scope") or "").lower()
        if kind not in KINDS:
            out.append(f"{tag}: kind {r.get('kind')!r} is not one of {', '.join(KINDS)}")
        if scope not in SCOPES:
            out.append(f"{tag}: scope {r.get('scope')!r} is not column or run")
        t, c, v = r.get("table_ref"), r.get("column_name"), str(r.get("value") or "").strip()
        if kind == "not_used" and scope != "column":
            out.append(f"{tag}: a not_used row names its table_ref and column_name (scope column)")
        if scope == "column":
            if not t or not c:
                out.append(f"{tag}: a column-scope row names its table_ref and column_name")
            else:
                if locked is not None and _bare(t) not in locked:
                    out.append(f"{tag}: table {t} is outside the G2 lock")
                if cols and str(c).lower() not in cols.get(_bare(t), set()):
                    out.append(f"{tag}: column {c} is not a column of {t} in 05_catalog.csv")
        elif scope == "run" and (t or c):
            out.append(f"{tag}: a run-scope row leaves table_ref and column_name empty")
        if not v:
            out.append(f"{tag}: value is empty")
        elif kind == "timezone" and not TZ.match(v):
            out.append(f"{tag}: timezone {v!r} is not an IANA name (Area/City) or UTC")
        elif kind == "period" and not PERIOD.match(v):
            out.append(f"{tag}: period {v!r} is not YYYY-MM-DD..YYYY-MM-DD[; rule]")
        if not str(r.get("reason") or "").strip():
            out.append(f"{tag}: reason is empty")
        rid = str(r.get("row_id") or "")
        if rid in seen:
            out.append(f"{tag}: duplicate row_id {rid}")
        seen.add(rid)
    return out


def approved_g3(run_dir: Path) -> set[str]:
    """check_ids of G3 decision rows with an approving outcome (run-wide value-map rows need one each)."""
    return {str(d.get("check_id")) for d in c2.load_decisions(run_dir)
            if str(d.get("gate")) == "G3" and str(d.get("outcome", "")).strip().lower() in APPROVE and d.get("check_id")}


def timezone_for(rows: Sequence[Mapping[str, Any]], table_ref: str, column: str,
                 approved: set[str]) -> str | None:
    """The row_id that settles the timezone of (table, column), else None (fix r3 B3-10)."""
    for r in rows:
        if str(r.get("kind") or "").lower() != "timezone" or not str(r.get("value") or "").strip():
            continue
        scope = str(r.get("scope") or "").lower()
        if scope == "column" and _bare(r.get("table_ref")) == _bare(table_ref) and \
                str(r.get("column_name") or "").lower() == column.lower():
            return str(r.get("row_id") or "timezone row")
        if scope == "run" and str(r.get("reason") or "").strip() and str(r.get("row_id")) in approved:
            return str(r["row_id"])
    return None


def not_used_for(rows: Sequence[Mapping[str, Any]], table_ref: str, column: str | None) -> str | None:
    """Fix r4 C4-9: the row_id of a not_used row naming (table, column), else None."""
    for r in rows:
        if str(r.get("kind") or "").lower() == "not_used" and column and \
                _bare(r.get("table_ref")) == _bare(table_ref) and str(r.get("column_name") or "").lower() == column.lower():
            return str(r.get("row_id") or "not_used row")
    return None


def touched_columns(rows: Sequence[Mapping[str, Any]]) -> set[tuple[str, str]]:
    """Fix r4 C4-8: (bare table, lower column) of every filter, derive or map row.

    Fix r5 (cold reviewer LOW): period rows are left out. A period row on another column only settled freshness (it is
    not applied), so it must not make that column's timezone trap blocking; a period row on the bindings' date column
    is already touched through the bindings (trap_scan.bound_columns)."""
    return {(_bare(r.get("table_ref")), str(r.get("column_name")).lower()) for r in rows
            if str(r.get("kind") or "").lower() in FILTER_KINDS and r.get("table_ref") and r.get("column_name")}


def period_for(rows: Sequence[Mapping[str, Any]], table_ref: str, column: str | None) -> str | None:
    """The row_id of a period row naming (table, column), else None (fix r3 B3-11)."""
    for r in rows:
        if str(r.get("kind") or "").lower() == "period" and str(r.get("scope") or "").lower() == "column" and \
                _bare(r.get("table_ref")) == _bare(table_ref) and column and \
                str(r.get("column_name") or "").lower() == column.lower():
            return str(r.get("row_id") or "period row")
    return None

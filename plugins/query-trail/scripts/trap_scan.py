"""trap_scan.py: the T05 trap scan over the shortlist and the card (design spec v1 sections 1.4, 2.2 P2; DB-06).

Runs after profiling and before G3 and G1b (DB-06 order: shortlist G2, profiling, trap scan,
value map G3, bindings G1b). Trap families, each detected from the discovery files (D0) and
resolved only by the card (Max):

  scd2_validity     SCD2 markers (valid_from/valid_to, effective/expiry, is_current, dbt_valid_*)
                    with no as-of rule in bindings.scd2_flags                       blocking
  version_filter    a version or scenario column (T05 T6) with no bound filter      blocking
  semi_additive     balance, stock, headcount, outstanding or snapshot columns with
                    no roll-up (closing / opening / average) in bindings.semi_additive_columns;
                    SUM across time is rejected (v4 P1). Fix r1 B-M7 (spec v2 2.2 P2): the name
                    token alone is not enough; the column also needs a type signal (a
                    non-integer measure type, or a snapshot DATE marker) or an aggregation
                    signal (the card names an integer column); identifier-like names
                    (..._id, _key, _code, _no) never qualify                        blocking
  fan_out_key       a profiled key that is not unique (grain test FAIL): a join on it fans
                    out and inflates sums (T05 T5b)                                 blocking
  timezone          a TIMESTAMP or DATETIME column with no timezone decided for that very column: a
                    value-map row naming its table and column, a run-wide value-map row (scope run)
                    with its own approving G3 decision row, or the card's bindings.date naming that
                    table and column with a timezone (fix r3 B3-10: one row never clears them all)
                                                                                    blocking
  partial_period    the profiled data does not cover the card period (max date before the
                    period end, or missing days)                                    blocking
  late_arrival      an ingestion or load timestamp beside the event date, or a GA4 daily
                    table (events_YYYYMMDD): a late-arrival band for G13; pin PROVISIONAL
                    while it is touched (spec 5.6)                                  not blocking

A trap is resolved by `trap_resolutions: {<trap_id>: {action: filter, filter: ...}}` or
`{action: not_applicable, reason: ...}` in the metric card, or by the matching binding.
Inputs: 05_catalog.csv, 06_profiles.csv, 08_shortlist.csv, 02_metric_card.md, optional
discovery_terms.json (period) and 11_value_map.csv. Output: 09_traps.csv.
After the G2 lock (14_discovery_lock.json) only the locked tables are scanned, and a card binding that names a
table outside the lock is refused (fix r3 B3-8).

Fix r4:
- C4-4: refused (exit 2) before the G2 lock, and while the lock's table set differs from the G2 decision (lib.g2lock).
- C4-8: once the card has bindings (measures, date or joins), the column traps timezone and semi_additive block only
  on columns the bindings touch (the date column, measure expressions and their filters, join keys) or that a
  filter, derive, map or period row of the value map names; every other such column is listed with resolution
  `not_bound`, not blocking, and needs no decision. Without bindings every column counts as touched (nothing says
  which are used): write the bindings draft before the scan. version_filter stays blocking on every column (its
  risk is exactly a column nobody filtered).
- C4-9: a value-map `not_used` row clears every trap on its column (resolution `not_used`), and a partial_period
  trap of a table whose date column is not_used.
- LOW: a G3 decision row whose --check-id is the trap_id (outcome approve, accept, filter, not_applicable or
  answered, with a reason) resolves that trap (resolution `g3_decision`); card trap_resolutions still work.

CLI:  trap_scan.py --run-dir R
Exit: 0 no unresolved blocking trap; 1 a blocking trap is unresolved (blocks G1b); 2 refused (no warehouse call is
      made by this script; local effects: none, 09_traps.csv is not written).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import g2lock  # noqa: E402  fix r4 C4-4
from lib import runctx as c2, valuemap  # noqa: E402
from lib import refusal  # noqa: E402  fix r3 B3-4: exit-2 local effects on stderr
from lib.exit_codes import CheckFailedError, ExitCode, RefusedError  # noqa: E402

TRAPS = "09_traps.csv"
TRAP_COLUMNS = (("trap_id", "VARCHAR"), ("family", "VARCHAR"), ("table_ref", "VARCHAR"), ("column_name", "VARCHAR"),
                ("evidence", "VARCHAR"), ("blocking", "BOOLEAN"), ("resolution", "VARCHAR"),
                ("resolution_detail", "VARCHAR"), ("source", "VARCHAR"))
SCD2 = re.compile(r"^(valid_from|valid_to|effective_(from|to|date|start|end)|eff_(dt|date|from|to)|exp(iry|iration)?_(dt|date)"
                  r"|end_date|start_date|is_current|current_flag|is_active_version|dbt_valid_(from|to)|row_(start|end)_date)$", re.I)
VERSION = re.compile(r"^(version|ver|scenario|budget_version|forecast_version|plan_version|snapshot_version)$", re.I)
SEMI_ADD = re.compile(r"(balance|_bal$|^bal_|stock|inventory|on_hand|headcount|outstanding|closing|opening|position|"
                      r"snapshot_date|as_of_date)", re.I)
LOAD_TS = re.compile(r"^(_?load(ed)?_(ts|at|time|timestamp|date)|_?ingest(ed|ion)?_(ts|at|time)|inserted_at|"
                     r"_partitiontime|etl_(ts|loaded_at)|updated_at)$", re.I)
GA4 = re.compile(r"(^|\.)events_(\d{8}|\*|intraday_\d{8})$")
NUMERIC = ("INT", "NUMERIC", "BIGNUMERIC", "FLOAT", "DECIMAL", "DOUBLE")
NON_INTEGER_MEASURE = ("NUMERIC", "BIGNUMERIC", "FLOAT", "DECIMAL", "DOUBLE")
ID_LIKE = re.compile(r"(^|_)(id|key|code|sk|nk|no|num|number|ref|type)$", re.I)


def semi_additive_signal(name: str, typ: str, card_text: str) -> str | None:
    """Why a semi-additive name candidate is a trap (type or aggregation signal), or None (B-M7)."""
    if not SEMI_ADD.search(name) or ID_LIKE.search(name):
        return None
    if "date" in name.lower():
        return "snapshot date marker"
    if typ.startswith(NON_INTEGER_MEASURE):
        return f"non-integer measure type {typ}"
    if typ.startswith("INT") and re.search(rf"(?<![a-z0-9_]){re.escape(name.lower())}(?![a-z0-9_])", card_text):
        return "integer column the card aggregates"
    return None


def _tid(family: str, table: str, column: str | None) -> str:
    return f"{family}:{table}:{column or '-'}"


def _bound(items: Any, table: str, column: str | None = None) -> bool:
    for it in items or []:
        if isinstance(it, str) and it in (table, f"{table}.{column}"):
            return True
        if isinstance(it, Mapping) and it.get("table") == table and (column is None or it.get("column") in (None, column)):
            if any(it.get(k) for k in ("as_of_rule", "rollup", "filter")):
                return True
    return False


def _date_binding(card: Mapping[str, Any], table: str, column: str) -> bool:
    """The card's bindings.date names this table and column with a timezone (a per-column decision)."""
    d = (card.get("bindings") or {}).get("date")
    return isinstance(d, Mapping) and bool(str(d.get("timezone") or "").strip()) and \
        valuemap._bare(d.get("table")) == valuemap._bare(table) and str(d.get("column") or "").lower() == column.lower()


COLUMN_TRAPS_BY_USE = ("timezone", "semi_additive")  # fix r4 C4-8: blocking only on columns the query touches
G3_RESOLVING = frozenset({"approve", "approved", "accept", "accepted", "filter", "not_applicable", "answered"})
_WORD = re.compile(r"(?:\b([A-Za-z_][A-Za-z0-9_]*)\.)?\b([A-Za-z_][A-Za-z0-9_]*)\b")


def has_bindings(card: Mapping[str, Any]) -> bool:
    b = card.get("bindings") or {}
    return isinstance(b, Mapping) and any(b.get(k) for k in ("measures", "date", "joins"))


def join_pairs(j: Mapping[str, Any]) -> list[Any]:
    """The key pairs of one bindings join: `on_keys` (fix r4 LOW), else `on` (older cards; YAML 1.1 may read an
    unquoted `on` key as True)."""
    return list(j.get("on_keys") or j.get("on") or j.get(True) or [])


def bound_columns(card: Mapping[str, Any], cols: Mapping[str, Sequence[Mapping[str, Any]]]) -> set[tuple[str, str]]:
    """Fix r4 C4-8: (bare table, lower column) the card's bindings touch: bindings.date, the columns named in each
    measure's expr and filter (unqualified: the measure's table; qualified t.c: the bindings table named t), and every
    join key."""
    b = card.get("bindings") or {}
    out: set[tuple[str, str]] = set()
    names = {valuemap._bare(t): {str(c["column_name"]).lower() for c in cs} for t, cs in cols.items()}
    shorts = {valuemap._bare(t).split(".")[-1]: valuemap._bare(t) for t in (b.get("tables") or []) if isinstance(t, str)}
    d = b.get("date")
    if isinstance(d, Mapping) and d.get("table") and d.get("column"):
        out.add((valuemap._bare(d["table"]), str(d["column"]).lower()))
    measures = b.get("measures") if isinstance(b.get("measures"), Mapping) else {}
    for m in measures.values():
        if not isinstance(m, Mapping) or not m.get("table"):
            continue
        home = valuemap._bare(m["table"])
        text = f"{m.get('expr') or ''} {m.get('filter') or ''}"
        for q, w in _WORD.findall(text):
            t = shorts.get(q.lower(), home) if q else home
            if w.lower() in names.get(t, set()):
                out.add((t, w.lower()))
    for j in b.get("joins") or []:
        if not isinstance(j, Mapping):
            continue
        for pair in join_pairs(j):
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                out.add((valuemap._bare(j.get("left")), str(pair[0]).lower()))
                out.add((valuemap._bare(j.get("right")), str(pair[1]).lower()))
    return out


def scan(catalog: Sequence[Mapping[str, Any]], profiles: Sequence[Mapping[str, Any]], shortlist: Sequence[Mapping[str, Any]],
         card: Mapping[str, Any], terms: Mapping[str, Any], value_map: Sequence[Mapping[str, Any]],
         approved_g3: set[str] | None = None, g3_rows: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Return trap rows for the shortlisted tables (unresolved unless the card resolves them)."""
    refs = [str(r["table_ref"]) for r in shortlist if r.get("table_ref") and not ("rank" in r and r["rank"] is None)]
    cols: dict[str, list[Mapping[str, Any]]] = {r: [] for r in refs}
    for c in catalog:
        if c.get("table_ref") in cols and c.get("column_name"):
            cols[str(c["table_ref"])].append(c)
    b = card.get("bindings") or {}
    found: list[dict[str, Any]] = []
    touched = (bound_columns(card, cols) | valuemap.touched_columns(value_map)) if has_bindings(card) else None
    date_cols = {valuemap._bare(r.get("table_ref")): str(r.get("date_column") or "").lower() for r in shortlist
                 if r.get("table_ref")}

    def add(family: str, table: str, column: str | None, evidence: str, blocking: bool, source: str,
            bound: bool = False, bound_by: str | None = "card binding") -> None:
        found.append({"trap_id": _tid(family, table, column), "family": family, "table_ref": table,
                      "column_name": column, "evidence": evidence, "blocking": blocking, "bound": bound,
                      "bound_by": bound_by if bound else None, "source": source})

    approved = approved_g3 or set()
    card_text = json.dumps(card, default=str, ensure_ascii=False).lower()
    for ref in refs:
        names = [str(c["column_name"]) for c in cols[ref]]
        scd = [n for n in names if SCD2.match(n)]
        if scd:
            add("scd2_validity", ref, ",".join(scd), "SCD2 markers " + ",".join(scd), True, "T05 T5a/T5b/T5c",
                _bound(b.get("scd2_flags"), ref))
        for c in cols[ref]:
            n, typ = str(c["column_name"]), str(c.get("data_type") or "").upper()
            if VERSION.match(n):
                add("version_filter", ref, n, f"version/scenario column {n}", True, "T05 T6a/T6b",
                    _bound(b.get("filters"), ref, n))
            why = semi_additive_signal(n, typ, card_text)
            if why:
                add("semi_additive", ref, n, f"semi-additive candidate {n} ({typ or 'type unknown'}; {why})", True,
                    "v4 P1 semi-additive", _bound(b.get("semi_additive_columns"), ref, n))
            if LOAD_TS.match(n):
                add("late_arrival", ref, n, f"load/ingestion column {n}: late-arrival band for G13", False,
                    "spec 5.6, T03 D")
            if typ.startswith(("TIMESTAMP", "DATETIME")):
                vm = valuemap.timezone_for(value_map, ref, n, approved)
                by_card = _date_binding(card, ref, n)
                add("timezone", ref, n, f"{n} is {typ}; its timezone is decided per column (value map or "
                    "bindings.date)", True, "G3 value map", bool(vm or by_card),
                    f"value map {vm}" if vm else "card bindings.date")
        if GA4.search(ref):
            add("late_arrival", ref, None, "GA4 daily table: updated up to 3 days after the table date", False,
                "spec 5.6 (GA4)")
    period = terms.get("period") or {}
    for p in profiles:
        ref = str(p.get("table_ref"))
        if ref not in cols:
            continue
        if p.get("profile") == "grain" and p.get("metric") == "n_distinct_key" and p.get("assertion") == "FAIL":
            add("fan_out_key", ref, None, f"key not unique: {p.get('detail')}", True, "T05 T5b, T01 E")
        if p.get("profile") == "freshness" and p.get("metric") == "max_date" and p.get("assertion") == "FAIL":
            add("partial_period", ref, None, f"data ends {p.get('value')}, card period ends {period.get('end')}",
                True, "v4 P2 freshness")
        if p.get("profile") == "completeness" and p.get("metric") == "n_days" and p.get("assertion") == "FAIL":
            add("partial_period", ref, "days", f"{p.get('value')} distinct days in the period", True,
                "v4 P2 completeness")
    seen: dict[str, dict[str, Any]] = {}
    for t in found:
        seen.setdefault(t["trap_id"], t)
    res = card.get("trap_resolutions") or {}
    g3 = g3_rows or {}
    out = []
    for t in seen.values():
        r = res.get(t["trap_id"])
        tb, col = valuemap._bare(t["table_ref"]), str(t.get("column_name") or "")
        col_l = col.lower() if col not in ("", "days") and "," not in col else ""
        unused = valuemap.not_used_for(value_map, t["table_ref"], col_l or None)
        if not unused and t["family"] == "partial_period" and date_cols.get(tb):
            unused = valuemap.not_used_for(value_map, t["table_ref"], date_cols[tb])
        if unused:
            out.append({**t, "blocking": False, "resolution": "not_used", "resolution_detail": f"value map {unused}"})
            continue
        if t["family"] in COLUMN_TRAPS_BY_USE and touched is not None and col_l and (tb, col_l) not in touched \
                and not t["bound"]:
            out.append({**t, "blocking": False, "resolution": "not_bound",
                        "resolution_detail": "the bindings and the value map do not use this column: informational"})
            continue
        if t["trap_id"] in g3:
            out.append({**t, "resolution": "g3_decision", "resolution_detail": g3[t["trap_id"]]})
            continue
        if isinstance(r, Mapping) and r.get("action") == "filter" and r.get("filter"):
            resolution, detail = "filter", str(r["filter"])
        elif isinstance(r, Mapping) and r.get("action") == "not_applicable" and str(r.get("reason") or "").strip():
            resolution, detail = "not_applicable", str(r["reason"])
        elif t["bound"] and t["family"] == "timezone":
            resolution, detail = "value_map", t["bound_by"]
        elif t["bound"]:
            resolution, detail = "bound", t["bound_by"]
        else:
            resolution, detail = "unresolved", None
        out.append({**t, "resolution": resolution, "resolution_detail": detail})
    return sorted(out, key=lambda t: t["trap_id"])


def run(run_dir: Path) -> int:
    """Scan, write 09_traps.csv, and return the exit code."""
    run_dir = Path(run_dir)
    catalog = c2.read_records(run_dir / "05_catalog.csv", "05_catalog.csv")[1]
    shortlist = c2.read_records(run_dir / "08_shortlist.csv", "08_shortlist.csv")[1]
    profiles = c2.read_records_if_exists(run_dir / "06_profiles.csv")
    card = c2.card_fields(run_dir / "02_metric_card.md")
    terms = c2.read_input_json(run_dir / "discovery_terms.json") if (run_dir / "discovery_terms.json").is_file() else {}
    vmap = valuemap.read(run_dir)
    lock = g2lock.require_bound(run_dir, "trap_scan.py")  # fix r4 C4-4: refused before the lock or off the G2 set
    if lock:  # after G2 only the locked tables (fix r3 B3-8)
        keep = list(lock.get("tables", {}))
        norm = {valuemap._bare(r) for r in keep}
        bound_tables = [str(x) for x in ((card.get("bindings") or {}).get("tables") or []) if isinstance(x, str)]
        outside = sorted({x for x in bound_tables if valuemap._bare(x) not in norm})
        if outside:
            raise RefusedError("card bindings name table(s) outside the G2 lock (14_discovery_lock.json): "
                               + ", ".join(outside) + "; locked: " + ", ".join(keep))
        by = {valuemap._bare(r.get("table_ref")): r for r in shortlist if r.get("table_ref")}
        shortlist = [dict(by.get(valuemap._bare(r)) or {"table_ref": r}, table_ref=r, rank=i)
                     for i, r in enumerate(keep, 1)]
    traps = scan(catalog, profiles, shortlist, card, terms, vmap, valuemap.approved_g3(run_dir), g3_trap_rows(run_dir))
    c2.write_records(run_dir / TRAPS, TRAP_COLUMNS, [tuple(t[n] for n, _ in TRAP_COLUMNS) for t in traps])
    open_ = [t["trap_id"] for t in traps if t["blocking"] and t["resolution"] == "unresolved"]
    if open_:
        raise CheckFailedError(f"{len(open_)} blocking trap(s) unresolved, G1b blocked until Max binds a filter "
                               "or records 'not applicable' with a reason: " + ", ".join(open_))
    print(f"{len(traps)} trap(s), none blocking and unresolved")
    return int(ExitCode.PASS)


def g3_trap_rows(run_dir: Path) -> dict[str, str]:
    """LOW (fix r4): trap_id -> 'decisions.csv row n: <outcome>: <reason>' of G3 rows that name a trap with a
    resolving outcome and a reason (the G3 decision rows suffice; no card trap_resolutions block is needed)."""
    out: dict[str, str] = {}
    for i, r in enumerate(c2.load_decisions(run_dir), 1):
        oc = str(r.get("outcome", "")).strip().lower()
        if str(r.get("gate")) == "G3" and r.get("check_id") and oc in G3_RESOLVING and str(r.get("reason") or "").strip():
            out[str(r["check_id"])] = f"decisions.csv row {i}: {oc}: {str(r['reason']).strip()}"
    return out


def _main(a: Any) -> int:
    c2.phase_check(a.run_dir, ["P2"])
    return run(a.run_dir)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    return c2.run_cli_args(lambda a: refusal.guard(a.run_dir, lambda: _main(a)), c2.base_parser("trap_scan.py", "T05 trap scan (spec 2.2 P2, DB-06)"), argv)


if __name__ == "__main__":
    sys.exit(main())

"""pin_plan.py: per-table pin routes, unpinnable list, late-arrival bands (design spec v1 sections 2.2 P3, 5.1, 5.4, 5.6; D-14).

Per table of the shortlist:
- pin_method = system_time when the table can take FOR SYSTEM_TIME AS OF and the business as-of (when the
  card fixes one) is inside its dataset's time-travel window with the 24-hour margin; csv_only otherwise
  (PROVISIONAL);
- a VIEW is resolved to its base tables from its definition (tables.get view query, the same text
  INFORMATION_SCHEMA.VIEWS holds, read for free); time travel on a view is silently ignored (L, BQ03), so
  compile.py inlines the definition. An unreadable definition, an EXTERNAL table, a materialized view, a
  snapshot or a routine called in a view definition is unpinnable (listed; the run is PROVISIONAL now);
- late-arrival bands come from run_config late_arrival_bands: open -> G13 (exit 3 after the plan is
  written), accepted_provisional -> PROVISIONAL, closed -> recorded.

as_of_utc itself is fixed at P6 start (spec 5.4) unless the card fixes a business as-of; the plan records
the window per dataset, the smallest window and the deadline rule (as_of + smallest window - 24 h).
The batch marker (ETL batch id or close marker) is the business pin (spec 5.2). View definitions are
stored with the project ID replaced by {{project}} (D-12).

Inputs: 00_preflight.json (windows), the shortlist (--tables FILE, default 14_discovery_lock.json "tables":
refs or objects with "ref"), run_config.json. Output: 15_pin_plan.json. Exit 2 wording (fix r3 B3-4, spec v4 1.4): "refused; no warehouse job was run (metadata reads, which bill nothing, may have been made); local effects: <files written, changed or removed, or none>" on stderr (lib/refusal.py).
After the G2 lock a --tables file naming a table outside 14_discovery_lock.json is refused (fix r3 B3-8).
Fix r11 L2 (live run 06): 13_table_decision.md carries the pin route, which this script writes after `discover.py
decide` ran at P2. Right after 15_pin_plan.json is written, when 13_table_decision.md exists (decide ran), it is
rewritten through lib.table_decision (the same text `discover.py decide` writes now) and one line says so, so P4's
packet freeze no longer refuses a decision file that only lacks the pin route. When the rebuild is refused (an invalid
value map, a binding outside the lock), the plan stays written, the decision file is left as it was, and one line on
stderr names the reason and says to rerun `discover.py decide` after the fix (the packet freeze refuses until then).
Exit 0 PASS (PROVISIONAL is a flag,
not a failure), 2 refused, 3 G13, 4 engine error.

CLI: python -I -X utf8 -B scripts/pin_plan.py --run-dir RUN [--tables FILE] [--batch-marker M] [--env-file F]
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlglot import exp  # noqa: E402

from lib import runctx, sqltext  # noqa: E402
from lib import refusal  # noqa: E402  fix r3 B3-4: exit-2 local effects on stderr
from lib.exit_codes import ExitCode, RefusedError  # noqa: E402

PLAN = "15_pin_plan.json"
MARGIN_HOURS = 24
MIN_WINDOW_HOURS = 48
PINNABLE_TYPES = {"TABLE"}


def sql_ref(ref: str, env: Mapping[str, Any] | None) -> str:
    """discover.py writes `alias.table` (alias = an env placeholder name, D-12); the SQL writes
    {{project}}.{{alias}}.table. Any other form is kept as written."""
    ph = (env or {}).get("placeholders", {})
    parts = ref.replace("`", "").split(".")
    if len(parts) == 2 and "{" not in ref and parts[0] in ph and parts[0] != "project":
        return "{{project}}.{{" + parts[0] + "}}." + parts[1]
    return ref


def shortlist_refs(run_dir: Path, tables_file: str | None, env: Mapping[str, Any] | None = None) -> list[str]:
    """Table references of the shortlist, as the SQL will write them."""
    p = run_dir / (tables_file or "14_discovery_lock.json")
    if not p.is_file():
        raise RefusedError(f"shortlist missing: {p}")
    raw = runctx.read_json(p)
    items = raw.get("tables", []) if isinstance(raw, dict) else raw
    refs = [x if isinstance(x, str) else (x.get("ref") or x.get("table")) for x in items]
    refs = [sql_ref(r, env) for r in refs if r]
    if not refs:
        raise RefusedError(f"{p}: no tables")
    lock = run_dir / "14_discovery_lock.json"
    if tables_file and lock.is_file():  # fix r3 B3-8: a --tables file cannot add a table Max did not keep at G2
        from lib import valuemap
        keep = {valuemap._bare(sql_ref(r, env)) for r in runctx.read_json(lock).get("tables", {})}
        outside = sorted({r for r in refs if valuemap._bare(r) not in keep})
        if outside:
            raise RefusedError(f"{p}: table(s) outside the G2 lock (14_discovery_lock.json): " + ", ".join(outside))
    return refs


def windows_from_preflight(run_dir: Path) -> dict[str, dict[str, Any]]:
    """{dataset ref: {"hours": int, "assumed": bool}} from 00_preflight.json."""
    p = run_dir / "00_preflight.json"
    if not p.is_file():
        raise RefusedError(f"{p} missing: preflight.py (P0) must pass first")
    pf = runctx.read_json(p)
    if pf.get("status") != "PASS":
        raise RefusedError("00_preflight.json status is not PASS")
    return {d["dataset"]: {"hours": int(d["time_travel_hours"]), "assumed": bool(d.get("assumed"))}
            for d in pf.get("datasets", [])}


def dataset_of(ref: str) -> str:
    """The dataset part of a reference as written (placeholder or literal)."""
    parts = ref.replace("`", "").split(".")
    if len(parts) < 2:
        raise RefusedError(f"table reference {ref!r} has no dataset part")
    return parts[-2]


def full_ref(ref: str, env: Mapping[str, Any]) -> str:
    """project.dataset.table for a metadata read (rendered in memory only)."""
    text = sqltext.render(ref, env.get("placeholders", {}), "bigquery").replace("`", "")
    parts = text.split(".")
    return text if len(parts) == 3 else f"{env['project']}.{text}"


def unrender_project(sql: str, project: str) -> str:
    """Replace the project ID by {{project}} in a view definition (D-12)."""
    out = re.sub(r"`" + re.escape(project) + r"\.([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)`", r"{{project}}.\1.\2", sql)
    return re.sub(r"(?<![\w-])" + re.escape(project) + r"\.", "{{project}}.", out)


def resolve_view(engine: Any, ref: str, view_sql: str, env: Mapping[str, Any], depth: int = 0
                 ) -> tuple[list[str], list[dict[str, str]]]:
    """(base table refs, unpinnable entries) of a view, recursing through views of views."""
    if depth > 8:
        return [], [{"ref": ref, "reason": "view nesting deeper than 8"}]
    tree = sqltext.parse_one_query(view_sql)
    bases, unp = [], []
    for d in tree.find_all(exp.Dot):
        if isinstance(d.expression, (exp.Anonymous, exp.Func)):
            unp.append({"ref": ref, "reason": f"routine called in the view definition: {d.sql()[:60]}"})
    for k in sqltext.base_table_keys(tree):
        info = engine.table_info(full_ref(k, env))
        if info.table_type == "VIEW":
            if not info.view_query:
                unp.append({"ref": k, "reason": "view definition unreadable"})
                continue
            b2, u2 = resolve_view(engine, k, unrender_project(info.view_query, env["project"]), env, depth + 1)
            bases += b2
            unp += u2
        elif info.table_type not in PINNABLE_TYPES:
            unp.append({"ref": k, "reason": f"{info.table_type} table cannot take time travel"})
        else:
            bases.append(k)
    return sorted(set(bases)), unp


def build_plan(refs: Sequence[str], engine: Any, env: Mapping[str, Any], cfg: Mapping[str, Any],
               windows: Mapping[str, Mapping[str, Any]], batch_marker: str | None, now: dt.datetime) -> dict[str, Any]:
    """The 15_pin_plan.json object."""
    pin_cfg = cfg.get("pin") or {}
    business = pin_cfg.get("business_as_of_utc")
    business_t = runctx.parse_utc(business) if business else None
    bands = cfg.get("late_arrival_bands") or []
    tables, unpinnable, reasons, used_windows = [], [], [], {}

    def window(ds: str) -> dict[str, Any]:
        if ds not in windows:
            hours = engine.time_travel_hours(sqltext.render(ds, env.get("placeholders", {}), "bigquery"))
            w = {"hours": hours or MIN_WINDOW_HOURS, "assumed": hours is None}
        else:
            w = dict(windows[ds])
        used_windows[ds] = w
        return w

    def route_for(ds: str) -> tuple[str, str | None]:
        w = window(ds)
        if business_t is not None and (now - business_t) > dt.timedelta(hours=w["hours"] - MARGIN_HOURS):
            return "csv_only", "business as-of older than the time-travel window"
        return "system_time", None

    for ref in refs:
        info = engine.table_info(full_ref(ref, env))
        entry: dict[str, Any] = {"ref": ref, "dataset": dataset_of(ref), "table_type": info.table_type,
                                 "view_query": None, "resolved_base_tables": []}
        if info.table_type == "VIEW":
            if not info.view_query:
                unpinnable.append({"ref": ref, "reason": "view definition unreadable"})
                entry["pin_method"] = "csv_only"
            else:
                vq = unrender_project(info.view_query, env["project"])
                bases, unp = resolve_view(engine, ref, vq, env)
                unpinnable += unp
                entry["view_query"] = vq
                for b in bases:
                    m, why = route_for(dataset_of(b))
                    entry["resolved_base_tables"].append({"ref": b, "pin_method": m, "reason": why})
                ok = bases and not unp and all(b["pin_method"] == "system_time" for b in entry["resolved_base_tables"])
                entry["pin_method"] = "system_time" if ok else "csv_only"
        elif info.table_type not in PINNABLE_TYPES:
            unpinnable.append({"ref": ref, "reason": f"{info.table_type} table cannot take time travel"})
            entry["pin_method"] = "csv_only"
        else:
            entry["pin_method"], why = route_for(entry["dataset"])
            if why:
                reasons.append(f"{ref}: {why}")
        entry["window_hours"] = window(entry["dataset"])["hours"]
        band = [b for b in bands if b["table"] == ref]
        entry["late_arrival"] = band[0] if band else None
        if band and band[0]["status"] == "accepted_provisional":
            reasons.append(f"{ref}: late-arrival band of {band[0]['band_hours']} h accepted as PROVISIONAL")
        tables.append(entry)
    if unpinnable:
        reasons.append("unpinnable dependencies: " + ", ".join(sorted({u["ref"] for u in unpinnable})))
    if any(t["pin_method"] == "csv_only" for t in tables):
        reasons.append("csv_only route for at least one table")
    for ds, w in used_windows.items():
        if w["assumed"]:
            reasons.append(f"time-travel window of {ds} unreadable: {MIN_WINDOW_HOURS} h assumed")
    smallest = min((w["hours"] for w in used_windows.values()), default=None)
    route = "system_time" if all(t["pin_method"] == "system_time" for t in tables) and not unpinnable else "csv_only"
    accepted = any(t["late_arrival"] and t["late_arrival"]["status"] == "accepted_provisional" for t in tables)
    status = "FINAL" if route == "system_time" and not accepted else "PROVISIONAL"
    return {"schema_version": "1", "created_utc": runctx.fmt_utc(now), "tables": tables, "unpinnable": unpinnable,
            "route": route, "pin_status": status, "provisional_reasons": reasons,
            "as_of_utc": runctx.fmt_utc(business_t) if business_t else None,
            "as_of_rule": "business as-of from the card" if business_t else
            f"fixed at P6 start: P6 start minus {int(pin_cfg.get('as_of_offset_minutes', 5))} minutes",
            "as_of_offset_minutes": int(pin_cfg.get("as_of_offset_minutes", 5)),
            "windows": {ds: w for ds, w in sorted(used_windows.items())}, "smallest_window_hours": smallest,
            "margin_hours": MARGIN_HOURS, "pin_deadline_rule": "as_of_utc + smallest_window_hours - margin_hours",
            "pin_deadline_utc": runctx.fmt_utc(business_t + dt.timedelta(hours=smallest - MARGIN_HOURS))
            if business_t and smallest else None,
            "business_pin": batch_marker or pin_cfg.get("batch_marker"), "as_of_group_id": None,
            "open_bands": [b for b in bands if b["status"] == "open"]}


def refresh_table_decision(run_dir: Path) -> bool:
    """Fix r11 L2: rewrite 13_table_decision.md with the pin route just written; True when it was rewritten.
    Fix r12 L1: never once the packet is frozen (packet.sha256 exists), as discover.py decide refuses then."""
    from lib import table_decision
    p = Path(run_dir) / table_decision.OUT
    if not p.is_file() or not (Path(run_dir) / "14_discovery_lock.json").is_file():
        return False
    if (Path(run_dir) / "packet.sha256").is_file():  # fix r12 L1: the same guard as discover.py decide
        print(f"pin_plan.py: packet frozen: {table_decision.OUT} not refreshed")
        return False
    try:
        text = table_decision.build(run_dir)
    except RefusedError as exc:
        sys.stderr.write(f"pin_plan.py: {table_decision.OUT} not refreshed ({exc}); fix that, then rerun "
                         "discover.py --run-dir RUN decide before P4\n")
        return False
    if p.read_text(encoding="utf-8") != text:
        table_decision.write(run_dir)
        print(f"pin_plan.py: {table_decision.OUT} refreshed with the pin route (what discover.py decide writes now)")
    else:
        print(f"pin_plan.py: {table_decision.OUT} already carries this pin route")
    return True


def main(argv: Sequence[str] | None = None, engine: Any = None) -> int:
    ap = argparse.ArgumentParser(prog="pin_plan.py", description="query-trail pin plan (spec 2.2 P3, 5)")
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--tables", default=None)
    ap.add_argument("--batch-marker", default=None)
    ap.add_argument("--env-file", default=None)
    a = ap.parse_args(argv)

    def body() -> int:
        runctx.enter(a.run_dir, ("P3",))
        cfg = runctx.load_run_config(a.run_dir)
        runctx.require_g0a(cfg)
        from lib import g2lock
        g2lock.require_bound(a.run_dir, "pin_plan.py")  # fix r4 C4-4: no pin plan before a lock bound to G2
        env = runctx.load_env(cfg, a.env_file)
        eng = engine
        if eng is None:
            from lib import bq
            eng = bq.BigQueryEngine(bq.Identity(env["project"], env["location"], env.get("service_account"),
                                                cfg.get("owner_mode") == "research"))
        plan = build_plan(shortlist_refs(a.run_dir, a.tables, env), eng, env, cfg, windows_from_preflight(a.run_dir),
                          a.batch_marker, runctx.utc_now())
        runctx.write_json(a.run_dir / PLAN, plan)
        refresh_table_decision(a.run_dir)
        if plan["open_bands"]:
            runctx.write_gate_request(a.run_dir, "G13", "P3", "pin_plan.py", "open late-arrival band",
                                      {"bands": plan["open_bands"]})
            print(f"pin_plan.py: NEEDS_DECISION: {len(plan['open_bands'])} open late-arrival band(s): G13")
            return int(ExitCode.NEEDS_DECISION)
        print(f"pin_plan.py: PASS: {len(plan['tables'])} tables, route {plan['route']}, {plan['pin_status']}")
        runctx.archive_own_request(a.run_dir, "pin_plan.py", ("G13",), "P3")  # fix r4 C4-10
        return int(ExitCode.PASS)

    return runctx.run_cli(lambda: refusal.guard(a.run_dir, body, warehouse=True), "pin_plan.py")


if __name__ == "__main__":
    sys.exit(main())

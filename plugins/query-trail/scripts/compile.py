"""compile.py: pin injection, allow-list and cumulative-CTE jobs (design spec v1 sections 2.2 P5 and P6, 5.1 to 5.3; D-08, D-14).

For every job (each step of chain(B), whole B, whole A):
- cumulative-CTE chaining (T02, spec 5.3): job N = `WITH s_a AS (...), ..., sN AS (...) SELECT * FROM sN`,
  steps in plan order, so no warehouse write is needed between steps; only the CTEs step N reads,
  transitively, are included (a sqlglot dependency walk, fix r1 B-M7; spec v2 5.3), so an unused read CTE is
  not billed (BigQuery bills at least 10 MiB per table referenced);
- pin injection by AST transform (research_r2/T03_work/pin_inject.py): `FOR SYSTEM_TIME AS OF <as_of>` on
  every base-table reference of the job (CTE names and table functions skipped; idempotent; a table
  whose pin plan route is csv_only gets no clause; a view is replaced by its definition first, because
  time travel on a view is silently ignored, L BQ03);
- allow-list (config/allowlist.json, D-08): exactly one SELECT/WITH statement, no forbidden statement
  or function pattern, no dataset-qualified function call; any hit exits 2 and nothing is written.

Executed SQL keeps the identifier placeholders (D-12): run_steps.py renders them in memory just before a
job is submitted. Outputs: executed/NN.run.sql, executed/B.run.sql, executed/A.run.sql and
executed/compile_report.json. At P5 (no --as-of) the as-of is provisional (now minus the offset); at
P6 run_steps.py re-renders with the fixed as_of_utc (spec 5.4). Before anything is read, B (and A in
second-author mode) must match its freeze (lib.freeze_check) and plan.json source_sha256 must equal B's
frozen sha256; otherwise exit 2: no warehouse call was made; local effects: none. Whole A is compiled in
second-author mode only. Exit 2 wording (fix r3 B3-4, spec v4 1.4): "refused; no warehouse call was made; local effects: <files written, changed or removed, or none>" on stderr (lib/refusal.py).

CLI: python -I -X utf8 -B scripts/compile.py --run-dir RUN [--as-of 2026-11-07T10:00:00Z]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import sqlglot  # noqa: E402
from sqlglot import exp  # noqa: E402

from lib import freeze_check, runctx, sqltext  # noqa: E402
from lib import refusal  # noqa: E402  fix r3 B3-4: exit-2 local effects on stderr
from lib.csv_contract import write_text_lf  # noqa: E402
from lib.exit_codes import ExitCode, RefusedError  # noqa: E402

EXECUTED_DIR = "executed"
PIN_PLAN = "15_pin_plan.json"
REPORT = "compile_report.json"
_AST_FORBIDDEN = (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter, exp.Command,
                  exp.Transaction, exp.Commit, exp.Rollback, exp.Set, exp.Use)


class ForbiddenError(RefusedError):
    """A statement or function outside the allow-list (exit 2, spec 2.2 P4 and P5)."""


def load_allowlist(path: Path | None = None) -> dict[str, Any]:
    """config/allowlist.json."""
    return runctx.read_json(path or runctx.CONFIG_DIR / "allowlist.json")


def check_allowlist(sql: str, dialect_name: str = "bigquery", allowlist: Mapping[str, Any] | None = None) -> exp.Expression:
    """Refuse anything but one SELECT/WITH statement free of forbidden patterns; return the parsed tree."""
    al = allowlist or load_allowlist()
    trees = sqltext.parse_statements(sql, dialect_name)
    if len(trees) != 1:
        raise ForbiddenError(f"one statement per job (allow-list): found {len(trees)}")
    tree = trees[0]
    if not sqltext.is_query(tree):
        raise ForbiddenError(f"statement {type(tree).__name__} is not SELECT/WITH (allow-list)")
    bad = [type(n).__name__ for n in tree.walk() if isinstance(n, _AST_FORBIDDEN)]
    if bad:
        raise ForbiddenError(f"forbidden construct inside the query: {bad[0]}")
    for d in tree.find_all(exp.Dot):
        if isinstance(d.expression, (exp.Anonymous, exp.Func)) and str(d.this.sql()).upper() not in ("SAFE",):
            raise ForbiddenError(f"dataset-qualified function call (UDF): {d.sql()[:80]}")
    text = sqltext.to_sql(tree, dialect_name, pretty=False)
    for section in ("forbidden_statement_patterns", "forbidden_function_patterns"):
        for p in al.get(section, []):
            if re.search(p["regex"], text):
                raise ForbiddenError(f"allow-list {p.get('id', p['regex'])}: {p['reason']}")
    return tree


# ---------------------------------------------------------------- pin injection
def as_of_literal(as_of: dt.datetime) -> str:
    """TIMESTAMP literal in UTC, microsecond precision."""
    return "TIMESTAMP '" + as_of.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f") + "+00'"


def _version_node(literal: str) -> exp.Expression:
    t = sqlglot.parse_one(f"SELECT * FROM x FOR SYSTEM_TIME AS OF {literal}", read=sqltext.dialect("bigquery"))
    return t.find(exp.Table).args["version"]


def inject_pin(tree: exp.Expression, literal: str | None, routes: Mapping[str, str],
               views: Mapping[str, str] | None = None, dialect_name: str = "bigquery") -> tuple[exp.Expression, list[str]]:
    """Add FOR SYSTEM_TIME AS OF to every base table whose route is system_time; return the pinned refs.

    `routes` maps a table reference (as written) to system_time or csv_only; a reference missing from
    the pin plan is refused (exit 2). `views` maps a view reference to its definition, inlined first.
    """
    views = dict(views or {})
    for _ in range(10):  # views of views: inline until none is left (bounded)
        changed = False
        for t in sqltext.base_tables(tree):
            key = sqltext.table_key(t)
            if key in views:
                sub = sqltext.parse_one_query(views[key], dialect_name)
                t.replace(exp.Subquery(this=sub, alias=exp.TableAlias(this=exp.to_identifier(t.alias_or_name))))
                changed = True
        if not changed:
            break
    pinned: list[str] = []
    version = _version_node(literal) if literal else None
    for t in sqltext.base_tables(tree):
        key = sqltext.table_key(t)
        route = routes.get(key)
        if route is None:
            raise RefusedError(f"table {key} is not in the pin plan (15_pin_plan.json): cannot pin")
        if route != "system_time" or version is None:
            continue
        existing = t.args.get("version")
        if existing is not None:
            if existing.sql() != version.sql():
                raise RefusedError(f"table {key} already carries a different FOR SYSTEM_TIME AS OF")
            pinned.append(key)
            continue
        t.set("version", version.copy())
        pinned.append(key)
    return tree, pinned


# ---------------------------------------------------------------- cumulative CTE jobs
def _refs(body: str, names: set[str]) -> set[str] | None:
    """Earlier CTE names a step body reads (unqualified table references); None when it does not parse."""
    for d in ("bigquery", "duckdb"):
        try:
            tree = sqltext.parse_one_query(body.rstrip().rstrip(";"), d)
        except sqltext.SqlTextError:
            continue
        inner = sqltext.cte_names(tree)
        return {t.name for t in tree.find_all(exp.Table) if not t.db and not t.catalog
                and t.name in names and t.name not in inner}
    return None


def step_dependencies(bodies: Sequence[tuple[str, str]], upto: int) -> set[str] | None:
    """CTE names step `upto` (1-based) depends on, transitively; None when a body does not parse."""
    names = [n for n, _ in bodies[:upto]]
    body_of = dict(bodies[:upto])
    need: set[str] = set()
    todo = [names[-1]]
    while todo:
        cur = todo.pop()
        earlier = set(names[:names.index(cur)])
        refs = _refs(body_of[cur], earlier)
        if refs is None:
            return None
        for r in refs - need:
            need.add(r)
            todo.append(r)
    return need


def assemble_job(bodies: Sequence[tuple[str, str]], upto: int) -> str:
    """Job `upto` (1-based): the CTEs step `upto` depends on plus itself, in plan order (spec 5.3, B-M7).

    When a body does not parse, every step 1..upto is kept (the unpruned cumulative job).
    """
    deps = step_dependencies(bodies, upto)
    keep = [(n, b) for i, (n, b) in enumerate(bodies[:upto], 1)
            if deps is None or i == upto or n in deps]
    parts = [f"{name} AS (\n{body.rstrip().rstrip(';')}\n)" for name, body in keep]
    return "WITH " + ",\n".join(parts) + f"\nSELECT * FROM {bodies[upto - 1][0]}"


def compile_sql(sql: str, literal: str | None, routes: Mapping[str, str], views: Mapping[str, str],
                dialect_name: str, allowlist: Mapping[str, Any]) -> tuple[str, list[str]]:
    """Allow-list, inject, re-check; return (executed SQL text, pinned table refs)."""
    tree = check_allowlist(sql, dialect_name, allowlist)
    if dialect_name == "bigquery":
        tree, pinned = inject_pin(tree, literal, routes, views, dialect_name)
    else:
        pinned = []
    text = sqltext.to_sql(tree, dialect_name, pretty=True)
    check_allowlist(text, dialect_name, allowlist)
    if dialect_name == "bigquery" and literal:
        again, _ = inject_pin(sqltext.parse_one_query(text, dialect_name), literal, routes, {}, dialect_name)
        if sqltext.to_sql(again, dialect_name, pretty=True) != text:
            raise RefusedError("pin injection is not idempotent on this job (spec 5.2)")
    return text, pinned


def load_pin_plan(run_dir: Path) -> dict[str, Any]:
    """15_pin_plan.json (pin_plan.py, P3)."""
    p = run_dir / PIN_PLAN
    if not p.is_file():
        raise RefusedError(f"{p} missing: pin_plan.py (P3) must run first")
    return runctx.read_json(p)


def routes_of(pin_plan: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """(table ref -> route, view ref -> definition) from the pin plan."""
    routes, views = {}, {}
    for t in pin_plan.get("tables", []):
        routes[t["ref"]] = t["pin_method"]
        if t.get("view_query"):
            views[t["ref"]] = t["view_query"]
        for b in t.get("resolved_base_tables", []):
            routes.setdefault(b["ref"], b["pin_method"])
    return routes, views


def compile_run(run_dir: Path, as_of: dt.datetime, provisional: bool) -> dict[str, Any]:
    """Write every executed job and compile_report.json; return the report."""
    sides = freeze_check.frozen_sides(run_dir)
    plan = runctx.read_json(run_dir / "plan.json")
    if plan.get("source_sha256") != sides["B"]:
        raise RefusedError("plan.json was split from another B (source_sha256 differs from the frozen B.sql): "
                           "nothing compiled; re-run split.py")
    dialect_name = plan.get("dialect", "bigquery")
    pin_plan = load_pin_plan(run_dir)
    routes, views = routes_of(pin_plan)
    al = load_allowlist()
    literal = as_of_literal(as_of)
    out_dir = run_dir / EXECUTED_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    bodies = []
    for s in plan["steps"]:
        text = (run_dir / s["file"]).read_text(encoding="utf-8")
        if sqltext.sha256_text(text) != s["sql_sha256"]:
            raise RefusedError(f"{s['file']} differs from plan.json (edited after split): nothing compiled")
        bodies.append((s["cte"], text))
    jobs = []
    header = f"-- query-trail executed SQL ({'provisional as_of, P5 check' if provisional else 'P6'}); as_of_utc {runctx.fmt_utc(as_of)}\n"
    for i, s in enumerate(plan["steps"], 1):
        text, pinned = compile_sql(assemble_job(bodies, i), literal, routes, views, dialect_name, al)
        jobs.append(_write_job(out_dir, f"{i:02d}.run.sql", header, text, f"step:{i:02d}", s["name"], pinned))
    for side in ("B", "A"):
        src = run_dir / f"{side}.sql"
        if side in sides:
            text, pinned = compile_sql(src.read_text(encoding="utf-8"), literal, routes, views, dialect_name, al)
            jobs.append(_write_job(out_dir, f"{side}.run.sql", header, text, f"whole:{side.lower()}", side, pinned))
    report = {"schema_version": "1", "as_of_utc": runctx.fmt_utc(as_of), "as_of_status": "provisional" if provisional
              else "fixed", "dialect": dialect_name, "chaining": "cumulative_cte", "jobs": jobs,
              "pin_routes": routes, "views_inlined": sorted(views)}
    runctx.write_json(out_dir / REPORT, report)
    return report


def _write_job(out_dir: Path, fname: str, header: str, text: str, label: str, name: str, pinned: list[str]) -> dict[str, Any]:
    full = header + text.rstrip() + "\n"
    write_text_lf(out_dir / fname, full)
    return {"label": label, "name": name, "path": f"{EXECUTED_DIR}/{fname}", "sha256": sqltext.sha256_text(full),
            "pinned_refs": pinned, "n_pinned": len(pinned)}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="compile.py", description="query-trail compile (spec 2.2 P5/P6, 5.2)")
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--as-of", default=None, help="as_of_utc (P6); omitted at P5 = provisional now - offset")
    a = ap.parse_args(argv)

    def body() -> int:
        runctx.enter(a.run_dir, ("P5", "P6"))
        cfg = runctx.load_run_config(a.run_dir)
        runctx.require_g0a(cfg)
        offset = int((cfg.get("pin") or {}).get("as_of_offset_minutes", 5))
        as_of = runctx.parse_utc(a.as_of) if a.as_of else runctx.utc_now() - dt.timedelta(minutes=offset)
        rep = compile_run(a.run_dir, as_of, provisional=a.as_of is None)
        print(f"compile.py: PASS: {len(rep['jobs'])} jobs, as_of {rep['as_of_utc']} ({rep['as_of_status']})")
        print(json.dumps({j["label"]: j["n_pinned"] for j in rep["jobs"]}))
        return int(ExitCode.PASS)

    return runctx.run_cli(lambda: refusal.guard(a.run_dir, body, warehouse=False), "compile.py")


if __name__ == "__main__":
    sys.exit(main())

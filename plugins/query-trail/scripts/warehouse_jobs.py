"""warehouse_jobs.py: the side-query runner for P2 discovery, P8 trace lookups and P9 logic inputs (design spec v1 sections 2.2 P2, P8, P9, 5.2, 6.2, 6.3; D-08, D-12).

discover.py, trace.py and logic_tests.py never touch the network; they write the queries they need and read
results from files (NOTES_stage2b interface table: "2a runner"). This script is that runner. Every query goes
through the same controls as a step job (run_steps.py):

- the statement and function allow-list (compile.check_allowlist: one SELECT or WITH, no forbidden pattern);
- trace lookups and logic inputs are pinned (compile.compile_sql with the manifest's as_of_utc and the
  pin plan's routes; spec 5.2), and refused after the pin deadline (spec 5.4); discovery metadata queries
  (INFORMATION_SCHEMA) are not pinned, and profiles run before P3 are not pinned either (P2 has no as-of);
- placeholders rendered in memory from the env file only (D-12): {{project}}, dataset aliases, {{region}};
- dry run first, then maximum_bytes_billed per job, labels query_trail_run / step=<kind>_<n> plus the run's
  job_labels, cache off; a job over the cap is recorded `over_cap` (discover.py then opens G15);
- every job runs behind the run's one budget ledger (lib.bq.metered; spec v2 5.3, fix r1 B-H3): a job whose
  reservation (max(dry run, 10 MiB x tables referenced)) would pass the per-run budget stops the invocation
  with G15 at the kind's phase (filter, raise cap, stop; exit 3); jobs already run stay recorded.
- trace lookups are batched (fix r1 B-M7): key lookups on the same table (one table, no join, no aggregate)
  run as one job per table per batch of up to BATCH_MAX lookups (`WHERE (lookup 1) OR (lookup 2) ...`); each
  lookup is then evaluated locally in DuckDB on the batch rows, so its result file is what its own job
  would return. Aggregating lookups (many_to_1) and anything else keep one job each.

Kinds:
  discovery  discover_plan.json queries -> discovery_inputs/<query_id>.json {"query_id","status","rows"}
             (status ok | denied | error | over_cap | not_run)                                    phase P2
  trace      trace/lookups.json entries with engine bigquery -> trace/results/<lookup_id>.json  phase P8
  logic      logic_inputs.json (or --inputs FILE) {"inputs": [{"name", "sql_path"[, "out"]}]} -> `out`, default
             logic_inputs/<name>.csv (contract CSV with sidecar and sha256, D-04)                phase P9
             Package A's step entries (fix r3 A3-2 / B3-8 addendum: the level-60 route of a `step:<name>` MECE or
             pop_bands source) are {"name": "step_<name>", "sql_path": "executed/NN.run.sql", "out": ...}: the step's
             executed cumulative SQL, re-pinned (idempotent) at the manifest's as_of_utc, capped and metered.
Bytes billed per job go to warehouse_jobs_report.json (appended per kind).

CLI:  warehouse_jobs.py --run-dir R discovery|trace|logic [--env-file F] [--only ID ...] [--inputs FILE]
After the G2 lock (14_discovery_lock.json), a discovery plan query that profiles a table outside the lock refuses the
whole invocation before any job (fix r3 B3-8: a table Max dropped at G2 is never queried again).

Exit: 0 every job ran (statuses recorded); 2 refused (state, G0a, allow-list, pin deadline, missing input, a table
      outside the G2 lock): stderr says whether any warehouse call was made (a refusal before the first job: none;
      local effects: none) or lists the jobs already run and recorded in warehouse_jobs_report.json and the ledger;
      3 a logic input over the cap, or the per-run budget would be passed (G15, gate_request.json);
      4 engine error on a logic input.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import decimal
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import duckdb  # noqa: E402

import compile as comp  # noqa: E402
from lib import bq, manifest, runctx, sqltext  # noqa: E402
from lib import refusal  # noqa: E402  fix r3 B3-4: exit-2 local effects on stderr
from lib import csv_contract as cc  # noqa: E402
from lib.exit_codes import ExitCode, QueryTrailError, RefusedError  # noqa: E402

PHASE = {"discovery": "P2", "trace": "P8", "logic": "P9"}
REPORT = "warehouse_jobs_report.json"
BATCH_MAX = 200


@dataclasses.dataclass
class SideJob:
    """One side query: id, SQL text (placeholders kept), whether it is pinned, where its result goes."""

    kind: str
    job_id: str
    sql: str
    pinned: bool
    out: Path
    sql_sha256: str | None = None  # fix r4 C4-11: logic jobs: sha256 of the executed SQL file they ran


def jsonable(v: Any) -> Any:
    """A JSON value for a result cell (decimals as text, dates and times ISO 8601)."""
    if isinstance(v, decimal.Decimal):
        return str(v)
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, bytes):
        return v.hex()
    return v


EXECUTED_HEADER = "-- query-trail executed SQL"  # compile.py _write_job header (executed/NN.run.sql)


def _jobs(run_dir: Path, kind: str, only: Sequence[str], inputs: str | None = None) -> list[SideJob]:
    if kind == "discovery":
        plan = runctx.read_json(run_dir / "discover_plan.json")
        pending = set(plan.get("pending", []))  # discover.py lists the queries with no result yet
        chosen = [q for q in plan.get("queries", []) if q["query_id"] in pending or only]
        chosen = [q for q in chosen if not only or q["query_id"] in only]
        lock = run_dir / "14_discovery_lock.json"
        if any(q.get("rung") == "P" for q in chosen):  # fix r4 C4-4: profiles only after a lock bound to G2
            from lib import g2lock
            g2lock.require_bound(run_dir, "warehouse_jobs.py discovery (profiling); no warehouse call was made")
        if lock.is_file():  # fix r3 B3-8: no profile of a table outside the G2 lock
            from lib import valuemap
            keep = {valuemap._bare(r) for r in runctx.read_json(lock).get("tables", {})}
            outside = sorted({str(q.get("table_ref")) for q in chosen
                              if q.get("rung") == "P" and valuemap._bare(q.get("table_ref")) not in keep})
            if outside:
                raise RefusedError("discover_plan.json profiles table(s) outside the G2 lock (14_discovery_lock.json): "
                                   + ", ".join(outside) + "; rerun discover.py plan; no warehouse call was made")
        jobs = [SideJob(kind, q["query_id"], q["sql"], False, run_dir / "discovery_inputs" / f"{q['query_id']}.json")
                for q in chosen]
    elif kind == "trace":
        lk = runctx.read_json(run_dir / "trace" / "lookups.json")["lookups"]
        jobs = [SideJob(kind, x["lookup_id"], (run_dir / x["sql_path"]).read_text(encoding="utf-8"),
                        bool(x.get("needs_pin", True)), run_dir / "trace" / "results" / f"{x['lookup_id']}.json")
                for x in lk if x.get("engine") == "bigquery"]
    else:
        spec_path = runctx.resolve_in_run(run_dir, inputs or "logic_inputs.json")
        if not spec_path.is_file():
            raise RefusedError(f"{spec_path.name} missing: logic_tests.py --write-inputs writes it (P9)")
        spec = runctx.read_json(spec_path)
        jobs = []
        for e in spec.get("inputs", []):
            if not isinstance(e, Mapping) or not e.get("name") or not e.get("sql_path"):
                raise RefusedError(f"{spec_path.name}: every input is {{name, sql_path[, out]}}; got {e!r}")
            src = runctx.resolve_in_run(run_dir, e["sql_path"])
            out = runctx.resolve_in_run(run_dir, e.get("out") or f"logic_inputs/{e['name']}.csv")
            text = src.read_text(encoding="utf-8")
            if text.startswith(EXECUTED_HEADER):  # compile.py's header line (its "; as_of" reads as a 2nd statement)
                text = text.split("\n", 1)[1] if "\n" in text else ""
            jobs.append(SideJob(kind, e["name"], text, True, out, cc.sha256_file(src)))
    return [j for j in jobs if not only or j.job_id in only]


def stamp_provenance(run_dir: Path, csv_path: Path, sql_sha256: str | None, attempt: int | None) -> dict[str, Any]:
    """Fix r4 C4-11: write {sql_sha256, as_of_utc, attempt} into a logic CSV's sidecar (`provenance`), so
    logic_tests.py accepts the CSV only for the executed SQL, pin and attempt it was produced under."""
    sp = cc.sidecar_path(csv_path)
    side = runctx.read_json(sp)
    prov = {"sql_sha256": sql_sha256, "as_of_utc": ((manifest.load(run_dir) or {}).get("pin") or {}).get("as_of_utc"),
            "attempt": int(attempt) if attempt is not None else runctx.current_attempt(run_dir)}
    side["provenance"] = prov
    runctx.write_json(sp, side)
    return prov


def _pin_inputs(run_dir: Path) -> tuple[str | None, dict[str, str], dict[str, str], dt.datetime | None]:
    """(as_of literal, routes, views, deadline) from manifest.json and 15_pin_plan.json."""
    m = manifest.load(run_dir) or {}
    pin = m.get("pin") or {}
    if not pin.get("as_of_utc"):
        raise RefusedError("no as_of_utc in manifest.json: pinned side queries run after P6 (spec 5.4)")
    pp = comp.load_pin_plan(run_dir)
    routes, views = comp.routes_of(pp)
    as_of = runctx.parse_utc(pin["as_of_utc"])
    deadline = runctx.parse_utc(pin["pin_deadline_utc"]) if pin.get("pin_deadline_utc") else None
    return comp.as_of_literal(as_of), routes, views, deadline


def scrub(text: str, env: Mapping[str, Any]) -> str:
    """Identifiers in an error text replaced by their placeholders (D-12): project, service account, datasets."""
    pairs = [(str(v), "{{" + k + "}}") for k, v in (env.get("placeholders") or {}).items() if v and len(str(v)) >= 4]
    if env.get("service_account"):
        pairs.append((str(env["service_account"]), "{{service_account}}"))
    out = str(text)
    for val, ph in sorted(pairs, key=lambda x: -len(x[0])):
        out = out.replace(val, ph)
    return out


def _status_of(exc: Exception) -> str:
    msg = str(exc).lower()
    if isinstance(exc, bq.CapExceeded):
        return "over_cap"
    if any(k in msg for k in ("forbidden", "access denied", "permission", "403")):
        return "denied"
    return "error"


def _rows(engine: Any, res: bq.JobResult) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for batch in engine.stream(res):
        out += [{k: jsonable(v) for k, v in r.items()} for r in batch.to_pylist()]
    return out


def _write_csv(engine: Any, res: bq.JobResult, path: Path, caps: Mapping[str, int]) -> cc.Sidecar:
    import run_steps

    con = duckdb.connect()
    try:
        cc.run_sql(con, "SET TimeZone = 'UTC'")
        n = run_steps.load_stream(con, engine.stream(res), res.schema, caps["stream_rows"], caps["stream_bytes"])
        if n != res.total_rows:
            raise RefusedError(f"{path.name}: {n} rows streamed, job COUNT(*) = {res.total_rows}")
        return cc.write_query_csv(con, "SELECT * FROM qt_step", path, expected_rows=res.total_rows)
    finally:
        con.close()


# ---------------------------------------------------------------- trace batching (fix r1 B-M7)
def batch_shape(sql: str) -> tuple[str, Any, Any] | None:
    """(table key, table node, WHERE condition) of a key lookup that can share a batch job, else None.

    Batchable: one SELECT over one stored table, a WHERE, no join, GROUP BY, HAVING, QUALIFY, DISTINCT,
    window, aggregate or subquery (so evaluating it on the batch rows equals running it on the table).
    """
    from sqlglot import exp

    try:
        tree = sqltext.parse_one_query(sql, "bigquery")
    except sqltext.SqlTextError:
        return None
    if not isinstance(tree, exp.Select) or tree.args.get("where") is None or tree.args.get("joins"):
        return None
    if any(tree.args.get(k) is not None for k in ("group", "having", "qualify", "distinct", "with")):
        return None
    if any(isinstance(n, (exp.AggFunc, exp.Window, exp.Subquery)) for n in tree.walk()) or \
            len(list(tree.find_all(exp.Select))) != 1:
        return None
    fr = tree.args.get("from_")
    if fr is None or not isinstance(fr.this, exp.Table) or sqltext.is_table_function(fr.this):
        return None
    return sqltext.table_key(fr.this), fr.this, tree.args["where"].this


def batch_sql(table: Any, conds: Sequence[Any]) -> str:
    """SELECT * FROM <table> WHERE (c1) OR (c2) ...: every row any lookup of the batch could select."""
    from sqlglot import exp

    t = table.copy()
    t.set("alias", None)
    q = exp.select("*").from_(t).where(exp.or_(*[exp.Paren(this=c.copy()) for c in conds]))
    return sqltext.to_sql(q, "bigquery", pretty=False)


def local_lookup(con: Any, sql: str) -> list[dict[str, Any]]:
    """One lookup evaluated in DuckDB on the batch rows (table qt_step)."""
    from sqlglot import exp

    tree = sqltext.parse_one_query(sql, "bigquery")
    tree.args["from_"].this.replace(exp.Table(this=exp.to_identifier("qt_step")))
    dsql = sqltext.transpile_to_duckdb(tree, strip_time_travel=True)
    return [{k: jsonable(v) for k, v in r.items()} for r in con.sql(dsql).to_arrow_table().to_pylist()]


def _batches(jobs: Sequence[SideJob]) -> tuple[list[tuple[str, list[tuple[SideJob, Any, Any]]]], list[SideJob]]:
    """Group batchable trace lookups by (table, pinned) in plan order; the rest run one job each."""
    groups: dict[tuple[str, bool], list[tuple[SideJob, Any, Any]]] = {}
    single: list[SideJob] = []
    for j in jobs:
        shape = batch_shape(j.sql)
        if shape is None:
            single.append(j)
            continue
        groups.setdefault((shape[0], j.pinned), []).append((j, shape[1], shape[2]))
    out = []
    for (key, _pinned), items in groups.items():
        for i in range(0, len(items), BATCH_MAX):
            out.append((key, items[i:i + BATCH_MAX]))
    return out, single


def run(run_dir: Path, kind: str, cfg: Mapping[str, Any], env: Mapping[str, Any], engine: Any,
        only: Sequence[str] = (), now_fn: Any = runctx.utc_now, attempt: int | None = None,
        batch: bool = True, inputs: str | None = None) -> dict[str, Any]:
    """Run every side job of `kind`; return the report section."""
    jobs = _jobs(run_dir, kind, only, inputs)
    engine = bq.metered(engine, run_dir, cfg, PHASE[kind], attempt)  # the one query path: the run's ledger
    al = comp.load_allowlist()
    caps = {**{"stream_rows": 10_000_000, "stream_bytes": 1_000_000_000}, **cfg["caps"]}
    placeholders = dict(env.get("placeholders", {}))
    placeholders.setdefault("region", str(env.get("location", "")).lower())
    literal = routes = views = deadline = None
    if any(j.pinned for j in jobs):
        literal, routes, views, deadline = _pin_inputs(run_dir)
    log: list[dict[str, Any]] = []

    def compiled(j_sql: str, pinned_job: bool, job_id: str) -> tuple[str, list[str]]:
        if pinned_job and deadline and now_fn() > deadline:
            raise RefusedError(f"pin deadline {runctx.fmt_utc(deadline)} passed before {job_id}: re-pin or "
                               "continue as csv_only (G8, spec 5.4)")
        if pinned_job:
            text, pinned_refs = comp.compile_sql(j_sql, literal, routes, views, "bigquery", al)
        else:
            comp.check_allowlist(j_sql, "bigquery", al)
            text, pinned_refs = j_sql, []
        return sqltext.render(text, placeholders, "bigquery"), pinned_refs

    def budget_stop(exc: bq.BudgetExceeded, entry: Mapping[str, Any]) -> None:
        _save(run_dir, kind, log)
        runctx.write_gate_request(run_dir, "G15", PHASE[kind], "warehouse_jobs.py",
                                  "the per-run budget would be passed (filter, raise the cap, or stop)",
                                  {**exc.details, "job": entry.get("id")})

    if kind == "trace" and batch:
        groups, jobs = _batches(jobs)
        for n, (key, items) in enumerate(groups, 1):
            entry = _run_batch(run_dir, n, key, items, compiled, engine, cfg, env, caps, budget_stop)
            log.append(entry)
    for i, j in enumerate(jobs, 1):
        sql, pinned = compiled(j.sql, j.pinned, j.job_id)
        jc = bq.job_config(cfg["run_id"], f"{kind[:5]}_{i:03d}", caps["warehouse_bytes_per_job"], False,
                           env.get("location"), cfg.get("job_labels") or {})
        entry: dict[str, Any] = {"id": j.job_id, "pinned_refs": pinned, "dry_run_bytes": None, "bytes_billed": 0,
                                 "status": None, "job_id": None}
        try:
            dr = engine.dry_run(sql, dataclasses.replace(jc, dry_run=True))
            entry["dry_run_bytes"] = dr.total_bytes_processed
            if bq.check_dry_run_against_cap(dr, jc) != ExitCode.PASS:
                raise bq.CapExceeded(f"dry-run estimate {dr.total_bytes_processed} over the per-job cap")
            res = engine.run(sql, jc)
            entry.update(job_id=res.job_id, bytes_billed=res.total_bytes_billed, rows=res.total_rows,
                         cache_hit=res.cache_hit)
            if kind == "logic":
                sc = _write_csv(engine, res, j.out, caps)
                stamp_provenance(run_dir, j.out, j.sql_sha256, attempt)  # fix r4 C4-11
                entry.update(status="ok", csv=j.out.relative_to(run_dir).as_posix(), csv_sha256=sc.csv_sha256)
            else:
                rows = _rows(engine, res)
                entry["status"] = "ok"
                body = {"status": "ok", "rows": rows}
                runctx.write_json(j.out, {"query_id": j.job_id, **body} if kind == "discovery" else
                                  _stamped(run_dir, j, body))
        except bq.BudgetExceeded as exc:
            budget_stop(exc, entry)
            raise
        except QueryTrailError as exc:
            entry.update(status=_status_of(exc), detail=scrub(str(exc), env)[:300])
            if kind == "logic":
                if entry["status"] == "over_cap":
                    runctx.write_gate_request(run_dir, "G15", "P9", "warehouse_jobs.py",
                                              "a logic input is over the per-job cap", entry)
                    _save(run_dir, kind, log + [entry])
                    raise
                _save(run_dir, kind, log + [entry])
                raise
            body = {"status": entry["status"], "rows": [], "detail": entry["detail"]}
            runctx.write_json(j.out, {"query_id": j.job_id, **body} if kind == "discovery" else
                              _stamped(run_dir, j, body))
        log.append(entry)
    return _save(run_dir, kind, log)


def _stamped(run_dir: Path, j: SideJob, body: Mapping[str, Any]) -> dict[str, Any]:
    """Fix r5 addendum 8: a trace result carries the lookup SQL's sha256, the run's as_of_utc and the attempt
    (runctx.lookup_stamp); trace.py refuses a result whose stamp is not the current one."""
    return {**dict(body), **runctx.lookup_stamp(run_dir, j.sql)}


def _run_batch(run_dir: Path, n: int, key: str, items: Sequence[tuple[SideJob, Any, Any]], compiled: Any,
               engine: Any, cfg: Mapping[str, Any], env: Mapping[str, Any], caps: Mapping[str, int],
               budget_stop: Any) -> dict[str, Any]:
    """One batch job for the key lookups on `key`; one result file per lookup; return the report entry."""
    import run_steps

    first = items[0][0]
    sql, pinned = compiled(batch_sql(items[0][1], [c for _, _, c in items]), first.pinned, f"batch {key}")
    jc = bq.job_config(cfg["run_id"], f"trace_b{n:03d}", caps["warehouse_bytes_per_job"], False,
                       env.get("location"), cfg.get("job_labels") or {})
    entry: dict[str, Any] = {"id": f"batch_{n:03d}", "table": key, "lookups": [j.job_id for j, _, _ in items],
                             "pinned_refs": pinned, "dry_run_bytes": None, "bytes_billed": 0, "status": None,
                             "job_id": None}
    con = duckdb.connect()
    try:
        try:
            dr = engine.dry_run(sql, dataclasses.replace(jc, dry_run=True))
            entry["dry_run_bytes"] = dr.total_bytes_processed
            if bq.check_dry_run_against_cap(dr, jc) != ExitCode.PASS:
                raise bq.CapExceeded(f"dry-run estimate {dr.total_bytes_processed} over the per-job cap")
            res = engine.run(sql, jc)
            entry.update(job_id=res.job_id, bytes_billed=res.total_bytes_billed, rows=res.total_rows,
                         cache_hit=res.cache_hit)
            cc.run_sql(con, "SET TimeZone = 'UTC'")
            got = run_steps.load_stream(con, engine.stream(res), res.schema, caps["stream_rows"], caps["stream_bytes"])
            if got != res.total_rows:
                raise RefusedError(f"batch {key}: {got} rows streamed, job COUNT(*) = {res.total_rows}")
        except bq.BudgetExceeded as exc:
            budget_stop(exc, entry)
            raise
        except QueryTrailError as exc:
            entry.update(status=_status_of(exc), detail=scrub(str(exc), env)[:300])
            for j, _, _ in items:
                runctx.write_json(j.out, _stamped(run_dir, j, {"status": entry["status"], "rows": [],
                                                               "detail": entry["detail"]}))
            return entry
        local_failed = []
        for j, _, _ in items:
            try:
                rows = local_lookup(con, j.sql)
            except Exception as exc:  # noqa: BLE001 - a lookup DuckDB cannot evaluate locally gets no result file
                local_failed.append(f"{j.job_id}: {type(exc).__name__}")
                runctx.write_json(j.out, _stamped(run_dir, j, {
                    "status": "error", "rows": [],
                    "detail": "batched lookup could not be evaluated locally; rerun it alone"}))
                continue
            runctx.write_json(j.out, _stamped(run_dir, j, {"status": "ok", "rows": rows}))
        entry["status"] = "ok" if not local_failed else "partial"
        if local_failed:
            entry["detail"] = "; ".join(local_failed)[:300]
        return entry
    finally:
        con.close()


def _save(run_dir: Path, kind: str, log: list[dict[str, Any]]) -> dict[str, Any]:
    p = run_dir / REPORT
    rep = runctx.read_json(p) if p.is_file() else {"schema_version": "1", "kinds": {}}
    sec = {"ran_utc": runctx.fmt_utc(runctx.utc_now()), "jobs": log,
           "bytes_billed_total": sum(int(x.get("bytes_billed") or 0) for x in log)}
    rep.setdefault("invocations", []).append({"kind": kind, **sec})  # every invocation kept: bytes are never lost
    rep["kinds"][kind] = sec
    rep["bytes_billed_total"] = sum(i["bytes_billed_total"] for i in rep["invocations"])
    runctx.write_json(p, rep)
    return sec


def main(argv: Sequence[str] | None = None, engine: Any = None, env: Mapping[str, Any] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="warehouse_jobs.py", description="side-query runner (spec 2.2 P2, P8, P9)")
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("kind", choices=sorted(PHASE))
    ap.add_argument("--env-file", default=None)
    ap.add_argument("--only", nargs="*", default=[])
    ap.add_argument("--inputs", default=None, help="logic: the inputs file, run-relative (default logic_inputs.json)")
    a = ap.parse_args(argv)

    def body() -> int:
        runctx.enter(a.run_dir, (PHASE[a.kind],))
        cfg = runctx.load_run_config(a.run_dir)
        runctx.require_g0a(cfg)
        e = dict(env) if env is not None else runctx.load_env(cfg, a.env_file)
        eng = engine or bq.BigQueryEngine(bq.Identity(e["project"], e["location"], e.get("service_account"),
                                                      cfg.get("owner_mode") == "research"))
        st_now = runctx.enter(a.run_dir, (PHASE[a.kind],))
        sec = run(a.run_dir, a.kind, cfg, e, eng, a.only, attempt=st_now.attempt, inputs=a.inputs)
        st = {}
        for x in sec["jobs"]:
            st[x["status"]] = st.get(x["status"], 0) + 1
        print(f"warehouse_jobs.py {a.kind}: {len(sec['jobs'])} job(s) {st}, {sec['bytes_billed_total']} bytes billed")
        runctx.archive_own_request(a.run_dir, "warehouse_jobs.py", ("G15",), PHASE[a.kind])  # fix r4 C4-10
        return int(ExitCode.PASS)

    return runctx.run_cli(lambda: refusal.guard(a.run_dir, body, warehouse=True), "warehouse_jobs.py")


if __name__ == "__main__":
    sys.exit(main())

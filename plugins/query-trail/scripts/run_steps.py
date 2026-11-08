"""run_steps.py: run every step job and whole B (and A) through one runner (design spec v1 sections 2.2 P6, 3.1, 3.2, 5.3, 5.4).

1. Edit guard: a step file whose sha256 differs from plan.json is refused (exit 2); nothing runs.
2. as_of_utc is fixed at P6 start (business as-of from the pin plan, else now minus the offset) and kept for
   the attempt; pin deadline = as_of + smallest window - 24 h; past it every job exits 2 (re-pin at G8).
3. compile.py renders every executed job with that as_of (cumulative CTEs, pins, allow-list).
4. Budget (spec v2 5.3, fix r1 B-H3): every job is dry-run first; a job over the per-job cap opens G15. The
   engine runs behind the run's one ledger (lib.bq.metered): the up-front sum of reservations
   (max(dry run, 10 MiB x tables referenced) per job) plus what the run already spent must fit the per-run
   budget, and every single job is reserved again before it runs; a breach opens G15 (gate_request.json,
   exit 3) before anything more is billed. Actual bytes billed go to manifest.budget.ledger.
5. Per job: labels query_trail_run and step (step=NN, whole_b, whole_a), cache off, byte cap; a job BigQuery
   refuses over maximum_bytes_billed opens G15 (exit 3). The result size picks the ladder plan
   (lib.levels.plan_step with the run's caps): a G15 plan exits 3; over the stream cap at 60/70 the hash is
   computed warehouse-side. Otherwise the result streams once (Arrow) into a local DuckDB table bounded by the
   stream cap, and the level's artifacts are written from it: row count, schema sidecar, multiset hash (always),
   aggregates (70), 1,000-row hash-rank sample and strata rows (80), full CSV (90 under the local cap, 100, and
   the deliverable at every level).
6. Checks: rows in the local table == job COUNT(*), rows in the CSV == COUNT(*) (exit 1, never accepted), header
   names == plan.json columns, cache_hit false, and at full CSV the streamed hash == the CSV hash.
7. Rows in to rows out (spec v2 2.2 P6, fix r1 B-H4): per step rows_in (read step: the source COUNT(*) at the
   pin, from the job itself when the read has no filter, else one cheap pinned COUNT job through the same
   runner; other steps: the input steps' rows_out per the op: max of the inputs for a join, sum for a
   union, the driving input otherwise), rows_out, row_delta_reason from the split op class and
   row_delta_source (split_op; g11_sentence when Max approved, at G11, a step sentence that announces a
   fan-out; max when a G8 row names the check). A join that increases rows, or a step whose count moves in
   the wrong direction, is `unexplained`: the required check p6_step_NN_rows_in_out FAILs (exit 1); its row
   carries no file hash, so only a G8 row naming that check_id explains it.
8. manifest.json (validated) with job ids, bytes billed per job (N x S measured against the dry-run sum),
   replayable per step; checks/run_steps_checks.csv; run_steps_report.json.

Exit 0 PASS, 1 a check failed, 2 refused (edit guard, deadline, missing input), 3 G15, 4 engine error. Exit 2
before the first job: no warehouse job was run (dry runs may have been made), local effects: none; a refusal after
jobs ran (the pin deadline passing mid-run) lists on stderr the jobs on the budget ledger and the files written
(fix r3 B3-4, lib/refusal.py).

CLI: python -I -X utf8 -B scripts/run_steps.py --run-dir RUN [--env-file F]
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import heapq
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import duckdb  # noqa: E402

from sqlglot import exp  # noqa: E402

import compile as comp  # noqa: E402
from lib import bq, canon, freeze_check, levels, manifest, runctx, sampling, sqltext, state  # noqa: E402
from lib import refusal  # noqa: E402  fix r3 B3-4: exit-2 local effects on stderr
from lib import csv_contract as cc  # noqa: E402
from lib.exit_codes import CheckFailedError, ExitCode, QueryTrailError, RefusedError  # noqa: E402

OUTPUTS = "outputs"
CHECKS = "checks"
REPORT = "run_steps_report.json"
CHECK_COLS = ["job", "check", "status", "detail", "attempt", "evidence_sha256"]  # fix r3 B3-6 / A3-4
_NUMERIC = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT",
            "DOUBLE", "FLOAT", "REAL", "DECIMAL")
_BQ_TO_DUCK = {"INT64": "BIGINT", "FLOAT64": "DOUBLE", "NUMERIC": "DECIMAL(38,9)", "BIGNUMERIC": "VARCHAR",
               "STRING": "VARCHAR", "BOOL": "BOOLEAN", "BYTES": "BLOB", "DATE": "DATE", "DATETIME": "TIMESTAMP",
               "TIME": "TIME", "TIMESTAMP": "TIMESTAMP WITH TIME ZONE", "JSON": "VARCHAR", "GEOGRAPHY": "VARCHAR"}


class GateNeeded(QueryTrailError):
    """A cap breach that needs Max (G15): gate_request.json is written, exit 3."""

    exit_code = ExitCode.NEEDS_DECISION

    def __init__(self, gate: str, reason: str, details: Mapping[str, Any]) -> None:
        super().__init__(f"{gate}: {reason}")
        self.gate, self.reason, self.details = gate, reason, dict(details)


@dataclasses.dataclass
class Job:
    """One warehouse job: a chain step or a whole query."""

    label: str
    bq_step: str
    name: str
    executed_path: str
    executed_sha256: str
    sql_path: str
    sql_sha256: str | None
    deliverable: bool
    side: str | None
    plan_step: Mapping[str, Any] | None


# ---------------------------------------------------------------- local artifacts
def duck_type(bq_type: str) -> str:
    """DuckDB type for an empty result's column (BigQuery schema)."""
    base = bq_type.split("(")[0].split("<")[0].upper()
    return _BQ_TO_DUCK.get(base, "VARCHAR")


def load_stream(con: duckdb.DuckDBPyConnection, batches: Any, schema: Sequence[tuple[str, str]],
                max_rows: int, max_bytes: int) -> int:
    """Insert Arrow batches into table qt_step; refuse (G15) beyond the stream cap; return rows loaded."""
    n, nbytes, created = 0, 0, False
    for b in batches:
        if not created:
            con.register("qt_batch", b)
            con.execute("CREATE TABLE qt_step AS SELECT * FROM qt_batch")
            con.unregister("qt_batch")
            created = True
        else:
            con.register("qt_batch", b)
            con.execute("INSERT INTO qt_step SELECT * FROM qt_batch")
            con.unregister("qt_batch")
        n += b.num_rows
        nbytes += b.nbytes
        if n > max_rows or nbytes > max_bytes:
            raise GateNeeded("G15", "result over the local stream cap while streaming",
                             {"rows_so_far": n, "bytes_so_far": nbytes, "stream_rows": max_rows, "stream_bytes": max_bytes})
    if not created:
        cols = ", ".join(f'{cc.quote_ident(nm)} {duck_type(t)}' for nm, t in schema)
        con.execute(f"CREATE TABLE qt_step ({cols})")
    return n


def write_schema_only(path: Path, columns: Sequence[cc.Column], rows: int) -> None:
    """Schema sidecar for a step whose rows are not retained (levels 60 to 80)."""
    side = cc.Sidecar(tuple(columns), rows, "", 0).to_json()
    side["csv_retained"] = False
    runctx.write_json(path, side)


def write_aggregates(con: duckdb.DuckDBPyConnection, columns: Sequence[cc.Column], keys: Sequence[str],
                     path: Path, max_groups: int) -> str:
    """COUNT and SUM of every numeric column by the grouping columns (totals when over max_groups); level 70."""
    q = cc.quote_ident
    nums = [c for c in columns if c.type.split("(")[0].upper() in _NUMERIC and c.name not in keys]
    keys = [k for k in keys if k in {c.name for c in columns}]
    aggs = ["COUNT(*) AS qt_rows"] + [f"COUNT({q(c.name)}) AS {q('count__' + c.name)}, "
                                      f"SUM({q(c.name)}) AS {q('sum__' + c.name)}" for c in nums]
    mode = "by_keys"
    if keys:
        groups = cc.scalar(con, f"SELECT COUNT(*) FROM (SELECT DISTINCT {', '.join(q(k) for k in keys)} FROM qt_step)")
        if groups > max_groups:
            keys, mode = [], f"totals ({groups} groups > {max_groups})"
    else:
        mode = "totals"
    sel = ", ".join([q(k) for k in keys] + aggs)
    sql = f"SELECT {sel} FROM qt_step" + (f" GROUP BY {', '.join(q(k) for k in keys)} ORDER BY "
                                          f"{', '.join(q(k) + ' NULLS LAST' for k in keys)}" if keys else "")
    cc.write_query_csv(con, sql, path)
    return mode


def write_sample(con: duckdb.DuckDBPyConnection, columns: Sequence[cc.Column], salt: str, n: int, path: Path,
                 float_tolerance: float | None) -> int:
    """Hash-rank sample: row id = sha256(canonical row | occurrence index), rank = sha256(salt | row id); level 80.

    The occurrence index among identical canonical rows replaces the file position (spec 6.2 note), so
    duplicates stay distinct units and BigQuery's arbitrary result order does not matter.
    """
    specs = canon.specs_for(columns, float_tolerance)
    ids = sampling.OccurrenceIds()
    heap: list[tuple[str, str, tuple[Any, ...]]] = []
    for row in canon.iter_query_rows(con, "SELECT * FROM qt_step", columns):
        rid = ids.next_id(canon.canon_row(row, specs))
        rank = sampling.rank_key(salt, rid)
        item = (_neg(rank), _neg(rid), row)
        if len(heap) < n:
            heapq.heappush(heap, item)
        elif item > heap[0]:
            heapq.heapreplace(heap, item)
    picked = sorted(((_neg(r), _neg(i), row) for r, i, row in heap))
    con.execute("CREATE OR REPLACE TABLE qt_sample AS SELECT ''::VARCHAR AS _qt_rank, ''::VARCHAR AS _qt_row_id, * "
                "FROM qt_step LIMIT 0")
    if picked:
        ph = ", ".join(["?"] * (len(columns) + 2))
        con.executemany(f"INSERT INTO qt_sample VALUES ({ph})", [[r, i, *row] for r, i, row in picked])
    cc.write_query_csv(con, "SELECT * FROM qt_sample ORDER BY _qt_rank NULLS LAST, _qt_row_id NULLS LAST", path)
    return len(picked)


def _neg(hexstr: str) -> str:
    """Order-reversing map on hex digests so a min-heap keeps the n smallest ranks."""
    return hexstr.translate(str.maketrans("0123456789abcdef", "fedcba9876543210"))


def write_strata(con: duckdb.DuckDBPyConnection, columns: Sequence[cc.Column], per: int, path: Path) -> int:
    """Strata rows: largest values, boundary dates, NULL and 'other' buckets, up to `per` each; level 80 (assumed)."""
    q = cc.quote_ident
    tie = ", ".join(f"{q(c.name)} NULLS LAST" for c in columns)
    parts = []
    for c in columns:
        base = c.type.split("(")[0].upper()
        col = q(c.name)
        if base in _NUMERIC:
            parts.append(f"(SELECT 'largest:{c.name}' AS _qt_stratum, * FROM qt_step WHERE {col} IS NOT NULL "
                         f"ORDER BY {col} DESC NULLS LAST, {tie} LIMIT {per})")
        if base.startswith(("DATE", "TIMESTAMP")):
            for agg in ("MIN", "MAX"):
                parts.append(f"(SELECT 'boundary_{agg.lower()}:{c.name}' AS _qt_stratum, * FROM qt_step WHERE {col} = "
                             f"(SELECT {agg}({col}) FROM qt_step) ORDER BY {tie} LIMIT {per})")
        parts.append(f"(SELECT 'null:{c.name}' AS _qt_stratum, * FROM qt_step WHERE {col} IS NULL ORDER BY {tie} LIMIT {per})")
        if base in ("VARCHAR", "TEXT"):
            parts.append(f"(SELECT 'other:{c.name}' AS _qt_stratum, * FROM qt_step WHERE lower({col}) = 'other' "
                         f"ORDER BY {tie} LIMIT {per})")
    sql = " UNION ALL ".join(parts) if parts else "SELECT 'none' AS _qt_stratum, * FROM qt_step LIMIT 0"
    sc = cc.write_query_csv(con, sql, path)
    return sc.rows


def p6_check_id(job: Any, check: Any) -> str:
    """The checks_manifest.csv id of one P6 step check: p6_<job>_<check> (':' -> '_')."""
    return "p6_" + "_".join(str(x or "").replace(":", "_") for x in (job, check))


def p6_status(status: str) -> str:
    return status if status in ("PASS", "FAIL") else "NOT_RUN"


def check_evidence(job: Any, check: Any, status: str, detail: Any, attempt: int) -> str:
    """Fix r3 A3-4 / B3-6: the evidence of one P6 step check row, sha256 of check_id|attempt|status|detail (the rows
    share one file, so the file's sha256 is not a per-row evidence); runctx.evidence_digest, package A."""
    return runctx.evidence_digest(p6_check_id(job, check), int(attempt), p6_status(status), str(detail or ""))


def with_evidence(checks: Sequence[Sequence[Any]], attempt: int) -> list[list[Any]]:
    """run_steps_checks.csv rows: job, check, status, detail, attempt, evidence_sha256 (fix r3 B3-6)."""
    return [[job, check, status, detail, int(attempt), check_evidence(job, check, status, detail, attempt)]
            for job, check, status, detail, *_ in checks]


def p6_manifest_rows(checks: Sequence[Sequence[Any]], csv_sha256: str) -> list[dict[str, Any]]:
    """checks_manifest.csv rows for the P6 step checks (NOTES_stage2b item 6: one checks table, spec 6.4).

    A rows_in_out row is required and carries no csv_sha256, so a decision that quotes the checks file's
    hash cannot explain it; only a G8 row naming its check_id can (fix r1 B-H4). Rows that carry an
    evidence_sha256 (run_steps_checks.csv columns 5 and 6, fix r3) pass it on, so the manifest row and the CSV row
    bind decisions to the same evidence.
    """
    out = []
    for job, check, status, detail, *extra in checks:
        st = p6_status(status)
        rows_check = check == "rows_in_out"
        ev = extra[1] if len(extra) >= 2 else None
        out.append(runctx.check_row(p6_check_id(job, check), "P6", "step_check", st, f"{CHECKS}/run_steps_checks.csv",
                                    None if rows_check else csv_sha256, str(detail or ""),
                                    None if st != "NOT_RUN" else f"status {status}", True if rows_check else None,
                                    evidence_sha256=ev))
    return out


# ---------------------------------------------------------------- rows in to rows out (fix r1 B-H4)
FAN_OUT_WORDS = re.compile(r"(?i)\bfan(s|ning)?[\s-]?out\b")
APPROVE_OUTCOMES = frozenset({"approve", "approved", "accept", "accepted", "signed"})


def row_delta(verb: str, rows_in: int | None, rows_out: int, rows_in_min: int | None = None) -> tuple[str, bool]:
    """(row_delta_reason, explained) from the split op class and the counts.

    read, filter, distinct (dedupe), union: rows may only fall; project: unchanged; aggregate: at most
    max(rows in, 1) (a global aggregate over no rows returns one); join: rows_in is the larger input, so
    more rows out than that is a many-to-many fan-out nobody announced; a join whose count lands strictly
    between its smallest input (`rows_in_min`) and its largest is `join_mixed` (fix r2, dec r2 L5): the count is
    what a fan-out of the smaller input plus a filter of the larger would give, and counts cannot tell that from
    a plain reduction; equal to the larger input (a lookup) or at most the smallest is `join_reduce`; window:
    unchanged (project) or fewer (QUALIFY, filter); unnest: an UNNEST expands rows by design (join_fan_out);
    recursive: no rule.
    """
    if verb == "recursive":
        return "union", True
    if rows_in is None:
        return "unexplained", False
    if verb == "unnest":
        return ("join_fan_out" if rows_out > rows_in else "join_reduce"), True
    if verb == "join" and rows_out <= rows_in and rows_in_min is not None and rows_in_min < rows_out < rows_in:
        return "join_mixed", True
    if verb == "window":
        if rows_out == rows_in:
            return "project", True
        return ("filter", True) if rows_out < rows_in else ("unexplained", False)
    if verb == "aggregate":
        ok = rows_out <= max(rows_in, 1)
        return ("aggregate" if ok else "unexplained"), ok
    falls = {"read": "read", "filter": "filter", "distinct": "dedupe", "union": "union", "join": "join_reduce"}
    if verb in falls:
        ok = rows_out <= rows_in
        return (falls[verb] if ok else "unexplained"), ok
    ok = rows_out == rows_in
    return ("project" if ok else "unexplained"), ok


def read_is_filtered(sql: str, dialect_name: str) -> bool:
    """True unless the read step is a plain projection of one table (no WHERE, QUALIFY, LIMIT, DISTINCT, ...)."""
    try:
        tree = sqltext.parse_one_query(sql, dialect_name)
    except sqltext.SqlTextError:
        return True
    if not isinstance(tree, exp.Select):
        return True
    if any(tree.args.get(k) is not None for k in ("where", "qualify", "having", "group", "limit", "distinct")):
        return True
    if tree.args.get("joins"):
        return True
    fr = tree.args.get("from_")
    return fr is None or not isinstance(fr.this, exp.Table) or sqltext.is_table_function(fr.this)


def count_job_sql(step_sql: str, dialect_name: str) -> str:
    """SELECT COUNT(*) AS qt_rows FROM <the read step's table, as written>: the source count at the pin."""
    tree = sqltext.parse_one_query(step_sql, dialect_name)
    tables = sqltext.base_tables(tree)
    if len({sqltext.table_key(t) for t in tables}) != 1:
        raise RefusedError("a read step must read exactly one table to count its source rows")
    t = tables[0].copy()
    t.set("alias", None)
    t.set("version", None)
    q = exp.select(exp.alias_(exp.Count(this=exp.Star()), "qt_rows")).from_(t)
    return sqltext.to_sql(q, dialect_name, pretty=False)


def step_sentences(run_dir: Path, attempt: int) -> tuple[dict[str, str], bool]:
    """(step name -> G11 sentence from step_sentences.json, whether G11 was approved this attempt)."""
    p = run_dir / "step_sentences.json"
    sents = runctx.read_json(p) if p.is_file() else {}
    sents = {str(k): str(v) for k, v in (sents or {}).items()} if isinstance(sents, Mapping) else {}
    approved = any(str(r.get("gate")) == "G11" and str(r.get("outcome", "")).strip().lower() in APPROVE_OUTCOMES
                   and int(r.get("attempt") or 1) == attempt for r in runctx.load_decisions(run_dir))
    return sents, approved


def decision_applies(row: Mapping[str, Any], check: Mapping[str, Any]) -> bool:
    """Fix r3 A3-4 (shared interface, one rule; package A owns it): a decision row applies to a check only when its
    input_hash is non-empty and equals the check's evidence_sha256. No same-attempt shortcut."""
    return bool(runctx.decision_applies(row, check))


def g8_explained(decisions: Sequence[Mapping[str, Any]], check_id: str, evidence_sha256: str | None) -> bool:
    """A G8 row with an explaining outcome and a reason that names `check_id` and binds to this very evidence (the
    unexplained FAIL row's sha256 of check_id|attempt|status|detail): an explanation of another failure, of another
    attempt, or without a hash never explains this one (fix r3 A3-4; dec r2 H1)."""
    check = {"check_id": check_id, "evidence_sha256": evidence_sha256}
    return any(str(d.get("gate", "")).upper() == "G8" and str(d.get("check_id") or "") == check_id
               and str(d.get("outcome", "")).strip().lower() in runctx.EXPLAINING_OUTCOMES
               and str(d.get("reason") or "").strip() and decision_applies(d, check) for d in decisions)


def rows_in_of(s: Mapping[str, Any], rows_by_name: Mapping[str, int], rows_out: int, filtered: bool,
               counted: int | None) -> tuple[int | None, str]:
    """(rows_in, basis) of one plan step."""
    if s["is_read_step"]:
        if not filtered:
            return rows_out, "unfiltered read: the step job read every source row at the pin"
        return counted, "pinned COUNT(*) job on the source table"
    prior = [rows_by_name.get(i["ref"]) for i in s.get("inputs", []) if i["kind"] == "prior_step"]
    if not prior or any(v is None for v in prior):
        return None, "input rows not recorded"
    verb = s.get("verb", "")
    if verb in ("join", "unnest"):
        return max(prior), "max of the inputs' rows_out (more rows out means a many-to-many fan-out)"
    if verb in ("union", "recursive"):
        return sum(prior), "sum of the inputs' rows_out"
    return prior[0], f"rows_out of {s['inputs'][0]['ref']}"


# ---------------------------------------------------------------- the runner
def _ladder(cfg: Mapping[str, Any]) -> levels.Ladder:
    lad = levels.load_ladder()
    return dataclasses.replace(lad, caps={**lad.caps, **cfg["caps"]})


def _jobs(run_dir: Path, plan: Mapping[str, Any], report: Mapping[str, Any], cfg: Mapping[str, Any]) -> list[Job]:
    by_label = {j["label"]: j for j in report["jobs"]}
    jobs = []
    for s in plan["steps"]:
        j = by_label[f"step:{s['step']:02d}"]
        jobs.append(Job(j["label"], f"{s['step']:02d}", s["name"], j["path"], j["sha256"], s["file"], s["sql_sha256"],
                        False, None, s))
    sides = ["B"] + (["A"] if str(cfg.get("author_mode", "single")).startswith("second:") else [])
    for side in sides:
        lab = f"whole:{side.lower()}"
        if lab not in by_label:
            raise RefusedError(f"{side}.sql missing: the {side} deliverable cannot run")
        j = by_label[lab]
        src = run_dir / f"{side}.sql"
        jobs.append(Job(lab, f"whole_{side.lower()}", f"{side}_whole", j["path"], j["sha256"], f"{side}.sql",
                        sqltext.sha256_text(src.read_text(encoding="utf-8")), True, side, None))
    return jobs


def edit_guard(run_dir: Path, plan: Mapping[str, Any]) -> None:
    """Refuse a step whose SQL differs from plan.json (spec 1.5 edit_guard, 9.1 non-happy path)."""
    for s in plan["steps"]:
        p = run_dir / s["file"]
        if not p.is_file() or sqltext.sha256_text(p.read_text(encoding="utf-8")) != s["sql_sha256"]:
            raise RefusedError(f"edit guard: {s['file']} differs from plan.json; nothing ran")


def fix_as_of(run_dir: Path, pin_plan: Mapping[str, Any], attempt: int, now: dt.datetime) -> dt.datetime:
    """as_of_utc for this attempt: kept from the manifest, else the business as-of, else now - offset."""
    m = manifest.load(run_dir)
    if m and m.get("attempt") == attempt and (m.get("pin") or {}).get("as_of_utc"):
        return runctx.parse_utc(m["pin"]["as_of_utc"])
    if pin_plan.get("as_of_utc"):
        return runctx.parse_utc(pin_plan["as_of_utc"])
    return now - dt.timedelta(minutes=int(pin_plan.get("as_of_offset_minutes", 5)))


def deadline_of(as_of: dt.datetime, pin_plan: Mapping[str, Any]) -> dt.datetime | None:
    """as_of + smallest window - margin (spec 5.4); None when no window is known."""
    w = pin_plan.get("smallest_window_hours")
    if not w:
        return None
    return as_of + dt.timedelta(hours=int(w) - int(pin_plan.get("margin_hours", 24)))


def run(run_dir: Path, cfg: Mapping[str, Any], st: state.RunState, engine: Any, env: Mapping[str, Any],
        now_fn: Any = runctx.utc_now) -> int:
    """The P6 body; returns the exit code (raises GateNeeded / RefusedError / EngineError)."""
    frozen = freeze_check.frozen_sides(run_dir)  # B (and A, second-author mode) unchanged since P4
    plan = runctx.read_json(run_dir / "plan.json")
    if plan.get("source_sha256") != frozen["B"]:
        raise RefusedError("plan.json was split from another B (source_sha256 differs from the frozen B.sql): "
                           "nothing ran")
    edit_guard(run_dir, plan)
    pin_plan = comp.load_pin_plan(run_dir)
    as_of = fix_as_of(run_dir, pin_plan, st.attempt, now_fn())
    deadline = deadline_of(as_of, pin_plan)
    if deadline and now_fn() > deadline:
        raise RefusedError(f"pin deadline {runctx.fmt_utc(deadline)} passed: re-pin or continue as csv_only (G8)")
    comp_report = comp.compile_run(run_dir, as_of, provisional=False)
    jobs = _jobs(run_dir, plan, comp_report, cfg)
    engine = bq.metered(engine, run_dir, cfg, "P6", st.attempt)  # the one query path: every job on the ledger
    level = st.level
    ladder = _ladder(cfg)
    caps = ladder.caps
    labels_extra = cfg.get("job_labels") or {}
    placeholders = env.get("placeholders", {})
    dialect_name = plan.get("dialect", "bigquery")
    texts = {j.label: sqltext.render((run_dir / j.executed_path).read_text(encoding="utf-8"), placeholders, "bigquery")
             for j in jobs}
    cfgs = {j.label: bq.job_config(cfg["run_id"], j.bq_step, caps["warehouse_bytes_per_job"], False,
                                   env.get("location"), labels_extra) for j in jobs}
    # rows-in COUNT jobs for read steps that filter (one cheap pinned COUNT(*) each, B-H4)
    routes, views = comp.routes_of(pin_plan)
    allow = comp.load_allowlist()
    filtered: dict[str, bool] = {}
    count_label: dict[str, str] = {}
    for j in jobs:
        s = j.plan_step
        if s is None or not s["is_read_step"]:
            continue
        body = (run_dir / s["file"]).read_text(encoding="utf-8")
        filtered[j.name] = read_is_filtered(body, dialect_name)
        if filtered[j.name]:
            ctext, _ = comp.compile_sql(count_job_sql(body, dialect_name), comp.as_of_literal(as_of), routes, views,
                                        dialect_name, allow)
            lab = f"count:{j.bq_step}"
            count_label[j.name] = lab
            texts[lab] = sqltext.render(ctext, placeholders, "bigquery")
            cfgs[lab] = bq.job_config(cfg["run_id"], j.bq_step, caps["warehouse_bytes_per_job"], False,
                                      env.get("location"), labels_extra)
    # budget check before anything is billed
    dry = {}
    for lab in texts:
        dr = engine.dry_run(texts[lab], dataclasses.replace(cfgs[lab], dry_run=True))
        dry[lab] = dr
        if bq.check_dry_run_against_cap(dr, cfgs[lab]) != ExitCode.PASS:
            raise GateNeeded("G15", "dry-run estimate over the per-job cap",
                             {"job": lab, "estimate_bytes": dr.total_bytes_processed,
                              "cap": caps["warehouse_bytes_per_job"]})
    total_dry = sum(d.total_bytes_processed for d in dry.values())
    total_res = sum(bq.reservation(dry[lab].total_bytes_processed, texts[lab]) for lab in texts)
    try:
        engine.ledger.check(total_res, "P6 jobs (steps, whole B/A, rows-in COUNT jobs)",
                            {"jobs": len(texts), "dry_run_bytes_total": total_dry})
    except bq.BudgetExceeded as exc:
        raise GateNeeded("G15", str(exc), exc.details) from exc
    sentences, g11_ok = step_sentences(run_dir, st.attempt)
    decisions = runctx.load_decisions(run_dir)
    out_dir = run_dir / OUTPUTS
    out_dir.mkdir(exist_ok=True)
    checks: list[list[Any]] = []
    m = manifest.load(run_dir) or manifest.new_manifest(cfg, st.attempt)
    if m.get("attempt") != st.attempt:
        m = manifest.new_manifest(cfg, st.attempt)
    m["confidence_level"] = level
    m["pin"] = {**manifest.pin_block(pin_plan), "as_of_utc": runctx.fmt_utc(as_of),
                "pin_deadline_utc": runctx.fmt_utc(deadline) if deadline else None}
    m["steps"], m["deliverables"] = [], []
    rows_by_name: dict[str, int] = {}
    plans_by_name: dict[str, levels.StepPlan] = {}
    files: list[dict[str, Any]] = []
    job_log = []
    failed = False
    tol = cfg.get("float_tolerance")
    count_billed = 0
    for j in jobs:
        if deadline and now_fn() > deadline:
            raise RefusedError(f"pin deadline passed before {j.label}: re-pin or continue as csv_only (G8)")
        started = runctx.utc_now()
        t0 = time.monotonic()
        try:
            try:
                res = engine.run(texts[j.label], cfgs[j.label])
            except bq.CapExceeded as exc:
                raise GateNeeded("G15", "BigQuery refused the job over maximum_bytes_billed (nothing billed)",
                                 {"job": j.label, "cap": caps["warehouse_bytes_per_job"], "detail": str(exc)}) from exc
            sp = levels.plan_step(level, res.total_rows, res.result_bytes, j.deliverable, ladder=ladder)
            if sp.gate:
                raise GateNeeded(sp.gate, sp.reason, {"job": j.label, "rows": res.total_rows, "bytes": res.result_bytes})
            stem = f"{j.name}"
            written_at = runctx.utc_now()  # taken before the artifacts are written: created_basis generated (B3-3)
            art = _materialise(engine, res, j, sp, stem, out_dir, cfg, tol, caps, cfgs[j.label], texts[j.label], checks)
            counted = None
            if j.name in count_label:
                lab = count_label[j.name]
                cres = engine.run(texts[lab], cfgs[lab])
                count_billed += int(cres.total_bytes_billed or 0)
                counted = _first_value(engine, cres, "qt_rows")
        except bq.BudgetExceeded as exc:
            raise GateNeeded("G15", str(exc), exc.details) from exc
        elapsed = int((time.monotonic() - t0) * 1000)
        if res.cache_hit:
            checks.append([j.label, "cache_off", "FAIL", "cache_hit true although use_query_cache is false"])
            failed = True
        if j.plan_step is not None:
            want = list(j.plan_step.get("columns") or [])
            got = [c["name"] for c in art["columns"]]
            ok = not want or want == got
            checks.append([j.label, "header_names", "PASS" if ok else "FAIL",
                           "names match plan.json" if ok else f"plan {want} vs result {got}"])
            failed |= not ok
        failed |= art["failed"]
        rows_by_name[j.name] = res.total_rows
        job_block = {"job_id": res.job_id, "label": j.label, "dry_run_bytes": dry[j.label].total_bytes_processed,
                     "bytes_billed": res.total_bytes_billed + art.get("extra_bytes_billed", 0),
                     "maximum_bytes_billed": caps["warehouse_bytes_per_job"], "use_query_cache": False,
                     "cache_hit": res.cache_hit, "as_of_utc": runctx.fmt_utc(as_of),
                     "started_utc": runctx.fmt_utc(started), "elapsed_ms": elapsed}
        job_log.append({"label": j.label, "name": j.name, "dry_run_bytes": job_block["dry_run_bytes"],
                        "bytes_billed": job_block["bytes_billed"], "rows": res.total_rows,
                        "stream_route": art.get("stream_route"), "digest_route": art["multiset_hash"]["route"]})
        files += [manifest.file_entry(run_dir, f, k, cfg, generated_utc=written_at) for f, k in art["files"]]
        files.append(manifest.file_entry(run_dir, j.executed_path, "executed_sql", cfg))
        if j.deliverable:
            m["deliverables"].append({"side": j.side, "sql_path": j.sql_path, "sql_sha256": j.sql_sha256,
                                      "executed_sql_path": j.executed_path, "rows_out": res.total_rows,
                                      "rows_in_csv": art["rows_in_csv"], "columns": art["columns"],
                                      "multiset_hash": art["multiset_hash"], "csv": art["csv"], "job": job_block})
            continue
        s = j.plan_step
        plans_by_name[j.name] = sp
        inputs = [{"kind": i["kind"], "ref": i["ref"],
                   "rows_in": rows_by_name.get(i["ref"]) if i["kind"] == "prior_step" else
                   (counted if s["is_read_step"] and counted is not None else None)} for i in s["inputs"]]
        rows_in, basis = rows_in_of(s, rows_by_name, res.total_rows, filtered.get(j.name, False), counted)
        verb = "recursive" if str(s.get("kind", "")).endswith("recursive") else str(s.get("verb") or "")
        prior_rows = [rows_by_name.get(i["ref"]) for i in s.get("inputs", []) if i["kind"] == "prior_step"]
        rows_min = min(prior_rows) if prior_rows and all(v is not None for v in prior_rows) else None
        reason, explained = row_delta(verb, rows_in, res.total_rows, rows_min)
        source = "split_op"
        cid = f"p6_step_{s['step']:02d}_rows_in_out"
        rows_in_text = rows_in if rows_in is not None else "not recorded"
        fail_ev = check_evidence(j.label, "rows_in_out", "FAIL", f"{rows_in_text} -> {res.total_rows} ({reason}; {basis})",
                                 st.attempt)  # the evidence a G8 explanation of this FAIL binds to
        sentence = sentences.get(s["name"])
        announced = verb in ("join", "unnest") and g11_ok and bool(sentence) and bool(FAN_OUT_WORDS.search(sentence or ""))
        if not explained and announced and rows_in is not None:
            reason, source, explained = "join_fan_out", "g11_sentence", True
        elif explained and announced and verb == "join" and rows_in is not None and res.total_rows <= rows_in:
            reason, source = "join_mixed", "g11_sentence"  # a fan-out was announced, yet the count did not rise
        elif not explained and g8_explained(decisions, cid, fail_ev):
            reason = "join_fan_out" if verb in ("join", "unnest") and rows_in is not None else "unexplained"
            source = "max"
        checks.append([j.label, "rows_in_out", "PASS" if explained else "FAIL",
                       f"{rows_in if rows_in is not None else 'not recorded'} -> {res.total_rows} ({reason}; {basis})"])
        failed |= not explained
        m["steps"].append({
            "step": s["step"], "name": s["name"], "sql_path": s["file"], "sql_sha256": s["sql_sha256"],
            "executed_sql_path": j.executed_path, "executed_sql_sha256": j.executed_sha256, "engine": "bigquery",
            "inputs": inputs, "is_read_step": s["is_read_step"], "grain": list(s.get("grain") or []),
            "requested_level": sp.requested_level, "effective_level": sp.effective_level, "degraded": sp.degraded,
            "degraded_reason": sp.reason if sp.degraded else None, "forced_retention": False,
            "artifacts": list(sp.artifacts), "rows_out": res.total_rows, "rows_in_csv": art["rows_in_csv"],
            "rows_in": rows_in, "rows_in_basis": basis, "row_delta_reason": reason, "row_delta_source": source,
            **({"one_sentence": sentence} if sentence else {}),
            "columns": art["columns"], "schema_path": art["schema_path"],
            "multiset_hash": art["multiset_hash"], "csv": art["csv"], "aggregates_path": art.get("aggregates_path"),
            "sample_path": art.get("sample_path"), "strata_path": art.get("strata_path"), "job": job_block,
            "replayable": False, "replayable_reason": "", "replay_status": "NONE",
            "status": "degraded" if sp.degraded else ("failed" if art["failed"] else "ok"), "error": None})
    refs = [levels.StepRef(s["name"], s["is_read_step"], tuple(i["ref"] for i in s["inputs"] if i["kind"] == "prior_step"))
            for s in m["steps"]]
    rmap = levels.replay_map(refs, plans_by_name)
    for s in m["steps"]:
        s["replayable"], s["replayable_reason"] = rmap[s["name"]]
    billed = [x["bytes_billed"] for x in job_log]
    step_billed = sum(x["bytes_billed"] for x in job_log if x["label"].startswith("step:"))
    whole_b = next((x["bytes_billed"] for x in job_log if x["label"] == "whole:b"), None)
    m["budget"] = {"dry_run_bytes_total": total_dry, "reserved_bytes_total": total_res,
                   "warehouse_bytes_per_run": caps["warehouse_bytes_per_run"],
                   "warehouse_bytes_per_job": caps["warehouse_bytes_per_job"], "bytes_billed_total": sum(billed),
                   **engine.ledger.block()}
    for f in ("plan.json", "15_pin_plan.json", "executed/compile_report.json"):
        if (run_dir / f).is_file():
            files.append(manifest.file_entry(run_dir, f, "run_record", cfg))
    for s in plan["steps"]:
        files.append(manifest.file_entry(run_dir, s["file"], "step_sql", cfg))
    manifest.upsert_files(m, files)
    manifest.save(run_dir, m)
    rows6 = with_evidence(checks, st.attempt)
    sc_checks = runctx.write_rows_csv(run_dir / CHECKS / "run_steps_checks.csv", CHECK_COLS, rows6)
    runctx.replace_phase_checks(run_dir, "P6", p6_manifest_rows(rows6, sc_checks.csv_sha256))
    rep = {"schema_version": "1", "as_of_utc": runctx.fmt_utc(as_of), "level": level, "jobs": job_log,
           "dry_run_bytes_total": total_dry, "bytes_billed_total": sum(billed), "step_jobs_bytes_billed": step_billed,
           "whole_b_bytes_billed": whole_b, "count_jobs_bytes_billed": count_billed,
           "reserved_bytes_total": total_res, "run_spent_bytes": engine.ledger.spent,
           "run_budget_bytes": engine.ledger.budget,
           "n_steps": len(plan["steps"]),
           "chain_cost_ratio_vs_whole_b": round(step_billed / whole_b, 3) if whole_b else None,
           "note": "N x S measurement: step_jobs_bytes_billed vs whole_b_bytes_billed (spec 5.3, 9.1); BigQuery bills "
                   "at least 10 MiB per table referenced; bytes_billed_total covers the step and whole jobs, "
                   "count_jobs_bytes_billed the rows-in COUNT jobs; manifest.budget.ledger holds every job of "
                   "every phase"}
    runctx.write_json(run_dir / REPORT, rep)
    return int(ExitCode.FAIL if failed else ExitCode.PASS)


def _first_value(engine: Any, res: bq.JobResult, column: str) -> int:
    """The one value of a one-row result (the COUNT job), read through the stream."""
    for batch in engine.stream(res):
        rows = batch.to_pylist()
        if rows:
            return int(rows[0][column])
    raise CheckFailedError(f"{column}: the COUNT job returned no row")


def _materialise(engine: Any, res: bq.JobResult, j: Job, sp: levels.StepPlan, stem: str, out_dir: Path,
                 cfg: Mapping[str, Any], tol: float | None, caps: Mapping[str, int], jc: bq.JobConfig, text: str,
                 checks: list[list[Any]]) -> dict[str, Any]:
    """Write what the plan asks for; return the manifest pieces."""
    rel = lambda p: f"{OUTPUTS}/{p.name}"  # noqa: E731
    out: dict[str, Any] = {"files": [], "failed": False, "rows_in_csv": None, "csv": None}
    schema_path = out_dir / f"{stem}{cc.SIDECAR_SUFFIX}"
    if sp.digest_route == levels.load_ladder().digest_routes["warehouse"]:
        rows_w, digest, res2 = engine.warehouse_digest(text, jc)
        cols = [cc.Column(n, t) for n, t in res.schema]
        out["extra_bytes_billed"] = res2.total_bytes_billed
        ok = rows_w == res.total_rows
        checks.append([j.label, "rows_warehouse_digest_vs_count", "PASS" if ok else "FAIL", f"{rows_w} vs {res.total_rows}"])
        out["failed"] = not ok
        write_schema_only(schema_path, cols, res.total_rows)
        out["multiset_hash"] = {"digest": digest, "rows": rows_w, "route": sp.digest_route,
                                "float_tolerance": tol, "schema_fingerprint": canon.schema_fingerprint(cols)}
        out["columns"] = [{"name": c.name, "type": c.type} for c in cols]
        out["schema_path"] = rel(schema_path)
        out["files"].append((rel(schema_path), "schema_sidecar"))
        out["stream_route"] = "none (warehouse digest)"
        return out
    tmp = Path(tempfile.mkdtemp(prefix="qt_stream_", dir=str(out_dir.parent)))
    con = duckdb.connect(str(tmp / "stream.duckdb"))
    try:
        cc.run_sql(con, "SET TimeZone = 'UTC'")
        stream = engine.stream(res)
        n = load_stream(con, stream, res.schema, caps["stream_rows"], caps["stream_bytes"])
        out["stream_route"] = getattr(stream, "route", None)
        cols = cc.describe(con, "SELECT * FROM qt_step")
        ok = n == res.total_rows == int(cc.scalar(con, "SELECT COUNT(*) FROM qt_step"))
        checks.append([j.label, "rows_streamed_vs_count", "PASS" if ok else "FAIL", f"{n} streamed, COUNT(*) {res.total_rows}"])
        if not ok:
            raise CheckFailedError(f"{j.label}: {n} rows streamed, job COUNT(*) = {res.total_rows} (never accepted)")
        dg = canon.digest_query(con, "SELECT * FROM qt_step", tol, columns=cols)
        out["multiset_hash"] = dg.to_json()
        out["columns"] = [{"name": c.name, "type": c.type} for c in cols]
        if "full_csv" in sp.artifacts:
            csv_path = out_dir / f"{stem}.csv"
            sc = cc.write_query_csv(con, "SELECT * FROM qt_step", csv_path, expected_rows=res.total_rows)
            out["rows_in_csv"] = sc.rows
            out["csv"] = {"path": rel(csv_path), "sha256": sc.csv_sha256, "bytes": sc.csv_bytes, "mode": "full",
                          "schema_path": rel(schema_path)}
            out["files"] += [(rel(csv_path), "full_csv"), (rel(schema_path), "schema_sidecar")]
            check_con = duckdb.connect()
            try:
                csv_dg = canon.digest_csv(check_con, csv_path, tol)
            finally:
                check_con.close()
            same = csv_dg.digest == dg.digest and csv_dg.rows == dg.rows
            checks.append([j.label, "streamed_hash_vs_csv_hash", "PASS" if same else "FAIL", f"{dg.digest} vs {csv_dg.digest}"])
            out["failed"] |= not same
        else:
            write_schema_only(schema_path, cols, res.total_rows)
            out["files"].append((rel(schema_path), "schema_sidecar"))
        out["schema_path"] = rel(schema_path)
        lad = levels.load_ladder()
        if "aggregates" in sp.artifacts:
            ap = out_dir / f"{stem}.aggregates.csv"
            keys = list((j.plan_step or {}).get("grain") or [])
            mode = write_aggregates(con, cols, keys, ap, int(caps.get("aggregate_max_groups", lad.caps["aggregate_max_groups"])))
            out["aggregates_path"] = rel(ap)
            out["files"].append((rel(ap), "aggregates"))
            checks.append([j.label, "aggregates", "PASS", mode])
        if "sample_rows" in sp.artifacts:
            spath = out_dir / f"{stem}.sample.csv"
            k = write_sample(con, cols, sampling.default_salt(cfg),
                             int(caps.get("sample_rows_level_80", lad.caps["sample_rows_level_80"])), spath, tol)
            out["sample_path"] = rel(spath)
            out["files"].append((rel(spath), "sample_rows"))
            checks.append([j.label, "sample_rows", "PASS", f"{k} rows"])
        if "strata_rows" in sp.artifacts:
            tpath = out_dir / f"{stem}.strata.csv"
            k = write_strata(con, cols, int(caps.get("strata_rows_per_stratum_level_80",
                                                     lad.caps["strata_rows_per_stratum_level_80"])), tpath)
            out["strata_path"] = rel(tpath)
            out["files"].append((rel(tpath), "strata_rows"))
            checks.append([j.label, "strata_rows", "PASS", f"{k} rows"])
    finally:
        con.close()
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def main(argv: Sequence[str] | None = None, engine: Any = None, env: Mapping[str, Any] | None = None,
         now_fn: Any = None) -> int:
    ap = argparse.ArgumentParser(prog="run_steps.py", description="query-trail step runner (spec 2.2 P6)")
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--env-file", default=None)
    a = ap.parse_args(argv)

    def body() -> int:
        st = runctx.enter(a.run_dir, ("P6",))
        cfg = runctx.load_run_config(a.run_dir)
        runctx.require_g0a(cfg)
        e = dict(env) if env is not None else runctx.load_env(cfg, a.env_file)
        eng = engine or bq.BigQueryEngine(bq.Identity(e["project"], e["location"], e.get("service_account"),
                                                      cfg.get("owner_mode") == "research"))
        try:
            code = run(a.run_dir, cfg, st, eng, e, now_fn or runctx.utc_now)
        except GateNeeded as g:
            runctx.write_gate_request(a.run_dir, g.gate, "P6", "run_steps.py", g.reason, g.details)
            raise
        print(f"run_steps.py: {'PASS' if code == 0 else 'FAIL'}: manifest.json, {REPORT}")
        if code == 0:
            runctx.archive_own_request(a.run_dir, "run_steps.py", None, "P6")  # fix r4 C4-10
        return code

    return runctx.run_cli(lambda: refusal.guard(a.run_dir, body, warehouse=True), "run_steps.py")


if __name__ == "__main__":
    sys.exit(main())

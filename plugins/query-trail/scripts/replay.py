"""replay.py: local DuckDB replay of replayable steps from retained CSVs (design spec v1 sections 2.2 P6 and P11, 5.5; DB-05).

A step replays only when manifest.json says it is replayable (every input a retained full CSV; read steps
never locally). Each input CSV is opened only through the lib reader (DuckDB read_csv, sidecar types,
allow_quoted_nulls=false, sha256 checked), bound as a view under the input step's CTE name; the step's
BigQuery SQL is transpiled to DuckDB by lib.sqltext.transpile_to_duckdb (NUMERIC -> DECIMAL(38,9)
explicitly; BIGNUMERIC is never replayed locally, D-03) and run.

Replay status per step (spec 5.5):
- EXACT only when compat/binding.json allows exact replay (`exact_allowed: true`), its recorded versions and
  dialect flags match this environment, and every output type of the step is inside the binding's tested
  scope; then the replayed digest and schema fingerprint must equal the manifest's;
- TOLERANT otherwise: the replayed rows must equal the step's rows as a canonical multiset under the run's
  FLOAT tolerance (row count, then the digest; when the digests differ and the step's own CSV is retained,
  a typed EXCEPT ALL both ways with DOUBLE columns compared within the tolerance);
- NONE when the step is not replayable. A step whose SQL casts to BIGNUMERIC or whose own or input columns are
  BIGNUMERIC is never replayed locally (decisions 6, D-03; fix r1 A-M6): replay_status NONE, replayable false,
  reason "BIGNUMERIC: not replayed locally". That is not a failure; the p6_replay detail names the step.

Writes checks/replay_status.csv, updates manifest.json (replay_status, compat_binding). Exit 0 PASS, 1 a
replayed step does not match, 2 refused.

CLI: python -I -X utf8 -B scripts/replay.py --run-dir RUN [--binding compat/binding.json]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import duckdb  # noqa: E402

from lib import canon, manifest, runctx, sqltext  # noqa: E402
from lib import csv_contract as cc  # noqa: E402
from lib.exit_codes import ExitCode, RefusedError  # noqa: E402

BINDING_DEFAULT = runctx.PLUGIN_ROOT / "compat" / "binding.json"
COLS = ["step", "replayable", "replay_status", "match", "detail"]
FLOAT = ("DOUBLE", "FLOAT", "REAL")
BIGNUMERIC_REASON = "BIGNUMERIC: not replayed locally"
_BIG = ("BIGNUMERIC", "BIGDECIMAL")


def bignumeric_step(step: Mapping[str, Any], plan_step: Mapping[str, Any], by_name: Mapping[str, Any],
                    run_dir: Path) -> bool:
    """True when the step's SQL casts to BIGNUMERIC, or a column of the step or of its inputs is BIGNUMERIC."""
    cols = list(step.get("columns") or [])
    for i in step.get("inputs") or []:
        cols += list((by_name.get(i.get("ref")) or {}).get("columns") or [])
    if any(str(c.get("type", "")).split("(")[0].strip().upper() in _BIG for c in cols):
        return True
    try:
        sqltext.transpile_to_duckdb((run_dir / plan_step["file"]).read_text(encoding="utf-8"))
    except sqltext.BignumericError:
        return True
    except Exception:  # noqa: BLE001 - any other transpile problem is replay_step's to report
        return False
    return False


def load_binding(path: Path) -> dict[str, Any] | None:
    """The compatibility binding, or None when absent."""
    return runctx.read_json(path) if Path(path).is_file() else None


def binding_matches(binding: Mapping[str, Any] | None) -> tuple[bool, list[str]]:
    """(matches, mismatches): versions and dialect flags against this environment."""
    if not binding:
        return False, ["no binding"]
    cur = manifest.versions()
    if not binding.get("versions"):
        return False, ["binding records no versions"]
    bad = [f"{k}: bound {v} vs current {cur.get(k)}" for k, v in (binding.get("versions") or {}).items()
           if k != "client_version" and cur.get(k) != v]
    flags = binding.get("dialect_flags") or {}
    want = {"read": "bigquery, normalization_strategy = case_sensitive",
            "write": "duckdb, normalization_strategy = case_sensitive"}
    bad += [f"dialect {k}: bound {flags.get(k)}" for k in want if flags.get(k) != want[k]]
    return not bad, bad


def classify(step_types: Sequence[str], binding: Mapping[str, Any] | None, matches: bool) -> str:
    """EXACT when the binding allows it, matches, and covers every output type; TOLERANT otherwise."""
    if not (binding and binding.get("exact_allowed") is True and matches):
        return "TOLERANT"
    scope = {t.split("(")[0].upper() for t in ((binding.get("scope") or {}).get("types") or [])}
    return "EXACT" if all(t.split("(")[0].upper() in scope for t in step_types) else "TOLERANT"


def tolerant_equal(con: duckdb.DuckDBPyConnection, a: str, b: str, columns: Sequence[cc.Column],
                   tol: float | None) -> bool:
    """Typed EXCEPT ALL both ways; DOUBLE columns within the tolerance after pairing on the other columns."""
    q = cc.quote_ident
    cast_b = "SELECT " + ", ".join(f"CAST({q(c.name)} AS {c.type}) AS {q(c.name)}" for c in columns) + f" FROM ({b})"
    x = cc.scalar(con, f"SELECT COUNT(*) FROM (SELECT * FROM ({a}) EXCEPT ALL {cast_b})")
    y = cc.scalar(con, f"SELECT COUNT(*) FROM ({cast_b} EXCEPT ALL SELECT * FROM ({a}))")
    if x == y == 0:
        return True
    fl = [c.name for c in columns if c.type.split("(")[0].upper() in FLOAT]
    keys = [c.name for c in columns if c.name not in fl]
    if not fl:
        return False
    t = tol if tol else 1e-9
    part = ", ".join(q(k) for k in keys) or "1"
    order = ", ".join(f"{q(c)} NULLS LAST" for c in fl)
    ra = f"(SELECT *, row_number() OVER (PARTITION BY {part} ORDER BY {order}) AS _rn FROM ({a}))"
    rb = f"(SELECT *, row_number() OVER (PARTITION BY {part} ORDER BY {order}) AS _rn FROM ({cast_b}))"
    on = " AND ".join([f"x.{q(k)} IS NOT DISTINCT FROM y.{q(k)}" for k in keys] + ["x._rn = y._rn"])
    bad = " OR ".join(f"abs(x.{q(c)} - y.{q(c)}) > {t}" for c in fl)
    un = cc.scalar(con, f"SELECT COUNT(*) FROM {ra} x FULL JOIN {rb} y ON {on} WHERE x._rn IS NULL OR y._rn IS NULL")
    mm = cc.scalar(con, f"SELECT COUNT(*) FROM {ra} x JOIN {rb} y ON {on} WHERE {bad}")
    return un == 0 and mm == 0


def replay_step(run_dir: Path, step: Mapping[str, Any], plan_step: Mapping[str, Any], by_name: Mapping[str, Any],
                cte_of: Mapping[str, str], status: str, tol: float | None) -> tuple[bool, str]:
    """Run one replayable step on DuckDB from its retained inputs; (match, detail)."""
    con = duckdb.connect()
    try:
        cc.run_sql(con, "SET TimeZone = 'UTC'")
        for i in step["inputs"]:
            if i["kind"] != "prior_step":
                return False, f"input {i['ref']} is not a prior step"
            src = by_name[i["ref"]]
            if not src.get("csv"):
                return False, f"input {i['ref']} has no retained CSV"
            rel = cc.read_csv(con, run_dir / src["csv"]["path"], validate=True)
            rel.create_view(cte_of[i["ref"]], replace=True)
        bq_sql = (run_dir / plan_step["file"]).read_text(encoding="utf-8")
        try:
            dsql = sqltext.transpile_to_duckdb(bq_sql)
        except sqltext.BignumericError as exc:
            return False, f"not replayable locally: {exc}"
        cols = cc.describe(con, dsql)
        mh = step["multiset_hash"]
        if mh["route"] != canon.LOCAL_ROUTE:
            return False, f"step hash route {mh['route']} is not comparable locally"
        dg = canon.digest_query(con, dsql, tol, columns=cols)
        if status == "EXACT":
            ok = dg.digest == mh["digest"] and dg.rows == mh["rows"] and dg.schema_fingerprint == mh["schema_fingerprint"]
            return ok, "digest and schema equal" if ok else "EXACT replay differs (digest, rows or types)"
        if dg.rows != mh["rows"]:
            return False, f"rows {dg.rows} vs {mh['rows']}"
        if dg.digest == mh["digest"]:
            return True, "canonical multiset hash equal"
        if step.get("csv"):
            own = cc.read_csv(con, run_dir / step["csv"]["path"], validate=True)
            own_cols = cc.read_sidecar(run_dir / step["csv"]["path"]).columns
            own_sql = "SELECT * FROM " + cc.read_csv_sql(run_dir / step["csv"]["path"], own_cols)
            del own
            ok = tolerant_equal(con, own_sql, dsql, own_cols, tol)
            return ok, "typed multiset equal within the FLOAT tolerance" if ok else "replayed rows differ"
        return False, "digest differs and the step's own rows are not retained"
    finally:
        con.close()


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="replay.py", description="query-trail local replay (spec 5.5)")
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--binding", default=None)
    a = ap.parse_args(argv)

    def body() -> int:
        runctx.enter(a.run_dir, ("P6", "P11"))
        cfg = runctx.load_run_config(a.run_dir)
        runctx.require_g0a(cfg)
        m = manifest.load(a.run_dir)
        if not m or not m.get("steps"):
            raise RefusedError("manifest.json with steps is required (run_steps.py, P6)")
        plan = runctx.read_json(a.run_dir / "plan.json")
        pstep = {s["name"]: s for s in plan["steps"]}
        cte_of = {s["name"]: s["cte"] for s in plan["steps"]}
        by_name = {s["name"]: s for s in m["steps"]}
        bpath = Path(a.binding) if a.binding else BINDING_DEFAULT
        binding = load_binding(bpath)
        matches, mism = binding_matches(binding)
        tol = cfg.get("float_tolerance")
        rows, failed, big = [], False, []
        for s in m["steps"]:
            if s["replayable"] and bignumeric_step(s, pstep[s["name"]], by_name, a.run_dir):
                s["replayable"], s["replayable_reason"] = False, BIGNUMERIC_REASON
                big.append(s["name"])
            if not s["replayable"]:
                s["replay_status"] = "NONE"
                rows.append([s["name"], "false", "NONE", None, s["replayable_reason"]])
                continue
            status = classify([c["type"] for c in s["columns"]], binding, matches)
            ok, detail = replay_step(a.run_dir, s, pstep[s["name"]], by_name, cte_of, status, tol)
            s["replay_status"] = status
            failed |= not ok
            rows.append([s["name"], "true", status, "true" if ok else "false", detail])
        m["compat_binding"] = {"id": (binding or {}).get("id", "none"),
                               "path": str(bpath.relative_to(runctx.PLUGIN_ROOT)).replace("\\", "/")
                               if bpath.is_relative_to(runctx.PLUGIN_ROOT) else bpath.name,
                               "matches_environment": matches}
        manifest.save(a.run_dir, m)
        sc = runctx.write_rows_csv(a.run_dir / "checks" / "replay_status.csv", COLS, rows)
        n = sum(1 for r in rows if r[1] == "true")
        # one row in the checks table, so a replay FAIL reaches the verdict (spec 6.4; NOTES_stage2b item 6)
        bigtxt = f"; not replayed locally ({BIGNUMERIC_REASON.split(':')[0]}): {', '.join(big)}" if big else ""
        runctx.upsert_checks(a.run_dir, [runctx.check_row(
            "p6_replay", "P6", "replay", "NOT_RUN" if n == 0 else ("FAIL" if failed else "PASS"),
            "checks/replay_status.csv", sc.csv_sha256, f"{n} replayable step(s){bigtxt}; binding "
            f"{'matches' if matches else 'does not match'}",
            (("no step is replayable at this level" + bigtxt) if n == 0 else None))])
        print(f"replay.py: {'FAIL' if failed else 'PASS'}: {n} replayable steps; binding "
              f"{'matches' if matches else 'does not match: ' + '; '.join(mism)}")
        return int(ExitCode.FAIL if failed else ExitCode.PASS)

    return runctx.run_cli(body, "replay.py")


if __name__ == "__main__":
    sys.exit(main())

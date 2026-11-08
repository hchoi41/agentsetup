"""compat_suite.py: the engine-pair fixture suite and its compatibility binding (design spec v1 sections 1.4, 5.5; DB-05, D-03).

Each fixture is one small query in two dialects (DuckDB and GoogleSQL) over the categories the
spec names: types, value domains, decimal division result types, rounding, overflow,
collation, ordering. The DuckDB side runs now, locally; the BigQuery side is recorded "not run"
until stage 2a's runner executes the GoogleSQL text and its results are merged with
--bigquery-results (JSON {fixture_id: {"values": [...], "type": "..."} or {"error": "..."}}).

Each GoogleSQL fixture is also transpiled by sqlglot with the replay dialect flags and run on
DuckDB (`duckdb_transpiled`): that is the path a replayed step takes, so the BigQuery result is
compared with it. The binding compat/<binding>.json (and, unless --no-current, the copy
compat/binding.json that replay.py reads) records the client, DuckDB, sqlglot, pandas and Python
versions under replay.py's keys, the run date, location, dialect flags, the value domains tested,
`scope.types`, `exact_allowed` and each fixture's results. Replay may be EXACT only under a
binding whose BigQuery side ran, whose fixtures all matched, and whose recorded versions and
dialect flags equal the current environment (`replay_status_allowed`); any mismatch forces
TOLERANT until the suite re-runs. BIGNUMERIC and types beyond DuckDB DECIMAL(38) are always
TOLERANT (D-03), so their fixture is marked `always_tolerant`.

The suite runs per version set, not per run: --run-dir is optional; when given, the run must
be at P6 or P11 (the phases that read replay status).

CLI:  compat_suite.py [--run-dir R] [--out-dir compat] [--binding ID] [--location L]
                      [--bigquery-results F] [--no-current]
Exit: 0 binding written; 2 refused; 4 engine error.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.metadata as md
import platform
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

import duckdb  # noqa: E402

from lib import runctx as c2  # noqa: E402
from lib.exit_codes import ExitCode, QueryTrailError  # noqa: E402

CURRENT = "binding.json"
DIALECT_FLAGS = {"read": "bigquery, normalization_strategy = case_sensitive",
                 "write": "duckdb, normalization_strategy = case_sensitive", "duckdb_timezone": "UTC"}
TYPE_EQUIV = {"BIGINT": "INT64", "INTEGER": "INT64", "HUGEINT": None, "DOUBLE": "FLOAT64", "FLOAT": "FLOAT64",
              "VARCHAR": "STRING", "BOOLEAN": "BOOL", "DATE": "DATE", "TIMESTAMP": "DATETIME",
              "TIMESTAMP WITH TIME ZONE": "TIMESTAMP", "DECIMAL(38,9)": "NUMERIC", "BLOB": "BYTES"}

# (id, category, duckdb sql, googlesql, note); every query returns one column named v.
FIXTURES: tuple[tuple[str, str, str, str, str], ...] = (
    ("types_int64", "types", "SELECT CAST(42 AS BIGINT) AS v", "SELECT CAST(42 AS INT64) AS v", "INT64 maps to BIGINT"),
    ("types_numeric", "types", "SELECT CAST('1.123456789' AS DECIMAL(38,9)) AS v",
     "SELECT CAST('1.123456789' AS NUMERIC) AS v", "NUMERIC maps to DECIMAL(38,9)"),
    ("types_bignumeric", "types", "SELECT CAST('1.5' AS DOUBLE) AS v", "SELECT CAST('1.5' AS BIGNUMERIC) AS v",
     "BIGNUMERIC has no DuckDB equivalent beyond DECIMAL(38): always TOLERANT (D-03)"),
    ("types_timestamp_tz", "types", "SELECT TIMESTAMPTZ '2026-01-01 00:00:00.123456+00' AS v",
     "SELECT TIMESTAMP '2026-01-01 00:00:00.123456+00' AS v", "TIMESTAMP is UTC, microseconds"),
    ("types_datetime", "types", "SELECT TIMESTAMP '2026-01-01 12:34:56.123456' AS v",
     "SELECT DATETIME '2026-01-01 12:34:56.123456' AS v", "DATETIME maps to naive TIMESTAMP"),
    ("types_date_trunc", "types", "SELECT DATE_TRUNC('month', DATE '2026-03-15') AS v",
     "SELECT DATE_TRUNC(DATE '2026-03-15', MONTH) AS v", "DATE_TRUNC result type drift seen in T04"),
    ("types_empty_vs_null", "types", "SELECT CAST('' IS NULL AS VARCHAR) || '/' || CAST(NULL IS NULL AS VARCHAR) AS v",
     "SELECT CONCAT(CAST('' IS NULL AS STRING), '/', CAST(NULL IS NULL AS STRING)) AS v", "'' is not NULL"),
    ("domain_date_min_max", "value_domains", "SELECT CAST(DATE '0001-01-01' AS VARCHAR) || '|' || "
     "CAST(DATE '9999-12-31' AS VARCHAR) AS v", "SELECT CONCAT(CAST(DATE '0001-01-01' AS STRING), '|', "
     "CAST(DATE '9999-12-31' AS STRING)) AS v", "BigQuery DATE range 0001-01-01 to 9999-12-31"),
    ("domain_numeric_max", "value_domains", "SELECT CAST('99999999999999999999999999999.999999999' AS DECIMAL(38,9)) AS v",
     "SELECT CAST('99999999999999999999999999999.999999999' AS NUMERIC) AS v", "NUMERIC maximum"),
    ("domain_hangul_length", "value_domains", "SELECT LENGTH('가나다') AS v", "SELECT LENGTH('가나다') AS v",
     "character length of Hangul"),
    ("division_int", "decimal_division", "SELECT 7 / 2 AS v", "SELECT 7 / 2 AS v", "integer / integer"),
    ("division_int_floor", "decimal_division", "SELECT 7 // 2 AS v", "SELECT DIV(7, 2) AS v", "integer division"),
    ("division_decimal", "decimal_division", "SELECT CAST(1 AS DECIMAL(38,9)) / CAST(3 AS DECIMAL(38,9)) AS v",
     "SELECT CAST(1 AS NUMERIC) / CAST(3 AS NUMERIC) AS v", "NUMERIC / NUMERIC result type"),
    ("division_by_zero_nullif", "decimal_division", "SELECT 1 / NULLIF(0, 0) AS v", "SELECT 1 / NULLIF(0, 0) AS v",
     "NULLIF guard gives NULL"),
    ("rounding_half", "rounding", "SELECT ROUND(2.5) AS v", "SELECT ROUND(2.5) AS v", "half away from zero?"),
    ("rounding_half_negative", "rounding", "SELECT ROUND(-2.5) AS v", "SELECT ROUND(-2.5) AS v", "negative half"),
    ("rounding_numeric_scale", "rounding", "SELECT ROUND(CAST('1.005' AS DECIMAL(38,9)), 2) AS v",
     "SELECT ROUND(CAST('1.005' AS NUMERIC), 2) AS v", "decimal rounding at scale 2"),
    ("rounding_cast_int", "rounding", "SELECT CAST(2.5 AS BIGINT) AS v", "SELECT CAST(2.5 AS INT64) AS v",
     "cast of a half to integer"),
    ("rounding_float_sum", "rounding", "SELECT 0.1::DOUBLE + 0.2::DOUBLE AS v", "SELECT CAST(0.1 AS FLOAT64) + "
     "CAST(0.2 AS FLOAT64) AS v", "binary floating point"),
    ("overflow_int64_add", "overflow", "SELECT CAST(9223372036854775807 AS BIGINT) + 1 AS v",
     "SELECT CAST(9223372036854775807 AS INT64) + 1 AS v", "INT64 overflow is an error"),
    ("overflow_sum_int64", "overflow", "SELECT SUM(x) AS v FROM (VALUES (9223372036854775807::BIGINT), (1::BIGINT)) t(x)",
     "SELECT SUM(x) AS v FROM UNNEST([9223372036854775807, 1]) AS x", "SUM(INT64) overflow: DuckDB widens"),
    ("overflow_cast_string", "overflow", "SELECT CAST('1.5' AS BIGINT) AS v", "SELECT CAST('1.5' AS INT64) AS v",
     "string with a fraction cast to integer"),
    ("collation_case", "collation", "SELECT CAST('a' < 'B' AS VARCHAR) AS v", "SELECT CAST('a' < 'B' AS STRING) AS v",
     "binary (code point) comparison"),
    ("collation_order", "collation", "SELECT string_agg(s, ',' ORDER BY s) AS v FROM (VALUES ('b'), ('B'), ('_'), ('é'), "
     "('a')) t(s)", "SELECT STRING_AGG(s, ',' ORDER BY s) AS v FROM UNNEST(['b', 'B', '_', 'é', 'a']) AS s",
     "default string order"),
    ("ordering_nulls_asc", "ordering", "SELECT string_agg(COALESCE(CAST(x AS VARCHAR), 'NULL'), ',' ORDER BY x) AS v "
     "FROM (VALUES (2), (NULL), (1)) t(x)", "SELECT STRING_AGG(COALESCE(CAST(x AS STRING), 'NULL'), ',' ORDER BY x) AS v "
     "FROM UNNEST([2, NULL, 1]) AS x", "default NULL placement in ascending order"),
    ("ordering_nulls_desc", "ordering", "SELECT string_agg(COALESCE(CAST(x AS VARCHAR), 'NULL'), ',' ORDER BY x DESC) AS v "
     "FROM (VALUES (2), (NULL), (1)) t(x)", "SELECT STRING_AGG(COALESCE(CAST(x AS STRING), 'NULL'), ',' ORDER BY x DESC) "
     "AS v FROM UNNEST([2, NULL, 1]) AS x", "default NULL placement in descending order"),
)
ALWAYS_TOLERANT = {"types_bignumeric"}


def _version(pkg: str) -> str | None:
    try:
        return md.version(pkg)
    except md.PackageNotFoundError:
        return None


def current_versions() -> dict[str, str | None]:
    """The versions the binding is tied to (DB-05), keyed as replay.py and the manifest engine block key them."""
    return {"duckdb_version": _version("duckdb"), "sqlglot_version": _version("sqlglot"),
            "pandas_version": _version("pandas"), "python_version": platform.python_version(),
            "client_version": _version("google-cloud-bigquery") or "missing"}


def extra_versions() -> dict[str, str | None]:
    """Further package versions, recorded but not matched."""
    return {p: _version(p) for p in ("pyarrow", "google-cloud-bigquery-storage")}


def transpile(bq_sql: str) -> str:
    """GoogleSQL to DuckDB with the case-sensitive dialects replay uses (DIALECT_FLAGS)."""
    import sqlglot

    read = sqlglot.Dialect.get_or_raise(DIALECT_FLAGS["read"])
    write = sqlglot.Dialect.get_or_raise(DIALECT_FLAGS["write"])
    return sqlglot.transpile(bq_sql, read=read, write=write)[0]


def run_duckdb(sql: str) -> dict[str, Any]:
    """Run one fixture on a fresh DuckDB connection; values as text, plus the result type."""
    con = duckdb.connect()
    try:
        con.execute("SET TimeZone = 'UTC'")
        con.execute("SET threads = 1")
        rows = con.execute(f"SELECT CAST(v AS VARCHAR), typeof(v) FROM ({sql}) AS qt_f").fetchall()
        return {"values": [r[0] for r in rows], "type": rows[0][1] if rows else None}
    except duckdb.Error as exc:
        return {"error": f"{type(exc).__name__}: {str(exc).splitlines()[0]}"}
    finally:
        con.close()


def compare(duck: Mapping[str, Any], bq: Mapping[str, Any] | None) -> bool | None:
    """Fixture match: both errored, or equal values and an equivalent type; None when BigQuery did not run."""
    if bq is None:
        return None
    if "error" in duck or "error" in bq:
        return "error" in duck and "error" in bq
    dtype = str(duck["type"])
    return [str(v) for v in duck["values"]] == [str(v) for v in bq.get("values", [])] and \
        str(bq.get("type")) in {str(TYPE_EQUIV.get(dtype, dtype)), dtype}


def build_binding(binding_id: str, location: str | None, bq_results: Mapping[str, Any] | None) -> dict[str, Any]:
    """Run the DuckDB sides of every fixture and assemble the binding object.

    Two DuckDB results per fixture: the hand-written DuckDB query (`duckdb`) and the GoogleSQL text
    transpiled by sqlglot with the replay dialect flags (`duckdb_transpiled`), which is the path a
    replayed step takes; the BigQuery result is compared with the transpiled one.
    """
    fixtures = []
    for fid, cat, dsql, bsql, note in FIXTURES:
        duck = run_duckdb(dsql)
        try:
            tsql = transpile(bsql)
            tran = run_duckdb(tsql)
        except Exception as exc:  # sqlglot raises several error types; any failure is a recorded result
            tsql, tran = None, {"error": f"transpile: {type(exc).__name__}: {str(exc).splitlines()[0]}"}
        bq = (bq_results or {}).get(fid)
        fixtures.append({"id": fid, "category": cat, "note": note, "duckdb_sql": dsql, "bigquery_sql": bsql,
                         "transpiled_sql": tsql, "duckdb": duck, "duckdb_transpiled": tran,
                         "bigquery": bq if bq is not None else {"status": "not run"},
                         "match": compare(tran, bq), "always_tolerant": fid in ALWAYS_TOLERANT})
    bq_ran = bq_results is not None and all(f["id"] in bq_results for f in fixtures)
    binding = {"schema_version": "1", "id": binding_id, "binding_id": binding_id, "created_utc": c2.utc_now_text(),
               "run_date": dt.date.today().isoformat(), "location": location, "versions": current_versions(),
               "versions_extra": extra_versions(), "dialect_flags": dict(DIALECT_FLAGS),
               "categories": sorted({f["category"] for f in fixtures}),
               "bigquery_side": "run" if bq_ran else "not run", "fixtures": fixtures,
               "tested_scope": "the fixtures above only; anything outside them replays TOLERANT"}
    matched_types = sorted({str(f["duckdb_transpiled"].get("type")).split("(")[0] for f in fixtures
                            if f["match"] is True and f["category"] in ("types", "value_domains")
                            and not f["always_tolerant"] and f["duckdb_transpiled"].get("type")})
    binding["scope"] = {"types": matched_types, "functions": [],
                        "value_domains": [f["id"] for f in fixtures if f["category"] == "value_domains"]}
    status, why = replay_status_allowed(binding)
    binding["exact_allowed"] = status == "EXACT"
    binding["status"] = "bound" if status == "EXACT" else f"tolerant: {why}"
    return binding


def matches_environment(binding: Mapping[str, Any], current: Mapping[str, Any] | None = None) -> tuple[bool, list[str]]:
    """Recorded versions equal the current environment (any mismatch forces TOLERANT, spec 5.5)."""
    cur = dict(current or current_versions())
    diffs = [f"{k}: binding {v!r}, now {cur.get(k)!r}" for k, v in binding.get("versions", {}).items() if cur.get(k) != v]
    return not diffs, diffs


def replay_status_allowed(binding: Mapping[str, Any] | None, current: Mapping[str, Any] | None = None) -> tuple[str, str]:
    """EXACT only when the BigQuery side ran, every fixture matched and the environment matches."""
    if binding is None:
        return "TOLERANT", "no compatibility binding"
    if binding.get("bigquery_side") != "run":
        return "TOLERANT", "binding incomplete: BigQuery side not run"
    bad = [f["id"] for f in binding["fixtures"] if f.get("match") is not True and not f.get("always_tolerant")]
    if bad:
        return "TOLERANT", "fixtures differ between engines: " + ", ".join(bad)
    ok, diffs = matches_environment(binding, current)
    if not ok:
        return "TOLERANT", "version mismatch: " + "; ".join(diffs)
    if dict(binding.get("dialect_flags") or {}) != DIALECT_FLAGS:
        return "TOLERANT", "dialect flags differ from the replay path"
    return "EXACT", "matching binding inside its tested scope (BIGNUMERIC and wider types stay TOLERANT)"


def _main(a: argparse.Namespace) -> int:
    if a.run_dir is not None:
        c2.phase_check(a.run_dir, ["P6", "P11"])
    v = current_versions()
    bid = a.binding or f"duckdb-{v.get('duckdb')}_sqlglot-{v.get('sqlglot')}_{dt.date.today():%Y%m%d}"
    bq = c2.read_input_json(Path(a.bigquery_results), "BigQuery fixture results") if a.bigquery_results else None
    binding = build_binding(bid, a.location, bq)
    out_dir = Path(a.out_dir) if a.out_dir else c2.PLUGIN_ROOT / "compat"
    c2.write_json(out_dir / f"{bid}.json", binding)
    if not a.no_current:
        c2.write_json(out_dir / CURRENT, binding)
    status, why = replay_status_allowed(binding)
    print(f"binding {bid}: BigQuery side {binding['bigquery_side']}; replay allowed: {status} ({why})"
          + ("" if a.no_current else f"; also written as {CURRENT}"))
    return int(ExitCode.PASS)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point (--run-dir optional: the suite runs per version set)."""
    ap = argparse.ArgumentParser(prog="compat_suite.py", description="engine-pair fixture suite (spec 5.5)")
    ap.add_argument("--run-dir", type=Path, help="optional run folder (phase P6 or P11)")
    ap.add_argument("--out-dir", help="output folder (default: <plugin>/compat)")
    ap.add_argument("--binding", help="binding id (default from versions and date)")
    ap.add_argument("--location", help="BigQuery location the BigQuery side ran in")
    ap.add_argument("--bigquery-results", help="JSON of BigQuery fixture results to merge")
    ap.add_argument("--no-current", action="store_true", help=f"do not also write {CURRENT} (the binding replay.py reads)")
    try:
        return c2.run_cli_args(_main, ap, argv)
    except QueryTrailError as exc:  # pragma: no cover - run_cli maps these already
        return int(exc.exit_code)


if __name__ == "__main__":
    sys.exit(main())

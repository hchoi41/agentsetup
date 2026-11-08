"""contract_check.py: a SQL's dry-run schema against 03_output_contract.json (design spec v1 sections 2.2 P4 and P6, 6.1 step 1; DB-02).

Checks, in order: column names (byte-exact, case-sensitive, D-09), column order, type family per column
(and decimal scale when both sides state one), and that every declared grain/key column is an output
column. The dry run is free (bytes are estimated, nothing is billed). Types are compared by family,
because BigQuery TIMESTAMP is tz-aware and DuckDB TIMESTAMP is not: contract types are BigQuery names;
a DuckDB schema (local replay, test engine) is mapped with its own rules.

Contract file (assumed shape; P2 writes it): {"columns": [{"name", "type", "scale"?}], "keys": [...],
"grain": [...]}. Output: checks/contract_<target>.csv and .json. Exit 0 PASS, 1 mismatch, 2 when no dry run
is possible (no engine, no credentials, the SQL is refused by BigQuery) or the contract is missing.

CLI: python -I -X utf8 -B scripts/contract_check.py --run-dir RUN [--sql B.sql] [--target B]
     [--schema-json saved_schema.json] [--env-file F]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import freeze_check, runctx, sqltext  # noqa: E402
from lib.exit_codes import ExitCode, QueryTrailError, RefusedError  # noqa: E402

CONTRACT = "03_output_contract.json"
_BQ = {"INT64": "INT64", "INTEGER": "INT64", "INT": "INT64", "SMALLINT": "INT64", "BIGINT": "INT64",
       "TINYINT": "INT64", "BYTEINT": "INT64", "FLOAT64": "FLOAT64", "FLOAT": "FLOAT64", "NUMERIC": "NUMERIC",
       "DECIMAL": "NUMERIC", "BIGNUMERIC": "BIGNUMERIC", "BIGDECIMAL": "BIGNUMERIC", "STRING": "STRING",
       "BOOL": "BOOL", "BOOLEAN": "BOOL", "BYTES": "BYTES", "DATE": "DATE", "DATETIME": "DATETIME",
       "TIME": "TIME", "TIMESTAMP": "TIMESTAMP", "JSON": "JSON", "GEOGRAPHY": "GEOGRAPHY", "INTERVAL": "INTERVAL"}
_DUCK = {"TINYINT": "INT64", "SMALLINT": "INT64", "INTEGER": "INT64", "INT": "INT64", "BIGINT": "INT64",
         "HUGEINT": "INT64", "UTINYINT": "INT64", "USMALLINT": "INT64", "UINTEGER": "INT64", "UBIGINT": "INT64",
         "DOUBLE": "FLOAT64", "FLOAT": "FLOAT64", "REAL": "FLOAT64", "DECIMAL": "NUMERIC", "NUMERIC": "NUMERIC",
         "VARCHAR": "STRING", "TEXT": "STRING", "BOOLEAN": "BOOL", "BLOB": "BYTES", "DATE": "DATE",
         "TIMESTAMP": "DATETIME", "TIMESTAMP_US": "DATETIME", "TIMESTAMP_NS": "DATETIME", "TIMESTAMP_MS": "DATETIME",
         "TIMESTAMP_S": "DATETIME", "TIMESTAMP WITH TIME ZONE": "TIMESTAMP", "TIMESTAMPTZ": "TIMESTAMP",
         "TIME": "TIME", "JSON": "JSON", "INTERVAL": "INTERVAL", "UUID": "STRING"}
_TYPE = re.compile(r"^\s*([A-Za-z_ ]+?)\s*(?:\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\))?\s*$")


def type_family(type_text: str, engine: str = "bigquery") -> tuple[str, int | None]:
    """(family, scale) of a type; nested types map to ARRAY or STRUCT; unknown types keep their name."""
    t = str(type_text).strip()
    up = t.upper()
    if up.startswith(("ARRAY", "LIST")) or up.endswith("[]"):
        return "ARRAY", None
    if up.startswith(("STRUCT", "RECORD", "MAP", "UNION")):
        return "STRUCT", None
    m = _TYPE.match(t)
    if not m:
        return up, None
    base = " ".join(m.group(1).upper().split())
    table = _BQ if engine == "bigquery" else _DUCK
    fam = table.get(base, base)
    scale = int(m.group(3)) if m.group(3) else (0 if m.group(2) and fam == "NUMERIC" else None)
    return fam, scale


def load_contract(run_dir: Path) -> dict[str, Any]:
    """03_output_contract.json; refuse when missing or malformed."""
    p = run_dir / CONTRACT
    if not p.is_file():
        raise RefusedError(f"{p} missing: the output contract is fixed at G1b (P2)")
    c = runctx.read_json(p)
    if not isinstance(c.get("columns"), list) or not all("name" in x and "type" in x for x in c["columns"]):
        raise RefusedError(f"{p}: 'columns' must list objects with name and type")
    return c


def compare(schema: Sequence[tuple[str, str]], contract: Mapping[str, Any], engine: str = "bigquery") -> list[list[Any]]:
    """Rows [check, column, expected, actual, status] for every check; status PASS or FAIL."""
    rows: list[list[Any]] = []
    want = [(c["name"], c["type"], c.get("scale")) for c in contract["columns"]]
    got_names = [n for n, _ in schema]
    want_names = [n for n, _, _ in want]
    rows.append(["names", "*", ",".join(want_names), ",".join(got_names),
                 "PASS" if sorted(want_names) == sorted(got_names) else "FAIL"])
    rows.append(["order", "*", ",".join(want_names), ",".join(got_names),
                 "PASS" if want_names == got_names else "FAIL"])
    got = dict(schema)
    for name, typ, scale in want:
        if name not in got:
            rows.append(["type", name, typ, None, "FAIL"])
            continue
        wf, ws = type_family(typ, "bigquery")
        gf, gs = type_family(got[name], engine)
        ws = int(scale) if scale is not None else ws
        ok = wf == gf and (ws is None or gs is None or ws == gs)
        rows.append(["type", name, f"{wf}" + (f"(scale {ws})" if ws is not None else ""),
                     f"{gf}" + (f"(scale {gs})" if gs is not None else "") + f" [{got[name]}]", "PASS" if ok else "FAIL"])
    for kind in ("grain", "keys"):
        for k in contract.get(kind) or []:
            rows.append([kind, k, "present", "present" if k in got else "missing", "PASS" if k in got else "FAIL"])
    return rows


def dry_run_schema(engine: Any, sql: str, cfg: Mapping[str, Any], env: Mapping[str, Any] | None) -> tuple[tuple[str, str], ...]:
    """Dry-run schema of rendered SQL; any failure means no dry run is possible (exit 2)."""
    from lib import bq

    try:
        rendered = sqltext.render(sql, (env or {}).get("placeholders", {}), "bigquery") if sqltext.placeholders_in(sql) \
            else sql
        jc = bq.job_config(cfg["run_id"], "contract", cfg["caps"]["warehouse_bytes_per_job"], True,
                           (env or {}).get("location"), cfg.get("job_labels"))
        return tuple(engine.dry_run(rendered, jc).schema)
    except QueryTrailError as exc:
        raise RefusedError(f"no dry run possible: {exc}") from exc


def main(argv: Sequence[str] | None = None, engine: Any = None) -> int:
    ap = argparse.ArgumentParser(prog="contract_check.py", description="query-trail output contract check (spec 2.2 P4/P6)")
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--sql", default="B.sql")
    ap.add_argument("--target", default=None, help="label in the report file name (default: the SQL file stem)")
    ap.add_argument("--schema-json", default=None, help="a saved schema [[name, type], ...] instead of a dry run")
    ap.add_argument("--schema-engine", default="bigquery", choices=("bigquery", "duckdb"))
    ap.add_argument("--env-file", default=None)
    a = ap.parse_args(argv)

    def body() -> int:
        st = runctx.enter(a.run_dir, ("P4", "P6"))
        cfg = runctx.load_run_config(a.run_dir)
        runctx.require_g0a(cfg)
        contract = load_contract(a.run_dir)
        target = a.target or Path(a.sql).stem
        type_engine = a.schema_engine
        if a.schema_json:
            schema = tuple(tuple(x) for x in runctx.read_json(a.run_dir / a.schema_json))
        else:
            p = a.run_dir / a.sql
            if not p.is_file():
                raise RefusedError(f"{p} does not exist")
            side = next((s for s in freeze_check.SIDES if p.resolve() == (a.run_dir / f"{s}.sql").resolve()), None)
            if side and st.phase == "P4":
                freeze_check.require_frozen_if_recorded(a.run_dir, side)  # pre-freeze loop: the draft
            elif side:
                freeze_check.require_frozen(a.run_dir, side)
            eng, env = engine, None
            if eng is None:
                from lib import bq
                try:
                    env = runctx.load_env(cfg, a.env_file)
                    eng = bq.BigQueryEngine(bq.Identity(env["project"], env["location"], env.get("service_account"),
                                                        cfg.get("owner_mode") == "research"))
                except QueryTrailError as exc:
                    raise RefusedError(f"no dry run possible: {exc}") from exc
            elif a.env_file:
                env = runctx.load_env(cfg, a.env_file)
            if getattr(eng, "name", "bigquery") != "bigquery":
                type_engine = "duckdb"
            schema = dry_run_schema(eng, p.read_text(encoding="utf-8"), cfg, env)
        rows = compare(schema, contract, type_engine)
        cols = ["check", "column", "expected", "actual", "status"]
        runctx.write_rows_csv(a.run_dir / "checks" / f"contract_{target}.csv", cols, rows)
        failed = [r for r in rows if r[4] == "FAIL"]
        runctx.write_json(a.run_dir / "checks" / f"contract_{target}.json",
                          {"target": target, "status": "FAIL" if failed else "PASS", "n_fail": len(failed),
                           "schema": [list(x) for x in schema], "checked_utc": runctx.fmt_utc(runctx.utc_now())})
        print(f"contract_check.py: {'FAIL' if failed else 'PASS'}: {target}: {len(failed)} of {len(rows)} checks failed")
        return int(ExitCode.FAIL if failed else ExitCode.PASS)

    return runctx.run_cli(body, "contract_check.py")


if __name__ == "__main__":
    sys.exit(main())

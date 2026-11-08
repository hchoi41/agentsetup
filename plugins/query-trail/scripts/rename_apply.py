"""rename_apply.py: apply Max's accepted renames to copies, with the lossless check (design spec v1 sections 1.4, 2.2 P10; DB-10).

Adapted from research_r2/T08_rename/rename_step.py (effective_plan, validate_plan, quote/fold,
build_select, lossless_check). Rules:

- Only renames.csv rows stamped by the script after Max answered count: decision = rename,
  accepted_by and accepted_at set, proposed differs from original (T08 effective_plan).
- The rename is a header bijection (DB-10): every original maps once, to a name no other
  column takes (BigQuery case folding), no reserved prefix (_TABLE_, _PARTITION, ...), no
  backtick, edge space or empty name, at most 300 characters; the inverse map restores the
  original names.
- It is applied to copies only: <stem>_renamed.csv for each retained step CSV and the
  deliverable CSV (DuckDB SELECT ... AS ..., written by the lib writer), and <stem>_renamed.sql
  for each step and whole SQL (an explicit, always-quoted outer SELECT; BigQuery SELECT has no
  RENAME). Originals are never opened for writing.
- Lossless check per CSV: the value multiset hash (lib.canon, name-blind) and the row count are
  unchanged, types are unchanged position by position, names equal the plan applied to the
  originals, and the inverse rename gives back the original names and the original hash.
A lossy map exits 1 and every renamed file this run wrote is removed; rename_report.json says why.
renames.csv `step` is the manifest step name, or B / A for a whole query's deliverable.

CLI:  rename_apply.py --run-dir R
Exit: 0 renamed (or skipped by Max); 1 lossy map; 2 refused.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

import duckdb  # noqa: E402

from lib import runctx as c2  # noqa: E402
from lib import canon  # noqa: E402
from lib import freeze_check  # noqa: E402
from lib import csv_contract as cc  # noqa: E402
from lib.exit_codes import CheckFailedError, ExitCode, RefusedError  # noqa: E402

RENAMES = "renames.csv"
REPORT = "rename_report.json"
BQ_RESERVED_PREFIXES = ("_TABLE_", "_FILE_", "_PARTITION", "_ROW_TIMESTAMP", "__ROOT__", "_COLIDENTIFIER",
                        "_CHANGE_SEQUENCE_NUMBER", "_CHANGE_TYPE", "_CHANGE_TIMESTAMP")
MAX_LEN = 300


def bq_quote(name: str) -> str:
    """Always-quote policy for GoogleSQL identifiers (T08)."""
    return "`" + name.replace("\\", "\\\\").replace("`", "\\`") + "`"


def fold(name: str) -> str:
    """BigQuery column names are case-insensitive: two names collide when they casefold equal."""
    return name.casefold()


def effective_plans(rows: Sequence[Mapping[str, Any]]) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Per step {original: proposed} from stamped rename rows, plus double-mapping errors."""
    plans: dict[str, dict[str, str]] = {}
    errs = []
    for r in rows:
        if str(r.get("decision")) != "rename" or not r.get("accepted_by") or not r.get("accepted_at"):
            continue
        o, p, step = str(r["original"]), str(r.get("proposed") or ""), str(r["step"])
        if not p or p == o:
            continue
        cur = plans.setdefault(step, {})
        if o in cur and cur[o] != p:
            errs.append(f"{step}: original {o!r} maps twice ({cur[o]!r} and {p!r})")
        cur[o] = p
    return plans, errs


def validate_plan(source_cols: Sequence[str], plan: Mapping[str, str]) -> list[str]:
    """Header bijection checks (T08 validate_plan, BigQuery rules)."""
    errs = []
    for o in plan:
        if o not in source_cols:
            ci = [c for c in source_cols if fold(c) == fold(o)]
            errs.append(f"UNKNOWN_ORIGINAL {o!r}" + (f" (case-insensitive match: {ci})" if ci else ""))
    for p in plan.values():
        if not p.strip() or p != p.strip():
            errs.append(f"EMPTY_OR_EDGE_SPACE {p!r}")
        if "`" in p:
            errs.append(f"BACKTICK {p!r}")
        if p.upper().startswith(BQ_RESERVED_PREFIXES):
            errs.append(f"BQ_RESERVED_PREFIX {p!r}")
        if len(p) > MAX_LEN:
            errs.append(f"TOO_LONG {p[:40]!r}...")
    final = [plan.get(c, c) for c in source_cols]
    groups: dict[str, list[str]] = {}
    for n in final:
        groups.setdefault(fold(n), []).append(n)
    errs += [f"COLLISION {v}" for v in groups.values() if len(v) > 1]
    inverse = {v: k for k, v in plan.items()}
    if len(inverse) != len(plan) or [inverse.get(n, n) for n in final] != list(source_cols):
        errs.append("ROUND_TRIP: the inverse map does not restore the original names")
    return errs


def renamed_path(p: Path) -> Path:
    """<dir>/<stem>_renamed<suffix>."""
    return p.with_name(p.stem + "_renamed" + p.suffix)


def rename_sql(sql: str, columns: Sequence[str], plan: Mapping[str, str]) -> str:
    """Wrap the original statement in an explicit, always-quoted renaming SELECT (T08 build_select)."""
    body = sql.strip().rstrip(";").rstrip()
    items = [f"{bq_quote(c)} AS {bq_quote(plan[c])}" if c in plan else bq_quote(c) for c in columns]
    return "SELECT\n  " + ",\n  ".join(items) + "\nFROM (\n" + body + "\n) AS qt_renamed\n"


def _select(columns: Sequence[cc.Column], plan: Mapping[str, str]) -> str:
    return ", ".join(f"{cc.quote_ident(c.name)} AS {cc.quote_ident(plan.get(c.name, c.name))}" for c in columns)


def rename_csv(src: Path, dst: Path, plan: Mapping[str, str]) -> dict[str, Any]:
    """Write the renamed copy and run the lossless check; return the per-file report."""
    if src.resolve() == dst.resolve():
        raise RefusedError(f"refusing to write over the original {src}")
    sc = cc.read_sidecar(src)
    con = duckdb.connect()
    try:
        cc.read_csv(con, src)
        rel = "SELECT " + _select(sc.columns, plan) + " FROM " + cc.read_csv_sql(src, sc.columns)
        cc.write_query_csv(con, rel, dst)
        before = canon.digest_csv(con, src)
        after = canon.digest_csv(con, dst)
        dsc = cc.read_sidecar(dst)
        inverse = {v: k for k, v in plan.items()}
        back_sql = "SELECT " + _select(dsc.columns, inverse) + " FROM " + cc.read_csv_sql(dst, dsc.columns)
        back = canon.digest_query(con, back_sql)
    finally:
        con.close()
    names_ok = [c.name for c in dsc.columns] == [plan.get(c.name, c.name) for c in sc.columns]
    types_ok = [c.type for c in dsc.columns] == [c.type for c in sc.columns]
    checks = {"names_equal_plan": names_ok, "types_unchanged": types_ok,
              "rows_unchanged": before.rows == after.rows == dsc.rows,
              "value_hash_unchanged": before.digest == after.digest,
              "round_trip_restores_original": back.digest == before.digest and back.schema_fingerprint
              == before.schema_fingerprint}
    return {"source": str(src), "renamed": str(dst), "rows": before.rows, "digest": before.digest,
            "checks": checks, "pass": all(checks.values())}


def targets(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Per rename key (step name, B, A): its columns, retained CSV and SQL files."""
    out: dict[str, dict[str, Any]] = {}
    for s in manifest["steps"]:
        csv = s.get("csv") if "full_csv" in s.get("artifacts", []) else None
        out[s["name"]] = {"columns": [c["name"] for c in s["columns"]], "csv": csv["path"] if csv else None,
                          "sql": [p for p in (s.get("sql_path"),) if p]}
    for d in manifest["deliverables"]:
        out[d["side"]] = {"columns": [c["name"] for c in d.get("columns", [])], "csv": d["csv"]["path"],
                          "sql": [p for p in (d.get("sql_path"),) if p]}
    return out


def apply(run_dir: Path) -> tuple[int, dict[str, Any]]:
    """Validate every plan, write the copies, run the lossless checks; clean up on any failure."""
    run_dir = Path(run_dir)
    manifest = c2.load_manifest(run_dir)
    _, rows = c2.read_records(run_dir / RENAMES, RENAMES)
    plans, errs = effective_plans(rows)
    tg = targets(manifest)
    report: dict[str, Any] = {"applied_utc": c2.utc_now_text(), "plans": plans, "errors": list(errs), "files": []}
    for step in plans:
        if step not in tg:
            report["errors"].append(f"{step}: not a manifest step or deliverable side")
    if not plans and not report["errors"]:
        report["status"] = "skipped: no accepted rename (Max kept or skipped every column)"
        c2.write_json(run_dir / REPORT, report)
        return int(ExitCode.PASS), report
    for step in plans:  # B.sql / A.sql are read only when they still match their freeze (exit 2, nothing written)
        for sp in tg.get(step, {}).get("sql", []):
            side = next((s for s in freeze_check.SIDES if Path(sp).as_posix() == f"{s}.sql"), None)
            if side:
                freeze_check.require_frozen(run_dir, side)
    written: list[Path] = []
    try:
        for step, plan in plans.items():
            if step not in tg:
                continue
            t = tg[step]
            cols = list(t["columns"])
            if t["csv"]:
                cols = [c.name for c in cc.read_sidecar(c2.resolve_in_run(run_dir, t["csv"])).columns]
            v = validate_plan(cols, plan)
            report["errors"] += [f"{step}: {e}" for e in v]
            if v:
                continue
            if t["csv"]:
                src = c2.resolve_in_run(run_dir, t["csv"])
                dst = renamed_path(src)
                written += [dst, cc.sidecar_path(dst)]
                rep = rename_csv(src, dst, plan)
                rep["step"] = step
                report["files"].append(rep)
                if not rep["pass"]:
                    report["errors"].append(f"{step}: lossless check failed for {t['csv']}: "
                                            + ", ".join(k for k, ok in rep["checks"].items() if not ok))
            for sp in t["sql"]:
                src = c2.resolve_in_run(run_dir, sp)
                if not src.is_file():
                    report["errors"].append(f"{step}: SQL file missing: {sp}")
                    continue
                dst = renamed_path(src)
                written.append(dst)
                c2.write_text(dst, rename_sql(src.read_text(encoding="utf-8"), cols, plan))
                report["files"].append({"step": step, "source": sp, "renamed": str(dst), "pass": True,
                                        "checks": {"sql_wrapped": True}})
    except BaseException:
        for p in written:
            p.unlink(missing_ok=True)
        raise
    if report["errors"]:
        for p in written:
            p.unlink(missing_ok=True)
        report["status"] = "lossy: no renamed file kept"
        report["files"] = []
        c2.write_json(run_dir / REPORT, report)
        return int(ExitCode.FAIL), report
    report["status"] = "lossless"
    c2.write_json(run_dir / REPORT, report)
    return int(ExitCode.PASS), report


def _main(a: Any) -> int:
    c2.phase_check(a.run_dir, ["P10"])
    code, rep = apply(a.run_dir)
    if code == ExitCode.FAIL:
        raise CheckFailedError("lossy rename map, no renamed file kept: " + "; ".join(rep["errors"]))
    print(f"rename {rep['status']}: {len(rep['files'])} file(s)")
    return code


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    return c2.run_cli_args(_main, c2.base_parser("rename_apply.py", "P10 rename on copies (spec 2.2 P10)"), argv)


if __name__ == "__main__":
    sys.exit(main())

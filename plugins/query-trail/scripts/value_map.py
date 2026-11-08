"""value_map.py: write, list and check 11_value_map.csv, the G3 value map (design spec v4 2.2 P2; fix r3 B3-9).

The only writer of 11_value_map.csv (never by hand, never by a library call from an ad-hoc snippet). Row shape,
kinds and what each row clears: scripts/lib/valuemap.py and skills/discover section 4. G3 reads the file this
script writes; a run-wide row (`--scope run`) also needs its own G3 decision row
(`decide.py --gate G3 --check-id <row_id> ...`) before it clears anything.

CLI:
  value_map.py --run-dir R add --kind timezone|period|filter|derive|map --table T --column C --value V --reason R
               [--replace]
  value_map.py --run-dir R add --kind timezone --scope run --value UTC --reason R     (run-wide, needs its G3 row)
  value_map.py --run-dir R add --kind not_used --table T --column C --reason R          (fix r4 C4-9; no --value)
  value_map.py --run-dir R list
  value_map.py --run-dir R validate
Exit: 0 written / listed / valid; 1 validate found problems (listed on stderr); 2 refused (phase not P2, a bad row,
      a table outside the G2 lock, a column not in 05_catalog.csv, a duplicate without --replace): no warehouse call
      is made by this script; local effects: none, 11_value_map.csv is unchanged.

Date boundaries (fix r4 LOW): a period value YYYY-MM-DD..YYYY-MM-DD names whole days; the start day and the end day are
both included (2025-01-01..2025-09-30 is January 1 through September 30). A not_used row (fix r4 C4-9) says the query
does not use that column; it settles freshness on it and clears its traps, and is never read as a filter.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import runctx as c2, valuemap  # noqa: E402
from lib import refusal  # noqa: E402  fix r3 B3-4: exit-2 local effects on stderr
from lib.exit_codes import CheckFailedError, ExitCode, RefusedError  # noqa: E402


def add(a: Any) -> int:
    run_dir = Path(a.run_dir)
    scope = a.scope
    table = (a.table or "").strip() or None
    column = (a.column or "").strip() or None
    if scope == "column" and (not table or not column):
        raise RefusedError("--table and --column name the column this row decides (or --scope run for a run-wide row)")
    if scope == "run" and (table or column):
        raise RefusedError("a run-wide row (--scope run) takes no --table or --column")
    value = (a.value or "").strip()
    if not value and a.kind == "not_used":
        value = valuemap.NOT_USED_DEFAULT
    if not value:
        raise RefusedError(f"--value is required for kind {a.kind}")
    row = {"kind": a.kind, "scope": scope, "table_ref": table, "column_name": column, "value": value,
           "reason": a.reason.strip(), "added_utc": c2.utc_now_text()}
    row["row_id"] = valuemap.row_id(a.kind, table, column, row["value"])
    rows = valuemap.read(run_dir)
    if any(str(r.get("row_id")) == row["row_id"] for r in rows):
        if not a.replace:
            raise RefusedError(f"{row['row_id']} already exists; pass --replace to change it")
        rows = [r for r in rows if str(r.get("row_id")) != row["row_id"]]
    probs = valuemap.problems([row], run_dir)
    if probs:
        raise RefusedError("; ".join(probs))
    valuemap.write(run_dir, rows + [row])
    print(f"value_map.py: {row['row_id']} added to {valuemap.VALUE_MAP}")
    if scope == "run":
        print(f"  run-wide: it clears nothing until G3 records it: decide.py --gate G3 --check-id {row['row_id']} "
              "--outcome approve --answer \"<Max's words>\"")
    return int(ExitCode.PASS)


def list_rows(a: Any) -> int:
    rows = valuemap.read(Path(a.run_dir))
    for r in rows:
        where = "run-wide" if str(r.get("scope")) == "run" else f"{r.get('table_ref')}.{r.get('column_name')}"
        print(f"{r.get('row_id')}  {r.get('kind')}  {where}  {r.get('value')}  ({r.get('reason')})")
    print(f"{len(rows)} row(s) in {valuemap.VALUE_MAP}")
    return int(ExitCode.PASS)


def validate(a: Any) -> int:
    run_dir = Path(a.run_dir)
    if not (run_dir / valuemap.VALUE_MAP).is_file():
        raise RefusedError(f"{valuemap.VALUE_MAP} missing: add rows with value_map.py add")
    probs = valuemap.problems(valuemap.read(run_dir), run_dir)
    if probs:
        raise CheckFailedError(f"{len(probs)} problem(s) in {valuemap.VALUE_MAP}: " + "; ".join(probs))
    print(f"{valuemap.VALUE_MAP} valid")
    return int(ExitCode.PASS)


def _main(a: Any) -> int:
    c2.phase_check(a.run_dir, ["P2"])
    return {"add": add, "list": list_rows, "validate": validate}[a.cmd](a)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    ap = c2.base_parser("value_map.py", "write, list and check 11_value_map.csv (G3; spec 2.2 P2)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ad = sub.add_parser("add")
    ad.add_argument("--kind", required=True, choices=valuemap.KINDS)
    ad.add_argument("--scope", default="column", choices=valuemap.SCOPES)
    ad.add_argument("--table", default=None, help="<dataset alias>.<table>, a locked table")
    ad.add_argument("--column", default=None)
    ad.add_argument("--value", default=None, help="required except for --kind not_used")
    ad.add_argument("--reason", required=True)
    ad.add_argument("--replace", action="store_true", help="replace the row with the same row_id")
    sub.add_parser("list")
    sub.add_parser("validate")
    return c2.run_cli_args(lambda a: refusal.guard(a.run_dir, lambda: _main(a)), ap, argv)


if __name__ == "__main__":
    sys.exit(main())

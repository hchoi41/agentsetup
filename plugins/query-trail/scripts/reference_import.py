"""reference_import.py: bring a reference extract into a run (fix brief r1 B-M3; design spec v2 sections 4.3, 6.3).

    python -I -X utf8 -B scripts/reference_import.py --run-dir RUN --file X.csv --source "..."
        --independence IND-n --as-of ... [--replace]

The tie-out (logic_tests.py, P9) reads reference_values.csv through the sanctioned typed reader, which needs
a sidecar with the file's sha256 (D-04). This script makes that possible for Max's own extract:

1. phase P1 to P9, G0a recorded, run_config.reference declares a reference (type not none) with its
   provenance (spec 6.3; fixed at G1a, so this script never changes run_config.json);
2. --source and --as-of must equal run_config.reference.source and .as_of; --independence must agree with
   run_config.reference.independence: IND-5 only for "independent" (a GL trial balance or a control total
   from another system), IND-0 to IND-4 only for "same_source" or "synthetic" (spec 4.3);
3. the file meets the CSV contract byte rules (UTF-8, no BOM, LF only, balanced quotes, final LF);
4. its header is `reference_value` plus columns of 03_output_contract.json (normally the grain); an unknown
   or duplicated column, no reference_value, or a missing grain column (run_config.reference.grain, else the
   output contract's grain, else its keys; fix r2 dec L1) is refused;
5. every value parses under the typed reader: the output contract's types for the grain columns, DOUBLE
   for reference_value;
6. the file is copied unchanged to reference_values.csv (its sha256 is Max's file's sha256), its typed
   sidecar reference_values.schema.json is written, and manifest.json gets a `reference` block (path, sha256,
   rows, columns, source, independence, as_of, import time) and a files[] entry (retention, spec 7.1).

Exit 0 imported; 2 refused: no warehouse call was made; local effects: none (the copy is staged in a temporary
folder and moved only after every check). Exit 2 wording (fix r3 B3-4, spec v4 1.4): "refused; no warehouse call was made; local effects: <files written, changed or removed, or none>" on stderr (lib/refusal.py).
"""
from __future__ import annotations

import argparse
import csv
import io
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import duckdb  # noqa: E402

from lib import csv_contract as cc  # noqa: E402
from lib import refusal  # noqa: E402  fix r3 B3-4: exit-2 local effects on stderr
from lib import manifest, runctx  # noqa: E402
from lib.exit_codes import ExitCode, RefusedError  # noqa: E402

TARGET = "reference_values.csv"
VALUE_COLUMN = "reference_value"
PHASES = tuple(f"P{i}" for i in range(1, 10))
IND_LEVELS = tuple(f"IND-{i}" for i in range(6))
_BQ_TO_DUCK = {"INT64": "BIGINT", "INTEGER": "BIGINT", "FLOAT64": "DOUBLE", "FLOAT": "DOUBLE",
               "NUMERIC": "DECIMAL(38,9)", "STRING": "VARCHAR", "BOOL": "BOOLEAN", "BOOLEAN": "BOOLEAN",
               "DATE": "DATE", "DATETIME": "TIMESTAMP", "TIMESTAMP": "TIMESTAMP WITH TIME ZONE"}


def duck_type(bq_type: str) -> str:
    """DuckDB reader type for an output-contract column type."""
    t = str(bq_type).upper().strip()
    if t.startswith(("NUMERIC(", "DECIMAL(")):
        return "DECIMAL" + t[t.index("("):]
    return _BQ_TO_DUCK.get(t.split("(")[0], "VARCHAR")


def check_provenance(ref: Mapping[str, Any], source: str, independence: str, as_of: str) -> None:
    """Refuse an import that disagrees with the reference fixed in run_config.json at G1a."""
    if str(ref.get("type", "none")) == "none":
        raise RefusedError("run_config.reference declares no reference (type none): set it at G1a first")
    if independence not in IND_LEVELS:
        raise RefusedError(f"--independence must be one of {', '.join(IND_LEVELS)}")
    for flag, key, given in (("--source", "source", source), ("--as-of", "as_of", as_of)):
        if str(ref.get(key) or "") != given:
            raise RefusedError(f"{flag} differs from run_config.reference.{key} (fixed at G1a)")
    cls = str(ref.get("independence") or "")
    if (independence == "IND-5") != (cls == "independent"):
        raise RefusedError(f"--independence {independence} disagrees with run_config.reference.independence "
                           f"{cls!r}: IND-5 only for an independent reference (spec 4.3)")


def header_columns(path: Path, contract: Mapping[str, Any]) -> list[cc.Column]:
    """Typed columns of the extract's header; refuse unknown, duplicated or missing columns."""
    first = path.read_bytes().decode("utf-8").split("\n", 1)[0]
    names = next(csv.reader(io.StringIO(first)), [])
    types = {str(c["name"]): str(c.get("type", "STRING")) for c in contract.get("columns", [])}
    if VALUE_COLUMN not in names:
        raise RefusedError(f"the header has no {VALUE_COLUMN} column")
    dup = sorted({n for n in names if names.count(n) > 1})
    unknown = [n for n in names if n != VALUE_COLUMN and n not in types]
    if dup or unknown:
        raise RefusedError(("duplicated column " + ", ".join(dup) + "; ") * bool(dup) +
                           ("unknown column " + ", ".join(unknown) + " (not in 03_output_contract.json)") * bool(unknown))
    return [cc.Column(n, "DOUBLE" if n == VALUE_COLUMN else duck_type(types[n])) for n in names]


def grain_columns(ref: Mapping[str, Any], contract: Mapping[str, Any]) -> list[str]:
    """The reference's grain: run_config.reference.grain (comma list), else the contract's grain, else its keys."""
    g = [x.strip() for x in str(ref.get("grain") or "").split(",") if x.strip()]
    return g or [str(x) for x in (contract.get("grain") or contract.get("keys") or [])]


def check_grain(cols: Sequence[cc.Column], ref: Mapping[str, Any], contract: Mapping[str, Any]) -> None:
    """Refuse an extract that lacks a grain column (the tie-out joins on the grain)."""
    have = {c.name for c in cols}
    missing = [g for g in grain_columns(ref, contract) if g not in have]
    if missing:
        raise RefusedError(f"grain column {', '.join(missing)} missing from the extract's header (the tie-out joins "
                           "reference_value to the deliverable on the grain)")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="reference_import.py", description="import a reference extract (spec v2 6.3)")
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--file", required=True, type=Path)
    ap.add_argument("--source", required=True)
    ap.add_argument("--independence", required=True, help="IND-5 independent; IND-0..IND-4 same-source or synthetic")
    ap.add_argument("--as-of", required=True)
    ap.add_argument("--replace", action="store_true", help="replace an imported reference_values.csv")
    a = ap.parse_args(argv)

    def body() -> int:
        st = runctx.enter(a.run_dir, PHASES)
        cfg = runctx.load_run_config(a.run_dir)
        runctx.require_g0a(cfg)
        ref = cfg.get("reference") or {"type": "none"}
        check_provenance(ref, a.source, a.independence, a.as_of)
        if not a.file.is_file():
            raise RefusedError(f"extract missing: {a.file}")
        target = a.run_dir / TARGET
        if target.exists() and not a.replace:
            raise RefusedError(f"{TARGET} already imported; pass --replace to import another extract")
        contract_path = a.run_dir / "03_output_contract.json"
        if not contract_path.is_file():
            raise RefusedError("03_output_contract.json missing: the header is checked against it (G1b)")
        cc.assert_contract(a.file)
        contract = runctx.read_json(contract_path)
        cols = header_columns(a.file, contract)
        check_grain(cols, ref, contract)
        con = duckdb.connect()
        try:
            cc.run_sql(con, "SET TimeZone = 'UTC'")
            every = ", ".join(f"COUNT({cc.quote_ident(c.name)})" for c in cols)  # forces every value to parse
            rows = int(cc.run_sql(con, f"SELECT COUNT(*), {every} FROM {cc.read_csv_sql(a.file, cols)}")[0][0])
        except duckdb.Error as exc:
            raise RefusedError(f"values do not parse under the typed reader: {str(exc).splitlines()[0][:200]}") from exc
        finally:
            con.close()
        sha = cc.sha256_file(a.file)
        side = cc.Sidecar(tuple(cols), rows, sha, a.file.stat().st_size,
                          writer="external extract (reference_import.py, contract validated)")
        m = manifest.load(a.run_dir) or manifest.new_manifest(cfg, st.attempt)
        with tempfile.TemporaryDirectory(dir=str(a.run_dir)) as td:  # stage, then move: nothing half-written
            staged = Path(td) / TARGET
            shutil.copyfile(a.file, staged)
            if cc.sha256_file(staged) != sha:
                raise RefusedError("the copy differs from the extract (sha256)")
            staged.replace(target)
        written_at = runctx.utc_now()  # the sidecar is generated here (created_basis generated, B3-3)
        cc.write_json_lf(cc.sidecar_path(target), side.to_json())
        m["reference"] = {"path": TARGET, "sha256": sha, "schema_path": cc.sidecar_path(target).name, "rows": rows,
                          "columns": [{"name": c.name, "type": c.type} for c in cols], "source": a.source,
                          "independence": a.independence, "independence_class": str(ref.get("independence") or "none"),
                          "reference_type": str(ref.get("type")), "as_of": a.as_of, "original_name": a.file.name,
                          "imported_utc": runctx.fmt_utc(runctx.utc_now())}
        manifest.upsert_files(m, [manifest.file_entry(a.run_dir, TARGET, "reference", cfg),  # from elsewhere: inferred
                                  manifest.file_entry(a.run_dir, cc.sidecar_path(target).name, "schema_sidecar", cfg,
                                                      generated_utc=written_at)])
        manifest.save(a.run_dir, m)
        print(f"reference_import.py: PASS: {TARGET} ({rows} rows, sha256 {sha[:12]}...), provenance in manifest.json")
        return int(ExitCode.PASS)

    return runctx.run_cli(lambda: refusal.guard(a.run_dir, body, warehouse=False), "reference_import.py")


if __name__ == "__main__":
    sys.exit(main())

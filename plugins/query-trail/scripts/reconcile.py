"""reconcile.py: B vs chain(B), then A vs chain(B) (design spec v1 sections 2.2 P7, 4.4, 6.1; D-06, D-07, DB-01).

Order (spec 6.1), on the typed local CSVs read only through the lib reader:
1. contracts: each side's names, order and types against 03_output_contract.json (when present), and side
   against side;
2. counts: rows, NULL keys on the declared key (> 0 is FAIL), grain (duplicate keys) on both sides;
   at level 60 (no CSV for the final step) the comparison is count + schema + multiset hash;
3. multiplicity diff on canonical row text (duplicates counted, which EXCEPT DISTINCT would hide);
4. matching method as v4 states it: exact canonical-row multiplicity on non-FLOAT columns; FLOAT columns are
   compared after matching on the declared key (or on the non-FLOAT columns when no key exists), duplicate
   matches paired by occurrence index (research_r2 BQ03_checker_fixed.py idea), within the run's absolute
   FLOAT tolerance; when neither a key nor a non-FLOAT column exists: "multiplicity only, values not matched";
5. every differing row named in checks/mismatch_rows.csv, always written, even empty (no BOM, lib writer).

Difference classes: count (row counts differ), key (key or non-FLOAT row present on one side only or with a
different multiplicity), value (matched rows whose values differ), type (names, order or types differ).
B vs chain(B) runs first; its failure routes to P5 (route=P5 in the status file) and A vs chain(B) is not run.
An A vs chain(B) failure routes to P5 as well (fix r1 A-H1: re-split or rewrite in a new attempt, or stop with a
G8 reason); no decomposition difference is ever accepted at G4.
The status file carries statuses and classes only, no wording (DB-01); report.py words the verdict at P11.

Status per comparison: PASS, FAIL, or INCONCLUSIVE (fix r1 A-H2) when nothing established value agreement:
(a) the multiset hash could not be compared (digest routes or tolerances differ) and no value comparison ran
(a side has no retained CSV), or (b) the method is "multiplicity only, values not matched". A FAIL wins over
INCONCLUSIVE. INCONCLUSIVE is never positive: report.py words it "not reconciled (comparison inconclusive: ...)".
When the cause is the stream cap (one side's digest is warehouse-side), reconcile.py writes only
gate_request.json (G15: filter, cut local, raise cap, stop) and exits 3; any other cause exits 1, route G8.

Outputs: checks/reconcile_<comparison>.csv, checks/mismatch_rows.csv, checks/reconcile_status.json. The status file
carries `attempt` (the run state's) and `steps_sha256` (runctx.steps_sha256: the manifest steps' sql_sha256 joined
by LF); report.py refuses a status from another attempt or other steps (fix r2 A2-H1).
Exit 0 every comparison PASS, 1 any FAIL or INCONCLUSIVE, 2 refused (missing manifest or CSV), 3 G15 request.

CLI: python -I -X utf8 -B scripts/reconcile.py --run-dir RUN
"""
from __future__ import annotations

import argparse
import collections
import decimal
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import duckdb  # noqa: E402

import contract_check  # noqa: E402
from lib import canon, manifest, runctx  # noqa: E402
from lib import csv_contract as cc  # noqa: E402
from lib.exit_codes import ExitCode, RefusedError  # noqa: E402

STATUS = "reconcile_status.json"
MISMATCH = "mismatch_rows.csv"
CHECK_COLS = ["check", "status", "left", "right", "detail"]
MISMATCH_COLS = ["comparison", "class", "side", "key", "row", "column", "left_value", "right_value",
                 "count_left", "count_right"]
FLOAT = ("DOUBLE", "FLOAT", "REAL", "FLOAT4", "FLOAT8", "FLOAT64")


class Side:
    """One side of a comparison: label, row count, hash, columns, and rows when a CSV is retained."""

    def __init__(self, label: str, entry: Mapping[str, Any], run_dir: Path) -> None:
        self.label = label
        self.entry = entry
        self.rows_out = int(entry["rows_out"])
        self.hash = entry["multiset_hash"]
        self.columns = [cc.Column(c["name"], c["type"]) for c in entry["columns"]]
        self.csv = run_dir / entry["csv"]["path"] if entry.get("csv") else None

    def read(self, con: duckdb.DuckDBPyConnection) -> list[tuple[Any, ...]]:
        """Every row through the sanctioned reader (sha256 checked)."""
        assert self.csv is not None
        cc.read_csv(con, self.csv, validate=True)
        sc = cc.read_sidecar(self.csv)
        self.columns = list(sc.columns)
        return list(canon.iter_query_rows(con, "SELECT * FROM " + cc.read_csv_sql(self.csv, sc.columns), sc.columns))


def _is_float(c: cc.Column) -> bool:
    return canon.parse_type(c.type)[0] in FLOAT


def _txt(cells: Sequence[Any]) -> str:
    return json.dumps(list(cells), ensure_ascii=False, separators=(",", ":"), default=str)


def compare_sides(name: str, left: Side, right: Side, contract: Mapping[str, Any] | None, tol: float | None,
                  con: duckdb.DuckDBPyConnection) -> tuple[dict[str, Any], list[list[Any]], list[list[Any]]]:
    """(status object, check rows, mismatch rows) for one comparison."""
    checks: list[list[Any]] = []
    mism: list[list[Any]] = []
    classes: set[str] = set()
    not_run: list[str] = []

    def chk(check: str, ok: bool | None, lv: Any, rv: Any, detail: str = "") -> None:
        checks.append([check, "NOT_RUN" if ok is None else ("PASS" if ok else "FAIL"), lv, rv, detail])

    # 1 contracts
    keys: list[str] = []
    if contract:
        keys = [k for k in (contract.get("keys") or contract.get("grain") or [])]
        for side in (left, right):
            rows = contract_check.compare([(c.name, c.type) for c in side.columns], contract, "duckdb")
            bad = [r for r in rows if r[4] == "FAIL"]
            chk(f"contract_{side.label}", not bad, len(rows) - len(bad), len(rows),
                "; ".join(f"{r[0]} {r[1]}" for r in bad[:5]))
            if bad:
                classes.add("type")
    else:
        chk("contract", None, None, None, "no 03_output_contract.json")
        not_run.append("contract")
    ln = [c.name for c in left.columns]
    rn = [c.name for c in right.columns]
    same_names, same_types = ln == rn, [c.type for c in left.columns] == [c.type for c in right.columns]
    chk("names_and_order", same_names, ",".join(ln), ",".join(rn))
    chk("types", same_types, ",".join(c.type for c in left.columns), ",".join(c.type for c in right.columns))
    if not (same_names and same_types):
        classes.add("type")
    # 2 counts and the level-60 hash test
    chk("row_count", left.rows_out == right.rows_out, left.rows_out, right.rows_out)
    if left.rows_out != right.rows_out:
        classes.add("count")
    cmp = canon.compare_digests(canon.StepDigest(**{k: left.hash.get(k) for k in ("digest", "rows", "route", "schema_fingerprint", "float_tolerance")}),
                                canon.StepDigest(**{k: right.hash.get(k) for k in ("digest", "rows", "route", "schema_fingerprint", "float_tolerance")}))
    chk("multiset_hash", None if cmp.code == ExitCode.REFUSED else cmp.code == ExitCode.PASS, left.hash["digest"],
        right.hash["digest"], "; ".join(cmp.reasons))
    hash_ok = cmp.code == ExitCode.PASS
    hash_ran = cmp.code != ExitCode.REFUSED
    value_compared = False
    method = "count, schema and multiset hash (level 60 route)"
    if left.csv is None or right.csv is None:
        not_run.append("multiplicity_and_matching (needs both sides as full CSV: level 90 or an escalation re-run)")
        chk("multiplicity_diff", None, None, None, not_run[-1])
        if not hash_ok:
            classes.add("value" if left.rows_out == right.rows_out and same_names and same_types else "count")
    else:
        lrows, rrows = left.read(con), right.read(con)
        cols = left.columns
        names = [c.name for c in cols]
        kidx = [names.index(k) for k in keys if k in names]
        for side, rows in ((left, lrows), (right, rrows)):
            if kidx:
                nk = sum(1 for r in rows if any(r[i] is None for i in kidx))
                chk(f"null_keys_{side.label}", nk == 0, nk, 0, "NULL keys on the declared key")
                cnt = collections.Counter(tuple(r[i] for i in kidx) for r in rows)
                dup = sum(v - 1 for v in cnt.values() if v > 1)
                chk(f"grain_{side.label}", dup == 0, dup, 0, "duplicate keys on the declared grain")
                if nk:
                    classes.add("key")
        if not same_names or len(cols) != len(right.columns):
            chk("multiplicity_diff", None, None, None, "schemas differ: rows not comparable")
        else:
            fidx = [i for i, c in enumerate(cols) if _is_float(c)]
            nidx = [i for i in range(len(cols)) if i not in fidx]
            if not fidx:
                method = "exact canonical-row multiplicity"
                value_compared = True
                specs = canon.specs_for(cols)
                _multiplicity(name, lrows, rrows, specs, list(range(len(cols))), kidx, names, mism, classes, chk,
                              "multiplicity_diff")
            elif kidx or nidx:
                on = kidx if kidx else nidx
                method = ("FLOAT columns matched on the declared key" if kidx else
                          "FLOAT columns matched on the non-FLOAT columns") + f" within tolerance {tol}"
                specs = canon.specs_for([cols[i] for i in nidx])
                _multiplicity(name, [tuple(r[i] for i in nidx) for r in lrows], [tuple(r[i] for i in nidx) for r in rrows],
                              specs, list(range(len(nidx))), [nidx.index(i) for i in kidx if i in nidx],
                              [names[i] for i in nidx], mism, classes, chk, "multiplicity_diff_non_float")
                _float_match(name, lrows, rrows, on, fidx, names, cols, tol, mism, classes, chk)
                value_compared = True
            else:
                method = "multiplicity only, values not matched"
                specs = canon.specs_for(cols, tol)
                _multiplicity(name, lrows, rrows, specs, list(range(len(cols))), [], names, mism, classes, chk,
                              "multiplicity_diff_tolerance_rounded")
    failed = any(r[1] == "FAIL" for r in checks)
    if failed and not classes:
        classes.add("value")
    reason, cause = None, None
    if not failed:
        if not hash_ran and not value_compared:
            reason = (f"multiset hash not comparable ({'; '.join(cmp.reasons)}) and no value comparison ran "
                      f"({not_run[-1] if not_run else 'rows not compared'})")
            routes = {left.hash.get("route"), right.hash.get("route")}
            cause = "stream_cap" if len(routes) > 1 and routes - {canon.LOCAL_ROUTE} else "other"
        elif method == "multiplicity only, values not matched":
            reason = "multiplicity only, values not matched (no declared key and no non-FLOAT column identifies rows)"
            cause = "other"
    status = "FAIL" if failed else ("INCONCLUSIVE" if reason else "PASS")
    st = {"comparison": name, "left": left.label, "right": right.label, "status": status,
          "classes": sorted(classes), "method": method, "not_run_at_level": not_run,
          "n_mismatch_rows": len(mism)}
    if reason:
        st.update(inconclusive_reason=reason, inconclusive_cause=cause)
    return st, checks, mism


def _multiplicity(name: str, lrows: Sequence[tuple[Any, ...]], rrows: Sequence[tuple[Any, ...]], specs: Sequence[canon.CellSpec],
                  idx: Sequence[int], kidx: Sequence[int], names: Sequence[str], mism: list[list[Any]], classes: set[str],
                  chk: Any, check_name: str) -> None:
    """Canonical-row multiplicity diff; differences are key class when the key projection differs, else value."""
    lm = canon.multiplicity(lrows, specs)
    rm = canon.multiplicity(rrows, specs)
    diff = canon.multiplicity_diff(lm, rm)
    key_diff: set[str] = set()
    if kidx:
        kl = collections.Counter(_txt([r[i] for i in kidx]) for r in lrows)
        kr = collections.Counter(_txt([r[i] for i in kidx]) for r in rrows)
        key_diff = {k for k in set(kl) | set(kr) if kl.get(k, 0) != kr.get(k, 0)}
    for row_text, cl, cr in diff:
        cells = json.loads(row_text)
        key = _txt([cells[i] for i in kidx]) if kidx else None
        cls = "key" if (not kidx or key in key_diff) else "value"
        classes.add(cls)
        side = "left_only" if cr == 0 else "right_only" if cl == 0 else "count_differs"
        mism.append([name, cls, side, key, row_text, None, None, None, cl, cr])
    chk(check_name, not diff, sum(lm.values()), sum(rm.values()), f"{len(diff)} canonical rows differ")


def _float_match(name: str, lrows: Sequence[tuple[Any, ...]], rrows: Sequence[tuple[Any, ...]], on: Sequence[int],
                 fidx: Sequence[int], names: Sequence[str], cols: Sequence[cc.Column], tol: float | None,
                 mism: list[list[Any]], classes: set[str], chk: Any) -> None:
    """Pair rows on `on` (occurrence index among equal keys, ordered by the FLOAT values) and compare FLOATs."""
    specs = canon.specs_for([cols[i] for i in on])

    def groups(rows: Sequence[tuple[Any, ...]]) -> dict[bytes, list[tuple[Any, ...]]]:
        g: dict[bytes, list[tuple[Any, ...]]] = collections.defaultdict(list)
        for r in rows:
            g[canon.canon_row([r[i] for i in on], specs)].append(r)
        for v in g.values():
            v.sort(key=lambda r: tuple((r[i] is None, r[i] if r[i] is not None else 0.0) for i in fidx))
        return g

    gl, gr = groups(lrows), groups(rrows)
    t = decimal.Decimal(repr(tol)) if tol else decimal.Decimal(0)
    bad = 0
    for k in sorted(set(gl) & set(gr)):
        for occ, (a, b) in enumerate(zip(gl[k], gr[k])):
            for i in fidx:
                x, y = a[i], b[i]
                if (x is None) != (y is None):
                    diff = True
                elif x is None:
                    diff = False
                else:
                    diff = abs(decimal.Decimal(repr(float(x))) - decimal.Decimal(repr(float(y)))) > t
                if diff:
                    bad += 1
                    classes.add("value")
                    mism.append([name, "value", "both", k.decode("utf-8") + f"#{occ}", None, names[i],
                                 None if x is None else repr(float(x)), None if y is None else repr(float(y)), 1, 1])
    chk("float_values_within_tolerance", bad == 0, None, None, f"{bad} FLOAT cells beyond tolerance {tol}")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="reconcile.py", description="query-trail reconciliation (spec 2.2 P7, 6.1)")
    ap.add_argument("--run-dir", required=True, type=Path)
    a = ap.parse_args(argv)

    def body() -> int:
        st_now = runctx.enter(a.run_dir, ("P7",))
        cfg = runctx.load_run_config(a.run_dir)
        runctx.require_g0a(cfg)
        m = manifest.load(a.run_dir)
        if not m or not m.get("steps") or not m.get("deliverables"):
            raise RefusedError("manifest.json with steps and deliverables is required (run_steps.py, P6)")
        contract = runctx.read_json(a.run_dir / contract_check.CONTRACT) if (a.run_dir / contract_check.CONTRACT).is_file() else None
        tol = cfg.get("float_tolerance")
        deliv = {d["side"]: d for d in m["deliverables"]}
        if "B" not in deliv:
            raise RefusedError("whole B is not in the manifest deliverables")
        chain = Side("chain_B", m["steps"][-1], a.run_dir)
        pairs = [("B_vs_chain", Side("B", deliv["B"], a.run_dir), chain)]
        if str(cfg.get("author_mode", "single")).startswith("second:"):
            if "A" not in deliv:
                raise RefusedError("second-author mode: whole A is not in the manifest deliverables")
            pairs.append(("A_vs_chain", Side("A", deliv["A"], a.run_dir), Side("chain_B", m["steps"][-1], a.run_dir)))
        statuses, mism_all, check_tables = [], [], {}
        con = duckdb.connect()
        try:
            for i, (name, left, right) in enumerate(pairs):
                if i > 0 and statuses[0]["status"] == "FAIL":
                    statuses.append({"comparison": name, "status": "NOT_RUN", "classes": [],
                                     "reason": "B vs chain(B) failed: split fidelity first (P5)"})
                    continue
                st, checks, mism = compare_sides(name, left, right, contract, tol, con)
                check_tables[name] = checks
                statuses.append(st)
                mism_all += mism
        finally:
            con.close()
        capped = [s for s in statuses if s.get("inconclusive_cause") == "stream_cap"]
        if capped:  # exit 3: the gate request is the only file written (spec 1.4)
            runctx.write_gate_request(
                a.run_dir, "G15", "P7", "reconcile.py",
                "comparison inconclusive: a side's digest is warehouse-side (over the stream cap) and no value "
                "comparison ran", {"comparisons": [{k: s.get(k) for k in ("comparison", "inconclusive_reason")}
                                                   for s in capped],
                                   "options": ["filter", "cut_local", "raise_cap", "stop"]})
            sys.stderr.write("NEEDS_DECISION: G15: " + "; ".join(s["inconclusive_reason"] for s in capped) + "\n")
            return int(ExitCode.NEEDS_DECISION)
        for name, checks in check_tables.items():
            runctx.write_rows_csv(a.run_dir / "checks" / f"reconcile_{name}.csv", CHECK_COLS, checks)
        runctx.write_rows_csv(a.run_dir / "checks" / MISMATCH, MISMATCH_COLS, mism_all)
        p7_rows = []
        for s in statuses:
            f = a.run_dir / "checks" / f"reconcile_{s['comparison']}.csv"
            sha = cc.read_sidecar(f).csv_sha256 if f.is_file() and s["status"] != "NOT_RUN" else None
            p7_rows.append(runctx.check_row(f"p7_{s['comparison']}", "P7", "comparison", s["status"],
                                            f"checks/reconcile_{s['comparison']}.csv" if sha else None, sha,
                                            s.get("inconclusive_reason") or ", ".join(s.get("classes") or []),
                                            s.get("reason") if s["status"] == "NOT_RUN" else None))
        runctx.replace_phase_checks(a.run_dir, "P7", p7_rows)
        failed = any(s["status"] in ("FAIL", "INCONCLUSIVE") for s in statuses)
        # fix r1 A-H1: a decomposition FAIL goes back to P5 (or stops with a G8 reason), never to G4
        route = "P5" if any(s["status"] == "FAIL" for s in statuses) else ("G8" if failed else None)
        runctx.write_json(a.run_dir / "checks" / STATUS, {
            "schema_version": "1", "exit_code": int(ExitCode.FAIL if failed else ExitCode.PASS), "route": route,
            "attempt": st_now.attempt, "steps_sha256": runctx.steps_sha256(m),  # fix r2 A2-H1: report.py checks both
            "confidence_level": m["confidence_level"], "independence": m["independence"]["level"],
            "pin_status": m["pin"]["pin_status"], "comparisons": statuses,
            "adequacy_counters_not_run": ["per-join orphans (warehouse-side, not in stage 2a)", "boundary hits"],
            "checked_utc": runctx.fmt_utc(runctx.utc_now())})
        print(f"reconcile.py: {'FAIL' if failed else 'PASS'}: " + ", ".join(f"{s['comparison']} {s['status']}"
                                                                            for s in statuses))
        if not failed:
            runctx.archive_own_request(a.run_dir, "reconcile.py", ("G15",), "P7")  # fix r4 C4-10
        return int(ExitCode.FAIL if failed else ExitCode.PASS)

    return runctx.run_cli(body, "reconcile.py")


if __name__ == "__main__":
    sys.exit(main())

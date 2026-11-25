"""Tests of the v2 raw-layer changes (package P2 of the v2 plan). Owner: P2.

Entries: A-aera-6 (helper rows below the last raw row; null flags), A-aera-5 and C-sol-5 (dependency cycles;
V-NO-VALUE), A-aera-9 and C-sol-4 (one exact key equality), A-aera-7 and C-sol-3 (accepted control totals per
snapshot), A-aera-1 (hashes of writing commands only), A-aera-10 (raw text stays text); supporting A-aera-4
(exact control totals), A-aera-2 (check --refresh and a stale query), A-aera-14 (an alert is not a block's check)
and J-4 (b) and (d) (check value formats; check counts). Tests marked excel drive Excel through lib.excel.
"""
from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib import audit, build, check, common, controls, land, lift, manifest, modelfolder, spec as specmod
from test_build import base_contract, base_spec, load, make_model, refusal_lines, sales_rows
import test_check as tc

# ----------------------------------------------------------------------------- helpers


def _write_rows(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return path


def _sheet(spec: dict, name: str) -> dict:
    return next(s for s in spec["sheets"] if s.get("name") == name or s.get("type") == name)


def _rows_only_contract(**col_changes: dict) -> dict:
    c = base_contract()
    c["expect"] = {"rows": {"min": 1, "max": 1000}}
    for col in c["columns"]:
        col.update(col_changes.get(col["name"], {}))
    return c


def _check_rows(ws) -> dict:
    return {ws.cell(r, 2).value: r for r in range(5, ws.max_row + 1) if ws.cell(r, 2).value}


class _FakeResult(SimpleNamespace):
    def step(self, name: str) -> dict:
        return {}

    def message(self) -> str:
        return "no error"


def fake_excel(monkeypatch: pytest.MonkeyPatch, values: dict, cells: list | None = None) -> None:
    """check_model as if Excel had recalculated: every check reads `values` (ref -> value)."""
    from lib import excel
    monkeypatch.delenv("ROTM_NO_EXCEL", raising=False)
    monkeypatch.setattr(excel, "excel_status", lambda: SimpleNamespace(available=True, reason=""))

    def job(wb, ops, read_refs, timeout_s=None):
        out = {ref: values.get(ref, 0) for ref in read_refs}
        return _FakeResult(ok=True, pid=4242, killed=False, excel_version="16.0"), out, list(cells or []), \
            len(cells or [])
    monkeypatch.setattr(check, "_excel_job", job)


# ============================================================================= 1. A-aera-6

def test_beyond_check_is_built_and_is_part_of_the_master_check(tmp_path: Path) -> None:
    root = make_model(tmp_path / "m")
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    man = manifest.load_manifest(root / "model.xlsx")
    chk = next(c for c in man["checks"] if c["id"] == "sales_beyond")
    assert chk["kind"] == "zero" and chk["severity"] == "error" and chk["auto"] is True
    ws = load(root / "model.xlsx")["checks"]
    r = _check_rows(ws)["sales_beyond"]
    assert ws.cell(r, 4).value == "=COUNTIF(h_sales!$B:$B,0)" and ws.cell(r, 9).value == f"=--(D{r}<>0)"
    last = max(int(common.split_cell(common.parse_ref(c["result"])[1])[1]) for c in man["checks"]
               if c["severity"] == "error")
    assert ws["C2"].value == f"=--(OR(SUM(I5:I{last})>0,COUNT(I5:I{last})<>COUNTA(B5:B{last})))" and r <= last
    # the id is reserved: a check of the spec cannot take it
    spec = base_spec()
    _sheet(spec, "checks")["checks"].append({"id": "sales_beyond", "kind": "zero", "label": "x",
                                              "actual": "SUM({blk:actual})"})
    common.write_json(root / "model.spec.json", spec)
    with pytest.raises(common.Refusal) as ei:
        specmod.load_spec(root / "model.spec.json")
    assert "check id 'sales_beyond' is the id of a check build adds" in refusal_lines(ei.value)


def test_capacity_above_the_landed_rows_is_refused_from_every_source(tmp_path: Path) -> None:
    c = base_contract()
    c["refresh"]["capacity_rows"] = 100
    root = make_model(tmp_path / "a", contract=c)
    with pytest.raises(common.Refusal) as ei:
        build.build(root / "model.spec.json")
    text = refusal_lines(ei.value)
    assert "contract sales, refresh.capacity_rows" in text and "72 landed rows" in text
    assert "rotm.py extend sizes the helper rows to the raw rows" in text
    root2 = make_model(tmp_path / "b")
    with pytest.raises(common.Refusal) as ei:
        build.build(root2 / "model.spec.json", capacity={"sales": 80})
    assert "--capacity" in refusal_lines(ei.value)
    spec = base_spec()
    spec["settings"] = {"helper_spare_rows": 3}
    root3 = make_model(tmp_path / "c", spec=spec)
    with pytest.raises(common.Refusal) as ei:
        build.build(root3 / "model.spec.json")
    assert "settings.helper_spare_rows" in refusal_lines(ei.value) and "landed rows + helper_spare_rows 3" in \
        refusal_lines(ei.value)
    rep = build.build(root2 / "model.spec.json", capacity={"sales": 70})     # fewer: a warning, as before
    assert [f.rule for f in rep.findings] == ["W-CAPACITY"]


def _nullable_model(tmp_path: Path, *, empties: tuple[int, ...] = (3, 17, 40)) -> Path:
    """The build fixture with a nullable amount: three empty amounts, a block counting through the null flag and
    a block counting the raw column, tied by a check."""
    c = _rows_only_contract(amount={"null_rule": "allowed"})
    rows = sales_rows()
    for i in empties:
        rows[i]["amount"] = ""
    src = _write_rows(tmp_path / "nullable.csv", rows)
    spec = base_spec()
    calc = _sheet(spec, "calc")
    calc["blocks"].append({"id": "n_flag", "title": "Lines with an amount (null flag)", "unit": "lines",
                           "format": "count", "rows": {"values": ["all"]},
                           "cell": "COUNTIFS({h:sales.amount_null},0)", "key": True})
    calc["blocks"].append({"id": "n_raw", "title": "Lines with an amount (raw)", "unit": "lines", "format": "count",
                           "rows": {"values": ["all"]}, "cell": "COUNT({raw:sales.amount})", "key": True})
    calc["blocks"].append({"id": "avg", "title": "Average amount", "unit": "KRW", "format": "amount",
                           "rows": {"values": ["all"]},
                           "cell": "AVERAGEIFS({h:sales.amount},{h:sales.amount_null},0)",
                           "key": True})
    checks = _sheet(spec, "checks")["checks"]
    checks.append({"id": "count_ties", "kind": "equal", "label": "COUNT through the flag equals COUNT of the raw",
                   "actual": "{cell:n_flag}", "expected": "{cell:n_raw}"})
    checks.append({"id": "avg_ties", "kind": "equal", "label": "Average through the flag equals the raw average",
                   "actual": "{cell:avg}", "expected": "AVERAGE({raw:sales.amount})"})
    _sheet(spec, "h_sales")["columns"].append({"id": "has_amount", "expr": "1-{this:amount_null}",
                                               "format": "flag"})
    return make_model(tmp_path / "m", contract=c, spec=spec, csv_path=src)


def test_null_flags_are_built_for_nullable_number_and_date_columns(tmp_path: Path) -> None:
    root = _nullable_model(tmp_path)
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    h = manifest.helper_sheet(manifest.load_manifest(root / "model.xlsx"), "sales")
    cols = {c["id"]: c for c in h["columns"]}
    flag = cols["amount_null"]
    assert flag["kind"] == "nullflag" and flag["raw_column"] == "amount" and flag["header"] == "amount empty"
    assert flag["formula"] == '=--(INDEX(raw_sales!$F:$F,A2)="")'
    ids = [c["id"] for c in h["columns"]]
    assert ids.index("amount") < ids.index("amount_null") < ids.index("month_end")   # before the derived columns
    ws = load(root / "model.xlsx")["h_sales"]
    assert ws[f"{flag['col']}1"].value == "amount empty"
    assert ws[f"{flag['col']}9"].value == '=--(INDEX(raw_sales!$F:$F,A9)="")'
    assert ws[f"{cols['has_amount']['col']}2"].value == f"=1-{flag['col']}2"           # {this:<column>_null}
    assert "inv_date_null" not in cols and "region_null" not in cols                 # never null; text


def test_a_helper_id_equal_to_a_null_flag_is_refused(tmp_path: Path) -> None:
    root = _nullable_model(tmp_path)
    spec = common.read_json(root / "model.spec.json")
    _sheet(spec, "h_sales")["columns"].append({"id": "amount_null", "expr": "{this:amount}*0"})
    common.write_json(root / "model.spec.json", spec)
    with pytest.raises(common.Refusal) as ei:
        specmod.load_spec(root / "model.spec.json")
    assert "helper column id 'amount_null' is already the null flag build adds" in refusal_lines(ei.value)


def test_check_names_helper_rows_below_the_raw_rows_and_extend_clears_them(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    m = tc.make_model(tmp_path, 10, capacity=10, landing="power_query")
    tc.rewrite_raw_sheet(m.wb, tc.sales_values(6))
    rep = check.check_model(m.root, accept_refresh=True)
    beyond = tc.find(rep, "H-BEYOND")
    assert beyond.severity == "error" and "h_sales rows 8 to 11 lie below the last raw row" in beyond.message
    assert f"extend \"{m.root.as_posix()}\" --table sales" in beyond.fix
    assert rep.exit_code == 1
    rep = check.extend_helpers(m.root, table="sales")
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    assert "cleared rows 8 to 11 (A:I)" in tc.find(rep, "H-CLEARED").message
    import openpyxl
    ws = openpyxl.load_workbook(m.wb)["h_sales"]
    assert ws["A7"].value == "=ROW()" and all(ws.cell(r, c).value is None for r in range(8, 12) for c in range(1, 10))
    h = manifest.helper_sheet(manifest.load_manifest(m.wb), "sales")
    assert h["last_row"] == 7 and h["capacity"] == 6
    assert check.coverage(m.wb, manifest.load_manifest(m.wb)) == []


@pytest.mark.excel
def test_fewer_rows_through_power_query_fail_until_extend_clears_them(tmp_path: Path) -> None:
    """A-aera-6 (Excel): a Power Query raw table over the landed CSV; a helper --({this:amount}=0) feeds a count
    block. Refreshed to fewer rows, the master check fails and check names H-BEYOND; after extend every check
    holds and the count excludes the removed rows."""
    spec = base_spec()
    _sheet(spec, "raw_sales")["landing"] = "power_query"
    _sheet(spec, "h_sales")["columns"].append({"id": "zero_amt", "expr": "--({this:amount}=0)", "format": "flag"})
    _sheet(spec, "calc")["blocks"].append({"id": "zeros", "title": "Lines with a zero amount", "unit": "lines",
                                           "format": "count", "rows": {"values": ["all"]},
                                           "cell": "SUM({h:sales.zero_amt})", "key": True})
    _sheet(spec, "checks")["checks"].append({"id": "zero_ties", "kind": "equal", "label": "Zero lines tie to raw",
                                              "actual": "{cell:zeros}", "expected": "COUNTIF({raw:sales.amount},0)"})
    root = make_model(tmp_path / "m", spec=spec, contract=_rows_only_contract())
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 0 and rep.data["power_query_attached"] == ["sales"], [f.line() for f in rep.findings]
    rows = sales_rows()[:50]
    assert land.land_csv(root, "sales", _write_rows(tmp_path / "fewer.csv", rows), force=True).exit_code == 0
    rep = check.check_model(root, refresh=True)
    assert rep.exit_code == 1
    beyond = next(f for f in rep.findings if f.rule == "H-BEYOND")
    assert "h_sales rows 52 to 73" in beyond.message
    failed = {f.data.get("check") for f in rep.findings if f.rule == "C-FAIL"}
    assert {"sales_beyond", "zero_ties", "master"} <= failed
    zeros = next(c for c in rep.data["checks"] if c["id"] == "zero_ties")
    assert zeros["actual"] == 22                                  # the 22 helper rows below the raw rows
    rep = check.extend_helpers(root, table="sales")
    assert rep.exit_code == 0 and any(f.rule == "H-CLEARED" for f in rep.findings), [f.line() for f in rep.findings]
    rep = check.check_model(root, strict=True)
    assert rep.exit_code == 0, [f.line() for f in rep.findings] + rep.notes
    zeros = next(c for c in rep.data["checks"] if c["id"] == "zero_ties")
    assert zeros["actual"] == 0 and rep.data["master"]["value"] == 0
    assert all(c["state"] == "pass" for c in rep.data["checks"] if c["severity"] == "error")


@pytest.mark.excel
def test_nullable_amount_counts_the_same_through_the_flag_and_lift_ties_out(tmp_path: Path) -> None:
    root = _nullable_model(tmp_path)
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    rep = check.check_model(root, strict=True)
    assert rep.exit_code == 0, [f.line() for f in rep.findings] + rep.notes
    by = {c["id"]: c for c in rep.data["checks"]}
    assert by["count_ties"]["actual"] == by["count_ties"]["expected"] == 69          # 72 lines, 3 empty
    assert by["avg_ties"]["state"] == "pass"
    report = lift.lift_model(root, run=True)
    assert report.exit_code == 0, [f.line() for f in report.findings] + report.notes
    tie = report.data["tie_out"]
    assert tie["status"] == "tie" and tie["counts"]["differ"] == 0


# ============================================================================= 2. A-aera-5, C-sol-5

def test_a_cycle_of_two_helper_columns_is_refused_with_both_paths(tmp_path: Path) -> None:
    spec = base_spec()
    h = _sheet(spec, "h_sales")
    h["columns"] += [{"id": "tax", "expr": "{this:gross}*{this:amount}"},
                     {"id": "gross", "expr": "{this:tax}+{this:amount}"}]
    si = spec["sheets"].index(h)
    root = make_model(tmp_path / "m", spec=spec)
    with pytest.raises(common.Refusal) as ei:
        build.build(root / "model.spec.json", strict=True)
    text = refusal_lines(ei.value)
    assert f"sheets[{si}].columns[2].expr" in text and f"sheets[{si}].columns[3].expr" in text
    assert "circular reference" in text and "h_sales.tax" in text and "h_sales.gross" in text
    assert not (root / "model.xlsx").exists()
    proc = subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(tc.ROTM), "build", str(root)],
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "circular reference" in proc.stdout + proc.stderr


def test_a_cycle_of_three_blocks_is_refused(tmp_path: Path) -> None:
    spec = base_spec()
    calc = _sheet(spec, "calc")
    calc["blocks"] += [
        {"id": "c1", "rows": {"values": ["a"]}, "cell": "{blk:c3.total}+1", "total": {"row": True, "col": True}},
        {"id": "c2", "rows": {"values": ["a"]}, "cell": "SUM({blk:c1})", "total": {"row": True, "col": True}},
        {"id": "c3", "rows": {"values": ["a"]}, "cell": "SUM({blk:c2})", "total": {"row": True, "col": True}}]
    root = make_model(tmp_path / "m", spec=spec)
    with pytest.raises(common.Refusal) as ei:
        specmod.load_spec(root / "model.spec.json")
    text = refusal_lines(ei.value)
    si = spec["sheets"].index(calc)
    n = len(calc["blocks"])
    for k in (n - 3, n - 2, n - 1):
        assert f"sheets[{si}].blocks[{k}].cell" in text
    assert "block 'c1'" in text and "block 'c3'" in text


def test_no_cycle_for_prev_first_and_a_key_output_read_by_a_later_item(tmp_path: Path) -> None:
    spec = base_spec()
    _sheet(spec, "calc")["blocks"].append(
        {"id": "run", "title": "Running sum", "cols": {"same_as": "actual"}, "items": [
            {"id": "a", "label": "A", "expr": "SUM({gcol:actual})"},
            {"id": "s", "label": "Sum", "first": "{r:a}", "expr": "{prev}+{r:a}", "key": True},
            {"id": "t", "label": "Twice", "expr": "{cell:run.s}*2"}]})
    root = make_model(tmp_path / "m", spec=spec)
    s = specmod.load_spec(root / "model.spec.json")
    assert specmod.find_cycles(s) == []


def test_an_item_cycle_in_one_column_is_refused(tmp_path: Path) -> None:
    spec = base_spec()
    _sheet(spec, "calc")["blocks"].append(
        {"id": "loop", "cols": {"same_as": "actual"}, "items": [
            {"id": "a", "label": "A", "expr": "{r:b}+1"}, {"id": "b", "label": "B", "expr": "{r:a}+1"}]})
    root = make_model(tmp_path / "m", spec=spec)
    with pytest.raises(common.Refusal) as ei:
        specmod.load_spec(root / "model.spec.json")
    assert "line item 'a' of block 'loop'" in refusal_lines(ei.value)


def test_check_reports_cells_without_a_value_as_V_NO_VALUE(tmp_path: Path, monkeypatch) -> None:
    m = tc.make_model(tmp_path, 10)
    cells = [["calc", "D20", "no value (circular reference)"], ["calc", "E20", "no value"], ["calc", "D21", "#DIV/0!"]]
    fake_excel(monkeypatch, {}, cells)
    rep = check.check_model(m.root, strict=True)
    nv = [f for f in rep.findings if f.rule == "V-NO-VALUE"]
    assert [(f.sheet, f.cell) for f in nv] == [("calc", "D20"), ("calc", "E20")]
    assert all(f.severity == "error" and "trace" in f.fix for f in nv)
    assert [(f.sheet, f.cell) for f in rep.findings if f.rule == "V-ERROR"] == [("calc", "D21")]
    assert rep.exit_code == 1 and check.RULES["V-NO-VALUE"][0] == "error"


@pytest.mark.excel
def test_a_hand_made_circular_pair_fails_check_strict(tmp_path: Path) -> None:
    import openpyxl
    root = make_model(tmp_path / "m")
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    wb = openpyxl.load_workbook(root / "model.xlsx")
    ws = wb["notes"]
    ws["B40"], ws["C40"] = "=C40+1", "=B40+1"
    wb.save(root / "model.xlsx")
    rep = check.check_model(root, strict=True)
    assert rep.exit_code == 1
    cells = {(f.sheet, f.cell) for f in rep.findings if f.rule == "V-NO-VALUE"}
    assert {("notes", "B40"), ("notes", "C40")} <= cells, [f.line() for f in rep.findings]


# ============================================================================= 3. A-aera-9, C-sol-4

TRICKY = ["001", "1", "Case", "CASE", "M*", "Monday", "A?", "AB", "12345678901234567890", "12345678901234599999"]


def test_check_raw_compares_keys_exactly() -> None:
    contract = {"table": "k", "key": ["id"], "grain": "one row per id", "expect": {"rows": {"min": 1, "max": 99}},
                "columns": [{"name": "id", "type": "text", "null_rule": "never"},
                            {"name": "amount", "type": "number", "null_rule": "never"}]}
    rows = [[k, float(i)] for i, k in enumerate(TRICKY)]
    assert [f.rule for f in check.check_raw(check.SheetRows("k", ["id", "amount"], rows), contract)] == []
    rows.append(["Case", 99.0])
    f = check.check_raw(check.SheetRows("k", ["id", "amount"], rows), contract)
    assert [x.rule for x in f] == ["D-KEY"]
    assert "row 12 repeats row 4" in f[0].message and "row 12 repeats row 4" in f[0].fix
    assert "look at these rows" in f[0].fix and "Do not remove rows" in f[0].fix
    assert "remove the duplicates" not in f[0].fix and f[0].data["rows"] == [{"row": 12, "first_row": 4}]


def test_the_keys_check_fix_says_look_at_the_rows(tmp_path: Path) -> None:
    m = tc.make_model(tmp_path, 10)
    fix = check._fix_for_check("sales_keys", m.root, manifest.load_manifest(m.wb))
    assert "look at those rows with the user" in fix and "--key-repeats" in fix and "Do not remove rows" in fix


def test_lift_dup_rule_is_exact_and_case_sensitive() -> None:
    layout = {"helpers": {"k": {"sheet": "h_k", "first_row": 2, "last_row": 4,
                                "columns": {"dup": {"id": "dup", "col": "D", "kind": "dup"}}}},
              "raw": {}, "blocks": {}, "checks": {}, "inputs": {}, "cells": {}}
    info = {"inputs": {}, "tables": {"k": {"columns": ["id", "amount"], "types": ["text", "number"], "key": ["id"],
                                            "expect": {}, "never_null": ["amount"], "snapshot": "raw/k.csv"}}}
    mdl = lift.Model(info, layout)
    mdl.raw["k"] = {"header": ["id", "amount"], "rows": [["Case", 1.0], ["CASE", 2.0], ["Case", 3.0]], "source": "x"}
    h = mdl.helpers("k", ["id", "amount"], ["id"])
    assert h.cols["dup"] == [0, 0, 1]


def _key_model(tmp_path: Path, name: str, ids: list[str], *, two_columns: bool, repeats: int = 0) -> Path:
    root = tmp_path / name
    modelfolder.init_model(root, name=f"keys_{name}")
    c = {"rotm_contract": 1, "table": "k", "raw_sheet": "raw_k", "excel_table": "tbl_raw_k",
         "purpose": "key equality test", "source": dict(base_contract()["source"]),
         "grain": "one row per id and part" if two_columns else "one row per id",
         "key": ["id", "part"] if two_columns else ["id"],
         "columns": [{"name": "id", "type": "text", "unit": None, "null_rule": "never", "allowed": None,
                      "source_expression": "id"},
                     {"name": "part", "type": "text", "unit": None, "null_rule": "never", "allowed": None,
                      "source_expression": "part"},
                     {"name": "amount", "type": "number", "unit": "KRW", "null_rule": "never", "allowed": None,
                      "source_expression": "amount"}],
         "expect": {"rows": {"min": 1, "max": 1000}}, "refresh": {"frequency": None, "capacity_rows": None,
                                                                    "allow_empty": False}}
    if repeats:
        c["expect"]["key_repeats"] = {"max": repeats, "note": "one line delivered twice"}
    from test_build import agree
    common.write_json(root / "contracts" / "k.contract.json", agree(c))
    src = tmp_path / f"{name}.csv"
    with open(src, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["id", "part", "amount"])
        for i, k in enumerate(ids):
            w.writerow([k, "x", i + 1])
    rep = land.land_csv(root, "k", src)
    assert rep.exit_code == 0, [x.line() for x in rep.findings]
    spec = {"rotm_spec": 1, "name": f"keys_{name}", "title": "Key equality", "sheets": [
        {"name": "raw_k", "type": "raw", "table": "k"},
        {"name": "h_k", "type": "helpers", "table": "k"},
        {"name": "calc", "type": "calc", "blocks": [
            {"id": "tot", "title": "Total", "unit": "KRW", "rows": {"values": ["all"]}, "cell": "SUM({h:k.amount})",
             "key": True}]},
        {"name": "checks", "type": "checks", "checks": [
            {"id": "tot_ties", "kind": "equal", "label": "Total ties to raw", "actual": "{cell:tot}",
             "expected": "SUM({raw:k.amount})"}]}]}
    common.write_json(root / "model.spec.json", spec)
    return root


def test_the_dup_helper_is_written_for_keys_with_wildcards(tmp_path: Path) -> None:
    root = _key_model(tmp_path, "single", TRICKY, two_columns=False)
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    h = manifest.helper_sheet(manifest.load_manifest(root / "model.xlsx"), "k")
    dup = next(c for c in h["columns"] if c["id"] == "dup")
    assert dup["formula"] == "=--(SUMPRODUCT(--EXACT(C$2:C2,C2))>1)*B2"


@pytest.mark.excel
@pytest.mark.parametrize("two_columns", [False, True], ids=["one_column_key", "two_column_key"])
def test_exact_keys_in_excel(tmp_path: Path, two_columns: bool) -> None:
    import openpyxl
    ids = TRICKY + ["Case"]                          # the last line repeats the key 'Case' (agreed as known)
    root = _key_model(tmp_path, "k2" if two_columns else "k1", ids, two_columns=two_columns, repeats=1)
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    rep = check.check_model(root, strict=True)
    assert rep.exit_code == 0, [f.line() for f in rep.findings] + rep.notes
    keys = next(c for c in rep.data["checks"] if c["id"] == "k_keys")
    assert keys["actual"] == 1 and keys["state"] == "pass" and rep.data["master"]["value"] == 0
    h = manifest.helper_sheet(manifest.load_manifest(root / "model.xlsx"), "k")
    col = next(c["col"] for c in h["columns"] if c["id"] == "dup")
    ws = openpyxl.load_workbook(root / "model.xlsx", data_only=True)["h_k"]
    dup = [ws[f"{col}{r}"].value for r in range(2, 2 + len(ids))]
    assert dup == [0] * len(TRICKY) + [1]           # 1 on the later copy only
    snap = land.read_snapshot(root, "k")
    c = common.read_json(root / "contracts" / "k.contract.json")
    c["expect"].pop("key_repeats")
    f = [x for x in check.check_raw(snap, c) if x.rule == "D-KEY"]
    assert f and "row 12 repeats row 4" in f[0].message


# ============================================================================= 4. A-aera-7, C-sol-3

def test_build_takes_the_accepted_control_total_for_its_snapshot(tmp_path: Path) -> None:
    root = make_model(tmp_path / "m")
    snap = land.read_snapshot(root, "sales")
    contract_before = (root / "contracts" / "sales.contract.json").read_bytes()
    controls.record_acceptance(root, "sales", digest=snap.digest, rows=len(snap.rows),
                               totals=[{"column": "amount", "value": 31435000.5, "tolerance": 0}],
                               given=["sales.amount=31435000.5"])
    assert build.build(root / "model.spec.json").exit_code in (0, 1)
    man = manifest.load_manifest(root / "model.xlsx")
    sheet, cell = common.parse_ref(man["inputs"]["sales_total_amount"])
    ws = load(root / "model.xlsx")[sheet]
    assert ws[cell].value == 31435000.5
    c, r = common.split_cell(cell)
    notes = [ws.cell(r, k).value for k in range(c + 1, ws.max_column + 1)]
    assert any(isinstance(v, str) and "raw/sales.controls.json" in v for v in notes)
    assert (root / "contracts" / "sales.contract.json").read_bytes() == contract_before
    # another snapshot: the contract's total again
    root2 = make_model(tmp_path / "m2")
    controls.record_acceptance(root2, "sales", digest="sha256:" + "0" * 64, rows=1,
                               totals=[{"column": "amount", "value": 5.0, "tolerance": 0}], given=[])
    build.build(root2 / "model.spec.json")
    ws2 = load(root2 / "model.xlsx")[sheet]
    assert ws2[cell].value == 31435000


def test_check_accept_records_the_control_totals_once(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    total10 = sum(100 + 7.5 * i for i in range(10))
    total12 = sum(100 + 7.5 * i for i in range(12))
    m = tc.make_model(tmp_path, 10, capacity=12, total=total10, landing="power_query", query_table=True)
    contract_before = (m.root / "contracts" / "sales.contract.json").read_bytes()
    tc.rewrite_raw_sheet(m.wb, tc.sales_values(12))
    text = f"sales.amount={total12}"
    rep = check.check_model(m.root, accept_refresh=True, controls={"sales.amount": total12}, control_texts=[text])
    assert "R-ACCEPTED" in tc.rules(rep) and "D-TOTAL" not in tc.rules(rep)
    data = common.read_json(controls.path(m.root, "sales"))
    digest = manifest.raw_sheet(manifest.load_manifest(m.wb), "sales")["digest"]
    assert [e["digest"] for e in data["accepted"]] == [digest]
    entry = data["accepted"][0]
    assert entry["rows"] == 12 and entry["given"] == [text]
    assert [(t["column"], float(t["value"]), float(t["tolerance"])) for t in entry["totals"]] == \
        [("amount", total12, 0.0)]
    assert controls.totals_for(m.root, "sales", digest)[0]["value"] == total12
    # the acceptance run again (as after an interruption) gives the same record
    rep = check.check_model(m.root, accept_refresh=True, controls={"sales.amount": total12}, control_texts=[text])
    assert "D-TOTAL" not in tc.rules(rep)
    again = common.read_json(controls.path(m.root, "sales"))
    assert again == data
    assert (m.root / "contracts" / "sales.contract.json").read_bytes() == contract_before
    # a later check without --control takes the accepted total from the record (not the contract)
    rep = check.check_model(m.root)
    assert "D-TOTAL" not in tc.rules(rep), [f.line() for f in rep.findings]


@pytest.mark.excel
def test_refresh_accept_with_a_new_control_then_rebuild_and_check_hold(tmp_path: Path) -> None:
    import openpyxl
    spec = base_spec()
    _sheet(spec, "raw_sales")["landing"] = "power_query"
    root = make_model(tmp_path / "m", spec=spec)
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 0 and rep.data["power_query_attached"] == ["sales"], [f.line() for f in rep.findings]
    rows = sales_rows()
    more = rows + [dict(r, invoice_id=f"9{r['invoice_id'][1:]}") for r in rows[:10]]
    total = sum(float(r["amount"]) for r in more)
    _write_rows(root / "raw" / "sales.csv", more)        # the source changed; the query reads this file
    text = f"sales.amount={total:.0f}"
    rep = check.check_model(root, refresh=True, accept_refresh=True, controls={"sales.amount": total},
                            control_texts=[text])
    assert "R-ACCEPTED" in tc.rules(rep) and "D-CONTROL-SET" in tc.rules(rep), [f.line() for f in rep.findings]
    assert check.extend_helpers(root, table="sales").exit_code == 0
    rep = check.check_model(root, accept_refresh=True, controls={"sales.amount": total}, control_texts=[text])
    assert rep.exit_code == 0, [f.line() for f in rep.findings] + rep.notes
    assert len(common.read_json(controls.path(root, "sales"))["accepted"]) == 1
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    rep = check.check_model(root, strict=True)
    assert rep.exit_code == 0, [f.line() for f in rep.findings] + rep.notes
    man = manifest.load_manifest(root / "model.xlsx")
    sheet, cell = common.parse_ref(man["inputs"]["sales_total_amount"])
    assert openpyxl.load_workbook(root / "model.xlsx", data_only=True)[sheet][cell].value == total


# ============================================================================= 5. A-aera-1

def test_an_audit_does_not_make_an_edited_workbook_ours(tmp_path: Path) -> None:
    import openpyxl
    root = make_model(tmp_path / "m")
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    wb = openpyxl.load_workbook(root / "model.xlsx")
    wb["notes"]["B30"] = "typed by hand in Excel"
    wb.save(root / "model.xlsx")
    audit.audit_workbook(root / "model.xlsx", strict=True, record=True)
    assert modelfolder.read_status(root)["workbooks"]["model.xlsx"]["audit"]["sha256"] == \
        common.file_sha256(root / "model.xlsx")
    with pytest.raises(common.Refusal) as ei:
        build.build(root / "model.spec.json", strict=True)
    assert "changed after rotm.py last wrote it" in ei.value.message
    rep = build.build(root / "model.spec.json", strict=True, force=True)
    assert rep.exit_code == 0
    backups = list(root.glob("model.bak_*.xlsx"))
    assert len(backups) == 1
    assert openpyxl.load_workbook(backups[0])["notes"]["B30"].value == "typed by hand in Excel"


def test_known_hashes_come_from_writing_commands_only(tmp_path: Path) -> None:
    # v3 (A-aera-2): a writer's output counts only when its input hash is owned (the chain from build's output);
    # a record without an input hash does not count
    root = make_model(tmp_path / "m")
    wb = root / "model.xlsx"
    h = {k: c * 64 for k, c in zip(("build", "check", "check_ro", "recalc", "audit", "render", "land", "landpq",
                                     "extend"), "abcdef012")}
    modelfolder.record_step(root, "model.xlsx", "build", {"sha256": h["build"]})
    modelfolder.record_step(root, "model.xlsx", "audit", {"sha256": h["audit"]})
    modelfolder.record_step(root, "model.xlsx", "render", {"sha256": h["render"]})
    modelfolder.record_step(root, "model.xlsx", "recalc", {"input_sha256": h["audit"], "output_sha256": h["recalc"]})
    modelfolder.record_step(root, "model.xlsx", "extend", {"output_sha256": h["extend"]})
    modelfolder.record_step(root, "model.xlsx", "check", {"recalculated": False, "input_sha256": h["check_ro"],
                                                          "output_sha256": h["check_ro"]})
    modelfolder.record_step(root, "model.xlsx", "land", {"power_query": True, "sha256": h["landpq"]})
    assert build._known_hashes(root, wb) == {h["build"]}
    modelfolder.record_step(root, "model.xlsx", "land", {"power_query": True, "input_sha256": h["build"],
                                                         "sha256": h["landpq"]})
    modelfolder.record_step(root, "model.xlsx", "check", {"recalculated": True, "input_sha256": h["landpq"],
                                                          "output_sha256": h["check"]})
    modelfolder.record_step(root, "model.xlsx", "extend", {"input_sha256": h["check"], "output_sha256": h["extend"]})
    modelfolder.record_step(root, "model.xlsx", "land", {"table": "sales", "rows": 72})
    assert build._known_hashes(root, wb) == {h["build"], h["landpq"], h["check"], h["extend"]}


# ============================================================================= 6. A-aera-10 (build part)

ODD_TEXTS = ["#N/A", "=1+1", "0012", "TRUE"]


def _odd_text_model(tmp_path: Path) -> Path:
    rows = sales_rows()
    for i, v in enumerate(ODD_TEXTS):
        rows[i]["product"] = v
    return make_model(tmp_path / "m", csv_path=_write_rows(tmp_path / "odd.csv", rows))


def test_raw_text_values_are_text_cells_whatever_they_look_like(tmp_path: Path) -> None:
    root = _odd_text_model(tmp_path)
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    ws = load(root / "model.xlsx")["raw_sales"]
    for i, v in enumerate(ODD_TEXTS):
        cell = ws[f"E{i + 2}"]
        assert cell.value == v and cell.data_type == "s", (v, cell.value, cell.data_type)


@pytest.mark.excel
def test_raw_text_values_hold_no_error_after_recalculation(tmp_path: Path) -> None:
    from lib import excel
    root = _odd_text_model(tmp_path)
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    res = excel.recalc(root / "model.xlsx", read=["raw_sales!E2:E5", "h_sales!G2:G5"])
    assert res.ok, res.errors
    assert res.error_count == 0, res.error_cells
    assert [r[0] for r in res.values["raw_sales!E2:E5"]] == ODD_TEXTS
    assert [r[0] for r in res.values["h_sales!G2:G5"]] == ODD_TEXTS


# ============================================================================= 7. supporting A-aera-4

def test_control_total_is_exact_against_a_large_total() -> None:
    contract = {"table": "k", "key": ["id"], "grain": "one row per id",
                "expect": {"rows": {"min": 1, "max": 99}, "totals": [{"column": "amount", "value": 1_000_000_000_000,
                                                                       "tolerance": 0}]},
                "columns": [{"name": "id", "type": "text", "null_rule": "never"},
                            {"name": "amount", "type": "number", "null_rule": "never"}]}
    rows = [["a", 999_999_999_000.0], ["b", 500.0]]
    f = check.check_raw(check.SheetRows("k", ["id", "amount"], rows), contract)
    assert [x.rule for x in f] == ["D-TOTAL"] and f[0].data["difference"] == -500
    rows[1][1] = 1000.0
    assert check.check_raw(check.SheetRows("k", ["id", "amount"], rows), contract) == []


def test_a_control_value_is_compared_exactly_with_the_input_cell(tmp_path: Path, monkeypatch) -> None:
    total = 1_000_000_000_000.0
    m = tc.make_model(tmp_path, 10, total=total - 500)
    man = manifest.load_manifest(m.wb)
    ref = next(c["result"] for c in man["checks"] if c["id"] == "sales_total_amount")
    row = common.split_cell(common.parse_ref(ref)[1])[1]
    detail = f"checks!D{row}:H{row}"
    fake_excel(monkeypatch, {detail: [[total - 500, total - 500, None, 0, 0]]})
    rep = check.check_model(m.root, controls={"sales.amount": total})
    item = rep.data["tables"]["sales"]["control_inputs"]
    assert item and item[0]["given"] == total and item[0]["workbook_value"] == total - 500


# ============================================================================= 8. supporting A-aera-2

def test_check_refresh_refuses_a_query_written_for_another_contract(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    m = tc.make_model(tmp_path, 10, landing="power_query")
    rep = check.check_model(m.root, refresh=True)                  # nothing recorded: a note only
    assert any("records no contract hash" in n for n in rep.notes)
    agreed = common.read_json(m.root / "contracts" / "sales.contract.json")["agreement"]["contract_sha256"]
    man = manifest.load_manifest(m.wb)
    manifest.raw_sheet(man, "sales")["query_contract_sha256"] = "f" * 64
    manifest.write_manifest(m.wb, man)
    before = common.file_sha256(m.wb)
    with pytest.raises(common.Refusal) as ei:
        check.check_model(m.root, refresh=True)
    text = refusal_lines(ei.value)
    assert "nothing was refreshed" in ei.value.message and "raw_sales" in text
    assert "land" in text and "--table sales" in text and "--power-query" in text
    assert common.file_sha256(m.wb) == before
    manifest.raw_sheet(man, "sales")["query_contract_sha256"] = agreed
    manifest.write_manifest(m.wb, man)
    assert check.check_model(m.root, refresh=True).exit_code in (1, 3)       # not refused
    # the stamp of queries/<t>.pq.m when the manifest records none
    manifest.raw_sheet(man, "sales").pop("query_contract_sha256")
    manifest.write_manifest(m.wb, man)
    (m.root / "queries").mkdir(exist_ok=True)
    (m.root / "queries" / "sales.pq.m").write_text(common.stamp_query("let\n  x = 1\nin\n  x\n", "e" * 64, "//"),
                                                   encoding="utf-8")
    with pytest.raises(common.Refusal) as ei:
        check.check_model(m.root, refresh=True)
    assert "the stamp of queries/sales.pq.m" in refusal_lines(ei.value)


# ============================================================================= 9. supporting A-aera-14

def test_a_block_read_only_by_an_alert_gets_W_NO_CHECK(tmp_path: Path) -> None:
    spec = base_spec()
    _sheet(spec, "calc")["blocks"].append({"id": "extra", "title": "Extra", "rows": {"values": ["all"]},
                                           "cell": "SUM({blk:actual})"})
    _sheet(spec, "checks")["checks"].append({"id": "extra_alert", "kind": "zero", "severity": "alert",
                                              "label": "Extra is zero", "actual": "{blk:extra}"})
    root = make_model(tmp_path / "m", spec=spec)
    rep = build.build(root / "model.spec.json")
    w = [f for f in rep.findings if f.rule == "W-NO-CHECK"]
    assert len(w) == 1 and "block 'extra'" in w[0].message and "an alert does not count" in w[0].message
    blocks = manifest.sheet_entry(manifest.load_manifest(root / "model.xlsx"), "calc")["blocks"]
    assert next(b for b in blocks if b["id"] == "extra")["checks"] == []


def test_checks_reference_says_an_alert_is_not_a_blocks_check(skill_dir: Path) -> None:
    text = (skill_dir / "references" / "checks.md").read_text(encoding="utf-8")
    assert "An alert does not count as a block's check" in text
    assert "Counting the numeric cells of a block is not an independent total" in text


# ============================================================================= 10. supporting J-4 (b), (d)

def test_a_spec_check_without_format_takes_the_format_of_what_it_reads(tmp_path: Path) -> None:
    root = make_model(tmp_path / "m")
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    wb = load(root / "model.xlsx")
    ws = wb["checks"]
    rows = _check_rows(ws)
    block_fmt = wb["calc"]["J10"].number_format               # block actual (format amount), its grand total
    rate_fmt = wb["summary"]["F11"].number_format             # measure pct (format rate)
    r = rows["actual_ties_raw"]
    assert [ws.cell(r, c).number_format for c in (4, 5, 7)] == [block_fmt] * 3
    r = rows["pct_ties"]
    assert [ws.cell(r, c).number_format for c in (4, 5, 7)] == [rate_fmt] * 3
    assert block_fmt != rate_fmt


def test_check_record_counts_error_checks_and_alerts_apart(tmp_path: Path, monkeypatch) -> None:
    root = make_model(tmp_path / "m")
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    man = manifest.load_manifest(root / "model.xlsx")
    errors = [c for c in man["checks"] if c["severity"] == "error"]
    alerts = [c for c in man["checks"] if c["severity"] == "alert"]
    assert len(alerts) == 1
    values = {c["result"]: 0 for c in man["checks"]}
    values[alerts[0]["result"]] = 1
    values[errors[0]["result"]] = 1
    fake_excel(monkeypatch, values)
    rep = check.check_model(root)
    rec = modelfolder.read_status(root)["workbooks"]["model.xlsx"]["check"]
    assert rec["checks"] == len(errors) and rec["failed"] == 1
    assert rec["alerts"] == 1 and rec["alerts_shown"] == 1
    line = f"checks: {len(errors) - 1} of {len(errors)} hold; alerts: 1 of 1 show a value"
    assert line in rep.notes
    assert check.summary_line(3, 3) == "checks: 3 of 3 hold"

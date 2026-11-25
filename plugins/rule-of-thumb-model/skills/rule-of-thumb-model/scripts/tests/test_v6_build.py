"""Tests of the v6 build changes (package P3 of the v6 plan). Owner: P3.

Entries: [C-sol-6, J-1] part 2 (the fetch of a date or datetime column that the contract allows to be empty gets the
number format date_or_empty or datetime_or_empty, whose zero section shows nothing, so an empty date is not shown as
1900-01-00), [A-aera-2] (build's carry-over of a query attached by hand records query_formula_sha256 of the formula
read back); supporting [A-aera-6] (F-ARGS is refused by build at its JSON path). Tests marked excel drive Excel
through lib.excel. test_empty_date_fetch_shows_nothing_after_excel is a fixed name (v6 plan section 5.3).
"""
from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path

import pytest

from lib import build, check, common, dictionary, formula, land, manifest
from test_build import DATA, base_contract, base_spec, make_model, sales_rows
import test_check as tc

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent


def out(r) -> str:
    return ((r.stdout or "") + (r.stderr or ""))[-4000:]


def write_rows(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return path


def nullable_date_model(root: Path, *, ctype: str = "date", null_rule: str = "allowed", empty_row: int = 1) -> Path:
    """The build fixture (tests/data/build) with inv_date of type `ctype` and null rule `null_rule`; the row
    `empty_row` (0-based) has no date and an amount of 0, so every check still holds (the contract's total is the
    sum of the other rows)."""
    c = base_contract()
    for col in c["columns"]:
        if col["name"] == "inv_date":
            col["type"] = ctype
            col["null_rule"] = null_rule
    rows = sales_rows()
    if null_rule == "allowed":
        rows[empty_row]["inv_date"] = ""
        rows[empty_row]["amount"] = "0"
    total = sum((Decimal(r["amount"]) for r in rows), Decimal(0))
    c["expect"]["totals"][0]["value"] = int(total) if total == total.to_integral_value() else float(total)
    src = write_rows(root.parent / f"{root.name}_sales.csv", rows)
    return make_model(root, contract=c, csv_path=src)


def helper_column(wb: Path, col_id: str) -> dict:
    h = manifest.helper_sheet(manifest.load_manifest(wb), "sales")
    return next(c for c in h["columns"] if c["id"] == col_id)


# ============================================================================= [C-sol-6, J-1] part 2: empty dates

def test_the_dictionary_holds_the_empty_date_formats() -> None:
    d = dictionary.load_dictionary()
    assert d.number_format("date_or_empty") == "yyyy-mm-dd;;"
    assert d.number_format("datetime_or_empty") == "yyyy-mm-dd hh:mm:ss;;"
    data = json.loads((SKILL / "references" / "style_dictionary.json").read_text(encoding="utf-8"))
    for key in ("date_or_empty", "datetime_or_empty"):
        assert data["number_formats"][key]["source"] == ("PLUGIN (r5 verdict J-1: an empty date shows as an empty "
                                                         "cell, not 1900-01-00)")
        assert build.FORMAT_WIDTH[key] == build.FORMAT_WIDTH[key.replace("_or_empty", "")]
    assert dictionary.source_problems(data) == []


def test_fetch_of_a_date_that_may_be_empty_gets_date_or_empty(tmp_path: Path, monkeypatch) -> None:
    import openpyxl
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = nullable_date_model(tmp_path / "m")
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    wb = root / "model.xlsx"
    fetch = helper_column(wb, "inv_date")
    assert fetch["kind"] == "fetch" and fetch["format"] == "date_or_empty"
    flag = helper_column(wb, "inv_date_null")                            # the null flag column stays as it was
    assert flag["kind"] == "nullflag" and flag["format"] == "flag"
    assert flag["formula"] == '=--(INDEX(raw_sales!$C:$C,A2)="")'
    assert fetch["formula"] == "=INDEX(raw_sales!$C:$C,A2)"              # the fetched value is unchanged (0 when empty)
    ws = openpyxl.load_workbook(wb)["h_sales"]
    col = fetch["col"]
    assert ws[f"{col}2"].number_format == ws[f"{col}3"].number_format == "yyyy-mm-dd;;"
    raw = manifest.raw_sheet(manifest.load_manifest(wb), "sales")
    assert raw["formats"][raw["columns"].index("inv_date")] == "date"   # the raw sheet keeps the date format


def test_fetch_of_a_date_that_is_never_empty_keeps_date(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = make_model(tmp_path / "m")
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    assert helper_column(root / "model.xlsx", "inv_date")["format"] == "date"


def test_fetch_of_a_datetime_that_may_be_empty_gets_datetime_or_empty(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = nullable_date_model(tmp_path / "m", ctype="datetime")
    rep = build.build(root / "model.spec.json")
    assert rep.counts()["errors"] == 0, [f.line() for f in rep.findings]
    assert helper_column(root / "model.xlsx", "inv_date")["format"] == "datetime_or_empty"


def test_a_date_format_the_contract_names_is_kept(tmp_path: Path, monkeypatch) -> None:
    """A contract format other than date or datetime (here month) is kept for the fetch: only the dictionary's own
    date formats are replaced."""
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = nullable_date_model(tmp_path / "m")
    path = root / "contracts" / "sales.contract.json"
    c = common.read_json(path)
    next(col for col in c["columns"] if col["name"] == "inv_date")["format"] = "month"
    from test_build import agree
    common.write_json(path, agree(c))
    rep = build.build(root / "model.spec.json")
    assert rep.counts()["errors"] == 0, [f.line() for f in rep.findings]
    assert helper_column(root / "model.xlsx", "inv_date")["format"] == "month"


@pytest.mark.excel
def test_empty_date_fetch_shows_nothing_after_excel(tmp_path: Path) -> None:
    from lib import excel
    root = nullable_date_model(tmp_path / "m", empty_row=1)
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    wb = root / "model.xlsx"
    col = helper_column(wb, "inv_date")["col"]
    flag = helper_column(wb, "inv_date_null")["col"]
    chk = check.check_model(root)                                         # Excel recalculates and saves
    assert chk.data["recalculated"] is True, [f.line() for f in chk.findings] + chk.notes
    res = excel.run_job(wb, [{"op": "read", "refs": [f"h_sales!{col}2", f"h_sales!{col}3", f"h_sales!{flag}3",
                                                     "raw_sales!C3"]}])
    assert res.ok, res.message()
    got = {v["ref"]: v for v in res.step("read")["values"]}
    empty, filled = got[f"h_sales!{col}3"], got[f"h_sales!{col}2"]
    assert got["raw_sales!C3"].get("value") in (None, "")                # the raw date is empty
    assert empty["value"] == 0 and empty["text"] == "", empty             # 0, shown as an empty cell
    assert got[f"h_sales!{flag}3"]["value"] == 1                          # the null flag marks the empty date
    first = sales_rows()[0]["inv_date"]
    assert filled["text"] == first, filled                               # a filled date shows as yyyy-mm-dd
    aud = tc.run_cli("audit", str(root), "--strict")
    assert aud.returncode == 0, out(aud)


# ============================================================================= [A-aera-2] carry-over of a hand query

@pytest.mark.excel
def test_hand_query_carried_over_records_the_formula_hash(tmp_path: Path) -> None:
    from lib import excel
    root = make_model(tmp_path / "m")
    wb = root / "model.xlsx"
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    c = common.read_json(root / "contracts" / "sales.contract.json")
    m = land.m_text(c, csv_path=root / "raw" / "sales.csv")
    res = excel.pq_attach(wb, sheet="raw_sales", table_name="tbl_finance", query_name="q_finance", m_text=m)
    assert res.ok, res.message()
    acc = tc.run_cli("check", str(root), "--accept-refresh")
    assert acc.returncode == 0, out(acc)
    e = manifest.raw_sheet(manifest.load_manifest(wb), "sales")
    assert e["query_attach"] == "hand" and "query_formula_sha256" not in e
    reb = tc.run_cli("build", str(root), "--strict", "--force")
    assert reb.returncode == 0, out(reb)
    e = manifest.raw_sheet(manifest.load_manifest(wb), "sales")
    assert e["query_attach"] == "hand" and e["query_name"] == "q_finance"
    inv = excel.inventory(wb)
    now = next(q["formula"] for q in inv["query_formulas"] if q["name"] == "q_finance")
    assert e.get("query_formula_sha256") == land.query_formula_sha256(now) == land.query_formula_sha256(m), e
    ref = check.check_model(root, refresh=True)                           # compared, equal: refreshed
    assert ref.data["refreshed"] is True and "X-QUERY-CHANGED" not in [f.rule for f in ref.findings]


# ============================================================================= supporting [A-aera-6] F-ARGS

def test_f_args_issue_is_refused_at_its_json_path(tmp_path: Path, monkeypatch) -> None:
    """build refuses an F-ARGS issue (P1's rule) at the JSON path of the expression, with the issue's own fix. The
    issue is given by a stand-in for formula.check_formula, so the test does not need P1's argument counts."""
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    spec = base_spec()
    h = next(s for s in spec["sheets"] if s["type"] == "helpers")
    k = next(i for i, col in enumerate(h["columns"]) if col["id"] == "amount_k")
    path = f"sheets[{spec['sheets'].index(h)}].columns[{k}].expr"
    root = make_model(tmp_path / "m", spec=spec)
    real = formula.check_formula
    seen: list[str] = []

    def fake(f: str, *args, **kwargs):
        issues = list(real(f, *args, **kwargs))
        if "/inputs!" in f and f.startswith("=H"):                        # amount_k: {this:amount}/{in:scale}
            seen.append(f)
            issues.append(formula.Issue("F-ARGS", "error", "IF has 4 argument(s); it takes 2 or 3 (IF(A1>0,1,0,1))",
                                        "give IF 2 or 3 arguments (references/model-spec-format.md, \"Argument "
                                        "counts\")", {"function": "IF", "count": 4, "allowed": "2 or 3"}))
        return issues

    monkeypatch.setattr(formula, "check_formula", fake)
    with pytest.raises(common.Refusal) as exc:
        build.build(root / "model.spec.json", strict=True)
    assert seen, "the stand-in did not see the helper formula"
    text = "\n".join([exc.value.message] + list(exc.value.details))
    assert f"{path}: IF has 4 argument(s); it takes 2 or 3 (IF(A1>0,1,0,1)) (in h_sales!" in text, text
    assert "give IF 2 or 3 arguments (references/model-spec-format.md, \"Argument counts\")" in text
    assert not (root / "model.xlsx").exists()
    r = tc.run_cli("build", str(root), "--strict")                       # the real rule: nothing to refuse here
    assert r.returncode == 0, out(r)

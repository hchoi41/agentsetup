"""Tests for lib/compat.py (M7): one-product functions and features, the mechanical replacements, and writing
per design section 11 (F17, F08a)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "data" / "compat"))

import m7_fixtures as fx7  # noqa: E402

from lib import common, compat, formula, workbook  # noqa: E402
from lib.workbook import PackageInfo, TableInfo  # noqa: E402

SCRIPTS = HERE.parent
TABLES = [TableInfo("Sales", "data", "A1:E6", ["Id", "Qty", "Price", "Line", "Label"]),
          TableInfo("Other", "lists", "B3:C9", ["Code", "Rate"])]


def plan(f: str, *, sheet: str = "data", row: int = 3, col: int = 4, product: str = "both"):
    return compat.plan_formula(f, sheet=sheet, row=row, col=col, tables=TABLES, product=product)


def rules(problems):
    return [(p[0], p[1]) for p in problems]


@pytest.fixture
def no_excel(monkeypatch):
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")


# ----------------------------------------------------------------------------- structured references

@pytest.mark.parametrize("text, table, this_row, cols", [
    ("Sales[@Qty]", "Sales", True, ["Qty"]),
    ("[@Qty]", None, True, ["Qty"]),
    ("Sales[[#This Row],[Qty]]", "Sales", True, ["Qty"]),
    ("Sales[[#This Row],[Qty]:[Price]]", "Sales", True, ["Qty", "Price"]),
    ("[@[Unit Price]]", None, True, ["Unit Price"]),
    ("Sales[#This Row]", "Sales", True, []),
    ("Sales[Qty]", "Sales", False, ["Qty"]),
    ("Sales[[#All],[Qty]]", "Sales", False, ["Qty"]),
])
def test_parse_structured(text, table, this_row, cols):
    s = compat.parse_structured(text)
    assert s is not None
    assert (s.table, s.this_row, s.columns) == (table, this_row, cols)


def test_this_row_becomes_same_row_a1():
    new, done, problems = plan("=Sales[[#This Row],[Qty]]*Sales[@Price]")
    assert new == "=B3*C3" and done == ["X-STRUCTREF"]
    assert not [p for p in problems if p[0] == "X-STRUCTREF"]


def test_this_row_from_another_sheet_names_the_table_sheet():
    new, _done, _p = plan("=Sales[@Qty]+1", sheet="calc", row=4, col=2)
    assert new == "=data!B4+1"


def test_unqualified_this_row_inside_the_table_and_column_span():
    new, _d, _p = plan("=SUM([@[Qty]:[Price]])".replace("[@[Qty]:[Price]]", "Sales[[#This Row],[Qty]:[Price]]"))
    assert new == "=SUM(B3:C3)"
    new2, _d, _p = plan("=[@Qty]*2", row=5, col=4)
    assert new2 == "=B5*2"


def test_this_row_outside_the_data_rows_is_reported_not_replaced():
    new, done, problems = plan("=Sales[@Qty]", sheet="calc", row=20, col=2)
    assert new is None and done == []
    assert ("X-STRUCTREF", "warning") in rules(problems)
    assert "outside the data rows" in problems[0][2]


def test_unknown_table_and_whole_table_reference():
    new, _d, problems = plan("=Nope[@Qty]")
    assert new is None and ("X-STRUCTREF", "warning") in rules(problems)
    new, _d, problems = plan("=SUM(Sales[Qty])", sheet="calc")
    assert new is None and ("X-TABLEREF", "info") in rules(problems)


# ----------------------------------------------------------------------------- prefixes, UDF, CONCATENATE

def test_missing_xlfn_prefix_is_added():
    new, done, _p = plan("=XLOOKUP(A1,B:B,C:C)")
    assert new == "=_xlfn.XLOOKUP(A1,B:B,C:C)" and done == ["X-PREFIX"]
    assert plan("=_xlfn.XLOOKUP(A1,B:B,C:C)")[0] is None


def test_xludf_known_function_is_repaired_and_unknown_reported():
    new, done, _p = plan('=IFERROR(_xludf.IFS(A1=1,2,A1=2,3),0)')
    assert new == '=IFERROR(_xlfn.IFS(A1=1,2,A1=2,3),0)' and done == ["X-UDF"]
    new, done, problems = plan("=_xludf.MYFUNC(A1)")
    assert new is None and ("X-UDF", "warning") in rules(problems)


@pytest.mark.parametrize("f, expected", [
    ('=CONCATENATE("(End of ",Model!$H$5,")")', '="(End of "&Model!$H$5&")"'),
    ("=CONCATENATE(A1,B1)+0", "=(A1&B1)+0"),
    ('=CONCATENATE(A1=B1,"x")', '=(A1=B1)&"x"'),
    ('=CONCATENATE("a",CONCATENATE(B1,C1))', '="a"&B1&C1'),
    ("=LEN(CONCATENATE(A1,B1))", "=LEN(A1&B1)"),
    ("=CONCATENATE(A1)", '=A1&""'),
    ('=CONCATENATE(A1+1," kg")', '=A1+1&" kg"'),
])
def test_concatenate_becomes_ampersand(f, expected):
    new, done, _p = plan(f)
    assert new == expected and done == ["X-CONCAT"]
    assert formula.depth(new) < formula.depth(f)


def test_concatenate_of_a_range_is_left():
    new, done, problems = plan("=CONCATENATE(A1:A3)")
    assert new is None and ("X-CONCAT", "info") in rules(problems)


def test_replacements_never_raise_depth():
    for f in ("=Sales[@Qty]*2", "=XLOOKUP(A1,B:B,C:C)", '=IFERROR(_xludf.IFS(A1=1,2),0)', "=CONCATENATE(A1,B1)"):
        new, _d, _p = plan(f)
        assert new is not None and formula.depth(new) <= formula.depth(f)


# ----------------------------------------------------------------------------- one-product functions

def test_google_only_function_is_an_error_in_both_and_excel_profiles():
    for product in ("both", "excel"):
        _n, _d, problems = plan('=QUERY(A:B,"select A")', product=product)
        assert ("X-FUNC", "error") in rules(problems), product
    _n, _d, problems = plan('=QUERY(A:B,"select A")', product="sheets")
    assert not [p for p in problems if p[0] == "X-FUNC"]


def test_excel_function_unsupported_in_sheets():
    _n, _d, problems = plan("=WEBSERVICE(A1)")
    assert ("X-FUNC", "error") in rules(problems)
    assert "not supported" in problems[0][2]
    _n, _d, problems = plan("=WEBSERVICE(A1)", product="excel")
    assert not [p for p in problems if p[0] == "X-FUNC"]


def test_not_confirmed_and_unknown_functions_are_warnings():
    _n, _d, problems = plan("=TAKE(A1:A9,2)")
    assert ("X-FUNC", "warning") in rules(problems) and "not confirmed" in problems[0][2]
    _n, _d, problems = plan("=FOOBAR(A1)")
    assert ("X-FUNC", "warning") in rules(problems)


def test_functions_in_both_products_are_not_reported():
    _n, _d, problems = plan("=SUMIFS(C:C,A:A,B1)+INDEX(D:D,MATCH(E1,F:F,0))")
    assert problems == []


# ----------------------------------------------------------------------------- features

def _pkg(**kw):
    base = dict(path=Path("x.xlsx"), parts=[], fragile=[])
    base.update(kw)
    return PackageInfo(**base)


class _Info:
    def __init__(self, pkg, date1904=False):
        self.package = pkg
        self.date1904 = date1904


def test_feature_findings_name_each_one_product_feature():
    pkg = _pkg(parts=["xl/vbaProject.bin", "xl/slicers/slicer1.xml", "xl/model/item.data", "xl/ctrlProps/c1.xml"],
               vba=True, has_power_query=True, query_names=["q_sales"], connections=2, mashup_connections=1,
               extensions={"xl/worksheets/sheet1.xml": ["conditionalFormattings", "sparklineGroups"]},
               pivots=1, external_links=1, metadata=True)
    found = {f.data["feature"]: f.severity for f in compat.feature_findings(_Info(pkg, date1904=True), "both")}
    for name in ("macros", "power_query", "connections", "x14_conditional_formats", "sparklines", "slicers",
                 "data_model", "controls", "external_links", "date1904"):
        assert found.get(name) == "warning", name
    assert found["pivot_tables"] == "info" and found["dynamic_arrays"] == "info"
    assert compat.feature_findings(_Info(pkg), "excel") == []
    assert compat.feature_findings(_Info(_pkg()), "both") == []


def test_power_query_message_points_to_connected_sheets():
    f = compat.feature_findings(_Info(_pkg(has_power_query=True, query_names=["q_a"])), "both")[0]
    assert "q_a" in f.message and "Connected Sheets" in f.fix


# ----------------------------------------------------------------------------- the command

def test_report_only_writes_nothing_and_reports_replaceable_items(tmp_path):
    src = fx7.compat_cases(tmp_path / "cases.xlsx")
    before = common.file_sha256(src)
    rep = compat.compat_workbook(src, report_only=True)
    assert common.file_sha256(src) == before
    assert not (tmp_path / "cases_compat.xlsx").exists()
    sev = {(f.rule, f.sheet, f.cell): f.severity for f in rep.findings}
    assert sev[("X-PREFIX", "calc", "C1")] == "error"
    assert sev[("X-UDF", "calc", "C2")] == "error"
    assert sev[("X-STRUCTREF", "data", "D2")] == "warning"
    assert sev[("X-FUNC", "calc", "C4")] == "error"            # QUERY
    assert rep.exit_code == common.EXIT_FINDINGS
    assert rep.data["replaced_count"] == 13 and rep.data["written"] is False


def test_write_without_excel_uses_openpyxl_and_says_not_verified(tmp_path, no_excel):
    src = fx7.compat_cases(tmp_path / "cases.xlsx")
    before = common.file_sha256(src)
    rep = compat.compat_workbook(src, product="excel")         # QUERY stays an error in profile excel
    out = tmp_path / "cases_compat.xlsx"
    assert out.is_file() and rep.data["output"] == out.as_posix() and rep.data["written"] is True
    assert common.file_sha256(src) == before                   # the original is never changed without --in-place
    assert rep.verified["recalculated"] is False
    assert rep.exit_code == common.EXIT_FINDINGS                # QUERY: an error finding remains
    info = workbook.open_workbook(out, with_values=False)
    data, calc = info.sheet("data"), info.sheet("calc")
    assert data.cell("D2").formula == "=B2*C2" and data.cell("E4").formula == '=A4&"-"&B4'
    assert calc.cell("C1").formula == "=_xlfn.XLOOKUP(B1,data!A2:A6,data!D2:D6)"
    assert calc.cell("C2").formula == '=IFERROR(_xlfn.IFS(B1="A03",1,B1="A04",2),0)'
    assert calc.cell("C3").formula == "=SUM(Sales[Line])"     # reported only
    assert [t.name for t in info.tables()] == ["Sales"]
    # F17: afterwards the cross-product rules pass for the replaced cells
    for sheet, coord in (("data", "D2"), ("data", "E6"), ("calc", "C1"), ("calc", "C2"), ("calc", "C6")):
        issues = formula.check_formula(info.sheet(sheet).cell(coord).formula, sheet=sheet)
        assert not [i for i in issues if i.rule in ("X-STRUCTREF", "F-FUNC-REFUSED", "F-FUNC-UNKNOWN")], coord
    again = compat.compat_workbook(out, report_only=True, product="excel")
    assert again.data["replaced_count"] == 0


def test_not_verified_without_excel_exits_3_when_nothing_else_is_wrong(tmp_path, no_excel):
    from openpyxl import Workbook
    wb = Workbook()
    wb.active["A1"] = "x"
    wb.active["B1"] = '=CONCATENATE(A1,"y")'
    wb.save(tmp_path / "c.xlsx")
    rep = compat.compat_workbook(tmp_path / "c.xlsx")
    assert rep.data["written"] is True and rep.exit_code == common.EXIT_NOT_AVAILABLE
    assert any("not compared after recalculation" in n for n in rep.notes)


def test_fragile_parts_without_excel_refuse_and_write_nothing(tmp_path, no_excel):
    src = fx7.with_chart_and_names(tmp_path / "chart.xlsx")
    from openpyxl import load_workbook
    wb = load_workbook(src)
    wb["calc"]["H5"] = '=CONCATENATE("a","b")'
    wb.save(src)
    # openpyxl keeps its own chart, so the fixture holds xl/charts: a fragile part
    assert workbook.package_parts(src).charts == 1
    with pytest.raises(common.EnvUnavailable) as exc:
        compat.compat_workbook(src)
    assert "openpyxl would drop" in str(exc.value) and "charts" in str(exc.value)
    assert not (tmp_path / "chart_compat.xlsx").exists()


def test_in_place_keeps_a_backup(tmp_path, no_excel):
    src = fx7.compat_cases(tmp_path / "cases.xlsx")
    rep = compat.compat_workbook(src, in_place=True, product="sheets")
    bak = Path(rep.data["backup"])
    assert bak.is_file() and bak.name.startswith("cases.bak_") and bak.suffix == ".xlsx"
    info = workbook.open_workbook(src, with_values=False)
    assert info.sheet("data").cell("D2").formula == "=B2*C2"


def test_output_options_are_checked(tmp_path):
    src = fx7.compat_cases(tmp_path / "cases.xlsx")
    with pytest.raises(common.Refusal):
        compat.compat_workbook(src, out=tmp_path / "x.xlsx", in_place=True)
    with pytest.raises(common.Refusal):
        compat.compat_workbook(src, out=src)
    with pytest.raises(common.Refusal):
        compat.compat_workbook(src, out=tmp_path / "x.csv")
    with pytest.raises(common.Refusal):
        compat.compat_workbook(tmp_path / "missing.xlsx")


def test_nothing_to_replace_writes_no_file(tmp_path):
    from openpyxl import Workbook
    wb = Workbook()
    wb.active["A1"] = "=SUM(B1:B3)"
    wb.save(tmp_path / "clean.xlsx")
    rep = compat.compat_workbook(tmp_path / "clean.xlsx")
    assert rep.exit_code == 0 and rep.data["written"] is False
    assert not (tmp_path / "clean_compat.xlsx").exists()


def test_dependents_follow_references_across_sheets(tmp_path):
    from openpyxl import Workbook
    wb = Workbook()
    a = wb.active
    a.title = "a"
    a["A1"] = "=XLOOKUP(1,B:B,C:C)"
    a["A2"] = "=A1*2"
    b = wb.create_sheet("b")
    b["A1"] = "=SUM(a!A1:A2)"
    b["A2"] = "=1+1"
    wb.save(tmp_path / "d.xlsx")
    info = workbook.open_workbook(tmp_path / "d.xlsx", with_values=False)
    deps = compat.dependents(info, {("a", "A1")})
    assert deps == {("a", "A1"), ("a", "A2"), ("b", "A1")}


def test_cli_report_only_json(tmp_path):
    src = fx7.compat_cases(tmp_path / "cases.xlsx")
    env = dict(os.environ, ROTM_NO_EXCEL="1")
    res = subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), "compat", str(src),
                          "--report-only", "--json", "-"], capture_output=True, text=True, encoding="utf-8", env=env)
    assert res.returncode == 1, res.stderr
    data = json.loads(res.stdout)
    assert data["command"] == "compat" and data["data"]["replaced_count"] == 13
    assert "Traceback" not in res.stderr


def test_cli_refuses_fragile_without_excel_with_exit_3(tmp_path):
    src = fx7.with_chart_and_names(tmp_path / "chart.xlsx")
    from openpyxl import load_workbook
    wb = load_workbook(src)
    wb["calc"]["H5"] = '=CONCATENATE("a","b")'
    wb.save(src)
    env = dict(os.environ, ROTM_NO_EXCEL="1")
    res = subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), "compat", str(src)],
                         capture_output=True, text=True, encoding="utf-8", env=env)
    assert res.returncode == 3 and "nothing was written" in res.stderr and "Traceback" not in res.stderr


# ----------------------------------------------------------------------------- with Excel

@pytest.mark.excel
def test_excel_compat_cases_values_proved_and_name_fixes_accepted(tmp_path):
    src = fx7.compat_cases(tmp_path / "cases.xlsx")
    rep = compat.compat_workbook(src)
    assert rep.data["written"] is True and rep.verified["recalculated"] is True
    assert rep.exit_code == common.EXIT_FINDINGS               # QUERY remains, an error by design
    assert not [f for f in rep.findings if f.rule == "W-UNINTENDED"]
    fixed = {f.cell for f in rep.findings if f.rule == "X-FIXED"} | {
        c for f in rep.findings if f.rule == "X-FIXED" for c in f.cells}
    assert "C1" in fixed and "C2" in fixed                     # #NAME? before, a result after
    info = workbook.open_workbook(Path(rep.data["output"]))
    assert info.sheet("calc").cell("C1").cached == 90           # XLOOKUP of A03: 3 * 30
    assert info.sheet("data").cell("D6").cached == 250          # 5 * 50, unchanged by the rewrite


@pytest.mark.excel
def test_excel_fragile_workbook_goes_through_excel_and_keeps_parts(tmp_path):
    src = fx7.with_chart_and_names(tmp_path / "chart.xlsx")
    from openpyxl import load_workbook
    wb = load_workbook(src)
    wb["calc"]["H5"] = '=CONCATENATE(A2,"-",B2)'
    wb["calc"]["H6"] = "=H5&\"!\""
    wb.save(src)
    before = workbook.snapshot(src)
    rep = compat.compat_workbook(src)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    assert any("through Excel" in n for n in rep.notes)
    after = workbook.snapshot(Path(rep.data["output"]))
    assert after.charts == before.charts == 1
    assert after.names == before.names and after.cf == before.cf and after.dv == before.dv
    assert workbook.normalise_formula(after.formulas[("calc", "H5")]) == '=A2&"-"&B2'


FOREIGN = ["Precision Model Complete.xlsx", "DCF Valuation Model (Complete).xlsx",
           "Building Business Acumen for FP&A Workbook.xlsx", "M2V3_end.xlsx", "12 Month Rolling Forecast.xlsx",
           "1.3-Testing-your-input-data.xlsx", "Beach Part 5_Complete.xlsx"]


@pytest.mark.excel
@pytest.mark.slow
@pytest.mark.parametrize("name", FOREIGN)
def test_excel_foreign_copies_keep_parts_or_refuse(name, foreign_dir, tmp_path):
    """F08a for compat: on copies of workbooks the plugin did not make (one with Power Query, one with charts)."""
    if not (foreign_dir / name).is_file():
        pytest.skip(f"{name} is not in {foreign_dir}")
    from lib import excel
    src = tmp_path / name
    shutil.copy2(foreign_dir / name, src)
    before = workbook.snapshot(src)
    rep = compat.compat_workbook(src)
    assert rep.exit_code in (0, 1), [f.line() for f in rep.findings][:5]
    assert not [f for f in rep.findings if f.rule == "W-UNINTENDED"], [f.line() for f in rep.findings][:5]
    if rep.data["written"]:
        out = Path(rep.data["output"])
        after = workbook.snapshot(out)
        assert (after.charts, after.queries, after.pivots) == (before.charts, before.queries, before.pivots)
        assert after.tables == before.tables
        # Excel's own counts (an Excel save merges conditional-format blocks, so openpyxl's rule count can drop
        # while Excel's count of format conditions stays the same)
        assert excel.inventory_counts(excel.inventory(out)) == excel.inventory_counts(excel.inventory(src))
        assert rep.verified["recalculated"] is True

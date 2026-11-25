"""Tests of lib.theme: the dictionary applied to an existing workbook with every value and formula unchanged, the
openpyxl and Excel paths, refusals, flags, the model folder, the command line (design sections 10.5, 11; F08, F08a).
Owner: M2.

The proof that theme changes styles only is made twice: by workbook.compare inside theme, and here by reading
every cell of both files with openpyxl (formula text and typed values; with Excel, the recalculated values too).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from lib import classify, common, dictionary, excel, modelfolder, styles, theme, workbook

SCRIPTS = Path(__file__).resolve().parents[1]


# ----------------------------------------------------------------------------- fixtures

def make_plain(path: Path, *, chart: bool = False) -> Path:
    """A workbook made by another tool: Calibri, a dark header fill, blue inputs, a table with a table style,
    a defined name, conditional formats, a data validation, merged cells, frozen panes and column widths."""
    import openpyxl
    from openpyxl.formatting.rule import CellIsRule
    from openpyxl.styles import Font, PatternFill
    from openpyxl.workbook.defined_name import DefinedName
    from openpyxl.worksheet.datavalidation import DataValidation
    from openpyxl.worksheet.table import Table, TableStyleInfo
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Inputs"
    ws["B1"] = "Assumptions"
    ws["B1"].font = Font(name="Calibri", sz=14, b=True, color="FF1F3864")
    for coord, text in (("B3", "Item"), ("C3", "Unit"), ("D3", "Value")):
        ws[coord] = text
        ws[coord].fill = PatternFill("solid", fgColor="FF1F3864")
        ws[coord].font = Font(color="FFFFFFFF", b=True)
    for i, (label, unit, value) in enumerate([("Growth", "%", 0.05), ("Price", "KRW", 1200), ("Units", "count", 300)],
                                             start=4):
        ws.cell(i, 2, label)
        ws.cell(i, 3, unit)
        ws.cell(i, 4, value).font = Font(color="FF0000FF", i=True)
    ws["B8"] = "Source: budget file v3, page 4, agreed with the controller in November"
    ws.merge_cells("B10:D10")
    ws["B10"] = "Merged caption"
    ws.freeze_panes = "D4"
    ws.column_dimensions["B"].width = 31.5
    dv = DataValidation(type="decimal", operator="between", formula1="0", formula2="1")
    ws.add_data_validation(dv)
    dv.add("D4")
    calc = wb.create_sheet("Calc")
    calc["B1"] = "Revenue"
    calc["B3"] = "Region"
    for j, m in enumerate(["Jan", "Feb", "Mar", "Total"], start=4):
        calc.cell(3, j, m)
    for i, r in enumerate(["East", "West", "North"], start=4):
        calc.cell(i, 2, r)
        for j in range(4, 7):
            calc.cell(i, j, f"=Inputs!$D$5*Inputs!$D$6*(1+Inputs!$D$4)^{j - 4}")
        calc.cell(i, 7, f"=SUM(D{i}:F{i})")
    calc["B7"] = "Total"
    for j, col in enumerate("DEFG", start=4):
        calc.cell(7, j, f"=SUM({col}4:{col}6)")
    calc["B9"] = "Revenue check"
    calc["D9"] = "=--(ABS(G7-SUM(D4:F6))>0.001)"
    calc["B10"] = "Text check"
    calc["D10"] = '=IF(D9=0,"OK","ERROR")'
    calc["D12"] = "=Inputs!D4"
    calc["D13"] = '=TEXT(D4,"0")&" units"'
    calc.conditional_formatting.add("D4:F6", CellIsRule(operator="lessThan", formula=["0"], font=Font(color="FFFF0000")))
    raw = wb.create_sheet("raw_sales")
    raw.append(["invoice", "region", "amount"])
    for k in range(1, 21):
        raw.append([f"INV{k:03d}", ["East", "West", "North"][k % 3], k * 10.5])
    tab = Table(displayName="tbl_raw_sales", ref="A1:C21")
    tab.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
    raw.add_table(tab)
    wb.defined_names["growth"] = DefinedName("growth", attr_text="Inputs!$D$4")
    if chart:
        from openpyxl.chart import BarChart, Reference
        ch = BarChart()
        ch.add_data(Reference(calc, min_col=4, min_row=3, max_col=6, max_row=6), titles_from_data=True)
        calc.add_chart(ch, "I3")
    wb.save(path)
    return path


def cells_of(path: Path, *, data_only: bool = False) -> dict[tuple[str, str], object]:
    """Every non-empty cell: formula text (or the cached value with data_only) and typed values, read by openpyxl.
    Array and data-table formulas become (kind, text, range), because openpyxl's formula objects compare by
    identity."""
    import warnings

    import openpyxl
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(path, data_only=data_only)
    out = {}
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                v = c.value
                if v is None:
                    continue
                kind = type(v).__name__
                if kind == "ArrayFormula":
                    v = ("array", v.text, str(v.ref))
                elif kind == "DataTableFormula":
                    v = ("data_table", str(getattr(v, "ref", "")), str(getattr(v, "r1", "")),
                         str(getattr(v, "r2", "")))
                out[(ws.title, c.coordinate)] = v
    wb.close()
    return out


def structure_of(path: Path) -> dict[str, object]:
    import openpyxl
    wb = openpyxl.load_workbook(path)
    out = {"sheets": wb.sheetnames, "names": sorted((n, str(v.value)) for n, v in wb.defined_names.items()),
           "tables": sorted((t.displayName, t.ref) for ws in wb.worksheets for t in ws.tables.values()),
           "cf": {ws.title: sum(len(c.rules) for c in ws.conditional_formatting) for ws in wb.worksheets},
           "dv": {ws.title: len(ws.data_validations.dataValidation) for ws in wb.worksheets},
           "merged": {ws.title: sorted(str(r) for r in ws.merged_cells.ranges) for ws in wb.worksheets},
           "freeze": {ws.title: ws.freeze_panes for ws in wb.worksheets},
           "widths": {ws.title: {k: v.width for k, v in ws.column_dimensions.items()} for ws in wb.worksheets}}
    wb.close()
    return out


def residual(path: Path, d: dictionary.Dictionary, attributes=styles.FOREIGN_ATTRIBUTES) -> list[str]:
    """What an audit of the style rules would find: the cells of `path` against the dictionary, with the types and
    sizes inferred from the file itself."""
    info = workbook.open_workbook(path)
    plan = classify.plan_workbook(info, d)
    types = classify.classify_workbook(info)
    out = []
    for s in info.sheets:
        if s.name not in plan:
            continue
        for c in s.iter_cells():
            exp = styles.expected_style(d, types[(s.name, c.coord)], sheet_size=plan[s.name].size)
            out += [f"{s.name}!{c.coord} {m}" for m in styles.compare_cell(c, exp, attributes=attributes)]
    return out


@pytest.fixture
def no_excel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")


@pytest.fixture
def plain(tmp_path: Path) -> Path:
    return make_plain(tmp_path / "plain.xlsx")


def rules_of(report) -> list[str]:
    return [f.rule for f in report.findings]


# ----------------------------------------------------------------------------- compress_ranges

def test_compress_ranges() -> None:
    assert theme.compress_ranges([(1, 1)]) == ["A1"]
    assert theme.compress_ranges([(1, 1), (1, 2), (2, 1), (2, 2)]) == ["A1:B2"]
    assert theme.compress_ranges([(1, 1), (1, 3), (2, 1), (2, 3), (3, 1)]) == ["A1:A3", "C1:C2"]
    assert theme.compress_ranges([(5, 4), (5, 5), (6, 4), (7, 4), (7, 5)]) == ["D5:E5", "D6", "D7:E7"]
    cells = [(r, c) for r in range(2, 602) for c in range(1, 7)]
    assert theme.compress_ranges(cells) == ["A2:F601"]
    many = [(r, c) for r in range(1, 40) for c in range(1, 30) if (r + c) % 3]
    out = theme.compress_ranges(many)
    covered = {(r, c) for a in out for (r, c) in common.iter_range(a)}
    assert covered == set(many)


# ----------------------------------------------------------------------------- the openpyxl path (no Excel)

def test_theme_keeps_every_value_and_formula(plain: Path, no_excel: None) -> None:
    before_cells, before_struct = cells_of(plain), structure_of(plain)
    sha = common.file_sha256(plain)
    report = theme.theme_workbook(plain, strict=True)
    out = plain.with_name("plain_theme.xlsx")
    assert report.exit_code == 0, [f.line() for f in report.findings]
    assert report.data["writer"] == "openpyxl" and report.data["written"] and out.is_file()
    assert common.file_sha256(plain) == sha                       # the original is not touched
    assert cells_of(out) == before_cells                          # every formula text and typed value
    assert structure_of(out) == before_struct                     # names, tables, cf, dv, merges, panes, widths
    assert report.data["cells_changed"] > 0 and "T-NOT-RECALC" in rules_of(report)


def test_after_theme_the_style_rules_pass(plain: Path, no_excel: None) -> None:
    d = dictionary.load_dictionary()
    assert residual(plain, d)                                     # the fixture is off-dictionary
    report = theme.theme_workbook(plain, strict=True)
    out = Path(report.data["output"])
    assert residual(out, d) == []                                 # F08: font, size, colour, bold and fill
    assert "T-RESIDUAL" not in rules_of(report) and "T-RECLASSIFIED" not in rules_of(report)
    info = workbook.open_workbook(out)
    inputs = info.sheet("Inputs")
    assert (inputs.cell("D5").font_name, inputs.cell("D5").font_size, inputs.cell("D5").fill) == ("Arial", 9, "FFF2CC")
    assert inputs.cell("B3").fill == "D9D9D9" and inputs.cell("B4").fill == "F2F2F2"
    assert inputs.cell("D4").italic is False
    raw = info.sheet("raw_sales")
    assert raw.cell("A2").font_size == 8 and raw.cell("A1").fill == "D9D9D9"


def test_audit_style_rules_pass_after_theme(plain: Path, no_excel: None) -> None:
    from lib import audit
    report = theme.theme_workbook(plain, strict=True)
    try:
        a = audit.audit_workbook(report.data["output"], rules=["S-FONT", "S-SIZE", "S-FILL", "S-FONTCOLOR", "S-BOLD"],
                                 record=False)
    except NotImplementedError:
        pytest.skip("lib.audit is not written yet")
    assert [f.line() for f in a.findings if f.rule.startswith("S-")] == []


def test_dry_run_writes_nothing(plain: Path, no_excel: None) -> None:
    report = theme.theme_workbook(plain, dry_run=True)
    assert report.exit_code == 0 and not report.data["written"]
    assert not plain.with_name("plain_theme.xlsx").exists()
    style = [f for f in report.findings if f.rule == "T-STYLE"]
    assert style and all("would be restyled" in f.message for f in style)
    assert report.data["sheets"]["raw_sales"]["size"] == 8 and report.data["sheets"]["Inputs"]["size"] == 9


def test_in_place_keeps_a_backup(plain: Path, no_excel: None) -> None:
    original = plain.read_bytes()
    before = cells_of(plain)
    report = theme.theme_workbook(plain, in_place=True)
    assert report.exit_code == 0
    backup = Path(report.data["backup"])
    assert backup.name.startswith("plain.bak_") and backup.suffix == ".xlsx"
    assert backup.read_bytes() == original
    assert cells_of(plain) == before and plain.read_bytes() != original
    assert not plain.with_name("plain_theme.xlsx").exists()


def test_a_second_theme_changes_nothing(plain: Path, no_excel: None) -> None:
    first = theme.theme_workbook(plain)
    out = Path(first.data["output"])
    second = theme.theme_workbook(out, out=out.with_name("again.xlsx"))
    assert second.data["cells_changed"] == 0 and "T-UNCHANGED" in rules_of(second)
    assert out.with_name("again.xlsx").read_bytes() == out.read_bytes()


def test_named_override_and_override_file(plain: Path, tmp_path: Path, no_excel: None) -> None:
    report = theme.theme_workbook(plain, use=["input_blue_font"], out=tmp_path / "blue.xlsx")
    inputs = workbook.open_workbook(tmp_path / "blue.xlsx").sheet("Inputs")
    assert inputs.cell("D5").font_color == "0000FF" and inputs.cell("D5").fill is None
    assert report.data["dictionary"]["applied"] == ["input_blue_font"]
    ov = tmp_path / "ov.json"
    common.write_json(ov, {"rotm_dictionary_override": 1, "use": ["links_green"]})
    theme.theme_workbook(plain, dictionary=str(ov), out=tmp_path / "green.xlsx")
    calc = workbook.open_workbook(tmp_path / "green.xlsx").sheet("Calc")
    assert calc.cell("D12").font_color == "008000"                # an import (a link to another sheet)


def test_optional_attributes(plain: Path, tmp_path: Path, no_excel: None) -> None:
    out = tmp_path / "all.xlsx"
    report = theme.theme_workbook(plain, out=out, number_formats=True, borders=True, align=True, views=True,
                                  strict=True)
    assert report.exit_code == 0, [f.line() for f in report.findings]
    info = workbook.open_workbook(out)
    calc = info.sheet("Calc")
    assert calc.cell("D7").border == {"top": {"style": "thin", "color": "000000"}}          # total
    assert calc.cell("D3").border == {"bottom": {"style": "thin", "color": "808080"}}       # column header
    assert calc.cell("D9").number_format == '[Red]"FAIL";[Red]"FAIL";"OK"'                  # a 0/1 check
    assert calc.cell("D10").number_format == "General"                                      # shows text: kept
    assert "T-NUMFMT-SKIPPED" in rules_of(report)
    assert calc.cell("D9").h_align == "center" and calc.cell("B4").h_align == "left"
    assert info.sheet("raw_sales").cell("C2").h_align == "right"
    assert all(not s.gridlines and s.zoom == 100 for s in info.sheets)
    left = residual(out, dictionary.load_dictionary(), styles.ALL_ATTRIBUTES)
    assert left == ['Calc!D10 number_format General, expected [Red]"FAIL";[Red]"FAIL";"OK"']   # the skipped one


def test_table_style_and_conditional_formats_are_reported(plain: Path, no_excel: None) -> None:
    report = theme.theme_workbook(plain, dry_run=True)
    assert "T-TABLESTYLE" in rules_of(report) and "T-CF" in rules_of(report)


# ----------------------------------------------------------------------------- refusals

def test_refusals(plain: Path, tmp_path: Path, no_excel: None) -> None:
    with pytest.raises(common.Refusal, match="use --in-place"):
        theme.theme_workbook(plain, out=plain)
    with pytest.raises(common.Refusal, match="not both"):
        theme.theme_workbook(plain, out=tmp_path / "x.xlsx", in_place=True)
    with pytest.raises(common.Refusal, match="another extension"):
        theme.theme_workbook(plain, out=tmp_path / "x.xlsm")
    with pytest.raises(common.Refusal, match="workbook not found"):
        theme.theme_workbook(tmp_path / "missing.xlsx")
    with pytest.raises(common.Refusal, match="names no sheet"):
        theme.theme_workbook(plain, raw_sheets=["raw_salez"])
    bad = tmp_path / "bad.xlsx"
    bad.write_text("not a workbook", encoding="utf-8")
    with pytest.raises(common.Refusal, match="not a zip package"):
        theme.theme_workbook(bad)
    with pytest.raises(common.Refusal, match="did you mean 'input_blue_font'"):
        theme.theme_workbook(plain, use=["input_blue_fnt"])


def test_fragile_parts_without_excel_refuse_and_write_nothing(tmp_path: Path, no_excel: None) -> None:
    wbp = make_plain(tmp_path / "chart.xlsx", chart=True)
    report = theme.theme_workbook(wbp)
    assert report.exit_code == common.EXIT_NOT_AVAILABLE and report.status == "not_verified"
    msg = next(f.message for f in report.findings if f.rule == "T-FRAGILE")
    assert "charts" in msg and "Excel is not available" in msg and "nothing was written" in msg
    assert not wbp.with_name("chart_theme.xlsx").exists()
    dry = theme.theme_workbook(wbp, dry_run=True)        # a dry run still says what would change
    assert dry.data["cells_changed"] > 0


# ----------------------------------------------------------------------------- model folder

def test_model_folder_override_manifest_and_status(model_dir: Path, no_excel: None) -> None:
    wbp = make_plain(model_dir / "model.xlsx")
    common.write_json(model_dir / "style_overrides.json", {"rotm_dictionary_override": 1, "use": ["links_green"]})
    manifest = {"rotm_manifest": 1, "workbook": "model.xlsx",
                "sheets": [{"name": "Inputs", "type": "inputs", "font_size": 9},
                           {"name": "Calc", "type": "calc", "font_size": 8},
                           {"name": "raw_sales", "type": "raw", "font_size": 8, "table": "sales"}],
                "cell_types": {"Calc": [["D4:F6", "calc"]]}}
    common.write_json(model_dir / "model.manifest.json", manifest)
    report = theme.theme_workbook(wbp, in_place=True, strict=True)
    assert report.exit_code == 0, [f.line() for f in report.findings]
    assert report.data["dictionary"]["applied"] == ["links_green"]
    info = workbook.open_workbook(wbp)
    assert info.sheet("Calc").cell("D4").font_size == 8          # the manifest's size, not the density guess
    assert info.sheet("Calc").cell("D12").font_color == "008000"
    m = common.read_json(model_dir / "model.manifest.json")
    assert m["dictionary_applied"] == ["links_green"] and len(m["dictionary_sha256"]) == 64
    status = modelfolder.read_status(model_dir)
    entry = status["workbooks"]["model.xlsx"]["theme"]
    assert entry["exit_code"] == 0 and entry["sha256"] == common.file_sha256(wbp)


# ----------------------------------------------------------------------------- the command line

def _rotm(*args: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONUTF8="1", **(env_extra or {}))
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", env=env, timeout=600)


def test_command_line_exit_codes_and_json(plain: Path, tmp_path: Path) -> None:
    no = {"ROTM_NO_EXCEL": "1"}
    r = _rotm("theme", str(plain), "--strict", "--json", "-", env_extra=no)
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["command"] == "theme" and rep["status"] == "pass" and rep["data"]["written"]
    assert "errors: 0" in r.stderr and "Traceback" not in r.stderr
    r = _rotm("theme", str(plain), "-o", str(plain), env_extra=no)
    assert r.returncode == 2 and "use --in-place" in r.stderr
    chart = make_plain(tmp_path / "chart.xlsx", chart=True)
    r = _rotm("theme", str(chart), env_extra=no)
    assert r.returncode == 3 and "T-FRAGILE" in r.stdout
    r = _rotm("theme", "-h")
    assert r.returncode == 0 and "--number-formats" in r.stdout and "--views" in r.stdout


# ----------------------------------------------------------------------------- through Excel

def _recalculated_values(path: Path, work: Path) -> dict[tuple[str, str], object]:
    copy = work / f"recalc_{path.name}"
    shutil.copy2(path, copy)
    res = excel.recalc(copy, save=True)
    assert res.ok, res.errors
    return cells_of(copy, data_only=True)


@pytest.mark.excel
def test_excel_path_keeps_chart_table_names_and_values(tmp_path: Path) -> None:
    wbp = make_plain(tmp_path / "chart.xlsx", chart=True)
    before_cells, before_struct = cells_of(wbp), structure_of(wbp)
    report = theme.theme_workbook(wbp, strict=True)
    assert report.exit_code == 0, [f.line() for f in report.findings]
    assert report.data["writer"] == "excel" and report.verified["recalculated"] is True
    out = Path(report.data["output"])
    assert cells_of(out) == before_cells
    after_struct = structure_of(out)
    for key in ("sheets", "names", "tables", "merged", "freeze", "dv"):
        assert after_struct[key] == before_struct[key], key
    assert workbook.package_parts(out).charts == workbook.package_parts(wbp).charts == 1
    work = tmp_path / "work"
    work.mkdir()
    assert _recalculated_values(out, work) == _recalculated_values(wbp, work)     # values after recalculation
    assert residual(out, dictionary.load_dictionary()) == []


@pytest.mark.excel
def test_openpyxl_path_with_excel_delivers_cached_values(plain: Path) -> None:
    report = theme.theme_workbook(plain, strict=True)
    assert report.exit_code == 0 and report.data["writer"] == "openpyxl" and report.verified["recalculated"]
    values = cells_of(Path(report.data["output"]), data_only=True)
    assert isinstance(values[("Calc", "G7")], (int, float)) and values[("Calc", "D10")] == "OK"


@pytest.mark.excel
def test_views_through_excel_are_a_warning(tmp_path: Path) -> None:
    wbp = make_plain(tmp_path / "chart.xlsx", chart=True)
    strict = theme.theme_workbook(wbp, views=True, strict=True, verify=False)
    assert "T-VIEWS" in rules_of(strict) and strict.exit_code == 1
    assert Path(strict.data["output"]).is_file()                # written: the styles were applied


FOREIGN_NAMES = ("M2V3_end.xlsx",                     # Power Query query, query table, dynamic arrays
                 "12 Month Rolling Forecast.xlsx",    # 6 charts
                 "DCF Valuation Model (Complete).xlsx")   # form controls, rich text, duplicate conditional formats


@pytest.mark.excel
@pytest.mark.parametrize("name", FOREIGN_NAMES)
def test_foreign_copies_through_excel(name: str, foreign_dir: Path, tmp_path: Path) -> None:
    src = foreign_dir / name
    if not src.is_file():
        pytest.skip(f"{name} is not in {foreign_dir}")
    wbp = tmp_path / ("w" + src.suffix)
    shutil.copy2(src, wbp)
    pkg_before = workbook.package_parts(wbp)
    before_cells = cells_of(wbp)
    report = theme.theme_workbook(wbp, strict=True)
    assert report.exit_code == 0, [f.line() for f in report.findings]
    assert report.verified["recalculated"] is True
    out = Path(report.data["output"])
    pkg_after = workbook.package_parts(out)
    assert (pkg_after.queries, pkg_after.charts, pkg_after.pivots, pkg_after.tables) == \
        (pkg_before.queries, pkg_before.charts, pkg_before.pivots, pkg_before.tables)
    after_cells = cells_of(out)
    changed = {k for k in before_cells if before_cells[k] != after_cells.get(k)}
    assert not changed, sorted(changed)[:10]
    assert residual(out, dictionary.load_dictionary()) == []


@pytest.mark.excel
def test_no_excel_process_is_left_behind(tmp_path: Path) -> None:
    since = common.now_iso()
    wbp = make_plain(tmp_path / "chart.xlsx", chart=True)
    theme.theme_workbook(wbp, strict=True)
    entries = excel.read_job_log(since=since)
    assert entries, "theme ran no Excel job"
    assert excel.orphan_pids(entries) == []

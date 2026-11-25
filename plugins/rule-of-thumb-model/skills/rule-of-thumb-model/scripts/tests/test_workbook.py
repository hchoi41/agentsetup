"""Tests of lib.workbook: reading any workbook without writing it, package parts openpyxl would drop, colours,
snapshots and the before/after comparison (design sections 11, 19; F06 "ends with a report and no traceback",
F08a). Includes copies of workbooks the plugin did not make (BUILD/foreign). Owner: CORE-IO."""
from __future__ import annotations

import base64
import io
import re
import shutil
import struct
import zipfile
from pathlib import Path

import pytest

from lib import common, workbook

GREY_TINT = -0.1499984740745262       # Excel's "White, Background 1, Darker 15%"
LIGHT_TINT = -0.0499893185216834      # "White, Background 1, Darker 5%"


# ----------------------------------------------------------------------------- fixtures

def make_workbook(path: Path) -> Path:
    import datetime as dt

    import openpyxl
    from openpyxl.chart import BarChart, Reference
    from openpyxl.formatting.rule import CellIsRule
    from openpyxl.styles import Border, Font, PatternFill, Side
    from openpyxl.styles.colors import Color
    from openpyxl.workbook.defined_name import DefinedName
    from openpyxl.worksheet.datavalidation import DataValidation
    from openpyxl.worksheet.formula import ArrayFormula
    from openpyxl.worksheet.table import Table

    wb = openpyxl.Workbook()
    inputs = wb.active
    inputs.title = "inputs"
    inputs["B1"] = "Inputs"
    inputs["B1"].font = Font(name="Arial", size=9, bold=True)
    inputs["B6"] = "Scale"
    inputs["B6"].fill = PatternFill("solid", fgColor=Color(theme=0, tint=GREY_TINT))
    inputs["B7"] = "Start"
    inputs["B7"].fill = PatternFill("solid", fgColor=Color(theme=0, tint=LIGHT_TINT))
    inputs["D6"] = 1000
    inputs["D6"].fill = PatternFill("solid", fgColor="FFFFF2CC")
    inputs["D6"].font = Font(name="Arial", size=9, color="FF0000FF")
    inputs["D7"] = dt.datetime(2026, 1, 31)
    inputs["D7"].border = Border(bottom=Side(style="thin", color="FF808080"))

    calc = wb.create_sheet("calc")
    calc["D7"] = "=inputs!D6*2"
    calc["D8"] = 5
    calc["E7"] = "=SUM(D7:D8)"
    calc["F7"] = '=IFERROR(INDEX(A:A,MATCH(B1,C:C,0)),"")'
    calc["G7"] = ArrayFormula("G7:G8", "=D7:D8*2")
    calc["G8"] = 10
    calc["H7"] = "=_xlfn.XLOOKUP(1,A:A,B:B)"
    calc["B10"] = "merged"
    calc.merge_cells("B10:C10")
    calc.freeze_panes = "D4"
    calc.sheet_view.showGridLines = False
    calc.sheet_view.zoomScale = 90
    calc.conditional_formatting.add("D7:D8", CellIsRule(operator="lessThan", formula=["0"], font=Font(color="FFFF0000")))
    dv = DataValidation(type="whole", operator="between", formula1="0", formula2="10")
    calc.add_data_validation(dv)
    dv.add("D8")
    chart = BarChart()
    chart.add_data(Reference(calc, min_col=4, min_row=7, max_row=8))
    calc.add_chart(chart, "J2")
    calc.defined_names["loc"] = DefinedName("loc", attr_text="calc!$D$7")

    raw = wb.create_sheet("raw_sales")
    raw.append(["id", "amount"])
    raw.append(["a", 1.5])
    raw.append(["b", 2.5])
    raw.add_table(Table(displayName="tbl_raw_sales", ref="A1:B3"))

    hidden = wb.create_sheet("hidden")
    hidden["A1"] = "x"
    hidden.sheet_state = "hidden"
    wb.defined_names["rate"] = DefinedName("rate", attr_text="inputs!$D$6")
    wb.save(path)
    return path


def rewrite(path: Path, out: Path, edits: dict[str, callable], add: dict[str, bytes] | None = None) -> Path:
    """Copy a zip package, changing members with edits[name](bytes) -> bytes and adding new members."""
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename in edits:
                data = edits[item.filename](data)
            dst.writestr(item, data)
        for name, data in (add or {}).items():
            dst.writestr(name, data)
    return out


def sheet_part(path: Path, title: str) -> str:
    """The worksheet part name of a sheet title in an openpyxl-written workbook (sheetN.xml in order)."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True)
    index = wb.sheetnames.index(title) + 1
    wb.close()
    return f"xl/worksheets/sheet{index}.xml"


def inject_cached(path: Path, out: Path, sheet: str, values: dict[str, tuple[str, str | None]]) -> Path:
    """Give formula cells the cached values Excel would have saved: {coord: (text, type or None)}."""
    part = sheet_part(path, sheet)

    def edit(data: bytes) -> bytes:
        text = data.decode("utf-8")
        for coord, (val, typ) in values.items():
            pattern = re.compile(rf'<c r="{coord}"([^>]*)>(<f[^>]*>.*?</f>|<f[^>]*/>)<v\s*/>')
            t = f' t="{typ}"' if typ else ""
            text, n = pattern.subn(lambda m: f'<c r="{coord}"{m.group(1)}{t}>{m.group(2)}<v>{val}</v>', text)
            assert n == 1, coord
        return text.encode("utf-8")
    return rewrite(path, out, {part: edit})


def mashup_item(names: list[str]) -> bytes:
    m_text = "section Section1;\r\n" + "".join(
        f"\r\nshared {('#\"' + n + '\"') if ' ' in n else n} = let Source = 1 in Source;" for n in names)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("Formulas/Section1.m", m_text)
    pkg = buf.getvalue()
    blob = struct.pack("<II", 0, len(pkg)) + pkg + struct.pack("<I", 0)
    xml = ('<?xml version="1.0" encoding="utf-16"?><DataMashup xmlns="http://schemas.microsoft.com/DataMashup">'
           + base64.b64encode(blob).decode("ascii") + "</DataMashup>")
    return b"\xff\xfe" + xml.encode("utf-16-le")


@pytest.fixture
def wb_path(tmp_path: Path) -> Path:
    return make_workbook(tmp_path / "fixture.xlsx")


# ----------------------------------------------------------------------------- reading

def test_open_workbook_reads_sheets_cells_and_styles(wb_path: Path) -> None:
    before = (common.file_sha256(wb_path), wb_path.stat().st_mtime_ns)
    info = workbook.open_workbook(wb_path)
    assert (common.file_sha256(wb_path), wb_path.stat().st_mtime_ns) == before      # never writes
    assert info.sheet_names == ["inputs", "calc", "raw_sales", "hidden"]
    assert info.sha256 == before[0] and not info.truncated and info.cell_count > 10
    inputs, calc, raw, hidden = (info.sheet(n) for n in info.sheet_names)
    assert hidden.state == "hidden" and inputs.state == "visible"
    b1 = inputs.cell("B1")
    assert (b1.font_name, b1.font_size, b1.bold) == ("Arial", 9.0, True)
    assert inputs.cell("B6").fill == "D9D9D9" and inputs.cell("B7").fill == "F2F2F2"
    d6 = inputs.cell("D6")
    assert d6.fill == "FFF2CC" and d6.font_color == "0000FF" and d6.value == 1000 and d6.formula is None
    assert d6.cached == 1000
    assert inputs.cell("D7").border == {"bottom": {"style": "thin", "color": "808080"}}
    assert calc.cell("D7").formula == "=inputs!D6*2" and calc.cell("D7").formula_kind == "normal"
    assert calc.cell("H7").formula == "=_xlfn.XLOOKUP(1,A:A,B:B)"
    assert calc.freeze == "D4" and calc.gridlines is False and calc.zoom == 90
    assert calc.merged == ["B10:C10"] and calc.cf_count == 1 and calc.dv_count == 1
    assert inputs.gridlines is True
    assert raw.tables[0].name == "tbl_raw_sales" and raw.tables[0].ref == "A1:B3"
    assert raw.tables[0].columns == ["id", "amount"] and raw.tables[0].from_query is False
    assert ("rate", "inputs!$D$6", None) in info.defined_names
    assert ("loc", "calc!$D$7", "calc") in info.defined_names
    assert info.calc_mode == "auto" and info.iterate is False
    assert info.manifest is None


def test_array_formulas_and_members(wb_path: Path) -> None:
    calc = workbook.open_workbook(wb_path).sheet("calc")
    g7, g8 = calc.cell("G7"), calc.cell("G8")
    assert g7.formula == "=D7:D8*2" and g7.formula_kind == "array" and g7.array_ref == "G7:G8"
    assert g8.formula is None and g8.formula_kind == "array_member"
    assert calc.array_ranges == ["G7:G8"]
    assert [c.coord for c in calc.formulas()] == ["D7", "E7", "F7", "G7", "H7"]
    assert "G8" not in [c.coord for c in calc.constants()] and "D8" in [c.coord for c in calc.constants()]
    assert [c.coord for c in calc.cells_in("D7:E8")] == ["D7", "E7", "D8"]


def test_openpyxl_written_workbook_has_no_cached_values(wb_path: Path) -> None:
    info = workbook.open_workbook(wb_path)
    assert info.has_cached_values is False
    assert info.sheet("calc").cell("D7").cached is None


def test_cached_values_and_error_values(wb_path: Path, tmp_path: Path) -> None:
    out = inject_cached(wb_path, tmp_path / "cached.xlsx", "calc",
                        {"D7": ("2000", None), "E7": ("2005", None), "F7": ("#N/A", "e")})
    info = workbook.open_workbook(out)
    calc = info.sheet("calc")
    assert info.has_cached_values is True
    assert calc.cell("D7").cached == 2000 and not calc.cell("D7").is_error
    f7 = calc.cell("F7")
    assert f7.cached == "#N/A" and f7.cached_type == "e" and f7.is_error
    assert calc.cell("E7").formula == "=SUM(D7:D8)"          # formulas are kept next to the cached values


def test_with_styles_and_values_off(wb_path: Path) -> None:
    info = workbook.open_workbook(wb_path, with_values=False, with_styles=False)
    c = info.sheet("inputs").cell("B6")
    assert c.fill is None and c.font_name is None and c.value == "Scale"


def test_max_cells_truncates_with_a_warning(wb_path: Path) -> None:
    info = workbook.open_workbook(wb_path, max_cells=5)
    assert info.truncated and info.cell_count == 5
    assert any("only the first 5 non-empty cells" in w for w in info.warnings)


def test_chartsheet_is_listed(tmp_path: Path) -> None:
    import openpyxl
    from openpyxl.chart import BarChart, Reference
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([1])
    ws.append([2])
    cs = wb.create_chartsheet("chart1")
    ch = BarChart()
    ch.add_data(Reference(ws, min_col=1, min_row=1, max_row=2))
    cs.add_chart(ch)
    p = tmp_path / "cs.xlsx"
    wb.save(p)
    info = workbook.open_workbook(p)
    assert [s.kind for s in info.sheets] == ["worksheet", "chartsheet"]
    assert info.package.charts == 1


# ----------------------------------------------------------------------------- refusals (F06: a report, never a traceback)

def test_unreadable_files_are_refusals(tmp_path: Path) -> None:
    ole = tmp_path / "locked.xlsx"
    ole.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 600)
    with pytest.raises(common.Refusal, match="encrypted"):
        workbook.open_workbook(ole)
    xls = tmp_path / "old.xls"
    xls.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 600)
    with pytest.raises(common.Refusal, match="old binary .xls"):
        workbook.open_workbook(xls)
    junk = tmp_path / "junk.xlsx"
    junk.write_text("not a workbook", encoding="utf-8")
    with pytest.raises(common.Refusal, match="not a zip package"):
        workbook.open_workbook(junk)
    csv = tmp_path / "data.csv"
    csv.write_text("a,b\n", encoding="utf-8")
    with pytest.raises(common.Refusal, match="not a workbook extension"):
        workbook.open_workbook(csv)
    with pytest.raises(common.Refusal, match="not found"):
        workbook.open_workbook(tmp_path / "missing.xlsx")


def test_damaged_package_is_a_refusal(wb_path: Path, tmp_path: Path) -> None:
    cut = tmp_path / "cut.xlsx"
    cut.write_bytes(wb_path.read_bytes()[: wb_path.stat().st_size // 2])
    with pytest.raises(common.Refusal):
        workbook.open_workbook(cut)
    broken = rewrite(wb_path, tmp_path / "broken.xlsx", {"xl/workbook.xml": lambda b: b"<workbook"})
    with pytest.raises(common.Refusal, match="openpyxl cannot read"):
        workbook.open_workbook(broken)


# ----------------------------------------------------------------------------- package parts

def test_package_parts_of_a_plain_workbook(wb_path: Path) -> None:
    pkg = workbook.package_parts(wb_path)
    assert pkg.charts == 1 and pkg.tables == 1 and pkg.drawings == 1
    assert not pkg.has_power_query and pkg.queries == 0 and not pkg.vba
    assert any(f.startswith("charts (") for f in pkg.fragile)
    assert any(f.startswith("drawings (") for f in pkg.fragile)
    assert workbook.fragile_parts(wb_path) == pkg.fragile


def test_a_workbook_without_fragile_parts(tmp_path: Path) -> None:
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active["A1"] = 1
    p = tmp_path / "plain.xlsx"
    wb.save(p)
    assert workbook.fragile_parts(p) == []


def test_power_query_detection_and_query_names(wb_path: Path, tmp_path: Path) -> None:
    out = rewrite(wb_path, tmp_path / "pq.xlsx", {}, {"customXml/item1.xml": mashup_item(["q_sales", "My Query"]),
                                                     "customXml/item2.xml": b"<other/>"})
    pkg = workbook.package_parts(out)
    assert pkg.has_power_query and pkg.query_names == ["q_sales", "My Query"] and pkg.queries == 2
    pq = [f for f in pkg.fragile if f.startswith("Power Query")]
    assert pq == ["Power Query queries (DataMashup) (customXml/item1.xml)"]
    assert workbook.open_workbook(out).package.query_names == ["q_sales", "My Query"]


def test_worksheet_extensions_and_rich_text_are_fragile(wb_path: Path, tmp_path: Path) -> None:
    part = sheet_part(wb_path, "inputs")
    ext = (b'<extLst><ext uri="{CCE6A557-97BC-4b89-ADB6-D9C93CAAB3DF}" '
           b'xmlns:x14="http://schemas.microsoft.com/office/spreadsheetml/2009/9/main">'
           b'<x14:dataValidations count="0" xmlns:xm="http://schemas.microsoft.com/office/excel/2006/main"/>'
           b'</ext></extLst></worksheet>')
    rich = b"<si><r><rPr><b/></rPr><t>Bold</t></r><r><t> plain</t></r></si></sst>"
    with zipfile.ZipFile(wb_path) as z:
        has_sst = "xl/sharedStrings.xml" in z.namelist()
    edits = {part: lambda b: b.replace(b"</worksheet>", ext)}
    add = {}
    if has_sst:
        edits["xl/sharedStrings.xml"] = lambda b: b.replace(b"</sst>", rich)
    else:                                   # openpyxl 3.1.5 may write no shared-strings part
        add["xl/sharedStrings.xml"] = (b'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                                       + rich)
    out = rewrite(wb_path, tmp_path / "ext.xlsx", edits, add)
    pkg = workbook.package_parts(out)
    assert pkg.extensions == {part: ["dataValidations"]} and pkg.rich_text
    assert any(f.startswith("worksheet extensions") and "dataValidations" in f for f in pkg.fragile)
    assert any(f.startswith("rich text inside cells") for f in pkg.fragile)
    info = workbook.open_workbook(out)                      # openpyxl warns; the reader keeps the warning
    assert any("extension is not supported" in w for w in info.warnings)


def test_connections_count_power_query_providers(wb_path: Path, tmp_path: Path) -> None:
    conn = (b'<connections xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            b'<connection id="1" name="Query - q_sales" type="5"><dbPr connection="Provider=Microsoft.Mashup.OleDb.1;'
            b'Data Source=$Workbook$;Location=q_sales" command="SELECT * FROM [q_sales]"/></connection>'
            b'<connection id="2" name="odbc" type="1"><dbPr connection="DSN=x"/></connection></connections>')
    out = rewrite(wb_path, tmp_path / "conn.xlsx", {}, {"xl/connections.xml": conn})
    pkg = workbook.package_parts(out)
    assert pkg.connections == 2 and pkg.mashup_connections == 1 and pkg.queries == 1
    assert any(f.startswith("data connections") for f in pkg.fragile)


# ----------------------------------------------------------------------------- colours

def test_resolve_color() -> None:
    from openpyxl.styles.colors import Color
    assert workbook.resolve_color(Color(rgb="FFD9D9D9"), None) == "D9D9D9"
    assert workbook.resolve_color(Color(theme=0, tint=GREY_TINT), None) == "D9D9D9"
    assert workbook.resolve_color(Color(theme=0, tint=LIGHT_TINT), None) == "F2F2F2"
    assert workbook.resolve_color(Color(theme=1), None) == "000000"
    assert workbook.resolve_color(Color(theme=4), ["FFFFFF", "000000", "E7E6E6", "44546A", "123456"]) == "123456"
    assert workbook.resolve_color(Color(indexed=2), None) == "FF0000"
    assert workbook.resolve_color(Color(indexed=64), None) is None
    assert workbook.resolve_color(Color(auto=True), None) is None
    assert workbook.resolve_color(None, None) is None
    assert workbook.resolve_color("#fff2cc", None) == "FFF2CC" and workbook.resolve_color("nope", None) is None
    assert workbook.apply_tint("000000", 0.5) == "808080"


def test_theme_palette_parses_the_office_order() -> None:
    xml = ('<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:themeElements><a:clrScheme '
           'name="x"><a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1><a:lt1><a:sysClr val="window" '
           'lastClr="FFFFFF"/></a:lt1><a:dk2><a:srgbClr val="44546A"/></a:dk2><a:lt2><a:srgbClr val="E7E6E6"/></a:lt2>'
           '<a:accent1><a:srgbClr val="4472C4"/></a:accent1></a:clrScheme></a:themeElements></a:theme>')
    assert workbook.theme_palette(xml) == ["FFFFFF", "000000", "E7E6E6", "44546A", "4472C4"]
    assert workbook.theme_palette(None) is None and workbook.theme_palette("<bad") is None


# ----------------------------------------------------------------------------- snapshots and compare

def test_identical_workbooks_have_no_differences(wb_path: Path, tmp_path: Path) -> None:
    copy = tmp_path / "copy.xlsx"
    shutil.copy2(wb_path, copy)
    a, b = workbook.snapshot(wb_path, styles=True), workbook.snapshot(copy, styles=True)
    assert workbook.compare(a, b, workbook.Intended("none")) == []
    assert a.tables == {"tbl_raw_sales": "raw_sales!A1:B3"} and a.charts == 1
    assert a.names == {"rate": "inputs!$D$6", "calc!loc": "calc!$D$7"}
    assert a.cf == {"inputs": 0, "calc": 1, "raw_sales": 0, "hidden": 0} and a.dv["calc"] == 1
    assert ("calc", "G8") in a.formula_cells


def edited(src: Path, out: Path, fn) -> Path:
    import openpyxl
    wb = openpyxl.load_workbook(src)
    fn(wb)
    wb.save(out)
    return out


def test_compare_finds_changes_outside_the_intended_ones(wb_path: Path, tmp_path: Path) -> None:
    from openpyxl.styles import Font
    before = workbook.snapshot(wb_path, styles=True)

    def change(wb) -> None:
        wb["calc"]["E7"] = "=SUM(D7:D9)"
        wb["inputs"]["D6"] = 999
        wb["inputs"]["B6"].font = Font(name="Calibri", size=11)
        del wb.defined_names["rate"]
        wb["raw_sales"].tables.pop("tbl_raw_sales")
        wb["calc"].unmerge_cells("B10:C10")
        wb.create_sheet("extra")
    after = workbook.snapshot(edited(wb_path, tmp_path / "after.xlsx", change), styles=True)
    diffs = workbook.compare(before, after, workbook.Intended("none"))
    kinds = {d.kind for d in diffs}
    assert {"formula", "value", "style", "name", "table", "merged", "sheet"} <= kinds
    msgs = " | ".join(d.message for d in diffs)
    assert "calc!E7: formula changed ('=SUM(D7:D8)' -> '=SUM(D7:D9)')" in msgs
    assert "inputs!D6: value changed (1000 -> 999)" in msgs
    assert "sheet 'extra' was added but was not an intended change" in msgs
    assert "defined name 'rate' was removed" in msgs and "table 'tbl_raw_sales' was removed" in msgs
    styles_only = workbook.compare(before, after, workbook.Intended("styles_only"))
    assert "style" not in {d.kind for d in styles_only} and "formula" in {d.kind for d in styles_only}


def test_intended_cells_and_new_sheets_are_allowed(wb_path: Path, tmp_path: Path) -> None:
    before = workbook.snapshot(wb_path)

    def change(wb) -> None:
        wb["calc"]["F7"] = "=IFERROR(INDEX(A:A,split_helpers!A1),\"\")"
        ws = wb.create_sheet("split_helpers")
        ws["A1"] = "=MATCH(calc!B1,calc!C:C,0)"
    after = workbook.snapshot(edited(wb_path, tmp_path / "split.xlsx", change))
    intended = workbook.Intended("cells", cells={("calc", "F7")}, new_sheets={"split_helpers"})
    assert workbook.compare(before, after, intended) == []
    assert workbook.compare(before, after, workbook.Intended("none"))


def test_new_rows_are_allowed(wb_path: Path, tmp_path: Path) -> None:
    before = workbook.snapshot(wb_path)
    after = workbook.snapshot(edited(wb_path, tmp_path / "ext.xlsx",
                                     lambda wb: wb["calc"].__setitem__("D20", "=D19+1")))
    assert workbook.compare(before, after, workbook.Intended("cells", new_rows={"calc": (20, 30)})) == []
    assert [d.kind for d in workbook.compare(before, after, workbook.Intended("none"))] == ["formula"]


def test_values_of_intended_cells_must_stay_equal(wb_path: Path, tmp_path: Path) -> None:
    first = inject_cached(wb_path, tmp_path / "a.xlsx", "calc", {"E7": ("2005", None)})
    second = inject_cached(edited(wb_path, tmp_path / "b0.xlsx", lambda wb: wb["calc"].__setitem__("E7", "=D7+D8")),
                           tmp_path / "b.xlsx", "calc", {"E7": ("2006", None)})
    a, b = workbook.snapshot(first), workbook.snapshot(second)
    intended = workbook.Intended("cells", cells={("calc", "E7")})
    assert [d.message for d in workbook.compare(a, b, intended)] == ["calc!E7: cached value changed (2005 -> 2006)"]
    intended.values_may_change = True
    assert workbook.compare(a, b, intended) == []


def test_cached_values_compared_only_when_both_have_them(wb_path: Path, tmp_path: Path) -> None:
    cached = inject_cached(wb_path, tmp_path / "c.xlsx", "calc", {"D7": ("2000", None)})
    assert workbook.compare(workbook.snapshot(wb_path), workbook.snapshot(cached), workbook.Intended("none")) == []


def test_counts_of_charts_cf_and_dv(wb_path: Path, tmp_path: Path) -> None:
    before = workbook.snapshot(wb_path)
    after = workbook.snapshot(edited(wb_path, tmp_path / "x.xlsx", lambda wb: (
        wb["calc"].conditional_formatting._cf_rules.clear(), wb["calc"].data_validations.dataValidation.clear())))
    kinds = {d.kind for d in workbook.compare(before, after, workbook.Intended("styles_only"))}
    assert {"cf", "dv"} <= kinds


def test_compare_stops_at_max_differences(wb_path: Path, tmp_path: Path) -> None:
    def change(wb) -> None:
        for r in range(1, 30):
            wb["hidden"].cell(row=r, column=2, value=r)
    before = workbook.snapshot(wb_path)
    after = workbook.snapshot(edited(wb_path, tmp_path / "many.xlsx", change))
    assert len(workbook.compare(before, after, workbook.Intended("none"), max_differences=5)) == 5


def test_normalise_formula_and_values_equal() -> None:
    import datetime as dt
    assert workbook.normalise_formula('=_xlfn.xlookup(a1,"North",b:b)') == '=XLOOKUP(A1,"North",B:B)'
    assert workbook.normalise_formula(None) is None
    # v2 (A-aera-4): exact by default; a relative tolerance only when asked for (calculated values)
    assert not workbook.values_equal(0.1 + 0.2, 0.3) and workbook.values_equal(0.1 + 0.2, 0.3, tolerance=1e-9)
    assert not workbook.values_equal(1.0, 1.001) and not workbook.values_equal(1.0, 1.001, tolerance=1e-9)
    assert workbook.values_equal(dt.datetime(2026, 1, 31), 46053) and workbook.values_equal(None, "")
    assert not workbook.values_equal(True, 1) and workbook.values_equal("a", "a") and not workbook.values_equal("a", "A")


# ----------------------------------------------------------------------------- workbooks the plugin did not make

def test_every_foreign_workbook_ends_in_a_result_or_a_refusal(foreign_dir: Path) -> None:
    files = sorted(p for p in foreign_dir.iterdir() if p.suffix.lower() in (".xlsx", ".xlsm", ".xls", ".xlsb"))
    assert files
    read = 0
    for p in files:
        sha = common.file_sha256(p)
        try:
            info = workbook.open_workbook(p)
            read += 1
            assert info.sheets and info.cell_count > 0, p.name
        except common.Refusal as exc:
            assert exc.fix, p.name
        assert common.file_sha256(p) == sha, f"{p.name} was changed"
    assert read >= 1


def test_foreign_power_query_workbook(foreign_dir: Path) -> None:
    p = foreign_dir / "M2V3_end.xlsx"
    if not p.is_file():
        pytest.skip("M2V3_end.xlsx not copied into the foreign folder")
    info = workbook.open_workbook(p)
    assert info.package.has_power_query and "table_listing_query" in info.package.query_names
    assert any(t.from_query for t in info.tables())
    assert any(f.startswith("Power Query") for f in info.package.fragile)
    assert any(c.formula_kind == "array_member" for s in info.sheets for c in s.cells.values())


def test_foreign_chart_and_names_workbook(foreign_dir: Path) -> None:
    p = foreign_dir / "12 Month Rolling Forecast.xlsx"
    if not p.is_file():
        pytest.skip("12 Month Rolling Forecast.xlsx not copied into the foreign folder")
    snap = workbook.snapshot(p, styles=True)
    assert snap.charts >= 1 and snap.names and snap.has_cached_values and sum(snap.cf.values()) >= 1
    assert workbook.compare(snap, workbook.snapshot(p, styles=True), workbook.Intended("none")) == []


def test_pivot_workbook_reads_and_openpyxl_is_restored(foreign_dir: Path, tmp_path: Path) -> None:
    from openpyxl.reader import workbook as reader_workbook
    original = reader_workbook.WorkbookParser.__dict__["pivot_caches"]
    p = foreign_dir / "1.3-Testing-your-input-data.xlsx"
    if not p.is_file():
        pytest.skip("1.3-Testing-your-input-data.xlsx not copied into the foreign folder")
    info = workbook.open_workbook(p)
    assert info.package.pivots >= 1 and info.cell_count > 1000
    assert reader_workbook.WorkbookParser.__dict__["pivot_caches"] is original
    broken = tmp_path / "broken.xlsx"
    broken.write_bytes(b"PK" + b"\0" * 100)
    with pytest.raises(common.Refusal):
        workbook.open_workbook(broken)
    assert reader_workbook.WorkbookParser.__dict__["pivot_caches"] is original


def test_foreign_encrypted_and_xls_are_refused(foreign_dir: Path) -> None:
    for p in foreign_dir.iterdir():
        head = p.read_bytes()[:8]
        if head.startswith(b"\xd0\xcf\x11\xe0"):
            with pytest.raises(common.Refusal, match="encrypted|old binary"):
                workbook.open_workbook(p)

"""Tests of lib.classify: cell types of a workbook the plugin did not build, sheet types and sizes by density,
raw sheets, check cells, the manifest winning, and independence from styles (design sections 10.5, 14).
Owner: M2."""
from __future__ import annotations

import datetime as dt
import random
from pathlib import Path

import pytest

from lib import classify, common, dictionary, workbook


@pytest.fixture(scope="module")
def d() -> dictionary.Dictionary:
    return dictionary.load_dictionary()


def make_sheet(name: str, cells: dict[str, object], *, tables=(), index: int = 0) -> workbook.SheetInfo:
    """cells: coord -> constant, or ("=formula", cached value)."""
    sheet = workbook.SheetInfo(name=name, index=index, state="visible", max_row=1, max_col=1, freeze=None,
                               gridlines=True, zoom=None, merged=[], tables=list(tables), cf_count=0, dv_count=0)
    for coord, v in cells.items():
        col, row = common.split_cell(coord)
        if isinstance(v, tuple):
            f, cached = v
            ci = workbook.CellInfo(sheet=name, coord=coord, row=row, col=col, value=f, formula=f, cached=cached,
                                   data_type="f", formula_kind="normal")
        else:
            ci = workbook.CellInfo(sheet=name, coord=coord, row=row, col=col, value=v, formula=None, cached=v,
                                   data_type="s" if isinstance(v, str) else "n")
        sheet.cells[(row, col)] = ci
        sheet.max_row, sheet.max_col = max(sheet.max_row, row), max(sheet.max_col, col)
    return sheet


def make_wb(*sheets: workbook.SheetInfo, manifest=None) -> workbook.WorkbookInfo:
    pkg = workbook.PackageInfo(path=Path("x.xlsx"), parts=[], fragile=[])
    return workbook.WorkbookInfo(path=Path("x.xlsx"), sha256="0" * 64, package=pkg, sheets=list(sheets),
                                 defined_names=[], calc_mode="auto", iterate=False, has_cached_values=True,
                                 manifest=manifest)


# A block sheet laid out as design 6.3: title B1, master check B2/C2, heading, header row, rows, total, notes.
BLOCK = {
    "B1": "Revenue by region", "B2": "Master check", "C2": ("=checks!$C$2", 0),
    "B4": "Actual sales",
    "B5": "Region", "C5": "Unit", "D5": dt.datetime(2026, 1, 31), "E5": dt.datetime(2026, 2, 28), "F5": "Total",
    "B6": "East", "C6": "KRW", "D6": ("=SUMIFS(h!$G:$G,h!$C:$C,$B6)", 10.0), "E6": ("=SUMIFS(h!$G:$G,h!$C:$C,$B6)", 11.0),
    "F6": ("=SUM(D6:E6)", 21.0), "G6": "from the GL extract of 2026-03-02",
    "B7": "West", "C7": "KRW", "D7": ("=SUMIFS(h!$G:$G,h!$C:$C,$B7)", 5.0), "E7": ("=SUMIFS(h!$G:$G,h!$C:$C,$B7)", 6.0),
    "F7": ("=SUM(D7:E7)", 11.0),
    "B8": "Total", "D8": ("=SUM(D6:D7)", 15.0), "E8": ("=SUM(E6:E7)", 17.0), "F8": ("=SUM(F6:F7)", 32.0),
    "B10": "Growth rate", "C10": "%", "D10": 0.05, "E10": ("=D10", 0.05),
    "B11": "Prior total", "D11": ("=prior!D8", 30.0),
    "B12": "Ties to the GL control", "D12": ("=--(ABS(F8-D11)>0.001)", 1),
    "B14": "Note: the GL extract excludes intercompany lines, which are booked in the elimination workbook.",
}
EXPECT = {
    "B1": "title", "C2": "master_check", "B2": "row_header", "B4": "heading",
    "B5": "col_header", "C5": "col_header", "D5": "col_header", "E5": "col_header", "F5": "col_header",
    "B6": "row_header", "C6": "unit", "D6": "calc", "E6": "calc", "F6": "total", "G6": "note",
    "B7": "row_header", "C7": "unit", "F7": "total",
    "B8": "row_header", "D8": "total", "E8": "total", "F8": "total",
    "B10": "row_header", "C10": "unit", "D10": "input", "E10": "link",
    "B11": "row_header", "D11": "import",
    "B12": "row_header", "D12": "check",
    "B14": "note",
}


def test_block_sheet_types() -> None:
    types = classify.classify_sheet(make_sheet("calc", BLOCK))
    got = {f"{common.col_letter(c)}{r}": t for (r, c), t in types.items()}
    for coord, want in EXPECT.items():
        assert got[coord] == want, (coord, got[coord], want)
    assert set(got) == set(BLOCK)
    assert set(got.values()) <= set(classify.CELL_TYPES)


def test_raw_sheet_header_row_and_raw_cells() -> None:
    s = make_sheet("raw_sales", {"A1": "invoice", "B1": "amount", "A2": "INV001", "B2": 10.5, "A3": "INV002",
                                 "B3": ("=1+1", 2)})
    types = classify.classify_sheet(s, raw=True)
    assert types[(1, 1)] == types[(1, 2)] == "col_header"
    assert {types[(2, 1)], types[(2, 2)], types[(3, 1)], types[(3, 2)]} == {"raw"}


def test_guess_raw_sheets_by_name_and_query_table() -> None:
    q = workbook.TableInfo(name="tblQ", sheet="Loaded", ref="A1:B3", columns=["a", "b"], from_query=True)
    wb = make_wb(make_sheet("raw_sales", {"A1": "x"}), make_sheet("Data", {"A1": "x"}, index=1),
                 make_sheet("sales_raw", {"A1": "x"}, index=2), make_sheet("Loaded", {"A1": "x"}, tables=[q], index=3),
                 make_sheet("Dashboard", {"A1": "x"}, index=4), make_sheet("calc", {"A1": "x"}, index=5))
    assert classify.guess_raw_sheets(wb) == {"raw_sales", "Data", "sales_raw", "Loaded"}
    assert classify.raw_sheet_names(wb, raw_sheets={"calc"}) == {"raw_sales", "Data", "sales_raw", "Loaded", "calc"}


def test_raw_sheets_option_and_manifest() -> None:
    wb = make_wb(make_sheet("listing", {"A1": "id", "A2": 5, "A3": 6}))
    assert classify.classify_workbook(wb)[("listing", "A2")] == "input"
    assert classify.classify_workbook(wb, raw_sheets={"listing"})[("listing", "A2")] == "raw"
    manifest = {"rotm_manifest": 1, "workbook": "x.xlsx",
                "sheets": [{"name": "listing", "type": "raw", "font_size": 8, "table": "t"}]}
    assert classify.classify_workbook(wb, manifest)[("listing", "A2")] == "raw"


def test_manifest_cell_types_win() -> None:
    wb = make_wb(make_sheet("calc", BLOCK))
    manifest = {"rotm_manifest": 1, "workbook": "x.xlsx", "sheets": [{"name": "calc", "type": "calc", "font_size": 8}],
                "cell_types": {"calc": [["D6:E7", "output"], ["D6", "calc"]]}}
    types = classify.classify_workbook(wb, manifest)
    assert types[("calc", "D6")] == "calc"           # the later range wins
    assert types[("calc", "E7")] == "output"
    assert types[("calc", "B1")] == "title"          # cells the manifest does not list: the classifier


def test_check_cells_by_label_result_text_and_check_sheet() -> None:
    s1 = make_sheet("Model", {"B3": "Balance check", "D3": ("=D1-D2", 0), "E3": ("=E1-E2", 0),
                              "B5": "Balance sheet", "D5": ("=D1", 3),
                              "B7": "Result", "D7": ('=IF(D1=D2,"OK","ERROR")', "OK"),
                              "B9": "Overall check", "D9": ("=MAX(D3:E3)", 0), "E9": ("=D9", 0)})
    s2 = make_sheet("Checks", {"B2": "Assets", "D2": ("=A1=B1", True), "D3": ("=SUM(A1:A3)", 6)}, index=1)
    wb = make_wb(s1, s2)
    found = set(classify.guess_check_cells(wb))
    assert {("Model", "D3"), ("Model", "E3"), ("Model", "D7"), ("Model", "D9"), ("Model", "E9"), ("Checks", "D2")} <= found
    assert ("Model", "D5") not in found            # "Balance sheet" is not a check label
    assert ("Checks", "D3") not in found           # no comparison
    types = classify.classify_workbook(wb)
    assert types[("Model", "D9")] == "master_check" and types[("Model", "E9")] == "check"
    assert types[("Model", "D7")] == "check"       # a check that shows text is still a check


def test_formula_showing_text_left_of_values_is_a_row_label() -> None:
    s = make_sheet("calc", {"B5": "Region", "D5": "Jan", "E5": "Feb",
                            "B6": ("=inputs!$D$12", "East"), "D6": ("=1+1", 2), "E6": ("=2+2", 4),
                            "B7": ("=inputs!$D$13", "West"), "D7": ("=1+1", 2), "E7": ("=2+2", 4),
                            "H6": ('=B6&" region"', "East region")})
    types = classify.classify_sheet(s)
    assert types[(6, 2)] == types[(7, 2)] == "row_header"
    assert types[(6, 8)] == "calc"                 # a formula right of the values stays a formula


def test_timeline_headers_from_formulas_with_cached_dates() -> None:
    s = make_sheet("calc", {"B6": "Period end", "D6": ("=EOMONTH(inputs!$D$6,0)", dt.datetime(2026, 1, 31)),
                            "E6": ("=EOMONTH(D6,1)", dt.datetime(2026, 2, 28)),
                            "B7": "Sales", "D7": ("=1*1", 1), "E7": ("=1*2", 2),
                            "B8": "Cost", "D8": ("=1*1", 1), "E8": ("=1*2", 2)})
    types = classify.classify_sheet(s)
    assert types[(6, 4)] == types[(6, 5)] == types[(6, 2)] == "col_header"


def test_year_headers_and_year_inputs() -> None:
    s = make_sheet("inputs", {"B3": "Item", "D3": 2025, "E3": 2026,
                              "B4": "Sales", "D4": 100, "E4": 110, "B5": "Cost", "D5": 50, "E5": 55,
                              "B8": "Start year", "D8": 2025})
    types = classify.classify_sheet(s)
    assert types[(3, 4)] == types[(3, 5)] == "col_header"
    assert types[(8, 4)] == "input" and types[(8, 2)] == "row_header"


def test_exports_are_cells_read_by_another_sheet() -> None:
    a = make_sheet("calc", {"B2": "Sales", "D2": ("=1+2", 3), "D3": ("=D2*2", 6)})
    b = make_sheet("summary", {"B2": "Sales", "D2": ("=calc!D2*1", 3)}, index=1)
    types = classify.classify_workbook(make_wb(a, b))
    assert types[("calc", "D2")] == "export" and types[("calc", "D3")] == "calc"


def test_classification_does_not_depend_on_styles(tmp_path: Path) -> None:
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "calc"
    for coord, v in BLOCK.items():
        ws[coord] = v[0] if isinstance(v, tuple) else v
    plain = tmp_path / "plain.xlsx"
    wb.save(plain)
    rng = random.Random(20261125)
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is not None:
                cell.font = Font(name=rng.choice(["Calibri", "Times New Roman", "Arial"]), sz=rng.choice([8, 10, 14]),
                                 b=rng.random() < 0.5, color=rng.choice(["FF0000FF", "FFFF0000", "FF000000"]))
                cell.fill = PatternFill("solid", fgColor=rng.choice(["FFFFFF00", "FFD9D9D9", "FF1F3864"]))
    styled = tmp_path / "styled.xlsx"
    wb.save(styled)
    a = classify.classify_workbook(workbook.open_workbook(plain))
    b = classify.classify_workbook(workbook.open_workbook(styled))
    assert a == b


# ----------------------------------------------------------------------------- sheet types and sizes

def _grid(rows: int, cols: int, fill: float = 1.0) -> dict[str, object]:
    rng = random.Random(7)
    out: dict[str, object] = {}
    for r in range(1, rows + 1):
        for c in range(1, cols + 1):
            if rng.random() < fill:
                out[f"{common.col_letter(c)}{r}"] = r * c
    return out


def test_guess_sheet_type_by_density_and_name(d: dictionary.Dictionary) -> None:
    dense = make_sheet("Model", _grid(60, 12))              # 720 cells, 100% filled
    sparse = make_sheet("Model", _grid(60, 12, 0.3))
    small = make_sheet("Model", _grid(10, 10))               # 100 cells: not a brick wall
    assert classify.guess_sheet_type(dense) == "calc" and d.sheet_size("calc") == 8
    assert classify.guess_sheet_type(sparse) == "output" and d.sheet_size("output") == 9
    assert classify.guess_sheet_type(small) == "output"
    assert classify.guess_sheet_type(make_sheet("Assumptions", _grid(10, 3))) == "inputs"
    assert classify.guess_sheet_type(make_sheet("Cover", {"B2": "x"})) == "cover"
    assert classify.guess_sheet_type(make_sheet("Checks", {"B2": "x"})) == "checks"
    assert classify.guess_sheet_type(make_sheet("Inputs", _grid(60, 12))) == "calc"   # dense wins over the name
    assert classify.guess_sheet_type(make_sheet("raw_lines", {"A1": "x"})) == "raw"
    assert classify.guess_sheet_type(make_sheet("h_sales", _grid(60, 12))) == "helpers"
    assert classify.guess_sheet_type(make_sheet("empty", {})) == "output"


def test_density_thresholds_come_from_the_dictionary(tmp_path: Path, d: dictionary.Dictionary) -> None:
    sheet = make_sheet("Model", _grid(20, 10))              # 200 cells, 100%
    assert classify.guess_sheet_type(sheet, d) == "output"
    path = tmp_path / "style_overrides.json"
    common.write_json(path, {"rotm_dictionary_override": 1, "set": {"rules": {
        "dense_min_cells": {"value": 100, "source": "project: test"}}}})
    assert classify.guess_sheet_type(sheet, dictionary.load_dictionary(path)) == "calc"
    assert d.rule("dense_fill_ratio") == 0.6 and d.rule("dense_min_cells") == 500


def boundary_cells(rows: int, cols: int, filled: int) -> dict[str, object]:
    """`filled` numbers in a block of rows x cols cells from A1: the first filled - 1 cells in row order and the
    block's last cell, so that the used block is exactly rows x cols."""
    out: dict[str, object] = {}
    order = [(r, c) for r in range(1, rows + 1) for c in range(1, cols + 1)]
    for r, c in order[:filled - 1] + [order[-1]]:
        out[f"{common.col_letter(c)}{r}"] = r * 100 + c
    assert len(out) == filled
    return out


@pytest.mark.parametrize("rows, cols, filled, dense", [
    (20, 25, 300, True),      # 300 filled cells in a block of 500 cells (60%)
    (20, 25, 299, False),     # 299 in 500 (59.8%)
    (499, 1, 300, False),     # 300 in 499 (60.1%): the block holds fewer than 500 cells
    (20, 25, 500, True),      # the whole block
])
def test_density_boundary_follows_d18(d: dictionary.Dictionary, rows: int, cols: int, filled: int,
                                      dense: bool) -> None:
    """v9 (r8 verdict A-aera-16; SYNTHESIS D18): dense when the used block holds at least dense_min_cells cells
    and at least dense_fill_ratio of them are filled. v8 compared the 500 with the filled cells, so 300 filled
    cells in a block of 500 cells was not dense."""
    sheet = make_sheet("Block", boundary_cells(rows, cols, filled))
    dens = classify.density(sheet)
    assert (dens["filled"], dens["area"]) == (filled, rows * cols)
    for dic in (None, d):
        st = classify.guess_sheet_type(sheet, dic)
        assert (st == "calc") is dense, (rows, cols, filled, st)
    plan = classify.plan_workbook(make_wb(sheet), d)["Block"]
    assert plan.size == (8 if dense else 9)
    ratio = f"{filled / (rows * cols):.1%}"
    what = f"{filled} filled cells in a block of {rows * cols} cells ({ratio})"
    if dense:
        assert plan.reason == f"dense: {what}, at least 60% filled and a block of at least 500 cells"
    else:
        assert plan.reason == f"not dense: {what}"


def test_plan_workbook_manifest_wins(d: dictionary.Dictionary) -> None:
    wb = make_wb(make_sheet("calc", {"B2": "x", "D2": 1}), make_sheet("raw_sales", {"A1": "a"}, index=1),
                 make_sheet("Other", _grid(60, 12), index=2))
    manifest = {"rotm_manifest": 1, "workbook": "x.xlsx",
                "sheets": [{"name": "calc", "type": "calc", "font_size": 9}, {"name": "raw_sales", "type": "raw",
                                                                             "font_size": 8, "table": "sales"}]}
    plan = classify.plan_workbook(wb, d, manifest)
    assert (plan["calc"].sheet_type, plan["calc"].size, plan["calc"].source) == ("calc", 9, "manifest")
    assert plan["raw_sales"].raw and plan["raw_sales"].size == 8
    assert (plan["Other"].sheet_type, plan["Other"].size, plan["Other"].source) == ("calc", 8, "inferred")
    plain = classify.plan_workbook(make_wb(make_sheet("calc", {"B2": "x"})), d)
    assert plain["calc"].size == 9 and "not dense" in plain["calc"].reason


# ----------------------------------------------------------------------------- workbooks the plugin did not make

def test_foreign_copies_classify_without_error(foreign_dir: Path, d: dictionary.Dictionary) -> None:
    done = 0
    for path in sorted(foreign_dir.glob("*.xls*")):
        try:
            info = workbook.open_workbook(path)
        except common.Refusal:
            continue                                         # encrypted or .xls: the reader refuses with a fix
        types = classify.classify_workbook(info)
        plan = classify.plan_workbook(info, d)
        assert set(types.values()) <= set(classify.CELL_TYPES), path.name
        assert all(p.size in (8, 9) for p in plan.values()), path.name
        n_cells = sum(len(s.cells) for s in info.sheets if s.kind == "worksheet")
        assert len(types) == n_cells, path.name              # every non-empty cell has a type
        done += 1
    if done == 0:                                            # any supplied set is tested for what it holds (J-2)
        pytest.skip(f"no workbook in {foreign_dir} that the reader opens")

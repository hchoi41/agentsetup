"""Tests of package P6 in round 3 (v3): split's helper cells in the comparison and in the dictionary's look
([A-aera-9, A-aera-18]), diff of a formula replaced by a typed value ([A-aera-16]) and trace labels from the
cell's own row or column header ([A-aera-14]). Owner: P6 (v3)."""
from __future__ import annotations

import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
SKILL = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(HERE / "data" / "compat"))

import m7_fixtures as fx7  # noqa: E402

from lib import audit, common, diff, split, trace  # noqa: E402
from lib import dictionary as dict_mod  # noqa: E402

STYLE_RULES = ("S-FONT", "S-SIZE", "S-FILL", "S-FONTCOLOR", "S-BOLD")


@pytest.fixture
def no_excel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")


def _with_cached(path: Path, sheet_xml: str, cached: dict[str, object]) -> Path:
    """Write cached values into formula cells of one sheet (openpyxl writes an empty <v> for a formula), so a
    test holds workbooks with values saved for their formulas without Excel."""
    tmp = path.with_suffix(".tmp.xlsx")
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == sheet_xml:
                text = data.decode("utf-8")
                for coord, value in cached.items():
                    text, n = re.subn(rf'(<c r="{coord}"[^>]*>)(<f>.*?</f>)(<v\s*/>|<v></v>)',
                                      rf"\g<1>\g<2><v>{value}</v>", text)
                    assert n == 1, coord
                data = text.encode("utf-8")
            zout.writestr(item, data)
    os.replace(tmp, path)
    return path


# ============================================================================ [A-aera-9, A-aera-18] split

def test_guard_name_is_the_iferror_or_ifna_around_the_moved_part() -> None:
    assert split.guard_name("=IFERROR(ABS(ROUND(1/A1,0)),0)", "ROUND(1/A1,0)") == "IFERROR"
    assert split.guard_name("=IFNA(ABS(INDEX(B:B,MATCH(C1,D:D,0))),0)", "MATCH(C1,D:D,0)") == "IFNA"
    # IFERROR and IFNA are named before an IF that also stands between them and the part
    assert split.guard_name("=IFERROR(IF(A1,ROUND(ABS(1/A1),0),0),0)", "ABS(1/A1)") == "IFERROR"
    # another function that keeps an error from the result is named when no IFERROR or IFNA encloses the part
    assert split.guard_name("=IF(A1=0,0,ROUND(ABS(1/A1),0))", "ABS(1/A1)") == "IF"
    assert split.guard_name("=ROUND(MAX(MIN(A1,3),0),0)", "MIN(A1,3)") is None


def test_guard_problems_name_a_helper_error_that_the_host_did_not_show() -> None:
    links = [split.HelperLink("Model", "B1", "B2", "=IFERROR(ABS(ROUND(1/A1,0)),0)", "ROUND(1/A1,0)"),
             split.HelperLink("Model", "C1", "C2", "=ROUND(MAX(MIN(A1,3),0),0)", "MIN(A1,3)"),
             split.HelperLink("Model", "D1", "D2", "=IFERROR(ABS(ROUND(1/A1,0)),0)", "ROUND(1/A1,0)")]
    before = {("Model", "B1"): 0, ("Model", "C1"): 0, ("Model", "D1"): "#DIV/0!"}
    after = {(split.HELPER_SHEET, "B2"): "#DIV/0!", (split.HELPER_SHEET, "C2"): 0,
             (split.HELPER_SHEET, "D2"): "#DIV/0!"}
    problems = split.guard_problems(links, before, after)
    # D1 already showed the error before the split, so its helper adds none; C2 holds no error
    assert [(p.helper, p.error, p.guard, p.host) for p in problems] == [("B2", "#DIV/0!", "IFERROR", "B1")]
    assert problems[0].message() == ("split_helpers!B2 holds #DIV/0!, which the original formula's IFERROR hid; "
                                     "split would put an error into the workbook")


@pytest.mark.excel
def test_excel_split_of_a_guarded_division_by_zero_gets_split_guard(tmp_path: Path) -> None:
    # v4, A-aera-14: a part under IFERROR or IFNA is refused before any value is read (test_v4_split.py); the
    # comparison of the new helper cells still finds an error that another guard (here ISERROR) kept out of the result;
    # a part in a branch of IF carries the condition (v6, test_v6_split.py)
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Model"
    ws["A1"] = 0
    ws["B1"] = "=--ISERROR(ABS(ROUND(1/A1,0)))"
    src = tmp_path / "guard.xlsx"
    wb.save(src)
    before = common.file_sha256(src)
    rep = split.split_cell(src, "Model!B1")
    assert rep.verified["recalculated"] is True
    assert rep.exit_code == common.EXIT_FINDINGS, [f.line() for f in rep.findings]
    guard = [f for f in rep.findings if f.rule == "SPLIT-GUARD"]
    assert len(guard) == 1 and guard[0].severity == "error"
    assert guard[0].sheet == split.HELPER_SHEET and guard[0].cell == "B2"
    assert "split_helpers!B2" in guard[0].message and "formula's ISERROR hid" in guard[0].message
    assert "#DIV/0!" in guard[0].message
    assert not any(f.rule == "SPLIT-DONE" for f in rep.findings)
    assert rep.data["written"] is False and not (tmp_path / "guard_split.xlsx").exists()
    assert common.file_sha256(src) == before


def test_split_helper_sheet_cells_get_the_dictionary_look(tmp_path: Path, no_excel: None) -> None:
    import openpyxl
    src = fx7.deep_cases(tmp_path / "deep.xlsx")
    rep = split.split_cell(src, "calc!D4")
    assert rep.data["written"] is True
    out = Path(rep.data["output"])
    d = dict_mod.load_dictionary()
    size = d.sheet_size("helpers")
    ws = openpyxl.load_workbook(out)[split.HELPER_SHEET]
    head = ws["B1"]
    assert head.font.name == d.font_name == "Arial" and float(head.font.sz) == size
    assert head.font.b is True                                  # the col_header look: bold
    for r in range(2, 8):
        cell = ws[f"B{r}"]
        assert cell.font.name == "Arial" and float(cell.font.sz) == size, cell.coordinate
    rep_a = audit.audit_workbook(out, strict=True, record=False)
    on_helpers = [f.line() for f in rep_a.findings if f.sheet == split.HELPER_SHEET and f.rule in STYLE_RULES]
    assert on_helpers == []


def test_split_into_an_existing_helper_sheet_styles_only_the_new_cells(tmp_path: Path, no_excel: None) -> None:
    import openpyxl
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "calc"
    for r in range(1, 5):
        ws.cell(r, 1, r * 2)
        ws.cell(r, 2, f"=ROUND(MAX(MIN(A{r},3),0),0)")
    ws["D1"] = "=IFERROR(INDEX(A1:A4,MATCH(2,A1:A4,0)),0)"
    helpers = wb.create_sheet(split.HELPER_SHEET)
    helpers["Z50"] = "kept as it was"
    wb.save(tmp_path / "s.xlsx")
    rep = split.split_cell(tmp_path / "s.xlsx", "calc!D1")
    assert rep.data["written"] is True
    out = openpyxl.load_workbook(Path(rep.data["output"]))[split.HELPER_SHEET]
    size = dict_mod.load_dictionary().sheet_size("helpers")
    # the existing sheet's last column is Z, so split writes after one blank column: AB1 and AB2
    assert "helper 1" in str(out["AB1"].value) and out["AB2"].value.startswith("=IFERROR(MATCH(")   # H5 (v4)
    for coord in ("AB1", "AB2"):
        assert out[coord].font.name == "Arial" and float(out[coord].font.sz) == size, coord
    assert out["Z50"].font.name == "Calibri" and float(out["Z50"].font.sz) == 11   # not written by split: kept


@pytest.mark.excel
def test_excel_split_through_excel_gives_the_helper_sheet_the_dictionary_look(tmp_path: Path) -> None:
    import openpyxl
    src = fx7.with_chart_and_names(tmp_path / "chart.xlsx")
    rep = split.split_cell(src, "calc!D2")
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    assert any("through Excel" in n for n in rep.notes)
    out = Path(rep.data["output"])
    size = dict_mod.load_dictionary().sheet_size("helpers")
    ws = openpyxl.load_workbook(out)[split.HELPER_SHEET]
    for coord in ("B1", "B2", "B7"):
        assert ws[coord].font.name == "Arial" and float(ws[coord].font.sz) == size, coord
    rep_a = audit.audit_workbook(out, strict=True, record=False)
    assert [f.line() for f in rep_a.findings if f.sheet == split.HELPER_SHEET and f.rule in STYLE_RULES] == []


# ============================================================================ [A-aera-16] diff

def _typed_pair(tmp_path: Path, old_b1: object, new_b1: object, old_cached: dict[str, object],
                new_cached: dict[str, object]) -> tuple[Path, Path]:
    from openpyxl import Workbook
    paths = []
    for name, b1, cached in (("old", old_b1, old_cached), ("new", new_b1, new_cached)):
        wb = Workbook()
        ws = wb.active
        ws.title = "Model"
        ws["A1"] = 5
        ws["B1"] = b1
        ws["C1"] = "=B1+1"
        p = tmp_path / name / "w.xlsx"
        p.parent.mkdir(parents=True, exist_ok=True)
        wb.save(p)
        paths.append(_with_cached(p, "xl/worksheets/sheet1.xml", cached))
    return paths[0], paths[1]


def test_diff_formula_replaced_by_a_typed_value_shows_both_sides(tmp_path: Path) -> None:
    old, new = _typed_pair(tmp_path, "=A1*0", 99, {"B1": 0, "C1": 1}, {"C1": 100})
    rep = diff.diff_workbooks(old, new)
    assert rep.data["outputs_compared"] is True
    f = [x for x in rep.findings if x.rule == "D-FORMULA" and x.cell == "B1"]
    assert len(f) == 1
    assert "=A1*0" in f[0].message and "saved value 0" in f[0].message and "typed value 99" in f[0].message
    outputs = {x.cell: x.message for x in rep.findings if x.rule == "D-OUTPUT"}
    assert "B1" in outputs and "0 -> 99" in outputs["B1"]
    assert "C1" in outputs                                  # the dependent cell is still listed
    group = rep.data["sheets"]["Model"]["formulas"][0]
    assert group["ref"] == "B1" and group["old"] == "=A1*0" and group["new"] is None
    assert group["old_value"] == 0 and group["new_value"] == 99
    md = diff.write_markdown(rep, tmp_path / "d.md").read_text(encoding="utf-8")
    row = next(line for line in md.splitlines() if line.startswith("| B1 |") and "removed" in line)
    assert "99" in row and "=A1*0" in row
    assert any(line.startswith("| B1 | 0 | 99 |") for line in md.splitlines())


def test_diff_typed_value_replaced_by_a_formula_shows_both_sides(tmp_path: Path) -> None:
    old, new = _typed_pair(tmp_path, 5, "=A1+1", {"C1": 6}, {"B1": 6, "C1": 7})
    rep = diff.diff_workbooks(old, new)
    f = [x for x in rep.findings if x.rule == "D-FORMULA" and x.cell == "B1"]
    assert len(f) == 1
    assert "typed value 5" in f[0].message and "=A1+1" in f[0].message and "saved value 6" in f[0].message
    outputs = {x.cell: x.message for x in rep.findings if x.rule == "D-OUTPUT"}
    assert "B1" in outputs and "5 -> 6" in outputs["B1"]


def test_diff_typed_value_replaced_by_a_formula_of_the_same_value_is_no_output_change(tmp_path: Path) -> None:
    old, new = _typed_pair(tmp_path, 6, "=A1+1", {"C1": 7}, {"B1": 6, "C1": 7})
    rep = diff.diff_workbooks(old, new)
    assert any(x.rule == "D-FORMULA" and x.cell == "B1" for x in rep.findings)
    assert not any(x.rule == "D-OUTPUT" for x in rep.findings)


def test_diff_formula_replaced_by_a_typed_value_without_saved_values(tmp_path: Path) -> None:
    from openpyxl import Workbook
    paths = []
    for name, b1 in (("old", "=A1*0"), ("new", 99)):
        wb = Workbook()
        wb.active.title = "Model"
        wb.active["A1"] = 5
        wb.active["B1"] = b1
        p = tmp_path / name / "w.xlsx"
        p.parent.mkdir(parents=True, exist_ok=True)
        wb.save(p)
        paths.append(p)
    rep = diff.diff_workbooks(*paths)
    f = [x for x in rep.findings if x.rule == "D-FORMULA" and x.cell == "B1"]
    assert len(f) == 1 and "no saved value" in f[0].message and "typed value 99" in f[0].message
    assert not any(x.rule == "D-OUTPUT" for x in rep.findings)


# ============================================================================ [A-aera-14] trace

def _worked_copy(tmp_path: Path, *, manifest: bool = True) -> Path:
    src = SKILL / "examples" / "sales_vs_target"
    dest = tmp_path / "worked"
    dest.mkdir()
    shutil.copy2(src / "model.xlsx", dest / "model.xlsx")
    if manifest:
        shutil.copy2(src / "model.manifest.json", dest / "model.manifest.json")
    return dest / "model.xlsx"


def test_trace_row_header_cell_is_printed_with_its_own_text(tmp_path: Path) -> None:
    wb = _worked_copy(tmp_path)
    line = trace.trace_cell(wb, "calc!B7").data["lines"][0]
    assert line.startswith("calc!B7 'North' / 'Region' = 'North'"), line
    assert "'East'" not in line
    # an input of a list: its own row's header and its column's header, not the list item above it
    line = trace.trace_cell(wb, "inputs!D13").data["lines"][0]
    assert line.startswith("inputs!D13 '2' / 'Value'"), line
    assert "'East'" not in line


def test_trace_labels_of_value_cells_on_the_worked_example(tmp_path: Path) -> None:
    wb = _worked_copy(tmp_path)
    lines = trace.trace_cell(wb, "calc!D7", depth=1).data["lines"]
    assert lines[0].startswith("calc!D7 'North' = "), lines[0]
    assert any(line.strip().startswith("- calc!B7 'North' / 'Region' = 'North'") for line in lines), lines
    line = trace.trace_cell(wb, "calc!D10", depth=1).data["lines"][0]
    assert line.startswith("calc!D10 'Total' = "), line


def test_trace_row_header_without_a_manifest_is_printed_with_its_own_text(tmp_path: Path) -> None:
    wb = _worked_copy(tmp_path, manifest=False)
    line = trace.trace_cell(wb, "calc!B7").data["lines"][0]
    assert line.startswith("calc!B7 'North' = 'North'"), line
    line = trace.trace_cell(wb, "inputs!D13").data["lines"][0]
    assert "'East'" not in line, line

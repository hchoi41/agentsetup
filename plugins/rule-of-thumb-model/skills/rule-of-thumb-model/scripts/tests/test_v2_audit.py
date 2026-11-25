"""Tests of the v2 audit changes (package P4). Owner: P4.

- A-aera-11, without a manifest: C-BLOCK (inferred) reports each calculation block that no inferred check cell
  reads. A block is two or more formula cells that hold one relative formula down a column or along a row, on a
  sheet that is not raw and not a checks sheet; a check reads it when its formula refers to one of its cells.
- A-aera-11, with a manifest: a registered check result cell that holds no formula is C-MISSING (error).
- A-aera-5 (home P2), the audit's part: V-NO-VALUE (error) lists formula cells that hold no value in a workbook
  that has cached values (workbook.formula_cells_without_value, written by P1).
Workbooks are written with openpyxl into tmp_path; the shipped model folders are audited as copies in tmp_path.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib import audit, common  # noqa: E402

SHIPPED_MODELS = [SKILL / "examples" / "sales_vs_target", SKILL / "templates" / "starter"] + [
    PLUGIN / "templates" / n for n in ("ar_aging", "budget_vs_actual", "price_volume_mix", "rolling_forecast")]
FIX = "add a check that ties this block to an independent total (references/checks.md)"


def write_wb(path: Path, sheets: dict[str, dict[str, Any]]) -> Path:
    """An .xlsx from {sheet: {coord: value}}, every cell in Arial 8."""
    import openpyxl
    from openpyxl.styles import Font
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, cells in sheets.items():
        ws = wb.create_sheet(name)
        for coord, value in cells.items():
            ws[coord] = value
            ws[coord].font = Font(name="Arial", size=8)
    wb.save(path)
    return path


def run(path: Path, **kw: Any) -> common.Report:
    kw.setdefault("strict", True)
    kw.setdefault("record", False)
    return audit.audit_workbook(path, **kw)


def blocks_of(rep: common.Report) -> list[common.Finding]:
    return [f for f in rep.findings if f.rule == "C-BLOCK"]


def cli_audit(target: Path, *args: str) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), "audit", str(target), *args]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=600)


# ----------------------------------------------------------------------------- inferred blocks (A-aera-11)

def two_blocks(check_second: bool = False) -> dict[str, dict[str, Any]]:
    """calc!C2:C6 (grown amounts) and calc!D2:D6 (the growth per row); the checks sheet reads C2:C6, and D2:D6
    only when `check_second`."""
    raw: dict[str, Any] = {"A1": "id", "B1": "amount"}
    calc: dict[str, Any] = {"B1": "row", "C1": "grown", "D1": "growth"}
    for r in range(2, 7):
        raw[f"A{r}"] = f"S{r:03d}"
        raw[f"B{r}"] = 100 * r
        calc[f"B{r}"] = r
        calc[f"C{r}"] = f"=raw_sales!B{r}*(1+inputs!$C$2)"
        calc[f"D{r}"] = f"=C{r}-raw_sales!B{r}"
    checks: dict[str, Any] = {
        "B2": "Master check", "C2": "=--(SUM(D4:D6)>0)", "B3": "Check",
        "B4": "Grown total ties", "D4": "=--(ABS(SUM(calc!$C$2:$C$6)-SUM(raw_sales!$B:$B)*(1+inputs!$C$2))>0)",
        "B5": "Rows", "D5": "=--(COUNT(raw_sales!$B:$B)<>COUNTA(raw_sales!$A:$A)-1)"}
    if check_second:
        checks.update({"B6": "Growth ties", "D6": "=--(ABS(SUM(calc!$D$2:$D$6)-SUM(raw_sales!$B:$B)*inputs!$C$2)>0)"})
    return {"inputs": {"B2": "Growth rate", "C2": 0.1}, "raw_sales": raw, "calc": calc, "checks": checks}


def test_two_blocks_with_a_check_on_one_reports_the_other_once(tmp_path: Path) -> None:
    rep = run(write_wb(tmp_path / "two.xlsx", two_blocks()))
    hits = blocks_of(rep)
    assert len(hits) == 1, [f.line() for f in hits]
    f = hits[0]
    assert f.severity == "warning" and f.sheet == "calc" and f.cell == "D2"
    assert f.message.startswith("(inferred)") and "calc!D2:D6" in f.message and "first cell D2" in f.message
    assert f.fix == FIX
    assert f.data["range"] == "D2:D6" and f.data["first_cell"] == "D2" and f.data["cells"] == 5
    assert f.data["inferred"] is True
    assert "inferred calculation blocks: 2; 1 read by a check cell, 1 read by none (C-BLOCK)" in rep.notes
    assert rep.exit_code == 1                          # a warning fails the strict audit


def test_a_check_on_each_block_reports_nothing(tmp_path: Path) -> None:
    rep = run(write_wb(tmp_path / "each.xlsx", two_blocks(check_second=True)))
    assert blocks_of(rep) == [], [f.line() for f in blocks_of(rep)]


def test_a_check_that_reads_one_cell_or_an_overlapping_range_reads_the_block(tmp_path: Path) -> None:
    sheets = two_blocks()
    sheets["checks"]["D4"] = "=--(ABS(SUM(calc!$C$5:$D$9)-calc!$C$2)>0)"     # overlaps C5:C6 and D5:D6
    assert blocks_of(run(write_wb(tmp_path / "overlap.xlsx", sheets))) == []
    sheets = two_blocks()
    sheets["checks"]["D5"] = "=--(calc!$D$6<0)"                               # one cell of D2:D6
    assert blocks_of(run(write_wb(tmp_path / "one.xlsx", sheets))) == []
    sheets = two_blocks()
    sheets["checks"]["D5"] = "=--(SUM(calc!$D:$D)<0)"                         # the whole column
    assert blocks_of(run(write_wb(tmp_path / "col.xlsx", sheets))) == []


def test_a_block_along_a_row_and_a_grid_are_one_finding_each(tmp_path: Path) -> None:
    sheets = two_blocks(check_second=True)
    calc = sheets["calc"]
    calc.update({"B9": "year", "C9": 1, "D9": 2, "E9": 3, "F9": 4})
    for col in "CDEF":
        calc[f"{col}10"] = f"={col}9*inputs!$C$2"                             # along row 10
        for r in (13, 14, 15):
            calc[f"{col}{r}"] = f"={col}$9*$B{r}"                             # a grid C13:F15, one formula
    for r in (13, 14, 15):
        calc[f"B{r}"] = r
    hits = blocks_of(run(write_wb(tmp_path / "rows.xlsx", sheets)))
    by_range = {f.data["range"]: f for f in hits}
    assert set(by_range) == {"C10:F10", "C13:F15"}, [f.line() for f in hits]
    assert "along row 10" in by_range["C10:F10"].message and by_range["C10:F10"].cell == "C10"
    assert by_range["C13:F15"].data["cells"] == 12 and by_range["C13:F15"].cell == "C13"


def test_blocks_on_raw_and_checks_sheets_are_not_reported(tmp_path: Path) -> None:
    sheets = two_blocks(check_second=True)
    for r in range(2, 7):
        sheets["raw_sales"][f"C{r}"] = f"=B{r}*2"                             # R-FORMULA, not C-BLOCK
        sheets["checks"][f"F{r + 6}"] = f"=calc!C{r}+0"                       # a run on the checks sheet
    rep = run(write_wb(tmp_path / "rawchk.xlsx", sheets))
    assert "R-FORMULA" in {f.rule for f in rep.findings}
    assert blocks_of(rep) == [], [f.line() for f in blocks_of(rep)]


def test_no_check_cells_means_no_block_finding(tmp_path: Path) -> None:
    sheets = two_blocks()
    del sheets["checks"]
    rep = run(write_wb(tmp_path / "none.xlsx", sheets))
    assert blocks_of(rep) == []
    assert any("no check cells" in f.message for f in rep.findings if f.rule == "C-MISSING")


def test_check_cells_on_a_calculation_sheet_are_not_a_block(tmp_path: Path) -> None:
    """A row labelled check on a calculation sheet holds check cells; they read the rows above them."""
    sheets = two_blocks()
    calc = sheets["calc"]
    calc.update({"A8": "Check", "C8": "=--(C2<raw_sales!B2)", "D8": "=--(D2<0)"})
    hits = blocks_of(run(write_wb(tmp_path / "rowcheck.xlsx", sheets)))
    assert hits == [], [f.line() for f in hits]                               # D8 reads D2, so D2:D6 is read


def t5_like() -> dict[str, dict[str, Any]]:
    """Like the T5 case: Settlement!B2:B6 is a block no check reads; the other blocks are checked."""
    sales: dict[str, Any] = {"A1": "Item", "B1": "Units", "C1": "Revenue"}
    settle: dict[str, Any] = {"A1": "Item", "B1": "Settled"}
    for r in range(2, 7):
        sales.update({f"A{r}": f"Item {r}", f"B{r}": 10 * r, f"C{r}": f"=B{r}*Inputs!$B$2"})
        settle.update({f"A{r}": f"Item {r}", f"B{r}": f"=Sales!C{r}*Inputs!$B$3"})
    return {
        "Inputs": {"A2": "Price", "B2": 25, "A3": "Settlement share", "B3": 0.9, "A4": "Revenue control", "B4": 5000},
        "Sales": sales,
        "Settlement": settle,
        "Summary": {"A2": "Revenue", "B2": "=SUM(Sales!C2:C6)", "A3": "Settled", "B3": "=SUM(Settlement!B2:B6)"},
        "Checks": {"A1": "Checks", "A2": "Master check", "B2": "=--(SUM(B4:B5)>0)",
                   "A4": "Revenue ties the control", "B4": "=--(ABS(SUM(Sales!C2:C6)-Inputs!$B$4)>0)",
                   "A5": "Units are positive", "B5": "=--(MIN(Sales!B2:B6)<0)"},
    }


def test_a_t5_like_unchecked_block_is_reported(tmp_path: Path) -> None:
    rep = run(write_wb(tmp_path / "t5.xlsx", t5_like()))
    hits = blocks_of(rep)
    assert [(f.sheet, f.data["range"], f.cell) for f in hits] == [("Settlement", "B2:B6", "B2")], \
        [f.line() for f in hits]
    assert "Settlement!B2:B6" in hits[0].message and "(inferred)" in hits[0].message
    fixed = t5_like()
    fixed["Checks"].update({"A6": "Settled ties revenue", "B6": "=--(ABS(SUM(Settlement!B2:B6)-Summary!B2*Inputs!$B$3)>0)"})
    fixed["Checks"]["B2"] = "=--(SUM(B4:B6)>0)"
    assert blocks_of(run(write_wb(tmp_path / "t5_fixed.xlsx", fixed))) == []


def test_a_defined_name_that_names_the_block_reads_it(tmp_path: Path) -> None:
    import openpyxl
    from openpyxl.workbook.defined_name import DefinedName
    p = write_wb(tmp_path / "named.xlsx", two_blocks())
    wb = openpyxl.load_workbook(p)
    wb.defined_names["growth_cells"] = DefinedName("growth_cells", attr_text="calc!$D$2:$D$6")
    wb["checks"]["D5"] = "=--(SUM(growth_cells)<0)"
    wb.save(p)
    assert blocks_of(run(p)) == []


# ----------------------------------------------------------------------------- registered check cells (A-aera-11)

def _copy_model(src: Path, dest: Path) -> Path:
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("*.bak_*"))
    return dest


def _drop_formula(path: Path, sheet: str, coord: str) -> None:
    """Remove the formula of one cell in the saved XML and keep its cached value (a check replaced by its value,
    as a hand edit in Excel would leave it)."""
    with zipfile.ZipFile(path) as z:
        part = audit._sheet_part_names(z)[sheet]
        items = {n: z.read(n) for n in z.namelist()}
    xml = items[part].decode("utf-8")
    pattern = re.compile(rf'(<c r="{coord}"[^>]*>)<f>[^<]*</f>')
    assert len(pattern.findall(xml)) == 1, f"{sheet}!{coord} holds no plain formula"
    items[part] = pattern.sub(r"\1", xml).encode("utf-8")
    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for n, data in items.items():
            z.writestr(n, data)
    tmp.replace(path)


def test_a_check_result_replaced_by_its_value_fails_the_strict_audit(tmp_path: Path) -> None:
    model = _copy_model(SKILL / "examples" / "sales_vs_target", tmp_path / "example")
    man = json.loads((model / "model.manifest.json").read_text(encoding="utf-8"))
    assert any(c["result"] == "checks!I5" for c in man["checks"])
    clean = cli_audit(model, "--strict", "--json", "-", "--quiet", "--no-record")
    assert "Traceback" not in clean.stderr
    assert not [f for f in json.loads(clean.stdout)["findings"] if f["rule"] == "C-MISSING"]
    _drop_formula(model / "model.xlsx", "checks", "I5")
    proc = cli_audit(model, "--strict", "--json", "-", "--quiet", "--no-record")
    assert proc.returncode == 1 and "Traceback" not in proc.stderr
    hits = [f for f in json.loads(proc.stdout)["findings"] if f["rule"] == "C-MISSING"]
    assert [(f["sheet"], f["cell"], f["severity"]) for f in hits] == [("checks", "I5", "error")], hits
    assert "checks!I5" in hits[0]["message"] and "typed value 0" in hits[0]["message"]


# ----------------------------------------------------------------------------- the shipped models and foreign set

@pytest.mark.parametrize("src", SHIPPED_MODELS, ids=[p.name for p in SHIPPED_MODELS])
def test_shipped_models_get_no_new_finding(src: Path, tmp_path: Path) -> None:
    if not src.is_dir() and src.parent == PLUGIN / "templates":
        pytest.skip(f"the plugin's templates folder is absent (skill copied alone): {src.name}")   # v4, A-aera-7
    if not (src / "model.xlsx").is_file():
        pytest.skip(f"{src.name} has no built workbook")
    model = _copy_model(src, tmp_path / src.name)
    rep = run(model / "model.xlsx")
    new = [f.line() for f in rep.findings if f.rule in ("C-BLOCK", "C-MISSING", "V-NO-VALUE")]
    assert new == []
    assert "A-RULE-FAILED" not in {f.rule for f in rep.findings}


def test_foreign_set_ends_with_a_report(foreign_dir: Path, tmp_path: Path) -> None:
    files = sorted(p for p in foreign_dir.glob("*.xls*") if p.is_file() and ".bak_" not in p.name)
    for src in files:
        p = tmp_path / src.name
        shutil.copy2(src, p)
        proc = cli_audit(p, "--strict", "--json", "-", "--quiet")
        assert proc.returncode in (0, 1), (src.name, proc.stderr[-500:])
        assert "Traceback" not in proc.stderr, src.name
        data = json.loads(proc.stdout)
        assert data["command"] == "audit"
        assert "A-RULE-FAILED" not in {f["rule"] for f in data["findings"]}, src.name


# ----------------------------------------------------------------------------- V-NO-VALUE (A-aera-5, the audit's part)

@pytest.mark.excel
def test_a_circular_pair_saved_by_excel_is_reported(tmp_path: Path) -> None:
    from lib import excel, workbook
    if not hasattr(workbook, "formula_cells_without_value"):
        pytest.fail("lib.workbook.formula_cells_without_value (package P1) is missing")
    p = write_wb(tmp_path / "circ.xlsx", {"calc": {"A1": "=B1+1", "B1": "=A1+1", "C1": 5, "D1": "=C1*2",
                                                     "E1": '=""'}})
    excel.recalc(p)
    rep = run(p)
    hits = [f for f in rep.findings if f.rule == "V-NO-VALUE"]
    cells = {c for f in hits for c in f.cells}
    assert {"A1", "B1"} <= cells, [f.line() for f in rep.findings]
    assert "D1" not in cells and "E1" not in cells                            # a value, and an empty text result
    assert all(f.severity == "error" and f.sheet == "calc" for f in hits)
    assert all(f.fix.startswith("the saved file holds no value for this formula: ") for f in hits)   # N-AUDIT (v3)
    assert rep.exit_code == 1


def test_the_shipped_example_has_no_cell_without_value(tmp_path: Path) -> None:
    from lib import workbook
    if not hasattr(workbook, "formula_cells_without_value"):
        pytest.fail("lib.workbook.formula_cells_without_value (package P1) is missing")
    model = _copy_model(SKILL / "examples" / "sales_vs_target", tmp_path / "example")
    rep = run(model / "model.xlsx")
    assert rep.data["has_cached_values"] is True
    assert [f.line() for f in rep.findings if f.rule == "V-NO-VALUE"] == []

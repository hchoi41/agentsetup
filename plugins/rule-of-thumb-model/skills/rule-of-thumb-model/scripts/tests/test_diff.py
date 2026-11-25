"""Tests for lib/diff.py (owner M8): formulas, constants, outputs, raw sheets and structure that changed between
two versions of a workbook."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from lib import diff

DATA = Path(__file__).resolve().parent / "data" / "lift"
if str(DATA) not in sys.path:
    sys.path.insert(0, str(DATA))
import fixture  # noqa: E402

ROTM = Path(__file__).resolve().parents[1] / "rotm.py"


def rotm(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(ROTM), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=300, env=dict(os.environ))


@pytest.fixture
def pair(tmp_path: Path) -> tuple[Path, Path]:
    p = fixture.write_model(tmp_path / "old")
    new_dir = tmp_path / "new"
    shutil.copytree(p["root"], new_dir)
    return p["workbook"], new_dir / "model.xlsx"


def edit(path: Path, fn) -> None:
    import openpyxl
    wb = openpyxl.load_workbook(path)
    fn(wb)
    wb.save(path)


def rules(report) -> list[str]:
    return [f.rule for f in report.findings]


def test_identical_workbooks_have_no_differences(pair):
    old, new = pair
    report = diff.diff_workbooks(old, new)
    assert report.findings == [] and report.exit_code == 0
    assert report.data["counts"]["formulas"] == 0 and "no differences" in report.notes[-1]
    assert any("outputs not compared" in n for n in report.notes)       # openpyxl-written: no saved values


def test_a_formula_changed_down_a_column_is_one_group(pair):
    old, new = pair

    def change(wb):
        ws = wb["h_sales"]
        for r in range(2, 42):
            ws[f"I{r}"] = f"=G{r}/inputs!$D$8"
    edit(new, change)
    report = diff.diff_workbooks(old, new)
    f = [x for x in report.findings if x.rule == "D-FORMULA"]
    assert len(f) == 1
    assert f[0].count == 40 and f[0].sheet == "h_sales" and f[0].message.startswith("I2:I41 (40 cells): formula changed")
    assert "=G2/inputs!$D$6 -> =G2/inputs!$D$8" in f[0].message
    assert report.data["counts"]["formulas"] == 40


def test_a_changed_input_is_named_by_its_id(pair):
    old, new = pair
    edit(new, lambda wb: wb["inputs"].__setitem__("D6", 2000))
    report = diff.diff_workbooks(old, new)
    f = [x for x in report.findings if x.rule == "D-INPUT"]
    assert len(f) == 1 and f[0].cell == "D6"
    assert "input scale changed: 1000 -> 2000" in f[0].message


def test_raw_sheet_is_summarised_by_rows_and_digest(pair):
    old, new = pair

    def change(wb):
        ws = wb["raw_sales"]
        ws["E7"] = 1.5
        ws.append(["INV09999", 1, None, "East", 10.0])
    edit(new, change)
    report = diff.diff_workbooks(old, new)
    raw = [x for x in report.findings if x.rule == "D-RAW"]
    assert len(raw) == 1
    assert "40 rows -> 41 rows" in raw[0].message and "1 rows added at the end" in raw[0].message
    assert "1 rows changed (first: row 7)" in raw[0].message and "digest changed" in raw[0].message
    assert all(f.sheet != "raw_sales" for f in report.findings if f.rule in ("D-INPUT", "D-FORMULA"))


def test_formula_on_a_raw_sheet_is_named(pair):
    old, new = pair
    edit(new, lambda wb: wb["raw_sales"].__setitem__("E3", "=E2*2"))
    report = diff.diff_workbooks(old, new)
    assert any("holds 1 formula cells" in f.message for f in report.findings if f.rule == "D-RAW")


def test_sheets_added_removed_and_structure(pair):
    old, new = pair

    def change(wb):
        wb.create_sheet("scratch")
        del wb["cover"]
        from openpyxl.workbook.defined_name import DefinedName
        wb.defined_names["rate"] = DefinedName("rate", attr_text="inputs!$D$6")
    edit(new, change)
    report = diff.diff_workbooks(old, new)
    msgs = [f.message for f in report.findings]
    assert "sheet 'scratch' was added" in msgs and "sheet 'cover' was removed" in msgs
    assert any(m.startswith("defined name 'rate'") for m in msgs)
    assert report.data["sheets_added"] == ["scratch"] and report.data["sheets_removed"] == ["cover"]


def test_markdown_lists_every_change(pair, tmp_path):
    old, new = pair

    def change(wb):
        wb["inputs"]["D6"] = 2000
        wb["calc"]["E25"] = "=D25+E24+0"
    edit(new, change)
    report = diff.diff_workbooks(old, new)
    md = diff.write_markdown(report, tmp_path / "diff.md").read_text(encoding="utf-8")
    assert "# Workbook differences" in md and "| inputs | 0 | 1 | 0 |" in md
    assert "| D6 | changed | 1000 | 2000 | input scale |" in md
    assert "`=D25+E24`" in md and "`=D25+E24+0`" in md
    assert "Outputs were not compared" in md


def test_cli_with_model_folders_json_and_fail_on_change(pair, tmp_path):
    old, new = pair
    res = rotm("diff", str(old.parent), str(new.parent), "--json", "-")
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["data"]["counts"]["formulas"] == 0
    edit(new, lambda wb: wb["inputs"].__setitem__("D8", 6000))
    res = rotm("diff", str(old), str(new), "--fail-on-change", "--md", str(tmp_path / "d.md"))
    assert res.returncode == 1 and "D-INPUT" in res.stdout and (tmp_path / "d.md").is_file()
    res = rotm("diff", str(old), str(tmp_path / "missing.xlsx"))
    assert res.returncode == 2 and "not found" in res.stderr


@pytest.mark.excel
def test_outputs_that_changed_after_recalculation(pair):
    """After Excel recalculates both versions, a changed input shows as the input and every output it moved."""
    from lib import excel
    old, new = pair
    assert excel.recalc(old, save=True).ok
    edit(new, lambda wb: wb["inputs"].__setitem__("D6", 2000))
    assert excel.recalc(new, save=True).ok
    report = diff.diff_workbooks(old, new)
    assert report.data["outputs_compared"] is True
    outputs = [f for f in report.findings if f.rule == "D-OUTPUT"]
    sheets = {f.sheet for f in outputs}
    assert {"h_sales", "calc", "summary"} <= sheets
    amount_k = [f for f in outputs if f.sheet == "h_sales"]
    assert len(amount_k) == 1 and amount_k[0].count == 40 and amount_k[0].message.startswith("I2:I41")
    assert any("[key output pct]" in f.message for f in outputs)
    assert [f for f in report.findings if f.rule == "D-INPUT"][0].message.endswith("1000 -> 2000")


def test_foreign_workbook_pair_reports_without_traceback(foreign_dir, tmp_path):
    """A workbook the plugin did not make, against a copy with one constant changed."""
    import openpyxl
    for src in sorted(foreign_dir.glob("*.xlsx"))[:6]:
        a = tmp_path / src.name
        shutil.copy2(src, a)
        try:
            wb = openpyxl.load_workbook(a)
        except Exception:  # noqa: BLE001 - a copy openpyxl cannot open is not the subject of this test
            continue
        ws = wb.worksheets[0]
        b = tmp_path / ("changed_" + src.name)
        ws.cell(ws.max_row + 2, 1, "rotm diff test")
        wb.save(b)
        report = diff.diff_workbooks(a, b)
        assert any(f.rule == "D-INPUT" and "rotm diff test" in f.message for f in report.findings), src.name
        return
    pytest.skip("no foreign .xlsx copy openpyxl can open")

"""v3 tests of the shared core (package P1, entry [A-aera-15, A-aera-17]).

- Formula cells without a value: a formula whose result is empty text and that holds a volatile function or
  depends on one is not V-NO-VALUE; the cells of a cycle still are. The fix "find the cycle and break it" is
  given only for cells on the cycle Excel names; another formula without a value gets the neutral fix.
- The stored @ operator: `_xlfn.SINGLE(<any argument>)` is shown as `@<argument>` and is not a function in the
  nesting depth.

Owner: P1 (v3). Machine-specific strings (folders of this PC, user names) are built at run time, never written here.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib import common, excel, workbook
from lib import formula as F

SCRIPTS = Path(__file__).resolve().parents[1]
ROTM = SCRIPTS / "rotm.py"

CYCLE_FIX = "a formula that refers to itself through other cells has no value: find the cycle with"
NEUTRAL_FIX = "Excel saved this formula without a value and named no circular reference for it"


def run_cli(*args: str, timeout: int = 900) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(ROTM), *args], capture_output=True,
                          text=True, encoding="utf-8", timeout=timeout)


def rules(formula: str) -> list[str]:
    return [i.rule for i in F.check_formula(formula)]


# ============================================================================= the stored @ operator (A-aera-17)

STORED_AT = "=_xlfn.SINGLE(INDEX(A1:A3,MATCH(B1,A1:A3,0)))"      # how Excel 16 saved =@INDEX(...) (reviewer)


def test_stored_at_operator_is_not_counted(tmp_path: Path) -> None:
    a = F.analyze(STORED_AT)
    assert a.ok, a.error
    assert a.formula == "=@INDEX(A1:A3,MATCH(B1,A1:A3,0))"
    assert a.depth == 2 and a.chain == ("INDEX", "MATCH")
    assert F.depth(STORED_AT) == 2
    assert "SINGLE" not in a.functions
    assert "F-DEPTH" not in rules(STORED_AT)
    assert F.to_file_formula(a.formula) == STORED_AT                  # back to the text Excel stores
    # a real three-deep formula is still refused
    assert F.depth("=ROUND(SUM(ABS(A1)),0)") == 3
    assert "F-DEPTH" in rules("=ROUND(SUM(ABS(A1)),0)")
    deep = "=_xlfn.SINGLE(IFERROR(INDEX(A:A,MATCH(B1,C:C,0)),0))"
    assert F.depth(deep) == 3 and "F-DEPTH" in rules(deep)
    # the audit of a workbook that holds the stored form
    from openpyxl import Workbook
    path = tmp_path / "at.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Probe"
    for r, v in enumerate((1, 2, 3), start=1):
        ws.cell(row=r, column=1, value=v)
    ws["B1"] = 2
    ws["H1"] = STORED_AT
    ws["H2"] = "=ROUND(SUM(ABS(A1)),0)"
    wb.save(path)
    proc = run_cli("audit", str(path), "--json", "-", "--no-record", timeout=300)
    assert proc.returncode in (0, 1), proc.stdout + proc.stderr
    report = json.loads(proc.stdout)
    depth_cells = {(f["sheet"], c) for f in report["findings"] if f["rule"] == "F-DEPTH" for c in f["cells"]}
    assert ("Probe", "H2") in depth_cells
    assert ("Probe", "H1") not in depth_cells


@pytest.mark.parametrize("stored, shown, depth", [
    ("=_xlfn.SINGLE(A1:A3)", "=@A1:A3", 0),                                     # a reference (as in v2)
    ("=_xlfn.SINGLE(Sheet2!A1:A3)*2", "=@Sheet2!A1:A3*2", 0),
    ("=SUM(_xlfn.SINGLE(A1:A3))", "=SUM(@A1:A3)", 1),
    ("=_xlfn.SINGLE(_xlfn.XLOOKUP(B1,A1:A3,C1:C3))", "=@XLOOKUP(B1,A1:A3,C1:C3)", 1),   # a call
    ('=_xlfn.SINGLE(INDEX(A:A,MATCH(")",B:B,0)))', '=@INDEX(A:A,MATCH(")",B:B,0))', 2),  # text with a bracket
    ("=_xlfn.SINGLE(INDEX(A1:A3,_xlfn.SINGLE(B1:B3)))", "=@INDEX(A1:A3,@B1:B3)", 1),        # nested
    ("=_xlfn.SINGLE(A1:A3+1)", "=@(A1:A3+1)", 0),                               # an expression keeps its group
    ("=1+_xlfn.SINGLE(SUM(A1:A3)*B1:B3)", "=1+@(SUM(A1:A3)*B1:B3)", 1),
    ("=_xlfn.SINGLE(\nINDEX(A1:A3,2))", "=\n@INDEX(A1:A3,2)", 1),               # a line break stays outside
])
def test_stored_at_operator_with_any_argument(stored: str, shown: str, depth: int) -> None:
    a = F.analyze(stored)
    assert a.ok, a.error
    assert a.formula == shown
    assert a.depth == depth
    assert "SINGLE" not in a.functions and "" not in a.functions
    if "\n" not in stored:
        assert F.to_file_formula(shown) == stored
        assert F.to_file_formula(stored) == stored                    # idempotent


def test_stored_at_operator_keeps_the_v2_spacing() -> None:
    assert F.strip_xlfn("=_xlfn.SINGLE( A1:A3 )") == "= @A1:A3 "
    assert F.strip_xlfn("=_xlfn.SINGLE( INDEX(A1:A3,2) )") == "= @INDEX(A1:A3,2) "
    assert F.strip_xlfn('="_xlfn.SINGLE(A1)"&_xlfn.SINGLE(A1:A3)') == '="_xlfn.SINGLE(A1)"&@A1:A3'


def test_at_before_a_group_is_not_a_call() -> None:
    a = F.analyze("=@(A1:A3*2)")
    assert a.ok and a.depth == 0 and a.functions == ()
    assert F.to_file_formula("=@(A1:A3*2)") == "=_xlfn.SINGLE(A1:A3*2)"
    assert F.depth("=SUM(@(A1:A3*2))") == 1


# ============================================================================= cells without a value (A-aera-15)

def _cell_xml(ref: str, formula_text: str, value: str | None, *, text_type: bool, ca: bool) -> str:
    t = ' t="str"' if text_type else ""
    f = f'<f ca="1">{formula_text}</f>' if ca else f"<f>{formula_text}</f>"
    v = "<v/>" if value == "" else ("" if value is None else f"<v>{value}</v>")
    return f'<c r="{ref}"{t}>{f}{v}</c>'


def _saved_like_excel(path: Path) -> None:
    """A workbook whose sheet XML holds the forms Excel 16 saved on this PC (measured 2026-11-25, build/v3/P1):
    a volatile empty-text result and a cycle both as <c t="str"><f ca="1">...</f><v/></c>."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Probe"
    ws["A1"] = 1
    ws["C2"] = '=IF(TODAY()>1,"","later")'
    ws["C3"] = "=TODAY()"
    ws["D2"] = '=C2&""'
    ws["E2"] = "=A1*2"
    ws["F2"] = '=OFFSET(A1,0,0)&""'
    ws["G2"] = "=SUM(OFFSET(A1,0,0,1,1))"
    lp = wb.create_sheet("Loop")
    lp["A1"] = "=B1+1"
    lp["B1"] = "=A1+1"
    lp["C1"] = "=A1*2"
    lp["X1"] = "=Y1+TODAY()*0"
    lp["Y1"] = "=X1+1"
    wb.save(path)
    forms = {
        "sheet1.xml": {
            "C2": _cell_xml("C2", 'IF(TODAY()&gt;1,"","later")', "", text_type=True, ca=True),
            "C3": _cell_xml("C3", "TODAY()", "46351", text_type=False, ca=True),
            "D2": _cell_xml("D2", 'C2&amp;""', "", text_type=True, ca=True),
            "E2": _cell_xml("E2", "A1*2", "2", text_type=False, ca=False),
            "F2": _cell_xml("F2", 'OFFSET(A1,0,0)&amp;""', "", text_type=True, ca=True),
            "G2": _cell_xml("G2", "SUM(OFFSET(A1,0,0,1,1))", "1", text_type=False, ca=True),
        },
        "sheet2.xml": {k: _cell_xml(k, f, "", text_type=True, ca=True) for k, f in
                       (("A1", "B1+1"), ("B1", "A1+1"), ("C1", "A1*2"), ("X1", "Y1+TODAY()*0"), ("Y1", "X1+1"))},
    }
    with zipfile.ZipFile(path) as z:
        parts = {i.filename: z.read(i.filename) for i in z.infolist()}
    for name, cells in forms.items():
        key = f"xl/worksheets/{name}"
        xml = parts[key].decode("utf-8")
        for ref, new in cells.items():
            xml, n = re.subn(rf'<c r="{ref}"[^>]*>.*?</c>', new, xml, count=1)
            assert n == 1, (name, ref)
        parts[key] = xml.encode("utf-8")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in parts.items():
            z.writestr(name, data)


def test_volatile_empty_text_cells_are_left_out(tmp_path: Path) -> None:
    path = tmp_path / "saved.xlsx"
    _saved_like_excel(path)
    # C2, D2 and F2 (volatile, empty text, no cycle shown by their references) are left out; the cycle with a
    # volatile function (X1, Y1) stays; C1 depends on the cycle and is not volatile
    assert workbook.formula_cells_without_value(path) == [
        ("Loop", "A1"), ("Loop", "B1"), ("Loop", "C1"), ("Loop", "X1"), ("Loop", "Y1")]
    found = workbook.cells_without_value(path, named=[("Probe", "C3"), ("Probe", "G2"), ("Loop", "A1")])
    assert found.listed == workbook.formula_cells_without_value(path)
    assert found.left_out == [("Probe", "C2"), ("Probe", "D2"), ("Probe", "F2")]
    # Excel named C3 (=TODAY(): no references) and G2 (OFFSET, with a value in the file) as the circular reference
    # of their sheet: not circular
    assert found.not_circular == [("Probe", "C3"), ("Probe", "G2")]
    assert found.on_named_cycle == {("Loop", "A1"), ("Loop", "B1")}


def test_cells_on_reference_cycles(tmp_path: Path) -> None:
    path = tmp_path / "saved.xlsx"
    _saved_like_excel(path)
    snap = workbook.snapshot(path)
    cells = [("Probe", "C2"), ("Probe", "D2"), ("Probe", "F2"), ("Loop", "A1"), ("Loop", "C1"), ("Loop", "X1")]
    assert workbook.cells_on_reference_cycles(snap, cells) == {("Loop", "A1"), ("Loop", "X1")}


def test_merge_names_a_cycle_only_for_cells_on_the_named_cycle(tmp_path: Path) -> None:
    path = tmp_path / "saved.xlsx"
    _saved_like_excel(path)
    steps = [{"op": "errors", "ok": True, "index": 1, "count": 2, "truncated": False,
              "cells": [["Probe", "C3", excel.CIRCULAR_TEXT], ["Loop", "A1", excel.CIRCULAR_TEXT]]}]
    excel._merge_cells_without_value(steps, [{"op": "errors"}], path)
    texts = {(c[0], c[1]): c[2] for c in steps[0]["cells"]}
    assert texts == {("Loop", "A1"): excel.CIRCULAR_TEXT, ("Loop", "B1"): excel.CIRCULAR_TEXT,
                     ("Loop", "C1"): excel.NO_VALUE_TEXT, ("Loop", "X1"): excel.NO_VALUE_TEXT,
                     ("Loop", "Y1"): excel.NO_VALUE_TEXT}
    assert steps[0]["count"] == 5 and steps[0]["truncated"] is False


def test_named_volatile_cell_is_dropped_without_a_save(tmp_path: Path) -> None:
    path = tmp_path / "saved.xlsx"
    _saved_like_excel(path)
    steps = [{"op": "errors", "ok": True, "index": 1, "count": 3, "truncated": False,
              "cells": [["Probe", "C2", excel.CIRCULAR_TEXT], ["Loop", "A1", excel.CIRCULAR_TEXT],
                        ["Probe", "E3", "#DIV/0!"]]}]
    excel._drop_named_volatile_cells(steps, path)
    assert steps[0]["cells"] == [["Loop", "A1", excel.CIRCULAR_TEXT], ["Probe", "E3", "#DIV/0!"]]
    assert steps[0]["count"] == 2


def test_recalc_fix_texts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                          capsys: pytest.CaptureFixture[str]) -> None:
    from openpyxl import Workbook
    path = tmp_path / "book.xlsx"
    Workbook().save(path)
    fake = excel.RecalcResult(ok=True, saved_path=None, input_sha256="0" * 64, output_sha256=None,
                              error_cells=[("S", "A1", excel.CIRCULAR_TEXT), ("S", "B1", excel.NO_VALUE_TEXT),
                                           ("S", "C1", "#DIV/0!")],
                              values={}, pid=1, elapsed_s=0.1, error_count=3, excel_version="16.0")
    monkeypatch.setattr(excel, "excel_status", lambda: SimpleNamespace(available=True, reason=None))
    monkeypatch.setattr(excel, "recalc", lambda *a, **k: fake)
    code = excel.run_recalc(argparse.Namespace(workbook=str(path), read=[], no_save=True, out=None, json="-",
                                               quiet=False))
    assert code == common.EXIT_FINDINGS
    report = json.loads(capsys.readouterr().out)
    by_cell = {f["cells"][0]: f for f in report["findings"]}
    assert by_cell["A1"]["rule"] == "V-NO-VALUE" and by_cell["A1"]["fix"].startswith(CYCLE_FIX)
    assert by_cell["A1"]["fix"].endswith("S!A1 and break it")
    assert by_cell["B1"]["rule"] == "V-NO-VALUE" and by_cell["B1"]["fix"].startswith(NEUTRAL_FIX)
    assert "break it" not in by_cell["B1"]["fix"] and "S!B1" in by_cell["B1"]["fix"]
    assert by_cell["C1"]["rule"] == "V-ERROR"


# ----------------------------------------------------------------------------- with Excel

def _probe_book(path: Path) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Probe"
    ws["A1"] = 1
    ws["C2"] = '=IF(TODAY()>1,"","later")'
    ws["E2"] = "=A1*2"                                   # the ordinary formula
    lp = wb.create_sheet("Loop")
    lp["A1"] = "=B1+1"
    lp["B1"] = "=A1+1"
    wb.save(path)


@pytest.mark.excel
def test_volatile_empty_text_is_not_v_no_value(tmp_path: Path) -> None:
    path = tmp_path / "probe.xlsx"
    _probe_book(path)
    # without a save: Excel 16 names Probe!C2 as the circular reference of its sheet (measured); it is not reported
    proc = run_cli("recalc", str(path), "--no-save", "--json", "-")
    assert proc.returncode == common.EXIT_FINDINGS, proc.stdout + proc.stderr
    report = json.loads(proc.stdout)
    named = {(f["sheet"], c) for f in report["findings"] if f["rule"] == "V-NO-VALUE" for c in f["cells"]}
    assert named and named <= {("Loop", "A1"), ("Loop", "B1")}
    assert not [f for f in report["findings"] if f.get("sheet") == "Probe"]
    # recalculated and saved by Excel through lib.excel (ROTM recalc runs lib.excel.recalc)
    proc = run_cli("recalc", str(path), "--json", "-")
    assert proc.returncode == common.EXIT_FINDINGS, proc.stdout + proc.stderr
    report = json.loads(proc.stdout)
    no_value = [f for f in report["findings"] if f["rule"] == "V-NO-VALUE"]
    named = {(f["sheet"], c) for f in no_value for c in f["cells"]}
    assert named == {("Loop", "A1"), ("Loop", "B1")}
    assert all(f["fix"].startswith(CYCLE_FIX) and f["fix"].endswith(" and break it") for f in no_value)
    assert not [f for f in report["findings"] if f.get("sheet") == "Probe"]
    assert {(c[0], c[1]) for c in report["data"]["error_cells"]} == {("Loop", "A1"), ("Loop", "B1")}
    # the file Excel saved
    with zipfile.ZipFile(path) as z:
        sheet1 = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert re.search(r'<c r="C2" t="str"><f ca="1">[^<]*</f><v\s*/>', sheet1), sheet1
    assert workbook.formula_cells_without_value(path) == [("Loop", "A1"), ("Loop", "B1")]
    proc = run_cli("audit", str(path), "--json", "-", "--no-record", timeout=300)
    assert proc.returncode in (0, 1), proc.stdout + proc.stderr
    report = json.loads(proc.stdout)
    audited = {(f["sheet"], c) for f in report["findings"] if f["rule"] == "V-NO-VALUE" for c in f["cells"]}
    assert audited == {("Loop", "A1"), ("Loop", "B1")}

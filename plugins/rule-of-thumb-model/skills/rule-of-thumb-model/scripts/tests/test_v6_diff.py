"""Tests of package P4 for the round 5 verdict entry [A-aera-5] (the stored type of raw cells and of typed cells).

- Part (2), changed in v6: `diff` lists a cell whose stored type changed (number, text, logical value) while its
  text reads the same as changed, with both types shown (for example `number 858800 -> text '858800'`): on raw
  sheets (the row comparison and the summary of the sheet) and on every other sheet (constants and the values
  Excel saved for formulas). Two equal workbooks still give no difference.
- Part (1), the half that v5 already holds: a cell of a boolean column stored as text is an error of `check`
  (D-TYPE, check.STORED_AS_TEXT_TYPES) and of `audit` (R-TYPE, audit.TEXT_REFUSED_TYPES) that names the first
  cell and the count. The other half (a text-column cell stored as a logical value) is P2's (audit) and P3's
  (check) supporting change, with their own Excel tests.
No Excel is needed: the workbooks are written with openpyxl, and check runs with ROTM_NO_EXCEL=1.
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from lib import diff

DATA = Path(__file__).resolve().parent / "data" / "lift"
if str(DATA) not in sys.path:
    sys.path.insert(0, str(DATA))
import fixture  # noqa: E402

SKILL = Path(__file__).resolve().parents[2]
EXAMPLE = SKILL / "examples" / "sales_vs_target"


def _edit(path: Path, fn) -> None:
    import openpyxl
    wb = openpyxl.load_workbook(path)
    fn(wb)
    wb.save(path)


@pytest.fixture
def pair(tmp_path: Path) -> tuple[Path, Path]:
    """The lift fixture model (raw_sales: invoice_id text, line_no integer, inv_date date, region text, amount
    number) and a copy of its folder."""
    p = fixture.write_model(tmp_path / "old")
    shutil.copytree(p["root"], tmp_path / "new")
    return p["workbook"], tmp_path / "new" / "model.xlsx"


def _raw_findings(report, sheet: str) -> list:
    return [f for f in report.findings if f.rule == "D-RAW" and f.sheet == sheet]


def test_the_reviewers_case_a_raw_number_stored_as_text_is_listed(tmp_path: Path) -> None:
    """raw_sales!F2 of a copy of the worked example: the number changed to text of the same digits. Both copies
    are saved through openpyxl, so that nothing else differs."""
    if not (EXAMPLE / "model.xlsx").is_file() or not (EXAMPLE / "model.manifest.json").is_file():
        pytest.skip("the worked example's workbook or manifest is absent")
    paths = []
    for name in ("old", "new"):
        folder = tmp_path / name
        folder.mkdir()
        for f in ("model.xlsx", "model.manifest.json"):
            shutil.copy2(EXAMPLE / f, folder / f)
        paths.append(folder / "model.xlsx")
    old, new = paths
    seen: dict = {}

    def to_text(wb) -> None:
        ws = wb["raw_sales"]
        assert ws["F1"].value == "amount"
        seen["value"] = ws["F2"].value
        assert isinstance(seen["value"], (int, float)) and not isinstance(seen["value"], bool)
        ws["F2"] = str(seen["value"])

    _edit(old, lambda wb: None)
    _edit(new, to_text)
    report = diff.diff_workbooks(old, new)
    raw = _raw_findings(report, "raw_sales")
    assert len(raw) == 1, [f.line() for f in report.findings]
    v = seen["value"]
    assert f"1 cells changed their stored type (first: F2: number {v} -> text '{v}')" in raw[0].message, raw[0].message
    assert "1 rows changed (first: row 2)" in raw[0].message
    assert "digest unchanged" in raw[0].message
    assert report.data["counts"]["raw_sheets_changed"] == 1 and "no differences" not in report.notes[-1]
    entry = report.data["sheets"]["raw_sales"]["raw"]
    assert entry["changed"] is True and entry["type_changes"] == 1
    assert entry["first_type_change"] == {"cell": "F2", "old": f"number {v}", "new": f"text '{v}'"}


def test_a_number_stored_as_text_is_listed_on_raw_and_other_sheets(pair: tuple[Path, Path], tmp_path: Path) -> None:
    old, new = pair
    _edit(old, lambda wb: wb["calc"].__setitem__("H40", 858800))

    def change(wb) -> None:
        wb["raw_sales"]["E2"] = str(wb["raw_sales"]["E2"].value)
        wb["inputs"]["D6"] = str(wb["inputs"]["D6"].value)
        wb["calc"]["H40"] = "858800"
    _edit(new, change)
    report = diff.diff_workbooks(old, new)
    raw = _raw_findings(report, "raw_sales")
    assert len(raw) == 1 and "cells changed their stored type (first: E2: number " in raw[0].message, raw
    inputs = {f.cell: f.message for f in report.findings if f.rule == "D-INPUT"}
    assert inputs["D6"].endswith(f"input scale changed: number {fixture.SCALE} -> text '{fixture.SCALE}'"), inputs
    assert inputs["H40"] == "H40: constant changed: number 858800 -> text '858800'", inputs
    group = next(g for g in report.data["sheets"]["calc"]["constants"] if g["ref"] == "H40")
    assert group["old_type"] == "number" and group["new_type"] == "text"
    md = diff.write_markdown(report, tmp_path / "d.md").read_text(encoding="utf-8")
    assert "| H40 | changed | number 858800 | text '858800' |  |" in md
    assert re.search(r"- Stored type changed: 1 cells \(first: E2: number [0-9.]+ -> text '[0-9.]+'\)", md), md


def test_text_true_replaced_by_the_logical_value_is_listed(pair: tuple[Path, Path]) -> None:
    old, new = pair

    def text_true(wb) -> None:
        wb["raw_sales"]["A2"] = "TRUE"
        wb["calc"]["H41"] = "TRUE"
    _edit(old, text_true)

    def logical_true(wb) -> None:
        wb["raw_sales"]["A2"] = True
        wb["calc"]["H41"] = True
    _edit(new, logical_true)
    report = diff.diff_workbooks(old, new)
    raw = _raw_findings(report, "raw_sales")
    assert len(raw) == 1, [f.line() for f in report.findings]
    assert "1 cells changed their stored type (first: A2: text 'TRUE' -> logical value TRUE)" in raw[0].message
    inputs = {f.cell: f.message for f in report.findings if f.rule == "D-INPUT"}
    assert inputs["H41"] == "H41: constant changed: text 'TRUE' -> logical value TRUE", inputs


def _cached(path: Path, coord: str, value: str, t: str | None) -> Path:
    """Give the formula cell `coord` of the first sheet a saved value (and its type attribute t)."""
    tmp = path.with_suffix(".tmp.xlsx")
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                text = data.decode("utf-8")
                attr = f' t="{t}"' if t else ""
                text, n = re.subn(rf'<c r="{coord}"([^>]*?)(?: t="n")?>(<f>.*?</f>)(?:<v\s*/>|<v></v>)',
                                  rf'<c r="{coord}"\g<1>{attr}>\g<2><v>{value}</v>', text)
                assert n == 1, text[:500]
                data = text.encode("utf-8")
            zout.writestr(item, data)
    os.replace(tmp, path)
    return path


def test_a_saved_value_whose_type_changed_is_listed_with_both_types(tmp_path: Path) -> None:
    from openpyxl import Workbook
    paths = []
    for name, value, t in (("old", "5", None), ("new", "5", "str")):
        wb = Workbook()
        wb.active.title = "Calc"
        wb.active["A1"] = 5
        wb.active["B1"] = "=A1*1"
        p = tmp_path / name / "w.xlsx"
        p.parent.mkdir(parents=True)
        wb.save(p)
        paths.append(_cached(p, "B1", value, t))
    report = diff.diff_workbooks(*paths)
    assert report.data["outputs_compared"] is True
    outputs = [f for f in report.findings if f.rule == "D-OUTPUT"]
    assert [f.message for f in outputs] == ["B1: value changed: number 5 -> text '5'"], [f.line() for f in outputs]


def test_equal_workbooks_still_give_no_difference(pair: tuple[Path, Path]) -> None:
    old, new = pair
    report = diff.diff_workbooks(old, new)
    assert report.findings == [] and "no differences" in report.notes[-1]

    def same(wb) -> None:
        wb["raw_sales"]["A2"] = "TRUE"
        wb["raw_sales"]["E3"] = "858800"
        wb["calc"]["H40"] = True
        wb["calc"]["H41"] = "858800"
    _edit(old, same)
    _edit(new, same)
    report = diff.diff_workbooks(old, new)
    assert report.findings == [], [f.line() for f in report.findings]
    assert report.data["sheets"]["raw_sales"]["raw"]["type_changes"] == 0


# ============================================================================= part (1): a boolean column as text

SCRIPTS = Path(__file__).resolve().parents[1]


def _rotm(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["ROTM_NO_EXCEL"] = "1"
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=600)


@pytest.fixture(scope="module")
def boolean_model(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The starter template with a boolean column `approved` added to the sample (column E of raw_lines), its
    contract proposed, shown and agreed, the sample landed, built without Excel."""
    base = tmp_path_factory.mktemp("v6_bool")
    root = base / "model"
    proc = _rotm("init", str(root), "--template", "starter")
    assert proc.returncode == 0, proc.stderr[-2000:]
    with open(root / "data" / "lines.csv", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    sample = base / "lines_bool.csv"
    with open(sample, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[*rows[0], "approved"], lineterminator="\n")
        w.writeheader()
        for i, r in enumerate(rows):
            w.writerow(dict(r, approved="TRUE" if i % 2 == 0 else "FALSE"))
    for args in (("contract", "propose", str(root), "--table", "lines", "--csv", str(sample), "--key", "line_id",
                  "--force"),
                 ("contract", "show", str(root), "--table", "lines"),
                 ("contract", "agree", str(root), "--table", "lines", "--reply", "agreed", "--by", "tester"),
                 ("land", str(root), "--table", "lines", "--csv", str(sample)),
                 ("build", str(root), "--strict")):
        proc = _rotm(*args)
        assert proc.returncode == 0, (args, proc.stdout[-2000:], proc.stderr[-2000:])
    con = json.loads((root / "contracts" / "lines.contract.json").read_text(encoding="utf-8"))
    assert [c["type"] for c in con["columns"] if c["name"] == "approved"] == ["boolean"]
    return root


def _report(proc: subprocess.CompletedProcess[str]) -> dict:
    return json.loads(proc.stdout)


def test_a_boolean_column_stored_as_text_is_an_error_of_check_and_audit(boolean_model: Path, tmp_path: Path) -> None:
    """Verified, not changed in v6: the logical value TRUE of raw_lines!E2 replaced by the text 'TRUE'."""
    root = tmp_path / "model"
    shutil.copytree(boolean_model, root)
    before = tmp_path / "before.xlsx"
    shutil.copy2(root / "model.xlsx", before)

    def to_text(wb) -> None:
        ws = wb["raw_lines"]
        assert ws["E1"].value == "approved" and ws["E2"].value is True
        ws["E2"] = "TRUE"
    _edit(root / "model.xlsx", to_text)
    audit = _rotm("audit", str(root / "model.xlsx"), "--strict", "--json", "-", "--quiet")
    assert audit.returncode == 1, audit.stderr[-2000:]
    found = [f for f in _report(audit)["findings"] if f["rule"] == "R-TYPE"]
    assert len(found) == 1 and found[0]["cell"] == "E2" and found[0]["severity"] == "error", found
    assert "1 cell(s) of boolean column approved in raw_lines are stored as text (first E2: 'TRUE')" \
        in found[0]["message"]
    check = _rotm("check", str(root), "--strict", "--json", "-", "--quiet")
    assert check.returncode != 0, check.stderr[-2000:]
    found = [f for f in _report(check)["findings"] if f["rule"] == "D-TYPE"]
    assert len(found) == 1 and found[0]["cell"] == "E2" and found[0]["severity"] == "error", found
    assert "1 cell(s) of boolean column approved in raw_lines are stored as text (first E2: 'TRUE')" \
        in found[0]["message"]
    # and diff lists the cell with both types (part 2)
    rep = diff.diff_workbooks(before, root / "model.xlsx")
    raw = _raw_findings(rep, "raw_lines")
    assert len(raw) == 1 and "(first: E2: logical value TRUE -> text 'TRUE')" in raw[0].message, raw

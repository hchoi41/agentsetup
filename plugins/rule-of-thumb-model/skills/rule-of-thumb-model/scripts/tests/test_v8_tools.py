"""Tests of package P1 in round 8 (v8): compat on defined names and inline rich text in the package scan, the
writers and the before-and-after comparison ([A-aera-7, A-aera-10]). Owner: P1 (v8).

- compat (A-aera-7): the function catalogue is applied to the formulas of defined names of workbook and worksheet
  scope; a name used inside another name is followed, and a cycle ends the walk without an error. A function one
  product lacks is X-FUNC with the name, its scope and one cell that uses it (or "no cell uses it"); the name is
  reported, not rewritten.
- inline rich text (A-aera-10): formatted runs inside a worksheet's inline strings make the package fragile
  (package_parts names the worksheet part), so split, compat and theme write through Excel or refuse without it
  with nothing written; snapshot() records the runs of every text cell that has them and compare() reports a cell
  whose runs changed outside the intended cells.
The fixture's inline string is written by openpyxl 3.1.5 (CellRichText is stored as <c t="inlineStr"><is><r>).
Tests marked excel drive Excel through lib.excel.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib import compat, workbook  # noqa: E402


@pytest.fixture
def no_excel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")


def _rotm(*args: str, excel: bool = True) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if not excel:
        env["ROTM_NO_EXCEL"] = "1"
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=900)


def _json(proc: subprocess.CompletedProcess[str]) -> dict:
    start = proc.stdout.find("{")
    assert start >= 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout[start:])


def _x_func(findings: list) -> list:
    out = []
    for f in findings:
        d = f if isinstance(f, dict) else f.to_dict()
        if d.get("rule") == "X-FUNC" and (d.get("data") or {}).get("kind") == "defined name":
            out.append(d)
    return out


# ============================================================================= A. compat: defined names

def _names_workbook(path: Path, names: dict[str, str], cells: dict[str, object] | None = None,
                    sheet_names: dict[str, str] | None = None) -> Path:
    """Sheet Forecast with A1:A3 = 1, 2, 3; workbook-scope `names`; Forecast-scope `sheet_names`; `cells`."""
    import openpyxl
    from openpyxl.workbook.defined_name import DefinedName
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Forecast"
    for r in range(1, 4):
        ws.cell(r, 1, r)
    wb.create_sheet("Notes")["A1"] = "notes"
    for n, text in names.items():
        wb.defined_names[n] = DefinedName(n, attr_text=text)
    for n, text in (sheet_names or {}).items():
        ws.defined_names[n] = DefinedName(n, attr_text=text)
    for ref, value in (cells or {}).items():
        sheet, coord = ref.split("!") if "!" in ref else ("Forecast", ref)
        wb[sheet][coord] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def test_compat_reports_a_function_in_a_defined_name_with_a_cell_that_uses_it(tmp_path: Path) -> None:
    """The reviewer's case (A-aera-7): ExpenseTotal = INFO("system"), Forecast!C1 = =ExpenseTotal. Both strict forms
    exit non-zero, the finding names the name, its scope and the cell, and no file is written."""
    wb = _names_workbook(tmp_path / "names.xlsx", {"ExpenseTotal": 'INFO("system")'}, {"C1": "=ExpenseTotal"})
    before = wb.read_bytes()
    for args in (["--report-only", "--strict"], ["--strict"]):
        proc = _rotm("compat", str(wb), *args, "--json", "-", excel=False)
        assert proc.returncode != 0, proc.stdout + proc.stderr
        found = _x_func(_json(proc)["findings"])
        assert len(found) == 1, found
        f = found[0]
        assert f["severity"] == "error" and (f["sheet"], f["cell"]) == ("Forecast", "C1")
        assert f["message"].startswith("defined name ExpenseTotal (workbook scope): INFO exists in Excel only")
        assert f["message"].endswith("; used by Forecast!C1")
        assert f["fix"].endswith(compat.NAME_NOT_REWRITTEN)
        assert f["data"]["function"] == "INFO" and f["data"]["used_by"] == "Forecast!C1"
    assert wb.read_bytes() == before
    assert not (tmp_path / "names_compat.xlsx").exists()


def test_compat_writes_a_replacement_and_leaves_the_name_as_it_is(tmp_path: Path, no_excel: None) -> None:
    """The rewrite path: a CONCATENATE cell is replaced; the defined name keeps its formula."""
    wb = _names_workbook(tmp_path / "names.xlsx", {"ExpenseTotal": 'INFO("system")'},
                         {"C1": "=ExpenseTotal", "D1": '=CONCATENATE("a","b")'})
    report = compat.compat_workbook(wb, strict=True)
    assert report.exit_code != 0
    assert len(_x_func(report.findings)) == 1
    out = tmp_path / "names_compat.xlsx"
    assert report.data.get("written") and out.is_file()
    import openpyxl
    book = openpyxl.load_workbook(out)
    assert book.defined_names["ExpenseTotal"].attr_text == 'INFO("system")'
    assert book["Forecast"]["D1"].value == '="a"&"b"'


def test_a_defined_name_with_a_shared_function_gets_no_finding(tmp_path: Path, no_excel: None) -> None:
    wb = _names_workbook(tmp_path / "sum.xlsx", {"Total": "SUM(Forecast!$A$1:$A$3)", "Rows": "Forecast!$A$1:$A$3"},
                         {"C1": "=Total", "C2": "=SUM(Rows)"})
    report = compat.compat_workbook(wb, report_only=True, strict=True)
    assert _x_func(report.findings) == []
    assert report.exit_code == 0, [f.to_dict() for f in report.findings]


def test_a_chain_of_names_is_followed_to_the_function(tmp_path: Path, no_excel: None) -> None:
    """Forecast!C1 = =Wrapper; Wrapper = ExpenseTotal*2; ExpenseTotal = INFO("system"); a Forecast-scope name that no
    cell uses."""
    wb = _names_workbook(tmp_path / "chain.xlsx", {"Wrapper": "ExpenseTotal*2", "ExpenseTotal": 'INFO("system")'},
                         {"C1": "=Wrapper"}, sheet_names={"LocalInfo": 'INFO("osversion")'})
    report = compat.compat_workbook(wb, report_only=True, strict=True)
    assert report.exit_code != 0
    by_name = {f["data"]["defined_name"]: f for f in _x_func(report.findings)}
    assert set(by_name) == {"Wrapper", "ExpenseTotal", "LocalInfo"}
    w = by_name["Wrapper"]
    assert w["message"].startswith("defined name Wrapper (workbook scope), through the name ExpenseTotal: INFO")
    assert w["data"]["through"] == ["ExpenseTotal"] and w["data"]["used_by"] == "Forecast!C1"
    assert by_name["ExpenseTotal"]["data"]["used_by"] == "Forecast!C1"          # used through Wrapper
    local = by_name["LocalInfo"]
    assert local["message"].startswith("defined name LocalInfo (Forecast scope): INFO")
    assert local["message"].endswith("; no cell uses it") and local["data"]["used_by"] is None


def test_two_names_that_use_each_other_end_without_an_error(tmp_path: Path, no_excel: None) -> None:
    wb = _names_workbook(tmp_path / "cycle.xlsx", {"LoopA": "LoopB+1", "LoopB": "LoopA+1"}, {"C1": "=LoopA"})
    report = compat.compat_workbook(wb, report_only=True, strict=True)
    assert _x_func(report.findings) == [] and report.exit_code == 0
    # a cycle that also holds a function one product lacks: each name is reported once
    wb = _names_workbook(tmp_path / "cycle2.xlsx", {"LoopA": 'LoopB+INFO("system")', "LoopB": "LoopA"},
                         {"C1": "=LoopB"})
    report = compat.compat_workbook(wb, report_only=True, strict=True)
    found = _x_func(report.findings)
    assert sorted(f["data"]["defined_name"] for f in found) == ["LoopA", "LoopB"]
    assert all(f["data"]["used_by"] == "Forecast!C1" for f in found)


# ============================================================================= B. inline rich text

RUN_TEXTS = ("Revenue ", "in thousands", " of EUR")


def _inline_workbook(path: Path, extra: dict[str, object] | None = None) -> Path:
    """Analysis!F2: an inline string of three runs, the second bold and red; Analysis!C3: a formula of depth 4 that
    split can split (A1:B5 hold numbers); `extra` cells of Analysis."""
    import openpyxl
    from openpyxl.cell.rich_text import CellRichText, TextBlock
    from openpyxl.cell.text import InlineFont
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Analysis"
    for r in range(1, 6):
        ws.cell(r, 1, r)
        ws.cell(r, 2, r * 2)
    ws["C3"] = "=ROUND(MAX(MIN(SUM(A1:A5),B1),0),2)"
    for coord, value in (extra or {}).items():
        ws[coord] = value
    ws["F2"] = CellRichText([RUN_TEXTS[0], TextBlock(InlineFont(b=True, color="FFFF0000"), RUN_TEXTS[1]),
                             RUN_TEXTS[2]])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    with zipfile.ZipFile(path) as z:
        xml = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert 't="inlineStr"><is><r>' in xml          # openpyxl stores the rich text as an inline string
    return path


def _assert_three_runs(runs: tuple) -> None:
    assert runs is not None and [r[0] for r in runs] == list(RUN_TEXTS), runs
    assert runs[1][1] is True and runs[1][3] == "FF0000", runs           # bold and red
    assert runs[0][1] is False and runs[2][1] is False, runs


def test_package_parts_names_the_worksheet_part_with_inline_rich_text(tmp_path: Path) -> None:
    wb = _inline_workbook(tmp_path / "inline.xlsx")
    pkg = workbook.package_parts(wb)
    assert pkg.rich_text and pkg.rich_text_parts == ["xl/worksheets/sheet1.xml"]
    assert any(f.startswith(workbook.RICH_TEXT_DESCRIPTION) and "xl/worksheets/sheet1.xml" in f for f in pkg.fragile)
    _assert_three_runs(workbook.text_runs(wb)[("Analysis", "F2")])
    # an inline string without runs is plain text: not fragile
    plain = tmp_path / "plain_inline.xlsx"
    with zipfile.ZipFile(wb) as zin, zipfile.ZipFile(plain, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                text = data.decode("utf-8")
                start, end = text.index("<is>"), text.index("</is>") + len("</is>")
                data = (text[:start] + "<is><t>" + "".join(RUN_TEXTS) + "</t></is>" + text[end:]).encode("utf-8")
            zout.writestr(item, data)
    pkg = workbook.package_parts(plain)
    assert not pkg.rich_text and pkg.rich_text_parts == [] and pkg.fragile == []
    assert workbook.text_runs(plain) == {}


def test_split_without_excel_refuses_inline_rich_text_and_writes_nothing(tmp_path: Path) -> None:
    wb = _inline_workbook(tmp_path / "inline.xlsx")
    before = wb.read_bytes()
    proc = _rotm("split", str(wb), "Analysis!C3", excel=False)
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "rich text inside cells" in proc.stdout + proc.stderr and "nothing was written" in proc.stdout + proc.stderr
    assert wb.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["inline.xlsx"]


def test_compat_and_theme_without_excel_refuse_inline_rich_text(tmp_path: Path, no_excel: None) -> None:
    """compat (through compat.write_edits, as split) and theme take the same path as for rich text in shared
    strings: without Excel, exit 3 and nothing written; theme reports T-RICHTEXT."""
    from lib import common, theme
    wb = _inline_workbook(tmp_path / "inline.xlsx", {"D4": '=CONCATENATE("a","b")'})
    before = wb.read_bytes()
    with pytest.raises(common.EnvUnavailable) as exc:
        compat.compat_workbook(wb)
    assert "rich text inside cells" in str(exc.value) and "nothing was written" in str(exc.value)
    rep = theme.theme_workbook(wb, in_place=True)
    assert rep.exit_code == 3 and not rep.data["written"]
    assert {f.rule for f in rep.findings} >= {"T-FRAGILE", "T-RICHTEXT"}
    assert wb.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["inline.xlsx"]


def test_compare_reports_a_cell_whose_runs_were_lost(tmp_path: Path) -> None:
    """The v7 path: openpyxl opens and saves the workbook, and F2 becomes plain text."""
    import openpyxl
    wb = _inline_workbook(tmp_path / "inline.xlsx")
    flat = tmp_path / "flat.xlsx"
    openpyxl.load_workbook(wb).save(flat)
    assert not workbook.package_parts(flat).rich_text
    before, after = workbook.snapshot(wb), workbook.snapshot(flat)
    _assert_three_runs(before.runs[("Analysis", "F2")])
    assert after.runs == {}
    diffs = workbook.compare(before, after, workbook.Intended(kind="cells", cells={("Analysis", "C3")}))
    runs = [d for d in diffs if d.kind == "runs"]
    assert [(d.sheet, d.cell) for d in runs] == [("Analysis", "F2")], [d.message for d in diffs]
    assert "formatted text runs changed (3 runs:" in runs[0].message and "-> no formatted runs)" in runs[0].message
    assert workbook.compare(before, after, workbook.Intended(kind="none"))[0].kind == "runs"
    # intended: the cell itself, or a change of styles only (theme gives a rich-text cell one font, T-RICHTEXT)
    assert not [d for d in workbook.compare(before, after, workbook.Intended(kind="cells", cells={("Analysis", "F2")}))
                if d.kind == "runs"]
    assert not [d for d in workbook.compare(before, after, workbook.Intended(kind="styles_only")) if d.kind == "runs"]


@pytest.mark.excel
def test_split_keeps_inline_rich_text_or_is_refused(tmp_path: Path) -> None:
    """With Excel: split of Analysis!C3 exits 0 and Analysis!F2 keeps its three runs, the second bold and red (read
    back from the saved file); split never reports no unintended change with the runs gone."""
    wb = _inline_workbook(tmp_path / "inline.xlsx")
    proc = _rotm("split", str(wb), "Analysis!C3", "--json", "-")
    rep = _json(proc)
    out = tmp_path / "inline_split.xlsx"
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert rep["data"].get("written") and out.is_file()
    assert rep["verified"].get("recalculated") is True
    _assert_three_runs(workbook.text_runs(out).get(("Analysis", "F2")))
    assert not [f for f in rep["findings"] if f["rule"] == "W-UNINTENDED"]

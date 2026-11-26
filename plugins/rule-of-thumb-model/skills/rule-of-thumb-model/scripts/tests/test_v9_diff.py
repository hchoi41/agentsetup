"""Tests of package P1 in round 9 (v9) for the round 8 verdict entry [A-aera-5, A-aera-13]. Owner: P1 (v9).

- Part (1), [A-aera-13]: the file prefixes _xlfn., _xlws. and _xlpm. are removed only outside text literals
  (workbook.normalise_formula, diff._norm_formula, formula.strip_xlfn). A criterion changed from the text
  "_xlfn.Fee" to the text "Fee" is a changed formula, also when the outputs are equal; the prefixed and the
  unprefixed form of one function name or LET name still compare equal.
- Part (2), [A-aera-5]: diff reports verified.saved_values_compared (both files hold values saved by Excel) and
  verified.recalculated (true only when reports/status.json of each file's model folder records a passing check
  that recalculated that file's current version, or a passing recalc of it). A workbook set to manual
  calculation gets a note that names the file; while its version has no such record, the note says that its
  saved values may be out of date and gives rotm.py recalc for both files.
Only test_after_recalc_on_both_the_dependent_cell_is_listed_as_changed needs Excel.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib import common, diff, formula, modelfolder, workbook  # noqa: E402

ROTM = SCRIPTS / "rotm.py"
SHEET_XML = "xl/worksheets/sheet1.xml"


def _rotm(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(ROTM), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=600, env=dict(os.environ))


def _with_saved_values(path: Path, saved: dict[str, object]) -> Path:
    """Write values saved for formula cells into the first sheet's XML (openpyxl writes none), so that a test holds
    a workbook with saved values, also stale ones, without Excel."""
    tmp = path.with_name(path.stem + "_v.xlsx")
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == SHEET_XML:
                text = data.decode("utf-8")
                for coord, value in saved.items():
                    text, n = re.subn(rf'(<c r="{coord}"[^>]*>)(<f>.*?</f>)(?:<v\s*/>|<v></v>)?(</c>)',
                                      rf"\g<1>\g<2><v>{value}</v>\g<3>", text)
                    assert n == 1, (coord, text[:400])
                data = text.encode("utf-8")
            zout.writestr(item, data)
    os.replace(tmp, path)
    return path


def _book(path: Path, cells: dict[str, object], *, sheet: str = "Model", manual: bool = False,
          saved: dict[str, object] | None = None) -> Path:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    for coord, value in cells.items():
        ws[coord] = value
    if manual:
        wb.calculation.calcMode = "manual"
        wb.calculation.fullCalcOnLoad = False
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    if saved:
        _with_saved_values(path, saved)
    return path


def _formula_findings(report) -> list:
    return [f for f in report.findings if f.rule == "D-FORMULA"]


# ============================================================================ part (1), [A-aera-13]

def test_normalise_formula_keeps_prefixes_inside_text() -> None:
    n = workbook.normalise_formula
    # inside a text literal the prefix is part of the text
    assert n('=SUMIF(A:A,"_xlfn.Fee",B:B)') == '=SUMIF(A:A,"_xlfn.Fee",B:B)'
    assert n('=SUMIF(A:A,"_xlfn.Fee",B:B)') != n('=SUMIF(A:A,"Fee",B:B)')
    assert n('=COUNTIF(a:a,"_xlws.x")') == '=COUNTIF(A:A,"_xlws.x")'
    assert n('=COUNTIF(a:a,"_xlpm.x")') == '=COUNTIF(A:A,"_xlpm.x")'
    # outside text literals the prefixes go and the text is upper case, as in v8
    assert n('=_xlfn.xlookup(a1,"_xlfn.North",b:b)') == '=XLOOKUP(A1,"_xlfn.North",B:B)'
    assert n('=_xlfn.xlookup(a1,"North",b:b)') == '=XLOOKUP(A1,"North",B:B)'
    assert n('=_xlfn._xlws.FILTER(a1:a9,b1:b9>0)') == '=FILTER(A1:A9,B1:B9>0)'
    assert n('=_xlws.SORT(a1:a9)') == '=SORT(A1:A9)'
    assert n('=_xlfn.LET(_xlpm.x,a1,_xlpm.x*2)') == '=LET(X,A1,X*2)'
    # a doubled quote stays inside its literal: the text after it is still text
    assert n('=_xlfn.CONCAT("say ""_xlfn.x"" now",_xlfn.TEXTJOIN(",",TRUE,a1:a2))') == \
        '=CONCAT("say ""_xlfn.x"" now",TEXTJOIN(",",TRUE,A1:A2))'
    assert n('="a""_xlfn."&_xlfn.CONCAT(b1)') == '="a""_xlfn."&CONCAT(B1)'
    assert n('=SUM(a1:a3)') == '=SUM(A1:A3)'
    assert n(None) is None
    # formula.strip_xlfn keeps text literals too (it removes the prefixes per piece outside the quotes)
    assert formula.strip_xlfn('=SUMIF(A:A,"_xlfn.Fee",B:B)') == '=SUMIF(A:A,"_xlfn.Fee",B:B)'
    assert formula.strip_xlfn('=_xlfn.CONCAT("a""_xlfn.b",_xlpm.x)') == '=CONCAT("a""_xlfn.b",x)'
    # diff compares the two criteria as different formulas
    assert diff._norm_formula('=SUMIF(A:A,"_xlfn.Fee",B:B)') != diff._norm_formula('=SUMIF(A:A,"Fee",B:B)')
    assert diff._norm_formula('=_xlfn.CONCAT(A1,B1)') == diff._norm_formula('=CONCAT(a1,b1)')


def test_criterion_text_that_held_a_prefix_is_a_changed_formula(tmp_path: Path) -> None:
    """The reviewer's case: =SUMIF(A:A,"_xlfn.Fee",B:B) changed to =SUMIF(A:A,"Fee",B:B) over data with
    Marketing only. Both give 0, so the outputs are equal; the formula is listed as changed and
    --fail-on-change exits 1."""
    data = {"A1": "Marketing", "B1": 10, "A2": "Marketing", "B2": 20, "A3": "Marketing", "B3": 30}
    old = _book(tmp_path / "old" / "fees.xlsx", {**data, "D1": '=SUMIF(A:A,"_xlfn.Fee",B:B)'}, saved={"D1": 0})
    new = _book(tmp_path / "new" / "fees.xlsx", {**data, "D1": '=SUMIF(A:A,"Fee",B:B)'}, saved={"D1": 0})
    rep = diff.diff_workbooks(old, new)
    assert rep.data["outputs_compared"] is True
    found = _formula_findings(rep)
    assert len(found) == 1, [f.line() for f in rep.findings]
    assert found[0].sheet == "Model" and found[0].cell == "D1"
    assert '"_xlfn.Fee"' in found[0].message and '"Fee"' in found[0].message
    assert rep.data["counts"]["formulas"] == 1 and rep.data["counts"]["outputs"] == 0
    res = _rotm("diff", str(old), str(new), "--fail-on-change")
    assert res.returncode == common.EXIT_FINDINGS, res.stdout + res.stderr
    assert "D-FORMULA" in res.stdout and "no differences" not in res.stdout
    assert rep.verified["saved_values_compared"] is True


def test_prefixed_and_unprefixed_function_names_still_compare_equal(tmp_path: Path) -> None:
    cells_old = {"A1": "x", "B1": "y", "C1": "=_xlfn.CONCAT(A1,B1)", "C2": "=_xlfn.LET(_xlpm.n,A1,_xlpm.n&B1)",
                 "C3": "=_xlfn._xlws.SORT(A1:B1)", "C4": '=_xlfn.CONCAT("a""_xlfn.b",A1)'}
    cells_new = {"A1": "x", "B1": "y", "C1": "=CONCAT(A1,B1)", "C2": "=LET(n,A1,n&B1)", "C3": "=SORT(A1:B1)",
                 "C4": '=CONCAT("a""_xlfn.b",A1)'}
    old = _book(tmp_path / "old" / "names.xlsx", cells_old)
    new = _book(tmp_path / "new" / "names.xlsx", cells_new)
    rep = diff.diff_workbooks(old, new)
    assert _formula_findings(rep) == [], [f.line() for f in rep.findings]
    assert rep.data["counts"]["formulas"] == 0
    res = _rotm("diff", str(old), str(new), "--fail-on-change")
    assert res.returncode == 0, res.stdout + res.stderr


# ============================================================================ part (2), [A-aera-5]

def _manual_pair(base: Path) -> tuple[Path, Path]:
    """Two model folders made with rotm.py init, each with Amounts!A1 and Amounts!C1 = A1*2, set to manual
    calculation and saved without a calculation: old A1 2 with C1 saved as 4; new A1 20 with C1 still saved as 4
    (a stale dependent cell)."""
    books = []
    for name, a1 in (("old_model", 2), ("new_model", 20)):
        root = base / name
        res = _rotm("init", str(root))
        assert res.returncode == 0, res.stdout + res.stderr
        assert modelfolder.is_model_root(root)
        books.append(_book(root / "model.xlsx", {"A1": a1, "C1": "=A1*2"}, sheet="Amounts", manual=True,
                           saved={"C1": 4}))
    return books[0], books[1]


def test_manual_calculation_with_a_stale_value_is_not_reported_as_recalculated(outside_tmp: Path) -> None:
    old, new = _manual_pair(outside_tmp / "stale")
    rep = diff.diff_workbooks(old.parent, new.parent)          # the model folders: diff reads their workbooks
    assert rep.verified["saved_values_compared"] is True
    assert rep.verified["recalculated"] is False
    assert rep.data["calc_mode"] == {"old": "manual", "new": "manual"}
    assert rep.data["recalculated_on_record"] == {"old": False, "new": False}
    # the stale saved value: A1 is listed as changed, C1 is not (4 and 4)
    assert [f.cell for f in rep.findings if f.rule == "D-INPUT"] == ["A1"]
    assert not [f for f in rep.findings if f.rule == "D-OUTPUT"], [f.line() for f in rep.findings]
    po, pn = common.norm_path(old), common.norm_path(new)
    recalc_both = f'run rotm.py recalc "{po}" and rotm.py recalc "{pn}" before the diff'
    for p in (po, pn):
        manual = [n for n in rep.notes if n.startswith(f"{p}: the workbook is set to manual calculation")]
        assert len(manual) == 1, rep.notes
        assert "so its saved values may be out of date" in manual[0] and manual[0].endswith(recalc_both)
    stale = [n for n in rep.notes if n.startswith("outputs compared on the values saved in the files; ")]
    assert len(stale) == 1 and po in stale[0] and pn in stale[0], rep.notes
    assert stale[0].endswith("have no passing check or recalc of this version in reports/status.json")
    # the Markdown report and the JSON say the same
    md = diff.write_markdown(rep, outside_tmp / "stale" / "d.md").read_text(encoding="utf-8")
    assert "- Saved values compared: yes" in md
    assert "- Recalculated: no; the outputs are the values saved in the files" in md
    assert "- Calculation mode: old manual, new manual" in md
    assert f"{po}: the workbook is set to manual calculation" in md
    res = _rotm("diff", str(old), str(new), "--json", "-")
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["verified"]["recalculated"] is False and out["verified"]["saved_values_compared"] is True
    assert out["data"]["calc_mode"] == {"old": "manual", "new": "manual"}
    assert any(n.startswith(f"{po}: the workbook is set to manual calculation") for n in out["notes"])


def test_recalculated_needs_a_status_record_of_each_files_version(outside_tmp: Path) -> None:
    """The rule of verified.recalculated, without Excel: a record that does not pass, or that names another
    version, does not count; a passing recalc or a passing check that recalculated counts for its version only."""
    old, new = _manual_pair(outside_tmp / "records")
    sha_old, sha_new = common.file_sha256(old), common.file_sha256(new)
    other = "0" * 64

    def recalculated() -> bool:
        return diff.diff_workbooks(old, new).verified["recalculated"]

    modelfolder.record_step(old.parent, "model.xlsx", "recalc", {"exit_code": 0, "output_sha256": sha_old})
    assert recalculated() is False                               # the new file has no record yet
    modelfolder.record_step(new.parent, "model.xlsx", "recalc", {"exit_code": 0, "output_sha256": other})
    assert recalculated() is False                               # a record of another version
    modelfolder.record_step(new.parent, "model.xlsx", "recalc", {"exit_code": 1, "output_sha256": sha_new})
    assert recalculated() is False                               # a recalc that did not pass
    modelfolder.record_step(new.parent, "model.xlsx", "check", {"exit_code": 0, "output_sha256": sha_new,
                                                                "recalculated": False})
    assert recalculated() is False                               # a check that did not recalculate
    modelfolder.record_step(new.parent, "model.xlsx", "check", {"exit_code": 0, "output_sha256": sha_new,
                                                                "recalculated": True})
    rep = diff.diff_workbooks(old, new)
    assert rep.verified["recalculated"] is True and rep.data["recalculated_on_record"] == {"old": True, "new": True}
    assert not [n for n in rep.notes if "may be out of date" in n or n.startswith("outputs compared on the values")]
    # a manual workbook whose version was recalculated on record still gets a note that names it
    for p in (old, new):
        assert any(n.startswith(f"{common.norm_path(p)}: the workbook is set to manual calculation; its saved "
                                "values are those of the recalculation") for n in rep.notes), rep.notes
    # a file outside a model folder has no status record
    loose = _book(outside_tmp / "records_loose" / "copy.xlsx", {"A1": 20, "C1": "=A1*2"}, sheet="Amounts",
                  saved={"C1": 40})
    rep = diff.diff_workbooks(old, loose)
    assert rep.verified["recalculated"] is False and rep.data["recalculated_on_record"]["new"] is False
    assert rep.data["calc_mode"]["new"] == "auto"
    assert not [n for n in rep.notes if f"{common.norm_path(loose)}: the workbook is set to manual" in n]


def test_diff_skill_states_the_two_fields_and_the_manual_calculation_note(plugin_dir: Path | None) -> None:
    if plugin_dir is None:
        pytest.skip("the skill folder lies outside its plugin: skills/diff/SKILL.md is not here")
    text = " ".join((plugin_dir / "skills" / "diff" / "SKILL.md").read_text(encoding="utf-8").split())
    for part in ("`verified.saved_values_compared`: true when both files hold values saved by Excel.",
                 "`verified.recalculated`: true only when `reports/status.json` of each file's model folder records "
                 "a passing check that recalculated that file's current version, or a passing recalc of it.",
                 "A workbook set to manual calculation gets a note that names it.",
                 "the note says that its saved values may be out of date and gives `rotm.py recalc` for both files",
                 "a text in quotes is compared as it is"):
        assert part in text, part


@pytest.mark.excel
def test_after_recalc_on_both_the_dependent_cell_is_listed_as_changed(outside_tmp: Path) -> None:
    old, new = _manual_pair(outside_tmp / "recalc")
    for p in (old, new):
        res = _rotm("recalc", str(p))
        assert res.returncode == 0, res.stdout + res.stderr
    rep = diff.diff_workbooks(old, new)
    outputs = [f for f in rep.findings if f.rule == "D-OUTPUT"]
    assert len(outputs) == 1 and outputs[0].sheet == "Amounts" and outputs[0].cell == "C1", \
        [f.line() for f in rep.findings]
    assert "4 -> 40" in outputs[0].message
    assert rep.verified["recalculated"] is True and rep.verified["saved_values_compared"] is True
    assert rep.data["recalculated_on_record"] == {"old": True, "new": True}
    assert not [n for n in rep.notes if "may be out of date" in n or n.startswith("outputs compared on the values")]
    # Excel keeps the workbook's calculation mode when it saves (measured in v9: manual stays manual); the note
    # then names the file and says that its saved values are those of the recorded recalculation
    for p, label in ((old, "old"), (new, "new")):
        if rep.data["calc_mode"][label] == "manual":
            assert any(n.startswith(f"{common.norm_path(p)}: the workbook is set to manual calculation; its saved "
                                    "values are those of the recalculation") for n in rep.notes), rep.notes

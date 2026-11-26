"""Tests of package P7 in round 7 (v7): split, compat and lift ([A-aera-5, A-aera-7, A-aera-12]). Owner: P7 (v7).

- split (A-aera-5): ROW() and COLUMN() without an argument in a part that moves to a helper are written with the
  original cell as their argument (COLUMN(Scenarios!D2)), so the helper gives the host's position after an input
  changes. A call that cannot be written so is SPLIT-GUARD, which names the call; nothing is written.
- compat (A-aera-7): _prefix_missing tests each function call by the name it is stored with and does not read text
  in quotes, so a prefixed name in a text literal, or another call that has the prefix, hides no unprefixed call.
- lift (A-aera-12): the script's PROPER treats a word as a run of Unicode letters, as Excel 16 does; a text with a
  character whose case Excel gives otherwise, or that was not measured, is not reproduced (L-NOT-REPRODUCED names
  the step and the character).
Tests marked excel drive Excel through lib.excel.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
SKILL = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib import build, common, compat, land, lift, modelfolder, split, workbook  # noqa: E402
from test_build import agree  # noqa: E402

STARTER = SKILL / "templates" / "starter"


@pytest.fixture
def no_excel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")


def _rotm(*args: str, excel: bool = True) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if not excel:
        env["ROTM_NO_EXCEL"] = "1"
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=900)


def _job(path: Path, operations: list[dict]) -> dict[str, object]:
    """One Excel job on `path` (the operations, then a full recalculation); the values read; nothing saved."""
    from lib import excel
    reads = [op for op in operations if op["op"] == "read"]
    ops = [op for op in operations if op["op"] != "read"] + [{"op": "recalc"}] + reads
    res = excel.run_job(path, ops)
    assert res.ok, res.message()
    return {str(i.get("ref")): excel._py_value(i.get("value")) for i in res.step("read").get("values") or []}


# ============================================================================= A. split: ROW() and COLUMN()

# the accuracy reviewer's formula (round 6): depth 3; the part at level 3 is COLUMN()
POSITION = "=A1*ABS(ABS(COLUMN()))"


def _position_workbook(path: Path, sheet: str = "Scenarios", a1: object = 0, cells: dict | None = None) -> Path:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    ws["A1"] = a1
    for ref, text in (cells or {"D2": POSITION}).items():
        ws[ref] = text
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def test_plan_writes_the_host_cell_into_row_and_column_without_an_argument() -> None:
    plan = split.plan_split(POSITION, limit=2, host="D2")
    assert plan.rewritten == "=A1*ABS(ABS(_rotm_h1))"
    assert plan.helpers == [("helper 1: COLUMN()", "=COLUMN(D2)")] and plan.parts == ["COLUMN()"]
    # ROW( ) with a space, COLUMN(), and a ROW with its own argument in one moved part; a text in quotes is kept
    plan = split.plan_split('=A1*ABS(ABS(ROW( )+COLUMN()+ROW(B7)+LEN("ROW()")))', limit=2, host="D2")
    assert [h for _l, h in plan.helpers] == ["=ROW(D2)", "=COLUMN(D2)", "=ROW(B7)", '=LEN("ROW()")']
    # the host keeps its own ROW(): only the moved part is bound
    plan = split.plan_split("=ROW()*ABS(ABS(ROW()))", limit=2, host="C9")
    assert plan.rewritten == "=ROW()*ABS(ABS(_rotm_h1))" and plan.helpers[0][1] == "=ROW(C9)"
    # a moved part deeper than the limit is split again; every helper that holds the call is bound
    plan = split.plan_split("=ABS(ABS(ROUND(COLUMN()/2,0)))", limit=2, host="E5")
    assert [h for _l, h in plan.helpers] == ["=ROUND(COLUMN(E5)/2,0)"]
    # a part without such a call is split as before
    assert [h for _l, h in split.plan_split("=A1*ABS(ABS(SUM(B1:B3)))", limit=2, host="D2").helpers] == \
        ["=SUM(B1:B3)"]
    # a length split binds every moved term
    long = "=" + "+".join(["A1"] * 30) + "+ROW()"
    plan = split.plan_length_split(long, length_limit=40, host="D2")
    assert plan.helpers[-1][1].endswith("+ROW(D2)") and "ROW()" not in "".join(h for _l, h in plan.helpers)
    # the sheet name is written with the reference when the helper goes to split_helpers (quoted when needed)
    assert split.qualify("=COLUMN(D2)", "Scenarios") == "=COLUMN(Scenarios!D2)"
    assert split.qualify("=COLUMN(D2)", "My Scenarios") == "=COLUMN('My Scenarios'!D2)"


def test_a_position_call_that_cannot_be_written_is_split_guard() -> None:
    with pytest.raises(split.SplitGuard) as exc:
        split.bind_position("=ABS(ROW(", "D2")
    err = exc.value
    assert isinstance(err, split.SplitPosition) and err.guard == "ROW" and err.data() == {"call": "ROW("}
    assert err.message("Scenarios!D2") == ("Scenarios!D2: the call ROW( gives the position of the cell that holds "
                                           "it, and split could not write the cell D2 as its argument in the helper; "
                                           "in a helper it would give the helper's position; nothing was written")
    assert err.fix == "write the cell as the argument of the call by hand (ROW(D2)), then split again"
    assert split.GUARD_RULE == "SPLIT-GUARD"


@pytest.mark.parametrize("sheet, helper", [("Scenarios", "=COLUMN(Scenarios!D2)"),
                                           ("My Scenarios", "=COLUMN('My Scenarios'!D2)")], ids=["plain", "quoted"])
def test_split_without_excel_writes_the_host_cell_as_the_argument(tmp_path: Path, no_excel: None, sheet: str,
                                                                   helper: str) -> None:
    src = _position_workbook(tmp_path / "position.xlsx", sheet=sheet)
    rep = split.split_cell(src, f"'{sheet}'!D2" if " " in sheet else f"{sheet}!D2")
    assert rep.data["written"] is True and rep.exit_code == common.EXIT_NOT_AVAILABLE, [f.line() for f in rep.findings]
    info = workbook.open_workbook(Path(rep.data["output"]), with_values=False)
    assert info.sheet(sheet).cell("D2").formula == "=A1*ABS(ABS(split_helpers!B2))"
    assert info.sheet(split.HELPER_SHEET).cell("B2").formula == helper


@pytest.mark.excel
def test_excel_split_of_column_keeps_the_value_after_an_input_changes(tmp_path: Path) -> None:
    """The reviewer's case: split while A1 = 0; then A1 = 0, 1 and 3 in both files: the host equals the original
    (4, 12 and 0 with COLUMN() of D2 = 4)."""
    src = _position_workbook(tmp_path / "position.xlsx", a1=0)
    rep = split.split_cell(src, "Scenarios!D2", limit=2)
    assert rep.exit_code == 0 and rep.verified["recalculated"] is True, [f.line() for f in rep.findings]
    out = Path(rep.data["output"])
    info = workbook.open_workbook(out, with_values=False)
    assert info.sheet(split.HELPER_SHEET).cell("B2").formula == "=COLUMN(Scenarios!D2)"
    for a1, expected in ((0, 0), (1, 4), (3, 12)):
        ops = [{"op": "value", "sheet": "Scenarios", "cell": "A1", "value": a1},
               {"op": "read", "refs": ["Scenarios!D2"]}]
        original, after = _job(src, ops), _job(out, ops)
        assert original["Scenarios!D2"] == expected, (a1, original)
        assert after["Scenarios!D2"] == original["Scenarios!D2"], (a1, after)


@pytest.mark.excel
def test_excel_split_of_a_block_with_row_and_column_keeps_every_value(tmp_path: Path) -> None:
    """A copied-down block D2:D4 with ROW() and COLUMN() in the moved part: split together with A1 = 0; after A1 = 2
    every host equals the original, and the helpers keep one relative form."""
    cells = {f"D{r}": "=$A$1*ABS(ABS(ROW()*10+COLUMN()))" for r in (2, 3, 4)}
    src = _position_workbook(tmp_path / "block.xlsx", a1=0, cells=cells)
    rep = split.split_cell(src, "Scenarios!D2", limit=2)
    assert rep.exit_code == 0 and rep.data["cells"] == 3, [f.line() for f in rep.findings]
    out = Path(rep.data["output"])
    helpers = workbook.open_workbook(out, with_values=False).sheet(split.HELPER_SHEET)
    assert [helpers.cell(f"B{r}").formula for r in (2, 3, 4)] == [f"=ROW(Scenarios!D{r})" for r in (2, 3, 4)]
    assert [helpers.cell(f"C{r}").formula for r in (2, 3, 4)] == [f"=COLUMN(Scenarios!D{r})" for r in (2, 3, 4)]
    refs = [f"Scenarios!D{r}" for r in (2, 3, 4)]
    ops = [{"op": "value", "sheet": "Scenarios", "cell": "A1", "value": 2}, {"op": "read", "refs": refs}]
    original, after = _job(src, ops), _job(out, ops)
    assert [original[r] for r in refs] == [48, 68, 88] and after == original, (original, after)


# ============================================================================= B. compat: each call's own prefix

# Rates!E1: an unprefixed call and a text literal that holds the prefixed name; Rates!E2: one prefixed and one
# unprefixed call (the accuracy reviewer's two cells, round 6). Each lookup returns 73 when Excel can read it.
E1 = '=IFERROR(XLOOKUP("b",A2:A4,B2:B4),"_xlfn.XLOOKUP unavailable")'
E2 = '=_xlfn.XLOOKUP("b",A2:A4,B2:B4)&"|"&XLOOKUP("b",A2:A4,B2:B4)'


def test_prefix_missing_reads_each_call_by_its_stored_name() -> None:
    assert compat._prefix_missing(E1) is True
    assert compat._prefix_missing(E2) is True
    assert compat._prefix_missing('=IFERROR(_xlfn.XLOOKUP("b",A2:A4,B2:B4),"_xlfn.XLOOKUP unavailable")') is False
    assert compat._prefix_missing('=_xlfn.XLOOKUP("b",A2:A4,B2:B4)&"|"&_xlfn.XLOOKUP("b",A2:A4,B2:B4)') is False
    assert compat._prefix_missing('=IF(A1,"XLOOKUP(1,B:B,C:C)",0)') is False        # a call only inside quotes
    assert compat._prefix_missing("=_xlws.FILTER(A1:A3,B1:B3)") is True               # FILTER needs _xlfn._xlws.
    assert compat._prefix_missing("=SUM(Sheet1!A1:XLOOKUP(1,B:B,C:C))") is True       # a range prefix before it
    assert compat._prefix_missing(E2, keep={"SUM"}) is False                          # a function a replacement removed
    new, done, problems = compat.plan_formula(E1, sheet="Rates", row=1, col=5, tables=[], product="both")
    assert new == '=IFERROR(_xlfn.XLOOKUP("b",A2:A4,B2:B4),"_xlfn.XLOOKUP unavailable")' and done == ["X-PREFIX"]
    new, done, problems = compat.plan_formula(E2, sheet="Rates", row=2, col=5, tables=[], product="both")
    assert new == '=_xlfn.XLOOKUP("b",A2:A4,B2:B4)&"|"&_xlfn.XLOOKUP("b",A2:A4,B2:B4)' and done == ["X-PREFIX"]


def _rates(path: Path) -> Path:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Rates"
    for r, (key, value) in enumerate((("a", 11), ("b", 73), ("c", 5)), start=2):
        ws[f"A{r}"] = key
        ws[f"B{r}"] = value
    ws["E1"] = E1
    ws["E2"] = E2
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


@pytest.mark.excel
def test_excel_compat_reports_and_repairs_each_unprefixed_call(tmp_path: Path) -> None:
    src = _rates(tmp_path / "rates.xlsx")
    before = _job(src, [{"op": "read", "refs": ["Rates!E1", "Rates!E2"]}])
    assert before["Rates!E1"] == "_xlfn.XLOOKUP unavailable" and str(before["Rates!E2"]) == "#NAME?", before
    sha = common.file_sha256(src)
    proc = _rotm("compat", str(src), "--report-only", "--strict", "--json", "-")
    assert proc.returncode == 1, proc.stdout[-2000:] + proc.stderr[-2000:]
    rep = json.loads(proc.stdout)
    # one finding per rule and sheet, with the cells it covers
    prefix = [f for f in rep["findings"] if f["rule"] == "X-PREFIX" and f["severity"] == "error"]
    assert len(prefix) == 1 and sorted(prefix[0]["cells"]) == ["E1", "E2"], rep["findings"]
    assert sorted(r["cell"] for r in rep["data"]["replaced"]) == ["E1", "E2"]
    assert common.file_sha256(src) == sha and not (tmp_path / "rates_compat.xlsx").exists()
    proc = _rotm("compat", str(src), "--json", "-")
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    rep = json.loads(proc.stdout)
    assert rep["data"]["written"] is True and rep["data"]["replaced_count"] == 2, rep["data"]
    out = Path(rep["data"]["output"])
    rates = workbook.open_workbook(out, with_values=False).sheet("Rates")
    assert rates.cell("E1").formula == '=IFERROR(_xlfn.XLOOKUP("b",A2:A4,B2:B4),"_xlfn.XLOOKUP unavailable")'
    assert rates.cell("E2").formula == '=_xlfn.XLOOKUP("b",A2:A4,B2:B4)&"|"&_xlfn.XLOOKUP("b",A2:A4,B2:B4)'
    after = _job(out, [{"op": "read", "refs": ["Rates!E1", "Rates!E2"]}])
    assert after == {"Rates!E1": 73, "Rates!E2": "73|73"}, after


# ============================================================================= C. lift: PROPER as Excel gives it

# Measured in Excel 16 on the build PC, 2026-11-26 (BUILD/v7/P7/context_test.py): the text and Excel's PROPER
EXCEL_PROPER = {
    "élÈVE dupONT": "Élève Dupont", "ÉCOLE normale": "École Normale", "françois l'été": "François L'Été",
    "jean-luc PICARD": "Jean-Luc Picard", "œuvre ÇA": "Œuvre Ça", "straße": "Straße", "ßen": "ßen",
    "STRASSE über ÄRGER": "Strasse Über Ärger", "groß": "Groß", "홍길동": "홍길동", "홍길동abc": "홍길동abc",
    "abc홍길동def": "Abc홍길동def", "김 철수": "김 철수", "abc123def": "Abc123Def", "2nd place": "2Nd Place",
    "a1b2c3": "A1B2C3", "o'neil": "O'Neil", "don't": "Don'T", "o’neil": "O’Neil",
    "иван ПЕТРОВ": "Иван Петров", "istanbul ıi": "Istanbul Ii", "東京abc": "東京abc", "ǆungla": "Ǆungla",
    "école": "ÉCole", "ﬁx": "ﬁx", "ÿves": "Ÿves", "ŉa": "ŉa", "ａｂｃ ｄｅｆ": "Ａｂｃ Ｄｅｆ",
    "αλφα βητα": "Αλφα Βητα", "\U0001F600abc": "\U0001F600Abc", "a\U00010428b": "A\U00010428B",
}
# Excel gives another case than the rule (or the character was not measured): the script does not reproduce them
NOT_REPRODUCED = {"ΟΔΟΣ": "U+03A3", "µm": "U+00B5", "İSTANBUL": "U+0130", "ⅰⅱ abc": "U+2170", "\ud800": "U+D800"}


def _runtime() -> dict:
    ns: dict = {}
    exec(compile(lift.runtime_source(), "lift_runtime", "exec"), ns)  # noqa: S102 - the plugin's own runtime
    return ns


def test_proper_follows_the_measured_excel_rule() -> None:
    rt = _runtime()
    got = {t: rt["PROPER"](t) for t in EXCEL_PROPER}
    assert got == EXCEL_PROPER
    for text, code in NOT_REPRODUCED.items():
        with pytest.raises(rt["NotReproduced"]) as exc:
            rt["PROPER"](text)
        assert str(exc.value).startswith(f"PROPER of a text with the character {code} is not reproduced"), exc.value
    assert rt["PROPER"](123) == "123" and rt["PROPER"]("") == ""
    assert "PROPER" in lift.supported_functions() and "PROPER" in lift.REPRODUCED_LIMITS


@pytest.mark.excel
def test_excel_proper_examples_equal_the_script(tmp_path: Path) -> None:
    from openpyxl import Workbook
    texts = list(EXCEL_PROPER)
    wb = Workbook()
    ws = wb.active
    ws.title = "P"
    for r, text in enumerate(texts, start=1):
        ws.cell(r, 1, text).data_type = "s"
        ws.cell(r, 2, f"=PROPER(A{r})")
    path = tmp_path / "proper.xlsx"
    wb.save(path)
    values = _job(path, [{"op": "read", "refs": [f"P!B{r}" for r in range(1, len(texts) + 1)]}])
    excel_values = {t: values[f"P!B{r}"] for r, t in enumerate(texts, start=1)}
    rt = _runtime()
    assert excel_values == EXCEL_PROPER
    assert {t: rt["PROPER"](t) for t in texts} == excel_values


FIRST = ["élÈVE", "françois", "JEAN-luc", "o'neil", "d’artagnan", "ßen", "jürgen", "øyvind", "ÇAĞLA", "홍길동",
         "김철수abc", "ǆungla"]
LAST = ["dupONT", "MÜLLER-groß", "o'brien", "a1b2c3 2nd", "이영희 núñez"]


def _rows() -> list[dict]:
    with open(STARTER / "data" / "lines.csv", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _proper_model(root: Path, names: list[str]) -> Path:
    """The starter template with line_id holding `names` and a helper column PROPER({this:line_id})."""
    rows = _rows()
    assert len(rows) == len(names) == 60
    for row, name in zip(rows, names):
        row["line_id"] = name
    modelfolder.init_model(root, name="proper_v7", template="starter")
    path = root / "contracts" / "lines.contract.json"
    c = common.read_json(path)
    c.pop("shown_sha256", None)
    common.write_json(path, agree(c))
    extract = root.parent / f"{root.name}_extract.csv"
    with open(extract, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    rep = land.land_csv(root, "lines", extract)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    spec = common.read_json(root / "model.spec.json")
    helpers = next(s for s in spec["sheets"] if s.get("type") == "helpers")
    helpers["columns"].append({"id": "name_proper", "label": "Line name with each word capitalised",
                               "expr": "PROPER({this:line_id})", "format": "text"})
    common.write_json(root / "model.spec.json", spec)
    rep = build.build(root / "model.spec.json")
    assert not [f for f in rep.findings if f.severity == "error"], [f.line() for f in rep.findings]
    return root


def _names() -> list[str]:
    return [f"{first} {last}" for first in FIRST for last in LAST]


@pytest.mark.excel
def test_proper_helper_over_accented_names_ties_out(tmp_path: Path) -> None:
    root = _proper_model(tmp_path / "m", _names())
    report = lift.lift_model(root, run=True)
    assert report.exit_code == 0, [f.line() for f in report.findings] + report.notes
    tie = report.data["tie_out"]
    assert tie["status"] == "tie" and tie["counts"]["differ"] == 0 and tie["counts"]["not reproduced"] == 0
    step = next(s for s in tie["steps"] if s["step"] == "h_lines.name_proper")
    assert step["cells"] == 60 and step["tie"] == 60, step


@pytest.mark.excel
def test_proper_of_a_text_the_script_does_not_reproduce_is_listed(tmp_path: Path) -> None:
    names = _names()
    names[7] = "ΣΟΦΙΑ dupONT"                          # Greek capital sigma: Excel writes a final sigma at a word end
    root = _proper_model(tmp_path / "m", names)
    report = lift.lift_model(root, run=True)
    assert report.exit_code == common.EXIT_FINDINGS, [f.line() for f in report.findings] + report.notes
    tie = report.data["tie_out"]
    step = next(s for s in tie["steps"] if s["step"] == "h_lines.name_proper")
    assert (step["cells"], step["tie"], step["differ"], step["not reproduced"]) == (60, 59, 0, 1), step
    found = [f for f in report.findings if f.rule == "L-NOT-REPRODUCED"]
    assert len(found) == 1 and found[0].message.startswith("h_lines.name_proper: 1 of 60 cells are not reproduced"), \
        [f.line() for f in report.findings]
    assert "U+03A3" in found[0].message
    assert not [f for f in report.findings if f.rule == "L-DIFF"], [f.line() for f in report.findings]

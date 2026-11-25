"""v2 tests of the shared core (package P1): key equality, control totals, query stamps, accepted controls,
portable reports, the comparison with tolerances and volatile cells, formula cells without a value, and the
Excel bridge (formats after a Power Query attach or refresh, cells without a value, cleaned saves).

Owner: P1 (v2). Machine-specific strings (folders of this PC, user names) are built at run time, never written here.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from decimal import Decimal
from pathlib import Path

import pytest

from lib import common, schemas, workbook

SCRIPTS = Path(__file__).resolve().parents[1]
ROTM = SCRIPTS / "rotm.py"


def run_cli(*args: str, timeout: int = 900) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(ROTM), *args], capture_output=True,
                          text=True, encoding="utf-8", timeout=timeout)


# ============================================================================= part A

# ----------------------------------------------------------------------------- A1 key_tuple

@pytest.mark.parametrize("a, b", [
    ("001", "1"), ("Case", "CASE"), ("M*", "Monday"), ("A?", "AB"),
    ("12345678901234567890", "12345678901234500000"),
    ("1", 1), ("TRUE", True), (True, 1), (dt.date(2026, 1, 2), "2026-01-02"),
])
def test_key_tuple_keeps_these_apart(a: object, b: object) -> None:
    assert common.key_tuple([a]) != common.key_tuple([b])
    assert len({common.key_tuple([a]), common.key_tuple([b])}) == 2


@pytest.mark.parametrize("a, b", [
    ("Case", "Case"), (1, 1.0), (dt.date(2026, 1, 2), dt.date(2026, 1, 2)), (None, None), (False, False),
    (10 ** 17, 1e17),
])
def test_key_tuple_equal_values_are_equal(a: object, b: object) -> None:
    assert common.key_tuple([a]) == common.key_tuple([b])
    assert hash(common.key_tuple([a])) == hash(common.key_tuple([b]))


def test_key_tuple_of_several_parts_and_none() -> None:
    assert common.key_tuple(["A", 1]) == common.key_tuple(["A", 1.0])
    assert common.key_tuple(["A", 1]) != common.key_tuple(["a", 1])
    assert common.key_tuple([None]) == (None,)
    assert common.key_tuple(["x", None]) != common.key_tuple(["x", ""])


# ----------------------------------------------------------------------------- A2 control_total_ok

def test_control_total_has_no_term_that_grows_with_the_total() -> None:
    values = [999_999_999_000, 500]
    ok, total, diff = common.control_total_ok(values, 1_000_000_000_000, 0)
    assert (ok, total, diff) == (False, Decimal("999999999500"), Decimal("-500"))
    ok, _total, diff = common.control_total_ok(values, 1_000_000_000_000, 500)
    assert ok and diff == Decimal("-500")
    ok, _total, _diff = common.control_total_ok(values, 1_000_000_000_000, 499.99)
    assert not ok


def test_control_total_is_exact_on_decimal_fractions_and_landed_text() -> None:
    assert common.control_total_ok([0.1, 0.2], 0.3, 0)[0]
    assert common.control_total_ok(["0.1", "0.2", "", None, True], "0.3")[0]
    ok, total, diff = common.control_total_ok(["999999999999.99", "0.01"], 1e12)
    assert ok and total == Decimal("1000000000000.00") and diff == 0


def test_control_total_refuses_text_that_is_not_a_number() -> None:
    with pytest.raises(ValueError, match="not a number"):
        common.control_total_ok(["12", "twelve"], 24)


# ----------------------------------------------------------------------------- A3 query stamps

_SHA = "ab" * 32


def test_query_stamp_round_trip_and_edit() -> None:
    body = "SELECT a\r\nFROM t\r\n"
    stamped = common.stamp_query(body, _SHA, "--")
    first, rest = stamped.split("\n", 1)
    assert re.fullmatch(r"-- rotm-stamp: contract sha256:[0-9a-f]{64}; body sha256:[0-9a-f]{64}", first)
    assert rest == "SELECT a\nFROM t\n"
    st = common.read_query_stamp(stamped)
    assert st == {"contract_sha256": _SHA, "body_sha256": st["body_sha256"], "edited": False}
    assert st["body_sha256"] == common.hashlib.sha256(rest.encode("utf-8")).hexdigest()
    assert common.read_query_stamp(stamped.replace("\n", "\r\n"))["edited"] is False
    edited = stamped.replace("FROM t", "FROM u")
    assert common.read_query_stamp(edited)["edited"] is True
    # stamping again replaces the old stamp line
    again = common.stamp_query(stamped, "cd" * 32, "--")
    assert again.count("rotm-stamp") == 1 and common.read_query_stamp(again)["contract_sha256"] == "cd" * 32
    m = common.stamp_query("let\n  x = 1\nin\n  x\n", _SHA, "//")
    assert m.startswith("// rotm-stamp: contract sha256:") and common.read_query_stamp(m)["edited"] is False


def test_query_without_stamp_reads_as_none_and_bad_arguments_are_refused() -> None:
    assert common.read_query_stamp("SELECT 1\n") is None
    assert common.read_query_stamp("") is None
    assert common.read_query_stamp("-- rotm-stamp: contract sha256:xyz; body sha256:abc\nSELECT 1") is None
    with pytest.raises(ValueError):
        common.stamp_query("x", _SHA, "#")
    with pytest.raises(ValueError):
        common.stamp_query("x", "not a hash", "--")


# ----------------------------------------------------------------------------- A4 controls

_DIGEST = "sha256:" + "1" * 64


def _model(tmp_path: Path) -> Path:
    root = tmp_path / "m"
    (root / "contracts").mkdir(parents=True)
    (root / "rotm_model.json").write_text('{"rotm_model": 1, "name": "m"}\n', encoding="utf-8")
    (root / "contracts" / "lines.contract.json").write_text('{"keep": "me"}\n', encoding="utf-8")
    return root


def test_controls_one_entry_per_digest_and_contract_unchanged(tmp_path: Path) -> None:
    from lib import controls
    root = _model(tmp_path)
    contract = root / "contracts" / "lines.contract.json"
    before = contract.read_bytes()
    assert controls.path(root, "lines") == root / "raw" / "lines.controls.json"
    assert controls.totals_for(root, "lines", _DIGEST) is None
    totals = [{"column": "amount", "value": 1234.5, "tolerance": 0}]
    rec = controls.record_acceptance(root, "lines", digest=_DIGEST, rows=60, totals=totals,
                                     given=["lines.amount=1234.5"], at="2026-11-25T08:00:00+09:00")
    first = controls.path(root, "lines").read_bytes()
    again = controls.record_acceptance(root, "lines", digest=_DIGEST, rows=60, totals=totals,
                                       given=["lines.amount=1234.5"])
    assert again == rec and controls.path(root, "lines").read_bytes() == first
    data = json.loads(first)
    assert data["rotm_controls"] == 1 and data["table"] == "lines" and len(data["accepted"]) == 1
    assert schemas.validate(data, "controls") == []
    assert controls.totals_for(root, "lines", _DIGEST) == totals
    assert controls.totals_for(root, "lines", "sha256:" + "2" * 64) is None
    # a new value for the same snapshot replaces its record; another snapshot gets its own
    controls.record_acceptance(root, "lines", digest=_DIGEST, rows=60,
                               totals=[{"column": "amount", "value": 99, "tolerance": 0}], given=["lines.amount=99"])
    controls.record_acceptance(root, "lines", digest="sha256:" + "2" * 64, rows=61, totals=totals, given=[])
    data = json.loads(controls.path(root, "lines").read_text(encoding="utf-8"))
    assert [e["digest"] for e in data["accepted"]] == [_DIGEST, "sha256:" + "2" * 64]
    assert controls.totals_for(root, "lines", _DIGEST)[0]["value"] == 99
    assert contract.read_bytes() == before
    assert sorted(p.name for p in (root / "contracts").iterdir()) == ["lines.contract.json"]


def test_controls_invalid_file_is_a_refusal_naming_it(tmp_path: Path) -> None:
    from lib import controls
    root = _model(tmp_path)
    p = controls.path(root, "lines")
    p.parent.mkdir(parents=True)
    p.write_text('{"rotm_controls": 1, "table": "lines", "accepted": [{"digest": "abc"}]}', encoding="utf-8")
    with pytest.raises(common.Refusal) as exc:
        controls.totals_for(root, "lines", _DIGEST)
    assert "lines.controls.json" in exc.value.line()
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(common.Refusal, match="lines.controls.json"):
        controls.record_acceptance(root, "lines", digest=_DIGEST, rows=1, totals=[], given=[])


def test_controls_schema_cases() -> None:
    good = {"rotm_controls": 1, "table": "lines", "accepted": [
        {"digest": _DIGEST, "rows": 3, "totals": [{"column": "amount", "value": 1.5, "tolerance": 0}],
         "given": ["lines.amount=1.5"], "at": "2026-11-25T08:00:00+09:00"}]}
    assert schemas.validate(good, "controls") == []
    bad = json.loads(json.dumps(good))
    bad["accepted"][0]["rows"] = -1
    bad["accepted"][0]["totals"][0]["tolerance"] = -1
    bad["table"] = "Lines"
    got = [e.line() for e in schemas.validate(bad, "controls")]
    assert any(x.startswith("accepted[0].rows") for x in got)
    assert any(x.startswith("accepted[0].totals[0].tolerance") for x in got)
    assert any(x.startswith("table") for x in got)


# ----------------------------------------------------------------------------- A5 portable reports

def _variants(folder: Path) -> list[str]:
    text = str(folder.resolve())
    return [text, text.replace("\\", "/"), text.replace("/", "\\")]


def test_reports_are_written_without_machine_folders(outside_tmp: Path) -> None:
    tmp_path = outside_tmp          # v5: a folder outside the skill folder wherever --basetemp points (conftest.py)
    root = _model(tmp_path)
    skill = common.skill_dir()
    state = common.state_dir(create=True)
    m_back = str(root.resolve()).replace("/", "\\")
    m_fwd = root.resolve().as_posix()
    rep = common.Report("audit", f"{m_fwd}/model.xlsx")
    rep.note(f"wrote {m_back}\\raw\\lines.csv and {m_fwd}/queries/lines.sql")
    rep.note(f"run python {skill.as_posix()}/scripts/rotm.py check \"{m_fwd}\"")
    rep.note(f"details in {state}{os.sep}last_error.txt")
    rep.note(f"upper case: {m_fwd.upper()}/model.xlsx")
    rep.error("X-ONE", "something", f"open {m_back}\\model.xlsx in Excel")
    rep.data.update(saved=f"{m_fwd}/model.xlsx", output_sha256="ab" * 32, digest="sha256:" + "3" * 64,
                    files=[f"{m_fwd}/reports/render/calc.png"], other_folder=str(tmp_path / "elsewhere" / "x.txt"))
    rep.finish(False)
    out = root / "reports" / "audit.json"
    rep.write_json(out)
    text = out.read_text(encoding="utf-8")
    low = text.lower()
    for folder in (root, skill, state):
        for v in _variants(folder):
            assert v.lower() not in low, f"{v} is still in the report"
            assert json.dumps(v)[1:-1].lower() not in low
    data = json.loads(text)
    assert data["target"] == "model.xlsx"
    assert data["notes"][0] == "wrote raw/lines.csv and queries/lines.sql"
    assert data["notes"][1] == 'run python <SKILL_DIR>/scripts/rotm.py check "<model folder>"'
    assert data["notes"][2] == "details in <state folder>/last_error.txt"
    assert data["notes"][3] == "upper case: model.xlsx" if os.name == "nt" else True
    assert data["findings"][0]["fix"] == "open model.xlsx in Excel"
    assert data["data"]["saved"] == "model.xlsx" and data["data"]["files"] == ["reports/render/calc.png"]
    assert data["data"]["output_sha256"] == "ab" * 32 and data["data"]["digest"] == "sha256:" + "3" * 64
    assert data["data"]["other_folder"] == str(tmp_path / "elsewhere" / "x.txt")
    assert schemas.validate(data, "report") == []


def test_files_outside_reports_are_written_unchanged(tmp_path: Path) -> None:
    root = _model(tmp_path)
    obj = {"workbook": root.resolve().as_posix() + "/model.xlsx", "skill": str(common.skill_dir())}
    for rel in ("contracts/x.contract.json", "model.manifest.json", "raw/lines.controls.json", "reportsx/a.json"):
        common.write_json(root / rel, obj)
        assert json.loads((root / rel).read_text(encoding="utf-8")) == obj
    # a reports folder that is not in a model folder is not changed either
    plain = tmp_path / "plain" / "reports" / "a.json"
    common.write_json(plain, obj)
    assert json.loads(plain.read_text(encoding="utf-8")) == obj


def test_portable_keeps_a_similar_folder_name_absolute(outside_tmp: Path) -> None:
    root = _model(outside_tmp)      # v5: outside the skill folder wherever --basetemp points (conftest.py)
    sibling = Path(str(root) + "2") / "model.xlsx"
    common.write_json(root / "reports" / "x.json", {"a": str(sibling), "b": f"{root.resolve().as_posix()}/"})
    data = json.loads((root / "reports" / "x.json").read_text(encoding="utf-8"))
    assert data["a"] == str(sibling)
    assert data["b"] == "<model folder>/"


# ----------------------------------------------------------------------------- A6 comparison, volatile cells

def _snap(values: dict, formulas: dict | None = None, *, sheets=("Inputs", "calc"), cached: bool = True,
          names: dict | None = None, tables: dict | None = None) -> workbook.Snapshot:
    formulas = dict(formulas or {})
    return workbook.Snapshot(path=Path("x.xlsx"), sheets=list(sheets), formulas=formulas, values=dict(values),
                             styles=None, tables=dict(tables or {}), names=dict(names or {}), charts=0, cf={}, dv={},
                             queries=0, has_cached_values=cached, formula_cells=set(formulas))


def test_values_equal_is_exact_unless_a_tolerance_is_given() -> None:
    assert not workbook.values_equal(1_000_000_000_000, 999_999_999_500)
    assert not workbook.values_equal(0.1 + 0.2, 0.3)
    assert workbook.values_equal(0.1 + 0.2, 0.3, tolerance=1e-9)
    assert workbook.values_equal(1_000_000_000_000, 999_999_999_500, tolerance=1e-9)
    assert not workbook.values_equal(1_000_000_000_000, 999_999_999_500, tolerance=1e-13)
    assert workbook.values_equal(1e-12, 0.0, tolerance=1e-9)          # floor 1 for small values
    assert workbook.values_equal(dt.date(2026, 1, 31), 46053) and workbook.values_equal(None, "")
    assert not workbook.values_equal("a", "A", tolerance=1) and not workbook.values_equal(True, 1, tolerance=1)
    assert workbook.DEFAULT_CALC_TOLERANCE == 1e-9


def test_typed_values_are_compared_exactly() -> None:
    a = _snap({("Inputs", "A2"): 1_000_000_000_000})
    b = _snap({("Inputs", "A2"): 999_999_999_500})
    diffs = workbook.compare(a, b, workbook.Intended("none"))
    assert [d.message for d in diffs] == ["Inputs!A2: value changed (1000000000000 -> 999999999500)"]


def test_calculated_values_use_the_calc_tolerance() -> None:
    f = {("calc", "B2"): "=Inputs!A2*1"}
    a = _snap({("calc", "B2"): 1_000_000_000_000}, f)
    b = _snap({("calc", "B2"): 999_999_999_500}, f)
    assert workbook.compare(a, b, workbook.Intended("none")) == []
    assert len(workbook.compare(a, b, workbook.Intended("none"), calc_tolerance=0)) == 1
    stats: dict = {}
    workbook.compare(a, b, workbook.Intended("none"), calc_tolerance=0, stats=stats)
    assert stats == {"cells_compared": 1, "values_left_out": 0, "first_left_out": None, "calc_tolerance": 0,
                     "calculated_values_compared": True}


def _volatile_book() -> workbook.Snapshot:
    f = {("calc", "A1"): "=RANDBETWEEN(1,12)", ("calc", "B1"): "=A1*2", ("calc", "C1"): "=B1+1",
         ("calc", "D1"): "=5*2", ("calc", "E1"): "=SUM(A1:A3)", ("calc", "F1"): "=SUM(Inputs!A:A)",
         ("Inputs", "A5"): "=NOW()", ("calc", "G1"): "=rng+1", ("calc", "H1"): "=vol",
         ("calc", "I1"): "=D1+1", ("calc", "J1"): "=SUM(Inputs!B:B)", ("calc", "K1"): "=SUM(2:2)",
         ("calc", "A2"): "=C1", ("calc", "L1"): "=\"RAND()\"&D1", ("calc", "M1"): "=SUM(tbl[amount])",
         ("Inputs", "C3"): "=TODAY()"}
    values = {k: 1 for k in f}
    return _snap(values, f, names={"rng": "calc!$C$1", "vol": "TODAY()+0", "calc!loc": "calc!$D$1"},
                 tables={"tbl": "Inputs!A1:C9"})


def test_volatile_cells_follow_direct_and_indirect_references() -> None:
    cells, first = workbook.volatile_cells(_volatile_book())
    want = {("calc", c) for c in ("A1", "B1", "C1", "E1", "F1", "G1", "H1", "K1", "A2", "M1")} | {
        ("Inputs", "A5"), ("Inputs", "C3")}
    assert cells == want
    assert first == "Inputs!C3"         # sheet order, then row, then column
    assert ("calc", "D1") not in cells and ("calc", "I1") not in cells and ("calc", "L1") not in cells
    assert ("calc", "J1") not in cells


def test_no_volatile_function_gives_no_cells() -> None:
    snap = _snap({("calc", "A1"): 1}, {("calc", "A1"): "=1+1", ("calc", "B1"): "=A1"})
    assert workbook.volatile_cells(snap) == (set(), None)


def test_compare_leaves_volatile_values_out_and_states_it() -> None:
    before = _volatile_book()
    after = _volatile_book()
    after.values[("calc", "A1")] = 7          # a new random draw and its dependents
    after.values[("calc", "B1")] = 14
    after.values[("calc", "D1")] = 11         # a real change of a cell that does not depend on a volatile one
    skip, _first = workbook.volatile_cells(before)
    stats: dict = {}
    diffs = workbook.compare(before, after, workbook.Intended("none"), skip_values=skip, stats=stats)
    assert [(d.sheet, d.cell) for d in diffs] == [("calc", "D1")]
    assert stats["cells_compared"] == len(before.formulas)
    assert stats["values_left_out"] == len(skip) and stats["first_left_out"] == "Inputs!C3"
    text = workbook.preservation_statement(stats)
    assert text == (f"compared {len(before.formulas)} cells: formulas and typed values exactly, calculated values "
                    f"within a relative tolerance of 1e-09; {len(skip)} cells left out of the value comparison "
                    "because they hold a volatile function or depend on one (first: Inputs!C3)")
    # a changed formula in a left-out cell is still reported
    after.formulas[("calc", "B1")] = "=A1*3"
    diffs = workbook.compare(before, after, workbook.Intended("none"), skip_values=skip)
    assert {(d.kind, d.cell) for d in diffs} == {("formula", "B1"), ("value", "D1")}


def test_preservation_statement_forms() -> None:
    s = workbook.preservation_statement({"cells_compared": 2734, "values_left_out": 12,
                                         "first_left_out": "DA-DATA!B10", "calc_tolerance": 1e-9})
    assert s == ("compared 2,734 cells: formulas and typed values exactly, calculated values within a relative "
                 "tolerance of 1e-09; 12 cells left out of the value comparison because they hold a volatile "
                 "function or depend on one (first: DA-DATA!B10)")
    s = workbook.preservation_statement({"cells_compared": 1, "values_left_out": 1, "first_left_out": "a!A1",
                                         "calc_tolerance": 0})
    assert s == ("compared 1 cell: formulas and typed values exactly, calculated values exactly; 1 cell left out "
                 "of the value comparison because it holds a volatile function or depends on one (first: a!A1)")
    s = workbook.preservation_statement({"cells_compared": 5, "values_left_out": 0, "first_left_out": None,
                                         "calc_tolerance": 1e-9, "calculated_values_compared": False})
    assert s == ("compared 5 cells: formulas and typed values exactly; calculated values were not compared "
                 "because a workbook holds no cached values")


def test_volatile_cells_is_fast_on_a_large_workbook() -> None:
    formulas: dict = {("calc", "A1"): "=RAND()"}
    for r in range(2, 47_002):
        formulas[("calc", f"A{r}")] = f"=A{r - 1}+1"
        formulas[("calc", f"B{r}")] = f"=SUMIFS(raw!$C:$C,raw!$A:$A,A{r})*2+C{r}"
    snap = _snap({k: 1 for k in formulas}, formulas, sheets=("calc", "raw"))
    assert len(snap.formulas) > 94_000
    t0 = time.monotonic()
    cells, first = workbook.volatile_cells(snap)
    elapsed = time.monotonic() - t0
    assert len(cells) == len(formulas) and first == "calc!A1"
    assert elapsed < 30, f"volatile_cells took {elapsed:.1f} s on {len(formulas):,} formulas"
    print(f"volatile_cells: {len(formulas):,} formulas in {elapsed:.2f} s")


def _xlsx_with_values(path: Path) -> None:
    """A workbook whose sheet XML has formula cells with and without a <v> element (edited after openpyxl)."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "loop"
    ws["A1"] = "=B1+1"
    ws["B1"] = "=A1+1"
    ws["C1"] = "=1+1"
    ws["D1"] = '=""'
    ws["E1"] = 5
    ws["F1"] = "=E1*2"
    wb.save(path)
    with zipfile.ZipFile(path) as z:
        parts = {i.filename: z.read(i.filename) for i in z.infolist()}
    xml = parts["xl/worksheets/sheet1.xml"].decode("utf-8")
    # openpyxl writes <v /> after every formula. As Excel 16 saves them (measured 2026-11-25): C1 a value, D1 an
    # empty text result (t="str", empty <v/>), A1 a circular cell (t="str", ca="1", empty <v/>); B1 gets no <v>
    # element at all; F1 keeps openpyxl's empty <v /> without a type (no value either).
    xml = re.sub(r"<f>1\+1</f>(<v\s*/>|<v></v>)?", "<f>1+1</f><v>2</v>", xml)
    xml = re.sub(r'<c r="D1"([^>]*)><f>""</f>(<v\s*/>|<v></v>)?', r'<c r="D1" t="str"><f>""</f><v/>', xml)
    xml = re.sub(r'<c r="A1"([^>]*)><f>B1\+1</f>(<v\s*/>|<v></v>)?', r'<c r="A1" t="str"><f ca="1">B1+1</f><v/>', xml)
    xml = re.sub(r"<f>A1\+1</f>(<v\s*/>|<v></v>)", "<f>A1+1</f>", xml)
    assert "<f>A1+1</f></c>" in xml and "<v>2</v>" in xml and xml.count('t="str"') == 2
    parts["xl/worksheets/sheet1.xml"] = xml.encode("utf-8")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in parts.items():
            z.writestr(name, data)


def test_formula_cells_without_value(tmp_path: Path) -> None:
    from openpyxl import Workbook
    fresh = tmp_path / "fresh.xlsx"
    wb = Workbook()
    wb.active["A1"] = "=B1+1"
    wb.save(fresh)
    assert workbook.formula_cells_without_value(fresh) == []        # no cached values at all
    p = tmp_path / "loop.xlsx"
    _xlsx_with_values(p)
    assert workbook.formula_cells_without_value(p) == [("loop", "A1"), ("loop", "B1"), ("loop", "F1")]


# ----------------------------------------------------------------------------- A7 schemas

def test_schema_names_include_controls() -> None:
    assert "controls" in schemas.SCHEMA_NAMES and "controls" in schemas.SCHEMAS


# ============================================================================= part B

def test_render_help_states_the_default_limits() -> None:
    proc = run_cli("render", "--help", timeout=120)
    assert proc.returncode == 0
    text = " ".join(proc.stdout.split())
    assert "rows 1 to 200" in text and "columns A to AN" in text
    assert "--max-rows" in text and "--max-cols" in text
    assert '"truncated": true' in text and "last row" in text and "last column" in text


def _circular_book(path: Path) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "loop"
    ws["A1"] = "=B1+1"
    ws["B1"] = "=A1+1"
    ws["C1"] = "=1+1"
    ws["D1"] = '=""'
    ws["E1"] = 5
    wb.save(path)


@pytest.mark.excel
def test_recalc_reports_a_circular_pair_and_not_an_empty_text_result(tmp_path: Path) -> None:
    wb = tmp_path / "loop.xlsx"
    _circular_book(wb)
    proc = run_cli("recalc", str(wb), "--json", "-")
    assert proc.returncode == common.EXIT_FINDINGS, proc.stdout + proc.stderr
    report = json.loads(proc.stdout)
    named = {(f["sheet"], c) for f in report["findings"] if f["rule"] == "V-NO-VALUE" for c in f["cells"]}
    assert named == {("loop", "A1"), ("loop", "B1")}
    assert not [f for f in report["findings"] if f["rule"] == "V-ERROR"]
    texts = {(c[0], c[1]): c[2] for c in report["data"]["error_cells"]}
    assert set(texts) == {("loop", "A1"), ("loop", "B1")} and report["data"]["error_count"] == 2
    assert all(t.startswith("no value") for t in texts.values())
    # the file Excel saved: both circular cells hold no value, the empty text result does
    assert workbook.formula_cells_without_value(wb) == [("loop", "A1"), ("loop", "B1")]


@pytest.mark.excel
def test_a_saved_workbook_holds_no_folder_path_and_no_user_name(tmp_path: Path) -> None:
    from openpyxl import Workbook
    from lib import excel
    wb_path = tmp_path / "clean.xlsx"
    wb = Workbook()
    wb.active["A1"] = 2
    wb.active["B1"] = "=A1*3"
    wb.save(wb_path)
    proc = run_cli("recalc", str(wb_path), "--json", "-")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads(proc.stdout)
    assert report["data"]["output_sha256"] == common.file_sha256(wb_path)
    with zipfile.ZipFile(wb_path) as z:
        names = z.namelist()
        wbx = z.read("xl/workbook.xml").decode("utf-8")
        core = z.read("docProps/core.xml").decode("utf-8")
    assert names[0] == "[Content_Types].xml"
    assert "absPath" not in wbx
    folder = str(tmp_path.resolve())
    for v in (folder, folder.replace("\\", "/"), folder.replace("/", "\\")):
        assert v.lower() not in wbx.lower() and v.lower() not in core.lower()
    assert re.findall(r"<cp:lastModifiedBy>(.*?)</cp:lastModifiedBy>", core) == [excel.creator_text()]
    # Excel opens the cleaned file again
    proc = run_cli("recalc", str(wb_path), "--read", "Sheet!B1", "--json", "-")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout)["data"]["values"] == {"Sheet!B1": 6}


def test_clean_saved_workbook_changes_only_the_two_members(tmp_path: Path) -> None:
    from lib import excel
    p = tmp_path / "book.xlsx"
    folder = "D:" + "\\work\\example\\"
    wbx = ('<workbook xmlns="x"><fileVersion appName="xl"/><workbookPr/><mc:AlternateContent '
           'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"><mc:Choice Requires="x15">'
           '<x15ac:absPath url="' + folder + '" '
           'xmlns:x15ac="http://schemas.microsoft.com/office/spreadsheetml/2010/11/ac"/>'
           '</mc:Choice></mc:AlternateContent><mc:AlternateContent xmlns:mc="m"><mc:Choice Requires="x"><keep/>'
           '</mc:Choice></mc:AlternateContent><bookViews/></workbook>')
    core = ('<cp:coreProperties xmlns:cp="c"><dc:creator>rule-of-thumb-model 0.1.0</dc:creator>'
            '<cp:lastModifiedBy>' + "Some" + " Person" + '</cp:lastModifiedBy></cp:coreProperties>')
    other = "<sheet>unchanged é</sheet>".encode("utf-8")
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", b"<Types/>")
        z.writestr("xl/workbook.xml", wbx.encode("utf-8"))
        z.writestr("docProps/core.xml", core.encode("utf-8"))
        z.writestr("xl/worksheets/sheet1.xml", other)
    assert excel.clean_saved_workbook(p) is None
    with zipfile.ZipFile(p) as z:
        assert z.namelist() == ["[Content_Types].xml", "xl/workbook.xml", "docProps/core.xml",
                                "xl/worksheets/sheet1.xml"]
        new_wbx = z.read("xl/workbook.xml").decode("utf-8")
        assert "absPath" not in new_wbx and folder not in new_wbx and "<keep/>" in new_wbx
        assert new_wbx.count("AlternateContent") == 2
        assert z.read("docProps/core.xml").decode("utf-8").endswith(
            "<cp:lastModifiedBy>" + excel.creator_text() + "</cp:lastModifiedBy></cp:coreProperties>")
        assert z.read("xl/worksheets/sheet1.xml") == other and z.read("[Content_Types].xml") == b"<Types/>"
    sha = common.file_sha256(p)
    assert excel.clean_saved_workbook(p) is None and common.file_sha256(p) == sha     # nothing left to change


# ----------------------------------------------------------------------------- B1: Power Query formats

_SPEC = {
    "rotm_spec": 1, "name": "pq_formats", "title": "Power Query formats (P1 test)", "output": "model.xlsx",
    "product": "both",
    "sheets": [
        {"name": "inputs", "type": "inputs", "items": [
            {"id": "start_month", "label": "First month", "value": "2026-01-31", "type": "date", "unit": "date",
             "source": "test"},
            {"id": "categories", "kind": "list", "label": "Categories",
             "values": ["Rent", "Staff", "Supplies", "Travel"], "format": "text", "source": "test"}]},
        {"name": "raw_lines", "type": "raw", "table": "lines", "landing": "power_query"},
        {"name": "h_lines", "type": "helpers", "table": "lines", "columns": [
            {"id": "month_end", "label": "Month end", "expr": "EOMONTH({this:line_date},0)", "format": "month"}]},
        {"name": "calc", "type": "calc", "blocks": [
            {"id": "spend", "title": "Expenses by category and month", "unit": "KRW", "format": "amount",
             "rows": {"in": "categories", "label": "Category"},
             "cols": {"timeline": {"start": "start_month", "periods": 3, "step": "month"}},
             "cell": "SUMIFS({h:lines.amount},{h:lines.category},{row},{h:lines.month_end},{col})",
             "total": {"row": True, "col": True}}]},
        {"name": "checks", "type": "checks", "checks": [
            {"id": "spend_ties_raw", "kind": "equal", "label": "Grid total equals the raw total of amount",
             "actual": "{blk:spend.total}", "expected": "SUM({raw:lines.amount})"}]}]}

_CATS = ("Rent", "Staff", "Supplies", "Travel")


def _lines(n: int, start: int = 1) -> list[list[str]]:
    rows = []
    for k in range(start, start + n):
        day = dt.date(2026, 1, 1) + dt.timedelta(days=(k * 7) % 89)
        rows.append([f"L{k:04d}", day.isoformat(), _CATS[k % 4], str(40_000 + (k * 7919) % 1_900_000)])
    return rows


def _write_lines(path: Path, rows: list[list[str]]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["line_id", "line_date", "category", "amount"])
        w.writerows(rows)


def _ok(proc: subprocess.CompletedProcess[str], what: str) -> None:
    assert proc.returncode == 0, f"{what}: exit {proc.returncode}\n{proc.stdout[-3000:]}\n{proc.stderr[-2000:]}"


def _audit(root: Path, out: Path, label: str, log: list[str]) -> dict:
    proc = run_cli("audit", str(root / "model.xlsx"), "--strict", "--json", str(out))
    rep = json.loads(out.read_text(encoding="utf-8"))
    found = [f"{f['rule']} {f.get('sheet')}!{f.get('cell')} ({f.get('count')}): {f['message']}"
             for f in rep["findings"]]
    styles = [x for x in found if x.startswith("S-")]
    log.append(f"{label}: audit --strict exit {proc.returncode}, findings {len(found)}, S-* findings {len(styles)}")
    assert not styles, f"{label}: style findings after the query load:\n" + "\n".join(styles) + "\n" + "\n".join(log)
    assert proc.returncode == 0, f"{label}: audit --strict exit {proc.returncode}:\n" + "\n".join(found + log)
    return rep


def _cycle(root: Path, tmp: Path, rows: list[list[str]], label: str, log: list[str]) -> None:
    _write_lines(root / "raw" / "lines.csv", rows)
    total = sum(int(r[3]) for r in rows)
    steps = (("check --refresh", ["check", str(root), "--refresh", "--quiet"]),
             ("extend", ["extend", str(root), "--quiet"]),
             ("check --accept-refresh --control", ["check", str(root), "--accept-refresh", "--control",
                                                   f"lines.amount={total}", "--quiet"]))
    for name, args in steps:
        proc = run_cli(*args)
        log.append(f"{label}: {name} exit {proc.returncode}: {(proc.stdout + proc.stderr).strip()[-300:]}")
    _audit(root, tmp / f"audit_{label.replace(' ', '_')}.json", label, log)


@pytest.mark.excel
def test_power_query_raw_table_keeps_its_formats_through_attach_growth_and_shrink(tmp_path: Path) -> None:
    root = tmp_path / "pqm"
    _ok(run_cli("init", str(root), "--name", "pq_formats"), "init")
    data = tmp_path / "lines.csv"
    base = _lines(60)
    _write_lines(data, base)
    total = sum(int(r[3]) for r in base)
    _ok(run_cli("contract", "propose", str(root), "--table", "lines", "--csv", str(data), "--key", "line_id",
                "--grain", "one row per expense line", "--unit", "amount=KRW", "--total", f"amount={total}"),
        "contract propose")
    task = tmp_path / "task.md"
    task.write_text("The user's reply to the contract: agreed\n", encoding="utf-8")
    _ok(run_cli("contract", "agree", str(root), "--table", "lines", "--reply", "agreed", "--by", "P1 test",
                "--channel", "task-file", "--source-file", str(task)), "contract agree")
    _ok(run_cli("land", str(root), "--table", "lines", "--csv", str(data)), "land --csv")
    (root / "model.spec.json").write_text(json.dumps(_SPEC, indent=1) + "\n", encoding="utf-8")
    _ok(run_cli("build", str(root), "--strict"), "build --strict")
    man = json.loads((root / "model.manifest.json").read_text(encoding="utf-8"))
    raw = next(e for e in man["sheets"] if e.get("type") == "raw")
    assert raw.get("landing") == "power_query", raw
    log: list[str] = []
    _audit(root, tmp_path / "audit_attach.json", "after the build's attach", log)
    _cycle(root, tmp_path, base + _lines(15, start=61), "75 rows", log)
    _cycle(root, tmp_path, base[:40], "40 rows", log)
    print("\n".join(log))


def _pq_manifest(model_dir: Path, landing: str = "power_query") -> Path:
    from lib import manifest as mmod
    wb = model_dir / "model.xlsx"
    data = {"rotm_manifest": 1, "workbook": "model.xlsx", "spec": "model.spec.json",
            "sheets": [{"name": "raw_lines", "type": "raw", "font_size": 8, "table": "lines",
                        "excel_table": "tbl_raw_lines", "landing": landing, "query_name": "q_lines",
                        "columns": ["line_id", "line_date", "category", "amount"],
                        "types": ["text", "date", "text", "number"], "formats": ["text", "date", None, "amount2"],
                        "rows": 60, "digest": "sha256:" + "c" * 64, "snapshot": "raw/lines.csv"},
                       {"name": "calc", "type": "calc", "font_size": 9}]}
    mmod.write_manifest(wb, data)
    return wb


def test_raw_formats_come_from_the_manifest_and_the_dictionary(model_dir: Path) -> None:
    from lib import dictionary, excel
    d = dictionary.load_for_model(model_dir)
    wb = _pq_manifest(model_dir)
    got = excel.raw_formats(wb)
    assert list(got) == ["raw_lines"]
    cols = got["raw_lines"]["columns"]
    assert [c["number_format"] for c in cols] == [d.number_format("text"), d.number_format("date"),
                                                   d.number_format(d.format_for_column_type("text")),
                                                   d.number_format("amount2")]
    assert all(c["font"]["name"] == d.font_name and c["font"]["size"] == 8 for c in cols)
    assert [c["align"]["horizontal"] for c in cols] == ["left", "right", "left", "right"]
    assert "border" not in cols[0]
    header = got["raw_lines"]["header"]
    assert header["font"]["bold"] is True and header["fill"] == d.style("col_header").fill
    assert header["number_format"] == d.number_format("general")
    # a static raw sheet gets formats only when a pq_attach loads it
    wb = _pq_manifest(model_dir, landing="static")
    assert excel.raw_formats(wb) is None
    assert list(excel.raw_formats(wb, sheets=["raw_lines"])) == ["raw_lines"]
    # a workbook without a manifest: nothing is added
    assert excel.raw_formats(model_dir / "other.xlsx") is None


def test_formats_key_is_added_to_query_operations_and_normalised(model_dir: Path) -> None:
    from lib import excel
    wb = _pq_manifest(model_dir)
    notes: list[str] = []
    ops = excel._with_raw_formats(wb, [{"op": "recalc"}, {"op": "pq_refresh"}], notes)
    assert "formats" not in ops[0] and set(ops[1]["formats"]) == {"raw_lines"} and notes == []
    norm = excel.normalize_operations(ops)
    look = norm[1]["formats"]["raw_lines"]["columns"][0]
    assert look["align"]["horizontal"] == -4131 and look["fill"] is None and look["number_format"] == "@"
    head = norm[1]["formats"]["raw_lines"]["header"]
    assert head["border"]["bottom"]["line"] == 1 and head["align"]["horizontal"] == -4108
    # an explicit "formats": None stays None (no format is applied: a refresh as Excel does it by hand)
    kept = excel._with_raw_formats(wb, [{"op": "pq_refresh", "formats": None}], notes)
    assert excel.normalize_operations(kept)[0]["formats"] is None
    # an invalid manifest leaves the operations as they are, with a note
    (model_dir / "model.manifest.json").write_text("{bad", encoding="utf-8")
    same = excel._with_raw_formats(wb, [{"op": "pq_attach", "sheet": "raw_lines"}], notes)
    assert "formats" not in same[0] and notes and "not applied" in notes[0]

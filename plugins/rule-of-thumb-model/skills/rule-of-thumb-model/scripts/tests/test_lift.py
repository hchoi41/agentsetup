"""Tests for lib/lift.py (owner M8): the script runtime against Excel's rules, the translator, the generated
script, and the tie-out (with and without Excel)."""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from lib import common, lift
from lib.lift import (BLANK, NotReproduced, Rng, XLErr, cell_value, date_serial)

DATA = Path(__file__).resolve().parent / "data" / "lift"
if str(DATA) not in sys.path:
    sys.path.insert(0, str(DATA))
import fixture  # noqa: E402

SCRIPTS = Path(__file__).resolve().parents[1]
ROTM = SCRIPTS / "rotm.py"
S = date_serial


def ev(expr: str):
    """Translate a literal-only spec expression and compute it as one cell."""
    code = lift.translate(expr, lift._Ctx("check", None))
    ns = dict(vars(lift))
    return cell_value(lambda: eval(code, ns))  # noqa: S307 - code written by the translator under test


def load_script(path: Path):
    spec = importlib.util.spec_from_file_location(f"lifted_{abs(hash(str(path)))}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_script(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-X", "utf8", "-B", str(path), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)


def rotm(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(ROTM), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=600,
                          env=dict(os.environ, **(env or {})))


@pytest.fixture
def model(tmp_path: Path) -> dict:
    return fixture.write_model(tmp_path / "model")


# ----------------------------------------------------------------------------- runtime: Excel's rules

def test_round_is_half_away_from_zero_on_shown_digits():
    assert lift.ROUND(2.675, 2) == 2.68          # Python's round gives 2.67
    assert lift.ROUND(-2.5, 0) == -3
    assert lift.ROUND(2.5, 0) == 3
    assert lift.ROUND(1234.5, -2) == 1200
    assert lift.ROUNDUP(2.1, 0) == 3
    assert lift.ROUNDDOWN(-2.7, 0) == -2
    assert lift.INT(-2.5) == -3
    assert lift.MOD(-7, 3) == 2


def test_dates_follow_the_1900_system():
    assert S(dt.date(1900, 1, 1)) == 1
    assert S(dt.date(1900, 2, 28)) == 59
    assert S(dt.date(1900, 3, 1)) == 61
    assert lift.serial_ymd(60) == (1900, 2, 29)
    assert (lift.YEAR(0), lift.MONTH(0), lift.DAY(0)) == (1900, 1, 0)
    assert lift.EOMONTH(lift.DATE(2026, 1, 15), 1) == S(dt.date(2026, 2, 28))
    assert lift.EOMONTH(lift.DATE(2026, 1, 15), -1) == S(dt.date(2025, 12, 31))
    assert lift.DATE(2026, 14, 1) == S(dt.date(2027, 2, 1))
    assert lift.DATE(2026, 3, 0) == S(dt.date(2026, 2, 28))
    assert lift.EDATE(S(dt.date(2026, 1, 31)), 1) == S(dt.date(2026, 2, 28))
    assert lift.EOMONTH(0, 0) == 31
    assert lift.WEEKDAY(S(dt.date(2026, 11, 25))) == 4      # a Wednesday
    assert lift.parse_date_text("2026-01-31") == S(dt.date(2026, 1, 31))
    with pytest.raises(XLErr):
        lift.EOMONTH(-1, 0)


def test_criteria_match_as_excel_does():
    names = Rng.column(["region", "East", "east", "West", "Northeast", BLANK, ""], tall=True)
    vals = Rng.column(["amount", 10, 20, 30, 40, 50, 60], tall=True)
    assert lift.SUMIFS(vals, names, "EAST") == 30                 # without regard to case
    assert lift.SUMIFS(vals, names, "<>East") == 30 + 40 + 50 + 60 + 0
    assert lift.SUMIFS(vals, names, "*east") == 10 + 20 + 40      # wildcard
    assert lift.SUMIFS(vals, names, "") == 50 + 60                # empty cells and empty text
    assert lift.SUMIFS(vals, vals, ">25") == 30 + 40 + 50 + 60
    assert lift.COUNTIFS(vals, ">=20", names, "<>West") == 4
    assert lift.COUNTIF(vals, 30) == 1
    assert lift.COUNTIF(vals, "30") == 1                          # number text matches the number
    assert lift.AVERAGEIFS(vals, names, "east") == 15
    assert lift.MAXIFS(vals, names, "*e*") == 40
    with pytest.raises(XLErr):
        lift.AVERAGEIFS(vals, names, "nowhere")
    with pytest.raises(XLErr):                                     # ranges of different sizes
        lift.SUMIFS(Rng.column([1, 2]), Rng.column([1, 2, 3]), 1)


def test_index_path_and_test_path_agree():
    import random
    rnd = random.Random(7)
    keys = Rng.column(["k"] + [rnd.choice(["a", "b", "c", 1, 2, BLANK]) for _ in range(300)], tall=True)
    vals = Rng.column(["v"] + [rnd.randint(1, 9) for _ in range(300)], tall=True)
    for c in ["a", "B", 1, "1", "", "c"]:
        indexed = lift.SUMIFS(vals, keys, c)
        tests = sum(v for k, v in zip(keys.flat()[1:], vals.flat()[1:]) if lift._criterion_test(c)(k))
        assert indexed == tests, c


def test_errors_pass_on_and_iferror_catches_them():
    assert ev("IFERROR(1/0,5)") == 5
    assert ev("IF(0,1/0,5)") == 5                                  # IF evaluates one branch only
    assert isinstance(ev("1/0"), XLErr) and ev("1/0").code == "#DIV/0!"
    assert ev("ISERROR(1/0)") is True
    assert ev("ISNUMBER(1/0)") is False
    assert ev("IFNA(NA(),7)") == 7
    assert ev("IFNA(1/0,7)").code == "#DIV/0!"
    stored = cell_value(lambda: 1 / 0)
    assert cell_value(lambda: stored + 1).code == "#DIV/0!"         # a kept error raises again
    assert lift.IFERROR(lambda: stored, lambda: 0) == 0
    with pytest.raises(NotReproduced):                              # not an Excel error: never swallowed
        lift.IFERROR(lambda: lift.NOT_REPRODUCED("x"), lambda: 0)
    assert ev('"a"+1').code == "#VALUE!"


def test_blank_counts_as_zero_or_empty_text():
    assert BLANK + 1 == 1 and 1 - BLANK == 1 and BLANK * 5 == 0
    assert lift.JOIN(BLANK, "a") == "a"
    assert cell_value(lambda: 1 / BLANK).code == "#DIV/0!"
    assert cell_value(lambda: BLANK) == 0                            # a formula pointing at an empty cell shows 0
    assert lift.ISBLANK(lambda: BLANK) is True and lift.ISBLANK(lambda: "") is False
    assert lift.SUM(Rng.column([1, BLANK, "x", True, 2])) == 3       # a range: numbers only
    assert lift.SUM(1, True, "2") == 4                               # given directly: TRUE and number text count
    assert lift.COUNTA(Rng.column([1, BLANK, "", "x"])) == 3


def test_comparison_and_text_rules():
    assert lift.EQ("abc", "ABC") is True
    assert lift.LT(100, "a") is True                                 # numbers sort before text
    assert lift.GT(True, 100) is True                                # logical values after text
    assert lift.EQ(BLANK, 0) is True and lift.EQ(BLANK, "") is True
    assert lift.JOIN(1.5, "") == "1.5" and lift.JOIN(2.0, "") == "2" and lift.JOIN(True, "") == "TRUE"
    assert lift.JOIN(0.1 + 0.2, "") == "0.3"
    assert lift.TEXT(S(dt.date(2026, 3, 31)), "yyyy-mm") == "2026-03"
    assert lift.TEXT(S(dt.date(2026, 3, 31)), "mmm yyyy") == "Mar 2026"
    assert lift.TEXT(1234.567, "#,##0.0") == "1,234.6"
    assert lift.TEXT(0.256, "0.0%") == "25.6%"
    with pytest.raises(NotReproduced):
        lift.TEXT(1, "[h]:mm")
    assert lift.TRIM("  a   b ") == "a b"
    assert lift.MID("abcdef", 2, 3) == "bcd" and lift.LEFT("abc") == "a" and lift.RIGHT("abc", 2) == "bc"
    assert lift.SUBSTITUTE("a-b-c", "-", "+", 2) == "a-b+c"
    assert lift.SEARCH("B*D", "abcde") == 2 and lift.FIND("c", "abc") == 3


def test_lookups():
    col = Rng.column(["East", "North", "South", "West"])
    assert lift.MATCH("south", col, 0) == 3
    with pytest.raises(XLErr):
        lift.MATCH("Central", col, 0)
    bounds = Rng.row([0, 30, 60, 90])
    assert lift.MATCH(45, bounds) == 2
    with pytest.raises(XLErr):
        lift.MATCH(-1, bounds, 1)
    assert lift.MATCH(90, bounds, 1) == 4
    grid = Rng([[1, 2, 3], [4, 5, 6]])
    assert lift.INDEX(grid, 2, 3) == 6
    assert lift.INDEX(col, 2) == "North"
    with pytest.raises(XLErr):
        lift.INDEX(grid, 3, 1)
    assert lift.XLOOKUP("west", col, Rng.column([10, 20, 30, 40])) == 40
    assert lift.XLOOKUP("x", col, Rng.column([10, 20, 30, 40]), "none") == "none"
    assert lift.VLOOKUP("North", Rng([["East", 1], ["North", 2]]), 2, False) == 2
    assert lift.SUMPRODUCT(lift.GT(Rng.column([1, 5, 9]), 4) * Rng.column([10, 20, 30])) == 50


# ----------------------------------------------------------------------------- translator

@pytest.mark.parametrize("expr,expected", [
    ("-2^2", 4), ("2^3^2", 64), ("1+2*3", 7), ("(1+2)*3", 9), ('"a"&1+1', "a2"), ("10%", 0.1),
    ("--(3>2)", 1), ("2*-3", -6), ("1=1", True), ('"b">"A"', True), ("SUM({1,2;3,4})", 10),
    ("ROUND(SUM(1,2.555),2)", 3.56), ("IF(1>2,\"x\",IF(2>1,\"y\",\"z\"))", "y"), ("MAX(1,5,3)-MIN(4,2)", 3),
    ("DATE(2026,2,0)", S(dt.date(2026, 1, 31))), ("ABS(-1)*0", 0), ("TRUE+TRUE", 2)])
def test_translated_expressions_compute_as_excel(expr, expected):
    assert ev(expr) == expected


@pytest.mark.parametrize("expr,why", [
    ("OFFSET({in:x},1,1)", "volatile"), ("TODAY()", "volatile"), ("LET(x,1,x)", "not translated"),
    ("A1+1", "cell reference"), ("FOO(1)", "not translated"), ("COLUMN()", "position")])
def test_untranslatable_expressions_say_why(expr, why):
    with pytest.raises(lift.Untranslatable, match=why):
        lift.parse_expression(expr) and lift.translate(expr, lift._Ctx("check", None))


def test_supported_functions_are_runtime_functions():
    names = lift.supported_functions()
    for name in ("SUMIFS", "COUNTIFS", "INDEX", "MATCH", "EOMONTH", "IFERROR", "IF", "XLOOKUP", "TEXT",
                 "PMT", "PV", "FV", "NPV", "ROW"):
        assert name in names
    assert "NOT_REPRODUCED" not in names and "EQ" not in names and "COLUMN" not in names
    assert set(lift.REPRODUCED_LIMITS) <= names


# ----------------------------------------------------------------------------- the generated script

def test_lift_writes_a_script_that_compiles(model):
    report = lift.lift_model(model["root"])
    assert report.exit_code == 0, [f.line() for f in report.findings]
    script = model["root"] / "lift" / "fixture_sales_lift.py"
    text = script.read_text(encoding="utf-8")
    compile(text, str(script), "exec")
    assert report.data["not_reproduced"] == 0 and report.data["reproduced"] == len(report.data["steps"])
    # dependencies first: the helper columns before the block that reads them, actual before target (same_as)
    order = [s["step"] for s in report.data["steps"]]
    assert order.index("h_sales.amount_k") < order.index("calc.actual") < order.index("calc.target")
    assert order.index("calc.bridge") < order.index("check neg_var")
    assert order.index("summary.ytd") < order.index("check pct_range")


def test_script_has_no_pattern_the_raw_sheet_guard_refuses(model):
    lift.lift_model(model["root"])
    text = (model["root"] / "lift" / "fixture_sales_lift.py").read_text(encoding="utf-8")
    write_re = re.compile(r"(?i)(\.save\s*\(|\bsaveas\b|\.savecopyas\s*\(|\bto_excel\s*\(|\bexcelwriter\b|"
                          r"\bopenpyxl\s*\.\s*workbook\s*\(|\bxlsxwriter\b)")
    assert write_re.search(text) is None


def test_untranslatable_step_is_reported_and_marked(model):
    data = json.loads(model["spec"].read_text(encoding="utf-8"))
    data["sheets"][2]["columns"][1]["expr"] = "LET(x,{this:amount},x/{in:scale})"
    model["spec"].write_text(json.dumps(data), encoding="utf-8")
    report = lift.lift_model(model["root"])
    assert report.exit_code == 1
    rules = [f.rule for f in report.findings]
    # v3, A-aera-13: a function the evaluator lacks is named once, as LIFT-UNSUPPORTED (function, cells, first cell)
    assert rules == ["LIFT-UNSUPPORTED"]
    assert report.findings[0].message.startswith("the lift evaluator does not reproduce LET: 40 cell(s), first h_sales!")
    script = model["root"] / "lift" / "fixture_sales_lift.py"
    text = script.read_text(encoding="utf-8")
    compile(text, str(script), "exec")
    assert "h_sales.amount_k: LET is not translated" in text


def test_r_is_refused_as_not_available(model):
    with pytest.raises(common.EnvUnavailable, match="R output is not available"):
        lift.lift_model(model["root"], lang="r")


def test_missing_workbook_or_manifest_is_refused(model):
    model["manifest"].unlink()
    with pytest.raises(common.Refusal, match="manifest.json not found"):
        lift.lift_model(model["root"])
    model["workbook"].unlink()
    with pytest.raises(common.Refusal, match="model.xlsx not found"):
        lift.lift_model(model["root"])


def test_not_recalculated_workbook_is_not_verified(model):
    lift.lift_model(model["root"])
    res = run_script(model["root"] / "lift" / "fixture_sales_lift.py")
    assert res.returncode == 3, res.stdout + res.stderr
    assert "Not verified" in res.stdout


def test_cli_run_without_excel_reports_not_verified(model):
    res = rotm("lift", str(model["root"]), "--run", "--json", "-", env={"ROTM_NO_EXCEL": "1"})
    assert res.returncode == 3, res.stderr
    rep = json.loads(res.stdout)
    assert rep["status"] == "not_verified" and rep["command"] == "lift"


def _computed_values(model) -> dict:
    """(sheet, coord) -> the script's own value for every compared cell (a stand-in for Excel's values)."""
    lift.lift_model(model["root"])
    mod = load_script(model["root"] / "lift" / "fixture_sales_lift.py")
    m, _values = mod.run_model(model["root"])
    out = {}
    for step in m.steps:
        for r, c, v in step["cells"]:
            if step["kind"] == "input":
                continue
            v = v.code if isinstance(v, mod.XLErr) else v
            out[(step["sheet"], common.a1(c, r))] = v
    return out


def test_tie_out_passes_on_matching_values_and_names_a_changed_cell(model, tmp_path):
    values = _computed_values(model)
    good = fixture.write_values_workbook(model["workbook"], tmp_path / "good.xlsx", values)
    shutil.copy2(model["manifest"], tmp_path / "good.manifest.json")
    script = model["root"] / "lift" / "fixture_sales_lift.py"
    res = run_script(script, "--workbook", str(good), "--json", str(tmp_path / "t.json"))
    assert res.returncode == 0, res.stdout[-3000:]
    result = json.loads((tmp_path / "t.json").read_text(encoding="utf-8"))
    assert result["status"] == "tie" and result["counts"]["differ"] == 0 and result["counts"]["cells"] > 500
    values[("calc", "E7")] = values[("calc", "E7")] + 1
    bad = fixture.write_values_workbook(model["workbook"], tmp_path / "bad.xlsx", values)
    shutil.copy2(model["manifest"], tmp_path / "bad.manifest.json")
    res = run_script(script, "--workbook", str(bad), "--json", str(tmp_path / "b.json"))
    assert res.returncode == 1
    result = json.loads((tmp_path / "b.json").read_text(encoding="utf-8"))
    differ = [c for c in result["cells"] if c["status"] == "differ"]
    assert [c["cell"] for c in differ] == ["calc!E7"]
    assert differ[0]["difference"] == pytest.approx(-1)
    assert "calc!E7" in res.stdout


def test_raw_from_workbook_and_csv_output(model, tmp_path):
    values = _computed_values(model)
    good = fixture.write_values_workbook(model["workbook"], tmp_path / "good.xlsx", values)
    shutil.copy2(model["manifest"], tmp_path / "good.manifest.json")
    script = model["root"] / "lift" / "fixture_sales_lift.py"
    res = run_script(script, "--workbook", str(good), "--raw", "workbook", "--csv", str(tmp_path / "csv"))
    assert res.returncode == 0, res.stdout[-3000:]
    assert (tmp_path / "csv" / "h_sales.csv").is_file() and (tmp_path / "csv" / "calc.actual.csv").is_file()
    head = (tmp_path / "csv" / "calc.actual.csv").read_text(encoding="utf-8").splitlines()[0]
    assert head == ",2026-01-31,2026-02-28,2026-03-31"


def test_script_computes_the_model_independently_of_the_workbook(model):
    """The script's numbers equal a plain Python calculation from the raw rows."""
    lift.lift_model(model["root"])
    mod = load_script(model["root"] / "lift" / "fixture_sales_lift.py")
    m, _ = mod.run_model(model["root"])
    rows = fixture.sales_rows()
    for i, region in enumerate(fixture.REGIONS):
        for j, month in enumerate(fixture.MONTHS):
            expect = sum(r[4] for r in rows if r[3] == region and r[2][:7] == month[:7]) / fixture.SCALE
            assert m.blocks["actual"].values[i][j] == pytest.approx(expect)
    assert m.blocks["actual"].grand == pytest.approx(sum(r[4] for r in rows) / fixture.SCALE)
    assert m.checks["actual_ties_raw"]["result"] == 0
    assert m.master == 0
    focus = m.h["sales"].cols["focus_no"]
    assert set(focus) <= {0, 1, 2} and 0 in focus                  # IFERROR(MATCH(...),0) for non-focus regions


def test_inputs_from_the_workbook(tmp_path):
    """An input changed in the workbook: with the specification's inputs the input cell differs; with
    --inputs workbook the script uses the workbook's value and everything ties out."""
    base = fixture.write_model(tmp_path / "base")
    other = fixture.write_model(tmp_path / "other")
    data = json.loads(other["spec"].read_text(encoding="utf-8"))
    data["sheets"][0]["items"][0]["value"] = 2000                    # scale 2000 in the other model
    other["spec"].write_text(json.dumps(data), encoding="utf-8")
    values = _computed_values(other)
    values[("inputs", "D6")] = 2000
    wb = fixture.write_values_workbook(base["workbook"], tmp_path / "edited.xlsx", values)
    shutil.copy2(base["manifest"], tmp_path / "edited.manifest.json")
    lift.lift_model(base["root"])
    script = base["root"] / "lift" / "fixture_sales_lift.py"
    res = run_script(script, "--workbook", str(wb), "--json", str(tmp_path / "a.json"))
    assert res.returncode == 1
    differ = {c["step"] for c in json.loads((tmp_path / "a.json").read_text(encoding="utf-8"))["cells"]
              if c["status"] == "differ"}
    assert "input scale" in differ and "h_sales.amount_k" in differ
    res = run_script(script, "--workbook", str(wb), "--inputs", "workbook")
    assert res.returncode == 0, res.stdout[-3000:]


def test_ifs_and_switch_are_lazy_like_excel():
    assert ev("IFS(1>2,1/0,2>1,7)") == 7
    assert ev("IFS(1>2,1)").code == "#N/A"
    assert ev('SWITCH("b","a",1,"B",2,3)') == 2
    assert ev('SWITCH("z","a",1/0,9)') == 9


def test_twenty_thousand_rows_compute_in_seconds(tmp_path):
    import time
    rows = fixture.sales_rows(20000)
    p = fixture.write_model(tmp_path / "big", rows=rows)
    lift.lift_model(p["root"])
    mod = load_script(p["root"] / "lift" / "fixture_sales_lift.py")
    start = time.perf_counter()
    m, _ = mod.run_model(p["root"])
    elapsed = time.perf_counter() - start
    assert m.checks["actual_ties_raw"]["result"] == 0 and m.checks["sales_keys"]["result"] == 0
    assert elapsed < 60, f"{elapsed:.1f} s"


def test_frames_need_pandas_only_when_asked(model):
    pytest.importorskip("pandas")
    lift.lift_model(model["root"])
    mod = load_script(model["root"] / "lift" / "fixture_sales_lift.py")
    m, _ = mod.run_model(model["root"])
    frames = m.frames()
    assert list(frames["actual"].columns) == ["2026-01-31", "2026-02-28", "2026-03-31"]
    assert list(frames["actual"].index) == fixture.REGIONS
    assert len(frames["h_sales"]) == 40


# ----------------------------------------------------------------------------- with Excel

@pytest.mark.excel
def test_tie_out_against_excel(model):
    from lib import excel
    res = excel.recalc(model["workbook"], save=True)
    assert res.ok and not res.error_cells, res.errors
    report = lift.lift_model(model["root"], run=True)
    assert report.exit_code == 0, [f.line() for f in report.findings] + report.notes
    tie = report.data["tie_out"]
    assert tie["status"] == "tie" and tie["counts"]["cells"] > 600 and tie["counts"]["differ"] == 0
    assert report.verified["tied_out"] and report.verified["recalculated"]


@pytest.mark.excel
def test_uncovered_raw_rows_tie_out_and_fail_the_cover_check(tmp_path):
    """Helper rows end 5 rows before the raw rows: the script computes what the workbook holds, so the tie-out
    passes, and the cover check fails in both."""
    from lib import excel
    p = fixture.write_model(tmp_path / "short", capacity=35)
    assert excel.recalc(p["workbook"], save=True).ok
    report = lift.lift_model(p["root"], run=True)
    assert report.exit_code == 0, [f.line() for f in report.findings]
    mod = load_script(p["root"] / "lift" / "fixture_sales_lift.py")
    m, _ = mod.run_model(p["root"])
    assert m.checks["sales_cover"]["cells"]["D"] == 5 and m.checks["sales_cover"]["result"] == 1
    assert m.master == 1


@pytest.mark.excel
def test_lift_run_recalculates_a_copy_when_the_workbook_has_no_values(model):
    before = common.file_sha256(model["workbook"])
    report = lift.lift_model(model["root"], run=True)
    assert report.exit_code == 0, [f.line() for f in report.findings] + report.notes
    assert any("temporary copy was recalculated" in n for n in report.notes)
    assert common.file_sha256(model["workbook"]) == before          # the model's workbook is not changed


EXCEL_BATTERY = [
    "ROUND(2.675,2)", "ROUND(-1.005,2)", "ROUNDUP(-2.1,0)", "ROUNDDOWN(2.99,1)", "INT(-0.5)", "MOD(-7,3)",
    "MOD(7.5,-2)", "EOMONTH(DATE(2026,1,31),1)", "EOMONTH(DATE(2024,2,10),0)", "EDATE(DATE(2026,3,31),-1)",
    "DATE(2026,13,1)", "DATE(2026,1,0)", "YEAR(DATE(2026,7,4))", "WEEKDAY(DATE(2026,11,25))",
    "WEEKDAY(DATE(2026,11,25),2)", '"a"&1.5', '"x"&(0.1+0.2)', '"n"&1/3', '"t"&TRUE', 'TEXT(DATE(2026,3,5),"yyyy-mm-dd")',
    'TEXT(1234.5,"#,##0.00")', 'TEXT(0.1234,"0.0%")', 'LEFT("abc",2)&MID("abcdef",3,2)', 'TRIM("  a   b ")',
    'UPPER("ab")&LOWER("CD")', 'LEN("hello")', 'SUBSTITUTE("a-b-c","-","")', 'SEARCH("c*e","abcdef")',
    '"abc"="ABC"', '1<"a"', 'TRUE>100', "2^-1", "-2^2", "IFERROR(1/0,-1)", 'IFERROR(MATCH("q",{"a","b"},0),0)',
    'MATCH(25,{0,10,20,30},1)', 'INDEX({1,2;3,4},2,1)', 'SUMPRODUCT({1,2,3},{4,5,6})', 'AND(1,TRUE,2>1)',
    'OR(0,FALSE)', 'VALUE("12.5")+N(TRUE)', 'COUNT(1,"2",TRUE,"x")', 'MAX(-1,-5)', 'AVERAGE(1,2,4)',
    'ABS(-3)*SIGN(-2)', 'CHOOSE(2,"a","b","c")', 'XLOOKUP("b",{"a","b"},{10,20})', 'ISNUMBER("1")',
]


@pytest.mark.excel
def test_runtime_matches_excel_on_a_battery_of_formulas(tmp_path):
    import openpyxl

    from lib import excel, formula
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "battery"
    for k, expr in enumerate(EXCEL_BATTERY, start=1):
        ws.cell(k, 1, formula.to_file_formula("=" + expr))
    path = tmp_path / "battery.xlsx"
    wb.save(path)
    refs = [f"battery!A{k}" for k in range(1, len(EXCEL_BATTERY) + 1)]
    res = excel.recalc(path, save=False, read=refs)
    assert res.ok, res.errors
    problems = []
    for k, expr in enumerate(EXCEL_BATTERY, start=1):
        want = res.values[f"battery!A{k}"]
        got = ev(expr)
        status, _d = lift.compare_value(want, got, 1e-9)
        if status != "tie" and not (isinstance(want, bool) and isinstance(got, (int, float)) and want == got):
            problems.append(f"{expr}: Excel {want!r}, script {got!r}")
    assert not problems, problems


def _built_by_build(tmp_path: Path, monkeypatch) -> dict:
    """The fixture specification compiled by lib.build (M3), not by the hand-written fixture workbook."""
    p = fixture.write_model(tmp_path / "built")
    p["workbook"].unlink()
    p["manifest"].unlink()
    monkeypatch.setenv("ROTM_EXAMPLE_ROOTS", str(tmp_path))      # the fixture's contract uses channel "example"
    res = rotm("build", str(p["root"]), "--force", env={"ROTM_EXAMPLE_ROOTS": str(tmp_path)})
    if res.returncode == 4 and "not implemented" in res.stderr:
        pytest.skip("lib.build is not written yet")
    assert res.returncode in (0, 1), res.stderr[-2000:]                 # 1: W-NO-CHECK on block 'target'
    assert p["workbook"].is_file() and p["manifest"].is_file(), res.stdout[-2000:] + res.stderr[-2000:]
    return p


def test_lift_reproduces_every_step_of_a_workbook_build_wrote(tmp_path, monkeypatch):
    p = _built_by_build(tmp_path, monkeypatch)
    report = lift.lift_model(p["root"])
    assert report.data["not_reproduced"] == 0, [f.line() for f in report.findings]
    script = p["root"] / "lift" / "fixture_sales_lift.py"
    res = run_script(script)
    assert res.returncode == 3 and "Not verified" in res.stdout      # build writes no saved values


@pytest.mark.excel
def test_tie_out_of_a_workbook_build_wrote_after_excel(tmp_path, monkeypatch):
    """lib.build and lift work from the same specification independently; after Excel recalculates the built
    workbook, every compared cell ties out."""
    from lib import excel
    p = _built_by_build(tmp_path, monkeypatch)
    assert excel.recalc(p["workbook"], save=True).ok
    report = lift.lift_model(p["root"], run=True)
    assert report.exit_code == 0, [f.line() for f in report.findings] + report.notes
    assert report.data["tie_out"]["counts"]["cells"] > 600 and report.data["tie_out"]["status"] == "tie"


def test_lift_on_the_shipped_example_when_present(skill_dir):
    """The worked example, once built: lift writes a script that compiles and reproduces every step."""
    example = skill_dir / "examples" / "sales_vs_target"
    if not (example / "model.xlsx").is_file() or not (example / "model.manifest.json").is_file():
        pytest.skip("the worked example is not built yet")
    out = Path(os.environ.get("TMP", "/tmp")) / f"rotm_example_lift_{os.getpid()}.py"
    try:
        report = lift.lift_model(example, out=out)
        compile(out.read_text(encoding="utf-8"), str(out), "exec")
        assert report.data["not_reproduced"] == 0, [f.line() for f in report.findings]
    finally:
        if out.exists():
            out.unlink()

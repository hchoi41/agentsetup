"""Tests of package P6 in round 5 (v5): split and lift. Owner: P6 (v5).

Entries:
- [A-aera-5, C-sol-9]: split of a guarded lookup keeps the guard on the INDEX part as well as on the MATCH part
  (pattern H5 with three helpers); after the split the host gives the original's result for the key present, the
  key missing, a matched return cell that holds an error after the split, and a match position outside the INDEX
  range, with IFERROR and with IFNA, and no helper holds an error value that the guard hid. A lookup inside IFNA
  inside IFERROR is refused with SPLIT-GUARD.
- [A-aera-7]: the evaluator lift writes skips text (also number text) and TRUE/FALSE reached through a reference in
  SUM, AVERAGE, MIN, MAX, PRODUCT, COUNT and NPV, converts a typed value as Excel does, and matches the criteria of
  the conditional functions with the rules of lib/criteria.py excel_criteria_match. A form where lift cannot tell a
  reference from a value is listed as not reproduced before the tie-out.
Tests marked excel drive Excel through lib.excel.
"""
from __future__ import annotations

import ast
import csv
import datetime as dt
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
SKILL = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib import common, criteria, formula, lift, split, workbook  # noqa: E402
from lib.lift import BLANK, Rng, XLErr  # noqa: E402

DATA = HERE / "data" / "lift"
if str(DATA) not in sys.path:
    sys.path.insert(0, str(DATA))
import fixture  # noqa: E402


@pytest.fixture
def no_excel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")


# ============================================================================= [A-aera-5, C-sol-9] split

GUARDS = ("IFERROR", "IFNA")
TEST_OF = {"IFERROR": "ISERROR", "IFNA": "ISNA"}


def _lookup(guard: str, layout: str) -> str:
    if layout == "short":                       # the reviewers' formula: one return cell for two keys
        return f"={guard}(INDEX(D1:D1,MATCH(A1,C1:C2,0)),0)"
    return f"={guard}(INDEX(D2:D3,MATCH(A2,C2:C3,0)),0)"


def _guarded_workbook(path: Path, guard: str, layout: str) -> tuple[Path, str]:
    """Model!B2 (layout "lookup": keys C2:C3 = k1, k2, values D2:D3 = 10, 20, key A2 = k2) or Model!B1 (layout
    "short": keys C1:C2 = a, b, one value D1 = 10, key A1 = a) holds the guarded lookup. Returns (path, host)."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Model"
    if layout == "short":
        ws["A1"], ws["C1"], ws["C2"], ws["D1"] = "a", "a", "b", 10
        host = "B1"
    else:
        ws["A1"], ws["B1"], ws["C1"], ws["D1"] = "Key", "Value", "Keys", "Values"
        ws["A2"], ws["C2"], ws["C3"], ws["D2"], ws["D3"] = "k2", "k1", "k2", 10, 20
        host = "B2"
    ws[host] = formula.to_file_formula(_lookup(guard, layout))
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path, host


def test_guarded_lookup_plan_keeps_the_guard_on_the_index_part() -> None:
    for guard in GUARDS:
        plan = split.plan_split(_lookup(guard, "lookup"), limit=2)
        texts = [h for _l, h in plan.helpers]
        assert texts == [f"={guard}(MATCH(A2,C2:C3,0),0)", "=MAX(_rotm_h1,1)",
                         f"=--{TEST_OF[guard]}(INDEX(D2:D3,_rotm_h2))"], guard
        assert plan.rewritten == "=IF(OR(_rotm_h1=0,_rotm_h3=1),0,INDEX(D2:D3,_rotm_h2))", guard
        # every helper and the host within depth 2; the guard's value is written once, in the host
        assert all(formula.depth(t) <= 2 for t in texts + [plan.rewritten]) and plan.depth_after == 2
        assert sum(t.count(",0)") for t in texts[1:]) == 0
    # a guard value that is a typed number other than 0 or 1 stays where the v4 pattern put it: in the host only
    plan = split.plan_split("=IFERROR(INDEX(D2:D3,MATCH(A2,C2:C3,0)),7)", limit=2)
    assert plan.rewritten == "=IF(OR(_rotm_h1=0,_rotm_h3=1),7,INDEX(D2:D3,_rotm_h2))"
    assert not any("7" in h for _l, h in plan.helpers)
    # lookup_pattern (read by the selftest's expectation) gives the same texts
    part = formula.over_limit_parts(_lookup("IFERROR", "lookup"), 2)[0]
    assert split.lookup_pattern(_lookup("IFERROR", "lookup"), part) == (
        "MATCH", 1, "=IFERROR(MATCH(A2,C2:C3,0),0)", "IF(OR(_rotm_h1=0,_rotm_h3=1),0,INDEX(D2:D3,_rotm_h2))")


def test_ifna_lookup_inside_iferror_is_refused_and_nothing_is_written(tmp_path: Path, no_excel: None) -> None:
    from openpyxl import Workbook
    from lib import selftest
    text = "=IFERROR(ROUND(IFNA(INDEX(D2:D3,MATCH(A2,C2:C3,0)),5),2),7)"
    part = formula.over_limit_parts(text, 4)[0]                 # at limit 4 the part to move is the MATCH call
    assert part.text == "MATCH(A2,C2:C3,0)" and split.lookup_pattern(text, part) is None
    assert selftest.f08a_split_expectation(text, limit=4) == split.GUARD_RULE
    wb = Workbook()
    ws = wb.active
    ws.title = "Model"
    ws["A2"], ws["C2"], ws["C3"], ws["D2"], ws["D3"] = "k2", "k1", "k2", 10, 20
    ws["B2"] = formula.to_file_formula(text)
    src = tmp_path / "mixed.xlsx"
    wb.save(src)
    before = common.file_sha256(src)
    rep = split.split_cell(src, "Model!B2", limit=4)
    guard = [f for f in rep.findings if f.rule == split.GUARD_RULE]
    assert rep.exit_code == common.EXIT_FINDINGS and len(guard) == 1, [f.line() for f in rep.findings]
    assert guard[0].message.startswith("Model!B2: the lookup ") and "sits inside IFNA, which sits inside IFERROR" in \
        guard[0].message and guard[0].message.endswith("; nothing was written")
    assert rep.data["written"] is False and not (tmp_path / "mixed_split.xlsx").exists()
    assert common.file_sha256(src) == before


def test_guarded_lookup_split_without_excel_writes_three_helpers(tmp_path: Path, no_excel: None) -> None:
    for guard in GUARDS:
        src, host = _guarded_workbook(tmp_path / guard / "lookup.xlsx", guard, "lookup")
        rep = split.split_cell(src, f"Model!{host}")
        assert rep.data["written"] is True and rep.data["helpers_per_cell"] == 3, [f.line() for f in rep.findings]
        info = workbook.open_workbook(Path(rep.data["output"]), with_values=False)
        sh = info.sheet(split.HELPER_SHEET)

        def shown(cell: object) -> str:
            return formula.display_form(cell.formula)           # without the file prefix _xlfn. of IFNA

        assert shown(info.sheet("Model").cell("B2")) == ("=IF(OR(split_helpers!B2=0,split_helpers!D2=1),0,"
                                                         "INDEX(D2:D3,split_helpers!C2))")
        assert shown(sh.cell("B2")) == f"={guard}(MATCH(Model!A2,Model!C2:C3,0),0)"
        assert shown(sh.cell("C2")) == "=MAX(B2,1)"
        assert shown(sh.cell("D2")) == f"=--{TEST_OF[guard]}(INDEX(Model!D2:D3,C2))"
        assert "the larger of helper 1 and 1" in sh.cell("C1").value
        assert f"1 when {guard} replaces INDEX(D2:D3,helper 2), else 0" in sh.cell("D1").value


# What changes after the split, in both workbooks: (layout, [(cell, value or formula)], {guard: expected host value})
SPLIT_CASES = {
    "key_present": ("lookup", [("A2", "k1")], {"IFERROR": 10, "IFNA": 10}),
    "key_missing": ("lookup", [("A2", "missing")], {"IFERROR": 0, "IFNA": 0}),
    "return_cell_changed_to_an_error": ("lookup", [("A2", "k2"), ("D3", "=NA()")], {"IFERROR": 0, "IFNA": 0}),
    "return_cell_changed_to_div0": ("lookup", [("A2", "k2"), ("D3", "=1/0")], {"IFERROR": 0, "IFNA": "#DIV/0!"}),
    "position_outside_the_index_range": ("short", [("A1", "b")], {"IFERROR": 0, "IFNA": "#REF!"}),
}


@pytest.fixture(scope="module")
def guarded_splits(tmp_path_factory: pytest.TempPathFactory):
    """(original, split copy, host) per guard and layout, each split once through Excel with the values that give
    no error (key k2 or a)."""
    made: dict[tuple[str, str], tuple[Path, Path, str]] = {}

    def get(guard: str, layout: str) -> tuple[Path, Path, str]:
        if (guard, layout) not in made:
            folder = tmp_path_factory.mktemp(f"h5_{guard}_{layout}")
            src, host = _guarded_workbook(folder / "lookup.xlsx", guard, layout)
            rep = split.split_cell(src, f"Model!{host}", limit=2)
            assert rep.exit_code == 0 and rep.verified["recalculated"] is True, [f.line() for f in rep.findings]
            assert rep.data["helpers_per_cell"] == 3
            made[(guard, layout)] = (src, Path(rep.data["output"]), host)
        return made[(guard, layout)]

    return get


def _after_change(path: Path, changes: list[tuple[str, str]], refs: list[str]) -> dict[str, object]:
    """One Excel job: the changes typed, a full recalculation, the cells read; nothing saved."""
    from lib import excel
    ops = []
    for cell, v in changes:
        if isinstance(v, str) and v.startswith("="):
            ops.append({"op": "formula", "sheet": "Model", "cell": cell, "formula": v})
        else:
            ops.append({"op": "value", "sheet": "Model", "cell": cell, "value": v})
    res = excel.run_job(path, ops + [{"op": "recalc"}, {"op": "read", "refs": refs}])
    assert res.ok, res.message()
    return {str(i.get("ref")): excel._py_value(i.get("value")) for i in res.step("read").get("values") or []}


@pytest.mark.excel
@pytest.mark.parametrize("guard", GUARDS)
@pytest.mark.parametrize("case", list(SPLIT_CASES))
def test_guarded_lookup_split_keeps_the_result_of_the_original(guarded_splits, guard: str, case: str) -> None:
    from lib import excel
    layout, changes, expected = SPLIT_CASES[case]
    src, out, host = guarded_splits(guard, layout)
    helpers = [f"{split.HELPER_SHEET}!{c}2" for c in "BCD"]
    original = _after_change(src, changes, [f"Model!{host}"])
    after = _after_change(out, changes, [f"Model!{host}"] + helpers)
    want = original[f"Model!{host}"]
    assert want == expected[guard], (case, guard, original)
    assert after[f"Model!{host}"] == want, (case, guard, after)
    # no helper shows an error value that the original's guard hid
    errors = {r: v for r, v in after.items() if r in helpers and excel.is_error(v)}
    assert errors == {} or excel.is_error(want), (case, guard, errors)


# ============================================================================= [A-aera-7] lift: criteria

def _criteria_pairs() -> list[tuple[object, object]]:
    """The pairs of test_v4_check.py::test_criteria_match_follows_excel_countifs (read from that file)."""
    text = (HERE / "test_v4_check.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    node = next(n for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "CRITERIA_PAIRS" for t in n.targets))
    return eval(compile(ast.Expression(node.value), "CRITERIA_PAIRS", "eval"), {"dt": dt})  # noqa: S307


def _runtime() -> dict:
    """The runtime lift copies into every script, run on its own (standard library only)."""
    ns: dict = {"__name__": "rotm_runtime_v5"}
    exec(compile(lift.runtime_source(), "runtime", "exec"), ns)  # noqa: S102 - the plugin's own runtime text
    return ns


def test_runtime_criteria_agree_with_lib_criteria_on_the_measured_pairs() -> None:
    pairs = _criteria_pairs()
    assert len(pairs) >= 88
    ns = _runtime()
    assert "from lib" not in lift.runtime_source() and "import lib" not in lift.runtime_source()
    match = ns["excel_criteria_match"]
    differ = [(c, v, match(c, v), criteria.excel_criteria_match(c, v)) for c, v in pairs
              if match(c, v) != criteria.excel_criteria_match(c, v)]
    assert differ == [], differ
    # every criterion of the pairs against every value of the pairs (empty text left out: see the next test)
    crits = [c for c, _v in pairs if c != ""]
    values = [v for _c, v in pairs if v != ""]
    differ = [(c, v) for c in crits for v in values if match(c, v) != criteria.excel_criteria_match(c, v)]
    assert differ == [], differ[:20]


def test_conditional_functions_follow_the_criteria_rules() -> None:
    codes = Rng.column(["001"] * 60, tall=True)
    assert lift.COUNTIF(codes, "=1") == 60 and lift.COUNTIF(codes, 1) == 60 and lift.COUNTIF(codes, "1") == 60
    assert lift.COUNTIFS(codes, "<>1") == 60                     # <> with a number: a text cell is never equal
    assert lift.COUNTIF(codes, ">0") == 0                        # <, > compare numbers with numbers only
    amounts = Rng.column(list(range(1, 61)), tall=True)
    assert lift.SUMIF(codes, "=1", amounts) == sum(range(1, 61))
    assert lift.SUMIFS(amounts, codes, "1.0") == lift.AVERAGEIFS(amounts, codes, "001") * 60
    assert lift.MAXIFS(amounts, codes, "=1") == 60 and lift.MINIFS(amounts, codes, 1) == 1
    # measured with Excel 16 on 2026-11-25 over (the text 001, an empty cell, a cell ="", the number 0)
    cells = Rng.column(["001", BLANK, "", 0])
    assert [lift.COUNTIF(cells, c) for c in ("", BLANK, "=", "<>", "=1", 1, "1", 0)] == [2, 1, 1, 3, 1, 1, 1, 1]
    # the index path and the test of each cell give what excel_criteria_match gives for each cell
    import random
    rnd = random.Random(25)
    pool = ["001", "1", "1.0", "a", "A", "Apple", "", BLANK, 0, 1, 1.0, 2, True, False, "TRUE", "1,000", 1000,
            dt.date(2024, 1, 31), "2024-01-31", 45322, "x*", XLErr("#N/A")]
    keys = Rng.column([rnd.choice(pool) for _ in range(400)], tall=True)
    vals = Rng.column([rnd.randint(1, 9) for _ in range(400)], tall=True)
    for c in ["001", 1, "1", "=1", "a", "A*", "<>1", ">0", "<=a", "TRUE", True, "=", "<>", "", BLANK, "1,000",
              "2024-01-31", dt.date(2024, 1, 31), "x~*", "?pple", "<>a"]:
        want = sum(v for k, v in zip(keys.flat(), vals.flat()) if lift.excel_criteria_match(c, k))
        assert lift.SUMIFS(vals, keys, c) == want, c
        assert sum(v for k, v in zip(keys.flat(), vals.flat()) if lift._criterion_test(c)(k)) == want, c
        if c not in ("", BLANK):
            lib_want = sum(v for k, v in zip(keys.flat(), vals.flat())
                           if not isinstance(k, XLErr) and criteria.excel_criteria_match(c, None if k is BLANK else k))
            err_rows = sum(v for k, v in zip(keys.flat(), vals.flat()) if isinstance(k, XLErr)
                           and lift.excel_criteria_match(c, k))
            assert want == lib_want + err_rows, c


# ============================================================================= [A-aera-7] lift: references

def _ev(expr: str, values: dict) -> object:
    """Translate a spec expression and compute it as one cell, with each {in:<id>} reading `values`."""
    code = lift.translate(expr, lift._Ctx("check", None))
    ns = dict(vars(lift))
    ns["m"] = SimpleNamespace(inp=lambda iid: values[iid])
    v = lift.cell_value(lambda: eval(code, ns))  # noqa: S307 - code written by the translator under test
    return v.code if isinstance(v, XLErr) else v


# The cells of the Excel battery: a1 the text 001, a2 the number 5, a3 TRUE, a4 the text abc, a6 =1/0, col the
# range of a1 and a2 (A1:A2), keys5 a range holding 5, vals001 a range holding the text 001.
CELLS = {"a1": "001", "a2": 5, "a3": True, "a4": "abc", "a6": XLErr("#DIV/0!"), "col": Rng.column(["001", 5]),
         "keys5": Rng.column([5]), "vals001": Rng.column(["001"])}
EXCEL_REF = {"a1": "A1", "a2": "A2", "a3": "A3", "a4": "A4", "a6": "A6", "col": "A1:A2", "keys5": "F1:F1",
             "vals001": "G1:G1"}
# Measured with Excel 16 on 2026-11-25 (a scratch probe outside the plugin; re-measured by the Excel test below).
AGGREGATE_BATTERY = [
    ("SUM({in:a1})", 0), ('SUM("001")', 1), ("SUM({in:a1},{in:a2})", 5), ("SUM(({in:a1}))", 0),
    ("SUM({in:col})", 5), ("SUM(IF(TRUE,{in:a1}))", 0), ("SUM(IF(TRUE,{in:a1},0))", 0),
    ("SUM(IF(FALSE,0,{in:a1}))", 0), ('SUM(IF(TRUE,"001"))', 1), ("SUM(CHOOSE(1,{in:a1}))", 0),
    ("SUM(INDEX({in:col},1))", 0), ('SUM(INDEX({in:col},1)&"")', 1), ("SUM(IFERROR({in:a1},0))", 1),
    ("SUM(IFNA({in:a1},0))", 1), ("SUM(IFS(TRUE,{in:a1}))", 0), ("SUM(SWITCH(1,1,{in:a1}))", 0),
    ("SUM(XLOOKUP(5,{in:keys5},{in:vals001}))", 0), ('SUM({in:a1}&"")', 1), ("SUM({in:a3})", 0), ("SUM(TRUE)", 1),
    ("SUM(ROUND({in:a1},0))", 1), ("SUM(MAX({in:a1}))", 0),
    ("COUNT({in:a1})", 0), ('COUNT("001")', 1), ("COUNT({in:a3})", 0), ("COUNT(TRUE)", 1),
    ("COUNT(IF(TRUE,{in:a1}))", 0), ("COUNT(IFERROR({in:a1},0))", 1), ("COUNT(INDEX({in:col},1))", 0),
    ("COUNT(CHOOSE(1,{in:a1}))", 0), ("COUNT({in:a6})", 0),
    ("AVERAGE({in:a1},{in:a2})", 5), ('AVERAGE("001",{in:a2})', 3), ("MIN({in:a1},{in:a2})", 5),
    ('MIN("001",{in:a2})', 1), ("MAX({in:a1},{in:a2})", 5), ("PRODUCT({in:a1},{in:a2})", 5), ("MAX({in:a4})", 0),
    ('MAX("abc")', "#VALUE!"), ("MAX({in:a3},{in:a2})", 5), ("MIN({in:a3})", 0), ("AVERAGE({in:a1})", "#DIV/0!"),
    ("NPV(0.1,{in:a1},{in:a2})", 4.545454545454545), ('NPV(0.1,"001",{in:a2})', 5.0413223140495855),
    ("NPV(0.1,{in:a3},{in:a2})", 4.545454545454545), ("NPV(0.1,IF(TRUE,{in:a1}),{in:a2})", 4.545454545454545),
    ("SUM({in:a6})", "#DIV/0!"), ("SUM(IF(TRUE,{in:a6}))", "#DIV/0!"), ("SUM(IF(TRUE,{in:col}))", 5),
    ("SUM(CHOOSE(1,{in:col}))", 5), ("SUM(IFERROR(INDEX({in:col},1),0))", 1),
    ("SUM(IFERROR(IF(TRUE,{in:a1}),0))", 1), ('SUM("abc")', "#VALUE!"), ('SUM(IFERROR(1/0,""))', "#VALUE!"),
    ('SUM("")', "#VALUE!"), ('COUNT("abc")', 0), ('AVERAGE("abc",1)', "#VALUE!"), ('PRODUCT("abc")', "#VALUE!"),
]


def test_aggregates_skip_text_reached_through_a_reference() -> None:
    # the reviewer's case: an amount and a reference to the text input 001
    code = lift.translate("SUM({this:amount},{in:code})", lift._Ctx("helpers", None))
    assert code == "SUM(as_ref(r['amount']), as_ref(m.inp('code')))"
    ns = dict(vars(lift), r={"amount": 655000}, m=SimpleNamespace(inp=lambda iid: "001"))
    assert eval(code, ns) == 655000  # noqa: S307
    assert lift.SUM(655000, "001") == 655001                     # typed in the formula: converted, as Excel does
    wrong = [(expr, want, _ev(expr, CELLS)) for expr, want in AGGREGATE_BATTERY
             if lift.compare_value(want, _ev(expr, CELLS), 1e-12)[0] != "tie"]
    assert wrong == [], wrong


@pytest.mark.parametrize("expr", [
    "SUM(INDEX(IF(TRUE,{in:col},{in:col}),1))", 'SUM(XLOOKUP(5,{in:keys5},{in:vals001},"none"))',
    "COUNT(XLOOKUP(5,{in:keys5},CHOOSE(1,{in:vals001})))", "MAX(XLOOKUP(5,{in:keys5},{in:vals001},{in:a1}&\"\"))"])
def test_a_form_lift_cannot_tell_is_not_reproduced(expr: str) -> None:
    with pytest.raises(lift.Untranslatable, match="cannot tell which"):
        lift.translate(expr, lift._Ctx("check", None))


def test_lift_lists_the_form_as_not_reproduced_before_the_tie_out(tmp_path: Path) -> None:
    p = fixture.write_model(tmp_path / "model")
    spec = common.read_json(p["spec"])
    cols = next(s for s in spec["sheets"] if s["name"] == "h_sales")["columns"]
    cols.append({"id": "pick", "label": "Picked", "format": "amount",
                 "expr": 'SUM(XLOOKUP({this:region},{in:focus},{in:focus},"none"))'})
    common.write_json(p["spec"], spec)
    rep = lift.lift_model(p["root"])
    steps = {s["step"]: s for s in rep.data["steps"]}
    assert steps["h_sales.pick"]["reproduced"] is False and "cannot tell which" in steps["h_sales.pick"]["reason"]
    found = [f for f in rep.findings if f.rule == "L-NOT-REPRODUCED"]
    assert len(found) == 1 and found[0].message.startswith("h_sales.pick is not reproduced: XLOOKUP as an argument "
                                                           "of SUM gives a cell reference or a value")
    assert rep.exit_code == common.EXIT_FINDINGS and "tie_out" not in rep.data


def test_items_row_placeholder_reads_the_line_item_row() -> None:
    """{blk:<items block>.<item>} is the item's row over the block's columns (the form build writes in v5, entry
    A-aera-9 of package P2): lift reads that row, not the block's grand total."""
    assert lift.translate("SUMPRODUCT({blk:bridge.var},{blk:bridge.tgt})", lift._Ctx("check", None)) == \
        "SUMPRODUCT(m.block('bridge').named('var'), m.block('bridge').named('tgt'))"
    model = SimpleNamespace(block_step=lambda b: None)
    b = lift.Block(model, "bridge", "calc", "items", ["Actual", "Variance"], ["Jan", "Feb"],
                   row_keys=["act", "var"])
    b.values = [[10, 20], [1, -2]]
    assert b.named("var").rows == [[1, -2]]
    assert lift.SUMPRODUCT(b.named("act"), b.named("var")) == 10 - 40
    grid = lift.Block(model, "g", "calc", "grid", ["r"], ["c1", "c2"], total_row=True, total_col=True)
    grid.values = [[3, 4]]
    grid.finish()
    assert grid.named("total") == 7


# ============================================================================= [A-aera-7] lift: Excel

@pytest.mark.excel
def test_aggregate_battery_matches_excel(tmp_path: Path) -> None:
    """The battery above re-measured: each expression with its placeholders written as the cells they name."""
    import openpyxl
    from lib import excel
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "p"
    ws["A1"] = "001"
    ws["A1"].data_type = "s"
    ws["A2"], ws["A3"], ws["A4"], ws["A6"], ws["F1"], ws["G1"] = 5, True, "abc", "=1/0", 5, "001"
    ws["G1"].data_type = "s"
    for k, (expr, _want) in enumerate(AGGREGATE_BATTERY, 1):
        text = expr
        for iid, ref in EXCEL_REF.items():
            text = text.replace("{in:" + iid + "}", ref)
        ws[f"D{k}"] = formula.to_file_formula("=" + text)
    path = tmp_path / "battery.xlsx"
    wb.save(path)
    n = len(AGGREGATE_BATTERY)
    res = excel.recalc(path, save=False, read=[f"p!D1:D{n}"])
    assert res.ok, res.errors
    got = [excel._py_value(row[0]) for row in res.values[f"p!D1:D{n}"]]
    wrong = [(expr, x, _ev(expr, CELLS)) for (expr, _w), x in zip(AGGREGATE_BATTERY, got)
             if lift.compare_value(x, _ev(expr, CELLS), 1e-12)[0] != "tie"]
    assert wrong == [], wrong


def _agree(contract: dict) -> dict:
    from lib import modelfolder
    c = {k: v for k, v in contract.items() if k != "agreement"}
    c["agreement"] = {"status": "agreed", "contract_sha256": modelfolder.contract_hash(c), "reply": "ok, agreed",
                      "by": "tester", "at": "2026-11-25T02:00:00+09:00", "channel": "session", "source_file": None,
                      "recorded_by": "test"}
    return c


def _starter(root: Path, *, category: str | None = None, spec_edit=None) -> Path:
    """A copy of the starter template with its contract agreed and its rows landed; `category` replaces every
    line's category (and the input list of categories); `spec_edit` changes the specification. Built."""
    from lib import build, land, modelfolder
    modelfolder.init_model(root, name="starter_v5", template="starter")
    with open(SKILL / "templates" / "starter" / "data" / "lines.csv", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if category is not None:
        rows = [dict(r, category=category) for r in rows]
    path = root / "contracts" / "lines.contract.json"
    c = common.read_json(path)
    c.pop("shown_sha256", None)
    if category is not None:
        next(col for col in c["columns"] if col["name"] == "category")["allowed"] = None
    common.write_json(path, _agree(c))
    src = root.parent / f"{root.name}_extract.csv"
    with open(src, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    rep = land.land_csv(root, "lines", src)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    spec = common.read_json(root / "model.spec.json")
    if category is not None:
        items = next(s for s in spec["sheets"] if s["type"] == "inputs")["items"]
        next(i for i in items if i["id"] == "categories")["values"] = [category]
    if spec_edit is not None:
        spec_edit(spec)
    common.write_json(root / "model.spec.json", spec)
    rep = build.build(root / "model.spec.json", power_query=False)
    assert rep.counts()["errors"] == 0, [f.line() for f in rep.findings]
    return root


def _cells_of(report, step: str) -> list[dict]:
    return [c for c in report.data["tie_out"].get("cells") or [] if c["step"] == step]


@pytest.mark.excel
def test_sum_of_an_amount_and_a_text_input_ties_out(tmp_path: Path) -> None:
    """The reviewer's first case: a helper column SUM({this:amount},{in:code}) with the text input 001."""
    def edit(spec: dict) -> None:
        inputs = next(s for s in spec["sheets"] if s["type"] == "inputs")["items"]
        inputs.append({"id": "code", "label": "Code", "value": "001", "type": "text", "unit": "code",
                       "source": "test"})
        helpers = next(s for s in spec["sheets"] if s["type"] == "helpers")["columns"]
        helpers.append({"id": "amt_code", "label": "Amount and code", "expr": "SUM({this:amount},{in:code})",
                        "format": "amount"})
    root = _starter(tmp_path / "m", spec_edit=edit)
    rep = lift.lift_model(root, run=True)
    assert rep.exit_code == 0, [f.line() for f in rep.findings] + rep.notes
    tie = rep.data["tie_out"]
    assert tie["status"] == "tie" and tie["counts"]["differ"] == 0 and tie["counts"]["not reproduced"] == 0
    step = next(s for s in tie["steps"] if s["step"] == "h_lines.amt_code")
    assert step["cells"] >= 60 and step["differ"] == 0
    from lib import excel
    copy = tmp_path / "recalculated.xlsx"
    shutil.copy2(root / "model.xlsx", copy)
    first = excel.recalc(copy, save=False, read=["h_lines!A1:Z2"])
    assert first.ok, first.errors
    header, row2 = first.values["h_lines!A1:Z2"]
    assert row2[header.index("amt_code")] == row2[header.index("amount")] == 655000   # the text 001 is skipped


@pytest.mark.excel
def test_countif_with_criterion_equal_1_over_text_001_ties_out(tmp_path: Path) -> None:
    """The reviewer's second case: COUNTIF with the criterion =1 over sixty text values 001."""
    def edit(spec: dict) -> None:
        checks = next(s for s in spec["sheets"] if s["type"] == "checks")["checks"]
        checks.append({"id": "code_count", "kind": "equal", "label": "Lines whose category reads as 1",
                       "actual": "COUNTIF({h:lines.category},\"=1\")", "expected": "COUNTA({raw:lines.category})-1"})
    root = _starter(tmp_path / "m", category="001", spec_edit=edit)
    rep = lift.lift_model(root, run=True)
    assert rep.exit_code == 0, [f.line() for f in rep.findings] + rep.notes
    tie = rep.data["tie_out"]
    assert tie["status"] == "tie" and tie["counts"]["differ"] == 0 and tie["counts"]["not reproduced"] == 0
    step = next(s for s in tie["steps"] if s["step"] == "check code_count")
    assert step["cells"] >= 2 and step["differ"] == 0

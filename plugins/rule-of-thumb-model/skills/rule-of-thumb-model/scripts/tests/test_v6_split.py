"""Tests of package P5 in round 6 (v6): split of a part that lies in a branch of IF ([A-aera-10]). Owner: P5 (v6).

- A part in the second or third argument of IF is moved into a helper that carries the IF's condition: the helper
  holds IF(<condition>,<part>,0) for a part from the second argument and IF(<condition>,0,<part>) for a part from the
  third (empty text "" in place of 0 when the part's function always gives text). The helper is then calculated
  only where the IF takes that branch, so it holds no error value where the original showed none.
- When the helper cannot carry the condition within the depth and length limits (the depth limit is 1, the part lies
  in a branch of two or more IFs, the part is a lookup inside IFERROR or IFNA, or the helper is over the length
  limit), split refuses with SPLIT-GUARD: it names the IF and the part, writes nothing and exits 1.
- A part in the first argument of IF (the condition) is moved as before.
Tests marked excel drive Excel through lib.excel.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib import common, formula, split, workbook  # noqa: E402

# the accuracy reviewer's formula (round 5): 0 when A1 is 0, else ABS(SQRT(1/A1)); depth 3
BRANCH = "=IF(A1=0,0,ABS(SQRT(1/A1)))"
# the same with a condition that makes the helper longer than the length limit of 100 characters
LONG_CONDITION = "=IF(A1+A2+A3+A4+A5+A6+A7+A8+A9+A10+A11+A12=0,0,ABS(SQRT(1/A1)))"
IF_FIX = ("apply the pattern of references/helper-patterns.md for a part in a branch of IF by hand (the helper holds "
          "IF(<condition>,<part>,0) or IF(<condition>,0,<part>)), or split this formula by hand; nothing was written")


@pytest.fixture
def no_excel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")


def _rotm(*args: str, excel: bool = True) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if not excel:
        env["ROTM_NO_EXCEL"] = "1"
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=900)


def _texts(plan: split.SplitPlan) -> list[str]:
    return [h for _label, h in plan.helpers]


def _workbook(path: Path, host_formula: str, a1: object = 1) -> Path:
    """Model!A1 holds `a1` (A2:A12 hold 0), Model!B1 holds `host_formula`."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Model"
    ws["A1"] = a1
    for r in range(2, 13):
        ws[f"A{r}"] = 0
    ws["B1"] = host_formula
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def _values_after(path: Path, a1: object, refs: list[str]) -> dict[str, object]:
    """One Excel job: Model!A1 set to `a1`, a full recalculation, `refs` read; nothing saved."""
    from lib import excel
    res = excel.run_job(path, [{"op": "value", "sheet": "Model", "cell": "A1", "value": a1}, {"op": "recalc"},
                               {"op": "read", "refs": refs}])
    assert res.ok, res.message()
    return {str(i.get("ref")): excel._py_value(i.get("value")) for i in res.step("read").get("values") or []}


# ============================================================================= the plan

def test_plan_carries_the_condition_of_the_if_into_the_helper() -> None:
    # a part from the third argument: IF(<condition>,0,<part>)
    plan = split.plan_split(BRANCH, limit=2)
    assert _texts(plan) == ["=IF(A1=0,0,SQRT(1/A1))"]
    assert plan.rewritten == "=IF(A1=0,0,ABS(_rotm_h1))"
    assert plan.parts == ["SQRT(1/A1)"] and plan.depth_after == 2
    assert plan.helpers[0][0] == "helper 1: SQRT(1/A1) where IF(A1=0) gives its argument 3, else 0"
    # a part from the second argument: IF(<condition>,<part>,0)
    plan2 = split.plan_split("=IF(A1<>0,ABS(SQRT(1/A1)),0)", limit=2)
    assert _texts(plan2) == ["=IF(A1<>0,SQRT(1/A1),0)"]
    assert plan2.rewritten == "=IF(A1<>0,ABS(_rotm_h1),0)"
    # the IF deeper in the formula: the condition is carried the same way
    plan3 = split.plan_split("=ROUND(IF(A1=0,0,ABS(1/A1)),2)", limit=2)
    assert _texts(plan3) == ["=IF(A1=0,0,ABS(1/A1))"] and plan3.rewritten == "=ROUND(IF(A1=0,0,_rotm_h1),2)"
    for p in (plan, plan2, plan3):
        assert all(formula.depth(t) <= 2 for t in _texts(p) + [p.rewritten])


def test_plan_carries_empty_text_for_a_part_that_gives_text() -> None:
    plan = split.plan_split('=IF(A1="","",UPPER(TRIM(LEFT(A1,3))))', limit=2)
    assert _texts(plan) == ['=IF(A1="","",LEFT(A1,3))', '=IF(A1="","",TRIM(_rotm_h1))']
    assert plan.rewritten == '=IF(A1="","",UPPER(_rotm_h2))'


def test_plan_splits_a_deep_part_in_a_branch_again_with_the_condition() -> None:
    plan = split.plan_split("=IF(A1=0,0,ROUND(ABS(SQRT(1/A1)),2))", limit=2)
    assert _texts(plan) == ["=IF(A1=0,0,SQRT(1/A1))", "=IF(A1=0,0,ABS(_rotm_h1))"]
    assert plan.rewritten == "=IF(A1=0,0,ROUND(_rotm_h2,2))" and plan.depth_after == 2
    # equal parts in the same branch of the same IF share one helper
    shared = split.plan_split("=IF(A1>0,ABS(SUM(b1:b3)-1)+MAX(SUM(B1:B3),0),0)", limit=2)
    assert _texts(shared) == ["=IF(A1>0,SUM(b1:b3),0)"]


def test_plan_moves_a_part_of_the_condition_as_before() -> None:
    plan = split.plan_split("=IF(ABS(SQRT(A1))>1,1,0)", limit=2)
    assert _texts(plan) == ["=SQRT(A1)"] and plan.rewritten == "=IF(ABS(_rotm_h1)>1,1,0)"
    # a condition at depth 2: its part is moved as before, and the carried condition reads that helper
    plan2 = split.plan_split("=IF(ROUND(ABS(A1),0)=0,0,ABS(SQRT(1/A1)))", limit=2)
    assert _texts(plan2) == ["=ABS(A1)", "=IF(ROUND(_rotm_h1,0)=0,0,SQRT(1/A1))"]
    assert plan2.rewritten == "=IF(ROUND(_rotm_h1,0)=0,0,ABS(_rotm_h2))"
    assert plan2.helpers[1][0] == ("helper 2: SQRT(1/A1) where IF(ROUND(helper 1,0)=0) gives its argument 3, "
                                   "else 0")


@pytest.mark.parametrize("text, limit, part, argument, shown_if, why", [
    ("=IF(A1=0,0,ABS(1/A1))", 1, "ABS(1/A1)", 3, "IF(A1=0,0,ABS(1/A1))", "depth limit 1"),
    ("=IF(A1=0,0,IF(B1>0,ABS(SQRT(1/A1)),0))", 2, "ABS(SQRT(1/A1))", 2, "IF(B1>0,ABS(SQRT(1/A1)),0)",
     "two or more IFs"),
    ('=IF(A2="",0,IFERROR(INDEX(D2:D3,MATCH(A2,C2:C3,0)),0))', 2, "INDEX(D2:D3,MATCH(A2,C2:C3,0))", 3,
     'IF(A2="",0,IFERROR(INDEX(D2:D3,MATCH(A2,C2:C3,0)),0))', "pattern H5"),
], ids=["limit_1", "two_ifs", "lookup_in_a_branch"])
def test_plan_refuses_a_part_whose_condition_cannot_be_carried(text: str, limit: int, part: str, argument: int,
                                                               shown_if: str, why: str) -> None:
    with pytest.raises(split.SplitGuard) as exc:
        split.plan_split(text, limit=limit)
    err = exc.value
    assert err.guard == "IF" and err.part == part and err.argument == argument and err.fix == IF_FIX
    assert err.message("Model!B1") == (f"Model!B1: the part {part} lies in argument {argument} of {shown_if}; in a "
                                       "helper it would be calculated for every input, also where the IF does not "
                                       "take that branch")
    assert why in err.why


# ============================================================================= the command

@pytest.mark.parametrize("excel", [False, pytest.param(True, marks=pytest.mark.excel)], ids=["no_excel", "excel"])
def test_helper_over_the_length_limit_is_refused_and_the_workbook_is_unchanged(tmp_path: Path, excel: bool) -> None:
    src = _workbook(tmp_path / "long.xlsx", LONG_CONDITION)
    before = common.file_sha256(src)
    proc = _rotm("split", str(src), "Model!B1", "--json", "-", excel=excel)
    assert proc.returncode == 1, proc.stdout[-2000:] + proc.stderr[-2000:]
    rep = json.loads(proc.stdout)
    guard = [f for f in rep["findings"] if f["rule"] == "SPLIT-GUARD"]
    assert len(guard) == 1 and guard[0]["severity"] == "error", rep["findings"]
    assert guard[0]["message"] == ("Model!B1: the part SQRT(1/A1) lies in argument 3 of "
                                   "IF(A1+A2+A3+A4+A5+A6+A7+A8+A9+A10+A11+A12=0,0,ABS(SQRT(1/A1))); in a helper it "
                                   "would be calculated for every input, also where the IF does not take that branch")
    assert guard[0]["fix"] == IF_FIX and guard[0]["sheet"] == "Model" and guard[0]["cell"] == "B1"
    assert guard[0]["data"]["argument"] == 3 and "over the length limit 100" in guard[0]["data"]["why"]
    assert not any(f["rule"] == "SPLIT-DONE" for f in rep["findings"])
    assert rep["data"]["written"] is False and not (tmp_path / "long_split.xlsx").exists()
    assert common.file_sha256(src) == before


def test_cell_over_the_length_limit_after_the_split_is_refused(tmp_path: Path, no_excel: None) -> None:
    # 99 characters before the split and 105 after it (ABS(SQRT(1/A1)) becomes ABS(split_helpers!B2))
    long_host = BRANCH + "".join(f"+A{r}" for r in range(2, 13)) * 2
    assert formula.analyze(long_host).length <= 100
    src = _workbook(tmp_path / "host.xlsx", long_host)
    before = common.file_sha256(src)
    rep = split.split_cell(src, "Model!B1")
    guard = [f for f in rep.findings if f.rule == "SPLIT-GUARD"]
    assert rep.exit_code == common.EXIT_FINDINGS and len(guard) == 1, [f.line() for f in rep.findings]
    assert guard[0].message.startswith("Model!B1: the part SQRT(1/A1) lies in argument 3 of IF(A1=0,0,ABS(SQRT(1/A1)))")
    assert "the cell Model!B1 would have length" in guard[0].data["why"]
    assert rep.data["written"] is False and not (tmp_path / "host_split.xlsx").exists()
    assert common.file_sha256(src) == before


def test_branch_split_without_excel_writes_the_condition_into_the_helper(tmp_path: Path, no_excel: None) -> None:
    src = _workbook(tmp_path / "branch.xlsx", BRANCH)
    rep = split.split_cell(src, "Model!B1")
    assert rep.data["written"] is True and rep.exit_code == common.EXIT_NOT_AVAILABLE, [f.line() for f in rep.findings]
    info = workbook.open_workbook(Path(rep.data["output"]), with_values=False)
    assert info.sheet("Model").cell("B1").formula == "=IF(A1=0,0,ABS(split_helpers!B2))"
    assert info.sheet(split.HELPER_SHEET).cell("B2").formula == "=IF(Model!A1=0,0,SQRT(1/Model!A1))"
    assert "where IF(A1=0) gives its argument 3, else 0" in info.sheet(split.HELPER_SHEET).cell("B1").value


@pytest.mark.excel
def test_excel_branch_split_keeps_the_result_in_both_branches(tmp_path: Path) -> None:
    """The reviewer's case: split with A1 = 1; then A1 = 1, 0 and 4: the host equals the original and no helper cell
    holds an error value."""
    src = _workbook(tmp_path / "branch.xlsx", BRANCH, a1=1)
    rep = split.split_cell(src, "Model!B1", limit=2)
    assert rep.exit_code == 0 and rep.verified["recalculated"] is True, [f.line() for f in rep.findings]
    assert any(f.rule == "SPLIT-DONE" for f in rep.findings)
    out = Path(rep.data["output"])
    info = workbook.open_workbook(out, with_values=False)
    helpers = [f"{split.HELPER_SHEET}!{c.coord}" for c in info.sheet(split.HELPER_SHEET).formulas()]
    assert helpers == [f"{split.HELPER_SHEET}!B2"]
    for a1, expected in ((1, 1), (0, 0), (4, 0.5)):
        original = _values_after(src, a1, ["Model!B1"])
        after = _values_after(out, a1, ["Model!B1"] + helpers)
        assert original["Model!B1"] == expected, (a1, original)
        assert after["Model!B1"] == original["Model!B1"], (a1, after)
        from lib import excel
        assert {r: v for r, v in after.items() if excel.is_error(v)} == {}, (a1, after)


@pytest.mark.excel
def test_excel_helper_error_that_another_guard_hid_is_still_split_guard(tmp_path: Path) -> None:
    """The check of the new helper cells after the recalculation stays for the functions other than IF that keep an
    error out of the result (here ISERROR): the helper holds #DIV/0!, the host 1; nothing is written."""
    src = _workbook(tmp_path / "iserror.xlsx", "=--ISERROR(ABS(ROUND(1/A1,0)))", a1=0)
    before = common.file_sha256(src)
    rep = split.split_cell(src, "Model!B1")
    assert rep.verified["recalculated"] is True
    assert rep.exit_code == common.EXIT_FINDINGS, [f.line() for f in rep.findings]
    guard = [f for f in rep.findings if f.rule == "SPLIT-GUARD"]
    assert len(guard) == 1 and guard[0].sheet == split.HELPER_SHEET and guard[0].cell == "B2"
    assert "#DIV/0!" in guard[0].message and "formula's ISERROR hid" in guard[0].message
    assert rep.data["written"] is False and not (tmp_path / "iserror_split.xlsx").exists()
    assert common.file_sha256(src) == before

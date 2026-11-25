"""Tests of package P7 in round 4 (v4): split of a part that sits inside IFERROR or IFNA ([A-aera-14]) and helper
sharing with text literals compared exactly ([A-aera-5]). Owner: P7 (v4)."""
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

LOOKUP = "=IFERROR(INDEX(D2:D3,MATCH(A2,C2:C3,0)),0)"
GUARDED_ROUND = "=IFERROR(ROUND(SUM(A1:A3)/B1,2),0)"


@pytest.fixture
def no_excel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")


def _rotm(*args: str, excel: bool = True) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if not excel:
        env["ROTM_NO_EXCEL"] = "1"
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=900)


def _lookup_workbook(path: Path, key: str = "k2") -> Path:
    """Model!B2 holds the guarded lookup of LOOKUP: the key in A2, the keys in C2:C3, the values in D2:D3."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Model"
    ws["A1"], ws["B1"], ws["C1"], ws["D1"] = "Key", "Value", "Keys", "Values"
    ws["A2"] = key
    ws["B2"] = LOOKUP
    ws["C2"], ws["C3"] = "k1", "k2"
    ws["D2"], ws["D3"] = 10, 20
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def _guarded_round_workbook(path: Path) -> Path:
    """Model!C1 holds GUARDED_ROUND with values that give no error (B1 is 4, not 0)."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Model"
    ws["A1"], ws["A2"], ws["A3"], ws["B1"] = 1, 2, 3, 4
    ws["C1"] = GUARDED_ROUND
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


# ============================================================================ [A-aera-14] the plan

def test_plan_writes_the_missing_key_pattern_for_a_guarded_lookup() -> None:
    plan = split.plan_split(LOOKUP, limit=2)
    # helper 1: the match position or 0; helper 2: a position INDEX can read; helper 3: 1 when the guard replaces
    # the result there (v5, A-aera-5 and C-sol-9); host: the guard's value when helper 1 is 0 or helper 3 is 1
    assert plan.helpers == [("helper 1: MATCH(A2,C2:C3,0)", "=IFERROR(MATCH(A2,C2:C3,0),0)"),
                            ("helper 2: the larger of helper 1 and 1", "=MAX(_rotm_h1,1)"),
                            ("helper 3: 1 when IFERROR replaces INDEX(D2:D3,helper 2), else 0",
                             "=--ISERROR(INDEX(D2:D3,_rotm_h2))")]
    assert plan.rewritten == "=IF(OR(_rotm_h1=0,_rotm_h3=1),0,INDEX(D2:D3,_rotm_h2))"
    assert plan.depth_after == 2
    # IFNA keeps IFNA in helper 1 and tests with ISNA; the file prefix _xlfn. is read as the same function
    ifna = split.plan_split('=_xlfn.IFNA(INDEX($C$2:$C$7,MATCH(B2,$A$2:$A$7,0)),"")', limit=2)
    assert [h for _l, h in ifna.helpers] == ["=IFNA(MATCH(B2,$A$2:$A$7,0),0)", "=MAX(_rotm_h1,1)",
                                             "=--ISNA(INDEX($C$2:$C$7,_rotm_h2))"]
    assert ifna.rewritten == '=IF(OR(_rotm_h1=0,_rotm_h3=1),"",INDEX($C$2:$C$7,_rotm_h2))'


def test_plan_moves_the_whole_pattern_when_the_guard_sits_deeper() -> None:
    # ROUND > IFERROR > INDEX > MATCH: the part at level 3 is the INDEX call; IF > INDEX goes to a helper of its own
    plan = split.plan_split("=ROUND(IFERROR(INDEX(D2:D3,MATCH(A2,C2:C3,0)),0),2)", limit=2)
    assert [h for _l, h in plan.helpers] == ["=IFERROR(MATCH(A2,C2:C3,0),0)", "=MAX(_rotm_h1,1)",
                                             "=--ISERROR(INDEX(D2:D3,_rotm_h2))",
                                             "=IF(OR(_rotm_h1=0,_rotm_h3=1),0,INDEX(D2:D3,_rotm_h2))"]
    assert plan.rewritten == "=ROUND(_rotm_h4,2)"
    assert plan.depth_after == 2


def test_plan_refuses_an_ifna_lookup_inside_iferror() -> None:
    # v5 (A-aera-5, C-sol-9): a helper with IFNA would show the errors the outer IFERROR hid, and a helper with
    # IFERROR would give IFNA's value for them; such a lookup is refused (v4 wrote IFERROR in the helper)
    with pytest.raises(split.SplitGuard) as exc:
        split.plan_split("=IFERROR(ROUND(IFNA(INDEX(D2:D3,MATCH(A2,C2:C3,0)),0),2),0)", limit=4)
    assert exc.value.guard == "IFNA" and exc.value.reason == split.MIXED_REASON.format(lookup="MATCH(A2,C2:C3,0)")
    # with IFNA alone helper 1 keeps IFNA; an IFERROR lookup inside IFNA is rewritten (IFERROR hides every error)
    alone = split.plan_split("=ROUND(IFNA(INDEX(D2:D3,MATCH(A2,C2:C3,0)),0),2)", limit=3)
    assert [h for _l, h in alone.helpers][0] == "=IFNA(MATCH(A2,C2:C3,0),0)"
    inner = split.plan_split("=IFNA(ROUND(IFERROR(INDEX(D2:D3,MATCH(A2,C2:C3,0)),0),2),0)", limit=4)
    assert [h for _l, h in inner.helpers][0] == "=IFERROR(MATCH(A2,C2:C3,0),0)"


@pytest.mark.parametrize("text, guard, part", [
    (GUARDED_ROUND, "IFERROR", "SUM(A1:A3)"),
    ("=IFNA(ROUND(VLOOKUP(A1,B:C,2,0),0),0)", "IFNA", "VLOOKUP(A1,B:C,2,0)"),
    ("=IFERROR(ROUND(INDEX(D2:D3,MATCH(A2,C2:C3,0)),2),0)", "IFERROR", "INDEX(D2:D3,MATCH(A2,C2:C3,0))"),
    ("=IFERROR(INDEX(D2:D3,MATCH(TRIM(A2),C2:C3,0)),0)", "IFERROR", "TRIM(A2)"),
])
def test_plan_refuses_any_other_part_under_a_guard(text: str, guard: str, part: str) -> None:
    with pytest.raises(split.SplitGuard) as exc:
        split.plan_split(text, limit=2)
    assert exc.value.guard == guard and exc.value.part == part


def test_plan_refuses_the_lookup_pattern_over_the_limit() -> None:
    with pytest.raises(split.SplitGuard) as exc:
        split.plan_split(LOOKUP, limit=1)
    assert "H5" in exc.value.reason and "depth limit 1" in exc.value.reason


# ============================================================================ [A-aera-14] the command

@pytest.mark.parametrize("excel", [False, pytest.param(True, marks=pytest.mark.excel)], ids=["no_excel", "excel"])
def test_other_guarded_part_is_refused_and_nothing_is_written(tmp_path: Path, excel: bool) -> None:
    src = _guarded_round_workbook(tmp_path / "round.xlsx")
    before = common.file_sha256(src)
    out = tmp_path / "round_split.xlsx"
    proc = _rotm("split", str(src), "Model!C1", "--json", "-", excel=excel)
    assert proc.returncode == 1, proc.stdout[-2000:] + proc.stderr[-2000:]
    rep = json.loads(proc.stdout)
    guard = [f for f in rep["findings"] if f["rule"] == "SPLIT-GUARD"]
    assert len(guard) == 1 and guard[0]["severity"] == "error"
    assert guard[0]["message"] == ("Model!C1: the part SUM(A1:A3) sits inside IFERROR, and split moves a guarded "
                                   "part only for a lookup (INDEX with MATCH); nothing was written")
    assert guard[0]["fix"] == ("apply the pattern of references/helper-patterns.md H5 by hand, or split this "
                               "formula by hand")
    assert guard[0]["sheet"] == "Model" and guard[0]["cell"] == "C1"
    assert not any(f["rule"] == "SPLIT-DONE" for f in rep["findings"])
    assert rep["data"]["written"] is False and not out.exists()
    assert common.file_sha256(src) == before


def test_guarded_lookup_split_without_excel_writes_the_pattern(tmp_path: Path, no_excel: None) -> None:
    src = _lookup_workbook(tmp_path / "lookup.xlsx")
    rep = split.split_cell(src, "Model!B2")
    assert rep.data["written"] is True, [f.line() for f in rep.findings]
    info = workbook.open_workbook(Path(rep.data["output"]), with_values=False)
    assert info.sheet("Model").cell("B2").formula == ("=IF(OR(split_helpers!B2=0,split_helpers!D2=1),0,"
                                                      "INDEX(D2:D3,split_helpers!C2))")     # v5: three helpers
    assert info.sheet("split_helpers").cell("B2").formula == "=IFERROR(MATCH(Model!A2,Model!C2:C3,0),0)"
    assert info.sheet("split_helpers").cell("C2").formula == "=MAX(B2,1)"
    assert info.sheet("split_helpers").cell("D2").formula == "=--ISERROR(INDEX(Model!D2:D3,C2))"


@pytest.mark.excel
def test_guarded_lookup_split_keeps_the_missing_key_result(tmp_path: Path) -> None:
    from lib import excel
    src = _lookup_workbook(tmp_path / "lookup.xlsx")
    rep = split.split_cell(src, "Model!B2", limit=2)
    assert rep.exit_code == 0 and rep.verified["recalculated"] is True, [f.line() for f in rep.findings]
    out = Path(rep.data["output"])
    info = workbook.open_workbook(out, with_values=False)
    helpers = [c.coord for c in info.sheet("split_helpers").formulas()]
    assert helpers == ["B2", "C2", "D2"]                            # v5: helpers 1 to 3 of the pattern H5
    assert formula.depth(info.sheet("Model").cell("B2").formula) <= 2
    for key, expected in (("k2", 20), ("k1", 10), ("missing", 0)):
        values = {}
        for name, path in (("original", src), ("split", out)):
            # one Excel job per workbook and key: A2 changed, a full recalculation, the cells read; nothing saved
            refs = ["Model!B2"] + ([f"split_helpers!{c}" for c in helpers] if name == "split" else [])
            res = excel.run_job(path, [{"op": "value", "sheet": "Model", "cell": "A2", "value": key},
                                       {"op": "recalc"}, {"op": "read", "refs": refs}])
            assert res.ok, res.message()
            values[name] = {str(i.get("ref")): excel._py_value(i.get("value"))
                            for i in res.step("read").get("values") or []}
        assert values["split"]["Model!B2"] == values["original"]["Model!B2"] == expected, (key, values)
        errors = {r: v for r, v in values["split"].items() if excel.is_error(v)}
        assert errors == {}, (key, errors)
        assert set(values["split"]) == {"Model!B2", "split_helpers!B2", "split_helpers!C2", "split_helpers!D2"}


# ============================================================================ [A-aera-5] text literals

CASES = '=SUM(N(EXACT(A2,"x")),N(EXACT(A2,"X")))'


def test_plan_keeps_text_literals_that_differ_in_case_apart() -> None:
    plan = split.plan_split(CASES, limit=2)
    assert [h for _l, h in plan.helpers] == ['=EXACT(A2,"x")', '=EXACT(A2,"X")']
    assert plan.rewritten == "=SUM(N(_rotm_h1),N(_rotm_h2))"
    # names outside quotes are compared without case: one helper for SUM(b1:b3) and SUM(B1:B3)
    # (v6, A-aera-10: outside a branch of IF, where a part's helper would carry the IF's condition)
    shared = split.plan_split("=ROUND(ABS(SUM(b1:b3)-1),0)+MAX(ROUND(SUM(B1:B3),0),0)", limit=2)
    assert len(shared.helpers) == 1


@pytest.mark.excel
def test_text_literals_that_differ_in_case_get_two_helpers(tmp_path: Path) -> None:
    from openpyxl import Workbook
    from lib import excel
    wb = Workbook()
    ws = wb.active
    ws.title = "Model"
    ws["A2"] = "x"
    ws["B2"] = CASES
    src = tmp_path / "cases.xlsx"
    wb.save(src)
    rep = split.split_cell(src, "Model!B2", limit=2)
    assert rep.exit_code == 0 and rep.verified["recalculated"] is True, [f.line() for f in rep.findings]
    assert rep.data["helpers_per_cell"] == 2
    out = Path(rep.data["output"])
    after = excel.read_cells(out, ["Model!B2"], recalc_first=True)
    before = excel.read_cells(src, ["Model!B2"], recalc_first=True)
    assert after == before == {"Model!B2": 1}

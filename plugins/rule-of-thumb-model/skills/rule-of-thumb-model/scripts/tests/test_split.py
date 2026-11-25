"""Tests for lib/split.py (M7): one over-limit formula rewritten into helper cells; values unchanged after
recalculation in Excel, proved by code (F19, F08a)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "data" / "compat"))

import m7_fixtures as fx7  # noqa: E402

from lib import common, formula, split, workbook  # noqa: E402

SCRIPTS = HERE.parent


@pytest.fixture
def no_excel(monkeypatch):
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")


@pytest.fixture
def deep(tmp_path: Path) -> Path:
    return fx7.deep_cases(tmp_path / "deep.xlsx")


def every_formula_within(path: Path, limit: int = 2, *, block: str | None = None) -> list[str]:
    """Formulas over `limit` on split_helpers and in the split block (other cells of the fixtures are deep)."""
    info = workbook.open_workbook(path, with_values=False)
    out = []
    for s in info.sheets:
        for c in s.formulas():
            inside = s.name == "split_helpers"
            if block:
                sheet, rng = common.parse_ref(block)
                c1, r1, c2, r2 = common.range_bounds(rng)
                inside = inside or (s.name == sheet and c1 <= c.col <= c2 and r1 <= c.row <= r2)
            if inside and c.formula and formula.depth(c.formula) > limit:
                out.append(f"{c.sheet}!{c.coord} {c.formula}")
    return out


# ----------------------------------------------------------------------------- plan_split (the part the audit names)

def test_plan_moves_the_part_the_audit_names():
    f = '=ROUND(INDEX(A:A,MATCH(B1,C:C,0)),0)'
    plan = split.plan_split(f, limit=2)
    audit_part = formula.over_limit_parts(f, 2)[0].text
    assert plan.parts == [audit_part] == ["MATCH(B1,C:C,0)"]
    assert plan.rewritten == '=ROUND(INDEX(A:A,_rotm_h1),0)'
    assert plan.helpers == [("helper 1: MATCH(B1,C:C,0)", "=MATCH(B1,C:C,0)")]
    assert plan.depth_after == 2
    # v4, A-aera-14: the same part inside IFERROR is written with the pattern H5 (a position or 0); v5 (A-aera-5,
    # C-sol-9): the guard also stays on the INDEX part (helpers 2 and 3)
    f = '=IFERROR(INDEX(A:A,MATCH(B1,C:C,0)),"")'
    plan = split.plan_split(f, limit=2)
    assert plan.parts[0] == formula.over_limit_parts(f, 2)[0].text == "MATCH(B1,C:C,0)"
    assert plan.rewritten == '=IF(OR(_rotm_h1=0,_rotm_h3=1),"",INDEX(A:A,_rotm_h2))'
    assert plan.helpers == [("helper 1: MATCH(B1,C:C,0)", "=IFERROR(MATCH(B1,C:C,0),0)"),
                            ("helper 2: the larger of helper 1 and 1", "=MAX(_rotm_h1,1)"),
                            ("helper 3: 1 when IFERROR replaces INDEX(A:A,helper 2), else 0",
                             "=--ISERROR(INDEX(A:A,_rotm_h2))")]
    assert plan.depth_after == 2


def test_plan_splits_a_deep_part_again():
    f = "=ROUND(MAX(MIN(SUM(A1:A9),B1),0),2)"            # depth 4 (design 8.1)
    plan = split.plan_split(f, limit=2)
    assert plan.rewritten == "=ROUND(MAX(_rotm_h1,0),2)"
    assert plan.helpers[0][1] == "=MIN(SUM(A1:A9),B1)" and plan.depth_after == 2
    plan1 = split.plan_split(f, limit=1)
    assert plan1.depth_after == 1 and len(plan1.helpers) == 3
    # calculation order: a helper comes after the helpers it reads
    names = plan1.names
    for k, (_label, text) in enumerate(plan1.helpers):
        for later in names[k:]:
            assert later not in text


def test_plan_shares_one_helper_for_equal_parts():
    # v6 (A-aera-10): parts outside any branch of IF, since a part in a branch carries its IF's condition
    f = "=ROUND(ABS(SUM(B1:B3)-SUM(C1:C3)),0)+MAX(ROUND(SUM(B1:B3),0),0)"
    plan = split.plan_split(f, limit=2)
    texts = [h for _l, h in plan.helpers]
    assert texts.count("=SUM(B1:B3)") == 1 and "=SUM(C1:C3)" in texts
    assert formula.depth(plan.rewritten) <= 2
    # the same part in the second and in the third argument of one IF: two helpers, each with the condition
    branches = split.plan_split("=IF(A1>0,ABS(SUM(B1:B3)-SUM(C1:C3)),MAX(SUM(B1:B3),0))", limit=2)
    assert [h for _l, h in branches.helpers] == ["=IF(A1>0,SUM(B1:B3),0)", "=IF(A1>0,SUM(C1:C3),0)",
                                                 "=IF(A1>0,0,SUM(B1:B3))"]


def test_plan_handles_nested_if_and_long_chains():
    # v6 (A-aera-10): IF(Z1,1,2) lies in a branch of two IFs; a helper carries the condition of one IF only
    f = "=A1+IF(X1,IF(Y1,IF(Z1,1,2),3),4)"
    with pytest.raises(split.SplitBranch) as exc:
        split.plan_split(f, limit=2)
    assert exc.value.if_text == "IF(Y1,IF(Z1,1,2),3)" and exc.value.argument == 2
    deep6 = "=ABS(ROUND(MAX(MIN(SUM(A1:A3),B1),INT(C1)),2))"
    assert split.plan_split(deep6, limit=2).depth_after <= 2


def test_plan_resolve_and_qualify():
    plan = split.plan_split("=IFERROR(INDEX($C$2:$C$7,MATCH(B2,$A$2:$A$7,0)),0)", limit=2, host="D2")
    refs = {"_rotm_h1": "B2", "_rotm_h2": "C2", "_rotm_h3": "D2"}
    host, helpers = plan.resolve(refs, host_refs={k: f"split_helpers!{v}" for k, v in refs.items()})
    # v5 (A-aera-5, C-sol-9): the pattern H5 with the guard on the MATCH part and on the INDEX part
    assert host == ("=IF(OR(split_helpers!B2=0,split_helpers!D2=1),0,INDEX($C$2:$C$7,split_helpers!C2))")
    assert helpers == ["=IFERROR(MATCH(B2,$A$2:$A$7,0),0)", "=MAX(B2,1)", "=--ISERROR(INDEX($C$2:$C$7,C2))"]
    assert split.qualify(helpers[0], "Other Sheet") ==         "=IFERROR(MATCH('Other Sheet'!B2,'Other Sheet'!$A$2:$A$7,0),0)"
    assert split.qualify("=SUM(x!A1,B1,rate)", "calc") == "=SUM(x!A1,calc!B1,rate)"


def test_plan_refusals_name_the_reason():
    with pytest.raises(common.Refusal, match="LET"):
        split.plan_split("=LET(x,SUM(A1:A3),IFERROR(INDEX(B:B,MATCH(x,C:C,0)),0))", limit=2)
    with pytest.raises(common.Refusal, match="argument 1 of SUMIFS"):
        split.plan_split("=IFERROR(SUMIFS(INDEX(A:C,0,MATCH(X1,A1:C1,0)),B:B,1),0)", limit=2)
    with pytest.raises(common.Refusal, match="one end of a range"):
        split.plan_split("=SUM(IF(X1,A1:INDEX(B:B,MATCH(C1,D:D,0)),0))", limit=2)
    with pytest.raises(common.Refusal):
        split.plan_split("=SUM(A1:A3)", limit=0)


# ----------------------------------------------------------------------------- split_cell without Excel

def test_copied_down_column_is_split_together(deep, no_excel):
    before = common.file_sha256(deep)
    rep = split.split_cell(deep, "calc!D4")
    assert common.file_sha256(deep) == before
    out = deep.with_name("deep_split.xlsx")
    assert rep.data["written"] is True and rep.data["output"] == out.as_posix()
    assert rep.data["block"] == "calc!D2:D7" and rep.data["cells"] == 6 and rep.data["helpers_per_cell"] == 3
    assert rep.exit_code == common.EXIT_NOT_AVAILABLE and rep.verified["recalculated"] is False
    info = workbook.open_workbook(out, with_values=False)
    calc, sh = info.sheet("calc"), info.sheet("split_helpers")
    assert info.sheet_names.index("split_helpers") == info.sheet_names.index("calc") + 1
    for r in range(2, 8):
        # v4, A-aera-14: the guarded lookup is written with the pattern H5; v5 (A-aera-5, C-sol-9): three helpers
        assert calc.cell(f"D{r}").formula == (f"=IF(OR(split_helpers!B{r}=0,split_helpers!D{r}=1),0,"
                                              f"INDEX($C$2:$C$7,split_helpers!C{r}))")
        assert sh.cell(f"B{r}").formula == f"=IFERROR(MATCH(calc!B{r},calc!$A$2:$A$7,0),0)"
        assert sh.cell(f"C{r}").formula == f"=MAX(B{r},1)"
        assert sh.cell(f"D{r}").formula == f"=--ISERROR(INDEX(calc!$C$2:$C$7,C{r}))"
    assert "helper 1" in sh.cell("B1").value and "calc!D2:D7" in sh.cell("B1").value
    forms = {formula.relative_form(calc.cell(f"D{r}").formula, f"D{r}") for r in range(2, 8)}
    assert len(forms) == 1                                      # the column stays consistent
    assert every_formula_within(out, block="calc!D2:D7") == []
    assert calc.cell("H2").formula.startswith("=IFERROR(INDEX(")   # the other cells are unchanged


def test_row_copied_across_is_split_together(deep, no_excel):
    rep = split.split_cell(deep, "calc!C10")
    assert rep.data["block"] == "calc!B10:E10" and rep.data["cells"] == 4
    out = Path(rep.data["output"])
    info = workbook.open_workbook(out, with_values=False)
    calc, sh = info.sheet("calc"), info.sheet("split_helpers")
    hosts = [calc.cell(f"{c}10").formula for c in "BCDE"]
    assert hosts[0] == "=IF(ABS(B11-1)>0.5,ROUND(split_helpers!B10,0),0)"    # IF > ROUND > SUM: SUM moves
    assert hosts[3] == "=IF(ABS(E11-1)>0.5,ROUND(split_helpers!E10,0),0)"
    # v6 (A-aera-10): SUM lies in the second argument of IF, so its helper carries the IF's condition
    assert sh.cell("B10").formula == "=IF(ABS(calc!B11-1)>0.5,SUM(calc!B11:B12),0)"
    assert sh.cell("E10").formula == "=IF(ABS(calc!E11-1)>0.5,SUM(calc!E11:E12),0)"
    assert len({formula.relative_form(f, f"{c}10") for f, c in zip(hosts, "BCDE")}) == 1
    assert every_formula_within(out, block="calc!B10:E10") == []


def test_single_cell_and_cell_only(deep, no_excel):
    rep = split.split_cell(deep, "calc!H2")
    assert rep.data["block"] == "calc!H2" and rep.data["cells"] == 1
    rep2 = split.split_cell(deep, "calc!D3", cell_only=True, out=deep.with_name("one.xlsx"))
    assert rep2.data["block"] == "calc!D3" and rep2.data["cells"] == 1
    info = workbook.open_workbook(deep.with_name("one.xlsx"), with_values=False)
    assert info.sheet("calc").cell("D2").formula.startswith("=IFERROR(INDEX($C$2:$C$7,MATCH(")


def test_deep_part_gets_two_levels_of_helpers(deep, no_excel):
    rep = split.split_cell(deep, "calc!F2", limit=1)
    assert rep.data["helpers_per_cell"] == 3
    assert every_formula_within(Path(rep.data["output"]), limit=1, block="calc!F2") == []


def test_host_sheet_with_a_space_and_an_existing_helper_sheet(tmp_path, no_excel):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "My Calc"
    for r in range(1, 5):
        ws.cell(r, 1, r * 2)
        ws.cell(r, 2, f"=ROUND(MAX(MIN(A{r},3),0),0)")
    ws["D1"] = "=IFERROR(INDEX(A1:A4,MATCH(2,A1:A4,0)),0)"
    wb.save(tmp_path / "s.xlsx")
    rep = split.split_cell(tmp_path / "s.xlsx", "'My Calc'!B1")
    out = Path(rep.data["output"])
    info = workbook.open_workbook(out, with_values=False)
    sh = info.sheet("split_helpers")
    assert rep.data["block"] == "My Calc!B1:B4"
    assert sh.cell("B2").formula == "=MIN('My Calc'!A1,3)"        # row 1 holds the header: one row down
    assert info.sheet("My Calc").cell("B1").formula == "=ROUND(MAX(split_helpers!B2,0),0)"
    rep2 = split.split_cell(out, "'My Calc'!D1", in_place=True)
    info2 = workbook.open_workbook(out, with_values=False)
    sh2 = info2.sheet("split_helpers")
    assert sh2.cell("D2").formula == "=IFERROR(MATCH(2,'My Calc'!A1:A4,0),0)"   # after a blank column; H5 (v4)
    assert sh2.cell("B2").formula == "=MIN('My Calc'!A1,3)"          # the first split is kept
    assert Path(rep2.data["backup"]).is_file()


def test_nothing_to_split_writes_nothing(deep, no_excel):
    rep = split.split_cell(deep, "calc!H2", limit=3)
    assert rep.exit_code == 0 and rep.data["written"] is False
    assert any(f.rule == "SPLIT-NONE" for f in rep.findings)
    assert not deep.with_name("deep_split.xlsx").exists()


def test_split_refusals(deep, no_excel):
    with pytest.raises(common.Refusal, match="holds no formula"):
        split.split_cell(deep, "calc!A2")
    with pytest.raises(common.Refusal, match="names no sheet"):
        split.split_cell(deep, "D2")
    with pytest.raises(common.Refusal, match="no sheet"):
        split.split_cell(deep, "nope!D2")
    with pytest.raises(common.Refusal, match="does not hold the same formula"):
        split.split_cell(deep, "calc!D2:F2")
    with pytest.raises(common.Refusal, match="--spec is not available"):
        split.split_cell(deep, "calc!D2", spec="model.spec.json")


def test_fragile_without_excel_refuses(tmp_path, no_excel):
    src = fx7.with_chart_and_names(tmp_path / "chart.xlsx")
    with pytest.raises(common.EnvUnavailable, match="openpyxl would drop"):
        split.split_cell(src, "calc!D2")
    assert not (tmp_path / "chart_split.xlsx").exists()


def test_cli_exit_codes(deep):
    env = dict(os.environ, ROTM_NO_EXCEL="1")
    base = [sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), "split", str(deep)]
    res = subprocess.run(base + ["calc!D2", "--json", "-"], capture_output=True, text=True, encoding="utf-8", env=env)
    assert res.returncode == 3, res.stderr                    # written, values not verified without Excel
    data = json.loads(res.stdout)
    assert data["data"]["block"] == "calc!D2:D7" and data["status"] == "not_verified"
    res = subprocess.run(base + ["calc!A2"], capture_output=True, text=True, encoding="utf-8", env=env)
    assert res.returncode == 2 and "Traceback" not in res.stderr


# ----------------------------------------------------------------------------- with Excel: the proof

@pytest.mark.excel
def test_excel_column_split_keeps_every_value(deep):
    from lib import excel
    rep = split.split_cell(deep, "calc!D2")
    assert rep.exit_code == 0 and rep.verified["recalculated"] is True, [f.line() for f in rep.findings]
    out = Path(rep.data["output"])
    vals_after = excel.read_cells(out, ["calc!D2:D7", "calc!H2"])
    vals_before = excel.read_cells(deep, ["calc!D2:D7", "calc!H2"], recalc_first=True)
    assert vals_after == vals_before
    assert every_formula_within(out, block="calc!D2:D7") == []


@pytest.mark.excel
def test_excel_fragile_workbook_split_through_excel_keeps_parts(tmp_path):
    src = fx7.with_chart_and_names(tmp_path / "chart.xlsx")
    before = workbook.snapshot(src)
    rep = split.split_cell(src, "calc!C10")
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    assert any("through Excel" in n for n in rep.notes)
    after = workbook.snapshot(Path(rep.data["output"]))
    assert after.charts == before.charts and after.names == before.names
    assert {s: after.dv[s] for s in before.dv} == before.dv and {s: after.cf[s] for s in before.cf} == before.cf
    assert "split_helpers" in after.sheets


@pytest.mark.excel
def test_excel_value_change_refuses_and_writes_nothing(tmp_path, monkeypatch):
    # v2 (A-aera-16): a volatile cell's value is left out of the comparison, so RAND no longer forces a change;
    # the helper split writes is made wrong on purpose (+1), which changes the host's value.
    from openpyxl import Workbook
    from lib import compat
    wb = Workbook()
    ws = wb.active
    ws.title = "calc"
    ws["A1"] = "=ROUND(MAX(MIN(A2,1),0),12)"
    ws["A2"] = 0.5
    wb.save(tmp_path / "r.xlsx")
    real = compat.write_edits

    def wrong_helper(src, target, edits, **kw):
        edits = list(edits)
        k = next(i for i, e in enumerate(edits) if e.sheet == split.HELPER_SHEET and e.formula)
        edits[k] = compat.CellEdit(edits[k].sheet, edits[k].cell, formula=edits[k].formula + "+1")
        return real(src, target, edits, **kw)

    monkeypatch.setattr(compat, "write_edits", wrong_helper)
    rep = split.split_cell(tmp_path / "r.xlsx", "calc!A1")
    assert rep.exit_code == common.EXIT_FINDINGS and rep.data["written"] is False
    assert any(f.rule == "W-UNINTENDED" for f in rep.findings)
    assert not (tmp_path / "r_split.xlsx").exists()


FOREIGN = [("Beach Part 5_Complete.xlsx", "'Trial Balance'!D5"),
           ("12 Month Rolling Forecast.xlsx", "'12 Month Rolling Forecast'!C3"),   # v4: SPLIT-GUARD (IFERROR)
           ("12 Month Rolling Forecast.xlsx", None),
           ("DCF Valuation Model (Complete).xlsx", "Model!I178"),
           ("M2V3_end.xlsx", None), ("Precision Model Complete.xlsx", None)]


@pytest.mark.excel
@pytest.mark.slow
@pytest.mark.parametrize("name, ref", FOREIGN)
def test_excel_foreign_copies_split_with_values_proved(name, ref, foreign_dir, tmp_path):
    """F08a for split: copies of workbooks the plugin did not make (one with Power Query, one with charts)."""
    from lib import excel
    if not (foreign_dir / name).is_file():
        pytest.skip(f"{name} is not in {foreign_dir}")
    src = tmp_path / name
    shutil.copy2(foreign_dir / name, src)
    if ref is None:                                   # the first over-limit formula split can move
        info = workbook.open_workbook(src, with_values=False)
        ref = None
        for s in info.sheets:
            for c in s.formulas():
                if c.formula and c.formula_kind in (None, "normal") and formula.depth(c.formula) > 2:
                    try:
                        split.plan_split(c.formula, limit=2)
                    except (common.Refusal, split.SplitGuard):
                        continue
                    ref = f"{common.quote_sheet(s.name)}!{c.coord}"
                    break
            if ref:
                break
        if ref is None:                               # every over-limit formula is refused: the refusal must say why
            deep_cells = [(s.name, c.coord) for s in info.sheets for c in s.formulas()
                          if c.formula and formula.depth(c.formula) > 2]
            if not deep_cells:
                pytest.skip(f"{name} has no over-limit formula")
            try:
                rep = split.split_cell(src, f"{common.quote_sheet(deep_cells[0][0])}!{deep_cells[0][1]}")
            except common.Refusal as exc:
                assert exc.fix and not src.with_name(src.stem + "_split.xlsx").exists()
                return
            guard = [f for f in rep.findings if f.rule == "SPLIT-GUARD"]
            assert guard and guard[0].fix and not src.with_name(src.stem + "_split.xlsx").exists()
            return
    try:
        rep = split.split_cell(src, ref)
    except common.Refusal as exc:                      # a refusal that says why is a pass (F08a)
        assert exc.fix
        return
    guard = [f for f in rep.findings if f.rule == "SPLIT-GUARD"]
    if guard:                                          # v4: a part under IFERROR or IFNA; nothing written
        assert rep.exit_code == common.EXIT_FINDINGS and guard[0].fix and rep.data["written"] is False
        assert not src.with_name(src.stem + "_split.xlsx").exists()
        return
    assert not [f for f in rep.findings if f.rule == "W-UNINTENDED"], [f.line() for f in rep.findings][:5]
    assert rep.exit_code == 0 and rep.verified["recalculated"] is True
    out = Path(rep.data["output"])
    inv_b, inv_a = excel.inventory_counts(excel.inventory(src)), excel.inventory_counts(excel.inventory(out))
    inv_b["sheets"] += 1                               # split_helpers
    assert inv_a == inv_b


@pytest.mark.excel
@pytest.mark.slow
def test_excel_power_query_workbook_keeps_its_queries(foreign_dir, tmp_path):
    """F08a for split on a workbook with Power Query tables: a deep formula is added through Excel, then split."""
    from lib import excel
    name = "1.3-Testing-your-input-data.xlsx"
    if not (foreign_dir / name).is_file():
        pytest.skip(f"{name} is not in {foreign_dir}")
    src = tmp_path / name
    shutil.copy2(foreign_dir / name, src)
    sheet = workbook.open_workbook(src, with_values=False).sheets[0].name
    res = excel.apply_edits(src, [{"op": "formula", "sheet": sheet, "cell": "AA1000",
                                   "formula": "=ROUND(MAX(MIN(SUM(2,3),4),0),0)"}], out=src)
    assert res.ok, res.message()
    inv_before = excel.inventory_counts(excel.inventory(src))
    assert inv_before["queries"] >= 1
    rep = split.split_cell(src, f"{common.quote_sheet(sheet)}!AA1000")
    assert rep.exit_code == 0 and rep.verified["recalculated"] is True, [f.line() for f in rep.findings]
    inv_after = excel.inventory_counts(excel.inventory(Path(rep.data["output"])))
    inv_before["sheets"] += 1
    assert inv_after == inv_before

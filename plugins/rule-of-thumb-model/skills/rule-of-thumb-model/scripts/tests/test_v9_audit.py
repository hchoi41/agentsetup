"""Tests of the v9 audit change (package P5 of the v9 plan). Owner: P5.

Entry [A-aera-11] (r8 verdict), without a manifest: a calculation block also counts as read when a check cell reads
a cell that only sums or links cells of the block: a cell whose formula is SUM(<refs>), SUBTOTAL(9 or 109, <refs>),
references joined by +, or one reference, of the block's cells or of another such cell, followed two cells deep at
most (audit.FOLLOW_LIMIT). No other formula is followed, so a block that reaches a check only through further
calculations is still C-BLOCK. The note gives the count of blocks read through a total or link cell.
Workbooks are written with openpyxl into tmp_path; the shipped starter is audited as a copy. No test needs Excel.
Run from the skill folder: python -P -X utf8 -B -m pytest scripts/tests/test_v9_audit.py -q -p no:cacheprovider
"""
from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from lib import audit, common
from test_audit import base_sheets
from test_v2_audit import SKILL, blocks_of, run, t5_like, two_blocks, write_wb
from test_v3_audit import _with_links

STARTER = SKILL / "templates" / "starter"


def note_of(rep: common.Report) -> str:
    notes = [n for n in rep.notes if n.startswith("inferred calculation blocks: ") and "links left out" not in n]
    assert len(notes) == 1, rep.notes
    return notes[0]


def ranges(rep: common.Report, sheet: str | None = None) -> list[str]:
    return sorted(f.data["range"] for f in blocks_of(rep) if sheet is None or f.sheet == sheet)


# ============================================================================= the forms that are followed

FOLLOWED = ["=SUM(D6:F6)", "=SUM( A1 , B2:B4 )", "=+SUM(A1:A5)", "=SUBTOTAL(9,A1:A5)", "=SUBTOTAL(109,A1:A5,B1:B5)",
            "=A1+B1 + Sheet2!C3", "=+calc!$G$10", "=calc!G10", "=A1:A5", "=SUM(A:A)", "=SUM($5:$7)",
            "='P&L (2026)'!B5", "=SUM('P&L (2026)'!B5:B9)"]
NOT_FOLLOWED = ["=SUM(A1:A5)*1", "=ROUND(SUM(A1:A5),0)", "=(A1+B1)", "=-A1", "=A1+-B1", "=A1-B1", "=A1*2",
                "=SUM(1,A1)", "=SUM(A1,)", "=SUM()", "=SUBTOTAL(1,A1:A5)", "=SUBTOTAL(2,A1:A5)", "=SUBTOTAL(9)",
                "=SUM(A1 B1:B5)", "=SUM(A1:INDEX(A:A,5))", "=SUM(A1)+SUM(B1)", "=SUM(Table1[Amount])",
                "=SUM([1]Sheet1!A1)", "=SUMIFS(A:A,B:B,1)", "=SUMPRODUCT(A1:A5)", "=AGGREGATE(9,6,A1:A5)",
                "=5", '="A1"', "=#REF!"]
NO_NAMES = SimpleNamespace(wb=SimpleNamespace(defined_names=[]))


@pytest.mark.parametrize("text", FOLLOWED)
def test_sums_plus_chains_and_links_are_total_or_link_cells(text: str) -> None:
    assert audit._total_or_link(NO_NAMES, "calc", text) is True


@pytest.mark.parametrize("text", NOT_FOLLOWED)
def test_any_other_formula_is_not_followed(text: str) -> None:
    assert audit._total_or_link(NO_NAMES, "calc", text) is False


def test_a_name_counts_only_when_it_names_one_plain_reference() -> None:
    ctx = SimpleNamespace(wb=SimpleNamespace(defined_names=[
        ("grid_rows", "calc!$G$6:$G$9", None), ("grid_dyn", "OFFSET(calc!$G$6,0,0,4,1)", None),
        ("rate", "0.1", None), ("local_rows", "calc!$G$6:$G$9", "Summary")]))
    assert audit._total_or_link(ctx, "Summary", "=SUM(grid_rows)") is True
    assert audit._total_or_link(ctx, "Summary", "=SUM(local_rows)") is True
    assert audit._total_or_link(ctx, "calc", "=SUM(local_rows)") is False        # scoped to another sheet
    assert audit._total_or_link(ctx, "Summary", "=SUM(grid_dyn)") is False        # a calculation
    assert audit._total_or_link(ctx, "Summary", "=SUM(rate)") is False            # a constant
    assert audit._total_or_link(ctx, "Summary", "=SUM(unknown_name)") is False


# ============================================================================= the shipped starter (the r8 evidence)

def test_starter_without_its_manifest_reads_its_grid_through_the_totals(tmp_path: Path) -> None:
    """The starter's grid calc!D6:F9 has row totals in calc!G6:G9 and the grand total calc!G10; the check
    checks!D13 links to calc!G10. v8 reported calc!D6:F9 and calc!G6:G9 as read by no check (8 C-BLOCK); v9
    follows calc!G10 and calc!G6:G9 and reports neither."""
    p = tmp_path / "starter_model.xlsx"
    shutil.copy2(STARTER / "model.xlsx", p)                   # without model.manifest.json: inferred mode
    rep = run(p)
    assert rep.data["mode"] == "inferred" and rep.data["has_cached_values"] is True
    calc = ranges(rep, "calc")
    assert "D6:F9" not in calc and "G6:G9" not in calc, [f.line() for f in blocks_of(rep)]
    assert calc == ["E5:F5"], [f.line() for f in blocks_of(rep)]            # the month headers: no check reads them
    assert note_of(rep) == ("inferred calculation blocks: 12; 6 read by a check cell (2 of them through a total or "
                            "link cell), 6 read by none (C-BLOCK)")


def test_starter_with_its_manifest_reports_no_block(tmp_path: Path) -> None:
    model = tmp_path / "starter"
    shutil.copytree(STARTER, model, ignore=shutil.ignore_patterns("*.bak_*"))
    rep = run(model / "model.xlsx")
    assert rep.data["mode"] == "manifest"
    assert blocks_of(rep) == [], [f.line() for f in blocks_of(rep)]


# ============================================================================= grids reached or not reached

def _grid(calc: dict[str, Any], top: int, rows: int, form: str) -> None:
    """A grid calc!D<top>:F<top+rows-1> of one formula, row totals in column G and column totals in row top+rows."""
    for i in range(rows):
        r = top + i
        calc[f"B{r}"] = f"Item {i + 1}"
        calc[f"C{r}"] = 10 * (i + 1)
        for col in "DEF":
            calc[f"{col}{r}"] = form.format(col=col, r=r)
        calc[f"G{r}"] = f"=SUM(D{r}:F{r})"
    last = top + rows
    for col in "DEFG":
        calc[f"{col}{last}"] = f"=SUM({col}{top}:{col}{last - 1})"


def four_grids() -> dict[str, dict[str, Any]]:
    """Grid A (rows 6 to 10): a check reads its grand total calc!G10. Grid B (rows 13 to 16): nothing reads it.
    Grid C (rows 19 to 22): a check reads Summary!B2 = ROUND(calc!$G$22,0), a calculation. Grid D (rows 25 to 28):
    a check reads Summary!B4 = B5, Summary!B5 = calc!$G$28 (two links), so calc!G28 is read and the totals G25:G27
    and the grid D25:F27 would be a third and fourth cell deep."""
    calc: dict[str, Any] = {"B1": "Calc", "D5": 1, "E5": 2, "F5": 3}
    _grid(calc, 6, 4, "=$C{r}*{col}$5*Inputs!$B$2")
    _grid(calc, 13, 3, "=$C{r}+{col}$5")
    _grid(calc, 19, 3, "=$C{r}-{col}$5")
    _grid(calc, 25, 3, "=$C{r}*{col}$5")
    return {
        "Inputs": {"A2": "Price", "B2": 25, "A3": "Expected", "B3": 1000},
        "calc": calc,
        "Summary": {"A2": "Rounded total C", "B2": "=ROUND(calc!$G$22,0)", "A4": "Total D", "B4": "=B5",
                    "A5": "Total D again", "B5": "=calc!$G$28"},
        "Checks": {"A1": "Checks", "A2": "Master check", "B2": "=--(SUM(B4:B6)>0)",
                   "A4": "Grid A ties", "B4": "=--(ABS(calc!$G$10-SUM(calc!$C$6:$C$9)*6*Inputs!$B$2)>0)",
                   "A5": "Grid C ties", "B5": "=--(ABS(Summary!$B$2-Inputs!$B$3)>0)",
                   "A6": "Grid D ties", "B6": "=--(ABS(Summary!$B$4-Inputs!$B$3)>0)"},
    }


def test_a_grid_whose_total_a_check_reads_is_read_and_others_are_reported(tmp_path: Path) -> None:
    rep = run(write_wb(tmp_path / "grids.xlsx", four_grids()))
    assert ranges(rep, "calc") == sorted([
        "D13:F15", "G13:G15", "D16:G16",              # grid B: no check reaches it
        "D19:F21", "G19:G21", "D22:G22",              # grid C: reached only through ROUND, a calculation
        "D25:F27", "G25:G27"]), [f.line() for f in blocks_of(rep)]   # grid D: beyond two total or link cells
    by_range = {f.data["range"]: f for f in blocks_of(rep)}
    assert by_range["D13:F15"].cell == "D13" and by_range["D13:F15"].message.endswith("is read by no check cell")
    assert note_of(rep) == ("inferred calculation blocks: 12; 4 read by a check cell (3 of them through a total or "
                            "link cell), 8 read by none (C-BLOCK)")
    assert rep.exit_code == 1


def _one_grid(total_cell: str) -> dict[str, dict[str, Any]]:
    """A grid calc!D6:F9 with row totals calc!G6:G9 and no column totals; a check reads Summary!B2."""
    calc: dict[str, Any] = {"B1": "Calc", "D5": 1, "E5": 2, "F5": 3}
    for r in range(6, 10):
        calc.update({f"B{r}": f"Item {r}", f"C{r}": 10 * r, f"G{r}": f"=SUM(D{r}:F{r})"})
        for col in "DEF":
            calc[f"{col}{r}"] = f"=$C{r}*{col}$5"
    return {"Inputs": {"A2": "Expected", "B2": 1000}, "calc": calc,
            "Summary": {"A2": "Total", "B2": total_cell},
            "Checks": {"A1": "Checks", "A2": "Master check", "B2": "=--(SUM(B4:B5)>0)",
                       "A4": "Grid ties", "B4": "=--(ABS(Summary!$B$2-Inputs!$B$2)>0)",
                       "A5": "Inputs are positive", "B5": "=--(Inputs!$B$2<0)"}}


@pytest.mark.parametrize("total_cell", ["=SUM(calc!G6:G9)", "=SUBTOTAL(9,calc!G6:G9)", "=SUBTOTAL(109,calc!$G$6:$G$9)",
                                        "=calc!G6+calc!G7+calc!G8+calc!G9", "=+calc!G6"])
def test_a_sum_subtotal_plus_chain_or_link_carries_the_read(total_cell: str, tmp_path: Path) -> None:
    rep = run(write_wb(tmp_path / "one.xlsx", _one_grid(total_cell)))
    assert blocks_of(rep) == [], [f.line() for f in blocks_of(rep)]
    assert note_of(rep) == ("inferred calculation blocks: 2; 2 read by a check cell (2 of them through a total or "
                            "link cell), 0 read by none (C-BLOCK)")


@pytest.mark.parametrize("total_cell", ["=SUM(calc!G6:G9)*1", "=ROUND(SUM(calc!G6:G9),0)", "=SUBTOTAL(1,calc!G6:G9)",
                                        "=AGGREGATE(9,6,calc!G6:G9)", "=calc!G6-calc!G7", "=SUMPRODUCT(calc!G6:G9)"])
def test_a_total_through_a_calculation_is_not_followed(total_cell: str, tmp_path: Path) -> None:
    rep = run(write_wb(tmp_path / "calc.xlsx", _one_grid(total_cell)))
    assert ranges(rep) == ["D6:F9", "G6:G9"], [f.line() for f in blocks_of(rep)]
    assert note_of(rep) == ("inferred calculation blocks: 2; 0 read by a check cell (0 of them through a total or "
                            "link cell), 2 read by none (C-BLOCK)")


# ============================================================================= the builder's fixtures

def test_the_t5_like_block_is_still_reported_at_the_same_cell(tmp_path: Path) -> None:
    """Summary!B3 = SUM(Settlement!B2:B6), but no check reads Summary!B3: Settlement!B2:B6 stays C-BLOCK at B2."""
    rep = run(write_wb(tmp_path / "t5.xlsx", t5_like()))
    assert [(f.sheet, f.data["range"], f.cell) for f in blocks_of(rep)] == [("Settlement", "B2:B6", "B2")]
    assert note_of(rep) == ("inferred calculation blocks: 2; 1 read by a check cell (0 of them through a total or "
                            "link cell), 1 read by none (C-BLOCK)")


def test_a_check_on_the_t5_like_summary_total_reads_the_block(tmp_path: Path) -> None:
    sheets = t5_like()
    sheets["Checks"].update({"A6": "Settled total is positive", "B6": "=--(Summary!$B$3<0)"})
    sheets["Checks"]["B2"] = "=--(SUM(B4:B6)>0)"
    rep = run(write_wb(tmp_path / "t5_total.xlsx", sheets))
    assert blocks_of(rep) == [], [f.line() for f in blocks_of(rep)]
    assert note_of(rep) == ("inferred calculation blocks: 2; 2 read by a check cell (1 of them through a total or "
                            "link cell), 0 read by none (C-BLOCK)")


def test_clean_fixtures_and_link_runs_give_no_finding(tmp_path: Path) -> None:
    fixed = t5_like()                                          # the clean twin: a check on the settlement block
    fixed["Checks"].update({"A6": "Settled ties revenue",
                            "B6": "=--(ABS(SUM(Settlement!B2:B6)-Summary!B2*Inputs!$B$3)>0)"})
    fixed["Checks"]["B2"] = "=--(SUM(B4:B6)>0)"
    for name, sheets in (("t5_fixed", fixed), ("each", two_blocks(check_second=True)), ("base", base_sheets())):
        rep = run(write_wb(tmp_path / f"{name}.xlsx", sheets))
        assert blocks_of(rep) == [], (name, [f.line() for f in blocks_of(rep)])
    links = _with_links()                                      # link runs plus the unchecked settlement block
    links["Links"].update({f"E{r}": f"=Settlement!B{r}" for r in range(2, 7)})
    links["Checks"].update({"A6": "Settled through the links", "B6": "=--(SUM(Links!$E$2:$E$6)<0)"})
    links["Checks"]["B2"] = "=--(SUM(B4:B6)>0)"
    rep = run(write_wb(tmp_path / "links.xlsx", links))
    assert blocks_of(rep) == [], [f.line() for f in blocks_of(rep)]
    assert note_of(rep) == ("inferred calculation blocks: 2; 2 read by a check cell (1 of them through a total or "
                            "link cell), 0 read by none (C-BLOCK)")
    assert any(n.startswith("inferred calculation blocks: 4 run(s) of links left out") for n in rep.notes), rep.notes


# ============================================================================= the command skill states the rule

def test_audit_skill_states_the_rule_the_code_applies() -> None:
    path = SKILL.parents[1] / "skills" / "audit" / "SKILL.md"
    if not path.is_file():                              # the skill folder alone passes its tests (v4, A-aera-7)
        pytest.skip("the skill is used without its plugin (PLUGIN/skills/audit/SKILL.md is absent)")
    flat = " ".join(path.read_text(encoding="utf-8").split())
    assert audit.FOLLOW_LIMIT == 2 and audit.SUBTOTAL_SUMS == {9.0, 109.0}
    assert ("A check that reads the block's total cell, or a link to it, counts as reading the block: a cell whose "
            "formula is only `SUM` or `SUBTOTAL` (function 9 or 109) of references, references joined by `+`, or one "
            "reference counts as read with the cells it refers to, at most two such cells deep; no other formula is "
            "followed.") in flat
    assert "read (and how many of them through a total or link cell) and not read" in flat

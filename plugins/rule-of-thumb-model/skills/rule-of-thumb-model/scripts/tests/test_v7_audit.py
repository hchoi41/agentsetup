"""Tests of the v7 changes of package P5 in the audit (round 6 verdict, 2026-11-26). Owner: P5.

- [A-aera-4, A-aera-13], audit side: W-CHECK-GUARD (warning). With a manifest, a registered check whose actual,
  expected (minimum, bound), maximum or result cell on the checks sheet calls IFERROR or IFNA
  (audit._check_guard_rule). Without a manifest, an inferred check cell whose outermost function is IFERROR or
  IFNA, marked "(inferred)" (audit._inferred_guard_rule). An error in an operand must show in the check's result
  (references/checks.md rule 4). The build side (build --strict warns for a check expression) is package P3's.
- [A-aera-11, A-aera-15] part (1): S-CF (warning, with a manifest only): one finding per sheet that holds a
  conditional-format rule, because build writes none (audit._conditional_format_rule). Without a manifest there is
  no S-CF; theme still reports such rules as T-CF.
- The six shipped models (copies of their built model.xlsx with their manifests) get neither finding, and the
  builder's foreign workbooks (BUILD/foreign, ROTM_FOREIGN_DIR) are audited without a failing rule family.

Run from the skill folder: python -P -X utf8 -B -m pytest scripts/tests/test_v7_audit.py -q -p no:cacheprovider
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib import audit, common  # noqa: E402

EXAMPLE = SKILL / "examples" / "sales_vs_target"
SHIPPED_MODELS = [EXAMPLE, SKILL / "templates" / "starter"] + [
    PLUGIN / "templates" / n for n in ("ar_aging", "budget_vs_actual", "price_volume_mix", "rolling_forecast")]
RULE_4 = "references/checks.md rule 4: an error in an operand must show in the check's result"


def _run(path: Path) -> common.Report:
    return audit.audit_workbook(path, strict=True, record=False)


def _hits(rep: common.Report, rule: str) -> list[common.Finding]:
    return [f for f in rep.findings if f.rule == rule]


def _example_copy(tmp_path: Path, name: str = "example") -> Path:
    """A copy of the worked example's model folder (its v6 model.xlsx and manifest), without its reports."""
    folder = tmp_path / name
    shutil.copytree(EXAMPLE, folder, ignore=shutil.ignore_patterns("reports", "*.bak_*"))
    return folder / "model.xlsx"


def _edit(path: Path, edit: Any) -> None:
    import openpyxl
    wb = openpyxl.load_workbook(path)
    edit(wb)
    wb.save(path)


def _row_of(check_id: str) -> int:
    import json
    man = json.loads((EXAMPLE / "model.manifest.json").read_text(encoding="utf-8"))
    ref = next(c["result"] for c in man["checks"] if c["id"] == check_id)
    return int(common.split_cell(ref.split("!")[1])[1])


# ============================================================================= the rule entries

def test_v7_rule_entries_are_listed_with_their_family() -> None:
    """W-CHECK-GUARD in the check family (runs with and without a manifest: needs 'workbook', as C-MISSING and
    C-BLOCK); S-CF in the style family (manifest only); --rules selects each one."""
    guard, cf = audit.RULES["W-CHECK-GUARD"], audit.RULES["S-CF"]
    assert (guard.severity, guard.needs, guard.title) == ("warning", "workbook",
                                                          "check whose operands are wrapped in IFERROR or IFNA")
    assert guard.source == "r6 verdict A-aera-4; references/checks.md rule 4"
    assert (cf.severity, cf.needs, cf.title) == ("warning", "manifest", "conditional-format rule in a built workbook")
    assert cf.source == "r6 verdict A-aera-11; build writes no conditional format"
    families = {name: ids for name, _fn, ids in audit.FAMILIES}
    assert "W-CHECK-GUARD" in families["check"] and "S-CF" in families["style"]
    assert audit._parse_rules("w-check-guard,s-cf") == {"W-CHECK-GUARD", "S-CF"}


# ============================================================================= W-CHECK-GUARD with a manifest

def test_registered_check_with_iferror_on_its_actual_side_is_w_check_guard(tmp_path: Path) -> None:
    """[A-aera-4]: a copy of the worked example whose check actual_ties_raw has the actual cell =IFERROR(<its
    formula>,0): audit --strict exits 1 with W-CHECK-GUARD at that cell, naming the check, the side, IFERROR and
    checks.md rule 4. v6 reported nothing for it (exit 0)."""
    wb_path = _example_copy(tmp_path)
    row = _row_of("actual_ties_raw")

    def guard(wb: Any) -> None:
        c = wb["checks"][f"D{row}"]
        c.value = "=IFERROR(" + str(c.value).lstrip("=") + ",0)"
    _edit(wb_path, guard)
    rep = _run(wb_path)
    hits = _hits(rep, "W-CHECK-GUARD")
    assert rep.exit_code == 1, [f.line() for f in rep.findings]
    assert len(hits) == 1, [f.line() for f in hits]
    f = hits[0]
    assert (f.severity, f.sheet, f.cell) == ("warning", "checks", f"D{row}")
    assert f.message == f"check actual_ties_raw (checks!D{row}): its actual cell holds IFERROR; {RULE_4}"
    assert f.fix.startswith("remove IFERROR or IFNA from the check; for a division that can be by zero, take the "
                            "zero case on the other side, for example --(<denominator><>0)*<numerator>")
    assert f.data["check"] == "actual_ties_raw" and f.data["side"] == "actual"


def test_guarded_expected_and_result_cells_name_their_side(tmp_path: Path) -> None:
    """[A-aera-4]: IFNA on the expected side of one check and IFERROR in the result cell of another are one finding
    each, naming 'expected' and 'result'; IFNA stored with its _xlfn. prefix is found."""
    wb_path = _example_copy(tmp_path)
    r_exp, r_res = _row_of("target_ties_raw"), _row_of("variance_ties")

    def guard(wb: Any) -> None:
        ws = wb["checks"]
        ws[f"E{r_exp}"] = "=_xlfn.IFNA(" + str(ws[f"E{r_exp}"].value).lstrip("=") + ",0)"
        ws[f"I{r_res}"] = "=IFERROR(" + str(ws[f"I{r_res}"].value).lstrip("=") + ",0)"
    _edit(wb_path, guard)
    hits = {f.cell: f for f in _hits(_run(wb_path), "W-CHECK-GUARD")}
    assert set(hits) == {f"E{r_exp}", f"I{r_res}"}, list(hits)
    assert hits[f"E{r_exp}"].message == f"check target_ties_raw (checks!E{r_exp}): its expected cell holds IFNA; {RULE_4}"
    assert hits[f"I{r_res}"].message == f"check variance_ties (checks!I{r_res}): its result cell holds IFERROR; {RULE_4}"


def test_iferror_inside_a_text_literal_is_no_guard(tmp_path: Path) -> None:
    """The word IFERROR inside quotes is text, not a call: no W-CHECK-GUARD."""
    wb_path = _example_copy(tmp_path)
    row = _row_of("cum_ties")

    def text(wb: Any) -> None:
        c = wb["checks"][f"D{row}"]
        c.value = "=" + str(c.value).lstrip("=") + '+LEN("IFERROR(")*0'
    _edit(wb_path, text)
    assert not _hits(_run(wb_path), "W-CHECK-GUARD")


# ============================================================================= W-CHECK-GUARD without a manifest

def _foreign(tmp_path: Path, check_formula: str, name: str = "w.xlsx") -> Path:
    """A workbook the plugin did not make: a check column D labelled Check, a master check, every cell Arial 9."""
    from test_audit import write_wb
    return write_wb(tmp_path / name, {"Checks": {
        "A1": "Item", "B1": "Model", "C1": "Source", "D1": "Check",
        "A2": "Settlement", "B2": 750, "C2": 750, "D2": check_formula,
        "A3": "Fees", "B3": 20, "C3": 20, "D3": check_formula.replace("2", "3"),
        "A5": "Master check", "D5": "=SUM(D2:D3)"}})


def test_inferred_check_cell_with_outer_iferror_is_w_check_guard_inferred(tmp_path: Path) -> None:
    """[A-aera-4]: check cells =IFERROR(IF(ABS(B2-C2)>1,1,0),0) (copied down to row 3): one W-CHECK-GUARD warning
    marked "(inferred)" for both cells, saying that an error of the operands shows as a pass. v6: no such finding."""
    rep = _run(_foreign(tmp_path, "=IFERROR(IF(ABS(B2-C2)>1,1,0),0)"))
    hits = _hits(rep, "W-CHECK-GUARD")
    assert len(hits) == 1, [f.line() for f in hits]
    f = hits[0]
    assert f.message == ("(inferred) Checks!D2 and 1 more cell(s) with the same formula: the check cell's outermost "
                         "function is IFERROR; an error in its operands shows as a pass")
    assert (f.severity, f.sheet, f.count, f.cells) == ("warning", "Checks", 2, ["D2", "D3"])
    assert f.data["inferred"] is True and f.data["fallback"] == "0"
    assert rep.exit_code == 1


def test_inferred_fallback_that_is_no_pass_is_named(tmp_path: Path) -> None:
    """An outer IFNA whose fallback is 1 (a failure): the finding names the fallback value instead of a pass."""
    rep = _run(_foreign(tmp_path, "=IFNA(--(ABS(B2-C2)>1),1)"))
    hits = _hits(rep, "W-CHECK-GUARD")
    assert len(hits) == 1 and hits[0].message.endswith(
        "the check cell's outermost function is IFNA; an error in its operands shows as its fallback value 1, not "
        "as an error"), [f.message for f in hits]


@pytest.mark.parametrize("check_formula", ["=--(ABS(B2-C2)>1)", "=--(ABS(IFERROR(B2,0)-C2)>1)",
                                           '=IF(ABS(B2-C2)>1,"IFERROR(","OK")'])
def test_inferred_check_without_an_outer_guard_gets_none(tmp_path: Path, check_formula: str) -> None:
    """Without a manifest only the outermost function counts: a plain check, an IFERROR inside the operands and the
    word IFERROR inside quotes give no W-CHECK-GUARD."""
    assert not _hits(_run(_foreign(tmp_path, check_formula)), "W-CHECK-GUARD")


# ============================================================================= S-CF

def test_conditional_fill_on_check_results_is_s_cf(tmp_path: Path) -> None:
    """[A-aera-11]: a copy of the worked example with a red conditional fill on checks!I6:I29 (openpyxl): S-CF on
    the checks sheet, one finding, and audit --strict exits 1. v6: exit 0."""
    from openpyxl.formatting.rule import CellIsRule
    from openpyxl.styles import PatternFill
    wb_path = _example_copy(tmp_path)
    _edit(wb_path, lambda wb: wb["checks"].conditional_formatting.add(
        "I6:I29", CellIsRule(operator="equal", formula=["0"], fill=PatternFill("solid", fgColor="FFFF0000"))))
    rep = _run(wb_path)
    hits = _hits(rep, "S-CF")
    assert rep.exit_code == 1
    assert [(f.sheet, f.severity, f.count) for f in hits] == [("checks", "warning", 1)]
    assert hits[0].message == ("sheet checks holds 1 conditional-format rule(s); build writes none, so they were "
                               "added after the build; the audit does not read the colours they draw")
    assert hits[0].fix == ("remove the rules (Home > Conditional Formatting > Clear Rules > Clear Rules from Entire "
                           "Sheet) and rebuild; a check result shows OK or FAIL by its number format")
    assert not [f for f in rep.findings if f.rule == "S-FILL"]          # the stored fills are unchanged


def test_two_sheets_with_rules_give_one_finding_each(tmp_path: Path) -> None:
    from openpyxl.formatting.rule import CellIsRule, FormulaRule
    from openpyxl.styles import PatternFill
    wb_path = _example_copy(tmp_path)
    red = PatternFill("solid", fgColor="FFFF0000")

    def add(wb: Any) -> None:
        wb["calc"].conditional_formatting.add("D7:I10", CellIsRule(operator="lessThan", formula=["0"], fill=red))
        wb["calc"].conditional_formatting.add("D12:I15", FormulaRule(formula=["D12<0"], fill=red))
        wb["summary"].conditional_formatting.add("D7:D10", CellIsRule(operator="lessThan", formula=["0"], fill=red))
    _edit(wb_path, add)
    hits = {f.sheet: f for f in _hits(_run(wb_path), "S-CF")}
    assert set(hits) == {"calc", "summary"}
    assert hits["calc"].data["rules"] == 2 and "holds 2 conditional-format rule(s)" in hits["calc"].message


def test_workbook_without_a_manifest_gets_no_s_cf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a manifest the plugin did not build the workbook: no S-CF (the inferred base of test_audit.py with a
    conditional format); theme still reports the rule as T-CF."""
    from openpyxl.formatting.rule import CellIsRule
    from openpyxl.styles import PatternFill

    from lib import theme
    from test_audit import write_base
    path = write_base(tmp_path / "base.xlsx", edit=lambda wb: wb["calc"].conditional_formatting.add(
        "D9:H9", CellIsRule(operator="lessThan", formula=["0"], fill=PatternFill("solid", fgColor="FFFF0000"))))
    rep = _run(path)
    assert not _hits(rep, "S-CF") and rep.data["mode"] == "inferred"
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    plan = theme.theme_workbook(path, dry_run=True)
    assert "T-CF" in {f.rule for f in plan.findings}


# ============================================================================= shipped models and foreign workbooks

def test_shipped_models_get_no_check_guard_and_no_s_cf(tmp_path: Path) -> None:
    """Copies of the six shipped model folders (their built model.xlsx and manifests): no W-CHECK-GUARD and no S-CF
    (the plugin's templates are skipped when the skill folder is used alone). Their strict exit 0 is tested by
    test_v6_audit.py::test_shipped_models_audit_strict."""
    seen = []
    for src in SHIPPED_MODELS:
        if not (src / "model.xlsx").is_file():
            continue
        folder = tmp_path / src.name
        shutil.copytree(src, folder, ignore=shutil.ignore_patterns("*.bak_*"))
        rep = _run(folder / "model.xlsx")
        assert not [f for f in rep.findings if f.rule in ("W-CHECK-GUARD", "S-CF")], (src.name, [
            f.line() for f in rep.findings if f.rule in ("W-CHECK-GUARD", "S-CF")])
        seen.append(src.name)
    assert "sales_vs_target" in seen and "starter" in seen, seen


def test_foreign_workbooks_are_audited_for_check_guards(foreign_dir: Path, tmp_path: Path,
                                                        capsys: pytest.CaptureFixture[str]) -> None:
    """Copies of the builder's foreign workbooks: the check family runs on each readable one (no A-RULE-FAILED for
    it), no S-CF (they have no manifest), and every W-CHECK-GUARD finding is '(inferred)' and names IFERROR or
    IFNA. The count is printed for the change note."""
    counts: dict[str, int] = {}
    for src in sorted(foreign_dir.glob("*.xls*")):
        copy = tmp_path / src.name
        shutil.copy2(src, copy)
        rep = _run(copy)
        if rep.data.get("mode") == "unreadable":
            continue
        assert not [f for f in rep.findings if f.rule == "A-RULE-FAILED" and f.data.get("family") == "check"], src.name
        assert not _hits(rep, "S-CF"), src.name
        guards = _hits(rep, "W-CHECK-GUARD")
        for f in guards:
            assert f.message.startswith("(inferred) ") and f.data.get("function") in ("IFERROR", "IFNA"), f.line()
        counts[src.name] = sum(f.count for f in guards)
    with capsys.disabled():
        print(f"\nW-CHECK-GUARD on the foreign workbooks: {sum(counts.values())} cell(s) in "
              f"{sum(1 for v in counts.values() if v)} of {len(counts)} readable workbooks: {counts}")
    assert counts

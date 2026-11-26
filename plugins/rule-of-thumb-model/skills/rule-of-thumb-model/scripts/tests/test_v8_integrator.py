"""Tests of the v8 integration (the v8 integrator, 2026-11-26). Owner: the integrator.

Tests that need two packages' changes together (v8 plan section 7):
- I-4 ([A-aera-5, A-aera-6] part (2)): one specification with rounding functions in its checks: build's W-CHECK-ROUND
  (P3) and audit's W-CHECK-ROUND (P4) name the same checks, cells and functions.
- I-5: the six shipped specifications build in strict mode with no finding (no I-TEXT-WIDTH, W-CHECK-GUARD,
  W-CHECK-ROUND or D-CRITERIA).
- [C-sol-10, J-1] part (2), request v8_P3 R1: a block whose rows are given as same_as a block whose rows are values
  (ISO dates) gets General in its row labels from build, as the labels it links to have, and audit's S-NUMFMT
  expects General there (v7 audit side of P4 expected the date format).

Run from the skill folder: python -P -X utf8 -B -m pytest scripts/tests/test_v8_integrator.py -q -p no:cacheprovider
"""
from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest

from lib import audit, build, common, manifest
from test_v5_build import _shipped_folders, copy_model, read_spec, sheet_of, write_spec

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
STARTER = SKILL / "templates" / "starter"


def lines(rep) -> list[str]:
    return [f.line() for f in rep.findings] + list(rep.notes)


def _starter_with(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change) -> Path:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = copy_model(STARTER, tmp_path / "starter", monkeypatch)
    data = read_spec(root)
    change(data)
    write_spec(root, data)
    return root


# ============================================================================= I-4: W-CHECK-ROUND in build and audit

def _rounded(data: dict) -> None:
    sheet_of(data, "checks")["checks"] += [
        {"id": "rounded", "kind": "equal", "label": "Grid total equals the raw total, both rounded",
         "actual": "ROUND({blk:spend.total},0)", "expected": "ROUND(SUM({raw:lines.amount}),0)"},
        {"id": "rounded_int", "kind": "zero", "label": "Whole part of the difference of the two totals",
         "actual": "INT({blk:spend.total}-SUM({raw:lines.amount}))"},
        {"id": "rounded_floor", "kind": "range", "label": "Grid total between the floored and the raw totals",
         "actual": "{blk:spend.total}", "min": "FLOOR.MATH(SUM({raw:lines.amount}))",
         "max": "CEILING.MATH(SUM({raw:lines.amount}))"}]


def test_build_and_audit_name_the_same_w_check_round_checks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _starter_with(tmp_path, monkeypatch, _rounded)
    rep = build.build(root / "model.spec.json", force=True, power_query=False)       # not strict: it writes
    assert rep.counts()["errors"] == 0, lines(rep)
    built = {(f.data["check"], f.sheet, f.cell, f.data["function"]) for f in rep.findings if f.rule == "W-CHECK-ROUND"}
    assert {(c, fn) for c, _s, _cell, fn in built} == {
        ("rounded", "ROUND"), ("rounded_int", "INT"), ("rounded_floor", "FLOOR.MATH"),
        ("rounded_floor", "CEILING.MATH")}, built
    assert len(built) == 5, built                                    # ROUND on both sides of 'rounded'
    aud = audit.audit_workbook(root / "model.xlsx", record=False, rules=["W-CHECK-ROUND"])
    audited = {(f.data["check"], f.sheet, f.cell, f.data["function"]) for f in aud.findings
               if f.rule == "W-CHECK-ROUND"}
    assert audited == built, (sorted(built), sorted(audited))
    # strict: both commands fail on the warning
    assert build.build(root / "model.spec.json", strict=True, force=True, power_query=False).exit_code == 1
    assert audit.audit_workbook(root / "model.xlsx", strict=True, record=False,
                                rules=["W-CHECK-ROUND"]).exit_code == 1


# ============================================================================= I-5: the shipped specifications

@pytest.mark.parametrize("folder", _shipped_folders(), ids=lambda p: p.name)
def test_shipped_specifications_build_strict_with_no_finding(folder: Path, tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    if not (folder / "model.spec.json").is_file():
        pytest.skip(f"{folder.name} is not in this copy (the skill folder copied alone)")
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = copy_model(folder, tmp_path / folder.name, monkeypatch)
    rep = build.build(root / "model.spec.json", strict=True, force=True, power_query=False)
    assert rep.exit_code == 0, lines(rep)
    assert rep.counts() == {"errors": 0, "warnings": 0, "info": 0}, [f.line() for f in rep.findings]


# ============================================================================= same_as of a values rows block

DATES = ["2026-01-31", "2026-02-28", "2026-03-31"]


def _values_blocks(data: dict) -> None:
    """Block by_val: rows given as values (ISO dates); block by_val_copy: rows and cols same_as by_val."""
    sheet_of(data, "calc")["blocks"] += [
        {"id": "by_val", "title": "Expenses by month end", "unit": "KRW", "format": "amount",
         "rows": {"values": DATES, "label": "Month end"},
         "cell": "SUMIFS({h:lines.amount},{h:lines.month_end},{row})", "total": {"row": True}},
        {"id": "by_val_copy", "title": "Expenses by month end, linked", "unit": "KRW", "format": "amount",
         "rows": {"same_as": "by_val", "label": "Month end"}, "cols": {"same_as": "by_val"},
         "cell": "{g:by_val}", "total": {"row": True}}]
    sheet_of(data, "checks")["checks"] += [
        {"id": "by_val_ties", "kind": "equal", "label": "Month-end block total equals the raw total",
         "actual": "{blk:by_val.total}", "expected": "SUM({raw:lines.amount})"},
        {"id": "by_val_copy_ties", "kind": "equal", "label": "Linked month-end block equals the month-end block",
         "actual": "{blk:by_val_copy.total}", "expected": "{blk:by_val.total}"}]


def _label_cells(b: dict) -> list[str]:
    c1, r1, _c2, r2 = common.range_bounds(b["labels"])
    return [common.a1(c1, r) for r in range(r1, r2 + 1)]


def test_same_as_rows_of_a_values_block_take_its_general_format_in_build_and_audit(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _starter_with(tmp_path, monkeypatch, _values_blocks)
    rep = build.build(root / "model.spec.json", strict=True, force=True, power_query=False)
    assert rep.exit_code == 0, lines(rep)
    m = manifest.load_manifest(root / "model.xlsx")
    blocks = {b["id"]: (s["name"], b) for s in m["sheets"] for b in s.get("blocks") or []}
    sheet, src = blocks["by_val"]
    _sheet, linked = blocks["by_val_copy"]
    assert linked["rows_kind"] == "same_as"
    ws = openpyxl.load_workbook(root / "model.xlsx")[sheet]
    pairs = list(zip(_label_cells(src), _label_cells(linked)))
    assert len(pairs) == len(DATES)
    for a, b in pairs:
        assert ws[a].number_format == "General", (a, ws[a].number_format)       # the row_header cell type
        assert ws[b].number_format == ws[a].number_format, (b, ws[b].number_format)
    aud = audit.audit_workbook(root / "model.xlsx", strict=True, record=False)
    linked_cells = {(sheet, b) for _a, b in pairs}
    numfmt = [f.line() for f in aud.findings if f.rule == "S-NUMFMT" and (f.sheet, f.cell) in linked_cells]
    assert not numfmt, numfmt

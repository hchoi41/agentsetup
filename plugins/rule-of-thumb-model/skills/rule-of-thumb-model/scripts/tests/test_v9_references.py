"""v9 tests of the Google Sheets and FAST references and of the density rule (round 8 verdict, package P7).

- [A-aera-14] references/google-sheets.md, "What a refresh does to the formulas that read an extract", step 5:
  <t>_cover or <t>_beyond at FAIL means the helper rows do not end at the last raw row; both at OK with the
  master check at FAIL means another check fails, read on the checks sheet. Still marked not tested in Google
  Sheets.
- [A-aera-18] references/fast-standard-rules.md, "Where the skill departs", item 8, agrees with its row 2.03-05
  and with the dictionary's frozen panes: C2 inside the frozen rows on inputs, calc, output and checks; on cover
  and notes C2 at the top with no frozen rows.
- [A-aera-16] the density rule of SYNTHESIS D18: the 500 is on the cells of the used block, the 60% on its filled
  cells; style-dictionary.md, style_dictionary.json, lib/classify.py and the message theme prints say the same.
- [A-aera-2, C-sol-1] part (6), text G-1: google-sheets.md "Models kept in Google Sheets" step 4 states the order
  of control totals and what to do after a new agreement.
Owner: P7 (v9). No test needs Excel.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from lib import classify, common, dictionary, theme, workbook

SKILL = Path(__file__).resolve().parents[2]
REFS = SKILL / "references"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """The text under a '## ' heading, up to the next '## ' heading."""
    start = text.index(f"\n## {heading}\n")
    end = text.find("\n## ", start + 1)
    return text[start:end if end != -1 else len(text)]


def _plain(text: str) -> str:
    """Text without code-span backticks and with runs of white space as one space."""
    return re.sub(r"\s+", " ", text.replace("`", "")).strip()


# ----------------------------------------------------------------------------- [A-aera-14] google-sheets.md step 5

def _refresh_step5() -> str:
    sec = _section(_read(REFS / "google-sheets.md"), "What a refresh does to the formulas that read an extract")
    start = sec.index("  5. To repair the helper rows in Google Sheets")
    end = sec.index("\n- Whether whole-column INDEX", start)
    return sec[start:end]


def test_refresh_repair_tells_row_alignment_from_another_failing_check() -> None:
    step = _refresh_step5()
    plain = _plain(step)
    assert "If one shows FAIL, the helper rows do not end at the last raw row yet." not in step   # the v8 text
    assert ("When <t>_cover or <t>_beyond shows FAIL, the helper rows do not end at the last raw row yet"
            in plain)
    case2 = plain[plain.index("When <t>_cover and <t>_beyond both show OK and the master check shows FAIL"):]
    for word in ("another check fails", "checks sheet", "Result (column I) shows FAIL", "its id (column B, Id)",
                 "column D, Actual", "column E, Expected or minimum", "its source",
                 "the sheet or range it checks (column J, Checks)"):
        assert word in case2, word
    # the passage stays marked as not tested in Google Sheets
    assert "The three procedures are not tested in Google Sheets." in plain
    assert "This reading is not tested in Google Sheets." in plain


def test_refresh_repair_names_the_checks_sheet_columns_build_writes() -> None:
    """The column letters the step names are those of build's checks sheet (B id ... I result, J the range)."""
    from lib import build
    assert build.CHECK_HEADERS[0] == "Id" and build.CHECK_HEADERS[2] == "Actual"
    assert build.CHECK_HEADERS[3] == "Expected or minimum" and build.CHECK_HEADERS[7] == "Result"
    assert build.CHECK_HEADERS[8] == "Checks"
    checks = _plain(_read(REFS / "checks.md"))
    assert ("Columns: B id, C what it checks, D actual, E expected or minimum, F maximum, G difference, "
            "H tolerance, I result, J the sheet or range it checks.") in checks


# ----------------------------------------------------------------------------- [A-aera-18] fast-standard-rules.md item 8

def _frozen_rows(freeze: str | None) -> int:
    if not freeze:
        return 0
    _col, row = common.split_cell(freeze)
    return row - 1


def test_fast_item_8_agrees_with_row_2_03_05_and_the_frozen_panes() -> None:
    text = _read(REFS / "fast-standard-rules.md")
    departs = _section(text, "Where the skill departs from FAST, and why")
    item8 = _plain(next(line for line in departs.splitlines() if line.startswith("8. ")))
    assert "every other sheet shows it in C2, inside the frozen rows" not in item8                  # the v8 text
    assert ("On the inputs, calc, output and checks sheets the master check is in C2, inside the frozen rows, "
            "so it stays in view.") in item8
    assert ("On the cover and notes sheets it is in C2 at the top of the sheet, with no frozen rows, so it "
            "scrolls out of view") in item8
    row = _plain(next(line for line in text.splitlines() if line.startswith("| 2.03-05 |")))
    assert "inside the frozen rows on the inputs, calc, output and checks sheets" in row
    assert "the cover and notes sheets have no frozen rows" in row
    # the dictionary's frozen panes: row 2 frozen on the four sheet types item 8 names, none on cover and notes
    data = json.loads(_read(REFS / "style_dictionary.json"))
    freeze = {name: st["freeze"]["value"] for name, st in data["sheet_types"].items()}
    block_types = [t for t in classify.SHEET_TYPES if t not in ("raw", "helpers")]
    assert {t for t in block_types if _frozen_rows(freeze[t]) >= 2} == {"inputs", "calc", "output", "checks"}
    assert {t for t in block_types if _frozen_rows(freeze[t]) == 0} == {"cover", "notes"}


# ----------------------------------------------------------------------------- G-1: google-sheets.md, models kept there

def test_export_route_states_the_order_of_control_totals_and_the_new_agreement() -> None:
    sec = _section(_read(REFS / "google-sheets.md"), "Models kept in Google Sheets")
    step4 = sec[sec.index("\n4. A refresh made in Google Sheets"):sec.index("\n5. Without the model folder")]
    plain = _plain(step4)
    assert "This passage is not tested in Google Sheets" in plain
    assert "(the contract's, or the total recorded for exactly these rows in" not in step4           # the v8 text
    assert ("When the source's total did not change, leave out --control: land compares the export with the "
            "control total in force (the order: --control of this run; else the total accepted for these rows "
            "in raw/<t>.controls.json under the agreement in force; else the agreed contract's total).") in plain
    assert ("After the contract was agreed again (for example with another control total), give --control with "
            "the source's current total: a total accepted under an earlier agreement is not used, and land "
            "reports L-TOTAL with both totals when the rows do not meet the contract's total. --control is "
            "accepted only for a column whose control total the contract names.") in plain


# ----------------------------------------------------------------------------- [A-aera-16] the density rule

def _boundary_sheet(name: str, rows: int, cols: int, filled: int) -> workbook.SheetInfo:
    sheet = workbook.SheetInfo(name=name, index=0, state="visible", max_row=rows, max_col=cols, freeze=None,
                               gridlines=True, zoom=None, merged=[], tables=[], cf_count=0, dv_count=0)
    order = [(r, c) for r in range(1, rows + 1) for c in range(1, cols + 1)]
    for r, c in order[:filled - 1] + [order[-1]]:
        coord = f"{common.col_letter(c)}{r}"
        sheet.cells[(r, c)] = workbook.CellInfo(sheet=name, coord=coord, row=r, col=c, value=r * 100 + c,
                                                formula=None, cached=r * 100 + c, data_type="n")
    return sheet


EXAMPLES = ((20, 25, 300, True), (20, 25, 299, False), (499, 1, 300, False))


def test_density_rule_is_stated_the_same_in_dictionary_reference_and_code() -> None:
    data = json.loads(_read(REFS / "style_dictionary.json"))
    rules = data["rules"]
    assert rules["dense_fill_ratio"]["value"] == classify.DENSE_FILL_RATIO == 0.6
    assert rules["dense_min_cells"]["value"] == classify.DENSE_MIN_CELLS == 500
    assert "used block holds at least this many cells, filled or empty" in rules["dense_min_cells"]["source"]
    assert "SYNTHESIS D18" in rules["dense_min_cells"]["source"]
    md = _read(REFS / "style-dictionary.md")
    assert "500 filled cells" not in md                                                         # the v8 rule
    assert md.count("its used block holds at least 500 cells, filled or empty, and at least 60% of them are "
                    "filled") == 2                                                              # table and section 6
    assert ("Example: 300 filled cells in a block of 500 cells (60%) is dense; 299 filled cells in it is not; "
            "300 filled cells in a block of 499 cells is not.") in md
    doc = _plain(classify.guess_sheet_type.__doc__ or "")
    assert ("holds at least dense_min_cells cells, filled or empty, of which at least dense_fill_ratio are "
            "filled") in doc
    d = dictionary.load_dictionary()
    for rows, cols, filled, dense in EXAMPLES:
        assert (classify.guess_sheet_type(_boundary_sheet("Block", rows, cols, filled), d) == "calc") is dense


def test_theme_prints_the_density_measure_at_the_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The message theme prints for each sheet (the note 'sizes: ...' and the JSON field reason)."""
    import openpyxl
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    names = ("Wall", "Spare", "Narrow")
    for name, (rows, cols, filled, _dense) in zip(names, EXAMPLES):
        ws = wb.create_sheet(name)
        order = [(r, c) for r in range(1, rows + 1) for c in range(1, cols + 1)]
        for r, c in order[:filled - 1] + [order[-1]]:
            ws.cell(r, c, r * 100 + c)
    path = tmp_path / "boundary.xlsx"
    wb.save(path)
    before = path.read_bytes()
    report = theme.theme_workbook(path, dry_run=True)
    assert path.read_bytes() == before
    sheets = report.data["sheets"]
    assert (sheets["Wall"]["size"], sheets["Spare"]["size"], sheets["Narrow"]["size"]) == (8, 9, 9)
    wall = ("dense: 300 filled cells in a block of 500 cells (60.0%), at least 60% filled and a block of at least "
            "500 cells")
    assert sheets["Wall"]["reason"] == wall
    assert sheets["Spare"]["reason"] == "not dense: 299 filled cells in a block of 500 cells (59.8%)"
    assert sheets["Narrow"]["reason"] == "not dense: 300 filled cells in a block of 499 cells (60.1%)"
    sizes = next(n for n in report.notes if n.startswith("sizes: "))
    assert f"Wall 8 ({wall})" in sizes and "Narrow 9 (not dense: 300 filled cells in a block of 499 cells" in sizes
    # style-dictionary.md quotes the same two messages
    md = _read(REFS / "style-dictionary.md")
    assert f"`{wall}`" in md and f"`{sheets['Spare']['reason']}`" in md

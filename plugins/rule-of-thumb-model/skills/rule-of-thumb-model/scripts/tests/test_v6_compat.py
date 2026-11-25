"""v6 tests of package P6 (compat and the Google Sheets reference), round 5 verdict.

- [A-aera-9, A-aera-16, J-2] (1): FILTER is marked as differing between the products in its third argument
  (lib/functions.py D_FILTER); `rotm.py compat` reports an Excel FILTER with a third argument at its cell
  (X-FILTER, warning) and does not rewrite it; a FILTER with two arguments gets no X-FILTER.
- [A-aera-9, A-aera-16, J-2] (2): google-sheets.md, the row "Excel tables on import" and the list of "Converting a
  workbook from Excel to Google Sheets", and compat's X-TABLEREF message say the same thing: the import of Excel
  tables as Sheets tables is the confirmed announcement of 11 August 2026; table styles, structured references in
  formulas and connected pivots stay not confirmed.
- [A-aera-9, A-aera-16, J-2] (3): google-sheets.md, the row "Dates": the Sheets sentence cites platforms A3.8, and
  the section "Sources" has its row.
- Supporting texts T-6 and T-7 ([C-sol-3]) and T-8 ([C-sol-6, J-1]) of BUILD/v6_plan.md section 5.4.

No test here needs Excel (ROTM_NO_EXCEL=1 for every command).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from lib import compat, functions as FN

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
REFS = SKILL / "references"
ROTM = SCRIPTS / "rotm.py"
HAS_PLUGIN = (PLUGIN / ".claude-plugin" / "plugin.json").is_file()

FILTER_3 = '=_xlfn._xlws.FILTER(A2:A3,B2:B3="South","none")'
FILTER_2 = '=_xlfn._xlws.FILTER(A2:A3,B2:B3="South")'
CONFIRMED_IMPORT = "the import of Excel tables as Sheets tables is the confirmed announcement of 11 August 2026"
OLD_TABLE_WORDS = ("stays a table", "whether an Excel table", "Excel tables, and notes")


def _rotm(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "ROTM_NO_EXCEL": "1"}
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(ROTM), *args], capture_output=True,
                          text=True, encoding="utf-8", timeout=600, env=env)


def _json_of(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    assert "Traceback" not in proc.stderr, proc.stderr[-800:]
    return json.loads(proc.stdout)


def _book(path: Path, formula: str | None, *, array: bool = False, table: bool = False) -> Path:
    import openpyxl
    from openpyxl.worksheet.formula import ArrayFormula
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "calc"
    ws["A1"], ws["B1"] = "Amount", "Region"
    ws["A2"], ws["B2"], ws["A3"], ws["B3"] = 1, "North", 2, "West"
    if table:
        from openpyxl.worksheet.table import Table
        ws.add_table(Table(displayName="sales", ref="A1:B3"))
    if formula is not None:
        ws["D2"] = ArrayFormula(ref="D2", text=formula) if array else formula
    wb.save(path)
    return path


def _hits(data: dict[str, Any], rule: str) -> list[dict[str, Any]]:
    return [f for f in data["findings"] if f["rule"] == rule]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return " ".join(text.split())


def _section(text: str, heading: str) -> str:
    level = len(heading) - len(heading.lstrip("#"))
    start = text.index(heading)
    rest = text[start + len(heading):]
    m = re.search(r"^#{1,%d} " % level, rest, flags=re.M)
    return heading + (rest[:m.start()] if m else rest)


def _no_code(text: str) -> str:
    return text.replace("`", "")


# ============================================================================= (1) FILTER's third argument

def test_filter_with_a_third_argument_is_reported_by_report_only_strict(tmp_path: Path) -> None:
    src = _book(tmp_path / "filter3.xlsx", FILTER_3)
    proc = _rotm("compat", str(src), "--report-only", "--strict", "--json", "-", "--quiet")
    data = _json_of(proc)
    hits = _hits(data, "X-FILTER")
    assert proc.returncode == 1, data["findings"]
    assert [(f["severity"], f["sheet"], f["cell"]) for f in hits] == [("warning", "calc", "D2")], hits
    assert hits[0]["message"] == ('FILTER with 3 arguments (third argument: "none"): in Excel the third argument '
                                  "is the value returned when no row matches; in Google Sheets it is a further "
                                  "condition")
    assert hits[0]["fix"].startswith("compat does not rewrite it")
    assert data["data"]["replaced_count"] == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["filter3.xlsx"]


def test_filter_with_a_third_argument_is_not_rewritten_and_strict_exits_1(tmp_path: Path) -> None:
    src = _book(tmp_path / "filter3.xlsx", FILTER_3)
    before = src.read_bytes()
    rep = compat.compat_workbook(src, strict=True)
    assert [f.rule for f in rep.findings if f.rule == "X-FILTER"] == ["X-FILTER"]
    assert rep.exit_code == 1
    assert rep.data["replaced_count"] == 0 and rep.data["written"] is False
    assert not (tmp_path / "filter3_compat.xlsx").exists() and src.read_bytes() == before


def test_filter_with_two_arguments_gets_no_finding_of_this_rule(tmp_path: Path) -> None:
    src = _book(tmp_path / "filter2.xlsx", FILTER_2)
    proc = _rotm("compat", str(src), "--report-only", "--strict", "--json", "-", "--quiet")
    data = _json_of(proc)
    assert not _hits(data, "X-FILTER"), data["findings"]
    assert proc.returncode == 0, data["findings"]          # the X-FUNC of FILTER (a recent Excel) is information


@pytest.mark.parametrize("text, expected", [
    ('=FILTER(A2:A3,B2:B3="South","none")', 1),
    ('=_xlfn._xlws.FILTER(A2:A3,B2:B3="South",)', 1),                    # an empty third argument counts
    ('=SUM(_xlfn._xlws.FILTER(A2:A3,B2:B3="South",0))', 1),              # inside another call
    ('=_xlfn._xlws.FILTER(A2:A3,B2:B3="South",C2:C3>0,D2:D3>0)', 1),     # the Google Sheets form, 4 arguments
    ('=_xlfn._xlws.FILTER(A2:A3,B2:B3="South")', 0),
    ('=_xlfn._xlws.SORT(A2:A3)', 0),                                     # SORT: no finding (change note)
])
def test_x_filter_counts_the_arguments_of_each_filter_call(text: str, expected: int) -> None:
    new, done, problems = compat.plan_formula(text, sheet="calc", row=2, col=4, tables=[], product="both")
    assert new is None or "X-FILTER" not in done
    assert [p[1] for p in problems if p[0] == "X-FILTER"] == ["warning"] * expected, problems


def test_x_filter_is_not_reported_for_the_excel_profile_and_is_for_sheets() -> None:
    _new, _done, excel = compat.plan_formula(FILTER_3, sheet="calc", row=2, col=4, tables=[], product="excel")
    _new, _done, sheets = compat.plan_formula(FILTER_3, sheet="calc", row=2, col=4, tables=[], product="sheets")
    assert not [p for p in excel if p[0] == "X-FILTER"]
    assert [p[0] for p in sheets if p[0] == "X-FILTER"] == ["X-FILTER"]


def test_x_filter_is_reported_in_an_array_formula_cell(tmp_path: Path) -> None:
    src = _book(tmp_path / "array.xlsx", FILTER_3, array=True)
    rep = compat.compat_workbook(src, report_only=True, strict=True)
    assert [(f.rule, f.cell) for f in rep.findings if f.rule == "X-FILTER"] == [("X-FILTER", "D2")]
    assert rep.exit_code == 1


def test_catalogue_marks_filter_as_differing_in_its_third_argument() -> None:
    e = FN.CATALOGUE["FILTER"]
    assert e.cls == "flagged" and e.sheets == "yes"
    assert e.differs.startswith("differs between the products in its third argument")
    assert "value returned when no row matches" in e.differs and "further condition" in e.differs
    assert "X-FILTER" in e.differs and e.differs in e.reason
    assert "CONCAT" in [n for n, x in FN.CATALOGUE.items() if x.differs]
    text = _read(REFS / "functions.md")
    row = next(line for line in text.splitlines() if line.startswith("| FILTER |"))
    assert "further condition" in row
    section = text.split("## Functions that differ between the products", 1)[1].split("\n## ", 1)[0]
    assert "- FILTER: differs between the products in its third argument" in section
    assert "X-FILTER" in section and "- CONCAT: " in section


def test_x_filter_is_named_where_compat_is_described() -> None:
    gs = _flat(_read(REFS / "google-sheets.md"))
    assert ("An Excel `FILTER` with a third argument is reported at its cell (`X-FILTER`, a warning) and not "
            "rewritten") in gs
    if not HAS_PLUGIN:
        pytest.skip("the skill is used without its plugin (PLUGIN/skills/compat/SKILL.md is absent)")
    skill = _flat(_read(PLUGIN / "skills" / "compat" / "SKILL.md"))
    assert "An Excel `FILTER` with a third argument is `X-FILTER` (a warning) at its cell" in skill
    assert "A `FILTER` with two arguments gets no `X-FILTER`" in skill


# ============================================================================= (2) Excel tables on import

def test_table_reference_message_holds_the_confirmed_import(tmp_path: Path) -> None:
    src = _book(tmp_path / "tableref.xlsx", "=SUM(sales[Amount])", table=True)
    proc = _rotm("compat", str(src), "--report-only", "--strict", "--json", "-", "--quiet")
    data = _json_of(proc)
    hits = _hits(data, "X-TABLEREF")
    assert [(f["severity"], f["cell"]) for f in hits] == [("info", "D2")], data["findings"]
    message = hits[0]["message"]
    assert CONFIRMED_IMPORT in message and "researcher 2 #17, confirmed" in message
    assert ("not confirmed, item by item: whether the table styles come through, whether structured references "
            "in formulas keep working after the import, and whether a connected pivot table stays connected to "
            "its table") in message
    assert not [w for w in OLD_TABLE_WORDS if w in message], message
    assert message == compat.TABLEREF_MESSAGE


def test_google_sheets_row_and_list_say_the_same_about_excel_tables() -> None:
    text = _read(REFS / "google-sheets.md")
    row = next(line for line in text.splitlines() if line.startswith("| Excel tables on import |"))
    conversion = _flat(_section(text, "## Converting a workbook from Excel to Google Sheets"))
    items = ("Not confirmed, item by item: whether the table styles come through, whether structured references "
             "in formulas (such as `Table1[Column]`) keep working after the import, and whether a connected pivot "
             "table stays connected to its table (platforms E5, not confirmed)")
    for where, part in (("the row", row), ("the list", conversion)):
        assert CONFIRMED_IMPORT in part, where
        assert "researcher 2 #17, confirmed" in part, where
        assert items in part, where
        assert "gradual rollout" in part, where
    not_confirmed = next(line for line in conversion.split("- ") if line.startswith("Not confirmed: how Arial"))
    assert "Excel tables" not in not_confirmed, not_confirmed
    for name in ("google-sheets.md", "functions.md"):
        assert not [w for w in OLD_TABLE_WORDS[:2] if w in _read(REFS / name)], name
    if HAS_PLUGIN:
        skill = _read(PLUGIN / "skills" / "compat" / "SKILL.md")
        assert not [w for w in OLD_TABLE_WORDS[:2] if w in skill]
        assert "confirmed announcement of 11 August 2026" in _flat(skill)


# ============================================================================= (3) J-2: the Dates row

def test_dates_row_cites_a3_8_and_the_sources_row_matches() -> None:
    text = _read(REFS / "google-sheets.md")
    row = next(line for line in text.splitlines() if line.startswith("| Dates |"))
    cells = [c.strip() for c in row.strip().strip("|").split(" | ")]
    sheets, excel = cells[1], cells[2]
    assert sheets.startswith('Sheets "uses the 1900 date system" and counts days since 30 December 1899')
    assert "(platforms A3.8; confirmed by the claim check of platforms A3.9, which also read this Google page)" \
        in sheets
    assert "(platforms A3.9, confirmed)" in excel and "A3.8" not in excel
    sources = text.split("\n## Sources\n", 1)[1]
    a38 = next(line for line in sources.splitlines() if line.startswith("| platforms A3.8 |"))
    assert a38 == ("| platforms A3.8 | Google Docs Editors Help | DATE | https://support.google.com/docs/answer/3092969 "
                   "| 2026-11-24 |")
    a39 = next(line for line in sources.splitlines() if line.startswith("| platforms A3.9 |"))
    assert "support.google.com" not in a39 and "learn.microsoft.com" in a39


# ============================================================================= T-6, T-7, T-8

def test_google_sheets_holds_the_texts_t6_t7_and_t8() -> None:
    text = _read(REFS / "google-sheets.md")
    refresh = _flat(_no_code(_section(text, "## What a refresh does to the formulas that read an extract")))
    t6 = ("After the repair for fewer raw rows or for zero raw rows, rotm.py check --strict and rotm.py audit "
          "--strict of the copy of the model folder (step 4 of [Models kept in Google Sheets]"
          "(#models-kept-in-google-sheets)) pass: the audit reads the helper rows down to the last row of the raw "
          "sheet, so the cleared rows below it give no finding, and it reports helper formulas left below that row "
          "as H-BEYOND. This was tested here with a local stand-in (a copy of a built workbook changed with "
          "openpyxl), not in Google Sheets (tests test_v6_audit.py::test_shorter_extract_repair_passes_check_and_"
          "audit and test_v6_audit.py::test_zero_row_repair_passes_check_and_audit).")
    assert t6 in refresh
    step5 = refresh.split("5. To repair the helper rows", 1)[1].split(" - Whether whole-column INDEX", 1)[0]
    assert step5.split(" - ")[-1].startswith("After the repair for fewer raw rows"), "T-6 is the last bullet"
    kept = _flat(_no_code(_section(text, "## Models kept in Google Sheets")))
    bullet = kept.split("H-COVERAGE or H-BEYOND (errors):", 1)[1].split(" - R-CHANGED", 1)[0]
    assert bullet.endswith("then download again. After that repair, check --strict and audit --strict of the copy "
                           "pass (tested with the local stand-in of step 5, not in Google Sheets).")
    conversion = _flat(_no_code(_section(text, "## Converting a workbook from Excel to Google Sheets")))
    not_confirmed = next(line for line in conversion.split(" - ") if "Not confirmed: how Arial" in line)
    assert ("the number formats date_or_empty and datetime_or_empty (an empty date shown as an empty cell)"
            in not_confirmed)

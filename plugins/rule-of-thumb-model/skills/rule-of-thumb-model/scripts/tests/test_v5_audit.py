"""Tests of the v5 audit, compat and theme changes (package P5 of the v5 plan). Owner: P5.

- [A-aera-11]: without a manifest, an empty cell between two runs of one formula in a helper column is one H-GAP
  finding at that cell (fix: copy the formula of the cell above into it); the uncovered count is the count of raw
  rows that no helper row reads; the copy-down advice is given only for rows missing at the end.
- [A-aera-12, A-aera-13]: compat applies the function catalogue to the formulas of data validations and
  conditional-format rules (X-FUNC with the sheet, the range and the kind of rule; reported, not rewritten); a CONCAT
  with one single-value argument is reported and rewritten to a&"".
- [C-sol-11]: audit takes --use NAME as theme does; theme prints the follow-up audit command with the look options
  it was given.
- [A-aera-3, C-sol-5], supporting: the rule id C-RECOMPUTE is in the audit's rule list, so --rules accepts it.
The hook tests of [A-aera-18] are in test_hooks.py (they use its fixtures and skip with it when the hooks are absent).
No test needs Excel except the one marked `excel`.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from lib import audit, common, compat, dictionary, functions as FN
from test_audit import write_base
from test_theme import make_plain
from test_v4_audit import _concat_book

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
ROTM = SCRIPTS / "rotm.py"
STYLE_RULES = "S-FONT,S-SIZE,S-FILL,S-FONTCOLOR,S-BOLD"


def _rotm(*args: str, excel: bool = False) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PYTHONUTF8="1")
    if not excel:
        env["ROTM_NO_EXCEL"] = "1"
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(ROTM), *args], capture_output=True,
                          text=True, encoding="utf-8", timeout=600, env=env)


def _json_of(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    assert "Traceback" not in proc.stderr, proc.stderr[-800:]
    return json.loads(proc.stdout)


# ============================================================================= [A-aera-11] inferred gaps and coverage

def _helper_findings(rep: common.Report) -> list[common.Finding]:
    return [f for f in rep.findings if f.rule in ("H-GAP", "H-COVERAGE", "H-INCONSISTENT") and f.sheet == "h_sales"]


def _drop(*cells: str):
    def mutate(s: dict[str, dict[str, Any]]) -> None:
        for coord in cells:
            del s["h_sales"][coord]
    return mutate


def test_inferred_gap_inside_a_helper_column_is_one_finding(tmp_path: Path) -> None:
    """The verdict's case: raw rows 2 to 14; the helper column that reads raw_sales row by row (h_sales!B,
    =INDEX(raw_sales!$C:$C,A<row>)) is complete except B7."""
    path = write_base(tmp_path / "gap.xlsx", _drop("B7"), rows=13)
    rep = audit.audit_workbook(path, strict=True, record=False)
    hits = _helper_findings(rep)
    assert [(f.rule, f.severity, f.cell, f.count) for f in hits] == [("H-GAP", "warning", "B7", 1)], [
        f.line() for f in rep.findings]
    f = hits[0]
    assert f.fix.startswith("copy the formula of B6 into B7"), f.fix
    assert f.data["uncovered"] == 1 and f.data["uncovered_rows"] == [7] and f.data["raw_sheet"] == "raw_sales"
    assert "(inferred)" in f.message and "1 raw row (7) of raw_sales is read by no helper row" in f.message
    assert not any("row 14" in g.fix or "down to row" in g.fix for g in rep.findings), [g.line() for g in rep.findings]


def test_inferred_gap_in_a_column_that_reads_no_raw_sheet(tmp_path: Path) -> None:
    """A gap in a helper column that reads another helper column (h_sales!C, =B<row>*(1+growth)): the same finding
    without an uncovered count."""
    rep = audit.audit_workbook(write_base(tmp_path / "gapc.xlsx", _drop("C7"), rows=13), strict=True, record=False)
    hits = _helper_findings(rep)
    assert [(f.rule, f.cell) for f in hits] == [("H-GAP", "C7")], [f.line() for f in rep.findings]
    assert hits[0].fix.startswith("copy the formula of C6 into C7") and "uncovered" not in hits[0].data


def test_inferred_coverage_counts_every_raw_row_no_helper_row_reads(tmp_path: Path) -> None:
    """Rows missing at the end and a gap inside: H-GAP at the gap; H-COVERAGE counts both and gives the copy-down
    advice for the rows at the end only."""
    tail = [f"{col}{r}" for r in range(11, 15) for col in "ABC"]
    rep = audit.audit_workbook(write_base(tmp_path / "tail.xlsx", _drop("B7", *tail), rows=13), strict=True,
                               record=False)
    hits = _helper_findings(rep)
    assert sorted((f.rule, f.cell) for f in hits) == [("H-COVERAGE", "B10"), ("H-GAP", "B7")], [
        f.line() for f in rep.findings]
    cov = next(f for f in hits if f.rule == "H-COVERAGE")
    assert cov.data["uncovered"] == 5 and cov.data["uncovered_rows"] == [7, 11, 12, 13, 14]
    assert "ends at row 10" in cov.message and "5 raw row(s) (7, 11 to 14) are not covered" in cov.message
    assert cov.fix.startswith("copy row 10 of the helper columns down to row 14"), cov.fix
    assert "B7" in cov.fix                                       # the gap inside is named, not copied over


def test_inferred_spacer_row_and_label_row_are_no_gap(tmp_path: Path) -> None:
    """A column that reads no raw sheet: an empty spacer row (no cell in the row) or a row with a label between two
    runs of one formula is a break between two blocks, not a gap; the same empty cell in a row that holds other
    cells is a gap. A column that reads a raw sheet row by row: an empty row is a gap (a raw row no helper reads)."""
    def blocks(s: dict[str, dict[str, Any]]) -> None:
        for r in (11, 12, 13, 15, 16, 17, 21, 22, 23, 25, 26, 27):
            s["calc"][f"D{r}"] = f"=C{r}*inputs!$D$4"
        s["calc"]["B24"] = "Second block"                     # a label row between D21:D23 and D25:D27
    rep = audit.audit_workbook(write_base(tmp_path / "spacer.xlsx", blocks), strict=True, record=False)
    assert not [f for f in rep.findings if f.rule == "H-GAP"], [f.line() for f in rep.findings]

    def used(s: dict[str, dict[str, Any]]) -> None:
        blocks(s)
        s["calc"]["E14"] = "=C14"                             # row 14 is in use, so D14 is a hole in column D
    rep = audit.audit_workbook(write_base(tmp_path / "used.xlsx", used), strict=True, record=False)
    assert [(f.sheet, f.cell) for f in rep.findings if f.rule == "H-GAP"] == [("calc", "D14")], [
        f.line() for f in rep.findings]

    rep = audit.audit_workbook(write_base(tmp_path / "row.xlsx", _drop("A7", "B7", "C7"), rows=13), strict=True,
                               record=False)
    gaps = [f for f in rep.findings if f.rule == "H-GAP"]
    assert [(f.cell, f.data.get("uncovered")) for f in gaps] == [("B7", 1)], [f.line() for f in rep.findings]


def test_inferred_complete_helper_column_gets_no_gap(tmp_path: Path) -> None:
    rep = audit.audit_workbook(write_base(tmp_path / "full.xlsx", rows=13), strict=True, record=False)
    assert _helper_findings(rep) == [], [f.line() for f in rep.findings]


# ============================================================================= [A-aera-12] validation and CF formulas

def _rules_book(path: Path, rule_formula: str, *, list_source: str | None = None) -> Path:
    """Controls!A1 with a custom data validation and a conditional-format formula rule, both `rule_formula`."""
    import openpyxl
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.styles import PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Controls"
    ws["A1"] = "pcdos"
    for r in (1, 2, 3):
        ws.cell(r, 2, r)
    dv = DataValidation(type="custom", formula1=rule_formula)
    ws.add_data_validation(dv)
    dv.add("A1")
    if list_source:
        lv = DataValidation(type="list", formula1=list_source)
        ws.add_data_validation(lv)
        lv.add("C1:C3")
    ws.conditional_formatting.add("A1", FormulaRule(formula=[rule_formula],
                                                    fill=PatternFill("solid", fgColor="FFFFC7CE")))
    wb.save(path)
    return path


def test_validation_and_conditional_format_formulas_are_reported(tmp_path: Path) -> None:
    src = _rules_book(tmp_path / "info.xlsx", 'INFO("system")="pcdos"')
    proc = _rotm("compat", str(src), "--report-only", "--strict", "--json", "-", "--quiet")
    data = _json_of(proc)
    hits = [f for f in data["findings"] if f["rule"] == "X-FUNC"]
    assert proc.returncode == 1, data["findings"]
    assert sorted((f["severity"], f["sheet"], f["cell"], f["data"]["range"], f["data"]["kind"]) for f in hits) == [
        ("error", "Controls", "A1", "A1", "conditional format (formula rule)"),
        ("error", "Controls", "A1", "A1", "data validation (custom formula)")], hits
    for f in hits:
        assert f["data"]["function"] == "INFO" and f["data"]["formula"] == '=INFO("system")="pcdos"'
        assert "on Controls!A1" in f["message"] and "INFO exists in Excel only" in f["message"]
        assert "compat reports this formula and does not rewrite it" in f["message"]
    assert data["data"]["replaced_count"] == 0
    # compat --strict (writing) exits non-zero too and writes nothing: these formulas are not rewritten
    proc = _rotm("compat", str(src), "--strict", "--json", "-", "--quiet")
    data = _json_of(proc)
    assert proc.returncode == 1 and data["data"]["written"] is False and data["data"]["replaced_count"] == 0
    assert len([f for f in data["findings"] if f["rule"] == "X-FUNC"]) == 2
    assert not (tmp_path / "info_compat.xlsx").exists()
    # with the Excel profile INFO is no problem
    rep = compat.compat_workbook(src, report_only=True, strict=True, product="excel")
    assert not [f for f in rep.findings if f.rule == "X-FUNC"], [f.line() for f in rep.findings]


def test_rules_with_shared_functions_get_no_finding(tmp_path: Path) -> None:
    src = _rules_book(tmp_path / "shared.xlsx", "AND(ISNUMBER($B$1),LEN(A1)<10,SUM($B$1:$B$3)>0)",
                      list_source="$B$1:$B$3")
    rep = compat.compat_workbook(src, report_only=True, strict=True)
    assert rep.findings == [] and rep.exit_code == 0, [f.line() for f in rep.findings]


def test_rule_formulas_are_read_from_main_and_extension_elements() -> None:
    """The reader of the sheet XML: main-namespace validations and rules, and the x14 extension forms (validations
    that read another sheet and rules Excel stores in extLst), with escaped characters."""
    xml = ('</sheetData><conditionalFormatting sqref="D4:F6"><cfRule type="cellIs" dxfId="0" priority="1" '
           'operator="lessThan"><formula>0</formula></cfRule><cfRule type="expression" priority="2">'
           '<formula>_xlfn.ISOMITTED(A1)</formula></cfRule></conditionalFormatting>'
           '<dataValidations count="2"><dataValidation type="list" sqref="C1:C3"><formula1>"a,b"</formula1>'
           '</dataValidation><dataValidation type="whole" operator="between" sqref="E1"><formula1>0</formula1>'
           '<formula2>MAX($B$1:$B$3)</formula2></dataValidation></dataValidations>'
           '<extLst><ext uri="{CCE6A557-97BC-4b89-ADB6-D9C93CAAB3DF}"><x14:dataValidations count="1">'
           '<x14:dataValidation type="list"><x14:formula1><xm:f>INDIRECT(&quot;lists!A1:A3&quot;)</xm:f>'
           '</x14:formula1><xm:sqref>G1:G9 H2</xm:sqref></x14:dataValidation></x14:dataValidations></ext>'
           '<ext uri="{78C0D931-6437-407d-A8EE-F0AAD7539E65}"><x14:conditionalFormattings>'
           '<x14:conditionalFormatting><x14:cfRule type="expression" priority="3"><xm:f>A1&lt;&gt;lists!B1</xm:f>'
           '</x14:cfRule><xm:sqref>J1</xm:sqref></x14:conditionalFormatting></x14:conditionalFormattings></ext>'
           '</extLst></worksheet>')
    got = [(r.kind, r.sqref, r.formula) for r in compat.rule_formulas_in_xml(xml)]
    assert got == [
        ("conditional format (cell value rule)", "D4:F6", "=0"),
        ("conditional format (formula rule)", "D4:F6", "=_xlfn.ISOMITTED(A1)"),
        ("data validation (list source)", "C1:C3", '="a,b"'),
        ("data validation (whole number limit)", "E1", "=0"),
        ("data validation (whole number limit)", "E1", "=MAX($B$1:$B$3)"),
        ("data validation (list source)", "G1:G9 H2", '=INDIRECT("lists!A1:A3")'),
        ("conditional format (formula rule)", "J1", "=A1<>lists!B1"),
    ], got


# ============================================================================= [A-aera-13] CONCAT with one argument

def test_concat_with_one_argument_is_reported_and_rewritten(tmp_path: Path) -> None:
    src = _concat_book(tmp_path / "one.xlsx", "=_xlfn.CONCAT(A1)")
    proc = _rotm("compat", str(src), "--report-only", "--strict", "--json", "-", "--quiet")
    data = _json_of(proc)
    hits = [f for f in data["findings"] if f["rule"] == "X-CONCAT"]
    assert proc.returncode == 1, data["findings"]
    assert [(f["severity"], f["sheet"], f["cell"]) for f in hits] == [("warning", "calc", "B1")], hits
    assert hits[0]["message"] == "CONCAT with 1 argument: Google Sheets' CONCAT takes two values"
    assert hits[0]["fix"] == "rewritten with & by rotm.py compat"
    out = tmp_path / "out.xlsx"
    proc = _rotm("compat", str(src), "-o", str(out), "--json", "-", "--quiet")
    data = _json_of(proc)
    assert proc.returncode in (0, 3), data["findings"]           # 3: written without Excel, not recalculated
    import openpyxl
    assert openpyxl.load_workbook(out)["calc"]["B1"].value == '=A1&""'
    again = compat.compat_workbook(out, report_only=True, strict=True)
    assert not [f for f in again.findings if f.rule == "X-CONCAT"], [f.line() for f in again.findings]


@pytest.mark.parametrize("text, expected", [
    ("=_xlfn.CONCAT(A1)", '=A1&""'),
    ("=CONCAT(A1)", '=A1&""'),
    ('=_xlfn.CONCAT("x")', '="x"&""'),
    ("=_xlfn.CONCAT(A1=A3)", '=(A1=A3)&""'),
    ("=LEN(_xlfn.CONCAT(A1))", '=LEN(A1&"")'),
])
def test_concat_of_one_single_value_is_rewritten(text: str, expected: str) -> None:
    new, done, problems = compat.plan_formula(text, sheet="calc", row=1, col=2, tables=[], product="both")
    assert new == expected and done == ["X-CONCAT"], (new, done, problems)
    notes: list[str] = []
    compat.plan_formula(text, sheet="calc", row=1, col=2, tables=[], product="both", notes=notes)
    assert notes == ["CONCAT with 1 argument"]


@pytest.mark.parametrize("text, warned", [
    ("=_xlfn.CONCAT(A1,A2)", False),                # exactly two single values: the form both products share
    ("=_xlfn.CONCAT(A1:A3)", True),                 # a range argument: reported, not rewritten (v4)
])
def test_concat_two_values_and_a_range_as_in_v4(text: str, warned: bool) -> None:
    new, done, problems = compat.plan_formula(text, sheet="calc", row=1, col=2, tables=[], product="both")
    assert new is None and done == [], (new, done)
    assert [p[1] for p in problems if p[0] == "X-CONCAT"] == (["warning"] if warned else []), problems


def test_catalogue_states_the_one_argument_concat() -> None:
    e = FN.CATALOGUE["CONCAT"]
    assert "exactly two single values is the form both products share" in e.differs
    assert 'one argument a becomes a&""' in e.differs
    text = (SKILL / "references" / "functions.md").read_text(encoding="utf-8")
    row = next(line for line in text.splitlines() if line.startswith("| CONCAT |"))
    assert "exactly two single values is the form both products share" in row and 'a&""' in row


@pytest.mark.excel
def test_concat_with_one_argument_keeps_its_value_in_excel(tmp_path: Path) -> None:
    import openpyxl
    src = _concat_book(tmp_path / "one_xl.xlsx", "=_xlfn.CONCAT(A1)")
    out = tmp_path / "one_xl_out.xlsx"
    rep = compat.compat_workbook(src, out=out)
    assert rep.exit_code == 0 and rep.verified["recalculated"], [f.line() for f in rep.findings] + rep.notes
    assert openpyxl.load_workbook(out)["calc"]["B1"].value == '=A1&""'
    assert openpyxl.load_workbook(out, data_only=True)["calc"]["B1"].value == "North"


# ============================================================================= [C-sol-11] the named look in audit

def _follow_up(theme_data: dict[str, Any]) -> list[str]:
    """The follow-up audit command theme printed (its last note), as an argument list for this Python."""
    note = theme_data["notes"][-1]
    assert note.startswith("follow-up audit: "), theme_data["notes"]
    argv = shlex.split(note[len("follow-up audit: "):])
    assert argv[0] == "python" and argv[1:5] == ["-P", "-X", "utf8", "-B"], argv
    return [sys.executable, *argv[1:]]


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PYTHONUTF8="1", ROTM_NO_EXCEL="1")
    return subprocess.run(argv + ["--json", "-", "--quiet"], capture_output=True, text=True, encoding="utf-8",
                          timeout=600, env=env)


def test_theme_follow_up_audit_with_the_named_look(tmp_path: Path) -> None:
    src = make_plain(tmp_path / "plain.xlsx")
    out = tmp_path / "blue.xlsx"
    proc = _rotm("theme", str(src), "-o", str(out), "--use", "input_blue_font", "--strict", "--json", "-")
    data = _json_of(proc)
    assert proc.returncode == 0, data["findings"]
    argv = _follow_up(data)
    assert argv[5:] == [ROTM.as_posix(), "audit", out.as_posix(), "--strict", "--rules", STYLE_RULES,
                        "--use", "input_blue_font"], argv
    assert "follow-up audit: " in proc.stderr                     # printed at the end of the report
    res = _run(argv)
    rep = _json_of(res)
    assert res.returncode == 0, [f["message"] for f in rep["findings"]]
    assert rep["data"]["dictionary"]["applied"] == ["input_blue_font"]


def test_audit_without_the_named_look_reports_the_font_colour(tmp_path: Path) -> None:
    src = make_plain(tmp_path / "plain.xlsx")
    out = tmp_path / "blue.xlsx"
    data = _json_of(_rotm("theme", str(src), "-o", str(out), "--use", "input_blue_font", "--strict", "--json", "-"))
    argv = _follow_up(data)
    i = argv.index("--use")
    res = _run(argv[:i] + argv[i + 2:])
    rep = _json_of(res)
    colour = [f for f in rep["findings"] if f["rule"] == "S-FONTCOLOR"]
    assert res.returncode == 1 and colour, rep["findings"]
    assert colour[0]["sheet"] == "Inputs" and colour[0]["cell"] == "D4"


def test_theme_follow_up_names_the_dictionary_file_too(tmp_path: Path) -> None:
    src = make_plain(tmp_path / "plain.xlsx")
    ov = tmp_path / "my looks" / "ov.json"
    ov.parent.mkdir()
    common.write_json(ov, {"rotm_dictionary_override": 1, "use": ["links_green"]})
    out = tmp_path / "both.xlsx"
    data = _json_of(_rotm("theme", str(src), "-o", str(out), "--dictionary", str(ov), "--use", "input_blue_font",
                          "--strict", "--json", "-"))
    assert data["exit_code"] == 0, data["findings"]
    assert data["data"]["dictionary"]["applied"] == ["input_blue_font", "links_green"]
    argv = _follow_up(data)
    assert argv[-4:] == ["--use", "input_blue_font", "--dictionary", ov.as_posix()], argv
    res = _run(argv)
    rep = _json_of(res)
    assert res.returncode == 0, [f["message"] for f in rep["findings"]]
    assert rep["data"]["dictionary"]["applied"] == ["input_blue_font", "links_green"]   # combined as theme does


def test_audit_use_is_refused_with_a_dictionary_object_and_for_an_unknown_name(tmp_path: Path) -> None:
    path = make_plain(tmp_path / "plain.xlsx")
    with pytest.raises(common.Refusal, match="not both"):
        audit.audit_workbook(path, dictionary=dictionary.load_dictionary(), use=["input_blue_font"], record=False)
    res = _rotm("audit", str(path), "--use", "input_blue_fnt")
    assert res.returncode == 2 and "did you mean 'input_blue_font'" in res.stderr + res.stdout


def test_audit_skill_names_use_and_theme_step_5_gives_the_options() -> None:
    audit_skill = PLUGIN / "skills" / "audit" / "SKILL.md"
    theme_skill = PLUGIN / "skills" / "theme" / "SKILL.md"
    if not (audit_skill.is_file() and theme_skill.is_file()):
        pytest.skip("the skill is used without its plugin (PLUGIN/skills/audit or PLUGIN/skills/theme is absent)")
    flat = " ".join(audit_skill.read_text(encoding="utf-8").split())
    assert "`--use NAME`" in flat and "as theme" in flat
    text = theme_skill.read_text(encoding="utf-8")
    step5 = next(line for line in text.splitlines() if line.startswith("5. "))
    rest = text.split(step5, 1)[1].split("\n## ", 1)[0]
    flat5 = " ".join((step5 + rest).split())
    assert f"--rules {STYLE_RULES}" in flat5 and "the same `--use` and `--dictionary` as step 3" in flat5
    assert "follow-up audit:" in flat5


# ============================================================================= [A-aera-3, C-sol-5] C-RECOMPUTE id

def test_c_recompute_is_accepted_by_rules() -> None:
    wb = SKILL / "examples" / "sales_vs_target" / "model.xlsx"
    if not wb.is_file():
        pytest.skip("the worked example's workbook is absent")
    proc = _rotm("audit", str(wb), "--rules", "C-RECOMPUTE", "--strict", "--no-record", "--json", "-", "--quiet")
    data = _json_of(proc)
    assert proc.returncode == 0, data["findings"]
    assert data["data"]["selected_rules"] == ["C-RECOMPUTE"] and data["data"]["rules_run"] == ["check"]

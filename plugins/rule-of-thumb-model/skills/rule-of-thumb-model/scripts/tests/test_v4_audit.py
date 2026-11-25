"""Tests of the v4 audit, compat and FAST reference changes (package P5 of the v4 plan). Owner: P5.

- [A-aera-18]: references/fast-standard-rules.md gives the exceptions of FAST 2.02-01 and says, for 3.01-04,
  that the worked example's bridge rows link to the block totals of the region blocks.
- [A-aera-20]: the function catalogue marks CONCAT as differing between the products; compat reports a CONCAT
  with another number of arguments than two, or with a range argument (X-CONCAT, warning), and rewrites the
  case of single-value arguments with &.
- [C-sol-12, C-sol-13, J-1], the audit's part: a raised alert is C-ALERT (info), not C-FAIL.
- [A-aera-8, A-aera-9] part 2, the audit's part: a cell of a text raw column stored as a number is R-TYPE (error).
Workbooks and models are written under tmp_path; no test needs Excel.
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

from lib import audit, build, common, compat, functions as FN
from test_audit import manifest_sheets, patch_cached, write_model
from test_build import DATA as BUILD_DATA
from test_build import make_model

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
ROTM = SCRIPTS / "rotm.py"


def _rotm(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "ROTM_NO_EXCEL": "1"}
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(ROTM), *args], capture_output=True,
                          text=True, encoding="utf-8", timeout=600, env=env)


def _json_of(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    assert "Traceback" not in proc.stderr, proc.stderr[-800:]
    return json.loads(proc.stdout)


def _rows(text: str, first: str) -> dict[str, list[str]]:
    """Rows of the Markdown tables whose header's first cell is `first`, by their first cell."""
    out: dict[str, list[str]] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
        if lines[i].strip().startswith("|") and cells[0] == first:
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                row = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                out[row[0]] = row
                i += 1
            continue
        i += 1
    return out


# ============================================================================= [A-aera-18] the FAST reference

def test_fast_rules_state_the_exceptions_and_the_bridge_links() -> None:
    import openpyxl
    rows = _rows((SKILL / "references" / "fast-standard-rules.md").read_text(encoding="utf-8"), "Rule")
    calc_block = rows["2.02-01"]
    for exception in ("corkscrews", "cascading calculations", "trivial formulas", "two-dimensional line items"):
        assert exception in calc_block[1], exception
    assert calc_block[2] == "platforms C1.20, not confirmed"
    totals = rows["3.01-04"]
    assert "do not use row totals in model logic" in totals[1] and "display totals are for display" in totals[1]
    assert totals[2] == "platforms C1.29, not confirmed"
    assert "calculations do not" not in totals[3]
    assert "Checks read block totals, and so do calculations" in totals[3]
    ws = openpyxl.load_workbook(SKILL / "examples" / "sales_vs_target" / "model.xlsx")["calc"]
    for cell, link, total_row in (("D37", "=D$10", 10), ("D38", "=D$18", 18)):
        assert ws[cell].value == link, cell                        # the shipped workbook holds the stated link
        assert f"`calc!{cell}` `{link}`" in totals[3], cell
        assert ws.cell(total_row, 2).value == "Total"              # the row it links to is a block total row
        assert str(ws.cell(total_row, 4).value).startswith("=SUM(D")


def test_dictionary_total_meaning_agrees_with_the_fast_reference() -> None:
    """Integrator, [A-aera-18]: the cell type `total` (shown on every cover's cell-type table) does not say that a
    block total is never used in model logic: checks read it, and so do links from other blocks."""
    data = json.loads((SKILL / "references" / "style_dictionary.json").read_text(encoding="utf-8"))
    meaning = data["cell_types"]["total"]["meaning"]["value"]
    assert "not used in model logic" not in meaning
    assert "FAST 3.01-04" in meaning and "checks" in meaning and "links from other blocks" in meaning
    ref = (SKILL / "references" / "style-dictionary.md").read_text(encoding="utf-8")
    assert meaning in ref                                         # the generated section shows the same words


# ============================================================================= [A-aera-20] CONCAT

def _concat_book(path: Path, text: str) -> Path:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "calc"
    ws["A1"], ws["A2"], ws["A3"] = "North", "-", "West"
    ws["B1"] = text
    wb.save(path)
    return path


def _concat_findings(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [f for f in data["findings"] if f["rule"] == "X-CONCAT"]


def test_concat_with_three_values_is_reported_and_rewritten(tmp_path: Path) -> None:
    src = _concat_book(tmp_path / "concat.xlsx", '=_xlfn.CONCAT("North","-","West")')
    proc = _rotm("compat", str(src), "--report-only", "--strict", "--json", "-", "--quiet")
    data = _json_of(proc)
    hits = _concat_findings(data)
    assert proc.returncode == 1, data["findings"]
    assert [(f["severity"], f["sheet"], f["cell"]) for f in hits] == [("warning", "calc", "B1")], hits
    assert hits[0]["message"] == "CONCAT with 3 arguments: Google Sheets' CONCAT takes two values"
    assert hits[0]["fix"] == "rewritten with & by rotm.py compat"
    out = tmp_path / "out.xlsx"
    proc = _rotm("compat", str(src), "-o", str(out), "--json", "-", "--quiet")
    data = _json_of(proc)
    assert proc.returncode in (0, 3), data["findings"]           # 3: written without Excel, not recalculated
    assert out.is_file() and data["data"]["replaced"][0]["rules"] == ["X-CONCAT"]
    import openpyxl
    assert openpyxl.load_workbook(out)["calc"]["B1"].value == '="North"&"-"&"West"'
    again = compat.compat_workbook(out, report_only=True, strict=True)
    assert not [f for f in again.findings if f.rule == "X-CONCAT"], [f.line() for f in again.findings]


@pytest.mark.parametrize("text, expected", [
    ("=_xlfn.CONCAT(A1,A2,A3)", "=A1&A2&A3"),
    ("=CONCAT(A1,A2,A3)", "=A1&A2&A3"),           # stored without its prefix: the rewrite removes the call
    ('=LEN(_xlfn.CONCAT(A1,"-",A3))', '=LEN(A1&"-"&A3)'),
    ('=_xlfn.CONCAT(A1,"-",A3)="North-West"', '=(A1&"-"&A3)="North-West"'),
    ('=_xlfn.CONCAT(_xlfn.CONCAT(A1,A2,A3),"!","?")', '=A1&A2&A3&"!"&"?"'),
    ('=_xlfn.CONCAT(A1,_xlfn.CONCAT(A2,A3),"!")', '=A1&_xlfn.CONCAT(A2,A3)&"!"'),
])
def test_concat_of_single_values_is_rewritten_with_ampersand(text: str, expected: str) -> None:
    new, done, problems = compat.plan_formula(text, sheet="calc", row=1, col=2, tables=[], product="both")
    assert new == expected and done == ["X-CONCAT"], (new, done, problems)
    assert not [p for p in problems if p[0] == "X-CONCAT"]


@pytest.mark.parametrize("text, warned", [
    ("=_xlfn.CONCAT(A1,A2)", False),
    # v5 (A-aera-13): one argument is no longer left as it is (test_v5_audit.py: reported and rewritten to A1&"")
    ("=_xlfn.CONCAT(A1,,A3)", True),                # an empty argument: reported, not rewritten
    ('=_xlfn.CONCAT(A1:A2,"x")', True),             # a range argument: reported, not rewritten
])
def test_concat_left_as_it_is(text: str, warned: bool) -> None:
    new, done, problems = compat.plan_formula(text, sheet="calc", row=1, col=2, tables=[], product="both")
    assert new is None and done == [], (new, done)
    assert [p[1] for p in problems if p[0] == "X-CONCAT"] == (["warning"] if warned else []), problems


def test_concat_of_a_range_is_reported_not_rewritten(tmp_path: Path) -> None:
    src = _concat_book(tmp_path / "range.xlsx", "=_xlfn.CONCAT(A1:A3)")
    rep = compat.compat_workbook(src, report_only=True, strict=True)
    hits = [f for f in rep.findings if f.rule == "X-CONCAT"]
    assert [(f.severity, f.cell) for f in hits] == [("warning", "B1")], [f.line() for f in rep.findings]
    assert hits[0].message == "CONCAT with a range argument A1:A3: Google Sheets' CONCAT takes two values"
    assert hits[0].fix == 'write TEXTJOIN("",FALSE,A1:A3) or join the cells with &'
    assert rep.exit_code == 1
    written = compat.compat_workbook(src)
    assert written.data["replaced_count"] == 0 and written.data["written"] is False
    assert not (tmp_path / "range_compat.xlsx").exists()


def test_concat_with_two_values_gives_no_x_concat_warning(tmp_path: Path) -> None:
    src = _concat_book(tmp_path / "two.xlsx", '=_xlfn.CONCAT("a","b")')
    rep = compat.compat_workbook(src, report_only=True, strict=True)
    assert not [f for f in rep.findings if f.rule == "X-CONCAT"], [f.line() for f in rep.findings]
    assert rep.data["replaced_count"] == 0
    # with the Excel profile the number of arguments does not matter
    three = _concat_book(tmp_path / "three.xlsx", '=_xlfn.CONCAT("North","-","West")')
    rep = compat.compat_workbook(three, report_only=True, strict=True, product="excel")
    assert not [f for f in rep.findings if f.rule == "X-CONCAT"] and rep.data["replaced_count"] == 0


def test_catalogue_marks_concat_as_differing() -> None:
    e = FN.CATALOGUE["CONCAT"]
    assert e.cls == "allowed" and e.sheets == "yes"
    assert "Excel's CONCAT takes any number of arguments and ranges" in e.differs
    assert "Google Sheets' CONCAT takes two values" in e.differs
    assert e.differs in e.reason and "X-CONCAT" in e.differs
    # v6 (r5 verdict A-aera-9, package P6): FILTER is marked as differing in its third argument too
    assert {n for n, x in FN.CATALOGUE.items() if x.differs} == {"CONCAT", "FILTER"}
    text = (SKILL / "references" / "functions.md").read_text(encoding="utf-8")
    row = next(line for line in text.splitlines() if line.startswith("| CONCAT |"))
    assert "Google Sheets' CONCAT takes two values" in row
    section = text.split("## Functions that differ between the products", 1)[1].split("\n## ", 1)[0]
    assert "- CONCAT: " in section and "X-CONCAT" in section


# ============================================================================= [J-1] C-ALERT in the audit

def _alert_model(folder: Path, *, alert: int, check: int) -> Path:
    """The manifest model of test_audit with an alert (severity alert, result checks!I7) whose cached result is
    `alert`, and the error check total_ties (checks!I6) whose cached result is `check`. v5: a raised alert's actual
    (checks!D7) is cached above its bound (checks!E7, cached 0), so that the saved result follows from its own
    operands, as Excel saves it (rule C-RECOMPUTE reports a result its operands do not give)."""
    def sheets(s: dict[str, dict[str, Any]]) -> None:
        s["checks"].update({"B7": "big_month", "D7": "=calc!$D$7", "E7": "=inputs!$D$6", "I7": "=--(D7>E7)"})

    def man(m: dict[str, Any]) -> None:
        m["checks"].append({"id": "big_month", "kind": "at_most", "severity": "alert", "result": "checks!I7",
                            "label": "a month above the maximum"})
        m["cell_types"]["checks"] += [["B7", "row_header"], ["D7:E7", "calc"], ["I7", "check"]]

    path = write_model(folder, sheets, man)
    cached: dict[tuple[str, str], Any] = {}
    full = manifest_sheets()
    sheets(full)
    for name, cells in full.items():
        for coord, v in cells.items():
            if isinstance(v, str) and v.startswith("="):
                cached[(name, coord)] = 0 if name in ("checks", "inputs") or coord == "C2" else 100.0
    cached[("checks", "I7")] = alert
    cached[("checks", "D7")] = 100.0 if alert else 0      # D7 > E7 (0) exactly when the alert is raised
    cached[("checks", "I6")] = check
    cached[("checks", "C2")] = check
    for sheet in ("inputs", "calc"):
        cached[(sheet, "C2")] = check
    patch_cached(path, cached)
    return path


def test_raised_alert_is_c_alert(tmp_path: Path) -> None:
    path = _alert_model(tmp_path / "raised", alert=1, check=0)
    rep = audit.audit_workbook(path, strict=True, record=False)
    alerts = [f for f in rep.findings if f.rule == "C-ALERT"]
    assert [(f.severity, f.sheet, f.cell) for f in alerts] == [("info", "checks", "I7")], [
        f.line() for f in rep.findings]
    assert alerts[0].message == "alert 'big_month' (a month above the maximum) is raised: checks!I7 holds 1"
    assert alerts[0].fix == "look at the alert's cells; alerts are not part of the master check"
    assert not [f for f in rep.findings if f.rule == "C-FAIL"], [f.line() for f in rep.findings]
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    assert audit.RULES["C-ALERT"].severity == "info"
    only = audit.audit_workbook(path, strict=True, record=False, rules=["C-ALERT"])
    assert [f.rule for f in only.findings] == ["C-ALERT"]
    # a failing error check is still C-FAIL (error)
    path = _alert_model(tmp_path / "failing", alert=0, check=1)
    rep = audit.audit_workbook(path, strict=True, record=False)
    fails = [f for f in rep.findings if f.rule == "C-FAIL"]
    assert ("error", "checks", "I6") in [(f.severity, f.sheet, f.cell) for f in fails], [
        f.line() for f in rep.findings]
    assert not [f for f in rep.findings if f.rule == "C-ALERT"] and rep.exit_code == 1


# ============================================================================= [A-aera-9] R-TYPE, text column

def test_text_key_stored_as_number_fails_audit(tmp_path: Path) -> None:
    import openpyxl
    lines = (BUILD_DATA / "sales.csv").read_text(encoding="utf-8").splitlines()
    first = lines[1].split(",")
    lines[1] = ",".join(["901", *first[1:]])                    # the text key '901'
    csv_path = tmp_path / "sales.csv"
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    root = make_model(tmp_path / "m", csv_path=csv_path)
    rep = build.build(root / "model.spec.json", strict=False)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    wb_path = root / "model.xlsx"
    wb = openpyxl.load_workbook(wb_path)
    ws = wb["raw_sales"]
    assert ws["A1"].value == "invoice_id" and ws["A2"].value == "901"
    before = audit.audit_workbook(wb_path, strict=True, record=False)
    assert not [f for f in before.findings if f.rule == "R-TYPE"], [f.line() for f in before.findings]
    ws["A2"].value = 901                                         # the same key stored as a number
    wb.save(wb_path)
    proc = _rotm("audit", str(root), "--strict", "--json", "-", "--quiet", "--no-record")
    data = _json_of(proc)
    hits = [f for f in data["findings"] if f["rule"] == "R-TYPE"]
    assert proc.returncode == 1, data["findings"]
    assert [(f["severity"], f["sheet"], f["cell"]) for f in hits] == [("error", "raw_sales", "A2")], hits
    assert hits[0]["message"] == ("1 cell(s) of text column invoice_id in raw_sales are stored as numbers "
                                  "(first A2: 901)")
    assert hits[0]["fix"].startswith("land the data again (") and hits[0]["fix"].endswith(
        "--table sales --csv <file> --force) and rebuild; a raw sheet holds landed values only")


# ============================================================================= the command skills

def test_command_skills_state_c_alert_and_the_concat_case() -> None:
    audit_skill = PLUGIN / "skills" / "audit" / "SKILL.md"
    compat_skill = PLUGIN / "skills" / "compat" / "SKILL.md"
    if not (audit_skill.is_file() and compat_skill.is_file()):
        pytest.skip("the skill is used without its plugin (PLUGIN/skills/audit/SKILL.md or "
                    "PLUGIN/skills/compat/SKILL.md is absent)")
    flat = " ".join(audit_skill.read_text(encoding="utf-8").split())
    assert "`C-ALERT` (info)" in flat and "shows ALERT" in flat
    assert re.search(r"`R-TYPE` \(error\)[^.]*text column[^.]*stored as a number", flat), flat
    flat = " ".join(compat_skill.read_text(encoding="utf-8").split())
    assert "`CONCAT`" in flat and "Google Sheets' `CONCAT` takes two values" in flat

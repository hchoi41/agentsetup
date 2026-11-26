"""v9 tests of package P4 (compat), round 8 verdict entry [A-aera-9, A-aera-12]. Owner: P4 (v9).

- (1) [A-aera-9]: the argument rules compat applies to worksheet formulas (X-CONCAT for an Excel CONCAT with one
  argument, more than two arguments or a range argument; X-FILTER for a FILTER with a third argument) also run on
  the formulas of data validations, conditional-format rules and defined names. A finding names the range or the
  name, has the rule id of the cell finding, and its fix ends with "the rule is reported, not rewritten" ("the name
  is reported, not rewritten" for a name). Nothing is rewritten; compat --strict and --report-only --strict exit
  non-zero. A CONCAT with two single values gets no finding.
- (2) [A-aera-12]: REGEXMATCH's catalogue fix describes ISNUMBER(SEARCH(...)) as a test for a piece of text without
  regard to case. compat prints the recipe only for a pattern that is a typed text with none of ^ $ . [ ] ( ) { } *
  + ? | and the backslash, with the words on case; for any other pattern it says that a rewrite is needed and that
  the original rule stays until the rewrite is tested on examples that match and examples that do not.

The workbooks are written with openpyxl. No test here needs Excel (ROTM_NO_EXCEL=1 for every command).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from lib import compat, functions as FN

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
ROTM = SCRIPTS / "rotm.py"

RANGE = "D2:D10"
CONCAT_3 = "LEN(_xlfn.CONCAT($A2,$B2,$C2))>0"                  # the form Excel saves
CONCAT_2 = "LEN(_xlfn.CONCAT($A2,$B2))>0"
NAME_CONCAT_3 = "LEN(_xlfn.CONCAT(Orders!$A2,Orders!$B2,Orders!$C2))>0"
NAME_CONCAT_2 = "LEN(_xlfn.CONCAT(Orders!$A2,Orders!$B2))>0"
CONCAT_MESSAGE = "CONCAT with 3 arguments: Google Sheets' CONCAT takes two values"
RULE_END = "the rule is reported, not rewritten"
NAME_END = "the name is reported, not rewritten"
PLACES = ("validation", "rule", "name")

# the fixed texts of BUILD/v9_plan.md section 5.1
CATALOGUE_FIX = ("ISNUMBER(SEARCH(<text>,<cell>)) in a helper column: it tests whether the cell holds the text, "
                 "without regard to case, so it gives REGEXMATCH's result only for a pattern that is plain text")
REWRITE = ("a rewrite for this pattern is needed (it holds a regular-expression sign); keep the original rule until "
           "the rewrite is tested on examples that match and examples that do not")
REWRITE_NOT_TYPED = ("a rewrite for this pattern is needed (the pattern is not a typed text); keep the original rule "
                     "until the rewrite is tested on examples that match and examples that do not")
ANCHORED = '"^[A-Z]{3}-[0-9]{3}$"'
SIGNS = ("^", "$", ".", "[", "]", "(", ")", "{", "}", "*", "+", "?", "|", "\\")


def _recipe(pattern: str, cell: str) -> str:
    return (f"use ISNUMBER(SEARCH({pattern},{cell})) in a helper column: it tests whether the cell holds {pattern} "
            "without regard to case, while REGEXMATCH is case-sensitive")


def _rotm(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "ROTM_NO_EXCEL": "1"}
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(ROTM), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=600, env=env)


def _json_of(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    assert "Traceback" not in proc.stderr, proc.stderr[-800:]
    return json.loads(proc.stdout)


def _book(path: Path, *, validation: str | None = None, rule: str | None = None, name: str | None = None,
          cells: dict[str, str] | None = None) -> Path:
    """Sheet Orders with A2:C10 typed; `validation`: a custom data validation on Orders!D2:D10; `rule`: a
    conditional-format formula rule on Orders!D2:D10; `name`: the workbook-scope name ValidOrder, used by
    Orders!E2; `cells`: further formulas of Orders."""
    import openpyxl
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.styles import PatternFill
    from openpyxl.workbook.defined_name import DefinedName
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Orders"
    for c, head in enumerate(("region", "prefix", "number", "status"), 1):
        ws.cell(1, c, head)
    for r in range(2, 11):
        ws.cell(r, 1, "EU")
        ws.cell(r, 2, "INV")
        ws.cell(r, 3, 400 + r)
    if validation is not None:
        dv = DataValidation(type="custom", formula1=validation)
        ws.add_data_validation(dv)
        dv.add(RANGE)
    if rule is not None:
        ws.conditional_formatting.add(RANGE, FormulaRule(formula=[rule], fill=PatternFill("solid",
                                                                                          fgColor="FFFFC7CE")))
    if name is not None:
        wb.defined_names["ValidOrder"] = DefinedName("ValidOrder", attr_text=name)
        ws["E2"] = "=ValidOrder"
    for coord, value in (cells or {}).items():
        ws[coord] = value
    wb.save(path)
    return path


def _place_book(path: Path, place: str, rule_formula: str, name_formula: str) -> Path:
    return _book(path, **{place: name_formula if place == "name" else rule_formula})


def _hits(findings: list[Any], rule: str) -> list[dict[str, Any]]:
    out = []
    for f in findings:
        d = f if isinstance(f, dict) else f.to_dict()
        if d["rule"] == rule:
            out.append(d)
    return out


def _check_place(f: dict[str, Any], place: str) -> None:
    """The finding names the range (validation, conditional format) or the name, and says it is not rewritten."""
    if place == "name":
        assert f["message"].startswith("defined name ValidOrder (workbook scope): " + CONCAT_MESSAGE), f
        assert f["message"].endswith("; used by Orders!E2"), f
        assert f["fix"].endswith(NAME_END) and "Name Manager" in f["fix"], f
        assert (f["sheet"], f["cell"]) == ("Orders", "E2"), f
        assert f["data"]["defined_name"] == "ValidOrder" and f["data"]["scope"] == "workbook", f
        return
    kind = "data validation (custom formula)" if place == "validation" else "conditional format (formula rule)"
    assert f["message"] == f"{kind} on Orders!{RANGE}: {CONCAT_MESSAGE}", f
    assert f["fix"].endswith(RULE_END), f
    assert (f["sheet"], f["cell"]) == ("Orders", "D2"), f
    assert f["data"]["kind"] == kind and f["data"]["range"] == RANGE, f


# ============================================================================= (1) argument rules outside cells

def test_concat_with_three_arguments_is_reported_in_a_validation_a_rule_and_a_name(tmp_path: Path) -> None:
    """The reviewer's case: LEN(CONCAT($A2,$B2,$C2))>0 in a data validation and a conditional-format rule on
    Orders!D2:D10 and in the defined name ValidOrder. Each place is reported; both strict forms exit non-zero and
    nothing is written."""
    src = _book(tmp_path / "orders.xlsx", validation=CONCAT_3, rule=CONCAT_3, name=NAME_CONCAT_3)
    before = src.read_bytes()
    for args in (["--report-only", "--strict"], ["--strict"]):
        proc = _rotm("compat", str(src), *args, "--json", "-", "--quiet")
        data = _json_of(proc)
        hits = _hits(data["findings"], "X-CONCAT")
        assert proc.returncode == 1, (args, data["findings"])
        assert len(hits) == 3 and all(f["severity"] == "warning" for f in hits), hits
        by_place = {"validation": [f for f in hits if f["message"].startswith("data validation")],
                    "rule": [f for f in hits if f["message"].startswith("conditional format")],
                    "name": [f for f in hits if f["message"].startswith("defined name")]}
        for place, found in by_place.items():
            assert len(found) == 1, (place, hits)
            _check_place(found[0], place)
        assert data["data"]["replaced_count"] == 0 and data["data"]["written"] is False
    assert src.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["orders.xlsx"]


@pytest.mark.parametrize("place", PLACES)
def test_each_place_alone_is_reported_and_exits_non_zero(tmp_path: Path, place: str) -> None:
    src = _place_book(tmp_path / f"{place}.xlsx", place, CONCAT_3, NAME_CONCAT_3)
    rep = compat.compat_workbook(src, report_only=True, strict=True)
    hits = _hits(rep.findings, "X-CONCAT")
    assert rep.exit_code != 0 and len(hits) == 1, [f.line() for f in rep.findings]
    _check_place(hits[0], place)
    # the same formula without the _xlfn. prefix (as another program may store it) is reported the same way
    plain = _place_book(tmp_path / f"{place}_plain.xlsx", place, CONCAT_3.replace("_xlfn.", ""),
                        NAME_CONCAT_3.replace("_xlfn.", ""))
    rep = compat.compat_workbook(plain, report_only=True, strict=True)
    assert rep.exit_code != 0 and len(_hits(rep.findings, "X-CONCAT")) == 1, [f.line() for f in rep.findings]
    # with the Excel profile the argument rules do not apply, as for a cell
    rep = compat.compat_workbook(src, report_only=True, strict=True, product="excel")
    assert _hits(rep.findings, "X-CONCAT") == [], [f.line() for f in rep.findings]


def test_concat_with_two_single_values_gets_no_finding(tmp_path: Path) -> None:
    src = _book(tmp_path / "two.xlsx", validation=CONCAT_2, rule=CONCAT_2, name=NAME_CONCAT_2)
    proc = _rotm("compat", str(src), "--report-only", "--strict", "--json", "-", "--quiet")
    data = _json_of(proc)
    assert _hits(data["findings"], "X-CONCAT") == [] and _hits(data["findings"], "X-FILTER") == [], data["findings"]
    assert proc.returncode == 0, data["findings"]


def test_filter_with_a_third_argument_is_reported_in_a_rule_and_a_name(tmp_path: Path) -> None:
    rule = 'ROWS(_xlfn._xlws.FILTER($A$2:$A$10,$C$2:$C$10>405,"none"))>1'
    name = '_xlfn._xlws.FILTER(Orders!$A$2:$A$10,Orders!$C$2:$C$10>405,"none")'
    src = _book(tmp_path / "filter.xlsx", validation=rule, rule=rule, name=name)
    rep = compat.compat_workbook(src, report_only=True, strict=True)
    hits = _hits(rep.findings, "X-FILTER")
    assert rep.exit_code != 0 and len(hits) == 3, [f.line() for f in rep.findings]
    message = ('FILTER with 3 arguments (third argument: "none"): in Excel the third argument is the value returned '
               "when no row matches; in Google Sheets it is a further condition")
    assert sorted(f["message"] for f in hits) == sorted([
        f"conditional format (formula rule) on Orders!{RANGE}: {message}",
        f"data validation (custom formula) on Orders!{RANGE}: {message}",
        f"defined name ValidOrder (workbook scope): {message}; used by Orders!E2"]), hits
    assert all(f["fix"].endswith(NAME_END if f["message"].startswith("defined") else RULE_END) for f in hits), hits
    two = _book(tmp_path / "filter2.xlsx", rule='ROWS(_xlfn._xlws.FILTER($A$2:$A$10,$C$2:$C$10>405))>1')
    assert _hits(compat.compat_workbook(two, report_only=True, strict=True).findings, "X-FILTER") == []


@pytest.mark.parametrize("formula, expected", [
    ("=LEN(CONCAT($A2,$B2,$C2))>0", [("X-CONCAT", "warning", "CONCAT with 3 arguments: Google Sheets' CONCAT takes "
                                       "two values", "join the values with & (a&b&...)")]),
    ("=LEN(CONCAT($A2))>0", [("X-CONCAT", "warning", "CONCAT with 1 argument: Google Sheets' CONCAT takes two values",
                              'write it as a&"" (the same text)')]),
    ("=LEN(CONCAT($A2:$C2))>0", [("X-CONCAT", "warning", "CONCAT with a range argument $A2:$C2: Google Sheets' CONCAT "
                                  "takes two values", 'write TEXTJOIN("",FALSE,$A2:$C2) or join the cells with &')]),
    ("=LEN(CONCAT($A2,$B2))>0", []),
    ('=LEN(CONCATENATE($A2,$B2,$C2))>0', []),            # CONCATENATE takes any number of values in both products
])
def test_argument_problems_are_the_rules_of_a_cell_without_a_rewrite(formula: str, expected: list) -> None:
    assert compat.argument_problems(formula, "both") == expected
    assert compat.argument_problems(formula, "excel") == []
    assert compat.argument_problems(formula, "sheets") == expected


# ============================================================================= (2) the REGEXMATCH advice

def test_the_catalogue_describes_the_search_recipe_as_a_test_without_regard_to_case() -> None:
    assert FN.replacement("REGEXMATCH") == CATALOGUE_FIX
    text = (SKILL / "references" / "functions.md").read_text(encoding="utf-8")
    row = next(line for line in text.splitlines() if line.startswith("| REGEXMATCH |"))
    assert f"| {CATALOGUE_FIX} |" in row, row


def _x_func_fix(formula: str, product: str = "both") -> list[str]:
    _new, _done, problems = compat.plan_formula(formula, sheet="Orders", row=2, col=6, tables=[], product=product)
    return [fix for rule, _sev, message, fix in problems if rule == "X-FUNC" and message.startswith("REGEXMATCH")]


def test_an_anchored_character_class_pattern_gets_the_rewrite_text() -> None:
    fixes = _x_func_fix(f"=REGEXMATCH(A2,{ANCHORED})")
    assert fixes == [REWRITE], fixes
    assert "ISNUMBER" not in fixes[0] and "SEARCH" not in fixes[0]


def test_a_plain_word_gets_the_recipe_with_the_words_on_case() -> None:
    assert _x_func_fix('=REGEXMATCH(A2,"invoice")') == [_recipe('"invoice"', "A2")]
    assert _x_func_fix('=IF(REGEXMATCH($B7,"INV 2026"),1,0)') == [_recipe('"INV 2026"', "$B7")]
    assert _x_func_fix('=REGEXMATCH(A2,"invoice")', product="sheets") == []      # REGEXMATCH is in Sheets


@pytest.mark.parametrize("sign", SIGNS)
def test_each_regular_expression_sign_gets_the_rewrite_text(sign: str) -> None:
    assert _x_func_fix(f'=REGEXMATCH(A2,"inv{sign}oice")') == [REWRITE], sign


@pytest.mark.parametrize("pattern", ["B1", '"inv"&"oice"', "LOWER(B1)"])
def test_a_pattern_that_is_not_a_typed_text_gets_the_rewrite_text(pattern: str) -> None:
    assert _x_func_fix(f"=REGEXMATCH(A2,{pattern})") == [REWRITE_NOT_TYPED], pattern


def test_two_calls_with_different_patterns_name_each_call() -> None:
    fixes = _x_func_fix(f'=AND(REGEXMATCH(A2,"invoice"),REGEXMATCH(B2,{ANCHORED}))')
    assert fixes == [f'for REGEXMATCH(A2,"invoice"): {_recipe(chr(34) + "invoice" + chr(34), "A2")}; '
                     f"for REGEXMATCH(B2,{ANCHORED}): {REWRITE}"], fixes


def test_regexmatch_in_a_rule_and_a_name_gets_the_same_advice(tmp_path: Path) -> None:
    """The reviewer's invoice filter as a conditional-format rule and a defined name: the anchored pattern gets the
    rewrite text, the plain word the recipe; the rule and the name are not rewritten."""
    src = _book(tmp_path / "regex.xlsx", rule=f"REGEXMATCH($B2&\"-\"&$C2,{ANCHORED})",
                name='REGEXMATCH(Orders!$A$2,"invoice")')
    rep = compat.compat_workbook(src, report_only=True, strict=True)
    hits = [f for f in _hits(rep.findings, "X-FUNC") if f["data"].get("function") == "REGEXMATCH"]
    assert rep.exit_code != 0 and len(hits) == 2, [f.line() for f in rep.findings]
    rule = next(f for f in hits if f["data"]["kind"].startswith("conditional format"))
    assert rule["fix"] == (f"in the rule (Home > Conditional Formatting > Manage Rules in Excel): {REWRITE}; "
                           f"{RULE_END}"), rule
    name = next(f for f in hits if f["data"]["kind"] == "defined name")
    assert name["fix"] == (f"in the name's formula (in Excel: Formulas > Name Manager): "
                           f"{_recipe(chr(34) + 'invoice' + chr(34), 'Orders!$A$2')}; {NAME_END}"), name


# ============================================================================= skills/compat/SKILL.md

def test_compat_skill_states_the_argument_rules_outside_cells_and_the_regexmatch_advice() -> None:
    path = SKILL.parents[1] / "skills" / "compat" / "SKILL.md"
    if not path.is_file():
        pytest.skip("the skill is used without its plugin (PLUGIN/skills/compat/SKILL.md is absent)")
    text = " ".join(path.read_text(encoding="utf-8").replace("`", "").split())
    assert ("The argument rules of a cell also run on the formulas of data validations, conditional-format rules "
            "and defined names") in text
    assert f"data validation (custom formula) on Orders!{RANGE}: {CONCAT_MESSAGE}" in text
    assert f"the fix ends with {RULE_END} ({NAME_END} for a name)" in text
    assert "--strict and --report-only --strict exit 1. A CONCAT with two single values gets no finding." in text
    assert ("only when the pattern is a typed text with none of ^ $ . [ ] ( ) { } * + ? | and the backslash"
            in text)
    assert compat.regexmatch_fix("A2", '"invoice"') in text          # the example is compat's own text
    assert ("the fix says that a rewrite for this pattern is needed and that the original rule stays until the "
            "rewrite is tested on examples that match and examples that do not") in text

"""Tests of the v9 integration (the v9 integrator, 2026-11-26). Owner: the integrator.

Tests that need two packages' changes together (v9 plan section 7) and that no package wrote:
- I-5: check.RULES["D-CRITERIA"] (P2's text of plan 5.1) names the cases of P3's code (criteria.SELF_REASONS,
  criteria._self_case) and the reason key; references/checks.md (P8) names the same cases.
- I-2: the documents state the texts the code gives: the general reason of the self-mismatch finding
  (references/helper-patterns.md, P3, against lib/criteria.py), the heading the fix texts name, and the refusal of
  --control for a column without a contract total (SKILL.md, column-contract.md, model-spec-format.md and the check
  skill, P8, against lib/land.py, P2).
- Request v9_P4 R1 ([A-aera-9]): references/google-sheets.md states that compat applies its argument rules to the
  formulas of data validations, conditional-format rules and defined names, as lib/compat.py does (P4).

Run from the skill folder: python -P -X utf8 -B -m pytest scripts/tests/test_v9_integrator.py -q -p no:cacheprovider
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from lib import check, compat, criteria, land

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
REFS = SKILL / "references"
HAS_PLUGIN = (PLUGIN / ".claude-plugin" / "plugin.json").is_file()


def _plain(text: str) -> str:
    """Flattened, without code-span backticks."""
    return re.sub(r"\s+", " ", text.replace("`", ""))


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ============================================================================= I-5: D-CRITERIA, code and checks.md

def test_d_criteria_rule_names_the_cases_the_code_reports() -> None:
    """The description of D-CRITERIA (P2) names the v9 case and the reason key; P3's code reports a self-mismatch
    for each case it names, with the reason texts of plan 5.1; checks.md item 9 (P8) names the same cases."""
    text = check.RULES["D-CRITERIA"][1]
    assert ("(for example the texts TRUE and FALSE, a label from a cell that starts with =, < or >, or a label that "
            "holds ~* or ~?)") in text, text
    assert "(v8, C-sol-1; v9, A-aera-1 and C-sol-2; reason self_mismatch)" in text, text
    assert check.RULES["D-CRITERIA"][0] == "warning"
    cases = {"TRUE": "logical", "false": "logical", "<100": "operator", "=x": "operator", "A~*": "tilde",
             "A~?": "tilde", "A~b*": "other", "A~~?": "other"}
    assert {label: criteria._self_case(label) for label in cases} == cases
    assert criteria.SELF_REASONS["tilde"] == ("Excel reads ~* and ~? in a criterion as a literal * and ?, so the label "
                                              "does not match itself")
    assert criteria.SELF_REASONS["other"] == "Excel's criteria matching does not match this text with itself"
    for label in ("A~b", "~", "A*", "East"):
        assert criteria._self_case(label) is None, label
    checks_md = _plain(_read(REFS / "checks.md"))
    assert ("(for example the texts TRUE and FALSE in any case, a label from a cell that starts with =, < or >, or a "
            "label that holds ~* or ~?; reason self_mismatch)") in checks_md


# ============================================================================= I-2: documents against the code texts

def test_helper_patterns_quotes_the_general_reason_and_holds_the_heading_the_fixes_name() -> None:
    text = _read(REFS / "helper-patterns.md")
    assert f'"{criteria.SELF_REASONS["other"]}"' in text
    heading = "A label that Excel's criteria matching does not match with itself"
    for fix in (criteria.FIX_SIGNS, criteria.FIX_SELF):
        assert f'references/helper-patterns.md, "{heading}"' in fix, fix
    assert f"\n### {heading}\n" in text
    assert "\n#### Helper keys for a label that holds a tilde\n" in text


def _control_docs() -> list[Path]:
    files = [SKILL / "SKILL.md", REFS / "column-contract.md", REFS / "model-spec-format.md"]
    if HAS_PLUGIN:
        files.append(PLUGIN / "skills" / "check" / "SKILL.md")
    return files


@pytest.mark.parametrize("path", _control_docs(), ids=lambda p: p.parent.name + "/" + p.name)
def test_documents_state_the_refusal_of_an_uncontracted_control(path: Path) -> None:
    """land's refusal (P2) says that a control total is part of the agreement and that build then writes the check
    <t>_total_<column>; the documents (P8, text D-2) say the same."""
    refusal = land.UNCONTRACTED_CONTROL.format(item="lines.amount=1", table="lines", column="amount", done="written")
    assert "a control total is part of the agreement" in refusal
    fix = land.UNCONTRACTED_CONTROL_FIX.format(model="m", table="lines", column="amount")
    assert "build then writes its check lines_total_amount" in fix
    text = _plain(_read(path))
    assert "a control total is part of the agreement" in text, path.name
    assert "build then writes its check <t>_total_<column>" in text, path.name


# ============================================================================= request v9_P4 R1 ([A-aera-9])

def test_google_sheets_states_the_argument_rules_on_rules_and_names() -> None:
    text = _plain(_read(REFS / "google-sheets.md"))
    assert ("The same function catalogue is applied to the formulas of data validations and conditional-format "
            "rules: a function one product lacks is X-FUNC with the sheet, the range and the kind of rule; these "
            "formulas are reported, not rewritten.") in text
    assert ("compat applies its argument rules (for example CONCAT with more than two arguments or a range, FILTER "
            "with a third argument) also to the formulas of data validations, conditional-format rules and defined "
            "names; they are reported, not rewritten, and --strict exits non-zero.") in text
    # the code that the sentence states (P4): the argument rules of a cell, run on a formula without a rewrite
    assert [p[0] for p in compat.argument_problems("=LEN(CONCAT($A2,$B2,$C2))>0", "both")] == ["X-CONCAT"]
    assert [p[0] for p in compat.argument_problems("=FILTER(A2:A9,B2:B9>0,\"none\")", "both")] == ["X-FILTER"]
    assert compat.argument_problems("=LEN(CONCAT($A2,$B2))>0", "both") == []
    assert compat.RULE_NOT_REWRITTEN == "the rule is reported, not rewritten"

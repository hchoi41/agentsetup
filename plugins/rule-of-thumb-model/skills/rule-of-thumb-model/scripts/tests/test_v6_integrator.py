"""Tests of the v6 integrator's changes (the requests of the eight v6 packages, 2026-11-26). Owner: the integrator.

- [C-sol-3] (major), the statement of references/google-sheets.md that check --strict and audit --strict of the copy
  of the model folder pass after the repair for fewer or zero raw rows: test_v6_audit.py runs check without Excel
  (exit 3, not recalculated); here the same stand-in is checked with Excel (exit 0, every check holds) and audited
  after Excel saved it.
- Request v6_P1 R-1: references/model-spec-format.md "Argument counts" gives the fix of the five pair rules in the
  form formula.py prints it.
- Request v6_P3 R1 and R2: the documents give the fix of X-QUERY-CHANGED for a query attached by hand and carried
  over by build. v7 (r6 verdict [A-aera-2, C-sol-5]): the v6 sentence that only check --refresh reads the query
  back is no longer true and is gone; SKILL.md states that check --accept-refresh reads it back too (with Excel).
- Request v6_P5 R2: SKILL.md P4 states that a part in a branch of IF carries the condition (or SPLIT-GUARD).
- Request v6_P8 R1 (the X-OPEN fix names the rebuild) is tested by the assertions added to
  test_v4_check.py::test_failed_excel_job_is_x_open, test_v4_core.py::test_x_open_finding_texts and
  test_v6_formula.py::test_x_open_fix_names_f_parse_f_args_and_excel; request v6_P5 R1 by
  test_v3_tools.py::test_excel_split_of_a_guarded_division_by_zero_gets_split_guard.

Run from the skill folder: python -P -X utf8 -B -m pytest scripts/tests/test_v6_integrator.py -q -p no:cacheprovider
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

from lib import formula

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
REFS = SKILL / "references"
HAS_PLUGIN = (PLUGIN / ".claude-plugin" / "plugin.json").is_file()
needs_plugin = pytest.mark.skipif(not HAS_PLUGIN, reason="skill copied without its plugin")
ROTM_CMD = [sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py")]
HELPER_RULES = ("H-GAP", "H-COVERAGE", "H-BEYOND")


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _no_code(text: str) -> str:
    return text.replace("`", "")


# ----------------------------------------------------------------------------- [C-sol-3]: the stand-in with Excel

def _rotm_excel(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop("ROTM_NO_EXCEL", None)
    return subprocess.run(ROTM_CMD + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=env, timeout=1800)


@pytest.mark.excel
@pytest.mark.parametrize("fewer, allow_empty", [(4, False), (60, True)], ids=["shorter", "zero"])
def test_repaired_download_passes_check_with_excel(tmp_path_factory: pytest.TempPathFactory,
                                                   request: pytest.FixtureRequest, tmp_path: Path, fewer: int,
                                                   allow_empty: bool) -> None:
    """The stand-in of test_v6_audit.py (a copy of the built starter workbook changed with openpyxl: fewer raw rows,
    or none under a contract agreed with --allow-empty, the helper rows below the last raw row cleared and kept,
    the new total in the control cell; the export landed with land --force --control): check --strict with Excel
    exits 0 with every check holding and no H-GAP, H-COVERAGE or H-BEYOND; audit --strict of the workbook Excel
    saved exits 0."""
    import test_v6_audit as v6a
    folder = v6a._outside_folder(tmp_path_factory, request, f"v6_int_{fewer}")
    model = v6a._static_model(folder / "model", allow_empty=allow_empty)
    copy, rows, _total = v6a._route(model, tmp_path, fewer=fewer)
    assert len(rows) == 60 - fewer
    out = tmp_path / "check_excel.json"
    checked = _rotm_excel("check", str(copy), "--strict", "--json", str(out))
    report: dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
    assert checked.returncode == 0, (checked.stdout[-3000:], checked.stderr[-2000:])
    assert not [f for f in report["findings"] if f["rule"] in HELPER_RULES], report["findings"]
    assert "checks: 9 of 9 hold" in checked.stdout, checked.stdout[-3000:]
    code, audited = v6a._audit_strict(copy, tmp_path, "audit_after_excel.json")
    assert not [f for f in audited["findings"] if f["rule"] in HELPER_RULES], audited["findings"]
    assert code == 0, [(f["rule"], f["message"]) for f in audited["findings"] if f["severity"] != "info"]


def test_google_sheets_names_the_excel_check_of_the_stand_in() -> None:
    text = _flat(_no_code(_read(REFS / "google-sheets.md")))
    assert ("With Excel, rotm.py check --strict of the same stand-in exits 0 with every check holding, and "
            "rotm.py audit --strict of the workbook Excel saved exits 0 (test "
            "test_v6_integrator.py::test_repaired_download_passes_check_with_excel).") in text


# ----------------------------------------------------------------------------- request v6_P1 R-1

def test_pair_rule_fix_in_the_specification_format_matches_the_code() -> None:
    text = _flat(_read(REFS / "model-spec-format.md"))
    section = text[text.index("## Argument counts"):text.index("## Checks")]
    assert ("for SUMIFS, AVERAGEIFS, MAXIFS, MINIFS and COUNTIFS the word `arguments` stands inside the parentheses"
            in section)
    example = re.search(r"for example `(give SUMIFS [^`]+)`", section)
    assert example, section
    printed = formula._args_fix("SUMIFS", formula.ARG_COUNTS["SUMIFS"])
    assert printed.startswith(example.group(1) + " ("), (printed, example.group(1))
    for name in ("SUMIFS", "AVERAGEIFS", "MAXIFS", "MINIFS", "COUNTIFS"):
        assert formula.ARG_COUNTS[name].pairs, name


# ----------------------------------------------------------------------------- request v6_P3 R1 and R2

HAND_FIX = ("For a query attached by hand and carried over by build (query_attach hand), the fix of X-QUERY-CHANGED "
            "is different: to keep the change, rebuild")
HAND_FIX_SHORT = ("For a query attached by hand and carried over by build (query_attach hand), the fix is different: "
                  "to keep the change, rebuild")
# v7 (r6 verdict [A-aera-2, C-sol-5]): with Excel, check --accept-refresh reads the query back also without
# --refresh, so the v6 sentences on the read-back order are no longer true and are replaced (v7 plan 5.4, T-4,
# T-17, T-20). The hand-query fix stays.
V6_READ_BACK = "The query is read back only by check --refresh"
V6_ALONE = "check --accept-refresh alone does not read the query again"
V7_READ_BACK = ("With Excel, check --accept-refresh reads the query back and compares it too, also when it is given "
                "without --refresh")


def test_power_query_reference_gives_the_hand_query_fix_and_the_read_back_order() -> None:
    text = _flat(_no_code(_read(REFS / "excel-power-query.md")))
    assert HAND_FIX in text
    assert ("build reads the query from the workbook, attaches it again and records its formula); to drop the "
            "change, change the query back in Excel's Power Query editor") in text
    assert V6_ALONE not in text and V6_READ_BACK not in text, "excel-power-query.md keeps the v6 read-back order"


def test_skill_md_gives_the_hand_query_fix_and_the_read_back_order() -> None:
    text = _flat(_no_code(_read(SKILL / "SKILL.md")))
    after = text[text.index("**After a refresh**"):]
    assert HAND_FIX_SHORT in after and V7_READ_BACK in after
    assert V6_ALONE not in text and V6_READ_BACK not in text


@needs_plugin
def test_check_skill_gives_the_hand_query_fix_and_the_read_back_order() -> None:
    text = _flat(_no_code(_read(PLUGIN / "skills" / "check" / "SKILL.md")))
    assert HAND_FIX_SHORT in text
    assert V6_ALONE not in text and V6_READ_BACK not in text, "skills/check/SKILL.md keeps the v6 read-back order"


def test_the_hand_query_fix_the_documents_give_is_the_one_check_prints() -> None:
    """The documents and check._query_formula_findings give the same two ways for a carried hand query."""
    code = _read(SCRIPTS / "lib" / "check.py")
    assert "the query was attached by hand and carried over by rotm.py build: to keep the change, rebuild " in code
    assert ("and records its formula); to drop the change, change the query back in Excel's Power Query editor"
            in code)


# ----------------------------------------------------------------------------- request v6_P5 R2

def test_skill_md_split_bullets_name_the_carried_condition() -> None:
    text = _flat(_no_code(_read(SKILL / "SKILL.md")))
    assert ("A part in the second or third argument of IF: the helper carries the IF's condition, "
            "IF(<condition>,<part>,0) or IF(<condition>,0,<part>), so it is calculated only where the IF takes that "
            "branch (v6).") in text
    assert "split writes nothing and reports SPLIT-GUARD (error, exit 1), which names the IF and the part" in text
    assert "(such as ISERROR or CHOOSE) kept out of the result also gets SPLIT-GUARD" in text
    assert "(such as IF) kept out of the result" not in text

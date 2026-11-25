"""Tests of the v5 integrator's changes (the requests of the eight v5 packages, 2026-11-25). Owner: the integrator.

- Request v5_P2 R1 ([A-aera-9, C-sol-14, J-2] part 3 with [A-aera-3, C-sol-5] part 2): recompute.py reads a cell
  with a date or time format as the serial number Excel holds in it, so a check of two dates is computed again like
  any other equal check, and the data of every C-RECOMPUTE finding holds only JSON values.
- Request v5_P3 R2: one function hashes the body of a query file (check calls land.query_body_sha256).
- Request v5_P5 R2: references/google-sheets.md states the one-argument CONCAT and the rule formulas compat reports.
- Request v5_P6 R1: SKILL.md and README.md describe the v5 split pattern of a guarded lookup.
- Request v5_P8 R1: the test specification scripts/tests/data/build/model.spec.json holds the scale input in the
  fixed form that references/model-spec-format.md shows for it.
- The call of recompute.check_findings in audit.py (plan 5.3) is tested by
  test_v5_check.py::test_result_replaced_by_zero_is_c_recompute_in_audit; request v5_P4 R1 (the source note of a
  recorded total) by test_v5_land.py::test_later_build_takes_the_recorded_total.

Run from the skill folder: python -P -X utf8 -B -m pytest scripts/tests/test_v5_integrator.py -q -p no:cacheprovider
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import pytest

from lib import check, land, recompute

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
REFS = SKILL / "references"
TESTS = SCRIPTS / "tests"
HAS_PLUGIN = (PLUGIN / ".claude-plugin" / "plugin.json").is_file()
needs_plugin = pytest.mark.skipif(not HAS_PLUGIN, reason="skill copied without its plugin")


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ----------------------------------------------------------------------------- request v5_P2 R1

def _date_check_workbook(path: Path, result: int, expected: dt.datetime | None = None) -> tuple[Path, dict]:
    """A checks sheet laid out as build writes it, with one equal check of two dates (values, not formulas, so
    openpyxl reads them back as Excel would have saved them): D5 holds 2026-01-31 and E5 `expected` (default the
    same day), both with a date format; G5 the difference in days, H5 the tolerance 0, I5 the given result, C2 the
    master (the same value as I5)."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "checks"
    day = dt.datetime(2026, 1, 31)
    other = expected or day
    ws["B5"] = "start_is_month_end"
    ws["C5"] = "First month is a month end"
    for ref, value in (("D5", day), ("E5", other)):
        ws[ref] = value
        ws[ref].number_format = "yyyy-mm-dd"
    ws["G5"] = (day - other).days
    ws["H5"] = 0
    ws["I5"] = result
    ws["C2"] = result
    wb.save(path)
    manifest = {"checks": [{"id": "start_is_month_end", "kind": "equal", "result": "checks!I5",
                            "severity": "error"}],
                "master_check": "checks!C2",
                "sheets": [{"name": "checks", "type": "checks", "results": "I5:I5", "ids": "B5:B5"}]}
    return path, manifest


def test_date_operands_count_as_serial_numbers(tmp_path: Path) -> None:
    """A check of two equal dates that holds: no finding (v5 P2's measurement before the change: two findings
    'actual checks!D5 holds 2026-01-31 00:00:00, not a number')."""
    wb, man = _date_check_workbook(tmp_path / "dates.xlsx", result=0)
    assert recompute.check_findings(wb, man) == []
    cell = recompute._classify(dt.datetime(2026, 1, 31), "n")
    assert (cell.kind, cell.value) == ("number", 46053.0)
    assert recompute._classify(dt.time(12, 0), "n").value == 0.5


def test_date_check_with_a_wrong_result_is_one_json_finding(tmp_path: Path) -> None:
    """A check of two dates 28 days apart whose saved result says 0 (holds): one finding of part (a) with the
    serial numbers, and its data can be written as JSON (before the change: two part (b) findings whose data held
    datetime objects, and check ended with exit 4 in the JSON encoder)."""
    wb, man = _date_check_workbook(tmp_path / "dates.xlsx", result=0, expected=dt.datetime(2026, 2, 28))
    found = recompute.check_findings(wb, man)
    msgs = [f.message for f in found]
    assert msgs == ["check 'start_is_month_end' (checks!I5): the saved result is 0, but its operands give 1: "
                    "|46053 - 46081| = 28, tolerance 0"], msgs
    for f in found:
        json.dumps(f.to_dict())
    assert recompute._json_safe({"v": dt.datetime(2026, 1, 31), "n": [1.5, float("nan")]}) == \
        {"v": "2026-01-31 00:00:00", "n": [1.5, "nan"]}


# ----------------------------------------------------------------------------- request v5_P3 R2

def test_one_function_hashes_the_query_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """check._query_body_sha256 is land.query_body_sha256: the hash the attach records and the hash the refresh
    compares come from one function."""
    text = "// rotm-stamp: x\nlet\n    Source = 1\nin\n    Source\n"
    assert check._query_body_sha256(text) == land.query_body_sha256(text)
    monkeypatch.setattr(land, "query_body_sha256", lambda t: "one function")
    assert check._query_body_sha256(text) == "one function"


# ----------------------------------------------------------------------------- request v5_P5 R2

def test_google_sheets_states_the_one_argument_concat_and_the_rule_formulas() -> None:
    text = _flat(_read(REFS / "google-sheets.md"))
    assert "a `CONCAT` with one argument or with more than two arguments, each a single value, with `&`" in text
    assert "one argument `a` becomes `a&\"\"`" in text
    assert "exactly two single values is the form both products share" in text
    assert ("The same function catalogue is applied to the formulas of data validations and conditional-format "
            "rules: a function one product lacks is `X-FUNC` with the sheet, the range and the kind of rule; these "
            "formulas are reported, not rewritten.") in text


# ----------------------------------------------------------------------------- request v5_P6 R1

V5_SPLIT_TEST = "test_v5_tools.py::test_guarded_lookup_split_keeps_the_result_of_the_original"


def test_skill_md_describes_the_v5_split_pattern() -> None:
    text = _flat(_read(SKILL / "SKILL.md"))
    assert ("helper 1 holds the match position or 0, helper 2 a position INDEX can read, helper 3 holds 1 when the "
            "guard would replace the result at that position") in text
    assert "A lookup inside IFNA that sits inside IFERROR gets `SPLIT-GUARD`" in text
    assert "a helper that holds the match position or 0 and a host that returns the guard's value when the helper " \
           "is 0" not in text
    assert "The guarded-lookup split (v5)" in text and "The guarded-lookup split (v4)" not in text
    assert V5_SPLIT_TEST in text


@needs_plugin
def test_readme_describes_the_v5_split_pattern() -> None:
    text = _flat(_read(PLUGIN / "README.md"))
    assert "The guarded-lookup split (v5:" in text and "The guarded-lookup split (v4:" not in text
    assert V5_SPLIT_TEST in text


# ----------------------------------------------------------------------------- request v5_P8 R1

def test_build_test_spec_holds_the_fixed_scale_the_reference_shows() -> None:
    spec = json.loads(_read(TESTS / "data" / "build" / "model.spec.json"))
    scale = next(i for s in spec["sheets"] if s.get("type") == "inputs" for i in s["items"] if i["id"] == "scale")
    assert scale["label"] == "Display scale (fixed at 1000)"
    assert scale["valid"] == {"min": 1000, "max": 1000}
    assert scale["note"] == "Fixed: the target grid is typed in KRW thousand."
    ref = _read(REFS / "model-spec-format.md")
    worked = ref[ref.index("## Worked example"):]
    for text in ('"label": "Display scale (fixed at 1000)"', '"valid": {"min": 1000, "max": 1000}',
                 '"note": "Fixed: the target grid is typed in KRW thousand."'):
        assert text in worked, text


# ----------------------------------------------------------------------------- [J-1]: the ids of contract controls

def test_ids_section_names_the_contract_control_ids_build_refuses() -> None:
    """model-spec-format.md "Ids" states which ids an input may not have because build gives them to contract
    controls (request v5_P2 R2 item 1), with the message build.py prints."""
    ref = _read(REFS / "model-spec-format.md")
    ids = _flat(ref[ref.index("## Ids"):ref.index("## Inputs")])
    code = _flat(_read(SCRIPTS / "lib" / "build.py"))
    message = "is the id build gives a contract control of table"
    assert message in code
    assert f"input id '<id>' {message} '<t>' (from contracts/<t>.contract.json <expect path>)" in ids
    for suffix in ("_rows_exact", "_rows_min", "_rows_max", "_tol_<column>", "_key_repeats", "_known_nulls_<column>"):
        assert f"`<t>{suffix}`" in ids, suffix
        assert f"<table>{suffix}" in code, suffix
    assert "A block or a check may have such an id." in ids


# ----------------------------------------------------------------------------- [A-aera-9, C-sol-14, J-2] part (1)

# The checks in the product form (a percentage times its denominator against the numerator): both sides are amounts.
PRODUCT_CHECKS = [("examples/sales_vs_target", ("ytd_pct_ties", "pct_grid_ties", "var_pct_all")),
                  ("templates/budget_vs_actual", ("ytd_pct_ties",)),
                  ("templates/rolling_forecast", ("fy_pct_ties",)),
                  ("templates/ar_aging", ("pct_ties",))]


@pytest.mark.parametrize("rel,ids", PRODUCT_CHECKS, ids=[p[0].split("/")[-1] for p in PRODUCT_CHECKS])
def test_product_form_checks_show_amounts_not_percentages(rel: str, ids: tuple[str, ...]) -> None:
    """The shipped workbooks show Actual, Expected and Difference of each product-form check in an amount format, not
    in the percentage format of the percentage it reads first (seen in the v5 renders before the fix: -436340.0 %
    for an amount of -4,363.40 KRW thousand)."""
    import openpyxl
    from lib import manifest
    folder = SKILL / rel if rel.startswith("examples") else PLUGIN / rel
    if not (folder / "model.xlsx").is_file():
        pytest.skip("the skill folder was copied without its plugin, so PLUGIN/templates is absent")
    spec = json.loads(_read(folder / "model.spec.json"))
    checks = {c["id"]: c for s in spec["sheets"] for c in s.get("checks") or []}
    man = manifest.load_manifest(folder / "model.xlsx")
    wb = openpyxl.load_workbook(folder / "model.xlsx")
    try:
        for cid in ids:
            assert checks[cid].get("format") in ("amount", "amount1", "amount2"), (rel, cid, checks[cid])
            entry = next(c for c in man["checks"] if c["id"] == cid)
            sheet, cell = entry["result"].split("!")
            row = wb[sheet][cell.replace("$", "")].row
            for col in ("D", "E", "G"):
                fmt = wb[sheet][f"{col}{row}"].number_format
                assert "%" not in fmt, (rel, cid, col, fmt)
    finally:
        wb.close()


# ----------------------------------------------------------------------------- [A-aera-3, C-sol-5]: command skills

@needs_plugin
def test_audit_and_check_skills_name_the_recomputation_rule() -> None:
    """The audit and check command skills say that their command reports C-RECOMPUTE (audit.py RULES holds it, and
    both commands call recompute.check_findings)."""
    from lib import audit
    assert "C-RECOMPUTE" in audit.RULES
    audit_skill = _flat(_read(PLUGIN / "skills" / "audit" / "SKILL.md"))
    check_skill = _flat(_read(PLUGIN / "skills" / "check" / "SKILL.md"))
    assert "a result its own operands do not give" in audit_skill
    assert ("With a manifest, on the saved values: `C-RECOMPUTE` (error) when a check's saved result is not the one "
            "its own operand cells give") in audit_skill
    assert ("After the recalculation it computes every check and the master check again from their own operand "
            "cells; a saved result they do not give is `C-RECOMPUTE`") in check_skill


# ----------------------------------------------------------------------------- checks.md, date operands

def test_checks_reference_states_how_a_date_operand_is_read() -> None:
    text = _flat(_read(REFS / "checks.md"))
    assert ("A cell with a date or time format (the Actual and Expected cells of a check of two dates) counts as the "
            "serial number Excel holds in it") in text

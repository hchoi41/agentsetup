"""Tests of the v8 change [C-sol-1] of package P6 (round 7 verdict, 2026-11-26). Owner: P6.

[C-sol-1]: a label of a conditional sum or count that is also a value of its criteria range, and that Excel's
criteria matching does not match with itself (the texts TRUE and FALSE in any case, which Excel reads as logical
values; a label taken from a cell that starts with =, < or >), makes build and check warn D-CRITERIA with the reason
key self_mismatch: Excel leaves the rows with this label out. references/helper-patterns.md, "A label that Excel's
criteria matching does not match with itself", gives the formula-only way: a helper key with a fixed prefix on both
sides.

- The starter with the text category TRUE at +300 in January and -300 in February, and East at +200 in January:
  build --strict does not exit 0 and D-CRITERIA names TRUE and its first raw row. With Excel, check --strict does not
  exit 0 and names the same, while every check holds and Excel's sums for TRUE are 0 and 0.
- The same data in the helper-key form of the section's JSON: build, audit and check --strict exit 0 (check with
  Excel), and every category-month sum equals the sum computed in Python from the CSV.
- Labels from cells that start with < or > are reported; a criterion typed in the formula that starts with an
  operator is not.
- Ordinary labels and the six shipped models give no self_mismatch finding.

Run from the skill folder: python -P -X utf8 -B -m pytest scripts/tests/test_v8_criteria.py -q -p no:cacheprovider
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from lib import build, common, criteria, manifest
from test_v4_check import starter
import test_check as tc

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
REFS = SKILL / "references"
PARENT = "## Labels that Excel's criteria matching merges"
SECTION = "### A label that Excel's criteria matching does not match with itself"
NAMES = (("sales.", "lines."), ("sales_", "lines_"), ("regions", "categories"), ("region", "category"))
NO_EXCEL = {"ROTM_NO_EXCEL": "1"}

# The reviewer's case (C-sol-1): TRUE nets to zero over the months, so every check holds without its rows.
ROWS = [
    {"line_id": "L0001", "line_date": "2026-01-10", "category": "East", "amount": "200"},
    {"line_id": "L0002", "line_date": "2026-01-12", "category": "TRUE", "amount": "300"},
    {"line_id": "L0003", "line_date": "2026-02-12", "category": "TRUE", "amount": "-300"},
]
CATEGORIES = ["East", "TRUE"]


# ----------------------------------------------------------------------------- helpers

def _section() -> str:
    text = (REFS / "helper-patterns.md").read_text(encoding="utf-8")
    assert PARENT in text, f"helper-patterns.md has no section {PARENT!r}"
    parent = text.split(PARENT, 1)[1].split("\n## ", 1)[0]
    assert SECTION in parent, f"{SECTION!r} is not under {PARENT!r}"
    return parent.split(SECTION, 1)[1].split("\n### ", 1)[0]


def _section_json() -> dict[str, Any]:
    blocks = re.findall(r"```json\n(.*?)```", _section(), flags=re.S)
    # v9 (P3, A-aera-1 and C-sol-2): the section also gives the helper keys for a label that holds a tilde (its
    # second JSON block, tested by test_v9_criteria.py); the helper key with a fixed prefix is the first
    assert len(blocks) == 2, f"the section holds {len(blocks)} JSON blocks; two are expected"
    return json.loads(blocks[0])


def _for_starter(obj: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(obj)
    for old, new in NAMES:
        text = text.replace(old, new)
    return json.loads(text)


def _helper_key_edit(spec: dict[str, Any]) -> None:
    """The section's JSON in the starter: its helper column on h_lines, its cell in the block spend."""
    pattern = _for_starter(_section_json())
    for sheet in spec["sheets"]:
        if sheet["name"] == "h_lines":
            sheet["columns"].extend(pattern["helper_columns"])
        if sheet["name"] == "calc":
            next(b for b in sheet["blocks"] if b["id"] == "spend")["cell"] = pattern["block"]["cell"]


def _first_raw_row(model: Path, value: str) -> int:
    """The first row of raw_lines whose category is `value` (read from the built workbook). The workbook is closed
    before the function returns: a read-only workbook keeps its file open, and Excel cannot then open it."""
    import openpyxl
    book = openpyxl.load_workbook(model / "model.xlsx", read_only=True)
    try:
        rows = list(book["raw_lines"].iter_rows(values_only=True))
    finally:
        book.close()
    header = list(rows[0])
    for r, row in enumerate(rows[1:], 2):
        if row[header.index("category")] == value:
            return r
    raise AssertionError(f"no raw row holds {value!r}")


def _criteria_findings(report: Path) -> list[dict[str, Any]]:
    rep = json.loads(report.read_text(encoding="utf-8"))
    return [f for f in rep["findings"] if f["rule"] == "D-CRITERIA"]


def _self_message(label: str, row: int) -> str:
    return (f"the label '{label}' is also a value of raw_lines column category (first row {row}), and Excel's "
            f"criteria matching does not match '{label}' with itself")


def _calc_sums(model: Path) -> dict[tuple[str, dt.date], Any]:
    """Values Excel saved in the block spend, by (row label, month end); the manifest names the cells."""
    import openpyxl
    man = json.loads((model / "model.manifest.json").read_text(encoding="utf-8"))
    blk = next(b for s in man["sheets"] if s["name"] == "calc" for b in s["blocks"] if b["id"] == "spend")
    c1, r1, c2, r2 = common.range_bounds(blk["body"])
    lc = common.range_bounds(blk["labels"])[0]
    ws = openpyxl.load_workbook(model / "model.xlsx", data_only=True)["calc"]
    out = {}
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            month = ws.cell(blk["header_row"], c).value
            out[(ws.cell(r, lc).value, month.date() if isinstance(month, dt.datetime) else month)] = ws.cell(r, c).value
    return out


def _python_sums(rows: list[dict[str, str]], months: list[dt.date]) -> dict[tuple[str, dt.date], Decimal]:
    out: dict[tuple[str, dt.date], Decimal] = defaultdict(Decimal)
    for cat in CATEGORIES:
        for m in months:
            out[(cat, m)] = Decimal(0)
    for row in rows:
        d = dt.date.fromisoformat(row["line_date"])
        end = next(m for m in months if (m.year, m.month) == (d.year, d.month))
        out[(row["category"], end)] += Decimal(row["amount"])
    return dict(out)


def _check(model: Path, report: Path) -> Any:
    proc = tc.run_cli("check", str(model), "--strict", "--json", str(report))
    if proc.returncode == 5:
        pytest.skip("Excel busy (exit 5): run again later")
    return proc


def _shipped() -> list[Path]:
    parents = [SKILL / "examples", SKILL / "templates", PLUGIN / "templates"]
    return [d for p in parents if p.is_dir() for d in sorted(p.iterdir()) if (d / "model.xlsx").is_file()]


# ============================================================================= the TRUE label

def test_true_label_fails_build_without_excel(outside_tmp: Path) -> None:
    """[C-sol-1] (no Excel): build --strict does not exit 0; D-CRITERIA names TRUE, its first raw row, the reason
    self_mismatch and the helper-key fix. v7 exited 0 with no finding."""
    root = starter(outside_tmp / "m", rows=ROWS, categories=CATEGORIES)
    report = outside_tmp / "build.json"
    proc = tc.run_cli("build", str(root), "--strict", "--no-power-query", "--json", str(report), env_extra=NO_EXCEL)
    assert proc.returncode == 1, (proc.stdout[-3000:], proc.stderr[-2000:])
    row = _first_raw_row(root, "TRUE")
    found = _criteria_findings(report)
    assert len(found) == 1, found
    f = found[0]
    assert _self_message("TRUE", row) in f["message"], f["message"]
    assert "Excel reads the criterion TRUE or FALSE as a logical value, which does not match the text" in f["message"]
    assert f["message"].endswith("so SUMIFS leaves out the rows with this label"), f["message"]
    assert '"k_"&category' in f["fix"] and "A label that Excel's criteria matching does not match with itself" \
        in f["fix"], f["fix"]
    assert f["severity"] == "warning"
    wb = root / "model.xlsx"
    data = [x.data for x in criteria.criteria_findings(wb, manifest.load_manifest(wb))]
    assert [(d["label"], d["reason"], d["row"], d["sheet"], d["column"]) for d in data] == \
        [("TRUE", "self_mismatch", row, "raw_lines", "category")]


@pytest.mark.excel
def test_true_label_whose_amounts_net_to_zero_fails_build_and_check(outside_tmp: Path) -> None:
    """[C-sol-1] (Excel): build --strict does not exit 0 and names TRUE and its first raw row; check --strict does
    not exit 0 and names the same, while every check holds and Excel's sums for TRUE are 0 and 0 (Python: +300 and
    -300): Excel leaves the rows out."""
    root = starter(outside_tmp / "m", rows=ROWS, categories=CATEGORIES)
    report = outside_tmp / "build.json"
    proc = tc.run_cli("build", str(root), "--strict", "--no-power-query", "--json", str(report), env_extra=NO_EXCEL)
    assert proc.returncode == 1, (proc.stdout[-3000:], proc.stderr[-2000:])
    row = _first_raw_row(root, "TRUE")
    assert [_self_message("TRUE", row) in f["message"] for f in _criteria_findings(report)] == [True]
    report = outside_tmp / "check.json"
    proc = _check(root, report)
    assert proc.returncode == 1, (proc.stdout[-3000:], proc.stderr[-2000:])
    rep = json.loads(report.read_text(encoding="utf-8"))
    errors = [(f["rule"], f["message"]) for f in rep["findings"] if f["severity"] == "error"]
    assert errors == [], errors
    assert [_self_message("TRUE", row) in f["message"] for f in _criteria_findings(report)] == [True]
    got = _calc_sums(root)
    jan, feb = dt.date(2026, 1, 31), dt.date(2026, 2, 28)
    assert (got[("TRUE", jan)], got[("TRUE", feb)]) == (0, 0), got
    assert got[("East", jan)] == 200, got
    want = _python_sums(ROWS, [jan, feb, dt.date(2026, 3, 31)])
    assert (want[("TRUE", jan)], want[("TRUE", feb)]) == (300, -300)


@pytest.mark.excel
def test_helper_key_form_passes_and_equals_python(outside_tmp: Path) -> None:
    """[C-sol-1] (Excel): the section's JSON on the same data: build --strict, audit --strict and check --strict exit
    0, and each category-month sum equals the sum computed in Python from the CSV."""
    root = starter(outside_tmp / "m", rows=ROWS, categories=CATEGORIES, spec_edit=_helper_key_edit)
    proc = tc.run_cli("build", str(root), "--strict", "--no-power-query", env_extra=NO_EXCEL)
    assert proc.returncode == 0, (proc.stdout[-3000:], proc.stderr[-2000:])
    proc = tc.run_cli("audit", str(root), "--strict", "--no-record", env_extra=NO_EXCEL)
    assert proc.returncode == 0, (proc.stdout[-3000:], proc.stderr[-2000:])
    report = outside_tmp / "check.json"
    proc = _check(root, report)
    assert proc.returncode == 0, (proc.stdout[-3000:], proc.stderr[-2000:])
    assert json.loads(report.read_text(encoding="utf-8"))["data"]["recalculated"] is True
    got = _calc_sums(root)
    months = sorted({m for _c, m in got})
    want = _python_sums(ROWS, months)
    assert set(got) == set(want), (sorted(got), sorted(want))
    for key, value in want.items():
        assert Decimal(str(got[key])) == value, (key, got[key], value)


def test_section_explains_the_two_cases_and_the_helper_key() -> None:
    """[C-sol-1]: the subsection names both cases, says why the rows are left out, gives the helper key on both
    sides and the reason key; its JSON holds the helper column and the block cell."""
    sec = _section()
    for words in ("TRUE", "FALSE", "logical value", "`=`, `<` or `>`", "leaves out", '"k_"&', "self_mismatch",
                  "D-CRITERIA", "not typed in the formula"):
        assert words in sec, words
    obj = _section_json()
    assert obj["helper_columns"][0]["expr"] == '"k_"&{this:region}'
    assert '"k_"&{row}' in obj["block"]["cell"] and "{h:sales.region_key}" in obj["block"]["cell"]


# ============================================================================= labels from a cell with an operator

def test_label_from_a_cell_that_starts_with_a_comparison_is_reported(tmp_path: Path) -> None:
    """[C-sol-1] (no Excel): the labels <100 and >Z come from the inputs list and are also values of the category
    column: each gets one D-CRITERIA with reason self_mismatch and the comparison reason. A criterion "<100" typed in
    a check formula is not compared (an operator is meant)."""
    rows = [{"line_id": "L0001", "line_date": "2026-01-10", "category": "East", "amount": "200"},
            {"line_id": "L0002", "line_date": "2026-01-12", "category": "<100", "amount": "50"},
            {"line_id": "L0003", "line_date": "2026-02-12", "category": ">Z", "amount": "70"}]

    def typed_check(spec: dict[str, Any]) -> None:
        checks = next(s for s in spec["sheets"] if s["type"] == "checks")["checks"]
        checks.append({"id": "typed_operator", "kind": "equal", "label": "Numbers below 100 in a text column",
                       "actual": 'COUNTIFS({h:lines.category},"<100")', "expected": "0"})

    root = starter(tmp_path / "m", rows=rows, categories=["East", "<100", ">Z"], spec_edit=typed_check)
    rep = build.build(root / "model.spec.json", power_query=False)
    assert rep.counts()["errors"] == 0, [f.line() for f in rep.findings]
    wb = root / "model.xlsx"
    found = criteria.criteria_findings(wb, manifest.load_manifest(wb))
    got = {(f.data["label"], f.data["reason"], f.data["row"], f.sheet) for f in found}
    assert got == {("<100", "self_mismatch", 3, "calc"), (">Z", "self_mismatch", 4, "calc")}, \
        [f.message for f in found]
    for f in found:
        assert "a criterion that starts with =, < or > is read as a comparison" in f.message, f.message
        assert f.message.startswith(f"block spend (calc!{f.cell}): ") and f.severity == "warning", f.message
    assert [f for f in rep.findings if f.rule == "D-CRITERIA"], [f.line() for f in rep.findings]


# ============================================================================= no finding

def test_ordinary_labels_and_shipped_models_give_no_self_mismatch(tmp_path: Path) -> None:
    """[C-sol-1] (no Excel): labels that hold TRUE or an operator inside the text, and the six shipped workbooks with
    their manifests (on the formulas and on the saved values), give no self_mismatch finding."""
    rows = [{"line_id": "L0001", "line_date": "2026-01-10", "category": "East", "amount": "200"},
            {"line_id": "L0002", "line_date": "2026-01-12", "category": "TRUE up", "amount": "300"},
            {"line_id": "L0003", "line_date": "2026-02-12", "category": "Net<0", "amount": "-300"},
            {"line_id": "L0004", "line_date": "2026-02-13", "category": "Not FALSE", "amount": "10"}]
    root = starter(tmp_path / "ordinary", rows=rows, categories=["East", "TRUE up", "Net<0", "Not FALSE"])
    rep = build.build(root / "model.spec.json", power_query=False)
    assert [f.line() for f in rep.findings if f.rule == "D-CRITERIA"] == []
    folders = _shipped()
    # the skill folder copied alone (selftest step f02) holds the worked example and the starter only
    has_plugin = (PLUGIN / ".claude-plugin" / "plugin.json").is_file()
    assert len(folders) == (6 if has_plugin else 2), folders
    for folder in folders:
        copy = tmp_path / folder.name
        shutil.copytree(folder, copy, ignore=shutil.ignore_patterns("reports", "lift", "__pycache__"))
        wb = copy / "model.xlsx"
        man = manifest.load_manifest(wb)
        for saved in (False, True):
            found = [f for f in criteria.criteria_findings(wb, man, saved_values=saved)
                     if f.data.get("reason") == "self_mismatch"]
            assert found == [], (folder.name, saved, [f.message for f in found])


def test_self_mismatch_follows_excel_criteria_match() -> None:
    """[C-sol-1]: the two cases are labels that excel_criteria_match does not match with themselves; ordinary
    labels, numbers and wildcard labels match themselves."""
    m = criteria.excel_criteria_match
    for label in ("TRUE", "false", "True", "<100", ">Z", "=x", "=", ">=5"):
        assert not m(label, label), label
    for label in ("East", "TRUE up", "Net<0", "001", "S*", "A?", "<z"):
        assert m(label, label), label

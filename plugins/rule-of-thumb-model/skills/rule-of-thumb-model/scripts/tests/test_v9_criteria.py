"""Tests of the v9 change [A-aera-1, C-sol-2] of package P3 (round 8 verdict, 2026-11-26). Owner: P3.

[A-aera-1, C-sol-2]: build and check give D-CRITERIA with the reason key self_mismatch for every label that is a value
of its criteria range and that Excel's criteria matching does not match with itself (excel_criteria_match of the
label with itself is false), not only for the two cases of v8. A label that holds ~* or ~? has its own reason text;
any other case has a general reason. The finding's data field case is logical, operator, tilde or other. The fix
names the pattern that fits: the helper key with a fixed prefix for TRUE, FALSE and the operators; for a label that
holds ~ together with * or ?, helper keys on both sides that replace ~, * and ? by fixed texts, one SUBSTITUTE per
helper column (references/helper-patterns.md, "A label that Excel's criteria matching does not match with itself").

- Excel 16 reads a ~ in a criterion that holds * or ? as the sign that makes the next character plain, whatever it is,
  and drops a ~ at the end; without * or ? a ~ is an ordinary character. excel_criteria_match follows it (81 pairs).
- The starter with A~* at +300 in January and -300 in February, A~? at +400 and -400, and East at +200: build --strict
  and check --strict (Excel) do not exit 0, and each finding names the label and its first raw row.
- The same data in the helper-key form of the section's second JSON block: build, audit and check --strict exit 0,
  and every category-month sum equals the sum computed in Python from the CSV.
- A label with ~ and no wildcard, ordinary labels and the six shipped models give no self_mismatch finding.

Run from the skill folder: python -P -X utf8 -B -m pytest scripts/tests/test_v9_criteria.py -q -p no:cacheprovider
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

from lib import build, common, criteria, formula, manifest
from test_v4_check import starter
import test_check as tc

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
REFS = SKILL / "references"
PARENT = "## Labels that Excel's criteria matching merges"
SECTION = "### A label that Excel's criteria matching does not match with itself"
SUBSECTION = "#### Helper keys for a label that holds a tilde"
NAMES = (("sales.", "lines."), ("sales_", "lines_"), ("regions", "categories"), ("region", "category"))
NO_EXCEL = {"ROTM_NO_EXCEL": "1"}
TILDE_REASON = "Excel reads ~* and ~? in a criterion as a literal * and ?, so the label does not match itself"
OTHER_REASON = "Excel's criteria matching does not match this text with itself"
SIGNS_FIX = ("give both sides helper keys that replace ~, * and ? by fixed texts, one SUBSTITUTE per helper column "
             '(references/helper-patterns.md, "A label that Excel\'s criteria matching does not match with itself")')

# The reviewers' case (A-aera-1, C-sol-2): both labels net to zero over the months, so every check holds without them.
ROWS = [
    {"line_id": "L0001", "line_date": "2026-01-10", "category": "East", "amount": "200"},
    {"line_id": "L0002", "line_date": "2026-01-12", "category": "A~*", "amount": "300"},
    {"line_id": "L0003", "line_date": "2026-02-12", "category": "A~*", "amount": "-300"},
    {"line_id": "L0004", "line_date": "2026-01-14", "category": "A~?", "amount": "400"},
    {"line_id": "L0005", "line_date": "2026-02-14", "category": "A~?", "amount": "-400"},
]
CATEGORIES = ["East", "A~*", "A~?"]

# Measured with Excel 16 on the build PC (2026-11-26): (criterion, cell value) -> COUNTIF over the one cell. Labels
# against themselves first, then against other values. COUNTIFS and SUMIFS gave the same answer on every pair.
SELF_LABELS = {
    "A~*": 0, "A~?": 0, "A~~": 1, "A~~*": 1, "A~~?": 0, "A~b": 1, "A~b*": 0, "A~b?": 0, "~": 1, "~~": 1, "~~~": 1,
    "~*": 0, "~?": 0, "*~": 1, "?~": 0, "A*~": 1, "A~*B": 0, "A~?B": 0, "A~**": 0, "A~??": 0, "A~~~*": 0, "A*": 1,
    "A?": 1, "*": 1, "?": 1, "A~": 1, "A~~B": 1, "~A": 1, "~A*": 0, "A~ *": 0, "A~1*": 0, "A~*~?": 0, "a~*": 0,
    "A~-": 1, "A~-*": 0, "1~*": 0, "~1": 1, "A_t__s_": 1, "<A~*": 1, "TRUE~*": 0,
}
OTHER_PAIRS = [
    ("A~*", "A*", 1), ("A~*", "AB", 0), ("A~?", "A?", 1), ("A~?", "AB", 0), ("A~~", "A~", 0), ("A~~*", "A~", 1),
    ("A~~*", "A~x", 1), ("A~~*", "A~~x", 1), ("A~~*", "A~*", 1), ("A~~?", "A~x", 1), ("A~~?", "A~~", 1),
    ("A~b*", "A~bc", 0), ("A~b*", "Abc", 1), ("A~b?", "A~bc", 0), ("A~b?", "Abc", 1), ("~~", "~", 0), ("~~~", "~~", 0),
    ("~~~", "~", 0), ("A~~B", "A~B", 0), ("~A*", "Ax", 1), ("~A*", "~Ax", 0), ("A~ *", "A x", 1), ("A~ *", "A~ x", 0),
    ("A~1*", "A1", 1), ("A~1*", "A~1", 0), ("A~*~?", "A*?", 1), ("A~**", "A*x", 1), ("A~??", "A?x", 1),
    ("A~~~*", "A~*", 1), ("A~-*", "A-x", 1), ("A~-*", "A~-x", 0), ("1~*", "1*", 1), ("~1", 1, 0), ("a~*", "A*", 1),
    ("A~*B", "A*B", 1), ("A~?B", "A?B", 1), ("*~", "x~", 1), ("*~", "x", 1), ("?~", "x", 1), ("A*~", "Ax", 1),
    ("A*~", "Ax~", 1),
]
PAIRS = [(c, c, k) for c, k in SELF_LABELS.items()] + OTHER_PAIRS


# ----------------------------------------------------------------------------- helpers

def _section() -> str:
    text = (REFS / "helper-patterns.md").read_text(encoding="utf-8")
    assert PARENT in text, f"helper-patterns.md has no section {PARENT!r}"
    parent = text.split(PARENT, 1)[1].split("\n## ", 1)[0]
    assert SECTION in parent, f"{SECTION!r} is not under {PARENT!r}"
    return parent.split(SECTION, 1)[1].split("\n### ", 1)[0]


def _subsection() -> str:
    sec = _section()
    assert SUBSECTION in sec, f"{SUBSECTION!r} is not in {SECTION!r}"
    return sec.split(SUBSECTION, 1)[1].split("\n#### ", 1)[0]


def _pattern_json() -> dict[str, Any]:
    blocks = re.findall(r"```json\n(.*?)```", _subsection(), flags=re.S)
    assert len(blocks) == 1, f"the subsection holds {len(blocks)} JSON blocks; one is expected"
    return json.loads(blocks[0])


def _for_starter(obj: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(obj)
    for old, new in NAMES:
        text = text.replace(old, new)
    return json.loads(text)


def _helper_keys_edit(spec: dict[str, Any]) -> None:
    """The subsection's JSON in the starter: helper columns on h_lines, the key grids before the block spend, the
    checks on the checks sheet and the new cell of spend."""
    pattern = _for_starter(_pattern_json())
    for sheet in spec["sheets"]:
        if sheet["name"] == "h_lines":
            sheet["columns"].extend(pattern["helper_columns"])
        if sheet["name"] == "calc":
            next(b for b in sheet["blocks"] if b["id"] == "spend")["cell"] = pattern["block"]["cell"]
            sheet["blocks"][0:0] = pattern["blocks"]
        if sheet["type"] == "checks":
            sheet["checks"].extend(pattern["checks"])


def _first_raw_row(model: Path, value: str) -> int:
    """The first row of raw_lines whose category is `value`. The workbook is closed before the function returns."""
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
            f"criteria matching does not match '{label}' with itself ({TILDE_REASON}), so SUMIFS leaves out the rows "
            f"with this label")


def _assert_tilde_findings(found: list[dict[str, Any]], root: Path) -> None:
    rows = {label: _first_raw_row(root, label) for label in ("A~*", "A~?")}
    assert len(found) == 2, [f["message"] for f in found]
    for label, row in rows.items():
        hit = [f for f in found if _self_message(label, row) in f["message"]]
        assert len(hit) == 1, (label, row, [f["message"] for f in found])
        assert hit[0]["fix"] == SIGNS_FIX and hit[0]["severity"] == "warning", hit[0]


def _calc_sums(model: Path) -> dict[tuple[str, dt.date], Any]:
    """Values Excel saved in the block spend, by (row label, month end); the manifest names the cells."""
    import openpyxl
    man = json.loads((model / "model.manifest.json").read_text(encoding="utf-8"))
    blk = next(b for s in man["sheets"] if s["name"] == "calc" for b in s["blocks"] if b["id"] == "spend")
    c1, r1, c2, r2 = common.range_bounds(blk["body"])
    lc = common.range_bounds(blk["labels"])[0]
    book = openpyxl.load_workbook(model / "model.xlsx", data_only=True)
    ws = book["calc"]
    out = {}
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            month = ws.cell(blk["header_row"], c).value
            out[(ws.cell(r, lc).value, month.date() if isinstance(month, dt.datetime) else month)] = ws.cell(r, c).value
    book.close()
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


# ============================================================================= how Excel reads ~

def test_excel_criteria_match_reads_tilde_as_excel_does() -> None:
    """[A-aera-1, C-sol-2] (no Excel): excel_criteria_match gives Excel 16's answer on the 81 measured pairs. In a
    criterion that holds * or ?, ~ makes the next character plain whatever it is (~~ a plain ~, ~b the letter b) and
    a ~ at the end is dropped; without * or ? a ~ is an ordinary character. v8 kept the ~ before a character other
    than * and ? (A~b* matched A~bc, Excel: Abc)."""
    wrong = [(c, v, k) for c, v, k in PAIRS if criteria.excel_criteria_match(c, v) != bool(k)]
    assert wrong == [], wrong
    assert len(PAIRS) == 81


@pytest.mark.excel
def test_tilde_rules_follow_excel_countif(tmp_path: Path) -> None:
    """[A-aera-1, C-sol-2] (Excel): column A holds the cell value, column B the criterion, column C
    =COUNTIF(A<r>,B<r>); every result equals the table PAIRS and excel_criteria_match."""
    import openpyxl

    from lib import excel
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "m"
    for i, (crit, val, _k) in enumerate(PAIRS, 1):
        for col, v in (("A", val), ("B", crit)):
            ws[f"{col}{i}"] = v
            if isinstance(v, str):
                ws[f"{col}{i}"].data_type = "s"            # a text that starts with = or < stays text
        ws[f"C{i}"] = f"=COUNTIF(A{i},B{i})"
    path = tmp_path / "tilde.xlsx"
    wb.save(path)
    n = len(PAIRS)
    res = excel.recalc(path, read=[f"m!C1:C{n}"])
    if not res.ok and any("busy" in str(e).lower() for e in res.errors):
        pytest.skip(f"Excel busy: {res.errors}")
    assert res.ok, res.errors
    got = [row[0] for row in res.values[f"m!C1:C{n}"]]
    wrong = [(c, v, k, g) for (c, v, k), g in zip(PAIRS, got) if g != k]
    assert wrong == [], wrong
    differ = [(c, v, g) for (c, v, _k), g in zip(PAIRS, got) if criteria.excel_criteria_match(c, v) != bool(g)]
    assert differ == [], differ


# ============================================================================= the four cases

def test_self_reason_gives_the_four_cases() -> None:
    """[A-aera-1, C-sol-2] (no Excel): _self_reason gives a reason for every label that excel_criteria_match does
    not match with itself, and None for every label that it matches; _self_case names the case; the fix names the
    pattern that fits."""
    cases = {"TRUE": "logical", "false": "logical", "<100": "operator", "=x": "operator", ">=5": "operator",
             "A~*": "tilde", "A~?": "tilde", "~*": "tilde", "A~*B": "tilde", "a~*": "tilde", "A~*~?": "tilde",
             "A~b*": "other", "A~~?": "other", "?~": "other", "~A*": "other", "=A~*": "operator"}
    for label, case in cases.items():
        assert not criteria.excel_criteria_match(label, label), label
        assert criteria._self_case(label) == case, (label, criteria._self_case(label))
        assert criteria._self_reason(label) == criteria.SELF_REASONS[case], label
    assert criteria.SELF_REASONS["tilde"] == TILDE_REASON and criteria.SELF_REASONS["other"] == OTHER_REASON
    for label in ("East", "A~b", "A~~", "~", "A~", "Net~1", "*~", "A*", "A?", "A~~*", "001", "<z", "TRUE up"):
        assert criteria.excel_criteria_match(label, label), label
        assert criteria._self_case(label) is None and criteria._self_reason(label) is None, label
    prefix = criteria._self_fix("TRUE", "category", "$B6")
    assert '"k_"&category' in prefix and "fixed prefix" in prefix
    assert criteria._self_fix("<100", "category", "$B6") == prefix
    for label in ("A~*", "A~?", "A~b*", "A~~?", "?~", "=A~*"):
        assert criteria._self_fix(label, "category", "$B6") == SIGNS_FIX, label


def test_each_case_gets_its_reason_case_and_pattern(tmp_path: Path) -> None:
    """[A-aera-1, C-sol-2] (no Excel): labels TRUE, <100, A~* and A~b* from the inputs list, each also a value of the
    category column, give one D-CRITERIA each with the reason key self_mismatch, the data field case (logical,
    operator, tilde, other), the reason text of the case and the fix that fits. v8 gave the first two only."""
    rows = [{"line_id": "L0001", "line_date": "2026-01-10", "category": "East", "amount": "200"},
            {"line_id": "L0002", "line_date": "2026-01-12", "category": "TRUE", "amount": "50"},
            {"line_id": "L0003", "line_date": "2026-01-13", "category": "<100", "amount": "60"},
            {"line_id": "L0004", "line_date": "2026-02-12", "category": "A~*", "amount": "70"},
            {"line_id": "L0005", "line_date": "2026-02-13", "category": "A~b*", "amount": "80"}]
    root = starter(tmp_path / "m", rows=rows, categories=["East", "TRUE", "<100", "A~*", "A~b*"])
    rep = build.build(root / "model.spec.json", power_query=False)
    assert rep.counts()["errors"] == 0, [f.line() for f in rep.findings]
    wb = root / "model.xlsx"
    found = criteria.criteria_findings(wb, manifest.load_manifest(wb))
    got = {f.data["label"]: (f.data["reason"], f.data["case"], f.data["row"]) for f in found}
    assert got == {"TRUE": ("self_mismatch", "logical", 3), "<100": ("self_mismatch", "operator", 4),
                   "A~*": ("self_mismatch", "tilde", 5), "A~b*": ("self_mismatch", "other", 6)}, \
        [f.message for f in found]
    for f in found:
        case = f.data["case"]
        assert f"({criteria.SELF_REASONS[case]})" in f.message, f.message
        assert f.message.endswith("so SUMIFS leaves out the rows with this label"), f.message
        if case in ("logical", "operator"):
            assert '"k_"&category' in f.fix, f.fix
        else:
            assert f.fix == SIGNS_FIX, f.fix
    assert len([f for f in rep.findings if f.rule == "D-CRITERIA"]) == 4, [f.line() for f in rep.findings]


# ============================================================================= the reviewers' case

def test_tilde_labels_fail_build_without_excel(outside_tmp: Path) -> None:
    """[A-aera-1, C-sol-2] (no Excel): build --strict does not exit 0; D-CRITERIA names A~* and A~?, their first raw
    rows, the tilde reason and the helper keys that replace ~, * and ?. v8 exited 0 with no finding."""
    root = starter(outside_tmp / "m", rows=ROWS, categories=CATEGORIES)
    report = outside_tmp / "build.json"
    proc = tc.run_cli("build", str(root), "--strict", "--no-power-query", "--json", str(report), env_extra=NO_EXCEL)
    assert proc.returncode == 1, (proc.stdout[-3000:], proc.stderr[-2000:])
    _assert_tilde_findings(_criteria_findings(report), root)


@pytest.mark.excel
def test_tilde_wildcard_labels_netting_to_zero_fail_build_and_check(outside_tmp: Path) -> None:
    """[A-aera-1, C-sol-2] (Excel): the starter with A~* (+300, -300), A~? (+400, -400) and East (+200): build --strict
    does not exit 0 and check --strict does not exit 0; each D-CRITERIA names the label and its first raw row, while
    every check holds and Excel's sums for both labels are 0 in January and February (Python: +300/-300 and
    +400/-400): Excel leaves the rows out."""
    root = starter(outside_tmp / "m", rows=ROWS, categories=CATEGORIES)
    report = outside_tmp / "build.json"
    proc = tc.run_cli("build", str(root), "--strict", "--no-power-query", "--json", str(report), env_extra=NO_EXCEL)
    assert proc.returncode != 0, (proc.stdout[-3000:], proc.stderr[-2000:])
    _assert_tilde_findings(_criteria_findings(report), root)
    report = outside_tmp / "check.json"
    proc = _check(root, report)
    assert proc.returncode != 0, (proc.stdout[-3000:], proc.stderr[-2000:])
    rep = json.loads(report.read_text(encoding="utf-8"))
    errors = [(f["rule"], f["message"]) for f in rep["findings"] if f["severity"] == "error"]
    assert errors == [], errors
    _assert_tilde_findings(_criteria_findings(report), root)
    got = _calc_sums(root)
    jan, feb = dt.date(2026, 1, 31), dt.date(2026, 2, 28)
    for label in ("A~*", "A~?"):
        assert (got[(label, jan)], got[(label, feb)]) == (0, 0), got
    assert got[("East", jan)] == 200, got
    want = _python_sums(ROWS, [jan, feb, dt.date(2026, 3, 31)])
    assert (want[("A~*", jan)], want[("A~*", feb)], want[("A~?", jan)], want[("A~?", feb)]) == (300, -300, 400, -400)


# ============================================================================= the helper keys

def test_section_gives_the_third_case_and_the_helper_keys() -> None:
    """[A-aera-1, C-sol-2]: the subsection names the third case with its reason, the general reason, the case field,
    the helper keys with one SUBSTITUTE per helper column, a table of formulas within the depth limit and the v9
    Excel tests; its JSON holds the three helper columns, the three key grids, the two checks and the block cell."""
    sec = _section()
    for words in ("`~*` or `~?`", "literal `*` and `?`", OTHER_REASON, "`logical`, `operator`, `tilde` or `other`",
                  "test_v9_criteria.py::test_tilde_wildcard_labels_netting_to_zero_fail_build_and_check",
                  "test_v9_criteria.py::test_tilde_rules_follow_excel_countif", "`A~b*`", "`A~~?`"):
        assert words in sec, words
    sub = _subsection()
    for words in ("one SUBSTITUTE per helper column", "depth 3", "`_t_`", "`_s_`", "`_q_`", "W-NO-CHECK",
                  "test_v9_criteria.py::test_helper_keys_for_tilde_labels_pass_and_equal_python", "Google Sheets"):
        assert words in sub, words
    obj = _pattern_json()
    exprs = [c["expr"] for c in obj["helper_columns"]] + [b["cell"] for b in obj["blocks"]]
    assert [e.count("SUBSTITUTE(") for e in exprs] == [1] * 6, exprs
    assert [c["id"] for c in obj["checks"]] == ["region_keys_rows", "region_keys_signs"]
    assert "{grow:region_keys}" in obj["block"]["cell"] and "{h:sales.region_key}" in obj["block"]["cell"]
    rows = [r for r in sub.split("\n") if r.startswith("| `")]
    assert len(rows) == 9, rows
    for r in rows:
        cells = [c.strip() for c in r.strip("|").split("|")]
        text = cells[2].strip("`")
        assert formula.depth(text) == int(cells[3]) <= 2, (text, formula.depth(text), cells[3])


def test_helper_keys_build_strict_without_findings(outside_tmp: Path) -> None:
    """[A-aera-1, C-sol-2] (no Excel): the subsection's JSON on the reviewers' data builds with --strict and exits 0:
    no D-CRITERIA and no other warning; every built formula has depth 2 or less."""
    root = starter(outside_tmp / "m", rows=ROWS, categories=CATEGORIES, spec_edit=_helper_keys_edit)
    report = outside_tmp / "build.json"
    proc = tc.run_cli("build", str(root), "--strict", "--no-power-query", "--json", str(report), env_extra=NO_EXCEL)
    assert proc.returncode == 0, (proc.stdout[-3000:], proc.stderr[-2000:])
    rep = json.loads(report.read_text(encoding="utf-8"))
    assert [f for f in rep["findings"] if f["severity"] in ("error", "warning")] == [], rep["findings"]
    import openpyxl
    book = openpyxl.load_workbook(root / "model.xlsx")
    try:
        deep = [(ws.title, c.coordinate, c.value) for ws in book for row in ws.iter_rows() for c in row
                if isinstance(c.value, str) and c.value.startswith("=") and formula.depth(c.value) > 2]
    finally:
        book.close()
    assert deep == [], deep


@pytest.mark.excel
def test_helper_keys_for_tilde_labels_pass_and_equal_python(outside_tmp: Path) -> None:
    """[A-aera-1, C-sol-2] (Excel): the subsection's JSON on the same data: build --strict, audit --strict and check
    --strict exit 0, and every sum per label and month equals the sum computed in Python from the CSV."""
    root = starter(outside_tmp / "m", rows=ROWS, categories=CATEGORIES, spec_edit=_helper_keys_edit)
    proc = tc.run_cli("build", str(root), "--strict", "--no-power-query", env_extra=NO_EXCEL)
    assert proc.returncode == 0, (proc.stdout[-3000:], proc.stderr[-2000:])
    proc = tc.run_cli("audit", str(root), "--strict", "--no-record", env_extra=NO_EXCEL)
    assert proc.returncode == 0, (proc.stdout[-3000:], proc.stderr[-2000:])
    report = outside_tmp / "check.json"
    proc = _check(root, report)
    assert proc.returncode == 0, (proc.stdout[-3000:], proc.stderr[-2000:])
    rep = json.loads(report.read_text(encoding="utf-8"))
    assert rep["data"]["recalculated"] is True
    assert _criteria_findings(report) == []
    got = _calc_sums(root)
    months = sorted({m for _c, m in got})
    want = _python_sums(ROWS, months)
    assert set(got) == set(want), (sorted(got), sorted(want))
    for key, value in want.items():
        assert Decimal(str(got[key])) == value, (key, got[key], value)
    jan, feb = dt.date(2026, 1, 31), dt.date(2026, 2, 28)
    assert (got[("A~*", jan)], got[("A~*", feb)], got[("A~?", jan)], got[("A~?", feb)]) == (300, -300, 400, -400)


# ============================================================================= no finding

def test_tilde_without_wildcard_ordinary_labels_and_shipped_models_give_no_self_mismatch(tmp_path: Path) -> None:
    """[A-aera-1, C-sol-2] (no Excel): labels with ~ and no wildcard (A~b, ~x, A~~, Net~), ordinary labels, and the
    shipped workbooks with their manifests (on the formulas and on the saved values) give no self_mismatch finding:
    6 folders with the plugin, 2 when the skill folder is alone (selftest step f02)."""
    rows = [{"line_id": "L0001", "line_date": "2026-01-10", "category": "East", "amount": "200"},
            {"line_id": "L0002", "line_date": "2026-01-12", "category": "A~b", "amount": "300"},
            {"line_id": "L0003", "line_date": "2026-02-12", "category": "~x", "amount": "-300"},
            {"line_id": "L0004", "line_date": "2026-02-13", "category": "A~~", "amount": "10"},
            {"line_id": "L0005", "line_date": "2026-03-13", "category": "Net~", "amount": "20"}]
    root = starter(tmp_path / "ordinary", rows=rows, categories=["East", "A~b", "~x", "A~~", "Net~"])
    rep = build.build(root / "model.spec.json", power_query=False)
    assert [f.line() for f in rep.findings if f.rule == "D-CRITERIA"] == []
    folders = _shipped()
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

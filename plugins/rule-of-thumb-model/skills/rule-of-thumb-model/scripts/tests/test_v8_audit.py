"""Tests of the v8 audit changes (package P4 of the v8 plan). Owner: P4.

Entries:
- [A-aera-5, A-aera-6] part (1), audit side: V-INPUT-RANGE (an error, the value family) with a manifest: a scalar
  input whose specification declares "valid", whose saved value lies outside it (the Excel test and the date test
  are in test_v8_check.py).
- [A-aera-5, A-aera-6] part (2), audit side: W-CHECK-ROUND (a warning, the check family) with a manifest: a
  registered check whose actual, expected (minimum, bound) or maximum cell calls ROUND, ROUNDUP, ROUNDDOWN, MROUND,
  INT, TRUNC, CEILING, FLOOR or one of their newer names (references/checks.md rule 3). No finding without a
  manifest.
- [A-aera-12]: the formula rules are skipped only on a sheet the manifest registers as raw. A sheet classed as raw
  by its name or by a query table, in a workbook without a manifest, gets them; R-FORMULA stays as an added finding.
- Supporting [C-sol-10, J-1] part (2), audit side: S-NUMFMT expects, for a header cell of a block whose rows or
  columns are given as same_as another block, the number format of the label cell it links to.
Run from the skill folder: python -P -X utf8 -B -m pytest scripts/tests/test_v8_audit.py -q -p no:cacheprovider
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path

import pytest

from lib import audit, build, common, manifest
import test_check as tc
from test_v4_check import starter

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
RULE_3 = "references/checks.md rule 3: a check compares the unrounded difference with the tolerance"
ROUND_FIX = ("remove ROUND from the check and compare the unrounded values with the tolerance; when a business rule "
             "needs rounded values, round them in a block and tie that block to an independent total")


def rules(rep) -> list[str]:
    return [f.rule for f in rep.findings]


def lines(rep) -> list[str]:
    return [f.line() for f in rep.findings] + list(rep.notes)


def hits(rep, rule: str) -> list:
    return [f for f in rep.findings if f.rule == rule]


def shipped_models() -> list[Path]:
    parents = [SKILL / "examples", SKILL / "templates"]
    if (PLUGIN / ".claude-plugin" / "plugin.json").is_file():
        parents.append(PLUGIN / "templates")
    return [d for parent in parents if parent.is_dir() for d in sorted(parent.iterdir())
            if (d / "model.spec.json").is_file() and (d / "model.xlsx").is_file()]


SHIPPED = shipped_models()


@pytest.fixture(scope="module")
def shipped_reports() -> dict[str, common.Report]:
    """audit --strict of each shipped workbook with its manifest, read only (nothing recorded in its folder)."""
    return {f"{d.parent.name}/{d.name}": audit.audit_workbook(d / "model.xlsx", strict=True, record=False)
            for d in SHIPPED}


# ============================================================================= the rule entries

def test_v8_rule_entries_are_in_their_families() -> None:
    families = {name: ids for name, _fn, ids in audit.FAMILIES}
    rng, rnd = audit.RULES["V-INPUT-RANGE"], audit.RULES["W-CHECK-ROUND"]
    assert (rng.severity, rng.needs) == ("error", "manifest") and "V-INPUT-RANGE" in families["value"]
    assert (rnd.severity, rnd.needs) == ("warning", "manifest") and "W-CHECK-ROUND" in families["check"]
    assert "references/checks.md rule 3" in rnd.source
    assert audit._parse_rules("v-input-range,w-check-round") == {"V-INPUT-RANGE", "W-CHECK-ROUND"}
    assert set(audit.ROUND_FUNCTIONS) == {"ROUND", "ROUNDUP", "ROUNDDOWN", "MROUND", "INT", "TRUNC", "CEILING",
                                          "FLOOR", "CEILING.MATH", "CEILING.PRECISE", "ISO.CEILING", "FLOOR.MATH",
                                          "FLOOR.PRECISE"}


# ============================================================================= W-CHECK-ROUND

def _round_starter(tmp_path: Path) -> Path:
    def edit(spec: dict) -> None:
        chk = next(s for s in spec["sheets"] if s["type"] == "checks")["checks"][0]
        chk["actual"] = "ROUND(" + chk["actual"] + ",0)"
        chk["expected"] = "ROUND(" + chk["expected"] + ",0)"

    root = starter(tmp_path / "m", spec_edit=edit)
    rep = build.build(root / "model.spec.json", power_query=False)            # not strict: it writes the workbook
    assert rep.counts()["errors"] == 0, lines(rep)
    return root


def test_audit_reports_w_check_round_at_a_registered_check_cell(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = _round_starter(tmp_path)
    wb = root / "model.xlsx"
    man = manifest.load_manifest(wb)
    ref = next(c["result"] for c in man["checks"] if c["id"] == "spend_ties_raw")
    sheet, cell = common.parse_ref(ref)
    row = common.split_cell(cell)[1]
    rep = audit.audit_workbook(wb, strict=True, record=False)
    found = hits(rep, "W-CHECK-ROUND")
    assert [(f.sheet, f.cell) for f in found] == [(sheet, f"D{row}"), (sheet, f"E{row}")], lines(rep)
    assert found[0].severity == "warning" and rep.exit_code == 1
    assert found[0].message == f"check spend_ties_raw ({sheet}!D{row}): its actual cell holds ROUND; {RULE_3}"
    assert found[1].message == f"check spend_ties_raw ({sheet}!E{row}): its expected cell holds ROUND; {RULE_3}"
    assert found[0].fix == ROUND_FIX
    assert found[0].data["check"] == "spend_ties_raw" and found[0].data["function"] == "ROUND"
    # --rules selects it alone
    rep = audit.audit_workbook(wb, strict=True, record=False, rules=["W-CHECK-ROUND"])
    assert rules(rep) == ["W-CHECK-ROUND", "W-CHECK-ROUND"] and rep.exit_code == 1


def test_w_check_round_reads_function_names_not_text(tmp_path: Path, monkeypatch) -> None:
    """A name inside quotes is not a call; the newer names (FLOOR.MATH, written _xlfn.FLOOR.MATH in the file) are."""
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = _round_starter(tmp_path)
    wb = root / "model.xlsx"
    man = manifest.load_manifest(wb)
    sheet, cell = common.parse_ref(next(c["result"] for c in man["checks"] if c["id"] == "spend_ties_raw"))
    row = common.split_cell(cell)[1]
    import openpyxl
    book = openpyxl.load_workbook(wb)
    ws = book[sheet]
    ws[f"D{row}"] = '=SUM(calc!$D$6:$F$9)+0*LEN("ROUND(")'
    ws[f"E{row}"] = "=_xlfn.FLOOR.MATH(SUM(raw_lines!$D:$D))"
    book.save(wb)
    found = hits(audit.audit_workbook(wb, record=False, rules=["W-CHECK-ROUND"]), "W-CHECK-ROUND")
    assert [(f.cell, f.data["function"]) for f in found] == [(f"E{row}", "FLOOR.MATH")]


def test_no_w_check_round_without_a_manifest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = _round_starter(tmp_path)
    plain = tmp_path / "plain.xlsx"
    shutil.copy2(root / "model.xlsx", plain)                  # no manifest next to the copy
    rep = audit.audit_workbook(plain, record=False)
    assert rep.data["mode"] == "inferred" and "W-CHECK-ROUND" not in rules(rep), lines(rep)


def test_shipped_models_get_no_w_check_round_in_audit(shipped_reports) -> None:
    assert len(shipped_reports) >= 2
    for name, rep in shipped_reports.items():
        assert "W-CHECK-ROUND" not in rules(rep), (name, lines(rep))


# ============================================================================= [A-aera-12]

def _raw_named_workbook(path: Path, sheet: str = "Data", query: bool = False) -> Path:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    ws["A1"], ws["B1"], ws["C1"] = "id", "amount", "derived"
    for r in range(2, 6):
        ws[f"A{r}"], ws[f"B{r}"] = f"r{r}", 10 * r
    ws["C2"] = "=ABS(ABS(ABS(B2)))"
    ws["C3"] = "=B3*17"
    wb.save(path)
    return path


def test_formula_rules_run_on_a_raw_named_sheet_without_a_manifest(tmp_path: Path) -> None:
    """r7 A-aera-12: v7 skipped the formula rules on Data (classed as raw by its name): --rules F-DEPTH,F-LITERAL
    --strict exited 0 with no finding."""
    path = _raw_named_workbook(tmp_path / "foreign.xlsx")
    r = tc.run_cli("audit", str(path), "--rules", "F-DEPTH,F-LITERAL", "--strict", "--json", "-")
    assert r.returncode == 1, r.stdout[-3000:] + r.stderr[-2000:]
    data = json.loads(r.stdout)
    by = {f["rule"]: f for f in data["findings"]}
    assert (by["F-DEPTH"]["sheet"], by["F-DEPTH"]["cell"]) == ("Data", "C2")
    assert by["F-DEPTH"]["fix"] == "move ABS(B2) into a helper cell and reference that cell"
    assert (by["F-LITERAL"]["sheet"], by["F-LITERAL"]["cell"]) == ("Data", "C3")
    rep = audit.audit_workbook(path, record=False)
    assert rep.data["raw_sheets"] == {"Data": "inferred from its name"}
    assert {"R-FORMULA", "F-DEPTH", "F-LITERAL"} <= set(rules(rep)), lines(rep)
    raw = next(f for f in rep.findings if f.rule == "R-FORMULA")
    assert raw.sheet == "Data" and raw.count == 2


def test_formula_rules_run_on_a_sheet_given_with_raw_sheets(tmp_path: Path) -> None:
    path = _raw_named_workbook(tmp_path / "foreign.xlsx", sheet="Extract")
    rep = audit.audit_workbook(path, record=False, raw_sheets=["Extract"], rules=["F-DEPTH", "R-FORMULA"])
    assert sorted(rules(rep)) == ["F-DEPTH", "R-FORMULA"], lines(rep)


def test_raw_sheet_of_a_built_model_gets_r_formula_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = starter(tmp_path / "m")
    assert build.build(root / "model.spec.json", power_query=False).counts()["errors"] == 0
    wb = root / "model.xlsx"
    import openpyxl
    book = openpyxl.load_workbook(wb)
    book["raw_lines"]["D2"] = "=ABS(ABS(ABS(17*C2)))"          # a formula typed into the raw sheet
    book.save(wb)
    rep = audit.audit_workbook(wb, record=False)
    on_raw = [f for f in rep.findings if f.sheet == "raw_lines"]
    assert "R-FORMULA" in [f.rule for f in on_raw], lines(rep)
    assert not [f for f in on_raw if f.rule.startswith("F-")], lines(rep)


def test_shipped_models_audit_strict_with_the_formula_rules(shipped_reports) -> None:
    """The six shipped workbooks audit in strict mode with exit 0 (no formula-rule finding, no V-INPUT-RANGE, no
    W-CHECK-ROUND, no new S-NUMFMT)."""
    for name, rep in shipped_reports.items():
        assert rep.exit_code == 0, (name, lines(rep))
        assert "formula" in rep.data["rules_run"]


# ============================================================================= S-NUMFMT for same_as labels

DATES = ["2026-01-31", "2026-02-28", "2026-03-31"]


def _same_as_spec(spec: dict) -> None:
    """The specification of plan 6.3: an input list of dates; A rows in the list, B rows same_as A; C cols in the
    list, D cols same_as C (and E rows same_as B: a chain)."""
    items = next(s for s in spec["sheets"] if s["type"] == "inputs")["items"]
    items.append({"id": "close_dates", "kind": "list", "label": "Closing dates", "values": list(DATES),
                  "format": "date", "source": "test"})
    calc = next(s for s in spec["sheets"] if s["type"] == "calc")
    calc["blocks"] += [
        {"id": "a_rows", "title": "A", "unit": "KRW", "format": "amount", "rows": {"in": "close_dates"},
         "cols": {"values": ["x"], "format": "text"}, "cell": "0"},
        {"id": "b_rows", "title": "B", "unit": "KRW", "format": "amount", "rows": {"same_as": "a_rows"},
         "cols": {"values": ["x"], "format": "text"}, "cell": "0"},
        {"id": "e_rows", "title": "E", "unit": "KRW", "format": "amount", "rows": {"same_as": "b_rows"},
         "cols": {"values": ["x"], "format": "text"}, "cell": "0"},
        {"id": "c_cols", "title": "C", "unit": "KRW", "format": "amount", "rows": {"values": ["r"], "format": "text"},
         "cols": {"in": "close_dates"}, "cell": "0"},
        {"id": "d_cols", "title": "D", "unit": "KRW", "format": "amount", "rows": {"values": ["r"], "format": "text"},
         "cols": {"same_as": "c_cols"}, "cell": "0"}]


def _header_cells(man: dict, block_id: str, axis: str) -> tuple[str, list[str]]:
    entry, b = next((e, b) for e in man["sheets"] for b in e.get("blocks") or [] if b["id"] == block_id)
    if axis == "rows":
        c1, r1, _c2, r2 = common.range_bounds(b["labels"])
        return entry["name"], [common.a1(c1, r) for r in range(r1, r2 + 1)]
    b1, _r1, b2, _r2 = common.range_bounds(b["body"])
    return entry["name"], [common.a1(c, int(b["header_row"])) for c in range(b1, b2 + 1)]


def test_s_numfmt_expects_the_linked_format_on_same_as_labels(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = starter(tmp_path / "m", spec_edit=_same_as_spec)
    rep = build.build(root / "model.spec.json", power_query=False)
    assert rep.counts()["errors"] == 0, lines(rep)
    wb = root / "model.xlsx"
    man = manifest.load_manifest(wb)
    from lib import dictionary
    d = dictionary.load_dictionary()
    date_fmt, month_fmt = d.number_format("date"), d.number_format("month")
    linked = {"b_rows": ("rows", date_fmt), "e_rows": ("rows", date_fmt), "d_cols": ("cols", date_fmt)}
    import openpyxl

    def set_formats(fmt_of) -> None:
        book = openpyxl.load_workbook(wb)
        for bid, (axis, fmt) in linked.items():
            sheet, cells = _header_cells(man, bid, axis)
            for c in cells:
                book[sheet][c].number_format = fmt_of(fmt)
        book.save(wb)

    set_formats(lambda fmt: fmt)                                  # the format of the cells they link to
    rep = audit.audit_workbook(wb, strict=True, record=False, rules=["S-NUMFMT"])
    targets = {(s, c) for bid, (axis, _f) in linked.items() for s in [_header_cells(man, bid, axis)[0]]
               for c in _header_cells(man, bid, axis)[1]}
    assert not [f for f in hits(rep, "S-NUMFMT") for c in (f.cells or [f.cell]) if (f.sheet, c) in targets], \
        lines(rep)
    set_formats(lambda fmt: "General")                            # the v7 build's format for these cells
    rep = audit.audit_workbook(wb, strict=True, record=False, rules=["S-NUMFMT"])
    found = {(f.sheet, c): f for f in hits(rep, "S-NUMFMT") for c in (f.cells or [f.cell])}
    assert targets <= set(found), (sorted(targets - set(found)), lines(rep))
    sheet, cells = _header_cells(man, "b_rows", "rows")
    f = found[(sheet, cells[0])]
    assert f.data["expected"] == date_fmt and "block 'a_rows'" in f.message, f.message
    sheet, cells = _header_cells(man, "d_cols", "cols")
    assert "block 'c_cols'" in found[(sheet, cells[0])].message
    assert month_fmt != date_fmt                                  # the list's own format (date) wins over month


def test_same_as_format_resolves_timeline_values_and_its_own_format(tmp_path: Path, monkeypatch) -> None:
    """The resolution P3's build uses: a timeline gives month; values rows give General (build writes a values row
    label with the row_header type's General; integrator, request v8_P3 R1); values cols give their node's format,
    a date value month, else General; a format on the same_as node itself wins."""
    from lib import dictionary
    d = dictionary.load_dictionary()
    blocks = {
        "t": {"cols": {"timeline": {"start": "s", "periods": 3, "step": "month"}}},
        "v": {"rows": {"values": ["2026-01-31", "2026-02-28"]}, "cols": {"values": ["a", "b"]}},
        "vd": {"cols": {"values": ["2026-01-31", "2026-02-28"]}},
        "vt": {"rows": {"values": ["x"], "format": "text"}},
        "s1": {"cols": {"same_as": "t"}},
        "s2": {"rows": {"same_as": "v"}, "cols": {"same_as": "v"}},
        "s3": {"rows": {"same_as": "vt", "format": "general"}},
        "s4": {"rows": {"same_as": "s5"}}, "s5": {"rows": {"same_as": "s4"}},          # a cycle: not resolved
        "s6": {"cols": {"same_as": "vd"}},
    }
    lists: dict = {}
    res = audit._linked_label_format
    assert res(blocks, lists, "s1", "cols", 0) == ("month", "t")
    assert res(blocks, lists, "s2", "rows", 1) == ("general", "v")
    assert res(blocks, lists, "s2", "cols", 0) == ("general", "v")
    assert res(blocks, lists, "s6", "cols", 1) == ("month", "vd")
    assert res(blocks, lists, "s3", "rows", 0) == ("general", "vt")
    assert res(blocks, lists, "s4", "rows", 0) is None
    assert d.number_format("month") and d.number_format("date")

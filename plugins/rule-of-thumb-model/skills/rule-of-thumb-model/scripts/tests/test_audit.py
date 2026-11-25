"""Tests of the audit (design section 14; spec S2, S3, S5; plan F06, F07, F23). Owner: M4.

Every rule has a workbook that passes and one that fails. Workbooks are written with openpyxl into tmp_path:
- the inferred base (no manifest) passes the strict audit; each mutation adds one defect;
- the manifest base (a sidecar manifest as rotm.py build writes it) passes the strict audit; each mutation adds
  one defect that only the manifest can show;
- cached values are written into the sheet XML by `patch_cached`, because openpyxl writes formulas without them
  (one test recalculates in Excel instead, marked `excel`).
The foreign tests audit copies of workbooks this plugin did not make (BUILD/foreign, ROTM_FOREIGN_DIR).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, Callable

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib import audit, common, schemas  # noqa: E402

DENSE, SPACED = 8, 9
SPACED_SHEETS = ("inputs", "checks", "cover", "output", "notes")


# ----------------------------------------------------------------------------- workbook writers

def _font_size(sheet: str) -> int:
    return SPACED if sheet in SPACED_SHEETS else DENSE


def write_wb(path: Path, sheets: dict[str, dict[str, Any]], *, fills: dict[tuple[str, str], str] | None = None,
             bold: set[tuple[str, str]] | None = None, fonts: dict[tuple[str, str], tuple[str, float]] | None = None,
             colors: dict[tuple[str, str], str] | None = None, formats: dict[tuple[str, str], str] | None = None,
             edit: Callable[[Any], None] | None = None) -> Path:
    """An .xlsx from {sheet: {coord: value}}; every cell Arial at the sheet's size unless `fonts` says otherwise."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, cells in sheets.items():
        ws = wb.create_sheet(name)
        for coord, value in cells.items():
            c = ws[coord]
            c.value = value
            key = (name, coord)
            fname, fsize = (fonts or {}).get(key, ("Arial", _font_size(name)))
            c.font = Font(name=fname, size=fsize, bold=key in (bold or set()),
                          color=(colors or {}).get(key))
            if key in (fills or {}):
                c.fill = PatternFill("solid", fgColor=(fills or {})[key])
            if key in (formats or {}):
                c.number_format = (formats or {})[key]
    if edit:
        edit(wb)
    wb.save(path)
    return path


def patch_cached(path: Path, values: dict[tuple[str, str], Any]) -> None:
    """Write cached values into formula cells of an openpyxl-written workbook (as Excel would after a recalc)."""
    with zipfile.ZipFile(path) as z:
        parts = audit._sheet_part_names(z)
        items = {n: z.read(n) for n in z.namelist()}
    for (sheet, coord), v in values.items():
        part = parts[sheet]
        xml = items[part].decode("utf-8")
        m = re.search(rf'<c r="{coord}"([^>]*)><f>(.*?)</f><v ?/>', xml)
        assert m, f"{sheet}!{coord} is not a formula cell written by openpyxl"
        attrs = re.sub(r'\s*t="[^"]*"', "", m.group(1))
        if isinstance(v, str) and v.startswith("#"):
            attrs += ' t="e"'
            text = v
        elif isinstance(v, bool):
            attrs += ' t="b"'
            text = "1" if v else "0"
        elif isinstance(v, str):
            attrs += ' t="str"'
            text = v
        else:
            text = repr(v)
        xml = xml[:m.start()] + f'<c r="{coord}"{attrs}><f>{m.group(2)}</f><v>{text}</v>' + xml[m.end():]
        items[part] = xml.encode("utf-8")
    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for n, data in items.items():
            z.writestr(n, data)
    tmp.replace(path)


def add_part(path: Path, name: str, data: bytes) -> None:
    """Add one zip member (a fake macro or Power Query part) to a workbook."""
    with zipfile.ZipFile(path, "a") as z:
        z.writestr(name, data)


def run(path: Path, **kw: Any) -> common.Report:
    kw.setdefault("strict", True)
    kw.setdefault("record", False)
    return audit.audit_workbook(path, **kw)


def rules_of(report: common.Report) -> set[str]:
    return {f.rule for f in report.findings}


def findings(report: common.Report, rule: str) -> list[common.Finding]:
    return [f for f in report.findings if f.rule == rule]


def cli(*args: str, script: str = "rotm.py") -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / script)]
    if script == "rotm.py":
        cmd.append("audit")
    return subprocess.run(cmd + list(args), capture_output=True, text=True, encoding="utf-8", timeout=300)


# ----------------------------------------------------------------------------- the inferred base

def base_sheets(rows: int = 5) -> dict[str, dict[str, Any]]:
    raw: dict[str, Any] = {"A1": "id", "B1": "region", "C1": "amount"}
    helpers: dict[str, Any] = {"A1": "row", "B1": "amount", "C1": "grown"}
    regions = ["East", "North", "South", "West"]
    for i in range(1, rows + 1):
        r = i + 1
        raw[f"A{r}"] = f"S{i:03d}"
        raw[f"B{r}"] = regions[i % 4]
        raw[f"C{r}"] = 100 * i
        helpers[f"A{r}"] = "=ROW()"
        helpers[f"B{r}"] = f"=INDEX(raw_sales!$C:$C,A{r})"
        helpers[f"C{r}"] = f"=B{r}*(1+inputs!$D$4)"
    return {
        "inputs": {"B1": "Inputs", "B4": "Growth rate", "C4": "rate", "D4": 0.05, "B5": "Expected rows",
                   "C5": "count", "D5": rows, "B6": "Control total", "C6": "KRW", "D6": 1500,
                   "B7": "Tolerance", "C7": "KRW", "D7": 0.001},
        "raw_sales": raw,
        "h_sales": helpers,
        "calc": {"B1": "Calculation", "B4": "Total grown", "C4": "KRW", "D4": "=SUM(h_sales!$C:$C)",
                 "B7": "Year", "D7": 1, "E7": 2, "F7": 3, "G7": 4, "H7": 5,
                 "B9": "Grown by year", "D9": "=D7*inputs!$D$4", "E9": "=E7*inputs!$D$4", "F9": "=F7*inputs!$D$4",
                 "G9": "=G7*inputs!$D$4", "H9": "=H7*inputs!$D$4"},
        # every calculation block (h_sales A, B and C; calc D9:H9) is read by a check (C-BLOCK, inferred)
        "checks": {"B1": "Checks", "B2": "Master check", "C2": "=--(SUM(D5:D9)>0)", "B4": "Check", "D4": "Result",
                   "B5": "Rows equal the contract", "D5": "=--(COUNTA(raw_sales!$A:$A)-1<>inputs!$D$5)",
                   "B6": "Amount total ties", "D6": "=--(ABS(SUM(raw_sales!$C:$C)-inputs!$D$6)>inputs!$D$7)",
                   "B7": "Helper rows cover the raw rows", "D7": "=--(COUNT(h_sales!$A:$A)<>COUNTA(raw_sales!$A:$A)-1)",
                   "B8": "Grown amounts tie", "D8": "=--(ABS(SUM(h_sales!$B:$B)*(1+inputs!$D$4)-SUM(h_sales!$C:$C))>0)",
                   "B9": "Grown by year ties", "D9": "=--(ABS(SUM(calc!$D$9:$H$9)-SUM(calc!$D$7:$H$7)*inputs!$D$4)>0)"},
    }


BASE_FILLS = {("inputs", f"D{r}"): "FFF2CC" for r in range(4, 8)} | {
    ("raw_sales", c): "D9D9D9" for c in ("A1", "B1", "C1")} | {("h_sales", c): "D9D9D9" for c in ("A1", "B1", "C1")}
BASE_BOLD = {("raw_sales", c) for c in ("A1", "B1", "C1")} | {("h_sales", c) for c in ("A1", "B1", "C1")} | {
    (s, "B1") for s in ("inputs", "calc", "checks")}


def write_base(path: Path, mutate: Callable[[dict[str, dict[str, Any]]], None] | None = None, **kw: Any) -> Path:
    sheets = base_sheets(kw.pop("rows", 5))
    if mutate:
        mutate(sheets)
    kw.setdefault("fills", dict(BASE_FILLS))
    kw.setdefault("bold", set(BASE_BOLD))
    return write_wb(path, sheets, **kw)


def base_cached_values(sheets: dict[str, dict[str, Any]]) -> dict[tuple[str, str], Any]:
    """Plausible cached values for every formula of the base (all checks pass)."""
    out: dict[tuple[str, str], Any] = {}
    for name, cells in sheets.items():
        for coord, v in cells.items():
            if isinstance(v, str) and v.startswith("="):
                out[(name, coord)] = 0 if name == "checks" else 1.5
    return out


@pytest.fixture
def base(tmp_path: Path) -> Path:
    return write_base(tmp_path / "base.xlsx")


def test_inferred_base_passes_strict(base: Path) -> None:
    rep = run(base)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    assert rules_of(rep) == {"V-NOT-RECALC"}
    assert rep.data["mode"] == "inferred"
    assert set(rep.data["raw_sheets"]) == {"raw_sales"}
    assert any("not recalculated" in n for n in rep.notes)
    assert schemas.validate(rep.to_dict(), "report") == []


# ----------------------------------------------------------------------------- one failing case per rule (inferred)

def _set(sheet: str, coord: str, value: Any) -> Callable[[dict[str, dict[str, Any]]], None]:
    def f(s: dict[str, dict[str, Any]]) -> None:
        s[sheet][coord] = value
    return f


LONG = "=" + "+".join(f"SUM(h_sales!$C${r}:$C${r + 1})" for r in range(2, 8))           # 151 characters
HUGE = "=" + "+".join(["inputs!$D$4"] * 700)                                              # 8,400 characters

FORMULA_CASES: list[tuple[str, str]] = [
    ("F-DEPTH", "=IFERROR(INDEX(h_sales!$C:$C,MATCH(1,h_sales!$A:$A,0)),0)"),
    ("F-LENGTH", LONG),
    ("F-EXCEL-LIMIT", HUGE),
    ("F-LINEBREAK", "=SUM(h_sales!$C:$C)\n+0"),
    ("F-LITERAL", "=D4*1.1"),
    ("F-NESTED-IF", "=IF(D4>0,IF(D4>inputs!$D$6,1,0),0)"),
    ("F-FUNC-REFUSED", '=INDIRECT("calc!D4")'),
    ("F-FUNC-FLAGGED", "=VLOOKUP(D4,h_sales!$A:$C,1,0)"),
    ("F-FUNC-UNKNOWN", "=FOOBAR(D4)"),
    ("F-VOLATILE", "=TODAY()"),
    ("F-SELF-SHEET", "=calc!D4"),
    ("F-EXTERNAL", "=[1]Sheet1!A1"),
    ("F-REF-ERROR", "=SUM(#REF!)"),
    ("F-PARSE", "=SUM(D4))"),
    ("F-ARRAY", "=SUM({1,2})"),
    ("F-NAME", "=growth_rate*D4"),
    ("X-STRUCTREF", "=SUM(Table1[@amount])"),
]


@pytest.mark.parametrize("rule,formula_text", FORMULA_CASES, ids=[r for r, _f in FORMULA_CASES])
def test_formula_rule_fails_and_passes(tmp_path: Path, rule: str, formula_text: str) -> None:
    good = run(write_base(tmp_path / "good.xlsx"))
    assert rule not in rules_of(good)
    bad = run(write_base(tmp_path / "bad.xlsx", _set("calc", "D5", formula_text)))
    hits = findings(bad, rule)
    assert hits, [f.line() for f in bad.findings]
    f = hits[0]
    assert f.sheet == "calc" and f.cell == "D5"
    assert f.message and f.fix
    assert f.severity == audit.RULES[rule].severity
    assert bad.exit_code == 1


def test_depth_finding_names_the_part_to_move(tmp_path: Path) -> None:
    rep = run(write_base(tmp_path / "d.xlsx", _set("calc", "D5", "=IFERROR(INDEX(h_sales!$C:$C,MATCH(1,h_sales!$A:$A,0)),0)")))
    f = findings(rep, "F-DEPTH")[0]
    assert f.data["part"] == "MATCH(1,h_sales!$A:$A,0)"
    assert "MATCH(1,h_sales!$A:$A,0)" in f.fix and "helper" in f.fix
    assert f.data["chain"] == ["IFERROR", "INDEX", "MATCH"]
    assert "IFERROR > INDEX > MATCH" in f.message


def test_depth_limit_setting_changes_the_result(tmp_path: Path) -> None:
    p = write_base(tmp_path / "d.xlsx", _set("calc", "D5", "=IFERROR(INDEX(h_sales!$C:$C,MATCH(1,h_sales!$A:$A,0)),0)"))
    assert "F-DEPTH" in rules_of(run(p))
    assert "F-DEPTH" not in rules_of(run(p, depth_limit=3))
    assert "F-DEPTH" in rules_of(run(write_base(tmp_path / "e.xlsx", _set("calc", "D5", "=ROUND(SUM(D4),0)")),
                                     depth_limit=1))


def test_length_limit_setting(tmp_path: Path) -> None:
    p = write_base(tmp_path / "l.xlsx", _set("calc", "D5", LONG))
    assert "F-LENGTH" in rules_of(run(p))
    assert "F-LENGTH" not in rules_of(run(p, length_limit=200))


def test_copied_down_formula_is_one_finding(tmp_path: Path) -> None:
    def mutate(s: dict[str, dict[str, Any]]) -> None:
        for r in range(2, 7):
            s["h_sales"][f"D{r}"] = f"=IFERROR(INDEX(raw_sales!$C:$C,MATCH(A{r},h_sales!$A:$A,0)),0)"
        s["h_sales"]["D1"] = "lookup"
    rep = run(write_base(tmp_path / "g.xlsx", mutate))
    hits = findings(rep, "F-DEPTH")
    assert len(hits) == 1
    assert hits[0].count == 5 and hits[0].cells == [f"D{r}" for r in range(2, 7)]
    assert "(5 cells)" in hits[0].line()


def test_raw_formula_fails(tmp_path: Path) -> None:
    assert "R-FORMULA" not in rules_of(run(write_base(tmp_path / "g.xlsx")))
    rep = run(write_base(tmp_path / "b.xlsx", _set("raw_sales", "C3", "=C2*2")))
    f = findings(rep, "R-FORMULA")[0]
    assert f.sheet == "raw_sales" and f.cell == "C3" and f.severity == "error"
    assert "inferred" in f.message
    assert "F-LITERAL" not in rules_of(rep)            # formulas on raw sheets are reported once, by R-FORMULA


def test_raw_sheets_flag_names_a_sheet(tmp_path: Path) -> None:
    def mutate(s: dict[str, dict[str, Any]]) -> None:
        s["Sales"] = s.pop("raw_sales")
        s["Sales"]["C3"] = "=C2*2"
    p = write_base(tmp_path / "s.xlsx", mutate)
    assert "R-FORMULA" not in rules_of(run(p))
    rep = run(p, raw_sheets=["Sales"])
    assert findings(rep, "R-FORMULA")[0].sheet == "Sales"
    with pytest.raises(common.Refusal, match="no sheet 'Salez'"):
        run(p, raw_sheets=["Salez"])


def test_raw_not_alone(tmp_path: Path) -> None:
    assert "R-NOT-ALONE" not in rules_of(run(write_base(tmp_path / "g.xlsx")))
    rep = run(write_base(tmp_path / "b.xlsx", _set("raw_sales", "F2", "note beside the table")))
    f = findings(rep, "R-NOT-ALONE")[0]
    assert f.cell == "F2" and "outside the raw table A1:C6" in f.message

    def shift(s: dict[str, dict[str, Any]]) -> None:
        s["raw_sales"] = {"A3": "id", "B3": "amount", "A4": "S001", "B4": 5}
    rep = run(write_base(tmp_path / "c.xlsx", shift))
    assert any("does not start at A1" in f.message for f in findings(rep, "R-NOT-ALONE"))


def test_raw_merged(tmp_path: Path) -> None:
    assert "R-MERGED" not in rules_of(run(write_base(tmp_path / "g.xlsx")))
    rep = run(write_base(tmp_path / "b.xlsx", edit=lambda wb: wb["raw_sales"].merge_cells("E10:F10")))
    assert findings(rep, "R-MERGED")[0].sheet == "raw_sales"
    assert "S-MERGED" not in rules_of(rep)


def test_helper_column_inconsistent_inferred(tmp_path: Path) -> None:
    rep = run(write_base(tmp_path / "b.xlsx", _set("h_sales", "C4", "=B4*inputs!$D$4")))
    f = findings(rep, "H-INCONSISTENT")[0]
    assert f.sheet == "h_sales" and f.cells == ["C4"] and f.severity == "warning"
    assert "inferred" in f.message and "C3" in f.fix


def test_helper_column_typed_value_inferred(tmp_path: Path) -> None:
    rep = run(write_base(tmp_path / "b.xlsx", _set("h_sales", "C4", 315)))
    f = findings(rep, "H-INCONSISTENT")[0]
    assert f.cell == "C4" and "typed value 315" in f.message and "C3" in f.fix and "inferred" in f.message
    assert "H-GAP" not in rules_of(rep)                # H-GAP needs the manifest's formula rows


def test_helper_coverage_inferred(tmp_path: Path) -> None:
    assert "H-COVERAGE" not in rules_of(run(write_base(tmp_path / "g.xlsx")))

    def grow(s: dict[str, dict[str, Any]]) -> None:
        s["raw_sales"].update({"A7": "S006", "B7": "East", "C7": 600, "A8": "S007", "B8": "West", "C8": 700})
    rep = run(write_base(tmp_path / "b.xlsx", grow))
    f = findings(rep, "H-COVERAGE")[0]
    assert f.sheet == "h_sales" and "ends at row 6" in f.message and "down to row 8" in f.message
    assert "2 raw row(s) (7 to 8)" in f.message


def test_block_row_inconsistent_inferred(tmp_path: Path) -> None:
    assert "B-INCONSISTENT" not in rules_of(run(write_base(tmp_path / "g.xlsx")))
    rep = run(write_base(tmp_path / "b.xlsx", _set("calc", "G9", "=G7+inputs!$D$4")))
    f = findings(rep, "B-INCONSISTENT")[0]
    assert f.cells == ["G9"] and "inferred" in f.message


def test_first_cell_of_a_row_may_differ(tmp_path: Path) -> None:
    rep = run(write_base(tmp_path / "o.xlsx", _set("calc", "D9", "=inputs!$D$4")))
    assert "B-INCONSISTENT" not in rules_of(rep)


def test_typed_value_inside_a_row(tmp_path: Path) -> None:
    rep = run(write_base(tmp_path / "t.xlsx", _set("calc", "F9", 0.15)))
    f = findings(rep, "B-INCONSISTENT")[0]
    assert f.cell == "F9" and "typed value" in f.message


def test_line_items_are_not_a_helper_column(tmp_path: Path) -> None:
    def items(s: dict[str, dict[str, Any]]) -> None:
        s["calc"].update({"B12": "Actual", "D12": "=SUM(h_sales!$B:$B)", "B13": "Target", "D13": "=inputs!$D$6",
                          "B14": "Variance", "D14": "=D12-D13", "B15": "Share", "D15": "=D14/D13"})
    rep = run(write_base(tmp_path / "i.xlsx", items))
    assert not ({"H-INCONSISTENT", "B-INCONSISTENT"} & rules_of(rep)), [f.line() for f in rep.findings]


def test_total_below_a_column_is_not_inconsistent(tmp_path: Path) -> None:
    rep = run(write_base(tmp_path / "t.xlsx", _set("h_sales", "C7", "=SUM(C2:C6)")))
    assert "H-INCONSISTENT" not in rules_of(rep), [f.line() for f in rep.findings]


def test_missing_checks_inferred(tmp_path: Path) -> None:
    rep = run(write_base(tmp_path / "n.xlsx", lambda s: s.pop("checks")))
    f = findings(rep, "C-MISSING")[0]
    assert "no check cells" in f.message and f.severity == "warning"

    def no_master(s: dict[str, dict[str, Any]]) -> None:
        del s["checks"]["B2"], s["checks"]["C2"]
    rep = run(write_base(tmp_path / "m.xlsx", no_master))
    assert "no master check" in findings(rep, "C-MISSING")[0].message


def test_master_check_found_by_reference(tmp_path: Path) -> None:
    rep = run(write_base(tmp_path / "r.xlsx", _set("checks", "B2", "All results")))
    assert "C-MISSING" not in rules_of(rep)


def test_values_without_formulas_need_no_checks(tmp_path: Path) -> None:
    p = write_wb(tmp_path / "v.xlsx", {"raw_x": {"A1": "id", "A2": 1}})
    rep = run(p)
    assert "C-MISSING" not in rules_of(rep) and rep.exit_code == 0


def test_check_fails_inferred(tmp_path: Path) -> None:
    sheets = base_sheets()
    good = write_base(tmp_path / "g.xlsx")
    patch_cached(good, base_cached_values(sheets))
    rep = run(good)
    assert not ({"C-FAIL", "V-NOT-RECALC"} & rules_of(rep)), [f.line() for f in rep.findings]
    assert rep.exit_code == 0
    bad = write_base(tmp_path / "b.xlsx")
    vals = base_cached_values(sheets)
    vals[("checks", "D5")] = 1
    vals[("checks", "C2")] = 1
    patch_cached(bad, vals)
    hits = findings(run(bad), "C-FAIL")
    assert {f.cell for f in hits} == {"C2", "D5"}


def test_error_values(tmp_path: Path) -> None:
    sheets = base_sheets()
    bad = write_base(tmp_path / "b.xlsx")
    vals = base_cached_values(sheets)
    vals[("calc", "D4")] = "#DIV/0!"
    patch_cached(bad, vals)
    f = findings(run(bad), "V-ERROR")[0]
    assert f.cell == "D4" and "#DIV/0!" in f.message and f.severity == "error"
    p = write_base(tmp_path / "c.xlsx", _set("calc", "E4", "#N/A"))      # a typed error constant
    assert "V-ERROR" in rules_of(run(p))


def test_not_recalculated_is_info(base: Path) -> None:
    f = findings(run(base), "V-NOT-RECALC")[0]
    assert f.severity == "info" and "rotm.py" in f.fix


def test_formula_cells_without_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """V-NO-VALUE reads workbook.formula_cells_without_value (P1) in a workbook that has cached values; the
    Excel case (a circular pair saved by Excel) is in test_v2_audit.py."""
    from lib import workbook
    asked: list[Path] = []

    def fake(path: Any) -> list[tuple[str, str]]:
        asked.append(Path(path))
        return [("h_sales", "C3"), ("h_sales", "B3")]
    monkeypatch.setattr(workbook, "formula_cells_without_value", fake, raising=False)
    sheets = base_sheets()
    p = write_base(tmp_path / "v.xlsx")
    patch_cached(p, base_cached_values(sheets))
    f = findings(run(p), "V-NO-VALUE")[0]
    assert f.severity == "error" and f.sheet == "h_sales" and f.cells == ["B3", "C3"] and f.count == 2
    assert f.fix.startswith("the saved file holds no value for this formula: ")      # fix N-AUDIT (v3)
    assert "names a circular reference when there is one" in f.fix and "trace" in f.fix and "h_sales!B3" in f.fix
    fresh = write_base(tmp_path / "fresh.xlsx")       # no cached values: V-NOT-RECALC, the finder is not asked
    asked.clear()
    rep = run(fresh)
    assert "V-NO-VALUE" not in rules_of(rep) and asked == []


# ----------------------------------------------------------------------------- styles (inferred)

def test_wrong_font_name(tmp_path: Path) -> None:
    rep = run(write_base(tmp_path / "f.xlsx", fonts={("calc", "D4"): ("Calibri", 8)}))
    f = findings(rep, "S-FONT")[0]
    assert f.cell == "D4" and "Calibri" in f.message and "Arial" in f.message and "theme" in f.fix


def test_wrong_font_size(tmp_path: Path) -> None:
    rep = run(write_base(tmp_path / "s.xlsx", fonts={("calc", "D4"): ("Arial", 11)}))
    f = findings(rep, "S-SIZE")[0]
    assert "size 11" in f.message and "8" in f.message and "9" in f.message


def test_fill_outside_the_dictionary(tmp_path: Path) -> None:
    rep = run(write_base(tmp_path / "c.xlsx", fills=dict(BASE_FILLS) | {("calc", "B4"): "4472C4"}))
    f = findings(rep, "S-FILL")[0]
    assert f.cell == "B4" and "4472C4" in f.message and "D9D9D9" in f.message


def test_white_fill_counts_as_no_fill(tmp_path: Path) -> None:
    rep = run(write_base(tmp_path / "w.xlsx", fills=dict(BASE_FILLS) | {("calc", "B4"): "FFFFFF"}))
    assert "S-FILL" not in rules_of(rep)


def test_font_colour_outside_the_dictionary(tmp_path: Path) -> None:
    rep = run(write_base(tmp_path / "c.xlsx", colors={("calc", "D4"): "0000FF"}))
    f = findings(rep, "S-FONTCOLOR")[0]
    assert "0000FF" in f.message


def test_named_override_allows_blue_inputs(tmp_path: Path) -> None:
    p = write_base(tmp_path / "c.xlsx", colors={("inputs", "D4"): "0000FF"})
    override = tmp_path / "style_overrides.json"
    override.write_text(json.dumps({"rotm_dictionary_override": 1, "use": ["input_blue_font"]}), encoding="utf-8")
    assert "S-FONTCOLOR" in rules_of(run(p))
    assert "S-FONTCOLOR" not in rules_of(run(p, dictionary=str(override)))


# ----------------------------------------------------------------------------- workbook rules

def test_merged_cells_outside_raw(tmp_path: Path) -> None:
    rep = run(write_base(tmp_path / "m.xlsx", _set("calc", "B11", "merged"),
                         edit=lambda wb: wb["calc"].merge_cells("B11:C11")))
    assert findings(rep, "S-MERGED")[0].sheet == "calc"


def test_hidden_rows_columns_sheets(tmp_path: Path) -> None:
    assert "S-HIDDEN" not in rules_of(run(write_base(tmp_path / "g.xlsx")))

    def hide(wb: Any) -> None:
        wb["calc"].row_dimensions[5].hidden = True
        wb["calc"].column_dimensions["F"].hidden = True
        wb["inputs"].sheet_state = "hidden"
    rep = run(write_base(tmp_path / "h.xlsx", edit=hide))
    msgs = {f.sheet: f.message for f in findings(rep, "S-HIDDEN")}
    assert "hidden" in msgs["inputs"]
    assert "1 hidden row(s) (5)" in msgs["calc"] and "1 hidden column(s) (F)" in msgs["calc"]


def test_grouped_rows_are_not_hidden(tmp_path: Path) -> None:
    def group(wb: Any) -> None:
        wb["calc"].row_dimensions.group(5, 6, hidden=True, outline_level=1)
    assert "S-HIDDEN" not in rules_of(run(write_base(tmp_path / "g.xlsx", edit=group)))


def test_calculation_mode_and_iteration(tmp_path: Path) -> None:
    def manual(wb: Any) -> None:
        wb.calculation.calcMode = "manual"
    def iterate(wb: Any) -> None:
        wb.calculation.iterate = True
    assert not ({"W-CALCMODE", "W-ITERATE"} & rules_of(run(write_base(tmp_path / "g.xlsx"))))
    assert "W-CALCMODE" in rules_of(run(write_base(tmp_path / "m.xlsx", edit=manual)))
    f = findings(run(write_base(tmp_path / "i.xlsx", edit=iterate)), "W-ITERATE")[0]
    assert f.severity == "error"


def test_one_product_features(tmp_path: Path) -> None:
    good = write_base(tmp_path / "g.xlsx")
    assert "X-FEATURE" not in rules_of(run(good))
    macro = write_base(tmp_path / "m.xlsx")
    add_part(macro, "xl/vbaProject.bin", b"\x00" * 16)
    f = findings(run(macro), "X-FEATURE")[0]
    assert "VBA macros" in f.message and f.severity == "warning"
    assert "X-FEATURE" not in rules_of(run(macro, product="excel"))
    pq = write_base(tmp_path / "q.xlsx")
    add_part(pq, "customXml/item1.xml",
             b'<?xml version="1.0"?><DataMashup xmlns="http://schemas.microsoft.com/DataMashup">AAAA</DataMashup>')
    assert any("Power Query" in f.message for f in findings(run(pq), "X-FEATURE"))


def test_data_bars_are_reported_as_not_confirmed(tmp_path: Path) -> None:
    def bars(wb: Any) -> None:
        from openpyxl.formatting.rule import DataBarRule
        wb["calc"].conditional_formatting.add("D9:H9", DataBarRule(start_type="min", end_type="max", color="638EC6"))
    f = findings(run(write_base(tmp_path / "d.xlsx", edit=bars)), "X-FEATURE")[0]
    assert f.severity == "info" and "not confirmed" in f.message


# ----------------------------------------------------------------------------- unreadable files and robustness

def test_unreadable_workbooks_end_with_a_report(tmp_path: Path) -> None:
    junk = tmp_path / "junk.xlsx"
    junk.write_bytes(b"this is not a workbook")
    old = tmp_path / "old.xls"
    old.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600)
    for p in (junk, old):
        rep = run(p)
        assert rules_of(rep) == {"A-UNREADABLE"} and rep.exit_code == 1
        assert rep.findings[0].fix
        assert schemas.validate(rep.to_dict(), "report") == []
    proc = cli(str(junk), "--json", "-")
    assert proc.returncode == 1 and "Traceback" not in proc.stderr
    assert json.loads(proc.stdout)["findings"][0]["rule"] == "A-UNREADABLE"


def test_missing_file_is_refused(tmp_path: Path) -> None:
    proc = cli(str(tmp_path / "nope.xlsx"))
    assert proc.returncode == 2 and "not found" in proc.stderr and "Traceback" not in proc.stderr


def test_a_failing_rule_family_does_not_stop_the_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(ctx: Any) -> None:
        raise KeyError("broken")
    fams = tuple((n, boom if n == "style" else fn, ids) for n, fn, ids in audit.FAMILIES)
    monkeypatch.setattr(audit, "FAMILIES", fams)
    rep = run(write_base(tmp_path / "b.xlsx", _set("calc", "D5", "=D4*1.1")))
    f = findings(rep, "A-RULE-FAILED")[0]
    assert "style" in f.message and "KeyError" in f.message
    assert "F-LITERAL" in rules_of(rep)
    assert rep.exit_code == 1


# ----------------------------------------------------------------------------- command line (S3)

def test_cli_strict_exit_codes(tmp_path: Path) -> None:
    good = write_base(tmp_path / "g.xlsx")
    warn = write_base(tmp_path / "w.xlsx", _set("calc", "D5", "=D4*1.1"))
    err = write_base(tmp_path / "e.xlsx", _set("raw_sales", "C3", "=C2+C2"))
    assert cli(str(good), "--strict").returncode == 0
    assert cli(str(warn)).returncode == 0
    assert cli(str(warn), "--strict").returncode == 1
    assert cli(str(err)).returncode == 1


def test_cli_json_report(tmp_path: Path) -> None:
    p = write_base(tmp_path / "w.xlsx", _set("calc", "D5", "=D4*1.1"))
    proc = cli(str(p), "--strict", "--json", "-", "--quiet")
    data = json.loads(proc.stdout)
    assert schemas.validate(data, "report") == []
    assert data["command"] == "audit" and data["strict"] is True and data["exit_code"] == 1
    assert data["summary"]["warnings"] >= 1
    f = next(x for x in data["findings"] if x["rule"] == "F-LITERAL")
    assert f["sheet"] == "calc" and f["cell"] == "D5" and f["fix"]
    assert proc.stderr.strip().splitlines() == [proc.stderr.strip().splitlines()[-1]]   # --quiet: summary only
    out = tmp_path / "report.json"
    proc = cli(str(p), "--json", str(out))
    assert json.loads(out.read_text(encoding="utf-8"))["exit_code"] == 0
    assert "F-LITERAL" in proc.stdout and "errors: 0" in proc.stdout


def test_formula_audit_entry_point(tmp_path: Path) -> None:
    p = write_base(tmp_path / "w.xlsx", _set("calc", "D5", "=IFERROR(INDEX(h_sales!$C:$C,MATCH(1,h_sales!$A:$A,0)),0)"))
    a = cli(str(p), "--strict", "--json", "-", script="formula_audit.py")
    b = cli(str(p), "--strict", "--json", "-")
    assert a.returncode == b.returncode == 1
    ja, jb = json.loads(a.stdout), json.loads(b.stdout)
    assert ja["summary"] == jb["summary"]
    assert [f["rule"] for f in ja["findings"]] == [f["rule"] for f in jb["findings"]]


def test_rules_filter(tmp_path: Path) -> None:
    p = write_base(tmp_path / "w.xlsx", _set("calc", "D5", "=TODAY()*1.1"))
    rep = run(p, rules=["F-VOLATILE"])
    assert rules_of(rep) == {"F-VOLATILE"}
    with pytest.raises(common.Refusal, match="F-VOLATIL"):
        run(p, rules=["F-VOLATIL"])
    assert cli(str(p), "--rules", "X-NOPE").returncode == 2


def test_v3_manifest_rules_are_listed_with_their_family() -> None:
    """The v3 rules (their behaviour is tested in test_v3_audit.py): severity, needs, and the family that runs
    them, so --rules selects each one."""
    assert (audit.RULES["CT-VERSION"].severity, audit.RULES["CT-VERSION"].needs) == ("error", "manifest")
    assert (audit.RULES["R-TYPE"].severity, audit.RULES["R-TYPE"].needs) == ("error", "manifest")
    assert (audit.RULES["W-SELF-CHECK"].severity, audit.RULES["W-SELF-CHECK"].needs) == ("warning", "manifest")
    families = {name: ids for name, _fn, ids in audit.FAMILIES}
    assert {"CT-VERSION", "R-TYPE"} <= families["manifest raw-sheet"]
    assert "W-SELF-CHECK" in families["check"]
    assert audit._parse_rules("ct-version,r-type,w-self-check") == {"CT-VERSION", "R-TYPE", "W-SELF-CHECK"}


def test_c_alert_is_a_check_rule_of_its_own() -> None:
    """v4 (J-1): a raised alert is C-ALERT (information), run by the check family, so --rules selects it; its
    behaviour is tested in test_v4_audit.py."""
    assert (audit.RULES["C-ALERT"].severity, audit.RULES["C-ALERT"].needs) == ("info", "values")
    families = {name: ids for name, _fn, ids in audit.FAMILIES}
    assert {"C-ALERT", "C-FAIL"} <= families["check"]
    assert audit._parse_rules("c-alert") == {"C-ALERT"}


def test_c_recompute_is_a_check_rule_of_its_own() -> None:
    """v5 (A-aera-3, C-sol-5): the rule entry C-RECOMPUTE is in the check family, so --rules selects it; the
    recomputation itself is lib/recompute.py's (tests in test_v5_check.py)."""
    info = audit.RULES["C-RECOMPUTE"]
    assert (info.severity, info.needs) == ("error", "manifest")
    assert info.source == "r4 verdict A-aera-3 and C-sol-5; design 9"
    families = {name: ids for name, _fn, ids in audit.FAMILIES}
    assert "C-RECOMPUTE" in families["check"]
    assert audit._parse_rules("c-recompute") == {"C-RECOMPUTE"}


def test_v6_rule_entries_are_listed_with_their_family() -> None:
    """v6 (r5 verdict A-aera-6 and C-sol-3): the rule entries F-ARGS (lib/formula.py reports it; tests in
    test_v6_formula.py) and H-BEYOND (the helper-column family; tests in test_v6_audit.py), so --rules selects
    each one."""
    f_args, beyond = audit.RULES["F-ARGS"], audit.RULES["H-BEYOND"]
    assert (f_args.severity, f_args.needs) == ("error", "formula")
    assert f_args.title == ("a listed function with too few or too many arguments (Excel does not open the "
                            "workbook)")
    assert f_args.source == ("r5 verdict A-aera-6; Microsoft's function reference (references/model-spec-format.md, "
                             "Argument counts)")
    assert (beyond.severity, beyond.needs, beyond.title) == ("error", "manifest",
                                                              "helper formulas below the last raw row")
    assert beyond.source == "r5 verdict C-sol-3; check.py H-BEYOND (v2)"
    families = {name: ids for name, _fn, ids in audit.FAMILIES}
    assert "F-ARGS" in families["formula"]
    assert "H-BEYOND" in families["helper-column"]
    assert audit._parse_rules("f-args,h-beyond") == {"F-ARGS", "H-BEYOND"}


def test_every_rule_has_a_source_and_a_test() -> None:
    text = Path(__file__).read_text(encoding="utf-8")
    for rid, info in audit.RULES.items():
        assert info.source and info.title and info.severity in common.SEVERITIES, rid
        assert f'"{rid}"' in text, f"no test names {rid}"


# ----------------------------------------------------------------------------- the manifest base

def _style_cells(wb: Any, manifest: dict[str, Any]) -> None:
    """Give every typed cell the dictionary's look, as rotm.py build does."""
    from openpyxl.styles import Border, Font, PatternFill, Side

    from lib import dictionary, manifest as mmod
    d = dictionary.load_dictionary()
    for entry in manifest["sheets"]:
        ws = wb[entry["name"]]
        size = entry["font_size"]
        ranges = mmod.cell_type_ranges(manifest, entry["name"])
        types = entry.get("types")
        for row in ws.iter_rows():
            for c in row:
                if c.value is None:
                    continue
                ct = mmod.cell_type_at(ranges, c.row, c.column)
                if ct is None:
                    c.font = Font(name="Arial", size=size)
                    continue
                st = d.style(ct)
                c.font = Font(name="Arial", size=d.size_for(ct, size), bold=st.bold,
                              color=None if st.font_color in (None, "000000") else st.font_color)
                if st.fill:
                    c.fill = PatternFill("solid", fgColor=st.fill)
                if st.border:
                    c.border = Border(**{k: Side(style=v["style"], color=v["color"]) for k, v in st.border.items()})
                if st.number_format != "item":
                    c.number_format = st.number_format
                elif types and c.row >= 2:
                    c.number_format = d.number_format(d.format_for_column_type(types[c.column - 1]))


def manifest_sheets(rows: int = 5) -> dict[str, dict[str, Any]]:
    raw: dict[str, Any] = {"A1": "invoice_id", "B1": "region", "C1": "amount"}
    hs: dict[str, Any] = {"A1": "row", "B1": "in_raw", "C1": "amount"}
    for i in range(1, rows + 1):
        r = i + 1
        raw.update({f"A{r}": f"INV{i:04d}", f"B{r}": ["East", "West"][i % 2], f"C{r}": 100 * i})
        hs.update({f"A{r}": "=ROW()", f"B{r}": f'=--(INDEX(raw_sales!$A:$A,A{r})<>"")',
                   f"C{r}": f"=INDEX(raw_sales!$C:$C,A{r})"})
    return {
        "inputs": {"B1": "Inputs", "B2": "Master check", "C2": "=checks!$C$2", "B5": "Rows, minimum", "D5": 1,
                   "B6": "Rows, maximum", "D6": 100},
        "raw_sales": raw,
        "h_sales": hs,
        "calc": {"B1": "Calculation", "B2": "Master check", "C2": "=checks!$C$2", "B6": "Region", "D6": "Q1",
                 "E6": "Q2", "F6": "Q3", "B7": "East", "B8": "West",
                 **{f"{col}{r}": f"=SUM(h_sales!$C:$C)*h_sales!$B$2" for col in "DEF" for r in (7, 8)}},
        "checks": {"B1": "Checks", "B2": "Master check",
                   "C2": "=--(OR(SUM(I5:I6)>0,COUNT(I5:I6)<>COUNTA(B5:B6)))",
                   "B4": "Id", "I4": "Result",
                   "B5": "sales_rows", "D5": "=COUNTA(raw_sales!$A:$A)-1", "E5": "=inputs!$D$5", "F5": "=inputs!$D$6",
                   "I5": "=--(OR(D5<E5,D5>F5))",
                   "B6": "total_ties", "D6": "=calc!$D$7", "E6": "=SUM(raw_sales!$C:$C)", "G6": "=D6-E6",
                   "H6": "=inputs!$D$5", "I6": "=--(ABS(G6)>H6)"},
    }


def manifest_for(rows: int = 5) -> dict[str, Any]:
    return {
        "rotm_manifest": 1, "plugin_version": "0.1.0", "workbook": "model.xlsx", "spec": None,
        "settings": {"depth_limit": 2, "length_limit": 100, "allowed_literals": [0, 1], "product": "both"},
        "sheets": [
            {"name": "inputs", "type": "inputs", "font_size": 9},
            {"name": "raw_sales", "type": "raw", "font_size": 8, "table": "sales", "excel_table": "tbl_raw_sales",
             "landing": "static", "columns": ["invoice_id", "region", "amount"], "types": ["text", "text", "number"],
             "rows": rows, "digest": None, "snapshot": "raw/sales.csv"},
            {"name": "h_sales", "type": "helpers", "font_size": 8, "table": "sales", "first_row": 2,
             "last_row": rows + 1, "capacity": rows,
             "columns": [{"id": "row", "col": "A", "kind": "row", "formula": "=ROW()"},
                         {"id": "in_raw", "col": "B", "kind": "flag", "formula": '=--(INDEX(raw_sales!$A:$A,A2)<>"")'},
                         {"id": "amount", "col": "C", "kind": "fetch", "raw_column": "amount",
                          "formula": "=INDEX(raw_sales!$C:$C,A2)"}]},
            {"name": "calc", "type": "calc", "font_size": 8,
             "blocks": [{"id": "by_region", "shape": "grid", "body": "D7:F8", "total": None, "checks": ["total_ties"]}]},
            {"name": "checks", "type": "checks", "font_size": 9, "master": "C2", "results": "I5:I6", "ids": "B5:B6"}],
        "inputs": {"rows_min": "inputs!D5", "rows_max": "inputs!D6"},
        "checks": [{"id": "sales_rows", "kind": "range", "result": "checks!I5", "auto": True, "targets": ["raw_sales"],
                    "label": "raw_sales rows within the contract"},
                   {"id": "total_ties", "kind": "equal", "result": "checks!I6", "label": "block total ties raw total"}],
        "master_check": "checks!C2", "master_links": ["inputs!C2", "calc!C2"],
        "cell_types": {
            "inputs": [["B1", "title"], ["B2", "label"], ["C2", "master_check"], ["B5:B6", "row_header"],
                       ["D5:D6", "input"]],
            "raw_sales": [["A1:C1", "col_header"], [f"A2:C{rows + 1}", "raw"]],
            "h_sales": [["A1:C1", "col_header"], [f"A2:C{rows + 1}", "calc"]],
            "calc": [["B1", "title"], ["B2", "label"], ["C2", "master_check"], ["B6", "row_header"],
                     ["D6:F6", "col_header"], ["B7:B8", "row_header"], ["D7:F8", "calc"]],
            "checks": [["B1", "title"], ["B2", "label"], ["C2", "master_check"], ["B4:I4", "col_header"],
                       ["B5:B6", "row_header"], ["D5:H6", "calc"], ["I5:I6", "check"]]},
    }


def write_model(folder: Path, mutate_sheets: Callable[[dict[str, dict[str, Any]]], None] | None = None,
                mutate_manifest: Callable[[dict[str, Any]], None] | None = None, *, rows: int = 5,
                after: Callable[[Any], None] | None = None, record_digest: bool = True) -> Path:
    """model.xlsx and model.manifest.json in `folder`; the raw digest is the sheet's digest before mutation."""
    import openpyxl

    from lib import manifest as mmod
    folder.mkdir(parents=True, exist_ok=True)
    sheets = manifest_sheets(rows)
    man = manifest_for(rows)
    path = folder / "model.xlsx"

    def save(sh: dict[str, dict[str, Any]], m: dict[str, Any]) -> None:
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        for name, cells in sh.items():
            ws = wb.create_sheet(name)
            for coord, v in cells.items():
                ws[coord] = v
        _style_cells(wb, m)
        if after:
            after(wb)
        wb.save(path)

    save(sheets, man)
    if record_digest:
        digest, n, _h = mmod.sheet_digest(path, "raw_sales", ["text", "text", "number"])
        man["sheets"][1]["digest"] = digest
        man["sheets"][1]["rows"] = n
    if mutate_sheets:
        mutate_sheets(sheets)
    if mutate_manifest:
        mutate_manifest(man)
    save(sheets, man)
    mmod.write_manifest(path, man)
    return path


@pytest.fixture
def model(tmp_path: Path) -> Path:
    return write_model(tmp_path / "m")


def test_manifest_base_passes_strict(model: Path) -> None:
    rep = run(model)
    assert rep.data["mode"] == "manifest"
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    assert rules_of(rep) == {"V-NOT-RECALC"}


def test_raw_changed_after_landing(tmp_path: Path) -> None:
    p = write_model(tmp_path / "m", _set("raw_sales", "C3", 999))
    f = findings(run(p), "R-CHANGED")[0]
    assert f.sheet == "raw_sales" and "landed snapshot" in f.message and "--accept-refresh" in f.fix


def test_raw_headers_against_the_contract(tmp_path: Path) -> None:
    p = write_model(tmp_path / "m", _set("raw_sales", "B1", "Region"))
    f = findings(run(p), "R-CONTRACT")[0]
    assert f.cell == "B1" and "'Region'" in f.message and "'region'" in f.message


def test_helper_column_inconsistent_manifest(tmp_path: Path) -> None:
    p = write_model(tmp_path / "m", _set("h_sales", "C4", "=INDEX(raw_sales!$B:$B,A4)"))
    f = findings(run(p), "H-INCONSISTENT")[0]
    assert f.severity == "error" and f.cells == ["C4"] and "inferred" not in f.message


def test_helper_gap_manifest(tmp_path: Path) -> None:
    def gap(s: dict[str, dict[str, Any]]) -> None:
        del s["h_sales"]["C5"]
        s["h_sales"]["C4"] = 300
    f = findings(run(write_model(tmp_path / "m", gap)), "H-GAP")[0]
    assert f.severity == "error" and set(f.cells) == {"C4", "C5"}
    assert "empty or typed value" in f.message


def test_helper_coverage_manifest(tmp_path: Path) -> None:
    def grow(s: dict[str, dict[str, Any]]) -> None:
        s["raw_sales"].update({"A7": "INV0006", "B7": "East", "C7": 600, "A8": "INV0007", "B8": "West", "C8": 700})
    rep = run(write_model(tmp_path / "m", grow))
    f = findings(rep, "H-COVERAGE")[0]
    assert "raw_sales has 7 data rows (rows 2 to 8)" in f.message
    assert "covers rows 2 to 6" in f.message and "2 raw rows (7 to 8) are not covered" in f.message
    assert "extend" in f.fix and "--table sales" in f.fix


def test_block_inconsistent_manifest(tmp_path: Path) -> None:
    p = write_model(tmp_path / "m", _set("calc", "E8", "=SUM(h_sales!$C:$C)"))
    f = findings(run(p), "B-INCONSISTENT")[0]
    assert f.cells == ["E8"] and "block 'by_region'" in f.message


def test_master_check_missing_manifest(tmp_path: Path) -> None:
    def no_master(m: dict[str, Any]) -> None:
        m["master_check"] = None
    f = findings(run(write_model(tmp_path / "m", mutate_manifest=no_master)), "C-MISSING")[0]
    assert f.severity == "error"


def test_check_result_without_formula_manifest(tmp_path: Path) -> None:
    """A registered check result that holds a typed value reads as passing whatever the model holds (A-aera-11)."""
    rep = run(write_model(tmp_path / "m", _set("checks", "I5", 0)))
    hits = findings(rep, "C-MISSING")
    assert [(f.sheet, f.cell, f.severity) for f in hits] == [("checks", "I5", "error")], [f.line() for f in hits]
    assert "sales_rows" in hits[0].message and "checks!I5" in hits[0].message and "--force" in hits[0].fix
    assert rep.exit_code == 1


def test_block_without_check(tmp_path: Path) -> None:
    def unchecked(m: dict[str, Any]) -> None:
        m["sheets"][3]["blocks"][0]["checks"] = []
    f = findings(run(write_model(tmp_path / "m", mutate_manifest=unchecked)), "C-BLOCK")[0]
    assert "by_region" in f.message


def test_check_wiring(tmp_path: Path) -> None:
    def outside(m: dict[str, Any]) -> None:
        m["checks"].append({"id": "late", "kind": "zero", "result": "checks!I9"})
    rep = run(write_model(tmp_path / "m", _set("checks", "I9", "=--(D5<>D5)"), outside))
    assert any(f.cell == "I9" for f in findings(rep, "C-WIRING"))
    rep = run(write_model(tmp_path / "n", _set("calc", "C2", "=checks!$C$3")))
    f = next(f for f in findings(rep, "C-WIRING") if f.sheet == "calc")
    assert "=checks!$C$2" in f.message


def test_check_fails_manifest(tmp_path: Path) -> None:
    def cached(fail: bool) -> dict[tuple[str, str], Any]:
        out = {}
        for name, cells in manifest_sheets().items():
            for coord, v in cells.items():
                if isinstance(v, str) and v.startswith("="):
                    out[(name, coord)] = 0 if name in ("checks", "inputs") or coord == "C2" else 100.0
        if fail:
            out[("checks", "I6")] = 1
        return out
    good = write_model(tmp_path / "g")
    patch_cached(good, cached(False))
    rep = run(good)
    assert rep.exit_code == 0 and "C-FAIL" not in rules_of(rep), [f.line() for f in rep.findings]
    bad = write_model(tmp_path / "b")
    patch_cached(bad, cached(True))
    f = findings(run(bad), "C-FAIL")[0]
    assert f.severity == "error" and f.cell == "I6" and "total_ties" in f.message


def test_bold_per_cell_type(tmp_path: Path) -> None:
    def plain(wb: Any) -> None:
        from openpyxl.styles import Font
        wb["calc"]["D6"].font = Font(name="Arial", size=8, bold=False)
    f = findings(run(write_model(tmp_path / "m", after=plain)), "S-BOLD")[0]
    assert f.cell == "D6" and "col_header" in f.message


def test_fill_per_cell_type(tmp_path: Path) -> None:
    def nofill(wb: Any) -> None:
        from openpyxl.styles import PatternFill
        wb["calc"]["E6"].fill = PatternFill(fill_type=None)
    f = findings(run(write_model(tmp_path / "m", after=nofill)), "S-FILL")[0]
    assert f.cell == "E6" and "D9D9D9" in f.message


def test_size_per_sheet_type(tmp_path: Path) -> None:
    def small(wb: Any) -> None:
        from openpyxl.styles import Font
        wb["checks"]["B5"].font = Font(name="Arial", size=8)
    f = findings(run(write_model(tmp_path / "m", after=small)), "S-SIZE")[0]
    assert f.cell == "B5" and "gives 9" in f.message


def test_font_colour_per_cell_type(tmp_path: Path) -> None:
    def grey(wb: Any) -> None:
        from openpyxl.styles import Font
        wb["calc"]["D7"].font = Font(name="Arial", size=8, color="595959")
    f = findings(run(write_model(tmp_path / "m", after=grey)), "S-FONTCOLOR")[0]
    assert f.cell == "D7" and "595959" in f.message


def test_number_format_manifest(tmp_path: Path) -> None:
    def general(wb: Any) -> None:
        wb["checks"]["I5"].number_format = "General"
    f = findings(run(write_model(tmp_path / "m", after=general)), "S-NUMFMT")[0]
    assert f.cell == "I5" and "FAIL" in f.message


def test_audit_is_recorded_in_the_model_folder(model_dir: Path) -> None:
    from lib import modelfolder
    p = write_model(model_dir)
    rep = audit.audit_workbook(p, strict=True)
    status = modelfolder.read_status(model_dir)
    entry = status["workbooks"]["model.xlsx"]["audit"]
    assert entry["sha256"] == common.file_sha256(p) and entry["strict"] is True
    assert entry["exit_code"] == rep.exit_code == 0 and entry["errors"] == entry["warnings"] == 0
    assert json.loads((model_dir / "reports" / "audit.json").read_text(encoding="utf-8"))["command"] == "audit"
    rep2 = audit.audit_workbook(model_dir, strict=True, record=False)    # a model folder audits its workbook
    assert rep2.target.endswith("model.xlsx")


def test_audit_never_writes_the_workbook(model: Path) -> None:
    before = common.file_sha256(model)
    run(model)
    run(model, record=True)
    assert common.file_sha256(model) == before


# ----------------------------------------------------------------------------- Excel (real cached values)

@pytest.mark.excel
def test_after_excel_recalculation(tmp_path: Path) -> None:
    from lib import excel
    good = write_model(tmp_path / "g")
    excel.recalc(good)
    rep = run(good)
    assert rep.exit_code == 0 and "V-NOT-RECALC" not in rules_of(rep), [f.line() for f in rep.findings]
    bad = write_model(tmp_path / "b", lambda s: s["checks"].update({"E6": "=SUM(raw_sales!$C:$C)+inputs!$D$6",
                                                                      "J5": "=1/0"}))
    excel.recalc(bad)
    rep = run(bad)
    assert "C-FAIL" in rules_of(rep) and "V-ERROR" in rules_of(rep)
    assert any(f.cell == "I6" for f in findings(rep, "C-FAIL"))


# ----------------------------------------------------------------------------- foreign workbooks (F06)

def _foreign_files(folder: Path) -> list[Path]:
    files = sorted(p for p in folder.glob("*.xls*") if p.is_file())
    return [p for p in files if not re.search(r"_(theme|compat|split)\.xlsx$|\.bak_", p.name)]


def test_foreign_workbooks_end_with_a_report(foreign_dir: Path, tmp_path: Path) -> None:
    """Every supplied workbook ends with a valid report; the set is tested for what it contains (J-2): a
    workbook with a Power Query part gets the X-FEATURE finding that names it. No feature is required."""
    from lib import workbook
    files = _foreign_files(foreign_dir)
    assert files
    for src in files:
        p = tmp_path / src.name
        shutil.copy2(src, p)                           # never audit the copies in place either
        rep = run(p)
        data = rep.to_dict()
        assert schemas.validate(data, "report") == [], src.name
        assert rep.exit_code in (0, 1), src.name
        assert "A-RULE-FAILED" not in rules_of(rep), (src.name, [f.line() for f in findings(rep, "A-RULE-FAILED")])
        if rep.data.get("mode") == "unreadable":
            assert rules_of(rep) == {"A-UNREADABLE"}, src.name
            continue
        assert rep.data["mode"] in ("inferred", "manifest"), src.name
        if workbook.package_parts(p).has_power_query:
            assert any("Power Query" in f.message for f in findings(rep, "X-FEATURE")), src.name


def test_foreign_workbooks_through_the_command_line(foreign_dir: Path, tmp_path: Path) -> None:
    """Workbooks with a query or a chart, else any readable workbook (J-2); skipped when none is readable."""
    from lib import workbook
    picked, readable = [], []
    for src in _foreign_files(foreign_dir):
        try:
            pkg = workbook.package_parts(src)
        except common.Refusal:
            continue                                   # unreadable: test_foreign_workbooks_end_with_a_report
        readable.append(src)
        if pkg.has_power_query or pkg.charts:
            picked.append(src)
    if not readable:
        pytest.skip(f"no readable workbook in {foreign_dir}")
    for src in (picked or readable)[:4]:
        p = tmp_path / src.name
        shutil.copy2(src, p)
        proc = cli(str(p), "--strict", "--json", "-")
        assert proc.returncode in (0, 1), (src.name, proc.stderr[-500:])
        assert "Traceback" not in proc.stderr
        assert json.loads(proc.stdout)["command"] == "audit"

"""Tests of the v3 audit changes (package P8 of the v3 plan). Owner: P8.

- [J-3]: without a manifest, a run of cells whose formula is only a reference to one cell (a link such as
  =Sheet!B2 or =B2) is not a calculation block and gets no C-BLOCK; a note counts the link runs left out.
- [A-aera-1, A-aera-4, C-sol-1] part 3, the audit's part: CT-VERSION (error) when the raw entry's
  contract_sha256 differs from the agreed contract's agreement.contract_sha256; a note when the entry has none.
- [A-aera-10], the audit's part: R-TYPE (error) for a cell of an integer, number, date, datetime or boolean
  raw column stored as text.
- [A-aera-15, A-aera-17], the audit's part: every V-NO-VALUE of the audit has the fix N-AUDIT.
- [A-aera-19], the audit's part: W-SELF-CHECK (warning) for a registered check whose actual and expected cells
  hold the same formula or links to the same cell.
- [J-2], the audit's part: S-NUMFMT expects the HDR-FORMAT format on header cells that show an input-list item.
- [A-aera-8, C-sol-6], the audit's part: a helper sheet with zero active rows gets no H-GAP, H-COVERAGE,
  H-INCONSISTENT or C-BLOCK.
Models are built or copied under tmp_path; no test needs Excel.
"""
from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from lib import audit, build, common, dictionary, manifest as mmod
from test_build import agree, base_contract, base_spec, make_model
from test_v2_audit import SHIPPED_MODELS, SKILL, blocks_of, cli_audit, run, t5_like, write_wb

N_AUDIT_START = "the saved file holds no value for this formula: "
SELF_FIX = "point one side at an independent total: a raw column total, a control total or another block's total"


def _copy(src: Path, dest: Path) -> Path:
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("*.bak_*"))
    return dest


def _hits(rep: common.Report, rule: str) -> list[common.Finding]:
    return [f for f in rep.findings if f.rule == rule]


def _json_audit(target: Path, *args: str) -> tuple[int, dict[str, Any]]:
    proc = cli_audit(target, "--strict", "--json", "-", "--quiet", "--no-record", *args)
    assert "Traceback" not in proc.stderr, proc.stderr[-800:]
    return proc.returncode, json.loads(proc.stdout)


# ============================================================================= [J-3] link runs are no blocks

def _with_links() -> dict[str, dict[str, Any]]:
    """The T5-like workbook (Settlement!B2:B6 is a calculation block no check reads) plus a sheet of link
    columns: links to another sheet, links on the same sheet, and one absolute link copied down."""
    sheets = t5_like()
    links: dict[str, Any] = {"A1": "Item", "B1": "Revenue", "C1": "Revenue again", "D1": "Price"}
    for r in range(2, 7):
        links.update({f"A{r}": f"Item {r}", f"B{r}": f"=Sales!C{r}", f"C{r}": f"=B{r}", f"D{r}": "=Inputs!$B$2"})
    sheets["Links"] = links
    return sheets


def test_link_columns_are_not_calculation_blocks(tmp_path: Path) -> None:
    rep = run(write_wb(tmp_path / "links.xlsx", _with_links()))
    hits = blocks_of(rep)
    assert [(f.sheet, f.data["range"]) for f in hits] == [("Settlement", "B2:B6")], [f.line() for f in hits]
    assert "inferred calculation blocks: 2; 1 read by a check cell, 1 read by none (C-BLOCK)" in rep.notes
    link_notes = [n for n in rep.notes if "run(s) of links left out" in n]
    assert link_notes == ["inferred calculation blocks: 3 run(s) of links left out (each cell holds only a "
                          "reference to one cell, such as =Sheet!B2 or =B2; a link is not a calculation, so "
                          "C-BLOCK does not apply to it)"], rep.notes


def test_a_link_row_and_a_plus_link_are_left_out_and_a_negated_link_is_a_block(tmp_path: Path) -> None:
    sheets = t5_like()
    sheets["Links"] = {"A1": "Item", "B2": "=+Sales!B2", "B3": "=+Sales!B3", "B4": "=+Sales!B4",
                       "C6": "=Inputs!B2", "D6": "=Inputs!C2", "E6": "=Inputs!D2",
                       "B9": "=-Sales!C2", "B10": "=-Sales!C3"}
    rep = run(write_wb(tmp_path / "row.xlsx", sheets))
    ranges = sorted((f.sheet, f.data["range"]) for f in blocks_of(rep))
    assert ranges == [("Links", "B9:B10"), ("Settlement", "B2:B6")], [f.line() for f in blocks_of(rep)]
    assert any(n.startswith("inferred calculation blocks: 2 run(s) of links left out") for n in rep.notes)


def test_the_t5_like_block_is_still_reported_and_no_link_note_without_links(tmp_path: Path) -> None:
    rep = run(write_wb(tmp_path / "t5.xlsx", t5_like()))
    assert [(f.sheet, f.data["range"]) for f in blocks_of(rep)] == [("Settlement", "B2:B6")]
    assert not [n for n in rep.notes if "links left out" in n]


# ============================================================================= CT-VERSION

def _starter(tmp_path: Path) -> Path:
    return _copy(SKILL / "templates" / "starter", tmp_path / "starter")


def _raw_entry(man: dict[str, Any], table: str) -> dict[str, Any]:
    return next(s for s in man["sheets"] if s.get("type") == "raw" and s.get("table") == table)


def _set_contract_hash(model: Path, value: str | None) -> None:
    path = model / "model.manifest.json"
    man = json.loads(path.read_text(encoding="utf-8"))
    entry = _raw_entry(man, "lines")
    if value is None:
        entry.pop("contract_sha256", None)
    else:
        entry["contract_sha256"] = value
    path.write_text(json.dumps(man, indent=1), encoding="utf-8")


def test_ct_version_compares_the_manifest_hash_with_the_agreed_contract(tmp_path: Path) -> None:
    model = _starter(tmp_path)
    agreed = json.loads((model / "contracts" / "lines.contract.json").read_text(encoding="utf-8"))
    agreed_hash = agreed["agreement"]["contract_sha256"]
    note = ("raw_lines: the manifest records no contract hash (built before v3), so the contract version was not "
            "compared")
    _set_contract_hash(model, None)                                     # absent: the note
    rep = run(model / "model.xlsx")
    assert _hits(rep, "CT-VERSION") == [] and note in rep.notes
    _set_contract_hash(model, agreed_hash)                              # equal: nothing
    rep = run(model / "model.xlsx")
    assert _hits(rep, "CT-VERSION") == [] and note not in rep.notes
    other = "0123456789ab" + "f" * 52                                   # another version
    _set_contract_hash(model, other)
    code, data = _json_audit(model)
    hits = [f for f in data["findings"] if f["rule"] == "CT-VERSION"]
    assert code == 1 and len(hits) == 1, data["findings"]
    f = hits[0]
    assert f["severity"] == "error" and f["sheet"] == "raw_lines"
    assert f["message"] == (f"raw_lines was built from another version of its contract: the manifest records "
                            f"sha256:0123456789ab..., contracts/lines.contract.json is agreed as "
                            f"sha256:{agreed_hash[:12]}...")
    assert f["fix"].startswith("the workbook was built from another version of the contract; land again if the "
                               "rows change (")
    assert "land" in f["fix"] and "--table lines --csv <file> --force" in f["fix"] and "build" in f["fix"]
    rep = run(model / "model.xlsx", rules=["CT-VERSION"])               # its own rule id selects it
    assert {x.rule for x in rep.findings} == {"CT-VERSION"}


def test_ct_version_does_not_compare_a_contract_that_is_not_agreed(tmp_path: Path) -> None:
    model = _starter(tmp_path)
    _set_contract_hash(model, "f" * 64)
    path = model / "contracts" / "lines.contract.json"
    c = json.loads(path.read_text(encoding="utf-8"))
    c["agreement"] = {"status": "proposed"}
    path.write_text(json.dumps(c, indent=1), encoding="utf-8")
    assert _hits(run(model / "model.xlsx"), "CT-VERSION") == []


# ============================================================================= R-TYPE

@pytest.fixture
def built_sales(tmp_path: Path) -> Path:
    root = make_model(tmp_path / "m")
    rep = build.build(root / "model.spec.json", strict=False)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    return root


def test_a_number_stored_as_text_is_r_type(built_sales: Path) -> None:
    import openpyxl
    wb_path = built_sales / "model.xlsx"
    assert _hits(run(wb_path), "R-TYPE") == []
    wb = openpyxl.load_workbook(wb_path)
    ws = wb["raw_sales"]
    assert ws["B2"].value == 1 and ws["B1"].value == "line_no"
    ws["B2"].value = "1"                                               # an integer stored as text
    assert ws["C1"].value == "inv_date"
    ws["C3"].value = "2026-01-15"                                      # a date stored as text
    wb.save(wb_path)
    code, data = _json_audit(built_sales)
    hits = sorted((f for f in data["findings"] if f["rule"] == "R-TYPE"), key=lambda f: f["cell"])
    assert code == 1 and [(f["sheet"], f["cell"], f["severity"]) for f in hits] == [
        ("raw_sales", "B2", "error"), ("raw_sales", "C3", "error")], data["findings"]
    assert hits[0]["message"] == "1 cell(s) of integer column line_no in raw_sales are stored as text (first B2: '1')"
    assert hits[1]["message"] == ("1 cell(s) of date column inv_date in raw_sales are stored as text "
                                  "(first C3: '2026-01-15')")
    assert hits[0]["fix"].startswith("land the data again (") and hits[0]["fix"].endswith(
        "--table sales --csv <file> --force) and rebuild; a raw sheet holds landed values only")


def test_text_in_a_text_column_is_not_r_type(built_sales: Path) -> None:
    import openpyxl
    wb_path = built_sales / "model.xlsx"
    wb = openpyxl.load_workbook(wb_path)
    assert wb["raw_sales"]["A1"].value == "invoice_id" and isinstance(wb["raw_sales"]["A2"].value, str)
    wb["raw_sales"]["A2"].value = "000999"
    wb.save(wb_path)
    assert _hits(run(wb_path), "R-TYPE") == []


# ============================================================================= V-NO-VALUE fix N-AUDIT

def test_every_v_no_value_of_the_audit_has_the_fix_n_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from lib import workbook
    model = _copy(SKILL / "examples" / "sales_vs_target", tmp_path / "example")
    monkeypatch.setattr(workbook, "formula_cells_without_value", lambda p: [("calc", "D7"), ("checks", "I5")])
    rep = run(model / "model.xlsx")
    assert rep.data["has_cached_values"] is True
    hits = _hits(rep, "V-NO-VALUE")
    assert sorted(f.sheet for f in hits) == ["calc", "checks"]
    for f in hits:
        assert f.severity == "error"
        assert f.fix.startswith(N_AUDIT_START + "python ") and " check " in f.fix
        assert ("recalculates it in Excel and names a circular reference when there is one; otherwise look at the "
                "formula and its inputs (") in f.fix
        assert " trace " in f.fix and f"{f.sheet}!{f.cell}" in f.fix and f.fix.endswith(")")
        assert "break it" not in f.fix and "cycle" not in f.fix


def test_audit_skill_states_the_v3_rules() -> None:
    path = SKILL.parents[1] / "skills" / "audit" / "SKILL.md"
    if not path.is_file():                              # v4 (A-aera-7): the skill folder alone passes its tests
        pytest.skip("the skill is used without its plugin (PLUGIN/skills/audit/SKILL.md is absent)")
    text = path.read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert "names a circular reference when there is one" in flat
    assert "find the cycle with `rotm.py trace` and break it" not in flat
    for rid in ("CT-VERSION", "R-TYPE", "W-SELF-CHECK", "S-NUMFMT"):
        assert f"`{rid}`" in flat, rid
    assert "only a reference to one cell" in flat


# ============================================================================= W-SELF-CHECK

def _check_row(ws: Any, check_id: str) -> int:
    return next(r for r in range(5, ws.max_row + 1) if ws.cell(r, 2).value == check_id)


def test_a_check_with_the_same_block_total_on_both_sides_is_w_self_check(tmp_path: Path) -> None:
    import openpyxl
    model = _starter(tmp_path)
    spec_path = model / "model.spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    chk = next(c for s in spec["sheets"] if s.get("type") == "checks" for c in s["checks"]
               if c["id"] == "spend_ties_raw")
    chk["expected"] = "{blk:spend.total}"
    spec_path.write_text(json.dumps(spec, indent=1), encoding="utf-8")
    cpath = model / "contracts" / "lines.contract.json"               # the copy's 'example' agreement does not
    common.write_json(cpath, agree(json.loads(cpath.read_text(encoding="utf-8"))))   # count outside the plugin
    rep = build.build(spec_path, force=True, strict=False)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    ws = openpyxl.load_workbook(model / "model.xlsx")["checks"]
    r = _check_row(ws, "spend_ties_raw")
    assert ws.cell(r, 4).value == ws.cell(r, 5).value == "=calc!$G$10"
    arep = run(model / "model.xlsx")
    hits = _hits(arep, "W-SELF-CHECK")
    assert [(f.sheet, f.cell, f.severity) for f in hits] == [("checks", f"D{r}", "warning")], \
        [f.line() for f in arep.findings]
    assert hits[0].message == ("check spend_ties_raw: actual (calc!$G$10) and expected (calc!$G$10) are the same, "
                               "so the check can never fail")
    assert hits[0].fix == SELF_FIX and hits[0].cells == [f"D{r}", f"E{r}"]
    assert arep.exit_code == 1                                        # a warning fails the strict audit


def test_links_to_the_same_cell_written_differently_are_w_self_check(tmp_path: Path) -> None:
    import openpyxl
    model = _starter(tmp_path)
    p = model / "model.xlsx"
    wb = openpyxl.load_workbook(p)
    ws = wb["checks"]
    r = _check_row(ws, "spend_ties_raw")
    assert ws.cell(r, 4).value == "=calc!$G$10"
    ws.cell(r, 5).value = "='calc'!G10"
    wb.save(p)
    hits = _hits(run(p), "W-SELF-CHECK")
    assert [(f.cell, f.data["check"]) for f in hits] == [(f"D{r}", "spend_ties_raw")]
    assert "expected ('calc'!G10)" in hits[0].message
    ws.cell(r, 5).value = "=calc!$G$10+0"                             # an expression is not the same text
    wb.save(p)
    assert _hits(run(p), "W-SELF-CHECK") == []


def test_a_check_build_adds_itself_is_not_compared(tmp_path: Path) -> None:
    import openpyxl
    model = _starter(tmp_path)
    man = json.loads((model / "model.manifest.json").read_text(encoding="utf-8"))
    assert next(c for c in man["checks"] if c["id"] == "lines_cover")["auto"] is True
    p = model / "model.xlsx"
    wb = openpyxl.load_workbook(p)
    ws = wb["checks"]
    r = _check_row(ws, "lines_cover")
    ws.cell(r, 5).value = ws.cell(r, 4).value                         # a generated check with equal sides
    wb.save(p)
    assert _hits(run(p), "W-SELF-CHECK") == []


def test_a_range_check_is_not_compared_and_an_at_most_check_is(tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """The audit compares the kinds build compares (v3 integrator, request 1 of P2): equal and at_most. A range
    check whose actual equals its minimum (column E) can still exceed its maximum (column F)."""
    import openpyxl
    model = _starter(tmp_path)
    p = model / "model.xlsx"
    wb = openpyxl.load_workbook(p)
    ws = wb["checks"]
    r = _check_row(ws, "spend_ties_raw")
    ws.cell(r, 5).value = ws.cell(r, 4).value                         # actual and column E hold the same link
    wb.save(p)
    mpath = model / "model.manifest.json"
    man = json.loads(mpath.read_text(encoding="utf-8"))
    entry = next(c for c in man["checks"] if c["id"] == "spend_ties_raw")
    assert entry["kind"] == "equal" and entry.get("auto") is not True

    def set_kind(kind: str) -> None:
        entry["kind"] = kind
        mpath.write_text(json.dumps(man, indent=1), encoding="utf-8")

    set_kind("range")
    assert _hits(run(p), "W-SELF-CHECK") == []
    with monkeypatch.context() as m:                                  # the rule without the kind filter (v3 P8)
        m.setattr(audit, "SELF_CHECK_KINDS", frozenset({"equal", "at_most", "range"}))
        assert len(_hits(run(p), "W-SELF-CHECK")) == 1
    set_kind("at_most")
    hits = _hits(run(p), "W-SELF-CHECK")
    assert [(f.cell, f.data["check"]) for f in hits] == [(f"D{r}", "spend_ties_raw")]


# ============================================================================= [J-2] HDR-FORMAT

DATES = ["2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30", "2026-05-31", "2026-06-30"]


def _date_list_spec() -> dict[str, Any]:
    """The build fixture plus an input list of ISO dates (no format), a block whose rows come from it and a block
    whose value columns come from it; a check reads each block."""
    spec = base_spec()
    inputs = next(s for s in spec["sheets"] if s["type"] == "inputs")
    inputs["items"].append({"id": "month_ends", "kind": "list", "label": "Month ends", "values": DATES,
                            "source": "test"})
    calc = next(s for s in spec["sheets"] if s["type"] == "calc")
    calc["blocks"].append({"id": "by_month", "title": "Sales by month end", "unit": "KRW thousand",
                           "format": "amount", "rows": {"in": "month_ends", "label": "Month end"},
                           "cols": {"values": ["Amount"]},
                           "cell": "SUMIFS({h:sales.amount_k},{h:sales.month_end},{row})",
                           "total": {"row": False, "col": True}})
    calc["blocks"].append({"id": "by_region_month", "title": "Sales by region and month end", "unit": "KRW thousand",
                           "format": "amount", "rows": {"in": "regions"}, "cols": {"in": "month_ends"},
                           "cell": "SUMIFS({h:sales.amount_k},{h:sales.region},{row},{h:sales.month_end},{col})",
                           "total": {"row": True, "col": True}})
    checks = next(s for s in spec["sheets"] if s["type"] == "checks")
    checks["checks"] += [
        {"id": "by_month_ties", "kind": "equal", "label": "Sales by month end equal the raw total",
         "actual": "SUM({blk:by_month})", "expected": "SUM({raw:sales.amount})/{in:scale}"},
        {"id": "by_region_month_ties", "kind": "equal", "label": "Sales by region and month end equal the raw total",
         "actual": "{blk:by_region_month.total}", "expected": "SUM({raw:sales.amount})/{in:scale}"}]
    return spec


@pytest.fixture
def date_model(tmp_path: Path) -> Path:
    root = make_model(tmp_path / "d", spec=_date_list_spec())
    rep = build.build(root / "model.spec.json", strict=False)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    return root


def _block(root: Path, block_id: str) -> dict[str, Any]:
    man = json.loads((root / "model.manifest.json").read_text(encoding="utf-8"))
    return next(b for s in man["sheets"] for b in s.get("blocks") or [] if b["id"] == block_id)


def _label_cells(b: dict[str, Any]) -> list[str]:
    c1, r1, _c2, r2 = common.range_bounds(b["labels"])
    return [common.a1(c1, r) for r in range(r1, r2 + 1)]


def _header_cells(b: dict[str, Any]) -> list[str]:
    c1, _r1, c2, _r2 = common.range_bounds(b["body"])
    return [common.a1(c, b["header_row"]) for c in range(c1, c2 + 1)]


def _set_formats(path: Path, sheet: str, coords: list[str], fmt: str) -> None:
    import openpyxl
    wb = openpyxl.load_workbook(path)
    for coord in coords:
        wb[sheet][coord].number_format = fmt
    wb.save(path)


def _numfmt_at(rep: common.Report, sheet: str, coords: list[str]) -> list[common.Finding]:
    return [f for f in _hits(rep, "S-NUMFMT") if f.sheet == sheet and set(f.cells or [f.cell]) & set(coords)]


def _cells(hits: list[common.Finding]) -> list[str]:
    return sorted(c for f in hits for c in (f.cells or [f.cell]))


def test_row_headers_of_a_date_list_expect_the_date_format(date_model: Path) -> None:
    d = dictionary.load_dictionary()
    date_fmt = d.number_format("date")
    p = date_model / "model.xlsx"
    rows = _label_cells(_block(date_model, "by_month"))
    assert len(rows) == len(DATES)
    _set_formats(p, "calc", rows, "General")                           # a row header left at General
    hits = _numfmt_at(run(p), "calc", rows)
    assert _cells(hits) == sorted(rows) and len(hits) == 1, [f.line() for f in hits]   # one finding, 6 cells
    assert hits[0].count == len(rows) and hits[0].message == (
        f"number format 'General'; the dictionary gives {date_fmt!r} for row headers that show an item of input "
        "list 'month_ends' (date)")
    _set_formats(p, "calc", rows, date_fmt)                            # the date format: no finding
    assert _numfmt_at(run(p), "calc", rows) == []


def test_column_headers_of_a_date_list_expect_the_month_format(date_model: Path) -> None:
    month_fmt = dictionary.load_dictionary().number_format("month")
    p = date_model / "model.xlsx"
    cols = _header_cells(_block(date_model, "by_region_month"))
    assert len(cols) == len(DATES)
    _set_formats(p, "calc", cols, "General")
    hits = _numfmt_at(run(p), "calc", cols)
    assert _cells(hits) == sorted(cols) and all(repr(month_fmt) in f.message and "column headers" in f.message
                                                for f in hits), [f.line() for f in hits]
    _set_formats(p, "calc", cols, month_fmt)
    assert _numfmt_at(run(p), "calc", cols) == []


def test_row_headers_of_a_text_list_take_general_or_the_text_format(date_model: Path) -> None:
    p = date_model / "model.xlsx"
    rows = _label_cells(_block(date_model, "actual"))                  # rows from the text list 'regions'
    for fmt in ("General", "@"):
        _set_formats(p, "calc", rows, fmt)
        assert _numfmt_at(run(p), "calc", rows) == [], fmt
    _set_formats(p, "calc", rows, "0.0%")
    assert _cells(_numfmt_at(run(p), "calc", rows)) == sorted(rows)


def test_header_cells_are_not_compared_without_the_specification(date_model: Path) -> None:
    p = date_model / "model.xlsx"
    rows = _label_cells(_block(date_model, "by_month"))
    (date_model / "model.spec.json").rename(date_model / "model.spec.json.moved")
    for fmt in ("General", dictionary.load_dictionary().number_format("date")):
        _set_formats(p, "calc", rows, fmt)
        rep = run(p)
        assert _numfmt_at(rep, "calc", rows) == [], fmt
        assert any("header cell(s) that show an input-list item were not compared" in n for n in rep.notes)


# ============================================================================= zero helper rows

def test_a_helper_sheet_with_zero_active_rows_gets_no_helper_finding(tmp_path: Path) -> None:
    import openpyxl
    src = Path(__file__).resolve().parent / "data" / "build" / "sales.csv"
    with open(src, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    one = tmp_path / "one.csv"
    with open(one, "w", encoding="utf-8", newline="") as f:
        csv.writer(f, lineterminator="\n").writerows(rows[:2])
    contract = base_contract()
    contract["expect"] = {"rows": {"min": 1, "max": 1000}}
    root = make_model(tmp_path / "z", contract=contract, csv_path=one)
    rep = build.build(root / "model.spec.json", strict=False)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    p = root / "model.xlsx"
    wb = openpyxl.load_workbook(p)
    for name in ("raw_sales", "h_sales"):
        ws = wb[name]
        assert ws.cell(2, 1).value is not None and ws.cell(3, 1).value is None
        for c in range(1, ws.max_column + 1):
            ws.cell(2, c).value = None
    wb.save(p)
    man = mmod.load_manifest(p)
    raw = _raw_entry(man, "sales")
    digest, n, _hdr = mmod.sheet_digest(p, "raw_sales", raw["types"])
    assert n == 0
    raw.update({"digest": digest, "rows": 0})
    helpers = next(s for s in man["sheets"] if s.get("type") == "helpers" and s.get("table") == "sales")
    helpers.update({"capacity": 0, "first_row": 2, "last_row": 1})
    mmod.write_manifest(p, man)
    arep = run(p)
    bad = [f.line() for f in arep.findings
           if f.rule in ("H-GAP", "H-COVERAGE", "H-INCONSISTENT", "C-BLOCK") and f.sheet == "h_sales"]
    assert bad == []
    assert "R-CHANGED" not in {f.rule for f in arep.findings}


# ============================================================================= the shipped models

@pytest.mark.parametrize("src", SHIPPED_MODELS, ids=[p.name for p in SHIPPED_MODELS])
def test_shipped_models_get_none_of_the_v3_findings(src: Path, tmp_path: Path) -> None:
    if not (src / "model.xlsx").is_file():
        pytest.skip(f"{src.name} has no built workbook")
    model = _copy(src, tmp_path / src.name)
    rep = run(model / "model.xlsx")
    new = [f.line() for f in rep.findings if f.rule in ("W-SELF-CHECK", "CT-VERSION", "R-TYPE", "S-NUMFMT")]
    assert new == []
    assert "A-RULE-FAILED" not in {f.rule for f in rep.findings}

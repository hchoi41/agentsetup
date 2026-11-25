"""Tests for lib/check.py: check and extend (design sections 7, 9, 15; plan F09, F10, F27). Owner: M5.

The model fixture is written here with openpyxl in the layout of design sections 6.3, 7.1, 9.1, 9.2 and 9.4
(raw_sales alone on its sheet from A1, the helper sheet h_sales with position-based INDEX fetches, the checks
sheet with result column I and the master check in C2, and the sidecar manifest of section 6.8), so these tests do
not depend on lib.build. Tests marked `excel` drive Excel through lib.excel (one job at a time behind the lock).
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib import check, common, contract, manifest, modelfolder

SCRIPTS = Path(__file__).resolve().parents[1]
ROTM = SCRIPTS / "rotm.py"
HEADER = ["invoice_id", "line_no", "inv_date", "region", "amount"]
TYPES = ["text", "integer", "date", "text", "number"]
REGIONS = ["East", "North", "South", "West"]
REPLY = "agreed, land it as proposed."


# ----------------------------------------------------------------------------- the model fixture

def sales_values(n: int) -> list[list]:
    """n typed rows: (invoice_id text with leading zeros, line_no, date, region, amount)."""
    rows = []
    for i in range(n):
        rows.append([f"00{1000 + i // 2}", i % 2 + 1, dt.date(2026, 1, 1) + dt.timedelta(days=i % 28),
                     REGIONS[i % 4], 100 + 7.5 * i])
    return rows


def write_source_csv(path: Path, n: int, *, rows: list[list] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = rows if rows is not None else sales_values(n)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(HEADER)
        for r in data:
            w.writerow([manifest.normalise_value(v, t) for v, t in zip(r, TYPES)])
    return path


def _helper_formulas(r: int) -> list[str]:
    return [
        "=ROW()",
        f'=--(INDEX(raw_sales!$A:$A,A{r})<>"")',
        f'=INDEX(raw_sales!$A:$A,A{r})&""',
        f"=INDEX(raw_sales!$B:$B,A{r})",
        f"=INDEX(raw_sales!$C:$C,A{r})",
        f'=INDEX(raw_sales!$D:$D,A{r})&""',
        f"=INDEX(raw_sales!$E:$E,A{r})",
        f"=EOMONTH(E{r},0)",
        f"=--(COUNTIFS($C:$C,C{r},$D:$D,D{r},$B:$B,1)*B{r}>1)",
    ]


HELPER_COLUMNS = [("row", "A", "row"), ("in_raw", "B", "flag"), ("invoice_id", "C", "fetch"),
                  ("line_no", "D", "fetch"), ("inv_date", "E", "fetch"), ("region", "F", "fetch"),
                  ("amount", "G", "fetch"), ("month_end", "H", "derived"), ("dup", "I", "dup")]


def write_workbook(path: Path, rows: list[list], *, capacity: int, rows_min: int = 1, rows_max: int = 100000,
                   total: float | None = None, chart: bool = False) -> dict:
    """The model workbook and its manifest (design 6.8); returns the manifest."""
    import openpyxl
    from openpyxl.worksheet.table import Table, TableStyleInfo
    wb = openpyxl.Workbook()
    inputs = wb.active
    inputs.title = "inputs"
    inputs["B1"] = "Inputs"
    inputs["B2"], inputs["C2"] = "Master check", "=checks!$C$2"
    inputs["B4"] = "Assumptions"
    inputs["B5"], inputs["C5"], inputs["D5"] = "Check tolerance", "KRW", 0.001
    inputs["B6"] = "Contract controls"
    inputs["B7"], inputs["C7"], inputs["D7"] = "sales rows, minimum", "rows", rows_min
    inputs["B8"], inputs["C8"], inputs["D8"] = "sales rows, maximum", "rows", rows_max
    if total is not None:
        inputs["B9"], inputs["C9"], inputs["D9"] = "sales amount, control total", "KRW", total
    raw = wb.create_sheet("raw_sales")
    raw.append(HEADER)
    for r in rows:
        raw.append(r)
    for i in range(len(rows)):
        raw.cell(row=i + 2, column=3).number_format = "yyyy-mm-dd"
    tab = Table(displayName="tbl_raw_sales", ref=f"A1:E{len(rows) + 1}")
    tab.tableStyleInfo = TableStyleInfo(name=None, showRowStripes=False)
    raw.add_table(tab)
    h = wb.create_sheet("h_sales")
    h.append([c[0] for c in HELPER_COLUMNS])
    for r in range(2, capacity + 2):
        for c, f in enumerate(_helper_formulas(r), 1):
            h.cell(row=r, column=c, value=f)
    calc = wb.create_sheet("calc")
    calc["B1"], calc["B2"], calc["C2"] = "Calculation", "Master check", "=checks!$C$2"
    calc["B4"], calc["D4"] = "Region", "Amount"
    for i, reg in enumerate(REGIONS):
        calc.cell(row=5 + i, column=2, value=reg)
        calc.cell(row=5 + i, column=4, value=f"=SUMIFS(h_sales!$G:$G,h_sales!$F:$F,$B{5 + i})")
    calc["B9"], calc["D9"] = "Total", "=SUM(D5:D8)"
    if chart:
        from openpyxl.chart import BarChart, Reference
        ch = BarChart()
        ch.add_data(Reference(calc, min_col=4, min_row=4, max_row=8), titles_from_data=True)
        calc.add_chart(ch, "F4")
    ck = wb.create_sheet("checks")
    ck["B1"], ck["B2"] = "Checks", "Master check"
    for col, text in zip("BCDEFGHIJ", ["Id", "Check", "Actual", "Expected or minimum", "Maximum", "Difference",
                                        "Tolerance", "Result", "Checks"]):
        ck[f"{col}4"] = text
    checks = [
        ("sales_rows", "range", "Rows of raw_sales within the contract's range",
         {"D": "=COUNTA(raw_sales!$A:$A)-1", "E": "=inputs!$D$7", "F": "=inputs!$D$8", "I": "=--(OR(D{r}<E{r},D{r}>F{r}))"}),
        ("sales_nulls_line_no", "zero", "Empty line_no cells in raw_sales",
         {"D": "=COUNTA(raw_sales!$A:$A)-COUNTA(raw_sales!$B:$B)", "I": "=--(D{r}<>0)"}),
        ("sales_cover", "at_most", "Raw rows not covered by h_sales",
         {"D": "=COUNTA(raw_sales!$A:$A)-1-COUNT(h_sales!$A:$A)", "E": 0, "I": "=--(D{r}>E{r})"}),
        ("sales_keys", "zero", "Duplicate keys in raw_sales",
         {"D": "=SUM(h_sales!$I:$I)", "I": "=--(D{r}<>0)"}),
        ("amount_ties_raw", "equal", "Calculated total equals the raw amount total",
         {"D": "=calc!$D$9", "E": "=SUM(raw_sales!$E:$E)", "G": "=D{r}-E{r}", "H": "=inputs!$D$5",
          "I": "=--(ABS(G{r})>H{r})"}),
    ]
    if total is not None:
        checks.append(("sales_total_amount", "equal", "Raw amount total equals the control total",
                       {"D": "=SUM(raw_sales!$E:$E)", "E": "=inputs!$D$9", "G": "=D{r}-E{r}", "H": 0,
                        "I": "=--(ABS(G{r})>H{r})"}))
    m_checks = []
    for i, (cid, kind, label, cells) in enumerate(checks):
        r = 5 + i
        ck[f"B{r}"], ck[f"C{r}"] = cid, label
        for col, v in cells.items():
            ck[f"{col}{r}"] = v.format(r=r) if isinstance(v, str) else v
        m_checks.append({"id": cid, "kind": kind, "label": label, "result": f"checks!I{r}", "auto": True,
                         "targets": ["raw_sales"], "severity": "error"})
    last = 4 + len(checks)
    ck["C2"] = f"=--(OR(SUM(I5:I{last})>0,COUNT(I5:I{last})<>COUNTA(B5:B{last})))"
    for r in range(5, last + 1):
        ck[f"I{r}"].number_format = '[Red]"FAIL";[Red]"FAIL";"OK"'
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    man = {
        "rotm_manifest": 1, "plugin_version": common.VERSION, "built_at": common.now_iso(), "spec": "model.spec.json",
        "spec_sha256": None, "dictionary_sha256": None, "workbook": path.name,
        "settings": {"depth_limit": 2, "length_limit": 100, "allowed_literals": [0, 1], "product": "both"},
        "sheets": [
            {"name": "inputs", "type": "inputs", "font_size": 9},
            {"name": "raw_sales", "type": "raw", "font_size": 8, "table": "sales", "excel_table": "tbl_raw_sales",
             "landing": "static", "query_name": "q_sales", "columns": HEADER, "types": TYPES, "rows": len(rows),
             "digest": manifest.raw_digest(HEADER, rows, TYPES), "snapshot": "raw/sales.csv",
             # v3 (A-aera-1): the rows build wrote into the workbook, as build records them
             "workbook_digest": manifest.raw_digest(HEADER, rows, TYPES)},
            {"name": "h_sales", "type": "helpers", "font_size": 8, "table": "sales", "first_row": 2,
             "last_row": capacity + 1, "capacity": capacity,
             "columns": [{"id": cid, "col": col, "kind": kind, "formula": _helper_formulas(2)[i]}
                         for i, (cid, col, kind) in enumerate(HELPER_COLUMNS)]},
            {"name": "calc", "type": "calc", "font_size": 8,
             "blocks": [{"id": "by_region", "shape": "grid", "body": "D5:D8", "total": "D9",
                         "checks": ["amount_ties_raw"]}]},
            {"name": "checks", "type": "checks", "font_size": 9, "master": "C2", "results": f"I5:I{last}",
             "ids": f"B5:B{last}"}],
        "inputs": {"check_tolerance": "inputs!D5"},
        "checks": m_checks, "master_check": "checks!C2", "master_links": ["inputs!C2", "calc!C2"],
    }
    manifest.write_manifest(path, man)
    return man


_QT_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/queryTable"


def attach_query_table(wb: Path, query: str = "q_sales", display_name: str = "tbl_raw_sales") -> None:
    """Turn the table `display_name` of a saved workbook into a query table of connection 1 loaded by the query
    `query` (the parts Excel writes for a query loaded to a table), so the acceptance of refreshed rows can be
    tested without Excel (v4, A-aera-3: rows are accepted only from a live query table). Excel does not open such
    a file (measured 2026-11-25: no DataMashup part); tests that run Excel attach a real query with lib.excel."""
    import re
    import zipfile
    with zipfile.ZipFile(wb) as z:
        items = {n: z.read(n) for n in z.namelist()}
    table = next(n for n, b in items.items() if re.match(r"xl/tables/table\d+\.xml$", n)
                 and f'displayName="{display_name}"'.encode() in b)
    if b'tableType="queryTable"' not in items[table]:
        items[table] = re.sub(rb"<table\b", b'<table tableType="queryTable"', items[table], count=1)
    folder, name = table.rsplit("/", 1)
    items[f"{folder}/_rels/{name}.rels"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.'
        f'org/package/2006/relationships"><Relationship Id="rId1" Type="{_QT_REL}" '
        'Target="../queryTables/queryTable1.xml"/></Relationships>').encode()
    items["xl/queryTables/queryTable1.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><queryTable xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main" name="ExternalData_1" connectionId="1" autoFormatId="16"/>').encode()
    items["xl/connections.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><connections xmlns="http://schemas.openxmlformats.org/'
        f'spreadsheetml/2006/main"><connection id="1" keepAlive="1" name="Query - {query}" type="5" '
        'refreshedVersion="8" saveData="1"><dbPr connection="Provider=Microsoft.Mashup.OleDb.1;Data Source=$Workbook$;'
        f'Location={query};Extended Properties=&quot;&quot;" command="SELECT * FROM [{query}]"/></connection>'
        '</connections>').encode()
    tmp = wb.with_name(wb.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in items.items():
            z.writestr(n, b)
    tmp.replace(wb)


def make_model(tmp_path: Path, n: int = 10, *, capacity: int | None = None, rows_range: str = "1:100000",
               total: float | None = None, agree: bool = True, chart: bool = False,
               landing: str = "static", query_table: bool = False) -> SimpleNamespace:
    """A complete model folder: marker, agreed contract, raw snapshot, workbook and manifest. `query_table` (with
    landing power_query): raw_sales is a query table of the query q_sales, recorded as land --power-query records
    it (query_name, query_contract_sha256), without Excel (attach_query_table)."""
    root = tmp_path / "model"
    modelfolder.init_model(root, name="m5_test")
    source = write_source_csv(tmp_path / "source" / "sales.csv", n)
    totals = [f"amount={total}"] if total is not None else None
    contract.propose(root, "sales", csv=source, key="invoice_id,line_no", grain="one row per invoice line",
                     rows=rows_range, units=["amount=KRW", "line_no=count"], totals=totals)
    task = tmp_path / "task.md"
    task.write_text(f"# Task\n\nContract reply from the user: {REPLY}\n", encoding="utf-8")
    if agree:
        contract.agree(root, "sales", reply=REPLY, by="Max", channel="task-file", source_file=task)
    shutil.copy2(source, root / "raw" / "sales.csv")
    lo, hi = (int(x) for x in rows_range.split(":"))
    wb = root / "model.xlsx"
    write_workbook(wb, sales_values(n), capacity=capacity or n, rows_min=lo, rows_max=hi, total=total, chart=chart)
    if agree:
        # v3 (A-aera-4): the agreed contract the workbook was built from, as build records it
        man = manifest.load_manifest(wb)
        agreed = common.read_json(modelfolder.contract_path(root, "sales"))["agreement"]["contract_sha256"]
        manifest.raw_sheet(man, "sales")["contract_sha256"] = agreed
        manifest.write_manifest(wb, man)
    if landing != "static":
        set_landing(wb, landing)
    if query_table:
        attach_query_table(wb, "q_sales")
        man = manifest.load_manifest(wb)
        entry = manifest.raw_sheet(man, "sales")
        entry["landing"], entry["query_name"] = "power_query", "q_sales"
        if agree:
            entry["query_contract_sha256"] = entry.get("contract_sha256")
        manifest.write_manifest(wb, man)
    return SimpleNamespace(root=root, wb=wb, source=source, task=task, n=n)


def set_landing(wb: Path, landing: str) -> None:
    man = manifest.load_manifest(wb)
    manifest.raw_sheet(man, "sales")["landing"] = landing
    manifest.write_manifest(wb, man)


def rewrite_raw_sheet(wb: Path, rows: list[list]) -> None:
    """Replace the raw sheet's rows with openpyxl (stands in for a refresh in tests without Excel). A query table
    made by attach_query_table stays a query table of the same query (openpyxl drops its parts on save)."""
    import openpyxl
    query = check.query_tables(wb).get("raw_sales", False)
    book = openpyxl.load_workbook(wb)
    ws = book["raw_sales"]
    ws.delete_rows(2, ws.max_row)
    for r in rows:
        ws.append(r)
    for i in range(len(rows)):
        ws.cell(row=i + 2, column=3).number_format = "yyyy-mm-dd"
    ws.tables["tbl_raw_sales"].ref = f"A1:E{len(rows) + 1}"
    book.save(wb)
    if query is not False:
        attach_query_table(wb, query or "q_sales")


def rules(report) -> list[str]:
    return [f.rule for f in report.findings]


def find(report, rule: str):
    hits = [f for f in report.findings if f.rule == rule]
    assert hits, f"no {rule} finding in {[f.line() for f in report.findings]}"
    return hits[0]


def run_cli(*args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(ROTM), *args], capture_output=True,
                          text=True, encoding="utf-8", env=env)


@pytest.fixture
def no_excel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")


# ----------------------------------------------------------------------------- check_raw

def raw_rows(n: int = 10) -> check.SheetRows:
    return check.SheetRows("sales", list(HEADER), sales_values(n))


@pytest.fixture
def sales_contract(tmp_path: Path) -> dict:
    m = make_model(tmp_path, 10, total=sum(100 + 7.5 * i for i in range(10)))
    return common.read_json(modelfolder.contract_path(m.root, "sales"))


def test_check_raw_passes_clean_rows(sales_contract: dict) -> None:
    assert check.check_raw(raw_rows(), sales_contract) == []


def test_check_raw_accepts_csv_text_rows(sales_contract: dict, tmp_path: Path) -> None:
    p = write_source_csv(tmp_path / "x.csv", 10)
    assert check.check_raw(check.read_snapshot_rows(p, "sales"), sales_contract) == []


def test_check_raw_rows_out_of_range(sales_contract: dict) -> None:
    c = dict(sales_contract, expect={"rows": {"min": 12, "max": 20}})
    f = check.check_raw(raw_rows(), c)
    assert [x.rule for x in f] == ["D-ROWS"]
    assert "has 10 data rows; the contract of 'sales' expects from 12 to 20" in f[0].message
    c = dict(sales_contract, expect={"rows": {"exact": 9}})
    assert "expects exactly 9" in check.check_raw(raw_rows(), c)[0].message


def test_check_raw_empty_result(sales_contract: dict) -> None:
    c = dict(sales_contract, expect={"rows": {"min": 0, "max": 5}})
    f = check.check_raw(check.SheetRows("sales", list(HEADER), []), c)
    assert [x.rule for x in f] == ["D-ROWS"] and "does not allow an empty result" in f[0].message


def test_check_raw_nulls_types_allowed_keys(sales_contract: dict) -> None:
    rows = sales_values(10)
    rows[2][1] = None                 # a null in a never-null column
    rows[3][2] = "31/01/2026"         # not an ISO date
    rows[4][4] = "12,5"               # a decimal comma
    rows[5][3] = "Central"            # not an allowed region
    rows[7][:2] = rows[6][:2]         # a duplicate key (invoice_id, line_no)
    f = check.check_raw(check.SheetRows("sales", list(HEADER), rows), sales_contract)
    by = {x.rule: x for x in f}
    assert set(by) == {"D-NULL", "D-TYPE", "D-ALLOWED", "D-KEY", "D-TOTAL"}
    assert by["D-NULL"].cell == "B4" and "line_no" in by["D-NULL"].message
    types = [x for x in f if x.rule == "D-TYPE"]
    assert {x.data["column"] for x in types} == {"inv_date", "amount"}
    assert by["D-ALLOWED"].cell == "D7" and "'Central'" in by["D-ALLOWED"].message
    assert by["D-KEY"].cell == "A9" and "1 rows of raw_sales repeat an earlier key" in by["D-KEY"].message
    assert all(x.fix for x in f)


def test_check_raw_header_differs(sales_contract: dict) -> None:
    header = ["invoice_id", "line_no", "region", "inv_date", "amount"]
    f = check.check_raw(check.SheetRows("sales", header, [[r[0], r[1], r[3], r[2], r[4]] for r in sales_values(10)]),
                        sales_contract)
    hdr = [x for x in f if x.rule == "R-CONTRACT"]
    assert hdr and "same names in another order" in hdr[0].message and hdr[0].cell == "A1"


def test_check_raw_control_totals(sales_contract: dict) -> None:
    total = sum(100 + 7.5 * i for i in range(10))
    assert check.check_raw(raw_rows(), sales_contract) == []
    f = check.check_raw(raw_rows(), sales_contract, controls={"amount": total + 5})
    assert [x.rule for x in f] == ["D-TOTAL"] and "--control" in f[0].message
    f = check.check_raw(raw_rows(), sales_contract, controls={"amount": (total + 5, 10)})
    assert f == []                    # within the given tolerance


def test_check_raw_accepts_land_rawtable_shape(sales_contract: dict) -> None:
    from lib import land
    t = land.RawTable(table="sales", columns=list(HEADER), types=list(TYPES),
                      rows=[tuple(r) for r in sales_values(10)])
    assert check.check_raw(t, sales_contract) == []


# ----------------------------------------------------------------------------- coverage

def test_coverage_holds(tmp_path: Path) -> None:
    m = make_model(tmp_path, 10)
    assert check.coverage(m.wb, manifest.load_manifest(m.wb)) == []


def test_coverage_names_uncovered_rows_and_the_exact_fix(tmp_path: Path) -> None:
    m = make_model(tmp_path, 10)
    rewrite_raw_sheet(m.wb, sales_values(16))
    f = check.coverage(m.wb, manifest.load_manifest(m.wb))
    assert [x.rule for x in f] == ["H-COVERAGE"]
    x = f[0]
    assert x.message == ("raw_sales has 16 data rows (rows 2 to 17); h_sales covers rows 2 to 11; "
                         "6 raw rows (12 to 17) are not covered")
    assert f"extend \"{m.root.as_posix()}\" --table sales" in x.fix
    assert "(or copy h_sales row 11, A:I, down to row 17)" in x.fix
    assert x.sheet == "h_sales" and x.cell == "A12"


def test_coverage_helper_rows_below_the_raw_rows_are_an_error(tmp_path: Path) -> None:
    # v2, A-aera-6: helper rows below the last raw row read empty raw cells as 0 and feed the calculations
    m = make_model(tmp_path, 10, capacity=15)
    f = check.coverage(m.wb, manifest.load_manifest(m.wb))
    assert [(x.rule, x.severity) for x in f] == [("H-BEYOND", "error")]
    assert "h_sales rows 12 to 16 lie below the last raw row of raw_sales (row 11)" in f[0].message
    assert f"extend \"{m.root.as_posix()}\" --table sales" in f[0].fix and f[0].cell == "A12"
    assert f[0].data["first_beyond"] == 12 and f[0].data["last_beyond"] == 16


def test_coverage_names_a_short_helper_column(tmp_path: Path) -> None:
    import openpyxl
    m = make_model(tmp_path, 10)
    book = openpyxl.load_workbook(m.wb)
    book["h_sales"]["H9"] = None             # one helper column stops early
    book.save(m.wb)
    f = check.coverage(m.wb, manifest.load_manifest(m.wb))
    assert f[0].rule == "H-COVERAGE"
    assert "column H (month_end) ends at row 8 while other helper columns reach row 11" in f[0].message
    assert "copy h_sales row 8, A:I, down to row 11" in f[0].fix


# ----------------------------------------------------------------------------- check_model without Excel

def test_check_without_excel_says_not_recalculated(tmp_path: Path, no_excel) -> None:
    m = make_model(tmp_path, 10)
    rep = check.check_model(m.root)
    assert rep.exit_code == common.EXIT_NOT_AVAILABLE and rep.status == "not_verified"
    assert rep.counts()["errors"] == 0
    assert any("audited but not recalculated" in n and "ROTM_NO_EXCEL=1" in n for n in rep.notes)
    assert rep.verified["recalculated"] is False
    st = modelfolder.read_status(m.root)["workbooks"]["model.xlsx"]["check"]
    assert st["recalculated"] is False and st["exit_code"] == 3 and st["failed"] is None and st["master"] is None
    assert (m.root / "reports" / "check.json").is_file()
    rep2 = check.check_model(m.wb, excel=False)          # a workbook inside the model folder works too
    assert rep2.exit_code == 3 and any("--no-excel was given" in n for n in rep2.notes)


def test_check_contract_not_agreed(tmp_path: Path, no_excel) -> None:
    m = make_model(tmp_path, 10, agree=False)
    rep = check.check_model(m.root)
    x = find(rep, "CT-STATE")
    assert "proposed, not agreed" in x.message and "contract agree" in x.fix
    assert rep.exit_code == 1


def test_check_static_raw_sheet_changed_is_never_accepted(tmp_path: Path, no_excel) -> None:
    m = make_model(tmp_path, 10)
    rows = sales_values(10)
    rows[0][4] = 999.0                          # a hand edit on a static raw sheet
    rewrite_raw_sheet(m.wb, rows)
    rep = check.check_model(m.root, accept_refresh=True)
    x = find(rep, "R-CHANGED")
    assert "static table" in x.message and "land" in x.fix
    assert rep.exit_code == 1
    assert manifest.raw_sheet(manifest.load_manifest(m.wb), "sales")["digest"] != \
        manifest.raw_digest(HEADER, rows, TYPES)


def test_check_refreshed_rows_need_acceptance_then_are_recorded(tmp_path: Path, no_excel) -> None:
    m = make_model(tmp_path, 10, capacity=14, landing="power_query",        # v2: no helper row below the raw rows
                   query_table=True)
    new_rows = sales_values(14)
    rewrite_raw_sheet(m.wb, new_rows)
    rep = check.check_model(m.root)
    x = find(rep, "R-CHANGED")
    assert "10 rows recorded, 14 rows now" in x.message and "--accept-refresh" in x.fix
    assert rep.exit_code == 1
    rep = check.check_model(m.root, accept_refresh=True)
    assert "R-CHANGED" not in rules(rep) and find(rep, "R-ACCEPTED").severity == "info"
    entry = manifest.raw_sheet(manifest.load_manifest(m.wb), "sales")
    assert entry["rows"] == 14 and entry["digest"] == manifest.raw_digest(HEADER, new_rows, TYPES)
    assert manifest.csv_digest(m.root / "raw" / "sales.csv", TYPES)[0] == entry["digest"]
    assert rep.exit_code == 3                    # nothing failed, but not recalculated
    rep = check.check_model(m.root)
    assert rep.counts()["errors"] == 0 and any("unchanged since landing" in n for n in rep.notes)


def test_check_refresh_breaking_the_contract_is_not_accepted(tmp_path: Path, no_excel) -> None:
    m = make_model(tmp_path, 10, capacity=20, landing="power_query", query_table=True)
    rows = sales_values(12)
    rows[11][:2] = rows[10][:2]                  # a duplicate key in the refreshed rows
    rewrite_raw_sheet(m.wb, rows)
    rep = check.check_model(m.root, accept_refresh=True)
    assert "D-KEY" in rules(rep)
    assert "not accepted: the new rows break the contract" in find(rep, "R-CHANGED").message
    assert manifest.raw_sheet(manifest.load_manifest(m.wb), "sales")["rows"] == 10


def test_check_control_total_after_refresh(tmp_path: Path, no_excel) -> None:
    total10 = sum(100 + 7.5 * i for i in range(10))
    total12 = sum(100 + 7.5 * i for i in range(12))
    m = make_model(tmp_path, 10, capacity=20, total=total10, landing="power_query", query_table=True)
    rewrite_raw_sheet(m.wb, sales_values(12))
    rep = check.check_model(m.root, accept_refresh=True)
    assert "D-TOTAL" in rules(rep) and "R-CHANGED" in rules(rep)       # the contract's old total no longer holds
    rep = check.check_model(m.root, accept_refresh=True, controls={"sales.amount": total12})
    assert "D-TOTAL" not in rules(rep) and "R-ACCEPTED" in rules(rep)
    w = find(rep, "D-CONTROL-INPUT")                  # no Excel: the workbook's own control cell is not changed
    # v3 (C-sol-1): the fix no longer says to type the total into the cell; the cell is not a control source
    assert w.severity == "warning" and "inputs!D9 holds 1,337.5" in w.message and "1,695 (--control)" in w.message
    assert "--accept-refresh --control sales.amount=<source total>" in w.fix and "type" not in w.fix


@pytest.mark.excel
def test_accepted_control_total_is_written_into_the_inputs_sheet(tmp_path: Path) -> None:
    import openpyxl
    total10 = sum(100 + 7.5 * i for i in range(10))
    total12 = sum(100 + 7.5 * i for i in range(12))
    from lib import excel
    m = make_model(tmp_path, 10, capacity=12, total=total10)
    # v4 (A-aera-3): rows are accepted only from a live query table, so a real query over the source CSV loads the
    # raw sheet (recorded as land --power-query records it) and the source gets 2 more rows
    res = excel.pq_attach(m.wb, sheet="raw_sales", table_name="tbl_raw_sales", query_name="q_sales",
                          m_text=_m_text(m.source))
    assert res.ok, res.errors
    man = manifest.load_manifest(m.wb)
    entry = manifest.raw_sheet(man, "sales")
    entry["landing"], entry["query_name"] = "power_query", "q_sales"
    entry["query_contract_sha256"] = entry["contract_sha256"]
    manifest.write_manifest(m.wb, man)
    write_source_csv(m.source, 12)
    assert check.check_model(m.root, refresh=True).data["refreshed"] is True
    rep = check.check_model(m.root, accept_refresh=True)
    assert rep.exit_code == 1 and "D-TOTAL" in rules(rep)
    assert "sales_total_amount" in {f.data.get("check") for f in rep.findings if f.rule == "C-FAIL"}
    rep = check.check_model(m.root, accept_refresh=True, controls={"sales.amount": total12})
    assert rep.exit_code == 0, [f.line() for f in rep.findings] + rep.notes
    x = find(rep, "D-CONTROL-SET")
    assert "inputs!D9 set to 1,695" in x.message and "it held 1,337.5" in x.message
    assert "D-CONTROL-INPUT" not in rules(rep)
    assert openpyxl.load_workbook(m.wb)["inputs"]["D9"].value == total12
    assert rep.data["master"]["value"] == 0
    st = modelfolder.read_status(m.root)["workbooks"]["model.xlsx"]["check"]
    assert st["output_sha256"] == common.file_sha256(m.wb) and st["master"] == 0


def test_check_snapshot_file_changed(tmp_path: Path, no_excel) -> None:
    m = make_model(tmp_path, 10)
    snap = m.root / "raw" / "sales.csv"
    snap.write_text(snap.read_text(encoding="utf-8").replace("East", "Eest", 1), encoding="utf-8")
    rep = check.check_model(m.root)
    assert "changed after it was landed" in find(rep, "R-SNAPSHOT").message
    snap.unlink()
    rep = check.check_model(m.root)
    assert find(rep, "R-SNAPSHOT").severity == "warning"


def test_check_uncovered_rows_without_excel(tmp_path: Path, no_excel) -> None:
    m = make_model(tmp_path, 10, landing="power_query")
    rewrite_raw_sheet(m.wb, sales_values(13))
    rep = check.check_model(m.root, accept_refresh=True)
    x = find(rep, "H-COVERAGE")
    assert "3 raw rows (12 to 14) are not covered" in x.message
    assert rep.data["tables"]["sales"]["coverage"]["uncovered"] == 3


@pytest.mark.parametrize("controls,text", [
    ({"sales.amout": 1}, "did you mean 'amount'"),
    ({"sale.amount": 1}, "no raw table 'sale'"),
    ({"sales.region": 1}, "needs a number column"),
    ({"amount": 1}, "TABLE.COLUMN=VALUE"),
])
def test_check_control_refusals(tmp_path: Path, no_excel, controls: dict, text: str) -> None:
    m = make_model(tmp_path, 10)
    with pytest.raises(common.Refusal) as exc:
        check.check_model(m.root, controls=controls)
    assert text in exc.value.line()


def test_parse_controls() -> None:
    assert check.parse_controls(["sales.amount=1,234.5"]) == {"sales.amount": 1234.5}
    with pytest.raises(common.Refusal):
        check.parse_controls(["sales.amount"])
    with pytest.raises(common.Refusal):
        check.parse_controls(["sales.amount=abc"])


def test_check_refuses_outside_a_model(tmp_path: Path, no_excel) -> None:
    with pytest.raises(common.Refusal, match="not a model folder"):
        check.check_model(tmp_path)
    loose = tmp_path / "loose.xlsx"
    import openpyxl
    openpyxl.Workbook().save(loose)
    with pytest.raises(common.Refusal, match="not inside a model folder"):
        check.check_model(loose)
    root = tmp_path / "m"
    modelfolder.init_model(root)
    with pytest.raises(common.Refusal, match="not built yet"):
        check.check_model(root)
    openpyxl.Workbook().save(root / "model.xlsx")
    with pytest.raises(common.Refusal, match="has no manifest"):
        check.check_model(root)


def test_check_cached_values_are_reported_as_not_verified(tmp_path: Path, no_excel) -> None:
    m = make_model(tmp_path, 10)
    rep = check.check_model(m.root)
    assert any("holds no cached values" in n for n in rep.notes)
    assert "C-FAIL" not in rules(rep)


# ----------------------------------------------------------------------------- extend without Excel

def test_extend_with_openpyxl_covers_the_new_rows(tmp_path: Path, no_excel) -> None:
    import openpyxl
    m = make_model(tmp_path, 10, landing="power_query")
    rewrite_raw_sheet(m.wb, sales_values(16))
    rep = check.extend_helpers(m.root)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    x = find(rep, "H-EXTENDED")
    assert "copied row 11 (A:I) down to row 17: 6 new helper rows with openpyxl" in x.message
    ws = openpyxl.load_workbook(m.wb)["h_sales"]
    for r in (12, 17):
        assert [ws.cell(row=r, column=c).value for c in range(1, 10)] == _helper_formulas(r)
    assert ws.cell(row=18, column=1).value is None
    entry = manifest.helper_sheet(manifest.load_manifest(m.wb), "sales")
    assert entry["last_row"] == 17 and entry["capacity"] == 16
    assert check.coverage(m.wb, manifest.load_manifest(m.wb)) == []
    backups = list((m.root / "reports" / "backups").glob("model.bak_*.xlsx"))
    assert len(backups) == 1
    st = modelfolder.read_status(m.root)["workbooks"]["model.xlsx"]["extend"]
    assert st["tables"]["sales"]["to_row"] == 17 and st["recalculated"] is False
    again = check.extend_helpers(m.root)
    assert again.exit_code == 0 and any("nothing to extend" in n for n in again.notes)


def test_extend_to_a_given_row_and_refusals(tmp_path: Path, no_excel) -> None:
    m = make_model(tmp_path, 10)
    rep = check.extend_helpers(m.root, table="sales", rows=30)
    assert rep.exit_code == 0
    assert manifest.helper_sheet(manifest.load_manifest(m.wb), "sales")["last_row"] == 30
    with pytest.raises(common.Refusal, match="did you mean 'sales'"):
        check.extend_helpers(m.root, table="sale")
    with pytest.raises(common.Refusal, match="--rows 1"):
        check.extend_helpers(m.root, rows=1)


def test_extend_refuses_fragile_parts_without_excel(tmp_path: Path, no_excel) -> None:
    m = make_model(tmp_path, 10, chart=True)
    rewrite_raw_sheet(m.wb, sales_values(12))
    before = common.file_sha256(m.wb)
    with pytest.raises(common.EnvUnavailable) as exc:
        check.extend_helpers(m.root)
    assert "parts openpyxl would drop" in exc.value.message and "charts" in exc.value.message
    assert "nothing was written" in exc.value.message
    assert common.file_sha256(m.wb) == before


# ----------------------------------------------------------------------------- command line

def test_cli_check_json_and_exit_code(tmp_path: Path) -> None:
    m = make_model(tmp_path, 10)
    r = run_cli("check", str(m.root), "--json", "-", env_extra={"ROTM_NO_EXCEL": "1"})
    assert r.returncode == 3, r.stderr
    payload = json.loads(r.stdout)
    assert payload["command"] == "check" and payload["status"] == "not_verified"
    assert payload["verified"]["recalculated"] is False
    assert "errors: 0" in r.stderr and "Traceback" not in r.stderr


def test_cli_check_refusal_and_extend(tmp_path: Path) -> None:
    r = run_cli("check", str(tmp_path), env_extra={"ROTM_NO_EXCEL": "1"})
    assert r.returncode == 2 and "Fix:" in r.stderr
    m = make_model(tmp_path, 10)
    rewrite_raw_sheet(m.wb, sales_values(12))
    r = run_cli("check", str(m.root), "--no-excel", env_extra={"ROTM_NO_EXCEL": "1"})
    assert r.returncode == 1 and "H-COVERAGE" in r.stdout and "extend" in r.stdout
    r = run_cli("extend", str(m.root), "--table", "sales", env_extra={"ROTM_NO_EXCEL": "1"})
    assert r.returncode == 0, r.stderr
    assert "copied row 11 (A:I) down to row 13" in r.stdout


# ----------------------------------------------------------------------------- with Excel

def _excel_log_since():
    return common.now_iso()


def _no_orphans(since: str) -> None:
    from lib import excel
    assert excel.orphan_pids(excel.read_job_log(since=since)) == []


@pytest.mark.excel
def test_check_with_excel_passes_and_records_status(tmp_path: Path) -> None:
    since = _excel_log_since()
    m = make_model(tmp_path, 10)
    rep = check.check_model(m.root)
    assert rep.exit_code == 0, [f.line() for f in rep.findings] + rep.notes
    assert rep.verified["recalculated"] is True
    assert rep.data["master"]["value"] == 0 and len(rep.data["checks"]) == 5
    assert all(c["state"] == "pass" for c in rep.data["checks"])
    assert any(n.startswith("check sales_rows OK (checks!I5)") and "actual 10" in n for n in rep.notes)
    st = modelfolder.read_status(m.root)["workbooks"]["model.xlsx"]["check"]
    assert st["recalculated"] is True and st["failed"] == 0 and st["master"] == 0
    assert st["output_sha256"] == common.file_sha256(m.wb) != st["input_sha256"]
    _no_orphans(since)


@pytest.mark.excel
def test_check_with_excel_reports_failing_checks(tmp_path: Path) -> None:
    m = make_model(tmp_path, 10, rows_range="1:8")         # the contract's range is too small for 10 rows
    rep = check.check_model(m.root)
    assert rep.exit_code == 1
    fails = [f for f in rep.findings if f.rule == "C-FAIL"]
    assert {f.data["check"] for f in fails} == {"sales_rows", "master"}
    rows_fail = next(f for f in fails if f.data["check"] == "sales_rows")
    assert rows_fail.cell == "I5" and "actual 10" in rows_fail.message and "range 1 to 8" in rows_fail.message
    assert "D-ROWS" in rules(rep)                           # the same fact seen by code from the raw rows
    st = modelfolder.read_status(m.root)["workbooks"]["model.xlsx"]["check"]
    assert st["failed"] == 1 and st["master"] == 1


@pytest.mark.excel
def test_extend_with_excel_without_fragile_parts(tmp_path: Path) -> None:
    m = make_model(tmp_path, 10, landing="power_query")
    rewrite_raw_sheet(m.wb, sales_values(13))
    rep = check.extend_helpers(m.root)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    assert "through Excel" in find(rep, "H-EXTENDED").message and rep.verified["recalculated"] is True
    # v4 (A-aera-3): the manifest says power_query, but the workbook holds no query table at A1 of raw_sales, so
    # the changed rows are not accepted as a refresh
    rep = check.check_model(m.root, accept_refresh=True)
    assert rep.exit_code == 1 and "R-ACCEPTED" not in rules(rep), [f.line() for f in rep.findings]
    assert "holds no query table at A1 of raw_sales" in find(rep, "R-CHANGED").message
    assert "H-COVERAGE" not in rules(rep)


def _m_text(csv_path: Path) -> str:
    """Power Query M over a local CSV with the contract's types (design 7.3)."""
    try:
        from lib import land
        c = {"table": "sales", "columns": [{"name": n, "type": t} for n, t in zip(HEADER, TYPES)]}
        return land.m_text(c, csv_path=csv_path)
    except Exception:  # noqa: BLE001 - land is written in parallel; this text follows design 7.3
        p = str(csv_path).replace('"', '""')
        types = ", ".join(f'{{"{n}", {t}}}' for n, t in zip(HEADER, ["type text", "Int64.Type", "type date",
                                                                       "type text", "type number"]))
        return ("let\n"
                f'    Source = Csv.Document(File.Contents("{p}"), [Delimiter=",", Encoding=65001, '
                "QuoteStyle=QuoteStyle.Csv]),\n"
                "    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),\n"
                f'    Typed = Table.TransformColumnTypes(Promoted, {{{types}}}, "en-US")\n'
                "in\n"
                "    Typed")


@pytest.mark.excel
def test_f27_power_query_table_grows_and_shrinks(tmp_path: Path) -> None:
    """F27: the raw sheet is a Power Query table over a local CSV; the CSV gets more rows and then fewer rows and
    the query is refreshed each time. check names the uncovered rows and the fix; extend (the documented fix)
    restores coverage; after each refresh every check holds and no formula is broken."""
    from lib import excel
    since = _excel_log_since()
    m = make_model(tmp_path, 10)
    res = excel.pq_attach(m.wb, sheet="raw_sales", table_name="tbl_raw_sales", query_name="q_sales",
                          m_text=_m_text(m.source))
    assert res.ok, res.errors
    set_landing(m.wb, "power_query")
    from lib import workbook as wbmod
    assert wbmod.package_parts(m.wb).has_power_query
    header, rows = manifest.sheet_rows(m.wb, "raw_sales")
    recorded = manifest.raw_sheet(manifest.load_manifest(m.wb), "sales")["digest"]
    assert manifest.raw_digest([str(h) for h in header], rows, TYPES) == recorded   # same normalisation as the CSV

    rep = check.check_model(m.root)
    assert rep.exit_code == 0, [f.line() for f in rep.findings] + rep.notes

    # More rows: 10 -> 16. The query table grows to row 17; h_sales still ends at row 11.
    write_source_csv(m.source, 16)
    rep = check.check_model(m.root, refresh=True)
    assert rep.exit_code == 1 and rep.data["refreshed"] is True
    cov = find(rep, "H-COVERAGE")
    assert "6 raw rows (12 to 17) are not covered" in cov.message
    assert "extend" in cov.fix and "copy h_sales row 11, A:I, down to row 17" in cov.fix
    assert "R-CHANGED" in rules(rep)
    assert {f.data.get("check") for f in rep.findings if f.rule == "C-FAIL"} >= {"sales_cover", "master"}
    assert rep.data["error_cells"] == []

    rep = check.extend_helpers(m.root)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    assert "through Excel" in find(rep, "H-EXTENDED").message
    assert wbmod.package_parts(m.wb).has_power_query                 # the query is still there

    rep = check.check_model(m.root, accept_refresh=True)
    assert rep.exit_code == 0, [f.line() for f in rep.findings] + rep.notes
    assert "R-ACCEPTED" in rules(rep) and rep.data["master"]["value"] == 0
    assert manifest.raw_sheet(manifest.load_manifest(m.wb), "sales")["rows"] == 16

    # Fewer rows: 16 -> 6. v2 (A-aera-6): the helper rows below the last raw row are an error until extend
    # clears them; then every check holds.
    write_source_csv(m.source, 6)
    rep = check.check_model(m.root, refresh=True, accept_refresh=True)
    assert rep.exit_code == 1, [f.line() for f in rep.findings] + rep.notes
    beyond = find(rep, "H-BEYOND")
    assert beyond.severity == "error" and "h_sales rows 8 to 17" in beyond.message and "extend" in beyond.fix
    assert "R-ACCEPTED" in rules(rep) and rep.data["error_cells"] == []
    assert manifest.raw_sheet(manifest.load_manifest(m.wb), "sales")["rows"] == 6
    rep = check.extend_helpers(m.root)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    assert "cleared rows 8 to 17" in find(rep, "H-CLEARED").message
    assert manifest.helper_sheet(manifest.load_manifest(m.wb), "sales")["last_row"] == 7
    rep = check.check_model(m.root)
    assert rep.exit_code == 0, [f.line() for f in rep.findings] + rep.notes
    assert rep.data["master"]["value"] == 0 and all(c["state"] == "pass" for c in rep.data["checks"])
    _no_orphans(since)


def test_static_sheet_older_than_a_later_landing_says_rebuild(tmp_path: Path, no_excel) -> None:
    m = make_model(tmp_path, 10)
    snap = write_source_csv(m.root / "raw" / "sales.csv", 12)     # stands in for land: new snapshot and digest
    manifest.update_raw(m.wb, "sales", digest=manifest.csv_digest(snap, TYPES)[0], rows=12)
    rep = check.check_model(m.root)
    x = find(rep, "R-CHANGED")
    assert "raw/sales.csv holds the rows landed after the workbook was built" in x.message
    assert "rebuild the workbook from the landed snapshot raw/sales.csv" in x.fix and "--force" in x.fix
    assert "R-SNAPSHOT" not in rules(rep)


# ----------------------------------------------------------------------------- with the real land and build

REAL_SPEC = {"rotm_spec": 1, "name": "m5_real", "title": "Sales by region", "sheets": [
    {"name": "raw_sales", "type": "raw", "table": "sales"},
    {"name": "h_sales", "type": "helpers", "table": "sales",
     "columns": [{"id": "month_end", "expr": "EOMONTH({this:inv_date},0)", "format": "date"}]},
    {"name": "calc", "type": "calc", "blocks": [
        {"id": "by_region", "title": "Amount by region", "unit": "KRW", "format": "amount",
         "rows": {"values": REGIONS}, "cell": "SUMIFS({h:sales.amount},{h:sales.region},{row})", "total": {"row": True}}]},
    {"name": "checks", "type": "checks", "checks": [
        {"id": "region_ties_raw", "kind": "equal", "label": "Region total equals the raw total",
         "actual": "{blk:by_region.total}", "expected": "SUM({raw:sales.amount})"}]}]}


def real_model(tmp_path: Path, n: int) -> SimpleNamespace:
    """A model made by the plugin's own land and build (skipped while either is not written)."""
    from lib import build, land
    root = tmp_path / "real"
    modelfolder.init_model(root, name="m5_real")
    src = write_source_csv(tmp_path / "src" / "sales.csv", n)
    contract.propose(root, "sales", csv=src, key="invoice_id,line_no", grain="one row per invoice line",
                     rows="1:100000", units=["amount=KRW", "line_no=count"],
                     totals=[f"amount={sum(100 + 7.5 * i for i in range(n))}"])
    task = tmp_path / "task.md"
    task.write_text(f"Contract reply: {REPLY}\n", encoding="utf-8")
    contract.agree(root, "sales", reply=REPLY, by="Max", channel="task-file", source_file=task)
    try:
        rep = land.land_csv(root, "sales", src)
    except NotImplementedError:
        pytest.skip("lib.land is not written yet")
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    common.write_json(root / "model.spec.json", REAL_SPEC)
    try:
        rep = build.build(root / "model.spec.json", strict=False, power_query=False)
    except NotImplementedError:
        pytest.skip("lib.build is not written yet")
    assert rep.counts()["errors"] == 0, [f.line() for f in rep.findings]
    return SimpleNamespace(root=root, wb=root / "model.xlsx", source=src, n=n)


def test_real_build_check_and_extend_without_excel(tmp_path: Path, no_excel) -> None:
    m = real_model(tmp_path, 10)
    rep = check.check_model(m.root)
    assert rep.exit_code == 3 and rep.counts()["errors"] == 0, [f.line() for f in rep.findings]
    # v4 (A-aera-3): rows are accepted only from a live query table (attach_query_table stands in for the query)
    attach_query_table(m.wb, "q_sales")
    man = manifest.load_manifest(m.wb)
    entry = manifest.raw_sheet(man, "sales")
    entry["landing"], entry["query_name"] = "power_query", "q_sales"
    entry["query_contract_sha256"] = entry["contract_sha256"]
    manifest.write_manifest(m.wb, man)
    rewrite_raw_sheet(m.wb, sales_values(13))
    rep = check.check_model(m.root, accept_refresh=True, controls={"sales.amount": sum(100 + 7.5 * i for i in range(13))})
    assert "3 raw rows (12 to 14) are not covered" in find(rep, "H-COVERAGE").message
    assert "R-ACCEPTED" in rules(rep)
    import openpyxl
    openpyxl.load_workbook(m.wb).save(m.wb)          # drops the query parts, so extend runs with openpyxl
    rep = check.extend_helpers(m.root)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    assert check.coverage(m.wb, manifest.load_manifest(m.wb)) == []


@pytest.mark.excel
def test_real_build_power_query_refresh_cycle(tmp_path: Path) -> None:
    """F27 on a model made by land and build: Power Query table over a local CSV, more rows, extend, accept with
    the new source total, fewer rows."""
    from lib import excel, land
    since = _excel_log_since()
    m = real_model(tmp_path, 10)
    rep = check.check_model(m.root)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    c = common.read_json(modelfolder.contract_path(m.root, "sales"))
    res = excel.pq_attach(m.wb, sheet="raw_sales", table_name="tbl_raw_sales", query_name="q_sales",
                          m_text=land.m_text(c, csv_path=m.source))
    assert res.ok, res.errors
    man = manifest.load_manifest(m.wb)
    entry = manifest.raw_sheet(man, "sales")
    entry["landing"], entry["query_name"] = "power_query", "q_sales"
    manifest.write_manifest(m.wb, man)
    write_source_csv(m.source, 14)
    rep = check.check_model(m.root, refresh=True)
    assert rep.exit_code == 1
    assert "4 raw rows (12 to 15) are not covered" in find(rep, "H-COVERAGE").message
    assert "D-TOTAL" in rules(rep)                     # the contract's control total is for the 10 landed rows
    assert check.extend_helpers(m.root).exit_code == 0
    total14 = sum(100 + 7.5 * i for i in range(14))
    rep = check.check_model(m.root, accept_refresh=True, controls={"sales.amount": total14})
    assert rep.exit_code == 0, [f.line() for f in rep.findings] + rep.notes
    assert "D-CONTROL-SET" in rules(rep) and rep.data["master"]["value"] == 0
    write_source_csv(m.source, 6)
    rep = check.check_model(m.root, refresh=True, accept_refresh=True,
                            controls={"sales.amount": sum(100 + 7.5 * i for i in range(6))})
    # v2 (A-aera-6): helper rows below the last raw row fail sales_beyond and the master check until extend
    assert rep.exit_code == 1, [f.line() for f in rep.findings] + rep.notes
    assert "H-BEYOND" in rules(rep) and rep.data["error_cells"] == []
    assert {f.data.get("check") for f in rep.findings if f.rule == "C-FAIL"} >= {"sales_beyond", "master"}
    assert check.extend_helpers(m.root).exit_code == 0
    rep = check.check_model(m.root)
    assert rep.exit_code == 0, [f.line() for f in rep.findings] + rep.notes
    _no_orphans(since)

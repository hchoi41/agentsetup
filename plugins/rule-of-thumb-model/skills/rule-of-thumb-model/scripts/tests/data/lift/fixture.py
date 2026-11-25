"""Test fixture for lift, diff and document (owner M8): a small model folder built by hand in the layout of
design sections 6.3, 7.1 and 9.1, so these modules can be tested before and apart from lib.build.

write_model(root) writes: rotm_model.json, contracts/sales.contract.json, raw/sales.csv, model.spec.json,
model.xlsx (formulas written with openpyxl, no cached values) and model.manifest.json.
write_values_workbook(src, dst, values) writes a copy whose formula cells hold given values instead (a stand-in
for a workbook Excel recalculated, for tests that run without Excel).
"""
from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path
from typing import Any

REGIONS = ["East", "North", "South", "West"]
FOCUS = ["East", "West"]
MONTHS = ["2026-01-31", "2026-02-28", "2026-03-31"]
BUDGET = [[2000, 2100, 2200], [1500, 1500, 1600], [1800, 1700, 1900], [1200, 1300, 1250]]
SCALE = 1000
THRESHOLD = 5000


def sales_rows(n: int = 40) -> list[list[Any]]:
    """Deterministic invoice lines: (invoice_id, line_no, inv_date, region, amount)."""
    rows = []
    for k in range(n):
        month = k % 3
        day = 1 + (k * 7) % 27
        date = dt.date(2026, month + 1, day)
        region = REGIONS[(k * 3 + month) % 4]
        amount = round(500 + (k * 937) % 9000 + (k % 7) * 0.25, 2)
        rows.append([f"INV{1000 + k // 2:05d}", 1 + k % 2, date.isoformat(), region, amount])
    return rows


def contract(rows: list[list[Any]]) -> dict[str, Any]:
    total = round(sum(r[4] for r in rows), 2)
    return {
        "rotm_contract": 1, "table": "sales", "raw_sheet": "raw_sales", "excel_table": "tbl_raw_sales",
        "purpose": "Invoice lines for the fixture model", "grain": "one row per invoice line",
        "key": ["invoice_id", "line_no"],
        "source": {"connector": "csv", "system": "fixture", "object": "sales.csv", "from": None, "where": None},
        "columns": [
            {"name": "invoice_id", "type": "text", "unit": None, "null_rule": "never", "allowed": None,
             "source_expression": "invoice_id", "description": "invoice number"},
            {"name": "line_no", "type": "integer", "unit": "count", "null_rule": "never", "allowed": None,
             "source_expression": "line_no"},
            {"name": "inv_date", "type": "date", "unit": "date", "null_rule": "never", "allowed": None,
             "source_expression": "inv_date"},
            {"name": "region", "type": "text", "unit": None, "null_rule": "never", "allowed": REGIONS,
             "source_expression": "region"},
            {"name": "amount", "type": "number", "unit": "KRW", "null_rule": "never", "allowed": None,
             "source_expression": "amount"}],
        "expect": {"rows": {"min": 1, "max": 1000}, "totals": [{"column": "amount", "value": total, "tolerance": 0}]},
        "refresh": {"frequency": "monthly", "capacity_rows": None, "allow_empty": False},
        "agreement": {"status": "agreed", "reply": "agreed", "by": "fixture", "at": "2026-11-25T00:00:00+09:00",
                      "channel": "example", "source_file": None, "recorded_by": "fixture"},
    }


def spec() -> dict[str, Any]:
    return {
        "rotm_spec": 1, "name": "fixture_sales", "title": "Fixture: sales against budget by region",
        "purpose": "Test model for lift, diff and document.", "output": "model.xlsx", "product": "both",
        "settings": {"depth_limit": 2, "length_limit": 100},
        "limitations": ["Three months only."],
        "sheets": [
            {"name": "inputs", "type": "inputs", "items": [
                {"id": "scale", "label": "Display scale", "value": SCALE, "type": "number", "format": "integer",
                 "unit": "KRW per unit shown", "source": "fixture"},
                {"id": "start_month", "label": "First month", "value": MONTHS[0], "type": "date", "format": "date",
                 "unit": "date", "source": "fixture"},
                {"id": "threshold", "label": "Large invoice line", "value": THRESHOLD, "type": "number",
                 "format": "amount", "unit": "KRW", "source": "fixture"},
                {"id": "regions", "kind": "list", "label": "Regions", "values": REGIONS, "format": "text",
                 "source": "contract sales"},
                {"id": "focus", "kind": "list", "label": "Focus regions", "values": FOCUS, "format": "text",
                 "source": "fixture"},
                {"id": "budget", "kind": "grid", "label": "Budget", "unit": "KRW thousand", "format": "amount",
                 "rows": {"values": REGIONS}, "cols": {"values": MONTHS, "format": "month"},
                 "values": BUDGET, "source": "fixture budget"}]},
            {"name": "raw_sales", "type": "raw", "table": "sales"},
            {"name": "h_sales", "type": "helpers", "table": "sales", "columns": [
                {"id": "month_end", "label": "Month end", "expr": "EOMONTH({this:inv_date},0)", "format": "date"},
                {"id": "amount_k", "label": "Amount (thousand)", "expr": "{this:amount}/{in:scale}",
                 "format": "amount1"},
                {"id": "big", "label": "Large line", "expr": "IF({this:amount}>{in:threshold},1,0)", "format": "flag"},
                {"id": "focus_no", "label": "Focus position",
                 "expr": "IFERROR(MATCH({this:region},{in:focus},0),0)", "format": "integer"}]},
            {"name": "calc", "type": "calc", "blocks": [
                {"id": "actual", "title": "Actual sales", "unit": "KRW thousand", "format": "amount",
                 "rows": {"in": "regions"},
                 "cols": {"timeline": {"start": "start_month", "periods": 3, "step": "month"}},
                 "cell": "SUMIFS({h:sales.amount_k},{h:sales.region},{row},{h:sales.month_end},{col})",
                 "total": {"row": True, "col": True}},
                {"id": "target", "title": "Budget", "unit": "KRW thousand", "format": "amount",
                 "rows": {"same_as": "actual"}, "cols": {"same_as": "actual"},
                 "cell": "INDEX({in:budget},MATCH({row},{in:budget.rows},0),MATCH({col},{in:budget.cols},0))",
                 "total": {"row": True}},
                {"id": "bridge", "title": "Variance", "cols": {"same_as": "actual"}, "items": [
                    {"id": "act", "label": "Actual", "unit": "KRW thousand", "expr": "{b:actual.total}"},
                    {"id": "tgt", "label": "Budget", "unit": "KRW thousand", "expr": "{b:target.total}"},
                    {"id": "var", "label": "Variance", "unit": "KRW thousand", "expr": "{r:act}-{r:tgt}"},
                    {"id": "cum", "label": "Cumulative variance", "unit": "KRW thousand", "expr": "{prev}+{r:var}",
                     "first": "{r:var}"}]}]},
            {"name": "summary", "type": "output", "blocks": [
                {"id": "ytd", "title": "Year to date by region", "rows": {"same_as": "actual"}, "measures": [
                    {"id": "act", "label": "Actual", "format": "amount", "expr": "SUM({grow:actual})", "total": "sum"},
                    {"id": "tgt", "label": "Budget", "format": "amount", "expr": "SUM({grow:target})", "total": "sum"},
                    {"id": "pct", "label": "Variance %", "format": "rate", "expr": "IFERROR({m:act}/{m:tgt}-1,0)",
                     "total": "same", "key": True}]}]},
            {"name": "checks", "type": "checks", "checks": [
                {"id": "actual_ties_raw", "kind": "equal", "label": "Actual block total equals the raw total",
                 "actual": "{blk:actual.total}", "expected": "SUM({raw:sales.amount})/{in:scale}",
                 "tolerance": "check_tolerance"},
                {"id": "neg_var", "kind": "zero", "severity": "alert", "label": "Variance cells below zero",
                 "actual": "COUNTIF({blk:bridge},\"<0\")"},
                {"id": "pct_range", "kind": "range", "severity": "alert", "label": "Variance % between -1 and 1",
                 "actual": "{cell:pct}", "min": -1, "max": 1}]},
        ],
    }


# Layout (design 6.3): B labels, C units, D first value column; row 1 title, row 2 master check.
INPUT_CELLS = {"scale": "inputs!D6", "start_month": "inputs!D7", "threshold": "inputs!D8",
               "check_tolerance": "inputs!D9", "regions": "inputs!D12:D15", "focus": "inputs!D18:D19",
               "budget": "inputs!D23:F26"}
HELPER_COLS = [("row", "A", "row", None), ("in_raw", "B", "flag", None), ("invoice_id", "C", "fetch", "invoice_id"),
               ("line_no", "D", "fetch", "line_no"), ("inv_date", "E", "fetch", "inv_date"),
               ("region", "F", "fetch", "region"), ("amount", "G", "fetch", "amount"),
               ("month_end", "H", "derived", None), ("amount_k", "I", "derived", None),
               ("big", "J", "derived", None), ("focus_no", "K", "derived", None), ("dup", "L", "dup", None)]
CHECK_ROWS = ["sales_rows", "sales_total_amount", "sales_nulls_line_no", "sales_nulls_inv_date",
              "sales_nulls_region", "sales_nulls_amount", "sales_cover", "sales_keys", "actual_ties_raw"]
ALERT_ROWS = {"neg_var": 17, "pct_range": 18}


def helper_formulas(r: int) -> dict[str, str]:
    return {
        "A": "=ROW()", "B": f'=--(INDEX(raw_sales!$A:$A,A{r})<>"")',
        "C": f'=INDEX(raw_sales!$A:$A,A{r})&""', "D": f"=INDEX(raw_sales!$B:$B,A{r})",
        "E": f"=INDEX(raw_sales!$C:$C,A{r})", "F": f'=INDEX(raw_sales!$D:$D,A{r})&""',
        "G": f"=INDEX(raw_sales!$E:$E,A{r})", "H": f"=EOMONTH(E{r},0)", "I": f"=G{r}/inputs!$D$6",
        "J": f"=IF(G{r}>inputs!$D$8,1,0)", "K": f"=IFERROR(MATCH(F{r},inputs!$D$18:$D$19,0),0)",
        "L": f"=--(COUNTIFS($C:$C,C{r},$D:$D,D{r},$B:$B,1)*B{r}>1)",
    }


def write_model(root: Path, *, rows: list[list[Any]] | None = None, capacity: int | None = None) -> dict[str, Path]:
    """Write the fixture model folder; returns the paths. `capacity`: helper rows (default the raw row count)."""
    import openpyxl
    rows = sales_rows() if rows is None else rows
    n = len(rows)
    cap = n if capacity is None else capacity
    root.mkdir(parents=True, exist_ok=True)
    for sub in ("contracts", "queries", "raw", "reports"):
        (root / sub).mkdir(exist_ok=True)
    (root / "rotm_model.json").write_text(json.dumps({
        "rotm_model": 1, "name": "fixture_sales", "created": "2026-11-25T00:00:00+09:00", "created_by": "fixture",
        "plugin_version": "0.1.0", "spec": "model.spec.json", "workbook": "model.xlsx", "product": "both"},
        indent=1) + "\n", encoding="utf-8")
    con = contract(rows)
    from lib import modelfolder
    con["agreement"]["contract_sha256"] = modelfolder.contract_hash(con)
    (root / "contracts" / "sales.contract.json").write_text(json.dumps(con, indent=1) + "\n", encoding="utf-8")
    with open(root / "raw" / "sales.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([c["name"] for c in con["columns"]])
        for r in rows:
            w.writerow(r)
    (root / "model.spec.json").write_text(json.dumps(spec(), indent=1) + "\n", encoding="utf-8")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "cover"
    ws["B1"] = "Fixture: sales against budget by region"
    ws["B2"] = "Master check"
    ws["C2"] = "=checks!$C$2"

    inp = wb.create_sheet("inputs")
    inp["B1"] = "Inputs"
    inp["B2"] = "Master check"
    inp["C2"] = "=checks!$C$2"
    inp["B4"] = "Assumptions"
    for (cell, label, value) in (("6", "Display scale", SCALE), ("7", "First month", dt.date(2026, 1, 31)),
                                 ("8", "Large invoice line", THRESHOLD), ("9", "Check tolerance", 0.001)):
        inp[f"B{cell}"] = label
        inp[f"D{cell}"] = value
    inp["B11"] = "Regions"
    for k, reg in enumerate(REGIONS):
        inp[f"B{12 + k}"] = str(k + 1)
        inp[f"D{12 + k}"] = reg
    inp["B17"] = "Focus regions"
    for k, reg in enumerate(FOCUS):
        inp[f"B{18 + k}"] = str(k + 1)
        inp[f"D{18 + k}"] = reg
    inp["B21"] = "Budget"
    for j, m in enumerate(MONTHS):
        inp.cell(22, 4 + j, dt.date.fromisoformat(m))
    for i, reg in enumerate(REGIONS):
        inp.cell(23 + i, 2, reg)
        for j in range(3):
            inp.cell(23 + i, 4 + j, BUDGET[i][j])
    inp["B28"] = "Contract controls"
    inp["B29"] = "sales: minimum rows"
    inp["D29"] = 1
    inp["B30"] = "sales: maximum rows"
    inp["D30"] = 1000
    inp["B31"] = "sales: control total of amount"
    inp["D31"] = con["expect"]["totals"][0]["value"]
    inp["B32"] = "sales: tolerance of the amount total"
    inp["D32"] = 0

    raw = wb.create_sheet("raw_sales")
    for j, c in enumerate(con["columns"]):
        raw.cell(1, 1 + j, c["name"])
    for i, r in enumerate(rows):
        raw.cell(2 + i, 1, r[0])
        raw.cell(2 + i, 2, r[1])
        raw.cell(2 + i, 3, dt.date.fromisoformat(r[2]))
        raw.cell(2 + i, 4, r[3])
        raw.cell(2 + i, 5, r[4])

    h = wb.create_sheet("h_sales")
    for cid, col, _kind, _raw in HELPER_COLS:
        h[f"{col}1"] = cid
    for r in range(2, 2 + cap):
        for col, f in helper_formulas(r).items():
            h[f"{col}{r}"] = f

    calc = wb.create_sheet("calc")
    calc["B1"] = "Calculations"
    calc["B2"] = "Master check"
    calc["C2"] = "=checks!$C$2"
    # actual: heading 4, header 5, body D6:F9, total row 10, total column G
    calc["B4"] = "Actual sales"
    calc["D5"] = "=EOMONTH(inputs!$D$7,0)"
    calc["E5"] = "=EOMONTH(D5,1)"
    calc["F5"] = "=EOMONTH(E5,1)"
    calc["G5"] = "Total"
    for i in range(4):
        r = 6 + i
        calc[f"B{r}"] = f"=inputs!$D${12 + i}"
        for j, col in enumerate("DEF"):
            calc[f"{col}{r}"] = f"=SUMIFS(h_sales!$I:$I,h_sales!$F:$F,$B{r},h_sales!$H:$H,{col}$5)"
        calc[f"G{r}"] = f"=SUM(D{r}:F{r})"
    calc["B10"] = "Total"
    for col in "DEF":
        calc[f"{col}10"] = f"=SUM({col}6:{col}9)"
    calc["G10"] = "=SUM(D6:F9)"
    # target: heading 12, header 13, body D14:F17, total row 18
    calc["B12"] = "Budget"
    for col in "DEF":
        calc[f"{col}13"] = f"={col}5"
    for i in range(4):
        r = 14 + i
        calc[f"B{r}"] = f"=$B{6 + i}"
        for col in "DEF":
            calc[f"{col}{r}"] = (f"=INDEX(inputs!$D$23:$F$26,MATCH($B{r},inputs!$B$23:$B$26,0),"
                                 f"MATCH({col}$13,inputs!$D$22:$F$22,0))")
    calc["B18"] = "Total"
    for col in "DEF":
        calc[f"{col}18"] = f"=SUM({col}14:{col}17)"
    # bridge (items): heading 20, header 21, body D22:F25
    calc["B20"] = "Variance"
    for col in "DEF":
        calc[f"{col}21"] = f"={col}5"
    for k, label in enumerate(["Actual", "Budget", "Variance", "Cumulative variance"]):
        calc[f"B{22 + k}"] = label
    for col in "DEF":
        calc[f"{col}22"] = f"={col}$10"
        calc[f"{col}23"] = f"={col}$18"
        calc[f"{col}24"] = f"={col}22-{col}23"
    calc["D25"] = "=D24"
    calc["E25"] = "=D25+E24"
    calc["F25"] = "=E25+F24"

    out = wb.create_sheet("summary")
    out["B1"] = "Summary"
    out["B2"] = "Master check"
    out["C2"] = "=checks!$C$2"
    out["B4"] = "Year to date by region"
    out["D5"], out["E5"], out["F5"] = "Actual", "Budget", "Variance %"
    for i in range(4):
        r = 6 + i
        out[f"B{r}"] = f"=calc!$B{6 + i}"
        out[f"D{r}"] = f"=SUM(calc!$D{6 + i}:$F{6 + i})"
        out[f"E{r}"] = f"=SUM(calc!$D{14 + i}:$F{14 + i})"
        out[f"F{r}"] = f"=IFERROR(D{r}/E{r}-1,0)"
    out["B10"] = "Total"
    out["D10"] = "=SUM(D6:D9)"
    out["E10"] = "=SUM(E6:E9)"
    out["F10"] = "=IFERROR(D10/E10-1,0)"

    chk = wb.create_sheet("checks")
    chk["B1"] = "Checks"
    chk["B2"] = "Master check"
    chk["C2"] = "=--(OR(SUM(I5:I13)>0,COUNT(I5:I13)<>COUNTA(B5:B13)))"
    for col, text in zip("BCDEFGHIJ", ["Id", "What it checks", "Actual", "Expected", "Maximum", "Difference",
                                       "Tolerance", "Result", "Checks"]):
        chk[f"{col}4"] = text
    formulas = {
        "sales_rows": {"D": "=COUNTA(raw_sales!$A:$A)-1", "E": "=inputs!$D$29", "F": "=inputs!$D$30",
                       "I": "=--(OR(D{r}<E{r},D{r}>F{r}))"},
        "sales_total_amount": {"D": "=SUM(raw_sales!$E:$E)", "E": "=inputs!$D$31", "G": "=D{r}-E{r}",
                               "H": "=inputs!$D$32", "I": "=--(ABS(G{r})>H{r})"},
        "sales_nulls_line_no": {"D": "=COUNTA(raw_sales!$A:$A)-COUNTA(raw_sales!$B:$B)", "I": "=--(D{r}<>0)"},
        "sales_nulls_inv_date": {"D": "=COUNTA(raw_sales!$A:$A)-COUNTA(raw_sales!$C:$C)", "I": "=--(D{r}<>0)"},
        "sales_nulls_region": {"D": "=COUNTA(raw_sales!$A:$A)-COUNTA(raw_sales!$D:$D)", "I": "=--(D{r}<>0)"},
        "sales_nulls_amount": {"D": "=COUNTA(raw_sales!$A:$A)-COUNTA(raw_sales!$E:$E)", "I": "=--(D{r}<>0)"},
        "sales_cover": {"D": "=COUNTA(raw_sales!$A:$A)-1-COUNT(h_sales!$A:$A)", "E": 0, "I": "=--(D{r}>E{r})"},
        "sales_keys": {"D": "=SUM(h_sales!$L:$L)", "I": "=--(D{r}<>0)"},
        "actual_ties_raw": {"D": "=calc!$G$10", "E": "=SUM(raw_sales!$E:$E)/inputs!$D$6", "G": "=D{r}-E{r}",
                            "H": "=inputs!$D$9", "I": "=--(ABS(G{r})>H{r})"},
        "neg_var": {"D": '=COUNTIF(calc!$D$22:$F$25,"<0")', "I": "=--(D{r}<>0)"},
        "pct_range": {"D": "=summary!$F$10", "E": -1, "F": 1, "I": "=--(OR(D{r}<E{r},D{r}>F{r}))"},
    }
    rows_of = {cid: 5 + k for k, cid in enumerate(CHECK_ROWS)}
    rows_of.update(ALERT_ROWS)
    chk["B16"] = "Alerts"
    for cid, r in rows_of.items():
        chk[f"B{r}"] = cid
        chk[f"C{r}"] = cid.replace("_", " ")
        for col, f in formulas[cid].items():
            chk[f"{col}{r}"] = f.format(r=r) if isinstance(f, str) else f
    path = root / "model.xlsx"
    wb.save(path)

    manifest = {
        "rotm_manifest": 1, "plugin_version": "0.1.0", "built_at": "2026-11-25T00:00:00+09:00",
        "spec": "model.spec.json", "spec_sha256": None, "dictionary_sha256": None, "workbook": "model.xlsx",
        "settings": {"depth_limit": 2, "length_limit": 100, "allowed_literals": [0, 1], "product": "both"},
        "sheets": [
            {"name": "cover", "type": "cover", "font_size": 9},
            {"name": "inputs", "type": "inputs", "font_size": 9},
            {"name": "raw_sales", "type": "raw", "font_size": 8, "table": "sales", "excel_table": "tbl_raw_sales",
             "landing": "static", "query_name": None, "columns": [c["name"] for c in con["columns"]],
             "types": [c["type"] for c in con["columns"]], "rows": n, "digest": None, "snapshot": "raw/sales.csv"},
            {"name": "h_sales", "type": "helpers", "font_size": 8, "table": "sales", "first_row": 2,
             "last_row": 1 + cap, "capacity": cap,
             "columns": [dict({"id": cid, "col": col, "kind": kind, "formula": helper_formulas(2)[col],
                               "header": cid}, **({"raw_column": rc} if rc else {}))
                         for cid, col, kind, rc in HELPER_COLS]},
            {"name": "calc", "type": "calc", "font_size": 8, "blocks": [
                {"id": "actual", "shape": "grid", "body": "D6:F9", "total": "G10", "checks": ["actual_ties_raw"]},
                {"id": "target", "shape": "grid", "body": "D14:F17", "total": "D18:F18", "checks": []},
                {"id": "bridge", "shape": "items", "body": "D22:F25", "total": None, "checks": ["neg_var"]}]},
            {"name": "summary", "type": "output", "font_size": 9, "blocks": [
                {"id": "ytd", "shape": "measures", "body": "D6:F9", "total": "D10:F10", "checks": ["pct_range"]}]},
            {"name": "checks", "type": "checks", "font_size": 9, "master": "C2", "results": "I5:I13",
             "ids": "B5:B13"}],
        "inputs": INPUT_CELLS, "cells": {"pct": "summary!F10"},
        "checks": [{"id": cid, "kind": "equal" if cid in ("sales_total_amount", "actual_ties_raw") else
                    "range" if cid in ("sales_rows", "pct_range") else "at_most" if cid == "sales_cover" else "zero",
                    "result": f"checks!I{r}", "auto": cid.startswith("sales_"),
                    "severity": "alert" if cid in ALERT_ROWS else "error"} for cid, r in rows_of.items()],
        "master_check": "checks!C2",
        "master_links": ["cover!C2", "inputs!C2", "calc!C2", "summary!C2"],
        "cell_types": {"inputs": [["D6:D9", "input"], ["B6:B9", "row_header"]]},
    }
    (root / "model.manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return {"root": root, "spec": root / "model.spec.json", "workbook": path,
            "manifest": root / "model.manifest.json", "csv": root / "raw" / "sales.csv"}


def write_values_workbook(src: Path, dst: Path, values: dict[tuple[str, str], Any]) -> Path:
    """Copy `src` with each formula cell named in `values` ((sheet, coord) -> value) replaced by that value, and
    every other formula cell left as it is."""
    import openpyxl
    wb = openpyxl.load_workbook(src)
    for (sheet, coord), v in values.items():
        wb[sheet][coord] = v
    wb.save(dst)
    return dst

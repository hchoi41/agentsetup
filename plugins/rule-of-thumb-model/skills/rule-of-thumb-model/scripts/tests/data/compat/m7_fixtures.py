"""Workbook fixtures for the M7 tests (compat, trace, split). Owner: M7.

Each function writes a small workbook with openpyxl into a given path and returns the path. The workbooks are
made at test time (no binary files in the repository) and hold formulas only; values exist after Excel has
recalculated them.
"""
from __future__ import annotations

from pathlib import Path

REGIONS = ("East", "North", "South", "West")
SALES = [  # invoice_id, region, amount (KRW)
    ("I001", "East", 1200), ("I002", "North", 800), ("I003", "East", 450), ("I004", "South", 2300),
    ("I005", "West", 990), ("I006", "North", 1500), ("I007", "South", 310), ("I008", "East", 760),
]


def model_like(path: Path) -> Path:
    """A workbook laid out like a built model (design 6.3, 7.1): raw_sales, h_sales, inputs, calc, checks."""
    from openpyxl import Workbook
    wb = Workbook()
    raw = wb.active
    raw.title = "raw_sales"
    raw.append(["invoice_id", "region", "amount"])
    for row in SALES:
        raw.append(list(row))
    h = wb.create_sheet("h_sales")
    h.append(["row", "in_raw", "region", "amount", "amount_k"])
    for r in range(2, len(SALES) + 2):
        h.cell(r, 1, "=ROW()")
        h.cell(r, 2, f'=--(INDEX(raw_sales!$A:$A,A{r})<>"")')
        h.cell(r, 3, f'=INDEX(raw_sales!$B:$B,A{r})&""')
        h.cell(r, 4, f"=INDEX(raw_sales!$C:$C,A{r})")
        h.cell(r, 5, f"=D{r}/inputs!$D$6")
    inp = wb.create_sheet("inputs")
    inp["B1"] = "Inputs"
    inp["B5"], inp["C5"], inp["D5"] = "Assumptions", "Unit", "Value"
    inp["B6"], inp["C6"], inp["D6"] = "Display scale", "KRW per unit shown", 1000
    inp["B7"], inp["C7"], inp["D7"] = "Check tolerance", "KRW thousand", 0.001
    calc = wb.create_sheet("calc")
    calc["B1"] = "Calculations"
    calc["B4"] = "Actual sales"
    calc["B5"], calc["C5"], calc["D5"] = "Region", "Unit", "Amount"
    for i, reg in enumerate(REGIONS):
        r = 6 + i
        calc.cell(r, 2, reg)
        calc.cell(r, 3, "KRW thousand")
        calc.cell(r, 4, f"=SUMIFS(h_sales!$E:$E,h_sales!$C:$C,$B{r})")
    calc["B10"], calc["D10"] = "Total", "=SUM(D6:D9)"
    chk = wb.create_sheet("checks")
    chk["B1"] = "Checks"
    chk["B2"], chk["C2"] = "Master check", "=--(OR(SUM(I5:I5)>0,COUNT(I5:I5)<>COUNTA(B5:B5)))"
    chk["B5"], chk["C5"] = "actual_ties_raw", "Actual total equals the raw total"
    chk["D5"], chk["E5"] = "=calc!$D$10", "=SUM(raw_sales!$C:$C)/inputs!$D$6"
    chk["G5"], chk["H5"], chk["I5"] = "=D5-E5", "=inputs!$D$7", "=--(ABS(G5)>H5)"
    wb.save(path)
    return path


def compat_cases(path: Path) -> Path:
    """One-product functions, structured references, CONCATENATE, a missing _xlfn. prefix and _xludf."""
    from openpyxl import Workbook
    from openpyxl.worksheet.table import Table
    wb = Workbook()
    ws = wb.active
    ws.title = "data"
    ws.append(["Id", "Qty", "Price", "Line", "Label"])
    for i in range(1, 6):
        r = i + 1
        ws.cell(r, 1, f"A{i:02d}")
        ws.cell(r, 2, i)
        ws.cell(r, 3, 10 * i)
        ws.cell(r, 4, "=Sales[[#This Row],[Qty]]*Sales[[#This Row],[Price]]")
        ws.cell(r, 5, f'=CONCATENATE(A{r},"-",B{r})')
    ws.add_table(Table(displayName="Sales", ref="A1:E6"))
    calc = wb.create_sheet("calc")
    calc["A1"] = "Lookup"
    calc["B1"] = "A03"
    calc["C1"] = "=XLOOKUP(B1,data!A2:A6,data!D2:D6)"                 # stored without _xlfn. (as openpyxl writes it)
    calc["C2"] = "=IFERROR(_xludf.IFS(B1=\"A03\",1,B1=\"A04\",2),0)"   # a known function stored as a UDF
    calc["C3"] = "=SUM(Sales[Line])"                                   # whole-table reference: reported only
    calc["C4"] = '=QUERY(data!A1:E6,"select A")'                       # Google Sheets only: reported only
    calc["C5"] = "=_xlfn.CONCAT(B1,\"x\")"                             # fine in both products
    calc["C6"] = "=CONCATENATE(B1,\" units\")"
    wb.save(path)
    return path


def deep_cases(path: Path) -> Path:
    """Formulas over the depth limit 2: a copied-down column, a row copied across, and single cells."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "calc"
    ws["A1"], ws["B1"], ws["C1"], ws["D1"] = "Key", "Code", "Value", "Result"
    keys = ["k1", "k2", "k3", "k4", "k5", "k6"]
    for i, k in enumerate(keys):
        r = i + 2
        ws.cell(r, 1, k)
        ws.cell(r, 2, keys[(i + 2) % len(keys)])
        ws.cell(r, 3, (i + 1) * 7.5)
        # depth 3: IFERROR > INDEX > MATCH (design 8.1 example)
        ws.cell(r, 4, f'=IFERROR(INDEX($C$2:$C$7,MATCH(B{r},$A$2:$A$7,0)),0)')
    ws["F1"] = "Rounded clamp"
    ws["F2"] = "=ROUND(MAX(MIN(SUM(C2:C7),C3),0),1)"                  # depth 4
    ws["A10"] = "Across"
    for c in range(2, 6):                                             # B10:E10, one formula across the row
        col = "ABCDEFG"[c - 1]
        ws.cell(10, c, f"=IF(ABS({col}11-1)>0.5,ROUND(SUM({col}11:{col}12),0),0)")
        ws.cell(11, c, c * 1.25)
        ws.cell(12, c, c * 2)
    ws["H2"] = "=IFERROR(INDEX($C$2:$C$7,MATCH(B2,$A$2:$A$7,0)),0)+1"  # depth 3, alone in its column
    other = wb.create_sheet("Other Sheet")
    other["A1"] = 5
    other["A2"] = "=IFERROR(INDEX(calc!$C$2:$C$7,MATCH(calc!B2,calc!$A$2:$A$7,0)),0)*A1"
    wb.save(path)
    return path


def with_chart_and_names(path: Path) -> Path:
    """A deep column plus a chart, a defined name, a conditional format and a data validation (fragile parts)."""
    from openpyxl.chart import BarChart, Reference
    from openpyxl.formatting.rule import CellIsRule
    from openpyxl.styles import PatternFill
    from openpyxl.workbook.defined_name import DefinedName
    from openpyxl.worksheet.datavalidation import DataValidation
    deep_cases(path)
    from openpyxl import load_workbook
    wb = load_workbook(path)
    ws = wb["calc"]
    chart = BarChart()
    chart.add_data(Reference(ws, min_col=3, min_row=1, max_row=7), titles_from_data=True)
    ws.add_chart(chart, "J2")
    wb.defined_names["rate_cell"] = DefinedName("rate_cell", attr_text="calc!$C$2")
    ws.conditional_formatting.add("C2:C7", CellIsRule(operator="greaterThan", formula=["40"],
                                                      fill=PatternFill("solid", fgColor="F2F2F2")))
    dv = DataValidation(type="list", formula1='"k1,k2,k3"')
    ws.add_data_validation(dv)
    dv.add("B2:B7")
    wb.save(path)
    return path

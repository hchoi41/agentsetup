"""`check` and `extend`: after a refresh, raw sheets against their contracts, coverage of every raw row by
the helper columns, recalculation in Excel, every check cell read back (design sections 7, 9, 15; F09, F10, F27).

Owner: M5. openpyxl is imported inside functions only.

check, in this order:
1. The contract state of every raw table (must be agreed; an "example" agreement only inside the plugin).
2. With Excel: one Excel job (lib.excel) that refreshes every query first when --refresh is given (BackgroundQuery
   off, waits until done), recalculates fully, lists error cells, reads every check cell and the master check, and
   saves. Without Excel (or --no-excel) nothing is recalculated and the report says so (exit 3).
3. Each raw sheet, read from the saved file, against its contract: header names and order, row count, key
   uniqueness, nulls, types (v3: a number, date or boolean cell stored as text is D-TYPE), allowed values,
   control totals. The control total (v3, A-aera-4, C-sol-1): --control of this run; else the total recorded in
   raw/<table>.controls.json for this exact sheet digest; else the agreed contract's. A value on the inputs sheet
   is never the control: when the cell the total check links holds another value, D-CONTROL-INPUT (a warning).
4. The raw sheet's digest against the landed snapshot recorded in the manifest, and raw/<table>.csv against the
   same digest. A changed raw sheet that is loaded by a query (the manifest says so, or v3, C-sol-7: the workbook
   holds a query table at A1 of the raw sheet) is accepted as the new snapshot only with --accept-refresh, only
   when step 3 found no error, and (v3, A-aera-1) only when the sheet's rows differ from the rows rotm.py last
   wrote into the workbook (the manifest's workbook_digest): rows equal to those and older than raw/<t>.csv are
   R-CHANGED (R-NEWER). When --refresh was asked for and no refresh ran, nothing is accepted (N-NOT-ACCEPTED). A
   changed static raw sheet is never accepted (raw data changes only by landing, spec 2.2 P2). The manifest's
   contract_sha256 is compared with the agreed contract (CT-VERSION). v4: rows (and --control totals) are accepted
   only from a live query table, one query_tables finds at A1 of the raw sheet (A-aera-3); a table with CT-VERSION
   gets nothing accepted or recorded in that run (C-sol-2); a query attached by hand (plan 5.3 rule, _hand_attached)
   is recorded at an acceptance also when its rows equal the landed snapshot (C-sol-6).
   v4 (A-aera-9): a number stored in a text column is D-TYPE and is tagged in the digest (NUMBER_TAG).
5. Coverage: the raw sheet's last row (column A, the never-null first contract column) against the last row of
   every helper column; uncovered rows get the exact fix (rotm.py extend, or the copy by hand, design 7.2).
6. Every check cell and the master check: 0 passes, anything else fails (design 9). v4 (C-sol-5): without a
   completed recalculation a cell with no saved value is not calculated (summary_line); an alert shows a value only
   when it holds a number other than 0, and a raised alert reads ALERT, never FAIL (J-1).
7. v4 (A-aera-1): labels that Excel's criteria matching merges (lib.criteria, D-CRITERIA), on the raw rows and,
   after a completed recalculation, on the values Excel saved. v4 (C-sol-3): when Excel started and could not open
   or calculate the workbook, X-OPEN (exit 1), not "not recalculated".
8. v5 ([A-aera-3, C-sol-5], C-RECOMPUTE, lib.recompute): after a completed recalculation, every check, alert and
   the master computed again from the saved values of their own operand cells, and their formulas compared with
   the text build recorded (result_formula, master_formula) when the manifest holds it.
v5 ([A-aera-2, C-sol-1, C-sol-4]): --refresh counts as a refresh only when the refresh step refreshed at least one
query table; a raw table the manifest records as loaded by Power Query whose sheet holds no query table gets
X-NO-QUERY (nothing accepted for it); before Excel starts, --refresh is refused when queries/<t>.pq.m differs from
the query text the attach recorded (query_body_sha256).
v6 (r5 verdict A-aera-2): with --refresh and Excel, before the refresh, the formula of each query rotm.py attached
(the raw entry records query_formula_sha256 and the sheet holds a live query table) is read from the workbook through
Excel and hashed with land.query_formula_sha256; a difference, or a formula that cannot be read, is X-QUERY-CHANGED
(a warning; an error with --accept-refresh). Then no table is refreshed (the job runs without pq_refresh, so the
refresh path that did not run accepts nothing: raw/, the controls files and the manifest stay as they are). A raw
entry without the field gets a note. v6 (r5 verdict C-sol-6): after a refresh that ran, I-HIDDEN-DECIMALS
(information) for a number column whose values hold more decimals than its number format shows. v6 (r5 verdict
A-aera-5): a logical value in a text column is D-TYPE and is tagged in the digest (LOGICAL_TAG).
v7 (r6 verdict [A-aera-2, C-sol-5]): the read-back of the query formula also runs for --accept-refresh given without
--refresh, when Excel is available; a difference is X-QUERY-CHANGED (an error) and no table is accepted in that run
(raw/<t>.csv, raw/<t>.controls.json and the manifest stay byte for byte). Without Excel the acceptance notes that
the query was not compared; for a raw entry without query_formula_sha256 it prints the note of v6.
v7 (r6 verdict A-aera-8, X-SHEETS-ONLY): before Excel starts, the workbook's formulas are searched for the functions
only Google Sheets has (functions.sheets_only_names()). When it holds one, check starts no Excel job (no refresh, no
recalculation, no save): X-SHEETS-ONLY (information) names each function with its count and first cell, the run
ends as when Excel is missing ("audited, not recalculated: functions only Google Sheets has (X-SHEETS-ONLY)"; exit 3
when nothing else fails), and --refresh or --accept-refresh accepts nothing.
v8 (r7 verdict A-aera-1): at an acceptance, when the manifest's landed digest differs from its workbook_digest
(raw/<t>.csv was landed after rotm.py last wrote rows into the workbook, for example with land --force), no changed
rows are accepted, whatever the sheet holds: R-CHANGED says that raw/<t>.csv was landed after the workbook was last
written, and raw/<t>.csv, raw/<t>.controls.json and the manifest stay byte for byte. This covers the case R-NEWER
covered at an acceptance; a check without --accept-refresh keeps the R-NEWER text. A manifest without
workbook_digest behaves as in v7 (R-NO-WBDIGEST).
v8 (r7 verdict [A-aera-5, A-aera-6] part 1, V-INPUT-RANGE): each scalar input whose specification declares "valid"
(min and max, or list) is read from the saved workbook (after the recalculation, or as saved when Excel did not
run); a saved value outside it is an error naming the cell, the value, the range and the input's note (spec.
valid_problem gives the range texts of build's own check). No row is added to the checks sheet.
v9 (r8 verdict [A-aera-2, C-sol-1]): The control total used for a table and column is, in this order: --control
given in this run; else the total accepted for this snapshot in raw/<t>.controls.json under the agreement in force;
else the agreed contract's total. A total accepted under an earlier agreement (the contract was agreed again since,
for example with another total) is not used: check prints a note that names it, and when the rows do not meet the
contract's total, D-TOTAL names both totals. Give --control with the source's current total; it is recorded under
the agreement in force (check --accept-refresh stores agreement.contract_sha256 in the entry; lib.controls).

Exit codes: 0 everything passed after recalculation; 1 an error finding (or, with --strict, a warning);
2 refused; 3 nothing failed but the workbook was not recalculated ("audited but not recalculated"); 5 Excel busy.

extend: the documented coverage fix (design 7.2): copy the last helper row down to the raw sheet's last row, and
clear the helper rows below the raw sheet's last row (v2, A-aera-6: they read empty raw cells as 0), through Excel
when Excel is available (it keeps queries, charts and names), else with openpyxl when the package has
no part openpyxl would drop, else refuse (design 11). The workbook is changed in place after a copy is kept in
reports/backups/, and the result is compared by code with the copy (formulas and constants outside the new rows
and the cleared rows must be equal; tables, names, charts, queries, conditional formats and validations must still
be there). v3 (A-aera-8): a refresh to zero rows clears every helper row; a later refresh with rows gets row 2 from
the formulas the manifest keeps.

v3 messages without a rule id (notes and refusals): N-NOT-ACCEPTED, N-HAND-QUERY, F-REFRESH-STATE (exit 2, before
Excel starts), the note for a manifest without contract_sha256; R-CHANGED texts R-NEWER and R-NO-WBDIGEST; the
fix texts F-TOTAL (a failing <t>_total_<column> check) and V-NO-VALUE C-CYCLE / N-RECALC.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import math
import os
import re
import shutil
import time
from dataclasses import dataclass, field, replace as _replace
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import common, manifest as manifest_mod, modelfolder

# Rule ids used by check and extend: id -> (severity, what fails). Ids shared with the audit (design 14) keep the
# audit's meaning: R-CHANGED, R-CONTRACT, H-COVERAGE, C-FAIL, C-MISSING, V-ERROR.
RULES: dict[str, tuple[str, str]] = {
    "CT-STATE": ("error", "a raw table's contract is not agreed (missing, invalid, proposed, stale, or an example "
                          "agreement outside the plugin)"),
    "R-CONTRACT": ("error", "the raw sheet's headers differ from the contract's column names or order"),
    "R-CHANGED": ("error", "the raw sheet differs from the landed snapshot recorded in the manifest, and the change "
                           "was not accepted"),
    "R-ACCEPTED": ("info", "refreshed raw rows passed the contract and were recorded as the new landed snapshot"),
    "R-SNAPSHOT": ("error", "raw/<table>.csv differs from the digest recorded in the manifest (a warning when the "
                            "file is missing)"),
    "D-ROWS": ("error", "the row count is outside the contract's expected rows"),
    "D-KEY": ("error", "a key value repeats, or a key column is empty"),
    "D-NULL": ("error", "an empty value in a column whose null rule is never"),
    "D-TYPE": ("error", "a value that does not fit the column's contract type"),
    "D-ALLOWED": ("error", "a value outside the column's allowed values"),
    "D-TOTAL": ("error", "a column total differs from its control total by more than the tolerance"),
    "D-CONTROL-INPUT": ("warning", "the control cell on the inputs sheet holds another value than the control total "
                                   "check used (--control, the accepted total of the snapshot, or the contract), and "
                                   "it was not written there"),
    "CT-VERSION": ("error", "the raw sheet was built from another version of its contract than the agreed one "
                            "(v3, A-aera-4)"),
    "X-NO-QUERY": ("warning", "--refresh was asked for, but the workbook has no Power Query query or connection (an "
                              "error with --accept-refresh)"),
    "D-KEY-SEPARATOR": ("warning", "a key part of the raw rows holds '|', the separator of the helper sheet's joined "
                                   "key, so the workbook's key check may show a repeat that is none (v3, C-sol-5)"),
    "D-CONTROL-SET": ("info", "an accepted --control total was written into the input cell the workbook's own "
                              "total check links"),
    "H-COVERAGE": ("error", "the helper rows end before the raw sheet's last row"),
    "H-BEYOND": ("error", "helper rows below the raw sheet's last row: they read empty raw cells as 0 and feed the "
                          "calculations until rotm.py extend clears them (v2; replaces the information H-SPARE)"),
    "C-FAIL": ("error", "a check cell or the master check is not 0 after recalculation"),
    "C-CACHED": ("warning", "a check cell's cached value is not 0 (the workbook was not recalculated)"),
    "C-ALERT": ("info", "an alert check is not 0 (alerts are not part of the master check, design 6.7)"),
    "C-MISSING": ("error", "the manifest lists no master check or no check"),
    "V-ERROR": ("error", "an error value in a cell after recalculation"),
    "V-NO-VALUE": ("error", "a formula cell with no value after recalculation: Excel names it as circular (fix "
                            "C-CYCLE), or saved it without a value (fix N-RECALC)"),
    "X-REFRESH": ("error", "a Power Query refresh failed"),
    "X-OPEN": ("error", "Excel started but could not open or calculate the workbook (v4, C-sol-3; most often a "
                        "formula Excel rejects)"),
    "D-CRITERIA": ("warning", "a label and another value of its criteria range are the same for Excel's criteria "
                              "matching, so SUMIFS and the other criteria functions add both under the one label "
                              "(v4, A-aera-1; lib.criteria); or a label that is also a value of its criteria range "
                              "and that Excel's criteria matching does not match with itself (for example the "
                              "texts TRUE and FALSE, a label from a cell that starts with =, < or >, or a label "
                              "that holds ~* or ~?), so the function leaves out the rows with this label (v8, "
                              "C-sol-1; v9, A-aera-1 and C-sol-2; reason self_mismatch)"),
    "X-QUERY-CHANGED": ("warning", "--refresh or --accept-refresh (with Excel): the formula of a query rotm.py "
                                   "attached differs in the workbook from the one recorded at the attach, or cannot "
                                   "be read; no table was refreshed and nothing was accepted (an error with "
                                   "--accept-refresh; v6, r5 verdict A-aera-2; v7, r6 verdict A-aera-2: also for "
                                   "--accept-refresh without --refresh; v9, r8 verdict C-sol-3: also a query that a "
                                   "query attached by hand reads, recorded by build as query_reads, that differs, is "
                                   "missing or cannot be read)"),
    "X-SHEETS-ONLY": ("info", "the workbook holds a function only Google Sheets has (references/functions.md: Excel "
                              "no, Sheets yes); Excel cannot calculate it, so check did not open the workbook in "
                              "Excel and did not recalculate it (exit 3 when nothing else fails; v7, r6 verdict "
                              "A-aera-8)"),
    "I-HIDDEN-DECIMALS": ("info", "a number column of a raw table holds values with more decimals than its number "
                                  "format shows; the values are stored and calculated in full (v6, r5 verdict "
                                  "C-sol-6; the exit code does not change)"),
    "V-INPUT-RANGE": ("error", "a scalar input's saved value lies outside the valid range or the list of valid values "
                               "its specification declares (\"valid\"); no row of the checks sheet tests it (v8, r7 "
                               "verdict A-aera-5)"),
    "X-EXTEND": ("error", "extend changed something outside the new helper rows; the workbook was restored"),
    "H-EXTENDED": ("info", "extend copied the last helper row down (one line per helper sheet)"),
    "H-CLEARED": ("info", "extend cleared the helper rows below the raw sheet's last row (one line per helper sheet)"),
}

FIRST_DATA_ROW = 2                 # raw and helper sheets: header in row 1, data from row 2 (design 7.1)
EXCEL_MAX_ROW = 1_048_576
MAX_EXAMPLES = 5                   # example cells named in one finding message
REFRESH_WAIT_S = 300               # seconds a Power Query refresh may take (excel.pq_refresh default)
DETAIL_FIRST_COL, DETAIL_LAST_COL = "D", "H"   # checks sheet: D actual .. H tolerance (design 9.1)


# ----------------------------------------------------------------------------- small helpers

def _rotm() -> str:
    return f"python -P -X utf8 -B \"{(common.scripts_dir() / 'rotm.py').as_posix()}\""


def _n(x: Any) -> str:
    """A number for a message: thousands separators, no trailing .0."""
    if isinstance(x, bool) or x is None:
        return str(x)
    if isinstance(x, int):
        return f"{x:,}"
    if isinstance(x, float):
        if math.isfinite(x) and x.is_integer() and abs(x) < 1e15:
            return f"{int(x):,}"
        return f"{x:,.6f}".rstrip("0").rstrip(".")
    return str(x)


def _is_formula(v: Any) -> bool:
    if isinstance(v, str):
        return v.startswith("=")
    return getattr(v, "text", None) is not None and not isinstance(v, str)   # ArrayFormula


def _cell_text(v: Any) -> Any:
    text = getattr(v, "text", None)
    if text is not None and not isinstance(v, str):
        return str(text)
    return v


class _Err(str):
    """An Excel error value read back (equal to its text, e.g. '#REF!')."""


def _from_excel(v: Any) -> Any:
    if isinstance(v, dict) and set(v) == {"error"}:
        return _Err(v["error"])
    if isinstance(v, list):
        return [_from_excel(x) for x in v]
    return v


def _is_error_value(v: Any) -> bool:
    if isinstance(v, _Err):
        return True
    try:
        from . import excel
        if excel.is_error(v):
            return True
    except Exception:  # noqa: BLE001 - lib.excel is optional for the reader
        pass
    from .workbook import ERROR_VALUES
    return isinstance(v, str) and v in ERROR_VALUES


@dataclass
class SheetRows:
    """Rows of one raw table (a raw sheet or a snapshot CSV); duck-compatible with land.RawTable."""
    table: str
    columns: list[str]
    rows: list[list[Any]]
    types: list[str] = field(default_factory=list)
    digest: str = ""


@dataclass
class _Model:
    root: Path
    workbook: Path
    rel: str
    manifest: dict[str, Any]


def _resolve(target: str | Path) -> _Model:
    """The model folder, its workbook and its manifest; common.Refusal naming what is missing."""
    p = Path(os.path.abspath(common.native_path(target)))
    if p.is_dir():
        root = modelfolder.find_model_root(p)
        if root is None:
            raise common.Refusal(f"{p.as_posix()} is not a model folder (no {modelfolder.MARKER} in it or above it)",
                                 fix="give the model folder made by rotm.py init, or a workbook inside it; for any "
                                     "other workbook use rotm.py audit")
        marker = modelfolder.read_marker(root)
        wb = Path(root) / str(marker.get("workbook") or "model.xlsx")
    elif p.is_file():
        root = modelfolder.find_model_root(p)
        if root is None:
            raise common.Refusal(f"{p.name} is not inside a model folder (no {modelfolder.MARKER} above it)",
                                 fix="check works on a model built by rotm.py build; for any other workbook use "
                                     "rotm.py audit")
        wb = p
    else:
        raise common.Refusal(f"{p.as_posix()}: not found", fix="give the model folder or its workbook")
    root = Path(root)
    if not wb.is_file():
        raise common.Refusal(f"{wb.name} does not exist in {root.as_posix()}: the model is not built yet",
                             fix=f"build it: {_rotm()} build \"{(root / 'model.spec.json').as_posix()}\"")
    man = manifest_mod.load_manifest(wb)
    if man is None:
        raise common.Refusal(f"{wb.name} has no manifest ({manifest_mod.manifest_path(wb).name}), so its raw sheets, "
                             "helper columns and checks are not known",
                             fix="build the workbook with rotm.py build (it writes the manifest); for a workbook "
                                 "made by hand use rotm.py audit")
    return _Model(root=root, workbook=wb, rel=common.relpath_posix(wb, root), manifest=man)


# ----------------------------------------------------------------------------- reading the workbook

@dataclass
class _HelperFacts:
    sheet: str
    present: bool
    first_row: int
    columns: list[tuple[str, str]]                    # (helper column id, column letter)
    last: dict[str, int] = field(default_factory=dict)   # column letter -> last row of the unbroken formula run
    any_last: int = 0                                 # last row holding any helper formula


@dataclass
class _Facts:
    raw: dict[str, tuple[list[Any], list[list[Any]]] | None] = field(default_factory=dict)   # table -> (header, rows)
    helpers: dict[str, _HelperFacts] = field(default_factory=dict)
    formulas: dict[str, str] = field(default_factory=dict)     # "sheet!A1" -> formula text (cells asked for)


def _read_sheet_rows(ws: Any) -> tuple[list[Any], list[list[Any]]]:
    """(header, data rows) of a raw sheet: header = row 1 up to its first empty cell; rows cut to that width;
    trailing empty rows removed (the same reading as manifest.sheet_rows)."""
    try:
        ws.reset_dimensions()
    except AttributeError:
        pass
    it = ws.iter_rows(values_only=True)
    first = next(it, ())
    header: list[Any] = []
    for v in first:
        if v is None or (isinstance(v, str) and v == ""):
            break
        header.append(v)
    width = len(header)
    rows: list[list[Any]] = []
    if width:
        for r in it:
            vals = [_cell_text(v) for v in r[:width]]
            vals += [None] * (width - len(vals))
            rows.append(vals)
    while rows and all(v is None or v == "" for v in rows[-1]):
        rows.pop()
    return header, rows


def _scan_helpers(ws: Any, facts: _HelperFacts) -> None:
    """Fill facts.last and facts.any_last from the helper sheet's formula cells."""
    try:
        ws.reset_dimensions()
    except AttributeError:
        pass
    if not facts.columns:
        return
    idx = {letter: common.col_index(letter) for _id, letter in facts.columns}
    max_col = max(idx.values())
    last = {letter: facts.first_row - 1 for letter in idx}
    broken = {letter: False for letter in idx}
    any_last = 0
    r = facts.first_row - 1
    for row in ws.iter_rows(min_row=facts.first_row, max_col=max_col, values_only=True):
        r += 1
        has_any = False
        for letter, ci in idx.items():
            v = row[ci - 1] if ci - 1 < len(row) else None
            if _is_formula(v):
                has_any = True
                if not broken[letter] and last[letter] == r - 1:
                    last[letter] = r
            elif not broken[letter] and last[letter] == r - 1:
                broken[letter] = True
        if has_any:
            any_last = r
    facts.last = last
    facts.any_last = any_last


def _read_facts(wb_path: Path, man: dict[str, Any], *, formula_refs: Sequence[str] = ()) -> _Facts:
    """One read-only pass over the workbook (formulas as text, data_only=False)."""
    import warnings

    import openpyxl
    facts = _Facts()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            book = openpyxl.load_workbook(wb_path, read_only=True, data_only=False, keep_links=False)
        except Exception as exc:  # noqa: BLE001 - a reader failure is a refusal naming it (F06)
            raise common.Refusal(f"{wb_path.name}: openpyxl cannot read this workbook ({type(exc).__name__}: "
                                 f"{str(exc)[:200]})", fix="open it in Excel, save it as .xlsx and run again") from None
        try:
            names = set(book.sheetnames)
            for entry in man.get("sheets", []):
                if entry.get("type") == "raw":
                    t = entry["table"]
                    facts.raw[t] = _read_sheet_rows(book[entry["name"]]) if entry["name"] in names else None
                elif entry.get("type") == "helpers":
                    t = entry["table"]
                    cols = [(str(c.get("id")), str(c.get("col"))) for c in entry.get("columns") or [] if c.get("col")]
                    hf = _HelperFacts(sheet=entry["name"], present=entry["name"] in names,
                                      first_row=int(entry.get("first_row") or FIRST_DATA_ROW), columns=cols)
                    if hf.present:
                        _scan_helpers(book[entry["name"]], hf)
                    facts.helpers[t] = hf
            by_sheet: dict[str, list[tuple[str, int, int]]] = {}
            for ref in formula_refs:
                sheet, cell = common.parse_ref(ref)
                if sheet in names:
                    c, r = common.split_cell(cell)
                    by_sheet.setdefault(sheet, []).append((ref, r, c))
            for sheet, items in by_sheet.items():
                ws = book[sheet]
                lo, hi = min(r for _x, r, _c in items), max(r for _x, r, _c in items)
                maxc = max(c for _x, _r, c in items)
                grid: dict[tuple[int, int], Any] = {}
                for r, row in enumerate(ws.iter_rows(min_row=lo, max_row=hi, max_col=maxc, values_only=True), lo):
                    for c, v in enumerate(row, 1):
                        grid[(r, c)] = v
                for ref, r, c in items:
                    v = grid.get((r, c))
                    if _is_formula(v):
                        facts.formulas[ref] = str(_cell_text(v))
        finally:
            book.close()
    return facts


def _cached_values(wb_path: Path, refs: Sequence[str]) -> tuple[dict[str, Any], bool]:
    """Cached values (data_only) of single cells and one-row ranges, and whether any formula cell had one."""
    import warnings

    import openpyxl
    out: dict[str, Any] = {}
    wanted: dict[str, list[tuple[str, int, int, int]]] = {}
    for ref in refs:
        sheet, addr = common.parse_ref(ref)
        c1, r1, c2, _r2 = common.range_bounds(addr)
        wanted.setdefault(sheet or "", []).append((ref, r1, c1, c2))
    found = False
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            book = openpyxl.load_workbook(wb_path, read_only=True, data_only=True, keep_links=False)
        except Exception:  # noqa: BLE001
            return out, False
        try:
            for sheet, items in wanted.items():
                if sheet not in book.sheetnames:
                    continue
                ws = book[sheet]
                lo, hi = min(i[1] for i in items), max(i[1] for i in items)
                maxc = max(i[3] for i in items)
                grid: dict[tuple[int, int], Any] = {}
                for r, row in enumerate(ws.iter_rows(min_row=lo, max_row=hi, max_col=maxc, values_only=True), lo):
                    for c, v in enumerate(row, 1):
                        grid[(r, c)] = v
                for ref, r, c1, c2 in items:
                    if c1 == c2:
                        out[ref] = grid.get((r, c1))
                        found = found or out[ref] is not None
                    else:
                        out[ref] = [[grid.get((r, c)) for c in range(c1, c2 + 1)]]
        finally:
            book.close()
    return out, found


# ----------------------------------------------------------------------------- raw rows against the contract

def _coord(col_index0: int, row_index0: int, first_row: int) -> str:
    return f"{common.col_letter(col_index0 + 1)}{first_row + row_index0}"


def _examples(items: Sequence[tuple[str, Any]]) -> str:
    shown = []
    for cell, value in items[:MAX_EXAMPLES]:
        shown.append(f"{cell}" if value is None else f"{cell} '{value}'")
    more = f" and {len(items) - MAX_EXAMPLES:,} more" if len(items) > MAX_EXAMPLES else ""
    return ", ".join(shown) + more


def _control_value(v: Any) -> tuple[float, float | None]:
    if isinstance(v, (tuple, list)):
        return float(v[0]), (float(v[1]) if len(v) > 1 and v[1] is not None else None)
    return float(v), None


def check_raw(table: Any, contract: dict[str, Any], *, controls: dict[str, Any] | None = None,
              sheet: str | None = None, first_row: int = FIRST_DATA_ROW,
              control_sources: dict[str, str] | None = None) -> list[common.Finding]:
    """Findings for raw rows against their contract (design 15): header names and order (R-CONTRACT), rows
    (D-ROWS), key uniqueness (D-KEY), nulls (D-NULL), types (D-TYPE), allowed values (D-ALLOWED) and control
    totals (D-TOTAL). `table` has .columns (header) and .rows (lists of values: CSV text, typed values or the
    values openpyxl reads); a land.RawTable fits. `controls` maps a column to a source-side total (or
    (total, tolerance)) that replaces the contract's total for that column."""
    from .contract import coerce, value_text
    findings: list[common.Finding] = []
    tname = contract.get("table", "?")
    where = sheet or contract.get("raw_sheet") or f"raw_{tname}"
    names = [c["name"] for c in contract["columns"]]
    header = ["" if h is None else str(h) for h in (getattr(table, "columns", None) or [])]
    rows = [list(r) for r in (getattr(table, "rows", None) or [])]
    if header != names:
        missing = [n for n in names if n not in header]
        extra = [h for h in header if h not in names]
        bits = []
        if missing:
            bits.append("missing " + ", ".join(missing))
        if extra:
            bits.append("not in the contract " + ", ".join(h or "(empty)" for h in extra))
        if not missing and not extra:
            bits.append("same names in another order")
        findings.append(common.Finding(
            "R-CONTRACT", "error",
            f"the headers of {where} differ from the contract of '{tname}' ({'; '.join(bits)}): expected "
            f"{', '.join(names)}; found {', '.join(h or '(empty)' for h in header) or 'no header'}",
            "land the data again with the contract's columns in the contract's order (shape the SQL or the query to "
            "the contract)", sheet=where, cell="A1",
            data={"expected": names, "found": header, "missing": missing, "extra": extra}))
    idx: dict[str, int] = {}
    for i, h in enumerate(header):
        idx.setdefault(h, i)
    n = len(rows)
    expect = contract.get("expect") or {}
    rspec = expect.get("rows") or {}
    allow_empty = bool((contract.get("refresh") or {}).get("allow_empty", False))
    bad_rows = None
    if "exact" in rspec and n != rspec["exact"]:
        bad_rows = f"exactly {_n(rspec['exact'])}"
    elif "min" in rspec and not (rspec["min"] <= n <= rspec["max"]):
        bad_rows = f"from {_n(rspec['min'])} to {_n(rspec['max'])}"
    if bad_rows:
        findings.append(common.Finding(
            "D-ROWS", "error", f"{where} has {_n(n)} data rows; the contract of '{tname}' expects {bad_rows}",
            "check the query's filter against the contract's scope; if the source really holds this many rows, "
            "propose the contract again with the new range and get the user's agreement",
            sheet=where, data={"rows": n, "expected": rspec}))
    elif n == 0 and not allow_empty:
        findings.append(common.Finding(
            "D-ROWS", "error", f"{where} has no data rows and the contract of '{tname}' does not allow an empty "
            "result", "check the query and its source; refresh again", sheet=where, data={"rows": 0}))
    typed_cols: dict[str, list[Any]] = {}
    for col in contract["columns"]:
        name, ctype = col["name"], col["type"]
        if name not in idx:
            continue
        j = idx[name]
        allowed = col.get("allowed")
        allowed_text = None
        if allowed:
            allowed_text = set()
            for a in allowed:
                ok, typed = coerce(a, ctype)
                allowed_text.add(value_text(typed if ok and typed is not None else a))
        nulls: list[tuple[str, Any]] = []
        bad: list[tuple[str, Any]] = []
        outside: list[tuple[str, Any]] = []
        typed_values: list[Any] = []
        for i, r in enumerate(rows):
            v = r[j] if j < len(r) else None
            ok, typed = coerce(v, ctype)
            if not ok:
                bad.append((_coord(j, i, first_row), v))
                typed_values.append(None)
                continue
            typed_values.append(typed)
            if typed is None:
                nulls.append((_coord(j, i, first_row), None))
                continue
            if allowed_text is not None and value_text(typed) not in allowed_text:
                outside.append((_coord(j, i, first_row), value_text(typed)))
        typed_cols[name] = typed_values
        known_nulls = (col.get("known_nulls") or {}).get("max", 0) if col["null_rule"] == "never" else 0
        if nulls and col["null_rule"] == "never" and not (known_nulls and len(nulls) <= known_nulls):
            findings.append(common.Finding(
                "D-NULL", "error", f"column {name} of {where} is empty in {_n(len(nulls))} rows "
                f"({_examples(nulls)}); its null rule is never"
                + (f" and the contract agrees only {_n(known_nulls)} as known" if known_nulls else ""),
                "fix the source or the query so that every row has a value; if the empty cells are known and kept, "
                "propose the contract again with --known-nulls <column>=<count>; if empty values are normal, with "
                "null rule allowed; and get the user's agreement",
                sheet=where, cell=nulls[0][0], count=len(nulls), cells=[c for c, _v in nulls[:20]],
                data={"column": name, "nulls": len(nulls), "known": known_nulls}))
        if bad:
            findings.append(common.Finding(
                "D-TYPE", "error", f"column {name} of {where} has {_n(len(bad))} values that are not {ctype} "
                f"({_examples(bad)})",
                f"cast the column to {ctype} in the SQL or the query (for a date: an ISO date; for a number: '.' as "
                "the decimal mark, no thousands separator)", sheet=where, cell=bad[0][0], count=len(bad),
                cells=[c for c, _v in bad[:20]], data={"column": name, "type": ctype, "bad": len(bad)}))
        if outside:
            findings.append(common.Finding(
                "D-ALLOWED", "error", f"column {name} of {where} has {_n(len(outside))} values outside the allowed "
                f"values {', '.join(sorted(allowed_text or []))} ({_examples(outside)})",
                "map the values to the allowed ones in the SQL or the query, or propose the contract again with the "
                "new values and get the user's agreement", sheet=where, cell=outside[0][0], count=len(outside),
                cells=[c for c, _v in outside[:20]], data={"column": name, "outside": len(outside)}))
    key = [k for k in contract.get("key") or [] if k in typed_cols]
    if key and len(key) == len(contract.get("key") or []):
        seen: dict[tuple[str, ...], int] = {}
        dups: list[tuple[str, Any]] = []
        empty_key: list[tuple[str, Any]] = []
        never = {c["name"] for c in contract["columns"] if c["null_rule"] == "never"}
        pairs: list[tuple[int, int]] = []          # (row of the later copy, row of the first copy)
        for i in range(n):
            parts = [typed_cols[k][i] for k in key]
            if any(p is None for p in parts):
                if any(parts[x] is None and key[x] not in never for x in range(len(key))):
                    empty_key.append((f"A{first_row + i}", None))
                continue
            # v2, A-aera-9: the one key equality of land, the workbook's dup column and lift: exact text,
            # case-sensitive, no wildcard, text never read as a number (common.key_tuple)
            tup = common.key_tuple(parts)
            if tup in seen:
                dups.append((f"A{first_row + i}", " + ".join(value_text(p) for p in parts)))
                pairs.append((first_row + i, first_row + seen[tup]))
            else:
                seen[tup] = i
        known_repeats = (expect.get("key_repeats") or {}).get("max", 0)
        if dups and not (known_repeats and len(dups) <= known_repeats):
            lines = ", ".join(f"row {_n(later)} repeats row {_n(earlier)}" for later, earlier in pairs[:MAX_EXAMPLES])
            if len(pairs) > MAX_EXAMPLES:
                lines += f" and {_n(len(pairs) - MAX_EXAMPLES)} more"
            findings.append(common.Finding(
                "D-KEY", "error", f"{_n(len(dups))} rows of {where} repeat an earlier key {' + '.join(key)} "
                f"({_examples(dups)}; {lines}); the grain is '{contract.get('grain')}'; keys compare as exact text "
                "(case-sensitive, no wildcard, text never read as a number)"
                + (f"; the contract agrees only {_n(known_repeats)} as known" if known_repeats else ""),
                f"look at these rows of {where} with the user ({lines}): if the user accepts them as they are, "
                "propose the contract again with --key-repeats <count> (known exceptions) and get the user's "
                "agreement; otherwise have the source corrected and land again. Do not remove rows to make the "
                "check hold", sheet=where, cell=dups[0][0], count=len(dups),
                cells=[c for c, _v in dups[:20]],
                data={"key": key, "duplicates": len(dups), "known": known_repeats,
                      "rows": [{"row": later, "first_row": earlier} for later, earlier in pairs[:20]]}))
        if empty_key:
            findings.append(common.Finding(
                "D-KEY", "error", f"a key column of {where} ({' + '.join(key)}) is empty in {_n(len(empty_key))} rows "
                f"({_examples(empty_key)})", "every key column needs a value in every row; fix the source or the query",
                sheet=where, cell=empty_key[0][0], count=len(empty_key), cells=[c for c, _v in empty_key[:20]],
                data={"key": key, "empty": len(empty_key)}))
    totals: dict[str, tuple[float, float, str]] = {}
    for tot in expect.get("totals") or []:
        totals[tot["column"]] = (float(tot["value"]), float(tot.get("tolerance", 0) or 0), "the contract")
    for col, v in (controls or {}).items():
        value, tol = _control_value(v)
        base_tol = totals.get(col, (0.0, 0.0, ""))[1]
        src = (control_sources or {}).get(col, "--control")
        totals[col] = (value, base_tol if tol is None else tol, src)
    for col, (value, tol, src) in totals.items():
        if col not in typed_cols:
            continue
        nums = [x for x in typed_cols[col] if isinstance(x, (int, float)) and not isinstance(x, bool)]
        # v2, A-aera-4: an exact sum against the control total, within the agreed tolerance only (no allowance
        # that grows with the total)
        ok, total, difference = common.control_total_ok(nums, value, tol)
        s, diff = float(total), float(difference)
        if not ok:
            findings.append(common.Finding(
                "D-TOTAL", "error", f"the sum of {col} on {where} is {_n(s)}; the control total from {src} is "
                f"{_n(value)}; the difference {_n(diff)} is over the tolerance {_n(tol)}",
                "find the missing or extra rows (compare the query's filter with the source); if the source total "
                f"itself changed, give the new one with rotm.py check --accept-refresh --control {tname}.{col}=<value> "
                f"(it is recorded for this snapshot in raw/{tname}.controls.json and written into the control cell on "
                "the inputs sheet)", sheet=where,
                data={"column": col, "sum": s, "control": value, "tolerance": tol, "difference": diff, "source": src}))
    return findings


def read_snapshot_rows(path: Path, table: str) -> SheetRows:
    """raw/<table>.csv as text rows (empty lines skipped)."""
    header, rows = manifest_mod.read_csv(path)
    out = [r for r in rows if any(c != "" for c in r)]
    return SheetRows(table=table, columns=list(header), rows=[list(r) for r in out])


def _write_snapshot(path: Path, header: Sequence[Any], rows: Sequence[Sequence[Any]], types: Sequence[str]) -> None:
    """Write a landed snapshot (design 4: UTF-8, comma, header row, ISO dates, '.' decimal, empty = null)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    width = len(header)
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["" if h is None else str(h) for h in header])
        for r in rows:
            cells = list(r)[:width] + [None] * max(0, width - len(r))
            w.writerow([manifest_mod.normalise_value(v, types[i] if i < len(types) else "text")
                        for i, v in enumerate(cells)])
    os.replace(tmp, path)


# ----------------------------------------------------------------------------- coverage

def _coverage(facts: _Facts, man: dict[str, Any], root: Path, raw_names: dict[str, str]
              ) -> tuple[list[common.Finding], dict[str, dict[str, Any]]]:
    findings: list[common.Finding] = []
    data: dict[str, dict[str, Any]] = {}
    for entry in man.get("sheets", []):
        if entry.get("type") != "helpers":
            continue
        t = entry["table"]
        hf = facts.helpers.get(t)
        hs = entry["name"]
        raw_sheet = raw_names.get(t, f"raw_{t}")
        raw = facts.raw.get(t)
        if hf is None or not hf.present:
            findings.append(common.Finding(
                "H-COVERAGE", "error", f"the workbook has no helper sheet {hs} (the manifest lists it)",
                "rebuild the workbook with rotm.py build --force", sheet=hs))
            continue
        if raw is None:
            continue
        header, rows = raw
        last_raw = FIRST_DATA_ROW - 1
        for i, r in enumerate(rows):
            if r and r[0] not in (None, ""):
                last_raw = FIRST_DATA_ROW + i
        first = hf.first_row
        lasts = hf.last or {}
        covered = min(lasts.values()) if lasts else first - 1
        reach = max(lasts.values()) if lasts else first - 1
        last_col = max((letter for _i, letter in hf.columns), key=common.col_index) if hf.columns else "A"
        info = {"helper_sheet": hs, "raw_sheet": raw_sheet, "raw_last_row": last_raw,
                "raw_data_rows": max(0, last_raw - 1), "helper_first_row": first, "helper_last_row": covered,
                "helper_any_last_row": hf.any_last, "uncovered": max(0, last_raw - covered),
                "spare": max(0, covered - max(last_raw, first - 1)), "last_col": last_col}
        data[t] = info
        if last_raw > covered:
            unc = last_raw - covered
            msg = (f"{raw_sheet} has {_n(last_raw - 1)} data rows (rows {FIRST_DATA_ROW} to {_n(last_raw)}); "
                   + (f"{hs} covers rows {first} to {_n(covered)}" if covered >= first else f"{hs} covers no row")
                   + f"; {_n(unc)} raw rows ({_n(covered + 1)} to {_n(last_raw)}) are not covered")
            short = [f"{letter} ({cid})" for cid, letter in hf.columns if lasts.get(letter, 0) == covered]
            if reach > covered and short:
                msg += (f"; column {', '.join(short)} ends at row {_n(covered)} while other helper columns reach "
                        f"row {_n(reach)}")
            if covered >= first:
                fix = (f"{_rotm()} extend \"{root.as_posix()}\" --table {t} (or copy {hs} row {_n(covered)}, "
                       f"A:{last_col}, down to row {_n(last_raw)})")
            else:
                # v3, A-aera-8: a helper sheet with zero rows; extend writes row 2 from the manifest's formulas
                fix = (f"{_rotm()} extend \"{root.as_posix()}\" --table {t} ({hs} has no formula row: extend writes "
                       f"row {first} from the helper formulas the manifest keeps, then fills it down to row "
                       f"{_n(last_raw)})")
            findings.append(common.Finding(
                "H-COVERAGE", "error", msg, fix, sheet=hs, cell=f"A{covered + 1}",
                data={"table": t, "raw_last_row": last_raw, "helper_last_row": covered, "uncovered": unc,
                      "first_uncovered": covered + 1, "last_col": last_col}))
        elif reach > last_raw and reach >= first:
            # v2, A-aera-6: a helper row below the last raw row reads empty raw cells as 0 (a number column), so a
            # helper such as --({this:amount}=0) counts it; the check <t>_beyond fails until extend clears it.
            below = max(last_raw, first - 1) + 1
            spare = reach - below + 1
            findings.append(common.Finding(
                "H-BEYOND", "error", f"{hs} rows {_n(below)} to {_n(reach)} lie below the last raw row of "
                f"{raw_sheet} (row {_n(last_raw)}): {_n(spare)} helper rows read empty raw cells as 0 and feed the "
                "calculations that read the helper columns",
                f"{_rotm()} extend \"{root.as_posix()}\" --table {t} (it clears {hs} rows {_n(below)} to "
                f"{_n(reach)}), then check again",
                sheet=hs, cell=f"A{below}",
                data={"table": t, "spare": spare, "first_beyond": below, "last_beyond": reach,
                      "raw_last_row": last_raw}))
    return findings, data


def coverage(wb: Any, manifest: dict[str, Any], *, model_root: str | Path | None = None) -> list[common.Finding]:
    """H-COVERAGE and H-BEYOND findings with the exact fix (design 7.2) for every helper sheet.
    `wb` is the workbook path (the facts are read from the saved file)."""
    path = Path(os.path.abspath(common.native_path(wb)))
    root = Path(model_root) if model_root else (modelfolder.find_model_root(path) or path.parent)
    facts = _read_facts(path, manifest)
    raw_names = {e["table"]: e["name"] for e in manifest.get("sheets", []) if e.get("type") == "raw"}
    return _coverage(facts, manifest, Path(root), raw_names)[0]


# ----------------------------------------------------------------------------- check cells

def _check_layout(man: dict[str, Any]) -> tuple[str | None, list[dict[str, Any]], dict[str, str]]:
    """(master ref, check entries with a result ref, check id -> detail range D r:H r on the result's row)."""
    master = man.get("master_check")
    checks = [c for c in man.get("checks", []) if c.get("result")]
    details: dict[str, str] = {}
    for c in checks:
        sheet, cell = common.parse_ref(c["result"])
        try:
            _col, row = common.split_cell(cell)
        except ValueError:
            continue
        details[c["id"]] = f"{common.quote_sheet(sheet or 'checks')}!{DETAIL_FIRST_COL}{row}:{DETAIL_LAST_COL}{row}"
    return master, checks, details


def _expected_ref(result_ref: str) -> str:
    """The expected cell (column E) on the row of a check result, e.g. checks!I9 -> checks!E9 (design 9.1)."""
    sheet, cell = common.parse_ref(result_ref)
    _col, row = common.split_cell(cell)
    return f"{sheet or 'checks'}!E{row}"


def _state(v: Any) -> str:
    if _is_error_value(v):
        return "error"
    if v is None or v == "":
        return "empty"
    if isinstance(v, bool):
        return "text"
    if isinstance(v, (int, float)):
        return "pass" if v == 0 else "fail"
    return "text"


def _fix_for_check(cid: str, root: Path, man: dict[str, Any]) -> str:
    tables = manifest_mod.raw_tables(man)
    for t in tables:
        if cid == f"{t}_cover":
            return f"run {_rotm()} extend \"{root.as_posix()}\" --table {t}, then check again"
        if cid == f"{t}_beyond":
            return (f"run {_rotm()} extend \"{root.as_posix()}\" --table {t} (it clears the helper rows below the "
                    "last raw row; the H-BEYOND line names them), then check again")
        if cid == f"{t}_rows":
            return (f"compare the rows of raw_{t} with the contract's expected rows; if the source really holds this "
                    "many rows, propose the contract again and get the user's agreement")
        if cid.startswith(f"{t}_total_"):
            # v3, C-sol-1 (F-TOTAL): the control total comes from the source system, never from a typed cell
            col = cid[len(f"{t}_total_"):]
            return (f"find the missing or extra rows in raw_{t} (compare them with the source system); after a "
                    f"refresh with a new source total run {_rotm()} check \"{root.as_posix()}\" --accept-refresh "
                    f"--control {t}.{col}=<source total>")
        if cid.startswith(f"{t}_nulls_"):
            return f"fix the source or the query so that the column has a value in every row of raw_{t}"
        if cid == f"{t}_keys":
            return (f"the rows of raw_{t} whose dup cell on h_{t} is 1 repeat an earlier key (the D-KEY line names "
                    "them, with the row of the earlier copy): look at those rows with the user; if the user accepts "
                    "them as they are, propose the contract again with --key-repeats <count> and get the user's "
                    "agreement; otherwise have the source corrected and land again. Do not remove rows to make "
                    "the check hold")
    return ("compare the check's actual and expected cells; rotm.py trace <workbook> <cell> lists the chain back "
            "to the raw rows")


def _detail_text(row: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(row, list) or not row:
        return "", {}
    vals = row[0] if isinstance(row[0], list) else row
    vals = list(vals) + [None] * (5 - len(vals))
    actual, expected, maximum, diff, tol = vals[:5]
    out = {"actual": actual, "expected": expected, "max": maximum, "difference": diff, "tolerance": tol}
    bits = []
    if actual is not None:
        bits.append(f"actual {_n(actual) if isinstance(actual, (int, float)) else actual}")
    if expected is not None and maximum is not None:
        bits.append(f"range {_n(expected) if isinstance(expected, (int, float)) else expected} to "
                    f"{_n(maximum) if isinstance(maximum, (int, float)) else maximum}")
    elif expected is not None:
        bits.append(f"expected {_n(expected) if isinstance(expected, (int, float)) else expected}")
    if tol is not None:
        bits.append(f"tolerance {_n(tol) if isinstance(tol, (int, float)) else tol}")
    return ("; " + ", ".join(bits)) if bits else "", out


def _report_checks(report: common.Report, man: dict[str, Any], root: Path, values: dict[str, Any],
                   recalculated: bool, any_cached: bool) -> dict[str, Any]:
    master_ref, checks, details = _check_layout(man)
    summary: dict[str, Any] = {"checks": [], "master": None, "count": len(checks), "failed": 0, "alerts": 0,
                               "alerts_shown": 0, "error_checks": 0, "held": 0, "not_calculated": 0,
                               "alerts_not_calculated": 0}
    if not master_ref or not checks:
        report.error("C-MISSING", "the manifest lists " + ("no master check" if not master_ref else "no check cell"),
                     "rebuild the workbook with rotm.py build (it generates the checks and the master check)")
    suffix = "" if recalculated else " (cached value; not recalculated)"
    failed_ids = []
    any_value = False
    for c in checks:
        cid, ref = c["id"], c["result"]
        v = values.get(ref)
        st = _state(v)
        text, det = _detail_text(values.get(details.get(cid, "")))
        sev = c.get("severity", "error")
        label = c.get("label") or cid
        sheet, cell = common.parse_ref(ref)
        entry = {"id": cid, "kind": c.get("kind"), "label": label, "severity": sev, "result_ref": ref,
                 "result": v if not isinstance(v, str) else str(v), "state": st, **det}
        summary["checks"].append(entry)
        # v4 (C-sol-5): without a completed recalculation, a check or alert cell with no saved value is not
        # calculated: not failing and not raised. After a recalculation an empty result stays an error (C-FAIL).
        no_value = st == "empty" and not recalculated
        any_value = any_value or not no_value
        # v2, J-4 (d): error checks and alerts are counted apart (an alert is not part of the master check); v4
        # (C-sol-5): an alert shows a value only when its cell holds a number other than 0
        if sev == "alert":
            summary["alerts"] += 1
            if no_value:
                summary["alerts_not_calculated"] += 1
            elif st == "fail":
                summary["alerts_shown"] += 1
        else:
            summary["error_checks"] += 1
            if no_value:
                summary["not_calculated"] += 1
            elif st == "pass":
                summary["held"] += 1
        if st == "pass":
            report.note(f"check {cid} OK ({ref}){suffix}: {label}{text}")
            continue
        if no_value:
            continue
        # v4 (J-1): a raised alert shows ALERT, never FAIL
        word = "ALERT" if sev == "alert" else "FAIL"
        shown = {"fail": f"{word} ({_n(v)})", "error": f"the error value {v}", "empty": "an empty result",
                 "text": f"text '{v}' instead of 0 or 1"}[st]
        if not recalculated:
            report.warning("C-CACHED", f"check {cid} ({label}) holds {shown}{text}{suffix}",
                           "recalculate: rotm.py check without --no-excel on a PC with Excel", sheet=sheet, cell=cell)
            continue
        if sev == "alert":
            report.info("C-ALERT", f"alert {cid} ({label}) shows {shown}{text}; alerts are not part of the master check",
                        "", sheet=sheet, cell=cell)
            continue
        summary["failed"] += 1
        failed_ids.append(cid)
        report.error("C-FAIL", f"check {cid} ({label}) shows {shown}{text}", _fix_for_check(cid, root, man),
                     sheet=sheet, cell=cell, data={"check": cid, "value": entry["result"]})
    if master_ref:
        v = values.get(master_ref)
        st = _state(v)
        summary["master"] = {"ref": master_ref, "value": v if not isinstance(v, str) else str(v), "state": st}
        sheet, cell = common.parse_ref(master_ref)
        if st == "pass":
            report.note(f"master check {master_ref} = 0 (OK){suffix}")
        elif recalculated:
            shown = {"fail": f"FAIL ({_n(v)})", "error": f"the error value {v}", "empty": "an empty result",
                     "text": f"text '{v}'"}[st]
            because = f": failing checks {', '.join(failed_ids)}" if failed_ids else \
                ": no listed check fails, so a check row is empty, deleted or not a number"
            report.error("C-FAIL", f"the master check {master_ref} shows {shown}{because}",
                         "fix the failing checks listed above; the master check fails while any check fails or a "
                         "check result is empty", sheet=sheet, cell=cell, data={"check": "master"})
        elif st != "empty":
            report.warning("C-CACHED", f"the master check {master_ref} holds {v!r}{suffix}",
                           "recalculate: rotm.py check without --no-excel on a PC with Excel", sheet=sheet, cell=cell)
    summary["line"] = summary_line(summary["held"], summary["error_checks"], summary["alerts_shown"],
                                   summary["alerts"], not_calculated=summary["not_calculated"],
                                   alerts_not_calculated=summary["alerts_not_calculated"])
    if not recalculated and any_value:
        summary["line"] += " (cached values)"
    return summary


def summary_line(held: int, checks: int, alerts_shown: int = 0, alerts: int = 0, *, not_calculated: int = 0,
                 alerts_not_calculated: int = 0) -> str:
    """The one wording of the check count (v2, J-4 (d)): 'checks: H of N hold; alerts: S of A show a value'; the
    alerts part only when the model has alerts. rotm.py status prints the same words (the check record's field
    line). v4 (C-sol-5): cells with no value are not calculated: 'checks: not calculated (0 of N have a value)'
    when no check has a value, 'checks: H of N hold, K not calculated (no value)' when some have none; alerts
    the same. check adds ' (cached values)' when it did not recalculate and at least one cell has a value."""
    def part(word: str, n: int, good: int, verb: str, missing: int) -> str:
        if n and missing >= n:
            return f"{word}: not calculated (0 of {n} have a value)"
        text = f"{word}: {good} of {n} {verb}"
        return text + (f", {missing} not calculated (no value)" if missing else "")
    line = part("checks", checks, held, "hold", not_calculated)
    if alerts:
        line += "; " + part("alerts", alerts, alerts_shown, "show a value", alerts_not_calculated)
    return line


# ----------------------------------------------------------------------------- check

def _controls_by_table(controls: dict[str, Any] | None, contracts: dict[str, dict[str, Any] | None],
                       tables: Sequence[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key, value in (controls or {}).items():
        if "." not in key:
            raise common.Refusal(f"--control {key}: write TABLE.COLUMN=VALUE", fix="e.g. --control sales.amount=1234567.5")
        t, col = key.split(".", 1)
        if t not in tables:
            from .schemas import nearest
            near = nearest(t, tables) if tables else None
            raise common.Refusal(f"--control {key}: the model has no raw table '{t}'"
                                 + (f"; did you mean '{near}'?" if near else ""),
                                 fix="use one of: " + ", ".join(tables))
        contract = contracts.get(t)
        if contract is not None:
            cols = {c["name"]: c["type"] for c in contract["columns"]}
            if col not in cols:
                from .schemas import nearest
                near = nearest(col, list(cols))
                raise common.Refusal(f"--control {key}: the contract of '{t}' has no column '{col}'"
                                     + (f"; did you mean '{near}'?" if near else ""),
                                     fix="use a number column of the contract")
            if cols[col] not in ("integer", "number"):
                raise common.Refusal(f"--control {key}: column '{col}' has type {cols[col]}; a control total needs a "
                                     "number column", fix="choose an integer or number column")
        try:
            out.setdefault(t, {})[col] = value if isinstance(value, (tuple, list)) else float(value)
        except (TypeError, ValueError):
            raise common.Refusal(f"--control {key}={value}: not a number", fix="write the total as a number") from None
    return out


_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
_MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_DOC_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_LOCATION_RE = re.compile(r'(?:^|;)\s*Location\s*=\s*(?:"((?:[^"]|"")*)"|([^;]*))', re.IGNORECASE)


def _part_rels(z: Any, names: set[str], part: str) -> dict[str, tuple[str, str]]:
    """Relationship id -> (type, target part) of one package part (internal targets only)."""
    import posixpath
    import xml.etree.ElementTree as ET
    folder, name = posixpath.split(part)
    rels = posixpath.join(folder, "_rels", name + ".rels")
    if rels not in names:
        return {}
    out: dict[str, tuple[str, str]] = {}
    try:
        root = ET.fromstring(z.read(rels))
    except (ET.ParseError, KeyError, OSError):
        return {}
    for rel in root.iter(_PKG_REL):
        target = rel.get("Target") or ""
        if rel.get("TargetMode") == "External" or not target:
            continue
        path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(folder, target))
        out[rel.get("Id") or ""] = (rel.get("Type") or "", path)
    return out


def query_tables(workbook: str | Path) -> dict[str, str | None]:
    """Sheet name -> query name for each sheet that holds a query-backed table (tableType="queryTable") whose
    range starts at A1 (v3, C-sol-7: a query attached by hand, whatever its name). The query's name is the
    Location of its connection in xl/connections.xml (else the connection's name without 'Query - '); None when
    the connection is not found. {} for a package that cannot be read. Reads the zip parts only (no openpyxl)."""
    import xml.etree.ElementTree as ET
    import zipfile
    p = Path(common.native_path(workbook))
    try:
        z = zipfile.ZipFile(p)
    except (zipfile.BadZipFile, OSError):
        return {}
    out: dict[str, str | None] = {}
    with z:
        names = set(z.namelist())
        if "xl/workbook.xml" not in names:
            return {}
        try:
            book = ET.fromstring(z.read("xl/workbook.xml"))
        except (ET.ParseError, KeyError, OSError):
            return {}
        book_rels = _part_rels(z, names, "xl/workbook.xml")
        connections: dict[str, str | None] = {}
        conn_part = next((target for typ, target in book_rels.values() if typ.endswith("/connections")),
                         "xl/connections.xml")
        if conn_part in names:
            try:
                for c in ET.fromstring(z.read(conn_part)).iter(f"{_MAIN_NS}connection"):
                    db = c.find(f"{_MAIN_NS}dbPr")
                    m = _LOCATION_RE.search((db.get("connection") or "") if db is not None else "")
                    if m:
                        qname = (m.group(1).replace('""', '"') if m.group(1) is not None else m.group(2)).strip()
                    else:
                        qname = re.sub(r"^Query - ", "", c.get("name") or "").strip()
                    connections[c.get("id") or ""] = qname or None
            except (ET.ParseError, KeyError, OSError):
                connections = {}
        for sheet in book.iter(f"{_MAIN_NS}sheet"):
            sheet_name = sheet.get("name") or ""
            rid = sheet.get(f"{_DOC_REL_NS}id") or ""
            sheet_part = book_rels.get(rid, ("", ""))[1]
            if sheet_part not in names:
                continue
            for typ, table_part in _part_rels(z, names, sheet_part).values():
                if not typ.endswith("/table") or table_part not in names:
                    continue
                try:
                    tab = ET.fromstring(z.read(table_part))
                except (ET.ParseError, KeyError, OSError):
                    continue
                ref = (tab.get("ref") or "").replace("$", "").upper()
                if tab.get("tableType") != "queryTable" or not (ref == "A1" or ref.startswith("A1:")):
                    continue
                qname = None
                for typ2, qt_part in _part_rels(z, names, table_part).values():
                    if typ2.endswith("/queryTable") and qt_part in names:
                        try:
                            qt = ET.fromstring(z.read(qt_part))
                        except (ET.ParseError, KeyError, OSError):
                            continue
                        qname = connections.get(qt.get("connectionId") or "")
                out[sheet_name] = qname
    return out


def _query_loaded(entry: dict[str, Any], package: Any, qtables: dict[str, str | None] | None = None) -> bool:
    """The raw sheet is loaded by a query: the workbook holds a query table at A1 of the sheet (query_tables),
    whatever the manifest says (v3, C-sol-7). v5 ([A-aera-2, C-sol-1, C-sol-4] part 1): what the manifest says
    alone no longer counts (_query_claimed), so the report's query_loaded is never true where query_table is
    false. `package` is kept for the callers' signature."""
    return bool(qtables) and (entry.get("name") or "") in (qtables or {})


def _query_claimed(entry: dict[str, Any], package: Any, qtables: dict[str, str | None] | None = None) -> bool:
    """The manifest or the workbook says a query loads the raw sheet: a live query table (_query_loaded), or the
    manifest records landing power_query, or names a query the package holds (the v4 meaning of _query_loaded).
    It chooses the words of a finding and the refresh-state refusal; rows are accepted only from a live table."""
    if _query_loaded(entry, package, qtables) or entry.get("landing") == "power_query":
        return True
    qn = entry.get("query_name")
    return bool(qn and package is not None and qn in (getattr(package, "query_names", None) or []))


def _pq_without_table(raw_entries: dict[str, dict[str, Any]], qtables: dict[str, str | None]) -> list[str]:
    """v5 ([A-aera-2, C-sol-1, C-sol-4] part 1): the raw tables whose manifest entry records landing power_query
    while the workbook holds no query table at A1 of their sheet (X-NO-QUERY per table)."""
    return [t for t, e in raw_entries.items()
            if e.get("landing") == "power_query" and (e.get("name") or "") not in qtables]


def _rotm_attached(entry: dict[str, Any], qtables: dict[str, str | None]) -> bool:
    """rotm.py attached the query at A1 of the raw sheet (v4 plan 5.3): the raw entry has landing power_query, a
    query_contract_sha256, query_attach other than "hand", and query_name equal to the name query_tables gives."""
    sheet = entry.get("name") or ""
    return (entry.get("landing") == "power_query" and bool(entry.get("query_contract_sha256"))
            and entry.get("query_attach") != "hand" and sheet in qtables
            and entry.get("query_name") == qtables.get(sheet))


def _hand_attached(entry: dict[str, Any], qtables: dict[str, str | None]) -> bool:
    """A query no rotm.py command attached (v4 plan 5.3, the rule build applies too): (a) the raw entry records
    query_attach "hand", or (b) query_tables names the raw sheet and the manifest does not record that rotm.py
    attached it (_rotm_attached)."""
    if entry.get("query_attach") == "hand":
        return True
    return (entry.get("name") or "") in qtables and not _rotm_attached(entry, qtables)


def _hand_fields(entry: dict[str, Any], qtables: dict[str, str | None]) -> dict[str, Any]:
    """The raw entry's fields for a query attached by hand (v3, C-sol-7; v4, C-sol-6): landing power_query,
    query_attach hand, the query's name, no contract hash."""
    return {"landing": "power_query", "query_attach": "hand", "query_contract_sha256": _DROP,
            "query_name": qtables.get(entry.get("name") or "") or entry.get("query_name")}


def _needs_fields(entry: dict[str, Any], fields: dict[str, Any]) -> bool:
    return any((key in entry) if value is _DROP else entry.get(key, _DROP) != value for key, value in fields.items())


def _stale_queries(root: Path, raw_entries: dict[str, dict[str, Any]],
                   contracts: dict[str, dict[str, Any] | None],
                   qtables: dict[str, str | None] | None = None) -> tuple[list[str], list[str]]:
    """(refusals, notes) for `check --refresh` (v2, A-aera-2): a raw table loaded by Power Query whose query was
    written for another contract than the agreed one. The query's contract hash: the manifest raw entry's
    query_contract_sha256 (written when the query was attached), else the stamp of queries/<t>.pq.m
    (common.read_query_stamp). v3, C-sol-7: a query attached by hand records no contract hash; it gets the note
    N-HAND-QUERY and is not compared."""
    refusals: list[str] = []
    notes: list[str] = []
    qtables = qtables or {}
    for t, entry in raw_entries.items():
        if _hand_attached(entry, qtables):
            name = entry.get("query_name") if entry.get("query_attach") == "hand" else None
            name = name or qtables.get(entry.get("name") or "") or "(name not found)"
            notes.append(f"--refresh: raw_{t}'s query {name} was attached by hand; no contract hash is recorded for "
                         "it, so it was not compared with the agreed contract")
            continue
        if entry.get("landing") != "power_query":
            continue
        agreed = (((contracts.get(t) or {}).get("agreement") or {}).get("contract_sha256"))
        recorded = entry.get("query_contract_sha256")
        where = "the manifest (query_contract_sha256)"
        if not recorded:
            qfile = root / "queries" / f"{t}.pq.m"
            stamp = None
            if qfile.is_file():
                try:
                    stamp = common.read_query_stamp(qfile.read_text(encoding="utf-8-sig"))
                except (OSError, UnicodeDecodeError):
                    stamp = None
            recorded = (stamp or {}).get("contract_sha256")
            where = f"the stamp of queries/{t}.pq.m"
        if not recorded:
            notes.append(f"--refresh: raw_{t}'s query records no contract hash (neither in the manifest nor in "
                         f"queries/{t}.pq.m), so it was not compared with the agreed contract")
            continue
        if agreed and recorded != agreed:
            refusals.append(f"raw_{t}: its Power Query query was written for another contract ({where}: "
                            f"sha256:{recorded[:12]}...) than the agreed contracts/{t}.contract.json "
                            f"(sha256:{agreed[:12]}...); a refresh would load rows shaped by the old contract; fix: "
                            f"{_rotm()} land \"{root.as_posix()}\" --table {t} (writes the query for the agreed "
                            f"contract), then {_rotm()} land \"{root.as_posix()}\" --table {t} --power-query")
    return refusals, notes


def _query_body_sha256(text: str) -> str:
    """The hash of the body of a query file, as common.stamp_query hashes it: the text with LF line ends and
    without its rotm-stamp line, UTF-8 (v5 plan 5.3). One function for the attach and the refresh: this calls
    land.query_body_sha256, which wrote the hash at the attach."""
    from .land import query_body_sha256
    return query_body_sha256(text)


def _changed_query_files(root: Path, raw_entries: dict[str, dict[str, Any]],
                         qtables: dict[str, str | None] | None = None) -> tuple[list[str], list[str]]:
    """(refusals, notes) for `check --refresh` (v5, [A-aera-2, C-sol-1, C-sol-4] part 2): a raw table loaded by a
    query rotm.py attached, whose queries/<t>.pq.m no longer has the body hash the attach recorded in the manifest
    (query_body_sha256): a refresh would run the query the workbook holds, not the file. A query attached by hand
    is not compared (its note comes from _stale_queries); an entry without the field gets a note only."""
    refusals: list[str] = []
    notes: list[str] = []
    qtables = qtables or {}
    model = root.as_posix()
    for t, entry in raw_entries.items():
        if entry.get("landing") != "power_query" or _hand_attached(entry, qtables):
            continue
        attach = f"{_rotm()} land \"{model}\" --table {t} --power-query"
        recorded = entry.get("query_body_sha256")
        if not recorded:
            notes.append(f"--refresh: raw_{t}'s manifest records no hash of the attached query text (attached before "
                         f"v5), so queries/{t}.pq.m was not compared with the workbook's query; {attach} attaches it "
                         "again and records the hash")
            continue
        qfile = root / "queries" / f"{t}.pq.m"
        if not qfile.is_file():
            refusals.append(f"raw_{t}: queries/{t}.pq.m is missing, so it cannot be compared with the query the "
                            f"workbook holds; fix: {attach}")
            continue
        try:
            now = _query_body_sha256(qfile.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            refusals.append(f"raw_{t}: queries/{t}.pq.m cannot be read ({type(exc).__name__}), so it cannot be "
                            f"compared with the query the workbook holds; fix: {attach}")
            continue
        if now != str(recorded).lower():
            refusals.append(f"raw_{t}: queries/{t}.pq.m differs from the query text attached to the workbook "
                            f"(manifest query_body_sha256 sha256:{str(recorded)[:12]}..., the file now "
                            f"sha256:{now[:12]}...); a refresh would run the query the workbook holds, not the file; "
                            f"fix: {attach} (attaches the file's query)")
    return refusals, notes


def _formula_hash_notes(root: Path, raw_entries: dict[str, dict[str, Any]],
                        qtables: dict[str, str | None] | None = None, option: str = "--refresh") -> list[str]:
    """Notes for `check --refresh` (v6, r5 verdict A-aera-2): a raw table loaded by a query rotm.py attached whose
    manifest entry records no query_formula_sha256 (attached before v6, or the formula was not read back): the
    workbook's query is not compared. A query attached by hand without the field keeps the note of _stale_queries.
    v7 (r6 verdict A-aera-2): `option` "--accept-refresh" for an acceptance given without --refresh."""
    notes: list[str] = []
    qtables = qtables or {}
    model = root.as_posix()
    for t, entry in raw_entries.items():
        if entry.get("landing") != "power_query" or entry.get("query_formula_sha256") or _hand_attached(entry, qtables):
            continue
        notes.append(f"{option}: raw_{t}'s manifest records no hash of the query formula the workbook holds "
                     "(attached before v6), so the workbook's query was not compared with the attached text; "
                     f"{_rotm()} land \"{model}\" --table {t} --power-query attaches it again and records the hash")
    return notes


def _formula_tables(raw_entries: dict[str, dict[str, Any]], qtables: dict[str, str | None],
                    no_table: Sequence[str] = ()) -> list[str]:
    """The raw tables whose query formula check --refresh compares (v6, r5 verdict A-aera-2): the manifest entry
    records query_formula_sha256 (written when rotm.py attached the query or carried a query attached by hand over a
    rebuild) and the workbook holds a live query table at A1 of the raw sheet (query_tables). v9 (r8 verdict
    C-sol-3): also an entry that records query_reads (the queries a query attached by hand reads, carried over by
    build), so that each of them is compared."""
    return [t for t, e in raw_entries.items()
            if (e.get("query_formula_sha256") or e.get("query_reads")) and (e.get("name") or "") in qtables
            and t not in no_table]


def _query_formula_findings(root: Path, wb: Path, raw_entries: dict[str, dict[str, Any]],
                            qtables: dict[str, str | None], tables: Sequence[str],
                            accept_refresh: bool) -> list[common.Finding]:
    """X-QUERY-CHANGED (v6, r5 verdict A-aera-2): for each table of `tables` (_formula_tables), the formula of the
    query bound to the table at A1 of its raw sheet, read from `wb` through Excel (lib.excel.inventory, read-only),
    hashed with land.query_formula_sha256 and compared with the manifest's query_formula_sha256. A different hash,
    or a formula Excel does not give, is a warning (an error with --accept-refresh). Raises common.ExcelBusy when the
    Excel lock is not free; an inventory that fails counts as a formula that cannot be read.

    v9 (r8 verdict C-sol-3): with Excel, check --refresh and check --accept-refresh also compare each query that a
    query attached by hand reads (the connection-only queries build carried over, recorded in the manifest as
    query_reads) with the hash build recorded: a changed, missing or unreadable one is X-QUERY-CHANGED and names that
    query; the table is not refreshed, nothing is accepted, and raw/<t>.csv, raw/<t>.controls.json and the manifest
    stay byte for byte. A query read whose formula build could not read back (formula_sha256 null) is not compared
    (a note says so)."""
    from . import excel as xl
    from .land import query_formula_sha256
    try:
        inv = xl.inventory(wb)
    except common.ExcelBusy:
        raise
    except common.RotmError:
        inv = {}
    formulas: dict[str, Any] = {}
    for q in inv.get("query_formulas") or []:
        if isinstance(q, dict) and q.get("name"):
            formulas[str(q["name"])] = q.get("formula")
    sheets = {str(s.get("name")): s for s in inv.get("sheets") or [] if isinstance(s, dict)}
    model = root.as_posix()
    out: list[common.Finding] = []
    for t in tables:
        entry = raw_entries[t]
        sheet = entry.get("name") or f"raw_{t}"
        out += _query_read_findings(entry, t, sheet, formulas, bool(inv), model, accept_refresh)
        if not entry.get("query_formula_sha256"):
            continue                                     # v9: only its query_reads are compared
        qname = qtables.get(sheet)
        if not qname:
            # else the connection name of the query table at A1 of the sheet, without 'Query - '
            at_a1 = [x for x in (sheets.get(sheet) or {}).get("tables") or []
                     if isinstance(x, dict) and x.get("query")
                     and re.match(r"^A1(:|$)", str(x.get("range") or "").replace("$", "").upper())]
            if at_a1 and at_a1[0].get("connection"):
                qname = re.sub(r"^Query - ", "", str(at_a1[0]["connection"])).strip() or None
        shown = qname or "(name not found)"
        text = formulas.get(qname or "")
        recorded = str(entry.get("query_formula_sha256") or "").lower()
        if entry.get("query_attach") == "hand":
            fix = (f"the query was attached by hand and carried over by rotm.py build: to keep the change, rebuild "
                   f"({_rotm()} build \"{model}\" --force; build reads the query from the workbook, attaches it again "
                   "and records its formula); to drop the change, change the query back in Excel's Power Query editor")
        else:
            fix = (f"put the change into queries/{t}.pq.m and run {_rotm()} land \"{model}\" --table {t} "
                   "--power-query (it attaches the file's query); to drop the change, run that command as it is")
        if not isinstance(text, str) or not text.strip():
            msg = (f"{sheet}: the formula of the query {shown} could not be read back from the workbook, so it could "
                   "not be compared with the query rotm.py attached; no table was refreshed and nothing was accepted "
                   f"for {sheet}")
            now = None
        else:
            now = query_formula_sha256(text)
            if now == recorded:
                continue
            msg = (f"{sheet}: the query {shown} that the workbook holds differs from the query rotm.py attached "
                   f"(manifest query_formula_sha256 sha256:{recorded[:12]}..., the workbook now sha256:{now[:12]}...); "
                   "it was changed outside rotm.py, for example in Excel's Power Query editor; no table was refreshed "
                   f"and nothing was accepted for {sheet}")
        out.append(common.Finding("X-QUERY-CHANGED", "error" if accept_refresh else "warning", msg, fix,
                                  sheet=sheet, cell="A1",
                                  data={"table": t, "query": qname, "recorded": recorded, "workbook": now}))
    return out


def _query_read_findings(entry: dict[str, Any], t: str, sheet: str, formulas: dict[str, Any], inventory_read: bool,
                         model: str, accept_refresh: bool) -> list[common.Finding]:
    """X-QUERY-CHANGED for the queries a query attached by hand reads (v9, r8 verdict C-sol-3): each item of the raw
    entry's query_reads ({"name", "formula_sha256", "file"}) whose formula in the workbook (`formulas`, from
    lib.excel.inventory; `inventory_read` False when the inventory failed) differs from the recorded hash, is missing
    from the workbook, or cannot be read. A warning; an error with --accept-refresh."""
    from .land import query_formula_sha256
    out: list[common.Finding] = []
    q = entry.get("query_name") or "(name not found)"
    for item in entry.get("query_reads") or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        dep = str(item["name"])
        recorded = str(item.get("formula_sha256") or "").lower()
        if not recorded:
            continue                                     # not read back at the build: not compared (build's note)
        text = formulas.get(dep)
        now = None
        if not inventory_read or (dep in formulas and (not isinstance(text, str) or not text.strip())):
            how = "cannot be read"
        elif dep not in formulas:
            how = "is missing from the workbook"
        else:
            now = query_formula_sha256(text)
            if now == recorded:
                continue
            how = (f"differs in the workbook from the formula build recorded (manifest query_reads "
                   f"sha256:{recorded[:12]}..., the workbook now sha256:{now[:12]}...)")
        file = item.get("file") or f"queries/{dep}.hand.pq.m"
        out.append(common.Finding(
            "X-QUERY-CHANGED", "error" if accept_refresh else "warning",
            f"{sheet}: the query {dep}, which the query {q} attached by hand reads, {how}; the table was not "
            "refreshed and nothing was accepted",
            f"review the change of {dep} with the user; to keep it, rebuild ({_rotm()} build \"{model}\" --force), "
            f"which records it, then refresh and accept; to undo it, put back its M text from {file}",
            sheet=sheet, cell="A1",
            data={"table": t, "query": dep, "read_by": q, "recorded": recorded, "workbook": now}))
    return out


def _not_compared_notes(root: Path, raw_entries: dict[str, dict[str, Any]], tables: Sequence[str],
                        why_not: str) -> list[str]:
    """v7 (r6 verdict A-aera-2): notes for check --accept-refresh given without --refresh when Excel did not read
    the query formula back (Excel missing, --no-excel): one per table whose formula the run would have compared
    (_formula_tables)."""
    model = root.as_posix()
    out = []
    for t in tables:
        sheet = raw_entries[t].get("name") or f"raw_{t}"
        out.append(f"--accept-refresh: the query of {sheet} was not read back from the workbook and compared with the "
                   f"query rotm.py attached, because {why_not}; a query changed outside rotm.py (for example in "
                   f"Excel's Power Query editor) is not found in this run; with Excel, {_rotm()} check \"{model}\" "
                   "--refresh --strict reads it back and compares it, and loads the rows from the source again")
    return out


# ----------------------------------------------------------------------------- functions only Google Sheets has

SHEETS_ONLY_REASON = "functions only Google Sheets has (X-SHEETS-ONLY)"
_TEXT_LITERAL_RE = re.compile(r'"(?:[^"]|"")*"?')


def _sheets_only_pattern() -> re.Pattern[str]:
    """A call of a function only Google Sheets has: its name (any case, also after the prefixes _xlfn., _xlws. or
    _xludf. that a file may store), then an opening bracket."""
    from . import functions
    names = sorted(functions.sheets_only_names(), key=len, reverse=True)
    return re.compile(r"(?<![A-Za-z0-9_.])(?:_xlfn\.|_xlws\.|_xludf\.)*(" + "|".join(re.escape(n) for n in names)
                      + r")\s*\(", re.I)


def _called_sheets_only(text: str, wanted: set[str], pattern: re.Pattern[str]) -> list[str]:
    """The functions of `wanted` that the formula `text` calls (lib.formula's analysis; a name inside a text
    literal is not a call)."""
    from . import formula, functions
    if not pattern.search(_TEXT_LITERAL_RE.sub('""', text)):
        return []
    called = []
    for name in formula.analyze(text).functions:
        n = functions.normalize_name(re.sub(r"^_XLUDF\.", "", name, flags=re.I))
        if n in wanted and n not in called:
            called.append(n)
    return called


def sheets_only_cells(workbook: str | Path) -> dict[str, dict[str, Any]]:
    """v7 (r6 verdict A-aera-8): the functions only Google Sheets has (functions.sheets_only_names(): Excel "no",
    Sheets "yes") that the workbook's cell formulas call, by name in the catalogue's order: {"count": formula cells,
    "first": "<sheet>!<cell>"} (sheets in workbook order, then row by row). The worksheet parts are first searched
    as bytes for such a name followed by a bracket; only when one is found are the formulas read (openpyxl,
    read-only, formulas as text) and analysed with lib.formula. Never writes."""
    import warnings
    import zipfile

    import openpyxl
    from . import functions
    wanted = set(functions.sheets_only_names())
    pattern = _sheets_only_pattern()
    path = Path(common.native_path(workbook))
    try:
        with zipfile.ZipFile(path) as z:
            parts = [n for n in z.namelist() if n.startswith("xl/worksheets/") and n.endswith(".xml")]
            raw_pattern = re.compile(pattern.pattern.encode("ascii"), re.I)
            if parts and not any(raw_pattern.search(z.read(n)) for n in parts):
                return {}
    except (OSError, zipfile.BadZipFile, KeyError):
        pass                                     # openpyxl reads it below, or the file cannot be read at all
    found: dict[str, dict[str, Any]] = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            book = openpyxl.load_workbook(path, read_only=True, data_only=False, keep_links=False)
        except Exception:  # noqa: BLE001 - an unreadable workbook is reported by the steps that read it
            return {}
        try:
            for ws in book.worksheets:
                try:
                    ws.reset_dimensions()
                except AttributeError:
                    pass
                for row in ws.iter_rows():
                    for c in row:
                        v = getattr(c, "value", None)
                        if isinstance(v, str):
                            if getattr(c, "data_type", None) != "f" or not v.startswith("="):
                                continue
                            text = v
                        elif v is not None and getattr(v, "text", None) is not None:      # ArrayFormula
                            text = "=" + str(v.text).lstrip("=")
                        else:
                            continue
                        for n in _called_sheets_only(text, wanted, pattern):
                            entry = found.setdefault(n, {"count": 0, "first": f"{common.quote_sheet(ws.title)}!"
                                                                              f"{c.coordinate}"})
                            entry["count"] += 1
        finally:
            book.close()
    order = functions.sheets_only_names()
    return {n: found[n] for n in sorted(found, key=order.index)}


def sheets_only_finding(found: dict[str, dict[str, Any]]) -> common.Finding:
    """X-SHEETS-ONLY (information, v7 r6 verdict A-aera-8) for the result of sheets_only_cells()."""
    from . import functions
    parts = [f"{n} ({_n(v['count'])} cell(s), first {v['first']})" for n, v in found.items()]
    instead = "; ".join(f"for {n} {functions.replacement(n) or 'a formula without it'}" for n in found)
    first = next(iter(found.values()))["first"]
    sheet, cell = common.parse_ref(first)
    return common.Finding(
        "X-SHEETS-ONLY", "info",
        "the workbook holds functions only Google Sheets has: " + "; ".join(parts) + "; Excel cannot calculate them, "
        "so the workbook was not opened in Excel and not recalculated",
        f"use the replacement references/functions.md names ({instead}) to verify the model in Excel; otherwise "
        "report 'audited, not recalculated: functions only Google Sheets has'",
        sheet=sheet, cell=cell, data={"functions": {n: dict(v) for n, v in found.items()}})


def check_model(target: str | Path, *, strict: bool = False, excel: bool = True, refresh: bool = False,
                accept_refresh: bool = False, controls: dict[str, Any] | None = None,
                control_texts: Sequence[str] | None = None) -> common.Report:
    """`target` is a model folder or a workbook inside one. Returns a common.Report (data: every check id with
    actual, expected and result; master; error cells; raw tables; coverage per table). Records the step "check" in
    reports/status.json and writes reports/check.json. `control_texts` are the --control texts as given (kept in
    raw/<t>.controls.json when a refresh is accepted)."""
    from . import workbook as wbmod
    model = _resolve(target)
    root, wb, man = model.root, model.workbook, model.manifest
    report = common.Report("check", wb.as_posix(), strict=strict)
    raw_entries = {e["table"]: e for e in man.get("sheets", []) if e.get("type") == "raw"}
    tables = list(raw_entries)
    contracts: dict[str, dict[str, Any] | None] = {}
    states: dict[str, str] = {}
    for t in tables:
        state, data, problems = modelfolder.read_contract(root, t)
        states[t] = state
        contracts[t] = data if state != "invalid" else None
        cmd = (f"show the user contracts/{t}.contract.md and record their reply with {_rotm()} contract agree "
               f"\"{root.as_posix()}\" --table {t} --reply \"<their words>\" --by <name>")
        if state == "missing":
            report.error("CT-STATE", f"raw table '{t}' has no contract (contracts/{t}.contract.json)",
                         f"propose one with rotm.py contract propose \"{root.as_posix()}\" --table {t} --csv <sample>, "
                         "then " + cmd)
        elif state == "invalid":
            report.error("CT-STATE", f"the contract of '{t}' is not valid: " + "; ".join(problems[:3]),
                         f"correct it, or propose it again with rotm.py contract propose --force; then {cmd}")
        elif state == "proposed":
            report.error("CT-STATE", f"the contract of '{t}' is proposed, not agreed", cmd)
        elif state == "stale":
            report.error("CT-STATE", f"the contract of '{t}' was changed after it was agreed; it counts as not agreed",
                         cmd)
        elif (data or {}).get("agreement", {}).get("channel") == "example":
            from . import spec as spec_mod
            if not spec_mod.example_channel_allowed(root):
                states[t] = "example"
                report.error("CT-STATE", f"the contract of '{t}' carries the 'example' agreement of a shipped "
                             "example or template; outside the plugin it counts as not agreed", cmd)
    ctl = _controls_by_table(controls, contracts, tables)
    if accept_refresh:
        # v9 (r8 verdict A-aera-4): before Excel starts and before anything is recorded, --control is accepted only
        # for a column whose control total the contract names (a control total is part of the agreement)
        from . import land as land_mod
        for t, cols in ctl.items():
            if contracts.get(t) is None:
                continue
            for col, value in cols.items():
                item = next((str(x).strip() for x in control_texts or []
                             if str(x).strip().startswith(f"{t}.{col}=")),
                            f"{t}.{col}={value[0] if isinstance(value, (tuple, list)) else value}")
                land_mod.refuse_uncontracted_control(item, t, col, contracts[t], model=root.as_posix(),
                                                     done="recorded")
    given_by_table: dict[str, list[str]] = {}
    for text in (control_texts if control_texts is not None
                 else [f"{k}={v[0] if isinstance(v, (tuple, list)) else v}" for k, v in (controls or {}).items()]):
        given_by_table.setdefault(str(text).strip().split(".", 1)[0], []).append(str(text).strip())
    try:
        package = wbmod.package_parts(wb)
    except common.RotmError:
        package = None
    qtables = query_tables(wb)
    loaded = {t: _query_loaded(e, package, qtables) for t, e in raw_entries.items()}
    claimed = {t: _query_claimed(e, package, qtables) for t, e in raw_entries.items()}
    has_query = bool(package is not None and (package.queries or package.connections)) or any(loaded.values())
    # v5 ([A-aera-2, C-sol-1, C-sol-4] part 1): tables the manifest records as loaded by Power Query whose sheet
    # holds no query table: X-NO-QUERY each, not refreshed, nothing accepted or recorded for them
    no_table = _pq_without_table(raw_entries, qtables) if refresh else []
    if refresh:
        # v3, A-aera-5 and C-sol-2 (F-REFRESH-STATE): before Excel starts, a refresh of a table whose contract is
        # not agreed is refused, so the refused run leaves the workbook as it was
        _refuse_refresh_states(root, tables, claimed, states)
        stale, stale_notes = _stale_queries(root, raw_entries, contracts, qtables)
        if stale:
            raise common.Refusal(f"--refresh refused: {len(stale)} raw table{'s' if len(stale) != 1 else ''} would "
                                 "be refreshed by a query written for another contract; nothing was refreshed",
                                 details=stale, fix="write the queries for the agreed contracts again (the lines "
                                                    "below name each table and the commands)")
        # v5 ([A-aera-2, C-sol-1, C-sol-4] part 2): before Excel starts, the query file against the text attached
        changed, changed_notes = _changed_query_files(root, raw_entries, qtables)
        if changed:
            raise common.Refusal(f"--refresh refused: {len(changed)} raw table{'s' if len(changed) != 1 else ''} "
                                 "would be refreshed by a query other than its file queries/<t>.pq.m; nothing was "
                                 "refreshed", details=changed,
                                 fix="attach the query files again with land --power-query (the lines below name each "
                                     "table and the command)")
        for note in stale_notes + changed_notes + _formula_hash_notes(root, raw_entries, qtables):
            report.note(note)
    versioned = _contract_versions(report, root, raw_entries, contracts, states)
    input_sha = common.file_sha256(wb)
    master_ref, check_entries, details = _check_layout(man)
    read_refs = ([master_ref] if master_ref else []) + [c["result"] for c in check_entries] + list(details.values())
    values: dict[str, Any] = {}
    error_cells: list[list[str]] = []
    error_count = 0
    recalculated = refreshed = False
    excel_pid = None
    why_not = ""
    not_opened = False                  # v4 (C-sol-3): Excel ran and could not open or calculate the workbook
    add_no_query = report.error if accept_refresh else report.warning
    for t in no_table:
        # v5 ([A-aera-2, C-sol-1, C-sol-4] part 1, X-NO-QUERY for one table): a warning; an error with --accept-refresh
        sheet = raw_entries[t].get("name") or f"raw_{t}"
        add_no_query("X-NO-QUERY", f"{sheet}: the manifest records that a Power Query query loads it, but the workbook "
                     f"holds no query table at A1 of {sheet}; it was not refreshed and nothing was accepted for it",
                     f"attach the query again with {_rotm()} land \"{root.as_posix()}\" --table {t} --power-query, "
                     f"then run {_rotm()} check \"{root.as_posix()}\" --refresh", sheet=sheet,
                     data={"table": t})
    if refresh and not has_query and not no_table:
        # v3, A-aera-12 part 5 (X-NO-QUERY): a warning (strict fails); an error with --accept-refresh
        add_no_query("X-NO-QUERY", "--refresh was asked for, but the workbook has no Power Query query or connection: "
                     "the raw sheets are static snapshots and nothing was refreshed",
                     f"land new rows ({_rotm()} land \"{root.as_posix()}\" --table <t> --csv <file> --force) and "
                     f"rebuild, or attach a query ({_rotm()} land \"{root.as_posix()}\" --table <t> --power-query)")
    # v7 (r6 verdict [A-aera-2, C-sol-5]): an acceptance given without --refresh compares the query formula too
    standalone_accept = accept_refresh and not refresh
    compare = _formula_tables(raw_entries, qtables, no_table) if (refresh or accept_refresh) and has_query else []
    query_changed = compared = False
    if standalone_accept:
        for note in _formula_hash_notes(root, raw_entries, qtables, option="--accept-refresh"):
            report.note(note)
    # v7 (r6 verdict A-aera-8, X-SHEETS-ONLY): before Excel starts, the functions only Google Sheets has; with any,
    # no Excel job runs (no refresh, no recalculation, no save) and the run ends as when Excel is missing
    sheets_only = sheets_only_cells(wb)
    if sheets_only:
        report.add(sheets_only_finding(sheets_only))
        why_not = SHEETS_ONLY_REASON
    elif not excel:
        why_not = "--no-excel was given"
    else:
        from . import excel as xl
        status = xl.excel_status()
        if not status.available:
            why_not = f"Excel is not available ({status.reason})"
        else:
            ops: list[dict[str, Any]] = []
            # v6 (r5 verdict A-aera-2): before the refresh, the formula of each query rotm.py attached (the raw
            # entry records query_formula_sha256 and the sheet holds a live query table) is read through Excel and
            # compared; on a difference (X-QUERY-CHANGED) no table is refreshed and nothing is accepted. The Excel
            # lock is held over the read and the main job, so no other job runs on the workbook between them.
            # v7 (r6 verdict A-aera-2): also for --accept-refresh without --refresh (`compare` above).
            res = None
            with (xl.excel_lock() if compare else contextlib.nullcontext()):
                if compare:
                    found = _query_formula_findings(root, wb, raw_entries, qtables, compare, accept_refresh)
                    report.extend(found)
                    compared = True
                    query_changed = bool(found)
                    if query_changed and refresh:
                        why_not = ("a query the workbook holds differs from the query rotm.py attached, or its "
                                   "formula could not be read (X-QUERY-CHANGED)")
                if refresh and has_query and not query_changed:
                    ops.append({"op": "pq_refresh", "wait_s": REFRESH_WAIT_S})
                try:
                    res, values, error_cells, error_count = _excel_job(
                        wb, ops, read_refs, timeout_s=max(xl._timeout(), REFRESH_WAIT_S + 120) if refresh else None)
                except common.ExcelBusy:
                    raise
                except common.EnvUnavailable as exc:
                    why_not = exc.message
                    res = None
            if res is not None:
                excel_pid = res.pid
                if res.ok:
                    recalculated = True
                    # v5 ([A-aera-2, C-sol-1, C-sol-4] part 1): refreshed only when the refresh step reports at
                    # least one table it refreshed
                    asked = any(o["op"] == "pq_refresh" for o in ops)
                    done = [x for x in (res.step("pq_refresh").get("tables") or []) if x.get("ok")] if asked else []
                    refreshed = bool(done)
                    for tinfo in done:
                        report.note(f"refreshed {tinfo.get('sheet')}!{tinfo.get('table')}: "
                                    f"{_n(tinfo.get('rows'))} rows ({tinfo.get('range')})")
                    if asked and not done:
                        why_not = "the refresh step refreshed no query table"
                    report.note(f"recalculated in Excel {res.excel_version or ''} and saved; Excel PID {res.pid} "
                                f"{'was stopped after Quit' if res.killed else 'closed'}".replace("  ", " "))
                else:
                    step = res.step("pq_refresh")
                    if step and not step.get("ok", True):
                        bad = [f"{t.get('sheet')}!{t.get('table')}: {t.get('problem')}"
                               for t in step.get("tables") or [] if not t.get("ok")]
                        report.error("X-REFRESH", "the Power Query refresh failed: "
                                     + ("; ".join(bad) if bad else res.message()) + "; nothing was saved",
                                     "if the source needs credentials Excel has not stored, refresh in Excel by hand, "
                                     "save, and run rotm.py check without --refresh")
                        why_not = "the refresh failed"
                    elif res.attached:
                        why_not = f"the Excel job failed: {res.message()}"
                    else:
                        # v4 (C-sol-3, X-OPEN): Excel started and could not open or calculate the file (a timeout
                        # too); the most likely cause is a formula Excel rejects. An error (exit 1), not "Excel not
                        # available" (exit 3).
                        # v5 ([A-aera-8, C-sol-8] item 3): the finding of excel.x_open_finding, which names the
                        # workbook by its file name and keeps Excel's reason in full whatever the path's length
                        not_opened = True
                        why_not = "Excel could not open the workbook"
                        report.add(xl.x_open_finding(wb, list(res.errors) or [res.message()], root))
                        report.note("Excel could not open the workbook; the check cells were not read and nothing "
                                    "was saved")
    # v3, A-aera-1 (4): when --refresh was asked for and no refresh ran (Excel missing or busy, the job or the
    # refresh failed, no query), no table is accepted and raw/, the controls files and the manifest stay as they are
    refresh_missed = refresh and not refreshed
    if refresh_missed:
        reason = (why_not if sheets_only else "the workbook has no query" if not has_query
                  else (why_not or "the refresh did not run"))
        report.note(f"no table was accepted: --refresh was asked for and no refresh ran ({reason}); raw/, the "
                    "controls files and the manifest were not changed")
    # v7: an acceptance without --refresh accepts nothing when the query formula differs (X-QUERY-CHANGED) or the
    # workbook was not opened in Excel because of a function only Google Sheets has (X-SHEETS-ONLY)
    accept_blocked = standalone_accept and (query_changed or bool(sheets_only))
    if accept_blocked:
        reason = ("the workbook holds functions only Google Sheets has (X-SHEETS-ONLY), so it was not opened in "
                  "Excel" if sheets_only else "a query the workbook holds differs from the query rotm.py attached, "
                  "or its formula could not be read (X-QUERY-CHANGED)")
        report.note(f"no table was accepted: {reason}; raw/, the controls files and the manifest were not changed")
    elif standalone_accept and compare and not compared:
        # without Excel (missing, or --no-excel) the query formula was not read back
        for note in _not_compared_notes(root, raw_entries, compare, why_not or "Excel did not run"):
            report.note(note)
    if not_opened:
        values, any_cached = {}, False           # X-OPEN: the check cells were not read
    elif not recalculated:
        lead = "audited, not recalculated" if sheets_only else "audited but not recalculated"
        report.note(f"{lead}: {why_not}. Raw sheets, contracts and coverage were checked by "
                    "code; the check cells were not recalculated, so their values are not verified")
        values, any_cached = _cached_values(wb, read_refs) if read_refs else ({}, False)
        if not any_cached:
            report.note("the workbook holds no cached values for its check cells (not recalculated since it was "
                        "written)")
    else:
        any_cached = True
    total_refs = [_expected_ref(c["result"]) for c in check_entries
                  if any(c["id"].startswith(f"{t}_total_") for t in tables)]
    # v8 (r7 verdict [A-aera-5, A-aera-6] part 1, V-INPUT-RANGE): the scalar inputs that declare "valid"
    spec_data, spec_note = read_spec_data(root, man)
    range_refs = [ref for _id, _item, ref in input_range_refs(spec_data, man)]
    facts = _read_facts(wb, man, formula_refs=total_refs + range_refs)
    if spec_note and man.get("inputs"):
        report.note(spec_note)
    if range_refs:
        # the saved values: after a completed recalculation the values Excel saved, else the file as it is
        saved = _cached_values(wb, range_refs)[0]

        def read_saved(ref: str) -> tuple[bool, Any]:
            if ref not in saved or (saved[ref] is None and ref in facts.formulas):
                return False, None               # a missing sheet, or a formula typed in with no saved value
            return True, saved[ref]

        spec_file = (root / str(man.get("spec") or "model.spec.json")).as_posix()
        report.extend(input_range_findings(spec_data, man, read_saved, f"{_rotm()} build \"{spec_file}\" --force"))
    # the input cells the total checks link (inputs!D9): their stored values are compared with the control used
    links = {_pure_link(facts.formulas.get(ref, "")) for ref in total_refs}
    input_refs = sorted(_quoted_ref(x) for x in links if x)
    input_values = _cached_values(wb, input_refs)[0] if input_refs else {}
    results = {c["id"]: c["result"] for c in check_entries}
    tables_data: dict[str, dict[str, Any]] = {}
    for t, entry in raw_entries.items():
        tables_data[t] = _check_table(report, model, t, entry, contracts.get(t), states.get(t, "missing"), facts,
                                      ctl.get(t, {}), accept_refresh and not refresh_missed and not accept_blocked,
                                      values, details, results, recalculated, package, given_by_table.get(t, []),
                                      qtables=qtables, input_values=input_values,
                                      may_write=not refresh_missed and not accept_blocked and t not in no_table,
                                      version_blocked=t in versioned)
    if refreshed:
        # v6 (r5 verdict C-sol-6, I-HIDDEN-DECIMALS): refreshed values with more decimals than the column's number
        # format shows; information only, no format is changed
        try:
            dictionary = _model_dictionary(root, man)
        except (common.RotmError, OSError, ValueError):
            dictionary = None
        for t, entry in raw_entries.items():
            raw = facts.raw.get(t)
            if dictionary is not None and raw is not None:
                report.extend(hidden_decimal_findings(root, t, entry, [str(h) for h in raw[0]], raw[1],
                                                      contracts.get(t), dictionary))
    rejob = _apply_control_inputs(report, wb, root, tables_data, read_refs)
    if rejob is not None:
        values, error_cells, error_count = rejob
    # v4 (A-aera-1, D-CRITERIA): labels that Excel's criteria matching merges, on the raw sheets' rows; after a
    # completed recalculation also on the values Excel saved in the other helper columns
    from . import criteria
    report.extend(criteria.criteria_findings(wb, man, saved_values=recalculated))
    # v5 ([A-aera-3, C-sol-5] parts 2 and 3, C-RECOMPUTE): after a completed recalculation, every check and the
    # master computed again from the saved values of their own operand cells (lib.recompute)
    if recalculated:
        from . import recompute
        report.extend(recompute.check_findings(wb, man))
    output_sha = common.file_sha256(wb)
    raw_names = {t: e["name"] for t, e in raw_entries.items()}
    cov_findings, cov_data = _coverage(facts, man, root, raw_names)
    report.extend(cov_findings)
    for t, info in cov_data.items():
        tables_data.setdefault(t, {})["coverage"] = info
    try:
        from .excel import CIRCULAR_TEXT
    except Exception:  # noqa: BLE001 - the text lib.excel's errors op writes for a cell Excel names as circular
        CIRCULAR_TEXT = "no value (circular reference)"
    for sheet, cell, text in error_cells:
        if str(text).startswith("no value"):
            # v2, A-aera-5: a formula cell Excel gave no value (P1's errors op: "no value (circular reference)"
            # for the cells Excel names as circular, "no value" for a formula saved without a value). v3
            # (A-aera-15): the cycle fix (C-CYCLE) only for a cell Excel names as circular; N-RECALC otherwise.
            ref = f"{common.quote_sheet(sheet)}!{cell}"
            if str(text).startswith(CIRCULAR_TEXT):
                fix = ("a formula that refers to itself through other cells has no value: find the cycle with "
                       f"{_rotm()} trace \"{wb.as_posix()}\" {ref} and break it")
            else:
                fix = ("Excel saved this formula without a value and named no circular reference for it: recalculate "
                       f"the workbook ({_rotm()} check \"{root.as_posix()}\") and look at the formula and its inputs "
                       f"({_rotm()} trace \"{wb.as_posix()}\" {ref})")
            report.error("V-NO-VALUE", f"{text}: the formula has no value after recalculation", fix,
                         sheet=sheet, cell=cell)
            continue
        report.error("V-ERROR", f"error value {text}", "trace the cell (rotm.py trace) and correct the formula or its "
                     "inputs; an error inside a check stays visible on purpose", sheet=sheet, cell=cell)
    if error_count > len(error_cells):
        report.note(f"{_n(error_count)} error cells in all; the first {_n(len(error_cells))} are listed")
    summary = _report_checks(report, man, root, values, recalculated, any_cached)
    report.note(summary["line"])
    report.verified["recalculated"] = recalculated
    report.data.update(recalculated=recalculated, refreshed=refreshed, input_sha256=input_sha,
                       output_sha256=output_sha, excel_pid=excel_pid, checks=summary["checks"],
                       master=summary["master"], error_cells=error_cells, error_count=error_count,
                       tables=tables_data)
    counts = report.counts()
    code = common.exit_code_for(counts["errors"], counts["warnings"], strict)
    if code == common.EXIT_OK and not recalculated:
        report.finish(strict, not_available=True)
    else:
        report.finish(strict)
    master_value = (summary["master"] or {}).get("value")
    entry = {"at": report.at, "exit_code": report.exit_code, "strict": strict, "input_sha256": input_sha,
             "output_sha256": output_sha, "recalculated": recalculated, "refreshed": refreshed,
             "checks": summary["error_checks"], "failed": summary["failed"] if recalculated else None,
             "alerts": summary["alerts"], "alerts_shown": summary["alerts_shown"] if recalculated else None,
             "master": int(master_value) if recalculated and isinstance(master_value, (int, float))
             and not isinstance(master_value, bool) and float(master_value).is_integer() else None,
             "errors": counts["errors"], "warnings": counts["warnings"], "line": summary["line"]}
    try:
        modelfolder.record_step(root, model.rel, "check", entry)
    except common.RotmError as exc:
        report.note(f"status not recorded in reports/status.json: {exc.line()}")
    try:
        report.write_json(root / "reports" / "check.json")
    except OSError as exc:
        report.note(f"reports/check.json not written: {exc}")
    return report


# ----------------------------------------------------------------------------- V-INPUT-RANGE (v8)

INPUT_RANGE_FIX = ("change the input's value in model.spec.json and rebuild ({rebuild}); a value typed or pasted "
                   "into the inputs sheet is not kept by a rebuild")


def read_spec_data(root: Path, man: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """The model's specification as JSON (the file the manifest names), and a note when it cannot be read."""
    path = Path(root) / str(man.get("spec") or "model.spec.json")
    try:
        data = common.read_json(path)
    except (OSError, ValueError) as exc:
        return None, (f"V-INPUT-RANGE: the specification {path.name} was not read ({type(exc).__name__}), so the "
                      "saved inputs were not compared with their valid ranges")
    return (data if isinstance(data, dict) else None), None


def input_range_refs(spec_data: dict[str, Any] | None, man: dict[str, Any]) -> list[tuple[str, dict[str, Any], str]]:
    """(input id, specification item, single-cell reference) of each scalar input of the specification that
    declares "valid" and whose cell the manifest records (manifest["inputs"])."""
    refs = man.get("inputs") or {}
    out: list[tuple[str, dict[str, Any], str]] = []
    sheets = (spec_data or {}).get("sheets")
    for s in sheets if isinstance(sheets, list) else []:
        if not isinstance(s, dict) or s.get("type") != "inputs":
            continue
        for item in s.get("items") or []:
            if not isinstance(item, dict) or item.get("kind") not in (None, "scalar"):
                continue
            ref = refs.get(item.get("id"))
            if not isinstance(item.get("valid"), dict) or not isinstance(ref, str) or ":" in ref:
                continue
            out.append((str(item["id"]), item, ref))
    return out


def _saved_value_text(v: Any) -> str:
    import datetime as dt

    from . import spec as spec_mod
    if v is None:
        return "no value (the cell is empty)"
    if isinstance(v, dt.datetime):
        return v.date().isoformat() if v.time() == dt.time(0) else v.isoformat(sep=" ")
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    return spec_mod._value_text(v)


def input_range_findings(spec_data: dict[str, Any] | None, man: dict[str, Any], read: Any,
                         rebuild: str) -> list[common.Finding]:
    """V-INPUT-RANGE (v8, r7 verdict [A-aera-5, A-aera-6] part 1), for check and for audit with a manifest: each
    scalar input that declares "valid" (input_range_refs), whose saved value lies outside it (spec.valid_problem).
    `read(ref)` gives (True, saved value; None for an empty cell) or (False, None) when the cell cannot be read (a
    missing sheet, a formula without a saved value): such a cell is left out. The message carries the input's note
    when it has one (a fixed input says why it is fixed)."""
    from . import spec as spec_mod
    found: list[common.Finding] = []
    for iid, item, ref in input_range_refs(spec_data, man):
        ok, value = read(ref)
        if not ok:
            continue
        problem = spec_mod.valid_problem(item["valid"], value)
        if problem is None:
            continue
        sheet, cell = common.parse_ref(ref)
        cell = cell.replace("$", "")
        where = f"{common.quote_sheet(sheet)}!{cell}" if sheet else cell
        note = item.get("note")
        message = (f"input '{iid}' ({item.get('label', iid)}) at {where} holds {_saved_value_text(value)}, {problem}"
                   + (f"; the input's note says: {note}" if note else ""))
        found.append(common.Finding("V-INPUT-RANGE", "error", message, INPUT_RANGE_FIX.format(rebuild=rebuild),
                                    sheet=sheet, cell=cell,
                                    data={"input": iid, "valid": dict(item["valid"]),
                                          "value": value if value is None or isinstance(value, (int, float, str))
                                          else _saved_value_text(value)}))
    return found


def _accepted_totals(root: Path, table: str, digest: str, contract: dict[str, Any] | None
                     ) -> tuple[dict[str, dict[str, Any]] | None, dict[str, Any] | None]:
    """(totals, stale entry): the control totals accepted for the snapshot with this digest
    (raw/<table>.controls.json, lib.controls) under the agreement in force of `contract`, by column, or None; the
    stale entry is the one recorded for this digest under an earlier agreement (v9, r8 verdict [A-aera-2, C-sol-1]),
    or None."""
    from . import controls as controls_mod
    found, stale = controls_mod.lookup(root, table, digest, contract)
    if found is None:
        return None, stale
    return {str(x["column"]): dict(x) for x in found}, None


def _record_accepted_totals(report: common.Report, root: Path, table: str, digest: str, rows: int,
                            contract: dict[str, Any] | None, ctl: dict[str, Any], given: Sequence[str]) -> None:
    """Record the control totals an accepted snapshot was checked against (v2, A-aera-7): each contract total
    column with the value used (--control of this run, else the total already recorded for this snapshot under the
    agreement in force, which an earlier --control gave, else the agreed contract's) and the contract's tolerance.
    v9 (r8 verdict A-aera-4): only contract total columns are recorded (check --accept-refresh refuses --control for
    any other column before Excel starts). v3 (C-sol-1): a value read from the workbook (the inputs sheet) is never
    recorded. The contract is not changed. Nothing is recorded when the snapshot was checked against no total."""
    tol_of = {x["column"]: float(x.get("tolerance", 0) or 0)
              for x in ((contract or {}).get("expect") or {}).get("totals") or []}
    used: list[dict[str, Any]] = []
    for x in ((contract or {}).get("expect") or {}).get("totals") or []:
        col = x["column"]
        if col in ctl:
            value, tol = _control_value(ctl[col])
            used.append({"column": col, "value": value, "tolerance": tol_of[col] if tol is None else tol})
        else:
            used.append({"column": col, "value": float(x["value"]), "tolerance": tol_of[col]})
    if not used:
        return
    from . import controls as controls_mod
    # v9 (r8 verdict [A-aera-2, C-sol-1] (1)): recorded under the agreement in force (agreement.contract_sha256)
    controls_mod.record_acceptance(root, table, digest=digest, rows=rows, totals=used, given=list(given),
                                   contract_sha256=controls_mod.agreement_hash(contract))
    report.note(f"raw/{table}.controls.json: the snapshot's control totals recorded ("
                + ", ".join(f"{u['column']} {_n(u['value'])}" for u in used)
                + "); rotm.py build and check use them for this snapshot under the agreement in force; the contract "
                  "is unchanged")


def _check_table(report: common.Report, model: _Model, t: str, entry: dict[str, Any],
                 contract: dict[str, Any] | None, state: str, facts: _Facts, controls: dict[str, Any],
                 accept_refresh: bool, values: dict[str, Any], details: dict[str, str], results: dict[str, str],
                 recalculated: bool, package: Any, given_texts: Sequence[str] = (), *,
                 qtables: dict[str, str | None] | None = None, input_values: dict[str, Any] | None = None,
                 may_write: bool = True, version_blocked: bool = False) -> dict[str, Any]:
    """Findings for one raw table and its acceptance. `accept_refresh` is off when --refresh was asked for and no
    refresh ran; `may_write` False then keeps the manifest as it is (v3, A-aera-1 (4)). v4: rows are accepted only
    from a live query table, a table query_tables names (A-aera-3); `version_blocked` (the table has CT-VERSION)
    accepts and records nothing for the table: no snapshot, no controls record, no manifest field (C-sol-2); a query
    attached by hand is recorded also when its rows equal the landed snapshot (C-sol-6)."""
    root, wb, man = model.root, model.workbook, model.manifest
    qtables = qtables or {}
    sheet = entry["name"]
    live = sheet in qtables
    # v5 ([A-aera-2, C-sol-1, C-sol-4] part 1): the report's query_loaded follows the live query table; what the
    # manifest says (claimed) chooses the words of R-CHANGED only
    loaded = _query_loaded(entry, package, qtables)
    claimed = _query_claimed(entry, package, qtables)
    if version_blocked:
        may_write = False
    info: dict[str, Any] = {"raw_sheet": sheet, "landing": entry.get("landing", "static"),
                            "query_loaded": loaded, "query_table": live, "contract_state": state}
    raw = facts.raw.get(t)
    if raw is None:
        report.error("R-CONTRACT", f"the workbook has no raw sheet {sheet} (the manifest lists it)",
                     "rebuild the workbook with rotm.py build --force", sheet=sheet)
        return info
    header, rows = raw
    hdr = [str(h) for h in header]
    types = manifest_mod.raw_types(man, t, root) or ["text"] * len(header)
    info["rows"] = len(rows)
    # v3, A-aera-10: a number, date or boolean cell stored as text is an error, and its stored type is part of the
    # row comparison (the digest), so the text '1' is not the number 1
    stored_findings, digest_rows = _stored_as_text(root, t, sheet, hdr, rows, types)
    digest = manifest_mod.raw_digest(hdr, digest_rows, types)
    # Control totals (v3, A-aera-4, C-sol-1): --control of this run; else the totals accepted for this exact sheet
    # digest (raw/<t>.controls.json, lib.controls); else the agreed contract's. The inputs sheet is never the
    # control: its control cell is compared with the control used (D-CONTROL-INPUT).
    ctl, sources, stale = _control_totals(root, t, digest, contract, controls)
    if stale is not None:
        # v9 (r8 verdict [A-aera-2, C-sol-1] (3)): an entry accepted under an earlier agreement is not used
        from . import controls as controls_mod
        report.note(controls_mod.stale_note(t, stale, contract, given=list(controls)))
    control_inputs = _control_inputs(t, contract, ctl, sources, results, facts, values, details, recalculated,
                                     input_values or {})
    info["control_inputs"] = control_inputs
    findings: list[common.Finding] = []
    if contract is not None:
        findings = check_raw(SheetRows(t, hdr, rows), contract, controls=ctl, sheet=sheet, control_sources=sources)
        flagged = {f.data.get("column") for f in stored_findings}
        findings = [f for f in findings if not (f.rule == "D-TYPE" and f.data.get("column") in flagged)]
        if stale is not None:
            findings = [_stale_total(f, t, stale, root, claimed) for f in findings]
    else:
        expected_cols = list(entry.get("columns") or [])
        if expected_cols and hdr != expected_cols:
            findings.append(common.Finding("R-CONTRACT", "error", f"the headers of {sheet} differ from the manifest's "
                                           f"columns ({', '.join(expected_cols)})", "rebuild from the snapshot",
                                           sheet=sheet, cell="A1"))
    findings = stored_findings + findings + _key_separator(root, t, sheet, hdr, rows, contract, man)
    data_errors = [f for f in findings if f.severity == "error"]
    report.extend(findings)
    recorded = entry.get("digest")
    wb_digest = entry.get("workbook_digest")
    info.update(digest=digest, recorded_digest=recorded, workbook_digest=wb_digest,
                changed=bool(recorded) and digest != recorded, accepted=False)
    snap_rel = entry.get("snapshot") or f"raw/{t}.csv"
    snap = root / snap_rel
    rows_before = entry.get("rows")
    snap_digest: str | None = None
    snap_problem: str | None = None
    if snap.is_file():
        try:
            snap_digest, snap_rows, _h = manifest_mod.csv_digest(snap, types)
            info["snapshot_rows"] = snap_rows
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            snap_problem = type(exc).__name__
    spec_path = (root / str(man.get("spec") or "model.spec.json")).as_posix()
    rebuild = f"{_rotm()} build \"{spec_path}\" --force"
    # The static sheet is older than a snapshot landed after the build (land records the new digest).
    landed_later = bool(recorded) and snap_digest == recorded and digest != recorded
    # v3, A-aera-1 (5): the sheet holds the rows rotm.py last wrote into the workbook, and raw/<t>.csv holds rows
    # landed after that (R-NEWER): such rows are never accepted as a refresh
    newer = bool(recorded) and wb_digest is not None and digest == wb_digest and digest != recorded
    newer_msg = (f"{sheet} holds the rows the workbook had when rotm.py last wrote it, and {snap_rel} holds rows "
                 f"landed after that; nothing was accepted and {snap_rel} was not changed")
    newer_fix = (f"refresh the query ({_rotm()} check \"{root.as_posix()}\" --refresh) or rebuild from the snapshot "
                 f"({rebuild})")
    # v8 (r7 verdict A-aera-1): raw/<t>.csv was landed after rotm.py last wrote rows into the workbook (the landed
    # digest differs from the workbook digest): an acceptance accepts no changed rows, whatever the sheet holds
    landed_after_write = bool(recorded) and wb_digest is not None and wb_digest != recorded
    landed_msg = (f"{sheet}: {snap_rel} was landed after the workbook was last written (the manifest's landed digest "
                  "differs from its workbook digest), so no changed rows are accepted; nothing was accepted, and "
                  f"{snap_rel}, raw/{t}.controls.json and the manifest were not changed")
    landed_fix = (f"rebuild from the landed snapshot ({rebuild}), then refresh ({_rotm()} check \"{root.as_posix()}\" "
                  "--refresh --strict) and accept (--accept-refresh)")
    if recorded and digest == recorded:
        report.note(f"{sheet}: {_n(len(rows))} data rows, unchanged since landing (digest equals the manifest)")
        if may_write and wb_digest != digest:
            # v3, A-aera-1: every check records the rows the workbook holds when they equal the landed snapshot
            _set_raw_fields(wb, t, {"workbook_digest": digest})
        if accept_refresh and not version_blocked and state == "agreed" and not data_errors:
            if live and may_write and _hand_attached(entry, qtables):
                # v4 (C-sol-6): the first load of a query attached by hand returns the rows already landed; it is
                # recorded here too (landing power_query, query_attach hand, its name, no contract hash)
                hand = _hand_fields(entry, qtables)
                if _needs_fields(entry, hand):
                    _set_raw_fields(wb, t, hand)
                    report.note(f"{sheet}: recorded in the manifest as loaded by the query "
                                f"{hand['query_name'] or '(name not found)'} attached by hand (query_attach hand)")
            if controls and claimed and snap_digest == recorded and not live:
                # v4 (A-aera-3): totals are accepted only from a live query table
                report.note(f"{sheet}: --accept-refresh: the --control totals were not recorded, because the "
                            f"workbook holds no query table at A1 of {sheet}")
            elif controls and loaded and snap_digest == recorded:
                # An acceptance run again (for example after an interrupted one, v2 A-aera-7): the rows are the
                # accepted snapshot already; the --control totals are recorded for it (the same record as the first
                # run) and written into the inputs sheet below.
                _record_accepted_totals(report, root, t, digest, len(rows), contract, ctl, given_texts)
                info["accepted"] = True
                report.note(f"{sheet}: --accept-refresh on rows already recorded as the landed snapshot; the "
                            f"--control totals were recorded for it in raw/{t}.controls.json")
    elif recorded is None or digest != recorded:
        what = (f"{sheet} differs from the landed snapshot recorded in the manifest "
                f"({_n(rows_before) if rows_before is not None else '?'} rows recorded, {_n(len(rows))} rows now)"
                if recorded else f"{sheet} has no landed digest in the manifest ({_n(len(rows))} rows now)")
        static_fix = (f"rebuild the workbook from the landed snapshot {snap_rel}: {rebuild}" if landed_later else
                      f"raw data changes only by landing: {_rotm()} land \"{root.as_posix()}\" --table {t} --csv "
                      f"<file>, then {rebuild}")
        if accept_refresh:
            if not claimed:
                report.error("R-CHANGED", what + "; it is a static table, not loaded by a query, so the change is not "
                             "accepted as a refresh", static_fix, sheet=sheet)
            elif not live:
                # v4 (A-aera-3): rows are accepted only from a live query table at A1 of the raw sheet
                says = ("the manifest says a query loads it" if entry.get("landing") == "power_query" else
                        f"the workbook holds the query {entry.get('query_name')}")
                report.error("R-CHANGED", what + f"; {says}, but the workbook holds no query table at A1 of {sheet} "
                             "(converted to a range or deleted), so the change is not accepted as a refresh",
                             f"attach the query again ({_rotm()} land \"{root.as_posix()}\" --table {t} --power-query, "
                             "or by hand as references/excel-power-query.md says) and refresh, or rebuild from the "
                             f"snapshot ({rebuild})", sheet=sheet)
            elif version_blocked:
                # v4 (C-sol-2): the workbook was built from another version of the contract (CT-VERSION)
                report.error("R-CHANGED", what + "; not accepted, because the workbook was built from another version "
                             "of its contract (CT-VERSION)",
                             f"rebuild the workbook from the agreed contract ({rebuild}), refresh again "
                             f"({_rotm()} check \"{root.as_posix()}\" --refresh) and run check --accept-refresh",
                             sheet=sheet)
            elif state != "agreed":
                report.error("R-CHANGED", what + f"; not accepted, because the contract of '{t}' is not agreed",
                             "get the contract agreed first (see CT-STATE)", sheet=sheet)
            elif landed_after_write:
                # v8 (r7 verdict A-aera-1): also covers the case R-NEWER named (the sheet equals workbook_digest)
                report.error("R-CHANGED", landed_msg, landed_fix, sheet=sheet)
            elif data_errors:
                report.error("R-CHANGED", what + f"; not accepted: the new rows break the contract "
                             f"({len(data_errors)} error findings above)",
                             "fix the source or the query, refresh again, and run check --accept-refresh", sheet=sheet)
            elif wb_digest is None:
                # v3, A-aera-1 (R-NO-WBDIGEST): a manifest written before v3 cannot tell a refresh from a landing
                report.error("R-CHANGED", f"the manifest records no workbook digest for {sheet} (built before v3), so "
                             "a refresh cannot be told from a later landing; nothing was accepted",
                             f"rebuild once ({rebuild}); build records the digest", sheet=sheet)
            else:
                _write_snapshot(snap, header, rows, types)
                written_digest = manifest_mod.csv_digest(snap, types)[0]
                fields: dict[str, Any] = {"digest": digest, "rows": len(rows), "snapshot": snap_rel,
                                          "workbook_digest": digest}
                if _hand_attached(entry, qtables):
                    # v3, C-sol-7: a query attached by hand at A1 of the raw sheet; no contract hash is recorded (v4:
                    # the rule of plan 5.3, also over a query rotm.py had attached before)
                    hand = _hand_fields(entry, qtables)
                    if _needs_fields(entry, hand):
                        report.note(f"{sheet}: recorded in the manifest as loaded by the query "
                                    f"{hand['query_name'] or '(name not found)'} attached by hand (query_attach hand)")
                    fields.update(hand)
                _set_raw_fields(wb, t, fields)
                info["accepted"] = True
                _record_accepted_totals(report, root, t, digest, len(rows), contract, ctl, given_texts)
                no_total = not ((contract or {}).get("expect") or {}).get("totals") and not ctl
                report.info("R-ACCEPTED", f"{sheet}: the refreshed rows passed the contract and were recorded as the "
                            f"new landed snapshot ({_n(rows_before) if rows_before is not None else '?'} rows before, "
                            f"{_n(len(rows))} now; {snap_rel} and the manifest digest updated)"
                            + ("; no control total was given, so the rows were checked for count, keys, nulls, "
                               "types and allowed values only" if no_total else ""), "", sheet=sheet)
                if written_digest != digest:
                    report.warning("R-SNAPSHOT", f"{snap_rel} was written but its digest differs from the sheet's",
                                   "report this: the CSV writer and the sheet reader normalise a value differently",
                                   sheet=sheet)
        elif recorded:
            if claimed and newer:
                report.error("R-CHANGED", newer_msg, newer_fix, sheet=sheet)
            else:
                if claimed:
                    fix = (f"if this is a refresh you expect, run {_rotm()} check \"{root.as_posix()}\" "
                           f"--accept-refresh (add --control {t}.<column>=<source total> when the source total "
                           f"changed); otherwise rebuild from the snapshot: {rebuild}")
                else:
                    fix = f"{sheet} is a static table: {static_fix}"
                if landed_later:
                    what += f"; {snap_rel} holds the rows landed after the workbook was built"
                report.error("R-CHANGED", what, fix, sheet=sheet)
        else:
            report.note(what + "; run check --accept-refresh to record it")
    if accept_refresh and version_blocked:
        report.note(f"{sheet}: nothing was accepted, because the workbook was built from another version of its "
                    f"contract (CT-VERSION); {snap_rel}, raw/{t}.controls.json and the manifest were not changed")
    if not info["accepted"] and recorded:
        if snap_problem:
            report.error("R-SNAPSHOT", f"{snap_rel} cannot be read ({snap_problem})",
                         f"land it again with rotm.py land \"{root.as_posix()}\" --table {t} --csv <file>")
        elif snap_digest is not None:
            if snap_digest != recorded:
                report.error("R-SNAPSHOT", f"{snap_rel} changed after it was landed: its digest differs from the "
                             "one recorded in the manifest",
                             f"land it again with {_rotm()} land \"{root.as_posix()}\" --table {t} --csv <file> "
                             "(land records the digest), or restore the file")
        else:
            report.warning("R-SNAPSHOT", f"{snap_rel} (the landed snapshot) is missing, so it was not compared",
                           f"land it again with rotm.py land \"{root.as_posix()}\" --table {t} --csv <file>")
    # A --control total that the workbook's own total check does not use yet: written into the linked input cell
    # by check_model when the rows were accepted and Excel is there. Any other difference between that cell and
    # the control used is D-CONTROL-INPUT (v3, C-sol-1): the cell is not a source of control totals.
    for item in control_inputs:
        item["write"] = bool(info["accepted"] and recalculated and item["input"] and item["source"] == "--control")
    return info


_DROP = object()            # _set_raw_fields: remove the key


def _set_raw_fields(wb: Path, table: str, fields: dict[str, Any]) -> bool:
    """Set fields of the manifest's raw entry of `table` (load_manifest, write_manifest; _DROP removes a key).
    Writes only when something changes; returns whether it wrote."""
    data = manifest_mod.load_manifest(wb)
    entry = manifest_mod.raw_sheet(data or {}, table) if data else None
    if entry is None:
        return False
    changed = False
    for key, value in fields.items():
        if value is _DROP:
            if key in entry:
                entry.pop(key)
                changed = True
        elif entry.get(key, _DROP) != value:
            entry[key] = value
            changed = True
    if changed:
        data["updated_at"] = common.now_iso()
        manifest_mod.write_manifest(wb, data)
    return changed


STORED_AS_TEXT_TYPES = ("integer", "number", "date", "datetime", "boolean")
TEXT_TAG = "\x00text:"      # prefixed to a value stored as text before the sheet's rows are digested (no landed text
                            # holds it), so the digest differs from the landed one (v3, A-aera-10)
NUMBER_TAG = "\x00number:"  # v4 (A-aera-9): the same for a number stored in a text column, so the text '901' is
                            # not the number 901
LOGICAL_TAG = "\x00logical:"   # v6 (r5 verdict A-aera-5): the same for a logical value stored in a text column, so
                               # the text 'TRUE' is not the logical value TRUE (followed by TRUE or FALSE)


def _stored_as_text(root: Path, t: str, sheet: str, header: Sequence[str], rows: Sequence[Sequence[Any]],
                    types: Sequence[str]) -> tuple[list[common.Finding], list[list[Any]] | Sequence[Sequence[Any]]]:
    """(D-TYPE findings, rows to digest) for workbook rows: a cell of an integer, number, date, datetime or boolean
    column that the workbook stores as text (v3, A-aera-10). The rows to digest hold TEXT_TAG + text in those
    cells, so the stored type is part of the row comparison. v4 (A-aera-9): a number in a text column, NUMBER_TAG.
    v6 (r5 verdict A-aera-5): a logical value in a text column, LOGICAL_TAG + TRUE or FALSE. Cells whose stored
    type fits the contract type are not tagged, so the digests of existing manifests and snapshots stay as they are."""
    findings: list[common.Finding] = []
    tagged: list[list[Any]] | None = None
    fix = (f"land the data again ({_rotm()} land \"{root.as_posix()}\" --table {t} --csv <file> --force) and "
           "rebuild; a raw sheet holds landed values only")
    for j, ctype in enumerate(list(types)[:len(header)]):
        if ctype in STORED_AS_TEXT_TYPES:
            kinds = [([(i, r[j]) for i, r in enumerate(rows) if j < len(r) and isinstance(r[j], str) and r[j] != ""],
                      TEXT_TAG, "text", "stored_as_text")]
        elif ctype == "text":
            # v4 (A-aera-9): a cell of a text column that the workbook stores as a number (not a logical value);
            # v6 (r5 verdict A-aera-5): a cell of a text column that the workbook stores as a logical value
            kinds = [([(i, r[j]) for i, r in enumerate(rows) if j < len(r) and isinstance(r[j], (int, float))
                       and not isinstance(r[j], bool)], NUMBER_TAG, "numbers", "stored_as_number"),
                     ([(i, r[j]) for i, r in enumerate(rows) if j < len(r) and isinstance(r[j], bool)],
                      LOGICAL_TAG, "logical values", "stored_as_logical")]
        else:
            continue
        for cells, tag, stored, key in kinds:
            if not cells:
                continue
            if tagged is None:
                tagged = [list(r) for r in rows]
            for i, v in cells:
                if isinstance(v, bool):
                    tagged[i][j] = tag + ("TRUE" if v else "FALSE")
                else:
                    tagged[i][j] = tag + (v if isinstance(v, str) else manifest_mod.normalise_value(v, "number"))
            first_i, first_v = cells[0]
            first = _coord(j, first_i, FIRST_DATA_ROW)
            if isinstance(first_v, bool):
                shown = "TRUE" if first_v else "FALSE"
            else:
                shown = f"'{first_v}'" if isinstance(first_v, str) else _n(first_v)
            findings.append(common.Finding(
                "D-TYPE", "error", f"{_n(len(cells))} cell(s) of {ctype} column {header[j]} in {sheet} are stored as "
                f"{stored} (first {first}: {shown})", fix, sheet=sheet, cell=first, count=len(cells),
                cells=[_coord(j, i, FIRST_DATA_ROW) for i, _v in cells[:20]],
                data={"column": header[j], "type": ctype, key: len(cells)}))
    return findings, (tagged if tagged is not None else rows)


# ----------------------------------------------------------------------------- hidden decimals (v6, C-sol-6)

_FMT_LITERAL = re.compile(r'"[^"]*"|\\.|\[[^\]]*\]|_.|\*.')


def shown_decimals(code: str) -> int | None:
    """The decimals of a value that the number format `code` shows, from its first section (v6, r5 verdict
    C-sol-6): the digit placeholders (0, # and ?) after the decimal point, 2 more for each bare percent sign, 3 fewer
    for each comma that scales by 1,000. None for General and for a format that shows no plain number (a date or
    time, text, scientific notation, or no digit placeholder): such a column is not compared."""
    first = str(code).split(";")[0]
    body = _FMT_LITERAL.sub("", first)
    if not body.strip() or body.strip().lower() == "general":
        return None
    if re.search(r"[A-Za-z@]", body) or not re.search(r"[0#?]", body):
        return None
    m = re.search(r"\.([0#?]*)", body)
    places = len(m.group(1)) if m else 0
    scale = re.search(r"[0#?](,+)[^0#?]*$", body)
    return places + 2 * body.count("%") - 3 * (len(scale.group(1)) if scale else 0)


def value_decimals(value: Any) -> int:
    """The decimals a number holds, at Excel's 15 significant digits (858800.25: 2; 0.1 + 0.2: 1); 0 for a whole
    number and for anything that is not a finite number."""
    from decimal import Decimal
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return 0
    if float(value).is_integer():
        return 0
    exp = Decimal(format(float(value), ".15g")).normalize().as_tuple().exponent
    return max(0, -int(exp)) if isinstance(exp, int) else 0


def hidden_decimal_findings(root: Path, t: str, entry: dict[str, Any], header: Sequence[Any],
                            rows: Sequence[Sequence[Any]], contract: dict[str, Any] | None,
                            dictionary: Any) -> list[common.Finding]:
    """I-HIDDEN-DECIMALS (v6, r5 verdict C-sol-6; information, the strict exit code does not change): for each
    number column of raw table `t` (the manifest raw entry's columns and types), the decimals its number format shows
    (the entry's formats key, read through `dictionary`; General is skipped) against the decimals of the values in
    `rows` (shown_decimals, value_decimals). One finding per column whose values hold more, naming the table, the
    column, the first sheet row and the count. The fix depends on where the format comes from: the contract column's
    "format" (agree another format, then rebuild), else the format build chose (rebuild). No format is changed. Used
    by check after a refresh that ran and by land after it lands rows into a model with a manifest."""
    columns = list(entry.get("columns") or [])
    types = list(entry.get("types") or [])
    formats = list(entry.get("formats") or [])
    index = {str(c): k for k, c in enumerate(columns)}
    sheet = entry.get("name") or f"raw_{t}"
    named = {c.get("name"): c.get("format") for c in (contract or {}).get("columns") or [] if isinstance(c, dict)}
    model = root.as_posix()
    out: list[common.Finding] = []
    for j, name in enumerate(header):
        k = index.get(str(name))
        if k is None or k >= len(types) or types[k] != "number" or k >= len(formats) or not formats[k]:
            continue
        key = str(formats[k])
        if key == "general":
            continue
        try:
            code = dictionary.number_format(key)
        except common.RotmError:
            continue
        shown = shown_decimals(code)
        if shown is None:
            continue
        hits = [(i, r[j]) for i, r in enumerate(rows) if j < len(r) and value_decimals(r[j]) > max(shown, 0)]
        if not hits:
            continue
        first_i, first_v = hits[0]
        row = FIRST_DATA_ROW + first_i
        if named.get(str(name)) == key:
            fix = (f"the format comes from contracts/{t}.contract.json (column {name}, \"format\": \"{key}\"): agree a "
                   "format that shows the decimals in the contract, then rebuild")
            origin = "contract"
        else:
            fix = (f"rebuild the workbook ({_rotm()} build \"{model}\" --force): build gives the column a format that "
                   "shows the decimals")
            origin = "build"
        out.append(common.Finding(
            "I-HIDDEN-DECIMALS", "info",
            f"{sheet} column {name}: {_n(len(hits))} value(s) hold more decimals than its number format {key} "
            f"({code}) shows (first row {row}: {format(float(first_v), '.15g')}); the values are stored and "
            "calculated in full", fix, sheet=sheet, cell=_coord(j, first_i, FIRST_DATA_ROW), count=len(hits),
            data={"table": t, "column": str(name), "format": key, "shown_decimals": shown, "first_row": row,
                  "origin": origin}))
    return out


def _sheet_rows_digest(root: Path, t: str, sheet: str, header: Sequence[Any], rows: Sequence[Sequence[Any]],
                       types: Sequence[str]) -> str:
    """The digest check compares for a raw sheet's rows (values stored as text tagged, _stored_as_text)."""
    hdr = [str(h) for h in header]
    return manifest_mod.raw_digest(hdr, _stored_as_text(root, t, sheet, hdr, rows, types)[1], types)


def _key_separator(root: Path, t: str, sheet: str, header: Sequence[str], rows: Sequence[Sequence[Any]],
                   contract: dict[str, Any] | None, man: dict[str, Any]) -> list[common.Finding]:
    """D-KEY-SEPARATOR (v3, C-sol-5): the helper sheet joins a key of several columns with '|' (a dupkey column)
    and a key part of the current raw rows holds '|', so two keys can join into one text. The workbook's key check
    may then show a repeat that is none; the key check of rotm.py check (D-KEY) stands."""
    hentry = manifest_mod.helper_sheet(man, t)
    if contract is None or hentry is None:
        return []
    dupkey = next((c for c in hentry.get("columns") or [] if c.get("kind") == "dupkey"), None)
    key = [k for k in contract.get("key") or []]
    idx = {h: i for i, h in enumerate(header)}
    if dupkey is None or len(key) < 2 or any(k not in idx for k in key):
        return []
    for i, r in enumerate(rows):
        for k in key:
            v = r[idx[k]] if idx[k] < len(r) else None
            if isinstance(v, str) and "|" in v:
                row = FIRST_DATA_ROW + i
                return [common.Finding(
                    "D-KEY-SEPARATOR", "warning", f"{sheet} row {row}: key column {k} holds '|', the separator of the "
                    f"joined key in {hentry['name']} column {dupkey.get('id')}, so the workbook's key check may show a "
                    "repeat that is none; the key check of rotm.py check stands",
                    f"rebuild ({_rotm()} build \"{root.as_posix()}\" --force): build then leaves the key check to "
                    "rotm.py check", sheet=sheet, cell=f"{common.col_letter(idx[k] + 1)}{row}",
                    data={"table": t, "row": row, "column": k})]
    return []


def _control_totals(root: Path, t: str, digest: str, contract: dict[str, Any] | None,
                    controls: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str], dict[str, Any] | None]:
    """(controls for check_raw, source of each, stale entry): the order of lib.controls (ORDER): --control of this
    run; else the total recorded in raw/<t>.controls.json for this exact digest under the agreement in force; else
    (not in the result) the agreed contract's total. v9 (r8 verdict [A-aera-2, C-sol-1]): an entry for the digest
    accepted under an earlier agreement is not used; it is returned as the stale entry (else None)."""
    ctl = dict(controls)
    sources = {c: "--control" for c in ctl}
    if contract is None:
        return ctl, sources, None
    accepted, stale = _accepted_totals(root, t, digest, contract)
    for tot in (contract.get("expect") or {}).get("totals") or []:
        col = tot["column"]
        if col in ctl or accepted is None or col not in accepted:
            continue
        rec = accepted[col]
        ctl[col] = (float(rec["value"]), float(rec.get("tolerance", tot.get("tolerance", 0)) or 0))
        sources[col] = f"raw/{t}.controls.json (accepted for this snapshot under the agreement in force)"
    return ctl, sources, stale


def _stale_total(f: common.Finding, t: str, stale: dict[str, Any], root: Path, query: bool) -> common.Finding:
    """D-TOTAL against the contract's total for a column whose total in raw/<t>.controls.json was accepted under an
    earlier agreement (v9, r8 verdict [A-aera-2, C-sol-1] (3)): the message names both totals and the fix gives the
    way on (--control with the source's current total, recorded under the agreement in force). Other findings are
    returned as they are."""
    from . import controls as controls_mod
    col = f.data.get("column") if isinstance(f.data, dict) else None
    if f.rule != "D-TOTAL" or f.data.get("source") != "the contract" or not col:
        return f
    tail = controls_mod.stale_tail(stale, col)
    if not tail:
        return f
    return _replace(
        f, message=f.message + tail,
        fix=controls_mod.stale_fix(t, col, root.as_posix(), query=query, rotm=_rotm()),
        data=dict(f.data, stale_accepted=controls_mod.stale_value(stale, col), stale_accepted_at=stale.get("at")))


def _quoted_ref(link: str) -> str:
    """'my inputs!D9' -> "'my inputs'!D9" (a _pure_link result as a reference _cached_values reads)."""
    sheet, _, cell = link.rpartition("!")
    return f"{common.quote_sheet(sheet)}!{cell}" if sheet else cell


def _control_inputs(t: str, contract: dict[str, Any] | None, ctl: dict[str, Any], sources: dict[str, str],
                    results: dict[str, str], facts: _Facts, values: dict[str, Any], details: dict[str, str],
                    recalculated: bool, input_values: dict[str, Any]) -> list[dict[str, Any]]:
    """For each total check <t>_total_<column>: the input cell its expected side links and the value stored
    there, when that value differs from the control total this check used (or a --control total that is not
    there yet). A value read from the workbook is only compared, never used as a control (v3, C-sol-1)."""
    out: list[dict[str, Any]] = []
    totals = {x["column"]: x for x in ((contract or {}).get("expect") or {}).get("totals") or []}
    for col in list(totals) + [c for c in ctl if c not in totals]:
        cid = f"{t}_total_{col}"
        if cid not in results:
            continue
        link = _pure_link(facts.formulas.get(_expected_ref(results[cid]), ""))
        stored: Any = None
        if link:
            stored = input_values.get(_quoted_ref(link))
        elif recalculated:
            row_vals = values.get(details.get(cid, ""))
            if isinstance(row_vals, list) and row_vals:
                vals = row_vals[0] if isinstance(row_vals[0], list) else row_vals
                stored = vals[1] if len(vals) > 1 else None
        cell_value = float(stored) if isinstance(stored, (int, float)) and not isinstance(stored, bool) else None
        if col in ctl:
            control, src = float(_control_value(ctl[col])[0]), sources.get(col, "--control")
        else:
            control, src = float(totals[col]["value"]), "the contract"
        if cell_value is not None and cell_value == control:          # exact (v2, A-aera-4)
            continue
        if cell_value is None and src != "--control":
            continue
        out.append({"column": col, "check": cid, "given": control, "control": control, "source": src,
                    "workbook_value": cell_value, "input": link})
    return out


def _refuse_refresh_states(root: Path, tables: Sequence[str], loaded: dict[str, bool], states: dict[str, str]) -> None:
    """F-REFRESH-STATE (v3, A-aera-5, C-sol-2): `check --refresh` is refused before Excel starts while a raw table
    loaded by a query has a contract that is missing, invalid, proposed, stale or agreed only on the example
    channel outside the plugin; the workbook is not opened, so it stays byte for byte."""
    bad = [(t, states.get(t, "missing")) for t in tables if loaded.get(t) and states.get(t, "missing") in
           REFRESH_STATE_WORDS]
    if not bad:
        return
    model = root.as_posix()
    details = []
    for t, st in bad:
        agree_cmd = (f"{_rotm()} contract agree \"{model}\" --table {t} --reply \"<their words>\" --by <name>")
        if st == "missing":
            how = (f"propose it ({_rotm()} contract propose \"{model}\" --table {t} --csv <sample>), show the user "
                   f"contracts/{t}.contract.md and record their reply ({agree_cmd})")
        else:
            how = (f"show the user the contract ({_rotm()} contract show \"{model}\" --table {t}) and record their "
                   f"reply ({agree_cmd})")
        details.append(f"raw_{t}: the contract is {REFRESH_STATE_WORDS[st]}; fix: {how}")
    raise common.Refusal(f"--refresh refused: {len(bad)} raw table(s) loaded by a query have no agreed contract; "
                         "nothing was refreshed", details=details,
                         fix=f"show the user the contract ({_rotm()} contract show \"{model}\" --table <t>) and record "
                             f"their reply ({_rotm()} contract agree \"{model}\" --table <t> --reply \"<their words>\" "
                             "--by <name>); the lines below name each table")


REFRESH_STATE_WORDS = {"missing": "missing", "invalid": "invalid", "proposed": "proposed", "stale": "stale",
                       "example": "agreed only on the example channel"}


def _contract_versions(report: common.Report, root: Path, raw_entries: dict[str, dict[str, Any]],
                       contracts: dict[str, dict[str, Any] | None], states: dict[str, str]) -> set[str]:
    """CT-VERSION (v3, A-aera-4): the manifest's contract_sha256 (the agreed contract build read) against the
    agreed contract's agreement.contract_sha256; a note when the manifest records none (built before v3). Returns
    the tables with CT-VERSION (v4, C-sol-2: nothing is accepted or recorded for them in this run)."""
    versioned: set[str] = set()
    for t, entry in raw_entries.items():
        if states.get(t) not in ("agreed", "example"):
            continue                                   # CT-STATE names a contract that is not agreed
        current = (((contracts.get(t) or {}).get("agreement") or {}).get("contract_sha256"))
        built = entry.get("contract_sha256")
        sheet = entry.get("name") or f"raw_{t}"
        if not built:
            report.note(f"{sheet}: the manifest records no contract hash (built before v3), so the contract version "
                        "was not compared")
        elif current and built != current:
            versioned.add(t)
            report.error("CT-VERSION", f"{sheet} was built from another version of its contract: the manifest records "
                         f"sha256:{_hex12(built)}..., contracts/{t}.contract.json is agreed as "
                         f"sha256:{_hex12(current)}...",
                         "the workbook was built from another version of the contract; land again if the rows change "
                         f"({_rotm()} land \"{root.as_posix()}\" --table {t} --csv <file> --force), and rebuild "
                         f"({_rotm()} build \"{root.as_posix()}\" --force)", sheet=sheet,
                         data={"table": t, "manifest": built, "agreed": current})
    return versioned


def _hex12(h: str) -> str:
    text = str(h)
    return (text[len("sha256:"):] if text.startswith("sha256:") else text)[:12]


_LINK_RE = re.compile(r"^=\s*(?:'((?:[^']|'')+)'|([A-Za-z_][\w.]*))!\$?([A-Za-z]{1,3})\$?(\d+)\s*$")


def _pure_link(formula: str) -> str | None:
    """'=inputs!$D$9' -> 'inputs!D9'; None when the formula is not a plain link to one cell."""
    m = _LINK_RE.match(formula or "")
    if not m:
        return None
    sheet = (m.group(1) or "").replace("''", "'") or m.group(2)
    return f"{sheet}!{m.group(3).upper()}{m.group(4)}"


def _apply_control_inputs(report: common.Report, wb: Path, root: Path, tables_data: dict[str, dict[str, Any]],
                          read_refs: Sequence[str]) -> tuple[dict[str, Any], list[list[str]], int] | None:
    """Write each accepted --control total into the input cell the workbook's own total check links, through
    Excel, then recalculate and read every check again (one job). Every other difference between that cell and
    the control total check used is D-CONTROL-INPUT (v3, C-sol-1: the inputs sheet does not set a control total).
    Returns (values, error cells, error count) of the new job, or None."""
    pending = [(t, item) for t, info in tables_data.items() for item in info.get("control_inputs") or []]
    edits, written = [], []
    for t, item in pending:
        if item.get("write"):
            sheet, cell = common.parse_ref(item["input"])
            edits.append({"op": "value", "sheet": sheet, "cell": cell, "value": item["given"]})
            written.append((t, item))
        else:
            where = item["input"] or "the control cell on the inputs sheet that check " + item["check"] + " reads"
            held = _n(item["workbook_value"]) if item["workbook_value"] is not None else "no number"
            col = f"{t}.{item['column']}"
            report.warning("D-CONTROL-INPUT",
                           f"{where} holds {held}; the control total of {col} this check used is "
                           f"{_n(item['control'])} ({item['source']})",
                           "the inputs sheet does not set a control total; after a refresh with a new source total "
                           f"run {_rotm()} check \"{root.as_posix()}\" --accept-refresh --control {col}=<source total>, "
                           f"which writes it there; otherwise rebuild ({_rotm()} build \"{root.as_posix()}\" --force)",
                           sheet=common.parse_ref(item["input"])[0] if item["input"] else None,
                           cell=common.parse_ref(item["input"])[1] if item["input"] else None,
                           data={"column": col, "control": item["control"], "source": item["source"],
                                 "workbook_value": item["workbook_value"]})
    if not edits:
        return None
    try:
        res, values, cells, count = _excel_job(wb, edits, read_refs)
    except common.EnvUnavailable as exc:
        report.warning("D-CONTROL-INPUT", f"the accepted control totals were not written into the inputs sheet: "
                       f"{exc.message}", "type them into the input cells named in the R-ACCEPTED lines")
        return None
    if not res.ok:
        report.warning("D-CONTROL-INPUT", f"the accepted control totals were not written into the inputs sheet: "
                       f"{res.message()}", "type them into the input cells by hand in Excel and run check again")
        return None
    for t, item in written:
        old = f" (it held {_n(item['workbook_value'])})" if item["workbook_value"] is not None else ""
        report.info("D-CONTROL-SET", f"{item['input']} set to {_n(item['given'])}, the accepted --control total of "
                    f"{t}.{item['column']}{old}; recalculated and saved", "", sheet=common.parse_ref(item["input"])[0])
    return values, cells, count


def _excel_job(wb: Path, ops: list[dict[str, Any]], read_refs: Sequence[str], *, timeout_s: float | None = None
               ) -> tuple[Any, dict[str, Any], list[list[str]], int]:
    """One Excel job: `ops`, then a full recalculation, the error cells and the read-back; saved in place.
    Returns (job result, values by ref, error cells, error count)."""
    from . import excel as xl
    job = list(ops) + [{"op": "recalc"}, {"op": "errors", "limit": 5000}]
    if read_refs:
        job.append({"op": "read", "refs": list(read_refs)})
    res = xl.run_job(wb, job, save=True, timeout_s=timeout_s)
    values: dict[str, Any] = {}
    cells: list[list[str]] = []
    count = 0
    if res.ok:
        for item in res.step("read").get("values") or []:
            values[str(item.get("ref"))] = None if item.get("problem") else _from_excel(item.get("value"))
        est = res.step("errors")
        cells = [list(map(str, c)) for c in est.get("cells") or []]
        count = int(est.get("count") or len(cells))
    return res, values, cells, count


# ----------------------------------------------------------------------------- extend

@dataclass
class _Plan:
    table: str
    sheet: str
    from_row: int
    to_row: int
    last_col: str
    first_row: int
    clear_from: int = 0            # v2, A-aera-6: helper rows clear_from..clear_to below the last raw row are cleared
    clear_to: int = 0
    raw_last_row: int = 0
    raw_sheet: str = ""
    # v3, A-aera-8: the helper sheet has no formula row (zero rows); row first_row is written from the manifest's
    # formulas first: (column letter, formula without _xlfn., number-format key) per helper column
    seed: list[tuple[str, str, str]] = field(default_factory=list)
    size: int = 8

    @property
    def fills(self) -> bool:
        return self.to_row > self.from_row

    @property
    def copy_from(self) -> int:
        """The row that is copied down: the last helper row, or the row written from the manifest."""
        return self.first_row if self.seed else self.from_row

    @property
    def clears(self) -> bool:
        return self.clear_to >= self.clear_from > 0

    @property
    def last_row(self) -> int:
        """The helper block's last row after the change."""
        if self.clears:
            return self.clear_from - 1
        return max(self.to_row, self.from_row)


def extend_helpers(target: str | Path, *, table: str | None = None, rows: int | None = None) -> common.Report:
    """Copy the last helper row down to the last raw row (or to sheet row `rows`) and clear the helper rows below
    the last raw row (and below `rows`), through Excel when it is available (else openpyxl when the package has no
    fragile part, else refuse); keep a copy in reports/backups/; compare before and after by code; update the
    manifest capacity; record the step. Returns a common.Report. v3 (A-aera-8): with zero raw rows every helper
    row is cleared, row 2 included (capacity 0, last_row 1); a helper sheet with no formula row gets row 2 from the
    manifest's columns[].formula, then the fill-down. The manifest's workbook_digest is set when the raw rows equal
    the landed snapshot."""
    from . import workbook as wbmod
    model = _resolve(target)
    root, wb, man = model.root, model.workbook, model.manifest
    report = common.Report("extend", wb.as_posix())
    helper_entries = {e["table"]: e for e in man.get("sheets", []) if e.get("type") == "helpers"}
    if not helper_entries:
        raise common.Refusal(f"{wb.name} has no helper sheet (the manifest lists none)", fix="nothing to extend")
    if table is not None and table not in helper_entries:
        from .schemas import nearest
        near = nearest(table, list(helper_entries))
        raise common.Refusal(f"the model has no helper sheet for table '{table}'"
                             + (f"; did you mean '{near}'?" if near else ""),
                             fix="use one of: " + ", ".join(helper_entries))
    if rows is not None and not (FIRST_DATA_ROW <= rows <= EXCEL_MAX_ROW):
        raise common.Refusal(f"--rows {rows}: give a sheet row from {FIRST_DATA_ROW} to {EXCEL_MAX_ROW:,}",
                             fix="give the last sheet row the helper block must reach, e.g. --rows 2351")
    facts = _read_facts(wb, man)
    raw_names = {e["table"]: e["name"] for e in man.get("sheets", []) if e.get("type") == "raw"}
    _cov, cov_data = _coverage(facts, man, root, raw_names)
    plans: list[_Plan] = []
    for t in ([table] if table else list(helper_entries)):
        entry = helper_entries[t]
        info = cov_data.get(t)
        hf = facts.helpers.get(t)
        if info is None or hf is None or not hf.present:
            raise common.Refusal(f"the helper sheet {entry['name']} is missing from {wb.name}",
                                 fix="rebuild the workbook with rotm.py build --force")
        covered, target_row = info["helper_last_row"], rows if rows is not None else info["raw_last_row"]
        raw_last = info["raw_last_row"]
        seed: list[tuple[str, str, str]] = []
        if covered < hf.first_row and max(target_row, raw_last) >= hf.first_row:
            # v3, A-aera-8: no formula row (a table built or extended with zero rows): row 2 is written from the
            # formulas the manifest keeps, then copied down
            for c in entry.get("columns") or []:
                f = str(c.get("formula") or "")
                if not c.get("col") or not f.startswith("=") or f == "=":
                    raise common.Refusal(f"{entry['name']} has no formula row, and the manifest keeps no formula for "
                                         f"helper column {c.get('id')}", fix="rebuild the workbook with rotm.py build "
                                                                             "--force")
                seed.append((str(c["col"]), f, str(c.get("format") or "general")))
        # v2, A-aera-6: helper rows below the last raw row (and below --rows) are cleared. v3, A-aera-8: with zero
        # raw rows every helper row is cleared, row 2 included; the manifest keeps the formulas for a later extend
        bottom = max(target_row, raw_last)
        lowest = max([hf.any_last] + list((hf.last or {}).values()))
        plan = _Plan(t, entry["name"], covered, max(covered, target_row), info["last_col"], hf.first_row,
                     raw_last_row=raw_last, raw_sheet=info["raw_sheet"], seed=seed,
                     size=int(entry.get("font_size") or 8))
        if lowest > bottom:
            plan.clear_from, plan.clear_to = bottom + 1, lowest
            plan.to_row = min(plan.to_row, bottom)
        if not plan.fills and not plan.clears:
            covers = (f"covers rows {hf.first_row} to {_n(covered)}" if covered >= hf.first_row else "covers no row")
            report.note(f"{entry['name']} {covers}; {info['raw_sheet']} ends at row {_n(raw_last)}: nothing to extend "
                        "or clear")
            continue
        plans.append(plan)
    if not plans:
        report.finish(False)
        return report
    input_sha = common.file_sha256(wb)
    before = wbmod.snapshot(wb)
    backups = root / "reports" / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    backup = backups / f"{wb.stem}.bak_{time.strftime('%Y%m%d_%H%M%S')}{wb.suffix}"
    k = 2
    while backup.exists():
        backup = backups / f"{wb.stem}.bak_{time.strftime('%Y%m%d_%H%M%S')}_{k}{wb.suffix}"
        k += 1
    shutil.copy2(wb, backup)
    report.note(f"copy kept before the change: {common.relpath_posix(backup, root)}")
    fragile = wbmod.fragile_parts(wb)
    from . import excel as xl
    status = xl.excel_status()
    recalculated = False
    dictionary = _model_dictionary(root, man) if any(p.seed for p in plans) else None
    if status.available:
        edits: list[dict[str, Any]] = []
        for p in plans:
            if p.seed and p.fills:
                edits += _seed_ops(p, dictionary)
        edits += [{"op": "fill_down", "sheet": p.sheet, "from_row": p.copy_from, "to_row": p.to_row,
                   "first_col": "A", "last_col": p.last_col} for p in plans if p.fills and p.to_row > p.copy_from]
        edits += [{"op": "clear", "sheet": p.sheet, "ranges": [f"A{p.clear_from}:{p.last_col}{p.clear_to}"],
                   "what": "contents"} for p in plans if p.clears]
        res = xl.apply_edits(wb, edits, out=wb, recalc_after=True)
        if not res.ok:
            raise common.EnvUnavailable(f"Excel did not extend the helper rows: {res.message()}; the workbook was not "
                                        "changed", fix="close the workbook if it is open in Excel and run extend again")
        recalculated = True
        method = f"through Excel {res.excel_version or ''} (PID {res.pid} closed)".replace("  ", " ")
    elif fragile:
        raise common.EnvUnavailable(
            f"{wb.name} holds parts openpyxl would drop: {'; '.join(fragile)}. Excel is not available "
            f"({status.reason}), so nothing was written",
            fix="run rotm.py extend on a PC with Excel, or copy the last helper row down by hand in Excel "
                "(design section 7.2)")
    else:
        _extend_openpyxl(wb, plans, dictionary)
        method = "with openpyxl (the package has no part openpyxl drops)"
    after = wbmod.snapshot(wb)
    before.has_cached_values = False           # compare formulas and constants; values change on purpose
    after.has_cached_values = False
    # The cleared helper rows are an intended change (their formulas go, their values may change); every other
    # cell must be equal (v2, A-aera-6).
    cleared = {(p.sheet, f"{common.col_letter(c)}{r}") for p in plans if p.clears
               for r in range(p.clear_from, p.clear_to + 1) for c in range(1, common.col_index(p.last_col) + 1)}
    intended = wbmod.Intended("cells" if cleared else "none", cells=cleared,
                              new_rows={p.sheet: (p.from_row + 1, p.to_row) for p in plans if p.fills},
                              values_may_change=bool(cleared))
    diffs = [d for d in wbmod.compare(before, after, intended)
             if not (d.kind == "name" and "_xlnm._FilterDatabase" in str(d.message))]
    if diffs:
        shutil.copy2(backup, wb)
        for d in diffs[:20]:
            report.error("X-EXTEND", d.message, "the workbook was restored from the copy; report this difference",
                         sheet=d.sheet, cell=d.cell)
        if len(diffs) > 20:
            report.note(f"{len(diffs)} differences in all; the first 20 are listed")
        report.finish(False)
        return report
    man2 = manifest_mod.load_manifest(wb) or man
    for p in plans:
        for e in man2.get("sheets", []):
            if e.get("type") == "helpers" and e.get("table") == p.table:
                e["last_row"] = p.last_row
                e["capacity"] = max(0, p.last_row - p.first_row + 1)
        if p.fills and p.seed:
            report.info("H-EXTENDED", f"{p.sheet}: wrote row {_n(p.first_row)} (A:{p.last_col}) from the helper "
                        f"formulas the manifest keeps (the sheet had no formula row) and copied it down to row "
                        f"{_n(p.to_row)}: {_n(p.to_row - p.from_row)} new helper rows {method}", "", sheet=p.sheet)
        elif p.fills:
            report.info("H-EXTENDED", f"{p.sheet}: copied row {_n(p.from_row)} (A:{p.last_col}) down to row "
                        f"{_n(p.to_row)}: {_n(p.to_row - p.from_row)} new helper rows {method}", "", sheet=p.sheet)
        if p.clears:
            report.info("H-CLEARED", f"{p.sheet}: cleared rows {_n(p.clear_from)} to {_n(p.clear_to)} (A:{p.last_col}),"
                        f" below the last raw row {_n(p.raw_last_row)} of {p.raw_sheet}: "
                        f"{_n(p.clear_to - p.clear_from + 1)} helper rows {method}; the helper block now ends at row "
                        f"{_n(p.last_row)}", "", sheet=p.sheet)
    # v3, A-aera-1: extend records the rows the workbook holds as its workbook digest only when they equal the
    # landed snapshot (after a refresh that is not accepted yet they differ, and the acceptance records them)
    for e in man2.get("sheets", []):
        if e.get("type") != "raw" or facts.raw.get(e.get("table")) is None:
            continue
        header, raw_rows = facts.raw[e["table"]]
        types = manifest_mod.raw_types(man2, e["table"], root) or ["text"] * len(header)
        d = _sheet_rows_digest(root, e["table"], e["name"], header, raw_rows, types)
        if e.get("digest") and d == e["digest"]:
            e["workbook_digest"] = d
    man2["updated_at"] = common.now_iso()
    manifest_mod.write_manifest(wb, man2)
    output_sha = common.file_sha256(wb)
    report.note("compared by code with the copy: formulas and constants outside the new and the cleared helper rows "
                "are equal; tables, names, charts, queries, conditional formats and validations are unchanged")
    if recalculated:
        report.note("recalculated in Excel and saved; run rotm.py check to read every check cell")
    else:
        report.note(f"not recalculated: the workbook now holds no cached values; run {_rotm()} check "
                    f"\"{root.as_posix()}\" on a PC with Excel")
    report.verified["recalculated"] = recalculated
    report.data.update(input_sha256=input_sha, output_sha256=output_sha, backup=backup.as_posix(),
                       tables={p.table: {"sheet": p.sheet, "from_row": p.from_row, "to_row": p.to_row,
                                         "last_col": p.last_col, "cleared_from": p.clear_from or None,
                                         "cleared_to": p.clear_to or None, "last_row": p.last_row}
                               for p in plans}, recalculated=recalculated)
    report.finish(False)
    try:
        modelfolder.record_step(root, model.rel, "extend", {
            "at": report.at, "exit_code": report.exit_code, "input_sha256": input_sha, "output_sha256": output_sha,
            "recalculated": recalculated, "tables": report.data["tables"]})
    except common.RotmError as exc:
        report.note(f"status not recorded in reports/status.json: {exc.line()}")
    return report


def _model_dictionary(root: Path, man: dict[str, Any]) -> Any:
    """The model's style dictionary (with the spec's "dictionary" override and style_overrides.json), as build
    used it; for the helper row extend writes from the manifest (v3, A-aera-8)."""
    from . import dictionary as dmod
    spec_dict = None
    spec_name = man.get("spec")
    if spec_name and (root / str(spec_name)).is_file():
        try:
            spec_dict = (common.read_json(root / str(spec_name)) or {}).get("dictionary")
        except (OSError, ValueError, AttributeError):
            spec_dict = None
    return dmod.load_for_model(root, spec_override=spec_dict)


def _seed_look(dictionary: Any, fmt: str, size: int) -> dict[str, Any]:
    """The calc look of a helper cell (build's _write_helpers): the dictionary's calc type in the column's number
    format; a text column left-aligned."""
    from . import styles
    look = styles.style_spec(dictionary, "calc", size=size, format_key=fmt or "general", value=0)
    if fmt == "text":
        look["align"] = {**(look.get("align") or {}), "horizontal": "left"}
    return look


def _seed_ops(p: _Plan, dictionary: Any) -> list[dict[str, Any]]:
    """Excel operations that write row p.first_row of a helper sheet with no formula row: each column's formula
    from the manifest and its look (v3, A-aera-8)."""
    ops: list[dict[str, Any]] = []
    for col, f, fmt in p.seed:
        cell = f"{col}{p.first_row}"
        # Excel stores a formula entered into a cell formatted as Text (@) as text (measured in Excel 16 on a text
        # helper column, 2026-11-25): the cell gets General first, then the formula, then its look
        ops.append({"op": "style", "sheet": p.sheet, "ranges": [cell], "number_format": "General"})
        ops.append({"op": "formula", "sheet": p.sheet, "cell": cell, "formula": f})
        ops.append({"op": "style", "sheet": p.sheet, "ranges": [cell], **_seed_look(dictionary, fmt, p.size)})
    return ops


def _extend_openpyxl(wb: Path, plans: Sequence[_Plan], dictionary: Any = None) -> None:
    """Copy each plan's source row down with openpyxl (formulas translated as Excel's fill-down does). A plan with
    `seed` first gets row first_row from the manifest's formulas, in the dictionary's calc style (v3, A-aera-8)."""
    import warnings
    from copy import copy

    import openpyxl
    from openpyxl.formula.translate import Translator
    from . import formula as fmod, styles
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        book = openpyxl.load_workbook(wb)
    for p in plans:
        ws = book[p.sheet]
        if p.seed and p.fills:
            from openpyxl.styles import Alignment
            for col, f, fmt in p.seed:
                cell = ws[f"{col}{p.first_row}"]
                cell.value = fmod.to_file_formula(f)
                styles.apply(cell, dictionary, "calc", size=p.size, format_key=fmt or "general")
                if fmt == "text":
                    cell.alignment = Alignment(horizontal="left", vertical=cell.alignment.vertical)
        for c in range(1, common.col_index(p.last_col) + 1):
            if p.clears:          # v2, A-aera-6: the rows below the last raw row lose their contents
                for r in range(p.clear_from, p.clear_to + 1):
                    if ws.cell(row=r, column=c).value is not None:
                        ws.cell(row=r, column=c).value = None
            if not p.fills:
                continue
            src = ws.cell(row=p.copy_from, column=c)
            v = src.value
            tr = Translator(v, origin=src.coordinate) if isinstance(v, str) and v.startswith("=") else None
            letter = common.col_letter(c)
            for r in range(p.copy_from + 1, p.to_row + 1):
                dst = ws.cell(row=r, column=c)
                dst.value = tr.translate_formula(f"{letter}{r}") if tr is not None else v
                if src.has_style:
                    dst._style = copy(src._style)
    tmp = wb.with_name(f".{wb.stem}.extend_{os.getpid()}.tmp{wb.suffix}")
    try:
        book.save(tmp)
        os.replace(tmp, wb)
    finally:
        if tmp.exists():
            tmp.unlink()


# ----------------------------------------------------------------------------- command line

def add_check_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("target", help="the model folder, or a workbook inside one")
    p.add_argument("--strict", action="store_true", help="exit 1 on any warning")
    p.add_argument("--json", metavar="FILE", help="write the report as JSON ('-' = stdout)")
    p.add_argument("--no-excel", action="store_true", help="do not recalculate (the report says: not recalculated)")
    p.add_argument("--refresh", action="store_true", help="refresh every Power Query table through Excel first")
    p.add_argument("--accept-refresh", action="store_true",
                   help="after a refresh: validate the new raw rows and record them as the landed snapshot")
    p.add_argument("--control", action="append", default=[], metavar="TABLE.COLUMN=VALUE",
                   help="source-side control total for the new snapshot")
    p.add_argument("--quiet", action="store_true", help="print only the last line")


def parse_controls(items: Iterable[str]) -> dict[str, float]:
    """['sales.amount=1234.5'] -> {'sales.amount': 1234.5}; common.Refusal for a malformed item."""
    out: dict[str, float] = {}
    for item in items or []:
        m = re.fullmatch(r"\s*([a-z][a-z0-9_]*)\.([^=\s]+)\s*=\s*(.+?)\s*", item)
        if not m:
            raise common.Refusal(f"--control '{item}' is not TABLE.COLUMN=VALUE",
                                 fix="write e.g. --control sales.amount=1234567.5")
        try:
            out[f"{m.group(1)}.{m.group(2)}"] = float(m.group(3).replace(",", ""))
        except ValueError:
            raise common.Refusal(f"--control {item}: '{m.group(3)}' is not a number",
                                 fix="write the source-side total as a number, e.g. 1234567.5") from None
    return out


def run_check(args: argparse.Namespace) -> int:
    report = check_model(args.target, strict=args.strict, excel=not args.no_excel, refresh=args.refresh,
                         accept_refresh=args.accept_refresh, controls=parse_controls(args.control),
                         control_texts=list(args.control or []))
    return report.emit(args.json, quiet=getattr(args, "quiet", False))


def add_extend_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("target", help="the model folder, or a workbook inside one")
    p.add_argument("--table", help="raw table (default every table with uncovered rows or helper rows below the "
                                   "last raw row)")
    p.add_argument("--rows", type=int, help="last sheet row the helper block must reach, e.g. 2351 "
                                            "(default the raw sheet's last row)")
    p.add_argument("--json", metavar="FILE", help="write the report as JSON ('-' = stdout)")
    p.add_argument("--quiet", action="store_true", help="print only the last line")


def run_extend(args: argparse.Namespace) -> int:
    report = extend_helpers(args.target, table=args.table, rows=args.rows)
    return report.emit(args.json, quiet=getattr(args, "quiet", False))

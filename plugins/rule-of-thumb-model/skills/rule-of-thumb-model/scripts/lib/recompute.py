"""Rule C-RECOMPUTE: each registered check computed again from the saved values of its own operand cells (v5, r4
verdict [A-aera-3, C-sol-5] parts 2 and 3; references/checks.md, "C-RECOMPUTE").

Owner: P3 of the v5 plan. Used by `check` (after a completed recalculation) and by `audit` (on the saved values, with
a manifest). openpyxl is imported inside the function only.

For each entry of manifest["checks"] (checks and alerts) the row on the checks sheet comes from its `result` ref.
The operands are read by the checks-sheet layout build writes (CHK, the same numbers as build.CHK): D actual,
E expected / minimum / bound, F maximum, G difference, H tolerance, I result. The result is computed again:

- equal: 1 when ABS(actual - expected) > tolerance, else 0;
- range: 1 when actual < minimum or actual > maximum;
- zero: 1 when actual <> 0;
- at_most: 1 when actual > bound.

The master check is computed again from the result cells as build's master formula does: 1 when the sum of the
numbers in the results range is above 0 or the count of numbers there differs from the count of filled id cells.
Alerts are not part of the master. When the manifest holds `result_formula` (per check entry) and `master_formula`,
the cells' formulas are compared with them. Operand formulas redirected by hand stay out of scope.

Findings (all errors, rule C-RECOMPUTE):
(a) the saved result differs from the computed one; (b) an operand (or the tolerance of an equal check) is not a
number (a number stored as text is not a number; a cell with a date or time format counts as the serial number
Excel holds in it); (c) the master differs from the results; (d) a result or master formula differs from the formula
build wrote. The data of each finding holds only JSON values.
"""
from __future__ import annotations

import datetime as _dt
import math
import re
from pathlib import Path
from typing import Any

from . import common

RULE = "C-RECOMPUTE"
# The checks-sheet columns (1 = A), the same numbers as build.CHK (a test asserts that they are equal)
CHK = {"id": 2, "label": 3, "actual": 4, "expected": 5, "max": 6, "diff": 7, "tol": 8, "result": 9, "target": 10}
CHECK_FIRST_ROW = 5          # build.CHECK_FIRST_ROW: the first check row (a manifest without the results range)
# An equal check's difference is computed in its own cell (G = D - E); Excel may give 0 for a difference that is
# tiny against its operands. Within this share of the larger operand, both results count as consistent.
REL_MARGIN = 1e-12

# operands per kind: (field name in the messages, column key of CHK)
OPERANDS = {
    "equal": (("actual", "actual"), ("expected", "expected"), ("tolerance", "tol")),
    "range": (("actual", "actual"), ("minimum", "expected"), ("maximum", "max")),
    "zero": (("actual", "actual"),),
    "at_most": (("actual", "actual"), ("bound", "expected")),
}

_ERRORS = ("#NULL!", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A", "#GETTING_DATA", "#SPILL!",
           "#CALC!", "#FIELD!", "#BLOCKED!", "#CONNECT!", "#BUSY!", "#UNKNOWN!", "#EXTERNAL!", "#PYTHON!")


def _rotm() -> str:
    return f"python -P -X utf8 -B \"{(common.scripts_dir() / 'rotm.py').as_posix()}\""


def _model_folder(workbook: Path) -> Path:
    """The model folder of the workbook (the first folder at or above it with rotm_model.json), else its folder."""
    from . import modelfolder
    return modelfolder.find_model_root(workbook) or workbook.parent


def _fix(workbook: Path) -> str:
    root = _model_folder(workbook).as_posix()
    return (f"rebuild the workbook with {_rotm()} build \"{root}\" --force (build writes the check formulas again), "
            f"then run {_rotm()} check \"{root}\"")


class _Cell:
    __slots__ = ("value", "kind")

    def __init__(self, value: Any, kind: str) -> None:
        self.value = value
        self.kind = kind            # number, error, text, bool, empty


def _serial(value: Any, epoch: Any = None) -> float | None:
    """A date, a date and time, a time or a duration as the Excel serial number it stands for (openpyxl returns
    these for a cell with a date or time number format); None for any other value."""
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time, _dt.timedelta)):
        from openpyxl.utils.datetime import to_excel
        return float(to_excel(value, epoch) if epoch is not None else to_excel(value))
    return None


def _json_safe(value: Any) -> Any:
    """A value for a finding's data: a number, a text, a boolean or None as it is (a dict or a list item by item),
    anything else as its text, so that no finding can stop the JSON report."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return str(value)


def _classify(value: Any, data_type: str | None) -> _Cell:
    if value is None or (isinstance(value, str) and value == "" and data_type != "e"):
        return _Cell(None, "empty")
    if data_type == "e" or (isinstance(value, str) and data_type != "s" and value.upper() in _ERRORS):
        return _Cell(str(value), "error")
    if isinstance(value, bool):
        return _Cell(value, "bool")
    serial = _serial(value)
    if serial is not None:
        return _Cell(serial, "number")      # a date operand is the number Excel holds
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return _Cell(value, "text")
        return _Cell(value, "number")
    return _Cell(value, "text")


def _read_sheet(path: Path, sheet: str, data_only: bool) -> dict[tuple[int, int], tuple[Any, str | None]] | None:
    """(row, column) -> (value, data_type) of the filled cells of one sheet; None when the sheet is absent."""
    import openpyxl
    book = openpyxl.load_workbook(common.native_path(path), read_only=True, data_only=data_only)
    try:
        if sheet not in book.sheetnames:
            return None
        ws = book[sheet]
        try:
            ws.reset_dimensions()
        except AttributeError:
            pass
        out: dict[tuple[int, int], tuple[Any, str | None]] = {}
        for row in ws.iter_rows():
            for c in row:
                r, col = getattr(c, "row", None), getattr(c, "column", None)
                if r is None or col is None or c.value is None:
                    continue
                v = c.value
                if not data_only and not isinstance(v, (str, int, float, bool)) and hasattr(v, "text"):
                    v = v.text          # an array formula object: its text
                if data_only:
                    serial = _serial(v, getattr(book, "epoch", None))
                    if serial is not None:
                        v = serial      # a date or time cell: the serial number, in the workbook's date system
                out[(int(r), int(col))] = (v, getattr(c, "data_type", None))
        return out
    finally:
        book.close()


def _num(x: Any) -> str:
    """A value for a message: a whole number without a decimal point, else up to 15 significant digits."""
    if isinstance(x, bool):
        return "TRUE" if x else "FALSE"
    if isinstance(x, int):
        return str(x)
    if isinstance(x, float):
        if x.is_integer() and abs(x) < 1e15:
            return str(int(x))
        return f"{x:.15g}"
    if x is None:
        return "(empty)"
    return repr(x) if isinstance(x, str) else str(x)


def _norm_formula(f: Any) -> str:
    """A formula for the comparison: without the leading '=', the _xlfn. prefixes and white space, upper case
    (result and master formulas hold no text literal)."""
    text = str(f or "").strip()
    text = text[1:] if text.startswith("=") else text
    text = re.sub(r"_xlfn\.|_xlws\.", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", "", text).upper()


def _ref(sheet: str, col: int, row: int) -> str:
    return f"{common.quote_sheet(sheet)}!{common.col_letter(col)}{row}"


def _range_cells(rng: str) -> list[tuple[int, int]]:
    """'I5:I28' -> [(5, 9), ..., (28, 9)]; [] when it cannot be read."""
    try:
        a, _sep, b = rng.replace("$", "").partition(":")
        c1, r1 = common.split_cell(a)
        c2, r2 = common.split_cell(b or a)
    except ValueError:
        return []
    return [(r, c) for r in range(min(r1, r2), max(r1, r2) + 1) for c in range(min(c1, c2), max(c1, c2) + 1)]


def _computed(kind: str, ops: dict[str, float]) -> tuple[set[int], str]:
    """(the results the operands allow, the detail text of message (a))."""
    a = ops["actual"]
    if kind == "equal":
        e, t = ops["expected"], ops["tolerance"]
        d = a - e
        out = {int(abs(d) > t)}
        margin = REL_MARGIN * max(abs(a), abs(e), abs(t))
        if abs(d) <= margin:
            out.add(int(0 > t))         # Excel may have given 0 for the tiny difference in G
        if abs(abs(d) - t) <= margin:
            out |= {0, 1}               # the difference equals the tolerance within rounding: not judged
        return out, f"|{_num(a)} - {_num(e)}| = {_num(abs(d))}, tolerance {_num(t)}"
    if kind == "range":
        lo, hi = ops["minimum"], ops["maximum"]
        return {int(a < lo or a > hi)}, f"{_num(a)} against {_num(lo)} to {_num(hi)}"
    if kind == "zero":
        return {int(a != 0)}, f"{_num(a)} against 0"
    b = ops["bound"]
    return {int(a > b)}, f"{_num(a)} against at most {_num(b)}"


def _checks_sheet_entry(manifest: dict[str, Any], sheet: str) -> dict[str, Any]:
    for s in manifest.get("sheets") or []:
        if s.get("type") == "checks" and (s.get("name") == sheet or not sheet):
            return s
    return {}


def check_findings(workbook: str | Path, manifest: dict[str, Any]) -> list[common.Finding]:
    """The C-RECOMPUTE findings of a workbook with its manifest (module docstring). Reads the saved values and the
    formulas of the checks sheet; [] when the manifest registers no check or the checks sheet is absent."""
    path = Path(common.native_path(workbook))
    entries = [c for c in (manifest or {}).get("checks") or [] if isinstance(c, dict) and c.get("result")]
    master_ref = (manifest or {}).get("master_check")
    if not entries and not master_ref:
        return []
    sheet = None
    for c in entries:
        sheet = common.parse_ref(str(c["result"]))[0] or "checks"
        break
    if sheet is None:
        sheet = common.parse_ref(str(master_ref))[0] or "checks"
    values = _read_sheet(path, sheet, data_only=True)
    formulas = _read_sheet(path, sheet, data_only=False)
    if values is None or formulas is None:
        return []
    fix = _fix(path)
    out: list[common.Finding] = []

    def cell(r: int, col: int) -> _Cell:
        v, dt = values.get((r, col), (None, None))
        return _classify(v, dt)

    def result_cell(c: dict[str, Any]) -> tuple[int, int] | None:
        try:
            col, r = common.split_cell(common.parse_ref(str(c["result"]))[1])
        except ValueError:
            return None
        return r, col

    # a workbook whose result cells hold no saved value at all (written, never calculated): nothing to compare
    saved_any = any(cell(*rc).kind != "empty" for rc in (result_cell(c) for c in entries) if rc is not None)
    if not saved_any and not any(c.get("result_formula") for c in entries) and not manifest.get("master_formula"):
        return []
    values_saved = saved_any

    def add(message: str, r: int, col: int, data: dict[str, Any]) -> None:
        coord = f"{common.col_letter(col)}{r}"
        out.append(common.Finding(RULE, "error", message, fix, sheet=sheet, cell=coord, cells=[coord],
                                  data=_json_safe(dict(data, rule=RULE))))

    for c in entries:
        csheet, coord = common.parse_ref(str(c["result"]))
        if (csheet or "checks") != sheet:
            continue
        try:
            col, r = common.split_cell(coord)
        except ValueError:
            continue
        kind = str(c.get("kind") or "")
        cid = str(c.get("id") or "")
        where = f"check '{cid}' ({common.quote_sheet(sheet)}!{coord})"
        base = {"check": cid, "kind": kind, "severity": c.get("severity", "error")}
        # (d) the result formula against the text build recorded
        recorded = c.get("result_formula")
        if recorded:
            now = formulas.get((r, col), (None, None))[0]
            if _norm_formula(now) != _norm_formula(recorded):
                add(f"{where}: its formula {now if now is not None else '(empty)'} differs from the formula build "
                    f"wrote ({recorded})", r, col, dict(base, part="d", formula=now, recorded=recorded))
        if kind not in OPERANDS or not values_saved:
            continue
        saved = cell(r, col)
        if saved.kind == "error":
            continue                # V-ERROR names the error value
        ops: dict[str, float] = {}
        bad = False
        for field, key in OPERANDS[kind]:
            oc = cell(r, CHK[key])
            if oc.kind == "number":
                ops[field] = float(oc.value)
                continue
            bad = True
            shown = oc.value if oc.kind == "error" else _num(oc.value)
            add(f"{where}: {field} {_ref(sheet, CHK[key], r)} holds {shown}, not a number", r, CHK[key],
                dict(base, part="b", field=field, value=oc.value))
        if bad:
            continue
        allowed, detail = _computed(kind, ops)
        computed = min(allowed) if len(allowed) == 1 else None
        if saved.kind == "number" and float(saved.value) in {float(x) for x in allowed}:
            continue
        shown_c = _num(computed) if computed is not None else " or ".join(_num(x) for x in sorted(allowed))
        add(f"{where}: the saved result is {_num(saved.value)}, but its operands give {shown_c}: {detail}", r, col,
            dict(base, part="a", saved=saved.value, computed=computed, operands=ops))

    if master_ref:
        out.extend(_master_findings(manifest, sheet, str(master_ref), values, formulas, entries, fix,
                                    values_saved=values_saved))
    return out


def _master_findings(manifest: dict[str, Any], sheet: str, master_ref: str,
                     values: dict[tuple[int, int], tuple[Any, str | None]],
                     formulas: dict[tuple[int, int], tuple[Any, str | None]], entries: list[dict[str, Any]],
                     fix: str, *, values_saved: bool = True) -> list[common.Finding]:
    msheet, mcoord = common.parse_ref(master_ref)
    if (msheet or "checks") != sheet:
        return []
    try:
        mcol, mrow = common.split_cell(mcoord)
    except ValueError:
        return []
    out: list[common.Finding] = []
    shown_ref = f"{common.quote_sheet(sheet)}!{mcoord}"

    def add(message: str, data: dict[str, Any]) -> None:
        out.append(common.Finding(RULE, "error", message, fix, sheet=sheet, cell=mcoord, cells=[mcoord],
                                  data=_json_safe(dict(data, rule=RULE))))

    recorded = (manifest or {}).get("master_formula")
    if recorded:
        now = formulas.get((mrow, mcol), (None, None))[0]
        if _norm_formula(now) != _norm_formula(recorded):
            add(f"the master check {shown_ref}: its formula {now if now is not None else '(empty)'} differs from the "
                f"formula build wrote ({recorded})", {"part": "d", "formula": now, "recorded": recorded})
    if not values_saved:
        return out
    entry = _checks_sheet_entry(manifest, sheet)
    results = _range_cells(str(entry.get("results") or ""))
    ids = _range_cells(str(entry.get("ids") or ""))
    if not results or not ids:
        rows = []
        for c in entries:
            if c.get("severity", "error") != "error":
                continue
            try:
                rows.append(common.split_cell(common.parse_ref(str(c["result"]))[1])[1])
            except ValueError:
                continue
        if not rows:
            return out
        last = max(rows)
        results = [(r, CHK["result"]) for r in range(CHECK_FIRST_ROW, last + 1)]
        ids = [(r, CHK["id"]) for r in range(CHECK_FIRST_ROW, last + 1)]
    total = 0.0
    numbers = 0
    for key in results:
        v = _classify(*values.get(key, (None, None)))
        if v.kind == "error":
            return out              # the master holds the error too; V-ERROR names it
        if v.kind == "number":
            total += float(v.value)
            numbers += 1
    filled = sum(1 for key in ids if values.get(key, (None, None))[0] not in (None, ""))
    computed = int(total > 0 or numbers != filled)
    saved = _classify(*values.get((mrow, mcol), (None, None)))
    if saved.kind == "error":
        return out
    if saved.kind == "number" and float(saved.value) == float(computed):
        return out
    add(f"the master check {shown_ref} holds {_num(saved.value)}, but the check results give {computed}",
        {"part": "c", "saved": saved.value, "computed": computed, "sum": total, "numbers": numbers,
         "ids": filled})
    return out

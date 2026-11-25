"""`build`: compile a model specification into a workbook (design sections 6, 7, 9; S1, F04, F05).

Owner: M3. The script owns layout, cell references, styles and check wiring, so two models built with it read
the same way (S1). Every compiled formula goes through lib.formula.check_formula; depth over the limit, a refused
function, a typed number other than the allowed ones, a plain A1 reference or a defined name refuses the spec
(exit 2) and names the part to move; length over the limit is a warning (design 6.6, 8). openpyxl is imported
inside functions only (design 19).

Layout (written down in references/model-spec-format.md):
- Sheets in type order cover, inputs, raw, helpers, calc, output, checks, notes; within a type in spec order.
- Block sheets: column A spacer (width 2), B row headers, C units, D the first value column; B1 the title,
  B2 "Master check" and C2 the master check (the formula on the checks sheet, a link elsewhere), row 3 blank,
  the first block from row 4: heading row, column-header row, body rows, optional total row, one blank row.
- Raw sheet raw_<t>: the contract's columns from A1, values only, Excel table tbl_raw_<t> without a style.
- Helper sheet h_<t>: row r belongs to raw row r: A row, B in_raw, the fetched raw columns, a null flag
  <column>_null for each fetched number, integer, date or datetime column whose null rule is allowed or whose
  contract agrees known_nulls for it (spec.null_flag_columns), the derived columns, then dup (key
  check: 1 on a later copy of a key, compared with EXACT; for a key of several columns after dup_key, the parts
  joined by '|') when the table has at most formula_key_check_max_rows rows and no part of a joined key holds '|'.
  The helper rows equal the landed rows (a capacity above them is refused); the check <t>_beyond counts helper
  rows below the last raw row. A table with zero landed rows gets zero helper rows (capacity 0, last_row 1): the
  sheet holds its header row only and the manifest keeps each column's row-2 formula for rotm.py extend (v3).
- Checks sheet: header row 4, error checks from row 5 (B id, C label, D actual, E expected or minimum,
  F maximum, G difference, H tolerance, I result, J what it checks), then the alerts, then the checks that only
  rotm.py check runs. Columns D to H are as wide as the widest value they show (v3, J-1), at least 12.
- Warnings of v3: W-SELF-CHECK (a specification check whose two sides are the same, A-aera-19).
- The manifest's raw entries record contract_sha256 (the agreed contract built from) and workbook_digest (the
  rows build wrote) (v3, A-aera-1, A-aera-4).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import math
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import common

# ----------------------------------------------------------------------------- layout constants (design 6.3, 9.1)

LABEL_COL = 2            # B: row headers
UNIT_COL = 3             # C: units (FAST 3.05)
FIRST_VALUE_COL = 4      # D: first value column on every block sheet (FAST 1.01-02)
TITLE_ROW = 1
MASTER_ROW = 2
FIRST_BLOCK_ROW = 4
HELPER_FIRST_ROW = 2     # helper row r reads raw row r (design 7.1)
CHECK_HEADER_ROW = 4
CHECK_FIRST_ROW = 5
CHK = {"id": 2, "label": 3, "actual": 4, "expected": 5, "max": 6, "diff": 7, "tol": 8, "result": 9, "target": 10}
CHECK_HEADERS = ("Id", "What it checks", "Actual", "Expected or minimum", "Maximum", "Difference", "Tolerance",
                 "Result", "Checks")
ROW_MARK = "1048575"      # the row number in a helper column's formula template (a row no model reaches; any
                          # typed number other than 0 and 1 is refused, so it cannot occur in an expression)
DATE_FORMATS = ("date", "month", "datetime")
# v5 (J-2): the Difference cell of a check whose values are dates: whole days (count), or days with their fraction
CHECK_DIFF_FORMAT = {"date": "count", "month": "count", "datetime": "amount2"}
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
GENERATED_IDS_RE = r"^[a-z][a-z0-9_]{0,23}$"
REFUSED_ISSUES = ("F-DEPTH", "F-EXCEL-LIMIT", "F-NESTED-IF", "F-FUNC-REFUSED", "F-EXTERNAL", "F-REF-ERROR",
                  "F-LITERAL", "F-PARSE", "F-NAME", "X-STRUCTREF",
                  "F-ARGS")   # v6 (r5 verdict A-aera-6): a listed function with too few or too many arguments;
                              # build prints the issue's own fix (formula.ARG_COUNTS)
DEFAULT_FORMAT_BY_INPUT_TYPE = {"date": "date", "integer": "count", "number": "general", "text": "text",
                                "boolean": "general"}
# Display widths in characters of the Normal font (Arial 9) for a formula cell whose value is not known yet.
FORMAT_WIDTH = {"amount": 12, "amount1": 13, "amount2": 14, "rate": 7, "rate2": 8, "factor": 8, "count": 9,
                "integer": 8, "date": 10, "datetime": 19, "month": 8, "text": 10, "flag": 4, "check": 6,
                "general": 11, "date_or_empty": 10, "datetime_or_empty": 19}
# v6 (r5 verdict J-1): the fetch of a date or datetime column that the contract allows to be empty shows 0 (an empty
# raw cell) as an empty cell, not as 1900-01-00; the fetched value stays 0 and calculations read the null flag
EMPTY_DATE_FORMATS = {"date": "date_or_empty", "datetime": "datetime_or_empty"}


@dataclass
class RefContext:
    """Where an expression sits, for placeholder resolution (design 6.6).

    `kind` is the context of spec.ALLOWED_IN (helpers, grid, items, measures, check) or "header" for generated
    header cells. `row` is a sheet row number, or ROW_MARK in a helper column template. `ri` and `ci` are the
    0-based body row and value column of a block cell; `ri` is None on a total row."""
    sheet: str
    row: int | str
    col: int
    block: str | None = None
    item: str | None = None
    helper_table: str | None = None
    kind: str = "grid"
    ri: int | None = 0
    ci: int | None = 0
    measure: str | None = None
    check: str | None = None
    path: str = ""


@dataclass
class CompileResult:
    workbook: Any                                   # openpyxl Workbook, not saved
    manifest: dict[str, Any]
    findings: list[Any] = field(default_factory=list)   # common.Finding (warnings and info)
    layout: dict[str, Any] = field(default_factory=dict)


class _F(str):
    """A formula build wrote. Any other text that starts with '=' is stored as text, not as a formula."""


_ILLEGAL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _store(cell: Any, value: Any) -> None:
    """Write a value: a _F as a formula; other text as text, even when it starts with '='."""
    if isinstance(value, str) and not isinstance(value, _F):
        value = _ILLEGAL_RE.sub("", value)
        if value == "":
            cell.value = None           # an empty label or unit: the cell keeps its style and holds nothing
            return
        cell.value = value
        if value.startswith("="):
            cell.data_type = "s"
        return
    cell.value = value


def _store_text(cell: Any, value: Any) -> None:
    """Write a value of a raw text column as a text cell whatever it looks like: an error name such as #N/A, a
    leading '=', digits such as 0012 or the word TRUE stay text (v2, A-aera-10; openpyxl would store #N/A as an
    error value and =1+1 as a formula)."""
    text = _ILLEGAL_RE.sub("", value if isinstance(value, str) else str(value))
    if text == "":
        cell.value = None
        return
    cell.value = text
    cell.data_type = "s"


class _Problem(Exception):
    """One problem of a spec at a JSON path; collected and raised together as one common.Refusal."""

    def __init__(self, path: str, message: str, fix: str = ""):
        super().__init__(message)
        self.path, self.message, self.fix = path, message, fix

    def line(self) -> str:
        text = f"{self.path}: {self.message}" if self.path else self.message
        if self.fix:
            text += (" " if text.rstrip().endswith((".", ";", ":")) else ". ") + f"Fix: {self.fix}"
        return text


# ----------------------------------------------------------------------------- small helpers

def _L(c: int) -> str:
    return common.col_letter(c)


def _prefix(target_sheet: str, host_sheet: str) -> str:
    """'calc!' for another sheet; '' for the host sheet (FAST 3.04-02 to 3.04-07, platforms C1.44; the individual number not confirmed)."""
    return "" if target_sheet == host_sheet else common.quote_sheet(target_sheet) + "!"


def _abs(c: int, r: int | str) -> str:
    return f"${_L(c)}${r}"


COVER_LINE = 100      # characters per cover line (purpose, limitations); fits columns B to E of the cover


def _wrap_line_count(text: str, capacity: float) -> int:
    """Lines a wrapped cell needs: words placed greedily on lines of `capacity` characters; a word longer than a
    line takes as many lines as it fills."""
    lines, cur = 1, 0.0
    for word in str(text).split():
        w = float(_text_width(word))
        if cur == 0:
            cur = w
        elif cur + 1 + w <= capacity:
            cur += 1 + w
        else:
            lines += 1
            cur = w
        while cur > capacity:
            lines += 1
            cur -= capacity
    return lines


def _cover_lines(text: str) -> list[str]:
    """`text` as lines of at most COVER_LINE characters, broken between words."""
    import textwrap
    return textwrap.wrap(str(text), COVER_LINE, break_long_words=False, break_on_hyphens=False) or [""]


def _text_width(text: str) -> int:
    """Characters as displayed: East Asian wide and full-width characters count 2."""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in str(text))


def _is_iso_date(value: Any) -> bool:
    return isinstance(value, str) and bool(ISO_DATE_RE.match(value))


def _to_date(value: str) -> _dt.datetime:
    return _dt.datetime.strptime(value, "%Y-%m-%d")


def _header_value(value: Any, fmt: str | None) -> Any:
    """Typed header and list values: ISO date text becomes a date unless the format is text (design 6.4)."""
    if _is_iso_date(value) and fmt != "text":
        try:
            return _to_date(value)
        except ValueError:
            return value
    return value


def _is_text_input(it: dict[str, Any]) -> bool:
    """A declared text input (v5, A-aera-17): "type": "text", or "format": "text"."""
    return it.get("type") == "text" or it.get("format") == "text"


def _is_tolerance(v: Any) -> bool:
    """A tolerance build accepts (v5, A-aera-3 and C-sol-5): a finite number of 0 or more (not true/false)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)) and v >= 0


def _shown(v: Any) -> str:
    if v is None:
        return "nothing"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return _num_text(v) if math.isfinite(float(v)) else str(v)
    return f"'{v}'"


def _num_text(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else repr(float(x))


CHECK_VALUE_MIN_WIDTH = 12.0     # checks sheet D:H, the narrowest width (v3, J-1: before v3 this width was fixed)


def _same_check_side(formula_mod: Any, a: str, b: str, host: str) -> bool:
    """True when two compiled check formulas are the same expression (compared without '=', '$', spaces,
    _xlfn. prefixes and case) or both are a link to the same cell (v3, A-aera-19, W-SELF-CHECK)."""
    def norm(f: str) -> str:
        text = formula_mod.strip_xlfn(str(f)).lstrip("=")
        return re.sub(r"[\s$]", "", text).upper()

    if norm(a) == norm(b):
        return True
    try:
        fa, fb = formula_mod.analyze(str(a)), formula_mod.analyze(str(b))
    except Exception:  # noqa: BLE001 - a formula the analyser cannot read is compared as text only
        return False
    if not (fa.is_pure_link and fb.is_pure_link and fa.refs and fb.refs):
        return False

    def where(ref: Any) -> tuple[str, str]:
        return ((ref.sheet or host).lower(), str(ref.a1).replace("$", "").upper())

    return where(fa.refs[0]) == where(fb.refs[0])


def _display_width(value: Any, fmt: str | None) -> float:
    """Characters a known number takes when Excel shows it in the dictionary format `fmt` (thousands
    separators, decimals, the sign or the brackets of a negative amount), plus 2 for the cell's padding (v3, J-1).
    0 for None."""
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 7.0
    if isinstance(value, (_dt.datetime, _dt.date)):
        return float(FORMAT_WIDTH.get(fmt or "date", 10)) + 1
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return float(_text_width(str(value)) + 1)
    v = float(value)
    key = fmt or "general"
    if key in ("amount", "amount1", "amount2"):
        places = {"amount": 0, "amount1": 1, "amount2": 2}[key]
        text = f"{abs(v):,.{places}f}"
        return float(len(text) + 2 + (1 if v < 0 else 0))          # (1,234) for a negative, 1,234_ otherwise
    if key == "count":
        text = f"{round(v):,}"
    elif key in ("integer", "flag", "check"):
        text = str(round(v))
    elif key in ("rate", "rate2"):
        text = f"{v * 100:.{1 if key == 'rate' else 2}f}%"
    elif key == "factor":
        text = f"{v:.4f}"
    elif key in DATE_FORMATS:
        return float(FORMAT_WIDTH.get(key, 10)) + 1
    else:                                                           # General: at most 11 characters are shown
        text = _num_text(v)
        if len(text) > 11:
            text = text[:11]
    return float(len(text) + 2)


def _map_span(start: int, end: int, segs: list[tuple[int, int, int, int]], src_len: int) -> tuple[int, int]:
    """Offsets in a resolved body -> offsets in the spec expression (placeholders map to their whole text)."""
    def one(pos: int, is_end: bool) -> int:
        delta = 0
        for o1, o2, s1, s2 in segs:
            if pos < o1 or (is_end and pos == o1):
                break
            if o1 <= pos < o2 or (is_end and pos == o2):
                return s2 if is_end else s1
            delta = s2 - o2
        return max(0, min(src_len, pos + delta))
    return one(start, False), one(end, True)


# ----------------------------------------------------------------------------- style application

class _Styler:
    """Applies the dictionary through lib.styles (M2). When lib.styles is not written yet, an equivalent local
    implementation is used (design 10.5: same named-style-free result: font, size, colour, bold, fill, border,
    number format, alignment, protection), and the build report says so."""

    def __init__(self, dictionary: Any):
        self.d = dictionary
        self.fallback = False
        self._cache: dict[tuple, tuple] = {}
        self.note: str | None = None
        from . import styles
        self._styles = styles

    def register(self, wb: Any) -> None:
        try:
            self._styles.register_named_styles(wb, self.d)
        except NotImplementedError:
            self.fallback = True
            self.note = ("styles applied by build's own copy of the dictionary rules (lib/styles.py is not written "
                         "yet)")

    def apply(self, cell: Any, cell_type: str, size: int, format_key: str | None) -> None:
        if not self.fallback:
            try:
                self._styles.apply(cell, self.d, cell_type, size=size, format_key=format_key)
                return
            except NotImplementedError:
                self.fallback = True
                self.note = ("styles applied by build's own copy of the dictionary rules (lib/styles.py is not "
                             "written yet)")
        self._apply_fallback(cell, cell_type, size, format_key)

    def _apply_fallback(self, cell: Any, cell_type: str, size: int, format_key: str | None) -> None:
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Protection, Side
        st = self.d.style(cell_type)
        v = cell.value
        is_num = isinstance(v, (int, float, _dt.date, _dt.datetime)) and not isinstance(v, bool)
        key = (cell_type, size, format_key, is_num if st.align == "type" else None)
        objs = self._cache.get(key)
        if objs is None:
            color = ("FF" + st.font_color) if st.font_color else None
            font = Font(name=self.d.font_name, size=self.d.size_for(cell_type, size), bold=st.bold,
                        italic=st.italic, color=color)
            fill = PatternFill(fill_type="solid", fgColor="FF" + st.fill, bgColor="FF" + st.fill) if st.fill \
                else PatternFill(fill_type=None)
            sides = {}
            for side, spec in (st.border or {}).items():
                sides[side] = Side(style=spec["style"], color=("FF" + spec["color"]) if spec.get("color") else None)
            border = Border(**sides)
            if st.align == "type":
                horizontal = "right" if is_num else "left"
            elif st.align == "general":
                horizontal = None
            else:
                horizontal = st.align
            align = Alignment(horizontal=horizontal, vertical="top" if st.wrap else None, wrap_text=st.wrap)
            if st.number_format == "item":
                nf = self.d.number_format(format_key or "general")
            else:
                nf = st.number_format
            objs = (font, fill, border, align, nf, Protection(locked=st.locked))
            self._cache[key] = objs
        cell.font, cell.fill, cell.border, cell.alignment, cell.number_format, cell.protection = objs


# ----------------------------------------------------------------------------- the compiler

class _Compiler:
    def __init__(self, spec: Any, capacity: dict[str, int] | None):
        from . import formula
        self.spec = spec
        self.d = spec.dictionary
        self.settings = spec.settings
        self.formula = formula
        self.capacity_cli = dict(capacity or {})
        self.problems: list[_Problem] = []
        self.report = common.Report("build", str(spec.output))
        self.L: dict[str, Any] = {"inputs": {}, "raw": {}, "helpers": {}, "blocks": {}, "checks": {}, "cells": {},
                                  "names": {}, "controls": {}}
        self.types: dict[str, dict[tuple[int, int], str]] = {}     # sheet -> (row, col) -> cell type
        self.type_ranges: dict[str, list[list[str]]] = {}          # sheet -> [[range, type]] for large areas
        self.widths: dict[str, dict[int, float]] = {}
        self.heights: dict[str, dict[int, float]] = {}
        self.wrapped: dict[str, list[tuple[int, int, str]]] = {}   # sheet -> (row, col, text) of wrapped notes
        self.notes_col: dict[str, int] = {}                        # sheet -> the one notes column (FAST 2.04-02)
        self.used_types: set[str] = set()
        self.sizes: dict[str, int] = {s.name: s.font_size for s in spec.sheets}
        self.snapshots: dict[str, Any] = {}
        self.raw_formats: dict[str, dict[str, str]] = {}   # table -> column -> number-format key (raw_format())
        self.magnitude = 1.0
        self.formula_count = 0          # formulas checked (one per expression and cell position kind)
        self.formula_cells = 0          # formula cells written
        self._issue_seen: dict[tuple[str, str], common.Finding] = {}
        self._problem_seen: set[tuple[str, str]] = set()

    # ------------------------------------------------------------------ problems and findings
    def problem(self, path: str, message: str, fix: str = "") -> None:
        key = (path, message)
        if key not in self._problem_seen:
            self._problem_seen.add(key)
            self.problems.append(_Problem(path, message, fix))

    def raise_problems(self) -> None:
        if self.problems:
            n = len(self.problems)
            raise common.Refusal(f"{self.spec.path.name} has {n} problem{'s' if n != 1 else ''} that build cannot "
                                 "compile; nothing was built", details=[p.line() for p in self.problems],
                                 fix="change the keys named below (references/model-spec-format.md)")

    # ------------------------------------------------------------------ entry
    def compile(self) -> CompileResult:
        import openpyxl
        from openpyxl.styles import Font
        from openpyxl.utils.indexed_list import IndexedList
        self._check_settings()
        self._names()
        self._load_raw()
        self._layout_inputs()
        self._layout_raw_and_helpers()
        self._layout_blocks()
        self._layout_checks()
        self._layout_keys()
        self.raise_problems()

        wb = openpyxl.Workbook()
        # Normal style in the dictionary font at the spaced size: empty cells a user types into are Arial too,
        # and column widths are measured in this font (Excel's width unit is the Normal font's digit width).
        wb._fonts = IndexedList([Font(name=self.d.font_name, size=self.d.sizes["spaced"])])
        wb.remove(wb.active)
        self.wb = wb
        self.styler = _Styler(self.d)
        self.styler.register(wb)
        self.ws: dict[str, Any] = {}
        for s in self.spec.sheets:
            ws = wb.create_sheet(s.name)
            self.ws[s.name] = ws
            self.types[s.name] = {}
            self.widths[s.name] = {}
            self.heights[s.name] = {}
            self.wrapped[s.name] = []
        for s in self.spec.sheets:
            if s.type == "inputs":
                self._write_inputs(s)
            elif s.type == "raw":
                self._write_raw(s)
            elif s.type == "helpers":
                self._write_helpers(s)
            elif s.type in ("calc", "output"):
                self._write_block_sheet(s)
            elif s.type == "checks":
                self._write_checks(s)
            elif s.type == "notes":
                self._write_notes(s)
        self._block_check_warnings()
        for s in self.spec.sheets:
            if s.type == "cover":
                self._write_cover(s)
        self.raise_problems()
        for s in self.spec.sheets:
            self._finish_sheet(s)
        self._sparse_sheet_notes()
        wb.active = 0
        wb.calculation.fullCalcOnLoad = True      # Excel computes every formula when the file is opened
        wb.calculation.calcMode = "auto"
        wb.properties.creator = f"{common.TOOL} {common.VERSION}"
        wb.properties.title = self.spec.title[:250]
        if self.styler.note:
            self.report.note(self.styler.note)
        manifest = self._manifest()
        return CompileResult(workbook=wb, manifest=manifest, findings=list(self.report.findings), layout=self.L)

    # ------------------------------------------------------------------ settings
    def _check_settings(self) -> None:
        st = self.settings
        if st["depth_limit"] < 2:
            self.problem("settings.depth_limit", f"depth limit {st['depth_limit']} is below 2, the depth of the master "
                         "check build writes (OR(SUM(...)>0, ...))", "use a depth limit of 2 or more")
        allowed = {abs(float(x)) for x in st["allowed_literals"]}
        if not {0.0, 1.0} <= allowed:
            self.problem("settings.allowed_literals", "allowed_literals must include 0 and 1: the check formulas "
                         "build writes use them (=COUNTA(...)-1, =--(D5<>0))", "add 0 and 1 to allowed_literals")

    def _names(self) -> None:
        names = self.L["names"]
        for st in ("cover", "inputs", "checks"):
            sheets = self.spec.sheets_of(st)
            names[st] = sheets[0].name if sheets else st

    # ------------------------------------------------------------------ raw data
    def _load_raw(self) -> None:
        from . import land
        for table, sheet in self.spec.raw.items():
            contract = self.spec.contracts.get(table)
            if contract is None:
                self.problem(f"{sheet.path}.table", f"raw table '{table}' has no readable contract",
                             f"propose and agree contracts/{table}.contract.json first")
                continue
            try:
                snap = land.read_snapshot(self.spec.root, table)
            except common.Refusal as exc:
                self.problem(f"{sheet.path}.table", exc.message, exc.fix or "")
                continue
            self.snapshots[table] = snap
            for ci, t in enumerate(snap.types):
                if t in ("integer", "number"):
                    total = math.fsum(abs(r[ci]) for r in snap.rows if isinstance(r[ci], (int, float)))
                    self.magnitude = max(self.magnitude, total)
            cols = {c["name"]: c for c in contract["columns"]}
            self.raw_formats[table] = {name: self._data_format(cols.get(name, {"type": t}), snap, ci)
                                       for ci, (name, t) in enumerate(zip(snap.columns, snap.types))}

    def _data_format(self, col: dict[str, Any], snap: Any, ci: int) -> str:
        """The number-format key of one raw column (raw sheet, helper fetch, control total): the contract column's
        "format" when it names one; else, for a number column whose landed values hold a fraction, two decimals
        (amount2, BOOKS B-44) when no value has more than two, General when one has more, so 7.25 is not shown
        as 7 (live test 1, 2026-11-25); else the dictionary's format for the column type."""
        if col.get("format"):
            return col["format"]
        ctype = col.get("type") or "text"
        if ctype == "number":
            places = 0
            for r in snap.rows:
                v = r[ci] if ci < len(r) else None
                if isinstance(v, float) and math.isfinite(v) and not v.is_integer():
                    text = format(v, ".15g")
                    if "e" in text or "E" in text:
                        places = 3
                    else:
                        places = max(places, len(text.split(".", 1)[1]) if "." in text else 0)
                    if places > 2:
                        break
            if places > 2:
                return "general"
            if places > 0:
                return "amount2"
        return self.d.format_for_column_type(ctype)

    def _key_match_unsafe(self, table: str, keys: list[str]) -> str | None:
        """Why the dup column would not compare this table's landed keys exactly, or None. The dup column compares
        with EXACT (exact text, case-sensitive, no wildcard, text never read as a number: the one key equality of
        common.key_tuple, v2 A-aera-9); the joined key of a key of several columns puts '|' between the parts, so a
        part that holds '|' could join two different keys into one text. Such tables are key-checked by rotm.py
        check only."""
        snap = self.snapshots.get(table)
        if snap is None or len(keys) < 2:
            return None
        idx = [snap.columns.index(k) for k in keys if k in snap.columns]
        for r in snap.rows:
            for i in idx:
                v = r[i] if i < len(r) else None
                if isinstance(v, str) and "|" in v:
                    return (f"a key value of raw_{table} ({v[:30]!r}) holds '|', the separator of the joined "
                            "key")
        return None

    def _accepted_totals(self, table: str) -> dict[str, dict[str, Any]] | None:
        """The control totals accepted for the landed snapshot of `table` (raw/<table>.controls.json, written by
        check --accept-refresh --control; lib.controls), by column; None when the snapshot has no record (it is
        the one first landed, so the contract's totals apply)."""
        snap = self.snapshots.get(table)
        if snap is None or not getattr(snap, "digest", None):
            return None
        from . import controls
        try:
            found = controls.totals_for(self.spec.root, table, snap.digest)
        except common.Refusal as exc:
            self.problem(f"raw/{table}.controls.json", exc.message, exc.fix or "")
            return None
        if found is None:
            return None
        return {str(t["column"]): dict(t) for t in found}

    def raw_format(self, table: str, col: dict[str, Any]) -> str:
        """The format key build gives raw column `col` of `table` (recorded in the manifest as "formats")."""
        found = self.raw_formats.get(table, {}).get(col.get("name", ""))
        if found:
            return found
        return col.get("format") or self.d.format_for_column_type(col.get("type") or "text")

    # ------------------------------------------------------------------ layout: inputs
    def _layout_inputs(self) -> None:
        name = self.L["names"]["inputs"]
        sheet = self.spec.sheet(name)
        items = sheet.data.get("items", []) if sheet else []
        r = FIRST_BLOCK_ROW
        self.input_blocks: list[dict[str, Any]] = []
        scalars = [(i, it) for i, it in enumerate(items) if it.get("kind", "scalar") == "scalar"]
        if scalars:
            blk = {"kind": "scalars", "title": "Assumptions", "heading": r, "header": r + 1, "rows": []}
            r += 2
            for i, it in scalars:
                self.L["inputs"][it["id"]] = {"kind": "scalar", "sheet": name, "row": r, "col": FIRST_VALUE_COL,
                                              "item": it, "path": f"{sheet.path}.items[{i}]"}
                blk["rows"].append((r, it))
                r += 1
            self.input_blocks.append(blk)
            r += 1
        for i, it in enumerate(items):
            kind = it.get("kind", "scalar")
            path = f"{sheet.path}.items[{i}]"
            if kind == "list":
                blk = {"kind": "list", "title": it["label"], "heading": r, "header": r + 1, "item": it}
                first = r + 2
                last = first + len(it["values"]) - 1
                self.L["inputs"][it["id"]] = {"kind": "list", "sheet": name, "first": first, "last": last,
                                              "col": FIRST_VALUE_COL, "item": it, "path": path}
                blk["first"], blk["last"] = first, last
                self.input_blocks.append(blk)
                r = last + 2
            elif kind == "grid":
                blk = {"kind": "grid", "title": it["label"], "heading": r, "header": r + 1, "item": it}
                first = r + 2
                last = first + len(it["rows"]["values"]) - 1
                ncols = len(it["cols"]["values"])
                self.L["inputs"][it["id"]] = {"kind": "grid", "sheet": name, "first": first, "last": last,
                                              "header": r + 1, "c1": FIRST_VALUE_COL,
                                              "c2": FIRST_VALUE_COL + ncols - 1, "item": it, "path": path}
                blk["first"], blk["last"] = first, last
                self.input_blocks.append(blk)
                r = last + 2
        widest = max([FIRST_VALUE_COL] + [e["c2"] for e in self.L["inputs"].values() if e["kind"] == "grid"])
        self.notes_col[name] = widest + 1
        # contract controls and the check tolerance (design 6.4)
        blk = {"kind": "controls", "title": "Contract controls", "heading": r, "header": r + 1, "rows": []}
        r += 2
        ids = set(self.L["inputs"])
        for table, rsheet in self.spec.raw.items():
            contract = self.spec.contracts.get(table)
            if contract is None:
                continue
            expect = contract.get("expect", {})
            rows = expect.get("rows")
            src = f"contract {table}, expect"
            entries = []
            if rows is not None:
                if "exact" in rows:
                    entries.append((f"{table}_rows_exact", f"{table}: rows expected", rows["exact"], "rows",
                                    src + ".rows.exact", "count"))
                else:
                    entries.append((f"{table}_rows_min", f"{table}: fewest rows", rows["min"], "rows",
                                    src + ".rows.min", "count"))
                    entries.append((f"{table}_rows_max", f"{table}: most rows", rows["max"], "rows",
                                    src + ".rows.max", "count"))
            cols = {c["name"]: c for c in contract["columns"]}
            accepted = self._accepted_totals(table)
            for t in expect.get("totals", []):
                col = cols.get(t["column"], {})
                unit = col.get("unit") or ""
                tfmt = self.raw_format(table, col) if col.get("type") else "general"
                value, tol, tsrc = t["value"], t.get("tolerance", 0), src + ".totals"
                rec = accepted.get(t["column"]) if accepted is not None else None
                if rec is not None:
                    # v2, A-aera-7: the total accepted with this snapshot (check --accept-refresh --control);
                    # v5, A-aera-6 and C-sol-3: or the source total recorded by land --csv ... --control
                    value, tol = rec["value"], rec.get("tolerance", tol)
                    tsrc = (f"raw/{table}.controls.json, recorded for this snapshot by rotm.py check "
                            f"--accept-refresh or rotm.py land --control (the contract's agreed total: "
                            f"{_num_text(t['value'])})")
                entries.append((f"{table}_total_{t['column']}", f"{table}: control total of {t['column']}",
                                value, unit, tsrc, tfmt))
                entries.append((f"{table}_tol_{t['column']}", f"{table}: tolerance of that total",
                                tol, unit, tsrc + " tolerance" if rec is not None else src + ".totals.tolerance",
                                "general"))
            # known exceptions the user agreed in the contract (references/column-contract.md, "Known exceptions")
            if isinstance(expect.get("key_repeats"), dict):
                entries.append((f"{table}_key_repeats", f"{table}: rows repeating a key, agreed as known",
                                expect["key_repeats"]["max"], "rows", src + ".key_repeats.max", "count"))
            for c in contract["columns"]:
                if c.get("null_rule") == "never" and isinstance(c.get("known_nulls"), dict):
                    entries.append((f"{table}_known_nulls_{c['name']}",
                                    f"{table}: empty {c['name']} cells, agreed as known", c["known_nulls"]["max"],
                                    "cells", f"contract {table}, columns.{c['name']}.known_nulls.max", "count"))
            for cid, label, value, unit, source, fmt in entries:
                if cid in ids:
                    # v5 (J-1): both places named; the control comes from the contract
                    ipath = self.L["inputs"].get(cid, {}).get("path") or "sheets"
                    head = f"contract {table}, "
                    where = (f"contracts/{table}.contract.json {source[len(head):]}" if source.startswith(head)
                             else source)
                    self.problem(f"{ipath}.id" if ipath != "sheets" else ipath,
                                 f"input id '{cid}' is the id build gives a contract control of table '{table}' "
                                 f"(from {where})",
                                 "rename the input: build names the contract controls of the inputs sheet "
                                 f"<table>_rows_exact, <table>_rows_min, <table>_rows_max, <table>_total_<column>, "
                                 "<table>_tol_<column>, <table>_key_repeats and <table>_known_nulls_<column>")
                    continue
                ids.add(cid)
                self.L["inputs"][cid] = {"kind": "scalar", "sheet": name, "row": r, "col": FIRST_VALUE_COL,
                                         "item": {"id": cid, "label": label, "value": value}, "generated": True}
                self.L["controls"][cid] = r
                blk["rows"].append((r, {"id": cid, "label": label, "value": value, "unit": unit, "source": source,
                                        "format": fmt}))
                r += 1
        tol_source = ("spec settings.check_tolerance" if "check_tolerance" in (self.spec.data.get("settings") or {})
                      else "dictionary rules.check_tolerance (" + (self.d.source("rules.check_tolerance") or "PLUGIN")
                      .split(":")[0].split("(")[0].strip() + ")")
        self.L["inputs"]["check_tolerance"] = {"kind": "scalar", "sheet": name, "row": r, "col": FIRST_VALUE_COL,
                                               "item": {"id": "check_tolerance"}, "generated": True}
        blk["rows"].append((r, {"id": "check_tolerance", "label": "Check tolerance",
                                "value": self.settings["check_tolerance"],
                                "unit": "unit of the values checked", "source": tol_source,
                                "format": "general"}))
        self.input_blocks.append(blk)

    # ------------------------------------------------------------------ layout: raw and helpers
    def _layout_raw_and_helpers(self) -> None:
        maxrows = int(self.settings["formula_key_check_max_rows"])
        spare = int(self.settings["helper_spare_rows"])
        for table in self.capacity_cli:
            if table not in self.spec.raw:
                self.problem("--capacity", f"table '{table}' has no raw sheet in the spec",
                             "use TABLE=ROWS with a table of the spec: " + ", ".join(self.spec.raw) if self.spec.raw
                             else "remove --capacity")
        for table, sheet in self.spec.raw.items():
            snap = self.snapshots.get(table)
            contract = self.spec.contracts.get(table)
            if snap is None or contract is None:
                continue
            cols = {c: _L(i + 1) for i, c in enumerate(snap.columns)}
            self.L["raw"][table] = {"sheet": sheet.name, "cols": cols, "rows": len(snap.rows),
                                    "last_col": len(snap.columns)}
        for table, hsheet in self.spec.helpers.items():
            raw = self.L["raw"].get(table)
            contract = self.spec.contracts.get(table)
            if raw is None or contract is None:
                continue
            landed = raw["rows"]
            refresh = contract.get("refresh") or {}
            if table in self.capacity_cli:
                capacity = self.capacity_cli[table]
                source = "--capacity"
                where, fix = "--capacity", f"remove --capacity {table}={capacity}"
            elif refresh.get("capacity_rows"):
                capacity = int(refresh["capacity_rows"])
                source = "contract refresh.capacity_rows"
                where = f"contract {table}, refresh.capacity_rows"
                fix = (f"remove capacity_rows from contracts/{table}.contract.json (propose the contract again "
                       "without --capacity and get the user's agreement)")
            else:
                capacity = landed + spare
                source = "landed rows" + (f" + helper_spare_rows {spare}" if spare else "")
                where, fix = "settings.helper_spare_rows", "set helper_spare_rows to 0 (remove it from the settings)"
            # v3, A-aera-8: a raw table with zero landed rows gets zero helper rows (capacity 0, last_row 1); the
            # row-2 formula of each helper column is kept in the manifest, and rotm.py extend writes it when a
            # later refresh returns rows. One or more landed rows keep at least one helper row.
            capacity = int(capacity) if landed == 0 else max(1, int(capacity))
            if capacity > landed:
                # v2, A-aera-6: a helper row below the last raw row reads empty raw cells as 0, so a helper such as
                # --({this:amount}=0) counts it. The helper rows equal the landed rows; after a refresh,
                # rotm.py extend sizes them to the raw rows again (it adds rows and clears rows below the last).
                self.problem(where, f"h_{table} would cover {capacity:,} rows but raw_{table} holds {landed:,} "
                             f"landed rows ({source}); a helper row below the last raw row reads empty raw cells "
                             "as 0 and would feed the calculations",
                             f"{fix}; after a refresh, rotm.py extend sizes the helper rows to the raw rows")
                capacity = landed                  # the layout goes on, so every other problem is found too
            if capacity < landed:
                self.report.warning("W-CAPACITY", f"h_{table} covers {capacity:,} rows but raw_{table} holds "
                                    f"{landed:,} landed rows ({source}); rows {capacity + 2:,} to {landed + 1:,} are "
                                    "not covered and the check " + f"{table}_cover fails",
                                    f"build with --capacity {table}={landed} or more", sheet=hsheet.name)
            ctypes = {c["name"]: c["type"] for c in contract["columns"]}
            fetch = hsheet.data.get("fetch")
            fetched = list(fetch) if fetch is not None else [c["name"] for c in contract["columns"]]
            columns: list[dict[str, Any]] = [{"id": "row", "kind": "row"}, {"id": "in_raw", "kind": "flag"}]
            for name in fetched:
                columns.append({"id": name, "kind": "fetch", "raw_column": name, "type": ctypes.get(name)})
            from . import spec as specmod
            for fid, name in specmod.null_flag_columns(contract, fetched):
                columns.append({"id": fid, "kind": "nullflag", "raw_column": name, "header": f"{name} empty"})
            for ci, col in enumerate(hsheet.data.get("columns", [])):
                columns.append({"id": col["id"], "kind": "derived", "spec": col,
                                "path": f"{hsheet.path}.columns[{ci}]"})
            keys = list(contract["key"])
            dup_reason = None
            if landed > maxrows:
                dup_reason = (f"raw_{table} has {landed:,} rows, more than formula_key_check_max_rows {maxrows:,}; "
                              "a per-row SUMPRODUCT(EXACT(...)) grows with the square of the row count")
            elif any(k not in fetched for k in keys):
                missing = [k for k in keys if k not in fetched]
                dup_reason = f"key column {', '.join(missing)} is not fetched on h_{table}"
            else:
                dup_reason = self._key_match_unsafe(table, keys)
            if dup_reason is None:
                if len(keys) > 1:
                    columns.append({"id": "dup_key", "kind": "dupkey", "keys": keys})
                columns.append({"id": "dup", "kind": "dup", "keys": keys})
            for i, c in enumerate(columns):
                c["col"] = _L(i + 1)
            self.L["helpers"][table] = {"sheet": hsheet.name, "columns": columns,
                                        "cols": {c["id"]: c["col"] for c in columns}, "capacity": capacity,
                                        "first_row": HELPER_FIRST_ROW, "last_row": HELPER_FIRST_ROW + capacity - 1,
                                        "dup_reason": dup_reason, "capacity_source": source}

    # ------------------------------------------------------------------ layout: blocks
    def _dim(self, block_id: str, dim: str, path: str, seen: tuple[str, ...] = ()) -> dict[str, Any] | None:
        """{"kind": values|in|timeline|same_as|items|measures|value, "n": count, ...} of a block's rows or cols."""
        b = self.spec.blocks.get(block_id)
        if b is None or block_id in seen:
            return None
        data, shape = b["data"], b["shape"]
        if dim == "rows" and shape == "items":
            return {"kind": "items", "n": len(data["items"])}
        if dim == "cols" and shape == "measures":
            return {"kind": "measures", "n": len(data["measures"])}
        node = data.get(dim)
        if node is None:
            return {"kind": "value", "n": 1} if dim == "cols" else None
        if "values" in node:
            return {"kind": "values", "n": len(node["values"]), "node": node}
        if "in" in node:
            inp = self.spec.inputs.get(node["in"])
            n = len(inp["item"]["values"]) if inp and inp["kind"] == "list" else 0
            return {"kind": "in", "n": n, "node": node}
        if "timeline" in node:
            return {"kind": "timeline", "n": int(node["timeline"]["periods"]), "node": node}
        if "same_as" in node:
            inner = self._dim(node["same_as"], dim, path, seen + (block_id,))
            if inner is None:
                return None
            return {"kind": "same_as", "n": inner["n"], "node": node, "target": node["same_as"]}
        return None

    def _layout_blocks(self) -> None:
        for s in self.spec.sheets:
            if s.type not in ("calc", "output"):
                continue
            r = FIRST_BLOCK_ROW
            for bi, block in enumerate(s.data["blocks"]):
                bid = block["id"]
                info = self.spec.blocks.get(bid)
                if info is None or info["sheet"] != s.name:
                    continue
                shape = info["shape"]
                path = f"{s.path}.blocks[{bi}]"
                rows = self._dim(bid, "rows", path)
                cols = self._dim(bid, "cols", path)
                if rows is None or cols is None or rows["n"] < 1 or cols["n"] < 1:
                    self.problem(path, f"block '{bid}' has no rows or no columns", "give it rows and cols")
                    r += 4
                    continue
                entry: dict[str, Any] = {"id": bid, "sheet": s.name, "shape": shape, "path": path, "data": block,
                                         "rows_dim": rows, "cols_dim": cols, "heading_row": r, "header_row": r + 1,
                                         "first_col": FIRST_VALUE_COL, "n_rows": rows["n"], "n_cols": cols["n"]}
                unit_row = None
                if shape == "measures" and any(m.get("unit") for m in block["measures"]):
                    unit_row = r + 2
                entry["unit_row"] = unit_row
                first = (unit_row or r + 1) + 1
                entry["first_row"] = first
                entry["last_row"] = first + rows["n"] - 1
                entry["last_col"] = FIRST_VALUE_COL + cols["n"] - 1
                total = block.get("total") or {}
                has_total_row = (shape == "grid" and bool(total.get("row"))) or \
                    (shape == "measures" and any(m.get("total", "none") != "none" for m in block["measures"]))
                entry["total_row"] = entry["last_row"] + 1 if has_total_row else None
                entry["total_col"] = entry["last_col"] + 1 if shape == "grid" and total.get("col") else None
                if shape == "items":
                    entry["items"] = {it["id"]: first + k for k, it in enumerate(block["items"])}
                if shape == "measures":
                    entry["measures"] = {m["id"]: FIRST_VALUE_COL + k for k, m in enumerate(block["measures"])}
                end_col = entry["total_col"] or entry["last_col"]
                inner = block.get("items") or block.get("measures") or []
                has_notes = bool(block.get("note") or block.get("source") or any(x.get("note") for x in inner))
                entry["notes_col"] = end_col + 1 if has_notes else None
                self.L["blocks"][bid] = entry
                self.notes_col[s.name] = max(self.notes_col.get(s.name, 0), end_col + 1)
                r = (entry["total_row"] or entry["last_row"]) + 2

    # ------------------------------------------------------------------ layout: checks
    def _layout_checks(self) -> None:
        name = self.L["names"]["checks"]
        rows: list[dict[str, Any]] = []
        alerts: list[dict[str, Any]] = []
        outside: list[dict[str, Any]] = []
        ins = self.L["inputs"]

        def iref(cid: str) -> str:
            e = ins[cid]
            return _prefix(e["sheet"], name) + _abs(e["col"], e["row"])

        def ival(cid: str) -> Any:
            return ((ins.get(cid) or {}).get("item") or {}).get("value")

        for table, rsheet in self.spec.raw.items():
            raw = self.L["raw"].get(table)
            contract = self.spec.contracts.get(table)
            if raw is None or contract is None:
                continue
            rs = common.quote_sheet(raw["sheet"]) + "!"
            col_a = f"{rs}$A:$A"
            expect = contract.get("expect", {})
            n_rows = raw["rows"]
            if "rows" in expect:
                if "exact" in expect["rows"]:
                    rows.append({"id": f"{table}_rows", "kind": "equal", "auto": True,
                                 "label": f"Rows of raw_{table} equal the contract's row count",
                                 "actual": f"COUNTA({col_a})-1", "expected": iref(f"{table}_rows_exact"),
                                 "tolerance": "0", "targets": [raw["sheet"]], "format": "count",
                                 "known": [n_rows, ival(f"{table}_rows_exact")], "known_tol": [0]})
                else:
                    rows.append({"id": f"{table}_rows", "kind": "range", "auto": True,
                                 "label": f"Rows of raw_{table} lie within the contract's range",
                                 "actual": f"COUNTA({col_a})-1", "min": iref(f"{table}_rows_min"),
                                 "max": iref(f"{table}_rows_max"), "targets": [raw["sheet"]], "format": "count",
                                 "known": [n_rows, ival(f"{table}_rows_min"), ival(f"{table}_rows_max")]})
            snap = self.snapshots.get(table)
            for t in expect.get("totals", []):
                letter = raw["cols"].get(t["column"])
                if letter is None:
                    continue
                # v3, J-1: the values this row shows are known at build time (the control total on the inputs
                # sheet and the landed column's sum), so the value columns are sized for them
                landed_sum = None
                if snap is not None and t["column"] in snap.columns:
                    ci = snap.columns.index(t["column"])
                    landed_sum = math.fsum(r[ci] for r in snap.rows if ci < len(r) and isinstance(r[ci], (int, float))
                                           and not isinstance(r[ci], bool))
                control = ival(f"{table}_total_{t['column']}")
                rows.append({"id": f"{table}_total_{t['column']}", "kind": "equal", "auto": True,
                             "label": f"Sum of raw_{table} {t['column']} equals the contract's control total",
                             "actual": f"SUM({rs}${letter}:${letter})",
                             "expected": iref(f"{table}_total_{t['column']}"),
                             "tolerance": iref(f"{table}_tol_{t['column']}"), "targets": [raw["sheet"]],
                             "format": next((self.raw_format(table, c)
                                             for c in contract["columns"] if c["name"] == t["column"]), None),
                             "known": [landed_sum, control,
                                       (landed_sum - control) if isinstance(landed_sum, (int, float))
                                       and isinstance(control, (int, float)) else None],
                             "known_tol": [ival(f"{table}_tol_{t['column']}")]})
            for c in contract["columns"][1:]:
                if c["null_rule"] != "never":
                    continue
                letter = raw["cols"].get(c["name"])
                if letter is None:
                    continue
                entry = {"id": f"{table}_nulls_{c['name']}", "kind": "zero", "auto": True,
                         "label": f"Empty cells in raw_{table} {c['name']} (never null by contract)",
                         "actual": f"COUNTA({col_a})-COUNTA({rs}${letter}:${letter})",
                         "targets": [raw["sheet"]], "format": "count"}
                known = c.get("known_nulls")
                if isinstance(known, dict) and f"{table}_known_nulls_{c['name']}" in ins:
                    entry.update({"kind": "at_most", "bound": iref(f"{table}_known_nulls_{c['name']}"),
                                  "label": f"Empty cells in raw_{table} {c['name']} (never null; at most "
                                           f"{known['max']:,} agreed as known)"})
                rows.append(entry)
            helper = self.L["helpers"].get(table)
            if helper is not None:
                hs = common.quote_sheet(helper["sheet"]) + "!"
                rows.append({"id": f"{table}_cover", "kind": "at_most", "auto": True,
                             "label": f"Raw rows of raw_{table} not covered by h_{table}",
                             "actual": f"COUNTA({col_a})-1-COUNT({hs}$A:$A)", "bound": "0",
                             "targets": [raw["sheet"], helper["sheet"]], "format": "count"})
                # v2, A-aera-6: a helper row below the last raw row (in_raw 0) reads empty raw cells as 0 and would
                # feed the calculations; it fails the master check until rotm.py extend clears it.
                rows.append({"id": f"{table}_beyond", "kind": "zero", "auto": True,
                             "label": f"Helper rows of h_{table} below the last raw row of raw_{table}",
                             "actual": f"COUNTIF({hs}${helper['cols']['in_raw']}:${helper['cols']['in_raw']},0)",
                             "targets": [helper["sheet"]], "format": "count"})
                if helper["dup_reason"] is None:
                    d = helper["cols"]["dup"]
                    entry = {"id": f"{table}_keys", "kind": "zero", "auto": True,
                             "label": f"Rows of raw_{table} repeating an earlier key ({', '.join(contract['key'])})",
                             "actual": f"SUM({hs}${d}:${d})", "targets": [helper["sheet"]], "format": "count"}
                    known = expect.get("key_repeats")
                    if isinstance(known, dict) and f"{table}_key_repeats" in ins:
                        entry.update({"kind": "at_most", "bound": iref(f"{table}_key_repeats"),
                                      "label": f"Rows of raw_{table} repeating an earlier key "
                                               f"({', '.join(contract['key'])}); at most {known['max']:,} agreed "
                                               "as known"})
                    rows.append(entry)
                else:
                    outside.append({"id": f"{table}_keys", "label": f"Key ({', '.join(contract['key'])}) unique in "
                                    f"raw_{table}: checked by rotm.py check ({helper['dup_reason']})"})
                    self.report.info("I-KEYS-OUTSIDE", f"no dup column on h_{table}: {helper['dup_reason']}; the key "
                                     "check runs in rotm.py check only", "", sheet=helper["sheet"])
            else:
                outside.append({"id": f"{table}_keys", "label": f"Key ({', '.join(contract['key'])}) unique in "
                                f"raw_{table}: checked by rotm.py check (the table has no helper sheet)"})
        for cid, info in self.spec.checks.items():
            chk = info["data"]
            entry = {"id": cid, "kind": chk["kind"], "auto": False, "label": chk["label"], "spec": chk,
                     "path": info["path"], "targets": list(chk.get("targets", []))}
            if chk.get("format"):
                entry["format"] = chk["format"]     # the check's value cells (live test 1: General showed 979070)
            else:
                # v2, J-4 (b): the format of what the actual expression reads first (block, item, measure, raw or
                # helper column); General (as before) when it reads none of them
                fmt = self._read_format(chk.get("actual"))
                if fmt:
                    entry["format"] = fmt
            (alerts if chk.get("severity") == "alert" else rows).append(entry)
        if not rows:
            self.problem("sheets", "the model has no check: no raw table and no check of severity error",
                         "add a check to the checks sheet (spec 2.2 P5; references/checks.md)")
        r = CHECK_FIRST_ROW
        for e in rows:
            e["row"] = r
            self.L["checks"][e["id"]] = {"sheet": name, "row": r}
            r += 1
        self.check_rows, self.alert_rows, self.outside_rows = rows, alerts, outside
        self.check_last = r - 1
        if alerts:
            self.alert_heading = r + 1
            r = r + 3
            for e in alerts:
                e["row"] = r
                self.L["checks"][e["id"]] = {"sheet": name, "row": r}
                r += 1
        if outside:
            self.outside_heading = r + 1
            r = r + 3
            for e in outside:
                e["row"] = r
                r += 1

    def _read_format(self, expr: Any) -> str | None:
        """The number-format key of the first block, line item, measure, raw column or helper column an
        expression reads (v2, J-4 (b)); None when it reads none of them."""
        if not isinstance(expr, str):
            return None
        from . import spec as specmod
        blocks = self.spec.blocks
        for ph in specmod.placeholders(expr):
            kind, arg = ph.kind, ph.arg or ""
            parts = arg.split(".")
            if kind in ("blk", "g", "grow", "gcol") and parts[0] in blocks:
                data, shape = blocks[parts[0]]["data"], blocks[parts[0]]["shape"]
                inner = data.get("items") if shape == "items" else data.get("measures") if shape == "measures" \
                    else None
                first = (inner or [{}])[0] if inner else {}
                if kind == "blk" and len(parts) == 2 and shape == "items":
                    # v5: {blk:<block>.<item>} reads that item, so its format
                    first = next((i for i in inner or [] if i.get("id") == parts[1]), first)
                return first.get("format") or data.get("format") or "amount"
            if kind == "cell":
                ko = self.spec.key_outputs.get(arg) or {}
                if ko.get("ambiguous") or ko.get("block") not in blocks:
                    continue
                data = blocks[ko["block"]]["data"]
                inner = data.get("items") or data.get("measures") or []
                own = next((x for x in inner if x.get("id") == ko.get("id")), {}) if ko["kind"] != "grid" else {}
                return own.get("format") or data.get("format") or "amount"
            if kind == "b" and len(parts) == 2 and parts[0] in blocks:
                data = blocks[parts[0]]["data"]
                own = next((x for x in data.get("items") or [] if x.get("id") == parts[1]), {})
                return own.get("format") or data.get("format") or "amount"
            if kind == "raw" and len(parts) == 2:
                contract = self.spec.contracts.get(parts[0]) or {}
                col = next((c for c in contract.get("columns", []) if c["name"] == parts[1]), None)
                if col is not None:
                    return self.raw_format(parts[0], col)
            if kind == "h" and len(parts) == 2:
                h = self.L["helpers"].get(parts[0])
                col = next((c for c in (h or {}).get("columns", []) if c["id"] == parts[1]), None)
                if col is None:
                    continue
                if col["kind"] == "fetch":
                    contract = self.spec.contracts.get(parts[0]) or {}
                    cc = next((c for c in contract.get("columns", []) if c["name"] == col["raw_column"]),
                              {"name": col["raw_column"]})
                    return self.raw_format(parts[0], cc) if cc.get("type") else "text"
                if col["kind"] == "derived":
                    sc = col["spec"]
                    return sc.get("format") or (self.d.format_for_column_type(sc["type"]) if sc.get("type")
                                                else "general")
                return {"row": "integer", "dupkey": "general"}.get(col["kind"], "flag")
        return None

    # ------------------------------------------------------------------ layout: key outputs
    def _layout_keys(self) -> None:
        for key, ko in self.spec.key_outputs.items():
            if ko.get("ambiguous"):
                continue
            b = self.L["blocks"].get(ko["block"])
            if b is None:
                continue
            kind, kid = ko["kind"], ko["id"]
            cell = None
            if kind == "grid":
                if b["total_row"] and b["total_col"]:
                    cell = (b["total_row"], b["total_col"])
                elif b["n_rows"] == 1 and b["n_cols"] == 1:
                    cell = (b["first_row"], b["first_col"])
                elif b["total_row"] and b["n_cols"] == 1:
                    cell = (b["total_row"], b["first_col"])
                elif b["total_col"] and b["n_rows"] == 1:
                    cell = (b["first_row"], b["total_col"])
                why = "a grid key output is its grand total: give the block \"total\": {\"row\": true, \"col\": true}"
            elif kind == "items":
                cell = (b["items"][kid], b["last_col"])
                why = ""
            else:
                meas = next(m for m in b["data"]["measures"] if m["id"] == kid)
                if meas.get("total", "none") != "none" and b["total_row"]:
                    cell = (b["total_row"], b["measures"][kid])
                elif b["n_rows"] == 1:
                    cell = (b["first_row"], b["measures"][kid])
                why = (f"measure '{kid}' has {b['n_rows']} rows and no total; give it \"total\": \"sum\" or "
                       "\"same\" so the key output is one cell")
            if cell is None:
                self.problem(ko.get("path", b["path"]), f"key output '{key}' is not one cell", why)
                continue
            self.L["cells"][key] = {"sheet": b["sheet"], "row": cell[0], "col": cell[1]}

    # ------------------------------------------------------------------ expression resolution
    def resolve(self, expr: str, ctx: RefContext) -> tuple[str, list[tuple[int, int, int, int]]]:
        """(formula body without '=', segments mapping resolved offsets to expression offsets)."""
        from . import spec as specmod
        out: list[str] = []
        segs: list[tuple[int, int, int, int]] = []
        pos = olen = 0
        for ph in specmod.placeholders(expr):
            lit = expr[pos:ph.start]
            out.append(lit)
            olen += len(lit)
            rep = self._replace(ph.kind, ph.arg, ctx)
            segs.append((olen, olen + len(rep), ph.start, ph.end))
            out.append(rep)
            olen += len(rep)
            pos = ph.end
        out.append(expr[pos:])
        return "".join(out), segs

    def _fail(self, message: str, fix: str = "") -> None:
        raise _Problem("", message, fix)

    def _block(self, bid: str) -> dict[str, Any]:
        b = self.L["blocks"].get(bid)
        if b is None:
            self._fail(f"block '{bid}' has no layout", "check the block id")
        return b  # type: ignore[return-value]

    def _replace(self, kind: str, arg: str | None, ctx: RefContext) -> str:
        host = ctx.sheet
        if kind == "in":
            iid, _, sub = (arg or "").partition(".")
            e = self.L["inputs"].get(iid)
            if e is None:
                self._fail(f"unknown input '{iid}'", "name an input of the inputs sheet")
            p = _prefix(e["sheet"], host)
            if e["kind"] == "scalar":
                return p + _abs(e["col"], e["row"])
            if e["kind"] == "list":
                return p + f"{_abs(e['col'], e['first'])}:{_abs(e['col'], e['last'])}"
            if sub == "rows":
                return p + f"{_abs(LABEL_COL, e['first'])}:{_abs(LABEL_COL, e['last'])}"
            if sub == "cols":
                return p + f"{_abs(e['c1'], e['header'])}:{_abs(e['c2'], e['header'])}"
            return p + f"{_abs(e['c1'], e['first'])}:{_abs(e['c2'], e['last'])}"
        if kind in ("raw", "h"):
            table, _, col = (arg or "").partition(".")
            src = self.L["raw" if kind == "raw" else "helpers"].get(table)
            if src is None:
                self._fail(f"table '{table}' has no {'raw' if kind == 'raw' else 'helper'} sheet in the layout")
            letter = src["cols"].get(col)
            if letter is None:
                self._fail(f"'{col}' is not a column of {src['sheet']}",
                           "use one of: " + ", ".join(src["cols"]))
            return _prefix(src["sheet"], host) + f"${letter}:${letter}"
        if kind == "this":
            h = self.L["helpers"].get(ctx.helper_table or "")
            letter = h["cols"].get(arg or "") if h else None
            if letter is None:
                self._fail(f"'{arg}' is not a column of this helper sheet")
            return f"{letter}{ctx.row}"
        if kind == "row":
            return f"${_L(LABEL_COL)}{ctx.row}"
        b = self.L["blocks"].get(ctx.block or "") if ctx.block else None
        if kind == "col":
            if b is None or ctx.ci is None:
                self._fail("{col} needs a block column")
            return f"{_L(ctx.col)}${b['header_row']}"
        if kind == "r":
            row = b["items"].get(arg) if b else None
            if row is None:
                self._fail(f"unknown line item '{arg}'")
            return f"{_L(ctx.col)}${row}"
        if kind == "prev":
            if ctx.ci is None or ctx.ci < 1:
                self._fail("{prev} in the first column", "give the item \"first\"")
            row = ctx.row if not arg else (b["items"].get(arg) if b else None)
            if row is None:
                self._fail(f"unknown line item '{arg}'")
            return f"{_L(ctx.col - 1)}${row}"
        if kind == "m":
            col = b["measures"].get(arg) if b else None
            if col is None:
                self._fail(f"unknown measure '{arg}'")
            return f"${_L(col)}{ctx.row}"
        if kind in ("g", "grow", "gcol"):
            o = self._block(arg or "")
            p = _prefix(o["sheet"], host)
            if kind == "g":
                if ctx.ri is None:
                    self._fail(f"{{g:{arg}}} on a total row", "read the other block's total with {b:<block>.total}")
                return p + f"{_L(o['first_col'] + ctx.ci)}{o['first_row'] + ctx.ri}"
            if kind == "grow":
                if ctx.ri is None:
                    if o["total_row"] is None:
                        self._fail(f"{{grow:{arg}}} on a total row, but block '{arg}' has no total row",
                                   f"give block '{arg}' \"total\": {{\"row\": true}}, or use \"total\": \"sum\"")
                    r = o["total_row"]
                else:
                    if ctx.ri >= o["n_rows"]:
                        self._fail(f"block '{arg}' has {o['n_rows']} rows; this block has more")
                    r = o["first_row"] + ctx.ri
                return p + f"${_L(o['first_col'])}{r}:${_L(o['last_col'])}{r}"
            if ctx.ci is None or ctx.ci >= o["n_cols"]:
                self._fail(f"block '{arg}' has {o['n_cols']} columns; this block has more")
            c = _L(o["first_col"] + ctx.ci)
            return p + f"{c}${o['first_row']}:{c}${o['last_row']}"
        if kind == "b":
            bid, _, item = (arg or "").partition(".")
            o = self._block(bid)
            if ctx.ci is None or ctx.ci >= o["n_cols"]:
                self._fail(f"block '{bid}' has {o['n_cols']} columns; this block has more")
            if item == "total":
                if o["total_row"] is None:
                    self._fail(f"block '{bid}' has no total row", f"give block '{bid}' \"total\": {{\"row\": true}}")
                row = o["total_row"]
            else:
                row = o.get("items", {}).get(item)
                if row is None:
                    self._fail(f"unknown item '{item}' of block '{bid}'")
            return _prefix(o["sheet"], host) + f"{_L(o['first_col'] + ctx.ci)}${row}"
        if kind == "blk":
            bid, _, sub = (arg or "").partition(".")
            o = self._block(bid)
            p = _prefix(o["sheet"], host)
            if sub and o["shape"] == "items" and (sub != "total" or sub in o.get("items", {})):
                # v5 (A-aera-9, C-sol-14): one line item's row over the block's columns, from the layout
                row = o.get("items", {}).get(sub)
                if row is None:
                    self._fail(f"unknown item '{sub}' of block '{bid}'", "name a line item of that block")
                return p + f"{_abs(o['first_col'], row)}:{_abs(o['last_col'], row)}"
            if sub == "total":
                if o["total_row"] and o["total_col"]:
                    return p + _abs(o["total_col"], o["total_row"])
                if o["total_row"] and o["n_cols"] == 1:
                    return p + _abs(o["first_col"], o["total_row"])
                if o["total_col"] and o["n_rows"] == 1:
                    return p + _abs(o["total_col"], o["first_row"])
                self._fail(f"{{blk:{bid}.total}}: block '{bid}' has no single grand total cell",
                           f"give block '{bid}' \"total\": {{\"row\": true, \"col\": true}}, or use "
                           f"SUM({{blk:{bid}}})")
            return p + f"{_abs(o['first_col'], o['first_row'])}:{_abs(o['last_col'], o['last_row'])}"
        if kind == "cell":
            c = self.L["cells"].get(arg or "")
            if c is None:
                self._fail(f"key output '{arg}' is not one cell", "see the problem reported for that key output")
            return _prefix(c["sheet"], host) + _abs(c["col"], c["row"])
        if kind == "chk":
            c = self.L["checks"].get(arg or "")
            if c is None:
                self._fail(f"unknown check '{arg}'")
            return _prefix(c["sheet"], host) + _abs(CHK["result"], c["row"])
        self._fail(f"placeholder kind '{kind}' is not known")
        return ""

    # ------------------------------------------------------------------ compile one expression
    def compile_expr(self, expr: str, ctx: RefContext, *, where: str) -> str | None:
        """Resolve and check one spec expression; returns the file formula ('=...', with _xlfn. prefixes) or None
        after recording a problem. `where` names the cell for messages, e.g. calc!D6."""
        path = ctx.path
        try:
            body, segs = self.resolve(expr, ctx)
        except _Problem as p:
            self.problem(path, f"{p.message} (in {where})", p.fix)
            return None
        f = "=" + body
        a = self.formula.analyze(f)
        # every reference must come from a placeholder: the layout belongs to build (S1)
        for ref in a.refs:
            s, e = ref.start - 1, ref.end - 1
            if not any(o1 <= s and e <= o2 for o1, o2, _s1, _s2 in segs):
                what = "a defined name" if ref.kind == "name" else "a plain reference"
                self.problem(path, f"{what} '{ref.text}' in the expression; build owns every cell reference",
                             "write a placeholder instead ({in:<id>}, {h:<table>.<column>}, {this:<column>}, "
                             "{blk:<block>} ...; references/model-spec-format.md, section 'Placeholders')")
                return None
        self._check(f, a, ctx, where, expr, segs)
        self.formula_count += 1
        return _F(self.formula.to_file_formula(f))

    def _move_hint(self, ctx: RefContext) -> str:
        return {"helpers": "a new helper column on this helper sheet, read with {this:<id>}",
                "grid": "a helper grid block with the same rows and cols, read with {g:<id>}",
                "items": "a new line item of this block, read with {r:<id>}",
                "measures": "a new measure of this block, read with {m:<id>}",
                "check": "a key output of a block (read with {cell:<id>}) or a block read with {blk:<id>}",
                }.get(ctx.kind, "a helper cell")

    def _check(self, f: str, a: Any, ctx: RefContext, where: str, expr: str | None,
               segs: list[tuple[int, int, int, int]] | None) -> None:
        st = self.settings
        issues = self.formula.check_formula(f, depth_limit=int(st["depth_limit"]), length_limit=int(st["length_limit"]),
                                            allowed_literals=st["allowed_literals"], product=self.spec.product,
                                            allow_functions=st["allow_functions"])
        path = ctx.path or where
        for iss in issues:
            if iss.rule in REFUSED_ISSUES:
                if expr is None:
                    self.problem(path, f"generated formula {f} in {where}: {iss.message}",
                                 "this is a build defect; report it with the specification")
                    continue
                fix = iss.fix
                if iss.rule == "F-DEPTH" and segs is not None:
                    s, e = _map_span(int(iss.data["start"]) - 1, int(iss.data["end"]) - 1, segs, len(expr))
                    part = expr[s:e]
                    fix = (f"move {part} into {self._move_hint(ctx)}, and use that in its place")
                    if iss.data.get("remaining_depth", 0) > st["depth_limit"]:
                        fix += f"; the moved part still has depth {iss.data['remaining_depth']}: split it again"
                elif iss.rule == "F-LITERAL":
                    value = iss.data.get("value")
                    fix = (f"put {_num_text(value) if isinstance(value, (int, float)) else value} in an input "
                           "item on the inputs sheet and write {in:<id>} in its place, or add it to "
                           "settings.allowed_literals")
                elif iss.rule == "F-PARSE" and segs is not None and isinstance(iss.data.get("start"), int) \
                        and isinstance(iss.data.get("end"), int):
                    # v4 (C-sol-3): the place of an incomplete formula in the expression, mapped as for F-DEPTH
                    s, e = _map_span(int(iss.data["start"]) - 1, int(iss.data["end"]) - 1, segs, len(expr))
                    if e <= s:
                        s, e = max(0, min(s, len(expr)) - 1), min(len(expr), max(s, 0) + 1)
                    fix = (f"complete the expression at '{expr[s:e]}' (characters {s + 1} to {e} of the expression): "
                           "an operand after each operator, every argument the function needs, closed brackets and "
                           "quotes")
                elif iss.rule == "F-PARSE":
                    fix = "check the brackets, commas and quotes of the expression"
                self.problem(path, f"{iss.message} (in {where})", fix)
            else:
                key = (path, iss.rule)
                seen = self._issue_seen.get(key)
                if seen is not None:
                    seen.count += 1
                    continue
                sheet, _, cell = where.partition("!")
                fnd = common.Finding(iss.rule, "warning" if iss.severity != "info" else "info",
                                     f"{path}: {iss.message}" if ctx.path else iss.message, iss.fix,
                                     sheet=sheet or None, cell=cell or None, data=dict(iss.data))
                self.report.findings.append(fnd)
                self._issue_seen[key] = fnd

    def gen(self, body: str, where: str) -> str:
        """A formula build writes itself: checked like any other (design 6.6); returns the file form."""
        f = "=" + body
        a = self.formula.analyze(f)
        self._check(f, a, RefContext(sheet=where.split("!")[0], row=0, col=0, kind="generated", path=""), where,
                    None, None)
        self.formula_count += 1
        return _F(self.formula.to_file_formula(f))

    # ------------------------------------------------------------------ writing cells
    def put(self, sheet: str, row: int, col: int, value: Any, ctype: str, fmt: str | None = None,
            width: float | None = None) -> Any:
        ws = self.ws[sheet]
        cell = ws.cell(row=row, column=col)
        _store(cell, value)
        if isinstance(value, _F):
            self.formula_cells += 1
        self.styler.apply(cell, ctype, self.sizes[sheet], fmt)
        self.types[sheet][(row, col)] = ctype
        self.used_types.add(ctype)
        if width is None:
            if isinstance(value, _F):
                width = self._formula_width(fmt)
            else:
                width = self._value_width(value, fmt)
            if ctype in ("col_header", "heading", "title", "total", "output", "master_check"):
                width *= 1.1
        w = self.widths[sheet]
        w[col] = max(w.get(col, 0), width)
        return cell

    def _formula_width(self, fmt: str | None) -> float:
        base = FORMAT_WIDTH.get(fmt or "general", 11)
        if fmt in ("amount", "amount1", "amount2", "count", "general"):
            digits = len(f"{self.magnitude:,.0f}") + 3 + (2 if fmt == "amount2" else 1 if fmt == "amount1" else 0)
            base = max(base, min(digits, 22))
        return float(base)

    def _value_width(self, value: Any, fmt: str | None) -> float:
        if value is None:
            return 0.0
        if isinstance(value, bool):
            return 5.0
        if isinstance(value, (_dt.datetime, _dt.date)):
            return float(FORMAT_WIDTH.get(fmt or "date", 10))
        if isinstance(value, (int, float)):
            if fmt in ("rate", "rate2"):
                return 8.0
            if fmt in (None, "general"):
                return float(len(_num_text(value)) + 1)
            return float(len(f"{abs(value):,.2f}") + 2)
        return float(_text_width(str(value)) + 1)

    def mark_range(self, sheet: str, rng: str, ctype: str) -> None:
        self.type_ranges.setdefault(sheet, []).append([rng, ctype])
        self.used_types.add(ctype)

    def _sheet_head(self, s: Any, title: str) -> None:
        name = s.name
        self.put(name, TITLE_ROW, LABEL_COL, title, "title", "general", width=12)
        self.put(name, MASTER_ROW, LABEL_COL, "Master check", "label", "general")
        checks = self.L["names"]["checks"]
        if s.type == "checks":
            return          # the master check formula is written by _write_checks
        f = self.gen(f"{common.quote_sheet(checks)}!$C${MASTER_ROW}", f"{name}!C{MASTER_ROW}")
        self.put(name, MASTER_ROW, UNIT_COL, f, "master_check")

    def _sheet_title(self, s: Any) -> str:
        if s.data.get("title"):
            return s.data["title"]
        if s.type == "cover":
            return self.spec.title
        return s.name.replace("_", " ").capitalize()

    # ------------------------------------------------------------------ inputs sheet
    def _write_inputs(self, s: Any) -> None:
        name = s.name
        self._sheet_head(s, self._sheet_title(s))
        for blk in self.input_blocks:
            kind = blk["kind"]
            self.put(name, blk["heading"], LABEL_COL, blk["title"], "heading", "general", width=8)
            h = blk["header"]
            nc = self.notes_col[name]
            if kind in ("scalars", "controls"):
                for c, text in ((LABEL_COL, "Item"), (UNIT_COL, "Unit"), (FIRST_VALUE_COL, "Value"),
                                (nc, "Source or note")):
                    self.put(name, h, c, text, "col_header", "general")
                for c in range(FIRST_VALUE_COL + 1, nc):
                    self.put(name, h, c, "", "col_header", "general", width=0)
                for r, it in blk["rows"]:
                    self._write_scalar(name, r, it, generated=(kind == "controls"))
            elif kind == "list":
                it = blk["item"]
                for c, text in ((LABEL_COL, "#"), (UNIT_COL, "Unit"), (FIRST_VALUE_COL, "Value"),
                                (nc, "Source or note")):
                    self.put(name, h, c, text, "col_header", "general")
                for c in range(FIRST_VALUE_COL + 1, nc):
                    self.put(name, h, c, "", "col_header", "general", width=0)
                fmt = it.get("format") or DEFAULT_FORMAT_BY_INPUT_TYPE.get(it.get("type", ""), None)
                for k, v in enumerate(it["values"]):
                    r = blk["first"] + k
                    value = _header_value(v, fmt) if it.get("type") != "text" else v
                    f = fmt or ("date" if isinstance(value, _dt.datetime) else "general")
                    self.put(name, r, LABEL_COL, str(k + 1), "row_header", "general", width=4)
                    self.put(name, r, UNIT_COL, it.get("unit") or "", "unit", "general")
                    cell = self.put(name, r, FIRST_VALUE_COL, value, "input", f)
                    if _is_text_input(it):
                        _store_text(cell, v)             # v5 (A-aera-17): a declared text input stays text
                    if k == 0 and (it.get("source") or it.get("note")):
                        self._note(name, r, nc, self._source_text(it))
                self._validation(name, it, f"{_L(FIRST_VALUE_COL)}{blk['first']}:{_L(FIRST_VALUE_COL)}{blk['last']}")
            elif kind == "grid":
                it = blk["item"]
                rows_fmt = it["rows"].get("format")
                cols_fmt = it["cols"].get("format")
                self.put(name, h, LABEL_COL, it["rows"].get("label", ""), "col_header", "general")
                self.put(name, h, UNIT_COL, "Unit", "col_header", "general")
                for j, v in enumerate(it["cols"]["values"]):
                    value = _header_value(v, cols_fmt)
                    f = cols_fmt or ("month" if isinstance(value, _dt.datetime) else "general")
                    self.put(name, h, FIRST_VALUE_COL + j, value, "col_header", f)
                fmt = it.get("format") or DEFAULT_FORMAT_BY_INPUT_TYPE.get(it.get("type", ""), "general")
                for i, rv in enumerate(it["rows"]["values"]):
                    r = blk["first"] + i
                    value = _header_value(rv, rows_fmt)
                    f = rows_fmt or ("date" if isinstance(value, _dt.datetime) else "general")
                    self.put(name, r, LABEL_COL, value, "row_header", f)
                    self.put(name, r, UNIT_COL, it.get("unit") or "", "unit", "general")
                    for j, v in enumerate(it["values"][i]):
                        cell = self.put(name, r, FIRST_VALUE_COL + j, v, "input", fmt)
                        if _is_text_input(it) and v is not None:
                            _store_text(cell, v)         # v5 (A-aera-17): a declared text input stays text
                if it.get("source") or it.get("note"):
                    self._note(name, h, nc, self._source_text(it))
                e = self.L["inputs"][it["id"]]
                self._validation(name, it, f"{_L(e['c1'])}{e['first']}:{_L(e['c2'])}{e['last']}")

    def _source_text(self, it: dict[str, Any]) -> str:
        parts = []
        if it.get("source"):
            parts.append(f"Source: {it['source']}")
        if it.get("note"):
            parts.append(str(it["note"]))
        return "; ".join(parts)

    def _note(self, sheet: str, row: int, col: int, text: str) -> None:
        if not text:
            return
        width = min(60.0, float(_text_width(text) + 2))
        self.put(sheet, row, col, text, "note", "general", width=width)
        self.wrapped[sheet].append((row, col, text))

    def _write_scalar(self, sheet: str, r: int, it: dict[str, Any], *, generated: bool) -> None:
        value = it["value"]
        typ = it.get("type")
        fmt = it.get("format")
        if typ == "date" or (typ is None and _is_iso_date(value) and fmt != "text"):
            value = _header_value(value, fmt)
            fmt = fmt or "date"
        if fmt is None:
            fmt = DEFAULT_FORMAT_BY_INPUT_TYPE.get(typ or "", None) or (
                "general" if isinstance(value, (int, float)) and not isinstance(value, bool) else
                "general")
        self.put(sheet, r, LABEL_COL, it.get("label", it["id"]), "row_header", "general")
        self.put(sheet, r, UNIT_COL, it.get("unit") or "", "unit", "general")
        cell = self.put(sheet, r, FIRST_VALUE_COL, value, "input", fmt)
        if not generated and _is_text_input(it):
            # v5 (A-aera-16, A-aera-17): a declared text input is a text cell whatever it looks like (#N/A, =x, 0012)
            _store_text(cell, it["value"])
        self._note(sheet, r, self.notes_col.get(sheet, FIRST_VALUE_COL + 1), self._source_text(it))
        if not generated:
            self._validation(sheet, it, f"{_L(FIRST_VALUE_COL)}{r}")

    def _validation(self, sheet: str, it: dict[str, Any], rng: str) -> None:
        valid = it.get("valid")
        if not valid:
            return
        from openpyxl.worksheet.datavalidation import DataValidation
        whole = it.get("type") == "integer"
        if "list" in valid:
            items = ",".join(str(v) for v in valid["list"])
            if len(items) > 250 or any("," in str(v) or '"' in str(v) for v in valid["list"]):
                self.problem(f"{it['id']}.valid.list", "the list of allowed values is too long for a cell "
                             "validation (255 characters) or holds a comma or a quote",
                             "shorten the values, or validate with a list input and {in:<id>} instead")
                return
            dv = DataValidation(type="list", formula1=f'"{items}"', allow_blank=False)
        else:
            lo, hi = valid.get("min"), valid.get("max")
            t = "whole" if whole else "decimal"
            if lo is not None and hi is not None:
                dv = DataValidation(type=t, operator="between", formula1=_num_text(lo), formula2=_num_text(hi))
            elif lo is not None:
                dv = DataValidation(type=t, operator="greaterThanOrEqual", formula1=_num_text(lo))
            else:
                dv = DataValidation(type=t, operator="lessThanOrEqual", formula1=_num_text(hi))
        dv.error = "This value is outside what the model allows (see the inputs sheet)."
        dv.errorTitle = "Input not allowed"
        dv.showErrorMessage = True
        dv.add(rng)
        self.ws[sheet].add_data_validation(dv)

    # ------------------------------------------------------------------ raw sheets
    def _write_raw(self, s: Any) -> None:
        from openpyxl.worksheet.table import Table
        table = s.data["table"]
        snap = self.snapshots.get(table)
        contract = self.spec.contracts.get(table)
        if snap is None or contract is None:
            return
        ws = self.ws[s.name]
        size = self.sizes[s.name]
        formats = []
        for c in contract["columns"]:
            formats.append(self.raw_format(table, c))
        widths = self.widths[s.name]
        for ci, name in enumerate(snap.columns):
            cell = ws.cell(row=1, column=ci + 1, value=name)
            self.styler.apply(cell, "col_header", size, "general")
            # + 3: the table's filter button covers the right end of a header cell (render review, integrator)
            widths[ci + 1] = (_text_width(name) + 2) * 1.1 * (0.92 if size == 8 else 1.0) + 3
        n = len(snap.rows)
        text_cols = {ci for ci, t in enumerate(snap.types) if t == "text"}
        for ri, row in enumerate(snap.rows):
            for ci, v in enumerate(row):
                if v is not None:
                    if ci in text_cols:
                        _store_text(ws.cell(row=ri + 2, column=ci + 1), v)
                    else:
                        _store(ws.cell(row=ri + 2, column=ci + 1), v)
        # style column by column: one apply per column, then the same style array copied down (fast)
        from copy import copy
        for ci in range(len(snap.columns)):
            protos: dict[bool, Any] = {}
            col_w = widths.get(ci + 1, 6)
            for ri in range(n):
                cell = ws.cell(row=ri + 2, column=ci + 1)
                v = cell.value
                is_num = isinstance(v, (int, float, _dt.date, _dt.datetime)) and not isinstance(v, bool)
                proto = protos.get(is_num)
                if proto is None:
                    self.styler.apply(cell, "raw", size, formats[ci])
                    protos[is_num] = cell
                else:
                    cell._style = copy(proto._style)
                if ri < 2000 and v is not None:
                    col_w = max(col_w, self._value_width(v, formats[ci]) * (0.92 if size == 8 else 1.0))
            widths[ci + 1] = col_w
        last = _L(len(snap.columns))
        self.mark_range(s.name, f"A1:{last}1", "col_header")
        if n:
            self.mark_range(s.name, f"A2:{last}{n + 1}", "raw")
        ref = f"A1:{last}{max(2, n + 1)}"
        tbl = Table(displayName=f"tbl_raw_{table}", ref=ref)
        tbl.tableStyleInfo = None     # no table style, so the dictionary's formats show (design 7.1)
        ws.add_table(tbl)

    # ------------------------------------------------------------------ helper sheets
    def _helper_template(self, table: str, col: dict[str, Any], h: dict[str, Any]) -> str | None:
        raw = self.L["raw"][table]
        rs = common.quote_sheet(raw["sheet"]) + "!"
        a = f"A{ROW_MARK}"
        kind = col["kind"]
        where = f"{h['sheet']}!{col['col']}{HELPER_FIRST_ROW}"
        if kind == "row":
            body = "ROW()"
        elif kind == "flag":
            body = f'--(INDEX({rs}$A:$A,{a})<>"")'
        elif kind == "fetch":
            letter = raw["cols"][col["raw_column"]]
            body = f"INDEX({rs}${letter}:${letter},{a})" + ('&""' if col.get("type") == "text" else "")
        elif kind == "nullflag":
            # 1 where the raw cell is empty: the fetch shows 0 there (v2, A-aera-6)
            letter = raw["cols"][col["raw_column"]]
            body = f'--(INDEX({rs}${letter}:${letter},{a})="")'
        elif kind == "dupkey":
            # the composite key as one text, parts joined by '|' (design 7.1, live test 1)
            body = '&"|"&'.join(f"{h['cols'][k]}{ROW_MARK}" for k in col["keys"])
        elif kind == "dup":
            # 1 on a later copy of a key: EXACT compares this row's key with every row from the first helper row
            # down to this one, exactly (text as text, case-sensitive, no wildcard: the one key equality of
            # common.key_tuple, v2 A-aera-9), so the count is 1 on the first copy and above 1 on a later one.
            # Neither MATCH nor COUNTIFS: both compare text without regard to case and read * ? ~ as wildcards,
            # and COUNTIFS reads 00123 and 123 as one number (measured in Excel 16, 2026-11-25).
            k = h["cols"]["dup_key"] if len(col["keys"]) > 1 else h["cols"][col["keys"][0]]
            body = (f"--(SUMPRODUCT(--EXACT({k}${HELPER_FIRST_ROW}:{k}{ROW_MARK},{k}{ROW_MARK}))>1)"
                    f"*B{ROW_MARK}")
        else:
            spec_col = col["spec"]
            ctx = RefContext(sheet=h["sheet"], row=HELPER_FIRST_ROW, col=common.col_index(col["col"]),
                             kind="helpers", helper_table=table, path=f"{col['path']}.expr")
            if self.compile_expr(spec_col["expr"], ctx, where=where) is None:     # checked at row 2
                return None
            ctx.row = ROW_MARK
            body, _segs = self.resolve(spec_col["expr"], ctx)
            if ROW_MARK in spec_col["expr"]:
                self.problem(ctx.path, f"the number {ROW_MARK} is typed into the expression", "remove it")
                return None
            return self.formula.to_file_formula("=" + body)
        return self._gen_template(body, where)

    def _gen_template(self, body: str, where: str) -> str:
        self.gen(body.replace(ROW_MARK, str(HELPER_FIRST_ROW)), where)       # check the row-2 form
        return self.formula.to_file_formula("=" + body)

    def _write_helpers(self, s: Any) -> None:
        table = s.data["table"]
        h = self.L["helpers"].get(table)
        if h is None:
            return
        ws = self.ws[s.name]
        size = self.sizes[s.name]
        contract = self.spec.contracts[table]
        ctypes = {c["name"]: c for c in contract["columns"]}
        templates: list[tuple[int, str | None, str]] = []
        for col in h["columns"]:
            ci = common.col_index(col["col"])
            header = col.get("header") or col["id"]
            cell = ws.cell(row=1, column=ci, value=header)
            self.styler.apply(cell, "col_header", size, "general")
            tmpl = self._helper_template(table, col, h)
            kind = col["kind"]
            if kind == "row":
                fmt = "integer"
            elif kind in ("flag", "dup", "nullflag"):
                fmt = "flag"
            elif kind == "dupkey":
                fmt = "general"
            elif kind == "fetch":
                c = ctypes.get(col["raw_column"], {"name": col["raw_column"]})
                fmt = self.raw_format(table, c) if c.get("type") else self.d.format_for_column_type("text")
                from . import spec as specmod
                if c.get("type") in EMPTY_DATE_FORMATS and fmt == c.get("type") and specmod.may_be_empty(c):
                    # v6 (r5 verdict J-1): 0 (an empty raw date) shows nothing; the manifest records the key as the
                    # column's format, so extend writes it into new rows
                    fmt = EMPTY_DATE_FORMATS[c["type"]]
            else:
                sc = col["spec"]
                fmt = sc.get("format") or (self.d.format_for_column_type(sc["type"]) if sc.get("type") else "general")
            col["format"] = fmt
            col["formula"] = tmpl.replace(ROW_MARK, str(HELPER_FIRST_ROW)) if tmpl else ""
            templates.append((ci, tmpl, fmt))
            base_w = max(_text_width(header) + 2, FORMAT_WIDTH.get(fmt, 10))
            if kind == "fetch":
                rw = self.widths.get(self.L["raw"][table]["sheet"], {}).get(
                    common.col_index(self.L["raw"][table]["cols"][col["raw_column"]]), 0)
                base_w = max(base_w, rw)
            elif fmt in ("amount", "amount1", "amount2", "count", "general") and kind == "derived":
                base_w = max(base_w, self._formula_width(fmt))
            self.widths[s.name][ci] = base_w * (0.92 if size == 8 else 1.0) * 1.05
        if any(t is None for _ci, t, _f in templates):
            return
        from copy import copy
        last_row = h["last_row"]
        last = _L(len(h["columns"]))
        self.mark_range(s.name, f"A1:{last}1", "col_header")
        if last_row < HELPER_FIRST_ROW:
            # v3, A-aera-8: zero landed rows: the header row only; the formulas are in the manifest
            # (columns[].formula), and rotm.py extend writes row 2 from them when a refresh returns rows.
            return
        for ci, tmpl, fmt in templates:
            first = ws.cell(row=HELPER_FIRST_ROW, column=ci, value=tmpl.replace(ROW_MARK, str(HELPER_FIRST_ROW)))
            self.styler.apply(first, "calc", size, fmt)
            if fmt == "text":
                # Text helper columns read left-aligned, as on the raw sheet; right-aligned text next to a number
                # column reads as one value (render review, integrator). The audit compares no alignment.
                from openpyxl.styles import Alignment
                first.alignment = Alignment(horizontal="left", vertical=first.alignment.vertical)
            for r in range(HELPER_FIRST_ROW + 1, last_row + 1):
                cell = ws.cell(row=r, column=ci, value=tmpl.replace(ROW_MARK, str(r)))
                cell._style = copy(first._style)
        self.formula_cells += len(templates) * (last_row - HELPER_FIRST_ROW + 1)
        self.mark_range(s.name, f"A{HELPER_FIRST_ROW}:{last}{last_row}", "calc")

    # ------------------------------------------------------------------ block sheets
    def _write_block_sheet(self, s: Any) -> None:
        self._sheet_head(s, self._sheet_title(s))
        for block in s.data["blocks"]:
            b = self.L["blocks"].get(block["id"])
            if b is None or b["sheet"] != s.name:
                continue
            self._write_block(b)

    def _dim_headers(self, b: dict[str, Any]) -> list[tuple[Any, str]]:
        """(value or formula, number-format key) for each value column header of block b."""
        dim = b["cols_dim"]
        shape = b["shape"]
        name = b["sheet"]
        out: list[tuple[Any, str]] = []
        if shape == "measures":
            return [(m["label"], "general") for m in b["data"]["measures"]]
        kind = dim["kind"]
        node = dim.get("node") or {}
        fmt = node.get("format")
        hr = b["header_row"]
        if kind == "value":
            return [("Value", "general")]
        if kind == "values":
            for v in node["values"]:
                value = _header_value(v, fmt)
                out.append((value, fmt or ("month" if isinstance(value, _dt.datetime) else "general")))
            return out
        if kind == "in":
            # rule HDR-FORMAT (v3, J-2): the block cols' format, else the list item's, else month when the list's
            # first value is an ISO date, else general (the col_header cell type takes the item's format)
            e = self.L["inputs"][node["in"]]
            lf = e["item"].get("format") or fmt
            first = (e["item"].get("values") or [None])[0]
            for k in range(dim["n"]):
                f = self.gen(_prefix(e["sheet"], name) + _abs(e["col"], e["first"] + k),
                             f"{name}!{_L(FIRST_VALUE_COL + k)}{hr}")
                out.append((f, fmt or lf or ("month" if _is_iso_date(first) else "general")))
            return out
        if kind == "timeline":
            t = node["timeline"]
            e = self.L["inputs"].get(t["start"])
            if e is None or e["kind"] != "scalar":
                self.problem(b["path"] + ".cols.timeline.start", f"input '{t['start']}' is not a scalar input")
                return [("", "general")] * dim["n"]
            start = _prefix(e["sheet"], name) + _abs(e["col"], e["row"])
            for k in range(dim["n"]):
                where = f"{name}!{_L(FIRST_VALUE_COL + k)}{hr}"
                if k == 0:
                    f = self.gen(f"EOMONTH({start},0)", where)
                else:
                    f = self.gen(f"EOMONTH({_L(FIRST_VALUE_COL + k - 1)}{hr},1)", where)
                out.append((f, fmt or "month"))
            return out
        # same_as: links to the other block's headers
        o = self.L["blocks"][dim["target"]]
        ofmt = None
        if o["cols_dim"].get("node"):
            ofmt = o["cols_dim"]["node"].get("format")
        if o["cols_dim"]["kind"] == "timeline":
            ofmt = ofmt or "month"
        for k in range(dim["n"]):
            f = self.gen(_prefix(o["sheet"], name) + f"{_L(o['first_col'] + k)}{o['header_row']}",
                         f"{name}!{_L(FIRST_VALUE_COL + k)}{hr}")
            out.append((f, fmt or ofmt or "general"))
        return out

    def _dim_labels(self, b: dict[str, Any]) -> list[tuple[Any, str]]:
        """(value or formula, format key) for each body row label of block b."""
        dim = b["rows_dim"]
        name = b["sheet"]
        if b["shape"] == "items":
            return [(it["label"], "general") for it in b["data"]["items"]]
        node = dim.get("node") or {}
        fmt = node.get("format")
        out: list[tuple[Any, str]] = []
        if dim["kind"] == "values":
            for v in node["values"]:
                value = _header_value(v, fmt)
                out.append((value, fmt or ("date" if isinstance(value, _dt.datetime) else "general")))
            return out
        if dim["kind"] == "in":
            # rule HDR-FORMAT (v3, J-2): the block rows' format, else the list item's, else date when the list's
            # first value is an ISO date, else general; _write_block gives the row header cell this format
            e = self.L["inputs"][node["in"]]
            lf = e["item"].get("format")
            first = (e["item"].get("values") or [None])[0]
            for k in range(dim["n"]):
                f = self.gen(_prefix(e["sheet"], name) + _abs(e["col"], e["first"] + k),
                             f"{name}!B{b['first_row'] + k}")
                out.append((f, fmt or lf or ("date" if _is_iso_date(first) else "general")))
            return out
        o = self.L["blocks"][dim["target"]]
        ofmt = (o["rows_dim"].get("node") or {}).get("format")
        for k in range(dim["n"]):
            f = self.gen(_prefix(o["sheet"], name) + f"${_L(LABEL_COL)}{o['first_row'] + k}",
                         f"{name}!B{b['first_row'] + k}")
            out.append((f, fmt or ofmt or "general"))
        return out

    def _write_block(self, b: dict[str, Any]) -> None:
        name = b["sheet"]
        block = b["data"]
        shape = b["shape"]
        path = b["path"]
        title = block.get("title") or block["id"].replace("_", " ").capitalize()
        self.put(name, b["heading_row"], LABEL_COL, title, "heading", "general", width=8)
        hr = b["header_row"]
        rows_label = ""
        node = block.get("rows") or {}
        if isinstance(node, dict):
            rows_label = node.get("label", "")
        self.put(name, hr, LABEL_COL, rows_label or ("Item" if shape == "items" else ""), "col_header", "general")
        self.put(name, hr, UNIT_COL, "Unit", "col_header", "general")
        headers = self._dim_headers(b)
        for k, (v, f) in enumerate(headers):
            self.put(name, hr, FIRST_VALUE_COL + k, v, "col_header", f)
        if b["total_col"]:
            self.put(name, hr, b["total_col"], "Total", "col_header", "general")
        if b["unit_row"]:
            self.put(name, b["unit_row"], LABEL_COL, "", "row_header", "general")
            self.put(name, b["unit_row"], UNIT_COL, "", "unit", "general")
            for k, m in enumerate(block["measures"]):
                self.put(name, b["unit_row"], FIRST_VALUE_COL + k, m.get("unit") or "", "unit", "general")
        labels = self._dim_labels(b)
        block_fmt = block.get("format")
        block_unit = block.get("unit") or ""
        notes_col = self.notes_col.get(name) if b["notes_col"] else None
        if notes_col and (block.get("note") or block.get("source")):
            self._note(name, b["heading_row"], notes_col, self._source_text(block))
        rows_from_list = b["rows_dim"]["kind"] == "in" and shape != "items"
        for i in range(b["n_rows"]):
            r = b["first_row"] + i
            lab, lfmt = labels[i]
            cell = self.put(name, r, LABEL_COL, lab, "row_header", lfmt)
            if rows_from_list:
                # rule HDR-FORMAT (v3, J-2): a row header that shows an item of an input list shows it in the list's
                # number format (a date list: the date format). The row_header cell type's own format is General,
                # set by the styler above; every other row header keeps it.
                cell.number_format = self.d.number_format(lfmt or "general")
            if shape == "items":
                it = block["items"][i]
                self.put(name, r, UNIT_COL, it.get("unit") or block_unit, "unit", "general")
                fmt = it.get("format") or block_fmt or "amount"
                ctype = "output" if it.get("key") else "calc"
                ipath = f"{path}.items[{i}]"
                for j in range(b["n_cols"]):
                    c = FIRST_VALUE_COL + j
                    use_first = j == 0 and "first" in it
                    expr = it["first"] if use_first else it["expr"]
                    ctx = RefContext(sheet=name, row=r, col=c, block=b["id"], item=it["id"], kind="items", ri=i, ci=j,
                                     path=f"{ipath}.{'first' if use_first else 'expr'}")
                    f = self.compile_expr(expr, ctx, where=f"{name}!{_L(c)}{r}")
                    if f is not None:
                        self.put(name, r, c, f, self._ctype(f, ctype, name), fmt)
                if it.get("note") and notes_col:
                    self._note(name, r, notes_col, it["note"])
            elif shape == "measures":
                self.put(name, r, UNIT_COL, block_unit, "unit", "general")
                for j, m in enumerate(block["measures"]):
                    c = FIRST_VALUE_COL + j
                    fmt = m.get("format") or block_fmt or "amount"
                    ctype = "output" if m.get("key") else "calc"
                    ctx = RefContext(sheet=name, row=r, col=c, block=b["id"], measure=m["id"], kind="measures",
                                     ri=i, ci=j, path=f"{path}.measures[{j}].expr")
                    f = self.compile_expr(m["expr"], ctx, where=f"{name}!{_L(c)}{r}")
                    if f is not None:
                        self.put(name, r, c, f, self._ctype(f, ctype, name), fmt)
            else:
                self.put(name, r, UNIT_COL, block_unit, "unit", "general")
                fmt = block_fmt or "amount"
                ctype = "output" if block.get("key") else "calc"
                for j in range(b["n_cols"]):
                    c = FIRST_VALUE_COL + j
                    ctx = RefContext(sheet=name, row=r, col=c, block=b["id"], kind="grid", ri=i, ci=j,
                                     path=f"{path}.cell")
                    f = self.compile_expr(block["cell"], ctx, where=f"{name}!{_L(c)}{r}")
                    if f is not None:
                        self.put(name, r, c, f, self._ctype(f, ctype, name), fmt)
                if b["total_col"]:
                    tc = b["total_col"]
                    f = self.gen(f"SUM({_L(FIRST_VALUE_COL)}{r}:{_L(b['last_col'])}{r})", f"{name}!{_L(tc)}{r}")
                    self.put(name, r, tc, f, "calc", fmt)
        tr = b["total_row"]
        if tr:
            self.put(name, tr, LABEL_COL, "Total", "row_header", "general")
            self.put(name, tr, UNIT_COL, block_unit, "unit", "general")
            r1, r2 = b["first_row"], b["last_row"]
            if shape == "grid":
                fmt = block_fmt or "amount"
                end = b["total_col"] or b["last_col"]
                for c in range(FIRST_VALUE_COL, end + 1):
                    f = self.gen(f"SUM({_L(c)}{r1}:{_L(c)}{r2})", f"{name}!{_L(c)}{tr}")
                    self.put(name, tr, c, f, "total", fmt)
            else:
                for j, m in enumerate(block["measures"]):
                    c = FIRST_VALUE_COL + j
                    fmt = m.get("format") or block_fmt or "amount"
                    mode = m.get("total", "none")
                    if mode == "sum":
                        f = self.gen(f"SUM({_L(c)}{r1}:{_L(c)}{r2})", f"{name}!{_L(c)}{tr}")
                    elif mode == "same":
                        ctx = RefContext(sheet=name, row=tr, col=c, block=b["id"], measure=m["id"], kind="measures",
                                         ri=None, ci=j, path=f"{path}.measures[{j}].expr")
                        f = self.compile_expr(m["expr"], ctx, where=f"{name}!{_L(c)}{tr}")
                    else:
                        continue
                    if f is not None:
                        self.put(name, tr, c, f, "total", fmt)

    def _ctype(self, f: str, default: str, host: str) -> str:
        """link / import for a pure reference, else `default` (calc or output)."""
        if default == "output":
            return default
        a = self.formula.analyze(f)
        if a.is_pure_link and a.refs:
            sheet = a.refs[0].sheet
            return "import" if sheet and sheet != host else "link"
        return default

    # ------------------------------------------------------------------ checks sheet
    def _check_field(self, e: dict[str, Any], key: str, row: int, col: int) -> str | None:
        """The formula of one field of a spec check (actual, expected, min, max, bound)."""
        name = self.L["names"]["checks"]
        chk = e["spec"]
        value = chk[key]
        where = f"{name}!{_L(col)}{row}"
        path = f"{e['path']}.{key}"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            allowed = {abs(float(x)) for x in self.settings["allowed_literals"]}
            if abs(float(value)) not in allowed:
                self.problem(path, f"the number {_num_text(value)} is typed into the check",
                             "put it in an input item on the inputs sheet and write {in:<id>} here")
                return None
            return self.gen(_num_text(value), where)
        ctx = RefContext(sheet=name, row=row, col=col, kind="check", check=e["id"], ri=None, ci=None, path=path)
        return self.compile_expr(value, ctx, where=where)

    def _write_check_row(self, e: dict[str, Any]) -> None:
        name = self.L["names"]["checks"]
        r = e["row"]
        kind = e["kind"]
        self.put(name, r, CHK["id"], e["id"], "row_header", "general")
        self.put(name, r, CHK["label"], e["label"], "row_header", "general", width=min(70.0, _text_width(e["label"]) + 2))
        fields: dict[str, str | None] = {}
        if e["auto"]:
            for key, col in (("actual", CHK["actual"]), ("expected", CHK["expected"]), ("min", CHK["expected"]),
                             ("max", CHK["max"]), ("bound", CHK["expected"]), ("tolerance", CHK["tol"])):
                if key in e:
                    fields[key] = self.gen(e[key], f"{name}!{_L(col)}{r}")
        else:
            chk = e["spec"]
            fields["actual"] = self._check_field(e, "actual", r, CHK["actual"])
            if kind == "equal":
                fields["expected"] = self._check_field(e, "expected", r, CHK["expected"])
                tol = chk.get("tolerance", "check_tolerance")
                te = self.L["inputs"].get(tol)
                tv = ((te or {}).get("item") or {}).get("value")
                if tol == "check_tolerance" and te is not None and te.get("generated"):
                    tv = self.settings["check_tolerance"]
                if te is None or te["kind"] != "scalar":
                    self.problem(f"{e['path']}.tolerance", f"tolerance '{tol}' is not a scalar input",
                                 "name a scalar input id (default check_tolerance)")
                    fields["tolerance"] = None
                elif not _is_tolerance(tv):
                    # v5 (A-aera-3, C-sol-5): a text, true/false, NaN, an infinite or a negative tolerance would let
                    # the check pass (or fail) whatever its two sides hold
                    self.problem(f"{e['path']}.tolerance", f"the tolerance of check '{e['id']}' is input '{tol}', "
                                 f"which holds {_shown(tv)}, not a finite number of 0 or more",
                                 f"write a number of 0 or more in input '{tol}'")
                    fields["tolerance"] = None
                else:
                    fields["tolerance"] = self.gen(_prefix(te["sheet"], name) + _abs(te["col"], te["row"]),
                                                   f"{name}!H{r}")
            elif kind == "range":
                fields["min"] = self._check_field(e, "min", r, CHK["expected"])
                fields["max"] = self._check_field(e, "max", r, CHK["max"])
            elif kind == "at_most":
                fields["bound"] = self._check_field(e, "bound", r, CHK["expected"])
        place = {"actual": CHK["actual"], "expected": CHK["expected"], "min": CHK["expected"],
                 "bound": CHK["expected"], "max": CHK["max"], "tolerance": CHK["tol"]}
        # Value cells: generated checks know their format (counts; a control total in its raw column's format);
        # a spec check's values stay General. The difference shows two decimals (BOOKS B-44), so floating-point
        # noise such as 2E-11 reads 0.00 (render review, integrator).
        vfmt = e.get("format") or "general"
        dfmt = vfmt if e.get("format") else "amount2"
        # v5 (J-2): the difference of two dates is a number of days, not a date (a date format shows 0 as a day of
        # 1900); Actual and Expected keep the date format, the tolerance stays General
        dfmt = CHECK_DIFF_FORMAT.get(vfmt, dfmt)
        width = self._check_value_width(e, vfmt, dfmt)
        for key, f in fields.items():
            if f is not None:
                self.put(name, r, place[key], f, self._ctype(f, "calc", name),
                         "general" if key == "tolerance" else vfmt, width=width)
        D, E, F, G, H = (_L(CHK[k]) for k in ("actual", "expected", "max", "diff", "tol"))
        if not e["auto"]:
            self._self_check_warning(e, fields, r)
        if kind == "equal":
            self.put(name, r, CHK["diff"], self.gen(f"{D}{r}-{E}{r}", f"{name}!{G}{r}"), "calc", dfmt, width=width)
            result = f"--(ABS({G}{r})>{H}{r})"
        elif kind == "range":
            result = f"--(OR({D}{r}<{E}{r},{D}{r}>{F}{r}))"
        elif kind == "zero":
            result = f"--({D}{r}<>0)"
        else:
            result = f"--({D}{r}>{E}{r})"
        # v4 (J-1): an alert row's Result shows ALERT when raised and OK when not (cell type alert), never FAIL
        result_formula = self.gen(result, f"{name}!I{r}")
        e["result_formula"] = str(result_formula)      # v5 (A-aera-3, C-sol-5): recorded in the manifest
        self.put(name, r, CHK["result"], result_formula, "alert" if e in self.alert_rows else "check", None, width=7)
        targets = e.get("targets") or []
        if not targets and not e["auto"]:
            targets = self._check_targets(e)
            e["targets"] = targets
        if targets:
            self.put(name, r, CHK["target"], ", ".join(targets), "note", "general")

    def _check_value_width(self, e: dict[str, Any], vfmt: str, dfmt: str) -> float:
        """The width of the checks sheet's value columns D:H for check row `e` (v3, J-1): the widest known value
        of the row in its number format (the control total, the landed sum, the row counts, the tolerance), the
        same width for the formula compared with it, and at least CHECK_VALUE_MIN_WIDTH. A specification check's
        values are not known at build time: its width is the estimate for a formula in its format (the largest
        raw column total, _formula_width). One width for the five columns: the widest row sets D to H."""
        widths = [CHECK_VALUE_MIN_WIDTH]
        for v in e.get("known") or []:
            widths += [_display_width(v, vfmt), _display_width(v, dfmt)]
        for v in e.get("known_tol") or []:
            widths.append(_display_width(v, "general"))
        if not e["auto"]:
            widths += [self._formula_width(vfmt), self._formula_width(dfmt)]
            chk = e["spec"]
            tol = chk.get("tolerance", "check_tolerance") if e["kind"] == "equal" else None
            te = self.L["inputs"].get(tol) if tol else None
            if te is not None and te.get("kind") == "scalar":
                tv = (te.get("item") or {}).get("value")
                if tv is None and tol == "check_tolerance":
                    tv = self.settings["check_tolerance"]
                widths.append(_display_width(tv, "general"))
        w = max(widths)
        cw = self.widths[self.L["names"]["checks"]]
        for col in range(CHK["actual"], CHK["tol"] + 1):
            cw[col] = max(cw.get(col, 0), w)
        return w

    def _self_check_warning(self, e: dict[str, Any], fields: dict[str, str | None], row: int) -> None:
        """W-SELF-CHECK (v3, A-aera-19): a specification check of kind equal or at_most whose actual and expected
        (or bound) compile to the same formula (compared without $ and spaces) or to links to the same cell can
        never fail. A range check is not compared: its actual equal to its minimum can still exceed its
        maximum."""
        other = {"equal": "expected", "at_most": "bound"}.get(e["kind"])
        a, b = fields.get("actual"), fields.get(other) if other else None
        if not a or not b:
            return
        host = self.L["names"]["checks"]
        if not _same_check_side(self.formula, a, b, host):
            return
        show_a = self.formula.strip_xlfn(str(a)).lstrip("=")
        show_b = self.formula.strip_xlfn(str(b)).lstrip("=")
        self.report.warning(
            "W-SELF-CHECK", f"check {e['id']}: actual ({show_a}) and expected ({show_b}) are the same, so the check "
            "can never fail", "point one side at an independent total: a raw column total, a control total or "
            "another block's total", sheet=host, cell=f"{_L(CHK['actual'])}{row}",
            data={"check": e["id"], "actual": show_a, "expected": show_b, "path": e.get("path")})

    def _check_targets(self, e: dict[str, Any]) -> list[str]:
        from . import spec as specmod
        out: list[str] = []
        chk = e["spec"]
        for key in ("actual", "expected", "min", "max", "bound"):
            v = chk.get(key)
            if not isinstance(v, str):
                continue
            for ph in specmod.placeholders(v):
                if ph.kind == "blk" and ph.arg:
                    bid = ph.arg.split(".")[0]
                    b = self.L["blocks"].get(bid)
                    if b:
                        t = f"{b['sheet']}!{_L(b['first_col'])}{b['first_row']}:{_L(b['last_col'])}{b['last_row']}"
                        if t not in out:
                            out.append(t)
                elif ph.kind == "cell" and ph.arg and ph.arg in self.L["cells"]:
                    c = self.L["cells"][ph.arg]
                    t = f"{c['sheet']}!{_L(c['col'])}{c['row']}"
                    if t not in out:
                        out.append(t)
                elif ph.kind in ("raw", "h") and ph.arg:
                    table = ph.arg.split(".")[0]
                    src = self.L["raw" if ph.kind == "raw" else "helpers"].get(table)
                    if src and src["sheet"] not in out:
                        out.append(src["sheet"])
        return out

    def _write_checks(self, s: Any) -> None:
        name = s.name
        self._sheet_head(s, self._sheet_title(s))
        for k, text in enumerate(CHECK_HEADERS):
            self.put(name, CHECK_HEADER_ROW, CHK["id"] + k, text, "col_header", "general")
        for e in self.check_rows:
            self._write_check_row(e)
        last = self.check_last
        master = (f"--(OR(SUM(I{CHECK_FIRST_ROW}:I{last})>0,COUNT(I{CHECK_FIRST_ROW}:I{last})<>"
                  f"COUNTA(B{CHECK_FIRST_ROW}:B{last})))")
        master_formula = self.gen(master, f"{name}!C{MASTER_ROW}")
        self.master_formula = str(master_formula)       # v5 (A-aera-3, C-sol-5): recorded in the manifest
        self.put(name, MASTER_ROW, UNIT_COL, master_formula, "master_check", None, width=7)
        if self.alert_rows:
            hr = self.alert_heading
            self.put(name, hr, LABEL_COL, "Alerts (not part of the master check)", "heading", "general", width=8)
            for k, text in enumerate(CHECK_HEADERS):
                self.put(name, hr + 1, CHK["id"] + k, text, "col_header", "general")
            for e in self.alert_rows:
                self._write_check_row(e)
        if self.outside_rows:
            hr = self.outside_heading
            self.put(name, hr, LABEL_COL, "Checked by rotm.py check (no cell in the workbook)", "heading", "general",
                     width=8)
            self.put(name, hr + 1, CHK["id"], "Id", "col_header", "general")
            self.put(name, hr + 1, CHK["label"], "What it checks", "col_header", "general")
            for e in self.outside_rows:
                self.put(name, e["row"], CHK["id"], e["id"], "row_header", "general")
                self.put(name, e["row"], CHK["label"], e["label"], "note", "general")

    def _block_check_warnings(self) -> None:
        named: dict[str, list[str]] = {}
        from . import spec as specmod
        for cid, info in self.spec.checks.items():
            if info["data"].get("severity", "error") != "error":
                continue            # v2, A-aera-14: an alert is not part of the master check: not a block's check
            for key in ("actual", "expected", "min", "max", "bound"):
                v = info["data"].get(key)
                if not isinstance(v, str):
                    continue
                for ph in specmod.placeholders(v):
                    if ph.kind == "blk" and ph.arg:
                        named.setdefault(ph.arg.split(".")[0], []).append(cid)
                    elif ph.kind == "cell" and ph.arg:
                        ko = self.spec.key_outputs.get(ph.arg)
                        if ko and not ko.get("ambiguous"):
                            named.setdefault(ko["block"], []).append(cid)
        self.block_checks = named
        for bid, b in self.L["blocks"].items():
            if bid not in named:
                self.report.warning("W-NO-CHECK", f"block '{bid}' on sheet {b['sheet']} is not named by any check of "
                                    "severity error (an alert does not count)",
                                    f"add a check to the checks sheet that reads {{blk:{bid}}} or a key output of it, "
                                    "against an independent total (spec 2.2 P5; references/checks.md)",
                                    sheet=b["sheet"], cell=f"{_L(b['first_col'])}{b['first_row']}")

    # ------------------------------------------------------------------ notes and cover
    def _write_notes(self, s: Any) -> None:
        name = s.name
        self._sheet_head(s, self._sheet_title(s))
        notes = s.data.get("notes", [])
        r = FIRST_BLOCK_ROW
        self.put(name, r, LABEL_COL, "Notes", "heading", "general", width=8)
        r += 1
        for c, text in ((LABEL_COL, "#"), (UNIT_COL, ""), (FIRST_VALUE_COL, "Note"), (FIRST_VALUE_COL + 1, "Source")):
            self.put(name, r, c, text, "col_header", "general")
        for k, n in enumerate(notes):
            r += 1
            # v4 (C-sol-13): the ordinal is a number (General shows 1 as 1), so Excel draws no marker for a number
            # stored as text; the cell keeps the row header look
            self.put(name, r, LABEL_COL, k + 1, "row_header", "general", width=4)
            self.put(name, r, UNIT_COL, "", "unit", "general")
            self._note(name, r, FIRST_VALUE_COL, n["text"])
            self._note(name, r, FIRST_VALUE_COL + 1, n.get("source", ""))

    def _write_cover(self, s: Any) -> None:
        name = s.name
        self._sheet_head(s, self._sheet_title(s))
        r = FIRST_BLOCK_ROW
        if self.spec.data.get("purpose"):
            self.put(name, r, LABEL_COL, "Purpose", "heading", "general", width=8)
            # A long text in B spills to the right past the sheet's used columns and is cut off there (render
            # review, integrator): write it as lines of at most COVER_LINE characters, one row each.
            for line in _cover_lines(self.spec.data["purpose"]):
                r += 1
                self.put(name, r, LABEL_COL, line, "label", "general", width=10)
            r += 2
        self.put(name, r, LABEL_COL, "Contents", "heading", "general", width=8)
        r += 1
        for c, text in ((LABEL_COL, "Sheet"), (UNIT_COL, "Type"), (FIRST_VALUE_COL, "Font size"),
                        (FIRST_VALUE_COL + 1, "Purpose")):
            self.put(name, r, c, text, "col_header", "general")
        default_purpose = {"cover": "this page: contents, cell types, master check, build record",
                           "inputs": "every typed number of the model: assumptions, lists, grids, contract controls",
                           "raw": "landed raw data, values only (never typed or edited)",
                           "helpers": "one row per raw row: fetched raw columns and row-wise helper columns",
                           "calc": "calculation blocks", "output": "key results",
                           "checks": "every check and the master check",
                           "notes": "notes with sources"}
        for sh in self.spec.sheets:
            r += 1
            self.put(name, r, LABEL_COL, sh.name, "row_header", "general")
            self.put(name, r, UNIT_COL, sh.type, "unit", "general")
            self.put(name, r, FIRST_VALUE_COL, f"Arial {sh.font_size}", "label", "general", width=9)
            self._note(name, r, FIRST_VALUE_COL + 1, sh.data.get("purpose") or default_purpose.get(sh.type, ""))
        r += 2
        self.put(name, r, LABEL_COL, "Cell types", "heading", "general", width=8)
        r += 1
        for c, text in ((LABEL_COL, "Cell type"), (UNIT_COL, ""), (FIRST_VALUE_COL, "Looks like"),
                        (FIRST_VALUE_COL + 1, "Meaning")):
            self.put(name, r, c, text, "col_header", "general")
        order = [t for t in self.d.cell_types() if t in self.used_types or t in ("input", "calc", "check")]
        for t in order:
            r += 1
            self.put(name, r, LABEL_COL, t, "row_header", "general")
            self.put(name, r, UNIT_COL, "", "unit", "general")
            self.put(name, r, FIRST_VALUE_COL, "Sample", t, "general", width=9)
            meaning = (self.d.data["cell_types"][t].get("meaning") or {}).get("value", "")
            self._note(name, r, FIRST_VALUE_COL + 1, meaning)
        r += 2
        self.put(name, r, LABEL_COL, "Build record", "heading", "general", width=8)
        applied = ", ".join(self.d.applied) if self.d.applied else "none"
        st = self.settings
        record = [
            ("Built with", f"{common.TOOL} {common.VERSION} (rotm.py build)"),
            ("Specification", f"{self.spec.path.name}, sha256 {self.spec.sha256[:12]}"),
            ("Dictionary", f"{self.d.data.get('name', 'style dictionary')} {self.d.data.get('version', '')}; "
                           f"overrides applied: {applied}"),
            ("Rules", f"nesting depth at most {st['depth_limit']} functions; formulas of at most "
                      f"{st['length_limit']} characters (a warning above); product profile {self.spec.product}"),
            ("Values", "computed by Excel when the file is opened or by rotm.py check; build writes no values"),
        ]
        for label, text in record:
            r += 1
            self.put(name, r, LABEL_COL, label, "row_header", "general")
            self.put(name, r, UNIT_COL, "", "unit", "general")
            self._note(name, r, FIRST_VALUE_COL + 1, text)
        lim = self.spec.data.get("limitations") or []
        if lim:
            r += 2
            self.put(name, r, LABEL_COL, "Limitations", "heading", "general", width=8)
            for text in lim:
                for line in _cover_lines(text):
                    r += 1
                    self.put(name, r, LABEL_COL, line, "label", "general", width=10)

    # ------------------------------------------------------------------ sheet view, widths, manifest
    def _finish_sheet(self, s: Any) -> None:
        ws = self.ws[s.name]
        view = self.d.sheet_view(s.type)
        ws.sheet_view.showGridLines = bool(view["gridlines"])
        ws.sheet_view.zoomScale = int(view["zoom"])
        if view["freeze"]:
            ws.freeze_panes = view["freeze"]
        widths = self.widths[s.name]
        block_sheet = s.type not in ("raw", "helpers")
        if block_sheet:
            widths[1] = 2.0
        for col, w in widths.items():
            if block_sheet and col == 1:
                width = 2.0
            elif block_sheet and col == LABEL_COL:
                width = min(max(w, 12.0), 45.0)
            elif block_sheet and col == UNIT_COL and s.type != "checks":
                width = min(max(w, 6.0), 30.0)
            elif s.type == "checks" and col == CHK["label"]:
                width = min(max(w, 20.0), 70.0)
            else:
                width = min(max(w, 4.0), 60.0)
            if self.sizes[s.name] == 8 and block_sheet and col > UNIT_COL:
                width *= 0.95
            ws.column_dimensions[_L(col)].width = round(width + 0.5, 1)
        # rows holding a wrapped note: as many lines as the text needs at the column's final width
        # (Arial at the sheet's size fits about 1.25 characters per width unit of the Normal font; measured on renders)
        per_line = 12.0 if self.sizes[s.name] >= 9 else 11.0
        for row, col, text in self.wrapped[s.name]:
            width = ws.column_dimensions[_L(col)].width or 8.43
            # Word by word, as Excel wraps, at 1.15 characters per width unit (1.25 under-counted a note with a
            # long unbreakable token and hid its first line: render review, integrator).
            lines = _wrap_line_count(text, max(1.0, (width - 1) * 1.15))
            if lines > 1:
                # A row with a wrapped note reads top down: every cell of the row aligned to the top (the label of
                # a row otherwise sits at its bottom, two lines below the start of its note; render review).
                from openpyxl.styles import Alignment
                for cell in ws[row]:
                    if cell.value is not None:
                        a = cell.alignment
                        cell.alignment = Alignment(horizontal=a.horizontal, vertical="top", wrap_text=a.wrap_text,
                                                   indent=a.indent)
                self.heights[s.name][row] = max(self.heights[s.name].get(row, 0), lines * per_line + 1)
        for row, height in self.heights[s.name].items():
            ws.row_dimensions[row].height = round(height, 1)

    def _sparse_sheet_notes(self) -> None:
        """I-SPARSE-SHEET (v5, A-aera-15, reduced scope; information, the strict exit code is unchanged): a
        calculation sheet without "font_size" in its spec entry that takes size 8 from its role and holds fewer
        populated cells (cells with a value or a formula) than the dictionary's rules.sparse_sheet_cells. build does
        not change the size: choosing it is Max's (SYNTHESIS D18). Raw and helper sheets are out of scope: they are
        tables of one row per raw row."""
        limit = self.d.rule("sparse_sheet_cells", None)
        if not isinstance(limit, (int, float)) or isinstance(limit, bool):
            return
        for s in self.spec.sheets:
            if s.type != "calc" or s.generated or "font_size" in s.data or self.sizes.get(s.name) != 8:
                continue
            ws = self.ws[s.name]
            n = sum(1 for c in ws._cells.values() if c.value is not None)
            if n >= limit:
                continue
            self.report.info(
                "I-SPARSE-SHEET", f"sheet '{s.name}' takes size 8 from its role (calc) and holds {n:,} populated cells, "
                f"fewer than rules.sparse_sheet_cells ({_num_text(limit)}); a sheet with white space takes size 9 "
                "(Max, spec 1.2)", f"if the sheet has white space, write \"font_size\": 9 in its entry of "
                f"model.spec.json ({s.path}); size 8 is for dense sheets", sheet=s.name,
                data={"sheet": s.name, "populated_cells": n, "limit": limit, "path": s.path})

    def _cell_type_ranges(self, sheet: str) -> list[list[str]]:
        cells = self.types.get(sheet, {})
        out: list[list[str]] = []
        by_row: dict[int, list[tuple[int, str]]] = {}
        for (r, c), t in cells.items():
            by_row.setdefault(r, []).append((c, t))
        runs: list[tuple[int, int, int, str]] = []      # (row, c1, c2, type)
        for r in sorted(by_row):
            items = sorted(by_row[r])
            start, prev, cur = items[0][0], items[0][0], items[0][1]
            for c, t in items[1:]:
                if c == prev + 1 and t == cur:
                    prev = c
                    continue
                runs.append((r, start, prev, cur))
                start, prev, cur = c, c, t
            runs.append((r, start, prev, cur))
        # merge runs with the same columns and type on consecutive rows
        open_: dict[tuple[int, int, str], list[int]] = {}
        rects: list[tuple[int, int, int, int, str]] = []
        for r, c1, c2, t in runs:
            key = (c1, c2, t)
            if key in open_ and open_[key][1] == r - 1:
                open_[key][1] = r
            else:
                if key in open_:
                    rects.append((c1, open_[key][0], c2, open_[key][1], t))
                open_[key] = [r, r]
        for (c1, c2, t), (r1, r2) in open_.items():
            rects.append((c1, r1, c2, r2, t))
        rects.sort(key=lambda x: (x[1], x[0]))
        for c1, r1, c2, r2, t in rects:
            rng = f"{_L(c1)}{r1}" if (c1, r1) == (c2, r2) else f"{_L(c1)}{r1}:{_L(c2)}{r2}"
            out.append([rng, t])
        return out + self.type_ranges.get(sheet, [])

    def _manifest(self) -> dict[str, Any]:
        from . import manifest as mf
        spec = self.spec
        out_dir = spec.output.parent
        sheets: list[dict[str, Any]] = []
        for s in spec.sheets:
            entry: dict[str, Any] = {"name": s.name, "type": s.type, "font_size": s.font_size}
            if s.type == "raw":
                table = s.data["table"]
                snap = self.snapshots.get(table)
                raw = self.L["raw"].get(table, {})
                entry.update({"table": table, "excel_table": f"tbl_raw_{table}",
                              "landing": s.data.get("landing", "static"),
                              "query_name": f"q_{table}" if s.data.get("landing") == "power_query" else None,
                              "columns": list(snap.columns) if snap else [],
                              "types": list(snap.types) if snap else [],
                              "formats": [self.raw_formats.get(table, {}).get(c) or
                                          self.d.format_for_column_type(t)
                                          for c, t in zip(snap.columns, snap.types)] if snap else [],
                              "rows": raw.get("rows", 0), "digest": snap.digest if snap else None,
                              "snapshot": f"raw/{table}.csv",
                              # v3, A-aera-4: the agreed contract this sheet was built from (check and audit
                              # compare it, CT-VERSION); A-aera-1: the rows build wrote into the sheet
                              "contract_sha256": ((spec.contracts.get(table) or {}).get("agreement") or {})
                              .get("contract_sha256"),
                              "workbook_digest": snap.digest if snap else None})
            elif s.type == "helpers":
                table = s.data["table"]
                h = self.L["helpers"].get(table)
                entry["table"] = table
                if h:
                    cols = []
                    for c in h["columns"]:
                        item = {"id": c["id"], "col": c["col"], "kind": c["kind"],
                                "formula": self.formula.strip_xlfn(c.get("formula") or "=") or "="}
                        if c.get("raw_column"):
                            item["raw_column"] = c["raw_column"]
                        item["header"] = c.get("header") or c["id"]
                        if c.get("format"):
                            item["format"] = c["format"]
                        if c["kind"] == "derived":
                            sc = c["spec"]
                            if sc.get("label"):
                                item["label"] = sc["label"]
                            if sc.get("type"):
                                item["type"] = sc["type"]
                        elif c.get("type"):
                            item["type"] = c["type"]
                        cols.append(item)
                    entry.update({"first_row": h["first_row"], "last_row": h["last_row"], "capacity": h["capacity"],
                                  "columns": cols, "capacity_source": h["capacity_source"]})
            elif s.type in ("calc", "output"):
                blocks = []
                for block in s.data["blocks"]:
                    b = self.L["blocks"].get(block["id"])
                    if b is None:
                        continue
                    body = f"{_L(b['first_col'])}{b['first_row']}:{_L(b['last_col'])}{b['last_row']}"
                    total = None
                    if b["total_row"] and b["total_col"]:
                        total = f"{_L(b['total_col'])}{b['total_row']}"
                    be: dict[str, Any] = {"id": b["id"], "shape": b["shape"], "body": body, "total": total,
                                          "checks": sorted(set(self.block_checks.get(b["id"], []))),
                                          "title": block.get("title") or b["id"],
                                          "heading_row": b["heading_row"], "header_row": b["header_row"],
                                          "labels": f"B{b['first_row']}:B{b['last_row']}",
                                          "total_row": b["total_row"], "total_col":
                                              _L(b["total_col"]) if b["total_col"] else None,
                                          "cols_kind": b["cols_dim"]["kind"], "rows_kind": b["rows_dim"]["kind"]}
                    if b["shape"] == "items":
                        be["items"] = [{"id": it["id"], "label": it["label"], "row": b["items"][it["id"]],
                                        "first": "first" in it, "key": bool(it.get("key"))}
                                       for it in block["items"]]
                    if b["shape"] == "measures":
                        be["measures"] = [{"id": m["id"], "label": m["label"], "col": _L(b["measures"][m["id"]]),
                                           "total": m.get("total", "none"), "key": bool(m.get("key"))}
                                          for m in block["measures"]]
                    blocks.append(be)
                entry["blocks"] = blocks
            elif s.type == "checks":
                entry.update({"master": f"C{MASTER_ROW}",
                              "results": f"I{CHECK_FIRST_ROW}:I{max(self.check_last, CHECK_FIRST_ROW)}",
                              "ids": f"B{CHECK_FIRST_ROW}:B{max(self.check_last, CHECK_FIRST_ROW)}"})
                if self.alert_rows:
                    a1, a2 = self.alert_rows[0]["row"], self.alert_rows[-1]["row"]
                    entry["alerts"] = f"I{a1}:I{a2}"
            sheets.append(entry)
        inputs = {}
        for iid, e in self.L["inputs"].items():
            sheet = common.quote_sheet(e["sheet"])
            if e["kind"] == "scalar":
                inputs[iid] = f"{sheet}!{_L(e['col'])}{e['row']}"
            elif e["kind"] == "list":
                inputs[iid] = f"{sheet}!{_L(e['col'])}{e['first']}:{_L(e['col'])}{e['last']}"
            else:
                inputs[iid] = f"{sheet}!{_L(e['c1'])}{e['first']}:{_L(e['c2'])}{e['last']}"
        cells = {k: f"{common.quote_sheet(c['sheet'])}!{_L(c['col'])}{c['row']}" for k, c in self.L["cells"].items()}
        cname = self.L["names"]["checks"]
        q = common.quote_sheet(cname)
        checks = []
        for e in self.check_rows + self.alert_rows:
            item = {"id": e["id"], "kind": e["kind"], "result": f"{q}!I{e['row']}", "auto": bool(e["auto"]),
                    "targets": list(e.get("targets") or []), "label": e["label"],
                    "severity": "alert" if e in self.alert_rows else "error"}
            if e.get("result_formula"):
                item["result_formula"] = e["result_formula"]      # v5: the Result cell's formula as build wrote it
            checks.append(item)
        master_links = [f"{common.quote_sheet(s.name)}!C{MASTER_ROW}" for s in spec.sheets
                        if s.type not in ("raw", "helpers", "checks")]
        cell_types = {s.name: self._cell_type_ranges(s.name) for s in spec.sheets}
        try:
            spec_rel = spec.path.relative_to(out_dir).as_posix()
        except ValueError:
            spec_rel = spec.path.as_posix()
        settings = {k: spec.settings[k] for k in spec.settings}
        settings["product"] = spec.product
        return {"rotm_manifest": mf.MANIFEST_VERSION, "plugin_version": common.VERSION, "built_at": common.now_iso(),
                "spec": spec_rel, "spec_sha256": spec.sha256, "dictionary_sha256": self.d.sha256,
                "dictionary_applied": list(self.d.applied), "workbook": spec.output.name, "settings": settings,
                "sheets": sheets, "inputs": inputs, "cells": cells, "checks": checks,
                "master_check": f"{q}!C{MASTER_ROW}", "master_formula": getattr(self, "master_formula", None),
                "master_links": master_links, "cell_types": cell_types,
                "outside_checks": [{"id": e["id"], "label": e["label"]} for e in self.outside_rows],
                "formula_cells": self.formula_cells}


# ----------------------------------------------------------------------------- public functions

def compile_spec(spec: Any, *, capacity: dict[str, int] | None = None) -> CompileResult:
    """Lay out every sheet of a lib.spec.Spec and write formulas and styles into a new openpyxl Workbook.
    Raises common.Refusal listing every problem (each with its JSON path and what to change)."""
    return _Compiler(spec, capacity).compile()


def resolve_expr(expr: str, ctx: RefContext, layout: dict[str, Any]) -> str:
    """A spec expression with placeholders -> an A1 formula with a leading '=' (design 6.6). `layout` is
    CompileResult.layout. Raises common.Refusal when a placeholder cannot be resolved there."""
    comp = _Compiler.__new__(_Compiler)
    comp.L = layout
    try:
        body, _segs = _Compiler.resolve(comp, expr, ctx)
    except _Problem as p:
        raise common.Refusal(p.message, fix=p.fix or None) from None
    return "=" + body


def parse_capacity(values: Iterable[str]) -> dict[str, int]:
    """['sales=900'] -> {'sales': 900}; refuses anything else."""
    out: dict[str, int] = {}
    for v in values or []:
        table, sep, n = str(v).partition("=")
        if not sep or not table.strip() or not n.strip().isdigit() or int(n) < 1:
            raise common.Refusal(f"--capacity {v!r} is not TABLE=ROWS", key="--capacity",
                                 fix="write it as --capacity sales=900 (a whole number of rows, 1 or more)")
        out[table.strip()] = int(n)
    return out


# The hash each command that writes the workbook records for the file it wrote (v2, A-aera-1). Read-only steps
# (audit, render, and check or recalc without a recalculation) record the hash of a file they did not write, so
# they are not listed: an edit made in Excel and then audited stays an edit build does not own. v3 (A-aera-2):
# a writer's output counts only when its input was owned (_known_hashes; modelfolder.write_pair).
WRITER_HASH_KEYS: dict[str, str] = {"build": "sha256", "check": "output_sha256", "recalc": "output_sha256",
                                    "theme": "output_sha256", "extend": "output_sha256", "land": "sha256",
                                    "compat": "output_sha256", "split": "output_sha256"}


def _known_hashes(root: Path, workbook: Path) -> set[str]:
    """Hashes of the workbook versions build owns (v3, A-aera-2): the output of every build pair of
    reports/writes.json and of the latest build record; then the output of every other writer's pair
    (modelfolder.write_pair: check only when it recalculated, land only when it attached a Power Query table,
    recalc, extend, theme, compat, split) whose recorded input hash is itself owned, repeated until nothing is
    added. The pairs come from writes.json and from the latest records of status.json. A pair without an input
    hash does not count, so a file edited in Excel and then checked or recalculated stays a file build does not
    own (it is refused without --force, and --force keeps a backup)."""
    from . import modelfolder
    rel = common.relpath_posix(workbook, root)
    pairs = list(modelfolder.read_writes(root).get(rel, []))
    for step, rec in modelfolder.read_status(root).get("workbooks", {}).get(rel, {}).items():
        if step in WRITER_HASH_KEYS and isinstance(rec, dict):
            pair = modelfolder.write_pair(step, rec)
            if pair is not None:
                pairs.append(pair)
    owned = {p["output_sha256"] for p in pairs if p.get("step") == "build"}
    rest = [p for p in pairs if p.get("step") != "build" and p.get("input_sha256")]
    grown = True
    while grown:
        grown = False
        for p in rest:
            if p["input_sha256"] in owned and p["output_sha256"] not in owned:
                owned.add(p["output_sha256"])
                grown = True
    return owned


def _save_atomic(wb: Any, target: Path) -> None:
    fd, tmp = tempfile.mkstemp(prefix=target.stem + ".", suffix=".tmp.xlsx", dir=str(target.parent))
    os.close(fd)
    try:
        wb.save(tmp)
        _replace_target(Path(tmp), target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _replace_target(tmp: Path, target: Path) -> None:
    try:
        os.replace(tmp, target)
    except PermissionError:
        raise common.Refusal(f"{target.name} cannot be replaced: another program holds it open (Excel?)",
                             fix=f"close {target.name} in Excel and run build again") from None


# ----------------------------------------------------------------------------- queries attached by hand (v4, C-sol-1)

HAND_QUERY_FILE = "queries/{table}.hand.pq.m"     # the M text of a query attached by hand; land never writes it


def hand_attached_tables(target: Path, old: dict[str, Any], spec: Any) -> dict[str, dict[str, Any]]:
    """The raw tables of the spec whose sheet in the existing workbook `target` is loaded by a query attached by
    hand (v4 plan 5.3): the old manifest's raw entry records query_attach "hand", or check.query_tables names the
    sheet and the entry does not record that rotm.py attached it (landing power_query, a query_contract_sha256,
    query_attach other than "hand" and query_name equal to the name query_tables gives).
    Returns table -> {"sheet", "old_sheet", "query_name" (None when not found)}."""
    from . import check as checkmod
    qtables = checkmod.query_tables(target)
    entries = {s.get("table"): s for s in (old.get("sheets") or []) if s.get("type") == "raw"}
    out: dict[str, dict[str, Any]] = {}
    for s in spec.sheets:
        if s.type != "raw":
            continue
        t = s.data["table"]
        e = entries.get(t) or {}
        old_sheet = e.get("name") or s.name
        in_workbook = old_sheet in qtables
        by_rotm = (e.get("landing") == "power_query" and bool(e.get("query_contract_sha256"))
                   and e.get("query_attach") != "hand" and e.get("query_name") == qtables.get(old_sheet))
        if e.get("query_attach") == "hand" or (in_workbook and not by_rotm):
            name = qtables.get(old_sheet) or (e.get("query_name") if e.get("query_attach") == "hand" else None)
            out[t] = {"sheet": s.name, "old_sheet": old_sheet, "query_name": name}
    return out


def _hand_name(h: dict[str, Any]) -> str:
    return h.get("query_name") or "(name not found)"


def _hand_refusal(hand: dict[str, dict[str, Any]], reasons: dict[str, str], *, no_power_query: bool = False
                  ) -> common.Refusal:
    """B-REFUSED for raw sheets loaded by a query attached by hand that build cannot carry over (v4 plan 5.1)."""
    lines = [f"{hand[t]['sheet']} is loaded by the query {_hand_name(hand[t])} attached by hand, and {why}; nothing "
             "was written" for t, why in reasons.items()]
    sheets = ", ".join(hand[t]["sheet"] for t in reasons)
    way = ("build without --no-power-query on a PC with Excel" if no_power_query else "run the build on a PC with Excel")
    fix = (f"{way} (build reads the query from the workbook and attaches it again), or pass --drop-hand-queries to "
           f"rebuild {sheets} as a static table (the query is removed and must be attached by hand again, "
           "references/excel-power-query.md \"Attaching a query by hand\")")
    return common.Refusal(lines[0], fix=fix, details=lines[1:])


def _read_hand_queries(target: Path, hand: dict[str, dict[str, Any]]) -> None:
    """Read each hand query's M text and the name of its query table from the existing workbook through Excel
    (lib.excel.inventory, opened read-only); fills "m_text" and "table_name". Raises the B-REFUSED refusal when
    Excel is not available or a text cannot be read; common.ExcelBusy when the Excel lock is not free."""
    from . import excel
    status = excel.excel_status()
    if not status.available:
        raise _hand_refusal(hand, {t: f"Excel is not available ({status.reason})" for t in hand})
    try:
        inv = excel.inventory(target)
    except common.ExcelBusy:
        raise
    except excel.ExcelJobFailed as exc:
        raise _hand_refusal(hand, {t: f"its M text could not be read from {target.name} ({_short(exc.message)})"
                                   for t in hand}) from None
    except common.EnvUnavailable as exc:
        raise _hand_refusal(hand, {t: f"Excel is not available ({_short(exc.message)})" for t in hand}) from None
    formulas: dict[str, Any] = {}
    for q in inv.get("query_formulas") or []:
        if isinstance(q, dict) and q.get("name"):
            formulas[str(q["name"])] = q.get("formula")
    sheets = {str(s.get("name")): s for s in inv.get("sheets") or [] if isinstance(s, dict)}
    missing: dict[str, str] = {}
    for t, h in hand.items():
        tables = [x for x in (sheets.get(h["old_sheet"]) or {}).get("tables") or []
                  if isinstance(x, dict) and x.get("query")
                  and re.match(r"^A1(:|$)", str(x.get("range") or "").replace("$", "").upper())]
        h["table_name"] = str(tables[0]["name"]) if tables else f"tbl_raw_{t}"
        if not h.get("query_name") and tables and tables[0].get("connection"):
            h["query_name"] = re.sub(r"^Query - ", "", str(tables[0]["connection"])).strip() or None
        text = formulas.get(h.get("query_name") or "")
        if not isinstance(text, str) or not text.strip():
            missing[t] = f"its M text could not be read from {target.name}"
        else:
            h["m_text"] = text
    if missing:
        raise _hand_refusal(hand, missing)


def _short(text: str, n: int = 200) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 3] + "..."


def _attach_hand_queries(tmp: Path, hand: dict[str, dict[str, Any]], manifest: dict[str, Any]) -> dict[str, Any]:
    """Attach each hand query to the new workbook `tmp` (not yet the target) with its old name, M text and table
    name. A sidecar manifest is written next to `tmp` for the Excel job (lib.excel.raw_formats gives the raw
    table's look from it) and removed afterwards. Returns table -> the sheet digest of the loaded rows. Raises the
    B-REFUSED refusal when an attach fails; common.ExcelBusy when the Excel lock is not free. v6 (r5 verdict
    A-aera-2): after each attach the query's formula is read back from `tmp` (land.read_query_formula) and its hash
    is put into the table's entry of `hand` as "query_formula_sha256" (None when it cannot be read)."""
    from . import excel
    from . import manifest as mf
    side = mf.write_manifest(tmp, manifest)
    digests: dict[str, Any] = {}
    try:
        for t, h in hand.items():
            try:
                res = excel.pq_attach(tmp, sheet=h["sheet"], table_name=h["table_name"], query_name=h["query_name"],
                                      m_text=h["m_text"])
            except common.ExcelBusy:
                raise
            except common.EnvUnavailable as exc:
                raise _hand_refusal(hand, {t: f"it could not be attached again ({_short(exc.message)})"}) from None
            if not res.ok:
                raise _hand_refusal(hand, {t: f"it could not be attached again ({_short(res.message())})"})
            # v6 (r5 verdict A-aera-2): the formula Excel holds for the query attached again, read back from the
            # saved file; build records its hash (query_formula_sha256) and check --refresh compares it
            from . import land
            formula = land.read_query_formula(tmp, h["query_name"], job=res)
            h["query_formula_sha256"] = land.query_formula_sha256(formula) if formula is not None else None
            types = (mf.raw_sheet(manifest, t) or {}).get("types") or []
            digests[t] = mf.sheet_digest(tmp, h["sheet"], types)     # (digest, rows, header)
    finally:
        try:
            side.unlink()
        except OSError:
            pass
    return digests


def _write_hand_query_file(root: Path, table: str, text: str, report: Any) -> str:
    """queries/<t>.hand.pq.m: the M text exactly as Excel gave it (UTF-8, no stamp). A different existing file is
    kept as queries/<t>.hand.pq.m.bak_<YYYYMMDD_HHMMSS>. Returns the path relative to the model folder."""
    rel = HAND_QUERY_FILE.format(table=table)
    path = root / rel
    data = text.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() != data:
        bak = path.with_name(f"{path.name}.bak_{_dt.datetime.now().strftime('%Y%m%d_%H%M%S')}")
        os.replace(path, bak)
        report.note(f"the previous {rel} was kept as {bak.name}")
    if not path.is_file():
        path.write_bytes(data)
    return rel


def _preflight_generated_queries(spec: Any, target: Path, old: dict[str, Any], hand: dict[str, Any],
                                 kept_pq: list[str]) -> None:
    """v5 (round 4 verdict A-aera-2, C-sol-1, C-sol-4, part 3): before the existing workbook is replaced, each
    generated query build will attach goes through the tests the attach makes before Excel starts
    (land.attached_query_text: the stamp is missing, the stamp is of another contract, a placeholder is left in the
    file). When one fails and the existing workbook holds a query table on that raw sheet, build refuses (B-REFUSED,
    exit 2) and writes nothing: the workbook would lose its query. Without such a query table (a first build)
    nothing is refused here; the attach after the build gives W-PQ-NOT-ATTACHED as before."""
    from . import check as checkmod
    from . import land
    from .contract import _rotm
    tables = [s.data["table"] for s in spec.sheets if s.type == "raw" and s.data.get("landing") == "power_query"
              and s.data["table"] not in hand]
    tables += [t for t in kept_pq if t not in tables]
    if not tables:
        return
    qtables = checkmod.query_tables(target)
    old_names = {s.get("table"): s.get("name") for s in (old.get("sheets") or []) if s.get("type") == "raw"}
    model = spec.root.as_posix()
    for t in tables:
        try:
            land.attached_query_text(spec.root, t, spec.contracts.get(t))
        except common.Refusal as exc:
            sheets = {spec.raw[t].name if t in spec.raw else f"raw_{t}", old_names.get(t) or f"raw_{t}"}
            loaded = [n for n in sheets if n in qtables]
            if not loaded:
                continue
            raise common.Refusal(
                f"{loaded[0]} of {target.name} is loaded by its Power Query table, and the query cannot be attached "
                f"again: {exc.message}; nothing was written",
                fix=f"write the query for the agreed contract with {_rotm()} land \"{model}\" --table {t} (land names "
                    "the next step when the file was edited by hand), then build again") from None


def _sync_marker_workbook(root: Path, target: Path, *, update: bool, report: Any) -> bool:
    """v4 (C-sol-9): one workbook name for a model folder. When `update` (build without -o) and `target` lies in the
    model folder, write its name into the marker's workbook field (rotm_model.json) when it differs, so audit,
    check, status, render and the hooks resolve the file just built. Returns True when the folder resolves to
    `target` (or the folder has no marker, as before v4); False after build -o to another file, with a note."""
    from . import modelfolder
    if not modelfolder.is_model_root(root):
        return True
    try:
        marker = modelfolder.read_marker(root)
    except common.RotmError:
        return True
    try:
        in_folder = target.parent.resolve() == root.resolve()
    except OSError:
        in_folder = False
    current = str(marker.get("workbook") or "model.xlsx")
    if update and in_folder and current != target.name:
        marker["workbook"] = target.name
        marker["updated"] = common.now_iso()
        common.write_json(root / modelfolder.MARKER, marker)
        report.note(f"rotm_model.json now names {target.name} as the model folder's workbook (the spec's output "
                    f"was {target.name}, the marker named {current}), so the folder commands and the hooks use it")
        current = target.name
    if in_folder and current == target.name:
        return True
    report.note(f"the folder commands use {current}, the workbook rotm_model.json names; for {target.name} name the "
                f"file: rotm.py check \"{target.as_posix()}\", rotm.py audit \"{target.as_posix()}\"")
    return False


def build(spec_path: str | Path, *, out: str | Path | None = None, force: bool = False, strict: bool = False,
          capacity: dict[str, int] | None = None, power_query: bool = True, drop_hand_queries: bool = False) -> Any:
    """Load the spec, compile, save the workbook (refuses to overwrite a workbook it did not build without --force),
    write the manifest, re-attach Power Query raw sheets through Excel, record the step. Returns a common.Report.

    v4 (C-sol-1): a raw sheet of the existing workbook loaded by a query attached by hand (hand_attached_tables) is
    carried over: its name, M text and table name are read through Excel before anything is written, the new
    workbook is written to a temporary file in the target's folder, the query is attached to it again, and only
    then the target, queries/<t>.hand.pq.m and the manifest (query_attach hand, no contract hash) are written.
    Without Excel, with a text that cannot be read or an attach that fails, build refuses (exit 2) and writes
    nothing. `drop_hand_queries` (--drop-hand-queries) rebuilds those sheets as static tables instead."""
    from . import land as land_mod
    from . import manifest as mf
    from . import modelfolder
    from . import spec as specmod
    spec = specmod.load_spec(spec_path, require_agreed=True)
    if out is not None:
        target = common.native_path(out)
        target = Path(os.path.abspath(target))
        if target.suffix.lower() != ".xlsx":
            raise common.Refusal(f"{target.name}: the output must be an .xlsx file", key="-o",
                                 fix="give a file name that ends in .xlsx")
        spec.output = target
    target = spec.output
    root = spec.root
    if target.exists():
        if target.is_dir():
            raise common.Refusal(f"{target.as_posix()} is a folder", key="-o", fix="give a workbook file name")
        current = common.file_sha256(target)
        ours = current in _known_hashes(root, target)
        if not ours and not force:
            why = ("it was not written by rotm.py" if not mf.exists(target)
                   else "it changed after rotm.py last wrote it (by build, check or another command)")
            raise common.Refusal(f"{target.name} exists and {why}; rebuilding would discard what is in it",
                                 fix="pass --force to rebuild it (a copy is kept as "
                                     f"{target.stem}.bak_<time>.xlsx), or build to another file with -o")
    kept_pq: list[str] = []
    old: dict[str, Any] = {}
    if target.exists() and mf.exists(target):
        try:
            old = mf.load_manifest(target) or {}
        except common.RotmError:
            old = {}
    # v4 (C-sol-1): raw sheets loaded by a query attached by hand are never given the generated query
    hand = hand_attached_tables(target, old, spec) if target.exists() else {}
    for s in old.get("sheets", []):
        t = s.get("table")
        if s.get("type") == "raw" and s.get("landing") == "power_query" and t in spec.raw and t not in hand \
                and spec.raw[t].data.get("landing", "static") != "power_query":
            kept_pq.append(t)
    if power_query and target.exists():
        _preflight_generated_queries(spec, target, old, hand, kept_pq)   # refuses before anything is written
    comp = _Compiler(spec, capacity)
    result = comp.compile()
    report = comp.report
    report.target = target.as_posix()
    carry = {} if drop_hand_queries else hand
    if carry and not power_query:
        raise _hand_refusal(carry, {t: "--no-power-query was given" for t in carry}, no_power_query=True)
    if carry:
        _read_hand_queries(target, carry)        # refuses (exit 2) before anything is written
    target.parent.mkdir(parents=True, exist_ok=True)
    carried: dict[str, Any] = {}
    tmp: Path | None = None
    try:
        if carry:
            fd, tmp_name = tempfile.mkstemp(prefix=target.stem + ".", suffix=".tmp.xlsx", dir=str(target.parent))
            os.close(fd)
            tmp = Path(tmp_name)
            result.workbook.save(tmp)
            carried = _attach_hand_queries(tmp, carry, result.manifest)
        backup = None
        if target.exists() and not (common.file_sha256(target) in _known_hashes(root, target)):
            backup = target.with_name(f"{target.stem}.bak_{_dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")
            import shutil
            shutil.copy2(target, backup)
            report.note(f"the previous {target.name} was kept as {backup.name}")
        if tmp is not None:
            _replace_target(tmp, target)
        else:
            _save_atomic(result.workbook, target)
    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink()
    # v4 (C-sol-9): without -o the marker's workbook follows the spec's output; the commands the report prints name
    # the folder when it resolves to the file just built, else the file
    folder_resolves = _sync_marker_workbook(root, target, update=out is None, report=report)
    ref = root.as_posix() if folder_resolves else target.as_posix()
    land_note = "" if folder_resolves else (f" (land attaches to the workbook rotm_model.json names, not to "
                                            f"{target.name})")
    for t, h in hand.items():
        e = mf.raw_sheet(result.manifest, t)
        if e is None:
            continue
        if t in carried:
            rel = _write_hand_query_file(root, t, h["m_text"], report)
            sheet_digest, rows, _header = carried[t]
            e.update(landing="power_query", query_attach="hand", query_name=h["query_name"], query_file=rel)
            e.pop("query_contract_sha256", None)
            if h.get("query_formula_sha256"):
                e["query_formula_sha256"] = h["query_formula_sha256"]
            else:
                e.pop("query_formula_sha256", None)
                report.note(land_mod.FORMULA_NOT_READ_NOTE.format(q=h["query_name"]))
            if h["table_name"] != e.get("excel_table"):
                e["excel_table"] = h["table_name"]
            if sheet_digest == e.get("digest"):
                e["workbook_digest"] = sheet_digest
            else:
                report.warning("W-PQ-DIGEST", f"the query {h['query_name']} attached by hand loaded {rows:,} rows into "
                               f"{h['sheet']} that differ from the manifest's raw digest (raw/{t}.csv)",
                               f"run rotm.py check \"{ref}\" --accept-refresh to validate the new rows "
                               "against the contract and record them", sheet=h["sheet"])
            report.note(f"{h['sheet']}: the query {h['query_name']} attached by hand was carried over: its M text is "
                        f"kept in {rel} and it was attached again with the same name; it was not compared with the "
                        "agreed contract")
        else:
            e["landing"] = "static"
            for k in [k for k in e if k.startswith("query_")]:
                e.pop(k)
            report.warning("W-PQ-NOT-ATTACHED", f"{h['sheet']} holds static values: --drop-hand-queries removed the "
                           f"query {_hand_name(h)} attached by hand; attach it by hand again",
                           "attach it as references/excel-power-query.md \"Attaching a query by hand\" says, then run "
                           f"rotm.py check \"{ref}\" --accept-refresh", sheet=h["sheet"])
    mf.write_manifest(target, result.manifest)
    # Power Query raw sheets: build writes static values with openpyxl, then re-attaches each query (design 7.3).
    # v4 (C-sol-1): never in place of a query attached by hand.
    pq_tables = [s.data["table"] for s in spec.sheets if s.type == "raw" and s.data.get("landing") == "power_query"
                 and s.data["table"] not in hand]
    pq_tables += kept_pq
    attached = []
    if pq_tables:
        if not power_query:
            for t in pq_tables:
                report.warning("W-PQ-NOT-ATTACHED", f"raw_{t} holds static values: --no-power-query was given",
                               f"attach the query later with rotm.py land \"{root.as_posix()}\" --table {t} "
                               f"--power-query{land_note}", sheet=f"raw_{t}")
        else:
            from . import excel, land
            status = excel.excel_status()
            for t in pq_tables:
                if not status.available:
                    report.warning("W-PQ-NOT-ATTACHED", f"raw_{t} holds static values: Excel is not available "
                                   f"({status.reason})", f"on a PC with Excel run rotm.py land \"{root.as_posix()}\" "
                                   f"--table {t} --power-query{land_note}", sheet=f"raw_{t}")
                    continue
                try:
                    sub = land.attach_power_query(target, root, t)
                    report.extend(sub.findings)
                    attached.append(t)
                    if t in kept_pq:
                        # v4 (C-sol-1): only for a generated query that was attached again
                        report.note(f"raw_{t} keeps its Power Query table (attached earlier with land --power-query); "
                                    f"write \"landing\": \"power_query\" on raw_{t} in the spec to record it there")
                except common.RotmError as exc:
                    report.warning("W-PQ-NOT-ATTACHED", f"raw_{t} holds static values: the query was not attached "
                                   f"({exc.line()})", f"correct queries/{t}.pq.m, then run rotm.py land "
                                   f"\"{root.as_posix()}\" --table {t} --power-query{land_note}", sheet=f"raw_{t}")
    if not modelfolder.is_model_root(root):
        report.info("I-NO-MARKER", f"{root.as_posix()} has no rotm_model.json, so the plugin's hooks do not watch it",
                    f"run rotm.py init \"{root.as_posix()}\" to make it a model folder")
    # v4 (A-aera-1): labels that Excel's criteria matching merges, on the landed rows (D-CRITERIA, warning)
    from . import criteria
    report.extend(criteria.criteria_findings(target, result.manifest))
    counts = report.counts()
    report.note("values not verified: the workbook holds no cached values (not recalculated); run rotm.py check "
                f"\"{ref}\" to recalculate it in Excel and read every check")
    report.data.update({"sheets": [s.name for s in spec.sheets], "formulas": comp.formula_cells,
                        "manifest": mf.manifest_path(target).name, "power_query_attached": attached,
                        "hand_queries_carried": {t: {"query": hand[t]["query_name"],
                                                     "file": HAND_QUERY_FILE.format(table=t)} for t in carried},
                        "hand_queries_dropped": [t for t in hand if t not in carried],
                        "capacity": {t: h["capacity"] for t, h in comp.L["helpers"].items()},
                        "rows": {t: r["rows"] for t, r in comp.L["raw"].items()}})
    code = report.finish(strict)
    try:
        modelfolder.record_step(root, target.as_posix(), "build", {
            "exit_code": code, "sha256": common.file_sha256(target), "errors": counts["errors"],
            "warnings": counts["warnings"], "strict": bool(strict), "spec_sha256": spec.sha256})
        report.write_json(root / "reports" / "build.json")
    except (common.RotmError, OSError, ValueError) as exc:
        report.note(f"status not recorded in reports/status.json: {exc}")
    return report


def add_build_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("spec", help="the model specification, e.g. model.spec.json (or its model folder)")
    p.add_argument("-o", "--out", help="output workbook (default: the spec's output)")
    p.add_argument("--force", action="store_true",
                   help="overwrite a workbook rotm.py did not write, or that changed since (a backup is kept)")
    p.add_argument("--strict", action="store_true", help="exit 1 on any warning (the workbook is still written)")
    p.add_argument("--capacity", action="append", default=[], metavar="TABLE=ROWS",
                   help="helper rows for a raw table (default: the landed rows; more than the landed rows is "
                        "refused, fewer warns W-CAPACITY)")
    p.add_argument("--no-power-query", action="store_true", help="write raw sheets as static values only")
    p.add_argument("--drop-hand-queries", action="store_true",
                   help="rebuild each raw sheet loaded by a query attached by hand as a static table: the query is "
                        "removed and must be attached by hand again (without it build carries such a query over "
                        "through Excel, or refuses when Excel is not available)")
    p.add_argument("--json", metavar="FILE", help="write the report as JSON ('-' = stdout)")
    p.add_argument("--quiet", action="store_true", help="print only the summary line")


def run_build(args: argparse.Namespace) -> int:
    try:
        capacity = parse_capacity(args.capacity)
        report = build(args.spec, out=args.out, force=args.force, strict=args.strict, capacity=capacity,
                       power_query=not args.no_power_query,
                       drop_hand_queries=bool(getattr(args, "drop_hand_queries", False)))
    except common.Refusal as exc:
        if args.json:
            rep = common.Report("build", str(args.spec), strict=bool(args.strict))
            rep.error("B-REFUSED", exc.message, exc.fix or "", data={"details": exc.details})
            for d in exc.details:
                rep.note(d)
            rep.finish(args.strict, refused=True)
            rep.write_json(args.json)
        raise
    code = report.emit(args.json, quiet=getattr(args, "quiet", False))
    if args.json != "-":
        print(f"wrote {report.target} ({report.data.get('formulas', 0)} formulas, "
              f"{len(report.data.get('sheets', []))} sheets) and {report.data.get('manifest')}")
    return code

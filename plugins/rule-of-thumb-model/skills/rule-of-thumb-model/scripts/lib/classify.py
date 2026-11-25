"""Classify the cells of any workbook into dictionary cell types (design sections 10.5, 14).

Owner: M2. Method adapted from research/scripts/style_survey.py (numeric constant, formula, pure link, text
label, header detection above runs of numbers, check labels).

Rules (written out in references/style-dictionary.md, section "How a workbook the plugin did not build is read"):
- The classifier reads values, formulas and positions only, never styles. theme changes styles only, so the
  types (and the sizes) the audit infers after theme are the ones theme used: theme followed by audit is stable.
- With a manifest, its cell_types win for the ranges it lists and its sheet entries give the sheet types and
  sizes; cells it does not list are classified by the rules below.
- Raw sheets (manifest type raw; names matching RAW_SHEET_RE; a table loaded by a query; --raw-sheets): the
  first non-empty row is col_header, every other cell is raw.
- Other sheets: a header-like cell (text, a date, or a year 1990 to 2100) found above the first cell of a run of
  numbers or formulas in the same column (at most 6 rows up) is col_header, when the run has 2 or more cells or the
  row has 2 or more such headers; a header row is extended to header-like cells beside it (gaps of at most 2
  empty columns). Text left of the first value of a row is row_header (unit in a column headed unit, units, uom
  or 단위); text right of the last value is note; text between values is label. Formulas: check or master_check
  next to a check label, with an OK/ERROR-style text result, or a comparison on a sheet named check; a pure
  reference is link (same sheet) or import (another sheet or workbook); a formula in a total row or column is
  total; a formula another sheet reads is export; any other formula is calc. Typed numbers, dates and booleans
  are input. Other text: title (the first text in rows 1 to 3), note (40 characters or more, or starting with
  note, source, 출처, 참고, ※), heading (short text alone in its row with values or headers within 2 rows below),
  label.
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from typing import Any

CELL_TYPES = ("input", "calc", "link", "import", "export", "check", "master_check", "note", "col_header",
              "row_header", "unit", "raw", "title", "heading", "total", "output", "label")
SHEET_TYPES = ("cover", "inputs", "raw", "helpers", "calc", "output", "checks", "notes")
RAW_SHEET_RE = r"(?i)^(raw|data|src|landed)[_ -]?|_raw$"
CHECK_WORDS = ("check", "chk", "control", "error", "diff", "검증", "체크", "차이", "오류")

# Thresholds of the density rule when the dictionary gives none (dictionary rules dense_fill_ratio and
# dense_min_cells; SYNTHESIS D18, builder default).
DENSE_FILL_RATIO = 0.6
DENSE_MIN_CELLS = 500
YEAR_MIN, YEAR_MAX = 1990, 2100
HEADER_LOOKUP_ROWS = 6
HEADER_GAP_COLS = 2
NOTE_MIN_CHARS = 40
CHECK_LABEL_MAX_CHARS = 60

_RAW_RE = re.compile(RAW_SHEET_RE)
_CHECK_RE = re.compile("|".join(re.escape(w) for w in CHECK_WORDS) + r"|reconcil", re.I)
_CHECK_EXCLUDE_RE = re.compile(r"balance\s*sheet|대차대조표", re.I)
_MASTER_RE = re.compile(r"(master|overall|global|model|total|all|전체|종합|마스터)\s*[-_ ]?\s*(check|chk|control|error|"
                        r"검증|체크|오류)", re.I)
_RESULT_WORDS = {"OK", "ERROR", "FAIL", "PASS", "CHECK", "TRUE", "FALSE", "ERR", "동일", "차이", "오류", "정상"}
_TOTAL_RE = re.compile(r"^\s*(total|sub-?total|grand\s+total|합계|총계|소계|계)\b|^\s*(합계|총계|소계)", re.I)
_NOTE_RE = re.compile(r"^\s*(note|notes|source|sources|주\s*[):]|※|\*|출처|참고)", re.I)
_UNIT_WORDS = {"unit", "units", "uom", "unit of measure", "단위"}
_NAME_TYPES = (   # sheet name -> sheet type, first match wins (after raw and helper sheets)
    ("checks", re.compile(r"(?i)check|chk|검증|체크|점검")),
    ("cover", re.compile(r"(?i)cover|contents|^toc$|^index$|readme|read me|표지|목차")),
    ("inputs", re.compile(r"(?i)input|assum|driver|param|가정|입력|전제")),
    ("notes", re.compile(r"(?i)^notes?$|memo|참고|주석")),
    ("output", re.compile(r"(?i)output|summary|dashboard|report|result|요약|결과|보고")),
)
_HELPER_RE = re.compile(r"(?i)^h_|helper")


@dataclass
class SheetPlan:
    """How one sheet is read: its sheet type, font size (8 or 9) and why."""
    name: str
    sheet_type: str
    size: int
    raw: bool
    source: str                 # "manifest" | "inferred"
    reason: str
    density: dict[str, Any] = field(default_factory=dict)   # filled cells, block area, ratio


# ----------------------------------------------------------------------------- small helpers

def _is_year(v: Any) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool) and float(v).is_integer()
            and YEAR_MIN <= v <= YEAR_MAX)


def _value_kind(v: Any) -> str:
    """text | date | year | num | bool | err | blank for a value (a constant or a cached result)."""
    from . import workbook
    if v is None:
        return "num"                           # a formula without a cached value: shown as a number in most models
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return "date"
    if isinstance(v, (int, float)):
        return "year" if _is_year(v) else "num"
    if isinstance(v, str):
        if v in workbook.ERROR_VALUES:
            return "err"
        return "text" if v.strip() else "blank"
    return "num"


def _layout_kind(c: Any) -> str:
    if c.formula is not None or c.formula_kind in ("data_table", "array_member", "data_table_member"):
        return _value_kind(c.cached)
    if c.data_type == "e":
        return "err"
    return _value_kind(c.value)


def _header_like(kind: str) -> bool:
    return kind in ("text", "date", "year")


def _data_like(kind: str) -> bool:
    return kind in ("num", "bool", "err")


def _row_value(kind: str) -> bool:
    return kind in ("num", "bool", "err", "date", "year")


def _is_formula(c: Any) -> bool:
    return c.formula is not None or c.formula_kind in ("data_table", "array_member", "data_table_member")


def _text(c: Any) -> str:
    v = c.cached if _is_formula(c) else c.value
    return v if isinstance(v, str) else ""


def is_raw_name(name: str) -> bool:
    return bool(_RAW_RE.search(name))


# ----------------------------------------------------------------------------- sheets

def density(sheet: Any) -> dict[str, Any]:
    """Filled cells, the area of the block from the first to the last used row and column, and their ratio."""
    if not sheet.cells:
        return {"filled": 0, "area": 0, "ratio": 0.0}
    rows = [r for r, _c in sheet.cells]
    cols = [c for _r, c in sheet.cells]
    area = (max(rows) - min(rows) + 1) * (max(cols) - min(cols) + 1)
    filled = len(sheet.cells)
    return {"filled": filled, "area": area, "ratio": round(filled / area, 4) if area else 0.0}


def _thresholds(dictionary: Any) -> tuple[float, int]:
    if dictionary is None:
        return DENSE_FILL_RATIO, DENSE_MIN_CELLS
    return (float(dictionary.rule("dense_fill_ratio", DENSE_FILL_RATIO)),
            int(dictionary.rule("dense_min_cells", DENSE_MIN_CELLS)))


def _guess(sheet: Any, dictionary: Any = None, raw: bool | None = None) -> tuple[str, str, dict[str, Any]]:
    dens = density(sheet)
    if raw is None:
        raw = is_raw_name(sheet.name) or any(t.from_query for t in sheet.tables)
    if raw:
        return "raw", "raw sheet (always dense)", dens
    ratio_min, cells_min = _thresholds(dictionary)
    dense = dens["filled"] >= cells_min and dens["ratio"] >= ratio_min
    what = (f"{dens['filled']} filled cells in a block of {dens['area']} ({dens['ratio']:.0%})"
            if dens["area"] else "no cells")
    if _HELPER_RE.search(sheet.name):
        return "helpers", f"helper sheet by name; {what}", dens
    if dense:
        return "calc", f"dense: {what}, at least {ratio_min:.0%} and {cells_min} cells", dens
    for sheet_type, pattern in _NAME_TYPES:
        if pattern.search(sheet.name):
            return sheet_type, f"not dense: {what}; '{sheet_type}' by its name", dens
    return "output", f"not dense: {what}", dens


def guess_sheet_type(sheet: Any, dictionary: Any = None) -> str:
    """One of SHEET_TYPES for a workbook.SheetInfo, chosen for its font size: raw sheets are "raw" (dense); a
    sheet whose used block is at least dense_fill_ratio filled and holds at least dense_min_cells cells is "calc"
    (dense: size 8); a sheet with white space gets the type its name suggests among cover, inputs, checks,
    notes and output, else "output" (spaced: size 9). Helper sheets (h_..., helper) are "helpers"."""
    return _guess(sheet, dictionary)[0]


def guess_raw_sheets(wb: Any) -> set[str]:
    """Raw sheets of a workbook without a manifest (design 14: names matching RAW_SHEET_RE, tables loaded by a
    query)."""
    return {s.name for s in wb.sheets if s.kind == "worksheet"
            and (is_raw_name(s.name) or any(t.from_query for t in s.tables))}


def _manifest_raw(manifest: dict[str, Any] | None) -> set[str]:
    if not manifest:
        return set()
    return {s["name"] for s in manifest.get("sheets", []) if s.get("type") == "raw"}


def raw_sheet_names(wb: Any, manifest: dict[str, Any] | None = None, *,
                    raw_sheets: set[str] | None = None) -> set[str]:
    """Raw sheets: the manifest's when it lists sheets, plus `raw_sheets`; else the guessed ones plus
    `raw_sheets`."""
    extra = set(raw_sheets or ())
    if manifest and manifest.get("sheets"):
        return _manifest_raw(manifest) | extra
    return guess_raw_sheets(wb) | extra


def plan_workbook(wb: Any, dictionary: Any, manifest: dict[str, Any] | None = None, *,
                  raw_sheets: set[str] | None = None) -> dict[str, SheetPlan]:
    """Sheet type and font size of every worksheet. The manifest's sheet entry (type, font_size) wins; other
    sheets are guessed (guess_sheet_type). theme and audit both use this, so they agree on sizes."""
    if manifest is None:
        manifest = getattr(wb, "manifest", None)
    raw = raw_sheet_names(wb, manifest, raw_sheets=raw_sheets)
    entries = {s.get("name"): s for s in (manifest or {}).get("sheets", [])}
    out: dict[str, SheetPlan] = {}
    for s in wb.sheets:
        if s.kind != "worksheet":
            continue
        entry = entries.get(s.name)
        if entry is not None and entry.get("type") in SHEET_TYPES:
            st = entry["type"]
            size = int(entry["font_size"]) if entry.get("font_size") in (8, 9) else dictionary.sheet_size(st)
            out[s.name] = SheetPlan(s.name, st, size, st == "raw", "manifest",
                                    f"manifest: {st} sheet, size {size}", density(s))
            continue
        st, reason, dens = _guess(s, dictionary, raw=s.name in raw)
        out[s.name] = SheetPlan(s.name, st, dictionary.sheet_size(st), st == "raw", "inferred", reason, dens)
    return out


# ----------------------------------------------------------------------------- checks

def _tokens(formula_text: str) -> list[Any]:
    from . import formula
    try:
        return formula.tokenize(formula_text)
    except Exception:  # noqa: BLE001 - an unreadable formula is not a test
        return []


def _has_comparison(formula_text: str) -> bool:
    return any(t.type == "OPERATOR-INFIX" and t.text in ("=", "<>", "<", ">", "<=", ">=") for t in _tokens(formula_text))


def _result_words(formula_text: str) -> bool:
    for t in _tokens(formula_text):
        if t.type == "OPERAND" and t.subtype == "TEXT":
            word = t.text.strip('"').strip()
            if word.upper() in _RESULT_WORDS or word in _RESULT_WORDS or _CHECK_RE.search(word):
                return True
    return False


def _check_label(text: str) -> bool:
    return bool(text) and len(text) <= CHECK_LABEL_MAX_CHARS and bool(_CHECK_RE.search(text)) \
        and not _CHECK_EXCLUDE_RE.search(text)


def _sheet_checks(sheet: Any, check_sheet: bool) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
    """(check cells, master-check cells) of one sheet, as (row, col)."""
    checks: set[tuple[int, int]] = set()
    masters: set[tuple[int, int]] = set()
    by_row: dict[int, list[Any]] = {}
    for (r, c), cell in sheet.cells.items():
        by_row.setdefault(r, []).append(cell)
    for r, cells in by_row.items():
        cells.sort(key=lambda x: x.col)
        for i, cell in enumerate(cells):
            if _is_formula(cell) or not isinstance(cell.value, str) or not _check_label(cell.value):
                continue
            right = [x for x in cells[i + 1:] if x.formula is not None]
            if not right:
                continue
            if _MASTER_RE.search(cell.value):
                masters.add((r, right[0].col))
                right = right[1:]
            checks.update((r, x.col) for x in right)
            break
    for (r, c), cell in sheet.cells.items():
        if cell.formula is None or (r, c) in checks or (r, c) in masters:
            continue
        if _result_words(cell.formula) or (check_sheet and _has_comparison(cell.formula)):
            checks.add((r, c))
    return checks, masters


def guess_check_cells(wb: Any) -> list[tuple[str, str]]:
    """Check cells of a workbook without a manifest: formulas right of a label with CHECK_WORDS (master checks
    included), formulas with an OK/ERROR-style text result, and comparisons on sheets named check or checks."""
    from . import common
    out: list[tuple[str, str]] = []
    for s in wb.sheets:
        if s.kind != "worksheet":
            continue
        checks, masters = _sheet_checks(s, bool(_NAME_TYPES[0][1].search(s.name)))
        for r, c in sorted(checks | masters):
            out.append((s.name, f"{common.col_letter(c)}{r}"))
    return out


# ----------------------------------------------------------------------------- cells

def _raw_sheet_types(sheet: Any) -> dict[tuple[int, int], str]:
    out: dict[tuple[int, int], str] = {}
    if not sheet.cells:
        return out
    first = min(r for r, _c in sheet.cells)
    for (r, c) in sheet.cells:
        out[(r, c)] = "col_header" if r == first else "raw"
    return out


def _exports(wb: Any) -> set[tuple[str, int, int]]:
    """Cells read by a formula on another sheet (single cells and ranges of at most 200 cells)."""
    from . import common, formula
    out: set[tuple[str, int, int]] = set()
    names = {s.name.casefold(): s.name for s in wb.sheets}
    for s in wb.sheets:
        for cell in s.cells.values():
            if cell.formula is None or "!" not in cell.formula:
                continue
            try:
                refs = formula.references(cell.formula)
            except Exception:  # noqa: BLE001
                continue
            for ref in refs:
                if ref.workbook or not ref.sheet or ref.kind not in ("cell", "range"):
                    continue
                target = names.get(ref.sheet.casefold())
                if target is None or target == s.name:
                    continue
                try:
                    c1, r1, c2, r2 = common.range_bounds(ref.a1.replace("$", ""))
                except ValueError:
                    continue
                if (c2 - c1 + 1) * (r2 - r1 + 1) > 200:
                    continue
                for rr in range(r1, r2 + 1):
                    for cc in range(c1, c2 + 1):
                        out.add((target, rr, cc))
    return out


def classify_sheet(sheet: Any, *, raw: bool = False, exports: set[tuple[int, int]] | None = None,
                   check_sheet: bool | None = None) -> dict[tuple[int, int], str]:
    """(row, col) -> cell type for every non-empty cell of one workbook.SheetInfo (rules in the module text)."""
    from . import formula
    if raw:
        return _raw_sheet_types(sheet)
    cells = sheet.cells
    kind = {pos: _layout_kind(c) for pos, c in cells.items()}
    types: dict[tuple[int, int], str] = {}

    # 1. column headers above runs of numbers or formulas
    cand: dict[tuple[int, int], int] = {}
    for (r, c), k in kind.items():
        if not _data_like(k):
            continue
        above = kind.get((r - 1, c))
        if above is not None and _data_like(above):
            continue                               # not the first cell of a run
        n, rr = 1, r + 1
        while _data_like(kind.get((rr, c), "")):
            n, rr = n + 1, rr + 1
        for up in range(r - 1, max(0, r - 1 - HEADER_LOOKUP_ROWS), -1):
            k_up = kind.get((up, c))
            if k_up is None or k_up == "blank":
                continue
            if _header_like(k_up):
                cand[(up, c)] = max(cand.get((up, c), 0), n)
            break
    per_row: dict[int, int] = {}
    for (r, _c) in cand:
        per_row[r] = per_row.get(r, 0) + 1
    headers = {pos for pos, n in cand.items() if n >= 2 or per_row[pos[0]] >= 2}
    for r in sorted({p[0] for p in headers}):
        row_cols = sorted(c for (rr, c) in headers if rr == r)
        lo, hi = row_cols[0], row_cols[-1]
        for step, start in ((-1, lo), (1, hi)):
            c, gap = start + step, 0
            while c >= 1 and gap <= HEADER_GAP_COLS:
                k = kind.get((r, c))
                if k is None or k == "blank":
                    gap += 1
                elif _header_like(k):
                    headers.add((r, c))
                    gap = 0
                else:
                    break
                c += step
    for pos in headers:
        types[pos] = "col_header"
    unit_cols = {c for (r, c) in headers if _text(cells[(r, c)]).strip().casefold() in _UNIT_WORDS}
    total_cols = {c for (r, c) in headers if _TOTAL_RE.search(_text(cells[(r, c)]))}

    # 2. checks (before the text rules: a check formula may show text such as OK)
    if check_sheet is None:
        check_sheet = bool(_NAME_TYPES[0][1].search(sheet.name))
    checks, masters = _sheet_checks(sheet, check_sheet)
    checks -= headers
    masters -= headers
    for pos in masters:
        types[pos] = "master_check"
    for pos in checks - masters:
        types[pos] = "check"

    # 3. rows: labels left of the values, notes right of them. A formula showing text left of the values is a
    # row label (a link to a list); elsewhere a formula stays a formula (step 5).
    by_row: dict[int, list[int]] = {}
    for (r, c) in cells:
        by_row.setdefault(r, []).append(c)
    total_rows: set[int] = set()
    text_rows: dict[int, list[int]] = {}
    checked = checks | masters
    for r, cols in by_row.items():
        cols.sort()
        values = [c for c in cols if (r, c) not in headers and ((r, c) in checked or _row_value(kind[(r, c)]))]
        texts = [c for c in cols if (r, c) not in types and kind[(r, c)] in ("text", "blank")]
        if not values:
            text_rows[r] = [c for c in texts if not _is_formula(cells[(r, c)])]
            continue
        first, last = values[0], values[-1]
        for c in texts:
            if c < first:
                types[(r, c)] = "unit" if c in unit_cols else "row_header"
                if _TOTAL_RE.search(_text(cells[(r, c)])):
                    total_rows.add(r)
            elif _is_formula(cells[(r, c)]):
                continue
            elif c > last:
                types[(r, c)] = "note"
            else:
                types[(r, c)] = "label"

    # 4. typed text in rows without values
    first_text = None
    for (r, c) in sorted(cells):
        if r > 3:
            break
        if (r, c) not in types and kind[(r, c)] == "text" and not _is_formula(cells[(r, c)]):
            first_text = (r, c)
            break
    filled_rows = sorted(by_row)
    for r, texts in text_rows.items():
        below = [x for x in filled_rows if r < x <= r + 2]
        lone = len(texts) == 1
        for c in texts:
            if (r, c) in types:
                continue
            text = _text(cells[(r, c)])
            if (r, c) == first_text:
                types[(r, c)] = "title"
            elif len(text) >= NOTE_MIN_CHARS or _NOTE_RE.search(text):
                types[(r, c)] = "note"
            elif lone and below and any(r2 not in text_rows or any((r2, cc) in headers for cc in by_row[r2])
                                         for r2 in below):
                types[(r, c)] = "heading"
            else:
                types[(r, c)] = "label"

    # 5. formulas and constants
    for (r, c), cell in cells.items():
        if (r, c) in types:
            continue
        if _is_formula(cell):
            if cell.formula is None:
                types[(r, c)] = "calc"             # a cell of an array result or data table
                continue
            try:
                a = formula.analyze(cell.formula)
            except Exception:  # noqa: BLE001 - an unreadable formula is still a calculation
                a = None
            if a is not None and a.is_pure_link and a.refs:
                ref = a.refs[0]
                same = ref.workbook is None and (ref.sheet is None or ref.sheet.casefold() == sheet.name.casefold())
                types[(r, c)] = "link" if same else "import"
            elif r in total_rows or c in total_cols:
                types[(r, c)] = "total"
            elif exports and (r, c) in exports:
                types[(r, c)] = "export"
            else:
                types[(r, c)] = "calc"
        elif kind[(r, c)] in ("num", "bool", "date", "year", "err"):
            types[(r, c)] = "input"
        else:
            types[(r, c)] = "label"
    return types


def classify_workbook(wb: Any, manifest: dict[str, Any] | None = None, *,
                      raw_sheets: set[str] | None = None) -> dict[tuple[str, str], str]:
    """(sheet, coord) -> cell type for every non-empty cell of a workbook.WorkbookInfo. The manifest's
    cell_types win for the ranges it lists (later ranges win, as manifest.cell_type_at)."""
    from . import common
    from . import manifest as manifest_mod
    if manifest is None:
        manifest = getattr(wb, "manifest", None)
    raw = raw_sheet_names(wb, manifest, raw_sheets=raw_sheets)
    exports_all = _exports(wb)
    out: dict[tuple[str, str], str] = {}
    for s in wb.sheets:
        if s.kind != "worksheet" or not s.cells:
            continue
        exports = {(r, c) for (name, r, c) in exports_all if name == s.name}
        types = classify_sheet(s, raw=s.name in raw, exports=exports)
        ranges = manifest_mod.cell_type_ranges(manifest, s.name) if manifest else []
        for (r, c), t in types.items():
            if ranges:
                t = manifest_mod.cell_type_at(ranges, r, c) or t
            out[(s.name, f"{common.col_letter(c)}{r}")] = t
    return out

"""`compat`: find functions and features that exist in only one of Excel and Google Sheets; replace the
mechanical cases inside the depth limit, report the rest (design sections 8.5, 11, 15; F17, F08a).

Owner: M7. Writes per design section 11 (openpyxl, or Excel when the package has a fragile part, or refuse).

What compat replaces (design 15; each replacement never raises the nesting depth, so it stays inside the limit
whenever the formula already was):
- X-STRUCTREF: a structured reference to the same table row (`Table1[@Amount]`, `[@Amount]`,
  `Table1[[#This Row],[Amount]]`) becomes the same-row A1 reference (`D7`, or `Sheet!D7` from another sheet).
  Google Sheets tables do not support "#This Row" (platforms A2.4, confirmed).
- X-PREFIX: a function stored without the `_xlfn.` prefix Excel needs in the file (Excel then shows #NAME?;
  verified by CORE-F in Excel 16.0 for XLOOKUP) gets the prefix (lib.formula.to_file_formula).
- X-UDF: `_xludf.<NAME>` where NAME is a catalogue function that exists in the target products (the program
  that saved the file did not know NAME, so Excel treats it as an unknown user-defined function) becomes NAME.
- X-CONCAT: CONCATENATE(a,b,...) becomes a&b&... (design 15); not when an argument is a range.
- X-CONCAT for Excel's CONCAT (v4, A-aera-20): Google Sheets' CONCAT takes two values, Excel's any number of
  arguments and ranges. Exactly two single-value arguments is the form both products share. A CONCAT with more
  than two arguments is reported (warning) and becomes a&b&... when every argument is a single value; a CONCAT
  with one single-value argument a is reported and becomes a&"" (v5, A-aera-13); a CONCAT with a range argument
  is reported and left. Not with --product excel.

What compat reports and does not replace: one-product functions (X-FUNC: Google-only functions such as QUERY,
Excel functions Google lists as unsupported; lib/functions.py gives availability and sources), functions whose
availability is not confirmed, functions not in the catalogue, whole-table structured references (X-TABLEREF),
and workbook features of one product (X-FEATURE: macros, Power Query, data connections, x14 extensions,
slicers, timelines, the Data Model, form controls, the 1904 date system).
The same function catalogue is applied to the formulas of data validations and conditional-format rules (v5,
A-aera-12): X-FUNC with the sheet, the range and the kind of rule; these formulas are reported, not rewritten.
X-FILTER (v6, A-aera-9): an Excel FILTER with a third argument (in Excel the value returned when no row matches,
in Google Sheets a further condition; lib/functions.py D_FILTER) is reported at its cell (warning) and not
rewritten. Not with --product excel.

The writer helpers in this module (`plan_output`, `write_edits`) are also used by lib/split.py.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import html
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from . import common
from . import formula as fx
from . import functions as fn

PRODUCTS = ("both", "excel", "sheets")
REPLACED_SHOWN = 500          # replaced cells listed in the report data (all are counted)

# Comparison operators bind looser than "&"; an argument holding one at its top level needs parentheses.
_COMPARISON = re.compile(r"<>|<=|>=|[<>=]")


# ----------------------------------------------------------------------------- data

@dataclass
class CellEdit:
    """One cell to write in an existing workbook (formula or constant)."""
    sheet: str
    cell: str
    formula: str | None = None          # with "=", display or file form
    value: Any = None                   # constant (used when formula is None)
    array: bool = False                 # a single-cell array formula (written as one in Excel and openpyxl)


@dataclass
class CellLook:
    """The style dictionary's look for cells a command creates (v3, A-aera-18: the cells split writes on its
    helper sheet). `areas`: (sheet, A1 range, cell type); every cell of a range gets the type's font, size on a
    sheet of `size` (8 or 9), colour, bold, italic, fill, border and alignment (lib.styles); an "item" number
    format without a format key keeps the cell's own format."""
    dictionary: Any
    size: int
    areas: list[tuple[str, str, str]] = field(default_factory=list)


@dataclass
class Replacement:
    sheet: str
    cell: str
    rule: str
    before: str
    after: str                          # file form (what is written)
    notes: list[str] = field(default_factory=list)
    array: bool = False                 # the cell holds a single-cell array formula


@dataclass
class CompatPlan:
    replacements: list[Replacement] = field(default_factory=list)
    findings: list[common.Finding] = field(default_factory=list)


# ----------------------------------------------------------------------------- structured references

_THIS_ROW = re.compile(r"#\s*this\s+row", re.I)


def _unescape_col(text: str) -> str:
    """Column name inside a structured reference: the escape character ' before [ ] # ' is removed."""
    out, i = [], 0
    while i < len(text):
        if text[i] == "'" and i + 1 < len(text):
            out.append(text[i + 1])
            i += 2
            continue
        out.append(text[i])
        i += 1
    return out and "".join(out).strip() or ""


def _bracket_items(spec: str) -> list[str]:
    """'[[#This Row],[Amount]]' -> ['#This Row', 'Amount']; '[@Amount]' -> ['@Amount']; '[Amount]' -> ['Amount'];
    '[[#This Row],[A]:[B]]' -> ['#This Row', 'A:B'] (escaped brackets are kept inside names)."""
    s = spec.strip()
    if not (s.startswith("[") and s.endswith("]")):
        return []
    inner = s[1:-1]
    if "[" not in inner.replace("'[", "").replace("']", ""):
        return [inner]
    items, cur, depth, i = [], [], 0, 0
    while i < len(inner):
        ch = inner[i]
        if ch == "'" and i + 1 < len(inner):
            cur.append(inner[i:i + 2])
            i += 2
            continue
        if ch == "[":
            depth += 1
            if depth == 1:
                i += 1
                continue
        elif ch == "]":
            depth -= 1
            if depth == 0:
                i += 1
                continue
        if depth == 0 and ch == ",":
            items.append("".join(cur))
            cur = []
        else:
            cur.append(ch)                  # ':' between [A]:[B] and the '@' of [@[Col]] are kept
        i += 1
    if cur:
        items.append("".join(cur))
    return [x.strip() for x in items if x.strip()]


@dataclass
class StructRef:
    table: str | None                   # None: unqualified, the table that holds the host cell
    this_row: bool
    columns: list[str]                  # one or two names (a column span), [] for the whole table
    special: list[str]                  # '#All', '#Data', '#Headers', '#Totals'


def parse_structured(text: str) -> StructRef | None:
    """Parse the structured reference text of a formula.Ref of kind 'structured' (without a sheet prefix)."""
    t = text.strip()
    if t.startswith("@"):
        t = t[1:]
    k = t.find("[")
    if k < 0:
        return None
    table = t[:k].strip() or None
    items = _bracket_items(t[k:])
    if not items:
        return None
    this_row, cols, special = False, [], []
    for item in items:
        if item.startswith("@"):
            this_row = True
            item = item[1:].strip()
            if item.startswith("[") and item.endswith("]"):
                sub = _bracket_items(item)
                item = sub[0] if sub else item[1:-1]
            if not item:
                continue
        if _THIS_ROW.fullmatch(item.strip()):
            this_row = True
            continue
        if item.startswith("#"):
            special.append(item)
            continue
        names = [n for n in item.split(":")] if ":" in item else [item]
        cols.extend(_unescape_col(n.strip().strip("[]")) for n in names)
    return StructRef(table=table, this_row=this_row, columns=[c for c in cols if c], special=special)


def _table_for(tables: Sequence[Any], name: str | None, sheet: str, row: int, col: int) -> Any | None:
    if name:
        for t in tables:
            if t.name.casefold() == name.casefold():
                return t
        return None
    for t in tables:                    # unqualified: the table holding the host cell
        if t.sheet != sheet:
            continue
        c1, r1, c2, r2 = common.range_bounds(t.ref)
        if c1 <= col <= c2 and r1 <= row <= r2:
            return t
    return None


def _this_row_a1(sref: StructRef, table: Any, host_sheet: str, host_row: int) -> tuple[str | None, str]:
    """The same-row A1 text for a #This Row reference, or (None, reason)."""
    c1, r1, c2, r2 = common.range_bounds(table.ref)
    if not (r1 + 1 <= host_row <= r2):
        return None, (f"the cell is outside the data rows of table {table.name} ({table.sheet} rows {r1 + 1} to "
                      f"{r2}), so the reference has no same-row cell")
    if not sref.columns:
        cols = [c1, c2]
    else:
        cols = []
        lower = [c.casefold() for c in table.columns]
        for name in sref.columns:
            if name.casefold() not in lower:
                return None, f"table {table.name} has no column '{name}'"
            cols.append(c1 + lower.index(name.casefold()))
    first, last = min(cols), max(cols)
    text = f"{common.col_letter(first)}{host_row}"
    if last != first:
        text += f":{common.col_letter(last)}{host_row}"
    if table.sheet != host_sheet:
        text = common.quote_sheet(table.sheet) + "!" + text
    return text, ""


# ----------------------------------------------------------------------------- CONCATENATE

def _top_level_comparison(text: str) -> bool:
    depth, quote = 0, False
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == '"':
                quote = False
        elif ch == '"':
            quote = True
        elif ch == "'":                           # quoted sheet name
            j = text.find("'", i + 1)
            while j >= 0 and j + 1 < len(text) and text[j + 1] == "'":
                j = text.find("'", j + 2)
            i = j if j >= 0 else len(text)
        elif ch in "({[":
            depth += 1
        elif ch in ")}]":
            depth -= 1
        elif depth == 0 and ch in "<>=":
            return True
        i += 1
    return False


def _concat_once(d: str) -> tuple[str, str | None] | None:
    """Replace the innermost mechanical CONCATENATE call of display-form formula d. Returns (new formula,
    None), or (d, reason) when a CONCATENATE exists but cannot be replaced, or None when there is none."""
    a = fx.analyze(d)
    if not a.ok:
        return None
    calls = list(a.calls)
    idx = [i for i, c in enumerate(calls) if c.name == "CONCATENATE"]
    if not idx:
        return None
    reason = None
    for i in sorted(idx, key=lambda k: -calls[k].level):
        c = calls[i]
        if any(calls[j].name == "CONCATENATE" and calls[j].start > c.start and calls[j].end <= c.end for j in idx):
            continue
        if not c.args:
            reason = "CONCATENATE has no arguments"
            continue
        args = [d[s:e].strip() for s, e in c.args]
        if any(not x for x in args):
            reason = "CONCATENATE has an empty argument"
            continue
        ranged = _range_arguments(a, calls, c)
        if ranged:
            reason = f"an argument of CONCATENATE is a range ({ranged[0].text}); & joins single values only"
            continue
        return _joined(d, calls, c, args), None
    return d, reason or "no CONCATENATE call could be replaced"


def _range_arguments(a: Any, calls: Sequence[Any], c: Any) -> list[Any]:
    """The range, column and row references inside call c of analysis a that are not inside a deeper call."""
    return [r for r in a.refs if c.start <= r.start < c.end and r.kind in ("range", "column", "row")
            and not any(calls[j].start <= r.start < calls[j].end and calls[j].level > c.level
                        for j in range(len(calls)))]


def _joined(d: str, calls: Sequence[Any], c: Any, args: Sequence[str]) -> str:
    """Formula d with call c replaced by its arguments joined with & (in brackets where the call is neither the
    whole formula nor a whole argument of its parent)."""
    parts = [f"({x})" if _top_level_comparison(x) else x for x in args]
    joined = "&".join(parts) if len(parts) > 1 else parts[0] + '&""'
    whole = d[1:].strip() == d[c.start:c.end].strip()
    as_arg = c.parent is not None and any(d[s:e].strip() == d[c.start:c.end].strip()
                                          for s, e in calls[c.parent].args)
    new_text = joined if (whole or as_arg) else f"({joined})"
    return d[:c.start] + new_text + d[c.end:]


# Excel's CONCAT (v4, A-aera-20; plan 5.1 X-CONCAT)
CONCAT_SHEETS = "Google Sheets' CONCAT takes two values"
CONCAT_REWRITTEN_FIX = "rewritten with & by rotm.py compat"


def _concat_function(d: str) -> tuple[str, list[str], list[tuple[str, str, str, str]]]:
    """Excel's CONCAT in display-form formula d. Google Sheets' CONCAT takes two values; Excel's takes any number
    of arguments and ranges. Exactly two single-value arguments is the form both products share and is left as it
    is. Innermost first: a CONCAT with one single-value argument a becomes a&"" (text, the value unchanged; v5,
    A-aera-13); a CONCAT with more than two arguments, all of them single values, is rewritten with &; a CONCAT
    with a range argument, or with more than two arguments one of which is empty, is reported and left; a CONCAT
    with two arguments one of which is empty, or with none, is left as it is (as in v4).
    Returns (new formula, one text per rewritten call such as 'CONCAT with 3 arguments', problems)."""
    done: list[str] = []
    problems: dict[str, tuple[str, str, str, str]] = {}
    for _ in range(50):
        a = fx.analyze(d)
        if not a.ok:
            break
        calls = list(a.calls)
        target: tuple[Any, list[str]] | None = None
        for i in sorted((k for k, c in enumerate(calls) if c.name == "CONCAT"), key=lambda k: -calls[k].level):
            c = calls[i]
            text = d[c.start:c.end]
            args = [d[s:e].strip() for s, e in c.args]
            ranged = _range_arguments(a, calls, c)
            if ranged:
                rng = ranged[0].text
                problems.setdefault(text, ("X-CONCAT", "warning",
                                           f"CONCAT with a range argument {rng}: {CONCAT_SHEETS}",
                                           f'write TEXTJOIN("",FALSE,{rng}) or join the cells with &'))
                continue
            if len(args) == 2 or not args or (len(args) == 1 and not args[0]):
                continue                           # two values: the shared form; no argument: left (v4)
            if not all(args):
                problems.setdefault(text, ("X-CONCAT", "warning", f"CONCAT with {len(args)} arguments: {CONCAT_SHEETS}",
                                           "an argument is empty, so compat did not rewrite it: join the values "
                                           "with & by hand"))
                continue
            target = (c, args)
            break
        if target is None:
            break
        c, args = target
        done.append(f"CONCAT with {len(args)} argument{'' if len(args) == 1 else 's'}")
        d = _joined(d, calls, c, args)
    return d, done, list(problems.values())


# X-TABLEREF (v6, A-aera-16): the same words as references/google-sheets.md, "Excel tables on import"
TABLEREF_MESSAGE = ("a whole-table structured reference (Table[Column]): Google Sheets tables accept Table[Column] "
                    "(platforms A2.3, not confirmed); the import of Excel tables as Sheets tables is the confirmed "
                    "announcement of 11 August 2026 (researcher 2 #17, confirmed); not confirmed, item by item: "
                    "whether the table styles come through, whether structured references in formulas keep "
                    "working after the import, and whether a connected pivot table stays connected to its table "
                    "(platforms E5)")

# FILTER's third argument (v6, A-aera-9; lib/functions.py D_FILTER)
FILTER_SHEETS = ("in Excel the third argument is the value returned when no row matches; in Google Sheets it is a "
                 "further condition")
FILTER_FIX = ("compat does not rewrite it: for a workbook that must also work in Google Sheets, remove the third "
              "argument and handle the case of no matching row in a separate cell, or test the formula in Google "
              "Sheets")


def _filter_problems(d: str) -> list[tuple[str, str, str, str]]:
    """X-FILTER for each FILTER call of display-form formula d with three or more arguments (v6, A-aera-9): Excel's
    FILTER(array, include, [if_empty]) returns its third argument when no row matches; Google Sheets'
    FILTER(range, condition1, [condition2, ...]) reads it as a further condition. Reported, never rewritten."""
    a = fx.analyze(d)
    if not a.ok:
        return []
    problems: dict[str, tuple[str, str, str, str]] = {}
    for c in a.calls:
        if c.name != "FILTER" or len(c.args) < 3:
            continue
        third = d[c.args[2][0]:c.args[2][1]].strip()
        shown = third if len(third) <= 40 else third[:37] + "..."
        problems.setdefault(d[c.start:c.end], (
            "X-FILTER", "warning",
            f"FILTER with {len(c.args)} arguments (third argument: {shown or 'empty'}): {FILTER_SHEETS}",
            FILTER_FIX))
    return list(problems.values())


# ----------------------------------------------------------------------------- one formula

def _availability(name: str, product: str) -> tuple[str, str, str] | None:
    """(severity, message, fix) when the function is a compatibility problem for the target products."""
    info = fn.info(name)
    if info is None:
        return ("warning", f"{name} is not in the function catalogue, so its availability in "
                f"{_product_words(product)} is not known (references/functions.md)",
                f"check that {name} exists in {_product_words(product)}, or use a catalogue function")
    src = info.availability or info.reason
    repl = f"use {info.replacement}" if info.replacement else "replace it (references/functions.md)"
    if product in ("both", "excel") and info.excel == "no":
        return ("error", f"{name} exists in Google Sheets only, not in Excel ({src})", repl)
    if product in ("both", "sheets") and info.sheets == "no":
        return ("error", f"{name} exists in Excel only; Google lists it as not supported in Sheets ({src})", repl)
    if product in ("both", "sheets") and info.sheets == "not confirmed":
        return ("warning", f"{name}: availability in Google Sheets is not confirmed ({src})",
                f"{repl}, or test the formula in Google Sheets")
    if product in ("both", "excel") and info.excel == "365":
        # v2 (A-aera-19): the advice names the function's own versions (functions.py excel_versions)
        versions = fn.versions_text(info.excel_versions, "or")
        return ("info", f"{name} exists only in {versions}; other Excel versions show #NAME? ({src})",
                f"nothing to change when every reader uses {versions}")
    return None


def _product_words(product: str) -> str:
    return {"both": "both Excel and Google Sheets", "excel": "Excel", "sheets": "Google Sheets"}[product]


_NOT_WRITABLE = ("the cell holds an array formula, which compat does not rewrite",
                 "change it by hand in Excel")


def plan_formula(f: str, *, sheet: str, row: int, col: int, tables: Sequence[Any], product: str,
                 replace: bool = True, notes: list[str] | None = None,
                 ) -> tuple[str | None, list[str], list[tuple[str, str, str, str]]]:
    """Plan the compat change of one formula (file form as stored).

    Returns (new file-form formula or None when unchanged, rules replaced, problems) where each problem is
    (rule, severity, message, fix) for what is reported and not replaced. With replace=False (array formulas)
    every replaceable item is reported instead. `notes`, when given, receives one text per rewritten Excel
    CONCAT call (for example 'CONCAT with 3 arguments'; v4, A-aera-20)."""
    problems: list[tuple[str, str, str, str]] = []
    replaced: list[str] = []
    d = fx.display_form(f)
    a = fx.analyze(d)
    old_depth = a.depth
    current = d

    # X-UDF: _xludf.<known function>
    udf_names = [n for n in a.functions if n.startswith("_XLUDF.")]
    for n in udf_names:
        base = n[len("_XLUDF."):]
        info = fn.info(base)
        if info is None:
            ok = False
        elif product == "both":
            ok = info.excel != "no" and info.sheets == "yes"
        elif product == "excel":
            ok = info.excel != "no"
        else:
            ok = info.sheets == "yes"
        if ok and replace:
            pattern = re.compile(r"_xludf\." + re.escape(base) + r"(?=\s*\()", re.I)
            new = "".join(seg if is_text else pattern.sub(base, seg) for is_text, seg in fx._segments(current))
            if new != current:
                current = new
                replaced.append("X-UDF")
        elif ok:
            problems.append(("X-UDF", "error", f"{base} is stored as a user-defined function (_xludf.{base}), so "
                             f"Excel shows #NAME?; {_NOT_WRITABLE[0]}", _NOT_WRITABLE[1]))
        else:
            problems.append(("X-UDF", "warning", f"{base} is stored as a user-defined function (_xludf.{base}) and "
                             f"is not a catalogue function available in {_product_words(product)}",
                             f"type the formula again in Excel, or replace {base} with a catalogue function"))

    # X-STRUCTREF / X-TABLEREF
    a = fx.analyze(current)
    edits: list[tuple[int, int, str]] = []
    this_row_msg = ("a #This Row structured reference ([@Column]), which Google Sheets does not support "
                    "(platforms A2.4, confirmed)")
    for r in a.refs:
        if r.kind != "structured":
            continue
        sref = parse_structured(r.a1)
        if sref is None:
            problems.append(("X-STRUCTREF", "warning", "a structured reference could not be read",
                             "write it as an A1 reference"))
            continue
        if not sref.this_row:
            problems.append(("X-TABLEREF", "info", TABLEREF_MESSAGE,
                             "nothing to change for Excel; for Google Sheets test the formula after conversion, or "
                             "write the A1 range of the column"))
            continue
        table = _table_for(tables, sref.table, sheet, row, col)
        if table is None:
            problems.append(("X-STRUCTREF", "warning", f"{this_row_msg}; its table was not found",
                             "write the same-row A1 reference by hand"))
            continue
        a1, why = _this_row_a1(sref, table, sheet, row)
        if a1 is None:
            problems.append(("X-STRUCTREF", "warning", f"{this_row_msg}; {why}", "write the A1 reference by hand"))
            continue
        if replace:
            edits.append((r.start, r.end, a1))
        else:
            problems.append(("X-STRUCTREF", "warning", f"{this_row_msg}; {_NOT_WRITABLE[0]}", _NOT_WRITABLE[1]))
    if edits:
        for s, e, t in sorted(edits, reverse=True):
            current = current[:s] + t + current[e:]
        replaced.append("X-STRUCTREF")

    # X-CONCAT (design 15)
    if replace and "CONCATENATE" in fx.analyze(current).functions:
        for _ in range(50):
            res = _concat_once(current)
            if res is None:
                break
            new, why = res
            if why is not None:
                problems.append(("X-CONCAT", "info", f"CONCATENATE was not replaced: {why}",
                                 "nothing to change: CONCATENATE exists in both products"))
                break
            current = new
            if "X-CONCAT" not in replaced:
                replaced.append("X-CONCAT")

    # X-CONCAT for Excel's CONCAT (v4, A-aera-20): Google Sheets' CONCAT takes two values
    if product != "excel" and "CONCAT" in fx.analyze(current).functions:
        joined, rewritten, concat_problems = _concat_function(current)
        problems.extend(concat_problems)
        if rewritten and replace:
            current = joined
            if notes is not None:
                notes.extend(rewritten)
            if "X-CONCAT" not in replaced:
                replaced.append("X-CONCAT")
        for what in rewritten if not replace else []:
            problems.append(("X-CONCAT", "warning", f"{what}: {CONCAT_SHEETS}; {_NOT_WRITABLE[0]}",
                             _NOT_WRITABLE[1]))

    # X-FILTER (v6, A-aera-9): FILTER's third argument has another meaning in Google Sheets; reported only
    if product != "excel" and "FILTER" in fx.analyze(current).functions:
        problems.extend(_filter_problems(current))

    # X-PREFIX: a function that needs _xlfn. in the file is stored without it (and is still in the formula)
    prefix_missing = _prefix_missing(f, keep=set(fx.analyze(current).functions))
    if prefix_missing and not replace:
        problems.append(("X-PREFIX", "error", "a function is stored without the _xlfn. prefix Excel needs in the "
                         f"file, so Excel shows #NAME?; {_NOT_WRITABLE[0]}", _NOT_WRITABLE[1]))
    new_file = None
    if replace and (replaced or prefix_missing):
        new_file = fx.to_file_formula(current)
        if prefix_missing:
            replaced.append("X-PREFIX")

    # one-product functions (on the final formula)
    final = fx.analyze(new_file if new_file is not None else f)
    for name in final.functions:
        if name.startswith("_XLUDF."):
            continue
        av = _availability(name, product)
        if av is not None:
            problems.append(("X-FUNC", *av))

    # never raise the depth; never leave a #This Row reference behind in a replaced cell
    if new_file is not None:
        after = fx.analyze(new_file)
        if after.depth > old_depth or not after.ok:
            problems.append(("X-SKIPPED", "warning", "a compat replacement would have changed the formula's "
                             "depth or made it unreadable, so the cell was left as it was",
                             "change the formula by hand"))
            return None, [], problems
    return new_file, replaced, problems


def _prefix_missing(f: str, keep: set[str] | None = None) -> bool:
    """True when a function that needs a file prefix is stored without it; with `keep`, only a function named in
    `keep` counts (a function a replacement removed needs no prefix)."""
    d = fx.display_form(f)
    a = fx.analyze(d)
    if not a.ok:
        return False
    upper = f.upper()
    for c in a.calls:
        prefix = fn.file_prefix(c.name)
        if prefix and (prefix.upper() + c.name) not in upper and (keep is None or c.name in keep):
            return True
    return False


# ----------------------------------------------------------------------------- workbook features

def feature_findings(info: Any, product: str) -> list[common.Finding]:
    """X-FEATURE findings: parts of the workbook that exist in Excel only (design 14, X-FEATURE)."""
    out: list[common.Finding] = []
    if product == "excel":
        return out
    pkg = info.package
    parts = pkg.parts

    def add(sev: str, what: str, message: str, fix: str, **data: Any) -> None:
        out.append(common.Finding("X-FEATURE", sev, message, fix, data={"feature": what, **data}))

    if pkg.vba:
        add("warning", "macros", "the workbook holds Excel macros (xl/vbaProject.bin); Google Sheets runs Apps Script, "
            "not Excel macros (not confirmed by this plugin's research)",
            "do the macro's work with formulas, or keep the macro for Excel users only")
    if pkg.has_power_query or pkg.queries:
        names = ", ".join(pkg.query_names[:5]) or f"{pkg.queries} queries"
        add("warning", "power_query", f"Power Query queries ({names}): Google Sheets has no Power Query; after "
            "conversion the loaded tables hold values that do not refresh (not confirmed, platforms E5)",
            "in Google Sheets land the same column contract with Connected Sheets (references/google-sheets.md)",
            queries=pkg.query_names)
    other_conn = max(0, pkg.connections - pkg.mashup_connections)
    if other_conn:
        add("warning", "connections", f"{other_conn} data connection(s) other than Power Query (xl/connections.xml) "
            "do not carry over to Google Sheets (not confirmed)",
            "land the data as a raw table (column contract) and reference the raw sheet")
    ext_kinds = sorted({k for kinds in pkg.extensions.values() for k in kinds})
    if "conditionalFormattings" in ext_kinds:
        add("warning", "x14_conditional_formats", "extended conditional formats (x14: data bars, icon sets or rules "
            "that read other sheets); how Google Sheets converts them is not confirmed (platforms E5)",
            "use plain conditional formats, or none (minimal colour)")
    if "sparklineGroups" in ext_kinds:
        add("warning", "sparklines", "Excel sparklines (x14 sparkline groups); Google Sheets draws sparklines with "
            "its SPARKLINE function instead, and conversion is not confirmed",
            "remove them from calculation sheets, or accept that they may not convert")
    if "slicerList" in ext_kinds or any(p.startswith(("xl/slicers/", "xl/slicerCaches/")) for p in parts):
        add("warning", "slicers", "slicers (xl/slicers): conversion to Google Sheets is not confirmed",
            "filter with input cells and formulas instead")
    if any(p.startswith("xl/timelines/") for p in parts):
        add("warning", "timelines", "timeline filters (xl/timelines): conversion to Google Sheets is not confirmed",
            "filter with a dated input cell instead")
    if any(p.startswith("xl/model/") for p in parts):
        add("warning", "data_model", "the Data Model (xl/model, Power Pivot) has no Google Sheets counterpart "
            "(not confirmed)", "land the needed table as a raw sheet and use SUMIFS over it")
    if pkg.pivots:
        add("info", "pivot_tables", f"{pkg.pivots} pivot table(s): Google Sheets has pivot tables; whether these "
            "convert is not confirmed", "check them after conversion, or use a SUMIFS grid")
    if any(p.startswith(("xl/ctrlProps/", "xl/activeX/")) for p in parts):
        add("warning", "controls", "form controls or ActiveX controls: Google Sheets does not convert them "
            "(not confirmed)", "use an input cell with data validation instead")
    if pkg.external_links:
        add("warning", "external_links", f"{pkg.external_links} link(s) to other workbooks (xl/externalLinks); "
            "Google Sheets cannot read another Excel file", "land the data as a raw table")
    if getattr(info, "date1904", False):
        add("warning", "date1904", "the workbook uses the 1904 date system; Google Sheets uses the 1900 date system "
            "(platforms A3.8 and A3.9, confirmed), so date serials differ by 1,462 days",
            "in Excel: File > Options > Advanced > clear 'Use 1904 date system', then check every date")
    if pkg.metadata:
        add("info", "dynamic_arrays", "dynamic-array formulas (xl/metadata.xml): Google Sheets expands arrays in "
            "its own way (platforms A3.4); spill behaviour after conversion is not confirmed",
            "build lists in helper cells (FAST 3.03-09)")
    return out


# ----------------------------------------------------------------------------- validation and rule formulas

@dataclass
class RuleFormula:
    """One formula of a data validation or a conditional-format rule (v5, A-aera-12)."""
    kind: str                           # 'data validation (custom formula)', 'conditional format (formula rule)'
    sqref: str                          # the range the rule applies to, as stored ('A1', 'C1:C3 E5')
    formula: str                        # file form with '=' (escaped characters of the XML resolved)


_DV_KINDS = {"custom": "custom formula", "list": "list source", "whole": "whole number limit",
             "decimal": "decimal limit", "date": "date limit", "time": "time limit",
             "textLength": "text length limit"}
_CF_KINDS = {"expression": "formula rule", "cellIs": "cell value rule"}
_ELEMENT_RE = re.compile(r"<(?:\w+:)?(conditionalFormatting|dataValidation)\b([^>]*)>")
_CFRULE_RE = re.compile(r"<(?:\w+:)?cfRule\b([^>]*)>")
_ATTR_RE = re.compile(r"([\w:]+)\s*=\s*\"([^\"]*)\"")
_SHEET_DATA_END = re.compile(rb"</(?:\w+:)?sheetData>")
_TAIL_KEEP = 1 << 24                    # bytes kept while </sheetData> has not been found


def _end_of(text: str, name: str, start: int) -> int:
    """The index of the end tag </name> (any prefix) at or after `start`; -1 when there is none."""
    m = re.compile(rf"</(?:\w+:)?{name}>").search(text, start)
    return m.start() if m else -1


def _children(body: str, name: str) -> list[str]:
    """The texts of the child elements <name>...</name> (any prefix) in `body`; for an x14 element the text of
    its <xm:f> child."""
    out = []
    for m in re.finditer(rf"<(?:\w+:)?{name}>(.*?)</(?:\w+:)?{name}>", body, re.S):
        inner = m.group(1)
        f = re.search(r"<(?:\w+:)?f>(.*?)</(?:\w+:)?f>", inner, re.S)
        out.append(html.unescape((f.group(1) if f else inner).strip()))
    return out


def _sqref(attrs: dict[str, str], body: str) -> str:
    if attrs.get("sqref"):
        return attrs["sqref"]
    m = re.search(r"<(?:\w+:)?sqref>(.*?)</(?:\w+:)?sqref>", body, re.S)
    return m.group(1).strip() if m else ""


def rule_formulas_in_xml(text: str) -> list[RuleFormula]:
    """The formulas of the data validations and conditional-format rules in a worksheet part's XML (the part after
    sheetData is enough), in document order: the main elements and the x14 forms Excel stores in extLst. A rule
    without a formula (a colour scale without formula values, a duplicate-values rule) gives none."""
    out: list[RuleFormula] = []
    pos = 0
    while True:
        m = _ELEMENT_RE.search(text, pos)
        if m is None:
            break
        name, raw_attrs = m.group(1), m.group(2)
        if raw_attrs.rstrip().endswith("/"):
            pos = m.end()
            continue
        end = _end_of(text, name, m.end())
        if end < 0:
            break
        body = text[m.end():end]
        pos = end
        attrs = {k.split(":")[-1]: html.unescape(v) for k, v in _ATTR_RE.findall(raw_attrs)}
        sqref = _sqref(attrs, body)
        if name == "dataValidation":
            dv_type = attrs.get("type", "")
            kind = f"data validation ({_DV_KINDS[dv_type]})" if dv_type in _DV_KINDS else "data validation"
            for formula_text in _children(body, "formula1") + _children(body, "formula2"):
                if formula_text:
                    out.append(RuleFormula(kind, sqref, "=" + formula_text))
            continue
        rpos = 0
        while True:
            r = _CFRULE_RE.search(body, rpos)
            if r is None:
                break
            r_attrs = {k.split(":")[-1]: v for k, v in _ATTR_RE.findall(r.group(1))}
            cf_type = r_attrs.get("type", "")
            kind = f"conditional format ({_CF_KINDS.get(cf_type, f'{cf_type} rule' if cf_type else 'rule')})"
            if r.group(1).rstrip().endswith("/"):
                rpos = r.end()
                continue
            r_end = _end_of(body, "cfRule", r.end())
            if r_end < 0:
                break
            r_body = body[r.end():r_end]
            rpos = r_end
            texts = _children(r_body, "formula")
            texts += [html.unescape(f.group(1).strip()) for f in re.finditer(
                r"<(?:\w+:)?f>(.*?)</(?:\w+:)?f>", r_body, re.S)]          # x14 rules and x14 formula values
            for v in re.finditer(r"<(?:\w+:)?cfvo\b([^>]*)>", r_body):          # a formula value of a scale or bar
                v_attrs = {k.split(":")[-1]: x for k, x in _ATTR_RE.findall(v.group(1))}
                if v_attrs.get("type") == "formula" and v_attrs.get("val"):
                    texts.append(html.unescape(v_attrs["val"]))
            for formula_text in texts:
                if formula_text:
                    out.append(RuleFormula(kind, sqref, "=" + formula_text))
    return out


def _part_after_sheet_data(z: zipfile.ZipFile, part: str) -> str:
    """The text of a worksheet part after </sheetData> (where validations, conditional formats and extLst are),
    read in chunks so that the cells of a large sheet are not kept."""
    kept = bytearray()
    found = False
    with z.open(part) as fh:
        while True:
            chunk = fh.read(1 << 22)
            if not chunk:
                break
            start = max(0, len(kept) - 16)
            kept += chunk
            if found:
                continue
            m = _SHEET_DATA_END.search(kept, start)
            if m:
                del kept[:m.start()]
                found = True
            elif len(kept) > _TAIL_KEEP:
                del kept[:-16]
    return kept.decode("utf-8", "replace")


def rule_formulas(path: str | Path) -> dict[str, list[RuleFormula]]:
    """Sheet name -> the formulas of its data validations and conditional-format rules (v5, A-aera-12)."""
    from .audit import _sheet_part_names
    out: dict[str, list[RuleFormula]] = {}
    try:
        z = zipfile.ZipFile(common.native_path(path))
    except (zipfile.BadZipFile, OSError):
        return out
    with z:
        for sheet, part in _sheet_part_names(z).items():
            try:
                found = rule_formulas_in_xml(_part_after_sheet_data(z, part))
            except (KeyError, OSError, zipfile.BadZipFile):
                continue
            if found:
                out[sheet] = found
    return out


def rule_findings(path: str | Path, product: str) -> list[common.Finding]:
    """X-FUNC findings for functions of the validation and conditional-format formulas that one of the target
    products lacks (or whose availability the catalogue does not confirm), with the sheet, the range and the kind
    of rule. These formulas are reported, not rewritten (v5, A-aera-12)."""
    out: list[common.Finding] = []
    for sheet, items in rule_formulas(path).items():
        for item in items:
            try:
                a = fx.analyze(fx.display_form(item.formula))
            except Exception:  # noqa: BLE001 - a formula the tokenizer cannot read is not reported here
                continue
            first = re.split(r"[\s:]", item.sqref.replace("$", ""), maxsplit=1)[0] or None
            where = "the Data Validation dialog" if item.kind.startswith("data") else \
                "Home > Conditional Formatting > Manage Rules"
            for name in a.functions:
                if name.startswith("_XLUDF."):
                    continue
                av = _availability(name, product)
                if av is None:
                    continue
                sev, message, fix = av
                out.append(common.Finding(
                    "X-FUNC", sev, f"{item.kind} on {sheet}!{item.sqref}: {message}; compat reports this formula and "
                    "does not rewrite it", f"{fix} in the rule ({where} in Excel)", sheet=sheet, cell=first,
                    data={"kind": item.kind, "range": item.sqref, "formula": item.formula, "function": name}))
    return out


# ----------------------------------------------------------------------------- dependents

def dependents(info: Any, seeds: set[tuple[str, str]], *, max_cells: int = 200_000) -> set[tuple[str, str]]:
    """The seed cells and every formula cell that reads them, directly or through other cells."""
    from . import trace
    sizes = {s.name: (s.max_col, s.max_row) for s in info.sheets if s.kind == "worksheet"}
    lower = {n.casefold(): n for n in sizes}
    edges: list[tuple[tuple[str, str], list[tuple[str, tuple[int, int, int, int]]]]] = []
    for s in info.sheets:
        if s.kind != "worksheet":
            continue
        for c in s.formulas():
            if c.formula is None:
                continue
            targets = []
            for r in fx.references(c.formula):
                if r.workbook or r.kind not in ("cell", "range", "column", "row"):
                    continue
                sheet = s.name if r.sheet is None else (r.sheet if r.sheet in sizes else lower.get(r.sheet.casefold()))
                if sheet is None:
                    continue
                b = trace.moved_bounds(r.a1, (c.col, c.row), (c.col, c.row, c.col, c.row), sizes[sheet])
                if b is not None:
                    targets.append((sheet, b))
            if targets:
                edges.append(((s.name, c.coord), targets))
            if len(edges) > max_cells:
                break
    found = set(seeds)
    points = {(sh, *common.split_cell(cell)) for sh, cell in seeds}
    changed = True
    while changed:
        changed = False
        for key, targets in edges:
            if key in found:
                continue
            for sheet, (c1, r1, c2, r2) in targets:
                if any(sh == sheet and c1 <= col <= c2 and r1 <= row <= r2 for sh, col, row in points):
                    found.add(key)
                    col, row = common.split_cell(key[1])
                    points.add((key[0], col, row))
                    changed = True
                    break
    return found


# ----------------------------------------------------------------------------- the whole workbook

def plan_workbook(info: Any, *, product: str = "both", replace: bool = True) -> CompatPlan:
    """Plan every replacement and every reported item of one workbook (lib.workbook.WorkbookInfo)."""
    plan = CompatPlan()
    tables = info.tables()
    for s in info.sheets:
        if s.kind != "worksheet":
            continue
        for c in s.formulas():
            if c.formula is None:
                continue
            single_array = c.formula_kind == "array" and (c.array_ref or c.coord).replace("$", "") in (
                c.coord, f"{c.coord}:{c.coord}")
            can_replace = replace and (c.formula_kind in (None, "normal") or single_array)
            notes: list[str] = []
            new, rules, problems = plan_formula(c.formula, sheet=s.name, row=c.row, col=c.col, tables=tables,
                                                product=product, replace=can_replace, notes=notes)
            if new is not None and rules:
                plan.replacements.append(Replacement(s.name, c.coord, "+".join(rules), c.formula, new,
                                                     notes=notes, array=single_array))
            for rule, sev, message, fix in problems:
                plan.findings.append(common.Finding(rule, sev, message, fix, sheet=s.name, cell=c.coord))
    path = getattr(info, "path", None)
    if path is not None:
        plan.findings.extend(rule_findings(path, product))     # validations and conditional formats (v5)
    plan.findings.extend(feature_findings(info, product))
    return plan


# ----------------------------------------------------------------------------- writing existing workbooks

def plan_output(src: Path, out: str | Path | None, in_place: bool, command: str) -> Path:
    """Where the command writes (design 11 step 5): `out`, the original (in place), or <stem>_<command><suffix>."""
    if in_place and out:
        raise common.Refusal("give either --in-place or -o, not both", key="--in-place")
    if in_place:
        return src
    if out:
        target = Path(common.native_path(out)).absolute()
        if target.suffix.lower() not in (".xlsx", ".xlsm"):
            raise common.Refusal(f"{target.name}: the output must end in .xlsx or .xlsm", key="-o")
        if os.path.normcase(str(target)) == os.path.normcase(str(src)):
            raise common.Refusal("-o names the workbook itself", key="-o",
                                 fix="use --in-place to replace the original (a .bak copy is kept), or another name")
        if src.suffix.lower() == ".xlsm" and target.suffix.lower() != ".xlsm":
            raise common.Refusal(f"{src.name} holds macros; the output must also end in .xlsm", key="-o")
        return target
    return src.with_name(f"{src.stem}_{command}{src.suffix}")


def _place(tmp: Path, dest: Path) -> None:
    """Copy tmp over dest through a temporary file in dest's folder, then replace."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(f".{dest.stem}.{uuid.uuid4().hex[:6]}.tmp{dest.suffix}")
    shutil.copy2(tmp, part)
    try:
        os.replace(part, dest)
    except OSError:
        if part.exists():
            part.unlink()
        raise common.Refusal(f"{dest.as_posix()} could not be replaced; it may be open in another program",
                             fix="close it there and run again") from None


def check_calc_tolerance(value: Any) -> float:
    """The relative tolerance for calculated values (v2, A-aera-4): None gives workbook.DEFAULT_CALC_TOLERANCE;
    otherwise a number 0 or more. Typed values and formulas are always compared exactly."""
    import math

    from . import workbook as wbk
    if value is None:
        return float(wbk.DEFAULT_CALC_TOLERANCE)
    try:
        tol = float(value)
    except (TypeError, ValueError):
        tol = float("nan")
    if not math.isfinite(tol) or tol < 0:
        raise common.Refusal(f"--calc-tolerance {value!r}: give a relative tolerance 0 or more, for example 1e-9",
                             key="--calc-tolerance",
                             fix="0 compares calculated values exactly; the default is "
                                 f"{wbk.DEFAULT_CALC_TOLERANCE:g}")
    return tol


def add_calc_tolerance_argument(p: argparse.ArgumentParser) -> None:
    """--calc-tolerance for theme, compat, split and diff (one wording)."""
    from . import workbook as wbk
    p.add_argument("--calc-tolerance", type=float, default=wbk.DEFAULT_CALC_TOLERANCE, metavar="REL",
                   help="relative tolerance for the calculated values of formula cells (default "
                        f"{wbk.DEFAULT_CALC_TOLERANCE:g}; 0 = exact); typed values and formulas are always "
                        "compared exactly")


def preserved(before: Any, after: Any, intended: Any, *, calc_tolerance: float, report: common.Report) -> list[Any]:
    """The one preservation rule of theme, compat and split (v2, A-aera-16): formulas and typed values exactly,
    cached values of formula cells within `calc_tolerance` (relative), cells that hold a volatile function (RAND,
    RANDBETWEEN, RANDARRAY, NOW, TODAY, OFFSET, INDIRECT, CELL, INFO) or depend on one left out of the value
    comparison (their formulas are still compared). Writes the one statement (workbook.preservation_statement)
    as a note and into report.data["preservation"]; returns the differences."""
    from . import workbook as wbk
    volatile, _first = wbk.volatile_cells(before)
    stats: dict[str, Any] = {}
    diffs = wbk.compare(before, after, intended, calc_tolerance=calc_tolerance, skip_values=volatile, stats=stats)
    statement = wbk.preservation_statement(stats)
    report.data["preservation"] = {"statement": statement, **stats}
    report.note(statement)
    return diffs


def _excel_available() -> tuple[bool, str]:
    try:
        from . import excel
        st = excel.excel_status()
    except Exception as exc:  # noqa: BLE001 - Excel missing is a state, not a crash
        return False, f"{type(exc).__name__}: {exc}"
    return bool(st.available), st.reason or ""


def _edit_ops(edits: Sequence[CellEdit], new_sheets: Sequence[str], after_sheet: str | None,
              look: CellLook | None = None) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    for name in new_sheets:
        ops.append({"op": "add_sheet", "name": name, "after": after_sheet})
    for e in edits:
        if e.formula is not None:
            ops.append({"op": "formula", "sheet": e.sheet, "cell": e.cell, "formula": e.formula, "dynamic": e.array})
        else:
            ops.append({"op": "value", "sheet": e.sheet, "cell": e.cell, "value": e.value})
    ops.extend(_look_ops(look))
    return ops


def _look_ops(look: CellLook | None) -> list[dict[str, Any]]:
    """The "style" operations of `look` for lib.excel.apply_edits: one per sheet and cell type."""
    if look is None:
        return []
    from . import styles
    grouped: dict[tuple[str, str], list[str]] = {}
    for sheet, rng, cell_type in look.areas:
        grouped.setdefault((sheet, cell_type), []).append(rng)
    return [{"op": "style", "sheet": sheet, "ranges": ranges,
             **styles.style_spec(look.dictionary, cell_type, size=look.size)}
            for (sheet, cell_type), ranges in grouped.items()]


def _apply_look(wb: Any, look: CellLook | None) -> None:
    """Give the cells of `look` the dictionary's look on an openpyxl workbook (not the named styles: a workbook
    the plugin did not build gets no new named styles)."""
    if look is None:
        return
    from . import styles
    for sheet, rng, cell_type in look.areas:
        ws = wb[sheet]
        for r, c in common.iter_range(rng):
            styles.apply(ws.cell(r, c), look.dictionary, cell_type, size=look.size, named=False)


def _write_openpyxl(src: Path, dest: Path, edits: Sequence[CellEdit], new_sheets: Sequence[str],
                    after_sheet: str | None = None, look: CellLook | None = None) -> None:
    import warnings

    import openpyxl
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(src, keep_vba=src.suffix.lower() == ".xlsm")
    for name in new_sheets:
        if name not in wb.sheetnames:
            index = wb.sheetnames.index(after_sheet) + 1 if after_sheet in wb.sheetnames else None
            wb.create_sheet(name, index)
    from openpyxl.worksheet.formula import ArrayFormula
    for e in edits:
        ws = wb[e.sheet]
        if e.formula is not None and e.array:
            ws[e.cell].value = ArrayFormula(ref=e.cell, text=fx.to_file_formula(e.formula))
        else:
            ws[e.cell].value = fx.to_file_formula(e.formula) if e.formula is not None else e.value
    _apply_look(wb, look)
    wb.save(dest)


def write_edits(src: Path, target: Path, edits: Sequence[CellEdit], *, command: str, report: common.Report,
                intended: Any, new_sheets: Sequence[str] = (), after_sheet: str | None = None,
                in_place: bool = False, accept: Any = None, calc_tolerance: float | None = None,
                look: CellLook | None = None, new_cells_check: Any = None) -> bool:
    """Write `edits` into a copy of `src` and place it at `target` (design section 11).

    - No fragile part: openpyxl writes; with Excel available the output is then recalculated in Excel.
    - A fragile part: Excel writes (lib.excel.apply_edits keeps queries, charts, names, formats).
    - A fragile part and no Excel: refuse (exit 3), nothing written.
    - `look` (v3, A-aera-18): the cells it names get the dictionary's look, in both writers.
    - Before and after are compared by lib.workbook.compare with `intended`, under the one preservation rule
      (preserved(): typed values exactly, calculated values within `calc_tolerance`, volatile cells left out of
      the value comparison and counted); with Excel both are recalculated first (the original only as a copy).
      A difference is an error finding and nothing is placed.
    - `new_cells_check(before, after, report)` (v3, A-aera-9): called only when values were compared after
      recalculation; it adds its own error findings for cells the command creates (which compare() does not
      compare, as they have no "before") and returns their number. One or more: nothing is placed, as for a
      difference.
    Returns True when the output was placed. report.verified["recalculated"] says whether values were compared.
    """
    from . import workbook as wbk
    tolerance = check_calc_tolerance(calc_tolerance)
    pkg = wbk.package_parts(src)
    have_excel, why = _excel_available()
    if pkg.fragile and not have_excel:
        raise common.EnvUnavailable(
            f"{src.name} holds parts openpyxl would drop: {'; '.join(pkg.fragile)}. Excel is not available "
            f"({why or 'not found'}), so nothing was written",
            fix="run this command on a machine with Excel")
    work = Path(tempfile.mkdtemp(prefix=f"rotm_{command}_"))
    try:
        tmp_out = work / f"out{target.suffix.lower() or src.suffix.lower()}"
        if pkg.fragile:
            from . import excel
            ops = _edit_ops(edits, new_sheets, after_sheet, look)
            res = excel.apply_edits(src, ops, out=tmp_out, recalc_after=True)
            if not res.ok:
                raise common.EnvUnavailable(f"Excel did not apply the edits: {res.message()}",
                                            fix="close the workbook if it is open in Excel and run again")
            report.note(f"written through Excel, which keeps the parts openpyxl would drop ({len(pkg.fragile)} kinds)")
        else:
            _write_openpyxl(src, tmp_out, edits, new_sheets, after_sheet, look)
            report.note("written with openpyxl (the workbook has no part openpyxl drops)")
        # comparison (design 11 steps 6 and 7)
        inv_diffs: list[str] = []
        if have_excel:
            from . import excel
            if not pkg.fragile:            # the openpyxl output gets its values from Excel
                rr = excel.run_job(tmp_out, [{"op": "recalc"}], save=True)
                if not rr.ok:
                    raise common.EnvUnavailable(f"Excel did not recalculate the output: {rr.message()}")
            before_copy = work / f"before{src.suffix.lower()}"
            shutil.copy2(src, before_copy)
            # the original as Excel sees it (opened read-only, so not changed), then a recalculated copy
            inv_src = excel.run_job(src, [{"op": "inventory"}])
            rb = excel.run_job(before_copy, [{"op": "recalc"}], save=True)
            inv_out = excel.run_job(tmp_out, [{"op": "inventory"}])
            if not (rb.ok and inv_src.ok and inv_out.ok):
                bad = next(r for r in (rb, inv_src, inv_out) if not r.ok)
                raise common.EnvUnavailable(f"Excel did not read the workbooks for the comparison: {bad.message()}")
            inv_diffs = _inventory_differences(excel, inv_src, inv_out, len(new_sheets))
            before = wbk.snapshot(before_copy)
            report.verified["recalculated"] = True
        else:
            before = wbk.snapshot(src)
            report.verified["recalculated"] = False
        after = wbk.snapshot(tmp_out)
        diffs = preserved(before, after, intended, calc_tolerance=tolerance, report=report)
        if accept is not None:
            diffs = [d for d in diffs if not accept(d, report)]
        for text in inv_diffs:
            diffs.append(wbk.Difference("part", None, None, None, None, text))
        if have_excel and not (before.has_cached_values or not before.formula_cells):
            report.note("the original holds no values after recalculation; values were not compared")
            report.verified["recalculated"] = False
        new_cell_problems = 0
        if new_cells_check is not None and report.verified.get("recalculated"):
            new_cell_problems = int(new_cells_check(before, after, report) or 0)
        if diffs:
            for d in diffs[:50]:
                report.error("W-UNINTENDED", f"{d.message}; this change was not intended by {command}",
                             "nothing was written; report this workbook, or make the change by hand in Excel",
                             sheet=d.sheet, cell=d.cell, data={"kind": d.kind})
            if len(diffs) > 50:
                report.note(f"{len(diffs) - 50} more unintended differences are not listed")
        if diffs or new_cell_problems:
            report.data["written"] = False
            return False
        if in_place:
            stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            bak = src.with_name(f"{src.stem}.bak_{stamp}{src.suffix}")
            shutil.copy2(src, bak)
            report.data["backup"] = bak.as_posix()
        existed = target.exists() and not in_place
        _place(tmp_out, target)
        if existed:
            report.note(f"{target.name} existed and was replaced")
        report.data["output"] = target.as_posix()
        report.data["written"] = True
        if report.verified.get("recalculated"):
            report.note("both files were recalculated in Excel (the original as a copy) and no difference was found "
                        "outside the intended change; sheets, tables, names, charts, conditional formats, "
                        "validations and queries are kept")
        else:
            report.note("values were not compared after recalculation (Excel not available): formulas, constants "
                        "and parts were compared; run rotm.py recalc or check on a PC with Excel")
        return True
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _inventory_differences(excel: Any, before: Any, after: Any, new_sheets: int) -> list[str]:
    """Excel's own counts (sheets, tables, query tables, charts, shapes, format conditions, validation cells,
    pivot tables, names, queries, connections) of the original and the output; the new sheets are expected."""
    def counts(res: Any) -> dict[str, int]:
        step = dict(res.step("inventory"))
        return excel.inventory_counts(step)
    b, a = counts(before), counts(after)
    out = []
    for key in sorted(set(b) | set(a)):
        want = b.get(key, 0) + (new_sheets if key == "sheets" else 0)
        if a.get(key, 0) != want:
            out.append(f"Excel counts {key.replace('_', ' ')}: {b.get(key, 0)} in the original, {a.get(key, 0)} in "
                       "the output")
    return out


# ----------------------------------------------------------------------------- the command

def compat_workbook(path: str | Path, *, out: str | Path | None = None, in_place: bool = False,
                    report_only: bool = False, product: str = "both", strict: bool = False,
                    calc_tolerance: float | None = None) -> Any:
    """Returns a common.Report (data: replaced cells with before and after formulas; reported items).
    `calc_tolerance`: relative tolerance for calculated values in the comparison (default
    workbook.DEFAULT_CALC_TOLERANCE); typed values are compared exactly."""
    from . import workbook as wbk
    if product not in PRODUCTS:
        raise common.Refusal(f"product {product!r}: use one of {', '.join(PRODUCTS)}", key="--product")
    tolerance = check_calc_tolerance(calc_tolerance)
    src = Path(common.native_path(path)).absolute()
    info = wbk.open_workbook(src, with_values=False, with_styles=False)
    target = None if report_only else plan_output(src, out, in_place, "compat")
    report = common.Report(command="compat", target=src.as_posix(), strict=strict)
    report.data.update(product=product, output=None, written=False)
    plan = plan_workbook(info, product=product, replace=True)
    # reported items
    for f in plan.findings:
        report.add(f)
    # replacements: one finding per rule and sheet (grouped), and the list in data. In report-only mode a
    # replaceable item is still a problem in the workbook: X-PREFIX and X-UDF are errors (#NAME? in Excel),
    # X-STRUCTREF a warning (as the audit), X-CONCAT information for CONCATENATE and a warning for an Excel
    # CONCAT that Google Sheets' CONCAT cannot take (v4, A-aera-20).
    for r in plan.replacements:
        for rule in r.rule.split("+"):
            if rule == "X-CONCAT":
                for finding in _concat_findings(r, report_only):
                    report.add(finding)
            elif report_only:
                sev = {"X-PREFIX": "error", "X-UDF": "error", "X-STRUCTREF": "warning"}.get(rule, "info")
                report.add(common.Finding(rule, sev, _replaced_message(rule, done=False),
                                          "run rotm.py compat without --report-only", sheet=r.sheet, cell=r.cell))
            else:
                report.info(rule, _replaced_message(rule), "nothing to change", sheet=r.sheet, cell=r.cell)
    report.data["replaced_count"] = len(plan.replacements)
    report.data["replaced"] = [{"sheet": r.sheet, "cell": r.cell, "rules": r.rule.split("+"), "before": r.before,
                                "after": r.after} for r in plan.replacements[:REPLACED_SHOWN]]
    if info.warnings:
        for w in info.warnings[:5]:
            report.note(f"reader: {w}")
    if report_only:
        report.note("report only: nothing was written")
        if plan.replacements:
            report.note(f"{len(plan.replacements)} cell(s) would be replaced; run without --report-only to write them")
    elif not plan.replacements:
        report.note("nothing to replace: no file was written")
    else:
        edits = [CellEdit(r.sheet, r.cell, formula=r.after, array=r.array) for r in plan.replacements]
        intended = wbk.Intended(kind="cells", cells={(r.sheet, r.cell) for r in plan.replacements})
        name_fixes = {(r.sheet, r.cell) for r in plan.replacements
                      if "X-PREFIX" in r.rule.split("+") or "X-UDF" in r.rule.split("+")}
        may_change = dependents(info, name_fixes) if name_fixes else set()

        def accept(d: Any, rep: common.Report) -> bool:
            # A missing prefix or an _xludf. name makes Excel show #NAME? (or the IFERROR fallback); repairing the
            # name is meant to change that cell's value and the values of the cells that read it.
            if d.kind == "value" and (d.sheet, d.cell) in may_change:
                rep.info("X-FIXED", "a value changed because a function name it depends on was repaired (before: "
                         "an error or an IFERROR fallback)", "nothing to change", sheet=d.sheet, cell=d.cell)
                return True
            return False

        assert target is not None
        write_edits(src, target, edits, command="compat", report=report, intended=intended, in_place=in_place,
                    accept=accept, calc_tolerance=tolerance)
    report.finish(strict)
    if (not report_only and plan.replacements and report.data.get("written")
            and not report.verified.get("recalculated") and report.exit_code == common.EXIT_OK):
        report.exit_code, report.status = common.EXIT_NOT_AVAILABLE, "not_verified"
    return report


def _concat_findings(r: Replacement, report_only: bool) -> list[common.Finding]:
    """The X-CONCAT findings of one replaced cell: information for a CONCATENATE (design 15); for each Excel
    CONCAT rewritten with & (r.notes), a warning in report-only mode and information when it was written."""
    out: list[common.Finding] = []
    if not r.notes or "CONCATENATE" in fx.analyze(fx.display_form(r.before)).functions:
        out.append(common.Finding("X-CONCAT", "info", _replaced_message("X-CONCAT", done=not report_only),
                                  "run rotm.py compat without --report-only" if report_only else "nothing to change",
                                  sheet=r.sheet, cell=r.cell))
    for what in r.notes:
        if report_only:
            out.append(common.Finding("X-CONCAT", "warning", f"{what}: {CONCAT_SHEETS}", CONCAT_REWRITTEN_FIX,
                                      sheet=r.sheet, cell=r.cell))
        else:
            form = 'a&""' if what.endswith(" 1 argument") else "a&b&..."
            out.append(common.Finding("X-CONCAT", "info", f"replaced {what} with {form}: {CONCAT_SHEETS}",
                                      "nothing to change", sheet=r.sheet, cell=r.cell))
    return out


def _replaced_message(rule: str, done: bool = True) -> str:
    what = {
        "X-STRUCTREF": ("a #This Row structured reference ([@Column]), which Google Sheets does not support "
                        "(platforms A2.4, confirmed)", "the same-row A1 reference"),
        "X-PREFIX": ("a function stored without the _xlfn. prefix Excel needs in the file (Excel shows #NAME?)",
                     "the function with its prefix"),
        "X-UDF": ("_xludf.<function>: a catalogue function stored as an unknown user-defined function (Excel shows "
                  "#NAME?)", "the function itself"),
        "X-CONCAT": ("CONCATENATE(a,b,...)", "a&b&... (one function call less)"),
    }.get(rule, (rule, "its replacement"))
    return f"replaced {what[0]} with {what[1]}" if done else f"{what[0]}; compat replaces it with {what[1]}"


def add_compat_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("workbook")
    p.add_argument("-o", "--out", help="output file (default <stem>_compat.xlsx)")
    p.add_argument("--in-place", action="store_true", help="replace the workbook (a .bak_<time>.xlsx copy is kept)")
    p.add_argument("--report-only", action="store_true", help="report; write nothing")
    p.add_argument("--product", choices=PRODUCTS, default="both", help="target products (default both)")
    add_calc_tolerance_argument(p)
    p.add_argument("--strict", action="store_true", help="exit 1 on any warning")
    p.add_argument("--json", metavar="FILE", help="write the report as JSON ('-' = stdout)")
    p.add_argument("--quiet", action="store_true", help="print only the summary line")


def run_compat(args: argparse.Namespace) -> int:
    report = compat_workbook(args.workbook, out=args.out, in_place=args.in_place, report_only=args.report_only,
                             product=args.product, strict=args.strict,
                             calc_tolerance=getattr(args, "calc_tolerance", None))
    return report.emit(args.json, quiet=getattr(args, "quiet", False))

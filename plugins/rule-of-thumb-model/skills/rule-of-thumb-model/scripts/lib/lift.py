"""`lift`: take a model beyond the spreadsheet: write a Python script that reproduces every helper column in
order and ties its results to the workbook's numbers (design section 15; F20; Max, spec 1.6 "going above
Spreadsheet (into Python or R)").

Owner: M8. This module uses the standard library and openpyxl only. The script it writes needs only the
standard library to compute the model and openpyxl to read the workbook's values; pandas is used only when the
user asks for data frames (Model.frames()).

How it works:
- The specification is the source of the steps. Every helper column, block and check expression is parsed with
  lib.formula.tokenize() and translated into one Python expression that calls the functions of the script
  runtime below (SUMIFS, EOMONTH, INDEX, ...), which follow Excel's rules for the cases noted in each function.
- The script reads the raw tables from raw/<table>.csv with the contract types, computes the helper columns, the
  blocks and the checks in the specification's order (dependencies first), reads the values Excel saved in the
  workbook (openpyxl, data_only) and prints a tie-out table: cell, workbook value, script value, difference.
- Cell positions come from the workbook's manifest (model.manifest.json, written by build) when the script runs,
  so a rebuild with a different layout does not need a new script; a changed specification does (the script
  warns when the manifest names another specification hash).
- Dates are Excel serial day numbers inside the model (1900 date system), as in the workbook, so every date
  function gives the workbook's result.
- A function or form the translator does not cover makes that step "not reproduced": lift reports it, the
  script shows its cells as not reproduced and exits 1 (design 15). Before the script is written, lift names
  each function of the model outside supported_functions() as LIFT-UNSUPPORTED (function, cell count, first
  cell; v3, A-aera-13).
- The control total of a raw table is the one accepted for the snapshot lift reads (raw/<table>.controls.json,
  lib.controls), else the contract's (v3, J-5). v9 (r8 verdict [A-aera-2, C-sol-1]): the order is: the total
  accepted for this snapshot in raw/<t>.controls.json under the agreement in force; else the agreed contract's
  total. A total accepted under an earlier agreement (the contract was agreed again since, for example with another
  total) is not used: lift prints a note that names it and the script uses the contract's total.
- R output is not written: no R was available to test it (design 15: "R output only if it can be tested").
"""
from __future__ import annotations

import argparse
import os
import pprint
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LANGS = ("python", "r")
SCRIPT_SUFFIX = "_lift.py"
RUN_TIMEOUT_S = 900

# >>> SCRIPT RUNTIME ----------------------------------------------------------------------------------------
# Everything from this line to the closing marker line below is copied verbatim into every script lift
# writes. Standard library only; openpyxl is imported inside read_workbook_values(). Where a result depends on
# one of Excel's rules, the rule is stated next to the code.
import argparse as _argparse
import bisect as _bisect
import csv as _csv
import datetime as _dt
import itertools as _itertools
import json as _json
import math as _math
import re as _re
import sys as _sys
from decimal import ROUND_DOWN as _ROUND_DOWN
from decimal import ROUND_HALF_UP as _ROUND_HALF_UP
from decimal import ROUND_UP as _ROUND_UP
from decimal import Decimal as _Decimal
from decimal import InvalidOperation as _InvalidOperation
from pathlib import Path as _Path

EXCEL_ROWS = 1048576          # rows of an Excel sheet; a whole-column range has this many
TOTAL = "total"               # row index of a block's total row
_OMIT = object()              # an optional argument that was not given
# The tie-out rule (v4, A-aera-6): two numbers tie out when |script - workbook| <= tolerance + REL_TOLERANCE *
# max(|script|, |workbook|). The tolerance is the model's check tolerance (absolute, in the unit of the cell).
# REL_TOLERANCE covers only the rounding of double-precision arithmetic (Excel adds a column in order; Python's
# sum() rounds less). Measured on 2026-11-25: the largest relative difference of a tie was 6.5e-14 on the six
# shipped models, 7.1e-15 on the financial functions against Excel 16, and 7.6e-15 on sums of about 2.6e14 with
# fractions; 1e-13 is the smallest power of ten above them. It allows 0.1 at 1,000,000,000,000.
REL_TOLERANCE = 1e-13


def _raise_self(self, *_args):
    raise self


class XLErr(Exception):
    """An Excel error value (#DIV/0!, #N/A, #VALUE!, #REF!, #NUM!). Raised while a cell is computed and kept as
    the cell's value; arithmetic on a kept error raises it again, so errors pass on as they do in Excel."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code

    def __repr__(self):
        return self.code

    __str__ = __repr__
    __add__ = __radd__ = __sub__ = __rsub__ = __mul__ = __rmul__ = _raise_self
    __truediv__ = __rtruediv__ = __pow__ = __rpow__ = __neg__ = __pos__ = _raise_self
    __lt__ = __le__ = __gt__ = __ge__ = __float__ = __int__ = _raise_self


class NotReproduced(Exception):
    """A value this script does not reproduce (a function or form the translator does not cover). Kept as the
    cell's value like an error; every cell that uses it is not reproduced either."""

    __add__ = __radd__ = __sub__ = __rsub__ = __mul__ = __rmul__ = _raise_self
    __truediv__ = __rtruediv__ = __pow__ = __rpow__ = __neg__ = __pos__ = _raise_self
    __lt__ = __le__ = __gt__ = __ge__ = __float__ = __int__ = _raise_self


def NOT_REPRODUCED(reason):
    raise NotReproduced(reason)


def ERROR_VALUE(code):
    """An error value written in a formula, e.g. #N/A."""
    raise XLErr(code)


class _Blank:
    """An empty cell. In arithmetic it counts as 0 and joined to text as "", as in Excel."""

    __slots__ = ()

    def __repr__(self):
        return "BLANK"

    def __bool__(self):
        return False

    def __add__(self, other):
        return 0 + other

    def __radd__(self, other):
        return other + 0

    def __sub__(self, other):
        return 0 - other

    def __rsub__(self, other):
        return other - 0

    def __mul__(self, other):
        return 0 * other

    def __rmul__(self, other):
        return other * 0

    def __truediv__(self, other):
        return 0 / other

    def __rtruediv__(self, other):
        return other / 0

    def __neg__(self):
        return 0

    def __pos__(self):
        return 0


BLANK = _Blank()

# ------------------------------------------------------------------ dates (Excel serial day numbers, 1900 system)
_D0 = _dt.date(1899, 12, 30)
_ISO_DATE = _re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?)?$")
_NUMBER_TEXT = _re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


def date_serial(d):
    """Excel serial day number of a date or datetime. Excel counts a 1900-02-29 that did not exist, so dates
    before 1900-03-01 are one lower than a plain day count."""
    if isinstance(d, _dt.datetime):
        whole = date_serial(d.date())
        frac = (d.hour * 3600 + d.minute * 60 + d.second + d.microsecond / 1e6) / 86400
        return whole + frac if frac else whole
    n = (d - _D0).days
    return n if n >= 61 else n - 1


def serial_ymd(s):
    """(year, month, day) of a serial day number; 0 is 1900-01-00 and 60 is 1900-02-29, as Excel shows them."""
    n = int(_math.floor(s))
    if n < 0:
        raise XLErr("#NUM!")
    if n == 0:
        return 1900, 1, 0
    if n == 60:
        return 1900, 2, 29
    d = _D0 + _dt.timedelta(days=n if n > 60 else n + 1)
    return d.year, d.month, d.day


def _ymd_serial(y, m, d):
    """Serial of day d of month m of year y; m and d may run past their ranges, as DATE allows."""
    y2, m0 = divmod(y * 12 + (m - 1), 12)
    if not 1900 <= y2 <= 9999:
        raise XLErr("#NUM!")
    return date_serial(_dt.date(y2, m0 + 1, 1)) + (d - 1)


def parse_date_text(text):
    """Serial of an ISO date or datetime text (YYYY-MM-DD, optionally with a time), or None."""
    m = _ISO_DATE.match(text.strip())
    if not m:
        return None
    y, mo, d, hh, mm, ss, frac = m.groups()
    try:
        value = _dt.datetime(int(y), int(mo), int(d), int(hh or 0), int(mm or 0), int(ss or 0),
                             int((frac or "0")[:6].ljust(6, "0")))
    except ValueError:
        return None
    return date_serial(value if (hh or mm or ss) else value.date())


# ------------------------------------------------------------------ ranges
class Rng:
    """A rectangle of cell values (a list of rows; BLANK for an empty cell). `tall` marks a whole column: it
    has Excel's 1,048,576 rows, and the rows after the stored ones are empty."""

    __slots__ = ("rows", "tall", "_flat")

    def __init__(self, rows, tall=False):
        self.rows = rows
        self.tall = tall
        self._flat = None

    @classmethod
    def column(cls, values, tall=False):
        return cls([[v] for v in values], tall)

    @classmethod
    def row(cls, values):
        return cls([list(values)])

    @property
    def height(self):
        return EXCEL_ROWS if self.tall else len(self.rows)

    @property
    def width(self):
        return len(self.rows[0]) if self.rows else 1

    def flat(self):
        if self._flat is None:
            self._flat = [v for r in self.rows for v in r]
        return self._flat

    def vector(self):
        """Values of a one-row or one-column range, in order."""
        if len(self.rows) == 1:
            return list(self.rows[0])
        if self.width == 1:
            return [r[0] for r in self.rows]
        raise XLErr("#N/A")

    def cell(self, r, c):
        """The value at row r, column c (both from 1)."""
        if r < 1 or c < 1 or r > self.height or c > self.width:
            raise XLErr("#REF!")
        if r > len(self.rows):
            return BLANK
        return self.rows[r - 1][c - 1]

    def __repr__(self):
        return f"Rng({len(self.rows)}x{self.width}{', whole column' if self.tall else ''})"

    def __add__(self, o):
        return _elementwise(self, o, _op_add)

    def __radd__(self, o):
        return _elementwise(o, self, _op_add)

    def __sub__(self, o):
        return _elementwise(self, o, _op_sub)

    def __rsub__(self, o):
        return _elementwise(o, self, _op_sub)

    def __mul__(self, o):
        return _elementwise(self, o, _op_mul)

    def __rmul__(self, o):
        return _elementwise(o, self, _op_mul)

    def __truediv__(self, o):
        return _elementwise(self, o, _op_div)

    def __rtruediv__(self, o):
        return _elementwise(o, self, _op_div)

    def __neg__(self):
        return _elementwise(self, 0, lambda x, _y: -_num(x))

    def __pos__(self):
        return self


def _op_add(x, y):
    return _num(x) + _num(y)


def _op_sub(x, y):
    return _num(x) - _num(y)


def _op_mul(x, y):
    return _num(x) * _num(y)


def _op_div(x, y):
    return _num(x) / _num(y)


def _safe(fn, *args):
    """fn(*args), or the error value it raised (as Excel keeps an error in one cell of an array)."""
    try:
        return fn(*args)
    except (XLErr, NotReproduced) as e:
        return e
    except ZeroDivisionError:
        return XLErr("#DIV/0!")
    except (TypeError, ValueError, OverflowError):
        return XLErr("#VALUE!")


def _elementwise(a, b, fn):
    """fn applied cell by cell; a single cell, row or column is repeated to the other's size (array formulas)."""
    ra = a.rows if isinstance(a, Rng) else [[a]]
    rb = b.rows if isinstance(b, Rng) else [[b]]
    h = max(len(ra), len(rb))
    w = max(len(ra[0]) if ra else 1, len(rb[0]) if rb else 1)

    def pick(rows, i, j):
        hi, wi = len(rows), len(rows[0]) if rows else 1
        if hi == 1 or i < hi:
            if wi == 1 or j < wi:
                return rows[0 if hi == 1 else i][0 if wi == 1 else j]
        return XLErr("#N/A")

    return Rng([[_safe(fn, pick(ra, i, j), pick(rb, i, j)) for j in range(w)] for i in range(h)])


# ------------------------------------------------------------------ coercion (Excel's rules)
def _val(x):
    """One value. A one-cell range gives its cell; a larger range gives #VALUE! (no implicit intersection)."""
    if isinstance(x, Rng):
        if len(x.rows) == 1 and len(x.rows[0]) == 1 and not x.tall:
            x = x.rows[0][0]
        else:
            raise XLErr("#VALUE!")
    if isinstance(x, (XLErr, NotReproduced)):
        raise x
    if x is None or x is _OMIT:
        return BLANK
    return x


def _text_to_number(text):
    """A number from text as Excel coerces it ("12", "1,234.5", "5%", "2026-01-31"); #VALUE! otherwise."""
    s = text.strip().replace(",", "")
    if _NUMBER_TEXT.match(s):
        return int(s) if _re.fullmatch(r"[+-]?\d+", s) else float(s)
    if s.endswith("%") and _NUMBER_TEXT.match(s[:-1]):
        return float(s[:-1]) / 100
    d = parse_date_text(s)
    if d is not None:
        return d
    raise XLErr("#VALUE!")


def _num(x):
    """A number for arithmetic: blank is 0, TRUE is 1, a date is its serial, number text is coerced."""
    x = _val(x)
    if x is BLANK:
        return 0
    if isinstance(x, bool):
        return int(x)
    if isinstance(x, (int, float)):
        return x
    if isinstance(x, _dt.date):
        return date_serial(x)
    if isinstance(x, str):
        return _text_to_number(x)
    raise XLErr("#VALUE!")


def _general(v):
    """A number as Excel's General format writes it in text: up to 15 significant digits."""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int):
        return str(v)
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    t = format(v, ".15g")
    if "e" in t:
        mant, exp = t.split("e")
        return f"{mant}E{int(exp):+03d}"
    return t


def _text(x):
    """Text for joining: blank is "", TRUE is "TRUE", numbers in General format (dates as their serial)."""
    x = _val(x)
    if x is BLANK:
        return ""
    if isinstance(x, bool):
        return "TRUE" if x else "FALSE"
    if isinstance(x, (int, float)):
        return _general(x)
    if isinstance(x, _dt.date):
        return _general(date_serial(x))
    return str(x)


def truthy(x):
    """The truth value of a condition: blank and 0 are FALSE, other numbers TRUE; text other than TRUE/FALSE
    gives #VALUE!."""
    x = _val(x)
    if x is BLANK:
        return False
    if isinstance(x, bool):
        return x
    if isinstance(x, (int, float)):
        return x != 0
    if isinstance(x, _dt.date):
        return True
    if isinstance(x, str):
        u = x.strip().upper()
        if u == "TRUE":
            return True
        if u == "FALSE":
            return False
    raise XLErr("#VALUE!")


# ------------------------------------------------------------------ operators
def _rank(v):
    if isinstance(v, bool):
        return 2, int(v)
    if isinstance(v, (int, float)):
        return 0, v
    if isinstance(v, _dt.date):
        return 0, date_serial(v)
    return 1, str(v).lower()


def _cmp(a, b):
    """Excel's comparison: numbers < text < logical values; text without regard to case; blank is 0, "" or
    FALSE, whichever the other side is."""
    a, b = _val(a), _val(b)
    if a is BLANK:
        a = "" if isinstance(b, str) else False if isinstance(b, bool) else 0
    if b is BLANK:
        b = "" if isinstance(a, str) else False if isinstance(a, bool) else 0
    ka, kb = _rank(a), _rank(b)
    return (ka > kb) - (ka < kb)


def _compare(a, b, test):
    if isinstance(a, Rng) or isinstance(b, Rng):
        return _elementwise(a, b, lambda x, y: test(_cmp(x, y)))
    return test(_cmp(a, b))


def EQ(a, b):
    return _compare(a, b, lambda c: c == 0)


def NE(a, b):
    return _compare(a, b, lambda c: c != 0)


def LT(a, b):
    return _compare(a, b, lambda c: c < 0)


def LE(a, b):
    return _compare(a, b, lambda c: c <= 0)


def GT(a, b):
    return _compare(a, b, lambda c: c > 0)


def GE(a, b):
    return _compare(a, b, lambda c: c >= 0)


def JOIN(a, b):
    """The & operator."""
    if isinstance(a, Rng) or isinstance(b, Rng):
        return _elementwise(a, b, lambda x, y: _text(x) + _text(y))
    return _text(a) + _text(b)


def POW(a, b):
    """The ^ operator: 0^0 and a negative base with a fractional power give #NUM!, 0 to a negative power #DIV/0!."""
    if isinstance(a, Rng) or isinstance(b, Rng):
        return _elementwise(a, b, POW)
    x, y = _num(a), _num(b)
    if x == 0 and y == 0:
        raise XLErr("#NUM!")
    if x == 0 and y < 0:
        raise XLErr("#DIV/0!")
    if x < 0 and y != int(y):
        raise XLErr("#NUM!")
    return x ** y


# ------------------------------------------------------------------ aggregation
def as_ref(x):
    """A value the formula reaches through a cell reference, as an argument of SUM, AVERAGE, MIN, MAX, PRODUCT,
    COUNT or NPV (v5, A-aera-7): a one-cell range, so the function skips text in it, also text that reads as a
    number, and TRUE/FALSE, as Excel does for a reference. Measured with Excel 16 on 2026-11-25 with A1 the text
    001: SUM(A1) gives 0 and SUM("001") gives 1; IF, CHOOSE, IFS, SWITCH, INDEX and XLOOKUP hand the reference on
    (SUM(IF(TRUE,A1)) gives 0), IFERROR, IFNA, VLOOKUP and the other functions give a value
    (SUM(IFERROR(A1,0)) gives 1). The translator writes as_ref() where Excel's argument is a reference."""
    return x if isinstance(x, Rng) else Rng([[x]])


def _numbers(args):
    """The numbers SUM, AVERAGE, MIN, MAX, PRODUCT and NPV use: in a range (and in a value reached through a
    reference, as_ref) only numbers (text, also number text, TRUE/FALSE and blanks are skipped); a value given
    directly counts when it is a number, TRUE/FALSE or number text, and other text gives #VALUE! (Excel 16,
    2026-11-25: SUM("abc") and SUM("") give #VALUE!)."""
    for a in args:
        if a is _OMIT:
            continue
        if isinstance(a, Rng):
            for v in a.flat():
                if isinstance(v, (XLErr, NotReproduced)):
                    raise v
                if isinstance(v, bool) or v is BLANK or v is None or isinstance(v, str):
                    continue
                yield date_serial(v) if isinstance(v, _dt.date) else v
            continue
        v = _val(a)
        if v is BLANK:
            continue
        if isinstance(v, bool):
            yield int(v)
        elif isinstance(v, (int, float)):
            yield v
        elif isinstance(v, _dt.date):
            yield date_serial(v)
        elif isinstance(v, str):
            yield _text_to_number(v)                   # #VALUE! for text that does not read as a number


def SUM(*args):
    return sum(_numbers(args))


def AVERAGE(*args):
    nums = list(_numbers(args))
    if not nums:
        raise XLErr("#DIV/0!")
    return sum(nums) / len(nums)


def MIN(*args):
    nums = list(_numbers(args))
    return min(nums) if nums else 0


def MAX(*args):
    nums = list(_numbers(args))
    return max(nums) if nums else 0


def PRODUCT(*args):
    out = 1
    for v in _numbers(args):
        out *= v
    return out


def COUNT(*args):
    """Numbers in ranges; numbers, TRUE/FALSE and number text given directly. Errors are not counted."""
    n = 0
    for a in args:
        if isinstance(a, Rng):
            n += sum(1 for v in a.flat()
                     if isinstance(v, (int, float, _dt.date)) and not isinstance(v, bool))
            continue
        try:
            v = _val(a)
        except (XLErr, NotReproduced):
            continue
        if isinstance(v, (bool, int, float, _dt.date)):
            n += 1
        elif isinstance(v, str):
            try:
                _text_to_number(v)
                n += 1
            except XLErr:
                pass
    return n


def COUNTA(*args):
    """Cells that are not empty (text "" from a formula and error values count)."""
    n = 0
    for a in args:
        if isinstance(a, Rng):
            n += sum(1 for v in a.flat() if not (v is BLANK or v is None))
        elif a is not _OMIT and not (a is BLANK or a is None):
            n += 1
    return n


def COUNTBLANK(rng):
    vals = rng.flat() if isinstance(rng, Rng) else [_val(rng)]
    n = sum(1 for v in vals if v is BLANK or v is None or v == "")
    if isinstance(rng, Rng) and rng.tall:
        n += EXCEL_ROWS - len(rng.rows)
    return n


def SUMPRODUCT(*arrays):
    """Sum of the products of same-size ranges; text, TRUE/FALSE and blanks count as 0."""
    rs = [a if isinstance(a, Rng) else Rng([[_val(a)]]) for a in arrays]
    h = max(len(r.rows) for r in rs)
    w = rs[0].width
    for r in rs:
        if r.width != w or (len(r.rows) != h and not r.tall):
            raise XLErr("#VALUE!")
    total = 0
    for i in range(h):
        for j in range(w):
            prod = 1
            for r in rs:
                v = r.rows[i][j] if i < len(r.rows) else BLANK
                if isinstance(v, (XLErr, NotReproduced)):
                    raise v
                if isinstance(v, bool) or not isinstance(v, (int, float, _dt.date)):
                    v = 0
                elif isinstance(v, _dt.date):
                    v = date_serial(v)
                prod *= v
            total += prod
    return total


def ROWS(rng):
    return rng.height if isinstance(rng, Rng) else 1


def COLUMNS(rng):
    return rng.width if isinstance(rng, Rng) else 1


# ------------------------------------------------------------------ equality keys (MATCH, XMATCH, XLOOKUP, SWITCH)
# Exact matches of a lookup; the conditional functions use the criteria rules below instead.
def _cell_key(v):
    if isinstance(v, bool):
        return "b", v
    if isinstance(v, (int, float)):
        return "n", float(v)
    if isinstance(v, _dt.date):
        return "n", float(date_serial(v))
    if v is BLANK or v is None or v == "":
        return ("e",)
    if isinstance(v, str):
        return "t", v.lower()
    return ("x",)                  # error values never meet an exact match


def _wildcard(pattern):
    out, i = [], 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "~" and i + 1 < len(pattern):
            out.append(_re.escape(pattern[i + 1]))
            i += 2
            continue
        out.append(".*" if ch == "*" else "." if ch == "?" else _re.escape(ch))
        i += 1
    return _re.compile("".join(out), _re.IGNORECASE | _re.DOTALL)


# ------------------------------------------------------------------ criteria (SUMIFS, COUNTIFS and the rest)
# v5, A-aera-7: SUMIFS, COUNTIFS, AVERAGEIFS, MAXIFS, MINIFS, SUMIF, COUNTIF and AVERAGEIF match a criterion with
# the rules of excel_criteria_match in the plugin's lib/criteria.py, which were measured with Excel 16.0 on
# 2026-11-25 (COUNTIFS, COUNTIF and SUMIFS on 152 + 61 pairs). The rules are copied here so that this script runs
# alone; a plugin test asserts that both give the same answer on the measured pairs.
# - A criterion that reads as a number (a number, or a text such as '001', '1.0', ' 1', '1e0', '1,000', '$1', '(1)',
#   '1%', '2024-01-31', '1/31/2024', '12:00') matches number cells of that value and text cells that read as the
#   same number, compared on their first 15 significant digits. A month and day without a year read as a date of
#   the current year.
# - A text criterion matches text cells only, without case. * and ? are wildcards (* any text, also none; ? one
#   character); ~ makes the next character plain (~* and ~? a literal * and ?, ~~ a literal ~, ~ before any other
#   character is dropped); a ~ at the end is dropped. Without * or ? a ~ is an ordinary character (v9, measured
#   with Excel 16 on 2026-11-26).
# - The texts TRUE and FALSE (any case) match the logical values only.
# - A leading =, <>, <, >, <= or >= is an operator: <> with a number never counts a text cell as equal; = reads texts
#   as numbers; <, >, <= and >= compare numbers with numbers and texts with texts (without case). '=' alone matches
#   empty cells, '<>' alone every cell that is not empty.
# - An empty criterion cell counts as the number 0.
# - The empty text "" as the criterion (typed, or from a formula) matches empty cells and cells that hold the empty
#   text (measured with Excel 16 on 2026-11-25: COUNTIF over an empty cell, a cell ="", the text 001 and the number
#   0 gives 2 with the criterion ""; lib/criteria.py reads "" as an empty cell, which counts as 0).
_INDEX_CACHE = {}
_FACTS_CACHE = {}
_C_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_C_CORE = r"(?:[0-9]{1,3}(?:,[0-9]{3,})+|[0-9]+)(?:\.[0-9]*)?(?:[eE][+-]?[0-9]+)?|\.[0-9]+(?:[eE][+-]?[0-9]+)?"
_C_NUMBER_RE = {
    side: _re.compile(r"^(?P<pct1>% *)?(?P<sign>[+-] *)?(?P<cur>" + cur + r" *(?P<sign2>[+-] *)?)?"
                      r"(?:\( *(?P<pcore>" + _C_CORE + r") *\)|(?P<core>" + _C_CORE + r")) *(?P<pct2>%)?$")
    for side, cur in (("criterion", r"\$"), ("value", "[$€]"))}
_C_FRACTION_RE = _re.compile(r"^([0-9]+) +([0-9]+)/([0-9]+)$")
_C_TIME_RE = _re.compile(r"^([0-9]{1,4}):([0-9]{2})(?::([0-9]{2}(?:\.[0-9]+)?))?(?: *([AaPp][Mm]))?$")
_C_MONTHS = {name: i for i, names in enumerate(
    (("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",), ("jun", "june"),
     ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
     ("dec", "december")), 1) for name in names}
_C_DATE_FORMS = (
    (_re.compile(r"^([0-9]{4})-([0-9]{1,2})-([0-9]{1,2})$"), "ymd"),
    (_re.compile(r"^([0-9]{4})/([0-9]{1,2})/([0-9]{1,2})$"), "ymd"),
    (_re.compile(r"^([0-9]{1,2})/([0-9]{1,2})/([0-9]{4})$"), "mdy"),
    (_re.compile(r"^([0-9]{1,2})[/-]([0-9]{1,2})$"), "md"),
    (_re.compile(r"^([A-Za-z]+) +([0-9]{4})$"), "my"),
    (_re.compile(r"^([0-9]{1,2}) +([A-Za-z]+) +([0-9]{4})$"), "dmy"),
)
_C_MAX = _Decimal("9.99999999999999E+307")
_C_OPERATORS = ("<=", ">=", "<>", "<", ">", "=")


def _c_sig15(d):
    """`d` cut to its first 15 significant digits (Excel keeps 15)."""
    if d == 0:
        return _Decimal(0)
    return d.quantize(_Decimal(1).scaleb(d.adjusted() - 14), rounding=_ROUND_DOWN)


def _c_serial(d):
    """Excel's serial number of a date, or None outside the years 1900 to 9999."""
    if not 1900 <= d.year <= 9999:
        return None
    if d < _dt.date(1900, 3, 1):
        return (d - _dt.date(1899, 12, 31)).days
    return (d - _D0).days


def _c_date_of(text):
    for regex, form in _C_DATE_FORMS:
        m = regex.match(text)
        if not m:
            continue
        try:
            if form == "ymd":
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            elif form == "mdy":
                mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            elif form == "md":
                mo, d, y = int(m.group(1)), int(m.group(2)), _dt.date.today().year
            elif form == "my":
                mo, d, y = _C_MONTHS.get(m.group(1).lower(), 0), 1, int(m.group(2))
            else:
                d, mo, y = int(m.group(1)), _C_MONTHS.get(m.group(2).lower(), 0), int(m.group(3))
            return _c_serial(_dt.date(y, mo, d))
        except ValueError:
            return None
    return None


def _c_time_of(text):
    m = _C_TIME_RE.match(text)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    s = _Decimal(m.group(3)) if m.group(3) else _Decimal(0)
    ampm = (m.group(4) or "").lower()
    if mi > 59 or s >= 60:
        return None
    if ampm:
        if not 1 <= h <= 12:
            return None
        h = (h % 12) + (12 if ampm == "pm" else 0)
    return (_Decimal(h) * 3600 + _Decimal(mi) * 60 + s) / _Decimal(86400)


def _c_text_number(text, side="criterion"):
    """The number the criteria rules read in `text` (15 significant digits), or None. `side`: "criterion" or
    "value" (a cell value also reads a leading euro sign)."""
    s = str(text).translate(_C_DIGITS).strip(" ")
    if not s:
        return None
    m = _C_NUMBER_RE[side].match(s)
    if m:
        if (m.group("pct1") and m.group("pct2")) or (m.group("sign") and m.group("sign2")) \
                or (m.group("pcore") and (m.group("sign") or m.group("sign2"))):
            return None
        core = (m.group("pcore") or m.group("core")).replace(",", "")
        try:
            value = _Decimal(core)
        except _InvalidOperation:
            return None
        if not value.is_finite() or abs(value) > _C_MAX:
            return None
        negative = (m.group("sign") or "").strip() == "-" or (m.group("sign2") or "").strip() == "-" \
            or bool(m.group("pcore"))
        if negative:
            value = -value
        if m.group("pct1") or m.group("pct2"):
            value = value / 100
        return _c_sig15(value)
    m = _C_FRACTION_RE.match(s)
    if m:
        whole, num, den = (int(x) for x in m.groups())
        return _c_sig15(_Decimal(whole) + _Decimal(num) / _Decimal(den)) if den else None
    t = _c_time_of(s)
    if t is not None:
        return _c_sig15(t)
    date_part, _, time_part = s.partition(" ")
    serial = _c_date_of(s)
    if serial is not None:
        return _Decimal(serial)
    if time_part:
        serial = _c_date_of(date_part)
        t = _c_time_of(time_part.strip(" "))
        if serial is not None and t is not None:
            return _c_sig15(_Decimal(serial) + t)
    return None


def _c_cell_number(v):
    """A number cell's value (dates and times as serial numbers, 15 significant digits); None for any other value."""
    if isinstance(v, bool) or v is None or v is BLANK:
        return None
    if isinstance(v, _dt.datetime):
        serial = _c_serial(v.date())
        if serial is None:
            return None
        secs = v.hour * 3600 + v.minute * 60 + v.second + _Decimal(v.microsecond) / _Decimal(1_000_000)
        return _c_sig15(_Decimal(serial) + _Decimal(secs) / _Decimal(86400))
    if isinstance(v, _dt.date):
        serial = _c_serial(v)
        return _Decimal(serial) if serial is not None else None
    if isinstance(v, _dt.time):
        return _c_sig15(_Decimal(v.hour * 3600 + v.minute * 60 + v.second) / _Decimal(86400))
    if isinstance(v, (int, float)):
        try:
            d = _Decimal(repr(v)) if isinstance(v, float) else _Decimal(v)
        except _InvalidOperation:
            return None
        return _c_sig15(d) if d.is_finite() else None
    return None


def _c_pattern(p):
    out, i = [], 0
    while i < len(p):
        ch = p[i]
        if ch == "~":
            if i + 1 == len(p):
                break                                   # a ~ at the end is dropped
            out.append(_re.escape(p[i + 1]))             # ~ makes the next character plain, whatever it is
            i += 2
            continue
        elif ch == "*":
            out.append(".*")
        elif ch == "?":
            out.append(".")
        else:
            out.append(_re.escape(ch))
        i += 1
    return _re.compile("".join(out), _re.IGNORECASE | _re.DOTALL)


class _Crit:
    """One criterion as the rules read it. op: "" or an operator; kind: number | bool | text | empty | blank_text."""

    __slots__ = ("op", "kind", "number", "boolean", "text", "fold", "regex")

    def __init__(self, op, kind, number=None, boolean=None, text=None):
        self.op, self.kind, self.number, self.boolean, self.text = op, kind, number, boolean, text
        self.fold = text.casefold() if text is not None else None
        self.regex = _c_pattern(text) if text is not None and ("*" in text or "?" in text) else None


def _c_parse(c):
    """The criterion value `c` (a number, text, TRUE/FALSE, a date or BLANK) read by the rules above."""
    if isinstance(c, str) and not c:
        return _Crit("", "blank_text")
    if c is None or c is BLANK:
        return _Crit("", "number", number=_Decimal(0))          # an empty criterion cell counts as 0
    if isinstance(c, bool):
        return _Crit("", "bool", boolean=c)
    if not isinstance(c, str):
        n = _c_cell_number(c)
        return _Crit("", "number", number=n) if n is not None else _Crit("", "text", text=str(c))
    op, operand = "", c
    for o in _C_OPERATORS:
        if c.startswith(o):
            op, operand = o, c[len(o):]
            break
    if operand == "":
        return _Crit(op, "empty")
    n = _c_text_number(operand, side="criterion")
    if n is not None:
        return _Crit(op, "number", number=n)
    if operand.casefold() in ("true", "false"):
        return _Crit(op, "bool", boolean=operand.casefold() == "true")
    return _Crit(op, "text", text=operand)


def _c_facts(v):
    """(kind, number, value) of a cell value for the rules: kind e (empty), b (TRUE/FALSE), n (a number or a date),
    t (text; number: the number the text reads as, or None) or x (an error value)."""
    if v is BLANK or v is None:
        return "e", None, None
    if isinstance(v, bool):
        return "b", None, v
    if isinstance(v, str):
        return "t", _c_text_number(v, side="value"), v
    n = _c_cell_number(v)
    return ("n", n, v) if n is not None else ("x", None, v)


_C_EMPTY = ("e", None, None)


def _c_text_equal(c, text):
    if c.regex is None:
        return c.fold == text.casefold()
    return c.regex.fullmatch(text) is not None


def _c_match(c, f):
    """Whether criterion `c` (from _c_parse) counts the cell whose facts are `f` (from _c_facts)."""
    kind, num, val = f
    if c.kind == "blank_text":
        return kind == "e" or (kind == "t" and val == "")
    if c.op in ("", "="):
        if c.kind == "empty":
            return kind == "e"
        if c.kind == "number":
            return kind in ("n", "t") and num is not None and num == c.number
        if c.kind == "bool":
            return kind == "b" and val is c.boolean
        return kind == "t" and _c_text_equal(c, val)
    if c.op == "<>":
        if c.kind == "empty":
            return kind != "e"
        if c.kind == "number":
            return not (kind == "n" and num == c.number)
        if c.kind == "bool":
            return not (kind == "b" and val is c.boolean)
        return not (kind == "t" and _c_text_equal(c, val))
    if c.kind == "number" and kind == "n":
        a, b = num, c.number
    elif c.kind == "text" and kind == "t":
        a, b = val.casefold(), c.fold
    else:
        return False
    return {"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[c.op]


def excel_criteria_match(criterion, value):
    """Whether Excel's conditional functions count the cell value `value` (BLANK or None = an empty cell) for the
    criterion `criterion`, by the rules above."""
    return _c_match(_c_parse(criterion), _c_facts(value))


def _criterion_test(c):
    """A test of one cell value for one criterion (the rules above)."""
    crit = _c_parse(_val(c))
    return lambda v: _c_match(crit, _c_facts(v))


def _c_key(c):
    """The one index key of an equality criterion, or None when the criterion needs a test of each cell (an
    operator other than =, a wildcard, or the empty text)."""
    if c.op not in ("", "="):
        return None
    if c.kind == "empty":
        return ("e",)
    if c.kind == "number":
        return "n", c.number
    if c.kind == "bool":
        return "b", c.boolean
    if c.kind == "text" and c.regex is None:
        return "t", c.fold
    return None


def _c_cell_keys(f):
    """The index keys a cell meets: a text that reads as a number meets the number's key too."""
    kind, num, val = f
    if kind == "e":
        return (("e",),)
    if kind == "b":
        return (("b", val),)
    if kind == "n":
        return (("n", num),)
    if kind == "t":
        return (("t", val.casefold()),) + ((("n", num),) if num is not None else ())
    return ()


def _range_facts(rng):
    entry = _FACTS_CACHE.get(id(rng))
    if entry is None or entry[0] is not rng:
        entry = (rng, [_c_facts(v) for v in rng.flat()])
        _FACTS_CACHE[id(rng)] = entry                # the range is kept so its id stays unique
    return entry[1]


def _same_shape(a, b):
    if not (isinstance(a, Rng) and isinstance(b, Rng)) or a.height != b.height or a.width != b.width:
        raise XLErr("#VALUE!")


def _positions(ranges, criteria):
    """Positions (in the flattened ranges) where every criterion holds."""
    if not ranges:
        raise XLErr("#VALUE!")
    for r in ranges[1:]:
        _same_shape(ranges[0], r)
    crits = [_c_parse(_val(c)) for c in criteria]
    facts = [_range_facts(r) for r in ranges]
    n = max(len(f) for f in facts)
    keys = [_c_key(c) for c in crits]
    if all(k is not None for k in keys):
        cache_key = tuple(id(r) for r in ranges)
        entry = _INDEX_CACHE.get(cache_key)
        if entry is None:
            index = {}
            for p in range(n):
                per = [_c_cell_keys(f[p] if p < len(f) else _C_EMPTY) for f in facts]
                for combo in _itertools.product(*per):
                    index.setdefault(combo, []).append(p)
            entry = (ranges, index)          # the ranges are kept so their ids stay unique
            _INDEX_CACHE[cache_key] = entry
        return list(entry[1].get(tuple(keys), ()))
    return [p for p in range(n)
            if all(_c_match(c, f[p] if p < len(f) else _C_EMPTY) for c, f in zip(crits, facts))]


def _pairs(pairs):
    if not pairs or len(pairs) % 2:
        raise XLErr("#VALUE!")
    return list(pairs[0::2]), list(pairs[1::2])


def _picked_numbers(rng, pos):
    vals = rng.flat()
    for p in pos:
        v = vals[p] if p < len(vals) else BLANK
        if isinstance(v, (XLErr, NotReproduced)):
            raise v
        if isinstance(v, bool) or not isinstance(v, (int, float, _dt.date)):
            continue
        yield date_serial(v) if isinstance(v, _dt.date) else v


def SUMIFS(sum_range, *pairs):
    ranges, criteria = _pairs(pairs)
    _same_shape(sum_range, ranges[0])
    return sum(_picked_numbers(sum_range, _positions(ranges, criteria)))


def COUNTIFS(*pairs):
    ranges, criteria = _pairs(pairs)
    return len(_positions(ranges, criteria))


def AVERAGEIFS(avg_range, *pairs):
    ranges, criteria = _pairs(pairs)
    _same_shape(avg_range, ranges[0])
    nums = list(_picked_numbers(avg_range, _positions(ranges, criteria)))
    if not nums:
        raise XLErr("#DIV/0!")
    return sum(nums) / len(nums)


def MAXIFS(max_range, *pairs):
    ranges, criteria = _pairs(pairs)
    _same_shape(max_range, ranges[0])
    nums = list(_picked_numbers(max_range, _positions(ranges, criteria)))
    return max(nums) if nums else 0


def MINIFS(min_range, *pairs):
    ranges, criteria = _pairs(pairs)
    _same_shape(min_range, ranges[0])
    nums = list(_picked_numbers(min_range, _positions(ranges, criteria)))
    return min(nums) if nums else 0


def SUMIF(rng, criterion, sum_range=_OMIT):
    return SUMIFS(rng if sum_range is _OMIT else sum_range, rng, criterion)


def COUNTIF(rng, criterion):
    return COUNTIFS(rng, criterion)


def AVERAGEIF(rng, criterion, avg_range=_OMIT):
    return AVERAGEIFS(rng if avg_range is _OMIT else avg_range, rng, criterion)


# ------------------------------------------------------------------ lookup
def _vector(x):
    return x.vector() if isinstance(x, Rng) else [_val(x)]


def _exact_position(x, vec, wildcards=True):
    x = _val(x)
    if x is BLANK:
        raise XLErr("#N/A")
    if wildcards and isinstance(x, str) and any(ch in x for ch in "*?~"):
        pattern = _wildcard(x)
        for p, v in enumerate(vec):
            if isinstance(v, str) and pattern.fullmatch(v):
                return p
        raise XLErr("#N/A")
    key = _cell_key(x)
    for p, v in enumerate(vec):
        if _cell_key(v) == key:
            return p
    raise XLErr("#N/A")


def _sorted_position(x, vec, descending=False):
    """Approximate match on sorted values: the last value at or below x (ascending) or at or above x
    (descending). Excel searches by halves; on sorted values this gives the same position."""
    x = _val(x)
    best = None
    for p, v in enumerate(vec):
        if v is BLANK or v is None or isinstance(v, (XLErr, NotReproduced)):
            continue
        if _rank(v)[0] != _rank(x)[0]:
            continue
        c = _cmp(v, x)
        if (c <= 0 and not descending) or (c >= 0 and descending):
            best = p
            if c == 0:
                continue
        else:
            break
    if best is None:
        raise XLErr("#N/A")
    return best


def MATCH(x, rng, match_type=1):
    vec = _vector(rng)
    kind = _num(match_type)
    if kind == 0:
        return _exact_position(x, vec) + 1
    return _sorted_position(x, vec, descending=kind < 0) + 1


def INDEX(rng, row, col=_OMIT):
    if not isinstance(rng, Rng):
        rng = Rng([[_val(rng)]])
    r = int(_num(row))
    c = None if col is _OMIT or col is BLANK else int(_num(col))
    if c is None:
        if len(rng.rows) == 1 and not rng.tall:
            return rng.cell(1, r) if r else rng
        if rng.width == 1:
            return rng.cell(r, 1) if r else rng
        if r == 0:
            raise XLErr("#VALUE!")
        return Rng.row(rng.rows[r - 1]) if r <= len(rng.rows) else Rng.row([BLANK] * rng.width)
    if r == 0 and c == 0:
        return rng
    if r == 0:
        if c < 1 or c > rng.width:
            raise XLErr("#REF!")
        return Rng.column([row_[c - 1] for row_ in rng.rows])
    if c == 0:
        if r < 1 or r > rng.height:
            raise XLErr("#REF!")
        return Rng.row(rng.rows[r - 1])
    return rng.cell(r, c)


def _pick_result(ret, p, along_rows):
    if not isinstance(ret, Rng):
        raise XLErr("#VALUE!")
    if along_rows:
        if p >= len(ret.rows):
            return BLANK
        row_ = ret.rows[p]
        return row_[0] if len(row_) == 1 else Rng.row(row_)
    col_ = [r[p] for r in ret.rows]
    return col_[0] if len(col_) == 1 else Rng.column(col_)


def XLOOKUP(x, lookup, ret, if_not_found=_OMIT, match_mode=0, search_mode=1):
    """Match modes 0 (exact), 2 (wildcards), -1 (exact or next smaller), 1 (exact or next larger); search modes
    1 (first to last) and -1 (last to first); binary search modes 2 and -2 are searched in order."""
    vec = _vector(lookup)
    along_rows = not (isinstance(lookup, Rng) and len(lookup.rows) == 1 and lookup.width > 1)
    mode = int(_num(match_mode))
    order = list(range(len(vec)))
    if int(_num(search_mode)) < 0:
        order.reverse()
    xv = _val(x)
    found = None
    if mode in (0, 2):
        key = _cell_key(xv)
        pattern = _wildcard(xv) if mode == 2 and isinstance(xv, str) else None
        for p in order:
            v = vec[p]
            if (pattern is not None and isinstance(v, str) and pattern.fullmatch(v)) or \
                    (pattern is None and _cell_key(v) == key):
                found = p
                break
    elif mode in (-1, 1):
        best = None
        for p in order:
            v = vec[p]
            if v is BLANK or v is None or _rank(v)[0] != _rank(xv)[0]:
                continue
            c = _cmp(v, xv)
            if c == 0:
                best = p
                break
            if (mode == -1 and c < 0 and (best is None or _cmp(v, vec[best]) > 0)) or \
                    (mode == 1 and c > 0 and (best is None or _cmp(v, vec[best]) < 0)):
                best = p
        found = best
    else:
        raise XLErr("#VALUE!")
    if found is None:
        if if_not_found is _OMIT:
            raise XLErr("#N/A")
        return if_not_found
    return _pick_result(ret, found, along_rows)


def XMATCH(x, lookup, match_mode=0, search_mode=1):
    vec = _vector(lookup)
    mode = int(_num(match_mode))
    if mode not in (0, 2):
        if mode == -1:
            return _sorted_position(x, vec) + 1
        raise NotReproduced("XMATCH with match mode 1 is not reproduced")
    order = list(range(len(vec)))
    if int(_num(search_mode)) < 0:
        order.reverse()
    xv = _val(x)
    key = _cell_key(xv)
    pattern = _wildcard(xv) if mode == 2 and isinstance(xv, str) else None
    for p in order:
        v = vec[p]
        if (pattern is not None and isinstance(v, str) and pattern.fullmatch(v)) or \
                (pattern is None and _cell_key(v) == key):
            return p + 1
    raise XLErr("#N/A")


def VLOOKUP(x, table, col, approximate=True):
    k = int(_num(col))
    if k < 1 or k > table.width:
        raise XLErr("#REF!" if k > table.width else "#VALUE!")
    first = [r[0] for r in table.rows]
    p = _sorted_position(x, first) if truthy(approximate) else _exact_position(x, first)
    return table.rows[p][k - 1]


def HLOOKUP(x, table, row, approximate=True):
    k = int(_num(row))
    if k < 1 or k > len(table.rows):
        raise XLErr("#REF!" if k > len(table.rows) else "#VALUE!")
    first = list(table.rows[0])
    p = _sorted_position(x, first) if truthy(approximate) else _exact_position(x, first)
    return table.rows[k - 1][p]


def CHOOSE(index, *options):
    k = int(_num(index))
    if k < 1 or k > len(options):
        raise XLErr("#VALUE!")
    return options[k - 1]()


def IFS(*pairs):
    """IFS(condition1, value1, ...): the value of the first true condition; #N/A when none is true."""
    if not pairs or len(pairs) % 2:
        raise XLErr("#VALUE!")
    for k in range(0, len(pairs), 2):
        if truthy(pairs[k]()):
            return pairs[k + 1]()
    raise XLErr("#N/A")


def SWITCH(expression, *cases):
    """SWITCH(value, match1, result1, ..., [default]): exact match without regard to case; #N/A without default."""
    v = _val(expression)
    key = _cell_key(v)
    for k in range(0, len(cases) - 1, 2):
        if _cell_key(_val(cases[k]())) == key:
            return cases[k + 1]()
    if len(cases) % 2:
        return cases[-1]()
    raise XLErr("#N/A")


# ------------------------------------------------------------------ logic and information
def _logicals(args):
    out = []
    for a in args:
        if isinstance(a, Rng):
            for v in a.flat():
                if isinstance(v, (XLErr, NotReproduced)):
                    raise v
                if isinstance(v, bool):
                    out.append(v)
                elif isinstance(v, (int, float)):
                    out.append(v != 0)
        elif a is not _OMIT:
            out.append(truthy(a))
    if not out:
        raise XLErr("#VALUE!")
    return out


def AND(*args):
    return all(_logicals(args))


def OR(*args):
    return any(_logicals(args))


def XOR(*args):
    return sum(_logicals(args)) % 2 == 1


def NOT(x):
    return not truthy(x)


def TRUE():
    return True


def FALSE():
    return False


def NA():
    raise XLErr("#N/A")


_CAUGHT = (XLErr, ZeroDivisionError, TypeError, ValueError, OverflowError)


def _outcome(fn):
    """(error code or None, value) of a lazily given argument."""
    try:
        v = fn()
    except XLErr as e:
        return e.code, None
    except ZeroDivisionError:
        return "#DIV/0!", None
    except (TypeError, ValueError, OverflowError):
        return "#VALUE!", None
    if isinstance(v, Rng) and len(v.rows) == 1 and len(v.rows[0]) == 1:
        v = v.rows[0][0]
    if isinstance(v, XLErr):
        return v.code, None
    if isinstance(v, NotReproduced):
        raise v
    return None, v


def IFERROR(value, fallback):
    code, v = _outcome(value)
    return fallback() if code else v


def IFNA(value, fallback):
    code, v = _outcome(value)
    if code == "#N/A":
        return fallback()
    if code:
        raise XLErr(code)
    return v


def ISERROR(value):
    return _outcome(value)[0] is not None


def ISERR(value):
    code = _outcome(value)[0]
    return code is not None and code != "#N/A"


def ISNA(value):
    return _outcome(value)[0] == "#N/A"


def _plain(value):
    code, v = _outcome(value)
    return None if code else v


def ISBLANK(value):
    v = _plain(value)
    return v is BLANK or v is None


def ISNUMBER(value):
    v = _plain(value)
    return isinstance(v, (int, float, _dt.date)) and not isinstance(v, bool)


def ISTEXT(value):
    return isinstance(_plain(value), str)


def ISNONTEXT(value):
    return not isinstance(_plain(value), str)


def ISLOGICAL(value):
    return isinstance(_plain(value), bool)


def N(x):
    v = _val(x)
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, _dt.date):
        return date_serial(v)
    return 0


# ------------------------------------------------------------------ math
def _decimal_round(x, digits, mode):
    """Round half away from zero (ROUND), away from zero (ROUNDUP) or toward zero (ROUNDDOWN), on the decimal
    digits the number shows (2.675 rounds to 2.68, as in Excel)."""
    v = _num(x)
    k = int(_num(digits))
    q = _Decimal(1).scaleb(-k)
    out = float(_Decimal(repr(float(v))).quantize(q, rounding=mode))
    return int(out) if k <= 0 and out == int(out) and abs(out) < 1e15 else out


def ROUND(x, digits=0):
    return _decimal_round(x, digits, _ROUND_HALF_UP)


def ROUNDUP(x, digits=0):
    return _decimal_round(x, digits, _ROUND_UP)


def ROUNDDOWN(x, digits=0):
    return _decimal_round(x, digits, _ROUND_DOWN)


def INT(x):
    return int(_math.floor(_num(x)))


def TRUNC(x, digits=0):
    return _decimal_round(x, digits, _ROUND_DOWN)


def ABS(x):
    return abs(_num(x))


def SIGN(x):
    v = _num(x)
    return (v > 0) - (v < 0)


def MOD(a, b):
    x, y = _num(a), _num(b)
    if y == 0:
        raise XLErr("#DIV/0!")
    return x - y * _math.floor(x / y)


def QUOTIENT(a, b):
    x, y = _num(a), _num(b)
    if y == 0:
        raise XLErr("#DIV/0!")
    return int(x / y)


def SQRT(x):
    v = _num(x)
    if v < 0:
        raise XLErr("#NUM!")
    return _math.sqrt(v)


def POWER(a, b):
    return POW(a, b)


def EXP(x):
    return _math.exp(_num(x))


def LN(x):
    v = _num(x)
    if v <= 0:
        raise XLErr("#NUM!")
    return _math.log(v)


def LOG10(x):
    v = _num(x)
    if v <= 0:
        raise XLErr("#NUM!")
    return _math.log10(v)


def LOG(x, base=10):
    v, b = _num(x), _num(base)
    if v <= 0 or b <= 0 or b == 1:
        raise XLErr("#NUM!" if b != 1 else "#DIV/0!")
    return _math.log(v, b)


def PI():
    return _math.pi


# ------------------------------------------------------------------ dates
def DATE(year, month, day):
    y, m, d = int(_num(year)), int(_num(month)), int(_num(day))
    if 0 <= y < 1900:
        y += 1900
    return _ymd_serial(y, m, d)


def EOMONTH(start, months):
    s = _num(start)
    if s < 0:
        raise XLErr("#NUM!")
    y, m, _d = serial_ymd(s)
    return _ymd_serial(y, m + int(_num(months)) + 1, 1) - 1


def EDATE(start, months):
    s = _num(start)
    if s < 0:
        raise XLErr("#NUM!")
    y, m, d = serial_ymd(s)
    y2, m0 = divmod(y * 12 + (m - 1) + int(_num(months)), 12)
    nxt = _dt.date(y2 + (m0 == 11), 1 if m0 == 11 else m0 + 2, 1)
    last = (nxt - _dt.timedelta(days=1)).day
    return _ymd_serial(y2, m0 + 1, min(d, last))


def YEAR(s):
    return serial_ymd(_num(s))[0]


def MONTH(s):
    return serial_ymd(_num(s))[1]


def DAY(s):
    return serial_ymd(_num(s))[2]


def DAYS(end, start):
    return int(_math.floor(_num(end))) - int(_math.floor(_num(start)))


def WEEKDAY(s, kind=1):
    n = int(_math.floor(_num(s)))
    k = int(_num(kind))
    # Serial 1 (1900-01-01) is a Sunday in Excel's count; from 1900-03-01 Excel's count matches the calendar.
    dow = (n + 6) % 7 if n >= 61 else (n - 1) % 7        # 0 = Sunday
    if k == 1:
        return dow + 1
    if k == 2:
        return (dow + 6) % 7 + 1
    if k == 3:
        return (dow + 6) % 7
    raise XLErr("#NUM!")


# ------------------------------------------------------------------ financial (v3, A-aera-13)
# Excel's annuity functions, with the rules measured in Excel 16 on 2026-11-25 (scripts/tests/test_v3_lift.py):
# - type (payment timing): 0 or omitted pays at the end of each period; any other number (1, 2, -1, 0.5, TRUE)
#   pays at the start, as 1 does.
# - (1 + rate) ^ nper follows the ^ operator (POW): 0 ^ 0 is #NUM!, a negative base with a fractional nper #NUM!.
# - PMT: nper 0 and a rate of -1 or less give #NUM!. PV: (1 + rate) ^ nper = 0 gives #DIV/0!.
# - rate 0: PMT = -(pv + fv) / nper, PV = -(fv + pmt * nper), FV = -(pv + pmt * nper); type has no effect.
def _due(timing):
    return 1 if _num(timing) != 0 else 0


def _plus_zero(x):
    return x + 0.0 if isinstance(x, float) else x       # -0.0 becomes 0.0, as Excel shows no negative zero


def PMT(rate, nper, pv, fv=0, timing=0):
    r, n, p, f, due = _num(rate), _num(nper), _num(pv), _num(fv), _due(timing)
    if n == 0 or r <= -1:
        raise XLErr("#NUM!")
    if r == 0:
        return _plus_zero(-(p + f) / n)
    g = POW(1 + r, n)
    return _plus_zero(-r * (p * g + f) / ((1 + r * due) * (g - 1)))


def PV(rate, nper, pmt, fv=0, timing=0):
    r, n, m, f, due = _num(rate), _num(nper), _num(pmt), _num(fv), _due(timing)
    if r == 0:
        return _plus_zero(-(f + m * n))
    g = POW(1 + r, n)
    if g == 0:
        raise XLErr("#DIV/0!")
    return _plus_zero(-(f + m * (1 + r * due) * (g - 1) / r) / g)


def FV(rate, nper, pmt, pv=0, timing=0):
    r, n, m, p, due = _num(rate), _num(nper), _num(pmt), _num(pv), _due(timing)
    if r == 0:
        return _plus_zero(-(p + m * n))
    g = POW(1 + r, n)
    return _plus_zero(-(p * g + m * (1 + r * due) * (g - 1) / r))


def NPV(rate, *values):
    """Each value discounted by (1 + rate) ^ k, k = 1, 2, ... in the order given. The values counted are those
    SUM counts (_numbers): in a range, an array or a value reached through a reference (as_ref, v5) only numbers,
    so text, TRUE/FALSE and empty cells take no period; a value given directly counts when it is a number,
    TRUE/FALSE or number text, and other typed text such as "x" gives #VALUE!. A rate of -1 gives #DIV/0!."""
    r = _num(rate)
    out, k = 0, 0
    for v in _numbers(values):
        k += 1
        g = POW(1 + r, k)
        if g == 0:
            raise XLErr("#DIV/0!")
        out += v / g
    return _plus_zero(out)


# ------------------------------------------------------------------ text
def LEFT(text, n=1):
    k = int(_num(n))
    if k < 0:
        raise XLErr("#VALUE!")
    return _text(text)[:k]


def RIGHT(text, n=1):
    k = int(_num(n))
    if k < 0:
        raise XLErr("#VALUE!")
    t = _text(text)
    return t[-k:] if k else ""


def MID(text, start, n):
    s, k = int(_num(start)), int(_num(n))
    if s < 1 or k < 0:
        raise XLErr("#VALUE!")
    return _text(text)[s - 1:s - 1 + k]


def LEN(text):
    return len(_text(text))


def TRIM(text):
    return _re.sub(" +", " ", _text(text)).strip(" ")


def UPPER(text):
    return _text(text).upper()


def LOWER(text):
    return _text(text).lower()


# v7, A-aera-12: PROPER as Excel 16 gives it. Measured on the build PC on 2026-11-26 (Excel 16.0, Windows locale
# en-US) for every code point from U+0001 to U+3FFFF, from U+E0000 to U+F0FFF and from U+10F000 to U+10FFFF, each
# as PROPER(c), PROPER("a"&c) and PROPER(c&"a"), and on 6,032 texts that mix French, German, Korean, CJK, Greek,
# Cyrillic, digits and apostrophes (v7 build notes, P7). Excel's rule: a letter that starts the text or follows a
# character that is not a letter is written in upper case, every other letter in lower case, and any other
# character stays as it is. A word is a run of letters by Unicode: below U+10000 a letter is a character for which
# str.isalpha() is true; its case is Python's single-character mapping, and a character whose mapping has two or
# more characters (for example the upper case of the German sharp s) stays as it is. From U+10000 on (two UTF-16
# units in Excel) no character is a letter and none changes. This gives Excel's three results for every measured
# code point except 2,239 (for example the micro sign U+00B5, the dotted capital I U+0130, and the Greek capital
# sigma U+03A3, whose lower case Excel writes as the final sigma at the end of a word). Those, and the code points
# that were not measured (U+0000, the surrogates, the noncharacters, the planes 4 to 13, and the planes 15 and 16
# outside their first and last 4,096 code points), are listed below: PROPER of a text that holds one of them is not
# reproduced. The case mapping is that of the Python that runs the script (Unicode 16.0 in Python 3.14).
_PROPER_NOT_REPRODUCED = (
    "0 B5 130 17F 19B 23F-240 250-252 25C 261 264-266 26A 26C 271 282 287 29D-29E 2B9-2BA 2C6-2CF 2EC "
    "370-374 376-377 37B-37D 37F 390 3A3 3B0 3CF 3D2-3D4 3D7-3D9 3F2-3F5 3F7-3FB 3FD-3FF 514-52F 560 588 "
    "5EF 620 800-815 81A 824 828 840-858 860-86A 870-887 889-88E 8A0-8C9 973-97A 980 9FC AF9 C34 C5A C5D "
    "C80 CDD CF1-CF2 D04 D29 D3A D4E D54-D56 D5F E31 E34-E3A E47-E4E E86 E89 E8C E8E-E93 E98 EA0 EA8-EA9 "
    "EAC EDE-EDF F8C 10A0-10C5 10C7 10CD 10D0-10FA 10FD-10FF 115A-115E 11A3-11A7 11FA-11FF 13A0-13F5 "
    "13F8-13FD 1677-167F 16EE-16F8 170D 171F 1878 1885-1886 18B0-18F5 191D-191E 19AA-19AB 19B0-19C0 "
    "19C8-19C9 1A20-1A54 1AA7 1B4C 1BBA-1BE5 1C80-1C8A 1C90-1CBA 1CBD-1CBF 1CE9-1CEC 1CEE-1CF3 1CF5-1CF6 "
    "1CFA 1D79 1D8E 1E9B 1E9E 1EFA-1F15 1F18-1F1D 1F20-1F45 1F48-1F4D 1F51 1F53 1F55 1F57 1F59 1F5B 1F5D "
    "1F5F-1F7D 1F88-1F8F 1F98-1F9F 1FA8-1FB1 1FB8-1FBC 1FBE 1FC8-1FCC 1FD0-1FD1 1FD8-1FDB 1FE0-1FE1 1FE5 "
    "1FE8-1FEC 1FF8-1FFC 2095-209C 2126 212A-212B 2160-2182 2185-2188 24B6-24CF 2C00-2C5F 2C6D-2C70 "
    "2C72-2C73 2C7E-2CE3 2CEB-2CEE 2CF2-2CF3 2D00-2D25 2D27 2D2D 2D66-2D67 2E2F 3007 3021-3029 3038-303A "
    "312E-312F 31B8-31BF 4DB6-4DBF 9FC4-9FFF A4D0-A4FD A640-A66D A67F-A69D A6A0-A6E5 A717-A71F A722-A72F "
    "A732-A76F A779-A788 A78B-A7CD A7D0-A7D1 A7D3 A7D5-A7DC A7F2-A7FA A8F2-A8F7 A8FB A8FD-A8FE A960-A97C "
    "A984-A9B2 A9CF A9E0-A9E4 A9E6-A9EF A9FA-A9FE AA60-AA76 AA7A AA7E-AAAF AAB1 AAB5-AAB6 AAB9-AABD AAC0 "
    "AAC2 AADB-AADD AAE0-AAEA AAF2-AAF4 AB01-AB06 AB09-AB0E AB11-AB16 AB20-AB26 AB28-AB2E AB30-AB5A "
    "AB5C-AB69 AB70-ABE2 D7B0-D7C6 D7CB-D7FB D800-DFFF FA2E-FA2F FA6B-FA6D FDD0-FDEF FFFE-FFFF "
    "1FFFE-1FFFF 2FFFE-2FFFF 3FFFE-DFFFF EFFFE-EFFFF F1000-10EFFF 10FFFE-10FFFF ")


def _code_ranges(text):
    """([first code point, ...], [last code point, ...]) of a list of hexadecimal code points and ranges."""
    starts, ends = [], []
    for part in text.split():
        a, _sep, b = part.partition("-")
        starts.append(int(a, 16))
        ends.append(int(b or a, 16))
    return starts, ends


_PROPER_STARTS, _PROPER_ENDS = _code_ranges(_PROPER_NOT_REPRODUCED)


def PROPER(text):
    out = []
    after_letter = False
    for ch in _text(text):
        n = ord(ch)
        k = _bisect.bisect_right(_PROPER_STARTS, n) - 1
        if k >= 0 and n <= _PROPER_ENDS[k]:
            raise NotReproduced(f"PROPER of a text with the character U+{n:04X} is not reproduced: Excel 16 gives "
                                "another case for it than this script's rule, or it was not measured "
                                "(_PROPER_NOT_REPRODUCED in this script)")
        if n < 0x10000 and ch.isalpha():
            mapped = ch.lower() if after_letter else ch.upper()
            out.append(mapped if len(mapped) == 1 else ch)
            after_letter = True
        else:
            out.append(ch)
            after_letter = False
    return "".join(out)


def EXACT(a, b):
    return _text(a) == _text(b)


def REPT(text, n):
    k = int(_num(n))
    if k < 0:
        raise XLErr("#VALUE!")
    return _text(text) * k


def SUBSTITUTE(text, old, new, instance=_OMIT):
    t, o, n = _text(text), _text(old), _text(new)
    if o == "":
        return t
    if instance is _OMIT:
        return t.replace(o, n)
    k = int(_num(instance))
    if k < 1:
        raise XLErr("#VALUE!")
    pos = -1
    for _ in range(k):
        pos = t.find(o, pos + 1)
        if pos < 0:
            return t
    return t[:pos] + n + t[pos + len(o):]


def FIND(find, within, start=1):
    s = int(_num(start))
    t = _text(within)
    if s < 1 or s > len(t) + 1:
        raise XLErr("#VALUE!")
    p = t.find(_text(find), s - 1)
    if p < 0:
        raise XLErr("#VALUE!")
    return p + 1


def SEARCH(find, within, start=1):
    s = int(_num(start))
    t = _text(within)
    if s < 1 or s > len(t) + 1:
        raise XLErr("#VALUE!")
    m = _wildcard(_text(find)).search(t, s - 1)
    if m is None:
        raise XLErr("#VALUE!")
    return m.start() + 1


def CONCATENATE(*args):
    return "".join(_text(a) for a in args)


def CONCAT(*args):
    out = []
    for a in args:
        if isinstance(a, Rng):
            out.extend(_text(v) for v in a.flat())
        else:
            out.append(_text(a))
    return "".join(out)


def VALUE(text):
    v = _val(text)
    if isinstance(v, bool):
        raise XLErr("#VALUE!")
    return _num(v)


def DATEVALUE(text):
    """The serial number of a date written as text (the forms parse_date_text reads, e.g. 2026-01-31); the time
    part is dropped. A number or a date that is not text gives #VALUE!, as in Excel."""
    v = _val(text)
    if not isinstance(v, str):
        raise XLErr("#VALUE!")
    serial = parse_date_text(v.strip())
    if serial is None:
        raise XLErr("#VALUE!")
    return int(serial)


def TIMEVALUE(text):
    """The time of day of a time written as text HH:MM[:SS] (or after a date), as a fraction of a day."""
    v = _val(text)
    if not isinstance(v, str):
        raise XLErr("#VALUE!")
    m = _re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*$", v.strip())
    if not m:
        raise XLErr("#VALUE!")
    h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    if h > 23 or mi > 59 or s > 59:
        raise XLErr("#VALUE!")
    return (h * 3600 + mi * 60 + s) / 86400


_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December")
_DAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")


def TEXT(value, format_text):
    """TEXT with the formats models use: dates (yyyy, yy, mmmm, mmm, mm, m, dddd, ddd, dd, d), 0, 0.0, #,##0,
    #,##0.00, 0%, 0.0%, General and @. Other formats are not reproduced."""
    fmt = _text(format_text)
    v = _val(value)
    if isinstance(v, str):
        try:
            v = _text_to_number(v)
        except XLErr:
            return v
    low = fmt.lower()
    if low == "@":
        return _text(v)
    if low == "general":
        return _general(_num(v))
    if _re.search(r"[yd]|mmm", low) or low in ("m", "mm", "m/d", "mm/dd"):
        y, mo, d = serial_ymd(_num(v))
        n = int(_math.floor(_num(v)))
        dow = (n + 6) % 7 if n >= 61 else (n - 1) % 7
        tokens = _re.findall(r'"[^"]*"|yyyy|yy|mmmm|mmm|mm|m|dddd|ddd|dd|d|.', fmt, flags=_re.IGNORECASE)
        out = []
        for t in tokens:
            tl = t.lower()
            if t.startswith('"'):
                out.append(t[1:-1])
            elif tl == "yyyy":
                out.append(f"{y:04d}")
            elif tl == "yy":
                out.append(f"{y % 100:02d}")
            elif tl == "mmmm":
                out.append(_MONTHS[mo - 1])
            elif tl == "mmm":
                out.append(_MONTHS[mo - 1][:3])
            elif tl == "mm":
                out.append(f"{mo:02d}")
            elif tl == "m":
                out.append(str(mo))
            elif tl == "dddd":
                out.append(_DAYS[dow])
            elif tl == "ddd":
                out.append(_DAYS[dow][:3])
            elif tl == "dd":
                out.append(f"{d:02d}")
            elif tl == "d":
                out.append(str(d))
            else:
                out.append(t)
        return "".join(out)
    m = _re.fullmatch(r"(#,##)?0(\.(0+))?(%)?", fmt)
    if m:
        x = _num(v) * (100 if m.group(4) else 1)
        places = len(m.group(3) or "")
        q = _Decimal(1).scaleb(-places)
        rounded = _Decimal(repr(float(x))).quantize(q, rounding=_ROUND_HALF_UP)
        body = f"{rounded:,.{places}f}" if m.group(1) else f"{rounded:.{places}f}"
        return body + ("%" if m.group(4) else "")
    raise NotReproduced(f"TEXT format {fmt!r} is not reproduced by this script")


# ------------------------------------------------------------------ cells
def cell_value(fn, *args):
    """One cell: fn(*args) as the cell would hold it. An Excel error becomes the cell's value; an empty result
    is 0, as a formula that points at an empty cell shows 0."""
    try:
        v = fn(*args)
    except (XLErr, NotReproduced) as e:
        return e
    except ZeroDivisionError:
        return XLErr("#DIV/0!")
    except (TypeError, ValueError, OverflowError):
        return XLErr("#VALUE!")
    if isinstance(v, Rng):
        if len(v.rows) == 1 and len(v.rows[0]) == 1 and not v.tall:
            v = v.rows[0][0]
            if isinstance(v, (XLErr, NotReproduced)):
                return v
        else:
            return XLErr("#VALUE!")          # an array in one cell
    if v is BLANK or v is None:
        return 0
    if isinstance(v, float) and (_math.isnan(v) or _math.isinf(v)):
        return XLErr("#NUM!")
    if isinstance(v, _dt.date):
        return date_serial(v)
    return v


def parse_typed(text, col_type):
    """A CSV field as the raw sheet holds it, by contract type; an empty field is an empty cell (BLANK)."""
    if text is None or text == "":
        return BLANK
    if col_type == "text":
        return text
    t = text.strip()
    if col_type == "integer":
        try:
            return int(t)
        except ValueError:
            return float(t) if _NUMBER_TEXT.match(t) else text
    if col_type == "number":
        if _NUMBER_TEXT.match(t):
            return int(t) if _re.fullmatch(r"[+-]?\d+", t) else float(t)
        return text
    if col_type in ("date", "datetime"):
        d = parse_date_text(t)
        return text if d is None else d
    if col_type == "boolean":
        u = t.upper()
        if u in ("TRUE", "1", "YES", "Y"):
            return True
        if u in ("FALSE", "0", "NO", "N"):
            return False
        return text
    return text


def sheet_value(v):
    """A value read from the workbook, in the script's terms (dates as serials, empty as BLANK)."""
    if v is None:
        return BLANK
    if isinstance(v, (_dt.datetime, _dt.date)):
        return date_serial(v)
    if isinstance(v, _dt.time):
        return (v.hour * 3600 + v.minute * 60 + v.second) / 86400
    return v


def _a1(col, row):
    out = ""
    while col:
        col, rem = divmod(col - 1, 26)
        out = chr(65 + rem) + out
    return f"{out}{row}"


def _split_a1(ref):
    m = _re.match(r"^\$?([A-Za-z]{1,3})\$?(\d+)$", ref.strip())
    if not m:
        raise ValueError(f"not a cell: {ref}")
    n = 0
    for ch in m.group(1).upper():
        n = n * 26 + ord(ch) - 64
    return n, int(m.group(2))


def _bounds(ref):
    """(first col, first row, last col, last row) of 'D7:I10' or 'D7'."""
    parts = ref.split("!")[-1].split(":")
    c1, r1 = _split_a1(parts[0])
    c2, r2 = _split_a1(parts[-1])
    return min(c1, c2), min(r1, r2), max(c1, c2), max(r1, r2)


def _sheet_and_cell(ref):
    sheet, _, cell = ref.rpartition("!")
    sheet = sheet.strip()
    if sheet.startswith("'") and sheet.endswith("'"):
        sheet = sheet[1:-1].replace("''", "'")
    return sheet, cell


class _Cursor:
    """One helper row at a time: r["amount"] is the value of helper column amount in the current row."""

    __slots__ = ("cols", "i")

    def __init__(self, cols):
        self.cols = cols
        self.i = 0

    def __getitem__(self, name):
        col = self.cols.get(name)
        if col is None:
            raise NotReproduced(f"helper column '{name}' is not computed by this script")
        return col[self.i]


class HelperSheet:
    """A helper sheet h_<table>: row r belongs to raw row r (design 7.1)."""

    def __init__(self, model, table, sheet, n):
        self.model, self.table, self.sheet, self.n = model, table, sheet, n
        self.cols = {}
        self.order = []

    def add(self, name, values):
        self.cols[name] = values
        if name not in self.order:
            self.order.append(name)
        self.model._ranges.pop(("h", self.table, name), None)
        self.model.helper_step(self, name)

    def compute(self, name, fn):
        cur = _Cursor(self.cols)
        out = []
        for i in range(self.n):
            cur.i = i
            out.append(cell_value(fn, cur))
        self.add(name, out)

    def not_reproduced(self, name, reason):
        self.add(name, [NotReproduced(reason)] * self.n)


class Block:
    """A calculation block: body values by row and column, the totals, and its place in the workbook."""

    def __init__(self, model, bid, sheet, shape, row_labels, col_labels, row_keys=None, col_keys=None,
                 total_row=False, total_col=False, timeline=False):
        self.model, self.bid, self.sheet, self.shape = model, bid, sheet, shape
        self.row_labels, self.col_labels = list(row_labels), list(col_labels)
        self._row = {k: i for i, k in enumerate(row_keys or [])}
        self._col = {k: j for j, k in enumerate(col_keys or [])}
        self.values = [[None] * len(self.col_labels) for _ in self.row_labels]
        self.want_total_row, self.want_total_col, self.timeline = total_row, total_col, timeline
        self.total_row = None          # one value per column
        self.total_col = None          # one value per row
        self.grand = None
        self.measure_totals = {}       # measure id -> kind (sum | same)

    # reading
    def v(self, item, j):
        """Line item `item` in column j (items blocks)."""
        if j < 0 or j >= len(self.col_labels):
            raise XLErr("#REF!")
        if item not in self._row:
            raise NotReproduced(f"item '{item}' is not in block '{self.bid}'")
        return self.values[self._row[item]][j]

    def at(self, i, key):
        """Row i (or TOTAL) of measure `key` (measures blocks), or column index key (grids)."""
        j = self._col[key] if key in self._col else key
        if i == TOTAL:
            if self.total_row is None or self.total_row[j] is None:
                raise NotReproduced(f"block '{self.bid}' has no total row here")
            return self.total_row[j]
        return self.values[i][j]

    def row_range(self, i):
        if i == TOTAL:
            if self.total_row is None:
                return Rng.row([self.col_total(j) for j in range(len(self.col_labels))])
            return Rng.row(self.total_row)
        return Rng.row(self.values[i])

    def col_range(self, j):
        return Rng.column([r[j] for r in self.values])

    def col_total(self, j):
        if self.total_row is not None and self.total_row[j] is not None:
            return self.total_row[j]
        return cell_value(SUM, self.col_range(j))

    def grand_total(self):
        if self.grand is not None:
            return self.grand
        return cell_value(SUM, self.body())

    def named(self, name):
        """{blk:<block>.<name>}: in an items block, the row of line item `name` over the block's columns, as build
        writes it (v5, the form of entry A-aera-9); `total` of a block without an item called total, and every other
        block: the grand total."""
        if self.shape == "items" and (name != TOTAL or name in self._row):
            if name not in self._row:
                raise NotReproduced(f"item '{name}' is not in block '{self.bid}'")
            return Rng.row(self.values[self._row[name]])
        return self.grand_total()

    def body(self):
        return Rng(self.values)

    # filling
    def fill(self, fn):
        """Grid: fn(i, j, row label, column label) for every body cell."""
        for i, row in enumerate(self.row_labels):
            for j, col in enumerate(self.col_labels):
                self.values[i][j] = cell_value(fn, i, j, row, col)

    def fill_items(self, items):
        """Items: [(item id, fn(j, col), first-column fn or None)] in computation order, column by column."""
        for j, col in enumerate(self.col_labels):
            for item, fn, first in items:
                f = first if (j == 0 and first is not None) else fn
                self.values[self._row[item]][j] = cell_value(f, j, col)

    def fill_measures(self, measures):
        """Measures: [(measure id, fn(i, row), total kind)] in computation order, row by row; then the totals."""
        for i, row in enumerate(self.row_labels):
            for key, fn, _total in measures:
                self.values[i][self._col[key]] = cell_value(fn, i, row)
        if any(total in ("sum", "same") for _k, _f, total in measures):
            self.total_row = [None] * len(self.col_labels)
            for key, fn, total in measures:
                j = self._col[key]
                if total == "sum":
                    self.total_row[j] = cell_value(SUM, self.col_range(j))
                elif total == "same":
                    self.total_row[j] = cell_value(fn, TOTAL, "Total")
                if total in ("sum", "same"):
                    self.measure_totals[key] = total

    def not_reproduced(self, reason):
        for i in range(len(self.row_labels)):
            for j in range(len(self.col_labels)):
                self.values[i][j] = NotReproduced(reason)

    def finish(self):
        """Grid totals (a SUM of the column above, of the row to the left, and of the body), then the cells."""
        if self.shape == "grid":
            if self.want_total_row:
                self.total_row = [cell_value(SUM, self.col_range(j)) for j in range(len(self.col_labels))]
            if self.want_total_col:
                self.total_col = [cell_value(SUM, Rng.row(r)) for r in self.values]
            if self.want_total_row and self.want_total_col:
                self.grand = cell_value(SUM, self.body())
        self.model.block_step(self)

    def frame(self):
        """The block as a pandas DataFrame (needs pandas)."""
        import pandas as pd
        cols = [_label(c) for c in self.col_labels]
        rows = [_label(r) for r in self.row_labels]
        data = [[_plain_value(v) for v in r] for r in self.values]
        return pd.DataFrame(data, index=rows, columns=cols)


def _label(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool) and 20000 < v < 80000 and v == int(v):
        y, m, d = serial_ymd(v)
        return f"{y:04d}-{m:02d}-{d:02d}"
    return v


def _plain_value(v):
    if isinstance(v, XLErr):
        return v.code
    if isinstance(v, NotReproduced):
        return "not reproduced"
    if v is BLANK:
        return None
    return v


class Model:
    """The model computed outside the workbook. INFO comes from the specification (written by lift); the
    layout (where each value sits in the workbook) comes from the manifest when the script runs."""

    def __init__(self, info, layout):
        self.info, self.layout = info, layout
        self.inputs = {k: dict(v) for k, v in info["inputs"].items()}
        self.raw = {}              # table -> {"header": [...], "rows": [[...]], "source": text}
        self.h = {}                # table -> HelperSheet
        self.blocks = {}           # block id -> Block
        self.checks = {}           # check id -> {"kind", "severity", "cells": {"D": v, ...}, "result": v}
        self.master = None
        self.steps = []            # {"name", "kind", "sheet", "cells": [(row, col, value)], "problem"}
        self.notes = []
        self._ranges = {}
        self._placed = {}          # (sheet, row, col) -> value of every block and check cell computed
        _INDEX_CACHE.clear()
        _FACTS_CACHE.clear()

    # inputs
    def inp(self, iid):
        item = self.inputs.get(iid)
        if item is None:
            raise NotReproduced(f"input '{iid}' is not known to this script")
        if item["kind"] == "scalar":
            return item["value"]
        if item["kind"] == "list":
            return Rng.column(item["values"])
        return Rng([list(r) for r in item["values"]])

    def inp_rows(self, iid):
        return Rng.column(self.inputs[iid]["rows"])

    def inp_cols(self, iid):
        return Rng.row(self.inputs[iid]["cols"])

    def list_values(self, iid):
        return list(self.inputs[iid]["values"])

    def timeline(self, start_id, periods):
        """Column headers =EOMONTH(start,0), then =EOMONTH(<left>,1) (design 6.5)."""
        out = [cell_value(EOMONTH, self.inp(start_id), 0)]
        for _ in range(int(periods) - 1):
            out.append(cell_value(EOMONTH, out[-1], 1))
        return out

    # raw tables
    def load_raw(self, model_dir, source, workbook_values=None):
        for table, t in self.info["tables"].items():
            if source == "workbook":
                sheet = self.layout["raw"].get(table, {}).get("sheet", f"raw_{table}")
                cells = (workbook_values or {}).get(sheet)
                if cells is None:
                    raise ScriptStop(3, f"the workbook has no sheet {sheet} to read raw table '{table}' from")
                header, rows = _sheet_table(cells)
                rows = [[sheet_value(v) for v in r] for r in rows]
                where = f"sheet {sheet}"
            else:
                rel = self.layout["raw"].get(table, {}).get("snapshot") or t["snapshot"]
                path = _Path(model_dir) / rel
                if not path.is_file():
                    raise ScriptStop(3, f"{rel} not found in the model folder: land the raw data with rotm.py land")
                with open(path, encoding="utf-8-sig", newline="") as f:
                    reader = _csv.reader(f)
                    header = next(reader, [])
                    types = [t["types"][i] if i < len(t["types"]) else "text" for i in range(len(header))]
                    rows = [[parse_typed(v, types[i] if i < len(types) else "text") for i, v in enumerate(r)]
                            for r in reader if any(x != "" for x in r)]
                where = rel
            width = len(header)
            rows = [(r + [BLANK] * width)[:width] for r in rows]
            if [str(h) for h in header] != list(t["columns"]):
                self.notes.append(f"raw table '{table}': the columns in {where} are {header}; the contract has "
                                  f"{t['columns']}")
            self.raw[table] = {"header": [str(h) for h in header], "rows": rows, "source": where}

    def raw_values(self, table, column):
        raw = self.raw[table]
        if column not in raw["header"]:
            raise NotReproduced(f"raw table '{table}' has no column '{column}'")
        k = raw["header"].index(column)
        return [r[k] for r in raw["rows"]]

    def rawcol(self, table, column):
        key = ("raw", table, column)
        if key not in self._ranges:
            self._ranges[key] = Rng.column([column] + self.raw_values(table, column), tall=True)
        return self._ranges[key]

    def hcol(self, table, column):
        key = ("h", table, column)
        if key not in self._ranges:
            h = self.h.get(table)
            if h is None or column not in h.cols:
                raise NotReproduced(f"helper column {table}.{column} is not computed by this script")
            lay = self.layout["helpers"].get(table, {}).get("columns", {}).get(column, {})
            header = lay.get("header") or lay.get("label") or column
            self._ranges[key] = Rng.column([header] + h.cols[column], tall=True)
        return self._ranges[key]

    def helpers(self, table, fetch, text_columns):
        """The fixed helper columns (design 7.1): row, in_raw, one fetch per raw column, dup when the workbook
        has it. Rows: as many as the workbook's helper block has (the manifest), so the tie-out compares like
        with like; a raw row beyond them is reported by the check <table>_cover, as in the workbook."""
        lay = self.layout["helpers"].get(table, {})
        raw_rows = self.raw[table]["rows"]
        first = int(lay.get("first_row") or 2)
        n = len(raw_rows)
        if lay.get("last_row"):
            n = int(lay["last_row"]) - first + 1
        h = HelperSheet(self, table, lay.get("sheet", f"h_{table}"), n)
        self.h[table] = h
        h.add("row", [first + i for i in range(n)])
        first_col = self.raw_values(table, self.info["tables"][table]["columns"][0])
        h.add("in_raw", [int(i < len(first_col) and first_col[i] is not BLANK) for i in range(n)])
        for col in fetch:
            vals = self.raw_values(table, col)
            if col in text_columns:      # =INDEX(raw!$X:$X,A2)&"" keeps IDs as text, a missing value as ""
                h.add(col, [_text(vals[i]) if i < len(vals) else "" for i in range(n)])
            else:                        # =INDEX(raw!$X:$X,A2): an empty raw cell shows 0
                h.add(col, [cell_value(lambda v=vals[i]: v) if i < len(vals) else 0 for i in range(n)])
        for c in lay.get("columns", {}).values():
            if c.get("kind") != "nullflag" or not c.get("raw_column"):
                continue
            # =--(INDEX(raw!$X:$X,A2)=""): 1 where the raw cell is empty (and below the last raw row)
            vals = self.raw_values(table, c["raw_column"])
            h.add(c["id"], [int(i >= len(vals) or vals[i] is BLANK or vals[i] is None or vals[i] == "")
                            for i in range(n)])
        dup = [c for c in lay.get("columns", {}).values() if c.get("kind") == "dup"]
        if dup:
            # dup_key (a key of several columns): =C2&"|"&D2; dup: =--(SUMPRODUCT(--EXACT(K$2:K2,K2))>1)*B2, 1 on a
            # later copy of a key (design 7.1). EXACT compares exact text, case-sensitive, with no wildcard (the
            # one key equality of common.key_tuple, v2 A-aera-9), so a dictionary of exact values gives the same.
            keys = [k for k in self.info["tables"][table]["key"] if k in h.cols]
            if len(keys) < len(self.info["tables"][table]["key"]):
                self.notes.append(f"{h.sheet}: a key column is not fetched, so the dup column uses only {keys}")
            dupkey = [c for c in lay.get("columns", {}).values() if c.get("kind") == "dupkey"]
            if len(keys) > 1 or dupkey:
                joined = ["|".join(_text(h.cols[k][i]) for k in keys) for i in range(n)]
                if dupkey:
                    h.add(dupkey[0]["id"], joined)
                values = joined
            else:
                values = h.cols[keys[0]] if keys else [BLANK] * n
            flags = h.cols["in_raw"]
            first_at = {}
            out = []
            for i in range(n):
                v = _val(values[i])
                norm = ("s", v) if isinstance(v, str) else ("n", v)
                try:
                    hit = first_at.setdefault(norm, i)
                except TypeError:
                    hit = i
                out.append(int(hit < i) * int(flags[i] or 0))
            h.add(dup[0]["id"], out)
        return h

    # blocks
    def grid(self, bid, sheet, rows, cols, total_row=False, total_col=False, timeline=False):
        b = Block(self, bid, sheet, "grid", rows, cols, total_row=total_row, total_col=total_col,
                  timeline=timeline)
        self.blocks[bid] = b
        return b

    def items(self, bid, sheet, items, cols, timeline=False):
        b = Block(self, bid, sheet, "items", [label for _k, label in items], cols,
                  row_keys=[k for k, _l in items], timeline=timeline)
        self.blocks[bid] = b
        return b

    def measures(self, bid, sheet, rows, measures):
        b = Block(self, bid, sheet, "measures", rows, [label for _k, label in measures],
                  col_keys=[k for k, _l in measures])
        self.blocks[bid] = b
        return b

    def block(self, bid):
        b = self.blocks.get(bid)
        if b is None:
            raise NotReproduced(f"block '{bid}' is not computed by this script")
        return b

    def cell(self, key):
        """A key output cell ({cell:<id>}), found through the manifest's cells map."""
        ref = self.layout["cells"].get(key)
        if ref is None:
            raise NotReproduced(f"key output '{key}' is not in the manifest")
        sheet, coord = _sheet_and_cell(ref)
        col, row = _split_a1(coord)
        if (sheet, row, col) in self._placed:
            return self._placed[(sheet, row, col)]
        raise NotReproduced(f"key output '{key}' ({ref}) is not a cell this script computed")

    def check(self, cid):
        c = self.checks.get(cid)
        if c is None:
            raise NotReproduced(f"check '{cid}' is not computed by this script")
        return c["result"]

    # checks (design 9.1: D actual, E expected or minimum, F maximum, G difference, H tolerance, I result)
    def _check(self, cid, kind, severity, cells, label=""):
        self.checks[cid] = {"kind": kind, "severity": severity, "cells": cells, "result": cells["I"],
                            "label": label}
        self.check_step(cid)

    def check_equal(self, cid, actual, expected, tolerance, severity="error", label=""):
        d, e, h = cell_value(actual), cell_value(expected), cell_value(tolerance)
        g = cell_value(lambda: d - e)
        i = cell_value(lambda: int(GT(ABS(g), h)))
        self._check(cid, "equal", severity, {"D": d, "E": e, "G": g, "H": h, "I": i}, label)

    def check_range(self, cid, actual, minimum, maximum, severity="error", label=""):
        d, e, f = cell_value(actual), cell_value(minimum), cell_value(maximum)
        i = cell_value(lambda: int(OR(LT(d, e), GT(d, f))))
        self._check(cid, "range", severity, {"D": d, "E": e, "F": f, "I": i}, label)

    def check_zero(self, cid, actual, severity="error", label=""):
        d = cell_value(actual)
        i = cell_value(lambda: int(NE(d, 0)))
        self._check(cid, "zero", severity, {"D": d, "I": i}, label)

    def check_at_most(self, cid, actual, bound, severity="error", label=""):
        d, e = cell_value(actual), cell_value(bound)
        i = cell_value(lambda: int(GT(d, e)))
        self._check(cid, "at_most", severity, {"D": d, "E": e, "I": i}, label)

    def raw_checks(self, table):
        """The checks build adds for every raw table (design 9.2), computed from the raw rows."""
        t = self.info["tables"][table]
        first = self.rawcol(table, t["columns"][0])
        n_first = COUNTA(first) - 1
        rows = t.get("expect", {}).get("rows") or {}
        if "exact" in rows:
            self.check_equal(f"{table}_rows", lambda: n_first, lambda: rows["exact"], lambda: 0,
                             label=f"{table}: row count")
        elif rows:
            self.check_range(f"{table}_rows", lambda: n_first, lambda: rows.get("min", BLANK),
                             lambda: rows.get("max", BLANK), label=f"{table}: row count")
        for total in t.get("expect", {}).get("totals", []):
            col = total["column"]
            self.check_equal(f"{table}_total_{col}", lambda: SUM(self.rawcol(table, col)),
                             lambda: total["value"], lambda: total.get("tolerance", 0),
                             label=f"{table}: control total of {col}")
        known_nulls = t.get("known_nulls") or {}
        for col in t["never_null"]:
            if col in known_nulls:       # an agreed known count (contract columns.<col>.known_nulls.max)
                self.check_at_most(f"{table}_nulls_{col}", lambda c=col: COUNTA(first) - COUNTA(self.rawcol(table, c)),
                                   lambda c=col: known_nulls[c], label=f"{table}: empty cells in {col}")
            else:
                self.check_zero(f"{table}_nulls_{col}", lambda c=col: COUNTA(first) - COUNTA(self.rawcol(table, c)),
                                label=f"{table}: empty cells in {col}")
        if table in self.h:
            h = self.h[table]
            self.check_at_most(f"{table}_cover", lambda: n_first - h.n, lambda: 0,
                               label=f"{table}: raw rows not covered by {h.sheet}")
            # =COUNTIF(h!$B:$B,0): helper rows below the last raw row (v2, A-aera-6)
            self.check_zero(f"{table}_beyond", lambda: COUNTIF(self.hcol(table, "in_raw"), 0),
                            label=f"{table}: helper rows of {h.sheet} below the last raw row")
            dup = [c["id"] for c in self.layout["helpers"].get(table, {}).get("columns", {}).values()
                   if c.get("kind") == "dup"]
            if dup and dup[0] in h.cols:
                repeats = (t.get("expect", {}).get("key_repeats") or {}).get("max")
                if repeats:              # an agreed known count (contract expect.key_repeats.max)
                    self.check_at_most(f"{table}_keys", lambda: SUM(self.hcol(table, dup[0])), lambda: repeats,
                                       label=f"{table}: rows repeating an earlier key")
                else:
                    self.check_zero(f"{table}_keys", lambda: SUM(self.hcol(table, dup[0])),
                                    label=f"{table}: rows repeating an earlier key")

    def master_check(self):
        """=--(OR(SUM(results)>0,COUNT(results)<>COUNTA(ids))) over the error checks (design 9.4)."""
        results = []
        for cid, c in self.checks.items():
            lay = self.layout["checks"].get(cid, {})
            severity = lay.get("severity") or c["severity"]
            if severity != "alert":
                results.append(c["result"])
        self.master = cell_value(lambda: int(OR(GT(SUM(Rng.column(results)), 0),
                                                NE(COUNT(Rng.column(results)), len(results)))))
        self.master_step()

    # steps: what the tie-out compares
    def helper_step(self, h, name):
        lay = self.layout["helpers"].get(h.table, {})
        col_lay = lay.get("columns", {}).get(name)
        if col_lay is None or not col_lay.get("col"):
            self.steps.append({"name": f"{h.sheet}.{name}", "kind": "helper", "sheet": h.sheet, "cells": [],
                               "problem": None, "note": "not in the manifest; not compared"})
            return
        col, _r = _split_a1(col_lay["col"] + "1")
        first = int(lay.get("first_row") or 2)
        cells = [(first + i, col, v) for i, v in enumerate(h.cols[name])]
        self.steps.append({"name": f"{h.sheet}.{name}", "kind": "helper", "sheet": h.sheet, "cells": cells,
                           "problem": None})

    def block_step(self, b):
        lay = self.layout["blocks"].get(b.bid)
        name = f"{b.sheet}.{b.bid}"
        if lay is None or not lay.get("body"):
            self.steps.append({"name": name, "kind": b.shape, "sheet": b.sheet, "cells": [], "problem":
                               f"block '{b.bid}' is not in the manifest, so its cells could not be found"})
            return
        sheet = lay.get("sheet", b.sheet)
        c1, r1, c2, r2 = _bounds(lay["body"])
        nr, nc = len(b.row_labels), len(b.col_labels)
        problem = None
        if (r2 - r1 + 1, c2 - c1 + 1) != (nr, nc):
            problem = (f"the workbook's body {lay['body']} is {r2 - r1 + 1} rows by {c2 - c1 + 1} columns; the "
                       f"specification gives {nr} by {nc}")
        cells = []
        for i in range(nr):
            for j in range(nc):
                cells.append((r1 + i, c1 + j, b.values[i][j]))
        if b.total_row is not None:
            for j, v in enumerate(b.total_row):
                if v is not None:
                    cells.append((r1 + nr, c1 + j, v))
        if b.total_col is not None:
            for i, v in enumerate(b.total_col):
                cells.append((r1 + i, c1 + nc, v))
        if b.grand is not None:
            cells.append((r1 + nr, c1 + nc, b.grand))
        if b.timeline:
            for j, v in enumerate(b.col_labels):
                cells.append((r1 - 1, c1 + j, v))
        for r, c, v in cells:
            self._placed[(sheet, r, c)] = v
        self.steps.append({"name": name, "kind": b.shape, "sheet": sheet, "cells": cells, "problem": problem})

    def check_step(self, cid):
        lay = self.layout["checks"].get(cid)
        if lay is None or not lay.get("result"):
            self.steps.append({"name": f"check {cid}", "kind": "check", "sheet": "", "cells": [], "problem": None,
                               "note": "not in the manifest; not compared"})
            return
        sheet, coord = _sheet_and_cell(lay["result"])
        _c, row = _split_a1(coord)
        cells = []
        for letter, v in sorted(self.checks[cid]["cells"].items()):
            if letter in ("G", "H"):
                continue
            col, _r = _split_a1(letter + "1")
            cells.append((row, col, v))
            self._placed[(sheet, row, col)] = v
        self.steps.append({"name": f"check {cid}", "kind": "check", "sheet": sheet, "cells": cells,
                           "problem": None})

    def master_step(self):
        refs = [r for r in [self.layout.get("master")] + list(self.layout.get("master_links") or []) if r]
        by_sheet = {}
        for ref in refs:
            sheet, coord = _sheet_and_cell(ref)
            col, row = _split_a1(coord)
            by_sheet.setdefault(sheet, []).append((row, col, self.master))
        for sheet, cells in by_sheet.items():
            self.steps.append({"name": f"master check ({sheet})", "kind": "master", "sheet": sheet,
                               "cells": cells, "problem": None})

    def input_steps(self):
        """The input cells: the values the script used against the workbook's."""
        for iid, ref in sorted((self.layout.get("inputs") or {}).items()):
            item = self.inputs.get(iid)
            if item is None:
                continue
            sheet, coord = _sheet_and_cell(ref)
            c1, r1, c2, r2 = _bounds(coord)
            if item["kind"] == "scalar":
                vals = [[item["value"]]]
            elif item["kind"] == "list":
                vals = [[v] for v in item["values"]]
            else:
                vals = [list(r) for r in item["values"]]
            cells = []
            for i, rowv in enumerate(vals):
                for j, v in enumerate(rowv):
                    if r1 + i <= r2 and c1 + j <= c2:
                        cells.append((r1 + i, c1 + j, BLANK if v is None else v))
            self.steps.append({"name": f"input {iid}", "kind": "input", "sheet": sheet, "cells": cells,
                               "problem": None})

    def inputs_from_workbook(self, values):
        """Use the input values held in the workbook instead of the specification's (--inputs workbook)."""
        for iid, ref in (self.layout.get("inputs") or {}).items():
            item = self.inputs.get(iid)
            if item is None:
                continue
            sheet, coord = _sheet_and_cell(ref)
            cells = values.get(sheet, {})
            c1, r1, c2, r2 = _bounds(coord)
            grid = [[sheet_value(cells.get((r, c))) for c in range(c1, c2 + 1)] for r in range(r1, r2 + 1)]
            if item["kind"] == "scalar":
                item["value"] = grid[0][0]
            elif item["kind"] == "list":
                item["values"] = [r[0] for r in grid]
            else:
                item["values"] = grid
        self._ranges.clear()

    def frames(self):
        """Every helper sheet and block as a pandas DataFrame, by name (needs pandas)."""
        import pandas as pd
        out = {}
        for table, h in self.h.items():
            out[h.sheet] = pd.DataFrame({k: [_plain_value(v) for v in h.cols[k]] for k in h.order})
        for bid, b in self.blocks.items():
            out[bid] = b.frame()
        return out

    def write_csv(self, folder):
        """Every helper sheet and block as a CSV file in `folder` (standard library only)."""
        target = _Path(folder)
        target.mkdir(parents=True, exist_ok=True)
        written = []
        for table, h in self.h.items():
            path = target / f"{h.sheet}.csv"
            with open(path, "w", encoding="utf-8", newline="") as f:
                w = _csv.writer(f)
                w.writerow(h.order)
                for i in range(h.n):
                    w.writerow([_plain_value(h.cols[k][i]) for k in h.order])
            written.append(path)
        for bid, b in self.blocks.items():
            path = target / f"{b.sheet}.{bid}.csv"
            with open(path, "w", encoding="utf-8", newline="") as f:
                w = _csv.writer(f)
                w.writerow([""] + [_label(c) for c in b.col_labels])
                for r, row in zip(b.row_labels, b.values):
                    w.writerow([_label(r)] + [_plain_value(v) for v in row])
            written.append(path)
        return written


def _sheet_table(cells):
    """(header, rows) of a raw sheet read from the workbook: the header is row 1 up to its first empty cell."""
    header = []
    c = 1
    while cells.get((1, c)) not in (None, ""):
        header.append(cells[(1, c)])
        c += 1
    last = max((r for (r, _c) in cells), default=1)
    rows = [[cells.get((r, k + 1)) for k in range(len(header))] for r in range(2, last + 1)]
    while rows and all(v in (None, "") for v in rows[-1]):
        rows.pop()
    return header, rows


class ScriptStop(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def read_layout(workbook):
    """Where every value sits in the workbook, from its manifest (model.manifest.json, written by build)."""
    mpath = workbook.with_name(workbook.stem + ".manifest.json")
    if not mpath.is_file():
        raise ScriptStop(3, f"{mpath.name} not found next to {workbook.name}: build the workbook with rotm.py build")
    man = _json.loads(mpath.read_text(encoding="utf-8-sig"))
    lay = {"blocks": {}, "helpers": {}, "raw": {}, "checks": {}, "inputs": dict(man.get("inputs") or {}),
           "cells": dict(man.get("cells") or {}), "master": man.get("master_check"),
           "master_links": list(man.get("master_links") or []), "spec_sha256": man.get("spec_sha256")}
    for s in man.get("sheets", []):
        if s.get("type") in ("calc", "output"):
            for b in s.get("blocks", []):
                lay["blocks"][b["id"]] = dict(b, sheet=s["name"])
        elif s.get("type") == "helpers":
            lay["helpers"][s["table"]] = {"sheet": s["name"], "first_row": s.get("first_row"),
                                          "last_row": s.get("last_row"),
                                          "columns": {c["id"]: c for c in s.get("columns", [])}}
        elif s.get("type") == "raw":
            lay["raw"][s["table"]] = {"sheet": s["name"], "snapshot": s.get("snapshot"), "digest": s.get("digest")}
    for c in man.get("checks", []):
        lay["checks"][c["id"]] = c
    return lay


def read_workbook_values(workbook, sheets=None):
    """{sheet: {(row, col): value}} of the values Excel saved (openpyxl, read-only, data_only). A workbook that
    Excel has not recalculated and saved holds no values for its formulas."""
    import warnings

    import openpyxl
    out = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(workbook, read_only=True, data_only=True)
        try:
            for ws in wb.worksheets:
                if sheets is not None and ws.title not in sheets:
                    continue
                cells = {}
                try:
                    ws.reset_dimensions()
                except AttributeError:
                    pass
                for row in ws.iter_rows():
                    for c in row:
                        v = getattr(c, "value", None)
                        if v is not None:
                            cells[(c.row, c.column)] = v
                out[ws.title] = cells
        finally:
            wb.close()
    return out


_ERROR_TEXTS = ("#NULL!", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A", "#SPILL!", "#CALC!")


def compare_value(wb, py, tolerance, rel=REL_TOLERANCE):
    """('tie' | 'differ' | 'not reproduced', difference or None) of a workbook value against the script's.
    Numbers tie when the difference is at most `tolerance` (absolute) plus `rel` times the larger value."""
    if isinstance(py, NotReproduced):
        return "not reproduced", None
    a = sheet_value(wb)
    b = py.code if isinstance(py, XLErr) else py
    if a is BLANK:
        a = None
    if b is BLANK:
        b = None
    if a in (None, "") and b in (None, ""):
        return "tie", None
    num_a = isinstance(a, (int, float))
    num_b = isinstance(b, (int, float))
    if num_a and num_b:
        d = float(b) - float(a)
        limit = tolerance + rel * max(abs(float(a)), abs(float(b)))
        return ("tie" if abs(d) <= limit else "differ"), d
    if isinstance(a, str) and isinstance(b, str):
        return ("tie" if a == b else "differ"), None
    return "differ", None


def _show(v):
    if isinstance(v, NotReproduced):
        return "not reproduced"
    if isinstance(v, XLErr):
        return v.code
    v = sheet_value(v)
    if v is BLANK:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        return f"{v:,.6g}" if abs(v) < 1e6 else f"{v:,.2f}"
    if isinstance(v, int):
        return f"{v:,}"
    text = str(v)
    return text if len(text) <= 28 else text[:25] + "..."


def tie_out(model, values, tolerance, rel=REL_TOLERANCE):
    """Compare every step's cells with the workbook. Returns the result as a dict (also the --json output):
    one record per compared cell in "cells", and per step the largest difference over every compared cell,
    ties included (v4, A-aera-6)."""
    steps, cells = [], []
    totals = {"cells": 0, "tie": 0, "differ": 0, "not reproduced": 0}
    largest, largest_cell = None, None
    formula_cells = filled = 0          # cells that always hold a formula: helper, block and master cells
    for step in model.steps:
        counts = {"cells": 0, "tie": 0, "differ": 0, "not reproduced": 0}
        max_d, max_cell = None, None
        first = None
        why = None                      # v7, A-aera-12: the reason of the step's first cell that is not reproduced
        sheet_cells = values.get(step["sheet"], {})
        always_formula = step["kind"] in ("helper", "grid", "items", "measures", "master")
        for row, col, py in step["cells"]:
            wb = sheet_cells.get((row, col))
            if always_formula:
                formula_cells += 1
                filled += wb is not None
            status, d = compare_value(wb, py, tolerance, rel)
            counts["cells"] += 1
            counts[status] += 1
            ref = f"{step['sheet']}!{_a1(col, row)}"
            if d is not None and (max_d is None or abs(d) > max_d):
                max_d, max_cell = abs(d), ref
            if status != "tie" and first is None:
                first = ref
            if status == "not reproduced" and why is None:
                why = str(py)
            cells.append({"cell": ref, "step": step["name"], "workbook": _show(wb), "script": _show(py),
                          "difference": d, "status": status})
        if step.get("problem"):
            counts["differ"] += 1
        for k in totals:
            totals[k] += counts[k]
        if max_d is not None and (largest is None or max_d > largest):
            largest, largest_cell = max_d, max_cell
        steps.append({"step": step["name"], "kind": step["kind"], "sheet": step["sheet"], **counts,
                      "max_difference": max_d, "max_difference_cell": max_cell, "first_difference": first,
                      "problem": step.get("problem"), "note": step.get("note"), "not_reproduced_reason": why})
    recalculated = not (formula_cells and filled == 0)
    if not recalculated:
        status = "not_verified"
    elif totals["differ"] or totals["not reproduced"]:
        status = "differ"
    else:
        status = "tie"
    return {"rotm_lift": 1, "model": model.info["name"], "status": status, "tolerance": tolerance,
            "relative_tolerance": rel, "max_difference": largest, "max_difference_cell": largest_cell,
            "recalculated": recalculated, "counts": totals, "steps": steps, "cells": cells,
            "notes": list(model.notes)}


def tie_out_header(result):
    """The header line of the tie-out: the cells compared, the rule and the largest difference (v4, A-aera-6)."""
    d, cell = result.get("max_difference"), result.get("max_difference_cell")
    largest = f"{d:.6g} at {cell}" if d is not None else "- (no number was compared)"
    return (f"tie-out: {result['counts']['cells']:,} cells compared; tolerance {result['tolerance']:g} (absolute) "
            f"plus {result.get('relative_tolerance', 0):g} times the larger value (double-precision rounding); "
            f"largest difference {largest}")


def print_tie_out(result, model, show_all=False, out=None):
    w = (out or _sys.stdout).write
    w(f"Tie-out of {model.info['name']}\n")
    w(tie_out_header(result) + "\n")
    w(f"{'step':<34} {'cells':>7} {'tie':>7} {'differ':>7} {'not repr.':>9}  largest difference\n")
    for s in result["steps"]:
        largest = "-" if s["max_difference"] is None else f"{s['max_difference']:,.6g}"
        w(f"{s['step'][:34]:<34} {s['cells']:>7,} {s['tie']:>7,} {s['differ']:>7,} {s['not reproduced']:>9,}  "
          f"{largest}\n")
        if s.get("problem"):
            w(f"    problem: {s['problem']}\n")
        if s.get("note"):
            w(f"    note: {s['note']}\n")
        if s.get("not_reproduced_reason"):
            w(f"    not reproduced: {s['not_reproduced_reason']}\n")
    shown =[c for c in result["cells"] if show_all or c["status"] != "tie"]
    if shown:
        w("\n" + (f"Every compared cell ({len(shown):,})" if show_all else
                  "Cells that do not tie out (first 40; --all shows every compared cell)") + "\n")
        w(f"{'cell':<22} {'workbook':>16} {'script':>16} {'difference':>12}  status\n")
        for c in shown[: None if show_all else 40]:
            d = "" if c["difference"] is None else f"{c['difference']:,.6g}"
            w(f"{c['cell'][:22]:<22} {c['workbook']:>16} {c['script']:>16} {d:>12}  {c['status']}\n")
    checks = [(cid, c) for cid, c in model.checks.items()]
    if checks:
        failing = [cid for cid, c in checks if c["result"] != 0]
        w(f"\nChecks computed by the script: {len(checks)}; failing: {len(failing)}"
          + (f" ({', '.join(failing[:10])})" if failing else "") + "\n")
        w(f"Master check computed by the script: {_show(model.master)} (0 = OK)\n")
    for n in result["notes"]:
        w(f"note: {n}\n")
    c = result["counts"]
    if result["status"] == "not_verified":
        w("\nNot verified: the workbook holds no values saved by Excel (it was not recalculated). Run "
          "rotm.py check <model> (Excel recalculates and saves it), then run this script again.\n")
    elif result["status"] == "tie":
        w(f"\nResult: every compared value ties out ({c['cells']:,} cells).\n")
    else:
        w(f"\nResult: {c['differ']:,} cells differ and {c['not reproduced']:,} are not reproduced, of "
          f"{c['cells']:,} compared.\n")


def run_model(model_dir=None, workbook=None, raw="csv", inputs="spec"):
    """Compute the model; returns (model, workbook values). Importable: m, _ = run_model(); m.frames()."""
    model_dir = _Path(model_dir) if model_dir else default_model_dir()
    wb = _Path(workbook) if workbook else model_dir / INFO["workbook"]
    if not wb.is_file():
        raise ScriptStop(3, f"workbook {wb} not found: build it with rotm.py build")
    layout = read_layout(wb)
    sheets = {b["sheet"] for b in layout["blocks"].values()} | {h["sheet"] for h in layout["helpers"].values()}
    for ref in [layout.get("master")] + layout["master_links"] + [c.get("result") for c in layout["checks"].values()] \
            + list(layout["inputs"].values()) + list(layout["cells"].values()):
        if ref:
            sheets.add(_sheet_and_cell(ref)[0])
    if raw == "workbook":
        sheets |= {r["sheet"] for r in layout["raw"].values()}
        sheets |= {f"raw_{t}" for t in INFO["tables"]}
    try:
        values = read_workbook_values(wb, sheets)
    except ImportError:
        raise ScriptStop(3, "openpyxl is not installed, so the workbook's values cannot be read "
                            "(pip install openpyxl)") from None
    model = Model(INFO, layout)
    if layout.get("spec_sha256") and layout["spec_sha256"] != INFO["spec_sha256"]:
        model.notes.append("the workbook was built from another version of the specification than this script "
                           "(spec sha256 differs); run rotm.py lift again")
    for table, t in INFO["tables"].items():
        recorded = layout["raw"].get(table, {}).get("digest")
        if t.get("snapshot_digest") and recorded and recorded != t["snapshot_digest"]:
            model.notes.append(f"raw table '{table}': the manifest records another snapshot digest than the one "
                               "this script took its control totals for (the snapshot was landed or accepted "
                               "again); run rotm.py lift again")
    if inputs == "workbook":
        model.inputs_from_workbook(values)
    model.load_raw(model_dir, raw, values)
    build_model(model)
    model.input_steps()
    return model, values


def main(argv=None):
    p = _argparse.ArgumentParser(description=f"The model {INFO['name']} computed in Python and tied out to the "
                                             "workbook. Exit 0: every value ties out; 1: a value differs or is "
                                             "not reproduced; 2: bad arguments; 3: not verified.")
    p.add_argument("--model", help="the model folder (default: the folder this script was written for)")
    p.add_argument("--workbook", help="the workbook to tie out to (default: the model's workbook)")
    p.add_argument("--raw", choices=("csv", "workbook"), default="csv",
                   help="read the raw tables from raw/<table>.csv (default) or from the workbook's raw sheets")
    p.add_argument("--inputs", choices=("spec", "workbook"), default="spec",
                   help="use the specification's input values (default) or those held in the workbook")
    p.add_argument("--all", action="store_true", help="print every compared cell")
    p.add_argument("--json", metavar="FILE",
                   help="write the tie-out as JSON, one record per compared cell ('-' = stdout)")
    p.add_argument("--csv", metavar="DIR", help="also write every helper sheet and block as a CSV file")
    args = p.parse_args(argv)
    try:
        model, values = run_model(args.model, args.workbook, args.raw, args.inputs)
    except ScriptStop as stop:
        _sys.stderr.write(f"{_Path(__file__).name}: {stop.message}\n")
        return stop.code
    result = tie_out(model, values, float(INFO["tolerance"]))
    stream = _sys.stderr if args.json == "-" else _sys.stdout
    print_tie_out(result, model, show_all=args.all, out=stream)
    if args.csv:
        written = model.write_csv(args.csv)
        stream.write(f"wrote {len(written)} CSV files to {args.csv}\n")
    if args.json:
        text = _json.dumps(result, ensure_ascii=False, indent=1, default=str)
        if args.json == "-":
            _sys.stdout.write(text + "\n")
        else:
            with open(args.json, "w", encoding="utf-8") as f:
                f.write(text + "\n")
    return {"tie": 0, "differ": 1, "not_verified": 3}[result["status"]]


# <<< SCRIPT RUNTIME ----------------------------------------------------------------------------------------

# ============================================================================================================
# lift itself: parse spec expressions, translate them, order the steps, write the script.
# ============================================================================================================

RUNTIME_START = "# >>> SCRIPT RUNTIME"
RUNTIME_END = "# <<< SCRIPT RUNTIME"
# Functions the translator writes as Python forms of their own, not as calls of the runtime above. ROW: only
# ROW() without an argument on a helper sheet (the helper row's number); another ROW is "not reproduced".
_SPECIAL = {"IF", "ROW"}
# Functions the evaluator reproduces only in part (references/functions.md lists these limits; v3, A-aera-13).
REPRODUCED_LIMITS = {
    # v7, A-aera-12: the characters of _PROPER_NOT_REPRODUCED (runtime above)
    "PROPER": "a word is a run of letters by Unicode, as Excel 16 treats it (measured on the code points up to "
              "U+3FFFF); a text with a character the script lists as not reproduced (2,239 measured characters "
              "whose case Excel gives otherwise, for example U+00B5, U+0130 and U+03A3, and the code points not "
              "measured) is not reproduced",
    "ROW": "only ROW() without an argument, on a helper sheet",
    "TEXT": "only the formats yyyy, yy, mmmm, mmm, mm, m, dddd, ddd, dd, d (dates), 0, 0.0, #,##0, #,##0.00, 0%, "
            "0.0%, General and @",
}
UNSUPPORTED_FIX = ("lift reproduces the functions marked in references/functions.md; change the model, or "
                   "reproduce these cells by hand after the lift")
# Arguments the runtime evaluates only when needed (written as lambda: ...), as Excel does.
_LAZY_ALL = {"IFERROR", "IFNA", "ISERROR", "ISERR", "ISNA", "ISBLANK", "ISNUMBER", "ISTEXT", "ISNONTEXT",
             "ISLOGICAL", "IFS"}
_LAZY_AFTER_FIRST = {"CHOOSE", "SWITCH"}
# v5, A-aera-7: the aggregate functions that skip text reached through a reference, with the index of their first
# such argument (NPV: the values after the rate), and the functions that hand on a reference (Excel 16, 2026-11-25).
_REF_AGGREGATES = {"SUM": 0, "AVERAGE": 0, "MIN": 0, "MAX": 0, "PRODUCT": 0, "COUNT": 0, "NPV": 1}
_REF_PASSING = {"IF", "CHOOSE", "IFS", "SWITCH", "INDEX", "XLOOKUP"}
_VOLATILE = {"TODAY", "NOW", "RAND", "RANDBETWEEN", "RANDARRAY", "CELL", "INFO", "OFFSET", "INDIRECT"}
_BIN_OPS = {"+": "+", "-": "-", "*": "*", "/": "/"}
_CMP_OPS = {"=": "EQ", "<>": "NE", "<": "LT", "<=": "LE", ">": "GT", ">=": "GE"}
_PRECEDENCE = {"^": 5, "*": 4, "/": 4, "+": 3, "-": 3, "&": 2, "=": 1, "<>": 1, "<": 1, "<=": 1, ">": 1, ">=": 1}
_PH_TOKEN = "PHQZ{}QZ"
_PH_TOKEN_RE = re.compile(r"^PHQZ(\d+)QZ$")


_SOURCE_CACHE: dict[str, Any] = {}


def runtime_source() -> str:
    """The runtime text between the markers in this file (copied into every script)."""
    if "runtime" not in _SOURCE_CACHE:
        text = Path(__file__).read_text(encoding="utf-8")
        start = text.index("\n" + RUNTIME_START + " ") + 1
        end = text.index("\n" + RUNTIME_END + " ", start) + 1
        body = text[start:end]
        _SOURCE_CACHE["runtime"] = body[body.index("\n") + 1:].rstrip() + "\n"
    return _SOURCE_CACHE["runtime"]


def supported_functions() -> frozenset[str]:
    """The one list of the Excel functions lift reproduces: the upper-case module-level functions of the runtime
    between the markers, and the forms the translator writes itself (_SPECIAL). lift reports a function of the
    model outside this list as LIFT-UNSUPPORTED, and references/functions.md marks the list (lib.functions)."""
    if "functions" not in _SOURCE_CACHE:
        names = set(re.findall(r"^def ([A-Z][A-Z0-9]*)\(", runtime_source(), flags=re.M))
        names -= {"NOT_REPRODUCED", "ERROR_VALUE", "EQ", "NE", "LT", "LE", "GT", "GE", "JOIN", "POW"}
        _SOURCE_CACHE["functions"] = frozenset(names | _SPECIAL)
    return _SOURCE_CACHE["functions"]


def expression_functions(expr: str) -> list[str]:
    """The functions a spec expression calls, upper case and without file prefixes, in the order written; [] when
    the expression cannot be read (lift then reports the step as not reproduced)."""
    try:
        tree, _phs = parse_expression(expr)
    except Untranslatable:
        return []
    out: list[str] = []

    def walk(node: Any) -> None:
        kind = node[0]
        if kind == "call":
            out.append(node[1])
            for a in node[2]:
                walk(a)
        elif kind in ("neg", "pct"):
            walk(node[1])
        elif kind == "bin":
            walk(node[2])
            walk(node[3])
        elif kind == "array":
            for row in node[1]:
                for v in row:
                    walk(v)

    walk(tree)
    return out


def _block_entries(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for s in manifest.get("sheets", []):
        for b in s.get("blocks", []) or []:
            out[b["id"]] = dict(b, sheet=s["name"])
    return out


def _range_cells(c1: int, r1: int, c2: int, r2: int) -> tuple[int, str | None]:
    """(number of cells, first cell) of a rectangle; (0, None) when it is empty."""
    if c2 < c1 or r2 < r1:
        return 0, None
    return (c2 - c1 + 1) * (r2 - r1 + 1), _a1(c1, r1)


def expression_cells(spec: Any, manifest: dict[str, Any]) -> list[tuple[str, str, str, int, str | None]]:
    """(step name, expression, sheet, cell count, first cell) of every expression of the specification, at the
    cells the manifest gives it: a helper column's rows, a grid's body, an item's row, a measure's column (and its
    total row when the total is "same"), a check's actual (D), expected or minimum (E) and maximum (F). A place the
    manifest does not have counts one cell with no cell named."""
    from . import manifest as manmod
    out: list[tuple[str, str, str, int, str | None]] = []
    for table, sheet in spec.helpers.items():
        entry = manmod.helper_sheet(manifest, table) or {}
        cols = {c["id"]: c for c in entry.get("columns", [])}
        first, last = int(entry.get("first_row") or 2), entry.get("last_row")
        for col in sheet.data.get("columns", []):
            name = f"{sheet.name}.{col['id']}"
            lay = cols.get(col["id"])
            if lay is None or not lay.get("col") or last is None:
                out.append((name, col["expr"], sheet.name, 1, None))
                continue
            c, _r = _split_a1(lay["col"] + "1")
            n, cell = _range_cells(c, first, c, int(last))
            out.append((name, col["expr"], entry.get("name", sheet.name), n, cell or f"{lay['col']}{first}"))
    blocks = _block_entries(manifest)
    for bid, b in spec.blocks.items():
        data, shape, name = b["data"], b["shape"], f"{b['sheet']}.{bid}"
        lay = blocks.get(bid)
        if lay is None or not lay.get("body"):
            exprs = ([data["cell"]] if shape == "grid" else
                     [x for i in data.get("items", []) for x in (i["expr"], i.get("first")) if x] if shape == "items"
                     else [m["expr"] for m in data.get("measures", [])])
            out += [(name, e, b["sheet"], 1, None) for e in exprs]
            continue
        sheet = lay["sheet"]
        c1, r1, c2, r2 = _bounds(lay["body"])
        if shape == "grid":
            out.append((name, data["cell"], sheet, *_range_cells(c1, r1, c2, r2)))
        elif shape == "items":
            for k, item in enumerate(data["items"]):
                r = r1 + k
                if "first" in item:
                    out.append((name, item["first"], sheet, *_range_cells(c1, r, c1, r)))
                    out.append((name, item["expr"], sheet, *_range_cells(c1 + 1, r, c2, r)))
                else:
                    out.append((name, item["expr"], sheet, *_range_cells(c1, r, c2, r)))
        else:
            for j, meas in enumerate(data["measures"]):
                last_row = r2 + (1 if meas.get("total") == "same" else 0)
                out.append((name, meas["expr"], sheet, *_range_cells(c1 + j, r1, c1 + j, last_row)))
    checks = {c["id"]: c for c in manifest.get("checks", [])}
    letters = {"actual": "D", "expected": "E", "min": "E", "bound": "E", "max": "F"}
    for cid, entry in spec.checks.items():
        chk = entry["data"]
        lay = checks.get(cid) or {}
        sheet, cell = _sheet_and_cell(lay["result"]) if lay.get("result") else (entry["sheet"], None)
        row = _split_a1(cell)[1] if cell else None
        for key, letter in letters.items():
            v = chk.get(key)
            if isinstance(v, str):
                out.append((f"check {cid}", v, sheet, 1, f"{letter}{row}" if row else None))
    return out


def unsupported_functions(spec: Any, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Every function of the specification that the evaluator does not reproduce (not in supported_functions()),
    in the order first used: {"function", "cells" (count), "sheet", "cell" (the first), "steps"}."""
    supported = supported_functions()
    found: dict[str, dict[str, Any]] = {}
    for step, expr, sheet, n, cell in expression_cells(spec, manifest):
        for fn in dict.fromkeys(expression_functions(expr)):      # a function written twice in one formula: once
            if fn in supported:
                continue
            e = found.setdefault(fn, {"function": fn, "cells": 0, "sheet": sheet, "cell": cell, "steps": []})
            if e["cell"] is None and cell is not None:
                e["sheet"], e["cell"] = sheet, cell
            e["cells"] += n
            if step not in e["steps"]:
                e["steps"].append(step)
    return list(found.values())


def _doc(text: Any) -> str:
    """Text that is safe inside the script's docstring."""
    return " ".join(str(text).replace("\\", "/").replace('"""', "'''").split())


class Untranslatable(Exception):
    """A spec expression the translator does not cover; the step is written as 'not reproduced'."""


# ----------------------------------------------------------------------------- parsing

def parse_expression(expr: str) -> tuple[Any, list[Any]]:
    """(tree, placeholders) of a spec expression. The tree is nested tuples: ("num", v), ("str", s),
    ("bool", b), ("err", code), ("missing",), ("ph", n), ("ref", text), ("neg", x), ("pct", x),
    ("bin", op, a, b), ("call", NAME, [args]), ("array", [[...]]). Raises Untranslatable."""
    from . import formula, spec as specmod
    phs = specmod.placeholders(expr)
    out, last = [], 0
    for n, ph in enumerate(phs):
        out.append(expr[last:ph.start])
        out.append(_PH_TOKEN.format(n))
        last = ph.end
    out.append(expr[last:])
    text = "".join(out).strip()
    try:
        tokens = [t for t in formula.tokenize("=" + text) if t.type != "WHITE-SPACE"]
    except ValueError as exc:
        raise Untranslatable(f"the expression could not be read ({exc})") from None
    parser = _Parser(tokens)
    tree = parser.expression(0)
    if parser.i != len(tokens):
        raise Untranslatable(f"unexpected '{tokens[parser.i].text}' in the expression")
    return tree, phs


class _Parser:
    def __init__(self, tokens: list[Any]):
        self.tokens = tokens
        self.i = 0

    def peek(self) -> Any:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self) -> Any:
        t = self.peek()
        if t is None:
            raise Untranslatable("the expression ends too early")
        self.i += 1
        return t

    def expression(self, min_prec: int) -> Any:
        left = self.unary()
        while True:
            t = self.peek()
            if t is None or t.type != "OPERATOR-INFIX":
                return left
            op = t.text.strip()
            if op not in _PRECEDENCE:
                raise Untranslatable(f"the operator '{op}' is not translated")
            prec = _PRECEDENCE[op]
            if prec < min_prec:
                return left
            self.take()
            right = self.expression(prec + 1)
            left = ("bin", op, left, right)

    def unary(self) -> Any:
        t = self.peek()
        if t is not None and t.type == "OPERATOR-PREFIX":
            self.take()
            inner = self.unary()
            return ("neg", inner) if t.text == "-" else inner
        node = self.primary()
        while True:
            t = self.peek()
            if t is not None and t.type == "OPERATOR-POSTFIX" and t.text == "%":
                self.take()
                node = ("pct", node)
            else:
                return node

    def primary(self) -> Any:
        t = self.take()
        if t.type == "OPERAND":
            if t.subtype == "NUMBER":
                v = float(t.text)
                return ("num", int(v) if v.is_integer() and "." not in t.text and "e" not in t.text.lower() else v)
            if t.subtype == "TEXT":
                return ("str", t.text[1:-1].replace('""', '"'))
            if t.subtype == "LOGICAL":
                return ("bool", t.text.upper() == "TRUE")
            if t.subtype == "ERROR":
                return ("err", t.text.upper())
            m = _PH_TOKEN_RE.match(t.text)
            if m:
                return ("ph", int(m.group(1)))
            return ("ref", t.text)
        if t.type == "FUNC" and t.subtype == "OPEN":
            name = t.text[:-1].upper()
            for prefix in ("_XLFN._XLWS.", "_XLFN.", "_XLWS."):
                if name.startswith(prefix):
                    name = name[len(prefix):]
            args: list[Any] = []
            nxt = self.peek()
            if nxt is not None and nxt.type == "FUNC" and nxt.subtype == "CLOSE":
                self.take()
                return ("call", name, args)
            while True:
                nxt = self.peek()
                if nxt is not None and (nxt.type == "SEP" and nxt.subtype == "ARG" or
                                        nxt.type == "FUNC" and nxt.subtype == "CLOSE"):
                    args.append(("missing",))
                else:
                    args.append(self.expression(0))
                sep = self.take()
                if sep.type == "FUNC" and sep.subtype == "CLOSE":
                    return ("call", name, args)
                if not (sep.type == "SEP" and sep.subtype == "ARG"):
                    raise Untranslatable(f"unexpected '{sep.text}' in the arguments of {name}")
        if t.type == "PAREN" and t.subtype == "OPEN":
            inner = self.expression(0)
            close = self.take()
            if not (close.type == "PAREN" and close.subtype == "CLOSE"):
                raise Untranslatable("a parenthesis is not closed")
            return inner
        if t.type == "ARRAY" and t.subtype == "OPEN":
            rows: list[list[Any]] = [[]]
            while True:
                nxt = self.peek()
                if nxt is not None and nxt.type == "ARRAY" and nxt.subtype == "CLOSE":
                    self.take()
                    return ("array", rows)
                rows[-1].append(self.expression(0))
                sep = self.take()
                if sep.type == "ARRAY" and sep.subtype == "CLOSE":
                    return ("array", rows)
                if sep.type == "SEP" and sep.subtype == "ROW":
                    rows.append([])
                elif not (sep.type == "SEP" and sep.subtype == "ARG"):
                    raise Untranslatable(f"unexpected '{sep.text}' in an array constant")
        raise Untranslatable(f"unexpected '{t.text}' in the expression")


# ----------------------------------------------------------------------------- translation

@dataclass
class _Ctx:
    """Where an expression is written: kind helpers | grid | items | measures | check."""
    kind: str
    spec: Any
    block: str | None = None
    item: str | None = None
    table: str | None = None
    deps: set[tuple[str, ...]] = field(default_factory=set)
    same_block: set[str] = field(default_factory=set)       # items / measures of this block used in this column


def translate(expr: str, ctx: _Ctx) -> str:
    """The Python expression for a spec expression in its context. Raises Untranslatable."""
    tree, phs = parse_expression(expr)
    return _emit(tree, phs, ctx)


def _emit(node: Any, phs: list[Any], ctx: _Ctx) -> str:
    kind = node[0]
    if kind == "num":
        return repr(node[1])
    if kind == "str":
        return repr(node[1])
    if kind == "bool":
        return "True" if node[1] else "False"
    if kind == "err":
        return f"ERROR_VALUE({node[1]!r})"
    if kind == "missing":
        return "BLANK"
    if kind == "ph":
        return _placeholder(phs[node[1]], ctx)
    if kind == "ref":
        raise Untranslatable(f"'{node[1]}' is a cell reference or name written in the specification; write it as "
                             "a placeholder")
    if kind == "neg":
        return f"(-{_emit(node[1], phs, ctx)})"
    if kind == "pct":
        return f"({_emit(node[1], phs, ctx)} / 100)"
    if kind == "array":
        rows = ", ".join("[" + ", ".join(_emit(v, phs, ctx) for v in r) + "]" for r in node[1])
        return f"Rng([{rows}])"
    if kind == "bin":
        op, a, b = node[1], _emit(node[2], phs, ctx), _emit(node[3], phs, ctx)
        if op in _BIN_OPS:
            return f"({a} {_BIN_OPS[op]} {b})"
        if op in _CMP_OPS:
            return f"{_CMP_OPS[op]}({a}, {b})"
        if op == "^":
            return f"POW({a}, {b})"
        if op == "&":
            return f"JOIN({a}, {b})"
        raise Untranslatable(f"the operator '{op}' is not translated")
    if kind == "call":
        return _call(node[1], node[2], phs, ctx)
    raise Untranslatable(f"unknown part {kind!r}")


def _call(name: str, args: list[Any], phs: list[Any], ctx: _Ctx) -> str:
    if name in _VOLATILE:
        raise Untranslatable(f"{name} is volatile or refers to cells by position at run time; its value cannot "
                             "be reproduced outside the workbook")
    if name == "IF":
        if not 2 <= len(args) <= 3:
            raise Untranslatable("IF needs 2 or 3 arguments")
        cond = _emit(args[0], phs, ctx)
        yes = _emit(args[1], phs, ctx)
        no = _emit(args[2], phs, ctx) if len(args) == 3 else "False"
        return f"({yes} if truthy({cond}) else {no})"
    if name == "ROW" and not args:
        if ctx.kind == "helpers":
            return 'r["row"]'
        raise Untranslatable("ROW() outside a helper sheet depends on the cell's position")
    if name in ("ROW", "COLUMN"):
        raise Untranslatable(f"{name} depends on the cell's position")
    if name not in supported_functions():
        raise Untranslatable(f"{name} is not translated by lift")
    if name in _REF_AGGREGATES:
        first = _REF_AGGREGATES[name]
        parts = [_emit(a, phs, ctx) for a in args[:first]] + [_ref_arg(a, phs, ctx, name) for a in args[first:]]
        return f"{name}({', '.join(parts)})"
    parts = [_emit(a, phs, ctx) for a in args]
    if name in _LAZY_ALL:
        parts = [f"lambda: {p}" for p in parts]
    elif name in _LAZY_AFTER_FIRST:
        parts = parts[:1] + [f"lambda: {p}" for p in parts[1:]]
    return f"{name}({', '.join(parts)})"


def _ref_arg(node: Any, phs: list[Any], ctx: _Ctx, agg: str) -> str:
    """An argument of an aggregate function (_REF_AGGREGATES; v5, A-aera-7). Excel skips text, also text that
    reads as a number, and TRUE/FALSE in a value it reaches through a reference, and converts a value typed or
    computed in the formula. A placeholder is a cell reference in the workbook, so its value is written as
    as_ref(...). IF, CHOOSE, IFS and SWITCH hand on the reference of the argument they return, so their results are
    written the same way; INDEX of a placeholder range and XLOOKUP with a placeholder return range give a reference
    too. Any other argument is a value. A form where lift cannot tell a reference from a value is Untranslatable
    (the step is listed as not reproduced before the tie-out)."""
    kind = node[0]
    if kind == "ph":
        return f"as_ref({_placeholder(phs[node[1]], ctx)})"
    if kind != "call":
        return _emit(node, phs, ctx)
    name, args = node[1], node[2]

    def lazy(a: Any, ref: bool) -> str:
        return f"lambda: {_ref_arg(a, phs, ctx, agg) if ref else _emit(a, phs, ctx)}"

    if name == "IF":
        if not 2 <= len(args) <= 3:
            raise Untranslatable("IF needs 2 or 3 arguments")
        cond = _emit(args[0], phs, ctx)
        yes = _ref_arg(args[1], phs, ctx, agg)
        no = _ref_arg(args[2], phs, ctx, agg) if len(args) == 3 else "False"
        return f"({yes} if truthy({cond}) else {no})"
    if name == "CHOOSE" and len(args) >= 2:
        return f"CHOOSE({', '.join([_emit(args[0], phs, ctx)] + [lazy(a, True) for a in args[1:]])})"
    if name == "IFS":
        return f"IFS({', '.join(lazy(a, k % 2 == 1) for k, a in enumerate(args))})"
    if name == "SWITCH" and args:
        rest = args[1:]
        parts = [_emit(args[0], phs, ctx)]
        for k, a in enumerate(rest):          # value1, result1, ..., and a default when the count is odd
            parts.append(lazy(a, k % 2 == 1 or (k == len(rest) - 1 and len(rest) % 2 == 1)))
        return f"SWITCH({', '.join(parts)})"

    def passes(a: Any) -> bool:
        return a[0] == "call" and a[1] in _REF_PASSING

    if name == "INDEX" and args:
        if args[0][0] == "ph":
            return f"as_ref({_call(name, args, phs, ctx)})"
        if not passes(args[0]):
            return _emit(node, phs, ctx)
    if name == "XLOOKUP" and len(args) >= 3:
        found = args[2]
        missing = args[3] if len(args) >= 4 else ("missing",)
        if found[0] == "ph" and missing[0] in ("ph", "missing", "num"):
            return f"as_ref({_call(name, args, phs, ctx)})"
        if found[0] != "ph" and not passes(found) and missing[0] != "ph" and not passes(missing):
            return _emit(node, phs, ctx)
    if name in _REF_PASSING:
        raise Untranslatable(f"{name} as an argument of {agg} gives a cell reference or a value depending on its "
                             f"arguments, and lift cannot tell which here; {agg} skips text only in a reference")
    return _emit(node, phs, ctx)


def _placeholder(ph: Any, ctx: _Ctx) -> str:
    spec = ctx.spec
    kind, arg = ph.kind, ph.arg
    parts = arg.split(".") if arg else []
    if kind == "in":
        iid = parts[0]
        if len(parts) == 2:
            return f"m.inp_{parts[1]}({iid!r})"
        return f"m.inp({iid!r})"
    if kind == "raw":
        return f"m.rawcol({parts[0]!r}, {parts[1]!r})"
    if kind == "h":
        table, col = parts
        ctx.deps.add(("hbase", table))
        if _is_derived(spec, table, col):
            ctx.deps.add(("hcol", table, col))
        return f"m.hcol({table!r}, {col!r})"
    if kind == "this":
        if _is_derived(spec, ctx.table, arg):
            ctx.deps.add(("hcol", ctx.table, arg))
        return f"r[{arg!r}]"
    if kind == "row":
        return "row"
    if kind == "col":
        return "col"
    if kind == "r":
        ctx.same_block.add(arg)
        return f"{_bvar(ctx.block)}.v({arg!r}, j)"
    if kind == "prev":
        return f"{_bvar(ctx.block)}.v({(arg or ctx.item)!r}, j - 1)"
    if kind == "m":
        ctx.same_block.add(arg)
        return f"{_bvar(ctx.block)}.at(i, {arg!r})"
    if kind in ("g", "grow", "gcol"):
        ctx.deps.add(("block", arg))
        method = {"g": "at(i, j)", "grow": "row_range(i)", "gcol": "col_range(j)"}[kind]
        return f"m.block({arg!r}).{method}"
    if kind == "b":
        bid, item = parts
        if bid == ctx.block:
            ctx.same_block.add(item)
            return f"{_bvar(bid)}.v({item!r}, j)"
        ctx.deps.add(("block", bid))
        if item == "total":
            return f"m.block({bid!r}).col_total(j)"
        return f"m.block({bid!r}).v({item!r}, j)"
    if kind == "blk":
        ctx.deps.add(("block", parts[0]))
        if len(parts) == 2:
            return f"m.block({parts[0]!r}).named({parts[1]!r})"
        return f"m.block({parts[0]!r}).body()"
    if kind == "cell":
        target = spec.key_outputs.get(arg) or {}
        if target.get("block"):
            ctx.deps.add(("block", target["block"]))
        return f"m.cell({arg!r})"
    if kind == "chk":
        ctx.deps.add(("check", arg))
        return f"m.check({arg!r})"
    raise Untranslatable(f"placeholder {ph.text} is not translated")


def _bvar(bid: str | None) -> str:
    return f"b_{bid}"


def _is_derived(spec: Any, table: str | None, col: str) -> bool:
    sheet = spec.helpers.get(table) if table else None
    return bool(sheet) and any(c["id"] == col for c in sheet.data.get("columns", []))


# ----------------------------------------------------------------------------- planning and writing

@dataclass
class _Step:
    key: tuple[str, ...]
    order: int
    lines: list[str]
    deps: set[tuple[str, ...]]
    name: str
    sheet: str
    reproduced: bool = True
    reason: str = ""


def _date_like(spec: Any, fmt: str | None, typ: str | None = None) -> bool:
    if typ == "date":
        return True
    if not fmt:
        return False
    if fmt in ("date", "month"):
        return True
    try:
        nf = spec.dictionary.number_format(fmt) if spec.dictionary is not None else ""
    except Exception:  # noqa: BLE001 - an unknown key is not a date format
        return False
    return bool(re.search(r"y|d|mmm", nf.lower())) and "[red]" not in nf.lower()


_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _script_value(v: Any, date_like: bool) -> Any:
    """A spec value as the script holds it (an ISO date as its serial when the item is a date)."""
    if v is None:
        return None
    if date_like and isinstance(v, str) and _ISO.match(v):
        return parse_date_text(v)
    return v


def _inputs_info(spec: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"check_tolerance": {"kind": "scalar", "value": spec.settings.get("check_tolerance", 0.001)}}
    for iid, entry in spec.inputs.items():
        if entry.get("generated"):
            continue
        item = entry["item"]
        k = entry["kind"]
        dl = _date_like(spec, item.get("format"), item.get("type"))
        if k == "scalar":
            out[iid] = {"kind": "scalar", "value": _script_value(item["value"], dl)}
        elif k == "list":
            out[iid] = {"kind": "list", "values": [_script_value(v, dl) for v in item["values"]]}
        else:
            rdl = _date_like(spec, item["rows"].get("format"))
            cdl = _date_like(spec, item["cols"].get("format"))
            out[iid] = {"kind": "grid", "values": [[_script_value(v, dl) for v in r] for r in item["values"]],
                        "rows": [_script_value(v, rdl) for v in item["rows"]["values"]],
                        "cols": [_script_value(v, cdl) for v in item["cols"]["values"]]}
    return out


def _snapshot_controls(spec: Any, table: str, snapshot: str, types: list[str],
                       expect: dict[str, Any], notes: list[str] | None = None) -> tuple[dict[str, Any], str | None]:
    """(expect, digest): the contract's expect with each control total replaced by the total accepted for the
    snapshot lift reads (raw/<table>.controls.json, lib.controls.lookup with the snapshot's digest and the table's
    agreed contract, written by check --accept-refresh --control or land --control), as build writes it into the
    inputs sheet; the contract's total where no total is accepted for that snapshot (v3, J-5). v9 (r8 verdict
    [A-aera-2, C-sol-1]): a total accepted under an earlier agreement is not used (the order of lib.controls); the
    contract's total is used and the note of lib.controls.stale_note is appended to `notes`. An invalid controls
    file is a common.Refusal, as in build."""
    import csv

    from . import controls, manifest as manmod
    path = spec.root / snapshot
    if not path.is_file():
        return expect, None
    try:
        digest = manmod.csv_digest(path, types)[0]
    except (OSError, UnicodeDecodeError, csv.Error):
        return expect, None
    totals = expect.get("totals") or []
    contract = spec.contracts.get(table)
    found, stale = controls.lookup(spec.root, table, digest, contract) if totals else (None, None)
    if stale is not None and notes is not None:
        notes.append(controls.stale_note(table, stale, contract))
    if not found:
        return expect, digest
    by_col = {str(t["column"]): t for t in found}
    new = []
    for t in totals:
        rec = by_col.get(t["column"])
        if rec is None:
            new.append(t)
        else:
            new.append(dict(t, value=rec["value"], tolerance=rec.get("tolerance", t.get("tolerance", 0)),
                            source=f"raw/{table}.controls.json, accepted for this snapshot (the contract's total: "
                                   f"{t['value']})"))
    return dict(expect, totals=new), digest


def _tables_info(spec: Any, manifest: dict[str, Any], notes: list[str] | None = None) -> dict[str, Any]:
    from . import manifest as manmod
    out: dict[str, Any] = {}
    for table in spec.raw:
        contract = spec.contracts.get(table) or {}
        cols = [c["name"] for c in contract.get("columns", [])]
        types = [c["type"] for c in contract.get("columns", [])]
        entry = manmod.raw_sheet(manifest, table) or {}
        if not cols:
            cols = list(entry.get("columns") or [])
            types = list(entry.get("types") or ["text"] * len(cols))
        never = [c["name"] for c in contract.get("columns", [])[1:] if c.get("null_rule") == "never"]
        known_nulls = {c["name"]: c["known_nulls"]["max"] for c in contract.get("columns", [])[1:]
                       if c.get("null_rule") == "never" and isinstance(c.get("known_nulls"), dict)}
        snapshot = entry.get("snapshot") or f"raw/{table}.csv"
        expect, digest = _snapshot_controls(spec, table, snapshot, types, contract.get("expect") or {}, notes)
        out[table] = {"columns": cols, "types": types, "key": list(contract.get("key") or []),
                      "expect": expect, "never_null": never, "snapshot": snapshot}
        if digest:
            out[table]["snapshot_digest"] = digest
        if known_nulls:
            out[table]["known_nulls"] = known_nulls
    return out


def _labels_code(spec: Any, block: dict[str, Any], dim: str) -> tuple[str, bool, tuple[str, ...] | None]:
    """(Python code for the row or column labels, whether they are a timeline, block they come from)."""
    node = block["data"].get(dim)
    if node is None:
        return '["Value"]', False, None
    if "values" in node:
        dl = _date_like(spec, node.get("format"))
        return repr([_script_value(v, dl) for v in node["values"]]), False, None
    if "in" in node:
        return f"m.list_values({node['in']!r})", False, None
    if "timeline" in node:
        t = node["timeline"]
        return f"m.timeline({t['start']!r}, {int(t['periods'])})", True, None
    if "same_as" in node:
        src = node["same_as"]
        attr = "row_labels" if dim == "rows" else "col_labels"
        timeline = _is_timeline(spec, src, dim)
        return f"m.block({src!r}).{attr}", timeline, ("block", src)
    return '["Value"]', False, None


def _is_timeline(spec: Any, bid: str, dim: str, seen: tuple[str, ...] = ()) -> bool:
    b = spec.blocks.get(bid)
    if b is None or bid in seen:
        return False
    node = b["data"].get(dim) or {}
    if "timeline" in node:
        return True
    if "same_as" in node:
        return _is_timeline(spec, node["same_as"], dim, seen + (bid,))
    return False


def _comment(text: str, width: int = 112) -> list[str]:
    text = " ".join(str(text).split())
    out, line = [], "#"
    for word in text.split(" "):
        if len(line) + 1 + len(word) > width and line != "#":
            out.append(line)
            line = "#  "
        line += " " + word
    out.append(line)
    return ["    " + x for x in out]


def _order_entries(entries: list[tuple[str, set[str]]], what: str) -> list[str]:
    """Ids in an order where every id comes after the ids it uses (stable; Refusal on a cycle)."""
    from . import common
    done: list[str] = []
    pending = list(entries)
    while pending:
        for k, (eid, deps) in enumerate(pending):
            if all(d in done or d == eid or d not in {e for e, _ in entries} for d in deps):
                done.append(eid)
                pending.pop(k)
                break
        else:
            raise common.Refusal(f"{what}: circular reference among " + ", ".join(e for e, _ in pending),
                                 fix="change the expressions so no item uses itself through others")
    return done


def _helper_steps(spec: Any, order: list[_Step]) -> None:
    for table, sheet in spec.helpers.items():
        contract = spec.contracts.get(table) or {}
        cols = [c["name"] for c in contract.get("columns", [])]
        types = {c["name"]: c["type"] for c in contract.get("columns", [])}
        fetch = list(sheet.data.get("fetch")) if sheet.data.get("fetch") is not None else cols
        text_cols = [c for c in fetch if types.get(c, "text") == "text"]
        lines = _comment(f"{sheet.name}: the fixed helper columns (design 7.1): row =ROW(), in_raw, and one "
                         f"fetch per raw column ({', '.join(fetch)})")
        lines.append(f"    h_{table} = m.helpers({table!r}, fetch={fetch!r}, text_columns={text_cols!r})")
        order.append(_Step(("hbase", table), len(order), lines, set(), f"{sheet.name} (fetched columns)", sheet.name))
        for col in sheet.data.get("columns", []):
            ctx = _Ctx("helpers", spec, table=table)
            label = f"{sheet.name}.{col['id']}"
            lines = _comment(f"{label}" + (f" ({col['label']})" if col.get("label") else "") + f": {col['expr']}")
            step = _Step(("hcol", table, col["id"]), len(order), lines, {("hbase", table)}, label, sheet.name)
            try:
                code = translate(col["expr"], ctx)
                lines.append(f"    h_{table}.compute({col['id']!r}, lambda r: {code})")
            except Untranslatable as exc:
                step.reproduced, step.reason = False, str(exc)
                lines.append(f"    h_{table}.not_reproduced({col['id']!r}, {str(exc)!r})")
            step.deps |= ctx.deps
            step.deps.discard(step.key)
            order.append(step)


def _block_steps(spec: Any, order: list[_Step]) -> None:
    for bid, b in spec.blocks.items():
        data, shape, sheet = b["data"], b["shape"], b["sheet"]
        var = _bvar(bid)
        title = data.get("title") or bid
        name = f"{sheet}.{bid}"
        deps: set[tuple[str, ...]] = set()
        lines = _comment(f"{name} ({shape} block: {title})")
        step = _Step(("block", bid), len(order), lines, deps, name, sheet)
        problems: list[str] = []
        if shape == "grid":
            rows, _t, rdep = _labels_code(spec, b, "rows")
            cols, timeline, cdep = _labels_code(spec, b, "cols")
            total = data.get("total") or {}
            for d in (rdep, cdep):
                if d:
                    deps.add(d)
            lines.append(f"    {var} = m.grid({bid!r}, {sheet!r}, rows={rows}, cols={cols}, "
                         f"total_row={bool(total.get('row'))}, total_col={bool(total.get('col'))}, "
                         f"timeline={timeline})")
            lines += _comment(f"every body cell: {data['cell']}")
            ctx = _Ctx("grid", spec, block=bid)
            try:
                code = translate(data["cell"], ctx)
                lines.append(f"    {var}.fill(lambda i, j, row, col: {code})")
            except Untranslatable as exc:
                problems.append(str(exc))
                lines.append(f"    {var}.not_reproduced({str(exc)!r})")
            deps |= ctx.deps
        elif shape == "items":
            cols, timeline, cdep = _labels_code(spec, b, "cols")
            if cdep:
                deps.add(cdep)
            items = [(i["id"], i["label"]) for i in data["items"]]
            lines.append(f"    {var} = m.items({bid!r}, {sheet!r}, items={items!r}, cols={cols}, timeline={timeline})")
            entries: list[tuple[str, set[str]]] = []
            code_of: dict[str, list[str]] = {}
            for item in data["items"]:
                ctx = _Ctx("items", spec, block=bid, item=item["id"])
                try:
                    code = translate(item["expr"], ctx)
                    first = translate(item["first"], ctx) if "first" in item else None
                    code_of[item["id"]] = _comment(f"{item['id']}: {item['expr']}"
                                                   + (f"; first column: {item['first']}" if "first" in item else ""))
                    code_of[item["id"]].append(f"        ({item['id']!r}, lambda j, col: {code}, "
                                               + (f"lambda j, col: {first}" if first else "None") + "),")
                except Untranslatable as exc:
                    problems.append(f"item {item['id']}: {exc}")
                    code_of[item["id"]] = [f"        ({item['id']!r}, lambda j, col: NOT_REPRODUCED({str(exc)!r}), None),"]
                deps |= ctx.deps
                entries.append((item["id"], set(ctx.same_block)))
            lines.append(f"    {var}.fill_items([")
            for iid in _order_entries(entries, f"block '{bid}'"):
                lines += ["    " + x if x.lstrip().startswith("#") else x for x in code_of[iid]]
            lines.append("    ])")
        else:
            rows, _t, rdep = _labels_code(spec, b, "rows")
            if rdep:
                deps.add(rdep)
            measures = [(mm["id"], mm["label"]) for mm in data["measures"]]
            lines.append(f"    {var} = m.measures({bid!r}, {sheet!r}, rows={rows}, measures={measures!r})")
            entries = []
            code_of = {}
            for meas in data["measures"]:
                ctx = _Ctx("measures", spec, block=bid)
                total = meas.get("total", "none")
                try:
                    code = translate(meas["expr"], ctx)
                    code_of[meas["id"]] = _comment(f"{meas['id']}: {meas['expr']} (total: {total})")
                    code_of[meas["id"]].append(f"        ({meas['id']!r}, lambda i, row: {code}, {total!r}),")
                except Untranslatable as exc:
                    problems.append(f"measure {meas['id']}: {exc}")
                    code_of[meas["id"]] = [f"        ({meas['id']!r}, lambda i, row: NOT_REPRODUCED({str(exc)!r}), "
                                           f"{total!r}),"]
                deps |= ctx.deps
                entries.append((meas["id"], set(ctx.same_block)))
            lines.append(f"    {var}.fill_measures([")
            for mid in _order_entries(entries, f"block '{bid}'"):
                lines += ["    " + x if x.lstrip().startswith("#") else x for x in code_of[mid]]
            lines.append("    ])")
        lines.append(f"    {var}.finish()")
        deps.discard(("block", bid))
        if problems:
            step.reproduced, step.reason = False, "; ".join(problems)
        order.append(step)


def _check_steps(spec: Any, order: list[_Step]) -> None:
    keys = {"equal": ("actual", "expected"), "range": ("actual", "min", "max"), "zero": ("actual",),
            "at_most": ("actual", "bound")}
    method = {"equal": "check_equal", "range": "check_range", "zero": "check_zero", "at_most": "check_at_most"}
    for cid, entry in spec.checks.items():
        chk = entry["data"]
        kind = chk["kind"]
        ctx = _Ctx("check", spec)
        lines = _comment(f"check {cid} ({kind}): {chk['label']}: "
                         + "; ".join(f"{k} {chk[k]}" for k in keys[kind]))
        step = _Step(("check", cid), len(order), lines, set(), f"check {cid}", entry["sheet"])
        args = []
        try:
            for k in keys[kind]:
                v = chk[k]
                args.append(f"lambda: {translate(v, ctx) if isinstance(v, str) else repr(v)}")
        except Untranslatable as exc:
            step.reproduced, step.reason = False, str(exc)
            args = [f"lambda: NOT_REPRODUCED({str(exc)!r})"] * len(keys[kind])
        if kind == "equal":
            args.append(f"lambda: m.inp({chk.get('tolerance', 'check_tolerance')!r})")
        severity = chk.get("severity", "error")
        lines.append(f"    m.{method[kind]}({cid!r}, " + ", ".join(args)
                     + f", severity={severity!r}, label={chk['label']!r})")
        step.deps |= ctx.deps
        order.append(step)
    for table in spec.raw:
        deps = {("hbase", table)} if table in spec.helpers else set()
        lines = _comment(f"the checks build adds for raw table '{table}' (design 9.2): rows, control totals, "
                         "empty cells, coverage, key uniqueness")
        lines.append(f"    m.raw_checks({table!r})")
        order.append(_Step(("rawchecks", table), len(order), lines, deps, f"checks of raw table {table}", "checks"))


def _sort_steps(steps: list[_Step]) -> list[_Step]:
    """Dependencies first, otherwise the specification's order."""
    from . import common
    by_key = {s.key: s for s in steps}
    for s in steps:
        resolved = set()
        for d in s.deps:
            if d[0] == "check" and d not in by_key:
                for t in by_key:
                    if t[0] == "rawchecks" and d[1].startswith(t[1] + "_"):
                        resolved.add(t)
            elif d in by_key:
                resolved.add(d)
        s.deps = resolved
    done: list[_Step] = []
    placed: set[tuple[str, ...]] = set()
    pending = sorted(steps, key=lambda s: s.order)
    while pending:
        for k, s in enumerate(pending):
            if s.deps <= placed:
                done.append(s)
                placed.add(s.key)
                pending.pop(k)
                break
        else:
            raise common.Refusal("the specification has a circular reference among: "
                                 + ", ".join(s.name for s in pending),
                                 fix="change the expressions so that no block or helper column uses itself "
                                     "through others")
    return done


def generate_script(spec: Any, manifest: dict[str, Any], *, workbook: Path,
                    notes: list[str] | None = None) -> tuple[str, list[_Step]]:
    """(script text, steps) for a loaded spec and its workbook's manifest. `notes` (v9): the notes for lift's
    report are appended to it (a control total accepted under an earlier agreement, _snapshot_controls)."""
    from . import common
    steps: list[_Step] = []
    _helper_steps(spec, steps)
    _block_steps(spec, steps)
    _check_steps(spec, steps)
    ordered = _sort_steps(steps)
    info = {"name": spec.name, "title": spec.title, "spec": common.relpath_posix(spec.path, spec.root),
            "spec_sha256": spec.sha256, "workbook": common.relpath_posix(workbook, spec.root),
            "model_dir": spec.root.as_posix(), "tolerance": float(spec.settings.get("check_tolerance", 0.001)),
            "lifted_at": common.now_iso(), "plugin_version": common.VERSION,
            "tables": _tables_info(spec, manifest, notes), "inputs": _inputs_info(spec)}
    body = ["def build_model(m):",
            '    """The model, step by step, in the specification\'s order (a step comes after the steps it',
            '    uses). Each step names its spec expression; r is one helper row, row/col are block labels."""']
    for s in ordered:
        body.append("")
        body += s.lines
    body += ["", "    m.master_check()"]
    not_repr = [s for s in ordered if not s.reproduced]
    header = _script_header(spec, info, ordered, not_repr)
    text = (header + "\n" + runtime_source() + "\n\n" + "# " + "=" * 110 + "\n# The model\n# " + "=" * 110
            + "\n\nINFO = " + pprint.pformat(info, width=110, sort_dicts=False) + "\n\n\n"
            + "def default_model_dir():\n"
            + '    """The folder above this script\'s folder when it is a model folder, else the one it was '
              'written for."""\n'
            + "    here = _Path(__file__).resolve().parent.parent\n"
            + '    return here if (here / "rotm_model.json").is_file() else _Path(INFO["model_dir"])\n\n\n'
            + "\n".join(body) + "\n\n\n"
            + 'if __name__ == "__main__":\n    _sys.exit(main())\n')
    return text, ordered


def _script_header(spec: Any, info: dict[str, Any], steps: list[_Step], not_repr: list[_Step]) -> str:
    lines = [f'"""{_doc(_safe_name(info["name"]))}{SCRIPT_SUFFIX}: the model "{_doc(spec.title)}" computed in Python and '
             'tied out to the workbook.',
             "",
             f"Written by rule-of-thumb-model {info['plugin_version']} (rotm.py lift) on {info['lifted_at']}.",
             f"Specification: {_doc(info['spec'])} (sha256 {info['spec_sha256'][:16]}...). "
             f"Workbook: {_doc(info['workbook'])}.",
             "",
             "What it does, in order:",
             "1. reads each raw table from raw/<table>.csv with the column types of its contract",
             "   (--raw workbook reads the workbook's raw sheets instead, e.g. after a Power Query refresh);",
             f"2. computes the {len(steps)} steps below: helper columns, blocks and checks, each after the steps it uses;",
             "3. reads the values Excel saved in the workbook and prints a tie-out table:",
             "   cell, workbook value, script value, difference.",
             "",
             f"Two numbers tie out when their difference is at most the model's check tolerance ({info['tolerance']:g},",
             f"absolute) plus {REL_TOLERANCE:g} times the larger value (the rounding of double-precision arithmetic).",
             "The table prints each step's largest difference over every compared cell, ties included.",
             "",
             "Exit codes: 0 every compared value ties out;",
             "1 a value differs or a step is not reproduced; 2 bad arguments;",
             "3 not verified (the workbook was not recalculated in Excel, or a file is missing).",
             "",
             "Run:   python <this file> [--all] [--json FILE] [--csv DIR] [--raw workbook] [--inputs workbook]",
             "       --all prints every compared cell; --json FILE holds one record per compared cell.",
             "Use:   from this script import run_model; m, _ = run_model(); m.frames()  (pandas data frames)",
             "",
             "Dates are Excel serial day numbers inside the model (1900 date system), as in the workbook.",
             "Control totals: those accepted for the snapshot in raw/<table>.controls.json under the agreement in",
             "force when this script was written (rotm.py check --accept-refresh --control or rotm.py land --control),",
             "else the contract's.",
             "Needs Python 3.10 or later and openpyxl (to read the workbook's values); pandas only for frames().",
             ]
    if not_repr:
        lines += ["", "Steps this script does not reproduce (their cells show 'not reproduced'; the script exits 1):"]
        lines += [f"- {_doc(s.name)}: {_doc(s.reason)}" for s in not_repr]
    lines += ['"""', "from __future__ import annotations", ""]
    return "\n".join(lines)


def _safe_name(name: str) -> str:
    out = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_")
    return out or "model"


def _reason_named(reason: str, names: tuple[str, ...]) -> bool:
    """True when every part of a step's reason is a function that a LIFT-UNSUPPORTED finding names already
    ("LET is not translated by lift"; a block's parts start with "item <id>: " or "measure <id>: ")."""
    if not names or not reason:
        return False
    parts = [re.sub(r"^(item|measure) [^:]+: ", "", p) for p in reason.split("; ")]
    return all(p.startswith(names) for p in parts)


def lift_model(target: str | Path, *, out: str | Path | None = None, lang: str = "python",
               run: bool = False) -> Any:
    """`target`: a spec or a model folder. Writes lift/<name>_lift.py; with `run`, runs it and puts the tie-out
    table in the report. Returns a common.Report."""
    from . import common, manifest as manmod, spec as specmod
    if lang == "r":
        raise common.EnvUnavailable(
            "R output is not available in this version: no R was installed where the plugin was built and tested, "
            "so an R script could not be tested (design 15: R output only if it can be tested). Nothing was written",
            fix="use --lang python; the Python script reads the same raw CSV files")
    if lang != "python":
        raise common.Refusal(f"unknown language '{lang}'", fix="use --lang python")
    sp = specmod.load_spec(target, require_agreed=False)
    workbook = sp.output
    if not workbook.is_file():
        raise common.Refusal(f"{workbook.name} not found in {sp.root.as_posix()}",
                             fix=f'build it first: rotm.py build "{sp.path.as_posix()}"')
    man = manmod.load_manifest(workbook)
    if man is None:
        raise common.Refusal(f"{manmod.manifest_path(workbook).name} not found next to {workbook.name}; lift reads "
                             "the cell positions from it", fix=f'rebuild: rotm.py build "{sp.path.as_posix()}"')
    out_path = common.native_path(out) if out else sp.root / "lift" / f"{_safe_name(sp.name)}{SCRIPT_SUFFIX}"
    out_path = Path(os.path.abspath(out_path))
    report = common.Report("lift", common.norm_path(out_path))
    # v3, A-aera-13: before the script is written, every function of the model that the evaluator does not
    # reproduce is named with its cell count and first cell, so the tie-out is not where the user learns it.
    unsupported = unsupported_functions(sp, man)
    for u in unsupported:
        where = f"{u['sheet']}!{u['cell']}" if u["cell"] else u["sheet"]
        report.error("LIFT-UNSUPPORTED", f"the lift evaluator does not reproduce {u['function']}: {u['cells']:,} "
                     f"cell(s), first {where}", UNSUPPORTED_FIX, sheet=u["sheet"], cell=u["cell"],
                     count=max(1, u["cells"]), data={"function": u["function"], "cells": u["cells"],
                                                     "steps": list(u["steps"])})
    lift_notes: list[str] = []
    text, steps = generate_script(sp, man, workbook=workbook, notes=lift_notes)
    for n in lift_notes:
        report.note(n)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, out_path)
    reproduced = [s for s in steps if s.reproduced]
    report.data = {"script": common.norm_path(out_path), "model": sp.root.as_posix(), "workbook": workbook.as_posix(),
                   "steps": [{"step": s.name, "reproduced": s.reproduced, "reason": s.reason} for s in steps],
                   "reproduced": len(reproduced), "not_reproduced": len(steps) - len(reproduced),
                   "unsupported": [{"function": u["function"], "cells": u["cells"],
                                    "first": f"{u['sheet']}!{u['cell']}" if u["cell"] else u["sheet"]}
                                   for u in unsupported]}
    named = tuple(f"{u['function']} " for u in unsupported)
    for s in steps:
        if not s.reproduced and not _reason_named(s.reason, named):   # LIFT-UNSUPPORTED names it already
            report.error("L-NOT-REPRODUCED", f"{s.name} is not reproduced: {s.reason}",
                         "the script shows this step's cells as 'not reproduced' and exits 1; write the step with "
                         "functions the script covers (references/functions.md lists the model's functions)",
                         sheet=s.sheet)
    report.note(f"wrote {common.relpath_posix(out_path, sp.root)}: {len(steps)} steps, {len(reproduced)} reproduced")
    if run:
        _run_and_report(out_path, workbook, report)
        if unsupported and report.exit_code == common.EXIT_NOT_AVAILABLE:
            report.finish()          # LIFT-UNSUPPORTED is known without the tie-out: exit 1, not 3 (not verified)
        return report
    report.note(f"run it: python \"{out_path.as_posix()}\" (prints the tie-out against {workbook.name})")
    report.finish()
    return report


def _run_script(script: Path, workbook: Path | None = None) -> tuple[int, dict[str, Any] | None, str]:
    fd, json_path = tempfile.mkstemp(prefix="rotm_lift_", suffix=".json")
    os.close(fd)
    try:
        cmd = [sys.executable, "-X", "utf8", "-B", str(script), "--json", json_path]
        if workbook is not None:
            cmd += ["--workbook", str(workbook)]
        env = dict(os.environ, PYTHONUTF8="1")
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=RUN_TIMEOUT_S, env=env)
        data = None
        try:
            raw = Path(json_path).read_text(encoding="utf-8")
            if raw.strip():
                import json
                data = json.loads(raw)
        except (OSError, ValueError):
            data = None
        return proc.returncode, data, (proc.stdout or "") + (proc.stderr or "")
    finally:
        try:
            os.unlink(json_path)
        except OSError:
            pass


def _run_and_report(script: Path, workbook: Path, report: Any) -> None:
    """Run the script; if the workbook holds no values saved by Excel, recalculate a temporary copy in Excel
    (the model's workbook is not changed) and run it against the copy."""
    from . import common
    code, data, output = _run_script(script)
    used = workbook
    if code == 3 and (data is None or data.get("status") == "not_verified"):
        copy = _recalculated_copy(workbook, report)
        if copy is not None:
            try:
                code, data, output = _run_script(script, copy)
                used = copy
            finally:
                _remove_temporary_folder(copy.parent, report)     # v4, J-2: the folder with all its contents
    report.data["run"] = {"exit_code": code, "workbook": used.as_posix(), "output": output[-6000:]}
    if data is None:
        line = (output.strip().splitlines() or ["no output"])[-1]
        if code == 3:
            report.note(f"not verified: {line}")
            report.finish(not_available=True)
        else:
            report.error("L-RUN", f"the script did not finish (exit {code}): {line[:300]}",
                         "run it by hand to see the message; report it with the script")
            report.finish()
        return
    report.data["tie_out"] = {k: data.get(k) for k in ("status", "tolerance", "relative_tolerance", "max_difference",
                                                        "max_difference_cell", "counts", "steps", "notes")}
    report.verified["tied_out"] = data["status"] == "tie"
    report.verified["recalculated"] = bool(data.get("recalculated"))
    for n in data.get("notes", []):
        report.note(n)
    if data["status"] == "not_verified":
        report.note("not verified: the workbook holds no values saved by Excel and Excel is not available; run "
                    "rotm.py check <model> on a machine with Excel, then rotm.py lift <model> --run")
        report.finish(not_available=True)
        return
    by_step: dict[str, list[dict[str, Any]]] = {}
    for c in data.get("cells", []):
        if c["status"] != "tie":
            by_step.setdefault(c["step"], []).append(c)
    for s in data["steps"]:
        bad = by_step.get(s["step"], [])
        if s.get("problem"):
            report.error("L-LAYOUT", f"{s['step']}: {s['problem']}",
                         "rebuild the workbook from this specification (rotm.py build), then lift again",
                         sheet=s["sheet"] or None)
        if s["differ"] and bad:
            first = bad[0]
            gaps = [abs(c["difference"]) for c in bad if c.get("difference") is not None]
            largest = f"largest difference of a differing cell {max(gaps):.6g}; " if gaps else ""
            report.add(common.Finding(
                "L-DIFF", "error",
                f"{s['step']}: {s['differ']} of {s['cells']} cells differ between the workbook and the script "
                f"({largest}first {first['cell']}: workbook {first['workbook']}, script {first['script']})",
                "find why they disagree: an input changed in the workbook (run the script with --inputs workbook), "
                "a raw sheet that differs from raw/<table>.csv (--raw workbook), or a formula edited by hand "
                "(rebuild with rotm.py build)",
                sheet=s["sheet"] or None, cell=first["cell"].split("!")[-1], count=s["differ"],
                cells=[c["cell"].split("!")[-1] for c in bad[:20]]))
        if s["not reproduced"]:
            # v7, A-aera-12: the reason of the first such cell (for example a PROPER text with a character listed
            # in _PROPER_NOT_REPRODUCED), so the formula is named with what the script does not reproduce
            why = f" (first: {s['not_reproduced_reason']})" if s.get("not_reproduced_reason") else ""
            report.add(common.Finding(
                "L-NOT-REPRODUCED", "error", f"{s['step']}: {s['not reproduced']} of {s['cells']} cells are not "
                f"reproduced by the script{why}", "see the step's reason in the script's header",
                sheet=s["sheet"] or None, count=s["not reproduced"]))
    c = data["counts"]
    t, r, d = data["tolerance"], data.get("relative_tolerance", 0), data.get("max_difference")
    largest = f"{d:.6g} at {data.get('max_difference_cell')}" if d is not None else "- (no number was compared)"
    report.note(f"tie-out: {c['tie']:,} of {c['cells']:,} cells tie out; {c['differ']:,} differ; "
                f"{c['not reproduced']:,} not reproduced (tolerance {t:g} absolute plus {r:g} times the larger "
                f"value; largest difference {largest})")
    report.finish()


def _remove_temporary_folder(folder: Path, report: Any) -> None:
    """Remove the temporary folder of _recalculated_copy with all its contents (v4, J-2). A second attempt after
    one second covers a file Excel releases late; a folder that still cannot be removed is named in a note."""
    import shutil
    import time
    for attempt in (1, 2):
        try:
            shutil.rmtree(folder)
            return
        except FileNotFoundError:
            return
        except OSError as exc:
            if attempt == 2:
                report.note(f"the temporary folder {folder.as_posix()} could not be removed ({type(exc).__name__}: "
                            f"{str(exc)[:200]}); delete it by hand")
                return
            time.sleep(1.0)


def _recalculated_copy(workbook: Path, report: Any) -> Path | None:
    """A recalculated copy of the workbook (and its manifest) in a temporary folder, or None without Excel. On
    every path that returns None or raises, the folder is removed here; the caller removes it after the run."""
    import shutil

    from . import manifest as manmod
    try:
        from . import excel
        status = excel.excel_status()
    except Exception as exc:  # noqa: BLE001 - Excel missing is a 'not verified', not a crash
        report.note(f"Excel is not available ({type(exc).__name__}); the workbook was not recalculated")
        return None
    if not status.available:
        report.note(f"Excel is not available ({status.reason}); the workbook was not recalculated")
        return None
    folder = Path(tempfile.mkdtemp(prefix="rotm_lift_"))
    keep = False
    try:
        copy = folder / workbook.name
        shutil.copy2(workbook, copy)
        mpath = manmod.manifest_path(workbook)
        if mpath.is_file():
            shutil.copy2(mpath, manmod.manifest_path(copy))
        try:
            res = excel.recalc(copy, save=True)
        except Exception as exc:  # noqa: BLE001
            report.note(f"the copy could not be recalculated in Excel ({type(exc).__name__}: {str(exc)[:200]})")
            return None
        if not res.ok:
            report.note("the copy could not be recalculated in Excel: " + "; ".join(res.errors)[:300])
            return None
        report.note(f"{workbook.name} holds no values saved by Excel; a temporary copy was recalculated in Excel "
                    f"for the tie-out ({workbook.name} itself was not changed)")
        keep = True
        return copy
    finally:
        if not keep:
            _remove_temporary_folder(folder, report)


def add_lift_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("target", help="the model specification or the model folder")
    p.add_argument("-o", "--out", help="output script (default <model>/lift/<name>_lift.py)")
    p.add_argument("--lang", choices=LANGS, default="python",
                   help="python (default); r is refused: no R was available to test an R script")
    p.add_argument("--run", action="store_true", help="run the script and print the tie-out")
    p.add_argument("--strict", action="store_true", help="exit 1 on any warning too")
    p.add_argument("--json", metavar="FILE", help="write the report as JSON ('-' = stdout)")


def run_lift(args: argparse.Namespace) -> int:
    report = lift_model(args.target, out=args.out, lang=args.lang, run=args.run)
    if getattr(args, "strict", False) and report.status in ("pass", "fail"):
        report.finish(True)
    if args.run and report.data.get("run", {}).get("output"):
        stream = sys.stderr if args.json == "-" else sys.stdout
        stream.write(report.data["run"]["output"].rstrip() + "\n")
    return report.emit(args.json)

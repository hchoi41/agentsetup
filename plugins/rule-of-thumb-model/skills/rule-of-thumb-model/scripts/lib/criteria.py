"""Labels that Excel's criteria matching merges (v4, A-aera-1): rule D-CRITERIA.

Owner: P3 (v4). openpyxl is imported inside functions only.

Excel's SUMIFS, COUNTIFS, AVERAGEIFS, MAXIFS, MINIFS, SUMIF, COUNTIF and AVERAGEIF do not compare a criterion with
a cell by exact text. excel_criteria_match() says whether they count a cell value for a criterion. Its rules were
measured with Excel 16.0 on the build PC on 2026-11-25 (COUNTIFS, COUNTIF and SUMIFS on 152 + 61 pairs and VALUE on
87 texts; scripts and results in build/v4/P3/ outside the plugin):

- A criterion that reads as a number (a number, or a text such as '001', '1.0', ' 1', '1e0', '1,000', '$1', '(1)',
  '1%', '2024-01-31', '1/31/2024', '12:00') matches number cells of that value and text cells that read as the same
  number. Numbers are compared on their first 15 significant digits, as Excel stores them, so the texts
  '1234567890123456' and '1234567890123457' read as the same number. A month and day without a year ('1/2', '1-30')
  read as a date of the current year.
- A text criterion matches text cells only, without case ('North' matches 'north'; 'Straße' matches 'STRASSE').
  When it holds * or ?, they are wildcards (* any text, also none; ? one character); ~* and ~? are a literal * and ?,
  a ~ at the end is dropped. Without * or ? a ~ is an ordinary character.
- The texts TRUE and FALSE (any case) match the logical values only, not the texts TRUE and FALSE.
- A criterion that starts with =, <>, <, >, <= or >= is an operator: = and <> with a number compare numbers only
  for <> (a text cell is never equal to a number there) and read texts as numbers for =; <, >, <= and >= compare
  numbers with numbers and texts with texts (without case); '=' alone matches empty cells, '<>' alone every cell
  that is not empty.
- An empty criterion cell counts as the number 0.

Not modelled (Excel's answer may differ): the order of texts for <, >, <= and >= follows the code points after case
folding, while Excel sorts letters with accents beside their base letter ('é' before 'z'); month names other than
English ('Jan', 'January'); dates in other orders than year-month-day, month/day/year and month/day; digits other
than 0-9 and the Arabic-Indic digits; currency signs other than $ (in a cell value also the euro sign).

criteria_findings() reports, for every criterion a formula of the workbook passes to one of these functions (the
row and column labels of a block, input-list items and other label cells, and typed criteria) and every distinct
value of its criteria range that is not exactly equal to it, the pairs Excel would add together (D-CRITERIA,
warning). A typed criterion that starts with an operator or holds a wildcard is taken as meant and is not compared.
Without saved values it covers criteria ranges that are fetch columns of a raw text column (and raw text columns
themselves), with the values of the raw sheet; with saved values it also covers the other helper columns (and the
other raw columns), with the values Excel saved.
"""
from __future__ import annotations

import datetime as dt
import re
import warnings
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from . import common

RULE = "D-CRITERIA"
FUNCTIONS = ("SUMIFS", "COUNTIFS", "AVERAGEIFS", "MAXIFS", "MINIFS", "SUMIF", "COUNTIF", "AVERAGEIF")
MAX_VALUES_PER_LABEL = 5        # warnings for one label of one block and one criteria column
MAX_LINK_HOPS = 10              # links followed from a criterion cell to the cell that holds the label
EXCEL_MAX_NUMBER = Decimal("9.99999999999999E+307")
FIX = ("make the labels unambiguous: change the contract's source expression of {column} (for example keep a code "
       "at a fixed length with its leading zeros, or put a letter in front of it), or match on a helper column that "
       "holds an unambiguous key; land again, show and agree the contract, and rebuild")

_FUNC_RE = re.compile(r"\b(?:_xlfn\.)?(?:SUMIFS?|COUNTIFS?|AVERAGEIFS?|MAXIFS|MINIFS)\s*\(", re.IGNORECASE)
_OPERATORS = ("<=", ">=", "<>", "<", ">", "=")


# ----------------------------------------------------------------------------- texts that read as numbers

_ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_CORE = r"(?:[0-9]{1,3}(?:,[0-9]{3,})+|[0-9]+)(?:\.[0-9]*)?(?:[eE][+-]?[0-9]+)?|\.[0-9]+(?:[eE][+-]?[0-9]+)?"
_NUMBER_RE = {
    side: re.compile(r"^(?P<pct1>% *)?(?P<sign>[+-] *)?(?P<cur>" + cur + r" *(?P<sign2>[+-] *)?)?"
                     r"(?:\( *(?P<pcore>" + _CORE + r") *\)|(?P<core>" + _CORE + r")) *(?P<pct2>%)?$")
    for side, cur in (("criterion", r"\$"), ("value", r"[$€]"))}
_FRACTION_RE = re.compile(r"^([0-9]+) +([0-9]+)/([0-9]+)$")
_TIME = r"([0-9]{1,4}):([0-9]{2})(?::([0-9]{2}(?:\.[0-9]+)?))?(?: *([AaPp][Mm]))?"
_TIME_RE = re.compile("^" + _TIME + "$")
_MONTHS = {name: i for i, names in enumerate(
    (("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",), ("jun", "june"),
     ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
     ("dec", "december")), 1) for name in names}
_DATE_FORMS = (
    (re.compile(r"^([0-9]{4})-([0-9]{1,2})-([0-9]{1,2})$"), "ymd"),
    (re.compile(r"^([0-9]{4})/([0-9]{1,2})/([0-9]{1,2})$"), "ymd"),
    (re.compile(r"^([0-9]{1,2})/([0-9]{1,2})/([0-9]{4})$"), "mdy"),
    (re.compile(r"^([0-9]{1,2})[/-]([0-9]{1,2})$"), "md"),
    (re.compile(r"^([A-Za-z]+) +([0-9]{4})$"), "my"),
    (re.compile(r"^([0-9]{1,2}) +([A-Za-z]+) +([0-9]{4})$"), "dmy"),
)
_EPOCH = dt.date(1899, 12, 30)


def _sig15(d: Decimal) -> Decimal:
    """`d` cut to its first 15 significant digits (Excel keeps 15)."""
    if d == 0:
        return Decimal(0)
    return d.quantize(Decimal(1).scaleb(d.adjusted() - 14), rounding=ROUND_DOWN)


def _serial(d: dt.date) -> int | None:
    """Excel's 1900 date serial number (with its 29 February 1900: dates before 1 March 1900 are one lower)."""
    if not (1900 <= d.year <= 9999):
        return None
    if d < dt.date(1900, 3, 1):
        return (d - dt.date(1899, 12, 31)).days
    return (d - _EPOCH).days


def _date_of(text: str) -> int | None:
    for regex, form in _DATE_FORMS:
        m = regex.match(text)
        if not m:
            continue
        try:
            if form == "ymd":
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            elif form == "mdy":
                mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            elif form == "md":
                mo, d, y = int(m.group(1)), int(m.group(2)), dt.date.today().year
            elif form == "my":
                mo, d, y = _MONTHS.get(m.group(1).lower(), 0), 1, int(m.group(2))
            else:
                d, mo, y = int(m.group(1)), _MONTHS.get(m.group(2).lower(), 0), int(m.group(3))
            return _serial(dt.date(y, mo, d))
        except ValueError:
            return None
    return None


def _time_of(text: str) -> Decimal | None:
    m = _TIME_RE.match(text)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    s = Decimal(m.group(3)) if m.group(3) else Decimal(0)
    ampm = (m.group(4) or "").lower()
    if mi > 59 or s >= 60:
        return None
    if ampm:
        if not 1 <= h <= 12:
            return None
        h = (h % 12) + (12 if ampm == "pm" else 0)
    return (Decimal(h) * 3600 + Decimal(mi) * 60 + s) / Decimal(86400)


def text_number(text: str, *, side: str = "criterion") -> Decimal | None:
    """The number Excel's criteria matching reads in `text` (cut to 15 significant digits), or None when it reads
    none. `side` is "criterion" or "value" (a cell value also reads a leading euro sign)."""
    s = str(text).translate(_ARABIC_INDIC).strip(" ")
    if not s:
        return None
    m = _NUMBER_RE[side].match(s)
    if m:
        if (m.group("pct1") and m.group("pct2")) or (m.group("sign") and m.group("sign2")) \
                or (m.group("pcore") and (m.group("sign") or m.group("sign2"))):
            return None
        core = (m.group("pcore") or m.group("core")).replace(",", "")
        try:
            value = Decimal(core)
        except InvalidOperation:
            return None
        if not value.is_finite() or abs(value) > EXCEL_MAX_NUMBER:
            return None
        negative = (m.group("sign") or "").strip() == "-" or (m.group("sign2") or "").strip() == "-" \
            or bool(m.group("pcore"))
        if negative:
            value = -value
        if m.group("pct1") or m.group("pct2"):
            value = value / 100
        return _sig15(value)
    m = _FRACTION_RE.match(s)
    if m:
        whole, num, den = (int(x) for x in m.groups())
        return _sig15(Decimal(whole) + Decimal(num) / Decimal(den)) if den else None
    t = _time_of(s)
    if t is not None:
        return _sig15(t)
    date_part, _, time_part = s.partition(" ")
    serial = _date_of(s)
    if serial is not None:
        return Decimal(serial)
    if time_part:
        serial = _date_of(date_part)
        t = _time_of(time_part.strip(" "))
        if serial is not None and t is not None:
            return _sig15(Decimal(serial) + t)
    return None


def _cell_number(v: Any) -> Decimal | None:
    """A number cell's value (dates and times as Excel's serial numbers); None for any other value."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, dt.datetime):
        serial = _serial(v.date())
        if serial is None:
            return None
        secs = v.hour * 3600 + v.minute * 60 + v.second + Decimal(v.microsecond) / Decimal(1_000_000)
        return _sig15(Decimal(serial) + Decimal(secs) / Decimal(86400))
    if isinstance(v, dt.date):
        serial = _serial(v)
        return Decimal(serial) if serial is not None else None
    if isinstance(v, dt.time):
        return _sig15((Decimal(v.hour * 3600 + v.minute * 60 + v.second)) / Decimal(86400))
    if isinstance(v, (int, float)):
        try:
            d = Decimal(repr(v)) if isinstance(v, float) else Decimal(v)
        except InvalidOperation:
            return None
        return _sig15(d) if d.is_finite() else None
    return None


# ----------------------------------------------------------------------------- the match

@dataclass(frozen=True)
class _Criterion:
    op: str                     # "" (none), "=", "<>", "<", ">", "<=", ">="
    kind: str                   # number | bool | text | empty
    number: Decimal | None = None
    boolean: bool | None = None
    text: str | None = None


def _criterion(c: Any) -> _Criterion:
    if isinstance(c, str) and not c:
        c = None
    if c is None:
        return _Criterion("", "number", number=Decimal(0))          # an empty criterion cell counts as 0
    if isinstance(c, bool):
        return _Criterion("", "bool", boolean=c)
    if not isinstance(c, str):
        n = _cell_number(c)
        return _Criterion("", "number", number=n) if n is not None else _Criterion("", "text", text=str(c))
    op, operand = "", c
    for o in _OPERATORS:
        if c.startswith(o):
            op, operand = o, c[len(o):]
            break
    if operand == "":
        return _Criterion(op, "empty")
    n = text_number(operand, side="criterion")
    if n is not None:
        return _Criterion(op, "number", number=n)
    if operand.casefold() in ("true", "false"):
        return _Criterion(op, "bool", boolean=operand.casefold() == "true")
    return _Criterion(op, "text", text=operand)


def _pattern(p: str) -> re.Pattern[str]:
    out: list[str] = []
    i = 0
    while i < len(p):
        ch = p[i]
        if ch == "~":
            if i + 1 < len(p) and p[i + 1] in "*?":
                out.append(re.escape(p[i + 1]))
                i += 2
                continue
            if i + 1 == len(p):
                break                                   # a ~ at the end is dropped
            out.append(re.escape(ch))
        elif ch == "*":
            out.append(".*")
        elif ch == "?":
            out.append(".")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("".join(out), re.IGNORECASE | re.DOTALL)


def _text_equal(pattern: str, text: str) -> bool:
    if "*" not in pattern and "?" not in pattern:
        return pattern.casefold() == text.casefold()
    return _pattern(pattern).fullmatch(text) is not None


def _value_number(v: Any) -> Decimal | None:
    """The number a cell value is for an equality with a number criterion: a number cell, or a text that reads
    as a number."""
    if isinstance(v, str):
        return text_number(v, side="value")
    return _cell_number(v)


def excel_criteria_match(criterion: Any, value: Any) -> bool:
    """Whether Excel's SUMIFS, COUNTIFS, AVERAGEIFS, MAXIFS, MINIFS, SUMIF, COUNTIF and AVERAGEIF count the cell
    value `value` (None = an empty cell; a str = a text cell, also the empty text a formula returns) for the
    criterion `criterion` (the value of the criterion cell or literal). The rules are in the module text."""
    c = _criterion(criterion)
    is_text = isinstance(value, str)
    is_bool = isinstance(value, bool)
    num = None if (is_text or is_bool) else _cell_number(value)
    if c.op in ("", "="):
        if c.kind == "empty":
            return value is None
        if c.kind == "number":
            got = _value_number(value) if not is_bool else None
            return got is not None and got == c.number
        if c.kind == "bool":
            return is_bool and value is c.boolean
        return is_text and _text_equal(str(c.text), value)
    if c.op == "<>":
        if c.kind == "empty":
            return value is not None
        if c.kind == "number":
            return not (num is not None and num == c.number)
        if c.kind == "bool":
            return not (is_bool and value is c.boolean)
        return not (is_text and _text_equal(str(c.text), value))
    # <, >, <=, >=
    if c.kind == "number" and num is not None:
        a, b = num, c.number
    elif c.kind == "text" and is_text:
        a, b = value.casefold(), str(c.text).casefold()
    else:
        return False
    return {"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[c.op]


def _exactly_equal(label: Any, value: Any) -> bool:
    if isinstance(label, str) or isinstance(value, str):
        return isinstance(label, str) and isinstance(value, str) and label == value
    if isinstance(label, bool) or isinstance(value, bool):
        return isinstance(label, bool) and isinstance(value, bool) and label is value
    a, b = _cell_number(label), _cell_number(value)
    return a is not None and a == b


def _reason(label: Any, value: Any) -> tuple[str, str]:
    """(key, text) of why Excel matches `label` with `value` (plan 5.1 texts)."""
    shown = _show(label)
    if isinstance(label, str):
        if label.startswith(("=", "<", ">")):
            return "operator", f"'{shown}' starts with an operator (=, < or >)"
        if "*" in label or "?" in label:
            return "wildcard", f"'{shown}' holds the wildcard * or ?"
        n = text_number(label)
        if n is not None:
            return "number", f"both read as the number {_number_text(n)}"
        if isinstance(value, str) and label.casefold() == value.casefold():
            return "case", "they differ only in case"
        return "other", "Excel's criteria rules"
    n = _cell_number(label)
    if n is not None:
        return "number", f"both read as the number {_number_text(n)}"
    return "other", "Excel's criteria rules"


def _number_text(n: Decimal) -> str:
    if n == n.to_integral_value():
        return str(int(n))
    return format(n.normalize(), "f")


def _show(v: Any) -> str:
    if v is None:
        return "(empty)"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, dt.datetime):
        return v.date().isoformat() if v.time() == dt.time(0) else v.isoformat(sep=" ")
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, (int, float)):
        n = _cell_number(v)
        return _number_text(n) if n is not None else str(v)
    return str(v)


# ----------------------------------------------------------------------------- the workbook

_SHEET = r"(?:'(?:[^']|'')+'|[A-Za-z0-9_.\u0080-￿]+)"
_COL_RANGE_RE = re.compile(r"^(?:(" + _SHEET + r")!)?\$?([A-Za-z]{1,3})(?:\$?([0-9]+))?:\$?([A-Za-z]{1,3})"
                           r"(?:\$?([0-9]+))?$")
_CELL_REF_RE = re.compile(r"^(?:(" + _SHEET + r")!)?\$?([A-Za-z]{1,3})\$?([0-9]+)$")
_LINK_RE = re.compile(r"^=\s*\+?\s*((?:" + _SHEET + r"!)?\$?[A-Za-z]{1,3}\$?[0-9]+)\s*$")
_TEXT_LITERAL_RE = re.compile(r'^"((?:[^"]|"")*)"$')
_NUMBER_LITERAL_RE = re.compile(r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$")


def _unquote(sheet: str | None) -> str | None:
    if sheet is None:
        return None
    if sheet.startswith("'") and sheet.endswith("'"):
        return sheet[1:-1].replace("''", "'")
    return sheet


def _pairs(name: str, n_args: int) -> Iterable[tuple[int, int]]:
    """(criteria range, criterion) argument indexes (0-based) of one call."""
    if name in ("SUMIF", "COUNTIF", "AVERAGEIF"):
        if n_args >= 2:
            yield 0, 1
        return
    start = 0 if name == "COUNTIFS" else 1
    for i in range(start, n_args - 1, 2):
        yield i, i + 1


@dataclass(frozen=True)
class _Call:
    sheet: str
    cell: str
    function: str
    range_text: str
    criterion_text: str


def _calls(formula_text: str, sheet: str, cell: str) -> list[_Call]:
    if not isinstance(formula_text, str) or not _FUNC_RE.search(formula_text):
        return []
    from . import formula as fmod
    a = fmod.analyze(formula_text)
    out: list[_Call] = []
    for call in a.calls:
        if call.name not in FUNCTIONS:
            continue
        args = [a.formula[s:e].strip() for s, e in call.args]
        for ri, ci in _pairs(call.name, len(args)):
            out.append(_Call(sheet, cell, call.name, args[ri], args[ci]))
    return out


@dataclass(frozen=True)
class _Source:
    kind: str                   # raw | helper
    sheet: str                  # the sheet whose column is read
    column: str                 # contract column name (raw) or helper column id
    letter: str                 # column letter on `sheet`
    first_row: int
    fetch_text: bool            # a helper fetch of a raw text column (INDEX(...)&""): values are texts


def _grid(ws: Any) -> dict[tuple[int, int], Any]:
    try:
        ws.reset_dimensions()
    except AttributeError:
        pass
    out: dict[tuple[int, int], Any] = {}
    for r, row in enumerate(ws.iter_rows(values_only=True), 1):
        for c, v in enumerate(row, 1):
            if v is not None:
                out[(r, c)] = v
    return out


def _column_values(ws: Any, letter: str, first_row: int) -> list[tuple[int, Any]]:
    try:
        ws.reset_dimensions()
    except AttributeError:
        pass
    ci = common.col_index(letter)
    out = []
    for r, row in enumerate(ws.iter_rows(min_row=first_row, min_col=ci, max_col=ci, values_only=True), first_row):
        out.append((r, row[0] if row else None))
    while out and out[-1][1] in (None, ""):
        out.pop()
    return out


def _as_fetched_text(v: Any) -> str:
    """The text a helper fetch INDEX(raw...)&"" returns for a raw cell value."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    n = _cell_number(v)
    return _number_text(n) if n is not None else str(v)


def _block_name(manifest: dict[str, Any], sheet: str, cell: str) -> str:
    try:
        col, row = common.split_cell(cell)
    except ValueError:
        return f"{sheet}!{cell}"
    for entry in manifest.get("sheets") or []:
        if entry.get("name") != sheet:
            continue
        for b in entry.get("blocks") or []:
            try:
                c1, r1, c2, r2 = common.range_bounds(str(b.get("body")))
            except (ValueError, TypeError):
                continue
            if b.get("labels"):
                try:
                    lc1, lr1, _lc2, _lr2 = common.range_bounds(str(b["labels"]))
                    c1, r1 = min(c1, lc1), min(r1, lr1)
                except ValueError:
                    pass
            for key in ("heading_row", "header_row"):
                if isinstance(b.get(key), int):
                    r1 = min(r1, b[key])
            if isinstance(b.get("total_row"), int):
                r2 = max(r2, b["total_row"])
            if b.get("total_col"):
                try:
                    c2 = max(c2, common.col_index(str(b["total_col"])))
                except ValueError:
                    pass
            if c1 <= col <= c2 and r1 <= row <= r2:
                return f"block {b.get('id')} ({sheet}!{cell})"
    return f"{sheet}!{cell}"


def criteria_findings(workbook: str | Path, manifest: dict[str, Any], *,
                      saved_values: bool = False) -> list[common.Finding]:
    """The D-CRITERIA warnings of one workbook (module text). Reads the workbook file and the manifest only.
    Without `saved_values` it covers criteria ranges that are fetch columns of a raw text column (and raw text
    columns), with the values of the raw sheet; with `saved_values` it also covers criteria ranges on other helper
    columns (and other raw columns), with the values Excel saved."""
    import openpyxl
    path = Path(common.native_path(workbook))
    sheets = manifest.get("sheets") or []
    raw = {e["name"]: e for e in sheets if e.get("type") == "raw" and e.get("name")}
    helpers = {e["name"]: e for e in sheets if e.get("type") == "helpers" and e.get("name")}
    raw_by_table = {e.get("table"): e for e in raw.values()}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            book = openpyxl.load_workbook(path, read_only=True, data_only=False, keep_links=False)
        except Exception:  # noqa: BLE001 - check and build name a workbook openpyxl cannot read elsewhere
            return []
        saved_book = None
        try:
            names = list(book.sheetnames)
            grids = {n: _grid(book[n]) for n in names if n not in raw and n not in helpers}
            calls: list[_Call] = []
            for n, grid in grids.items():
                for (r, c), v in sorted(grid.items()):
                    if isinstance(v, str) and v.startswith("="):
                        calls += _calls(v, n, f"{common.col_letter(c)}{r}")
            for h in helpers.values():
                first = int(h.get("first_row") or 2)
                for col in h.get("columns") or []:
                    if col.get("formula") and col.get("col"):
                        calls += _calls(str(col["formula"]), h["name"], f"{col['col']}{first}")
            if not calls:
                return []
            saved: dict[str, dict[tuple[int, int], Any]] = {}
            if saved_values:
                saved_book = openpyxl.load_workbook(path, read_only=True, data_only=True, keep_links=False)
                saved = {n: _grid(saved_book[n]) for n in grids}
            values_cache: dict[_Source, dict[Any, int]] = {}

            def source_of(call: _Call) -> _Source | None:
                m = _COL_RANGE_RE.match(call.range_text)
                if not m or m.group(2).upper() != m.group(4).upper():
                    return None
                sheet = _unquote(m.group(1)) or call.sheet
                letter = m.group(2).upper()
                if sheet in helpers:
                    h = helpers[sheet]
                    col = next((c for c in h.get("columns") or [] if str(c.get("col", "")).upper() == letter), None)
                    if col is None:
                        return None
                    rentry = raw_by_table.get(h.get("table"))
                    if col.get("kind") == "fetch" and rentry is not None and col.get("raw_column"):
                        cols = list(rentry.get("columns") or [])
                        types = list(rentry.get("types") or [])
                        if col["raw_column"] in cols:
                            j = cols.index(col["raw_column"])
                            if (types[j] if j < len(types) else "text") == "text":
                                return _Source("raw", rentry["name"], col["raw_column"], common.col_letter(j + 1),
                                               2, True)
                    if saved_values:
                        return _Source("helper", sheet, str(col.get("id")), letter, int(h.get("first_row") or 2),
                                       False)
                    return None
                if sheet in raw:
                    rentry = raw[sheet]
                    j = common.col_index(letter) - 1
                    cols = list(rentry.get("columns") or [])
                    types = list(rentry.get("types") or [])
                    if j >= len(cols):
                        return None
                    if (types[j] if j < len(types) else "text") == "text" or saved_values:
                        return _Source("raw", sheet, cols[j], letter, 2, False)
                return None

            def distinct(src: _Source) -> dict[Any, int]:
                if src in values_cache:
                    return values_cache[src]
                wbk = saved_book if (src.kind == "helper" and saved_book is not None) else book
                found: dict[Any, int] = {}
                if src.sheet in wbk.sheetnames:
                    for r, v in _column_values(wbk[src.sheet], src.letter, src.first_row):
                        if src.fetch_text:
                            v = _as_fetched_text(v)
                        elif v is None:
                            continue
                        key = (type(v).__name__, v)
                        if key not in found:
                            found[key] = r
                values_cache[src] = found
                return found

            def label_of(call: _Call) -> tuple[Any, bool] | None:
                """(label value, typed) of the criterion argument; None when it is not a literal or a label cell."""
                text = call.criterion_text
                m = _TEXT_LITERAL_RE.match(text)
                if m:
                    return m.group(1).replace('""', '"'), True
                if _NUMBER_LITERAL_RE.match(text):
                    return float(text), True
                if text.upper() in ("TRUE", "FALSE"):
                    return text.upper() == "TRUE", True
                m = _CELL_REF_RE.match(text)
                if not m:
                    return None
                sheet, letter, row = _unquote(m.group(1)) or call.sheet, m.group(2).upper(), int(m.group(3))
                for _hop in range(MAX_LINK_HOPS):
                    if sheet not in grids:
                        return None                         # a raw or helper cell: a criterion per row
                    v = grids[sheet].get((row, common.col_index(letter)))
                    if getattr(v, "text", None) is not None and not isinstance(v, str):
                        return None                         # an array formula
                    if not (isinstance(v, str) and v.startswith("=")):
                        return v, False
                    if saved_values:
                        return saved.get(sheet, {}).get((row, common.col_index(letter))), False
                    link = _LINK_RE.match(v)
                    if not link:
                        return None
                    m2 = _CELL_REF_RE.match(link.group(1))
                    if not m2:
                        return None
                    sheet, letter, row = _unquote(m2.group(1)) or sheet, m2.group(2).upper(), int(m2.group(3))
                return None

            out: list[common.Finding] = []
            seen: set[tuple[Any, ...]] = set()
            per_label: dict[tuple[Any, ...], list[common.Finding]] = {}
            for call in calls:
                src = source_of(call)
                if src is None:
                    continue
                got = label_of(call)
                if got is None:
                    continue
                label, typed = got
                if label is None or label == "":
                    continue
                if typed and isinstance(label, str) and (label.startswith(("=", "<", ">")) or "*" in label
                                                         or "?" in label):
                    continue                                # a typed operator or pattern is meant
                where = _block_name(manifest, call.sheet, call.cell)
                group = (where.split(" (")[0], src.sheet, src.column, type(label).__name__, label)
                for (_tname, value), row in distinct(src).items():
                    if _exactly_equal(label, value) or not excel_criteria_match(label, value):
                        continue
                    key = group + (type(value).__name__, value)
                    if key in seen:
                        continue
                    seen.add(key)
                    items = per_label.setdefault(group, [])
                    if len(items) >= MAX_VALUES_PER_LABEL:
                        items[-1].data["more"] = items[-1].data.get("more", 0) + 1
                        continue
                    rkey, rtext = _reason(label, value)
                    f = common.Finding(
                        RULE, "warning",
                        f"{where}: the label '{_show(label)}' and the value '{_show(value)}' of {src.sheet} column "
                        f"{src.column} (first row {row}) are the same for Excel's criteria matching ({rtext}), so "
                        f"{call.function} adds both under '{_show(label)}'",
                        FIX.format(column=src.column), sheet=call.sheet, cell=call.cell,
                        data={"where": where, "label": label if not isinstance(label, (dt.date, dt.time))
                              else _show(label), "value": value if not isinstance(value, (dt.date, dt.time))
                              else _show(value), "sheet": src.sheet, "column": src.column, "row": row,
                              "function": call.function, "reason": rkey, "source": src.kind})
                    items.append(f)
                    out.append(f)
            for f in out:
                more = f.data.get("more")
                if more:
                    f.message += f" (and {more} more values of this column match '{_show(f.data['label'])}')"
            return out
        finally:
            book.close()
            if saved_book is not None:
                saved_book.close()

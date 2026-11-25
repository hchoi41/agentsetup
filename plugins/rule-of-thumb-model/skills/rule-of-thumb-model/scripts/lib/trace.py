"""`trace`: for one cell, the chain of helper cells back to the raw rows, in words (design section 15; F18).

Owner: M7. Uses lib.formula.references and lib.formula.relative_form (cells of one column with the same
relative form are one helper column).

How the chain is built (never writes the workbook):
- The traced cell's formula is split into its references (lib.formula). Each reference becomes a node.
- A referenced range is split into formula groups (cells with the same relative form: one helper column, one
  formula across a block row, or one formula over a block) and typed values. A whole-column reference skips the
  text cells above the first formula (the column header).
- A group's precedents are the references of its first cell, moved over the group's rows and columns (a
  relative reference moves with the group, an anchored one stays).
- The chain ends at raw columns (sheet, column letter, header, row range), at inputs (label, value, unit) and at
  typed values. Raw sheets come from the manifest when the workbook was built by this plugin; otherwise they are
  inferred from the sheet name (raw, data, src, landed), from tables loaded by a query, or from --raw-sheets.
- One raw cell (v4, A-aera-13): a single cell of a helper sheet that reads one raw row by position,
  INDEX(raw_<t>!$X:$X,<row cell>), ends at the one raw cell raw_<t>!X<row> with its value. The row is the saved
  value of the row cell when it holds one, else the helper row's own position (the manifest records the row
  column as =ROW()). Without either, the raw column stays.
- Conditional sums and counts (v4, A-aera-13): a raw range reached through a range argument of SUMIFS, COUNTIFS,
  AVERAGEIFS, MAXIFS, MINIFS, SUMIF, COUNTIF or AVERAGEIF (directly, or through helper columns of the same rows
  and their INDEX fetches) stays the whole range and is labelled "the rows that can contribute to <function> in
  <cell>, where <criteria range> matches <criterion> (<its value>) ...". The matching rows are not named.
- Each range is traced once; a second visit prints "traced above". At most MAX_NODES nodes are written.

Labels (v3, A-aera-14): a cell is labelled by its own row's header and its own column's header, never by a cell
of another row.
- With a manifest that records cell types (a workbook this plugin built): the row header is the row_header cell
  of the same row (column B in this plugin's layout; a row header cell is labelled by its own text); the column
  header is the col_header cell at the top of the cell's block, found by going up the column until a col_header
  cell (a title, heading or label cell above stops the search: no column header).
- Without cell types: the row header is the nearest text to the left on the same row (a text cell with no text
  to its left is its own row header); the column header is the nearest text above, past cells that do not hold
  text, and only for a cell that does not hold text itself: above a text cell, the text in its column is another
  row's label, not a header.
- Raw and helper sheets: the column header is row 1.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from . import common
from . import formula as fx

MAX_NODES = 300                 # nodes written before the trace stops (stated in the output)
MAX_GROUPS = 6                  # formula groups shown per range; the rest are counted
MAX_LEVELS = 40                 # safety limit when --depth is not given
LABEL_SCAN = 40                 # cells scanned left (row label) or up (column header) for a label

RAW_NAME = re.compile(r"^(raw|data|src|landed)[_ -]?|_raw$", re.I)       # design 14 (inference without manifest)
INPUT_NAME = re.compile(r"input|assumption|driver|가정|입력", re.I)
CHECK_NAME = re.compile(r"^checks?$|check|chk|control|검증|체크", re.I)
HELPER_NAME = re.compile(r"^h_|helper", re.I)
BLOCK_TYPES = ("cover", "inputs", "calc", "output", "checks", "notes")
# Manifest cell types (v3, A-aera-14): going up a column from a cell, these end the search for its column header.
HEADER_STOP_TYPES = ("title", "heading", "label")
OWN_TEXT_TYPES = ("row_header", "col_header", "heading", "title", "label")   # labelled by their own text
# Conditional sums and counts (v4, A-aera-13): the functions whose range arguments are labelled as the rows that
# can contribute, with their criteria.
CONDITIONAL = ("SUMIFS", "COUNTIFS", "AVERAGEIFS", "MAXIFS", "MINIFS", "SUMIF", "COUNTIF", "AVERAGEIF")


def _conditional_args(name: str, nargs: int) -> tuple[list[int], list[tuple[int, int]]]:
    """(range argument numbers, (criteria range, criterion) argument pairs) of a conditional function; 1-based."""
    if name == "COUNTIFS":
        pairs = [(k, k + 1) for k in range(1, nargs, 2)]
        return [k for k, _c in pairs], pairs
    if name in ("SUMIFS", "AVERAGEIFS", "MAXIFS", "MINIFS"):
        pairs = [(k, k + 1) for k in range(2, nargs, 2)]
        return [1] + [k for k, _c in pairs], pairs
    pairs = [(1, 2)] if nargs >= 2 else []                       # SUMIF, COUNTIF, AVERAGEIF
    return ([1, 3] if nargs >= 3 and name != "COUNTIF" else [1]), pairs

_CELL = re.compile(r"^(\$?)([A-Za-z]{1,3})(\$?)(\d+)$")
_COL = re.compile(r"^(\$?)([A-Za-z]{1,3})$")
_ROW = re.compile(r"^(\$?)(\d+)$")


@dataclass
class TraceNode:
    ref: str                      # "summary!D6" or "h_sales!G2:G601"
    label: str                    # header or row label, e.g. "Amount (KRW thousand)"
    kind: str                     # input | raw | helper_column | block_cell | check | constant (and see KINDS)
    formula: str | None
    children: list["TraceNode"] = field(default_factory=list)
    # additions (M7): what the line says besides the label
    value: Any = None             # cached value of a single cell
    count: int = 1                # cells in the node
    rows: str = ""                # "rows 2 to 601"
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"ref": self.ref, "label": self.label, "kind": self.kind, "formula": self.formula,
                "value": _json_value(self.value), "count": self.count, "rows": self.rows, "note": self.note,
                "children": [c.to_dict() for c in self.children]}


# kinds: input, raw, helper_column, block_cell, check, constant, range (one formula over several cells),
# constants (several typed values), empty, external, name, error, seen (traced above), cut (limit reached)


def _json_value(v: Any) -> Any:
    if isinstance(v, (_dt.date, _dt.datetime, _dt.time)):
        return v.isoformat()
    return v


# ----------------------------------------------------------------------------- reference endpoints

@dataclass
class _End:
    col: int | None
    row: int | None
    abs_col: bool
    abs_row: bool


def _endpoints(a1: str) -> list[_End] | None:
    parts = a1.replace(" ", "").split(":")
    out = []
    for p in parts:
        if "!" in p:
            p = p.rsplit("!", 1)[1]
        m = _CELL.match(p)
        if m:
            out.append(_End(common.col_index(m.group(2)), int(m.group(4)), bool(m.group(1)), bool(m.group(3))))
            continue
        m = _COL.match(p)
        if m:
            out.append(_End(common.col_index(m.group(2)), None, bool(m.group(1)), True))
            continue
        m = _ROW.match(p)
        if m:
            out.append(_End(None, int(m.group(2)), True, bool(m.group(1))))
            continue
        return None
    return out or None


def moved_bounds(a1: str, first: tuple[int, int], box: tuple[int, int, int, int],
                 sheet_size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    """Bounds (c1, r1, c2, r2) a reference covers when the formula of the first cell `first` (col, row) is copied
    over the group's bounding box `box` (c1, r1, c2, r2). sheet_size = (max_col, max_row) of the referenced sheet,
    used for whole columns and rows."""
    ends = _endpoints(a1)
    if ends is None:
        return None
    fc, fr = first
    gc1, gr1, gc2, gr2 = box
    max_col, max_row = max(sheet_size[0], 1), max(sheet_size[1], 1)
    cols: list[int] = []
    rows: list[int] = []
    for e in ends:
        if e.col is None:
            cols += [1, max_col]
        elif e.abs_col:
            cols.append(e.col)
        else:
            cols += [e.col + (gc1 - fc), e.col + (gc2 - fc)]
        if e.row is None:
            rows += [1, max_row]
        elif e.abs_row:
            rows.append(e.row)
        else:
            rows += [e.row + (gr1 - fr), e.row + (gr2 - fr)]
    c1, c2 = max(1, min(cols)), max(cols)
    r1, r2 = max(1, min(rows)), max(rows)
    if c2 < 1 or r2 < 1:
        return None
    return c1, r1, c2, r2


def _box_text(sheet: str, box: tuple[int, int, int, int]) -> str:
    c1, r1, c2, r2 = box
    a = f"{common.col_letter(c1)}{r1}"
    text = a if (c1, r1) == (c2, r2) else f"{a}:{common.col_letter(c2)}{r2}"
    return f"{common.quote_sheet(sheet)}!{text}"


def _fmt(v: Any) -> str:
    if v is None:
        return "(no value)"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        if v.is_integer() and abs(v) < 1e15:
            return f"{int(v):,}"
        if abs(v) >= 1000:
            return f"{v:,.4f}".rstrip("0").rstrip(".")
        return format(v, ".6g")
    if isinstance(v, _dt.datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S") if (v.hour or v.minute or v.second) else v.strftime("%Y-%m-%d")
    if isinstance(v, _dt.date):
        return v.isoformat()
    text = str(v)
    return f"'{text[:60]}'" + ("..." if len(text) > 60 else "")


def _short(text: str, n: int = 140) -> str:
    text = text.replace("\r", " ").replace("\n", " ")
    return text if len(text) <= n else text[:n - 3] + "..."


# ----------------------------------------------------------------------------- the tracer

class _Tracer:
    def __init__(self, info: Any, *, raw_sheets: Sequence[str] = (), max_levels: int = MAX_LEVELS):
        self.info = info
        self.sheets = {s.name: s for s in info.sheets if s.kind == "worksheet"}
        self.lower = {n.casefold(): n for n in self.sheets}
        self.max_levels = max_levels
        self.nodes = 0
        self.cut = False
        self.seen: dict[tuple[str, tuple[int, int, int, int]], str] = {}
        self.reached_raw: set[str] = set()
        self.cycles = 0
        self.types: dict[str, str] = {}
        man = info.manifest or {}
        for entry in man.get("sheets") or []:
            if isinstance(entry, dict) and entry.get("name") and entry.get("type"):
                self.types[str(entry["name"])] = str(entry["type"])
        self.inferred = not self.types
        for name in self.sheets:
            if name in self.types:
                continue
            if name in raw_sheets or RAW_NAME.search(name):
                self.types[name] = "raw"
            elif HELPER_NAME.search(name):
                self.types[name] = "helpers"
            elif INPUT_NAME.search(name):
                self.types[name] = "inputs"
            elif CHECK_NAME.search(name):
                self.types[name] = "checks"
        for t in info.tables():
            if t.from_query and t.sheet in self.sheets and t.sheet not in raw_sheets:
                self.types.setdefault(t.sheet, "raw")
                if self.types[t.sheet] != "raw" and self.inferred:
                    self.types[t.sheet] = "raw"
        self.names = {n.casefold(): (n, v, scope) for n, v, scope in info.defined_names}
        self._by_col: dict[str, dict[int, list[Any]]] = {}
        self.tables = info.tables()
        self.manifest = man
        self._cell_type_ranges: dict[str, list[tuple[int, int, int, int, str]]] = {}
        # v4, A-aera-13: the label of the conditional sum or count whose range is being traced (innermost last)
        self._context: list[str | None] = []
        # v4, A-aera-13: the row column (kind "row", =ROW()) of each helper sheet in the manifest's layout
        self._row_cols: dict[str, int] = {}
        for entry in man.get("sheets") or []:
            if not (isinstance(entry, dict) and entry.get("type") == "helpers" and entry.get("name")):
                continue
            for col in entry.get("columns") or []:
                if (isinstance(col, dict) and col.get("kind") == "row" and isinstance(col.get("col"), str)
                        and str(col.get("formula") or "").replace(" ", "").upper() == "=ROW()"):
                    try:
                        self._row_cols[str(entry["name"])] = common.col_index(col["col"])
                    except (ValueError, KeyError):
                        pass

    # -- labels
    def _text(self, sheet: Any, row: int, col: int) -> str | None:
        c = sheet.at(row, col)
        if c is None:
            return None
        v = c.cached if c.formula is not None else c.value
        if isinstance(v, str) and v.strip() and not v.startswith("="):
            return v.strip()
        return None

    def _ranges(self, sheet_name: str) -> list[tuple[int, int, int, int, str]]:
        """The manifest's cell-type ranges of the sheet; [] without a manifest or without cell types for it."""
        out = self._cell_type_ranges.get(sheet_name)
        if out is None:
            out = []
            if self.manifest:
                from . import manifest as mmod
                try:
                    out = mmod.cell_type_ranges(self.manifest, sheet_name)
                except (ValueError, TypeError, KeyError, AttributeError):
                    out = []
            self._cell_type_ranges[sheet_name] = out
        return out

    def _type_at(self, sheet_name: str, row: int, col: int) -> str | None:
        ranges = self._ranges(sheet_name)
        if not ranges:
            return None
        from . import manifest as mmod
        return mmod.cell_type_at(ranges, row, col)

    def header(self, sheet_name: str, col: int, first_row: int) -> str | None:
        """Column header of the cell (or the first cell of a group) at `first_row` (v3, A-aera-14): row 1 on raw
        and helper sheets; with the manifest's cell types the col_header cell at the top of the cell's block;
        else the nearest text above `first_row` (past cells without text), only for a cell that holds no text."""
        s = self.sheets[sheet_name]
        if self.types.get(sheet_name) in ("raw", "helpers"):
            return self._text(s, 1, col)
        if self._ranges(sheet_name):
            own = self._type_at(sheet_name, first_row, col)
            if own == "col_header" or own in HEADER_STOP_TYPES:
                return None                            # a header cell has no column header above it
            for r in range(first_row - 1, 0, -1):
                ct = self._type_at(sheet_name, r, col)
                if ct == "col_header":
                    return self._text(s, r, col)
                if ct in HEADER_STOP_TYPES:
                    return None
            return None
        if self._text(s, first_row, col):
            return None                                # above a text cell is another row's label, not a header
        for r in range(first_row - 1, max(0, first_row - 1 - LABEL_SCAN), -1):
            t = self._text(s, r, col)
            if t:
                return t
        return None

    def row_label(self, sheet_name: str, row: int, col: int) -> str | None:
        """Row header of the cell (v3, A-aera-14): with the manifest's cell types the nearest row_header cell
        that holds text on the same row, the cell itself included; else (and when the row has none) the nearest
        text to the left on the same row."""
        s = self.sheets[sheet_name]
        if self._ranges(sheet_name):
            for c in range(col, 0, -1):
                if self._type_at(sheet_name, row, c) == "row_header":
                    t = self._text(s, row, c)
                    if t:
                        return t
        for c in range(col - 1, max(0, col - 1 - LABEL_SCAN), -1):
            t = self._text(s, row, c)
            if t:
                return t
        return None

    def own_label(self, sheet_name: str, row: int, col: int) -> str | None:
        """The cell's own text when it is the header of its row (v3, A-aera-14): with the manifest's cell types a
        row_header, col_header, heading, title or label cell; without them a text cell (row_label found no text
        to its left)."""
        s = self.sheets[sheet_name]
        if self._ranges(sheet_name) and self._type_at(sheet_name, row, col) not in OWN_TEXT_TYPES:
            return None
        return self._text(s, row, col)

    def unit(self, sheet_name: str, row: int, col: int) -> str | None:
        """The unit column of this plugin's layout (column C, design 6.3) when the label sits in column B."""
        if self.types.get(sheet_name) in ("inputs", "calc", "output", "checks", "cover") and col > 3:
            if self._text(self.sheets[sheet_name], row, 2):
                return self._text(self.sheets[sheet_name], row, 3)
        return None

    def cell_label(self, sheet_name: str, row: int, col: int) -> str:
        parts = []
        rl = self.row_label(sheet_name, row, col)
        if self.unit(sheet_name, row, col):          # this plugin's layout: label in B, unit in C (design 6.3)
            rl = self._text(self.sheets[sheet_name], row, 2)
        if not rl:
            rl = self.own_label(sheet_name, row, col)
        if rl:
            parts.append(f"'{_short(rl, 60)}'")
        hd = self.header(sheet_name, col, row)
        if hd and hd != rl:
            parts.append(f"'{_short(hd, 60)}'")
        return " / ".join(parts)

    # -- nodes
    def trace(self, sheet_name: str, row: int, col: int, max_depth: int | None) -> TraceNode:
        s = self.sheets[sheet_name]
        c = s.at(row, col)
        box = (col, row, col, row)
        root = self._cell_node(sheet_name, c, row, col)
        self.seen[(sheet_name, box)] = root.ref
        self.nodes = 1
        if c is not None and c.formula:
            root.children = self._precedents(sheet_name, c.formula, (col, row), box, 1, max_depth)
        return root

    def _kind_of_cell(self, sheet_name: str) -> str:
        t = self.types.get(sheet_name)
        return {"checks": "check", "helpers": "helper_column", "raw": "raw", "inputs": "input"}.get(t or "",
                                                                                                    "block_cell")

    def _cell_node(self, sheet_name: str, c: Any, row: int, col: int) -> TraceNode:
        ref = _box_text(sheet_name, (col, row, col, row))
        label = self.cell_label(sheet_name, row, col)
        if c is None:
            return TraceNode(ref, label, "empty", None)
        if c.formula is not None:
            kind = self._kind_of_cell(sheet_name)
            if kind in ("raw", "input"):
                kind = "block_cell"
            return TraceNode(ref, label, kind, fx.display_form(c.formula), value=c.cached)
        kind = "raw" if self.types.get(sheet_name) == "raw" else ("input" if self.types.get(sheet_name) == "inputs"
                                                                  else "constant")
        node = TraceNode(ref, label, kind, None, value=c.value)
        u = self.unit(sheet_name, row, col)
        if u:
            node.note = f"unit '{u}'"
        if kind == "raw":
            self.reached_raw.add(sheet_name)
        return node

    def _precedents(self, host_sheet: str, f: str, first: tuple[int, int], box: tuple[int, int, int, int],
                    level: int, max_depth: int | None) -> list[TraceNode]:
        out: list[TraceNode] = []
        if (max_depth is not None and level > max_depth) or level > self.max_levels:
            if fx.references(f):
                out.append(TraceNode("", "", "cut", None, note="depth limit reached; trace that cell to go further"))
            return out
        a = fx.analyze(f)
        done: set[tuple[str, tuple[int, int, int, int]]] = set()
        fetches = self._fetch_cells(host_sheet, a, first, box)
        contexts = self._ref_contexts(host_sheet, a, first, box)
        for i, r in enumerate(a.refs):
            if self.nodes >= MAX_NODES:
                if not self.cut:
                    self.cut = True
                    out.append(TraceNode("", "", "cut", None, note=f"stopped after {MAX_NODES} nodes"))
                break
            if i in fetches:
                node = self._raw_cell_node(*fetches[i], done)
            else:
                self._context.append(contexts[i])
                try:
                    node = self._ref_node(host_sheet, r, first, box, level, max_depth, done)
                finally:
                    self._context.pop()
            if node is not None:
                out.append(node)
        return out

    # -- v4, A-aera-13: one raw cell for a fetch cell; the label of a conditional sum or count
    @staticmethod
    def _refs_in(a: Any, span: tuple[int, int]) -> list[int]:
        return [i for i, r in enumerate(a.refs) if span[0] <= r.start < span[1]]

    def _saved(self, sheet_name: str, row: int, col: int) -> Any:
        c = self.sheets[sheet_name].at(row, col)
        if c is None:
            return None
        return c.cached if c.formula is not None else c.value

    def _fetch_cells(self, host_sheet: str, a: Any, first: tuple[int, int],
                     box: tuple[int, int, int, int]) -> dict[int, tuple[str, int, int, str]]:
        """{index of the raw column reference: (raw sheet, column, row, why)} for each INDEX(<one raw column>,
        <row cell>) of a single cell of a helper sheet whose raw row is known: the saved value of the row cell,
        else the helper row's own position when the manifest records that cell's column as =ROW()."""
        out: dict[int, tuple[str, int, int, str]] = {}
        if self.types.get(host_sheet) != "helpers" or box[0] != box[2] or box[1] != box[3]:
            return out
        host = self.sheets[host_sheet]
        for call in a.calls:
            if call.name != "INDEX" or len(call.args) != 2:
                continue
            spans = list(call.args)
            in1, in2 = self._refs_in(a, spans[0]), self._refs_in(a, spans[1])
            if len(in1) != 1 or len(in2) != 1:
                continue
            col_ref, row_ref = a.refs[in1[0]], a.refs[in2[0]]
            if (a.formula[spans[0][0]:spans[0][1]].strip() != col_ref.text
                    or a.formula[spans[1][0]:spans[1][1]].strip() != row_ref.text):
                continue                                   # plain references only
            raw_sheet = self._resolve_sheet(host_sheet, col_ref.sheet)
            ends = _endpoints(col_ref.a1)
            if (raw_sheet is None or raw_sheet not in self.sheets or self.types.get(raw_sheet) != "raw"
                    or col_ref.workbook or not ends or len(ends) != 2 or ends[0].row is not None
                    or ends[0].col is None or ends[0].col != ends[1].col):
                continue                                   # one whole column of a raw sheet
            if row_ref.kind != "cell" or row_ref.workbook or self._resolve_sheet(host_sheet, row_ref.sheet) != host_sheet:
                continue
            b = moved_bounds(row_ref.a1, first, box, (host.max_col, host.max_row))
            if b is None or b[0] != b[2] or b[1] != b[3]:
                continue
            rc, rr = b[0], b[1]
            cell_text = _box_text(host_sheet, (rc, rr, rc, rr))
            saved = self._saved(host_sheet, rr, rc)
            if isinstance(saved, (int, float)) and not isinstance(saved, bool) and float(saved).is_integer() \
                    and saved >= 1:
                row, why = int(saved), f"the saved value of {cell_text}"
            elif self._row_cols.get(host_sheet) == rc:
                row, why = rr, (f"the helper row's own position ({cell_text} is =ROW() in the manifest's layout "
                                "and holds no saved value)")
            else:
                continue
            out[in1[0]] = (raw_sheet, ends[0].col, row, why)
        return out

    def _raw_cell_node(self, raw_sheet: str, col: int, row: int, why: str, done: set) -> TraceNode | None:
        bounds = (col, row, col, row)
        key = (raw_sheet, bounds)
        if key in done:
            return None
        done.add(key)
        self.nodes += 1
        ref = _box_text(raw_sheet, bounds)
        if key in self.seen:
            return TraceNode(ref, "", "seen", None, note="traced above")
        self.seen[key] = ref
        self.reached_raw.add(raw_sheet)
        h = self._text(self.sheets[raw_sheet], 1, col)
        value = self._saved(raw_sheet, row, col)
        note = f"raw data; row {row:,} is {why}" + ("; the cell is empty" if value is None else "")
        return TraceNode(ref, f"'{_short(h, 60)}'" if h else "", "raw", None, value=value, note=note)

    def _ref_contexts(self, host_sheet: str, a: Any, first: tuple[int, int],
                      box: tuple[int, int, int, int]) -> list[str | None]:
        """For each reference of the formula: the label of the conditional sum or count whose rows it holds, or
        None. A range argument of a conditional function of this formula gets that function's label; on a helper
        sheet, a reference to the same rows of the same sheet and the raw column of an INDEX keep the label the
        helper column was reached with; every other reference has none."""
        own: dict[int, str] = {}
        for call in sorted(a.calls, key=lambda c: c.level):   # an inner call's label replaces an outer one's
            if call.name not in CONDITIONAL or not call.args:
                continue
            ranges, pairs = _conditional_args(call.name, len(call.args))
            text = self._conditional_label(host_sheet, a, call, pairs, first, box)
            for k in ranges:
                if k <= len(call.args):
                    for i in self._refs_in(a, call.args[k - 1]):
                        own[i] = text
        inherited = self._context[-1] if self._context else None
        out: list[str | None] = []
        for i, r in enumerate(a.refs):
            if i in own:
                out.append(own[i])
            elif inherited and self.types.get(host_sheet) == "helpers" and self._same_rows(host_sheet, a, i, r,
                                                                                          first, box):
                out.append(inherited)
            else:
                out.append(None)
        return out

    def _same_rows(self, host_sheet: str, a: Any, i: int, r: Any, first: tuple[int, int],
                   box: tuple[int, int, int, int]) -> bool:
        """True for a reference that holds the same rows as the helper cells being traced: the same helper sheet
        moved over the same rows, or the raw column that an INDEX of the formula reads by row."""
        if r.workbook or r.kind in ("name", "structured", "error"):
            return False
        target = self._resolve_sheet(host_sheet, r.sheet)
        if target is None or target not in self.sheets:
            return False
        if target == host_sheet:
            ts = self.sheets[target]
            b = moved_bounds(r.a1, first, box, (ts.max_col, ts.max_row))
            return b is not None and (b[1], b[3]) == (box[1], box[3])
        if self.types.get(target) == "raw":
            return any(c.name == "INDEX" and c.args and c.args[0][0] <= r.start < c.args[0][1] for c in a.calls)
        return False

    def _conditional_label(self, host_sheet: str, a: Any, call: Any, pairs: list[tuple[int, int]],
                           first: tuple[int, int], box: tuple[int, int, int, int]) -> str:
        single = box[0] == box[2] and box[1] == box[3]
        parts = []
        for rk, ck in pairs:
            if ck > len(call.args):
                continue
            rs, re_ = call.args[rk - 1]
            cs, ce = call.args[ck - 1]
            crit = a.formula[cs:ce].strip()
            text = f"{_short(a.formula[rs:re_].strip(), 60)} matches {_short(crit, 60)}"
            refs = self._refs_in(a, (cs, ce))
            if single and len(refs) == 1 and a.refs[refs[0]].text == crit and a.refs[refs[0]].kind == "cell":
                r = a.refs[refs[0]]
                target = self._resolve_sheet(host_sheet, r.sheet)
                if target in self.sheets and not r.workbook:
                    ts = self.sheets[target]
                    b = moved_bounds(r.a1, first, box, (ts.max_col, ts.max_row))
                    if b is not None and b[0] == b[2] and b[1] == b[3]:
                        v = self._saved(target, b[1], b[0])
                        if v is not None:
                            text += f" ({_fmt(v)})"
            parts.append(text)
        label = f"the rows that can contribute to {call.name} in {_box_text(host_sheet, box)}"
        if parts:
            label += ", where " + " and ".join(parts)
        if not single:
            label += " (the criteria of its first cell)"
        return label

    def _resolve_sheet(self, host_sheet: str, name: str | None) -> str | None:
        if name is None:
            return host_sheet
        if name in self.sheets:
            return name
        return self.lower.get(name.casefold())

    def _ref_node(self, host_sheet: str, r: Any, first: tuple[int, int], box: tuple[int, int, int, int],
                  level: int, max_depth: int | None, done: set) -> TraceNode | None:
        if r.workbook:
            self.nodes += 1
            return TraceNode(r.text, "", "external", None, note="a reference to another workbook: not traced")
        if r.kind == "error":
            self.nodes += 1
            return TraceNode(r.text, "", "error", None, note="#REF!: the cells it pointed to were deleted")
        target_sheet = self._resolve_sheet(host_sheet, r.sheet)
        a1 = r.a1
        if r.kind == "name":
            entry = self.names.get(r.a1.casefold())
            if entry is None:
                self.nodes += 1
                return TraceNode(r.text, "", "name", None, note="a name that is not defined in this workbook")
            _n, value, _scope = entry
            sheet_part, addr = common.parse_ref(value.lstrip("="))
            target_sheet = self._resolve_sheet(host_sheet, sheet_part) if sheet_part else None
            if target_sheet is None or _endpoints(addr) is None:
                self.nodes += 1
                return TraceNode(r.text, "", "name", None, note=f"defined name = {_short(value, 80)}")
            a1 = addr
        if r.kind == "structured":
            resolved = self._structured(host_sheet, r, first)
            if resolved is None:
                self.nodes += 1
                return TraceNode(r.text, "", "name", None, note="a table reference that could not be resolved")
            target_sheet, a1 = resolved
        if target_sheet is None or target_sheet not in self.sheets:
            self.nodes += 1
            return TraceNode(r.text, "", "error", None, note=f"sheet '{r.sheet}' not found")
        ts = self.sheets[target_sheet]
        bounds = moved_bounds(a1, first, box, (ts.max_col, ts.max_row))
        if bounds is None:
            self.nodes += 1
            return TraceNode(r.text, "", "name", None, note="a reference this trace cannot read")
        key = (target_sheet, bounds)
        if key in done:
            return None
        done.add(key)
        whole = _endpoints(a1) is not None and any(e.row is None for e in _endpoints(a1) or [])
        return self._range_node(target_sheet, bounds, level, max_depth, whole_column=whole)

    def _structured(self, host_sheet: str, r: Any, first: tuple[int, int]) -> tuple[str, str] | None:
        from . import compat
        sref = compat.parse_structured(r.a1)
        if sref is None:
            return None
        table = compat._table_for(self.tables, sref.table, host_sheet, first[1], first[0])
        if table is None:
            return None
        c1, r1, c2, r2 = common.range_bounds(table.ref)
        lower = [c.casefold() for c in table.columns]
        cols = [c1 + lower.index(n.casefold()) for n in sref.columns if n.casefold() in lower] or [c1, c2]
        lo, hi = min(cols), max(cols)
        rows = (first[1], first[1]) if sref.this_row else (r1 + 1, r2)
        if any(s.lstrip("#").casefold() == "all" for s in sref.special):
            rows = (r1, r2)
        if sref.this_row:              # moves with the group like a relative same-row reference
            text = f"${common.col_letter(lo)}{rows[0]}:${common.col_letter(hi)}{rows[1]}"
        else:
            text = f"${common.col_letter(lo)}${rows[0]}:${common.col_letter(hi)}${rows[1]}"
        return table.sheet, text

    def _range_node(self, sheet_name: str, bounds: tuple[int, int, int, int], level: int, max_depth: int | None,
                    *, whole_column: bool) -> TraceNode:
        self.nodes += 1
        ref = _box_text(sheet_name, bounds)
        key = (sheet_name, bounds)
        if key in self.seen:
            return TraceNode(ref, "", "seen", None, note="traced above")
        self.seen[key] = ref
        s = self.sheets[sheet_name]
        c1, r1, c2, r2 = bounds
        cols = self._columns(sheet_name)
        cells = [c for cc in range(c1, min(c2, s.max_col) + 1) for c in cols.get(cc, ()) if r1 <= c.row <= r2]
        if not cells:
            return TraceNode(ref, "", "empty", None, count=0)
        cells.sort(key=lambda c: (c.row, c.col))
        formulas = [c for c in cells if c.formula is not None]
        consts = [c for c in cells if c.formula is None and c.formula_kind not in ("array_member", "data_table_member")]
        if whole_column and formulas:
            top = min(c.row for c in formulas)
            consts = [c for c in consts if not (c.row < top and isinstance(c.value, str))]
        if len(cells) == 1 and (c1, r1) == (c2, r2):
            c = cells[0]
            node = self._cell_node(sheet_name, c, c.row, c.col)
            if c.formula is not None:
                node.children = self._precedents(sheet_name, c.formula, (c.col, c.row), (c.col, c.row, c.col, c.row),
                                                 level + 1, max_depth)
            return node
        if not formulas:
            return self._values_node(sheet_name, bounds, consts, ref)
        groups = self._groups(formulas)
        if len(groups) == 1 and not consts:
            return self._group_node(sheet_name, groups[0], level, max_depth)
        parent = TraceNode(ref, self._range_label(sheet_name, bounds), "range", None, count=len(formulas) + len(consts),
                           note=f"{len(groups)} different formula(s)" + (f" and {len(consts)} typed value(s)"
                                                                         if consts else ""))
        for g in groups[:MAX_GROUPS]:
            if self.nodes >= MAX_NODES:
                break
            self.nodes += 1
            parent.children.append(self._group_node(sheet_name, g, level + 1, max_depth))
        if len(groups) > MAX_GROUPS:
            parent.children.append(TraceNode("", "", "cut", None, note=f"and {len(groups) - MAX_GROUPS} more formula "
                                             "groups, not traced"))
        if consts:
            cb = (min(c.col for c in consts), min(c.row for c in consts), max(c.col for c in consts),
                  max(c.row for c in consts))
            parent.children.append(self._values_node(sheet_name, cb, consts, _box_text(sheet_name, cb)))
        return parent

    def _columns(self, sheet_name: str) -> dict[int, list[Any]]:
        """The sheet's cells by column (built once per sheet)."""
        index = self._by_col.get(sheet_name)
        if index is None:
            index = {}
            for (_r, c), cell in self.sheets[sheet_name].cells.items():
                index.setdefault(c, []).append(cell)
            self._by_col[sheet_name] = index
        return index

    def _groups(self, formulas: list[Any]) -> list[list[Any]]:
        groups: dict[str, list[Any]] = {}
        for c in formulas:
            try:
                key = fx.relative_form(c.formula, c.coord)
            except ValueError:
                key = c.formula
            groups.setdefault(key, []).append(c)
        return list(groups.values())

    def _range_label(self, sheet_name: str, bounds: tuple[int, int, int, int]) -> str:
        c1, r1, c2, r2 = bounds
        if c1 == c2:
            h = self.header(sheet_name, c1, r1 if r1 > 1 else 2)
            return f"'{_short(h, 60)}'" if h else ""
        if r1 == r2:
            rl = self.row_label(sheet_name, r1, c1)
            return f"'{_short(rl, 60)}'" if rl else ""
        return ""

    def _group_node(self, sheet_name: str, group: list[Any], level: int, max_depth: int | None) -> TraceNode:
        first = group[0]
        box = (min(c.col for c in group), min(c.row for c in group), max(c.col for c in group),
               max(c.row for c in group))
        c1, r1, c2, r2 = box
        if len(group) == 1:
            node = self._cell_node(sheet_name, first, first.row, first.col)
        else:
            if c1 == c2 and self.types.get(sheet_name) in BLOCK_TYPES:
                kind = "range"
                label = self.header(sheet_name, c1, r1)
                ref = _box_text(sheet_name, box)
                rows = ""
            elif c1 == c2:
                kind = "helper_column"
                label = self.header(sheet_name, c1, r1)
                ref = f"{common.quote_sheet(sheet_name)} column {common.col_letter(c1)}"
                rows = f"rows {r1:,} to {r2:,}"
            else:
                kind = "check" if self.types.get(sheet_name) == "checks" else "range"
                label = self.row_label(sheet_name, r1, c1) if r1 == r2 else None
                ref = _box_text(sheet_name, box)
                rows = ""
            node = TraceNode(ref, f"'{_short(label, 60)}'" if label else "", kind, fx.display_form(first.formula),
                             count=len(group), rows=rows)
            if kind == "range" and c1 == c2:
                node.note = "one formula down the column"
            elif kind == "range" and r1 == r2:
                node.note = "one formula across the row"
            elif kind == "range":
                node.note = "one formula over the block"
            elif kind == "helper_column":
                node.note = "one formula on every row"
        node.children = self._precedents(sheet_name, first.formula, (first.col, first.row), box, level + 1, max_depth)
        return node

    def _values_node(self, sheet_name: str, bounds: tuple[int, int, int, int], consts: list[Any],
                     ref: str) -> TraceNode:
        c1, r1, c2, r2 = bounds
        if self.types.get(sheet_name) == "raw":
            self.reached_raw.add(sheet_name)
            data = [c for c in consts if c.row >= 2] or consts
            lo, hi = min(c.row for c in data), max(c.row for c in data)
            cols = sorted({c.col for c in data})
            names = []
            for col in cols[:8]:
                h = self._text(self.sheets[sheet_name], 1, col)
                names.append(f"{common.col_letter(col)} '{_short(h, 40)}'" if h else common.col_letter(col))
            more = f" and {len(cols) - 8} more columns" if len(cols) > 8 else ""
            what = ("column " if len(cols) == 1 else "columns ") + ", ".join(names) + more
            context = self._context[-1] if self._context else None      # v4, A-aera-13
            return TraceNode(f"{common.quote_sheet(sheet_name)} {what}", "", "raw", None, count=hi - lo + 1,
                             rows=f"rows {lo:,} to {hi:,}", note="raw data" + (f"; {context}" if context else ""))
        if len(consts) == 1:
            c = consts[0]
            return self._cell_node(sheet_name, c, c.row, c.col)
        label = self._range_label(sheet_name, bounds)
        shown = ", ".join(_fmt(c.value) for c in consts[:4]) + (", ..." if len(consts) > 4 else "")
        kind = "input" if self.types.get(sheet_name) == "inputs" else "constants"
        return TraceNode(ref, label, kind, None, count=len(consts), note=f"values {shown}")


# ----------------------------------------------------------------------------- words

def _line(node: TraceNode) -> str:
    if node.kind == "cut":
        return f"({node.note})"
    words = {"input": "input", "raw": "raw data", "helper_column": "helper column", "block_cell": "",
             "check": "check", "constant": "typed value", "range": "", "constants": "typed values",
             "empty": "empty", "external": "outside this workbook", "name": "", "error": "error", "seen": ""}
    head = node.ref
    if node.label:
        head += f" {node.label}"
    bits = []
    if node.rows:
        bits.append(node.rows)
    if node.count > 1 and node.kind not in ("raw",):
        bits.append(f"{node.count:,} cells")
    elif node.kind == "raw" and node.count > 1:
        bits.append(f"{node.count:,} rows")
    kind_word = words.get(node.kind, "")
    if kind_word and node.kind not in ("raw",) or (node.kind == "raw" and not node.note):
        bits.append(kind_word)
    if node.note:
        bits.append(node.note)
    if bits:
        head += " (" + ", ".join(b for b in bits if b) + ")"
    if node.count == 1 and node.kind not in ("empty", "seen", "external", "name", "error") and (
            node.value is not None or (node.formula is None and node.kind != "raw")):
        head += f" = {_fmt(node.value)}"
    if node.formula:
        head += f": {_short(node.formula)}"
    return head


def tree_lines(node: TraceNode, level: int = 0) -> list[str]:
    prefix = "" if level == 0 else "  " * level + "- "
    lines = [prefix + _line(node)]
    for ch in node.children:
        lines += tree_lines(ch, level + 1)
    return lines


# ----------------------------------------------------------------------------- the command

def trace_cell(path: str | Path, ref: str, *, depth: int | None = None, raw_sheets: Sequence[str] = ()) -> Any:
    """Returns a common.Report; data["tree"] is the TraceNode tree as dicts, data["lines"] the text lines."""
    from . import workbook as wbk
    if depth is not None and depth < 1:
        raise common.Refusal(f"--depth {depth}: give 1 or more levels", key="--depth")
    src = Path(common.native_path(path)).absolute()
    sheet_part, cell_part = common.parse_ref(ref)
    info = wbk.open_workbook(src, with_values=True, with_styles=False)
    names = [s.name for s in info.sheets if s.kind == "worksheet"]
    if sheet_part is None:
        if len(names) != 1:
            raise common.Refusal(f"'{ref}' names no sheet", key="ref",
                                 fix=f"write the cell as sheet!cell, e.g. {common.quote_sheet(names[0]) if names else 'calc'}!D7")
        sheet_part = names[0]
    sheet = next((n for n in names if n == sheet_part), None) or next(
        (n for n in names if n.casefold() == sheet_part.casefold()), None)
    if sheet is None:
        import difflib
        near = difflib.get_close_matches(sheet_part, names, n=1, cutoff=0.5)
        hint = f"; did you mean '{near[0]}'?" if near else f"; the sheets are {', '.join(names[:12])}"
        raise common.Refusal(f"the workbook has no sheet '{sheet_part}'{hint}", key="ref")
    try:
        col, row = common.split_cell(cell_part)
    except ValueError:
        raise common.Refusal(f"'{cell_part}' is not one cell", key="ref",
                             fix="give one cell such as summary!D6 (not a range)") from None
    for rs in raw_sheets:
        if rs not in names:
            raise common.Refusal(f"--raw-sheets: the workbook has no sheet '{rs}'", key="--raw-sheets")
    tracer = _Tracer(info, raw_sheets=raw_sheets)
    s = tracer.sheets[sheet]
    c = s.at(row, col)
    report = common.Report(command="trace", target=src.as_posix())
    if c is None:
        raise common.Refusal(f"{sheet}!{cell_part} is empty; there is nothing to trace", key="ref",
                             fix="give a cell that holds a formula, for example a key output")
    root = tracer.trace(sheet, row, col, depth)
    lines = tree_lines(root)
    report.data["tree"] = root.to_dict()
    report.data["lines"] = lines
    report.data["raw_sheets_reached"] = sorted(tracer.reached_raw)
    report.data["nodes"] = tracer.nodes
    if c.formula is None:
        report.note(f"{sheet}!{cell_part} holds a typed value, not a formula: the chain is this one cell")
    elif not tracer.reached_raw:
        report.note("the chain reaches no raw sheet: it ends at inputs and typed values"
                    + (" (raw sheets were inferred from sheet names; name them with --raw-sheets)"
                       if tracer.inferred else ""))
    if tracer.inferred:
        report.note("no manifest: sheet roles (raw, helpers, inputs, checks) were inferred from sheet names and "
                    "query tables")
    if not info.has_cached_values and any(x.is_formula for x in s.cells.values()):
        report.note("the workbook holds no calculated values (not recalculated): values are shown as (no value)")
    if tracer.cut:
        report.note(f"the trace stopped after {MAX_NODES} nodes; trace a cell further down the chain to see more")
    report.finish(False)
    return report


def add_trace_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("workbook")
    p.add_argument("ref", help="the cell to trace, e.g. summary!D6")
    p.add_argument("--depth", type=int, help="stop after this many levels")
    p.add_argument("--json", metavar="FILE", help="write the tree as JSON ('-' = stdout)")
    p.add_argument("--raw-sheets", help="comma-separated raw sheet names (workbooks without a manifest)")


def run_trace(args: argparse.Namespace) -> int:
    raw = [x.strip() for x in (args.raw_sheets or "").split(",") if x.strip()]
    report = trace_cell(args.workbook, args.ref, depth=args.depth, raw_sheets=raw)
    stream = sys.stderr if args.json == "-" else sys.stdout
    for line in report.data.get("lines", []):
        stream.write(line + "\n")
    for n in report.notes:
        stream.write(f"note {n}\n")
    report.write_json(args.json)
    return report.exit_code

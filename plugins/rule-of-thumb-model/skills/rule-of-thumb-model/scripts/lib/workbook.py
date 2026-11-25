"""Read any workbook (never writes): sheets, formulas, cached values, styles, tables, names, package parts;
snapshots and the before/after comparison (design sections 11, 19).

Owner: CORE-IO. openpyxl is imported inside functions only.

- open_workbook() reads a workbook twice with openpyxl: once with formulas (data_only=False, full mode, for
  styles, tables, merged cells, conditional formats and validations) and once read-only with data_only=True
  for the cached values Excel saved. A workbook written by openpyxl has no cached values until Excel has
  recalculated and saved it (WorkbookInfo.has_cached_values is then False).
- package_parts() lists the zip parts with the standard library and names the parts openpyxl drops or damages
  when it saves a workbook it did not create (design 11 step 1). A command that writes an existing workbook
  goes through Excel when any is present, or refuses (design D12).
- Array formulas are read as their text; the other cells of an array or a spilled range are marked
  "array_member" (they hold cached values, not typed numbers). Data-table formulas ({=TABLE(...)}) have no
  formula text in the file; they are marked "data_table" and their depth is not measured (research synthesis D1).
- Theme colours are resolved with the workbook's theme and tint (method of research/scripts/style_survey.py).
"""
from __future__ import annotations

import base64
import colorsys
import datetime as _dt
import io
import math
import re
import struct
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Sequence

# Package parts openpyxl drops or damages on save (design section 11, step 1): (regex on the part name, description).
FRAGILE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"^customXml/item\d+\.xml$", "Power Query query (DataMashup) when the item holds one"),
    (r"^xl/connections\.xml$", "data connections"),
    (r"^xl/queryTables/", "query tables"),
    (r"^xl/charts/", "charts"),
    (r"^xl/drawings/", "drawings (charts, shapes, pictures, comment boxes)"),
    (r"^xl/pivotTables/", "pivot tables"),
    (r"^xl/pivotCache/", "pivot caches"),
    (r"^xl/slicers/", "slicers"),
    (r"^xl/slicerCaches/", "slicer caches"),
    (r"^xl/timelines/", "timelines"),
    (r"^xl/model/", "Data Model"),
    (r"^xl/externalLinks/", "links to other workbooks"),
    (r"^xl/threadedComments/", "threaded comments"),
    (r"^xl/persons/", "comment authors"),
    (r"^xl/richData/", "rich data types"),
    (r"^xl/ctrlProps/", "form controls"),
    (r"^xl/vbaProject\.bin$", "macros"),
    # Added by CORE-IO (also dropped by openpyxl 3.1.5 on save):
    (r"^xl/metadata\.xml$", "dynamic-array metadata (spilled formulas would lose their spill)"),
    (r"^xl/embeddings/", "embedded objects"),
    (r"^xl/activeX/", "ActiveX controls"),
    (r"^xl/webextensions/", "Office add-in parts"),
    (r"^xl/namedSheetViews/", "named sheet views"),
    (r"^xl/featurePropertyBag/", "cell check boxes and feature properties"),
)
# Not parts, found by reading parts (design 11 step 1): worksheet extensions and rich text.
EXTENSION_DESCRIPTION = "worksheet extensions (x14/x15: data bars, icon sets, sparklines, extended validation lists)"
RICH_TEXT_DESCRIPTION = "rich text inside cells (formatting of part of a cell's text)"

ERROR_VALUES = frozenset({"#NULL!", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A", "#GETTING_DATA",
                          "#SPILL!", "#CALC!", "#FIELD!", "#BLOCKED!", "#CONNECT!", "#BUSY!", "#UNKNOWN!",
                          "#EXTERNAL!", "#PYTHON!"})
# Office 2013-2022 default theme in openpyxl's theme-index order (lt1, dk1, lt2, dk2, accent1..6, hlink, folHlink).
DEFAULT_THEME = ("FFFFFF", "000000", "E7E6E6", "44546A", "4472C4", "ED7D31", "A5A5A5", "FFC000", "5B9BD5",
                 "70AD47", "0563C1", "954F72")
READABLE_SUFFIXES = (".xlsx", ".xlsm", ".xltx", ".xltm")
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_CHUNK = 1 << 20


@dataclass
class CellInfo:
    sheet: str
    coord: str                       # "D7"
    row: int
    col: int
    value: Any                       # as stored: formula text "=..." or a constant
    formula: str | None              # formula text with "=", None for constants
    cached: Any = None               # cached value (data_only), None when absent; constants: the value
    data_type: str = ""              # openpyxl data type: n, s, f, b, d, e
    font_name: str | None = None
    font_size: float | None = None
    bold: bool = False
    italic: bool = False
    font_color: str | None = None    # 6-digit hex, theme and tint resolved; None = automatic
    fill: str | None = None          # 6-digit hex; None = no fill
    number_format: str = "General"
    border: dict[str, Any] = field(default_factory=dict)
    h_align: str | None = None
    style_name: str | None = None
    locked: bool = True
    formula_kind: str | None = None  # None | "normal" | "array" | "data_table" | "array_member" | "data_table_member"
    array_ref: str | None = None     # the range of an array formula (anchor cell only)
    cached_type: str | None = None   # data type of the cached value ("e" for an error value)
    wrap: bool = False

    @property
    def is_formula(self) -> bool:
        return self.formula is not None or self.formula_kind == "data_table"

    @property
    def is_error(self) -> bool:
        """True when the cached value is an Excel error value (#DIV/0!, #REF!, ...).

        v4 (A-aera-8): decided from the type the file stores, not from the text. A cell read by open_workbook()
        always has `cached_type` when it has a value: openpyxl's data type of the stored value (t="e" gives "e";
        t="s", "str" and "inlineStr" give "s"), and for a formula cell the type of its cached result. So the text
        '#N/A' in a text cell, or a formula whose result is the text '#N/A', is not an error. Only a CellInfo
        made without `cached_type` falls back to the text of a formula cell's cached value."""
        if self.cached_type is not None:
            return self.cached_type == "e"
        return isinstance(self.cached, str) and self.cached in ERROR_VALUES and self.formula is not None

    @property
    def style(self) -> dict[str, Any]:
        """The style attributes compared by snapshots and the audit."""
        return {"font_name": self.font_name, "font_size": self.font_size, "bold": self.bold, "italic": self.italic,
                "font_color": self.font_color, "fill": self.fill, "number_format": self.number_format,
                "border": self.border, "h_align": self.h_align}


@dataclass
class TableInfo:
    name: str
    sheet: str
    ref: str                         # "A1:F601"
    columns: list[str]
    from_query: bool = False         # loaded by a query or connection


@dataclass
class SheetInfo:
    name: str
    index: int
    state: str                       # visible | hidden | veryHidden
    max_row: int
    max_col: int
    freeze: str | None
    gridlines: bool
    zoom: int | None
    merged: list[str]
    tables: list[TableInfo]
    cf_count: int
    dv_count: int
    cells: dict[tuple[int, int], CellInfo] = field(default_factory=dict)   # (row, col) -> cell
    kind: str = "worksheet"          # worksheet | chartsheet
    array_ranges: list[str] = field(default_factory=list)

    def cell(self, coord: str) -> CellInfo | None:
        from . import common
        col, row = common.split_cell(coord)
        return self.cells.get((row, col))

    def at(self, row: int, col: int) -> CellInfo | None:
        return self.cells.get((row, col))

    def formulas(self) -> Iterator[CellInfo]:
        """Formula cells (normal, array anchors and data tables) in row-major order."""
        for key in sorted(self.cells):
            c = self.cells[key]
            if c.is_formula:
                yield c

    def constants(self) -> Iterator[CellInfo]:
        """Constant cells that are not part of an array or data-table result, in row-major order."""
        for key in sorted(self.cells):
            c = self.cells[key]
            if not c.is_formula and c.formula_kind not in ("array_member", "data_table_member"):
                yield c

    def iter_cells(self) -> Iterator[CellInfo]:
        for key in sorted(self.cells):
            yield self.cells[key]

    def cells_in(self, ref: str) -> Iterator[CellInfo]:
        """Non-empty cells inside a range such as 'B6:D9' (row-major)."""
        from . import common
        c1, r1, c2, r2 = common.range_bounds(ref)
        for (r, c) in sorted(self.cells):
            if r1 <= r <= r2 and c1 <= c <= c2:
                yield self.cells[(r, c)]


@dataclass
class PackageInfo:
    path: Path
    parts: list[str]
    fragile: list[str]               # descriptions with part names, e.g. "charts (xl/charts/chart1.xml)"
    has_power_query: bool = False
    connections: int = 0
    charts: int = 0
    pivots: int = 0
    vba: bool = False
    external_links: int = 0
    query_names: list[str] = field(default_factory=list)   # Power Query query names (from the DataMashup)
    mashup_connections: int = 0      # connections that use the Power Query provider
    tables: int = 0
    drawings: int = 0
    extensions: dict[str, list[str]] = field(default_factory=dict)   # worksheet part -> x14/x15 element names
    rich_text: bool = False
    metadata: bool = False           # xl/metadata.xml (dynamic arrays)

    @property
    def queries(self) -> int:
        """Number of Power Query queries (names read from the DataMashup, else Power Query connections)."""
        return len(self.query_names) or self.mashup_connections


@dataclass
class WorkbookInfo:
    path: Path
    sha256: str
    package: PackageInfo
    sheets: list[SheetInfo]
    defined_names: list[tuple[str, str, str | None]]   # (name, value, sheet scope or None)
    calc_mode: str                   # auto | manual | autoNoTable
    iterate: bool
    has_cached_values: bool          # at least one formula cell has a cached value
    manifest: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)   # what openpyxl or the reader could not read
    truncated: bool = False          # max_cells was reached: later cells were not read
    cell_count: int = 0
    date1904: bool = False

    def sheet(self, name: str) -> SheetInfo | None:
        for s in self.sheets:
            if s.name == name:
                return s
        return None

    @property
    def sheet_names(self) -> list[str]:
        return [s.name for s in self.sheets]

    def tables(self) -> list[TableInfo]:
        return [t for s in self.sheets for t in s.tables]


# ----------------------------------------------------------------------------- colours

def theme_palette(theme_xml: bytes | str | None) -> list[str] | None:
    """Theme colours in openpyxl's theme-index order (lt1, dk1, lt2, dk2, accents, links), or None."""
    if not theme_xml:
        return None
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(theme_xml)
    except ET.ParseError:
        return None
    ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
    scheme = root.find(".//a:clrScheme", ns)
    if scheme is None:
        return None
    vals = []
    for child in list(scheme):
        val = None
        for sub in child:
            tag = sub.tag.split("}")[-1]
            if tag == "srgbClr":
                val = sub.get("val")
            elif tag == "sysClr":
                val = sub.get("lastClr")
        vals.append((val or "000000").upper())
    if len(vals) < 4:
        return None
    vals[0], vals[1] = vals[1], vals[0]
    vals[2], vals[3] = vals[3], vals[2]
    return vals


def apply_tint(rgb: str, tint: float) -> str:
    """Excel's tint on a colour: lighter for tint > 0, darker for tint < 0 (HLS luminance)."""
    if not tint:
        return rgb.upper()
    r, g, b = (int(rgb[i:i + 2], 16) / 255 for i in (0, 2, 4))
    h, lum, s = colorsys.rgb_to_hls(r, g, b)
    lum = lum * (1 + tint) if tint < 0 else lum * (1 - tint) + tint
    r, g, b = colorsys.hls_to_rgb(h, max(0.0, min(1.0, lum)), s)
    return "".join(f"{round(x * 255):02X}" for x in (r, g, b))


def resolve_color(color: Any, theme_colors: list[str] | None, indexed: list[str] | None = None) -> str | None:
    """An openpyxl Color (rgb, indexed, theme with tint) or a hex string as 6-digit hex; None for automatic.
    Without a theme the Office default theme is used."""
    if color is None:
        return None
    if isinstance(color, str):
        text = color.strip().lstrip("#")
        return text[-6:].upper() if re.fullmatch(r"[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8}", text) else None
    try:
        ctype = color.type
        if ctype == "rgb":
            v = color.rgb
            if not isinstance(v, str) or not re.fullmatch(r"[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8}", v):
                return None
            return v[-6:].upper()
        if ctype == "theme":
            idx = color.theme
            palette = theme_colors or list(DEFAULT_THEME)
            if idx is None or idx >= len(palette):
                return None
            return apply_tint(palette[idx], float(color.tint or 0))
        if ctype == "indexed":
            idx = color.indexed
            if idx is None or idx in (64, 65):
                return None
            if indexed is None:
                from openpyxl.styles.colors import COLOR_INDEX
                indexed = list(COLOR_INDEX)
            if 0 <= idx < len(indexed):
                return str(indexed[idx])[-6:].upper()
            return None
    except (AttributeError, TypeError, ValueError):
        return None
    return None


# ----------------------------------------------------------------------------- package parts

def _decode_xml_head(data: bytes) -> str:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", "ignore")
    if data[:3] == b"\xef\xbb\xbf":
        return data[3:].decode("utf-8", "ignore")
    if len(data) > 1 and data[1:2] == b"\x00":
        return data.decode("utf-16-le", "ignore")
    return data.decode("utf-8", "ignore")


def _mashup_names(raw: bytes) -> list[str]:
    """Query names from a customXml DataMashup item (base64 package; Formulas/Section1.m). Best effort."""
    text = _decode_xml_head(raw)
    m = re.search(r"<DataMashup[^>]*>([^<]+)</DataMashup>", text)
    if not m:
        return []
    try:
        blob = base64.b64decode(m.group(1).strip())
        _version, n = struct.unpack("<II", blob[:8])
        with zipfile.ZipFile(io.BytesIO(blob[8:8 + n])) as z:
            name = next((x for x in z.namelist() if x.lower().endswith("section1.m")), None)
            if name is None:
                return []
            m_text = z.read(name).decode("utf-8-sig", "ignore")
    except (ValueError, struct.error, zipfile.BadZipFile, KeyError, OSError):
        return []
    names = []
    for q in re.finditer(r'\bshared\s+(#"(?:[^"]|"")*"|[A-Za-z_][A-Za-z0-9_.]*)\s*=', m_text):
        n = q.group(1)
        names.append(n[2:-1].replace('""', '"') if n.startswith('#"') else n)
    return names


def _scan_member(z: zipfile.ZipFile, name: str, pattern: re.Pattern[bytes], *, start_marker: bytes | None = None,
                 limit: int = 64 << 20) -> list[bytes]:
    """Matches of `pattern` in one zip member, read in chunks; with `start_marker`, only after it."""
    found: list[bytes] = []
    tail = b""
    started = start_marker is None
    read = 0
    with z.open(name) as f:
        while read < limit:
            chunk = f.read(_CHUNK)
            if not chunk:
                break
            read += len(chunk)
            data = tail + chunk
            if not started:
                pos = data.find(start_marker)
                if pos < 0:
                    tail = data[-len(start_marker):]
                    continue
                started = True
                data = data[pos:]
            found.extend(m.group(0) for m in pattern.finditer(data))
            tail = data[-64:]
            if len(found) > 200:
                break
    return found


_EXT_ELEMENT = re.compile(rb"<(?:x14|x15|x12ac|xcalcf):([A-Za-z]+)")
_EXT_KINDS = frozenset({"conditionalFormattings", "dataValidations", "sparklineGroups", "slicerList", "protectedRanges",
                        "ignoredErrors", "webExtensions", "timelineRefs", "tableSlicerCache"})
_RICH_RUN = re.compile(rb"<si><r>|<r><rPr>|<r><t[ >]")


def package_parts(path: str | Path) -> PackageInfo:
    """List the zip parts and the fragile ones (FRAGILE_PATTERNS; customXml only when it holds a DataMashup;
    worksheet extension lists; rich text in shared strings). Raises common.Refusal for a file that is not an
    OOXML package."""
    from . import common
    p = common.native_path(path)
    _check_readable(p)
    try:
        z = zipfile.ZipFile(p)
    except (zipfile.BadZipFile, OSError) as exc:
        raise common.Refusal(f"{p.name}: not a readable .xlsx package ({type(exc).__name__}: {exc})",
                             fix="open it in Excel and save it as .xlsx") from None
    with z:
        parts = sorted(z.namelist())
        info = PackageInfo(path=p, parts=parts, fragile=[])
        groups: dict[str, list[str]] = {}
        for name in parts:
            for pattern, desc in FRAGILE_PATTERNS:
                if not re.match(pattern, name):
                    continue
                if desc.startswith("Power Query"):
                    try:
                        raw = z.read(name)
                    except (KeyError, OSError, zipfile.BadZipFile):
                        break
                    if "DataMashup" not in _decode_xml_head(raw[:4096]):
                        break
                    info.has_power_query = True
                    info.query_names += _mashup_names(raw)
                    desc = "Power Query queries (DataMashup)"
                if "/_rels/" in name:
                    break
                groups.setdefault(desc, []).append(name)
                break
        info.charts = sum(1 for n in parts if re.match(r"^xl/charts/chart(?:Ex)?\d+\.xml$", n))
        info.pivots = sum(1 for n in parts if re.match(r"^xl/pivotTables/pivotTable\d+\.xml$", n))
        info.tables = sum(1 for n in parts if re.match(r"^xl/tables/table\d+\.xml$", n))
        info.drawings = sum(1 for n in parts if re.match(r"^xl/drawings/drawing\d+\.xml$", n))
        info.external_links = sum(1 for n in parts if re.match(r"^xl/externalLinks/externalLink\d+\.xml$", n))
        info.vba = "xl/vbaProject.bin" in parts
        info.metadata = "xl/metadata.xml" in parts
        if "xl/connections.xml" in parts:
            try:
                text = z.read("xl/connections.xml").decode("utf-8", "ignore")
                info.connections = len(re.findall(r"<connection\b", text))
                info.mashup_connections = len(re.findall(r"Microsoft\.Mashup\.OleDb", text))
            except (KeyError, OSError, zipfile.BadZipFile):
                pass
        for name in parts:
            if re.match(r"^xl/worksheets/sheet\d+\.xml$", name):
                try:
                    found = _scan_member(z, name, _EXT_ELEMENT, start_marker=b"<extLst")
                except (KeyError, OSError, zipfile.BadZipFile):
                    continue
                if found:
                    seen = {m.split(b":")[1].decode("ascii", "ignore") for m in found}
                    kinds = sorted(seen & _EXT_KINDS) or ["other"]
                    info.extensions[name] = kinds
        if info.extensions:
            groups.setdefault(EXTENSION_DESCRIPTION, []).extend(
                f"{n}: {', '.join(k)}" for n, k in info.extensions.items())
        if "xl/sharedStrings.xml" in parts:
            try:
                info.rich_text = bool(_scan_member(z, "xl/sharedStrings.xml", _RICH_RUN))
            except (KeyError, OSError, zipfile.BadZipFile):
                info.rich_text = False
            if info.rich_text:
                groups.setdefault(RICH_TEXT_DESCRIPTION, []).append("xl/sharedStrings.xml")
    for _pattern, desc in FRAGILE_PATTERNS + ((None, "Power Query queries (DataMashup)"),
                                              (None, EXTENSION_DESCRIPTION), (None, RICH_TEXT_DESCRIPTION)):
        names = groups.pop(desc, None)
        if names:
            shown = ", ".join(names[:3]) + (f", and {len(names) - 3} more" if len(names) > 3 else "")
            count = f"{len(names)} parts: " if len(names) > 1 else ""
            info.fragile.append(f"{desc} ({count}{shown})")
    return info


def fragile_parts(path: str | Path) -> list[str]:
    """package_parts(path).fragile."""
    return package_parts(path).fragile


def _check_readable(p: Path) -> None:
    from . import common
    if not p.is_file():
        raise common.Refusal(f"{p.as_posix()}: workbook not found", fix="check the path")
    suffix = p.suffix.lower()
    try:
        with open(p, "rb") as f:
            head = f.read(8)
    except OSError as exc:
        raise common.Refusal(f"{p.name}: cannot be opened ({exc.strerror or exc})",
                             fix="close the workbook in Excel if it is open there, or check its permissions") from None
    if head.startswith(_OLE_MAGIC):
        if suffix in READABLE_SUFFIXES:
            raise common.Refusal(f"{p.name}: the file is encrypted (password protected) or not an OOXML workbook "
                                 "(it is an OLE compound file)",
                                 fix="remove the password in Excel (File > Info > Protect Workbook) and save as .xlsx")
        raise common.Refusal(f"{p.name}: the old binary .xls format cannot be read",
                             fix="open it in Excel and save it as .xlsx")
    if suffix not in READABLE_SUFFIXES:
        if suffix == ".xlsb":
            raise common.Refusal(f"{p.name}: the binary .xlsb format cannot be read", fix="save it as .xlsx in Excel")
        raise common.Refusal(f"{p.name}: '{suffix or '(no extension)'}' is not a workbook extension this tool reads",
                             fix="give an .xlsx or .xlsm file")
    if not head.startswith(b"PK"):
        raise common.Refusal(f"{p.name}: not a zip package, so not a readable .xlsx (damaged or not a workbook)",
                             fix="open it in Excel and save it as .xlsx")


# ----------------------------------------------------------------------------- reading

def _side(side: Any, theme: list[str] | None, indexed: list[str] | None) -> dict[str, Any] | None:
    if side is None or not side.style:
        return None
    return {"style": side.style, "color": resolve_color(side.color, theme, indexed)}


def _fill_hex(fill: Any, theme: list[str] | None, indexed: list[str] | None) -> str | None:
    if fill is None:
        return None
    if getattr(fill, "tagname", "") == "gradientFill":
        stops = getattr(fill, "stop", None) or []
        return resolve_color(stops[0].color, theme, indexed) if stops else None
    pattern = getattr(fill, "patternType", None) or getattr(fill, "fill_type", None)
    if pattern in (None, "none"):
        return None
    return resolve_color(fill.fgColor, theme, indexed) or ("000000" if pattern == "solid" else None)


def _style_attrs(cell: Any, theme: list[str] | None, indexed: list[str] | None) -> dict[str, Any]:
    font = cell.font
    border = {}
    for name in ("left", "right", "top", "bottom"):
        s = _side(getattr(cell.border, name, None), theme, indexed)
        if s:
            border[name] = s
    try:
        style_name = cell.style
    except (AttributeError, IndexError, KeyError):
        style_name = None
    return {"font_name": font.name if font else None,
            "font_size": float(font.sz) if font is not None and font.sz is not None else None,
            "bold": bool(font.b) if font is not None else False,
            "italic": bool(font.i) if font is not None else False,
            "font_color": resolve_color(font.color, theme, indexed) if font is not None else None,
            "fill": _fill_hex(cell.fill, theme, indexed),
            "number_format": cell.number_format or "General",
            "border": border,
            "h_align": cell.alignment.horizontal if cell.alignment is not None else None,
            "wrap": bool(cell.alignment.wrap_text) if cell.alignment is not None else False,
            "style_name": style_name,
            "locked": bool(cell.protection.locked) if cell.protection is not None else True}


def _formula_of(value: Any, data_type: str | None = None) -> tuple[str | None, str | None, str | None]:
    """(formula text, formula kind, array ref) of a stored cell value.

    `data_type` (v4, A-aera-8): openpyxl's data type of the cell as loaded. openpyxl's reader
    (WorkSheetParser.parse_cell, openpyxl 3.1.5) gives "f" only to a cell that has an <f> element in the sheet XML
    and keeps the stored type of every other cell (t="s", "str" and "inlineStr" give "s"; "n", "b", "e", "d").
    So a text cell whose text starts with '=' (data type "s") is not a formula. None (a caller that does not know
    the stored type) keeps the text rule."""
    if isinstance(value, str):
        if value.startswith("=") and len(value) > 1 and data_type in (None, "f"):
            return value, "normal", None
        return None, None, None
    cls = type(value).__name__
    if cls == "ArrayFormula":
        text = value.text or ""
        if not text.startswith("="):
            text = "=" + text
        return text, "array", str(value.ref) if value.ref else None
    if cls == "DataTableFormula":
        return None, "data_table", str(getattr(value, "ref", "") or "") or None
    return None, None, None


class _PivotCaches(dict):
    def __missing__(self, key: Any) -> None:
        return None


@contextmanager
def _light_pivot_caches() -> Iterator[None]:
    """While a workbook is read: openpyxl 3.1.5 parses every pivot cache with all its records once per worksheet
    (WorkbookParser.pivot_caches is a property, read in read_worksheets for each sheet). A dashboard workbook with
    18 sheets took 38 s to open. The reader never needs pivot records, so for the load the property is replaced
    by one that parses each cache definition once and leaves its records out. Restored afterwards."""
    try:
        from openpyxl.packaging.relationship import get_rel
        from openpyxl.pivot.cache import CacheDefinition
        from openpyxl.reader import workbook as reader_workbook
    except ImportError:          # another openpyxl layout: read as it is
        yield
        return
    original = reader_workbook.WorkbookParser.__dict__.get("pivot_caches")

    def light(self: Any) -> dict:
        cached = getattr(self, "_rotm_pivot_caches", None)
        if cached is None:
            cached = _PivotCaches()
            for c in getattr(self, "caches", []) or []:
                try:
                    cache = get_rel(self.archive, self.rels, id=c.id, cls=CacheDefinition)
                except Exception:  # noqa: BLE001 - a damaged cache is left out; the pivot table keeps no cache
                    continue
                cache.records = None
                cached[c.cacheId] = cache
            self._rotm_pivot_caches = cached
        return cached

    reader_workbook.WorkbookParser.pivot_caches = property(light)
    try:
        yield
    finally:
        if original is not None:
            reader_workbook.WorkbookParser.pivot_caches = original


def open_workbook(path: str | Path, *, with_values: bool = True, with_styles: bool = True,
                  max_cells: int = 2_000_000) -> WorkbookInfo:
    """Read a workbook (formulas with data_only=False, cached values with data_only=True).
    Never writes. Raises common.Refusal for an unreadable file (encrypted, not OOXML, damaged), naming the reason."""
    import warnings

    from . import common
    p = Path(common.native_path(path)).absolute()
    package = package_parts(p)
    sha = common.file_sha256(p)
    import openpyxl
    from openpyxl.chartsheet import Chartsheet
    notes: list[str] = []
    with warnings.catch_warnings(record=True) as caught, _light_pivot_caches():
        warnings.simplefilter("always")
        try:
            wb = openpyxl.load_workbook(p, data_only=False, keep_links=False)
        except Exception as exc:  # noqa: BLE001 - every reader failure becomes a refusal naming it (F06)
            raise common.Refusal(f"{p.name}: openpyxl cannot read this workbook ({type(exc).__name__}: "
                                 f"{str(exc)[:200]})",
                                 fix="open it in Excel, repair it if Excel offers to, and save it as .xlsx") from None
    for w in caught:
        text = str(w.message)
        if text not in notes:
            notes.append(text)
    theme = theme_palette(getattr(wb, "loaded_theme", None))
    indexed = list(wb._colors) if getattr(wb, "_colors", None) else None
    style_cache: dict[Any, dict[str, Any]] = {}
    sheets: list[SheetInfo] = []
    total = 0
    truncated = False
    for index, ws in enumerate(wb._sheets):
        if isinstance(ws, Chartsheet):
            sheets.append(SheetInfo(ws.title, index, ws.sheet_state, 0, 0, None, True, None, [], [], 0, 0,
                                    kind="chartsheet"))
            continue
        view = ws.sheet_view
        tables = []
        for t in ws.tables.values():           # TableList.items() gives (name, ref); values() gives the tables
            cols = [c.name for c in (t.tableColumns or [])]
            tables.append(TableInfo(name=t.displayName or t.name, sheet=ws.title, ref=str(t.ref), columns=cols,
                                    from_query=(t.tableType == "queryTable")))
        try:
            cf_count = sum(len(cf.rules) for cf in ws.conditional_formatting)
        except (AttributeError, TypeError):
            cf_count = 0
        dv_count = len(ws.data_validations.dataValidation) if ws.data_validations is not None else 0
        info = SheetInfo(name=ws.title, index=index, state=ws.sheet_state, max_row=ws.max_row, max_col=ws.max_column,
                         freeze=ws.freeze_panes, gridlines=view.showGridLines is not False,
                         zoom=int(view.zoomScale) if view.zoomScale else None,
                         merged=[str(r) for r in ws.merged_cells.ranges], tables=tables, cf_count=cf_count,
                         dv_count=dv_count)
        arrays: list[tuple[str, int, int]] = []
        for (r, c), cell in ws._cells.items():
            value = cell.value
            if value is None:
                continue
            if total >= max_cells:
                truncated = True
                break
            total += 1
            formula, fkind, aref = _formula_of(value, cell.data_type or None)
            if fkind == "data_table":
                value = f"{{=TABLE({getattr(value, 'r1', '') or ''},{getattr(value, 'r2', '') or ''})}}"
            ci = CellInfo(sheet=ws.title, coord=f"{common.col_letter(c)}{r}", row=r, col=c, value=value,
                          formula=formula, data_type=cell.data_type or "", formula_kind=fkind, array_ref=aref)
            if fkind is None:
                ci.cached = value
                ci.cached_type = cell.data_type
            if fkind in ("array", "data_table") and aref:
                arrays.append((aref, r, c))
            if with_styles:
                key = tuple(cell._style) if getattr(cell, "_style", None) is not None else None
                attrs = style_cache.get(key) if key is not None else None
                if attrs is None:
                    try:
                        attrs = _style_attrs(cell, theme, indexed)
                    except Exception as exc:  # noqa: BLE001 - an unreadable style is a note, not a crash (F06)
                        attrs = {}
                        msg = f"some cell styles could not be read ({type(exc).__name__}); they count as unstyled"
                        if msg not in notes:
                            notes.append(msg)
                    if key is not None:
                        style_cache[key] = attrs
                for k, v in attrs.items():
                    setattr(ci, k, v)
            info.cells[(r, c)] = ci
        for aref, ar, ac in arrays:
            anchor = info.cells.get((ar, ac))
            member = "data_table_member" if anchor is not None and anchor.formula_kind == "data_table" else "array_member"
            try:
                cells = list(common.iter_range(aref))
            except ValueError:
                continue
            if len(cells) > 1:
                info.array_ranges.append(aref)
            for (r, c) in cells:
                if (r, c) == (ar, ac):
                    continue
                m = info.cells.get((r, c))
                if m is not None and m.formula is None:
                    m.formula_kind = member
        sheets.append(info)
        if truncated:
            notes.append(f"only the first {max_cells} non-empty cells were read; later cells and sheets were not")
            break
    names: list[tuple[str, str, str | None]] = []
    try:
        for nm, dn in wb.defined_names.items():
            names.append((nm, str(dn.value), None))
        for ws in wb.worksheets:
            for nm, dn in ws.defined_names.items():
                names.append((nm, str(dn.value), ws.title))
    except (AttributeError, TypeError):
        notes.append("defined names could not be read")
    calc = getattr(wb, "calculation", None)
    calc_mode = (getattr(calc, "calcMode", None) or "auto") if calc is not None else "auto"
    iterate = bool(getattr(calc, "iterate", False)) if calc is not None else False
    date1904 = bool(getattr(wb, "epoch", None) and wb.epoch.year == 1904)
    try:
        wb.close()
    except Exception:  # noqa: BLE001
        pass
    has_cached = False
    if with_values and any(c.is_formula for s in sheets for c in s.cells.values()):
        has_cached = _read_cached(p, sheets, notes)
    manifest = None
    try:
        from . import manifest as manifest_mod
        manifest = manifest_mod.load_manifest(p)
    except common.RotmError as exc:
        notes.append(f"manifest not used: {exc.line()}")
    return WorkbookInfo(path=p, sha256=sha, package=package, sheets=sheets, defined_names=names,
                        calc_mode=str(calc_mode), iterate=iterate, has_cached_values=has_cached, manifest=manifest,
                        warnings=notes, truncated=truncated, cell_count=total, date1904=date1904)


def _read_cached(p: Path, sheets: list[SheetInfo], notes: list[str]) -> bool:
    """Fill CellInfo.cached for formula cells from a read-only data_only pass. True when any cached value exists."""
    import warnings

    import openpyxl
    by_name = {s.name: s for s in sheets if s.kind == "worksheet"}
    found = False
    with warnings.catch_warnings():          # read-only sheets parse lazily, so the whole pass stays inside
        warnings.simplefilter("ignore")
        try:
            wbv = openpyxl.load_workbook(p, read_only=True, data_only=True, keep_links=False)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"cached values not read ({type(exc).__name__}: {str(exc)[:120]})")
            return False
        try:
            for ws in wbv.worksheets:
                info = by_name.get(ws.title)
                if info is None or not any(c.is_formula for c in info.cells.values()):
                    continue
                try:
                    ws.reset_dimensions()
                except AttributeError:
                    pass
                for row in ws.iter_rows():
                    for c in row:
                        v = getattr(c, "value", None)
                        if v is None:
                            continue
                        ci = info.cells.get((c.row, c.column))
                        if ci is not None and ci.is_formula:
                            ci.cached = v
                            ci.cached_type = getattr(c, "data_type", None)
                            found = True
        except Exception as exc:  # noqa: BLE001
            notes.append(f"cached values only partly read ({type(exc).__name__}: {str(exc)[:120]})")
        finally:
            wbv.close()
    return found


# ----------------------------------------------------------------------------- snapshots and comparison

@dataclass
class Snapshot:
    path: Path
    sheets: list[str]
    formulas: dict[tuple[str, str], str]          # (sheet, coord) -> formula text
    values: dict[tuple[str, str], Any]            # (sheet, coord) -> constant or cached value
    styles: dict[tuple[str, str], dict[str, Any]] | None
    tables: dict[str, str]                        # table name -> "sheet!ref"
    names: dict[str, str]                         # defined name ("sheet!name" when sheet-scoped) -> value
    charts: int
    cf: dict[str, int]                            # sheet -> conditional-format rule count
    dv: dict[str, int]                            # sheet -> data-validation count
    queries: int                                  # Power Query queries
    has_cached_values: bool = False
    merged: dict[str, int] = field(default_factory=dict)   # sheet -> merged range count
    pivots: int = 0
    formula_cells: set[tuple[str, str]] = field(default_factory=set)   # cells holding a formula (incl. data tables)
    sha256: str = ""


@dataclass
class Intended:
    """What a command may change (written before the command runs; design section 11, step 6).
    kind "styles_only": style attributes may change anywhere; "cells": the listed cells may change their formula
    and style (their values must stay equal unless values_may_change); "none": nothing may change.
    new_sheets and new_rows are always allowed to appear."""
    kind: str                                     # "styles_only" | "cells" | "none"
    cells: set[tuple[str, str]] = field(default_factory=set)
    new_sheets: set[str] = field(default_factory=set)
    new_rows: dict[str, tuple[int, int]] = field(default_factory=dict)   # sheet -> (first, last) new rows
    values_may_change: bool = False


@dataclass
class Difference:
    kind: str                                     # formula | value | style | sheet | table | name | chart | cf | dv | query | merged | pivot
    sheet: str | None
    cell: str | None
    before: Any
    after: Any
    message: str


def snapshot(path: str | Path, *, styles: bool = False) -> Snapshot:
    """Everything compare() looks at, read from one workbook (never written)."""
    info = open_workbook(path, with_values=True, with_styles=styles)
    formulas: dict[tuple[str, str], str] = {}
    values: dict[tuple[str, str], Any] = {}
    style_map: dict[tuple[str, str], dict[str, Any]] | None = {} if styles else None
    fcells: set[tuple[str, str]] = set()
    for s in info.sheets:
        for c in s.cells.values():
            key = (s.name, c.coord)
            if c.formula is not None:
                formulas[key] = c.formula
                fcells.add(key)
            elif c.formula_kind == "data_table":
                formulas[key] = str(c.value)
                fcells.add(key)
            elif c.formula_kind in ("array_member", "data_table_member"):
                fcells.add(key)                        # results of a formula: compared like formula cells
            values[key] = c.cached
            if style_map is not None:
                style_map[key] = c.style
    names = {(f"{scope}!{n}" if scope else n): v for n, v, scope in info.defined_names}
    return Snapshot(path=info.path, sheets=info.sheet_names, formulas=formulas, values=values, styles=style_map,
                    tables={t.name: f"{t.sheet}!{t.ref}" for t in info.tables()}, names=names,
                    charts=info.package.charts, cf={s.name: s.cf_count for s in info.sheets},
                    dv={s.name: s.dv_count for s in info.sheets}, queries=info.package.queries,
                    has_cached_values=info.has_cached_values, merged={s.name: len(s.merged) for s in info.sheets},
                    pivots=info.package.pivots, formula_cells=fcells, sha256=info.sha256)


def normalise_formula(formula: str | None) -> str | None:
    """Formula text for comparison: _xlfn./_xlws. prefixes removed and upper case outside text literals
    (Excel stores function names and references in upper case when it saves)."""
    if formula is None:
        return None
    text = formula.replace("_xlfn._xlws.", "").replace("_xlfn.", "").replace("_xlws.", "")
    out, inside = [], False
    for ch in text:
        if ch == '"':
            inside = not inside
            out.append(ch)
        else:
            out.append(ch if inside else ch.upper())
    return "".join(out)


def _as_number(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, _dt.datetime):
        return (v - _dt.datetime(1899, 12, 30)).total_seconds() / 86400.0
    if isinstance(v, _dt.date):
        return float((v - _dt.date(1899, 12, 30)).days)
    if isinstance(v, _dt.time):
        return (v.hour * 3600 + v.minute * 60 + v.second + v.microsecond / 1e6) / 86400.0
    return None


# Relative tolerance for calculated values (cached values of formula cells) only; typed values are compared
# exactly (v2, A-aera-4). A difference d between a and b is within it when d <= tolerance * max(1, |a|, |b|).
DEFAULT_CALC_TOLERANCE = 1e-9


def values_equal(a: Any, b: Any, *, tolerance: float = 0.0) -> bool:
    """Numbers (and dates and times as Excel serials) equal only when exactly equal, unless `tolerance` > 0:
    then abs(a - b) <= tolerance * max(1, abs(a), abs(b)) (relative, with a floor of 1). Text and booleans
    exactly; a boolean never equals a number; None equals empty text."""
    if a is None or b is None:
        return (a in (None, "")) and (b in (None, ""))
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and a == b:
        return True                    # exact, also for whole numbers beyond 2**53 (Python compares by value)
    na, nb = _as_number(a), _as_number(b)
    if na is not None and nb is not None:
        if na == nb and not (isinstance(a, int) and isinstance(b, int)):
            return True
        if tolerance and tolerance > 0 and math.isfinite(na) and math.isfinite(nb):
            return abs(na - nb) <= tolerance * max(1.0, abs(na), abs(nb))
        return False
    return a == b


def _short(v: Any) -> str:
    text = repr(v)
    return text if len(text) <= 60 else text[:57] + "..."


def _cell_order(sheets: Sequence[str]) -> Any:
    """Sort key of (sheet, coord): the sheet's place in the workbook, then row, then column."""
    from . import common
    place = {s: i for i, s in enumerate(sheets)}

    def key(item: tuple[str, str]) -> tuple[int, str, int, int]:
        sheet, coord = item
        try:
            col, row = common.split_cell(coord)
        except ValueError:
            col, row = 0, 0
        return place.get(sheet, len(place)), sheet, row, col

    return key


def compare(before: Snapshot, after: Snapshot, intended: Intended, *, max_differences: int = 10_000,
            calc_tolerance: float = DEFAULT_CALC_TOLERANCE, skip_values: set[tuple[str, str]] | None = None,
            stats: dict[str, Any] | None = None) -> list[Difference]:
    """Every difference outside `intended`. Formulas are compared as text (normalise_formula); typed values
    (constants) exactly; cached values of formula cells within the relative `calc_tolerance` (values_equal), and
    only when both snapshots hold cached values. Cells in `skip_values` (for example volatile_cells()) keep their
    formula compared but not their cached value. `stats`, when given, is filled with cells_compared,
    values_left_out, first_left_out ('Sheet!A1' or None), calc_tolerance and calculated_values_compared (the
    input of preservation_statement()). Stops after `max_differences`."""
    diffs: list[Difference] = []
    skip = skip_values or set()
    counted = {"cells": 0, "left_out": 0}
    left_out: list[tuple[str, str]] = []

    def fill_stats() -> None:
        if stats is None:
            return
        first = min(left_out, key=_cell_order(before.sheets)) if left_out else None
        stats.update(cells_compared=counted["cells"], values_left_out=counted["left_out"],
                     first_left_out=f"{first[0]}!{first[1]}" if first else None, calc_tolerance=calc_tolerance,
                     calculated_values_compared=bool(before.has_cached_values and after.has_cached_values))

    def add(kind: str, sheet: str | None, cell: str | None, b: Any, a: Any, message: str) -> bool:
        diffs.append(Difference(kind, sheet, cell, b, a, message))
        if len(diffs) >= max_differences:
            fill_stats()
            return True
        return False

    def is_new(sheet: str, coord: str) -> bool:
        if sheet in intended.new_sheets:
            return True
        rows = intended.new_rows.get(sheet)
        if rows:
            from . import common
            _c, r = common.split_cell(coord)
            return rows[0] <= r <= rows[1]
        return False

    for s in before.sheets:
        if s not in after.sheets:
            if add("sheet", s, None, s, None, f"sheet '{s}' was removed"):
                return diffs
    for s in after.sheets:
        if s not in before.sheets and s not in intended.new_sheets:
            if add("sheet", s, None, None, s, f"sheet '{s}' was added but was not an intended change"):
                return diffs
    compare_cached = before.has_cached_values and after.has_cached_values
    keys = sorted(set(before.values) | set(after.values) | set(before.formulas) | set(after.formulas))
    for key in keys:
        sheet, coord = key
        if sheet not in before.sheets:
            continue                                   # a new sheet (already reported when not intended)
        if is_new(sheet, coord) and key not in before.values and key not in before.formulas:
            continue
        counted["cells"] += 1
        intended_cell = intended.kind == "cells" and key in intended.cells
        fb, fa = before.formulas.get(key), after.formulas.get(key)
        if not intended_cell and normalise_formula(fb) != normalise_formula(fa):
            what = ("formula changed" if fb and fa else "formula added" if fa else "formula removed")
            if add("formula", sheet, coord, fb, fa, f"{sheet}!{coord}: {what} ({_short(fb)} -> {_short(fa)})"):
                return diffs
            continue
        is_formula = key in before.formula_cells or key in after.formula_cells
        if is_formula and not compare_cached:
            continue
        if intended_cell and intended.values_may_change:
            continue
        if is_formula and key in skip:
            counted["left_out"] += 1
            left_out.append(key)
            continue
        vb, va = before.values.get(key), after.values.get(key)
        if not values_equal(vb, va, tolerance=calc_tolerance if is_formula else 0.0):
            label = "cached value" if is_formula else "value"
            if add("value", sheet, coord, vb, va, f"{sheet}!{coord}: {label} changed ({_short(vb)} -> {_short(va)})"):
                return diffs
    if before.styles is not None and after.styles is not None and intended.kind != "styles_only":
        for key in sorted(set(before.styles) | set(after.styles)):
            sheet, coord = key
            if sheet not in before.sheets or (intended.kind == "cells" and key in intended.cells):
                continue
            if is_new(sheet, coord) and key not in before.styles:
                continue
            sb, sa = before.styles.get(key), after.styles.get(key)
            if sb != sa:
                changed = sorted(k for k in set(sb or {}) | set(sa or {}) if (sb or {}).get(k) != (sa or {}).get(k))
                if add("style", sheet, coord, sb, sa, f"{sheet}!{coord}: style changed ({', '.join(changed)})"):
                    return diffs
    for name in sorted(set(before.tables) | set(after.tables)):
        b, a = before.tables.get(name), after.tables.get(name)
        if b == a:
            continue
        if b is None and a is not None and a.split("!")[0] in intended.new_sheets:
            continue
        msg = (f"table '{name}' was removed" if a is None else f"table '{name}' was added" if b is None
               else f"table '{name}' changed from {b} to {a}")
        if add("table", None, None, b, a, msg):
            return diffs
    for name in sorted(set(before.names) | set(after.names)):
        if name.startswith("_xlfn.") or "!_xlfn." in name:
            continue                                   # hidden names Excel writes for newer functions
        b, a = before.names.get(name), after.names.get(name)
        if b != a:
            msg = (f"defined name '{name}' was removed" if a is None else f"defined name '{name}' was added"
                   if b is None else f"defined name '{name}' changed from {b} to {a}")
            if add("name", None, None, b, a, msg):
                return diffs
    for kind, b, a, what in (("chart", before.charts, after.charts, "charts"),
                             ("query", before.queries, after.queries, "Power Query queries"),
                             ("pivot", before.pivots, after.pivots, "pivot tables")):
        if b != a and add(kind, None, None, b, a, f"{what}: {b} before, {a} after"):
            return diffs
    for kind, bmap, amap, what in (("cf", before.cf, after.cf, "conditional-format rules"),
                                   ("dv", before.dv, after.dv, "data validations"),
                                   ("merged", before.merged, after.merged, "merged ranges")):
        for sheet in before.sheets:
            if sheet in after.sheets and bmap.get(sheet, 0) != amap.get(sheet, 0):
                if add(kind, sheet, None, bmap.get(sheet, 0), amap.get(sheet, 0),
                       f"sheet '{sheet}': {what} {bmap.get(sheet, 0)} before, {amap.get(sheet, 0)} after"):
                    return diffs
    fill_stats()
    return diffs


# ----------------------------------------------------------------------------- volatile cells (v2, A-aera-16)

_MAX_ROW = 1_048_576
_MAX_COL = 16_384
_VOLATILE_HINT = re.compile(r"(?i)(?<![A-Za-z0-9_.])(?:_xlfn\.)?(?:RAND|RANDBETWEEN|RANDARRAY|NOW|TODAY|OFFSET|"
                            r"INDIRECT|CELL|INFO)\s*\(")
_A1_END = re.compile(r"^\$?([A-Za-z]{1,3})?\$?(\d+)?$")


def _holds_volatile(text: str) -> bool:
    """True when a formula (or a defined name's formula) calls a volatile function (lib.functions.VOLATILE)."""
    if not text or not _VOLATILE_HINT.search(text):
        return False
    from . import formula, functions
    a = formula.analyze(text if text.startswith("=") else "=" + text)
    return any(fn in functions.VOLATILE for fn in a.functions)


def _end_bounds(part: str) -> tuple[int | None, int | None] | None:
    from . import common
    k = part.rfind("!")
    m = _A1_END.match(part[k + 1:] if k >= 0 else part)
    if not m or (m.group(1) is None and m.group(2) is None):
        return None
    col = common.col_index(m.group(1)) if m.group(1) else None
    row = int(m.group(2)) if m.group(2) else None
    return col, row


def _a1_area(a1: str) -> tuple[int, int, int, int] | None:
    """'B2' -> (2, 2, 2, 2); 'A1:C9'; 'C:C' -> whole column; '2:2' -> whole row. (c1, r1, c2, r2) or None."""
    ends = [_end_bounds(p) for p in a1.replace("$", "").split(":")]
    if not ends or any(e is None for e in ends):
        return None
    cols = [e[0] for e in ends]
    rows = [e[1] for e in ends]
    c1, c2 = (1, _MAX_COL) if any(c is None for c in cols) else (min(cols), max(cols))
    r1, r2 = (1, _MAX_ROW) if any(r is None for r in rows) else (min(rows), max(rows))
    return c1, r1, c2, r2


_Area = tuple[str, tuple[int, int, int, int]]


def _ref_resolver(snapshot: Snapshot, volatile_names: set[str]) -> Any:
    """ref_areas(ref, host sheet) -> the areas [(sheet, (c1, r1, c2, r2))] a formula.Ref refers to (a cell, a
    range, a whole column or row, a defined name that names a range, a table named in a structured reference);
    None when the reference is a defined name in `volatile_names`. References to other workbooks give []."""
    from . import common, formula
    lower_names = {k.lower(): k for k in snapshot.names}
    lower_tables = {k.lower(): v for k, v in snapshot.tables.items()}

    def name_areas(name: str, host: str, depth: int) -> list[_Area] | None:
        """Areas a defined name refers to; None when the name is volatile."""
        for key in (f"{host}!{name}", name):
            match = lower_names.get(key.lower())
            if match is None:
                continue
            if match in volatile_names:
                return None
            out: list[_Area] = []
            if depth > 5:
                return out
            value = str(snapshot.names[match])
            for r in formula.references(value if value.startswith("=") else "=" + value):
                got = ref_areas(r, host, depth + 1)
                if got is None:
                    return None
                out += got
            return out
        return []

    def ref_areas(r: Any, host: str, depth: int = 0) -> list[_Area] | None:
        if r.workbook:
            return []
        sheet = r.sheet or host
        if r.kind in ("cell", "range", "column", "row"):
            area = _a1_area(r.a1)
            return [(sheet, area)] if area else []
        if r.kind == "name":
            return name_areas(r.a1, host, depth)
        if r.kind == "structured":
            loc = lower_tables.get(r.a1.split("[", 1)[0].lower())
            if loc and "!" in loc:
                tsheet, tref = common.parse_ref(loc)
                area = _a1_area(tref)
                return [(tsheet or host, area)] if area else []
        return []

    return ref_areas


def volatile_cells(snapshot: Snapshot) -> tuple[set[tuple[str, str]], str | None]:
    """Formula cells whose value changes on every recalculation: cells that call RAND, RANDBETWEEN, RANDARRAY, NOW,
    TODAY, OFFSET, INDIRECT, CELL or INFO (lib.functions.VOLATILE), and cells that refer to such a cell directly
    or through other formula cells (cell, range, whole-column and whole-row references, other sheets, a defined
    name that names a range, a table named in a structured reference; a defined name whose formula calls a
    volatile function counts as volatile). Returns (cells as (sheet, coord), the first one as 'Sheet!A1' in sheet
    order, then row, then column; None when there is none). References to other workbooks are not followed.
    The propagation goes over a reverse index (one pass over the formulas, then a walk from the volatile cells)."""
    from . import common, formula
    texts = {k: f for k, f in snapshot.formulas.items() if f and f.startswith("=")}
    volatile_names = {n for n, v in snapshot.names.items() if _holds_volatile(str(v))}
    if not volatile_names and not any(_VOLATILE_HINT.search(f) for f in texts.values()):
        return set(), None
    ref_areas = _ref_resolver(snapshot, volatile_names)

    seeds: list[tuple[str, str]] = []
    point: dict[tuple[str, int, int], list[tuple[str, str]]] = {}
    areas: dict[_Area, list[tuple[str, str]]] = {}
    for key, text in texts.items():
        sheet = key[0]
        if _holds_volatile(text):
            seeds.append(key)
            continue
        for r in formula.references(text):
            got = ref_areas(r, sheet)
            if got is None:
                seeds.append(key)
                break
            for s, (c1, r1, c2, r2) in got:
                if c1 == c2 and r1 == r2:
                    point.setdefault((s, r1, c1), []).append(key)
                else:
                    areas.setdefault((s, (c1, r1, c2, r2)), []).append(key)
    # areas indexed by sheet and column; areas wider than 256 columns (whole rows) are kept apart
    by_col: dict[tuple[str, int], list[_Area]] = {}
    wide: dict[str, list[_Area]] = {}
    for akey in areas:
        s, (c1, _r1, c2, _r2) = akey
        if c2 - c1 >= 256:
            wide.setdefault(s, []).append(akey)
        else:
            for c in range(c1, c2 + 1):
                by_col.setdefault((s, c), []).append(akey)
    found: set[tuple[str, str]] = set(seeds)
    done_areas: set[_Area] = set()
    queue = list(seeds)
    while queue:
        sheet, coord = queue.pop()
        try:
            col, row = common.split_cell(coord)
        except ValueError:
            continue
        nxt = list(point.get((sheet, row, col), ()))
        for akey in by_col.get((sheet, col), []) + wide.get(sheet, []):
            if akey in done_areas:
                continue
            _s, (c1, r1, c2, r2) = akey
            if r1 <= row <= r2 and c1 <= col <= c2:
                done_areas.add(akey)
                nxt += areas[akey]
        for d in nxt:
            if d not in found:
                found.add(d)
                queue.append(d)
    if not found:
        return set(), None
    first = min(found, key=_cell_order(snapshot.sheets))
    return found, f"{first[0]}!{first[1]}"


def preservation_statement(stats: dict[str, Any]) -> str:
    """The one sentence the commands print about what compare() compared, from its `stats`. Example:
    'compared 2,734 cells: formulas and typed values exactly, calculated values within a relative tolerance of
    1e-09; 12 cells left out of the value comparison because they hold a volatile function or depend on one
    (first: DA-DATA!B10)'."""
    n = int(stats.get("cells_compared") or 0)
    tol = float(stats.get("calc_tolerance") or 0.0)
    text = f"compared {n:,} cell{'' if n == 1 else 's'}: formulas and typed values exactly"
    if stats.get("calculated_values_compared", True) is False:
        text += "; calculated values were not compared because a workbook holds no cached values"
    elif tol > 0:
        text += f", calculated values within a relative tolerance of {tol:g}"
    else:
        text += ", calculated values exactly"
    left = int(stats.get("values_left_out") or 0)
    if left:
        verb = "it holds a volatile function or depends" if left == 1 else "they hold a volatile function or depend"
        text += (f"; {left:,} cell{'' if left == 1 else 's'} left out of the value comparison because {verb} on one"
                 + (f" (first: {stats['first_left_out']})" if stats.get("first_left_out") else ""))
    return text


# ----------------------------------------------------------------------------- formula cells without a value (v2, A-aera-5)

_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def _sheet_parts(z: zipfile.ZipFile) -> list[tuple[str, str]]:
    """(sheet name, zip part) of every worksheet, in workbook order."""
    import posixpath
    import xml.etree.ElementTree as ET
    wbx = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    targets = {r.get("Id"): r.get("Target", "") for r in rels.iter(f"{{{_NS_PKG_REL}}}Relationship")}
    names = set(z.namelist())
    out = []
    for sh in wbx.iter(f"{{{_NS_MAIN}}}sheet"):
        target = targets.get(sh.get(f"{{{_NS_REL}}}id") or "", "")
        part = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
        if part in names and "/worksheets/" in f"/{part}":
            out.append((sh.get("name") or "", part))
    return out


_DYNAMIC_REFERENCE = frozenset({"OFFSET", "INDIRECT"})   # functions whose referenced cells a formula text does not show
CYCLE_WALK_LIMIT = 200_000                               # formula cells one reference walk visits at most


class _RefGraph:
    """The formula cells each formula refers to (its precedents), read from a snapshot, for walks over references
    (v3, A-aera-15). A formula that calls OFFSET or INDIRECT, or refers to a defined name that calls a volatile
    function, has precedents that its text does not show: precedents() gives None for it."""

    def __init__(self, snapshot: Snapshot) -> None:
        import bisect
        from . import common
        self._bisect = bisect
        self.texts = {k: f for k, f in snapshot.formulas.items() if f and f.startswith("=")}
        volatile_names = {n for n, v in snapshot.names.items() if _holds_volatile(str(v))}
        self._ref_areas = _ref_resolver(snapshot, volatile_names)
        by_col: dict[tuple[str, int], list[tuple[int, tuple[str, str]]]] = {}
        for key in self.texts:
            try:
                col, row = common.split_cell(key[1])
            except ValueError:
                continue
            by_col.setdefault((key[0], col), []).append((row, key))
        self._rows: dict[tuple[str, int], list[int]] = {}
        self._keys: dict[tuple[str, int], list[tuple[str, str]]] = {}
        self._cols: dict[str, list[int]] = {}
        for (sheet, col), items in by_col.items():
            items.sort()
            self._rows[(sheet, col)] = [r for r, _k in items]
            self._keys[(sheet, col)] = [k for _r, k in items]
            self._cols.setdefault(sheet, []).append(col)
        for cols in self._cols.values():
            cols.sort()
        self._prec: dict[tuple[str, str], list[tuple[str, str]] | None] = {}
        self._reach: dict[tuple[str, str], tuple[set[tuple[str, str]], bool]] = {}

    def _cells_in(self, sheet: str, area: tuple[int, int, int, int]) -> list[tuple[str, str]]:
        c1, r1, c2, r2 = area
        bis = self._bisect
        cols = self._cols.get(sheet, [])
        out: list[tuple[str, str]] = []
        for col in cols[bis.bisect_left(cols, c1):bis.bisect_right(cols, c2)]:
            rows = self._rows[(sheet, col)]
            out += self._keys[(sheet, col)][bis.bisect_left(rows, r1):bis.bisect_right(rows, r2)]
        return out

    def precedents(self, key: tuple[str, str]) -> list[tuple[str, str]] | None:
        if key in self._prec:
            return self._prec[key]
        from . import formula
        text = self.texts.get(key)
        out: list[tuple[str, str]] | None = []
        if text:
            a = formula.analyze(text)
            if any(fn in _DYNAMIC_REFERENCE for fn in a.functions):
                out = None
            else:
                for r in a.refs:
                    got = self._ref_areas(r, key[0])
                    if got is None:
                        out = None
                        break
                    for sheet, area in got:
                        out += self._cells_in(sheet, area)
        self._prec[key] = out
        return out

    def reach(self, start: tuple[str, str], limit: int = CYCLE_WALK_LIMIT) -> tuple[set[tuple[str, str]], bool]:
        """(the formula cells `start` refers to directly or through other formula cells, complete): complete is
        False when a precedent list was unknown on the way or the walk stopped at `limit` cells."""
        if start in self._reach:
            return self._reach[start]
        seen: set[tuple[str, str]] = set()
        complete = True
        stack = [start]
        while stack:
            prec = self.precedents(stack.pop())
            if prec is None:
                complete = False
                continue
            for k in prec:
                if k not in seen:
                    if len(seen) >= limit:
                        complete = False
                        stack = []
                        break
                    seen.add(k)
                    stack.append(k)
        self._reach[start] = (seen, complete)
        return seen, complete

    def on_cycle(self, key: tuple[str, str]) -> bool | None:
        """True: the cell refers to itself through other cells; False: its references show it does not; None:
        not known (OFFSET, INDIRECT or a volatile name on the way, or the walk stopped at its limit)."""
        seen, complete = self.reach(key)
        if key in seen:
            return True
        return False if complete else None

    def same_cycle(self, a: tuple[str, str], b: tuple[str, str]) -> bool:
        """True when each of the two cells refers to the other (directly or through other cells). The walk from
        `b` is made first, so many cells `a` tested against one `b` cost one walk each only when `b` reaches them."""
        if a == b:
            return self.on_cycle(a) is True
        return a in self.reach(b)[0] and b in self.reach(a)[0]


def cells_on_reference_cycles(snapshot: Snapshot, cells: Sequence[tuple[str, str]]) -> set[tuple[str, str]]:
    """Of `cells`, the formula cells whose references show that they lie on a cycle of references (a formula that
    refers to itself through other formula cells). A cycle made through OFFSET, INDIRECT or a defined name that
    calls a volatile function is not shown by the references, and a walk stops at CYCLE_WALK_LIMIT cells.
    References to other workbooks are not followed."""
    graph = _RefGraph(snapshot)
    return {c for c in cells if graph.on_cycle(c) is True}


@dataclass
class NoValueCells:
    """What cells_without_value() found in a saved workbook (v3, A-aera-15)."""
    listed: list[tuple[str, str]]          # formula cells without a value: formula_cells_without_value()
    left_out: list[tuple[str, str]]        # empty-text results that hold a volatile function or depend on one and
                                           # whose references show no cycle through them: not listed
    not_circular: list[tuple[str, str]]    # of `named`: cells that hold a volatile function or depend on one and
                                           # either lie on no cycle of references or hold a value in the file
    on_named_cycle: set[tuple[str, str]]   # listed cells on a cycle of references through a cell of `named` that
                                           # is not in not_circular


def cells_without_value(path: str | Path, named: Sequence[Sequence[str]] = ()) -> NoValueCells:
    """Formula cells whose saved sheet XML holds no value, and how they relate to the cells Excel names as
    circular (`named`: (sheet, cell) pairs, from Worksheet.CircularReference). Never writes.

    The XML rule: a formula cell holds no value when it has no <v> element, an empty <v/> without the text type
    t="str", or an empty <v/> with t="str" whose <f> has ca="1". Measured with Excel 16 on this PC (2026-11-25):
    Excel saves a cell of a cycle of references (a circular reference) as <c t="str"><f ca="1">B1+1</f><v/></c>, a
    formula whose result is empty text as <c t="str"><f>""</f><v/></c> (a value), and a volatile formula whose
    result is empty text, such as =IF(TODAY()>1,"","later"), as <c t="str"><f ca="1">...</f><v/></c>, the same
    form as a circular cell. openpyxl's cached value is None in all three cases.

    The volatile rule: a cell of that last form that holds a volatile function or depends on one
    (volatile_cells()) is an empty-text result and goes to `left_out`, not to `listed`, unless its references show
    that it lies on a cycle (cells_on_reference_cycles(): a cycle through a volatile function, such as
    A1 =B1+TODAY()*0 and B1 =A1+1, stays listed). Excel 16 also names a volatile cell, even one with a value such
    as =TODAY(), as the circular reference of its sheet when another sheet holds a cycle (measured 2026-11-25): a
    `named` cell that holds a volatile function or depends on one goes to `not_circular` when its references show
    it lies on no cycle or when the file holds a value for it (a cell of a cycle is saved without one).

    The snapshot is read only when the XML holds a cell of the volatile form or a `named` cell and the workbook
    calls a volatile function, or when `named` and `listed` are both not empty.

    `listed` is [] when no formula cell of the workbook has a value (a workbook written by openpyxl and not yet
    recalculated by Excel)."""
    import xml.etree.ElementTree as ET
    from . import common
    p = common.native_path(path)
    missing: list[tuple[str, str]] = []
    empty_text: list[tuple[str, str]] = []
    any_value = False
    hint = False
    c_tag, f_tag, v_tag = f"{{{_NS_MAIN}}}c", f"{{{_NS_MAIN}}}f", f"{{{_NS_MAIN}}}v"
    with zipfile.ZipFile(p) as z:
        try:
            hint = bool(_VOLATILE_HINT.search(z.read("xl/workbook.xml").decode("utf-8", "replace")))
        except KeyError:
            hint = False
        for sheet, part in _sheet_parts(z):
            with z.open(part) as fh:
                for _event, el in ET.iterparse(fh, events=("end",)):
                    if el.tag != c_tag:
                        continue
                    f = el.find(f_tag)
                    if f is not None:
                        if f.text and not hint and _VOLATILE_HINT.search(f.text):
                            hint = True
                        v = el.find(v_tag)
                        if v is not None and (bool(v.text) or (el.get("t") == "str" and f.get("ca") != "1")):
                            any_value = True
                        else:
                            key = (sheet, el.get("r") or "")
                            missing.append(key)
                            if v is not None and el.get("t") == "str" and f.get("ca") == "1":
                                empty_text.append(key)
                    el.clear()
    named_keys = list(dict.fromkeys((str(c[0]), str(c[1]).replace("$", "")) for c in named if len(c) >= 2))
    left_out: list[tuple[str, str]] = []
    not_circular: list[tuple[str, str]] = []
    on_named: set[tuple[str, str]] = set()
    listed = missing if any_value else []
    need_volatile = hint and bool(empty_text or named_keys)
    if need_volatile or (named_keys and listed):
        try:
            snap = snapshot(p)
            graph = _RefGraph(snap)
            vol = volatile_cells(snap)[0] if need_volatile else set()
            left_out = [c for c in empty_text if c in vol and graph.on_cycle(c) is not True]
            without = set(missing)
            not_circular = [c for c in named_keys
                            if c in vol and (c not in without or graph.on_cycle(c) is False)]
            drop = set(left_out)
            listed = [c for c in listed if c not in drop]
            kept = [c for c in named_keys if c not in set(not_circular)]
            on_named = {c for c in listed if any(graph.same_cycle(c, k) for k in kept)}
        except Exception:  # noqa: BLE001 - an unreadable workbook keeps the XML rule alone (v2 behaviour)
            left_out, not_circular, on_named = [], [], set()
            listed = missing if any_value else []
    return NoValueCells(listed=listed, left_out=left_out, not_circular=not_circular, on_named_cycle=on_named)


def formula_cells_without_value(path: str | Path) -> list[tuple[str, str]]:
    """Formula cells whose saved sheet XML holds no value (the XML rule of cells_without_value()), as (sheet,
    coord) in sheet order, then in the order of the sheet XML. A formula whose result is empty text and that holds
    a volatile function or depends on one (volatile_cells()) is not listed (v3, A-aera-15: Excel 16 saves it in
    the same form as a cell of a circular reference), unless its references show that it lies on a cycle of
    references. Returns [] when no formula cell of the workbook has a value (a workbook written by openpyxl and
    not yet recalculated by Excel). Never writes."""
    return cells_without_value(path).listed

"""The sidecar manifest `<workbook stem>.manifest.json` and the raw-sheet digest (design sections 6.8, 7, D11).

Owner: CORE-IO. Standard library only at module level; openpyxl is imported inside sheet_rows().

The digest is the same function for a CSV snapshot (raw/<table>.csv) and for a workbook raw sheet, so the audit
(rule R-CHANGED) and `check` can tell whether a raw sheet still holds the landed rows. Values are normalised
first (design 6.8): text as is; integer str(int); number format(float, '.15g'); date YYYY-MM-DD; datetime ISO to
the second, a fraction of a second cut and never rounded up (cut_to_second, A-aera-6); boolean TRUE/FALSE; null
(None or empty text) ''. Cells are joined by \\x1f, rows by \\x1e, and
trailing empty rows are not part of the digest (a CSV ends with a line break, a sheet may hold formatted empty
rows below the table).
"""
from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import math
import os
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

MANIFEST_SUFFIX = ".manifest.json"
MANIFEST_VERSION = 1
CELL_SEP = "\x1f"
ROW_SEP = "\x1e"
EXCEL_EPOCH = _dt.datetime(1899, 12, 30)       # serial 0 in the 1900 date system as Excel counts it


def manifest_path(workbook_path: str | Path) -> Path:
    """model.xlsx -> model.manifest.json in the same folder."""
    p = Path(workbook_path)
    return p.with_name(p.stem + MANIFEST_SUFFIX)


def load_manifest(workbook_path: str | Path) -> dict[str, Any] | None:
    """The validated manifest, or None when the sidecar file does not exist.
    Raises common.Refusal when the file exists but is not valid JSON or fails the schema."""
    from . import common, schemas
    path = manifest_path(common.native_path(workbook_path))
    if not path.is_file():
        return None
    data = common.load_json_file(path, "manifest")
    schemas.require_valid(data, "manifest", path.name)
    return data


def write_manifest(workbook_path: str | Path, manifest: dict[str, Any]) -> Path:
    """Validate (schema "manifest") and write atomically; returns the path."""
    from . import common, schemas
    path = manifest_path(common.native_path(workbook_path))
    schemas.require_valid(manifest, "manifest", path.name)
    return common.write_json(path, manifest)


# ----------------------------------------------------------------------------- normalisation and digest

def _serial_to_datetime(x: float) -> _dt.datetime:
    return EXCEL_EPOCH + _dt.timedelta(days=float(x))


def cut_to_second(d: _dt.datetime, *, sheet: bool = False) -> _dt.datetime:
    """The one rule for a datetime in the snapshot and in both digests (A-aera-6): a fraction of a second is cut,
    never rounded up, so no time moves into the next second, day or period. A value read from a sheet (`sheet`:
    an Excel serial, or the datetime openpyxl makes of one) is first rounded to the nearest millisecond, which
    removes the float error of the serial, so a time landed as 23:59:59 reads back as 23:59:59; then the fraction
    is cut. A value parsed from text (land, a CSV) is cut as written. The time zone, if any, is dropped."""
    d = d.replace(tzinfo=None)
    if sheet and d.microsecond >= 999_500:          # 999.5 ms or more rounds to the next whole second
        d = d + _dt.timedelta(microseconds=1_000_000 - d.microsecond)
    return d.replace(microsecond=0)


def _parse_datetime_text(text: str) -> _dt.datetime | None:
    t = text.strip()
    if not t:
        return None
    try:
        return _dt.datetime.fromisoformat(t.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _number_text(x: float) -> str:
    if x == 0:
        x = 0.0                 # -0.0 and 0.0 digest the same
    if math.isnan(x) or math.isinf(x):
        return str(x)
    return format(float(x), ".15g")


def normalise_value(value: Any, col_type: str) -> str:
    """Design 6.8: text as is; integer str(int); number format(float, '.15g'); date YYYY-MM-DD;
    datetime ISO to the second, the fraction cut (cut_to_second: a datetime object or an Excel serial is read as
    a sheet value, a text as written); boolean TRUE/FALSE; None -> ''. A value that does not fit its type is
    written as text, so it changes the digest instead of raising."""
    if value is None:
        return ""
    if isinstance(value, str) and value == "":
        return ""
    if col_type == "integer":
        if isinstance(value, bool):
            return str(int(value))
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            return str(int(value)) if value.is_integer() else _number_text(value)
        if isinstance(value, str):
            t = value.strip()
            try:
                return str(int(t))
            except ValueError:
                try:
                    f = float(t)
                    return str(int(f)) if f.is_integer() else _number_text(f)
                except ValueError:
                    return value
        return str(value)
    if col_type == "number":
        if isinstance(value, bool):
            return _number_text(float(value))
        if isinstance(value, (int, float)):
            return _number_text(float(value))
        if isinstance(value, str):
            try:
                return _number_text(float(value.strip()))
            except ValueError:
                return value
        return str(value)
    if col_type == "date":
        if isinstance(value, _dt.datetime):
            return value.date().isoformat()
        if isinstance(value, _dt.date):
            return value.isoformat()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return _serial_to_datetime(value).date().isoformat()
        if isinstance(value, str):
            d = _parse_datetime_text(value)
            return d.date().isoformat() if d else value
        return str(value)
    if col_type == "datetime":
        if isinstance(value, _dt.datetime):
            return cut_to_second(value, sheet=True).isoformat()
        if isinstance(value, _dt.date):
            return _dt.datetime(value.year, value.month, value.day).isoformat()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return cut_to_second(_serial_to_datetime(value), sheet=True).isoformat()
        if isinstance(value, str):
            d = _parse_datetime_text(value)
            return cut_to_second(d).isoformat() if d else value
        return str(value)
    if col_type == "boolean":
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, (int, float)):
            return "TRUE" if value else "FALSE"
        if isinstance(value, str):
            t = value.strip().upper()
            if t in ("TRUE", "1", "YES", "Y"):
                return "TRUE"
            if t in ("FALSE", "0", "NO", "N"):
                return "FALSE"
        return str(value)
    # text (and any unknown type)
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else _number_text(value)
    if isinstance(value, _dt.datetime):
        return value.isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    return str(value)


def raw_digest(header: Sequence[str], rows: Iterable[Sequence[Any]], types: Sequence[str]) -> str:
    """'sha256:<hex>' over the header and normalised rows (cells joined by CELL_SEP, rows by ROW_SEP).
    The same function digests a CSV snapshot and a workbook raw sheet. Rows are cut or padded to the header's
    width only when they are longer with empty cells; trailing empty rows are left out."""
    width = len(header)
    col_types = list(types) + ["text"] * max(0, width - len(types))
    h = hashlib.sha256()
    h.update(CELL_SEP.join("" if c is None else str(c) for c in header).encode("utf-8"))
    pending_empty = 0
    for row in rows:
        cells = list(row)
        while len(cells) > width and normalise_value(cells[-1], "text") == "":
            cells.pop()
        cells += [None] * (width - len(cells))
        norm = [normalise_value(v, col_types[i] if i < width else "text") for i, v in enumerate(cells)]
        if all(x == "" for x in norm):
            pending_empty += 1
            continue
        for _ in range(pending_empty):
            h.update((ROW_SEP + CELL_SEP.join([""] * width)).encode("utf-8"))
        pending_empty = 0
        h.update((ROW_SEP + CELL_SEP.join(norm)).encode("utf-8"))
    return "sha256:" + h.hexdigest()


def read_csv(path: str | Path) -> tuple[list[str], Iterator[list[str]]]:
    """(header, rows) of a raw snapshot CSV (UTF-8 with or without BOM, comma, header row; design 4).
    The rows iterator holds the file open until it is used up."""
    p = Path(path)

    def gen() -> Iterator[list[str]]:
        with open(p, encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                yield row

    with open(p, encoding="utf-8-sig", newline="") as f:
        header = next(csv.reader(f), [])
    return header, gen()


def csv_digest(path: str | Path, types: Sequence[str]) -> tuple[str, int, list[str]]:
    """(digest, data row count, header) of a raw snapshot CSV."""
    header, rows = read_csv(path)
    counted: list[int] = [0]

    def counting() -> Iterator[list[str]]:
        for r in rows:
            if any(c != "" for c in r):
                counted[0] += 1
            yield r

    return raw_digest(header, counting(), types), counted[0], header


def sheet_rows(workbook_path: str | Path, sheet: str) -> tuple[list[Any], list[list[Any]]]:
    """(header, data rows) of a raw sheet as stored (formulas as text, data_only=False), read only.
    The header is row 1 up to its first empty cell; data rows are cut to the header width."""
    import warnings

    import openpyxl
    from . import common
    p = common.native_path(workbook_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(p, read_only=True, data_only=False)
    try:
        if sheet not in wb.sheetnames:
            raise common.Refusal(f"{p.name} has no sheet '{sheet}'", fix="check the raw sheet name (raw_<table>)")
        ws = wb[sheet]
        ws.reset_dimensions()
        it = ws.iter_rows(values_only=True)
        first = next(it, ())
        header: list[Any] = []
        for v in first:
            if v is None or (isinstance(v, str) and v == ""):
                break
            header.append(v)
        width = len(header)
        rows = []
        for r in it:
            vals = list(r[:width])
            vals += [None] * (width - len(vals))
            rows.append([_cell_text(v) for v in vals])
        while rows and all(v is None or v == "" for v in rows[-1]):
            rows.pop()
        return header, rows
    finally:
        wb.close()


def _cell_text(v: Any) -> Any:
    """ArrayFormula and DataTableFormula objects as their formula text; other values unchanged."""
    text = getattr(v, "text", None)
    if text is not None and not isinstance(v, str):
        return str(text)
    if v is not None and type(v).__name__ == "DataTableFormula":
        return "=TABLE()"
    return v


def sheet_digest(workbook_path: str | Path, sheet: str, types: Sequence[str]) -> tuple[str, int, list[Any]]:
    """(digest, data row count, header) of a workbook raw sheet, with the same normalisation as csv_digest."""
    header, rows = sheet_rows(workbook_path, sheet)
    count = sum(1 for r in rows if any(normalise_value(v, "text") != "" for v in r))
    return raw_digest([str(h) for h in header], rows, types), count, [str(h) for h in header]


# ----------------------------------------------------------------------------- lookups

def cell_type_ranges(manifest: dict[str, Any], sheet: str) -> list[tuple[int, int, int, int, str]]:
    """manifest["cell_types"][sheet] as (min_col, min_row, max_col, max_row, cell type); later entries win."""
    from . import common
    out = []
    for rng, ctype in manifest.get("cell_types", {}).get(sheet, []):
        c1, r1, c2, r2 = common.range_bounds(rng)
        out.append((c1, r1, c2, r2, ctype))
    return out


def cell_type_at(ranges: Sequence[tuple[int, int, int, int, str]], row: int, col: int) -> str | None:
    """The cell type of (row, col) in the output of cell_type_ranges (the last matching range wins)."""
    found = None
    for c1, r1, c2, r2, ctype in ranges:
        if r1 <= row <= r2 and c1 <= col <= c2:
            found = ctype
    return found


def cell_types(manifest: dict[str, Any], sheet: str | None = None) -> dict[tuple[str, str], str]:
    """Expand manifest["cell_types"] ranges into (sheet, coord) -> cell type (later ranges win).
    For large sheets prefer cell_type_ranges + cell_type_at."""
    from . import common
    out: dict[tuple[str, str], str] = {}
    sheets = [sheet] if sheet is not None else list(manifest.get("cell_types", {}))
    for s in sheets:
        for c1, r1, c2, r2, ctype in cell_type_ranges(manifest, s):
            for r in range(r1, r2 + 1):
                for c in range(c1, c2 + 1):
                    out[(s, f"{common.col_letter(c)}{r}")] = ctype
    return out


def sheet_entry(manifest: dict[str, Any], name: str) -> dict[str, Any] | None:
    """The manifest entry of the sheet called `name`."""
    for s in manifest.get("sheets", []):
        if s.get("name") == name:
            return s
    return None


def raw_sheet(manifest: dict[str, Any], table: str) -> dict[str, Any] | None:
    """The manifest entry of the raw sheet of `table`."""
    for s in manifest.get("sheets", []):
        if s.get("type") == "raw" and s.get("table") == table:
            return s
    return None


def helper_sheet(manifest: dict[str, Any], table: str) -> dict[str, Any] | None:
    """The manifest entry of the helper sheet of `table`."""
    for s in manifest.get("sheets", []):
        if s.get("type") == "helpers" and s.get("table") == table:
            return s
    return None


def raw_tables(manifest: dict[str, Any]) -> list[str]:
    """The tables that have a raw sheet, in manifest order."""
    return [s["table"] for s in manifest.get("sheets", []) if s.get("type") == "raw"]


def raw_types(manifest: dict[str, Any], table: str, model_root: str | Path | None = None) -> list[str] | None:
    """The column types of a raw table: the manifest's "types" when written, else the contract's, else None."""
    entry = raw_sheet(manifest, table)
    if entry and entry.get("types"):
        return list(entry["types"])
    if model_root is not None:
        from . import modelfolder
        state, data, _ = modelfolder.read_contract(model_root, table)
        if data and state != "invalid":
            return [c["type"] for c in data["columns"]]
    return None


def update_raw(workbook_path: str | Path, table: str, *, digest: str, rows: int,
               snapshot: str | None = None) -> None:
    """Record a new raw digest and row count (after land or an accepted refresh)."""
    from . import common
    wb = common.native_path(workbook_path)
    data = load_manifest(wb)
    if data is None:
        raise common.Refusal(f"{wb.name} has no manifest ({manifest_path(wb).name})",
                             fix="build the workbook with rotm.py build first")
    entry = raw_sheet(data, table)
    if entry is None:
        raise common.Refusal(f"{manifest_path(wb).name} has no raw sheet for table '{table}'",
                             fix="add the raw sheet to the specification and rebuild")
    entry["digest"] = digest
    entry["rows"] = int(rows)
    if snapshot is not None:
        entry["snapshot"] = snapshot
    data["updated_at"] = common.now_iso()
    write_manifest(wb, data)


def exists(workbook_path: str | Path) -> bool:
    return manifest_path(workbook_path).is_file() and os.path.getsize(manifest_path(workbook_path)) > 0

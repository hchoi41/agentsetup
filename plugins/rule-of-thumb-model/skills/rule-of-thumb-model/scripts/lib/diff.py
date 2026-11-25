"""`diff`: compare two versions of a workbook: formulas, inputs and outputs that changed (design section 15; F21).

Owner: M8. Reads both workbooks with lib.workbook (never writes them).

What is compared, per sheet that both versions have:
- formulas: the formula text after the _xlfn. prefixes are removed and outside text put in upper case
  (workbook.normalise_formula); a formula added, removed or changed;
- constants outside raw sheets (inputs and labels): a value added, removed or changed, compared exactly (v2,
  A-aera-4: 1,000,000,000,000 and 999,999,999,500 differ); a cell the manifest marks as an input is named by its
  input id;
- outputs: the values Excel saved for formula cells, when both workbooks hold such values (a workbook written by
  openpyxl and not recalculated holds none; the report then says outputs were not compared); compared within a
  relative tolerance, --calc-tolerance (default workbook.DEFAULT_CALC_TOLERANCE, 1e-9; 0 = exact), which the
  report prints;
- a cell whose formula was replaced by a typed value, or the reverse (v3, A-aera-16): the formula line shows both
  sides, the formula with the value Excel saved for it and the typed value; when outputs are compared and the
  two values differ, the cell is also listed among the changed outputs;
- raw sheets (the manifest's, else inferred by name or query table): row count, digest (manifest.raw_digest)
  and the rows that changed, not cell by cell;
- the stored type of a cell (v6, round 5 verdict A-aera-5): a cell whose stored type changed (number, text,
  logical value; stored_type) while its text reads the same is listed as changed, with both types shown
  (`number 858800 -> text '858800'`, `text 'TRUE' -> logical value TRUE`): on raw sheets the row counts as
  changed and the D-RAW line counts such cells and names the first (the digest reads each value by its column
  type, so it can stay equal); on every other sheet the D-INPUT and D-OUTPUT lines show both types;
- structure: sheets added or removed, tables, defined names, charts, queries, pivot tables, conditional formats
  and data validations.
Changes of one kind in one column of consecutive rows are one group (a formula copied down 500 rows and changed
in all of them is one line: its relative form changed the same way in every row).
Every difference is an info finding, so the exit code is 0 unless --fail-on-change is given (then 1 when
anything differs). The Markdown file (--md) lists every changed cell, up to 2,000 per category and sheet.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MD_CELL_LIMIT = 2000
RAW_NAME_RE = re.compile(r"^(raw|data|src|landed)[_ -]?|_raw$", re.IGNORECASE)    # design 14, inferred raw sheets


@dataclass
class Change:
    sheet: str
    coord: str
    row: int
    col: int
    kind: str            # changed | added | removed
    old: Any
    new: Any
    name: str = ""       # input id, key output id or check id when the manifest knows the cell
    key: Any = None      # grouping key
    # v3 (A-aera-16), a formula added or removed: the value of each side (the formula's saved value, the typed
    # value of the other side) and whether each workbook holds saved values
    old_value: Any = None
    new_value: Any = None
    old_saved: bool = False
    new_saved: bool = False
    # v6 (A-aera-5), a constant or a saved value: the stored type of each side (cell_type)
    old_type: str = ""
    new_type: str = ""


@dataclass
class Group:
    sheet: str
    category: str        # formula | constant | output
    kind: str
    cells: list[Change] = field(default_factory=list)

    @property
    def ref(self) -> str:
        from . import common
        first, last = self.cells[0], self.cells[-1]
        if len(self.cells) == 1:
            return first.coord
        return f"{first.coord}:{common.col_letter(last.col)}{last.row}"


def _resolve_workbook(path: str | Path) -> Path:
    """A workbook path, or the workbook of a model folder (its marker names it)."""
    from . import common, modelfolder
    p = common.native_path(path)
    if p.is_dir():
        if modelfolder.is_model_root(p):
            marker = modelfolder.read_marker(p)
            return p / marker.get("workbook", "model.xlsx")
        raise common.Refusal(f"{p.as_posix()} is a folder without rotm_model.json",
                             fix="give the workbook file, or a model folder")
    return p


def _raw_sheets(info: Any) -> dict[str, list[str] | None]:
    """Raw sheet name -> column types (None when unknown) from the manifest, else inferred."""
    out: dict[str, list[str] | None] = {}
    man = info.manifest
    if man:
        for s in man.get("sheets", []):
            if s.get("type") == "raw":
                out[s["name"]] = list(s.get("types") or []) or None
        if out:
            return out
    for s in info.sheets:
        if s.kind != "worksheet":
            continue
        if RAW_NAME_RE.search(s.name) or any(t.from_query for t in s.tables):
            out[s.name] = None
    return out


def _names(info: Any) -> dict[tuple[str, str], str]:
    """(sheet, coord) -> 'input <id>' / 'key output <id>' / 'check <id>' / 'master check' from the manifest."""
    from . import common
    out: dict[tuple[str, str], str] = {}
    man = info.manifest or {}

    def put(ref: str, label: str) -> None:
        sheet, cell = common.parse_ref(ref)
        if sheet is None:
            return
        try:
            for r, c in common.iter_range(cell):
                out.setdefault((sheet, common.a1(c, r)), label)
        except ValueError:
            pass

    for iid, ref in (man.get("inputs") or {}).items():
        put(ref, f"input {iid}")
    for oid, ref in (man.get("cells") or {}).items():
        put(ref, f"key output {oid}")
    for chk in man.get("checks") or []:
        if chk.get("result"):
            put(chk["result"], f"check {chk['id']}")
    if man.get("master_check"):
        put(man["master_check"], "master check")
    return out


def _raw_summary(sheet: Any, types: list[str] | None) -> dict[str, Any]:
    from . import manifest as manmod
    cells = sheet.cells
    header: list[str] = []
    c = 1
    while (1, c) in cells and cells[(1, c)].value not in (None, ""):
        header.append(str(cells[(1, c)].value))
        c += 1
    width = max(len(header), 1)
    last = max((r for (r, _c) in cells), default=1)
    rows, kinds = [], []
    for r in range(2, last + 1):
        rows.append([cells[(r, k)].value if (r, k) in cells else None for k in range(1, width + 1)])
        kinds.append([cell_type(cells.get((r, k))) for k in range(1, width + 1)])
    while rows and all(v in (None, "") for v in rows[-1]):
        rows.pop()
    del kinds[len(rows):]
    col_types = (types or [])[:width] + ["text"] * max(0, width - len(types or []))
    norm = [[manmod.normalise_value(v, col_types[i]) for i, v in enumerate(r)] for r in rows]
    return {"header": header, "rows": len(rows), "norm": norm, "values": rows, "kinds": kinds,
            "digest": manmod.raw_digest(header, rows, col_types),
            "formulas": sum(1 for ci in cells.values() if ci.is_formula)}


# The stored types whose change diff lists with both types (v6, round 5 verdict A-aera-5). An error value is a type
# of its own and keeps the v5 lines.
STORED_TYPES = ("number", "text", "logical value")


def stored_type(v: Any) -> str:
    """The type a workbook stores a value as: 'logical value', 'number' (dates and times too: Excel stores them as
    numbers), 'text', or '' for an empty cell (v6, round 5 verdict A-aera-5)."""
    if v is None or (isinstance(v, str) and v == ""):
        return ""
    if isinstance(v, bool):
        return "logical value"
    if isinstance(v, (int, float, _dt.date, _dt.datetime, _dt.time)):
        return "number"
    return "text"


def cell_type(cell: Any, *, saved: bool = False) -> str:
    """stored_type of a cell's typed value (`saved`: of the value Excel saved for its formula); 'error value' for
    an error value; '' for an absent or empty cell."""
    if cell is None:
        return ""
    value = cell.cached if saved else cell.value
    if stored_type(value) and (cell.cached_type if saved else cell.data_type) == "e":
        return "error value"
    return stored_type(value)


def type_changed(ta: str, tb: str) -> bool:
    """True when both sides hold a number, text or logical value (STORED_TYPES) and the types differ."""
    return ta in STORED_TYPES and tb in STORED_TYPES and ta != tb


def typed_text(v: Any, t: str | None = None) -> str:
    """A value with its stored type (`t`, else stored_type(v)), as the report shows a cell whose stored type
    changed: number 858800, text '858800', logical value TRUE."""
    t = stored_type(v) if t is None else t
    if not t:
        return "(empty)"
    if t == "logical value":
        return f"logical value {'TRUE' if v else 'FALSE'}"
    if t == "text":
        return f"text '{_short(v)}'"
    return f"{t} {_short(v)}"


def change_sides(ch: "Change", *, code: bool = False, empty: str = "(empty)") -> tuple[str, str]:
    """The two sides of a changed constant or saved value: with both stored types when they differ (v6, A-aera-5),
    else as in v5 (`empty` for an empty side). `code`: for the Markdown table (pipes escaped)."""
    if type_changed(ch.old_type, ch.new_type):
        a, b = typed_text(ch.old, ch.old_type), typed_text(ch.new, ch.new_type)
        return (_md(a), _md(b)) if code else (a, b)
    if code:
        return _md(ch.old), _md(ch.new)
    return _short(ch.old) or empty, _short(ch.new) or empty


def _short(v: Any, limit: int = 60) -> str:
    if v is None:
        return ""
    text = str(v)
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _value_text(v: Any) -> str:
    return "(empty)" if v in (None, "") else _short(v)


def side_texts(ch: Change, *, code: bool = False) -> tuple[str, str]:
    """The old and new side of a formula change, as the report and the Markdown file show them. A formula
    replaced by a typed value, or the reverse (v3, A-aera-16): the formula with the value Excel saved for it
    ("no saved value" when that workbook holds none) and the typed value on the other side. `code`: formulas in
    backticks (Markdown)."""
    def formula(f: Any, value: Any, saved: bool) -> str:
        text = f"`{_md(f)}`" if code else _short(f)
        return f"{text} ({'saved value ' + _value_text(value) if saved else 'no saved value'})"

    def typed(value: Any) -> str:
        return "(empty)" if value in (None, "") else f"typed value {_md(value) if code else _short(value)}"

    if ch.kind == "removed":
        return formula(ch.old, ch.old_value, ch.old_saved), typed(ch.new_value)
    if ch.kind == "added":
        return typed(ch.old_value), formula(ch.new, ch.new_value, ch.new_saved)
    if code:
        return f"`{_md(ch.old)}`", f"`{_md(ch.new)}`"
    return _short(ch.old) or "(none)", _short(ch.new) or "(none)"


def _group(changes: list[Change], category: str) -> list[Group]:
    changes.sort(key=lambda ch: (ch.sheet, ch.col, ch.row))
    groups: list[Group] = []
    for ch in changes:
        g = groups[-1] if groups else None
        if (g is not None and g.sheet == ch.sheet and g.kind == ch.kind and g.cells[-1].col == ch.col
                and g.cells[-1].row + 1 == ch.row and g.cells[-1].key == ch.key and not ch.name
                and not g.cells[-1].name):
            g.cells.append(ch)
        else:
            groups.append(Group(ch.sheet, category, ch.kind, [ch]))
    return groups


def _norm_formula(f: str | None) -> str | None:
    """Formula text for comparison: formula.strip_xlfn (also _xlpm., spill and @ forms), then upper case outside
    text (workbook.normalise_formula)."""
    from . import formula, workbook
    if f is None:
        return None
    try:
        f = formula.strip_xlfn(f)
    except Exception:  # noqa: BLE001 - text strip_xlfn cannot read is compared as it is
        pass
    return workbook.normalise_formula(f)


def _formula_key(old: str | None, new: str | None, coord: str) -> Any:
    from . import formula
    try:
        return (formula.relative_form(old, coord) if old else None, formula.relative_form(new, coord) if new else None)
    except Exception:  # noqa: BLE001 - a formula relative_form cannot read is grouped by its text
        return (old, new)


def tolerance_words(tolerance: float, *, subject: bool = True) -> str:
    """How outputs were compared, for the report (with the subject) and the Markdown file (without it)."""
    how = ("exactly (--calc-tolerance 0)" if tolerance == 0 else
           f"within a relative tolerance of {tolerance:g} (--calc-tolerance)")
    return f"outputs (calculated values) {how}" if subject else how


def diff_workbooks(old: str | Path, new: str | Path, *, calc_tolerance: float | None = None) -> Any:
    """Returns a common.Report; data: sheets added and removed, and per sheet the formula, input and output
    changes; raw sheets summarised by row count and digest. Constants are compared exactly; outputs within
    `calc_tolerance` (relative; default workbook.DEFAULT_CALC_TOLERANCE; 0 = exact)."""
    from . import common, compat, workbook
    tolerance = compat.check_calc_tolerance(calc_tolerance)
    old_p, new_p = _resolve_workbook(old), _resolve_workbook(new)
    a = workbook.open_workbook(old_p, with_styles=False)
    b = workbook.open_workbook(new_p, with_styles=False)
    report = common.Report("diff", f"{common.norm_path(old_p)} -> {common.norm_path(new_p)}")
    for w in a.warnings:
        report.note(f"old: {w}")
    for w in b.warnings:
        report.note(f"new: {w}")
    raw = {**_raw_sheets(a), **_raw_sheets(b)}
    names = {**_names(a), **_names(b)}
    compare_outputs = a.has_cached_values and b.has_cached_values
    if not compare_outputs:
        missing = [p.name for p, info in ((old_p, a), (new_p, b)) if not info.has_cached_values]
        report.note(f"outputs not compared: {', '.join(missing)} holds no values saved by Excel (not recalculated); "
                    "run rotm.py check or rotm.py recalc on it first")
    report.note("compared: formulas and constants (typed values) exactly"
                + (f"; {tolerance_words(tolerance)}" if compare_outputs else ""))
    report.verified["recalculated"] = compare_outputs
    sheets_a, sheets_b = a.sheet_names, b.sheet_names
    added = [s for s in sheets_b if s not in sheets_a]
    removed = [s for s in sheets_a if s not in sheets_b]
    for s in added:
        report.info("D-SHEET", f"sheet '{s}' was added", sheet=s)
    for s in removed:
        report.info("D-SHEET", f"sheet '{s}' was removed", sheet=s)
    per_sheet: dict[str, dict[str, Any]] = {}
    for name in sheets_b:
        if name not in sheets_a:
            continue
        sa, sb = a.sheet(name), b.sheet(name)
        entry: dict[str, Any] = {"formulas": [], "constants": [], "outputs": [], "raw": None}
        per_sheet[name] = entry
        if sa.kind != "worksheet" or sb.kind != "worksheet":
            continue
        if name in raw:
            entry["raw"] = _raw_entry(name, sa, sb, raw[name], report)
            continue
        f_changes: list[Change] = []
        c_changes: list[Change] = []
        o_changes: list[Change] = []
        for key in sorted(set(sa.cells) | set(sb.cells)):
            ca, cb = sa.cells.get(key), sb.cells.get(key)
            r, c = key
            coord = common.a1(c, r)
            label = names.get((name, coord), "")
            fa = ca.formula if ca is not None else None
            fb = cb.formula if cb is not None else None
            if fa is not None or fb is not None:
                na, nb = _norm_formula(fa), _norm_formula(fb)
                if na != nb:
                    kind = "changed" if fa and fb else "added" if fb else "removed"
                    ch = Change(name, coord, r, c, kind, fa, fb, label, _formula_key(fa, fb, coord))
                    if kind != "changed":
                        # v3 (A-aera-16): the formula's saved value and the typed value of the other side
                        ch.old_value = _cell_value(ca)
                        ch.new_value = _cell_value(cb)
                        ch.old_saved, ch.new_saved = bool(a.has_cached_values), bool(b.has_cached_values)
                    f_changes.append(ch)
                if compare_outputs and fa is not None and fb is not None:
                    va, vb = ca.cached, cb.cached
                    if not workbook.values_equal(va, vb, tolerance=tolerance):
                        o_changes.append(_typed_change(name, coord, r, c, va, vb, label,
                                                       cell_type(ca, saved=True), cell_type(cb, saved=True)))
                elif compare_outputs and _typed_other_side(ca, cb):
                    # v3 (A-aera-16): a formula replaced by a typed value, or the reverse, changes the cell's
                    # output when the two values differ
                    va, vb = _cell_value(ca), _cell_value(cb)
                    if not workbook.values_equal(va, vb, tolerance=tolerance):
                        o_changes.append(_typed_change(name, coord, r, c, va, vb, label,
                                                       cell_type(ca, saved=ca.formula is not None),
                                                       cell_type(cb, saved=cb.formula is not None)))
                continue
            if (ca is not None and ca.formula_kind in ("array_member", "data_table_member")) or \
                    (cb is not None and cb.formula_kind in ("array_member", "data_table_member")):
                continue
            va = ca.value if ca is not None else None
            vb = cb.value if cb is not None else None
            # typed values: exactly (A-aera-4); a number and text of the same digits differ (values_equal), and
            # the line shows both stored types (v6, A-aera-5)
            if not workbook.values_equal(va, vb, tolerance=0.0):
                kind = "changed" if va not in (None, "") and vb not in (None, "") else \
                    "added" if va in (None, "") else "removed"
                ch = _typed_change(name, coord, r, c, va, vb, label, cell_type(ca), cell_type(cb))
                ch.kind = kind
                c_changes.append(ch)
        entry["formulas"] = _group(f_changes, "formula")
        entry["constants"] = _group(c_changes, "constant")
        entry["outputs"] = _group(o_changes, "output")
        for g in entry["formulas"]:
            first = g.cells[0]
            n = len(g.cells)
            old_text, new_text = side_texts(first)
            report.info("D-FORMULA", f"{g.ref}{f' ({n} cells)' if n > 1 else ''}: formula {g.kind}: "
                        f"{old_text} -> {new_text}"
                        + (f" [{first.name}]" if first.name else ""), sheet=name, cell=first.coord, count=n,
                        cells=[ch.coord for ch in g.cells[:20]])
        for g in entry["constants"]:
            first = g.cells[0]
            n = len(g.cells)
            what = first.name or "constant"
            old_text, new_text = change_sides(first)
            report.info("D-INPUT", f"{g.ref}{f' ({n} cells)' if n > 1 else ''}: {what} {g.kind}: "
                        f"{old_text} -> {new_text}", sheet=name,
                        cell=first.coord, count=n, cells=[ch.coord for ch in g.cells[:20]])
        for g in entry["outputs"]:
            first = g.cells[0]
            n = len(g.cells)
            old_text, new_text = change_sides(first, empty="")
            report.info("D-OUTPUT", f"{g.ref}{f' ({n} cells)' if n > 1 else ''}: value changed: "
                        f"{old_text} -> {new_text}" + (f" [{first.name}]" if first.name else ""),
                        sheet=name, cell=first.coord, count=n, cells=[ch.coord for ch in g.cells[:20]])
    structure = _structure(a, b)
    for line in structure:
        report.info("D-STRUCTURE", line)
    counts = {"sheets_added": len(added), "sheets_removed": len(removed),
              "formulas": sum(len(g.cells) for e in per_sheet.values() for g in e["formulas"]),
              "constants": sum(len(g.cells) for e in per_sheet.values() for g in e["constants"]),
              "outputs": sum(len(g.cells) for e in per_sheet.values() for g in e["outputs"]),
              "raw_sheets_changed": sum(1 for e in per_sheet.values() if e["raw"] and e["raw"]["changed"]),
              "structure": len(structure)}
    report.data = {"old": common.norm_path(old_p), "new": common.norm_path(new_p), "old_sha256": a.sha256,
                   "new_sha256": b.sha256, "sheets_added": added, "sheets_removed": removed,
                   "outputs_compared": compare_outputs, "calc_tolerance": tolerance, "counts": counts,
                   "structure": structure,
                   "sheets": {s: _entry_dict(e) for s, e in per_sheet.items()}}
    report.groups = per_sheet                   # the changed cells, for write_markdown (not part of the JSON)
    total = sum(counts.values())
    report.note("no differences" if total == 0 else
                f"{counts['formulas']} formula cells, {counts['constants']} constant cells and {counts['outputs']} "
                f"output cells differ; {counts['raw_sheets_changed']} raw sheets changed; {len(added)} sheets "
                f"added, {len(removed)} removed")
    report.finish()
    return report


def _typed_change(sheet: str, coord: str, row: int, col: int, old: Any, new: Any, label: str, old_type: str,
                  new_type: str) -> Change:
    """A changed constant or saved value with the stored type of each side; changes of the stored type are grouped
    apart from other changes (v6, A-aera-5)."""
    key = ("type", old_type, new_type) if type_changed(old_type, new_type) else None
    return Change(sheet, coord, row, col, "changed", old, new, label, key, old_type=old_type, new_type=new_type)


def _cell_value(cell: Any) -> Any:
    """What a cell shows: the saved value of a formula cell, the typed value of another cell, None when absent."""
    if cell is None:
        return None
    return cell.cached if cell.formula is not None else cell.value


def _typed_other_side(ca: Any, cb: Any) -> bool:
    """True when one side holds a formula and the other a typed value (not empty, not part of an array or a data
    table)."""
    for formula_side, other in ((ca, cb), (cb, ca)):
        if formula_side is not None and formula_side.formula is not None and other is not None \
                and other.formula is None and other.formula_kind not in ("array_member", "data_table_member") \
                and other.value not in (None, ""):
            return True
    return False


def _raw_type_changes(ra: dict[str, Any], rb: dict[str, Any]) -> list[tuple[int, int, str, str]]:
    """(sheet row, column, old side, new side) of each raw cell present in both versions whose stored type changed
    (number, text, logical value), in row order (v6, round 5 verdict A-aera-5)."""
    out = []
    for k in range(min(ra["rows"], rb["rows"])):
        ka, kb = ra["kinds"][k], rb["kinds"][k]
        for j in range(min(len(ka), len(kb))):
            if type_changed(ka[j], kb[j]):
                out.append((k + 2, j + 1, typed_text(ra["values"][k][j], ka[j]), typed_text(rb["values"][k][j], kb[j])))
    return out


def _raw_entry(name: str, sa: Any, sb: Any, types: list[str] | None, report: Any) -> dict[str, Any]:
    from . import common
    ra, rb = _raw_summary(sa, types), _raw_summary(sb, types)
    retyped = _raw_type_changes(ra, rb)
    retyped_rows = {row for row, _c, _a, _b in retyped}
    # a row counts as changed when a value or the stored type of a cell changed (v6, A-aera-5): the normalised
    # values read 858800 and '858800' in a number column the same
    changed_rows = [k + 2 for k in range(min(ra["rows"], rb["rows"]))
                    if ra["norm"][k] != rb["norm"][k] or k + 2 in retyped_rows]
    digest_changed = ra["digest"] != rb["digest"]
    changed = digest_changed or ra["header"] != rb["header"] or bool(retyped)
    first = None
    if retyped:
        row, col, old_text, new_text = retyped[0]
        first = {"cell": common.a1(col, row), "old": old_text, "new": new_text}
    entry = {"rows_old": ra["rows"], "rows_new": rb["rows"], "digest_old": ra["digest"], "digest_new": rb["digest"],
             "header_old": ra["header"], "header_new": rb["header"], "changed": changed,
             "changed_rows": len(changed_rows), "first_changed_row": changed_rows[0] if changed_rows else None,
             "type_changes": len(retyped), "first_type_change": first, "formulas_new": rb["formulas"]}
    if changed:
        parts = [f"{ra['rows']:,} rows -> {rb['rows']:,} rows"]
        if rb["rows"] > ra["rows"]:
            parts.append(f"{rb['rows'] - ra['rows']:,} rows added at the end")
        elif rb["rows"] < ra["rows"]:
            parts.append(f"{ra['rows'] - rb['rows']:,} rows fewer")
        if changed_rows:
            parts.append(f"{len(changed_rows):,} rows changed (first: row {changed_rows[0]:,})")
        if first:
            parts.append(f"{len(retyped):,} cells changed their stored type (first: {first['cell']}: {first['old']} "
                         f"-> {first['new']})")
        if ra["header"] != rb["header"]:
            parts.append(f"header changed: {ra['header']} -> {rb['header']}")
        ending = ("digest changed" if digest_changed else
                  "digest unchanged (the digest reads each value by its column type, not by its stored type)")
        report.info("D-RAW", f"raw sheet {name}: " + "; ".join(parts) + f"; {ending}", sheet=name,
                    **({"cell": first["cell"], "count": len(retyped),
                        "cells": [common.a1(c, r) for r, c, _a, _b in retyped[:20]]} if first else {}))
    if rb["formulas"]:
        report.info("D-RAW", f"raw sheet {name} holds {rb['formulas']} formula cells in the new version "
                    "(a raw sheet holds landed values only; rotm.py audit reports them as R-FORMULA)", sheet=name)
    return entry


def _structure(a: Any, b: Any) -> list[str]:
    out: list[str] = []
    ta = {t.name: f"{t.sheet}!{t.ref}" for t in a.tables()}
    tb = {t.name: f"{t.sheet}!{t.ref}" for t in b.tables()}
    for n in sorted(set(ta) | set(tb)):
        if ta.get(n) != tb.get(n):
            out.append(f"table '{n}': {ta.get(n) or '(none)'} -> {tb.get(n) or '(none)'}")
    na = {(f"{s}!{n}" if s else n): v for n, v, s in a.defined_names if not n.startswith("_xlfn.")}
    nb = {(f"{s}!{n}" if s else n): v for n, v, s in b.defined_names if not n.startswith("_xlfn.")}
    for n in sorted(set(na) | set(nb)):
        if na.get(n) != nb.get(n):
            out.append(f"defined name '{n}': {na.get(n) or '(none)'} -> {nb.get(n) or '(none)'}")
    for what, x, y in (("charts", a.package.charts, b.package.charts), ("Power Query queries", a.package.queries,
                       b.package.queries), ("pivot tables", a.package.pivots, b.package.pivots)):
        if x != y:
            out.append(f"{what}: {x} -> {y}")
    for s in b.sheets:
        sa = a.sheet(s.name)
        if sa is None:
            continue
        if sa.cf_count != s.cf_count:
            out.append(f"sheet '{s.name}': conditional-format rules {sa.cf_count} -> {s.cf_count}")
        if sa.dv_count != s.dv_count:
            out.append(f"sheet '{s.name}': data validations {sa.dv_count} -> {s.dv_count}")
        if sa.state != s.state:
            out.append(f"sheet '{s.name}': {sa.state} -> {s.state}")
    if a.sheet_names != b.sheet_names and not (set(a.sheet_names) ^ set(b.sheet_names)):
        out.append(f"sheet order: {', '.join(a.sheet_names)} -> {', '.join(b.sheet_names)}")
    return out


def _entry_dict(e: dict[str, Any]) -> dict[str, Any]:
    def groups(gs: list[Group]) -> list[dict[str, Any]]:
        out = []
        for g in gs:
            first = g.cells[0]
            item = {"ref": g.ref, "kind": g.kind, "cells": len(g.cells), "name": first.name,
                    "old": _json_value(first.old), "new": _json_value(first.new)}
            if g.category == "formula" and g.kind in ("added", "removed"):
                # v3 (A-aera-16): both sides' values (the formula's saved value, the typed value)
                item.update(old_value=_json_value(first.old_value), new_value=_json_value(first.new_value),
                            old_saved=first.old_saved, new_saved=first.new_saved)
            if type_changed(first.old_type, first.new_type):
                item.update(old_type=first.old_type, new_type=first.new_type)       # v6 (A-aera-5)
            out.append(item)
        return out
    return {"formulas": groups(e["formulas"]), "constants": groups(e["constants"]),
            "outputs": groups(e["outputs"]), "raw": e["raw"]}


def _json_value(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    return str(v)


def _md(text: Any) -> str:
    return str("" if text is None else text).replace("|", "\\|").replace("\n", " ")


def write_markdown(report: Any, path: str | Path) -> Path:
    """The differences as Markdown: a summary per sheet, then every changed cell (up to MD_CELL_LIMIT per
    category and sheet)."""
    from . import common
    d = report.data
    per_sheet: dict[str, dict[str, Any]] = getattr(report, "groups", {})
    lines = ["# Workbook differences", "",
             f"- Old: `{d['old']}` (sha256 {d['old_sha256'][:12]})",
             f"- New: `{d['new']}` (sha256 {d['new_sha256'][:12]})",
             f"- Written by rule-of-thumb-model {common.VERSION} (rotm.py diff) on {report.at}.",
             "- Compared: formulas (without _xlfn. prefixes), constants outside raw sheets (exactly), "
             + (f"the values Excel saved for formula cells "
                f"({tolerance_words(d.get('calc_tolerance', 0.0), subject=False)}), "
                if d["outputs_compared"] else "")
             + "raw sheets by row count, digest and the stored type of each cell, and the workbook structure; "
               "a cell whose stored type changed (number, text, logical value) is listed with both types."]
    if not d["outputs_compared"]:
        lines.append("- Outputs were not compared: a workbook holds no values saved by Excel (not recalculated).")
    lines += ["", "## Summary", "",
              "| Sheet | Formula cells | Constant cells | Output cells | Raw sheet |",
              "| --- | ---: | ---: | ---: | --- |"]
    for s in d["sheets_added"]:
        lines.append(f"| {_md(s)} | sheet added | | | |")
    for s in d["sheets_removed"]:
        lines.append(f"| {_md(s)} | sheet removed | | | |")
    for s, e in per_sheet.items():
        raw = e["raw"]
        raw_text = "" if raw is None else ("changed" if raw["changed"] else "unchanged")
        lines.append(f"| {_md(s)} | {sum(len(g.cells) for g in e['formulas'])} | "
                     f"{sum(len(g.cells) for g in e['constants'])} | {sum(len(g.cells) for g in e['outputs'])} | "
                     f"{raw_text} |")
    for s, e in per_sheet.items():
        if not (e["formulas"] or e["constants"] or e["outputs"] or (e["raw"] and e["raw"]["changed"])):
            continue
        lines += ["", f"## {s}"]
        if e["raw"] and e["raw"]["changed"]:
            r = e["raw"]
            lines += ["", f"- Rows: {r['rows_old']:,} -> {r['rows_new']:,}",
                      f"- Rows changed in place: {r['changed_rows']:,}"
                      + (f" (first: row {r['first_changed_row']:,})" if r["first_changed_row"] else ""),
                      f"- Digest: `{r['digest_old']}` -> `{r['digest_new']}`"]
            if r.get("first_type_change"):
                t = r["first_type_change"]
                lines.append(f"- Stored type changed: {r['type_changes']:,} cells (first: {t['cell']}: "
                             f"{_md(t['old'])} -> {_md(t['new'])})")
            if r["header_old"] != r["header_new"]:
                lines.append(f"- Header: {r['header_old']} -> {r['header_new']}")
        for title, key, cols in (("Formulas", "formulas", ("Cells", "Change", "Old formula", "New formula", "Name")),
                                 ("Constants (inputs and labels)", "constants",
                                  ("Cell", "Change", "Old value", "New value", "Name")),
                                 ("Outputs (values Excel saved)", "outputs",
                                  ("Cell", "Old value", "New value", "Difference", "Name"))):
            groups = e[key]
            if not groups:
                continue
            lines += ["", f"### {title}", "", "| " + " | ".join(cols) + " |", "|" + " --- |" * len(cols)]
            shown = 0
            for g in groups:
                if key == "formulas":
                    first = g.cells[0]
                    n = len(g.cells)
                    old_text, new_text = side_texts(first, code=True)
                    lines.append(f"| {g.ref}{f' ({n})' if n > 1 else ''} | {g.kind} | {old_text} | "
                                 f"{new_text} | {_md(first.name)} |")
                    shown += 1
                else:
                    for ch in g.cells:
                        if shown >= MD_CELL_LIMIT:
                            break
                        old_md, new_md = change_sides(ch, code=True)             # v6: both stored types
                        if key == "constants":
                            lines.append(f"| {ch.coord} | {ch.kind} | {old_md} | {new_md} | {_md(ch.name)} |")
                        else:
                            diff = ""
                            try:
                                from . import workbook
                                x, y = workbook._as_number(ch.old), workbook._as_number(ch.new)
                                if x is not None and y is not None:
                                    diff = f"{y - x:,.6g}"
                            except Exception:  # noqa: BLE001
                                diff = ""
                            lines.append(f"| {ch.coord} | {old_md} | {new_md} | {diff} | {_md(ch.name)} |")
                        shown += 1
                if shown >= MD_CELL_LIMIT:
                    lines.append(f"| ... | more than {MD_CELL_LIMIT:,} cells; see the JSON report | | | |")
                    break
    if d["structure"]:
        lines += ["", "## Structure", ""] + [f"- {_md(x)}" for x in d["structure"]]
    if report.notes:
        lines += ["", "## Notes", ""] + [f"- {_md(n)}" for n in report.notes]
    target = Path(common.native_path(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return target


def add_diff_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("old", help="the old workbook (or its model folder)")
    p.add_argument("new", help="the new workbook (or its model folder)")
    p.add_argument("--md", metavar="FILE", help="write the differences as Markdown")
    p.add_argument("--json", metavar="FILE", help="write the report as JSON ('-' = stdout)")
    p.add_argument("--fail-on-change", action="store_true", help="exit 1 when anything differs")
    from . import compat
    compat.add_calc_tolerance_argument(p)
    p.add_argument("--quiet", action="store_true", help="print only the counts")


def run_diff(args: argparse.Namespace) -> int:
    from . import common
    report = diff_workbooks(args.old, args.new, calc_tolerance=getattr(args, "calc_tolerance", None))
    if args.md:
        path = write_markdown(report, args.md)
        report.note(f"wrote {common.norm_path(path)}")
    if getattr(args, "fail_on_change", False) and report.findings:
        report.exit_code, report.status = common.EXIT_FINDINGS, "fail"
    return report.emit(args.json, quiet=getattr(args, "quiet", False))

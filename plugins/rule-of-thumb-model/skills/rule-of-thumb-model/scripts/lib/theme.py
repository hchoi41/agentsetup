"""`theme`: apply the style dictionary to an existing workbook without changing any value or formula
(design sections 10.5, 11; F08, F08a).

Owner: M2. Writes through openpyxl only when the package has no fragile part (and the file is .xlsx), otherwise
through lib.excel.apply_edits, otherwise refuses (exit 3). Compares before and after by code (workbook.compare).

Steps (design 11):
1. Read the workbook (never written), load the dictionary (the model folder's style_overrides.json when the
   workbook sits in a model folder), plan each sheet's type and size and classify every non-empty cell
   (lib.classify: content only, so the audit afterwards infers the same types and sizes).
2. List the cells whose look differs from the dictionary, attribute by attribute (lib.styles.mismatches). With
   --number-formats, a raw cell of a workbook with a manifest gets the number format of its column's key in the
   raw sheet's manifest entry ("formats", written by build), or the column type's default when no key is
   recorded (v2, J-1; the same expectation as the audit's S-NUMFMT). Without a manifest raw cells keep theirs.
3. Write them into a working copy: openpyxl when no fragile part exists, else Excel; else refuse, nothing written.
4. Compare the working copy with the original: every formula and typed value (exactly), the sheets, tables,
   defined names, charts, conditional formats, data validations, merged ranges, pivot tables and Power Query
   queries must be equal. With Excel (default when it is available) both are recalculated on copies first and
   compared under the one preservation rule of theme, compat and split (v2, A-aera-16): cached values of formula
   cells within --calc-tolerance (relative), except cells that hold a volatile function or depend on one, whose
   values are left out and counted; the report states it in one sentence (workbook.preservation_statement).
   Any difference: error finding, nothing written.
5. Read the copy again and compare each cell with the dictionary: what still differs is a warning (T-RESIDUAL).
6. Deliver: a new file <stem>_theme<suffix> (or -o), or with --in-place the original after a backup
   <stem>.bak_<YYYYMMDD_HHMMSS><suffix>. A model folder's manifest and reports/status.json are updated.
7. The last note of the report is the follow-up audit command with the look options theme was given (each
   --use, --dictionary, --raw-sheets), so that the audit loads the same dictionary (v5, C-sol-11).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

# Finding ids of this command (they appear in theme reports only).
RULES: dict[str, tuple[str, str]] = {
    "T-STYLE": ("info", "cells of one sheet restyled (or to be restyled with --dry-run), counted by attribute"),
    "T-UNCHANGED": ("info", "no cell needed a change"),
    "T-UNINTENDED": ("error", "a formula, value or workbook part differed after theme; nothing was written"),
    "T-RESIDUAL": ("warning", "after theme a cell still differs from the dictionary"),
    "T-RECLASSIFIED": ("warning", "after theme the classifier gave a cell another type"),
    "T-NOT-VERIFIED": ("warning", "values could not be compared after recalculation in Excel"),
    "T-NOT-RECALC": ("info", "Excel is not available: formulas and typed values were compared, cached values not"),
    "T-VIEWS": ("warning", "--views was given but gridlines and zoom could not be set through Excel"),
    "T-NUMFMT-SKIPPED": ("info", "--number-formats: a check cell that does not hold 0 or 1 kept its format"),
    "T-CF": ("info", "conditional formats can draw colours the dictionary does not define; theme leaves them"),
    "T-TABLESTYLE": ("info", "an Excel table style draws banded colours the dictionary does not define"),
    "T-RICHTEXT": ("info", "rich text inside cells: theme sets one font for the whole cell"),
    "T-FRAGILE": ("error", "the workbook holds parts openpyxl would drop and Excel is not available"),
}
_FLAG_FORMULA_RE = re.compile(r"^=\s*(--|N\(|IF\(|AND\(|OR\(|NOT\()", re.I)
_OPENPYXL_SUFFIXES = (".xlsx",)


@dataclass
class _Target:
    sheet: str
    coord: str
    row: int
    col: int
    cell_type: str
    expected: dict[str, Any]
    attributes: tuple[str, ...]
    changed: tuple[str, ...]              # attributes that differ now
    value: Any
    format_key: str | None = None         # number-format key of a raw cell (v2, J-1), else None


# ----------------------------------------------------------------------------- helpers

def compress_ranges(cells: Sequence[tuple[int, int]]) -> list[str]:
    """(row, col) pairs -> rectangle addresses ("A1", "B2:D9"): runs along each row, then runs of equal runs
    down the rows. Row-major order of the rectangles' top-left corners."""
    from . import common
    by_row: dict[int, list[int]] = {}
    for r, c in set(cells):
        by_row.setdefault(r, []).append(c)
    open_rects: dict[tuple[int, int], tuple[int, int]] = {}      # (c1, c2) -> (r1, r_last)
    rects: list[tuple[int, int, int, int]] = []
    for r in sorted(by_row):
        cols = sorted(by_row[r])
        runs: list[tuple[int, int]] = []
        start = prev = cols[0]
        for c in cols[1:]:
            if c == prev + 1:
                prev = c
                continue
            runs.append((start, prev))
            start = prev = c
        runs.append((start, prev))
        current = set(runs)
        for key in list(open_rects):
            r1, last = open_rects[key]
            if key not in current or last != r - 1:
                rects.append((r1, last, key[0], key[1]))
                del open_rects[key]
        for key in runs:
            if key in open_rects:
                open_rects[key] = (open_rects[key][0], r)
            else:
                open_rects[key] = (r, r)
    for (c1, c2), (r1, r2) in open_rects.items():
        rects.append((r1, r2, c1, c2))
    out = []
    for r1, r2, c1, c2 in sorted(rects, key=lambda x: (x[0], x[2])):
        a = f"{common.col_letter(c1)}{r1}"
        b = f"{common.col_letter(c2)}{r2}"
        out.append(a if a == b else f"{a}:{b}")
    return out


def _load_dictionary(src: Path, dictionary: Any, use: Sequence[str]) -> Any:
    from . import common
    from . import dictionary as dict_mod
    from . import modelfolder
    if dictionary is not None and not isinstance(dictionary, (str, os.PathLike)):
        if use:
            raise common.Refusal("give named overrides through the dictionary object or --use, not both",
                                 key="use")
        return dictionary
    if dictionary is not None:
        return dict_mod.load_dictionary(dictionary, use=use)
    root = modelfolder.find_model_root(src)
    if root is None:
        return dict_mod.load_dictionary(use=use)
    spec_override = None
    try:
        marker = modelfolder.read_marker(root)
        spec_path = Path(root) / str(marker.get("spec") or "model.spec.json")
        if spec_path.is_file():
            spec_override = (common.read_json(spec_path) or {}).get("dictionary")
    except (common.RotmError, OSError, ValueError, AttributeError):
        spec_override = None
    return dict_mod.load_for_model(root, spec_override=spec_override, use=use)


def _default_out(src: Path) -> Path:
    return src.with_name(f"{src.stem}_theme{src.suffix}")


def _check_out(src: Path, out: Path) -> Path:
    from . import common
    if out.suffix.lower() not in (".xlsx", ".xlsm", ".xltx", ".xltm"):
        raise common.Refusal(f"{out.name}: the output must be a workbook (.xlsx or .xlsm)", key="out",
                             fix=f"give a file name ending in {src.suffix}")
    if out.suffix.lower() != src.suffix.lower():
        raise common.Refusal(f"{out.name}: the output has another extension than {src.name}", key="out",
                             fix=f"give a file name ending in {src.suffix} (a change of format can drop parts)")
    if os.path.normcase(str(out.resolve())) == os.path.normcase(str(src.resolve())):
        raise common.Refusal(f"{out.name} is the workbook itself", key="out",
                             fix="use --in-place to replace the original (a .bak copy is kept), or give another -o")
    return out


def _flag_value(cell: Any) -> bool:
    """A check cell that can take the check format: it holds 0 or 1 (or TRUE/FALSE), or, without a cached value,
    its formula is a flag (=--(...), =N(...), =IF(...), AND, OR, NOT) with no text literal (a formula that can
    show "OK" or "ERROR" is not a 0/1 flag)."""
    v = cell.cached if cell.formula is not None else cell.value
    if isinstance(v, bool):
        return True
    if isinstance(v, (int, float)):
        return float(v) in (0.0, 1.0)
    if v is None and cell.formula is not None:
        return bool(_FLAG_FORMULA_RE.match(cell.formula)) and '"' not in cell.formula
    return False


def _attributes(number_formats: bool, borders: bool, align: bool) -> tuple[str, ...]:
    from . import styles
    attrs = list(styles.THEME_ATTRIBUTES)
    if number_formats:
        attrs.append("number_format")
    if borders:
        attrs.append("border")
    if align:
        attrs.append("align")
    return tuple(attrs)


def raw_format_keys(manifest: dict[str, Any] | None, d: Any) -> dict[tuple[str, int], str]:
    """(raw sheet, column number) -> number-format key of its raw cells (v2, J-1): the key the raw sheet's
    manifest entry records for the column ("formats", written by build), else the column type's default
    (dictionary.format_for_column_type). The same expectation as audit's S-NUMFMT. {} without a manifest."""
    from . import common
    out: dict[tuple[str, int], str] = {}
    for entry in (manifest or {}).get("sheets", []) or []:
        if not isinstance(entry, dict) or entry.get("type") != "raw" or not entry.get("types"):
            continue
        built = entry.get("formats") if isinstance(entry.get("formats"), list) else []
        for i, col_type in enumerate(entry["types"]):
            key = built[i] if i < len(built) and isinstance(built[i], str) and built[i] else None
            try:
                key = key or d.format_for_column_type(col_type)
                d.number_format(key)
            except common.RotmError:
                continue
            out[(str(entry.get("name")), i + 1)] = key
    return out


def _targets(info: Any, d: Any, plan: dict[str, Any], types: dict[tuple[str, str], str],
             attributes: tuple[str, ...], report: Any) -> list[_Target]:
    from . import styles
    out: list[_Target] = []
    skipped: dict[str, list[str]] = {}
    raw_keys = raw_format_keys(info.manifest, d) if "number_format" in attributes else {}
    for s in info.sheets:
        sp = plan.get(s.name)
        if sp is None:
            continue
        for cell in s.iter_cells():
            t = types.get((s.name, cell.coord))
            if t is None:
                continue
            attrs = attributes
            if "number_format" in attrs and t in ("check", "master_check") and not _flag_value(cell):
                attrs = tuple(a for a in attrs if a != "number_format")
                skipped.setdefault(s.name, []).append(cell.coord)
            key = raw_keys.get((s.name, cell.col)) if t == "raw" and cell.row >= 2 else None
            exp = styles.expected_style(d, t, sheet_size=sp.size, format_key=key)
            changed = tuple(m.attribute for m in styles.mismatches(cell, exp, attributes=attrs))
            out.append(_Target(s.name, cell.coord, cell.row, cell.col, t, exp, attrs, tuple(dict.fromkeys(changed)),
                               cell.value, key))
    for sheet, coords in skipped.items():
        report.info("T-NUMFMT-SKIPPED", f"{len(coords)} check cells hold a value other than 0 or 1, so they keep "
                    "their number format", "convert the check to a 0/1 flag (checks.md), then run theme again",
                    sheet=sheet, cell=coords[0], count=len(coords), cells=coords[:20])
    return out


def _excel_ops(targets: list[_Target], d: Any, plan: dict[str, Any]) -> list[dict[str, Any]]:
    from . import styles
    groups: dict[tuple[Any, ...], list[tuple[int, int]]] = {}
    specs: dict[tuple[Any, ...], dict[str, Any]] = {}
    for t in targets:
        if not t.changed:
            continue
        spec = styles.style_spec(d, t.cell_type, size=plan[t.sheet].size, format_key=t.format_key, value=t.value,
                                 attributes=t.attributes)
        key = (t.sheet, repr(sorted(spec.items())))
        groups.setdefault(key, []).append((t.row, t.col))
        specs[key] = spec
    ops = []
    for key, cells in groups.items():
        ops.append({"op": "style", "sheet": key[0], "ranges": compress_ranges(cells), **specs[key]})
    return ops


def _write_openpyxl(src: Path, dest: Path, targets: list[_Target], plan: dict[str, Any], d: Any,
                    views: bool) -> None:
    import warnings

    import openpyxl

    from . import styles
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(src)
    for t in targets:
        if t.changed:
            styles.apply_attributes(wb[t.sheet][t.coord], t.expected, attributes=t.attributes)
    if views:
        for name, sp in plan.items():
            view = d.sheet_view(sp.sheet_type)
            ws = wb[name]
            ws.sheet_view.showGridLines = bool(view["gridlines"])
            ws.sheet_view.zoomScale = int(view["zoom"])
    wb.save(dest)


def _settle_with_excel(src: Path, candidate: Path, diffs: list[Any], report: Any) -> list[Any]:
    """Conditional-format and data-validation counts as openpyxl reads them can differ between a file written by
    another program and the same file saved by Excel: Excel merges duplicate rules when it opens a file (seen on
    a foreign copy on 2026-11-25: openpyxl counted 23 rules, Excel 19, and a plain Excel re-save also held 19).
    Such differences are settled with Excel's own inventory of both files: equal there, the difference is dropped
    with a note; different there, it stays."""
    from . import common, excel
    kinds = {"cf": "format_conditions", "dv": "validation_cells"}
    pending = [x for x in diffs if x.kind in kinds]
    if not pending:
        return diffs
    try:
        inv_before = {s.get("name"): s for s in excel.inventory(src).get("sheets", [])}
        inv_after = {s.get("name"): s for s in excel.inventory(candidate).get("sheets", [])}
    except common.ExcelBusy:
        raise
    except common.RotmError as exc:
        report.note(f"conditional-format and validation counts were not checked with Excel: {exc.message}")
        return diffs
    kept = [x for x in diffs if x.kind not in kinds]
    for x in pending:
        key = kinds[x.kind]
        b = (inv_before.get(x.sheet) or {}).get(key)
        a = (inv_after.get(x.sheet) or {}).get(key)
        if b is not None and b == a:
            report.note(f"sheet '{x.sheet}': openpyxl reads {x.before} {x.kind} entries in the original and {x.after} "
                        f"in the result; Excel reads {b} {key.replace('_', ' ')} in both (Excel merges duplicate "
                        "rules when it opens a file), so nothing was lost")
        else:
            x.message += f" (Excel's own count: {b} before, {a} after)"
            kept.append(x)
    return kept


def _report_differences(report: Any, diffs: list[Any], when: str) -> None:
    for diff in diffs[:200]:
        report.error("T-UNINTENDED", f"{when}: {diff.message}",
                     "nothing was written; report this workbook (theme must change styles only)",
                     sheet=diff.sheet, cell=diff.cell, data={"kind": diff.kind})
    if len(diffs) > 200:
        report.note(f"{len(diffs) - 200} more differences were found and not listed")


def _verify_by_recalc(src: Path, candidate: Path, work: Path, report: Any,
                      calc_tolerance: float | None = None) -> tuple[bool, Path | None]:
    """Recalculate copies of the original and of the candidate in Excel and compare them under the one
    preservation rule (compat.preserved: formulas and typed values exactly, calculated values within
    `calc_tolerance`, cells that hold a volatile function or depend on one left out of the value comparison and
    counted). Returns (passed, the recalculated candidate copy) or (True, None) when the comparison could not
    run."""
    from . import common, compat, excel, workbook
    tolerance = compat.check_calc_tolerance(calc_tolerance)
    before = work / f"before{src.suffix}"
    after = work / f"after{src.suffix}"
    shutil.copy2(src, before)
    shutil.copy2(candidate, after)
    try:
        r1 = excel.recalc(before, save=True)
        r2 = excel.recalc(after, save=True)
    except common.ExcelBusy:
        raise
    except common.RotmError as exc:
        report.warning("T-NOT-VERIFIED", f"values were not compared after recalculation: {exc.message}",
                       "open both files in Excel and compare, or run theme again when Excel works")
        return True, None
    if not (r1.ok and r2.ok):
        why = "; ".join(r1.errors + r2.errors) or "the Excel job did not finish"
        report.warning("T-NOT-VERIFIED", f"values were not compared after recalculation: {why}",
                       "open both files in Excel and compare, or run theme again when Excel works")
        return True, None
    sb = workbook.snapshot(before)
    sa = workbook.snapshot(after)
    report.note("both the original and the result were recalculated in Excel (copies) and compared")
    diffs = compat.preserved(sb, sa, workbook.Intended(kind="styles_only"), calc_tolerance=tolerance, report=report)
    if diffs:
        _report_differences(report, diffs, "after recalculation in Excel")
        return False, None
    report.verified["recalculated"] = True
    return True, after


def _residual(path: Path, d: Any, plan: dict[str, Any], types: dict[tuple[str, str], str],
              targets: list[_Target], raw_sheets: set[str] | None, manifest: dict[str, Any] | None,
              report: Any) -> int:
    """Compare the written copy with the dictionary again (the original's manifest, because the working copy has
    no sidecar); returns the number of cells that still differ."""
    from . import classify, styles, workbook
    info = workbook.open_workbook(path)
    new_types = classify.classify_workbook(info, manifest, raw_sheets=raw_sheets)
    by_key = {(t.sheet, t.coord): t for t in targets}
    moved: dict[str, list[str]] = {}
    left: dict[tuple[str, str], list[str]] = {}
    for s in info.sheets:
        sp = plan.get(s.name)
        if sp is None:
            continue
        for cell in s.iter_cells():
            key = (s.name, cell.coord)
            t = by_key.get(key)
            if t is None:
                continue
            if new_types.get(key) != types.get(key):
                moved.setdefault(s.name, []).append(cell.coord)
            exp = styles.expected_style(d, t.cell_type, sheet_size=sp.size, format_key=t.format_key)
            for m in styles.mismatches(cell, exp, attributes=t.attributes):
                left.setdefault((s.name, m.attribute), []).append(cell.coord)
    for sheet, coords in moved.items():
        report.warning("T-RECLASSIFIED", f"{len(coords)} cells get another cell type when the result is read again",
                       "report this workbook: the classifier must not depend on styles", sheet=sheet,
                       cell=coords[0], count=len(coords), cells=coords[:20])
    for (sheet, attr), coords in left.items():
        report.warning("T-RESIDUAL", f"{len(coords)} cells still differ from the dictionary in {attr} after theme",
                       "open the cells in Excel to see what holds the old look (a cell style, rich text or a "
                       "protected sheet), then run theme again", sheet=sheet, cell=coords[0], count=len(coords),
                       cells=coords[:20], data={"attribute": attr})
    return sum(len(v) for v in left.values())


def _place(source: Path, dest: Path) -> None:
    """Copy `source` next to `dest` and replace `dest` atomically."""
    from . import common
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    try:
        shutil.copy2(source, part)
        os.replace(part, dest)
    except PermissionError:
        if part.exists():
            part.unlink()
        raise common.Refusal(f"{dest.name} cannot be written (it is open in Excel or read-only)",
                             fix="close it in Excel, or give another output with -o") from None


def _update_model(src: Path, dest: Path, d: Any, info: Any, report: Any, changed: int) -> None:
    """Manifest beside the result (dictionary fields) and reports/status.json when the result is in a model folder."""
    from . import common, manifest, modelfolder
    if info.manifest is not None:
        data = dict(info.manifest)
        data["workbook"] = dest.name
        data["dictionary_sha256"] = d.sha256
        data["dictionary_applied"] = list(d.applied)
        data["updated_at"] = common.now_iso()
        try:
            manifest.write_manifest(dest, data)
            report.note(f"manifest {manifest.manifest_path(dest).name} written (dictionary fields updated)")
        except common.RotmError as exc:
            report.note(f"manifest not written: {exc.line()}")
    root = modelfolder.find_model_root(dest)
    if root is None:
        return
    try:
        modelfolder.record_step(root, str(dest), "theme", {
            "exit_code": report.exit_code, "sha256": common.file_sha256(dest), "input_sha256": info.sha256,
            "output_sha256": common.file_sha256(dest), "cells_changed": changed, "dictionary_sha256": d.sha256})
    except (common.RotmError, ValueError, OSError) as exc:
        report.note(f"status not recorded in reports/status.json: {exc}")


def _workbook_notes(info: Any, report: Any) -> None:
    for s in info.sheets:
        if s.cf_count:
            report.info("T-CF", f"{s.cf_count} conditional-format rules; they can draw colours the dictionary does "
                        "not define, and theme leaves them as they are",
                        "remove them in Excel when they only add colour (minimal colour, spec 1.2)", sheet=s.name)
    if info.package.rich_text:
        report.info("T-RICHTEXT", "some cells hold rich text (more than one font in one cell); theme gives each such "
                    "cell one font", "")
    _table_styles(info.path, report)


_TABLE_NAME_RE = re.compile(rb'<table\b[^>]*\bdisplayName="([^"]+)"')
_TABLE_STYLE_RE = re.compile(rb'<tableStyleInfo\b[^>]*\bname="([^"]+)"')


def _table_styles(path: Path, report: Any) -> None:
    """Excel tables with a table style (read from xl/tables/*.xml; theme leaves table styles as they are)."""
    import zipfile
    try:
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if not re.match(r"^xl/tables/table\d+\.xml$", name):
                    continue
                head = z.read(name)[:4096]
                style = _TABLE_STYLE_RE.search(head)
                if style:
                    table = _TABLE_NAME_RE.search(head)
                    label = table.group(1).decode("utf-8", "replace") if table else name
                    report.info("T-TABLESTYLE", f"table {label} uses the table style "
                                f"{style.group(1).decode('utf-8', 'replace')}, which draws banded colours the "
                                "dictionary does not define; theme does not change table styles",
                                "in Excel: Table Design > Table Styles > None (SYNTHESIS D8: table style None)")
    except (OSError, zipfile.BadZipFile, KeyError):
        return


# ----------------------------------------------------------------------------- the command

def theme_workbook(path: str | Path, *, out: str | Path | None = None, in_place: bool = False,
                   dictionary: Any = None, use: Sequence[str] = (), number_formats: bool = False,
                   borders: bool = False, views: bool = False, dry_run: bool = False,
                   strict: bool = False, align: bool = False, raw_sheets: Sequence[str] | None = None,
                   verify: bool | None = None, calc_tolerance: float | None = None) -> Any:
    """Returns a common.Report (data: cells changed per sheet and attribute; output path).
    `dictionary` is a lib.dictionary.Dictionary, a path of an override file, or None (the model folder's
    override when the workbook sits in a model folder, else the plugin dictionary). `verify` (default: when
    Excel is available) recalculates copies of both files in Excel before the value comparison.
    `calc_tolerance`: relative tolerance for calculated values in that comparison (default
    workbook.DEFAULT_CALC_TOLERANCE); typed values are compared exactly."""
    from . import classify, common, compat, excel, workbook
    tolerance = compat.check_calc_tolerance(calc_tolerance)
    src = Path(os.path.abspath(common.native_path(path)))
    report = common.Report(command="theme", target=src.as_posix(), strict=strict)
    if in_place and out is not None:
        raise common.Refusal("give -o or --in-place, not both", key="out",
                             fix="-o writes a new file; --in-place replaces the workbook after keeping a .bak copy")
    info = workbook.open_workbook(src)
    d = _load_dictionary(src, dictionary, use)
    dest = src if in_place else _check_out(src, Path(os.path.abspath(common.native_path(out))) if out is not None
                                           else _default_out(src))
    raw = set(raw_sheets or ())
    missing = sorted(raw - set(info.sheet_names))
    if missing:
        raise common.Refusal(f"--raw-sheets names no sheet of this workbook: {', '.join(missing)}", key="raw-sheets",
                             fix="give sheet names exactly as the tabs show them: " + ", ".join(info.sheet_names))
    plan = classify.plan_workbook(info, d, info.manifest, raw_sheets=raw or None)
    types = classify.classify_workbook(info, info.manifest, raw_sheets=raw or None)
    attributes = _attributes(number_formats, borders, align)
    targets = _targets(info, d, plan, types, attributes, report)
    changed = [t for t in targets if t.changed]

    # what changes, per sheet
    sheets_data: dict[str, Any] = {}
    for name, sp in plan.items():
        sheet_targets = [t for t in targets if t.sheet == name]
        counts: dict[str, int] = {}
        for t in sheet_targets:
            for a in t.changed:
                counts[a] = counts.get(a, 0) + 1
        type_counts: dict[str, int] = {}
        for t in sheet_targets:
            type_counts[t.cell_type] = type_counts.get(t.cell_type, 0) + 1
        cells = [t.coord for t in sheet_targets if t.changed]
        sheets_data[name] = {"sheet_type": sp.sheet_type, "size": sp.size, "plan": sp.source, "reason": sp.reason,
                             "cells": len(sheet_targets), "changed_cells": len(cells), "changed": counts,
                             "types": type_counts}
        if cells:
            what = ", ".join(f"{a} {n}" for a, n in sorted(counts.items()))
            verb = "would be restyled" if dry_run else "restyled"
            report.info("T-STYLE", f"{len(cells)} of {len(sheet_targets)} cells {verb} at size {sp.size} "
                        f"({sp.sheet_type} sheet): {what}", "", sheet=name, cell=cells[0], count=len(cells),
                        cells=cells[:20], data={"changed": counts, "size": sp.size, "sheet_type": sp.sheet_type})
    _workbook_notes(info, report)
    fragile = list(info.package.fragile)
    use_excel = bool(fragile) or src.suffix.lower() not in _OPENPYXL_SUFFIXES
    status = excel.excel_status()
    report.data.update({
        "workbook": src.as_posix(), "output": dest.as_posix(), "in_place": in_place, "dry_run": dry_run,
        "written": False, "writer": None, "attributes": list(attributes), "views": views,
        "dictionary": {"sha256": d.sha256, "applied": list(d.applied)}, "fragile_parts": fragile,
        "excel_available": status.available, "cells": len(targets), "cells_changed": len(changed),
        "sheets": sheets_data, "backup": None})
    report.note("sizes: " + "; ".join(f"{n} {sp.size} ({sp.reason})" for n, sp in plan.items()))

    if use_excel and not status.available:
        parts = "; ".join(fragile) if fragile else f"the {src.suffix} format (macros or template parts)"
        report.error("T-FRAGILE", f"{src.name} holds parts openpyxl would drop: {parts}. Excel is not available "
                     f"({status.reason}), so nothing was written", "run this command on a machine with Excel")
        report.data["writer"] = "excel"
        report.finish(strict, not_available=True)
        return report
    if dry_run:
        report.data["writer"] = "excel" if use_excel else "openpyxl"
        report.note("dry run: nothing was written")
        report.finish(strict)
        return report
    if not changed and not views:
        report.info("T-UNCHANGED", "no cell needed a change", "")
        if not in_place:
            _place(src, dest)
            report.data["written"] = True
            report.note(f"{dest.name} is a copy of the original")
        report.finish(strict)
        _follow_up_note(report, dest, dictionary, use, raw_sheets)
        return report

    do_verify = status.available if verify is None else (bool(verify) and status.available)
    if verify and not status.available:
        report.warning("T-NOT-VERIFIED", f"--verify needs Excel, which is not available ({status.reason})",
                       "run theme on a machine with Excel")
    state_work = common.state_dir(create=True) / "theme"
    state_work.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%d_%H%M%S_"), dir=str(state_work)))
    try:
        candidate = work / f"themed{src.suffix}"
        if use_excel:
            report.data["writer"] = "excel"
            ops = _excel_ops(targets, d, plan)
            if ops:
                res = excel.apply_edits(src, ops, out=candidate)
                if not res.ok:
                    raise excel.ExcelJobFailed(f"Excel did not apply the styles: {res.message()}",
                                               fix="close other Excel windows that hold the file and run theme again")
            else:
                shutil.copy2(src, candidate)
            if views:
                report.warning("T-VIEWS", "gridlines and zoom were not set: the workbook goes through Excel, and the "
                               "Excel bridge has no view operation", "in Excel, on each sheet: View > clear "
                               "Gridlines, View > Zoom > 100%")
        else:
            report.data["writer"] = "openpyxl"
            _write_openpyxl(src, candidate, targets, plan, d, views)
        # 4. comparison without recalculation: formulas and typed values (and the parts)
        before = workbook.snapshot(src)
        after = workbook.snapshot(candidate)
        after.has_cached_values = False
        diffs = workbook.compare(before, after, workbook.Intended(kind="styles_only"))
        if diffs and use_excel:
            diffs = _settle_with_excel(src, candidate, diffs, report)
        if diffs:
            _report_differences(report, diffs, "after theme")
            report.finish(strict)
            return report
        deliver = candidate
        if do_verify:
            ok, recalculated = _verify_by_recalc(src, candidate, work, report, tolerance)
            if not ok:
                report.finish(strict)
                return report
            if recalculated is not None and not use_excel:
                deliver = recalculated          # openpyxl dropped the cached values; Excel's copy holds them
        else:
            why = "--no-verify was given" if status.available else "Excel is not available"
            tail = "" if use_excel else "; the result holds no cached values until Excel opens it"
            report.info("T-NOT-RECALC", f"{why}: formulas and typed values were compared and are equal; cached "
                        f"values were not compared after recalculation{tail}", "")
        # 5. the result against the dictionary
        _residual(deliver, d, plan, types, targets, raw or None, info.manifest, report)
        # 6. deliver
        if in_place:
            stamp = time.strftime("%Y%m%d_%H%M%S")
            backup = src.with_name(f"{src.stem}.bak_{stamp}{src.suffix}")
            shutil.copy2(src, backup)
            report.data["backup"] = backup.as_posix()
        _place(deliver, dest)
        report.data["written"] = True
        report.finish(strict)
        _update_model(src, dest, d, info, report, len(changed))
        report.finish(strict)
        _follow_up_note(report, dest, dictionary, use, raw_sheets)
        return report
    finally:
        shutil.rmtree(work, ignore_errors=True)


FOLLOW_UP_RULES = "S-FONT,S-SIZE,S-FILL,S-FONTCOLOR,S-BOLD"   # the style rules of the follow-up audit (skill step 5)


def follow_up_command(output: Path, dictionary: Any = None, use: Sequence[str] = (),
                      raw_sheets: Sequence[str] | None = None) -> str:
    """The audit command that checks theme's result with the look theme applied (v5, C-sol-11): the style rules,
    each --use NAME and the --dictionary file theme was given (and --raw-sheets when given), so that the audit
    loads the same dictionary as theme."""
    from . import audit, common
    args = [output.as_posix(), "--strict", "--rules", FOLLOW_UP_RULES]
    for name in use or ():
        args += ["--use", str(name)]
    if isinstance(dictionary, (str, os.PathLike)):
        args += ["--dictionary", Path(os.path.abspath(common.native_path(dictionary))).as_posix()]
    if raw_sheets:
        args += ["--raw-sheets", ",".join(raw_sheets)]
    return audit._rotm_cmd("audit", *args)


def _follow_up_note(report: Any, dest: Path, dictionary: Any, use: Sequence[str],
                    raw_sheets: Sequence[str] | None) -> None:
    """The last note of the report: the follow-up audit command, when the result exists."""
    if report.data.get("written") or (report.data.get("in_place") and dest.is_file()):
        report.note("follow-up audit: " + follow_up_command(dest, dictionary, use, raw_sheets))


def add_theme_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("workbook")
    p.add_argument("-o", "--out", help="output file (default <stem>_theme.xlsx next to the workbook)")
    p.add_argument("--in-place", action="store_true", help="replace the workbook (a .bak_<time>.xlsx copy is kept)")
    p.add_argument("--dictionary", help="project override file (style_overrides.json format)")
    p.add_argument("--use", action="append", default=[], metavar="NAME", help="named override, e.g. input_blue_font")
    p.add_argument("--number-formats", action="store_true", help="also set number formats")
    p.add_argument("--borders", action="store_true", help="also set borders")
    p.add_argument("--views", action="store_true", help="also set gridlines and zoom")
    p.add_argument("--align", action="store_true", help="also set horizontal alignment and wrap")
    p.add_argument("--raw-sheets", metavar="S,...", help="sheets to treat as raw data (comma-separated names)")
    p.add_argument("--no-verify", action="store_true",
                   help="do not recalculate copies in Excel before comparing values (default: verify when Excel "
                        "is available)")
    from . import compat
    compat.add_calc_tolerance_argument(p)
    p.add_argument("--dry-run", action="store_true", help="report what would change; write nothing")
    p.add_argument("--strict", action="store_true", help="exit 1 on any warning")
    p.add_argument("--json", metavar="FILE", help="write the report as JSON ('-' = stdout)")
    p.add_argument("--quiet", action="store_true", help="print only the summary line")


def run_theme(args: argparse.Namespace) -> int:
    raw = [s.strip() for s in (getattr(args, "raw_sheets", None) or "").split(",") if s.strip()]
    report = theme_workbook(args.workbook, out=args.out, in_place=args.in_place, dictionary=args.dictionary,
                            use=args.use, number_formats=args.number_formats, borders=args.borders,
                            views=args.views, dry_run=args.dry_run, strict=args.strict,
                            align=getattr(args, "align", False), raw_sheets=raw or None,
                            verify=False if getattr(args, "no_verify", False) else None,
                            calc_tolerance=getattr(args, "calc_tolerance", None))
    return report.emit(args.json, quiet=getattr(args, "quiet", False))

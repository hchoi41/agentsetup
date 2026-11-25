"""`document`: write the model's map: sheets, inputs, helper columns, checks, column contracts
(design section 15; F22).

Owner: M8. Reads the model folder (marker, specification, contracts, reports/status.json), the workbook's
manifest and the workbook itself (lib.workbook, never written), and writes one Markdown file.

With a manifest the map is exact: every sheet with its type and font size, every input with its value, unit,
cell and source, every raw table with its contract and agreement record (the user's reply quoted as recorded),
every helper column with its letter, header and formula, every block with its range, shape, rows, columns and
expression, every check with its cell and saved value, the key outputs, the master check, the dictionary
overrides and the build record. Without a manifest (a workbook this plugin did not build) the map is inferred:
sheets with their size and role guessed from names and query tables, columns that hold the same formula in 3 or
more rows (helper-like columns), defined names, tables and queries; it says it is inferred.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
from pathlib import Path
from typing import Any

DEFAULT_NAME = "MODEL_MAP.md"
LIST_SHOWN = 12            # list values shown per input before "and N more"
INFERRED_MIN_RUN = 3       # formula cells with one relative form that make a helper-like column (design 14)
SHEET_ROLES = {"cover": "title, purpose, contents, cell-type legend, master check, build record",
               "inputs": "every typed number of the model: assumptions, lists, input grids, contract controls",
               "raw": "landed data only (no formulas, no edits); one table from A1",
               "helpers": "one helper row per raw row, one step per column",
               "calc": "calculation blocks", "output": "key results",
               "checks": "every check and the master check", "notes": "notes with sources"}
RAW_NAME_RE = re.compile(r"^(raw|data|src|landed)[_ -]?|_raw$", re.IGNORECASE)


def _md(v: Any) -> str:
    return str("" if v is None else v).replace("|", "\\|").replace("\n", " ")


def _fmt(v: Any) -> str:
    """A value as the map shows it."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, dt.datetime):
        return v.date().isoformat() if v.time() == dt.time(0) else v.isoformat(sep=" ")
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, float):
        return f"{v:,.6g}" if abs(v) < 1e6 else f"{v:,.2f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _table(head: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "|" + " --- |" * len(head)]
    for r in rows:
        out.append("| " + " | ".join(_md(x) for x in r) + " |")
    return out


def _resolve(target: str | Path) -> tuple[Path | None, Path | None]:
    """(model folder or None, workbook or None)."""
    from . import common, modelfolder
    p = Path(common.native_path(target)).absolute()
    if p.is_dir():
        if not modelfolder.is_model_root(p):
            raise common.Refusal(f"{p.as_posix()} is not a model folder (no valid rotm_model.json)",
                                 fix="give a model folder made by rotm.py init, or a workbook file")
        marker = modelfolder.read_marker(p)
        wb = p / marker.get("workbook", "model.xlsx")
        return p, wb if wb.is_file() else None
    if not p.is_file():
        raise common.Refusal(f"{p.as_posix()} not found", fix="give a model folder or a workbook file")
    return modelfolder.find_model_root(p), p


def _cell_value(info: Any, ref: str | None) -> Any:
    """The value a workbook cell holds ('sheet!D7'): the saved value of a formula, or the constant."""
    from . import common
    if info is None or not ref:
        return None
    sheet, cell = common.parse_ref(ref)
    s = info.sheet(sheet) if sheet else None
    if s is None or ":" in cell:
        return None
    try:
        c = s.cell(cell)
    except ValueError:
        return None
    if c is None:
        return None
    return c.cached if c.is_formula else c.value


def _cell_formula(info: Any, sheet: str, coord: str) -> str | None:
    if info is None:
        return None
    s = info.sheet(sheet)
    c = s.cell(coord) if s is not None else None
    return c.formula if c is not None else None


def _dim_text(spec: Any, block: dict[str, Any], dim: str) -> str:
    node = block.get(dim)
    if node is None:
        return "one column 'Value'" if dim == "cols" else ""
    if "values" in node:
        vals = [str(v) for v in node["values"]]
        return f"{len(vals)} values: " + ", ".join(vals[:LIST_SHOWN]) + (" ..." if len(vals) > LIST_SHOWN else "")
    if "in" in node:
        inp = spec.inputs.get(node["in"]) if spec else None
        n = len(inp["item"].get("values", [])) if inp else "?"
        return f"list input {node['in']} ({n} values)"
    if "timeline" in node:
        t = node["timeline"]
        return f"timeline of {t['periods']} months from input {t['start']} (month ends)"
    if "same_as" in node:
        return f"same as block {node['same_as']}"
    return ""


def _expr_text(block: dict[str, Any]) -> str:
    if "cell" in block:
        return f"every cell: `{block['cell']}`"
    if "items" in block:
        return "; ".join(f"{i['id']}: `{i['expr']}`" + (f" (first column `{i['first']}`)" if "first" in i else "")
                         for i in block["items"])
    return "; ".join(f"{m['id']}: `{m['expr']}`" + (f" (total {m['total']})" if m.get("total") else "")
                     for m in block.get("measures", []))


def document_model(target: str | Path, *, out: str | Path | None = None) -> Any:
    """`target`: a model folder or a workbook. Writes the Markdown map; returns a common.Report."""
    from . import common, manifest as manmod, modelfolder, spec as specmod, workbook
    root, wb_path = _resolve(target)
    report = common.Report("document", common.norm_path(wb_path or root))
    spec = None
    if root is not None and (root / specmod.SPEC_FILE).is_file():
        try:
            spec = specmod.load_spec(root, require_agreed=False)
            for n in spec.notes:
                report.note(n)
        except common.RotmError as exc:
            report.warning("M-SPEC", f"the specification could not be read: {exc.line()[:300]}",
                           "correct model.spec.json (rotm.py build names every problem); the map was written "
                           "from the manifest and the workbook only", sheet=None)
    man = None
    info = None
    if wb_path is not None and wb_path.is_file():
        try:
            man = manmod.load_manifest(wb_path)
        except common.RotmError as exc:
            report.warning("M-MANIFEST", f"the manifest could not be read: {exc.line()[:300]}",
                           "rebuild the workbook with rotm.py build")
        try:
            info = workbook.open_workbook(wb_path, with_styles=False)
        except common.RotmError as exc:
            report.warning("M-WORKBOOK", f"the workbook could not be read: {exc.line()[:300]}", exc.fix or "")
    elif root is not None:
        report.note("the model has no workbook yet (not built); the map shows the specification and the contracts")
    if info is None and spec is None and root is None:
        raise common.Refusal("nothing to document: the workbook could not be read",
                             fix="give a readable .xlsx or a model folder")
    title = (spec.title if spec else None) or (modelfolder.read_marker(root).get("name") if root else None) \
        or (wb_path.stem if wb_path else "model")
    lines: list[str] = [f"# Model map: {title}", ""]
    lines += _header(report, root, wb_path, spec, man, info)
    if man is not None or spec is not None:
        lines += _status_section(root, wb_path)
        lines += _sheets_section(spec, man, info)
        lines += _raw_section(root, spec, man)
        lines += _inputs_section(spec, man, info)
        lines += _helpers_section(spec, man, info)
        lines += _blocks_section(spec, man, info)
        lines += _checks_section(spec, man, info)
        lines += _outputs_section(spec, man, info)
        lines += _style_section(spec, man)
        lines += _notes_section(root, spec)
        mode = "manifest" if man is not None else "specification"
    else:
        lines += _inferred(info)
        mode = "inferred"
    if out is not None:
        out_path = Path(common.native_path(out)).absolute()
    elif root is not None:
        out_path = root / DEFAULT_NAME
    else:
        out_path = wb_path.with_name(f"{wb_path.stem}_{DEFAULT_NAME}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + ".tmp")
    tmp.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")
    tmp.replace(out_path)
    report.data = {"path": common.norm_path(out_path), "mode": mode,
                   "sections": [x[3:] for x in lines if x.startswith("## ")]}
    report.note(f"wrote {common.norm_path(out_path)} ({mode} map, {len(report.data['sections'])} sections)")
    report.finish()
    return report


def _header(report: Any, root: Path | None, wb: Path | None, spec: Any, man: dict | None, info: Any) -> list[str]:
    from . import common
    out = []
    if root is not None:
        out.append(f"- Model folder: `{root.as_posix()}`")
    if wb is not None:
        out.append(f"- Workbook: `{wb.as_posix()}`" + (f" (sha256 {info.sha256[:12]})" if info else ""))
    if spec is not None:
        out.append(f"- Specification: `{common.relpath_posix(spec.path, root or spec.root)}` (sha256 {spec.sha256[:12]})")
        if spec.data.get("purpose"):
            out.append(f"- Purpose: {spec.data['purpose']}")
        out.append(f"- Product: {spec.product} (functions checked for "
                   + {"both": "Excel and Google Sheets", "excel": "Excel", "sheets": "Google Sheets"}[spec.product] + ")")
        s = spec.settings
        out.append(f"- Rules: nesting depth at most {s['depth_limit']} functions; formula length at most "
                   f"{s['length_limit']} characters; numbers allowed in formulas: "
                   f"{', '.join(_fmt(x) for x in s['allowed_literals'])}; check tolerance {_fmt(s['check_tolerance'])}")
    if man is not None:
        out.append(f"- Built: {man.get('built_at', '?')} by rule-of-thumb-model {man.get('plugin_version', '?')}"
                   + (f"; updated {man['updated_at']}" if man.get("updated_at") else ""))
        if man.get("spec_sha256") and spec is not None and man["spec_sha256"] != spec.sha256:
            out.append("- The workbook was built from another version of the specification (the hashes differ); "
                       "rebuild it with rotm.py build.")
            report.warning("M-STALE", "the workbook was built from another version of the specification",
                           "rebuild with rotm.py build")
    if info is not None:
        out.append("- Values: " + ("the values Excel saved when it last recalculated the workbook"
                                   if info.has_cached_values else
                                   "not recalculated: the workbook holds no values saved by Excel, so values of "
                                   "formula cells are not shown (run rotm.py check)"))
    lift = list((root / "lift").glob("*_lift.py")) if root is not None and (root / "lift").is_dir() else []
    if lift:
        out.append(f"- Python version of the model: `lift/{lift[0].name}` (rotm.py lift)")
    out.append(f"- Written by rule-of-thumb-model {common.VERSION} (rotm.py document) on {report.at}.")
    return out + [""]


def _status_section(root: Path | None, wb: Path | None) -> list[str]:
    from . import modelfolder
    if root is None or wb is None:
        return []
    try:
        status = modelfolder.read_status(root)
    except Exception:  # noqa: BLE001 - an unreadable status file is left out of the map
        return []
    steps = (status.get("workbooks") or {}).get(wb.name) or {}
    if not steps:
        return []
    rows = []
    for step, e in steps.items():
        detail = []
        for k in ("errors", "warnings", "checks", "failed", "master", "error_cells"):
            if k in e:
                detail.append(f"{k} {e[k]}")
        if "recalculated" in e:
            detail.append("recalculated" if e["recalculated"] else "not recalculated")
        rows.append([step, e.get("at", ""), e.get("exit_code", ""), ", ".join(detail)])
    return ["## Last runs", ""] + _table(["Step", "At", "Exit code", "Result"], rows) + [""]


def _sheets_section(spec: Any, man: dict | None, info: Any) -> list[str]:
    rows = []
    entries = man.get("sheets", []) if man else [{"name": s.name, "type": s.type, "font_size": s.font_size}
                                                  for s in spec.sheets]
    spec_sheets = {s.name: s for s in spec.sheets} if spec else {}
    for k, e in enumerate(entries, start=1):
        s = info.sheet(e["name"]) if info else None
        size = f"{s.max_row:,} rows x {s.max_col:,} columns" if s is not None and s.kind == "worksheet" else ""
        ss = spec_sheets.get(e["name"])
        purpose = (ss.data.get("purpose") or ss.data.get("title")) if ss is not None else None
        rows.append([k, e["name"], e.get("type", ""), e.get("font_size", ""), size,
                     purpose or SHEET_ROLES.get(e.get("type", ""), "")])
    if info is not None:
        known = {e["name"] for e in entries}
        for s in info.sheets:
            if s.name not in known:
                rows.append(["", s.name, "not in the manifest", "", f"{s.max_row:,} rows x {s.max_col:,} columns",
                             "a sheet the build did not write"])
    return ["## Sheets", ""] + _table(["#", "Sheet", "Type", "Font size", "Used range", "Purpose"], rows) + [""]


def _raw_section(root: Path | None, spec: Any, man: dict | None) -> list[str]:
    from . import manifest as manmod, modelfolder
    tables = list(spec.raw) if spec else (manmod.raw_tables(man) if man else [])
    if root is not None:
        for t in modelfolder.tables(root):
            if t not in tables:
                tables.append(t)
    if not tables:
        return []
    out = ["## Raw tables and column contracts", ""]
    for t in tables:
        entry = manmod.raw_sheet(man, t) if man else None
        state, con, why = modelfolder.read_contract(root, t) if root is not None else ("missing", None, [])
        out.append(f"### {t}")
        out.append("")
        out.append(f"- Raw sheet: `{(entry or {}).get('name', f'raw_{t}')}`"
                   + (f", Excel table `{entry['excel_table']}`" if entry and entry.get("excel_table") else "")
                   + (f", landing {entry['landing']}" if entry and entry.get("landing") else ""))
        if entry:
            out.append(f"- Landed rows: {_fmt(entry.get('rows'))}; snapshot `{entry.get('snapshot') or ''}`; digest "
                       f"`{entry.get('digest') or 'not recorded'}`")
        out.append(f"- Contract: `contracts/{t}.contract.json`, state {state}"
                   + (f" ({'; '.join(why[:2])})" if why else ""))
        if con:
            src = con.get("source") or {}
            if src:
                out.append("- Source: " + ", ".join(f"{k} {v}" for k, v in src.items() if v))
            out.append(f"- Grain: {con['grain']}; key: {', '.join(con['key'])}")
            rows = (con.get("expect") or {}).get("rows") or {}
            if rows:
                out.append("- Expected rows: " + (f"exactly {rows['exact']:,}" if "exact" in rows else
                                                  f"{rows.get('min', 0):,} to {rows.get('max', 'any'):,}"
                                                  if isinstance(rows.get("max"), int) else
                                                  f"at least {rows.get('min', 0):,}"))
            for tot in (con.get("expect") or {}).get("totals", []):
                out.append(f"- Control total: sum of {tot['column']} = {_fmt(tot['value'])} (tolerance "
                           f"{_fmt(tot.get('tolerance', 0))})")
            out.append("")
            out += _table(["Column", "Type", "Unit", "Null rule", "Allowed values", "Source expression", "Description"],
                          [[c["name"], c["type"], c.get("unit") or "", c["null_rule"],
                            ", ".join(str(v) for v in c.get("allowed") or []), c.get("source_expression") or "",
                            c.get("description") or ""] for c in con["columns"]])
            ag = con.get("agreement") or {}
            out.append("")
            if ag.get("status") == "agreed":
                out.append(f"- Agreement: agreed by {ag.get('by')} on {ag.get('at')} through {ag.get('channel')}"
                           + (f" (file {ag['source_file']})" if ag.get("source_file") else "")
                           + (f"; reply: \"{_md(ag.get('reply'))}\"" if ag.get("reply") else ""))
                if state == "stale":
                    out.append("- The contract was changed after that agreement, so it counts as not agreed.")
            else:
                out.append("- Agreement: proposed, not agreed. Show the user the contract and record their reply "
                           "with rotm.py contract agree.")
        out.append("")
    return out


def _input_value_text(item: dict[str, Any], kind: str) -> str:
    if kind == "scalar":
        return _fmt(item.get("value"))
    if kind == "list":
        vals = [_fmt(v) for v in item.get("values", [])]
        more = f" and {len(vals) - LIST_SHOWN} more" if len(vals) > LIST_SHOWN else ""
        return ", ".join(vals[:LIST_SHOWN]) + more
    nr, nc = len(item["rows"]["values"]), len(item["cols"]["values"])
    return (f"grid of {nr} rows x {nc} columns; rows {', '.join(_fmt(v) for v in item['rows']['values'][:6])}; "
            f"columns {', '.join(_fmt(v) for v in item['cols']['values'][:6])}")


def _inputs_section(spec: Any, man: dict | None, info: Any) -> list[str]:
    cells = (man or {}).get("inputs") or {}
    rows = []
    if spec is not None:
        for iid, e in spec.inputs.items():
            item, kind = e["item"], e["kind"]
            ref = cells.get(iid, "")
            if e.get("generated"):
                value = _fmt(_cell_value(info, ref)) or _fmt(spec.settings.get(iid))
                rows.append([iid, "added by build", value, "", ref, "dictionary rule"])
                continue
            text = _input_value_text(item, kind)
            if kind == "scalar" and info is not None and ref:
                held = _cell_value(info, ref)
                if held is not None and _fmt(held) != text and not (isinstance(held, dt.date)
                                                                    and held.isoformat()[:10] == str(item["value"])):
                    text += f" (the workbook holds {_fmt(held)})"
            rows.append([iid, item["label"], text, item.get("unit") or "", ref,
                         item.get("source") or item.get("note") or ""])
    else:
        for iid, ref in cells.items():
            rows.append([iid, "", _fmt(_cell_value(info, ref)), "", ref, ""])
    if not rows:
        return []
    return ["## Inputs", ""] + _table(["Id", "Label", "Value", "Unit", "Cell", "Source"], rows) + [""]


def _helpers_section(spec: Any, man: dict | None, info: Any) -> list[str]:
    entries = [s for s in (man or {}).get("sheets", []) if s.get("type") == "helpers"]
    if not entries and spec is None:
        return []
    out = ["## Helper columns", ""]
    exprs: dict[tuple[str, str], dict[str, Any]] = {}
    if spec is not None:
        for table, sh in spec.helpers.items():
            for c in sh.data.get("columns", []):
                exprs[(table, c["id"])] = c
    if entries:
        for e in entries:
            first, last = e.get("first_row", 2), e.get("last_row")
            out.append(f"### {e['name']} (raw table {e['table']})")
            out.append("")
            out.append(f"- Rows {first:,} to {_fmt(last)}: one helper row per raw row; capacity {_fmt(e.get('capacity'))} "
                       "rows. A raw row below the last helper row is not covered (check "
                       f"{e['table']}_cover); rotm.py extend copies the last row down.")
            out.append("")
            rows = []
            for c in e.get("columns", []):
                sc = exprs.get((e["table"], c["id"]), {})
                rows.append([c["col"], c["id"], c.get("header") or sc.get("label") or "", c["kind"],
                             f"`{c['formula']}`", f"`{sc['expr']}`" if sc.get("expr") else "", sc.get("unit") or ""])
            out += _table(["Column", "Id", "Header", "Kind", "Formula (first row)", "Specification", "Unit"], rows)
            out.append("")
    else:
        for (table, cid), c in exprs.items():
            out.append(f"- h_{table}.{cid}: `{c['expr']}`")
        out.append("")
    return out


def _blocks_section(spec: Any, man: dict | None, info: Any) -> list[str]:
    from . import common
    entries: dict[str, dict[str, Any]] = {}
    for s in (man or {}).get("sheets", []):
        if s.get("type") in ("calc", "output"):
            for b in s.get("blocks", []):
                entries[b["id"]] = dict(b, sheet=s["name"])
    if spec is None and not entries:
        return []
    out = ["## Calculation blocks", ""]
    rows = []
    ids = list(spec.blocks) if spec is not None else list(entries)
    for bid in ids:
        sb = spec.blocks.get(bid) if spec is not None else None
        me = entries.get(bid, {})
        data = sb["data"] if sb else {}
        sheet = me.get("sheet") or (sb["sheet"] if sb else "")
        body = me.get("body", "")
        first_formula = ""
        if body and info is not None:
            try:
                c1, r1, _c2, _r2 = common.range_bounds(body)
                first_formula = _cell_formula(info, sheet, common.a1(c1, r1)) or ""
            except ValueError:
                first_formula = ""
        totals = data.get("total") or {}
        total_text = ", ".join(k for k in ("row", "col") if totals.get(k))
        if data.get("measures"):
            total_text = ", ".join(f"{m['id']} {m['total']}" for m in data["measures"] if m.get("total"))
        rows.append([f"{sheet}!{body}" if body else sheet, bid, data.get("title") or "",
                     (sb["shape"] if sb else me.get("shape", "")),
                     _dim_text(spec, data, "rows") if data.get("rows") else
                     (f"{len(data.get('items', []))} line items" if data.get("items") else ""),
                     _dim_text(spec, data, "cols") if "measures" not in data else
                     f"{len(data['measures'])} measures", total_text,
                     _expr_text(data) if data else "", f"`{first_formula}`" if first_formula else "",
                     ", ".join(me.get("checks") or (spec.block_checks.get(bid, []) if spec else []))])
    out += _table(["Range", "Block", "Title", "Shape", "Rows", "Columns", "Totals", "Specification",
                   "Formula (first cell)", "Checks"], rows)
    return out + [""]


def _checks_section(spec: Any, man: dict | None, info: Any) -> list[str]:
    entries = (man or {}).get("checks") or []
    out = ["## Checks", "",
           "A check result is 0 when it passes and 1 when it fails (shown OK and FAIL). The master check fails when "
           "any error check fails or a result is missing; alerts are shown but not part of the master check.", ""]
    rows = []
    seen = set()
    labels = {cid: e["data"] for cid, e in (spec.checks.items() if spec else [])}
    for c in entries:
        seen.add(c["id"])
        spec_chk = labels.get(c["id"], {})
        value = _cell_value(info, c.get("result"))
        shown = "" if value is None else ("OK" if value == 0 else "FAIL" if value == 1 else _fmt(value))
        label = c.get("label") or spec_chk.get("label") or _label_cell(info, c.get("result")) \
            or _generated_label(c["id"], spec)
        rows.append([c["id"], c.get("kind") or spec_chk.get("kind", ""), label,
                     c.get("severity") or spec_chk.get("severity", "error"), c.get("result", ""),
                     shown, "added by build" if c.get("auto") else "specification"])
    for cid, chk in labels.items():
        if cid not in seen:
            rows.append([cid, chk["kind"], chk["label"], chk.get("severity", "error"), "", "",
                         "specification (not in the manifest)"])
    if not rows:
        return []
    out += _table(["Id", "Kind", "What it checks", "Severity", "Result cell", "Saved result", "From"], rows)
    if man and man.get("master_check"):
        value = _cell_value(info, man["master_check"])
        shown = "" if value is None else ("OK" if value == 0 else "FAIL" if value == 1 else _fmt(value))
        out += ["", f"- Master check: `{man['master_check']}`" + (f", saved result {shown}" if shown else "")
                + (f"; linked in {', '.join(man.get('master_links') or [])}" if man.get("master_links") else "")]
    return out + [""]


def _label_cell(info: Any, result_ref: str | None) -> str:
    """The text in column C of a check's row (design 9.1: C is 'what it checks')."""
    from . import common
    if info is None or not result_ref:
        return ""
    sheet, cell = common.parse_ref(result_ref)
    try:
        _c, row = common.split_cell(cell)
    except ValueError:
        return ""
    v = _cell_value(info, f"{common.quote_sheet(sheet)}!C{row}") if sheet else None
    return v if isinstance(v, str) else ""


def _generated_label(cid: str, spec: Any) -> str:
    """What a check build adds for a raw table checks (design 9.2), from its id."""
    tables = sorted(spec.raw, key=len, reverse=True) if spec is not None else []
    for t in tables:
        if not cid.startswith(t + "_"):
            continue
        rest = cid[len(t) + 1:]
        if rest == "rows":
            return f"raw table {t}: row count against the contract"
        if rest.startswith("total_"):
            return f"raw table {t}: control total of {rest[6:]} against the contract"
        if rest.startswith("nulls_"):
            return f"raw table {t}: empty cells in {rest[6:]} (never null in the contract)"
        if rest == "cover":
            return f"raw table {t}: raw rows the helper sheet h_{t} does not cover"
        if rest == "keys":
            return f"raw table {t}: rows that repeat an earlier key (at most the contract's known repeats)"
    return ""


def _outputs_section(spec: Any, man: dict | None, info: Any) -> list[str]:
    cells = (man or {}).get("cells") or {}
    keys = {k: v for k, v in (spec.key_outputs.items() if spec else []) if not v.get("ambiguous")}
    rows = []
    for oid in sorted(set(cells) | set(keys)):
        k = keys.get(oid, {})
        rows.append([oid, cells.get(oid, ""), k.get("block", ""), _fmt(_cell_value(info, cells.get(oid)))])
    if not rows:
        return []
    return ["## Key outputs", ""] + _table(["Id", "Cell", "Block", "Saved value"], rows) + [""]


def _style_section(spec: Any, man: dict | None) -> list[str]:
    out = ["## Style", ""]
    applied = (man or {}).get("dictionary_applied") or []
    d = spec.dictionary if spec is not None else None
    if d is not None:
        name = getattr(d, "data", {}).get("name") if hasattr(d, "data") else None
        out.append(f"- Style dictionary: {name or 'the plugin dictionary'} (references/style_dictionary.json)")
    out.append("- Named overrides applied: " + (", ".join(applied) if applied else "none"))
    if spec is not None and spec.data.get("dictionary"):
        out.append(f"- Project override file: `{spec.data['dictionary']}`")
    out.append("- Font sizes per sheet: see Sheets (8 on dense sheets, 9 on sheets with white space; Max, spec 1.2).")
    return out + [""]


def _notes_section(root: Path | None, spec: Any) -> list[str]:
    if spec is None:
        return []
    out = []
    lim = spec.data.get("limitations") or []
    if lim:
        out += ["## Limitations", ""] + [f"- {_md(x)}" for x in lim] + [""]
    notes = [n for s in spec.sheets if s.type == "notes" for n in s.data.get("notes", [])]
    if notes:
        out += ["## Notes", ""] + [f"- {_md(n['text'])}" + (f" (source: {_md(n['source'])})" if n.get("source") else "")
                                   for n in notes] + [""]
    return out


def _inferred(info: Any) -> list[str]:
    """The map of a workbook this plugin did not build: what can be read without a manifest."""
    from . import common, formula
    out = ["This workbook has no manifest (rotm.py build writes one), so the map below is inferred from the "
           "workbook alone: sheet roles are guessed from names and query tables, and helper-like columns are "
           f"columns with the same formula in {INFERRED_MIN_RUN} or more rows.", ""]
    rows = []
    helper_rows = []
    for s in info.sheets:
        if s.kind != "worksheet":
            rows.append([s.name, "chart sheet", "", "", "", ""])
            continue
        formulas = list(s.formulas())
        constants = sum(1 for _ in s.constants())
        role = ("raw (inferred: name or query table)" if RAW_NAME_RE.search(s.name) or any(t.from_query for t in s.tables)
                else "checks (inferred: name)" if s.name.lower() in ("check", "checks") else
                "calculations" if formulas else "values only")
        rows.append([s.name, role, f"{s.max_row:,} x {s.max_col:,}", f"{len(formulas):,}", f"{constants:,}",
                     ", ".join(f"{t.name} {t.ref}" + (" (query)" if t.from_query else "") for t in s.tables)])
        by_col: dict[int, list[Any]] = {}
        for c in formulas:
            if c.formula:
                by_col.setdefault(c.col, []).append(c)
        for col, cells in sorted(by_col.items()):
            cells.sort(key=lambda c: c.row)
            run = [cells[0]]
            form0 = _rel(formula, cells[0])
            for c in cells[1:]:
                if c.row == run[-1].row + 1 and _rel(formula, c) == form0:
                    run.append(c)
                    continue
                if len(run) >= INFERRED_MIN_RUN:
                    helper_rows.append(_helper_row(common, s, run))
                run, form0 = [c], _rel(formula, c)
            if len(run) >= INFERRED_MIN_RUN:
                helper_rows.append(_helper_row(common, s, run))
    out += ["## Sheets (inferred)", ""] + _table(["Sheet", "Role", "Used range", "Formulas", "Constants", "Tables"],
                                                 rows) + [""]
    if helper_rows:
        out += ["## Helper-like columns (inferred)", ""]
        out += _table(["Sheet", "Column", "Rows", "Header", "Formula (first row)"], helper_rows[:400])
        if len(helper_rows) > 400:
            out.append(f"| ... | {len(helper_rows) - 400} more columns | | | |")
        out.append("")
    names = [(n, v, sc) for n, v, sc in info.defined_names if not n.startswith("_xlfn.")]
    if names:
        out += ["## Defined names", ""] + _table(["Name", "Refers to", "Scope"],
                                                 [[n, v, sc or "workbook"] for n, v, sc in names[:200]]) + [""]
    pkg = info.package
    out += ["## Parts", "", f"- Power Query queries: {pkg.queries}" + (f" ({', '.join(pkg.query_names)})"
                                                                       if pkg.query_names else ""),
            f"- Charts: {pkg.charts}; pivot tables: {pkg.pivots}; links to other workbooks: {pkg.external_links}",
            f"- Calculation mode: {info.calc_mode}" + ("; iterative calculation on" if info.iterate else ""), ""]
    return out


def _rel(formula_mod: Any, cell: Any) -> str:
    try:
        return formula_mod.relative_form(cell.formula, cell.coord)
    except Exception:  # noqa: BLE001
        return cell.formula


def _helper_row(common: Any, sheet: Any, run: list[Any]) -> list[Any]:
    letter = common.col_letter(run[0].col)
    head = sheet.cells.get((1, run[0].col))
    header = head.value if head is not None and not head.is_formula else ""
    return [sheet.name, letter, f"{run[0].row:,} to {run[-1].row:,}", header or "", f"`{run[0].formula}`"]


def add_document_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("target", help="the model folder, or a workbook")
    p.add_argument("-o", "--out", help=f"output file (default <model>/{DEFAULT_NAME}; for a workbook outside a "
                                       f"model folder <stem>_{DEFAULT_NAME} next to it)")
    p.add_argument("--json", metavar="FILE", help="write the report as JSON ('-' = stdout)")


def run_document(args: argparse.Namespace) -> int:
    report = document_model(args.target, out=args.out)
    return report.emit(args.json)

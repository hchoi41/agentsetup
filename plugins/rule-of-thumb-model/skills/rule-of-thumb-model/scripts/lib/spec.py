"""Load and validate a model specification (design section 6).

Owner: CORE-IO. The loader checks the schema (lib.schemas "spec") and every cross reference that needs no
layout: ids unique, referenced ids exist, placeholders allowed where they are written, shapes that {g:},
{grow:}, {gcol:} and {b:} need, number-format keys, font sizes 8 or 9, raw and helper sheets per table, and
the contracts (agreed before build). Every problem is reported at once, each with its JSON path and what to
change, e.g. "sheets[4].blocks[0].cell: unknown block 'actuals' in {g:actuals}; did you mean 'actual'?".
Turning placeholders into A1 references needs the layout and belongs to lib.build (M3).
"""
from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SHEET_TYPES = ("cover", "inputs", "raw", "helpers", "calc", "output", "checks", "notes")   # also the build order
DEFAULT_FONT_SIZE = {"cover": 9, "inputs": 9, "raw": 8, "helpers": 8, "calc": 8, "output": 9, "checks": 9, "notes": 9}
ALLOWED_FONT_SIZES = (8, 9)                    # Max, spec 1.2
BLOCK_SHAPES = ("grid", "items", "measures")
PLACEHOLDER_KINDS = ("in", "raw", "h", "this", "row", "col", "r", "prev", "m", "g", "grow", "gcol", "b", "blk", "cell", "chk")
PLACEHOLDER_RE = re.compile(r"\{([a-z]+)(?::([^{}]*))?\}")
SETTING_KEYS = ("depth_limit", "length_limit", "allowed_literals", "check_tolerance", "helper_spare_rows",
                "formula_key_check_max_rows", "allow_functions")
# Used when no dictionary is given; equal to the plugin dictionary's rules (tested).
DEFAULT_SETTINGS: dict[str, Any] = {"depth_limit": 2, "length_limit": 100, "allowed_literals": [0, 1],
                                    "check_tolerance": 0.001, "helper_spare_rows": 0,
                                    "formula_key_check_max_rows": 5000, "allow_functions": []}
GENERATED_SHEETS = ("cover", "inputs", "checks")          # build writes these even when the spec omits them
GENERATED_INPUTS = ("check_tolerance",)                   # inputs build adds (design 6.4)
HELPER_FIXED_COLUMNS = ("row", "in_raw", "dup_key", "dup")   # helper columns build adds (design 7.1)
# Null flags (v2, A-aera-6; v3, A-aera-7): for each fetched raw column of these types whose null rule is
# "allowed", or whose contract agrees known nulls for it (columns.<c>.known_nulls), build adds the helper column
# <column>_null (kind nullflag), 1 where the raw cell is empty. An empty raw cell is fetched as 0, so COUNT and
# AVERAGE over such a column use the flag: COUNTIFS(<flag>,0), AVERAGEIFS(<value>,<flag>,0).
NULL_FLAG_TYPES = ("number", "integer", "date", "datetime")
NULL_FLAG_SUFFIX = "_null"
GENERATED_CHECK_SUFFIXES = ("rows", "cover", "beyond", "keys")     # checks build adds per raw table
EXAMPLE_ROOTS_ENV = "ROTM_EXAMPLE_ROOTS"                  # extra folders where channel "example" is accepted
SPEC_FILE = "model.spec.json"

# Where each placeholder kind may be written (design 6.6). Contexts: helpers, grid, items, measures, check.
ALLOWED_IN: dict[str, tuple[str, ...]] = {
    "in": ("helpers", "grid", "items", "measures", "check"),
    "raw": ("helpers", "grid", "items", "measures", "check"),
    "h": ("grid", "items", "measures", "check"),
    "this": ("helpers",),
    "row": ("grid", "measures"),
    "col": ("grid", "items"),
    "r": ("items",),
    "prev": ("items",),
    "m": ("measures",),
    "g": ("grid",),
    "grow": ("grid", "measures"),
    "gcol": ("grid", "items"),
    "b": ("items", "grid"),
    "blk": ("helpers", "grid", "items", "measures", "check"),
    "cell": ("helpers", "grid", "items", "measures", "check"),
    "chk": ("grid", "items", "measures", "check"),
}
_CONTEXT_WORDS = {"helpers": "a helper column", "grid": "a grid block", "items": "an items block",
                  "measures": "a measures block", "check": "a check"}
_CANDIDATE_RE = re.compile(r"\{([A-Za-z_]+)(?::([^{}]*))?\}")


@dataclass(frozen=True)
class Placeholder:
    kind: str          # one of PLACEHOLDER_KINDS (as written; the loader refuses other kinds)
    arg: str | None    # text after the colon, e.g. "sales.amount"
    start: int         # offset of "{" in the expression
    end: int           # offset just after "}"
    text: str          # the placeholder as written


@dataclass
class SheetSpec:
    name: str
    type: str
    font_size: int
    data: dict[str, Any]
    path: str = ""                 # JSON path in the spec, e.g. "sheets[3]"; "" for a generated sheet
    generated: bool = False        # True when build adds the sheet (cover, inputs, checks absent from the spec)


@dataclass
class Spec:
    path: Path                     # the spec file
    root: Path                     # the model folder (the spec's folder)
    data: dict[str, Any]           # the parsed JSON
    name: str
    title: str
    output: Path                   # absolute path of the workbook to write
    product: str                   # both | excel | sheets
    settings: dict[str, Any]       # resolved settings (resolve_settings)
    sheets: list[SheetSpec]        # in build order (SHEET_TYPES order, spec order within a type)
    contracts: dict[str, dict[str, Any]] = field(default_factory=dict)   # table -> contract
    dictionary: Any = None         # lib.dictionary.Dictionary
    sha256: str = ""               # sha256 of the spec file's bytes
    contract_states: dict[str, str] = field(default_factory=dict)       # table -> state (modelfolder)
    inputs: dict[str, dict[str, Any]] = field(default_factory=dict)     # id -> {"kind", "item", "sheet", "path"}
    blocks: dict[str, dict[str, Any]] = field(default_factory=dict)     # id -> {"shape", "data", "sheet", "path"}
    checks: dict[str, dict[str, Any]] = field(default_factory=dict)     # id -> {"data", "sheet", "path"}
    key_outputs: dict[str, dict[str, Any]] = field(default_factory=dict)  # id or block.id -> {"block", "id", "kind"}
    raw: dict[str, SheetSpec] = field(default_factory=dict)             # table -> raw sheet
    helpers: dict[str, SheetSpec] = field(default_factory=dict)         # table -> helper sheet
    helper_columns: dict[str, list[str]] = field(default_factory=dict)  # table -> every helper column name
    block_checks: dict[str, list[str]] = field(default_factory=dict)    # block id -> check ids naming it
    notes: list[str] = field(default_factory=list)                      # what the loader could not check

    def sheet(self, name: str) -> SheetSpec | None:
        for s in self.sheets:
            if s.name == name:
                return s
        return None

    def sheets_of(self, sheet_type: str) -> list[SheetSpec]:
        return [s for s in self.sheets if s.type == sheet_type]

    def expressions(self) -> list[tuple[str, str, str, dict[str, Any]]]:
        """Every expression as (JSON path, expression, context, info); see _iter_expressions."""
        return list(_iter_expressions(self.data))


# ----------------------------------------------------------------------------- placeholders

def string_spans(expr: str) -> list[tuple[int, int]]:
    """(start, end) of every double-quoted text literal ("" inside text is an escaped quote)."""
    spans, start = [], None
    for i, ch in enumerate(expr):
        if ch == '"':
            if start is None:
                start = i
            else:
                spans.append((start, i + 1))
                start = None
    if start is not None:
        spans.append((start, len(expr)))
    return spans


def placeholders(expr: str) -> list[Placeholder]:
    """Every {kind:arg} placeholder of a spec expression, in order. Braces inside double-quoted text and array
    constants such as {1,2;3,4} or {TRUE} are not placeholders."""
    spans = string_spans(expr)
    out = []
    for m in _CANDIDATE_RE.finditer(expr):
        if any(a <= m.start() < b for a, b in spans):
            continue
        kind = m.group(1)
        if kind.upper() in ("TRUE", "FALSE") and m.group(2) is None:
            continue
        out.append(Placeholder(kind, m.group(2), m.start(), m.end(), m.group(0)))
    return out


# ----------------------------------------------------------------------------- settings

def resolve_settings(spec_settings: dict[str, Any] | None, dictionary: Any,
                     cli: dict[str, Any] | None = None) -> dict[str, Any]:
    """Settings with precedence CLI > spec settings > project override > plugin dictionary rules (design 6.1).
    (The project override is already merged into `dictionary`.) None values in `cli` are ignored.
    Raises common.Refusal naming the key and its layer when a value is not allowed."""
    from . import common, schemas
    out = copy.deepcopy(DEFAULT_SETTINGS)
    if dictionary is not None:
        for key in SETTING_KEYS:
            value = dictionary.rule(key, None)
            if value is not None:
                out[key] = value
    for layer, values in (("settings", spec_settings), ("command line", cli)):
        for key, value in (values or {}).items():
            if value is None:
                continue
            if key not in SETTING_KEYS:
                near = schemas.nearest(key, SETTING_KEYS)
                raise common.Refusal(f"{layer}: unknown setting '{key}'" + (f"; did you mean '{near}'?" if near else ""),
                                     fix="use one of: " + ", ".join(SETTING_KEYS))
            out[key] = copy.deepcopy(value)
    errs: list[Any] = []
    schemas.SETTINGS.check(out, "settings", errs)
    if errs:
        raise common.Refusal("settings are not valid", details=[e.line() for e in errs],
                             fix="change the setting named below (spec \"settings\", the command line, or the "
                                 "dictionary's rules)")
    out["allow_functions"] = sorted({str(f).upper() for f in out["allow_functions"]})
    return out


# ----------------------------------------------------------------------------- expressions

def _iter_expressions(data: dict[str, Any]):
    """(path, expression, context, info) for every expression of a spec that passed the schema.
    info: sheet, table (helpers), block and shape (blocks), item / measure / column / check ids."""
    for si, sheet in enumerate(data.get("sheets", [])):
        st, sp = sheet.get("type"), f"sheets[{si}]"
        if st == "helpers":
            for ci, col in enumerate(sheet.get("columns", [])):
                yield (f"{sp}.columns[{ci}].expr", col["expr"], "helpers",
                       {"sheet": sheet["name"], "table": sheet["table"], "column": col["id"], "index": ci})
        elif st in ("calc", "output"):
            for bi, block in enumerate(sheet.get("blocks", [])):
                bp = f"{sp}.blocks[{bi}]"
                base = {"sheet": sheet["name"], "block": block["id"], "block_data": block}
                if "cell" in block:
                    yield f"{bp}.cell", block["cell"], "grid", dict(base, shape="grid")
                elif "items" in block:
                    for ii, item in enumerate(block["items"]):
                        info = dict(base, shape="items", item=item["id"], has_first="first" in item)
                        yield f"{bp}.items[{ii}].expr", item["expr"], "items", info
                        if "first" in item:
                            yield f"{bp}.items[{ii}].first", item["first"], "items", dict(info, is_first=True)
                elif "measures" in block:
                    for mi, meas in enumerate(block["measures"]):
                        yield (f"{bp}.measures[{mi}].expr", meas["expr"], "measures",
                               dict(base, shape="measures", measure=meas["id"]))
        elif st == "checks":
            for ci, chk in enumerate(sheet.get("checks", [])):
                for key in ("actual", "expected", "min", "max", "bound"):
                    if key in chk and isinstance(chk[key], str):
                        yield (f"{sp}.checks[{ci}].{key}", chk[key], "check",
                               {"sheet": sheet["name"], "check": chk["id"]})


class _Problems:
    def __init__(self) -> None:
        self.items: list[Any] = []

    def add(self, path: str, message: str, fix: str = "") -> None:
        from .schemas import SchemaError
        self.items.append(SchemaError(path, message, fix))

    def __bool__(self) -> bool:
        return bool(self.items)


def _did_you_mean(name: str, valid: Any) -> str:
    from .schemas import nearest
    near = nearest(name, list(valid))
    return f"; did you mean '{near}'?" if near else ""


# ----------------------------------------------------------------------------- loader

def _example_roots() -> list[Path]:
    from . import common
    roots = [common.skill_dir()]
    plugin = common.plugin_dir()
    if plugin is not None:
        roots.append(plugin)
    for part in os.environ.get(EXAMPLE_ROOTS_ENV, "").split(os.pathsep):
        if part.strip():
            roots.append(common.native_path(part.strip()))
    return roots


def example_channel_allowed(root: str | Path) -> bool:
    """Channel "example" counts as agreed only for model folders inside SKILL or PLUGIN (shipped examples and
    templates), or inside a folder listed in ROTM_EXAMPLE_ROOTS (selftest copies; design 5)."""
    from . import common
    return any(common.is_within(root, r) for r in _example_roots())


def load_spec(path: str | Path, *, require_agreed: bool = True,
              cli_settings: dict[str, Any] | None = None) -> Spec:
    """Parse, validate and resolve a spec; raises common.Refusal listing every problem with its JSON path.
    `path` may be the spec file or its model folder. With `require_agreed`, every raw table's contract must be
    agreed (channel "example" only inside SKILL or PLUGIN, design 5)."""
    from . import common, dictionary as dictmod, schemas
    p = common.native_path(path)
    if p.is_dir():
        p = p / SPEC_FILE
    p = Path(os.path.abspath(p))
    data = common.load_json_file(p, "model specification")
    schemas.require_valid(data, "spec", p.name)
    root = p.parent
    dictionary = dictmod.load_for_model(root, spec_override=data.get("dictionary"))
    settings = resolve_settings(data.get("settings"), dictionary, cli_settings)
    spec = Spec(path=p, root=root, data=data, name=data["name"], title=data["title"],
                output=root / data.get("output", "model.xlsx"), product=data.get("product", "both"),
                settings=settings, sheets=[], dictionary=dictionary, sha256=common.file_sha256(p))
    problems = _Problems()
    _index_sheets(spec, problems)
    _load_contracts(spec, problems, require_agreed)
    _index_ids(spec, problems)
    _check_input_values(spec, problems)
    _check_dimensions(spec, problems)
    _check_formats(spec, problems)
    _check_expressions(spec, problems)
    _check_cycles(spec, problems)
    if problems:
        n = len(problems.items)
        raise common.Refusal(f"{p.name} has {n} problem{'s' if n != 1 else ''}; nothing was built",
                             details=[e.line() for e in problems.items],
                             fix="change the keys named below")
    return spec


def _index_sheets(spec: Spec, problems: _Problems) -> None:
    data, d = spec.data, spec.dictionary
    seen: dict[str, str] = {}
    by_type: dict[str, list[SheetSpec]] = {t: [] for t in SHEET_TYPES}
    for si, sheet in enumerate(data["sheets"]):
        path, name, st = f"sheets[{si}]", sheet["name"], sheet["type"]
        if name in seen:
            problems.add(f"{path}.name", f"sheet name '{name}' is already used by {seen[name]}",
                         "give every sheet a different name")
            continue
        seen[name] = path
        if (name.startswith("raw_") and st != "raw") or (name.startswith("h_") and st != "helpers"):
            prefix = "raw_" if name.startswith("raw_") else "h_"
            problems.add(f"{path}.name", f"sheet names starting with '{prefix}' are kept for "
                         f"{'raw' if prefix == 'raw_' else 'helper'} sheets; this is a {st} sheet",
                         "choose a name without that prefix")
        size = sheet.get("font_size") or (d.sheet_size(st) if d is not None else DEFAULT_FONT_SIZE[st])
        by_type[st].append(SheetSpec(name, st, int(size), sheet, path))
    for st in ("cover", "inputs", "checks", "notes"):
        if len(by_type[st]) > 1:
            extra = by_type[st][1]
            problems.add(f"{extra.path}.type", f"a second {st} sheet ('{extra.name}'); a model has at most one",
                         f"merge it into '{by_type[st][0].name}'")
    for st in GENERATED_SHEETS:
        if not by_type[st]:
            if st in seen:
                problems.add(f"{seen[st]}.name", f"sheet name '{st}' is needed for the {st} sheet that build adds",
                             f"rename this sheet, or give it \"type\": \"{st}\"")
                continue
            size = d.sheet_size(st) if d is not None else DEFAULT_FONT_SIZE[st]
            by_type[st].append(SheetSpec(st, st, int(size), {"name": st, "type": st}, "", generated=True))
    for st in ("raw", "helpers"):
        for s in by_type[st]:
            table = s.data["table"]
            target = spec.raw if st == "raw" else spec.helpers
            if table in target:
                problems.add(f"{s.path}.table", f"table '{table}' already has a {st} sheet ('{target[table].name}')",
                             f"keep one {st} sheet per table")
            else:
                target[table] = s
    for table, s in spec.helpers.items():
        if table not in spec.raw:
            problems.add(f"{s.path}.table", f"helper sheet '{s.name}' is for table '{table}', which has no raw sheet"
                         + _did_you_mean(table, spec.raw),
                         f"add {{\"name\": \"raw_{table}\", \"type\": \"raw\", \"table\": \"{table}\"}} to \"sheets\"")
    spec.sheets = [s for st in SHEET_TYPES for s in by_type[st]]


def _load_contracts(spec: Spec, problems: _Problems, require_agreed: bool) -> None:
    from . import modelfolder
    for table, sheet in spec.raw.items():
        state, data, why = modelfolder.read_contract(spec.root, table)
        spec.contract_states[table] = state
        cfile = f"contracts/{table}.contract.json"
        model = spec.root.as_posix()
        if state == "invalid":
            if require_agreed:
                problems.add(f"{sheet.path}.table", f"the contract of table '{table}' ({cfile}) is not valid: "
                             + "; ".join(why[:3]), "correct the contract, or propose it again with rotm.py contract "
                             f"propose \"{model}\" --table {table} --force")
            else:
                spec.notes.append(f"{cfile} is not valid; columns of '{table}' were not checked")
            continue
        if state == "missing":
            if require_agreed:
                problems.add(f"{sheet.path}.table", f"raw table '{table}' has no contract ({cfile})",
                             f"propose one with rotm.py contract propose \"{model}\" --table {table} --csv <sample>, "
                             "show it to the user and record their reply with rotm.py contract agree")
            else:
                spec.notes.append(f"no contract for '{table}'; its columns were not checked")
            continue
        spec.contracts[table] = data
        if not require_agreed:
            continue
        agreement = data.get("agreement") or {}
        agree_cmd = (f"show the user contracts/{table}.contract.md, then record their reply with rotm.py contract "
                     f"agree \"{model}\" --table {table} --reply \"<their words>\" --by <name>")
        if state == "proposed":
            problems.add(f"{sheet.path}.table", f"the contract of raw table '{table}' is proposed, not agreed",
                         agree_cmd)
        elif state == "stale":
            problems.add(f"{sheet.path}.table", f"the contract of raw table '{table}' was changed after "
                         f"{agreement.get('by')} agreed it on {agreement.get('at')}", agree_cmd)
        elif agreement.get("channel") == "example" and not example_channel_allowed(spec.root):
            problems.add(f"{sheet.path}.table", f"the contract of raw table '{table}' carries the 'example' "
                         "agreement of a shipped example or template; outside the plugin folder it counts as not "
                         "agreed", agree_cmd)


ID_SET_FIX = ("rename one of them: inputs, blocks and checks share one set of ids "
              "(references/model-spec-format.md, \"Ids\")")


def _index_ids(spec: Spec, problems: _Problems) -> None:
    """Inputs, blocks and checks share one set of ids, with the inputs and checks build adds (v5, J-1): a second
    owner of an id is refused at its JSON path, naming both paths. The second owner is still indexed under its own
    kind when that kind has no entry of the id yet, so no later step fails on it (no KeyError, exit 4) and no
    follow-on 'unknown block' or 'unknown input' line is printed. Key outputs ({cell:}), helper columns and sheet
    names are looked up in their own tables and may repeat an id of another kind."""
    owners: dict[str, tuple[str, str]] = {}

    def claim(id_: str, path: str, what: str) -> bool:
        if id_ in owners:
            kind_a, path_a = owners[id_]
            problems.add(path, f"id '{id_}' is used by {kind_a} at {path_a} and by {what} at {path}", ID_SET_FIX)
            return False
        owners[id_] = (what, path)
        return True

    for gid in GENERATED_INPUTS:
        owners[gid] = ("the input build adds on the inputs sheet", f"settings.{gid}")
        spec.inputs[gid] = {"kind": "scalar", "item": {"id": gid}, "sheet": "inputs", "path": "", "generated": True}
    generated_checks = _generated_check_ids(spec)
    for cid, table in generated_checks.items():
        raw_path = spec.raw[table].path if table in spec.raw else "sheets"
        owners[cid] = (f"the check build adds for raw table '{table}'", f"{raw_path}.table")
    for s in spec.sheets:
        if s.type == "inputs":
            for ii, item in enumerate(s.data.get("items", [])):
                path = f"{s.path}.items[{ii}]"
                if claim(item["id"], f"{path}.id", "an input") or item["id"] not in spec.inputs:
                    spec.inputs[item["id"]] = {"kind": item.get("kind", "scalar"), "item": item, "sheet": s.name,
                                               "path": path}
        elif s.type in ("calc", "output"):
            for bi, block in enumerate(s.data["blocks"]):
                path = f"{s.path}.blocks[{bi}]"
                shape = "grid" if "cell" in block else "items" if "items" in block else "measures"
                if claim(block["id"], f"{path}.id", "a block") or block["id"] not in spec.blocks:
                    spec.blocks[block["id"]] = {"shape": shape, "data": block, "sheet": s.name, "path": path,
                                                "sheet_type": s.type}
                inner = block.get("items") or block.get("measures") or []
                word = "item" if shape == "items" else "measure"
                seen: dict[str, int] = {}
                for k, entry in enumerate(inner):
                    ep = f"{path}.{'items' if shape == 'items' else 'measures'}[{k}].id"
                    if entry["id"] in seen:
                        problems.add(ep, f"{word} id '{entry['id']}' repeats {word} {seen[entry['id']]} of this block",
                                     f"give every {word} of a block a different id")
                    seen.setdefault(entry["id"], k)
                    if entry.get("key"):
                        _add_key(spec, problems, entry["id"], block["id"], shape, ep)
                if shape == "grid" and block.get("key"):
                    _add_key(spec, problems, block["id"], block["id"], "grid", f"{path}.key")
        elif s.type == "checks":
            for ci, chk in enumerate(s.data.get("checks", [])):
                path = f"{s.path}.checks[{ci}]"
                if chk["id"] in generated_checks:
                    problems.add(f"{path}.id", f"check id '{chk['id']}' is the id of a check build adds for raw table "
                                 f"'{generated_checks[chk['id']]}'", "choose another id")
                    continue
                if claim(chk["id"], f"{path}.id", "a check") or chk["id"] not in spec.checks:
                    spec.checks[chk["id"]] = {"data": chk, "sheet": s.name, "path": path}
    for table, sheet in spec.helpers.items():
        names = list(HELPER_FIXED_COLUMNS)
        contract = spec.contracts.get(table)
        raw_cols = [c["name"] for c in contract["columns"]] if contract else None
        fetch = sheet.data.get("fetch")
        if fetch is not None and raw_cols is not None:
            for fi, col in enumerate(fetch):
                if col not in raw_cols:
                    problems.add(f"{sheet.path}.fetch[{fi}]", f"'{col}' is not a column of the contract of '{table}'"
                                 + _did_you_mean(col, raw_cols), "use column names from the contract")
        fetched = list(fetch) if fetch is not None else (raw_cols or [])
        # v4, A-aera-12: a fetched contract column named like a helper column build adds would give the helper
        # sheet two columns of that name, and the generated checks would read the wrong one.
        for ci, name, helper in helper_name_clashes(contract, fetched):
            problems.add(f"contracts/{table}.contract.json columns[{ci}].name", *helper_name_problem(spec, table, name,
                                                                                                    helper))
        names += fetched
        flags = [fid for fid, _raw in null_flag_columns(contract, fetched)] if contract else []
        names += flags
        for ci, col in enumerate(sheet.data.get("columns", [])):
            cp = f"{sheet.path}.columns[{ci}].id"
            if col["id"] in names:
                why = "a helper column build adds" if col["id"] in HELPER_FIXED_COLUMNS else \
                    "the null flag build adds for a raw column that may be empty" if col["id"] in flags else \
                    "a fetched raw column" if col["id"] in fetched else "an earlier helper column"
                problems.add(cp, f"helper column id '{col['id']}' is already {why} of '{sheet.name}'",
                             "choose another id")
            names.append(col["id"])
        spec.helper_columns[table] = names
        if fetch is None and raw_cols is None:
            spec.notes.append(f"helper sheet '{sheet.name}': raw columns unknown (no contract); fetched column names "
                              "were not checked")


def _value_text(v: Any) -> str:
    """A value of a spec as a message shows it: numbers without a trailing .0, text in quotes."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return str(int(v))
    if isinstance(v, (int, float)):
        return repr(v)
    return f"'{v}'"


def _serial_of(v: Any) -> float | None:
    """The number Excel compares for a value: a number as it is; an ISO date (YYYY-MM-DD) as its date serial;
    None for anything else (text, true/false)."""
    import datetime as dt
    import math
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(float(v)) else None
    if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        try:
            return float((dt.date.fromisoformat(v) - dt.date(1899, 12, 30)).days)
        except ValueError:
            return None
    return None


def _in_list(v: Any, allowed: list[Any]) -> bool:
    for a in allowed:
        if v == a and isinstance(v, bool) == isinstance(a, bool):
            return True
        if str(v) == str(a):
            return True
    return False


def _check_input_values(spec: Spec, problems: _Problems) -> None:
    """v5 (round 4 verdict A-aera-1): a value of a scalar, list or grid input outside its own "valid" (min and max,
    or list) is refused at its JSON path; an empty cell of a grid (null) is not checked. A date input is compared by
    its date serial, as Excel's validation compares it."""
    for iid, info in spec.inputs.items():
        if info.get("generated") or not info.get("path"):
            continue
        item = info["item"]
        valid = item.get("valid")
        if not isinstance(valid, dict):
            continue
        label = item.get("label", iid)
        note = item.get("note")
        tail = f"; the input's note says: {note}" if note else ""
        kind = info["kind"]
        if kind == "list":
            cells = [(f"{info['path']}.values[{i}]", v) for i, v in enumerate(item.get("values") or [])]
        elif kind == "grid":
            cells = [(f"{info['path']}.values[{r}][{c}]", v) for r, row in enumerate(item.get("values") or [])
                     for c, v in enumerate(row or [])]
        else:
            cells = [(f"{info['path']}.value", item.get("value"))]
        lo, hi = valid.get("min"), valid.get("max")
        for path, v in cells:
            if v is None and kind == "grid":
                continue
            if "list" in valid:
                allowed = list(valid["list"])
                if not _in_list(v, allowed):
                    shown = ", ".join(_value_text(a) if not isinstance(a, str) else a for a in allowed)
                    problems.add(path, f"input '{iid}' ({label}) holds {_value_text(v)}, which is not one of its valid "
                                 f"values ({shown})", "write one of the valid values" + tail)
                continue
            x = _serial_of(v)
            if x is not None and (lo is None or x >= lo) and (hi is None or x <= hi):
                continue
            if lo is not None and hi is not None:
                rng, fix = f"{_value_text(lo)} to {_value_text(hi)}", f"write a value from {_value_text(lo)} to " \
                                                                    f"{_value_text(hi)}"
            elif lo is not None:
                rng, fix = f"of at least {_value_text(lo)}", f"write a value of at least {_value_text(lo)}"
            else:
                rng, fix = f"of at most {_value_text(hi)}", f"write a value of at most {_value_text(hi)}"
            problems.add(path, f"input '{iid}' ({label}) holds {_value_text(v)}, outside its valid range {rng}",
                         fix + tail)


def _add_key(spec: Spec, problems: _Problems, id_: str, block: str, kind: str, path: str) -> None:
    qualified = f"{block}.{id_}" if kind != "grid" else block
    spec.key_outputs[qualified] = {"block": block, "id": id_, "kind": kind, "path": path}
    if id_ in spec.key_outputs and spec.key_outputs[id_].get("block") != block:
        spec.key_outputs[id_] = {"ambiguous": True, "path": path}
    elif id_ not in spec.key_outputs:
        spec.key_outputs[id_] = {"block": block, "id": id_, "kind": kind, "path": path}


def may_be_empty(column: dict[str, Any]) -> bool:
    """True when the contract lets a cell of this column be empty: null rule "allowed", or null rule "never"
    with agreed known nulls (columns.<c>.known_nulls, v3 A-aera-7)."""
    return column.get("null_rule") == "allowed" or (
        column.get("null_rule") == "never" and isinstance(column.get("known_nulls"), dict))


def null_flag_columns(contract: dict[str, Any] | None, fetched: list[str]) -> list[tuple[str, str]]:
    """(helper column id, raw column) of every null flag build adds: one per fetched raw column of a type in
    NULL_FLAG_TYPES that may be empty (may_be_empty: null rule "allowed", or agreed known nulls), in the order of
    the fetched columns. lift computes the same flags from the manifest's columns of kind nullflag."""
    if not contract:
        return []
    cols = {c["name"]: c for c in contract.get("columns", [])}
    out = []
    for name in fetched:
        c = cols.get(name)
        if c and c.get("type") in NULL_FLAG_TYPES and may_be_empty(c):
            out.append((f"{name}{NULL_FLAG_SUFFIX}", name))
    return out


def helper_name_clashes(contract: dict[str, Any] | None, fetched: list[str]) -> list[tuple[int, str, str]]:
    """(index in the contract's columns, column name, helper) for every fetched contract column whose name is a
    helper column build adds on the helper sheet (round 3 verdict, A-aera-12): one of HELPER_FIXED_COLUMNS (helper
    = that name), or the null flag of another fetched column (null_flag_columns; helper = "<c>_null, the null flag
    of column '<c>'"). `contract propose` notes the same names (N-HELPER-NAME) with every column counted as
    fetched."""
    if not contract:
        return []
    flags = dict(null_flag_columns(contract, fetched))
    out = []
    for i, c in enumerate(contract.get("columns", [])):
        name = c.get("name")
        if name not in fetched:
            continue
        if name in HELPER_FIXED_COLUMNS:
            out.append((i, name, name))
        elif name in flags:
            out.append((i, name, f"{name}, the null flag of column '{flags[name]}'"))
    return out


def helper_name_problem(spec: Spec, table: str, name: str, helper: str) -> tuple[str, str]:
    """(message, fix) of the refusal of a contract column named like a helper column (A-aera-12)."""
    from .contract import _rotm
    rotm, model = _rotm(), spec.root.as_posix()
    return (f"contract column '{name}' of '{table}' has the name of a helper column build adds ({helper})",
            f"rename the destination column in contracts/{table}.contract.json (for example '{name}_src'), keep its "
            f"source expression, then show the contract and get it agreed again ({rotm} contract show \"{model}\" "
            f"--table {table}; {rotm} contract agree \"{model}\" --table {table} --reply \"<their words>\" "
            "--by <name>)")


def _generated_check_ids(spec: Spec) -> dict[str, str]:
    out: dict[str, str] = {}
    for table in spec.raw:
        for suffix in GENERATED_CHECK_SUFFIXES:
            out[f"{table}_{suffix}"] = table
        contract = spec.contracts.get(table)
        if contract:
            cols = contract["columns"]
            for t in contract.get("expect", {}).get("totals", []):
                out[f"{table}_total_{t['column']}"] = table
            for c in cols[1:]:
                if c["null_rule"] == "never":
                    out[f"{table}_nulls_{c['name']}"] = table
    return out


# ----------------------------------------------------------------------------- shapes of rows and columns

def _dim(spec: Spec, block_id: str, dim: str, seen: tuple[str, ...] = ()) -> tuple | None:
    """A comparable signature of a block's rows or cols, following same_as; None when it has none."""
    b = spec.blocks.get(block_id)
    if b is None or block_id in seen:
        return None
    data, shape = b["data"], b["shape"]
    if dim == "rows":
        if shape == "items":
            return ("items", block_id)
        node = data.get("rows")
    else:
        if shape == "measures":
            return ("measures", block_id)
        node = data.get("cols")
        if node is None:
            return ("value",)
    if node is None:
        return None
    if "same_as" in node:
        return _dim(spec, node["same_as"], dim, seen + (block_id,))
    if "values" in node:
        return ("values", tuple(str(v) for v in node["values"]))
    if "in" in node:
        return ("in", node["in"])
    if "timeline" in node:
        t = node["timeline"]
        return ("timeline", t["start"], t["periods"], t["step"])
    return None


def _check_dimensions(spec: Spec, problems: _Problems) -> None:
    for bid, b in spec.blocks.items():
        data, path = b["data"], b["path"]
        for dim in ("rows", "cols"):
            node = data.get(dim)
            if not node:
                continue
            if "same_as" in node:
                target = node["same_as"]
                tb = spec.blocks.get(target)
                if tb is None:
                    problems.add(f"{path}.{dim}.same_as", f"unknown block '{target}'" + _did_you_mean(target, spec.blocks),
                                 "name a block of a calc or output sheet")
                elif target == bid:
                    problems.add(f"{path}.{dim}.same_as", "names its own block", "name another block")
                elif (dim == "rows" and tb["shape"] == "items") or (dim == "cols" and tb["shape"] == "measures"):
                    problems.add(f"{path}.{dim}.same_as", f"block '{target}' is a {tb['shape']} block and has no {dim}",
                                 f"name a block that has {dim} (a grid)")
                elif _dim(spec, bid, dim) is None:
                    problems.add(f"{path}.{dim}.same_as", "the same_as chain loops back to this block",
                                 "end the chain at a block with values, in or timeline")
            if "in" in node:
                src = node["in"]
                inp = spec.inputs.get(src)
                if inp is None:
                    problems.add(f"{path}.{dim}.in", f"unknown input '{src}'" + _did_you_mean(src, spec.inputs),
                                 "name a list input of the inputs sheet")
                elif inp["kind"] != "list":
                    problems.add(f"{path}.{dim}.in", f"input '{src}' is a {inp['kind']}, not a list",
                                 "name an input with \"kind\": \"list\"")
            if "timeline" in node:
                start = node["timeline"]["start"]
                inp = spec.inputs.get(start)
                if inp is None:
                    problems.add(f"{path}.{dim}.timeline.start", f"unknown input '{start}'"
                                 + _did_you_mean(start, spec.inputs), "name a scalar date input, e.g. start_month")
                elif inp["kind"] != "scalar" or inp.get("generated"):
                    problems.add(f"{path}.{dim}.timeline.start", f"input '{start}' is not a scalar input",
                                 "name a scalar date input")
                elif inp["item"].get("type") not in (None, "date"):
                    problems.add(f"{path}.{dim}.timeline.start", f"input '{start}' has type "
                                 f"{inp['item'].get('type')}, not date", "give the start input \"type\": \"date\"")


def _check_formats(spec: Spec, problems: _Problems) -> None:
    d = spec.dictionary
    if d is None:
        return
    keys = set(d.number_format_keys())

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                p = f"{path}.{k}" if path else k
                if k == "format" and isinstance(v, str):
                    if v not in keys:
                        problems.add(p, f"number format '{v}' is not in the style dictionary" + _did_you_mean(v, keys),
                                     "use one of: " + ", ".join(sorted(keys)))
                else:
                    walk(v, p)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(spec.data.get("sheets", []), "sheets")


def _check_expressions(spec: Spec, problems: _Problems) -> None:
    helper_order: dict[str, list[str]] = {}
    for table, names in spec.helper_columns.items():
        helper_order[table] = names
    for path, expr, ctx, info in _iter_expressions(spec.data):
        for ph in placeholders(expr):
            _check_placeholder(spec, problems, path, ph, ctx, info, helper_order)
        if ctx == "items" and not info.get("is_first"):
            if any(ph.kind == "prev" for ph in placeholders(expr)) and not info.get("has_first"):
                problems.add(path, f"line item '{info['item']}' uses {{prev}} but has no \"first\"",
                             "add \"first\": the expression for the first column (references/model-spec-format.md)")
        if ctx == "items" and info.get("is_first"):
            for ph in placeholders(expr):
                if ph.kind == "prev":
                    problems.add(path, "\"first\" is the first column and cannot use {prev}",
                                 "write the first column's expression without {prev}")
        if ctx == "check":
            for ph in placeholders(expr):
                if ph.kind == "blk" and ph.arg:
                    bid = ph.arg.split(".")[0]
                    if bid in spec.blocks:
                        spec.block_checks.setdefault(bid, [])
                        if info["check"] not in spec.block_checks[bid]:
                            spec.block_checks[bid].append(info["check"])
                if ph.kind == "cell" and ph.arg:
                    target = spec.key_outputs.get(ph.arg)
                    if target and not target.get("ambiguous"):
                        spec.block_checks.setdefault(target["block"], [])
                        if info["check"] not in spec.block_checks[target["block"]]:
                            spec.block_checks[target["block"]].append(info["check"])
    for path, expr, ctx, info in _iter_expressions(spec.data):
        if "{" in expr or "}" in expr:
            stray = _stray_braces(expr)
            if stray:
                problems.add(path, f"'{stray}' is not a placeholder", "write placeholders as {kind:arg}, e.g. "
                             "{in:scale}; kinds: " + ", ".join(PLACEHOLDER_KINDS))


def _stray_braces(expr: str) -> str | None:
    """Text with a brace that is neither a placeholder, a quoted string nor an array constant."""
    spans = string_spans(expr)
    taken = [(m.start(), m.end()) for m in _CANDIDATE_RE.finditer(expr)]
    i = 0
    while i < len(expr):
        ch = expr[i]
        if ch in "{}" and not any(a <= i < b for a, b in spans) and not any(a <= i < b for a, b in taken):
            if ch == "{":
                end = expr.find("}", i)
                body = expr[i + 1:end] if end > i else expr[i + 1:]
                if end > i and re.fullmatch(r"[\s\d.,;\-+\"#A-Z/!?]*", body):
                    i = end + 1          # an array constant such as {1,2;3,4}; F-ARRAY is the audit's rule
                    continue
                return expr[i:end + 1] if end > i else expr[i:]
            return ch
        i += 1
    return None


def _check_placeholder(spec: Spec, problems: _Problems, path: str, ph: Placeholder, ctx: str,
                       info: dict[str, Any], helper_order: dict[str, list[str]]) -> None:
    kind, arg = ph.kind, ph.arg
    where = _CONTEXT_WORDS[ctx]
    if kind not in PLACEHOLDER_KINDS:
        problems.add(path, f"unknown placeholder kind '{kind}' in {ph.text}" + _did_you_mean(kind.lower(), PLACEHOLDER_KINDS),
                     "use one of: " + ", ".join(PLACEHOLDER_KINDS))
        return
    if ctx not in ALLOWED_IN[kind]:
        allowed = ", ".join(_CONTEXT_WORDS[c] for c in ALLOWED_IN[kind])
        fix = {"this": "outside a helper sheet, read helper columns as {h:<table>.<column>}",
               "h": "inside a helper sheet, read same-row helper columns as {this:<column>}"}.get(kind, "")
        problems.add(path, f"{ph.text} cannot be used in {where} (only in {allowed})",
                     fix or "use a placeholder that fits this place (references/model-spec-format.md lists where each kind fits)")
        return
    no_arg = kind in ("row", "col")
    if no_arg:
        if arg is not None:
            problems.add(path, f"{ph.text}: {{{kind}}} takes no argument", f"write {{{kind}}}")
        return
    if kind == "prev":
        if arg is not None:
            _need_item(spec, problems, path, ph, info, arg)
        return
    if not arg:
        problems.add(path, f"{ph.text} needs an argument", f"write {{{kind}:<{_ARG_FORM[kind]}>}}")
        return
    parts = arg.split(".")
    if kind == "in":
        iid = parts[0]
        inp = spec.inputs.get(iid)
        if inp is None:
            problems.add(path, f"unknown input '{iid}' in {ph.text}" + _did_you_mean(iid, spec.inputs),
                         "name an input of the inputs sheet")
        elif len(parts) > 2 or (len(parts) == 2 and parts[1] not in ("rows", "cols")):
            problems.add(path, f"{ph.text}: after the input id only .rows or .cols may follow",
                         f"write {{in:{iid}}}, {{in:{iid}.rows}} or {{in:{iid}.cols}}")
        elif len(parts) == 2 and inp["kind"] != "grid":
            problems.add(path, f"{ph.text}: input '{iid}' is a {inp['kind']}; .{parts[1]} needs a grid input",
                         f"write {{in:{iid}}}")
    elif kind in ("raw", "h"):
        if len(parts) != 2:
            problems.add(path, f"{ph.text} needs <table>.<column>", f"write {{{kind}:<table>.<column>}}, e.g. "
                         f"{{{kind}:sales.amount}}")
            return
        table, col = parts
        if kind == "raw":
            if table not in spec.raw:
                problems.add(path, f"unknown raw table '{table}' in {ph.text}" + _did_you_mean(table, spec.raw),
                             "name a table that has a raw sheet")
                return
            contract = spec.contracts.get(table)
            if contract is not None:
                cols = [c["name"] for c in contract["columns"]]
                if col not in cols:
                    problems.add(path, f"'{col}' is not a column of raw table '{table}' in {ph.text}"
                                 + _did_you_mean(col, cols), "use a column name of the contract")
        else:
            if table not in spec.helpers:
                problems.add(path, f"table '{table}' has no helper sheet, in {ph.text}" + _did_you_mean(table, spec.helpers),
                             f"add a helper sheet h_{table}, or read the raw column with {{raw:{table}.{col}}}")
                return
            names = helper_order.get(table, [])
            if (spec.contracts.get(table) is not None or spec.helpers[table].data.get("fetch") is not None) \
                    and col not in names:
                problems.add(path, f"'{col}' is not a helper column of h_{table} in {ph.text}"
                             + _did_you_mean(col, names), "use a fetched raw column or a helper column id")
    elif kind == "this":
        table = info["table"]
        names = helper_order.get(table, [])
        own = info["column"]
        if arg == own:
            problems.add(path, f"{ph.text}: helper column '{own}' refers to itself", "refer to another column")
        elif (spec.contracts.get(table) is not None or spec.helpers[table].data.get("fetch") is not None) \
                and arg not in names:
            problems.add(path, f"'{arg}' is not a column of helper sheet h_{table} in {ph.text}"
                         + _did_you_mean(arg, names), "use a fetched raw column or a helper column id")
    elif kind == "r":
        _need_item(spec, problems, path, ph, info, arg, self_ok=False)
    elif kind == "m":
        block = _own_block(spec, info)
        ids = [m["id"] for m in block.get("measures") or []]
        if arg not in ids:
            problems.add(path, f"unknown measure '{arg}' in {ph.text}" + _did_you_mean(arg, ids),
                         "name a measure of this block")
        elif arg == info.get("measure"):
            problems.add(path, f"{ph.text}: measure '{arg}' refers to itself", "refer to another measure")
    elif kind in ("g", "grow", "gcol"):
        bid = arg
        tb = spec.blocks.get(bid)
        if tb is None:
            problems.add(path, f"unknown block '{bid}' in {ph.text}" + _did_you_mean(bid, spec.blocks),
                         "name a grid block")
            return
        if tb["shape"] != "grid":
            problems.add(path, f"{ph.text}: block '{bid}' is a {tb['shape']} block; {{{kind}:}} needs a grid",
                         "name a grid block")
            return
        here = info["block"]
        if bid == here:
            problems.add(path, f"{ph.text} refers to its own block", "refer to another block")
            return
        need = {"g": ("rows", "cols"), "grow": ("rows",), "gcol": ("cols",)}[kind]
        for dim in need:
            if _dim(spec, bid, dim) != _dim(spec, here, dim):
                problems.add(path, f"{ph.text}: block '{bid}' does not have the same {dim} as block '{here}'",
                             f"give block '{here}' \"{dim}\": {{\"same_as\": \"{bid}\"}}, or the same {dim} definition")
    elif kind == "b":
        if len(parts) != 2:
            problems.add(path, f"{ph.text} needs <block>.<item> or <block>.total",
                         "write {b:<block>.<item>} or {b:<block>.total}")
            return
        bid, item = parts
        tb = spec.blocks.get(bid)
        if tb is None:
            problems.add(path, f"unknown block '{bid}' in {ph.text}" + _did_you_mean(bid, spec.blocks),
                         "name a block of a calc or output sheet")
            return
        here = info["block"]
        if item == "total":
            if tb["shape"] != "grid" or not tb["data"].get("total", {}).get("row"):
                problems.add(path, f"{ph.text}: block '{bid}' has no total row",
                             f"add \"total\": {{\"row\": true}} to grid block '{bid}'")
                return
        else:
            if tb["shape"] != "items":
                problems.add(path, f"{ph.text}: block '{bid}' is a {tb['shape']} block; {{b:<block>.<item>}} reads a "
                             "line item of an items block", f"write {{b:{bid}.total}} or name an items block")
                return
            ids = [i["id"] for i in tb["data"]["items"]]
            if item not in ids:
                problems.add(path, f"unknown item '{item}' of block '{bid}' in {ph.text}" + _did_you_mean(item, ids),
                             "name a line item of that block")
                return
            if bid == here and item == info.get("item"):
                problems.add(path, f"{ph.text}: line item '{item}' refers to itself", "refer to another item")
                return
        if _dim(spec, bid, "cols") != _dim(spec, here, "cols"):
            problems.add(path, f"{ph.text}: block '{bid}' does not have the same cols as block '{here}'",
                         f"give block '{here}' \"cols\": {{\"same_as\": \"{bid}\"}}")
    elif kind == "blk":
        if len(parts) > 2:
            problems.add(path, f"{ph.text}: after the block id only .total or .<item> (a line item of an items block) "
                         "may follow", "write {blk:<block>}, {blk:<block>.total} or {blk:<items block>.<item>}")
            return
        bid = parts[0]
        tb = spec.blocks.get(bid)
        if tb is None:
            problems.add(path, f"unknown block '{bid}' in {ph.text}" + _did_you_mean(bid, spec.blocks),
                         "name a block of a calc or output sheet")
        elif len(parts) == 2 and tb["shape"] == "items" and (
                parts[1] != "total" or "total" in [i["id"] for i in tb["data"]["items"]]):
            # v5 (A-aera-9, C-sol-14): {blk:<block>.<item>} is the row of one line item over the block's columns;
            # build resolves it from the layout, so it follows the item when the items are reordered
            ids = [i["id"] for i in tb["data"]["items"]]
            if parts[1] not in ids:
                problems.add(path, f"unknown item '{parts[1]}' of block '{bid}' in {ph.text}"
                             + _did_you_mean(parts[1], ids), "name a line item of that block")
            elif bid == info.get("block"):
                problems.add(path, f"{ph.text} refers to its own block", f"read the line item with {{r:{parts[1]}}}")
        elif len(parts) == 2 and parts[1] != "total":
            problems.add(path, f"{ph.text}: block '{bid}' is a {tb['shape']} block; {{blk:<block>.<item>}} names a line "
                         "item of an items block", f"write {{blk:{bid}}} or {{blk:{bid}.total}}")
        elif len(parts) == 2:
            total = tb["data"].get("total", {}) if tb["shape"] == "grid" else {}
            if not (total.get("row") or total.get("col")):
                problems.add(path, f"{ph.text}: block '{bid}' has no total", f"add \"total\": {{\"row\": true, "
                             f"\"col\": true}} to grid block '{bid}', or use {{blk:{bid}}}")
        elif bid == info.get("block"):
            problems.add(path, f"{ph.text} refers to its own block", "refer to another block")
    elif kind == "cell":
        target = spec.key_outputs.get(arg)
        if target is None:
            keys = [k for k, v in spec.key_outputs.items() if not v.get("ambiguous")]
            problems.add(path, f"unknown key output '{arg}' in {ph.text}" + _did_you_mean(arg, keys),
                         "mark the item, measure or grid block with \"key\": true, or name one that has it")
        elif target.get("ambiguous"):
            problems.add(path, f"{ph.text}: more than one key output is named '{arg}'",
                         f"write {{cell:<block>.{arg}}} to say which block")
    elif kind == "chk":
        generated = _generated_check_ids(spec)
        if arg not in spec.checks and arg not in generated:
            problems.add(path, f"unknown check '{arg}' in {ph.text}" + _did_you_mean(arg, list(spec.checks) + list(generated)),
                         "name a check of the checks sheet")
        elif ctx == "check" and arg == info.get("check"):
            problems.add(path, f"{ph.text}: check '{arg}' refers to itself", "refer to another check")


# ----------------------------------------------------------------------------- dependency cycles (v2, A-aera-5)
#
# Nodes: a helper column the spec defines ("helper", table, id), a grid block ("grid", block), a line item
# ("item", block, id), a measure ("measure", block, id) and a check of the spec ("check", id). An edge A -> B
# means an expression of A reads B. {prev} reads the previous column and adds no edge (a cycle needs the same
# column). Items are read column by column: "first" holds column 1 only, "expr" the other columns, so the two
# views are searched separately. Fetched, fixed and null-flag helper columns, inputs and raw columns read no
# calculated value and are not nodes. A cycle is refused: Excel gives a formula that refers to itself through
# other cells no value, and the check cells that read it can still show 0.

# Self references the placeholder checks already refuse with their own message.
_SELF_REFUSED_KINDS = ("this", "r", "m", "g", "grow", "gcol", "blk", "chk")


def _block_nodes(spec: Spec, bid: str) -> list[tuple[str, ...]]:
    b = spec.blocks.get(bid)
    if b is None:
        return []
    if b["shape"] == "grid":
        return [("grid", bid)]
    if b["shape"] == "items":
        return [("item", bid, it["id"]) for it in b["data"]["items"]]
    return [("measure", bid, m["id"]) for m in b["data"]["measures"]]


def _key_node(spec: Spec, key: str) -> list[tuple[str, ...]]:
    target = spec.key_outputs.get(key) or {}
    if target.get("ambiguous") or not target.get("block"):
        return []
    kind, block, id_ = target["kind"], target["block"], target["id"]
    if kind == "grid":
        return [("grid", block)]
    return [("item" if kind == "items" else "measure", block, id_)]


def _block_columns(spec: Spec, bid: str, seen: tuple[str, ...] = ()) -> int | None:
    """Number of value columns of an items block (None when it cannot be told without the layout)."""
    b = spec.blocks.get(bid)
    if b is None or bid in seen:
        return None
    node = b["data"].get("cols")
    if node is None:
        return 1
    if "values" in node:
        return len(node["values"])
    if "in" in node:
        inp = spec.inputs.get(node["in"]) or {}
        vals = (inp.get("item") or {}).get("values")
        return len(vals) if isinstance(vals, list) else None
    if "timeline" in node:
        try:
            return int(node["timeline"]["periods"])
        except (KeyError, TypeError, ValueError):
            return None
    if "same_as" in node:
        return _block_columns(spec, node["same_as"], seen + (bid,))
    return None


def _node_label(spec: Spec, node: tuple[str, ...]) -> str:
    if node[0] == "helper":
        sheet = spec.helpers.get(node[1])
        return f"helper column {sheet.name if sheet else 'h_' + node[1]}.{node[2]}"
    if node[0] == "grid":
        return f"block '{node[1]}'"
    if node[0] == "item":
        return f"line item '{node[2]}' of block '{node[1]}'"
    if node[0] == "measure":
        return f"measure '{node[2]}' of block '{node[1]}'"
    return f"check '{node[1]}'"


def dependency_edges(spec: Spec) -> tuple[list[tuple[tuple[str, ...], tuple[str, ...], str, str]], list[tuple[str, ...]]]:
    """(edges, nodes in spec order). Each edge is (from node, to node, JSON path of the expression, view): view
    "all" (every column), "first" (the first column of an items block) or "rest" (the other columns)."""
    edges: list[tuple[tuple[str, ...], tuple[str, ...], str, str]] = []
    order: list[tuple[str, ...]] = []
    derived = {t: {c["id"] for c in s.data.get("columns", [])} for t, s in spec.helpers.items()}
    for path, expr, ctx, info in _iter_expressions(spec.data):
        if ctx == "helpers":
            src: tuple[str, ...] = ("helper", info["table"], info["column"])
            view = "all"
        elif ctx == "grid":
            src, view = ("grid", info["block"]), "all"
        elif ctx == "items":
            src = ("item", info["block"], info["item"])
            if info.get("is_first"):
                view = "first"
            elif info.get("has_first"):
                view = "rest" if _block_columns(spec, info["block"]) != 1 else "never"
            else:
                view = "all"
        elif ctx == "measures":
            src, view = ("measure", info["block"], info["measure"]), "all"
        else:
            src, view = ("check", info["check"]), "all"
        if src not in order:
            order.append(src)
        if view == "never":
            continue
        for ph in placeholders(expr):
            kind, arg = ph.kind, ph.arg or ""
            parts = arg.split(".")
            targets: list[tuple[str, ...]] = []
            if kind == "this" and ctx == "helpers":
                if arg in derived.get(info["table"], set()):
                    targets = [("helper", info["table"], arg)]
            elif kind == "h" and len(parts) == 2:
                if parts[1] in derived.get(parts[0], set()):
                    targets = [("helper", parts[0], parts[1])]
            elif kind == "r" and info.get("block"):
                targets = [("item", info["block"], arg)]
            elif kind == "m" and info.get("block"):
                targets = [("measure", info["block"], arg)]
            elif kind == "b" and len(parts) == 2:
                targets = _block_nodes(spec, parts[0]) if parts[1] == "total" else [("item", parts[0], parts[1])]
            elif kind in ("g", "grow", "gcol"):
                targets = _block_nodes(spec, arg)
            elif kind == "blk":
                tb = spec.blocks.get(parts[0])
                if len(parts) == 2 and tb is not None and tb["shape"] == "items" and \
                        parts[1] in [i["id"] for i in tb["data"]["items"]]:
                    targets = [("item", parts[0], parts[1])]          # v5: {blk:<block>.<item>} reads one item
                else:
                    targets = _block_nodes(spec, parts[0])
            elif kind == "cell":
                targets = _key_node(spec, arg)
            elif kind == "chk" and arg in spec.checks:
                targets = [("check", arg)]
            for t in targets:
                if t == src and kind in _SELF_REFUSED_KINDS:
                    continue
                edges.append((src, t, path, view))
    return edges, order


def find_cycles(spec: Spec) -> list[list[tuple[tuple[str, ...], tuple[str, ...], str]]]:
    """Every dependency cycle of the spec, each as its edges (from, to, JSON path) in order; one cycle per
    strongly connected group of nodes and view, without repeats."""
    edges, order = dependency_edges(spec)
    found: list[list[tuple[tuple[str, ...], tuple[str, ...], str]]] = []
    seen_sets: set[frozenset[str]] = set()
    for view in ("first", "rest"):
        adj: dict[tuple[str, ...], list[tuple[tuple[str, ...], str]]] = {}
        for a, b, path, v in edges:
            if v == "all" or v == view:
                adj.setdefault(a, []).append((b, path))
        for comp in _strong_components(order, adj):
            members = set(comp)
            start = comp[0]
            if len(comp) == 1 and not any(b == start for b, _p in adj.get(start, [])):
                continue
            cycle = _one_cycle(start, members, adj)
            key = frozenset(p for _a, _b, p in cycle)
            if cycle and key not in seen_sets:
                seen_sets.add(key)
                found.append(cycle)
    return found


def _strong_components(order: list[tuple[str, ...]], adj: dict[tuple[str, ...], list[tuple[tuple[str, ...], str]]]
                       ) -> list[list[tuple[str, ...]]]:
    """Tarjan's strongly connected components (iterative), each in spec order."""
    nodes = list(order) + [n for n in adj if n not in order]
    for targets in adj.values():
        for b, _p in targets:
            if b not in nodes:
                nodes.append(b)
    rank = {n: i for i, n in enumerate(nodes)}
    index: dict[tuple[str, ...], int] = {}
    low: dict[tuple[str, ...], int] = {}
    stack: list[tuple[str, ...]] = []
    on: set[tuple[str, ...]] = set()
    out: list[list[tuple[str, ...]]] = []
    counter = 0
    for root in nodes:
        if root in index:
            continue
        work = [(root, 0)]
        while work:
            v, i = work.pop()
            if i == 0:
                index[v] = low[v] = counter
                counter += 1
                stack.append(v)
                on.add(v)
            succ = adj.get(v, [])
            if i < len(succ):
                work.append((v, i + 1))
                w = succ[i][0]
                if w not in index:
                    work.append((w, 0))
                elif w in on:
                    low[v] = min(low[v], index[w])
                continue
            for w, _p in succ:
                if w in on:
                    low[v] = min(low[v], low[w])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                out.append(sorted(comp, key=lambda n: rank[n]))
    return out


def _one_cycle(start: tuple[str, ...], members: set[tuple[str, ...]],
               adj: dict[tuple[str, ...], list[tuple[tuple[str, ...], str]]]
               ) -> list[tuple[tuple[str, ...], tuple[str, ...], str]]:
    """One cycle through `start` inside `members` (breadth-first, so the shortest), as its edges."""
    prev: dict[tuple[str, ...], tuple[tuple[str, ...], str]] = {}
    queue = [start]
    k = 0
    while k < len(queue):
        v = queue[k]
        k += 1
        for w, path in adj.get(v, []):
            if w not in members:
                continue
            if w == start:
                cycle = [(v, w, path)]
                while v != start:
                    pv, pp = prev[v]
                    cycle.append((pv, v, pp))
                    v = pv
                return list(reversed(cycle))
            if w not in prev:
                prev[w] = (v, path)
                queue.append(w)
    return []


def _check_cycles(spec: Spec, problems: _Problems) -> None:
    for cycle in find_cycles(spec):
        paths: list[str] = []
        for _a, _b, p in cycle:
            if p not in paths:
                paths.append(p)
        chain = "; ".join(f"{p} ({_node_label(spec, a)}) reads {_node_label(spec, b)}" for a, b, p in cycle)
        problems.add(paths[0], f"circular reference among {', '.join(paths)}: {chain}",
                     "change one of these expressions so that no value reads itself through the others; Excel "
                     "gives a formula that refers to itself through other cells no value, and the checks that read "
                     "it can still show 0")


_ARG_FORM = {"in": "input id", "raw": "table>.<column", "h": "table>.<column", "this": "column", "r": "item",
             "m": "measure", "g": "block", "grow": "block", "gcol": "block", "b": "block>.<item",
             "blk": "block", "cell": "key output id", "chk": "check id", "prev": "item"}


def _need_item(spec: Spec, problems: _Problems, path: str, ph: Placeholder, info: dict[str, Any], arg: str,
               self_ok: bool = True) -> None:
    block = _own_block(spec, info)
    ids = [i["id"] for i in block.get("items") or []]
    if arg not in ids:
        problems.add(path, f"unknown line item '{arg}' in {ph.text}" + _did_you_mean(arg, ids),
                     "name a line item of this block")
    elif not self_ok and arg == info.get("item"):
        problems.add(path, f"{ph.text}: line item '{arg}' refers to itself in the same column",
                     "use {prev} for the previous column, or refer to another item")


def _own_block(spec: Spec, info: dict[str, Any]) -> dict[str, Any]:
    """The block an expression is written in: its own data (v5, J-1), so a block whose id is refused as used twice
    is read as written and no lookup by id fails."""
    data = info.get("block_data")
    if isinstance(data, dict):
        return data
    b = spec.blocks.get(info.get("block") or "")
    return b["data"] if b else {}

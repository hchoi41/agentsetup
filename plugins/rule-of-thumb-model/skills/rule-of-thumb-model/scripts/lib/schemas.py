"""Schemas and a small validator for every JSON file of the plugin (design sections 3.3, 4, 5, 6, 10).

Owner: CORE-IO. Standard library only (no jsonschema package on the build PC; the hooks import this module
under `python -I`, design D2).

Schema names: "marker", "contract", "spec", "dictionary", "dictionary_override", "manifest", "status", "report",
"controls" (raw/<table>.controls.json, the control totals accepted per raw snapshot; lib.controls).
Every message names the JSON path and says what to change; an unknown key gets the nearest valid key, e.g.
"sheets[4].blocks[0].cel: unknown key 'cel'; did you mean 'cell'? Fix: rename it to 'cell'".

This module checks the shape of one file. Checks that need other files or the layout (ids that must exist,
contracts that must be agreed, placeholders) live in lib.spec, lib.dictionary and lib.modelfolder.
"""
from __future__ import annotations

import difflib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

SCHEMA_NAMES = ("marker", "contract", "spec", "dictionary", "dictionary_override", "manifest", "status", "report",
                "controls")

# Patterns fixed by the design.
TABLE_RE = r"^[a-z][a-z0-9_]{0,19}$"          # contract table names (design 5)
ID_RE = r"^[a-z][a-z0-9_]{0,23}$"             # item, block, column and check ids; sheet names (design 6.1)
COLUMN_RE = r"^[^\W\d]\w{0,40}$"              # contract column names (Unicode letters allowed, design 5)
COLUMN_TYPES = ("text", "integer", "number", "date", "datetime", "boolean")
NULL_RULES = ("never", "allowed")
CHANNELS = ("session", "task-file", "example")
CHECK_KINDS = ("equal", "range", "zero", "at_most")
PRODUCTS = ("both", "excel", "sheets")
SHEET_TYPES = ("cover", "inputs", "raw", "helpers", "calc", "output", "checks", "notes")
BLOCK_SHAPES = ("grid", "items", "measures")
CONNECTORS = ("power_query", "connected_sheets", "csv", "other")
CELL_ATTRIBUTES = ("meaning", "font_color", "bold", "italic", "fill", "border", "number_format", "align",
                   "wrap", "locked", "size")
ALIGNS = ("left", "center", "right", "type", "general")
BORDER_SIDES = ("left", "right", "top", "bottom")
BORDER_STYLES = ("thin", "medium", "thick", "dashed", "dotted", "double", "hair", "mediumDashed",
                 "dashDot", "mediumDashDot", "dashDotDot", "mediumDashDotDot", "slantDashDot")
REPORT_STATUSES = ("pass", "fail", "refused", "not_verified", "error")
SEVERITIES = ("error", "warning", "info")

ROOT = "(top level)"


@dataclass(frozen=True)
class SchemaError:
    path: str          # JSON path, e.g. "columns[2].type"
    message: str       # what is wrong
    fix: str = ""      # what to change

    def line(self) -> str:
        return f"{self.path}: {self.message}" + (f" Fix: {self.fix}" if self.fix else "")


def nearest(key: str, valid: Iterable[str]) -> str | None:
    """The closest valid key (difflib, cutoff 0.6), or None."""
    found = difflib.get_close_matches(str(key), [str(v) for v in valid], n=1, cutoff=0.6)
    return found[0] if found else None


def _join(path: str, key: str) -> str:
    return key if path in ("", ROOT) else f"{path}.{key}"


def _index(path: str, i: int) -> str:
    return f"[{i}]" if path in ("", ROOT) else f"{path}[{i}]"


def kind_of(value: Any) -> str:
    """Plain name of a JSON value's kind, for messages."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true/false"
    if isinstance(value, int):
        return "a whole number"
    if isinstance(value, float):
        return "a number"
    if isinstance(value, str):
        return "text"
    if isinstance(value, list):
        return "a list"
    if isinstance(value, dict):
        return "an object"
    return type(value).__name__


def _show(value: Any, limit: int = 60) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        text = repr(value)
    return text if len(text) <= limit else text[:limit - 3] + "..."


# ----------------------------------------------------------------------------- nodes

class Node:
    """One schema node. `desc` is a short description used in fixes ("add \"x\": <desc>")."""

    desc = "a value"

    def check(self, value: Any, path: str, errs: list[SchemaError]) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def partial(self) -> "Node":
        """The node with every object key optional (for override files); leaves stay complete."""
        return self


class AnyValue(Node):
    desc = "any value"

    def check(self, value: Any, path: str, errs: list[SchemaError]) -> None:
        return


class Str(Node):
    def __init__(self, *, pattern: str | None = None, enum: Sequence[str] | None = None, min_len: int = 0,
                 max_len: int | None = None, hint: str | None = None, desc: str | None = None):
        self.pattern = re.compile(pattern) if pattern else None
        self.enum = tuple(enum) if enum else None
        self.min_len = min_len
        self.max_len = max_len
        self.hint = hint
        self.desc = desc or ("one of " + ", ".join(f'"{e}"' for e in self.enum) if self.enum else "text")

    def check(self, value: Any, path: str, errs: list[SchemaError]) -> None:
        if not isinstance(value, str):
            errs.append(SchemaError(path, f"must be text, got {kind_of(value)} {_show(value)}",
                                    f"write it as {self.desc} in double quotes"))
            return
        if self.enum is not None and value not in self.enum:
            near = nearest(value, self.enum)
            msg = f"'{value}' is not allowed" + (f"; did you mean '{near}'?" if near else "")
            errs.append(SchemaError(path, msg, "use one of: " + ", ".join(self.enum)))
            return
        if len(value.strip()) < self.min_len:
            errs.append(SchemaError(path, "is empty", f"write {self.hint or self.desc}"))
            return
        if self.max_len is not None and len(value) > self.max_len:
            errs.append(SchemaError(path, f"is {len(value)} characters long; the limit is {self.max_len}",
                                    "shorten it"))
            return
        if self.pattern is not None and not self.pattern.match(value):
            errs.append(SchemaError(path, f"'{value}' does not have the required form",
                                    f"use {self.hint or 'the form ' + self.pattern.pattern}"))


class Int(Node):
    def __init__(self, min: int | None = None, max: int | None = None, *, desc: str | None = None):
        self.min, self.max = min, max
        if desc:
            self.desc = desc
        elif min is not None and max is not None:
            self.desc = f"a whole number from {min} to {max}"
        elif min is not None:
            self.desc = f"a whole number of at least {min}"
        else:
            self.desc = "a whole number"

    def check(self, value: Any, path: str, errs: list[SchemaError]) -> None:
        if isinstance(value, bool) or not isinstance(value, int):
            errs.append(SchemaError(path, f"must be a whole number, got {kind_of(value)} {_show(value)}",
                                    f"write {self.desc} without quotes or decimals"))
            return
        if (self.min is not None and value < self.min) or (self.max is not None and value > self.max):
            errs.append(SchemaError(path, f"{value} is out of range", f"use {self.desc}"))


class Num(Node):
    def __init__(self, min: float | None = None, max: float | None = None, *, desc: str | None = None):
        self.min, self.max = min, max
        if desc:
            self.desc = desc
        elif min is not None and max is not None:
            self.desc = f"a number from {min} to {max}"
        elif min is not None:
            self.desc = f"a number of at least {min}"
        else:
            self.desc = "a number"

    def check(self, value: Any, path: str, errs: list[SchemaError]) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errs.append(SchemaError(path, f"must be a number, got {kind_of(value)} {_show(value)}",
                                    f"write {self.desc} without quotes"))
            return
        if isinstance(value, float) and not math.isfinite(value):
            errs.append(SchemaError(path, f"{value} is not a finite number", f"write {self.desc}"))
            return
        if (self.min is not None and value < self.min) or (self.max is not None and value > self.max):
            errs.append(SchemaError(path, f"{value} is out of range", f"use {self.desc}"))


class Bool(Node):
    desc = "true or false"

    def check(self, value: Any, path: str, errs: list[SchemaError]) -> None:
        if not isinstance(value, bool):
            errs.append(SchemaError(path, f"must be true or false, got {kind_of(value)} {_show(value)}",
                                    "write true or false without quotes"))


class Null(Node):
    desc = "null"

    def check(self, value: Any, path: str, errs: list[SchemaError]) -> None:
        if value is not None:
            errs.append(SchemaError(path, f"must be null, got {kind_of(value)} {_show(value)}", "write null"))


class Const(Node):
    def __init__(self, value: Any, *, hint: str | None = None):
        self.value = value
        self.hint = hint
        self.desc = _show(value)

    def check(self, value: Any, path: str, errs: list[SchemaError]) -> None:
        if value != self.value or isinstance(value, bool) != isinstance(self.value, bool):
            errs.append(SchemaError(path, f"must be {_show(self.value)}, got {_show(value)}",
                                    self.hint or f"write {_show(self.value)}"))


class Enum(Node):
    def __init__(self, values: Sequence[Any], *, hint: str | None = None):
        self.values = tuple(values)
        self.hint = hint
        self.desc = "one of " + ", ".join(_show(v) for v in self.values)

    def check(self, value: Any, path: str, errs: list[SchemaError]) -> None:
        ok = any(value == v and isinstance(value, bool) == isinstance(v, bool) for v in self.values)
        if not ok:
            near = nearest(str(value), [str(v) for v in self.values]) if isinstance(value, str) else None
            msg = f"{_show(value)} is not allowed" + (f"; did you mean '{near}'?" if near else "")
            errs.append(SchemaError(path, msg, self.hint or "use " + self.desc))


class Arr(Node):
    def __init__(self, item: Node, *, min_items: int = 0, max_items: int | None = None, unique: bool = False,
                 desc: str | None = None):
        self.item = item
        self.min_items = min_items
        self.max_items = max_items
        self.unique = unique
        self.desc = desc or f"a list of {item.desc}"

    def check(self, value: Any, path: str, errs: list[SchemaError]) -> None:
        if not isinstance(value, list):
            errs.append(SchemaError(path, f"must be a list, got {kind_of(value)} {_show(value)}",
                                    f"write {self.desc} in square brackets"))
            return
        if len(value) < self.min_items:
            need = "at least one item" if self.min_items == 1 else f"at least {self.min_items} items"
            errs.append(SchemaError(path, f"has {len(value)} items; it needs {need}", f"add {self.item.desc}"))
        if self.max_items is not None and len(value) > self.max_items:
            errs.append(SchemaError(path, f"has {len(value)} items; at most {self.max_items} are allowed",
                                    "remove the extra items"))
        seen: dict[str, int] = {}
        for i, item in enumerate(value):
            self.item.check(item, _index(path, i), errs)
            if self.unique:
                key = json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
                if key in seen:
                    errs.append(SchemaError(_index(path, i), f"repeats item {seen[key]} ({_show(item)})",
                                            "remove the duplicate"))
                else:
                    seen[key] = i

    def partial(self) -> "Node":
        return Arr(self.item.partial(), min_items=0, max_items=self.max_items, unique=self.unique, desc=self.desc)


class Tup(Node):
    """A list of fixed length with one node per position, e.g. ["B6:B9", "row_header"]."""

    def __init__(self, *items: Node, desc: str | None = None):
        self.items = items
        self.desc = desc or "[" + ", ".join(i.desc for i in items) + "]"

    def check(self, value: Any, path: str, errs: list[SchemaError]) -> None:
        if not isinstance(value, list) or len(value) != len(self.items):
            errs.append(SchemaError(path, f"must be a list of {len(self.items)} items, got {_show(value)}",
                                    f"write {self.desc}"))
            return
        for i, (node, item) in enumerate(zip(self.items, value)):
            node.check(item, _index(path, i), errs)


Rule = Callable[[dict, str, list], None]


class Obj(Node):
    """An object with required and optional keys. `extra` is the node for keys not listed (None: unknown keys
    are refused). `exactly_one` / `at_least_one` name keys of which exactly one / at least one must be present.
    `rules` are cross-field checks `rule(obj, path, errs)` run after the keys passed."""

    def __init__(self, required: dict[str, Node] | None = None, optional: dict[str, Node] | None = None, *,
                 extra: Node | None = None, exactly_one: Sequence[str] = (), at_least_one: Sequence[str] = (),
                 rules: Sequence[Rule] = (), desc: str = "an object", what: str | None = None):
        self.required = dict(required or {})
        self.optional = dict(optional or {})
        self.extra = extra
        self.exactly_one = tuple(exactly_one)
        self.at_least_one = tuple(at_least_one)
        self.rules = tuple(rules)
        self.desc = desc
        self.what = what or desc

    def keys(self) -> list[str]:
        return list(self.required) + list(self.optional)

    def check(self, value: Any, path: str, errs: list[SchemaError]) -> None:
        if not isinstance(value, dict):
            errs.append(SchemaError(path, f"must be {self.desc} (a JSON object in braces), got {kind_of(value)} "
                                          f"{_show(value)}", f"write {self.what} as {{\"key\": value, ...}}"))
            return
        before = len(errs)
        for key, node in self.required.items():
            if key not in value:
                errs.append(SchemaError(_join(path, key), f"is required in {self.what}",
                                        f"add \"{key}\": {node.desc}"))
            else:
                node.check(value[key], _join(path, key), errs)
        for key, node in self.optional.items():
            if key in value:
                node.check(value[key], _join(path, key), errs)
        for key in value:
            if key in self.required or key in self.optional:
                continue
            if self.extra is not None:
                self.extra.check(value[key], _join(path, key), errs)
                continue
            near = nearest(key, self.keys())
            msg = f"unknown key '{key}'" + (f"; did you mean '{near}'?" if near else "")
            fix = f"rename it to '{near}'" if near else (
                "remove it; allowed keys: " + ", ".join(self.keys()) if self.keys() else "remove it")
            errs.append(SchemaError(_join(path, key), msg, fix))
        if self.exactly_one:
            present = [k for k in self.exactly_one if k in value]
            if len(present) != 1:
                names = ", ".join(f"'{k}'" for k in self.exactly_one)
                got = "none" if not present else ", ".join(f"'{k}'" for k in present)
                errs.append(SchemaError(path or ROOT, f"needs exactly one of {names}; it has {got}",
                                        "keep exactly one of them"))
        if self.at_least_one and not any(k in value for k in self.at_least_one):
            names = ", ".join(f"'{k}'" for k in self.at_least_one)
            errs.append(SchemaError(path or ROOT, f"needs at least one of {names}", "add one of them"))
        if len(errs) == before:
            for rule in self.rules:
                rule(value, path or ROOT, errs)

    def partial(self) -> "Node":
        opt = {k: n.partial() for k, n in {**self.required, **self.optional}.items()}
        return Obj({}, opt, extra=self.extra.partial() if self.extra else None, desc=self.desc, what=self.what)


class MapOf(Node):
    """An object whose keys are names (matching `key_pattern`) and whose values all follow `value`."""

    def __init__(self, value: Node, *, key_pattern: str | None = None, key_hint: str | None = None,
                 min_items: int = 0, required_keys: Sequence[str] = (), desc: str = "an object of named entries"):
        self.value = value
        self.key_pattern = re.compile(key_pattern) if key_pattern else None
        self.key_hint = key_hint
        self.min_items = min_items
        self.required_keys = tuple(required_keys)
        self.desc = desc

    def check(self, value: Any, path: str, errs: list[SchemaError]) -> None:
        if not isinstance(value, dict):
            errs.append(SchemaError(path, f"must be {self.desc} (a JSON object), got {kind_of(value)} "
                                          f"{_show(value)}", "write it as {\"name\": value, ...}"))
            return
        if len(value) < self.min_items:
            errs.append(SchemaError(path, f"has {len(value)} entries; it needs at least {self.min_items}",
                                    "add an entry"))
        for key in self.required_keys:
            if key not in value:
                errs.append(SchemaError(_join(path, key), "is required", f"add \"{key}\": {self.value.desc}"))
        for key, item in value.items():
            if self.key_pattern is not None and not self.key_pattern.match(key):
                errs.append(SchemaError(_join(path, key), f"'{key}' is not a valid name",
                                        f"use {self.key_hint or 'the form ' + self.key_pattern.pattern}"))
                continue
            self.value.check(item, _join(path, key), errs)

    def partial(self) -> "Node":
        return MapOf(self.value.partial(), key_pattern=self.key_pattern.pattern if self.key_pattern else None,
                     key_hint=self.key_hint, desc=self.desc)


class OneOf(Node):
    """The value must follow one of the options; the errors of the closest option are reported."""

    def __init__(self, *options: Node, desc: str | None = None):
        self.options = options
        self.desc = desc or " or ".join(o.desc for o in options)

    def check(self, value: Any, path: str, errs: list[SchemaError]) -> None:
        best: list[SchemaError] | None = None
        for opt in self.options:
            trial: list[SchemaError] = []
            opt.check(value, path, trial)
            if not trial:
                return
            kind_mismatch = len(trial) == 1 and trial[0].path == path and trial[0].message.startswith("must be")
            if not kind_mismatch and (best is None or len(trial) < len(best)):
                best = trial          # the value has this option's kind; report what is wrong inside it
        if best:
            errs.extend(best)
        else:
            errs.append(SchemaError(path, f"must be {self.desc}, got {kind_of(value)} {_show(value)}",
                                    f"write {self.desc}"))

    def partial(self) -> "Node":
        return OneOf(*(o.partial() for o in self.options), desc=self.desc)


class Nullable(Node):
    def __init__(self, node: Node):
        self.node = node
        self.desc = f"{node.desc} or null"

    def check(self, value: Any, path: str, errs: list[SchemaError]) -> None:
        if value is not None:
            self.node.check(value, path, errs)

    def partial(self) -> "Node":
        return Nullable(self.node.partial())


class Switch(Node):
    """An object whose `key` (for example "type") selects the schema of the rest."""

    def __init__(self, key: str, cases: dict[Any, Node], *, default: Node | None = None, what: str = "entry"):
        self.key = key
        self.cases = cases
        self.default = default
        self.what = what
        self.desc = f"an object with \"{key}\": " + " | ".join(_show(k) for k in cases)

    def check(self, value: Any, path: str, errs: list[SchemaError]) -> None:
        if not isinstance(value, dict):
            errs.append(SchemaError(path, f"must be an object ({self.what}), got {kind_of(value)} {_show(value)}",
                                    f"write it as {{\"{self.key}\": ..., ...}}"))
            return
        if self.key not in value:
            if self.default is not None:
                self.default.check(value, path, errs)
                return
            errs.append(SchemaError(_join(path, self.key), f"is required in every {self.what}",
                                    f"add \"{self.key}\": one of " + ", ".join(_show(k) for k in self.cases)))
            return
        tag = value[self.key]
        if not isinstance(tag, (str, int, float, bool)) or tag not in self.cases:
            near = nearest(str(tag), [str(k) for k in self.cases]) if isinstance(tag, str) else None
            msg = f"{_show(tag)} is not a known {self.key}" + (f"; did you mean '{near}'?" if near else "")
            errs.append(SchemaError(_join(path, self.key), msg,
                                    "use one of: " + ", ".join(str(k) for k in self.cases)))
            return
        self.cases[tag].check(value, path, errs)

    def partial(self) -> "Node":
        return Switch(self.key, {k: v.partial() for k, v in self.cases.items()},
                      default=self.default.partial() if self.default else None, what=self.what)


class ShapeSwitch(Node):
    """An object whose shape is chosen by which marker key it holds, e.g. a block with "cell" is a grid."""

    def __init__(self, markers: dict[str, tuple[str, Node]], *, what: str):
        self.markers = markers   # marker key -> (shape name, node)
        self.what = what
        self.desc = what

    def check(self, value: Any, path: str, errs: list[SchemaError]) -> None:
        if not isinstance(value, dict):
            errs.append(SchemaError(path, f"must be an object ({self.what}), got {kind_of(value)} {_show(value)}",
                                    "write it as {\"id\": ..., ...}"))
            return
        present = [k for k in self.markers if k in value]
        if len(present) != 1:
            names = ", ".join(f"'{k}' ({self.markers[k][0]})" for k in self.markers)
            got = "none" if not present else ", ".join(f"'{k}'" for k in present)
            msg = f"{self.what} needs exactly one of {names}; it has {got}"
            fix = "keep the one key that gives the shape you want"
            if not present:
                known = {k for _shape, node in self.markers.values() if isinstance(node, Obj) for k in node.keys()}
                for key in value:
                    if key in known:
                        continue
                    near = nearest(key, self.markers)
                    if near and key != near:
                        msg += f"; '{key}' looks like '{near}'"
                        fix = f"rename '{key}' to '{near}'"
                        break
            errs.append(SchemaError(path or ROOT, msg, fix))
            return
        self.markers[present[0]][1].check(value, path, errs)


class Leaf(Node):
    """A dictionary leaf {"value": ..., "source": "..."} (design 10.1): the schema refuses a leaf without a source."""

    def __init__(self, value: Node):
        self.value = value
        self.desc = f"{{\"value\": {value.desc}, \"source\": \"<where the value comes from>\"}}"
        self._obj = Obj({"value": value, "source": Str(min_len=1, desc="the source text",
                                                       hint="the source: a key of 'sources', e.g. \"MAX\"")},
                        {"note": Str()}, desc="a leaf {\"value\": ..., \"source\": ...}",
                        what="a dictionary value")

    def check(self, value: Any, path: str, errs: list[SchemaError]) -> None:
        self._obj.check(value, path, errs)


# ----------------------------------------------------------------------------- shared pieces

TEXT = Str(desc="text")
NONEMPTY = Str(min_len=1, desc="text (not empty)")
ID = Str(pattern=ID_RE, desc="an id",
         hint="lower-case letters, digits and _, starting with a letter, at most 24 characters (e.g. sales_total)")
SHEET_NAME = Str(pattern=ID_RE, desc="a sheet name",
                 hint="lower-case letters, digits and _, starting with a letter, at most 24 characters (e.g. calc)")
TABLE = Str(pattern=TABLE_RE, desc="a table name",
            hint="lower-case letters, digits and _, starting with a letter, at most 20 characters (e.g. sales)")
COLUMN = Str(pattern=COLUMN_RE, desc="a column name",
             hint="letters, digits and _ only, starting with a letter, at most 41 characters, no spaces "
                  "(e.g. inv_date)")
MODEL_NAME = Str(pattern=r"^[^\\/:*?\"<>|\x00-\x1f]{1,64}$", desc="a model name",
                 hint="1 to 64 characters without \\ / : * ? \" < > |")
FILE_NAME = Str(pattern=r"^[^\\/:*?\"<>|\x00-\x1f]+$", desc="a file name",
                hint="a file name without a folder, e.g. model.spec.json")
XLSX_NAME = Str(pattern=r"^[^\\/:*?\"<>|\x00-\x1f]+\.xlsx$", desc="a workbook file name",
                hint="a file name ending in .xlsx, without a folder, e.g. model.xlsx")
FORMAT_KEY = Str(pattern=r"^[A-Za-z][A-Za-z0-9_]*$", desc="a number-format key",
                 hint="a key of number_formats in the style dictionary, e.g. amount")
UNIT = Nullable(Str(desc="a unit, e.g. KRW"))
EXPR = Str(min_len=1, desc="an expression",
           hint="an Excel formula body without the leading '=', with {kind:arg} placeholders")
EXPR_OR_NUM = OneOf(EXPR, Num(), desc="an expression or a number")
SCALAR = OneOf(Str(), Num(), Bool(), desc="text, a number or true/false")
SCALAR_OR_NULL = OneOf(Str(), Num(), Bool(), Null(), desc="text, a number, true/false or null")
HEX6 = Str(pattern=r"^[0-9A-Fa-f]{6}$", desc="a colour", hint="six hexadecimal digits, e.g. D9D9D9")
SHA256 = Str(pattern=r"^[0-9a-f]{64}$", desc="a sha256 hex digest", hint="64 lower-case hexadecimal digits")
DIGEST = Str(pattern=r"^sha256:[0-9a-f]{64}$", desc="a raw digest", hint="sha256:<64 hexadecimal digits>")
CELL = Str(pattern=r"^\$?[A-Z]{1,3}\$?[1-9][0-9]*$", desc="a cell address", hint="an A1 address such as D7")
RANGE = Str(pattern=r"^\$?[A-Z]{1,3}\$?[1-9][0-9]*(:\$?[A-Z]{1,3}\$?[1-9][0-9]*)?$", desc="a range",
            hint="an A1 range such as B6:B9 or one cell such as D7")
REF = Str(pattern=r"^(?:'(?:[^']|'')+'|[^!'\s][^!']*)!\$?[A-Z]{1,3}\$?[1-9][0-9]*(:\$?[A-Z]{1,3}\$?[1-9][0-9]*)?$",
          desc="a sheet reference", hint="sheet!A1 or sheet!A1:B2, e.g. checks!C2")
COL_LETTERS = Str(pattern=r"^[A-Z]{1,3}$", desc="column letters", hint="column letters such as G")
FUNC_NAME = Str(pattern=r"^[A-Za-z_][A-Za-z0-9._]*$", desc="a function name", hint="a function name such as VLOOKUP")


# ----------------------------------------------------------------------------- marker (design 4.1)

MARKER = Obj(
    {"rotm_model": Const(1, hint="write 1 (the marker format version)"), "name": MODEL_NAME},
    {"created": TEXT, "created_by": TEXT, "plugin_version": TEXT, "spec": FILE_NAME, "workbook": XLSX_NAME,
     "product": Enum(PRODUCTS), "template": TEXT, "description": TEXT, "updated": TEXT},
    desc="the model marker", what="rotm_model.json")


# ----------------------------------------------------------------------------- contract (design 5)

def _contract_rules(obj: dict, path: str, errs: list[SchemaError]) -> None:
    table = obj["table"]
    if obj.get("raw_sheet", f"raw_{table}") != f"raw_{table}":
        errs.append(SchemaError(_join(path, "raw_sheet"), f"'{obj['raw_sheet']}' is not 'raw_{table}'",
                                f"set \"raw_sheet\": \"raw_{table}\" (the raw sheet is always raw_<table>)"))
    if obj.get("excel_table", f"tbl_raw_{table}") != f"tbl_raw_{table}":
        errs.append(SchemaError(_join(path, "excel_table"), f"'{obj['excel_table']}' is not 'tbl_raw_{table}'",
                                f"set \"excel_table\": \"tbl_raw_{table}\""))
    cols = obj["columns"]
    names = [c["name"] for c in cols]
    seen: set[str] = set()
    for i, n in enumerate(names):
        if n.lower() in seen:
            errs.append(SchemaError(f"{_join(path, 'columns')}[{i}].name", f"'{n}' repeats an earlier column name "
                                    "(names are compared without case)", "give every column a different name"))
        seen.add(n.lower())
    if cols and cols[0]["null_rule"] != "never":
        errs.append(SchemaError(f"{_join(path, 'columns')}[0].null_rule",
                                f"the first column '{names[0]}' must never be null: it decides which raw rows exist "
                                "(see references/column-contract.md)", "set \"null_rule\": \"never\", or move a never-null column first"))
    for i, k in enumerate(obj["key"]):
        if k not in names:
            near = nearest(k, names)
            errs.append(SchemaError(f"{_join(path, 'key')}[{i}]", f"'{k}' is not a column of this contract"
                                    + (f"; did you mean '{near}'?" if near else ""),
                                    "use column names from \"columns\""))
    expect = obj["expect"]
    for i, t in enumerate(expect.get("totals", [])):
        if t["column"] not in names:
            near = nearest(t["column"], names)
            errs.append(SchemaError(f"{_join(path, 'expect')}.totals[{i}].column",
                                    f"'{t['column']}' is not a column of this contract"
                                    + (f"; did you mean '{near}'?" if near else ""), "use a numeric column name"))
        else:
            ctype = cols[names.index(t["column"])]["type"]
            if ctype not in ("integer", "number"):
                errs.append(SchemaError(f"{_join(path, 'expect')}.totals[{i}].column",
                                        f"'{t['column']}' has type {ctype}; a control total needs a number column",
                                        "choose an integer or number column"))
    # known exceptions (references/column-contract.md, "Known exceptions")
    for i, c in enumerate(cols):
        if "known_nulls" not in c:
            continue
        where = f"{_join(path, 'columns')}[{i}].known_nulls"
        if c["null_rule"] != "never":
            errs.append(SchemaError(where, f"column '{c['name']}' has null rule {c['null_rule']}, so every empty cell "
                                    "is allowed and there is nothing to agree as known",
                                    "remove known_nulls, or set \"null_rule\": \"never\""))
        elif i == 0:
            errs.append(SchemaError(where, f"the first column '{c['name']}' decides which raw rows exist, so it can "
                                    "have no empty cell", "remove known_nulls from the first column"))
        elif c["name"] in obj["key"]:
            errs.append(SchemaError(where, f"'{c['name']}' is a key column; a key column needs a value in every row",
                                    "remove known_nulls from the key column, or agree a different key"))
    ag = obj.get("agreement")
    if isinstance(ag, dict) and ag.get("status") == "agreed":
        for key in ("reply", "by", "at", "channel", "contract_sha256"):
            if not ag.get(key):
                errs.append(SchemaError(f"{_join(path, 'agreement')}.{key}",
                                        "is empty, but an agreed contract records it",
                                        "record the agreement with rotm.py contract agree (never by hand)"))
        if ag.get("channel") == "task-file" and not ag.get("source_file"):
            errs.append(SchemaError(f"{_join(path, 'agreement')}.source_file",
                                    "is empty, but channel task-file needs the task file that holds the reply",
                                    "record the agreement with rotm.py contract agree --channel task-file "
                                    "--source-file <task.md>"))


def _rows_rules(obj: dict, path: str, errs: list[SchemaError]) -> None:
    if "exact" in obj and ("min" in obj or "max" in obj):
        errs.append(SchemaError(path, "has both 'exact' and 'min'/'max'", "keep 'exact', or keep 'min' and 'max'"))
    elif "exact" not in obj and not ("min" in obj and "max" in obj):
        errs.append(SchemaError(path, "needs 'exact', or both 'min' and 'max'",
                                "write {\"exact\": 600} or {\"min\": 1, \"max\": 100000}"))
    elif "min" in obj and obj["min"] > obj["max"]:
        errs.append(SchemaError(path, f"min {obj['min']} is greater than max {obj['max']}", "swap them"))


KNOWN = Obj({"max": Int(1)}, {"note": TEXT}, desc="an agreed known exception",
            what="a known exception ({\"max\": N, \"note\": \"what is known\"})")

CONTRACT_COLUMN = Obj(
    {"name": COLUMN, "type": Enum(COLUMN_TYPES), "null_rule": Enum(NULL_RULES)},
    {"unit": UNIT, "allowed": Nullable(Arr(SCALAR, min_items=1)), "source_expression": Nullable(TEXT),
     "description": TEXT, "format": FORMAT_KEY, "observed": AnyValue(), "known_nulls": KNOWN},
    desc="a contract column", what="a contract column")

CONTRACT = Obj(
    {"rotm_contract": Const(1, hint="write 1 (the contract format version)"), "table": TABLE,
     "grain": Str(min_len=1, desc="the grain of a row", hint="what one row is, e.g. \"one row per invoice line\""),
     "key": Arr(COLUMN, min_items=1, unique=True, desc="a list of key column names"),
     "columns": Arr(CONTRACT_COLUMN, min_items=1),
     "expect": Obj({}, {"rows": Obj({}, {"exact": Int(0), "min": Int(0), "max": Int(0)}, rules=[_rows_rules],
                                    desc="the expected row count"),
                        "totals": Arr(Obj({"column": COLUMN, "value": Num()}, {"tolerance": Num(0), "note": TEXT},
                                          desc="a control total")),
                        "key_repeats": KNOWN},
                   at_least_one=("rows", "totals"), desc="the expected rows or control totals",
                   what="expect")},
    {"raw_sheet": SHEET_NAME, "excel_table": Str(pattern=r"^tbl_raw_[a-z][a-z0-9_]{0,19}$", desc="the Excel table name"),
     "purpose": TEXT, "description": TEXT, "notes": Arr(TEXT),
     "source": Nullable(Obj({}, {"connector": Enum(CONNECTORS), "system": Nullable(TEXT), "object": Nullable(TEXT),
                                 "from": Nullable(TEXT), "where": Nullable(TEXT)},
                            extra=OneOf(Str(), Null(), desc="text or null"), desc="the source")),
     "refresh": Obj({}, {"frequency": Nullable(TEXT), "capacity_rows": Nullable(Int(1)),
                         "allow_empty": Bool()}, desc="the refresh settings"),
     "agreement": Obj({"status": Enum(("proposed", "agreed"))},
                      {"contract_sha256": Nullable(SHA256), "reply": Nullable(TEXT), "by": Nullable(TEXT),
                       "at": Nullable(TEXT), "channel": Nullable(Enum(CHANNELS)), "source_file": Nullable(TEXT),
                       "recorded_by": Nullable(TEXT), "note": Nullable(TEXT), "version": Nullable(Int(1)),
                       "reply_reused_from": Nullable(Int(1)), "history": Arr(AnyValue()),
                       # the table the reply was recorded for (v2, C-sol-13; written by rotm.py contract agree)
                       "table": Nullable(TABLE),
                       # the content hash of the JSON that contracts/<t>.contract.md was last rendered from (v3,
                       # C-sol-3; written by contract propose, show and agree; outside the hashed content)
                       "shown_sha256": Nullable(SHA256)},
                      desc="the agreement record", what="agreement"),
     "proposed_at": TEXT, "proposed_by": TEXT, "version": Int(1)},
    rules=[_contract_rules], desc="a column contract", what="the contract")


# ----------------------------------------------------------------------------- model specification (design 6)

def _valid_rules(obj: dict, path: str, errs: list[SchemaError]) -> None:
    if "list" in obj and ("min" in obj or "max" in obj):
        errs.append(SchemaError(path, "has both 'list' and 'min'/'max'", "keep one kind of validation"))
    if "min" in obj and "max" in obj and obj["min"] > obj["max"]:
        errs.append(SchemaError(path, f"min {obj['min']} is greater than max {obj['max']}", "swap them"))


VALID = Obj({}, {"min": Num(), "max": Num(), "list": Arr(SCALAR, min_items=1)},
            at_least_one=("min", "max", "list"), rules=[_valid_rules], desc="a validation",
            what="valid ({\"min\": 0, \"max\": 1} or {\"list\": [...]})")

INPUT_TYPES = ("number", "integer", "date", "text", "boolean")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _input_scalar_rules(obj: dict, path: str, errs: list[SchemaError]) -> None:
    t, v = obj.get("type"), obj["value"]
    if t == "date" and not (isinstance(v, str) and _ISO_DATE.match(v)):
        errs.append(SchemaError(_join(path, "value"), f"{_show(v)} is not a date in the form YYYY-MM-DD",
                                "write the date as text, e.g. \"2026-01-31\""))
    elif t in ("number", "integer") and (isinstance(v, bool) or not isinstance(v, (int, float))):
        errs.append(SchemaError(_join(path, "value"), f"{_show(v)} is not a number but the type is {t}",
                                "write the number without quotes"))
    elif t == "integer" and isinstance(v, float) and not v.is_integer():
        errs.append(SchemaError(_join(path, "value"), f"{v} is not a whole number but the type is integer",
                                "write a whole number, or set \"type\": \"number\""))
    elif t == "boolean" and not isinstance(v, bool):
        errs.append(SchemaError(_join(path, "value"), f"{_show(v)} is not true/false but the type is boolean",
                                "write true or false"))
    elif t == "text" and not isinstance(v, str):
        errs.append(SchemaError(_join(path, "value"), f"{_show(v)} is not text but the type is text",
                                "write the value in double quotes"))


def _input_grid_rules(obj: dict, path: str, errs: list[SchemaError]) -> None:
    nr, nc = len(obj["rows"]["values"]), len(obj["cols"]["values"])
    vals = obj["values"]
    if len(vals) != nr:
        errs.append(SchemaError(_join(path, "values"), f"has {len(vals)} rows; rows.values has {nr}",
                                f"give one list of {nc} values per row"))
        return
    for i, row in enumerate(vals):
        if len(row) != nc:
            errs.append(SchemaError(f"{_join(path, 'values')}[{i}]", f"has {len(row)} values; cols.values has {nc}",
                                    f"give {nc} values in this row"))


_INPUT_COMMON = {"format": FORMAT_KEY, "unit": UNIT, "source": TEXT, "note": TEXT, "valid": VALID,
                 "type": Enum(INPUT_TYPES)}
INPUT_SCALAR = Obj({"id": ID, "label": NONEMPTY, "value": SCALAR}, {"kind": Const("scalar"), **_INPUT_COMMON},
                   rules=[_input_scalar_rules], desc="a scalar input", what="a scalar input item")
INPUT_LIST = Obj({"id": ID, "kind": Const("list"), "label": NONEMPTY, "values": Arr(SCALAR, min_items=1)},
                 dict(_INPUT_COMMON), desc="a list input", what="a list input item")
_HEADERS = Obj({"values": Arr(SCALAR, min_items=1)}, {"format": FORMAT_KEY, "label": TEXT},
               desc="headers {\"values\": [...]}", what="the headers")
INPUT_GRID = Obj({"id": ID, "kind": Const("grid"), "label": NONEMPTY, "rows": _HEADERS, "cols": _HEADERS,
                  "values": Arr(Arr(SCALAR_OR_NULL), min_items=1, desc="a list of rows, each a list of values")},
                 dict(_INPUT_COMMON), rules=[_input_grid_rules], desc="a grid input", what="a grid input item")
INPUT_ITEM = Switch("kind", {"scalar": INPUT_SCALAR, "list": INPUT_LIST, "grid": INPUT_GRID},
                    default=INPUT_SCALAR, what="input item")

ROWS = Obj({}, {"values": Arr(SCALAR, min_items=1), "in": ID, "same_as": ID, "format": FORMAT_KEY,
                "label": TEXT},        # label: written in B of the column-header row (model-spec-format.md)
           exactly_one=("values", "in", "same_as"), desc="rows", what="rows ({\"values\"}, {\"in\"} or {\"same_as\"})")
TIMELINE = Obj({"start": ID, "periods": Int(1, 1200), "step": Enum(("month",))}, desc="a timeline",
               what="timeline ({\"start\": <input id>, \"periods\": N, \"step\": \"month\"})")
COLS = Obj({}, {"values": Arr(SCALAR, min_items=1), "in": ID, "timeline": TIMELINE, "same_as": ID,
                "format": FORMAT_KEY},
           exactly_one=("values", "in", "timeline", "same_as"), desc="columns",
           what="cols ({\"values\"}, {\"in\"}, {\"timeline\"} or {\"same_as\"})")
_BLOCK_COMMON = {"title": TEXT, "unit": UNIT, "format": FORMAT_KEY, "note": TEXT, "source": TEXT}
LINE_ITEM = Obj({"id": ID, "label": NONEMPTY, "expr": EXPR},
                {"unit": UNIT, "format": FORMAT_KEY, "first": EXPR, "key": Bool(), "note": TEXT},
                desc="a line item", what="a line item")
MEASURE = Obj({"id": ID, "label": NONEMPTY, "expr": EXPR},
              {"unit": UNIT, "format": FORMAT_KEY, "total": Enum(("sum", "same", "none")), "key": Bool(),
               "note": TEXT}, desc="a measure", what="a measure")
GRID_BLOCK = Obj({"id": ID, "rows": ROWS, "cell": EXPR},
                 {**_BLOCK_COMMON, "cols": COLS, "total": Obj({}, {"row": Bool(), "col": Bool()}, desc="totals"),
                  "key": Bool()}, desc="a grid block", what="a grid block")
ITEMS_BLOCK = Obj({"id": ID, "items": Arr(LINE_ITEM, min_items=1)}, {**_BLOCK_COMMON, "cols": COLS},
                  desc="an items block", what="an items block")
MEASURES_BLOCK = Obj({"id": ID, "rows": ROWS, "measures": Arr(MEASURE, min_items=1)}, dict(_BLOCK_COMMON),
                     desc="a measures block", what="a measures block")
BLOCK = ShapeSwitch({"cell": ("grid", GRID_BLOCK), "items": ("items", ITEMS_BLOCK),
                     "measures": ("measures", MEASURES_BLOCK)}, what="a block")

HELPER_COLUMN = Obj({"id": ID, "expr": EXPR},
                    {"label": TEXT, "format": FORMAT_KEY, "unit": UNIT, "type": Enum(COLUMN_TYPES), "note": TEXT},
                    desc="a helper column", what="a helper column")

_CHECK_COMMON = {"severity": Enum(("error", "alert")), "note": TEXT, "targets": Arr(TEXT), "format": FORMAT_KEY}
CHECK = Switch("kind", {
    "equal": Obj({"id": ID, "kind": Const("equal"), "label": NONEMPTY, "actual": EXPR_OR_NUM,
                  "expected": EXPR_OR_NUM}, {**_CHECK_COMMON, "tolerance": ID},
                 desc="an equal check", what="an equal check"),
    "range": Obj({"id": ID, "kind": Const("range"), "label": NONEMPTY, "actual": EXPR_OR_NUM,
                  "min": EXPR_OR_NUM, "max": EXPR_OR_NUM}, dict(_CHECK_COMMON),
                 desc="a range check", what="a range check"),
    "zero": Obj({"id": ID, "kind": Const("zero"), "label": NONEMPTY, "actual": EXPR_OR_NUM}, dict(_CHECK_COMMON),
                desc="a zero check", what="a zero check"),
    "at_most": Obj({"id": ID, "kind": Const("at_most"), "label": NONEMPTY, "actual": EXPR_OR_NUM,
                    "bound": EXPR_OR_NUM}, dict(_CHECK_COMMON), desc="an at_most check", what="an at_most check"),
}, what="check")

NOTE = Obj({"text": NONEMPTY}, {"source": TEXT}, desc="a note", what="a note")

_SHEET_COMMON = {"font_size": Enum((8, 9), hint="use 8 (dense sheets: a brick wall of data) or 9 (sheets with "
                                                "white space); Max's rule, spec 1.2"),
                 "title": TEXT, "purpose": TEXT, "note": TEXT}


def _sheet(type_name: str, required: dict[str, Node] | None = None, optional: dict[str, Node] | None = None,
           rules: Sequence[Rule] = ()) -> Obj:
    return Obj({"name": SHEET_NAME, "type": Const(type_name), **(required or {})},
               {**_SHEET_COMMON, **(optional or {})}, rules=rules, desc=f"a {type_name} sheet",
               what=f"a {type_name} sheet")


def _raw_sheet_rules(obj: dict, path: str, errs: list[SchemaError]) -> None:
    if obj["name"] != f"raw_{obj['table']}":
        errs.append(SchemaError(_join(path, "name"), f"'{obj['name']}' is not 'raw_{obj['table']}'",
                                f"name the raw sheet of table '{obj['table']}' \"raw_{obj['table']}\""))


def _helper_sheet_rules(obj: dict, path: str, errs: list[SchemaError]) -> None:
    if obj["name"] != f"h_{obj['table']}":
        errs.append(SchemaError(_join(path, "name"), f"'{obj['name']}' is not 'h_{obj['table']}'",
                                f"name the helper sheet of table '{obj['table']}' \"h_{obj['table']}\""))


SHEET = Switch("type", {
    "cover": _sheet("cover"),
    "inputs": _sheet("inputs", optional={"items": Arr(INPUT_ITEM)}),
    "raw": _sheet("raw", {"table": TABLE}, {"landing": Enum(("static", "power_query"))}, rules=[_raw_sheet_rules]),
    "helpers": _sheet("helpers", {"table": TABLE}, {"fetch": Arr(COLUMN, unique=True), "columns": Arr(HELPER_COLUMN)},
                      rules=[_helper_sheet_rules]),
    "calc": _sheet("calc", {"blocks": Arr(BLOCK, min_items=1)}),
    "output": _sheet("output", {"blocks": Arr(BLOCK, min_items=1)}),
    "checks": _sheet("checks", optional={"checks": Arr(CHECK)}),
    "notes": _sheet("notes", optional={"notes": Arr(NOTE)}),
}, what="sheet")

SETTINGS = Obj({}, {
    "depth_limit": Int(1, 64, desc="a whole number from 1 to 64 (Excel allows 64 nesting levels)"),
    "length_limit": Int(10, 8192, desc="a whole number of characters from 10 to 8192 (Excel's formula limit)"),
    "allowed_literals": Arr(Num(), desc="a list of numbers allowed inside formulas, e.g. [0, 1]"),
    "check_tolerance": Num(0, desc="a number of at least 0"),
    "helper_spare_rows": Int(0, 1_000_000),
    "formula_key_check_max_rows": Int(0, 1_048_575),
    "allow_functions": Arr(FUNC_NAME, desc="a list of function names accepted for this model, e.g. [\"VLOOKUP\"]"),
}, desc="the settings", what="settings")

SPEC = Obj(
    {"rotm_spec": Const(1, hint="write 1 (the specification format version)"), "name": MODEL_NAME,
     "title": NONEMPTY, "sheets": Arr(SHEET, min_items=1)},
    {"purpose": TEXT, "output": XLSX_NAME, "product": Enum(PRODUCTS), "settings": SETTINGS,
     "dictionary": Nullable(Str(min_len=1, desc="the path of a dictionary override file")),
     "limitations": Arr(TEXT), "description": TEXT, "note": TEXT},
    desc="a model specification", what="the specification")


# ----------------------------------------------------------------------------- style dictionary (design 10)

def _color_ref() -> Node:
    return Nullable(Str(pattern=r"^[A-Za-z][A-Za-z0-9_]*$", desc="a colour key",
                        hint="a key of 'colors', e.g. text"))


_BORDER_SIDE = Obj({"style": Enum(BORDER_STYLES)}, {"color": Str(pattern=r"^[A-Za-z][A-Za-z0-9_]*$",
                                                                 desc="a colour key")},
                   desc="a border side", what="a border side")
BORDER = Nullable(Obj({}, {s: _BORDER_SIDE for s in BORDER_SIDES}, desc="a border",
                      what="a border ({\"bottom\": {\"style\": \"thin\", \"color\": \"rule_line\"}})"))

CELL_TYPE = Obj({k: Leaf(n) for k, n in {
    "meaning": Str(), "font_color": _color_ref(), "bold": Bool(), "italic": Bool(), "fill": _color_ref(),
    "border": BORDER, "number_format": Str(pattern=r"^[A-Za-z][A-Za-z0-9_]*$", desc="\"item\" or a number-format key"),
    "align": Enum(ALIGNS), "wrap": Bool(), "locked": Bool(),
    "size": Enum(("sheet", "spaced", "dense")),
}.items()}, desc="a cell type", what="a cell type")

SHEET_TYPE_VIEW = Obj({"size": Leaf(Enum(("dense", "spaced"))), "gridlines": Leaf(Bool()),
                       "zoom": Leaf(Int(10, 400)), "freeze": Leaf(Nullable(CELL))},
                      desc="a sheet type", what="a sheet type")

RULES = Obj({}, {"depth_limit": Leaf(Int(1, 64)), "length_limit": Leaf(Int(10, 8192)),
                 "allowed_literals": Leaf(Arr(Num())), "check_tolerance": Leaf(Num(0)),
                 "helper_spare_rows": Leaf(Int(0, 1_000_000)), "formula_key_check_max_rows": Leaf(Int(0, 1_048_575))},
            extra=Leaf(AnyValue()), desc="the rules", what="rules")

_DICT_BODY: dict[str, Node] = {
    "font": Leaf(Str(min_len=1, desc="a font name")),
    "sizes": Obj({"dense": Leaf(Int(1, 409)), "spaced": Leaf(Int(1, 409))}, desc="the sizes", what="sizes"),
    "sheet_types": MapOf(SHEET_TYPE_VIEW, key_pattern=r"^[a-z][a-z0-9_]*$", required_keys=SHEET_TYPES,
                         desc="the sheet types"),
    "colors": MapOf(Leaf(HEX6), key_pattern=r"^[A-Za-z][A-Za-z0-9_]*$", desc="the colours"),
    "number_formats": MapOf(Leaf(Str(min_len=1)), key_pattern=r"^[A-Za-z][A-Za-z0-9_]*$",
                            desc="the number formats"),
    "contract_type_formats": MapOf(Leaf(Str(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")), key_pattern=r"^[a-z]+$",
                                   required_keys=COLUMN_TYPES, desc="the formats per contract type"),
    "cell_types": MapOf(CELL_TYPE, key_pattern=r"^[a-z][a-z0-9_]*$", min_items=1, desc="the cell types"),
    "rules": RULES,
}

# "set" is checked against DICTIONARY_PARTIAL by _dictionary_rules (defined below).
NAMED_OVERRIDE = Obj({"meaning": NONEMPTY, "source": NONEMPTY,
                      "set": Obj({}, {}, extra=AnyValue(), desc="a partial dictionary")},
                     desc="a named override", what="a named override")


def _dictionary_rules(obj: dict, path: str, errs: list[SchemaError]) -> None:
    """Cross references inside one merged dictionary: colour keys, number-format keys, contract formats."""
    colors = set(obj.get("colors", {}))
    formats = set(obj.get("number_formats", {}))
    for ct_name, ct in obj.get("cell_types", {}).items():
        base = f"cell_types.{ct_name}"
        for attr in ("font_color", "fill"):
            key = ct[attr]["value"]
            if key is not None and key not in colors:
                near = nearest(key, colors)
                errs.append(SchemaError(f"{base}.{attr}.value", f"colour key '{key}' is not in 'colors'"
                                        + (f"; did you mean '{near}'?" if near else ""),
                                        "use a key of 'colors' or add the colour there"))
        border = ct["border"]["value"]
        for side, spec in (border or {}).items():
            key = spec.get("color")
            if key is not None and key not in colors:
                errs.append(SchemaError(f"{base}.border.value.{side}.color", f"colour key '{key}' is not in 'colors'",
                                        "use a key of 'colors' or add the colour there"))
        nf = ct["number_format"]["value"]
        if nf != "item" and nf not in formats:
            near = nearest(nf, formats)
            errs.append(SchemaError(f"{base}.number_format.value", f"'{nf}' is not 'item' or a key of number_formats"
                                    + (f"; did you mean '{near}'?" if near else ""),
                                    "use \"item\" or a key of number_formats"))
    for t, leaf in obj.get("contract_type_formats", {}).items():
        if leaf["value"] not in formats:
            errs.append(SchemaError(f"contract_type_formats.{t}.value", f"'{leaf['value']}' is not a key of "
                                    "number_formats", "use a key of number_formats"))
    for name, ov in obj.get("named_overrides", {}).items():
        sub: list[SchemaError] = []
        DICTIONARY_PARTIAL.check(ov["set"], f"named_overrides.{name}.set", sub)
        errs.extend(sub)


DICTIONARY = Obj(
    {"rotm_dictionary": Const(1, hint="write 1 (the dictionary format version)"),
     "sources": MapOf(NONEMPTY, key_pattern=r"^[A-Z][A-Z0-9_]*$", min_items=1,
                      key_hint="upper-case letters, e.g. MAX", desc="the sources"),
     **_DICT_BODY},
    {"name": TEXT, "version": TEXT, "note": TEXT,
     "named_overrides": MapOf(NAMED_OVERRIDE, key_pattern=r"^[a-z][a-z0-9_]*$", desc="the named overrides")},
    rules=[_dictionary_rules], desc="a style dictionary", what="the style dictionary")

DICTIONARY_PARTIAL = Obj({}, {k: n.partial() for k, n in _DICT_BODY.items()}
                         | {"sources": MapOf(NONEMPTY, key_pattern=r"^[A-Z][A-Z0-9_]*$", desc="the sources")},
                         desc="a partial dictionary", what="a partial dictionary")

DICTIONARY_OVERRIDE = Obj(
    {"rotm_dictionary_override": Const(1, hint="write 1 (the override format version)")},
    {"use": Arr(Str(pattern=r"^[a-z][a-z0-9_]*$", desc="a named override"), unique=True),
     "set": DICTIONARY_PARTIAL, "name": TEXT, "note": TEXT},
    desc="a dictionary override", what="the dictionary override file")


# ----------------------------------------------------------------------------- manifest (design 6.8)

_M_SHEET_COMMON = {"name": Str(min_len=1, desc="a sheet name"), "font_size": Int(1, 409)}
_M_EXTRA = AnyValue()
M_HELPER_COLUMN = Obj({"id": Str(min_len=1), "col": COL_LETTERS,
                       "kind": Enum(("row", "flag", "fetch", "derived", "dupkey", "dup", "nullflag")),
                       "formula": Str(min_len=1)},
                      {"raw_column": Str(), "label": Str(), "header": Str(), "format": Str(), "type": Str()},
                      extra=_M_EXTRA, desc="a helper column entry")
M_BLOCK = Obj({"id": Str(min_len=1), "shape": Enum(BLOCK_SHAPES), "body": RANGE},
              {"total": Nullable(RANGE), "checks": Arr(Str()), "title": Str()}, extra=_M_EXTRA, desc="a block entry")


def _m_sheet(type_name: str, required: dict[str, Node] | None = None,
             optional: dict[str, Node] | None = None) -> Obj:
    return Obj({**_M_SHEET_COMMON, "type": Const(type_name), **(required or {})}, optional or {},
               extra=_M_EXTRA, desc=f"a {type_name} sheet entry", what=f"a {type_name} sheet entry")


M_SHEET = Switch("type", {
    "cover": _m_sheet("cover"), "inputs": _m_sheet("inputs"), "notes": _m_sheet("notes"),
    "raw": _m_sheet("raw", {"table": TABLE}, {
        "excel_table": Str(), "landing": Enum(("static", "power_query")), "query_name": Nullable(Str()),
        "columns": Arr(Str()), "types": Arr(Enum(COLUMN_TYPES)), "rows": Int(0), "digest": Nullable(DIGEST),
        "snapshot": Nullable(Str())}),
    "helpers": _m_sheet("helpers", {"table": TABLE}, {
        "first_row": Int(1), "last_row": Int(1), "capacity": Int(0), "columns": Arr(M_HELPER_COLUMN)}),
    "calc": _m_sheet("calc", optional={"blocks": Arr(M_BLOCK)}),
    "output": _m_sheet("output", optional={"blocks": Arr(M_BLOCK)}),
    "checks": _m_sheet("checks", optional={"master": CELL, "results": RANGE, "ids": RANGE}),
}, what="manifest sheet entry")

M_CHECK = Obj({"id": Str(min_len=1), "result": REF},
              {"kind": Enum(CHECK_KINDS), "auto": Bool(), "targets": Arr(Str()), "label": Str(),
               "severity": Enum(("error", "alert"))}, extra=_M_EXTRA, desc="a check entry")

MANIFEST = Obj(
    {"rotm_manifest": Const(1, hint="write 1 (the manifest format version)"), "workbook": Str(min_len=1),
     "sheets": Arr(M_SHEET)},
    {"plugin_version": Str(), "built_at": Str(), "updated_at": Str(), "spec": Nullable(Str()),
     "spec_sha256": Nullable(SHA256), "dictionary_sha256": Nullable(SHA256), "dictionary_applied": Arr(Str()),
     "settings": Obj({}, {}, extra=AnyValue(), desc="the settings"),
     "inputs": MapOf(REF, desc="input id -> cell"), "cells": MapOf(REF, desc="key output id -> cell"),
     "checks": Arr(M_CHECK), "master_check": Nullable(REF), "master_links": Arr(REF),
     "cell_types": MapOf(Arr(Tup(RANGE, Str(pattern=r"^[a-z][a-z0-9_]*$", desc="a cell type"),
                                 desc="[range, cell type]")), desc="sheet -> [[range, cell type], ...]")},
    extra=_M_EXTRA, desc="a manifest", what="the manifest")


# ----------------------------------------------------------------------------- status (design 4.2)

STATUS_ENTRY = Obj({}, {"at": Str(), "exit_code": Int(0, 255), "sha256": Nullable(SHA256),
                        "input_sha256": Nullable(SHA256), "output_sha256": Nullable(SHA256)},
                   extra=AnyValue(), desc="a step record")
STATUS = Obj({"rotm_status": Const(1), "workbooks": MapOf(MapOf(STATUS_ENTRY, key_pattern=r"^[a-z][a-z0-9_]*$"),
                                                          desc="workbook -> step -> record")},
             desc="the status file", what="reports/status.json")


# ----------------------------------------------------------------------------- report (design 3.3)

FINDING = Obj({"rule": Str(min_len=1), "severity": Enum(SEVERITIES), "message": Str()},
              {"sheet": Nullable(Str()), "cell": Nullable(Str()), "count": Int(1), "cells": Arr(Str()),
               "fix": Str(), "data": Obj({}, {}, extra=AnyValue())}, desc="a finding")
REPORT = Obj(
    {"rotm_report": Const(1), "tool": Const("rule-of-thumb-model"), "version": Str(), "command": Str(min_len=1),
     "target": Str(), "at": Str(), "strict": Bool(), "exit_code": Int(0, 255), "status": Enum(REPORT_STATUSES),
     "summary": Obj({"errors": Int(0), "warnings": Int(0), "info": Int(0)}, desc="the summary"),
     "findings": Arr(FINDING), "notes": Arr(Str()), "verified": Obj({}, {}, extra=Bool(), desc="what was verified"),
     "data": Obj({}, {}, extra=AnyValue())},
    desc="a report", what="the report")


# ----------------------------------------------------------------------------- accepted controls (v2, lib.controls)

CONTROL_TOTAL = Obj({"column": COLUMN, "value": Num(), "tolerance": Num(0)}, desc="an accepted control total",
                    what="an accepted control total")
CONTROL_ACCEPTANCE = Obj({"digest": DIGEST, "rows": Int(0), "totals": Arr(CONTROL_TOTAL), "given": Arr(Str()),
                          "at": Str(min_len=1)}, desc="an accepted snapshot", what="an accepted snapshot")
CONTROLS = Obj({"rotm_controls": Const(1, hint="write 1 (the controls format version)"), "table": TABLE,
                "accepted": Arr(CONTROL_ACCEPTANCE)}, desc="the accepted control totals",
               what="raw/<table>.controls.json")


SCHEMAS: dict[str, Node] = {
    "marker": MARKER, "contract": CONTRACT, "spec": SPEC, "dictionary": DICTIONARY,
    "dictionary_override": DICTIONARY_OVERRIDE, "manifest": MANIFEST, "status": STATUS, "report": REPORT,
    "controls": CONTROLS,
}


def validate(obj: Any, name: str) -> list[SchemaError]:
    """Every problem of `obj` against schema `name` (empty list when valid)."""
    if name not in SCHEMAS:
        raise ValueError(f"unknown schema {name!r}; known: {', '.join(SCHEMA_NAMES)}")
    errs: list[SchemaError] = []
    SCHEMAS[name].check(obj, ROOT, errs)
    return errs


def require_valid(obj: Any, name: str, source: str) -> None:
    """Raise common.Refusal listing every SchemaError (prefixed with `source`, e.g. the file name)."""
    errs = validate(obj, name)
    if errs:
        from .common import Refusal
        raise Refusal(f"{source} is not valid ({len(errs)} problem{'s' if len(errs) != 1 else ''}); "
                      "nothing was written", details=[e.line() for e in errs],
                      fix="change the keys named below")

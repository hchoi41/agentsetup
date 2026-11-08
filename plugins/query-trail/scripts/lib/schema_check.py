"""Minimal JSON Schema (draft 2020-12 subset) validator for run_config and manifest (spec 1.6, 2.2 P0, P6).

The `jsonschema` package is not installed in the project env (checked 2026-11-07), so this module
covers exactly the keywords config/run_config.schema.json and config/manifest.schema.json use:
type, enum, const, required, properties, additionalProperties, items, minimum, maximum,
exclusiveMinimum, minLength, maxLength, pattern, uniqueItems, oneOf, anyOf, allOf, not,
if/then/else and local `$ref` (#/$defs/...). `format` is not asserted except `date-time`
and `date`, which are checked by parsing. Unknown keywords are ignored, never treated as a pass
of something they would forbid: the stage-2 schemas use none.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def load_schema(path: Path) -> dict[str, Any]:
    """Read a schema file."""
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _ok_format(fmt: str, v: Any) -> bool:
    if not isinstance(v, str):
        return True
    try:
        if fmt == "date-time":
            dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
        elif fmt == "date":
            dt.date.fromisoformat(v)
    except ValueError:
        return False
    return True


def _resolve(root: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise ValueError(f"only local $ref is supported: {ref}")
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _errors(root: dict[str, Any], sch: Any, v: Any, path: str) -> list[str]:
    if sch is True or sch == {}:
        return []
    if sch is False:
        return [f"{path}: not allowed"]
    out: list[str] = []
    if "$ref" in sch:
        out += _errors(root, _resolve(root, sch["$ref"]), v, path)
    t = sch.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_TYPES[x](v) for x in types):
            return out + [f"{path}: expected {'/'.join(types)}, got {type(v).__name__}"]
    if "const" in sch and v != sch["const"]:
        out.append(f"{path}: must equal {sch['const']!r}")
    if "enum" in sch and v not in sch["enum"]:
        out.append(f"{path}: {v!r} not in {sch['enum']!r}")
    if isinstance(v, str):
        if "minLength" in sch and len(v) < sch["minLength"]:
            out.append(f"{path}: shorter than {sch['minLength']}")
        if "maxLength" in sch and len(v) > sch["maxLength"]:
            out.append(f"{path}: longer than {sch['maxLength']}")
        if "pattern" in sch and not re.search(sch["pattern"], v):
            out.append(f"{path}: does not match {sch['pattern']}")
        if "format" in sch and not _ok_format(sch["format"], v):
            out.append(f"{path}: not a valid {sch['format']}")
    if _TYPES["number"](v):
        if "minimum" in sch and v < sch["minimum"]:
            out.append(f"{path}: below minimum {sch['minimum']}")
        if "maximum" in sch and v > sch["maximum"]:
            out.append(f"{path}: above maximum {sch['maximum']}")
        if "exclusiveMinimum" in sch and v <= sch["exclusiveMinimum"]:
            out.append(f"{path}: not above {sch['exclusiveMinimum']}")
    if isinstance(v, dict):
        for k in sch.get("required", []):
            if k not in v:
                out.append(f"{path}: missing required {k!r}")
        props = sch.get("properties", {})
        for k, sub in props.items():
            if k in v:
                out += _errors(root, sub, v[k], f"{path}/{k}")
        ap = sch.get("additionalProperties", True)
        for k in v:
            if k not in props:
                if ap is False:
                    out.append(f"{path}: unexpected property {k!r}")
                elif isinstance(ap, dict):
                    out += _errors(root, ap, v[k], f"{path}/{k}")
    if isinstance(v, list):
        if "items" in sch:
            for i, item in enumerate(v):
                out += _errors(root, sch["items"], item, f"{path}/{i}")
        if sch.get("uniqueItems"):
            seen = [json.dumps(x, sort_keys=True) for x in v]
            if len(seen) != len(set(seen)):
                out.append(f"{path}: items are not unique")
    for sub in sch.get("allOf", []):
        out += _errors(root, sub, v, path)
    if "anyOf" in sch and not any(not _errors(root, s, v, path) for s in sch["anyOf"]):
        out.append(f"{path}: matches none of anyOf")
    if "oneOf" in sch:
        n = sum(1 for s in sch["oneOf"] if not _errors(root, s, v, path))
        if n != 1:
            out.append(f"{path}: matches {n} of oneOf, expected exactly 1")
    if "not" in sch and not _errors(root, sch["not"], v, path):
        out.append(f"{path}: matches a forbidden schema (not)")
    if "if" in sch:
        branch = sch.get("then") if not _errors(root, sch["if"], v, path) else sch.get("else")
        if branch is not None:
            out += _errors(root, branch, v, path)
    return out


def validate(instance: Any, schema: dict[str, Any]) -> list[str]:
    """Return every violation as 'path: message'; an empty list means valid."""
    return _errors(schema, schema, instance, "$")

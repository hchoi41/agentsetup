"""Load and merge the style dictionary (design section 10).

Owner: CORE-IO (the dictionary's content, references/style_dictionary.json, belongs to M2).

Layers, in order (design 10.4): the plugin dictionary (or `base`), the named overrides given in `use`
(for example `theme --use input_blue_font`), then a project override file `<model>/style_overrides.json`
(its own "use" list first, then its "set" part). Every layer is schema-checked, and the merged result is
checked again, so a colour key or number-format key that does not exist is refused with its JSON path.
The plugin's own file is never edited for a project.

A leaf {"value": ..., "source": ...} is replaced as a whole by an override (a border given in an override
replaces the border; it is not merged side by side).
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

OVERRIDE_FILE = "style_overrides.json"
DICTIONARY_FILE = "style_dictionary.json"
FAIL_COLOR_KEY = "fail_text"            # the [Red] of the check number format (design 9.5)


def default_path() -> Path:
    """SKILL/references/style_dictionary.json."""
    from . import common
    return common.references_dir() / DICTIONARY_FILE


@dataclass(frozen=True)
class CellStyle:
    """The resolved look of one cell type (colours as 6-digit upper-case hex or None)."""
    type: str
    font_color: str | None
    bold: bool
    italic: bool
    fill: str | None
    border: dict[str, Any] | None      # e.g. {"bottom": {"style": "thin", "color": "808080"}}
    number_format: str                 # "item" or a number-format string
    align: str                         # left | center | right | type (raw: numbers right, text left) | general
    wrap: bool
    locked: bool
    size: str                          # "sheet", "spaced" or "dense"
    sources: dict[str, str] = field(default_factory=dict)   # attribute -> source
    number_format_key: str | None = None                     # the number_formats key, None for "item"


def _is_leaf(node: Any) -> bool:
    return isinstance(node, dict) and "value" in node and "source" in node


@dataclass
class Dictionary:
    data: dict[str, Any]               # merged JSON
    path: Path                         # the plugin dictionary file
    font_name: str
    sizes: dict[str, int]              # {"dense": 8, "spaced": 9}
    applied: list[str] = field(default_factory=list)   # named overrides and override files applied, in order
    sha256: str = ""                   # canonical hash of `data`
    _cache: dict[str, CellStyle] = field(default_factory=dict, repr=False, compare=False)

    # -- lookups
    def color(self, key: str | None) -> str | None:
        """The hex value of a key of `colors`; None stays None."""
        if key is None:
            return None
        from . import common, schemas
        colors = self.data["colors"]
        if key not in colors:
            near = schemas.nearest(key, colors)
            raise common.Refusal(f"colour key '{key}' is not in the dictionary"
                                 + (f"; did you mean '{near}'?" if near else ""),
                                 fix="use a key of 'colors' in the style dictionary")
        return str(colors[key]["value"]).upper()

    def cell_types(self) -> list[str]:
        return list(self.data["cell_types"])

    def style(self, cell_type: str) -> CellStyle:
        """The look of `cell_type` with colours and the number format resolved."""
        if cell_type in self._cache:
            return self._cache[cell_type]
        from . import common, schemas
        types = self.data["cell_types"]
        if cell_type not in types:
            near = schemas.nearest(cell_type, types)
            raise common.Refusal(f"cell type '{cell_type}' is not in the dictionary"
                                 + (f"; did you mean '{near}'?" if near else ""),
                                 fix="use one of: " + ", ".join(types))
        ct = types[cell_type]

        def v(attr: str) -> Any:
            return ct[attr]["value"]

        border = None
        if v("border"):
            border = {side: {"style": spec["style"], "color": self.color(spec.get("color")) if spec.get("color")
                             else None} for side, spec in v("border").items()}
        nf_key = v("number_format")
        style = CellStyle(
            type=cell_type, font_color=self.color(v("font_color")), bold=bool(v("bold")), italic=bool(v("italic")),
            fill=self.color(v("fill")), border=border,
            number_format="item" if nf_key == "item" else self.number_format(nf_key),
            align=v("align"), wrap=bool(v("wrap")), locked=bool(v("locked")), size=v("size"),
            sources={attr: leaf["source"] for attr, leaf in ct.items() if _is_leaf(leaf)},
            number_format_key=None if nf_key == "item" else nf_key)
        self._cache[cell_type] = style
        return style

    def sheet_size(self, sheet_type: str, override: int | None = None) -> int:
        """8 or 9 for a sheet type (design 6.2, 10.2); `override` (8 or 9) wins."""
        from . import common, schemas
        if override is not None:
            if override not in (8, 9) or isinstance(override, bool):
                raise common.Refusal(f"font size {override} is not allowed",
                                     fix="use 8 (dense sheets) or 9 (sheets with white space); Max's rule, spec 1.2")
            return override
        views = self.data["sheet_types"]
        if sheet_type not in views:
            near = schemas.nearest(sheet_type, views)
            raise common.Refusal(f"sheet type '{sheet_type}' is not in the dictionary"
                                 + (f"; did you mean '{near}'?" if near else ""),
                                 fix="use one of: " + ", ".join(views))
        return int(self.sizes[views[sheet_type]["size"]["value"]])

    def size_for(self, cell_type: str, sheet_size: int) -> int:
        """The font size of a cell of `cell_type` on a sheet whose size is `sheet_size`."""
        kind = self.style(cell_type).size
        if kind == "sheet":
            return int(sheet_size)
        return int(self.sizes[kind])

    def sheet_view(self, sheet_type: str) -> dict[str, Any]:
        """{"size": 8|9, "gridlines": bool, "zoom": int, "freeze": "D4"|None} for a sheet type."""
        self.sheet_size(sheet_type)    # validates the name
        view = self.data["sheet_types"][sheet_type]
        return {"size": self.sheet_size(sheet_type), "gridlines": bool(view["gridlines"]["value"]),
                "zoom": int(view["zoom"]["value"]), "freeze": view["freeze"]["value"]}

    def number_format(self, key: str) -> str:
        """The number-format string of a key of `number_formats`."""
        from . import common, schemas
        formats = self.data["number_formats"]
        if key not in formats:
            near = schemas.nearest(key, formats)
            raise common.Refusal(f"number format '{key}' is not in the dictionary"
                                 + (f"; did you mean '{near}'?" if near else ""),
                                 fix="use one of: " + ", ".join(formats))
        return str(formats[key]["value"])

    def number_format_keys(self) -> list[str]:
        return list(self.data["number_formats"])

    def format_for_column_type(self, col_type: str) -> str:
        """The number_formats key for a contract column type (text, integer, number, date, datetime, boolean)."""
        from . import common
        table = self.data["contract_type_formats"]
        if col_type not in table:
            raise common.Refusal(f"contract column type '{col_type}' has no format in the dictionary",
                                 fix="use one of: " + ", ".join(table))
        return str(table[col_type]["value"])

    def rule(self, name: str, default: Any = None) -> Any:
        """The value of rules.<name> (depth_limit, length_limit, ...)."""
        leaf = self.data.get("rules", {}).get(name)
        return copy.deepcopy(leaf["value"]) if _is_leaf(leaf) else default

    def palette(self) -> set[str]:
        """Every colour (hex) used by the active cell types, plus the check failure colour."""
        out: set[str] = set()
        for name in self.cell_types():
            st = self.style(name)
            for hexv in (st.font_color, st.fill):
                if hexv:
                    out.add(hexv)
            for side in (st.border or {}).values():
                if side.get("color"):
                    out.add(side["color"])
        if FAIL_COLOR_KEY in self.data["colors"]:
            out.add(self.color(FAIL_COLOR_KEY))
        return out

    def named_overrides(self) -> list[str]:
        return list(self.data.get("named_overrides", {}))

    def source(self, dotted: str) -> str | None:
        """The source text of a leaf, by dotted path, e.g. 'cell_types.input.fill'."""
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node.get("source") if _is_leaf(node) else None


def merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge into a new dict: dicts merge key by key; a leaf ({"value", "source"}) or any other value
    replaces the old one whole. Neither argument is changed."""
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and not _is_leaf(value) and isinstance(out.get(key), dict) \
                and not _is_leaf(out.get(key)):
            out[key] = merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _apply_named(data: dict[str, Any], name: str, origin: str) -> dict[str, Any]:
    from . import common, schemas
    named = data.get("named_overrides", {})
    if name not in named:
        near = schemas.nearest(name, named)
        raise common.Refusal(f"{origin}: named override '{name}' is not in the dictionary"
                             + (f"; did you mean '{near}'?" if near else ""),
                             fix=("use one of: " + ", ".join(named)) if named else "remove it")
    return merge(data, named[name]["set"])


def load_dictionary(override: str | Path | None = None, *, use: Sequence[str] = (),
                    base: str | Path | None = None) -> Dictionary:
    """The plugin dictionary (or `base`), then the named overrides in `use`, then the override file.
    Validates every layer (schema "dictionary" / "dictionary_override"); raises common.Refusal."""
    from . import common, schemas
    base_path = common.native_path(base) if base else default_path()
    data = common.load_json_file(base_path, "style dictionary")
    schemas.require_valid(data, "dictionary", base_path.name)
    applied: list[str] = []
    for name in use:
        data = _apply_named(data, name, "--use")
        applied.append(name)
    if override is not None:
        op = common.native_path(override)
        ov = common.load_json_file(op, "dictionary override")
        schemas.require_valid(ov, "dictionary_override", op.name)
        for name in ov.get("use", []):
            data = _apply_named(data, name, f"{op.name} use")
            applied.append(name)
        if ov.get("set"):
            data = merge(data, ov["set"])
            applied.append(op.name)
    if applied:
        errs = schemas.validate(data, "dictionary")
        if errs:
            raise common.Refusal(f"the style dictionary after applying {', '.join(applied)} is not valid "
                                 f"({len(errs)} problem{'s' if len(errs) != 1 else ''})",
                                 details=[e.line() for e in errs],
                                 fix="change the override so that every key it names exists in the dictionary")
    return Dictionary(data=data, path=base_path, font_name=str(data["font"]["value"]),
                      sizes={k: int(v["value"]) for k, v in data["sizes"].items()}, applied=applied,
                      sha256=common.canonical_hash(data))


def find_override(model_root: str | Path) -> Path | None:
    """<model>/style_overrides.json when it exists."""
    p = Path(model_root) / OVERRIDE_FILE
    return p if p.is_file() else None


def load_for_model(model_root: str | Path | None, *, spec_override: str | Path | None = None,
                   use: Sequence[str] = ()) -> Dictionary:
    """The dictionary a model uses: the spec's `dictionary` path (relative to the model folder) when given,
    else <model>/style_overrides.json when it exists, else the plugin dictionary alone."""
    from . import common
    override: Path | None = None
    if spec_override:
        p = common.native_path(spec_override)
        override = p if p.is_absolute() or model_root is None else Path(model_root) / p
        if not override.is_file():
            raise common.Refusal(f"dictionary override {override.as_posix()} not found",
                                 fix="correct the spec's \"dictionary\" path (relative to the model folder) or "
                                     "set it to null")
    elif model_root is not None:
        override = find_override(model_root)
    return load_dictionary(override, use=use)


def source_problems(data: dict[str, Any]) -> list[str]:
    """Leaves whose source names no key of `sources` (design 10.1: a source names a key of `sources`).
    Returned as 'path: source' lines; the loader does not refuse them (owner M2 keeps them aligned)."""
    keys = set(data.get("sources", {}))
    out: list[str] = []

    def walk(node: Any, path: str) -> None:
        if _is_leaf(node):
            src = str(node["source"])
            if not keys & set(re.findall(r"[A-Za-z0-9_]+", src)):
                out.append(f"{path}: {src}")
            return
        if isinstance(node, dict):
            for k, child in node.items():
                if k in ("sources", "named_overrides"):
                    continue
                walk(child, f"{path}.{k}" if path else k)

    walk(data, "")
    return out

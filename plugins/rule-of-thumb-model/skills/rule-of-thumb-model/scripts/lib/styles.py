"""Apply the style dictionary to openpyxl cells; named styles; the expected look for the audit (design section 10.5).

Owner: M2. openpyxl is imported inside functions only.

Three views of one cell type's look, all built from lib.dictionary:
- expected_style(): a plain dict with the keys of workbook.CellInfo (font_name, font_size, bold, italic, font_color,
  fill, number_format, border, align, wrap, locked). The audit compares a cell to it with compare_cell().
- style_spec(): the same look in the shape of an excel.apply_edits "style" operation (font, fill, number_format,
  border, align), for edits made through Excel.
- apply() / apply_attributes(): the look written onto an openpyxl cell (build, and theme on workbooks without
  fragile parts).

Equivalences used when a cell is compared (written once here so build, theme and audit agree):
- An automatic font colour (CellInfo.font_color None) counts as 000000: Excel draws automatic text in black.
- Font names compare without regard to case.
- Sizes compare within 0.01 point.
- Alignment "general" counts as right for numbers, dates and booleans and as left for text (what Excel shows);
  "centerContinuous" (centre across selection) counts as center.
- A border side with an automatic colour counts as 000000.
- Number formats compare by what they display (number_formats_equal, v6, r5 verdict A-aera-8 and A-aera-13; the
  one comparison of audit S-NUMFMT and of theme): letter case does not count, and a literal character is
  the same written bare, after a backslash or in quotes (Excel saves yyyy-mm-dd as yyyy\\-mm\\-dd). A character that
  has a meaning of its own when bare (the percent sign, the digit placeholders, the comma, the point, the date and
  time letters) differs from the same character in quotes or after a backslash: 0.0% and 0.0"%" differ.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Any, Sequence

NAMED_STYLE_PREFIX = "rotm "                 # named styles "rotm input", "rotm calc", ...
FOREIGN_ATTRIBUTES = ("font_name", "font_size", "font_color", "bold", "fill")   # compared on workbooks without a manifest
ALL_ATTRIBUTES = FOREIGN_ATTRIBUTES + ("italic", "number_format", "border", "align")
# Attributes theme sets by default (FOREIGN_ATTRIBUTES plus italic, which is part of the font); the flags
# --number-formats, --borders and --align add the others (design 10.5).
THEME_ATTRIBUTES = FOREIGN_ATTRIBUTES + ("italic",)

# The audit rule of each attribute (design section 14). Attributes without a rule there map to None.
ATTRIBUTE_RULES: dict[str, str | None] = {
    "font_name": "S-FONT", "font_size": "S-SIZE", "fill": "S-FILL", "font_color": "S-FONTCOLOR", "bold": "S-BOLD",
    "number_format": "S-NUMFMT", "italic": None, "border": None, "align": None,
}
# S-BOLD applies to these cell types only (design 14: header, title, total, output or master-check cells).
BOLD_CHECKED_TYPES = ("col_header", "title", "heading", "total", "output", "master_check")

AUTOMATIC_TEXT = "000000"                   # what an automatic font colour shows
SIDES = ("left", "right", "top", "bottom")


@dataclass(frozen=True)
class Mismatch:
    """One attribute of a cell that differs from the dictionary."""
    attribute: str
    actual: Any
    expected: Any
    rule: str | None
    text: str                                  # "font_name Calibri, expected Arial"


# ----------------------------------------------------------------------------- the expected look

def named_style_name(cell_type: str) -> str:
    """'input' -> 'rotm input'."""
    return NAMED_STYLE_PREFIX + cell_type


def _hex(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lstrip("#").upper()
    if len(text) == 8:
        text = text[2:]
    return text or None


def _resolve_align(align: str | None, value: Any) -> str | None:
    """'type' -> right for numbers, dates and booleans, left for text; other values unchanged."""
    if align != "type":
        return align
    if value is None:
        return None
    return "left" if isinstance(value, str) and not str(value).startswith("=") else "right"


def expected_style(dictionary: Any, cell_type: str, *, sheet_size: int,
                   format_key: str | None = None) -> dict[str, Any]:
    """What the audit expects for a cell of `cell_type` on a sheet of size `sheet_size` (8 or 9).

    number_format is None when the type's format is "item" and no `format_key` is given (the item's own format
    is not known, so it is not compared). border is {} for no border. align may be "type" (numbers right, text
    left), resolved per cell by compare_cell()."""
    st = dictionary.style(cell_type)
    if st.number_format == "item":
        number_format = dictionary.number_format(format_key) if format_key else None
    else:
        number_format = st.number_format
    border = {side: {"style": spec["style"], "color": spec.get("color")} for side, spec in (st.border or {}).items()}
    return {"cell_type": cell_type, "font_name": dictionary.font_name,
            "font_size": dictionary.size_for(cell_type, sheet_size), "bold": st.bold, "italic": st.italic,
            "font_color": st.font_color, "fill": st.fill, "number_format": number_format, "border": border,
            "align": st.align, "wrap": st.wrap, "locked": st.locked}


def style_spec(dictionary: Any, cell_type: str, *, size: int, format_key: str | None = None,
               value: Any = None, attributes: Sequence[str] = ALL_ATTRIBUTES) -> dict[str, Any]:
    """The look as a plain dict, the shape of an excel.apply_edits "style" operation (font, fill, number_format,
    border, align). `size` is the sheet's size (8 or 9). Only `attributes` are included: font keys for
    font_name, font_size, font_color, bold, italic; "fill"; "number_format" when it is known; "border" with all
    four sides ("none" where the dictionary has none); "align" with horizontal (resolved for "type" from
    `value`) and wrap. The caller adds "op", "sheet" and "ranges"."""
    exp = expected_style(dictionary, cell_type, sheet_size=size, format_key=format_key)
    attrs = set(attributes)
    out: dict[str, Any] = {}
    font: dict[str, Any] = {}
    if "font_name" in attrs:
        font["name"] = exp["font_name"]
    if "font_size" in attrs:
        font["size"] = exp["font_size"]
    if "bold" in attrs:
        font["bold"] = exp["bold"]
    if "italic" in attrs:
        font["italic"] = exp["italic"]
    if "font_color" in attrs:
        font["color"] = exp["font_color"]
    if font:
        out["font"] = font
    if "fill" in attrs:
        out["fill"] = exp["fill"]
    if "number_format" in attrs and exp["number_format"] is not None:
        out["number_format"] = exp["number_format"]
    if "border" in attrs:
        out["border"] = {side: ({"style": exp["border"][side]["style"], "color": exp["border"][side]["color"]}
                                if side in exp["border"] else "none") for side in SIDES}
    if "align" in attrs:
        align: dict[str, Any] = {"wrap": exp["wrap"]}
        horizontal = _resolve_align(exp["align"], value)
        if horizontal is not None:
            align["horizontal"] = horizontal
        out["align"] = align
    return out


# ----------------------------------------------------------------------------- comparison

# Characters Excel shows as themselves when they stand bare in a number format (no quotes and no backslash needed).
_BARE_LITERALS = frozenset("$-+/():!^&'~{}<>= ")


def number_format_tokens(fmt: str | None) -> tuple[tuple[str, str], ...]:
    """A number format as the tokens that decide what it displays (v6, A-aera-8 and A-aera-13).

    - ("lit", c): a literal character: each character of a quoted text, the character after a backslash, and a bare
      character Excel shows as itself ($ - + / ( ) : ! ^ & ' ~ { } < > = and the space).
    - ("_", c) and ("*", c): the space as wide as c, and c repeated to fill the cell.
    - ("[]", text): a part in square brackets (a colour, a condition, a locale, an elapsed time).
    - ("code", c): any other bare character, which has a meaning of its own (0 # ? . , % E @ ; and the date and time
      letters).
    Letter case does not count (r5 verdict A-aera-8: 'Equal: letter case'), so every token is in lower case. An empty
    format counts as General."""
    text = ((fmt or "").strip() or "General").lower()
    out: list[tuple[str, str]] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            j = text.find('"', i + 1)
            end = n if j < 0 else j
            out.extend(("lit", c) for c in text[i + 1:end])
            i = end + 1
        elif ch == "\\":
            if i + 1 < n:
                out.append(("lit", text[i + 1]))
                i += 2
            else:
                out.append(("lit", "\\"))
                i += 1
        elif ch in "_*":
            out.append((ch, text[i + 1] if i + 1 < n else ""))
            i += 2
        elif ch == "[":
            j = text.find("]", i + 1)
            end = n if j < 0 else j
            out.append(("[]", text[i + 1:end]))
            i = end + 1
        elif ch in _BARE_LITERALS:
            out.append(("lit", ch))
            i += 1
        else:
            out.append(("code", ch))
            i += 1
    return tuple(out)


def number_formats_equal(a: str | None, b: str | None) -> bool:
    """True when the two number formats display a value the same way (v6, r5 verdict A-aera-8 and A-aera-13): the one
    comparison of number formats, used by audit S-NUMFMT and by theme (its plan, T-RESIDUAL and --number-formats).
    yyyy-mm-dd equals yyyy\\-mm\\-dd and YYYY-MM-DD; #,##0_);(#,##0) equals #,##0_);\\(#,##0\\); 0.0% does not equal
    0.0"%" or 0.0\\%, which show the percent sign without multiplying by 100."""
    if a == b:
        return True
    return number_format_tokens(a) == number_format_tokens(b)


def _font_color(value: Any) -> str:
    return _hex(value) or AUTOMATIC_TEXT


def _size_text(value: Any) -> str:
    if value is None:
        return "(none)"
    f = float(value)
    return str(int(f)) if f.is_integer() else f"{f:g}"


def _color_text(value: Any, automatic: str = "automatic") -> str:
    return _hex(value) or automatic


def _shown_align(h_align: str | None, value: Any) -> str:
    """The horizontal alignment Excel shows: general resolves by value type; centerContinuous counts as center."""
    h = (h_align or "general")
    if h in ("centerContinuous", "center_across"):
        return "center"
    if h == "general":
        if isinstance(value, bool) or isinstance(value, (int, float, _dt.date, _dt.datetime, _dt.time)):
            return "right"
        if isinstance(value, str) and value.startswith("="):
            return "right"                     # a formula: shown by its result; numbers are the common case
        return "left"
    return h


def _border_sides(border: Any) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for side, spec in (border or {}).items():
        if side not in SIDES or not spec:
            continue
        style = spec.get("style") if isinstance(spec, dict) else spec
        if not style or style == "none":
            continue
        color = spec.get("color") if isinstance(spec, dict) else None
        out[side] = (str(style), _font_color(color))
    return out


def _border_text(sides: dict[str, tuple[str, str]]) -> str:
    if not sides:
        return "none"
    return ", ".join(f"{side} {style} {color}" for side, (style, color) in sorted(sides.items()))


def mismatches(cell: Any, expected: dict[str, Any], *,
               attributes: Sequence[str] = FOREIGN_ATTRIBUTES) -> list[Mismatch]:
    """Every attribute of a workbook.CellInfo that differs from `expected` (from expected_style)."""
    out: list[Mismatch] = []

    def add(attr: str, actual: Any, exp: Any, actual_text: str, exp_text: str) -> None:
        out.append(Mismatch(attr, actual, exp, ATTRIBUTE_RULES.get(attr), f"{attr} {actual_text}, expected {exp_text}"))

    for attr in attributes:
        if attr == "font_name":
            a, e = getattr(cell, "font_name", None), expected.get("font_name")
            if e is not None and (a or "").casefold() != str(e).casefold():
                add(attr, a, e, a or "(none)", str(e))
        elif attr == "font_size":
            a, e = getattr(cell, "font_size", None), expected.get("font_size")
            if e is not None and (a is None or abs(float(a) - float(e)) > 0.01):
                add(attr, a, e, _size_text(a), _size_text(e))
        elif attr == "font_color":
            a, e = getattr(cell, "font_color", None), expected.get("font_color")
            if _font_color(a) != _font_color(e):
                add(attr, a, e, _color_text(a), _color_text(e))
        elif attr in ("bold", "italic"):
            a, e = bool(getattr(cell, attr, False)), bool(expected.get(attr, False))
            if a != e:
                add(attr, a, e, "yes" if a else "no", "yes" if e else "no")
        elif attr == "fill":
            a, e = _hex(getattr(cell, "fill", None)), _hex(expected.get("fill"))
            if a != e:
                add(attr, a, e, a or "none", e or "none")
        elif attr == "number_format":
            e = expected.get("number_format")
            if e is None:
                continue
            a = getattr(cell, "number_format", None) or "General"
            if not number_formats_equal(a, e):
                add(attr, a, e, a, e)
        elif attr == "border":
            a_sides = _border_sides(getattr(cell, "border", None))
            e_sides = _border_sides(expected.get("border"))
            if a_sides != e_sides:
                add(attr, a_sides, e_sides, _border_text(a_sides), _border_text(e_sides))
        elif attr == "align":
            value = getattr(cell, "value", None)
            e = _resolve_align(expected.get("align"), value)
            if e is not None and e != "general":
                a = _shown_align(getattr(cell, "h_align", None), value)
                if a != e:
                    add(attr, getattr(cell, "h_align", None), e, a, e)
            e_wrap = expected.get("wrap")
            if e_wrap is not None and bool(getattr(cell, "wrap", False)) != bool(e_wrap):
                a_wrap = bool(getattr(cell, "wrap", False))
                add("align", a_wrap, e_wrap, "wrap on" if a_wrap else "wrap off", "wrap on" if e_wrap else "wrap off")
        else:
            raise ValueError(f"unknown style attribute {attr!r}; use one of {', '.join(ALL_ATTRIBUTES)}")
    return out


def compare_cell(cell: Any, expected: dict[str, Any], *,
                 attributes: Sequence[str] = FOREIGN_ATTRIBUTES) -> list[str]:
    """Mismatches of a workbook.CellInfo against `expected`, e.g. ["font_name Calibri, expected Arial"]."""
    return [m.text for m in mismatches(cell, expected, attributes=attributes)]


# ----------------------------------------------------------------------------- openpyxl objects

def _argb(hexv: str | None) -> str | None:
    return None if hexv is None else "FF" + hexv.upper()


def _side(spec: dict[str, Any] | None) -> Any:
    from openpyxl.styles import Side
    if not spec:
        return Side()
    return Side(style=spec["style"], color=_argb(spec.get("color") or AUTOMATIC_TEXT))


def _border_obj(border: dict[str, Any]) -> Any:
    from openpyxl.styles import Border
    return Border(**{side: _side(border.get(side)) for side in SIDES})


def _fill_obj(hexv: str | None) -> Any:
    from openpyxl.styles import PatternFill
    if hexv is None:
        return PatternFill(fill_type=None)
    return PatternFill(fill_type="solid", fgColor=_argb(hexv), bgColor=_argb(hexv))


def _font_obj(exp: dict[str, Any], old: Any = None, attributes: Sequence[str] = ALL_ATTRIBUTES) -> Any:
    """A Font with the expected attributes; the other attributes are copied from `old` (underline, strike, ...).
    When the name changes, the theme scheme, charset and family of the old font are dropped, because a font with
    scheme "minor" or "major" is drawn in the theme's font whatever its name says."""
    from openpyxl.styles import Font
    attrs = set(attributes)
    kw: dict[str, Any] = {}
    if old is not None:
        for key in ("name", "sz", "b", "i", "u", "strike", "vertAlign", "charset", "outline", "shadow", "condense",
                    "extend", "family", "scheme"):
            kw[key] = getattr(old, key, None)
        kw["color"] = old.color
    if "font_name" in attrs:
        if old is None or (kw.get("name") or "").casefold() != exp["font_name"].casefold() or kw.get("scheme"):
            kw.update(scheme=None, charset=None, family=None)
        kw["name"] = exp["font_name"]
    if "font_size" in attrs:
        kw["sz"] = exp["font_size"]
    if "bold" in attrs:
        kw["b"] = exp["bold"]
    if "italic" in attrs:
        kw["i"] = exp["italic"]
    if "font_color" in attrs:
        kw["color"] = _argb(exp["font_color"] or AUTOMATIC_TEXT)
    return Font(**{k: v for k, v in kw.items() if v is not None or k in ("scheme", "charset", "family")})


def _alignment_obj(exp: dict[str, Any], value: Any, old: Any = None) -> Any:
    from openpyxl.styles import Alignment
    kw: dict[str, Any] = {}
    if old is not None:
        for key in ("horizontal", "vertical", "textRotation", "wrapText", "shrinkToFit", "indent"):
            kw[key] = getattr(old, key, None)
    horizontal = _resolve_align(exp["align"], value)
    kw["horizontal"] = None if horizontal in (None, "general") else horizontal
    kw["wrapText"] = bool(exp["wrap"]) or None
    return Alignment(**{k: v for k, v in kw.items() if v is not None})


def apply_attributes(cell: Any, expected: dict[str, Any], *, attributes: Sequence[str] = ALL_ATTRIBUTES) -> None:
    """Write `attributes` of `expected` (from expected_style) onto an openpyxl cell and keep its other style
    attributes (theme uses this on workbooks without fragile parts)."""
    attrs = set(attributes)
    font_attrs = [a for a in ("font_name", "font_size", "font_color", "bold", "italic") if a in attrs]
    if font_attrs:
        cell.font = _font_obj(expected, cell.font, font_attrs)
    if "fill" in attrs:
        cell.fill = _fill_obj(_hex(expected["fill"]))
    if "number_format" in attrs and expected.get("number_format") is not None:
        cell.number_format = expected["number_format"]
    if "border" in attrs:
        cell.border = _border_obj(expected["border"])
    if "align" in attrs:
        cell.alignment = _alignment_obj(expected, cell.value, cell.alignment)


def _named_style(dictionary: Any, cell_type: str, size: int) -> Any:
    from openpyxl.styles import NamedStyle, Protection
    exp = expected_style(dictionary, cell_type, sheet_size=size)
    ns = NamedStyle(name=named_style_name(cell_type))
    ns.font = _font_obj(exp)
    ns.fill = _fill_obj(_hex(exp["fill"]))
    ns.border = _border_obj(exp["border"])
    ns.alignment = _alignment_obj(exp, None)
    ns.number_format = exp["number_format"] or "General"
    ns.protection = Protection(locked=bool(exp["locked"]))
    return ns


def register_named_styles(wb: Any, dictionary: Any, *, size: int | None = None) -> dict[str, str]:
    """Register one named style per cell type ("rotm input", "rotm calc", ...); returns cell type -> style name.

    The named style holds the type's look at `size` (default: the dictionary's spaced size, 9); apply() sets the
    sheet's size on each cell. A style of that name already in the workbook is kept as it is. Named styles are
    an aid for a person editing in Excel; the audit compares the look of each cell, not the style name
    (SYNTHESIS D8: style ids are local to a workbook)."""
    base = int(size if size is not None else dictionary.sizes["spaced"])
    existing = set(wb.named_styles)
    out: dict[str, str] = {}
    for cell_type in dictionary.cell_types():
        name = named_style_name(cell_type)
        if name not in existing:
            wb.add_named_style(_named_style(dictionary, cell_type, base))
            existing.add(name)
        out[cell_type] = name
    wb._rotm_named_styles = (dictionary.sha256, out)
    return out


def set_default_font(wb: Any, dictionary: Any, *, size: int | None = None) -> None:
    """Make the workbook's default font (the Normal style, used by empty cells and by Excel's column-width unit)
    the dictionary font at `size` (default 9) (SYNTHESIS D8: "set the workbook's Normal style to Arial 9").
    Call it on a workbook this plugin creates, before cells are styled. openpyxl keeps the default font at index
    0 of the workbook's font list; that entry is replaced and every other index stays where it was."""
    from openpyxl.styles import Font
    from openpyxl.utils.indexed_list import IndexedList
    sz = int(size if size is not None else dictionary.sizes["spaced"])
    fonts = list(wb._fonts)
    fonts[0] = Font(name=dictionary.font_name, sz=sz)
    wb._fonts = IndexedList(fonts)


def apply(cell: Any, dictionary: Any, cell_type: str, *, size: int, format_key: str | None = None,
          named: bool = True) -> None:
    """Give an openpyxl cell the dictionary's look for `cell_type` on a sheet of size `size` (8 or 9).

    `format_key` (a key of number_formats) is used when the type's number_format is "item"; without it an "item"
    cell keeps its number format. With `named` the cell first gets the type's named style (registered on first
    use), then every attribute is set on the cell: font (name, the type's size on this sheet, colour, bold,
    italic), fill, border, number format, alignment (for "type": numbers right, text left) and locking."""
    if named:
        wb = cell.parent.parent
        reg = getattr(wb, "_rotm_named_styles", None)
        if reg is None or reg[0] != dictionary.sha256:
            register_named_styles(wb, dictionary)
        cell.style = named_style_name(cell_type)
    objs = _objects(dictionary, cell_type, size, format_key, _resolve_align(dictionary.style(cell_type).align,
                                                                             cell.value))
    font, fill, border, number_format, alignment, protection = objs
    cell.font, cell.fill, cell.border = font, fill, border
    if number_format is not None:
        cell.number_format = number_format
    cell.alignment, cell.protection = alignment, protection


# ----------------------------------------------------------------------------- the reference tables

REFERENCE_FILE = "style-dictionary.md"
GENERATED_START = "<!-- generated from style_dictionary.json by lib.styles.dictionary_markdown: do not edit by hand -->"
GENERATED_END = "<!-- end of generated tables -->"
_ATTR_ORDER = ("meaning", "font_color", "bold", "italic", "fill", "border", "number_format", "align", "wrap",
               "locked", "size")


def _md(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _show(attr: str, value: Any, data: dict[str, Any]) -> str:
    if value is None:
        return "none" if attr in ("fill", "border") else "automatic" if attr == "font_color" else "null"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if attr in ("font_color", "fill") and value in data.get("colors", {}):
        return f"`{value}` ({data['colors'][value]['value']})"
    if attr == "border":
        sides = []
        for side, spec in value.items():
            color = spec.get("color")
            hexv = data.get("colors", {}).get(color, {}).get("value", color)
            sides.append(f"{side} {spec['style']} {hexv}")
        return ", ".join(sides)
    if attr == "number_format" and value in data.get("number_formats", {}):
        return f"`{value}` (`{data['number_formats'][value]['value']}`)"
    if attr == "size":
        return {"sheet": "the sheet's size (8 or 9)", "spaced": "9", "dense": "8"}.get(value, str(value))
    return f"`{value}`" if attr in ("number_format", "align") else str(value)


def dictionary_markdown(data: dict[str, Any]) -> str:
    """The value tables of references/style-dictionary.md, generated from the dictionary JSON (every value with
    its source), so the document cannot drift from the file the scripts read."""
    lines: list[str] = [GENERATED_START, ""]
    lines += ["### Sources", "", "| Key | What it is |", "| --- | --- |"]
    lines += [f"| {k} | {_md(v)} |" for k, v in data["sources"].items()]
    lines += ["", "### Font and sizes", "", "| Entry | Value | Source |", "| --- | --- | --- |",
              f"| font | {_md(data['font']['value'])} | {_md(data['font']['source'])} |"]
    lines += [f"| sizes.{k} | {v['value']} | {_md(v['source'])} |" for k, v in data["sizes"].items()]
    lines += ["", "### Sheet types", "", "| Sheet type | Size | Gridlines | Zoom (%) | Freeze at | Sources |",
              "| --- | --- | --- | --- | --- | --- |"]
    for name, st in data["sheet_types"].items():
        size = data["sizes"][st["size"]["value"]]["value"]
        srcs = "; ".join(f"{a}: {_md(st[a]['source'])}" for a in ("size", "gridlines", "zoom", "freeze"))
        lines.append(f"| {name} | {st['size']['value']} ({size}) | {'shown' if st['gridlines']['value'] else 'hidden'} "
                     f"| {st['zoom']['value']} | {st['freeze']['value'] or 'none'} | {srcs} |")
    lines += ["", "### Colours", "", "| Key | Hex | Source |", "| --- | --- | --- |"]
    lines += [f"| {k} | {v['value']} | {_md(v['source'])} |" for k, v in data["colors"].items()]
    lines += ["", "### Number formats", "", "| Key | Format | Source |", "| --- | --- | --- |"]
    lines += [f"| {k} | `{_md(v['value'])}` | {_md(v['source'])} |" for k, v in data["number_formats"].items()]
    lines += ["", "Contract column types take these formats (design 6.8, land and build):", "",
              "| Contract type | Format key | Source |", "| --- | --- | --- |"]
    lines += [f"| {k} | {v['value']} | {_md(v['source'])} |" for k, v in data["contract_type_formats"].items()]
    lines += ["", "### Cell types", ""]
    for name, ct in data["cell_types"].items():
        lines += [f"#### {name}", "", "| Attribute | Value | Source |", "| --- | --- | --- |"]
        for attr in _ATTR_ORDER:
            leaf = ct.get(attr)
            if leaf is None:
                continue
            lines.append(f"| {attr} | {_md(_show(attr, leaf['value'], data))} | {_md(leaf['source'])} |")
        lines.append("")
    lines += ["### Rules", "", "| Rule | Value | Source |", "| --- | --- | --- |"]
    lines += [f"| {k} | {_md(v['value'])} | {_md(v['source'])} |" for k, v in data.get("rules", {}).items()]
    lines += ["", "### Named overrides", "", "| Name | What it changes | Source |", "| --- | --- | --- |"]
    for name, ov in data.get("named_overrides", {}).items():
        changes = []
        for ct_name, attrs in ov["set"].get("cell_types", {}).items():
            for attr, leaf in attrs.items():
                changes.append(f"{ct_name}.{attr} = {_show(attr, leaf['value'], data)}")
        lines.append(f"| {name} | {_md(ov['meaning'])}: {_md('; '.join(changes))} | {_md(ov['source'])} |")
    lines += ["", GENERATED_END]
    return "\n".join(lines) + "\n"


def reference_path() -> Any:
    from . import common
    return common.references_dir() / REFERENCE_FILE


def write_reference(path: Any = None) -> Any:
    """Replace the generated part of references/style-dictionary.md with tables from the plugin dictionary.
    Run after editing style_dictionary.json:
    python -P -X utf8 -B -c "import sys; sys.path.insert(0, 'scripts'); from lib import styles; styles.write_reference()"
    (from the skill folder)."""
    from pathlib import Path

    from . import common
    from . import dictionary as dict_mod
    target = Path(path) if path is not None else reference_path()
    data = common.read_json(dict_mod.default_path())
    text = target.read_text(encoding="utf-8")
    start, end = text.index(GENERATED_START), text.index(GENERATED_END) + len(GENERATED_END)
    new = text[:start] + dictionary_markdown(data).rstrip("\n") + text[end:]
    target.write_text(new, encoding="utf-8", newline="\n")
    return target


_OBJECTS: dict[tuple[Any, ...], tuple[Any, ...]] = {}


def _objects(dictionary: Any, cell_type: str, size: int, format_key: str | None, horizontal: str | None) -> tuple:
    """The openpyxl style objects of one look, built once (build calls apply() for every cell)."""
    from openpyxl.styles import Protection
    key = (dictionary.sha256, cell_type, int(size), format_key, horizontal)
    objs = _OBJECTS.get(key)
    if objs is None:
        exp = expected_style(dictionary, cell_type, sheet_size=size, format_key=format_key)
        value = None if horizontal is None else ("text" if horizontal == "left" else 0)
        objs = (_font_obj(exp), _fill_obj(_hex(exp["fill"])), _border_obj(exp["border"]), exp["number_format"],
                _alignment_obj(exp, value), Protection(locked=bool(exp["locked"])))
        if len(_OBJECTS) > 4096:
            _OBJECTS.clear()
        _OBJECTS[key] = objs
    return objs

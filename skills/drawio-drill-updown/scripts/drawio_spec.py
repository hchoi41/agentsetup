#!/usr/bin/env python3
"""drawio_spec.py: compile a JSON sheet spec into a draw.io (.drawio) file, add sheets to an
existing file, list, validate, and export (when a draw.io CLI is installed).

Usage
  python drawio_spec.py build    SPEC.json [-o OUT.drawio] [--force] [--theme minimal|colorful|bw] [--max-colours 3|4] (or --max-colors) [--strict]
  python drawio_spec.py add      SPEC.json --into FILE.drawio [--position first|last|before:NAME|after:NAME] [--theme ...] [--max-colours ...] [--strict]
  python drawio_spec.py list     FILE.drawio
  python drawio_spec.py validate FILE.drawio [--ladder] [--strict]
  python drawio_spec.py export   FILE.drawio [--format png|svg|pdf] [--out DIR] [--scale 1.5]

add without --position inserts first; pass --position last for a drill-down.

Spec format: see ../references/spec_format.md. Roles are one fixed vocabulary so every sheet of a
file reads the same way (who decides). The theme sets how a role is drawn: by shape (minimal, bw)
or by colour (colorful). The theme is recorded on <mxfile>, and in minimal, bw and colorful with a custom
accent also as style keys on each sheet's layer cell (and its first vertex), so 'add' keeps it. A box's
or lane's "link" names a sheet; a click then opens it (draw.io page link). build and add end with
'warnings: N'; --strict exits 1 when N is above 0. Standard library only.
"""
import argparse
import base64
import difflib
import html as html_lib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import urllib.parse
import zlib
import xml.dom.minidom as minidom
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

ESC = {'"': '&quot;'}
# an entity reference already in the text (the five XML ones, or a character reference) is kept as is
_ENTITY = re.compile(r"&(?:amp|lt|gt|quot|apos|#[0-9]+|#x[0-9A-Fa-f]+);")


def attr(text):
    """One XML attribute value (an id, a parent, source or target reference, a link, a style), escaped
    once at serialisation: '&', '<' and '"' become entities, and an entity reference already written
    stays as it is. Every other character is written as typed, so a value that was already valid XML
    is written byte for byte as before."""
    text = str(text)
    out, pos = [], 0
    for m in _ENTITY.finditer(text):
        out.append(text[pos:m.start()].replace("&", "&amp;"))
        out.append(m.group(0))
        pos = m.end()
    out.append(text[pos:].replace("&", "&amp;"))
    return "".join(out).replace("<", "&lt;").replace('"', "&quot;")


def lit_attr(text):
    """One XML attribute value written as a literal string: '&', '<', '>' and '"' always become
    entities, so the value reads back exactly as typed ('A&amp;B' stays 'A&amp;B'). Ids and the
    references to them use this; style strings use attr()."""
    return escape(str(text), ESC)


def xml_decode(written):
    """The value an XML parser reads from an attribute written as `written` (itself when it is not
    well-formed, which the build's own XML check then refuses)."""
    try:
        return ET.fromstring('<a v="%s"/>' % written).get("v")
    except ET.ParseError:
        return written


def lit(text):
    """Literal label text: HTML-escape it (draw.io labels are HTML), then XML-escape it for the
    attribute. Markup and entities in the spec show as typed."""
    return escape(text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), ESC)


def label(text, headline=False):
    """draw.io labels are HTML (html=1): a newline in the spec becomes <br>, which must sit
    entity-encoded inside the XML attribute exactly as draw.io saves it. headline=True sets the
    first line of a multi-line label in bold."""
    if headline and "\n" in text:
        first, rest = text.split("\n", 1)
        return "&lt;b&gt;%s&lt;/b&gt;&lt;br&gt;%s" % (lit(first), lit(rest).replace("\n", "&lt;br&gt;"))
    if headline and text:
        return "&lt;b&gt;%s&lt;/b&gt;" % lit(text)
    return lit(text).replace("\n", "&lt;br&gt;")


def old_label(text):
    """colorful: the label encoder of the script before themes existed. Only XML-escaped, so markup
    and entities in the spec stay active HTML in draw.io, as they always did."""
    return escape(text, ESC).replace("\n", "&lt;br&gt;")


# role -> (fill, stroke, dashed) for the colorful theme. One palette for every level so colour
# means the same thing on every sheet: who decides / what kind of thing it is.
ROLES = {
    "human":   ("#FFF2CC", "#D6B656", False),   # a person decides (gate, question, verdict)
    "propose": ("#DAE8FC", "#6C8EBF", False),   # an LLM / skill proposes, drafts, explains
    "decide":  ("#D5E8D4", "#82B366", False),   # deterministic code decides (script, check)
    "guard":   ("#FFE6CC", "#D79B00", False),   # policy layer (hooks, caps, allow-lists)
    "outside": ("#E1D5E7", "#9673A6", False),   # systems used but not contained
    "record":  ("#FFFFFF", "#82B366", True),    # artifact / audit record (dashed)
    "note":    ("#FFFFFF", "#9E9E9E", False),   # legend, footnote, caption
    "stage":   ("#DAE8FC", "#6C8EBF", False),   # column header
    "plain":   ("#F5F5F5", "#9E9E9E", False),   # neutral component
}
LANE_FILLS = {"human": "#FFFBEA", "propose": "#EEF4FC", "decide": "#EDF7EC", "guard": "#FFF3E6",
              "outside": "#F3EEF7", "record": "#F7F7F7", "plain": "#F5F5F5", "note": "#FAFAFA"}

# Themes. colorful = the original look (role -> colour). minimal and bw draw roles as shapes.
THEMES = ("minimal", "colorful", "bw")
DEFAULT_THEME = "minimal"
DEFAULT_ACCENT = "#1F5FBF"
INK, PAPER, TINT = "#000000", "#FFFFFF", "#F2F2F2"
MAX_COLOURS_CHOICES = (3, 4)     # minimal: black, white, grey (3), + the accent (4)
DEFAULT_MAX_COLOURS = 4
HARD_LIMIT = 5                   # guard: five or more colours always fails outside colorful
TINTED_ROLES = ("note", "stage")  # roles that group or annotate get the grey tint in minimal

# Shape catalogue: name -> (draw.io style, share of the width that holds text, extra spacing so
# text clears the outline, top/bottom allowance in px, allowance as a share of the height).
# All built into draw.io; no extra libraries to load.
SHAPES = {
    "process":          ("rounded=0;", 1.0, "", 0, 0),
    "rounded":          ("rounded=1;absoluteArcSize=1;arcSize=12;", 1.0, "", 0, 0),
    "decision":         ("shape=rhombus;perimeter=rhombusPerimeter;", 0.5, "", 0, 0.5),
    "terminator":       ("rounded=1;arcSize=50;roundedPerimeter=1;", 0.9, "spacingLeft=8;spacingRight=8;", 0, 0),
    "data":             ("shape=parallelogram;perimeter=parallelogramPerimeter;fixedSize=1;size=12;", 1.0, "spacingLeft=12;spacingRight=12;", 0, 0),
    "document":         ("shape=document;boundedLbl=1;size=0.12;perimeter=outlinePerimeter;", 1.0, "", 0, 0.12),
    "documents":        ("shape=mxgraph.flowchart.multi-document;boundedLbl=1;perimeter=outlinePerimeter;", 0.9, "", 0, 0.25),
    "predefined":       ("shape=process;fixedSize=1;size=8;backgroundOutline=1;", 1.0, "spacingLeft=10;spacingRight=10;", 0, 0),
    "manual_input":     ("shape=manualInput;size=12;perimeter=outlinePerimeter;", 1.0, "spacingTop=10;", 10, 0),
    "manual_operation": ("shape=trapezoid;perimeter=trapezoidPerimeter;fixedSize=1;size=12;flipV=1;", 1.0, "spacingLeft=12;spacingRight=12;", 0, 0),
    "preparation":      ("shape=hexagon;perimeter=hexagonPerimeter2;fixedSize=1;size=10;", 1.0, "spacingLeft=10;spacingRight=10;", 0, 0),
    "database":         ("shape=cylinder3;backgroundOutline=1;size=8;perimeter=outlinePerimeter;", 1.0, "spacingTop=12;spacingBottom=6;", 18, 0),
    "stored_data":      ("shape=dataStorage;fixedSize=1;size=12;perimeter=outlinePerimeter;", 1.0, "spacingLeft=10;spacingRight=14;", 0, 0),
    "delay":            ("shape=delay;perimeter=outlinePerimeter;", 1.0, "", 0, 0),
    "offpage":          ("shape=offPageConnector;size=0.25;perimeter=outlinePerimeter;", 1.0, "", 0, 0.25),
    "actor":            ("shape=umlActor;verticalLabelPosition=bottom;labelPosition=center;", None, "", 0, 0),
    "cloud":            ("shape=cloud;perimeter=cloudPerimeter;", 0.6, "", 0, 0.4),
    "note":             ("shape=note;size=12;perimeter=outlinePerimeter;", 1.0, "spacingRight=12;", 0, 0),
    "step":             ("shape=step;perimeter=stepPerimeter;fixedSize=1;size=10;", 1.0, "spacingLeft=12;spacingRight=12;", 0, 0),
    "double":           ("shape=ext;double=1;rounded=0;", 1.0, "spacingLeft=3;spacingTop=3;spacingRight=3;", 3, 0),
}
CENTRED_SHAPES = ("decision", "cloud", "terminator")   # text forced to the centre; one short line
# draw.io's label area of a predefined process stops at its side bars (size=8 on each side), so the
# text width loses 2 x 8 px more than the spacing says
BAR_INSET = {"predefined": 16}
ACCENT_WIDTH = 4        # the accent drawn by width (bw; minimal with max_colours 3)
PLAIN_WIDTH_MAX = 2     # in those modes no other box or edge may be wider than this
# role -> shape in the minimal and bw themes. Six are ANSI/ISO 5807 flowchart symbols; outside
# (double border), note (folded corner) and stage (chevron) are draw.io built-ins with no ANSI symbol.
ROLE_SHAPES = {"human": "manual_input", "propose": "rounded", "decide": "predefined",
               "guard": "preparation", "outside": "double", "record": "document",
               "note": "note", "stage": "step", "plain": "process"}
# roles an explicit shape may carry without hiding them (minimal, bw); the legend reads
# '<shape meaning>: <role meaning>'. Any other role: build warns that the shape hides it.
DEFAULT_SHAPE_ROLES = ("plain", "outside", "record")
# manual operation is the ANSI symbol for work a person does, so it may carry the human role too (human
# defaults to manual input: its flat bottom and full-width body leave room for bulleted text)
SHAPE_ROLES = {"actor": ("human",), "decision": ("human", "decide"),
               "manual_operation": ("human",) + DEFAULT_SHAPE_ROLES}


def shape_roles(name):
    return SHAPE_ROLES.get(name, DEFAULT_SHAPE_ROLES)


# default legend meanings
ROLE_MEANINGS = {"human": "a person decides", "propose": "an LLM or skill proposes",
                 "decide": "code decides", "guard": "policy layer", "outside": "outside system",
                 "record": "record or artifact", "note": "note", "stage": "stage", "plain": "component"}
SHAPE_MEANINGS = {"process": "process", "rounded": "alternate process", "decision": "yes/no check",
                  "terminator": "start or end", "data": "data in or out", "document": "document",
                  "documents": "set of documents", "predefined": "predefined process",
                  "manual_input": "manual input", "manual_operation": "manual operation",
                  "preparation": "preparation", "database": "database", "stored_data": "stored data",
                  "delay": "wait or delay", "offpage": "continues on another sheet", "actor": "person",
                  "cloud": "network or cloud service", "note": "note", "step": "stage",
                  "double": "outside system"}
FRAME_MEANING = "group of parts"   # the legend's frame row (minimal, bw); labels.frame overrides it
LEGEND_KEY = "dsLegend=1;"   # harmless custom style key; legend cells are not ladder vertices
CONTAINER_KEY = "container=1;collapsible=0;"
# a hand-written colour key: a palette word followed by box, boxes, '=', ':' or a dash
PALETTE_KEY = re.compile(r"\b(yellow|blue|green|orange|purple|red|grey|gray)\s*(?:boxes\b|box\b|=|:|—|–|-\s)", re.I)

# Style strings in minimal and bw go through an allow-list. Anything else (image, glass, sketch,
# stencils, bare named styles, gradients, shadow, opacity, ...) stops the run.
STYLE_COLOUR_KEYS = ("fillColor", "strokeColor", "fontColor", "labelBackgroundColor", "labelBorderColor")
STYLE_ALLOWED_KEYS = frozenset((
    # geometry and edge routing
    "size", "fixedSize", "flipH", "flipV", "direction", "aspect", "backgroundOutline", "double", "boundedLbl",
    "edgeStyle", "elbow", "curved", "orthogonal", "orthogonalLoop", "jettySize",
    "exitX", "exitY", "exitDx", "exitDy", "exitPerimeter", "entryX", "entryY", "entryDx", "entryDy", "entryPerimeter",
    "startArrow", "endArrow", "startFill", "endFill", "startSize", "endSize", "jumpStyle", "jumpSize",
    # spacing
    "spacing", "spacingLeft", "spacingRight", "spacingTop", "spacingBottom",
    # typography (html is not here: the compiler always writes html=1)
    "whiteSpace", "overflow", "fontSize", "fontStyle", "fontFamily", "align", "verticalAlign",
    "labelPosition", "verticalLabelPosition", "labelWidth", "horizontal", "textDirection",
    # perimeter, arc, line, rotation
    "perimeter", "perimeterSpacing", "sourcePerimeterSpacing", "targetPerimeterSpacing",
    "rounded", "arcSize", "absoluteArcSize", "roundedPerimeter", "dashed", "dashPattern", "strokeWidth", "rotation",
    # what draw.io itself writes on a label typed onto an edge (edgeLabel;...;resizable=0;points=[];)
    "resizable", "points",
) + STYLE_COLOUR_KEYS)
# a gradient or partial opacity paints shades between the palette colours that a colour count misses
OPACITY_KEYS = ("opacity", "fillOpacity", "strokeOpacity", "textOpacity")
RECORD_KEYS = ("dsTheme", "dsAccent", "dsMaxColours")
# keys only the compiler writes (a raw style may not): HTML labels, containers, the swimlane body, the
# legend mark and the theme record
COMPILER_STYLE_KEYS = frozenset(("html", "container", "collapsible", "swimlaneFillColor", "dsLegend") + RECORD_KEYS)
# the script's own markers: list, validate and add read the theme and the legend from them, so a spec's raw
# style may not carry one (in any theme)
RESERVED_STYLE_KEYS = RECORD_KEYS + ("dsLegend",)
# raw style keys the fit check, "h": "auto" and a frame's headline room read (they change the text's room)
FIT_STYLE_KEYS = ("fontSize", "spacing", "spacingLeft", "spacingRight", "spacingTop", "spacingBottom",
                  "labelWidth", "size", "fixedSize")
# a key that marks a role's shape: shape name -> (key, value the shape needs)
SHAPE_MARK = {"rounded": ("rounded", "1"), "double": ("double", "1")}
# raw style keys that change how a box's outline is drawn; the legend groups boxes by them and draws
# its sample with them (shape= is handled as the box's shape)
OUTLINE_KEYS = ("rounded", "arcSize", "absoluteArcSize", "double", "dashed", "dashPattern", "strokeWidth",
                "size", "fixedSize", "flipH", "flipV", "direction", "backgroundOutline")
LANE_GUTTER = 150  # px: default width of a row lane's title strip (and of the colorful title gutter)
DEFAULT_STARTSIZE = 40   # draw.io's title strip size for shape=swimlane when the style gives no startSize
NAMED_SWIMLANE_STARTSIZE = 23   # draw.io's named `swimlane` style sets startSize=23
OVERLAP_MIN = 5   # px: overlaps thinner than this in either direction are a shared border
FRAME_PAD = 10    # px: a frame's children keep this distance from its left, right and bottom sides

# Fidelity ladder (sheet name prefix -> allowed vertex count). Edges are not counted.
LADDER = {
    "L0": (1, 4,   "one box: the task as one sentence; input/output arrows at most"),
    "L1": (8, 25,  "concept: actors, 3-6 components, main flows, one footnote"),
    "L2": (25, 50, "stages x layers: 3-6 columns, 3-6 lanes, no IDs"),
    "L3": (60, 130, "detail: every phase/gate/script/artifact by name"),
    "L4": (0, 10 ** 6, "implementation: files, functions, commands"),
}


WARNINGS = [0]   # warnings printed so far in this run; build and add report the count


def warn(msg):
    WARNINGS[0] += 1
    print("warn: " + msg, file=sys.stderr)


# Keys a spec may use. Anything else is ignored with a warning that names the nearest valid key.
ACCENT_KEYS = ("accent_color", "accent_colour")
MAX_COLOURS_KEYS = ("max_colours", "max_colors")
SPEC_KEYS = ("file", "theme", "sheets") + ACCENT_KEYS + MAX_COLOURS_KEYS
SHEET_KEYS = ("id", "name", "width", "height", "grid", "cells")
GRID_KEYS = ("x0", "pitch", "col_width")
_GEOM_KEYS = ("type", "id", "x", "y", "w", "h", "col", "span")
CELL_KEYS = {
    "box": _GEOM_KEYS + ("text", "role", "shape", "accent", "font", "bold", "headline", "align", "valign",
                         "dashed", "fill", "stroke", "square", "style", "link"),
    "lane": _GEOM_KEYS + ("text", "role", "gutter", "fill", "font", "orientation", "link"),
    "text": _GEOM_KEYS + ("text", "font", "bold", "align"),
    "edge": ("type", "id", "from", "to", "label", "role", "color", "accent", "dashed", "width", "exit", "entry",
             "points", "label_at", "label_offset", "font", "style"),
    "legend": ("type", "id", "x", "y", "w", "col", "span", "labels", "title", "font"),
}


def _nearest(key, valid):
    m = difflib.get_close_matches(key, valid, n=1, cutoff=0.5)   # 0.5: a two-letter key with one slip ('xO')
    return ("nearest valid key: %r" % m[0]) if m else ("valid keys: %s" % ", ".join(sorted(valid)))


def check_keys(spec):
    """Warn on every unknown top-level, sheet or cell key: it would be ignored silently otherwise."""
    for k in spec:
        if k not in SPEC_KEYS:
            warn("spec: unknown top-level key %r (%s); ignored" % (k, _nearest(k, SPEC_KEYS)))
    for sh in spec.get("sheets") or []:
        nm = sh.get("name", sh.get("id"))
        for k in sh:
            if k not in SHEET_KEYS:
                warn("sheet %r: unknown key %r (%s); ignored" % (nm, k, _nearest(k, SHEET_KEYS)))
        if isinstance(sh.get("grid"), dict):
            # a misspelt grid key would leave its value at 0 and move every column placed by col
            for k in sh["grid"]:
                if k not in GRID_KEYS:
                    warn("sheet %r grid: unknown key %r (%s); ignored" % (nm, k, _nearest(k, GRID_KEYS)))
        for c in sh.get("cells") or []:
            valid = CELL_KEYS.get(c.get("type", "box"))
            if valid is None:
                continue    # an unknown type stops the run when the cell is compiled
            for k in c:
                if k not in valid:
                    warn("sheet %r cell %r: unknown key %r for a %s (%s); ignored" % (
                        nm, c.get("id", (c.get("text") or c.get("label") or "")[:30]), k, c.get("type", "box"),
                        _nearest(k, valid)))
            if c.get("type") == "legend" and isinstance(c.get("labels"), dict):
                # a label key names a role, a catalogue shape or the accent; any other key would leave
                # the default meaning on its row without a word
                names = tuple(ROLES) + tuple(n for n in SHAPES if n not in ROLES) + ("accent", "frame")
                for k in c["labels"]:
                    if k not in names:
                        warn("sheet %r legend %r: labels key %r is not a role, a shape, 'frame' or 'accent' (%s); ignored" % (
                            nm, c.get("id", "legend"), k, _nearest(k, names)))


def setting(spec, names):
    """A setting that may be spelled two ways (accent_color / accent_colour); two spellings with
    different values stop the run."""
    found = [(n, spec[n]) for n in names if n in spec]
    if len(found) > 1 and found[0][1] != found[1][1]:
        raise SystemExit("the spec sets both %s=%r and %s=%r; keep one" % (found[0] + found[1]))
    return found[0][1] if found else None


# One validation path for theme, accent and max_colours: the CLI, the spec and the file's record.
def check_theme(v, source):
    if v not in THEMES:
        raise SystemExit("%s: unknown theme %r; valid themes: %s" % (source, v, ", ".join(THEMES)))
    return v


ACCENT_MIN_CONTRAST = 3.0   # WCAG contrast an accent needs against white and against the grey tint


def contrast(a, b):
    """WCAG contrast ratio of two #RRGGBB colours (1 to 21)."""
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def check_accent(v, source, recorded=False):
    """An accent colour, normalised as every other colour is (#RGB, #RRGGBB, any case; black and
    white by name). It may not be a palette colour, and (where it is chosen: the spec or the command
    line) it needs a contrast of 3:1 against white and against the grey tint, or it cannot be seen.
    A file's recorded accent is already drawn, so reading it checks only the first two rules."""
    n = _norm_colour(v) if isinstance(v, str) else None
    if n is None or not re.match(r"^#[0-9A-F]{6}$", n):
        raise SystemExit("%s: accent_color %r is not a colour (#RRGGBB or #RGB)" % (source, v))
    named = {INK: "black", PAPER: "white", TINT: "the grey tint"}
    if n in named:
        raise SystemExit("%s: accent_color %s is %s, which the palette already uses; the accent would not stand "
                         "out. Pick another colour" % (source, n, named[n]))
    if not recorded:
        low = [(nm, contrast(n, c)) for c, nm in ((PAPER, "white"), (TINT, "the grey tint")) if contrast(n, c) < ACCENT_MIN_CONTRAST]
        if low:
            raise SystemExit("%s: accent_color %s has a contrast of only %s; an accent needs at least %g:1 against "
                             "white and the grey tint to be seen. Pick a darker colour" % (
                                 source, v, " and ".join("%.1f:1 against %s" % (r, nm) for nm, r in low),
                                 ACCENT_MIN_CONTRAST))
    return n


def check_max_colours(v, source):
    """None (not set), or 3 or 4. A whole number written as a string ("4", as the file stores it) is
    read as that number; anything else is an error with a message."""
    if v is None:
        return None
    if isinstance(v, str) and re.match(r"^\s*\d+\s*$", v):
        v = int(v)
    if isinstance(v, bool) or not isinstance(v, int):
        raise SystemExit("%s: max_colours %r is not a whole number; use 3 or 4" % (source, v))
    if v not in MAX_COLOURS_CHOICES:
        raise SystemExit("%s: max_colours %r is not 3 or 4 (for black and white only use --theme bw; "
                         "for more colours, colorful)" % (source, v))
    return v


def accent_drawn(theme, max_colours):
    """Whether the accent colour is drawn: in colorful and in minimal with max_colours 4. bw and minimal
    with 3 colours draw the accent as a black line of width 4."""
    return theme == "colorful" or (theme == "minimal" and (max_colours or DEFAULT_MAX_COLOURS) >= 4)


def resolve_theme(spec, cli_theme=None, max_colours=None, quiet=False):
    """(theme, accent). The spec's accent_color is always checked as a colour; its contrast only where
    the accent colour is drawn (accent_drawn). Where it is not drawn, a set accent_color warns that it
    is ignored (unless quiet). max_colours: the value in force (the command line's); None reads the spec."""
    if cli_theme:
        theme = check_theme(cli_theme, "--theme")
    else:
        theme = check_theme(spec.get("theme") or DEFAULT_THEME, "spec")
    accent = setting(spec, ACCENT_KEYS)
    if accent is None:
        return theme, DEFAULT_ACCENT
    if max_colours is None and theme == "minimal":
        max_colours = check_max_colours(setting(spec, MAX_COLOURS_KEYS), "spec")
    if accent_drawn(theme, max_colours):
        return theme, check_accent(accent, "spec")
    n = check_accent(accent, "spec", recorded=True)    # the format and palette rules; no contrast rule
    if not quiet:
        warn("theme %s draws the accent as a black line of width %d; the spec's accent_color %s is ignored" % (
            theme if theme == "bw" else "minimal with max_colours 3", ACCENT_WIDTH, n))
    return theme, n


def resolve_max_colours(spec, cli_value=None, theme="minimal"):
    """minimal only: 3 or 4 (default 4). Other themes ignore it."""
    if cli_value is not None:
        v = check_max_colours(cli_value, "--max-colours")
    else:
        v = check_max_colours(setting(spec, MAX_COLOURS_KEYS), "spec")
    if v is None:
        return DEFAULT_MAX_COLOURS
    if theme != "minimal":
        warn("max_colours applies to the minimal theme only; ignored in %s" % theme)
    return v


def parse_style(st):
    """draw.io style string -> ({key: value}, [bare names]); a later declaration of a key wins."""
    d, bare = {}, []
    for tok in (st or "").split(";"):
        tok = tok.strip()
        if not tok:
            continue
        if "=" in tok:
            k, v = tok.split("=", 1)
            d[k.strip()] = v.strip()
        else:
            bare.append(tok)
    return d, bare


def drop_keys(st, keys):
    """A normalised style string without the given keys. A declaration's key is compared trimmed, as
    draw.io reads it, so ' strokeWidth=7' is dropped too; the declarations kept are written as given."""
    return "".join(t + ";" for t in (st or "").split(";") if t and t.split("=", 1)[0].strip() not in keys)


def theme_palette(theme, accent, max_colours):
    """The colours a theme may paint (None: no limit). minimal: black, white, the grey tint, and the
    accent when max_colours is 4."""
    if theme == "bw":
        return {INK, PAPER}
    if theme == "minimal":
        return {INK, PAPER, TINT} | ({accent} if (max_colours or DEFAULT_MAX_COLOURS) >= 4 else set())
    return None


# draw.io shape name -> catalogue name, for the shapes behind the catalogue
CATALOGUE_DRAWIO_SHAPES = {}
for _name, _spec in SHAPES.items():
    _v = parse_style(_spec[0])[0].get("shape")
    if _v and _v not in CATALOGUE_DRAWIO_SHAPES:
        CATALOGUE_DRAWIO_SHAPES[_v] = _name

# ---- what a minimal or bw style may hold, in one place. The raw-style allow-list (check_style, for a
# cell's "style") and the audit of existing files (style_breaches) both read these, so what the
# compiler can write and what the audit permits cannot drift apart.
# keys a raw style may set: the allow-list, plus shape= and gradient / opacity keys under value rules
RAW_STYLE_KEYS = STYLE_ALLOWED_KEYS | frozenset(("shape", "gradientColor") + OPACITY_KEYS)
# keys the audit permits: the raw ones, the compiler's own, and every key of a catalogue shape's style
PERMITTED_STYLE_KEYS = (RAW_STYLE_KEYS | COMPILER_STYLE_KEYS
                        | frozenset(k for _s in SHAPES.values() for k in parse_style(_s[0])[0]))
# shape= values in an existing file: the draw.io names behind the catalogue, `line` (the legend's accent
# sample of an edge) and `swimlane` (draw.io's other spelling of the lane the compiler writes as the bare
# name `swimlane;`). A spec's own style still takes only the catalogue's names (check_style).
PERMITTED_SHAPE_VALUES = frozenset(CATALOGUE_DRAWIO_SHAPES) | frozenset(("line", "swimlane"))


def style_value_breach(k, v, palette):
    """Why one declaration k=v breaks a minimal or bw style whose key is permitted (None when it does
    not): a gradient other than none, opacity below 100, a shape outside the catalogue, a colour
    outside the palette (palette None: not checked)."""
    if k == "gradientColor":
        return None if v.lower() in NAMED_NONE else "a gradient"
    if k in OPACITY_KEYS:
        try:
            return None if float(v) >= 100 else "partial opacity"
        except ValueError:
            return "partial opacity"
    if k == "shape":
        return None if v in PERMITTED_SHAPE_VALUES else "a shape outside the catalogue"
    if k.endswith("Color") and palette is not None and v.lower() not in NAMED_NONE and _norm_colour(v) not in palette:
        return "a colour outside the palette"
    return None


# --------------------------------------------------------------------------- text measure
NARROW = set("ijl.,:;'|!`I")
SEMI = set("frt()[]-/ \u00a0\"")
WIDE = set("mwMW@%")


def text_width(s, font, bold=False):
    """Rough rendered width of one line in draw.io's default font (Helvetica/Arial metrics).
    Wide and full-width characters (Hangul, CJK) are 1 em; combining marks take no width."""
    w = 0.0
    for ch in s:
        if unicodedata.combining(ch) or unicodedata.category(ch) in ("Mn", "Me", "Cf"):
            continue
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            f = 1.0
        elif ch in NARROW:
            f = 0.25
        elif ch in SEMI:
            f = 0.31
        elif ch in WIDE:
            f = 0.86
        elif ch.isupper():
            f = 0.68
        else:
            f = 0.56
        w += f
    return w * font * (1.08 if bold else 1.0)


def wrapped_lines(text, width, font, bold_first=False, bold_all=False):
    """Lines the text takes when draw.io wraps it at `width` px (greedy, at spaces)."""
    total = 0
    for k, line in enumerate(text.split("\n")):
        bold = bold_all or (bold_first and k == 0)
        if not line:
            total += 1
            continue
        n, cur = 1, 0.0
        sp = text_width(" ", font, bold)
        for word in line.split(" "):
            ww = text_width(word, font, bold)
            add = ww if cur == 0 else sp + ww
            if cur + add <= width or cur == 0:
                cur += add
                while cur > width and width > 0:   # one word longer than the line breaks
                    n += 1
                    cur -= width
            else:
                n += 1
                cur = ww
                while cur > width and width > 0:
                    n += 1
                    cur -= width
        total += n
    return total


def style_spacing(extra):
    d, _ = parse_style(extra)
    return {k: int(float(d.get(k, 0))) for k in ("spacingLeft", "spacingRight", "spacingTop", "spacingBottom")}


# draw.io's value for an outline key that a style leaves out (size has no shape-free default)
OUTLINE_DEFAULTS = {"rounded": "0", "arcSize": "15", "absoluteArcSize": "0", "double": "0", "strokeWidth": "1",
                    "fixedSize": "0", "flipH": "0", "flipV": "0", "direction": "east", "backgroundOutline": "0"}


def _outline_value(v):
    """An outline value in one spelling: numbers as numbers (1, 1.0 and 1.00 are one value)."""
    try:
        f = float(v)
    except ValueError:
        return v.strip()
    if not math.isfinite(f):
        return v.strip()
    return ("%f" % f).rstrip("0").rstrip(".") or "0"


def outline_of(d, base=None):
    """The outline keys of a parsed style, in OUTLINE_KEYS order (dashed is kept apart by the caller),
    normalised: a value equal to the shape's default (its value in `base`, the shape's own style, else
    draw.io's default) is left out, as if absent, and numbers compare as numbers."""
    base = base or {}
    out = []
    for k in OUTLINE_KEYS:
        if k == "dashed" or k not in d:
            continue
        v = _outline_value(d[k])
        default = base.get(k, OUTLINE_DEFAULTS.get(k))
        if default is not None and v == _outline_value(default):
            continue
        out.append((k, v))
    return tuple(out)


def drawing_of(d):
    """What a parsed style draws, for comparing two boxes: the draw.io shape (a plain rectangle when the
    style names none) and every OUTLINE_KEYS value but dashed (the caller keeps it with the paint), a
    key the style leaves out taking draw.io's default; an arc size does nothing on a corner that is not
    rounded, so it is left out there. Two styles spelt differently that draw the same give one value."""
    o = {}
    for k in OUTLINE_KEYS:
        if k == "dashed":
            continue
        v = d.get(k, OUTLINE_DEFAULTS.get(k))
        if v is not None:
            o[k] = _outline_value(v)
    if o.get("rounded") == "0":
        o.pop("arcSize", None)
        o.pop("absoluteArcSize", None)
    return d.get("shape", "rect"), tuple(sorted(o.items()))


def style_number(d, key):
    """A numeric style value, or None when the key is absent or not a number."""
    try:
        v = float(d[key])
    except (KeyError, ValueError):
        return None
    return v if math.isfinite(v) else None


def delay_inset(h):
    """Right spacing that keeps text clear of a delay's round right side."""
    return max(0, int(h) // 2 - 6)


# catalogue shapes whose text inset grows with the box's height: shape -> inset(h) on the right
HEIGHT_INSETS = {"delay": delay_inset}


# Shapes whose bars, slants, fold or curve take room from the text by their `size` (and `fixedSize`):
# shape -> (sides, catalogue size, catalogue fixedSize). The catalogue's spacing is set for the
# catalogue size; a raw size (or fixedSize) changes the room by the difference. sides: "both" or
# "right" (the room is taken from the width), "share" (a share of the height, as the catalogue's
# allowance), "top_px" (px off the top), "cylinder" (the cylinder's top and bottom).
SIZE_INSETS = {
    "predefined": ("both", 8, 1), "data": ("both", 12, 1), "manual_operation": ("both", 12, 1),
    "preparation": ("both", 10, 1), "stored_data": ("both", 12, 1), "step": ("both", 10, 1),
    "note": ("right", 12, 1), "document": ("share", 0.12, 0), "offpage": ("share", 0.25, 0),
    "manual_input": ("top_px", 12, 1), "database": ("cylinder", 8, 1),
}


def size_inset(size, fixed, w):
    """draw.io's rule for a size: px when fixedSize is set, else a share of the side."""
    return max(0.0, min(w, size)) if fixed else w * max(0.0, min(1.0, size))


def label_room(w, shape_name, over=None, extra_right=0):
    """The width draw.io leaves for a box's text: labelWidth when the raw style sets it, else the
    shape's text width less its bars or insets (from the effective size and fixedSize); then less
    the spacing on both sides. May be 0 or less: then no text area remains."""
    _, room, extra, _, _ = SHAPES[shape_name]
    sp = style_spacing(extra)
    over = over or {}
    s = over.get("spacing", 4)
    left = over.get("spacingLeft", sp["spacingLeft"])
    right = over["spacingRight"] if "spacingRight" in over else sp["spacingRight"] + extra_right
    if "labelWidth" in over:
        return over["labelWidth"] - 2 * s - left - right
    width = w * (room or 1.0) - 2 * s - left - right - BAR_INSET.get(shape_name, 0)
    ins = SIZE_INSETS.get(shape_name)
    if ins and ins[0] in ("both", "right") and ("size" in over or "fixedSize" in over):
        sides = 2 if ins[0] == "both" else 1
        eff = size_inset(over.get("size", ins[1]), over.get("fixedSize", ins[2]), w)
        width -= sides * (eff - size_inset(ins[1], ins[2], w))
    return width


def needed_height(text, w, font, shape_name, bold_first=False, bold_all=False, pad=12, extra_right=0, over=None):
    """(lines, height) a box needs: lines x 1.35 x font + pad, plus the shape's allowance.
    pad 12 is the height rule ("h": "auto"); pad 8 (the label's own spacing, 4 a side) is where text
    clips. extra_right: spacing on the right that the box adds to its shape's own (delay).
    over: keys of the box's raw style ({key: number}): spacing, labelWidth, size and fixedSize replace
    the compiler's values, as they do in draw.io, so the text gets that much more or less room."""
    _, room, extra, allow_px, allow_frac = SHAPES[shape_name]
    sp = style_spacing(extra)
    over = over or {}
    s = over.get("spacing", 4)
    top = over.get("spacingTop", sp["spacingTop"])
    bottom = over.get("spacingBottom", sp["spacingBottom"])
    width = max(1.0, label_room(w, shape_name, over, extra_right))
    lines = wrapped_lines(text, width, font, bold_first, bold_all)
    ins = SIZE_INSETS.get(shape_name)
    if ins and ("size" in over or "fixedSize" in over):
        size = over.get("size", ins[1])
        if ins[0] == "share":
            allow_frac = max(0.0, min(0.9, size))
        elif ins[0] == "top_px":
            allow_px += size - ins[1]
        elif ins[0] == "cylinder":
            allow_px += (size - ins[1]) * allow_px / float(ins[1])
    need = lines * 1.35 * font + pad + 2 * (s - 4) + allow_px + (top - sp["spacingTop"]) + bottom
    if allow_frac:
        need = need / (1.0 - allow_frac)
    return lines, int(math.ceil(need))


# --------------------------------------------------------------------------- compile
class Compiler:
    def __init__(self, sheet, theme="colorful", accent=DEFAULT_ACCENT, max_colours=DEFAULT_MAX_COLOURS, pages=None):
        self.s = sheet
        self.pages = pages if pages is not None else {sheet["name"]: sheet["id"]}   # sheet name -> diagram id
        self.sid = sheet["id"]
        self.theme = theme
        self.accent = accent
        self.max_colours = max_colours
        self.shaped = theme != "colorful"
        # accent as colour, or as a black stroke of width 4 (bw; minimal with 3 colours)
        self.accent_as_width = theme == "bw" or (theme == "minimal" and max_colours < 4)
        self.tint = TINT if theme == "minimal" else PAPER
        self.palette = theme_palette(theme, accent, max_colours)
        self.recs = []          # vertex records, in compile order
        self.edges = []
        self.ids = {}
        self.decoded = {}       # id as read back from the file -> id as given
        self.n = 0
        self.vertices = 0
        self.legend_vertices = 0
        self.looks = []         # (cell, look key, sample info, meaning, sort key)
        self.legend_boxes = []
        g = sheet.get("grid") or {}
        self.gx0, self.gpitch, self.gcw = g.get("x0", 0), g.get("pitch", 0), g.get("col_width", 0)

    def nid(self, user_id=None):
        self.n += 1
        raw = user_id or "auto%d" % self.n
        full = "%s_%s" % (self.sid, raw)
        if full in self.ids:
            raise SystemExit("duplicate cell id %r in sheet %r" % (raw, self.s["name"]))
        # two ids must stay two ids once the file is parsed
        seen = self.decoded.setdefault(xml_decode(self.idattr(full)), full)
        if seen != full:
            raise SystemExit("cell ids %r and %r in sheet %r are the same id once the file is read (%r); rename one" % (
                seen.split("_", 1)[1], raw, self.s["name"], xml_decode(self.idattr(full))))
        self.ids[full] = True
        return full

    def idattr(self, v):
        """An id, or a reference to one, as written in the file: a literal string (lit_attr). In
        colorful an id that was already valid XML is written as typed, as before themes existed (it
        then reads back decoded; nid stops when two ids would meet that way)."""
        v = str(v)
        if not self.shaped and attr(v) == v:
            return v
        return lit_attr(v)

    def geom(self, c, default_w=None, default_h=None):
        if not self.shaped:
            # colorful: the rules of the script before themes existed, every fallback included (x from
            # col is x0 + pitch * col with any grid value missing taken as 0; w from span likewise; any
            # other cell without w takes col_width, else 120; h defaults to 60)
            x = c.get("x")
            if x is None and "col" in c:
                x = self.gx0 + self.gpitch * c["col"]
            if x is None:
                x = 0
            w = c.get("w")
            if w is None and "span" in c:
                w = self.gpitch * (c["span"] - 1) + self.gcw
            if w is None:
                w = self.gcw or default_w or 120
            y = c.get("y", 0)
            h = c.get("h", default_h or 60)
            if h == "auto":
                if c.get("type", "box") != "box":
                    raise SystemExit('%s: "h": "auto" is for boxes only' % self.where(c))
                return int(x), int(y), int(w), "auto"
            return int(x), int(y), int(w), int(h)
        # minimal, bw: each value taken from the grid needs only the grid values its formula uses: x from
        # col needs pitch (x0 defaults to 0); a cell placed by col or span that gives no w takes col_width
        # (and pitch when span is over 1). A cell that gives its own x or w needs nothing from the grid.
        x = c.get("x")
        if x is None and "col" in c:
            if not self.gpitch > 0:
                raise SystemExit("%s uses col/span, but the sheet's grid has no pitch above 0, which x = x0 + pitch * col "
                                 "needs (set grid.pitch, or give x)" % self.where(c))
            x = self.gx0 + self.gpitch * c["col"]
        if x is None:
            x = 0
        w = c.get("w")
        if w is None and ("col" in c or "span" in c):
            span = c.get("span", 1)
            if not self.gcw > 0 or (span != 1 and not self.gpitch > 0):
                raise SystemExit("%s uses col/span and gives no w, but the sheet's grid lacks what w = pitch * (span - 1) "
                                 "+ col_width needs (set grid.col_width%s above 0, or give w)" % (
                                     self.where(c), "" if span == 1 else " and grid.pitch"))
        if w is None and "span" in c:
            w = self.gpitch * (c["span"] - 1) + self.gcw
        if w is None:
            w = self.gcw or default_w or 120
        y = c.get("y", 0)
        h = c.get("h", default_h or 60)
        if h == "auto":
            if c.get("type", "box") != "box":
                raise SystemExit('%s: "h": "auto" is for boxes only' % self.where(c))
            return int(x), int(y), int(w), "auto"
        return int(x), int(y), int(w), int(h)

    def where(self, c):
        if "id" not in c and c.get("type") == "edge":
            return "sheet %r edge %s -> %s" % (self.s["name"], c.get("from"), c.get("to"))
        return "sheet %r cell %r" % (self.s["name"], c.get("id", (c.get("text") or "")[:30]))

    def shape_of(self, c):
        sh = c.get("shape")
        if sh is not None and sh not in SHAPES:
            raise SystemExit("unknown shape %r in %s; valid shapes: %s" % (sh, self.where(c), ", ".join(sorted(SHAPES))))
        return sh

    def cell_palette(self, c):
        """The colours one cell or edge may paint. In minimal the accent colour belongs only to a cell
        or edge that sets accent, so the legend's accent row explains every use of it. Only a box or
        an edge can set accent; on any other type the key is unknown and unlocks nothing."""
        accented = c.get("accent") and c.get("type", "box") in ("box", "edge")
        if self.theme == "minimal" and self.accent in self.palette and not accented:
            return self.palette - {self.accent}
        return self.palette

    def colour_override(self, c, key, default):
        """A cell's fill / stroke / color override. colorful takes any colour; minimal and bw keep
        one inside the theme's palette and ignore any other, with a warning."""
        if key not in c:
            return default
        if self.palette is None:
            return c[key]
        v = _norm_colour(str(c[key]))
        allowed = self.cell_palette(c)
        if v in allowed:
            return v
        if v in self.palette:
            warn("%s: %s=%r is the accent colour, which only a cell or edge that sets accent may use; ignored" % (
                self.where(c), key, c[key]))
        else:
            warn("%s: %s=%r is outside the palette (%s); ignored in the %s theme" % (
                self.where(c), key, c[key], " ".join(sorted(allowed)), self.theme))
        return default

    def check_style(self, c):
        """minimal and bw: a 'style' string passes an allow-list (geometry, spacing, typography,
        perimeter, arc, dashed, strokeWidth, rotation, the catalogue's shapes, and colour keys whose
        values are in the palette). Tokens are trimmed and the trimmed string is what gets written,
        so the audit reads exactly what draw.io reads."""
        st = c.get("style", "")
        # in every theme: the script's own markers (theme record, legend mark) are not a spec's to write
        for tok in (st if isinstance(st, str) else "").split(";"):
            k = tok.split("=", 1)[0].strip()
            if "=" in tok and k in RESERVED_STYLE_KEYS:
                raise SystemExit("%s: style key %s is reserved: the script writes it to record the file's theme or "
                                 "mark its legend, and list, validate and add read it back. Drop it from the style "
                                 "(copied from a built file?)" % (self.where(c), k))
        if not st or not self.shaped:
            return st
        out = []
        for tok in st.split(";"):
            tok = tok.strip()
            if not tok:
                continue
            if "=" not in tok:
                raise SystemExit("%s: style token %r is a named draw.io style (it brings its own paint); "
                                 "not allowed in theme %s" % (self.where(c), tok, self.theme))
            k, v = (s.strip() for s in tok.split("=", 1))
            if k == "html":
                # the compiler writes HTML labels (bold headline, <br>), so html stays 1
                if v != "1":
                    warn("%s: style html=%s ignored; the compiler writes HTML labels (html=1)" % (self.where(c), v))
                continue
            if k == "gradientColor" or k in OPACITY_KEYS:
                if style_value_breach(k, v, None):
                    raise SystemExit("%s: style key %s=%s paints shades between the palette colours (a gradient or "
                                     "partial opacity); not allowed in theme %s" % (self.where(c), k, v, self.theme))
            elif k == "shape":
                if v not in CATALOGUE_DRAWIO_SHAPES:
                    raise SystemExit("%s: style key shape=%s is not a catalogue shape; not allowed in theme %s "
                                     "(use the cell's \"shape\": %s)" % (self.where(c), v, self.theme, ", ".join(sorted(SHAPES))))
            elif k not in RAW_STYLE_KEYS:
                raise SystemExit("%s: style key %r is not allowed in theme %s; allowed: geometry, spacing, "
                                 "typography, perimeter, arc, dashed, strokeWidth, rotation, catalogue shapes, "
                                 "and the colour keys %s with palette values" % (
                                     self.where(c), k, self.theme, ", ".join(STYLE_COLOUR_KEYS)))
            elif k in STYLE_COLOUR_KEYS and v.lower() not in NAMED_NONE and _norm_colour(v) not in self.cell_palette(c):
                if _norm_colour(v) in self.palette:
                    raise SystemExit("%s: style key %s=%s is the accent colour, which only a cell or edge that sets "
                                     "accent may use" % (self.where(c), k, v))
                raise SystemExit("%s: style key %s=%s is outside the %s palette (%s)" % (
                    self.where(c), k, v, self.theme, " ".join(sorted(self.cell_palette(c)))))
            out.append("%s=%s;" % (k, v))
        return "".join(out)

    # ---- styles shared by boxes
    def shape_style(self, shape_name, font, fill, stroke, align, valign, extra="", ink=INK):
        base, _, spacing, _, _ = SHAPES[shape_name]
        if shape_name == "actor":
            align, valign = "center", "top"
        if shape_name in CENTRED_SHAPES:
            align, valign = "center", "middle"
        return "%swhiteSpace=wrap;html=1;fontSize=%d;fillColor=%s;strokeColor=%s;fontColor=%s;align=%s;verticalAlign=%s;spacing=4;%s%s" % (
            base, font, fill, stroke, ink, align, valign, spacing, extra)

    def role_paint(self, role, frame=False):
        """(fill, stroke, dashed) of a role in the current theme."""
        if self.theme == "colorful":
            return ROLES[role]
        if frame:
            return self.tint, INK, self.theme == "bw"
        if self.theme == "minimal" and role in TINTED_ROLES:
            return self.tint, INK, False
        return PAPER, INK, False

    def add_rec(self, i, text, style, x, y, w, h, kind, counted=True, headline=False, head=0, auto=False):
        if counted:
            self.vertices += 1
        else:
            self.legend_vertices += 1
        # colorful keeps the old label encoder for what the old script drew (not for the generated legend)
        raw = self.theme == "colorful" and kind not in ("legend", "legend_row")
        self.recs.append({"id": i, "text": text, "style": style, "x": x, "y": y, "w": w, "h": h,
                          "kind": kind, "counted": counted, "headline": headline, "parent": None,
                          "head": head, "auto": auto, "raw": raw, "link": None, "strip": None})

    def text_fit(self, c, text, w, h, font, bold, what):
        """A text cell or lane title clips when its wrapped lines need more than its height (a text
        cell has 2 px spacing on each side)."""
        if not text or w <= 4:
            return
        lines = wrapped_lines(text, w - 4, font, bold_all=bold)
        need = int(math.ceil(lines * 1.35 * font + 4))
        if need > h:
            warn("sheet %r %s %r clips: %d wrapped lines at font %d need h>=%d (h=%d, w=%d)" % (
                self.s["name"], what, c.get("id", text[:30]), lines, font, need, h, w))

    def lane_column(self, c, w, h):
        """Whether a lane is a column lane: its "orientation" ("row" or "column") when given, else by
        shape (taller than wide is a column)."""
        o = c.get("orientation")
        if o is None:
            return h > w
        if o not in ("row", "column"):
            raise SystemExit('%s: orientation %r must be "row" or "column"' % (self.where(c), o))
        return o == "column"

    def lane_gutter(self, c, default, limit, side):
        """A lane's title strip size: its gutter (a number from 0 to below the lane's width on a row,
        height on a column) or the default; any other value stops the run."""
        size = c.get("gutter", default)
        if isinstance(size, bool) or not isinstance(size, (int, float)) or not 0 <= size < limit:
            raise SystemExit("%s: gutter %r must be a number from 0 to the lane's %s (%d)" % (
                self.where(c), size, side, limit))
        return int(size)

    def link_target(self, c):
        """The diagram id a cell's "link" opens (None without a link); a name that is no sheet of
        the file stops the run."""
        if "link" not in c:
            return None
        target = c["link"]
        if not isinstance(target, str) or target not in self.pages:
            raise SystemExit("%s: link %r names no sheet of this file (sheets: %s)" % (
                self.where(c), target, ", ".join(sorted(self.pages))))
        return self.pages[target]

    def swimlane(self, c, x, y, w, h, font, text):
        """minimal, bw: a lane is one draw.io swimlane, a container whose title strip holds its title.
        A row lane has its strip on the left with the title written across it (direction=north turns
        the strip to the left side; the text stays horizontal); a column lane has its strip on top. The
        strip is grey in minimal, white in bw; the body is always white."""
        column = self.lane_column(c, w, h)
        tfont = max(font, 10)
        if not text:
            size = 0    # an untitled lane has no strip, whatever gutter says
        else:
            if column:
                # strip height: the title's wrapped lines across the lane's width
                lines = wrapped_lines(text, max(1, w - 4), tfont, bold_all=True)
                default = int(math.ceil(lines * 1.35 * tfont + 12))
            else:
                default = LANE_GUTTER
            size = self.lane_gutter(c, default, h if column else w, "height" if column else "width")
        # the body is white: edges and outlines are black and frames and notes grey, so any other body
        # would hide them or the text on it
        if "fill" in c and _norm_colour(str(c["fill"])) != PAPER:
            warn("%s: fill=%r ignored; in theme %s a lane's body is always white, so the edges, outlines and text "
                 "inside it stay visible" % (self.where(c), c["fill"], self.theme))
        style = ("swimlane;%sstartSize=%d;html=1;whiteSpace=wrap;fontSize=%d;fontStyle=1;fontColor=%s;fillColor=%s;"
                 "swimlaneFillColor=%s;strokeColor=%s;align=center;verticalAlign=middle;%s") % (
            "" if column else "direction=north;", size, tfont, INK, self.tint, PAPER, INK, CONTAINER_KEY)
        self.add_rec(self.nid(c.get("id")), text, style, x, y, w, h, "lane", headline=False)
        self.recs[-1]["strip"] = ("top" if column else "left", size)
        self.recs[-1]["link"] = self.link_target(c)
        if text:
            # a strip too small for one line of the title: on a row lane narrower than one character
            # (plus the 2 px spacing a side), on a column lane lower than one line
            one = int(math.ceil(1.35 * tfont + 4)) if column else tfont + 4
            if size < one:
                warn("sheet %r lane title %r has no room: its title strip is %d px %s, and one line needs %d; widen "
                     "gutter" % (self.s["name"], c.get("id", text[:30]), size, "high" if column else "wide", one))
            elif column:
                self.text_fit(c, text, w, size, tfont, True, "lane title")
            else:
                self.text_fit(c, text, size, h, tfont, True, "lane title")

    def vertex(self, c):
        t = c.get("type", "box")
        x, y, w, h = self.geom(c)
        role = c.get("role", "plain")
        if role not in ROLES:
            raise SystemExit("unknown role %r in sheet %r" % (role, self.s["name"]))
        font = c.get("font", 9)
        text = c.get("text", "")
        if t == "lane":
            if self.shaped:
                self.swimlane(c, x, y, w, h, font, text)
                return
            # colorful: a tinted rectangle and a title text cell in a gutter on the left (the original look)
            # an explicit "orientation": "column" puts the title across the top, in a strip as high as the
            # swimlane's in minimal and bw; a lane without the key keeps the left gutter whatever its shape
            top_title = c.get("orientation") is not None and self.lane_column(c, w, h)
            if not top_title and self.lane_column(c, w, h):
                warn("%s: this lane is a column by its shape, but without \"orientation\": \"column\" colorful puts "
                     "its title in a gutter on the left, over the boxes placed below a top strip. Set "
                     "\"orientation\": \"column\" on it" % self.where(c))
            fill = c.get("fill", LANE_FILLS.get(role, "#F5F5F5"))
            style = "rounded=0;whiteSpace=wrap;html=1;fontSize=%d;fillColor=%s;strokeColor=%s;align=left;verticalAlign=top;spacingLeft=6;" % (font, fill, "#BDBDBD")
            link = self.link_target(c)
            i = self.nid(c.get("id"))
            self.add_rec(i, "", style, x, y, w, h, "lane")
            if text and top_title:
                # lane title across the top, in a strip as high as the swimlane strip in minimal and bw
                tfont = max(font, 10)
                lines = wrapped_lines(text, max(1, w - 4), tfont, bold_all=True)
                sh = self.lane_gutter(c, int(math.ceil(lines * 1.35 * tfont + 12)), h, "height")
                tstyle = "text;html=1;whiteSpace=wrap;fontSize=%d;align=center;verticalAlign=middle;fontStyle=1;" % tfont
                self.add_rec(self.nid(), text, tstyle, x, y, w, sh, "text")
                self.text_fit(c, text, w, sh, tfont, True, "lane title")
            elif text:
                # lane title in a gutter on the left, vertically centred, written as before themes. A gutter
                # that is not a number stops the run (the script before themes failed on it); one below 0 or
                # not narrower than the lane is drawn as before, with a warning
                gw = c.get("gutter", LANE_GUTTER)
                if not isinstance(gw, (int, float)):
                    self.lane_gutter(c, LANE_GUTTER, w, "width")
                elif isinstance(gw, bool) or not 0 <= gw < w:
                    warn("%s: gutter %r is not a number from 0 to below the lane's width (%d); its title cell is "
                         "drawn %d px wide, as before themes, and may not show. minimal and bw stop on it" % (
                             self.where(c), gw, w, int(gw)))
                j = self.nid()
                tfont = max(font, 10)
                tstyle = "text;html=1;whiteSpace=wrap;fontSize=%d;align=center;verticalAlign=middle;fontStyle=1;" % tfont
                self.add_rec(j, text, tstyle, x + 4, y, gw, h, "text")
                self.text_fit(c, text, gw, h, tfont, True, "lane title")
            # a link goes on the title, the cell a click lands on (on the rectangle when untitled)
            self.recs[-1]["link"] = link
            return
        if t == "text":
            style = "text;html=1;whiteSpace=wrap;fontSize=%d;align=%s;verticalAlign=middle;" % (font, c.get("align", "left"))
            if c.get("bold", True):
                style += "fontStyle=1;"
            i = self.nid(c.get("id"))
            self.add_rec(i, text, style, x, y, w, h, "text")
            self.text_fit(c, text, w, h, font, c.get("bold", True), "text")
            return
        if t != "box":
            if self.shaped:
                raise SystemExit("unknown cell type %r in %s" % (t, self.where(c)))
            # colorful: drawn as a box, as the script before themes did
            warn("%s: unknown cell type %r; drawn as a box (valid types: box, lane, text, edge, legend)" % (self.where(c), t))
        # box
        shape = self.shape_of(c)
        frame = bool(c.get("square"))
        if frame:
            shape = None   # a frame is a plain square rectangle in every theme; a shape it names is dropped
        if self.shaped and shape == ROLE_SHAPES[role]:
            shape = None   # the role's own shape, stated: the same as no shape (no warning, role meaning)
        accent = bool(c.get("accent"))
        bold = bool(c.get("bold"))
        if self.shaped and ("\n" in text or frame):
            # minimal, bw: a frame, and text of more than one line, sit at the top left unless the box
            # says otherwise
            align, valign = c.get("align", "left"), c.get("valign", "top")
        else:
            align, valign = c.get("align", "center"), c.get("valign", "middle")
        user_style = self.check_style(c)
        ud, _ = parse_style(user_style)
        # what draw.io will draw: the raw style's font size, bold flag and spacing replace the cell's
        # (the raw style is written last), so the fit check, "h": "auto" and a frame's headline room
        # measure with them
        m_font = style_number(ud, "fontSize") or font
        m_bold = bold if style_number(ud, "fontStyle") is None else bool(int(style_number(ud, "fontStyle")) & 1)
        over = {k: style_number(ud, k) for k in FIT_STYLE_KEYS if k != "fontSize" and style_number(ud, k) is not None}
        styled = CATALOGUE_DRAWIO_SHAPES.get(ud.get("shape")) if "shape" in ud else None
        if self.shaped and shape is None and styled == ROLE_SHAPES[role]:
            styled = None
        if frame and self.shaped and "shape" in ud:
            raise SystemExit("%s: a frame is a square rectangle; drop shape= from its style" % self.where(c))
        legend_shape = shape
        if styled and not frame:
            legend_shape = styled       # the legend shows the shape that is drawn
            if self.shaped:
                shape = styled
        # minimal, bw: the headline of a multi-line box, and of any frame, is bold
        headline = bool(self.shaped and c.get("headline", True) and not bold and text
                        and ("\n" in text or frame))
        if frame or (self.theme == "colorful" and shape is None):
            shape_name = "process"   # a frame stays a plain square rectangle in every theme
        elif shape is not None:
            shape_name = shape
        else:
            shape_name = ROLE_SHAPES[role]
        if self.shaped:
            # keys the compiler enforces win over the raw style: a frame stays square, and a decision,
            # cloud or terminator keeps its text centred. A conflicting raw key is dropped with a warning,
            # so the compiler's value is the only declaration of that key.
            enforced = {}
            if frame:
                enforced["rounded"] = "0"
                if self.theme == "bw":
                    enforced["dashed"] = "1"     # the dashed border is the only frame cue in bw
            if shape_name in CENTRED_SHAPES:
                enforced.update(align="center", verticalAlign="middle")
            clash = [t for t in user_style.split(";") if t and t.split("=", 1)[0] in enforced
                     and t.split("=", 1)[1] != enforced[t.split("=", 1)[0]]]
            if clash:
                warn("%s: style %s conflicts with what the compiler keeps on a %s (%s); ignored" % (
                    self.where(c), ";".join(clash), "frame" if frame else shape_name,
                    ";".join("%s=%s" % kv for kv in enforced.items())))
            if enforced:
                user_style = drop_keys(user_style, tuple(enforced))
        centre_after = ""
        if not self.shaped and shape is not None and shape_name in CENTRED_SHAPES:
            # colorful, a catalogue shape chosen by the cell: it centres its text as in the other themes.
            # The raw style is kept as it was; the centring is written after it, so it is what draw.io reads.
            keep = {"align": "center", "verticalAlign": "middle"}
            toks = [t.strip() for t in user_style.split(";") if "=" in t and t.split("=", 1)[0].strip() in keep]
            if toks:
                clash = [t for t in toks if t.split("=", 1)[1].strip() != keep[t.split("=", 1)[0].strip()]]
                if clash:
                    warn("%s: style %s conflicts with what the compiler keeps on a %s (%s); ignored" % (
                        self.where(c), ";".join(clash), shape_name, ";".join("%s=%s" % kv for kv in keep.items())))
                centre_after = "align=center;verticalAlign=middle;"
        if self.shaped:
            mark = SHAPE_MARK.get(shape_name)
            if mark and mark[0] in ud and ud[mark[0]] != mark[1]:
                warn("%s: style %s=%s removes what marks a %s shape (%s=%s), so the box no longer looks like its "
                     "legend sample; drop it" % (self.where(c), mark[0], ud[mark[0]], shape_name, mark[0], mark[1]))
        # height: given, or computed from the wrapped text ("h": "auto"). A delay's right inset
        # depends on its height, so the two are computed together.
        has_room = SHAPES[shape_name][1] is not None and bool(text)

        def fit(right, pad):
            if not has_room:
                return 0, 0
            return needed_height(text, w, m_font, shape_name, bold_first=headline, bold_all=m_bold, pad=pad,
                                 extra_right=right, over=over)
        inset = 0
        grows = HEIGHT_INSETS.get(shape_name)     # an inset that grows with the height (delay)
        if has_room:
            room_w = label_room(w, shape_name, over, grows(h) if grows and h != "auto" else 0)
            if room_w < 1:
                warn("%s: no text area remains: the box's width, its shape's insets and the spacing%s leave %d px for "
                     "the text; widen the box or reduce them" % (
                         self.where(c), " and labelWidth" if "labelWidth" in over else "", int(math.floor(room_w))))
        if h == "auto":
            if frame:
                raise SystemExit('%s: "h": "auto" is not available on a frame; a frame\'s height must be a number' % self.where(c))
            if SHAPES[shape_name][1] is None:
                raise SystemExit('%s: "h": "auto" is not available for shape %r' % (self.where(c), shape_name))
            one_char = text_width("n", m_font)
            for _ in range(60):
                lines, need = fit(inset, 12)
                h = max(need, int(math.ceil(1.35 * m_font + 12)))
                nxt = grows(h) if grows else 0
                if grows and has_room:
                    # the inset takes width as the box grows: each pass checks the width that is left
                    left = label_room(w, shape_name, over, nxt)
                    if left < one_char:
                        raise SystemExit("%s: this %s is too narrow for its text: as its height grows to %d px its "
                                         "round side takes %d px, leaving %d px, less than one character; widen the box" % (
                                             self.where(c), shape_name, h, nxt, int(math.floor(left))))
                if nxt == inset:
                    break
                inset = nxt
            else:
                raise SystemExit("%s: the height of this %s does not settle for its text; widen the box" % (
                    self.where(c), shape_name))
            print("auto h: sheet %r box %r h=%d (%d lines, font %g)" % (self.s["name"], c.get("id", text[:30]), h, lines, m_font))
            auto = True
        else:
            auto = False
            if grows:
                inset = grows(h)
            lines, need = fit(inset, 12)
            _, clip = fit(inset, 8)
            if clip > h and not frame:
                warn("sheet %r box %r text clips: %d wrapped lines at font %g need h>=%d (h=%d, w=%d, shape %s)" % (
                    self.s["name"], c.get("id", text[:30]), lines, m_font, need, h, w, shape_name))
        a_col, a_w = self.accent_stroke()
        # the accent is applied after every override (the cell's stroke and the raw style's stroke keys),
        # so an accented box is drawn as the legend's accent sample shows it
        unstroked = drop_keys(user_style, ("strokeColor", "strokeWidth"))
        defer_ink = False
        if self.theme == "colorful" and shape is None:
            # the original look, byte for byte
            fill, stroke, dashed = ROLES[role]
            fill = c.get("fill", fill)
            base_stroke = c.get("stroke", stroke)
            dashed = c.get("dashed", dashed)

            def paint(st_stroke, width, ustyle):
                s = "rounded=%d;whiteSpace=wrap;html=1;fontSize=%d;fillColor=%s;strokeColor=%s;align=%s;verticalAlign=%s;spacing=4;" % (
                    0 if frame else 1, font, fill, st_stroke, align, valign)
                s += ("fontStyle=1;" if bold else "") + ("dashed=1;" if dashed else "")
                return s + ("strokeWidth=%d;" % width if width else "") + ustyle
            style = paint(a_col, a_w, unstroked) if accent else paint(base_stroke, 0, user_style)
            # the legend groups a box by its look without the accent; on an accented box the accent
            # supersedes the stroke colour and width of its fields and raw style, so they are left out
            look_style = paint(stroke, 0, unstroked) if accent else paint(base_stroke, 0, user_style)
        else:
            fill, role_stroke, dashed = self.role_paint(role, frame)
            fill = self.colour_override(c, "fill", fill)
            stroke = self.colour_override(c, "stroke", role_stroke)
            dashed = c.get("dashed", dashed)
            if frame and self.theme == "bw" and not dashed:
                warn("%s: dashed=%s conflicts with what the compiler keeps on a frame in bw (dashed=1); ignored" % (
                    self.where(c), json.dumps(c.get("dashed"))))
                dashed = True
            ink = INK
            if shape_name != "actor":     # an actor's label sits below it, on the page
                # what is behind the text: the label background when set, else the box's own fill;
                # a box without fill shows the surface behind it, known only after nesting
                behind, what = text_behind(ud, fill)
                if behind is None:
                    defer_ink = True
                elif "fontColor" in ud:
                    if norm_paint(ud["fontColor"]) == behind:
                        warn("%s: fontColor=%s is %s; its text cannot be read" % (self.where(c), ud["fontColor"], what))
                else:
                    ink = text_on(behind)     # black or white, whichever contrasts more
            if shape_name in CENTRED_SHAPES:
                if c.get("align", "center") != "center" or c.get("valign", "middle") != "middle":
                    warn("%s: a %s centres its text; align/valign ignored" % (self.where(c), shape_name))
                if "\n" in text:
                    warn("%s: a %s holds one short line; this text has %d" % (self.where(c), shape_name, text.count("\n") + 1))
            if shape is not None and self.shaped and role not in shape_roles(shape):
                warn("%s: shape %r hides role %r in theme %s; the box no longer shows its role (shape %s carries %s)" % (
                    self.where(c), shape, role, self.theme, shape, ", ".join(shape_roles(shape))))
            extra = "spacingRight=%d;" % inset if shape_name == "delay" else ""   # clear the round right side

            def paint(st_stroke, ustyle):
                s = self.shape_style(shape_name, font, fill, st_stroke, align, valign, extra, ink)
                s += ("fontStyle=1;" if bold else "") + ("dashed=1;" if dashed else "")
                if frame and self.shaped:
                    s += CONTAINER_KEY
                s += ustyle
                if centre_after:      # colorful: the centring after the raw style
                    s += (";" if not s.endswith(";") else "") + centre_after
                return s
            # the legend's look without the accent: on an accented box the accent supersedes the stroke
            # colour and width of its fields and raw style, so the role's own stroke stands in for them
            look_style = paint(role_stroke, unstroked) if accent else paint(stroke, user_style)
            if self.theme == "bw" and not frame and parse_style(look_style)[0].get("dashed") == "1":
                warn("%s: a dashed outline marks a frame (a group of parts) in bw, so this box reads as one; drop "
                     "dashed, or make it a frame (\"square\": true)" % self.where(c))
            if not accent:
                style = look_style
                self.check_plain_width(c, style)
            elif not self.shaped:     # colorful with a catalogue shape: the width before the raw style, as before
                style = paint(a_col, "strokeWidth=%d;" % a_w + unstroked)
            else:
                style = paint(a_col, unstroked) + "strokeWidth=%d;" % a_w
        head = 0
        if frame and text:
            head = needed_height(text, w, m_font, "process", bold_first=headline, bold_all=m_bold, over=over)[1]
        i = self.nid(c.get("id"))
        self.add_rec(i, text, style, x, y, w, h, "frame" if frame else "box", headline=headline, head=head, auto=auto)
        self.recs[-1]["shape"] = shape_name
        self.recs[-1]["own_ink"] = "fontColor" in ud
        self.recs[-1]["defer_ink"] = defer_ink
        if shape_name == "actor":
            # the caption below the figure: measured for the page, holder and overlap checks
            self.recs[-1]["caption"] = actor_caption(x, y, w, h, text, style)
        # a click on the box opens the named sheet (draw.io's page link on a UserObject)
        self.recs[-1]["link"] = self.link_target(c)
        self.note_look(c, role, legend_shape, shape_name, frame, look_style, unstroked if accent else user_style)

    def accent_stroke(self):
        """(colour, width) of the accent, shared by accented boxes, accented edges and the legend's
        accent sample: a black line of width 4 in bw and in minimal with 3 colours, else the accent
        colour at width 2."""
        return (INK, ACCENT_WIDTH) if self.accent_as_width else (self.accent, 2)

    def check_plain_width(self, c, style):
        """bw, and minimal with 3 colours: the accent is a black line of width 4, so a box or edge
        without accent that is wider than 2 could be read as the accent."""
        if not self.accent_as_width:
            return
        try:
            sw = float(parse_style(style)[0].get("strokeWidth", 1))
        except ValueError:
            return
        if sw > PLAIN_WIDTH_MAX:
            warn("%s: line width %g; in theme %s%s the accent is a black line of width %d, so a line without "
                 "accent may be at most %d wide" % (self.where(c), sw, self.theme,
                                                    " with max_colours 3" if self.theme == "minimal" else "",
                                                    ACCENT_WIDTH, PLAIN_WIDTH_MAX))

    # ---- generated legend
    @staticmethod
    def meaning_text(meaning, labels):
        """A legend row's meaning in the words of one legend: meaning is (kind, role, shape), kind one of
        role, shape, shape_role ('<shape meaning>: <role meaning>') and frame; labels is that legend's
        own labels, so two legends on one sheet each print their own."""
        kind, role, shape = meaning
        rmean = labels.get(role, ROLE_MEANINGS[role]) if role is not None else None
        if kind == "role":
            return rmean
        if kind == "frame":
            return labels.get("frame", FRAME_MEANING)
        smean = labels.get(shape, SHAPE_MEANINGS[shape])
        return "%s: %s" % (smean, rmean) if kind == "shape_role" else smean

    def note_look(self, c, role, shape, shape_name, frame, style, user_style=""):
        """Record what a box looks like (its final shape, paint and outline, without its accent) and
        what it means, for the legend. Colours are normalised so one paint spelled two ways is one
        row. The outline is every OUTLINE_KEYS value of the drawn style (minimal, bw), or of the raw
        style (colorful, where the role row is a colour swatch), so a sample looks like its boxes."""
        d, _ = parse_style(style)
        fill = norm_paint(d.get("fillColor", "none"))
        stroke = norm_paint(d.get("strokeColor", "none"))
        dashed = d.get("dashed", "0") == "1"
        # meanings are kept as (kind, role, shape) and put into words by each legend (meaning_text)
        rmean = ("role", role, None)
        rkey = (0, list(ROLES).index(role))
        if self.theme == "colorful":
            # roles by colour (frames included), catalogue shapes by outline in plain colours; a raw value
            # equal to the swatch's default (rounded) or the shape's own is the same as none. Rows are
            # grouped by what is drawn (drawing_of), whatever the names behind it
            ud = parse_style(user_style)[0]
            raw = outline_of(ud, {"rounded": "1"})
            swatch = drawing_of(parse_style("rounded=1;" + drop_keys(user_style, ("shape",)))[0])
            self.looks.append((c, ("role", fill, stroke, dashed, swatch), ("role", role, fill, stroke, dashed, raw), rmean,
                               rkey, (role, None)))
            if shape is not None:
                pf, ps, _ = ROLES["plain"]
                sraw = outline_of(ud, parse_style(SHAPES[shape][0])[0])
                drawn = drawing_of(parse_style(SHAPES[shape][0] + user_style)[0])
                self.looks.append((c, ("shape", drawn), ("shape", shape, pf, ps, False, sraw),
                                   ("shape", None, shape), (1, list(SHAPES).index(shape)), (None, shape)))
            return
        outline = outline_of(d, parse_style(SHAPES[shape_name][0])[0])
        # grouped by the drawing after every override (draw.io shape, outline, paint), not by the names
        # behind it: two boxes drawn alike share one row, and two roles drawn alike warn
        look = ("shape", drawing_of(d), fill, stroke, dashed)
        if frame:
            # minimal, bw: frames get a row of their own, drawn as the frame is drawn
            self.looks.append((c, look, ("shape", "process", fill, stroke, dashed, outline),
                               ("frame", None, None), (2, 0), ("frame", None)))
            return
        if shape is not None:
            meaning, key = ("shape", None, shape), (1, list(SHAPES).index(shape))
            if role in shape_roles(shape):
                meaning = ("shape_role", role, shape)
        else:
            meaning, key = rmean, rkey
        self.looks.append((c, look, ("shape", shape_name, fill, stroke, dashed, outline), meaning, key, (role, shape)))

    def legend_items(self, labels=None):
        """Legend rows: one per distinct appearance, in role then shape order; an appearance with
        two meanings shows both. The accent comes last. labels: the legend's own labels."""
        labels = labels if isinstance(labels, dict) else {}
        groups = {}
        variants = {}
        for c, look, sample, meaning, key, who in self.looks:
            meaning = self.meaning_text(meaning, labels)
            g = groups.setdefault(look, {"sample": sample, "meanings": [], "key": key})
            g["key"] = min(g["key"], key)
            if meaning not in g["meanings"]:
                g["meanings"].append(meaning)
            variants.setdefault(who, set()).add(look)
        for (role, shape), looks in variants.items():
            if len(looks) > 1:
                what = "frames" if role == "frame" else ("boxes of %s" % (
                    ("role %r" % role) if shape is None else ("shape %r" % shape)))
                warn("sheet %r: %s are painted %d different ways; the legend shows each" % (
                    self.s["name"], what, len(looks)))
        items = sorted(groups.values(), key=lambda g: g["key"])
        rows = [(g["sample"], "; ".join(g["meanings"])) for g in items]
        box_accent = any(c.get("accent") and c.get("type", "box") == "box" for c in self.s["cells"])
        edge_accent = any(c.get("accent") and c.get("type") == "edge" for c in self.s["cells"])
        if box_accent or edge_accent:
            # only edges carry it: the sample is a line, as the edges are
            rows.append((("accent", "box" if box_accent else "edge"), labels.get("accent", "highlighted")))
        return rows

    def sample_style(self, sample, font):
        """The legend sample: the shape and paint of the boxes it describes, label to its right."""
        if sample[0] == "accent":
            col, width = self.accent_stroke()
            if sample[1] == "edge":
                return "shape=line;html=1;fillColor=none;strokeColor=%s;strokeWidth=%d;" % (col, width)
            return "rounded=0;html=1;fillColor=%s;strokeColor=%s;strokeWidth=%d;" % (PAPER, col, width)
        kind, name, fill, stroke, dashed, outline = sample
        out = dict(outline)  # the boxes' own outline: rounded, double, line width, size, flips ...
        if kind == "role":   # colorful: a colour swatch, rounded unless the boxes' raw style says otherwise
            st = "rounded=%s;html=1;fillColor=%s;strokeColor=%s;" % (out.pop("rounded", "1"), fill, stroke)
            st += "".join("%s=%s;" % kv for kv in out.items())
        else:
            d, bare = parse_style(SHAPES[name][0])
            for k in ("boundedLbl", "verticalLabelPosition", "labelPosition"):
                d.pop(k, None)
            d.update(out)
            st = "".join(b + ";" for b in bare) + "".join("%s=%s;" % kv for kv in d.items())
            st += "html=1;fillColor=%s;strokeColor=%s;" % (fill, stroke)
        if dashed:
            st += "dashed=1;"
        return st

    def legend(self, c):
        # without w or span the width is 220, which needs nothing from the grid (a legend placed by col
        # needs only pitch)
        x, y, w, _ = self.geom(c if ("w" in c or "span" in c) else dict(c, w=220), default_w=220)
        font = c.get("font", 9)
        tfont = max(font, 10)
        items = self.legend_items(c.get("labels"))
        # a label for a role, shape or accent the sheet does not show gives no row: say so
        used = set()
        for _, _, _, _, _, (role, shape) in self.looks:
            if role is not None and (self.theme == "colorful" or shape is None or role in shape_roles(shape)):
                used.add(role)
            if shape is not None:
                used.add(shape)
        if any(s[0] == "accent" for s, _ in items):
            used.add("accent")
        labels = c.get("labels") if isinstance(c.get("labels"), dict) else {}
        for k in labels:
            if (k in ROLES or k in SHAPES or k in ("accent", "frame")) and k not in used:
                warn("%s: labels key %r names %s this sheet does not show, so its row does not appear" % (
                    self.where(c), k, "the accent, which no box or edge sets" if k == "accent" else
                    ("a frame, which the legend lists only in minimal and bw" if k == "frame" else "a role or shape")))
        sw, sh = 44, 20
        lab_x = 10 + sw + 8              # label starts here, relative to the legend's left edge
        lab_w = w - lab_x - 8
        if lab_w < max(40, 4 * font):
            raise SystemExit("%s: legend width %d leaves %d px for labels; make it at least %d wide" % (
                self.where(c), w, lab_w, lab_x + 8 + max(40, 4 * font)))
        # the bold title wraps at the frame's inner width (spacingLeft 8 and draw.io's 2 px spacing a
        # side), explicit line breaks included; the first row starts below all of its lines
        title = c.get("title", "Legend")
        tlines = wrapped_lines(title, max(1, w - 8 - 4), tfont, bold_all=True) if title else 1
        top = int(math.ceil(tlines * tfont * 1.35 + 12))
        rows = []
        for sample, meaning in items:
            lines = wrapped_lines(meaning, lab_w - 4, font)
            rows.append(max(sh + 6, int(math.ceil(lines * 1.35 * font + 8))))
        h = top + sum(rows) + 6
        if self.theme == "colorful":
            ffill, fstroke = ROLES["note"][0], ROLES["note"][1]
        else:
            ffill, fstroke = self.tint, INK
        fstyle = "rounded=0;whiteSpace=wrap;html=1;fillColor=%s;strokeColor=%s;fontColor=%s;fontSize=%d;fontStyle=1;align=left;verticalAlign=top;spacingLeft=8;spacingTop=2;%s%s" % (
            ffill, fstroke, INK, tfont, CONTAINER_KEY if self.shaped else "", LEGEND_KEY)
        frame_k = len(self.recs)
        self.add_rec(self.nid(c.get("id")), c.get("title", "Legend"), fstyle, x, y, w, h, "legend", counted=False)
        yy = y + top
        for (sample, meaning), rh in zip(items, rows):
            sx, swid = x + 10, sw
            spacing_left = 8
            if sample[0] == "shape" and sample[1] == "actor":
                sx, swid = x + 10 + sw // 2 - 7, 14
                spacing_left = 8 + (sw - swid) - (sw // 2 - 7)
            st = self.sample_style(sample, font)
            st += ("whiteSpace=wrap;fontSize=%d;fontColor=%s;labelPosition=right;verticalLabelPosition=middle;"
                   "align=left;verticalAlign=middle;labelWidth=%d;spacingLeft=%d;%s") % (font, INK, lab_w, spacing_left, LEGEND_KEY)
            self.add_rec(self.nid(), meaning, st, sx, yy + (rh - sh) // 2, swid, sh, "legend_row", counted=False)
            if self.shaped:
                self.recs[-1]["parent"] = frame_k   # minimal, bw: the legend moves as one unit in draw.io
            yy += rh
        self.legend_boxes.append((x, y, w, h))
        print("legend: sheet %r at x=%d y=%d w=%d h=%d (%d rows)" % (self.s["name"], x, y, w, h, len(items)))

    def edge(self, c):
        src = "%s_%s" % (self.sid, c["from"])
        dst = "%s_%s" % (self.sid, c["to"])
        for ref in (src, dst):
            if ref not in self.ids:
                raise SystemExit("edge in sheet %r refers to unknown cell %r" % (self.s["name"], ref.split("_", 1)[1]))
        role = c.get("role")
        width = c.get("width", 1)
        if self.theme == "colorful":
            color = c.get("color") or (ROLES[role][1] if role in ROLES else "#555555")
        else:
            color = self.colour_override(c, "color", INK)
        user_style = self.check_style(c)
        if c.get("accent"):
            # the accent wins over the edge's own colour, width and style, as the legend's sample shows it
            color, width = self.accent_stroke()
            user_style = drop_keys(user_style, ("strokeColor", "strokeWidth"))
        style = "edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;fontSize=%d;strokeColor=%s;strokeWidth=%d;endArrow=block;endFill=1;" % (
            c.get("font", 8), color, width)
        if not c.get("accent"):
            self.check_plain_width(c, style + user_style)
        if c.get("dashed"):
            style += "dashed=1;"
        if "exit" in c:
            style += "exitX=%s;exitY=%s;" % tuple(c["exit"])
        if "entry" in c:
            style += "entryX=%s;entryY=%s;" % tuple(c["entry"])
        if self.shaped:
            # an edge label sits on its label background when the style sets one: its text contrasts with it
            ud = parse_style(user_style)[0]
            behind = norm_paint(ud.get("labelBackgroundColor", "none"))
            if behind != "none":
                if "fontColor" in ud:
                    if norm_paint(ud["fontColor"]) == behind:
                        warn("%s: fontColor=%s is its label background; its label cannot be read" % (
                            self.where(c), ud["fontColor"]))
                elif text_on(behind) == PAPER:
                    style += "fontColor=%s;" % PAPER
        style += user_style
        i = self.nid(c.get("id"))
        # the edge is written once nesting is known (edge_parents): in minimal and bw it moves into the
        # smallest container holding both ends, its waypoints converted to that container's coordinates
        self.edges.append({"id": i, "src": src, "dst": dst, "style": style,
                           "value": (old_label if self.theme == "colorful" else label)(c.get("label", "")),
                           "points": [(int(px), int(py)) for px, py in c.get("points") or []],
                           "offset": tuple(int(v) for v in c["label_offset"]) if c.get("label_offset") else None,
                           # label_at: -1 = at the source, 0 = middle (default), 1 = at the target
                           "geom_x": ' x="%s"' % c["label_at"] if "label_at" in c else "",
                           "parent": None, "cell": c,
                           # the stroke is the compiler's default: no color field, no accent, no raw strokeColor
                           "default_stroke": not c.get("accent") and "color" not in c
                           and "strokeColor" not in parse_style(user_style)[0]})

    def edge_contrast(self):
        """minimal, bw: an edge drawn inside a lane or frame (its parent, edge_parents) runs on that
        container's body. A default black edge on a black body is drawn in the contrasting palette colour
        (white); an edge whose colour is its own (a color field, a raw strokeColor, the accent) and does not
        contrast with the body warns with the repair."""
        for e in self.edges:
            if e["parent"] is None:
                continue        # on the page, which is white
            p = self.recs[e["parent"]]
            pd = parse_style(p["style"])[0]
            body = norm_paint(pd.get("swimlaneFillColor" if p["kind"] == "lane" else "fillColor", "none"))
            if body == "none":
                continue
            stroke = norm_paint(parse_style(e["style"])[0].get("strokeColor", INK))
            if luminance(stroke) is None or luminance(body) is None or contrast(stroke, body) >= ACCENT_MIN_CONTRAST:
                continue
            if e["default_stroke"]:
                e["style"] = e["style"].replace("strokeColor=%s;" % INK, "strokeColor=%s;" % text_on(body), 1)
                continue
            warn("%s: its line (%s) runs on the body of %s %s, which is %s, and cannot be seen; %s" % (
                self.where(e["cell"]), stroke, p["kind"], p["id"].split("_", 1)[1], body,
                "drop the accent or the container's fill" if e["cell"].get("accent") else
                "use the colour %s for it, or drop the container's fill" % text_on(body)))

    def edge_parents(self):
        """minimal, bw: each edge's parent is the container draw.io itself gives an edge (its nearest
        common ancestor rule): the smallest lane or frame that holds both ends, where a container
        at one end holds itself; an edge from a cell to itself takes that cell's parent. An edge whose
        ends share only the page stays on the page."""
        index = {r["id"]: k for k, r in enumerate(self.recs)}

        def chain(k):
            out = []
            while k is not None:
                out.append(k)
                k = self.recs[k]["parent"]
            return out
        for e in self.edges:
            a, b = index.get(e["src"]), index.get(e["dst"])
            if a is None or b is None:
                continue
            if a == b:
                e["parent"] = self.recs[a]["parent"]
                continue
            ca, cb = chain(a), chain(b)
            if len(ca) > len(cb):
                ca, cb = cb, ca
            # the nearest cell on the shorter chain (itself included) that is a strict ancestor of the other end
            e["parent"] = next((k for k in ca if k in cb[1:]), None)

    def edge_xml(self, e):
        p = e["parent"]
        if p is None:
            pid, ox, oy = "1", 0, 0
        else:
            pr = self.recs[p]
            pid, ox, oy = pr["id"], pr["x"], pr["y"]
        inner = ""
        if e["points"]:
            inner += '<Array as="points">%s</Array>' % "".join('<mxPoint x="%d" y="%d"/>' % (px - ox, py - oy)
                                                              for px, py in e["points"])
        if e["offset"]:
            # a label offset is a displacement from the label's place on the edge, not a position: it
            # moves with the edge and needs no conversion
            inner += '<mxPoint x="%d" y="%d" as="offset"/>' % e["offset"]
        return '<mxCell id="%s" value="%s" style="%s" edge="1" parent="%s" source="%s" target="%s"><mxGeometry%s relative="1" as="geometry">%s</mxGeometry></mxCell>' % (
            self.idattr(e["id"]), e["value"], attr(e["style"]), self.idattr(pid), self.idattr(e["src"]),
            self.idattr(e["dst"]), e["geom_x"], inner)

    # ---- containment (minimal, bw): frames and lanes are draw.io containers
    @staticmethod
    def drawn(r):
        """The rectangle a cell covers as drawn, as a dict with x, y, w, h: its geometry, or for a cell
        whose style sets a rotation the bounding rectangle of the turned shape (rotated_bounds). The
        containment, title-strip and overlap checks use it; the geometry written to the file is unchanged."""
        x, y, w, h = rotated_bounds(r["x"], r["y"], r["w"], r["h"], r["style"])
        return {"x": x, "y": y, "w": w, "h": h}

    def nest(self):
        conts = [k for k, r in enumerate(self.recs) if r["kind"] in ("frame", "lane")]

        def inside(a, b):
            return (a["x"] >= b["x"] and a["y"] >= b["y"] and a["x"] + a["w"] <= b["x"] + b["w"]
                    and a["y"] + a["h"] <= b["y"] + b["h"])
        for k, r in enumerate(self.recs):
            # a legend inside a lane or frame moves with it; its rows stay children of the legend
            if r["kind"] not in ("box", "frame", "text", "lane", "legend"):
                continue
            dr = self.drawn(r)
            best = None
            for j in conts:
                if j == k:
                    continue
                p = self.drawn(self.recs[j])
                area_p, area_r = p["w"] * p["h"], dr["w"] * dr["h"]
                if not inside(dr, p) or area_p < area_r or (area_p == area_r and j > k):
                    continue
                if best is None or area_p < self.drawn(self.recs[best])["w"] * self.drawn(self.recs[best])["h"]:
                    best = j
            r["parent"] = best

    def check_overlaps(self):
        """Warn for every pair of vertices that overlap without one containing the other, and for a
        legend that covers any content vertex (containment included; a lane or frame may hold it).
        Cells are measured as drawn (a rotated cell by its turned bounds)."""
        boxes = [(r["id"].split("_", 1)[1], dict(r, **self.drawn(r))) for r in self.recs
                 if r["kind"] in ("box", "frame", "text", "lane", "legend")]
        for a in range(len(boxes)):
            na, ra = boxes[a]
            for b in range(a + 1, len(boxes)):
                nb, rb = boxes[b]
                ix = min(ra["x"] + ra["w"], rb["x"] + rb["w"]) - max(ra["x"], rb["x"])
                iy = min(ra["y"] + ra["h"], rb["y"] + rb["h"]) - max(ra["y"], rb["y"])
                if ix < OVERLAP_MIN or iy < OVERLAP_MIN:
                    continue
                if ix * iy in (ra["w"] * ra["h"], rb["w"] * rb["h"]):
                    # one contains the other: fine, unless a legend is involved and the other is
                    # not a lane or frame holding it
                    if (ra["kind"] == "legend") == (rb["kind"] == "legend"):
                        continue
                    lg, other, no = (ra, rb, nb) if ra["kind"] == "legend" else (rb, ra, na)
                    if other["kind"] in ("lane", "frame") and ix * iy == lg["w"] * lg["h"]:
                        continue
                    warn("sheet %r: the legend %s covers %s; move the legend" % (
                        self.s["name"], lg["id"].split("_", 1)[1], no))
                    continue
                warn("sheet %r: %s and %s overlap (%dx%d px)" % (self.s["name"], na, nb, ix, iy))

    def check_frames(self):
        """minimal, bw: a frame's children start below its headline and keep 10 px from its other
        sides; a lane's children keep 10 px from every side but its title strip; an auto-height box
        that grows past the bottom of its frame or lane is reported."""
        nm = self.s["name"]
        # every cell measured as drawn (a rotated cell by its turned bounds)
        drawn = [dict(r, **self.drawn(r)) for r in self.recs]
        for k, f in enumerate(drawn):
            if f["kind"] != "frame":
                continue
            fid = f["id"].split("_", 1)[1]
            top = f["y"] + max(f["head"], FRAME_PAD)
            for r in drawn:
                rid = r["id"].split("_", 1)[1]
                if r["parent"] == k:
                    if r["y"] < top:
                        warn("sheet %r: %s starts above the headline of frame %s (y=%d, minimum y=%d)" % (nm, rid, fid, r["y"], top))
                    for side, gap in (("left", r["x"] - f["x"]), ("right", f["x"] + f["w"] - r["x"] - r["w"]),
                                      ("bottom", f["y"] + f["h"] - r["y"] - r["h"])):
                        if gap < FRAME_PAD:
                            warn("sheet %r: %s is %d px from the %s side of frame %s (minimum %d)" % (nm, rid, gap, side, fid, FRAME_PAD))
                elif r["auto"] and f["x"] <= r["x"] and r["x"] + r["w"] <= f["x"] + f["w"] and \
                        f["y"] <= r["y"] < f["y"] + f["h"] < r["y"] + r["h"]:
                    warn("sheet %r: auto-height box %s grows past the bottom of frame %s (box bottom y=%d, frame bottom y=%d); "
                         "enlarge the frame" % (nm, rid, fid, r["y"] + r["h"], f["y"] + f["h"]))
        # lanes: the same padding on the sides without a title strip (check_lanes covers the strip side)
        for k, ln in enumerate(drawn):
            if ln["kind"] != "lane":
                continue
            lid = ln["id"].split("_", 1)[1]
            strip_side = ln["strip"][0] if ln["strip"] and ln["strip"][1] else None
            for r in drawn:
                rid = r["id"].split("_", 1)[1]
                if r["parent"] == k:
                    for side, gap in (("top", r["y"] - ln["y"]), ("left", r["x"] - ln["x"]),
                                      ("right", ln["x"] + ln["w"] - r["x"] - r["w"]),
                                      ("bottom", ln["y"] + ln["h"] - r["y"] - r["h"])):
                        if side != strip_side and gap < FRAME_PAD:
                            warn("sheet %r: %s is %d px from the %s side of lane %s (minimum %d)" % (nm, rid, gap, side, lid, FRAME_PAD))
                elif r["auto"] and ln["x"] <= r["x"] and r["x"] + r["w"] <= ln["x"] + ln["w"] and \
                        ln["y"] <= r["y"] < ln["y"] + ln["h"] < r["y"] + r["h"]:
                    warn("sheet %r: auto-height box %s grows past the bottom of lane %s (box bottom y=%d, lane bottom y=%d); "
                         "enlarge the lane" % (nm, rid, lid, r["y"] + r["h"], ln["y"] + ln["h"]))

    def check_lanes(self):
        """minimal, bw: a lane's title strip is not room for its contents. A child of a row lane starts
        at or right of the strip's right edge; a child of a column lane at or below its bottom edge."""
        nm = self.s["name"]
        for k, ln in enumerate(self.recs):
            if ln["kind"] != "lane" or not ln["strip"] or not ln["strip"][1]:
                continue
            side, size = ln["strip"]
            for r in self.recs:
                if r["parent"] != k:
                    continue
                r = dict(r, **self.drawn(r))     # measured as drawn (a rotated cell by its turned bounds)
                rid = r["id"].split("_", 1)[1]
                if side == "left" and r["x"] < ln["x"] + size:
                    warn("sheet %r: %s overlaps the title strip of lane %s (x=%d; contents start at x>=%d)" % (
                        nm, rid, ln["id"].split("_", 1)[1], r["x"], ln["x"] + size))
                elif side == "top" and r["y"] < ln["y"] + size:
                    warn("sheet %r: %s overlaps the title strip of lane %s (y=%d; contents start at y>=%d)" % (
                        nm, rid, ln["id"].split("_", 1)[1], r["y"], ln["y"] + size))

    def surface_at(self, area, skip=None):
        """The paint behind an area: the fill of the nearest painted surface that holds it wholly (a
        lane's title strip or body, a filled frame, a filled box, a legend), else the page (white).
        Nearest is smallest; an unpainted surface (fill none) is looked through."""
        def holds(rect):
            return (rect[0] <= area[0] and rect[1] <= area[1] and area[0] + area[2] <= rect[0] + rect[2]
                    and area[1] + area[3] <= rect[1] + rect[3])
        best = None
        for s in self.recs:
            if s is skip or s["kind"] not in ("box", "frame", "lane", "legend"):
                continue
            d = parse_style(s["style"])[0]
            whole = (s["x"], s["y"], s["w"], s["h"])
            if s["kind"] == "lane":
                body = norm_paint(d.get("swimlaneFillColor", "none"))
                regions = [(whole, body)]
                side, size = s["strip"] or ("top", 0)
                if size:
                    strip = norm_paint(d.get("fillColor", "none"))
                    if side == "top":
                        regions += [((s["x"], s["y"], s["w"], size), strip),
                                    ((s["x"], s["y"] + size, s["w"], s["h"] - size), body)]
                    else:
                        regions += [((s["x"], s["y"], size, s["h"]), strip),
                                    ((s["x"] + size, s["y"], s["w"] - size, s["h"]), body)]
            else:
                regions = [(whole, norm_paint(d.get("fillColor", "none")))]
            for rect, paint in regions:
                if paint != "none" and holds(rect) and (best is None or rect[2] * rect[3] < best[0]):
                    best = (rect[2] * rect[3], paint)
        return best[1] if best else PAPER

    def contrast_on_black(self):
        """minimal, bw: text that has no fill of its own (a text cell, and an actor's caption, which sits
        below the figure) takes its colour from the surface behind it (surface_at): white where white
        contrasts more, black elsewhere. An actor whose style sets a label background takes it from that.
        A fontColor in the actor's own style is left as it is."""
        for r in self.recs:
            if r["kind"] == "text":
                area = (r["x"], r["y"], r["w"], r["h"])
            elif r["kind"] == "box" and r.get("shape") == "actor" and not r.get("own_ink") and r.get("caption"):
                area = r["caption"]
            else:
                continue
            behind = None
            if r["kind"] == "box":       # the caption's own label background, when the style sets one
                lbg = norm_paint(parse_style(r["style"])[0].get("labelBackgroundColor", "none"))
                behind = None if lbg == "none" else lbg
            if text_on(behind or self.surface_at(area, skip=r)) != PAPER:
                continue
            if r["kind"] == "text":
                r["style"] += "fontColor=%s;" % PAPER
            else:
                r["style"] = r["style"].replace("fontColor=%s;" % INK, "fontColor=%s;" % PAPER, 1)

    def ink_behind_unfilled(self):
        """A box drawn without fill and without a label background (fillColor none) shows what lies
        behind it: its text takes the colour that contrasts with the nearest painted surface holding it
        (surface_at, after nesting), or, when its style sets fontColor, warns if that is the same colour."""
        for r in self.recs:
            if not r.get("defer_ink"):
                continue
            behind = self.surface_at((r["x"], r["y"], r["w"], r["h"]), skip=r)
            own = parse_style(r["style"])[0].get("fontColor") if r.get("own_ink") else None
            if own is not None:
                if norm_paint(own) == behind:
                    warn("sheet %r box %r: fontColor=%s is the colour of the surface behind it (the box has no fill); its "
                         "text cannot be read" % (self.s["name"], r["id"].split("_", 1)[1], own))
            elif text_on(behind) == PAPER:
                r["style"] = r["style"].replace("fontColor=%s;" % INK, "fontColor=%s;" % PAPER, 1)

    def check_page(self):
        """Warn for every box, frame, text cell, lane or legend that reaches outside the page; a cell
        whose style sets a rotation is measured by the bounding rectangle of its turned shape."""
        pw, ph = self.s.get("width", 1200), self.s.get("height", 800)
        for r in self.recs:
            if r["kind"] not in ("box", "frame", "text", "lane", "legend") or r["h"] == "auto":
                continue
            bx, by, bw, bh = rotated_bounds(r["x"], r["y"], r["w"], r["h"], r["style"])
            if bx < 0 or by < 0 or bx + bw > pw + 1 or by + bh > ph + 1:
                warn("sheet %r: %s (x %g to %g, y %g to %g%s) is outside the page %sx%s; move it or enlarge the page" % (
                    self.s["name"], r["id"].split("_", 1)[1], bx, bx + bw, by, by + bh,
                    ", rotated" if (bw, bh) != (r["w"], r["h"]) else "", pw, ph))

    def check_captions(self):
        """An actor's caption sits below the figure: warn when it leaves the page, reaches past the
        bottom of the lane or frame that holds the actor, or overlaps a neighbouring cell."""
        nm = self.s["name"]
        pw, ph = self.s.get("width", 1200), self.s.get("height", 800)
        for r in self.recs:
            cap = r.get("caption")
            if not cap:
                continue
            rid = r["id"].split("_", 1)[1]
            cx, cy, cw, ch = cap
            if cx < 0 or cy < 0 or cx + cw > pw or cy + ch > ph:
                warn("sheet %r: the caption of actor %s (x %d to %d, y %d to %d) is outside the page %dx%d; move the actor "
                     "or enlarge the page" % (nm, rid, cx, cx + cw, cy, cy + ch, pw, ph))
            holder = None
            for s in self.recs:
                if s is not r and s["kind"] in ("lane", "frame") and s["x"] <= r["x"] and s["y"] <= r["y"] \
                        and r["x"] + r["w"] <= s["x"] + s["w"] and r["y"] + r["h"] <= s["y"] + s["h"]:
                    if holder is None or s["w"] * s["h"] < holder["w"] * holder["h"]:
                        holder = s
            if holder is not None and cy + ch > holder["y"] + holder["h"]:
                warn("sheet %r: the caption of actor %s reaches past the bottom of %s %s (caption bottom y=%d, %s bottom "
                     "y=%d); enlarge the %s or move the actor up" % (
                         nm, rid, holder["kind"], holder["id"].split("_", 1)[1], cy + ch, holder["kind"],
                         holder["y"] + holder["h"], holder["kind"]))
            for s in self.recs:
                if s is r or s["kind"] not in ("box", "frame", "text", "lane", "legend"):
                    continue
                ix = min(cx + cw, s["x"] + s["w"]) - max(cx, s["x"])
                iy = min(cy + ch, s["y"] + s["h"]) - max(cy, s["y"])
                if ix < OVERLAP_MIN or iy < OVERLAP_MIN:
                    continue
                if s["kind"] in ("lane", "frame", "legend") and ix * iy == cw * ch:
                    continue    # the caption lies inside a container
                warn("sheet %r: the caption of actor %s overlaps %s (%dx%d px); move one of them" % (
                    nm, rid, s["id"].split("_", 1)[1], ix, iy))

    def check_lookalikes(self):
        """minimal, bw: two boxes of different roles drawn with one shape and paint (a human decision
        and a decide decision) cannot be told apart from the legend."""
        first = {}     # look -> {role: first box id}
        drawn = {}     # look -> the catalogue name of its first box, for the message
        for c, look, sample, _, _, who in self.looks:
            first.setdefault(look, {}).setdefault(who[0], c.get("id", (c.get("text") or "")[:30]))
            drawn.setdefault(look, sample[1])
        for look, roles in first.items():
            items = list(roles.items())
            for k in range(1, len(items)):
                (r1, a), (r2, b) = items[0], items[k]
                warn("sheet %r: boxes %r (%s) and %r (%s) are drawn alike (%s); the legend cannot tell them apart. "
                     "Give one a different outline in its style (strokeWidth=2%s), or use the roles' own "
                     "shapes; naming who decides in each headline helps the reader but does not clear this warning" % (
                         self.s["name"], a, "a frame" if r1 == "frame" else "role %s" % r1, b,
                         "a frame" if r2 == "frame" else "role %s" % r2, drawn[look],
                         ", dashed=1" if self.theme == "minimal" else ""))

    def emit(self):
        out, done, held = [], set(), {}

        def put(k):
            r = self.recs[k]
            p = r["parent"]
            if p is None:
                pid, x, y = "1", r["x"], r["y"]
            else:
                pr = self.recs[p]
                pid, x, y = pr["id"], r["x"] - pr["x"], r["y"] - pr["y"]
            value = old_label(r["text"]) if r["raw"] else label(r["text"], r["headline"])
            geometry = '<mxGeometry x="%d" y="%d" width="%d" height="%d" as="geometry"/>' % (x, y, r["w"], r["h"])
            if r["link"] is not None:
                # draw.io's page link: the id and label move to a UserObject around the cell
                # the diagram id is written literally, so the link names it literally too
                out.append('<UserObject label="%s" link="%s" id="%s"><mxCell style="%s" vertex="1" parent="%s">%s</mxCell></UserObject>' % (
                    value, lit_attr("data:page/id," + r["link"]), self.idattr(r["id"]), attr(r["style"]),
                    self.idattr(pid), geometry))
            else:
                out.append('<mxCell id="%s" value="%s" style="%s" vertex="1" parent="%s">%s</mxCell>' % (
                    self.idattr(r["id"]), value, attr(r["style"]), self.idattr(pid), geometry))
            done.add(k)
            for ch in held.pop(k, []):
                put(ch)
        for k, r in enumerate(self.recs):
            p = r["parent"]
            if p is not None and p not in done:
                held.setdefault(p, []).append(k)
            else:
                put(k)
        return "".join(out)

    def check_accent_flags(self):
        """accent on a box or an edge is true or false (a JSON boolean): the string "false" would
        otherwise turn it on. The key did not exist before themes, so no older spec holds it."""
        for c in self.s["cells"]:
            if c.get("type", "box") in ("lane", "text", "legend"):
                continue       # the key is unknown there: check_keys warns and it is ignored
            if "accent" in c and not isinstance(c["accent"], bool):
                raise SystemExit("%s: accent %s must be true or false (a JSON boolean, not a string or number)" % (
                    self.where(c), json.dumps(c["accent"])))

    def compile(self):
        self.check_accent_flags()
        # three passes: shapes, then legends (they list what the sheet uses), then edges
        for c in self.s["cells"]:
            if c.get("type", "box") not in ("edge", "legend"):
                self.vertex(c)
        for c in self.s["cells"]:
            if c.get("type") == "legend":
                self.legend(c)
        for c in self.s["cells"]:
            if c.get("type") == "edge":
                self.edge(c)
        if self.shaped:
            self.nest()
            self.edge_parents()
            self.edge_contrast()
            self.check_frames()
            self.check_lanes()
            self.contrast_on_black()
            self.check_lookalikes()
            has_legend = any(c.get("type") == "legend" for c in self.s["cells"])
            if not has_legend and re.match(r"L[1-4]", str(self.s["name"])):
                # roles are told apart by shape only: the legend is what makes the shapes readable
                warn("sheet %r has no legend cell; theme %s tells roles apart by shape only, so every sheet from L1 "
                     "to L4 needs one. Add {\"type\": \"legend\", ...}" % (self.s["name"], self.theme))
            if not has_legend:
                for c in self.s["cells"]:
                    if c.get("type", "box") not in ("box", "text"):
                        continue
                    # grey is minimal's own tint, so it names no colour key there
                    m = [w.group(1) for w in PALETTE_KEY.finditer(c.get("text", "") or "")
                         if not (self.theme == "minimal" and w.group(1).lower() in ("grey", "gray"))]
                    if m:
                        warn("%s names the colour %r but theme %s draws roles by shape and the sheet has no legend cell; "
                             "add a legend, or build with --theme colorful" % (self.where(c), m[0], self.theme))
                        break
        self.ink_behind_unfilled()
        self.check_page()
        self.check_captions()
        self.check_overlaps()
        layer = '<mxCell id="1" parent="0"/>'
        if self.shaped or self.accent != DEFAULT_ACCENT:
            # the theme record as style keys, which survive a save in draw.io (it may drop <mxfile>
            # attributes): on the sheet's layer cell, which no content edit deletes, and, for readers of
            # older files, on the first vertex too. colorful with the default accent writes none, as
            # before themes. A raw style may end without ';', so a separator goes first.
            record = style_record(self.theme, self.accent, self.max_colours)
            layer = '<mxCell id="1" style="%s" parent="0"/>' % attr(record)
            if self.recs:
                st = self.recs[0]["style"]
                if st and not st.endswith(";"):
                    st += ";"
                self.recs[0]["style"] = st + record
        w, h = self.s.get("width", 1200), self.s.get("height", 800)
        return ('<diagram id="%s" name="%s"><mxGraphModel dx="1400" dy="800" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="%d" pageHeight="%d" math="0" shadow="0">'
                '<root><mxCell id="0"/>%s%s</root></mxGraphModel></diagram>' % (
                    escape(self.sid, ESC), escape(self.s["name"], ESC), w, h, layer,
                    self.emit() + "".join(self.edge_xml(e) for e in self.edges)))


def check_sheet_list(spec):
    """A spec holds one or more sheets: no "sheets" list, or an empty one, stops build and add before
    anything is backed up or written."""
    sheets = spec.get("sheets") if isinstance(spec, dict) else None
    if not isinstance(sheets, list) or not sheets:
        raise SystemExit("the spec has no sheets (%s); give \"sheets\" one or more sheets" % (
            "an empty list" if sheets == [] else "no \"sheets\" list"))


def compile_spec(spec, theme=None, accent=None, max_colours=None, compilers=None, pages=None):
    """theme: CLI override; else spec['theme']; else minimal. pages: {sheet name: diagram id} of the
    sheets already in the file (add), which a box's link may name. Returns [(name, xml, vertices)]."""
    check_sheet_list(spec)
    check_keys(spec)
    # an accent passed in was resolved (and its warnings printed) by the caller: build_document, add
    theme, spec_accent = resolve_theme(spec, theme, max_colours, quiet=accent is not None)
    accent = accent or spec_accent
    if max_colours is None:
        max_colours = resolve_max_colours(spec, None, theme) if theme == "minimal" else DEFAULT_MAX_COLOURS
    out = []
    seen, names = set(), set()
    # two sheets with one name: a link or a position by name could not tell them apart. colorful builds
    # them when no cell links, as the script before themes did
    linked = any("link" in c for sh in spec["sheets"] for c in (sh.get("cells") or []) if isinstance(c, dict))
    for sh in spec["sheets"]:
        if sh["id"] in seen:
            raise SystemExit("duplicate sheet id %r" % sh["id"])
        if sh["name"] in names and (theme != "colorful" or linked):
            raise SystemExit("duplicate sheet name %r in the spec; every sheet needs its own name" % sh["name"])
        seen.add(sh["id"])
        names.add(sh["name"])
    # a link names a sheet; it opens the diagram id the file holds for that sheet
    all_pages = dict(pages or {})
    all_pages.update((sh["name"], sh["id"]) for sh in spec["sheets"])
    for sh in spec["sheets"]:
        c = Compiler(sh, theme, accent, max_colours, all_pages)
        out.append((sh["name"], c.compile(), c.vertices))
        if compilers is not None:
            compilers.append(c)
    return out


def theme_record(theme, accent, max_colours):
    """Attributes on <mxfile> that let 'add' keep the file's theme. A file with none is colorful
    (every file built before themes existed), so colorful with the default accent writes none."""
    if theme == "colorful":
        return "" if accent == DEFAULT_ACCENT else ' dsTheme="colorful" dsAccent="%s"' % accent
    if theme == "minimal":
        return ' dsTheme="minimal" dsAccent="%s" dsMaxColours="%d"' % (accent, max_colours)
    return ' dsTheme="bw" dsAccent="%s"' % accent


def style_record(theme, accent, max_colours):
    """The same record as style keys (minimal, bw, and colorful with a custom accent), written on the
    first vertex of each sheet: draw.io keeps unknown style keys when it saves."""
    if theme == "minimal":
        return "dsTheme=minimal;dsAccent=%s;dsMaxColours=%d;" % (accent, max_colours)
    return "dsTheme=%s;dsAccent=%s;" % (theme, accent)


def wrap_file(diagrams_xml, record=""):
    return '<mxfile host="app.diagrams.net" agent="drawio_spec.py" version="24.0"%s>%s</mxfile>' % (record, "".join(diagrams_xml))


def record_of(style):
    """The theme record keys a style holds, read by parsing it (a missing last ';' or a following key
    makes no difference)."""
    kv = parse_style(style)[0]
    return {k: kv[k] for k in RECORD_KEYS if k in kv}


def _sheet_record_parts(model_xml):
    """(layer record, first-cell record) of one graph model, each read key by key: the layer cell (a
    cell whose parent is the root cell "0") that holds the key, else the first other cell that does."""
    layer, cell = {}, {}
    for cv in model_cells(ET.fromstring(model_xml)):
        rec = record_of(cv.get("style"))
        if not rec:
            continue
        is_layer = cv.get("parent") == "0" and cv.get("vertex") != "1" and cv.get("edge") != "1"
        target = layer if is_layer else cell
        for k, v in rec.items():
            target.setdefault(k, v)
    return layer, cell


def sheet_records(root):
    """[(sheet name, record, where)] for every sheet that carries theme record keys as style keys.
    Each setting is read on its own: from the sheet's layer cell, else (files built before the layer
    record, or a layer that lost the key) from the first cell that holds it. where is "layer" when
    the layer holds every key of the record, else "cell"."""
    out = []
    for d in root.findall("diagram"):
        model = decode_diagram(d)
        if not model:
            continue
        layer, cell = _sheet_record_parts(model)
        rec = dict(cell)
        rec.update(layer)
        if rec:
            out.append((d.get("name", ""), rec, "layer" if set(layer) == set(rec) else "cell"))
    return out


def sheet_record(root):
    """The theme record kept as style keys, setting by setting: the value of the first sheet that holds
    it ({} when no sheet holds any). Disagreeing sheets are reported by record_conflicts."""
    out = {}
    for _, rec, _ in sheet_records(root):
        for k, v in rec.items():
            out.setdefault(k, v)
    return out


def _record_value(k, v):
    v = str(v).strip()
    return v.upper() if k == "dsAccent" else v


def record_conflicts(root):
    """Every disagreement between the theme records of a file, setting by setting: within a sheet
    (its layer against its first-cell record), each sheet against the <mxfile> record, and the sheets
    against each other. Only settings both records hold are compared; a file with no records has none."""
    file_rec = {k: root.get(k) for k in RECORD_KEYS if root.get(k) is not None}
    sheets = sheet_records(root)
    names = {"dsTheme": "theme", "dsAccent": "accent", "dsMaxColours": "max_colours"}

    def diff(a, b):
        return ["%s %s vs %s" % (names[k], a[k], b[k]) for k in RECORD_KEYS
                if k in a and k in b and _record_value(k, a[k]) != _record_value(k, b[k])]
    out = []
    for d in root.findall("diagram"):
        model = decode_diagram(d)
        if model:
            layer, cell = _sheet_record_parts(model)
            dd = diff(layer, cell)
            if dd:
                out.append("sheet %r: its layer and its first cell record different theme settings (%s)" % (
                    d.get("name", ""), "; ".join(dd)))
    for name, rec, _ in sheets:
        d = diff(rec, file_rec)
        if d:
            out.append("sheet %r records %s; the file (<mxfile>) records otherwise (%s)" % (
                name, ", ".join("%s %s" % (names[k], rec[k]) for k in RECORD_KEYS if k in rec), "; ".join(d)))
    for i in range(len(sheets)):
        for j in range(i + 1, len(sheets)):
            d = diff(sheets[i][1], sheets[j][1])
            if d:
                out.append("sheets %r and %r record different theme settings (%s)" % (sheets[i][0], sheets[j][0], "; ".join(d)))
    return out


def file_theme(root):
    """(theme, accent, max_colours, recorded). Each setting is resolved on its own: the <mxfile>
    attribute when present, else the value the sheets' records hold (layer cell, then first-cell record
    of older files), else the default; a file whose theme no record holds is colorful. Records that
    disagree are reported by record_conflicts (validate) and stop add. A value that is present goes
    through the same checks as the CLI and the spec."""
    sheets = sheet_record(root)
    rec, source = {}, {}
    for k in RECORD_KEYS:
        if root.get(k) is not None:
            rec[k], source[k] = root.get(k), "the file's theme record"
        elif k in sheets:
            rec[k], source[k] = sheets[k], "the file's theme record (cell style)"
    if "dsTheme" not in rec:
        theme, recorded = "colorful", False
    else:
        theme, recorded = check_theme(rec["dsTheme"], source["dsTheme"]), True
    accent = DEFAULT_ACCENT if "dsAccent" not in rec else check_accent(rec["dsAccent"], source["dsAccent"], recorded=True)
    mc = None if "dsMaxColours" not in rec else check_max_colours(rec["dsMaxColours"], source["dsMaxColours"])
    return theme, accent, mc, recorded


# --------------------------------------------------------------------------- colour audit
NAMED_NONE = ("none", "", "default", "inherit")
NAMED_COLOURS = {"black": INK, "white": PAPER}


def _norm_colour(v):
    v = v.strip()
    if v.lower() in NAMED_COLOURS:
        return NAMED_COLOURS[v.lower()]
    m = re.match(r"^#([0-9A-Fa-f]{3})$", v)
    if m:
        v = "#" + "".join(ch * 2 for ch in m.group(1))
    return v.upper()


def norm_paint(v):
    """A colour for grouping: 'none' and its synonyms stay 'none'; other spellings normalised."""
    return "none" if v.strip().lower() in NAMED_NONE else _norm_colour(v)


def luminance(colour):
    """WCAG relative luminance of a colour (None when it is not #RRGGBB after normalising)."""
    m = re.match(r"^#([0-9A-Fa-f]{6})$", _norm_colour(colour) if colour and colour != "none" else "")
    if not m:
        return None

    def lin(ch):
        return ch / 12.92 if ch <= 0.03928 else ((ch + 0.055) / 1.055) ** 2.4
    r, g, b = (int(m.group(1)[k:k + 2], 16) / 255.0 for k in (0, 2, 4))
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def text_on(fill):
    """Black or white text, whichever contrasts more with the fill (WCAG relative luminance; a tie,
    'none' and a value that is not #RRGGBB give black). Both are in every palette."""
    lum = luminance(fill)
    if lum is None:
        return INK
    return PAPER if 1.05 / (lum + 0.05) > (lum + 0.05) / 0.05 else INK


def text_behind(d, fill=None):
    """(colour, what) behind a cell's text, from its parsed style d: the label background when set,
    else the cell's own fill (`fill` when the style sets none); (None, ...) when neither paints, so
    the surface behind the cell shows through."""
    lbg = norm_paint(d.get("labelBackgroundColor", "none"))
    if lbg != "none":
        return lbg, "its label background"
    own = norm_paint(d.get("fillColor", fill if fill is not None else "none"))
    if own != "none":
        return own, "the box's fill"
    return None, "the surface behind it"


def rotated_bounds(x, y, w, h, style):
    """(x, y, w, h) of the rectangle a cell covers on the page: its geometry, or, when its style sets a
    rotation, the bounding rectangle of the geometry turned about its centre (as draw.io draws it)."""
    rot = style_number(parse_style(style)[0], "rotation")
    if not rot:
        return x, y, w, h
    a = math.radians(rot)
    c, s = abs(math.cos(a)), abs(math.sin(a))
    rw, rh = w * c + h * s, w * s + h * c
    cx, cy = x + w / 2.0, y + h / 2.0
    return tuple(round(v, 6) for v in (cx - rw / 2.0, cy - rh / 2.0, rw, rh))


def actor_caption(x, y, w, h, text, style):
    """(x, y, w, h) of an actor's caption, which draw.io writes below the figure, wrapped at the
    figure's width less its spacing (a word longer than that widens the caption, centred); None
    without text."""
    if not text:
        return None
    d = parse_style(style)[0]
    font = style_number(d, "fontSize") or 9
    fs = style_number(d, "fontStyle")
    bold = fs is not None and bool(int(fs) & 1)
    s = style_number(d, "spacing")
    s = 4 if s is None else s
    words = [wd for line in text.split("\n") for wd in line.split(" ")]
    cw = max(float(w), max(text_width(wd, font, bold) for wd in words) + 2 * s)
    lines = wrapped_lines(text, max(1.0, cw - 2 * s), font, bold_all=bold)
    ch = int(math.ceil(lines * 1.35 * font + 2 * s))
    cw = int(math.ceil(cw))
    return (x + (w - cw) // 2, y + h, cw, ch)


def model_styles(model_xml):
    """Every style attribute of a decoded graph model, read from the parsed XML."""
    if not model_xml:
        return []
    return [e.get("style") for e in ET.fromstring(model_xml).iter() if e.get("style") is not None]


# Colour in a label's active HTML: a tag's color / bgcolor attribute, and color / background
# declarations in its style attribute. A decoded label value holds active tags as '<'; markup the
# compiler escaped arrives as '&lt;' and is text, not paint.
HTML_TAG = re.compile(r"<[A-Za-z][^<>]*>")
HTML_COLOUR_ATTR = re.compile(r"""\s(?:color|bgcolor)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.I)
HTML_STYLE_ATTR = re.compile(r"""\sstyle\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.I)
CSS_PAINT = ("color", "background", "background-color")
CSS_COLOUR_TOKEN = re.compile(r"#[0-9A-Fa-f]{3,8}\b|rgba?\([^)]*\)|[A-Za-z]+")
HTML_NO_PAINT = ("transparent", "currentcolor", "initial", "unset", "inherit", "none")
CSS_NAMED = frozenset("""aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue
blueviolet brown burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk crimson cyan darkblue
darkcyan darkgoldenrod darkgray darkgreen darkgrey darkkhaki darkmagenta darkolivegreen darkorange darkorchid
darkred darksalmon darkseagreen darkslateblue darkslategray darkslategrey darkturquoise darkviolet deeppink
deepskyblue dimgray dimgrey dodgerblue firebrick floralwhite forestgreen fuchsia gainsboro ghostwhite gold
goldenrod gray green greenyellow grey honeydew hotpink indianred indigo ivory khaki lavender lavenderblush
lawngreen lemonchiffon lightblue lightcoral lightcyan lightgoldenrodyellow lightgray lightgreen lightgrey
lightpink lightsalmon lightseagreen lightskyblue lightslategray lightslategrey lightsteelblue lightyellow lime
limegreen linen magenta maroon mediumaquamarine mediumblue mediumorchid mediumpurple mediumseagreen
mediumslateblue mediumspringgreen mediumturquoise mediumvioletred midnightblue mintcream mistyrose moccasin
navajowhite navy oldlace olive olivedrab orange orangered orchid palegoldenrod palegreen paleturquoise
palevioletred papayawhip peru pink plum powderblue purple rebeccapurple red rosybrown royalblue saddlebrown
salmon sandybrown seagreen seashell sienna silver skyblue slateblue slategray slategrey snow springgreen
steelblue tan teal thistle tomato turquoise violet wheat white whitesmoke yellow yellowgreen""".split())
WRAPPERS = ("object", "UserObject")   # draw.io's cell wrappers: they carry the cell's id and label


class CellView:
    """One cell of a graph model. In draw.io's object / UserObject wrapper the id and label (value)
    come from the wrapper; style, parent, source, target and geometry from the inner mxCell."""

    def __init__(self, el, wrapper=None):
        self.el, self.wrapper = el, wrapper

    def get(self, key, default=None):
        if self.wrapper is not None and key in ("id", "value"):
            return self.wrapper.get("id" if key == "id" else "label", default)
        return self.el.get(key, default)

    def find(self, path):
        return self.el.find(path)


def model_cells(el):
    """Every cell under an element, in document order, wrapped cells read through their wrapper."""
    out, inner_seen = [], set()
    for e in el.iter():
        if e.tag in WRAPPERS:
            inner = e.find("mxCell")
            if inner is None:
                inner = ET.Element("mxCell")
            inner_seen.add(id(inner))
            out.append(CellView(inner, e))
        elif e.tag == "mxCell" and id(e) not in inner_seen:
            out.append(CellView(e))
    return out


def html_mode(style):
    """draw.io reads a label as HTML only when the cell's style says html=1."""
    return parse_style(style)[0].get("html") == "1"


def plain_text(value, html=True):
    """The text a label shows: in an HTML label, <br> is a line break, other tags are dropped and
    entities decoded."""
    value = value or ""
    if not html:
        return value
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    return html_lib.unescape(re.sub(r"<[^<>]*>", "", value))


# bare style tokens that carry no paint: the ones the compiler itself writes (text cells, lanes, and
# the draw.io names behind the catalogue shapes) and draw.io's own paint-free names, which the app
# writes on ordinary hand edits (edgeLabel: a label typed onto an edge). Any other bare token is a
# named style, which brings paint of its own.
DRAWIO_PAINT_FREE_STYLES = ("edgeLabel", "group", "ellipse", "rhombus", "triangle", "line")
COMPILER_BARE_STYLES = (frozenset(("text", "swimlane")) | frozenset(CATALOGUE_DRAWIO_SHAPES)
                        | frozenset(DRAWIO_PAINT_FREE_STYLES))


def style_breaches(style, palette=None):
    """What one cell style of a minimal or bw file holds beyond what is permitted (an allow-list):
    a bare name other than the compiler's own and draw.io's paint-free names; a key that neither the
    compiler writes nor the raw-style allow-list permits (glass, sketch, image, shadow, ...), named by
    key; and a permitted key whose value breaks its rule (a gradient, opacity below 100, a shape
    outside the catalogue, a colour outside `palette`). Every declaration counts, a repeated key too."""
    found = []
    for tok in (style or "").split(";"):
        tok = tok.strip()
        if not tok:
            continue
        if "=" not in tok:
            if tok not in COMPILER_BARE_STYLES:
                found.append("named style %r (it brings its own paint)" % tok)
            continue
        k, v = (s.strip() for s in tok.split("=", 1))
        if k not in PERMITTED_STYLE_KEYS:
            found.append("%s=%s (a key the compiler does not write and the style allow-list does not permit)" % (k, v))
            continue
        why = style_value_breach(k, v, palette)
        if why:
            found.append("%s=%s (%s)" % (k, v, why))
    return found


# The HTML a minimal or bw label may hold (an allow-list): what the compiler writes (<b>, <br>) and the
# paint-free structure draw.io's own label editor writes. Attributes: `align` and a `style` whose every
# property is paint-free; `font` may also carry `face` and `size`. Anything else (another tag such as img
# or svg, a color / bgcolor attribute, a colour, background, border, shadow, filter or opacity property,
# or any property not listed) is a breach.
LABEL_TAGS = frozenset("b strong i em u br div span p font ul ol li sub sup".split())
LABEL_ATTRS = {"*": frozenset(("style", "align")), "font": frozenset(("style", "align", "face", "size"))}
LABEL_CSS = frozenset(("text-align", "font-size", "font-weight", "font-style", "font-family", "line-height",
                       "text-decoration", "text-decoration-line", "white-space"))
LABEL_CSS_PREFIXES = ("margin", "padding")
TEXT_DECORATION_WORDS = frozenset("none underline overline line-through solid double dotted dashed wavy".split())
LABEL_TAG = re.compile(r"<\s*(/?)\s*([A-Za-z][A-Za-z0-9-]*)([^<>]*)>")
LABEL_ATTR = re.compile(r"""([^\s=/"'<>]+)\s*(?:=\s*("[^"]*"|'[^']*'|[^\s"'<>]+))?""")


def css_breaches(style):
    """The declarations of an inline style that are not paint-free (named as written)."""
    bad = []
    for decl in style.split(";"):
        if not decl.strip():
            continue
        if ":" not in decl:
            bad.append(decl.strip())
            continue
        prop, val = (s.strip() for s in decl.split(":", 1))
        p = prop.lower()
        if p.startswith("text-decoration"):
            if p not in LABEL_CSS or any(w not in TEXT_DECORATION_WORDS for w in val.lower().replace(",", " ").split()):
                bad.append("%s: %s" % (prop, val))
        elif p not in LABEL_CSS and not any(p == x or p.startswith(x + "-") for x in LABEL_CSS_PREFIXES):
            bad.append("%s: %s" % (prop, val))
    return bad


def label_breaches(value):
    """What a label's active HTML holds beyond the allow-list above: a tag not listed (named with what
    it carries), an attribute that is not permitted on it, or a style property that can paint."""
    found = []
    for m in LABEL_TAG.finditer(value or ""):
        closing, name, rest = m.group(1), m.group(2).lower(), m.group(3).strip().rstrip("/").strip()
        if name not in LABEL_TAGS:
            found.append(m.group(0))
            continue
        if closing:
            continue
        allowed = LABEL_ATTRS.get(name, LABEL_ATTRS["*"])
        for a in LABEL_ATTR.finditer(rest):
            key, val = a.group(1).lower(), (a.group(2) or "").strip("\"'")
            if key not in allowed:
                found.append("%s (attribute %s on <%s>)" % (m.group(0), a.group(1), name))
            elif key == "style":
                bad = css_breaches(val)
                if bad:
                    found.append("%s (style %s)" % (m.group(0), "; ".join(bad)))
    return found


# The attributes of a page (<mxGraphModel>) a minimal or bw file may hold: the ones the compiler writes.
# The two that can paint have value rules: shadow only 0, background only absent, none or a palette
# colour. Any other attribute (backgroundImage, ...) is a breach named in the message.
PAGE_ATTRS = frozenset(("dx", "dy", "grid", "gridSize", "guides", "tooltips", "connect", "arrows", "fold", "page",
                        "pageScale", "pageWidth", "pageHeight", "math", "shadow", "background"))


def page_breaches(model, palette=None):
    """What a page's own attributes hold beyond PAGE_ATTRS and their value rules."""
    found = []
    for k, v in model.attrib.items():
        if k not in PAGE_ATTRS:
            found.append("%s=%s (a page attribute the compiler does not write)" % (k, v[:60]))
        elif k == "shadow" and v.strip() not in ("0", ""):
            found.append("shadow=%s (the page draws shadows, which paint grey)" % v)
        elif k == "background" and v.strip().lower() not in NAMED_NONE and palette is not None \
                and (html_colour(v) or v) not in palette:
            found.append("background=%s (a page colour outside the palette)" % v)
    return found


def effect_problems(root, palette=None):
    """minimal and bw files: everything a page's attributes, a cell style or a label holds beyond the
    allow-lists (see page_breaches, style_breaches and label_breaches)."""
    probs = []
    for d in root.findall("diagram"):
        model = decode_diagram(d)
        if not model:
            continue
        m = ET.fromstring(model)
        eff = page_breaches(m, palette)
        if eff:
            probs.append("sheet %r: page %s; a minimal or bw page holds only the attributes the compiler writes" % (
                d.get("name", ""), "; ".join(eff)))
        for cv in model_cells(m):
            eff = style_breaches(cv.get("style"), palette)
            if eff:
                probs.append("sheet %r cell %s: style %s; a minimal or bw cell holds only what the compiler writes "
                             "and the style allow-list permits" % (d.get("name", ""), cv.get("id"), "; ".join(eff)))
            v = cv.get("value")
            if v and "<" in v and html_mode(cv.get("style")):
                eff = label_breaches(v)
                if eff:
                    probs.append("sheet %r cell %s: label markup %s; a minimal or bw label holds only paint-free "
                                 "HTML (text tags, and style properties that set no colour)" % (
                                     d.get("name", ""), cv.get("id"), " ".join(eff)))
    return probs


def html_colour(v):
    """A colour written in HTML or CSS, normalised; None for no paint."""
    v = v.strip().strip("\"'").replace("!important", "").strip()
    low = v.lower()
    if low in NAMED_NONE or low in HTML_NO_PAINT:
        return None
    m = re.match(r"^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", low)
    if m:
        return "#%02X%02X%02X" % tuple(min(255, int(g)) for g in m.groups())
    return _norm_colour(v)


def label_colours(value):
    """The colours a label's HTML paints: a tag's color / bgcolor attribute, and the colour in a
    color, background or background-color declaration of its style attribute (quoted or not; hex,
    rgb() and CSS colour names)."""
    found = set()
    for tag in HTML_TAG.findall(value or ""):
        for m in HTML_COLOUR_ATTR.finditer(tag):
            c = html_colour(next(g for g in m.groups() if g is not None))
            if c:
                found.add(c)
        for m in HTML_STYLE_ATTR.finditer(tag):
            for decl in next(g for g in m.groups() if g is not None).split(";"):
                if ":" not in decl:
                    continue
                prop, val = decl.split(":", 1)
                if prop.strip().lower() not in CSS_PAINT:
                    continue
                val = val.replace("!important", "").strip()
                if prop.strip().lower() == "color":
                    toks = [val]           # one colour (a name, #hex or rgb())
                else:                      # shorthand: its #hex, rgb() and colour-name parts
                    toks = [t for t in CSS_COLOUR_TOKEN.findall(val) if not t.isalpha() or t.lower() in CSS_NAMED]
                for tok in toks:
                    c = html_colour(tok)
                    if c:
                        found.add(c)
    return found


def element_colours(el):
    """Every colour an element and its descendants paint: each *Color key of each style, the page
    background of each graph model, and colour in the HTML of each label whose cell has html=1
    (with html=0 draw.io shows markup as text)."""
    cols = colours_of_styles([e.get("style") for e in el.iter() if e.get("style") is not None])
    for e in el.iter():
        if e.tag == "mxGraphModel":
            bg = e.get("background")
            if bg is not None and html_colour(bg):
                cols.add(html_colour(bg))
    for cv in model_cells(el):
        v = cv.get("value")
        if v and "<" in v and html_mode(cv.get("style")):
            cols |= label_colours(v)
    return cols


def model_colours(model_xml):
    return element_colours(ET.fromstring(model_xml)) if model_xml else set()


def colours_of_styles(styles):
    """The distinct colours the styles paint: every declaration of every *Color key (a superseded
    declaration counts too, so whitespace or duplicate keys cannot hide a colour), normalised."""
    found = set()
    for st in styles:
        for tok in (st or "").split(";"):
            if "=" not in tok:
                continue
            k, v = tok.split("=", 1)
            if k.strip().endswith("Color") and v.strip().lower() not in NAMED_NONE:
                found.add(_norm_colour(v))
    return found


def emitted_colours(xml):
    """The colours of an XML text (a file, one or more <diagram> elements, or a graph model),
    parsed as XML; compressed sheets are decoded. The same path as file_colours."""
    root = ET.fromstring("<r>%s</r>" % xml)
    cols = element_colours(root)
    for d in root.iter("diagram"):
        if d.find("mxGraphModel") is None:
            cols |= model_colours(decode_diagram(d))
    return cols


def palette_problems(theme, cols, accent=DEFAULT_ACCENT, max_colours=DEFAULT_MAX_COLOURS):
    """What breaks the theme's palette (empty when nothing does)."""
    allowed = theme_palette(theme, accent, max_colours)
    if allowed is None:
        return []
    probs = []
    extra = cols - allowed
    if extra:
        what = "theme %s%s" % (theme, (" with max_colours %d" % (max_colours or DEFAULT_MAX_COLOURS)) if theme == "minimal" else "")
        probs.append("%s allows only %s; this paints also %s" % (what, " ".join(sorted(allowed)), " ".join(sorted(extra))))
    if len(cols) >= HARD_LIMIT:
        probs.append("theme %s allows fewer than %d colours per file; this paints %d: %s" % (
            theme, HARD_LIMIT, len(cols), " ".join(sorted(cols))))
    return probs


def check_palette(theme, xml, accent=DEFAULT_ACCENT, max_colours=DEFAULT_MAX_COLOURS, cols=None):
    """Stop when the palette breaks the theme."""
    cols = emitted_colours(xml) if cols is None else cols
    probs = palette_problems(theme, cols, accent, max_colours)
    if probs:
        raise SystemExit("; ".join(probs) + ". Check 'style' strings, or use --theme colorful.")
    return cols


# --------------------------------------------------------------------------- read existing
def decode_diagram(d):
    """Return the mxGraphModel XML text of a <diagram> element, decompressing if needed."""
    child = d.find("mxGraphModel")
    if child is not None:
        return ET.tostring(child, encoding="unicode")
    txt = (d.text or "").strip()
    if not txt:
        return ""
    raw = zlib.decompress(base64.b64decode(txt), -15)
    return urllib.parse.unquote(raw.decode("utf-8"))


def read_file(path):
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        raise SystemExit("refusing to parse a .drawio with a DOCTYPE/ENTITY declaration")
    root = ET.fromstring(text)
    if root.tag != "mxfile":
        raise SystemExit("not an mxfile: root tag %r" % root.tag)
    return root


def sheet_index(root):
    """One index of a file's sheets, for validate and add: names and diagram ids in file order, and
    the names and ids that occur more than once (a position or link naming one cannot tell them apart)."""
    names = [d.get("name", "") for d in root.findall("diagram")]
    ids = [d.get("id") for d in root.findall("diagram")]
    return {"names": names, "ids": ids,
            "dup_names": sorted(set(n for n in names if names.count(n) > 1)),
            "dup_ids": sorted(set(i for i in ids if i is not None and ids.count(i) > 1))}


def is_legend_cell(c):
    """A generated legend cell: its parsed style sets dsLegend to 1 (wherever the key stands, with or
    without the last ';')."""
    return parse_style(c.get("style"))[0].get("dsLegend") == "1"


def swimlane_strip(style):
    """(side, size) of a draw.io swimlane's title strip, or None when the style is not a swimlane.
    Side follows draw.io's rule: horizontal (default 1) and direction turn the strip, flips mirror it;
    size is startSize when the style sets it, else 23 for draw.io's named swimlane style (`swimlane;`),
    else 40, the bare shape's fallback (`shape=swimlane;`)."""
    d, bare = parse_style(style)
    if "swimlane" not in bare and d.get("shape") != "swimlane":
        return None
    size = style_number(d, "startSize")
    if size is None and "swimlane" in bare:
        size = NAMED_SWIMLANE_STARTSIZE
    n = 0 if d.get("horizontal", "1") not in ("0", "false") else 3
    n += {"north": -1, "south": 1, "west": 2}.get(d.get("direction", "east"), 0)
    if d.get("flipH") == "1" and n % 2 == 1:
        n += 2
    if d.get("flipV") == "1" and n % 2 == 0:
        n += 2
    return ("top", "right", "bottom", "left")[n % 4], DEFAULT_STARTSIZE if size is None else size


def strip_overlap(side, size, lane_w, lane_h, x, y, w, h):
    """Whether a child at (x, y, w, h), relative to its lane, reaches into the lane's title strip."""
    return {"top": y < size, "left": x < size, "bottom": y + h > lane_h - size, "right": x + w > lane_w - size}[side]


def file_colours(root):
    """The colours of an existing file: each sheet's graph model decoded and parsed as XML, and every
    cell's style, the page background and the labels' active HTML read (the same path build uses)."""
    models = [decode_diagram(d) for d in root.findall("diagram")]
    cols = set()
    for m in models:
        cols |= model_colours(m)
    return cols, models


def sheet_summary(root):
    rows = []
    for d in root.findall("diagram"):
        model = decode_diagram(d)
        cells = model_cells(ET.fromstring(model)) if model else []
        vs = [c for c in cells if c.get("vertex") == "1"]
        verts = len([c for c in vs if not is_legend_cell(c)])
        legend = len(vs) - verts
        edges = len([c for c in cells if c.get("edge") == "1"])
        rows.append((d.get("name", ""), d.get("id", ""), verts, edges, legend))
    return rows


def theme_line(root):
    theme, accent, mc, rec = file_theme(root)
    cols, _ = file_colours(root)
    extra = ""
    if theme == "minimal":
        extra = ", max_colours %d" % (mc or DEFAULT_MAX_COLOURS)
    if not rec:
        extra += ", no theme record (built before themes, or colorful with the default accent)"
    return "theme %s%s; %d colours: %s" % (theme, extra, len(cols), " ".join(sorted(cols))), theme, cols, mc


# --------------------------------------------------------------------------- commands
def cmd_build(a):
    spec = json.load(open(a.spec, encoding="utf-8"))
    out = a.out or spec.get("file")
    if not out:
        raise SystemExit("no output path: pass -o or set spec['file']")
    if os.path.exists(out) and not a.force:
        raise SystemExit("%s exists; to rebuild a file you built from this spec, pass --force; to add sheets to a "
                         "file you did not build, use 'add'" % out)
    start = WARNINGS[0]
    comps = []
    doc, sheets, cols, theme, accent, mc = build_document(spec, a.theme, a.max_colours, comps)
    with open(out, "w", encoding="utf-8") as f:
        f.write(doc)
    for (name, _, v), c in zip(sheets, comps):
        print("sheet %-28s vertices=%d legend=%d" % (name, v, c.legend_vertices))
    print("theme %s%s, %d colours: %s" % (theme, (" (max_colours %d)" % mc) if theme == "minimal" else "",
                                         len(cols), " ".join(sorted(cols))))
    print("wrote", out)
    n = WARNINGS[0] - start
    print("warnings: %d" % n)
    return 1 if a.strict and n else 0


def build_document(spec, cli_theme=None, cli_max_colours=None, compilers=None):
    """What build writes: (file text, [(name, xml, vertices)], colours, theme, accent, max_colours).
    The XML is parsed once and the palette checked before anything is written."""
    check_sheet_list(spec)
    mc_asked = check_max_colours(cli_max_colours, "--max-colours") if cli_max_colours is not None else None
    theme, accent = resolve_theme(spec, cli_theme, mc_asked)
    mc = resolve_max_colours(spec, cli_max_colours, theme)
    sheets = compile_spec(spec, theme, accent, mc, compilers)
    doc = wrap_file([x for _, x, _ in sheets], theme_record(theme, accent, mc))
    minidom.parseString(doc)
    cols = check_palette(theme, doc, accent, mc)
    return doc, sheets, cols, theme, accent, mc


def cmd_add(a):
    start = WARNINGS[0]
    spec = json.load(open(a.spec, encoding="utf-8"))
    check_sheet_list(spec)      # nothing to add: stop before the file is read, backed up or written
    root = read_file(a.into)
    index = sheet_index(root)
    existing, existing_ids = index["names"], index["ids"]
    # a position or a link that names a sheet the file holds twice (or whose id it holds twice) is
    # ambiguous: stop before anything is compiled, backed up or written
    # two sheets with one name, in the batch or between the batch and the file, would leave a file that
    # fails validate: stop first, in every theme (only a standalone colorful build keeps the pre-theme rule)
    batch = [sh.get("name") for sh in spec.get("sheets") or []]
    for nm in sorted(set(n for n in batch if batch.count(n) > 1), key=str):
        raise SystemExit("duplicate sheet name %r in the spec (%d sheets); every sheet needs its own name" % (
            nm, batch.count(nm)))
    pos = a.position or "first"
    if pos.startswith("before:") or pos.startswith("after:"):
        nm = pos.partition(":")[2]
        if nm in index["dup_names"]:
            raise SystemExit("--position %s: %s has %d sheets named %r; rename one in draw.io first" % (
                pos, a.into, existing.count(nm), nm))
    spec_names = set(sh.get("name") for sh in spec.get("sheets") or [])
    for sh in spec.get("sheets") or []:
        for c in sh.get("cells") or []:
            target = c.get("link")
            if not isinstance(target, str) or target in spec_names or target not in existing:
                continue
            if target in index["dup_names"]:
                raise SystemExit("sheet %r cell %r: link %r names %d sheets of %s; rename one in draw.io first" % (
                    sh.get("name"), c.get("id"), target, existing.count(target), a.into))
            tid = existing_ids[existing.index(target)]
            if tid in index["dup_ids"]:
                raise SystemExit("sheet %r cell %r: link %r opens diagram id %r, which %d sheets of %s share; a page link "
                                 "cannot tell them apart" % (sh.get("name"), c.get("id"), target, tid, existing_ids.count(tid), a.into))
    ftheme, faccent, fmc, recorded = file_theme(root)
    # pages whose theme records disagree: the file has no one theme to keep
    conflicts = record_conflicts(root)
    if conflicts:
        raise SystemExit("%s: %s. One file keeps one theme; make the records agree in draw.io (or rebuild) first." % (
            a.into, "; ".join(conflicts)))
    # the command line and the spec are each checked against the file: either conflict stops the run
    for asked, source in ((a.theme, "--theme"), (spec.get("theme"), "the spec's theme")):
        if not asked:
            continue
        check_theme(asked, source)
        if asked != ftheme:
            raise SystemExit("%s is drawn in theme %s%s; %s %s would mix themes in one file. "
                             "Drop --theme and the spec's theme to inherit %s." % (
                                 a.into, ftheme, "" if recorded else " (no theme record: treated as colorful)",
                                 source, asked, ftheme))
    # the contrast rule applies where the file draws the accent colour; bw warns below that it is ignored
    _, spec_accent = resolve_theme(spec, ftheme, fmc or DEFAULT_MAX_COLOURS, quiet=True)
    if setting(spec, ACCENT_KEYS) is not None and spec_accent != faccent:
        if ftheme == "bw":   # bw never draws the accent colour, so a different one changes nothing
            warn("%s is bw, which draws the accent as a black line; the spec's accent_color %s is ignored" % (
                a.into, spec_accent))
        else:
            raise SystemExit("%s records the accent %s%s; the spec's accent_color %s would put a second accent in the file. "
                             "Drop accent_color to inherit %s." % (
                                 a.into, faccent, "" if recorded else " (no theme record: the default)", spec_accent, faccent))
    accent = faccent
    mc = fmc or DEFAULT_MAX_COLOURS
    if a.max_colours is not None:
        asked_mc = check_max_colours(a.max_colours, "--max-colours")
    else:
        asked_mc = check_max_colours(setting(spec, MAX_COLOURS_KEYS), "spec")
    if asked_mc is not None and ftheme != "minimal":
        warn("max_colours applies to the minimal theme only; ignored in %s" % ftheme)
    if ftheme == "minimal" and asked_mc is not None and asked_mc != mc:
        raise SystemExit("%s was built with max_colours %d; the requested %s conflicts" % (a.into, mc, asked_mc))
    if ftheme in ("minimal", "bw"):
        effects = effect_problems(root, theme_palette(ftheme, accent, mc))
        if effects:
            # with the file's colours that break the palette, as validate reports them
            effects = palette_problems(ftheme, file_colours(root)[0], accent, mc) + effects
            raise SystemExit("%s: %s. Theme %s paints only its palette colours; remove them in draw.io first." % (
                a.into, "; ".join(effects), ftheme))
    # a link may name an existing sheet: it opens the diagram id the file holds for it
    pages = {}
    for d in root.findall("diagram"):
        if d.get("name") is not None and d.get("id") is not None:
            pages.setdefault(d.get("name"), d.get("id"))
    comps = []
    sheets = compile_spec(spec, ftheme, accent, mc, comps, pages)
    # the palette rule counts the whole file: existing sheets plus the new ones
    old_cols, _ = file_colours(root)
    new_cols = emitted_colours("".join(x for _, x, _ in sheets))
    cols = check_palette(ftheme, "", accent, mc, cols=old_cols | new_cols)
    new_elems = []
    for name, x, v in sheets:
        if name in existing:
            raise SystemExit("sheet %r already exists in %s; existing sheets are never rewritten" % (name, a.into))
        el = ET.fromstring(x)
        if el.get("id") in existing_ids:
            raise SystemExit("sheet id %r already used in %s" % (el.get("id"), a.into))
        new_elems.append(el)
    diagrams = root.findall("diagram")
    if pos == "first":
        idx = 0
    elif pos == "last":
        idx = len(diagrams)
    elif pos.startswith("before:") or pos.startswith("after:"):
        kind, _, nm = pos.partition(":")
        if nm not in existing:
            raise SystemExit("no sheet named %r in %s (have %s)" % (nm, a.into, existing))
        idx = existing.index(nm) + (1 if kind == "after" else 0)
    else:
        raise SystemExit("bad --position %r" % pos)
    for (name, _, v), c in zip(sheets, comps):
        print("sheet %-28s vertices=%d legend=%d" % (name, v, c.legend_vertices))
    n = WARNINGS[0] - start
    if a.strict and n:
        # --strict: a warned add leaves the file as it was (no backup, no write)
        print("--strict: %d warning(s); %s left unchanged" % (n, a.into), file=sys.stderr)
        print("warnings: %d" % n)
        return 1
    # rebuild child order: elements before idx, new, rest (non-diagram children kept in place)
    children = list(root)
    dpos = [i for i, ch in enumerate(children) if ch.tag == "diagram"]
    insert_at = dpos[idx] if idx < len(dpos) else (dpos[-1] + 1 if dpos else len(children))
    for k, el in enumerate(new_elems):
        root.insert(insert_at + k, el)
    # the assembled file (existing sheets plus the new ones) must hold one theme record
    conflicts = record_conflicts(root)
    if conflicts:
        raise SystemExit("%s: the new sheets would disagree with the file's theme records: %s; nothing written" % (
            a.into, "; ".join(conflicts)))
    # write atomically: a temporary file beside the destination, read back, then the backup, then one
    # rename over the destination; a failed write leaves the original bytes in place
    folder = os.path.dirname(os.path.abspath(a.into))
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(a.into) + ".", suffix=".tmp", dir=folder)
    os.close(fd)
    try:
        ET.ElementTree(root).write(tmp, encoding="utf-8", xml_declaration=False)
        read_file(tmp)
    except BaseException:
        os.remove(tmp)
        raise
    bak = a.into + ".bak"
    try:
        shutil.copyfile(a.into, bak)
        os.replace(tmp, a.into)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    print("theme %s (from the file%s), %d colours in the file: %s" % (
        ftheme, "" if recorded else ", no record: colorful", len(cols), " ".join(sorted(cols))))
    print("added %d sheet(s) to %s at position %s (backup: %s)" % (len(new_elems), a.into, pos, bak))
    print("warnings: %d" % n)
    return 0


def audit_problems(root, theme, cols, mc):
    """What breaks the palette the file records: colours outside it; in minimal and bw anything a
    style or label holds beyond the allow-list; and theme records that disagree."""
    accent = file_theme(root)[1]
    probs = palette_problems(theme, cols, accent, mc or DEFAULT_MAX_COLOURS)
    if theme in ("minimal", "bw"):
        probs += effect_problems(root, theme_palette(theme, accent, mc or DEFAULT_MAX_COLOURS))
    probs += record_conflicts(root)
    return probs


def cmd_list(a):
    root = read_file(a.file)
    tl, theme, cols, mc = theme_line(root)
    print(tl)
    for p in audit_problems(root, theme, cols, mc):
        print("warn " + p)
    if not root.findall("diagram"):
        print("warn the file holds no sheets (no <diagram>)")
    for name, sid, v, e, lg in sheet_summary(root):
        print("%-28s id=%-12s vertices=%-4d legend=%-3d edges=%d" % (name, sid, v, lg, e))


def cmd_validate(a):
    root = read_file(a.file)
    problems = 0
    warned = [0]

    def warn_line(msg):
        # a finding that does not stop draw.io; --strict counts these as well
        warned[0] += 1
        print("warn " + msg)
    tl, theme, cols, mc = theme_line(root)
    print(tl)
    # the palette the file records: theme, accent and max_colours
    for p in audit_problems(root, theme, cols, mc):
        print("ERR  " + p); problems += 1
    if not root.findall("diagram"):
        print("ERR  the file holds no sheets (no <diagram>); draw.io opens it empty"); problems += 1
    index = sheet_index(root)
    for nm in index["dup_names"]:
        print("ERR  %d sheets are named %r; before:NAME, after:NAME and a link by name cannot tell them apart" % (
            index["names"].count(nm), nm))
        problems += 1
    for sid in index["dup_ids"]:
        print("ERR  %d sheets have the diagram id %r; a page link to it cannot tell them apart" % (index["ids"].count(sid), sid))
        problems += 1
    page_ids = set(d.get("id") for d in root.findall("diagram"))
    for d in root.findall("diagram"):
        name = d.get("name", "")
        model = decode_diagram(d)
        m = ET.fromstring(model)
        for e in m.iter():
            link = e.get("link") if e.tag in WRAPPERS else None
            if link and link.startswith("data:page/id,") and link[len("data:page/id,"):] not in page_ids:
                print("ERR  %s: cell %s links to page id %s, which no sheet of this file has" % (
                    name, e.get("id"), link[len("data:page/id,"):])); problems += 1
        cells = model_cells(m)
        ids = {}
        for c in cells:
            i = c.get("id")
            if i in ids:
                print("ERR  %s: duplicate id %s" % (name, i)); problems += 1
            ids[i] = c

        def absolute(c, depth=0):
            g = c.find("mxGeometry")
            x, y = (float(g.get("x", 0)), float(g.get("y", 0))) if g is not None else (0.0, 0.0)
            p = ids.get(c.get("parent"))
            if p is not None and p.get("vertex") == "1" and depth < 50:
                px, py = absolute(p, depth + 1)
                x, y = x + px, y + py
            return x, y
        pw, ph = int(m.get("pageWidth", 0) or 0), int(m.get("pageHeight", 0) or 0)
        verts = legend = 0
        for c in cells:
            if c.get("edge") == "1":
                for k in ("source", "target"):
                    if c.get(k) not in ids:
                        print("ERR  %s: edge %s has dangling %s=%s" % (name, c.get("id"), k, c.get(k))); problems += 1
            elif c.get("vertex") == "1":
                if is_legend_cell(c):   # a generated legend is not a ladder vertex
                    legend += 1
                else:
                    verts += 1
                g = c.find("mxGeometry")
                if g is not None and g.get("relative") != "1":
                    w, h = float(g.get("width", 0)), float(g.get("height", 0))
                    if w <= 0 or h <= 0:
                        print("ERR  %s: cell %s (%s) has width %g and height %g; it cannot be seen" % (
                            name, c.get("id"), (c.get("value") or "")[:30], w, h)); problems += 1
                if g is not None and pw and ph:
                    x, y = absolute(c)
                    w, h = float(g.get("width", 0)), float(g.get("height", 0))
                    # a rotated cell covers the bounding rectangle of its turned geometry
                    bx, by, bw, bh = rotated_bounds(x, y, w, h, c.get("style"))
                    if bx + bw > pw + 1 or by + bh > ph + 1 or bx < 0 or by < 0:
                        warn_line("%s: cell %s (%s) outside page %dx%d" % (name, c.get("id"), (c.get("value") or "")[:30], pw, ph))
                    st = c.get("style") or ""
                    sd = parse_style(st)[0]
                    if sd.get("shape") == "umlActor" and sd.get("verticalLabelPosition") == "bottom":
                        cap = actor_caption(x, y, w, h, plain_text(c.get("value"), html_mode(st)), st)
                        if cap and (cap[0] < 0 or cap[1] < 0 or cap[0] + cap[2] > pw + 1 or cap[1] + cap[3] > ph + 1):
                            warn_line("%s: the caption of actor %s (%s) is outside page %dx%d" % (
                                name, c.get("id"), (c.get("value") or "")[:30], pw, ph))
        # a swimlane's title strip is not room for its contents (a lane built as a rectangle and a title
        # cell, as before swimlanes, has no strip to check)
        for c in cells:
            strip = swimlane_strip(c.get("style")) if c.get("vertex") == "1" else None
            g = c.find("mxGeometry")
            if not strip or not strip[1] or g is None:
                continue
            lw, lh = float(g.get("width", 0)), float(g.get("height", 0))
            for k in cells:
                kg = k.find("mxGeometry")
                if k.get("vertex") != "1" or k.get("parent") != c.get("id") or kg is None or kg.get("relative") == "1":
                    continue
                # the child as drawn: a rotated child by the bounds of its turned shape
                kx, ky, kw, kh = rotated_bounds(*(float(kg.get(a, 0)) for a in ("x", "y", "width", "height")),
                                                style=k.get("style"))
                if strip_overlap(strip[0], strip[1], lw, lh, kx, ky, kw, kh):
                    warn_line("%s: cell %s (%s) overlaps the title strip of lane %s" % (
                        name, k.get("id"), (k.get("value") or "")[:30], c.get("id")))
        line = "ok   %-28s vertices=%d legend=%d" % (name, verts, legend)
        if a.ladder:
            mm = re.match(r"(L\d)", name)
            if mm and mm.group(1) in LADDER:
                lo, hi, desc = LADDER[mm.group(1)]
                if not (lo <= verts <= hi):
                    print("ERR  %s: %d vertices outside %s budget %d-%d (%s)" % (name, verts, mm.group(1), lo, hi, desc)); problems += 1
                else:
                    line += "  within %s budget %d-%d (legend cells not counted)" % (mm.group(1), lo, hi)
                if theme in ("minimal", "bw") and mm.group(1) != "L0" and legend == 0:
                    warn_line("%s: no legend cell; theme %s tells roles apart by shape only, so every sheet "
                          "from L1 down needs one" % (name, theme))
            else:
                warn_line("%s: name has no L<n> prefix; ladder budget not checked" % name)
        print(line)
    print("problems:", problems)
    if getattr(a, "strict", False) and warned[0]:
        print("--strict: %d warn line(s)" % warned[0])
        return 1
    return 1 if problems else 0


def find_drawio():
    env = os.environ.get("DRAWIO_EXE")
    cands = [env, shutil.which("drawio"), shutil.which("draw.io"),
             os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "draw.io", "draw.io.exe"),
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "draw.io", "draw.io.exe"),
             "/Applications/draw.io.app/Contents/MacOS/draw.io", "/usr/bin/drawio", "/snap/bin/drawio"]
    for c in cands:
        if c and os.path.exists(c):
            return c
    if sys.platform == "win32":
        # Microsoft Store build: lives under WindowsApps (not listable, but executable by path).
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command",
                                "(Get-AppxPackage draw.io.draw.ioDiagrams).InstallLocation"],
                               capture_output=True, text=True, timeout=30)
            loc = (r.stdout or "").strip().splitlines()
            if loc:
                exe = os.path.join(loc[-1], "app", "draw.io.exe")
                if os.path.exists(exe):
                    return exe
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def run_export(exe, src, out, fmt, page, scale):
    """Export one page (`page` as the renderer counts it) to `out`: (ok, invalid_index), invalid_index
    True when the renderer said the page index is invalid (it counts from the other base)."""
    cmd = [exe, "-x", "-f", fmt, "-s", str(scale), "-p", str(page), "-o", out, src]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if r.returncode == 0 and os.path.exists(out):
        return True, False
    text = (r.stdout or "") + (r.stderr or "")
    if "Invalid page index" in text:
        return False, True
    msg = [ln for ln in text.splitlines() if "disk_cache" not in ln and "gpu" not in ln.lower()]
    print("export failed (rc=%s): %s" % (r.returncode, " | ".join(msg[-3:])), file=sys.stderr)
    return False, False


def export_names(base, names, fmt):
    """One output file name per sheet, all resolved before the renderer runs: <base>__<sheet>.<fmt>,
    the sheet name reduced to letters, digits, '_', '.' and '-'. Names that would collide (compared
    without regard to case, as Windows and macOS compare them) get the page number appended (_p2), so no
    page overwrites another."""
    safe = [re.sub(r"[^A-Za-z0-9_.-]+", "_", n or "") for n in names]
    files = ["%s__%s" % (base, s) for s in safe]
    count = {}
    for f in files:
        count[f.lower()] = count.get(f.lower(), 0) + 1
    taken = set(f.lower() for f in files if count[f.lower()] == 1)
    out = []
    for i, f in enumerate(files):
        if count[f.lower()] > 1:
            g, k = "%s_p%d" % (f, i + 1), 1
            while g.lower() in taken:
                k += 1
                g = "%s_p%d_%d" % (f, i + 1, k)
            f = g
        taken.add(f.lower())
        out.append("%s.%s" % (f, fmt))
    return out


def cmd_export(a):
    exe = find_drawio()
    if not exe:
        print("no draw.io CLI found (checked DRAWIO_EXE, PATH, Program Files, LOCALAPPDATA, the Microsoft Store package); open the file in draw.io to view it")
        return 2
    outdir = a.out or os.path.dirname(os.path.abspath(a.file))
    os.makedirs(outdir, exist_ok=True)
    root = read_file(a.file)
    names = [d.get("name") for d in root.findall("diagram")]
    base = os.path.splitext(os.path.basename(a.file))[0]
    outs = [os.path.join(outdir, f) for f in export_names(base, names, a.format)]
    src = os.path.abspath(a.file)
    # the renderer's page base (draw.io >= 27.0.2 counts from 1) is found once per run, on the last
    # page: there a one-based index is valid only for a one-based renderer, so the answer is never a
    # wrong page that happens to exist. Every page then uses that base.
    order = [len(names) - 1] + list(range(len(names) - 1)) if names else []
    base_index, ok_of = None, {}
    for i in order:
        if base_index is None:
            ok, invalid = run_export(exe, src, outs[i], a.format, i + 1, a.scale)
            if ok:
                base_index = 1
            elif invalid:
                ok, _ = run_export(exe, src, outs[i], a.format, i, a.scale)
                base_index = 0 if ok else None
        else:
            ok, _ = run_export(exe, src, outs[i], a.format, i + base_index, a.scale)
        ok_of[i] = ok
    rc = 0
    for i, name in enumerate(names):
        print("%s %-28s -> %s" % ("ok  " if ok_of[i] else "FAIL", name, outs[i]))
        rc = rc or (0 if ok_of[i] else 1)
    return rc


def main():
    # diagnostics must not abort a build on a console that cannot show the text (cp1252 and Korean
    # labels); the diagram file itself is always written as UTF-8
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("spec"); b.add_argument("-o", "--out"); b.add_argument("--force", action="store_true")
    b.add_argument("--theme", help="minimal (default), colorful or bw; overrides spec['theme']")
    b.add_argument("--max-colours", "--max-colors", dest="max_colours", type=int,
                   help="minimal only: 3 or 4 (default 4); overrides spec['max_colours']")
    b.add_argument("--strict", action="store_true", help="exit 1 when the build printed any warning (the file is still written)")
    ad = sub.add_parser("add"); ad.add_argument("spec"); ad.add_argument("--into", required=True); ad.add_argument("--position")
    ad.add_argument("--theme", help="must match the file's theme (the default is the file's theme)")
    ad.add_argument("--max-colours", "--max-colors", dest="max_colours", type=int, help="must match the file's max_colours")
    ad.add_argument("--strict", action="store_true", help="exit 1 and leave the file unchanged when the add printed any warning")
    ls = sub.add_parser("list"); ls.add_argument("file")
    va = sub.add_parser("validate"); va.add_argument("file"); va.add_argument("--ladder", action="store_true")
    va.add_argument("--strict", action="store_true", help="exit 1 when any warn line was printed (as well as on any ERR)")
    ex = sub.add_parser("export"); ex.add_argument("file"); ex.add_argument("--format", default="png"); ex.add_argument("--out"); ex.add_argument("--scale", default="1.5")
    a = p.parse_args()
    rc = {"build": cmd_build, "add": cmd_add, "list": cmd_list, "validate": cmd_validate, "export": cmd_export}[a.cmd](a)
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()

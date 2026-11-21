#!/usr/bin/env python3
"""drawio_spec.py: compile a JSON sheet spec into a draw.io (.drawio) file, add sheets to an
existing file, list, validate, and export (when a draw.io CLI is installed).

Usage
  python drawio_spec.py build    SPEC.json [-o OUT.drawio] [--force] [--theme minimal|colorful|bw] [--max-colours 3|4] (or --max-colors)
  python drawio_spec.py add      SPEC.json --into FILE.drawio [--position first|last|before:NAME|after:NAME] [--theme ...] [--max-colours ...]
  python drawio_spec.py list     FILE.drawio
  python drawio_spec.py validate FILE.drawio [--ladder]
  python drawio_spec.py export   FILE.drawio [--format png|svg|pdf] [--out DIR]

Spec format: see ../references/spec_format.md. Roles are one fixed vocabulary so every sheet of a
file reads the same way (who decides). The theme sets how a role is drawn: by shape (minimal, bw)
or by colour (colorful). The theme is recorded on <mxfile>, and in minimal and bw also as style keys on
the first vertex of each sheet, so 'add' keeps it. Standard library only.
"""
import argparse
import base64
import difflib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import unicodedata
import urllib.parse
import zlib
import xml.dom.minidom as minidom
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

ESC = {'"': '&quot;'}


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
# role -> shape in the minimal and bw themes (ANSI/ISO flowchart symbols)
ROLE_SHAPES = {"human": "manual_input", "propose": "rounded", "decide": "predefined",
               "guard": "preparation", "outside": "double", "record": "document",
               "note": "note", "stage": "step", "plain": "process"}
# roles an explicit shape may carry without hiding them (minimal, bw); the legend reads
# '<shape meaning>: <role meaning>'. Any other role: build warns that the shape hides it.
SHAPE_ROLES = {"actor": ("human",), "decision": ("human", "decide")}
DEFAULT_SHAPE_ROLES = ("plain", "outside", "record")


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
    # typography
    "html", "whiteSpace", "overflow", "fontSize", "fontStyle", "fontFamily", "align", "verticalAlign",
    "labelPosition", "verticalLabelPosition", "labelWidth", "horizontal", "textDirection",
    # perimeter, arc, line, rotation
    "perimeter", "perimeterSpacing", "sourcePerimeterSpacing", "targetPerimeterSpacing",
    "rounded", "arcSize", "absoluteArcSize", "roundedPerimeter", "dashed", "dashPattern", "strokeWidth", "rotation",
) + STYLE_COLOUR_KEYS)
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


def warn(msg):
    print("warn: " + msg, file=sys.stderr)


# Keys a spec may use. Anything else is ignored with a warning that names the nearest valid key.
ACCENT_KEYS = ("accent_color", "accent_colour")
MAX_COLOURS_KEYS = ("max_colours", "max_colors")
SPEC_KEYS = ("file", "theme", "sheets") + ACCENT_KEYS + MAX_COLOURS_KEYS
SHEET_KEYS = ("id", "name", "width", "height", "grid", "cells")
_GEOM_KEYS = ("type", "id", "x", "y", "w", "h", "col", "span")
CELL_KEYS = {
    "box": _GEOM_KEYS + ("text", "role", "shape", "accent", "font", "bold", "headline", "align", "valign",
                         "dashed", "fill", "stroke", "square", "style"),
    "lane": _GEOM_KEYS + ("text", "role", "gutter", "fill", "font"),
    "text": _GEOM_KEYS + ("text", "font", "bold", "align"),
    "edge": ("type", "id", "from", "to", "label", "role", "color", "accent", "dashed", "width", "exit", "entry",
             "points", "label_at", "label_offset", "font", "style"),
    "legend": ("type", "id", "x", "y", "w", "col", "span", "labels", "title", "font"),
}


def _nearest(key, valid):
    m = difflib.get_close_matches(key, valid, n=1, cutoff=0.6)
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
        for c in sh.get("cells") or []:
            valid = CELL_KEYS.get(c.get("type", "box"))
            if valid is None:
                continue    # an unknown type stops the run when the cell is compiled
            for k in c:
                if k not in valid:
                    warn("sheet %r cell %r: unknown key %r for a %s (%s); ignored" % (
                        nm, c.get("id", (c.get("text") or c.get("label") or "")[:30]), k, c.get("type", "box"),
                        _nearest(k, valid)))


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


def check_accent(v, source):
    if not isinstance(v, str) or not re.match(r"^#[0-9A-Fa-f]{6}$", v):
        raise SystemExit("%s: accent_color %r is not a #RRGGBB colour" % (source, v))
    v = v.upper()
    named = {INK: "black", PAPER: "white", TINT: "the grey tint"}
    if v in named:
        raise SystemExit("%s: accent_color %s is %s, which the palette already uses; the accent would not stand "
                         "out. Pick another colour" % (source, v, named[v]))
    return v


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


def resolve_theme(spec, cli_theme=None):
    if cli_theme:
        theme = check_theme(cli_theme, "--theme")
    else:
        theme = check_theme(spec.get("theme") or DEFAULT_THEME, "spec")
    accent = setting(spec, ACCENT_KEYS)
    return theme, (DEFAULT_ACCENT if accent is None else check_accent(accent, "spec"))


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
    """A normalised style string without the given keys."""
    return "".join(t + ";" for t in (st or "").split(";") if t and t.split("=", 1)[0] not in keys)


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


def delay_inset(h):
    """Right spacing that keeps text clear of a delay's round right side."""
    return max(0, int(h) // 2 - 6)


def needed_height(text, w, font, shape_name, bold_first=False, bold_all=False, pad=12, extra_right=0):
    """(lines, height) a box needs: lines x 1.35 x font + pad, plus the shape's allowance.
    pad 12 is the height rule ("h": "auto"); pad 8 (the label's own spacing) is where text clips.
    extra_right: spacing on the right that the box adds to its shape's own (delay)."""
    _, room, extra, allow_px, allow_frac = SHAPES[shape_name]
    sp = style_spacing(extra)
    width = max(1.0, w * (room or 1.0) - 8 - sp["spacingLeft"] - sp["spacingRight"] - extra_right
                - BAR_INSET.get(shape_name, 0))
    lines = wrapped_lines(text, width, font, bold_first, bold_all)
    need = lines * 1.35 * font + pad + allow_px + sp["spacingBottom"]
    if allow_frac:
        need = need / (1.0 - allow_frac)
    return lines, int(math.ceil(need))


# --------------------------------------------------------------------------- compile
class Compiler:
    def __init__(self, sheet, theme="colorful", accent=DEFAULT_ACCENT, max_colours=DEFAULT_MAX_COLOURS):
        self.s = sheet
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
        self.ids[full] = True
        return full

    def geom(self, c, default_w=None, default_h=None):
        if ("col" in c or "span" in c) and not (self.gpitch > 0 and self.gcw > 0):
            raise SystemExit("%s uses col/span, but the sheet has no usable grid (set grid.pitch and grid.col_width "
                             "above 0, or give x and w)" % self.where(c))
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
        or edge that sets accent, so the legend's accent row explains every use of it."""
        if self.theme == "minimal" and self.accent in self.palette and not c.get("accent"):
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
            if k == "shape":
                if v not in CATALOGUE_DRAWIO_SHAPES:
                    raise SystemExit("%s: style key shape=%s is not a catalogue shape; not allowed in theme %s "
                                     "(use the cell's \"shape\": %s)" % (self.where(c), v, self.theme, ", ".join(sorted(SHAPES))))
            elif k not in STYLE_ALLOWED_KEYS:
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
    def shape_style(self, shape_name, font, fill, stroke, align, valign, extra=""):
        base, _, spacing, _, _ = SHAPES[shape_name]
        if shape_name == "actor":
            align, valign = "center", "top"
        if shape_name in CENTRED_SHAPES:
            align, valign = "center", "middle"
        return "%swhiteSpace=wrap;html=1;fontSize=%d;fillColor=%s;strokeColor=%s;fontColor=%s;align=%s;verticalAlign=%s;spacing=4;%s%s" % (
            base, font, fill, stroke, INK, align, valign, spacing, extra)

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
                          "head": head, "auto": auto, "raw": raw})

    def vertex(self, c):
        t = c.get("type", "box")
        x, y, w, h = self.geom(c)
        role = c.get("role", "plain")
        if role not in ROLES:
            raise SystemExit("unknown role %r in sheet %r" % (role, self.s["name"]))
        font = c.get("font", 9)
        text = c.get("text", "")
        if t == "lane":
            if self.theme == "colorful":
                fill = c.get("fill", LANE_FILLS.get(role, "#F5F5F5"))
                lane_stroke = "#BDBDBD"
            else:
                fill, lane_stroke = self.colour_override(c, "fill", self.tint), INK
            style = "rounded=0;whiteSpace=wrap;html=1;fontSize=%d;fillColor=%s;strokeColor=%s;align=left;verticalAlign=top;spacingLeft=6;" % (font, fill, lane_stroke)
            if self.shaped:
                style += CONTAINER_KEY
            i = self.nid(c.get("id"))
            self.add_rec(i, "", style, x, y, w, h, "lane")
            if text:
                # lane title in a gutter on the left, vertically centred
                gw = c.get("gutter", 150)
                j = self.nid()
                tstyle = "text;html=1;whiteSpace=wrap;fontSize=%d;align=center;verticalAlign=middle;fontStyle=1;" % max(font, 10)
                self.add_rec(j, text, tstyle, x + 4, y, gw, h, "text")
            return
        if t == "text":
            style = "text;html=1;whiteSpace=wrap;fontSize=%d;align=%s;verticalAlign=middle;" % (font, c.get("align", "left"))
            if c.get("bold", True):
                style += "fontStyle=1;"
            i = self.nid(c.get("id"))
            self.add_rec(i, text, style, x, y, w, h, "text")
            return
        if t != "box":
            raise SystemExit("unknown cell type %r in %s" % (t, self.where(c)))
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
        # height: given, or computed from the wrapped text ("h": "auto"). A delay's right inset
        # depends on its height, so the two are computed together.
        has_room = SHAPES[shape_name][1] is not None and bool(text)

        def fit(right, pad):
            if not has_room:
                return 0, 0
            return needed_height(text, w, font, shape_name, bold_first=headline, bold_all=bold, pad=pad, extra_right=right)
        inset = 0
        if h == "auto":
            if frame:
                raise SystemExit('%s: a frame takes its height from what it holds; give "h" a number' % self.where(c))
            if SHAPES[shape_name][1] is None:
                raise SystemExit('%s: "h": "auto" is not available for shape %r' % (self.where(c), shape_name))
            for _ in range(60):
                lines, need = fit(inset, 12)
                h = max(need, int(math.ceil(1.35 * font + 12)))
                nxt = delay_inset(h) if shape_name == "delay" else 0
                if nxt == inset:
                    break
                inset = nxt
            else:
                raise SystemExit("%s: this delay is too narrow for its text; widen it" % self.where(c))
            print("auto h: sheet %r box %r h=%d (%d lines, font %d)" % (self.s["name"], c.get("id", text[:30]), h, lines, font))
            auto = True
        else:
            auto = False
            if shape_name == "delay":
                inset = delay_inset(h)
            lines, need = fit(inset, 12)
            _, clip = fit(inset, 8)
            if clip > h and not frame:
                warn("sheet %r box %r text clips: %d wrapped lines at font %d need h>=%d (h=%d, w=%d, shape %s)" % (
                    self.s["name"], c.get("id", text[:30]), lines, font, need, h, w, shape_name))
        if self.theme == "colorful" and shape is None:
            # the original look, byte for byte
            fill, stroke, dashed = ROLES[role]
            fill = c.get("fill", fill)
            base_stroke = c.get("stroke", stroke)
            dashed = c.get("dashed", dashed)

            def paint(st_stroke, width):
                s = "rounded=%d;whiteSpace=wrap;html=1;fontSize=%d;fillColor=%s;strokeColor=%s;align=%s;verticalAlign=%s;spacing=4;" % (
                    0 if frame else 1, font, fill, st_stroke, align, valign)
                s += ("fontStyle=1;" if bold else "") + ("dashed=1;" if dashed else "")
                return s + ("strokeWidth=%d;" % width if width else "") + user_style
            style = paint(self.accent, 2) if accent else paint(base_stroke, 0)
            look_style = paint(base_stroke, 0)
        else:
            fill, stroke, dashed = self.role_paint(role, frame)
            fill = self.colour_override(c, "fill", fill)
            stroke = self.colour_override(c, "stroke", stroke)
            dashed = c.get("dashed", dashed)
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
                s = self.shape_style(shape_name, font, fill, st_stroke, align, valign, extra)
                s += ("fontStyle=1;" if bold else "") + ("dashed=1;" if dashed else "")
                if not self.shaped:     # colorful with a catalogue shape, as before
                    return s + ("strokeWidth=2;" if ustyle is None else "") + user_style
                if frame:
                    s += CONTAINER_KEY
                return s + (ustyle if ustyle is not None else "")
            look_style = paint(stroke, user_style)
            if not accent:
                style = look_style
                self.check_plain_width(c, style)
            elif not self.shaped:
                style = paint(self.accent, None)
            else:
                # the accent is applied after every override, so it is drawn as the legend shows it
                a_col, a_w = (INK, ACCENT_WIDTH) if self.accent_as_width else (self.accent, 2)
                style = paint(a_col, drop_keys(user_style, ("strokeColor", "strokeWidth"))) + "strokeWidth=%d;" % a_w
        head = 0
        if frame and text:
            head = needed_height(text, w, font, "process", bold_first=headline, bold_all=bold)[1]
        i = self.nid(c.get("id"))
        self.add_rec(i, text, style, x, y, w, h, "frame" if frame else "box", headline=headline, head=head, auto=auto)
        self.note_look(c, role, legend_shape, shape_name, frame, look_style)

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
    def legend_labels(self):
        for lc in self.s["cells"]:
            if lc.get("type") == "legend":
                return lc.get("labels") or {}
        return {}

    def note_look(self, c, role, shape, shape_name, frame, style):
        """Record what a box looks like (its final shape and paint, without its accent) and what it
        means, for the legend. Colours are normalised so one paint spelled two ways is one row."""
        d, _ = parse_style(style)
        fill = norm_paint(d.get("fillColor", "none"))
        stroke = norm_paint(d.get("strokeColor", "none"))
        dashed = d.get("dashed", "0") == "1"
        labels = self.legend_labels()
        rmean = labels.get(role, ROLE_MEANINGS[role])
        rkey = (0, list(ROLES).index(role))
        if self.theme == "colorful":
            # roles by colour (frames included), catalogue shapes by outline in plain colours
            self.looks.append((c, ("role", fill, stroke, dashed), ("role", role, fill, stroke, dashed), rmean, rkey, (role, None)))
            if shape is not None:
                pf, ps, _ = ROLES["plain"]
                self.looks.append((c, ("shape", shape), ("shape", shape, pf, ps, False), labels.get(shape, SHAPE_MEANINGS[shape]),
                                   (1, list(SHAPES).index(shape)), (None, shape)))
            return
        if frame:
            return
        if shape is not None:
            meaning, key = labels.get(shape, SHAPE_MEANINGS[shape]), (1, list(SHAPES).index(shape))
            if role in shape_roles(shape):
                meaning = "%s: %s" % (meaning, rmean)
        else:
            meaning, key = rmean, rkey
        self.looks.append((c, ("shape", shape_name, fill, stroke, dashed), ("shape", shape_name, fill, stroke, dashed),
                           meaning, key, (role, shape)))

    def legend_items(self):
        """Legend rows: one per distinct appearance, in role then shape order; an appearance with
        two meanings shows both. The accent comes last."""
        groups = {}
        variants = {}
        for c, look, sample, meaning, key, who in self.looks:
            g = groups.setdefault(look, {"sample": sample, "meanings": [], "key": key})
            g["key"] = min(g["key"], key)
            if meaning not in g["meanings"]:
                g["meanings"].append(meaning)
            variants.setdefault(who, set()).add(look)
        for (role, shape), looks in variants.items():
            if len(looks) > 1:
                warn("sheet %r: boxes of %s are painted %d different ways; the legend shows each" % (
                    self.s["name"], ("role %r" % role) if shape is None else ("shape %r" % shape), len(looks)))
        items = sorted(groups.values(), key=lambda g: g["key"])
        rows = [(g["sample"], "; ".join(g["meanings"])) for g in items]
        box_accent = any(c.get("accent") and c.get("type", "box") == "box" for c in self.s["cells"])
        edge_accent = any(c.get("accent") and c.get("type") == "edge" for c in self.s["cells"])
        if box_accent or edge_accent:
            # only edges carry it: the sample is a line, as the edges are
            rows.append((("accent", "box" if box_accent else "edge"), self.legend_labels().get("accent", "highlighted")))
        return rows

    def sample_style(self, sample, font):
        """The legend sample: the shape and paint of the boxes it describes, label to its right."""
        if sample[0] == "accent":
            col, width = (INK, ACCENT_WIDTH) if self.accent_as_width else (self.accent, 2)
            if sample[1] == "edge":
                return "shape=line;html=1;fillColor=none;strokeColor=%s;strokeWidth=%d;" % (col, width)
            return "rounded=0;html=1;fillColor=%s;strokeColor=%s;strokeWidth=%d;" % (PAPER, col, width)
        kind, name, fill, stroke, dashed = sample
        if kind == "role":   # colorful
            st = "rounded=1;html=1;fillColor=%s;strokeColor=%s;" % (fill, stroke)
        else:
            base = SHAPES[name][0]
            d, bare = parse_style(base)
            for k in ("boundedLbl", "verticalLabelPosition", "labelPosition"):
                d.pop(k, None)
            st = "".join(b + ";" for b in bare) + "".join("%s=%s;" % kv for kv in d.items())
            st += "html=1;fillColor=%s;strokeColor=%s;" % (fill, stroke)
        if dashed:
            st += "dashed=1;"
        return st

    def legend(self, c):
        x, y, w, _ = self.geom(c, default_w=220)
        if "w" not in c and "span" not in c:
            w = 220
        font = c.get("font", 9)
        tfont = max(font, 10)
        items = self.legend_items()
        sw, sh = 44, 20
        lab_x = 10 + sw + 8              # label starts here, relative to the legend's left edge
        lab_w = w - lab_x - 8
        if lab_w < max(40, 4 * font):
            raise SystemExit("%s: legend width %d leaves %d px for labels; make it at least %d wide" % (
                self.where(c), w, lab_w, lab_x + 8 + max(40, 4 * font)))
        top = int(math.ceil(tfont * 1.35 + 12))
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
            if self.accent_as_width:
                color, width = INK, ACCENT_WIDTH
            else:
                color, width = self.accent, max(width, 2)
            if self.shaped:   # the accent wins over the edge's own style, as the legend shows it
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
        style += user_style
        i = self.nid(c.get("id"))
        inner = ""
        if c.get("points"):
            inner += '<Array as="points">%s</Array>' % "".join('<mxPoint x="%d" y="%d"/>' % (int(px), int(py)) for px, py in c["points"])
        if c.get("label_offset"):
            inner += '<mxPoint x="%d" y="%d" as="offset"/>' % tuple(int(v) for v in c["label_offset"])
        # label_at: -1 = at the source, 0 = middle (default), 1 = at the target
        geom_x = ' x="%s"' % c["label_at"] if "label_at" in c else ""
        self.edges.append('<mxCell id="%s" value="%s" style="%s" edge="1" parent="1" source="%s" target="%s"><mxGeometry%s relative="1" as="geometry">%s</mxGeometry></mxCell>' % (
            i, (old_label if self.theme == "colorful" else label)(c.get("label", "")), style, src, dst, geom_x, inner))

    # ---- containment (minimal, bw): frames and lanes are draw.io containers
    def nest(self):
        conts = [k for k, r in enumerate(self.recs) if r["kind"] in ("frame", "lane")]

        def inside(a, b):
            return (a["x"] >= b["x"] and a["y"] >= b["y"] and a["x"] + a["w"] <= b["x"] + b["w"]
                    and a["y"] + a["h"] <= b["y"] + b["h"])
        for k, r in enumerate(self.recs):
            if r["kind"] not in ("box", "frame", "text", "lane"):
                continue
            best = None
            for j in conts:
                if j == k:
                    continue
                p = self.recs[j]
                area_p, area_r = p["w"] * p["h"], r["w"] * r["h"]
                if not inside(r, p) or area_p < area_r or (area_p == area_r and j > k):
                    continue
                if best is None or area_p < self.recs[best]["w"] * self.recs[best]["h"]:
                    best = j
            r["parent"] = best

    def check_overlaps(self):
        """Warn for every pair of vertices that overlap without one containing the other, and for a
        legend that covers any content vertex (containment included; a lane or frame may hold it)."""
        boxes = [(r["id"].split("_", 1)[1], r) for r in self.recs if r["kind"] in ("box", "frame", "text", "lane", "legend")]
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
        sides; an auto-height box that grows past its frame's bottom is reported."""
        nm = self.s["name"]
        for k, f in enumerate(self.recs):
            if f["kind"] != "frame":
                continue
            fid = f["id"].split("_", 1)[1]
            top = f["y"] + max(f["head"], FRAME_PAD)
            for r in self.recs:
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
            out.append('<mxCell id="%s" value="%s" style="%s" vertex="1" parent="%s"><mxGeometry x="%d" y="%d" width="%d" height="%d" as="geometry"/></mxCell>' % (
                r["id"], value, r["style"], pid, x, y, r["w"], r["h"]))
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

    def compile(self):
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
            self.check_frames()
            if not any(c.get("type") == "legend" for c in self.s["cells"]):
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
        self.check_overlaps()
        if self.shaped and self.recs:
            # a second theme record that survives a save in draw.io, which may drop <mxfile> attributes
            self.recs[0]["style"] += style_record(self.theme, self.accent, self.max_colours)
        w, h = self.s.get("width", 1200), self.s.get("height", 800)
        return ('<diagram id="%s" name="%s"><mxGraphModel dx="1400" dy="800" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="%d" pageHeight="%d" math="0" shadow="0">'
                '<root><mxCell id="0"/><mxCell id="1" parent="0"/>%s</root></mxGraphModel></diagram>' % (
                    escape(self.sid, ESC), escape(self.s["name"], ESC), w, h, self.emit() + "".join(self.edges)))


def compile_spec(spec, theme=None, accent=None, max_colours=None, compilers=None):
    """theme: CLI override; else spec['theme']; else minimal. Returns [(name, xml, vertices)]."""
    check_keys(spec)
    theme, spec_accent = resolve_theme(spec, theme)
    accent = accent or spec_accent
    if max_colours is None:
        max_colours = resolve_max_colours(spec, None, theme) if theme == "minimal" else DEFAULT_MAX_COLOURS
    out = []
    seen, names = set(), set()
    for sh in spec["sheets"]:
        if sh["id"] in seen:
            raise SystemExit("duplicate sheet id %r" % sh["id"])
        if sh["name"] in names:
            raise SystemExit("duplicate sheet name %r in the spec; every sheet needs its own name" % sh["name"])
        seen.add(sh["id"])
        names.add(sh["name"])
        c = Compiler(sh, theme, accent, max_colours)
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
    """The same record as style keys (minimal, bw), written on the first vertex of each sheet:
    draw.io keeps unknown style keys when it saves."""
    if theme == "minimal":
        return "dsTheme=minimal;dsAccent=%s;dsMaxColours=%d;" % (accent, max_colours)
    return "dsTheme=%s;dsAccent=%s;" % (theme, accent)


def wrap_file(diagrams_xml, record=""):
    return '<mxfile host="app.diagrams.net" agent="drawio_spec.py" version="24.0"%s>%s</mxfile>' % (record, "".join(diagrams_xml))


RECORD_KEYS = ("dsTheme", "dsAccent", "dsMaxColours")


def sheet_record(root):
    """The theme record kept as style keys on a cell, or {} when no cell carries one."""
    for d in root.findall("diagram"):
        model = decode_diagram(d)
        if not model or "dsTheme=" not in model:
            continue
        for e in ET.fromstring(model).iter():
            st = e.get("style") or ""
            if "dsTheme=" in st:
                kv = parse_style(st)[0]
                return {k: kv[k] for k in RECORD_KEYS if k in kv}
    return {}


def file_theme(root):
    """(theme, accent, max_colours, recorded). The <mxfile> attributes are read first, then the style
    keys on the first vertex of a sheet; a file with neither is colorful. A record that is present
    goes through the same checks as the CLI and the spec."""
    rec = {k: root.get(k) for k in RECORD_KEYS if root.get(k) is not None}
    source = "the file's theme record"
    if "dsTheme" not in rec:
        rec, source = sheet_record(root), "the file's theme record (cell style)"
    if "dsTheme" not in rec:
        return "colorful", DEFAULT_ACCENT, None, False
    theme = check_theme(rec["dsTheme"], source)
    accent = check_accent(rec.get("dsAccent") or DEFAULT_ACCENT, source)
    mc = check_max_colours(rec.get("dsMaxColours"), source)
    return theme, accent, mc, True


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
HTML_STYLE_ATTR = re.compile(r"""\sstyle\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)
CSS_PAINT = ("color", "background", "background-color")
CSS_COLOUR_TOKEN = re.compile(r"#[0-9A-Fa-f]{3,8}\b|rgba?\([^)]*\)")
HTML_NO_PAINT = ("transparent", "currentcolor", "initial", "unset", "inherit", "none")


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
    """The colours a label's active HTML paints."""
    found = set()
    for tag in HTML_TAG.findall(value or ""):
        for m in HTML_COLOUR_ATTR.finditer(tag):
            c = html_colour(next(g for g in m.groups() if g is not None))
            if c:
                found.add(c)
        for m in HTML_STYLE_ATTR.finditer(tag):
            for decl in (m.group(1) if m.group(1) is not None else m.group(2)).split(";"):
                if ":" not in decl:
                    continue
                prop, val = decl.split(":", 1)
                if prop.strip().lower() not in CSS_PAINT:
                    continue
                val = val.replace("!important", "").strip()
                if re.match(r"^[A-Za-z]+$", val) or prop.strip().lower() == "color":
                    toks = [val]           # one colour (a name, #hex or rgb())
                else:
                    toks = CSS_COLOUR_TOKEN.findall(val)   # shorthand: the #hex and rgb() parts
                for tok in toks:
                    c = html_colour(tok)
                    if c:
                        found.add(c)
    return found


def element_colours(el):
    """Every colour an element and its descendants paint: each *Color key of each style, the page
    background of each graph model, and colour in the active HTML of each label."""
    cols = colours_of_styles([e.get("style") for e in el.iter() if e.get("style") is not None])
    for e in el.iter():
        if e.tag == "mxGraphModel":
            bg = e.get("background")
            if bg is not None and html_colour(bg):
                cols.add(html_colour(bg))
        for attr in ("value", "label"):
            v = e.get(attr)
            if v and "<" in v:
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


def is_legend_cell(c):
    return LEGEND_KEY in (c.get("style") or "")


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
        m = ET.fromstring(model) if model else None
        vs = m.findall(".//mxCell[@vertex='1']") if m is not None else []
        verts = len([c for c in vs if not is_legend_cell(c)])
        legend = len(vs) - verts
        edges = len(m.findall(".//mxCell[@edge='1']")) if m is not None else 0
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
        raise SystemExit("%s exists; use 'add' to append sheets or --force to overwrite" % out)
    theme, accent = resolve_theme(spec, a.theme)
    mc = resolve_max_colours(spec, a.max_colours, theme)
    comps = []
    sheets = compile_spec(spec, theme, accent, mc, comps)
    doc = wrap_file([x for _, x, _ in sheets], theme_record(theme, accent, mc))
    minidom.parseString(doc)
    cols = check_palette(theme, doc, accent, mc)
    with open(out, "w", encoding="utf-8") as f:
        f.write(doc)
    for (name, _, v), c in zip(sheets, comps):
        print("sheet %-28s vertices=%d legend=%d" % (name, v, c.legend_vertices))
    print("theme %s%s, %d colours: %s" % (theme, (" (max_colours %d)" % mc) if theme == "minimal" else "",
                                         len(cols), " ".join(sorted(cols))))
    print("wrote", out)


def cmd_add(a):
    spec = json.load(open(a.spec, encoding="utf-8"))
    root = read_file(a.into)
    existing = [d.get("name") for d in root.findall("diagram")]
    existing_ids = [d.get("id") for d in root.findall("diagram")]
    ftheme, faccent, fmc, recorded = file_theme(root)
    asked = a.theme or spec.get("theme")
    if asked:
        check_theme(asked, "--theme" if a.theme else "spec")
    if asked and asked != ftheme:
        raise SystemExit("%s is drawn in theme %s%s; the requested theme %s would mix themes in one file. "
                         "Drop --theme and the spec's theme to inherit %s." % (
                             a.into, ftheme, "" if recorded else " (no theme record: treated as colorful)", asked, ftheme))
    _, spec_accent = resolve_theme(spec, ftheme)
    if setting(spec, ACCENT_KEYS) is not None and spec_accent != faccent:
        raise SystemExit("%s records the accent %s%s; the spec's accent_color %s would put a second accent in the file. "
                         "Drop accent_color to inherit %s." % (
                             a.into, faccent, "" if recorded else " (no theme record: the default)", spec_accent, faccent))
    accent = faccent
    mc = fmc or DEFAULT_MAX_COLOURS
    if a.max_colours is not None:
        asked_mc = check_max_colours(a.max_colours, "--max-colours")
    else:
        asked_mc = check_max_colours(setting(spec, MAX_COLOURS_KEYS), "spec")
    if ftheme == "minimal" and asked_mc is not None and asked_mc != mc:
        raise SystemExit("%s was built with max_colours %d; the requested %s conflicts" % (a.into, mc, asked_mc))
    comps = []
    sheets = compile_spec(spec, ftheme, accent, mc, comps)
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
    pos = a.position or "first"
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
    # rebuild child order: elements before idx, new, rest (non-diagram children kept in place)
    children = list(root)
    dpos = [i for i, ch in enumerate(children) if ch.tag == "diagram"]
    insert_at = dpos[idx] if idx < len(dpos) else (dpos[-1] + 1 if dpos else len(children))
    for k, el in enumerate(new_elems):
        root.insert(insert_at + k, el)
    bak = a.into + ".bak"
    shutil.copyfile(a.into, bak)
    ET.ElementTree(root).write(a.into, encoding="utf-8", xml_declaration=False)
    print("theme %s (from the file%s), %d colours in the file: %s" % (
        ftheme, "" if recorded else ", no record: colorful", len(cols), " ".join(sorted(cols))))
    print("added %d sheet(s) to %s at position %s (backup: %s)" % (len(new_elems), a.into, pos, bak))


def cmd_list(a):
    root = read_file(a.file)
    print(theme_line(root)[0])
    for name, sid, v, e, lg in sheet_summary(root):
        print("%-28s id=%-12s vertices=%-4d legend=%-3d edges=%d" % (name, sid, v, lg, e))


def cmd_validate(a):
    root = read_file(a.file)
    problems = 0
    tl, theme, cols, mc = theme_line(root)
    print(tl)
    # the palette the file records: theme, accent and max_colours
    for p in palette_problems(theme, cols, file_theme(root)[1], mc or DEFAULT_MAX_COLOURS):
        print("ERR  " + p); problems += 1
    names = [d.get("name", "") for d in root.findall("diagram")]
    for nm in sorted(set(n for n in names if names.count(n) > 1)):
        print("ERR  %d sheets are named %r; before:NAME and after:NAME cannot tell them apart" % (names.count(nm), nm))
        problems += 1
    for d in root.findall("diagram"):
        name = d.get("name", "")
        model = decode_diagram(d)
        m = ET.fromstring(model)
        cells = m.findall(".//mxCell")
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
                    if x + w > pw + 1 or y + h > ph + 1 or x < 0 or y < 0:
                        print("warn %s: cell %s (%s) outside page %dx%d" % (name, c.get("id"), (c.get("value") or "")[:30], pw, ph))
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
                    print("warn %s: no legend cell; theme %s tells roles apart by shape only, so every sheet "
                          "from L1 down needs one" % (name, theme))
            else:
                print("warn %s: name has no L<n> prefix; ladder budget not checked" % name)
        print(line)
    print("problems:", problems)
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


def run_export(exe, src, out, fmt, page_index, scale):
    """page_index is 0-based here; draw.io >= 27.0.2 wants 1-based, so try that first."""
    for p in (page_index + 1, page_index):
        cmd = [exe, "-x", "-f", fmt, "-s", str(scale), "-p", str(p), "-o", out, src]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if r.returncode == 0 and os.path.exists(out):
            return True
        if "Invalid page index" not in (r.stdout or "") + (r.stderr or ""):
            msg = [ln for ln in ((r.stdout or "") + (r.stderr or "")).splitlines() if "disk_cache" not in ln and "gpu" not in ln.lower()]
            print("export failed (rc=%s): %s" % (r.returncode, " | ".join(msg[-3:])), file=sys.stderr)
            return False
    return False


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
    rc = 0
    for i, name in enumerate(names):
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
        out = os.path.join(outdir, "%s__%s.%s" % (base, safe, a.format))
        ok = run_export(exe, os.path.abspath(a.file), out, a.format, i, a.scale)
        print("%s %-28s -> %s" % ("ok  " if ok else "FAIL", name, out))
        rc = rc or (0 if ok else 1)
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
    ad = sub.add_parser("add"); ad.add_argument("spec"); ad.add_argument("--into", required=True); ad.add_argument("--position")
    ad.add_argument("--theme", help="must match the file's theme (the default is the file's theme)")
    ad.add_argument("--max-colours", "--max-colors", dest="max_colours", type=int, help="must match the file's max_colours")
    ls = sub.add_parser("list"); ls.add_argument("file")
    va = sub.add_parser("validate"); va.add_argument("file"); va.add_argument("--ladder", action="store_true")
    ex = sub.add_parser("export"); ex.add_argument("file"); ex.add_argument("--format", default="png"); ex.add_argument("--out"); ex.add_argument("--scale", default="1.5")
    a = p.parse_args()
    rc = {"build": cmd_build, "add": cmd_add, "list": cmd_list, "validate": cmd_validate, "export": cmd_export}[a.cmd](a)
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()

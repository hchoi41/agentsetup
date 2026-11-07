#!/usr/bin/env python3
"""drawio_spec.py: compile a JSON sheet spec into a draw.io (.drawio) file, add sheets to an
existing file, list, validate, and export (when a draw.io CLI is installed).

Usage
  python drawio_spec.py build    SPEC.json [-o OUT.drawio] [--force]
  python drawio_spec.py add      SPEC.json --into FILE.drawio [--position first|last|before:NAME|after:NAME]
  python drawio_spec.py list     FILE.drawio
  python drawio_spec.py validate FILE.drawio [--ladder]
  python drawio_spec.py export   FILE.drawio [--format png|svg|pdf] [--out DIR]

Spec format: see ../references/spec_format.md. Roles map to one fixed palette so every sheet of a
file reads the same way (who decides). Standard library only.
"""
import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import zlib
import xml.dom.minidom as minidom
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

ESC = {'"': '&quot;'}


def label(text):
    """draw.io labels are HTML (html=1): a newline in the spec becomes <br>, which must sit
    entity-encoded inside the XML attribute exactly as draw.io saves it."""
    return escape(text, ESC).replace("\n", "&lt;br&gt;")

# role -> (fill, stroke, dashed). One palette for every level so colour means the same thing on
# every sheet: who decides / what kind of thing it is.
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

# Fidelity ladder (sheet name prefix -> allowed vertex count). Edges are not counted.
LADDER = {
    "L0": (1, 4,   "one box: the task as one sentence; input/output arrows at most"),
    "L1": (8, 25,  "concept: actors, 3-6 components, main flows, one footnote"),
    "L2": (25, 50, "stages x layers: 3-6 columns, 3-6 lanes, no IDs"),
    "L3": (60, 130, "detail: every phase/gate/script/artifact by name"),
    "L4": (0, 10 ** 6, "implementation: files, functions, commands"),
}


# --------------------------------------------------------------------------- compile
class Compiler:
    def __init__(self, sheet):
        self.s = sheet
        self.sid = sheet["id"]
        self.cells = []
        self.ids = {}
        self.n = 0
        self.vertices = 0
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
        return int(x), int(y), int(w), int(h)

    def vertex(self, c):
        t = c.get("type", "box")
        x, y, w, h = self.geom(c)
        role = c.get("role", "plain")
        if role not in ROLES:
            raise SystemExit("unknown role %r in sheet %r" % (role, self.s["name"]))
        fill, stroke, dashed = ROLES[role]
        fill = c.get("fill", fill)
        stroke = c.get("stroke", stroke)
        dashed = c.get("dashed", dashed)
        font = c.get("font", 9)
        text = c.get("text", "")
        if t == "lane":
            fill = c.get("fill", LANE_FILLS.get(role, "#F5F5F5"))
            style = "rounded=0;whiteSpace=wrap;html=1;fontSize=%d;fillColor=%s;strokeColor=#BDBDBD;align=left;verticalAlign=top;spacingLeft=6;" % (font, fill)
            label = ""
            i = self.nid(c.get("id"))
            self.cells.append(self._v(i, label, style, x, y, w, h))
            if text:
                # lane title in a gutter on the left, vertically centred
                gw = c.get("gutter", 150)
                j = self.nid()
                tstyle = "text;html=1;whiteSpace=wrap;fontSize=%d;align=center;verticalAlign=middle;fontStyle=1;" % max(font, 10)
                self.cells.append(self._v(j, text, tstyle, x + 4, y, gw, h))
            return
        if t == "text":
            style = "text;html=1;whiteSpace=wrap;fontSize=%d;align=%s;verticalAlign=middle;" % (font, c.get("align", "left"))
            if c.get("bold", True):
                style += "fontStyle=1;"
            i = self.nid(c.get("id"))
            self.cells.append(self._v(i, text, style, x, y, w, h))
            return
        # box
        style = "rounded=%d;whiteSpace=wrap;html=1;fontSize=%d;fillColor=%s;strokeColor=%s;align=%s;verticalAlign=%s;spacing=4;" % (
            0 if c.get("square") else 1, font, fill, stroke, c.get("align", "center"), c.get("valign", "middle"))
        if c.get("bold"):
            style += "fontStyle=1;"
        if dashed:
            style += "dashed=1;"
        style += c.get("style", "")
        i = self.nid(c.get("id"))
        self.cells.append(self._v(i, text, style, x, y, w, h))
        self.check_fit(c, text, w, h, font)

    def check_fit(self, c, text, w, h, font):
        # rough capacity: chars per line ~ w / (0.5*font), lines ~ h / (1.35*font)
        cap = (w / (0.5 * font)) * (h / (1.35 * font))
        if text and len(text) > cap * 1.05:
            print("warn: sheet %r box %r text %d chars > est. capacity %d (w=%d h=%d font=%d)" % (
                self.s["name"], c.get("id", text[:30]), len(text), int(cap), w, h, font), file=sys.stderr)

    def _v(self, i, text, style, x, y, w, h):
        self.vertices += 1
        return '<mxCell id="%s" value="%s" style="%s" vertex="1" parent="1"><mxGeometry x="%d" y="%d" width="%d" height="%d" as="geometry"/></mxCell>' % (
            i, label(text), style, x, y, w, h)

    def edge(self, c):
        src = "%s_%s" % (self.sid, c["from"])
        dst = "%s_%s" % (self.sid, c["to"])
        for ref in (src, dst):
            if ref not in self.ids:
                raise SystemExit("edge in sheet %r refers to unknown cell %r" % (self.s["name"], ref.split("_", 1)[1]))
        role = c.get("role")
        color = c.get("color") or (ROLES[role][1] if role in ROLES else "#555555")
        style = "edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;fontSize=%d;strokeColor=%s;strokeWidth=%d;endArrow=block;endFill=1;" % (
            c.get("font", 8), color, c.get("width", 1))
        if c.get("dashed"):
            style += "dashed=1;"
        if "exit" in c:
            style += "exitX=%s;exitY=%s;" % tuple(c["exit"])
        if "entry" in c:
            style += "entryX=%s;entryY=%s;" % tuple(c["entry"])
        style += c.get("style", "")
        i = self.nid(c.get("id"))
        inner = ""
        if c.get("points"):
            inner += '<Array as="points">%s</Array>' % "".join('<mxPoint x="%d" y="%d"/>' % (int(px), int(py)) for px, py in c["points"])
        if c.get("label_offset"):
            inner += '<mxPoint x="%d" y="%d" as="offset"/>' % tuple(int(v) for v in c["label_offset"])
        # label_at: -1 = at the source, 0 = middle (default), 1 = at the target
        geom_x = ' x="%s"' % c["label_at"] if "label_at" in c else ""
        self.cells.append('<mxCell id="%s" value="%s" style="%s" edge="1" parent="1" source="%s" target="%s"><mxGeometry%s relative="1" as="geometry">%s</mxGeometry></mxCell>' % (
            i, label(c.get("label", "")), style, src, dst, geom_x, inner))

    def compile(self):
        # two passes so edges may be listed anywhere
        for c in self.s["cells"]:
            if c.get("type", "box") != "edge":
                self.vertex(c)
        for c in self.s["cells"]:
            if c.get("type") == "edge":
                self.edge(c)
        w, h = self.s.get("width", 1200), self.s.get("height", 800)
        return ('<diagram id="%s" name="%s"><mxGraphModel dx="1400" dy="800" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="%d" pageHeight="%d" math="0" shadow="0">'
                '<root><mxCell id="0"/><mxCell id="1" parent="0"/>%s</root></mxGraphModel></diagram>' % (
                    escape(self.sid, ESC), escape(self.s["name"], ESC), w, h, "".join(self.cells)))


def compile_spec(spec):
    out = []
    seen = set()
    for sh in spec["sheets"]:
        if sh["id"] in seen:
            raise SystemExit("duplicate sheet id %r" % sh["id"])
        seen.add(sh["id"])
        c = Compiler(sh)
        out.append((sh["name"], c.compile(), c.vertices))
    return out


def wrap_file(diagrams_xml):
    return '<mxfile host="app.diagrams.net" agent="drawio_spec.py" version="24.0">%s</mxfile>' % "".join(diagrams_xml)


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


def sheet_summary(root):
    rows = []
    for d in root.findall("diagram"):
        model = decode_diagram(d)
        m = ET.fromstring(model) if model else None
        verts = len(m.findall(".//mxCell[@vertex='1']")) if m is not None else 0
        edges = len(m.findall(".//mxCell[@edge='1']")) if m is not None else 0
        rows.append((d.get("name", ""), d.get("id", ""), verts, edges))
    return rows


# --------------------------------------------------------------------------- commands
def cmd_build(a):
    spec = json.load(open(a.spec, encoding="utf-8"))
    out = a.out or spec.get("file")
    if not out:
        raise SystemExit("no output path: pass -o or set spec['file']")
    if os.path.exists(out) and not a.force:
        raise SystemExit("%s exists; use 'add' to append sheets or --force to overwrite" % out)
    sheets = compile_spec(spec)
    doc = wrap_file([x for _, x, _ in sheets])
    minidom.parseString(doc)
    with open(out, "w", encoding="utf-8") as f:
        f.write(doc)
    for name, _, v in sheets:
        print("sheet %-28s vertices=%d" % (name, v))
    print("wrote", out)


def cmd_add(a):
    spec = json.load(open(a.spec, encoding="utf-8"))
    root = read_file(a.into)
    existing = [d.get("name") for d in root.findall("diagram")]
    existing_ids = [d.get("id") for d in root.findall("diagram")]
    sheets = compile_spec(spec)
    new_elems = []
    for name, x, v in sheets:
        if name in existing:
            raise SystemExit("sheet %r already exists in %s; existing sheets are never rewritten" % (name, a.into))
        el = ET.fromstring(x)
        if el.get("id") in existing_ids:
            raise SystemExit("sheet id %r already used in %s" % (el.get("id"), a.into))
        new_elems.append(el)
        print("sheet %-28s vertices=%d" % (name, v))
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
    # rebuild child order: elements before idx, new, rest (non-diagram children kept in place)
    children = list(root)
    dpos = [i for i, ch in enumerate(children) if ch.tag == "diagram"]
    insert_at = dpos[idx] if idx < len(dpos) else (dpos[-1] + 1 if dpos else len(children))
    for k, el in enumerate(new_elems):
        root.insert(insert_at + k, el)
    bak = a.into + ".bak"
    shutil.copyfile(a.into, bak)
    ET.ElementTree(root).write(a.into, encoding="utf-8", xml_declaration=False)
    print("added %d sheet(s) to %s at position %s (backup: %s)" % (len(new_elems), a.into, pos, bak))


def cmd_list(a):
    root = read_file(a.file)
    for name, sid, v, e in sheet_summary(root):
        print("%-28s id=%-12s vertices=%-4d edges=%d" % (name, sid, v, e))


def cmd_validate(a):
    root = read_file(a.file)
    problems = 0
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
        pw, ph = int(m.get("pageWidth", 0) or 0), int(m.get("pageHeight", 0) or 0)
        verts = 0
        for c in cells:
            if c.get("edge") == "1":
                for k in ("source", "target"):
                    if c.get(k) not in ids:
                        print("ERR  %s: edge %s has dangling %s=%s" % (name, c.get("id"), k, c.get(k))); problems += 1
            elif c.get("vertex") == "1":
                verts += 1
                g = c.find("mxGeometry")
                if g is not None and pw and ph:
                    x, y = float(g.get("x", 0)), float(g.get("y", 0))
                    w, h = float(g.get("width", 0)), float(g.get("height", 0))
                    if x + w > pw + 1 or y + h > ph + 1 or x < 0 or y < 0:
                        print("warn %s: cell %s (%s) outside page %dx%d" % (name, c.get("id"), (c.get("value") or "")[:30], pw, ph))
        line = "ok   %-28s vertices=%d" % (name, verts)
        if a.ladder:
            mm = re.match(r"(L\d)", name)
            if mm and mm.group(1) in LADDER:
                lo, hi, desc = LADDER[mm.group(1)]
                if not (lo <= verts <= hi):
                    print("ERR  %s: %d vertices outside %s budget %d-%d (%s)" % (name, verts, mm.group(1), lo, hi, desc)); problems += 1
                else:
                    line += "  within %s budget %d-%d" % (mm.group(1), lo, hi)
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
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("spec"); b.add_argument("-o", "--out"); b.add_argument("--force", action="store_true")
    ad = sub.add_parser("add"); ad.add_argument("spec"); ad.add_argument("--into", required=True); ad.add_argument("--position")
    ls = sub.add_parser("list"); ls.add_argument("file")
    va = sub.add_parser("validate"); va.add_argument("file"); va.add_argument("--ladder", action="store_true")
    ex = sub.add_parser("export"); ex.add_argument("file"); ex.add_argument("--format", default="png"); ex.add_argument("--out"); ex.add_argument("--scale", default="1.5")
    a = p.parse_args()
    rc = {"build": cmd_build, "add": cmd_add, "list": cmd_list, "validate": cmd_validate, "export": cmd_export}[a.cmd](a)
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()

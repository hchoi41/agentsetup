"""Tests for drawio_spec.py themes, shapes and the generated legend. Run: pytest scripts/test_drawio_spec.py"""
import json
import os
import re
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import drawio_spec as ds  # noqa: E402

SCRIPT = os.path.join(HERE, "drawio_spec.py")

# The palette and box style of the script before themes existed (the colorful baseline).
OLD_ROLES = {
    "human": ("#FFF2CC", "#D6B656", False), "propose": ("#DAE8FC", "#6C8EBF", False),
    "decide": ("#D5E8D4", "#82B366", False), "guard": ("#FFE6CC", "#D79B00", False),
    "outside": ("#E1D5E7", "#9673A6", False), "record": ("#FFFFFF", "#82B366", True),
    "note": ("#FFFFFF", "#9E9E9E", False), "stage": ("#DAE8FC", "#6C8EBF", False),
    "plain": ("#F5F5F5", "#9E9E9E", False),
}


def old_box_style(role, font=9, align="center", valign="middle"):
    fill, stroke, dashed = OLD_ROLES[role]
    s = "rounded=1;whiteSpace=wrap;html=1;fontSize=%d;fillColor=%s;strokeColor=%s;align=%s;verticalAlign=%s;spacing=4;" % (
        font, fill, stroke, align, valign)
    return s + ("dashed=1;" if dashed else "")


def one_box_per_role(**top):
    # the lane is taller than wide, so in minimal and bw it is a column lane: boxes start below its title strip
    cells = [{"id": r, "role": r, "text": r, "x": 10, "y": 30 + 70 * k, "w": 120, "h": 60}
             for k, r in enumerate(ds.ROLES)]
    cells.append({"type": "lane", "x": 0, "y": 0, "w": 400, "h": 700, "text": "lane", "role": "human"})
    cells.append({"type": "edge", "from": "human", "to": "decide", "role": "decide"})
    spec = {"sheets": [{"id": "s", "name": "L1 Test", "width": 600, "height": 900, "cells": cells}]}
    spec.update(top)
    return spec


def styles(xml):
    return {m.group(1): m.group(2) for m in re.finditer(r'<mxCell id="s_([^"]+)" value="[^"]*" style="([^"]*)"', xml)}


def build(spec, tmp_path, *args):
    p = tmp_path / "spec.json"
    p.write_text(json.dumps(spec), encoding="utf-8")
    out = tmp_path / "out.drawio"
    r = subprocess.run([sys.executable, SCRIPT, "build", str(p), "-o", str(out), "--force"] + list(args),
                       capture_output=True, text=True)
    return r, out


def test_default_theme_is_minimal():
    assert ds.resolve_theme({})[0] == "minimal"
    (_, xml, _), = ds.compile_spec(one_box_per_role())
    st = styles(xml)
    assert st["human"].startswith("shape=manualInput;")
    assert "fillColor=#FFFFFF;strokeColor=#000000;" in st["human"]
    assert "shape=document;" in st["record"]


def test_cli_theme_overrides_spec():
    (_, xml, _), = ds.compile_spec(one_box_per_role(theme="bw"), "colorful")
    assert styles(xml)["human"] == old_box_style("human")


def test_colorful_matches_baseline_styles_for_every_role():
    (_, xml, _), = ds.compile_spec(one_box_per_role(theme="colorful"))
    st = styles(xml)
    for role in ds.ROLES:
        assert st[role] == old_box_style(role), role
    assert "strokeColor=#82B366;" in [v for k, v in st.items() if k.startswith("auto") and "edgeStyle" in v][0]


def test_bw_emits_only_black_and_white(tmp_path, capsys):
    spec = one_box_per_role(theme="bw")
    spec["sheets"][0]["cells"][0]["fill"] = "#FF0000"
    spec["sheets"][0]["cells"].append({"type": "edge", "from": "plain", "to": "note", "color": "#00FF00", "accent": True})
    spec["sheets"][0]["cells"].append({"type": "legend", "x": 300, "y": 10})
    (_, xml, _), = ds.compile_spec(spec)
    assert ds.emitted_colours(xml) == {"#000000", "#FFFFFF"}
    assert "ignored in the bw theme" in capsys.readouterr().err
    r, out = build(one_box_per_role(theme="bw"), tmp_path)
    assert r.returncode == 0 and out.exists()


def test_minimal_palette_and_accent():
    spec = one_box_per_role()
    (_, xml, _), = ds.compile_spec(spec)
    assert ds.emitted_colours(xml) == {"#000000", "#FFFFFF", "#F2F2F2"}
    spec["sheets"][0]["cells"][0]["accent"] = True
    (_, xml, _), = ds.compile_spec(spec)
    assert ds.emitted_colours(xml) == {"#000000", "#FFFFFF", "#F2F2F2", "#1F5FBF"}


def test_minimal_ignores_an_override_outside_its_palette(tmp_path):
    spec = one_box_per_role()
    spec["sheets"][0]["cells"][0]["accent"] = True
    spec["sheets"][0]["cells"][1]["fill"] = "#FFF2CC"            # outside the palette: ignored
    spec["sheets"][0]["cells"].append({"type": "edge", "from": "plain", "to": "note", "color": "#00AA00"})
    r, out = build(spec, tmp_path)
    assert r.returncode == 0, r.stderr
    assert "fill='#FFF2CC' is outside the palette" in r.stderr and "color='#00AA00' is outside the palette" in r.stderr
    assert "4 colours: #000000 #1F5FBF #F2F2F2 #FFFFFF" in r.stdout


def test_five_colours_still_stop_the_run_as_a_guard():
    cols = {"#000000", "#FFFFFF", "#F2F2F2", "#1F5FBF", "#FFF2CC"}
    with pytest.raises(SystemExit, match="fewer than 5"):
        ds.check_palette("minimal", "", cols=cols)


def test_bw_fails_on_colour_in_style_string(tmp_path):
    spec = one_box_per_role(theme="bw")
    spec["sheets"][0]["cells"][0]["style"] = "fontColor=#FF0000;"
    r, out = build(spec, tmp_path)
    assert r.returncode != 0 and not out.exists() and "#FF0000" in r.stderr


def test_unknown_theme_error(tmp_path):
    with pytest.raises(SystemExit, match="unknown theme 'neon'.*minimal, colorful, bw"):
        ds.compile_spec(one_box_per_role(theme="neon"))
    r, _ = build(one_box_per_role(), tmp_path, "--theme", "neon")
    assert r.returncode != 0 and "unknown theme" in r.stderr


def test_unknown_shape_error():
    spec = one_box_per_role()
    spec["sheets"][0]["cells"][0]["shape"] = "blob"
    with pytest.raises(SystemExit, match="unknown shape 'blob'.*database"):
        ds.compile_spec(spec)


def test_catalogue_shape_overrides_role_shape_and_keeps_colorful_colours():
    spec = one_box_per_role()
    spec["sheets"][0]["cells"][0]["shape"] = "database"
    (_, xml, _), = ds.compile_spec(spec)
    assert styles(xml)["human"].startswith("shape=cylinder3;")
    spec["theme"] = "colorful"
    (_, xml, _), = ds.compile_spec(spec)
    st = styles(xml)["human"]
    assert st.startswith("shape=cylinder3;") and "fillColor=#FFF2CC;strokeColor=#D6B656;" in st


def test_frame_stays_square_rectangle():
    spec = one_box_per_role()
    spec["sheets"][0]["cells"][0]["square"] = True
    (_, xml, _), = ds.compile_spec(spec)
    st = styles(xml)["human"]
    assert st.startswith("rounded=0;") and "fillColor=#F2F2F2;" in st


@pytest.mark.parametrize("theme", ds.THEMES)
def test_legend_not_counted(theme, tmp_path):
    spec = one_box_per_role(theme=theme)
    (_, _, v0), = ds.compile_spec(spec)
    spec["sheets"][0]["cells"].append({"type": "legend", "x": 300, "y": 10, "labels": {"human": "Max decides"}})
    (_, xml, v1), = ds.compile_spec(spec)
    assert v0 == v1
    # one frame (it carries the title) plus one cell per row; colorful merges stage and propose,
    # which share one look
    rows = len(ds.ROLES) - (1 if theme == "colorful" else 0)
    assert "Max decides" in xml and xml.count(ds.LEGEND_KEY) == 1 + rows
    r, out = build(spec, tmp_path)
    assert r.returncode == 0
    val = subprocess.run([sys.executable, SCRIPT, "validate", str(out), "--ladder"], capture_output=True, text=True)
    assert "vertices=%d legend=%d" % (v0, 1 + rows) in val.stdout
    lst = subprocess.run([sys.executable, SCRIPT, "list", str(out)], capture_output=True, text=True)
    assert "vertices=%-4d legend=%-3d" % (v0, 1 + rows) in lst.stdout


def test_legend_lists_only_roles_used():
    cells = [{"id": "a", "role": "human", "text": "a", "x": 0, "y": 0},
             {"id": "b", "role": "plain", "shape": "database", "text": "b", "x": 0, "y": 80},
             {"type": "legend", "x": 300, "y": 0}]
    spec = {"sheets": [{"id": "s", "name": "L1 T", "cells": cells}]}
    (_, xml, _), = ds.compile_spec(spec)
    assert "a person decides" in xml and 'value="database: component"' in xml and "code decides" not in xml


# --------------------------------------------------------------------------- colorful = the old script, whole file
REFS = os.path.join(os.path.dirname(HERE), "references")
COLORFUL_DIGESTS = {   # SHA-256 of the builds made by the script before themes existed
    "example_L1_overview.json": "9816c035de277ccf26907428977b39ef20876aa23161df7e79491083c55d2008",
    "example_plugin_3_levels.json": "d9c146e01996e287f0da4a6392803f0aeb1bf89e8c08470816c6346030ca136d",
}


@pytest.mark.parametrize("name", sorted(COLORFUL_DIGESTS))
def test_colorful_examples_byte_identical_to_pre_theme_build(name, tmp_path):
    import hashlib
    out = tmp_path / "out.drawio"
    r = subprocess.run([sys.executable, SCRIPT, "build", os.path.join(REFS, name), "-o", str(out), "--force", "--theme", "colorful"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert hashlib.sha256(out.read_bytes()).hexdigest() == COLORFUL_DIGESTS[name]


# --------------------------------------------------------------------------- colour budget
def test_max_colours_3_draws_accent_in_black_and_ignores_a_fourth(tmp_path):
    spec = one_box_per_role(max_colours=3)
    spec["sheets"][0]["cells"][0]["accent"] = True
    (_, xml, _), = ds.compile_spec(spec)
    assert ds.emitted_colours(xml) == {"#000000", "#FFFFFF", "#F2F2F2"}
    assert "strokeWidth=4;" in styles(xml)["human"]
    spec["sheets"][0]["cells"][1]["fill"] = "#1F5FBF"   # the accent colour is not in the 3-colour palette
    r, out = build(spec, tmp_path)
    assert r.returncode == 0 and "is outside the palette" in r.stderr and "3 colours" in r.stdout
    spec["sheets"][0]["cells"][1]["style"] = "fillColor=#1F5FBF;"
    r, out = build(spec, tmp_path)
    assert r.returncode != 0 and "#1F5FBF" in r.stderr
    r, out = build(one_box_per_role(), tmp_path, "--max-colours", "3")
    assert r.returncode == 0 and "max_colours 3" in r.stdout


def test_three_colour_accent_is_black_after_every_override():
    spec = one_box_per_role(max_colours=3)
    cells = {c.get("id"): c for c in spec["sheets"][0]["cells"]}
    cells["plain"].update({"accent": True, "stroke": "#FFFFFF", "style": "strokeColor=#F2F2F2;strokeWidth=1;"})
    spec["sheets"][0]["cells"] += [{"type": "edge", "id": "e", "from": "plain", "to": "note", "accent": True,
                                    "color": "#F2F2F2", "style": "strokeColor=#FFFFFF;"},
                                   {"type": "legend", "x": 300, "y": 10}]
    (_, xml, _), = ds.compile_spec(spec)
    st = styles(xml)
    for k in ("plain", "e"):
        d, _ = ds.parse_style(st[k])
        assert (d["strokeColor"], d["strokeWidth"]) == ("#000000", "4"), k
        assert st[k].count("strokeColor=") == 1, k
    sample = re.search(r'value="highlighted" style="([^"]*)"', xml).group(1)
    assert "strokeColor=#000000;strokeWidth=4;" in sample


@pytest.mark.parametrize("value", [2, 5])
def test_max_colours_must_be_3_or_4(value, tmp_path):
    with pytest.raises(SystemExit, match="not 3 or 4"):
        ds.compile_spec(one_box_per_role(max_colours=value))
    r, out = build(one_box_per_role(), tmp_path, "--max-colours", str(value))
    assert r.returncode != 0 and not out.exists()


def test_default_build_reports_three_colours_and_records_the_theme(tmp_path):
    r, out = build(one_box_per_role(), tmp_path)
    assert r.returncode == 0 and "theme minimal (max_colours 4), 3 colours" in r.stdout
    assert 'dsTheme="minimal" dsAccent="#1F5FBF" dsMaxColours="4"' in out.read_text(encoding="utf-8")


@pytest.mark.parametrize("theme", ["bw", "minimal"])
@pytest.mark.parametrize("style", ["blue;", "gradientColor=#000000;", "shadow=1;", "opacity=50;"])
def test_style_strings_that_add_paint_are_rejected(theme, style, tmp_path):
    spec = one_box_per_role(theme=theme)
    spec["sheets"][0]["cells"][0]["style"] = style
    r, out = build(spec, tmp_path)
    assert r.returncode != 0 and not out.exists()
    assert style.split("=")[0].rstrip(";") in r.stderr


def test_colour_audit_reads_styles_as_drawio_does():
    # a later fillColor wins; named black and white are the same colours as #000000 and #FFFFFF
    spec = one_box_per_role()
    spec["sheets"][0]["cells"][0].update({"fill": "#FF0000", "style": "fillColor=#fff;"})
    (_, xml, _), = ds.compile_spec(spec)
    assert ds.emitted_colours(xml) == {"#000000", "#FFFFFF", "#F2F2F2"}
    spec = one_box_per_role(theme="bw")
    spec["sheets"][0]["cells"][0]["style"] = "fontColor=black;strokeColor=white;"
    (_, xml, _), = ds.compile_spec(spec)
    assert ds.emitted_colours(xml) == {"#000000", "#FFFFFF"}
    ds.check_palette("bw", xml)


# --------------------------------------------------------------------------- add keeps the file's theme
def add(spec, into, tmp_path, *args):
    p = tmp_path / "add.json"
    p.write_text(json.dumps(spec), encoding="utf-8")
    return subprocess.run([sys.executable, SCRIPT, "add", str(p), "--into", str(into), "--position", "last"] + list(args),
                          capture_output=True, text=True)


def second_sheet(**top):
    spec = one_box_per_role(**top)
    spec["sheets"][0].update({"id": "t", "name": "L2 More"})
    return spec


def test_add_inherits_the_file_theme(tmp_path):
    r, out = build(one_box_per_role(theme="bw"), tmp_path)
    r = add(second_sheet(), out, tmp_path)
    assert r.returncode == 0, r.stderr
    assert "theme bw (from the file)" in r.stdout
    assert ds.emitted_colours(out.read_text(encoding="utf-8")) == {"#000000", "#FFFFFF"}


def test_add_with_conflicting_theme_leaves_the_file_untouched(tmp_path):
    r, out = build(one_box_per_role(), tmp_path)
    before = out.read_bytes()
    r = add(second_sheet(theme="colorful"), out, tmp_path)
    assert r.returncode != 0 and "mix themes" in r.stderr
    r = add(second_sheet(), out, tmp_path, "--theme", "bw")
    assert r.returncode != 0 and out.read_bytes() == before


def test_add_with_a_conflicting_accent_stops_and_leaves_the_file_untouched(tmp_path):
    spec = one_box_per_role(accent_color="#123456")
    spec["sheets"][0]["cells"][0]["accent"] = True          # file: 4 colours
    r, out = build(spec, tmp_path)
    before = out.read_bytes()
    more = second_sheet(accent_color="#654321")             # would fit a 5-colour budget, still refused
    more["sheets"][0]["cells"][0]["accent"] = True
    r = add(more, out, tmp_path)
    assert r.returncode != 0 and "#123456" in r.stderr and "second accent" in r.stderr
    assert out.read_bytes() == before
    more.pop("accent_color")                                 # no accent_color: the file's is used
    r = add(more, out, tmp_path)
    assert r.returncode == 0, r.stderr
    assert ds.emitted_colours(out.read_text(encoding="utf-8")) == {"#000000", "#FFFFFF", "#F2F2F2", "#123456"}


def inject_style(path, cell_id, extra):
    """Hand-edit a built file: append style text to one cell (as editing in draw.io would)."""
    text = path.read_text(encoding="utf-8")
    new = re.sub(r'(<mxCell id="%s" value="[^"]*" style="[^"]*)' % re.escape(cell_id), lambda m: m.group(1) + extra, text, count=1)
    assert new != text
    path.write_text(new, encoding="utf-8")


def test_existing_file_colours_are_read_from_the_xml_even_with_an_apostrophe(tmp_path):
    r, out = build(one_box_per_role(), tmp_path)
    inject_style(out, "s_plain", "fontFamily=Bob&apos;s Font;fontColor=#DEAD00;")
    root = ds.read_file(str(out))
    assert "#DEAD00" in ds.file_colours(root)[0]
    val = subprocess.run([sys.executable, SCRIPT, "validate", str(out)], capture_output=True, text=True)
    assert val.returncode == 1 and "#DEAD00" in val.stdout and "ERR" in val.stdout
    before = out.read_bytes()
    more = second_sheet()
    more["sheets"][0]["cells"][0]["accent"] = True
    r = add(more, out, tmp_path)
    assert r.returncode != 0 and "#DEAD00" in r.stderr and out.read_bytes() == before


def test_validate_applies_the_recorded_max_colours(tmp_path):
    r, out = build(one_box_per_role(max_colours=3), tmp_path)
    val = subprocess.run([sys.executable, SCRIPT, "validate", str(out)], capture_output=True, text=True)
    assert val.returncode == 0 and "problems: 0" in val.stdout
    inject_style(out, "s_plain", "fontColor=#1F5FBF;")      # the accent colour, but the file records 3
    val = subprocess.run([sys.executable, SCRIPT, "validate", str(out)], capture_output=True, text=True)
    assert val.returncode == 1 and "max_colours 3" in val.stdout and "#1F5FBF" in val.stdout


def test_add_to_a_file_without_theme_record_is_colorful(tmp_path):
    r, out = build(one_box_per_role(theme="colorful"), tmp_path)
    assert "dsTheme" not in out.read_text(encoding="utf-8")
    r = add(second_sheet(), out, tmp_path)
    assert r.returncode == 0 and "theme colorful (from the file, no record: colorful)" in r.stdout
    assert old_box_style("human") in out.read_text(encoding="utf-8").split('name="L2 More"')[1]
    r = add(second_sheet(theme="minimal"), out, tmp_path)
    assert r.returncode != 0 and "treated as colorful" in r.stderr


def test_list_and_validate_print_theme_and_colours(tmp_path):
    r, out = build(one_box_per_role(), tmp_path)
    for cmd in ("list", "validate"):
        o = subprocess.run([sys.executable, SCRIPT, cmd, str(out)], capture_output=True, text=True).stdout
        assert "theme minimal, max_colours 4; 3 colours: #000000 #F2F2F2 #FFFFFF" in o


# --------------------------------------------------------------------------- legend
def legend_rows(xml):
    return [m.group(1) for m in re.finditer(r'value="([^"]*)" style="[^"]*labelPosition=right;[^"]*dsLegend=1;', xml)]


def test_legend_sample_uses_the_effective_paint_and_warns_on_mixed_paint(capsys):
    cells = [{"id": "a", "role": "guard", "text": "a", "x": 0, "y": 0, "fill": "#FF0000", "stroke": "#00FF00"},
             {"id": "b", "role": "guard", "text": "b", "x": 0, "y": 80},
             {"type": "legend", "x": 300, "y": 0, "labels": {"guard": "special red"}}]
    (_, xml, _), = ds.compile_spec({"theme": "colorful", "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    row = [m.group(0) for m in re.finditer(r'<mxCell[^>]*value="special red"[^>]*>', xml)]
    assert len(row) == 2 and "fillColor=#FF0000;strokeColor=#00FF00;" in "".join(row)
    assert "painted 2 different ways" in capsys.readouterr().err


def test_legend_merges_one_look_with_two_meanings():
    cells = [{"id": "a", "role": "record", "text": "a", "x": 0, "y": 0},
             {"id": "b", "role": "plain", "shape": "document", "text": "b", "x": 0, "y": 80},
             {"type": "legend", "x": 300, "y": 0, "labels": {"record": "audit evidence", "document": "customer input"}}]
    (_, xml, _), = ds.compile_spec({"sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    assert legend_rows(xml) == ["audit evidence; customer input: component"]


def test_legend_rows_grow_with_font_and_label_and_narrow_width_fails():
    cells = [{"id": "a", "role": "record", "text": "a", "x": 0, "y": 0},
             {"type": "legend", "id": "lg", "x": 300, "y": 0, "w": 160, "font": 14,
              "labels": {"record": "Audit log retained for forty business days"}}]
    spec = {"sheets": [{"id": "s", "name": "L1 T", "cells": cells}]}
    (_, xml, _), = ds.compile_spec(spec)
    frame_h = int(re.search(r'id="s_lg".*?height="(\d+)"', xml).group(1))
    assert frame_h > 28 + 26 + 6   # taller than the old fixed title and one 26 px row
    cells[1]["w"] = 100
    with pytest.raises(SystemExit, match="leaves .* px for labels"):
        ds.compile_spec(spec)


# --------------------------------------------------------------------------- shapes and text
def test_terminator_and_outline_shapes_use_their_outline_as_perimeter():
    assert "roundedPerimeter=1;" in ds.SHAPES["terminator"][0]
    for name in ("document", "documents", "manual_input", "stored_data", "delay", "offpage", "database", "note"):
        assert "perimeter=" in ds.SHAPES[name][0], name
    for name in ("manual_operation", "documents", "stored_data", "delay", "offpage"):
        assert name in ds.SHAPE_MEANINGS


@pytest.mark.parametrize("shape", ["decision", "cloud"])
def test_decision_and_cloud_centre_their_text_and_warn(shape, capsys):
    spec = one_box_per_role()
    plain = [c for c in spec["sheets"][0]["cells"] if c.get("id") == "plain"][0]
    plain.update({"shape": shape, "text": "Ready?\nmore", "align": "left", "valign": "top"})
    (_, xml, _), = ds.compile_spec(spec)
    assert "align=center;verticalAlign=middle;" in styles(xml)["plain"]
    err = capsys.readouterr().err
    assert "centres its text" in err and "one short line" in err


def test_shape_that_hides_a_role_warns(capsys):
    spec = one_box_per_role()
    cells = {c.get("id"): c for c in spec["sheets"][0]["cells"]}
    cells["human"]["shape"] = "database"
    cells["plain"]["shape"] = "database"     # plain: no warning
    ds.compile_spec(spec)
    err = capsys.readouterr().err
    assert "hides role 'human'" in err and "hides role 'plain'" not in err


def test_auto_height_and_wrapped_line_warning(capsys):
    cells = [{"id": "a", "role": "human", "text": "Head\n• one\n• two\n• three", "x": 0, "y": 0, "w": 200, "h": "auto",
              "font": 10, "align": "left", "valign": "top"},
             {"id": "b", "role": "plain", "text": "Head\n• " + "word " * 40, "x": 0, "y": 200, "w": 200, "h": 40}]
    (_, xml, _), = ds.compile_spec({"sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    h = int(re.search(r'id="s_a".*?height="(\d+)"', xml).group(1))
    assert h == 76    # 4 lines x 1.35 x 10 + 12, plus manual_input's 10 px allowance
    out = capsys.readouterr()
    assert "auto h: sheet 'L1 T' box 'a' h=76 (4 lines, font 10)" in out.out
    assert "box 'b' text clips" in out.err


def test_headline_bold_in_minimal_and_bw_only():
    spec = one_box_per_role()
    cells = {c.get("id"): c for c in spec["sheets"][0]["cells"]}
    cells["human"]["text"] = "Head\n• one"
    cells["propose"].update({"text": "Head\n• one", "headline": False})
    (_, xml, _), = ds.compile_spec(spec)
    assert 'value="&lt;b&gt;Head&lt;/b&gt;&lt;br&gt;' in xml.split('id="s_human"')[1][:60]
    assert "&lt;b&gt;" not in xml.split('id="s_propose"')[1][:60]
    (_, xml, _), = ds.compile_spec(spec, "colorful")
    assert "&lt;b&gt;" not in xml
    cells["decide"].update({"text": "Set of 3", "square": True})    # a frame's one-line headline
    cells["guard"]["text"] = "one line"                              # a one-line box stays plain
    (_, xml, _), = ds.compile_spec(spec)
    assert 'value="&lt;b&gt;Set of 3&lt;/b&gt;"' in xml and 'value="one line"' in xml


def test_bw_frames_are_dashed():
    spec = one_box_per_role(theme="bw")
    spec["sheets"][0]["cells"][0]["square"] = True
    (_, xml, _), = ds.compile_spec(spec)
    assert "dashed=1;" in styles(xml)["human"]
    (_, xml, _), = ds.compile_spec(spec, "minimal")
    assert "dashed=1;" not in styles(xml)["human"]


def test_palette_word_without_legend_warns(capsys):
    spec = one_box_per_role()
    spec["sheets"][0]["cells"][0]["text"] = "yellow = a person decides"
    ds.compile_spec(spec)
    assert "names the colour 'yellow'" in capsys.readouterr().err
    spec["sheets"][0]["cells"].append({"type": "legend", "x": 300, "y": 10})
    ds.compile_spec(spec)
    assert "names the colour" not in capsys.readouterr().err


# --------------------------------------------------------------------------- containment and overlaps
def nested_spec(theme):
    cells = [{"type": "lane", "id": "lane", "x": 0, "y": 0, "w": 700, "h": 300, "text": "Lane"},
             {"id": "frame", "square": True, "text": "Set", "x": 200, "y": 20, "w": 300, "h": 200},
             {"id": "kid", "text": "kid", "x": 220, "y": 60, "w": 100, "h": 40},
             {"id": "free", "text": "free", "x": 10, "y": 320, "w": 100, "h": 40}]
    return {"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "width": 800, "height": 400, "cells": cells}]}


def test_frames_and_lanes_are_containers_in_minimal_and_bw():
    for theme in ("minimal", "bw"):
        (_, xml, v), = ds.compile_spec(nested_spec(theme))
        assert v == 4   # lane (one swimlane, its title is its label), frame, kid, free
        assert re.search(r'id="s_lane" value="Lane" style="swimlane;[^"]*container=1;collapsible=0;', xml)
        assert re.search(r'id="s_frame"[^>]*container=1;collapsible=0;[^>]*parent="s_lane"><mxGeometry x="200" y="20"', xml)
        assert re.search(r'id="s_kid"[^>]*parent="s_frame"><mxGeometry x="20" y="40"', xml)
        assert re.search(r'id="s_free"[^>]*parent="1"><mxGeometry x="10" y="320"', xml)
    (_, xml, _), = ds.compile_spec(nested_spec("colorful"))
    assert "container=1" not in xml and 'parent="s_' not in xml


def test_validate_resolves_nested_geometry(tmp_path):
    spec = nested_spec("minimal")
    r, out = build(spec, tmp_path)
    val = subprocess.run([sys.executable, SCRIPT, "validate", str(out), "--ladder"], capture_output=True, text=True)
    assert "outside page" not in val.stdout
    spec["sheets"][0]["width"] = 310     # kid ends at x 320 absolute (120 relative to its frame)
    r, out = build(spec, tmp_path)
    val = subprocess.run([sys.executable, SCRIPT, "validate", str(out)], capture_output=True, text=True)
    assert "cell s_kid (kid) outside page" in val.stdout


def test_partial_overlap_warns_and_containment_does_not(capsys):
    spec = nested_spec("minimal")
    spec["sheets"][0]["cells"].append({"id": "clash", "text": "clash", "x": 480, "y": 200, "w": 100, "h": 60})
    ds.compile_spec(spec)
    err = capsys.readouterr().err
    assert "frame and clash overlap" in err
    assert "lane and kid" not in err and "frame and kid" not in err and "lane and frame" not in err


# --------------------------------------------------------------------------- style allow-list (minimal, bw)
@pytest.mark.parametrize("theme", ["bw", "minimal"])
@pytest.mark.parametrize("style,named", [
    ("fillColor=#E03131; fillColor=#FFFFFF;", "#E03131"),          # whitespace duplicate key
    ("glass=1;", "glass"),
    ("shape=image;image=data:image/svg+xml,abc;", "image"),
    ("shape=mxgraph.aws4.lambda;", "mxgraph.aws4.lambda"),          # a stencil with its own paint
    ("swimlane;", "swimlane"),                                       # a named bare style
    ("sketch=1;", "sketch"),
])
def test_style_allow_list_stops_unlisted_keys(theme, style, named, tmp_path):
    spec = one_box_per_role(theme=theme)
    spec["sheets"][0]["cells"][0]["style"] = style
    r, out = build(spec, tmp_path)
    assert r.returncode != 0 and not out.exists()
    assert named in r.stderr


def test_style_is_written_normalised_and_catalogue_shapes_pass():
    spec = one_box_per_role(theme="bw")
    cells = {c.get("id"): c for c in spec["sheets"][0]["cells"]}
    cells["plain"]["style"] = " fontColor = #000000 ;  spacingTop=4 ;shape=cylinder3;"
    (_, xml, _), = ds.compile_spec(spec)
    st = styles(xml)["plain"]
    assert st.endswith("fontColor=#000000;spacingTop=4;shape=cylinder3;")
    assert " fontColor" not in st


def test_legend_shows_the_drawn_shape():
    # minimal: a shape named in 'style' is the shape the legend shows
    cells = [{"id": "d", "role": "decide", "text": "d", "x": 0, "y": 0, "style": "shape=cloud;"},
             {"type": "legend", "x": 300, "y": 0}]
    (_, xml, _), = ds.compile_spec({"sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    assert styles(xml)["d"].startswith("shape=cloud;")
    assert re.search(r'value="network or cloud service" style="shape=cloud;', xml)
    assert "predefined" not in "".join(legend_rows(xml))
    # colorful: a frame drops the shape it names, and the legend has no row for it
    cells = [{"id": "f", "role": "record", "square": True, "shape": "actor", "text": "set", "x": 0, "y": 0, "w": 200, "h": 100},
             {"type": "legend", "x": 300, "y": 0}]
    (_, xml, _), = ds.compile_spec({"theme": "colorful", "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    assert "umlActor" not in xml and "person" not in xml


def test_legend_groups_one_paint_spelled_two_ways():
    cells = [{"id": "a", "role": "plain", "text": "a", "x": 0, "y": 0, "fill": "#FFF"},
             {"id": "b", "role": "plain", "text": "b", "x": 0, "y": 80, "fill": "white"},
             {"id": "c", "role": "plain", "text": "c", "x": 0, "y": 160},
             {"type": "legend", "x": 300, "y": 0}]
    (_, xml, _), = ds.compile_spec({"sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    assert legend_rows(xml) == ["component"]


# --------------------------------------------------------------------------- literal text
@pytest.mark.parametrize("theme", ["minimal", "bw"])
def test_markup_and_entities_in_text_stay_literal(theme, tmp_path):
    cells = [{"id": "a", "role": "plain", "text": "Render <input> & type &copy;\n<span style='color:red'>x</span>",
              "x": 0, "y": 0, "w": 300, "h": 80},
             {"id": "b", "role": "plain", "text": "b", "x": 0, "y": 200},
             {"type": "edge", "id": "e", "from": "a", "to": "b", "label": "<b>not bold</b>"},
             {"type": "legend", "x": 400, "y": 0, "labels": {"plain": "a <i>component</i>"}}]
    spec = {"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "width": 800, "height": 400, "cells": cells}]}
    r, out = build(spec, tmp_path)
    assert r.returncode == 0, r.stderr
    root = ds.read_file(str(out))
    model = ds.ET.fromstring(ds.decode_diagram(root.find("diagram")))
    values = {c.get("id"): c.get("value") for c in model.iter("mxCell")}
    # what draw.io renders as HTML: the user's characters arrive entity-encoded, only our tags are live
    assert "Render &lt;input&gt; &amp; type &amp;copy;" in values["s_a"]
    assert "&lt;span style='color:red'&gt;x&lt;/span&gt;" in values["s_a"]
    assert values["s_e"] == "&lt;b&gt;not bold&lt;/b&gt;"
    assert "a &lt;i&gt;component&lt;/i&gt;" in values.values()


# --------------------------------------------------------------------------- text measure and height
def test_delay_auto_height_matches_its_numeric_equivalent(capsys):
    cells = [{"id": "d", "role": "plain", "shape": "delay", "text": "M" * 47, "x": 0, "y": 0, "w": 110, "h": "auto"}]
    spec = {"sheets": [{"id": "s", "name": "L1 T", "cells": cells}]}
    (_, xml, _), = ds.compile_spec(spec)
    h = int(re.search(r'id="s_d".*?height="(\d+)"', xml).group(1))
    assert "spacingRight=%d;" % (h // 2 - 6) in styles(xml)["d"]
    capsys.readouterr()
    cells[0]["h"] = h
    (_, xml2, _), = ds.compile_spec(spec)
    assert "text clips" not in capsys.readouterr().err
    assert styles(xml2)["d"] == styles(xml)["d"]


def test_korean_text_counts_as_wide_characters(capsys):
    hangul = "가" * 24
    assert ds.text_width(hangul, 12) == pytest.approx(24 * 12)
    assert ds.text_width("é", 10) == ds.text_width("e", 10)       # a combining mark takes no width
    cells = [{"id": "k", "role": "plain", "text": hangul, "x": 0, "y": 0, "w": 120, "h": "auto", "font": 12}]
    ds.compile_spec({"sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    assert "h=61 (3 lines, font 12)" in capsys.readouterr().out


def test_build_with_korean_text_survives_a_cp1252_console(tmp_path):
    spec = {"sheets": [{"id": "s", "name": "L1 T", "cells": [
        {"role": "plain", "text": "한국어 상자\n• 둘째 줄", "x": 0, "y": 0, "w": 160, "h": "auto"},
        {"type": "legend", "x": 300, "y": 0, "labels": {"plain": "구성 요소"}}]}]}
    p = tmp_path / "spec.json"
    p.write_text(json.dumps(spec), encoding="utf-8")
    out = tmp_path / "out.drawio"
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    r = subprocess.run([sys.executable, SCRIPT, "build", str(p), "-o", str(out), "--force"],
                       capture_output=True, env=env)
    assert r.returncode == 0, r.stderr
    assert b"auto h:" in r.stdout and b"(2 lines" in r.stdout
    assert "한국어" in out.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- geometry checks
def test_col_without_a_grid_is_an_error_and_validate_flags_invisible_cells(tmp_path):
    cells = [{"id": "a", "text": "a", "col": 1, "y": 0, "h": 40}]
    with pytest.raises(SystemExit, match="sheet 'L1 T' cell 'a' uses col/span"):
        ds.compile_spec({"sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    r, out = build(one_box_per_role(), tmp_path)
    text = out.read_text(encoding="utf-8")
    new = re.sub(r'(id="s_plain"[^>]*><mxGeometry x="\d+" y="\d+") width="\d+"', r'\1 width="0"', text)
    assert new != text
    out.write_text(new, encoding="utf-8")
    val = subprocess.run([sys.executable, SCRIPT, "validate", str(out)], capture_output=True, text=True)
    assert val.returncode == 1 and "s_plain" in val.stdout and "cannot be seen" in val.stdout


def test_legend_covering_a_box_warns(capsys):
    cells = [{"id": "ok", "role": "plain", "text": "Approve", "x": 15, "y": 52, "w": 80, "h": 26},
             {"type": "legend", "id": "lg", "x": 0, "y": 30, "w": 220}]
    ds.compile_spec({"sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    assert "the legend lg covers ok" in capsys.readouterr().err
    cells[1]["x"] = 300
    cells.insert(0, {"type": "lane", "id": "ln", "x": 0, "y": 0, "w": 800, "h": 300})
    ds.compile_spec({"sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    assert "covers" not in capsys.readouterr().err      # a lane may hold the legend


def test_frame_padding_and_auto_height_past_the_frame(capsys):
    cells = [{"id": "f", "square": True, "text": "Set 3", "x": 0, "y": 0, "w": 300, "h": 200},
             {"id": "high", "text": "high", "x": 20, "y": 10, "w": 100, "h": 30},
             {"id": "edge", "text": "edge", "x": 4, "y": 60, "w": 100, "h": 30},
             {"id": "grow", "text": "grow\n• one\n• two\n• three\n• four\n• five\n• six", "x": 150, "y": 150, "w": 120, "h": "auto"}]
    ds.compile_spec({"sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    err = capsys.readouterr().err
    assert "high starts above the headline of frame f (y=10, minimum y=25)" in err
    assert "edge is 4 px from the left side of frame f" in err
    assert "auto-height box grow grows past the bottom of frame f" in err


# --------------------------------------------------------------------------- roles, shapes, alignment, legend
def test_shapes_on_their_allowed_roles_keep_the_role_in_the_legend(capsys):
    cells = [{"id": "p", "role": "human", "shape": "actor", "text": "Max", "x": 0, "y": 0, "w": 40, "h": 70},
             {"id": "q", "role": "decide", "shape": "decision", "text": "ok?", "x": 0, "y": 100, "w": 80, "h": 60},
             {"id": "r", "role": "propose", "shape": "database", "text": "db", "x": 0, "y": 200},
             {"type": "legend", "x": 300, "y": 0}]
    (_, xml, _), = ds.compile_spec({"sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    err = capsys.readouterr().err
    assert "hides role 'human'" not in err and "hides role 'decide'" not in err and "hides role 'propose'" in err
    rows = legend_rows(xml)
    assert "person: a person decides" in rows and "yes/no check: code decides" in rows and "database" in rows


def test_multi_line_boxes_default_to_top_left_in_minimal_and_bw():
    spec = one_box_per_role()
    cells = {c.get("id"): c for c in spec["sheets"][0]["cells"]}
    cells["plain"]["text"] = "Head\n• one"
    cells["guard"].update({"text": "Head\n• one", "align": "center"})
    cells["decide"].update({"text": "Head", "shape": "decision"})
    for theme in ("minimal", "bw"):
        (_, xml, _), = ds.compile_spec(spec, theme)
        st = styles(xml)
        assert "align=left;verticalAlign=top;" in st["plain"]
        assert "align=center;verticalAlign=top;" in st["guard"]
        assert "align=center;verticalAlign=middle;" in st["decide"]
        assert "align=center;verticalAlign=middle;" in st["human"]     # one line: centred
    (_, xml, _), = ds.compile_spec(spec, "colorful")
    assert "align=center;verticalAlign=middle;" in styles(xml)["plain"]


def test_accent_only_on_edges_gives_a_line_sample():
    cells = [{"id": "a", "text": "a", "x": 0, "y": 0}, {"id": "b", "text": "b", "x": 0, "y": 100},
             {"type": "edge", "from": "a", "to": "b", "accent": True},
             {"type": "legend", "x": 300, "y": 0, "labels": {"accent": "the compared path"}}]
    spec = {"sheets": [{"id": "s", "name": "L1 T", "cells": cells}]}
    (_, xml, _), = ds.compile_spec(spec)
    assert re.search(r'value="the compared path" style="shape=line;[^"]*strokeColor=#1F5FBF;strokeWidth=2;', xml)
    cells[0]["accent"] = True
    (_, xml, _), = ds.compile_spec(spec)
    assert re.search(r'value="the compared path" style="rounded=0;', xml)


def test_validate_ladder_warns_on_a_sheet_without_legend(tmp_path):
    spec = one_box_per_role()
    r, out = build(spec, tmp_path)
    val = subprocess.run([sys.executable, SCRIPT, "validate", str(out), "--ladder"], capture_output=True, text=True)
    assert "L1 Test: no legend cell" in val.stdout
    spec["sheets"][0]["cells"].append({"type": "legend", "x": 300, "y": 10})
    r, out = build(spec, tmp_path)
    val = subprocess.run([sys.executable, SCRIPT, "validate", str(out), "--ladder"], capture_output=True, text=True)
    assert "no legend cell" not in val.stdout
    r, out = build(one_box_per_role(theme="colorful"), tmp_path)
    val = subprocess.run([sys.executable, SCRIPT, "validate", str(out), "--ladder"], capture_output=True, text=True)
    assert "no legend cell" not in val.stdout


@pytest.mark.parametrize("text,theme,warns", [
    ("blue/green deploy", "minimal", False),
    ("grey box = a note", "minimal", False),
    ("grey box = a note", "bw", True),
    ("Blue boxes: skills", "minimal", True),
    ("red - stop", "bw", True),
])
def test_colour_key_warning_matches_key_phrasing_only(text, theme, warns, capsys):
    spec = one_box_per_role(theme=theme)
    spec["sheets"][0]["cells"][0]["text"] = text
    ds.compile_spec(spec)
    assert ("names the colour" in capsys.readouterr().err) == warns


# --------------------------------------------------------------------------- the default-theme examples
@pytest.mark.parametrize("name", ["example_L1_minimal.json", "example_plugin_3_levels_minimal.json"])
@pytest.mark.parametrize("theme", ["minimal", "bw"])
def test_minimal_examples_build_and_validate_without_warnings(name, theme, tmp_path):
    out = tmp_path / "out.drawio"
    r = subprocess.run([sys.executable, SCRIPT, "build", os.path.join(REFS, name), "-o", str(out), "--force", "--theme", theme],
                       capture_output=True, text=True)
    assert r.returncode == 0 and r.stderr == "", r.stderr
    val = subprocess.run([sys.executable, SCRIPT, "validate", str(out), "--ladder"], capture_output=True, text=True)
    assert val.returncode == 0 and "warn" not in val.stdout and "ERR" not in val.stdout, val.stdout


# --------------------------------------------------------------------------- colorful labels: the old encoder
def baseline_label(text):
    """The label encoder of the script before themes existed: XML escaping only."""
    from xml.sax.saxutils import escape
    return escape(text, {'"': '&quot;'}).replace("\n", "&lt;br&gt;")


def test_colorful_labels_are_encoded_as_the_baseline_script_did(tmp_path):
    marked = '<b>DONE</b> &copy; "q"\nline two'
    cells = [{"type": "lane", "x": 0, "y": 0, "w": 700, "h": 300, "text": "<i>Lane</i> &amp;"},
             {"id": "a", "role": "plain", "text": marked, "x": 200, "y": 20, "w": 200, "h": 60},
             {"id": "b", "role": "decide", "text": "b", "x": 200, "y": 200},
             {"type": "text", "id": "t", "text": "<u>note</u> &lt;", "x": 450, "y": 20, "w": 200, "h": 30},
             {"type": "edge", "id": "e", "from": "a", "to": "b", "label": "<b>x</b> &copy;"}]
    spec = {"theme": "colorful", "sheets": [{"id": "s", "name": "L1 T", "width": 800, "height": 400, "cells": cells}]}
    r, out = build(spec, tmp_path)
    assert r.returncode == 0, r.stderr
    text = out.read_text(encoding="utf-8")
    for ident, raw in (("s_a", marked), ("s_t", "<u>note</u> &lt;"), ("s_e", "<b>x</b> &copy;"),
                       ("s_auto2", "<i>Lane</i> &amp;")):
        assert '<mxCell id="%s" value="%s"' % (ident, baseline_label(raw)) in text, ident
    # minimal and bw keep the text literal
    spec["theme"] = "minimal"
    r, out = build(spec, tmp_path)
    assert "&amp;lt;b&amp;gt;DONE" in out.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- colour audit of existing files
def compress_sheets(path):
    """Rewrite a built file with every sheet compressed, as draw.io saves by default."""
    import base64
    import urllib.parse
    import zlib
    root = ds.read_file(str(path))
    for d in root.findall("diagram"):
        m = d.find("mxGraphModel")
        raw = urllib.parse.quote(ds.ET.tostring(m, encoding="unicode"), safe="")
        co = zlib.compressobj(9, zlib.DEFLATED, -15)
        d.remove(m)
        d.text = base64.b64encode(co.compress(raw.encode("utf-8")) + co.flush()).decode("ascii")
    ds.ET.ElementTree(root).write(str(path), encoding="utf-8", xml_declaration=False)
    assert "<mxGraphModel" not in path.read_text(encoding="utf-8")


def edit_model(path, fn):
    """Hand-edit the first sheet's graph model (decoded), writing it back uncompressed."""
    root = ds.read_file(str(path))
    d = root.find("diagram")
    m = ds.ET.fromstring(ds.decode_diagram(d))
    fn(m)
    for ch in list(d):
        d.remove(ch)
    d.text = None
    d.append(m)
    ds.ET.ElementTree(root).write(str(path), encoding="utf-8", xml_declaration=False)


def set_background(m):
    m.set("background", "#FF0000")


def set_font_colour(m):
    for c in m.iter("mxCell"):
        if c.get("id") == "s_plain":
            c.set("value", '<font color="#0066FF">blue</font> and <span style="background-color: rgb(0, 128, 0)">x</span>')


@pytest.mark.parametrize("compressed", [False, True])
@pytest.mark.parametrize("edit,colours", [(set_background, ["#FF0000"]), (set_font_colour, ["#0066FF", "#008000"])])
def test_audit_counts_page_background_and_label_html_colours(edit, colours, compressed, tmp_path):
    r, out = build(one_box_per_role(theme="bw"), tmp_path)
    edit_model(out, edit)
    if compressed:
        compress_sheets(out)
    val = subprocess.run([sys.executable, SCRIPT, "validate", str(out)], capture_output=True, text=True)
    assert val.returncode == 1 and "ERR" in val.stdout and all(c in val.stdout for c in colours), val.stdout
    before = out.read_bytes()
    r = add(second_sheet(), out, tmp_path)
    assert r.returncode != 0 and all(c in r.stderr for c in colours) and out.read_bytes() == before
    assert not os.path.exists(str(out) + ".bak")


def test_escaped_markup_in_a_label_is_not_paint(tmp_path):
    spec = one_box_per_role(theme="bw")
    spec["sheets"][0]["cells"][0]["text"] = "<font color='#0066FF'>x</font> <span style=\"color:red\">y</span>"
    r, out = build(spec, tmp_path)
    assert r.returncode == 0, r.stderr
    val = subprocess.run([sys.executable, SCRIPT, "validate", str(out)], capture_output=True, text=True)
    assert val.returncode == 0 and "2 colours: #000000 #FFFFFF" in val.stdout


def test_label_colours_reads_active_html_only():
    assert ds.label_colours('<font color="red">a</font><div style="color:#fff;background:url(x) no-repeat #123">b</div>') == \
        {"RED", "#FFFFFF", "#112233"}
    assert ds.label_colours("&lt;font color='#0066FF'&gt;") == set()
    assert ds.label_colours('<span style="color: inherit; background-color: transparent">a</span>') == set()


# --------------------------------------------------------------------------- the accent colour belongs to accented cells
def test_minimal_accent_colour_only_on_accented_cells(tmp_path):
    spec = one_box_per_role()
    cells = {c.get("id"): c for c in spec["sheets"][0]["cells"]}
    cells["human"]["accent"] = True                       # a genuine accent
    cells["propose"]["fill"] = "#1F5FBF"                  # unmarked override equal to the accent: ignored
    cells["human"]["fill"] = "#1F5FBF"                    # on the accented cell: kept
    lane = [c for c in spec["sheets"][0]["cells"] if c.get("type") == "lane"][0]
    lane["fill"] = "#1F5FBF"                              # a lane's body is always white: ignored
    spec["sheets"][0]["cells"] += [{"type": "edge", "id": "e2", "from": "plain", "to": "note", "color": "#1F5FBF"},
                                   {"type": "legend", "x": 420, "y": 10, "labels": {"accent": "the path"}}]
    r, out = build(spec, tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stderr.count("is the accent colour, which only a cell or edge that sets accent may use") == 2
    assert "fill='#1F5FBF' ignored; in theme minimal a lane's body is always white" in r.stderr
    xml = out.read_text(encoding="utf-8")
    st = styles(xml)
    assert "fillColor=#FFFFFF;" in st["propose"] and "fillColor=#1F5FBF;" in st["human"]
    assert "strokeColor=#000000;" in st["e2"]
    assert "the path" in legend_rows(xml)
    spec["sheets"][0]["cells"][1]["style"] = "fontColor=#1F5FBF;"   # raw style on an unmarked cell: stops
    r, out = build(spec, tmp_path)
    assert r.returncode != 0 and "is the accent colour" in r.stderr


@pytest.mark.parametrize("colour", ["#000000", "#ffffff", "#F2F2F2"])
def test_accent_equal_to_a_palette_colour_stops(colour, tmp_path):
    with pytest.raises(SystemExit, match="accent_color %s is" % colour.upper()):
        ds.compile_spec(one_box_per_role(accent_color=colour))
    r, out = build(one_box_per_role(accent_color=colour, theme="colorful"), tmp_path)
    assert r.returncode != 0 and not out.exists()


# --------------------------------------------------------------------------- a role's own shape, stated
@pytest.mark.parametrize("theme", ["minimal", "bw"])
@pytest.mark.parametrize("role", sorted(ds.ROLE_SHAPES))
def test_role_with_its_own_shape_stated_is_the_default(role, theme, capsys):
    def sheet(**extra):
        box = dict({"id": "a", "role": role, "text": "a", "x": 0, "y": 0, "w": 120, "h": 60}, **extra)
        return {"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "cells": [box, {"type": "legend", "x": 300, "y": 0}]}]}
    (_, implicit, _), = ds.compile_spec(sheet())
    capsys.readouterr()
    (_, explicit, _), = ds.compile_spec(sheet(shape=ds.ROLE_SHAPES[role]))
    assert "hides role" not in capsys.readouterr().err
    assert explicit == implicit
    assert legend_rows(explicit) == [ds.ROLE_MEANINGS[role]]


# --------------------------------------------------------------------------- enforced keys, frame title, terminator
def test_frame_title_top_left_and_enforced_keys_win(capsys):
    cells = [{"id": "f", "square": True, "text": "Set", "x": 0, "y": 0, "w": 300, "h": 200,
              "style": "rounded=1;arcSize=50;"},
             {"id": "d", "shape": "decision", "role": "decide", "text": "ok?", "x": 400, "y": 0, "w": 100, "h": 60,
              "style": "align=left;verticalAlign=top;"},
             {"id": "kid", "text": "kid", "x": 100, "y": 80, "w": 100, "h": 40}]
    for theme in ("minimal", "bw"):
        (_, xml, _), = ds.compile_spec({"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
        st = styles(xml)
        d, _ = ds.parse_style(st["f"])
        assert (d["rounded"], d["align"], d["verticalAlign"]) == ("0", "left", "top")
        assert st["f"].count("rounded=") == 1
        d, _ = ds.parse_style(st["d"])
        assert (d["align"], d["verticalAlign"]) == ("center", "middle") and st["d"].count("align=") == 1
        err = capsys.readouterr().err
        assert "style rounded=1 conflicts" in err and "style align=left;verticalAlign=top conflicts" in err
    (_, xml, _), = ds.compile_spec({"theme": "colorful", "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    assert "align=center;verticalAlign=middle;" in styles(xml)["f"]     # colorful unchanged


def test_terminator_centres_its_text_and_warns(capsys):
    cells = [{"id": "t", "shape": "terminator", "text": "Start\nmore", "x": 0, "y": 0, "w": 140, "h": 120, "align": "left"}]
    (_, xml, _), = ds.compile_spec({"sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    assert "align=center;verticalAlign=middle;" in styles(xml)["t"]
    err = capsys.readouterr().err
    assert "a terminator centres its text" in err and "a terminator holds one short line" in err


def test_predefined_text_wraps_between_the_bars(capsys):
    # 'Status updated' at font 9 is about 63 px: it fits the old 72 px estimate, not the 56 px draw.io leaves
    cells = [{"id": "d", "role": "decide", "text": "Status updated", "x": 0, "y": 0, "w": 100, "h": "auto"}]
    ds.compile_spec({"sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    assert "(2 lines, font 9)" in capsys.readouterr().out
    cells[0]["w"] = 116
    ds.compile_spec({"sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    assert "(1 lines, font 9)" in capsys.readouterr().out


# --------------------------------------------------------------------------- legend as one unit
def test_legend_rows_are_children_of_the_legend_frame(tmp_path):
    cells = [{"id": "a", "role": "human", "text": "a", "x": 0, "y": 0},
             {"id": "b", "role": "record", "text": "b", "x": 0, "y": 100},
             {"type": "legend", "id": "lg", "x": 300, "y": 50}]
    for theme in ("minimal", "bw"):
        spec = {"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "width": 600, "height": 300, "cells": cells}]}
        (_, xml, _), = ds.compile_spec(spec)
        assert re.search(r'id="s_lg"[^>]*container=1;collapsible=0;dsLegend=1;" vertex="1" parent="1"><mxGeometry x="300" y="50"', xml)
        rows = re.findall(r'labelPosition=right;[^"]*dsLegend=1;" vertex="1" parent="([^"]+)"><mxGeometry x="(\d+)" y="(\d+)"', xml)
        assert len(rows) == 2 and all(p == "s_lg" and x == "10" for p, x, _ in rows)
        assert int(rows[0][2]) < 50   # relative to the frame
        r, out = build(spec, tmp_path)
        val = subprocess.run([sys.executable, SCRIPT, "validate", str(out), "--ladder"], capture_output=True, text=True)
        assert "outside page" not in val.stdout and "legend=3" in val.stdout
    (_, xml, _), = ds.compile_spec(dict(spec, theme="colorful"))
    assert "container=1" not in xml and 'parent="s_' not in xml


# --------------------------------------------------------------------------- settings: one strict path
def test_max_colours_aliases_and_bad_values(tmp_path):
    no_ladder = one_box_per_role(max_colors=3)
    no_ladder["sheets"][0]["name"] = "Test"     # v10: an L1-L4 sheet without a legend warns (C-deb-7)
    r, out = build(no_ladder, tmp_path)
    assert r.returncode == 0 and "max_colours 3" in r.stdout and r.stderr == ""
    r, out = build(one_box_per_role(), tmp_path, "--max-colors", "3")
    assert r.returncode == 0 and "max_colours 3" in r.stdout
    r, out = build(one_box_per_role(accent_colour="#123456"), tmp_path)
    assert r.returncode == 0 and 'dsAccent="#123456"' in out.read_text(encoding="utf-8")
    r, _ = build(one_box_per_role(max_colours=3, max_colors=4), tmp_path)
    assert r.returncode != 0 and "keep one" in r.stderr
    for bad, msg in ((3.9, "not a whole number"), ("four", "not a whole number"), (True, "not a whole number")):
        r, out = build(one_box_per_role(max_colours=bad), tmp_path)
        assert r.returncode != 0 and msg in r.stderr and "Traceback" not in r.stderr, r.stderr
    r, out = build(one_box_per_role(), tmp_path)
    for bad in ("four", 3.9):
        r = add(second_sheet(max_colours=bad), out, tmp_path)
        assert r.returncode != 0 and "not a whole number" in r.stderr and "Traceback" not in r.stderr, r.stderr


def test_unknown_keys_warn_with_the_nearest_valid_key(capsys):
    spec = one_box_per_role(max_colour=3)
    spec["sheets"][0]["heigth"] = 900
    spec["sheets"][0]["cells"] += [{"type": "text", "id": "tx", "text": "t", "x": 0, "y": 0, "w": 50, "h": 20, "valign": "top"},
                                   {"type": "edge", "from": "plain", "to": "note", "colour": "#000000"}]
    ds.compile_spec(spec)
    err = capsys.readouterr().err
    assert "unknown top-level key 'max_colour' (nearest valid key: 'max_colours')" in err
    assert "sheet 'L1 Test': unknown key 'heigth' (nearest valid key: 'height')" in err
    assert "sheet 'L1 Test' cell 'tx': unknown key 'valign' for a text (nearest valid key: 'align')" in err
    assert "unknown key 'colour' for a edge (nearest valid key: 'color')" in err


def test_stored_theme_is_checked_and_a_missing_record_is_colorful(tmp_path):
    r, out = build(one_box_per_role(), tmp_path)
    text = out.read_text(encoding="utf-8")
    out.write_text(text.replace('dsTheme="minimal"', 'dsTheme="miniml"'), encoding="utf-8")
    for cmd in ("validate", "list"):
        v = subprocess.run([sys.executable, SCRIPT, cmd, str(out)], capture_output=True, text=True)
        assert v.returncode != 0 and "unknown theme 'miniml'" in v.stderr and "Traceback" not in v.stderr
    before = out.read_bytes()
    r = add(second_sheet(), out, tmp_path)
    assert r.returncode != 0 and "unknown theme 'miniml'" in r.stderr and out.read_bytes() == before
    out.write_text(text.replace('dsMaxColours="4"', 'dsMaxColours="four"'), encoding="utf-8")
    v = subprocess.run([sys.executable, SCRIPT, "validate", str(out)], capture_output=True, text=True)
    assert v.returncode != 0 and "not a whole number" in v.stderr


def test_theme_record_survives_stripped_mxfile_attributes(tmp_path):
    spec = one_box_per_role(max_colours=3)
    spec["sheets"].append(dict(second_sheet()["sheets"][0], id="u", name="L2 Two"))
    r, out = build(spec, tmp_path)
    text = out.read_text(encoding="utf-8")
    assert text.count("dsTheme=minimal;dsAccent=#1F5FBF;dsMaxColours=3;") == 4      # layer and first vertex, per sheet
    out.write_text(re.sub(r' ds(Theme|Accent|MaxColours)="[^"]*"', "", text), encoding="utf-8")
    assert ds.file_theme(ds.read_file(str(out))) == ("minimal", "#1F5FBF", 3, True)
    more = second_sheet()
    more["sheets"][0].update({"id": "v", "name": "L3 Three"})
    more["sheets"][0]["cells"][0]["accent"] = True
    r = add(more, out, tmp_path)
    assert r.returncode == 0 and "theme minimal (from the file)" in r.stdout, r.stderr
    new = out.read_text(encoding="utf-8").split('name="L3 Three"')[1]
    assert "shape=manualInput;" in new and "strokeWidth=4;" in new and "#1F5FBF" not in new.replace("dsAccent=#1F5FBF", "")
    r = add(dict(second_sheet(theme="colorful")), out, tmp_path)
    assert r.returncode != 0 and "mix themes" in r.stderr
    compress_sheets(out)
    assert ds.file_theme(ds.read_file(str(out)))[0] == "minimal"


def test_colorful_files_carry_no_style_record():
    (_, xml, _), = ds.compile_spec(one_box_per_role(theme="colorful"))
    assert "dsTheme" not in xml


# --------------------------------------------------------------------------- duplicate sheet names
def test_duplicate_sheet_names_are_refused_and_reported(tmp_path):
    spec = one_box_per_role()
    spec["sheets"].append(dict(spec["sheets"][0], id="u"))
    with pytest.raises(SystemExit, match="duplicate sheet name 'L1 Test'"):
        ds.compile_spec(spec)
    r, out = build(one_box_per_role(), tmp_path)
    batch = second_sheet()
    batch["sheets"].append(dict(batch["sheets"][0], id="u"))
    before = out.read_bytes()
    r = add(batch, out, tmp_path)
    assert r.returncode != 0 and "duplicate sheet name 'L2 More'" in r.stderr and out.read_bytes() == before
    text = out.read_text(encoding="utf-8")
    m = re.search(r"<diagram .*?</diagram>", text)
    out.write_text(text.replace(m.group(0), m.group(0) + m.group(0).replace('id="s"', 'id="s2"', 1)), encoding="utf-8")
    val = subprocess.run([sys.executable, SCRIPT, "validate", str(out)], capture_output=True, text=True)
    assert val.returncode == 1 and "2 sheets are named 'L1 Test'" in val.stdout


# --------------------------------------------------------------------------- the accent drawn by width
@pytest.mark.parametrize("top", [{"theme": "bw"}, {"max_colours": 3}])
def test_accent_by_width_is_4_and_wide_plain_lines_warn(top, capsys):
    spec = one_box_per_role(**top)
    cells = {c.get("id"): c for c in spec["sheets"][0]["cells"]}
    cells["human"]["accent"] = True
    cells["plain"]["style"] = "strokeWidth=3;"
    spec["sheets"][0]["cells"] += [{"type": "edge", "id": "acc", "from": "plain", "to": "note", "accent": True, "width": 2},
                                   {"type": "edge", "id": "wide", "from": "note", "to": "stage", "width": 3},
                                   {"type": "edge", "id": "ok", "from": "stage", "to": "guard", "width": 2},
                                   {"type": "legend", "x": 420, "y": 10}]
    (_, xml, _), = ds.compile_spec(spec)
    st = styles(xml)
    assert "strokeWidth=4;" in st["human"] and "strokeWidth=4;" in st["acc"]
    assert re.search(r'value="highlighted" style="[^"]*strokeColor=#000000;strokeWidth=4;', xml)
    err = capsys.readouterr().err
    assert "cell 'plain': line width 3" in err and "cell 'wide': line width 3" in err
    assert "'ok'" not in err and "'human'" not in err
    spec.pop("theme", None)
    spec.pop("max_colours", None)
    ds.compile_spec(spec)                     # minimal with 4 colours: the accent is a colour, no warning
    assert "line width" not in capsys.readouterr().err


# =========================================================================== v5
def validate(path, *args):
    return subprocess.run([sys.executable, SCRIPT, "validate", str(path)] + list(args), capture_output=True, text=True)


def list_file(path):
    return subprocess.run([sys.executable, SCRIPT, "list", str(path)], capture_output=True, text=True)


def edit_cell(cell_id, fn):
    def edit(m):
        for c in m.iter("mxCell"):
            if c.get("id") == cell_id:
                fn(c)
    return edit


# --------------------------------------------------------------------------- A-aera-1: gradients and opacity
def add_gradient(c):
    c.set("style", c.get("style") + "gradientColor=#000000;")


def add_opacity(c):
    c.set("style", c.get("style") + "fillOpacity=50;")


@pytest.mark.parametrize("compressed", [False, True])
@pytest.mark.parametrize("theme,fn,key", [("bw", add_gradient, "gradientColor=#000000"),
                                         ("minimal", add_opacity, "fillOpacity=50")])
def test_gradient_or_partial_opacity_breaks_a_minimal_or_bw_file(theme, fn, key, compressed, tmp_path):
    r, out = build(one_box_per_role(theme=theme), tmp_path)
    edit_model(out, edit_cell("s_plain", fn))
    if compressed:
        compress_sheets(out)
    val = validate(out)
    assert val.returncode == 1 and "ERR" in val.stdout and key in val.stdout and "s_plain" in val.stdout, val.stdout
    assert key in list_file(out).stdout
    before = out.read_bytes()
    r = add(second_sheet(), out, tmp_path)
    assert r.returncode != 0 and key in r.stderr and out.read_bytes() == before
    assert not os.path.exists(str(out) + ".bak")


def test_raw_style_accepts_no_gradient_and_full_opacity_only(tmp_path):
    spec = one_box_per_role(theme="bw")
    spec["sheets"][0]["cells"][0]["style"] = "gradientColor=none;opacity=100;"
    r, out = build(spec, tmp_path)
    assert r.returncode == 0, r.stderr
    spec["sheets"][0]["cells"][0]["style"] = "strokeOpacity=99;"
    r, out = build(spec, tmp_path)
    assert r.returncode != 0 and "strokeOpacity=99" in r.stderr


# --------------------------------------------------------------------------- A-aera-2: label audit follows html mode
def set_label(value, html=True):
    def fn(c):
        c.set("value", value)
        if not html:
            c.set("style", c.get("style").replace("html=1;", "html=0;"))
    return fn


@pytest.mark.parametrize("value,html,bad", [
    ('<span style="background:red no-repeat">x</span>', True, "RED"),
    ("<span style=color:red>x</span>", True, "RED"),
    ('<font color="red">x</font>', False, None),
    ('<span style="background:url(x) no-repeat">x</span>', True, '<span style="background:url(x) no-repeat">'),
])
def test_label_audit_follows_html_mode_and_reads_unquoted_and_named_colours(value, html, bad, tmp_path):
    r, out = build(one_box_per_role(theme="bw"), tmp_path)
    edit_model(out, edit_cell("s_plain", set_label(value, html)))
    val = validate(out)
    before = out.read_bytes()
    r = add(second_sheet(), out, tmp_path)
    if bad:
        assert val.returncode == 1 and bad in val.stdout, val.stdout
        assert r.returncode != 0 and bad in r.stderr and out.read_bytes() == before
    else:
        assert val.returncode == 0 and "problems: 0" in val.stdout, val.stdout
        assert r.returncode == 0, r.stderr


# --------------------------------------------------------------------------- A-aera-3: the accent wins, as the legend shows
def test_accented_box_and_edge_match_the_legend_sample_in_minimal():
    cells = [{"id": "a", "text": "a", "x": 0, "y": 0, "accent": True, "style": "strokeColor=#000000;strokeWidth=9;"},
             {"id": "b", "text": "b", "x": 0, "y": 100},
             {"type": "edge", "id": "e", "from": "a", "to": "b", "accent": True, "width": 8,
              "style": "strokeWidth=9;strokeColor=#000000;"},
             {"type": "legend", "x": 300, "y": 0}]
    (_, xml, _), = ds.compile_spec({"sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    st = styles(xml)
    for k in ("a", "e"):
        d, _ = ds.parse_style(st[k])
        assert (d["strokeColor"], d["strokeWidth"]) == ("#1F5FBF", "2"), k
        assert st[k].count("strokeWidth=") == 1 and st[k].count("strokeColor=") == 1, k
    assert "strokeColor=#1F5FBF;strokeWidth=2;" in re.search(r'value="highlighted" style="([^"]*)"', xml).group(1)


# --------------------------------------------------------------------------- A-aera-4, C-deb-3: a lane cannot take the accent
def test_a_lane_with_accent_cannot_use_the_accent_colour(capsys):
    spec = one_box_per_role()
    lane = [c for c in spec["sheets"][0]["cells"] if c.get("type") == "lane"][0]
    lane.update(accent=True, fill="#1F5FBF")
    (_, xml, _), = ds.compile_spec(spec)
    err = capsys.readouterr().err
    assert "unknown key 'accent' for a lane" in err and "a lane's body is always white" in err
    assert ds.emitted_colours(xml) == {"#000000", "#FFFFFF", "#F2F2F2"}
    assert "#1F5FBF" not in xml.replace("dsAccent=#1F5FBF", "")




# --------------------------------------------------------------------------- A-aera-6: a raw key that removes a shape's mark
@pytest.mark.parametrize("role,style", [("propose", "rounded=0;"), ("outside", "double=0;")])
def test_raw_key_that_removes_a_role_shape_mark_warns(role, style, capsys):
    cells = [{"id": "a", "role": role, "text": "a", "x": 0, "y": 0, "style": style}]
    for theme in ("minimal", "bw"):
        ds.compile_spec({"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
        err = capsys.readouterr().err
        assert "cell 'a': style %s removes what marks" % style.rstrip(";") in err, err
    ds.compile_spec({"theme": "colorful", "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    assert "removes what marks" not in capsys.readouterr().err


# --------------------------------------------------------------------------- A-aera-7: two roles drawn alike
def test_two_roles_drawn_alike_warn_with_both_ids(capsys):
    cells = [{"id": "h", "shape": "decision", "role": "human", "text": "ok?", "x": 0, "y": 0, "w": 100, "h": 60},
             {"id": "d", "shape": "decision", "role": "decide", "text": "ok?", "x": 0, "y": 100, "w": 100, "h": 60}]
    for theme in ("minimal", "bw"):
        ds.compile_spec({"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
        assert "boxes 'h' (role human) and 'd' (role decide) are drawn alike" in capsys.readouterr().err
    ds.compile_spec({"theme": "minimal", "sheets": [{"id": "s", "name": "L1 T", "cells": cells[:1]}]})
    assert "drawn alike" not in capsys.readouterr().err


# --------------------------------------------------------------------------- A-aera-8: html stays on
def test_html_stays_on_for_box_and_edge_labels(capsys):
    cells = [{"id": "a", "text": "Head\nDetail", "x": 0, "y": 0, "w": 120, "h": 60, "style": "html=0;"},
             {"id": "b", "text": "b", "x": 0, "y": 100},
             {"type": "edge", "id": "e", "from": "a", "to": "b", "label": "x\ny", "style": "html=0;"}]
    for theme in ("minimal", "bw"):
        (_, xml, _), = ds.compile_spec({"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
        st = styles(xml)
        for k in ("a", "e"):
            assert ds.parse_style(st[k])[0]["html"] == "1" and "html=0" not in st[k], k
        values = {c.get("id"): c.get("value") for c in ds.ET.fromstring(xml).iter("mxCell")}
        assert values["s_a"] == "<b>Head</b><br>Detail" and values["s_e"] == "x<br>y"
        assert capsys.readouterr().err.count("style html=0 ignored") == 2


# --------------------------------------------------------------------------- A-aera-10: a custom colorful accent is recorded on cells
def test_colorful_custom_accent_is_recorded_on_each_sheet_and_kept_by_add(tmp_path):
    spec = one_box_per_role(theme="colorful", accent_color="#CC6600")
    spec["sheets"][0]["cells"][0]["accent"] = True
    r, out = build(spec, tmp_path)
    text = out.read_text(encoding="utf-8")
    assert text.count("dsTheme=colorful;dsAccent=#CC6600;") == 2      # the layer cell and the first vertex
    out.write_text(re.sub(r' ds(Theme|Accent|MaxColours)="[^"]*"', "", text), encoding="utf-8")
    assert ds.file_theme(ds.read_file(str(out))) == ("colorful", "#CC6600", None, True)
    more = second_sheet()
    more["sheets"][0]["cells"][0]["accent"] = True
    r = add(more, out, tmp_path)
    assert r.returncode == 0, r.stderr
    new = out.read_text(encoding="utf-8").split('name="L2 More"')[1]
    assert "strokeColor=#CC6600;" in new and "#1F5FBF" not in new


# --------------------------------------------------------------------------- A-aera-11: a bw frame is always dashed
def test_bw_frame_stays_dashed_whatever_the_cell_or_style_says(capsys):
    cells = [{"id": "f", "square": True, "text": "Set", "x": 0, "y": 0, "w": 300, "h": 200, "dashed": False},
             {"id": "g", "square": True, "text": "Set", "x": 400, "y": 0, "w": 300, "h": 200, "style": "dashed=0;"}]
    (_, xml, _), = ds.compile_spec({"theme": "bw", "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    st = styles(xml)
    for k in ("f", "g"):
        assert ds.parse_style(st[k])[0]["dashed"] == "1" and st[k].count("dashed=") == 1, k
    err = capsys.readouterr().err
    assert "cell 'f': dashed=false conflicts" in err and "cell 'g': style dashed=0 conflicts" in err
    (_, xml, _), = ds.compile_spec({"theme": "minimal", "sheets": [{"id": "s", "name": "L1 T", "cells": cells[:1]}]})
    assert "dashed=" not in styles(xml)["f"] and "conflicts" not in capsys.readouterr().err


# --------------------------------------------------------------------------- A-aera-13: add checks both themes
def test_add_checks_the_command_line_and_the_spec_theme_separately(tmp_path):
    r, out = build(one_box_per_role(), tmp_path)
    before = out.read_bytes()
    r = add(second_sheet(theme="bw"), out, tmp_path, "--theme", "minimal")
    assert r.returncode != 0 and "the spec's theme bw" in r.stderr and out.read_bytes() == before
    r = add(second_sheet(theme="minimal"), out, tmp_path, "--theme", "bw")
    assert r.returncode != 0 and "--theme bw" in r.stderr and out.read_bytes() == before
    r = add(second_sheet(theme="minimal"), out, tmp_path, "--theme", "minimal")
    assert r.returncode == 0, r.stderr


# --------------------------------------------------------------------------- A-aera-14: text on black is white
def test_black_fill_gets_white_text_and_a_font_colour_equal_to_the_fill_warns(capsys):
    cells = [{"type": "lane", "id": "ln", "x": 0, "y": 0, "w": 600, "h": 300, "text": "Lane", "fill": "#000000"},
             {"id": "a", "text": "a", "x": 200, "y": 20, "fill": "#000000"},
             {"id": "b", "text": "b", "x": 200, "y": 120, "style": "fillColor=#000000;fontColor=#000000;"}]
    for theme in ("minimal", "bw"):
        (_, xml, _), = ds.compile_spec({"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
        st = styles(xml)
        assert ds.parse_style(st["a"])[0]["fontColor"] == "#FFFFFF"
        assert ds.parse_style(st["b"])[0]["fontColor"] == "#000000"
        err = capsys.readouterr().err
        assert "cell 'b': fontColor=#000000 is the box's fill" in err
        # a lane's body is always white (its fill is ignored with a warning); its title sits on the title
        # strip (grey in minimal, white in bw)
        lane = ds.parse_style(st["ln"])[0]
        assert lane["swimlaneFillColor"] == "#FFFFFF" and lane["fontColor"] == "#000000", theme
        assert "a lane's body is always white" in err
        assert lane["fillColor"] == ("#F2F2F2" if theme == "minimal" else "#FFFFFF"), theme


# --------------------------------------------------------------------------- A-aera-15: object and UserObject wrappers
def wrap_cells(tag, ids):
    def fn(m):
        root = m.find("root")
        for k, c in enumerate(list(root)):
            if c.get("id") in ids:
                w = ds.ET.Element(tag, {"id": c.get("id"), "label": c.get("value") or "", "tooltip": "Details"})
                del c.attrib["id"]
                c.attrib.pop("value", None)
                root.remove(c)
                w.append(c)
                root.insert(k, w)
    return fn


@pytest.mark.parametrize("tag", ["object", "UserObject"])
def test_wrapped_cells_are_read_through_their_wrapper(tag, tmp_path):
    cells = [{"id": "a", "text": "a", "x": 0, "y": 0}, {"id": "b", "text": "b", "x": 0, "y": 100},
             {"type": "edge", "id": "e", "from": "a", "to": "b"}, {"type": "legend", "x": 300, "y": 0}]
    spec = {"sheets": [{"id": "s", "name": "L1 T", "width": 600, "height": 300, "cells": cells}]}
    r, out = build(spec, tmp_path)
    edit_model(out, wrap_cells(tag, ("s_a", "s_b")))
    assert out.read_text(encoding="utf-8").count("<%s " % tag) == 2
    val = validate(out)
    assert val.returncode == 0 and "problems: 0" in val.stdout and "vertices=2 legend=2" in val.stdout, val.stdout
    assert "vertices=2" in list_file(out).stdout and "edges=1" in list_file(out).stdout
    r = add(second_sheet(), out, tmp_path)
    assert r.returncode == 0, r.stderr

    def paint(m):
        for w in m.iter(tag):
            w.set("label", '<font color="red">a</font>')
    r, out = build(spec, tmp_path)
    edit_model(out, wrap_cells(tag, ("s_a",)))
    edit_model(out, paint)
    val = validate(out)
    assert val.returncode == 1 and "RED" in val.stdout


# --------------------------------------------------------------------------- white lanes, grey title strip
def test_minimal_lane_is_white_with_a_grey_title_strip():
    (_, xml, _), = ds.compile_spec(nested_spec("minimal"))
    st = styles(xml)
    lane = ds.parse_style(st["lane"])[0]
    assert (lane["fillColor"], lane["swimlaneFillColor"], lane["strokeColor"]) == ("#F2F2F2", "#FFFFFF", "#000000")
    assert "fillColor=#F2F2F2;" in st["frame"]
    (_, xml, _), = ds.compile_spec(nested_spec("bw"))
    lane = ds.parse_style(styles(xml)["lane"])[0]
    assert (lane["fillColor"], lane["swimlaneFillColor"], lane["strokeColor"]) == ("#FFFFFF", "#FFFFFF", "#000000")
    for theme in ("minimal", "bw"):
        (_, xml, _), = ds.compile_spec(nested_spec(theme))
        assert 'value="Lane"' in xml and xml.count('value="Lane"') == 1     # no separate title cell


# --------------------------------------------------------------------------- C-deb-2: the worked example
def test_three_level_example_uses_catalogue_shapes_and_shares_its_L1():
    three = json.load(open(os.path.join(REFS, "example_plugin_3_levels_minimal.json"), encoding="utf-8"))
    one = json.load(open(os.path.join(REFS, "example_L1_minimal.json"), encoding="utf-8"))
    strip = lambda sh: dict(sh, cells=[{k: v for k, v in c.items() if k != "link"} for c in sh["cells"]])  # noqa: E731
    assert strip(three["sheets"][0]) == one["sheets"][0]
    want = {"wh": "database", "out0": "database", "out2": "database", "catalog": "database", "bq": "database",
            "duck": "database", "a6": "documents", "a7": "documents", "a8": "documents"}
    got = {c["id"]: c.get("shape") for sh in three["sheets"] for c in sh["cells"] if c.get("id") in want}
    assert got == want
    links = [c["link"] for c in three["sheets"][0]["cells"] if "link" in c]
    assert links and set(links) == {three["sheets"][1]["name"]}


# --------------------------------------------------------------------------- C-deb-5: add and settings outside minimal
def test_add_warns_on_max_colours_outside_minimal_and_bw_ignores_a_different_accent(tmp_path):
    r, out = build(one_box_per_role(theme="bw"), tmp_path)
    r = add(second_sheet(max_colours=3), out, tmp_path)
    assert r.returncode == 0 and "max_colours applies to the minimal theme only; ignored in bw" in r.stderr
    more = second_sheet(accent_color="#123456")
    more["sheets"][0].update(id="u", name="L3 X")
    r = add(more, out, tmp_path, "--max-colours", "3")
    assert r.returncode == 0 and "accent_color #123456 is ignored" in r.stderr and "ignored in bw" in r.stderr
    assert ds.emitted_colours(out.read_text(encoding="utf-8")) == {"#000000", "#FFFFFF"}


# --------------------------------------------------------------------------- C-deb-6: a legend inside a lane
def test_a_legend_inside_a_lane_moves_with_it():
    cells = [{"type": "lane", "id": "ln", "x": 0, "y": 0, "w": 800, "h": 400, "text": "Lane"},
             {"id": "a", "role": "human", "text": "a", "x": 200, "y": 20},
             {"type": "legend", "id": "lg", "x": 400, "y": 20}]
    for theme in ("minimal", "bw"):
        (_, xml, _), = ds.compile_spec({"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
        assert re.search(r'id="s_lg"[^>]*parent="s_ln"><mxGeometry x="400" y="20"', xml)
        rows = re.findall(r'labelPosition=right;[^"]*dsLegend=1;" vertex="1" parent="([^"]+)"', xml)
        assert rows and set(rows) == {"s_lg"}
    (_, xml, _), = ds.compile_spec({"theme": "colorful", "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    assert re.search(r'id="s_lg"[^>]*parent="1"', xml)


# --------------------------------------------------------------------------- C-deb-10: page links
def linked_spec(theme="minimal"):
    return {"theme": theme, "sheets": [
        {"id": "top", "name": "L1 Top", "cells": [{"id": "a", "text": "a", "x": 0, "y": 0, "link": "L2 Down"},
                                                  {"id": "b", "text": "b", "x": 0, "y": 100},
                                                  {"type": "edge", "id": "e", "from": "a", "to": "b"}]},
        {"id": "down", "name": "L2 Down", "cells": [{"id": "c", "text": "c", "x": 0, "y": 0, "link": "L1 Top"}]}]}


@pytest.mark.parametrize("theme", ["minimal", "bw", "colorful"])
def test_link_wraps_the_box_in_a_page_link(theme, tmp_path):
    spec = linked_spec(theme)
    (_, top, v), (_, down, _) = ds.compile_spec(spec)
    assert '<UserObject label="a" link="data:page/id,down" id="top_a"><mxCell style="' in top
    assert 'link="data:page/id,top" id="down_c"' in down and 'source="top_a"' in top
    plain = json.loads(json.dumps(spec))
    for sh in plain["sheets"]:
        for c in sh["cells"]:
            c.pop("link", None)
    (_, top0, v0), _ = ds.compile_spec(plain)
    assert v == v0 and ds.emitted_colours(top) == ds.emitted_colours(top0)    # no vertex, no colour
    r, out = build(spec, tmp_path)
    val = validate(out)
    assert val.returncode == 0 and "problems: 0" in val.stdout and "vertices=2" in val.stdout, val.stdout
    spec["sheets"][1]["cells"][0]["link"] = "L9 Nowhere"
    with pytest.raises(SystemExit, match="link 'L9 Nowhere' names no sheet"):
        ds.compile_spec(spec)


def test_add_links_to_an_existing_sheet_by_the_id_the_file_holds(tmp_path):
    r, out = build(one_box_per_role(), tmp_path)
    out.write_text(out.read_text(encoding="utf-8").replace('<diagram id="s" ', '<diagram id="Xy7-q" '), encoding="utf-8")
    before = out.read_bytes()
    bad = second_sheet()
    bad["sheets"][0]["cells"][0]["link"] = "L9 Nowhere"
    r = add(bad, out, tmp_path)
    assert r.returncode != 0 and "names no sheet" in r.stderr and out.read_bytes() == before
    more = second_sheet()
    more["sheets"][0]["cells"][0]["link"] = "L1 Test"
    r = add(more, out, tmp_path)
    assert r.returncode == 0, r.stderr
    text = out.read_text(encoding="utf-8")
    assert 'link="data:page/id,Xy7-q"' in text
    assert validate(out).returncode == 0
    out.write_text(text.replace("data:page/id,Xy7-q", "data:page/id,gone"), encoding="utf-8")
    val = validate(out)
    assert val.returncode == 1 and "t_human links to page id gone" in val.stdout, val.stdout


# --------------------------------------------------------------------------- C-deb-11: text cells and lane titles
def test_text_cells_and_lane_titles_warn_when_they_clip(capsys):
    cells = [{"type": "text", "id": "sub", "text": "a long subtitle " * 10, "x": 0, "y": 0, "w": 200, "h": 20},
             {"type": "lane", "id": "ln", "x": 0, "y": 40, "w": 600, "h": 30,
              "text": "A lane title that is far too long for its gutter"}]
    for theme in ("minimal", "colorful"):
        ds.compile_spec({"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
        err = capsys.readouterr().err
        assert "text 'sub' clips" in err and "lane title 'ln' clips" in err, err
    cells[0].update(w=1200)
    cells[1].update(h=120)
    ds.compile_spec({"sheets": [{"id": "s", "name": "L1 T", "width": 1300, "cells": cells}]})
    assert "clips" not in capsys.readouterr().err


# =========================================================================== v6
# --------------------------------------------------------------------------- lanes are draw.io swimlanes
def lane_spec(theme="minimal", **lane):
    ln = {"type": "lane", "id": "ln", "x": 20, "y": 20, "w": 700, "h": 200, "text": "Scripts decide"}
    ln.update(lane)
    cells = [ln, {"id": "a", "role": "decide", "text": "a", "x": 200, "y": 60},
             {"type": "edge", "id": "e", "from": "a", "to": "a"}]
    return {"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "width": 800, "height": 600, "cells": cells}]}


def test_row_lane_is_one_swimlane_with_its_title_strip_on_the_left():
    for theme, strip in (("minimal", "#F2F2F2"), ("bw", "#FFFFFF")):
        (_, xml, v), = ds.compile_spec(lane_spec(theme))
        st = styles(xml)
        d, bare = ds.parse_style(st["ln"])
        assert bare[0] == "swimlane" and d["direction"] == "north" and d["startSize"] == "150"
        assert (d["fillColor"], d["swimlaneFillColor"], d["strokeColor"], d["fontColor"]) == (strip, "#FFFFFF", "#000000", "#000000")
        assert d["fontStyle"] == "1" and d["collapsible"] == "0" and d["container"] == "1"
        assert re.search(r'id="s_ln" value="Scripts decide"', xml) and xml.count("Scripts decide") == 1
        assert re.search(r'id="s_a"[^>]*parent="s_ln"><mxGeometry x="180" y="40"', xml)    # relative to the lane
        assert v == 2                                                                      # lane and box
        assert ds.emitted_colours(xml) <= {"#000000", "#FFFFFF", "#F2F2F2"}
        assert ds.swimlane_strip(st["ln"]) == ("left", 150)
    (_, xml, v), = ds.compile_spec(lane_spec("colorful"))      # colorful: a rectangle and a title cell, as before
    assert "swimlane" not in xml and v == 3 and 'value="Scripts decide" style="text;' in xml


def test_column_lane_has_its_title_strip_on_top_and_gutter_sets_the_strip():
    (_, xml, _), = ds.compile_spec(lane_spec(w=220, h=500))
    d, _ = ds.parse_style(styles(xml)["ln"])
    assert "direction" not in d and d["startSize"] == "26"      # one line at font 10: 1 x 1.35 x 10 + 12
    assert ds.swimlane_strip(styles(xml)["ln"]) == ("top", 26)
    (_, xml, _), = ds.compile_spec(lane_spec(gutter=120))
    assert ds.parse_style(styles(xml)["ln"])[0]["startSize"] == "120"
    for bad in (-1, 700, "wide", True):
        with pytest.raises(SystemExit, match="gutter"):
            ds.compile_spec(lane_spec(gutter=bad))


def test_a_child_on_the_title_strip_warns_in_build_and_validate(capsys, tmp_path):
    spec = lane_spec()
    spec["sheets"][0]["cells"][1]["x"] = 100                  # strip ends at x 170
    ds.compile_spec(spec)
    assert "a overlaps the title strip of lane ln (x=100; contents start at x>=170)" in capsys.readouterr().err
    spec = lane_spec(w=220, h=500, x=150)
    spec["sheets"][0]["cells"][1].update(x=170, y=30)           # strip ends at y 46
    ds.compile_spec(spec)
    assert "a overlaps the title strip of lane ln (y=30; contents start at y>=46)" in capsys.readouterr().err
    ds.compile_spec(lane_spec())
    assert "title strip" not in capsys.readouterr().err
    r, out = build(lane_spec(), tmp_path)
    assert "title strip" not in validate(out).stdout
    edit_model(out, edit_cell("s_a", lambda c: c.find("mxGeometry").set("x", "20")))
    assert "cell s_a (a) overlaps the title strip of lane s_ln" in validate(out).stdout


def test_lane_titles_are_measured_across_the_strip(capsys):
    ds.compile_spec(lane_spec(h=30, text="A lane title that is far too long for its strip"))
    assert "lane title 'ln' clips" in capsys.readouterr().err
    ds.compile_spec(lane_spec(h=60, text="A lane title that is far too long for its strip"))
    assert "clips" not in capsys.readouterr().err                # two lines across 150 px fit in 60
    ds.compile_spec(lane_spec(w=220, h=500, gutter=20, text="A column title in two lines\nsecond"))
    assert "lane title 'ln' clips" in capsys.readouterr().err


@pytest.mark.parametrize("style,side,size", [
    ("swimlane;startSize=30;", "top", 30), ("swimlane;horizontal=0;startSize=30;", "left", 30),
    ("swimlane;direction=north;startSize=150;", "left", 150), ("swimlane;direction=south;", "right", 23),
    ("swimlane;horizontal=0;flipH=1;", "right", 23), ("shape=swimlane;direction=west;startSize=9;", "bottom", 9),
    ("rounded=0;container=1;", None, None)])
def test_swimlane_strip_side_follows_drawio(style, side, size):
    assert ds.swimlane_strip(style) == (None if side is None else (side, size))


# the cells version 5 wrote for a lane: a rectangle container and a title text cell inside it
V5_LANE_FILE = (
    '<mxfile host="app.diagrams.net" agent="drawio_spec.py" version="24.0" dsTheme="minimal" dsAccent="#1F5FBF" '
    'dsMaxColours="4"><diagram id="s" name="L1 T"><mxGraphModel dx="1400" dy="800" grid="1" gridSize="10" guides="1" '
    'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="800" pageHeight="400" math="0" '
    'shadow="0"><root><mxCell id="0"/><mxCell id="1" parent="0"/><mxCell id="s_ln" value="" style="rounded=0;'
    'whiteSpace=wrap;html=1;fontSize=9;fillColor=#FFFFFF;strokeColor=#000000;align=left;verticalAlign=top;spacingLeft=6;'
    'container=1;collapsible=0;dsTheme=minimal;dsAccent=#1F5FBF;dsMaxColours=4;" vertex="1" parent="1"><mxGeometry x="0" '
    'y="0" width="700" height="300" as="geometry"/></mxCell><mxCell id="s_auto2" value="Lane" style="text;html=1;'
    'whiteSpace=wrap;fontSize=10;align=center;verticalAlign=middle;fontStyle=1;fillColor=#F2F2F2;strokeColor=#000000;" '
    'vertex="1" parent="s_ln"><mxGeometry x="0" y="0" width="154" height="300" as="geometry"/></mxCell><mxCell id="s_a" '
    'value="a" style="rounded=0;whiteSpace=wrap;html=1;fontSize=9;fillColor=#FFFFFF;strokeColor=#000000;fontColor=#000000;'
    'align=center;verticalAlign=middle;spacing=4;" vertex="1" parent="s_ln"><mxGeometry x="200" y="20" width="120" '
    'height="60" as="geometry"/></mxCell><mxCell id="s_b" value="b" style="rounded=0;whiteSpace=wrap;html=1;fontSize=9;'
    'fillColor=#FFFFFF;strokeColor=#000000;fontColor=#000000;align=center;verticalAlign=middle;spacing=4;" vertex="1" '
    'parent="s_ln"><mxGeometry x="200" y="120" width="120" height="60" as="geometry"/></mxCell><mxCell id="s_lg" '
    'value="Legend" style="rounded=0;whiteSpace=wrap;html=1;fillColor=#F2F2F2;strokeColor=#000000;fontColor=#000000;'
    'fontSize=10;fontStyle=1;align=left;verticalAlign=top;spacingLeft=8;spacingTop=2;container=1;collapsible=0;'
    'dsLegend=1;" vertex="1" parent="s_ln"><mxGeometry x="400" y="20" width="220" height="58" as="geometry"/></mxCell>'
    '<mxCell id="s_auto6" value="component" style="rounded=0;html=1;fillColor=#FFFFFF;strokeColor=#000000;'
    'whiteSpace=wrap;fontSize=9;fontColor=#000000;labelPosition=right;verticalLabelPosition=middle;align=left;'
    'verticalAlign=middle;labelWidth=150;spacingLeft=8;dsLegend=1;" vertex="1" parent="s_lg"><mxGeometry x="10" y="29" '
    'width="44" height="20" as="geometry"/></mxCell><mxCell id="s_e" value="" style="edgeStyle=orthogonalEdgeStyle;'
    'rounded=1;html=1;fontSize=8;strokeColor=#000000;strokeWidth=1;endArrow=block;endFill=1;" edge="1" parent="1" '
    'source="s_a" target="s_b"><mxGeometry relative="1" as="geometry"></mxGeometry></mxCell></root></mxGraphModel>'
    '</diagram></mxfile>')


def test_a_file_with_rectangle_lanes_still_validates_lists_and_accepts_add(tmp_path):
    out = tmp_path / "v5.drawio"
    out.write_text(V5_LANE_FILE, encoding="utf-8")
    val = validate(out)
    assert "problems: 0" in val.stdout and "vertices=4 legend=2" in val.stdout and "title strip" not in val.stdout, val.stdout
    assert "vertices=4" in list_file(out).stdout and "warn" not in list_file(out).stdout
    more = {"sheets": [{"id": "t", "name": "L2 More", "width": 800, "height": 400, "cells": lane_spec()["sheets"][0]["cells"]}]}
    r = add(more, out, tmp_path)
    assert r.returncode == 0, r.stderr
    text = out.read_text(encoding="utf-8")
    assert 'id="t_ln" value="Scripts decide" style="swimlane;' in text and 'id="s_auto2" value="Lane"' in text
    val = validate(out)
    assert "problems: 0" in val.stdout and "theme minimal" in val.stdout, val.stdout


def test_swimlane_colours_are_audited_and_refused_in_a_box_style(tmp_path):
    r, out = build(lane_spec("bw"), tmp_path)
    assert validate(out).returncode == 0
    edit_model(out, edit_cell("s_ln", lambda c: c.set("style", c.get("style") + "swimlaneFillColor=#F2F2F2;")))
    val = validate(out)
    assert val.returncode == 1 and "#F2F2F2" in val.stdout
    spec = lane_spec()
    spec["sheets"][0]["cells"][1]["style"] = "swimlaneFillColor=#FFFFFF;"
    r, out = build(spec, tmp_path)
    assert r.returncode != 0 and "swimlaneFillColor" in r.stderr


# --------------------------------------------------------------------------- the accent wins in colorful too
def test_colorful_accent_wins_over_overrides_and_matches_its_legend_sample():
    cells = [{"id": "a", "text": "a", "x": 0, "y": 0, "accent": True, "stroke": "#00FF00",
              "style": "strokeColor=#00FF00;strokeWidth=9;"},
             {"id": "s", "shape": "database", "text": "s", "x": 0, "y": 200, "accent": True, "style": "strokeWidth=9;"},
             {"id": "b", "text": "b", "x": 0, "y": 100, "style": "strokeColor=#00FF00;strokeWidth=9;"},
             {"type": "edge", "id": "e", "from": "a", "to": "b", "accent": True, "width": 8,
              "style": "strokeWidth=9;strokeColor=#00FF00;"},
             {"type": "legend", "x": 300, "y": 0}]
    (_, xml, _), = ds.compile_spec({"theme": "colorful", "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    st = styles(xml)
    for k in ("a", "s", "e"):
        d, _ = ds.parse_style(st[k])
        assert (d["strokeColor"], d["strokeWidth"]) == ("#1F5FBF", "2"), k
        assert st[k].count("strokeWidth=") == 1 and st[k].count("strokeColor=") == 1, k
    assert st["b"].endswith("strokeColor=#00FF00;strokeWidth=9;")      # no accent: the raw style still wins
    sample = ds.parse_style(re.search(r'value="highlighted" style="([^"]*)"', xml).group(1))[0]
    assert (sample["strokeColor"], sample["strokeWidth"]) == ("#1F5FBF", "2")


# --------------------------------------------------------------------------- effective font and spacing
def auto_h(capsys, **box):
    c = {"id": "a", "text": "Head\n• a first bullet that is long\n• second", "x": 0, "y": 0, "w": 160, "h": "auto"}
    c.update(box)
    ds.compile_spec({"sheets": [{"id": "s", "name": "L1 T", "cells": [c]}]})
    return re.search(r"box 'a' h=(\d+) \((\d+) lines, font (\S+)\)", capsys.readouterr().out).groups()


def test_font_and_spacing_in_the_raw_style_measure_like_the_cell_fields(capsys):
    assert auto_h(capsys, font=14) == auto_h(capsys, style="fontSize=14;")
    assert auto_h(capsys, w=130) == auto_h(capsys, style="spacingLeft=30;")          # 30 px less room either way
    assert auto_h(capsys, w=130) == auto_h(capsys, style="spacingRight=30;")
    base = int(auto_h(capsys)[0])
    assert int(auto_h(capsys, style="spacingTop=20;")[0]) == base + 20
    assert int(auto_h(capsys, style="spacing=10;")[0]) > base
    big = auto_h(capsys, w=130, style="fontSize=36;spacingLeft=70;")                   # the reviewer's box
    assert big[2] == "36" and int(big[0]) > 300
    # the clip warning: the same numbers whichever way the font is given
    warns = []
    for c in ({"font": 14}, {"style": "fontSize=14;"}):
        box = {"id": "a", "text": "Head\n• one\n• two\n• three", "x": 0, "y": 0, "w": 160, "h": 40}
        box.update(c)
        ds.compile_spec({"sheets": [{"id": "s", "name": "L1 T", "cells": [box]}]})
        warns.append([w for w in capsys.readouterr().err.splitlines() if "clips" in w])
    assert warns[0] == warns[1] and len(warns[0]) == 1 and "font 14" in warns[0][0]
    assert "do not see" not in "".join(warns[0])


def test_frame_headline_room_uses_the_raw_font(capsys):
    cells = [{"id": "f", "square": True, "text": "Set", "x": 0, "y": 0, "w": 300, "h": 200, "style": "fontSize=30;"},
             {"id": "k", "text": "k", "x": 20, "y": 30, "w": 100, "h": 40}]
    ds.compile_spec({"sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    assert "k starts above the headline of frame f" in capsys.readouterr().err
    cells[0]["style"] = ""
    ds.compile_spec({"sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    assert "headline" not in capsys.readouterr().err


# --------------------------------------------------------------------------- the legend shows each box's outline
@pytest.mark.parametrize("theme", ["minimal", "bw"])
@pytest.mark.parametrize("role,style,key,value", [
    ("propose", "rounded=0;", "rounded", "0"), ("outside", "double=0;", "double", "0"),
    ("plain", "strokeWidth=2;", "strokeWidth", "2"), ("decide", "size=20;", "size", "20"),
    ("plain", "dashed=1;dashPattern=1 4;", "dashPattern", "1 4"), ("guard", "flipH=1;", "flipH", "1")])
def test_legend_groups_and_draws_boxes_by_their_outline(theme, role, style, key, value, capsys):
    cells = [{"id": "a", "role": role, "text": "a", "x": 0, "y": 0, "style": style},
             {"id": "b", "role": role, "text": "b", "x": 0, "y": 100},
             {"type": "legend", "id": "lg", "x": 300, "y": 0, "w": 300}]
    (_, xml, _), = ds.compile_spec({"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    rows = [ds.parse_style(m)[0] for m in re.findall(r'style="([^"]*labelPosition=right;[^"]*)"', xml)]
    assert len(rows) == 2                                          # one row per outline
    assert sum(1 for r in rows if r.get(key) == value) == 1
    box = ds.parse_style(styles(xml)["a"])[0]
    drawn = [r for r in rows if r.get(key) == value][0]
    for k in ds.OUTLINE_KEYS:
        if k in box:
            assert drawn.get(k) == box[k], k                       # the sample carries the box's outline
    err = capsys.readouterr().err
    assert "painted 2 different ways" in err
    if style in ("rounded=0;", "double=0;"):
        assert "removes what marks" in err


def test_legend_outline_in_colorful_follows_the_raw_style():
    cells = [{"id": "a", "role": "propose", "text": "a", "x": 0, "y": 0, "style": "rounded=0;"},
             {"id": "b", "role": "decide", "text": "b", "x": 0, "y": 100},
             {"type": "legend", "id": "lg", "x": 300, "y": 0, "w": 300}]
    (_, xml, _), = ds.compile_spec({"theme": "colorful", "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    rows = re.findall(r'value="([^"]*)" style="([^"]*labelPosition=right;[^"]*)"', xml)
    got = {v: ds.parse_style(s)[0]["rounded"] for v, s in rows}
    assert got == {"an LLM or skill proposes": "0", "code decides": "1"}


# --------------------------------------------------------------------------- bulleted boxes at the top left in every theme
@pytest.mark.parametrize("theme", ["minimal", "bw", "colorful"])
@pytest.mark.parametrize("name", ["example_L1_minimal.json", "example_plugin_3_levels_minimal.json"])
def test_example_bullets_sit_at_the_top_left_in_every_theme(name, theme):
    spec = json.load(open(os.path.join(REFS, name), encoding="utf-8"))
    xmls = [x for _, x, _ in ds.compile_spec(spec, theme=theme)]
    n = 0
    for sh, xml in zip(spec["sheets"], xmls):
        st = {k.split("_", 1)[1]: v for k, v in re.findall(r'(?:<mxCell id="|id=")(%s_[^"]+)"[^>]*?style="([^"]*)"' % sh["id"], xml)}
        st.update({m.group(1): m.group(2) for m in re.finditer(
            r'<UserObject label="[^"]*" link="[^"]*" id="%s_([^"]+)"><mxCell style="([^"]*)"' % sh["id"], xml)})
        for c in sh["cells"]:
            if c.get("type", "box") == "box" and "\n" in c.get("text", ""):
                d = ds.parse_style(st[c["id"]])[0]
                assert (d["align"], d["verticalAlign"]) == ("left", "top"), (sh["id"], c["id"], theme)
                n += 1
    assert n == (11 if name == "example_L1_minimal.json" else 96)


# =========================================================================== v7
# --------------------------------------------------------------------------- specs without theme-era keys build as before
# Each spec uses only keys the script had before themes existed. BASELINE_DIGESTS holds the SHA-256 of the
# file that script (the colorful baseline) writes for it; a colorful build must write the same bytes.
BASELINE_SPECS = {
    "grid_col_with_own_w": {"sheets": [{"id": "s", "name": "L1 T", "grid": {"x0": 20, "pitch": 100}, "cells": [
        {"id": "b", "col": 1, "w": 80, "y": 10, "h": 40, "text": "B"}]}]},
    "grid_full": {"sheets": [{"id": "g", "name": "L2 Grid", "width": 1400, "height": 700,
                              "grid": {"x0": 180, "pitch": 320, "col_width": 300}, "cells": [
        {"type": "lane", "id": "own", "role": "human", "text": "Owner", "x": 20, "y": 80, "w": 1300, "h": 200},
        {"type": "lane", "id": "oth", "role": "decide", "text": "Other lane", "gutter": 120, "x": 20, "y": 290,
         "w": 1300, "h": 200, "fill": "#EEEEEE"},
        {"type": "text", "id": "hdr", "text": "Stages", "col": 0, "span": 2, "y": 40, "h": 30},
        {"type": "text", "id": "note", "text": "a note", "x": 900, "y": 40, "w": 200, "h": 30, "bold": False,
         "align": "center", "font": 11},
        {"id": "a", "role": "human", "text": "A\n• one\n• two", "col": 0, "y": 100, "h": 80, "align": "left",
         "valign": "top"},
        {"id": "b", "role": "propose", "text": "B spans", "col": 1, "span": 2, "y": 100, "h": 60, "bold": True},
        {"id": "c", "role": "decide", "text": "C", "col": 0, "w": 140, "y": 310, "h": 50, "style": "fontStyle=2"},
        {"id": "d", "role": "record", "text": "D", "x": 900, "y": 310, "w": 160, "h": 50, "fill": "#FFFFFF",
         "stroke": "#333333", "font": 12},
        {"id": "f", "role": "plain", "text": "Frame", "x": 500, "y": 380, "w": 300, "h": 100, "square": True,
         "dashed": True},
        {"type": "edge", "from": "a", "to": "c", "label": "down", "exit": [0.5, 1], "entry": [0.5, 0], "role": "decide"},
        {"type": "edge", "id": "e2", "from": "b", "to": "d", "points": [[825, 200], [825, 335]], "label": "via channel",
         "label_at": -0.5, "label_offset": [0, -8], "width": 2, "dashed": True, "color": "#123456", "font": 9},
        {"type": "edge", "from": "c", "to": "f", "style": "endArrow=open;"}]}]},
    "no_grid_defaults": {"sheets": [{"id": "n", "name": "L1 N", "cells": [
        {"id": "x", "text": "default width and height", "x": 10, "y": 10},
        {"type": "text", "id": "t", "text": "text cell", "x": 10, "y": 100, "w": 120, "h": 20},
        {"type": "lane", "id": "tall", "text": "Tall lane", "x": 300, "y": 0, "w": 200, "h": 400},
        {"id": "y", "role": "guard", "text": "in the tall lane", "x": 320, "y": 50, "w": 150, "h": 40},
        {"type": "edge", "from": "x", "to": "y"}]}]},
    "xml_valid_specials": {"sheets": [{"id": "q", "name": "L1 Q", "cells": [
        {"id": "a>b", "text": "Tom & Jerry <b>bold</b>", "x": 10, "y": 10, "w": 160, "h": 40,
         "style": "fontFamily='Helvetica';"},
        {"id": "c'd", "text": "c", "x": 10, "y": 100},
        {"type": "edge", "from": "a>b", "to": "c'd", "label": "x > y"}]}]},
}
BASELINE_DIGESTS = {
    "grid_col_with_own_w": "47f8fea963a2afb593fd100eab9506895f0a35ae73f2ee27c3c6ed8ba4522ed4",
    "grid_full": "deb8f960bd82a3094cfeaaf2b88b0343ceaa6c0bb16a70d56015084203f5746d",
    "no_grid_defaults": "4db644ef6a99eff1b67484253e7b649ea3e9f7eec7f595c73cff5a62475acc0d",
    "xml_valid_specials": "29af1c32bf2b1566b4e94b2feceade6f283b7b13aebd2a1160d16e9145eebdd7",
}


@pytest.mark.parametrize("name", sorted(BASELINE_SPECS))
def test_colorful_build_of_a_pre_theme_spec_equals_the_baseline_script(name, tmp_path):
    import hashlib
    r, out = build(BASELINE_SPECS[name], tmp_path, "--theme", "colorful")
    assert r.returncode == 0, r.stderr
    assert hashlib.sha256(out.read_bytes()).hexdigest() == BASELINE_DIGESTS[name]


def test_a_grid_value_is_needed_only_by_the_cell_that_takes_it():
    def sheet(grid, *cells):
        return {"sheets": [{"id": "s", "name": "L1 T", "grid": grid, "cells": list(cells)}]}
    for theme in ("minimal", "bw", "colorful"):
        (_, xml, _), = ds.compile_spec(sheet({"x0": 20, "pitch": 100}, {"id": "b", "col": 1, "w": 80, "y": 10, "h": 40}), theme)
        assert 'x="120" y="10" width="80" height="40"' in xml, theme
    (_, xml, _), = ds.compile_spec(sheet({"col_width": 90}, {"id": "b", "x": 5, "span": 1, "y": 0, "h": 40}))
    assert 'x="5" y="0" width="90"' in xml                     # span 1 needs col_width only
    with pytest.raises(SystemExit, match="gives no w.*set grid.col_width above 0"):
        ds.compile_spec(sheet({"x0": 20, "pitch": 100}, {"id": "b", "col": 1, "y": 10, "h": 40}))
    with pytest.raises(SystemExit, match="no pitch above 0"):
        ds.compile_spec(sheet({"col_width": 100}, {"id": "b", "col": 1, "w": 80, "y": 10, "h": 40}))
    with pytest.raises(SystemExit, match="col_width and grid.pitch"):
        ds.compile_spec(sheet({"col_width": 100}, {"id": "b", "x": 0, "span": 2, "y": 10, "h": 40}))


# --------------------------------------------------------------------------- paint the colour count cannot see
def set_style(style):
    def fn(c):
        c.set("style", style)
    return fn


def add_shadow(c):
    c.set("style", c.get("style") + "shadow=1;")


def page_shadow(m):
    m.set("shadow", "1")


@pytest.mark.parametrize("theme", ["bw", "minimal"])
@pytest.mark.parametrize("edit,key", [
    (edit_cell("s_plain", set_style("blue;html=1;")), "named style 'blue'"),
    (edit_cell("s_plain", add_shadow), "shadow=1"),
    (page_shadow, "the page draws shadows"),
    (edit_cell("s_plain", set_label('<span style="color: rgba(0, 0, 0, 0.4)">x</span>')), "rgba(0, 0, 0, 0.4)"),
    (edit_cell("s_plain", set_label('<font color="rgb(0 0 0 / 40%)">x</font>')), "rgb(0 0 0 / 40%)"),
    (edit_cell("s_plain", set_label('<div style="opacity: 0.5">x</div>')), "opacity: 0.5"),
])
def test_paint_a_colour_count_cannot_see_breaks_a_minimal_or_bw_file(theme, edit, key, tmp_path):
    r, out = build(one_box_per_role(theme=theme), tmp_path)
    edit_model(out, edit)
    val = validate(out)
    assert val.returncode == 1 and "ERR" in val.stdout and key in val.stdout, val.stdout
    listed = list_file(out).stdout
    assert "warn" in listed and key in listed
    before = out.read_bytes()
    r = add(second_sheet(), out, tmp_path)
    assert r.returncode != 0 and key in r.stderr and out.read_bytes() == before
    assert not os.path.exists(str(out) + ".bak")


def test_paint_audit_reads_compressed_sheets_and_passes_what_the_compiler_writes(tmp_path):
    r, out = build(one_box_per_role(theme="bw"), tmp_path)
    edit_model(out, edit_cell("s_plain", set_style("blue;html=1;")))
    compress_sheets(out)
    assert validate(out).returncode == 1
    for style in ("text;html=1;", "swimlane;html=1;", "rhombus;html=1;"):
        assert ds.style_breaches(style) == [], style
    # an allow-list: a key the compiler never writes is a breach whatever its value
    assert ds.style_breaches("shadow=0;html=1;") and ds.label_breaches('<span style="opacity: 1">x</span>')
    assert ds.label_breaches("<b>head</b><br>line<br/>") == []
    assert ds.label_breaches('&lt;span style="opacity:0.2"&gt;') == []          # escaped markup is text


# --------------------------------------------------------------------------- the theme record after a style without ';'
@pytest.mark.parametrize("style", ["fontFamily=Arial", "fontFamily=Arial;"])
def test_theme_record_after_a_style_without_its_last_semicolon(style, tmp_path):
    spec = {"theme": "colorful", "accent_color": "#DC0000", "sheets": [{"id": "s", "name": "L1 T", "cells": [
        {"id": "a", "text": "a", "x": 0, "y": 0, "style": style}, {"id": "b", "text": "b", "x": 0, "y": 100}]}]}
    r, out = build(spec, tmp_path)
    assert r.returncode == 0, r.stderr
    text = out.read_text(encoding="utf-8")
    assert "fontFamily=Arial;dsTheme=colorful;dsAccent=#DC0000;" in text
    out.write_text(re.sub(r' ds(Theme|Accent|MaxColours)="[^"]*"', "", text), encoding="utf-8")   # a save in draw.io
    assert ds.file_theme(ds.read_file(str(out))) == ("colorful", "#DC0000", None, True)
    more = {"sheets": [{"id": "t", "name": "L2 T", "cells": [{"id": "c", "text": "c", "x": 0, "y": 0, "accent": True}]}]}
    r = add(more, out, tmp_path)
    assert r.returncode == 0, r.stderr
    new = out.read_text(encoding="utf-8").split('name="L2 T"')[1]
    assert "strokeColor=#DC0000;" in new and "#1F5FBF" not in new


# --------------------------------------------------------------------------- lane body white; text on black
def test_lane_body_is_white_and_uncoloured_text_on_black_turns_white(capsys):
    cells = [{"type": "lane", "id": "ln", "x": 0, "y": 0, "w": 800, "h": 300, "text": "Lane", "fill": "#000000"},
             {"id": "f", "square": True, "text": "Set", "x": 200, "y": 20, "w": 300, "h": 250, "fill": "#000000"},
             {"type": "text", "id": "t", "text": "caption", "x": 220, "y": 60, "w": 100, "h": 20},
             {"id": "p", "shape": "actor", "role": "human", "text": "Max", "x": 350, "y": 60, "w": 40, "h": 70},
             {"id": "q", "shape": "actor", "role": "human", "text": "Own", "x": 420, "y": 60, "w": 40, "h": 70,
              "style": "fontColor=#000000;"},
             {"type": "text", "id": "t2", "text": "outside", "x": 550, "y": 60, "w": 100, "h": 20},
             {"id": "p2", "shape": "actor", "role": "human", "text": "Out", "x": 680, "y": 60, "w": 40, "h": 70}]
    for theme in ("minimal", "bw"):
        (_, xml, _), = ds.compile_spec({"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
        st = {k: ds.parse_style(v)[0] for k, v in styles(xml).items()}
        assert st["ln"]["swimlaneFillColor"] == "#FFFFFF"
        assert "fill='#000000' ignored; in theme %s a lane's body is always white" % theme in capsys.readouterr().err
        assert st["t"]["fontColor"] == "#FFFFFF" and st["p"]["fontColor"] == "#FFFFFF"    # on the black frame
        assert st["q"]["fontColor"] == "#000000"                                            # its own colour
        assert st["t2"].get("fontColor", "#000000") == "#000000" and st["p2"]["fontColor"] == "#000000"
        assert ds.emitted_colours(xml) <= {"#000000", "#FFFFFF", "#F2F2F2"}


# --------------------------------------------------------------------------- the label width draw.io really leaves
def test_label_width_follows_the_bars_size_and_labelWidth(capsys):
    small = auto_h(capsys, role="decide", text="Approval owner", w=140, style="size=8;")
    big = auto_h(capsys, role="decide", text="Approval owner", w=140, style="size=42;")
    assert small[1] == "1" and int(big[1]) >= 2 and int(big[0]) > int(small[0])
    assert auto_h(capsys, role="decide", text="Approval owner", w=140) == small           # size 8 is the catalogue's
    assert auto_h(capsys, role="decide", text="Approval owner", w=140, style="size=0.3;fixedSize=0;")[1] != "1"
    wide = auto_h(capsys, text="Approval owner", w=400)
    narrow = auto_h(capsys, text="Approval owner", w=400, style="labelWidth=40;")
    assert wide[1] == "1" and int(narrow[1]) >= 3
    # an explicit height warns on the same measure
    box = {"id": "a", "role": "decide", "text": "Approval owner", "x": 0, "y": 0, "w": 140, "h": 25}
    for style, clips in (("size=8;", False), ("size=42;", True)):
        ds.compile_spec({"sheets": [{"id": "s", "name": "L1 T", "cells": [dict(box, style=style)]}]})
        assert ("text clips" in capsys.readouterr().err) == clips, style
    ds.compile_spec({"sheets": [{"id": "s", "name": "L1 T", "cells": [dict(box, w=100, style="size=45;")]}]})
    assert "no text area remains" in capsys.readouterr().err


# --------------------------------------------------------------------------- a default spelt out is the same look
@pytest.mark.parametrize("theme", ["minimal", "bw"])
def test_an_outline_default_spelt_out_is_the_same_look(theme, capsys):
    cells = [{"id": "h", "role": "human", "shape": "decision", "text": "h", "x": 0, "y": 0, "w": 80, "h": 60},
             {"id": "d", "role": "decide", "shape": "decision", "text": "d", "x": 0, "y": 100, "w": 80, "h": 60,
              "style": "strokeWidth=1;"},
             {"id": "p1", "text": "p1", "x": 200, "y": 0},
             {"id": "p2", "text": "p2", "x": 200, "y": 100, "style": "strokeWidth=1;"},
             {"id": "p3", "text": "p3", "x": 200, "y": 200, "style": "strokeWidth=1.0;"},
             {"type": "legend", "id": "lg", "x": 400, "y": 0, "w": 300}]
    (_, xml, _), = ds.compile_spec({"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    err = capsys.readouterr().err
    assert "boxes 'h' (role human) and 'd' (role decide) are drawn alike" in err
    assert "different ways" not in err
    assert legend_rows(xml).count("component") == 1
    assert ds.outline_of({"strokeWidth": "2.0"}) == ds.outline_of({"strokeWidth": "2"}) == (("strokeWidth", "2"),)


# --------------------------------------------------------------------------- XML-sensitive ids and styles
@pytest.mark.parametrize("theme", ["minimal", "bw", "colorful"])
def test_ids_and_styles_with_xml_characters_build(theme, tmp_path):
    spec = {"theme": theme, "sheets": [
        {"id": "s", "name": "L1 T", "cells": [
            {"id": "R&D", "text": "R&D", "x": 0, "y": 0, "link": "L2 T"},
            {"id": "b", "text": "b", "x": 0, "y": 100, "style": 'fontFamily="Times New Roman", serif;'},
            {"type": "edge", "id": "e&1", "from": "R&D", "to": "b"}]},
        {"id": "t", "name": "L2 T", "cells": [{"id": "c<1", "text": "c", "x": 0, "y": 0}]}]}
    r, out = build(spec, tmp_path)
    assert r.returncode == 0, r.stderr
    cells = {c.get("id"): c for c in ds.model_cells(ds.read_file(str(out)))}
    assert cells["s_R&D"].wrapper.get("link") == "data:page/id,t"
    assert (cells["s_e&1"].get("source"), cells["s_e&1"].get("target")) == ("s_R&D", "s_b")
    assert 'fontFamily="Times New Roman", serif' in cells["s_b"].get("style") and "t_c<1" in cells
    assert validate(out).returncode == 0
    assert ds.attr("a&amp;b") == "a&amp;b" and ds.attr('a&b"<c>') == "a&amp;b&quot;&lt;c>"


# --------------------------------------------------------------------------- one index of sheet names and ids
def duplicate_first_sheet(path, sid, name):
    import copy
    root = ds.read_file(str(path))
    d = copy.deepcopy(root.find("diagram"))
    d.set("id", sid)
    d.set("name", name)
    root.append(d)
    ds.ET.ElementTree(root).write(str(path), encoding="utf-8", xml_declaration=False)


def test_add_refuses_an_ambiguous_position_or_link_and_validate_reports_duplicates(tmp_path):
    r, out = build(one_box_per_role(), tmp_path)
    duplicate_first_sheet(out, "s2", "L1 Test")                 # two sheets named 'L1 Test'
    val = validate(out)
    assert val.returncode == 1 and "2 sheets are named 'L1 Test'" in val.stdout
    before = out.read_bytes()
    r = add(second_sheet(), out, tmp_path, "--position", "after:L1 Test")
    assert r.returncode != 0 and "2 sheets named 'L1 Test'" in r.stderr
    linked = second_sheet()
    linked["sheets"][0]["cells"][0]["link"] = "L1 Test"
    r = add(linked, out, tmp_path)
    assert r.returncode != 0 and "names 2 sheets" in r.stderr
    assert out.read_bytes() == before and not os.path.exists(str(out) + ".bak")
    r, out = build(one_box_per_role(), tmp_path)
    duplicate_first_sheet(out, "s", "L1 Copy")                  # two names, one diagram id
    val = validate(out)
    assert val.returncode == 1 and "2 sheets have the diagram id 's'" in val.stdout
    before = out.read_bytes()
    linked["sheets"][0]["cells"][0]["link"] = "L1 Copy"
    r = add(linked, out, tmp_path)
    assert r.returncode != 0 and "which 2 sheets" in r.stderr and out.read_bytes() == before
    assert add(second_sheet(), out, tmp_path, "--position", "after:L1 Copy").returncode == 0     # names are unique


# --------------------------------------------------------------------------- the worked example links every level down
def test_three_level_example_links_l1_to_l2_and_l2_to_l3():
    spec = json.load(open(os.path.join(REFS, "example_plugin_3_levels_minimal.json"), encoding="utf-8"))
    (_, l1, _), (_, l2, _), _ = ds.compile_spec(spec)
    # L1: 10 boxes and its 3 lanes; L2: 4 stage headers and its 6 lanes
    assert l1.count('link="data:page/id,l2"') == 13 and l2.count('link="data:page/id,l3"') == 10


# --------------------------------------------------------------------------- a legend title of several lines
@pytest.mark.parametrize("theme", ["minimal", "bw", "colorful"])
def test_a_legend_title_of_three_lines_pushes_the_rows_down(theme, capsys):
    def rows_y(title):
        cells = [{"id": "a", "text": "a", "x": 0, "y": 0},
                 {"type": "legend", "id": "lg", "x": 300, "y": 100, "w": 220, "title": title}]
        (_, xml, _), = ds.compile_spec({"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
        y = int(re.search(r'value="component" style="[^"]*"[^>]*><mxGeometry x="\d+" y="(\d+)"', xml).group(1))
        h = int(re.search(r'id="s_lg"[^>]*><mxGeometry x="\d+" y="\d+" width="\d+" height="(\d+)"', xml).group(1))
        return y - (0 if theme != "colorful" else 100), h
    y1, h1 = rows_y("Legend")
    y3, h3 = rows_y("Legend\nsecond line\nthird line")
    assert y3 - y1 == h3 - h1 == 27                          # two more lines at font 10: 2 x 13.5
    assert y3 >= 3 * 13.5 + 12


# --------------------------------------------------------------------------- strip size of a named swimlane
@pytest.mark.parametrize("style,warns", [("swimlane;", False), ("shape=swimlane;", True),
                                         ("swimlane;startSize=40;", True), ("shape=swimlane;startSize=20;", False)])
def test_validate_reads_the_named_swimlane_strip_as_23(style, warns, tmp_path):
    r, out = build(lane_spec(), tmp_path)

    def edit(m):
        for c in m.iter("mxCell"):
            if c.get("id") == "s_ln":
                c.set("style", style + "html=1;container=1;")
            if c.get("id") == "s_a":
                c.find("mxGeometry").set("y", "30")
    edit_model(out, edit)
    assert ("overlaps the title strip" in validate(out).stdout) == warns


# --------------------------------------------------------------------------- colorful catalogue shapes stay centred
def test_colorful_centred_catalogue_shape_keeps_its_text_centred(capsys):
    cells = [{"id": "d", "shape": "decision", "role": "decide", "text": "ok?", "x": 0, "y": 0, "w": 120, "h": 80,
              "style": "align=right;verticalAlign=bottom;"},
             {"id": "e", "shape": "cloud", "text": "net", "x": 0, "y": 100, "w": 120, "h": 80, "style": "align=center;"},
             {"id": "p", "role": "decide", "text": "p", "x": 200, "y": 0, "style": "align=right;"}]
    (_, xml, _), = ds.compile_spec({"theme": "colorful", "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    st = {k: ds.parse_style(v)[0] for k, v in styles(xml).items()}
    assert (st["d"]["align"], st["d"]["verticalAlign"]) == ("center", "middle")
    assert (st["e"]["align"], st["e"]["verticalAlign"]) == ("center", "middle")
    assert st["p"]["align"] == "right"                                   # no catalogue shape: untouched
    err = capsys.readouterr().err
    assert "style align=right;verticalAlign=bottom conflicts with what the compiler keeps on a decision" in err
    assert "cell 'e'" not in err


# --------------------------------------------------------------------------- lane orientation
def test_lane_orientation_sets_row_or_column(capsys):
    def lane(**kw):
        ln = {"type": "lane", "id": "ln", "x": 0, "y": 0, "w": 280, "h": 160, "text": "Actor"}
        ln.update(kw)
        return ln
    child = {"id": "a", "text": "a", "x": 20, "y": 40, "w": 100, "h": 40}
    (_, xml, _), = ds.compile_spec({"sheets": [{"id": "s", "name": "L1 T", "cells": [lane(orientation="column"), child]}]})
    d, _ = ds.parse_style(styles(xml)["ln"])
    assert "direction" not in d and d["startSize"] == "26"
    assert "overlaps the title strip" not in capsys.readouterr().err
    (_, xml, _), = ds.compile_spec({"sheets": [{"id": "s", "name": "L1 T", "cells": [
        lane(w=200, h=500, orientation="row", gutter=60), dict(child, y=100)]}]})
    d, _ = ds.parse_style(styles(xml)["ln"])
    assert d["direction"] == "north" and d["startSize"] == "60"
    assert "a overlaps the title strip of lane ln (x=20; contents start at x>=60)" in capsys.readouterr().err
    with pytest.raises(SystemExit, match='orientation .*"row" or "column"'):
        ds.compile_spec({"sheets": [{"id": "s", "name": "L1 T", "cells": [lane(orientation="rows")]}]})


# --------------------------------------------------------------------------- untitled lanes; a title with no room
def test_an_untitled_lane_has_no_strip_and_a_title_without_room_warns(capsys):
    (_, xml, _), = ds.compile_spec({"sheets": [{"id": "s", "name": "T", "cells": [
        {"type": "lane", "id": "ln", "x": 0, "y": 0, "w": 600, "h": 100},
        {"id": "a", "text": "a", "x": 10, "y": 20, "w": 100, "h": 40}]}]})
    assert ds.parse_style(styles(xml)["ln"])[0]["startSize"] == "0"
    assert capsys.readouterr().err == ""
    for ln in ({"w": 600, "h": 100, "gutter": 0}, {"w": 600, "h": 100, "gutter": 10},
               {"w": 200, "h": 400, "gutter": 5}):
        ds.compile_spec({"sheets": [{"id": "s", "name": "L1 T", "cells": [
            dict({"type": "lane", "id": "ln", "x": 0, "y": 0, "text": "Owner"}, **ln)]}]})
        assert "lane title 'ln' has no room" in capsys.readouterr().err, ln


# --------------------------------------------------------------------------- lanes get the frame's padding checks
def test_lane_children_keep_10_px_from_the_sides_without_a_strip(capsys):
    cells = [{"type": "lane", "id": "ln", "x": 0, "y": 0, "w": 700, "h": 200, "text": "Row lane"},
             {"id": "top", "text": "t", "x": 160, "y": 4, "w": 100, "h": 40},
             {"id": "bot", "text": "b", "x": 300, "y": 150, "w": 100, "h": 46},
             {"id": "rgt", "text": "r", "x": 590, "y": 60, "w": 104, "h": 40},
             {"id": "ok", "text": "ok", "x": 150, "y": 60, "w": 100, "h": 40},
             {"id": "grow", "text": "grow\n• one\n• two\n• three\n• four\n• five",
              "x": 420, "y": 150, "w": 120, "h": "auto"}]
    ds.compile_spec({"sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    err = capsys.readouterr().err
    assert "top is 4 px from the top side of lane ln (minimum 10)" in err
    assert "bot is 4 px from the bottom side of lane ln" in err
    assert "rgt is 6 px from the right side of lane ln" in err
    assert "auto-height box grow grows past the bottom of lane ln" in err
    assert "ok is" not in err and "left side of lane" not in err       # the strip side is check_lanes' part


# --------------------------------------------------------------------------- column lanes in colorful
@pytest.mark.parametrize("orientation,warns", [(None, True), ("row", False), ("column", False)])
def test_a_column_lane_in_colorful_warns_only_when_inferred(orientation, warns, capsys):
    ln = {"type": "lane", "id": "ln", "x": 0, "y": 0, "w": 200, "h": 400, "text": "Owner"}
    if orientation:
        ln["orientation"] = orientation
    ds.compile_spec({"theme": "colorful", "sheets": [{"id": "s", "name": "L1 T", "cells": [ln]}]})
    assert ('without "orientation": "column" colorful puts its title in a gutter' in capsys.readouterr().err) == warns


def lane_geoms(xml):
    return {m.group(1): tuple(int(v) for v in m.groups()[1:]) for m in re.finditer(
        r'<mxCell id="s_([^"]+)" value="[^"]*" style="[^"]*" vertex="1" parent="1"><mxGeometry x="(\d+)" y="(\d+)" '
        r'width="(\d+)" height="(\d+)"', xml)}


def test_colorful_explicit_column_lane_puts_its_title_across_the_top(capsys):
    cells = [{"type": "lane", "id": "ln", "x": 10, "y": 80, "w": 190, "h": 500, "text": "Max", "orientation": "column"},
             {"id": "a", "text": "a", "x": 20, "y": 116, "w": 170, "h": 60}]
    (_, xml, v), = ds.compile_spec({"theme": "colorful", "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    g = lane_geoms(xml)
    assert g["ln"] == (10, 80, 190, 500)
    assert g["auto2"] == (10, 80, 190, 26)              # the strip of the minimal / bw swimlane: 26 px at font 10
    assert v == 3 and capsys.readouterr().err == ""
    (_, mxml, _), = ds.compile_spec({"theme": "minimal", "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    assert ds.parse_style(styles(mxml)["ln"])[0]["startSize"] == "26"
    cells[0]["gutter"] = 40
    (_, xml, _), = ds.compile_spec({"theme": "colorful", "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    assert lane_geoms(xml)["auto2"] == (10, 80, 190, 40)


def test_colorful_lane_without_orientation_keeps_the_left_gutter(capsys):
    cells = [{"type": "lane", "id": "ln", "x": 10, "y": 80, "w": 190, "h": 500, "text": "Max"}]
    (_, xml, _), = ds.compile_spec({"theme": "colorful", "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    assert lane_geoms(xml)["auto2"] == (14, 80, 150, 500)     # x + 4, gutter 150, the lane's height, as before
    cells[0].update(w=600, h=100)
    (_, xml, _), = ds.compile_spec({"theme": "colorful", "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    assert lane_geoms(xml)["auto2"] == (14, 80, 150, 100)


# --------------------------------------------------------------------------- legend label keys
def test_legend_label_keys_are_checked(capsys):
    cells = [{"id": "a", "role": "human", "text": "a", "x": 0, "y": 0},
             {"id": "w", "role": "outside", "shape": "database", "text": "w", "x": 0, "y": 100},
             {"type": "legend", "id": "lg", "x": 300, "y": 0, "w": 300,
              "labels": {"humans": "Max decides", "databse": "x", "guard": "y", "accent": "z", "database": "store"}}]
    (_, xml, _), = ds.compile_spec({"sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    err = capsys.readouterr().err
    assert "labels key 'humans' is not a role, a shape, 'frame' or 'accent' (nearest valid key: 'human')" in err
    assert "labels key 'databse' is not a role, a shape, 'frame' or 'accent' (nearest valid key: 'database')" in err
    assert "labels key 'guard' names a role or shape this sheet does not show" in err
    assert "labels key 'accent' names the accent, which no box or edge sets" in err
    assert "'database' names" not in err and "store: outside system" in legend_rows(xml)


def test_drawio_paint_free_bare_styles_are_not_a_breach():
    # draw.io writes edgeLabel on a label typed onto an edge; it carries no paint
    for tok in ("edgeLabel", "group", "ellipse", "rhombus", "triangle", "line"):
        assert tok in ds.COMPILER_BARE_STYLES
    assert "blue" not in ds.COMPILER_BARE_STYLES


# =========================================================================== v8
# --------------------------------------------------------------------------- colorful equals the pre-theme script
# baseline_fixture/drawio_spec_baseline.py is the script before themes existed. A seeded generator writes
# specs that use only the keys that script knew; for every spec it builds, a colorful build must build too
# and write the same bytes. Specs the baseline refuses are counted, not compared.
BASELINE_FIXTURE = os.path.join(HERE, "baseline_fixture", "drawio_spec_baseline.py")


def load_baseline():
    import importlib.util
    sys.dont_write_bytecode = True          # no __pycache__ beside the fixture
    spec = importlib.util.spec_from_file_location("drawio_spec_baseline", BASELINE_FIXTURE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PRE_THEME_TEXTS = ["", "A", "Approve the card", "Tom & Jerry <b>bold</b>", "two\nlines", "x > y and 'q'",
                   'say "hi"', "Rows 9,041\n• Origin known 7,486\n     ◦ classified 6,200",
                   "&copy; 2026", "a fairly long label that will have to wrap across several lines in a box",
                   "한글 라벨", "A\n• one\n• two\n• three"]
PRE_THEME_COLOURS = ["#FFFFFF", "#000000", "#123456", "#EEEEEE", "red", "#abc", "none"]
PRE_THEME_STYLES = ["", "fontStyle=2", "endArrow=open;", "fontFamily='Helvetica';", "absoluteArcSize=1;arcSize=12;",
                    "shape=rhombus;", "fontSize=14;spacing=6", "strokeWidth=3;", "dashed=0;", "align=right;",
                    "shape=umlActor;verticalLabelPosition=bottom;", "rounded=0", "fillColor=#FF0000;"]
PRE_THEME_IDS = ["a", "b1", "x>y", "q'r", "A&amp;B", "s p", "été", "n_1", "auto3", "7"]


def pre_theme_spec(rng):
    """One spec that uses only keys the script before themes knew."""
    roles = list(OLD_ROLES)

    def pick(seq):
        return seq[rng.randrange(len(seq))]

    def maybe(p):
        return rng.random() < p

    def num(vals):
        return pick(vals)
    sheets = []
    for si in range(pick([1, 1, 1, 2])):
        sh = {"id": pick(["s", "g", "L&amp;D", "x>1"]) if si == 0 else "p%d" % si,
              "name": pick(["L1 A", "L2 B", "Sheet <1>", "x & y", "L1 A"])}
        if maybe(0.7):
            sh["width"], sh["height"] = num([800, 1200, 1400.5]), num([600, 800, 1100.2])
        if maybe(0.75):
            g = {}
            for k, vals in (("x0", [0, 20, 180, 12.5]), ("pitch", [0, 100, 330, 160.5]),
                            ("col_width", [0, 90, 300, 140.7])):
                if maybe(0.6):
                    g[k] = num(vals)
            sh["grid"] = g
        cells, ids, used = [], [], set()

        def new_id(c):
            if maybe(0.75):
                i = pick(PRE_THEME_IDS) if maybe(0.3) else "c%d" % len(cells)
                if i in used and maybe(0.9):
                    i = "c%d_%d" % (len(cells), rng.randrange(1000))
                c["id"] = i
                used.add(i)
                ids.append(i)

        def place(c, box=True):
            r = rng.random()
            if r < 0.45:
                c["x"] = num([0, 10, 240, 37.6, -5, 900])
            elif r < 0.8:
                c["col"] = num([0, 1, 2, 0.5])
            if maybe(0.9):
                c["y"] = num([0, 10, 116, 300.4, 590])
            r = rng.random()
            if r < 0.45:
                c["w"] = num([2, 80, 120, 170, 300.9, 460])
            elif r < 0.65:
                c["span"] = num([1, 2, 3, 0])
            if maybe(0.85 if box else 0.95):
                c["h"] = num([0, 20, 40, 60, 103, 250.5])
        for _ in range(rng.randint(1, 10)):
            kind = rng.choices(["box", "lane", "text", "odd"], weights=[6, 2, 2, 0.5])[0]
            c = {}
            if kind == "lane":
                c["type"] = "lane"
                c["x"], c["y"] = num([0, 10, 20.5]), num([0, 80, 116])
                c["w"], c["h"] = pick([(1300, 200), (190, 500), (600, 100.5), (210, 268)])
                if maybe(0.7):
                    c["text"] = pick(PRE_THEME_TEXTS[1:])
                if maybe(0.5):
                    # any gutter the script before themes accepted: negative, zero, oversized, odd values;
                    # a string makes that script fail, so such specs are counted, not compared
                    c["gutter"] = pick([0, 60, 120, 150.5, int(c["w"]) - 1, int(c["w"]), -5, -0.5, 10 ** 6,
                                        True, False, 3, "wide"])
            elif kind == "text":
                c["type"] = "text"
                c["text"] = pick(PRE_THEME_TEXTS)
                place(c, box=False)
                if maybe(0.3):
                    c["bold"] = pick([False, True, 0])
                if maybe(0.3):
                    c["align"] = pick(["center", "right", "left"])
            else:
                if kind == "odd":
                    c["type"] = pick(["circle", "note", "Box", "group"])   # unknown types were drawn as boxes
                c["text"] = pick(PRE_THEME_TEXTS)
                place(c)
                if maybe(0.5):
                    c["role"] = pick(roles)
                for k, vals in (("fill", PRE_THEME_COLOURS), ("stroke", PRE_THEME_COLOURS),
                                ("align", ["left", "center", "right"]), ("valign", ["top", "middle", "bottom"]),
                                ("style", PRE_THEME_STYLES)):
                    if maybe(0.2):
                        c[k] = pick(vals)
                for k in ("dashed", "square", "bold"):
                    if maybe(0.15):
                        c[k] = pick([True, False, 1])
            if kind != "lane" and maybe(0.3):
                c["font"] = pick([8, 9, 10, 12, 9.5])
            if kind == "lane" and maybe(0.3):
                c["role"] = pick(roles)
            if kind == "lane" and maybe(0.2):
                c["fill"] = pick(PRE_THEME_COLOURS)
            new_id(c)
            cells.append(c)
        for _ in range(rng.randint(0, 4) if len(ids) >= 2 else 0):
            e = {"type": "edge", "from": pick(ids), "to": pick(ids)}
            for k, gen in (("label", lambda: pick(PRE_THEME_TEXTS)), ("role", lambda: pick(roles + ["boss"])),
                           ("color", lambda: pick(PRE_THEME_COLOURS)), ("width", lambda: pick([1, 2, 1.5])),
                           ("dashed", lambda: pick([True, False])), ("exit", lambda: [pick([0, 0.5, 1]), pick([0, 0.25, 1])]),
                           ("entry", lambda: [pick([0, 0.5, 1]), pick([0, 0.75, 1])]),
                           ("points", lambda: [[pick([755, 100.5]), pick([157, 253])], [pick([755, 300]), 253]]),
                           ("label_at", lambda: pick([-0.5, 0, 0.7])), ("label_offset", lambda: [pick([0, -8]), pick([-10, 4.5])]),
                           ("font", lambda: pick([8, 9, 10.5])), ("style", lambda: pick(PRE_THEME_STYLES)),
                           ("id", lambda: "e%d" % rng.randrange(10 ** 6))):
                if maybe(0.3):
                    e[k] = gen()
            cells.insert(rng.randrange(len(cells) + 1), e)       # edges may be listed anywhere
        sh["cells"] = cells
        sheets.append(sh)
    return {"sheets": sheets}


def compare_with_baseline(seed, count, base=None):
    """(specs the baseline built, specs it refused, [(index, spec, reason)] for every difference)."""
    import copy
    import contextlib
    import io
    import random
    base = base or load_baseline()
    rng = random.Random(seed)
    built = refused = 0
    diffs = []
    for k in range(count):
        spec = pre_theme_spec(rng)
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            try:
                sheets = base.compile_spec(copy.deepcopy(spec))
                old = base.wrap_file([x for _, x, _ in sheets])
                base.minidom.parseString(old)
            except (Exception, SystemExit):
                old = None
            try:
                new = ds.build_document(copy.deepcopy(spec), "colorful")[0]
                why = None
            except (Exception, SystemExit) as ex:
                new, why = None, "%s: %s" % (type(ex).__name__, ex)
        if old is None:
            refused += 1
            continue
        built += 1
        if new != old:
            diffs.append((k, spec, why or "different bytes"))
    return built, refused, diffs


@pytest.mark.parametrize("seed", [20261122, 8])
def test_colorful_equals_the_pre_theme_script_on_generated_specs(seed):
    built, refused, diffs = compare_with_baseline(seed, 300)
    assert built >= 200, (built, refused)
    assert not diffs, "%d of %d differ; first: #%d %s\n%s" % (
        len(diffs), built, diffs[0][0], diffs[0][2], json.dumps(diffs[0][1]))


def baseline_build(spec, tmp_path):
    p = tmp_path / "pre.json"
    p.write_text(json.dumps(spec), encoding="utf-8")
    out = tmp_path / "pre.drawio"
    r = subprocess.run([sys.executable, BASELINE_FIXTURE, "build", str(p), "-o", str(out), "--force"],
                       capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    return r, out


def test_colorful_takes_the_pre_theme_fallbacks_where_minimal_and_bw_stop(tmp_path, capsys):
    # the reviewer's case: a grid with only a pitch, a box placed by col without w (default width 120)
    pos_only = {"sheets": [{"id": "s", "name": "L1 T", "grid": {"pitch": 200}, "cells": [
        {"id": "b", "col": 1, "y": 10, "h": 40, "text": "B"}]}]}
    odd_type = {"sheets": [{"id": "s", "name": "L1 T", "cells": [{"type": "circle", "id": "c", "text": "c", "x": 5, "y": 5}]}]}
    two_names = {"sheets": [{"id": "s", "name": "L1 T", "cells": [{"id": "a", "text": "a", "x": 0, "y": 0}]},
                            {"id": "t", "name": "L1 T", "cells": [{"id": "a", "text": "a", "x": 0, "y": 0}]}]}
    for spec in (pos_only, odd_type, two_names):
        old, old_out = baseline_build(spec, tmp_path)
        assert old.returncode == 0, old.stderr
        r, out = build(spec, tmp_path, "--theme", "colorful")
        assert r.returncode == 0 and out.read_bytes() == old_out.read_bytes(), r.stderr
        for theme in ("minimal", "bw"):
            with pytest.raises(SystemExit):
                ds.compile_spec(spec, theme)
    assert 'x="200" y="10" width="120" height="40"' in ds.compile_spec(pos_only, "colorful")[0][1]
    capsys.readouterr()
    ds.compile_spec(odd_type, "colorful")
    assert "unknown cell type 'circle'; drawn as a box" in capsys.readouterr().err
    linked = json.loads(json.dumps(two_names))
    linked["sheets"][0]["cells"][0]["link"] = "L1 T"
    with pytest.raises(SystemExit, match="duplicate sheet name"):
        ds.compile_spec(linked, "colorful")


def test_colorful_row_lane_gutter_is_checked_only_where_it_was_broken(capsys):
    def lane(**kw):
        return {"sheets": [{"id": "s", "name": "L1 T", "cells": [dict({"type": "lane", "id": "ln", "x": 0, "y": 0,
                                                                     "w": 600, "h": 100}, **kw)]}]}
    # colorful writes what the script before themes wrote, and warns; minimal and bw stop
    for odd, width in ((-5, -5), (600, 600), (True, 1), (10 ** 6, 10 ** 6)):
        (_, xml, _), = ds.compile_spec(lane(text="Owner", gutter=odd), "colorful")
        assert 'width="%d" height="100"' % width in xml
        assert "gutter %r is not a number from 0 to below the lane's width" % odd in capsys.readouterr().err
        for theme in ("minimal", "bw"):
            with pytest.raises(SystemExit, match="gutter .* must be a number from 0 to the lane's width"):
                ds.compile_spec(lane(text="Owner", gutter=odd), theme)
    with pytest.raises(SystemExit, match="gutter 'wide' must be a number"):      # the old script failed on it
        ds.compile_spec(lane(text="Owner", gutter="wide"), "colorful")
    ds.compile_spec(lane(gutter="wide"), "colorful")            # untitled: the gutter is not used, as before
    (_, xml, _), = ds.compile_spec(lane(text="Owner", gutter=120.5), "colorful")
    assert 'width="120" height="100"' in xml


# --------------------------------------------------------------------------- the theme record on the layer cell
def delete_cells(pred):
    def fn(m):
        root_el = m.find("root")
        for c in list(root_el):
            if pred(c):
                root_el.remove(c)
    return fn


def test_theme_record_on_the_layer_survives_deleting_the_content(tmp_path):
    spec = {"theme": "bw", "sheets": [{"id": "s", "name": "L1 T", "cells": [
        {"id": "a", "text": "a", "x": 0, "y": 0}, {"id": "b", "text": "b", "x": 0, "y": 100}]}]}
    r, out = build(spec, tmp_path)
    text = out.read_text(encoding="utf-8")
    assert '<mxCell id="1" style="dsTheme=bw;dsAccent=#1F5FBF;" parent="0"/>' in text
    out.write_text(re.sub(r' ds(Theme|Accent|MaxColours)="[^"]*"', "", text), encoding="utf-8")   # a save in draw.io
    edit_model(out, delete_cells(lambda c: c.get("id") == "s_a"))                                # the first vertex
    assert "dsTheme=bw" not in out.read_text(encoding="utf-8").split('id="s_b"')[1]
    assert ds.file_theme(ds.read_file(str(out)))[:2] == ("bw", "#1F5FBF")
    assert list_file(out).stdout.startswith("theme bw")
    more = {"sheets": [{"id": "t", "name": "L2 T", "cells": [{"id": "h", "role": "human", "text": "h", "x": 0, "y": 0}]}]}
    r = add(more, out, tmp_path)
    assert r.returncode == 0 and "theme bw (from the file)" in r.stdout and "2 colours" in r.stdout, r.stdout + r.stderr
    edit_model(out, delete_cells(lambda c: c.get("id") not in ("0", "1")))                     # all content
    more["sheets"][0].update(id="u", name="L3 T")
    r = add(more, out, tmp_path)
    assert r.returncode == 0 and "theme bw (from the file)" in r.stdout, r.stderr
    assert ds.file_colours(ds.read_file(str(out)))[0] == {"#000000", "#FFFFFF"}


# --------------------------------------------------------------------------- the audit is an allow-list
def black_box_glass(c):
    c.set("style", c.get("style").replace("fillColor=#FFFFFF;", "fillColor=#000000;") + "glass=1;")


@pytest.mark.parametrize("theme", ["bw", "minimal"])
@pytest.mark.parametrize("edit,key", [
    (edit_cell("s_plain", black_box_glass), "glass=1"),
    (edit_cell("s_plain", set_label('<span style="background:linear-gradient(black,white)">x</span>')), "linear-gradient"),
    (edit_cell("s_plain", set_label('<span style="border:4px solid red">x</span>')), "border:4px solid red"),
    (edit_cell("s_plain", add_shadow), "shadow=1"),
    (edit_cell("s_plain", lambda c: c.set("style", c.get("style") + "sketch=1;")), "sketch=1"),
    (edit_cell("s_plain", lambda c: c.set("style", c.get("style") + "image=data:x;")), "image=data:x"),
    (edit_cell("s_plain", set_label('<marquee>x</marquee>')), "<marquee>"),
])
def test_the_audit_permits_only_what_the_compiler_writes(theme, edit, key, tmp_path):
    r, out = build(one_box_per_role(theme=theme), tmp_path)
    edit_model(out, edit)
    val = validate(out)
    assert val.returncode == 1 and "ERR" in val.stdout and key in val.stdout, val.stdout
    assert key in list_file(out).stdout
    before = out.read_bytes()
    r = add(second_sheet(), out, tmp_path)
    assert r.returncode != 0 and key in r.stderr and out.read_bytes() == before
    assert not os.path.exists(str(out) + ".bak")


EDITOR_LABELS = ['<div>Head</div><div><span style="font-size: 9px;">detail</span></div>',
                 '<font face="Helvetica">x</font>', '<i>x</i>',
                 '<p style="text-align: left; margin: 0px; line-height: 1.2;"><u>a</u> <b>b</b><br/>c</p>',
                 '<ul><li><span style="text-decoration: underline;">one</span></li></ul><sub>2</sub>']
PAINT_LABELS = [('<span style="color:red">x</span>', "color:red"),
                ('<font color="#ff0000">x</font>', "attribute color"),
                ('<span style="background:linear-gradient(black,white)">x</span>', "linear-gradient"),
                ('<span style="border:4px solid red">x</span>', "border: 4px solid red"),
                ('<span style="text-shadow:0 0 2px red">x</span>', "text-shadow"),
                ('<span style="text-decoration: underline red">x</span>', "text-decoration: underline red"),
                ('<img src="x.png">', "<img"), ('<svg width="9"></svg>', "<svg"), ('<blink>x</blink>', "<blink>")]


def test_label_audit_permits_the_editors_paint_free_html_and_refuses_paint(tmp_path):
    r, out = build(one_box_per_role(theme="bw"), tmp_path)
    clean = out.read_bytes()
    for value in EDITOR_LABELS:
        assert ds.label_breaches(value) == [], value
        out.write_bytes(clean)
        edit_model(out, edit_cell("s_plain", set_label(value)))
        val = validate(out)
        assert val.returncode == 0 and "problems: 0" in val.stdout, (value, val.stdout)
    assert add(second_sheet(), out, tmp_path).returncode == 0          # an edited file still takes a sheet
    os.remove(str(out) + ".bak")
    for value, key in PAINT_LABELS:
        out.write_bytes(clean)
        edit_model(out, edit_cell("s_plain", set_label(value)))
        assert ds.label_breaches(value), value
        val = validate(out)
        assert val.returncode == 1 and key in val.stdout, (value, val.stdout)
        before = out.read_bytes()
        r = add(second_sheet(), out, tmp_path)
        assert r.returncode != 0 and key in r.stderr and out.read_bytes() == before, (value, r.stderr)
        assert not os.path.exists(str(out) + ".bak")


def catalogue_spec(theme):
    cells = []
    for k, name in enumerate(ds.SHAPES):
        role = "human" if name in ("actor", "decision") else "plain"
        cells.append({"id": name, "shape": name, "role": role, "text": name if name not in ("decision", "cloud",
                      "terminator", "actor") else "ok", "x": 20 + 200 * (k % 5), "y": 20 + 160 * (k // 5),
                      "w": 40 if name == "actor" else 150, "h": 70 if name == "actor" else "auto"})
    cells += [{"id": "acc", "text": "acc", "x": 20, "y": 700, "accent": True, "style": "strokeColor=#000000;"},
              {"id": "fr", "square": True, "text": "Frame\n• part", "x": 300, "y": 680, "w": 300, "h": 140},
              {"id": "in", "role": "record", "text": "in", "x": 320, "y": 740, "w": 120, "h": 40},
              {"type": "text", "id": "t", "text": "a text", "x": 700, "y": 700, "w": 100, "h": 20},
              {"type": "edge", "from": "acc", "to": "in", "accent": True, "label": "flow"},
              {"type": "edge", "from": "process", "to": "data", "dashed": True, "width": 2, "label": "x"},
              {"type": "legend", "id": "lg", "x": 1020, "y": 20, "w": 260}]
    return {"theme": theme, "sheets": [{"id": "c", "name": "L1 Catalogue", "width": 1300, "height": 900, "cells": cells}]}


def compiler_specs(theme):
    """Specs whose files the compiler writes: the reference examples and the test specs."""
    specs = [json.load(open(os.path.join(REFS, n), encoding="utf-8")) for n in sorted(os.listdir(REFS))
             if n.endswith(".json")]
    specs += [one_box_per_role(), nested_spec(theme), lane_spec(theme), lane_spec(theme, orientation="column"),
              linked_spec(theme), catalogue_spec(theme), one_box_per_role(max_colours=3)]
    for s in specs:
        s["theme"] = theme
    return specs


@pytest.mark.parametrize("theme", ["minimal", "bw"])
def test_every_file_the_compiler_builds_passes_the_allow_list(theme, tmp_path, capsys):
    for spec in compiler_specs(theme):
        doc = ds.build_document(spec)[0]
        root = ds.ET.fromstring(doc)
        assert ds.effect_problems(root, ds.theme_palette(*ds.file_theme(root)[:2], ds.file_theme(root)[2] or 4)) == [], \
            spec["sheets"][0]["name"]
        assert ds.record_conflicts(root) == []
        # and the permitted set covers every key the compiler wrote
        for st in ds.model_styles(ds.decode_diagram(root.find("diagram"))):
            assert set(ds.parse_style(st)[0]) <= ds.PERMITTED_STYLE_KEYS, st
    # after add, with an accent, a lane, a frame and a legend on the new sheet
    r, out = build(compiler_specs(theme)[0], tmp_path)
    assert r.returncode == 0, r.stderr
    more = catalogue_spec(theme)
    more.pop("theme")
    assert add(more, out, tmp_path).returncode == 0
    val = validate(out)
    assert val.returncode == 0 and "problems: 0" in val.stdout, val.stdout


# --------------------------------------------------------------------------- the surface behind a label is the nearest one
def test_text_in_a_white_lane_inside_a_black_frame_stays_black():
    cells = [{"id": "f", "square": True, "text": "Set", "x": 0, "y": 0, "w": 700, "h": 400, "fill": "#000000"},
             {"type": "lane", "id": "ln", "x": 20, "y": 40, "w": 600, "h": 300, "text": "Lane"},
             {"type": "text", "id": "t", "text": "caption", "x": 200, "y": 80, "w": 100, "h": 20},
             {"id": "p", "shape": "actor", "role": "human", "text": "Max", "x": 400, "y": 80, "w": 40, "h": 70},
             {"type": "text", "id": "t2", "text": "on the frame", "x": 30, "y": 350, "w": 100, "h": 20}]
    for theme in ("minimal", "bw"):
        (_, xml, _), = ds.compile_spec({"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
        st = {k: ds.parse_style(v)[0] for k, v in styles(xml).items()}
        assert st["t"].get("fontColor", "#000000") == "#000000" and st["p"]["fontColor"] == "#000000", theme
        assert st["t2"]["fontColor"] == "#FFFFFF", theme                       # outside the lane, on black


# --------------------------------------------------------------------------- ids stay literal
def test_ids_are_literal_strings_and_two_ids_stay_two(tmp_path):
    cells = [{"id": "A&B", "text": "1", "x": 0, "y": 0}, {"id": "A&amp;B", "text": "2", "x": 0, "y": 100},
             {"type": "edge", "id": "e", "from": "A&B", "to": "A&amp;B"}]
    for theme in ("minimal", "bw"):
        r, out = build({"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]}, tmp_path)
        assert r.returncode == 0, r.stderr
        got = {c.get("id"): c for c in ds.model_cells(ds.read_file(str(out)))}
        assert "s_A&B" in got and "s_A&amp;B" in got
        assert (got["s_e"].get("source"), got["s_e"].get("target")) == ("s_A&B", "s_A&amp;B")
        assert validate(out).returncode == 0
    with pytest.raises(SystemExit, match="are the same id once the file is read"):
        ds.compile_spec({"theme": "colorful", "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    for theme in ("minimal", "bw", "colorful"):
        spec = {"theme": theme, "sheets": [
            {"id": "top", "name": "L1 Top", "cells": [{"id": "a", "text": "a", "x": 0, "y": 0, "link": "L2 R"}]},
            {"id": "R&amp;D", "name": "L2 R", "cells": [{"id": "b", "text": "b", "x": 0, "y": 0}]}]}
        r, out = build(spec, tmp_path)
        assert r.returncode == 0, r.stderr
        root = ds.read_file(str(out))
        target = [d.get("id") for d in root.findall("diagram") if d.get("name") == "L2 R"][0]
        link = [c for c in ds.model_cells(root) if c.get("id") == "top_a"][0].wrapper.get("link")
        assert target == "R&amp;D" and link == "data:page/id," + target, (theme, link)
        assert validate(out).returncode == 0


# --------------------------------------------------------------------------- a delay that cannot fit its text stops
def test_a_delay_whose_round_side_eats_its_width_stops(capsys):
    text = ("Waiting for the owner\n• replies within one working day\n• escalates after two days\n"
            "• then closes the ticket")
    assert len(text) >= 105
    for theme in ("minimal", "bw", "colorful"):
        with pytest.raises(SystemExit, match="too narrow for its text.*widen the box"):
            ds.compile_spec({"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "cells": [
                {"id": "d", "shape": "delay", "text": text, "x": 0, "y": 0, "w": 120, "h": "auto"}]}]})
    (_, xml, _), = ds.compile_spec({"sheets": [{"id": "s", "name": "L1 T", "cells": [
        {"id": "d", "shape": "delay", "text": text, "x": 0, "y": 0, "w": 320, "h": "auto"}]}]})
    assert int(re.search(r'id="s_d".*?height="(\d+)"', xml).group(1)) < 200


# --------------------------------------------------------------------------- conflicting theme records
def test_pages_with_conflicting_theme_records_are_reported_and_add_refuses(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    r, fa = build(one_box_per_role(theme="minimal"), a)
    r, fb = build(second_sheet(theme="bw"), b)
    root = ds.read_file(str(fa))
    root.append(ds.read_file(str(fb)).find("diagram"))          # a bw page imported into a minimal file
    ds.ET.ElementTree(root).write(str(fa), encoding="utf-8", xml_declaration=False)
    val = validate(fa)
    assert val.returncode == 1 and "sheet 'L2 More' records theme bw" in val.stdout, val.stdout
    assert "sheets 'L1 Test' and 'L2 More' record different theme settings (theme minimal vs bw)" in val.stdout
    before = fa.read_bytes()
    r = add({"sheets": [{"id": "n", "name": "L3 New", "cells": [{"id": "x", "text": "x", "x": 0, "y": 0}]}]}, fa, a)
    assert r.returncode != 0 and "record different theme settings" in r.stderr and fa.read_bytes() == before
    assert not os.path.exists(str(fa) + ".bak")
    r, plain = build(one_box_per_role(theme="colorful"), tmp_path)
    assert ds.record_conflicts(ds.read_file(str(plain))) == []


# --------------------------------------------------------------------------- the accent supersedes stroke in the legend
@pytest.mark.parametrize("theme", ["minimal", "bw", "colorful"])
def test_an_accent_removes_the_stroke_from_the_legend_grouping(theme, capsys):
    cells = [{"id": "a", "text": "a", "x": 0, "y": 0, "accent": True},
             {"id": "b", "text": "b", "x": 0, "y": 100, "accent": True, "style": "strokeColor=#FFFFFF;strokeWidth=9;"},
             {"type": "legend", "id": "lg", "x": 300, "y": 0}]
    (_, xml, _), = ds.compile_spec({"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    rows = legend_rows(xml)
    assert rows.count("component") == 1 and rows.count("highlighted") == 1 and len(rows) == 2, rows
    assert "different ways" not in capsys.readouterr().err
    assert "strokeWidth=9" not in xml


# --------------------------------------------------------------------------- an untitled lane has no strip
def test_an_untitled_lane_ignores_its_gutter(capsys):
    for w, h in ((600, 100), (200, 400)):
        (_, xml, _), = ds.compile_spec({"sheets": [{"id": "s", "name": "T", "cells": [
            {"type": "lane", "id": "ln", "x": 0, "y": 0, "w": w, "h": h, "gutter": 60},
            {"id": "a", "text": "a", "x": 15, "y": 20, "w": 100, "h": 40}]}]})
        assert ds.parse_style(styles(xml)["ln"])[0]["startSize"] == "0"
        assert capsys.readouterr().err == ""


# --------------------------------------------------------------------------- text black or white by contrast
def test_box_text_is_black_or_white_by_contrast():
    cells = [{"id": "dark", "text": "d", "x": 0, "y": 0, "accent": True, "fill": "#102030"},
             {"id": "own", "text": "o", "x": 0, "y": 100, "accent": True, "fill": "#102030", "style": "fontColor=#000000;"},
             {"id": "light", "text": "l", "x": 0, "y": 200}]
    (_, xml, _), = ds.compile_spec({"accent_color": "#102030", "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    st = {k: ds.parse_style(v)[0] for k, v in styles(xml).items()}
    assert st["dark"]["fontColor"] == "#FFFFFF" and st["own"]["fontColor"] == "#000000"
    assert st["light"]["fontColor"] == "#000000"
    assert ds.text_on("#F2F2F2") == ds.text_on("#FFFFFF") == ds.text_on("none") == "#000000"
    assert ds.text_on("#000000") == ds.text_on("#1F5FBF") == "#FFFFFF"


# --------------------------------------------------------------------------- an actor's caption is measured
def test_an_actor_caption_is_checked_against_the_page_and_its_neighbours(capsys, tmp_path):
    spec = {"sheets": [{"id": "s", "name": "L1 T", "width": 300, "height": 120, "cells": [
        {"id": "p", "shape": "actor", "role": "human", "text": "Customer support representative",
         "x": 20, "y": 30, "w": 40, "h": 70}]}]}
    r, out = build(spec, tmp_path)
    assert r.returncode == 0 and "the caption of actor p" in r.stderr and "outside the page" in r.stderr, r.stderr
    assert "the caption of actor s_p" in validate(out).stdout
    spec["sheets"][0].update(height=600)
    spec["sheets"][0]["cells"] += [{"id": "below", "text": "below", "x": 0, "y": 110, "w": 100, "h": 30},
                                   {"type": "lane", "id": "ln", "x": 0, "y": 0, "w": 280, "h": 108, "text": "Owner",
                                    "orientation": "column"}]
    spec["sheets"][0]["cells"][0]["y"] = 34
    ds.compile_spec(spec)
    err = capsys.readouterr().err
    assert "the caption of actor p overlaps below" in err and "reaches past the bottom of lane ln" in err
    (_, xml, _), = ds.compile_spec(spec)
    assert 'x="20" y="34" width="40" height="70"' in xml          # the actor itself is unchanged


# --------------------------------------------------------------------------- the L1 example nests into L2
def test_l1_example_has_one_container_per_owner_and_nests_into_l2():
    spec = json.load(open(os.path.join(REFS, "example_plugin_3_levels_minimal.json"), encoding="utf-8"))
    l1, l2, l3 = spec["sheets"]
    comps = []
    ds.compile_spec(spec, compilers=comps)
    recs = {r["id"].split("_", 1)[1]: r for r in comps[0].recs}

    def parent(k):
        p = recs[k]["parent"]
        return None if p is None else comps[0].recs[p]["id"].split("_", 1)[1]
    assert [k for k in recs if parent(k) == "lane_max"] == ["ask", "gates", "verdict"]
    assert sorted(k for k in recs if parent(k) == "plugin") == ["audit", "decide", "guard", "propose"]
    containers = {c["text"].split("\n")[0] for c in l1["cells"] if c.get("type") == "lane" or c.get("id") in
                  ("propose", "decide", "audit", "guard")}
    l2_lanes = [c["text"] for c in l2["cells"] if c.get("type") == "lane"]
    l3_lanes = [c["text"] for c in l3["cells"] if c.get("type") == "lane"]
    assert all(t in containers for t in l2_lanes) and len(set(l2_lanes)) == len(l2_lanes), (l2_lanes, containers)
    assert l3_lanes == l2_lanes
    # every L1 box or lane has a visible descendant on L2: a lane named after it, or a box whose headline names it
    heads = " | ".join(c.get("text", "").split("\n")[0].lower() for c in l2["cells"] if c.get("type") != "edge")
    for c in l1["cells"]:
        if c.get("type") in ("lane", None) and c.get("role") != "note":
            assert c.get("link") == "L2 Stages and layers", c["id"]
    for word in ("asks", "gate", "verdict", "whole a", "warehouse", "independent reference"):
        assert word in heads, word
    for sh in (l1, l2, l3):
        sub = [c["text"] for c in sh["cells"] if c.get("id") == "sub"][0]
        assert sub.endswith(".") and sub.count(". ") == 0, sub          # one sentence


# --------------------------------------------------------------------------- a lane can link
@pytest.mark.parametrize("theme", ["minimal", "bw", "colorful"])
def test_a_lane_links_to_the_sheet_below(theme, tmp_path, capsys):
    spec = {"theme": theme, "sheets": [
        {"id": "top", "name": "L1 Top", "cells": [
            {"type": "lane", "id": "ln", "x": 0, "y": 0, "w": 200, "h": 300, "text": "Max", "orientation": "column",
             "link": "L2 Down"},
            {"id": "a", "text": "a", "x": 20, "y": 60, "w": 160, "h": 40}]},
        {"id": "down", "name": "L2 Down", "cells": [{"id": "c", "text": "c", "x": 0, "y": 0}]}]}
    r, out = build(spec, tmp_path)
    assert r.returncode == 0 and "unknown key" not in r.stderr, r.stderr
    wrapped = [c for c in ds.model_cells(ds.read_file(str(out))) if c.wrapper is not None]
    assert len(wrapped) == 1 and wrapped[0].wrapper.get("link") == "data:page/id,down"
    if theme == "colorful":
        assert wrapped[0].get("value") == "Max" and wrapped[0].get("style").startswith("text;")
    else:
        assert wrapped[0].get("id") == "top_ln" and wrapped[0].get("style").startswith("swimlane;")
        kid = [c for c in ds.model_cells(ds.read_file(str(out))) if c.get("id") == "top_a"][0]
        assert kid.get("parent") == "top_ln"
    assert validate(out).returncode == 0


# --------------------------------------------------------------------------- stages as rows point down
@pytest.mark.parametrize("theme", ["minimal", "bw"])
def test_row_stage_headers_pointing_down_give_one_legend_row(theme, capsys):
    cells = [{"id": "st%d" % k, "role": "stage", "text": "S%d" % k, "x": 0, "y": 60 + 120 * k, "w": 60, "h": 100,
              "style": "direction=south;"} for k in range(3)]
    cells += [{"type": "lane", "id": "ln%d" % k, "x": 80 + 220 * k, "y": 0, "w": 200, "h": 420, "text": "L%d" % k,
               "orientation": "column"} for k in range(2)]
    cells.append({"type": "legend", "id": "lg", "x": 560, "y": 0})
    (_, xml, _), = ds.compile_spec({"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    rows = legend_rows(xml)
    assert rows == ["stage"], rows
    sample = re.search(r'value="stage" style="([^"]*)"', xml).group(1)
    assert "shape=step;" in sample and "direction=south;" in sample
    assert "different ways" not in capsys.readouterr().err


# --------------------------------------------------------------------------- frame height message
def test_a_frame_with_auto_height_names_the_rule():
    with pytest.raises(SystemExit, match="not available on a frame; a frame's height must be a number"):
        ds.compile_spec({"sheets": [{"id": "s", "name": "L1 T", "cells": [
            {"id": "f", "square": True, "text": "F", "x": 0, "y": 0, "w": 200, "h": "auto"}]}]})


# --------------------------------------------------------------------------- warnings: N and --strict
def test_build_and_add_report_warnings_and_strict_fails_on_any(tmp_path):
    clean = {"sheets": [{"id": "s", "name": "T", "cells": [{"id": "a", "text": "a", "x": 0, "y": 0}]}]}
    r, out = build(clean, tmp_path, "--strict")
    assert r.returncode == 0 and r.stdout.strip().splitlines()[-1] == "warnings: 0"
    warned = {"sheets": [{"id": "s", "name": "T", "cells": [{"id": "a", "text": "a", "x": 0, "y": 0, "colour": 1}]}]}
    r, out = build(warned, tmp_path, "--strict")
    assert r.returncode == 1 and r.stdout.strip().splitlines()[-1] == "warnings: 1" and out.exists()
    r, out = build(warned, tmp_path)
    assert r.returncode == 0 and r.stdout.strip().splitlines()[-1] == "warnings: 1"
    r, out = build(clean, tmp_path)
    before = out.read_bytes()
    more = {"sheets": [{"id": "t", "name": "T2", "cells": [{"id": "b", "text": "b", "x": 0, "y": 0, "colour": 1}]}]}
    r = add(more, out, tmp_path, "--strict")
    assert r.returncode == 1 and "left unchanged" in r.stderr and r.stdout.strip().splitlines()[-1] == "warnings: 1"
    assert out.read_bytes() == before and not os.path.exists(str(out) + ".bak")
    r = add(more, out, tmp_path)
    assert r.returncode == 0 and r.stdout.strip().splitlines()[-1] == "warnings: 1"



# =========================================================================== v9
# --------------------------------------------------------------------------- the page's own attributes are allow-listed
def page_attr(key, value):
    def fn(m):
        m.set(key, value)
    return fn


IMAGE = '{"src":"data:image/png;base64,iVBORw0KGgo=","width":60,"height":60}'


@pytest.mark.parametrize("theme", ["bw", "minimal"])
@pytest.mark.parametrize("key,value,bad,compressed", [
    ("backgroundImage", IMAGE, "backgroundImage=", False),
    ("backgroundImage", IMAGE, "backgroundImage=", True),
    ("background", "#FF0000", "background=#FF0000", True),
    ("extraPaint", "1", "extraPaint=1", False),
    ("shadow", "1", "shadow=1", False),
    ("background", "none", None, False),
    ("background", "#ffffff", None, True),
])
def test_a_page_attribute_the_compiler_does_not_write_breaks_a_minimal_or_bw_file(theme, compressed, key, value, bad,
                                                                                   tmp_path):
    r, out = build(one_box_per_role(theme=theme), tmp_path)
    edit_model(out, page_attr(key, value))
    if compressed:
        compress_sheets(out)
    val = validate(out)
    before = out.read_bytes()
    r = add(second_sheet(), out, tmp_path)
    if bad is None:
        assert val.returncode == 0 and "problems: 0" in val.stdout, val.stdout
        assert r.returncode == 0, r.stderr
        return
    assert val.returncode == 1 and bad in val.stdout and "page" in val.stdout, val.stdout
    assert bad in list_file(out).stdout
    assert r.returncode != 0 and bad in r.stderr and out.read_bytes() == before, r.stderr
    assert not os.path.exists(str(out) + ".bak")


# --------------------------------------------------------------------------- each theme setting recovered on its own
def strip_attr(path, attr_name):
    text = path.read_text(encoding="utf-8")
    new = re.sub(r' %s="[^"]*"' % attr_name, "", text, count=1)
    assert new != text
    path.write_text(new, encoding="utf-8")


def test_each_theme_setting_is_recovered_from_the_records_that_still_hold_it(tmp_path):
    # the reviewer's case: a three-colour file loses only dsMaxColours from <mxfile>
    r, out = build(one_box_per_role(max_colours=3), tmp_path)
    strip_attr(out, "dsMaxColours")
    val = validate(out)
    assert val.returncode == 0 and "max_colours 3" in val.stdout, val.stdout
    more = second_sheet()
    more["sheets"][0]["cells"][0]["accent"] = True
    more["sheets"][0]["name"] = "More"          # v10: an L1-L4 sheet without a legend warns (C-deb-7)
    r = add(more, out, tmp_path, "--strict")
    assert r.returncode == 0, r.stderr
    assert ds.file_colours(ds.read_file(str(out)))[0] == {"#000000", "#FFFFFF", "#F2F2F2"}
    assert validate(out).returncode == 0
    # a custom accent loses only dsAccent from <mxfile>: the added sheet keeps the recorded accent
    sub = tmp_path / "acc"
    sub.mkdir()
    spec = one_box_per_role(accent_color="#8B0000")
    spec["sheets"][0]["cells"][0]["accent"] = True
    r, out = build(spec, sub)
    strip_attr(out, "dsAccent")
    assert ds.file_theme(ds.read_file(str(out)))[:3] == ("minimal", "#8B0000", 4)
    r = add(more, out, sub, "--strict")
    assert r.returncode == 0, r.stderr
    cols = ds.file_colours(ds.read_file(str(out)))[0]
    assert "#8B0000" in cols and ds.DEFAULT_ACCENT not in cols, cols
    # only dsTheme lost from <mxfile>, the rest kept there: the theme comes from the sheets
    sub = tmp_path / "th"
    sub.mkdir()
    r, out = build(one_box_per_role(theme="bw"), sub)
    strip_attr(out, "dsTheme")
    assert ds.file_theme(ds.read_file(str(out)))[0] == "bw"


def test_a_sheet_whose_layer_and_first_cell_disagree_is_a_conflict(tmp_path):
    r, out = build(one_box_per_role(max_colours=3), tmp_path)
    edit_model(out, lambda m: [c.set("style", c.get("style").replace("dsMaxColours=3", "dsMaxColours=4"))
                               for c in m.iter("mxCell") if c.get("id") == "1"])
    val = validate(out)
    assert val.returncode == 1 and "its layer and its first cell record different theme settings" in val.stdout
    # a <mxfile> record without dsTheme is still compared setting by setting
    r, out = build(one_box_per_role(max_colours=3), tmp_path)
    strip_attr(out, "dsTheme")
    text = out.read_text(encoding="utf-8").replace('dsMaxColours="3"', 'dsMaxColours="4"', 1)
    out.write_text(text, encoding="utf-8")
    assert any("max_colours 3 vs 4" in p for p in ds.record_conflicts(ds.read_file(str(out))))


def test_add_audits_the_assembled_file_for_record_conflicts(tmp_path, monkeypatch):
    import argparse
    r, out = build(one_box_per_role(max_colours=3), tmp_path)
    strip_attr(out, "dsMaxColours")
    before = out.read_bytes()
    p = tmp_path / "more.json"
    p.write_text(json.dumps(second_sheet()), encoding="utf-8")
    # resolve the settings as a group again (the version before): max_colours falls back to the default 4
    real = ds.file_theme
    monkeypatch.setattr(ds, "file_theme", lambda root: real(root)[:2] + (None, True))
    ns = argparse.Namespace(spec=str(p), into=str(out), position="last", theme=None, max_colours=None, strict=False)
    with pytest.raises(SystemExit, match="would disagree with the file's theme records"):
        ds.cmd_add(ns)
    assert out.read_bytes() == before and not os.path.exists(str(out) + ".bak")


# --------------------------------------------------------------------------- text colour against what is behind it
def test_text_colour_follows_what_is_really_behind_the_text(capsys):
    cells = [{"id": "lb", "text": "on a black label", "x": 0, "y": 0, "w": 160, "style": "labelBackgroundColor=#000000;"},
             {"id": "lbo", "text": "own", "x": 0, "y": 80, "w": 160,
              "style": "labelBackgroundColor=#000000;fontColor=#000000;"},
             {"id": "f", "square": True, "text": "Set", "x": 300, "y": 0, "w": 300, "h": 200, "fill": "#000000"},
             {"id": "clear", "text": "no fill", "x": 320, "y": 60, "w": 120, "h": 40, "style": "fillColor=none;"},
             {"id": "clear2", "text": "no fill", "x": 320, "y": 120, "w": 120, "h": 40,
              "style": "fillColor=none;fontColor=#000000;"},
             {"type": "lane", "id": "ln", "x": 0, "y": 300, "w": 600, "h": 100, "text": "Lane"},
             {"id": "inlane", "text": "no fill", "x": 200, "y": 330, "w": 120, "h": 40, "style": "fillColor=none;"},
             {"id": "p", "shape": "actor", "role": "human", "text": "Max", "x": 500, "y": 420, "w": 40, "h": 70,
              "style": "labelBackgroundColor=#000000;"},
             {"type": "edge", "id": "e", "from": "lb", "to": "lbo", "label": "x", "style": "labelBackgroundColor=#000000;"},
             {"type": "edge", "id": "e2", "from": "lb", "to": "lbo", "label": "y",
              "style": "labelBackgroundColor=#000000;fontColor=#000000;"}]
    for theme in ("minimal", "bw"):
        (_, xml, _), = ds.compile_spec({"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "width": 700,
                                                                     "height": 600, "cells": cells}]})
        st = {k: ds.parse_style(v)[0] for k, v in styles(xml).items()}
        err = capsys.readouterr().err
        assert st["lb"]["fontColor"] == "#FFFFFF", theme                 # the label background, not the white fill
        assert "'lbo'" in err and "fontColor=#000000 is its label background" in err
        assert st["clear"]["fontColor"] == "#FFFFFF", theme              # no fill: the black frame behind it
        assert "box 'clear2': fontColor=#000000 is the colour of the surface behind it" in err
        assert st["inlane"]["fontColor"] == "#000000"                    # no fill in a white lane
        assert st["p"]["fontColor"] == "#FFFFFF"                         # the caption's own label background
        assert st["e"]["fontColor"] == "#FFFFFF" and "fontColor=#000000 is its label background; its label" in err
    (_, xml, _), = ds.compile_spec({"theme": "colorful", "sheets": [{"id": "s", "name": "L1 T", "cells": [
        {"id": "e", "text": "x", "x": 0, "y": 0, "style": "labelBackgroundColor=#000000;"},
        {"type": "edge", "from": "e", "to": "e", "style": "labelBackgroundColor=#000000;"}]}]})
    assert "fontColor" not in xml                                        # colorful keeps the look from before


# --------------------------------------------------------------------------- draw.io's other swimlane spelling
@pytest.mark.parametrize("theme", ["bw", "minimal"])
@pytest.mark.parametrize("spelling", ["swimlane;", "shape=swimlane;"])
def test_both_drawio_swimlane_spellings_pass_validate_and_add(theme, spelling, tmp_path):
    r, out = build(lane_spec(theme), tmp_path)
    edit_model(out, edit_cell("s_ln", lambda c: c.set("style", spelling + c.get("style")[len("swimlane;"):])))
    assert ('style="%s' % spelling) in out.read_text(encoding="utf-8")
    val = validate(out)
    assert val.returncode == 0 and "problems: 0" in val.stdout, val.stdout
    r = add(second_sheet(), out, tmp_path)
    assert r.returncode == 0, r.stderr
    # the strip is read for both spellings: a child moved into it warns
    edit_model(out, edit_cell("s_a", lambda c: c.find("mxGeometry").set("x", "20")))
    assert "overlaps the title strip of lane s_ln" in validate(out).stdout
    with pytest.raises(SystemExit, match="not a catalogue shape"):     # a spec still writes a lane as a lane
        ds.compile_spec({"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "cells": [
            {"id": "x", "text": "x", "x": 0, "y": 0, "style": "shape=swimlane;"}]}]})


# --------------------------------------------------------------------------- marker keys are parsed
def test_legend_and_record_markers_are_parsed_not_matched(tmp_path):
    spec = json.load(open(os.path.join(REFS, "example_L1_minimal.json"), encoding="utf-8"))
    r, out = build(spec, tmp_path)
    assert "vertices=16 legend=10" in validate(out, "--ladder").stdout

    def last_key_without_semicolon(m):
        for c in m.iter("mxCell"):
            st = c.get("style") or ""
            if st.endswith("dsLegend=1;"):
                c.set("style", st[:-1])
    edit_model(out, last_key_without_semicolon)
    val = validate(out, "--ladder")
    assert "vertices=16 legend=10" in val.stdout and val.returncode == 0, val.stdout

    def marker_first(m):
        for c in m.iter("mxCell"):
            st = c.get("style") or ""
            if "dsLegend=1" in st:
                c.set("style", "dsLegend=1;" + st.replace("dsLegend=1;", "").replace("dsLegend=1", ""))
    edit_model(out, marker_first)
    assert "vertices=16 legend=10" in validate(out, "--ladder").stdout
    assert ds.is_legend_cell({"style": "rounded=0;dsLegend = 1"}) and not ds.is_legend_cell({"style": "xdsLegend=1;"})
    assert ds.record_of("html=1;dsTheme = bw;dsAccent=#1F5FBF") == {"dsTheme": "bw", "dsAccent": "#1F5FBF"}
    # a layer record without its last ';' and spaces round '=' is read
    r, out = build(one_box_per_role(theme="bw"), tmp_path)
    out.write_text(re.sub(r' ds(Theme|Accent|MaxColours)="[^"]*"', "", out.read_text(encoding="utf-8")), encoding="utf-8")
    edit_model(out, lambda m: [c.set("style", "dsTheme = bw;dsAccent=#1F5FBF") for c in m.iter("mxCell") if c.get("id") == "1"])
    edit_model(out, delete_cells(lambda c: c.get("id") == "s_human"))
    assert ds.file_theme(ds.read_file(str(out)))[0] == "bw"


# --------------------------------------------------------------------------- the legend groups by what is drawn
@pytest.mark.parametrize("theme", ["minimal", "bw"])
def test_two_roles_drawn_alike_by_overrides_share_one_row_and_warn(theme, capsys):
    cells = [{"id": "plain", "text": "a", "x": 0, "y": 0, "style": "rounded=1;absoluteArcSize=1;arcSize=12;"},
             {"id": "prop", "role": "propose", "text": "b", "x": 0, "y": 100},
             {"type": "legend", "id": "lg", "x": 300, "y": 0}]
    (_, xml, _), = ds.compile_spec({"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    rows = legend_rows(xml)
    assert len(rows) == 1 and set(rows[0].split("; ")) == {"component", "an LLM or skill proposes"}, rows
    assert "boxes 'plain' (role plain) and 'prop' (role propose) are drawn alike" in capsys.readouterr().err
    # spelt differently, drawn the same, one role: one row and no warning
    cells = [{"id": "a", "text": "a", "x": 0, "y": 0, "style": "arcSize=40;"},
             {"id": "b", "text": "b", "x": 0, "y": 100}, {"type": "legend", "id": "lg", "x": 300, "y": 0}]
    (_, xml, _), = ds.compile_spec({"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    assert legend_rows(xml) == ["component"] and "different ways" not in capsys.readouterr().err


# --------------------------------------------------------------------------- the look-alike warning names remedies that clear it
def test_lookalike_warning_clears_by_outline_or_role_shapes(capsys):
    named = [{"id": "h", "shape": "decision", "role": "human", "text": "Max decides?", "x": 0, "y": 0, "w": 180, "h": 60},
             {"id": "d", "shape": "decision", "role": "decide", "text": "Code decides?", "x": 0, "y": 100, "w": 180, "h": 60}]
    for theme in ("minimal", "bw"):
        ds.compile_spec({"theme": theme, "sheets": [{"id": "s", "name": "T", "cells": named}]})
        err = capsys.readouterr().err
        assert "drawn alike" in err and "strokeWidth=2" in err and "does not clear this warning" in err
        outlined = json.loads(json.dumps(named))
        outlined[1]["style"] = "strokeWidth=2;"
        ds.compile_spec({"theme": theme, "sheets": [{"id": "s", "name": "T", "cells": outlined}]})
        assert capsys.readouterr().err == ""
        own = json.loads(json.dumps(named))
        for c in own:
            c.pop("shape")
        own[0]["h"] = own[1]["h"] = 40
        ds.compile_spec({"theme": theme, "sheets": [{"id": "s", "name": "T", "cells": own}]})
        assert "drawn alike" not in capsys.readouterr().err


# --------------------------------------------------------------------------- add refuses duplicate names in its batch
@pytest.mark.parametrize("theme", ["minimal", "bw", "colorful"])
def test_add_refuses_two_new_sheets_with_one_name_in_every_theme(theme, tmp_path):
    r, out = build(one_box_per_role(theme=theme), tmp_path)
    before = out.read_bytes()
    two = {"sheets": [{"id": "n1", "name": "New", "cells": [{"id": "a", "text": "a", "x": 0, "y": 0}]},
                      {"id": "n2", "name": "New", "cells": [{"id": "a", "text": "a", "x": 0, "y": 0}]}]}
    r = add(two, out, tmp_path, "--strict")
    assert r.returncode != 0 and "duplicate sheet name 'New'" in r.stderr, r.stderr
    assert out.read_bytes() == before and not os.path.exists(str(out) + ".bak")
    one = {"sheets": [{"id": "n1", "name": "L1 Test", "cells": [{"id": "a", "text": "a", "x": 0, "y": 0}]}]}
    r = add(one, out, tmp_path)
    assert r.returncode != 0 and "already exists" in r.stderr and out.read_bytes() == before
    if theme == "colorful":                    # a standalone colorful build keeps the pre-theme rule
        assert ds.compile_spec(two, "colorful")


# --------------------------------------------------------------------------- a misspelt grid key warns
def test_a_misspelt_grid_key_warns_with_the_nearest_name(capsys):
    def spec(grid):
        return {"sheets": [{"id": "s", "name": "L1 T", "grid": grid, "cells": [
            {"id": "a", "text": "a", "col": 1, "y": 0}]}]}
    for theme in ("minimal", "bw", "colorful"):
        try:
            ds.compile_spec(spec({"xO": 150, "pitch": 200, "col_width": 180}), theme)
        except SystemExit:
            pass
        assert "grid: unknown key 'xO' (nearest valid key: 'x0'); ignored" in capsys.readouterr().err, theme
    # colorful: a warning only; the output is the one without the key
    assert ds.compile_spec(spec({"xO": 150, "pitch": 200, "col_width": 180}), "colorful") == \
        ds.compile_spec(spec({"pitch": 200, "col_width": 180}), "colorful")


# --------------------------------------------------------------------------- rotation in the page-bounds check
def test_a_rotated_cell_is_measured_by_its_turned_bounds(tmp_path, capsys):
    def spec(rot):
        st = "rotation=%d;" % rot if rot else ""
        return {"sheets": [{"id": "s", "name": "L1 T", "width": 400, "height": 300, "cells": [
            {"id": "doc", "shape": "document", "text": "turned", "x": 30, "y": 0, "w": 180, "h": 40, "style": st}]}]}
    r, out = build(spec(90), tmp_path)
    assert "doc (x 100 to 140, y -70 to 110, rotated) is outside the page 400x300" in r.stderr, r.stderr
    assert "cell s_doc (turned) outside page 400x300" in validate(out, "--ladder").stdout
    for rot in (0, 180):
        r, out = build(spec(rot), tmp_path)
        assert "outside" not in r.stderr and "outside" not in validate(out).stdout, rot
    assert ds.rotated_bounds(30, 0, 180, 40, "rotation=90;") == (100.0, -70.0, 40.0, 180.0)


# --------------------------------------------------------------------------- edges move with their container
def edge_spec(theme):
    return {"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "width": 800, "height": 500, "cells": [
        {"type": "lane", "id": "ln", "x": 100, "y": 100, "w": 600, "h": 300, "text": "Owner"},
        {"id": "a", "text": "a", "x": 300, "y": 150, "w": 100, "h": 40},
        {"id": "b", "text": "b", "x": 500, "y": 300, "w": 100, "h": 40},
        {"id": "o", "text": "o", "x": 0, "y": 0, "w": 80, "h": 40},
        {"type": "edge", "id": "in", "from": "a", "to": "b", "points": [[410, 170], [410, 225]], "label": "x",
         "label_offset": [5, -8]},
        {"type": "edge", "id": "up", "from": "b", "to": "ln"},
        {"type": "edge", "id": "out", "from": "o", "to": "a", "points": [[40, 170]]}]}]}


def edge_cells(path):
    return {c.get("id"): c for c in ds.model_cells(ds.read_file(str(path))) if c.get("edge") == "1"}


def test_edges_are_parented_to_the_container_of_both_ends_in_minimal_and_bw(tmp_path):
    for theme in ("minimal", "bw", "colorful"):
        r, out = build(edge_spec(theme), tmp_path)
        assert r.returncode == 0, r.stderr
        e = edge_cells(out)
        pts = [(p.get("x"), p.get("y")) for p in e["s_in"].find("mxGeometry").find("Array")]
        offset = [p for p in e["s_in"].find("mxGeometry") if p.get("as") == "offset"][0]
        assert (offset.get("x"), offset.get("y")) == ("5", "-8")          # a displacement: unchanged
        if theme == "colorful":
            assert {c.get("parent") for c in e.values()} == {"1"} and pts == [("410", "170"), ("410", "225")]
        else:
            # both ends in the lane: the lane, points relative to it; an end that is the lane: the lane;
            # ends that share only the page: the page, points absolute
            assert e["s_in"].get("parent") == "s_ln" and pts == [("310", "70"), ("310", "125")], pts
            assert e["s_up"].get("parent") == "s_ln" and e["s_out"].get("parent") == "1"
            assert [(p.get("x"), p.get("y")) for p in e["s_out"].find("mxGeometry").find("Array")] == [("40", "170")]
        val = validate(out)
        assert "problems: 0" in val.stdout and "dangling" not in val.stdout, val.stdout
        assert "edges=3" in list_file(out).stdout
        assert add(second_sheet(), out, tmp_path).returncode == 0
        os.remove(str(out) + ".bak")
    # a file built before (every edge on the page layer) reads the same
    r, out = build(edge_spec("minimal"), tmp_path)
    def to_page(m):
        for c in m.iter("mxCell"):
            if c.get("edge") == "1" and c.get("parent") == "s_ln":
                c.set("parent", "1")
                for pt in c.iter("mxPoint"):
                    if pt.get("as") != "offset":
                        pt.set("x", str(int(pt.get("x")) + 100))
                        pt.set("y", str(int(pt.get("y")) + 100))
    edit_model(out, to_page)
    assert {c.get("parent") for c in edge_cells(out).values()} == {"1"}
    assert "problems: 0" in validate(out).stdout and add(second_sheet(), out, tmp_path).returncode == 0


# --------------------------------------------------------------------------- add writes atomically
def test_an_interrupted_add_leaves_the_original_bytes(tmp_path, monkeypatch):
    import argparse
    r, out = build(one_box_per_role(theme="bw"), tmp_path)
    before = out.read_bytes()
    p = tmp_path / "more.json"
    p.write_text(json.dumps(second_sheet()), encoding="utf-8")
    ns = argparse.Namespace(spec=str(p), into=str(out), position="last", theme=None, max_colours=None, strict=False)

    real = ds.ET.ElementTree.write

    def broken(self, path, *a, **k):
        if not (isinstance(path, str) and path.endswith(".tmp")):
            return real(self, path, *a, **k)
        with open(path, "w", encoding="utf-8") as f:
            f.write("<mxfile")                       # seven bytes, then the disk fails
        raise OSError("simulated write failure")
    monkeypatch.setattr(ds.ET.ElementTree, "write", broken)
    with pytest.raises(OSError, match="simulated"):
        ds.cmd_add(ns)
    assert out.read_bytes() == before and not os.path.exists(str(out) + ".bak")
    assert sorted(os.listdir(str(tmp_path))) == ["more.json", "out.drawio", "spec.json"]
    monkeypatch.undo()
    assert ds.cmd_add(ns) == 0
    assert "L2 More" in out.read_text(encoding="utf-8") and os.path.exists(str(out) + ".bak")
    assert sorted(os.listdir(str(tmp_path))) == ["more.json", "out.drawio", "out.drawio.bak", "spec.json"]


# --------------------------------------------------------------------------- the plugin is a lane on L1
def test_l1_example_draws_its_three_owners_as_column_lanes():
    spec = json.load(open(os.path.join(REFS, "example_plugin_3_levels_minimal.json"), encoding="utf-8"))
    l1 = spec["sheets"][0]
    lanes = [c for c in l1["cells"] if c.get("type") == "lane"]
    assert [c["text"] for c in lanes] == ["Max", "The plugin", "Outside the plugin"]
    assert all(c.get("orientation") == "column" and c["y"] == 80 and c.get("link") == "L2 Stages and layers"
               for c in lanes)
    assert not any(c.get("square") for c in l1["cells"])
    decide = [c for c in l1["cells"] if c.get("id") == "decide"][0]
    assert decide["h"] == "auto"
    only = json.load(open(os.path.join(REFS, "example_L1_minimal.json"), encoding="utf-8"))["sheets"][0]
    for c in only["cells"] + l1["cells"]:
        c.pop("link", None)
    assert only == l1
    for c in spec["sheets"][1]["cells"]:
        if c.get("type") == "lane":
            assert c.get("link") == "L3 Detail P0 to P11", c["id"]


def test_l2_lanes_open_the_l3_sheet_in_the_compiled_file(tmp_path):
    spec = json.load(open(os.path.join(REFS, "example_plugin_3_levels_minimal.json"), encoding="utf-8"))
    for theme in ("minimal", "bw"):
        r, out = build(spec, tmp_path, "--theme", theme)
        root = ds.read_file(str(out))
        l3 = [d.get("id") for d in root.findall("diagram") if d.get("name") == "L3 Detail P0 to P11"][0]
        l2 = [d for d in root.findall("diagram") if d.get("name") == "L2 Stages and layers"][0]
        lanes = [c for c in ds.model_cells(l2) if (c.get("style") or "").startswith("swimlane;")]
        assert len(lanes) == 6 and all(c.wrapper is not None and c.wrapper.get("link") == "data:page/id," + l3
                                       for c in lanes), [c.get("id") for c in lanes]


# --------------------------------------------------------------------------- the rebuild loop uses --force
def test_build_refusal_names_force_first(tmp_path):
    r, out = build(one_box_per_role(), tmp_path)
    p = tmp_path / "spec.json"
    r = subprocess.run([sys.executable, SCRIPT, "build", str(p), "-o", str(out)], capture_output=True, text=True)
    assert r.returncode != 0 and r.stderr.index("--force") < r.stderr.index("'add'"), r.stderr


# --------------------------------------------------------------------------- a legend row for frames
@pytest.mark.parametrize("theme", ["minimal", "bw"])
def test_frames_get_a_legend_row_and_a_dashed_box_in_bw_warns(theme, capsys):
    cells = [{"id": "f", "square": True, "text": "Set", "x": 0, "y": 0, "w": 300, "h": 200},
             {"id": "a", "text": "a", "x": 20, "y": 60, "w": 100, "h": 40},
             {"type": "legend", "id": "lg", "x": 400, "y": 0}]
    (_, xml, _), = ds.compile_spec({"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    assert legend_rows(xml) == ["component", "group of parts"]
    sample = ds.parse_style(re.search(r'value="group of parts" style="([^"]*)"', xml).group(1))[0]
    assert sample["fillColor"] == ("#F2F2F2" if theme == "minimal" else "#FFFFFF") and sample["rounded"] == "0"
    assert (sample.get("dashed") == "1") == (theme == "bw")
    assert capsys.readouterr().err == ""
    cells[2]["labels"] = {"frame": "set of rows"}
    (_, xml, _), = ds.compile_spec({"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    assert legend_rows(xml) == ["component", "set of rows"] and capsys.readouterr().err == ""
    dashed = cells[:2] + [{"id": "d", "text": "d", "x": 0, "y": 300, "dashed": True},
                          {"id": "d2", "text": "d2", "x": 200, "y": 300, "style": "dashed=1;"}]
    ds.compile_spec({"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "cells": dashed}]})
    err = capsys.readouterr().err
    assert (err.count("reads as one") == 2) == (theme == "bw"), err
    (_, xml, _), = ds.compile_spec({"theme": "colorful", "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    assert "group of parts" not in xml and "set of rows" not in xml       # colorful lists frames by role colour


# --------------------------------------------------------------------------- the accent colour
def test_accent_colour_is_normalised_and_must_be_visible(tmp_path):
    assert ds.resolve_theme({"accent_color": "#00f"})[1] == "#0000FF"
    assert ds.resolve_theme({"accent_color": "#1f5fbf"})[1] == "#1F5FBF"
    for bad, msg in (("red", "is not a colour"), ("#12345", "is not a colour"), ("white", "is white"),
                     ("#F3F3F3", "contrast of only"), ("#FFFF00", "contrast of only"), ("#9A9A9A", "against the grey tint")):
        with pytest.raises(SystemExit, match=msg):
            ds.resolve_theme({"accent_color": bad})
    # a file that recorded a pale accent (built before this check) is still read
    spec = one_box_per_role(theme="colorful", accent_color="#123456")
    r, out = build(spec, tmp_path)
    text = out.read_text(encoding="utf-8")
    assert ds.file_theme(ds.ET.fromstring(text.replace("#123456", "#FFFF00")))[1] == "#FFFF00"


# =========================================================================== v10
# --------------------------------------------------------------------------- A-aera-1: the script's markers are reserved
@pytest.mark.parametrize("theme", ["minimal", "bw", "colorful"])
@pytest.mark.parametrize("key", ["dsTheme", "dsAccent", "dsMaxColours", "dsLegend"])
def test_a_raw_style_with_a_reserved_marker_key_stops_build_and_add(theme, key, tmp_path):
    value = {"dsTheme": "bw", "dsAccent": "#1F5FBF", "dsMaxColours": "3", "dsLegend": "1"}[key]
    spec = {"theme": theme, "sheets": [{"id": "s", "name": "T", "cells": [
        {"id": "a", "text": "a", "x": 0, "y": 0, "style": "fontSize=9; %s=%s" % (key, value)}]}]}
    r, out = build(spec, tmp_path)
    assert r.returncode != 0 and not out.exists(), r.stderr
    assert "cell 'a': style key %s is reserved" % key in r.stderr, r.stderr
    edge = {"theme": theme, "sheets": [{"id": "s", "name": "T", "cells": [
        {"id": "a", "text": "a", "x": 0, "y": 0}, {"id": "b", "text": "b", "x": 200, "y": 0},
        {"type": "edge", "id": "e", "from": "a", "to": "b", "style": "%s=%s;" % (key, value)}]}]}
    with pytest.raises(SystemExit, match="cell 'e': style key %s is reserved" % key):
        ds.compile_spec(edge)
    # add: refused before any backup or write
    r, out = build({"theme": theme, "sheets": [{"id": "s", "name": "T", "cells": [
        {"id": "a", "text": "a", "x": 0, "y": 0}]}]}, tmp_path)
    assert r.returncode == 0, r.stderr
    before = out.read_bytes()
    more = json.loads(json.dumps(spec))
    more["sheets"][0].update({"id": "t", "name": "T2"})
    more.pop("theme")
    r = add(more, out, tmp_path)
    assert r.returncode != 0 and "is reserved" in r.stderr
    assert out.read_bytes() == before and not os.path.exists(str(out) + ".bak")


# --------------------------------------------------------------------------- A-aera-2: rotated cells measured as drawn
def test_a_quarter_turned_box_is_contained_and_checked_by_its_drawn_bounds(tmp_path, capsys):
    lane = {"type": "lane", "id": "ln", "x": 0, "y": 100, "w": 600, "h": 160, "text": "Owner"}
    # the reviewer's case: 180x40 at (200, 130) turned 90 covers x 270-310, y 60-240: 40 px above the lane
    # and 40x40 px of the neighbour n, which its stored geometry does not touch
    cells = [lane, {"id": "b", "text": "b", "x": 200, "y": 130, "w": 180, "h": 40, "style": "rotation=90;"},
             {"id": "n", "text": "n", "x": 270, "y": 200, "w": 80, "h": 40}]
    for theme in ("minimal", "bw"):
        (_, xml, _), = ds.compile_spec({"theme": theme, "sheets": [{"id": "s", "name": "T", "cells": cells}]})
        err = capsys.readouterr().err
        assert "ln and b overlap (40x140 px)" in err and "b and n overlap (40x40 px)" in err, err
        # not held by the lane it leaves; the stored geometry is unchanged
        assert re.search(r'id="s_b" value="b" style="[^"]*" vertex="1" parent="1"><mxGeometry x="200" y="130" '
                         r'width="180" height="40"', xml), xml
        assert re.search(r'id="s_n" [^>]*parent="s_ln"', xml)
    # turned back (0 or 180) it fits its lane again; the neighbour moved clear: no warning
    for rot in (0, 180):
        ok = json.loads(json.dumps(cells))
        ok[1]["style"] = "rotation=%d;" % rot
        ok[2]["x"] = 420
        ds.compile_spec({"sheets": [{"id": "s", "name": "T", "cells": ok}]})
        assert capsys.readouterr().err == "", rot
    # a turned box whose stored geometry clears the title strip but whose drawn shape enters it
    strip = [lane, {"id": "s", "text": "", "x": 160, "y": 110, "w": 40, "h": 180, "style": "rotation=90;"}]
    r, out = build({"sheets": [{"id": "s", "name": "T", "cells": strip}]}, tmp_path)
    assert "s overlaps the title strip of lane ln (x=90; contents start at x>=150)" in r.stderr, r.stderr
    assert "cell s_s () overlaps the title strip of lane s_ln" in validate(out).stdout


# --------------------------------------------------------------------------- A-aera-4: an edge on a black body
@pytest.mark.parametrize("theme", ["minimal", "bw"])
def test_an_edge_on_a_black_frame_is_drawn_white_or_warns(theme, capsys):
    def sheet(**edge):
        return {"theme": theme, "sheets": [{"id": "s", "name": "T", "cells": [
            {"id": "f", "square": True, "fill": "black", "text": "Black set", "x": 0, "y": 0, "w": 400, "h": 200},
            {"id": "a", "text": "a", "x": 20, "y": 60, "w": 100, "h": 40},
            {"id": "b", "text": "b", "x": 260, "y": 60, "w": 100, "h": 40},
            {"id": "o", "text": "o", "x": 500, "y": 60, "w": 100, "h": 40},
            dict({"type": "edge", "id": "in", "from": "a", "to": "b"}, **edge),
            {"type": "edge", "id": "out", "from": "b", "to": "o"}]}]}
    (_, xml, _), = ds.compile_spec(sheet())
    st = {m.group(1): m.group(2) for m in re.finditer(r'<mxCell id="s_([^"]+)" value="[^"]*" style="([^"]*)" edge="1"', xml)}
    assert ds.parse_style(st["in"])[0]["strokeColor"] == "#FFFFFF" and 'parent="s_f"' in xml
    assert ds.parse_style(st["out"])[0]["strokeColor"] == "#000000"       # on the page: black as before
    assert capsys.readouterr().err == ""
    ds.compile_spec(sheet(color="black"))
    err = capsys.readouterr().err
    assert "runs on the body of frame f, which is #000000, and cannot be seen; use the colour #FFFFFF" in err, err
    ds.compile_spec(sheet(accent=True))
    err = capsys.readouterr().err
    if theme == "bw":       # the accent is a black line
        assert "cannot be seen; drop the accent or the container's fill" in err, err
    else:                   # the blue accent contrasts with black (3.4:1)
        assert "cannot be seen" not in err, err


# --------------------------------------------------------------------------- A-aera-5 / A-aera-6: export
def test_export_names_are_resolved_before_rendering_and_never_collide():
    names = ["L4 Review A", "L4 Review_A", "L1 a", "L1 A", "L2 한글", "L2 영어", "Solo", "L2 __", "L2 __ _p5"]
    files = ds.export_names("f", names, "png")
    assert len(set(f.lower() for f in files)) == len(files), files
    assert files[0] == "f__L4_Review_A_p1.png" and files[1] == "f__L4_Review_A_p2.png"
    assert files[2] == "f__L1_a_p3.png" and files[3] == "f__L1_A_p4.png"
    assert files[6] == "f__Solo.png"
    assert ds.export_names("f", ["L1 Overview", "L2 Stages and layers"], "png") == [
        "f__L1_Overview.png", "f__L2_Stages_and_layers.png"]


@pytest.mark.parametrize("base", [1, 0])
def test_export_finds_the_renderer_page_base_once_and_exports_every_page(base, tmp_path, monkeypatch, capsys):
    import argparse
    import types
    spec = {"sheets": [{"id": n.lower(), "name": n, "cells": [{"id": "a", "text": n, "x": 0, "y": 0}]}
                       for n in ("Cover", "Detail", "Appendix")]}
    r, out = build(spec, tmp_path)
    assert r.returncode == 0, r.stderr
    pages = ["Cover", "Detail", "Appendix"]
    calls = []

    def stub(cmd, **kw):
        p, dest = int(cmd[cmd.index("-p") + 1]), cmd[cmd.index("-o") + 1]
        calls.append(p)
        k = p - base
        if not 0 <= k < len(pages):
            return types.SimpleNamespace(returncode=1, stdout="Error: Invalid page index", stderr="")
        with open(dest, "w", encoding="utf-8") as f:
            f.write(pages[k])
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(ds, "find_drawio", lambda: "stub-renderer")
    monkeypatch.setattr(ds.subprocess, "run", stub)
    outdir = tmp_path / "png"
    rc = ds.cmd_export(argparse.Namespace(file=str(out), format="png", out=str(outdir), scale="1.5"))
    assert rc == 0, capsys.readouterr()
    for n in pages:
        assert (outdir / ("out__%s.png" % n)).read_text(encoding="utf-8") == n
    assert len(calls) == (3 if base == 1 else 4), calls      # one probe on the last page, then one call a page


# --------------------------------------------------------------------------- A-aera-7: accent drops stroke keys written with spaces
def test_an_accent_drops_stroke_keys_written_with_spaces(capsys):
    cells = [{"id": "a", "text": "a", "x": 0, "y": 0, "accent": True, "style": "strokeColor=#FF0000; strokeWidth=7;"},
             {"id": "p", "text": "p", "x": 0, "y": 100},
             {"id": "q", "text": "q", "x": 200, "y": 100, "style": "strokeColor=#FF0000; strokeWidth=7;"},
             {"type": "edge", "id": "e", "from": "a", "to": "p", "accent": True, "style": " strokeWidth=7; endArrow=open;"},
             {"type": "legend", "x": 400, "y": 0}]
    (_, xml, _), = ds.compile_spec({"theme": "colorful", "sheets": [{"id": "s", "name": "T", "cells": cells}]})
    st = styles(xml)
    assert "strokeWidth=7" not in st["a"] and "#FF0000" not in st["a"], st["a"]
    edge = re.search(r'id="s_e" value="[^"]*" style="([^"]*)"', xml).group(1)
    assert "strokeWidth=7" not in edge and " endArrow=open;" in edge, edge
    # without the accent the raw style is written as given
    assert st["q"].endswith("strokeColor=#FF0000; strokeWidth=7;"), st["q"]
    legend = "".join(m.group(0) for m in re.finditer(r'<mxCell[^>]*dsLegend=1;[^>]*>', xml))
    # rows: plain (p, and a without its accent), plain as q draws it, the accent; v9 gave a a row of its own
    assert legend_rows(xml) == ["component", "component", "highlighted"] and legend.count("strokeWidth=7") == 1, legend
    # a: accented plain box, p: plain box -> one role row; q (red, wide) is a second look of plain
    assert "boxes of role 'plain' are painted 2 different ways" in capsys.readouterr().err


# --------------------------------------------------------------------------- A-aera-8: an empty sheet list
def test_an_empty_sheet_list_stops_build_and_add_and_validate_reports_a_file_without_sheets(tmp_path):
    for theme in ("minimal", "bw", "colorful"):
        for spec in ({"sheets": []}, {}):
            with pytest.raises(SystemExit, match="the spec has no sheets"):
                ds.compile_spec(dict(spec, theme=theme))
        r, out = build({"theme": theme, "sheets": []}, tmp_path)
        assert r.returncode != 0 and not out.exists() and "no sheets" in r.stderr
    r, out = build(one_box_per_role(theme="bw"), tmp_path)
    assert add(second_sheet(), out, tmp_path).returncode == 0
    bak = (tmp_path / "out.drawio.bak").read_bytes()
    before = out.read_bytes()
    for args in ((), ("--strict",)):
        r = add({"sheets": []}, out, tmp_path, *args)
        assert r.returncode != 0 and "no sheets" in r.stderr, r.stderr
        assert out.read_bytes() == before and (tmp_path / "out.drawio.bak").read_bytes() == bak
    empty = tmp_path / "empty.drawio"
    empty.write_text('<mxfile host="app.diagrams.net"></mxfile>', encoding="utf-8")
    val = validate(empty, "--ladder")
    assert val.returncode == 1 and "holds no sheets" in val.stdout and "problems: 1" in val.stdout, val.stdout
    assert "warn the file holds no sheets" in list_file(empty).stdout


# --------------------------------------------------------------------------- A-aera-9: each legend its own labels
@pytest.mark.parametrize("theme", ["minimal", "bw", "colorful"])
def test_two_legends_on_one_sheet_print_their_own_labels(theme, capsys):
    cells = [{"id": "h", "role": "human", "text": "h", "x": 0, "y": 0},
             {"type": "legend", "id": "l1", "x": 300, "y": 0, "labels": {"human": "First owner"}},
             {"type": "legend", "id": "l2", "x": 600, "y": 0, "labels": {"human": "Second owner"}}]
    (_, xml, _), = ds.compile_spec({"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    assert legend_rows(xml) == ["First owner", "Second owner"], legend_rows(xml)
    assert capsys.readouterr().err == ""


# --------------------------------------------------------------------------- A-aera-11: accent is a JSON boolean
@pytest.mark.parametrize("theme", ["minimal", "bw", "colorful"])
def test_accent_must_be_a_json_boolean(theme):
    def spec(box=None, edge=None):
        a, e = {"id": "a", "text": "a", "x": 0, "y": 0}, {"type": "edge", "id": "e", "from": "a", "to": "b"}
        if box is not None:
            a["accent"] = box
        if edge is not None:
            e["accent"] = edge
        return {"theme": theme, "sheets": [{"id": "s", "name": "T", "cells": [
            a, {"id": "b", "text": "b", "x": 200, "y": 0}, e]}]}
    for bad in ("false", "true", 1, 0):
        with pytest.raises(SystemExit, match=r"cell 'a': accent .* must be true or false"):
            ds.compile_spec(spec(box=bad))
        with pytest.raises(SystemExit, match=r"cell 'e': accent .* must be true or false"):
            ds.compile_spec(spec(edge=bad))
    (_, on, _), = ds.compile_spec(spec(box=True, edge=True))
    (_, off, _), = ds.compile_spec(spec(box=False, edge=False))
    (_, unset, _), = ds.compile_spec(spec())
    assert off == unset and on != off


# --------------------------------------------------------------------------- C-deb-1, C-deb-4: documented numbers
def test_documented_headline_room_and_legend_limit_match_the_script():
    # one line at font 9 needs 1 x 1.35 x 9 + 12 = 24.15 px, rounded up to 25
    assert ds.needed_height("Head", 300, 9, "process")[1] == 25
    text = re.sub(r"\s+", " ", open(os.path.join(REFS, "spec_format.md"), encoding="utf-8").read())
    assert "one line at font 9: 25 px" in text and "4 × font when that is larger" in text
    # the legend's label room: 40 px, or 4 x font when that is larger (48 at font 12)
    cell = {"type": "legend", "x": 300, "y": 0, "w": 10 + 44 + 8 + 8 + 44, "font": 12}
    with pytest.raises(SystemExit, match="leaves 44 px for labels; make it at least 118 wide"):
        ds.compile_spec({"sheets": [{"id": "s", "name": "L1 T", "cells": [{"id": "a", "text": "a", "x": 0, "y": 0}, cell]}]})
    ds.compile_spec({"sheets": [{"id": "s", "name": "L1 T", "cells": [{"id": "a", "text": "a", "x": 0, "y": 0},
                                                                     dict(cell, font=9)]}]})


# --------------------------------------------------------------------------- C-deb-2: a legend placed by col
@pytest.mark.parametrize("theme", ["minimal", "bw", "colorful"])
def test_a_legend_placed_by_col_takes_width_220_without_col_width(theme):
    cells = [{"id": "a", "text": "a", "x": 0, "y": 0, "w": 120}, {"type": "legend", "id": "lg", "col": 1, "y": 0}]
    (_, xml, _), = ds.compile_spec({"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "grid": {"pitch": 300},
                                                                "cells": cells}]})
    assert re.search(r'<mxCell id="s_lg" [^>]*><mxGeometry x="300" y="0" width="220"', xml), xml
    (_, with_cw, _), = ds.compile_spec({"theme": theme, "sheets": [{"id": "s", "name": "L1 T",
                                                                    "grid": {"pitch": 300, "col_width": 90}, "cells": cells}]})
    assert with_cw == xml


# --------------------------------------------------------------------------- C-deb-3: the accent contrast only where it is drawn
def test_accent_contrast_is_checked_only_where_the_accent_colour_is_drawn(tmp_path, capsys):
    pale = "#FFFF00"
    for top in ({"theme": "bw"}, {"theme": "minimal", "max_colours": 3}):
        s = one_box_per_role(accent_color=pale, **top)
        s["sheets"][0]["name"] = "Test"
        capsys.readouterr()
        ds.compile_spec(s)
        assert "accent_color #FFFF00 is ignored" in capsys.readouterr().err
        r, out = build(s, tmp_path, "--strict")
        assert r.returncode == 1 and out.exists() and r.stderr.count("is ignored") == 1, r.stderr
        with pytest.raises(SystemExit, match="is not a colour"):
            ds.compile_spec(dict(s, accent_color="red"))
        with pytest.raises(SystemExit, match="is white"):
            ds.compile_spec(dict(s, accent_color="#FFFFFF"))
    r, out = build(one_box_per_role(accent_color=pale), tmp_path, "--max-colours", "3")
    assert r.returncode == 0 and "is ignored" in r.stderr, r.stderr
    for top in ({"theme": "minimal"}, {"theme": "colorful"}):
        with pytest.raises(SystemExit, match="contrast of only"):
            ds.compile_spec(one_box_per_role(accent_color=pale, **top))
    # add into a bw file: a pale accent warns as before and no longer stops
    r, out = build(one_box_per_role(theme="bw"), tmp_path)
    r = add(second_sheet(accent_color=pale), out, tmp_path)
    assert r.returncode == 0 and "accent_color #FFFF00 is ignored" in r.stderr and "contrast of only" not in r.stderr, r.stderr


# --------------------------------------------------------------------------- C-deb-5: manual operation may carry human
@pytest.mark.parametrize("theme", ["minimal", "bw"])
def test_manual_operation_carries_the_human_role(theme, capsys):
    cells = [{"id": "h", "role": "human", "shape": "manual_operation", "text": "Max sorts", "x": 0, "y": 0, "w": 160},
             {"id": "p", "shape": "manual_operation", "text": "plain", "x": 0, "y": 100, "w": 160},
             {"type": "legend", "x": 300, "y": 0}]
    (_, xml, _), = ds.compile_spec({"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    err = capsys.readouterr().err
    assert "hides role" not in err, err
    assert legend_rows(xml) == ["manual operation: a person decides; manual operation: component"], legend_rows(xml)
    cells[1]["role"] = "propose"
    ds.compile_spec({"theme": theme, "sheets": [{"id": "s", "name": "L1 T", "cells": cells}]})
    assert "shape 'manual_operation' hides role 'propose'" in capsys.readouterr().err
    assert "room for bulleted text" in open(os.path.join(REFS, "spec_format.md"), encoding="utf-8").read()


# --------------------------------------------------------------------------- C-deb-7: a ladder sheet needs a legend
def test_build_warns_on_an_l1_to_l4_sheet_without_a_legend_in_minimal_and_bw(tmp_path, capsys):
    def spec(name, theme, legend=False):
        cells = [{"id": "a", "text": "a", "x": 0, "y": 0}]
        if legend:
            cells.append({"type": "legend", "x": 300, "y": 0})
        return {"theme": theme, "sheets": [{"id": "s", "name": name, "cells": cells}]}
    for theme in ("minimal", "bw"):
        for name in ("L1 A", "L2 B", "L3 C", "L4 D"):
            ds.compile_spec(spec(name, theme))
            assert "has no legend cell" in capsys.readouterr().err, (theme, name)
            ds.compile_spec(spec(name, theme, legend=True))
            assert capsys.readouterr().err == ""
        for name in ("L0 Task", "Notes"):
            ds.compile_spec(spec(name, theme))
            assert capsys.readouterr().err == "", name
        r, out = build(spec("L2 B", theme), tmp_path, "--strict")
        assert r.returncode == 1 and "warnings: 1" in r.stdout
    ds.compile_spec(spec("L1 A", "colorful"))
    assert capsys.readouterr().err == ""


# --------------------------------------------------------------------------- C-deb-9: validate --strict
def test_validate_strict_exits_1_on_any_warn_line(tmp_path):
    r, out = build({"sheets": [{"id": "s", "name": "Notes", "cells": [{"id": "a", "text": "a", "x": 0, "y": 0}]}]},
                   tmp_path)
    plain = validate(out, "--ladder")
    assert plain.returncode == 0 and "warn Notes: name has no L<n> prefix" in plain.stdout, plain.stdout
    strict = validate(out, "--ladder", "--strict")
    assert strict.returncode == 1 and "--strict: 1 warn line(s)" in strict.stdout, strict.stdout
    for name in ("example_L1_minimal.json", "example_plugin_3_levels_minimal.json"):
        for theme in ("minimal", "bw"):
            r, out = build(json.load(open(os.path.join(REFS, name), encoding="utf-8")), tmp_path, "--theme", theme)
            v = validate(out, "--ladder", "--strict")
            assert v.returncode == 0 and "problems: 0" in v.stdout and "warn" not in v.stdout, v.stdout

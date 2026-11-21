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
    cells = [{"id": r, "role": r, "text": r, "x": 10, "y": 10 + 70 * k, "w": 120, "h": 60}
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
        assert v == 5   # lane, lane title, frame, kid, free: the count is unchanged
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
    lane["fill"] = "#1F5FBF"                              # a lane never sets accent: ignored
    spec["sheets"][0]["cells"] += [{"type": "edge", "id": "e2", "from": "plain", "to": "note", "color": "#1F5FBF"},
                                   {"type": "legend", "x": 420, "y": 10, "labels": {"accent": "the path"}}]
    r, out = build(spec, tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stderr.count("is the accent colour, which only a cell or edge that sets accent may use") == 3
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
    r, out = build(one_box_per_role(max_colors=3), tmp_path)
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
    assert text.count("dsTheme=minimal;dsAccent=#1F5FBF;dsMaxColours=3;") == 2      # one per sheet
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

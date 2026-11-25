"""Tests of lib.styles: the expected look per cell type, the Excel operation shape, the comparison and its
equivalences, writing the look with openpyxl and reading it back, named styles, the default font
(design sections 10, 12, 14; F12). Owner: M2."""
from __future__ import annotations

import datetime as dt
import zipfile
from pathlib import Path

import pytest

from lib import dictionary, excel, styles, workbook


@pytest.fixture(scope="module")
def d() -> dictionary.Dictionary:
    return dictionary.load_dictionary()


class Cell:
    """A stand-in for workbook.CellInfo with only the style attributes."""

    def __init__(self, **kw):
        base = {"font_name": "Arial", "font_size": 9.0, "bold": False, "italic": False, "font_color": None,
                "fill": None, "number_format": "General", "border": {}, "h_align": None, "wrap": False, "value": 1}
        base.update(kw)
        self.__dict__.update(base)


# ----------------------------------------------------------------------------- expected look (Max's fixed points)

def test_every_cell_type_is_arial_8_or_9(d: dictionary.Dictionary) -> None:
    for t in d.cell_types():
        for size in (8, 9):
            e = styles.expected_style(d, t, sheet_size=size)
            assert e["font_name"] == "Arial"
            assert e["font_size"] in (8, 9)
            assert e["cell_type"] == t


def test_title_is_9_on_a_dense_sheet_and_others_follow_the_sheet(d: dictionary.Dictionary) -> None:
    assert styles.expected_style(d, "title", sheet_size=8)["font_size"] == 9
    assert styles.expected_style(d, "calc", sheet_size=8)["font_size"] == 8
    assert styles.expected_style(d, "calc", sheet_size=9)["font_size"] == 9


def test_headers_grey_and_row_headers_lighter(d: dictionary.Dictionary) -> None:
    col = styles.expected_style(d, "col_header", sheet_size=9)
    row = styles.expected_style(d, "row_header", sheet_size=9)
    assert col["fill"] == "D9D9D9" and row["fill"] == "F2F2F2"
    assert int(row["fill"][:2], 16) > int(col["fill"][:2], 16)       # lighter
    assert col["bold"] is True and row["bold"] is False
    assert col["border"] == {"bottom": {"style": "thin", "color": "808080"}}


def test_minimal_colour_every_used_colour_is_in_the_palette(d: dictionary.Dictionary) -> None:
    pal = d.palette()
    for t in d.cell_types():
        e = styles.expected_style(d, t, sheet_size=9)
        for v in (e["font_color"], e["fill"]):
            assert v is None or v in pal
        for side in e["border"].values():
            assert side["color"] in pal


def test_number_format_item_is_none_unless_a_key_is_given(d: dictionary.Dictionary) -> None:
    assert styles.expected_style(d, "calc", sheet_size=8)["number_format"] is None
    assert styles.expected_style(d, "calc", sheet_size=8, format_key="rate")["number_format"] == "0.0%"
    assert styles.expected_style(d, "check", sheet_size=9)["number_format"] == '[Red]"FAIL";[Red]"FAIL";"OK"'
    assert styles.expected_style(d, "check", sheet_size=9, format_key="rate")["number_format"].startswith("[Red]")


def test_named_override_changes_the_expected_look() -> None:
    blue = dictionary.load_dictionary(use=["input_blue_font"])
    e = styles.expected_style(blue, "input", sheet_size=9)
    assert e["font_color"] == "0000FF" and e["fill"] is None
    unit = styles.expected_style(dictionary.load_dictionary(use=["unit_unfilled"]), "unit", sheet_size=9)
    assert unit["fill"] is None
    total = styles.expected_style(dictionary.load_dictionary(use=["total_grey_rule"]), "total", sheet_size=8)
    assert total["border"] == {"top": {"style": "thin", "color": "808080"}}


# ----------------------------------------------------------------------------- Excel operation shape

@pytest.mark.parametrize("size", [8, 9])
def test_style_spec_is_a_valid_excel_operation_for_every_type(d: dictionary.Dictionary, size: int) -> None:
    for t in d.cell_types():
        spec = styles.style_spec(d, t, size=size, value=12.5)
        op = {"op": "style", "sheet": "calc", "ranges": ["B4:D9", "F2"], **spec}
        norm = excel.normalize_operations([op])[0]
        assert norm["font"]["name"] == "Arial"
        assert norm["font"]["size"] == d.size_for(t, size)
        assert set(norm["border"]) == {"top", "bottom", "left", "right"}


def test_style_spec_attributes_limit_the_keys(d: dictionary.Dictionary) -> None:
    spec = styles.style_spec(d, "input", size=9, attributes=styles.THEME_ATTRIBUTES)
    assert set(spec) == {"font", "fill"}
    assert spec["font"] == {"name": "Arial", "size": 9, "bold": False, "italic": False, "color": "000000"}
    assert spec["fill"] == "FFF2CC"
    raw_text = styles.style_spec(d, "raw", size=8, value="East", attributes=("align",))
    raw_num = styles.style_spec(d, "raw", size=8, value=3.0, attributes=("align",))
    assert raw_text["align"]["horizontal"] == "left" and raw_num["align"]["horizontal"] == "right"


def test_style_spec_none_fill_means_remove_the_fill(d: dictionary.Dictionary) -> None:
    spec = styles.style_spec(d, "calc", size=8, attributes=("fill",))
    assert "fill" in spec and spec["fill"] is None
    assert excel.normalize_operations([{"op": "style", "sheet": "s", "ranges": "A1", **spec}])[0]["fill"] is None


# ----------------------------------------------------------------------------- comparison

def test_compare_cell_finds_each_attribute(d: dictionary.Dictionary) -> None:
    e = styles.expected_style(d, "col_header", sheet_size=8)
    cell = Cell(font_name="Calibri", font_size=11.0, bold=False, font_color="FFFFFF", fill="1F3864")
    got = styles.compare_cell(cell, e)
    assert got == ["font_name Calibri, expected Arial", "font_size 11, expected 8", "font_color FFFFFF, expected 000000",
                   "bold no, expected yes", "fill 1F3864, expected D9D9D9"]
    rules = [m.rule for m in styles.mismatches(cell, e)]
    assert rules == ["S-FONT", "S-SIZE", "S-FONTCOLOR", "S-BOLD", "S-FILL"]


def test_compare_cell_equivalences(d: dictionary.Dictionary) -> None:
    e = styles.expected_style(d, "calc", sheet_size=8)
    # automatic font colour counts as 000000; font names without case; sizes within 0.01 point
    assert styles.compare_cell(Cell(font_name="ARIAL", font_size=8.004, font_color=None), e) == []
    # a white fill is a fill
    assert styles.compare_cell(Cell(font_size=8.0, fill="FFFFFF"), e) == ["fill FFFFFF, expected none"]
    # general alignment shows numbers right and text left
    e_all = styles.expected_style(d, "calc", sheet_size=8)
    assert styles.compare_cell(Cell(font_size=8.0, value=5, h_align=None), e_all, attributes=("align",)) == []
    assert styles.compare_cell(Cell(font_size=8.0, value=5, h_align="left"), e_all, attributes=("align",)) == \
        ["align left, expected right"]
    raw = styles.expected_style(d, "raw", sheet_size=8)
    assert styles.compare_cell(Cell(value="East", h_align=None), raw, attributes=("align",)) == []
    assert styles.compare_cell(Cell(value=3.5, h_align="general"), raw, attributes=("align",)) == []
    head = styles.expected_style(d, "col_header", sheet_size=9)
    assert styles.compare_cell(Cell(value="Jan", h_align="centerContinuous"), head, attributes=("align",)) == []


def test_compare_cell_number_format_and_border(d: dictionary.Dictionary) -> None:
    chk = styles.expected_style(d, "check", sheet_size=9)
    assert styles.compare_cell(Cell(number_format="0"), chk, attributes=("number_format",)) == \
        ['number_format 0, expected [Red]"FAIL";[Red]"FAIL";"OK"']
    calc = styles.expected_style(d, "calc", sheet_size=8)
    assert styles.compare_cell(Cell(number_format="0.00"), calc, attributes=("number_format",)) == []   # item
    total = styles.expected_style(d, "total", sheet_size=8)
    good = Cell(border={"top": {"style": "thin", "color": None}})        # automatic colour counts as 000000
    assert styles.compare_cell(good, total, attributes=("border",)) == []
    bad = Cell(border={"top": {"style": "thin", "color": "000000"}, "bottom": {"style": "double", "color": "000000"}})
    assert styles.compare_cell(bad, total, attributes=("border",)) == \
        ["border bottom double 000000, top thin 000000, expected top thin 000000"]


def test_compare_cell_refuses_an_unknown_attribute(d: dictionary.Dictionary) -> None:
    with pytest.raises(ValueError, match="unknown style attribute"):
        styles.compare_cell(Cell(), styles.expected_style(d, "calc", sheet_size=8), attributes=("colour",))


def test_attribute_rules_cover_the_design_rules() -> None:
    assert {"S-FONT", "S-SIZE", "S-FILL", "S-FONTCOLOR", "S-BOLD", "S-NUMFMT"} <= set(styles.ATTRIBUTE_RULES.values())
    assert set(styles.FOREIGN_ATTRIBUTES) <= set(styles.ALL_ATTRIBUTES)
    assert set(styles.THEME_ATTRIBUTES) == set(styles.FOREIGN_ATTRIBUTES) | {"italic"}


# ----------------------------------------------------------------------------- openpyxl: write and read back

FORMAT_FOR = {"input": "amount", "calc": "amount", "link": "rate", "import": "count", "export": "amount",
              "raw": "date", "total": "amount", "output": "rate", "col_header": "month"}


def _value_for(t: str):
    if t in ("raw", "col_header"):
        return dt.datetime(2026, 1, 31)
    if t in ("check", "master_check"):
        return 0
    if t in ("input", "calc", "link", "import", "export", "total", "output"):
        return 1234.5
    return "Text of a " + t


def test_apply_then_read_back_matches_for_every_type_and_size(tmp_path: Path, d: dictionary.Dictionary) -> None:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "s"
    styles.set_default_font(wb, d)
    placed = {}
    for i, t in enumerate(d.cell_types(), start=1):
        for j, size in enumerate((8, 9), start=1):
            cell = ws.cell(i, j, _value_for(t))
            styles.apply(cell, d, t, size=size, format_key=FORMAT_FOR.get(t))
            placed[cell.coordinate] = (t, size)
    path = tmp_path / "styled.xlsx"
    wb.save(path)
    info = workbook.open_workbook(path)
    sheet = info.sheet("s")
    for coord, (t, size) in placed.items():
        exp = styles.expected_style(d, t, sheet_size=size, format_key=FORMAT_FOR.get(t))
        assert styles.compare_cell(sheet.cell(coord), exp, attributes=styles.ALL_ATTRIBUTES) == [], (coord, t, size)
        assert sheet.cell(coord).style_name == styles.named_style_name(t)
        assert sheet.cell(coord).locked is (t != "input")


def test_apply_sets_locking_from_the_dictionary(tmp_path: Path, d: dictionary.Dictionary) -> None:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    styles.apply(ws["A1"], d, "input", size=9)
    styles.apply(ws["A2"], d, "calc", size=9)
    assert ws["A1"].protection.locked is False and ws["A2"].protection.locked is True


def test_register_named_styles_once(d: dictionary.Dictionary) -> None:
    import openpyxl
    wb = openpyxl.Workbook()
    names = styles.register_named_styles(wb, d)
    assert names["input"] == "rotm input" and set(names) == set(d.cell_types())
    again = styles.register_named_styles(wb, d)
    assert again == names
    assert len([n for n in wb.named_styles if n.startswith("rotm ")]) == len(d.cell_types())


def test_set_default_font_is_written_to_the_normal_style(tmp_path: Path, d: dictionary.Dictionary) -> None:
    import openpyxl
    wb = openpyxl.Workbook()
    styles.set_default_font(wb, d)
    wb.active["A1"] = 1
    path = tmp_path / "normal.xlsx"
    wb.save(path)
    back = openpyxl.load_workbook(path)
    assert back._named_styles["Normal"].font.name == "Arial" and back._named_styles["Normal"].font.sz == 9
    assert back.active["A1"].font.name == "Arial"
    assert b"Calibri" not in zipfile.ZipFile(path).read("xl/styles.xml")


def test_apply_attributes_keeps_what_it_does_not_set(tmp_path: Path, d: dictionary.Dictionary) -> None:
    import openpyxl
    from openpyxl.styles import Font
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = 5
    ws["A1"].font = Font(name="Calibri", sz=11, u="single", scheme="minor", color="FFFF0000")
    ws["A1"].number_format = "0.000"
    exp = styles.expected_style(d, "calc", sheet_size=8)
    styles.apply_attributes(ws["A1"], exp, attributes=styles.THEME_ATTRIBUTES)
    f = ws["A1"].font
    assert (f.name, f.sz, f.u, f.scheme) == ("Arial", 8, "single", None)   # the theme scheme is dropped with the name
    assert f.color.rgb == "FF000000"
    assert ws["A1"].number_format == "0.000"                               # not part of the theme attributes


# ----------------------------------------------------------------------------- the dictionary file and its reference

def test_every_source_names_a_key_of_sources() -> None:
    from lib import common
    data = common.read_json(dictionary.default_path())
    assert dictionary.source_problems(data) == []
    for name, ov in data["named_overrides"].items():
        assert ov["source"] and ov["meaning"], name


def test_f12_cell_types_and_both_input_conventions_are_defined(d: dictionary.Dictionary) -> None:
    for t in ("input", "calc", "link", "import", "check", "note", "col_header", "row_header", "raw", "title",
              "total", "output", "label", "unit"):
        assert t in d.cell_types(), t
    assert {"input_blue_font", "input_blue_on_fill", "links_green", "fast_import_export", "unit_unfilled",
            "total_grey_rule"} <= set(d.named_overrides())


def test_reference_tables_match_the_dictionary() -> None:
    from lib import common
    text = styles.reference_path().read_text(encoding="utf-8")
    start, end = text.index(styles.GENERATED_START), text.index(styles.GENERATED_END) + len(styles.GENERATED_END)
    expected = styles.dictionary_markdown(common.read_json(dictionary.default_path())).rstrip("\n")
    assert text[start:end] == expected, ("references/style-dictionary.md is out of date; run from the skill folder: "
                                         "python -P -X utf8 -B -c \"import sys; sys.path.insert(0, 'scripts'); "
                                         "from lib import styles; styles.write_reference()\"")
    for t in dictionary.load_dictionary().cell_types():
        assert f"#### {t}\n" in text, t


def test_write_reference_replaces_only_the_generated_part(tmp_path: Path) -> None:
    page = tmp_path / "page.md"
    page.write_text(f"# Head\n\nprose\n\n{styles.GENERATED_START}\nSTALE-TABLES-XYZ\n{styles.GENERATED_END}\n\ntail\n",
                    encoding="utf-8")
    styles.write_reference(page)
    text = page.read_text(encoding="utf-8")
    assert text.startswith("# Head\n\nprose\n\n") and text.endswith(f"{styles.GENERATED_END}\n\ntail\n")
    assert "STALE-TABLES-XYZ" not in text and "#### input" in text


def test_style_spec_round_trip_through_normalize_keeps_colours(d: dictionary.Dictionary) -> None:
    spec = styles.style_spec(d, "total", size=8)
    norm = excel.normalize_operations([{"op": "style", "sheet": "s", "ranges": "D7:J7", **spec}])[0]
    assert norm["border"]["top"]["color"] == "000000" and norm["border"]["bottom"]["line"] == -4142


# ----------------------------------------------------------------------------- what the audit compares (v2)

AUDITED = ("font name", "size", "font colour", "bold", "fill", "number format")


def _section(text: str, heading: str) -> str:
    return text.split(heading, 1)[1].split("\n## ", 1)[0]


def test_section_5_states_exactly_what_the_audit_compares() -> None:
    """A-aera-18, C-sol-12: the audit's style rules are the six attributes section 5 names; borders and alignment
    are written by build (and by theme with --borders and --align) and are not audited."""
    from lib import audit
    sec = _section(styles.reference_path().read_text(encoding="utf-8"), "## 5. How the commands use it")
    for words in AUDITED:
        assert words in sec, f"section 5 must name {words!r}"
    assert "borders and alignment" in sec and "not audited" in sec
    assert "--borders" in sec and "--align" in sec
    style_rules = {r.id for r in audit.RULES.values() if r.id.startswith("S-") and r.needs in ("styles", "manifest")}
    assert style_rules == {"S-FONT", "S-SIZE", "S-FILL", "S-FONTCOLOR", "S-BOLD", "S-NUMFMT"}


def test_the_audit_does_not_report_a_changed_border_or_alignment(tmp_path: Path) -> None:
    """What section 5 states, seen on a copy of the shipped starter model: a border and an alignment changed by
    hand give no style finding, a changed fill does."""
    import shutil
    from openpyxl import load_workbook
    from openpyxl.styles import Alignment, Border, PatternFill, Side
    from lib import audit
    src = styles.reference_path().parents[1] / "templates" / "starter"
    root = shutil.copytree(src, tmp_path / "starter", ignore=shutil.ignore_patterns("reports"))
    wb = load_workbook(root / "model.xlsx")
    cell = wb["raw_lines"]["A1"]
    cell.border = Border(top=Side(style="double", color="FFFF0000"))
    cell.alignment = Alignment(horizontal="right", vertical="top", text_rotation=90)
    wb["raw_lines"]["B1"].fill = PatternFill("solid", fgColor="FFFF0000")
    wb.save(root / "model.xlsx")
    rep = audit.audit_workbook(root / "model.xlsx", record=False)
    style = [(f.rule, c) for f in rep.findings if f.rule.startswith("S-") for c in (f.cells or [f.cell])]
    assert ("S-FILL", "B1") in style
    assert not [r for r, c in style if c == "A1"], style

"""Tests of lib.dictionary: loading, Max's fixed points through the API, named overrides, the per-project
override file, merging, the palette (design section 10; F12). Owner: CORE-IO."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from lib import common, dictionary

DENSE = ("raw", "helpers", "calc")
SPACED = ("cover", "inputs", "output", "checks", "notes")


@pytest.fixture
def d() -> dictionary.Dictionary:
    return dictionary.load_dictionary()


def write_override(path: Path, body: dict) -> Path:
    common.write_json(path, {"rotm_dictionary_override": 1, **body})
    return path


# ----------------------------------------------------------------------------- Max's fixed points (spec 1.2)

def test_font_and_sizes_are_maxs(d: dictionary.Dictionary) -> None:
    assert d.font_name == "Arial"
    assert d.sizes == {"dense": 8, "spaced": 9}
    for st in DENSE:
        assert d.sheet_size(st) == 8, st
    for st in SPACED:
        assert d.sheet_size(st) == 9, st


def test_sheet_size_override_is_8_or_9_only(d: dictionary.Dictionary) -> None:
    assert d.sheet_size("calc", 9) == 9 and d.sheet_size("cover", 8) == 8
    with pytest.raises(common.Refusal, match="font size 10 is not allowed"):
        d.sheet_size("calc", 10)
    with pytest.raises(common.Refusal, match="did you mean 'calc'"):
        d.sheet_size("calcs")


def test_headers_are_grey_and_row_headers_lighter(d: dictionary.Dictionary) -> None:
    col, row = d.style("col_header"), d.style("row_header")
    assert col.fill == "D9D9D9" and row.fill == "F2F2F2"
    for hexv in (col.fill, row.fill):
        assert hexv[0:2] == hexv[2:4] == hexv[4:6]
    assert int(row.fill[:2], 16) > int(col.fill[:2], 16)
    assert col.bold and col.border == {"bottom": {"style": "thin", "color": "808080"}}


def test_title_is_size_9_on_a_dense_sheet(d: dictionary.Dictionary) -> None:
    assert d.size_for("title", 8) == 9
    assert d.size_for("calc", 8) == 8 and d.size_for("calc", 9) == 9


def test_input_default_and_check_format(d: dictionary.Dictionary) -> None:
    inp = d.style("input")
    assert inp.fill == "FFF2CC" and inp.font_color == "000000" and not inp.locked
    assert inp.number_format == "item" and inp.number_format_key is None
    chk = d.style("check")
    assert chk.number_format == '[Red]"FAIL";[Red]"FAIL";"OK"' and chk.number_format_key == "check"
    assert chk.fill is None
    assert inp.sources["fill"].startswith("GUIDES")


def test_every_f12_cell_type_resolves(d: dictionary.Dictionary) -> None:
    for name in ("input", "calc", "link", "import", "check", "note", "col_header", "row_header", "raw", "title",
                 "total", "output", "label", "unit", "master_check", "heading"):
        st = d.style(name)
        assert st.type == name and st.size in ("sheet", "spaced", "dense")
        assert set(st.sources) >= {"font_color", "fill", "bold"}


def test_unknown_names_are_refused_with_a_suggestion(d: dictionary.Dictionary) -> None:
    with pytest.raises(common.Refusal, match="did you mean 'input'"):
        d.style("inputs")
    with pytest.raises(common.Refusal, match="did you mean 'amount'"):
        d.number_format("amont")
    with pytest.raises(common.Refusal, match="did you mean 'text'"):
        d.color("txt")


def test_minimal_colour_palette(d: dictionary.Dictionary) -> None:
    pal = d.palette()
    assert {"000000", "D9D9D9", "F2F2F2", "FFF2CC", "595959", "808080", "FF0000"} == pal
    assert "0000FF" not in pal


def test_rules_and_formats(d: dictionary.Dictionary) -> None:
    assert d.rule("depth_limit") == 2 and d.rule("length_limit") == 100
    assert d.rule("allowed_literals") == [0, 1] and d.rule("check_tolerance") == 0.001
    assert d.rule("missing", "x") == "x"
    assert d.number_format("amount") == '#,##0_);(#,##0);"-"_)'
    assert d.format_for_column_type("number") == "amount" and d.format_for_column_type("date") == "date"
    assert "amount" in d.number_format_keys()
    view = d.sheet_view("raw")
    assert view == {"size": 8, "gridlines": False, "zoom": 100, "freeze": "A2"}
    assert d.source("cell_types.input.fill").startswith("GUIDES") and d.source("nope.x") is None


# ----------------------------------------------------------------------------- overrides

def test_named_override_by_use(d: dictionary.Dictionary) -> None:
    blue = dictionary.load_dictionary(use=["input_blue_font"])
    st = blue.style("input")
    assert st.font_color == "0000FF" and st.fill is None
    assert blue.applied == ["input_blue_font"] and blue.sha256 != d.sha256
    assert "0000FF" in blue.palette() and "FFF2CC" not in blue.palette()
    both = dictionary.load_dictionary(use=["input_blue_on_fill"])
    assert both.style("input").fill == "FFF2CC" and both.style("input").font_color == "0000FF"
    with pytest.raises(common.Refusal, match="did you mean 'input_blue_font'"):
        dictionary.load_dictionary(use=["input_blue_fnt"])


def test_project_override_file(tmp_path: Path, d: dictionary.Dictionary) -> None:
    path = write_override(tmp_path / "style_overrides.json", {
        "use": ["links_green"],
        "set": {"colors": {"brand_fill": {"value": "EDEDED", "source": "project: Max's brand grey"}},
                "cell_types": {"total": {"fill": {"value": "brand_fill", "source": "project"},
                                         "border": {"value": {"bottom": {"style": "double", "color": "text"}},
                                                    "source": "project"}}},
                "rules": {"depth_limit": {"value": 3, "source": "project: legacy model"}}}})
    od = dictionary.load_dictionary(path)
    assert od.applied == ["links_green", "style_overrides.json"]
    assert od.style("import").font_color == "008000"
    total = od.style("total")
    assert total.fill == "EDEDED"
    assert total.border == {"bottom": {"style": "double", "color": "000000"}}   # replaced whole, not merged
    assert od.rule("depth_limit") == 3
    assert d.style("total").border == {"top": {"style": "thin", "color": "000000"}}  # the plugin file is unchanged


def test_override_leaf_without_source_is_refused(tmp_path: Path) -> None:
    path = write_override(tmp_path / "o.json", {"set": {"cell_types": {"input": {"bold": {"value": True}}}}})
    with pytest.raises(common.Refusal) as e:
        dictionary.load_dictionary(path)
    assert "o.json is not valid" in e.value.message
    assert any(x.startswith("set.cell_types.input.bold.source: is required") for x in e.value.details)


def test_override_naming_a_missing_colour_is_refused_after_merge(tmp_path: Path) -> None:
    path = write_override(tmp_path / "o.json", {"set": {"cell_types": {"input": {
        "fill": {"value": "nope_fill", "source": "project"}}}}})
    with pytest.raises(common.Refusal) as e:
        dictionary.load_dictionary(path)
    assert "after applying o.json" in e.value.message
    assert any("colour key 'nope_fill' is not in 'colors'" in x for x in e.value.details)


def test_override_with_unknown_named_override(tmp_path: Path) -> None:
    path = write_override(tmp_path / "o.json", {"use": ["fast_import"]})
    with pytest.raises(common.Refusal, match="did you mean 'fast_import_export'"):
        dictionary.load_dictionary(path)


def test_a_new_cell_type_needs_every_attribute(tmp_path: Path) -> None:
    path = write_override(tmp_path / "o.json", {"set": {"cell_types": {"kpi": {
        "bold": {"value": True, "source": "project"}}}}})
    with pytest.raises(common.Refusal) as e:
        dictionary.load_dictionary(path)
    assert any(x.startswith("cell_types.kpi.meaning: is required") for x in e.value.details)


def test_merge_does_not_change_its_arguments() -> None:
    base = {"a": {"b": 1, "leaf": {"value": {"x": 1}, "source": "S"}}, "c": [1]}
    over = {"a": {"leaf": {"value": {"y": 2}, "source": "T"}, "d": 3}, "c": [2]}
    keep_base, keep_over = copy.deepcopy(base), copy.deepcopy(over)
    out = dictionary.merge(base, over)
    assert out == {"a": {"b": 1, "leaf": {"value": {"y": 2}, "source": "T"}, "d": 3}, "c": [2]}
    assert base == keep_base and over == keep_over


def test_find_override_and_load_for_model(tmp_path: Path) -> None:
    assert dictionary.find_override(tmp_path) is None
    assert dictionary.load_for_model(tmp_path).applied == []
    write_override(tmp_path / "style_overrides.json", {"use": ["input_blue_font"]})
    assert dictionary.find_override(tmp_path) == tmp_path / "style_overrides.json"
    assert dictionary.load_for_model(tmp_path).applied == ["input_blue_font"]
    write_override(tmp_path / "alt.json", {"use": ["links_green"]})
    assert dictionary.load_for_model(tmp_path, spec_override="alt.json").applied == ["links_green"]
    with pytest.raises(common.Refusal, match="not found"):
        dictionary.load_for_model(tmp_path, spec_override="missing.json")


def test_base_dictionary_errors_are_refusals(tmp_path: Path) -> None:
    with pytest.raises(common.Refusal, match="not found"):
        dictionary.load_dictionary(base=tmp_path / "none.json")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"rotm_dictionary": 1}), encoding="utf-8")
    with pytest.raises(common.Refusal, match="bad.json is not valid"):
        dictionary.load_dictionary(base=bad)


def test_source_problems_finds_leaves_that_name_no_source() -> None:
    data = {"sources": {"MAX": "Max"}, "font": {"value": "Arial", "source": "MAX"},
            "colors": {"x": {"value": "000000", "source": "Excel default"}}}
    assert dictionary.source_problems(data) == ["colors.x: Excel default"]


# ----------------------------------------------------------------------------- fast_import_export (v2)

def test_fast_import_export_says_what_it_colours() -> None:
    """A-aera-18, C-sol-12: build gives no cell the export type, so in a built model the override colours the
    importing cells only; the export type is given only where the workbook has no manifest."""
    data = common.read_json(dictionary.default_path())
    meaning = data["named_overrides"]["fast_import_export"]["meaning"]
    assert "importing cells" in meaning and "build" in meaning and "no manifest" in meaning
    fast = dictionary.load_dictionary(use=["fast_import_export"])
    assert fast.style("import").font_color == "0000FF" and fast.style("export").font_color == "FF0000"


def test_build_gives_no_cell_the_export_type_and_classify_does_without_a_manifest(tmp_path: Path) -> None:
    from openpyxl import Workbook
    from lib import classify, workbook
    skill = dictionary.default_path().parents[1]
    manifests = list(skill.glob("examples/*/model.manifest.json")) + list(skill.glob("templates/*/model.manifest.json"))
    manifests += list(skill.parents[1].glob("templates/*/model.manifest.json"))
    assert manifests
    seen: set[str] = set()
    for path in manifests:
        by_sheet = json.loads(path.read_text(encoding="utf-8")).get("cell_types", {})
        types = {ctype for ranges in by_sheet.values() for _rng, ctype in ranges}
        assert "export" not in types, path
        seen |= types
    assert "import" in seen, "the shipped models hold importing cells"
    wb = Workbook()
    calc = wb.active
    calc.title = "calc"
    calc["A1"], calc["B1"] = 5, "=A1*2"
    out = wb.create_sheet("summary")
    out["A1"], out["B1"] = "Result", "=calc!B1"
    out["A2"], out["B2"] = "Plus one", "=calc!B1+1"
    wb.save(tmp_path / "w.xlsx")
    types = classify.classify_workbook(workbook.open_workbook(tmp_path / "w.xlsx"))
    assert types[("calc", "B1")] == "export"
    assert types[("summary", "B1")] == "import"

"""Tests of lib.spec: loading, settings precedence, contracts, and every layout-free cross reference
(design section 6; F04 "a bad specification is refused with a message that says what to change").
Owner: CORE-IO."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Callable

import pytest

from lib import common, dictionary, modelfolder, spec as specmod


def contract(table: str = "sales") -> dict:
    return {"rotm_contract": 1, "table": table, "raw_sheet": f"raw_{table}", "excel_table": f"tbl_raw_{table}",
            "grain": "one row per invoice line", "key": ["invoice_id", "line_no"],
            "columns": [{"name": "invoice_id", "type": "text", "null_rule": "never"},
                        {"name": "line_no", "type": "integer", "null_rule": "never"},
                        {"name": "inv_date", "type": "date", "null_rule": "never"},
                        {"name": "region", "type": "text", "null_rule": "never"},
                        {"name": "amount", "type": "number", "unit": "KRW", "null_rule": "never"}],
            "expect": {"rows": {"min": 1, "max": 100000}, "totals": [{"column": "amount", "value": 100.0}]},
            "agreement": {"status": "proposed"}}


def agree(root: Path, c: dict, channel: str = "session") -> None:
    c = copy.deepcopy(c)
    c["agreement"] = {"status": "agreed", "contract_sha256": modelfolder.contract_hash(c), "reply": "ok, agreed",
                      "by": "Max", "at": "2026-11-25T01:20:00+09:00", "channel": channel, "source_file": None,
                      "recorded_by": "rotm.py contract agree"}
    common.write_json(modelfolder.contract_path(root, c["table"]), c)


def base_spec() -> dict:
    return {"rotm_spec": 1, "name": "sales_vs_target", "title": "Sales versus target", "product": "both",
            "sheets": [
                {"name": "notes", "type": "notes", "notes": [{"text": "Targets are monthly.", "source": "task.md"}]},
                {"name": "summary", "type": "output", "blocks": [
                    {"id": "ytd", "title": "Year to date", "rows": {"same_as": "actual"}, "measures": [
                        {"id": "act", "label": "Actual", "format": "amount", "expr": "SUM({grow:actual})", "total": "sum"},
                        {"id": "tgt", "label": "Target", "format": "amount", "expr": "SUM({grow:target})", "total": "sum"},
                        {"id": "pct", "label": "Variance %", "format": "rate", "expr": "IFERROR({m:act}/{m:tgt}-1,0)",
                         "total": "same", "key": True}]}]},
                {"name": "inputs", "type": "inputs", "items": [
                    {"id": "start_month", "label": "First month", "value": "2026-01-31", "type": "date", "format": "date"},
                    {"id": "scale", "label": "Display scale", "value": 1000, "format": "integer", "unit": "KRW per unit"},
                    {"id": "regions", "kind": "list", "label": "Regions", "values": ["East", "North", "South", "West"]},
                    {"id": "budget", "kind": "grid", "label": "Budget", "unit": "KRW", "format": "amount",
                     "rows": {"values": ["East", "North"]}, "cols": {"values": ["2026-01-31", "2026-02-28"]},
                     "values": [[100, 110], [90, 95]]}]},
                {"name": "raw_sales", "type": "raw", "table": "sales"},
                {"name": "h_sales", "type": "helpers", "table": "sales", "columns": [
                    {"id": "month_end", "label": "Month end", "expr": "EOMONTH({this:inv_date},0)", "format": "date"},
                    {"id": "amount_k", "label": "Amount", "expr": "{this:amount}/{in:scale}", "format": "amount1"}]},
                {"name": "calc", "type": "calc", "blocks": [
                    {"id": "actual", "title": "Actual sales", "unit": "KRW thousand", "format": "amount",
                     "rows": {"in": "regions"},
                     "cols": {"timeline": {"start": "start_month", "periods": 6, "step": "month"}},
                     "cell": "SUMIFS({h:sales.amount_k},{h:sales.region},{row},{h:sales.month_end},{col})",
                     "total": {"row": True, "col": True}},
                    {"id": "target", "title": "Target", "rows": {"same_as": "actual"}, "cols": {"same_as": "actual"},
                     "cell": "{g:actual}*1", "total": {"row": True}},
                    {"id": "bridge", "title": "Variance", "cols": {"same_as": "actual"}, "items": [
                        {"id": "act", "label": "Actual", "expr": "{b:actual.total}"},
                        {"id": "tgt", "label": "Target", "expr": "{b:target.total}"},
                        {"id": "var", "label": "Variance", "expr": "{r:act}-{r:tgt}", "key": True},
                        {"id": "cum", "label": "Cumulative", "expr": "{prev}+{r:var}", "first": "{r:var}"}]}]},
                {"name": "checks", "type": "checks", "checks": [
                    {"id": "actual_ties_raw", "kind": "equal", "label": "Actual ties raw",
                     "actual": "{blk:actual.total}", "expected": "SUM({raw:sales.amount})/{in:scale}",
                     "tolerance": "check_tolerance"},
                    {"id": "neg_months", "kind": "zero", "severity": "alert", "label": "Negative months",
                     "actual": "COUNTIF({blk:target},\"<0\")"},
                    {"id": "pct_range", "kind": "range", "label": "Variance within range", "actual": "{cell:pct}",
                     "min": "-1", "max": 1}]}],
            "settings": {"length_limit": 120}}


@pytest.fixture
def model(outside_tmp: Path) -> Path:
    # v5: outside the plugin wherever --basetemp points (conftest.py), so the 'example' channel is refused here
    root = outside_tmp / "sales"
    modelfolder.init_model(root, name="sales_vs_target")
    agree(root, contract())
    return root


def write_spec(root: Path, data: dict) -> Path:
    path = root / "model.spec.json"
    common.write_json(path, data)
    return path


def refusal(root: Path, change: Callable[[dict], Any], **kw: Any) -> list[str]:
    data = base_spec()
    change(data)
    write_spec(root, data)
    with pytest.raises(common.Refusal) as e:
        specmod.load_spec(root / "model.spec.json", **kw)
    assert e.value.exit_code == 2
    return e.value.details


def find(details: list[str], text: str) -> str:
    hits = [d for d in details if text in d]
    assert hits, f"{text!r} not in {details}"
    return hits[0]


# ----------------------------------------------------------------------------- loading

def test_load_valid_spec(model: Path) -> None:
    write_spec(model, base_spec())
    s = specmod.load_spec(model)                      # the folder works as well as the file
    assert s.path == model / "model.spec.json" and s.root == model
    assert s.name == "sales_vs_target" and s.output == model / "model.xlsx" and s.product == "both"
    assert [sh.type for sh in s.sheets] == ["cover", "inputs", "raw", "helpers", "calc", "output", "checks", "notes"]
    assert [sh.name for sh in s.sheets][:2] == ["cover", "inputs"]
    assert s.sheet("cover").generated and not s.sheet("inputs").generated
    sizes = {sh.name: sh.font_size for sh in s.sheets}
    assert sizes == {"cover": 9, "inputs": 9, "raw_sales": 8, "h_sales": 8, "calc": 8, "summary": 9, "checks": 9,
                     "notes": 9}
    assert s.settings["length_limit"] == 120 and s.settings["depth_limit"] == 2
    assert s.contracts["sales"]["table"] == "sales" and s.contract_states == {"sales": "agreed"}
    assert s.blocks["bridge"]["shape"] == "items" and s.inputs["regions"]["kind"] == "list"
    assert "check_tolerance" in s.inputs and s.inputs["check_tolerance"]["generated"]
    assert s.helper_columns["sales"][:4] == ["row", "in_raw", "dup_key", "dup"]
    assert "amount_k" in s.helper_columns["sales"] and "inv_date" in s.helper_columns["sales"]
    assert s.block_checks == {"actual": ["actual_ties_raw"], "target": ["neg_months"], "ytd": ["pct_range"]}
    assert s.key_outputs["pct"]["block"] == "ytd" and s.key_outputs["bridge.var"]["id"] == "var"
    assert len(s.sha256) == 64 and s.dictionary.font_name == "Arial"
    assert len(s.expressions()) >= 10


def test_generated_checks_sheet_when_the_spec_has_none(model: Path) -> None:
    data = base_spec()
    data["sheets"] = [sh for sh in data["sheets"] if sh["type"] not in ("checks",)]
    write_spec(model, data)
    s = specmod.load_spec(model)
    assert s.sheet("checks").generated and s.sheet("checks").font_size == 9


def test_font_size_on_a_sheet_wins(model: Path) -> None:
    data = base_spec()
    data["sheets"][5]["font_size"] = 9
    write_spec(model, data)
    assert specmod.load_spec(model).sheet("calc").font_size == 9


def test_schema_errors_come_first_with_paths(model: Path) -> None:
    data = base_spec()
    data["sheets"][5]["font_size"] = 10
    write_spec(model, data)
    with pytest.raises(common.Refusal) as e:
        specmod.load_spec(model)
    assert "model.spec.json is not valid" in e.value.message
    assert find(e.value.details, "sheets[5].font_size: 10 is not allowed")


def test_bad_json_names_the_line(model: Path) -> None:
    (model / "model.spec.json").write_text('{"rotm_spec": 1,\n "name": }', encoding="utf-8")
    with pytest.raises(common.Refusal, match="line 2 column"):
        specmod.load_spec(model)
    with pytest.raises(common.Refusal, match="not found"):
        specmod.load_spec(model / "other.spec.json")


def test_project_override_is_used(model: Path) -> None:
    common.write_json(model / "style_overrides.json", {"rotm_dictionary_override": 1, "use": ["input_blue_font"],
                                                      "set": {"rules": {"depth_limit": {"value": 3, "source": "project"}}}})
    write_spec(model, base_spec())
    s = specmod.load_spec(model)
    assert s.dictionary.applied == ["input_blue_font", "style_overrides.json"]
    assert s.settings["depth_limit"] == 3


# ----------------------------------------------------------------------------- settings

def test_settings_precedence() -> None:
    d = dictionary.load_dictionary()
    assert specmod.resolve_settings(None, d) == {**specmod.DEFAULT_SETTINGS, "allow_functions": []}
    assert specmod.resolve_settings({"depth_limit": 3}, d)["depth_limit"] == 3
    out = specmod.resolve_settings({"depth_limit": 3, "allow_functions": ["vlookup"]}, d,
                                   {"depth_limit": 4, "length_limit": None})
    assert out["depth_limit"] == 4 and out["length_limit"] == 100 and out["allow_functions"] == ["VLOOKUP"]
    with pytest.raises(common.Refusal, match="did you mean 'depth_limit'"):
        specmod.resolve_settings(None, d, {"depth": 3})
    with pytest.raises(common.Refusal) as e:
        specmod.resolve_settings(None, d, {"depth_limit": 0})
    assert find(e.value.details, "settings.depth_limit: 0 is out of range")


def test_default_settings_equal_the_dictionary_rules() -> None:
    d = dictionary.load_dictionary()
    for key, value in specmod.DEFAULT_SETTINGS.items():
        if key != "allow_functions":
            assert d.rule(key) == value, key


# ----------------------------------------------------------------------------- contracts

def test_missing_contract(model: Path) -> None:
    modelfolder.contract_path(model, "sales").unlink()
    d = refusal(model, lambda s: None)
    assert "contract propose" in find(d, "sheets[3].table: raw table 'sales' has no contract")


def test_proposed_and_stale_contracts_are_not_agreed(model: Path) -> None:
    common.write_json(modelfolder.contract_path(model, "sales"), contract())
    d = refusal(model, lambda s: None)
    assert "contract agree" in find(d, "is proposed, not agreed")
    agree(model, contract())
    c = common.read_json(modelfolder.contract_path(model, "sales"))
    c["grain"] = "changed"
    common.write_json(modelfolder.contract_path(model, "sales"), c)
    d = refusal(model, lambda s: None)
    assert find(d, "was changed after Max agreed it")


def test_require_agreed_false_loads_a_proposed_contract(model: Path) -> None:
    common.write_json(modelfolder.contract_path(model, "sales"), contract())
    write_spec(model, base_spec())
    s = specmod.load_spec(model, require_agreed=False)
    assert s.contract_states["sales"] == "proposed" and "sales" in s.contracts
    modelfolder.contract_path(model, "sales").unlink()
    s = specmod.load_spec(model, require_agreed=False)
    assert s.contract_states["sales"] == "missing" and s.notes


def test_example_agreement_counts_only_inside_the_plugin(model: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agree(model, contract(), channel="example")
    monkeypatch.delenv(specmod.EXAMPLE_ROOTS_ENV, raising=False)
    d = refusal(model, lambda s: None)
    assert find(d, "carries the 'example' agreement")
    monkeypatch.setenv(specmod.EXAMPLE_ROOTS_ENV, str(model.parent))
    write_spec(model, base_spec())
    assert specmod.load_spec(model).contract_states["sales"] == "agreed"
    assert specmod.example_channel_allowed(common.skill_dir() / "examples" / "x")


# ----------------------------------------------------------------------------- cross references

def test_unknown_block_with_suggestion(model: Path) -> None:
    def change(s: dict) -> None:
        s["sheets"][5]["blocks"][1]["cell"] = "{g:actuals}*1"
    line = find(refusal(model, change), "sheets[5].blocks[1].cell: unknown block 'actuals' in {g:actuals}")
    assert "did you mean 'actual'?" in line and " Fix: " in line


@pytest.mark.parametrize("path,expr,expected", [
    (("sheets", 5, "blocks", 0, "cell"), "{this:amount}", "{this:amount} cannot be used in a grid block"),
    (("sheets", 4, "columns", 0, "expr"), "{h:sales.inv_date}", "{h:sales.inv_date} cannot be used in a helper column"),
    (("sheets", 6, "checks", 0, "expected"), "SUM({raw:sales.amout})", "'amout' is not a column of raw table 'sales'"),
    (("sheets", 6, "checks", 0, "expected"), "SUM({raw:orders.amount})", "unknown raw table 'orders'"),
    (("sheets", 4, "columns", 1, "expr"), "{this:amount}/{in:scal}", "unknown input 'scal' in {in:scal}; did you mean 'scale'?"),
    (("sheets", 4, "columns", 1, "expr"), "{this:amount}/{in:scale.rows}", "input 'scale' is a scalar; .rows needs a grid input"),
    (("sheets", 5, "blocks", 2, "items", 1, "expr"), "{b:actual.totl}", "{b:<block>.<item>} reads a line item"),
    (("sheets", 5, "blocks", 2, "items", 1, "expr"), "{b:ytd.total}", "block 'ytd' has no total row"),
    (("sheets", 5, "blocks", 2, "items", 1, "expr"), "{b:target.total}+{r:tgt}", "line item 'tgt' refers to itself"),
    (("sheets", 1, "blocks", 0, "measures", 0, "expr"), "SUM({grow:bridge})", "block 'bridge' is a items block"),
    (("sheets", 1, "blocks", 0, "measures", 2, "expr"), "{m:pct}", "measure 'pct' refers to itself"),
    (("sheets", 1, "blocks", 0, "measures", 2, "expr"), "{m:ac}", "unknown measure 'ac'"),
    (("sheets", 6, "checks", 2, "actual"), "{cell:pc}", "unknown key output 'pc'"),
    (("sheets", 6, "checks", 2, "actual"), "{chk:pct_range}", "check 'pct_range' refers to itself"),
    (("sheets", 6, "checks", 2, "actual"), "{chk:sales_rows}+{chk:nope}", "unknown check 'nope'"),
    (("sheets", 6, "checks", 2, "actual"), "{xyz:1}", "unknown placeholder kind 'xyz'"),
    (("sheets", 6, "checks", 2, "actual"), "{Blk:actual}", "unknown placeholder kind 'Blk' in {Blk:actual}; did you mean 'blk'?"),
    (("sheets", 6, "checks", 2, "actual"), "{row}", "{row} cannot be used in a check"),
    (("sheets", 5, "blocks", 0, "cell"), "{row:x}", "{row} takes no argument"),
    (("sheets", 5, "blocks", 0, "cell"), "{blk:actual}", "{blk:actual} refers to its own block"),
    (("sheets", 6, "checks", 2, "actual"), "{blk:bridge.total}", "block 'bridge' has no total"),
    (("sheets", 6, "checks", 2, "actual"), "{raw:sales}", "{raw:sales} needs <table>.<column>"),
    (("sheets", 6, "checks", 2, "actual"), "SUM(A1}", "'}' is not a placeholder"),
    (("sheets", 4, "columns", 0, "expr"), "{this:month_end}", "helper column 'month_end' refers to itself"),
])
def test_cross_reference_refusals(model: Path, path: tuple, expr: str, expected: str) -> None:
    def change(s: dict) -> None:
        node = s
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = expr
    find(refusal(model, change), expected)


def test_grid_shapes_must_match(model: Path) -> None:
    def change(s: dict) -> None:
        s["sheets"][5]["blocks"][1]["rows"] = {"values": ["East", "North", "South", "West"]}
    line = find(refusal(model, change), "block 'actual' does not have the same rows as block 'target'")
    assert "\"same_as\": \"actual\"" in line


def test_prev_needs_first_and_first_cannot_use_prev(model: Path) -> None:
    def change(s: dict) -> None:
        item = s["sheets"][5]["blocks"][2]["items"][3]
        item.pop("first")
    find(refusal(model, change), "line item 'cum' uses {prev} but has no \"first\"")

    def change2(s: dict) -> None:
        s["sheets"][5]["blocks"][2]["items"][3]["first"] = "{prev}"
    find(refusal(model, change2), "\"first\" is the first column and cannot use {prev}")


def test_ids_must_be_unique_and_generated_ids_are_kept(model: Path) -> None:
    def change(s: dict) -> None:
        s["sheets"][6]["checks"][1]["id"] = "actual"
        s["sheets"][6]["checks"].append({"id": "sales_rows", "kind": "zero", "label": "x", "actual": "0"})
        s["sheets"][2]["items"].append({"id": "check_tolerance", "label": "t", "value": 1})
        s["sheets"][5]["blocks"][2]["items"][1]["id"] = "act"
    d = refusal(model, change)
    # v5 (J-1): both JSON paths and "rename one of them"
    find(d, "id 'actual' is used by a block at sheets[5].blocks[0].id and by a check at sheets[6].checks[1].id")
    find(d, "check id 'sales_rows' is the id of a check build adds for raw table 'sales'")
    find(d, "id 'check_tolerance' is used by the input build adds on the inputs sheet at settings.check_tolerance and "
            "by an input at sheets[2].items[4].id")
    find(d, "rename one of them: inputs, blocks and checks share one set of ids")
    find(d, "item id 'act' repeats item 0 of this block")


def test_sheet_level_problems(model: Path) -> None:
    def change(s: dict) -> None:
        s["sheets"].append({"name": "inputs2", "type": "inputs"})
        s["sheets"].append({"name": "raw_sales", "type": "raw", "table": "sales"})
        s["sheets"].append({"name": "h_orders", "type": "helpers", "table": "orders"})
        s["sheets"].append({"name": "raw_x_calc", "type": "calc", "blocks": [
            {"id": "z", "rows": {"values": [1]}, "cell": "1"}]})
    d = refusal(model, change)
    find(d, "a second inputs sheet ('inputs2')")
    find(d, "sheet name 'raw_sales' is already used by sheets[3]")
    find(d, "helper sheet 'h_orders' is for table 'orders', which has no raw sheet")
    find(d, "sheet names starting with 'raw_' are kept for raw sheets")


def test_generated_sheet_name_must_be_free(model: Path) -> None:
    def change(s: dict) -> None:
        s["sheets"] = [sh for sh in s["sheets"] if sh["type"] != "checks"]
        s["sheets"][5]["name"] = "checks"
    find(refusal(model, change), "sheet name 'checks' is needed for the checks sheet that build adds")


def test_dimensions_and_inputs(model: Path) -> None:
    def change(s: dict) -> None:
        s["sheets"][5]["blocks"][0]["rows"] = {"in": "scale"}
        s["sheets"][5]["blocks"][0]["cols"] = {"timeline": {"start": "regions", "periods": 6, "step": "month"}}
        s["sheets"][5]["blocks"][1]["cols"] = {"same_as": "bridge"}
        s["sheets"][1]["blocks"][0]["rows"] = {"same_as": "nope"}
    d = refusal(model, change)
    find(d, "sheets[5].blocks[0].rows.in: input 'scale' is a scalar, not a list")
    find(d, "sheets[5].blocks[0].cols.timeline.start: input 'regions' is not a scalar input")
    find(d, "sheets[1].blocks[0].rows.same_as: unknown block 'nope'")


def test_same_as_cycle(model: Path) -> None:
    def change(s: dict) -> None:
        s["sheets"][5]["blocks"][0]["rows"] = {"same_as": "target"}
    find(refusal(model, change), "the same_as chain loops back to this block")


def test_unknown_number_format(model: Path) -> None:
    def change(s: dict) -> None:
        s["sheets"][5]["blocks"][0]["format"] = "amont"
    line = find(refusal(model, change), "sheets[5].blocks[0].format: number format 'amont' is not in the style dictionary")
    assert "did you mean 'amount'?" in line


def test_unknown_fetch_column(model: Path) -> None:
    def change(s: dict) -> None:
        s["sheets"][4]["fetch"] = ["inv_date", "regon"]
    find(refusal(model, change), "sheets[4].fetch[1]: 'regon' is not a column of the contract of 'sales'; did you mean 'region'?")


def test_every_problem_is_reported_at_once(model: Path) -> None:
    def change(s: dict) -> None:
        s["sheets"][5]["blocks"][1]["cell"] = "{g:actuals}"
        s["sheets"][6]["checks"][0]["expected"] = "{raw:sales.amout}"
        s["sheets"][4]["columns"][1]["expr"] = "{in:scal}"
    d = refusal(model, change)
    assert len(d) >= 3 and all(" Fix: " in x for x in d)


# ----------------------------------------------------------------------------- placeholders

def test_placeholders_skip_text_and_array_constants() -> None:
    expr = 'IF({in:x}>0,"{row}",SUM({1,2;3,4}))+{TRUE}+{raw:sales.amount}'
    phs = specmod.placeholders(expr)
    assert [(p.kind, p.arg) for p in phs] == [("in", "x"), ("raw", "sales.amount")]
    assert expr[phs[0].start:phs[0].end] == "{in:x}" and phs[1].text == "{raw:sales.amount}"
    assert [(p.kind, p.arg) for p in specmod.placeholders("{prev}+{row}")] == [("prev", None), ("row", None)]
    assert specmod.string_spans('a"b""c"d') == [(1, 4), (4, 7)]


def test_allowed_in_covers_every_kind() -> None:
    assert set(specmod.ALLOWED_IN) == set(specmod.PLACEHOLDER_KINDS)

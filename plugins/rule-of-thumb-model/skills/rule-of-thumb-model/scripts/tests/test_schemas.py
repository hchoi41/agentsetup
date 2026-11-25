"""Tests of lib.schemas: the validator and the eight schemas (design sections 3.3, 4, 5, 6, 10).
Each schema has a passing case and failing cases; messages name the JSON path and say what to change.
Owner: CORE-IO."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from lib import common, schemas

SKILL = Path(__file__).resolve().parents[2]


def lines(obj: object, name: str) -> list[str]:
    return [e.line() for e in schemas.validate(obj, name)]


def contract(**over: object) -> dict:
    c = {"rotm_contract": 1, "table": "sales", "raw_sheet": "raw_sales", "excel_table": "tbl_raw_sales",
         "purpose": "Invoice lines", "source": {"connector": "power_query", "system": "BigQuery", "object": "x.y"},
         "grain": "one row per invoice line", "key": ["invoice_id", "line_no"],
         "columns": [
             {"name": "invoice_id", "type": "text", "unit": None, "null_rule": "never", "allowed": None,
              "source_expression": "CAST(i.invoice_id AS STRING)"},
             {"name": "line_no", "type": "integer", "unit": "count", "null_rule": "never"},
             {"name": "region", "type": "text", "null_rule": "never", "allowed": ["East", "West"]},
             {"name": "amount", "type": "number", "unit": "KRW", "null_rule": "allowed"}],
         "expect": {"rows": {"min": 1, "max": 100000}, "totals": [{"column": "amount", "value": 12345.5, "tolerance": 0}]},
         "refresh": {"frequency": "monthly", "capacity_rows": None, "allow_empty": False},
         "agreement": {"status": "proposed"}}
    c.update(over)
    return c


def spec(**over: object) -> dict:
    s = {"rotm_spec": 1, "name": "sales_vs_target", "title": "Sales versus target",
         "sheets": [
             {"name": "inputs", "type": "inputs", "items": [
                 {"id": "start_month", "label": "First month", "value": "2026-01-31", "type": "date", "format": "date"},
                 {"id": "regions", "kind": "list", "label": "Regions", "values": ["East", "West"]},
                 {"id": "budget", "kind": "grid", "label": "Budget", "rows": {"values": ["East", "West"]},
                  "cols": {"values": ["2026-01-31", "2026-02-28"], "format": "month"}, "values": [[1, 2], [3, 4]]}]},
             {"name": "raw_sales", "type": "raw", "table": "sales"},
             {"name": "h_sales", "type": "helpers", "table": "sales", "columns": [
                 {"id": "month_end", "expr": "EOMONTH({this:inv_date},0)", "format": "date"}]},
             {"name": "calc", "type": "calc", "blocks": [
                 {"id": "actual", "title": "Actual", "rows": {"in": "regions"},
                  "cols": {"timeline": {"start": "start_month", "periods": 6, "step": "month"}},
                  "cell": "SUMIFS({h:sales.amount},{h:sales.region},{row},{h:sales.month_end},{col})",
                  "total": {"row": True, "col": True}},
                 {"id": "bridge", "cols": {"same_as": "actual"}, "items": [
                     {"id": "act", "label": "Actual", "expr": "{b:actual.total}"},
                     {"id": "cum", "label": "Cumulative", "expr": "{prev}+{r:act}", "first": "{r:act}"}]},
                 {"id": "ytd", "rows": {"same_as": "actual"}, "measures": [
                     {"id": "act", "label": "Actual", "expr": "SUM({grow:actual})", "total": "sum", "key": True}]}]},
             {"name": "checks", "type": "checks", "checks": [
                 {"id": "ties", "kind": "equal", "label": "Actual ties raw", "actual": "{blk:actual.total}",
                  "expected": "SUM({raw:sales.amount})", "tolerance": "check_tolerance"},
                 {"id": "neg", "kind": "zero", "severity": "alert", "label": "Negative months",
                  "actual": "COUNTIF({blk:actual},\"<0\")"}]},
             {"name": "notes", "type": "notes", "notes": [{"text": "Targets are monthly.", "source": "task.md"}]}],
         "settings": {"depth_limit": 2, "length_limit": 100}}
    s.update(over)
    return s


def test_every_schema_is_registered() -> None:
    assert set(schemas.SCHEMAS) == set(schemas.SCHEMA_NAMES)
    with pytest.raises(ValueError):
        schemas.validate({}, "nope")


def test_nearest() -> None:
    assert schemas.nearest("cel", ["cell", "items"]) == "cell"
    assert schemas.nearest("zzz", ["cell"]) is None


# ----------------------------------------------------------------------------- marker

def test_marker_valid_and_invalid() -> None:
    ok = {"rotm_model": 1, "name": "sales_vs_target", "created": "2026-11-25T01:02:03+09:00",
          "created_by": "rotm.py init", "plugin_version": "0.1.0", "spec": "model.spec.json", "workbook": "model.xlsx",
          "product": "both"}
    assert lines(ok, "marker") == []
    bad = lines({"rotm_model": 2, "nam": "x", "workbook": "model.xls", "product": "web"}, "marker")
    assert "rotm_model: must be 1, got 2 Fix: write 1 (the marker format version)" in bad
    assert "name: is required in rotm_model.json Fix: add \"name\": a model name" in bad
    assert any(x.startswith("nam: unknown key 'nam'; did you mean 'name'?") for x in bad)
    assert any(x.startswith("workbook: 'model.xls' does not have the required form") for x in bad)
    assert any(x.startswith("product: \"web\" is not allowed") for x in bad)


def test_marker_must_be_an_object() -> None:
    assert lines([1], "marker")[0].startswith("(top level): must be the model marker")


# ----------------------------------------------------------------------------- contract

def test_contract_valid() -> None:
    assert lines(contract(), "contract") == []
    agreed = contract(agreement={"status": "agreed", "contract_sha256": "a" * 64, "reply": "ok, agreed",
                                 "by": "Max", "at": "2026-11-25T01:20:00+09:00", "channel": "session",
                                 "source_file": None, "recorded_by": "rotm.py contract agree"})
    assert lines(agreed, "contract") == []


@pytest.mark.parametrize("change,expected", [
    (lambda c: c["columns"][0].update(null_rule="allowed"), "columns[0].null_rule: the first column 'invoice_id' must never be null"),
    (lambda c: c.update(key=["invoice_id", "line"]), "key[1]: 'line' is not a column of this contract; did you mean 'line_no'?"),
    (lambda c: c["columns"].append({"name": "Region", "type": "text", "null_rule": "never"}), "columns[4].name: 'Region' repeats an earlier column name"),
    (lambda c: c.update(raw_sheet="sales_raw"), "raw_sheet: 'sales_raw' is not 'raw_sales'"),
    (lambda c: c["expect"]["totals"].append({"column": "region", "value": 1}), "expect.totals[1].column: 'region' has type text"),
    (lambda c: c.update(expect={}), "expect: needs at least one of 'rows', 'totals'"),
    (lambda c: c["expect"].update(rows={"min": 5, "max": 1}), "expect.rows: min 5 is greater than max 1"),
    (lambda c: c["expect"].update(rows={"min": 5}), "expect.rows: needs 'exact', or both 'min' and 'max'"),
    (lambda c: c["columns"][1].update(type="int"), "columns[1].type: \"int\" is not allowed"),
    (lambda c: c["columns"][1].update(name="line no"), "columns[1].name: 'line no' does not have the required form"),
    (lambda c: c.update(table="Sales"), "table: 'Sales' does not have the required form"),
    (lambda c: c.update(agreement={"status": "agreed", "by": "Max"}), "agreement.reply: is empty, but an agreed contract records it"),
])
def test_contract_refusals(change, expected: str) -> None:
    c = contract()
    change(c)
    got = lines(c, "contract")
    assert any(x.startswith(expected) for x in got), got
    assert all(" Fix: " in x for x in got)


def test_contract_task_file_needs_source_file() -> None:
    c = contract(agreement={"status": "agreed", "contract_sha256": "a" * 64, "reply": "agreed", "by": "task",
                            "at": "2026-11-25", "channel": "task-file"})
    assert any(x.startswith("agreement.source_file: is empty") for x in lines(c, "contract"))


def test_contract_column_names_may_be_unicode() -> None:
    c = contract()
    c["columns"].append({"name": "매출액", "type": "number", "null_rule": "allowed"})
    assert lines(c, "contract") == []


# ----------------------------------------------------------------------------- spec

def test_spec_valid() -> None:
    assert lines(spec(), "spec") == []


@pytest.mark.parametrize("path,value,expected", [
    (("sheets", 3, "font_size"), 10, "sheets[3].font_size: 10 is not allowed Fix: use 8 (dense sheets: a brick wall of data) or 9"),
    (("sheets", 3, "type"), "calcs", "sheets[3].type: \"calcs\" is not a known type; did you mean 'calc'?"),
    (("sheets", 1, "name"), "sales", "sheets[1].name: 'sales' is not 'raw_sales'"),
    (("sheets", 2, "name"), "helpers_sales", "sheets[2].name: 'helpers_sales' is not 'h_sales'"),
    (("sheets", 0, "items", 0, "value"), "Jan 2026", "sheets[0].items[0].value: \"Jan 2026\" is not a date in the form YYYY-MM-DD"),
    (("sheets", 0, "items", 2, "values"), [[1, 2]], "sheets[0].items[2].values: has 1 rows; rows.values has 2"),
    (("sheets", 0, "items", 1, "kind"), "lists", "sheets[0].items[1].kind: \"lists\" is not a known kind; did you mean 'list'?"),
    (("sheets", 3, "blocks", 0, "rows"), {"in": "regions", "values": ["a"]}, "sheets[3].blocks[0].rows: needs exactly one of 'values', 'in', 'same_as'; it has 'values', 'in'"),
    (("sheets", 3, "blocks", 0, "cols", "timeline", "step"), "week", "sheets[3].blocks[0].cols.timeline.step: \"week\" is not allowed"),
    (("sheets", 4, "checks", 0, "kind"), "equals", "sheets[4].checks[0].kind: \"equals\" is not a known kind; did you mean 'equal'?"),
    (("settings", "depth_limit"), 0, "settings.depth_limit: 0 is out of range Fix: use a whole number from 1 to 64"),
    (("settings", "depth"), 3, "settings.depth: unknown key 'depth'; did you mean 'depth_limit'?"),
    (("output",), "out/model.xlsx", "output: 'out/model.xlsx' does not have the required form"),
])
def test_spec_refusals(path: tuple, value: object, expected: str) -> None:
    s = spec()
    node = s
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    got = lines(s, "spec")
    assert any(x.startswith(expected) for x in got), got


def test_spec_block_needs_one_shape_and_names_a_misspelling() -> None:
    s = spec()
    block = s["sheets"][3]["blocks"][0]
    block["cel"] = block.pop("cell")
    got = lines(s, "spec")
    assert got == ["sheets[3].blocks[0]: a block needs exactly one of 'cell' (grid), 'items' (items), 'measures' "
                   "(measures); it has none; 'cel' looks like 'cell' Fix: rename 'cel' to 'cell'"]
    block["cell"] = "1"
    block.pop("cel")
    block["items"] = [{"id": "x", "label": "X", "expr": "1"}]
    assert any("it has 'cell', 'items'" in x for x in lines(s, "spec"))


def test_spec_check_keys_depend_on_kind() -> None:
    s = spec()
    s["sheets"][4]["checks"][1]["expected"] = "0"
    got = lines(s, "spec")
    assert any(x.startswith("sheets[4].checks[1].expected: unknown key 'expected'") for x in got), got
    s = spec()
    s["sheets"][4]["checks"].append({"id": "rng", "kind": "range", "label": "r", "actual": "1", "min": 0})
    assert any(x.startswith("sheets[4].checks[2].max: is required in a range check") for x in lines(s, "spec"))


def test_spec_unknown_top_level_key_and_missing_title() -> None:
    s = spec()
    s.pop("title")
    s["tittle"] = "x"
    got = lines(s, "spec")
    assert "title: is required in the specification Fix: add \"title\": text (not empty)" in got
    assert any(x.startswith("tittle: unknown key 'tittle'; did you mean 'title'?") for x in got)


def test_spec_every_error_has_a_fix() -> None:
    s = spec(rotm_spec=2, product="web")
    s["sheets"][3]["blocks"][0]["total"] = {"row": "yes"}
    errs = schemas.validate(s, "spec")
    assert len(errs) >= 3
    assert all(e.path and e.fix for e in errs)


# ----------------------------------------------------------------------------- dictionary

def plugin_dictionary() -> dict:
    return json.loads((SKILL / "references" / "style_dictionary.json").read_text(encoding="utf-8"))


def test_plugin_dictionary_is_valid() -> None:
    assert lines(plugin_dictionary(), "dictionary") == []


def test_dictionary_leaf_without_source_is_refused() -> None:
    d = plugin_dictionary()
    del d["font"]["source"]
    assert lines(d, "dictionary") == ["font.source: is required in a dictionary value Fix: add \"source\": the source text"]
    d = plugin_dictionary()
    d["cell_types"]["input"]["fill"]["source"] = "  "
    assert any(x.startswith("cell_types.input.fill.source: is empty") for x in lines(d, "dictionary"))


def test_dictionary_cross_references() -> None:
    d = plugin_dictionary()
    d["cell_types"]["input"]["fill"]["value"] = "input_fil"
    d["cell_types"]["calc"]["number_format"]["value"] = "amont"
    got = lines(d, "dictionary")
    assert any(x.startswith("cell_types.input.fill.value: colour key 'input_fil' is not in 'colors'; did you mean 'input_fill'?") for x in got)
    assert any(x.startswith("cell_types.calc.number_format.value: 'amont' is not 'item' or a key of number_formats; did you mean 'amount'?") for x in got)


def test_dictionary_named_override_set_is_checked() -> None:
    d = plugin_dictionary()
    d["named_overrides"]["input_blue_font"]["set"]["cell_types"]["input"]["fill"] = {"value": None}
    assert any(x.startswith("named_overrides.input_blue_font.set.cell_types.input.fill.source: is required")
               for x in lines(d, "dictionary"))


def test_dictionary_bad_colour_hex_and_sheet_type_missing() -> None:
    d = plugin_dictionary()
    d["colors"]["text"]["value"] = "black"
    del d["sheet_types"]["raw"]
    got = lines(d, "dictionary")
    assert any(x.startswith("colors.text.value: 'black' does not have the required form") for x in got)
    assert any(x.startswith("sheet_types.raw: is required") for x in got)


def test_dictionary_override_partial() -> None:
    ok = {"rotm_dictionary_override": 1, "use": ["input_blue_font"],
          "set": {"cell_types": {"input": {"bold": {"value": True, "source": "project choice"}}},
                  "colors": {"brand": {"value": "112233", "source": "project"}}}}
    assert lines(ok, "dictionary_override") == []
    bad = {"rotm_dictionary_override": 1, "set": {"cell_types": {"input": {"bold": {"value": True}}},
                                                  "font": "Calibri"}}
    got = lines(bad, "dictionary_override")
    assert any(x.startswith("set.cell_types.input.bold.source: is required") for x in got)
    assert any(x.startswith("set.font: must be a leaf") for x in got)


# ----------------------------------------------------------------------------- manifest, status, report

def manifest() -> dict:
    return {"rotm_manifest": 1, "plugin_version": "0.1.0", "built_at": "2026-11-25T02:00:00+09:00",
            "spec": "model.spec.json", "spec_sha256": "a" * 64, "dictionary_sha256": "b" * 64,
            "workbook": "model.xlsx", "settings": {"depth_limit": 2, "length_limit": 100, "allowed_literals": [0, 1],
                                                   "product": "both"},
            "sheets": [
                {"name": "raw_sales", "type": "raw", "font_size": 8, "table": "sales", "excel_table": "tbl_raw_sales",
                 "landing": "static", "query_name": "q_sales", "columns": ["invoice_id", "amount"],
                 "types": ["text", "number"], "rows": 600, "digest": "sha256:" + "c" * 64, "snapshot": "raw/sales.csv"},
                {"name": "h_sales", "type": "helpers", "font_size": 8, "table": "sales", "first_row": 2,
                 "last_row": 601, "capacity": 600,
                 "columns": [{"id": "row", "col": "A", "kind": "row", "formula": "=ROW()"},
                             {"id": "inv_date", "col": "C", "kind": "fetch", "raw_column": "inv_date",
                              "formula": "=INDEX(raw_sales!$C:$C,A2)"}]},
                {"name": "calc", "type": "calc", "font_size": 8,
                 "blocks": [{"id": "actual", "shape": "grid", "body": "D7:I10", "total": "J11", "checks": ["ties"]}]},
                {"name": "checks", "type": "checks", "font_size": 9, "master": "C2", "results": "I5:I18", "ids": "B5:B18"}],
            "inputs": {"scale": "inputs!D7"}, "cells": {"pct": "summary!F11"},
            "checks": [{"id": "sales_rows", "kind": "range", "result": "checks!I5", "auto": True, "targets": ["raw_sales"]}],
            "master_check": "checks!C2", "master_links": ["cover!C2", "inputs!C2"],
            "cell_types": {"inputs": [["B6:B9", "row_header"], ["D6:D9", "input"]]}}


def test_manifest_valid_and_invalid() -> None:
    assert lines(manifest(), "manifest") == []
    m = manifest()
    m["sheets"][0]["digest"] = "abc"
    m["cell_types"]["inputs"].append(["B6"])
    m["checks"][0]["result"] = "I5"
    m["sheets"][1]["columns"][0]["kind"] = "rows"
    got = lines(m, "manifest")
    assert any(x.startswith("sheets[0].digest: 'abc' does not have the required form") for x in got)
    assert any(x.startswith("cell_types.inputs[2]: must be a list of 2 items") for x in got)
    assert any(x.startswith("checks[0].result: 'I5' does not have the required form") for x in got)
    assert any(x.startswith("sheets[1].columns[0].kind: \"rows\" is not allowed") for x in got)


def test_manifest_allows_extra_keys_for_builders() -> None:
    m = manifest()
    m["layout"] = {"anything": 1}
    m["sheets"][2]["blocks"][0]["rows"] = ["East"]
    assert lines(m, "manifest") == []


def test_status_schema() -> None:
    ok = {"rotm_status": 1, "workbooks": {"model.xlsx": {
        "build": {"at": "x", "exit_code": 0, "sha256": "a" * 64, "errors": 0, "warnings": 0},
        "check": {"at": "x", "exit_code": 0, "input_sha256": "a" * 64, "output_sha256": "b" * 64,
                  "recalculated": True, "checks": 14, "failed": 0, "master": 0}}}}
    assert lines(ok, "status") == []
    bad = copy.deepcopy(ok)
    bad["workbooks"]["model.xlsx"]["Build"] = {}
    assert any(x.startswith("workbooks.model.xlsx.Build: 'Build' is not a valid name") for x in lines(bad, "status"))


def test_report_schema_rejects_bad_status() -> None:
    r = common.Report("audit", "x.xlsx")
    r.finish(False)
    d = r.to_dict()
    assert lines(d, "report") == []
    d["status"] = "ok"
    assert any(x.startswith("status: \"ok\" is not allowed") for x in lines(d, "report"))


def test_require_valid_raises_refusal_with_details() -> None:
    with pytest.raises(common.Refusal) as e:
        schemas.require_valid({"rotm_model": 1}, "marker", "rotm_model.json")
    assert e.value.exit_code == 2
    assert e.value.message.startswith("rotm_model.json is not valid (1 problem)")
    assert e.value.details == ["name: is required in rotm_model.json Fix: add \"name\": a model name"]


def test_number_types_are_strict() -> None:
    errs: list = []
    schemas.Int(0, 10).check(True, "x", errs)
    schemas.Int(0, 10).check(2.5, "y", errs)
    schemas.Num().check("3", "z", errs)
    schemas.Num().check(float("nan"), "w", errs)
    assert [e.path for e in errs] == ["x", "y", "z", "w"]


# ----------------------------------------------------------------------------- v2 (P1): nullflag, agreement table, controls

def test_manifest_helper_column_kind_nullflag() -> None:
    m = manifest()
    m["sheets"][1]["columns"].append({"id": "amount_null", "col": "D", "kind": "nullflag", "raw_column": "amount",
                                      "header": "amount empty", "formula": "=--(INDEX(raw_sales!$F:$F,A2)=\"\")"})
    assert lines(m, "manifest") == []
    m["sheets"][1]["columns"][-1]["kind"] = "nullflags"
    assert any(x.startswith("sheets[1].columns[2].kind: \"nullflags\" is not allowed") for x in lines(m, "manifest"))


def test_contract_agreement_records_its_table() -> None:
    agreed = {"status": "agreed", "contract_sha256": "a" * 64, "reply": "agreed", "by": "Max",
              "at": "2026-11-25T01:20:00+09:00", "channel": "session", "table": "sales"}
    assert lines(contract(agreement=agreed), "contract") == []
    assert lines(contract(agreement={"status": "proposed", "table": None}), "contract") == []
    bad = dict(agreed, table="Sales Lines")
    assert any(x.startswith("agreement.table: 'Sales Lines' does not have the required form")
               for x in lines(contract(agreement=bad), "contract"))


def test_controls_schema_valid_and_invalid() -> None:
    ok = {"rotm_controls": 1, "table": "sales", "accepted": [
        {"digest": "sha256:" + "c" * 64, "rows": 600, "totals": [{"column": "amount", "value": 12345.5, "tolerance": 0}],
         "given": ["sales.amount=12345.5"], "at": "2026-11-25T08:00:00+09:00"}]}
    assert lines(ok, "controls") == []
    bad = copy.deepcopy(ok)
    bad["accepted"][0]["digest"] = "c" * 64
    del bad["accepted"][0]["rows"]
    bad["extra"] = 1
    got = lines(bad, "controls")
    assert any(x.startswith("accepted[0].digest: ") for x in got)
    assert any(x.startswith("accepted[0].rows: is required") for x in got)
    assert any(x.startswith("extra: unknown key 'extra'") for x in got)

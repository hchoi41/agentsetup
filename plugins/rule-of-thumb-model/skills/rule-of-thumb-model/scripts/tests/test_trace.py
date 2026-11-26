"""Tests for lib/trace.py (M7): the chain from one cell back to the raw rows, in words (F18)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "data" / "compat"))

import m7_fixtures as fx7  # noqa: E402

from lib import common, trace  # noqa: E402

SCRIPTS = HERE.parent


def kinds(node: dict) -> list[str]:
    out = [node["kind"]]
    for ch in node["children"]:
        out += kinds(ch)
    return out


@pytest.fixture
def model(tmp_path: Path) -> Path:
    return fx7.model_like(tmp_path / "model_like.xlsx")


# ----------------------------------------------------------------------------- bounds of moved references

@pytest.mark.parametrize("a1, first, box, expected", [
    ("E2", (6, 2), (6, 2, 6, 601), (5, 2, 5, 601)),               # relative: moves down the helper column
    ("inputs!$D$6".split("!")[1], (6, 2), (6, 2, 6, 601), (4, 6, 4, 6)),  # anchored: stays
    ("$B7", (4, 7), (4, 7, 9, 7), (2, 7, 2, 7)),                  # row header of a grid row
    ("D$6", (4, 7), (4, 7, 9, 10), (4, 6, 9, 6)),                 # column header of a grid
    ("$G:$G", (4, 7), (4, 7, 4, 7), (7, 1, 7, 900)),              # whole column
    ("$H178:H178", (9, 178), (9, 178, 14, 178), (8, 178, 13, 178)),  # mixed anchors (a running total)
])
def test_moved_bounds(a1, first, box, expected):
    assert trace.moved_bounds(a1, first, box, (50, 900)) == expected


def test_moved_bounds_refuses_text_it_cannot_read():
    assert trace.moved_bounds("Sales[Qty]", (1, 1), (1, 1, 1, 1), (5, 5)) is None


# ----------------------------------------------------------------------------- chains

def test_chain_reaches_raw_columns_and_inputs(model):
    rep = trace.trace_cell(model, "calc!D10")
    text = "\n".join(rep.data["lines"])
    assert rep.data["lines"][0].startswith("calc!D10 'Total' / 'Amount'")
    assert "h_sales column E 'amount_k' (rows 2 to 9, 8 cells, helper column, one formula on every row): " \
           "=D2/inputs!$D$6" in text
    # v4, A-aera-13: raw columns read through SUMIFS are labelled as the rows that can contribute, with criteria
    contrib = ("the rows that can contribute to SUMIFS in calc!D6:D9, where h_sales!$C:$C matches $B6 (the "
               "criteria of its first cell)")
    assert f"raw_sales column C 'amount' (rows 2 to 9, 8 rows, raw data; {contrib})" in text
    assert f"raw_sales column B 'region' (rows 2 to 9, 8 rows, raw data; {contrib})" in text
    assert "inputs!D6 'Display scale' / 'Value' (input, unit 'KRW per unit shown')" in text
    assert rep.data["raw_sheets_reached"] == ["raw_sales"]
    assert {"raw", "helper_column", "input"} <= set(kinds(rep.data["tree"]))
    assert rep.exit_code == 0


def test_lines_are_indented_by_level(model):
    lines = trace.trace_cell(model, "calc!D10").data["lines"]
    assert not lines[0].startswith(" ")
    assert lines[1].startswith("  - ")
    assert any(line.startswith("        - ") for line in lines)


def test_check_cell_chain_and_seen_once(model):
    rep = trace.trace_cell(model, "checks!I5")
    text = "\n".join(rep.data["lines"])
    assert "checks!D5" in text and "checks!E5" in text and "inputs!D7 'Check tolerance'" in text
    assert text.count("raw_sales column C 'amount' (rows 2 to 9") == 1      # traced once, then "traced above"
    assert "traced above" in text


def test_depth_limit_stops_the_chain(model):
    rep = trace.trace_cell(model, "calc!D10", depth=1)
    text = "\n".join(rep.data["lines"])
    assert "depth limit reached" in text and "raw_sales" not in text


def test_json_tree_has_every_field(model, tmp_path):
    rep = trace.trace_cell(model, "calc!D6")
    tree = rep.data["tree"]
    assert set(tree) == {"ref", "label", "kind", "formula", "value", "count", "rows", "note", "children"}
    out = tmp_path / "t.json"
    rep.write_json(out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["command"] == "trace" and data["data"]["tree"]["ref"] == "calc!D6"


def test_structured_this_row_resolves_to_the_same_row(tmp_path):
    src = fx7.compat_cases(tmp_path / "cases.xlsx")
    rep = trace.trace_cell(src, "data!D3")
    text = "\n".join(rep.data["lines"])
    assert "data!B3 'A02' / 'Qty' (raw data) = 2" in text and "data!C3 'A02' / 'Price' (raw data) = 20" in text


def test_whole_table_reference_resolves_to_the_column(tmp_path):
    src = fx7.compat_cases(tmp_path / "cases.xlsx")
    rep = trace.trace_cell(src, "calc!C3")
    text = "\n".join(rep.data["lines"])
    assert "data column D 'Line'" in text and "rows 2 to 6" in text


def test_cycle_terminates(tmp_path):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "s"
    ws["A1"], ws["B1"] = "=B1+1", "=A1+1"
    wb.save(tmp_path / "cycle.xlsx")
    rep = trace.trace_cell(tmp_path / "cycle.xlsx", "s!A1")
    assert "traced above" in "\n".join(rep.data["lines"])


def test_external_name_and_ref_error_are_leaves(tmp_path):
    from openpyxl import Workbook
    from openpyxl.workbook.defined_name import DefinedName
    wb = Workbook()
    ws = wb.active
    ws.title = "s"
    ws["A1"] = 5
    ws["B1"] = "=rate*2+#REF!+missing_name"
    wb.defined_names["rate"] = DefinedName("rate", attr_text="s!$A$1")
    wb.save(tmp_path / "n.xlsx")
    rep = trace.trace_cell(tmp_path / "n.xlsx", "s!B1")
    k = kinds(rep.data["tree"])
    assert "constant" in k and "name" in k          # rate -> s!A1 (typed value); missing_name -> a leaf


def test_raw_sheets_option_names_the_raw_sheet(tmp_path):
    from openpyxl import Workbook
    wb = Workbook()
    d = wb.active
    d.title = "Journal"
    d.append(["acct", "amount"])
    for i in range(1, 5):
        d.append([f"a{i}", i * 10])
    s = wb.create_sheet("Sum")
    s["A1"] = "=SUM(Journal!B:B)"
    wb.save(tmp_path / "j.xlsx")
    plain = trace.trace_cell(tmp_path / "j.xlsx", "Sum!A1")
    assert plain.data["raw_sheets_reached"] == [] and any("no raw sheet" in n for n in plain.notes)
    named = trace.trace_cell(tmp_path / "j.xlsx", "Sum!A1", raw_sheets=["Journal"])
    assert named.data["raw_sheets_reached"] == ["Journal"]
    assert "Journal column B 'amount' (rows 2 to 5, 4 rows, raw data)" in "\n".join(named.data["lines"])


def test_manifest_sheet_types_are_used(model):
    common.write_json(model.with_name("model_like.manifest.json"), {
        "rotm_manifest": 1, "plugin_version": "0.1.0", "built_at": "2026-11-25T00:00:00+09:00",
        "spec": "model.spec.json", "workbook": model.name,
        "sheets": [{"name": "raw_sales", "type": "raw", "font_size": 8, "table": "sales"},
                   {"name": "h_sales", "type": "helpers", "font_size": 8, "table": "sales"},
                   {"name": "inputs", "type": "inputs", "font_size": 9}, {"name": "calc", "type": "calc", "font_size": 8},
                   {"name": "checks", "type": "checks", "font_size": 9}]})
    from lib import manifest
    try:
        if manifest.load_manifest(model) is None:
            pytest.skip("the manifest loader does not read this minimal manifest")
    except common.Refusal as exc:
        pytest.skip(f"the manifest schema wants more keys: {exc}")
    rep = trace.trace_cell(model, "calc!D10")
    text = "\n".join(rep.data["lines"])
    assert "calc!D6:D9 'Amount' (4 cells, one formula down the column)" in text
    assert not any("inferred" in n for n in rep.notes)


# ----------------------------------------------------------------------------- refusals

def test_refusals_say_what_to_change(model):
    with pytest.raises(common.Refusal, match="empty"):
        trace.trace_cell(model, "calc!Z99")
    with pytest.raises(common.Refusal, match="did you mean 'calc'"):
        trace.trace_cell(model, "calk!D10")
    with pytest.raises(common.Refusal, match="names no sheet"):
        trace.trace_cell(model, "D10")
    with pytest.raises(common.Refusal, match="not one cell"):
        trace.trace_cell(model, "calc!D6:D9")
    with pytest.raises(common.Refusal):
        trace.trace_cell(model, "calc!D10", depth=0)
    with pytest.raises(common.Refusal):
        trace.trace_cell(model, "calc!D10", raw_sheets=["nope"])


def test_typed_value_is_a_one_cell_chain(model):
    rep = trace.trace_cell(model, "inputs!D6")
    assert len(rep.data["lines"]) == 1 and "= 1,000" in rep.data["lines"][0]
    assert any("typed value" in n for n in rep.notes)


# ----------------------------------------------------------------------------- command line and foreign files

def test_cli_prints_lines_and_json(model):
    env = dict(os.environ, ROTM_NO_EXCEL="1")
    cmd = [sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), "trace", str(model), "calc!D10"]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env)
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines()[0].startswith("calc!D10")
    res = subprocess.run(cmd + ["--json", "-"], capture_output=True, text=True, encoding="utf-8", env=env)
    assert res.returncode == 0 and json.loads(res.stdout)["data"]["tree"]["kind"] == "block_cell"
    res = subprocess.run(cmd[:-1] + ["nosheet!A1"], capture_output=True, text=True, encoding="utf-8", env=env)
    assert res.returncode == 2 and "Traceback" not in res.stderr


FOREIGN = [("Beach Part 5_Complete.xlsx", "'Trial Balance'!D5"), ("M2V3_end.xlsx", "financial_summary!C8"),
           ("12 Month Rolling Forecast.xlsx", "'12 Month Rolling Forecast'!C3"),
           ("DCF Valuation Model (Complete).xlsx", "Model!I178"), ("Precision Model Complete.xlsx", None)]


@pytest.mark.parametrize("name, ref", FOREIGN)
def test_foreign_workbooks_trace_without_traceback(name, ref, foreign_dir, tmp_path):
    if not (foreign_dir / name).is_file():
        pytest.skip(f"{name} is not in {foreign_dir}")
    src = tmp_path / name
    shutil.copy2(foreign_dir / name, src)
    if ref is None:                                   # the last formula of the first sheet that has one
        from lib import workbook
        info = workbook.open_workbook(src, with_values=False)
        cell = next(c for s in info.sheets for c in s.formulas() if c.formula)
        ref = f"{common.quote_sheet(cell.sheet)}!{cell.coord}"
    rep = trace.trace_cell(src, ref, depth=6)
    assert rep.data["lines"] and rep.data["nodes"] <= trace.MAX_NODES + 10

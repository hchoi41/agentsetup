"""Tests for lib/document.py (owner M8): the model map from manifest, specification and contracts, and the
inferred map of a workbook without a manifest."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from lib import common, document

DATA = Path(__file__).resolve().parent / "data" / "lift"
if str(DATA) not in sys.path:
    sys.path.insert(0, str(DATA))
import fixture  # noqa: E402

ROTM = Path(__file__).resolve().parents[1] / "rotm.py"


def rotm(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(ROTM), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=300, env=dict(os.environ))


@pytest.fixture
def model(tmp_path: Path) -> dict:
    return fixture.write_model(tmp_path / "model")


def test_map_of_a_model_folder_has_every_section(model):
    report = document.document_model(model["root"])
    assert report.exit_code == 0, [f.line() for f in report.findings]
    path = model["root"] / document.DEFAULT_NAME
    md = path.read_text(encoding="utf-8")
    assert report.data["mode"] == "manifest"
    for section in ("Sheets", "Raw tables and column contracts", "Inputs", "Helper columns", "Calculation blocks",
                    "Checks", "Key outputs", "Style", "Limitations"):
        assert f"\n## {section}\n" in md, section
    assert md.startswith("# Model map: Fixture: sales against budget by region")


def test_map_shows_inputs_with_value_unit_cell_and_source(model):
    document.document_model(model["root"])
    md = (model["root"] / document.DEFAULT_NAME).read_text(encoding="utf-8")
    assert "| scale | Display scale | 1,000 | KRW per unit shown | inputs!D6 | fixture |" in md
    assert "| start_month | First month | 2026-01-31 | date | inputs!D7 | fixture |" in md
    assert "| regions | Regions | East, North, South, West |  | inputs!D12:D15 | contract sales |" in md


def test_map_shows_contract_and_the_users_reply(model):
    document.document_model(model["root"])
    md = (model["root"] / document.DEFAULT_NAME).read_text(encoding="utf-8")
    assert "- Grain: one row per invoice line; key: invoice_id, line_no" in md
    assert "| region | text |  | never | East, North, South, West | region |  |" in md
    assert '- Agreement: agreed by fixture on 2026-11-25T00:00:00+09:00 through example; reply: "agreed"' in md
    assert "state agreed" in md


def test_map_shows_helper_columns_blocks_and_checks(model):
    document.document_model(model["root"])
    md = (model["root"] / document.DEFAULT_NAME).read_text(encoding="utf-8")
    assert "| H | month_end | month_end | derived | `=EOMONTH(E2,0)` | `EOMONTH({this:inv_date},0)` |  |" in md
    assert "| calc!D6:F9 | actual | Actual sales | grid | list input regions (4 values) |" in md
    assert "timeline of 3 months from input start_month (month ends)" in md
    assert "`=SUMIFS(h_sales!$I:$I,h_sales!$F:$F,$B6,h_sales!$H:$H,D$5)`" in md
    assert "| actual_ties_raw | equal | Actual block total equals the raw total | error | checks!I13 |" in md
    assert "| neg_var | zero | Variance cells below zero | alert | checks!I17 |" in md
    assert "- Master check: `checks!C2`" in md
    assert "Values: not recalculated" in md                 # openpyxl-written: no saved values shown


def test_a_proposed_contract_is_shown_as_not_agreed(model):
    path = model["root"] / "contracts" / "sales.contract.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["agreement"] = {"status": "proposed"}
    path.write_text(json.dumps(data), encoding="utf-8")
    document.document_model(model["root"])
    md = (model["root"] / document.DEFAULT_NAME).read_text(encoding="utf-8")
    assert "- Agreement: proposed, not agreed." in md and "state proposed" in md


def test_inferred_map_of_a_workbook_without_manifest(model, tmp_path):
    wb = tmp_path / "plain" / "book.xlsx"
    wb.parent.mkdir()
    shutil.copy2(model["workbook"], wb)
    report = document.document_model(wb)
    assert report.data["mode"] == "inferred" and report.exit_code == 0
    out = wb.with_name(f"book_{document.DEFAULT_NAME}")
    md = out.read_text(encoding="utf-8")
    assert "has no manifest" in md and "## Sheets (inferred)" in md
    assert "| raw_sales | raw (inferred: name or query table) |" in md
    assert "| h_sales | I | 2 to 41 | amount_k | `=G2/inputs!$D$6` |" in md


def test_output_path_and_cli(model, tmp_path):
    out = tmp_path / "maps" / "map.md"
    res = rotm("document", str(model["root"]), "-o", str(out), "--json", "-")
    assert res.returncode == 0, res.stderr
    rep = json.loads(res.stdout)
    assert rep["command"] == "document" and rep["data"]["path"] == common.norm_path(out)
    assert out.is_file()
    res = rotm("document", str(tmp_path / "nowhere"))
    assert res.returncode == 2 and "not found" in res.stderr


def test_a_bad_specification_still_gives_a_map(model):
    model["spec"].write_text("{not json", encoding="utf-8")
    report = document.document_model(model["root"])
    assert [f.rule for f in report.findings] == ["M-SPEC"]
    md = (model["root"] / document.DEFAULT_NAME).read_text(encoding="utf-8")
    assert "## Helper columns" in md and "| H | month_end |" in md


@pytest.mark.excel
def test_map_shows_saved_values_after_recalculation(model):
    from lib import excel
    assert excel.recalc(model["workbook"], save=True).ok
    document.document_model(model["root"])
    md = (model["root"] / document.DEFAULT_NAME).read_text(encoding="utf-8")
    assert "the values Excel saved" in md
    assert "| sales_rows | range | sales rows | error | checks!I5 | OK | added by build |" in md
    assert "saved result OK" in md


def test_map_of_foreign_workbooks_ends_without_traceback(foreign_dir, tmp_path):
    done = 0
    for src in sorted(foreign_dir.glob("*.xls*"))[:8]:
        copy = tmp_path / src.name
        shutil.copy2(src, copy)
        try:
            report = document.document_model(copy, out=tmp_path / f"{src.stem}.md")
        except common.Refusal as exc:          # encrypted or .xls: refused with what to change
            assert exc.fix
            continue
        assert report.data["mode"] == "inferred"
        done += 1
    assert done >= 1

"""Tests of lib/build.py (design sections 6, 7, 9; S1, F04, F05, F14 base). Owner: M3.

The fixture model (tests/data/build/) has one raw table (sales: 72 invoice lines, January to June 2026, four
regions), an input list, an input grid, two helper columns, a grid block with totals, a grid read from the input
grid with INDEX and two MATCH calls, an items block with a running sum, a measures block with a "same" total, four
error checks, one alert and a notes sheet. Tests marked excel recalculate the built workbook in Excel and compare
the numbers with a calculation in Python from the CSV.
"""
from __future__ import annotations

import copy
import csv
import datetime as dt
import json
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
DATA = Path(__file__).resolve().parent / "data" / "build"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib import build, common, formula, land, manifest, modelfolder, schemas  # noqa: E402


# ----------------------------------------------------------------------------- fixtures

def agree(contract: dict, *, by: str = "tester") -> dict:
    c = {k: v for k, v in contract.items() if k != "agreement"}
    c["agreement"] = {"status": "agreed", "contract_sha256": modelfolder.contract_hash(c), "reply": "ok, agreed",
                      "by": by, "at": "2026-11-25T02:00:00+09:00", "channel": "session", "source_file": None,
                      "recorded_by": "test"}
    return c


def base_spec() -> dict:
    return json.loads((DATA / "model.spec.json").read_text(encoding="utf-8"))


def base_contract() -> dict:
    return json.loads((DATA / "sales.contract.json").read_text(encoding="utf-8"))


def make_model(root: Path, *, spec: dict | None = None, contract: dict | None = None, agreed: bool = True,
               csv_path: Path | None = None, land_it: bool = True) -> Path:
    modelfolder.init_model(root, name="sales_test")
    c = contract if contract is not None else base_contract()
    c = agree(c) if agreed else {**{k: v for k, v in c.items() if k != "agreement"}, "agreement": {"status": "proposed"}}
    common.write_json(root / "contracts" / "sales.contract.json", c)
    if land_it:
        if agreed:
            rep = land.land_csv(root, "sales", csv_path or DATA / "sales.csv")
            assert rep.exit_code == 0, [f.line() for f in rep.findings]
        else:
            (root / "raw").mkdir(exist_ok=True)
            shutil.copy(csv_path or DATA / "sales.csv", root / "raw" / "sales.csv")
    common.write_json(root / "model.spec.json", spec if spec is not None else base_spec())
    return root


@pytest.fixture
def model(tmp_path: Path) -> Path:
    return make_model(tmp_path / "m")


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, object]:
    import os
    state = tmp_path_factory.mktemp("state_build_module")
    old = os.environ.get("ROTM_STATE_DIR")
    os.environ["ROTM_STATE_DIR"] = str(state)
    try:
        root = make_model(tmp_path_factory.mktemp("built") / "m")
        rep = build.build(root / "model.spec.json", strict=True)
    finally:
        if old is None:
            os.environ.pop("ROTM_STATE_DIR", None)
        else:
            os.environ["ROTM_STATE_DIR"] = old
    return root, rep


def load(path: Path):
    import openpyxl
    return openpyxl.load_workbook(path)


def refusal_lines(exc: common.Refusal) -> str:
    return "\n".join([exc.message] + list(exc.details))


def sales_rows() -> list[dict]:
    with open(DATA / "sales.csv", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# ----------------------------------------------------------------------------- the built fixture

def test_build_fixture_strict_passes(built) -> None:
    root, rep = built
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    assert rep.counts() == {"errors": 0, "warnings": 0, "info": 0}
    assert (root / "model.xlsx").is_file()
    assert any("not recalculated" in n for n in rep.notes)


def test_sheet_order_is_the_type_order(built) -> None:
    root, _ = built
    wb = load(root / "model.xlsx")
    assert wb.sheetnames == ["cover", "inputs", "raw_sales", "h_sales", "calc", "summary", "checks", "notes"]
    assert wb.active.title == "cover"
    assert len(wb.defined_names) == 0               # FAST 3.03-08: no defined names


def test_manifest_is_valid_and_complete(built) -> None:
    root, _ = built
    data = manifest.load_manifest(root / "model.xlsx")
    assert schemas.validate(data, "manifest") == []
    assert data["master_check"] == "checks!C2"
    raw = manifest.raw_sheet(data, "sales")
    assert raw["rows"] == 72 and raw["excel_table"] == "tbl_raw_sales" and raw["landing"] == "static"
    assert raw["columns"] == ["invoice_id", "line_no", "inv_date", "region", "product", "amount"]
    h = manifest.helper_sheet(data, "sales")
    assert h["first_row"] == 2 and h["last_row"] == 73 and h["capacity"] == 72
    assert [c["id"] for c in h["columns"]] == ["row", "in_raw", "invoice_id", "line_no", "inv_date", "region",
                                              "product", "amount", "month_end", "amount_k", "dup_key", "dup"]
    assert h["columns"][0]["formula"] == "=ROW()"
    assert set(data["cells"]) >= {"cum", "pct", "bridge.cum", "ytd.pct"}
    assert data["cells"]["pct"] == "summary!F11"
    ids = [c["id"] for c in data["checks"]]
    assert ids[:2] == ["sales_rows", "sales_total_amount"] and "actual_ties_raw" in ids
    assert [c["severity"] for c in data["checks"] if c["id"] == "neg_months"] == ["alert"]
    calc = manifest.sheet_entry(data, "calc")
    actual = calc["blocks"][0]
    assert actual["id"] == "actual" and actual["body"] == "D6:I9" and actual["total"] == "J10"
    assert actual["checks"] == ["actual_ties_raw", "cum_ties", "pct_ties"]
    bridge = calc["blocks"][2]
    assert [i["first"] for i in bridge["items"]] == [False, False, False, True]
    assert data["cell_types"]["inputs"]


def test_raw_sheet_digest_equals_manifest(built) -> None:
    root, _ = built
    data = manifest.load_manifest(root / "model.xlsx")
    raw = manifest.raw_sheet(data, "sales")
    digest, rows, header = manifest.sheet_digest(root / "model.xlsx", "raw_sales", raw["types"])
    assert digest == raw["digest"] and rows == 72
    snap_digest, _n, _h = manifest.csv_digest(root / "raw" / "sales.csv", raw["types"])
    assert snap_digest == digest


def test_raw_sheet_is_values_only_with_a_plain_table(built) -> None:
    root, _ = built
    wb = load(root / "model.xlsx")
    ws = wb["raw_sales"]
    assert ws.freeze_panes == "A2"
    assert not any(isinstance(c.value, str) and c.value.startswith("=") for row in ws.iter_rows() for c in row)
    tbl = ws.tables["tbl_raw_sales"]
    assert tbl.ref == "A1:F73" and tbl.tableStyleInfo is None
    assert ws["A2"].value == "000101" and ws["A2"].number_format == "@"      # leading zeros kept as text
    assert isinstance(ws["C2"].value, dt.datetime)
    assert ws.max_row == 73 and ws.max_column == 6                           # nothing below or beside


def test_every_formula_passes_the_formula_rules(built) -> None:
    root, _ = built
    wb = load(root / "model.xlsx")
    data = manifest.load_manifest(root / "model.xlsx")
    n = 0
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value.startswith("="):
                    n += 1
                    issues = formula.check_formula(c.value, depth_limit=2, length_limit=100, sheet=ws.title)
                    assert issues == [], (ws.title, c.coordinate, c.value, issues)
                    assert formula.depth(c.value) <= 2
    assert n > 900
    assert data["formula_cells"] == n


def test_helper_columns_have_one_relative_form(built) -> None:
    root, _ = built
    ws = load(root / "model.xlsx")["h_sales"]
    for col in range(1, ws.max_column + 1):
        forms = {formula.relative_form(ws.cell(row=r, column=col).value, ws.cell(row=r, column=col).coordinate)
                 for r in range(2, ws.max_row + 1)}
        assert len(forms) == 1, (col, forms)
    assert ws["B2"].value == '=--(INDEX(raw_sales!$A:$A,A2)<>"")'
    assert ws["C2"].value == '=INDEX(raw_sales!$A:$A,A2)&""'
    assert ws["I2"].value == "=EOMONTH(E2,0)"
    assert ws["J5"].value == "=H5/inputs!$D$7"
    assert ws["K2"].value == '=C2&"|"&D2'
    assert ws["L2"].value == "=--(SUMPRODUCT(--EXACT(K$2:K2,K2))>1)*B2"     # v2: exact key equality (A-aera-9)


def test_grid_items_and_measures_formulas(built) -> None:
    root, _ = built
    wb = load(root / "model.xlsx")
    calc, summary, checks = wb["calc"], wb["summary"], wb["checks"]
    assert calc["D5"].value == "=EOMONTH(inputs!$D$6,0)" and calc["E5"].value == "=EOMONTH(D5,1)"
    assert calc["B6"].value == "=inputs!$D$11"
    assert calc["D6"].value == "=SUMIFS(h_sales!$J:$J,h_sales!$F:$F,$B6,h_sales!$I:$I,D$5)"
    assert calc["J6"].value == "=SUM(D6:I6)" and calc["D10"].value == "=SUM(D6:D9)"
    assert calc["J10"].value == "=SUM(J6:J9)"
    assert calc["D14"].value == ("=INDEX(inputs!$D$18:$I$21,MATCH($B14,inputs!$B$18:$B$21,0),"
                                 "MATCH(D$13,inputs!$D$17:$I$17,0))")
    assert calc["D22"].value == "=D$10"                        # {b:actual.total}
    assert calc["D25"].value == "=D$24" and calc["E25"].value == "=D$25+E$24"   # first, then {prev}+{r:var}
    assert summary["D7"].value == "=SUM(calc!$D6:$I6)"           # {grow:actual}
    assert summary["F7"].value == "=IFERROR($D7/$E7-1,0)"
    assert summary["F11"].value == "=IFERROR($D11/$E11-1,0)"     # total "same"
    assert summary["D11"].value == "=SUM(D7:D10)"
    # v2: one more generated check, sales_beyond (A-aera-6)
    assert checks["C2"].value == "=--(OR(SUM(I5:I17)>0,COUNT(I5:I17)<>COUNTA(B5:B17)))"
    assert checks["I5"].value == "=--(OR(D5<E5,D5>F5))"
    assert checks["I6"].value == "=--(ABS(G6)>H6)"
    assert checks["D14"].value == "=calc!$J$10" and checks["E14"].value == "=SUM(raw_sales!$F:$F)/inputs!$D$7"
    assert checks["D16"].value == "=calc!$I$25"                   # {cell:cum}: the item's last column
    assert checks["B12"].value == "sales_beyond" and checks["D12"].value == "=COUNTIF(h_sales!$B:$B,0)"
    assert checks["I12"].value == "=--(D12<>0)"
    for name in ("cover", "inputs", "calc", "summary", "notes"):
        assert wb[name]["C2"].value == "=checks!$C$2"


def test_styles_follow_the_dictionary(built) -> None:
    root, _ = built
    wb = load(root / "model.xlsx")
    sizes = {"cover": 9, "inputs": 9, "raw_sales": 8, "h_sales": 8, "calc": 8, "summary": 9, "checks": 9,
             "notes": 9}
    for ws in wb.worksheets:
        assert ws.sheet_view.showGridLines is False
        for row in ws.iter_rows():
            for c in row:
                if c.value is None:
                    continue
                assert c.font.name == "Arial", (ws.title, c.coordinate)
                if c.row != 1 or ws.title.startswith(("raw_", "h_")):
                    assert c.font.sz == sizes[ws.title], (ws.title, c.coordinate, c.font.sz)
    inputs = wb["inputs"]
    assert inputs["D7"].fill.fgColor.rgb.endswith("FFF2CC")          # input fill (design 10.3 default)
    assert inputs["B5"].fill.fgColor.rgb.endswith("D9D9D9") and inputs["B5"].font.b
    assert inputs["B6"].fill.fgColor.rgb.endswith("F2F2F2")
    assert wb["checks"]["I5"].number_format == '[Red]"FAIL";[Red]"FAIL";"OK"'
    assert wb["cover"]["B1"].font.b and wb["cover"]["B1"].font.sz == 9
    assert wb["calc"].freeze_panes == "D4" and wb["checks"].freeze_panes == "D5" and wb["h_sales"].freeze_panes == "C2"
    assert wb["calc"].column_dimensions["A"].width == pytest.approx(2.5, abs=0.6)


def test_inputs_controls_and_validation(built) -> None:
    root, _ = built
    ws = load(root / "model.xlsx")["inputs"]
    labels = {ws.cell(row=r, column=2).value: r for r in range(1, ws.max_row + 1)}
    r = labels["sales: control total of amount"]
    assert ws.cell(row=r, column=4).value == 31435000
    assert ws.cell(row=r, column=4).number_format == '#,##0_);(#,##0);"-"_)'
    assert ws.cell(row=labels["Check tolerance"], column=4).value == 0.001
    assert any("D7" in str(dv.sqref) for dv in ws.data_validations.dataValidation)


def test_cover_lists_sheets_cell_types_and_build_record(built) -> None:
    root, _ = built
    ws = load(root / "model.xlsx")["cover"]
    texts = [c.value for row in ws.iter_rows() for c in row if isinstance(c.value, str)]
    for name in ("raw_sales", "h_sales", "Cell types", "Build record", "Limitations", "input", "master_check"):
        assert name in texts, name
    assert any(t.startswith("rule-of-thumb-model 0.1.0") for t in texts)


def test_status_and_report_recorded(built) -> None:
    root, _ = built
    st = modelfolder.read_status(root)
    rec = st["workbooks"]["model.xlsx"]["build"]
    assert rec["exit_code"] == 0 and rec["sha256"] == common.file_sha256(root / "model.xlsx")
    rep = common.read_json(root / "reports" / "build.json")
    assert schemas.validate(rep, "report") == []


# ----------------------------------------------------------------------------- refusals (exit 2, nothing written)

def _set_cell(spec: dict, sheet: str, block: str, expr: str) -> dict:
    for s in spec["sheets"]:
        if s["name"] == sheet:
            for b in s["blocks"]:
                if b["id"] == block:
                    b["cell"] = expr
    return spec


@pytest.mark.parametrize("expr, needle", [
    ("IFERROR(INDEX({in:target},MATCH({row},{in:target.rows},0),1),0)",
     "move MATCH({row},{in:target.rows},0) into a helper grid block"),
    ("SUMIFS({h:sales.amount_k},{h:sales.region},{row})*1.1", "put 1.1 in an input item"),
    ("SUM(A1:A3)", "a plain reference 'A1:A3'"),
    ("SUM(OFFSET({blk:tgt},0,0))", "OFFSET is refused"),
    ("IF({row}=\"East\",IF({col}>0,1,0),0)", "IF inside IF"),
    ("SUM(scale)", "a defined name 'scale'"),
    ("SUM({blk:tgt.total})", "no single grand total"),
])
def test_bad_expressions_are_refused_with_the_part_to_move(tmp_path: Path, expr: str, needle: str) -> None:
    spec = base_spec()
    if needle == "no single grand total":
        for s in spec["sheets"]:
            if s["name"] == "calc":
                s["blocks"][1]["total"] = {"row": True}
        _set_cell(spec, "calc", "actual", "SUMIFS({h:sales.amount_k},{h:sales.region},{row},{h:sales.month_end},"
                                          "{col})+0*SUM({blk:tgt.total})")
        spec["sheets"][-2]["checks"][1]["actual"] = "SUM({blk:tgt})"
    else:
        _set_cell(spec, "calc", "actual", expr)
    root = make_model(tmp_path / "m", spec=spec)
    with pytest.raises(common.Refusal) as ei:
        build.build(root / "model.spec.json")
    text = refusal_lines(ei.value)
    assert needle in text, text
    assert "sheets[3].blocks[0].cell" in text
    assert "nothing was built" in ei.value.message
    assert not (root / "model.xlsx").exists()


def test_depth_limit_setting_is_used_and_one_is_refused(tmp_path: Path) -> None:
    spec = base_spec()
    spec["settings"] = {"depth_limit": 1}
    root = make_model(tmp_path / "m", spec=spec)
    with pytest.raises(common.Refusal) as ei:
        build.build(root / "model.spec.json")
    assert "depth limit 1 is below 2" in refusal_lines(ei.value)


def test_allowed_literals_must_keep_zero_and_one(tmp_path: Path) -> None:
    spec = base_spec()
    spec["settings"] = {"allowed_literals": [0]}
    root = make_model(tmp_path / "m", spec=spec)
    with pytest.raises(common.Refusal) as ei:
        build.build(root / "model.spec.json")
    assert "must include 0 and 1" in refusal_lines(ei.value)


def test_allowed_literal_in_settings_is_accepted(tmp_path: Path) -> None:
    spec = base_spec()
    spec["settings"] = {"allowed_literals": [0, 1, 12]}
    for s in spec["sheets"]:
        if s["name"] == "h_sales":
            s["columns"].append({"id": "per_month", "expr": "{this:amount}/12"})
    root = make_model(tmp_path / "m", spec=spec)
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 0
    assert load(root / "model.xlsx")["h_sales"]["K2"].value == "=H2/12"


def test_unagreed_contract_is_refused(tmp_path: Path) -> None:
    root = make_model(tmp_path / "m", agreed=False)
    with pytest.raises(common.Refusal) as ei:
        build.build(root / "model.spec.json")
    assert "proposed, not agreed" in refusal_lines(ei.value)
    assert not (root / "model.xlsx").exists()


def test_missing_snapshot_is_refused_with_the_land_command(tmp_path: Path) -> None:
    root = make_model(tmp_path / "m", land_it=False)
    with pytest.raises(common.Refusal) as ei:
        build.build(root / "model.spec.json")
    text = refusal_lines(ei.value)
    assert "raw/sales.csv not found" in text and "rotm.py land" in text


def test_key_output_that_is_not_one_cell_is_refused(tmp_path: Path) -> None:
    spec = base_spec()
    for s in spec["sheets"]:
        if s["name"] == "summary":
            s["blocks"][0]["measures"][2]["total"] = "none"
    root = make_model(tmp_path / "m", spec=spec)
    with pytest.raises(common.Refusal) as ei:
        build.build(root / "model.spec.json")
    text = refusal_lines(ei.value)
    assert "key output 'pct' is not one cell" in text and "\"total\": \"sum\" or \"same\"" in text


def test_typed_number_in_a_check_is_refused(tmp_path: Path) -> None:
    spec = base_spec()
    spec["sheets"][-2]["checks"][0]["expected"] = 31435
    root = make_model(tmp_path / "m", spec=spec)
    with pytest.raises(common.Refusal) as ei:
        build.build(root / "model.spec.json")
    assert "the number 31435 is typed into the check" in refusal_lines(ei.value)


def test_helper_expression_depth_names_a_helper_column(tmp_path: Path) -> None:
    spec = base_spec()
    for s in spec["sheets"]:
        if s["name"] == "h_sales":
            s["columns"][0]["expr"] = "EOMONTH(DATE(YEAR({this:inv_date}),MONTH({this:inv_date}),1),0)"
    root = make_model(tmp_path / "m", spec=spec)
    with pytest.raises(common.Refusal) as ei:
        build.build(root / "model.spec.json")
    text = refusal_lines(ei.value)
    assert "sheets[2].columns[0].expr" in text
    assert "move YEAR({this:inv_date}) into a new helper column on this helper sheet" in text


def test_cli_refusal_exit_2_and_json(tmp_path: Path) -> None:
    spec = _set_cell(base_spec(), "calc", "actual", "SUM(A1:A3)")
    root = make_model(tmp_path / "m", spec=spec)
    out = tmp_path / "rep.json"
    proc = subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), "build",
                           str(root / "model.spec.json"), "--json", str(out)],
                          capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert proc.returncode == 2, proc.stderr
    assert "Traceback" not in proc.stderr and "plain reference" in proc.stderr
    rep = common.read_json(out)
    assert rep["status"] == "refused" and rep["exit_code"] == 2 and schemas.validate(rep, "report") == []


# ----------------------------------------------------------------------------- warnings, strict, overwrite

def test_block_without_check_warns_and_strict_exits_1(tmp_path: Path) -> None:
    spec = base_spec()
    spec["sheets"][-2]["checks"] = [c for c in spec["sheets"][-2]["checks"] if c["id"] != "pct_ties"]
    root = make_model(tmp_path / "m", spec=spec)
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 1
    assert [f.rule for f in rep.findings] == ["W-NO-CHECK"]
    assert "block 'ytd'" in rep.findings[0].message
    assert (root / "model.xlsx").is_file()                    # written all the same (as drawio's build --strict)
    rep2 = build.build(root / "model.spec.json", strict=False)
    assert rep2.exit_code == 0


def test_long_formula_is_a_warning(tmp_path: Path) -> None:
    spec = base_spec()
    spec["settings"] = {"length_limit": 40}
    root = make_model(tmp_path / "m", spec=spec)
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 1
    rules = {f.rule for f in rep.findings}
    assert rules == {"F-LENGTH"}


def test_overwrite_rules(tmp_path: Path, model: Path) -> None:
    target = model / "model.xlsx"
    target.write_bytes(b"not a workbook of ours")
    with pytest.raises(common.Refusal) as ei:
        build.build(model / "model.spec.json")
    assert "was not written by rotm.py" in ei.value.message and "--force" in (ei.value.fix or "")
    rep = build.build(model / "model.spec.json", force=True)
    assert rep.exit_code == 0
    backups = list(model.glob("model.bak_*.xlsx"))
    assert len(backups) == 1 and backups[0].read_bytes() == b"not a workbook of ours"
    rep = build.build(model / "model.spec.json")       # its own last output: no --force needed
    assert rep.exit_code == 0
    import openpyxl
    wb = openpyxl.load_workbook(target)
    wb["notes"]["B30"] = "typed by hand"
    wb.save(target)
    with pytest.raises(common.Refusal) as ei:
        build.build(model / "model.spec.json")
    assert "changed after rotm.py last wrote it" in ei.value.message


def test_output_option_and_bad_suffix(model: Path, tmp_path: Path) -> None:
    rep = build.build(model / "model.spec.json", out=model / "other.xlsx")
    assert rep.exit_code == 0 and (model / "other.xlsx").is_file() and (model / "other.manifest.json").is_file()
    with pytest.raises(common.Refusal):
        build.build(model / "model.spec.json", out=model / "other.xls")


def test_capacity_option_and_contract_capacity(tmp_path: Path) -> None:
    root = make_model(tmp_path / "a")
    rep = build.build(root / "model.spec.json", capacity={"sales": 10})
    assert [f.rule for f in rep.findings] == ["W-CAPACITY"]
    ws = load(root / "model.xlsx")["h_sales"]
    assert ws.max_row == 11
    # v2 (A-aera-6): a capacity above the landed rows is refused, whatever its source, and the source is named
    c = base_contract()
    c["refresh"]["capacity_rows"] = 100
    root2 = make_model(tmp_path / "b", contract=c)
    with pytest.raises(common.Refusal) as ei:
        build.build(root2 / "model.spec.json", strict=True)
    text = refusal_lines(ei.value)
    assert "contract sales, refresh.capacity_rows" in text and "72 landed rows" in text and "extend" in text
    assert not (root2 / "model.xlsx").exists()
    with pytest.raises(common.Refusal) as ei:
        build.build(root / "model.spec.json", capacity={"sales": 73}, force=True)
    assert "--capacity" in refusal_lines(ei.value) and "remove --capacity sales=73" in refusal_lines(ei.value)
    spec = base_spec()
    spec["settings"] = {"helper_spare_rows": 5}
    root3 = make_model(tmp_path / "c", spec=spec)
    with pytest.raises(common.Refusal) as ei:
        build.build(root3 / "model.spec.json")
    assert "settings.helper_spare_rows" in refusal_lines(ei.value)
    assert build.build(root / "model.spec.json", capacity={"sales": 72}, force=True).exit_code == 0
    with pytest.raises(common.Refusal):
        build.parse_capacity(["sales:10"])
    with pytest.raises(common.Refusal) as ei:
        build.build(root / "model.spec.json", capacity={"nope": 5})
    assert "table 'nope' has no raw sheet" in refusal_lines(ei.value)


def test_key_check_moves_out_above_the_row_limit(tmp_path: Path) -> None:
    spec = base_spec()
    spec["settings"] = {"formula_key_check_max_rows": 50}
    root = make_model(tmp_path / "m", spec=spec)
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 0
    assert [f.rule for f in rep.findings] == ["I-KEYS-OUTSIDE"]
    wb = load(root / "model.xlsx")
    assert wb["h_sales"]["K1"].value is None                     # no dup column
    data = manifest.load_manifest(root / "model.xlsx")
    assert "sales_keys" not in [c["id"] for c in data["checks"]]
    assert data["outside_checks"][0]["id"] == "sales_keys"
    texts = [c.value for row in wb["checks"].iter_rows() for c in row if isinstance(c.value, str)]
    assert any("checked by rotm.py check" in t for t in texts)
    last = 4 + len(data["checks"]) - 1                          # error checks only, no alert
    assert wb["checks"]["C2"].value == f"=--(OR(SUM(I5:I{last})>0,COUNT(I5:I{last})<>COUNTA(B5:B{last})))"


def test_text_that_starts_with_equals_lands_as_text(tmp_path: Path) -> None:
    rows = sales_rows()
    rows[0]["product"] = "=1+1"
    src = tmp_path / "odd.csv"
    with open(src, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    root = make_model(tmp_path / "m", csv_path=src)
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 0
    cell = load(root / "model.xlsx")["raw_sales"]["E2"]
    assert cell.value == "=1+1" and cell.data_type == "s"


def test_generated_sheets_when_the_spec_omits_them(tmp_path: Path) -> None:
    spec = base_spec()
    spec["sheets"] = [s for s in spec["sheets"] if s["type"] not in ("notes",)]
    for s in spec["sheets"]:
        if s["type"] == "checks":
            s.pop("name")
    spec["sheets"] = [s for s in spec["sheets"] if "name" in s]
    root = make_model(tmp_path / "m", spec=spec)
    rep = build.build(root / "model.spec.json")
    # without the spec's checks, every block warns W-NO-CHECK; the generated checks sheet holds the raw checks
    assert {f.rule for f in rep.findings} == {"W-NO-CHECK"}
    wb = load(root / "model.xlsx")
    assert wb.sheetnames[-1] == "checks" and wb["checks"]["B5"].value == "sales_rows"


def test_model_without_any_check_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "m"
    modelfolder.init_model(root, name="tiny")
    spec = {"rotm_spec": 1, "name": "tiny", "title": "Tiny", "sheets": [
        {"name": "inputs", "type": "inputs", "items": [{"id": "a", "label": "A", "value": 1}]},
        {"name": "calc", "type": "calc", "blocks": [{"id": "x", "rows": {"values": ["one"]}, "cell": "{in:a}"}]}]}
    common.write_json(root / "model.spec.json", spec)
    with pytest.raises(common.Refusal) as ei:
        build.build(root / "model.spec.json")
    assert "the model has no check" in refusal_lines(ei.value)


def test_model_without_raw_tables_builds(tmp_path: Path) -> None:
    root = tmp_path / "m"
    modelfolder.init_model(root, name="tiny")
    spec = {"rotm_spec": 1, "name": "tiny", "title": "Tiny", "sheets": [
        {"name": "inputs", "type": "inputs", "items": [{"id": "a", "label": "A", "value": 1, "unit": "count"},
                                                       {"id": "b", "label": "B", "value": 0, "unit": "count"}]},
        {"name": "calc", "type": "calc", "blocks": [
            {"id": "x", "title": "Sum", "rows": {"values": ["one"]}, "cell": "{in:a}+{in:b}", "key": True}]},
        {"name": "checks", "type": "checks", "checks": [
            {"id": "x_ok", "kind": "equal", "label": "x equals a plus b", "actual": "{cell:x}",
             "expected": "{in:a}+{in:b}"}]}]}
    common.write_json(root / "model.spec.json", spec)
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    wb = load(root / "model.xlsx")
    assert wb.sheetnames == ["cover", "inputs", "calc", "checks"]
    assert wb["calc"]["D6"].value == "=inputs!$D$6+inputs!$D$7"


def test_g_gcol_and_chk_placeholders(tmp_path: Path) -> None:
    spec = base_spec()
    for s in spec["sheets"]:
        if s["name"] == "calc":
            s["blocks"].append({"id": "gap", "title": "Actual minus target", "unit": "KRW thousand",
                                "rows": {"same_as": "actual"}, "cols": {"same_as": "actual"},
                                "cell": "{g:actual}-{g:tgt}", "total": {"row": True, "col": True}})
            s["blocks"].append({"id": "coltot", "title": "Column totals", "cols": {"same_as": "actual"}, "items": [
                {"id": "a", "label": "Actual", "unit": "KRW thousand", "expr": "SUM({gcol:actual})"},
                {"id": "ok", "label": "Tie check holds", "unit": "flag", "format": "flag",
                 "expr": "1-{chk:actual_ties_raw}"}]})
    chk = spec["sheets"][-2]["checks"]
    chk.append({"id": "gap_ties", "kind": "equal", "label": "Gap total equals actual minus target",
                "actual": "{blk:gap.total}", "expected": "{blk:actual.total}-{blk:tgt.total}"})
    chk.append({"id": "coltot_ties", "kind": "equal", "label": "Column totals equal the actual total",
                "actual": "SUM({blk:coltot})", "expected": "{blk:actual.total}+COLUMNS({blk:actual})"})
    root = make_model(tmp_path / "m", spec=spec)
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    ws = load(root / "model.xlsx")["calc"]
    assert ws["D29"].value == "=D6-D14" and ws["E30"].value == "=E7-E15"          # {g:} same row and column
    assert ws["D37"].value == "=SUM(D$6:D$9)" and ws["I37"].value == "=SUM(I$6:I$9)"   # {gcol:}
    assert ws["D38"].value == "=1-checks!$I$14"                     # {chk:} (v2: sales_beyond is row 8)


def test_resolve_expr_public_function(built) -> None:
    root, _ = built
    from lib import spec as specmod
    s = specmod.load_spec(root / "model.spec.json")
    result = build.compile_spec(s)
    ctx = build.RefContext(sheet="calc", row=6, col=4, block="actual", kind="grid", ri=0, ci=0)
    assert build.resolve_expr("SUM({blk:tgt})+{in:scale}", ctx, result.layout) == \
        "=SUM($D$14:$I$17)+inputs!$D$7"
    with pytest.raises(common.Refusal):
        build.resolve_expr("{blk:nope}", ctx, result.layout)


def test_cli_build_exit_codes(tmp_path: Path) -> None:
    root = make_model(tmp_path / "m")
    cmd = [sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), "build", str(root), "--strict",
           "--json", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert proc.returncode == 0, proc.stderr
    rep = json.loads(proc.stdout)
    assert rep["status"] == "pass" and rep["command"] == "build" and schemas.validate(rep, "report") == []


# ----------------------------------------------------------------------------- Excel (T3 for the fixture)

def _python_expected() -> dict:
    rows = sales_rows()
    actual: dict[tuple[str, int], float] = defaultdict(float)
    for r in rows:
        actual[(r["region"], int(r["inv_date"][5:7]))] += float(r["amount"]) / 1000
    return actual


@pytest.mark.excel
def test_recalculated_in_excel_every_check_holds_and_values_tie(tmp_path: Path) -> None:
    from lib import excel
    root = make_model(tmp_path / "m")
    build.build(root / "model.spec.json", strict=True)
    res = excel.recalc(root / "model.xlsx", read=["checks!C2", "checks!I5:I17", "calc!B6:I9", "summary!F11",
                                                 "checks!I21"])
    assert res.ok, res.errors
    assert res.error_count == 0, res.error_cells
    assert res.values["checks!C2"] == 0
    assert all(v[0] == 0 for v in res.values["checks!I5:I17"])
    expected = _python_expected()
    for row in res.values["calc!B6:I9"]:
        region = row[0]
        for k, v in enumerate(row[2:]):
            assert v == pytest.approx(expected[(region, k + 1)], abs=1e-9), (region, k + 1)
    total_act = sum(expected.values())
    total_tgt = sum(sum(r) for r in base_spec()["sheets"][0]["items"][3]["values"])
    assert res.values["summary!F11"] == pytest.approx(total_act / total_tgt - 1, abs=1e-12)
    assert res.values["checks!I21"] == 0                           # the alert holds too
    assert res.pid is not None and not excel.pid_alive(res.pid)


@pytest.mark.excel
def test_power_query_landing_survives_more_and_fewer_rows(tmp_path: Path) -> None:
    """F27 base: the raw sheet is a Power Query table over raw/sales.csv; the snapshot is replaced by more rows,
    refreshed, then by fewer rows, refreshed. The cover check reports the uncovered rows, the beyond check (v2) the
    helper rows below the raw rows; no error value appears."""
    from lib import excel
    spec = base_spec()
    for s in spec["sheets"]:
        if s["type"] == "raw":
            s["landing"] = "power_query"
    c = base_contract()
    c["expect"] = {"rows": {"min": 1, "max": 1000}}
    root = make_model(tmp_path / "m", spec=spec, contract=c)
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    assert rep.data["power_query_attached"] == ["sales"]
    data = manifest.load_manifest(root / "model.xlsx")
    assert manifest.raw_sheet(data, "sales")["landing"] == "power_query"
    rows = sales_rows()
    more = rows + [dict(r, invoice_id=f"9{r['invoice_id'][1:]}") for r in rows[:10]]
    fewer = rows[:50]
    cover = next(c["result"] for c in data["checks"] if c["id"] == "sales_cover")     # checks!I<r>
    beyond = next(c["result"] for c in data["checks"] if c["id"] == "sales_beyond")
    for extra, expect_cover, expect_beyond in ((more, 1, 0), (fewer, 0, 1)):
        src = tmp_path / f"in_{len(extra)}.csv"
        with open(src, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
            w.writeheader()
            w.writerows(extra)
        lrep = land.land_csv(root, "sales", src, force=True)
        assert lrep.exit_code == 0, [x.line() for x in lrep.findings]
        res = excel.pq_refresh(root / "model.xlsx")
        assert res.ok, res.errors
        actual = cover.replace("!I", "!D")
        b_actual = beyond.replace("!I", "!D")
        out = excel.recalc(root / "model.xlsx", read=["checks!C2", "checks!D5", actual, cover, b_actual, beyond])
        assert out.ok and out.error_count == 0, out.error_cells
        assert out.values["checks!D5"] == len(extra)
        assert out.values[cover] == expect_cover            # sales_cover: 10 rows not covered, then none
        assert out.values[actual] == (10 if expect_cover else -22)
        # v2 (A-aera-6): 22 helper rows below the 51st sheet row fail sales_beyond until extend clears them
        assert out.values[beyond] == expect_beyond and out.values[b_actual] == (22 if expect_beyond else 0)
        assert out.values["checks!C2"] == 1                 # one of the two checks fails each time

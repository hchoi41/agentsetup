"""Tests of the v3 changes of package P5 (lift, the specification, the function catalogue). Owner: P5 (v3).

Entries: A-aera-7 (a null flag for a column with agreed known nulls), A-aera-13 and J-5 (PMT, PV, FV and NPV in
the lift evaluator; LIFT-UNSUPPORTED before the script is written; the control total of the snapshot lift reads);
supporting A-aera-8 and C-sol-6 (lift on the zero-row layout). Tests marked excel drive Excel through lib.excel.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from lib import build, check, common, controls, land, lift, manifest, modelfolder, spec as specmod
from lib.lift import BLANK, Rng, XLErr, cell_value
from test_build import agree

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
ROTM = SCRIPTS / "rotm.py"
STARTER = SKILL / "templates" / "starter"
DATA = Path(__file__).resolve().parent / "data" / "lift"
if str(DATA) not in sys.path:
    sys.path.insert(0, str(DATA))
import fixture  # noqa: E402


# ----------------------------------------------------------------------------- helpers

def _rows(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _write_rows(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return path


def _number(d: Decimal):
    return int(d) if d == d.to_integral_value() else float(d)


def _total(rows: list[dict]):
    return _number(sum((Decimal(r["amount"]) for r in rows if r["amount"] != ""), Decimal(0)))


def _sheet(spec: dict, name: str) -> dict:
    return next(s for s in spec["sheets"] if s.get("name") == name or s.get("type") == name)


def _starter(root: Path, *, rows: list[dict] | None = None, contract_edit=None, spec_edit=None) -> Path:
    """A copy of the starter template (rotm.py init --template starter), its contract agreed in a session with
    the control total of `rows`, the rows landed, and the specification changed by `spec_edit`."""
    modelfolder.init_model(root, name="starter_v3", template="starter")
    rows = rows if rows is not None else _rows(STARTER / "data" / "lines.csv")
    path = root / "contracts" / "lines.contract.json"
    c = common.read_json(path)
    c.pop("shown_sha256", None)
    c["expect"]["totals"][0]["value"] = _total(rows)
    if contract_edit:
        contract_edit(c)
    common.write_json(path, agree(c))
    src = _write_rows(root.parent / f"{root.name}_extract.csv", rows)
    rep = land.land_csv(root, "lines", src)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    if spec_edit:
        spec = common.read_json(root / "model.spec.json")
        spec_edit(spec)
        common.write_json(root / "model.spec.json", spec)
    return root


def _load_script(path: Path):
    s = importlib.util.spec_from_file_location(f"lifted_v3_{abs(hash(str(path)))}", path)
    mod = importlib.util.module_from_spec(s)
    s.loader.exec_module(mod)
    return mod


def _script(root: Path) -> Path:
    return root / "lift" / "starter_lift.py"


def _errors(rep) -> list[str]:
    return [f.line() for f in rep.findings if f.severity == "error"]


def ev(expr: str):
    """Translate a literal-only spec expression and compute it as one cell."""
    code = lift.translate(expr, lift._Ctx("check", None))
    ns = dict(vars(lift))
    return cell_value(lambda: eval(code, ns))  # noqa: S307 - code written by the translator under test


def _ties(want, got) -> bool:
    """Excel's value against the script's: equal errors, or numbers within a relative 1e-9."""
    status, _d = lift.compare_value(want, got, 0)
    if isinstance(want, (int, float)) and isinstance(got, (int, float)) and not isinstance(got, bool):
        return abs(float(got) - float(want)) <= 1e-9 * max(1.0, abs(float(want)))
    return status == "tie"


# ============================================================================= A. A-aera-7

def _known_null_contract(c: dict) -> None:
    amount = next(col for col in c["columns"] if col["name"] == "amount")
    amount["null_rule"] = "never"
    amount["known_nulls"] = {"max": 1, "note": "one line agreed as known without an amount"}


def _known_null_rows() -> list[dict]:
    rows = _rows(STARTER / "data" / "lines.csv")
    rows[16]["amount"] = ""                        # line L0017: the one agreed known null
    return rows


def _flag_blocks(spec: dict) -> None:
    calc = _sheet(spec, "calc")
    calc["blocks"] += [
        {"id": "n_flag", "title": "Lines with an amount (null flag)", "unit": "lines", "format": "count",
         "rows": {"values": ["all"]}, "cell": "COUNTIFS({h:lines.amount_null},0)", "key": True},
        {"id": "n_raw", "title": "Lines with an amount (raw)", "unit": "lines", "format": "count",
         "rows": {"values": ["all"]}, "cell": "COUNT({raw:lines.amount})", "key": True},
        {"id": "avg", "title": "Average amount of the lines with an amount", "unit": "KRW", "format": "amount2",
         "rows": {"values": ["all"]}, "cell": "AVERAGEIFS({h:lines.amount},{h:lines.amount_null},0)", "key": True}]
    _sheet(spec, "checks")["checks"] += [
        {"id": "count_ties", "kind": "equal", "label": "COUNTIFS through the flag equals COUNT of the raw column",
         "actual": "{cell:n_flag}", "expected": "{cell:n_raw}"},
        {"id": "avg_ties", "kind": "equal", "label": "AVERAGEIFS through the flag equals AVERAGE of the raw column",
         "actual": "{cell:avg}", "expected": "AVERAGE({raw:lines.amount})"}]


def _known_null_model(root: Path) -> Path:
    return _starter(root, rows=_known_null_rows(), contract_edit=_known_null_contract, spec_edit=_flag_blocks)


def _filled() -> list[Decimal]:
    return [Decimal(r["amount"]) for r in _known_null_rows() if r["amount"] != ""]


def test_null_flag_columns_include_agreed_known_nulls() -> None:
    contract = {"columns": [
        {"name": "id", "type": "text", "null_rule": "never"},
        {"name": "amount", "type": "number", "null_rule": "never", "known_nulls": {"max": 1, "note": "known"}},
        {"name": "qty", "type": "integer", "null_rule": "allowed"},
        {"name": "paid_on", "type": "date", "null_rule": "never"},
        {"name": "memo", "type": "text", "null_rule": "never", "known_nulls": {"max": 2, "note": "known"}}]}
    fetched = ["id", "amount", "qty", "paid_on", "memo"]
    assert specmod.null_flag_columns(contract, fetched) == [("amount_null", "amount"), ("qty_null", "qty")]
    assert specmod.null_flag_columns(contract, ["qty", "amount"]) == [("qty_null", "qty"), ("amount_null", "amount")]
    assert specmod.may_be_empty(contract["columns"][1]) and not specmod.may_be_empty(contract["columns"][3])


def test_a_helper_id_equal_to_a_known_null_flag_is_refused(tmp_path: Path) -> None:
    def edit(spec: dict) -> None:
        _sheet(spec, "h_lines")["columns"].append({"id": "amount_null", "expr": "{this:amount}*0"})
    root = _starter(tmp_path / "m", rows=_known_null_rows(), contract_edit=_known_null_contract, spec_edit=edit)
    with pytest.raises(common.Refusal) as ei:
        specmod.load_spec(root / "model.spec.json")
    text = "\n".join([ei.value.message] + list(ei.value.details))
    assert "helper column id 'amount_null' is already the null flag build adds for a raw column that may be " \
           "empty" in text


def test_known_null_flag_is_built_and_lift_computes_it(tmp_path: Path) -> None:
    root = _known_null_model(tmp_path / "m")
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    h = manifest.helper_sheet(manifest.load_manifest(root / "model.xlsx"), "lines")
    flag = {c["id"]: c for c in h["columns"]}["amount_null"]
    assert flag["kind"] == "nullflag" and flag["raw_column"] == "amount" and flag["header"] == "amount empty"
    assert flag["formula"] == '=--(INDEX(raw_lines!$D:$D,A2)="")'
    rep = lift.lift_model(root)
    assert rep.exit_code == 0 and rep.data["not_reproduced"] == 0, [f.line() for f in rep.findings]
    mod = _load_script(_script(root))
    m, _values = mod.run_model(root)
    assert m.h["lines"].cols["amount_null"] == [int(r["amount"] == "") for r in _known_null_rows()]
    filled = _filled()
    assert m.blocks["n_flag"].values[0][0] == len(filled) == 59
    assert m.blocks["n_raw"].values[0][0] == 59
    assert m.blocks["avg"].values[0][0] == pytest.approx(float(sum(filled) / len(filled)), rel=1e-12)
    assert m.checks["count_ties"]["result"] == 0 and m.checks["avg_ties"]["result"] == 0
    assert m.checks["lines_nulls_amount"]["cells"]["D"] == 1 and m.checks["lines_nulls_amount"]["result"] == 0
    assert m.master == 0


@pytest.mark.excel
def test_known_null_flag_averageifs_and_countifs(tmp_path: Path) -> None:
    root = _known_null_model(tmp_path / "m")
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    rep = check.check_model(root, strict=True)
    assert rep.exit_code == 0, [f.line() for f in rep.findings] + rep.notes
    by = {c["id"]: c for c in rep.data["checks"]}
    filled = _filled()
    assert by["count_ties"]["actual"] == by["count_ties"]["expected"] == len(filled) == 59
    assert by["avg_ties"]["actual"] == pytest.approx(float(sum(filled) / len(filled)), rel=1e-12)
    assert by["avg_ties"]["state"] == "pass" and rep.data["master"]["value"] == 0
    import openpyxl
    man = manifest.load_manifest(root / "model.xlsx")
    ws = openpyxl.load_workbook(root / "model.xlsx", data_only=True)
    for key, want in (("n_flag", len(filled)), ("avg", float(sum(filled) / len(filled)))):
        sheet, cell = common.parse_ref(man["cells"][key])
        assert ws[sheet][cell].value == pytest.approx(want, rel=1e-12), key
    report = lift.lift_model(root, run=True)
    assert report.exit_code == 0, [f.line() for f in report.findings] + report.notes
    assert report.data["tie_out"]["status"] == "tie" and report.data["tie_out"]["counts"]["differ"] == 0


# ============================================================================= B. A-aera-13: PMT, PV, FV, NPV

# Values Excel 16 gave on the build PC on 2026-11-25 (BUILD/v3/P5/probe_fin.py and probe_fin2.py): the edge
# cases of the rules stated beside the runtime functions in lib/lift.py.
EXCEL_MEASURED = {
    "PMT(0,0,100)": "#NUM!", "PMT(0.05,0,100)": "#NUM!", "PMT(-1,10,1000)": "#NUM!", "PMT(-2,3,100)": "#NUM!",
    "PMT(-1.0000001,2,100)": "#NUM!", "PMT(-0.999,2,100)": -9.990009990010012e-05,
    "PMT(0.05,10,1000,0,2)": -123.33769044329205, "PMT(0.05,10,1000,0,-0.5)": -123.33769044329205,
    "PMT(0.05,10,-1000,0,TRUE)": 123.33769044329205, 'PMT(0.05,10,1000,0,"1")': -123.33769044329205,
    "PMT(0.05,10,1000,,1)": -123.33769044329205, "PMT(0.05,-10,1000)": 79.50457496545668,
    "PMT(0.05,10.5,1000)": -124.72497796698542, 'PMT(0.05,10,"1000")': -129.50457496545667,
    "PMT(0.05,10,TRUE)": -0.12950457496545667, "PMT(0,10,1000,100,1)": -110, 'PMT("x",10,1000)': "#VALUE!",
    "PMT(-0.5,2.5,100)": -10.736861692729645, "PV(0,0,100)": 0, "PV(0.05,0,100)": 0, "PV(-1,10,100)": "#DIV/0!",
    "PV(-1,0,100)": "#NUM!", "PV(-1.5,0,100)": 0, "PV(-1.5,2.5,100)": "#NUM!", "PV(-2,3,100)": 100,
    "PV(-3,2,100,10,1)": -52.5, "PV(0.05,10,100,0,3)": -810.7821675644054, "PV(0.05,-10,100)": 1257.789253554883,
    "PV(0,10,100,50,1)": -1050, "FV(0,0,100)": 0, "FV(-1,10,100)": -100, "FV(-1,10,100,0,1)": 0,
    "FV(-1,0,100)": "#NUM!", "FV(-2,0.5,100)": "#NUM!", "FV(-3,2,100,10,1)": -240,
    "FV(0.05,10,-100,,-2)": 1320.6787162326273, "FV(0.05,0,100,50)": -50, "FV(0,10,100,50,1)": -1050,
    "NPV(0.1,100,200)": 256.198347107438, "NPV(0,1,2)": 3, "NPV(-1,100)": "#DIV/0!", "NPV(-2,100,200)": 100,
    "NPV(0.1,TRUE,100)": 83.55371900826445, 'NPV(0.1,"50",100)': 128.099173553719,
    "NPV(0.1,{1,2,3})": 4.815927873779113, 'NPV(0.1,{1,"a",TRUE,3})': 3.388429752066115,
    "NPV(0.1,1/0,100)": "#DIV/0!", "NPV(-0.01,100,200)": 305.0709111315172,
}


def test_financial_functions_follow_the_rules_measured_in_excel() -> None:
    assert {"PMT", "PV", "FV", "NPV"} <= lift.supported_functions()
    wrong = [f"{e}: Excel {want!r}, script {ev(e)!r}" for e, want in EXCEL_MEASURED.items() if not _ties(want, ev(e))]
    assert not wrong, wrong
    # NPV over a range: only numbers take a period (text, TRUE and empty cells do not), as in Excel
    col = Rng.column(["hdr", 100, BLANK, "text", True, 300], tall=True)
    assert lift.NPV(0.1, col) == pytest.approx(338.8429752066115, rel=1e-12)
    assert lift.NPV(0.1, Rng.column([100, BLANK]), BLANK, 100) == pytest.approx(173.55371900826444, rel=1e-12)
    assert isinstance(cell_value(lambda: lift.PMT(-1, 10, 1000)), XLErr)


RATES = ("0", "0.05/12", "-0.01")
TIMINGS = (0, 1)
NPV_CELLS = ["hdr", 100, None, "text", True, 300, -50, 0.5]


def _financial_battery() -> list[str]:
    out = []
    for r in RATES:
        for t in TIMINGS:
            out += [f"PMT({r},36,100000,0,{t})", f"PMT({r},36,100000,5000,{t})", f"PMT({r},7.5,-2500,0,{t})",
                    f"PV({r},36,-2500,0,{t})", f"PV({r},36,-2500,1000,{t})", f"PV({r},-6,100,0,{t})",
                    f"FV({r},36,-2500,-10000,{t})", f"FV({r},36,-2500,0,{t})", f"FV({r},12.25,100,50,{t})"]
        out += [f"NPV({r},100,200,300)", f"NPV({r},-1000,300,400,500)", f"NPV({r},{{100,200;300,400}})"]
    return out + list(EXCEL_MEASURED)


@pytest.mark.excel
def test_financial_functions_equal_excel(tmp_path: Path) -> None:
    import openpyxl

    from lib import excel, formula
    battery = _financial_battery()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "fin"
    for k, v in enumerate(NPV_CELLS, start=1):
        if v is not None:
            ws.cell(k, 2, v)
    for k, expr in enumerate(battery, start=1):
        ws.cell(k, 1, formula.to_file_formula("=" + expr))
    ranges = [f"NPV({r},B1:B{len(NPV_CELLS)})" for r in RATES] + [f"NPV({r},B2:B3,C1,100)" for r in RATES]
    for k, expr in enumerate(ranges, start=1):
        ws.cell(k, 4, formula.to_file_formula("=" + expr))
    path = tmp_path / "fin.xlsx"
    wb.save(path)
    refs = [f"fin!A{k}" for k in range(1, len(battery) + 1)] + [f"fin!D{k}" for k in range(1, len(ranges) + 1)]
    res = excel.recalc(path, save=False, read=refs)
    assert res.ok, res.errors
    problems = []
    for k, expr in enumerate(battery, start=1):
        want, got = res.values[f"fin!A{k}"], ev(expr)
        if not _ties(want, got):
            problems.append(f"{expr}: Excel {want!r}, script {got!r}")
    col = Rng.column([BLANK if v is None else v for v in NPV_CELLS], tall=True)
    for k, expr in enumerate(ranges, start=1):
        rate = ev(RATES[(k - 1) % len(RATES)])
        got = cell_value(lift.NPV, rate, col) if k <= len(RATES) else \
            cell_value(lift.NPV, rate, Rng.column([100, BLANK]), BLANK, 100)
        want = res.values[f"fin!D{k}"]
        if not _ties(want, got):
            problems.append(f"{expr}: Excel {want!r}, script {got!r}")
    assert not problems, problems
    assert len(battery) >= 3 * 2 * 9 + 3 * 3


def _financial_helpers(spec: dict) -> None:
    _sheet(spec, "inputs")["items"] += [
        {"id": "rate", "label": "Rate per month", "value": 0.05 / 12, "type": "number", "format": "rate2",
         "unit": "rate per month", "source": "test"},
        {"id": "periods", "label": "Months", "value": 12, "type": "number", "format": "integer", "unit": "months",
         "source": "test"}]
    _sheet(spec, "h_lines")["columns"] += [
        {"id": "payment", "label": "Payment over one period at rate 0", "expr": "PMT(0,1,{this:amount})",
         "format": "amount"},
        {"id": "pmt_due", "label": "Monthly payment, paid at the start", "expr":
         "PMT({in:rate},{in:periods},{this:amount},0,1)", "format": "amount2"},
        {"id": "pv_end", "label": "Present value of those payments", "expr": "PV({in:rate},{in:periods},{this:pmt_due})",
         "format": "amount2"},
        {"id": "fv_due", "label": "Future value", "expr": "FV({in:rate},{in:periods},{this:pmt_due},{this:amount},1)",
         "format": "amount2"}]
    _sheet(spec, "calc")["blocks"].append(
        {"id": "npv", "title": "Net present value of the amounts", "unit": "KRW", "format": "amount2",
         "rows": {"values": ["all"]}, "cell": "NPV({in:rate},{h:lines.amount})"})
    spec["settings"] = dict(spec.get("settings") or {}, allow_functions=["NPV"])


@pytest.mark.excel
def test_pmt_helper_ties_out(tmp_path: Path) -> None:
    root = _starter(tmp_path / "m", spec_edit=_financial_helpers)
    rep = build.build(root / "model.spec.json")
    assert not _errors(rep), [f.line() for f in rep.findings]
    report = lift.lift_model(root, run=True)
    assert report.exit_code == 0, [f.line() for f in report.findings] + report.notes
    assert not [f for f in report.findings if f.rule == "LIFT-UNSUPPORTED"]
    tie = report.data["tie_out"]
    assert tie["status"] == "tie" and tie["counts"]["differ"] == 0 and tie["counts"]["not reproduced"] == 0
    steps = {s["step"]: s for s in tie["steps"]}
    for name in ("h_lines.payment", "h_lines.pmt_due", "h_lines.pv_end", "h_lines.fv_due"):
        assert steps[name]["cells"] == 60 and steps[name]["tie"] == 60, steps[name]
    assert steps["calc.npv"]["tie"] == steps["calc.npv"]["cells"] >= 1
    m, _ = _load_script(_script(root)).run_model(root)
    amounts = [float(r["amount"]) for r in _rows(root / "raw" / "lines.csv")]
    assert m.h["lines"].cols["payment"] == pytest.approx([-a for a in amounts])


# ============================================================================= B. A-aera-13: LIFT-UNSUPPORTED

def _unsupported_fixture(tmp_path: Path) -> dict:
    """The lift fixture with LET, MEDIAN and MROUND written into columns and an item its manifest places."""
    p = fixture.write_model(tmp_path / "model")
    data = json.loads(p["spec"].read_text(encoding="utf-8"))
    h = next(s for s in data["sheets"] if s.get("type") == "helpers")
    cols = {c["id"]: c for c in h["columns"]}
    cols["amount_k"]["expr"] = "LET(x,{this:amount},x/{in:scale})"
    cols["big"]["expr"] = "IF(MEDIAN({this:amount},{in:threshold})>{in:threshold},1,0)"
    cols["focus_no"]["expr"] = "MROUND({this:amount},{in:scale})+MEDIAN({this:amount},0)"
    bridge = next(b for s in data["sheets"] for b in s.get("blocks", []) if b["id"] == "bridge")
    next(i for i in bridge["items"] if i["id"] == "var")["expr"] = "MEDIAN({r:act},{r:tgt})"
    p["spec"].write_text(json.dumps(data), encoding="utf-8")
    return p


def test_unsupported_function_is_named_before_the_tie_out(tmp_path: Path) -> None:
    p = _unsupported_fixture(tmp_path)
    report = lift.lift_model(p["root"])
    assert report.exit_code == 1
    found = [f for f in report.findings if f.rule == "LIFT-UNSUPPORTED"]
    assert report.findings[:len(found)] == found                       # named first, before any other finding
    man = json.loads(p["manifest"].read_text(encoding="utf-8"))
    h = next(s for s in man["sheets"] if s.get("type") == "helpers")
    col = {c["id"]: c["col"] for c in h["columns"]}
    n = h["last_row"] - h["first_row"] + 1
    by = {f.data["function"]: f for f in found}
    assert list(by) == ["LET", "MEDIAN", "MROUND"]                     # in the order first used
    assert by["LET"].message == (f"the lift evaluator does not reproduce LET: {n:,} cell(s), first "
                                 f"h_sales!{col['amount_k']}{h['first_row']}")
    # MEDIAN: two helper columns of n rows each and the item 'var' of block bridge (body D22:F25, third row)
    assert by["MEDIAN"].data["cells"] == 2 * n + 3 and by["MEDIAN"].cell == f"{col['big']}{h['first_row']}"
    assert by["MEDIAN"].data["steps"] == ["h_sales.big", "h_sales.focus_no", "calc.bridge"]
    assert by["MROUND"].data["cells"] == n and by["MROUND"].sheet == "h_sales"
    assert all(f.severity == "error" and "references/functions.md" in f.fix for f in found)
    assert "change the model, or reproduce these cells by hand after the lift" in found[0].fix
    # the steps these functions stop are not reported a second time as L-NOT-REPRODUCED
    assert not [f for f in report.findings if f.rule == "L-NOT-REPRODUCED"]
    assert [u["function"] for u in report.data["unsupported"]] == ["LET", "MEDIAN", "MROUND"]
    script = p["root"] / "lift" / "fixture_sales_lift.py"
    text = script.read_text(encoding="utf-8")
    compile(text, str(script), "exec")                                  # still written, for a hand completion
    assert "h_sales.amount_k: LET is not translated" in text
    # without Excel, `lift --run` cannot tie out, and still exits 1 with the finding (not 3, not verified)
    res = subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(ROTM), "lift", str(p["root"]), "--run",
                          "--json", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                         timeout=600, env=dict(os.environ, ROTM_NO_EXCEL="1"))
    assert res.returncode == 1, res.stdout[-2000:] + res.stderr[-2000:]
    rules = [f["rule"] for f in json.loads(res.stdout)["findings"]]
    assert rules[:3] == ["LIFT-UNSUPPORTED"] * 3


def test_every_function_of_the_shipped_models_is_reproduced(skill_dir: Path) -> None:
    roots = [skill_dir / "examples" / "sales_vs_target", skill_dir / "templates" / "starter"]
    roots += sorted(p for p in (skill_dir.parents[1] / "templates").glob("*") if (p / "model.spec.json").is_file())
    checked = 0
    for root in roots:
        if not (root / "model.manifest.json").is_file():
            continue
        sp = specmod.load_spec(root / "model.spec.json", require_agreed=False)
        man = json.loads((root / "model.manifest.json").read_text(encoding="utf-8"))
        assert lift.unsupported_functions(sp, man) == [], root.name
        checked += 1
    assert checked >= 2


def test_a_position_dependent_row_is_still_not_reproduced(tmp_path: Path) -> None:
    p = fixture.write_model(tmp_path / "model")
    data = json.loads(p["spec"].read_text(encoding="utf-8"))
    h = next(s for s in data["sheets"] if s.get("type") == "helpers")
    h["columns"].append({"id": "where", "expr": "ROW({this:amount})"})
    p["spec"].write_text(json.dumps(data), encoding="utf-8")
    report = lift.lift_model(p["root"])
    assert [f.rule for f in report.findings] == ["L-NOT-REPRODUCED"]      # ROW is listed; ROW(ref) is not covered
    assert "ROW depends on the cell's position" in report.findings[0].message


# ============================================================================= B. J-5: control totals

def _types(root: Path) -> list[str]:
    return [c["type"] for c in common.read_json(root / "contracts" / "lines.contract.json")["columns"]]


def test_control_total_comes_from_the_controls_file_for_the_snapshot(tmp_path: Path) -> None:
    root = _starter(tmp_path / "m")
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    contract_total = _total(_rows(root / "raw" / "lines.csv"))
    digest = manifest.csv_digest(root / "raw" / "lines.csv", _types(root))[0]
    # a total accepted for another snapshot does not apply
    agreed = common.read_json(root / "contracts" / "lines.contract.json")["agreement"]["contract_sha256"]   # v9
    controls.record_acceptance(root, "lines", digest="sha256:" + "0" * 64, rows=61,
                               totals=[{"column": "amount", "value": 1, "tolerance": 0}], given=["lines.amount=1"],
                               contract_sha256=agreed)
    lift.lift_model(root)
    mod = _load_script(_script(root))
    assert mod.INFO["tables"]["lines"]["expect"]["totals"][0]["value"] == contract_total
    assert mod.INFO["tables"]["lines"]["snapshot_digest"] == digest
    # the total accepted for this snapshot does
    accepted = contract_total + 1250
    controls.record_acceptance(root, "lines", digest=digest, rows=60,
                               totals=[{"column": "amount", "value": accepted, "tolerance": 0}],
                               given=[f"lines.amount={accepted}"], contract_sha256=agreed)
    lift.lift_model(root)
    mod = _load_script(_script(root))
    total = mod.INFO["tables"]["lines"]["expect"]["totals"][0]
    assert total["value"] == accepted and "raw/lines.controls.json" in total["source"]
    m, _ = mod.run_model(root)
    assert m.checks["lines_total_amount"]["cells"]["E"] == accepted
    assert m.checks["lines_total_amount"]["cells"]["D"] == contract_total
    assert m.checks["lines_total_amount"]["result"] == 1                  # the raw rows do not reach it
    # the script says so when the manifest records another snapshot after the lift
    man = common.read_json(root / "model.manifest.json")
    next(s for s in man["sheets"] if s.get("type") == "raw")["digest"] = "sha256:" + "1" * 64
    common.write_json(root / "model.manifest.json", man)
    m, _ = mod.run_model(root)
    assert any("raw table 'lines'" in n and "run rotm.py lift again" in n for n in m.notes)


@pytest.mark.excel
def test_lift_ties_out_after_an_accepted_refresh_with_a_new_control(tmp_path: Path) -> None:
    def edit(spec: dict) -> None:
        _sheet(spec, "raw_lines")["landing"] = "power_query"
    root = _starter(tmp_path / "m", spec_edit=edit)
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 0 and rep.data.get("power_query_attached") == ["lines"], [f.line() for f in rep.findings]
    rows = _rows(root / "raw" / "lines.csv")
    more = rows + [dict(r, line_id=f"M{r['line_id'][1:]}") for r in rows[:7]]
    total = _total(more)
    _write_rows(root / "raw" / "lines.csv", more)          # the source changed; the query reads this file
    rep = check.check_model(root, refresh=True)
    assert rep.exit_code != 0 and "R-CHANGED" in [f.rule for f in rep.findings], [f.line() for f in rep.findings]
    rep = check.extend_helpers(root, table="lines")
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    text = f"lines.amount={total}"
    rep = check.check_model(root, accept_refresh=True, controls={"lines.amount": total}, control_texts=[text])
    assert rep.exit_code == 0, [f.line() for f in rep.findings] + rep.notes
    assert "R-ACCEPTED" in [f.rule for f in rep.findings]
    assert total != _total(rows)
    report = lift.lift_model(root, run=True)
    assert report.exit_code == 0, [f.line() for f in report.findings] + report.notes
    tie = report.data["tie_out"]
    assert tie["status"] == "tie" and tie["counts"]["differ"] == 0
    mod = _load_script(_script(root))
    assert mod.INFO["tables"]["lines"]["expect"]["totals"][0]["value"] == total
    m, _ = mod.run_model(root)
    assert m.checks["lines_total_amount"]["result"] == 0 and m.master == 0


# ============================================================================= C. A-aera-8, C-sol-6 (supporting)

def test_zero_row_layout_runs_and_compares_no_helper_cell(tmp_path: Path) -> None:
    """The zero-row layout of the v3 plan (5.1): helpers capacity 0, first_row 2, last_row 1; the raw CSV holds
    the header only. The checks rows, cover, beyond, keys and the total give 0; no helper cell is compared."""
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "k.csv").write_text("id,amount\n", encoding="utf-8")
    cols = {"row": "A", "in_raw": "B", "id": "C", "amount": "D", "amount_null": "E", "double": "F", "dup": "G"}
    kinds = {"row": "row", "in_raw": "flag", "id": "fetch", "amount": "fetch", "amount_null": "nullflag",
             "double": "derived", "dup": "dup"}
    checks = ["k_rows", "k_total_amount", "k_nulls_amount", "k_cover", "k_beyond", "k_keys"]
    layout = {"helpers": {"k": {"sheet": "h_k", "first_row": 2, "last_row": 1, "columns": {
                  c: {"id": c, "col": cols[c], "kind": kinds[c], **({"raw_column": "amount"} if c == "amount_null"
                                                                   else {})} for c in cols}}},
              "raw": {"k": {"sheet": "raw_k", "snapshot": "raw/k.csv"}},
              "blocks": {"b": {"id": "b", "body": "D6:D6", "sheet": "calc"}},
              "checks": {cid: {"id": cid, "result": f"checks!I{5 + i}"} for i, cid in enumerate(checks)},
              "inputs": {}, "cells": {}, "master": "checks!C2", "master_links": []}
    info = {"name": "zero", "inputs": {}, "tables": {"k": {
        "columns": ["id", "amount"], "types": ["text", "number"], "key": ["id"], "never_null": ["amount"],
        "expect": {"rows": {"min": 0, "max": 10}, "totals": [{"column": "amount", "value": 0, "tolerance": 0}]},
        "snapshot": "raw/k.csv"}}}
    m = lift.Model(info, layout)
    m.load_raw(tmp_path, "csv")
    h = m.helpers("k", ["id", "amount"], ["id"])
    h.compute("double", lambda r: r["amount"] * 2)
    b = m.grid("b", "calc", rows=["all"], cols=["Value"])
    b.fill(lambda i, j, row, col: lift.SUMIFS(m.hcol("k", "amount"), m.hcol("k", "amount_null"), 0)
           + lift.COUNTIFS(m.hcol("k", "in_raw"), 1))
    b.finish()
    m.raw_checks("k")
    m.master_check()
    assert h.n == 0 and all(v == [] for v in h.cols.values())
    for cid in checks:
        assert m.checks[cid]["result"] == 0, cid
    assert m.checks["k_cover"]["cells"]["D"] == 0 and m.checks["k_beyond"]["cells"]["D"] == 0
    assert m.checks["k_rows"]["cells"]["D"] == 0 and m.checks["k_total_amount"]["cells"]["D"] == 0
    assert m.blocks["b"].values == [[0]] and m.master == 0
    helper_steps = [s for s in m.steps if s["kind"] == "helper"]
    assert helper_steps and all(s["cells"] == [] for s in helper_steps)
    result = lift.tie_out(m, {"h_k": {(1, 1): "row"}}, 0.001)
    assert sum(s["cells"] for s in result["steps"] if s["kind"] == "helper") == 0

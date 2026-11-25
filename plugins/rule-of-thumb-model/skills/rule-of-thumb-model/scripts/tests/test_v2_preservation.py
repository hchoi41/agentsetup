"""Tests of package P5 in round 2 (v2): the one preservation rule of theme, compat and split (A-aera-16), exact
typed values and a separate tolerance for calculated values (A-aera-4), split of an over-long + and - chain
(A-aera-13), Excel versions per function (A-aera-19) and raw number formats in theme (J-1). Owner: P5 (v2)."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib import audit, common, compat, diff, manifest, split, theme, workbook  # noqa: E402
from lib import functions as FN  # noqa: E402

ROTM = [sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py")]
LAMBDA_FAMILY = ("LAMBDA", "MAP", "BYROW", "BYCOL", "REDUCE", "SCAN", "MAKEARRAY", "ISOMITTED")


@pytest.fixture
def no_excel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")


def _rotm(*args: str, no_excel: bool = True) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if no_excel:
        env["ROTM_NO_EXCEL"] = "1"
    return subprocess.run([*ROTM, *args], capture_output=True, text=True, encoding="utf-8", env=env, timeout=600)


def _save(wb, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def _with_cached(path: Path, sheet_xml: str, cached: dict[str, object]) -> Path:
    """Write cached values into formula cells of one sheet (openpyxl writes `<v />` for a formula), so a test can
    hold two workbooks whose calculated values differ without Excel."""
    tmp = path.with_suffix(".tmp.xlsx")
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == sheet_xml:
                text = data.decode("utf-8")
                for coord, value in cached.items():
                    text, n = re.subn(rf'(<c r="{coord}"[^>]*>)(<f>.*?</f>)<v\s*/>', rf"\g<1>\g<2><v>{value}</v>",
                                      text)
                    assert n == 1, coord
                data = text.encode("utf-8")
            zout.writestr(item, data)
    os.replace(tmp, path)
    return path


# ============================================================================ A-aera-19: versions per function

def test_lambda_family_lists_microsoft_365_and_excel_2024_only() -> None:
    for name in LAMBDA_FAMILY:
        assert FN.CATALOGUE[name].excel_versions == ("Microsoft 365", "Excel 2024"), name


def test_functions_excel_2021_has_list_it() -> None:
    for name in ("XLOOKUP", "XMATCH", "LET", "FILTER", "UNIQUE", "SORT", "SEQUENCE"):
        versions = FN.CATALOGUE[name].excel_versions
        assert versions[:3] == ("Microsoft 365", "Excel 2024", "Excel 2021"), name


def test_every_entry_has_its_own_version_list() -> None:
    for name, e in FN.CATALOGUE.items():
        if e.excel == "no":
            assert e.excel_versions == (), name
            continue
        assert e.excel_versions and e.excel_versions[0] == "Microsoft 365", name
        if e.excel == "yes":
            assert "Excel 2016" in e.excel_versions, name
        else:
            assert "Excel 2016" not in e.excel_versions, name
        # the availability text names the oldest version of the list (or says Microsoft 365 only)
        oldest = e.excel_versions[-1].replace("Excel ", "")
        assert oldest in e.availability or e.excel == "yes", (name, e.availability)


def test_compat_advice_separates_lambda_from_xlookup() -> None:
    sev, message, fix = compat._availability("LAMBDA", "excel")
    assert sev == "info"
    assert "2021" not in message and "Microsoft 365 or Excel 2024" in message, message
    sev, message, fix = compat._availability("XLOOKUP", "excel")
    assert "Excel 2021" in message and "Excel 2024" in message, message
    # the Google Sheets part stays separate: a not-confirmed function keeps its own warning
    sev, message, _fix = compat._availability("TAKE", "both")
    assert sev == "warning" and "not confirmed" in message


def test_compat_report_for_a_lambda_cell_does_not_name_excel_2021(tmp_path: Path) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    wb.active["A1"] = 3
    wb.active["B1"] = "=_xlfn.LAMBDA(_xlpm.x,_xlpm.x+1)(A1)"
    wb.active["C1"] = "=_xlfn.XLOOKUP(A1,A1:A1,A1:A1)"
    rep = compat.compat_workbook(_save(wb, tmp_path / "l.xlsx"), report_only=True, product="excel")
    lines = {f.cell: f.message for f in rep.findings if f.rule == "X-FUNC"}
    assert "2021" not in lines["B1"] and "Excel 2024" in lines["B1"]
    assert "Excel 2021" in lines["C1"]


def test_functions_md_shows_the_version_lists() -> None:
    text = (SKILL / "references" / "functions.md").read_text(encoding="utf-8")
    row = next(line for line in text.splitlines() if line.startswith("| LAMBDA |"))
    assert "Microsoft 365, Excel 2024 |" in row and "2021" not in row.split("|")[4]
    row = next(line for line in text.splitlines() if line.startswith("| XLOOKUP |"))
    assert "Microsoft 365, Excel 2024, Excel 2021" in row


# ============================================================================ A-aera-4: diff, exact typed values

def _inputs_pair(tmp_path: Path, old_value: float, new_value: float) -> tuple[Path, Path]:
    from openpyxl import Workbook
    out = []
    for name, v in (("old.xlsx", old_value), ("new.xlsx", new_value)):
        wb = Workbook()
        ws = wb.active
        ws.title = "Inputs"
        ws["A1"] = "control total"
        ws["A2"] = v
        out.append(_save(wb, tmp_path / name))
    return out[0], out[1]


def test_diff_reports_a_typed_change_of_500_in_a_trillion(tmp_path: Path) -> None:
    old, new = _inputs_pair(tmp_path, 1_000_000_000_000, 999_999_999_500)
    rep = diff.diff_workbooks(old, new)
    inputs = [f for f in rep.findings if f.rule == "D-INPUT"]
    assert [f.cell for f in inputs] == ["A2"], [f.line() for f in rep.findings]
    res = _rotm("diff", str(old), str(new), "--fail-on-change", "--json", "-")
    assert res.returncode == 1, res.stdout + res.stderr
    data = json.loads(res.stdout)
    assert data["data"]["counts"]["constants"] == 1


def _outputs_pair(tmp_path: Path, old_cached: float, new_cached: float) -> tuple[Path, Path]:
    from openpyxl import Workbook
    out = []
    for name, cached in (("old.xlsx", old_cached), ("new.xlsx", new_cached)):
        wb = Workbook()
        ws = wb.active
        ws.title = "Calc"
        ws["A1"] = 1
        ws["B1"] = "=A1*1"
        p = _save(wb, tmp_path / name)
        out.append(_with_cached(p, "xl/worksheets/sheet1.xml", {"B1": repr(cached)}))
    return out[0], out[1]


def test_diff_compares_outputs_within_the_calc_tolerance(tmp_path: Path) -> None:
    old, new = _outputs_pair(tmp_path, 1_000_000_000_000.0, 1_000_000_000_001.0)     # relative 1e-12
    rep = diff.diff_workbooks(old, new)
    assert rep.data["outputs_compared"] is True
    assert not [f for f in rep.findings if f.rule == "D-OUTPUT"], [f.line() for f in rep.findings]
    assert rep.data["calc_tolerance"] == workbook.DEFAULT_CALC_TOLERANCE
    assert any("relative tolerance of 1e-09" in n for n in rep.notes), rep.notes
    rep0 = diff.diff_workbooks(old, new, calc_tolerance=0)
    assert [f.cell for f in rep0.findings if f.rule == "D-OUTPUT"] == ["B1"]
    res = _rotm("diff", str(old), str(new), "--calc-tolerance", "0", "--fail-on-change", "--json", "-")
    assert res.returncode == 1, res.stdout + res.stderr
    data = json.loads(res.stdout)
    assert data["data"]["counts"]["outputs"] == 1 and data["data"]["calc_tolerance"] == 0
    res = _rotm("diff", str(old), str(new), "--fail-on-change")
    assert res.returncode == 0, res.stdout + res.stderr


def test_calc_tolerance_option_on_every_preserving_command() -> None:
    import argparse
    for add in (theme.add_theme_arguments, compat.add_compat_arguments, split.add_split_arguments,
                diff.add_diff_arguments):
        p = argparse.ArgumentParser()
        add(p)
        args = ["a.xlsx", "b.xlsx"] if add is diff.add_diff_arguments else (
            ["a.xlsx", "calc!B1"] if add is split.add_split_arguments else ["a.xlsx"])
        assert p.parse_args(args).calc_tolerance == workbook.DEFAULT_CALC_TOLERANCE
        assert p.parse_args([*args, "--calc-tolerance", "0"]).calc_tolerance == 0
    if not (SKILL.parents[1] / ".claude-plugin" / "plugin.json").is_file():
        return                                       # skill copied without its plugin: no command skills to read
    for name in ("theme", "compat", "split", "diff"):
        text = (SKILL.parents[1] / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        assert "--calc-tolerance" in text, name


# ============================================================================ A-aera-13: split of an over-long chain

def _terms(n: int) -> str:
    return "=" + "+".join(f"A{i}" for i in range(1, n + 1))


def _evaluate(text: str, env: dict[str, float]) -> float:
    expr = text[1:].replace("^", "**")
    expr = re.sub(r"\b([A-Z]+\d+|H\d{3})\b", lambda m: repr(env[m.group(1)]), expr)
    return eval(expr, {"__builtins__": {}}, {})  # noqa: S307 - test arithmetic on cell values only


def test_chain_terms_reads_the_top_level_chain() -> None:
    assert split.chain_terms("=A1-B1^2+SUM(C1:C3)-1E+5") == [("+", "A1"), ("-", "B1^2"), ("+", "SUM(C1:C3)"),
                                                            ("-", "1E+5")]
    assert split.chain_terms("=-A1+B1") == [("+", "-A1"), ("+", "B1")]
    assert split.chain_terms("=SUM(A1,A2)") is None                     # one term
    assert split.chain_terms('=A1+B1&"x"') is None                       # & binds looser than +
    assert split.chain_terms("=A1+B1>0") is None                         # a comparison at the top level
    assert split.chain_terms("=(A1+B1)") is None                         # the chain is inside parentheses


def test_length_split_keeps_every_sign() -> None:
    f = "=A1-B1^2+C1-D1-E1+F1-G1*2+H1"
    env = {"A1": 5.0, "B1": 3.0, "C1": 7.0, "D1": 11.0, "E1": 13.0, "F1": 17.0, "G1": 19.0, "H1": 23.0}
    plan = split.plan_length_split(f, length_limit=12, ref_length=4)
    names = {n.lower(): f"H{i + 1:03d}" for i, n in enumerate(plan.names)}
    host, helpers = plan.resolve(names)
    for text in [host, *helpers]:
        assert len(text) <= 12, text
    values = dict(env)
    for i, text in enumerate(helpers):
        values[f"H{i + 1:03d}"] = _evaluate(text, values)
    assert _evaluate(host, values) == _evaluate(f, env)
    assert len(plan.helpers) >= 3


def test_length_split_of_the_40_term_sum_is_within_the_limits() -> None:
    plan = split.plan_length_split(_terms(40), length_limit=100, sheet="Calc")
    names = {n.lower(): f"split_helpers!{common.a1(2 + i, 1)}" for i, n in enumerate(plan.names)}
    host, helpers = plan.resolve(names, host_refs=names)
    assert len(host) <= 100 and all(len(h) <= 100 for h in helpers)
    assert plan.kind == "length" and plan.depth_after <= 2


def test_over_long_formula_of_another_shape_is_refused_with_exit_1(tmp_path: Path, no_excel: None) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Calc"
    for i in range(1, 41):
        ws[f"A{i}"] = i
    ws["B1"] = "=SUM(" + ",".join(f"A{i}" for i in range(1, 41)) + ")"
    src = _save(wb, tmp_path / "long.xlsx")
    rep = split.split_cell(src, "Calc!B1")
    shape = [f for f in rep.findings if f.rule == "SPLIT-SHAPE"]
    assert rep.exit_code == 1 and shape and shape[0].severity == "error", [f.line() for f in rep.findings]
    assert "H10" in shape[0].fix and "SUM" in shape[0].message
    assert not (tmp_path / "long_split.xlsx").exists()
    res = _rotm("split", str(src), "Calc!B1")
    assert res.returncode == 1 and "SPLIT-SHAPE" in res.stdout + res.stderr


def test_over_long_chain_without_excel_is_written_and_not_verified(tmp_path: Path, no_excel: None) -> None:
    src = _sum_workbook(tmp_path / "sum40.xlsx")
    rep = split.split_cell(src, "Calc!B1")
    assert rep.data["written"] is True and rep.exit_code == common.EXIT_NOT_AVAILABLE
    info = workbook.open_workbook(Path(rep.data["output"]), with_values=False)
    from lib import formula as fx
    for s in info.sheets:
        for c in s.formulas():
            assert fx.analyze(c.formula).length <= 100 and fx.depth(c.formula) <= 2, (s.name, c.coord, c.formula)
    assert rep.data["kind"] == "length" and rep.data["length_before"] == 151


def _sum_workbook(path: Path) -> Path:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Calc"
    for i in range(1, 41):
        ws[f"A{i}"] = i * 1.5 if i % 2 else -i
    ws["B1"] = _terms(40)
    return _save(wb, path)


@pytest.mark.excel
def test_excel_40_term_sum_is_split_with_its_value_unchanged(tmp_path: Path) -> None:
    src = _sum_workbook(tmp_path / "sum40.xlsx")
    assert len(_terms(40)) == 151
    rep = split.split_cell(src, "Calc!B1")
    assert rep.exit_code == 0, [f.line() for f in rep.findings]
    assert rep.verified["recalculated"] is True and rep.data["written"] is True
    assert any(f.rule == "SPLIT-DONE" for f in rep.findings)
    out = Path(rep.data["output"])
    from lib import excel
    copy = tmp_path / "check.xlsx"
    shutil.copy2(out, copy)
    assert excel.recalc(copy, save=True).ok
    expected = sum((i * 1.5 if i % 2 else -i) for i in range(1, 41))
    assert workbook.open_workbook(copy).sheet("Calc").cell("B1").cached == pytest.approx(expected, abs=0)


# ============================================================================ J-1: theme --number-formats on raw cells

def _built_with_amount1(tmp_path: Path) -> Path:
    from test_build import base_contract, make_model
    from lib import build
    c = base_contract()
    for col in c["columns"]:
        if col["name"] == "amount":
            col["format"] = "amount1"
    root = make_model(tmp_path / "m", contract=c)
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    return root


def _changed_copy(root: Path, *, formats: bool = True) -> Path:
    import openpyxl
    wb = openpyxl.load_workbook(root / "model.xlsx")
    wb["raw_sales"]["F3"].number_format = "0"
    wb.save(root / "changed.xlsx")
    man = manifest.load_manifest(root / "model.xlsx") | {"workbook": "changed.xlsx"}
    if not formats:
        for s in man["sheets"]:
            s.pop("formats", None)
    common.write_json(root / "changed.manifest.json", man)
    return root / "changed.xlsx"


def test_theme_number_formats_writes_the_recorded_raw_format_back(tmp_path: Path, no_excel: None) -> None:
    root = _built_with_amount1(tmp_path)
    changed = _changed_copy(root)
    before = audit.audit_workbook(changed, strict=True, record=False)
    assert any(f.rule == "S-NUMFMT" and f.cell == "F3" for f in before.findings)
    rep = theme.theme_workbook(changed, out=root / "themed.xlsx", number_formats=True)
    assert rep.data["written"] is True, [f.line() for f in rep.findings]
    import openpyxl
    ws = openpyxl.load_workbook(root / "themed.xlsx")["raw_sales"]
    assert ws["F3"].number_format == '#,##0.0_);(#,##0.0);"-"_)'
    after = audit.audit_workbook(root / "themed.xlsx", strict=True, record=False)
    assert not [f for f in after.findings if f.rule == "S-NUMFMT"], [f.line() for f in after.findings]


def test_theme_number_formats_uses_the_type_default_when_no_key_is_recorded(tmp_path: Path, no_excel: None) -> None:
    from lib import dictionary
    root = _built_with_amount1(tmp_path)
    changed = _changed_copy(root, formats=False)
    rep = theme.theme_workbook(changed, out=root / "themed.xlsx", number_formats=True)
    assert rep.data["written"] is True
    import openpyxl
    d = dictionary.load_dictionary()
    ws = openpyxl.load_workbook(root / "themed.xlsx")["raw_sales"]
    assert ws["F3"].number_format == d.number_format(d.format_for_column_type("number"))


def test_theme_without_a_manifest_leaves_raw_number_formats(tmp_path: Path, no_excel: None) -> None:
    root = _built_with_amount1(tmp_path)
    changed = _changed_copy(root)
    loose = tmp_path / "loose" / "changed.xlsx"
    loose.parent.mkdir()
    shutil.copy2(changed, loose)
    rep = theme.theme_workbook(loose, out=loose.with_name("themed.xlsx"), number_formats=True)
    assert rep.data["written"] is True
    import openpyxl
    assert openpyxl.load_workbook(loose.with_name("themed.xlsx"))["raw_sales"]["F3"].number_format == "0"


# ============================================================================ A-aera-16: the one preservation rule

def _volatile_workbook(path: Path) -> Path:
    """RANDBETWEEN in calc!A1, two cells that depend on it, independent cells, one CONCATENATE for compat and one
    formula of depth 3 for split."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "calc"
    ws["A1"] = "=RANDBETWEEN(1,1000000000)"
    ws["A2"] = "=A1*2"
    ws["A3"] = "=A2+1"
    ws["B1"] = 7
    ws["B2"] = "=B1*3"
    ws["C1"] = '=CONCATENATE("a","b")'
    # v6 integrator (r5 verdict A-aera-10): the depth-3 formula has its deep part in the condition of IF, which split
    # moves as before; v6 refuses the earlier nested IF chain =IF(B1>0,IF(B1>1,IF(B1>2,1,2),3),4) with SPLIT-GUARD,
    # because that part lies in a branch of two IFs (test_split.py::test_plan_handles_nested_if_and_long_chains).
    # The value stays 1 for B1 = 7 and B1 = 8, as before, so the tests that change B1 see the same cells change.
    ws["D1"] = "=IF(ABS(ROUND(B1,0))>0,1,2)"
    return _save(wb, path)


def _statement_of(rep) -> dict:
    pres = rep.data.get("preservation")
    assert pres, rep.data.keys()
    assert pres["statement"] in rep.notes
    return pres


@pytest.mark.excel
def test_excel_theme_on_a_volatile_workbook_states_what_was_left_out(tmp_path: Path) -> None:
    src = _volatile_workbook(tmp_path / "vol.xlsx")
    rep = theme.theme_workbook(src, out=tmp_path / "vol_theme.xlsx")
    assert rep.exit_code == 0 and rep.data["written"], [f.line() for f in rep.findings]
    pres = _statement_of(rep)
    assert pres["values_left_out"] == 3 and pres["first_left_out"] == "calc!A1", pres
    assert pres["calc_tolerance"] == workbook.DEFAULT_CALC_TOLERANCE
    text = " ".join(rep.notes)
    assert "cached values were not compared" not in text and "every formula and value was compared" not in text
    assert not [f for f in rep.findings if f.rule == "T-VOLATILE"]


@pytest.mark.excel
def test_excel_theme_still_reports_a_changed_non_volatile_value(tmp_path: Path) -> None:
    from openpyxl import load_workbook
    src = _volatile_workbook(tmp_path / "vol.xlsx")
    candidate = tmp_path / "candidate.xlsx"
    wb = load_workbook(src)
    wb["calc"]["B1"] = 8
    wb.save(candidate)
    work = tmp_path / "work"
    work.mkdir()
    report = common.Report(command="theme", target=src.as_posix())
    ok, _path = theme._verify_by_recalc(src, candidate, work, report)
    assert ok is False
    cells = {f.cell for f in report.findings if f.rule == "T-UNINTENDED"}
    assert "B1" in cells and "B2" in cells and not cells & {"A1", "A2", "A3"}, cells


@pytest.mark.excel
def test_excel_compat_on_a_volatile_workbook_passes_and_states_the_count(tmp_path: Path) -> None:
    src = _volatile_workbook(tmp_path / "vol.xlsx")
    rep = compat.compat_workbook(src)
    assert rep.data["written"] is True and rep.verified["recalculated"] is True, [f.line() for f in rep.findings]
    assert not [f for f in rep.findings if f.rule == "W-UNINTENDED"]
    pres = _statement_of(rep)
    assert pres["values_left_out"] == 3 and pres["first_left_out"] == "calc!A1", pres


@pytest.mark.excel
def test_excel_split_on_a_volatile_workbook_passes_and_states_the_count(tmp_path: Path) -> None:
    src = _volatile_workbook(tmp_path / "vol.xlsx")
    rep = split.split_cell(src, "calc!D1", cell_only=True)
    assert rep.exit_code == 0 and rep.data["written"] is True, [f.line() for f in rep.findings]
    pres = _statement_of(rep)
    assert pres["values_left_out"] == 3 and pres["first_left_out"] == "calc!A1", pres


@pytest.mark.excel
def test_excel_write_edits_still_reports_a_changed_non_volatile_value(tmp_path: Path) -> None:
    src = _volatile_workbook(tmp_path / "vol.xlsx")
    report = common.Report(command="compat", target=src.as_posix())
    edits = [compat.CellEdit("calc", "C1", formula='="a"&"b"'), compat.CellEdit("calc", "B1", value=8)]
    intended = workbook.Intended(kind="cells", cells={("calc", "C1")})
    placed = compat.write_edits(src, tmp_path / "out.xlsx", edits, command="compat", report=report,
                                intended=intended)
    assert placed is False
    cells = {f.cell for f in report.findings if f.rule == "W-UNINTENDED"}
    assert "B1" in cells and "B2" in cells and not cells & {"A1", "A2", "A3"}, cells

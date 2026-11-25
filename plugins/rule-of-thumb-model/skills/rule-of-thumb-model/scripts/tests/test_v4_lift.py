"""Tests of the v4 changes of package P6 (lift and trace). Owner: P6 (v4).

Entries:
- [A-aera-6, J-2]: two numbers tie out when their difference is at most the model's check tolerance (absolute)
  plus REL_TOLERANCE times the larger value (double-precision rounding); each step's largest difference is taken
  over every compared cell, ties included, and printed always; --all prints and --json holds one record per
  compared cell; the script lift writes uses the same rule; the temporary folder of lift --run is removed with
  all its contents on every path out.
- [A-aera-13]: a fetch cell of a helper sheet is traced to the one raw cell it reads; a raw range read through a
  conditional sum or count stays, labelled as the rows that can contribute, with its criteria.
Tests marked excel drive Excel through lib.excel.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib import common, lift, trace

DATA = Path(__file__).resolve().parent / "data" / "lift"
if str(DATA) not in sys.path:
    sys.path.insert(0, str(DATA))
import fixture  # noqa: E402

SCRIPT_NAME = "fixture_sales_lift.py"


# ----------------------------------------------------------------------------- helpers

def _load_script(path: Path):
    s = importlib.util.spec_from_file_location(f"lifted_v4_{abs(hash(str(path)))}", path)
    mod = importlib.util.module_from_spec(s)
    s.loader.exec_module(mod)
    return mod


def _run_script(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-X", "utf8", "-B", str(path), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=600)


def _computed_values(root: Path) -> dict:
    """(sheet, coord) -> the script's own value for every compared cell (a stand-in for Excel's values)."""
    lift.lift_model(root)
    mod = _load_script(root / "lift" / SCRIPT_NAME)
    m, _values = mod.run_model(root)
    out = {}
    for step in m.steps:
        for r, c, v in step["cells"]:
            if step["kind"] == "input":
                continue
            out[(step["sheet"], common.a1(c, r))] = v.code if isinstance(v, mod.XLErr) else v
    return out


def _values_workbook(p: dict, dest: Path, values: dict) -> Path:
    """A copy of the fixture workbook whose formula cells hold `values`, with its manifest next to it."""
    wb = fixture.write_values_workbook(p["workbook"], dest, values)
    shutil.copy2(p["manifest"], dest.with_name(dest.stem + ".manifest.json"))
    return wb


def _rotm_lift_folders() -> list[str]:
    return sorted(x.name for x in Path(tempfile.gettempdir()).glob("rotm_lift_*") if x.is_dir())


# ============================================================================= [A-aera-6, J-2] the tolerance

def test_a_difference_of_500_at_a_trillion_differs(tmp_path: Path) -> None:
    """The reviewer's case: the script gives 1,000,000,000,000 in a helper column and the workbook (Excel) holds
    1,000,000,000,500. v3 allowed 1e-9 times the value (1,000 here) and called it a tie."""
    rows = fixture.sales_rows()
    rows[0][4] = 1_000_000_000_000
    p = fixture.write_model(tmp_path / "model", rows=rows)
    values = _computed_values(p["root"])
    assert values[("h_sales", "G2")] == 1_000_000_000_000
    values[("h_sales", "G2")] = 1_000_000_000_500
    wb = _values_workbook(p, tmp_path / "excel.xlsx", values)
    script = p["root"] / "lift" / SCRIPT_NAME
    res = _run_script(script, "--workbook", str(wb), "--json", str(tmp_path / "t.json"))
    assert res.returncode == 1, res.stdout[-3000:] + res.stderr[-2000:]
    result = json.loads((tmp_path / "t.json").read_text(encoding="utf-8"))
    differ = [c for c in result["cells"] if c["status"] == "differ"]
    assert [c["cell"] for c in differ] == ["h_sales!G2"]
    assert differ[0]["difference"] == -500                      # script minus workbook
    step = next(s for s in result["steps"] if s["step"] == "h_sales.amount")
    assert step["differ"] == 1 and step["max_difference"] == 500 and step["max_difference_cell"] == "h_sales!G2"
    assert result["max_difference"] == 500 and result["max_difference_cell"] == "h_sales!G2"
    assert result["tolerance"] == 0.001 and result["relative_tolerance"] == lift.REL_TOLERANCE == 1e-13
    header = (f"tie-out: {result['counts']['cells']:,} cells compared; tolerance 0.001 (absolute) plus 1e-13 times "
              "the larger value (double-precision rounding); largest difference 500 at h_sales!G2")
    assert header in res.stdout.splitlines()
    assert "h_sales!G2" in res.stdout and "differ" in res.stdout
    # the rule itself: absolute tolerance plus 1e-13 times the larger value
    assert lift.compare_value(1_000_000_000_500, 1_000_000_000_000, 0.001) == ("differ", -500.0)
    assert lift.compare_value(1_000_000_000_001, 1_000_000_000_000, 0.001)[0] == "differ"      # 1 at 1e12
    assert lift.compare_value(1_000_000_000_000.05, 1_000_000_000_000, 0.001)[0] == "tie"     # rounding: 0.1
    assert lift.compare_value(1000.0009, 1000, 0.001)[0] == "tie"
    assert lift.compare_value(1000.0011, 1000, 0.001)[0] == "differ"
    # the script lift writes carries the same rule (the runtime is copied into it)
    mod = _load_script(script)
    assert mod.REL_TOLERANCE == lift.REL_TOLERANCE
    assert mod.compare_value(1_000_000_000_500, 1_000_000_000_000, 0.001) == ("differ", -500.0)
    text = script.read_text(encoding="utf-8")
    assert "plus 1e-13 times the larger value" in text and "one record per compared cell" in text


def test_rotm_lift_report_states_the_rule_and_the_largest_difference(tmp_path: Path) -> None:
    """rotm.py lift --run: the tie-out note names the rule and the largest difference; L-DIFF names the largest
    difference of a differing cell."""
    rows = fixture.sales_rows()
    rows[0][4] = 1_000_000_000_000
    p = fixture.write_model(tmp_path / "model", rows=rows)
    values = _computed_values(p["root"])
    values[("h_sales", "G2")] = 1_000_000_000_500
    wb = fixture.write_values_workbook(p["workbook"], tmp_path / "excel.xlsx", values)
    shutil.move(str(wb), str(p["workbook"]))                   # the model's workbook now holds the values
    report = lift.lift_model(p["root"], run=True)
    assert report.exit_code == 1, [f.line() for f in report.findings] + report.notes
    tie = report.data["tie_out"]
    assert tie["status"] == "differ" and tie["max_difference"] == 500 and tie["max_difference_cell"] == "h_sales!G2"
    assert tie["relative_tolerance"] == 1e-13 and tie["tolerance"] == 0.001
    note = next(n for n in report.notes if n.startswith("tie-out:"))
    assert note.startswith(f"tie-out: {tie['counts']['tie']:,} of {tie['counts']['cells']:,} cells tie out; 1 differ")
    assert "(tolerance 0.001 absolute plus 1e-13 times the larger value; largest difference 500 at h_sales!G2)" in note
    diff = [f for f in report.findings if f.rule == "L-DIFF"]
    assert len(diff) == 1 and "largest difference of a differing cell 500" in diff[0].message
    assert diff[0].cell == "G2"


def _shipped_roots(skill_dir: Path, plugin_dir: Path | None) -> list[Path]:
    roots = [skill_dir / "examples" / "sales_vs_target", skill_dir / "templates" / "starter"]
    if plugin_dir is not None:
        roots += sorted(p for p in (plugin_dir / "templates").glob("*") if (p / "model.spec.json").is_file())
    return [r for r in roots if (r / "model.xlsx").is_file() and (r / "model.manifest.json").is_file()]


def test_shipped_models_tie_out_in_every_cell(skill_dir: Path, plugin_dir: Path | None, tmp_path: Path) -> None:
    """Every shipped model folder present (six with the plugin; two when the skill folder is used alone) ties
    out in every compared cell under the v4 rule, against the values Excel saved in the shipped workbook."""
    roots = _shipped_roots(skill_dir, plugin_dir)
    if not roots:
        pytest.skip("no shipped model folder with a built workbook is present")
    checked = []
    for root in roots:
        copy = tmp_path / root.name
        shutil.copytree(root, copy, ignore=shutil.ignore_patterns("lift", "reports"))
        rep = lift.lift_model(copy)
        assert rep.data["not_reproduced"] == 0, (root.name, [f.line() for f in rep.findings])
        out = tmp_path / f"{root.name}.json"
        res = _run_script(Path(rep.data["script"]), "--json", str(out))
        result = json.loads(out.read_text(encoding="utf-8"))
        c = result["counts"]
        assert res.returncode == 0 and result["status"] == "tie", (root.name, res.stdout[-3000:])
        assert c["cells"] > 0 and c["tie"] == c["cells"] and c["differ"] == 0 and c["not reproduced"] == 0, root.name
        assert len(result["cells"]) == c["cells"] and all(x["status"] == "tie" for x in result["cells"]), root.name
        assert result["max_difference"] is not None and result["max_difference"] <= result["tolerance"], root.name
        checked.append(f"{root.name}: {c['cells']} cells, largest difference {result['max_difference']:.3g}")
    shipped = 2 + (len([p for p in (plugin_dir / "templates").glob("*") if (p / "model.spec.json").is_file()])
                   if plugin_dir is not None else 0)
    assert len(checked) == shipped, checked                 # every shipped folder present has a built workbook


def test_all_lists_every_compared_cell(tmp_path: Path) -> None:
    """--all prints one line per compared cell and --json holds one record per compared cell (v3 kept 27 of
    597); every step's largest difference is printed, also when every cell ties (0, not '-')."""
    p = fixture.write_model(tmp_path / "model")
    wb = _values_workbook(p, tmp_path / "good.xlsx", _computed_values(p["root"]))
    script = p["root"] / "lift" / SCRIPT_NAME
    res = _run_script(script, "--workbook", str(wb), "--all", "--json", str(tmp_path / "t.json"))
    assert res.returncode == 0, res.stdout[-3000:]
    result = json.loads((tmp_path / "t.json").read_text(encoding="utf-8"))
    n = result["counts"]["cells"]
    assert n > 500 and result["counts"]["tie"] == n
    assert len(result["cells"]) == n
    assert len({c["cell"] for c in result["cells"]}) == n           # one record per cell, none twice
    lines = res.stdout.splitlines()
    start = lines.index(f"Every compared cell ({n:,})")
    listed = []
    for line in lines[start + 2:]:
        if not line.strip():
            break
        listed.append(line)
    assert len(listed) == n
    assert all(line.rstrip().endswith("tie") for line in listed)
    # the header line and the steps table: the largest difference over every compared cell, ties included,
    # printed although every cell ties
    d, cell = result["max_difference"], result["max_difference_cell"]
    assert d is not None and 0 <= d <= 0.001 and cell
    assert lines[1] == (f"tie-out: {n:,} cells compared; tolerance 0.001 (absolute) plus 1e-13 times the larger "
                        f"value (double-precision rounding); largest difference {d:.6g} at {cell}")
    numeric = [s for s in result["steps"] if s["max_difference"] is not None]
    assert len(numeric) >= 10 and all(s["max_difference"] <= 0.001 for s in numeric)
    step = max(numeric, key=lambda s: s["max_difference"])
    assert step["max_difference"] == d and step["max_difference_cell"] == cell
    for s in numeric:
        row = next(line for line in lines if line.startswith(s["step"][:34] + " "))
        assert row.split()[-1] == f"{s['max_difference']:,.6g}", row        # printed, also 0 (v3 printed '-')
    # without --all only the cells that do not tie out are listed (none here)
    res = _run_script(script, "--workbook", str(wb))
    assert res.returncode == 0 and "Every compared cell" not in res.stdout


# ============================================================================= [J-2] the temporary folder

class _Stop(BaseException):
    """An interruption that is not an Exception (as KeyboardInterrupt is not)."""


def _fake_excel(monkeypatch, recalc) -> list[Path]:
    from lib import excel
    created: list[Path] = []
    real = tempfile.mkdtemp

    def spy(*args, **kwargs):
        d = real(*args, **kwargs)
        created.append(Path(d))
        return d
    monkeypatch.setattr(lift.tempfile, "mkdtemp", spy)
    monkeypatch.setattr(excel, "excel_status", lambda: SimpleNamespace(available=True, reason=""))
    monkeypatch.setattr(excel, "recalc", recalc)
    return created


def test_temporary_folder_is_removed_on_every_path_without_excel(tmp_path: Path, monkeypatch) -> None:
    """lift --run on a workbook without saved values, with Excel replaced by fakes: the folder rotm_lift_<random>
    is gone after a recalculation that fails, one that raises, an interruption, a run of the script that raises,
    and a run that ends (the copy holds the manifest too, which v3 left behind)."""
    p = fixture.write_model(tmp_path / "model")
    lift.lift_model(p["root"])
    script = p["root"] / "lift" / SCRIPT_NAME
    cases = {
        "failed": lambda path, save=True: SimpleNamespace(ok=False, errors=["Excel could not open it"]),
        "raised": lambda path, save=True: (_ for _ in ()).throw(RuntimeError("Excel stopped")),
        "ended": lambda path, save=True: SimpleNamespace(ok=True, errors=[]),
    }
    for name, recalc in cases.items():
        created = _fake_excel(monkeypatch, recalc)
        report = common.Report("lift", "x")
        report.data = {}
        lift._run_and_report(script, p["workbook"], report)
        assert len(created) == 1 and not created[0].exists(), name
        assert report.data["run"]["exit_code"] == 3, name      # the copy holds no values either: not verified

    def interrupted(path, save=True):
        raise _Stop()
    created = _fake_excel(monkeypatch, interrupted)
    report = common.Report("lift", "x")
    report.data = {}
    with pytest.raises(_Stop):
        lift._run_and_report(script, p["workbook"], report)
    assert len(created) == 1 and not created[0].exists()

    created = _fake_excel(monkeypatch, cases["ended"])
    real_run = lift._run_script

    def run_fails_on_the_copy(s, workbook=None):
        if workbook is not None:
            assert workbook.parent == created[0] and workbook.is_file()
            assert (workbook.parent / "model.manifest.json").is_file()
            raise RuntimeError("the script could not start")
        return real_run(s, workbook)
    monkeypatch.setattr(lift, "_run_script", run_fails_on_the_copy)
    report = common.Report("lift", "x")
    report.data = {}
    with pytest.raises(RuntimeError):
        lift._run_and_report(script, p["workbook"], report)
    assert len(created) == 1 and not created[0].exists()


@pytest.mark.excel
def test_lift_run_leaves_no_temporary_folder(tmp_path: Path, monkeypatch) -> None:
    """lift --run on a workbook without values saved by Excel: a copy is recalculated in Excel in a temporary
    folder, and afterwards no rotm_lift_ folder is left (v3 left one per run)."""
    p = fixture.write_model(tmp_path / "model")
    created: list[Path] = []
    real = tempfile.mkdtemp

    def spy(*args, **kwargs):
        d = real(*args, **kwargs)
        created.append(Path(d))
        return d
    monkeypatch.setattr(lift.tempfile, "mkdtemp", spy)
    before = _rotm_lift_folders()
    report = lift.lift_model(p["root"], run=True)
    after = _rotm_lift_folders()
    assert report.exit_code == 0, [f.line() for f in report.findings] + report.notes
    assert any("temporary copy was recalculated" in n for n in report.notes)
    assert report.data["tie_out"]["status"] == "tie"
    assert len(created) == 1 and not created[0].exists()
    assert after == before


# ============================================================================= [A-aera-13] trace

def _worked_copy(skill_dir: Path, dest: Path, *, saved_values: bool = True) -> Path:
    src = skill_dir / "examples" / "sales_vs_target"
    dest.mkdir(parents=True)
    shutil.copy2(src / "model.manifest.json", dest / "model.manifest.json")
    if saved_values:
        shutil.copy2(src / "model.xlsx", dest / "model.xlsx")
    else:                                   # openpyxl writes no saved values: the row comes from the layout
        import openpyxl
        wb = openpyxl.load_workbook(src / "model.xlsx")
        wb.save(dest / "model.xlsx")
    return dest / "model.xlsx"


def _nodes(node: dict) -> list[dict]:
    out = [node]
    for ch in node["children"]:
        out += _nodes(ch)
    return out


def _raw_value(skill_dir: Path, row: int, column: str):
    with open(skill_dir / "examples" / "sales_vs_target" / "raw" / "sales.csv", encoding="utf-8-sig",
              newline="") as f:
        return list(csv.DictReader(f))[row - 2][column]


def test_trace_of_a_fetch_cell_ends_at_one_raw_cell(skill_dir: Path, tmp_path: Path) -> None:
    """h_sales!G22 = INDEX(raw_sales!$F:$F,A22) ends at raw_sales!F22 with its value, not at the whole column."""
    wb = _worked_copy(skill_dir, tmp_path / "worked")
    rep = trace.trace_cell(wb, "h_sales!G22")
    nodes = _nodes(rep.data["tree"])
    raw = [n for n in nodes if n["kind"] == "raw"]
    assert [n["ref"] for n in raw] == ["raw_sales!F22"]
    want = float(_raw_value(skill_dir, 22, "amount"))
    assert raw[0]["value"] == want and raw[0]["count"] == 1 and raw[0]["label"] == "'amount'"
    assert "row 22 is the saved value of h_sales!A22" in raw[0]["note"]
    assert not any("raw_sales column" in n["ref"] for n in nodes)
    assert f"- raw_sales!F22 'amount' (raw data; row 22 is the saved value of h_sales!A22) = {want:,.0f}" in \
        "\n".join(rep.data["lines"])
    assert rep.data["raw_sheets_reached"] == ["raw_sales"]
    # a text fetch and the in_raw flag read one raw row by position too
    for ref, end in (("h_sales!F22", "raw_sales!D22"), ("h_sales!B22", "raw_sales!A22")):
        refs = [n["ref"] for n in _nodes(trace.trace_cell(wb, ref).data["tree"]) if n["kind"] == "raw"]
        assert refs == [end], ref


def test_trace_of_a_fetch_cell_without_saved_values_takes_the_row_from_the_layout(skill_dir: Path,
                                                                                   tmp_path: Path) -> None:
    wb = _worked_copy(skill_dir, tmp_path / "worked", saved_values=False)
    rep = trace.trace_cell(wb, "h_sales!G22")
    raw = [n for n in _nodes(rep.data["tree"]) if n["kind"] == "raw"]
    assert [n["ref"] for n in raw] == ["raw_sales!F22"]
    assert raw[0]["value"] == float(_raw_value(skill_dir, 22, "amount"))      # raw sheets hold typed values
    assert "row 22 is the helper row's own position (h_sales!A22 is =ROW() in the manifest's layout" in raw[0]["note"]
    # without the manifest and without saved values the raw row is not known: the column stays
    (tmp_path / "worked" / "model.manifest.json").unlink()
    rep = trace.trace_cell(wb, "h_sales!G22")
    assert [n["ref"] for n in _nodes(rep.data["tree"]) if n["kind"] == "raw"] == ["raw_sales column F 'amount'"]


def test_trace_of_a_conditional_sum_names_the_rows_that_can_contribute(skill_dir: Path, tmp_path: Path) -> None:
    """calc!D6 = SUMIFS over helper columns: the raw columns stay whole and are labelled as the rows that can
    contribute, with the criteria and their values; the matching rows are not named (a later addition)."""
    wb = _worked_copy(skill_dir, tmp_path / "worked")
    rep = trace.trace_cell(wb, "calc!D6")
    raw = [n for n in _nodes(rep.data["tree"]) if n["kind"] == "raw"]
    label = ("raw data; the rows that can contribute to SUMIFS in calc!D6, where h_sales!$F:$F matches $B6 "
             "('East') and h_sales!$H:$H matches D$5 (2026-01-31)")
    assert {n["ref"] for n in raw} == {"raw_sales column F 'amount'", "raw_sales column D 'region'",
                                       "raw_sales column C 'inv_date'"}
    assert all(n["note"] == label and n["rows"] == "rows 2 to 601" and n["count"] == 600 for n in raw)
    # a reference that is not a range of the SUMIFS carries no such label
    scale = next(n for n in _nodes(rep.data["tree"]) if n["ref"] == "inputs!D7")
    assert "contribute" not in scale["note"]
    # a plain sum of a raw column keeps the plain label (checks!D6 = SUM(raw_sales!$F:$F))
    rep = trace.trace_cell(wb, "checks!D6")
    raw = [n for n in _nodes(rep.data["tree"]) if n["kind"] == "raw"]
    assert [n["ref"] for n in raw] == ["raw_sales column F 'amount'"] and raw[0]["note"] == "raw data"
    # a conditional count over a flag column names its criterion (checks!D12 = COUNTIF(h_sales!$B:$B,0))
    rep = trace.trace_cell(wb, "checks!D12")
    raw = [n for n in _nodes(rep.data["tree"]) if n["kind"] == "raw"]
    assert [n["note"] for n in raw] == [
        "raw data; the rows that can contribute to COUNTIF in checks!D12, where h_sales!$B:$B matches 0"]

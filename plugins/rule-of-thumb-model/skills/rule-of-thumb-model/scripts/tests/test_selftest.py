"""selftest's own logic (design section 16). Owner: INTEGRATOR.

The full selftest runs pytest, so it is not run from pytest; these tests cover the step bookkeeping, the exit codes,
the folders it finds, the F08a fixture and the F27 data helpers.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from lib import common, selftest  # noqa: E402


def test_steps_are_the_designed_steps() -> None:
    assert selftest.STEPS == ("pytest", "examples", "portable", "recalc", "render", "orphans", "f02_skill_alone",
                              "f08a_existing", "f27_power_query")
    assert set(selftest.QUICK_STEPS) == {"pytest", "examples", "portable"}
    assert "portable" not in selftest.EXCEL_STEPS


@pytest.mark.parametrize("results, strict, code", [
    ({"pytest": "pass", "examples": "pass"}, False, 0),
    ({"pytest": "pass", "examples": "fail"}, False, 1),
    ({"pytest": "pass", "recalc": "skipped"}, False, 0),
    ({"pytest": "pass", "recalc": "skipped"}, True, 3),
    ({"pytest": "pass", "recalc": "not run"}, True, 0),            # --quick never changes the exit code
    ({"pytest": "fail", "recalc": "skipped"}, True, 1),
])
def test_exit_code_for_steps(results: dict[str, str], strict: bool, code: int) -> None:
    assert selftest.exit_code_for_steps(results, strict=strict) == code


def test_model_folders_finds_the_example_and_the_templates() -> None:
    names = {f"{p.parent.name}/{p.name}" for p in selftest.model_folders()}
    assert {"examples/sales_vs_target", "templates/starter"} <= names


def test_f08a_fixture_has_the_parts_f08a_names(tmp_path: Path) -> None:
    from lib import formula, workbook
    path = selftest.make_f08a_fixture(tmp_path / "fixture.xlsx")
    pk = workbook.package_parts(path)
    assert pk.charts == 1 and pk.tables == 1
    snap = workbook.snapshot(path)
    assert snap.names and snap.cf.get("Data") and snap.dv.get("Data")
    deep = [f for f in snap.formulas.values() if formula.depth(f) > 2]
    assert deep, "the fixture needs one formula over the depth limit for split"


def test_f27_more_lines_follow_the_contract(tmp_path: Path) -> None:
    rows = [["L0001", "2026-01-02", "Rent", "655000"], ["L0002", "2026-02-27", "Staff", "1158000"]]
    more = selftest._more_lines(rows, 3)
    assert [r[0] for r in more] == ["L9001", "L9002", "L9003"]
    assert {r[2] for r in more} <= {"Rent", "Staff"}
    path = tmp_path / "lines.csv"
    selftest._write_csv(path, rows + more)
    assert selftest._read_csv(path) == rows + more


def test_copy_leaves_out_bytecode(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "__pycache__").mkdir(parents=True)
    (src / "__pycache__" / "x.pyc").write_bytes(b"x")
    (src / "keep.txt").write_text("k", encoding="utf-8")
    dest = selftest._copytree(src, tmp_path / "dest")
    assert (dest / "keep.txt").is_file() and not (dest / "__pycache__").exists()


def test_selftest_help_lists_its_flags() -> None:
    import subprocess
    proc = subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), "selftest", "-h"],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0
    for flag in ("--strict", "--quick", "--no-excel", "--foreign", "--keep", "--json"):
        assert flag in proc.stdout


def test_a_failing_step_is_reported_and_the_others_still_run(tmp_path: Path) -> None:
    rep = common.Report("selftest", "x")
    run = selftest._Run(rep, tmp_path, strict=True, quick=False, excel_ok=False, excel_why="test", foreign_dir=None)

    def bad() -> str:
        raise selftest.StepFailed("the pass condition does not hold")

    run.run("examples", bad)
    run.run("pytest", lambda: "ok")
    run.skip("recalc", "Excel is not available (test)")
    assert run.results == {"examples": "fail", "pytest": "pass", "recalc": "skipped"}
    assert [f.rule for f in rep.findings] == ["SELFTEST-FAIL", "SELFTEST-PASS", "SELFTEST-SKIPPED"]
    assert selftest.exit_code_for_steps(run.results, strict=True) == 1


# ----------------------------------------------------------------------------- f27: the command sequence (v2)

def _spend_total(root: Path) -> float:
    """The starter's spend block total as the fake workbook holds it: the amounts of raw/lines.csv whose category is
    one of the four and whose date is in January to March 2026 (the test's own reading of the starter)."""
    total = 0.0
    for r in selftest._read_csv(root / "raw" / "lines.csv"):
        if r[2] in ("Rent", "Staff", "Supplies", "Travel") and r[1][:7] in ("2026-01", "2026-02", "2026-03"):
            total += float(r[3])
    return total


def _write_fake_workbook(root: Path, value: float) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "calc"
    ws["G10"] = value
    wb.save(root / "model.xlsx")


class _FakeRotm:
    """Stands in for selftest._rotm in step f27: records each command and answers as the plugin would, with no
    Excel. check --refresh reports H-COVERAGE when the CSV the query reads holds more rows than were landed and
    H-BEYOND when it holds fewer; `omit` leaves one rule out, to see that the step then fails. build and an accepted
    refresh write model.xlsx with the spend block's total in calc!G10 (as Excel would save it); with `stale` the
    accepted refresh leaves the built value there, to see that the step then fails."""

    def __init__(self, omit: str | None = None, stale: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.landed = 0
        self.omit = omit
        self.stale = stale

    def __call__(self, args: list[str], *, env=None, timeout=None, skill=None):
        import csv
        import json
        import shutil
        import subprocess
        self.calls.append(list(args))
        cmd, root = args[0], Path(args[1]) if len(args) > 1 else None
        out = Path(args[args.index("--json") + 1]) if "--json" in args else None
        report: dict = {"summary": {"errors": 0, "warnings": 0}, "findings": [], "data": {}}
        code = 0
        starter = SCRIPTS.parent / "templates" / "starter"
        if cmd == "init":
            root.mkdir(parents=True)
            shutil.copy2(starter / "model.spec.json", root / "model.spec.json")
            shutil.copytree(starter / "data", root / "data")
        elif cmd == "land":
            (root / "raw").mkdir(exist_ok=True)
            shutil.copy2(args[args.index("--csv") + 1], root / "raw" / "lines.csv")
        elif cmd == "build":
            spec = json.loads((root / "model.spec.json").read_text(encoding="utf-8"))
            raw = next(s for s in spec["sheets"] if s.get("type") == "raw")
            (root / "model.manifest.json").write_text(json.dumps(
                {"workbook": "model.xlsx",
                 "sheets": [{"type": "raw", "landing": raw.get("landing", "static")},
                            {"name": "calc", "type": "calc", "blocks": [{"id": "spend", "total": "G10"}]}]}),
                encoding="utf-8")
            self.landed = self._rows(root)
            _write_fake_workbook(root, _spend_total(root))
        elif cmd == "extend":
            self.landed = self._rows(root)
        elif cmd == "check":
            report["data"] = {"master": {"value": 0}, "error_cells": []}
            if "--accept-refresh" in args and not self.stale:
                _write_fake_workbook(root, _spend_total(root))
            rows = self._rows(root)
            if "--refresh" in args and rows != self.landed:
                rule = "H-COVERAGE" if rows > self.landed else "H-BEYOND"
                if rule != self.omit:
                    report["findings"].append({"rule": rule, "severity": "error", "message": f"{rule} rows",
                                               "fix": f"rotm.py extend {root} --table lines"})
                report["data"]["master"]["value"] = 1
                code = 1
        if out is not None:
            out.write_text(json.dumps(report), encoding="utf-8")
        return subprocess.CompletedProcess(args, code, "", "")

    @staticmethod
    def _rows(root: Path) -> int:
        with open(root / "raw" / "lines.csv", encoding="utf-8", newline="") as f:
            return sum(1 for _ in f) - 1


def _run_f27(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: _FakeRotm) -> tuple:
    monkeypatch.setattr(selftest, "_rotm", fake)
    rep = common.Report("selftest", "x")
    run = selftest._Run(rep, tmp_path, strict=True, quick=False, excel_ok=True, excel_why="", foreign_dir=None)
    run.run("f27_power_query", run.step_f27)
    return run, rep


def test_f27_runs_the_planned_command_sequence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRotm()
    run, rep = _run_f27(tmp_path, monkeypatch, fake)
    assert run.results == {"f27_power_query": "pass"}, [f.message for f in rep.findings]
    seq = [(c[0], tuple(a for a in c if a.startswith("--") and a not in ("--json", "--quiet"))) for c in fake.calls]
    assert seq == [
        ("init", ("--template", "--name")),
        ("contract", ("--table", "--reply", "--by", "--channel", "--source-file")),
        ("land", ("--table", "--csv")),
        ("build", ("--strict", "--force")),
        ("check", ()),
        ("audit", ("--strict",)),
        ("check", ("--refresh",)),
        ("extend", ()),
        ("check", ("--accept-refresh", "--control")),
        ("audit", ("--strict",)),
        ("check", ("--refresh",)),
        ("extend", ()),
        ("check", ("--accept-refresh", "--control")),
        ("audit", ("--strict",)),
        ("build", ("--strict",)),
        ("check", ("--strict",)),
        ("audit", ("--strict",)),
    ]
    land = fake.calls[2]
    assert Path(land[land.index("--csv") + 1]).as_posix().endswith("/data/lines.csv")
    agree = fake.calls[1]
    assert agree[agree.index("--channel") + 1] == "task-file"
    controls = [c[c.index("--control") + 1] for c in fake.calls if "--control" in c]
    base = selftest._read_csv(SCRIPTS.parent / "templates" / "starter" / "data" / "lines.csv")
    grown = base + selftest._more_lines(base, 15)
    assert controls == [f"lines.amount={sum(int(r[3]) for r in grown)}",
                        f"lines.amount={sum(int(r[3]) for r in base[:40])}"]
    text = next(f.message for f in rep.findings if f.rule == "SELFTEST-PASS")
    lines = text.split("Power Query table over a local CSV: ", 1)[1].split("; ")
    # one line per command, the landing setting, and one line per accepted refresh for the output cell (v3)
    assert len(lines) == len(fake.calls) + 1 + 2
    assert any("H-COVERAGE" in line for line in lines) and any("H-BEYOND" in line for line in lines)
    outputs = [line for line in lines if "calc!G10" in line]
    assert len(outputs) == 2 and all("computed from the CSV" in line for line in outputs), outputs


# ----------------------------------------------------------------------------- v3: f27 output cell, N-PYTEST, strict

def test_f27_fails_when_the_output_cell_keeps_the_old_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """[A-aera-12, C-sol-9, C-sol-10] part 4: after an accepted refresh the spend block's total must hold the value
    computed from the new CSV; a workbook whose output still holds the built value fails the step, naming the cell
    and both values."""
    run, rep = _run_f27(tmp_path, monkeypatch, _FakeRotm(stale=True))
    assert run.results == {"f27_power_query": "fail"}
    msg = next(f.message for f in rep.findings if f.rule == "SELFTEST-FAIL")
    assert "calc!G10" in msg and "computed from raw/lines.csv" in msg, msg


def test_f27_block_total_follows_the_block_categories_and_months() -> None:
    """The Python value of the spend block: only the block's categories (the input list) and the three months of
    its timeline count."""
    import json
    spec = json.loads((SCRIPTS.parent / "templates" / "starter" / "model.spec.json").read_text(encoding="utf-8"))
    rows = [["L1", "2026-01-02", "Rent", "100"], ["L2", "2026-03-31", "Staff", "20.5"],
            ["L3", "2026-04-01", "Rent", "7"], ["L4", "2025-12-31", "Rent", "9"], ["L5", "2026-02-10", "Other", "3"]]
    assert selftest.f27_block_total(spec, rows) == selftest.Decimal("120.5")


def test_selftest_without_pytest_prints_one_line_and_exits_3(monkeypatch: pytest.MonkeyPatch,
                                                              capsys: pytest.CaptureFixture[str]) -> None:
    """[C-sol-16]: without pytest, `rotm.py selftest` prints one line that names the package and exits 3; no step
    runs and no traceback is printed."""
    import argparse
    import importlib.util
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a, **k: None if name == "pytest" else real(name, *a, **k))

    def no_step(*_a, **_k):
        raise AssertionError("no selftest step may run without pytest")

    monkeypatch.setattr(selftest, "selftest", no_step)
    args = argparse.Namespace(strict=True, quick=False, no_excel=False, foreign=None, keep=False, json=None,
                              quiet=False, command="selftest")
    code = common.run_command(selftest.run_selftest, args, name="selftest")
    out = capsys.readouterr()
    lines = [line for line in (out.out + out.err).splitlines() if line.strip()]
    assert code == 3
    assert lines == [selftest.N_PYTEST], lines
    assert "pytest" in lines[0] and "Traceback" not in out.out + out.err


@pytest.mark.parametrize("strict", [True, False])
def test_report_strict_field_is_the_strict_option(strict: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """[J-6]: the JSON report's strict field is the --strict option. No step runs: the step methods are replaced."""
    for name in ("step_pytest", "step_examples", "step_portable", "step_recalc", "step_render", "step_orphans",
                 "step_f02", "step_f08a", "step_f27"):
        monkeypatch.setattr(selftest._Run, name, lambda self: "replaced in the test")
    rep = selftest.selftest(strict=strict, excel=False)
    data = rep.to_dict()
    assert data["strict"] is strict
    assert data["exit_code"] == (3 if strict else 0)          # Excel steps skipped: exit 3 only with --strict
    out = tmp_path / "selftest.json"
    rep.write_json(out)
    import json
    assert json.loads(out.read_text(encoding="utf-8"))["strict"] is strict


def test_f27_control_totals_are_exact_sums_of_the_csv(tmp_path: Path) -> None:
    rows = [["L1", "2026-01-02", "Rent", "655000"], ["L2", "2026-01-03", "Rent", "0.1"],
            ["L3", "2026-01-04", "Rent", "0.2"]]
    assert selftest._amount_total(rows) == "655000.3"


@pytest.mark.parametrize("omit", ["H-COVERAGE", "H-BEYOND"])
def test_f27_fails_when_check_does_not_name_the_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                     omit: str) -> None:
    run, rep = _run_f27(tmp_path, monkeypatch, _FakeRotm(omit=omit))
    assert run.results == {"f27_power_query": "fail"}
    assert any(omit in f.message for f in rep.findings if f.rule == "SELFTEST-FAIL")


# ----------------------------------------------------------------------------- f08a: volatile cells (v2)

def test_f08a_comparison_leaves_volatile_values_out_and_states_it(tmp_path: Path) -> None:
    """A-aera-16 in step f08a: a RANDBETWEEN cell and the cells that depend on it keep their formula compared, not
    their cached value; a changed value elsewhere is still a difference; the line carries the statement."""
    from openpyxl import Workbook
    from lib import workbook
    paths = []
    for name, rnd, other in (("before.xlsx", 3, 10), ("after.xlsx", 7, 10), ("changed.xlsx", 7, 11)):
        wb = Workbook()
        ws = wb.active
        ws.title = "calc"
        ws["A1"], ws["A2"], ws["A3"], ws["B1"] = "=RANDBETWEEN(1,9)", "=A1*2", "=A2+1", other
        wb.save(tmp_path / name)
        # cached values as Excel would save them: the volatile chain changes between saves
        _set_cached(tmp_path / name, {"A1": rnd, "A2": rnd * 2, "A3": rnd * 2 + 1})
        paths.append(tmp_path / name)
    before, after, changed = (workbook.snapshot(p) for p in paths)
    intended = workbook.Intended("styles_only")
    diffs, statement = selftest.f08a_compare(before, after, intended, workbook)
    assert diffs == []
    assert "3 cells left out" in statement and "calc!A1" in statement
    diffs, _ = selftest.f08a_compare(before, changed, intended, workbook)
    assert diffs and any("B1" in d.message for d in diffs)


# ----------------------------------------------------------------------------- v4: [A-aera-17] the f08a runner's classes

GUARD_FINDING = {"rule": "SPLIT-GUARD", "severity": "error", "sheet": "Data", "cell": "E2",
                 "message": "Data!E2: the part SUM($D$2:$D$5) sits inside IFERROR, and split moves a guarded part "
                            "only for a lookup (INDEX with MATCH); nothing was written",
                 "fix": "apply the pattern of references/helper-patterns.md H5 by hand, or split this formula by hand"}
NONE_FINDING = {"rule": "SPLIT-NONE", "severity": "info", "message": "Data!E2 has nesting depth 2; nothing to split"}


class _FakeReport:
    """Stands in for selftest._rotm in the f08a runner: writes the given JSON report, writes no output file, and
    returns the given exit code."""

    def __init__(self, findings: list[dict], code: int) -> None:
        self.findings, self.code = findings, code

    def __call__(self, args: list[str], *, env=None, timeout=None, skill=None):
        import json
        import subprocess
        out = Path(args[args.index("--json") + 1])
        out.write_text(json.dumps({"findings": self.findings, "data": {"written": False}, "verified": {}}),
                       encoding="utf-8")
        return subprocess.CompletedProcess(args, self.code, "", "")


def _f08a_split(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, findings: list[dict], code: int,
                expect: str | None) -> str:
    from lib import workbook
    monkeypatch.setattr(selftest, "_rotm", _FakeReport(findings, code))
    run = selftest._Run(common.Report("selftest", "x"), tmp_path, strict=True, quick=False, excel_ok=True,
                        excel_why="", foreign_dir=None)
    base = tmp_path / "f08a" / "book"
    base.mkdir(parents=True, exist_ok=True)
    args = ["split", str(base / "orig.xlsx"), "Data!E2", "--cell-only"]
    return run._f08a_command(tmp_path / "book.xlsx", base, "split", args, None, None, {}, workbook,
                             expect=expect)


def test_f08a_runner_does_not_call_a_split_guard_refusal_nothing_to_change(tmp_path: Path,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    # an error finding the run does not expect: the step fails, and its line names the rule id
    with pytest.raises(selftest.StepFailed) as exc:
        _f08a_split(tmp_path, monkeypatch, [GUARD_FINDING], 1, None)
    assert "SPLIT-GUARD" in str(exc.value) and "nothing to change" not in str(exc.value)
    assert "not expected" in str(exc.value)
    # the refusal the run expects: an expected refusal, never 'nothing to change'
    line = _f08a_split(tmp_path, monkeypatch, [GUARD_FINDING], 1, "SPLIT-GUARD")
    assert line.startswith("split: refused as expected (SPLIT-GUARD: Data!E2: the part"), line
    assert "nothing to change" not in line


def test_f08a_runner_classes_a_run_without_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # no finding of severity error, exit 0: a verified no-op
    assert _f08a_split(tmp_path, monkeypatch, [NONE_FINDING], 0, None) == "split: nothing to change, nothing written"
    # the run expected SPLIT-GUARD and got none: a failure that names the expected rule id
    with pytest.raises(selftest.StepFailed, match="expected SPLIT-GUARD"):
        _f08a_split(tmp_path, monkeypatch, [NONE_FINDING], 0, "SPLIT-GUARD")
    # another error finding than the expected one fails too
    other = dict(GUARD_FINDING, rule="SPLIT-SHAPE")
    with pytest.raises(selftest.StepFailed, match="SPLIT-SHAPE"):
        _f08a_split(tmp_path, monkeypatch, [other], 1, "SPLIT-GUARD")


def test_f08a_expectation_comes_from_the_formula() -> None:
    assert selftest.f08a_split_expectation("=IFERROR(ROUND(SUM(A1:A3)/B1,2),0)") == "SPLIT-GUARD"
    assert selftest.f08a_split_expectation("=IFERROR(INDEX(D2:D3,MATCH(A2,C2:C3,0)),0)") is None
    assert selftest.f08a_split_expectation("=ROUND(IFERROR(INDEX(D2:D3,MATCH(A2,C2:C3,0)),0),2)") is None
    assert selftest.f08a_split_expectation("=IFERROR(INDEX(D2:D3,MATCH(TRIM(A2),C2:C3,0)),0)") == "SPLIT-GUARD"
    assert selftest.f08a_split_expectation("=ROUND(MAX(MIN(SUM(A1:A9),B1),0),2)") is None
    # a guarded part inside a part that is moved: the moved part is split again under the same rule
    assert selftest.f08a_split_expectation("=ROUND(SUM(IFERROR(ABS(ROUND(A1,0)),0),1),2)", limit=2) == "SPLIT-GUARD"


def test_f08a_fixture_names_its_expected_split_outcomes(tmp_path: Path) -> None:
    from lib import split, workbook
    path = selftest.make_f08a_fixture(tmp_path / "fixture.xlsx")
    info = workbook.open_workbook(path, with_values=False)
    assert [(s, c) for s, c, _e in selftest.F08A_FIXTURE_SPLITS] == [("Data", "G2"), ("Data", "E2")]
    for sheet, cell, expect in selftest.F08A_FIXTURE_SPLITS:
        text = info.sheet(sheet).cell(cell).formula
        assert selftest.f08a_split_expectation(text) == expect, (cell, text)
        if expect is None:
            assert split.plan_split(text, limit=2).helpers
        else:
            with pytest.raises(split.SplitGuard):
                split.plan_split(text, limit=2)


# ----------------------------------------------------------------------------- v4: [A-aera-7] f02 names the tests

FAILING_NAME = "scripts/tests/test_one.py::test_fails_on_purpose"   # as selftest.failing_tests writes it (v5)


def _fake_skill(root: Path) -> Path:
    """A skill folder with the plugin's scripts, the skill's pytest.ini (read at run time) and one test file of two
    tests, one of which fails. v5 (A-aera-8, C-sol-8): with its own pytest.ini the folder is pytest's root folder
    wherever the temporary folder is (also under a folder that holds another pytest configuration file)."""
    import shutil
    skill = root / "skills" / "rule-of-thumb-model"
    shutil.copytree(SCRIPTS / "lib", skill / "scripts" / "lib", ignore=shutil.ignore_patterns(*selftest.COPY_SKIP))
    shutil.copy2(SCRIPTS / "rotm.py", skill / "scripts" / "rotm.py")
    (skill / "pytest.ini").write_text((SCRIPTS.parent / "pytest.ini").read_text(encoding="utf-8"), encoding="utf-8")
    tests = skill / "scripts" / "tests"
    tests.mkdir()
    (tests / "test_one.py").write_text("def test_passes():\n    assert True\n\n\n"
                                       "def test_fails_on_purpose():\n    assert 1 == 2\n", encoding="utf-8")
    return skill


def test_pytest_step_names_the_failing_tests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    skill = _fake_skill(tmp_path / "fake")
    monkeypatch.setattr(selftest, "_skill", lambda: skill)
    rep = common.Report("selftest", "x")
    run = selftest._Run(rep, tmp_path / "work", strict=True, quick=True, excel_ok=False, excel_why="test",
                        foreign_dir=None)
    run.run("pytest", run.step_pytest)
    assert run.results == {"pytest": "fail"}
    assert [selftest.node_name(t) for t in rep.data["pytest_failed"]] == [FAILING_NAME]
    msg = next(f.message for f in rep.findings if f.rule == "SELFTEST-FAIL")
    assert FAILING_NAME in msg, msg


def test_f02_failure_names_the_failing_tests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    skill = _fake_skill(tmp_path / "fake")
    monkeypatch.setattr(selftest, "_skill", lambda: skill)
    rep = common.Report("selftest", "x")
    work = tmp_path / "work"
    work.mkdir()
    run = selftest._Run(rep, work, strict=True, quick=False, excel_ok=False, excel_why="test", foreign_dir=None)
    run.run("f02_skill_alone", run.step_f02)
    assert run.results == {"f02_skill_alone": "fail"}
    msg = next(f.message for f in rep.findings if f.rule == "SELFTEST-FAIL")
    assert f"failing tests of its pytest step: {FAILING_NAME}" in msg, msg
    assert "failing tests of its pytest step" in msg and "pytest" in msg, msg


def _set_cached(path: Path, values: dict[str, int]) -> None:
    """Write cached values <v> into the formula cells of sheet1 (openpyxl saves formulas without them)."""
    import re
    import zipfile
    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            data = src.read(info)
            if info.filename == "xl/worksheets/sheet1.xml":
                text = data.decode("utf-8")
                for ref, v in values.items():
                    text = re.sub(rf'(<c r="{ref}"[^>]*>)(<f>[^<]*</f>)(<v>[^<]*</v>|<v\s*/>)?',
                                  lambda m, v=v: f"{m.group(1)}{m.group(2)}<v>{v}</v>", text)
                data = text.encode("utf-8")
            dst.writestr(info, data)
    tmp.replace(path)

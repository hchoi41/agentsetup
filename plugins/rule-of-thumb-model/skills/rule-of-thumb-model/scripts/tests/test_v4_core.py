"""v4 tests of the shared core (package P1 of the v4 plan).

Entries:
- [C-sol-3] part 1: a formula that is not complete (an operator without an operand after it, two operators in a
  row, an operand missing before an operator, unbalanced brackets, a text in quotes that is not closed) is
  F-PARSE with severity error; the forms Excel 16 opens (signs after an operator, the percent sign, empty
  function arguments) are not. The measured list: data/formula/incomplete_examples.json.
- [C-sol-3] part 2: `rotm.py recalc` of a workbook Excel started for but could not open is X-OPEN (error, exit 1),
  not "not recalculated" (exit 3).
- [C-sol-10]: a job that timed out before it recorded its Excel PID stops no Excel process and names each
  automation Excel that appeared during the job.
- [A-aera-8, A-aera-9] part 1: workbook.open_workbook decides formula and error value from the type the file
  stores, so the landed texts '=pending' and '#N/A' are text, before and after Excel saves the workbook.

Tests marked excel drive Excel through lib.excel. Owner: P1 (v4). Machine-specific strings (folders of this PC,
user names) are built at run time, never written here.
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from lib import common, excel, workbook
from lib import formula as F

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
ROTM = SCRIPTS / "rotm.py"
EXAMPLES = json.loads((SCRIPTS / "tests" / "data" / "formula" / "incomplete_examples.json").read_text(encoding="utf-8"))
INCOMPLETE_FIX = ("complete the expression: an operand after each operator, every argument the function needs, "
                  "closed brackets and quotes")
TIMEOUT_NOTE = ("Excel PID 99999 (an automation Excel that appeared during this job) was not stopped: the job timed "
                "out before it recorded its Excel PID, so the PID is not known to be this job's")


def run_cli(*args: str, timeout: int = 900, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(ROTM), *args], capture_output=True,
                          text=True, encoding="utf-8", timeout=timeout, env={**os.environ, **(env or {})})


def parse_issues(formula: str) -> list[F.Issue]:
    return [i for i in F.check_formula(formula) if i.rule == "F-PARSE"]


# ============================================================================= [C-sol-3] part 1: formula.py

def test_incomplete_formulas_are_f_parse_errors() -> None:
    assert EXAMPLES["measured"] == "2026-11-25" and EXAMPLES["refused"] and EXAMPLES["accepted"]
    cases = {e["formula"] for e in EXAMPLES["refused"]}
    for needed in ("=D39+", "=A1&", "=A1*/B1", "=A1+*B1", "=*A1", "=SUM(*A1)", "=SUM(A1", "=SUM(A1))", '="abc'):
        assert needed in cases, needed
    for e in EXAMPLES["refused"]:
        issues = parse_issues(e["formula"])
        assert len(issues) == 1, (e["formula"], issues)
        i = issues[0]
        assert i.severity == "error", e["formula"]
        assert (i.data["reason"], i.data["start"], i.data["end"]) == (e["reason"], e["start"], e["end"]), e["formula"]
        assert i.message == (f"the formula is not complete: {e['reason']} at character {e['start']} "
                             f"({e['formula']})"), i.message
        assert i.fix == INCOMPLETE_FIX
        assert F.analyze(e["formula"]).incomplete == (e["reason"], e["start"], e["end"])
    # the reasons and places of the named cases
    want = {"=D39+": ("the operator '+' has no operand after it", 4, 5),
            "=A1&": ("the operator '&' has no operand after it", 3, 4),
            "=A1*/B1": ("two operators in a row ('*/')", 3, 5),
            "=A1+*B1": ("two operators in a row ('+*')", 3, 5),
            "=*A1": ("an operand is missing before '*'", 1, 2),
            "=SUM(*A1)": ("an operand is missing before '*'", 5, 6),
            "=SUM(A1": ("1 opening and 0 closing brackets", 4, 5),
            "=SUM(A1))": ("1 opening and 2 closing brackets", 8, 9),
            '="abc': ("a text in quotes is not closed", 1, 5)}
    for f, (reason, start, end) in want.items():
        assert F.analyze(f).incomplete == (reason, start, end), f
    # the forms Excel opens: no F-PARSE
    accepted = {e["formula"] for e in EXAMPLES["accepted"]}
    for needed in ("=A1+-B1", "=A1++B1", "=-A1", "=A1%", "=IF(A1,,1)", "=SUM(A1,)", "=INDEX(A1:A3,,1)",
                   "=ROUND(A1,)", "=ROUND(,1)", "=MATCH(A1,,0)", "=SUM(,)", "=IF(,1,2)", "=INDEX(,1)"):
        assert needed in accepted, needed
    for f in sorted(accepted) + ['="a""b"&A1', "='My Sheet'!A1+1", "=Table1[[#This Row],[Amount]]*2",
                                 "=LET(x,1,x+1)", "=@INDEX(A1:A3,1)", "=A1#", "=_xlfn.SINGLE(A1:A3)+1", "=+A1",
                                 '="("&A1', '=A1&")"', "={1,2;3,4}"]:
        assert not parse_issues(f), (f, parse_issues(f))
        assert F.analyze(f).incomplete is None, f
    # text the tokenizer cannot read and that is complete keeps the F-PARSE warning
    assert F.analyze("=SUM(A1)").incomplete is None
    unread = [i for i in F.check_formula("='Sheet 1!A1") if i.rule == "F-PARSE"]
    assert [i.severity for i in unread] == ["warning"], unread


def test_formula_command_refuses_a_trailing_operator() -> None:
    for extra in ((), ("--strict",)):
        proc = run_cli("formula", "=D39+", *extra, timeout=120)
        out = proc.stdout + proc.stderr
        assert proc.returncode == common.EXIT_FINDINGS, out
        assert "F-PARSE" in out and "the formula is not complete" in out, out
        assert "the operator '+' has no operand after it at character 4" in out, out


def _json_path_of_item(spec: dict, block_id: str, item_id: str) -> str:
    for si, sheet in enumerate(spec["sheets"]):
        for bi, block in enumerate(sheet.get("blocks") or []):
            if block.get("id") != block_id:
                continue
            for ii, item in enumerate(block.get("items") or []):
                if item.get("id") == item_id:
                    return f"sheets[{si}].blocks[{bi}].items[{ii}].expr"
    raise AssertionError(f"{block_id}.{item_id} is not in the specification")


def test_incomplete_expression_is_refused_by_build_with_its_json_path(tmp_path: Path) -> None:
    root = tmp_path / "sales_vs_target"
    shutil.copytree(SKILL / "examples" / "sales_vs_target", root,
                    ignore=shutil.ignore_patterns("__pycache__", "lift"))
    (root / "model.xlsx").unlink()
    spec_path = root / "model.spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    path = _json_path_of_item(spec, "bridge", "var_pct")
    si, bi, ii = (int(x.split("]")[0]) for x in path.split("[")[1:])
    item = spec["sheets"][si]["blocks"][bi]["items"][ii]
    assert item["id"] == "var_pct"
    item["expr"] = "{r:var}+"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    # outside the plugin folder the shipped 'example' agreements count as not agreed: set them back to proposed,
    # show the contracts and agree them on a task file
    task = tmp_path / "task.md"
    reply = "agreed, land it as proposed."
    task.write_text(f"The user's reply to the contracts: {reply}\n", encoding="utf-8")
    for table in ("sales", "target"):
        cpath = root / "contracts" / f"{table}.contract.json"
        data = json.loads(cpath.read_text(encoding="utf-8"))
        data["agreement"] = {"status": "proposed"}
        cpath.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        proc = run_cli("contract", "show", str(root), "--table", table, timeout=300)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        proc = run_cli("contract", "agree", str(root), "--table", table, "--reply", reply, "--by", "the user",
                       "--channel", "task-file", "--source-file", str(task), timeout=300)
        assert proc.returncode == 0 and "agreed by the user" in proc.stdout, proc.stdout + proc.stderr
    proc = run_cli("build", str(root), "--strict", env={"ROTM_NO_EXCEL": "1"})
    out = proc.stdout + proc.stderr
    assert proc.returncode == common.EXIT_REFUSED, out
    assert path in out, (path, out)
    assert "the formula is not complete" in out and "has no operand after it" in out, out
    assert not (root / "model.xlsx").exists(), "build wrote a workbook for an incomplete expression"


@pytest.mark.excel
@pytest.mark.slow
def test_excel_refuses_the_forms_f_parse_refuses(tmp_path: Path) -> None:
    """The measurement of data/formula/incomplete_examples.json, run again: Excel does not open a workbook that
    holds one refused form; it opens and calculates one workbook that holds every accepted form."""
    from openpyxl import Workbook

    def book(name: str, formulas: list[str]) -> Path:
        wb = Workbook()
        ws = wb.active
        ws.title = "calc"
        ws["A1"], ws["A2"], ws["A3"], ws["B1"] = 1, 2, 3, 2
        for r, f in enumerate(formulas, start=1):
            ws.cell(row=r, column=3, value=f)
        path = tmp_path / f"{name}.xlsx"
        wb.save(path)
        return path

    accepted = [e["formula"] for e in EXAMPLES["accepted"]]
    res = excel.recalc(book("accepted", accepted), out=tmp_path / "accepted_saved.xlsx")
    assert res.ok, res.errors
    import openpyxl
    ws = openpyxl.load_workbook(tmp_path / "accepted_saved.xlsx")["calc"]
    kept = [ws.cell(row=r, column=3).value for r in range(1, len(accepted) + 1)]
    assert kept == accepted, "Excel changed an accepted formula"
    refused_open = []
    for n, e in enumerate(EXAMPLES["refused"], start=1):
        res = excel.recalc(book(f"refused_{n:02d}", [e["formula"]]), save=False)
        if res.ok:
            refused_open.append(e["formula"])
        else:
            assert any("could not open" in x for x in res.errors), (e["formula"], res.errors)
    assert not refused_open, f"Excel opened forms that F-PARSE refuses: {refused_open}"


# ============================================================================= [C-sol-3] part 2: excel.py recalc

@pytest.mark.excel
def test_recalc_of_a_workbook_excel_cannot_open_is_x_open(tmp_path: Path) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "calc"
    ws["D39"], ws["A1"] = 1, "=D39+"
    path = tmp_path / "refused.xlsx"
    wb.save(path)
    sha = common.file_sha256(path)
    proc = run_cli("recalc", str(path), "--json", str(tmp_path / "recalc.json"), timeout=600)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    assert "X-OPEN" in out and "Excel started but could not open or calculate refused.xlsx" in out, out
    assert "not recalculated" not in out, out
    assert excel.X_OPEN_NOTE in out, out
    assert "audit" in out and "--strict" in out and "F-PARSE" in out, out
    report = json.loads((tmp_path / "recalc.json").read_text(encoding="utf-8"))
    assert report["exit_code"] == 1 and report["status"] == "fail"
    assert [f["rule"] for f in report["findings"]] == ["X-OPEN"]
    assert report["findings"][0]["severity"] == "error"
    assert common.file_sha256(path) == sha, "the workbook changed"


def test_x_open_finding_texts(tmp_path: Path) -> None:
    """No Excel: the X-OPEN message and fix of plan section 5.1, with and without a model folder."""
    book = tmp_path / "m" / "model.xlsx"
    long_error = "Excel could not open " + "x" * 300
    f = excel.x_open_finding(book, [long_error], tmp_path / "m")
    assert f.rule == "X-OPEN" and f.severity == "error"
    head = "Excel started but could not open or calculate model.xlsx: "
    assert f.message.startswith(head) and len(f.message) == len(head) + excel.X_OPEN_ERROR_MAX
    assert f.message.endswith("...")
    # v6 (r5 verdict A-aera-6): the fix names the two causes the strict audit finds (F-PARSE, F-ARGS) and Excel
    assert f.fix.startswith("the most likely cause is a formula Excel rejects; the strict audit finds two such causes, "
                            "an incomplete formula (F-PARSE) and a listed function with too few or too many arguments "
                            "(F-ARGS): run ")
    assert f'audit "{book.as_posix()}" --strict, correct the expression in model.spec.json, rebuild (' in f.fix
    # v6 integrator (request v6_P8 R1): the rebuild between the correction and the check
    assert f'build "{(tmp_path / "m").as_posix()}" --force), then run ' in f.fix
    other = "; for another cause, open model.xlsx in Excel and read the message Excel shows"
    assert f.fix.endswith(f'check "{(tmp_path / "m").as_posix()}" again{other}')
    g = excel.x_open_finding(book, ["the Excel job did not finish within 600 s and was stopped"])
    assert g.message == ("Excel started but could not open or calculate model.xlsx: the Excel job did not finish "
                         "within 600 s and was stopped")
    assert g.fix.endswith(f'recalc "{book.as_posix()}" again{other}')
    assert "not recalculated" not in excel.X_OPEN_NOTE


def test_recalc_command_reports_a_failed_job_as_x_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    """No Excel: run_recalc with a job that ran and failed (exit 1, X-OPEN), timed out (X-OPEN), or was handed an
    Excel instance it did not start (unchanged: exit 3, 'not recalculated')."""
    import argparse
    from openpyxl import Workbook
    wb = Workbook()
    wb.active["A1"] = 1
    path = tmp_path / "book.xlsx"
    wb.save(path)
    monkeypatch.setattr(excel, "excel_status", lambda: excel.ExcelStatus(available=True, version="16.0"))

    def fake(errors: list[str], *, attached: bool = False, timed_out: bool = False):
        def recalc(*_a, **_k):
            return excel.RecalcResult(ok=False, saved_path=None, input_sha256="0" * 64, output_sha256=None,
                                      error_cells=[], values={}, pid=None, elapsed_s=1.0, errors=errors,
                                      attached=attached, timed_out=timed_out)
        return recalc

    args = argparse.Namespace(workbook=str(path), read=[], no_save=False, out=None, json=None, quiet=False)
    monkeypatch.setattr(excel, "recalc", fake(["Excel could not open book.xlsx: Unable to get the Open property"]))
    assert excel.run_recalc(args) == 1
    out = capsys.readouterr().out
    assert "X-OPEN" in out and "not recalculated" not in out and excel.X_OPEN_NOTE in out, out
    monkeypatch.setattr(excel, "recalc", fake(["the Excel job did not finish within 600 s and was stopped"],
                                              timed_out=True))
    assert excel.run_recalc(args) == 1
    out = capsys.readouterr().out
    assert "X-OPEN" in out and "did not finish within 600 s" in out, out
    monkeypatch.setattr(excel, "recalc", fake(["attached to an Excel instance this job did not start (PID 1)"],
                                              attached=True))
    assert excel.run_recalc(args) == common.EXIT_NOT_AVAILABLE
    out = capsys.readouterr().out
    assert "X-OPEN" not in out and "not recalculated: attached to an Excel instance" in out, out


# ============================================================================= [C-sol-10]: excel.py run_job

class _FakePopen:
    """A job that never finishes: the first communicate() raises TimeoutExpired. With `pid`, the job writes its
    PID file first, as excel_job.ps1 does as its first action after Excel starts."""

    write_pid: int | None = None

    def __init__(self, cmd, **_kw):
        self.cmd = cmd
        self.returncode = None
        self.calls = 0
        if self.write_pid is not None:
            job = json.loads(Path(cmd[cmd.index("-Job") + 1]).read_text(encoding="utf-8"))
            Path(job["pid_file"]).write_text(str(self.write_pid), encoding="utf-8")

    def communicate(self, timeout=None):
        self.calls += 1
        if self.calls == 1:
            raise subprocess.TimeoutExpired(self.cmd, timeout)
        self.returncode = 1
        return b"", b""

    def kill(self):
        self.returncode = 1


def _patch_job(monkeypatch: pytest.MonkeyPatch, kills: list[int], *, write_pid: int | None,
               alive: set[int] = frozenset()) -> None:
    @contextmanager
    def no_lock(timeout_s=None):
        yield Path("excel.lock")

    popen = type("Popen", (_FakePopen,), {"write_pid": write_pid})
    monkeypatch.setattr(excel, "_require_excel", lambda: excel.ExcelStatus(available=True, version="16.0"))
    monkeypatch.setattr(excel, "excel_lock", no_lock)
    monkeypatch.setattr(excel, "excel_pids", lambda: set())
    monkeypatch.setattr(excel, "_automation_excel_pids", lambda: {99999: "EXCEL.EXE /automation -Embedding"})
    monkeypatch.setattr(excel, "_kill", lambda pid: kills.append(pid) or True)
    monkeypatch.setattr(excel, "pid_alive", lambda pid: pid in alive)
    monkeypatch.setattr(excel.subprocess, "Popen", popen)


def test_timeout_without_a_pid_stops_no_excel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    wb.active["A1"] = 1
    path = tmp_path / "book.xlsx"
    wb.save(path)
    kills: list[int] = []
    _patch_job(monkeypatch, kills, write_pid=None)
    res = excel.run_job(path, [{"op": "recalc"}], timeout_s=1)
    assert res.timed_out and not res.ok and res.pid is None
    assert kills == [], f"Excel PIDs stopped without a recorded PID: {kills}"
    assert not res.killed
    assert TIMEOUT_NOTE in res.notes, res.notes
    assert "did not finish within 1 s" in res.message()


def test_timeout_with_a_recorded_pid_stops_that_excel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The job's own recorded PID keeps the orphan rule: it is stopped; the other automation Excel is not."""
    from openpyxl import Workbook
    wb = Workbook()
    wb.active["A1"] = 1
    path = tmp_path / "book.xlsx"
    wb.save(path)
    kills: list[int] = []
    _patch_job(monkeypatch, kills, write_pid=4242, alive={4242, 99999})
    res = excel.run_job(path, [{"op": "recalc"}], timeout_s=1)
    assert res.timed_out and res.pid == 4242
    assert kills == [4242] and res.killed
    assert "Excel PID 4242 was still running after the job and was stopped" in res.notes
    assert not any("99999" in n for n in res.notes), res.notes


# ============================================================================= [A-aera-8, A-aera-9] part 1: workbook.py

LOOKALIKES = ("=pending", "#N/A")


def _starter_with_lookalike_ids(tmp_path: Path) -> Path:
    """rotm.py init --template starter; the text key line_id of the first two rows holds '=pending' and '#N/A';
    the contract agreed on the task-file channel; landed; built without Power Query."""
    root = tmp_path / "starter"
    proc = run_cli("init", str(root), "--template", "starter", "--name", "lookalike", timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    with (root / "data" / "lines.csv").open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows[0][0] == "line_id"
    rows[1][0], rows[2][0] = LOOKALIKES
    src = tmp_path / "extract" / "lines.csv"
    src.parent.mkdir()
    with src.open("w", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerows(rows)
    task = tmp_path / "task.md"
    reply = "agreed, land it as proposed."
    task.write_text(f"The user's reply to contracts/lines.contract.md: {reply}\n", encoding="utf-8")
    proc = run_cli("contract", "agree", str(root), "--table", "lines", "--reply", reply, "--by", "the user",
                   "--channel", "task-file", "--source-file", str(task), timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    proc = run_cli("land", str(root), "--table", "lines", "--csv", str(src), timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    proc = run_cli("build", str(root), "--no-power-query", env={"ROTM_NO_EXCEL": "1"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return root


def _audit(target: Path) -> dict:
    proc = run_cli("audit", str(target), "--strict", "--json", "-", "--no-record", timeout=600)
    assert proc.returncode in (0, 1), proc.stdout + proc.stderr
    return json.loads(proc.stdout)


def _cells(report: dict, rule: str) -> set[tuple[str, str]]:
    return {(f["sheet"], c) for f in report["findings"] if f["rule"] == rule for c in (f["cells"] or [f["cell"]])}


def _stored_types(path: Path, sheet: str, coords: list[str]) -> dict[str, str]:
    import openpyxl
    ws = openpyxl.load_workbook(path)[sheet]
    return {c: ws[c].data_type for c in coords}


def test_text_that_looks_like_a_formula_or_an_error_is_text(tmp_path: Path) -> None:
    root = _starter_with_lookalike_ids(tmp_path)
    book = root / "model.xlsx"
    assert _stored_types(book, "raw_lines", ["A2", "A3"]) == {"A2": "s", "A3": "s"}
    info = workbook.open_workbook(book)
    raw = info.sheet("raw_lines")
    for coord, text in zip(("A2", "A3"), LOOKALIKES):
        c = raw.cell(coord)
        assert c.value == text and c.formula is None and not c.is_formula and not c.is_error, (coord, c)
    report = _audit(root)
    assert not _cells(report, "R-FORMULA"), report["findings"]
    assert not _cells(report, "V-ERROR"), report["findings"]
    # a real formula on the raw sheet and a real error value are still reported
    import openpyxl
    wbk = openpyxl.load_workbook(book)
    ws = wbk["raw_lines"]
    ws["E2"] = "=1+1"
    ws["E3"] = "#N/A"                          # openpyxl stores an error name as an error value (t="e")
    wbk.save(book)
    assert _stored_types(book, "raw_lines", ["A2", "E2", "E3"]) == {"A2": "s", "E2": "f", "E3": "e"}
    report = _audit(root)
    assert ("raw_lines", "E2") in _cells(report, "R-FORMULA"), report["findings"]
    assert ("raw_lines", "A2") not in _cells(report, "R-FORMULA")
    assert ("raw_lines", "E3") in _cells(report, "V-ERROR"), report["findings"]
    assert ("raw_lines", "A3") not in _cells(report, "V-ERROR")


@pytest.mark.excel
def test_text_that_looks_like_a_formula_or_an_error_stays_text_after_excel_saves(tmp_path: Path) -> None:
    root = _starter_with_lookalike_ids(tmp_path)
    book = root / "model.xlsx"
    res = excel.recalc(book)
    assert res.ok, res.errors
    assert _stored_types(book, "raw_lines", ["A2", "A3"]) == {"A2": "s", "A3": "s"}
    info = workbook.open_workbook(book)
    assert info.has_cached_values
    fetched = [c for s in info.sheets if s.name.startswith("h_") for c in s.formulas() if c.cached in LOOKALIKES]
    assert fetched and all(c.cached_type == "s" and not c.is_error for c in fetched), \
        [(c.sheet, c.coord, c.cached, c.cached_type) for c in fetched]
    report = _audit(root)
    assert not _cells(report, "R-FORMULA"), report["findings"]
    assert not _cells(report, "V-ERROR"), report["findings"]
    # a real error value calculated by Excel is still V-ERROR
    import openpyxl
    wbk = openpyxl.load_workbook(book)
    calc = "calc"
    wbk[calc]["Z200"] = "=1/0"
    wbk.save(book)
    res = excel.recalc(book)
    assert res.ok, res.errors
    report = _audit(root)
    assert (calc, "Z200") in _cells(report, "V-ERROR"), report["findings"]
    assert not any(c in _cells(report, "V-ERROR") for c in {(c.sheet, c.coord) for c in fetched})

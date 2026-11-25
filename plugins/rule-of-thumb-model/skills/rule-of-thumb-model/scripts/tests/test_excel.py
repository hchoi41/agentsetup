"""Tests of the Excel bridge lib/excel.py and lib/excel_job.ps1 (design section 12). Owner: CORE-X.

Tests marked `excel` start Microsoft Excel through COM and are skipped where Excel is not available
(conftest.py). They take the machine-wide Excel lock, so they wait while another job runs.
The other tests need no Excel and run everywhere.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

import pytest

from lib import common, excel

SCRIPTS = Path(__file__).resolve().parents[1]


def run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", timeout=600,
                          env={**os.environ, **(env or {})})


# ----------------------------------------------------------------------------- fixtures

def make_simple(path: Path) -> Path:
    """calc: 2 inputs and a product; three error formulas; checks: a check that is 0 when calc!A3 is 6."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "calc"
    ws["A1"], ws["A2"], ws["A3"] = 2, 3, "=A1*A2"
    ws["A4"], ws["A5"], ws["A6"] = "=1/0", "=NA()", '=A1+"x"'
    ws["B1"] = "text"
    chk = wb.create_sheet("checks")
    chk["B2"], chk["C2"] = "Master check", "=--(calc!A3<>6)"
    wb.save(path)
    return path


def make_clean(path: Path) -> Path:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "calc"
    ws["A1"], ws["A2"], ws["A3"] = 2, 3, "=A1*A2"
    chk = wb.create_sheet("checks")
    chk["B2"], chk["C2"] = "Master check", "=--(calc!A3<>6)"
    wb.save(path)
    return path


def make_rich(path: Path) -> Path:
    """A workbook with a table, a chart, a defined name, conditional formats and data validation (F08a parts)."""
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference
    from openpyxl.formatting.rule import CellIsRule
    from openpyxl.styles import Font, PatternFill
    from openpyxl.workbook.defined_name import DefinedName
    from openpyxl.worksheet.datavalidation import DataValidation
    from openpyxl.worksheet.table import Table

    wb = Workbook()
    data = wb.active
    data.title = "data"
    data.append(["id", "region", "amount"])
    for i in range(1, 11):
        data.append([i, ["East", "West"][i % 2], i * 100])
    data.add_table(Table(displayName="tbl_data", ref="A1:C11"))
    chart = BarChart()
    chart.add_data(Reference(data, min_col=3, min_row=1, max_row=11), titles_from_data=True)
    data.add_chart(chart, "E2")
    calc = wb.create_sheet("calc")
    calc["A1"], calc["B1"] = "rate", 0.1
    for r in range(2, 8):
        calc.cell(r, 1, f"Line {r}")
        calc.cell(r, 2, f"=data!C{r}*$B$1")
        calc.cell(r, 3, f"=B{r}*2")
        calc.cell(r, 2).font = Font(name="Calibri", size=11)
    calc["E1"] = "=SUM(B2:B7)"
    calc.conditional_formatting.add("B2:B7", CellIsRule(operator="greaterThan", formula=["50"],
                                                        fill=PatternFill("solid", fgColor="FFC7CE")))
    dv = DataValidation(type="list", formula1='"East,West"', allow_blank=True)
    calc.add_data_validation(dv)
    dv.add("F2:F5")
    wb.defined_names["rate"] = DefinedName("rate", attr_text="calc!$B$1")
    wb.save(path)
    return path


def formulas(path: Path) -> dict[str, str]:
    from openpyxl import load_workbook
    wb = load_workbook(path)
    return {f"{ws.title}!{c.coordinate}": c.value for ws in wb.worksheets for row in ws.iter_rows() for c in row
            if isinstance(c.value, str) and c.value.startswith("=")}


def write_csv(path: Path, n: int) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("id,region,amount\n" + "".join(f"{i:04d},R{i % 3},{i * 10}\n" for i in range(1, n + 1)))


def m_text_for(csv_path: Path) -> str:
    win = str(csv_path).replace("/", "\\")
    return ("let\n"
            f'    Source = Csv.Document(File.Contents("{win}"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),\n'
            "    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),\n"
            '    Typed = Table.TransformColumnTypes(Promoted, {{"id", type text}, {"region", type text}, '
            '{"amount", type number}}, "en-US")\n'
            "in\n    Typed")


def make_raw_model(path: Path, n_raw: int, helper_rows: int) -> Path:
    """raw_sales (a static table from A1, design 7.1), h_sales (position-based helpers), checks."""
    from openpyxl import Workbook
    from openpyxl.worksheet.table import Table
    wb = Workbook()
    raw = wb.active
    raw.title = "raw_sales"
    raw.append(["id", "region", "amount"])
    for i in range(1, n_raw + 1):
        raw.append([f"{i:04d}", f"R{i % 3}", i * 10])
    raw.add_table(Table(displayName="tbl_raw_sales", ref=f"A1:C{n_raw + 1}"))
    h = wb.create_sheet("h_sales")
    h.append(["row", "in_raw", "id", "amount"])
    for r in range(2, helper_rows + 2):
        h.cell(r, 1, "=ROW()")
        h.cell(r, 2, f'=--(INDEX(raw_sales!$A:$A,A{r})<>"")')
        h.cell(r, 3, f'=INDEX(raw_sales!$A:$A,A{r})&""')
        h.cell(r, 4, f"=INDEX(raw_sales!$C:$C,A{r})")
    c = wb.create_sheet("checks")
    c["B2"], c["C2"] = "covered rows", "=SUM(h_sales!B:B)"
    c["B3"], c["C3"] = "raw rows", '=COUNTA(raw_sales!A:A)-1'
    c["B4"], c["C4"] = "helper total", "=SUM(h_sales!D:D)"
    wb.save(path)
    return path


@pytest.fixture
def lock_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private lock file, so lock tests never block the machine-wide Excel lock."""
    path = tmp_path / "locks" / "excel.lock"
    monkeypatch.setenv("ROTM_EXCEL_LOCK", str(path))
    return path


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=60)
    return proc.pid


# ----------------------------------------------------------------------------- lock (no Excel needed)

def test_lock_path_from_env_and_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ROTM_EXCEL_LOCK", str(tmp_path / "x.lock"))
    assert excel.lock_path() == tmp_path / "x.lock"
    monkeypatch.delenv("ROTM_EXCEL_LOCK")
    monkeypatch.setenv("ROTM_STATE_DIR", str(tmp_path / "state"))      # a moved state folder keeps the shared lock
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert excel.lock_path() == tmp_path / "local" / "rule-of-thumb-model" / "excel.lock"


def test_lock_records_owner_and_is_removed_after(lock_in_tmp: Path) -> None:
    with excel.excel_lock(timeout_s=5) as path:
        assert path == lock_in_tmp
        owner = excel.read_lock(path)
        assert owner["pid"] == os.getpid()
        assert owner["host"] and owner["started"]
    assert not lock_in_tmp.exists()


def test_lock_is_reentrant_in_one_process(lock_in_tmp: Path) -> None:
    with excel.excel_lock(timeout_s=2):
        with excel.excel_lock(timeout_s=2):
            assert lock_in_tmp.exists()
        assert lock_in_tmp.exists()
    assert not lock_in_tmp.exists()


def test_lock_released_after_an_exception(lock_in_tmp: Path) -> None:
    with pytest.raises(RuntimeError):
        with excel.excel_lock(timeout_s=2):
            raise RuntimeError("boom")
    assert not lock_in_tmp.exists()
    with excel.excel_lock(timeout_s=2):
        pass


def test_lock_held_by_another_process_gives_busy_then_stale_lock_is_removed(lock_in_tmp: Path, tmp_path: Path) -> None:
    flag = tmp_path / "held.flag"
    code = (f"import sys, time; sys.path.insert(0, {str(SCRIPTS)!r}); from lib import excel\n"
            f"with excel.excel_lock(timeout_s=10):\n"
            f"    open({str(flag)!r}, 'w').write('held')\n"
            f"    time.sleep(60)\n")
    holder = subprocess.Popen([sys.executable, "-P", "-B", "-c", code])   # -B: no __pycache__ in the plugin (O-1)
    try:
        deadline = time.monotonic() + 30
        while not flag.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert flag.exists(), "the holder process did not take the lock"
        with pytest.raises(common.ExcelBusy) as info:
            with excel.excel_lock(timeout_s=1):
                pass
        assert f"PID {holder.pid}" in info.value.message
        assert info.value.exit_code == common.EXIT_BUSY
        assert "ROTM_EXCEL_TIMEOUT" in (info.value.fix or "")
    finally:
        holder.kill()
        holder.wait(timeout=30)
    # The holder was killed with the lock file still on disk: its owner is gone, so the lock is stale.
    assert lock_in_tmp.exists()
    start = time.monotonic()
    with excel.excel_lock(timeout_s=10):
        assert excel.read_lock(lock_in_tmp)["pid"] == os.getpid()
    assert time.monotonic() - start < 5


def test_lock_of_a_dead_pid_written_by_hand_is_removed(lock_in_tmp: Path) -> None:
    lock_in_tmp.parent.mkdir(parents=True)
    lock_in_tmp.write_text(json.dumps({"rotm_excel_lock": 1, "pid": _dead_pid(), "host": __import__("socket").gethostname(),
                                       "started": "2026-11-25T00:00:00+09:00"}), encoding="utf-8")
    with excel.excel_lock(timeout_s=5):
        assert excel.read_lock(lock_in_tmp)["pid"] == os.getpid()


def test_unreadable_lock_is_stale_only_when_old(lock_in_tmp: Path) -> None:
    lock_in_tmp.parent.mkdir(parents=True)
    lock_in_tmp.write_text("", encoding="utf-8")
    with pytest.raises(common.ExcelBusy):
        with excel.excel_lock(timeout_s=1):
            pass
    old = time.time() - excel.STALE_UNREADABLE_LOCK_S - 5
    os.utime(lock_in_tmp, (old, old))
    with excel.excel_lock(timeout_s=5):
        pass


def test_timeout_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROTM_EXCEL_TIMEOUT", "42")
    assert excel._timeout() == 42.0
    monkeypatch.setenv("ROTM_EXCEL_TIMEOUT", "not a number")
    assert excel._timeout() == excel.DEFAULT_TIMEOUT_S
    assert excel._timeout(7) == 7.0


# ----------------------------------------------------------------------------- processes, parsing (no Excel needed)

def test_parse_tasklist_output() -> None:
    text = ('"EXCEL.EXE","1234","Console","1","90,000 K"\r\n'
            '"EXCEL.EXE","5678","Console","1","1,000 K"\r\n'
            '"notepad.exe","42","Console","1","1 K"\r\n')
    assert excel._parse_tasklist(text) == {1234, 5678}
    assert excel._parse_tasklist("INFO: No tasks are running which match the specified criteria.\r\n") == set()


def test_pid_alive() -> None:
    assert excel.pid_alive(os.getpid())
    assert not excel.pid_alive(_dead_pid())
    assert not excel.pid_alive(None)
    assert not excel.pid_alive(0)


def test_job_log_and_orphans() -> None:
    assert excel.read_job_log() == []                     # each test has its own state folder (conftest)
    path = excel.job_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"at": "2026-11-25T01:00:00+09:00", "pid": _dead_pid(), "ok": True}) + "\nnot json\n",
                    encoding="utf-8")
    entries = excel.read_job_log()
    assert len(entries) == 1
    assert excel.orphan_pids(entries) == []
    assert excel.read_job_log(since="2026-11-25T02:00:00+09:00") == []


def test_png_size(tmp_path: Path) -> None:
    import struct
    import zlib
    raw = b"\x00\x00\x00\x00"
    png = (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
           + struct.pack(">I", zlib.crc32(b"IHDR" + struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)))
           + struct.pack(">I", len(zlib.compress(raw))) + b"IDAT" + zlib.compress(raw) + b"\x00" * 4)
    (tmp_path / "a.png").write_bytes(png)
    (tmp_path / "b.png").write_bytes(b"not a png")
    assert excel.png_size(tmp_path / "a.png") == (1, 1)
    assert excel.png_size(tmp_path / "b.png") is None


# ----------------------------------------------------------------------------- operations (no Excel needed)

def test_address_groups_stay_under_255_characters() -> None:
    ranges = [f"B{r}:J{r}" for r in range(1000, 1400)]
    groups = excel.address_groups(ranges)
    assert all(len(g) < 255 for g in groups)
    assert ",".join(groups).split(",") == ranges


def test_style_operation_is_normalised() -> None:
    ops = excel.normalize_operations([{"op": "style", "sheet": "calc", "ranges": ["$B$4:$J$4", "b7"],
                                       "font": {"name": "Arial", "size": 8, "bold": True, "color": "FF000000"},
                                       "fill": "#fff2cc", "number_format": "#,##0", "border": "top thin 000000",
                                       "align": "right"}])
    op = ops[0]
    assert op["addresses"] == ["B4:J4,B7"]
    assert op["font"] == {"name": "Arial", "size": 8, "bold": True, "color": "000000"}
    assert op["fill"] == "FFF2CC"
    assert op["border"]["top"] == {"indices": [8, 12], "line": 1, "weight": 2, "color": "000000"}
    assert op["align"] == {"horizontal": -4152}


def test_style_fill_none_and_font_colour_automatic() -> None:
    op = excel.normalize_operations([{"op": "style", "sheet": "s", "ranges": "A1", "fill": None,
                                      "font": {"color": None}}])[0]
    assert "fill" in op and op["fill"] is None
    assert "color" in op["font"] and op["font"]["color"] is None


def test_border_forms() -> None:
    none = excel.normalize_operations([{"op": "style", "sheet": "s", "ranges": "A1", "border": "none"}])[0]["border"]
    assert set(none) == {"top", "bottom", "left", "right"} and none["top"]["line"] == -4142
    full = excel.normalize_operations([{"op": "style", "sheet": "s", "ranges": "A1",
                                        "border": {"all": {"style": "medium", "color": "808080"}}}])[0]["border"]
    assert full["left"] == {"indices": [7, 11], "line": 1, "weight": -4138, "color": "808080"}


@pytest.mark.parametrize("op, words", [
    ({"op": "frobnicate"}, "unknown operation"),
    ({"op": "style", "sheet": "s", "ranges": "A1"}, "at least one of"),
    ({"op": "style", "ranges": "A1", "fill": "FFF2CC"}, "give 'sheet'"),
    ({"op": "style", "sheet": "s", "ranges": "A1", "fill": "yellow"}, "six hex digits"),
    ({"op": "style", "sheet": "s", "ranges": "other!A1", "fill": "FFF2CC"}, "names sheet 'other'"),
    ({"op": "style", "sheet": "s", "ranges": "A1:", "fill": "FFF2CC"}, "not a range address"),
    ({"op": "style", "sheet": "s", "ranges": "A1", "border": "top wiggly"}, "border style"),
    ({"op": "value", "sheet": "s", "cell": "A1", "value": "=1+1"}, "use a 'formula' operation"),
    ({"op": "value", "sheet": "s", "cell": "A1", "value": {"x": 1}}, "must be text"),
    ({"op": "formula", "sheet": "s", "cell": "A1"}, "give 'formula'"),
    ({"op": "formula", "sheet": "s", "cell": "A", "formula": "=1"}, "not an address"),
    ({"op": "fill_down", "sheet": "s", "from_row": 5, "to_row": 4, "last_col": "C"}, "to_row must be at least"),
    ({"op": "fill_down", "sheet": "s", "from_row": 2, "to_row": 4, "last_col": "?"}, "not a column"),
    ({"op": "add_sheet", "name": "bad/name"}, "sheet name"),
    ({"op": "clear", "sheet": "s", "ranges": "A1", "what": "everything"}, "use contents, all or formats"),
    ({"op": "pq_attach", "sheet": "raw", "table_name": "tbl raw", "query_name": "q", "m_text": "let"}, "no spaces"),
])
def test_bad_operations_are_refused_with_the_key_to_change(op: dict, words: str) -> None:
    with pytest.raises(common.Refusal) as info:
        excel.normalize_operations([op])
    assert words in info.value.message
    assert info.value.message.startswith("operation 1 (")


def test_formula_prefixes_are_stripped_outside_text() -> None:
    op = excel.normalize_operations([{"op": "formula", "sheet": "s", "cell": "$D$7",
                                      "formula": '_xlfn.XLOOKUP(A1,B:B,C:C,"_xlfn.keep")'}])[0]
    assert op["cell"] == "D7"
    assert op["formula"] == '=XLOOKUP(A1,B:B,C:C,"_xlfn.keep")'


def test_values_that_excel_would_convert_are_written_as_text() -> None:
    ops = excel.normalize_operations([
        {"op": "value", "sheet": "s", "cell": "A1", "value": "00123"},
        {"op": "value", "sheet": "s", "cell": "A2", "value": "-abc"},
        {"op": "value", "sheet": "s", "cell": "A3", "value": "Helper for calc!D7"},
        {"op": "value", "sheet": "s", "cell": "A4", "value": dt.date(2026, 1, 31)},
        {"op": "value", "sheet": "s", "cell": "A5", "value": None},
    ])
    assert [o["as_text"] for o in ops] == [True, True, False, False, False]
    assert ops[3]["value"] == 46053.0          # 2026-01-31 in Excel's 1900 date system
    assert ops[4]["value"] is None


def test_excel_serial_matches_known_dates() -> None:
    assert excel.excel_serial(dt.date(1900, 3, 1)) == 61
    assert excel.excel_serial(dt.datetime(2026, 1, 31, 12, 0)) == 46053.5


def test_fill_down_columns_by_letter_or_number() -> None:
    op = excel.normalize_operations([{"op": "fill_down", "sheet": "h", "from_row": 601, "to_row": 900,
                                      "first_col": "A", "last_col": "H"}])[0]
    assert (op["first_col"], op["last_col"]) == (1, 8)


def test_hex_color() -> None:
    assert excel.hex_color("ff000000") == "000000"
    assert excel.hex_color("none") is None
    with pytest.raises(ValueError):
        excel.hex_color("12345")


# ----------------------------------------------------------------------------- without Excel (forced)

def test_no_excel_status_and_job_refusal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    status = excel.excel_status()
    assert not status.available and "ROTM_NO_EXCEL=1" in status.reason
    wb = make_clean(tmp_path / "m.xlsx")
    with pytest.raises(common.EnvUnavailable) as info:
        excel.run_job(wb, [{"op": "recalc"}])
    assert "Excel is not available" in info.value.message
    assert info.value.exit_code == common.EXIT_NOT_AVAILABLE


def test_bad_input_is_refused_before_excel_is_needed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    with pytest.raises(common.Refusal) as info:
        excel.run_job(tmp_path / "missing.xlsx", [{"op": "recalc"}])
    assert "no such file" in info.value.message
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    with pytest.raises(common.Refusal):
        excel.run_job(tmp_path / "notes.txt", [{"op": "recalc"}])
    wb = make_clean(tmp_path / "m.xlsm")
    with pytest.raises(common.Refusal) as info:
        excel.run_job(wb, [{"op": "recalc"}], save_as=tmp_path / "out.xlsx")
    assert "drop them" in info.value.message


def test_cli_without_excel_says_not_recalculated_and_not_rendered(tmp_path: Path) -> None:
    wb = make_clean(tmp_path / "m.xlsx")
    before = common.file_sha256(wb)
    proc = run_cli("recalc", str(wb), "--json", "-", env={"ROTM_NO_EXCEL": "1"})
    assert proc.returncode == common.EXIT_NOT_AVAILABLE, proc.stderr
    report = json.loads(proc.stdout)
    assert report["status"] == "not_verified" and report["verified"]["recalculated"] is False
    assert any("not recalculated" in n for n in report["notes"])
    assert common.file_sha256(wb) == before
    proc = run_cli("render", str(wb), env={"ROTM_NO_EXCEL": "1"})
    assert proc.returncode == common.EXIT_NOT_AVAILABLE
    assert "not rendered: the sheets were not looked at" in proc.stdout


def test_cli_refuses_a_missing_workbook(tmp_path: Path) -> None:
    proc = run_cli("recalc", str(tmp_path / "nope.xlsx"))
    assert proc.returncode == common.EXIT_REFUSED
    assert "no such file" in proc.stderr and "Traceback" not in proc.stderr


# ----------------------------------------------------------------------------- with Excel

def assert_own_excel_ended(res: Any, since: str) -> None:
    """The Excel this job started has ended, and no Excel recorded in this test's job log is still running.
    Other Excel processes on the PC (Max's own, or another program's) are not looked at (J-2)."""
    assert res.pid and not getattr(res, "attached", False), "the job did not start its own Excel"
    assert not excel.pid_alive(res.pid), "the Excel this job started is still running"
    assert excel.orphan_pids(excel.read_job_log(since=since)) == []


@pytest.mark.excel
def test_recalc_writes_values_finds_errors_and_leaves_no_excel(tmp_path: Path) -> None:
    from openpyxl import load_workbook
    wb = make_simple(tmp_path / "simple.xlsx")
    since = common.now_iso()
    res = excel.recalc(wb, read=["checks!C2", "calc!A3", "calc!A1:B2", "calc!A4", "nosheet!A1"])
    assert res.ok, res.errors
    assert_own_excel_ended(res, since)
    assert res.values["checks!C2"] == 0 and res.values["calc!A3"] == 6
    assert res.values["calc!A1:B2"] == [[2, "text"], [3, None]]
    assert excel.is_error(res.values["calc!A4"]) and res.values["calc!A4"] == "#DIV/0!"
    assert "nosheet" in res.problems["nosheet!A1"]
    assert sorted(res.error_cells) == [("calc", "A4", "#DIV/0!"), ("calc", "A5", "#N/A"), ("calc", "A6", "#VALUE!")]
    assert res.error_count == 3
    assert res.calc_mode == "automatic"
    assert res.excel_version and res.excel_version.startswith("16")
    assert res.output_sha256 and res.output_sha256 != res.input_sha256
    cached = load_workbook(wb, data_only=True)
    assert cached["calc"]["A3"].value == 6 and cached["checks"]["C2"].value == 0
    logged = excel.read_job_log()
    assert logged and logged[-1]["pid"] == res.pid and logged[-1]["ok"] is True
    assert excel.orphan_pids(logged) == []


@pytest.mark.excel
def test_an_excel_that_another_program_starts_during_the_job_does_not_fail_the_test(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """J-2: an Excel process the test did not start appears on the PC while the job runs (here a PID added to
    the process list after the job's own first look). The test's assertions stay on its own job."""
    wb = make_clean(tmp_path / "clean.xlsx")
    real = excel.excel_pids
    looks = {"n": 0}
    other = 4_194_300                            # not a PID this test or its job started

    def pids_with_another_excel() -> set[int]:
        looks["n"] += 1
        return real() | ({other} if looks["n"] > 1 else set())

    monkeypatch.setattr(excel, "excel_pids", pids_with_another_excel)
    since = common.now_iso()
    res = excel.recalc(wb, read=["calc!A3"])
    assert res.ok and res.values["calc!A3"] == 6
    assert_own_excel_ended(res, since)
    assert other in excel.excel_pids(), "the other Excel is still on the process list"


@pytest.mark.excel
def test_recalc_without_save_leaves_the_file_unchanged(tmp_path: Path) -> None:
    wb = make_clean(tmp_path / "clean.xlsx")
    sha = common.file_sha256(wb)
    res = excel.recalc(wb, save=False, read=["calc!A3"])
    assert res.ok and res.values["calc!A3"] == 6
    assert res.saved_path is None and res.output_sha256 is None
    assert common.file_sha256(wb) == sha


@pytest.mark.excel
def test_recalc_to_another_file_keeps_the_original(tmp_path: Path) -> None:
    wb = make_clean(tmp_path / "clean.xlsx")
    sha = common.file_sha256(wb)
    out = tmp_path / "sub" / "recalculated.xlsx"
    res = excel.recalc(wb, out=out)
    assert res.ok and res.saved_path == out and out.is_file()
    assert common.file_sha256(wb) == sha


@pytest.mark.excel
def test_read_cells_and_find_errors(tmp_path: Path) -> None:
    wb = make_simple(tmp_path / "simple.xlsx")
    assert excel.read_cells(wb, ["calc!A3"], recalc_first=True) == {"calc!A3": 6}
    found = excel.find_errors(wb, recalc_first=True)
    assert ("calc", "A4", "#DIV/0!") in found and len(found) == 3
    with pytest.raises(common.Refusal):
        excel.read_cells(wb, ["nosheet!A1"])


@pytest.mark.excel
def test_render_one_png_per_sheet_including_hidden_and_charts(tmp_path: Path) -> None:
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference
    from openpyxl.styles import Font, PatternFill
    path = tmp_path / "look.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "calc"
    ws.sheet_view.showGridLines = False
    for r in range(1, 21):
        for c in range(1, 7):
            cell = ws.cell(r, c, r * c if r > 1 else f"Col {c}")
            cell.font = Font(name="Arial", size=8, bold=(r == 1))
            if r == 1:
                cell.fill = PatternFill("solid", fgColor="D9D9D9")
    chart = BarChart()
    chart.add_data(Reference(ws, min_col=2, min_row=1, max_row=10), titles_from_data=True)
    ws.add_chart(chart, "H2")
    hidden = wb.create_sheet("notes")
    hidden["A1"] = "hidden sheet"
    hidden.sheet_state = "hidden"
    wb.create_sheet("empty")
    long = wb.create_sheet("raw_long")
    for r in range(1, 400):
        long.cell(r, 1, r)
    chart_sheet = wb.create_chartsheet("chart_tab")
    tab_chart = BarChart()
    tab_chart.add_data(Reference(ws, min_col=3, min_row=1, max_row=10), titles_from_data=True)
    chart_sheet.add_chart(tab_chart)
    wb.save(path)
    out = tmp_path / "render"
    items = excel.render_sheets(path, out)
    assert [i.sheet for i in items] == ["calc", "notes", "empty", "raw_long", "chart_tab"]
    for i in items:
        assert i.problem is None and i.path is not None and i.path.is_file()
        assert excel.png_size(i.path) == (i.width_px, i.height_px)
        assert i.width_px > 0 and i.height_px > 0
    calc = items[0]
    assert calc.path.name == "calc.png"
    assert calc.path.stat().st_size > 5000, "the calc render looks blank"
    assert calc.last_col >= 8, "the chart at H2 is inside the rendered range"
    assert items[3].truncated and items[3].range == "A1:A200" and items[3].last_row == 399
    assert items[4].path.stat().st_size > 2000, "the chart sheet render looks blank"
    index = json.loads((out / "render.json").read_text(encoding="utf-8"))
    assert [f["file"] for f in index["files"]] == ["calc.png", "notes.png", "empty.png", "raw_long.png", "chart_tab.png"]
    assert index["note"] == "rendered, not looked at by code"
    assert common.file_sha256(path) == index["sha256"], "a render does not change the workbook"
    # A second render of one sheet keeps the other files; a full render after a sheet is removed deletes its PNG.
    excel.render_sheets(path, out, sheets=["calc"])
    assert (out / "raw_long.png").is_file()
    from openpyxl import load_workbook
    wb2 = load_workbook(path)
    del wb2["raw_long"]
    wb2.save(path)
    excel.render_sheets(path, out)
    assert not (out / "raw_long.png").exists()
    with pytest.raises(common.Refusal):
        excel.render_sheets(path, out, sheets=["no such sheet"])


@pytest.mark.excel
def test_apply_edits_keeps_tables_charts_names_conditional_formats_and_validation(tmp_path: Path) -> None:
    from openpyxl import load_workbook
    src = make_rich(tmp_path / "rich.xlsx")
    sha = common.file_sha256(src)
    before_formulas = formulas(src)
    inv_before = excel.inventory(src)
    counts_before = excel.inventory_counts(inv_before)
    assert counts_before["charts"] == 1 and counts_before["tables"] == 1
    assert counts_before["format_conditions"] == 1 and counts_before["validation_cells"] == 4
    assert "rate" in inv_before["names"]
    out = tmp_path / "rich_edited.xlsx"
    res = excel.apply_edits(src, [
        {"op": "style", "sheet": "calc", "ranges": ["B2:C7"], "font": {"name": "Arial", "size": 8, "color": "000000"},
         "fill": "FFF2CC", "number_format": "#,##0.0", "border": "top thin 000000", "align": "right"},
        {"op": "formula", "sheet": "calc", "cell": "D2", "formula": "=_xlfn.XLOOKUP(2,data!A2:A11,data!C2:C11)"},
        {"op": "value", "sheet": "calc", "cell": "G1", "value": "note"},
        {"op": "formula", "sheet": "calc", "cell": "H2", "formula": "=C2+1"},
        {"op": "fill_down", "sheet": "calc", "from_row": 2, "to_row": 7, "first_col": "H", "last_col": "H"},
        {"op": "add_sheet", "name": "split_helpers", "after": "calc"},
        {"op": "value", "sheet": "split_helpers", "cell": "A1", "value": "00123"},
    ], out=out, recalc_after=True)
    assert res.ok, res.errors
    assert res.saved_path == out
    assert common.file_sha256(src) == sha, "the original workbook was changed"
    inv_after = excel.inventory(out)
    counts_after = excel.inventory_counts(inv_after)
    assert counts_after == {**counts_before, "sheets": counts_before["sheets"] + 1}
    assert inv_after["names"] == inv_before["names"]
    with zipfile.ZipFile(out) as z:
        assert any(n.startswith("xl/charts/chart") for n in z.namelist())
    after_formulas = formulas(out)
    intended = {"calc!D2", *(f"calc!H{r}" for r in range(2, 8))}
    assert {k: v for k, v in after_formulas.items() if k not in intended} == before_formulas
    assert after_formulas["calc!H7"] == "=C7+1"
    assert after_formulas["calc!D2"].replace("_xlfn.", "") == "=XLOOKUP(2,data!A2:A11,data!C2:C11)"
    wb = load_workbook(out)
    b2 = wb["calc"]["B2"]
    assert b2.font.name == "Arial" and b2.font.sz == 8
    assert b2.fill.fgColor.rgb.endswith("FFF2CC")
    assert b2.number_format == "#,##0.0"
    assert b2.border.top.style == "thin"
    assert b2.alignment.horizontal == "right"
    assert wb["calc"]["A2"].font.name != "Arial", "cells outside the ranges keep their style"
    assert wb["split_helpers"]["A1"].value == "00123"
    assert wb.sheetnames == ["data", "calc", "split_helpers"]
    cached = load_workbook(out, data_only=True)
    assert cached["calc"]["D2"].value == 200
    assert cached["calc"]["H2"].value == pytest.approx(21.0)


@pytest.mark.excel
def test_failed_edit_saves_nothing(tmp_path: Path) -> None:
    src = make_clean(tmp_path / "clean.xlsx")
    out = tmp_path / "out.xlsx"
    res = excel.apply_edits(src, [{"op": "value", "sheet": "calc", "cell": "B1", "value": 1},
                                  {"op": "formula", "sheet": "calc", "cell": "B2", "formula": "=SUM(("}], out=out)
    assert not res.ok
    assert "Excel refused the formula for calc!B2" in res.message()
    assert not out.exists()
    assert res.step("value")["ok"] is True and res.step("formula")["ok"] is False


@pytest.mark.excel
def test_power_query_attach_then_refresh_with_more_and_fewer_rows(tmp_path: Path) -> None:
    """F27 at the bridge level: a raw sheet filled by a Power Query table over a local CSV."""
    csv_path = tmp_path / "sales.csv"
    write_csv(csv_path, 5)
    wb = make_raw_model(tmp_path / "pq_model.xlsx", n_raw=5, helper_rows=8)
    res = excel.pq_attach(wb, sheet="raw_sales", table_name="tbl_raw_sales", query_name="q_sales",
                          m_text=m_text_for(csv_path))
    assert res.ok, res.errors
    step = res.step("pq_attach")
    assert step["rows"] == 5 and step["range"] == "A1:C6" and step["table_name"] == "tbl_raw_sales"
    with zipfile.ZipFile(wb) as z:
        names = z.namelist()
    assert "xl/connections.xml" in names and any(n.startswith("xl/queryTables/") for n in names)
    inv = excel.inventory(wb)
    assert inv["queries"] == ["q_sales"]
    raw = next(s for s in inv["sheets"] if s["name"] == "raw_sales")
    assert raw["tables"][0]["name"] == "tbl_raw_sales" and raw["tables"][0]["query"] is True
    values = excel.read_cells(wb, ["checks!C2", "checks!C3", "raw_sales!A2"], recalc_first=True)
    assert values == {"checks!C2": 5, "checks!C3": 5, "raw_sales!A2": "0001"}
    # More rows: 8 raw rows, still covered by 8 helper rows.
    write_csv(csv_path, 8)
    res = excel.pq_refresh(wb)
    assert res.ok, res.errors
    assert res.step("pq_refresh")["tables"][0]["rows"] == 8
    rec = excel.recalc(wb, read=["checks!C2", "checks!C3", "checks!C4"])
    assert rec.values == {"checks!C2": 8, "checks!C3": 8, "checks!C4": 360}
    assert rec.error_cells == []
    # Fewer rows: no reference breaks; helper rows past the data read empty raw cells.
    write_csv(csv_path, 3)
    res = excel.pq_refresh(wb)
    assert res.ok and res.step("pq_refresh")["tables"][0]["rows"] == 3
    rec = excel.recalc(wb, read=["checks!C2", "checks!C4", "h_sales!B4:B9"])
    assert rec.error_cells == [], "a refresh to fewer rows broke a formula"
    assert rec.values["checks!C2"] == 3 and rec.values["checks!C4"] == 60
    assert rec.values["h_sales!B4:B9"] == [[1], [0], [0], [0], [0], [0]]
    assert "#REF!" not in json.dumps(formulas(wb))
    # An edit through Excel keeps the query (F08a with a Power Query table).
    out = tmp_path / "pq_styled.xlsx"
    res = excel.apply_edits(wb, [{"op": "style", "sheet": "h_sales", "ranges": ["A1:D1"], "font": {"bold": True}}], out=out)
    assert res.ok, res.errors
    assert excel.inventory_counts(excel.inventory(out)) == excel.inventory_counts(excel.inventory(wb))
    # Attaching again with the same query name updates the M text and keeps the table.
    write_csv(csv_path, 4)
    res = excel.pq_attach(wb, sheet="raw_sales", table_name="tbl_raw_sales", query_name="q_sales",
                          m_text=m_text_for(csv_path))
    assert res.ok and res.step("pq_attach")["query"] == "updated" and res.step("pq_attach")["table"] == "kept"
    assert res.step("pq_attach")["rows"] == 4


@pytest.mark.excel
def test_failed_refresh_is_reported_and_nothing_is_saved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROTM_EXCEL_TIMEOUT", "60")     # a refresh that hangs on a dialog fails within 3 minutes
    csv_path = tmp_path / "sales.csv"
    write_csv(csv_path, 3)
    wb = make_raw_model(tmp_path / "pq_model.xlsx", n_raw=3, helper_rows=3)
    assert excel.pq_attach(wb, sheet="raw_sales", table_name="tbl_raw_sales", query_name="q_sales",
                           m_text=m_text_for(csv_path)).ok
    sha = common.file_sha256(wb)
    csv_path.unlink()
    res = excel.pq_refresh(wb, wait_s=30)
    assert not res.ok
    tables = res.step("pq_refresh").get("tables") or []
    assert tables and tables[0]["ok"] is False and tables[0]["problem"]
    assert common.file_sha256(wb) == sha


@pytest.mark.excel
def test_excel_that_cannot_start_gives_a_clear_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wb = make_clean(tmp_path / "clean.xlsx")
    monkeypatch.setenv("ROTM_EXCEL_PROGID", "Rotm.NoSuchApplication")
    with pytest.raises(common.EnvUnavailable) as info:
        excel.run_job(wb, [{"op": "recalc"}])
    assert info.value.message.startswith("Excel is not available: Excel could not be started")
    proc = run_cli("recalc", str(wb), env={"ROTM_EXCEL_PROGID": "Rotm.NoSuchApplication"})
    assert proc.returncode == common.EXIT_NOT_AVAILABLE
    assert "not recalculated: Excel is not available: Excel could not be started" in proc.stdout


@pytest.mark.excel
@pytest.mark.slow
def test_a_job_that_times_out_stops_only_the_excel_it_started(tmp_path: Path) -> None:
    wb = make_clean(tmp_path / "clean.xlsx")
    since = common.now_iso()
    res = excel.run_job(wb, [{"op": "sleep", "seconds": 120}], timeout_s=20)
    assert res.timed_out and not res.ok
    assert "did not finish within 20 s" in res.message()
    assert_own_excel_ended(res, since)
    assert res.killed


@pytest.mark.excel
def test_an_instance_the_job_did_not_start_is_left_alone(tmp_path: Path) -> None:
    wb = make_clean(tmp_path / "clean.xlsx")
    res = excel.run_job(wb, [{"op": "recalc"}], _treat_as_foreign=True)
    try:
        assert res.attached and not res.ok
        assert "did not start" in res.message()
        assert not res.killed, "the bridge stopped an instance it treats as not its own"
    finally:
        # The instance is really this test's: it leaves by itself once released; stop it if it did not.
        deadline = time.monotonic() + 15
        while res.pid and excel.pid_alive(res.pid) and time.monotonic() < deadline:
            time.sleep(0.2)
        if res.pid and excel.pid_alive(res.pid):
            excel._kill(res.pid)


@pytest.mark.excel
def test_long_paths_are_worked_on_as_a_copy(tmp_path: Path) -> None:
    deep = tmp_path
    while len(str(deep)) < excel.PATH_LIMIT + 10:
        deep = deep / "a_rather_long_folder_name"
    deep.mkdir(parents=True)
    wb = make_clean(deep / "model.xlsx")
    res = excel.recalc(wb, read=["calc!A3"])
    assert res.ok, res.errors
    assert res.values["calc!A3"] == 6 and res.saved_path == wb
    from openpyxl import load_workbook
    assert load_workbook(wb, data_only=True)["calc"]["A3"].value == 6


@pytest.mark.excel
def test_cli_recalc_and_render_with_model_folder_records(model_dir: Path) -> None:
    wb = make_simple(model_dir / "model.xlsx")
    proc = run_cli("recalc", str(wb), "--read", "checks!C2", "--json", "-")
    assert proc.returncode == common.EXIT_FINDINGS, proc.stderr
    report = json.loads(proc.stdout)
    assert report["command"] == "recalc" and report["verified"]["recalculated"] is True
    assert report["summary"]["errors"] == 3
    assert {f["rule"] for f in report["findings"]} == {"V-ERROR"}
    assert report["data"]["values"] == {"checks!C2": 0}
    assert "V-ERROR" in proc.stderr and "errors: 3" in proc.stderr
    status = json.loads((model_dir / "reports" / "status.json").read_text(encoding="utf-8"))
    entry = status["workbooks"]["model.xlsx"]["recalc"]
    assert entry["error_cells"] == 3 and entry["output_sha256"] == common.file_sha256(wb)
    proc = run_cli("render", str(wb))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "rendered, not looked at by code" in proc.stdout
    assert (model_dir / "reports" / "render" / "calc.png").is_file()
    status = json.loads((model_dir / "reports" / "status.json").read_text(encoding="utf-8"))
    assert "reports/render/calc.png" in status["workbooks"]["model.xlsx"]["render"]["files"]


@pytest.mark.excel
def test_cli_recalc_clean_workbook_exits_0(tmp_path: Path) -> None:
    wb = make_clean(tmp_path / "clean.xlsx")
    proc = run_cli("recalc", str(wb), "--read", "checks!C2")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "read checks!C2 = 0" in proc.stdout
    assert proc.stdout.strip().endswith("errors: 0  warnings: 0  info: 0")


@pytest.mark.excel
@pytest.mark.slow
def test_edits_through_excel_keep_the_parts_of_foreign_workbooks(foreign_dir: Path, tmp_path: Path) -> None:
    """Copies of workbooks the plugin did not make (BUILD/foreign or ROTM_FOREIGN_DIR): a style edit through
    Excel keeps every query, table, chart, name, conditional format and validation cell, and every formula."""
    candidates = sorted((p for p in foreign_dir.glob("*.xls[xm]") if not p.name.startswith("~$")),
                        key=lambda p: p.stat().st_size)
    kinds = {p: _parts_kind(p) for p in candidates}
    chosen: list[Path] = []
    for kind in ("query", "chart", "macro", "plain"):       # the smallest workbook of each kind present
        pick = next((p for p in candidates if kind in kinds[p] and p not in chosen), None)
        if pick is not None:
            chosen.append(pick)
    assert chosen
    for original in chosen:
        work = tmp_path / original.name
        shutil.copy2(original, work)
        inv = excel.inventory(work)
        sheet = next(s["name"] for s in inv["sheets"] if s["kind"] == "Worksheet" and s["visible"] == -1)
        out = tmp_path / f"{work.stem}_edited{work.suffix}"
        res = excel.apply_edits(work, [{"op": "style", "sheet": sheet, "ranges": ["A1"], "font": {"name": "Arial"}}],
                                out=out)
        assert res.ok, (original.name, res.errors)
        assert excel.inventory_counts(excel.inventory(out)) == excel.inventory_counts(inv), original.name
        assert formulas(out) == formulas(work), original.name


def _parts_kind(path: Path) -> set[str]:
    """{'query', 'chart', 'macro'} parts a workbook package holds, or {'plain'}."""
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
    except zipfile.BadZipFile:
        return set()
    kinds = set()
    if any(n.startswith(("xl/queryTables/", "xl/connections.xml")) for n in names):
        kinds.add("query")
    if any(n.startswith("xl/charts/") for n in names):
        kinds.add("chart")
    if "xl/vbaProject.bin" in names:
        kinds.add("macro")
    return kinds or {"plain"}


# ----------------------------------------------------------------------------- blank renders (integrator)

def _png(width: int, height: int, color: int, depth: int, rows: list[bytes], palette: bytes | None = None) -> bytes:
    import struct as _st
    import zlib as _z

    def chunk(t: bytes, d: bytes) -> bytes:
        return _st.pack(">I", len(d)) + t + d + _st.pack(">I", _z.crc32(t + d) & 0xFFFFFFFF)
    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", _st.pack(">IIBBBBB", width, height, depth, color, 0, 0, 0))
    if palette is not None:
        out += chunk(b"PLTE", palette)
    out += chunk(b"IDAT", _z.compress(b"".join(b"\x00" + r for r in rows))) + chunk(b"IEND", b"")
    return out


def test_png_is_blank_detects_one_colour_images(tmp_path: Path) -> None:
    from lib import excel as ex
    white = tmp_path / "white.png"
    white.write_bytes(_png(4, 3, 2, 8, [b"\xff" * 12] * 3))
    one_palette = tmp_path / "palette1.png"           # Excel's blank export: 1-bit, a palette of one colour
    one_palette.write_bytes(_png(9, 2, 3, 1, [b"\x00\x00"] * 2, palette=b"\xff\xff\xff"))
    header = tmp_path / "header.png"                  # a grey header pixel in the first row
    header.write_bytes(_png(4, 3, 2, 8, [b"\xd9\xd9\xd9" + b"\xff" * 9] + [b"\xff" * 12] * 2))
    assert ex.png_is_blank(white) is True
    assert ex.png_is_blank(one_palette) is True
    assert ex.png_is_blank(header) is False
    assert ex.png_is_blank(tmp_path / "missing.png") is None

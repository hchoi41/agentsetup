"""v5 tests of the shared core (package P1 of the v5 plan).

Entry [A-aera-8, C-sol-8] (r4 verdict, major): the suite must pass wherever the temporary folder is.
- selftest.failing_tests writes the part of each test name before the first '::' with forward slashes, whatever
  separator pytest printed (item 1).
- excel.x_open_finding: the reason Excel gave is never cut off by a long path. The workbook is named by its file
  name, every absolute path of the workbook (and of the working copy excel.py uses above PATH_LIMIT) in the reason
  is replaced by the file name, any other absolute path is shortened to '.../<parent folder>/<file name>', and
  X_OPEN_ERROR_MAX cuts only a reason that is itself longer after that (item 3). The recalc command uses it.

No test here needs Excel. Owner: P1 (v5). Machine-specific strings (folders of this PC, user names) are built at
run time from tmp_path, never written here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from lib import common, excel, selftest

OPEN_REASON = "Unable to get the Open property of the Workbooks class"
HEAD = "Excel started but could not open or calculate model.xlsx: "


def _long_book(tmp_path: Path, length: int = 150, *, name: str = "model.xlsx") -> tuple[Path, str]:
    """A workbook path of at least `length` characters under tmp_path, and the name of the folder that pads it."""
    pad = max(8, length - len(str(tmp_path / name)) - 1)
    folder = ("long_folder_" + "x" * pad)[:pad]
    book = tmp_path / folder / name
    assert len(str(book)) >= length
    return book, folder


def _no_folder_of(message: str, book: Path, folder: str) -> None:
    for text in (str(book.parent), book.parent.as_posix(), folder, book.anchor, book.drive + "/"):
        if text:
            assert text not in message, (text, message)


# ============================================================================= item 1: selftest.failing_tests

def test_failing_tests_writes_the_file_part_with_forward_slashes() -> None:
    output = "\n".join([
        "..F.E",
        "=========================== short test summary info ===========================",
        "FAILED scripts\\tests\\test_a.py::test_x - AssertionError: assert 1 == 2",
        "ERROR scripts/tests/test_a.py::test_y[a\\b] - RuntimeError: boom",
        "FAILED scripts/tests/test_a.py::test_x - AssertionError: assert 1 == 2",
        "FAILED scripts\\tests\\sub\\test_b.py::TestK::test_z[c:\\d] - assert 0",
        "ERROR scripts\\tests\\test_c.py",
        "2 failed, 1 error in 0.10s",
    ])
    assert selftest.failing_tests(output) == [
        "scripts/tests/test_a.py::test_x",
        "scripts/tests/test_a.py::test_y[a\\b]",
        "scripts/tests/sub/test_b.py::TestK::test_z[c:\\d]",
        "scripts/tests/test_c.py",
    ]


def test_f02_copy_failures_use_the_normalised_names(tmp_path: Path) -> None:
    """Step f02 reads the copy's JSON report; a name the copy wrote with backslashes is reported with '/'."""
    import subprocess
    report = tmp_path / "selftest_quick.json"
    report.write_text(json.dumps({
        "data": {"steps": {"pytest": "fail", "examples": "pass"},
                 "pytest_failed": ["scripts\\tests\\test_one.py::test_fails_on_purpose",
                                   "scripts/tests/test_one.py::test_fails_on_purpose"]},
        "findings": [{"rule": "SELFTEST-FAIL", "message": "step pytest: pytest exit 1"}]}), encoding="utf-8")
    proc = subprocess.CompletedProcess(["x"], 1, "", "")
    text = selftest._copy_failures(report, proc)
    assert "failing tests of its pytest step: scripts/tests/test_one.py::test_fails_on_purpose;" in text, text
    assert "\\" not in text, text


# ============================================================================= item 3: excel.x_open_finding

@pytest.mark.parametrize("form", ["windows", "posix"])
def test_x_open_keeps_the_reason_with_a_long_path(tmp_path: Path, form: str) -> None:
    book, folder = _long_book(tmp_path)
    shown = str(book) if form == "windows" else book.as_posix()
    error = f"Excel could not open {shown}: {OPEN_REASON}"
    f = excel.x_open_finding(book, [error], book.parent)
    assert f.rule == "X-OPEN" and f.severity == "error"
    assert f.message == HEAD + f"Excel could not open model.xlsx: {OPEN_REASON}", f.message
    _no_folder_of(f.message, book, folder)
    assert f.data["errors"] == [error]
    # above PATH_LIMIT Excel works on a copy in the job folder; that path is the workbook too
    big, big_folder = _long_book(tmp_path, excel.PATH_LIMIT + 40)
    copy = common.state_dir() / "jobs" / "20261125_101010_1234_abcdef" / "book.xlsx"
    error = f"Excel could not open {copy}: {OPEN_REASON}"
    g = excel.x_open_finding(big, [error], big.parent)
    assert g.message == HEAD + f"Excel could not open model.xlsx: {OPEN_REASON}", g.message
    _no_folder_of(g.message, big, big_folder)
    assert "jobs" not in g.message and g.data["errors"] == [error]


def test_x_open_shortens_another_path(tmp_path: Path) -> None:
    book, folder = _long_book(tmp_path)
    other = tmp_path / folder / "sources" / "linked.xlsx"
    error = f"Excel could not open {book}: the linked file {other} was not found ({OPEN_REASON})"
    f = excel.x_open_finding(book, [error])
    assert f.message == HEAD + ("Excel could not open model.xlsx: the linked file .../sources/linked.xlsx was not "
                                f"found ({OPEN_REASON})"), f.message
    _no_folder_of(f.message, book, folder)
    assert f.data["errors"] == [error]


def test_x_open_cuts_only_a_long_reason(tmp_path: Path) -> None:
    reason = "Unable to read the file because " + "r" * 268
    assert len(reason) == 300
    f = excel.x_open_finding(tmp_path / "model.xlsx", [reason])
    assert f.message == HEAD + reason[:excel.X_OPEN_ERROR_MAX - 3] + "...", f.message
    assert len(f.message) == len(HEAD) + excel.X_OPEN_ERROR_MAX
    book, folder = _long_book(tmp_path)
    short = excel.x_open_finding(book, [f"Excel could not open model.xlsx: {reason}"])
    long = excel.x_open_finding(book, [f"Excel could not open {book}: {reason}"])
    assert long.message == short.message, (long.message, short.message)
    assert len(long.message) == len(HEAD) + excel.X_OPEN_ERROR_MAX and long.message.endswith("...")
    _no_folder_of(long.message, book, folder)
    assert long.data["errors"] == [f"Excel could not open {book}: {reason}"]


def test_recalc_command_keeps_the_reason_with_a_long_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                         capsys: pytest.CaptureFixture[str]) -> None:
    from openpyxl import Workbook
    book, folder = _long_book(tmp_path)
    book.parent.mkdir(parents=True)
    wb = Workbook()
    wb.active["A1"] = 1
    wb.save(book)
    error = f"Excel could not open {book.resolve()}: {OPEN_REASON}"
    monkeypatch.setattr(excel, "excel_status", lambda: excel.ExcelStatus(available=True, version="16.0"))

    def recalc(*_a, **_k):
        return excel.RecalcResult(ok=False, saved_path=None, input_sha256="0" * 64, output_sha256=None,
                                  error_cells=[], values={}, pid=None, elapsed_s=1.0, errors=[error])
    monkeypatch.setattr(excel, "recalc", recalc)
    out_json = tmp_path / "recalc.json"
    args = argparse.Namespace(workbook=str(book), read=[], no_save=False, out=None, json=str(out_json), quiet=False)
    assert excel.run_recalc(args) == 1
    capsys.readouterr()
    report = json.loads(out_json.read_text(encoding="utf-8"))
    [x] = [f for f in report["findings"] if f["rule"] == "X-OPEN"]
    assert x["message"] == HEAD + f"Excel could not open model.xlsx: {OPEN_REASON}", x["message"]
    _no_folder_of(x["message"], book, folder)
    assert excel.X_OPEN_NOTE in report["notes"]

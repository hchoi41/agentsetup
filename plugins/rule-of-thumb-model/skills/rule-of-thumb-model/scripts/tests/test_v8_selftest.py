"""Tests of the v8 change of package P7 to selftest (round 7 verdict, [A-aera-18]).

selftest writes one line to stderr, flushed, when a step starts and when it ends (the step name and the elapsed
time), and about every PYTEST_PROGRESS_S seconds during the pytest step (with the count of tests done when pytest's
progress output gives it, else the elapsed time only). Stdout and the JSON report are as before.

The quick selftest runs as a user runs it (`rotm.py selftest --quick --json FILE`, a subprocess) in a small fake
skill folder under tmp_path: the skill's scripts (lib and rotm.py), its pytest.ini and a tests folder of two trivial
tests. That folder has no example or template, so the step examples fails, and the steps after portable are not
run: the run shows the passed, failed and skipped lines. The same run with the progress writer turned off goes
through a small driver that replaces selftest._progress before it calls rotm.main.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from lib import common, selftest  # noqa: E402

LINE = re.compile(r"^selftest: step (\S+) (started|passed in \d+ s|failed in \d+ s|skipped in 0 s) "
                  r"\(elapsed \d+ s\)$")
RUNNING = re.compile(r"^selftest: step pytest running(?:: (\d+) tests done)? \(elapsed \d+ s\)$")

DRIVER = """import sys
sys.path.insert(0, sys.argv[1])
from lib import selftest
selftest._progress = lambda text: None      # the progress writer turned off
import rotm
sys.exit(rotm.main(sys.argv[2:]))
"""


def _fake_skill(root: Path) -> Path:
    skill = root / "skills" / "rule-of-thumb-model"
    shutil.copytree(SCRIPTS / "lib", skill / "scripts" / "lib", ignore=shutil.ignore_patterns(*selftest.COPY_SKIP))
    shutil.copy2(SCRIPTS / "rotm.py", skill / "scripts" / "rotm.py")
    (skill / "pytest.ini").write_text((SCRIPTS.parent / "pytest.ini").read_text(encoding="utf-8"), encoding="utf-8")
    tests = skill / "scripts" / "tests"
    tests.mkdir()
    (tests / "test_two.py").write_text("def test_one():\n    assert True\n\n\ndef test_two():\n    assert 1 + 1 == 2\n",
                                       encoding="utf-8")
    return skill


def _run(args: list[str], json_out: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PYTHONUTF8="1")
    for name in ("ROTM_EXAMPLE_ROOTS", "ROTM_FOREIGN_DIR"):
        env.pop(name, None)
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", *args, "selftest", "--quick", "--json",
                           str(json_out)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=env, timeout=600)


_TIMES = [(re.compile(r"\bin \d+(?:\.\d+)?\s?s\b"), "in <t>"),                      # "passed in 3 s", "in 0.05s"
          (re.compile(r"rotm_selftest_[A-Za-z0-9_]+"), "rotm_selftest_<x>"),        # the temporary working folder
          (re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:[+-]\d\d:\d\d|Z)?"), "<time>")]


def times_removed(obj):
    """The report or the text with every time and the name of the temporary working folder replaced."""
    if isinstance(obj, dict):
        return {k: times_removed(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [times_removed(v) for v in obj]
    if isinstance(obj, str):
        for pattern, repl in _TIMES:
            obj = pattern.sub(repl, obj)
    return obj


def test_quick_selftest_writes_progress_lines_to_stderr(tmp_path: Path) -> None:
    skill = _fake_skill(tmp_path / "fake")
    rotm = skill / "scripts" / "rotm.py"
    on = _run([str(rotm)], tmp_path / "on.json")
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER, encoding="utf-8")
    off = _run([str(driver), str(skill / "scripts")], tmp_path / "off.json")
    assert on.returncode == off.returncode == 1, (on.returncode, on.stdout[-2000:], on.stderr[-2000:])

    # stderr: the start and the end line of each step that ran, in order; one line for each step not run
    lines = [line for line in on.stderr.splitlines() if line.strip()]
    assert all(LINE.match(line) or RUNNING.match(line) for line in lines), lines
    events = [(LINE.match(line).group(1), LINE.match(line).group(2).split(" in ")[0])
              for line in lines if LINE.match(line)]
    assert events == [("pytest", "started"), ("pytest", "passed"), ("examples", "started"), ("examples", "failed"),
                      ("portable", "started"), ("portable", "passed"), ("recalc", "skipped"), ("render", "skipped"),
                      ("orphans", "skipped"), ("f02_skill_alone", "skipped"), ("f08a_existing", "skipped"),
                      ("f27_power_query", "skipped")], events
    # stdout holds no progress line; the run without the progress writer writes none to stderr
    assert not any(line.startswith("selftest: step ") for line in on.stdout.splitlines()), on.stdout
    assert not any(line.startswith("selftest: step ") for line in off.stderr.splitlines()), off.stderr

    # stdout and the JSON report, with the times removed, equal those of the run without the progress writer
    rep_on = json.loads((tmp_path / "on.json").read_text(encoding="utf-8"))
    rep_off = json.loads((tmp_path / "off.json").read_text(encoding="utf-8"))
    assert rep_on["data"]["steps"]["pytest"] == "pass" and rep_on["data"]["steps"]["examples"] == "fail"
    assert times_removed(rep_on) == times_removed(rep_off)
    assert times_removed(on.stdout) == times_removed(off.stdout)


FAKE_PYTEST = """import os, sys, time
def wait_for(name):
    end = time.monotonic() + 60
    while not os.path.exists(os.path.join(sys.argv[1], name)):
        if time.monotonic() > end:
            sys.exit(9)
        time.sleep(0.02)
wait_for("go1")
sys.stdout.write("....F.s" + " " * 60 + "[ 50%]\\n")
sys.stdout.flush()
wait_for("go2")
sys.stdout.write("..x" + " " * 64 + "[100%]\\n")
sys.stdout.write("FAILED scripts/tests/test_x.py::test_f - assert 1 == 2\\n")
sys.stdout.write("1 failed, 7 passed, 1 skipped, 1 xfailed in 0.50s\\n")
sys.stdout.flush()
sys.exit(1)
"""


def test_pytest_progress_line_gives_the_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The running line with the interval set to 0 s, on a fake pytest output: before pytest has printed a progress
    line, the elapsed time only; after it, the count of tests done. The fake waits for a file that the recording
    progress writer creates, so the order does not depend on timing. The exit code and the whole stdout come back
    as subprocess.run gave them in v7."""
    fake = tmp_path / "fake_pytest.py"
    fake.write_text(FAKE_PYTEST, encoding="utf-8")
    seen: list[str] = []

    def record(text: str) -> None:
        seen.append(f"selftest: {text}")
        if RUNNING.match(seen[-1]):
            (tmp_path / ("go2" if RUNNING.match(seen[-1]).group(1) else "go1")).touch()

    monkeypatch.setattr(selftest, "_progress", record)
    monkeypatch.setattr(selftest, "PYTEST_PROGRESS_S", 0.0)
    monkeypatch.setattr(selftest, "_skill", lambda: tmp_path)
    run = selftest._Run(common.Report("selftest", "x"), tmp_path, strict=False, quick=True, excel_ok=False,
                        excel_why="test", foreign_dir=None)
    code, stdout = run._pytest_run([sys.executable, "-P", "-X", "utf8", "-B", str(fake), str(tmp_path)], None)
    assert code == 1
    assert stdout.splitlines()[-1] == "1 failed, 7 passed, 1 skipped, 1 xfailed in 0.50s"
    assert selftest.failing_tests(stdout) == ["scripts/tests/test_x.py::test_f"]
    running = [RUNNING.match(line) for line in seen]
    assert running and all(running), seen
    counts = [m.group(1) for m in running]
    assert counts[0] is None and "7" in counts, counts
    assert counts.index("7") > 0 and all(c is None for c in counts[:counts.index("7")]), counts
    assert all(c in (None, "7", "10") for c in counts), counts       # 10 only if pytest had not ended yet


@pytest.mark.parametrize("line, count", [
    ("........................................................................ [ 48%]\n", 72),
    ("....F.sxX.E   [100%]", 11),
    ("1 failed, 7 passed in 0.50s", None),
    ("FAILED scripts/tests/test_x.py::test_f - assert 1 == 2", None),
    ("", None),
])
def test_pytest_progress_count_reads_the_progress_lines(line: str, count: int | None) -> None:
    assert selftest.pytest_progress_count(line) == count


def test_copy_failures_tail_leaves_out_the_progress_lines(tmp_path: Path) -> None:
    """Step f02 without the copy's JSON report: the tail of the copy's output is the one v7 gave, without the copy's
    progress lines."""
    err = ("Traceback (most recent call last):\n  File x\nValueError: broken\n"
           "selftest: step pytest started (elapsed 0 s)\nselftest: step pytest failed in 1 s (elapsed 1 s)\n")
    proc = subprocess.CompletedProcess(["x"], 4, "out line\n", err)
    text = selftest._copy_failures(tmp_path / "missing.json", proc)
    assert text == "out line | Traceback (most recent call last): | File x | ValueError: broken", text

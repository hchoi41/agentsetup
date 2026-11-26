"""Tests of the v6 change of package P4 to the finish check hook (round 5 verdict, [C-sol-5], reduced scope).

- The audit and check commands that hooks/finish_check.py prints name the workbook file itself
  (`check "<workbook path>"`), in the refusal and in the one-line notice of a stop that is let through. A second
  workbook built with `build -o` at the top level of a model folder is checked as that file, not as the marker's
  workbook.
- Workbooks in a subfolder of the model folder stay as decided in earlier rounds: the hooks do not watch them.

Each hook is run as Claude Code runs it (python -I, the payload on stdin), with the helpers of test_hooks.py. The
last test builds a real model folder from the starter template without Excel (ROTM_NO_EXCEL=1).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from lib import check, modelfolder
from test_hooks import (HOOKS, ROTM, assert_silent, bash, build_cycle, fake_rotm, make_workbook, model,  # noqa: F401
                        post, run_hook, status_pass, stop)

SCRIPTS = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not (HOOKS / "hooks.json").is_file(),
                                reason="PLUGIN/hooks is absent (the skill is used without its plugin)")
CHECK_RE = re.compile(r'rotm\.py"? check "([^"]+)"')
AUDIT_RE = re.compile(r'rotm\.py"? audit "([^"]+)" --strict')


def _same(a: str | Path, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


def _write_draft(root: Path, env: dict, sid: str, *, name: str = "draft.xlsx", value: int = 7) -> Path:
    """raw_guard before `build -o <name>`, the call writes <name>, audit_on_write after it."""
    payload = bash(f"{ROTM} build model.spec.json -o {name} --strict", root, sid=sid)
    assert_silent(run_hook("raw_guard", payload, env=env))
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    make_workbook(target, value)
    run_hook("audit_on_write", post(payload), env=env)
    return target


def test_the_check_command_names_a_second_workbook_at_the_top_level(model: Path, fake_rotm: tuple[dict, Path]) -> None:
    env, _log = fake_rotm
    build_cycle(model, env, sid="sess-o1")                  # model.xlsx written in this session ...
    status_pass(model)                                      # ... and verified
    draft = _write_draft(model, env, "sess-o1")             # draft.xlsx written; its own audit was clean
    out = run_hook("finish_check", stop(model, sid="sess-o1"))
    assert out.code == 0 and out.stderr == "", out
    reason = out.json()["reason"]
    assert reason.startswith("rule-of-thumb-model finish check: draft.xlsx changed after"), reason
    targets = CHECK_RE.findall(reason)
    assert len(targets) == 1 and _same(targets[0], draft), reason
    assert f'check "{model.as_posix()}"' not in reason
    # the stop that follows is let through; its one line names the same command
    through = run_hook("finish_check", stop(model, sid="sess-o1", active=True))
    msg = through.json()["systemMessage"]
    notice_targets = CHECK_RE.findall(msg)
    assert len(notice_targets) == 1 and _same(notice_targets[0], draft), msg
    assert msg.startswith("rule-of-thumb-model finish check: not verified at the end of the session: draft.xlsx: ")


def test_the_audit_command_names_the_second_workbook(model: Path, fake_rotm: tuple[dict, Path]) -> None:
    env, _log = fake_rotm
    build_cycle(model, env, sid="sess-o2")
    status_pass(model)
    draft = _write_draft(model, dict(env, FAKE_ROTM_MODE="findings"), "sess-o2")
    reason = run_hook("finish_check", stop(model, sid="sess-o2")).json()["reason"]
    audits, checks = AUDIT_RE.findall(reason), CHECK_RE.findall(reason)
    assert len(audits) == 1 and _same(audits[0], draft), reason
    assert len(checks) == 1 and _same(checks[0], draft), reason
    msg = run_hook("finish_check", stop(model, sid="sess-o2", active=True)).json()["systemMessage"]
    assert [_same(p, draft) for p in AUDIT_RE.findall(msg) + CHECK_RE.findall(msg)] == [True, True], msg


def test_with_only_the_marker_workbook_the_command_checks_that_workbook(model: Path,
                                                                        fake_rotm: tuple[dict, Path]) -> None:
    env, _log = fake_rotm
    build_cycle(model, env, sid="sess-o3")
    reason = run_hook("finish_check", stop(model, sid="sess-o3")).json()["reason"]
    targets = CHECK_RE.findall(reason)
    assert len(targets) == 1 and _same(targets[0], model / "model.xlsx"), reason


def test_a_workbook_in_a_subfolder_stays_unwatched(model: Path, fake_rotm: tuple[dict, Path]) -> None:
    """As decided in earlier rounds (design 13.5: workbooks directly in the model folder)."""
    env, _log = fake_rotm
    _write_draft(model, env, "sess-o4", name="exports/draft.xlsx")
    assert_silent(run_hook("finish_check", stop(model, sid="sess-o4")))


# ============================================================================= a real model folder

def _rotm(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["ROTM_NO_EXCEL"] = "1"
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=600)


@pytest.fixture(scope="module")
def built_model(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The starter template (init --template starter), its contract shown and agreed, its sample landed, built
    without Excel (set-up of test_v5_land.py)."""
    root = tmp_path_factory.mktemp("v6_hooks") / "model"
    for args in (("init", str(root), "--template", "starter"),
                 ("contract", "show", str(root), "--table", "lines"),
                 ("contract", "agree", str(root), "--table", "lines", "--reply", "agreed", "--by", "tester"),
                 ("land", str(root), "--table", "lines", "--csv", str(root / "data" / "lines.csv")),
                 ("build", str(root), "--strict")):
        proc = _rotm(*args)
        assert proc.returncode == 0, (args, proc.stdout[-2000:], proc.stderr[-2000:])
    return root


def test_a_real_build_to_a_second_workbook_is_checked_as_that_file(built_model: Path,
                                                                   fake_rotm: tuple[dict, Path]) -> None:
    env, _log = fake_rotm
    root = built_model
    draft = root / "draft.xlsx"
    command = f'{ROTM} build "{root.as_posix()}" -o "{draft.as_posix()}" --strict'
    payload = bash(command, root, sid="sess-real-o")
    assert_silent(run_hook("raw_guard", payload, env=env))
    proc = _rotm("build", str(root), "-o", str(draft), "--strict")
    assert proc.returncode == 0 and draft.is_file(), (proc.stdout[-2000:], proc.stderr[-2000:])
    run_hook("audit_on_write", post(payload), env=env)
    reason = run_hook("finish_check", stop(root, sid="sess-real-o")).json()["reason"]
    (target,) = CHECK_RE.findall(reason)
    assert _same(target, draft), reason
    # the command checks that file: check resolves the workbook path to itself, not to the marker's workbook
    resolved = check._resolve(target)
    assert _same(resolved.workbook, draft) and resolved.rel == "draft.xlsx"
    assert modelfolder.read_marker(root).get("workbook", "model.xlsx") == "model.xlsx"
    run = _rotm("check", target, "--json", "-", "--quiet")
    assert "Traceback" not in run.stderr, run.stderr[-2000:]
    status = modelfolder.read_status(root)["workbooks"]
    assert "check" in status.get("draft.xlsx", {}), status.keys()


def test_a_real_rebuild_of_the_marker_workbook_is_checked_as_that_file(built_model: Path,
                                                                       fake_rotm: tuple[dict, Path]) -> None:
    env, _log = fake_rotm
    root = built_model
    payload = bash(f'{ROTM} build "{root.as_posix()}" --force --strict', root, sid="sess-real-m")
    assert_silent(run_hook("raw_guard", payload, env=env))
    proc = _rotm("build", str(root), "--force", "--strict")
    assert proc.returncode == 0, (proc.stdout[-2000:], proc.stderr[-2000:])
    run_hook("audit_on_write", post(payload), env=env)
    reason = run_hook("finish_check", stop(root, sid="sess-real-m")).json()["reason"]
    (target,) = CHECK_RE.findall(reason)
    assert _same(target, root / "model.xlsx"), reason
    assert _same(check._resolve(target).workbook, root / "model.xlsx")

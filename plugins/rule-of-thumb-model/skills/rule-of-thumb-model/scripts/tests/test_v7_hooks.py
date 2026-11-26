"""Tests of the v7 change of package P6 to the hooks (round 6 verdict, [C-sol-1]).

A command that runs rotm.py or formula_audit.py names its model folder in an argument that the subcommand defines
as a model folder or a file: the positional model folder, specification or workbook, -o/--out, --csv and
--source-file. hooks/_hooklib.py resolves each such argument against the command's working directory and adds it
to the segment's paths whatever it looks like, so a bare folder name such as `gate-relative` puts the model
folder in scope, as `./gate-relative` and the full path do.

Each hook runs as Claude Code runs it (python -I, the payload on stdin), with the helpers of test_hooks.py. Every
command is given from the parent folder of the model folder, in three forms: the bare name, ./name and the full
path. The table of subcommands and arguments in _hooklib.py is compared with the parsers of rotm.py and
formula_audit.py.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path

import pytest

from lib import modelfolder
from test_hooks import (HOOKS, NOT_VERIFIED, ROTM, assert_refused, assert_silent, bash, fake_rotm,  # noqa: F401
                        make_workbook, post, run_hook, stop, touch_later, write_contract)

pytestmark = pytest.mark.skipif(not (HOOKS / "hooks.json").is_file(),
                                reason="PLUGIN/hooks is absent (the skill is used without its plugin)")

# v7 integrator: imported only when the hooks are there, so that the module is collected (and its tests skipped) in
# a skill folder copied without its plugin (selftest step f02_skill_alone)
hl = None
if (HOOKS / "_hooklib.py").is_file():
    if str(HOOKS) not in sys.path:
        sys.path.insert(0, str(HOOKS))
    hl = importlib.import_module("_hooklib")

MODEL_NAME = "gate-relative"
FORMS = ("bare", "dot", "absolute")


def _same(a: str | Path, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


@pytest.fixture
def parent(tmp_path: Path) -> Path:
    """A plain folder (no marker at or above it) that holds the model folder gate-relative: marker, the proposed
    contract of table 'lines', a workbook."""
    folder = tmp_path / "work"
    root = folder / MODEL_NAME
    modelfolder.init_model(root, name="gate_relative")
    write_contract(root, "lines", agreed=False)
    make_workbook(root / "model.xlsx")
    assert modelfolder.find_model_root(folder) is None
    return folder


def _target(form: str, root: Path) -> str:
    """The model folder as the command names it, from its parent folder."""
    return {"bare": root.name, "dot": f"./{root.name}", "absolute": f'"{root.as_posix()}"'}[form]


def _transcript(path: Path, *typed: str) -> Path:
    """A session transcript in which the user typed these messages and nothing else."""
    lines = [json.dumps({"type": "user", "message": {"role": "user", "content": text},
                         "timestamp": f"2026-11-26T00:0{i}:00Z", "uuid": f"u{i}"}) for i, text in enumerate(typed)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _build_cycle(parent: Path, form: str, env: dict, sid: str) -> Path:
    """raw_guard before `rotm.py build <model folder> --force --strict` given from the parent folder, the build
    writes model.xlsx, audit_on_write after it. Returns the workbook."""
    root = parent / MODEL_NAME
    payload = bash(f"{ROTM} build {_target(form, root)} --force --strict", parent, sid=sid)
    assert_silent(run_hook("raw_guard", payload, env=env))
    touch_later(root / "model.xlsx", 4242)
    post_out = run_hook("audit_on_write", post(payload), env=env)
    assert post_out.code == 0 and post_out.stderr == "", post_out
    return root / "model.xlsx"


# ============================================================================= the four fixed tests (plan 5.3)

@pytest.mark.parametrize("form", FORMS)
def test_agreement_guard_refuses_an_untyped_reply(form: str, parent: Path, tmp_path: Path) -> None:
    """The cold reviewer's case: the transcript holds only 'Please show the proposed contract.'."""
    root = parent / MODEL_NAME
    cmd = f'{ROTM} contract agree {_target(form, root)} --table lines --reply "I agree" --by Reviewer'
    untyped = _transcript(tmp_path / "untyped.jsonl", "Please show the proposed contract.")
    assert_refused(run_hook("contract_gate", bash(cmd, parent, transcript=untyped)),
                   "not in any message from the user in this session", '--reply "I agree" was not found')
    # the same reply, typed by the user, is let through
    typed = _transcript(tmp_path / "typed.jsonl", "Please show the proposed contract.", "I agree")
    assert_silent(run_hook("contract_gate", bash(cmd, parent, transcript=typed)))


@pytest.mark.parametrize("form", FORMS)
def test_audit_on_write_reports_after_a_build(form: str, parent: Path, fake_rotm: tuple[dict, Path]) -> None:
    env, log = fake_rotm
    root = parent / MODEL_NAME
    payload = bash(f"{ROTM} build {_target(form, root)} --force --strict", parent, sid=f"sess-v7-post-{form}")
    assert_silent(run_hook("raw_guard", payload, env=env))
    touch_later(root / "model.xlsx", 4243)
    for failure in (False, True):              # build --strict with a warning exits 1: PostToolUseFailure
        if failure:
            touch_later(root / "model.xlsx", 4244)
        out = run_hook("audit_on_write", post(payload, failure=failure), env=env)
        assert out.code == 0 and out.stderr == "", out
        ctx = out.json()["hookSpecificOutput"]
        assert ctx["hookEventName"] == ("PostToolUseFailure" if failure else "PostToolUse")
        assert ctx["additionalContext"] == ("rule-of-thumb-model audit on write: model.xlsx: 0 errors, 0 warnings "
                                            f"({NOT_VERIFIED})")
    audited = [line.split() for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(audited) == 2 and all(a[0] == "audit" and _same(a[1], root / "model.xlsx") for a in audited), audited


@pytest.mark.parametrize("form", FORMS)
def test_finish_check_refuses_once_for_an_unverified_workbook(form: str, parent: Path,
                                                             fake_rotm: tuple[dict, Path]) -> None:
    env, _log = fake_rotm
    sid = f"sess-v7-stop-{form}"
    workbook = _build_cycle(parent, form, env, sid)
    first = run_hook("finish_check", stop(parent, sid=sid))
    assert first.code == 0 and first.stderr == "", first
    data = first.json()
    assert data["decision"] == "block", data
    assert data["reason"].startswith("rule-of-thumb-model finish check: model.xlsx changed after its last passing "
                                     "audit or recalculation."), data["reason"]
    assert f'check "{workbook.as_posix()}"' in data["reason"], data["reason"]
    # the next stop is let through with its one-line notice (plan F16 c): after the refusal, and again without
    # a new write
    for active in (True, False):
        through = run_hook("finish_check", stop(parent, sid=sid, active=active))
        assert through.code == 0 and through.stderr == "", through
        msg = through.json()
        assert "decision" not in msg, msg
        assert msg["systemMessage"].startswith("rule-of-thumb-model finish check: not verified at the end of the "
                                               "session: model.xlsx: "), msg


def test_a_bare_word_that_is_no_model_folder_gives_no_output(tmp_path: Path, fake_rotm: tuple[dict, Path],
                                                            _isolated_state: Path) -> None:
    """A folder without a marker, and a word that names nothing: no hook output, no session record."""
    env, log = fake_rotm
    folder = tmp_path / "plain"
    (folder / "notamodel").mkdir(parents=True)
    make_workbook(folder / "notamodel" / "model.xlsx")
    untyped = _transcript(tmp_path / "untyped.jsonl", "Please show the proposed contract.")
    for word in ("notamodel", "missing", "./notamodel"):
        agree = f'{ROTM} contract agree {word} --table lines --reply "I agree" --by Reviewer'
        assert_silent(run_hook("contract_gate", bash(agree, folder, transcript=untyped)))
        build = bash(f"{ROTM} build {word} --force --strict", folder, sid="sess-v7-none")
        for hook in ("contract_gate", "raw_guard"):
            assert_silent(run_hook(hook, build, env=env))
        touch_later(folder / "notamodel" / "model.xlsx", 77)
        assert_silent(run_hook("audit_on_write", post(build), env=env))
        assert_silent(run_hook("audit_on_write", post(build, failure=True), env=env))
        assert_silent(run_hook("finish_check", stop(folder, sid="sess-v7-none")))
    assert not log.exists()
    assert not (_isolated_state / "sessions").exists()


# ============================================================================= the argument table

def _parser(key: str) -> argparse.ArgumentParser:
    """The parser of one table key, built as rotm.py (or formula_audit.py) builds it."""
    import rotm
    if key == "formula_audit.py":
        from lib import audit
        parser = argparse.ArgumentParser(prog="formula_audit.py")
        audit.add_audit_arguments(parser)
        return parser
    command, _, action = key.partition(" ")
    module_name, prefix = rotm.COMMANDS[command][:2]
    parser = argparse.ArgumentParser(prog=f"rotm.py {command}")
    getattr(importlib.import_module(f"lib.{module_name}"), f"add_{prefix}_arguments")(parser)
    if not action:
        return parser
    subs = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    assert len(subs) == 1, key
    return subs[0].choices[action]


def _shape(parser: argparse.ArgumentParser) -> tuple[tuple[str, ...], set[str], set[str], set[str]]:
    """(positionals in order, options that take one value, options that take none, options that take any number)."""
    pos: list[str] = []
    values: set[str] = set()
    flags: set[str] = set()
    lists: set[str] = set()
    for a in parser._actions:
        if isinstance(a, (argparse._HelpAction, argparse._SubParsersAction)):
            continue
        if not a.option_strings:
            assert a.nargs in (None, "?"), (a.dest, a.nargs)
            pos.append(a.dest)
        elif a.nargs == 0:
            flags.update(a.option_strings)
        elif a.nargs is None:
            values.update(a.option_strings)
        elif a.nargs == "*":
            lists.update(a.option_strings)
        else:
            raise AssertionError(f"{a.option_strings}: nargs {a.nargs!r} is not in the hook table's forms")
    return tuple(pos), values, flags, lists


def _table_keys() -> set[str]:
    import rotm
    keys = {"formula_audit.py"}
    for name in rotm.COMMANDS:
        parser = _parser(name)
        subs = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
        keys |= {f"{name} {action}" for action in subs[0].choices} if subs else {name}
    return keys


def test_the_argument_table_equals_the_parsers() -> None:
    """hooks/_hooklib.py holds its own copy of the subcommands' arguments (the hooks run with -I and cannot import
    the modules that define them); it must equal the parsers."""
    assert set(hl.ROTM_ARGS) == _table_keys()
    positionals: set[str] = set()
    for key, spec in hl.ROTM_ARGS.items():
        pos, values, flags, lists = _shape(_parser(key))
        assert spec.positionals == pos, key
        assert set(spec.values) == values, (key, set(spec.values) ^ values)
        assert set(spec.flags) == flags, (key, set(spec.flags) ^ flags)
        assert set(spec.lists) == lists, (key, set(spec.lists) ^ lists)
        positionals.update(pos)
    # every positional names a model folder, a specification or a workbook, except a cell reference and a formula
    assert hl.ROTM_PATH_POSITIONALS == positionals - {"ref", "formula"}
    every_value = {o for spec in hl.ROTM_ARGS.values() for o in spec.values}
    assert hl.ROTM_PATH_OPTIONS == {"-o", "--out", "--csv", "--source-file"} and hl.ROTM_PATH_OPTIONS <= every_value


@pytest.mark.parametrize("script,args,paths", [
    ("rotm.py", ["contract", "agree", "m", "--table", "t", "--reply", "I agree", "--by", "R"], ["m"]),
    ("rotm.py", ["contract", "agree", "--table", "t", "m", "--reply=I agree", "--channel", "task-file",
                 "--source-file", "task.md"], ["m", "task.md"]),
    ("rotm.py", ["contract", "propose", "m", "--tab", "t", "--force", "--csv", "data/x.csv"], ["m", "data/x.csv"]),
    ("rotm.py", ["build", "--strict", "-odraft.xlsx", "m"], ["m", "draft.xlsx"]),
    ("rotm.py", ["build", "m", "--out=draft.xlsx", "--capacity", "sales=10"], ["m", "draft.xlsx"]),
    ("rotm.py", ["land", "m", "--table", "t", "--csv", "x.csv", "--control", "t.a=1", "--force"], ["m", "x.csv"]),
    ("rotm.py", ["recalc", "m/model.xlsx", "--read", "checks!C2", "checks!C3", "--no-save"], ["m/model.xlsx"]),
    ("rotm.py", ["trace", "model.xlsx", "summary!D6", "--depth", "3"], ["model.xlsx"]),
    ("rotm.py", ["diff", "old", "new", "--md", "d.md"], ["old", "new"]),
    ("rotm.py", ["check", "--", "-odd name"], ["-odd name"]),
    ("rotm.py", ["formula", "=A1/B1", "--sheet", "calc"], []),
    ("rotm.py", ["status", "-"], []),
    ("rotm.py", ["selftest", "--quick"], []),
    ("rotm.py", ["contract", "-h"], []),
    ("rotm.py", ["nosuchcommand", "m"], []),
    ("formula_audit.py", ["m", "--strict", "--json", "-"], ["m"]),
])
def test_the_path_arguments_of_a_call(script: str, args: list[str], paths: list[str]) -> None:
    assert hl.rotm_call(script, args).paths() == paths


def test_a_bare_name_after_cd_and_in_a_nested_shell_is_resolved_there(parent: Path) -> None:
    root = parent / MODEL_NAME
    top = parent.parent
    for command, shell in ((f"cd {parent.name} && {ROTM} build {MODEL_NAME} --strict", "bash"),
                           (f"bash -c 'cd {parent.name}; {ROTM} check {MODEL_NAME} --strict'", "bash"),
                           (f"Set-Location {parent.name}; python rotm.py audit {MODEL_NAME} --strict", "powershell")):
        segs = hl.analyze(command, top, shell)
        rotm_segs = [s for s in segs if s.rotm is not None]
        assert len(rotm_segs) == 1, command
        assert any(_same(p, root) for p in rotm_segs[0].paths), (command, rotm_segs[0].paths)
        payload = {"tool_name": "PowerShell" if shell == "powershell" else "Bash", "cwd": str(top),
                   "tool_input": {"command": command}}
        assert [_same(r, root) for r in hl.Context("test", payload).roots()] == [True], command

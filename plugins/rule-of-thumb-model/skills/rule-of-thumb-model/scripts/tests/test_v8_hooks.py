"""Tests of the v8 change of package P7 to the finish check (round 7 verdict, [C-sol-5]).

A stop that is let through because the last message says "not recalculated" is remembered for each workbook whose
recalculation the words excused: its sha256 and the present write sequence go into the session record
("disclosed"). A later stop of the same session does not refuse again for a workbook that has the same sha256 and
was not written since; a workbook written after that stop is judged as before. The words excuse the recalculation
only: the audit is judged at every stop. A refused stop records nothing. The one refusal per session end and the
reading of the words anywhere in the message stay as in v7.

Each hook runs as Claude Code runs it (python -I, the payload on stdin), with the helpers of test_hooks.py and its
stand-in for rotm.py (a clean strict audit, or findings with FAKE_ROTM_MODE=findings).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib import common
from test_hooks import (HOOKS, ROTM, assert_silent, bash, build_cycle, fake_rotm, model,  # noqa: F401
                        run_hook, stop, touch_later)

pytestmark = pytest.mark.skipif(not (HOOKS / "hooks.json").is_file(),
                                reason="PLUGIN/hooks is absent (the skill is used without its plugin)")

DISCLOSURE = "Built and audited. Excel was absent, so model.xlsx was not recalculated."
NOTICE = "rule-of-thumb-model finish check: not verified at the end of the session: model.xlsx: "


def _record(state: Path, sid: str) -> dict:
    return json.loads((state / "sessions" / f"{sid}.json").read_text(encoding="utf-8"))


def _decision(out) -> dict:
    assert out.code == 0 and out.stderr == "", out
    return out.json()


def test_disclosure_is_remembered_for_the_unchanged_workbook(model: Path, fake_rotm: tuple[dict, Path],
                                                            _isolated_state: Path) -> None:
    """The reviewer's case: a disclosure passes; the next stop, whose message does not repeat it, is not refused
    for the same unchanged workbook (v7 refused it)."""
    env, _log = fake_rotm
    sid = "sess-v8-remember"
    build_cycle(model, env, sid=sid)                      # the hook's own strict audit is clean
    assert_silent(run_hook("finish_check", stop(model, sid=sid, last=DISCLOSURE)))
    # later stops without the words: no refusal and no notice; also from outside the model folder
    for last, cwd in (("Done.", model), ("Here is the answer to your question.", model.parent), (None, model)):
        assert_silent(run_hook("finish_check", stop(cwd, sid=sid, last=last)))
    rec = _record(_isolated_state, sid)
    assert rec["finish_refusals"] == 0
    (entry,) = rec["disclosed"].values()
    assert entry["sha256"] == common.file_sha256(model / "model.xlsx") and entry["seq"] == rec["write_seq"] == 1


@pytest.mark.parametrize("how", ["post_hook", "unseen"])
def test_workbook_changed_after_the_disclosure_is_refused_once(how: str, model: Path, fake_rotm: tuple[dict, Path],
                                                               _isolated_state: Path) -> None:
    """A write after the disclosure (seen by audit_on_write, or seen only by the finish check): the next stop
    without the words is refused once; the stops after it are let through with the notice (plan F16 c)."""
    env, _log = fake_rotm
    sid = f"sess-v8-changed-{how}"
    build_cycle(model, env, sid=sid)
    assert_silent(run_hook("finish_check", stop(model, sid=sid, last=DISCLOSURE)))
    disclosed_sha = common.file_sha256(model / "model.xlsx")
    if how == "post_hook":
        build_cycle(model, env, sid=sid)
    else:
        assert_silent(run_hook("raw_guard", bash("ls", model, sid=sid), env=env))
        touch_later(model / "model.xlsx", 5151)           # no PostToolUse hook saw this write
    assert common.file_sha256(model / "model.xlsx") != disclosed_sha
    first = _decision(run_hook("finish_check", stop(model, sid=sid)))
    assert first["decision"] == "block", first
    assert "- model.xlsx: not recalculated: no passing check of this version." in first["reason"], first["reason"]
    for active in (True, False):
        through = _decision(run_hook("finish_check", stop(model, sid=sid, active=active)))
        assert "decision" not in through and through["systemMessage"].startswith(NOTICE), through
        assert "no passing recalculation" in through["systemMessage"], through
    rec = _record(_isolated_state, sid)
    assert rec["finish_refusals"] == 1 and rec["finish_refused_at_seq"] == rec["write_seq"] == 2
    # the record still holds the disclosed version only; the new version was never disclosed
    assert [e["sha256"] for e in rec["disclosed"].values()] == [disclosed_sha]


def test_disclosure_does_not_excuse_a_failed_audit(model: Path, fake_rotm: tuple[dict, Path],
                                                  _isolated_state: Path) -> None:
    """The words excuse the recalculation only. A refused stop records nothing; a stop that is let through with the
    words records the disclosure, and the later notices name the audit and not the recalculation."""
    env, _log = fake_rotm
    sid = "sess-v8-audit"
    build_cycle(model, dict(env, FAKE_ROTM_MODE="findings"), sid=sid)
    first = _decision(run_hook("finish_check", stop(model, sid=sid, last=DISCLOSURE)))
    assert first["decision"] == "block", first
    assert "audit not passed: the strict audit of this version found 1 errors and 2 warnings" in first["reason"]
    assert "not recalculated: no passing check" not in first["reason"]
    assert _record(_isolated_state, sid).get("disclosed", {}) == {}      # a refusal records nothing
    # the stop after the refusal is let through; its message holds the words, so the disclosure is recorded
    through = _decision(run_hook("finish_check", stop(model, sid=sid, active=True, last=DISCLOSURE)))
    assert "decision" not in through and through["systemMessage"].startswith(NOTICE), through
    assert "no clean strict audit" in through["systemMessage"]
    assert "no passing recalculation" not in through["systemMessage"]
    # a later stop without the words: the audit is still missing, the recalculation is remembered
    later = _decision(run_hook("finish_check", stop(model, sid=sid)))
    assert "decision" not in later and "no clean strict audit" in later["systemMessage"], later
    assert "no passing recalculation" not in later["systemMessage"], later
    (entry,) = _record(_isolated_state, sid)["disclosed"].values()
    assert entry["sha256"] == common.file_sha256(model / "model.xlsx")
    # a new write with the same findings is judged as before: refused once, and the recalculation is missing again
    build_cycle(model, dict(env, FAKE_ROTM_MODE="findings"), sid=sid)
    again = _decision(run_hook("finish_check", stop(model, sid=sid)))
    assert again["decision"] == "block" and "not recalculated: no passing check" in again["reason"], again

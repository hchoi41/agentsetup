"""Tests of the v6 change of package P4 to the contract agreement (round 5 verdict, [A-aera-1, C-sol-1]).

- Explicit refusal and stop words make a reply give no agreement, in `contract agree` (the --reply and the
  task-file channels) and in the contract gate hook, which calls the same function (contract.reads_as_refusal):
  reject, decline and refuse in their verb and noun forms; 'hold off' and 'hold on'; a negation followed by run,
  proceed, start, land, continue or 'go ahead'; 'please wait', and 'wait until' or 'wait for' when not negated;
  the Korean forms 거절, 반려, 거부, 보류, 기다려 and 하지 마.
- Every refusal that is not a change request or a question says that no agreement was given and to wait for the
  user's reply (contract.POSTPONE_MESSAGE).
- No word is required in an agreement and the reply need not name the table: free-form approvals are recorded.

The hook test runs the hook as Claude Code does (python -I, the payload on stdin), with the helpers of
test_hooks.py; it is skipped when PLUGIN/hooks is absent (the skill copied alone).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from lib import common, contract, modelfolder
from test_hooks import HOOKS, ROTM, assert_refused, assert_silent, bash, model, run_hook  # noqa: F401

SCRIPTS = Path(__file__).resolve().parents[1]
hooks_present = pytest.mark.skipif(not (HOOKS / "hooks.json").is_file(),
                                   reason="PLUGIN/hooks is absent (the skill is used without its plugin)")
BY = "Owner"
WAIT = "no agreement was given; wait for the user's reply"


def run_cli(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["ROTM_NO_EXCEL"] = "1"
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", env=env, timeout=300)


def _proposed(tmp_path: Path) -> Path:
    root = tmp_path / "m"
    sample = tmp_path / "sample" / "sales.csv"
    sample.parent.mkdir(parents=True, exist_ok=True)
    sample.write_text("id,region,amount\nA1,East,10\nA2,West,20\nA3,East,30\n", encoding="utf-8")
    contract.propose(root, "sales", csv=sample, key="id", units=["amount=KRW"], totals=["amount=60"])
    return root


def _cpath(root: Path) -> Path:
    return modelfolder.contract_path(root, "sales")


def _task(tmp_path: Path, text: str) -> Path:
    task = tmp_path / "task.md"
    task.write_text(text + "\n", encoding="utf-8")
    return task


# ============================================================================= the entry's sentences

REFUSED = ["Please reject the proposed sales contract.", "Please hold off on running the query.",
           "Do not run the query yet."]
RECORDED = ["agreed", "looks good, go ahead", "No need to wait, go ahead.", "Reviewed, agreed."]


@pytest.mark.parametrize("reply", REFUSED)
def test_a_refusal_or_stop_through_the_reply_records_nothing(tmp_path: Path, reply: str) -> None:
    root = _proposed(tmp_path)
    before = _cpath(root).read_bytes()
    r = run_cli("contract", "agree", str(root), "--table", "sales", "--reply", reply, "--by", BY)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "reads as a refusal or a change request" in r.stderr and WAIT in r.stderr, r.stderr
    assert "no agreement was recorded" in r.stderr and "Traceback" not in r.stderr
    assert _cpath(root).read_bytes() == before and modelfolder.contract_state(root, "sales") == "proposed"


@pytest.mark.parametrize("reply", REFUSED)
def test_a_task_file_that_holds_only_a_refusal_or_stop_records_nothing(tmp_path: Path, reply: str) -> None:
    root = _proposed(tmp_path)
    task = _task(tmp_path, reply)
    before = _cpath(root).read_bytes()
    r = run_cli("contract", "agree", str(root), "--table", "sales", "--reply", reply, "--by", BY,
                "--channel", "task-file", "--source-file", str(task))
    assert r.returncode == 2, r.stdout + r.stderr
    assert "reads as a refusal or a change request" in r.stderr and WAIT in r.stderr, r.stderr
    assert "Traceback" not in r.stderr
    assert _cpath(root).read_bytes() == before and modelfolder.contract_state(root, "sales") == "proposed"


@pytest.mark.parametrize("reply", RECORDED)
def test_free_form_approvals_through_the_reply_are_recorded(tmp_path: Path, reply: str) -> None:
    root = _proposed(tmp_path)
    assert contract.reads_as_refusal(reply) is None, reply
    r = run_cli("contract", "agree", str(root), "--table", "sales", "--reply", reply, "--by", BY)
    assert r.returncode == 0, r.stdout + r.stderr
    data = common.read_json(_cpath(root))
    assert data["agreement"]["reply"] == reply and modelfolder.contract_state(root, "sales") == "agreed"


def test_pre_agreed_words_in_a_task_file_are_recorded(tmp_path: Path) -> None:
    root = _proposed(tmp_path)
    task = _task(tmp_path, "# Task\n\nContract agreement: pre-agreed for this test")
    r = run_cli("contract", "agree", str(root), "--table", "sales", "--reply", "pre-agreed for this test", "--by", BY,
                "--channel", "task-file", "--source-file", str(task))
    assert r.returncode == 0, r.stdout + r.stderr
    data = common.read_json(_cpath(root))
    assert data["agreement"]["reply"] == "pre-agreed for this test" and data["agreement"]["channel"] == "task-file"


# ============================================================================= every form of the words

KOREAN = ["거절합니다", "반려합니다", "거부합니다", "보류해 주세요", "기다려 주세요", "진행하지 마세요", "아직 랜딩하지마"]


@pytest.mark.parametrize("reply", KOREAN)
def test_each_korean_refusal_or_stop_word_is_refused(tmp_path: Path, reply: str) -> None:
    root = _proposed(tmp_path)
    before = _cpath(root).read_bytes()
    with pytest.raises(common.Refusal) as exc:
        contract.agree(root, "sales", reply=reply, by=BY)
    line = exc.value.line()
    assert "reads as a refusal or a change request" in line and WAIT in line, line
    assert _cpath(root).read_bytes() == before and modelfolder.contract_state(root, "sales") == "proposed"


ENGLISH = ["I reject the key.", "Rejected.", "The grain is a rejection of what I asked.", "We decline this contract.",
           "Declined for now.", "I refuse to sign off on this.", "Refusal: the unit is wrong.", "Hold on, the key.",
           "Please hold off.", "Please wait.", "Wait until finance confirms the totals.", "wait for my reply",
           "Waiting for the totals from finance.", "Don't proceed.", "never land it", "Do not continue.",
           "Please don't go ahead yet.", "You cannot start yet.", "No need to run the query.", "do not yet run it"]


@pytest.mark.parametrize("reply", ENGLISH)
def test_each_english_refusal_or_stop_form_is_refused(reply: str) -> None:
    why = contract.reads_as_refusal(reply)
    assert why is not None, reply
    assert contract.refusal_message(why) == f": {WAIT}", (reply, why)


NOT_STOPPED = ["No need to wait, go ahead.", "Agreed, don't wait for finance.", "Agreed, no need to wait for the totals.",
               "Agreed without waiting for the totals.", "No need to hold off, agreed.", "Agreed, go ahead and run it.",
               "Agreed; run the query.", "Agreed. Land it now.", "Looks good, proceed.", "Yes, continue.",
               "Agreed: rejected_qty is a count of returned units.", "동의합니다. 기다릴 필요 없이 진행하세요",
               "Agreed; depreciation uses the declining balance method.", "반려동물 컬럼 포함, 동의합니다",
               "보류하지 말고 진행하세요", "OK, start."]


@pytest.mark.parametrize("reply", NOT_STOPPED)
def test_negated_waits_and_plain_approvals_are_not_refused(tmp_path: Path, reply: str) -> None:
    assert contract.reads_as_refusal(reply) is None, (reply, contract.reads_as_refusal(reply))
    root = _proposed(tmp_path)
    data = contract.agree(root, "sales", reply=reply, by=BY)
    assert data["agreement"]["reply"] == reply and modelfolder.contract_state(root, "sales") == "agreed"


def test_every_refusal_that_is_not_a_change_or_a_question_says_to_wait() -> None:
    assert contract.POSTPONE_MESSAGE == WAIT
    for reply in ("I have not agreed to this column contract.", "no, I do not like it", "I disagree",
                  "Please reject the proposed sales contract.", "Do not run the query yet.", "Not yet."):
        why = contract.reads_as_refusal(reply)
        assert why is not None and contract.refusal_message(why) == f": {WAIT}", (reply, why)
    change = contract.reads_as_refusal("Please change the key to invoice number.")
    assert change.startswith(contract.CHANGE_REASON) and contract.refusal_message(change) == ""
    question = contract.reads_as_refusal("What does the amount column include?")
    assert question.startswith(contract.QUESTION_REASON)
    assert contract.refusal_message(question) == f"; {contract.QUESTION_MESSAGE}"


def test_the_reason_names_the_words(tmp_path: Path) -> None:
    assert contract.reads_as_refusal("Please reject the proposed sales contract.") == "says 'reject'"
    assert contract.reads_as_refusal("Do not run the query yet.") == "says 'do not run'"
    assert contract.reads_as_refusal("Please hold off on running the query.") == \
        f"{contract.POSTPONE_REASON} ('hold off')"
    assert contract.reads_as_refusal("보류해 주세요") == f"{contract.POSTPONE_REASON} ('보류')"
    assert contract.reads_as_refusal("No need to wait, go ahead.") is None
    assert contract.reads_as_refusal("No, wait.") == "starts with 'no'"


def test_a_task_file_sentence_that_stops_refuses_a_shorter_reply(tmp_path: Path) -> None:
    """The task-file channel reads the sentence that holds the reply (contract.refusing_sentence)."""
    root = _proposed(tmp_path)
    task = _task(tmp_path, "# Task\n\nThe contract is agreed, but please hold off on running the query.")
    with pytest.raises(common.Refusal) as exc:
        contract.agree(root, "sales", reply="The contract is agreed", by=BY, channel="task-file", source_file=task)
    line = exc.value.line()
    assert "postpones the decision ('hold off')" in line and WAIT in line, line
    assert modelfolder.contract_state(root, "sales") == "proposed"


# ============================================================================= the contract gate hook

def _transcript(tmp_path: Path, entries: list[dict]) -> Path:
    t = tmp_path / "t.jsonl"
    t.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return t


def _propose_call(root: Path) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_x", "name": "Bash", "input": {
            "command": f'{ROTM} contract propose "{root.as_posix()}" --table sales --csv data/sales.csv --force'}}]}}


def _user(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def _agree(reply: str) -> str:
    return f'{ROTM} contract agree . --table sales --reply "{reply}" --by {BY}'


@hooks_present
@pytest.mark.parametrize("reply", REFUSED)
def test_the_hook_denies_a_refusal_or_stop_the_user_typed(model: Path, tmp_path: Path, reply: str) -> None:
    t = _transcript(tmp_path, [_propose_call(model), _user(reply)])
    out = run_hook("contract_gate", bash(_agree(reply), model, transcript=t))
    assert_refused(out, "reads as a refusal or a change request", WAIT, "no agreement may be recorded")


@hooks_present
def test_the_hook_denies_a_short_reply_from_a_message_that_stops(model: Path, tmp_path: Path) -> None:
    t = _transcript(tmp_path, [_propose_call(model), _user("Looks fine. Please hold off on running the query.")])
    out = run_hook("contract_gate", bash(_agree("Looks fine."), model, transcript=t))
    assert_refused(out, "Please hold off on running the query.", "postpones the decision ('hold off')", WAIT)


@hooks_present
@pytest.mark.parametrize("reply", RECORDED)
def test_the_hook_lets_approvals_through(model: Path, tmp_path: Path, reply: str) -> None:
    t = _transcript(tmp_path, [_propose_call(model), _user(reply)])
    assert_silent(run_hook("contract_gate", bash(_agree(reply), model, transcript=t)))

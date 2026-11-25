"""Tests of the v2 changes of package P7, contract agreement and hooks (round 1 verdict).

- A-aera-3: a reply that holds a negated agreement anywhere is a refusal, in `contract agree` and in the contract
  gate hook; free-form approvals are still recorded.
- C-sol-13: the hook accepts a reply only when the user typed it after the latest proposal or edit of that
  table's contract that the transcript shows; `contract agree` stores the table with the contract hash.
- A-aera-12 and C-sol-7: a stop that the finish check lets through while a workbook is unverified gives one line
  the user sees (systemMessage) and no decision.
- J-4 (a): `contract propose` writes a display format for each number column from the sample.
- Supporting A-aera-9: propose compares keys exactly (common.key_tuple).
- Supporting O-1: the hook test fixtures hold no machine-specific path.

The hook tests run each hook as Claude Code does (python -I, the payload on stdin), with the helpers of
test_hooks.py. They are skipped when PLUGIN/hooks is absent (the skill copied alone).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from lib import common, contract, modelfolder, schemas
from test_hooks import (DATA, HOOKS, ROTM, assert_refused, assert_silent, bash, build_cycle, fake_rotm,  # noqa: F401
                        model, outside, pre, run_hook, status_pass, stop, write_contract)

SCRIPTS = Path(__file__).resolve().parents[1]
hooks_present = pytest.mark.skipif(not (HOOKS / "hooks.json").is_file(),
                                   reason="PLUGIN/hooks is absent (the skill is used without its plugin)")
BY = "Owner"


def _csv(path: Path, header: list[str], rows: list[list[str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(",".join(r) for r in [header, *rows]) + "\n", encoding="utf-8")
    return path


def _proposed(tmp_path: Path, table: str = "sales") -> Path:
    root = tmp_path / "m"
    sample = _csv(tmp_path / "sample" / f"{table}.csv", ["id", "region", "amount"],
                  [["A1", "East", "10"], ["A2", "West", "20"], ["A3", "East", "30"]])
    contract.propose(root, table, csv=sample, key="id")
    return root


def _transcript(tmp_path: Path, entries: list[dict], name: str = "t.jsonl") -> Path:
    t = tmp_path / name
    t.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return t


def _user(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def _tool(name: str, **tool_input: object) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_x", "name": name, "input": dict(tool_input)}]}}


def _propose(root: Path, table: str) -> dict:
    return _tool("Bash", command=f'{ROTM} contract propose "{root.as_posix()}" --table {table} --csv data/{table}.csv '
                                 "--force")


def _agree(table: str, reply: str = "agreed", extra: str = "") -> str:
    return f'{ROTM} contract agree . --table {table} --reply "{reply}" --by {BY}{extra}'


# ============================================================================= A-aera-3: refusal reading

REFUSED = ["I have not agreed to this column contract.", "not agreed yet", "I don't agree with the key",
           "I haven't agreed yet", "Ok so far, but I disagree with the grain", "We cannot agree to this key",
           "Looks fine, but not yet", "The key isn’t right", "아직 동의하지 않았습니다", "단위는 동의 안 해요",
           "키는 아직 승인 전입니다", "이 계약에는 동의할 수 없습니다"]
RECORDED = ["agreed", "looks good, go ahead", "I have no changes, agreed", "yeah. good.",
            "lookw tooe. i like the setup.", "ok, agreed. amount is net of VAT.", "동의합니다", "좋아요, 진행하세요",
            "전부동의합니다"]


@pytest.mark.parametrize("reply", REFUSED)
def test_a_negated_agreement_anywhere_is_refused_by_the_command(tmp_path: Path, reply: str) -> None:
    root = _proposed(tmp_path)
    with pytest.raises(common.Refusal) as exc:
        contract.agree(root, "sales", reply=reply, by=BY)
    assert "reads as a refusal or a change request" in exc.value.line()
    assert modelfolder.contract_state(root, "sales") == "proposed"


@pytest.mark.parametrize("reply", RECORDED)
def test_free_form_approvals_are_recorded(tmp_path: Path, reply: str) -> None:
    root = _proposed(tmp_path)
    data = contract.agree(root, "sales", reply=reply, by=BY)
    assert data["agreement"]["reply"] == reply
    assert modelfolder.contract_state(root, "sales") == "agreed"


def test_pre_agreed_words_in_a_task_file_are_recorded(tmp_path: Path) -> None:
    root = _proposed(tmp_path)
    task = tmp_path / "task.md"
    task.write_text("# Task\n\nContract agreement: pre-agreed for this test\n", encoding="utf-8")
    contract.agree(root, "sales", reply="pre-agreed for this test", by=BY, channel="task-file", source_file=task)
    assert modelfolder.contract_state(root, "sales") == "agreed"


def test_the_refusal_reason_names_the_words(tmp_path: Path) -> None:
    assert contract.reads_as_refusal("I have not agreed to this column contract.") == "says 'not agreed'"
    assert contract.reads_as_refusal("no, change it") == "starts with 'no'"
    assert contract.reads_as_refusal("I have no changes, agreed") is None
    r = subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), "contract", "agree",
                        str(_proposed(tmp_path)), "--table", "sales", "--reply", "I don't agree with the key",
                        "--by", BY], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 2 and "the reply says 'don't agree'" in r.stderr and "Traceback" not in r.stderr


def test_the_contract_module_imports_under_isolated_python() -> None:
    """The contract gate hook imports lib.contract under python -I, which hides openpyxl."""
    code = ("import sys; sys.path.insert(0, sys.argv[1]); from lib import contract; "
            "print('openpyxl' in sys.modules, contract.reads_as_refusal('not agreed yet') is not None)")
    proc = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(SCRIPTS)], capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split() == ["False", "True"]


@hooks_present
@pytest.mark.parametrize("reply", REFUSED[:3])
def test_the_hook_refuses_a_negated_agreement_the_user_typed(model: Path, tmp_path: Path, reply: str) -> None:
    t = _transcript(tmp_path, [_propose(model, "sales"), _user(reply)])
    out = run_hook("contract_gate", bash(_agree("sales", reply), model, transcript=t))
    assert_refused(out, "reads as a refusal or a change request", "contract agree")
    # the task-file channel is refused by the hook too (rotm.py would refuse it as well)
    out = run_hook("contract_gate", bash(_agree("sales", reply, " --channel task-file --source-file task.md"), model,
                                         transcript=t))
    assert_refused(out, "reads as a refusal or a change request")


@hooks_present
@pytest.mark.parametrize("reply", RECORDED[:5])
def test_the_hook_lets_free_form_approvals_through(model: Path, tmp_path: Path, reply: str) -> None:
    t = _transcript(tmp_path, [_propose(model, "sales"), _user(reply)])
    assert_silent(run_hook("contract_gate", bash(_agree("sales", reply), model, transcript=t)))


# ============================================================================= C-sol-13: reply after the proposal

@hooks_present
def test_a_reply_given_before_the_second_proposal_is_refused_for_the_second_table(model: Path,
                                                                                   tmp_path: Path) -> None:
    write_contract(model, "stock", agreed=False)
    t = _transcript(tmp_path, [_user("build the model"), _propose(model, "sales"), _user("agreed"),
                               _propose(model, "stock")])
    assert_refused(run_hook("contract_gate", bash(_agree("stock"), model, transcript=t)),
                   "typed before the latest proposal or edit of contracts/stock.contract.json", "contract show")
    assert_silent(run_hook("contract_gate", bash(_agree("sales"), model, transcript=t)))


@hooks_present
def test_a_reply_given_after_both_proposals_is_accepted_for_both(model: Path, tmp_path: Path) -> None:
    write_contract(model, "stock", agreed=False)
    t = _transcript(tmp_path, [_propose(model, "sales"), _propose(model, "stock"), _user("agreed")])
    assert_silent(run_hook("contract_gate", bash(_agree("sales"), model, transcript=t)))
    assert_silent(run_hook("contract_gate", bash(_agree("stock"), model, transcript=t)))


@hooks_present
@pytest.mark.parametrize("edit", ["Edit", "Write", "MultiEdit", "sed", "python"])
def test_an_edit_of_the_contract_after_the_reply_makes_the_reply_refused(model: Path, tmp_path: Path,
                                                                        edit: str) -> None:
    path = (model / "contracts" / "sales.contract.json").as_posix()
    tool = {"Edit": _tool("Edit", file_path=path, old_string="KRW", new_string="USD"),
            "Write": _tool("Write", file_path=path, content="{}"),
            "MultiEdit": _tool("MultiEdit", file_path=path, edits=[{"old_string": "KRW", "new_string": "USD"}]),
            "sed": _tool("Bash", command="sed -i 's/KRW/USD/' contracts/sales.contract.json"),
            "python": _tool("Bash", command="python -c \"open('contracts/sales.contract.json','w').write('{}')\"")}[edit]
    tool["cwd"] = model.as_posix()
    t = _transcript(tmp_path, [_propose(model, "sales"), _user("agreed"), tool])
    assert_refused(run_hook("contract_gate", bash(_agree("sales"), model, transcript=t)),
                   "typed before the latest proposal or edit")
    # a reply typed after the edit is accepted; the earlier words alone still are not
    t2 = _transcript(tmp_path, [_propose(model, "sales"), _user("agreed"), tool, _user("agreed, unit USD")],
                     name="t2.jsonl")
    assert_silent(run_hook("contract_gate", bash(_agree("sales", "agreed, unit USD"), model, transcript=t2)))


@hooks_present
def test_proposing_and_agreeing_in_one_command_is_refused(model: Path, tmp_path: Path) -> None:
    t = _transcript(tmp_path, [_user("agreed")])
    cmd = f'{ROTM} contract propose . --table sales --columns id,amount:number --force && {_agree("sales")}'
    assert_refused(run_hook("contract_gate", bash(cmd, model, transcript=t)),
                   "proposes or changes the contract of table 'sales' and records an agreement for it in the same call")


@hooks_present
def test_a_proposal_in_another_model_folder_does_not_count(model: Path, tmp_path: Path) -> None:
    other = tmp_path / "other_model"
    modelfolder.init_model(other, name="other_model")
    write_contract(other, "sales", agreed=False)
    t = _transcript(tmp_path, [_propose(model, "sales"), _user("agreed"), _propose(other, "sales")])
    assert_silent(run_hook("contract_gate", bash(_agree("sales"), model, transcript=t)))


def test_agree_stores_the_table_with_the_contract_hash(tmp_path: Path) -> None:
    root = _proposed(tmp_path)
    data = contract.agree(root, "sales", reply="agreed", by=BY)
    ag = data["agreement"]
    assert ag["table"] == "sales" and ag["contract_sha256"] == modelfolder.contract_hash(data)
    assert schemas.validate(common.read_json(modelfolder.contract_path(root, "sales")), "contract") == []


# ============================================================================= A-aera-12, C-sol-7: the notice

def _notice(out) -> str:
    assert out.code == 0 and out.stderr == "", out
    data = out.json()
    assert "decision" not in data and set(data) == {"systemMessage"}, data
    msg = data["systemMessage"]
    assert "\n" not in msg
    return msg


@hooks_present
def test_an_unchanged_second_stop_gives_no_block_and_gives_the_notice(model: Path, fake_rotm: tuple) -> None:
    env, _log = fake_rotm
    build_cycle(model, env, sid="sess-n1")
    assert run_hook("finish_check", stop(model, sid="sess-n1")).json()["decision"] == "block"
    msg = _notice(run_hook("finish_check", stop(model, sid="sess-n1", active=True)))
    # v6 (r5 verdict, C-sol-5): the command names the workbook file itself
    wb = re.fullmatch(r"rule-of-thumb-model finish check: not verified at the end of the session: model\.xlsx: no "
                      r"passing recalculation \(rotm\.py check \"([^\"]+)\"\), and the last message does not say "
                      r"'not recalculated'\.", msg)
    assert wb is not None and Path(wb.group(1)).resolve() == (model / "model.xlsx").resolve(), msg
    # a later stop with no new write: still no second refusal (plan F16 c), and the same notice
    assert _notice(run_hook("finish_check", stop(model, sid="sess-n1"))) == msg


@hooks_present
def test_a_second_stop_after_an_honest_message_gives_neither(model: Path, fake_rotm: tuple) -> None:
    env, _log = fake_rotm
    build_cycle(model, env, sid="sess-n2")
    assert run_hook("finish_check", stop(model, sid="sess-n2")).json()["decision"] == "block"
    assert_silent(run_hook("finish_check", stop(model, sid="sess-n2", active=True,
                                                last="Built and audited; the workbook was not recalculated.")))


@hooks_present
def test_the_notice_names_a_failed_audit_even_after_not_recalculated(model: Path, fake_rotm: tuple) -> None:
    env, _log = fake_rotm
    build_cycle(model, dict(env, FAKE_ROTM_MODE="findings"), sid="sess-n3")
    assert run_hook("finish_check", stop(model, sid="sess-n3")).json()["decision"] == "block"
    msg = _notice(run_hook("finish_check", stop(model, sid="sess-n3", active=True,
                                                last="The workbook was not recalculated.")))
    # v6 (r5 verdict, C-sol-5): the command names the workbook file itself
    assert re.search(r"model\.xlsx: no clean strict audit \(rotm\.py audit \"[^\"]+/model\.xlsx\" --strict\)\.$", msg), msg
    assert "recalculation" not in msg


@hooks_present
def test_a_verified_workbook_gives_no_notice(model: Path, fake_rotm: tuple) -> None:
    env, _log = fake_rotm
    build_cycle(model, env, sid="sess-n4")
    status_pass(model)
    assert_silent(run_hook("finish_check", stop(model, sid="sess-n4", active=True)))


@hooks_present
def test_outside_a_model_folder_the_stop_gives_nothing(model: Path, outside: Path, fake_rotm: tuple) -> None:
    env, _log = fake_rotm
    assert_silent(run_hook("finish_check", stop(outside, sid="sess-none", active=True)))
    build_cycle(model, env, sid="sess-n5")
    (model / modelfolder.MARKER).unlink()                 # the folder is no longer a model folder
    assert_silent(run_hook("finish_check", stop(model, sid="sess-n5")))
    assert_silent(run_hook("finish_check", stop(model, sid="sess-n5", active=True)))


# ============================================================================= J-4 (a): display formats

def _propose_numbers(tmp_path: Path, values: list[str], ctype: str | None = None) -> dict:
    rows = [[f"K{i}", v, str(i + 1)] for i, v in enumerate(values)]
    sample = _csv(tmp_path / "s.csv", ["id", "value", "qty"], rows)
    path = contract.propose(tmp_path / "m", "t1", csv=sample, key="id",
                            types=[f"value={ctype}"] if ctype else None)
    data = common.read_json(path)
    assert schemas.validate(data, "contract") == []
    return data


def _formats(data: dict) -> dict:
    return {c["name"]: c.get("format") for c in data["columns"]}


def test_propose_shows_values_with_decimals_with_two_decimals(tmp_path: Path) -> None:
    data = _propose_numbers(tmp_path, ["7.25", "12.5", "3"])
    assert _formats(data) == {"id": None, "value": "amount2", "qty": "count"}
    md = (tmp_path / "m" / "contracts" / "t1.contract.md").read_text(encoding="utf-8")
    assert "| # | Column | Type | Unit | Format | Null rule |" in md
    assert "| 2 | value | number | ? | amount2 | never |" in md and "| 1 | id | text | - | default | never |" in md
    notes = " ".join(data["notes"])
    assert ("display formats proposed from the sample (keys of the style dictionary's number_formats, column "
            "'Format' above): value amount2 (2 decimals; the values hold decimals); qty count (0 decimals; whole "
            "numbers)") in notes
    assert "Points to confirm:" in md and "value amount2 (2 decimals; the values hold decimals)" in md


def test_propose_gives_values_mostly_below_1_enough_decimals(tmp_path: Path) -> None:
    data = _propose_numbers(tmp_path, ["0.0123", "0.0456", "0.0789", "1.5"])
    assert _formats(data)["value"] == "factor"
    assert "value factor (4 decimals; most values are below 1 and need 4 decimals)" in " ".join(data["notes"])


def test_propose_gives_general_when_no_number_format_has_enough_decimals(tmp_path: Path) -> None:
    data = _propose_numbers(tmp_path, ["0.00012", "0.00034"])
    assert _formats(data)["value"] == "general"
    assert "value general (General, Excel's default; most values are below 1 and need 5 decimals" \
        in " ".join(data["notes"])


def test_propose_keeps_the_type_default_for_whole_numbers(tmp_path: Path) -> None:
    data = _propose_numbers(tmp_path, ["100", "250.0", "-3"], ctype="number")
    assert _formats(data)["value"] == "amount" and _formats(data)["qty"] == "count"


def test_a_column_list_without_a_sample_gets_no_format(tmp_path: Path) -> None:
    path = contract.propose(tmp_path / "m", "t1", columns="id,amount:number", key="id")
    assert all("format" not in c for c in common.read_json(path)["columns"])


def test_proposed_format_unit_rules() -> None:
    from lib import dictionary
    d = dictionary.load_dictionary()
    assert contract.proposed_format([1.0, 2.0, None], "number", d) == ("amount", "whole numbers")
    assert contract.proposed_format([1, 2], "integer", d) == ("count", "whole numbers")
    assert contract.proposed_format([0.5, 0.25, 0.0, 0.0, 0.0], "number", d)[0] == "amount2"   # zeros not counted
    assert contract.proposed_format([0.125, 2.5, 3.5], "number", d)[0] == "amount2"           # mostly 1 or more
    assert contract._plain_decimals('#,##0.00_);(#,##0.00);"-"_)') == 2
    assert contract._plain_decimals("0.0%") is None and contract._plain_decimals("yyyy-mm-dd") is None


# ============================================================================= supporting A-aera-9 and O-1

def test_propose_compares_keys_exactly(tmp_path: Path) -> None:
    sample = _csv(tmp_path / "k.csv", ["code", "amount"], [["T1", "1"], ["t1", "2"], ["001", "3"], ["1", "4"]])
    prof = contract.profile_csv(sample)
    assert ["code"] in contract.key_candidates(prof, ["text", "integer"])
    data = common.read_json(contract.propose(tmp_path / "m", "k1", csv=sample, key="code"))
    assert "key_repeats" not in data["expect"]
    assert not any("repeats" in n for n in data["notes"])


def test_hook_fixtures_hold_no_machine_specific_path() -> None:
    patterns = [re.compile("[A-Za-z]:[\\\\/]" + "Us" + "ers" + "[\\\\/]", re.IGNORECASE),
                re.compile("C:[\\\\/]" + "000" + "\\."), re.compile("One" + "Drive" + "(?:[\\\\/]| - )")]
    files = sorted(p for p in DATA.iterdir() if p.is_file())
    assert len(files) >= 6
    for f in files:
        text = f.read_text(encoding="utf-8")
        for pat in patterns:
            assert not pat.search(text), (f.name, pat.pattern)

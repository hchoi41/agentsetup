"""Tests of the v3 changes of package P4, contract and hooks (round 2 verdict).

- C-sol-3: `contract propose` and `contract show` record agreement.shown_sha256 (the content hash of the JSON the
  .md was rendered from); `contract agree` refuses (F-SHOWN) when the .md is missing, differs from the text rendered
  from the current JSON, or was rendered from other content.
- A-aera-3, C-sol-4: a reply that asks for a change is refused (R-CHANGE), by `contract agree` and by the contract
  gate hook; on the task-file channel every sentence of the task file that holds the reply must not read as a
  refusal.
- C-sol-14: the raw-sheet guard lets a copy (cp, Copy-Item, copy, xcopy, robocopy, shutil.copy) to a workbook name
  that does not exist yet through; a copy over an existing workbook and every write into raw/ stay refused.
- J-4: `contract propose` notes a table name longer than 12 characters (N-NAME); `contract remove` moves a table's
  files into MODEL/removed/<t>_<time>/ and is refused while the specification names the table.

The hook tests run each hook as Claude Code does (python -I, the payload on stdin), with the helpers of
test_hooks.py. They are skipped when PLUGIN/hooks is absent (the skill copied alone).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from lib import common, contract, modelfolder, schemas
from test_hooks import (HOOKS, ROTM, assert_refused, assert_silent, bash, make_workbook, model,  # noqa: F401
                        pwsh, run_hook)

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
hooks_present = pytest.mark.skipif(not (HOOKS / "hooks.json").is_file(),
                                   reason="PLUGIN/hooks is absent (the skill is used without its plugin)")
BY = "Owner"


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", env=dict(os.environ), timeout=120)


def _csv(path: Path, header: list[str], rows: list[list[str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(",".join(r) for r in [header, *rows]) + "\n", encoding="utf-8")
    return path


def _proposed(tmp_path: Path, table: str = "sales", total: str | None = "amount=60") -> Path:
    root = tmp_path / "m"
    sample = _csv(tmp_path / "sample" / f"{table}.csv", ["id", "region", "amount"],
                  [["A1", "East", "10"], ["A2", "West", "20"], ["A3", "East", "30"]])
    contract.propose(root, table, csv=sample, key="id", units=["amount=KRW"], totals=[total] if total else None)
    return root


def _cpath(root: Path, table: str = "sales") -> Path:
    return modelfolder.contract_path(root, table)


def _md(root: Path, table: str = "sales") -> Path:
    return root / "contracts" / f"{table}.contract.md"


def _edit_total(root: Path, value: int) -> None:
    """An edit of the proposed JSON, as an agent makes it with a text tool (the .md is not rewritten)."""
    data = common.read_json(_cpath(root))
    data["expect"]["totals"][0]["value"] = value
    common.write_json(_cpath(root), data)


# ============================================================================= C-sol-3: shown hash, F-SHOWN

def test_propose_records_the_hash_of_the_content_the_md_shows(tmp_path: Path) -> None:
    root = _proposed(tmp_path)
    data = common.read_json(_cpath(root))
    assert data["agreement"]["shown_sha256"] == modelfolder.contract_hash(data)
    assert schemas.validate(data, "contract") == []
    assert _md(root).read_text(encoding="utf-8") == contract.render_md(data)


def test_agree_refuses_a_contract_edited_after_it_was_shown_and_records_after_show(tmp_path: Path) -> None:
    root = _proposed(tmp_path)
    _edit_total(root, 61)
    before = _cpath(root).read_bytes()
    with pytest.raises(common.Refusal) as exc:
        contract.agree(root, "sales", reply="agreed", by=BY)
    line = exc.value.line()
    assert "the contract changed after it was last shown" in line and "no agreement was recorded" in line
    assert "contract show" in line and "--table sales" in line and "record their new reply" in line
    assert _cpath(root).read_bytes() == before and modelfolder.contract_state(root, "sales") == "proposed"
    # the command line: exit 2, one refusal, no traceback
    r = run_cli("contract", "agree", str(root), "--table", "sales", "--reply", "agreed", "--by", BY)
    assert r.returncode == 2 and "changed after it was last shown" in r.stderr and "Traceback" not in r.stderr
    # after contract show the .md shows the edited total, and the reply is recorded
    text, rewritten = contract.show(root, "sales")
    assert rewritten and "Control total: sum of amount = 61 (tolerance 0)" in text
    data = common.read_json(_cpath(root))
    assert data["agreement"]["shown_sha256"] == modelfolder.contract_hash(data)
    contract.agree(root, "sales", reply="agreed", by=BY)
    assert modelfolder.contract_state(root, "sales") == "agreed"


def test_agree_refuses_an_md_edited_by_hand(tmp_path: Path) -> None:
    root = _proposed(tmp_path)
    md = _md(root)
    md.write_text(md.read_text(encoding="utf-8").replace("= 60 (tolerance", "= 59 (tolerance"), encoding="utf-8")
    with pytest.raises(common.Refusal) as exc:
        contract.agree(root, "sales", reply="agreed", by=BY)
    assert "differs from the text rendered from contracts/sales.contract.json" in exc.value.line()
    assert modelfolder.contract_state(root, "sales") == "proposed"
    contract.show(root, "sales")
    contract.agree(root, "sales", reply="agreed", by=BY)
    assert modelfolder.contract_state(root, "sales") == "agreed"


def test_agree_refuses_when_the_md_is_missing(tmp_path: Path) -> None:
    root = _proposed(tmp_path)
    _md(root).unlink()
    with pytest.raises(common.Refusal) as exc:
        contract.agree(root, "sales", reply="agreed", by=BY)
    assert "contracts/sales.contract.md is missing" in exc.value.line()
    assert modelfolder.contract_state(root, "sales") == "proposed"


def test_a_contract_without_the_shown_hash_is_compared_by_the_md_text(tmp_path: Path) -> None:
    """A contract proposed before v3 has no agreement.shown_sha256: only the .md text is compared."""
    root = _proposed(tmp_path)
    data = common.read_json(_cpath(root))
    del data["agreement"]["shown_sha256"]
    common.write_json(_cpath(root), data)
    contract.agree(root, "sales", reply="agreed", by=BY)
    assert modelfolder.contract_state(root, "sales") == "agreed"
    # without the hash, an edit that the .md does not show is still refused
    root2 = _proposed(tmp_path / "two")
    data = common.read_json(_cpath(root2))
    del data["agreement"]["shown_sha256"]
    data["expect"]["totals"][0]["value"] = 61
    common.write_json(_cpath(root2), data)
    with pytest.raises(common.Refusal, match="changed after it was last shown"):
        contract.agree(root2, "sales", reply="agreed", by=BY)


def test_an_edit_the_md_does_not_show_is_caught_by_the_shown_hash(tmp_path: Path) -> None:
    root = _proposed(tmp_path)
    data = common.read_json(_cpath(root))
    data["proposed_by"] = "an edit of a field the .md does not show"
    common.write_json(_cpath(root), data)
    assert _md(root).read_text(encoding="utf-8") == contract.render_md(data)    # the text alone cannot tell
    with pytest.raises(common.Refusal) as exc:
        contract.agree(root, "sales", reply="agreed", by=BY)
    assert "was rendered from other content" in exc.value.line()


def test_show_writes_the_json_only_when_the_shown_hash_differs(tmp_path: Path) -> None:
    root = _proposed(tmp_path)
    before = _cpath(root).read_bytes()
    assert contract.show(root, "sales")[1] is False
    assert _cpath(root).read_bytes() == before                    # nothing to record: the JSON is not written
    _edit_total(root, 70)
    edited = _cpath(root).read_bytes()
    contract.show(root, "sales")
    after = _cpath(root).read_bytes()
    assert after != edited                                         # the new shown hash is recorded
    data = common.read_json(_cpath(root))
    assert data["agreement"]["shown_sha256"] == modelfolder.contract_hash(data)
    assert data["expect"]["totals"][0]["value"] == 70              # and nothing else changed
    contract.show(root, "sales")
    assert _cpath(root).read_bytes() == after


def test_show_keeps_an_agreed_contract_agreed(tmp_path: Path) -> None:
    root = _proposed(tmp_path)
    contract.agree(root, "sales", reply="agreed", by=BY)
    data = common.read_json(_cpath(root))
    assert data["agreement"]["shown_sha256"] == data["agreement"]["contract_sha256"]
    del data["agreement"]["shown_sha256"]
    common.write_json(_cpath(root), data)
    contract.show(root, "sales")                                   # records the field again
    assert modelfolder.contract_state(root, "sales") == "agreed"
    assert "shown_sha256" in common.read_json(_cpath(root))["agreement"]


def test_a_contract_copied_from_a_template_can_be_agreed(tmp_path: Path) -> None:
    """rotm.py init --template rewrites only the status line of the .md; that line is not compared."""
    root = tmp_path / "from_template"
    modelfolder.init_model(root, name="from_template", template="starter")
    assert modelfolder.contract_state(root, "lines") == "proposed"
    task = tmp_path / "task.md"
    task.write_text("The user's reply to the contract: agreed for this test\n", encoding="utf-8")
    contract.agree(root, "lines", reply="agreed for this test", by=BY, channel="task-file", source_file=task)
    assert modelfolder.contract_state(root, "lines") == "agreed"


def _shipped_contracts() -> list[Path]:
    out = sorted((SKILL / "examples").glob("*/contracts/*.contract.json"))
    out += sorted((SKILL / "templates").glob("*/contracts/*.contract.json"))
    out += sorted((PLUGIN / "templates").glob("*/contracts/*.contract.json"))
    return out


def _shipped_folders() -> list[Path]:
    """The shipped model folders that are present: SKILL/examples/*, SKILL/templates/* and, when the skill is used
    with its plugin, PLUGIN/templates/* (round 3 verdict, A-aera-7: the skill copied alone has no PLUGIN/templates)."""
    roots = [SKILL / "examples", SKILL / "templates", PLUGIN / "templates"]
    return sorted(d for r in roots if r.is_dir() for d in r.iterdir() if (d / modelfolder.MARKER).is_file())


def test_the_shipped_contracts_md_files_equal_their_rendered_text() -> None:
    """Read-only: each shipped contracts/<t>.contract.md equals render_md of its JSON (the integrator regenerates
    them with contract show, which also records agreement.shown_sha256). The expected count is computed from the
    model folders present, not written as a number (A-aera-7)."""
    folders = _shipped_folders()
    assert (SKILL / "examples" / "sales_vs_target") in folders and (SKILL / "templates" / "starter") in folders
    expected = sum(len(list((d / "contracts").glob("*.contract.json"))) for d in folders)
    assert all(list((d / "contracts").glob("*.contract.json")) for d in folders), "a shipped folder has no contract"
    paths = _shipped_contracts()
    assert len(paths) == expected
    differ = []
    for p in paths:
        data = json.loads(p.read_text(encoding="utf-8"))
        md = p.with_name(p.name[: -len(".contract.json")] + ".contract.md")
        if not md.is_file() or md.read_text(encoding="utf-8") != contract.render_md(data):
            differ.append(p.relative_to(PLUGIN).as_posix())
    assert not differ, differ


def test_shown_sha256_is_outside_the_hashed_content() -> None:
    c = {"rotm_contract": 1, "table": "t1", "grain": "one row per x", "key": ["x"],
         "columns": [{"name": "x", "type": "text", "null_rule": "never"}], "expect": {"rows": {"min": 1, "max": 9}},
         "agreement": {"status": "proposed"}}
    h = modelfolder.contract_hash(c)
    c["agreement"]["shown_sha256"] = h
    assert modelfolder.contract_hash(c) == h and schemas.validate(c, "contract") == []
    c["agreement"]["shown_sha256"] = "not a hash"
    assert any("shown_sha256" in e.line() for e in schemas.validate(c, "contract"))


# ============================================================================= A-aera-3, C-sol-4: change requests

CHANGE_REFUSED = [
    "Please change the key to invoice number before landing.",
    "Sales is good. Please change the unit in target.",
    "Can you rename amount to net_amount?",
    "The unit should be changed to USD.",
    "Looks good, but drop the region column.",
    "Replace the key with invoice_id.",
    "The grain needs to change to one row per invoice.",
    "Correct the unit, then it is fine.",
    "단위를 USD로 바꿔 주세요",
    "키를 수정해 주세요",
]
CHANGE_RECORDED = [
    "agreed",
    "looks good, go ahead",
    "I have no changes, agreed",
    "agreed, nothing to change",
    "No changes, agreed.",
    "Agreed, change nothing.",
    "Agreed without changes.",
    "That is correct, agreed.",
    "Correct, go ahead.",
    "ok, agreed. amount is net of VAT.",
    "변경해야 할 부분은 없습니다. 동의합니다",
]


@pytest.mark.parametrize("reply", CHANGE_REFUSED)
def test_a_change_request_is_refused_by_the_command(tmp_path: Path, reply: str) -> None:
    root = _proposed(tmp_path)
    why = contract.reads_as_refusal(reply)
    assert why is not None and why.startswith("asks for a change ('"), why
    with pytest.raises(common.Refusal) as exc:
        contract.agree(root, "sales", reply=reply, by=BY)
    line = exc.value.line()
    assert "asks for a change" in line and "reads as a refusal or a change request" in line
    assert "make the change, run" in line and "contract show" in line
    assert "record their reply to the new version" in line
    assert modelfolder.contract_state(root, "sales") == "proposed"


@pytest.mark.parametrize("reply", CHANGE_RECORDED)
def test_approvals_without_a_change_are_recorded(tmp_path: Path, reply: str) -> None:
    root = _proposed(tmp_path)
    assert contract.reads_as_refusal(reply) is None, reply
    data = contract.agree(root, "sales", reply=reply, by=BY)
    assert data["agreement"]["reply"] == reply
    assert modelfolder.contract_state(root, "sales") == "agreed"


def test_the_agreed_part_of_a_reply_is_recorded_for_its_table(tmp_path: Path) -> None:
    root = _proposed(tmp_path)
    contract.agree(root, "sales", reply="Sales is good.", by=BY)
    assert modelfolder.contract_state(root, "sales") == "agreed"


def test_the_command_line_refuses_a_change_request(tmp_path: Path) -> None:
    root = _proposed(tmp_path)
    r = run_cli("contract", "agree", str(root), "--table", "sales", "--reply",
                "Please change the key to invoice number before landing.", "--by", BY)
    assert r.returncode == 2 and "asks for a change ('please change')" in r.stderr
    assert "Traceback" not in r.stderr


def test_a_task_file_sentence_that_refuses_is_refused(tmp_path: Path) -> None:
    root = _proposed(tmp_path)
    task = tmp_path / "task.md"
    task.write_text("This proposal is not agreed.\n", encoding="utf-8")
    with pytest.raises(common.Refusal) as exc:
        contract.agree(root, "sales", reply="agreed", by=BY, channel="task-file", source_file=task)
    line = exc.value.line()
    assert "'This proposal is not agreed.'" in line and "reads as a refusal or a change request" in line
    assert modelfolder.contract_state(root, "sales") == "proposed"
    # a task file that agrees in one sentence and refuses in another: the short reply is in both, so it is
    # refused; the sentence that agrees, copied exactly, is recorded
    task.write_text("# Task\n\nContract: agreed as proposed.\n\nIf the contract is not agreed, stop.\n",
                    encoding="utf-8")
    with pytest.raises(common.Refusal, match="If the contract is not agreed, stop."):
        contract.agree(root, "sales", reply="agreed", by=BY, channel="task-file", source_file=task)
    contract.agree(root, "sales", reply="Contract: agreed as proposed.", by=BY, channel="task-file",
                   source_file=task)
    assert modelfolder.contract_state(root, "sales") == "agreed"


def test_pre_agreed_words_in_a_task_file_are_still_recorded(tmp_path: Path) -> None:
    root = _proposed(tmp_path)
    task = tmp_path / "task.md"
    task.write_text("# Task\n\nContract agreement: pre-agreed for this test\n\n- Land the CSV after the agreement.\n",
                    encoding="utf-8")
    contract.agree(root, "sales", reply="pre-agreed for this test", by=BY, channel="task-file", source_file=task)
    assert modelfolder.contract_state(root, "sales") == "agreed"


def test_a_change_request_in_the_task_file_sentence_is_refused(tmp_path: Path) -> None:
    root = _proposed(tmp_path)
    task = tmp_path / "task.md"
    task.write_text("Reply: agreed, but please change the unit to USD.\n", encoding="utf-8")
    with pytest.raises(common.Refusal, match="asks for a change"):
        contract.agree(root, "sales", reply="agreed", by=BY, channel="task-file", source_file=task)


def _transcript(tmp_path: Path, entries: list[dict], name: str = "t.jsonl") -> Path:
    t = tmp_path / name
    t.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return t


def _user(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def _propose_call(root: Path, table: str) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_p", "name": "Bash",
         "input": {"command": f'{ROTM} contract propose "{root.as_posix()}" --table {table} --csv data/{table}.csv '
                              "--force"}}]}}


def _agree_cmd(table: str, reply: str, extra: str = "") -> str:
    return f'{ROTM} contract agree . --table {table} --reply "{reply}" --by {BY}{extra}'


@hooks_present
@pytest.mark.parametrize("reply", CHANGE_REFUSED)
def test_the_hook_refuses_a_change_request_the_user_typed(model: Path, tmp_path: Path, reply: str) -> None:
    t = _transcript(tmp_path, [_propose_call(model, "target"), _user(reply)])
    out = run_hook("contract_gate", bash(_agree_cmd("target", reply), model, transcript=t))
    assert_refused(out, "asks for a change", "reads as a refusal or a change request",
                   "the reply asks for a change: make the change, run", "contract show", "--table target",
                   "record their reply to the new version")
    assert len(out.stderr.strip().splitlines()) == 1


@hooks_present
@pytest.mark.parametrize("reply", CHANGE_RECORDED)
def test_the_hook_lets_approvals_without_a_change_through(model: Path, tmp_path: Path, reply: str) -> None:
    t = _transcript(tmp_path, [_propose_call(model, "sales"), _user(reply)])
    assert_silent(run_hook("contract_gate", bash(_agree_cmd("sales", reply), model, transcript=t)))


@hooks_present
def test_the_hook_accepts_the_agreed_sentence_of_a_mixed_reply(model: Path, tmp_path: Path) -> None:
    message = "Sales is good. Please change the unit in target."
    t = _transcript(tmp_path, [_propose_call(model, "sales"), _propose_call(model, "target"), _user(message)])
    assert_silent(run_hook("contract_gate", bash(_agree_cmd("sales", "Sales is good."), model, transcript=t)))
    assert_refused(run_hook("contract_gate", bash(_agree_cmd("target", message), model, transcript=t)),
                   "asks for a change ('please change')")


@hooks_present
def test_the_hook_leaves_the_task_file_sentence_to_rotm(model: Path, tmp_path: Path) -> None:
    """The hook lets --channel task-file through when the reply itself does not refuse; rotm.py reads the file."""
    t = _transcript(tmp_path, [_user("build the model")])
    cmd = _agree_cmd("sales", "agreed", " --channel task-file --source-file task.md")
    assert_silent(run_hook("contract_gate", bash(cmd, model, transcript=t)))


# ============================================================================= C-sol-14: copies to a new workbook

@pytest.fixture
def download(tmp_path: Path) -> Path:
    d = tmp_path / "downloads"
    d.mkdir()
    make_workbook(d / "export.xlsx", 3)
    return d / "export.xlsx"


def _py(code: str) -> str:
    return f'python -c "{code}"'


NEW_COPIES = {
    "bash cp": lambda src: bash(f'cp "{src}" review_copy.xlsx', Path(".")),
    "bash cp into the folder": lambda src: bash(f'cp "{src}" ./', Path(".")),
    "PowerShell Copy-Item": lambda src: pwsh(f"Copy-Item -LiteralPath '{src}' -Destination analysis_copy.xlsx",
                                             Path(".")),
    "PowerShell copy": lambda src: pwsh(f"copy '{src}' analysis_copy.xlsx", Path(".")),
    "python shutil.copy": lambda src: bash(_py(f"import shutil; shutil.copy('{src}', 'review_copy.xlsx')"),
                                           Path(".")),
    "python shutil.copy2": lambda src: bash(_py(f"import shutil; shutil.copy2('{src}', 'review_copy.xlsx')"),
                                            Path(".")),
    "xcopy": lambda src: pwsh(f"xcopy '{src}' review_copy.xlsx /Y", Path(".")),
    "robocopy": lambda src: pwsh(f"robocopy '{Path(src).parent}' . {Path(src).name}", Path(".")),
}


def _with_cwd(payload: dict, cwd: Path) -> dict:
    payload["cwd"] = str(cwd)
    payload["transcript_path"] = str(cwd / "no_transcript.jsonl")
    return payload


@hooks_present
@pytest.mark.parametrize("how", list(NEW_COPIES))
def test_the_guard_lets_a_copy_to_a_new_workbook_name_through(model: Path, download: Path, how: str) -> None:
    payload = _with_cwd(NEW_COPIES[how](download.as_posix()), model)
    assert_silent(run_hook("raw_guard", payload))


OVERWRITES = {
    "bash cp": lambda src: bash(f'cp "{src}" model.xlsx', Path(".")),
    "PowerShell Copy-Item": lambda src: pwsh(f"Copy-Item -LiteralPath '{src}' -Destination model.xlsx -Force",
                                             Path(".")),
    "python shutil.copy": lambda src: bash(_py(f"import shutil; shutil.copy('{src}', 'model.xlsx')"), Path(".")),
    "xcopy": lambda src: pwsh(f"xcopy '{src}' model.xlsx /Y", Path(".")),
    "robocopy": lambda src: pwsh(f"robocopy '{Path(src).parent}' . model.xlsx", Path(".")),
    "bash cp with a wildcard": lambda src: bash(f'cp "{Path(src).parent.as_posix()}"/*.xlsx .', Path(".")),
}


@hooks_present
@pytest.mark.parametrize("how", list(OVERWRITES))
def test_the_guard_refuses_a_copy_over_the_existing_workbook(model: Path, download: Path, how: str) -> None:
    if how == "robocopy":
        make_workbook(download.parent / "model.xlsx", 4)
    out = run_hook("raw_guard", _with_cwd(OVERWRITES[how](download.as_posix()), model))
    assert_refused(out, "raw-sheet guard: this command writes", "outside rotm.py")


INTO_RAW = {
    "bash cp": lambda src: bash(f'cp "{src}" raw/export.xlsx', Path(".")),
    "PowerShell Copy-Item": lambda src: pwsh(f"Copy-Item -LiteralPath '{src}' -Destination raw\\export.xlsx",
                                             Path(".")),
    "python shutil.copy": lambda src: bash(_py(f"import shutil; shutil.copy('{src}', 'raw/new.csv')"), Path(".")),
    "xcopy": lambda src: pwsh(f"xcopy '{src}' raw\\ /Y", Path(".")),
    "robocopy": lambda src: pwsh(f"robocopy '{Path(src).parent}' raw export.xlsx", Path(".")),
}


@hooks_present
@pytest.mark.parametrize("how", list(INTO_RAW))
def test_the_guard_refuses_a_copy_into_raw(model: Path, download: Path, how: str) -> None:
    out = run_hook("raw_guard", _with_cwd(INTO_RAW[how](download.as_posix()), model))
    assert_refused(out, "raw-sheet guard: raw/")


@hooks_present
def test_the_guard_lets_the_download_into_a_copy_of_the_model_folder(tmp_path: Path, download: Path) -> None:
    """references/google-sheets.md 'Models kept in Google Sheets' step 3: the copy holds no workbook yet."""
    copy_root = tmp_path / "review_copy"
    modelfolder.init_model(copy_root, name="review_copy")
    assert not (copy_root / "model.xlsx").exists()
    assert_silent(run_hook("raw_guard", bash(f'cp "{download.as_posix()}" model.xlsx', copy_root)))
    make_workbook(copy_root / "model.xlsx", 5)                     # the copy now holds the download
    assert_refused(run_hook("raw_guard", bash(f'cp "{download.as_posix()}" model.xlsx', copy_root)),
                   "this command writes model.xlsx outside rotm.py")


@hooks_present
@pytest.mark.parametrize("command", ["mv export.xlsx moved.xlsx",
                                     _py("import shutil; shutil.move('export.xlsx', 'moved.xlsx')")])
def test_a_move_to_a_new_workbook_name_is_still_refused(model: Path, command: str) -> None:
    make_workbook(model / "export.xlsx", 6)
    assert_refused(run_hook("raw_guard", bash(command, model)), "raw-sheet guard")


# ============================================================================= J-4: long names, contract remove

LONG = "invoice_lines_26"          # 16 characters


def test_propose_notes_a_table_name_longer_than_12_characters(tmp_path: Path) -> None:
    out = tmp_path / "result.json"
    r = run_cli("contract", "propose", str(tmp_path / "m"), "--table", LONG, "--columns", "id,amount:number",
                "--json", str(out))
    assert r.returncode == 0, r.stderr
    note = (f"table name '{LONG}' has 16 characters: it is part of the sheet names raw_{LONG} and h_{LONG} and of "
            "every formula that reads them, and counts toward the 100-character formula length limit; a name of at "
            "most 12 characters keeps lookup formulas short")
    assert note in r.stdout and "N-NAME" in r.stdout
    assert note in json.loads(out.read_text(encoding="utf-8"))["notes"][0]
    assert note not in _md(tmp_path / "m", LONG).read_text(encoding="utf-8")   # the readable contract is unchanged
    r = run_cli("contract", "propose", str(tmp_path / "m"), "--table", "lines_26", "--columns", "id")
    assert r.returncode == 0 and "N-NAME" not in r.stdout
    assert contract.name_note("abcdefghijkl") is None and contract.name_note("abcdefghijklm") is not None


def _table_files(root: Path, table: str) -> list[str]:
    (root / "queries").mkdir(exist_ok=True)
    (root / "raw").mkdir(exist_ok=True)
    for rel in (f"queries/{table}.sql", f"queries/{table}.pq.m", f"queries/{table}.pq.m.bak_20261125_101010",
                f"raw/{table}.csv", f"raw/{table}.controls.json"):
        (root / rel).write_text("x\n", encoding="utf-8")
    return [f"contracts/{table}.contract.json", f"contracts/{table}.contract.md", f"queries/{table}.pq.m",
            f"queries/{table}.pq.m.bak_20261125_101010", f"queries/{table}.sql", f"raw/{table}.controls.json",
            f"raw/{table}.csv"]


def _spec(root: Path, tables: list[str]) -> None:
    sheets = [{"name": f"raw_{t}", "type": "raw", "table": t} for t in tables]
    (root / "model.spec.json").write_text(json.dumps({"rotm_spec": 1, "sheets": sheets}), encoding="utf-8")


def test_remove_moves_the_files_of_the_table_into_removed(tmp_path: Path) -> None:
    root = _proposed(tmp_path, table=LONG)
    expected = _table_files(root, LONG)
    contract.propose(root, "lines", columns="id,amount:number")
    kept = _table_files(root, "lines")
    _spec(root, ["lines"])                                           # the specification names the new table only
    r = run_cli("contract", "remove", str(root), "--table", LONG, "--json", str(tmp_path / "r.json"))
    assert r.returncode == 0, r.stderr
    result = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    folder = root / result["folder"]
    assert folder.parent == root / "removed" and folder.name.startswith(f"{LONG}_")
    assert [m["from"] for m in result["moved"]] == expected
    for rel in expected:
        assert not (root / rel).exists(), rel
        assert (folder / rel).is_file(), rel
        assert rel in r.stdout
    for rel in kept:
        assert (root / rel).is_file(), rel
    assert [s["table"] for s in contract.status_all(root)] == ["lines"]


def test_remove_is_refused_while_the_specification_names_the_table(tmp_path: Path) -> None:
    root = _proposed(tmp_path)
    expected = _table_files(root, "sales")
    _spec(root, ["sales"])
    r = run_cli("contract", "remove", str(root), "--table", "sales")
    assert r.returncode == 2 and "model.spec.json names table 'sales' (sheet raw_sales)" in r.stderr
    assert "nothing was moved" in r.stderr and "Traceback" not in r.stderr
    for rel in expected:
        assert (root / rel).is_file(), rel
    assert not (root / "removed").exists()


def test_remove_of_a_table_with_no_files_is_refused(tmp_path: Path) -> None:
    root = _proposed(tmp_path)
    with pytest.raises(common.Refusal, match="has no files in contracts/, queries/ or raw/"):
        contract.remove(root, "orders")


@hooks_present
def test_contract_remove_passes_the_hooks(model: Path) -> None:
    cmd = f'{ROTM} contract remove "{model.as_posix()}" --table sales'
    for hook in ("raw_guard", "contract_gate"):
        assert_silent(run_hook(hook, bash(cmd, model)))

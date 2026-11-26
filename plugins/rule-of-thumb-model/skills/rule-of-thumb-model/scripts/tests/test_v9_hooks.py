"""Tests of the v9 change of package P6 to the finish check (round 8 verdict, [C-sol-5]).

A passing audit or check counts only while the model folder's inputs are as they were when it was recorded. Each
audit, check and recalc record in reports/status.json, and the session record of the hook's own audit, holds the
field "inputs" (modelfolder.inputs_fingerprint: per contracts/<t>.contract.json, the sha256 of raw/<t>.csv and the
agreement hash when the contract is agreed). After a landing or an agreement of other content, a pass that counts
by the workbook's sha256 is stale: the stop is refused once, and the refusal names the table and the command that
brings the workbook up to date (build, or check --refresh for a table loaded by a query). A record without
"inputs" (written before v9) counts as in v8. One refusal per session end, the notice of a stop that is let
through and the disclosure rule stay as in v8.

Each hook runs as Claude Code runs it (python -I, the payload on stdin), with the helpers of test_hooks.py and its
stand-in for rotm.py. The Excel test runs the real rotm.py build, audit, land and check on a copy of the starter.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from lib import common, modelfolder
from test_hooks import (HOOKS, ROTM, SKILL, assert_silent, bash, build_cycle, fake_rotm, make_workbook,  # noqa: F401
                        model, post, run_hook, status_pass, stop, touch_later)

pytestmark = pytest.mark.skipif(not (HOOKS / "hooks.json").is_file(),
                                reason="PLUGIN/hooks is absent (the skill is used without its plugin)")

HEAD_V8 = "rule-of-thumb-model finish check: model.xlsx changed after its last passing audit or recalculation."
HEAD_STALE = ("rule-of-thumb-model finish check: model.xlsx: the raw data or contracts of its model folder changed "
              "after its last passing audit or check.")
NOTICE = "rule-of-thumb-model finish check: not verified at the end of the session: model.xlsx: "
TAIL = ("If Excel is not available, say in your report that the workbook was not recalculated. If a finding cannot "
        "be fixed, say which one and why in your report.")
SCRIPT = SKILL / "scripts" / "rotm.py"
QUOTED = re.compile(r'"([^"]+)"')


def _same(a: str | Path, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


def _canon(text: str, *paths: Path) -> str:
    """The text with each quoted path that names one of `paths` (or rotm.py) written as that path's as_posix():
    the hook may spell a folder in another case or with other separators."""
    def sub(m: re.Match) -> str:
        for p in (*paths, SCRIPT):
            if _same(m.group(1), p):
                return f'"{p.as_posix()}"'
        return m.group(0)
    return QUOTED.sub(sub, text)


def _record(state: Path, sid: str) -> dict:
    return json.loads((state / "sessions" / f"{sid}.json").read_text(encoding="utf-8"))


def _decision(out) -> dict:
    assert out.code == 0 and out.stderr == "" and "Traceback" not in out.stdout, out
    assert out.stdout.strip(), "the hook gave no output: the stop was let through silently"
    return out.json()


def _raw_line(table: str) -> str:
    return f"{table}: raw/{table}.csv changed after the last passing audit or check of this version (a landing)"


def _contract_line(table: str) -> str:
    return (f"{table}: contract '{table}' was agreed again, or is no longer agreed, after the last passing audit or "
            "check of this version")


def _rebuild(root: Path, rotm: str, extra: str = "") -> str:
    return f'{rotm} build "{root.as_posix()}"{extra} --force, then audit --strict and check it'


def _land_raw(root: Path, table: str, text: str) -> None:
    """A landing as the finish check sees it: raw/<t>.csv holds other bytes (no workbook is written)."""
    (root / "raw" / f"{table}.csv").write_text(text, encoding="utf-8")


def _agree_again(root: Path, table: str, total: float) -> None:
    """The contract agreed again with another control total (what contract propose --force and contract agree
    leave): new content, agreement.contract_sha256 the hash of that content."""
    path = modelfolder.contract_path(root, table)
    c = json.loads(path.read_text(encoding="utf-8"))
    c.setdefault("expect", {})["totals"] = [{"column": "amount", "value": total, "tolerance": 0}]
    c["agreement"] = dict(c["agreement"], contract_sha256=modelfolder.contract_hash(c), at=common.now_iso())
    common.write_json(path, c)
    assert modelfolder.contract_state(root, table) == "agreed"


# ============================================================================= modelfolder: the fingerprint

def test_inputs_fingerprint_holds_raw_and_agreement_hashes(model: Path) -> None:
    fp = modelfolder.inputs_fingerprint(model)
    assert set(fp) == {"sales", "target"}                          # one entry per contracts/<t>.contract.json
    assert fp["sales"] == {"raw_sha256": common.file_sha256(model / "raw" / "sales.csv"), "contract_sha256": None}
    target = json.loads(modelfolder.contract_path(model, "target").read_text(encoding="utf-8"))
    assert fp["target"] == {"raw_sha256": None, "contract_sha256": target["agreement"]["contract_sha256"]}
    # an edit after the agreement (state stale) counts as not agreed
    target["grain"] = "one row per target line"
    common.write_json(modelfolder.contract_path(model, "target"), target)
    assert modelfolder.inputs_fingerprint(model)["target"]["contract_sha256"] is None
    assert modelfolder.inputs_changes(fp, modelfolder.inputs_fingerprint(model)) == [("target", "contract")]
    assert modelfolder.inputs_changes(None, fp) == [] and modelfolder.inputs_changes(fp, fp) == []


def test_record_step_adds_inputs_to_audit_check_and_recalc_records(model: Path) -> None:
    fp = modelfolder.inputs_fingerprint(model)
    for step in ("audit", "check", "recalc", "build", "land", "render"):
        modelfolder.record_step(model, "model.xlsx", step, {"exit_code": 0})
    modelfolder.record_step(model, "model.xlsx", "theme", {"exit_code": 0, "inputs": {"kept": 1}})
    steps = modelfolder.read_status(model)["workbooks"]["model.xlsx"]
    for step in ("audit", "check", "recalc"):
        assert steps[step]["inputs"] == fp, step
    for step in ("build", "land", "render"):
        assert "inputs" not in steps[step], step
    assert steps["theme"]["inputs"] == {"kept": 1}                 # another step: the entry is kept as given
    modelfolder.record_step(model, "model.xlsx", "audit", {"exit_code": 0, "inputs": {"given": None}})
    assert modelfolder.read_status(model)["workbooks"]["model.xlsx"]["audit"]["inputs"] == {"given": None}


# ============================================================================= the three tests of the entry

@pytest.mark.parametrize("how", ["agreed_again", "no_longer_agreed"])
def test_contract_agreed_again_after_the_pass_makes_it_stale(how: str, model: Path, fake_rotm: tuple[dict, Path],
                                                             _isolated_state: Path) -> None:
    """Status records written with record_step; the contract 'target' is then agreed again with another total (or
    edited, so that it is no longer agreed): one refusal naming the contract and the rebuild, then the notice; after
    the rebuild with its audit and check the stop passes."""
    env, _log = fake_rotm
    sid = f"sess-v9-agree-{how}"
    build_cycle(model, env, sid=sid)                          # the hook's own strict audit is clean
    status_pass(model)                                        # audit and check pass (record_step adds inputs)
    assert_silent(run_hook("finish_check", stop(model, sid=sid)))
    if how == "agreed_again":
        _agree_again(model, "target", 12345)
    else:
        path = modelfolder.contract_path(model, "target")
        c = json.loads(path.read_text(encoding="utf-8"))
        c["grain"] = "one row per target line"
        common.write_json(path, c)
        assert modelfolder.contract_state(model, "target") == "stale"
    first = _decision(run_hook("finish_check", stop(model, sid=sid)))
    assert first["decision"] == "block", first
    lines = _canon(first["reason"], model).splitlines()
    assert lines == [HEAD_STALE, f"- model.xlsx: {_contract_line('target')}. Run {_rebuild(model, ROTM)}.",
                     TAIL], lines
    for active in (True, False):                              # let through with the notice (plan F16 c)
        through = _decision(run_hook("finish_check", stop(model, sid=sid, active=active)))
        assert set(through) == {"systemMessage"}, through
        assert _canon(through["systemMessage"], model) == (NOTICE + _contract_line("target")
                                                           + f" ({_rebuild(model, 'rotm.py')}).")
    assert _record(_isolated_state, sid)["finish_refusals"] == 1
    # the rebuild, its audit and check: the stop passes
    build_cycle(model, env, sid=sid)
    status_pass(model)
    assert_silent(run_hook("finish_check", stop(model, sid=sid)))
    assert _record(_isolated_state, sid)["finish_refusals"] == 1


def test_session_without_a_landing_after_the_pass_behaves_as_in_v8(model: Path, fake_rotm: tuple[dict, Path],
                                                                   _isolated_state: Path) -> None:
    """No landing and no agreement after the pass: the v8 results, text for text. A status record without
    "inputs" (written before v9) counts as in v8, also after a landing."""
    env, _log = fake_rotm
    wb = model / "model.xlsx"
    # (1) audit and check pass: silent, also on later stops
    build_cycle(model, env, sid="sess-v9-v8a")
    status_pass(model)
    for last in ("Done.", None):
        assert_silent(run_hook("finish_check", stop(model, sid="sess-v9-v8a", last=last)))
    # (2) the hook's own clean audit and no check: the v8 refusal once, then the v8 notice
    build_cycle(model, env, sid="sess-v9-v8b")
    first = _decision(run_hook("finish_check", stop(model, sid="sess-v9-v8b")))
    assert set(first) == {"decision", "reason"} and first["decision"] == "block", first
    assert _canon(first["reason"], wb) == "\n".join([
        HEAD_V8, f'- model.xlsx: not recalculated: no passing check of this version. Run {ROTM} check '
                 f'"{wb.as_posix()}".', TAIL]), first
    through = _decision(run_hook("finish_check", stop(model, sid="sess-v9-v8b", active=True)))
    assert set(through) == {"systemMessage"}, through
    assert _canon(through["systemMessage"], wb) == (NOTICE + f'no passing recalculation (rotm.py check '
                                                    f'"{wb.as_posix()}"), and the last message does not say '
                                                    "'not recalculated'."), through
    assert _record(_isolated_state, "sess-v9-v8b")["finish_refusals"] == 1
    # (3) the words "not recalculated" excuse the recalculation, and the disclosure is remembered (v8)
    build_cycle(model, env, sid="sess-v9-v8c")
    assert_silent(run_hook("finish_check", stop(model, sid="sess-v9-v8c", last="model.xlsx was not recalculated.")))
    assert_silent(run_hook("finish_check", stop(model, sid="sess-v9-v8c")))
    assert _record(_isolated_state, "sess-v9-v8c")["disclosed"]
    # (4) records written before v9 (no "inputs"): they count as in v8, also after raw/ changed
    build_cycle(model, env, sid="sess-v9-v8d")
    sha = common.file_sha256(wb)
    status = modelfolder.read_status(model)
    status["workbooks"]["model.xlsx"].update(
        audit={"exit_code": 0, "strict": True, "errors": 0, "warnings": 0, "sha256": sha, "at": common.now_iso()},
        check={"exit_code": 0, "recalculated": True, "checks": 6, "failed": 0, "master": 0, "input_sha256": sha,
               "output_sha256": sha, "at": common.now_iso()})
    common.write_json(modelfolder.status_path(model), status)
    _land_raw(model, "sales", "id,amount\nA1,10\nA2,20\n")
    assert_silent(run_hook("finish_check", stop(model, sid="sess-v9-v8d")))


@pytest.mark.excel
def test_landing_after_the_pass_is_refused_once_naming_the_table_and_the_rebuild(tmp_path: Path,
                                                                                 _isolated_state: Path) -> None:
    """The reviewer's case: a starter copy in one simulated hook session. build, audit --strict and check pass;
    land --force --control of a changed extract; the stop with 'Done.' is refused once, naming lines and the
    rebuild; after the rebuild, its audit and its check, the stop passes."""
    src = SKILL / "templates" / "starter"
    if not (src / "model.spec.json").is_file():
        pytest.skip("the starter template is absent")
    root = tmp_path / "starter"
    shutil.copytree(src, root, ignore=shutil.ignore_patterns("render"))
    env = {"ROTM_EXAMPLE_ROOTS": str(tmp_path)}             # the shipped 'example' agreement holds here
    sid = "sess-v9-land"
    wb = root / "model.xlsx"

    def step(*args: str) -> tuple[subprocess.CompletedProcess, object]:
        shown = " ".join(f'"{a}"' if ("/" in a or "\\" in a) else a for a in args)
        payload = bash(f"{ROTM} {shown}", root, sid=sid)
        for hook in ("contract_gate", "raw_guard"):
            out = run_hook(hook, payload, env=env)
            assert out.code == 0 and "Traceback" not in out.stderr, (hook, out)
        proc = subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPT), *args], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=900, cwd=str(root),
                              env=dict(os.environ, **env))
        return proc, run_hook("audit_on_write", post(payload), env=env)

    def passing_cycle() -> None:
        proc, hook = step("build", root.as_posix(), "--force")
        assert proc.returncode == 0, ("build", proc.stdout[-3000:], proc.stderr[-3000:])
        ctx = hook.json()
        assert "decision" not in ctx and ": 0 errors, 0 warnings" in ctx["hookSpecificOutput"]["additionalContext"], ctx
        proc, _hook = step("audit", wb.as_posix(), "--strict")
        assert proc.returncode == 0, ("audit", proc.stdout[-3000:], proc.stderr[-3000:])
        proc, hook = step("check", root.as_posix())
        assert proc.returncode == 0, ("check", proc.stdout[-3000:], proc.stderr[-3000:])
        rec = modelfolder.read_status(root)["workbooks"]["model.xlsx"]["check"]
        assert rec["recalculated"] is True and rec["output_sha256"] == common.file_sha256(wb), rec

    passing_cycle()
    assert_silent(run_hook("finish_check", stop(root, sid=sid)))         # the pass counts before the landing
    checked = common.file_sha256(wb)
    # a changed extract with its new source total, landed as the documents say
    rows = list(csv.reader(io.StringIO((root / "raw" / "lines.csv").read_text(encoding="utf-8"))))
    amount = rows[0].index("amount")
    rows[1][amount] = str(int(float(rows[1][amount])) + 1000)
    total = sum(int(float(r[amount])) for r in rows[1:])
    extract = tmp_path / "lines_new.csv"
    with extract.open("w", encoding="utf-8", newline="") as f:
        csv.writer(f, lineterminator="\n").writerows(rows)
    proc, hook = step("land", root.as_posix(), "--table", "lines", "--csv", extract.as_posix(), "--force",
                      "--control", f"lines.amount={total}")
    assert proc.returncode == 0, ("land", proc.stdout[-3000:], proc.stderr[-3000:])
    assert_silent(hook)                                                   # land wrote no workbook
    assert common.file_sha256(wb) == checked                              # the workbook is as it was checked
    first = _decision(run_hook("finish_check", stop(root, sid=sid)))
    assert first["decision"] == "block", first
    lines = _canon(first["reason"], root).splitlines()
    assert lines == [HEAD_STALE, f"- model.xlsx: {_raw_line('lines')}. Run {_rebuild(root, ROTM)}.", TAIL], lines
    for active in (True, False):                                          # one refusal per session end
        through = _decision(run_hook("finish_check", stop(root, sid=sid, active=active)))
        assert set(through) == {"systemMessage"}, through
        assert _canon(through["systemMessage"], root) == (NOTICE + _raw_line("lines")
                                                          + f" ({_rebuild(root, 'rotm.py')})."), through
    assert _record(_isolated_state, sid)["finish_refusals"] == 1
    # the command the refusal names, then its audit and check: the stop passes
    passing_cycle()
    assert_silent(run_hook("finish_check", stop(root, sid=sid)))
    assert _record(_isolated_state, sid)["finish_refusals"] == 1
    steps = modelfolder.read_status(root)["workbooks"]["model.xlsx"]
    assert steps["audit"]["inputs"] == steps["check"]["inputs"] == modelfolder.inputs_fingerprint(root), steps


# ============================================================================= the other cases of the rule

def test_accept_refresh_leaves_a_pass_that_counts(model: Path, fake_rotm: tuple[dict, Path],
                                                  _isolated_state: Path) -> None:
    """check --accept-refresh rewrites raw/<t>.csv before it records its step (lib/check.py): its record holds the
    new fingerprint, so the recalculation counts, and the hook's audit of the saved file counts too."""
    env, _log = fake_rotm
    sid = "sess-v9-accept"
    build_cycle(model, env, sid=sid)
    status_pass(model, check=False)
    audited = common.file_sha256(model / "model.xlsx")
    payload = bash(f'{ROTM} check "{model.as_posix()}" --accept-refresh --control sales.amount=30', model, sid=sid)
    assert_silent(run_hook("raw_guard", payload, env=env))
    touch_later(model / "model.xlsx", 777)                    # the refresh loaded new rows; Excel saved the file
    _land_raw(model, "sales", "id,amount\nA1,10\nA2,20\n")    # the accepted snapshot
    saved = common.file_sha256(model / "model.xlsx")
    modelfolder.record_step(model, "model.xlsx", "check", {"exit_code": 0, "recalculated": True, "refreshed": True,
                                                           "checks": 6, "failed": 0, "master": 0,
                                                           "input_sha256": audited, "output_sha256": saved})
    rec = modelfolder.read_status(model)["workbooks"]["model.xlsx"]["check"]
    assert rec["inputs"] == modelfolder.inputs_fingerprint(model)
    out = run_hook("audit_on_write", post(payload), env=env)
    assert out.json()["hookSpecificOutput"]["additionalContext"].endswith("0 warnings (recalculated)"), out
    (own,) = _record(_isolated_state, sid)["audits"].values()
    assert own["inputs"] == modelfolder.inputs_fingerprint(model) and own["reused"] is False
    assert_silent(run_hook("finish_check", stop(model, sid=sid)))


def test_landing_of_a_query_loaded_table_names_the_refresh(model: Path, fake_rotm: tuple[dict, Path]) -> None:
    """A table the manifest records as loaded by a query (landing power_query): a new raw/<t>.csv is brought into the
    workbook by check --refresh; a new agreement still needs the rebuild."""
    env, _log = fake_rotm
    sid = "sess-v9-query"
    wb = model / "model.xlsx"
    common.write_json(model / "model.manifest.json", {"spec": "model.spec.json", "sheets": [
        {"name": "raw_sales", "type": "raw", "table": "sales", "landing": "power_query"},
        {"name": "raw_target", "type": "raw", "table": "target", "landing": "static"}]})
    build_cycle(model, env, sid=sid)
    status_pass(model)
    _land_raw(model, "sales", "id,amount\nA1,10\nA3,30\n")
    reason = _canon(_decision(run_hook("finish_check", stop(model, sid=sid)))["reason"], wb, model)
    assert reason.splitlines() == [HEAD_STALE, f"- model.xlsx: {_raw_line('sales')}. Run {ROTM} check "
                                   f'"{wb.as_posix()}" --refresh, then audit --strict.', TAIL], reason
    msg = _canon(_decision(run_hook("finish_check", stop(model, sid=sid, active=True)))["systemMessage"], wb)
    assert msg == NOTICE + _raw_line("sales") + f' (rotm.py check "{wb.as_posix()}" --refresh, then audit --strict).'
    # with a new agreement as well, the one command is the rebuild
    build_cycle(model, env, sid=sid)
    status_pass(model)
    _land_raw(model, "sales", "id,amount\nA1,11\n")
    _agree_again(model, "target", 99)
    lines = _canon(_decision(run_hook("finish_check", stop(model, sid=sid)))["reason"], model).splitlines()
    assert lines[1:3] == [f"- model.xlsx: {_raw_line('sales')}. Run {_rebuild(model, ROTM)}.",
                          f"- model.xlsx: {_contract_line('target')}. Run {_rebuild(model, ROTM)}."], lines


def test_stale_pass_of_a_second_workbook_names_its_output(model: Path, fake_rotm: tuple[dict, Path]) -> None:
    """A workbook built with -o: the rebuild names it with -o, so that build writes that file again."""
    env, _log = fake_rotm
    sid = "sess-v9-draft"
    payload = bash(f"{ROTM} build model.spec.json -o draft.xlsx", model, sid=sid)
    assert_silent(run_hook("raw_guard", payload, env=env))
    draft = model / "draft.xlsx"
    make_workbook(draft, 5)
    run_hook("audit_on_write", post(payload), env=env)        # its own strict audit is clean
    sha = common.file_sha256(draft)
    modelfolder.record_step(model, "draft.xlsx", "check", {"exit_code": 0, "recalculated": True, "failed": 0,
                                                           "master": 0, "input_sha256": sha, "output_sha256": sha})
    assert_silent(run_hook("finish_check", stop(model, sid=sid)))
    _land_raw(model, "sales", "id,amount\nA9,90\n")
    lines = _canon(_decision(run_hook("finish_check", stop(model, sid=sid)))["reason"], model, draft).splitlines()
    assert lines[0] == HEAD_STALE.replace("model.xlsx", "draft.xlsx"), lines
    out_flag = f' -o "{draft.as_posix()}"'
    assert lines[1] == f"- draft.xlsx: {_raw_line('sales')}. Run {_rebuild(model, ROTM, out_flag)}.", lines


def test_audit_on_write_does_not_reuse_an_audit_older_than_a_landing(model: Path, fake_rotm: tuple[dict, Path],
                                                                    _isolated_state: Path) -> None:
    """One command builds, audits and then lands: the audit it recorded is older than the landing, so the hook runs
    the audit again, and the session record holds the inputs of that audit."""
    env, log = fake_rotm
    sid = "sess-v9-reuse"
    payload = bash(f"{ROTM} build . --force && {ROTM} audit model.xlsx --strict && {ROTM} land . --table sales "
                   "--csv new.csv --force", model, sid=sid)
    assert_silent(run_hook("raw_guard", payload, env=env))
    touch_later(model / "model.xlsx", 31)
    built = common.file_sha256(model / "model.xlsx")
    modelfolder.record_step(model, "model.xlsx", "audit", {"exit_code": 0, "strict": True, "errors": 0,
                                                           "warnings": 0, "sha256": built})
    _land_raw(model, "sales", "id,amount\nB1,1\n")
    text = run_hook("audit_on_write", post(payload), env=env).json()["hookSpecificOutput"]["additionalContext"]
    assert "from the strict audit this command ran" not in text and log.is_file(), text
    (own,) = _record(_isolated_state, sid)["audits"].values()
    assert own["reused"] is False and own["inputs"] == modelfolder.inputs_fingerprint(model)
    # the hook's own audit is of the landed inputs: with a check of them the stop passes
    status_pass(model, audit=False)
    assert_silent(run_hook("finish_check", stop(model, sid=sid)))

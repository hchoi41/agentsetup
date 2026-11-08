"""Tests for the tikitaka engine — charter §10 required tests + criteria v3 §3/§4 guards."""
import json
import os
import sys
import time
from pathlib import Path

import pytest
from conftest import FAKE, role

from tikitaka import access, gates, preflight
from tikitaka.config import ConfigError, validate_job
from tikitaka.engine import Engine, Escalation
from tikitaka.seats import run_seat


def engine(job_raw, tmp_path, **kw):
    job = validate_job(job_raw, config_source=kw.pop("config_source", "agent"))
    return Engine(job, tmp_path / "run", check_tools=False, **kw)


# ---------- config (§2) ----------
def test_missing_mandatory_role_rejected(job_raw):
    del job_raw["roles"]["qa_qc"]
    with pytest.raises(ConfigError, match="mandatory"):
        validate_job(job_raw)


def test_duplicate_seat_alias_rejected(job_raw):
    job_raw["roles"]["cold_reviewer"]["seat"] = "acc"
    with pytest.raises(ConfigError, match="duplicate seat"):
        validate_job(job_raw)


def test_new_role_needs_stage(job_raw):
    job_raw["roles"]["linter"] = role("lint")
    with pytest.raises(ConfigError, match="stage"):
        validate_job(job_raw)


def test_double_check_from_agent_config_ignored(job_raw):
    job_raw["double_check"] = {"enabled": True, "duration": {"type": "iterations", "n": 2}}
    job = validate_job(job_raw, config_source="agent")
    assert job.double_check["enabled"] is False and job.double_check_source.startswith("ignored")


def test_job_config_double_check_only_iterations(job_raw):
    job_raw["double_check"] = {"enabled": True, "duration": {"type": "session"}}
    with pytest.raises(ConfigError, match="only 'iterations'"):
        validate_job(job_raw, config_source="user")


# ---------- gates (§7) ----------
def _rev(role="accuracy_reviewer", seat="acc", ids=("A-acc-1",), fixable=True):
    return {"role": role, "seat": seat, "model": "m", "artifact_version": "v1", "verdict": "revise",
            "issues": [{"id": i, "location": "l", "problem": "p", "evidence": "e", "fix": "f", "severity": "major",
                        "fixable_in_loop": fixable} for i in ids]}


def _ver(items, improvements, nec, judge_items=()):
    return {"artifact_version": "v1", "plan_assessment": "", "process_assessment": "", "output_assessment": "",
            "items": items, "judge_items": list(judge_items), "improvements": improvements, "improvement_necessary": nec}


def test_review_id_prefix_enforced():
    with pytest.raises(gates.GateError, match="must start with"):
        gates.validate_review(_rev(ids=("X-1",)), role="accuracy_reviewer", seat="acc")


def test_invalid_output_rejected():
    for bad in ("", "no json here", "```json\n{broken\n```"):
        with pytest.raises(gates.GateError):
            gates.extract_json(bad)


def test_verdict_consistency_and_coverage():
    revs = [_rev()]
    ok = gates.validate_verdict(_ver([{"id": "A-acc-1", "decision": "improve", "reason": "r"}], ["[A-acc-1] do"], "yes"), revs)
    assert ok["_driving"] == ["A-acc-1"]
    cases = [
        (_ver([{"id": "A-acc-1", "decision": "improve", "reason": "r"}], ["[A-acc-1] do"], "no"), "inconsistent"),
        (_ver([{"id": "A-acc-1", "decision": "improve", "reason": "r"}], ["do it"], "yes"), "without item id"),
        (_ver([{"id": "A-acc-1", "decision": "keep", "reason": "r"}], ["[A-acc-1] do"], "no"), "non-driving"),
        (_ver([{"id": "A-acc-1", "decision": "improve", "reason": "r"}], [], "yes"), "missing from improvements"),
        (_ver([], [], "no"), "without a decision"),
    ]
    for v, msg in cases:
        with pytest.raises(gates.GateError, match=msg):
            gates.validate_verdict(v, [_rev()])


def test_improve_on_non_fixable_fails_unless_overridden():
    revs = [_rev(fixable=False)]
    with pytest.raises(gates.GateError, match="non-fixable"):
        gates.validate_verdict(_ver([{"id": "A-acc-1", "decision": "improve", "reason": "r"}], ["[A-acc-1] x"], "yes"), revs)
    v = gates.validate_verdict(_ver([{"id": "A-acc-1", "decision": "improve", "reason": "fixable after all",
                                      "fixable_override": True}], ["[A-acc-1] x"], "yes"), [_rev(fixable=False)])
    assert v["improvement_necessary"] == "yes"


def test_duplicate_issue_ids_rejected():
    with pytest.raises(gates.GateError, match="duplicate"):
        gates.check_unique_ids([_rev(), _rev()])


def test_agreement_needs_orchestrator_proposal():
    rec = {"type": "finish", "proposal": {}, "role": "judge", "model": "m", "agree": True, "reasons": "r"}
    with pytest.raises(gates.GateError, match="proposal"):
        gates.validate_agreement(rec, decision_type="finish")


def test_qa_pass_needs_verbatim_quote():
    d = {"role": "qa_qc", "results": [{"id": "Q1", "result": "pass", "evidence": "not in the text"}]}
    with pytest.raises(gates.GateError, match="verbatim"):
        gates.validate_qa(d, functions=["f"], artifact_text="hello world")


# ---------- access (§5) — tests 2, 3, 4 ----------
def test_worker_env_scrubbed_and_override_per_role():
    base = {"PATH": "x", "GH_TOKEN": "s", "GOOGLE_APPLICATION_CREDENTIALS": "c", "ANTHROPIC_API_KEY": "k"}
    env = access.worker_env("builder", [], base)
    assert env == {"PATH": "x"}
    ov = [{"roles": ["builder"], "access": ["GOOGLE_APPLICATION_CREDENTIALS"]}]
    assert "GOOGLE_APPLICATION_CREDENTIALS" in access.worker_env("builder", ov, base)
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in access.worker_env("judge", ov, base)


def _write(root, rel, d):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d), encoding="utf-8")


def test_override_not_inherited_by_new_project(settings_root):
    _write(settings_root, "projects/p1.json", {"overrides": [{"roles": ["builder"], "access": ["X"]}]})
    assert access.active_overrides(project="p1", session_id=None, hub=None, cli_overrides=[])
    assert access.active_overrides(project="p2", session_id=None, hub=None, cli_overrides=[]) == []


def test_override_not_inherited_by_new_session(settings_root):
    _write(settings_root, "sessions/s1.json", {"overrides": [{"roles": ["builder"], "access": ["X"]}]})
    assert access.active_overrides(project=None, session_id="s1", hub=None, cli_overrides=[])
    assert access.active_overrides(project=None, session_id="s2", hub=None, cli_overrides=[]) == []


def test_project_complete_clears_overrides(settings_root):
    _write(settings_root, "projects/p1.json", {"overrides": [{"roles": ["builder"], "access": ["X"]}],
                                               "double_check": {"enabled": True}})
    access.clear_project_overrides("p1")
    assert access.active_overrides(project="p1", session_id=None, hub=None, cli_overrides=[]) == []
    assert "double_check" not in json.loads((settings_root / "projects/p1.json").read_text())


def test_completed_project_in_index_grants_nothing(settings_root, tmp_path):
    hub = tmp_path / "hub"
    (hub / "00.ABOUT").mkdir(parents=True)
    (hub / "00.ABOUT/PROJECT_REGISTRY.json").write_text(json.dumps({"projects": [{"canonical_slug": "p1", "display_name": "P One"}]}))
    (hub / "00.ABOUT/PROJECT_INDEX.md").write_text("| project_name | status |\n| --- | --- |\n| P One | completed |\n")
    _write(settings_root, "projects/p1.json", {"overrides": [{"roles": ["builder"], "access": ["X"]}]})
    assert access.active_overrides(project="p1", session_id=None, hub=hub, cli_overrides=[]) == []


def test_override_in_job_config_rejected(job_raw, tmp_path, scenario):
    scenario()
    job_raw["overrides"] = [{"roles": ["builder"], "access": ["GH_TOKEN"]}]
    e = engine(job_raw, tmp_path)
    e.run()
    ov = json.loads((tmp_path / "run/overrides.json").read_text())
    assert ov["accepted"] == [] and ov["rejected"][0]["source"] == "job config"


# ---------- double-check durations (§3.6) — test 1 ----------
def test_double_check_iterations_window(job_raw):
    job_raw["double_check"] = {"enabled": True, "duration": {"type": "iterations", "n": 2}}
    job = validate_job(job_raw, config_source="user")
    act = [access.double_check_state(job, session_id=None, run_round=r)["active"] for r in (1, 2, 3)]
    assert act == [True, True, False]


def test_double_check_session_scope(job_raw, settings_root):
    _write(settings_root, "sessions/s1.json", {"double_check": {"enabled": True}})
    job = validate_job(job_raw)
    assert access.double_check_state(job, session_id="s1", run_round=5)["active"]
    assert not access.double_check_state(job, session_id="s2", run_round=1)["active"]


def test_double_check_project_scope(job_raw, settings_root):
    _write(settings_root, "projects/p1.json", {"double_check": {"enabled": True}})
    job_raw["project"] = "p1"
    job = validate_job(job_raw)
    assert access.double_check_state(job, session_id="s1", run_round=1)["active"]
    assert access.double_check_state(job, session_id="s9", run_round=1)["active"]      # survives a new session
    job_raw["project"] = "p2"
    assert not access.double_check_state(validate_job(job_raw), session_id="s1", run_round=1)["active"]


def test_double_check_active_without_pair_escalates(job_raw, tmp_path, scenario, settings_root):
    scenario()
    _write(settings_root, "sessions/s1.json", {"double_check": {"enabled": True}})
    e = engine(job_raw, tmp_path, session_id="s1")
    assert e.run() == "escalated"
    assert "second reviewer pair" in (tmp_path / "run/ESCALATION.md").read_text()


def test_double_check_runs_four_reviewers(job_raw, tmp_path, scenario, settings_root):
    scenario()
    job_raw["roles"]["accuracy_reviewer_2"] = role("acc2")
    job_raw["roles"]["cold_reviewer_2"] = role("cold2")
    _write(settings_root, "sessions/s1.json", {"double_check": {"enabled": True}})
    assert engine(job_raw, tmp_path, session_id="s1").run() == "finished"
    assert len(list((tmp_path / "run/reviews").glob("m1_r1_*.json"))) == 4


# ---------- preflight / tool fixes (criteria §4) ----------
def test_argv_limit():
    with pytest.raises(preflight.PreflightError, match="stdin"):
        preflight.check_argv(["x", "y" * 9000])


def test_codex_sandbox_explicit_and_safe():
    args = preflight.codex_sandbox_args("accuracy_reviewer", None, platform="win32")
    assert args[:2] == ["-s", "read-only"] and 'windows.sandbox="elevated"' in args
    with pytest.raises(preflight.PreflightError):
        preflight.codex_sandbox_args("accuracy_reviewer", "danger-full-access", platform="win32")
    with pytest.raises(preflight.PreflightError, match="workspace-write"):
        preflight.codex_sandbox_args("builder", "workspace-write", platform="win32")


def test_run_lock_is_exclusive(tmp_path):
    with preflight.RunLock(tmp_path / "out"):
        with pytest.raises(preflight.PreflightError, match="another runner"):
            with preflight.RunLock(tmp_path / "out"):
                pass


def test_watcher_with_true_stop_condition_refused():
    with pytest.raises(preflight.PreflightError, match="already true"):
        preflight.start_watcher(lambda: True)
    assert preflight.start_watcher(lambda: False)


def test_missing_application_refused():
    with pytest.raises(preflight.PreflightError, match="not found"):
        preflight.resolve_application("definitely-not-a-cli-xyz")


def test_expired_login_detected():
    with pytest.raises(preflight.PreflightError, match="login expired"):
        preflight.run_logins(["gcloud"], {"gcloud": [sys.executable, "-c", "import sys; sys.exit(1)"]})
    assert preflight.run_logins(["ok"], {"ok": [sys.executable, "-c", "pass"]})[0]["ok"]


def test_claude_seat_gets_bg_wait_env_after_scrub(tmp_path, monkeypatch, scenario):
    dump = tmp_path / "env.txt"
    scenario(env_dump=str(dump))
    from tikitaka import seats
    from tikitaka.config import Role
    monkeypatch.setattr(seats, "build_argv", lambda r, out: [sys.executable, "-I", str(FAKE)])
    env = access.worker_env("cold_reviewer", [], {**os.environ, "ANTHROPIC_API_KEY": "k"})
    run_seat(Role("cold_reviewer", "dec", "claude", "m", "high"), "You are the X", out_prefix=tmp_path / "c", env=env)
    names = json.loads(dump.read_text().splitlines()[0])
    assert "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS" in names and "ANTHROPIC_API_KEY" not in names


# ---------- engine end-to-end (tests 5, 6, 7, 8) ----------
def test_full_run_finishes_with_records(job_raw, tmp_path, scenario):
    scenario(review_rounds_with_issue=1)
    e = engine(job_raw, tmp_path)
    assert e.run() == "finished"
    run = tmp_path / "run"
    for f in ("plan.md", "process.md", "tokens.json", "qa.json", "AUDIT.md", "overrides.json", "double_check.json",
              "agreements/plan_approval_seq1.json", "agreements/milestone_m1_seq1.json", "agreements/finish_seq1.json",
              "verdicts/m1_r1_judge.json", "verdicts/m1_r2_judge.json", "artifacts/v2.changes.md"):
        assert (run / f).exists(), f
    assert e.v == 2 and not e.stopped_by_cap
    tokens = json.loads((run / "tokens.json").read_text())
    assert {t["role"] for t in tokens} >= {"builder", "accuracy_reviewer", "cold_reviewer", "judge", "qa_qc", "auditor"}


def test_stopped_by_cap(job_raw, tmp_path, scenario):
    scenario(review_rounds_with_issue=99)
    job_raw["max_iterations"] = 2
    e = engine(job_raw, tmp_path)
    assert e.run() == "finished" and e.stopped_by_cap and e.v == 2
    assert "stopped by cap" in (tmp_path / "run/process.md").read_text()


def test_judge_disagreement_blocks_plan(job_raw, tmp_path, scenario):
    scenario(disagree=["plan_approval"])
    e = engine(job_raw, tmp_path)
    assert e.run() == "escalated" and e.v == 0
    assert json.loads((tmp_path / "run/agreements/plan_approval_seq1.json").read_text())["agree"] is False


def test_skipping_a_gate_is_refused(job_raw, tmp_path, scenario):
    scenario()
    e = engine(job_raw, tmp_path)
    e.overrides = []
    with pytest.raises(Escalation):
        e.require_gate("finish")


def test_invalid_reviews_reinvoked_then_never_reach_judge(job_raw, tmp_path, scenario):
    scenario(bad_review=True)
    e = engine(job_raw, tmp_path)
    assert e.run() == "escalated"
    proc = (tmp_path / "run/process.md").read_text()
    assert proc.count("gate REJECT review accuracy_reviewer") == 3
    assert not list((tmp_path / "run/verdicts").glob("*.json"))


def test_seat_replacement_after_retries(job_raw, tmp_path, scenario):
    scenario(bad_review=True)
    good = tmp_path / "good.json"
    good.write_text(json.dumps({}))
    # replacement seat uses a scenario without bad_review via its own env? simpler: replacement is a valid fake with no scenario
    job_raw["roles"]["accuracy_reviewer"]["replacement"] = {"seat": "acc_b", "cli": "fake", "script": str(FAKE)}
    e = engine(job_raw, tmp_path)
    e.run()
    assert any(r["to"] == "acc_b" for r in e.replacements)


def test_qa_fail_bounded_fix_and_one_rerun(job_raw, tmp_path, scenario):
    scenario(review_rounds_with_issue=0, qa_fail_first=True)
    e = engine(job_raw, tmp_path)
    assert e.run() == "finished"
    run = tmp_path / "run"
    assert (run / "agreements/qa_failure_handling_seq1.json").exists()
    assert (run / "verdicts/qafix_judge.json").exists() and (run / "qa_rerun.json").exists()


def test_user_cutoff_logged_and_incomplete(job_raw, tmp_path, scenario):
    scenario(sleep=3)
    job_raw["roles"]["builder"]["cutoff_seconds"] = 1
    e = engine(job_raw, tmp_path)
    assert e.run() == "escalated"
    cut = json.loads((tmp_path / "run/cutoffs.json").read_text())
    assert cut[0]["trigger"] == "user request"


def test_no_cutoff_by_default(job_raw, tmp_path, scenario):
    scenario(sleep=1, review_rounds_with_issue=0)
    assert engine(job_raw, tmp_path).run() == "finished"
    assert not (tmp_path / "run/cutoffs.json").exists()


def test_extension_role_dispatched_by_stage(job_raw, tmp_path, scenario):
    scenario(review_rounds_with_issue=0)
    job_raw["roles"]["linter"] = role("lint", stage="review", hands_off_to="judge", instruction="lint it")
    assert engine(job_raw, tmp_path).run() == "finished"
    assert (tmp_path / "run/extensions/review_linter.json").exists()


def test_consultation_logged(job_raw, tmp_path, scenario):
    scenario()
    e = engine(job_raw, tmp_path)
    e.overrides = []
    e.consult("plan", "is the plan ok?")
    rec = json.loads((tmp_path / "run/consultations/1.json").read_text())
    assert rec["step"] == "plan" and rec["question"]


def test_token_count_excludes_cache_reads():
    from tikitaka.seats import _sum_usage
    assert _sum_usage({"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 1000,
                       "cache_creation_input_tokens": 20}) == (35, 1000)


def test_artifact_with_code_fences_captured_whole(job_raw, tmp_path, scenario):
    """Regression for the 2026-11-08 live run: a README with code blocks was truncated at the first fence."""
    scenario(review_rounds_with_issue=0)
    assert engine(job_raw, tmp_path).run() == "finished"
    art = (tmp_path / "run/artifacts/v1_a.md").read_text()
    assert "TAIL-AFTER-CODE-BLOCK" in art and "```sh" in art


def test_claude_seat_isolated_from_mcp_and_settings():
    from tikitaka import seats
    from tikitaka.config import Role
    import shutil as _sh
    if not _sh.which("claude"):
        pytest.skip("claude not installed")
    argv = seats.build_argv(Role("cold_reviewer", "dec", "claude", "m", "high"), Path("o.md"))
    assert "--strict-mcp-config" in argv and argv[argv.index("--setting-sources") + 1] == ""


def test_builder_prompt_and_parser_use_same_delimiters(job_raw):
    from tikitaka import prompts
    from tikitaka.config import validate_job
    p = prompts.builder(validate_job(job_raw), "plan", "spec", "", None, None)
    src = (Path(__file__).resolve().parents[2] / "orchestrate/tikitaka/engine.py").read_text(encoding="utf-8")
    assert "<<<ARTIFACT" in p and "ARTIFACT>>>" in p and "<<<ARTIFACT" in src

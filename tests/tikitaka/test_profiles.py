"""Agent profiles: the engine uses the profiles already on the machine and asks before creating any (engine 1.1)."""
import json
import re
from pathlib import Path

import pytest

from tikitaka import access, preflight, profiles
from tikitaka.config import ConfigError, Role, validate_job
from tikitaka.seats import run_seat

OK = lambda p: (True, "logged in")  # noqa: E731


def _home(tmp_path):
    h = tmp_path / "home"
    (h / ".claude").mkdir(parents=True); (h / ".claude" / "settings.json").write_text("{}")
    (h / ".claude-rev").mkdir(); (h / ".claude-rev" / ".claude.json").write_text("{}")
    (h / ".claude-empty").mkdir()                                            # not a profile: no CLI files
    (h / ".claude-server-tool").mkdir(); (h / ".claude-server-tool" / "config.json").write_text("{}")
    (h / ".codex").mkdir(); (h / ".codex" / "auth.json").write_text("{}")
    return h


def _job(job_raw, **by_role):
    raw = json.loads(json.dumps(job_raw))
    for name, (cli, prof) in by_role.items():
        raw["roles"][name] = {"seat": raw["roles"][name]["seat"], "cli": cli, "model": "m", **({"profile": prof} if prof else {})}
    return validate_job(raw)


def test_discover_finds_only_real_profiles(tmp_path):
    found = profiles.discover(_home(tmp_path), environ={})
    assert [p.name for p in found["claude"]] == ["claude", "claude-rev"]
    assert [p.name for p in found["codex"]] == ["codex"]


def test_discover_includes_profile_folder_from_env(tmp_path):
    h = _home(tmp_path)
    other = tmp_path / "elsewhere" / "myclaude"; other.mkdir(parents=True); (other / ".credentials.json").write_text("{}")
    names = [p.name for p in profiles.discover(h, environ={"CLAUDE_CONFIG_DIR": str(other)})["claude"]]
    assert names == ["claude", "claude-rev", "myclaude"]


def test_seat_profile_resolved_to_its_folder(job_raw, tmp_path):
    h = _home(tmp_path)
    job = _job(job_raw, cold_reviewer=("claude", "claude-rev"), accuracy_reviewer=("codex", "codex"))
    got = profiles.resolve_job_profiles(job, home_dir=h, environ={}, status_fn=OK)
    assert got["cold_reviewer"].path == h / ".claude-rev"
    assert job.roles["accuracy_reviewer"].extra["_profile_path"] == str(h / ".codex")


def test_missing_named_profile_stops_and_offers_create(job_raw, tmp_path):
    job = _job(job_raw, cold_reviewer=("claude", "claude-nothere"))
    with pytest.raises(profiles.ProfileError, match="not on this machine") as e:
        profiles.resolve_job_profiles(job, home_dir=_home(tmp_path), environ={}, status_fn=OK)
    assert e.value.fixes == [("create", "claude", "claude-nothere")]
    assert "claude, claude-rev" in str(e.value)          # lists what is available


def test_no_profiles_at_all_offers_create(job_raw, tmp_path):
    bare = tmp_path / "bare"; bare.mkdir()
    job = _job(job_raw, cold_reviewer=("claude", "claude-x"))
    with pytest.raises(profiles.ProfileError, match="No agent profiles found") as e:
        profiles.resolve_job_profiles(job, home_dir=bare, environ={}, status_fn=OK)
    assert e.value.none_found and ("create", "claude", "claude-x") in e.value.fixes


def test_seat_without_profile_refused(job_raw, tmp_path):
    job = _job(job_raw, cold_reviewer=("claude", None))
    with pytest.raises(profiles.ProfileError, match="names no claude profile"):
        profiles.resolve_job_profiles(job, home_dir=_home(tmp_path), environ={}, status_fn=OK)


def test_profile_given_as_folder_must_be_a_found_profile(job_raw, tmp_path):
    h = _home(tmp_path)
    ok = _job(job_raw, cold_reviewer=("claude", str(h / ".claude-rev")))
    assert profiles.resolve_job_profiles(ok, home_dir=h, environ={}, status_fn=OK)["cold_reviewer"].name == "claude-rev"
    bad = _job(job_raw, cold_reviewer=("claude", str(h / ".claude-empty")))
    with pytest.raises(profiles.ProfileError):
        profiles.resolve_job_profiles(bad, home_dir=h, environ={}, status_fn=OK)


def test_logged_out_profile_stops_and_offers_login(job_raw, tmp_path):
    job = _job(job_raw, cold_reviewer=("claude", "claude-rev"))
    with pytest.raises(profiles.ProfileError, match="not logged in") as e:
        profiles.resolve_job_profiles(job, home_dir=_home(tmp_path), environ={}, status_fn=lambda p: (False, "not logged in"))
    assert e.value.fixes == [("login", "claude", "claude-rev")]


def test_seat_gets_only_its_profile_folder(tmp_path, monkeypatch):
    from tikitaka import seats
    seen = {}

    class P:
        returncode, stdout, stderr = 0, json.dumps({"result": "x", "usage": {}}), ""
    monkeypatch.setattr(seats, "build_argv", lambda r, out: ["x"])
    monkeypatch.setattr(seats.subprocess, "run", lambda argv, **kw: (seen.update(env=kw["env"]), P())[1])
    base = access.worker_env("cold_reviewer", [], {"CLAUDE_CONFIG_DIR": "/inherited", "CODEX_HOME": "/inherited", "PATH": "p"})
    assert "CLAUDE_CONFIG_DIR" not in base and "CODEX_HOME" not in base
    run_seat(Role("cold_reviewer", "rev2", "claude", "m", extra={"_profile_path": "/p/claude-rev"}), "p",
             out_prefix=tmp_path / "c", env=base)
    assert seen["env"]["CLAUDE_CONFIG_DIR"] == "/p/claude-rev" and "CODEX_HOME" not in seen["env"]
    run_seat(Role("accuracy_reviewer", "rev1", "codex", "m", extra={"_profile_path": "/p/codex"}), "p",
             out_prefix=tmp_path / "a", env=base)
    assert seen["env"]["CODEX_HOME"] == "/p/codex" and "CLAUDE_CONFIG_DIR" not in seen["env"]


def test_create_profile_makes_folder_and_runs_cli_login(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(preflight, "resolve_application", lambda name: name)

    class R:
        returncode = 0
    p = profiles.create("claude", "claude-new", home_dir=tmp_path,
                        runner=lambda argv, env: (calls.append((argv, env["CLAUDE_CONFIG_DIR"])), R())[1])
    assert p.path == tmp_path / ".claude-new" and p.path.is_dir()
    assert calls == [(["claude", "auth", "login"], str(tmp_path / ".claude-new"))]
    with pytest.raises(ValueError):
        profiles.create("claude", "../escape", home_dir=tmp_path)


def test_cli_asks_user_before_creating_profiles(job_raw, tmp_path, monkeypatch, capsys):
    from tikitaka import __main__ as cli
    bare = tmp_path / "bare"; bare.mkdir()
    monkeypatch.setenv("TIKITAKA_PROFILE_HOME", str(bare))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    job = _job(job_raw, cold_reviewer=("claude", "claude-x"))
    # no terminal: never creates; tells the user what to run; exit 3
    assert cli._check_profiles(job, interactive=False) == 3
    assert "profiles create --cli claude --name claude-x" in capsys.readouterr().out
    # user says no: nothing created
    made = []
    assert cli._check_profiles(job, interactive=True, input_fn=lambda q: "n", create=lambda c, n: made.append(n)) == 3
    assert made == [] and not (bare / ".claude-x").exists()
    # user says yes: profile created, then the check passes
    monkeypatch.setattr(profiles, "login_status", OK)

    def mk(c, n):
        made.append(n); (bare / f".{n}").mkdir(); (bare / f".{n}" / ".credentials.json").write_text("{}")
    assert cli._check_profiles(job, interactive=True, input_fn=lambda q: "y", create=mk) is None
    assert made == ["claude-x"]


def test_no_built_in_logins():
    assert not hasattr(preflight, "DEFAULT_LOGINS")
    with pytest.raises(preflight.PreflightError, match="without a command"):
        preflight.run_logins(["cloud"])


def test_require_logins_needs_its_own_command(job_raw):
    with pytest.raises(ConfigError, match="without a command"):
        validate_job({**job_raw, "require_logins": ["cloud"]})
    validate_job({**job_raw, "require_logins": ["cloud"], "login_commands": {"cloud": ["true"]}})


def test_shipped_engine_names_no_profile_from_this_machine():
    """The engine, its README and tests ship to every machine: none of them may name a profile found on the
    machine running the tests (e.g. the <x> of ~/.claude-<x>). The release scan adds the publisher's seat names."""
    root = Path(__file__).resolve().parents[2]
    suffixes = {p.name.split("-", 1)[1] for ps in profiles.discover().values() for p in ps if "-" in p.name}
    suffixes = {s for s in suffixes if len(s) >= 3}
    if not suffixes:
        pytest.skip("no named profiles on this machine")
    pat = re.compile(r"\b(" + "|".join(map(re.escape, sorted(suffixes))) + r")\b", re.I)
    files = [*(root / "orchestrate" / "tikitaka").glob("*.py"), root / "orchestrate" / "tikitaka" / "README.md",
             *Path(__file__).parent.glob("*.py")]
    hits = [f"{f.name}:{i}" for f in files
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1) if pat.search(line)]
    assert hits == []

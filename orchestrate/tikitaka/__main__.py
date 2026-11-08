"""CLI: python -m tikitaka run --config job.json [--config-from-user] [--project SLUG] [--session ID]
                             [--override role=ENV1,ENV2] [--hub PATH] [--run-dir PATH]
       python -m tikitaka profiles [--no-status]            (agent profiles on this machine)
       python -m tikitaka profiles create --cli claude|codex --name NAME   (new profile; runs the CLI's own login)
       python -m tikitaka profiles login --cli claude|codex --name NAME    (log in to an existing profile)
       python -m tikitaka clear-project-overrides SLUG      (called by project-complete)"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import access, preflight, profiles
from .config import load_job
from .engine import Engine


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tikitaka")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--config", required=True)
    r.add_argument("--config-from-user", action="store_true", help="the user wrote/passed this config (honours double_check)")
    r.add_argument("--project")
    r.add_argument("--session")
    r.add_argument("--override", action="append", default=[], help="role=ENV1,ENV2 (user-typed)")
    r.add_argument("--hub", default=str(Path(__file__).resolve().parents[2]))
    r.add_argument("--run-dir")
    pr = sub.add_parser("profiles")
    pr.add_argument("action", nargs="?", choices=["list", "create", "login"], default="list")
    pr.add_argument("--cli", choices=sorted(profiles.CLIS))
    pr.add_argument("--name")
    pr.add_argument("--no-status", action="store_true", help="list without running each CLI's login status check")
    c = sub.add_parser("clear-project-overrides")
    c.add_argument("slug")
    a = ap.parse_args(argv)
    if a.cmd == "clear-project-overrides":
        access.clear_project_overrides(a.slug)
        print(f"cleared overrides for project {a.slug}")
        return 0
    if a.cmd == "profiles":
        return _profiles_cmd(a)
    job = load_job(a.config, config_source="user" if a.config_from_user else "agent")
    if a.project:
        job.project = a.project
    hub = Path(a.hub)
    run_dir = Path(a.run_dir) if a.run_dir else hub / "_orchestrate" / f"{job.name}_{time.strftime('%Y%m%d_%H%M%S')}"
    overrides = []
    for o in a.override:
        role, _, envs = o.partition("=")
        overrides.append({"roles": [role], "access": [e for e in envs.split(",") if e]})
    rc = _check_profiles(job)
    if rc is not None:
        return rc
    with preflight.RunLock(run_dir / "run"):
        status = Engine(job, run_dir, hub=hub, session_id=a.session, cli_overrides=overrides).run()
    print(f"{status}: {run_dir}")
    return {"finished": 0, "escalated": 3}.get(status, 1)


def _ask(question: str, input_fn=input) -> bool:
    try:
        return input_fn(question + " [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _check_profiles(job, *, interactive=None, input_fn=input, create=profiles.create, login=profiles.login):
    """Stop before any model is called when a seat's profile is missing or logged out. In a terminal, ask the
    user whether to create it / log in; otherwise print what to run and return 3 (the user must decide)."""
    if not any(r.cli in profiles.CLIS for r in job.roles.values()):
        return None
    interactive = sys.stdin.isatty() if interactive is None else interactive
    for _ in range(3):
        try:
            profiles.resolve_job_profiles(job)
            return None
        except profiles.ProfileError as e:
            print(str(e))
            if not e.fixes:
                print("Fix the job config's \"profile\" values (see `python -m tikitaka profiles`), or create one with\n"
                      "  python -m tikitaka profiles create --cli <claude|codex> --name <cli>-<name>")
                return 3
            if not interactive:
                print("The user must decide. To fix, run:")
                for action, cli, name in e.fixes:
                    print(f"  python -m tikitaka profiles {action} --cli {cli} --name {name}")
                return 3
            q = "No agent profiles found. Create agent profiles now?" if e.none_found else "Fix these profiles now?"
            if not _ask(q + " (" + "; ".join(f"{a} {c} profile {n}" for a, c, n in e.fixes) + ")", input_fn):
                print("Stopped: no profile changes made.")
                return 3
            for action, cli, name in e.fixes:
                if action == "create":
                    create(cli, name)
                else:
                    login(cli, profiles.profile_dir(cli, name))
    return 3


def _profiles_cmd(a) -> int:
    if a.action in ("create", "login"):
        if not (a.cli and a.name):
            print(f"profiles {a.action} needs --cli and --name")
            return 2
        if a.action == "login":
            rc = profiles.login(a.cli, profiles.profile_dir(a.cli, a.name))
            print(f"{a.cli} login for profile {a.name} exited {rc}")
            return 0 if rc == 0 else 1
        p = profiles.create(a.cli, a.name)
        print(f"created {p.cli} profile {p.name}: {p.path}")
        return 0
    found = profiles.discover()
    if not any(found.values()):
        print("No agent profiles found on this machine.")
        if sys.stdin.isatty() and _ask("Create agent profiles now?"):
            for cli in profiles.CLIS:
                if _ask(f"Create a {cli} profile?"):
                    name = input(f"name ({cli} or {cli}-<name>): ").strip() or cli
                    p = profiles.create(cli, name)
                    print(f"created {p.cli} profile {p.name}: {p.path}")
        return 0
    for cli, ps in found.items():
        for p in ps:
            status = "" if a.no_status else "  " + profiles.login_status(p)[1]
            print(f"{cli:7} {p.name:24} {p.path}{status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

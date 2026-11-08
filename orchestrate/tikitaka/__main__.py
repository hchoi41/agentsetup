"""CLI: python -m tikitaka run --config job.json [--config-from-user] [--project SLUG] [--session ID]
                             [--override role=ENV1,ENV2] [--hub PATH] [--run-dir PATH]
       python -m tikitaka clear-project-overrides SLUG      (called by project-complete)"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import access, preflight
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
    c = sub.add_parser("clear-project-overrides")
    c.add_argument("slug")
    a = ap.parse_args(argv)
    if a.cmd == "clear-project-overrides":
        access.clear_project_overrides(a.slug)
        print(f"cleared overrides for project {a.slug}")
        return 0
    job = load_job(a.config, config_source="user" if a.config_from_user else "agent")
    if a.project:
        job.project = a.project
    hub = Path(a.hub)
    run_dir = Path(a.run_dir) if a.run_dir else hub / "_orchestrate" / f"{job.name}_{time.strftime('%Y%m%d_%H%M%S')}"
    overrides = []
    for o in a.override:
        role, _, envs = o.partition("=")
        overrides.append({"roles": [role], "access": [e for e in envs.split(",") if e]})
    with preflight.RunLock(run_dir / "run"):
        status = Engine(job, run_dir, hub=hub, session_id=a.session, cli_overrides=overrides).run()
    print(f"{status}: {run_dir}")
    return {"finished": 0, "escalated": 3}.get(status, 1)


if __name__ == "__main__":
    sys.exit(main())

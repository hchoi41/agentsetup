"""run_init.py: create a run folder Max can start from (fix brief r1 B-M2; design spec v2 sections 1.6, 2.2 P0).

    python -I -X utf8 -B scripts/run_init.py --run-id ID [--level 60] [--home DIR] [--env NAME | --env-file F]

1. <QT_HOME> is --home, else the QT_HOME environment variable, else %USERPROFILE%/.query-trail (the folder that
   also holds env/<name>.json and active_run.json). The run folder is <QT_HOME>/runs/<run_id>/; `RUN` in the
   skills means this folder.
2. The env file (D-12) is --env-file, else <QT_HOME>/env/<env>.json; it must load (JSON or bq_env.sh style)
   and name a project and a location. Only its name goes into run_config.json (`env`): identifiers never
   enter run files.
3. Writes run_state.json (lib/state.py: P0, pending, attempt 1, the level) and run_config.json from the
   run_config.schema.json defaults: caps 5 GiB per job and 50 GiB per run, local CSV 1 M rows or 100 MB at
   level 90, stream 10 M rows or 1 GB; retention 6 months; reference none; owner_mode null; required_checks
   null (the spec 6.4 default); author mode single; digest route local; sample N 60. The file is validated
   against the schema before anything is written. G0a is not written: Max answers it at P0 (decide.py).
4. Activates the run (<QT_HOME>/active_run.json, read by the hooks) and prints the next commands.
   Fix r11 L7 (live run 06): it prints `RUN=<folder>` and `QT_HOME=<folder>` with forward slashes (the form Git Bash
   and the Python scripts both read), and the next commands in that form; when QT_HOME was given with backslashes
   (--home or the environment variable) one line says to use the forward-slash form.

Exit 0 created; 2 refused (bad run id or level, env file missing or invalid, run folder exists): no warehouse call
was made; local effects: none (the run folder is not created).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import envfile, runctx, schema_check, state  # noqa: E402
from lib.exit_codes import ExitCode, RefusedError  # noqa: E402
from lib.levels import LevelError, parse_level  # noqa: E402

RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ENV_NAME = re.compile(r"^[a-z0-9_-]{1,32}$")


def schema_defaults() -> dict[str, Any]:
    """Default values of run_config.schema.json (top level and caps)."""
    sch = schema_check.load_schema(runctx.CONFIG_DIR / "run_config.schema.json")
    props = sch["properties"]
    caps = {k: v["default"] for k, v in props["caps"]["properties"].items() if "default" in v}
    return {"author_mode": props["author_mode"]["default"], "sample_n": props["sample_n"]["default"],
            "digest_route": props["digest_route"]["default"], "owner_mode": props["owner_mode"]["default"],
            "retention": {"months": props["retention"]["properties"]["months"]["default"]}, "caps": caps}


def build_config(run_id: str, env_name: str, level: int) -> dict[str, Any]:
    """run_config.json for a new run: schema defaults, the env name, the level; validated."""
    d = schema_defaults()
    cfg = {"schema_version": "1", "run_id": run_id, "created_utc": runctx.fmt_utc(runctx.utc_now()),
           "env": env_name, "confidence_level": level, "author_mode": d["author_mode"], "sample_n": d["sample_n"],
           "float_tolerance": None, "digest_route": d["digest_route"], "caps": d["caps"],
           "retention": d["retention"], "reference": {"type": "none"}, "owner_mode": d["owner_mode"],
           "required_checks": None, "plugin_version": runctx.plugin_version()}  # fix r4 C4-LOW (W36)
    errs = schema_check.validate(cfg, schema_check.load_schema(runctx.CONFIG_DIR / "run_config.schema.json"))
    if errs:
        raise RefusedError("run_config template fails run_config.schema.json: " + "; ".join(errs[:5]))
    return cfg


def resolve_env(home: Path, env_name: str | None, env_file: str | None) -> tuple[str, Path]:
    """(env name for run_config, env file path); refuse a missing or invalid env file."""
    if env_file:
        path = Path(env_file)
        name = env_name or re.sub(r"[^a-z0-9_-]+", "_", path.stem.lower())[:32] or "default"
    else:
        name = env_name or "default"
        path = home / "env" / f"{name}.json"
    if not ENV_NAME.match(name):
        raise RefusedError(f"env name {name!r} must match {ENV_NAME.pattern}")
    if not path.is_file():
        raise RefusedError(f"env file missing: {path} (fields: project, location, service_account, datasets, "
                           "placeholders; or a bq_env.sh with BQ_PROJECT, BQ_LOCATION, QT_SERVICE_ACCOUNT, "
                           "BQ_SCRATCH_DATASET)")
    try:
        env = envfile.load_any(path)
    except (OSError, ValueError) as exc:
        raise RefusedError(f"env file {path.name} unreadable: {type(exc).__name__}") from exc
    missing = [k for k in ("project", "location") if not env.get(k)]
    if missing:
        raise RefusedError(f"env file {path.name} lacks {', '.join(missing)}")
    return name, path


def interpreter() -> str:
    """The pinned interpreter for the printed commands: QT_PY (QC_PY accepted as an alias), else this one."""
    return envfile.interpreter(os.environ) or sys.executable


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="run_init.py", description="create a query-trail run (spec v2 1.6)")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--level", default="60", help="60..100 or quick, medium, high, extra high, max")
    ap.add_argument("--home", default=None, help="QT_HOME (default: QT_HOME env var, else ~/.query-trail)")
    ap.add_argument("--env", default=None, help="env name: <QT_HOME>/env/<env>.json")
    ap.add_argument("--env-file", default=None, help="explicit env file (JSON or bq_env.sh style)")
    a = ap.parse_args(argv)

    def body() -> int:
        if not RUN_ID.match(a.run_id):
            raise RefusedError(f"run id {a.run_id!r} must match {RUN_ID.pattern}")
        try:
            level = parse_level(a.level)
        except LevelError as exc:
            raise RefusedError(f"level: {exc}") from exc
        home = Path(a.home) if a.home else state.qt_home()
        env_name, env_path = resolve_env(home, a.env, a.env_file)
        run = home / "runs" / a.run_id
        if run.exists():
            raise RefusedError(f"{run} already exists; a new run needs a new run id")
        cfg = build_config(a.run_id, env_name, level)
        st = state.new_state(a.run_id, level)
        run.mkdir(parents=True)
        state.write_state(state.state_path(run), st)
        runctx.write_json(run / runctx.RUN_CONFIG, cfg)
        ptr = state.activate(run, home)
        py, plugin = interpreter(), runctx.PLUGIN_ROOT
        print(f"run_init.py: run {a.run_id} created at {run} (level {level}, env {env_name}); "
              f"active run: {ptr}")
        fwd = lambda p: Path(p).as_posix()  # noqa: E731 - fix r11 L7: one form for Git Bash and Python alike
        print(f"RUN={fwd(run)}")
        print(f"QT_HOME={fwd(home)}")
        given = a.home if a.home else os.environ.get("QT_HOME", "")
        if "\\" in given:
            print(f"note: QT_HOME was given with backslashes; under Git Bash use the forward-slash form: "
                  f"QT_HOME={fwd(home)}")
        if a.home and Path(a.home).resolve() != state.qt_home().resolve():
            print(f"note: set QT_HOME={fwd(home)} for the hooks (they read QT_HOME to find active_run.json)")
        print("next: in Claude Code run  /query-trail:run run=" + fwd(run) + "  (P0 asks G0a first), or by hand:")
        print(f'  "{fwd(py)}" -I -X utf8 -B "{fwd(plugin / "scripts" / "preflight.py")}" --run-dir "{fwd(run)}" '
              f'--env-file "{fwd(env_path)}"')
        return int(ExitCode.PASS)

    return runctx.run_cli(body, "run_init.py")


if __name__ == "__main__":
    sys.exit(main())

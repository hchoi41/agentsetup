"""Agent profiles: use the profiles already set up on this machine; never ship or assume profile names.

A profile is a CLI's own config folder in the user's home directory:
    claude   ~/.claude (name "claude")   and ~/.claude-<x> (name "claude-<x>")   -> CLAUDE_CONFIG_DIR
    codex    ~/.codex  (name "codex")    and ~/.codex-<x>  (name "codex-<x>")    -> CODEX_HOME
A folder counts as a profile only when it holds the CLI's own files (markers below); a folder set in
CLAUDE_CONFIG_DIR / CODEX_HOME is also found. Every claude/codex seat names its profile in the job
config (``"profile": "claude-x"``); the engine checks it exists and is logged in, then gives that
folder to that seat only. When none is found, or a named one is missing, the run stops before any
model is called and the user is asked whether to create it (``python -m tikitaka profiles create``),
which runs the CLI's own login. Secrets are never read or stored here.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import preflight

CLIS = {
    "claude": {"env": "CLAUDE_CONFIG_DIR", "markers": (".claude.json", ".credentials.json", "settings.json", "projects"),
               "status": ["auth", "status", "--json"], "login": ["auth", "login"]},
    "codex": {"env": "CODEX_HOME", "markers": ("auth.json", "config.toml"),
              "status": ["login", "status"], "login": ["login"]},
}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


class ProfileError(preflight.PreflightError):
    """A seat's profile is missing or not logged in. ``fixes`` lists (action, cli, name) the user can approve:
    action "create" (make the folder and log in) or "login" (log in to an existing profile)."""

    def __init__(self, message: str, fixes: list, none_found: bool = False):
        super().__init__(message)
        self.fixes = fixes
        self.none_found = none_found


@dataclass(frozen=True)
class Profile:
    cli: str
    name: str
    path: Path


def home() -> Path:
    return Path(os.environ.get("TIKITAKA_PROFILE_HOME") or Path.home())


def _is_profile(d: Path, cli: str) -> bool:
    return d.is_dir() and any((d / m).exists() for m in CLIS[cli]["markers"])


def discover(home_dir: Path | None = None, environ: dict | None = None) -> dict:
    """{cli: [Profile, ...]} for the profiles present on this machine."""
    h = Path(home_dir or home())
    env = os.environ if environ is None else environ
    found = {}
    for cli, spec in CLIS.items():
        out, seen = [], set()

        def add(d: Path):
            key = os.path.normcase(str(d.resolve()))
            if key not in seen and _is_profile(d, cli):
                seen.add(key)
                out.append(Profile(cli, d.name.lstrip("."), d))
        add(h / f".{cli}")
        for d in sorted(h.glob(f".{cli}-*")):
            add(d)
        if env.get(spec["env"]):
            add(Path(os.path.expanduser(env[spec["env"]])))
        found[cli] = out
    return found


def login_status(profile: Profile, timeout: int = 60) -> tuple:
    """(logged_in, detail) from the CLI's own status command run against that profile folder."""
    spec = CLIS[profile.cli]
    env = dict(os.environ)
    env[spec["env"]] = str(profile.path)
    try:
        exe = preflight.resolve_application(profile.cli)
        r = subprocess.run([exe, *spec["status"]], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, env=env)
    except (preflight.PreflightError, OSError, subprocess.TimeoutExpired) as e:
        return False, f"status check failed: {e}"
    out = (r.stdout or "") + (r.stderr or "")
    if profile.cli == "claude":
        try:
            ok = bool(json.loads(r.stdout).get("loggedIn"))
        except ValueError:
            ok = False
    else:
        ok = r.returncode == 0 and "not logged in" not in out.lower()
    return ok, "logged in" if ok else "not logged in"


def _match(value: str, profiles: list) -> Profile | None:
    if any(c in value for c in "/\\~"):          # a folder path: must be one of the profiles found
        key = os.path.normcase(str(Path(os.path.expanduser(value)).resolve()))
        return next((p for p in profiles if os.path.normcase(str(p.path.resolve())) == key), None)
    return next((p for p in profiles if p.name == value), None)


def _available(found: dict, clis) -> str:
    return "; ".join(f"{c}: {', '.join(p.name for p in found[c]) or 'none'}" for c in sorted(clis))


def resolve_job_profiles(job, *, home_dir: Path | None = None, status_fn=None, environ: dict | None = None) -> dict:
    """Check every claude/codex seat's profile; set ``role.extra['_profile_path']``. Raises ProfileError."""
    status_fn = status_fn or login_status
    found = discover(home_dir, environ)
    seats = [r for r in job.roles.values() if r.cli in CLIS]
    used = {r.cli for r in seats}
    problems, fixes = [], []
    none_found = any(not found[c] for c in used)
    resolved = {}
    for r in seats:
        value = r.extra.get("profile")
        if not value:
            problems.append(f"role {r.name} (seat {r.seat}) names no {r.cli} profile; add \"profile\" to it")
            continue
        p = _match(str(value), found[r.cli])
        if p is None:
            problems.append(f"role {r.name} (seat {r.seat}) names {r.cli} profile {value!r}, which is not on this machine")
            v = str(value)
            name = v if v == r.cli or (NAME_RE.match(v) and v.startswith(f"{r.cli}-")) else None
            if name and ("create", r.cli, name) not in fixes:
                fixes.append(("create", r.cli, name))
            continue
        resolved[r.name] = p
    for p in sorted(set(resolved.values()), key=lambda x: (x.cli, x.name)):
        ok, detail = status_fn(p)
        if not ok:
            problems.append(f"{p.cli} profile {p.name!r} ({p.path}) is {detail}")
            fixes.append(("login", p.cli, p.name))
    if none_found and not fixes:
        for c in sorted(c for c in used if not found[c]):
            fixes.append(("create", c, c))
    if problems:
        head = (f"No agent profiles found on this machine for: {', '.join(sorted(c for c in used if not found[c]))}.\n"
                if none_found else "")
        raise ProfileError(head + "\n".join(problems) + f"\nProfiles on this machine: {_available(found, used)}.",
                           fixes, none_found)
    for name, p in resolved.items():
        job.roles[name].extra["_profile_path"] = str(p.path)
    return resolved


def profile_dir(cli: str, name: str, home_dir: Path | None = None) -> Path:
    if cli not in CLIS:
        raise ValueError(f"profiles exist only for {sorted(CLIS)}")
    if name == cli:
        return Path(home_dir or home()) / f".{cli}"
    if not (NAME_RE.match(name) and name.startswith(f"{cli}-")):
        raise ValueError(f"profile name must look like {cli}-<lowercase letters, digits, - or _>")
    return Path(home_dir or home()) / f".{name}"


def login(cli: str, path: Path, runner=subprocess.run) -> int:
    """Run the CLI's own interactive login against ``path`` (inherits the terminal; nothing captured)."""
    env = dict(os.environ)
    env[CLIS[cli]["env"]] = str(path)
    return runner([preflight.resolve_application(cli), *CLIS[cli]["login"]], env=env).returncode


def create(cli: str, name: str, *, home_dir: Path | None = None, runner=subprocess.run) -> Profile:
    """Make a new profile folder and log in to it. Refuses a folder that is already a profile."""
    d = profile_dir(cli, name, home_dir)
    if _is_profile(d, cli):
        raise ValueError(f"{cli} profile {name!r} already exists at {d}")
    d.mkdir(parents=True, exist_ok=True)
    rc = login(cli, d, runner)
    if rc != 0:
        raise preflight.PreflightError(f"{cli} login for profile {name!r} exited {rc}")
    return Profile(cli, name, d)

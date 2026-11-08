"""spawn_scrubbed.py: run a second author in a fresh, scrubbed, packet-only process (design spec v1 sections 1.5, 2.2 P4, 4.2, 7.3).

A Codex run started from a Claude session once leaked the session token into its log and model (project
memory 2026-10-11), so the child gets an environment built from an allow-list only:

- a variable is passed only when its name is on the allow-list (Windows system variables the CLI needs to
  start, plus names given with --allow);
- a name matching TOKEN, SECRET, KEY, PASSWORD, OAUTH, ^CLAUDE_CODE_, ^CLAUDECODE, ^ANTHROPIC_ or
  ^CLAUDE_CONFIG_DIR$ (case-insensitive) is always removed, even when allow-listed;
- cwd is the packet folder; stdout and stderr go to files under spawn/; the child's exit code is passed through.

spawn/<label>.env_names.json records which names were kept and which were removed (names only, never values).
Isolation of file reads is attested, not enforced, on native Windows (IND-3, T14).

CLI: python -I -X utf8 -B scripts/spawn_scrubbed.py --run-dir RUN --label codex_a [--allow NAME ...]
     [--timeout 3600] -- codex exec "..."
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import runctx  # noqa: E402
from lib.exit_codes import ExitCode, RefusedError  # noqa: E402

DENY = re.compile(r"TOKEN|SECRET|KEY|PASSWORD|OAUTH|^CLAUDE_CODE_|^CLAUDECODE|^ANTHROPIC_|^CLAUDE_CONFIG_DIR$", re.I)
ALLOW_DEFAULT = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP", "USERPROFILE",
                 "HOMEDRIVE", "HOMEPATH", "HOME", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES",
                 "PROGRAMFILES(X86)", "PROGRAMW6432", "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)",
                 "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS", "USERNAME", "COMPUTERNAME", "LANG",
                 "LC_ALL", "TZ", "PYTHONUTF8")
SPAWN_DIR = "spawn"


def scrub(env: Mapping[str, str], allow: Sequence[str] = ()) -> tuple[dict[str, str], list[str], list[str]]:
    """(child env, kept names, removed names): allow-list first, deny pattern always wins."""
    allowed = {n.upper() for n in ALLOW_DEFAULT} | {n.upper() for n in allow}
    kept, removed, out = [], [], {}
    for name, value in env.items():
        if DENY.search(name) or name.upper() not in allowed:
            removed.append(name)
        else:
            kept.append(name)
            out[name] = value
    return out, sorted(kept), sorted(removed)


def resolve(cmd: Sequence[str], env: Mapping[str, str]) -> list[str]:
    """Resolve argv[0] on the scrubbed PATH (a .cmd shim works with shell=False on Windows)."""
    exe = shutil.which(cmd[0], path=env.get("PATH") or env.get("Path"))
    if exe is None:
        raise RefusedError(f"command not found on the scrubbed PATH: {cmd[0]}")
    return [exe, *cmd[1:]]


def spawn(run_dir: Path, label: str, cmd: Sequence[str], allow: Sequence[str] = (), timeout: float | None = None,
          env: Mapping[str, str] | None = None) -> int:
    """Run `cmd` scrubbed with cwd = packet/; return its exit code."""
    if not re.fullmatch(r"[a-z0-9_-]{1,40}", label):
        raise RefusedError("label must be lowercase letters, digits, _ or - (max 40)")
    packet = run_dir / "packet"
    if not (run_dir / "packet.sha256").is_file() or not packet.is_dir():
        raise RefusedError("the packet is not frozen: run packet_freeze.py --freeze packet first")
    child_env, kept, removed = scrub(os.environ if env is None else env, allow)
    child_env["QT_SCRUBBED_SPAWN"] = label  # marker: the child knows it runs scrubbed (gate.py checks the wrapper)
    argv = resolve(cmd, child_env)
    out_dir = run_dir / SPAWN_DIR
    out_dir.mkdir(exist_ok=True)
    runctx.write_json(out_dir / f"{label}.env_names.json", {"kept": kept, "removed": removed,
                                                            "cwd": "packet/", "argv0": Path(argv[0]).name})
    with open(out_dir / f"{label}.stdout.txt", "wb") as so, open(out_dir / f"{label}.stderr.txt", "wb") as se:
        try:
            proc = subprocess.run(argv, cwd=str(packet), env=child_env, stdout=so, stderr=se, stdin=subprocess.DEVNULL,
                                  timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            se.write(b"\nspawn_scrubbed.py: timeout\n")
            return int(ExitCode.ENGINE_ERROR)
    return int(proc.returncode)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="spawn_scrubbed.py", description="scrubbed packet-only spawn (spec 2.2 P4, 7.3)")
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--label", required=True)
    ap.add_argument("--allow", nargs="*", default=[])
    ap.add_argument("--timeout", type=float, default=None)
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    cmd = a.cmd[1:] if a.cmd[:1] == ["--"] else a.cmd

    def body() -> int:
        runctx.enter(a.run_dir, ("P4",))
        cfg = runctx.load_run_config(a.run_dir)
        runctx.require_g0a(cfg)
        if not cmd:
            raise RefusedError("no command given after --")
        return spawn(a.run_dir, a.label, cmd, a.allow, a.timeout)

    return runctx.run_cli(body, "spawn_scrubbed.py")


if __name__ == "__main__":
    sys.exit(main())

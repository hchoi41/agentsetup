"""Seat adapters. Every call is a fresh subprocess with a scrubbed environment, an empty working
directory, and the prompt on stdin (never argv). No timeout by default (cut-off prevention rule)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import preflight


@dataclass
class SeatResult:
    text: str
    tokens: int
    incomplete: bool = False
    cutoff_reason: str = ""
    returncode: int = 0
    stderr: str = ""
    usage: dict | None = None


def _sum_usage(u: dict) -> tuple:
    """(billable tokens excluding cache reads, cache-read tokens)."""
    if not isinstance(u, dict):
        return 0, 0
    nums = {k: int(v) for k, v in u.items() if k.endswith("tokens") and isinstance(v, (int, float))}
    cache_read = sum(v for k, v in nums.items() if "cache_read" in k or k == "cached_input_tokens")
    return sum(nums.values()) - cache_read, cache_read


def _codex_tokens(events_path: Path) -> int:
    total = 0
    try:
        for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            u = ev.get("usage") or (ev.get("msg") or {}).get("usage") or {}
            if isinstance(u, dict) and u:
                total = max(total, _sum_usage(u)[0])
    except OSError:
        pass
    return total


def build_argv(role, out_md: Path) -> list:
    if role.cli == "codex":
        exe = preflight.resolve_application("codex")
        return [exe, "exec", "--ignore-user-config", "--skip-git-repo-check",
                "-m", role.model, "-c", f"model_reasoning_effort={role.effort or 'high'}",
                *preflight.codex_sandbox_args(role.name, role.extra.get("sandbox")),
                "--json", "-o", str(out_md), "-"]
    if role.cli == "claude":
        exe = preflight.resolve_application("claude")
        return [exe, "-p", "--model", role.model, "--effort", role.effort or "high",
                "--no-session-persistence", "--output-format", "json", "--tools", "",
                # isolation: no MCP servers, plugins, hooks or user settings in the seat's context
                # (the default profile loaded ~560k tokens of context per call on 2026-11-08)
                "--strict-mcp-config", "--setting-sources", role.extra.get("setting_sources", "")]
    if role.cli == "fake":
        return [sys.executable, "-I", str(Path(role.extra["script"]).resolve())]
    raise ValueError(f"no adapter for cli {role.cli!r}")


def run_seat(role, prompt: str, *, out_prefix: Path, env: dict) -> SeatResult:
    """Run one seat once. A cut-off happens only if the user set ``cutoff_seconds`` on the role
    (explicit request) — it is then logged and the output marked incomplete."""
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_md = out_prefix.with_suffix(".md")
    argv = build_argv(role, out_md)
    preflight.check_argv(argv)
    env = dict(env)
    if role.cli == "claude":
        if role.extra.get("profile"):
            env["CLAUDE_CONFIG_DIR"] = os.path.expanduser(role.extra["profile"])
        env["CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS"] = "0"   # set after the scrub: never kill background review work
    cutoff = role.extra.get("cutoff_seconds")
    out_prefix.with_suffix(".prompt.md").write_text(prompt, encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="tikitaka_seat_") as cwd:
        try:
            p = subprocess.run(argv, input=prompt, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", cwd=cwd, env=env, timeout=cutoff)
        except subprocess.TimeoutExpired as e:
            partial = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            return SeatResult(partial, 0, True, f"user-set cutoff_seconds={cutoff}", -1, "")
    stdout, stderr = p.stdout or "", p.stderr or ""
    out_prefix.with_suffix(".stdout.txt").write_text(stdout, encoding="utf-8")
    out_prefix.with_suffix(".err").write_text(stderr, encoding="utf-8")
    tokens, text, usage = 0, "", None
    if role.cli == "codex":
        out_prefix.with_suffix(".events.jsonl").write_text(stdout, encoding="utf-8")
        tokens = _codex_tokens(out_prefix.with_suffix(".events.jsonl"))
        text = out_md.read_text(encoding="utf-8") if out_md.exists() else ""
    elif role.cli in ("claude", "fake"):
        try:
            d = json.loads(stdout)
            text = d.get("result", "")
            usage = d.get("usage") or {}
            tokens = _sum_usage(usage)[0]
        except ValueError:
            text = stdout
        out_md.write_text(text, encoding="utf-8")
    incomplete = "Background tasks still running" in stderr and "stopping them" in stderr
    return SeatResult(text, tokens, incomplete, "headless harness stopped background work" if incomplete else "",
                      p.returncode, stderr[-2000:], usage)

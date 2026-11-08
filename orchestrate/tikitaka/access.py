"""Live-environment access, user overrides and double-check scope (charter §3.6, §5).

User-owned settings live outside the hub and outside run directories:
    <settings_root>/sessions/<session_id>.json   (session scope)
    <settings_root>/projects/<slug>.json         (project scope)
settings_root = $TIKITAKA_SETTINGS_ROOT or ~/.maxagent/tikitaka. Each file may hold
    {"overrides": [{"roles": [...], "access": [...], "reason": "..."}],
     "double_check": {"enabled": true}}
Overrides may also come from the CLI flag ``--override role=ENV1,ENV2`` (user-typed).
Agent-written sources (job config, run directory, relayed prompts) are never read as override sources.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

SECRET_RE = re.compile(r"TOKEN|SECRET|KEY|PASSWORD|OAUTH|CREDENTIAL|^CLAUDE_CODE_|^CLAUDECODE|^ANTHROPIC_|"
                       r"^CLAUDE_CONFIG_DIR$|^CODEX_HOME$|^GOOGLE_|^GCLOUD|^CLOUDSDK_|^OPENAI_|^GH_|^GITHUB_|^AZURE_|^AWS_|^GWS", re.I)


def settings_root() -> Path:
    return Path(os.environ.get("TIKITAKA_SETTINGS_ROOT") or Path.home() / ".maxagent" / "tikitaka")


def _read(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def project_completed(slug: str, hub: Path | None) -> bool:
    """A project counts as completed when PROJECT_INDEX lists it as completed, or its settings file says so."""
    if _read(settings_root() / "projects" / f"{slug}.json").get("completed"):
        return True
    if hub:
        idx = hub / "00.ABOUT" / "PROJECT_INDEX.md"
        reg = hub / "00.ABOUT" / "PROJECT_REGISTRY.json"
        try:
            names = {p["canonical_slug"]: p.get("display_name", "") for p in json.loads(reg.read_text(encoding="utf-8"))["projects"]}
            name = names.get(slug)
            if name:
                for line in idx.read_text(encoding="utf-8").splitlines():
                    cells = [c.strip() for c in line.split("|")]
                    if len(cells) > 2 and cells[1] == name:
                        return cells[2].lower() == "completed"
        except (OSError, ValueError, KeyError):
            pass
    return False


def clear_project_overrides(slug: str) -> bool:
    """Called by project-complete: removes overrides and double-check from the project settings and marks it completed."""
    p = settings_root() / "projects" / f"{slug}.json"
    d = _read(p)
    d.pop("overrides", None)
    d.pop("double_check", None)
    d["completed"] = True
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=1), encoding="utf-8")
    return True


def active_overrides(*, project: str | None, session_id: str | None, hub: Path | None, cli_overrides: list) -> list:
    """Overrides valid for the current project/session. Each record carries its scope and source."""
    out = []
    for o in cli_overrides:
        out.append({**o, "scope": {"session": session_id}, "source": "cli flag (user)"})
    if project:
        if not project_completed(project, hub):
            for o in _read(settings_root() / "projects" / f"{project}.json").get("overrides", []):
                out.append({**o, "scope": {"project": project}, "source": f"user settings: projects/{project}.json"})
    elif session_id:
        for o in _read(settings_root() / "sessions" / f"{session_id}.json").get("overrides", []):
            out.append({**o, "scope": {"session": session_id}, "source": f"user settings: sessions/{session_id}.json"})
    return out


def reject_agent_overrides(job_raw: dict, run_dir: Path) -> list:
    """Overrides found in agent-writable places are rejected and logged (never applied)."""
    rejected = []
    if "overrides" in job_raw:
        rejected.append({"source": "job config", "value": job_raw["overrides"], "status": "rejected"})
    return rejected


def worker_env(role: str, overrides: list, base: dict | None = None) -> dict:
    """Environment for a worker process: secrets scrubbed; only user-overridden variables pass for that role."""
    base = dict(os.environ if base is None else base)
    allowed = set()
    for o in overrides:
        if role in o.get("roles", []):
            allowed |= set(o.get("access", []))
    return {k: v for k, v in base.items() if not SECRET_RE.search(k) or k in allowed}


def double_check_state(job, *, session_id: str | None, run_round: int, start_round_hint: int | None = None) -> dict:
    """Is double-check active for this run-wide round? Session/project settings beat the job config."""
    rec = {"active": False, "source": job.double_check_source, "run_round": run_round}
    setting = None
    if job.project and not project_completed(job.project, None):
        s = _read(settings_root() / "projects" / f"{job.project}.json").get("double_check")
        if s is not None:
            setting, rec["scope"], rec["source"] = s, {"project": job.project}, f"user settings: projects/{job.project}.json"
    if setting is None and not job.project and session_id:
        s = _read(settings_root() / "sessions" / f"{session_id}.json").get("double_check")
        if s is not None:
            setting, rec["scope"], rec["source"] = s, {"session": session_id}, f"user settings: sessions/{session_id}.json"
    if setting is not None:
        rec["active"] = bool(setting.get("enabled"))
        rec["start_round"] = 1
        return rec
    dc = job.double_check
    if dc.get("enabled"):
        n = int(dc["duration"]["n"])
        start = int(dc.get("start_round", start_round_hint or 1))
        rec.update(scope={"iterations": n}, start_round=start, active=start <= run_round <= start + n - 1)
    return rec

"""Job config loading and validation (charter §2)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

MANDATORY = ("orchestrator", "builder", "accuracy_reviewer", "cold_reviewer", "judge", "qa_qc", "auditor")
OPTIONAL = ("researcher", "accuracy_reviewer_2", "cold_reviewer_2")
STAGES = ("before_plan", "build", "review", "verdict", "after_loop", "final")
DEFAULT_STAGE = {
    "researcher": "before_plan", "builder": "build",
    "accuracy_reviewer": "review", "cold_reviewer": "review",
    "accuracy_reviewer_2": "review", "cold_reviewer_2": "review",
    "judge": "verdict", "qa_qc": "after_loop", "auditor": "final",
    "orchestrator": None,
}
CLIS = ("claude", "codex", "copilot", "fake", "internal")


class ConfigError(ValueError):
    pass


@dataclass
class Role:
    name: str
    seat: str
    cli: str
    model: str = ""
    effort: str = ""
    stage: str | None = None
    receives: list = field(default_factory=list)
    hands_off_to: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class Job:
    name: str
    roles: dict
    spec_path: Path
    source_paths: list
    brief: str
    artifact_name: str
    milestones: list
    planned_functions: list
    max_iterations: int = 3
    token_cap: int | None = None
    double_check: dict = field(default_factory=lambda: {"enabled": False})
    double_check_source: str = "default"
    project: str | None = None
    user_channel: bool = True
    raw: dict = field(default_factory=dict)
    path: Path | None = None


def load_job(path: str | Path, *, config_source: str = "agent") -> Job:
    """Load and validate a job config.

    ``config_source`` is ``"user"`` only when the user passed the config themselves (CLI flag
    ``--config-from-user``). The job-config ``double_check`` block is honoured only then (charter §3.6).
    """
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    return validate_job(raw, path=path, config_source=config_source)


def validate_job(raw: dict, *, path: Path | None = None, config_source: str = "agent") -> Job:
    base = path.parent if path else Path.cwd()
    roles_raw = raw.get("roles") or {}
    missing = [r for r in MANDATORY if r not in roles_raw]
    if missing:
        raise ConfigError(f"missing mandatory role(s): {missing}")
    roles: dict[str, Role] = {}
    seats: dict[str, str] = {}
    for name, r in roles_raw.items():
        if not isinstance(r, dict) or not r.get("seat") or not r.get("cli"):
            raise ConfigError(f"role {name}: 'seat' and 'cli' are required")
        if r["cli"] not in CLIS:
            raise ConfigError(f"role {name}: unknown cli {r['cli']!r}")
        stage = r.get("stage", DEFAULT_STAGE.get(name))
        if name not in DEFAULT_STAGE and stage not in STAGES:
            raise ConfigError(f"role {name}: new roles need a 'stage' from {STAGES}")
        known = {"seat", "cli", "model", "effort", "stage", "receives", "hands_off_to"}
        roles[name] = Role(name, r["seat"], r["cli"], r.get("model", ""), r.get("effort", ""), stage,
                           list(r.get("receives", [])), r.get("hands_off_to", ""),
                           {k: v for k, v in r.items() if k not in known})
    # Seat aliases are unique across all role entries (charter §2). A seat alias labels one role
    # instance; one *agent* (cli + model + profile) may still fill several roles under different aliases,
    # e.g. "dec" as cold reviewer and "dec.judge" as judge.
    for name, role in roles.items():
        if role.seat in seats:
            raise ConfigError(f"duplicate seat alias {role.seat!r} on roles {seats[role.seat]} and {name}")
        seats[role.seat] = name
    dc = raw.get("double_check") or {"enabled": False}
    dc_source = "default"
    if "double_check" in raw:
        if config_source != "user":
            dc = {"enabled": False, "ignored": True}
            dc_source = "ignored:agent-written job config"
        else:
            dur = (dc.get("duration") or {"type": "iterations", "n": 1})
            if dur.get("type") != "iterations":
                raise ConfigError("job-config double_check.duration.type accepts only 'iterations'")
            if int(dur.get("n", 0)) < 1:
                raise ConfigError("double_check.duration.n must be >= 1")
            dc_source = "user job config"
    mi = int(raw.get("max_iterations", 3))
    if mi < 1:
        raise ConfigError("max_iterations must be >= 1")
    tc = raw.get("token_cap")
    spec = raw.get("spec")
    if not spec:
        raise ConfigError("'spec' (path to the spec the artifact must satisfy) is required")
    return Job(
        name=raw.get("name") or (path.stem if path else "job"),
        roles=roles,
        spec_path=(base / spec).resolve(),
        source_paths=[(base / s).resolve() for s in raw.get("sources", [])],
        brief=raw.get("brief", ""),
        artifact_name=raw.get("artifact_name", "artifact.md"),
        milestones=list(raw.get("milestones", [])),
        planned_functions=list(raw.get("planned_functions", [])),
        max_iterations=mi,
        token_cap=int(tc) if tc is not None else None,
        double_check=dc,
        double_check_source=dc_source,
        project=raw.get("project"),
        user_channel=bool(raw.get("user_channel", True)),
        raw=raw,
        path=path,
    )


def double_check_pair_present(job: Job) -> bool:
    return "accuracy_reviewer_2" in job.roles and "cold_reviewer_2" in job.roles


def extension_roles(job: Job, stage: str) -> list:
    """Roles beyond the built-ins that are dispatched at ``stage`` (charter §2 extension mechanism)."""
    return [r for n, r in job.roles.items() if n not in DEFAULT_STAGE and r.stage == stage]

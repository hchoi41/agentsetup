"""Env-file readers (design spec v1 section 1.6, D-12): identifiers live only in the env file, never in run files.

Two formats give the same normalised dict {project, location, service_account, datasets, placeholders}:

- JSON, the plugin's own format (%USERPROFILE%/.query-trail/env/<env>.json): keys as above.
- Shell, the research project's bq_env.sh style: `export NAME="value"` lines. Mapped names:
  BQ_PROJECT -> project, BQ_LOCATION -> location, QT_SERVICE_ACCOUNT -> service_account,
  BQ_SCRATCH_DATASET (or QT_DATASETS, comma-separated) -> datasets; placeholders
  {"project": project, "ds": first dataset}.

`from_environ` reads the same names from the process environment (tests: QT_ENV_FILE or variables).
`interpreter` gives the pinned Python: QT_PY, with QC_PY accepted as an alias (spec v2 1.6, fix r1 B-M2).
Nothing here prints a value.
"""
from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
from typing import Any, Mapping

SHELL_NAMES = {"BQ_PROJECT": "project", "BQ_LOCATION": "location", "QT_SERVICE_ACCOUNT": "service_account"}


def parse_shell(text: str) -> dict[str, str]:
    """`export NAME=value` / `NAME=value` assignments of a POSIX env script; comments ignored.

    Only `${NAME}` references to names assigned earlier in the same file are expanded (no process
    environment, no command substitution), so the file stays the only source of the values.
    """
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        name, _, rest = line.partition("=")
        if not name.replace("_", "").isalnum():
            continue
        try:
            parts = shlex.split(rest, comments=True, posix=True)
        except ValueError:
            continue
        val = parts[0] if parts else ""
        for k, v in out.items():
            val = val.replace("${" + k + "}", v)
        out[name] = val
    return out


def normalise(names: Mapping[str, str]) -> dict[str, Any]:
    """The env dict from shell-style names."""
    env: dict[str, Any] = {v: names[k] for k, v in SHELL_NAMES.items() if names.get(k)}
    ds = [d.strip() for d in (names.get("QT_DATASETS") or names.get("BQ_SCRATCH_DATASET") or "").split(",") if d.strip()]
    env["datasets"] = ds
    env["placeholders"] = {"project": env.get("project", "")}
    if ds:
        env["placeholders"]["ds"] = ds[0]
    return env


def load_any(path: str | Path) -> dict[str, Any]:
    """Read a JSON or shell env file into the normalised dict."""
    p = Path(path)
    text = p.read_text(encoding="utf-8-sig")
    if p.suffix.lower() == ".json":
        env = json.loads(text)
        env.setdefault("datasets", [])
        env.setdefault("placeholders", {})
        if env.get("project"):
            env["placeholders"].setdefault("project", env["project"])
        return env
    return normalise(parse_shell(text))


def from_environ(environ: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    """QT_ENV_FILE when set, else the shell names in the environment; None when no project is known."""
    e = os.environ if environ is None else environ
    if e.get("QT_ENV_FILE"):
        return load_any(e["QT_ENV_FILE"])
    env = normalise(e)
    return env if env.get("project") else None


def interpreter(names: Mapping[str, str]) -> str | None:
    """The pinned interpreter: QT_PY, else QC_PY (the research env file's name, kept as an alias; fix r1 B-M2)."""
    return names.get("QT_PY") or names.get("QC_PY") or None


def identifier_values(env: Mapping[str, Any]) -> list[str]:
    """The identifier strings an env file holds (project, service account), for leak scans."""
    vals = [str(env.get(k)) for k in ("project", "service_account") if env.get(k)]
    return sorted(set(vals))

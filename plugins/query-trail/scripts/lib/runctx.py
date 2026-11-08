"""Run-folder context shared by the stage-2a scripts (design spec v1 sections 1.4, 1.5, 1.6, 2.1, 7.1).

- `enter(run_dir, phases)`: the phase check every script runs first (spec 1.3, 1.5); a script that
  serves two phases (compile P5/P6, replay P6/P11, contract_check P4/P6) passes when the state is
  at either.
- `load_run_config` validates run_config.json against config/run_config.schema.json; `require_g0a`
  refuses (exit 2) when the G0a approval block is missing ("no G0a row: every script exits 2").
- `load_env` reads the env file %USERPROFILE%\\.query-trail\\env\\<env>.json (D-12): identifiers
  live there, never in run files.
- `write_gate_request` writes gate_request.json for exit 3.
- `write_rows_csv` writes small report tables (lint_report.csv, checks/*.csv, 00_capability_probe.csv)
  through the D-04 writer (Arrow via DuckDB COPY), never through pandas or the csv module.
- `run_cli` maps QueryTrailError to its exit code and any other exception to exit 4 (spec 1.4).
"""
from __future__ import annotations

import calendar
import datetime as dt
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from lib import csv_contract as cc  # noqa: F401 - re-exported for c2 callers
from lib import schema_check, state
from lib.csv_contract import Sidecar, write_arrow_csv, write_json_lf
from lib.exit_codes import CheckFailedError, ExitCode, QueryTrailError, RefusedError

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PLUGIN_ROOT / "config"
RUN_CONFIG = "run_config.json"
GATE_REQUEST = "gate_request.json"
ENV_DIR_DEFAULT = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".query-trail" / "env"


class ConfigError(RefusedError):
    """run_config.json or the env file is missing or invalid (exit 2)."""


# ---------------------------------------------------------------- time
def utc_now() -> dt.datetime:
    """Current UTC time, second precision, tz-aware."""
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def fmt_utc(t: dt.datetime) -> str:
    """ISO 8601 with a Z suffix."""
    return t.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_utc(text: str) -> dt.datetime:
    """Parse ISO 8601 (Z or offset); naive input is taken as UTC."""
    t = dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def add_months(t: dt.datetime, months: int) -> dt.datetime:
    """Calendar-month addition, day clamped to the month's length (spec 7.1)."""
    m = t.month - 1 + months
    y, m = t.year + m // 12, m % 12 + 1
    return t.replace(year=y, month=m, day=min(t.day, calendar.monthrange(y, m)[1]))


def retention_until(created: dt.datetime, cfg: Mapping[str, Any]) -> str:
    """created + 6 calendar months, or the run's override (months or a date)."""
    ret = cfg.get("retention") or {}
    if ret.get("until_date"):
        d = dt.date.fromisoformat(ret["until_date"])
        return fmt_utc(dt.datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=dt.timezone.utc))
    return fmt_utc(add_months(created, int(ret.get("months", 6))))


# ---------------------------------------------------------------- JSON
def read_json(path: Path) -> Any:
    """Read a JSON file (BOM tolerated on read only)."""
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path: Path, obj: Any) -> None:
    """Write JSON under the file contract (UTF-8 without BOM, LF)."""
    write_json_lf(Path(path), obj)


# ---------------------------------------------------------------- plugin version (fix r4 C4-LOW, walk W36)
def plugin_version() -> str | None:
    """`version` of this plugin's .claude-plugin/plugin.json, or None when unreadable."""
    try:
        return str(json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8-sig"))
                   ["version"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def check_plugin_version(run_dir: Path) -> None:
    """Refuse (exit 2) a run whose run_config.json plugin_version (stamped by run_init.py) differs from the current
    plugin: its earlier files follow another plugin's formats. A run_config without the stamp (a run started before
    fix round 4) is not checked."""
    p = Path(run_dir) / RUN_CONFIG
    try:
        stamped = json.loads(p.read_text(encoding="utf-8-sig")).get("plugin_version") if p.is_file() else None
    except (OSError, ValueError, AttributeError):
        return
    now = plugin_version()
    if stamped and now and str(stamped) != now:
        raise ConfigError(f"run started under plugin {stamped}; current {now}; start a new run")


# ---------------------------------------------------------------- phase gate
def enter(run_dir: Path, phases: Sequence[str]) -> state.RunState:
    """Phase check (spec 1.3): raise StateError (exit 2) unless the state is at one of `phases`; fix r4: and refuse
    a run started under another plugin version (check_plugin_version)."""
    check_plugin_version(run_dir)
    reasons = []
    for ph in phases:
        res = state.check_phase(state.state_path(run_dir), ph)
        if res.code == ExitCode.PASS:
            return state.read_state(state.state_path(run_dir))
        reasons.append(res.reason)
    raise state.StateError("; ".join(dict.fromkeys(reasons)))


# ---------------------------------------------------------------- run config and env
def load_run_config(run_dir: Path, validate: bool = True) -> dict[str, Any]:
    """Load run_config.json; with `validate`, refuse anything the schema rejects."""
    p = Path(run_dir) / RUN_CONFIG
    if not p.is_file():
        raise ConfigError(f"{p} does not exist")
    try:
        cfg = read_json(p)
    except (OSError, ValueError) as exc:
        raise ConfigError(f"{p}: {exc}") from exc
    if validate:
        errs = schema_check.validate(cfg, schema_check.load_schema(CONFIG_DIR / "run_config.schema.json"))
        if errs:
            raise ConfigError(f"{p} fails run_config.schema.json: " + "; ".join(errs[:8]))
    return cfg


def has_g0a(cfg: Mapping[str, Any]) -> bool:
    """True when the G0a approval block is present."""
    return isinstance(cfg.get("g0a_approval"), Mapping) and bool(cfg["g0a_approval"].get("approved_by"))


def require_g0a(cfg: Mapping[str, Any]) -> None:
    """Spec 2.2 P0: without a G0a record every script exits 2."""
    if not has_g0a(cfg):
        raise ConfigError("no G0a approval in run_config.json: nothing runs before G0a (spec 2.2 P0)")


def env_path(env_name: str, override: str | Path | None = None) -> Path:
    """Env file path: explicit override, else %USERPROFILE%\\.query-trail\\env\\<env>.json."""
    return Path(override) if override else ENV_DIR_DEFAULT / f"{env_name}.json"


def load_env(cfg: Mapping[str, Any], override: str | Path | None = None) -> dict[str, Any]:
    """Load the env file: project, location, service_account, datasets, placeholders (D-12)."""
    from lib import envfile

    p = env_path(str(cfg.get("env", "")), override)
    if not p.is_file():
        raise ConfigError(f"env file missing: {p}")
    try:
        env = envfile.load_any(p)  # JSON, or a bq_env.sh-style shell file (lib.envfile)
    except (OSError, ValueError) as exc:
        raise ConfigError(f"env file {p.name} unreadable: {exc}") from exc
    for k in ("project", "location"):
        if not isinstance(env.get(k), str) or not env[k]:
            raise ConfigError(f"env file {p.name}: '{k}' missing")
    env.setdefault("datasets", [])
    env.setdefault("placeholders", {})
    env["placeholders"].setdefault("project", env["project"])
    return env


# ---------------------------------------------------------------- gate requests
def write_gate_request(run_dir: Path, gate: str, phase: str, script: str, reason: str,
                       details: Any = None) -> Path:
    """Write gate_request.json (exit 3 contract, spec 1.4); one open request per run folder.

    `details` is kept as given (a mapping, or a list of problems); None becomes {}. The Stop hook
    refuses to end a run while this file exists without an answer (gate_answer.json, spec 1.5).
    """
    p = Path(run_dir) / GATE_REQUEST
    det = dict(details) if isinstance(details, Mapping) else ({} if details is None else details)
    write_json(p, {"schema_version": "1", "gate": gate, "phase": phase, "script": script,
                   "reason": reason, "details": det, "created_utc": fmt_utc(utc_now())})
    return p


def archive_own_request(run_dir: Path, script: str, gates: Sequence[str] | None = None,
                        phase: str | None = None, why: str | None = None) -> str | None:
    """Fix r4 C4-10 (shared helper, every exit-3 script): on a later exit 0, move this script's own open
    gate_request.json (its `script` is `script`, its gate in `gates` and its phase `phase` when given) to
    gates/stale_<gate>_request_<time>.json, with archived_utc and archived_by. The exit 0 shows the condition that
    opened it no longer holds. A request another script opened, or one of another gate or phase, is left alone.
    Return the archived run-relative path, or None."""
    p = Path(run_dir) / GATE_REQUEST
    if not p.is_file():
        return None
    try:
        req = read_json(p)
    except (OSError, ValueError):
        return None
    if not isinstance(req, Mapping) or str(req.get("script")) != script:
        return None
    if gates is not None and str(req.get("gate")) not in gates:
        return None
    if phase is not None and str(req.get("phase")) != phase:
        return None
    gdir = Path(run_dir) / "gates"
    gdir.mkdir(exist_ok=True)
    stamp = state.utc_now().replace("-", "").replace(":", "")
    gate = str(req.get("gate"))
    dest, n = gdir / f"stale_{gate}_request_{stamp}.json", 2
    while dest.exists():
        dest, n = gdir / f"stale_{gate}_request_{stamp}_{n}.json", n + 1
    write_json(dest, {**dict(req), "archived_utc": state.utc_now(),
                      "archived_by": why or f"{script} exit 0 at {gate}: the condition that opened it no longer holds"})
    p.unlink()
    return dest.relative_to(run_dir).as_posix()


# ---------------------------------------------------------------- report CSVs through the D-04 writer
def write_rows_csv(path: Path, columns: Sequence[str], rows: Sequence[Sequence[Any]],
                   types: Mapping[str, Any] | None = None) -> Sidecar:
    """Write rows (text unless `types` gives a pyarrow type) via Arrow and DuckDB COPY."""
    import pyarrow as pa

    types = types or {}
    arrays = []
    for i, c in enumerate(columns):
        t = types.get(c, pa.string())
        vals = [r[i] for r in rows]
        if t == pa.string():
            vals = [None if v is None else (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
                                            if isinstance(v, (dict, list)) else str(v)) for v in vals]
        arrays.append(pa.array(vals, type=t))
    table = pa.Table.from_arrays(arrays, names=list(columns))
    return write_arrow_csv(table, Path(path), expected_rows=len(rows))


# ---------------------------------------------------------------- CLI wrapper
def run_cli(fn: Callable[[], int], script: str) -> int:
    """Run a script body; QueryTrailError -> its exit code; anything else -> 4 (engine/env error)."""
    try:
        return int(fn())
    except QueryTrailError as exc:
        sys.stderr.write(f"{script}: {ExitCode(exc.exit_code).name}: {exc}\n")
        return int(exc.exit_code)
    except Exception as exc:  # noqa: BLE001 - spec 1.4: unexpected failure is an engine/environment error
        sys.stderr.write(f"{script}: ENGINE_ERROR: {type(exc).__name__}: {exc}\n")
        traceback.print_exc(file=sys.stderr)
        return int(ExitCode.ENGINE_ERROR)


# ================================================================ merged from stage2b_common.py (NOTES_stage2b item 1)
# The P1, P2 and P8 to P11 scripts import this module as `c2`. Names that collide with the stage-2a
# helpers above carry a distinct name: utc_now_text (str), read_input_json (missing file = exit 2),
# load_run_config_raw (no schema validation), run_cli_args (argparse wrapper).

CHECKS_MANIFEST = "checks_manifest.csv"
CHECK_COLUMNS: tuple[tuple[str, str], ...] = (
    ("check_id", "VARCHAR"), ("phase", "VARCHAR"), ("family", "VARCHAR"), ("status", "VARCHAR"),
    ("not_run_reason", "VARCHAR"), ("required", "BOOLEAN"), ("csv_path", "VARCHAR"),
    ("csv_sha256", "VARCHAR"), ("detail", "VARCHAR"), ("created_utc", "VARCHAR"),
    ("attempt", "BIGINT"),  # fix r2 A2-H1: the run state's attempt that wrote the row (stamped on write)
    ("evidence_sha256", "VARCHAR"))  # fix r3 A3-4: what a decision binds to (evidence_sha256 below)
CHECK_RUN_STATUSES = ("PASS", "FAIL", "NOT_RUN", "INCONCLUSIVE")  # INCONCLUSIVE: P7 comparisons only (fix r1)
FINAL_STATUSES = ("PASS", "FAIL-explained", "FAIL-unexplained", "NOT-RUN-at-level")
EXPLAINING_OUTCOMES = frozenset({"explained", "accept", "accepted", "explained_exception"})
DECISION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("gate", "VARCHAR"), ("phase", "VARCHAR"), ("input_hash", "VARCHAR"), ("outcome", "VARCHAR"),
    ("reason", "VARCHAR"), ("time_utc", "VARCHAR"), ("check_id", "VARCHAR"), ("decided_by", "VARCHAR"),
    ("answer_text", "VARCHAR"), ("attempt", "BIGINT"), ("supersedes", "BIGINT"), ("gate_request", "VARCHAR"),
    ("category", "VARCHAR"))
# G4 accepts a reference difference only, with one of these categories (fix r1 A-H1); never a decomposition FAIL.
G4_CATEGORIES = ("timing", "rounding", "scope", "known_source_defect", "other_with_reason")


class InputMissingError(RefusedError):
    """A required input file is missing or unreadable (exit 2: nothing ran)."""


def utc_now_text() -> str:
    """Current UTC time, ISO 8601 with Z, second precision (same form as lib.state)."""
    return state.utc_now()


def canonical_json(obj: Any) -> str:
    """Sorted-key compact JSON; dates and other objects as str (stable hashing input)."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def sha256_text(text: str) -> str:
    """Hex SHA-256 of UTF-8 text."""
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_obj(obj: Any) -> str:
    """Hex SHA-256 of the canonical JSON of an object."""
    return sha256_text(canonical_json(obj))


def read_input_json(path: Path, what: str = "input") -> Any:
    """Load a JSON file; a missing or malformed file is an InputMissingError (exit 2)."""
    p = Path(path)
    if not p.is_file():
        raise InputMissingError(f"{what} missing: {p}")
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise InputMissingError(f"{what} unreadable: {p}: {exc}") from exc


def write_text(path: Path, text: str) -> None:
    """Write text under the file contract (UTF-8 without BOM, LF)."""
    from lib.csv_contract import write_text_lf

    write_text_lf(Path(path), text)


def phase_check(run_dir: Path, phases: Sequence[str]) -> str:
    """Pass when run_state.json names one of `phases` (pending or in progress); else RefusedError. Fix r4: a run
    started under another plugin version is refused (check_plugin_version)."""
    check_plugin_version(run_dir)
    reasons = []
    for ph in phases:
        res = state.check_phase(state.state_path(run_dir), ph)
        if res.code == ExitCode.PASS:
            return ph
        reasons.append(res.reason)
    raise RefusedError("phase check failed: " + "; ".join(dict.fromkeys(reasons)))


def base_parser(prog: str, description: str) -> Any:
    """argparse parser with the shared --run-dir option."""
    import argparse

    ap = argparse.ArgumentParser(prog=prog, description=description)
    ap.add_argument("--run-dir", required=True, type=Path, help="runs/<run_id>/ folder (holds run_state.json)")
    return ap


def run_cli_args(main: Callable[[Any], int], parser: Any, argv: Sequence[str] | None = None) -> int:
    """Parse, run, and map library errors to the spec 1.4 exit codes (message on stderr)."""
    import duckdb

    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        return int(main(args))
    except QueryTrailError as exc:
        label = {ExitCode.REFUSED: "REFUSED", ExitCode.FAIL: "FAIL"}.get(exc.exit_code, exc.exit_code.name)
        sys.stderr.write(f"{label}: {exc}\n")
        return int(exc.exit_code)
    except (duckdb.Error, OSError) as exc:
        sys.stderr.write(f"ENGINE_ERROR: {type(exc).__name__}: {exc}\n")
        return int(ExitCode.ENGINE_ERROR)


def write_records(path: Path, columns: Sequence[tuple[str, str]], rows: Iterable[Sequence[Any]]) -> Sidecar:
    """Write Python records as a contract CSV: typed DuckDB table, then lib write_query_csv (D-04)."""
    import duckdb

    from lib import csv_contract as cc

    con = duckdb.connect()
    try:
        cc.run_sql(con, "SET TimeZone = 'UTC'")
        defs = ", ".join(f"{cc.quote_ident(n)} {t}" for n, t in columns)
        cc.run_sql(con, f"CREATE TABLE qt_rec (qt_ord BIGINT, {defs})")
        ph = ", ".join("?" for _ in range(len(columns) + 1))
        data = [(i, *r) for i, r in enumerate(rows)]
        for r in data:
            if len(r) != len(columns) + 1:
                raise RefusedError(f"{path}: record has {len(r) - 1} values for {len(columns)} columns")
        if data:
            con.executemany(f"INSERT INTO qt_rec VALUES ({ph})", data)
        proj = ", ".join(cc.quote_ident(n) for n, _ in columns)
        return cc.write_query_csv(con, f"SELECT {proj} FROM qt_rec ORDER BY qt_ord", Path(path))
    finally:
        con.close()


def read_records(path: Path, what: str = "CSV") -> tuple[list[str], list[dict[str, Any]]]:
    """Read a contract CSV through lib read_csv; return column names and rows as dicts, file order."""
    import duckdb

    from lib import csv_contract as cc

    p = Path(path)
    if not p.is_file():
        raise InputMissingError(f"{what} missing: {p}")
    con = duckdb.connect()
    try:
        cc.run_sql(con, "SET TimeZone = 'UTC'")
        cc.run_sql(con, "SET threads = 1")
        return cc.fetch_dicts(con, cc.read_csv(con, p))
    finally:
        con.close()


def read_records_if_exists(path: Path) -> list[dict[str, Any]]:
    """Rows of a contract CSV, or [] when the file does not exist."""
    return read_records(path)[1] if Path(path).is_file() else []


def load_run_config_raw(run_dir: Path, required: bool = True) -> dict[str, Any]:
    """run_config.json without schema validation (spec 1.6); {} when optional and absent."""
    p = Path(run_dir) / RUN_CONFIG
    if not p.is_file() and not required:
        return {}
    return read_input_json(p, RUN_CONFIG)


def load_manifest(run_dir: Path) -> dict[str, Any]:
    """manifest.json of the run (written by run_steps.py)."""
    m = read_input_json(Path(run_dir) / "manifest.json", "manifest.json")
    for k in ("run_id", "confidence_level", "steps", "deliverables"):
        if k not in m:
            raise InputMissingError(f"manifest.json lacks {k!r}")
    return m


def run_level(run_dir: Path) -> int:
    """Confidence level from run_state.json (the orchestrator's authority)."""
    return state.read_state(state.state_path(run_dir)).level


def resolve_in_run(run_dir: Path, rel: str) -> Path:
    """Path of a run-relative file; refuse absolute paths or paths that leave the run folder."""
    root = Path(run_dir).resolve()
    p = (root / rel).resolve()
    if p != root and root not in p.parents:
        raise RefusedError(f"path leaves the run folder: {rel}")
    return p


# ---------------------------------------------------------------- decisions.csv (spec 2.1; NOTES item 5)
def load_decisions(run_dir: Path) -> list[dict[str, Any]]:
    """decisions.csv rows (DECISION_COLUMNS, file order); [] if absent."""
    return read_records_if_exists(Path(run_dir) / "decisions.csv")


def append_decision(run_dir: Path, gate: str, phase: str, outcome: str, reason: str,
                    input_hash: str | None = None, check_id: str | None = None,
                    decided_by: str | None = None, answer_text: str | None = None, attempt: int | None = None,
                    supersedes: int | None = None, gate_request: str | None = None,
                    category: str | None = None) -> dict[str, Any]:
    """Append one decisions.csv row (spec 2.1); `check_id` ties a G8 reason or G4 accept to its check.

    `category` is the G4 accept category (G4_CATEGORIES), empty for every other row.

    `answer_text` is the human's answer verbatim; `attempt` the run state's attempt; `supersedes` the 1-based
    row number a --supersede row replaces; `gate_request` the archived gates/NN_<gate>_request.json it answers.
    Older files with fewer columns are read and rewritten with the full column set (missing values empty).
    """
    rows = load_decisions(run_dir)
    row = {"gate": gate, "phase": phase, "input_hash": input_hash, "outcome": outcome, "reason": reason,
           "time_utc": utc_now_text(), "check_id": check_id, "decided_by": decided_by, "answer_text": answer_text,
           "attempt": attempt, "supersedes": supersedes, "gate_request": gate_request, "category": category}
    names = [n for n, _ in DECISION_COLUMNS]
    allrows = [{n: (r.get(n) if n != "time_utc" else (r.get("time_utc") or r.get("time"))) for n in names}
               for r in rows] + [row]
    write_records(Path(run_dir) / "decisions.csv", DECISION_COLUMNS, [tuple(r.get(n) for n in names) for r in allrows])
    return row


# ---------------------------------------------------------------- cards (YAML front matter)
_FRONT_OPEN = "---\n"


def card_fields(path: Path) -> dict[str, Any]:
    """Structured fields of a card: the YAML front matter of 01_question_card.md / 02_metric_card.md."""
    import yaml  # local import: only card readers need it

    p = Path(path)
    if not p.is_file():
        raise InputMissingError(f"card missing: {p}")
    text = p.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    end = text.find("\n---", len(_FRONT_OPEN)) if text.startswith(_FRONT_OPEN) else -1
    if end < 0:
        raise RefusedError(f"{p.name}: no YAML front matter (--- ... ---) with the card fields")
    data = yaml.safe_load(text[len(_FRONT_OPEN):end]) or {}
    if not isinstance(data, dict):
        raise RefusedError(f"{p.name}: front matter is not a mapping")
    return data


# ---------------------------------------------------------------- checks_manifest.csv and spec 6.4 (NOTES item 6)
def contract_grain(oc: Any) -> list[str]:
    """Fix r14: the output contract's grouping columns (`grain`, non-empty names), else []."""
    g = oc.get("grain") if isinstance(oc, Mapping) else None
    return [str(x).strip() for x in g if isinstance(x, str) and x.strip()] if isinstance(g, list) else []


def weight_grain_hash(grain: Sequence[str]) -> str:
    """Fix r15 L2: what a G1b `confirmed` row (aggregation_weight not_applicable on a grouped output) is bound to: its
    input_hash is this hash of the grain it was given for, so a changed grain needs a new confirmation."""
    return sha256_obj({"aggregation_weight": "not_applicable", "grain": [str(g) for g in grain]})


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, float) and v != v) or str(v).strip() == ""


SHARED_FILE_FAMILIES = frozenset({"step_check"})  # P6 step checks share run_steps_checks.csv: the row is the evidence


def evidence_digest(check_id: Any, attempt: Any, status: Any, detail: Any, file_sha256: Any = None) -> str:
    """Fix r4 C4-1: sha256 of the canonical envelope `check_id|attempt|status|detail|<file sha256 or "-">` (UTF-8, LF).

    The file part is the check file's sha256 when the check has a file of its own, else "-"."""
    parts = ["" if _blank(x) else str(x) for x in (check_id, attempt, status)]
    parts.append("" if detail is None or (isinstance(detail, float) and detail != detail) else str(detail))
    parts.append("-" if _blank(file_sha256) else str(file_sha256).strip())
    return sha256_text("|".join(parts).replace("\r\n", "\n").replace("\r", "\n"))


def evidence_sha256(check: Mapping[str, Any]) -> str:
    """Fix r4 C4-1 (shared interface): the evidence a decision about this check binds to.

    Always the envelope digest of the row itself: check_id, attempt (a row without one counts as attempt 1), status,
    detail and the check file's sha256 (`csv_sha256`; "-" for a check without a file of its own, and for the P6 step
    checks, whose rows share run_steps_checks.csv). A stored `evidence_sha256` column is never trusted for a row that
    has a status: a file-backed check whose status, detail or attempt changes gets a new digest even when its file is
    unchanged. A bare evidence reference (a mapping without `status`, e.g. run_steps.g8_explained) passes its
    `evidence_sha256` through.
    """
    if "status" not in check:
        own = check.get("evidence_sha256")
        if not _blank(own):
            return str(own).strip()
    shared = str(check.get("family") or "") in SHARED_FILE_FAMILIES
    return evidence_digest(check.get("check_id"), row_attempt(check), check.get("status"), check.get("detail"),
                           None if shared else check.get("csv_sha256"))


def check_row(check_id: str, phase: str, family: str, status: str, csv_path: str | None = None,
              csv_sha256: str | None = None, detail: str = "", not_run_reason: str | None = None,
              required: bool | None = None, evidence_sha256: str | None = None) -> dict[str, Any]:
    """One checks_manifest.csv row (spec 6.3: each check writes a CSV and a manifest row).

    Fix r4 C4-1: `evidence_sha256` is the envelope digest (evidence_sha256 above), recomputed when upsert_checks /
    replace_phase_checks stamp the row's attempt (the digest names the attempt, the status, the detail and the file).
    """
    if status not in CHECK_RUN_STATUSES:
        raise ValueError(f"check status must be one of {CHECK_RUN_STATUSES}: {status!r}")
    ev = evidence_sha256 if not _blank(evidence_sha256) else None  # fix r4 C4-1: stamped with the attempt later
    return {"check_id": check_id, "phase": phase, "family": family, "status": status,
            "not_run_reason": not_run_reason, "required": required, "csv_path": csv_path,
            "csv_sha256": csv_sha256, "detail": detail, "created_utc": utc_now_text(), "evidence_sha256": ev}


def _with_evidence(r: dict[str, Any]) -> dict[str, Any]:
    r["evidence_sha256"] = evidence_sha256(r)  # fix r4 C4-1: always the envelope of the row as written
    return r


def _stamp_attempt(run_dir: Path, rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Fix r2 A2-H1: copy of `rows` with `attempt` set from run_state.json where a row has none.

    Fix r4 C4-1: then `evidence_sha256`, the envelope digest of the row with this attempt.
    """
    attempt = current_attempt(run_dir, None)
    out = [dict(r) for r in rows]
    for r in out:
        if r.get("attempt") is None:
            r["attempt"] = attempt
        _with_evidence(r)
    return out


def upsert_checks(run_dir: Path, rows: Sequence[Mapping[str, Any]]) -> Path:
    """Replace rows with the same check_id in checks/checks_manifest.csv; keep everyone else's rows.

    Each new row carries the run state's attempt (fix r2 A2-H1), so report.py can refuse rows of another attempt.
    """
    path = Path(run_dir) / "checks" / CHECKS_MANIFEST
    names = [n for n, _ in CHECK_COLUMNS]
    new_ids = {r["check_id"] for r in rows}
    kept = [_with_evidence(r) for r in read_records_if_exists(path) if r.get("check_id") not in new_ids]
    allrows = kept + _stamp_attempt(run_dir, rows)
    write_records(path, CHECK_COLUMNS, [tuple(r.get(n) for n in names) for r in allrows])
    return path


def replace_phase_checks(run_dir: Path, phase: str, rows: Sequence[Mapping[str, Any]]) -> Path:
    """Drop every checks_manifest.csv row of `phase`, then add `rows` (a re-run leaves no stale rows)."""
    path = Path(run_dir) / "checks" / CHECKS_MANIFEST
    names = [n for n, _ in CHECK_COLUMNS]
    kept = [_with_evidence(r) for r in read_records_if_exists(path) if str(r.get("phase")) != phase]
    allrows = kept + _stamp_attempt(run_dir, rows)
    write_records(path, CHECK_COLUMNS, [tuple(r.get(n) for n in names) for r in allrows])
    return path


def load_checks(run_dir: Path) -> list[dict[str, Any]]:
    """Rows of checks/checks_manifest.csv ([] when absent)."""
    return read_records_if_exists(Path(run_dir) / "checks" / CHECKS_MANIFEST)


# ---------------------------------------------------------------- attempt binding (fix r2 A2-H1)
def row_attempt(row: Mapping[str, Any]) -> int:
    """The attempt a decisions.csv or checks_manifest.csv row belongs to; a row without one counts as attempt 1."""
    v = row.get("attempt")
    try:
        return 1 if v is None or (isinstance(v, float) and v != v) or str(v).strip() == "" else int(v)
    except (TypeError, ValueError):
        return 1


def current_attempt(run_dir: Path, default: int | None = 1) -> int | None:
    """The run state's attempt (run_state.json); `default` when there is no valid state file."""
    try:
        return state.read_state(state.state_path(Path(run_dir))).attempt
    except state.StateError:
        return default


def decision_applies(row: Mapping[str, Any], check: Mapping[str, Any], attempt: int | None = None) -> bool:
    """Fix r3 A3-4 (shared interface, one rule): a decision row applies to a check only when its input_hash is
    non-empty and equals the check's evidence_sha256 (evidence_sha256 above, fix r4 C4-1: the digest of
    check_id|attempt|status|detail|<file sha256 or "-">). No same-attempt shortcut: a reason given for one failure never explains
    another, even in the same attempt, and a row without a hash never applies. `attempt` is accepted for older
    callers and ignored (the attempt is inside the digest)."""
    h = row.get("input_hash")
    if _blank(h):
        return False
    return str(h).strip() == evidence_sha256(check)


def steps_check_rows(run_dir: Path) -> list[dict[str, Any]]:
    """run_steps.py's per-step checks (checks/run_steps_checks.csv: job, check, status, detail[, attempt,
    evidence_sha256]) as P6 check rows `p6_<job>_<check>`; [] when the file is absent. Their evidence is the envelope
    of the row with "-" as the file part (the rows share one file; fix r4 C4-1)."""
    from lib import csv_contract as cc

    p = Path(run_dir) / "checks" / "run_steps_checks.csv"
    if not p.is_file():
        return []
    sha = cc.read_sidecar(p).csv_sha256
    out = []
    for r in read_records(p)[1]:
        st = str(r.get("status"))
        st = st if st in ("PASS", "FAIL") else "NOT_RUN"
        cid = "p6_" + "_".join(str(r.get(k) or "").replace(":", "_") for k in ("job", "check"))
        row = check_row(cid, "P6", "step_check", st, "checks/run_steps_checks.csv", sha, str(r.get("detail") or ""),
                        None if st != "NOT_RUN" else f"status {r.get('status')}",
                        evidence_sha256=None if _blank(r.get("evidence_sha256")) else str(r["evidence_sha256"]))
        row["attempt"] = None if _blank(r.get("attempt")) else row_attempt(r)
        out.append(row)
    return out


def find_check(run_dir: Path, check_id: str) -> dict[str, Any] | None:
    """The current row of `check_id`: checks/checks_manifest.csv first, then checks/run_steps_checks.csv."""
    for r in load_checks(run_dir):
        if str(r.get("check_id")) == str(check_id):
            return r
    return next((r for r in steps_check_rows(run_dir) if r["check_id"] == str(check_id)), None)


def steps_sha256(manifest: Mapping[str, Any]) -> str:
    """sha256 of the manifest steps' sql_sha256 values joined by LF (reconcile_status.json `steps_sha256`)."""
    return sha256_text("\n".join(str(s.get("sql_sha256") or "") for s in manifest.get("steps") or []))


def _explains(dec: Mapping[str, Any], check: Mapping[str, Any], attempt: int = 1) -> bool:
    """Fix r1 (A-H1, conductor): a reference check is explained by a G4 accept (or G8) naming it by check_id or by its
    csv sha256; every other check (P6 step checks, P8, P9) only by a G8 row whose check_id names it. A comparison
    row or an INCONCLUSIVE status is never explained. Fix r3 A3-4: and only by a row whose input_hash is the check's
    evidence_sha256 (`decision_applies`); `attempt` is kept for older callers."""
    if not decision_applies(dec, check, attempt):
        return False
    gate = str(dec.get("gate", "")).upper()
    family = str(check.get("family"))
    if str(check.get("status")) == "INCONCLUSIVE" or family == "comparison":
        return False
    if str(dec.get("outcome", "")).strip().lower() not in EXPLAINING_OUTCOMES:
        return False
    if not str(dec.get("reason") or "").strip():
        return False
    ref = dec.get("check_id") or dec.get("ref")
    named = bool(ref) and str(ref) == str(check.get("check_id"))
    if family == "reference":
        return gate in {"G4", "G8"}  # the evidence hash already ties the row to this reference result
    return gate == "G8" and named


def classify_checks(checks: Sequence[Mapping[str, Any]], required_checks: Sequence[str] | None,
                    decisions: Sequence[Mapping[str, Any]], expected: Sequence[str] = (),
                    attempt: int = 1) -> list[dict[str, Any]]:
    """Spec 6.4: every check ends PASS, FAIL-explained, FAIL-unexplained or NOT-RUN-at-level.

    `required_checks` is run_config.required_checks; None means the default, every D0 check of
    P6 to P9 that can run at the level: a row that says `required` true or false keeps its flag (the
    script that wrote it knows whether it could run); a row without the flag is required unless its
    status is NOT_RUN (it could not run at the level, so it is listed, not required); `expected` ids
    are required even without a row. A required check with no row at all never ran: FAIL-unexplained.
    A required check whose row is NOT_RUN did not run although it could (fix r1 A-H5, A-M4): also
    FAIL-unexplained, with its reason, until a G8 reason explains it.
    A FAIL is explained only by a G8 reason (or, for a reference check, a G4 accept) row in decisions.csv
    that names the check (check_id or ref column) or carries its csv sha256 as input_hash, with a reason.
    An INCONCLUSIVE status or a P7 comparison row is never explained. Fix r3 A3-4: a decision explains a check only
    when its input_hash is the check's evidence_sha256 (`decision_applies`); `attempt` is kept for older callers.
    """
    by_id = {str(c["check_id"]): c for c in checks}
    out: list[dict[str, Any]] = []
    if required_checks is None:
        req = set(expected)
        for i, c in by_id.items():
            flag = c.get("required")
            if flag is None or (isinstance(flag, float) and flag != flag):
                if str(c.get("phase")) in {"P6", "P7", "P8", "P9"} and c.get("status") != "NOT_RUN":
                    req.add(i)
            elif flag in (True, "true", "True", 1):
                req.add(i)
            else:
                req.discard(i)
    else:
        req = set(required_checks)
    for cid, c in by_id.items():
        status = str(c.get("status"))
        detail = c.get("detail") or ""
        if status == "PASS":
            final = "PASS"
        elif status == "NOT_RUN" and cid not in req:
            final = "NOT-RUN-at-level"
        else:
            if status == "NOT_RUN":
                detail = f"required, did not run: {c.get('not_run_reason') or detail or 'no reason recorded'}"
            final = "FAIL-explained" if any(_explains(d, c, attempt) for d in decisions) else "FAIL-unexplained"
        out.append({"check_id": cid, "phase": c.get("phase"), "family": c.get("family"),
                    "required": cid in req, "final": final, "detail": detail,
                    "not_run_reason": c.get("not_run_reason"), "status": status})
    for cid in sorted(req - set(by_id)):
        out.append({"check_id": cid, "phase": None, "family": None, "required": True,
                    "final": "FAIL-unexplained", "detail": "required check has no result (never ran)",
                    "not_run_reason": None})
    return out


def fail(msg: str) -> CheckFailedError:
    """A CheckFailedError (exit 1) with a message."""
    return CheckFailedError(msg)


# ---------------------------------------------------------------- trace result stamps (fix r5 addendum 8)
STAMP_KEYS = ("sql_sha256", "as_of_utc", "attempt")


def lookup_stamp(run_dir: Path, sql_text: str) -> dict[str, Any]:
    """The stamp a trace result carries: sha256 of the lookup's SQL file text, the run's as_of_utc
    (manifest.json pin) and the run state's attempt. warehouse_jobs.py writes it; trace.py accepts a result only when
    all three equal what they are now, so a rewrite (new attempt, new as-of) never reuses an old result."""
    import hashlib

    from lib import manifest as _manifest

    m = _manifest.load(Path(run_dir)) or {}
    return {"sql_sha256": hashlib.sha256(sql_text.encode("utf-8")).hexdigest(),
            "as_of_utc": (m.get("pin") or {}).get("as_of_utc"), "attempt": current_attempt(run_dir)}


def stamp_mismatch(run_dir: Path, result: Mapping[str, Any], sql_text: str) -> list[str]:
    """Fix r5 addendum 8: the stamp keys of a trace result that differ from the current lookup_stamp (all of them
    for an unstamped result)."""
    want = lookup_stamp(run_dir, sql_text)
    return [k for k in STAMP_KEYS if result.get(k) != want[k]]

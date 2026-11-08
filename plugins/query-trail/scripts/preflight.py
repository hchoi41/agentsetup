"""preflight.py: environment, run config, rights probe, caps, time-travel windows (design spec v3 section 2.2 P0; D-08).

Order and exit codes (spec 1.4, 2.2 P0; fix r2 B2-M1: offline checks first):
1. phase P0; run_config.json valid against the schema (else 2); the G0a approval block present (else
   gate_request.json G0a with the G0a questions and their value sets, exit 3; nothing is probed before G0a; fix r11
   L9: the request's details.caps carries both warehouse caps and the sentence "Budget caps for this run: <per job>
   per job, <per run> per run (run_config.json caps)" in human units, so Max sees what he approves);
2. offline checks, before any live probe: the caps rule (the per-job cap caps.warehouse_bytes_per_job must not
   exceed the per-run budget caps.warehouse_bytes_per_run), the env file (project, location), the interpreter
   (Python >= 3.12, imports duckdb, sqlglot, pandas, pyarrow, google-cloud-bigquery, google-auth) and the path
   length. A failure here refuses (exit 2; 4 for a missing interpreter or import) with the rule and the fix on
   stderr: "refused; no warehouse call was made; local effects: none (no attempt file, no 00_preflight.json)";
3. live: ADC present (else 4); identity: the env file's service account through impersonation, or owner mode
   only when run_config says `owner_mode: research` (labelled "owner mode, non-compliant with D-08, research
   only");
4. rights probe, fail closed (exit 2; spec v4 2.2 P0, fix r3 B3-1). The identity's grant is the boundary; the
   probe records an observation of it, not a proof. Three observations: tables.testIamPermissions on every table
   of every in-scope dataset (paged through, no cap; the API returns the requested permissions the caller holds on
   the table, inherited grants included) returns no write permission; a CREATE TABLE DRY RUN is refused (403) in
   every in-scope dataset (fix r6 H2: dry_run=True, so nothing is created and nothing billed; the plugin never
   submits a non-dry-run write statement; an accepted dry run is recorded write_possible and refuses, exit 2,
   "identity is not read-only"); projects.testIamPermissions is asked for bigquery.datasets.create, bigquery.datasets.update,
   bigquery.tables.create, bigquery.tables.update, bigquery.tables.updateData, bigquery.tables.delete and
   bigquery.jobs.create (each recorded; a mutation permission held fails), or recorded UNAVAILABLE when the Cloud
   Resource Manager API is off. The record (rights.observation.statement) is the observation sentence only: "On
   <time>, no write permission was returned by tables.testIamPermissions on N in-scope tables; CREATE TABLE dry
   run was refused for bigquery.tables.create in each of M in-scope datasets; project-level mutation permissions <tested: list (none held) |
   UNAVAILABLE (reason)>." A refusal reused from an earlier attempt is labelled "reused observation from <time>".
   No certification wording is written. A table listing that fails, or any failed table test, is UNVERIFIABLE
   (exit 2); a dataset with no table is recorded and skipped by the table test (dec r3 L8); no table at all in
   scope is UNVERIFIABLE. The write smoke runs once per identity per run: a later preflight attempt reuses an
   earlier attempt's refusal for the same identity (by sha256, D-12) and dataset unless --force-smoke. Every
   dataset's access list is read (unreadable: exit 2) as supplementary evidence: the identity listed with WRITER
   or OWNER fails; listed read-only passes that row; not listed directly (a group or project grant) is
   UNVERIFIED, never PASS. In owner mode the probe is recorded but does not fail, the write probe is skipped,
   and no observation sentence is written;
5. the time-travel window of every dataset (unreadable: 48 h, recorded as assumed);
6. self-test: CSV contract round trip (NULL vs "", Hangul) and streamed hash = CSV hash (else 1, G8).

Outputs: 00_preflight.json and 00_capability_probe.csv (the latest attempt) plus 00_preflight.attemptN.json and
00_capability_probe.attemptN.csv (every attempt kept). Exit 2 (fix r3 B3-4) means "refused"; stderr says which:
before any live probe "no warehouse call was made; local effects: none"; after live activity it lists the calls
made, the files written and the ledger entries added. Identifiers are not written (D-12): datasets are named by their
env placeholder when one exists; the identity only by sha256.

CLI: python -I -X utf8 -B scripts/preflight.py --run-dir RUN [--env-file F] [--force-smoke]
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import platform
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import runctx  # noqa: E402
from lib import refusal  # noqa: E402  fix r3 B3-4: exit-2 local effects on stderr
from lib.exit_codes import ExitCode, QueryTrailError, RefusedError  # noqa: E402

OUT_JSON = "00_preflight.json"
OUT_CSV = "00_capability_probe.csv"
MIN_WINDOW_HOURS = 48
PATH_BUDGET = 120  # longest relative artifact path a run writes (outputs/NN_<verb>_<object>.aggregates.csv + margin)
REQUIRED_IMPORTS = ("duckdb", "sqlglot", "pandas", "pyarrow", "google.cloud.bigquery", "google.auth")
PROJECT_MUTATIONS_TESTED = ("bigquery.datasets.create", "bigquery.datasets.update", "bigquery.tables.create",
                            "bigquery.tables.update", "bigquery.tables.updateData", "bigquery.tables.delete")
PROJECT_TESTED = PROJECT_MUTATIONS_TESTED + ("bigquery.jobs.create",)
OFFLINE_REFUSAL = "refused; no warehouse call was made; local effects: none (no attempt file, no 00_preflight.json)"
CAPS_RULE = "the per-job cap must not exceed the per-run budget"
# G0a questions and their value sets (fix r2 shared interface: decide.py G0a flags, package A)
G0A_QUESTIONS = ["plan route", "ZDR", "employer approval", "retention", "default level", "author mode"]
G0A_VALUE_SETS: dict[str, Any] = {
    "plan_route": ["system_time", "csv_only"], "zdr": ["yes", "no", "not_applicable"],
    "employer_approval": ["yes", "no", "not_applicable"],
    "retention_months": "a whole number of months; recorded as an override only when it differs from the default 6",
    "level": [60, 70, 80, 90, 100], "author_mode": ["single", "second:codex", "second:max"]}
ATTEMPT_FILE = re.compile(r"^00_preflight\.attempt(\d+)\.json$")


def human_bytes(n: Any) -> str:
    """Bytes in binary units for a person: 5368709120 -> '5 GiB', 1610612736 -> '1.5 GiB'."""
    try:
        v = float(n)
    except (TypeError, ValueError):
        return str(n)
    for unit, size in (("TiB", 1024 ** 4), ("GiB", 1024 ** 3), ("MiB", 1024 ** 2), ("KiB", 1024)):
        if v >= size:
            q = v / size
            return f"{q:.0f} {unit}" if q == int(q) else f"{q:.1f} {unit}"
    return f"{int(v)} bytes"


def g0a_caps(cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Fix r11 L9: the warehouse caps of run_config.json for the G0a request: both numbers and one sentence."""
    caps = cfg.get("caps") or {}
    job, per_run = caps.get("warehouse_bytes_per_job"), caps.get("warehouse_bytes_per_run")
    return {"warehouse_bytes_per_job": job, "warehouse_bytes_per_run": per_run,
            "sentence": f"Budget caps for this run: {human_bytes(job)} per job, {human_bytes(per_run)} per run "
                        "(run_config.json caps)"}


class Probe:
    """Collects capability rows and the worst exit code."""

    def __init__(self) -> None:
        self.rows: list[list[Any]] = []
        self.code = ExitCode.PASS
        self.reasons: list[str] = []

    def rec(self, check: str, status: str, detail: str = "", fail_code: ExitCode | None = None) -> None:
        self.rows.append([check, status, detail])
        if fail_code is not None and status in ("FAIL", "UNVERIFIABLE"):
            self.reasons.append(f"{check}: {detail}")
            if self.code == ExitCode.PASS or _rank(fail_code) > _rank(self.code):
                self.code = fail_code


def _rank(c: ExitCode) -> int:
    return {ExitCode.PASS: 0, ExitCode.FAIL: 1, ExitCode.REFUSED: 2, ExitCode.ENGINE_ERROR: 3}.get(c, 0)


def long_paths_enabled() -> bool:
    """Windows LongPathsEnabled registry value (non-Windows: True)."""
    if not sys.platform.startswith("win"):
        return True
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as k:
            return bool(winreg.QueryValueEx(k, "LongPathsEnabled")[0])
    except OSError:
        return False


def env_checks(p: Probe, run_dir: Path) -> dict[str, str]:
    """Python, imports, path length (offline); returns versions."""
    v = sys.version_info
    p.rec("python", "PASS" if v >= (3, 12) else "FAIL", platform.python_version(), ExitCode.ENGINE_ERROR)
    versions = {"python": platform.python_version()}
    for mod in REQUIRED_IMPORTS:
        try:
            m = importlib.import_module(mod)
            versions[mod] = str(getattr(m, "__version__", "?"))
            p.rec(f"import {mod}", "PASS", versions[mod])
        except ImportError as exc:
            p.rec(f"import {mod}", "FAIL", f"{type(exc).__name__}; fix: install it into the pinned interpreter (QT_PY)",
                  ExitCode.ENGINE_ERROR)
    try:
        importlib.import_module("google.cloud.bigquery_storage")
        p.rec("import google.cloud.bigquery_storage", "PASS", "Storage Read API client available")
    except ImportError:
        p.rec("import google.cloud.bigquery_storage", "WARN", "absent: results stream by REST pages")
    n = len(str(run_dir.resolve())) + PATH_BUDGET
    if n <= 259:
        p.rec("path length", "PASS", f"{n} <= 259")
    elif long_paths_enabled():
        p.rec("path length", "PASS", f"{n} > 259 but LongPathsEnabled")
    else:
        p.rec("path length", "FAIL", f"run folder path + {PATH_BUDGET} = {n} > 259 and long paths are off; fix: a "
              "shorter QT_HOME, or turn LongPathsEnabled on", ExitCode.REFUSED)
    return versions


def caps_check(p: Probe, cfg: Mapping[str, Any]) -> dict[str, int]:
    """Caps from run_config (offline): the per-job cap must not exceed the per-run budget."""
    caps = dict(cfg["caps"])
    job, per_run = caps["warehouse_bytes_per_job"], caps["warehouse_bytes_per_run"]
    if job <= per_run:
        p.rec("caps", "PASS", f"per job {job} <= per run {per_run} ({CAPS_RULE})")
    else:
        p.rec("caps", "FAIL", f"per-job cap caps.warehouse_bytes_per_job {job} exceeds the per-run budget "
              f"caps.warehouse_bytes_per_run {per_run}; rule: {CAPS_RULE}; fix: in run_config.json (before G1a) "
              f"lower caps.warehouse_bytes_per_job to at most {per_run}, or raise caps.warehouse_bytes_per_run to at "
              f"least {job}", ExitCode.REFUSED)
    return caps


def adc_check(p: Probe, project: str) -> bool:
    """ADC present (google.auth.default succeeds)."""
    from lib import bq
    try:
        bq.source_credentials(project)
        p.rec("adc", "PASS", "application default credentials found")
        return True
    except QueryTrailError as exc:
        p.rec("adc", "FAIL", str(exc)[:200], ExitCode.ENGINE_ERROR)
        return False


def dataset_names(env: Mapping[str, Any]) -> list[tuple[str, str]]:
    """(dataset id, name used in files): the placeholder form {{name}} when the env maps one to the id."""
    rev = {v: k for k, v in (env.get("placeholders") or {}).items() if k != "project"}
    return [(d, "{{" + rev[d] + "}}" if d in rev else d) for d in env.get("datasets") or []]


OBSERVATION_BASIS = ("the identity's grant is the boundary; this is an observation of it, not a proof: "
                     "tables.testIamPermissions on every in-scope table, paged through without a cap (the requested "
                     "permissions the caller holds, inherited grants included: "
                     "https://cloud.google.com/bigquery/docs/reference/rest/v2/tables/testIamPermissions); a CREATE "
                     "TABLE dry-run write probe (dry_run=True: nothing created, nothing billed) in every in-scope "
                     "dataset; projects.testIamPermissions on " + ", ".join(PROJECT_TESTED)
                     + " (or UNAVAILABLE); the dataset access lists are supplementary evidence")


def identity_sha256(engine: Any, env: Mapping[str, Any], owner: bool) -> str:
    """sha256 of the identity the jobs run as (never the identity itself, D-12)."""
    mode = "owner" if owner else "impersonated"
    who = "" if owner else (getattr(engine, "principal", None) or "")
    return hashlib.sha256(f"{mode}|{who}|{env.get('project', '')}".encode("utf-8")).hexdigest()


def prior_smokes(run_dir: Path, ident: str) -> dict[str, dict[str, str]]:
    """dataset name -> {file, observed_utc} of the earlier refusal of the write smoke for this identity."""
    found: dict[str, dict[str, str]] = {}
    files = sorted(((int(m.group(1)), f) for f in run_dir.glob("00_preflight.attempt*.json")
                    if (m := ATTEMPT_FILE.match(f.name))), reverse=True)
    for _, f in files:
        try:
            doc = runctx.read_json(f)
            ws = (doc.get("rights") or {}).get("write_smoke") or {}
        except (OSError, ValueError, AttributeError):
            continue
        if not isinstance(ws, Mapping) or ws.get("identity_sha256") != ident:
            continue
        for d in ws.get("datasets") or []:
            if d.get("status") == "PASS" and d.get("dataset") not in found:
                found[d["dataset"]] = {"file": d.get("reused_from") or f.name,
                                       "observed_utc": str(d.get("observed_utc") or doc.get("checked_utc") or "unknown")}
    return found


def write_smokes(p: Probe, engine: Any, datasets: list[tuple[str, str]], cfg: Mapping[str, Any],
                 env: Mapping[str, Any], ident: str, reuse: Mapping[str, Mapping[str, str]]) -> dict[str, Any]:
    """A CREATE TABLE dry run in every in-scope dataset, each refused (403); earlier refusals for this identity reused.
    Fix r6 H2: mode dry_run only; an accepted dry run is write_possible, the row FAILs ("identity is not read-only")."""
    from lib import bq
    out: dict[str, Any] = {"status": "PASS", "mode": "dry_run", "write_possible": False, "identity_sha256": ident,
                           "datasets": []}
    worst = {"PASS": 0, "FAIL": 1, "UNVERIFIABLE": 2}
    for ds_id, ds_name in datasets:
        if ds_name in reuse:
            row = {"dataset": ds_name, "status": "PASS", "detail": "reused observation from "
                   f"{reuse[ds_name]['observed_utc']} (an earlier preflight attempt of this run, same identity)",
                   "reused_from": reuse[ds_name]["file"], "observed_utc": reuse[ds_name]["observed_utc"]}
        else:
            jc = bq.job_config(cfg["run_id"], "preflight", min(cfg["caps"]["warehouse_bytes_per_job"], 10 * 1024 ** 2),
                               False, env["location"], cfg.get("job_labels"))
            try:
                refused, detail = engine.write_smoke(ds_id, jc)
                row = {"dataset": ds_name, "status": "PASS" if refused else "FAIL", "write_possible": not refused,
                       "detail": detail if refused else f"identity is not read-only: {detail}",
                       "observed_utc": runctx.fmt_utc(runctx.utc_now())}
                out["write_possible"] = out["write_possible"] or not refused
            except QueryTrailError as exc:
                row = {"dataset": ds_name, "status": "UNVERIFIABLE", "detail": str(exc)}
        out["datasets"].append(row)
        p.rec(f"write-refusal smoke {ds_name}", row["status"],
              row["detail"] + (f" ({row['reused_from']})" if row.get("reused_from") else ""),
              None if row["status"] == "PASS" else ExitCode.REFUSED)
        if worst[row["status"]] > worst[out["status"]]:
            out["status"] = row["status"]
    n_reused = sum(bool(d.get("reused_from")) for d in out["datasets"])
    p.rec("write-refusal smoke", out["status"], f"{len(datasets)} in-scope datasets, CREATE TABLE dry run refused in "
          f"{sum(d['status'] == 'PASS' for d in out['datasets'])}" + (f" ({n_reused} reused from an earlier attempt)"
                                                                    if n_reused else ""))
    return out


def observation_statement(observed_utc: str, n_tables: int, smoke: Mapping[str, Any],
                          project_test: Mapping[str, Any] | None, skipped: Sequence[str] = ()) -> str:
    """The observation sentence, the only rights wording written (fix r3 B3-1; aera r3 H1). No certification."""
    rows = list(smoke.get("datasets") or [])
    reused = [f"{d['dataset']}: reused observation from {d.get('observed_utc') or 'unknown'}" for d in rows
              if d.get("reused_from")]
    pt = project_test or {}
    if pt.get("status") == "UNAVAILABLE":
        proj = f"UNAVAILABLE ({pt.get('reason')})"
    else:
        proj = "tested: " + ", ".join(PROJECT_MUTATIONS_TESTED) + " (none held)"
    out = (f"On {observed_utc}, no write permission was returned by tables.testIamPermissions on {n_tables} in-scope "
           f"tables; CREATE TABLE dry run was refused for bigquery.tables.create in each of {len(rows)} in-scope datasets"
           + (f" ({'; '.join(reused)})" if reused else "") + f"; project-level mutation permissions {proj}.")
    if skipped:
        out += f" Datasets with no table (table test skipped): {', '.join(skipped)}."
    return out


def _project_test(p: Probe, engine: Any, fail: ExitCode | None) -> dict[str, Any]:
    """projects.testIamPermissions: every mutation and read permission asked; PROJECT_TESTED recorded each (r3 B3-1)."""
    from lib import bq
    try:
        held = engine.project_permissions(bq.WRITE_PERMISSIONS + bq.READ_PERMISSIONS)
    except bq.RightsUnverifiable as exc:
        p.rec("projects.testIamPermissions", "UNAVAILABLE", f"{exc}; recorded: {', '.join(PROJECT_TESTED)} not tested")
        return {"status": "UNAVAILABLE", "reason": str(exc), "tested": {perm: "UNAVAILABLE" for perm in PROJECT_TESTED}}
    writes = sorted(set(held) & set(bq.WRITE_PERMISSIONS))
    tested = {perm: perm in held for perm in PROJECT_TESTED}
    listing = ", ".join(f"{k} {'held' if v else 'not held'}" for k, v in tested.items())
    p.rec("projects.testIamPermissions", "FAIL" if writes else "PASS",
          f"write permissions held: {writes}" if writes else f"no write permission at project level ({listing})", fail)
    if "bigquery.jobs.create" not in held:
        p.rec("bigquery.jobs.create", "FAIL", "the identity cannot run query jobs", ExitCode.REFUSED)
    return {"status": "FAIL" if writes else "PASS", "write_permissions": writes, "tested": tested,
            "jobs_create": "bigquery.jobs.create" in held}


def rights_probe(p: Probe, engine: Any, env: Mapping[str, Any], cfg: Mapping[str, Any], owner: bool,
                 run_dir: Path, force_smoke: bool = False) -> dict[str, Any]:
    """The P0 rights probe; fills `p` and returns the rights block of 00_preflight.json."""
    from lib import bq
    fail = None if owner else ExitCode.REFUSED
    out: dict[str, Any] = {"mode": "owner" if owner else "impersonated", "project_test": None,
                           "table_test": [], "tables_listed": {}, "access_lists": [], "write_smoke": None,
                           "observation": {"basis": OBSERVATION_BASIS, "status": "UNVERIFIABLE",
                                           "table_test": "UNVERIFIABLE", "write_smoke": None, "tables_tested": 0,
                                           "datasets_without_tables": [], "observed_utc": None, "statement": None}}
    datasets = dataset_names(env)
    if not datasets:
        p.rec("datasets in scope", "UNVERIFIABLE", "the env file lists no datasets", ExitCode.REFUSED)
        return out
    out["project_test"] = _project_test(p, engine, fail)
    principal = (engine.principal or "").lower()
    table_status = "PASS"  # worst of the per-dataset table tests: PASS < FAIL < UNVERIFIABLE
    rank = {"PASS": 0, "FAIL": 1, "UNVERIFIABLE": 2}

    def table_result(status: str) -> None:
        nonlocal table_status
        if rank[status] > rank[table_status]:
            table_status = status

    for ds_id, ds_name in datasets:
        try:
            entries = engine.dataset_access(ds_id)
        except bq.RightsUnverifiable as exc:
            p.rec(f"access list {ds_name}", "UNVERIFIABLE", str(exc), ExitCode.REFUSED)
            entries = None
        if entries is not None:
            mine = [e.get("role") for e in entries if principal and any(str(e.get(k, "")).lower().endswith(principal)
                                                                        for k in ("userByEmail", "iamMember"))]
            bad = [r for r in mine if str(r).upper() in ("WRITER", "OWNER", "ROLES/BIGQUERY.DATAEDITOR",
                                                         "ROLES/BIGQUERY.DATAOWNER", "ROLES/BIGQUERY.ADMIN")]
            status = "FAIL" if bad else ("PASS" if mine else "UNVERIFIED")
            out["access_lists"].append({"dataset": ds_name, "identity_roles": mine, "entries": len(entries),
                                        "status": status})
            p.rec(f"access list {ds_name}", status,
                  f"identity roles {mine}" if mine else "identity not listed directly (a project or group grant "
                  "cannot be read here; supplementary evidence only)", fail if bad else None)
        try:
            listed = list(engine.list_tables(ds_id))  # bq.list_tables pages through every table (no cap)
        except bq.RightsUnverifiable as exc:
            p.rec(f"tables of {ds_name}", "UNVERIFIABLE", f"{exc}: tables.testIamPermissions not listable",
                  ExitCode.REFUSED)
            table_result("UNVERIFIABLE")
            continue
        out["tables_listed"][ds_name] = len(listed)
        if not listed:  # recorded and skipped, not refused (dec r3 L8); the write smoke still runs there
            p.rec(f"tables.testIamPermissions {ds_name}", "SKIPPED", "no table in the dataset: nothing to test")
            out["observation"]["datasets_without_tables"].append(ds_name)
            continue
        n_write, broke = 0, False
        for t in listed:
            try:
                held_t = engine.table_permissions(ds_id, t, bq.TABLE_WRITE_PERMISSIONS + ("bigquery.tables.getData",))
            except bq.RightsUnverifiable as exc:
                p.rec(f"tables.testIamPermissions {ds_name}", "UNVERIFIABLE", str(exc), ExitCode.REFUSED)
                table_result("UNVERIFIABLE")
                broke = True
                break
            w = sorted(set(held_t) & set(bq.TABLE_WRITE_PERMISSIONS))
            n_write += bool(w)
            out["table_test"].append({"dataset": ds_name, "table_index": len(out["table_test"]) + 1,
                                      "write_permissions": w, "get_data": "bigquery.tables.getData" in held_t})
        if broke:
            continue
        p.rec(f"tables.testIamPermissions {ds_name}", "FAIL" if n_write else "PASS",
              f"{n_write} of {len(listed)} tables grant a write permission" if n_write else
              f"{len(listed)} tables, no write permission (effective permissions)", fail if n_write else None)
        table_result("FAIL" if n_write else "PASS")
    cert = out["observation"]
    if not out["table_test"] and table_status == "PASS":
        p.rec("tables.testIamPermissions", "UNVERIFIABLE", "no table in any in-scope dataset: nothing to observe",
              fail)
        table_status = "UNVERIFIABLE"
    cert["table_test"], cert["tables_tested"] = table_status, len(out["table_test"])
    if owner:
        out["write_smoke"] = {"status": "SKIPPED", "mode": "dry_run", "reason": "owner mode: a write would succeed",
                              "datasets": []}
        p.rec("write-refusal smoke", "SKIPPED", "owner mode")
        cert.update(status="SKIPPED", write_smoke="SKIPPED")
        return out
    ident = identity_sha256(engine, env, owner)
    out["write_smoke"] = write_smokes(p, engine, datasets, cfg, env, ident,
                                      {} if force_smoke else prior_smokes(run_dir, ident))
    smoke = out["write_smoke"]["status"]
    cert["write_smoke"] = smoke
    project = out["project_test"] or {}
    if table_status == "PASS" and smoke == "PASS" and project.get("status") in ("PASS", "UNAVAILABLE"):
        cert["status"] = "PASS"
        cert["observed_utc"] = runctx.fmt_utc(runctx.utc_now())
        cert["statement"] = observation_statement(cert["observed_utc"], len(out["table_test"]), out["write_smoke"],
                                                  project, cert["datasets_without_tables"])
    elif "UNVERIFIABLE" in (table_status, smoke):
        cert["status"] = "UNVERIFIABLE"
    else:
        cert["status"] = "FAIL"
    p.rec("rights observation", cert["status"],
          cert["statement"] or f"table test {table_status}, write smoke {smoke}, project test {project.get('status')}",
          None if cert["status"] == "PASS" else ExitCode.REFUSED)
    return out


def windows(p: Probe, engine: Any, env: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Time-travel window per dataset; unreadable = 48 h assumed (spec 2.2 P0)."""
    out = []
    for ds_id, ds_name in dataset_names(env):
        h = engine.time_travel_hours(ds_id)
        assumed = h is None
        out.append({"dataset": ds_name, "time_travel_hours": h if h else MIN_WINDOW_HOURS, "assumed": assumed})
        p.rec(f"time travel {ds_name}", "ASSUMED" if assumed else "PASS",
              f"{MIN_WINDOW_HOURS} h assumed (window unreadable)" if assumed else f"{h} h")
    return out


def self_test(p: Probe) -> dict[str, Any]:
    """CSV contract round trip and hash agreement (exit 1 on a surprise)."""
    import duckdb
    from lib import canon, csv_contract as cc
    with tempfile.TemporaryDirectory() as td:
        con = duckdb.connect()
        sql = "SELECT * FROM (VALUES (1, NULL, ''), (2, '김철수', 'a,\"b\"'), (2, '김철수', 'a,\"b\"')) t(k, s, e)"
        try:
            cc.write_query_csv(con, sql, Path(td) / "t.csv")
            rows = cc.read_csv(con, Path(td) / "t.csv").order("k").fetchall()
            same = canon.digest_query(con, sql).digest == canon.digest_csv(con, Path(td) / "t.csv").digest
            ok = rows[0][1] is None and rows[0][2] == "" and rows[1][1] == "김철수" and same and len(rows) == 3
        except Exception as exc:  # noqa: BLE001 - a self-test surprise is reported, not raised
            ok, rows, same = False, [], str(exc)
        finally:
            con.close()
    p.rec("self-test csv contract and hash", "PASS" if ok else "FAIL",
          "NULL vs '' kept, Hangul kept, streamed hash = CSV hash" if ok else f"surprise: {same}", ExitCode.FAIL)
    return {"status": "PASS" if ok else "FAIL"}


def next_attempt(run_dir: Path) -> int:
    """1 + the highest preflight attempt file number (every attempt keeps its file, fix r2 B2-M1)."""
    nums = [int(m.group(1)) for f in run_dir.glob("00_preflight.attempt*.json") if (m := ATTEMPT_FILE.match(f.name))]
    return max(nums, default=0) + 1


def _ledger_len(run_dir: Path) -> int:
    from lib import manifest
    m = manifest.load(run_dir) or {}
    return len((m.get("budget") or {}).get("ledger") or [])


def main(argv: Sequence[str] | None = None, engine: Any = None, skip_adc: bool = False) -> int:
    ap = argparse.ArgumentParser(prog="preflight.py", description="query-trail preflight (spec v3 2.2 P0)")
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--env-file", default=None)
    ap.add_argument("--force-smoke", action="store_true",
                    help="run the CREATE TABLE dry run again although an earlier attempt of this run saw it refused for "
                         "this identity")
    a = ap.parse_args(argv)

    def body() -> int:
        st = runctx.enter(a.run_dir, ("P0",))
        cfg = runctx.load_run_config(a.run_dir)
        if not runctx.has_g0a(cfg):
            caps = g0a_caps(cfg)  # fix r11 L9: the caps Max approves at G0a, in human units
            runctx.write_gate_request(a.run_dir, "G0a", "P0", "preflight.py", "G0a use approval not recorded",
                                      {"questions": G0A_QUESTIONS, "value_sets": G0A_VALUE_SETS, "caps": caps})
            print("preflight.py: NEEDS_DECISION: G0a approval block missing from run_config.json (nothing probed); "
                  + caps["sentence"])
            return int(ExitCode.NEEDS_DECISION)
        # offline checks first (fix r2 B2-M1): config schema (above), caps rule, env file, interpreter, path length
        p = Probe()
        report: dict[str, Any] = {"schema_version": "1", "checked_utc": runctx.fmt_utc(runctx.utc_now())}
        report["caps"] = caps_check(p, cfg)
        env = runctx.load_env(cfg, a.env_file)
        versions = env_checks(p, a.run_dir)
        if p.code != ExitCode.PASS:
            sys.stderr.write(f"preflight.py: {ExitCode(p.code).name} (exit {int(p.code)}) before any live probe: "
                             f"{OFFLINE_REFUSAL}\n")
            for r in p.reasons:
                sys.stderr.write("  " + r + "\n")
            return int(p.code)
        owner = cfg.get("owner_mode") == "research"
        report.update({"versions": versions, "owner_mode": cfg.get("owner_mode"),
                       "identity": "owner mode, non-compliant with D-08, research only" if owner
                       else "impersonated service account (its grant is the boundary; rights.observation records "
                            "what P0 observed)",
                       "location": env["location"]})
        attempt = next_attempt(a.run_dir)
        report["preflight_attempt"] = attempt
        ledger_before = _ledger_len(a.run_dir)
        live = False
        eng = engine
        if skip_adc or engine is not None or adc_check(p, env["project"]):
            if eng is None:
                from lib import bq
                try:
                    eng = bq.BigQueryEngine(bq.Identity(env["project"], env["location"], env.get("service_account"),
                                                        owner))
                    p.rec("identity", "PASS", report["identity"])
                except RefusedError as exc:
                    p.rec("identity", "FAIL", str(exc), ExitCode.REFUSED)
                except QueryTrailError as exc:
                    p.rec("identity", "FAIL", str(exc), ExitCode.ENGINE_ERROR)
        if eng is not None and p.code == ExitCode.PASS:
            from lib import bq
            live = True
            eng = bq.metered(eng, a.run_dir, cfg, "P0", st.attempt)  # the write smoke goes on the budget ledger
            report["rights"] = rights_probe(p, eng, env, cfg, owner, a.run_dir, a.force_smoke)
            report["datasets"] = windows(p, eng, env)
        report["self_test"] = self_test(p)
        report["status"] = "PASS" if p.code == ExitCode.PASS else "FAIL"
        report["exit_code"] = int(p.code)
        report["fail_reasons"] = p.reasons
        written = [OUT_JSON, f"00_preflight.attempt{attempt}.json", OUT_CSV, f"00_capability_probe.attempt{attempt}.csv"]
        for name in (written[0], written[1]):
            runctx.write_json(a.run_dir / name, report)
        for name in (written[2], written[3]):
            runctx.write_rows_csv(a.run_dir / name, ["check", "status", "detail"], p.rows)
        print(f"preflight.py: {report['status']} (exit {int(p.code)}); {len(p.rows)} checks -> {OUT_JSON} "
              f"(preflight attempt {attempt})")
        for r in p.reasons:
            print("  " + r)
        if p.code == ExitCode.REFUSED and live:
            added = _ledger_len(a.run_dir) - ledger_before
            rights = report.get("rights") or {}
            smokes = [d for d in (rights.get("write_smoke") or {}).get("datasets") or [] if not d.get("reused_from")]
            ran = [f"identity and rights probe ({len(rights.get('table_test') or [])} table tests, {len(smokes)} write "
                   "smokes)", "time-travel windows", "self-test"]
            files = written + (["manifest.json (budget.ledger)"] if added else [])
            sys.stderr.write(f"preflight.py: refused after live activity; warehouse calls made: {', '.join(ran)}; "
                             f"local effects: written {', '.join(files)}; ledger entries: {added}\n")
        if p.code == ExitCode.PASS:
            runctx.archive_own_request(a.run_dir, "preflight.py", ("G0a",), "P0")  # fix r4 C4-10
        return int(p.code)

    return runctx.run_cli(lambda: refusal.guard(a.run_dir, body, warehouse=True), "preflight.py")


if __name__ == "__main__":
    sys.exit(main())

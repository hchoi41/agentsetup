"""manifest.json skeleton, validation and file entries (design spec v1 sections 2.2 P4 and P6, 4.2, 5.5, 7.1).

The manifest is validated against config/manifest.schema.json on every save (lib.schema_check), so a
script can never leave an invalid manifest. Independence levels follow spec 4.2 and decisions section 4:
single = IND-0; second:codex = IND-3 with isolation_attested_not_enforced (native Windows, T14);
second:max = IND-4; an informed re-author after a G4 reject is a dependent revision (IND-0).
"""
from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import Any, Mapping

from lib import runctx, schema_check
from lib.csv_contract import Sidecar, sha256_file
from lib.exit_codes import RefusedError

MANIFEST = "manifest.json"
SCHEMA_PATH = runctx.CONFIG_DIR / "manifest.schema.json"


class ManifestError(RefusedError):
    """A manifest that fails its schema (exit 2: nothing is written)."""


def independence(author_mode: str, dependent_revision: bool = False) -> dict[str, Any]:
    """The manifest independence block for an author mode."""
    if dependent_revision:
        return {"level": "IND-0", "dependent_revision": True}
    if author_mode == "second:codex":
        return {"level": "IND-3", "isolation_attested_not_enforced": True}
    if author_mode == "second:max":
        return {"level": "IND-4"}
    return {"level": "IND-0"}


def versions() -> dict[str, str]:
    """Library versions recorded in the manifest engine block and the compat binding."""
    import duckdb
    import pandas
    import sqlglot

    out = {"duckdb_version": duckdb.__version__, "sqlglot_version": sqlglot.__version__,
           "pandas_version": pandas.__version__, "python_version": platform.python_version()}
    try:
        from google.cloud import bigquery
        out["client_version"] = bigquery.__version__
    except ImportError:  # pragma: no cover - the env has the client
        out["client_version"] = "missing"
    return out


def new_manifest(cfg: Mapping[str, Any], attempt: int, pin: Mapping[str, Any] | None = None,
                 dependent_revision: bool = False) -> dict[str, Any]:
    """A valid manifest with no steps, deliverables or files yet."""
    contract = Sidecar((), 0, "0" * 64, 0).to_json()["contract"]
    contract["writer"] = "arrow via duckdb COPY"
    p = dict(pin or {"route": "csv_only", "pin_status": "PROVISIONAL",
                     "provisional_reasons": ["no pin plan when the manifest was created"]})
    m = {"schema_version": "1", "run_id": cfg["run_id"], "attempt": int(attempt),
         "created_utc": runctx.fmt_utc(runctx.utc_now()), "confidence_level": int(cfg["confidence_level"]),
         "author_mode": cfg.get("author_mode", "single"), "owner_mode": cfg.get("owner_mode"),
         "independence": independence(cfg.get("author_mode", "single"), dependent_revision),
         "engine": {"name": "bigquery", "transport": "python_client_adc", "chaining": "cumulative_cte",
                    "project_ref_stored": False, **versions()},
         "csv_contract": {k: contract[k] for k in ("encoding", "bom", "line_ending", "quoting", "null_token",
                                                    "empty_string_token", "writer", "reader")},
         "pin": p, "steps": [], "deliverables": [], "files": []}
    return m


def pin_block(pin_plan: Mapping[str, Any] | None) -> dict[str, Any]:
    """Manifest pin block from 15_pin_plan.json."""
    if not pin_plan:
        return {"route": "csv_only", "pin_status": "PROVISIONAL", "provisional_reasons": ["no pin plan"]}
    return {"route": pin_plan["route"], "pin_status": pin_plan["pin_status"],
            "as_of_utc": pin_plan.get("as_of_utc"), "pin_deadline_utc": pin_plan.get("pin_deadline_utc"),
            "smallest_window_hours": pin_plan.get("smallest_window_hours"),
            "business_pin": pin_plan.get("business_pin"), "as_of_group_id": pin_plan.get("as_of_group_id"),
            "unpinnable": [u["ref"] if isinstance(u, Mapping) else str(u) for u in pin_plan.get("unpinnable", [])],
            "provisional_reasons": list(pin_plan.get("provisional_reasons", []))}


def validate(m: Mapping[str, Any]) -> list[str]:
    """Schema violations of a manifest."""
    return schema_check.validate(m, schema_check.load_schema(SCHEMA_PATH))


def load(run_dir: Path) -> dict[str, Any] | None:
    """manifest.json or None."""
    p = Path(run_dir) / MANIFEST
    return runctx.read_json(p) if p.is_file() else None


def save(run_dir: Path, m: Mapping[str, Any]) -> None:
    """Validate, then write manifest.json; refuse an invalid manifest."""
    errs = validate(m)
    if errs:
        raise ManifestError("manifest fails manifest.schema.json: " + "; ".join(errs[:8]))
    runctx.write_json(Path(run_dir) / MANIFEST, m)


def _birth_time(st: Any) -> float | None:
    """The file's birth (creation) time where the OS reports one (Windows, macOS, some Linux), else None."""
    v = getattr(st, "st_birthtime", None)
    return float(v) if v else None


CREATED_BASES = ("generated", "inferred")


def file_entry(run_dir: Path, rel: str, kind: str, cfg: Mapping[str, Any], now: Any = None,
               generated_utc: Any = None) -> dict[str, Any]:
    """files[] entry: path, sha256, bytes, kind, created_utc, created_basis, registered_utc, retention_until
    (spec v4 7.1; fix r3 B3-3, aera r3 M1).

    created_basis says where created_utc comes from:
    - `generated`: the script that wrote the file passes `generated_utc`, the time it took when it wrote it
      (taken just before the write, so it never postdates the file); created_utc is that time.
    - `inferred`: a file registered from elsewhere, or written earlier by another step: created_utc is the earliest
      of the file's birth time (where the OS gives one), its modification time and the registration time. This is
      an inference from filesystem timestamps, not creation provenance: an old file modified just before its first
      registration, or a copy that reset its timestamps, gets a recent date; it only guarantees that a late
      registration, a later modification or a future mtime does not extend retention.
    registered_utc is the registration time. upsert_files keeps created_utc, created_basis, registered_utc and
    retention_until once an entry for the path exists.
    """
    import datetime as dt

    p = Path(run_dir) / rel
    st = p.stat()
    reg = (now or runctx.utc_now()).astimezone(dt.timezone.utc).replace(microsecond=0)
    if generated_utc is not None:
        created, basis = generated_utc.astimezone(dt.timezone.utc).replace(microsecond=0), "generated"
    else:
        stamps = [st.st_mtime, reg.timestamp()] + ([_birth_time(st)] if _birth_time(st) else [])
        created = dt.datetime.fromtimestamp(min(stamps), tz=dt.timezone.utc).replace(microsecond=0)
        basis = "inferred"
    return {"path": rel.replace("\\", "/"), "sha256": sha256_file(p), "bytes": st.st_size, "kind": kind,
            "created_utc": runctx.fmt_utc(created), "created_basis": basis, "registered_utc": runctx.fmt_utc(reg),
            "retention_until": runctx.retention_until(created, cfg)}


IMMUTABLE_FILE_FIELDS = ("created_utc", "created_basis", "registered_utc", "retention_until")


def upsert_files(m: dict[str, Any], entries: list[dict[str, Any]]) -> None:
    """Replace files[] entries by path, keep the rest; created_utc, created_basis, registered_utc and retention_until
    of an existing entry never change (fix r2 B2-M6: the retention clock runs from the first registration)."""
    by = {f["path"]: f for f in m.get("files", [])}
    for e in entries:
        old = by.get(e["path"])
        new = dict(e)
        if old:
            for k in IMMUTABLE_FILE_FIELDS:
                if old.get(k):
                    new[k] = old[k]
                elif k == "created_basis" and old.get("created_utc"):
                    new[k] = "inferred"  # an entry written before fix r3 had no basis: its date was inferred
        by[e["path"]] = new
    m["files"] = sorted(by.values(), key=lambda f: f["path"])


def platform_is_windows() -> bool:
    """IND-3 is attested, not enforced, on native Windows (spec 4.2)."""
    return sys.platform.startswith("win")


# ---------------------------------------------------------------- seal (NOTES_stage2b item 7; spec 7.2)
SEAL = "manifest.sha256"


def seal(run_dir: Path) -> str:
    """Write manifest.sha256 = the current sha256 of manifest.json (P11, report.py); return it."""
    from lib.csv_contract import write_text_lf

    mp = Path(run_dir) / MANIFEST
    if not mp.is_file():
        raise ManifestError("manifest.json missing: nothing to seal")
    sha = sha256_file(mp)
    write_text_lf(Path(run_dir) / SEAL, f"{sha}  {MANIFEST}\n")
    return sha


def verify_seal(run_dir: Path) -> str:
    """The manifest sha256 when manifest.sha256 seals it; RefusedError when missing or unsigned."""
    mp, sp = Path(run_dir) / MANIFEST, Path(run_dir) / SEAL
    if not mp.is_file():
        raise RefusedError("manifest.json missing: close deletes by manifest list only")
    if not sp.is_file():
        raise RefusedError("manifest is unsigned: manifest.sha256 missing (report.py writes it at P11)")
    parts = sp.read_text(encoding="utf-8").split()
    got = sha256_file(mp)
    if not parts or parts[0] != got:
        raise RefusedError("manifest is unsigned: manifest.sha256 does not match manifest.json")
    return got


# ---------------------------------------------------------------- P8 to P11 outputs (NOTES_stage2b item 8)
LATE_DIRS = ("samples", "trace", "checks")
LATE_TOP = ("report.md", "report_status.json", "trace_report.md", "renames.csv", "rename_report.json",
            "secret_scan_report.json")
NEVER_LISTED = frozenset({MANIFEST, SEAL, "deletions.log", "run_state.json", "gate_request.json"})


def late_outputs(run_dir: Path) -> list[str]:
    """Run-relative paths of the P7 to P11 outputs that exist (checks/, samples/, trace/, report, renames)."""
    root = Path(run_dir)
    out: list[str] = []
    for d in LATE_DIRS:
        if (root / d).is_dir():
            out += [p.relative_to(root).as_posix() for p in sorted((root / d).rglob("*")) if p.is_file()]
    out += [n for n in LATE_TOP if (root / n).is_file()]
    out += [p.name for p in sorted(root.glob("*_renamed.*")) if p.is_file()]
    return [p for p in out if p.split("/")[-1] not in NEVER_LISTED]


def register_late_outputs(run_dir: Path, cfg: Mapping[str, Any], m: dict[str, Any] | None = None,
                          generated: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Add every P7 to P11 output to files[] (kind by folder) and save; close.py may then delete it on time.

    `generated` maps a run-relative path to the time its writer took when writing it (created_basis generated);
    every other path is registered with an inferred created_utc (fix r3 B3-3).
    """
    m = m if m is not None else load(run_dir)
    if m is None:
        raise ManifestError("manifest.json missing: P8 to P11 outputs cannot be registered")
    have = {f["path"]: f for f in m.get("files", [])}
    entries = []
    for rel in late_outputs(run_dir):
        kind = rel.split("/")[0] if "/" in rel else "report"
        e = file_entry(run_dir, rel, kind, cfg, generated_utc=(generated or {}).get(rel))
        old = have.get(rel)
        if old and old.get("sha256") == e["sha256"]:
            continue  # unchanged: keep its original created_utc and retention date
        entries.append(e)
    upsert_files(m, entries)
    save_files(run_dir, m)
    return m


def save_files(run_dir: Path, m: Mapping[str, Any]) -> None:
    """Write manifest.json after a files[]-only change: every files[] entry is validated against the schema.

    report.py touches nothing but files[]; the rest of the manifest was validated by run_steps.py when it
    was written, so only the part this writer changed is re-validated here.
    """
    schema = schema_check.load_schema(SCHEMA_PATH)
    sub = {"$defs": schema.get("$defs", {}), **schema["properties"]["files"]}
    errs = schema_check.validate(list(m.get("files", [])), sub)
    if errs:
        raise ManifestError("files[] fails manifest.schema.json: " + "; ".join(errs[:8]))
    runctx.write_json(Path(run_dir) / MANIFEST, m)

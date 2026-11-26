"""The model folder: marker, layout, contract state, status file; subcommands `init` and `status`
(design sections 3.1, 4, 5, 17).

Owner: CORE-IO. Standard library only: the hooks import this module under `python -I` (design D2).

A folder is a model folder when it holds `rotm_model.json` with `"rotm_model": 1` (design D3). The marker is
written by `rotm.py init`, the first step of the procedure (and by `contract propose` when it is absent), so
the hooks recognise the folder before any contract or SQL exists.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from . import common, schemas

MARKER = "rotm_model.json"           # the marker file (design D3, section 4.1)
MARKER_VERSION = 1
LAYOUT = ("contracts", "queries", "raw", "reports")
MAX_UP = 40                          # levels find_model_root walks up
STATUS_FILE = "reports/status.json"
CONTRACT_STATES = ("missing", "invalid", "proposed", "agreed", "stale")
PRODUCTS = ("both", "excel", "sheets")
STEPS = ("build", "audit", "recalc", "check", "render", "land", "theme", "compat", "split", "extend",
         "lift", "diff", "document", "contract")
CONTRACT_SUFFIX = ".contract.json"
# Not copied by init --template: reports/, queries/ and raw/ (with raw/<t>.controls.json), and the template's built
# workbook and its manifest. A model made from a template has proposed contracts; after they are agreed, `land`
# writes its queries (stamped with the agreed contract) and its snapshot from the sample extract in data/ or the
# user's own extract (A-aera-2, C-sol-2). A copied query would keep the template's source, a copied workbook would
# show the template's numbers, and the finish-check hook would count it as a workbook the session wrote
# (integrator, live test s2).
TEMPLATE_SKIP = ("reports", "queries", "raw", "__pycache__", MARKER)
TEMPLATE_SKIP_SUFFIXES = (".xlsx", ".xlsm", ".manifest.json")
_LOCK_WAIT_S = 10.0
_LOCK_STALE_S = 60.0


# ----------------------------------------------------------------------------- marker

def _marker_data(root: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((root / MARKER).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("rotm_model") == MARKER_VERSION else None


def is_model_root(path: str | Path) -> bool:
    """True when `path` is a folder holding a valid marker (`"rotm_model": 1`)."""
    try:
        p = common.native_path(path)
        return (p / MARKER).is_file() and _marker_data(p) is not None
    except OSError:
        return False


def find_model_root(path: str | Path, max_up: int = MAX_UP) -> Path | None:
    """The first folder at or above `path` (a file or a folder, which need not exist yet) that holds a valid
    marker, else None. Relative paths are taken from the current folder."""
    try:
        p = Path(os.path.abspath(common.native_path(path)))
    except (OSError, ValueError):
        return None
    cur = p if p.is_dir() else p.parent
    for _ in range(max_up + 1):
        if is_model_root(cur):
            return cur
        if cur.parent == cur:
            return None
        cur = cur.parent
    return None


def read_marker(root: str | Path) -> dict[str, Any]:
    """The marker as a dict; raises common.Refusal naming the problem when it is missing or malformed."""
    r = common.native_path(root)
    path = r / MARKER
    if not path.is_file():
        raise common.Refusal(f"{r.as_posix()} is not a model folder: it has no {MARKER}",
                             fix=f"run rotm.py init \"{r.as_posix()}\" first, or give the model folder")
    data = common.load_json_file(path, "marker")
    schemas.require_valid(data, "marker", f"{r.name}/{MARKER}")
    return data


def _safe_name(text: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", text).strip() or "model"
    return name[:64]


def template_dirs() -> list[Path]:
    """Folders searched by `init --template NAME`: SKILL/templates, then PLUGIN/templates when the plugin exists."""
    dirs = [common.skill_dir() / "templates"]
    plugin = common.plugin_dir()
    if plugin is not None:
        dirs.append(plugin / "templates")
    return [d for d in dirs if d.is_dir()]


def available_templates() -> dict[str, Path]:
    """Template name -> folder (first found wins: SKILL before PLUGIN). A template is a folder with a spec."""
    out: dict[str, Path] = {}
    for d in template_dirs():
        for sub in sorted(d.iterdir()):
            if sub.is_dir() and (sub / "model.spec.json").is_file() and sub.name not in out:
                out[sub.name] = sub
    return out


def _find_template(template: str) -> Path:
    candidate = common.native_path(template)
    if candidate.is_dir():
        if not (candidate / "model.spec.json").is_file():
            raise common.Refusal(f"template folder {candidate.as_posix()} has no model.spec.json",
                                 fix="give a folder that holds a model specification")
        return candidate
    found = available_templates()
    if template in found:
        return found[template]
    near = schemas.nearest(template, found)
    raise common.Refusal(f"no template named '{template}'" + (f"; did you mean '{near}'?" if near else ""),
                         fix=("use one of: " + ", ".join(found)) if found else
                         "give the path of a template folder (none is installed)")


def _reset_agreements(root: Path, template_name: str) -> list[str]:
    """Set every copied contract back to proposed (design 5, 17). Returns the tables reset."""
    reset = []
    cdir = root / "contracts"
    if not cdir.is_dir():
        return reset
    when = common.now_iso()
    for path in sorted(cdir.glob("*" + CONTRACT_SUFFIX)):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        old = data.get("agreement") or {}
        data["agreement"] = {"status": "proposed", "contract_sha256": None, "reply": None, "by": None, "at": None,
                             "channel": None, "source_file": None, "recorded_by": None,
                             "note": f"copied from template '{template_name}' by rotm.py init on {when}; "
                                     "not agreed: show the user the contract and record their reply"}
        if old.get("status") == "agreed":
            data["agreement"]["history"] = [{k: old.get(k) for k in ("status", "by", "at", "channel")}]
        common.write_json(path, data)
        md = path.with_name(path.name[: -len(CONTRACT_SUFFIX)] + ".contract.md")
        if md.is_file():
            text = md.read_text(encoding="utf-8")
            kept = [ln for ln in text.splitlines() if not ln.startswith(("Agreed by ", "Agreed (version "))]
            kept.append("")
            kept.append(f"Not agreed: copied from template '{template_name}' on {when}. "
                        "Reply 'agreed' or say what to change.")
            md.write_text("\n".join(kept) + "\n", encoding="utf-8")
        reset.append(path.name[: -len(CONTRACT_SUFFIX)])
    return reset


def init_model(root: str | Path, *, name: str | None = None, product: str = "both",
               workbook: str = "model.xlsx", spec: str = "model.spec.json",
               template: str | None = None, force: bool = False) -> dict[str, Any]:
    """Create the folder, its LAYOUT subfolders and the marker; with `template`, copy that template
    (SKILL/templates/<name>, then PLUGIN/templates/<name>, or a path) without its queries/, raw/, reports/ and built
    workbook, and reset its agreements to proposed.
    Idempotent when the marker exists with the same name; refuses a different name unless `force`.
    Returns the marker (with "reset_tables" listing contracts set back to proposed, when a template was copied)."""
    r = Path(os.path.abspath(common.native_path(root)))
    if product not in PRODUCTS:
        raise common.Refusal(f"product '{product}' is not known", fix="use one of: " + ", ".join(PRODUCTS))
    if r.exists() and not r.is_dir():
        raise common.Refusal(f"{r.as_posix()} is a file, not a folder", fix="give a folder path for the model")
    outer = find_model_root(r.parent) if r.parent != r else None
    if outer is not None and not force:
        raise common.Refusal(f"{r.as_posix()} lies inside the model folder {outer.as_posix()}",
                             fix="choose a folder outside it (one model per folder), or pass --force")
    model_name = name if name is not None else _safe_name(r.name)
    errs = schemas.validate({"rotm_model": 1, "name": model_name}, "marker")
    if errs:
        raise common.Refusal(f"model name '{model_name}' is not allowed", fix=errs[0].fix or "choose another name")
    existing = _marker_data(r) if r.is_dir() else None
    if existing is not None and template is None:
        if existing.get("name") == model_name or name is None:
            for sub in LAYOUT:
                (r / sub).mkdir(exist_ok=True)
            return existing
        if not force:
            raise common.Refusal(f"{r.as_posix()} is already the model folder of '{existing.get('name')}'",
                                 fix=f"use that name, or pass --force to rename it to '{model_name}'")
    reset: list[str] = []
    template_name = None
    if template is not None:
        src = _find_template(template)
        template_name = src.name
        if r.is_dir() and not force:
            def _taken(dest: Path) -> bool:
                return dest.is_file() or (dest.is_dir() and any(dest.iterdir()))
            clash = [p.name for p in src.iterdir() if p.name not in TEMPLATE_SKIP and _taken(r / p.name)]
            if clash:
                raise common.Refusal(f"{r.as_posix()} already holds {', '.join(sorted(clash))}; the template would "
                                     "overwrite them", fix="choose an empty folder, or pass --force")
        r.mkdir(parents=True, exist_ok=True)
        for item in src.iterdir():
            if item.name in TEMPLATE_SKIP or item.name.startswith(".") or ".bak_" in item.name:
                continue
            if item.is_file() and item.name.lower().endswith(TEMPLATE_SKIP_SUFFIXES):
                continue        # the template's built workbook: the new model is built from its own data (P2, P3)
            dest = r / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "~$*"))
            else:
                shutil.copy2(item, dest)
        reset = _reset_agreements(r, template_name)
    r.mkdir(parents=True, exist_ok=True)
    for sub in LAYOUT:
        (r / sub).mkdir(exist_ok=True)
    marker: dict[str, Any] = {
        "rotm_model": MARKER_VERSION, "name": model_name, "created": common.now_iso(),
        "created_by": "rotm.py init", "plugin_version": common.VERSION, "spec": spec, "workbook": workbook,
        "product": product}
    if existing is not None and existing.get("created"):
        marker["created"] = existing["created"]
        marker["updated"] = common.now_iso()
    if template_name:
        marker["template"] = template_name
    schemas.require_valid(marker, "marker", MARKER)
    common.write_json(r / MARKER, marker)
    out = dict(marker)
    if template_name:
        out["reset_tables"] = reset
    return out


# ----------------------------------------------------------------------------- contracts

def contract_path(root: str | Path, table: str) -> Path:
    """<root>/contracts/<table>.contract.json."""
    return Path(root) / "contracts" / f"{table}{CONTRACT_SUFFIX}"


def contract_hash(contract: dict[str, Any]) -> str:
    """canonical_hash of the contract without its "agreement" member (design 5)."""
    return common.canonical_hash({k: v for k, v in contract.items() if k != "agreement"})


def read_contract(root: str | Path, table: str) -> tuple[str, dict[str, Any] | None, list[str]]:
    """(state, contract or None, problems). The state is one of CONTRACT_STATES; problems explain 'invalid'."""
    path = contract_path(root, table)
    if not path.is_file():
        return "missing", None, []
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        return "invalid", None, [f"{path.name}: cannot be read as JSON ({type(exc).__name__}: {exc})"]
    errs = schemas.validate(data, "contract")
    if errs:
        return "invalid", data if isinstance(data, dict) else None, [f"{path.name}: {e.line()}" for e in errs]
    if data["table"] != table:
        return "invalid", data, [f"{path.name}: table is '{data['table']}' but the file is named for '{table}'"]
    agreement = data.get("agreement") or {"status": "proposed"}
    if agreement.get("status") != "agreed":
        return "proposed", data, []
    if agreement.get("contract_sha256") != contract_hash(data):
        return "stale", data, [f"{path.name}: edited after it was agreed (hash differs); it counts as not agreed"]
    return "agreed", data, []


def contract_state(root: str | Path, table: str) -> str:
    """One of CONTRACT_STATES (design section 5): missing, invalid, proposed, agreed (hash matches), stale."""
    return read_contract(root, table)[0]


def tables(root: str | Path) -> list[str]:
    """Table names that have a contract file, sorted."""
    cdir = Path(root) / "contracts"
    if not cdir.is_dir():
        return []
    return sorted(p.name[: -len(CONTRACT_SUFFIX)] for p in cdir.glob("*" + CONTRACT_SUFFIX) if p.is_file())


# ----------------------------------------------------------------------------- status file

def status_path(root: str | Path) -> Path:
    """<root>/reports/status.json."""
    return Path(root) / STATUS_FILE


def _empty_status() -> dict[str, Any]:
    return {"rotm_status": 1, "workbooks": {}}


# v9 (round 8 verdict C-sol-5): the steps whose record holds the fingerprint of the model folder's inputs
INPUT_STEPS = ("audit", "check", "recalc")


def inputs_fingerprint(root: str | Path) -> dict[str, dict[str, str | None]]:
    """The inputs of the model folder's workbooks as they are now (v9, round 8 verdict C-sol-5): for every
    contracts/<t>.contract.json, {"raw_sha256": sha256 hex of the bytes of raw/<t>.csv, or None when that file does
    not exist or cannot be read; "contract_sha256": agreement.contract_sha256 when the contract is agreed (its hash
    matches, contract_state "agreed"), else None}. A landing changes raw_sha256; an agreement of other content, or a
    contract that is no longer agreed, changes contract_sha256. raw/<t>.controls.json is not part of it."""
    r = Path(root)
    out: dict[str, dict[str, str | None]] = {}
    for t in tables(r):
        raw = r / "raw" / f"{t}.csv"
        try:
            raw_sha = common.file_sha256(raw) if raw.is_file() else None
        except OSError:
            raw_sha = None
        state, data, _problems = read_contract(r, t)
        agreed = ((data or {}).get("agreement") or {}).get("contract_sha256") if state == "agreed" else None
        out[t] = {"raw_sha256": raw_sha, "contract_sha256": agreed if isinstance(agreed, str) else None}
    return out


def inputs_changes(recorded: Any, current: dict[str, dict[str, Any]]) -> list[tuple[str, str]]:
    """The differences between a record's field "inputs" and the current fingerprint, as (table, what) sorted by
    table, what being "raw" (raw_sha256 differs) or "contract" (contract_sha256 differs); a table that is in one of
    the two only counts as having None for both values. [] when `recorded` is not a dict (a record written before
    v9) or equals `current`."""
    if not isinstance(recorded, dict) or recorded == current:
        return []
    out: list[tuple[str, str]] = []
    for t in sorted(set(recorded) | set(current)):
        old = recorded.get(t) if isinstance(recorded.get(t), dict) else {}
        new = current.get(t) or {}
        if old.get("raw_sha256") != new.get("raw_sha256"):
            out.append((t, "raw"))
        if old.get("contract_sha256") != new.get("contract_sha256"):
            out.append((t, "contract"))
    return out


def read_status(root: str | Path) -> dict[str, Any]:
    """reports/status.json, or an empty status ({"rotm_status": 1, "workbooks": {}}) when absent or unreadable."""
    path = status_path(root)
    if not path.is_file():
        return _empty_status()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return _empty_status()
    if schemas.validate(data, "status"):
        return _empty_status()
    return data


class _FileLock:
    """Exclusive create of <file>.lock; a lock older than _LOCK_STALE_S is taken over."""

    def __init__(self, target: Path):
        self.path = target.with_name(target.name + ".lock")
        self.fd: int | None = None

    def __enter__(self) -> "_FileLock":
        deadline = time.monotonic() + _LOCK_WAIT_S
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, f"{os.getpid()}".encode())
                return self
            except (FileExistsError, PermissionError):
                # Windows: a lock file another thread is deleting ("delete pending") raises PermissionError on
                # open; it is busy in the same way as an existing lock.
                try:
                    if time.time() - self.path.stat().st_mtime > _LOCK_STALE_S:
                        self.path.unlink()
                        continue
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    raise common.RotmError(f"{self.path.as_posix()} is held by another process",
                                           fix="wait and run the command again, or delete the lock file "
                                               "if no rotm.py command is running")
                time.sleep(0.05)

    def __exit__(self, *exc: Any) -> None:
        if self.fd is not None:
            os.close(self.fd)
        try:
            self.path.unlink()
        except OSError:
            pass


WRITES_FILE = "reports/writes.json"
WRITES_VERSION = 1
WRITES_MAX = 500                     # pairs kept per workbook; the oldest are dropped


def writes_path(root: str | Path) -> Path:
    """<root>/reports/writes.json: the history of the workbook versions rotm.py commands wrote (v3, A-aera-2)."""
    return Path(root) / WRITES_FILE


def write_pair(step: str, entry: dict[str, Any]) -> dict[str, Any] | None:
    """The (input, output) pair a step record gives for the write history, or None when the step did not write
    the workbook: build "sha256"; land "output_sha256" or "sha256" only when power_query is true; check
    "output_sha256" only when recalculated is true; recalc, theme, extend, compat, split "output_sha256". The
    input is the record's "input_sha256" (None when it has none; such a pair does not make a file owned)."""
    if step == "build":
        out = entry.get("sha256")
    elif step == "land":
        out = (entry.get("output_sha256") or entry.get("sha256")) if entry.get("power_query") else None
    elif step == "check":
        out = entry.get("output_sha256") if entry.get("recalculated") else None
    elif step in ("recalc", "theme", "extend", "compat", "split"):
        out = entry.get("output_sha256")
    else:
        out = None
    if not isinstance(out, str) or not out:
        return None
    inp = entry.get("input_sha256")
    return {"step": step, "input_sha256": inp if isinstance(inp, str) and inp else None, "output_sha256": out,
            "at": entry.get("at") or common.now_iso()}


def read_writes(root: str | Path) -> dict[str, list[dict[str, Any]]]:
    """reports/writes.json as {workbook rel: [pair, ...]} (oldest first); {} when absent or unreadable. A pair
    without a text output hash is left out."""
    path = writes_path(root)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    books = data.get("workbooks") if isinstance(data, dict) else None
    if not isinstance(books, dict):
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for rel, pairs in books.items():
        if isinstance(rel, str) and isinstance(pairs, list):
            out[rel] = [p for p in pairs if isinstance(p, dict) and isinstance(p.get("output_sha256"), str)]
    return out


def _append_write(root: Path, rel: str, pair: dict[str, Any]) -> None:
    """Append one pair to reports/writes.json (the caller holds the status file's lock)."""
    books = read_writes(root)
    pairs = books.setdefault(rel, [])
    pairs.append(pair)
    if len(pairs) > WRITES_MAX:
        del pairs[: len(pairs) - WRITES_MAX]
    common.write_json(writes_path(root), {"rotm_writes": WRITES_VERSION, "workbooks": books})


def record_step(root: str | Path, workbook_rel: str, step: str, entry: dict[str, Any]) -> None:
    """Set status["workbooks"][workbook_rel][step] = entry (atomic write; design section 4.2).
    Steps: build, audit, recalc, check, render, land, theme, compat, split, extend (and lift, diff, document,
    contract). `workbook_rel` may be absolute; it is stored relative to the model folder with forward slashes.
    An unreadable status file is kept as status.json.bad_<time> and a new one is started.
    v3 (A-aera-2): a record of a step that wrote the workbook (write_pair) also appends its (input, output) pair to
    reports/writes.json, under the same lock; status.json keeps one record per step as before.
    v9 (round 8 verdict C-sol-5): an audit, check or recalc record gets the field "inputs" (inputs_fingerprint of
    the model folder when the step is recorded) unless the entry holds it already; the finish check counts a pass
    only while the fingerprint is unchanged."""
    r = Path(os.path.abspath(common.native_path(root)))
    if step not in STEPS:
        raise ValueError(f"unknown step {step!r}; known steps: {', '.join(STEPS)}")
    if not isinstance(entry, dict):
        raise ValueError("entry must be a dict")
    if step in INPUT_STEPS and "inputs" not in entry:
        entry = dict(entry, inputs=inputs_fingerprint(r))
    wp = common.native_path(workbook_rel)
    if not wp.is_absolute():
        # A path given relative to the current folder (rotm.py audit examples/m/model.xlsx run from elsewhere) is
        # taken as given when it lies in the model folder; otherwise it is relative to the model folder.
        here = Path(os.path.abspath(wp))
        wp = here if here.exists() and common.is_within(here, r) else r / wp
    rel = common.relpath_posix(wp, r)
    record = dict(entry)
    record.setdefault("at", common.now_iso())
    path = status_path(r)
    with _FileLock(path):
        data = _empty_status()
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                if schemas.validate(data, "status"):
                    raise ValueError("schema")
            except (OSError, ValueError):
                stamp = time.strftime("%Y%m%d_%H%M%S")
                try:
                    path.replace(path.with_name(f"{path.name}.bad_{stamp}"))
                except OSError:
                    pass
                data = _empty_status()
        data["workbooks"].setdefault(rel, {})[step] = record
        errs = schemas.validate(data, "status")
        if errs:
            raise common.RotmError(f"status record for {rel} {step} is not valid: {errs[0].line()}")
        common.write_json(path, data)
        pair = write_pair(step, record)
        if pair is not None:
            _append_write(r, rel, pair)


# ----------------------------------------------------------------------------- subcommands

def add_init_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("dir", nargs="?", help="the model folder to create (or an existing one)")
    p.add_argument("--name", help="model name (default: the folder name)")
    p.add_argument("--product", choices=PRODUCTS, default="both", help="target product profile (default both)")
    p.add_argument("--template", help="template name (SKILL/templates or PLUGIN/templates) or a template folder")
    p.add_argument("--force", action="store_true", help="replace a marker that has a different name")
    p.add_argument("--list-templates", action="store_true", help="list the available templates and stop")


def run_init(args: argparse.Namespace) -> int:
    if getattr(args, "list_templates", False):
        found = available_templates()
        if not found:
            print("no templates found (looked in: " + ", ".join(d.as_posix() for d in template_dirs()) + ")")
        for tname, folder in found.items():
            print(f"{tname}  {folder.as_posix()}")
        return common.EXIT_OK
    if not args.dir:
        raise common.Refusal("no model folder given", key="dir",
                             fix="give the folder to create: rotm.py init <folder> [--template NAME]")
    root = Path(os.path.abspath(common.native_path(args.dir)))
    had_marker = is_model_root(root)
    marker = init_model(root, name=args.name, product=args.product, template=args.template, force=args.force)
    verb = "model folder already exists" if had_marker and not args.template and not args.force else "created model folder"
    print(f"{verb}: {root.as_posix()} (name '{marker['name']}', product {marker.get('product', 'both')}, "
          f"marker {MARKER})")
    skill = common.skill_dir().as_posix()
    rotm = f"python -P -X utf8 -B \"{skill}/scripts/rotm.py\""
    if marker.get("template"):
        print(f"copied template '{marker['template']}' (without its queries/, raw/, reports/ and workbook)")
        for t in marker.get("reset_tables", []):
            print(f"contract '{t}' set to proposed: show contracts/{t}.contract.md to the user and record their reply")
        for t in marker.get("reset_tables", []):
            sample = root / "data" / f"{t}.csv"
            csv_arg = f"\"{sample.as_posix()}\"" if sample.is_file() else "<extract.csv>"
            print(f"next, after the user agreed contract '{t}': {rotm} land \"{root.as_posix()}\" --table {t} "
                  f"--csv {csv_arg} (writes raw/{t}.csv and queries/{t}.sql and .pq.m), then build")
        if marker.get("reset_tables"):
            return common.EXIT_OK
    print(f"next: {rotm} contract propose \"{root.as_posix()}\" --table <table> --csv <sample.csv>")
    return common.EXIT_OK


def add_status_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("dir", help="the model folder")
    p.add_argument("--json", metavar="FILE", help="write the status as JSON ('-' = stdout)")


def status_summary(root: str | Path) -> dict[str, Any]:
    """Marker, contract states and the last step records per workbook, with whether each record matches the
    workbook as it is now (sha256)."""
    r = Path(os.path.abspath(common.native_path(root)))
    marker = read_marker(r)
    contracts = []
    for t in tables(r):
        state, data, problems = read_contract(r, t)
        ag = (data or {}).get("agreement") or {}
        contracts.append({"table": t, "state": state, "by": ag.get("by") if state == "agreed" else None,
                          "at": ag.get("at") if state == "agreed" else None, "channel": ag.get("channel"),
                          "problems": problems})
    status = read_status(r)
    names = set(status["workbooks"])
    if marker.get("workbook"):
        names.add(marker["workbook"])
    workbooks = []
    for rel in sorted(names):
        path = r / rel
        sha = common.file_sha256(path) if path.is_file() else None
        steps = {}
        for step, rec in status["workbooks"].get(rel, {}).items():
            recorded = rec.get("output_sha256") or rec.get("sha256")
            steps[step] = dict(rec, current=(sha is not None and recorded == sha) if recorded else None)
        workbooks.append({"workbook": rel, "exists": sha is not None, "sha256": sha, "steps": steps})
    return {"root": r.as_posix(), "marker": marker, "contracts": contracts, "workbooks": workbooks}


def check_counts_text(rec: dict[str, Any]) -> str:
    """The check step's counts in check's own words (v2, J-4 (d); lib.check.summary_line):
    'checks: H of N hold; alerts: S of A show a value', from the record fields checks (error checks), failed (error
    checks not holding), alerts and alerts_shown; the alerts part only when the record has alerts. A record
    without failed (not recalculated) gives 'checks: N, not recalculated'. v4 (C-sol-5): a record with the field
    line (the summary line as check printed it) gives that line."""
    line = rec.get("line")
    if isinstance(line, str) and line:
        return line
    n = rec.get("checks")
    failed = rec.get("failed")
    if not isinstance(n, int) or isinstance(n, bool):
        return ""
    if not isinstance(failed, int) or isinstance(failed, bool):
        line = f"checks: {n}, not recalculated"
    else:
        line = f"checks: {n - failed} of {n} hold"
    alerts = rec.get("alerts")
    if isinstance(alerts, int) and not isinstance(alerts, bool) and alerts:
        shown = rec.get("alerts_shown")
        line += f"; alerts: {shown if isinstance(shown, int) else '?'} of {alerts} show a value"
    return line


def run_status(args: argparse.Namespace) -> int:
    summary = status_summary(args.dir)
    out = sys.stderr if args.json == "-" else sys.stdout
    m = summary["marker"]
    out.write(f"model folder: {summary['root']} (name '{m['name']}', product {m.get('product', 'both')})\n")
    if not summary["contracts"]:
        out.write("contracts: none yet (run rotm.py contract propose)\n")
    else:
        out.write("contracts:\n")
        width = max(len(c["table"]) for c in summary["contracts"])
        for c in summary["contracts"]:
            extra = f"  by {c['by']} on {c['at']} ({c['channel']})" if c["state"] == "agreed" else ""
            out.write(f"  {c['table'].ljust(width)}  {c['state']}{extra}\n")
            for prob in c["problems"]:
                out.write(f"    {prob}\n")
    for wb in summary["workbooks"]:
        state = f"exists, sha256 {wb['sha256'][:12]}" if wb["exists"] else "not built yet"
        out.write(f"workbook {wb['workbook']} ({state})\n")
        for step, rec in wb["steps"].items():
            cur = {True: "matches the file now", False: "older than the file now", None: ""}[rec.get("current")]
            bits = [f"exit {rec.get('exit_code')}" if "exit_code" in rec else ""]
            for key in ("errors", "warnings"):
                if key in rec:
                    bits.append(f"{key} {rec[key]}")
            counts = check_counts_text(rec) if step == "check" else ""
            if counts:
                bits.append(counts)
            else:
                for key in ("checks", "failed"):
                    if key in rec:
                        bits.append(f"{key} {rec[key]}")
            if "recalculated" in rec:
                bits.append("recalculated" if rec["recalculated"] else "not recalculated")
            if rec.get("strict"):
                bits.append("strict")
            out.write(f"  {step.ljust(7)} {rec.get('at', '')}  {'  '.join(b for b in bits if b)}  {cur}\n")
    if args.json:
        if args.json == "-":
            sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=1) + "\n")
        else:
            common.write_json(args.json, summary)
    return common.EXIT_OK

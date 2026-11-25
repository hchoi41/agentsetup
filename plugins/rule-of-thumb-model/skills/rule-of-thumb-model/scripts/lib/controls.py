"""Control totals accepted per raw snapshot (v2, A-aera-7): raw/<table>.controls.json beside the snapshot.

Owner: P1 (v2). Standard library only (common and schemas).

`rotm.py check --accept-refresh --control` validates new raw rows against a source-side control total that
differs from the agreed contract's total (the source grew or shrank). The accepted total is kept here, under the
digest of the accepted raw snapshot; the contract is not changed (a change of the contract is shown to the user
and agreed again). `rotm.py land --csv FILE --control` (v5, A-aera-6 and C-sol-3: an export of a refreshed
extract, for example from Google Sheets) writes the same record for the snapshot it lands. build, check and land
use the accepted totals of a snapshot whose digest is recorded here and the contract's totals otherwise.

File: {"rotm_controls": 1, "table": T, "accepted": [{"digest": "sha256:<64 hex>", "rows": N,
       "totals": [{"column": C, "value": V, "tolerance": X}], "given": ["T.C=V", ...], "at": ISO time}]}
- One entry per digest. Recording the same digest again with the same rows, totals and given texts keeps the
  entry as it is (its time too), so an acceptance that is run again after an interruption gives the same file.
  Other values for the same digest replace its entry.
- This module never writes contracts/. Writes go through common.write_json (atomic).
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

from . import common, schemas

SUFFIX = ".controls.json"
VERSION = 1


def path(root: str | Path, table: str) -> Path:
    """<model folder>/raw/<table>.controls.json."""
    return Path(common.native_path(root)) / "raw" / f"{table}{SUFFIX}"


def _empty(table: str) -> dict[str, Any]:
    return {"rotm_controls": VERSION, "table": table, "accepted": []}


def load(root: str | Path, table: str) -> dict[str, Any] | None:
    """The validated file, or None when it does not exist. An unreadable or invalid file is a common.Refusal
    that names the file."""
    p = path(root, table)
    if not p.is_file():
        return None
    data = common.load_json_file(p, "accepted-controls file")
    errs = schemas.validate(data, "controls")
    if not errs and data.get("table") != table:
        errs = [schemas.SchemaError("table", f"'{data.get('table')}' is not '{table}'",
                                    f"set \"table\": \"{table}\" or move the file to raw/{data.get('table')}{SUFFIX}")]
    if errs:
        raise common.Refusal(f"{p.as_posix()} is not valid ({len(errs)} problem{'s' if len(errs) != 1 else ''})",
                             details=[e.line() for e in errs],
                             fix="correct the keys named below, or delete the file and accept the refresh again "
                                 "with rotm.py check --accept-refresh --control")
    return data


def _number(v: Any) -> Any:
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(str(v))
    return v


def _totals(totals: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for t in totals:
        out.append({"column": t["column"], "value": _number(t["value"]), "tolerance": _number(t.get("tolerance", 0))})
    return out


def record_acceptance(root: str | Path, table: str, *, digest: str, rows: int, totals: Sequence[dict[str, Any]],
                      given: Sequence[str], at: str | None = None) -> dict[str, Any]:
    """Record the control totals accepted for the raw snapshot `digest` of `table`; returns the stored entry.
    `totals` are {"column", "value", "tolerance"} (the tolerance is the contract's); `given` are the --control
    texts as the user typed them. Raises common.Refusal when the existing file is invalid or the new entry does
    not follow the schema (nothing is written then)."""
    data = load(root, table) or _empty(table)
    entry = {"digest": digest, "rows": int(rows), "totals": _totals(totals), "given": [str(g) for g in given],
             "at": at or common.now_iso()}
    accepted = list(data["accepted"])
    for i, old in enumerate(accepted):
        if old.get("digest") != digest:
            continue
        if all(old.get(k) == entry[k] for k in ("rows", "totals", "given")):
            return old
        accepted[i] = entry
        break
    else:
        accepted.append(entry)
    data = {"rotm_controls": VERSION, "table": table, "accepted": accepted}
    schemas.require_valid(data, "controls", f"raw/{table}{SUFFIX}")
    common.write_json(path(root, table), data)
    return entry


def totals_for(root: str | Path, table: str, digest: str | None) -> list[dict[str, Any]] | None:
    """The totals accepted for snapshot `digest` ({"column", "value", "tolerance"} each), or None when no
    acceptance of that snapshot is recorded (callers then use the contract's totals)."""
    if not digest:
        return None
    data = load(root, table)
    if data is None:
        return None
    for e in data["accepted"]:
        if e.get("digest") == digest:
            return [dict(t) for t in e.get("totals") or []]
    return None

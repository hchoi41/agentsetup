"""Control totals accepted per raw snapshot (v2, A-aera-7): raw/<table>.controls.json beside the snapshot.

Owner: P1 (v2); v9: P2. Standard library only (common and schemas).

`rotm.py check --accept-refresh --control` validates new raw rows against a source-side control total that
differs from the agreed contract's total (the source grew or shrank). The accepted total is kept here, under the
digest of the accepted raw snapshot; the contract is not changed (a change of the contract is shown to the user
and agreed again). `rotm.py land --csv FILE --control` (v5, A-aera-6 and C-sol-3: an export of a refreshed
extract, for example from Google Sheets) writes the same record for the snapshot it lands.

The order (v9, r8 verdict [A-aera-2, C-sol-1]):
- ORDER: The control total used for a table and column is, in this order: --control given in this run; else the
  total accepted for this snapshot in raw/<t>.controls.json under the agreement in force; else the agreed
  contract's total.
- ORDER-STALE: A total accepted under an earlier agreement (the contract was agreed again since, for example with
  another total) is not used: land, build, check and lift print a note that names it, and when the rows do not meet
  the contract's total, L-TOTAL (land) or D-TOTAL (check) names both totals. Give --control with the source's
  current total; it is recorded under the agreement in force.
- ORDER-DETAIL: Each entry of raw/<t>.controls.json records the hash of the agreed contract it was accepted under
  (contract_sha256). An entry written before v9 has no hash; it is used only when its time is not earlier than the
  time of the current agreement, and not when either time is missing. Recording the same values again under the
  same agreement keeps the entry as it is; a record under another agreement replaces it. contract show and
  contract agree with unchanged content keep the entry in force.

lookup() is the one reader every caller uses (land, build, check, lift); stale_note() gives the note for an entry
that does not apply, stale_tail() the tail of L-TOTAL and D-TOTAL, stale_fix() their fix.

File: {"rotm_controls": 1, "table": T, "accepted": [{"digest": "sha256:<64 hex>", "rows": N,
       "totals": [{"column": C, "value": V, "tolerance": X}], "given": ["T.C=V", ...], "at": ISO time,
       "contract_sha256": "<64 hex>" (v9; absent in an entry written before v9)}]}
- One entry per digest. Recording the same digest again with the same rows, totals, given texts and contract hash
  keeps the entry as it is (its time too), so an acceptance that is run again after an interruption gives the same
  file. Other values for the same digest (also another agreement) replace its entry.
- This module never writes contracts/. Writes go through common.write_json (atomic).
"""
from __future__ import annotations

import datetime as _dt
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


def agreement_hash(contract: dict[str, Any] | None) -> str | None:
    """agreement.contract_sha256 of `contract` when its agreement record says agreed, else None."""
    ag = (contract or {}).get("agreement")
    if not isinstance(ag, dict) or ag.get("status") != "agreed":
        return None
    h = ag.get("contract_sha256")
    return h if isinstance(h, str) and h else None


def _agreement_at(contract: dict[str, Any] | None) -> str | None:
    ag = (contract or {}).get("agreement")
    if not isinstance(ag, dict) or ag.get("status") != "agreed":
        return None
    return ag.get("at") if isinstance(ag.get("at"), str) else None


def _time(text: Any) -> _dt.datetime | None:
    """An ISO time as an aware datetime (a time without offset is read as local time), or None when unreadable."""
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        t = _dt.datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo is not None else t.astimezone()


def applies(entry: dict[str, Any], contract: dict[str, Any] | None) -> bool:
    """True when `entry` was accepted under the agreement in force of `contract` (ORDER-DETAIL): its
    contract_sha256 equals agreement.contract_sha256; an entry without the field (written before v9) applies only
    when its time and the agreement's time are both readable ISO times and its time is not earlier."""
    if "contract_sha256" in entry:
        h = agreement_hash(contract)
        return h is not None and entry.get("contract_sha256") == h
    e_at, a_at = _time(entry.get("at")), _time(_agreement_at(contract))
    return e_at is not None and a_at is not None and e_at >= a_at


def record_acceptance(root: str | Path, table: str, *, digest: str, rows: int, totals: Sequence[dict[str, Any]],
                      given: Sequence[str], contract_sha256: str | None, at: str | None = None) -> dict[str, Any]:
    """Record the control totals accepted for the raw snapshot `digest` of `table`; returns the stored entry.
    `totals` are {"column", "value", "tolerance"} (the tolerance is the contract's); `given` are the --control
    texts as the user typed them; `contract_sha256` (v9, a keyword every caller states) is agreement.contract_sha256
    of the agreed contract in force, stored in the entry when it is not None. An entry for the same digest with the
    same rows, totals, given texts and contract hash is kept as it is (the file is not written); any other entry
    for that digest is replaced. Raises common.Refusal when the existing file is invalid or the new entry does not
    follow the schema (nothing is written then)."""
    data = load(root, table) or _empty(table)
    entry: dict[str, Any] = {"digest": digest, "rows": int(rows), "totals": _totals(totals),
                             "given": [str(g) for g in given], "at": at or common.now_iso()}
    if contract_sha256 is not None:
        entry["contract_sha256"] = str(contract_sha256)
    accepted = list(data["accepted"])
    for i, old in enumerate(accepted):
        if old.get("digest") != digest:
            continue
        if all(old.get(k) == entry.get(k) for k in ("rows", "totals", "given", "contract_sha256")):
            return old
        accepted[i] = entry
        break
    else:
        accepted.append(entry)
    data = {"rotm_controls": VERSION, "table": table, "accepted": accepted}
    schemas.require_valid(data, "controls", f"raw/{table}{SUFFIX}")
    common.write_json(path(root, table), data)
    return entry


def lookup(root: str | Path, table: str, digest: str | None,
           contract: dict[str, Any] | None) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
    """(totals, stale entry) for snapshot `digest` of `table` under the agreed contract `contract` (the one the
    caller holds): the totals ({"column", "value", "tolerance"} each) of the entry for that digest when it applies
    (applies()), and None as the second item; (None, the entry) when an entry for the digest exists and does not
    apply (accepted under an earlier agreement); (None, None) when no entry is recorded for the digest. Callers use
    the contract's totals whenever the first item is None. An invalid file is a common.Refusal (load)."""
    if not digest:
        return None, None
    data = load(root, table)
    if data is None:
        return None, None
    for e in data["accepted"]:
        if e.get("digest") == digest:
            if applies(e, contract):
                return [dict(t) for t in e.get("totals") or []], None
            return None, dict(e)
    return None, None


def totals_for(root: str | Path, table: str, digest: str | None,
               contract: dict[str, Any] | None) -> list[dict[str, Any]] | None:
    """The totals accepted for snapshot `digest` under the agreement in force of `contract` (lookup's first item),
    or None (callers then use the contract's totals)."""
    return lookup(root, table, digest, contract)[0]


def num_text(v: Any) -> str:
    """A total in a message: 57514500 -> '57,514,500'; 0.25 -> '0.25' (no exponent)."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    if x.is_integer():
        return f"{x:,.0f}"
    return f"{x:,.6f}".rstrip("0").rstrip(".")


def stale_value(entry: dict[str, Any] | None, column: str) -> Any:
    """The total of `column` in a stale entry, or None."""
    for t in (entry or {}).get("totals") or []:
        if t.get("column") == column:
            return t.get("value")
    return None


def stale_note(table: str, entry: dict[str, Any], contract: dict[str, Any] | None,
               given: Sequence[str] = ()) -> str:
    """The note for an entry of raw/<table>.controls.json that does not apply (v9, plan 5.1): it names the entry's
    totals and time, says that they were accepted under an earlier agreement and are not used, and which totals are
    used instead (the agreed contract's; for a column in `given`, the --control total of this run)."""
    totals = [t for t in entry.get("totals") or [] if isinstance(t, dict)]
    contract_totals = {t["column"]: t.get("value") for t in ((contract or {}).get("expect") or {}).get("totals") or []}
    at = entry.get("at") or "an unknown time"
    if len(totals) == 1:
        named = f"the total {totals[0]['column']} = {num_text(totals[0]['value'])}"
        verb = "was"
    else:
        named = "the totals " + ", ".join(f"{t['column']} = {num_text(t['value'])}" for t in totals)
        verb = "were"
    head = (f"raw/{table}.controls.json: {named} accepted for this snapshot at {at} {verb} accepted under an earlier "
            f"agreement of contract '{table}' and {'is' if verb == 'was' else 'are'} not used")
    cols = [t["column"] for t in totals]
    from_contract = [c for c in cols if c not in given and c in contract_totals]
    parts = []
    if len(from_contract) == 1 and len(cols) == 1:
        parts.append(f"the agreed contract's total {num_text(contract_totals[from_contract[0]])} is used")
    elif len(from_contract) == 1:
        c = from_contract[0]
        parts.append(f"the agreed contract's total {c} = {num_text(contract_totals[c])} is used")
    elif from_contract:
        parts.append("the agreed contract's totals "
                     + ", ".join(f"{c} = {num_text(contract_totals[c])}" for c in from_contract) + " are used")
    by_given = [c for c in cols if c in given]
    if by_given:
        parts.append(f"the total given with --control in this run is used for {', '.join(by_given)}")
    no_total = [c for c in cols if c not in given and c not in contract_totals]
    if no_total:
        parts.append(f"the agreed contract names no control total for {', '.join(no_total)}")
    return head + ("; " + "; ".join(parts) if parts else "")


def stale_tail(entry: dict[str, Any] | None, column: str) -> str:
    """The tail of L-TOTAL and D-TOTAL for a column whose total in a stale entry is not used, or '' (plan 5.1)."""
    value = stale_value(entry, column)
    if value is None:
        return ""
    return (f"; the total {num_text(value)} accepted for these rows at {(entry or {}).get('at') or 'an unknown time'} "
            "was accepted under an earlier agreement and is not used")


def stale_fix(table: str, column: str, model: str, *, query: bool = False, rotm: str = "rotm.py") -> str:
    """The fix of L-TOTAL and D-TOTAL when a stale entry names the column (plan 5.1): give the source's current
    total, which is recorded for this snapshot under the agreement in force. `query`: the table is loaded by a query
    (check --accept-refresh); otherwise land --csv --force."""
    if query:
        cmd = f"{rotm} check \"{model}\" --accept-refresh --control {table}.{column}=<source total>"
    else:
        cmd = (f"{rotm} land \"{model}\" --table {table} --csv <file> --force --control "
               f"{table}.{column}=<source total>")
    return f"give the source's current total: {cmd}; it is recorded for this snapshot under the agreement in force"

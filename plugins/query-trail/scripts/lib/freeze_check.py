"""freeze_check: B.sql (and A.sql in second-author mode) verified against its freeze before a script reads it
(fix brief r0 item 2; design spec v1 sections 2.2 P4 and P5, 4.1, 4.2).

packet_freeze.py writes packet_freeze.json (packet digest = sha256 of packet.sha256; A and B sha256). Every
script that reads B.sql or A.sql after P4 calls `require_frozen` first: split, lint (P5), compile, run_steps,
contract_check (P6) and rename_apply. It refuses (exit 2, nothing read or written) when the record or the side
is missing, when packet.sha256 no longer has the recorded digest or no longer lists packet/, or when the file's
current sha256 differs from the record. In second-author mode (author_mode starts with "second:") a check of B
also checks A. The P4 pre-freeze loop (contract_check, lint on the draft) uses `require_frozen_if_recorded`:
an unfrozen draft is read; a frozen side must still match its record.
"""
from __future__ import annotations

from pathlib import Path

from lib import runctx
from lib.csv_contract import sha256_file
from lib.exit_codes import RefusedError

RECORD = "packet_freeze.json"
PACKET_DIR = "packet"
PACKET_LIST = "packet.sha256"
SIDES = ("A", "B")


class FreezeError(RefusedError):
    """B.sql or A.sql is not frozen, or no longer matches its freeze (exit 2)."""


def packet_lines(packet_dir: Path) -> list[str]:
    """`<sha256>  <relpath>` for every file under packet/, sorted by POSIX relative path."""
    packet_dir = Path(packet_dir)
    files = sorted((p for p in packet_dir.rglob("*") if p.is_file()), key=lambda p: p.relative_to(packet_dir).as_posix())
    return [f"{sha256_file(p)}  {p.relative_to(packet_dir).as_posix()}" for p in files]


def _record(run_dir: Path) -> dict:
    p = Path(run_dir) / RECORD
    if not p.is_file():
        raise FreezeError(f"no freeze record ({RECORD}): B.sql is read only after packet_freeze.py --freeze packet "
                          "and --freeze B (P4)")
    try:
        rec = runctx.read_json(p)
    except (OSError, ValueError) as exc:
        raise FreezeError(f"{RECORD} unreadable: {exc}") from exc
    if not isinstance(rec, dict):
        raise FreezeError(f"{RECORD} is not an object")
    return rec


def author_mode(run_dir: Path, rec: dict | None = None) -> str:
    """run_config.json author_mode (inside the G1a hash), else the freeze record's, else single."""
    cfg = runctx.load_run_config_raw(Path(run_dir), required=False)
    return str(cfg.get("author_mode") or (rec or {}).get("author_mode") or "single")


def _check_packet(run_dir: Path, rec: dict) -> None:
    pk = rec.get("packet")
    lst = Path(run_dir) / PACKET_LIST
    if not isinstance(pk, dict) or not pk.get("digest"):
        raise FreezeError(f"{RECORD} has no packet freeze: freeze the packet first")
    if not lst.is_file() or sha256_file(lst) != pk["digest"]:
        raise FreezeError(f"the packet changed after it was frozen: {PACKET_LIST} no longer has digest "
                          f"{pk['digest'][:12]}...")
    if packet_lines(Path(run_dir) / PACKET_DIR) != lst.read_text(encoding="utf-8").splitlines():
        raise FreezeError(f"the packet changed after it was frozen: {PACKET_DIR}/ no longer matches {PACKET_LIST}")


def _check_side(run_dir: Path, rec: dict, side: str) -> str:
    ent = rec.get(side)
    if not isinstance(ent, dict) or not ent.get("sha256"):
        raise FreezeError(f"{side}.sql is not frozen: run packet_freeze.py --freeze {side} (P4) before it is read")
    p = Path(run_dir) / f"{side}.sql"
    if not p.is_file():
        raise FreezeError(f"{side}.sql is frozen but missing")
    h = sha256_file(p)
    if h != ent["sha256"]:
        raise FreezeError(f"{side}.sql changed after it was frozen (now {h[:12]}..., frozen {ent['sha256'][:12]}...): "
                          "nothing read; a changed query needs a decision that routes back to P4 (G8 rewrite, G9 or "
                          "G10 rewrite, G11 split_finer or reject), then packet_freeze.py --freeze B --new-attempt; "
                          "otherwise it is a new run")
    return h


def require_frozen(run_dir: Path, side: str = "B") -> str:
    """The frozen sha256 of `side`; refuse (exit 2) unless packet and side match the record (A too, second mode)."""
    if side not in SIDES:
        raise FreezeError(f"unknown side {side!r}: A or B")
    rec = _record(run_dir)
    _check_packet(run_dir, rec)
    mode = author_mode(run_dir, rec)
    second = mode.startswith("second:")
    if side == "A" and not second:
        raise FreezeError(f"author_mode is {mode}: A.sql is not part of this run")
    h = _check_side(run_dir, rec, side)
    if side == "B" and second:
        _check_side(run_dir, rec, "A")
    return h


def frozen_sides(run_dir: Path) -> dict[str, str]:
    """{"B": sha256} or, in second-author mode, {"B": ..., "A": ...}; every hash verified."""
    out = {"B": require_frozen(run_dir, "B")}
    if author_mode(run_dir, _record(run_dir)).startswith("second:"):
        out["A"] = require_frozen(run_dir, "A")
    return out


def require_frozen_if_recorded(run_dir: Path, side: str = "B") -> str | None:
    """P4 pre-freeze loop: None while `side` is not frozen yet; once frozen, `require_frozen`."""
    p = Path(run_dir) / RECORD
    if not p.is_file():
        return None
    rec = _record(run_dir)
    if side not in rec:
        return None
    return require_frozen(run_dir, side)

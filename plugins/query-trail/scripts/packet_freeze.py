"""packet_freeze.py: hash-freeze the author packet, then A before B (design spec v1 sections 2.2 P4, 4.1, 4.2; T09 folder rule).

- `--freeze packet`: copy the packet inputs into packet/ (default: 02_metric_card.md, 03_output_contract.json,
  13_table_decision.md, 11_value_map.csv; spec 0.3 "Packet") and write packet.sha256: one line
  `<sha256>  <relative path>` per file under packet/, sorted by relative path (POSIX). The packet digest is
  the sha256 of that file.
- `--freeze A`: in second-author mode, record A.sql's hash (A is frozen before B starts, spec 4.2).
- `--freeze B`: record B.sql's hash; in second-author mode refuse (exit 2) unless A is frozen and unchanged.
- `--verify`: recompute every recorded hash; exit 1 on any change.

The freeze record is packet_freeze.json; author mode and independence level go to manifest.json
(created as a valid skeleton when absent). Exit 0 PASS, 1 changed file on --verify, 2 refused. Every later
reader of B.sql / A.sql checks this record first through lib.freeze_check.require_frozen.

Fix r4:
- C4-13: `--freeze packet` rebuilds 13_table_decision.md with lib.table_decision.build and refuses (exit 2, nothing
  written) when the file on disk differs: decide ran before G1b, a value-map edit or the pin plan; rerun
  `discover.py --run-dir RUN decide` (allowed at P2 to P4 until the packet is frozen).
- C4-14: `--freeze B --new-attempt` re-freezes a corrected B inside the run, only after a decision of the current
  attempt that routes back to P4 (lib.state.REWORK_ROUTES: G8 rewrite or change_course, G9 rewrite, G10 rewrite,
  G11 split_finer or reject). It keeps the old B hash under packet_freeze.json `B.superseded[]` with that decision
  row, archives steps/, plan.json, executed/, checks/ and lint_report.csv of the old attempt under attempts/<n>/,
  increments run_state.attempt, and so split, lint and compile run again. The packet stays frozen: a packet change
  is still a new run. Without such a row a second B freeze is refused as before.
- W37: a missing packet input names the command that writes it (13_table_decision.md: discover.py decide).

Fix r11 L1 (live run 06): a `.csv` packet input is copied with its `.schema.json` sidecar (D-04), so the csv_check
hook finds the sidecar next to packet/11_value_map.csv. packet_lines lists every file under packet/, so the sidecar
is listed in packet.sha256 and covered by the packet digest (its bytes are a copy of the run-folder sidecar).
packet_freeze.json `packet.files` still counts the packet inputs; `packet.sidecars` counts the copied sidecars.

CLI: python -I -X utf8 -B scripts/packet_freeze.py --run-dir RUN --freeze packet|A|B [--include F ...]
     python -I -X utf8 -B scripts/packet_freeze.py --run-dir RUN --freeze B --new-attempt
     python -I -X utf8 -B scripts/packet_freeze.py --run-dir RUN --verify
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import manifest, runctx  # noqa: E402
from lib.csv_contract import sha256_file, write_text_lf  # noqa: E402
from lib.exit_codes import CheckFailedError, ExitCode, RefusedError  # noqa: E402
from lib.freeze_check import PACKET_DIR, PACKET_LIST, RECORD, packet_lines  # noqa: E402,F401 - one definition

DEFAULT_INPUTS = ("02_metric_card.md", "03_output_contract.json", "13_table_decision.md", "11_value_map.csv")


def freeze_packet(run_dir: Path, inputs: Sequence[str]) -> dict[str, Any]:
    """Copy inputs into packet/ and write packet.sha256; refuse a missing input or a second freeze."""
    pdir = run_dir / PACKET_DIR
    if (run_dir / PACKET_LIST).exists():
        raise RefusedError("packet already frozen: a changed packet is a new run")
    missing = [f for f in inputs if not (run_dir / f).is_file() and not (pdir / f).is_file()]
    if missing:
        how = {"13_table_decision.md": "discover.py --run-dir RUN decide (P2, after the G2 lock)",
               "11_value_map.csv": "value_map.py --run-dir RUN add ... (P2, G3)"}
        raise RefusedError(f"packet inputs missing: {missing}; write them with: "
                           + "; ".join(f"{m}: {how.get(m, 'the P1/P2 step that owns it')}" for m in missing))
    if "13_table_decision.md" in inputs and (run_dir / "13_table_decision.md").is_file() \
            and (run_dir / "14_discovery_lock.json").is_file():  # no lock: no P2 ran, pin_plan and decide refuse
        from lib import table_decision  # fix r4 C4-13: the decision must be what decide writes from today's inputs
        now = table_decision.build(run_dir)
        if (run_dir / "13_table_decision.md").read_text(encoding="utf-8") != now:
            raise RefusedError("13_table_decision.md differs from what discover.py decide writes from the current "
                               "lock, bindings, value map and pin plan (it ran before a later change): rerun "
                               "discover.py --run-dir RUN decide, then freeze the packet")
    pdir.mkdir(exist_ok=True)
    sidecars = 0
    for f in inputs:
        src, dst = run_dir / f, pdir / f
        if src.is_file() and src.resolve() != dst.resolve():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
        if f.lower().endswith(".csv"):  # fix r11 L1: the D-04 sidecar travels with its CSV
            ssrc, sdst = sidecar_of(run_dir / f), sidecar_of(pdir / f)
            if ssrc.is_file() and ssrc.resolve() != sdst.resolve():
                shutil.copyfile(ssrc, sdst)
            sidecars += sdst.is_file()
    lines = packet_lines(pdir)
    write_text_lf(run_dir / PACKET_LIST, "\n".join(lines) + "\n")
    return {"digest": sha256_file(run_dir / PACKET_LIST), "files": len(lines) - sidecars, "sidecars": sidecars,
            "frozen_utc": runctx.fmt_utc(runctx.utc_now())}


def sidecar_of(csv_path: Path) -> Path:
    """`<stem>.schema.json` next to `<stem>.csv` (D-04; the name csv_check.py looks for)."""
    return csv_path.with_name(csv_path.name[:-4] + ".schema.json")


def _record(run_dir: Path) -> dict[str, Any]:
    p = run_dir / RECORD
    return runctx.read_json(p) if p.is_file() else {"schema_version": "1"}


def verify(run_dir: Path) -> list[str]:
    """Every recorded hash that no longer matches."""
    rec = _record(run_dir)
    bad = []
    if "packet" in rec:
        if not (run_dir / PACKET_LIST).is_file() or sha256_file(run_dir / PACKET_LIST) != rec["packet"]["digest"]:
            bad.append(PACKET_LIST)
        else:
            want = (run_dir / PACKET_LIST).read_text(encoding="utf-8").splitlines()
            if packet_lines(run_dir / PACKET_DIR) != want:
                bad.append(PACKET_DIR + "/")
    for side in ("A", "B"):
        if side in rec:
            p = run_dir / f"{side}.sql"
            if not p.is_file() or sha256_file(p) != rec[side]["sha256"]:
                bad.append(f"{side}.sql")
    return bad


def freeze_side(run_dir: Path, side: str, author_mode: str) -> dict[str, Any]:
    """Record A.sql or B.sql; B in second-author mode needs a frozen, unchanged A first."""
    rec = _record(run_dir)
    if "packet" not in rec:
        raise RefusedError("freeze the packet first")
    if side in rec:
        raise RefusedError(f"{side}.sql already frozen")
    if side == "A" and author_mode == "single":
        raise RefusedError("author_mode is single: there is no A to freeze")
    if side == "B" and author_mode.startswith("second:"):
        if "A" not in rec:
            raise RefusedError("second-author mode: A must be frozen before B (spec 4.2)")
        if "A.sql" in verify(run_dir):
            raise RefusedError("A.sql changed after it was frozen")
    p = run_dir / f"{side}.sql"
    if not p.is_file():
        raise RefusedError(f"{p} does not exist")
    return {"sha256": sha256_file(p), "frozen_utc": runctx.fmt_utc(runctx.utc_now())}


# fix r5 addendum 8: samples, trace results and logic inputs of the old attempt go too (a sampled row's lookup id
# is a hash of its values, so an unchanged row would otherwise pick up a result recomputed at the old as-of)
REWORK_ARCHIVE = ("steps", "plan.json", "executed", "checks", "lint_report.csv", "samples", "trace", "trace_report.md",
                  "logic_inputs", "logic_inputs.json")


def new_attempt_b(run_dir: Path, rec: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Fix r4 C4-14: re-freeze a corrected B.sql inside the run; return (the new B entry, the new attempt)."""
    from lib import state

    st = state.read_state(state.state_path(run_dir))
    row = state.rework_row(run_dir, st.attempt)
    if row is None:
        raise RefusedError(f"--new-attempt needs a decision of attempt {st.attempt} that routes back to P4 (G8 rewrite "
                           "or change_course, G9 rewrite, G10 rewrite, G11 split_finer or reject): a changed B without "
                           "one is a new run")
    old = rec.get("B")
    if not isinstance(old, dict) or not old.get("sha256"):
        raise RefusedError("--new-attempt re-freezes a B that was frozen before; freeze B first")
    p = run_dir / "B.sql"
    if not p.is_file():
        raise RefusedError(f"{p} does not exist")
    h = sha256_file(p)
    if h == old["sha256"]:
        raise RefusedError("B.sql is unchanged: nothing to re-freeze (write the corrected B first)")
    dest = run_dir / "attempts" / str(st.attempt)
    if dest.exists():
        raise RefusedError(f"{dest.relative_to(run_dir).as_posix()} already exists: attempt {st.attempt} was archived")
    dest.mkdir(parents=True)
    for name in REWORK_ARCHIVE:
        src = run_dir / name
        if src.exists():
            shutil.move(str(src), str(dest / name))
    if (run_dir / RECORD).is_file():  # the freeze record as it stood for that attempt
        shutil.copyfile(run_dir / RECORD, dest / RECORD)
    superseded = list(old.get("superseded") or []) + [
        {"sha256": old["sha256"], "frozen_utc": old.get("frozen_utc"), "attempt": st.attempt,
         "decision_row": f"decisions.csv#{row}", "superseded_utc": runctx.fmt_utc(runctx.utc_now())}]
    new = {"sha256": h, "frozen_utc": runctx.fmt_utc(runctx.utc_now()), "superseded": superseded}
    state.write_state(state.state_path(run_dir), state.update_state(st, attempt=st.attempt + 1))
    return new, st.attempt + 1


def update_manifest(run_dir: Path, cfg: dict[str, Any], attempt: int, dependent_revision: bool) -> dict[str, Any]:
    """Create or update manifest.json with author mode and independence level."""
    m = manifest.load(run_dir)
    if m is None:
        pin_plan = runctx.read_json(run_dir / "15_pin_plan.json") if (run_dir / "15_pin_plan.json").is_file() else None
        m = manifest.new_manifest(cfg, attempt, manifest.pin_block(pin_plan), dependent_revision)
    m["author_mode"] = cfg.get("author_mode", "single")
    m["owner_mode"] = cfg.get("owner_mode")
    m["independence"] = manifest.independence(m["author_mode"], dependent_revision)
    manifest.save(run_dir, m)
    return m


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="packet_freeze.py", description="query-trail packet freeze (spec 2.2 P4)")
    ap.add_argument("--run-dir", required=True, type=Path)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--freeze", choices=("packet", "A", "B"))
    g.add_argument("--verify", action="store_true")
    ap.add_argument("--include", nargs="*", default=[], help="extra packet files, relative to the run folder")
    ap.add_argument("--inputs", nargs="*", default=None, help="replace the default packet inputs")
    ap.add_argument("--dependent-revision", action="store_true", help="informed re-author after G4 reject (IND-0)")
    ap.add_argument("--new-attempt", action="store_true",
                    help="with --freeze B: re-freeze a corrected B after a decision that routes back to P4 (C4-14)")
    a = ap.parse_args(argv)

    def body() -> int:
        st = runctx.enter(a.run_dir, ("P4",))
        cfg = runctx.load_run_config(a.run_dir)
        runctx.require_g0a(cfg)
        if a.verify:
            bad = verify(a.run_dir)
            if bad:
                raise CheckFailedError(f"frozen files changed: {bad}")
            print("packet_freeze.py: PASS: every frozen hash matches")
            return int(ExitCode.PASS)
        rec = _record(a.run_dir)
        mode = cfg.get("author_mode", "single")
        attempt = st.attempt
        if a.new_attempt and a.freeze != "B":
            raise RefusedError("--new-attempt applies to --freeze B only (a packet change is a new run)")
        if a.freeze == "packet":
            rec["packet"] = freeze_packet(a.run_dir, list(a.inputs if a.inputs is not None else DEFAULT_INPUTS)
                                          + list(a.include))
        elif a.new_attempt:
            rec["B"], attempt = new_attempt_b(a.run_dir, rec)
        else:
            rec[a.freeze] = freeze_side(a.run_dir, a.freeze, mode)
        rec["author_mode"] = mode
        m = update_manifest(a.run_dir, cfg, attempt, a.dependent_revision)
        rec["independence"] = m["independence"]
        runctx.write_json(a.run_dir / RECORD, rec)
        print(f"packet_freeze.py: PASS: froze {a.freeze}; author_mode {mode}; {m['independence']['level']}"
              + (f"; attempt {attempt}: split, lint and compile run again (P5)" if a.new_attempt else ""))
        return int(ExitCode.PASS)

    return runctx.run_cli(body, "packet_freeze.py")


if __name__ == "__main__":
    sys.exit(main())

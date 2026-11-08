"""edit_guard.py: PreToolUse hook for Write, Edit, MultiEdit and NotebookEdit (design spec v1 section 1.5).

Blocks (exit 2):
1. while a run is active (<QT_HOME>/active_run.json): any write outside the active run folder, except the
   plugin's own tests/ (so a fix to a test can still be made in a build session);
2. inside any run folder, edits to script-owned or frozen files:
   - always script-owned: manifest.json, manifest.sha256, decisions.csv, run_state.json, gate_request.json,
     plan.json, card_hashes.json, packet.sha256, packet_freeze.json, lint_report.csv, report.md,
     report_status.json, run_steps_report.json, the 00/05-09/14/15 discovery and pin files (every preflight
     attempt's 00_preflight.attemptN.json and 00_capability_probe.attemptN.csv too, fix r2), deletions.log,
     every *.schema.json sidecar, 11_value_map.csv (value_map.py writes it) and 13_table_decision.md
     (discover.py decide writes it) since fix r3, discover_status.json, and the folders packet/, checks/,
     outputs/, executed/, samples/, trace/, gates/, spawn/, discovery_inputs/;
   - frozen by a gate: steps/ after G11 is recorded in decisions.csv; A.sql and B.sql once
     packet_freeze.json records them (fix r4 C4-14: B.sql opens again while the state is at P4 after a decision of
     the attempt that routes back to P4; packet_freeze.py --freeze B --new-attempt re-freezes it); 00_question.txt (Max's question, verbatim; fix r2 B2-M3),
     01_question_card.md and run_config.json once G1a is approved: card_hashes.json holds a G1a hash AND
     decisions.csv holds a G1a approve row in any attempt of the run (fix r3 A3-1: the hash is provisional until
     the approve row, so Max can ask for a redraft at G1a; fix r4 C4-3: once approved, frozen for the rest of the
     run, whatever the attempt; later attempts inherit the approval and changing intent is a new run). The
     metric card's G1a fields are protected by card_freeze.py's intent hash from G1a on (any attempt) and the whole
     file by this hook from G1b, because its bindings are written at P2, between G1a and G1b;
     02_metric_card.md, 03_output_contract.json, 11_value_map.csv and 13_table_decision.md after G1b.
A write through Bash (redirection, sed -i, tee, cp, mv, rm) is checked by gate.py with protected() below, as
far as a command line shows it (fix r1 B-M7); scripts refuse a
changed file by hash (run_steps.py edit guard, packet_freeze.py --verify, card_freeze.py).
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _hooklib as hl  # noqa: E402

OWNED_FILES = {"manifest.json", "manifest.sha256", "decisions.csv", "run_state.json", "gate_request.json",
               "plan.json", "card_hashes.json", "packet.sha256", "packet_freeze.json", "lint_report.csv",
               "report.md", "report_status.json", "run_steps_report.json", "deletions.log",
               "00_preflight.json", "00_capability_probe.csv", "14_discovery_lock.json", "15_pin_plan.json",
               "secret_scan_report.json", "trace_report.md", "discover_plan.json", "discover_status.json",
               "11_value_map.csv", "13_table_decision.md"}
OWNED_PREFIXES = ("05_", "06_", "07_", "08_", "09_", "00_preflight.", "00_capability_probe.")
OWNED_DIRS = {"packet", "checks", "outputs", "executed", "samples", "trace", "gates", "spawn", "discovery_inputs"}
G1A_FILES = {"00_question.txt", "01_question_card.md", "run_config.json"}
G1B_FILES = {"02_metric_card.md", "03_output_contract.json", "11_value_map.csv", "13_table_decision.md"}


APPROVE_OUTCOMES = frozenset({"approve", "approved", "accept", "accepted", "signed"})


def _approved_in_run(run: Path, gate: str) -> bool:
    """Fix r4 C4-3: decisions.csv holds an approve row for `gate` in ANY attempt of the run that no later --supersede
    row replaced (rows are 1-based; `supersedes` names the row a supersede row replaces). Later attempts (P5 rework,
    P6 escalation) inherit the approval; they never repeat G1a, so the freeze holds for the rest of the run."""
    try:
        with open(run / "decisions.csv", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        return False
    replaced = set()
    for r in rows:
        try:
            replaced.add(int(float(r.get("supersedes") or "")))
        except ValueError:
            pass
    return any(r.get("gate") == gate and str(r.get("outcome") or "").strip().lower() in ("approve", "approved")
               and i not in replaced for i, r in enumerate(rows, 1))


REWORK_ROUTES = {("G8", "rewrite"), ("G8", "change_course"), ("G9", "rewrite"), ("G10", "rewrite"),
                 ("G11", "split_finer"), ("G11", "reject")}  # lib.state.REWORK_ROUTES (hooks stay stdlib)


def _rework_open(run: Path) -> bool:
    """Fix r4 C4-14: the run state is at P4 and decisions.csv holds a decision of its attempt that routes back to P4,
    so the corrected B.sql may be written (packet_freeze.py --freeze B --new-attempt re-freezes it)."""
    st = hl.run_state(run) or {}
    if st.get("phase") != "P4":
        return False
    try:
        attempt = int(st.get("attempt") or 1)
        with open(run / "decisions.csv", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
    except (OSError, TypeError, ValueError):
        return False
    for r in rows:
        try:
            ra = int(float(r.get("attempt") or 1))
        except ValueError:
            ra = 1
        if ra == attempt and (r.get("gate"), str(r.get("outcome") or "").strip().lower()) in REWORK_ROUTES:
            return True
    return False


def _decided(run: Path, gate: str) -> bool:
    """decisions.csv holds an approve-class row for `gate` (approve, accept, signed; fix r1 B-LOW)."""
    try:
        with open(run / "decisions.csv", encoding="utf-8", newline="") as f:
            return any(r.get("gate") == gate and str(r.get("outcome") or "").strip().lower() in APPROVE_OUTCOMES
                       for r in csv.DictReader(f))
    except OSError:
        return False


def protected(run: Path, rel: Path) -> str | None:
    """Why a run-relative path may not be edited by hand, or None."""
    parts = tuple(x.lower() for x in rel.parts)  # paths are compared case-insensitively (Windows)
    name = parts[-1] if parts else ""
    if not parts:
        return None
    if parts[0] in OWNED_DIRS:
        return f"{parts[0]}/ is script-owned"
    if len(parts) == 1:
        if name in OWNED_FILES or name.startswith(OWNED_PREFIXES):
            return f"{name} is script-owned"
        if name.endswith(".schema.json"):
            return f"{name} is a CSV sidecar written by the CSV contract writer"
        rec = hl.read_json(run / "packet_freeze.json") or {}
        if name in ("a.sql", "b.sql") and isinstance(rec, dict) and rec.get(name[0].upper()) \
                and not (name == "b.sql" and _rework_open(run)):
            return f"{name[0].upper()}.sql is frozen (packet_freeze.json records its hash)"
        hashes = hl.read_json(run / "card_hashes.json") or {}
        if name in G1A_FILES and isinstance(hashes, dict) and hashes.get("G1a") and _approved_in_run(run, "G1a"):
            return f"{name} is frozen at G1a (approved; a changed card is a new run)"
        if name in G1B_FILES and isinstance(hashes, dict) and hashes.get("G1b"):
            return f"{name} is frozen at G1b"
    if parts[0] == "steps" and _decided(run, "G11"):
        return "steps/ are frozen after G11 (spec 1.5); rework goes through P5 (split.py) with a new attempt"
    return None


def main() -> None:
    d = hl.read_payload()
    ti = d.get("tool_input") or {}
    raw = ti.get("file_path") or ti.get("notebook_path") or ti.get("path")
    if not raw:
        hl.allow()
    target = hl.resolve(str(raw), d.get("cwd"))
    active = hl.active_run()
    if active is not None and not hl.inside(target, active) and not hl.inside(target, hl.PLUGIN_ROOT / "tests"):
        hl.deny(f"{target.name} is outside the active run folder {active.name}; during a run only the run folder "
                "(and the plugin's tests/) may be written")
    run = active if active is not None and hl.inside(target, active) else hl.run_root_of(target)
    if run is not None:
        why = protected(run, Path(hl.norm(target)).relative_to(hl.norm(run)))
        if why:
            hl.deny(why + "; scripts write it, Max decides at the gate (decide.py records the decision)")
    hl.allow()


if __name__ == "__main__":
    main()

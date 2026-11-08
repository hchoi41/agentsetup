"""run_state.json read/write and the phase check (design spec v1 sections 1.3, 1.5 and 2).

Every phase skill's first action is `state.py check --phase Pn`; it exits 2 (REFUSED) when the
orchestrator is elsewhere, so an accidental trigger does nothing (spec 1.3). Every script also
exits 2 without a valid state file, because the Bash hook cannot see inside a Python script
(spec 1.5). A phase check passes only when the state names that phase and its status is
`pending` or `in_progress`.

The state file is written atomically as UTF-8 without BOM, LF (D-04 file rules).

CLI (exit codes per spec 1.4):
  python -I -X utf8 scripts/lib/state.py check --phase P5 [--state run_state.json]
  python -I -X utf8 scripts/lib/state.py show [--state ...]
  python -I -X utf8 scripts/lib/state.py init --run-id ID [--level 60] [--state ...]
  python -I -X utf8 scripts/lib/state.py set --phase P6 --status in_progress [--state ...]
  python -I -X utf8 scripts/lib/state.py set --blocked "<script> exit 2: <reason>" [--state ...]
  python -I -X utf8 scripts/lib/state.py activate --run-dir RUN     (the hooks' active run, spec 1.5)
  python -I -X utf8 scripts/lib/state.py deactivate

`set --phase` (fix brief r0 item 1) refuses (exit 2) a forward move of more than one phase, and leaving a phase
whose per-run gates (PER_RUN_GATES: P0 G0a G0b; P1 G1a; P2 G2 G3 G1b; P5 G11; P8 G12; P10 G5) have no
closing decisions.csv row for this attempt (fix r14: a row with an empty check_id whose outcome is in the gate's
CLOSING_OUTCOMES, not superseded by a later row; a check-id row, a trap resolution or a lone reject, stop or decline
never closes the phase gate); a backward move (re-work) is allowed and sets the status to pending. Fix r4
C4-14: a move back to P4 from P5 or later needs a decision of this attempt that routes back to P4 (REWORK_ROUTES:
G8 rewrite or change_course, G9 rewrite, G10 rewrite, G11 split_finer or reject).
GATE_PHASES, ORCHESTRATOR_GATES and REQUEST_GATES below are the gate maps decide.py enforces.

`set --blocked REASON` (fix r2, walk W7) leaves the phase in_progress after a script exited 2 and records the reason
(blocked_reason, blocked_utc); the Stop hook then lets the turn end while Max fixes the cause. Any later `set`
clears it.

`activate` writes <QT_HOME>/active_run.json (QT_HOME defaults to %USERPROFILE%/.query-trail, beside the
env files): the hooks then confine writes to that run folder and the Stop hook checks its open gates.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.csv_contract import write_json_lf  # noqa: E402
from lib.exit_codes import ExitCode, RefusedError  # noqa: E402
from lib.levels import LevelError, parse_level  # noqa: E402

STATE_FILE = "run_state.json"
SCHEMA_VERSION = "1"
PHASES: tuple[str, ...] = tuple(f"P{i}" for i in range(12))
STATUSES: tuple[str, ...] = ("pending", "in_progress", "waiting_gate", "done", "failed")
ACTIVE: frozenset[str] = frozenset({"pending", "in_progress"})

# ---------------------------------------------------------------- gates (spec 2.1; fix brief r0 item 1)
# The one gate -> phase map; decide.py imports it. Spec 2.1 table, plus three phases where a documented flow
# asks the gate: G8 at P0 (preflight self-test surprise, skills/preflight) and P4 (author loop does not
# converge, spec 2.2 P4 "(G8)"); G15 at P2, P8 and P9, where discover.py and warehouse_jobs.py open it (P8:
# the per-run budget ledger stops trace lookups, fix r1 B-H3; spec v2 2.1); G15 at P7 (reconcile.py, a comparison
# INCONCLUSIVE because of the stream cap) and G4 at P9 (a reference difference found by the tie-out), fix r1;
# G13 at P2 (discover.py: freshness disagreement on a locked table, fix r3 B3-11).
GATE_PHASES: dict[str, tuple[str, ...]] = {
    "G0a": ("P0",), "G0b": ("P0",), "G1a": ("P1",), "G6": ("P1", "P11"),
    "G2": ("P2",), "G3": ("P2",), "G1b": ("P2",),
    "G8": ("P0", "P2", "P4", "P6", "P7", "P8", "P9"), "G13": ("P2", "P3", "P9"),
    "G9": ("P5",), "G10": ("P5",), "G11": ("P5",), "G15": ("P2", "P6", "P7", "P8", "P9"),
    "G4": ("P7", "P9"), "G12": ("P8",), "G5": ("P10",), "G16": ("P11",),
}
# Per-run gates: a phase cannot be left (state.py set --phase) without a decisions.csv row for each, this attempt.
PER_RUN_GATES: dict[str, tuple[str, ...]] = {
    "P0": ("G0a", "G0b"), "P1": ("G1a",), "P2": ("G2", "G3", "G1b"), "P5": ("G11",), "P8": ("G12",), "P10": ("G5",)}
# Fix r14: the outcomes that close each per-run gate (the run skill's gate table, decide.py's G12 signing words).
# Only a row with an empty check_id closes it; reject, stop, decline and check-id rows never do.
CLOSING_OUTCOMES: dict[str, frozenset[str]] = {
    "G0a": frozenset({"approve"}), "G0b": frozenset({"accept"}), "G1a": frozenset({"approve"}),
    "G2": frozenset({"approve"}), "G3": frozenset({"approve"}), "G1b": frozenset({"approve"}),
    "G11": frozenset({"approve", "accept", "signed"}), "G12": frozenset({"signed", "approve", "accept"}),
    "G5": frozenset({"approve", "skip"})}
# Asked by the orchestrator without a script request: answerable without one, once per gate per attempt
# (a second row needs --supersede).
ORCHESTRATOR_GATES: frozenset[str] = frozenset({"G0a", "G0b", "G1a", "G2", "G3", "G1b", "G11", "G12", "G5"})
# (gate, phase) pairs that a script opens through gate_request.json (exit 3): answerable only while it is open.
REQUEST_GATES: frozenset[tuple[str, str]] = frozenset({
    ("G6", "P1"), ("G6", "P11"), ("G10", "P5"), ("G13", "P2"), ("G13", "P3"), ("G15", "P2"), ("G15", "P6"), ("G15", "P7"),
    ("G15", "P8"), ("G15", "P9")})
# Every other gate at its phases (G8, G9, G4, G16, G13 at P9) is conditional: answerable, repeatable.


class StateError(RefusedError):
    """Missing, unreadable or invalid run_state.json (exit 2)."""


@dataclasses.dataclass(frozen=True)
class RunState:
    """The orchestrator's position: which phase, its status, level and attempt."""

    run_id: str
    phase: str
    phase_status: str
    level: int
    attempt: int
    updated_utc: str
    schema_version: str = SCHEMA_VERSION
    blocked_reason: str | None = None  # set by `set --blocked` after an exit 2 (fix r2 W7); cleared by any `set`
    blocked_utc: str | None = None

    def validate(self) -> "RunState":
        """Raise StateError when a field is out of range; return self."""
        if not self.run_id or len(self.run_id) > 64:
            raise StateError(f"invalid run_id {self.run_id!r}")
        if self.phase not in PHASES:
            raise StateError(f"invalid phase {self.phase!r}")
        if self.phase_status not in STATUSES:
            raise StateError(f"invalid phase_status {self.phase_status!r}")
        if self.attempt < 1:
            raise StateError(f"invalid attempt {self.attempt!r}")
        if self.schema_version != SCHEMA_VERSION:
            raise StateError(f"unsupported schema_version {self.schema_version!r}")
        try:
            parse_level(self.level)
        except LevelError as exc:
            raise StateError(f"invalid level in run state: {exc}") from exc
        return self


@dataclasses.dataclass(frozen=True)
class CheckResult:
    """Phase-check outcome: exit code and a one-line reason."""

    code: ExitCode
    reason: str


def utc_now() -> str:
    """Current UTC time, ISO 8601 with a Z suffix, second precision."""
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def state_path(run_dir: Path | str | None = None) -> Path:
    """Path of run_state.json inside a run folder (default: the current directory)."""
    return Path(run_dir or ".") / STATE_FILE


def read_state(path: Path) -> RunState:
    """Load and validate run_state.json; raise StateError on any problem."""
    p = Path(path)
    if not p.is_file():
        raise StateError(f"no valid run state: {p} does not exist")
    try:
        raw: dict[str, Any] = json.loads(p.read_text(encoding="utf-8-sig"))
        st = RunState(str(raw["run_id"]), str(raw["phase"]), str(raw["phase_status"]), int(raw["level"]),
                      int(raw["attempt"]), str(raw["updated_utc"]), str(raw.get("schema_version", "")),
                      raw.get("blocked_reason") or None, raw.get("blocked_utc") or None)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise StateError(f"no valid run state: {p}: {exc}") from exc
    return st.validate()


def write_state(path: Path, state: RunState) -> None:
    """Validate and write run_state.json atomically."""
    write_json_lf(Path(path), dataclasses.asdict(state.validate()))


def new_state(run_id: str, level: Any = 60) -> RunState:
    """A fresh state at P0, pending, attempt 1."""
    return RunState(run_id, "P0", "pending", parse_level(level), 1, utc_now()).validate()


def update_state(state: RunState, **changes: Any) -> RunState:
    """Return a copy with `changes` applied and a fresh timestamp, validated."""
    if "level" in changes:
        changes["level"] = parse_level(changes["level"])
    return dataclasses.replace(state, updated_utc=utc_now(), **changes).validate()


def check_phase(path: Path, phase: str) -> CheckResult:
    """PASS when the orchestrator is at `phase` (pending or in progress); REFUSED otherwise."""
    if phase not in PHASES:
        return CheckResult(ExitCode.REFUSED, f"unknown phase {phase!r}")
    try:
        st = read_state(path)
    except StateError as exc:
        return CheckResult(ExitCode.REFUSED, str(exc))
    if st.phase != phase:
        return CheckResult(ExitCode.REFUSED, f"orchestrator is at {st.phase}, not {phase}: nothing ran")
    if st.phase_status not in ACTIVE:
        return CheckResult(ExitCode.REFUSED, f"{phase} is {st.phase_status}, not pending or in_progress: nothing ran")
    return CheckResult(ExitCode.PASS, f"{phase} {st.phase_status} (run {st.run_id}, level {st.level})")


# ---------------------------------------------------------------- active run pointer (hooks, spec 1.5)
def qt_home() -> Path:
    """<QT_HOME> or %USERPROFILE%/.query-trail (the folder that also holds env/<env>.json, D-12)."""
    if os.environ.get("QT_HOME"):
        return Path(os.environ["QT_HOME"])
    return Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(Path.home())) / ".query-trail"


def activate(run_dir: Path, home: Path | None = None) -> Path:
    """Point the hooks at `run_dir` (it must hold a valid run_state.json); return the pointer path.

    `home` (run_init.py --home) overrides <QT_HOME>; the hooks read QT_HOME, so the two must agree.
    """
    st = read_state(state_path(run_dir))
    p = Path(home or qt_home()) / "active_run.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    write_json_lf(p, {"run_dir": str(Path(run_dir).resolve()), "run_id": st.run_id, "activated_utc": utc_now()})
    return p


def deactivate() -> None:
    """Remove the active-run pointer (the hooks fall back to protecting run folders only)."""
    p = qt_home() / "active_run.json"
    if p.is_file():
        p.unlink()


# ---------------------------------------------------------------- CLI
def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="state.py", description="query-trail run_state.json (spec 1.3)")
    ap.add_argument("--state", default=STATE_FILE, help="path to run_state.json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").add_argument("--phase", required=True)
    sub.add_parser("show")
    i = sub.add_parser("init")
    i.add_argument("--run-id", required=True)
    i.add_argument("--level", default="60")
    sub.add_parser("activate").add_argument("--run-dir", required=True)
    sub.add_parser("deactivate")
    s = sub.add_parser("set")
    s.add_argument("--phase")
    s.add_argument("--status", choices=STATUSES)
    s.add_argument("--level")
    s.add_argument("--attempt", type=int)
    s.add_argument("--blocked", metavar="REASON",
                   help="after a script exited 2: keep the phase in_progress and record the reason (the Stop hook "
                        "then lets the turn end); any later set clears it")
    return ap


def missing_gates(run_dir: Path, phase: str, attempt: int) -> list[str]:
    """Per-run gates of `phase` with no closing decisions.csv row for `attempt` (a row without attempt counts as 1).

    Fix r14: a row closes its gate only when its check_id is empty, its outcome is in CLOSING_OUTCOMES[gate] and no
    later row supersedes it (a G1a approve replaced by a reject --supersede no longer closes G1a)."""
    from lib import runctx  # local import: runctx imports this module

    rows = runctx.load_decisions(Path(run_dir))
    replaced = {int(float(r["supersedes"])) for r in rows if str(r.get("supersedes") or "").strip() not in ("", "nan")}
    have = {str(r.get("gate")) for i, r in enumerate(rows, 1)
            if int(r.get("attempt") or 1) == attempt and i not in replaced
            and str(r.get("check_id") or "").strip() in ("", "nan")
            and str(r.get("outcome", "")).strip().lower() in CLOSING_OUTCOMES.get(str(r.get("gate")), frozenset())}
    return [g for g in PER_RUN_GATES.get(phase, ()) if g not in have]


REWORK_ROUTES: frozenset[tuple[str, str]] = frozenset({  # fix r4 C4-14: decisions that route a run back to P4
    ("G8", "rewrite"), ("G8", "change_course"), ("G9", "rewrite"), ("G10", "rewrite"), ("G11", "split_finer"),
    ("G11", "reject")})


def rework_row(run_dir: Path, attempt: int) -> int | None:
    """Fix r4 C4-14: the 1-based decisions.csv row of the latest decision of `attempt` that routes back to P4 (G8
    rewrite or change_course, G9 rewrite, G10 rewrite, G11 split_finer or reject), else None."""
    from lib import runctx  # local import: runctx imports this module

    hits = [i for i, r in enumerate(runctx.load_decisions(Path(run_dir)), 1)
            if (str(r.get("gate")), str(r.get("outcome", "")).strip().lower()) in REWORK_ROUTES
            and int(r.get("attempt") or 1) == attempt]
    return hits[-1] if hits else None


def check_transition(path: Path, st: RunState, target: str) -> bool:
    """Refuse a forward move of more than one phase, or leaving a phase whose per-run gates have no closing row.

    Returns True for a backward move (re-work), which the caller records as pending.
    """
    if target not in PHASES:
        raise StateError(f"invalid phase {target!r}")
    cur, nxt = PHASES.index(st.phase), PHASES.index(target)
    if nxt < cur:
        if target == "P4" and cur >= PHASES.index("P5") and rework_row(Path(path).parent, st.attempt) is None:
            raise StateError(f"{st.phase} -> P4 needs a decision that routes back to P4 in attempt {st.attempt} "
                             "(G8 rewrite or change_course, G9 rewrite, G10 rewrite, G11 split_finer or reject); "
                             "record it with decide.py first")
        return True
    if nxt > cur + 1:
        raise StateError(f"{st.phase} -> {target} skips {', '.join(PHASES[cur + 1:nxt])}: one phase at a time")
    if nxt == cur + 1:
        miss = missing_gates(Path(path).parent, st.phase, st.attempt)
        if miss:
            raise StateError(f"cannot leave {st.phase}: no closing decisions.csv row for {', '.join(miss)} in attempt "
                             f"{st.attempt} (a row without --check-id whose outcome closes the gate: "
                             + "; ".join(f"{g} {' or '.join(sorted(CLOSING_OUTCOMES[g]))}" for g in miss)
                             + "; record them with decide.py)")
    return False


def _set(path: Path, a: argparse.Namespace) -> None:
    changes: dict[str, Any] = {k: v for k, v in (("phase", a.phase), ("phase_status", a.status), ("level", a.level),
                                                 ("attempt", a.attempt)) if v is not None}
    st = read_state(path)
    if a.blocked is not None:
        if changes or st.phase_status != "in_progress" or not a.blocked.strip():
            raise StateError("--blocked records why a phase in_progress stopped (a script exited 2); it takes a "
                             "reason and no other change, and the phase must be in_progress")
        write_state(path, update_state(st, blocked_reason=a.blocked.strip(), blocked_utc=utc_now()))
        return
    changes.update(blocked_reason=None, blocked_utc=None)
    if a.phase is not None and check_transition(path, st, a.phase):
        changes["phase_status"] = "pending"
        sys.stderr.write(f"state.py: {st.phase} -> {a.phase} is a move backward (re-work): status pending\n")
    write_state(path, update_state(st, **changes))


def _cli(argv: Sequence[str]) -> int:
    a = _parser().parse_args(argv)
    path = Path(a.state)
    if a.cmd == "check":
        res = check_phase(path, a.phase)
        (sys.stdout if res.code == ExitCode.PASS else sys.stderr).write(res.reason + "\n")
        return int(res.code)
    try:
        if a.cmd == "show":
            print(json.dumps(dataclasses.asdict(read_state(path)), ensure_ascii=False))
        elif a.cmd == "activate":
            print(f"active run: {activate(Path(a.run_dir))}")
        elif a.cmd == "deactivate":
            deactivate()
        elif a.cmd == "init":
            if path.exists():
                raise StateError(f"{path} already exists; a new run needs a new folder")
            write_state(path, new_state(a.run_id, a.level))
        else:
            _set(path, a)
    except RefusedError as exc:
        sys.stderr.write(f"REFUSED: {exc}\n")
        return int(exc.exit_code)
    return int(ExitCode.PASS)


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))

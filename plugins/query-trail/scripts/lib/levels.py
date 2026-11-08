"""Confidence ladder: load config/levels.json, plan what a step writes, decide replayability.

Implements design spec v1 section 3.1 (the ladder table), 3.3 (accepted level inputs; anything
else exits 2) and 5.5 (a step is replayable only when every input is a retained full CSV; read
steps replay only by a warehouse re-run). Caps come from design decisions v1 section 4.

CLI (exit codes per spec 1.4):
  python -I -X utf8 scripts/lib/levels.py parse <level>
  python -I -X utf8 scripts/lib/levels.py plan --level <level> [--rows N] [--bytes B] [--deliverable]
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.exit_codes import ExitCode, RefusedError  # noqa: E402

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
LEVELS_PATH = PLUGIN_ROOT / "config" / "levels.json"


class LevelError(RefusedError):
    """An input that is not one of the accepted level names or numbers (exit 2, spec 3.3)."""


@dataclasses.dataclass(frozen=True)
class LevelSpec:
    """One rung of the ladder as encoded in levels.json."""

    level: int
    name: str
    step_artifacts: tuple[str, ...]
    checks: tuple[str, ...]
    replayable: str
    over_cap: str


@dataclasses.dataclass(frozen=True)
class Ladder:
    """The whole ladder: rungs, aliases, default, caps and the deliverable's artifacts."""

    default_level: int
    levels: Mapping[int, LevelSpec]
    aliases: Mapping[str, int]
    caps: Mapping[str, int]
    deliverable_artifacts: tuple[str, ...]
    sample_n: int
    digest_routes: Mapping[str, str]


@dataclasses.dataclass(frozen=True)
class StepPlan:
    """What one step writes at a level, after the cap rules are applied."""

    requested_level: int
    effective_level: int
    artifacts: tuple[str, ...]
    degraded: bool
    digest_route: str
    gate: str | None
    reason: str

    @property
    def retains_full_csv(self) -> bool:
        """True when the step's full CSV is kept (what replay needs)."""
        return "full_csv" in self.artifacts


@dataclasses.dataclass(frozen=True)
class StepRef:
    """The facts replayability depends on: is it a read step, and which steps feed it."""

    step_id: str
    is_read_step: bool
    inputs: tuple[str, ...]


def load_ladder(path: Path | None = None) -> Ladder:
    """Read levels.json into a Ladder."""
    raw: dict[str, Any] = json.loads(Path(path or LEVELS_PATH).read_text(encoding="utf-8-sig"))
    levels = {int(e["level"]): LevelSpec(int(e["level"]), str(e["name"]), tuple(e["step_artifacts"]),
                                         tuple(e["checks"]), str(e["replayable"]), str(e["over_cap"]))
              for e in raw["levels"]}
    return Ladder(int(raw["default_level"]), levels, {k: int(v) for k, v in raw["aliases"].items()},
                  {k: int(v) for k, v in raw["caps"].items()}, tuple(raw["deliverable_artifacts"]),
                  int(raw["sample_n"]), dict(raw["digest_routes"]))


def parse_level(value: Any, ladder: Ladder | None = None) -> int:
    """Accept 60/70/80/90/100 or quick/medium/high/extra high/max (case and spacing ignored)."""
    lad = ladder or load_ladder()
    if isinstance(value, bool):
        raise LevelError(f"not a confidence level: {value!r}")
    if isinstance(value, int):
        if value in lad.levels:
            return value
        raise LevelError(f"not a confidence level: {value!r}")
    text = " ".join(str(value).strip().lower().split())
    if text.isdigit() and int(text) in lad.levels:
        return int(text)
    if text in lad.aliases:
        return lad.aliases[text]
    raise LevelError(f"not a confidence level: {value!r}; use {', '.join(lad.aliases)} or "
                     + ", ".join(str(n) for n in sorted(lad.levels)))


def level_name(level: int, ladder: Ladder | None = None) -> str:
    """Name of a numeric level (60 -> 'quick')."""
    lad = ladder or load_ladder()
    return lad.levels[parse_level(level, lad)].name


def artifacts_for(level: Any, ladder: Ladder | None = None) -> tuple[str, ...]:
    """Nominal artifacts a (non-deliverable) step writes at a level, before cap rules."""
    lad = ladder or load_ladder()
    return lad.levels[parse_level(level, lad)].step_artifacts


def _over(rows: int | None, bytes_: int | None, max_rows: int, max_bytes: int) -> bool:
    return (rows is not None and rows > max_rows) or (bytes_ is not None and bytes_ > max_bytes)


def plan_step(level: Any, rows: int | None = None, bytes_: int | None = None, is_deliverable: bool = False,
              forced_retention: bool = False, ladder: Ladder | None = None) -> StepPlan:
    """Answer "what does this step write at level L" given its size (unknown size = nominal plan)."""
    lad = ladder or load_ladder()
    lv = parse_level(level, lad)
    caps = lad.caps
    over_stream = _over(rows, bytes_, caps["stream_rows"], caps["stream_bytes"])
    route = lad.digest_routes["warehouse" if over_stream else "local"]
    if is_deliverable:
        gate = "G15" if over_stream else None
        why = "deliverable output: full CSV at every level" + ("; over the stream cap: G15" if gate else "")
        return StepPlan(lv, lv, lad.deliverable_artifacts, False, route, gate, why)
    spec = lad.levels[lv]
    arts, eff, degraded, gate, why = spec.step_artifacts, lv, False, None, f"level {lv} {spec.name}"
    if lv == 90 and _over(rows, bytes_, caps["local_csv_rows_level_90"], caps["local_csv_bytes_level_90"]):
        arts, eff, degraded, why = lad.levels[80].step_artifacts, 80, True, "over the level-90 local cap: drops to 80"
    elif lv == 100 and over_stream:
        gate, why = "G15", "over the stream cap at level 100: G15 (filter, partitioned CSV, or accept 90)"
    elif over_stream:
        why += "; over the stream cap: digest computed warehouse-side"
    if forced_retention and "full_csv" not in arts:
        arts, why = arts + ("full_csv",), why + "; forced retention for a DuckDB cut (spec 5.3)"
    if rows is None and bytes_ is None:
        why += "; size unknown, nominal plan"
    return StepPlan(lv, eff, tuple(arts), degraded, route, gate, why)


def replayable(step: StepRef, plans: Mapping[str, StepPlan]) -> tuple[bool, str]:
    """Spec 5.5: replayable only when every input is a retained full CSV; read steps never locally."""
    if step.is_read_step:
        return False, "read step: replays only by a warehouse re-run with the same pin inside the window"
    missing = [i for i in step.inputs if i not in plans or not plans[i].retains_full_csv]
    if missing:
        return False, "inputs without a retained full CSV: " + ", ".join(missing)
    return True, "every input is a retained full CSV"


def replay_map(steps: Sequence[StepRef], plans: Mapping[str, StepPlan]) -> dict[str, tuple[bool, str]]:
    """Replayability of every step, keyed by step id (what the manifest records)."""
    return {s.step_id: replayable(s, plans) for s in steps}


# ---------------------------------------------------------------- CLI
def _cli(argv: Sequence[str]) -> int:
    ap = argparse.ArgumentParser(prog="levels.py", description="query-trail confidence ladder (spec 3)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("parse")
    p.add_argument("level")
    q = sub.add_parser("plan")
    q.add_argument("--level", required=True)
    q.add_argument("--rows", type=int)
    q.add_argument("--bytes", type=int, dest="bytes_")
    q.add_argument("--deliverable", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "parse":
            lv = parse_level(a.level)
            print(json.dumps({"level": lv, "name": level_name(lv)}))
        else:
            # Informational: a gate in the plan is reported, not opened (run_steps.py opens G15).
            print(json.dumps(dataclasses.asdict(plan_step(a.level, a.rows, a.bytes_, a.deliverable))))
    except RefusedError as exc:
        sys.stderr.write(f"REFUSED: {exc}\n")
        return int(exc.exit_code)
    return int(ExitCode.PASS)


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))

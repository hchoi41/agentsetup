"""`step:<ref>` card sources resolved against plan.json (design spec v5 2.2 P1, P5, P9; fix r4 C4-15).

The card may name a MECE or pop_bands source as `step:<ref>` at G1a, before split.py has named the steps. split names
a step `NN_<verb>_<object>` (e.g. 03_project_read_orders) and keeps the author's CTE name (`cte`, e.g. read_orders).
`<ref>` is accepted as the step's `name`, its `cte`, or its number (`3` or `03`). `match` returns the plan step's
`name`, or None; `unmatched` lists the card's step sources that match no step (split.py warns about them at P5,
logic_tests.py refuses them at P9).
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

PREFIX = "step:"


def match(steps: Sequence[Mapping[str, Any]], ref: str) -> str | None:
    """The plan step name that `ref` (name, CTE name or step number) names, else None."""
    r = str(ref).strip()
    for s in steps:
        if r == str(s.get("name")):
            return str(s.get("name"))
    for s in steps:
        if r == str(s.get("cte") or ""):
            return str(s.get("name"))
    if r.isdigit():
        for s in steps:
            try:
                if int(r) == int(s.get("step")):
                    return str(s.get("name"))
            except (TypeError, ValueError):
                continue
    return None


def card_step_refs(card: Mapping[str, Any]) -> list[tuple[str, str]]:
    """(where, ref) of every `step:<ref>` source in the card (mece[i], chain, pop_bands, reference_compare)."""
    out: list[tuple[str, Any]] = []
    mece = card.get("mece")
    if isinstance(mece, list):
        out += [(f"mece[{i}]", m.get("source")) for i, m in enumerate(mece, 1) if isinstance(m, Mapping)]
    for key in ("chain", "pop_bands", "reference_compare"):
        if isinstance(card.get(key), Mapping):
            out.append((key, card[key].get("source")))
    return [(w, str(s).strip()[len(PREFIX):].strip()) for w, s in out
            if isinstance(s, str) and s.strip().startswith(PREFIX)]


def unmatched(steps: Sequence[Mapping[str, Any]], refs: Iterable[tuple[str, str]]) -> list[str]:
    return [f"{w}: step:{r}" for w, r in refs if match(steps, r) is None]


def describe(steps: Sequence[Mapping[str, Any]]) -> str:
    return ", ".join(f"{s.get('name')} (cte {s.get('cte')}, {int(s.get('step') or 0):02d})" for s in steps) or "none"

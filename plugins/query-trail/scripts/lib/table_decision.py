"""13_table_decision.md: the packet's table decision, written by `discover.py decide` (design spec v4 2.2 P2; fix r3 B3-12).

The blind author sees only the packet (02_metric_card.md, 03_output_contract.json, this file, 11_value_map.csv), so
this file carries what B needs and nothing about the tables Max dropped beyond their names: the locked tables in
placeholder form `{{project}}.{{<alias>}}.<table>`, key and grain test per table, joins, filters with the exact values,
the date column and the period boundaries, the timezone, the columns the bindings name with their types, the
freshness outcome, the tables dropped at G2, and the pin route when 15_pin_plan.json exists.

Fixed headings (skills quote them): # Table decision, ## Tables, ## Joins, ## Filters and values,
## Dates and period, ## Timezone, ## Columns named by the bindings, ## Freshness, ## Dropped at G2, ## Pin route,
## Sources.
Refusals (exit 2, nothing written): no 14_discovery_lock.json (run discover.py lock first), a card binding or a
value-map row naming a table outside the lock, an invalid value map.

Fix r4 C4-12 (cold reviewer r4): the blind author applies what it reads here, and B and its steps would share a wrong
filter, so only what the query must apply is written as such: "## Filters and values" lists filter, map and derive
rows and the bindings' measure filters; "## Dates and period" lists the card period, the bindings' date column, and a
period row only when it names that very date column. Every other period row (one that settled a freshness question)
and every not_used row is listed under "## Freshness", marked "settled; not a filter". The dropped tables keep their
names under "## Dropped at G2" with the reason they are listed (so B never reads them).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from lib import runctx as c2, valuemap
from lib.csv_contract import write_text_lf
from lib.exit_codes import RefusedError

OUT = "13_table_decision.md"
HEADINGS = ("# Table decision", "## Tables", "## Joins", "## Filters and values", "## Dates and period", "## Timezone",
            "## Columns named by the bindings", "## Freshness", "## Dropped at G2", "## Pin route", "## Sources")


def placeholder(ref: str) -> str:
    """`ds.t`, `{{ds}}.t` or `{{project}}.{{ds}}.t` -> `{{project}}.{{ds}}.t` (D-12: no identifier)."""
    parts = [p.strip("`{} ") for p in str(ref).replace("`", "").split(".")]
    if len(parts) == 3 and parts[0] == "project":
        parts = parts[1:]
    return "{{project}}.{{" + parts[0] + "}}." + parts[1] if len(parts) == 2 else str(ref)


def _cell(v: Any) -> str:
    s = "" if v is None else str(v)
    return s.replace("|", "\\|").replace("\n", " ") or "-"


def _table(head: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(_cell(x) for x in r) + " |" for r in rows]
    return out


def _idents(text: Any) -> set[str]:
    return {w.lower() for w in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", str(text or ""))}


def build(run_dir: Path) -> str:
    """The markdown text (raises RefusedError when an input is missing or outside the lock)."""
    run_dir = Path(run_dir)
    lock_p = run_dir / "14_discovery_lock.json"
    if not lock_p.is_file():
        raise RefusedError("14_discovery_lock.json missing: after G2 run discover.py --run-dir RUN lock --tables "
                           "<the tables Max kept>, then discover.py --run-dir RUN decide")
    from lib import g2lock
    g2lock.require_bound(run_dir, "13_table_decision.md")  # fix r4 C4-4: only from a lock bound to G2
    lock = c2.read_input_json(lock_p, lock_p.name)
    locked = [str(r) for r in lock.get("tables", {})]
    keep = {valuemap._bare(r) for r in locked}
    vm = valuemap.read(run_dir)
    probs = valuemap.problems(vm, run_dir)
    if probs:
        raise RefusedError("11_value_map.csv is not valid (value_map.py validate): " + "; ".join(probs[:6]))
    card = c2.card_fields(run_dir / "02_metric_card.md") if (run_dir / "02_metric_card.md").is_file() else {}
    b = card.get("bindings") if isinstance(card.get("bindings"), dict) else {}
    outside = sorted({str(t) for t in (b.get("tables") or []) if isinstance(t, str) and valuemap._bare(t) not in keep})
    if outside:
        raise RefusedError("card bindings name table(s) outside the G2 lock: " + ", ".join(outside))
    terms = c2.read_input_json(run_dir / "discovery_terms.json") if (run_dir / "discovery_terms.json").is_file() else {}
    short = {valuemap._bare(r.get("table_ref")): r for r in c2.read_records_if_exists(run_dir / "08_shortlist.csv")
             if r.get("table_ref") and r.get("rank") is not None}
    types: dict[tuple[str, str], str] = {}
    for c in c2.read_records_if_exists(run_dir / "05_catalog.csv"):
        if c.get("table_ref") and c.get("column_name"):
            types[(valuemap._bare(c["table_ref"]), str(c["column_name"]).lower())] = str(c.get("data_type") or "")
    lines = [HEADINGS[0], "",
             "Written by `discover.py decide` from the G2 lock, the profiles, the G3 value map and the metric card "
             "bindings. Use only these tables, in this placeholder form. Apply exactly the filters under "
             "\"Filters and values\" and the row-date boundaries under \"Dates and period\"; the rows under "
             "\"Freshness\" are settled data-owner questions, not filters.", ""]
    # tables
    rows = []
    for ref in locked:
        e = lock["tables"][ref] or {}
        s = short.get(valuemap._bare(ref), {})
        rows.append((placeholder(ref), ", ".join(e.get("key_hypothesis") or []) or "none declared",
                     s.get("grain_test") or "not run", e.get("date_column") or "none",
                     e.get("table_type") or "-"))
    lines += [HEADINGS[1], ""] + _table(("table (as written in SQL)", "key / grain", "grain test", "date column",
                                         "type"), rows) + [""]
    # joins
    joins = b.get("joins") if isinstance(b.get("joins"), list) else []
    lines += [HEADINGS[2], ""]
    if joins:
        lines += _table(("left", "right", "on (left column = right column)"),
                        [(placeholder(str(j.get("left"))), placeholder(str(j.get("right"))),
                          "; ".join(f"{p[0]} = {p[1]}" for p in (j.get("on_keys") or j.get("on") or j.get(True) or [])
                                    if isinstance(p, list) and len(p) == 2))
                         for j in joins if isinstance(j, dict)])
    else:
        lines.append("none: the bindings name no join (one table, or joins not yet bound at G1b).")
    lines.append("")
    # filters and values
    fv = [r for r in vm if str(r.get("kind")) in ("filter", "map", "derive")]
    measures = b.get("measures") if isinstance(b.get("measures"), dict) else {}
    mf = [(name, m.get("table"), m.get("filter")) for name, m in measures.items() if isinstance(m, dict) and m.get("filter")]
    lines += [HEADINGS[3], ""]
    if fv or mf:
        lines += _table(("kind", "table", "column", "exact value or condition", "reason"),
                        [(r.get("kind"), placeholder(str(r.get("table_ref"))) if r.get("table_ref") else "run-wide",
                          r.get("column_name"), r.get("value"), r.get("reason")) for r in fv]
                        + [("measure filter", placeholder(str(t)) if t else "-", name, flt, "card bindings.measures")
                           for name, t, flt in mf])
    else:
        lines.append("none: no filter, map or derive row in 11_value_map.csv and no measure filter in the bindings.")
    lines.append("")
    # dates and period
    p = terms.get("period") or {}
    d = b.get("date") if isinstance(b.get("date"), dict) else {}
    lines += [HEADINGS[4], ""]
    lines.append(f"- Period: {p.get('start')} to {p.get('end')}, both ends inclusive." if p.get("start") and p.get("end")
                 else "- Period: not given in discovery_terms.json.")
    if d:
        lines.append(f"- Row date: {placeholder(str(d.get('table')))}.{d.get('column')}, truncated to "
                     f"{d.get('truncation')} (card bindings.date).")
    date_key = (valuemap._bare(d.get("table")), str(d.get("column") or "").lower()) if d.get("table") else None
    settled = []  # fix r4 C4-12: period rows on other columns settled freshness; they are not filters
    for r in vm:
        if str(r.get("kind")) != "period":
            continue
        if date_key and (valuemap._bare(r.get("table_ref")), str(r.get("column_name") or "").lower()) == date_key:
            lines.append(f"- {placeholder(str(r.get('table_ref')))}.{r.get('column_name')}: {r.get('value')} "
                         f"({r.get('reason')}; value map {r.get('row_id')})")
        else:
            settled.append(r)
    lines.append("")
    # timezone
    lines += [HEADINGS[5], ""]
    tz = [r for r in vm if str(r.get("kind")) == "timezone"]
    for r in tz:
        where = "run-wide (its own G3 row)" if str(r.get("scope")) == "run" else \
            f"{placeholder(str(r.get('table_ref')))}.{r.get('column_name')}"
        lines.append(f"- {where}: {r.get('value')} ({r.get('reason')})")
    if d.get("timezone"):
        lines.append(f"- bindings.date: {d.get('timezone')}")
    if not tz and not d.get("timezone"):
        lines.append("- none decided: timestamps are used as stored.")
    lines.append("")
    # columns named by the bindings
    named: dict[tuple[str, str], str] = {}
    for name, m in measures.items():
        if isinstance(m, dict) and m.get("table"):
            for w in _idents(m.get("expr")) | _idents(m.get("filter")):
                key = (valuemap._bare(m["table"]), w)
                if key in types:
                    named.setdefault(key, f"measure {name}")
    for j in joins:
        if isinstance(j, dict):
            for pair in j.get("on_keys") or j.get("on") or j.get(True) or []:
                if isinstance(pair, list) and len(pair) == 2:
                    for side, col in ((j.get("left"), pair[0]), (j.get("right"), pair[1])):
                        key = (valuemap._bare(side), str(col).lower())
                        named.setdefault(key, "join key")
    if d.get("table") and d.get("column"):
        named.setdefault((valuemap._bare(d["table"]), str(d["column"]).lower()), "row date")
    lines += [HEADINGS[6], ""]
    if named:
        lines += _table(("table", "column", "type", "used as"),
                        [(placeholder(t), c, types.get((t, c), "not in 05_catalog.csv"), why)
                         for (t, c), why in sorted(named.items())])
    else:
        lines.append("none yet: the metric card has no bindings (G1b binds them).")
    lines.append("")
    # freshness
    lines += [HEADINGS[7], ""]
    lines += _table(("table", "business date", "completeness", "outcome"),
                    [(placeholder(ref), short.get(valuemap._bare(ref), {}).get("freshness_business") or "not run",
                      short.get(valuemap._bare(ref), {}).get("freshness_completeness") or "not run",
                      short.get(valuemap._bare(ref), {}).get("freshness_agree") or "not run") for ref in locked])
    for r in settled + [x for x in vm if str(x.get("kind")) == "not_used"]:
        what = "not used by the query" if str(r.get("kind")) == "not_used" else f"period {r.get('value')}"
        # fix r5 (cold reviewer LOW): the row's reason is not Max's words; it is not quoted here
        lines.append(f"- {placeholder(str(r.get('table_ref')))}.{r.get('column_name')}: {what} "
                     f"(value map {r.get('row_id')}): settled; not a filter")
    lines.append("")
    dropped = [str(r) for r in lock.get("dropped_at_g2") or []]
    lines += [HEADINGS[8], ""] + ([f"- {r}: dropped at G2" for r in dropped] or ["- none"])
    if dropped:
        lines.append("- These names are listed only so that B never reads these tables; use only the tables above.")
    lines.append("")
    pin = run_dir / "15_pin_plan.json"
    lines += [HEADINGS[9], ""]
    if pin.is_file():
        pp = c2.read_input_json(pin, pin.name)
        lines.append(f"- route {pp.get('route')}, status {pp.get('pin_status')}; as_of_utc is fixed at P6 start.")
    else:
        lines.append("- not known yet (P3 writes 15_pin_plan.json); steps read the tables as written above, the "
                     "pin is injected by compile.py.")
    lines += ["", HEADINGS[10], "",
              f"- 14_discovery_lock.json lock_sha256 {lock.get('lock_sha256')}",
              "- 11_value_map.csv rows: " + (", ".join(str(r.get("row_id")) for r in vm) or "none"), ""]
    return "\n".join(lines)


def write(run_dir: Path) -> Path:
    """Write 13_table_decision.md (UTF-8, LF) and return its path."""
    text = build(run_dir)
    p = Path(run_dir) / OUT
    write_text_lf(p, text)
    return p

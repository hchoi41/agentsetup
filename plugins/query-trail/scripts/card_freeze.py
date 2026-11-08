"""card_freeze.py: hash the question and metric cards at G1a and G1b (design spec v1 sections 1.4, 2.1, 2.2 P1 and P2).

G1a (phase P1) hashes the card intent: the question from 01_question_card.md, the metric card
fields metric, populations, ratio type, chain, aggregation weight, reference type, tolerance,
headline field, confidence level, author mode and sample N (spec 2.1 G1a), and run_config.json,
which is "fixed inside the G1a hash" (spec 1.6). G1b (phase P2) re-checks that the intent hash
is unchanged (a card changed after G1a is a new run, exit 1), refuses while 09_traps.csv holds
an unresolved blocking trap (DB-06, exit 2), and hashes the bindings (tables, columns, SCD2
flags, semi-additive columns, reference extract) with 03_output_contract.json (DB-02).

A ratio metric (ratio_type other than none) whose chain is 'none' is refused (exit 2, nothing written; fix r1
A-M4): the 톱니 사슬 chain test would otherwise drop out silently.

Fix r2 A2-M8: completeness walks the chain block, not only the top-level fields. The chain needs `links` and
`method` (pooled | within_entity); each link needs `name`, `numerator`, `denominator`, `numerator_population` and
`denominator_population`; `final`, when given, is {numerator, denominator}; `entity`, when given, is a list of
columns; `source`, when given, is `deliverable` or a run-relative CSV. Each missing or invalid key is its own card_completeness.csv row (field `chain.links[2].numerator_population`,
1-based) whose detail is the key's plain-word meaning (MEANING, the same words as skills/card "What the fields
mean"), and the exit-3 request lists the same text so the run skill can ask Max for exactly those keys.
When RUN/00_question.txt (Max's words, saved by the run skill) exists, 01_question_card.md must quote it
(whitespace-insensitive); otherwise `question` is invalid.

Fix r3:
- A3-1: the G1a hash is provisional until Max approves. While decisions.csv has no G1a `approve` row (fix r4 C4-3: in
  any attempt of the run; a row superseded by a later --supersede row does not count), a changed card is re-hashed and the
  previous hash is kept in card_hashes.json `G1a.superseded[]` ({intent_hash, hashed_utc, superseded_utc}). After the
  approve row, a changed card is refused (exit 1: a card changed after its approval is a new run). On exit 0 a
  gate_request.json of the same gate, left by an earlier exit 3, is archived to gates/stale_<gate>_request_<time>.json.
- A3-2: every `mece[i]` needs `source` in one of the three forms (`deliverable`, a run-relative CSV, `step:<name>`),
  a non-empty `universe`, and `parts`, or `part_column` with `domain`; `pop_bands` needs a `source` in the same
  forms. Each missing or invalid key is its own row with its plain-word meaning. `step:<name>` is resolved at P9
  (logic_tests.py) against plan.json; at G1a only its form is checked, because plan.json does not exist yet.
- A3-3: `headline.claim` is required when `headline.field` is set: one of report.STRENGTH's verdict words or `none`.
- dec L2: the sha256 of 00_question.txt is part of the G1a payload (`question_text_sha256`) and listed in `fields`.
- A3-5 (conductor addendum, walk W33): G1b validates 03_output_contract.json (against config/output_contract.schema.json
  when that file exists, else the minimum shape: `grain` list, `columns` [{name, type, nullable}] with type one of
  CONTRACT_TYPES, `key` list of column names (`keys` read as the older name), `headline` naming a column) and the
  card's `bindings` block (`tables` in placeholder form, `joins` [{left, right, on: [[lcol, rcol]]}], `measures`
  {name: {expr, table[, filter]}}, `derived` {name: expr over measure names}, `date` {table, column, truncation,
  timezone}); every contract column is a measure, a derived name or a grain column; every measure, join and date
  table is in `tables`. Each problem is one card_completeness.csv row with its plain-word meaning; exit 3.

The card fields are the YAML front matter of each card file; Max writes and freezes the card,
this script only hashes and reports completeness (spec 1.3: the card skill never freezes).

Fix r11 L4 (live run 06): `aggregation_weight: not_applicable` means one entity. When the card's chain names its
entity columns (`chain.entity`, a non-empty list), the metric has one entity per value of those columns, so
not_applicable is invalid (exit 3, the row says "not_applicable is for a single entity; this metric has entities by
<columns>: choose pooled or equal_weighted"). A card without `chain.entity` keeps not_applicable.

Fix r14 (Max overturned the round-12 ruling; fix brief r14): the same question against the output contract, at G1b.
When 03_output_contract.json has a non-empty `grain` and the sealed card says `aggregation_weight: not_applicable`,
G1b is incomplete (exit 3) with the row `02_metric_card.md:aggregation_weight invalid` ("not_applicable (sealed at
G1a) but the output contract is grouped by <grain>; confirm on the record that no combined total is wanted, or start
a new run with pooled or equal_weighted"). A grouped output with no combined total is legitimate, so the row asks for a
recorded confirmation and never a card edit (the weight is in the G1a hash): a decisions.csv row at G1b with
`check_id aggregation_weight:not_applicable` and outcome `confirmed` (decide.py; any attempt; not superseded) clears
it, and card_hashes.json G1b records it as `aggregation_weight_confirmed: decisions.csv#N`. A change of weight is a new
run. The rule does not look at the chain: with `chain.entity` declared G1a has already refused not_applicable.
Fix r15 L2: the confirmation is bound to the grain: decide.py records runctx.weight_grain_hash(grain) as the row's
input_hash, and only a row for the current grain clears the check; a changed grain before the G1b approve needs a new
confirmation, and card_hashes.json G1b records the grain it was given for.
A missing or invalid field writes card_completeness.csv and gate_request.json and exits 3.

CLI:  card_freeze.py --run-dir R --gate G1a|G1b
Exit: 0 hashed; 1 intent changed after G1a was approved (or, at G1b, after the G1a hash); 2 refused (state,
      missing file, blocking trap, a ratio metric without a chain);
      3 incomplete card or bindings, or (G1b) not_applicable on a grouped output contract without a recorded
      confirmation (gate_request.json written).
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import runctx as c2  # noqa: E402
from lib import schema_check  # noqa: E402
from lib.exit_codes import CheckFailedError, ExitCode, RefusedError  # noqa: E402
from lib.levels import LevelError, parse_level  # noqa: E402

QUESTION_CARD = "01_question_card.md"
METRIC_CARD = "02_metric_card.md"
OUTPUT_CONTRACT = "03_output_contract.json"
HASHES = "card_hashes.json"
COMPLETENESS = "card_completeness.csv"
TRAPS = "09_traps.csv"

INTENT_QUESTION_FIELDS = ("question",)
INTENT_METRIC_FIELDS = ("metric", "populations", "ratio_type", "chain", "aggregation_weight", "reference_type",
                        "tolerance", "headline", "confidence_level", "author_mode")
OPTIONAL_INTENT_FIELDS = ("sample_n", "mece", "pop_bands")
BINDING_FIELDS = ("tables", "joins", "measures", "derived", "date", "scd2_flags", "semi_additive_columns",
                  "reference_extract")  # fix r3 A3-5: the G1b bindings shape (skills/card "Bindings at G1b")
CONTRACT_TYPES = ("DATE", "TIMESTAMP", "STRING", "INT64", "FLOAT64", "NUMERIC", "BIGNUMERIC", "BOOL")
CONTRACT_SCHEMA = c2.CONFIG_DIR / "output_contract.schema.json"
TRUNCATIONS = ("HOUR", "DAY", "WEEK", "ISOWEEK", "MONTH", "QUARTER", "YEAR", "NONE")
STEP_SOURCE = re.compile(r"^step:[A-Za-z0-9_.-]+$")
TABLE_FORM = re.compile(r"^(\{\{\w+\}\}\.)?(\{\{\w+\}\}|\w+)\.[A-Za-z0-9_*]+$")
APPROVE_OUTCOMES = frozenset({"approve", "approved"})
RATIO_TYPES = {"cohort", "flow", "share", "per_entity", "none"}
WEIGHTS = {"pooled", "equal_weighted", "not_applicable"}
CHAIN_METHODS = {"pooled", "within_entity"}
QUESTION_TEXT = "00_question.txt"
LINK_KEYS = ("name", "numerator", "denominator", "numerator_population", "denominator_population")
# Plain-word meaning of each card key (fix r2 A2-M8); skills/card/SKILL.md "What the fields mean" says the same.
MEANING: dict[str, str] = {
    "question": "Max's question in his own words, quoted as he said it",
    "metric": "what is measured, in one line (e.g. revenue per active customer, by month)",
    "populations": "who or what is counted on top (numerator) and underneath (denominator) of the metric",
    "ratio_type": "the kind of ratio: cohort, flow, share, per_entity, or none for a plain total",
    "chain": "the 톱니 사슬: links whose ratios multiply back to the metric (each link's denominator is the next "
             "link's numerator)",
    "chain.links": "the list of links, first link's numerator = the metric's numerator, last link's denominator = "
                   "the metric's denominator",
    "chain.method": "how link ratios are formed: pooled (sum the tops and the bottoms over all entities, then divide) "
                    "or within_entity (divide inside each entity first)",
    "chain.source": "where the chain's columns are read: deliverable (the final output, the default), a "
                    "run-relative CSV such as aggregates/orders_detail.csv, or step:<name> (a step of plan.json); "
                    "never a warehouse table name",
    "source": "where the test's columns are read: deliverable (the final output), a run-relative CSV such as "
              "aggregates/orders_detail.csv, or step:<name> (a step name that must exist in plan.json); never a "
              "warehouse table name",
    "universe": "which rows are split, as a condition (e.g. order_id IS NOT NULL); TRUE for every row",
    "parts": "the parts as {part name: condition}, or part_column with domain (the column and its allowed values)",
    "claim": "the strongest verdict Max wants to be able to say about the headline: consistent, reconciled, "
             "verified against reference (or another verdict word of the report), or none",
    "chain.entity": "a list of the columns that name one entity, e.g. [month] or [month, customer_id]",
    "chain.final": "the overall ratio as {numerator, denominator}: the first link's numerator column and the last "
                   "link's denominator column",
    "name": "a short name for this link, e.g. revenue_per_order",
    "numerator": "the column on top of this link's ratio",
    "denominator": "the column underneath this link's ratio; it is the next link's numerator",
    "numerator_population": "who is counted on top of this link, in words (e.g. completed orders)",
    "denominator_population": "who is counted underneath this link, in words; it must equal the next link's "
                              "numerator_population",
    "aggregation_weight": "how the final metric combines entities: pooled (total over total; big entities weigh "
                          "more), equal_weighted (average of each entity's ratio; every entity counts once), or "
                          "not_applicable (one entity)",
    "reference_type": "the outside number the result is compared with: none, gl_trial_balance, control_total, "
                      "bi_dashboard or synthetic",
    "tolerance": "how far a number may differ and still count as equal, e.g. 0.01 (or one number per test)",
    "headline": "the reported column (field) and the strongest verdict Max wants to say about it (claim)",
    "headline.field": "the deliverable column reported as the headline; the name is provisional until G2 confirms "
                      "the tables",
    "confidence_level": "the level for this run: 60, 70, 80, 90 or 100; must equal run_config.json",
    "author_mode": "who writes the query: single, second:codex or second:max; must equal run_config.json",
    "sample_n": "how many rows are sampled per unit (a whole number, 1 or more)",
    "mece": "a split of the population into parts that do not overlap and leave nothing out, or 'none'",
    "pop_bands": "allowed period-over-period change, in percent, fixed before results",
}
_LINK_WORD = {"numerator": "top", "denominator": "bottom"}
AUTHOR_MODES = {"single", "second:codex", "second:max"}
REFERENCE_TYPES = {"none", "gl_trial_balance", "control_total", "bi_dashboard", "synthetic"}
COMPLETENESS_COLUMNS = (("gate", "VARCHAR"), ("card", "VARCHAR"), ("field", "VARCHAR"), ("status", "VARCHAR"),
                        ("detail", "VARCHAR"))
Row = tuple[str, str, str, str, str]


def _empty(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, (list, dict)) and not v)


def _source_ok(src: Any) -> bool:
    """Fix r3 A3-2, the three card source forms: 'deliverable', a run-relative .csv path, or 'step:<name>'
    (resolved at P9 by logic_tests.py against plan.json)."""
    t = str(src).strip()
    if t == "deliverable" or STEP_SOURCE.match(t):
        return True
    return (t.lower().endswith(".csv") and "{{" not in t and not t.startswith(("/", "\\")) and ":" not in t
            and ".." not in t.replace("\\", "/").split("/"))


def _source_problem(prefix: str, src: Any) -> list[tuple[str, str, str]]:
    if _empty(src):
        return [(f"{prefix}.source", "missing", MEANING["source"])]
    if not _source_ok(src):
        why = ("Windows drive letters are not run-relative paths; " if re.match(r"^[A-Za-z]:", str(src).strip())
               else "")  # fix r4 LOW: say why a path with ':' is refused
        return [(f"{prefix}.source", "invalid", f"{src!r} is not allowed; " + why + MEANING["source"])]
    return []


def _condition_problem(field: str, cond: Any) -> list[tuple[str, str, str]]:
    """Fix r5 addendum 7: a card condition must parse as one SQL boolean expression (sqlglot, BigQuery dialect)."""
    from lib import sqltext

    why = sqltext.condition_problem(cond)
    return [] if why is None else [(field, "invalid", f"{cond!r}: {why}; " + sqltext.CONDITION_MEANING)]


def mece_problems(v: Any) -> list[tuple[str, str, str]]:
    """Fix r3 A3-2: (field, missing | invalid, meaning) for every incomplete key of each `mece[i]` (1-based)."""
    if isinstance(v, str):
        return [] if v.strip().lower() in ("none", "not_applicable") else [
            ("mece", "invalid", "mece is a list of splits or 'none'; " + MEANING["mece"])]
    if not isinstance(v, list):
        return [("mece", "invalid", "mece is a list of splits or 'none'; " + MEANING["mece"])]
    out: list[tuple[str, str, str]] = []
    for i, m in enumerate(v, 1):
        p = f"mece[{i}]"
        if not isinstance(m, dict):
            out.append((p, "invalid", "a split is a mapping with name, source, universe and parts"))
            continue
        out += _source_problem(p, m.get("source"))
        if _empty(m.get("universe")):
            out.append((f"{p}.universe", "missing", MEANING["universe"]))
        else:
            out += _condition_problem(f"{p}.universe", m.get("universe"))
        parts_ok = isinstance(m.get("parts"), dict) and bool(m["parts"])
        if parts_ok:
            for k, cond in m["parts"].items():
                out += _condition_problem(f"{p}.parts.{k}", cond)
        col_ok = not _empty(m.get("part_column")) and isinstance(m.get("domain"), list) and bool(m["domain"])
        if not parts_ok and not col_ok:
            has_any = any(not _empty(m.get(k)) for k in ("parts", "part_column", "domain"))
            out.append((f"{p}.parts", "invalid" if has_any else "missing", MEANING["parts"]))
    return out


def pop_bands_problems(v: Any) -> list[tuple[str, str, str]]:
    """Fix r3 A3-2: pop_bands is a mapping whose `source` is in one of the three forms."""
    if not isinstance(v, dict):
        return [("pop_bands", "invalid", "pop_bands is a mapping; " + MEANING["pop_bands"])]
    out = _source_problem("pop_bands", v.get("source"))
    for k in ("condition", "universe"):  # fix r5 addendum 7: any condition the bands carry is SQL too
        if not _empty(v.get(k)):
            out += _condition_problem(f"pop_bands.{k}", v.get(k))
    return out


_STRENGTH: dict[str, int] | None = None


def verdict_words() -> tuple[str, ...]:
    """report.STRENGTH's verdict words (A3-3), loaded from report.py beside this script."""
    global _STRENGTH
    if _STRENGTH is None:
        spec = importlib.util.spec_from_file_location("qt_report_for_card", Path(__file__).with_name("report.py"))
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        _STRENGTH = dict(mod.STRENGTH)
    return tuple(_STRENGTH)


def headline_problems(v: Any) -> list[tuple[str, str, str]]:
    """Fix r3 A3-3: `claim` is required once `field` is set, and is a verdict word or 'none'."""
    if not isinstance(v, dict) or _empty(v.get("field")):
        return []
    c = v.get("claim")
    if _empty(c):
        return [("headline.claim", "missing", MEANING["claim"])]
    words = verdict_words()
    if str(c).strip() != "none" and str(c).strip() not in words:
        return [("headline.claim", "invalid", f"{c!r} is not a verdict word; one of: none, " + ", ".join(words))]
    return []


NESTED = {"chain": lambda v: chain_problems(v), "mece": lambda v: mece_problems(v),
          "pop_bands": lambda v: pop_bands_problems(v), "headline": lambda v: headline_problems(v)}


def chain_problems(v: Any) -> list[tuple[str, str, str]]:
    """(field, missing | invalid, plain-word meaning) for every incomplete key of a chain mapping (A2-M8)."""
    out: list[tuple[str, str, str]] = []
    if not isinstance(v, dict):
        return out
    links = v.get("links")
    if not isinstance(links, list) or not links:
        out.append(("chain.links", "missing" if _empty(links) else "invalid", MEANING["chain.links"]))
        links = []
    m = v.get("method")
    if _empty(m):
        out.append(("chain.method", "missing", MEANING["chain.method"]))
    elif str(m) not in CHAIN_METHODS:
        out.append(("chain.method", "invalid", f"{m!r} is not allowed; " + MEANING["chain.method"]))
    src = v.get("source")
    if src is not None and not _source_ok(src):
        out.append(("chain.source", "invalid", MEANING["chain.source"]))
    ent = v.get("entity")
    if ent is not None and not (isinstance(ent, list) and ent and all(isinstance(e, str) and e.strip() for e in ent)):
        out.append(("chain.entity", "invalid", MEANING["chain.entity"]))
    fin = v.get("final")
    if fin is not None and not (isinstance(fin, dict) and not _empty(fin.get("numerator"))
                                and not _empty(fin.get("denominator"))):
        out.append(("chain.final", "invalid", MEANING["chain.final"]))
    for i, link in enumerate(links, 1):
        if not isinstance(link, dict):
            out.append((f"chain.links[{i}]", "invalid", "a link is a mapping with " + ", ".join(LINK_KEYS)))
            continue
        for k in LINK_KEYS:
            if _empty(link.get(k)):
                out.append((f"chain.links[{i}].{k}", "missing", MEANING[k]))
    return out


def _check_chain(v: Any) -> str | None:
    if isinstance(v, str):
        return None if v.strip().lower() == "none" else "chain must be 'none' or a mapping with links"
    if not isinstance(v, dict):
        return "chain must be 'none' or a mapping with links"
    probs = chain_problems(v)
    if probs:
        return f"{len(probs)} chain key(s) missing or invalid (the chain.* rows name each one and what it means)"
    return None


def _check_value(field: str, v: Any) -> str | None:
    """Return why a present value is invalid, or None."""
    if field == "ratio_type" and str(v) not in RATIO_TYPES:
        return f"ratio_type must be one of {sorted(RATIO_TYPES)}"
    if field == "aggregation_weight" and str(v) not in WEIGHTS:
        return f"aggregation_weight must be one of {sorted(WEIGHTS)}"
    if field == "author_mode" and str(v) not in AUTHOR_MODES:
        return f"author_mode must be one of {sorted(AUTHOR_MODES)}"
    if field == "reference_type" and str(v) not in REFERENCE_TYPES:
        return f"reference_type must be one of {sorted(REFERENCE_TYPES)}"
    if field == "confidence_level":
        try:
            parse_level(v)
        except LevelError as exc:
            return str(exc)
    if field == "tolerance":
        nums = list(v.values()) if isinstance(v, dict) else [v]
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and x >= 0 for x in nums):
            return "tolerance must be a number >= 0 or a mapping of numbers >= 0"
    if field == "headline" and (not isinstance(v, dict) or _empty(v.get("field"))):
        return "headline must be a mapping with 'field' (the deliverable column reported as the headline)"
    if field == "chain":
        return _check_chain(v)
    if field in ("mece", "pop_bands", "headline"):
        probs = NESTED[field](v)
        if probs:
            return f"{len(probs)} {field} key(s) missing or invalid (the {field}.* rows name each one and what it means)"
    if field == "sample_n" and (not isinstance(v, int) or isinstance(v, bool) or v < 1):
        return "sample_n must be a positive integer"
    return None


def completeness(gate: str, card: str, fields: dict[str, Any], names: tuple[str, ...],
                 optional: tuple[str, ...] = ()) -> list[Row]:
    """One row per field: present, missing or invalid (spec 2.2 P1 completeness report).

    A missing or invalid row's detail carries the field's plain-word meaning (fix r2 A2-M8); the chain block adds a
    row per incomplete nested key.
    """
    rows: list[Row] = []
    for f in names + optional:
        if f not in fields or _empty(fields[f]):
            if f not in optional:
                rows.append((gate, card, f, "missing", MEANING.get(f, "")))
            continue
        why = _check_value(f, fields[f])
        detail = (why + ("; " + MEANING[f] if f in MEANING and f not in NESTED else "")) if why else ""
        rows.append((gate, card, f, "invalid" if why else "present", detail))
        if f in NESTED:
            rows += [(gate, card, k, st, d) for k, st, d in NESTED[f](fields[f])]
    return rows


def _norm(text: str) -> str:
    return " ".join(str(text).split())


def question_quote_rows(run_dir: Path) -> list[Row]:
    """01_question_card.md must quote RUN/00_question.txt (Max's words) when the run skill saved it (fix r2 A2-M7)."""
    p = Path(run_dir) / QUESTION_TEXT
    if not p.is_file():
        return []
    words = _norm(p.read_text(encoding="utf-8-sig"))
    card = _norm((Path(run_dir) / QUESTION_CARD).read_text(encoding="utf-8-sig"))
    if not words or words in card:
        return []
    return [("G1a", QUESTION_CARD, "question", "invalid",
             f"the card does not quote {QUESTION_TEXT} (Max's words) verbatim; " + MEANING["question"])]


def _cross_checks(m: dict[str, Any], rc: dict[str, Any]) -> list[Row]:
    """Document-vs-document mismatches between the metric card and run_config.json."""
    rows: list[Row] = []
    for f in ("confidence_level", "author_mode"):
        if _empty(m.get(f)) or f not in rc:
            continue
        try:
            same = parse_level(m[f]) == parse_level(rc[f]) if f == "confidence_level" else m[f] == rc[f]
        except LevelError:
            continue  # already reported as invalid
        if not same:
            rows.append(("G1a", METRIC_CARD, f, "invalid", f"card says {m[f]!r}, run_config.json says {rc[f]!r}"))
    rtype, rct = m.get("reference_type"), (rc.get("reference") or {}).get("type")
    if not _empty(rtype) and rct is not None and str(rtype) != str(rct):
        rows.append(("G1a", METRIC_CARD, "reference_type", "invalid",
                     f"card says {rtype!r}, run_config.json reference.type is {rct!r}"))
    return rows


def entity_columns(metric: dict[str, Any]) -> list[str]:
    """The card's entity columns: `chain.entity` when it is a non-empty list of names, else []."""
    ch = metric.get("chain")
    ent = ch.get("entity") if isinstance(ch, dict) else None
    return [str(e) for e in ent if not _empty(e)] if isinstance(ent, list) else []


def weight_vs_entity(metric: dict[str, Any]) -> Row | None:
    """Fix r11 L4: aggregation_weight not_applicable on a card that declares entity columns is invalid."""
    ents = entity_columns(metric)
    if not ents or str(metric.get("aggregation_weight") or "").strip() != "not_applicable":
        return None
    return ("G1a", METRIC_CARD, "aggregation_weight", "invalid",
            f"not_applicable is for a single entity; this metric has entities by {', '.join(ents)}: choose pooled or "
            f"equal_weighted; {MEANING['aggregation_weight']}")


WEIGHT_CHECK_ID = "aggregation_weight:not_applicable"  # fix r14: the G1b confirmation's decisions.csv check_id
WEIGHT_CONFIRMED = "confirmed"


def contract_grain(oc: Any) -> list[str]:
    """Fix r14: the output contract's grouping columns (`grain`, non-empty names), else [] (runctx.contract_grain)."""
    return c2.contract_grain(oc)


def weight_confirmation_row(run_dir: Path, grain: list[str] | None = None) -> int | None:
    """Fix r14: the 1-based decisions.csv row where Max confirmed aggregation_weight not_applicable on a grouped output
    (gate G1b, check_id WEIGHT_CHECK_ID, outcome confirmed), in any attempt, not replaced by a later --supersede row;
    None when there is none. Fix r15 L2: with `grain`, only a row whose input_hash is runctx.weight_grain_hash(grain)
    counts: a confirmation given for another grain does not clear the current one."""
    rows = c2.load_decisions(run_dir)
    replaced = {c2.row_attempt({"attempt": r.get("supersedes")}) for r in rows if not c2._blank(r.get("supersedes"))}
    hits = [i for i, r in enumerate(rows, 1) if str(r.get("gate")) == "G1b"
            and str(r.get("check_id") or "").strip() == WEIGHT_CHECK_ID
            and str(r.get("outcome", "")).strip().lower() == WEIGHT_CONFIRMED and i not in replaced
            and (grain is None or str(r.get("input_hash") or "") == c2.weight_grain_hash(grain))]
    return hits[-1] if hits else None


def weight_vs_grain(run_dir: Path, metric: dict[str, Any], oc: Any) -> Row | None:
    """Fix r14: at G1b, not_applicable (sealed at G1a) on a grouped output contract needs Max's recorded confirmation
    (weight_confirmation_row); without it, one invalid aggregation_weight row naming the grain."""
    grain = contract_grain(oc)
    if not grain or str(metric.get("aggregation_weight") or "").strip() != "not_applicable":
        return None
    if weight_confirmation_row(run_dir, grain) is not None:
        return None
    other = weight_confirmation_row(run_dir)
    again = (f" (decisions.csv#{other} confirmed another grain: confirm again for this one)"
             if other is not None else "")
    return ("G1b", METRIC_CARD, "aggregation_weight", "invalid",
            f"not_applicable (sealed at G1a) but the output contract is grouped by {', '.join(grain)}; confirm on the "
            "record that no combined total is wanted, or start a new run with pooled or equal_weighted" + again)


def refuse_ratio_without_chain(metric: dict[str, Any]) -> None:
    """Fix r1 A-M4: ratio_type other than none requires a chain block; 'chain: none' is refused (exit 2)."""
    rt, ch = str(metric.get("ratio_type")), metric.get("chain")
    if rt in RATIO_TYPES - {"none"} and isinstance(ch, str) and ch.strip().lower() == "none":
        raise RefusedError(f"02_metric_card.md: field 'chain' is 'none' but ratio_type is {rt!r}: a ratio needs a "
                           f"chain block ({MEANING['chain']}; links with {', '.join(LINK_KEYS)}); 'chain: none' is "
                           "accepted only with ratio_type none (spec 6.3)")


def intent_payload(run_dir: Path) -> tuple[dict[str, Any], list[Row]]:
    """The G1a intent object and its completeness rows."""
    q = c2.card_fields(run_dir / QUESTION_CARD)
    m = c2.card_fields(run_dir / METRIC_CARD)
    rc = c2.load_run_config_raw(run_dir)
    rows = completeness("G1a", QUESTION_CARD, q, INTENT_QUESTION_FIELDS)
    quote_rows = question_quote_rows(run_dir)
    if quote_rows:
        rows = [r for r in rows if not (r[1] == QUESTION_CARD and r[2] == "question")] + quote_rows
    rows += completeness("G1a", METRIC_CARD, m, INTENT_METRIC_FIELDS, OPTIONAL_INTENT_FIELDS)
    rows += _cross_checks(m, rc)
    weight_row = weight_vs_entity(m)
    if weight_row:
        rows = [r for r in rows if not (r[1] == METRIC_CARD and r[2] == "aggregation_weight")] + [weight_row]
    metric = {k: m.get(k) for k in INTENT_METRIC_FIELDS + OPTIONAL_INTENT_FIELDS}
    try:
        metric["confidence_level"] = parse_level(metric["confidence_level"])
    except LevelError:
        pass
    return {"question": {k: q.get(k) for k in INTENT_QUESTION_FIELDS}, "metric": metric, "run_config": rc,
            "question_text_sha256": question_text_sha256(run_dir)}, rows


def question_text_sha256(run_dir: Path) -> str | None:
    """dec L2: sha256 of RUN/00_question.txt (Max's words), None when the run skill saved none."""
    p = Path(run_dir) / QUESTION_TEXT
    return c2.sha256_text(p.read_bytes().decode("utf-8-sig")) if p.is_file() else None


def intent_hash(run_dir: Path) -> str:
    """Recompute the G1a intent hash of the run's current cards and run_config.json."""
    return c2.sha256_obj(intent_payload(Path(run_dir))[0])


def frozen_intent_hash(run_dir: Path) -> str:
    """The G1a hash recorded in card_hashes.json; RefusedError when G1a was never hashed."""
    h = c2.read_input_json(Path(run_dir) / HASHES, HASHES).get("G1a", {}).get("intent_hash")
    if not h:
        raise RefusedError(f"{HASHES} has no G1a intent hash")
    return str(h)


def assert_intent_unchanged(run_dir: Path) -> str:
    """CheckFailedError (exit 1) when the cards or run_config changed after G1a; return the hash."""
    frozen, now = frozen_intent_hash(run_dir), intent_hash(run_dir)
    if frozen != now:
        raise CheckFailedError(f"card intent changed after G1a ({frozen[:12]} -> {now[:12]}): this is a new run")
    return now


def frozen_metric_card(run_dir: Path) -> dict[str, Any]:
    """Metric card fields after asserting the G1a hash still holds (tolerances fixed before results)."""
    assert_intent_unchanged(run_dir)
    return c2.card_fields(Path(run_dir) / METRIC_CARD)


BIND_MEANING: dict[str, str] = {  # fix r3 A3-5; skills/card "Bindings at G1b" says the same
    "grain": "the list of columns that name one output row, e.g. [order_month]",
    "columns": "the ordered output columns, each {name, type, nullable}; type one of " + ", ".join(CONTRACT_TYPES),
    "key": "the list of key columns, each one of the output columns ([] when the output has no key)",
    "headline": "the output column reported as the headline",
    "tables": "the tables read, in placeholder form such as {{ds}}.qt_orders or ds.qt_orders, never a project ID",
    "joins": "how the tables join: a list of {left, right, on_keys: [[left column, right column], ...], type: inner or "
             "left (default inner)}; every pair is applied; [] for one table (older cards may say on, quoted in YAML)",
    "measures": "each measure as {name: {expr, table[, filter]}}, e.g. revenue: {expr: SUM(sale_price), table: "
                "{{ds}}.qt_order_items}",
    "derived": "each derived column as {name: expression over measure names}, e.g. rev_per_customer: "
               "revenue / customers; {} when there are none",
    "date": "the date that places a row in a period: {table, column, truncation, timezone}, e.g. {table: "
            "{{ds}}.qt_orders, column: created_at, truncation: MONTH, timezone: UTC}",
    "scd2_flags": "the slowly changing dimension tables and their as-of rule; [] when there are none",
    "semi_additive_columns": "columns that must not be summed over time (balances); [] when there are none",
    "reference_extract": "how the reference values were extracted (file, query or report name)",
}
_SQL_WORDS = frozenset({"AND", "OR", "NOT", "NULL", "CASE", "WHEN", "THEN", "ELSE", "END", "IS", "AS", "IN", "TRUE",
                        "FALSE", "DISTINCT"})


def _row(card: str, field: str, status: str, detail: str) -> Row:
    return ("G1b", card, field, status, detail)


def _contract_minimum(oc: Any) -> list[Row]:
    """The minimum contract shape when config/output_contract.schema.json does not exist (A3-5)."""
    if not isinstance(oc, dict):
        return [_row(OUTPUT_CONTRACT, "<file>", "invalid", "the output contract is a JSON object")]
    rows: list[Row] = []
    g = oc.get("grain")
    if not isinstance(g, list) or not all(isinstance(x, str) and x.strip() for x in g):
        rows.append(_row(OUTPUT_CONTRACT, "grain", "missing" if g is None else "invalid", BIND_MEANING["grain"]))
    cols = oc.get("columns")
    if not isinstance(cols, list) or not cols:
        rows.append(_row(OUTPUT_CONTRACT, "columns", "missing" if _empty(cols) else "invalid", BIND_MEANING["columns"]))
        cols = []
    for i, c in enumerate(cols, 1):
        if not isinstance(c, dict) or _empty(c.get("name")):
            rows.append(_row(OUTPUT_CONTRACT, f"columns[{i}].name", "missing", BIND_MEANING["columns"]))
            continue
        t = c.get("type")
        if _empty(t):
            rows.append(_row(OUTPUT_CONTRACT, f"columns[{i}].type", "missing", BIND_MEANING["columns"]))
        elif str(t).upper() not in CONTRACT_TYPES:
            rows.append(_row(OUTPUT_CONTRACT, f"columns[{i}].type", "invalid",
                             f"{c['name']}: type {t!r} is not one of {', '.join(CONTRACT_TYPES)}"))
        if not isinstance(c.get("nullable"), bool):
            rows.append(_row(OUTPUT_CONTRACT, f"columns[{i}].nullable", "missing" if c.get("nullable") is None
                             else "invalid", f"{c['name']}: nullable is true or false"))
    names = [str(c["name"]) for c in cols if isinstance(c, dict) and not _empty(c.get("name"))]
    if len({n.casefold() for n in names}) != len(names):
        rows.append(_row(OUTPUT_CONTRACT, "columns", "invalid", "duplicate column names"))
    key = oc.get("key", oc.get("keys"))
    if not isinstance(key, list):
        rows.append(_row(OUTPUT_CONTRACT, "key", "missing", BIND_MEANING["key"]))
    elif not {str(k) for k in key} <= set(names):
        rows.append(_row(OUTPUT_CONTRACT, "key", "invalid", "not output columns: "
                         + ", ".join(sorted({str(k) for k in key} - set(names)))))
    hl = oc.get("headline")
    if _empty(hl):
        rows.append(_row(OUTPUT_CONTRACT, "headline", "missing", BIND_MEANING["headline"]))
    elif str(hl) not in names:
        rows.append(_row(OUTPUT_CONTRACT, "headline", "invalid", f"{hl!r} is not an output column"))
    return rows


def _contract_rows(run_dir: Path) -> tuple[Any, list[Row]]:
    p = run_dir / OUTPUT_CONTRACT
    if not p.is_file():
        return None, [("G1b", OUTPUT_CONTRACT, "<file>", "missing", "output contract is fixed at G1b (DB-02)")]
    oc = c2.read_input_json(p, OUTPUT_CONTRACT)
    if CONTRACT_SCHEMA.is_file():  # package B's schema, when present, is the contract shape
        errs = schema_check.validate(oc, schema_check.load_schema(CONTRACT_SCHEMA))
        rows = [_row(OUTPUT_CONTRACT, "<schema>", "invalid", f"{e} (config/{CONTRACT_SCHEMA.name})") for e in errs]
    else:
        rows = _contract_minimum(oc)
    return oc, rows or [_row(OUTPUT_CONTRACT, "<file>", "present", "")]


def _tables_in(b: dict[str, Any]) -> set[str]:
    t = b.get("tables")
    return {str(x) for x in t} if isinstance(t, list) else set()


def bindings_problems(b: dict[str, Any], need_reference: bool) -> list[Row]:
    """Fix r3 A3-5: one row per missing or wrong key of the metric card's bindings block."""
    rows: list[Row] = []
    M = METRIC_CARD

    def bad(field: str, status: str, detail: str) -> None:
        rows.append(_row(M, f"bindings.{field}", status, detail))

    tables = b.get("tables")
    if not isinstance(tables, list) or not tables:
        bad("tables", "missing" if _empty(tables) else "invalid", BIND_MEANING["tables"])
    else:
        for i, t in enumerate(tables, 1):
            if not isinstance(t, str) or not TABLE_FORM.match(t.strip()):
                bad(f"tables[{i}]", "invalid", f"{t!r}: " + BIND_MEANING["tables"])
    known = _tables_in(b)
    joins = b.get("joins")
    if not isinstance(joins, list):
        bad("joins", "missing" if joins is None else "invalid", BIND_MEANING["joins"])
        joins = []
    for i, j in enumerate(joins, 1):
        if not isinstance(j, dict):
            bad(f"joins[{i}]", "invalid", BIND_MEANING["joins"])
            continue
        for side in ("left", "right"):
            if _empty(j.get(side)):
                bad(f"joins[{i}].{side}", "missing", BIND_MEANING["joins"])
            elif str(j[side]) not in known:
                bad(f"joins[{i}].{side}", "invalid", f"{j[side]!r} is not in bindings.tables")
        on = j.get("on_keys") or j.get("on") or j.get(True)  # fix r4 LOW: on_keys; `on` (YAML 1.1: True) for old cards
        if not (isinstance(on, list) and on and all(isinstance(x, list) and len(x) == 2
                                                    and all(isinstance(c, str) and c.strip() for c in x) for x in on)):
            bad(f"joins[{i}].on_keys", "missing" if _empty(on) else "invalid", BIND_MEANING["joins"])
        if str(j.get("type") or "inner").strip().lower() not in ("inner", "left"):  # fix r4 C4-5: run as declared
            bad(f"joins[{i}].type", "invalid", f"{j.get('type')!r}: inner or left; " + BIND_MEANING["joins"])
    measures = b.get("measures")
    if not isinstance(measures, dict) or not measures:
        bad("measures", "missing" if _empty(measures) else "invalid", BIND_MEANING["measures"])
        measures = {}
    for name, m in measures.items():
        if not isinstance(m, dict) or _empty(m.get("expr")):
            bad(f"measures.{name}.expr", "missing", BIND_MEANING["measures"])
        if not isinstance(m, dict) or _empty(m.get("table")):
            bad(f"measures.{name}.table", "missing", BIND_MEANING["measures"])
        elif str(m["table"]) not in known:
            bad(f"measures.{name}.table", "invalid", f"{m['table']!r} is not in bindings.tables")
        if isinstance(m, dict) and m.get("filter") is not None and not isinstance(m["filter"], str):
            bad(f"measures.{name}.filter", "invalid", "filter is a condition written as text")
    derived = b.get("derived")
    if not isinstance(derived, dict):
        bad("derived", "missing" if derived is None else "invalid", BIND_MEANING["derived"])
        derived = {}
    for name, expr in derived.items():
        if _empty(expr) or not isinstance(expr, str):
            bad(f"derived.{name}", "missing", BIND_MEANING["derived"])
            continue
        idents = {w for w in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b(?!\s*\()", expr) if w.upper() not in _SQL_WORDS}
        unknown = sorted(idents - {str(k) for k in measures} - {str(k) for k in derived})
        if unknown:
            bad(f"derived.{name}", "invalid", "not measure names: " + ", ".join(unknown))
    d = b.get("date")
    if not isinstance(d, dict):
        bad("date", "missing" if d is None else "invalid", BIND_MEANING["date"])
    else:
        for k in ("table", "column", "truncation", "timezone"):
            if _empty(d.get(k)):
                bad(f"date.{k}", "missing", BIND_MEANING["date"])
        if not _empty(d.get("table")) and str(d["table"]) not in known:
            bad("date.table", "invalid", f"{d['table']!r} is not in bindings.tables")
        if not _empty(d.get("truncation")) and str(d["truncation"]).upper() not in TRUNCATIONS:
            bad("date.truncation", "invalid", f"one of {', '.join(TRUNCATIONS)}")
    for f in ("scd2_flags", "semi_additive_columns"):
        if not isinstance(b.get(f), list):
            bad(f, "missing" if b.get(f) is None else "invalid", BIND_MEANING[f])
    if need_reference and _empty(b.get("reference_extract")):
        bad("reference_extract", "missing", BIND_MEANING["reference_extract"])
    return rows


def contract_vs_bindings(oc: Any, b: dict[str, Any]) -> list[Row]:
    """Fix r3 A3-5: every contract column is a measure, a derived name or a grain column."""
    if not isinstance(oc, dict) or not isinstance(oc.get("columns"), list):
        return []
    grain = {str(g) for g in oc.get("grain") or []} if isinstance(oc.get("grain"), list) else set()
    known = grain | ({str(k) for k in b["measures"]} if isinstance(b.get("measures"), dict) else set()) | (
        {str(k) for k in b["derived"]} if isinstance(b.get("derived"), dict) else set())
    rows: list[Row] = []
    for i, c in enumerate(oc["columns"], 1):
        n = c.get("name") if isinstance(c, dict) else None
        if not _empty(n) and str(n) not in known:
            rows.append(_row(OUTPUT_CONTRACT, f"columns[{i}].name", "invalid",
                             f"{n!r} is not a measure, a derived name or a grain column of the bindings"))
    return rows


def unresolved_blocking_traps(run_dir: Path) -> list[str]:
    """Trap ids in 09_traps.csv that are blocking and unresolved (DB-06)."""
    rows = c2.read_records_if_exists(Path(run_dir) / TRAPS)
    return [str(r["trap_id"]) for r in rows if r.get("blocking") and str(r.get("resolution") or "unresolved") == "unresolved"]


def bindings_payload(run_dir: Path) -> tuple[dict[str, Any], list[Row]]:
    """The G1b bindings object (the metric card's bindings block plus the output contract) and its rows (A3-5)."""
    m = c2.card_fields(run_dir / METRIC_CARD)
    b = m.get("bindings") if isinstance(m.get("bindings"), dict) else {}
    rows = bindings_problems(b, str(m.get("reference_type", "none")) != "none")
    if not rows:
        rows = [("G1b", METRIC_CARD, "bindings", "present", "")]
    oc, crow = _contract_rows(run_dir)
    crow += contract_vs_bindings(oc, b)
    if len(crow) > 1:
        crow = [r for r in crow if r[3] != "present"]
    weight_row = weight_vs_grain(run_dir, m, oc)  # fix r14
    if weight_row:
        crow.append(weight_row)
    return {"bindings": b, "trap_resolutions": m.get("trap_resolutions") or {}, "output_contract": oc}, rows + crow


def _report(run_dir: Path, rows: list[Row]) -> list[str]:
    c2.write_records(run_dir / COMPLETENESS, COMPLETENESS_COLUMNS, rows)
    return [f"{r[1]}:{r[2]} {r[3]}" + (f" ({r[4]})" if r[4] else "") for r in rows if r[3] != "present"]


def g1a_approval_row(run_dir: Path) -> int | None:
    """Fix r3 A3-1: the 1-based decisions.csv row of a G1a approve that no later --supersede row replaced; None while
    Max has not approved. Fix r4 C4-3: in any attempt of the run, not only the current one: later attempts (P5
    rework, P6 escalation) inherit the approval and never repeat G1a, so a changed card is a new run."""
    rows = c2.load_decisions(run_dir)
    replaced = {c2.row_attempt({"attempt": r.get("supersedes")}) for r in rows if not c2._blank(r.get("supersedes"))}
    hits = [i for i, r in enumerate(rows, 1) if str(r.get("gate")) == "G1a"
            and str(r.get("outcome", "")).strip().lower() in APPROVE_OUTCOMES and i not in replaced]
    return hits[-1] if hits else None


def archive_stale_request(run_dir: Path, gate: str) -> str | None:
    """dec L3: on exit 0, move a gate_request.json of this gate (left by an earlier exit 3) to gates/; fix r4 C4-10:
    through the shared runctx.archive_own_request."""
    return c2.archive_own_request(run_dir, "card_freeze.py", (gate,), None,
                                  f"card_freeze.py exit 0 at {gate}: the card is complete now")


def freeze(run_dir: Path, gate: str) -> int:
    """Hash one gate's payload; return the exit code."""
    run_dir = Path(run_dir)
    hashes_path = run_dir / HASHES
    hashes = c2.read_input_json(hashes_path, HASHES) if hashes_path.is_file() else {}
    if gate == "G1a":
        payload, rows = intent_payload(run_dir)
        refuse_ratio_without_chain(payload["metric"])
        problems = _report(run_dir, rows)
        if problems:
            c2.write_gate_request(run_dir, "G1a", "P1", "card_freeze.py", "card incomplete", problems)
            sys.stderr.write("NEEDS_DECISION: card incomplete: " + "; ".join(problems) + "\n")
            return int(ExitCode.NEEDS_DECISION)
        h = c2.sha256_obj(payload)
        prev = hashes.get("G1a") if isinstance(hashes.get("G1a"), dict) else {}
        superseded = list(prev.get("superseded") or [])
        old = prev.get("intent_hash")
        if old not in (None, h):
            row = g1a_approval_row(run_dir)
            if row is not None:
                raise CheckFailedError(f"G1a was approved (decisions.csv row {row}) for intent {str(old)[:12]}; the "
                                       f"cards or run_config.json now hash to {h[:12]}: a card changed after Max "
                                       "approved it is a new run")
            superseded.append({"intent_hash": old, "hashed_utc": prev.get("hashed_utc"),
                               "superseded_utc": c2.utc_now_text()})
            print(f"G1a not approved yet: previous hash {str(old)[:12]} kept under G1a.superseded")
        hashes["G1a"] = {"intent_hash": h, "hashed_utc": c2.utc_now_text(),
                         "fields": list(INTENT_QUESTION_FIELDS + INTENT_METRIC_FIELDS + OPTIONAL_INTENT_FIELDS)
                         + ["run_config.json", QUESTION_TEXT],
                         "question_text_sha256": payload["question_text_sha256"],
                         "superseded": superseded}
        hashes.pop("G1b", None)
    else:
        ih = assert_intent_unchanged(run_dir)
        blocking = unresolved_blocking_traps(run_dir)
        if blocking:
            raise RefusedError("blocking trap(s) unresolved, G1b is blocked (DB-06): " + ", ".join(blocking))
        payload, rows = bindings_payload(run_dir)
        problems = _report(run_dir, rows)
        if problems:
            c2.write_gate_request(run_dir, "G1b", "P2", "card_freeze.py", "bindings incomplete", problems)
            sys.stderr.write("NEEDS_DECISION: bindings incomplete: " + "; ".join(problems) + "\n")
            return int(ExitCode.NEEDS_DECISION)
        hashes["G1b"] = {"bindings_hash": c2.sha256_obj(payload), "intent_hash_checked": ih,
                         "hashed_utc": c2.utc_now_text()}
        grain = contract_grain(payload["output_contract"])
        confirmed = weight_confirmation_row(run_dir, grain) if grain else None
        if confirmed is not None and str(
                c2.card_fields(run_dir / METRIC_CARD).get("aggregation_weight") or "").strip() == "not_applicable":
            hashes["G1b"]["aggregation_weight_confirmed"] = f"decisions.csv#{confirmed}"  # fix r14
            hashes["G1b"]["aggregation_weight_confirmed_grain"] = grain  # fix r15 L2
    c2.write_json(hashes_path, hashes)
    print(f"{gate} hashed: " + str(hashes[gate].get("intent_hash") or hashes[gate].get("bindings_hash")))
    moved = archive_stale_request(run_dir, gate)
    if moved:
        print(f"the earlier {gate} gate_request.json (card incomplete) is answered by this card: archived to {moved}")
    return int(ExitCode.PASS)


def _main(a: Any) -> int:
    c2.phase_check(a.run_dir, ["P1"] if a.gate == "G1a" else ["P2"])
    return freeze(a.run_dir, a.gate)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    ap = c2.base_parser("card_freeze.py", "hash the cards at G1a / G1b (spec 2.1, 2.2 P1 and P2)")
    ap.add_argument("--gate", required=True, choices=["G1a", "G1b"])
    return c2.run_cli_args(_main, ap, argv)


if __name__ == "__main__":
    sys.exit(main())

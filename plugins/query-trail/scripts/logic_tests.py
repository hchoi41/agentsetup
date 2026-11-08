"""logic_tests.py: P9 logic tests: ratio chain, MECE, reference tie-out, PoP bands (design spec v1 sections 2.2 P9, 6.3, 6.4).

Every definition and tolerance is read from the metric card after asserting that the G1a
intent hash still holds, because tolerances are fixed at G1a, before results (spec 2.2 P9; the
logic skill never sets a tolerance after seeing results). Data come from the deliverable CSV or
from pinned, capped warehouse aggregates that stage 2a's runner wrote as contract CSVs; every
CSV is read through the lib reader and the arithmetic runs in local DuckDB.

톱니 사슬 ratio chain (decisions Q5; v4 aB3 and C9), card block `chain`:
  source, entity [columns], method pooled | within_entity, links [{name, numerator,
  denominator, numerator_population, denominator_population}], final {numerator, denominator},
  headline_column (optional, per-entity metric in the source to recompute).
  - links telescope only as pooled sums (SUM(num) / NULLIF(SUM(den), 0) per link) or within each
    entity before aggregating; a mean of entity ratios per link is not a chain (refused, exit 2);
  - population mesh, asserted D0: each link's denominator population equals the next link's
    numerator population (labels, and per entity values when the columns differ);
  - zero or NULL denominators go through NULLIF; chain-ineligible entities (zero or NULL
    intermediate or final denominator) are listed with their direct ratio and their share of the
    final population, and the chain result is labelled "computed over the eligible population";
  - the card's aggregation weight (pooled or equal_weighted) applies to the final metric after
    the chain is checked.
MECE (card list `mece`): explicit universe predicate (required), parts as predicates or a part
column with a domain; no overlap, no gap (TLP: a NULL predicate is not a match), parts sum to
total, domain exhaustiveness.
Reference tie-out: run_config.reference with mandatory provenance (source, extraction method,
as_of, grain, basis, tolerance; missing = exit 2) and reference_values.csv; outcome agree,
disagree or none. PoP bands (card `pop_bands`): optional screen.

Fix r6 H1: every declared pop_bands condition (`universe`, `condition`) is applied as a WHERE on the source before the
period-over-period comparison, with SQL NULL semantics (a NULL predicate excludes the row). A card condition (pop_bands,
MECE universe or part) that names a column the source does not have makes that check NOT_RUN, required, with the
missing columns and the source in the reason; it is never PASS and nothing is computed for it.

Writes checks/logic_*.csv and rows in checks/checks_manifest.csv, with `required` set by the
spec 6.4 rule (run_config.required_checks, default every check that ran). A test the card leaves out is
written NOT_RUN and required by default (fix r1 A-M4): p9_chain for a ratio metric without a chain block,
p9_mece when the card has no `mece` list; `mece: none` and ratio_type none mark them not applicable
(NOT_RUN, listed, not required).

Fix r3 A3-2, card source forms (chain.source, mece[i].source, pop_bands.source, reference_compare.source):
`deliverable`, a run-relative CSV path, or `step:<name>`. `step:<name>` resolves to the step's CSV in manifest.json
when it exists, else to logic_inputs/step_<name>.csv, the result of a warehouse query of that step's executed SQL
(executed/NN.run.sql) run by warehouse_jobs.py logic: pinned, capped and metered by the run's budget ledger. Every
source is resolved before anything is written: a step name that is not in plan.json is refused (exit 2, the message
lists plan.json's step names). At level 60 no step CSV exists, so the route is two commands before this one:
`logic_tests.py --write-inputs` (adds {name, sql_path: the step's executed SQL, out: logic_inputs/step_<name>.csv}
to logic_inputs.json), then `warehouse_jobs.py --run-dir R logic --inputs logic_inputs.json` (runs each entry,
metered, and writes its `out` CSV). Only a step source whose `out` CSV is still missing is refused (exit 2, nothing
written), and the message names that warehouse_jobs.py command.

Fix r4:
- C4-11: logic_inputs/step_<name>.csv is accepted only when its sidecar's `provenance` (written by warehouse_jobs.py
  logic) names the sha256 of the step's current executed SQL, the manifest's as_of_utc and the run state's attempt;
  otherwise the input is pending again (a P6 rerun or a redraft never lets the old SQL's output be judged), and
  --write-inputs re-emits its entry in logic_inputs.json.
- C4-15: `step:<ref>` names a plan.json step by its name, its CTE name or its number (lib.stepref).

Fix r14: when the card's aggregation_weight is not_applicable and Max confirmed it at G1b on a grouped output
(card_freeze.weight_confirmation_row), the chain's `final_metric` informational row says so in its detail:
"not_applicable confirmed at G1b (decisions.csv#N)". Nothing else changes.

CLI:  logic_tests.py --run-dir R [--write-inputs]
Exit: 0 all PASS (or reference none), or --write-inputs done; 1 a FAIL or reference disagree (G8); 2 refused.
"""
from __future__ import annotations

import importlib.util
import math
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

import duckdb  # noqa: E402

from lib import runctx as c2, stepref  # noqa: E402
from lib import csv_contract as cc  # noqa: E402
from lib.exit_codes import ExitCode, RefusedError  # noqa: E402

CHECKS = "checks"
PROVENANCE = ("source", "extraction_method", "as_of", "grain", "basis", "tolerance")
FORBIDDEN_SQL = re.compile(r";|\b(read_\w+|copy|attach|detach|install|load|pragma|export|import|set|call|create|drop|"
                           r"insert|update|delete|alter|getenv|glob)\b", re.I)
SUB_COLUMNS = (("sub_check", "VARCHAR"), ("status", "VARCHAR"), ("expected", "VARCHAR"), ("observed", "VARCHAR"),
               ("detail", "VARCHAR"))


def _card_module() -> Any:
    spec = importlib.util.spec_from_file_location("qt_card_freeze_for_logic", Path(__file__).with_name("card_freeze.py"))
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def q(name: str) -> str:
    """DuckDB identifier quoting."""
    return '"' + str(name).replace('"', '""') + '"'


def safe_predicate(expr: str, what: str) -> str:
    """A card predicate for local DuckDB: refuse anything but an expression."""
    if not isinstance(expr, str) or not expr.strip():
        raise RefusedError(f"{what}: empty predicate")
    if FORBIDDEN_SQL.search(expr):
        raise RefusedError(f"{what}: predicate contains a forbidden token: {expr!r}")
    from lib import sqltext

    why = sqltext.condition_problem(expr)  # fix r5 addendum 7: never paste prose into SQL
    if why is not None:
        raise RefusedError(f"{what}: {expr!r}: {why}; {sqltext.CONDITION_MEANING}; nothing run")
    return f"({expr})"


def condition_problems(card: Mapping[str, Any]) -> list[str]:
    """Fix r5 addendum 7: every card condition logic_tests.py would run (MECE universe and parts, pop_bands
    condition) that does not parse as one SQL boolean expression, as '<field>: <text>: <why>'."""
    from lib import sqltext

    found: list[tuple[str, Any]] = []
    mece = card.get("mece")
    for i, m in enumerate(mece if isinstance(mece, list) else [], 1):
        if isinstance(m, Mapping):
            found.append((f"mece[{i}].universe", m.get("universe")))
            found += [(f"mece[{i}].parts.{k}", v) for k, v in dict(m.get("parts") or {}).items()]
    b = card.get("pop_bands")
    if isinstance(b, Mapping):
        found += [(f"pop_bands.{k}", b[k]) for k in ("condition", "universe") if b.get(k) not in (None, "")]
    out = []
    for field, cond in found:
        why = sqltext.condition_problem(cond)
        if why is not None:
            out.append(f"{field}: {cond!r}: {why}")
    return out


def missing_condition_columns(src: "Source", conds: Sequence[tuple[str, Any]]) -> str | None:
    """Fix r6 H1: the NOT_RUN reason when a card condition names a column the source does not have, else None.
    Column names compare case-insensitively (DuckDB and BigQuery identifiers)."""
    from lib import sqltext

    have = {c.lower() for c in src.columns}
    bad = []
    for field, cond in conds:
        if cond in (None, "") or sqltext.condition_problem(cond) is not None:
            continue  # unparsable conditions are refused before anything runs (fix r5 addendum 7)
        miss = [c for c in sqltext.condition_columns(str(cond)) if c.lower() not in have]
        if miss:
            bad.append(f"{field} {cond!r} names {', '.join(miss)}")
    if not bad:
        return None
    return (f"condition column(s) not in {src.rel} (columns: {', '.join(src.columns)}): " + "; ".join(bad)
            + "; the condition was not applied, so the check did not run")


def pop_conditions(b: Mapping[str, Any]) -> list[tuple[str, Any]]:
    """Fix r6 H1: the declared pop_bands conditions, in the order they apply."""
    return [(f"pop_bands.{k}", b[k]) for k in ("universe", "condition") if b.get(k) not in (None, "")]


def mece_conditions(m: Mapping[str, Any]) -> list[tuple[str, Any]]:
    name = str(m.get("name", "partition"))
    return ([(f"MECE {name} universe", m.get("universe"))]
            + [(f"MECE {name} part {k}", v) for k, v in dict(m.get("parts") or {}).items()])


def tolerance_for(card: Mapping[str, Any], family: str) -> float:
    """Absolute tolerance from the card: a number, or a mapping keyed by family (or 'default')."""
    t = card.get("tolerance", 0)
    if isinstance(t, Mapping):
        t = t.get(family, t.get("default", 0))
    return float(t or 0)


# ---------------------------------------------------------------- card sources (fix r3 A3-2)
STEP_PREFIX = "step:"
WAREHOUSE_CMD = "warehouse_jobs.py --run-dir R logic --inputs logic_inputs.json"


def plan_steps(run_dir: Path) -> list[dict[str, Any]]:
    """Steps of plan.json in plan order (a step source must name one)."""
    plan = c2.read_input_json(Path(run_dir) / "plan.json", "plan.json (step:<name> sources name its steps)")
    return [dict(s) for s in plan.get("steps") or [] if isinstance(s, Mapping)]


def plan_step_names(run_dir: Path) -> list[str]:
    """Step names of plan.json in plan order."""
    return [str(s.get("name")) for s in plan_steps(run_dir)]


def _step_sql(run_dir: Path, name: str, manifest_step: Mapping[str, Any] | None, index: int) -> str | None:
    """Run-relative path of the step's executed SQL: manifest.json, else executed/compile_report.json, else NN."""
    if manifest_step and manifest_step.get("executed_sql_path"):
        return str(manifest_step["executed_sql_path"])
    rep = Path(run_dir) / "executed" / "compile_report.json"
    if rep.is_file():
        job = next((j for j in c2.read_input_json(rep, "executed/compile_report.json").get("jobs") or []
                    if str(j.get("label", "")).startswith("step:") and str(j.get("name")) == name), None)
        if job and job.get("path"):
            return str(job["path"])
    p = f"executed/{index:02d}.run.sql"
    return p if (Path(run_dir) / p).is_file() else None


def resolve_source(run_dir: Path, manifest: Mapping[str, Any], ref: Any) -> tuple[str | None, dict[str, Any] | None]:
    """(run-relative CSV path or None for the deliverable, pending warehouse input or None) of one card source."""
    if ref is None or str(ref).strip() in ("", "deliverable"):
        return None, None
    t = str(ref).strip()
    if not t.startswith(STEP_PREFIX):
        return t, None
    ref = t[len(STEP_PREFIX):].strip()
    steps = plan_steps(run_dir)
    names = [str(s.get("name")) for s in steps]
    name = stepref.match(steps, ref)  # fix r4 C4-15: the step's name, its CTE name or its number
    if name is None:
        raise RefusedError(f"card source {t!r}: plan.json has no step named {ref!r}; plan.json step names: "
                           + (", ".join(names) or "none") + "; a step may also be named by its CTE name or number: "
                           + stepref.describe(steps) + " (nothing written)")
    ms = next((s for s in manifest.get("steps") or [] if str(s.get("name")) == name), None)
    csv = (ms or {}).get("csv") or {}
    if csv.get("path") and c2.resolve_in_run(run_dir, str(csv["path"])).is_file():
        return str(csv["path"]), None
    stem = "step_" + re.sub(r"[^A-Za-z0-9_]+", "_", name)
    out = f"logic_inputs/{stem}.csv"
    sql = _step_sql(run_dir, name, ms, names.index(name) + 1)
    if c2.resolve_in_run(run_dir, out).is_file() and sql is not None and input_is_current(run_dir, manifest, out, sql):
        return out, None
    if sql is None:
        raise RefusedError(f"card source {t!r}: the step has no CSV and no executed SQL (executed/NN.run.sql) to "
                           "query; P6 writes it (nothing written)")
    return out, {"name": stem, "sql_path": sql, "out": out, "step": name}


def input_is_current(run_dir: Path, manifest: Mapping[str, Any], out: str, sql_path: str) -> bool:
    """Fix r4 C4-11: the logic input CSV was produced from this executed SQL, at this as_of_utc, in this attempt."""
    from lib import csv_contract as cc

    try:
        side = c2.read_json(cc.sidecar_path(c2.resolve_in_run(run_dir, out)))
        sql_file = c2.resolve_in_run(run_dir, sql_path)
    except (OSError, ValueError, RefusedError):
        return False
    prov = side.get("provenance") if isinstance(side, Mapping) else None
    if not isinstance(prov, Mapping) or not sql_file.is_file():
        return False
    as_of = (manifest.get("pin") or {}).get("as_of_utc")
    return (prov.get("sql_sha256") == cc.sha256_file(sql_file) and prov.get("as_of_utc") == as_of
            and c2.row_attempt({"attempt": prov.get("attempt")}) == c2.current_attempt(run_dir))


def card_sources(card: Mapping[str, Any]) -> list[tuple[str, Any]]:
    """(where, source) of every card test that reads data, in run order."""
    out: list[tuple[str, Any]] = []
    mece = card.get("mece")
    if isinstance(mece, list):
        out += [(f"mece[{i}]", m.get("source")) for i, m in enumerate(mece, 1) if isinstance(m, Mapping)]
    if isinstance(card.get("chain"), Mapping):
        out.append(("chain", card["chain"].get("source")))
    out.append(("reference_compare", (card.get("reference_compare") or {}).get("source")))
    if isinstance(card.get("pop_bands"), Mapping):
        out.append(("pop_bands", card["pop_bands"].get("source")))
    return out


def pending_inputs(run_dir: Path, manifest: Mapping[str, Any], card: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Warehouse inputs the card's step sources still need (refuses a step name not in plan.json)."""
    seen: dict[str, dict[str, Any]] = {}
    for _, src in card_sources(card):
        _, pend = resolve_source(run_dir, manifest, src)
        if pend:
            seen.setdefault(pend["name"], pend)
    return list(seen.values())


def write_inputs(run_dir: Path) -> list[dict[str, Any]]:
    """--write-inputs: add the pending step queries to logic_inputs.json (entries kept by name); return them."""
    run_dir = Path(run_dir)
    card = _card_module().frozen_metric_card(run_dir)
    pend = pending_inputs(run_dir, c2.load_manifest(run_dir), card)
    if pend:
        p = run_dir / "logic_inputs.json"
        spec = c2.read_input_json(p, "logic_inputs.json") if p.is_file() else {"inputs": []}
        names = {e["name"] for e in pend}  # fix r4 C4-11: a stale entry is re-emitted with the current SQL path
        spec["inputs"] = [e for e in spec.get("inputs") or [] if str(e.get("name")) not in names] + [
            {k: e[k] for k in ("name", "sql_path", "out")} for e in pend]
        c2.write_json(p, spec)
    return pend


class Source:
    """A contract CSV opened as view `src` in a private DuckDB connection."""

    def __init__(self, run_dir: Path, manifest: Mapping[str, Any], ref: str | None) -> None:
        if ref is not None and str(ref).strip().startswith(STEP_PREFIX):
            ref, pend = resolve_source(run_dir, manifest, ref)
            if pend:
                raise RefusedError(f"step source {pend['step']!r}: {pend['out']} is missing; run logic_tests.py "
                                   f"--write-inputs, then {WAREHOUSE_CMD}")
        if not ref or ref == "deliverable":
            d = next((d for d in manifest["deliverables"] if d.get("side") == "B"), None)
            if d is None:
                raise c2.InputMissingError("no deliverable B in manifest.json")
            ref = d["csv"]["path"]
        self.path = c2.resolve_in_run(run_dir, str(ref))
        self.rel = str(ref)
        self.con = duckdb.connect()
        cc.run_sql(self.con, "SET threads = 1")
        sc = cc.read_sidecar(self.path)
        cc.read_csv(self.con, self.path)
        cc.run_sql(self.con, "CREATE VIEW src AS SELECT * FROM " + cc.read_csv_sql(self.path, sc.columns))
        self.columns = [c.name for c in sc.columns]

    def one(self, sql: str) -> tuple[Any, ...]:
        rows = cc.fetch_tuples(self.con, sql)
        return rows[0] if rows else None

    def all(self, sql: str) -> list[tuple[Any, ...]]:
        return cc.fetch_tuples(self.con, sql)

    def need(self, cols: Sequence[str], what: str) -> None:
        missing = [c for c in cols if c not in self.columns]
        if missing:
            raise RefusedError(f"{what}: columns not in {self.rel}: {missing}")

    def close(self) -> None:
        self.con.close()


def _f(v: Any) -> float | None:
    return None if v is None else float(v)


def _close(a: float | None, b: float | None, tol: float) -> bool:
    if a is None or b is None:  # fix r9: two Nones compared nothing, so they never agree
        return False
    return abs(a - b) <= tol + 1e-12 * max(1.0, abs(a), abs(b))


# ---------------------------------------------------------------- chain
NO_ELIGIBLE = "no eligible entity: every entity has a zero or NULL denominator"
NO_CHAIN_ROWS = "chain source has 0 rows"
NO_HEADLINE = "headline column NULL on every eligible entity"


def not_run_reason(*whys: str | None) -> str | None:
    """Fix r10 (r9 LOW 4): the single "nothing compared" test used by the chain, MECE and pop_bands paths: the first
    NOT_RUN reason given, else None. A sub-check that compared nothing is NOT_RUN and required, never PASS."""
    return next((w for w in whys if w), None)


def chain_test(src: Source, chain: Mapping[str, Any], weight: str, tol: float,
               weight_note: str | None = None) -> tuple[list[tuple], list[tuple], dict]:
    """The 톱니 사슬 ratio chain over one entity-level source. Returns sub-check rows, ineligible rows, summary."""
    method = str(chain.get("method", "pooled"))
    if method not in ("pooled", "within_entity"):
        raise RefusedError(f"chain method {method!r}: links telescope only pooled or within each entity; "
                           "a mean of entity ratios per link is not a chain")
    links = list(chain["links"])
    ent = [str(e) for e in (chain.get("entity") or [])]
    final = chain.get("final") or {"numerator": links[0]["numerator"], "denominator": links[-1]["denominator"]}
    fn, fd = str(final["numerator"]), str(final["denominator"])
    cols = ent + [fn, fd] + [str(x) for l in links for x in (l["numerator"], l["denominator"])]
    if chain.get("headline_column"):
        cols.append(str(chain["headline_column"]))
    src.need(list(dict.fromkeys(cols)), "chain")
    rows: list[tuple] = []
    ok = True
    if str(links[0]["numerator"]) != fn or str(links[-1]["denominator"]) != fd:
        rows.append(("structure_final", "FAIL", f"{links[0]['numerator']}/{links[-1]['denominator']}", f"{fn}/{fd}",
                     "the chain's first numerator and last denominator must be the final metric's"))
        ok = False
    for i in range(len(links) - 1):
        a, b = links[i], links[i + 1]
        la, lb = a.get("denominator_population"), b.get("numerator_population")
        if la is not None and lb is not None:
            st = "PASS" if str(la) == str(lb) else "FAIL"
            ok &= st == "PASS"
            rows.append((f"population_mesh_label_{i + 1}", st, str(la), str(lb),
                         f"denominator population of {a['name']} vs numerator population of {b['name']}"))
        da, nb = str(a["denominator"]), str(b["numerator"])
        if da == nb:
            rows.append((f"population_mesh_data_{i + 1}", "PASS", da, nb, "same component column (by construction)"))
        else:
            n_bad = src.one(f"SELECT COUNT(*) FROM src WHERE {q(da)} IS DISTINCT FROM {q(nb)}")[0]
            st = "PASS" if n_bad == 0 else "FAIL"
            ok &= st == "PASS"
            rows.append((f"population_mesh_data_{i + 1}", st, "0 entities differ", f"{n_bad} entities differ",
                         f"{da} (denominator of {a['name']}) vs {nb} (numerator of {b['name']}) per entity"))
    dens = list(dict.fromkeys([str(l["denominator"]) for l in links] + [fd]))
    inelig_pred = " OR ".join(f"COALESCE({q(d)}, 0) = 0" for d in dens)
    elig = f"NOT ({inelig_pred})"
    ent_sel = ", ".join(f"CAST({q(e)} AS VARCHAR)" for e in ent) if ent else "'<all>'"
    ent_expr = f"concat_ws('|', {ent_sel})" if ent else "'<all>'"
    total_den = _f(src.one(f"SELECT SUM({q(fd)}) FROM src")[0])
    inelig = src.all(f"SELECT {ent_expr}, {q(fn)} / NULLIF({q(fd)}, 0), {q(fd)} FROM src WHERE {inelig_pred} "
                     f"ORDER BY 1")
    inelig_rows = [(str(e), None if r is None else float(r), None if d is None else float(d),
                    None if not total_den or d is None else float(d) / total_den) for e, r, d in inelig]
    n_ent, n_elig = src.one(f"SELECT COUNT(*), COUNT(*) FILTER (WHERE {elig}) FROM src")
    rows.append(("eligibility", "PASS", f"{n_ent} entities", f"{n_elig} eligible, {n_ent - n_elig} ineligible",
                 "recorded, not tested: chain computed over the eligible population" + (
                     f"; ineligible entities hold {sum(r[3] or 0 for r in inelig_rows):.4%} of the final population"
                     if inelig_rows else "")))
    if not n_elig:  # fix r9: a chain over no eligible entity compared nothing (NOT_RUN, required)
        why = NO_CHAIN_ROWS if not n_ent else NO_ELIGIBLE
        rows += [(cid, "NOT_RUN", "-", "-", why) for cid in ("telescope_pooled", "telescope_within_entity")]
        return rows, inelig_rows, {"ok": False, "final": None, "eligible": 0, "entities": n_ent, "not_run": why}
    pooled = [src.one(f"SELECT SUM({q(l['numerator'])}) / NULLIF(SUM({q(l['denominator'])}), 0) FROM src WHERE {elig}")[0]
              for l in links]
    prod = None if any(p is None for p in pooled) else math.prod(float(p) for p in pooled)
    direct = _f(src.one(f"SELECT SUM({q(fn)}) / NULLIF(SUM({q(fd)}), 0) FROM src WHERE {elig}")[0])
    st = "PASS" if _close(prod, direct, tol) else "FAIL"
    ok &= st == "PASS"
    rows.append(("telescope_pooled", st, f"{direct}", f"{prod}", "product of pooled link ratios vs pooled final "
                 "ratio over the eligible population"))
    link_prod = " * ".join(f"({q(l['numerator'])} / NULLIF({q(l['denominator'])}, 0))" for l in links)
    # fix r10 (r9 MEDIUM 1): a NULL on either side counts as differing (NOT COALESCE(<agree>, FALSE))
    n_bad = src.one(f"SELECT COUNT(*) FROM src WHERE {elig} AND NOT COALESCE(ABS(({link_prod}) - ({q(fn)} / NULLIF("
                    f"{q(fd)}, 0))) <= {tol!r} + 1e-12 * GREATEST(1, ABS({q(fn)} / NULLIF({q(fd)}, 0))), FALSE)")[0]
    st = "PASS" if n_bad == 0 else "FAIL"
    ok &= st == "PASS"
    rows.append(("telescope_within_entity", st, "0 entities differ", f"{n_bad} entities differ",
                 "per entity: product of link ratios vs the entity's direct ratio"))
    if weight == "equal_weighted":
        final_val = _f(src.one(f"SELECT AVG({q(fn)} / NULLIF({q(fd)}, 0)) FROM src WHERE {elig}")[0])
    else:
        final_val = direct
    rows.append(("final_metric", "PASS", weight, f"{final_val}",
                 f"recorded, not tested: final metric by the card's aggregation weight ({weight}) over the eligible population"
                 + (f"; {weight_note}" if weight_note else "")))
    if chain.get("headline_column"):
        h = str(chain["headline_column"])
        ratio = f"{q(fn)} / NULLIF({q(fd)}, 0)"
        n_bad, n_cmp = src.one(f"SELECT COUNT(*) FILTER (WHERE NOT COALESCE(ABS({q(h)} - {ratio}) <= {tol!r} + 1e-12 "
                               f"* GREATEST(1, ABS({q(h)})), FALSE)), COUNT(*) FILTER (WHERE {q(h)} IS NOT NULL AND "
                               f"{ratio} IS NOT NULL) FROM src WHERE {elig}")  # fix r10 (r9 MEDIUM 1)
        if not n_cmp:  # the headline was never compared against anything
            rows.append(("headline_recompute", "NOT_RUN", "-", "0 compared", NO_HEADLINE))
            return rows, inelig_rows, {"ok": False, "final": final_val, "eligible": n_elig, "entities": n_ent,
                                       "not_run": NO_HEADLINE}
        st = "PASS" if n_bad == 0 else "FAIL"
        ok &= st == "PASS"
        rows.append(("headline_recompute", st, "0 entities differ", f"{n_bad} entities differ",
                     f"{h} vs {fn} / NULLIF({fd}, 0) per eligible entity ({n_cmp} compared; a NULL side differs)"))
    return rows, inelig_rows, {"ok": ok, "final": final_val, "eligible": n_elig, "entities": n_ent}


# ---------------------------------------------------------------- MECE
EMPTY_SELECTION = "condition selected 0 rows"
NO_PAIR = "no period pair compared"


def mece_test(src: Source, m: Mapping[str, Any], tol: float) -> tuple[list[tuple], bool, str | None]:
    """No overlap, no gap, parts sum to total and domain exhaustiveness against the stated universe.

    Fix r8 (r7 MEDIUM 2): an empty universe compares nothing, so every sub-check is NOT_RUN and the third element
    is the NOT_RUN reason (spec 2.2 P7: a comparison that compared nothing is never positive); else None."""
    name = str(m.get("name", "partition"))
    if not str(m.get("universe") or "").strip():
        raise RefusedError(f"MECE {name}: an explicit universe from the card is required (spec 6.3)")
    uni = safe_predicate(str(m["universe"]), f"MECE {name} universe")
    if m.get("parts"):
        parts = {str(k): safe_predicate(str(v), f"MECE {name} part {k}") for k, v in dict(m["parts"]).items()}
    elif m.get("part_column") and m.get("domain"):
        pc = str(m["part_column"])
        src.need([pc], f"MECE {name}")
        parts = {str(v): f"({q(pc)} = '{str(v).replace(chr(39), chr(39) * 2)}')" for v in m["domain"]}
    else:
        raise RefusedError(f"MECE {name}: needs parts (predicates) or part_column with domain")
    hits = " + ".join(f"CASE WHEN {p} THEN 1 ELSE 0 END" for p in parts.values())
    base = f"FROM (SELECT *, ({hits}) AS qt_hits FROM src WHERE {uni}) AS u"
    n, n_over, n_gap = src.one(f"SELECT COUNT(*), COUNT(*) FILTER (WHERE qt_hits > 1), "
                               f"COUNT(*) FILTER (WHERE qt_hits = 0) {base}")
    if not n:
        rows = [("universe_rows", "NOT_RUN", str(m["universe"]), "0", EMPTY_SELECTION)]
        rows += [(cid, "NOT_RUN", "-", "-", EMPTY_SELECTION) for cid in ("no_overlap", "no_gap", "parts_sum_to_total")]
        return rows, False, EMPTY_SELECTION
    rows = [("universe_rows", "PASS", str(m["universe"]), str(n), "rows in the stated universe"),
            ("no_overlap", "PASS" if n_over == 0 else "FAIL", "0", str(n_over), "rows in more than one part"),
            ("no_gap", "PASS" if n_gap == 0 else "FAIL", "0", str(n_gap),
             "rows in no part (a NULL predicate is not a match: TLP)")]
    if m.get("measure"):
        mc = str(m["measure"])
        src.need([mc], f"MECE {name}")
        total, parts_sum = src.one(f"SELECT SUM({q(mc)}), SUM({q(mc)} * qt_hits) {base}")
        total, parts_sum = _f(total), _f(parts_sum)
        if total is None:  # fix r9: a measure NULL on every universe row compared nothing
            null_why = f"measure {mc} is NULL on every universe row"
            rows.append(("parts_sum_to_total", "NOT_RUN", "-", "-", null_why))
            return rows, False, null_why
        rows.append(("parts_sum_to_total", "PASS" if _close(total, parts_sum, tol) else "FAIL", f"{total}",
                     f"{parts_sum}", f"SUM({mc}) over parts vs over the universe"))
        if m.get("total") is not None:
            st = "PASS" if _close(float(m["total"]), total, tol) else "FAIL"
            rows.append(("universe_total", st, f"{m['total']}", f"{total}", "card total vs universe sum"))
    if m.get("part_column") and m.get("domain"):
        pc = str(m["part_column"])
        dom = [str(v) for v in m["domain"]]
        observed = [r[0] for r in src.all(f"SELECT DISTINCT CAST({q(pc)} AS VARCHAR) {base} ORDER BY 1 NULLS FIRST")]
        outside = [("NULL" if v is None else v) for v in observed if v is None or v not in dom]
        rows.append(("domain_exhaustive", "PASS" if not outside else "FAIL", ",".join(dom), ",".join(outside) or "-",
                     "observed values outside the declared domain (NULL included)"))
        unused = [v for v in dom if v not in observed]
        rows.append(("domain_unused", "PASS", "-", ",".join(unused) or "-", "recorded, not tested: declared values never observed"))
    return rows, all(r[1] == "PASS" for r in rows), None


# ---------------------------------------------------------------- reference
def reference_test(run_dir: Path, src: Source, rc: Mapping[str, Any], card: Mapping[str, Any]
                   ) -> tuple[list[tuple], str, dict[str, Any]]:
    """Tie-out against an independent reference; outcome agree, disagree or none."""
    ref = rc.get("reference") or {"type": "none"}
    if ref.get("type", "none") == "none":
        return [("reference", "NOT_RUN", "-", "-", "no reference: G16 at close")], "none", {"type": "none"}
    missing = [f for f in PROVENANCE if ref.get(f) in (None, "")]
    if missing:
        raise RefusedError("reference provenance is mandatory (spec 6.3); missing: " + ", ".join(missing))
    _, vals = c2.read_records(run_dir / "reference_values.csv", "reference_values.csv")
    tol = float(ref["tolerance"])
    cmpc = card.get("reference_compare") or {}
    col = str(cmpc.get("column") or (card.get("headline") or {}).get("field"))
    grain = [g.strip() for g in str(ref["grain"]).split(",") if g.strip()]
    src.need([col], "reference")
    how = cmpc.get("how") or ("per_key" if grain and all(g in src.columns for g in grain) else "single")
    rows: list[tuple] = []
    if how == "per_key":
        src.need(grain, "reference")
        got = {tuple(str(x) for x in r[:-1]): _f(r[-1]) for r in
               src.all(f"SELECT {', '.join(f'CAST({q(g)} AS VARCHAR)' for g in grain)}, {q(col)} FROM src")}
        for v in vals:
            k = tuple(str(v.get(g)) for g in grain)
            ours, theirs = got.pop(k, None), _f(v.get("reference_value"))
            st = "PASS" if ours is not None and _close(ours, theirs, tol) else "FAIL"
            rows.append((f"tie_out {'|'.join(k)}", st, f"{theirs}", f"{ours}", f"|diff| <= {tol}"))
        for k, ours in got.items():
            rows.append((f"tie_out {'|'.join(k)}", "FAIL", "missing in reference", f"{ours}", "key only in the output"))
    else:
        agg = "SUM" if how == "sum" else "MAX"
        n = src.one("SELECT COUNT(*) FROM src")[0]
        if how == "single" and n != 1:
            raise RefusedError(f"reference compare 'single' needs a one-row output; {src.rel} has {n} rows")
        ours = _f(src.one(f"SELECT {agg}({q(col)}) FROM src")[0])
        theirs = _f(vals[0].get("reference_value")) if len(vals) == 1 else None
        if len(vals) != 1:
            raise RefusedError("reference_values.csv must hold one row for a 'single' or 'sum' comparison")
        rows.append(("tie_out", "PASS" if _close(ours, theirs, tol) else "FAIL", f"{theirs}", f"{ours}",
                     f"{how} of {col}, |diff| <= {tol}"))
    outcome = "agree" if rows and all(r[1] == "PASS" for r in rows) else "disagree"
    prov = {k: ref.get(k) for k in ("type", "independence") + PROVENANCE}
    return rows, outcome, prov


# ---------------------------------------------------------------- PoP bands
def pop_test(src: Source, b: Mapping[str, Any]) -> tuple[list[tuple], bool, str | None, int]:
    """Period-over-period change of a series within bands fixed at G1a (optional screen), over the rows the declared
    conditions select (fix r6 H1: universe AND condition as a WHERE; a NULL predicate excludes the row).

    Returns (rows, ok, not_run_reason, skipped): fix r8 (r7 MEDIUM 2) makes the reason EMPTY_SELECTION when the
    conditions select no row and NO_PAIR when no period pair was compared; `skipped` counts the pairs left out
    because the previous value was 0 or NULL."""
    pc, vc = str(b["period_column"]), str(b["value_column"])
    series = [str(s) for s in b.get("series_columns", [])]
    src.need([pc, vc] + series, "pop_bands")
    preds = [safe_predicate(str(c), f) for f, c in pop_conditions(b)]
    rel = f"(SELECT * FROM src WHERE {' AND '.join(preds)}) AS s" if preds else "src"
    lo, hi = float(b["lower_pct"]), float(b["upper_pct"])
    part = f"PARTITION BY {', '.join(q(s) for s in series)} " if series else ""
    sel = ", ".join(f"CAST({q(s)} AS VARCHAR)" for s in series) or "'<all>'"
    out = src.all(f"SELECT concat_ws('|', {sel}), CAST({q(pc)} AS VARCHAR), {q(vc)}, "
                  f"LAG({q(vc)}) OVER ({part}ORDER BY {q(pc)}) FROM {rel} ORDER BY 1, 2")
    if not out:
        return [], False, EMPTY_SELECTION, 0
    rows, skipped, first = [], 0, set()
    for s, p, v, prev in out:
        if s not in first:  # the first period of a series has no previous period: not a pair
            first.add(s)
            continue
        if prev is None or v is None or float(prev) == 0:
            skipped += 1
            continue
        pct = (float(v) - float(prev)) / abs(float(prev)) * 100
        rows.append((f"pop {s} {p}", "PASS" if lo <= pct <= hi else "FAIL", f"[{lo}, {hi}] %", f"{pct:.2f} %",
                     f"{vc} vs previous period"))
    if not rows:
        why = NO_PAIR + (f" ({skipped} pair(s) skipped: previous value 0 or NULL)" if skipped else "")
        return [], False, why, skipped
    return rows, all(r[1] == "PASS" for r in rows), None, skipped


# ---------------------------------------------------------------- run
def _write(run_dir: Path, name: str, rows: Sequence[tuple], columns: Sequence[tuple[str, str]] = SUB_COLUMNS
           ) -> tuple[str, str]:
    rel = f"{CHECKS}/{name}"
    sc = c2.write_records(run_dir / rel, columns, rows)
    return rel, sc.csv_sha256


def weight_note(run_dir: Path, weight: str) -> str | None:
    """Fix r14: "not_applicable confirmed at G1b (decisions.csv#N)" when Max confirmed the weight on a grouped output."""
    if weight != "not_applicable":
        return None
    row = _card_module().weight_confirmation_row(run_dir)
    return None if row is None else f"not_applicable confirmed at G1b (decisions.csv#{row})"


def run(run_dir: Path) -> tuple[int, list[dict[str, Any]]]:
    """Run every configured test in the spec 6.3 order; write CSVs and manifest rows."""
    run_dir = Path(run_dir)
    card = _card_module().frozen_metric_card(run_dir)
    rc = c2.load_run_config_raw(run_dir)
    manifest = c2.load_manifest(run_dir)
    required = rc.get("required_checks")
    checks: list[dict[str, Any]] = []
    weight = str(card.get("aggregation_weight", "pooled"))
    pend = pending_inputs(run_dir, manifest, card)  # fix r3 A3-2: every source resolves before anything is written
    if pend:
        raise RefusedError("step source(s) whose warehouse result is missing: "
                           + ", ".join(f"{e['step']} ({e['out']}, from {e['sql_path']})" for e in pend)
                           + f". Run logic_tests.py --run-dir R --write-inputs (adds them to logic_inputs.json), then "
                           f"{WAREHOUSE_CMD} (pinned, capped, metered; writes each out CSV), then logic_tests.py again; "
                           "nothing written")
    bad = condition_problems(card)  # fix r5 addendum 7: refuse before anything runs or is written
    if bad:
        from lib import sqltext

        raise RefusedError("card condition(s) that are not SQL: " + "; ".join(bad) + f". Each must be "
                           f"{sqltext.CONDITION_MEANING}; fix the card (a new run after G1a); nothing run or written")

    def add(cid: str, family: str, status: str, rel: str | None, sha: str | None, detail: str,
            reason: str | None = None, force_required: bool | None = None) -> None:
        if required is not None:
            req = cid in set(required)
        else:
            req = (status != "NOT_RUN") if force_required is None else force_required
        if family == "reference":
            req = False  # the reference outcome is its own column of the verdict table (spec 4.4)
        checks.append(c2.check_row(cid, "P9", family, status, rel, sha, detail, reason, req))

    mece = card.get("mece")
    if isinstance(mece, str) and mece.strip().lower() in ("none", "not_applicable"):
        add("p9_mece", "mece", "NOT_RUN", None, None, "MECE not applicable",
            "not applicable per the card (mece: none)", force_required=False)
        mece = []
    elif not mece:
        add("p9_mece", "mece", "NOT_RUN", None, None, "MECE did not run",
            "the card lists no MECE partition (mece); spec 6.3 requires one against a stated universe",
            force_required=True)
        mece = []
    for m in mece:
        name = re.sub(r"[^a-z0-9_]+", "_", str(m.get("name", "partition")).lower())
        src = Source(run_dir, manifest, m.get("source"))
        try:
            why = missing_condition_columns(src, mece_conditions(m))  # fix r6 H1
            rows, ok, empty = ([], False, None) if why else mece_test(src, m, tolerance_for(card, "mece"))
        finally:
            src.close()
        nothing = not_run_reason(why, empty)  # fix r8/r10: nothing compared is NOT_RUN, required
        if nothing:
            rel, sha = _write(run_dir, f"logic_mece_{name}.csv", rows) if empty else (None, None)
            add(f"p9_mece_{name}", "mece", "NOT_RUN", rel, sha, f"MECE {name} did not run", nothing,
                force_required=True)
            continue
        rel, sha = _write(run_dir, f"logic_mece_{name}.csv", rows)
        add(f"p9_mece_{name}", "mece", "PASS" if ok else "FAIL", rel, sha, f"MECE {name} on {m.get('source', 'deliverable')}")
    chain = card.get("chain")
    if isinstance(chain, Mapping):
        src = Source(run_dir, manifest, chain.get("source"))
        try:
            rows, inelig, summ = chain_test(src, chain, weight, tolerance_for(card, "chain"), weight_note(run_dir, weight))
        finally:
            src.close()
        rel, sha = _write(run_dir, "logic_chain.csv", rows)
        _write(run_dir, "logic_chain_ineligible.csv", inelig,
               (("entity", "VARCHAR"), ("direct_ratio", "DOUBLE"), ("final_denominator", "DOUBLE"),
                ("share_of_final_population", "DOUBLE")))
        if not_run_reason(summ.get("not_run")):  # fix r9/r10
            add("p9_chain", "chain", "NOT_RUN", rel, sha, "chain did not run", summ["not_run"], force_required=True)
        else:
            add("p9_chain", "chain", "PASS" if summ["ok"] else "FAIL", rel, sha,
                f"chain over {summ['eligible']} of {summ['entities']} entities (eligible population); final {summ['final']}")
    elif str(card.get("ratio_type", "none")) != "none":
        add("p9_chain", "chain", "NOT_RUN", None, None, "chain did not run",
            f"ratio_type {card.get('ratio_type')} but the card has no chain block", force_required=True)
    else:
        add("p9_chain", "chain", "NOT_RUN", None, None, "chain not applicable",
            "not applicable: ratio_type none", force_required=False)
    src = Source(run_dir, manifest, (card.get("reference_compare") or {}).get("source"))
    try:
        rows, outcome, prov = reference_test(run_dir, src, rc, card)
    finally:
        src.close()
    rel, sha = _write(run_dir, "logic_reference.csv", rows)
    c2.write_json(run_dir / CHECKS / "logic_reference.json", {"outcome": outcome, "provenance": prov,
                                                              "csv": rel, "checked_utc": c2.utc_now_text()})
    add("p9_reference", "reference", {"agree": "PASS", "disagree": "FAIL", "none": "NOT_RUN"}[outcome], rel, sha,
        f"reference outcome: {outcome}", "no reference" if outcome == "none" else None)
    if isinstance(card.get("pop_bands"), Mapping):
        b = card["pop_bands"]
        src = Source(run_dir, manifest, b.get("source"))
        try:
            why = missing_condition_columns(src, pop_conditions(b))  # fix r6 H1: never PASS without the predicate
            rows, ok, empty, skipped = ([], False, None, 0) if why else pop_test(src, b)
        finally:
            src.close()
        conds = " AND ".join(f"({c})" for _, c in pop_conditions(b))
        nothing = not_run_reason(why, empty)  # fix r8/r10: a check that compared nothing is NOT_RUN (required)
        if nothing:
            add("p9_pop_bands", "screen", "NOT_RUN", None, None, "PoP bands did not run", nothing,
                force_required=True)
        else:
            rel, sha = _write(run_dir, "logic_pop.csv", rows)
            add("p9_pop_bands", "screen", "PASS" if ok else "FAIL", rel, sha, "PoP bands fixed at G1a"
                + (f"; over the rows where {conds}" if conds else "; over every row (no condition declared)")
                + (f"; {skipped} pair(s) skipped: previous value 0 or NULL" if skipped else ""))
    c2.upsert_checks(run_dir, checks)
    bad = [c["check_id"] for c in checks if c["status"] == "FAIL"]
    return (int(ExitCode.FAIL) if bad else int(ExitCode.PASS)), checks


def _main(a: Any) -> int:
    c2.phase_check(a.run_dir, ["P9"])
    if a.write_inputs:
        pend = write_inputs(a.run_dir)
        for e in pend:
            print(f"logic_inputs.json: {e['name']}: {e['sql_path']} -> {e['out']} (step {e['step']})")
        print(f"next: {WAREHOUSE_CMD}, then logic_tests.py" if pend
              else "logic_inputs.json: no step source needs a warehouse query")
        return int(ExitCode.PASS)
    code, checks = run(a.run_dir)
    for c in checks:
        print(f"{c['check_id']}: {c['status']} ({c['detail']})")
    if code == ExitCode.FAIL:
        sys.stderr.write("FAIL: " + ", ".join(c["check_id"] for c in checks if c["status"] == "FAIL") + " (G8)\n")
    return code


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    ap = c2.base_parser("logic_tests.py", "P9 logic tests (spec 6.3)")
    ap.add_argument("--write-inputs", action="store_true",
                    help="add the warehouse queries that step:<name> sources need to logic_inputs.json, then stop")
    return c2.run_cli_args(_main, ap, argv)


if __name__ == "__main__":
    sys.exit(main())

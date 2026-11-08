"""trace.py: P8 trace of every sampled row to its source, and the trace report (design spec v1 sections 2.2 P8, 6.2).

Trace by cardinality (spec 6.2): 1_to_1 key lookup (one upstream row, same values);
many_to_1 back-trace plus re-aggregation (the sampled group's measures recomputed from its
contributor rows); 1_to_many input key to output rows (the input key must resolve to one
upstream row). Ratios recompute numerator and denominator from source, not from the previous
step (an upstream error passed 32 of 32 times when checked only against the previous step, X T06).

`generate` writes one key-bounded, partition-filtered lookup per sampled row and upstream:
  source tables (BigQuery): SQL with the {{project}} and dataset-alias placeholders, run later
    by stage 2a's runner after compile.py injects the pin, under dry run and the per-job cap;
    its result lands in trace/results/<lookup_id>.json {"status", "rows"};
  prior steps with a retained full CSV (level 90 and 100): DuckDB SQL over the sanctioned
    reader (read_csv, sidecar types, allow_quoted_nulls=false), run locally by `evaluate`.
Samples are drawn locally (key-hash predicates save no bytes, L BQ03); only lookups go out.

trace_plan.json (optional, D1 proposal Max can read): per unit {cardinality, tolerance,
upstream: [{ref, kind: source_table|prior_step, key_map {sample column: upstream column or SQL
expression}, partition_filter {column, start, end}, filters [SQL], measures {sample column:
aggregate SQL}, compare {sample column: upstream column}, compute {sample column: SQL expression
over the upstream row}}]}.

trace.py owns the default plan (fix r1 A-H5), written to trace/trace_plan_effective.json: a unit
without a trace_plan.json entry traces 1_to_1 on the step grain to every manifest input; the
deliverable unit traces 1_to_1 to the final step on the deliverable grain. Every plan, given or
default, gets the card's chain ratios: a ratio column (a chain link's name, or the headline column
for the final pair) is recomputed from its numerator and denominator, from the upstream step's
columns in a 1_to_1 lookup, or as ratio of the source aggregates in a many_to_1 lookup when both
parts are measured there.

Coverage (fix r1 A-H5), trace/trace_coverage.json and per row in trace_results.csv: the
deliverable measures are the output contract's numeric non-key columns (else the deliverable's
numeric non-grain columns). Per unit: which measures were compared against which upstream
(`compared`), `measures_not_recomputed`, `measures_not_from_source` (compared only step to step,
or not at all) and `columns_not_compared`. p8_trace (required) is FAIL on any FAIL; NOT_RUN when
any row is not traced, any unit leaves a measure it carries uncompared, or a measure is compared in
no unit; PASS only when every deliverable measure was compared in every unit that carries it.
Source-join recompute (fix r1 A-H5 part 4, scripts/trace_source.py): for the deliverable unit, the final
step and any step on the deliverable grain, the card's bindings.columns definitions of the grain and the
measures are recomputed per sampled grain value from the source tables, joined along plan.json's join
steps: one single-table lookup per table, the first by the grain, the next by the keys collected from
the previous result, so `generate` runs in stages (generate, runner, generate again) until no lookup is
pending; then a local DuckDB aggregation. A measure it cannot resolve is listed in
measures_not_recomputed with the reason (`not_recomputed_reasons`).

`evaluate` compares, writes trace/trace_results.csv (one sentence per sampled row in Max's
reading form), trace/trace_coverage.json, trace_report.md (FAIL lines first; per-unit coverage;
G12 sign-off line) and a checks_manifest row.

CLI:  trace.py --run-dir R generate | evaluate
Exit: 0 PASS or coverage incomplete (p8_trace NOT_RUN, required: the report words it); 3 a sampled
      row not reproduced from source (fix r4 C4-16: p8_trace FAIL, a required-check FAIL, opens G8 with
      gate_request.json, check_id p8_trace, outcomes explained, rewrite, re_pin, csv_only, stop; a later exit 0
      archives it); 2 refused (state, missing sample, a lookup result not yet run).

Fix r4 C4-17: a source group row compares several measures at once; when it FAILs, only the measures that differed
(`failed_cols`) count as failed from source, and the others in it count as recomputed and matched.

Fix r5 M1 (spec v5 6.2 is the rule): complete coverage is computed per required (unit, measure) pair, a pair being a
sampled unit and a deliverable measure it carries. A pair is recomputed from source when every sampled row of the unit
has a source lookup that compared the measure and matched (a PASS row, or a FAIL row that did not differ on it) and no
source lookup of the unit failed on it; a NOT_TRACED row, a row with no source lookup, or a failure leaves the pair
open. `measures_from_source` lists the measures whose every pair is recomputed and that failed nowhere;
`measures_partially_recomputed` maps each other measure compared from source somewhere to
{"units_recomputed": complete pairs, "units_required": pairs}. Per unit: `measures_from_source` (its complete pairs)
and `measures_not_from_source` (the rest of its measures).

Fix r5 addendum 5: below level 90 (per levels.json: the level's step artifacts hold no `full_csv`) no step CSV is
retained, so a lookup whose target is a prior step's CSV is NOT_APPLICABLE, not NOT_TRACED: it does not count against
coverage, trace_report.md says "step-to-step sampling: not applicable at level N" and trace_coverage.json carries
`step_to_step: "not applicable at level N"`. Coverage is then decided by source recompute alone per (unit, measure):
a measure not recomputed from source in a unit is `measures_not_recomputed` there, and p8_trace is PASS only when
every pair is recomputed from source with no FAIL. At level 90 and 100 the old rule stands (a missing step CSV is
NOT_TRACED).
"""
from __future__ import annotations

import datetime as dt
import decimal
import importlib.util
import math
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

import duckdb  # noqa: E402

import trace_source  # noqa: E402
from lib import runctx as c2  # noqa: E402
from lib import csv_contract as cc  # noqa: E402
from lib import levels as lv  # noqa: E402
from lib.exit_codes import CheckFailedError, ExitCode, RefusedError  # noqa: E402

TRACE = "trace"
LOOKUPS = "lookups.json"
RESULTS_CSV = "trace_results.csv"
REPORT = "trace_report.md"
COVERAGE = "trace_coverage.json"
EFFECTIVE_PLAN = "trace_plan_effective.json"
CMP_PREFIX = "qt_cmp__"
CARDINALITIES = ("1_to_1", "many_to_1", "1_to_many")
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
RESULT_COLUMNS = (("unit", "VARCHAR"), ("selection", "VARCHAR"), ("stratum", "VARCHAR"), ("row_id", "VARCHAR"),
                  ("lookup_id", "VARCHAR"), ("upstream", "VARCHAR"), ("cardinality", "VARCHAR"),
                  ("status", "VARCHAR"), ("sentence", "VARCHAR"), ("compared", "VARCHAR"),
                  ("columns_not_compared", "VARCHAR"), ("measures_not_recomputed", "VARCHAR"))
META = {"_qt_selection", "_qt_stratum", "_qt_position", "_qt_row_id", "_qt_rank"}


def _sample_module() -> Any:
    spec = importlib.util.spec_from_file_location("qt_sample_for_trace", Path(__file__).with_name("sample.py"))
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules.setdefault("qt_sample_for_trace", mod)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- literals and SQL
def bq_literal(v: Any, type_text: str = "") -> str:
    """GoogleSQL literal for a key value (NULL handled by the caller)."""
    t = type_text.upper()
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, decimal.Decimal):
        return f"NUMERIC '{format(v, 'f')}'" if abs(v.as_tuple().exponent) <= 9 else f"BIGNUMERIC '{format(v, 'f')}'"
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return f"CAST('{'NaN' if math.isnan(v) else ('inf' if v > 0 else '-inf')}' AS FLOAT64)"
        return repr(v)
    if isinstance(v, dt.datetime):
        if "TIME ZONE" in t or "TIMESTAMPTZ" in t or v.tzinfo is not None:
            u = v if v.tzinfo is None else v.astimezone(dt.timezone.utc).replace(tzinfo=None)
            return f"TIMESTAMP '{u.isoformat(sep=' ', timespec='microseconds')}+00'"
        return f"DATETIME '{v.isoformat(sep=' ', timespec='microseconds')}'"
    if isinstance(v, dt.date):
        return f"DATE '{v.isoformat()}'"
    if isinstance(v, (bytes, bytearray, memoryview)):
        return f"FROM_HEX('{bytes(v).hex()}')"
    s = str(v).replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
    return f"'{s}'"


def duck_literal(v: Any, type_text: str = "") -> str:
    """DuckDB literal for a key value (NULL handled by the caller)."""
    t = type_text.upper()
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, decimal.Decimal):
        return format(v, "f")
    if isinstance(v, float):
        return repr(v) if math.isfinite(v) else f"'{v}'::DOUBLE"
    if isinstance(v, dt.datetime):
        if "TIME ZONE" in t or v.tzinfo is not None:
            u = v if v.tzinfo is None else v.astimezone(dt.timezone.utc).replace(tzinfo=None)
            return f"TIMESTAMPTZ '{u.isoformat(sep=' ', timespec='microseconds')}+00'"
        return f"TIMESTAMP '{v.isoformat(sep=' ', timespec='microseconds')}'"
    if isinstance(v, dt.date):
        return f"DATE '{v.isoformat()}'"
    if isinstance(v, (bytes, bytearray, memoryview)):
        return f"from_hex('{bytes(v).hex()}')"
    return "'" + str(v).replace("'", "''") + "'"


def _col(expr: str, engine: str) -> str:
    """A plain column name is quoted; anything else is a plan expression, used as written."""
    if IDENT.match(expr):
        return f"`{expr}`" if engine == "bigquery" else '"' + expr + '"'
    return f"({expr})"


def key_predicate(key_map: Mapping[str, str], row: Mapping[str, Any], types: Mapping[str, str], engine: str) -> str:
    """AND of NULL-safe equalities between upstream expressions and the sampled key values."""
    lit = bq_literal if engine == "bigquery" else duck_literal
    parts = []
    for scol, ucol in key_map.items():
        v = row.get(scol)
        parts.append(f"{_col(ucol, engine)} IS NULL" if v is None else f"{_col(ucol, engine)} = {lit(v, types.get(scol, ''))}")
    return " AND ".join(parts) if parts else "FALSE"


def _source_sql(ref: str) -> str:
    ds, tb = ref.split(".", 1)
    return f"`{{{{project}}}}.{{{{{ds}}}}}.{tb}`"


def partition_predicate(pf: Mapping[str, Any] | None, engine: str) -> str | None:
    """Partition filter on the upstream (constant bounds, so BigQuery can prune)."""
    if not pf or not pf.get("column") or not pf.get("start") or not pf.get("end"):
        return None
    c = _col(str(pf["column"]), engine)
    return f"CAST({c} AS DATE) BETWEEN DATE '{pf['start']}' AND DATE '{pf['end']}'"


def compute_sql(spec: Any, engine: str) -> str:
    """A compute entry: {"ratio": [numerator, denominator]} (added from the card's chain) or a SQL expression."""
    if isinstance(spec, Mapping) and spec.get("ratio"):
        num, den = spec["ratio"]
        return f"CAST({_col(str(num), engine)} AS {'FLOAT64' if engine == 'bigquery' else 'DOUBLE'}) / " \
               f"NULLIF({_col(str(den), engine)}, 0)"
    return f"({spec})"


def lookup_sql(unit_plan: Mapping[str, Any], up: Mapping[str, Any], row: Mapping[str, Any],
               types: Mapping[str, str], engine: str, local_from: str | None = None) -> str:
    """One lookup: key-bounded and partition-filtered; many_to_1 re-aggregates; `compute` adds recomputed columns."""
    src = local_from if engine == "duckdb" else _source_sql(str(up["ref"]))
    conds = [p for p in [partition_predicate(up.get("partition_filter"), engine)] if p]
    conds += [f"({f})" for f in up.get("filters", [])]
    conds.append(key_predicate(up["key_map"], row, types, engine))
    where = " AND ".join(conds)
    if unit_plan["cardinality"] == "many_to_1":
        meas = ", ".join(f"{expr} AS {_col(name, engine)}" for name, expr in up.get("measures", {}).items())
        return f"SELECT COUNT(*) AS qt_n_contributors{', ' + meas if meas else ''} FROM {src} WHERE {where}"
    extra = "".join(f", {compute_sql(spec, engine)} AS {_col(CMP_PREFIX + name, engine)}"
                    for name, spec in (up.get("compute") or {}).items())
    return f"SELECT *{extra} FROM {src} WHERE {where} LIMIT 1001"


# ---------------------------------------------------------------- plan
def ratio_defs(run_dir: Path) -> dict[str, tuple[str, str]]:
    """Ratio columns named by the card's chain: link name -> (numerator, denominator); headline column -> final pair."""
    p = Path(run_dir) / "02_metric_card.md"
    if not p.is_file():
        return {}
    ch = c2.card_fields(p).get("chain")
    if not isinstance(ch, Mapping):
        return {}
    out = {str(l["name"]): (str(l["numerator"]), str(l["denominator"])) for l in ch.get("links") or []
           if isinstance(l, Mapping) and l.get("name") and l.get("numerator") and l.get("denominator")}
    links = ch.get("links") or []
    fin = ch.get("final") or ({"numerator": links[0].get("numerator"), "denominator": links[-1].get("denominator")}
                              if links else {})
    if ch.get("headline_column") and fin.get("numerator") and fin.get("denominator"):
        out[str(ch["headline_column"])] = (str(fin["numerator"]), str(fin["denominator"]))
    return out


def _deliverable(manifest: Mapping[str, Any]) -> Mapping[str, Any] | None:
    return next((d for d in manifest["deliverables"] if d.get("side") == "B"), None)


def deliverable_grain(run_dir: Path, manifest: Mapping[str, Any], names: Sequence[str]) -> list[str]:
    """Contract keys (or grain), else the final step's grain, when every column is in `names`."""
    p = Path(run_dir) / "03_output_contract.json"
    oc = c2.read_input_json(p) if p.is_file() else {}
    for cand in (oc.get("keys"), oc.get("grain"), manifest["steps"][-1].get("grain") if manifest["steps"] else None):
        if cand and all(k in names for k in cand):
            return list(cand)
    return []


def deliverable_measures(run_dir: Path, manifest: Mapping[str, Any]) -> list[str]:
    """The output contract's numeric non-key columns, else the deliverable's numeric non-grain columns."""
    from lib import canon
    d = _deliverable(manifest)
    if d is None or not d.get("csv"):
        return []
    cols = cc.read_sidecar(c2.resolve_in_run(run_dir, d["csv"]["path"])).columns
    names = [c.name for c in cols]
    keys = set(deliverable_grain(run_dir, manifest, names))
    p = Path(run_dir) / "03_output_contract.json"
    oc = c2.read_input_json(p) if p.is_file() else {}
    typed = [(str(c["name"]), str(c["type"])) for c in oc.get("columns") or [] if isinstance(c, Mapping)] \
        or [(c.name, c.type) for c in cols]
    numeric = {"INT64": "int", "NUMERIC": "decimal", "BIGNUMERIC": "decimal", "FLOAT64": "float"}
    return [n for n, t in typed if n not in keys and n in names
            and (numeric.get(t.split("(")[0].upper()) or canon.type_class(t)) in ("int", "float", "decimal")]


def _upstream_columns(run_dir: Path, manifest: Mapping[str, Any], up: Mapping[str, Any]) -> list[str] | None:
    if up.get("kind") != "prior_step":
        return None
    csv = _local_csv(manifest, str(up["ref"]))
    if csv is not None:
        return [c.name for c in cc.read_sidecar(c2.resolve_in_run(run_dir, csv["path"])).columns]
    s = next((s for s in manifest["steps"] if s["name"] == up["ref"]), None)
    return [str(c["name"]) for c in (s or {}).get("columns") or []] or None


def _augment(plan: dict[str, Any], unit_cols: Sequence[str], ratios: Mapping[str, tuple[str, str]],
             run_dir: Path, manifest: Mapping[str, Any]) -> None:
    """Add the card's chain ratios a lookup can recompute (never overriding what the plan states)."""
    for up in plan["upstream"]:
        if plan["cardinality"] == "many_to_1":
            meas = up.setdefault("measures", {})
            for col, (num, den) in ratios.items():
                if col in unit_cols and col not in meas and num in meas and den in meas:
                    meas[col] = f"CAST(({meas[num]}) AS {'FLOAT64' if up.get('kind') == 'source_table' else 'DOUBLE'}) " \
                                f"/ NULLIF(({meas[den]}), 0)"
                    up.setdefault("recomputed_ratios", []).append(col)
        elif plan["cardinality"] == "1_to_1":
            ucols = _upstream_columns(run_dir, manifest, up)
            if not ucols:
                continue
            comp = up.setdefault("compute", {})
            for col, (num, den) in ratios.items():
                if col in unit_cols and col not in ucols and col not in comp and num in ucols and den in ucols:
                    comp[col] = {"ratio": [num, den]}
            if not comp:
                up.pop("compute")


def unit_plans(run_dir: Path, manifest: Mapping[str, Any],
               unit_columns: Mapping[str, Sequence[str]] | None = None) -> dict[str, dict[str, Any]]:
    """Per-unit trace plan: trace_plan.json where given, else the default (1_to_1 on the grain to every input; the
    deliverable 1_to_1 to the final step); every plan gets the chain ratios its lookups can recompute."""
    import copy
    given = c2.read_input_json(run_dir / "trace_plan.json").get("units", {}) if (run_dir / "trace_plan.json").is_file() else {}
    steps = {s["name"]: s for s in manifest["steps"]}
    ratios = ratio_defs(run_dir)
    card = c2.card_fields(run_dir / "02_metric_card.md") if (run_dir / "02_metric_card.md").is_file() else {}
    measures = deliverable_measures(run_dir, manifest) if unit_columns is not None else []
    d = _deliverable(manifest)
    dgrain = deliverable_grain(run_dir, manifest, [c.name for c in cc.read_sidecar(
        c2.resolve_in_run(run_dir, d["csv"]["path"])).columns]) if (unit_columns is not None and d and d.get("csv")) else []
    last = manifest["steps"][-1]["name"] if manifest["steps"] else None
    plans = {}
    for name in set(steps) | set(given) | {"deliverable_B"}:
        if name in given:
            p = copy.deepcopy(dict(given[name]))
            p["plan_source"] = "trace_plan.json"
        elif name in steps and steps[name].get("grain"):
            g = list(steps[name]["grain"])
            ups = [{"ref": i["ref"], "kind": i["kind"], "key_map": {k: k for k in g}} for i in steps[name]["inputs"]]
            p = {"cardinality": "1_to_1", "upstream": ups, "plan_source": "default: 1_to_1 on the step grain"}
        elif name == "deliverable_B" and manifest["steps"] and _deliverable(manifest) is not None \
                and _deliverable(manifest).get("csv"):
            d = _deliverable(manifest)
            names = [c.name for c in cc.read_sidecar(c2.resolve_in_run(run_dir, d["csv"]["path"])).columns]
            g = deliverable_grain(run_dir, manifest, names)
            last = manifest["steps"][-1]["name"]
            p = {"cardinality": "1_to_1", "plan_source": "default: 1_to_1 to the final step on the deliverable grain",
                 "upstream": [{"ref": last, "kind": "prior_step", "key_map": {k: k for k in g}}] if g else []}
        else:
            p = {"cardinality": "1_to_1", "upstream": [], "plan_source": "default: no grain, not traced"}
        if p.get("cardinality") not in CARDINALITIES:
            raise RefusedError(f"trace plan {name}: cardinality must be one of {CARDINALITIES}")
        p.setdefault("tolerance", 0)
        p.setdefault("upstream", [])
        if unit_columns is not None and name in unit_columns:
            _augment(p, unit_columns[name], ratios, run_dir, manifest)
            cols = unit_columns[name]
            mine = [m for m in measures if m in cols]
            on_grain = name in ("deliverable_B", last) or (name in steps and list(steps[name].get("grain") or []) == dgrain)
            if dgrain and set(dgrain) <= set(cols) and mine and on_grain:
                p["source_join"] = trace_source.build(run_dir, card, dgrain, mine, ratios)
        plans[name] = p
    return plans


def unit_columns_of(run_dir: Path, sman: Mapping[str, Any]) -> dict[str, list[str]]:
    """Data columns (no _qt_ bookkeeping) of every sampled unit's CSV."""
    return {u["unit"]: [c.name for c in cc.read_sidecar(run_dir / u["csv"]).columns if c.name not in META]
            for u in sman["units"]}


def _local_csv(manifest: Mapping[str, Any], ref: str) -> dict[str, Any] | None:
    for s in manifest["steps"]:
        if s["name"] == ref and s.get("csv") and "full_csv" in s.get("artifacts", []):
            return s["csv"]
    return None


def step_na_level(sman: Mapping[str, Any]) -> int | None:
    """Fix r5 addendum 5: the run level when it retains no step CSV (step-to-step lookups not applicable), else None."""
    level = sman.get("level")
    if level is None:
        return None
    return None if "full_csv" in lv.artifacts_for(level) else int(lv.parse_level(level))


def generate(run_dir: Path) -> dict[str, Any]:
    """Write trace/lookups.json and trace/sql/*.sql for every sampled row and upstream."""
    run_dir = Path(run_dir)
    manifest = c2.load_manifest(run_dir)
    sman = c2.read_input_json(run_dir / "samples" / "sample_manifest.json", "samples/sample_manifest.json")
    plans = unit_plans(run_dir, manifest, unit_columns_of(run_dir, sman))
    c2.write_json(run_dir / TRACE / EFFECTIVE_PLAN, {"generated_utc": c2.utc_now_text(), "units": {
        u["unit"]: plans.get(u["unit"], {}) for u in sman["units"]}})
    lookups = []
    na_level = step_na_level(sman)
    for u in sman["units"]:
        plan = plans.get(u["unit"], {"cardinality": "1_to_1", "upstream": []})
        sc = cc.read_sidecar(run_dir / u["csv"])
        types = {c.name: c.type for c in sc.columns}
        _, rows = c2.read_records(run_dir / u["csv"], u["csv"])
        sj = plan.get("source_join") or {}
        for row in rows:
            rid = str(row["_qt_row_id"])
            if sj.get("tables"):
                lookups += _source_join_entries(run_dir, u["unit"], rid, row, types, sj)
            if not plan["upstream"] and not sj.get("tables"):
                lookups.append({"lookup_id": f"{u['unit']}__{rid[:16]}__none", "unit": u["unit"], "row_id": rid,
                                "upstream": None, "status_hint": "not traced: no key or upstream in the trace plan"})
            for i, up in enumerate(plan["upstream"]):
                lid = f"{u['unit']}__{rid[:16]}__{i}"
                entry = {"lookup_id": lid, "unit": u["unit"], "row_id": rid, "upstream": up["ref"],
                         "kind": up["kind"], "cardinality": plan["cardinality"]}
                if up["kind"] == "source_table":
                    sql = lookup_sql(plan, up, row, types, "bigquery")
                    entry.update(engine="bigquery", needs_pin=True, dry_run_first=True,
                                 partition_filtered=bool(partition_predicate(up.get("partition_filter"), "bigquery")))
                else:
                    csv = _local_csv(manifest, str(up["ref"]))
                    if csv is None and na_level is not None:  # fix r5 addendum 5
                        entry.update(engine=None, not_applicable=True,
                                     status_hint=f"step-to-step lookup not applicable at level {na_level} (no step "
                                                 "CSV is retained below level 90)")
                        lookups.append(entry)
                        continue
                    if csv is None:
                        entry.update(engine=None, status_hint="not traced: prior step has no retained full CSV at "
                                                              "this level; trace to source instead")
                        lookups.append(entry)
                        continue
                    src = cc.read_csv_sql(c2.resolve_in_run(run_dir, csv["path"]), cc.read_sidecar(
                        c2.resolve_in_run(run_dir, csv["path"])).columns)
                    sql = lookup_sql(plan, up, row, types, "duckdb", local_from=src)
                    entry.update(engine="duckdb", local_csv=csv["path"])
                rel = f"{TRACE}/sql/{lid}.sql"
                c2.write_text(run_dir / rel, sql + "\n")
                entry["sql_path"] = rel
                lookups.append(entry)
    out = {"generated_utc": c2.utc_now_text(), "lookups": lookups}
    c2.write_json(run_dir / TRACE / LOOKUPS, out)
    return out


def _result(run_dir: Path, lid: str, sql_path: str | None = None) -> dict[str, Any] | None:
    """A lookup's result, or None when it has none or (fix r5 addendum 8) its stamp is not the current one."""
    p = run_dir / TRACE / "results" / f"{lid}.json"
    if not p.is_file():
        return None
    res = c2.read_input_json(p)
    if sql_path is not None and c2.stamp_mismatch(run_dir, res, (run_dir / sql_path).read_text(encoding="utf-8")):
        return None
    return res


def current_result(run_dir: Path, x: Mapping[str, Any]) -> dict[str, Any]:
    """Fix r5 addendum 8: a warehouse lookup's result, refused (exit 2) unless it carries the current stamp
    (lookup SQL sha256, as_of_utc, attempt): a result of another attempt or another as-of is never compared."""
    p = run_dir / TRACE / "results" / f"{x['lookup_id']}.json"
    res = c2.read_input_json(p)
    bad = c2.stamp_mismatch(run_dir, res, (run_dir / x["sql_path"]).read_text(encoding="utf-8"))
    if bad:
        raise RefusedError(f"{p.relative_to(run_dir).as_posix()}: result stamp differs from the current lookup "
                           f"({', '.join(bad)}): written for another SQL, as-of or attempt; run trace.py generate and "
                           "warehouse_jobs.py trace again; nothing evaluated")
    return res


def _source_join_entries(run_dir: Path, unit: str, rid: str, row: Mapping[str, Any], types: Mapping[str, str],
                         sj: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Stage entries of one sampled row's source join: SQL where the parent's rows are in, else pending."""
    gid = f"{unit}__{rid[:16]}__src"
    out, lids, rows_of = [], {}, {}
    for i, t in enumerate(sj["tables"]):
        lid = f"{gid}__{i}_{trace_source.short(t['table'])}"
        lids[t["table"]] = lid
        e = {"lookup_id": lid, "unit": unit, "row_id": rid, "upstream": t["table"], "kind": "source_join",
             "cardinality": "source_join", "join_group": gid, "stage": i, "source_table": t["table"]}
        parent_rows = None
        if i > 0:
            pl = lids[t["parent"]]
            prow = rows_of.get(t["parent"], "missing")
            if prow == "missing":
                e.update(engine="pending", depends_on=pl)
                out.append(e)
                rows_of[t["table"]] = "missing"
                continue
            if prow == "bad":
                e.update(engine=None, status_hint=f"not traced: lookup {pl} did not return rows")
                out.append(e)
                rows_of[t["table"]] = "bad"
                continue
            parent_rows = prow
        sql = trace_source.lookup_sql(sj, i, row, types, parent_rows, bq_literal, _source_sql)
        if sql is None:
            e.update(engine="local_empty")
            rows_of[t["table"]] = []
        else:
            rel = f"{TRACE}/sql/{lid}.sql"
            c2.write_text(run_dir / rel, sql + "\n")
            e.update(engine="bigquery", needs_pin=True, dry_run_first=True, sql_path=rel)
            res = _result(run_dir, lid, rel)
            rows_of[t["table"]] = "missing" if res is None else (list(res.get("rows", [])) if res.get("status") == "ok"
                                                                 else "bad")
        out.append(e)
    return out


# ---------------------------------------------------------------- compare
def _num(v: Any) -> decimal.Decimal | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float, decimal.Decimal)):
        try:
            return decimal.Decimal(repr(v)) if isinstance(v, float) else decimal.Decimal(v)
        except decimal.InvalidOperation:
            return None
    if isinstance(v, str) and re.fullmatch(r"-?\d+(\.\d+)?([eE][-+]?\d+)?", v.strip()):
        return decimal.Decimal(v.strip())
    return None


def _time(v: Any) -> dt.datetime | None:
    if isinstance(v, dt.datetime):
        return v if v.tzinfo else v.replace(tzinfo=dt.timezone.utc)
    if isinstance(v, dt.date):
        return dt.datetime(v.year, v.month, v.day, tzinfo=dt.timezone.utc)
    if isinstance(v, str) and re.match(r"^\d{4}-\d{2}-\d{2}", v):
        try:
            return c2.parse_utc(v.replace(" UTC", "+00:00"))
        except ValueError:
            return None
    return None


def values_equal(a: Any, b: Any, tol: float = 0.0) -> bool:
    """Compare a sampled value with an upstream value across Python and JSON types."""
    if a is None or b is None:
        return a is None and b is None
    na, nb = _num(a), _num(b)
    if na is not None and nb is not None:
        return abs(na - nb) <= decimal.Decimal(repr(float(tol)))
    ta, tb = _time(a), _time(b)
    if ta is not None and tb is not None:
        return ta == tb
    if isinstance(a, (bytes, bytearray)) or isinstance(b, (bytes, bytearray)):
        return (bytes(a).hex() if isinstance(a, (bytes, bytearray)) else str(a)) == \
               (bytes(b).hex() if isinstance(b, (bytes, bytearray)) else str(b))
    return str(a) == str(b)


def fmt(v: Any) -> str:
    """Reading form of a value: thousands separators, two decimals for non-integers."""
    n = _num(v)
    if n is not None and not isinstance(v, str):
        return f"{n:,.0f}" if n == n.to_integral_value() else f"{n:,.2f}"
    if v is None:
        return "NULL"
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return str(v)


def describe_key(key_map: Mapping[str, str], row: Mapping[str, Any]) -> str:
    """'order_id=6649, line=2' for the sampled key."""
    return ", ".join(f"{k}={fmt(row.get(k))}" for k in key_map) or "(no key)"


def where_of(up: Mapping[str, Any]) -> str:
    """'source ds.t' or 'step 02_x': what a lookup compared against."""
    return f"{'source' if up['kind'] == 'source_table' else 'step'} {up['ref']}"


def judge(plan: Mapping[str, Any], up: Mapping[str, Any], row: Mapping[str, Any],
          rows: Sequence[Mapping[str, Any]]) -> tuple[str, str, list[str]]:
    """PASS or FAIL, the finding phrase, and the sample columns actually compared, for one lookup's rows."""
    tol = float(plan.get("tolerance") or 0)
    where = where_of(up)
    card = plan["cardinality"]
    if card == "many_to_1":
        r0 = rows[0] if rows else {}
        n = r0.get("qt_n_contributors", 0)
        diffs, parts, missing = [], [], []
        cols = [c for c in up.get("measures", {}) if c in row and c in r0]
        for col in up.get("measures", {}):
            if col not in r0:  # the lookup result does not carry it (an older lookup): not compared, not a FAIL
                missing.append(col)
                continue
            got = r0.get(col)
            parts.append(f"{col} {fmt(row.get(col))} vs recomputed {fmt(got)}")
            if not values_equal(row.get(col), got, tol):
                diffs.append(col)
        if not rows or int(n or 0) == 0:
            return "FAIL", f"has no contributor rows in {where}", []
        phrase = f"recomputed from {fmt(n)} contributor rows in {where}: " + "; ".join(parts)
        if missing:
            phrase += f" (not in the lookup result, not compared: {', '.join(missing)})"
        return ("FAIL" if diffs else "PASS"), phrase, cols
    if not rows:
        return "FAIL", f"is not found in {where} (0 rows inside the key bounds and partition filter)", []
    if len(rows) > 1:
        return "FAIL", (f"matches {len(rows) if len(rows) <= 1000 else 'over 1000'} rows in {where} "
                        "(must be 1: fan-out)"), []
    if card == "1_to_many":
        return "PASS", f"resolves to exactly 1 row in {where}", []
    u = rows[0]
    cmp = dict(up.get("compare") or {c: c for c in row if c not in META and c in u})
    cmp = {c: uc for c, uc in cmp.items() if uc in u}
    comp = [c for c in (up.get("compute") or {}) if CMP_PREFIX + c in u and c in row]
    diffs = [(c, u.get(uc)) for c, uc in cmp.items() if not values_equal(row.get(c), u.get(uc), tol)]
    diffs += [(c, u.get(CMP_PREFIX + c)) for c in comp if not values_equal(row.get(c), u.get(CMP_PREFIX + c), tol)]
    compared = list(dict.fromkeys(list(cmp) + comp))
    if diffs:
        return "FAIL", f"is found once in {where} but differs in " + ", ".join(
            f"{c} ({fmt(row.get(c))} vs {fmt(v)}{' recomputed' if c in comp else ''})" for c, v in diffs), compared
    rec = f"; recomputed: {', '.join(comp)}" if comp else ""
    return "PASS", f"is found once in {where} with the same values ({len(compared)} columns compared{rec})", compared


def _local_rows(sql: str) -> list[dict[str, Any]]:
    con = duckdb.connect()
    try:
        cc.run_sql(con, "SET TimeZone = 'UTC'")
        return cc.fetch_dicts(con, sql)[1]
    finally:
        con.close()


def evaluate(run_dir: Path) -> tuple[int, list[dict[str, Any]]]:
    """Compare every lookup, write the results, the report and the checks row; return the exit code."""
    run_dir = Path(run_dir)
    manifest = c2.load_manifest(run_dir)
    sman = c2.read_input_json(run_dir / "samples" / "sample_manifest.json", "samples/sample_manifest.json")
    lk = c2.read_input_json(run_dir / TRACE / LOOKUPS, "trace/lookups.json")["lookups"]
    ucols = unit_columns_of(run_dir, sman)
    plans = unit_plans(run_dir, manifest, ucols)
    samples = {u["unit"]: {str(r["_qt_row_id"]): r for r in c2.read_records(run_dir / u["csv"], u["csv"])[1]}
               for u in sman["units"]}
    pending = [x["lookup_id"] for x in lk if x.get("engine") == "pending"]
    if pending:
        raise RefusedError(f"{len(pending)} source-join lookup(s) wait for an earlier stage: run warehouse_jobs.py "
                           "trace, then trace.py generate again (" + ", ".join(pending[:3]) + ")")
    missing = [x["lookup_id"] for x in lk if x.get("engine") == "bigquery"
               and not (run_dir / TRACE / "results" / f"{x['lookup_id']}.json").is_file()]
    if missing:
        raise RefusedError(f"{len(missing)} warehouse lookup(s) not run yet (trace/results/<lookup_id>.json): "
                           + ", ".join(missing[:5]))
    out = []
    groups: dict[str, list[dict[str, Any]]] = {}
    for x in lk:
        if x.get("join_group"):
            groups.setdefault(x["join_group"], []).append(x)
            continue
        row = samples[x["unit"]][x["row_id"]]
        plan = plans.get(x["unit"], {"cardinality": "1_to_1", "upstream": []})
        sel = str(row.get("_qt_selection"))
        label = "R" if sel in ("random", "census") else str(row.get("_qt_stratum"))
        if x.get("not_applicable"):  # fix r5 addendum 5: no step CSV at this level; not a coverage gap
            out.append({"unit": x["unit"], "selection": sel, "stratum": label, "row_id": x["row_id"],
                        "lookup_id": x["lookup_id"], "upstream": x.get("upstream"),
                        "cardinality": plan["cardinality"], "status": "NOT_APPLICABLE",
                        "sentence": f"{x['unit']} {label}: row {x['row_id'][:12]} against {x.get('upstream')}: "
                                    f"{x.get('status_hint')}.", "cols": [], "where": None})
            continue
        if x.get("upstream") is None or x.get("engine") is None:
            status, sentence = "NOT_TRACED", f"{x['unit']} {label}: row {x['row_id'][:12]} not traced: " + \
                str(x.get("status_hint", "no lookup")).removeprefix("not traced: ") + "."
            out.append({"unit": x["unit"], "selection": sel, "stratum": label, "row_id": x["row_id"],
                        "lookup_id": x["lookup_id"], "upstream": x.get("upstream"),
                        "cardinality": plan["cardinality"], "status": status, "sentence": sentence, "cols": [],
                        "where": None})
            continue
        up = next(u for u in plan["upstream"] if u["ref"] == x["upstream"])
        keyd = describe_key(up["key_map"], row)
        if x["engine"] == "duckdb":
            rows, rstat = _local_rows((run_dir / x["sql_path"]).read_text(encoding="utf-8")), "ok"
        else:
            res = current_result(run_dir, x)
            rstat, rows = str(res.get("status")), list(res.get("rows", []))
        cols: list[str] = []
        if rstat != "ok":
            reason = {"over_cap": "lookup over the per-job cap (narrow the key bounds)",
                      "denied": "lookup denied", "error": "lookup error"}.get(rstat, f"lookup {rstat}")
            status, sentence = "NOT_TRACED", f"{x['unit']} {label}: row {keyd} not traced: {reason}."
        else:
            status, phrase, cols = judge(plan, up, row, rows)
            noun = "group" if plan["cardinality"] == "many_to_1" else "row"
            sentence = f"{x['unit']} {label}: {noun} {keyd} {phrase} -> {status}."
        out.append({"unit": x["unit"], "selection": sel, "stratum": label, "row_id": x["row_id"],
                    "lookup_id": x["lookup_id"], "upstream": x["upstream"], "cardinality": plan["cardinality"],
                    "status": status, "sentence": sentence, "cols": cols, "where": where_of(up)})
    for gid, xs in groups.items():
        out.append(_judge_source_join(run_dir, gid, xs, samples, plans))
    measures = deliverable_measures(run_dir, manifest)
    cov = coverage(sman, plans, ucols, out, measures, step_na_level=step_na_level(sman))
    by_unit = {u["unit"]: u for u in cov["units"]}
    for r in out:
        u = by_unit.get(r["unit"], {})
        r["compared"] = ", ".join(r.pop("cols"))
        r.pop("where")
        r["columns_not_compared"] = ", ".join(u.get("columns_not_compared", []))
        r["measures_not_recomputed"] = ", ".join(u.get("measures_not_recomputed", []))
    c2.write_records(run_dir / TRACE / RESULTS_CSV, RESULT_COLUMNS, [tuple(r[n] for n, _ in RESULT_COLUMNS) for r in out])
    c2.write_json(run_dir / TRACE / COVERAGE, cov)
    write_report(run_dir, sman, out, cov)
    fails = [r for r in out if r["status"] == "FAIL"]
    not_traced = [r for r in out if r["status"] == "NOT_TRACED"]
    sc = cc.read_sidecar(run_dir / TRACE / RESULTS_CSV)
    detail = (f"{len(out)} lookups: {len(fails)} FAIL, {len(not_traced)} not traced; deliverable measures compared: "
              f"{', '.join(m for m in measures if m not in cov['measures_never_compared']) or 'none'}; from source: "
              f"{', '.join(cov['measures_from_source']) or 'none'}")
    c2.upsert_checks(run_dir, [c2.check_row(
        "p8_trace", "P8", "trace", cov["status"], f"{TRACE}/{RESULTS_CSV}", sc.csv_sha256, detail,
        "; ".join(cov["not_run_reasons"]) if cov["status"] == "NOT_RUN" else None, required=True)])
    if fails:
        return int(ExitCode.FAIL), out
    if cov["status"] == "NOT_RUN":
        sys.stderr.write("trace coverage incomplete (p8_trace NOT_RUN, required): " + "; ".join(cov["not_run_reasons"])
                         + "\n")
    return int(ExitCode.PASS), out


def _judge_source_join(run_dir: Path, gid: str, xs: Sequence[Mapping[str, Any]], samples: Mapping[str, Any],
                       plans: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """One result row per sampled row's source join: the card's definitions recomputed and compared."""
    x0 = xs[0]
    row = samples[x0["unit"]][x0["row_id"]]
    sj = plans[x0["unit"]]["source_join"]
    sel = str(row.get("_qt_selection"))
    label = "R" if sel in ("random", "census") else str(row.get("_qt_stratum"))
    keyd = describe_key({g: g for g in sj["grain_defs"]}, row)
    where = "source join " + " + ".join(t["table"] for t in sj["tables"])
    rows_by, why = {}, None
    for x in xs:
        if x.get("engine") is None:
            why = str(x.get("status_hint", "not traced")).removeprefix("not traced: ")
        elif x["engine"] == "local_empty":
            rows_by[x["source_table"]] = []
        else:
            res = current_result(run_dir, x)
            if str(res.get("status")) != "ok":
                why = f"lookup {x['lookup_id']} {res.get('status')}"
            else:
                rows_by[x["source_table"]] = list(res.get("rows", []))
    cols: list[str] = []
    diffs: list[str] = []
    if why:
        status, sentence = "NOT_TRACED", f"{x0['unit']} {label}: group {keyd} not traced by {where}: {why}."
    else:
        got = trace_source.compute(sj, rows_by)
        cols = [m for m in list(sj["measures"]) + list(sj["ratios"]) + list(sj.get("derived") or {}) if m in row]
        diffs = [m for m in cols if not values_equal(row.get(m), got.get(m), sj["tolerance"])]
        parts = "; ".join(f"{m} {fmt(row.get(m))} vs recomputed {fmt(got.get(m))}" for m in cols)
        n = sum(len(v) for v in rows_by.values())
        status = "FAIL" if diffs else "PASS"
        sentence = (f"{x0['unit']} {label}: group {keyd} recomputed by {where} ({fmt(n)} source rows): {parts} "
                    f"-> {status}.")
    return {"unit": x0["unit"], "selection": sel, "stratum": label, "row_id": x0["row_id"], "lookup_id": gid,
            "upstream": where, "cardinality": "source_join", "status": status, "sentence": sentence, "cols": cols,
            "failed_cols": diffs, "where": where}


NO_MEASURE = "NO_MEASURE"
NO_MEASURE_NOTE = "no measure to trace"


def coverage(sman: Mapping[str, Any], plans: Mapping[str, Mapping[str, Any]], ucols: Mapping[str, Sequence[str]],
             results: Sequence[Mapping[str, Any]], measures: Sequence[str],
             step_na_level: int | None = None) -> dict[str, Any]:
    """Per-unit coverage and the p8_trace status (fix r1 A-H5).

    Fix r2 A2-M1: a measure counts as recomputed from source only through PASS source lookups, and not at all when
    any source lookup of it FAILed; those are listed in `measures_source_failed` (per unit and for the run).
    Fix r5 M1: per (unit, measure) pair, every sampled row of the unit must match from source (module docstring);
    a measure matched in some pairs only is `measures_partially_recomputed`, with unit counts.
    """
    units, reasons = [], []
    seen: set[str] = set()
    failed_all: set[str] = set()
    pairs: dict[str, list[bool]] = {}  # fix r5 M1: measure -> one complete/open flag per unit carrying it
    matched_somewhere: set[str] = set()
    for u in sman["units"]:
        name = u["unit"]
        plan = plans.get(name, {"upstream": []})
        rs = [r for r in results if r["unit"] == name]
        keycols = {k for up in plan.get("upstream", []) for k in (up.get("key_map") or {})}
        keycols |= set((plan.get("source_join") or {}).get("grain_defs") or {})
        cols = list(ucols.get(name, []))
        mine = [m for m in measures if m in cols and m not in keycols]
        compared: dict[str, list[str]] = {}
        for r in rs:
            if r["status"] in ("PASS", "FAIL"):
                for c in r["cols"]:
                    compared.setdefault(c, [])
                    if r["where"] not in compared[c]:
                        compared[c].append(r["where"])
        # fix r4 C4-17: a source group row compares several measures at once; a FAIL row fails only the measures that
        # differed (failed_cols), the others in it were recomputed and matched
        src_rows = [r for r in rs if r["status"] in ("PASS", "FAIL") and str(r["where"]).startswith("source ")]
        src_fail = {c for r in src_rows for c in (r["failed_cols"] if "failed_cols" in r and r["status"] == "FAIL"
                                                  else (r["cols"] if r["status"] == "FAIL" else []))}
        src_pass = {c for r in src_rows for c in r["cols"]
                    if c not in (r.get("failed_cols") or []) and not (r["status"] == "FAIL" and "failed_cols" not in r)}
        # fix r5 M1: a pair is complete only when every sampled row of the unit matched the measure from source
        def _matched(r: Mapping[str, Any], m: str) -> bool:
            if r not in src_rows or m not in r["cols"]:
                return False
            return r["status"] == "PASS" or ("failed_cols" in r and m not in r["failed_cols"])
        row_ids = list(dict.fromkeys(r.get("row_id") for r in rs))
        src = {m for m in src_pass - src_fail
               if row_ids and all(any(_matched(r, m) for r in rs if r.get("row_id") == rid) for rid in row_ids)}
        # fix r5 addendum 5: with step-to-step not applicable, source recompute alone decides coverage
        not_rec = [m for m in mine if (m not in src if step_na_level is not None else m not in compared)]
        n_nt = sum(1 for r in rs if r["status"] == "NOT_TRACED")
        status = ("FAIL" if any(r["status"] == "FAIL" for r in rs) else
                  "NOT_RUN" if (n_nt or not_rec or not rs) else "PASS")
        # fix r6 (cold reviewer LOW): a unit that carries no deliverable measure and whose lookups are all
        # NOT_APPLICABLE traced nothing; it is reported "no measure to trace", never PASS (and does not block)
        no_measure = not mine and bool(rs) and all(r["status"] == "NOT_APPLICABLE" for r in rs)
        if no_measure:
            status = NO_MEASURE
        if n_nt:
            reasons.append(f"{name}: {n_nt} lookup(s) not traced")
        if not_rec:
            reasons.append(f"{name}: measures not recomputed: {', '.join(not_rec)}")
        if not rs:
            reasons.append(f"{name}: no lookups")
        seen |= {m for m in mine if m in compared}
        for m in mine:
            pairs.setdefault(m, []).append(m in src)
        matched_somewhere |= {m for m in mine if m in src_pass}
        failed_all |= {m for m in mine if m in src_fail}
        units.append({"unit": name, "status": status, "plan_source": plan.get("plan_source"), "measures": mine,
                      "compared": {m: compared[m] for m in mine if m in compared},
                      "measures_not_recomputed": not_rec, "measures_from_source": [m for m in mine if m in src],
                      "measures_not_from_source": [m for m in mine if m not in src],
                      "measures_source_failed": [m for m in mine if m in src_fail],
                      "not_recomputed_reasons": {m: ((plan.get("source_join") or {}).get("reasons") or {}).get(
                          m, "not compared by any lookup of the plan") for m in not_rec},
                      "columns_not_compared": [c for c in cols if c not in keycols and c not in compared],
                      "not_traced": n_nt, **({"note": NO_MEASURE_NOTE} if no_measure else {})})
    never = [m for m in measures if m not in seen]
    if never:
        reasons.append(f"deliverable measures compared in no unit: {', '.join(never)}")
    if not sman["units"]:
        reasons.append("no sampled unit")
    status = ("FAIL" if any(u["status"] == "FAIL" for u in units) else
              "NOT_RUN" if reasons else "PASS")
    complete = [m for m in measures if pairs.get(m) and all(pairs[m]) and m not in failed_all]
    partial = {m: {"units_recomputed": sum(pairs[m]), "units_required": len(pairs[m])} for m in measures
               if m in matched_somewhere and m not in complete and m not in failed_all}
    return {"deliverable_measures": list(measures), "units": units, "measures_never_compared": never,
            "measures_from_source": complete, "measures_partially_recomputed": partial,
            "measures_source_failed": [m for m in measures if m in failed_all], "status": status,
            "not_run_reasons": reasons,
            **({"step_to_step": f"not applicable at level {step_na_level}"} if step_na_level is not None else {})}


def coverage_line(u: Mapping[str, Any]) -> str:
    """'- Coverage, unit: compared m against step X; ...; not recomputed from source: ...; columns not compared: ...'."""
    if u.get("status") == NO_MEASURE:
        return f"- Coverage, {u['unit']}: {NO_MEASURE_NOTE} (no deliverable measure; every lookup not applicable)"
    comp = u.get("compared") or {}
    parts = [("compared " + "; ".join(f"{m} against {', '.join(w)}" for m, w in comp.items())) if comp
             else "compared no deliverable measure"]
    if u.get("measures_not_recomputed"):
        parts.append("not recomputed: " + ", ".join(u["measures_not_recomputed"]))
    parts.append("not recomputed from source: " + (", ".join(u.get("measures_not_from_source") or []) or "none"))
    parts.append("columns not compared: " + (", ".join(u.get("columns_not_compared") or []) or "none"))
    return f"- Coverage, {u['unit']}: " + "; ".join(parts)


def write_report(run_dir: Path, sman: Mapping[str, Any], results: Sequence[Mapping[str, Any]],
                 cov: Mapping[str, Any] | None = None) -> None:
    """trace_report.md: per unit, FAIL lines first, the coverage line, the assurance sentence; G12 sign-off line."""
    cov_by = {u["unit"]: u for u in (cov or {}).get("units", [])}
    smod = _sample_module()
    lines = ["# Trace report", "", f"Run folder: `{Path(run_dir).name}`. Salt and draw: `samples/sample_manifest.json` "
             f"(N={sman['n']}, level {sman['level']}).", "",
             "Reader rule: one line per sampled row and upstream; FAIL lines first; targeted (T_) lines are never "
             "counted toward the random N.", ""]
    if (cov or {}).get("step_to_step"):  # fix r5 addendum 5
        lines += [f"Note: step-to-step sampling: {cov['step_to_step']} (no step CSV is retained); coverage is "
                  "decided by source recompute alone.", ""]
    order = {"FAIL": 0, "NOT_TRACED": 1, "NOT_APPLICABLE": 2, "PASS": 3}
    for u in sman["units"]:
        rs = [r for r in results if r["unit"] == u["unit"]]
        rand = [r for r in rs if r["selection"] in ("random", "census")]
        targ = [r for r in rs if r["selection"] == "targeted"]
        rand_fail_rows = {r["row_id"] for r in rand if r["status"] == "FAIL"}
        lines.append(f"## {u['unit']}: random {len({r['row_id'] for r in rand})} (fail {len(rand_fail_rows)}), "
                     f"targeted {len({r['row_id'] for r in targ})} "
                     f"(fail {len({r['row_id'] for r in targ if r['status'] == 'FAIL'})})")
        lines.append("")
        for r in sorted(rs, key=lambda r: (order.get(r["status"], 3), r["selection"] != "targeted", r["lookup_id"])):
            lines.append(f"- {r['sentence']}")
        if u.get("empty_strata"):
            lines.append(f"- Empty strata (reported, may itself be the finding): {', '.join(u['empty_strata'])}.")
        if u["unit"] in cov_by:
            lines.append(coverage_line(cov_by[u["unit"]]))
        lines.append("")
        lines.append("Assurance: " + smod.assurance(int(sman["n"]), int(u["residual_rows"]), bool(u["census"]),
                                                    len(rand_fail_rows)) + ".")
        lines.append("")
    for nr in sman.get("not_run", []):
        lines.append(f"- {nr['unit']}: {nr['reason']}.")
    if cov is not None:
        lines += ["", f"Coverage status: {cov['status']}" + (f" ({'; '.join(cov['not_run_reasons'])})"
                                                            if cov.get("not_run_reasons") else "") + ".",
                  "Deliverable measures recomputed from a source table: "
                  + (", ".join(cov["measures_from_source"]) or "none (sampling is step-to-step only)") + "."]
        not_src = [m for m in cov["deliverable_measures"] if m not in cov["measures_from_source"]]
        if not_src:
            lines.append(f"Deliverable measures not recomputed from source: {', '.join(not_src)}.")
        if cov.get("measures_source_failed"):  # fix r2 A2-M1: a failed recompute is surfaced, not counted
            lines.append("Source recompute failed (FAIL rows above) for: "
                         + ", ".join(cov["measures_source_failed"]) + ".")
    lines += ["", "G12: Max reads this report, including what each unit compared and what it did not recompute from "
              "source, and signs it in decisions.csv (gate G12)."]
    c2.write_text(Path(run_dir) / TRACE / REPORT, "\n".join(lines) + "\n")
    c2.write_text(Path(run_dir) / REPORT, "\n".join(lines) + "\n")


def _main(a: Any) -> int:
    c2.phase_check(a.run_dir, ["P8"])
    if a.cmd == "generate":
        out = generate(a.run_dir)
        n_bq = sum(1 for x in out["lookups"] if x.get("engine") == "bigquery")
        todo = [x["lookup_id"] for x in out["lookups"] if x.get("engine") == "bigquery"
                and _result(Path(a.run_dir), x["lookup_id"], x["sql_path"]) is None]  # stale counts as missing
        n_pend = sum(1 for x in out["lookups"] if x.get("engine") == "pending")
        print(f"{len(out['lookups'])} lookups ({n_bq} for the warehouse runner, {len(todo)} without a result, "
              f"{n_pend} pending an earlier stage)")
        return int(ExitCode.PASS)
    code, out = evaluate(a.run_dir)
    if code == ExitCode.FAIL:  # fix r4 C4-16: an open G8 request, so the Stop hook and decide.py see the gate
        n = sum(1 for r in out if r["status"] == "FAIL")
        c2.write_gate_request(a.run_dir, "G8", "P8", "trace.py", f"p8_trace FAIL: {n} sampled row(s) not reproduced "
                              "from source", {"check_id": "p8_trace", "failed_rows": n,
                                              "options": list(G8_OPTIONS)})
        sys.stderr.write(f"NEEDS_DECISION: G8: {n} sampled row(s) not reproduced from source (p8_trace FAIL); "
                         f"decide.py --gate G8 --check-id p8_trace --outcome {'|'.join(G8_OPTIONS)}\n")
        return int(ExitCode.NEEDS_DECISION)
    nt = sum(1 for r in out if r["status"] == "NOT_TRACED")
    print(f"{len(out)} lookups: none failed, {nt} not traced; coverage in {TRACE}/{COVERAGE}")
    c2.archive_own_request(a.run_dir, "trace.py", ("G8",), "P8")  # fix r4 C4-10, C4-16
    return int(ExitCode.PASS)


G8_OPTIONS = ("explained", "rewrite", "re_pin", "csv_only", "stop")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    ap = c2.base_parser("trace.py", "P8 trace to source (spec 6.2)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("generate")
    sub.add_parser("evaluate")
    return c2.run_cli_args(_main, ap, argv)


if __name__ == "__main__":
    sys.exit(main())

---
name: sample
description: "Internal phase of query-trail. Do NOT use for general SQL, 'reconcile' or 'how many' questions. Phase P8 (Sample and trace): salted hash-rank draw, key-bounded lookups to source, one sentence per sampled row; called only by the query-trail run orchestrator."
user-invocable: false
---

# sample (P8)

Purpose: after 100 % automated checks, draw targeted strata and a random N per sampling unit, trace each sampled row back to its source, and give Max one sentence per row to read. The sample is fixed by the salt; nobody chooses rows.

Commands use `QT_PY`, `PLUGIN`, `RUN`, `ENV_FILE` as the run skill defines them.

## 1. State check (first action)

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/lib/state.py" --state "$RUN/run_state.json" check --phase P8` — exit 2: do nothing and return.

## 2. Trace plan (D1, before the draw)

Propose `trace_plan.json`: per unit (`deliverable_B` at 60 and 70; each step at 80 and above) the cardinality (1_to_1, many_to_1, 1_to_many), the upstream (`source_table` as `alias.table`, or `prior_step`), the key map, filters, measures for many_to_1 (ratios recompute numerator and denominator from source), and a tolerance. Max may read it; it never depends on which rows are drawn. Its JSON shape (`trace.py` reads `units`; a unit left out gets the default plan):

```json
{"units": {"deliverable_B": {
  "cardinality": "many_to_1", "tolerance": 0,
  "upstream": [{"ref": "{{ds_sales}}.qt_orders", "kind": "source_table",
                "key_map": {"order_month": "DATE_TRUNC(DATE(created_at), MONTH)"},
                "partition_filter": {"column": "created_at", "start": "2025-01-01", "end": "2026-01-01"},
                "filters": ["status = 'Complete'"],
                "measures": {"revenue": "SUM(sale_price)", "orders": "COUNT(DISTINCT order_id)"},
                "compare": {}, "compute": {}}]}}}
```

`kind` is `source_table` (`ref` as `alias.table`) or `prior_step` (`ref` a step name); `key_map` maps a sample column to an upstream column or SQL expression; `measures` (many_to_1) are aggregate SQL over the upstream rows; `compare` maps a sample column to an upstream column and `compute` to a SQL expression over the upstream row (1_to_1).

Source-join recompute: when the card's bindings define the deliverable grain and measures over `bindings.tables` (`measures` {name: {expr, table[, filter]}}, `derived` {name: expression over measure names}, `date` {table, column, truncation, timezone} for the date grain column, `joins` [{left, right, on_keys, type}]; the older `bindings.columns` is read only as a fallback for old run folders), trace.py recomputes them from source for each sampled grain value: one lookup per source table, the first restricted by the grain, the next by the join keys collected from the previous result (join keys from the join steps in `plan.json`), then aggregated locally with the card's definitions. It runs in stages: `generate`, `warehouse_jobs.py trace --only <new ids>`, `generate` again; repeat (run `warehouse_jobs.py trace` until trace.py reports both 0 pending and 0 without a result, the two counters `generate` prints: "N lookups (... K without a result, P pending an earlier stage)"), then `evaluate`. Below level 90 no step CSV is retained, so step-to-step lookups are NOT_APPLICABLE (not a coverage gap; the sheet says "step-to-step sampling: not applicable at level N") and source recompute alone decides coverage. A measure whose definition or join path cannot be resolved is listed in `measures_not_recomputed` with the reason.

A unit without an entry gets trace.py's default plan (1_to_1 on the step grain to every input; the deliverable 1_to_1 to the final step), and every plan gets the card's chain ratios recomputed from their parts. `trace/trace_plan_effective.json` shows what ran. Aim the plan at the deliverable measures (the output contract's numeric non-key columns): each one a unit carries must be compared there, preferably from a source table, or p8_trace is NOT_RUN.

## 3. Draw

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/sample.py" --run-dir "$RUN"` — exit 0 drawn (`samples/`); exit 2 refused (state, missing input, or a salt different from the one already recorded). Population ≤ N traces all of it.

## 4. Trace

1. `"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/trace.py" --run-dir "$RUN" generate` (lookups in `trace/lookups.json`).
2. `"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/warehouse_jobs.py" --run-dir "$RUN" trace --env-file "$ENV_FILE"` (source lookups, pinned to the run's as_of_utc, dry run and cap; exit 2 after the pin deadline: G8 re-pin or csv_only).
3. `"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/trace.py" --run-dir "$RUN" evaluate`

| evaluate exit | Meaning | Next |
|---|---|---|
| 0 | no sampled row failed; p8_trace PASS, or NOT_RUN when coverage is incomplete (rows not traced, or a deliverable measure not compared; a measure counts as recomputed from source only when every sampled row of every unit carrying it matched from source, else it is listed in `measures_partially_recomputed` with unit counts: `trace/trace_coverage.json`) | G12. Incomplete coverage does not change the verdict's whole-vs-parts wording; it adds "; sampling incomplete: <measures> not recomputed from source" (or "; sampling: step-to-step only") and keeps "verified" and "traced" off the report. Widen the plan and trace again to remove the qualifier. G12 is refused until `trace_report.md` lists the measures not recomputed from source |
| 3 | a sampled row not reproduced from source (p8_trace FAIL): `gate_request.json` opens G8 with check_id `p8_trace` | G8 (`explained` with `--check-id p8_trace`, `rewrite`, `re_pin`, `csv_only`, `stop`) and a required-check FAIL; still G12; a later exit 0 archives the request |
| 2 | a warehouse lookup has no result yet; or a result written for another SQL, as-of or attempt (its stamp is not the current lookup's; stderr names the file) | run step 2; for a stale stamp, run `generate` and step 2 again |

A lookup over the cap: narrower key bounds in the trace plan, or "not traced" with the reason.

## Never

Change the sample: no new salt, no re-draw, no dropping a row, no swapping a unit after seeing a result.

## Artifacts left

`trace_plan.json`, `samples/` (unit CSVs, `sample_manifest.json` with salt, N, strata, wording), `trace/` (lookups, SQL, results, `trace_results.csv`, `trace_coverage.json`, `trace_plan_effective.json`), `trace_report.md` (per unit: what was compared against what, and what was not recomputed from source), the `p8_trace` row in `checks/checks_manifest.csv`.

## Hands to

G12 (Max reads and signs `trace_report.md`; one sentence per sampled row), G8 on a FAIL, then P9.

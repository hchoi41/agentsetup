---
name: logic
description: "Internal phase of query-trail. Do NOT use for general SQL, 'reconcile' or 'how many' questions. Phase P9 (Logic tests): ratio chain (톱니 사슬), population mesh, MECE, reference tie-out, PoP bands; called only by the query-trail run orchestrator."
user-invocable: false
---

# logic (P9)

Purpose: test the meaning of the answer, not its split: the ratio chain telescopes, populations mesh, parts are MECE against a stated universe, and the result ties out to the reference within the tolerance fixed at G1a. Every definition and tolerance comes from the metric card frozen at G1a.

Commands use `QT_PY`, `PLUGIN`, `RUN`, `ENV_FILE` as the run skill defines them.

## 1. State check (first action)

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/lib/state.py" --state "$RUN/run_state.json" check --phase P9` — exit 2: do nothing and return.

## 2. Warehouse inputs

When a card test names a source other than the deliverable (for example a MECE universe at order grain), write the query as `logic_sql/<name>.sql` (placeholders, one SELECT) and list it in `logic_inputs.json` (`{"inputs": [{"name", "sql_path"}]}`), then:

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/warehouse_jobs.py" --run-dir "$RUN" logic --env-file "$ENV_FILE"`

Exit 0: `logic_inputs/<name>.csv` (pinned to the run's as_of_utc, capped, contract CSV); 3: over the cap (G15); 2: allow-list, state or pin deadline.

Card sources take three forms: `deliverable`, a run-relative CSV, or `step:<name>`. A `step:<name>` source reads the step's CSV when the run kept one; otherwise (always at level 60, where no step CSV is kept) it is a metered warehouse query of that step's executed SQL. Run these in order before the tests:

1. `"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/logic_tests.py" --run-dir "$RUN" --write-inputs` — adds `{"name": "step_<name>", "sql_path": "executed/NN.run.sql", "out": "logic_inputs/step_<name>.csv"}` to `logic_inputs.json` for each step source without a CSV (exit 0, also when there is nothing to add). A step name that plan.json does not have is exit 2 with plan.json's step names: that is a card problem (back to P1).
2. `"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/warehouse_jobs.py" --run-dir "$RUN" logic --inputs logic_inputs.json --env-file "$ENV_FILE"` — runs each entry (pinned, capped, metered by the run's budget ledger) and writes its `out` CSV. Exit 3: G15 (over the cap or the run budget).
3. Section 3 below (`logic_tests.py` proper). It refuses (exit 2, nothing written) only when an `out` CSV is still missing, and the message names the command in step 2.

The reference values (`reference_values.csv`: grain columns plus `reference_value`) come from Max or his extract, with the provenance in `run_config.json reference` (source, extraction method, as_of, grain, basis, tolerance).

## 3. Tests

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/logic_tests.py" --run-dir "$RUN"`

| Exit | Meaning | Next |
|---|---|---|
| 0 | every test PASS (reference agree, or none) | P10 |
| 1 | a FAIL (MECE gap or overlap, mesh, chain), reference disagree, or the cards changed after G1a | the card change is a new run; G8 for a FAIL; a disagreeing reference fixes the wording "not reconciled (to reference)" unless Max accepts the difference at G4 (below) |
| 2 | refused, nothing written: a predicate that is not an expression, a mean of entity ratios used as a chain, missing reference provenance, a `step:<name>` that plan.json does not have (the message lists its step names), or a step source whose `out` CSV is still missing (run `--write-inputs`, then `warehouse_jobs.py ... logic --inputs logic_inputs.json`, then this again) | report; back to P1 for card problems |

No reference: outcome "none", which triggers G16 at close.

A disagreeing reference goes to G4 (here at P9): accept, reject or stop. An accept names the check and a category: `decide.py --gate G4 --outcome accept --check-id p9_reference --category timing|rounding|scope|known_source_defect|other_with_reason --input-file checks/logic_reference.csv ...`. The verdict then carries "; reference differs: N rows accepted at G4 (categories)", never "agrees".

A test the card leaves out is written as NOT_RUN and required: a ratio metric with no chain block (card_freeze.py refuses that card at G1a), or no `mece` list (`mece: none` in the card marks MECE not applicable). A required test that did not run keeps the verdict at "not reconciled" until Max writes a G8 reason naming it.

## 4. Explain (D1)

Read `checks/logic_*.csv` to Max: which links telescope, the eligible population, any domain values outside the card's domain. Numbers only.

## Never

Set or change a tolerance, a universe or a reference after seeing results. Those were fixed at G1a.

## Artifacts left

`logic_inputs.json`, `logic_sql/`, `logic_inputs/`, `reference_values.csv`, `checks/logic_*.csv`, `checks/logic_reference.json`, P9 rows in `checks/checks_manifest.csv`.

## Hands to

G13 for a data-owner question (close status, restatement), G8 on a FAIL, then P10.

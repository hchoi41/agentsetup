---
name: decompose
description: "Internal phase of query-trail. Do NOT use for general SQL, 'reconcile' or 'how many' questions. Phase P5 (Decompose): D0 split of B into one-operation steps, lint, compile with the pin; called only by the query-trail run orchestrator."
user-invocable: false
---

# decompose (P5)

Purpose: cut B into chain(B), one operation per step, so each step can be checked and said in one sentence. The splitter is code; you only explain its output to Max.

Commands use `QT_PY`, `PLUGIN`, `RUN`, `ENV_FILE` as the run skill defines them.

## 1. State check (first action)

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/lib/state.py" --state "$RUN/run_state.json" check --phase P5` — exit 2: do nothing and return.

## 2. Split

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/split.py" --run-dir "$RUN" --fetch-schema --env-file "$ENV_FILE"`

| Exit | Meaning | Next |
|---|---|---|
| 0 | `steps/NN_<verb>_<object>.sql` and `plan.json` | lint |
| 3 | a refused construct (NOT IN subquery, non-equality correlation, select-list correlated scalar, scripting, DML); `gate_request.json` G10 | orchestrator asks G10: a labelled hand split (B vs chain(B) stays mandatory) or a rewrite of B (back to P4) |
| 2 | B does not parse or qualify against the catalog | back to P4 |

## 3. Lint

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/lint.py" --run-dir "$RUN"` — exit 0: no unwaived ERROR; exit 1: an N1–N9 or A1 ERROR (for example ROW_NUMBER without a total order). Ask G9 through the orchestrator: a waiver with a reason (Max adds it to `run_config.json lint_waivers` before G1a or records it at G9) or a rewrite of B. Warnings do not stop the phase; tell Max each B2 warning in one sentence. For a LEFT JOIN ("joined but unused: <table>"): B joins it and reads none of its columns outside its own ON clause, so it only costs bytes and could fan out rows; he says which column it supplies, or asks for B without it. For an INNER, CROSS, RIGHT, FULL or comma join ("INNER JOIN to <table> keeps only rows with a match ..."): no column of it is read, but the join still decides which rows B keeps, so dropping it changes the number; never advise dropping it. Ask whether that filter is intended: if yes it is bound as a filter row at G3, if not the table is dropped at G2.

## 4. Compile

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/compile.py" --run-dir "$RUN"` — exit 0: `executed/NN.run.sql`, `B.run.sql` with cumulative CTEs and FOR SYSTEM_TIME AS OF on every base reference (provisional as-of; P6 re-renders); exit 2: a forbidden statement or function, or a table missing from the pin plan.

## 5. Step names the card refers to

The metric card may name steps as sources (`mece[i].source` or `pop_bands.source`: `deliverable`, a run-relative CSV path, or `step:<name>`). Every `step:<name>` must name a step of `plan.json` by its name (`steps[].name`, e.g. `03_project_read_orders`), its CTE name (`steps[].cte`, e.g. `read_orders`) or its number (`03`); the author keeps the CTE names the card references, and split.py warns on stderr about a card source that matches no step; tell Max at G11 when a name the card uses does not exist, because P9 refuses it (exit 2, listing plan.json's step names). At P9 `step:<name>` resolves to that step's CSV when the level kept one, else to a metered, pinned warehouse query of the step's executed SQL (`executed/NN.run.sql`) through `warehouse_jobs.py logic`.

## 6. Readability test

For each step in `plan.json` write one plain sentence (what rows go in, what comes out) into `step_sentences.json` (`{"<step name>": "<sentence>"}`). Give Max the list for G11. A join step that is meant to multiply rows says so in its sentence ("fans out"); with G11 approved, `run_steps.py` then records that step's row increase as `join_fan_out` from the G11 sentence instead of `unexplained`. A step Max cannot say in one sentence: split finer (hand split via G10) and ask G11 again.

## Never

Hand-split without a G10 decision, or edit `steps/` after G11 (the edit guard blocks it; `run_steps.py` refuses a step whose hash differs from `plan.json`).

## Artifacts left

`catalog_schema.json`, `steps/`, `plan.json`, `lint_report.csv`, `executed/`, decisions rows G9, G10, G11.

## Hands to

G10 or G9 when needed, then G11, then P6.

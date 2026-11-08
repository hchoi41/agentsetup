---
name: run-steps
description: "Internal phase of query-trail. Do NOT use for general SQL, 'reconcile' or 'how many' questions. Phase P6 (Run steps): fixes as_of_utc, runs every step job and whole B through one capped runner, then local replay; called only by the query-trail run orchestrator."
user-invocable: false
---

# run-steps (P6)

Purpose: run each step of chain(B) and whole B (and A) as BigQuery jobs pinned to one `as_of_utc`, and write exactly what the confidence level asks for each step. You narrate; the scripts decide.

Commands use `QT_PY`, `PLUGIN`, `RUN`, `ENV_FILE` as the run skill defines them.

## 1. State check (first action)

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/lib/state.py" --state "$RUN/run_state.json" check --phase P6` — exit 2: do nothing and return.

## 2. Run

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/run_steps.py" --run-dir "$RUN" --env-file "$ENV_FILE"`

The script refuses edited steps, fixes `as_of_utc` (business as-of, else P6 start minus 5 minutes), checks the pin deadline, dry-runs every job against the per-job cap, checks the sum of reservations (max(dry run, 10 MiB x tables referenced) per job) plus what the run already spent against the per-run budget before anything is billed, then runs each job with labels, cache off and the byte cap behind the run's ledger (`manifest.json` budget), streams once, and writes the level's artifacts. Per step it records rows in, rows out and a reason (`rows_in`, `row_delta_reason`, `row_delta_source`); a filtered read step gets one cheap pinned COUNT(*) job for its source rows.

| Exit | Meaning | Next |
|---|---|---|
| 0 | every job ran, every step check passed | replay |
| 3 | G15: a dry-run estimate over the per-job cap, spent + reserved over the per-run budget, a job BigQuery refused over maximum_bytes_billed, or a result over the stream cap (`gate_request.json`) | orchestrator asks G15: filter (a new run), cut to local DuckDB, raise the cap to a number Max types, or stop |
| 1 | a step check failed (row count vs COUNT(*), header names, streamed hash vs CSV hash, cache hit, or `p6_step_NN_rows_in_out`: a join that increased rows or a count that moved the wrong way, reason `unexplained`) | G8; a row-count failure is never accepted; an unexplained row change is explained only by a G8 row with `--check-id p6_step_NN_rows_in_out` |
| 2 | edit guard (a step differs from `plan.json`), pin deadline passed, missing input | deadline: G8 offers re-pin (new attempt) or csv_only (PROVISIONAL) |
| 4 | engine or environment error | tell Max; one unchanged retry after the fix |

## 3. Replay

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/replay.py" --run-dir "$RUN"` — exit 0: replayable steps match (EXACT under a matching compat binding, else TOLERANT); exit 1: a replayed step differs (G8); exit 2: manifest missing.

## 4. Narrate

Tell Max in a few lines: the as_of_utc, the pin status, rows in -> rows out per step with the reason, bytes billed and the run's spent / budget (from `run_steps_report.json` and `manifest.json` budget), steps degraded by a local cap. Numbers only; no judgement words.

## Never

Decide anything or choose wording. Never rerun a job to "get a better result"; a rerun is an escalation re-run with a new attempt number, ordered by Max.

## Artifacts left

`outputs/` (per step: row count, schema sidecar, multiset hash; aggregates at 70; sample and strata at 80; full CSV at 90 under the local cap and 100; the deliverable CSV at every level), `executed/`, `manifest.json`, `run_steps_report.json`, `checks/run_steps_checks.csv`, P6 rows in `checks/checks_manifest.csv`, `checks/replay_status.csv`.

## Hands to

G15 or G8 when needed, otherwise P7.

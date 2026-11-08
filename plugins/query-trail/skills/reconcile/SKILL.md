---
name: reconcile
description: "Internal phase of query-trail. Do NOT use for general SQL, 'reconcile' or 'how many' questions. Phase P7 (Reconcile): B vs chain(B), then A vs chain(B) in second-author mode, by the local checker; called only by the query-trail run orchestrator."
user-invocable: false
---

# reconcile (P7)

Purpose: compare the whole query with its own split (split fidelity) and, in second-author mode, the second author's whole with the split, on typed CSVs. The script writes a status and an exit code, never wording.

Commands use `QT_PY`, `PLUGIN`, `RUN` as the run skill defines them.

## 1. State check (first action)

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/lib/state.py" --state "$RUN/run_state.json" check --phase P7` — exit 2: do nothing and return.

## 2. Reconcile

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/reconcile.py" --run-dir "$RUN"`

Order: contracts (names, order, keys, types, scale against the G1b contract), counts (rows, NULL keys, grain), multiplicity diff, matching (exact canonical rows on non-FLOAT columns; FLOAT columns after matching on the key within the run's tolerance), differing rows named in `checks/mismatch_rows.csv` (always written). At level 60 only contracts, counts and the hash test can run.

Each comparison ends PASS, FAIL or INCONCLUSIVE. INCONCLUSIVE means nothing established value agreement: the hash could not be compared and no rows were compared, or the method was "multiplicity only, values not matched". It is never positive.

| Exit | Meaning | Next |
|---|---|---|
| 0 | every comparison PASS | P8 |
| 1 and `route: P5` in `checks/reconcile_status.json` | a comparison differs (B vs chain(B), or A vs chain(B) in second-author mode) | back to P5 with the classifier output (count, key, value or type): re-split or rewrite in a new attempt (A vs chain(B): re-author; blind keeps its level, informed is a dependent revision, IND-0); at most 2 rounds, then stop with a G8 reason (hand split via G10 is a re-split). Never G4: no decision turns this difference into agreement; the verdict stays "not reconciled: decomposition differs (classes)" |
| 1 and `route: G8` | INCONCLUSIVE (reason in `inconclusive_reason`) | offer an escalation re-run (level 90, same as_of_utc) or a declared key in a new run; otherwise stop with a G8 reason. The verdict stays "not reconciled (comparison inconclusive: reason)" |
| 3 | G15: a side's digest is warehouse-side (over the stream cap) and no rows were compared; only `gate_request.json` is written | G15: filter, cut to local, raise the cap, or stop |
| 2 | manifest or a CSV missing | report; back to P6 |

At level 60 a mismatch cannot be localised to a step; tell the orchestrator so it can offer Max an escalation re-run at a higher level with the same as_of_utc.

## 3. Explain (D1)

Explain the classifier output to Max in plain words, citing `checks/reconcile_*.csv` and `checks/mismatch_rows.csv` rows. Facts only.

## Never

Adjudicate a difference between your own author's sides: the seat that wrote B never decides whether a B difference is acceptable. Only Max decides: re-split, rewrite or stop (G8). G4 is for reference differences at P9, never for B vs chain(B) or A vs chain(B).

## Artifacts left

`checks/reconcile_<comparison>.csv`, `checks/mismatch_rows.csv`, `checks/reconcile_status.json`, P7 rows in `checks/checks_manifest.csv`.

## Hands to

P5 (split fidelity, or A vs chain(B)), G8 (inconclusive, or a stop), G15 (stream cap), or P8.

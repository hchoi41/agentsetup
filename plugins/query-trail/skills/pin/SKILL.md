---
name: pin
description: "Internal phase of query-trail. Do NOT use for general SQL, 'reconcile' or 'how many' questions. Phase P3 (Pin plan): per-table pin routes, unpinnable list, late-arrival bands; called only by the query-trail run orchestrator."
user-invocable: false
---

# pin (P3)

Purpose: decide, per table, how both sides of every comparison will read the same frozen data under read-only rights. `as_of_utc` itself is fixed later, at P6 start, so the human waits of P4 and P5 stay outside the time-travel window.

Commands use `QT_PY`, `PLUGIN`, `RUN`, `ENV_FILE` as the run skill defines them.

## 1. State check (first action)

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/lib/state.py" --state "$RUN/run_state.json" check --phase P3` — exit 2: do nothing and return.

## 2. Batch marker (D1)

If the source has an ETL batch id or a close marker, propose it to Max as the business pin; it is recorded beside `as_of_utc`. If none exists, say so; do not invent one.

## 3. Pin plan

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/pin_plan.py" --run-dir "$RUN" --env-file "$ENV_FILE" [--batch-marker M]`

| Exit | Meaning | Next |
|---|---|---|
| 0 | `15_pin_plan.json` written; route `system_time` (FINAL candidate) or `csv_only` (PROVISIONAL, a flag, not a failure); `13_table_decision.md` refreshed with that pin route (one line says so) | tell the orchestrator the pin status in one sentence; P4 (no second `discover.py decide` is needed) |
| 3 | an open late-arrival band (`gate_request.json` G13); the plan and the refreshed `13_table_decision.md` are written | orchestrator asks G13; Max answers (closed, accepted_provisional); rerun |
| 2 | preflight not PASS, shortlist missing, or a table not in the plan | report the reason |
| 4 | metadata read failed | tell Max; one unchanged retry after the fix |

The decision file refresh: right after the plan is written, `pin_plan.py` rewrites `13_table_decision.md` (written by `discover.py decide` at P2) with the pin route, the same text `decide` would write now, so P4's packet freeze accepts it. If the rewrite is refused (an invalid value map, a binding outside the lock), stderr says "13_table_decision.md not refreshed" with the reason; fix that and rerun `discover.py --run-dir "$RUN" decide` before P4.

Read the plan back to Max when the route is `csv_only` or a table is unpinnable (a view that cannot be resolved, an external table, a snapshot): the run is announced PROVISIONAL now, and he may stop.

## Never

Pick a route the probe did not allow. A view is resolved to its base tables (time travel on a view is silently ignored); a table outside its window is `csv_only`.

## Artifacts left

`15_pin_plan.json` (routes, windows, smallest window, the deadline rule as_of + smallest window − 24 h, unpinnable list, bands, business pin); `13_table_decision.md` refreshed with the pin route.

## Hands to

G13 when a band is open; otherwise P4.

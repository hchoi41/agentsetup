---
name: discover
description: "Internal phase of query-trail. Do NOT use for general SQL, 'reconcile' or 'how many' questions. Phase P2 (Table discovery): privilege-ladder listing, profiling, trap scan, value map and bindings; called only by the query-trail run orchestrator."
user-invocable: false
---

# discover (P2)

Purpose: find the tables the card needs, prove their grain and freshness, surface the traps, and fix the value map and the output contract with Max. Order (DB-06): shortlist G2, profiling, trap scan, value map G3, bindings G1b.

Commands use `QT_PY`, `PLUGIN`, `RUN`, `ENV_FILE` as the run skill defines them.

## 1. State check (first action)

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/lib/state.py" --state "$RUN/run_state.json" check --phase P2` — exit 2: do nothing and return.

## 2. Listing

Write `discovery_terms.json` yourself from the cards (do not ask Max first), run the steps below, then Max is asked at G2. Shape (`config/discovery_terms.schema.json`; `build` refuses a wrong shape naming the field and the shape it expects; a list field never takes a bare string):

| Field | Shape | Example |
|---|---|---|
| `terms` | list of search words | `["orders", "sale price"]` |
| `synonyms` | `{term: [words]}` | `{"revenue": ["sales"]}` |
| `required_columns` | list of column names | `["order_id", "sale_price"]` |
| `datasets` | list of env dataset aliases | `["ds"]` |
| `key_hypotheses` | `{"<alias>.<table>": [key columns]}`, always a list | `{"ds.qt_orders": ["order_id"]}` |
| `date_columns` | `{"<alias>.<table>": "column"}`, one name | `{"ds.qt_orders": "created_at"}` |
| `period` | `{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}`, inclusive | |
| `reference_date` | `"YYYY-MM-DD"` (default: period end) | |
| `control_table` | `{ref, table_column, watermark_column[, status_column, success_value]}` | |
| `top_k`, `stale_days` | whole numbers (defaults 5 and 30) | |

Tables Max names in `00_question.txt` get a score bonus. Then:

1. `"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/discover.py" --run-dir "$RUN" plan`
2. `"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/warehouse_jobs.py" --run-dir "$RUN" discovery --env-file "$ENV_FILE"` (runs only pending queries; dry run, cap, labels, the run's budget ledger; exit 2 = a query failed the allow-list; exit 3 = G15, the per-run budget would be passed). A table with an expiration date is penalised and flagged in `07_candidates.csv`, not excluded.
3. `"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/discover.py" --run-dir "$RUN" build`

| build exit | Meaning | Next |
|---|---|---|
| 0 | `05` to `08` and `discover_status.json` written; read Max its `g2_sentence` (shortlist, "sources refused" for listing or profiling queries the warehouse refused, dropped tables) | G2 |
| 3 | rung 0, absence not established, 0 shortlisted, a profile over the cap or the run budget, or (after the lock) a freshness disagreement (`gate_request.json`) | G2, G15 or G13 through the orchestrator |
| 2 | terms (wrong shape: stderr names the field) or results missing | fix the input, rerun |

## 3. Shortlist, profiling, traps

After Max confirms tables at G2: record his answer with `decide.py --gate G2 --outcome approve --tables ds.t1,ds.t2 --answer "<his words>"` (fix r4: the G2 row names the table set, and the lock is bound to it), then `discover.py --run-dir "$RUN" lock --tables ds.t1,ds.t2` (the same tables; another set is refused: "table set differs from the G2 decision: ask G2 again with --supersede"; the tables he keeps; the shortlisted ones he drops are recorded as "dropped at G2"), then `plan`, `warehouse_jobs.py ... discovery`, `build` again (nothing is profiled before the lock; only the locked tables are profiled: grain test over the period rows, NULL keys, freshness). From the lock on, `plan`, `build`, `warehouse_jobs.py`, `trap_scan.py`, `value_map.py` and `pin_plan.py` refuse (exit 2) a table outside the lock; the shortlist shows each dropped table on one "dropped at G2" line and nothing else. A freshness disagreement on a locked table (data ending before the period end, missing days, rows after the period, future-dated rows) exits 3 with G13; the request lists one check id per table, `freshness:<table>`. Max answers as data owner (`decide.py --gate G13 --outcome answered --check-id freshness:<table>`), a `period` value-map row for that table and date column settles it, or, when the query does not use that date column, a `not_used` row (`value_map.py add --kind not_used --table T --column C --reason R`) settles it; the shortlist's `freshness_agree` and 13_table_decision.md say which ("settled by data-owner answer: <reason>"). A rerun of `build` that exits 0 archives its own open request. Write the bindings draft into the metric card before `trap_scan.py`: with bindings, timezone and semi-additive traps block only on columns the bindings or the value map use (others are listed `not_bound` and need no decision). Then `"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/trap_scan.py" --run-dir "$RUN"`: exit 0 none blocking; exit 1 a blocking trap (`09_traps.csv`): Max binds a filter or records `not_applicable` with a reason: `decide.py --gate G3 --outcome not_applicable --check-id <trap_id> --reason "<why>" --answer "<Max's words, verbatim>"` is enough (fix r4: the G3 row resolves the trap; a `trap_resolutions` block in the metric card, {<trap_id>: {action: filter, filter: ...} or {action: not_applicable, reason: ...}}, still works), then rerun. A run whose P2 predates a plugin update is a new run: every script refuses a run started under another plugin version (`plugin_version` in run_config.json).

## 4. Value map and bindings

Write each proposed value-map row with `value_map.py` (the only writer of `11_value_map.csv`; never by hand, never by a library call), then G3 reads the file:

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/value_map.py" --run-dir "$RUN" add --kind <kind> --table <alias>.<table> --column <column> --value "<value>" --reason "<why>"` (`--replace` changes a row; `list` shows them; `validate` exits 1 listing problems).

| Kind | Value | Clears |
|---|---|---|
| `timezone` | IANA name or UTC, e.g. `Asia/Seoul` | the timezone trap of that table and column only |
| `period` | `YYYY-MM-DD..YYYY-MM-DD[; rule]` (whole days, start and end both included) | a freshness disagreement (G13) on that table and date column; a filter only on the bindings' date column (otherwise listed under Freshness, "settled; not a filter") |
| `not_used` | none needed ("not used") | the query does not use that column: settles freshness on it and clears its traps; never a filter |
| `filter` | the exact condition or value list, e.g. `status IN ('Complete')` | nothing by itself; the author writes it into B |
| `derive` | the expression | nothing by itself |
| `map` | `raw=label` | nothing by itself |

Columns of `11_value_map.csv`: `row_id` (`vm:<kind>:<table or run>:<column or *>[:<hash>]`), `kind`, `scope` (`column`, or `run` with `--scope run` and no table or column), `table_ref`, `column_name`, `value`, `reason`, `added_utc`. A run-wide row clears nothing until Max approves it at G3 with `--check-id <its row_id>`. A table must be in the lock and a column in `05_catalog.csv`.

Then the metric card `bindings` and `03_output_contract.json`, which `card_freeze.py --gate G1b` checks:

| Bindings key | Shape |
|---|---|
| `tables` | list of tables in placeholder form, `{{ds}}.qt_orders` (all in the lock) |
| `joins` | list of `{left, right, on_keys: [[left column, right column], ...], type: inner or left}` (every pair is applied; `on`, quoted, still read in older cards); `[]` for one table |
| `measures` | `{name: {expr, table[, filter]}}`, e.g. `revenue: {expr: SUM(sale_price), table: "{{ds}}.qt_order_items"}` |
| `derived` | `{name: expression over measure names}`; `{}` when none |
| `date` | `{table, column, truncation, timezone}`; truncation one of HOUR, DAY, WEEK, ISOWEEK, MONTH, QUARTER, YEAR, NONE |
| `scd2_flags` | list (`[]` when none) of `{table, as_of_rule}` |
| `semi_additive_columns` | list (`[]` when none) of `{table, column, rollup}` |
| `reference_extract` | text, when a reference is declared |

`03_output_contract.json`: `grain` (list of column names), `columns` (ordered list of `{name, type, nullable}`; type one of DATE, TIMESTAMP, STRING, INT64, FLOAT64, NUMERIC, BIGNUMERIC, BOOL), `key` (list of output columns, `[]` when none), `headline` (one output column). Every contract column is a measure, a derived name or a grain column.

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/card_freeze.py" --run-dir "$RUN" --gate G1b`: exit 0 hashed; 3 incomplete; 2 a blocking trap is still open; 1 the G1a intent changed (new run).

## 5. Table decision (last step of P2)

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/discover.py" --run-dir "$RUN" decide` writes `13_table_decision.md` from the lock, the profiles, the value map, the bindings and the pin plan when known: fixed headings `# Table decision`, `## Tables` (placeholder form `{{project}}.{{<alias>}}.<table>`, key, grain test, date column), `## Joins`, `## Filters and values` (exact values), `## Dates and period`, `## Timezone`, `## Columns named by the bindings` (with types), `## Freshness`, `## Dropped at G2`, `## Pin route`, `## Sources`. It is a packet input: the blind author sees only the card, the contract, this file and the value map. Exit 2 names what is missing (no lock: run `lock` first). Run it again after any value-map or bindings change; `packet_freeze.py --freeze packet` refuses a decision file that differs from what `decide` writes now, and `decide` may be rerun at P3 or P4 until the packet is frozen. At P3 `pin_plan.py` refreshes the file itself with the pin route it writes, so after an unchanged P3 no rerun is needed.

Discovery-lock drift on a later run: `discover.py ... drift` exits 1, G8.

## Never

Report a table or column absent from one narrow search: absence needs every listing rung run and returned.

## Artifacts left

`discovery_terms.json`, `discover_plan.json`, `discovery_inputs/`, `05_catalog.csv` to `09_traps.csv`, `discover_status.json`, `11_value_map.csv`, `13_table_decision.md`, `14_discovery_lock.json`, `03_output_contract.json`, `card_hashes.json` (G1b), decisions rows G2, G3, G1b (G13 when freshness disagreed).

## Hands to

G2, G3, G1b (G13 on a freshness disagreement, G8 on drift), `13_table_decision.md` written, then P3.

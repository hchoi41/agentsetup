---
name: author
description: "Internal phase of query-trail. Do NOT use for general SQL, 'reconcile' or 'how many' questions. Phase P4 (Author): freezes the packet, writes whole query B blind from the packet folder only, optionally a second author's A through spawn_scrubbed.py; called only by the query-trail run orchestrator."
user-invocable: false
---

# author (P4)

Purpose: write the whole query B (one SELECT with named CTEs) from the frozen packet only, so the author sees the card, contract, table decision and value map and nothing else. In second-author mode a blind whole A is written and frozen first.

Commands use `QT_PY`, `PLUGIN`, `RUN`, `ENV_FILE` as the run skill defines them.

## 1. State check (first action)

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/lib/state.py" --state "$RUN/run_state.json" check --phase P4` — exit 2: do nothing and return.

## 2. Freeze the packet

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/packet_freeze.py" --run-dir "$RUN" --freeze packet` — exit 0: `packet/` and `packet.sha256` written; exit 2: an input is missing.

## 3. Second author (only when author_mode is second:codex or second:max)

- Codex: `"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/spawn_scrubbed.py" --run-dir "$RUN" --label codex_a -- codex exec "<prompt: write A.sql from the files in this folder only>"`. The child runs with cwd `packet/` and an allow-listed environment (session tokens removed); `spawn/codex_a.env_names.json` lists kept and removed names. Exit = the child's exit; 2 = packet not frozen. Copy its A.sql to `RUN/A.sql` unchanged.
- Max: he writes `A.sql` himself.

Then `packet_freeze.py --run-dir "$RUN" --freeze A`. No A in second-author mode: continue as IND-0 only with a G8 row.

## 4. Write B blind

Start a fresh subagent whose only input is the `packet/` folder (T09 folder rule): it reads `packet/02_metric_card.md`, `packet/03_output_contract.json`, `packet/13_table_decision.md`, `packet/11_value_map.csv` and writes `RUN/B.sql`: one statement, `SELECT` or `WITH ... SELECT`, tables as `{{project}}.{{ds}}.table`, each would-be temporary view as one named CTE, output names and order exactly as the contract. It never opens A.sql, earlier runs, discovery files or results.

Pre-freeze loop, at most 3 rounds, messages about B only:

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/contract_check.py" --run-dir "$RUN" --env-file "$ENV_FILE"` — exit 0 conforms; exit 1 a mismatch (fix B and rerun); exit 2 the dry run is impossible or a forbidden statement or function (UDF, remote function, EXTERNAL_QUERY): rewrite B. `lint.py --run-dir "$RUN" --targets B` gives lint messages about B (exit 1 = an unwaived ERROR).

Still failing after 3 rounds: exit 1 to the orchestrator, G8 (Max decides whether the card or the contract is wrong).

## 5. Freeze B

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/packet_freeze.py" --run-dir "$RUN" --freeze B` — exit 2 in second-author mode when A is not frozen or changed.

## Never

Read outside the packet folder while writing B. Never show B's author anything of A.

## Artifacts left

`packet/`, `packet.sha256`, `packet_freeze.json`, `B.sql` (and `A.sql`, `spawn/`), `contract_check_B.csv`, the manifest skeleton with the independence level.

## Hands to

G8 when the loop does not converge; otherwise P5.

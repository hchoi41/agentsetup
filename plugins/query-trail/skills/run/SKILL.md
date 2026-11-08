---
name: run
description: "Orchestrator of query-trail, invoked by Max only as /query-trail:run [level=60..100 or quick..max] [run=<run id or folder>]. Walks P0 to P11 through the phase skills, stops at every gate to ask Max, resumes from run_state.json."
disable-model-invocation: true
---

# run: the orchestrator (P0 to P11)

Scripts decide; Max decides at gates. Never run SQL, edit an artifact, skip a gate, or answer one for Max.

**Names.** `PLUGIN`: two folders above this skill's base directory. `QT_PY`: the pinned interpreter (the environment variable `QT_PY`; `QC_PY`, the name in the research `bq_env.sh`, is accepted as an alias). `QT_HOME`: the environment variable, else `%USERPROFILE%\.query-trail` (holds `env/`, `runs/`, `active_run.json`). Use QT_HOME with forward slashes under Git Bash (`C:/Users/<you>/.query-trail`, never a mix of `\` and `/`): `ls` and `cat` on a mixed path fail there, while the Python scripts read either form. `run_init.py` prints `RUN=` and `QT_HOME=` in that form; reuse those lines. `RUN`: from `run=`: a value that names an existing folder, or contains `/`, `\` or a drive letter, is that folder; any other value is a run id and `RUN` is `<QT_HOME>/runs/<id>/` (whether or not it exists yet). Without `run=`, ask Max for a run id (letters, digits, `_`, `-`). `ENV_FILE`: the env file Max names, else `<QT_HOME>/env/<env>.json`. A script call is `"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/<script>.py" --run-dir "$RUN" ...`, with `--env-file "$ENV_FILE"` when it touches BigQuery. `state.py` means `"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/lib/state.py"`.

## Start or resume

1. No `RUN/run_state.json`: `"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/run_init.py" --run-id <id> --level <level> --env-file "$ENV_FILE"` (add `--home "$QT_HOME"` when QT_HOME is set; `<id>` is the run id from `run=`, `<level>` from `level=`, default 60). It writes `run_state.json` (P0, pending) and `run_config.json` from the schema defaults (50 GiB per run, 5 GiB per job) and activates the run. The gate hook lets this command through without `--run-dir`. Exit 2: read the reason (bad id or level, env file missing, run exists), fix it, ask again. Exit 0 prints `RUN=<folder>` and `QT_HOME=<folder>` with forward slashes: use those values for `RUN` and `QT_HOME` from here on.
2. Resuming an existing run: `state.py activate --run-dir "$RUN"` (the hooks confine writes to `RUN`).
3. Resume at the state's phase; an open `gate_request.json` first. Before leaving P2: `discover.py decide` has written `13_table_decision.md` (the packet's table decision; P4 refuses without it). At P3 `pin_plan.py` refreshes it with the pin route it has just written, so P4 needs no second `decide`. If `run_state.json` carries `blocked_reason`, tell Max that reason first and ask whether the cause is fixed before rerunning.

## The question (before P1)

Before the first P1 step, unless `RUN/00_question.txt` exists: ask Max for his question in his own words with one AskUserQuestion, worded exactly like this:

- Header: "Your question".
- Text: "P0 is done: G0a and G0b recorded. P1 needs one input: your question. P1 of 11 (P0 to P11). In one plain paragraph, what do you want to find out from the data? It is saved unchanged to 00_question.txt; anything missing is asked later, one field at a time."
- Options: "Reuse the previous run's text (shown above)", only when a previous run's `00_question.txt` exists under `QT_HOME/runs` (quote that text, with its run id, in the message just before the question); and "Pause here". His own paragraph is the free-text answer.

Only when he asks what to include, answer in a second paragraph, then ask the same question again: the question in one sentence; the metric and how it is computed; numerator and denominator and who is counted in each; the ratio type (cohort, flow, share, per_entity, none); the chain of ratios that multiply back to the metric, if there is one; pooled or within-entity weighting; the reference to compare with and its tolerance; the headline claim; anything that must not be counted. None of these is needed now.

Save his answer unchanged to `RUN/00_question.txt` (UTF-8, LF; never shortened, translated or completed by you; with "Reuse", the previous file's text byte for byte) and hand that file to the `card` skill, which drafts both cards from it and quotes it. Fields he left out stay blank: the card skill asks for exactly those when `card_freeze.py` lists them. "Pause here": `state.py --state "$RUN/run_state.json" set --blocked "waiting for Max's question"` (the Stop hook then lets the turn end) and stop. `00_question.txt` is frozen at G1a with the cards.

## Walk

For each phase up to P11, one phase at a time: `state.py --state "$RUN/run_state.json" set --phase Pn --status in_progress`, call the phase skill (`query-trail:preflight` P0, `card` P1, `discover` P2, `pin` P3, `author` P4, `decompose` P5, `run-steps` P6, `reconcile` P7, `sample` P8, `logic` P9, Max's `/query-trail:rename` or a skip at P10, `report` P11), then `set --status done`. A re-work route (for example back to P5) is `set --phase P5`; the state becomes pending.

**Editing `run_config.json`.** Between G0a and G1a Max may change `run_config.json` (level, author mode, `sample_n`, caps, reference and its provenance, `job_labels`, `pin`): he edits it, or you edit it on his explicit words in this session and say what changed. G1a hashes it (`card_freeze.py`, with the cards); after G1a the edit guard blocks any change, and a changed configuration is a new run. A caps change that answers a preflight refusal is followed by a preflight rerun, which records the new caps in its own attempt file.

## Gates

A script that exits 3 writes `gate_request.json`; the per-phase gates are asked anyway. For each gate: `set --status waiting_gate`; ask Max with AskUserQuestion in this main session, one sentence plus the file to open; record his words with `decide.py --run-dir "$RUN" --gate Gn --outcome <outcome> --reason "<his words>" --by Max --answer "<his answer, verbatim>" [--check-id ID] [--input-file F] [--supersede]` (exit 2: read the reason on stderr, fix that, ask again); `set --status in_progress`; rerun the script that asked. `--answer` is AskUserQuestion's answer unchanged; `--reason` is the part of his words that gives the reason (the whole answer when he gave no separate reason).

What code enforces (and nothing more):
- `decide.py` exits 2 and records nothing when the gate does not belong to the state's phase (spec v3 2.1 table, plus G8 at P0 and P4, G15 at P2, P7, P8 and P9, G4 at P9); when another gate's `gate_request.json` is open; when the gate is one a script opens at this phase (G6 at P1 and P11, G10 at P5, G13 at P2 and P3, G15 at P2, P6, P7, P8, P9) and no request is open; when a per-run gate (G0a, G0b, G1a, G2, G3, G1b, G11, G12, G5) already has a row this attempt for the same `--check-id` and `--supersede` is not given (the new row then records `supersedes=<row n>`); when `--answer` is empty; when an outcome word is not one of `decide.py` OUTCOMES; when a G0a flag value is outside its set.
- `state.py set --phase` exits 2 on a forward move of more than one phase, and on leaving P0, P1, P2, P5, P8 or P10 until each of its per-run gates (P0 G0a G0b; P1 G1a; P2 G2 G3 G1b; P5 G11; P8 G12; P10 G5) has a closing `decisions.csv` row this attempt: no `--check-id`, outcome G0a approve, G0b accept, G1a/G2/G3/G1b approve, G11 approve, accept or signed, G12 signed, approve or accept, G5 approve or skip, and no later row superseding it (fix r14). A G1b `confirmed` row, a G3 trap resolution or a reject does not count, so ask the gate itself after them. Conditional gates are not required to move on.

What still depends on this skill: code cannot verify a person. `--by` is free text, and `answer_text` is only Max's answer if you pass AskUserQuestion's answer to `--answer` unchanged. Never compose, shorten or translate it, and never call `decide.py` without asking Max first.

| Gate | Say | Open | Outcome words |
|---|---|---|---|
| G0a | Approve use: plan route, ZDR, employer approval, retention, default level, author mode (the questions and value sets are in the preflight skill); first the sentence "Budget caps for this run: <per job> per job, <per run> per run (run_config.json caps)" from `gate_request.json` `details.caps.sentence`. | `run_config.json` | `approve`, `reject`, `stop` (flags: `--plan-route`, `--zdr`, `--employer-approval`, `--retention-months`, `--level`, `--author-mode`) |
| G0b | The technical preflight passed; accept it? | `00_preflight.json` | `accept`, `reject`, `stop` (with `--input-file 00_preflight.json`) |
| (card) | `card_freeze.py` exit 3: complete these card fields (not a gate; nothing is recorded; the card skill asks). | `card_completeness.csv` | none: Max's words go into the card, then card_freeze reruns |
| G1a | Is this the question, metric, chain, aggregation weight (`aggregation_weight`: pooled, equal_weighted or not_applicable, with the value on the card and whether it was prefilled from his words), reference, tolerance? | the cards, `00_question.txt` | `approve`, `reject` (he says what to change; the card is redrafted and frozen again: until the G1a approve row the hash is provisional and the edit guard lets the cards and `run_config.json` change), `stop` |
| G2 | Read him `g2_sentence` from `discover_status.json` (shortlist, sources refused, dropped tables): are these the tables and join keys? Record it with `--tables <the ones he keeps>` (the lock is bound to that set), then `discover.py lock --tables <the same>`. | `08_shortlist.csv`, `discover_status.json` | `approve` (with `--tables`), `reject`, `stop`; a changed set later: `approve --supersede --tables ...`, then `lock` again |
| G3 | Are these the filter values, timezone and dates? (`value_map.py list`) | `11_value_map.csv` (written by `value_map.py`), `09_traps.csv` | `approve`, `reject`, `stop`; a trap resolution with `--check-id <trap id>`: `approve` or `not_applicable` with his reason; a run-wide value-map row with `--check-id <its row_id>`: `approve` |
| G1b | Are the bindings and output contract right? | `03_output_contract.json` | `approve`, `reject`, `stop`; `confirmed` only with `--check-id aggregation_weight:not_applicable` (fix r14: when `card_freeze.py --gate G1b` exits 3 with `02_metric_card.md:aggregation_weight invalid (not_applicable ... grouped by <cols> ...)`, do not edit the card; ask Max "The output is grouped by <cols> and the card says the final number is not averaged across them. Is that what you want? confirm / new run"; on confirm run `decide.py --run-dir "$RUN" --gate G1b --outcome confirmed --check-id aggregation_weight:not_applicable --reason "<his words>" --by Max --answer "<his answer, verbatim>"` and rerun `card_freeze.py --gate G1b`; on new run, stop: a different weight is a new run) |
| G6 | Add this entry to the registry? | `gate_request.json` | `approve`, `reject` (then `registry.py approve --by Max` on approve) |
| G8 | A check failed: reason, change course, or stop? | `gate_request.json` | `explained` with `--check-id` (his reason explains that one check, this attempt only), `rewrite`, `re_pin`, `csv_only`, `stop` |
| G9 | Waive this lint error with a reason, or rewrite B? | `lint_report.csv` | `waive` (with his reason), `rewrite` |
| G10 | Refused construct: hand split or rewrite? | `gate_request.json` | `hand_split`, `rewrite`, `stop` |
| G11 | Can you say each step in one sentence? | `plan.json` | `approve` (or `accept`, `signed`), `split_finer` |
| G13 | Answer a data-owner question (P2: freshness disagrees on a locked table; P3: a late-arrival band). | `gate_request.json`, `08_shortlist.csv` (P2) or `15_pin_plan.json` (P3) | `answered` (his answer in `--answer`; at P2 with `--check-id freshness:<table>`, one table per row), `stop`; at P2 a `value_map.py add --kind period` row naming that table and date column, or a `not_used` row when the query does not use that column, settles it instead |
| G15 | Over the per-job cap, the per-run budget (spent + held + reserved, `manifest.json` budget) or the stream cap: filter, cut to DuckDB, raise the cap, or stop? | `gate_request.json` | `filter`, `cut_local`, `raise_cap`, `accept_level_90`, `stop` |
| G4 | A reference difference (P7 or the P9 tie-out): accept with a category and the comparison's check id, reject, or stop? Never for a B vs chain(B) or A vs chain(B) FAIL: that routes to P5 (a new attempt) or stop with a G8 reason; an INCONCLUSIVE comparison routes to G8 (G15 when the cause is the stream cap). | `checks/mismatch_rows.csv` | `accept` (with `--check-id`, `--category` timing, rounding, scope, known_source_defect or other_with_reason, and `--input-file` the reference comparison CSV), `reject`, `stop` |
| G12 | Read and sign the trace report. | `trace_report.md` | `signed` (or `approve`, `accept`), `reject` |
| G5 | Rename columns now or skip? | deliverable CSV | `approve`, `skip` |
| G16 | Acknowledge the close limits? | `report.md` | `acknowledge`, `decline` |

`decide.py` accepts any word of its OUTCOMES list at any gate; the column above lists the words each documented flow expects. Use only those.

## Failure routing

**Any non-zero exit (1, 2 or 4) that stops the phase:** leave the phase `in_progress` (if it is `waiting_gate`, because the failure came while a gate was being answered, first set it back with `state.py --state "$RUN/run_state.json" set --status in_progress`: `--blocked` is refused in any other status), record it with `state.py --state "$RUN/run_state.json" set --blocked "<script> exit <n>: <the stderr reason>"` (the Stop hook then lets the turn end), and tell Max the reason (never edit a script-owned file). After the fix, rerun the script (any `state.py set` clears the blocked reason).

Exit 1, route per phase:

| Phase | Exit 1 means | Route |
|---|---|---|
| P0 | preflight self-test surprise | G8 |
| P1 | `card_freeze.py`: the approved intent changed | a new run (or, before the G1a approve row, redraft and freeze again) |
| P2 | `trap_scan.py`: a blocking trap; `discover.py drift`: the lock drifted; `value_map.py validate`: problems listed | G3 (bind or `not_applicable`); G8; fix with `value_map.py add --replace` |
| P5 | `lint.py`: an unwaived ERROR | G9; `rewrite` routes back to P4 (below) |
| P6 | `run_steps.py`: a step check failed | G8 with `--check-id` |
| P7 | `reconcile.py`: a comparison failed | `route: P5` in `checks/reconcile_status.json` or G4 for reference differences only |
| P8 | `trace.py` / `sample.py`: a trace or sample check failed | G8 |
| P9 | `logic_tests.py`: a logic test failed | G8 (G4 for a tie-out difference) |
| P11 | `report.py` / `secret_scan.py`: a check failed | G8 |

Exit 2: the script refused. Stderr says whether a warehouse call was made ("refused: no warehouse call was made" or the jobs on the budget ledger during the call) and lists the local effects (files written, changed or removed, or "none"); tell Max both and the fix the message names. Exit 4: tell Max; after his fix, rerun once unchanged. `route: P5` in `checks/reconcile_status.json`: back to P5 with a new attempt, at most 2 rounds, then stop with a G8 reason (the verdict stays "not reconciled: decomposition differs"). A G8 reason applies to the attempt it was given in: after a re-work round, a failing check is asked again. Level 60 cannot localise a mismatch: offer an escalation re-run. Sampling that did not recompute every measure from source is a qualifier on a "consistent" verdict ("sampling incomplete: <measures> not recomputed from source"), not a "not reconciled".

**Approve with a change.** When Max approves a gate artifact with a change (a value-map row, a binding), make the change first through the script that owns it (`value_map.py add --replace`, the card, `discover.py decide`), then record `approve` with `--input-file` naming the changed artifact, so the row's hash covers what he approved. Do not record approve before the change.

**Rework of B inside the run (fix r4).** A decision that routes back to P4 (G8 `rewrite` or `change_course`, G9 `rewrite`, G10 `rewrite`, G11 `split_finer` or `reject`) lets `state.py set --phase P4` move back from P5 or later; the author corrects `B.sql` (the edit guard opens it at P4 after that decision), then `packet_freeze.py --freeze B --new-attempt` re-freezes it in a new attempt (old hash under `B.superseded[]`, old steps, plan, executed SQL and checks archived under `attempts/<n>/`), and P5 runs split, lint and compile again. The packet stays frozen: a packet or card change is a new run. G1a is never asked again in a later attempt: its approval holds for the whole run.

## End

After P11: `set --phase P11 --status done`, then the report skill's final pair (`secret_scan.py`, then `view.py`: the G16 row came after the earlier scans), `state.py deactivate`. Quote the verdict line of `report.md` verbatim; `close.py --apply` is Max's to run. P11 ends with `view.py` (the report skill): give Max the `view.html` path to open in his browser; `/query-trail:view` renders it again for any run.

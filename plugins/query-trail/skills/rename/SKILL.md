---
name: rename
description: "P10 column rename of query-trail, invoked by Max only as /query-trail:rename. One output column at a time, Accept, Keep or Skip-all with AskUserQuestion; writes renames.csv and runs rename_apply.py on copies with the lossless check."
disable-model-invocation: true
---

# rename (P10, Max only)

Purpose: give the deliverable and step outputs business names, one column at a time, on copies, with a check that the rename lost nothing. Runs in the main session because AskUserQuestion is unavailable in subagents.

Commands use `QT_PY`, `PLUGIN`, `RUN` as the run skill defines them.

## 1. State check (first action)

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/lib/state.py" --state "$RUN/run_state.json" check --phase P10` — exit 2: tell Max the run is not at P10 and stop.

## 2. Proposals

Read the deliverable columns from `manifest.json` (deliverable B) and, at levels 90 and 100, each retained step's columns. For each column propose one name: short, business words, no reserved prefix (`_TABLE_`, `_PARTITION`, `_FILE_`, `_ROW_TIMESTAMP`), no backtick, no edge space, at most 300 characters, unique under case folding.

## 3. Ask, one column at a time

For each column, AskUserQuestion with three options:

- **Accept** `<original>` → `<proposed>`
- **Keep** `<original>`
- **Skip all** (stop asking; every remaining column keeps its name)

Max may type his own name instead of the proposal. Never ask about two columns in one question.

## 4. renames.csv

Write `renames.csv` through the lib writer (never by hand-editing CSV text): columns `step` (manifest step name, or `B` / `A` for a whole query's deliverable), `original`, `proposed`, `decision` (`rename` or `keep`), `accepted_by` (Max), `accepted_at` (UTC). Only rows with `decision = rename`, a different name and both stamps count.

Record the gate: `decide.py --run-dir "$RUN" --gate G5 --outcome approve --reason "<n> renames, <m> kept" --by Max --answer "<Max's words, verbatim>"` (or `--outcome skip` for Skip all before any rename).

## 5. Apply

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/rename_apply.py" --run-dir "$RUN"`

| Exit | Meaning | Next |
|---|---|---|
| 0 | `<stem>_renamed.csv` and `<stem>_renamed.sql` copies written; lossless (same value hash, rows, types; inverse map restores the names) | tell Max the files; back to the orchestrator (P11) |
| 1 | a lossy map: every renamed file this run wrote is removed; `rename_report.json` says why | show Max the reason; ask again for the colliding column |
| 2 | a collision or reserved name in a proposal, or state | fix that proposal with Max; rerun |

## Never

Rename an original file, rename without Max's answer for that column, or batch several columns into one question.

## Artifacts left

`renames.csv`, `*_renamed.csv`, `*_renamed.sql`, `rename_report.json`, decisions row G5.

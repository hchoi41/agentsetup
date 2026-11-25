---
name: trace
description: "Invoked by the user only as /rule-of-thumb-model:trace <workbook> <Sheet!Cell>. Rule-of-thumb-model command: for one output cell, list in words the chain of helper cells back to the raw rows."
disable-model-invocation: true
argument-hint: "<workbook> <Sheet!Cell>"
---

# trace: one cell back to the raw rows, in words

## Names

- `SKILL_DIR`: two folders above this skill's base directory, then `skills/rule-of-thumb-model` (Claude Code prints
  "Base directory for this skill: ..." when it loads a skill; write the path with forward slashes under Git Bash).
- `ROTM`: `python -P -X utf8 -B "<SKILL_DIR>/scripts/rotm.py"` (never `-I`: openpyxl may live in the user
  site-packages, which `-I` hides).
- `MODEL`: the model folder (it holds `rotm_model.json`).

## Read first

The main skill `<SKILL_DIR>/SKILL.md`: the procedure P1 to P7, the hooks and the common mistakes apply to this
command too.

## Steps

1. `ROTM trace <workbook> <sheet>!<cell>`, for example `ROTM trace MODEL/model.xlsx summary!F11`.
   - `--depth N` limits the levels; `--json FILE` gives the graph; `--raw-sheets S,...` names raw sheets of a
     workbook without a manifest.
2. The output is indented by level: each cell with its formula, helper columns shown once as a column ("h_sales
   column G 'amount_k' rows 2 to 601: =E2/inputs!$D$7"), ending at raw columns (sheet, column, header, row range)
   and inputs (label, value, unit).
   - One cell of a helper sheet that fetches one raw row by position (`=INDEX(raw_sales!$F:$F,A22)`) ends at that
     raw cell with its value (`raw_sales!F22 'amount' ... = 540,700`); a raw range read through SUMIFS, COUNTIFS or
     another conditional sum or count stays whole and is labelled "the rows that can contribute", with the criteria
     and their values (the matching rows are not named).

## Exit codes

| Code | Meaning | What to do |
| --- | --- | --- |
| 0 | done; with `--strict`, no warning either | continue |
| 1 | findings: an error, or with `--strict` any warning | fix each finding: every line names the sheet, the cell and the fix |
| 2 | refused: bad input, nothing written | change what the message names |
| 3 | not available or not verified (for example Excel is missing) | say "not recalculated" (or "not rendered") in the report |
| 4 | internal error: one line; the traceback is in the state folder's `last_error.txt` | report it to the user; do not work around it by editing files |
| 5 | Excel busy: another job holds the Excel lock | wait and run again |

## What to tell the user

- The chain in words, from the cell to the raw columns and inputs it rests on.
- Cells whose values were not recalculated are shown without values.

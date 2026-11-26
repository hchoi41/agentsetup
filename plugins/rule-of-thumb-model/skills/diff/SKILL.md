---
name: diff
description: "Invoked by the user only as /rule-of-thumb-model:diff <old workbook> <new workbook>. Rule-of-thumb-model command: compare two versions of a workbook: formulas, inputs and outputs that changed."
disable-model-invocation: true
argument-hint: "<old workbook> <new workbook>"
---

# diff: what changed between two versions of a workbook

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

1. `ROTM diff <old> <new>` (each may be a workbook or a model folder). `--md FILE` writes a Markdown report;
   `--json FILE` the report; `--fail-on-change` exits 1 when anything differs.
2. It lists: sheets added or removed (`D-SHEET`); formula cells whose formula changed (`D-FORMULA`, compared without
   the file prefixes `_xlfn.`, `_xlws.` and `_xlpm.` outside text literals; a text in quotes is compared as it is, so
   the criterion `"_xlfn.Fee"` changed to `"Fee"` is a changed formula); input values changed (`D-INPUT`); cached output values changed (`D-OUTPUT`); raw sheets as row
   count and digest (`D-RAW`); structure (`D-STRUCTURE`).
   - A formula replaced by a typed value, or the reverse: the `D-FORMULA` line shows both sides, the formula with
     the value Excel saved for it and the typed value (`B1: formula removed: =A1*0 (saved value 0) -> typed value
     99`); when the two values differ, the cell is also a `D-OUTPUT`.
   - A cell whose stored type changed (number, text, logical value) while its text reads the same is listed as
     changed, with both types. This holds on raw sheets and on every other sheet: the number 858800 and the text
     `858800` are a change, and so are the text `TRUE` and the logical value TRUE.
3. Values are compared only when both files hold values saved by Excel; run `ROTM recalc` on a file that has none.
   The report, the Markdown file and the JSON say what the comparison of outputs rests on:
   - `verified.saved_values_compared`: true when both files hold values saved by Excel.
   - `verified.recalculated`: true only when `reports/status.json` of each file's model folder records a passing
     check that recalculated that file's current version, or a passing recalc of it. diff itself does not
     recalculate. A file outside a model folder gives false. When it is false and values were compared, a note
     says that the outputs are the values saved in the files and names each file without such a record.
   - A workbook set to manual calculation gets a note that names it. While its version has no such record, the
     note says that its saved values may be out of date and gives `rotm.py recalc` for both files: run
     `ROTM recalc "<old>"` and `ROTM recalc "<new>"`, then run the diff again.
   - The JSON's `data.calc_mode` gives each file's calculation mode (`auto`, `manual` or `autoNoTable`) and
     `data.recalculated_on_record` whether each file has such a record.
4. Formulas and typed values (inputs, constants) are compared exactly: 1,000,000,000,000 and 999,999,999,500 are a
   change. Calculated values (outputs) are compared within a relative tolerance, `--calc-tolerance` (default 1e-9;
   `--calc-tolerance 0` compares them exactly); the report prints the tolerance it used.

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

- The changes grouped as formulas, inputs, outputs and raw data, with sheet and cell.
- When one file held no recalculated values: outputs were not compared.
- Whether the outputs were recalculated (`verified.recalculated` true) or are the values saved in the files. For a
  workbook set to manual calculation without a recalculation on record: its saved values may be out of date until
  both files are recalculated with `ROTM recalc`.
- The tolerance used for outputs (the report's note `compared: ...`).

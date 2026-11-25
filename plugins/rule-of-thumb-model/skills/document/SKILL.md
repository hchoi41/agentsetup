---
name: document
description: "Invoked by the user only as /rule-of-thumb-model:document <model folder or workbook>. Rule-of-thumb-model command: write the model's map: sheets, inputs, helper columns, checks, column contracts."
disable-model-invocation: true
argument-hint: "<model folder or workbook>"
---

# document: write the model's map

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

1. `ROTM document MODEL` writes `MODEL/MODEL_MAP.md` from the manifest, the specification and the contracts:
   sheets (name, type, font size, purpose), inputs (id, label, value, unit, cell, source), raw tables (contract
   summary and agreement record), helper columns (letter, header, formula), blocks (range, shape, formula), checks
   (id, kind, label, cell), key outputs, the master check, dictionary overrides and the build record.
2. A workbook outside a model folder: an inferred map, written as `<stem>_MODEL_MAP.md` next to it. `-o FILE` names
   the output.
3. Warnings `M-SPEC`, `M-MANIFEST`, `M-WORKBOOK`, `M-STALE`: a file is missing, or the workbook changed after the
   manifest was written (rebuild, then document again).

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

- The path of the map, and any warning about stale or missing files.

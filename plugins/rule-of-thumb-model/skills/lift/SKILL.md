---
name: lift
description: "Invoked by the user only as /rule-of-thumb-model:lift <spec or model folder>. Rule-of-thumb-model command: take a model beyond the spreadsheet: write a Python script that reproduces every helper column and ties out to the workbook."
disable-model-invocation: true
argument-hint: "<spec or model folder>"
---

# lift: the model as a Python script that ties out to the workbook

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

1. The model must be built by this plugin (lift reads the manifest and the specification).
2. `ROTM lift MODEL --run` writes `MODEL/lift/<name>_lift.py` and runs it: it reads `raw/*.csv` with the contract
   types, computes every helper column and block in the workbook's order, reads the workbook's recalculated values
   and prints a tie-out table (cell, workbook value, script value, difference).
   - The workbook needs values saved by Excel: without them `--run` recalculates a temporary copy in Excel; without
     Excel it exits 3 ("not verified"). The temporary folder of that copy (`rotm_lift_<random>` in the system's
     temporary folder) is removed with all its contents after the run, also when the run fails.
   - The tie-out rule: two numbers tie out when their difference is at most the model's check tolerance
     (`settings.check_tolerance`, default 0.001, absolute, in the unit of the cell) plus 1e-13 times the larger of
     the two values. The 1e-13 covers only the rounding of double-precision arithmetic; it allows 0.1 at
     1,000,000,000,000, so a difference of 500 at that size differs. The header line prints both numbers and the
     largest difference with its cell, for example `tie-out: 607 cells compared; tolerance 0.001 (absolute) plus
     1e-13 times the larger value (double-precision rounding); largest difference 3.63798e-12 at calc!F25`. Each
     step's row prints its largest difference over every compared cell, ties included (`-` only when the step
     compares no number).
   - `--lang r` exits 3: R output is not available in this version (no R was installed to test it).
   - The script computes the fixed helper columns as build writes them: `in_raw`, the fetches, the null flags
     `<column>_null` (of each number, integer, date or datetime column whose null rule is `allowed` or whose
     contract agrees `known_nulls` for it), and `dup` with the exact key rule (case-sensitive, no wildcard); and the
     generated checks, `<t>_beyond` included. A table with zero rows gives 0 in `<t>_rows`, `<t>_cover`,
     `<t>_beyond` and every total, and no helper cell is compared.
   - The control total of a raw table is the total accepted for the snapshot the script reads
     (`raw/<t>.controls.json`, written by `check --accept-refresh --control`), else the contract's total, as build
     writes it into the inputs sheet. After a later acceptance, run `lift` again: the script says so in a note when
     the manifest records another snapshot.
   - The script reproduces the financial functions PMT, PV, FV and NPV with Excel's arguments and rules (measured
     in Excel 16 on 2026-11-25):
     - arguments: `PMT(rate,nper,pv,fv,type)`, `PV(rate,nper,pmt,fv,type)`, `FV(rate,nper,pmt,pv,type)`,
       `NPV(rate,value1,...)`; fv, pv and type may be left out (0);
     - type 0 pays at the end of each period; any other number pays at the start;
     - at rate 0: PMT = -(pv + fv) / nper, PV = -(fv + pmt * nper), FV = -(pv + pmt * nper);
     - PMT gives #NUM! for nper 0 and for a rate of -1 or less;
     - NPV discounts the numbers of a range only: text, TRUE/FALSE and empty cells take no period;
     - compared with Excel for the rates 0, 0.05/12 and -0.01 and the types 0 and 1: relative difference at most
       1e-9 (test `test_v3_lift.py::test_financial_functions_equal_excel`).
   - Text in the aggregates and the criteria follows Excel (v5, measured in Excel 16 on 2026-11-25): SUM, AVERAGE,
     MIN, MAX, PRODUCT, COUNT and NPV skip text (also text that reads as a number, such as `001`) and TRUE/FALSE in a
     cell they reach through a placeholder, IF, CHOOSE, IFS, SWITCH, INDEX or XLOOKUP, and convert a value typed or
     computed in the formula; SUMIFS, COUNTIFS, AVERAGEIFS, MAXIFS, MINIFS, SUMIF, COUNTIF and AVERAGEIF match their
     criteria with the rules of `<SKILL_DIR>/references/helper-patterns.md` ("Labels that Excel's criteria matching
     merges"), so the criterion `=1` counts the text `001`. A form where lift cannot tell a reference from a value
     (for example XLOOKUP with a typed not-found text inside SUM) is listed as `L-NOT-REPRODUCED` before the tie-out.
   - A specification whose expressions read each other in a cycle is refused (exit 2) when it is loaded, with the
     JSON path of each expression in the cycle.
3. The script's own options: `--all` (prints every compared cell, one line each; without it only the cells that do
   not tie out, the first 40), `--json FILE` (one record per compared cell, as many as the tie-out counts), `--csv
   DIR` (each helper sheet and block as CSV), `--raw workbook` (read the raw sheets, for example after a Power
   Query refresh), `--inputs workbook`.
4. Before the script is written, `lift` names every function of the model that the script does not reproduce:
   `LIFT-UNSUPPORTED` (error, exit 1), one finding per function with the number of cells and the first cell, for
   example `the lift evaluator does not reproduce MEDIAN: 60 cell(s), first h_lines!H2`. The column Lift of
   `<SKILL_DIR>/references/functions.md` marks the functions the script reproduces. The script is still written;
   its cells for that function show "not reproduced". Fix: change the model, or reproduce those cells by hand after
   the lift. With `--run` and no Excel, the exit code stays 1.
5. Exit 1 with `L-DIFF` (a value differs) or `L-NOT-REPRODUCED` (a form the translator does not cover, such as a cell
   reference typed in the specification or `ROW(<ref>)`): the tie-out table names the cells.

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

- The script's path and the tie-out: how many cells compared, how many tie, the tolerance with the 1e-13 rounding
  allowance, and the largest difference with its cell.
- Each `LIFT-UNSUPPORTED` function with its cell count and first cell, and any step not reproduced, by name.

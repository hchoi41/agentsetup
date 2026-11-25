---
name: audit
description: "Invoked by the user only as /rule-of-thumb-model:audit <workbook> [--strict]. Rule-of-thumb-model command: audit any workbook against the rules: nesting depth, length, typed numbers, raw sheets, helper columns, checks, error values, styles, functions in both products."
disable-model-invocation: true
argument-hint: "<workbook> [--strict]"
---

# audit: audit any workbook against the rules (P7)

## Names

- `SKILL_DIR`: two folders above this skill's base directory, then `skills/rule-of-thumb-model` (Claude Code prints
  "Base directory for this skill: ..." when it loads a skill; write the path with forward slashes under Git Bash).
- `ROTM`: `python -P -X utf8 -B "<SKILL_DIR>/scripts/rotm.py"` (never `-I`: openpyxl may live in the user
  site-packages, which `-I` hides).
- `MODEL`: the model folder (it holds `rotm_model.json`).

## Read first

The main skill `<SKILL_DIR>/SKILL.md`: the procedure P1 to P7, the hooks and the common mistakes apply to this
command too. Then `<SKILL_DIR>/references/fast-standard-rules.md` for the published rules behind each audit rule.

## Steps

1. `ROTM audit <workbook or MODEL> --strict` (the same as `python -P -X utf8 -B "<SKILL_DIR>/scripts/formula_audit.py"
   <workbook> --strict`). A model folder audits the workbook its marker names.
   - `--json FILE` (or `-`) writes the report; `--rules ID,...` runs some rules only (not recorded);
     `--limit N`, `--max-length N`; `--raw-sheets S,...` names raw sheets of a workbook the plugin did not build;
     `--product excel|sheets|both`; `--no-record` writes no report into the model folder.
   - `--use NAME` applies a named look of the style dictionary (for example `input_blue_font`; may be repeated)
     as theme does, and combines with `--dictionary` as theme does (the named looks first, then the file). To
     audit a workbook that theme restyled, give the same `--use` and `--dictionary` as the theme run: theme
     prints that audit command as the last note of its report.
2. Read each finding: rule id, sheet and cell (or the first cells and a count), the message and the fix. For
   `F-DEPTH` the fix names the part to move into a helper (`references/helper-patterns.md`).
3. Rules: F- formulas (depth, length, typed numbers, nested IF, refused, flagged, unknown and volatile functions,
   own-sheet prefix, other workbooks, `#REF!`, arrays, names, `F-PARSE` a formula that is not complete (an
   error: a trailing or doubled operator, a missing operand, an unclosed bracket or quote; Excel does not open
   such a workbook) or formula text the tokenizer cannot read (a warning), `F-ARGS` a listed function with too
   few or too many arguments (an error; Excel does not open such a workbook; the 29 functions and their counts are
   in `<SKILL_DIR>/references/model-spec-format.md`, "Argument counts")), R- raw sheets (formulas, not alone,
   changed since landing, headers against the contract, text in a typed column, merged cells), CT- the
   contract version the workbook was built from, H- helper columns (inconsistent, gaps,
   coverage, `H-BEYOND` helper formulas below the last raw row), B- block rows, C- checks (missing, block without a check, wiring, a result its own operands do not
   give, failing, raised alerts), V- error values,
   formula cells with no value (`V-NO-VALUE`) and not recalculated, S- styles against the dictionary, W-
   calculation mode, iteration and a check compared with itself, X- one-product features. `A-UNREADABLE`: a file the audit cannot read
   (encrypted, `.xls`); `A-RULE-FAILED`: one rule family could not run.
   - `C-MISSING` (error) also names a check result cell of the manifest that holds no formula (for example a
     typed 0): such a cell shows a pass whatever the model holds. Fix: `rotm.py build --force`.
   - With a manifest, on the saved values: `C-RECOMPUTE` (error) when a check's saved result is not the one its
     own operand cells give, when an operand is not a number, when the master check differs from the check
     results, or when a Result or master formula differs from the formula build recorded in the manifest. Fix:
     `rotm.py build MODEL --force`, then `rotm.py check MODEL` (`<SKILL_DIR>/references/checks.md`, "C-RECOMPUTE").
   - `V-NO-VALUE` (error): in a workbook that Excel recalculated and saved, a formula cell that holds no value.
     The saved file does not say which cells are circular, so the fix says: `rotm.py check MODEL` recalculates
     the workbook in Excel and names a circular reference when there is one; otherwise look at the formula and
     its inputs with `rotm.py trace`. A formula whose result is empty text is not reported, also when it holds
     a volatile function or depends on one. A cell whose formulas' references show that it lies on a cycle of
     references (for example A1 `=B1+TODAY()*0` and B1 `=A1+1`) is still reported. A workbook that was not
     recalculated gets `V-NOT-RECALC` instead.
   - With a manifest, in a model folder: `CT-VERSION` (error) when the raw entry of the manifest records
     another contract hash than the agreed `contracts/<t>.contract.json`: the workbook was built from another
     version of the contract. Fix: land again if the rows change (`rotm.py land MODEL --table <t> --csv <file>
     --force`), and rebuild (`rotm.py build MODEL --force`). A manifest that records no contract hash (built
     before v3) gets a note instead; a contract that is not agreed is not compared.
   - With a manifest: `R-TYPE` (error) when a cell of an integer, number, date, datetime or boolean raw column
     (the manifest's column types, else the contract's) is stored as text, for example the text '1' in an
     integer column, and when a cell of a text column is stored as a number, for example the text key '901'
     turned into the number 901, or as a logical value, for example the text 'TRUE' turned into TRUE. One finding per column names the first cell and the count. Fix: land the data
     again with `--force` and rebuild; a raw sheet holds landed values only.
   - With a manifest, the helper rows a helper sheet must fill end at the last data row of its raw sheet in the
     workbook, not at the last helper row the manifest recorded. The audit reports `H-GAP` and `H-INCONSISTENT`
     only from the first helper row to the last raw row: there an empty or typed helper cell is `H-GAP` at that
     cell, and a differing formula is `H-INCONSISTENT`. Below the last raw row, a row that is empty in every helper
     column gives no finding, and helper formulas (and typed values) are `H-BEYOND` (error), one finding per helper
     sheet that names the rows and the first cell: they read empty raw cells as 0. Fix: clear the contents of those
     rows (column A to the last helper column; do not delete the rows), or run `ROTM extend MODEL --table <t>` (it
     clears them). Raw rows that no helper row reads stay `H-COVERAGE`.
   - With a manifest: `C-ALERT` (info) when an alert (a check of severity `alert`) is raised: its Result cell
     holds a value other than 0, and on the checks sheet that cell shows ALERT (OK when the alert is not raised).
     Alerts are not part of the master check, so `--strict` does not exit 1 for it. A failing check of severity
     `error` is `C-FAIL` (error).
   - With a manifest: `W-SELF-CHECK` (warning) when the actual and expected cells of a check of the
     specification (columns D and E of its row on the checks sheet) hold the same formula (after removing `$`
     and spaces) or links to the same cell: such a check can never fail. The checks build adds itself are not
     compared. Only the kinds `equal` and `at_most` are compared, as in build: a `range` check whose actual
     equals its minimum can still exceed its maximum. Fix: point one side at an independent total (a raw column total, a control total or another
     block's total).
   - With a manifest: `S-NUMFMT` (warning) expects, for a header cell that shows an item of an input list, the
     format of that item: the block's own rows or columns format, else the list's format, else the date format
     (row headers) or the month format (column headers) when the item is an ISO date, else General. A header
     of a list whose format is text may hold the text format or General. Without the specification these
     header cells are not compared, and a note says so.
4. Without a manifest (a workbook the plugin did not build) raw sheets, helper columns and checks are inferred;
   those findings start with "(inferred)", and styles are compared with the dictionary as a whole.
   - When the workbook has check cells, `C-BLOCK` (warning, "(inferred)") names each calculation block that no
     check cell reads: two or more formula cells with one formula copied down a column or along a row, on a
     sheet that is not raw and not a checks sheet. A check reads a block when its formula refers to at least one
     cell of the block (a range that overlaps it counts). Each finding gives the sheet, the first cell and the
     range; the fix is a check that ties the block to an independent total (`references/checks.md`). A note
     gives the count of blocks found, read and not read.
   - A run of cells whose formula is only a reference to one cell (a link such as `=Sheet!B2` or `=B2`) is
     not a calculation block and gets no `C-BLOCK`. A note gives the count of such link runs left out.
   - An empty cell between two runs of one formula in a column is `H-GAP` (warning, "(inferred)") at that cell;
     the fix copies the formula of the cell above into it. A row that holds a label is taken as a deliberate
     break and gets no finding, and so is an empty spacer row (a row with no cell) in a column that reads no raw
     sheet. When the column reads a raw sheet row by row, every gap is reported and the finding counts the raw
     rows of the gap that no helper row reads.
   - `H-COVERAGE` (warning, "(inferred)"): a helper column that reads a raw sheet row by row ends above the raw
     sheet's last row. The count is of the raw rows, from the column's first formula row down, that no helper row
     reads (gaps inside included); the copy-down advice covers the rows missing at the end only.

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

- `errors: N  warnings: M`, then the findings grouped by rule with their fixes.
- When values were not recalculated (`V-NOT-RECALC`): the checks could not be read; run `check` with Excel.
- The audit never writes the workbook.

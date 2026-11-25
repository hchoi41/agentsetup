---
name: compat
description: "Invoked by the user only as /rule-of-thumb-model:compat <workbook>. Rule-of-thumb-model command: make a workbook work in both Excel and Google Sheets: find one-product functions and features, replace or report them."
disable-model-invocation: true
argument-hint: "<workbook>"
---

# compat: make a workbook work in both Excel and Google Sheets

## Names

- `SKILL_DIR`: two folders above this skill's base directory, then `skills/rule-of-thumb-model` (Claude Code prints
  "Base directory for this skill: ..." when it loads a skill; write the path with forward slashes under Git Bash).
- `ROTM`: `python -P -X utf8 -B "<SKILL_DIR>/scripts/rotm.py"` (never `-I`: openpyxl may live in the user
  site-packages, which `-I` hides).
- `MODEL`: the model folder (it holds `rotm_model.json`).

## Read first

The main skill `<SKILL_DIR>/SKILL.md`: the procedure P1 to P7, the hooks and the common mistakes apply to this
command too. Then `<SKILL_DIR>/references/google-sheets.md` (what differs) and `<SKILL_DIR>/references/functions.md` (which functions exist in both).

## Steps

1. Report first: `ROTM compat <workbook> --report-only`.
2. Replace the mechanical cases: `ROTM compat <workbook> --strict` writes `<stem>_compat.xlsx` (`--in-place`
   keeps a `.bak_` copy). It replaces only:
   - `[@Col]` and `#This Row` references by same-row A1 references (`X-STRUCTREF`);
   - a missing `_xlfn.` prefix (`X-PREFIX`) and a known `_xludf.` function (`X-UDF`);
   - `CONCATENATE` by `&` (`X-CONCAT`); values that change because a function name was repaired (`X-FIXED`).
   - an Excel `CONCAT` with one argument or with more than two arguments, by `&` when every argument is a single
     value (`X-CONCAT`, a warning with `--report-only`): Google Sheets' `CONCAT` takes two values, so exactly two
     single values is the form both products share and is left as it is; one argument `a` becomes `a&""` (the
     same text); a `CONCAT` with a range argument is reported and not replaced (write
     `TEXTJOIN("",FALSE,<range>)` or join the cells with `&`).
3. Everything else is reported for the user to decide: one-product functions (`X-FUNC`), whole-table references
   (`X-TABLEREF`), features such as macros, Power Query, connections, slicers, the Data Model (`X-FEATURE`).
   - An Excel `FILTER` with a third argument is `X-FILTER` (a warning) at its cell: in Excel the third argument
     is the value returned when no row matches; in Google Sheets it is a further condition. compat does not
     rewrite it, so `--strict` and `--report-only --strict` exit 1. A `FILTER` with two arguments gets no
     `X-FILTER`. Not with `--product excel`.
   - `X-TABLEREF` (information): the import of Excel tables as Sheets tables is the confirmed announcement of
     11 August 2026; not confirmed, item by item: the table styles, structured references in formulas and
     connected pivot tables (`references/google-sheets.md`, "Excel tables on import").
   - The function catalogue is also applied to the formulas of data validations (custom formulas, list sources
     and limits) and of conditional-format rules. A function that one product lacks is `X-FUNC` with the sheet,
     the range and the kind of rule, for example `data validation (custom formula) on Controls!A1: INFO exists
     in Excel only`. compat reports these formulas and does not rewrite them, so `--strict` and
     `--report-only --strict` exit 1 (an error exits 1 without `--strict` too). Change the rule in Excel: Data >
     Data Validation, or Home > Conditional Formatting > Manage Rules.
4. compat compares the cells and Excel's counts of sheets, tables, charts, names, queries and conditional formats
   before and after; any difference outside the replaced cells is `W-UNINTENDED` and nothing is written.
   `--product excel|sheets` targets one product. The comparison rule is the same in theme, compat and split:
   - formulas and typed values: exactly;
   - calculated values (after both files are recalculated in Excel): within a relative tolerance,
     `--calc-tolerance` (default 1e-9; `--calc-tolerance 0` compares them exactly);
   - a cell that holds RAND, RANDBETWEEN, RANDARRAY, NOW, TODAY, OFFSET, INDIRECT, CELL or INFO, or depends on such
     a cell: its formula is compared, its value is left out;
   - the report states the result in one sentence: the cells compared, the tolerance, and the cells left out with
     the first one.
5. For a function that needs a recent Excel (`X-FUNC`, information), the message names the Excel versions that
   have it, from the function catalogue (`references/functions.md`, column "Excel versions"); for example LAMBDA:
   Microsoft 365 or Excel 2024 (not Excel 2021).

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

- The cells replaced (before and after), and the items reported that compat does not change.
- The comparison sentence (cells compared, the tolerance, the cells left out as volatile and the first one).
- That Google Sheets itself was not opened: compatibility is checked by rule against the documented function lists.
- Exit 3: written without Excel, so values were not compared after recalculation.

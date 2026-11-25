---
name: theme
description: "Invoked by the user only as /rule-of-thumb-model:theme <workbook> [--use NAME]. Rule-of-thumb-model command: apply the style dictionary to an existing workbook without changing any value or formula."
disable-model-invocation: true
argument-hint: "<workbook> [--use NAME]"
---

# theme: apply the style dictionary to an existing workbook (P6)

## Names

- `SKILL_DIR`: two folders above this skill's base directory, then `skills/rule-of-thumb-model` (Claude Code prints
  "Base directory for this skill: ..." when it loads a skill; write the path with forward slashes under Git Bash).
- `ROTM`: `python -P -X utf8 -B "<SKILL_DIR>/scripts/rotm.py"` (never `-I`: openpyxl may live in the user
  site-packages, which `-I` hides).
- `MODEL`: the model folder (it holds `rotm_model.json`).

## Read first

The main skill `<SKILL_DIR>/SKILL.md`: the procedure P1 to P7, the hooks and the common mistakes apply to this
command too. Then `<SKILL_DIR>/references/style-dictionary.md` (sections 5 to 7: how a foreign workbook is read and what theme never changes).

## Steps

1. A workbook built by `rotm.py build`: do not theme it; change the dictionary override and rebuild.
2. See what would change: `ROTM theme <workbook> --dry-run`.
3. Apply: `ROTM theme <workbook> --strict`. It writes `<stem>_theme.xlsx` next to the workbook; the original is not
   changed (`--in-place` replaces it after keeping `<stem>.bak_<time>.xlsx`).
   - Default: font name, size, colour, bold, italic and fill. Also `--number-formats`, `--borders`, `--views`
     (gridlines and zoom), `--align`.
   - `--number-formats` on a workbook with a manifest gives each raw cell the number format recorded for its
     column in the raw sheet's manifest entry (`formats`, written by build), or the column type's default when
     none is recorded; the audit's `S-NUMFMT` expects the same. Without a manifest, raw cells keep their format.
   - A named look: `--use input_blue_font` (or `input_blue_on_fill`, `links_green`, `fast_import_export`,
     `unit_unfilled`, `total_grey_rule`); a project file: `--dictionary style_overrides.json`.
   - Raw sheets the classifier misses: `--raw-sheets S1,S2`.
4. theme compares before and after by code and keeps queries, tables, charts, names and conditional formats. A
   workbook with parts openpyxl would drop is edited through Excel; without Excel it is refused (exit 3,
   `T-FRAGILE`) and nothing is written. The comparison rule is the same in theme, compat and split:
   - formulas and typed values: exactly;
   - calculated values (after both files are recalculated in Excel, when Excel is available): within a relative
     tolerance, `--calc-tolerance` (default 1e-9; `--calc-tolerance 0` compares them exactly);
   - a cell that holds RAND, RANDBETWEEN, RANDARRAY, NOW, TODAY, OFFSET, INDIRECT, CELL or INFO, or depends on such
     a cell: its formula is compared, its value is left out;
   - the report states the result in one sentence, for example `compared 2,734 cells: formulas and typed values
     exactly, calculated values within a relative tolerance of 1e-09; 12 cells left out of the value comparison
     because they hold a volatile function or depend on one (first: DA-DATA!B10)`.
5. Run the follow-up audit that theme prints as the last note of its report (`follow-up audit: ...`):
   `ROTM audit <stem>_theme.xlsx --strict --rules S-FONT,S-SIZE,S-FILL,S-FONTCOLOR,S-BOLD` with the same `--use`
   and `--dictionary` as step 3 (and `--raw-sheets` when step 3 gave it), for example
   `ROTM audit <stem>_theme.xlsx --strict --rules S-FONT,S-SIZE,S-FILL,S-FONTCOLOR,S-BOLD --use input_blue_font`.
   It should report nothing. Without the named look the audit compares the cells with the plugin dictionary and
   reports, for example, a blue input font as `S-FONTCOLOR`.

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

- The output path, and how many cells changed per attribute (`T-STYLE`).
- Whether values and formulas were compared after recalculation in Excel (`verified.recalculated`) or without it,
  and the comparison sentence (cells compared, the tolerance, the cells left out as volatile and the first one).
- What theme leaves alone and says so: conditional formats (`T-CF`), table styles (`T-TABLESTYLE`).
- With `T-FRAGILE`: nothing was written; Excel is needed.

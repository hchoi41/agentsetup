---
name: split
description: "Invoked by the user only as /rule-of-thumb-model:split <workbook> <Sheet!Cell>. Rule-of-thumb-model command: rewrite one formula that nests too deep as helper cells, with values unchanged after recalculation."
disable-model-invocation: true
argument-hint: "<workbook> <Sheet!Cell>"
---

# split: rewrite one over-limit formula as helper cells (P4)

## Names

- `SKILL_DIR`: two folders above this skill's base directory, then `skills/rule-of-thumb-model` (Claude Code prints
  "Base directory for this skill: ..." when it loads a skill; write the path with forward slashes under Git Bash).
- `ROTM`: `python -P -X utf8 -B "<SKILL_DIR>/scripts/rotm.py"` (never `-I`: openpyxl may live in the user
  site-packages, which `-I` hides).
- `MODEL`: the model folder (it holds `rotm_model.json`).

## Read first

The main skill `<SKILL_DIR>/SKILL.md`: the procedure P1 to P7, the hooks and the common mistakes apply to this
command too. Then `<SKILL_DIR>/references/helper-patterns.md`.

## Steps

1. A model built by this plugin holds no over-limit formula (build refuses one). For its specification, move the
   part build names into a helper column, line item or measure and rebuild; `split --spec` is refused in this version
   and says so.
2. A workbook the plugin did not build: `ROTM formula "<the formula>"` shows its depth and the part to move.
3. `ROTM split <workbook> <sheet>!<cell>` writes `<stem>_split.xlsx` (`--in-place` keeps a `.bak_` copy):
   the moved parts go to a sheet `split_helpers` (one helper column aligned by row when the formula is copied down
   a column, else one row per split cell). `--cell-only` splits only the named cell; `--limit N` sets the depth.
4. What split rewrites:
   - a formula over the depth limit: the nested calls the audit names (`F-DEPTH`) move to helper cells;
   - a formula within the depth limit but over the length limit (`F-LENGTH`; 100 characters, or the model's
     `settings.length_limit`): only when it is a chain of `+` and `-` terms at its top level, for example
     `=A1+A2+...+A40`. The terms move, in order, into helper cells of a few terms each, every helper within the
     length limit, every sign kept; the cell then adds the helpers;
   - any other over-long formula (one function call with many arguments, a chain of `*`, `&` or a comparison at the
     top level, one pair of parentheses around everything): split writes nothing and exits 1 with the error
     `SPLIT-SHAPE`, which names the shape; apply the pattern by hand (`<SKILL_DIR>/references/helper-patterns.md`,
     H10);
   - in every part that moves to a helper, `ROW()` and `COLUMN()` without an argument are written with the original
     cell as their argument (`COLUMN()` of `Scenarios!D2` becomes `COLUMN(Scenarios!D2)` on `split_helpers`), so the
     helper gives the cell's row or column also after an input changes, and a call that cannot be written so is
     `SPLIT-GUARD`, which names the call, with nothing written.
5. The cells split writes on `split_helpers` get the style dictionary's look: the heading of each helper column
   the `col_header` look, the helper formulas the `calc` look, in the dictionary's font at the size of a helpers
   sheet (Arial, 8 points, in the plugin dictionary). The strict audit's style rules (`S-FONT`, `S-SIZE`, `S-FILL`, `S-FONTCOLOR`, `S-BOLD`) pass
   for them. Cells of an existing `split_helpers` sheet that split does not write keep their look.
6. split recalculates both files in Excel and requires every value unchanged; any other difference is
   `W-UNINTENDED` and nothing is written. `SPLIT-NONE`: the formula is within both limits; nothing written.
   Formatted text inside a cell (rich text, in shared or inline strings) is a part openpyxl drops: split then
   writes through Excel, which keeps it, or without Excel exits 3 with nothing written, and the comparison
   includes the formatted runs of every other text cell.
7. A part that sits inside IFERROR or IFNA is never moved into a helper on its own, because the helper could show
   an error that the guard hid:
   - a guarded lookup, `IFERROR(INDEX(<range>,MATCH(<key>,<keys>,0)),<value>)` or the same with IFNA, is written
     with the pattern H5 of `<SKILL_DIR>/references/helper-patterns.md`, which keeps the guard on the MATCH part
     and on the INDEX part. `<G>` is the guard (IFERROR or IFNA) and `<T>` its test (ISERROR or ISNA):
     - helper 1 `=<G>(MATCH(<key>,<keys>,0),0)`: the match position, or 0 when the key is missing;
     - helper 2 `=MAX(<helper 1>,1)`: a position INDEX can read (1 when helper 1 is 0);
     - helper 3 `=--<T>(INDEX(<range>,<helper 2>))`: 1 when the guard would replace the result at that position
       (the return cell holds an error, or the position is outside the range), else 0;
     - in place of the guard `IF(OR(<helper 1>=0,<helper 3>=1),<value>,INDEX(<range>,<helper 2>))`.
     Example: `=IFERROR(INDEX(D2:D3,MATCH(A2,C2:C3,0)),0)` in `Model!B2` becomes
     `=IF(OR(split_helpers!B2=0,split_helpers!D2=1),0,INDEX(D2:D3,split_helpers!C2))`; `split_helpers!B2` holds
     `=IFERROR(MATCH(Model!A2,Model!C2:C3,0),0)`, `C2` holds `=MAX(B2,1)` and `D2` holds
     `=--ISERROR(INDEX(Model!D2:D3,C2))`. Every helper and the IF have depth 2 or less. The guard's value is
     written once, in the IF. After the split the cell gives the original's result also when the key changes, when the matched
     return cell later holds an error, and when the match position is outside the INDEX range, and no helper holds
     an error value that the guard hid (Excel tests
     `test_v5_tools.py::test_guarded_lookup_split_keeps_the_result_of_the_original`, with IFERROR and with IFNA,
     and `test_v4_split.py::test_guarded_lookup_split_keeps_the_missing_key_result`). With IFNA, an error of the
     key itself (such as `#DIV/0!`) shows in helpers 1 and 2 and in the cell, as it shows in the original. When the
     IF would be over the depth limit where the guard was, it goes to a helper of its own;
   - a lookup inside IFNA that sits inside IFERROR, for example `=IFERROR(ROUND(IFNA(INDEX(...,MATCH(...)),0),2),0)`:
     split writes nothing and exits 1 with `SPLIT-GUARD`, because a helper that keeps IFNA's result would show the
     error values that the IFERROR hid;
   - any other part inside IFERROR or IFNA, for example `SUM(A1:A3)` in `=IFERROR(ROUND(SUM(A1:A3)/B1,2),0)`:
     split writes nothing and exits 1 with the error `SPLIT-GUARD`, whatever the current values are, also without
     Excel. The finding names the cell, the guard and the part; apply the pattern H5 by hand, or split the formula
     by hand.
8. A part that lies in the second or third argument of IF is never moved into a helper that is calculated for
   every input, because Excel calculates that argument only where the IF takes it (the rule of
   `<SKILL_DIR>/references/helper-patterns.md`, "A part in a branch of IF"):
   - the helper carries the IF's condition: `IF(<condition>,<part>,0)` for a part from the second argument and
     `IF(<condition>,0,<part>)` for a part from the third. A part whose function always gives text (such as TEXT,
     LEFT, TRIM or UPPER) gets empty text `""` in place of 0. Where the IF does not take that branch, the helper
     holds 0 (or `""`) and the cell does not read it. Example: `=IF(A1=0,0,ABS(SQRT(1/A1)))` in `Model!B1` becomes
     `=IF(A1=0,0,ABS(split_helpers!B2))`, and `split_helpers!B2` holds `=IF(Model!A1=0,0,SQRT(1/Model!A1))`. With
     A1 = 1 and with A1 = 0 the cell gives the original's result (1 and 0), and the helper holds no error value
     (Excel test `test_v6_split.py::test_excel_branch_split_keeps_the_result_in_both_branches`);
   - the condition is written as it is after the split: a part of the condition that is over the limit goes to a
     helper of its own, and the condition reads that helper. A helper over the depth limit is split again the same
     way, and its helpers carry the same condition;
   - split writes nothing and exits 1 with the error `SPLIT-GUARD` when the helper cannot carry the condition
     within the limits: the depth limit is 1 (IF and the part need depth 2), the part lies in a branch of two or
     more IFs, the part is the INDEX or MATCH call of a guarded lookup (the helpers of H5 take no condition), or a
     helper that carries the condition, or the cell itself, would be over the length limit. The finding names the
     cell, the part, the argument and the IF; its data and a note say why the condition could not be carried. Apply
     the pattern "A part in a branch of IF" by hand, or split the formula by hand;
   - a part in the first argument of IF (the condition) is calculated whenever the IF is, and is moved as for any
     other part.
9. The new helper cells are part of the comparison: a helper cell that holds an error value (such as `#DIV/0!`)
   after the recalculation while its cell showed no error before is also the error `SPLIT-GUARD`. It names the
   helper cell and the function that kept the error out of the result (such as ISERROR or CHOOSE). Example:
   `=--ISERROR(ABS(ROUND(1/A1,0)))` with A1 = 0 moves `ROUND(1/A1,0)` to `split_helpers!B2`, which holds
   `#DIV/0!` while the cell shows 1. split then writes nothing, does not report `SPLIT-DONE` and exits 1. This
   check needs Excel (exit 3 without it: values not compared). It finds an error value with the current inputs
   only; for IF, step 8 applies before any value is read.
10. Two parts share one helper only when their texts are equal with text in double quotes compared exactly (names
   and references are compared without case): `EXACT(A2,"x")` and `EXACT(A2,"X")` get two helpers.
11. The comparison rule is the same in theme, compat and split:
   - formulas and typed values: exactly;
   - calculated values: within a relative tolerance, `--calc-tolerance` (default 1e-9; `--calc-tolerance 0`
     compares them exactly);
   - a cell that holds RAND, RANDBETWEEN, RANDARRAY, NOW, TODAY, OFFSET, INDIRECT, CELL or INFO, or depends on such
     a cell: its formula is compared, its value is left out;
   - the report states the result in one sentence: the cells compared, the tolerance, and the cells left out with
     the first one.

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

- The cell, its old formula, the helper cells and the new formula.
- Whether values were compared after recalculation (exit 3 means written without Excel, not compared), and the
  comparison sentence.
- With `SPLIT-SHAPE`: nothing was written; the shape it names, and the pattern H10 to apply by hand.
- For a guarded lookup: that the pattern H5 was used: helper 1 holds the match position or 0, helper 2 a position
  INDEX can read, helper 3 holds 1 when the guard replaces the result, and the cell keeps the guard's value.
- With `SPLIT-GUARD`: nothing was written; the cell, the guard and the part it names (or the helper cell, its error
  value and the guard), and the pattern H5 to apply by hand.
- For a part in a branch of IF: that the helper carries the IF's condition and holds 0 (or empty text) where the
  IF does not take that branch; with `SPLIT-GUARD` for such a part, the IF and the part it names, why the condition
  could not be carried (the note), and the pattern "A part in a branch of IF" to apply by hand.

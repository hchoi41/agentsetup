---
name: check
description: "Invoked by the user only as /rule-of-thumb-model:check <model folder> [--refresh]. Rule-of-thumb-model command: after a refresh: raw sheets against their contracts, coverage of every raw row by the helper columns, recalculation in Excel, every check cell read back."
disable-model-invocation: true
argument-hint: "<model folder> [--refresh]"
---

# check: raw against contracts, coverage, recalculation, every check (P7 and after a refresh)

## Names

- `SKILL_DIR`: two folders above this skill's base directory, then `skills/rule-of-thumb-model` (Claude Code prints
  "Base directory for this skill: ..." when it loads a skill; write the path with forward slashes under Git Bash).
- `ROTM`: `python -P -X utf8 -B "<SKILL_DIR>/scripts/rotm.py"` (never `-I`: openpyxl may live in the user
  site-packages, which `-I` hides).
- `MODEL`: the model folder (it holds `rotm_model.json`).

## Read first

The main skill `<SKILL_DIR>/SKILL.md`: the procedure P1 to P7, the hooks and the common mistakes apply to this
command too. Then `<SKILL_DIR>/references/checks.md` (what `rotm.py check` does) and, for Power Query, `<SKILL_DIR>/references/excel-power-query.md`.

## Steps

1. `ROTM check MODEL --strict`. It checks the contracts' states, each `raw/<t>.csv` against the manifest and the
   contract (rows, keys, nulls, types, allowed values, control totals), the raw sheets against the snapshots, the
   coverage of raw rows by helper rows, then recalculates in Excel (and saves), reads every check and the master
   check back, and lists error values. After the recalculation it computes every check and the master check again
   from their own operand cells; a saved result they do not give is `C-RECOMPUTE` (an error;
   `<SKILL_DIR>/references/checks.md`, "C-RECOMPUTE"). Each passing check is one `check <id> OK` line; a failing one is `C-FAIL`
   with its actual and expected values; a raised alert is `C-ALERT` (info: alerts are not part of the master
   check), and its Result cell shows ALERT in red, never FAIL. The last lines say `checks: H of N hold; alerts:
   S of A show a value` (N error checks; A alerts, of which S hold a number other than 0; the alerts part only
   when there are alerts). A check or alert cell without a saved value is counted as not calculated: a workbook
   with no saved values gives `checks: not calculated (0 of N have a value)`, some cells without a value give
   `checks: H of N hold, K not calculated (no value)`, and ` (cached values)` is added when the run did not
   recalculate and at least one cell holds a value. `D-CRITERIA` (a warning) names a label and another value of
   the same column that Excel's criteria matching adds together (for example `001` and `1`):
   `<SKILL_DIR>/references/helper-patterns.md`, "Labels that Excel's criteria matching merges".
   A formula cell without a value is `V-NO-VALUE` with its sheet and cell. A cell Excel names as circular (the
   message starts `no value (circular reference)`): find the cycle with `ROTM trace` and break it. Excel names one
   cell of a cycle per sheet; the other cells of that cycle get the same message and fix. Another formula Excel
   saved without a value (the message `no value`): recalculate (`ROTM check MODEL`) and look at the formula and its
   inputs (`ROTM trace`). A volatile formula whose result is empty text is not reported. A cell whose formulas'
   references show that it lies on a cycle of references (for example A1 `=B1+TODAY()*0` and B1 `=A1+1`) is still
   reported.
   - Excel started but could not open or calculate the workbook: `X-OPEN`, an error, exit 1 (not exit 3). The
     check cells were not read and nothing was saved. The most likely cause is a formula Excel rejects. The strict
     audit finds two such causes: an incomplete formula (`F-PARSE`) and a listed function with too few or too many
     arguments (`F-ARGS`, `references/model-spec-format.md` "Argument counts"). Run
     `ROTM audit "<workbook>" --strict`, correct the expression in `model.spec.json`, rebuild, and run
     `ROTM check MODEL` again. For another cause, open the workbook in Excel and read the message Excel shows. An
     incomplete expression (a trailing or doubled operator, a missing operand, an unclosed bracket or quote) and a
     wrong argument count are refused by `rotm.py formula` and by build (`F-PARSE`, `F-ARGS`; build exit 2 with the
     JSON path).
   - `V-INPUT-RANGE` (an error): check reads each scalar input whose specification declares `valid` (min and max,
     or list) from the saved workbook, after the recalculation, and without Excel as saved. A saved value outside
     it (for example the fixed scale 1000 typed or pasted over as 1) names the cell, the value, the range and the
     input's note; no row of the checks sheet tests it, so the master check can still show OK. Fix: change the
     input's value in `model.spec.json` and rebuild (`ROTM build MODEL --force`); a value typed or pasted into the
     inputs sheet is not kept by a rebuild.
2. After a refresh in Excel: `ROTM check MODEL --refresh --strict` (refreshes every query and waits until it has
   finished). Give `--strict`: without it a warning such as `X-NO-QUERY` leaves exit 0, so read the report's
   `refreshed` value (`data.refreshed` in `MODEL/reports/check.json`). `X-NO-QUERY` stays a warning (an error with
   `--accept-refresh`). It refuses (exit 2, nothing refreshed) a Power Query table whose query was written for
   another contract than the agreed one, and names the `land` commands that write the query again. Before Excel starts it also refuses
   (exit 2, "nothing was refreshed") while a raw table loaded by a query has a contract that is missing, invalid,
   proposed, stale, or agreed only on the example channel; show the user each contract it names and record their
   reply (`ROTM contract show`, `ROTM contract agree`). A workbook with no query or connection gets `X-NO-QUERY`
   (a warning; an error with `--accept-refresh`): nothing was refreshed. A query attached by hand at
   `raw_<t>!A1` counts as a query.
   - Before Excel starts it also refuses (exit 2, nothing refreshed) a table whose `queries/<t>.pq.m` differs from
     the query text attached to the workbook (the hash the attach recorded in the manifest): `land` and
     `land --keep-queries` change the query file only. The refusal names the command that puts it right,
     `ROTM land MODEL --table <t> --power-query` (it attaches the file's query); then refresh again.
   - A table that the manifest records as loaded by Power Query, whose sheet holds no query table at A1:
     `X-NO-QUERY` for that table (a warning; an error with `--accept-refresh`). It was not refreshed and nothing
     is accepted for it. Attach the query again with `ROTM land MODEL --table <t> --power-query`, then
     `ROTM check MODEL --refresh --strict`.
   - A query changed in Excel's Power Query editor (or in any other way than `rotm.py`): the attach records the
     hash of the query formula Excel gives back (`query_formula_sha256` in the manifest), and `check --refresh`
     reads the formula again through Excel before the refresh and compares. On a difference it reports
     `X-QUERY-CHANGED` (a warning; an error with `--accept-refresh`), refreshes no table and accepts nothing:
     `raw/<t>.csv`, `raw/<t>.controls.json` and the manifest stay as they were. Put the change into
     `queries/<t>.pq.m` and run `ROTM land MODEL --table <t> --power-query`. A manifest written before v6 has no
     such hash: a note says that the workbook's query was not compared. For a query attached by hand and carried
     over by build (`query_attach` `hand`), the fix is different: to keep the change, rebuild
     (`ROTM build MODEL --force`; build reads the query from the workbook, attaches it again and records its
     formula); to drop the change, change the query back in Excel's Power Query editor.
   - With Excel, `check --accept-refresh` reads the query back and compares it too, also when it is given without
     `--refresh`: on a difference it reports `X-QUERY-CHANGED` as an error, accepts nothing, and `raw/<t>.csv`,
     `raw/<t>.controls.json` and the manifest stay byte for byte. Without Excel, or for a manifest without the
     hash, the acceptance prints the note that the query was not compared. A value typed into a raw sheet that a
     query loads is not found by an acceptance alone: run `check --refresh` first, which loads the rows from the
     source again.
   - With Excel, `check --refresh` and `check --accept-refresh` also compare each query that a query attached by
     hand reads (the connection-only queries build carried over, recorded in the manifest as `query_reads`) with
     the hash build recorded: a changed, missing or unreadable one is `X-QUERY-CHANGED` and names that query; the
     table is not refreshed, nothing is accepted, and `raw/<t>.csv`, `raw/<t>.controls.json` and the manifest stay
     byte for byte.
   - `I-HIDDEN-DECIMALS` (information; the exit code does not change): a number column of the new rows holds more
     decimals than its number format shows. The values are stored and calculated in full. A rebuild shows them
     when build chose the format; when the contract names the format, agree another format in the contract, then
     rebuild.
3. `H-COVERAGE` (more raw rows than helper rows) or `H-BEYOND` (helper rows below the last raw row, after a refresh
   to fewer rows: they read empty raw cells as 0 and feed the calculations, and the check `<t>_beyond` fails): run
   the fix it prints, `ROTM extend MODEL --table <t>`. `extend` copies the last helper row down to the last raw row
   and clears the helper rows below it (`--rows N`: the last sheet row to reach). It keeps a copy in
   `MODEL/reports/backups/` and compares by code. After a refresh to zero rows it clears every helper row; from
   zero helper rows it writes the first one from the formulas the manifest keeps.
4. Accept the refreshed rows: `ROTM check MODEL --accept-refresh --control <t>.<column>=<new source total>`. It
   records them only when they pass the contract, writes the control total into its input cell, records the total
   with the snapshot's digest and the hash of the agreed contract in force in `MODEL/raw/<t>.controls.json` (a
   rebuild then keeps it while the contract is not agreed again; the contract is not changed) and checks again.
   Running it again under the same agreement gives the same record. A changed static raw sheet is never
   accepted: land the data and rebuild. A new extract with a new source total is landed with
   `ROTM land MODEL --table <t> --csv <file> --force --control <t>.<column>=<source total>`: `land` checks it
   against the contract and the total given and records that total for the new snapshot in
   `raw/<t>.controls.json` (a total that does not equal the file's sum is `L-TOTAL`; nothing is written).
   `--control` is accepted only for a column whose control total the agreed contract names: a control total is
   part of the agreement. For any other column `land` and `check --accept-refresh` refuse (exit 2; nothing is
   written or recorded). Add the total to the contract (`contract propose ... --force ...
   --total <column>=<source total>`), show it and get it agreed; build then writes its check
   `<t>_total_<column>`.
   - The control total used for a table and column is, in this order: `--control` given in this run; else the
     total accepted for this snapshot in `raw/<t>.controls.json` under the agreement in force; else the agreed
     contract's total. A total accepted under an earlier agreement (the contract was agreed again since, for
     example with another total) is not used: `land`, `build`, `check` and `lift` print a note that names it, and
     when the rows do not meet the contract's total, `L-TOTAL` (`land`) or `D-TOTAL` (`check`) names both totals.
     Give `--control` with the source's current total; it is recorded under the agreement in force. Never the
     inputs sheet: a different value there is `D-CONTROL-INPUT` (a warning). A failing `<t>_total_<column>`: find the missing or extra rows in `raw_<t>`;
     after a refresh with a new source total use `--control`.
   - Nothing is accepted when `raw_<t>` still holds the rows `rotm.py` last wrote and `raw/<t>.csv` holds rows
     landed after them (`R-CHANGED`; `raw/<t>.csv` is not changed): run `ROTM check MODEL --refresh --strict` or
     `ROTM build MODEL --force`. A manifest built before v3 records no workbook digest: rebuild once.
   - When `raw/<t>.csv` was landed after `rotm.py` last wrote rows into the workbook (the manifest's landed digest
     differs from its workbook digest, for example after `land --force`), `--accept-refresh` accepts no changed
     rows, whatever `raw_<t>` holds: `R-CHANGED` says that `raw/<t>.csv` was landed after the workbook was last
     written, and `raw/<t>.csv`, `raw/<t>.controls.json` and the manifest stay byte for byte. Rebuild from the
     landed snapshot (`ROTM build MODEL --force`), then refresh (`ROTM check MODEL --refresh --strict`) and accept.
   - Nothing is accepted when `--refresh` was asked for and no refresh ran (Excel missing or busy, the refresh
     failed, no query): `raw/`, the controls files and the manifest are not changed; the exit code is not 0.
   - `CT-VERSION`: the workbook was built from another version of its contract; land again if the rows change,
     and rebuild (`ROTM build MODEL --force`). At an acceptance nothing is accepted for that table in that run:
     `raw/<t>.csv`, `raw/<t>.controls.json` and the manifest are not changed.
   - Changed rows are accepted only from a live query table: when the manifest says `power_query` but the
     workbook holds no query table at A1 of `raw_<t>` (converted to a range or deleted), `R-CHANGED` says so and
     nothing is accepted. Attach the query again and refresh, or rebuild from the snapshot.
   - A query attached by hand is recorded at its acceptance (`landing` `power_query`, `query_attach` `hand`, the
     query's name), also when its first load equals the landed snapshot.
   - `D-TYPE`: a raw cell of an integer, number, date, datetime or boolean column stored as text (the text `1` is
     not the number 1), or a cell of a text column stored as a number (the text `901` is not the number 901) or as
     a logical value (the text `TRUE` is not the logical value TRUE): land the data again and rebuild.
   - `D-KEY-SEPARATOR` (a warning): a key part holds `|`, so the workbook's joined key check may show a repeat
     that is none; the key check of `rotm.py check` stands. Rebuild.
5. `D-KEY` (a repeated key) names the rows ("row 12 repeats row 4"): look at those rows with the user; if the user
   accepts them, propose the contract again with `--key-repeats <count>` and get the user's agreement; otherwise
   have the source corrected. Do not remove rows. Keys compare as exact text (case-sensitive, no wildcard).
6. Without Excel (Excel missing, or `--no-excel`): exit 3 when nothing failed ("audited but not recalculated");
   errors still give exit 1. Excel busy: exit 5, run again later. Excel started but could not open the workbook:
   `X-OPEN`, exit 1 (step 1); it is not "not recalculated".
   - A workbook that holds a function only Google Sheets has: check reports `X-SHEETS-ONLY` (information) with
     the functions and their cells, does not open the workbook in Excel, leaves the file byte for byte and exits 3
     (not recalculated). Say in the report "audited, not recalculated: functions only Google Sheets has". The
     functions are those `<SKILL_DIR>/references/functions.md` marks Excel `no` and Sheets `yes` (JOIN, SPLIT,
     REGEXMATCH and others); the fix names the alternative of each (for JOIN the `&` operator), which Excel can
     check. With `--refresh` or `--accept-refresh` nothing is refreshed or accepted in that run.

## Exit codes

| Code | Meaning | What to do |
| --- | --- | --- |
| 0 | done; with `--strict`, no warning either | continue |
| 1 | findings: an error (`X-OPEN` included), or with `--strict` any warning | fix each finding: every line names the sheet, the cell and the fix |
| 2 | refused: bad input, nothing written | change what the message names |
| 3 | not available or not verified (for example Excel is missing) | say "not recalculated" (or "not rendered") in the report |
| 4 | internal error: one line; the traceback is in the state folder's `last_error.txt` | report it to the user; do not work around it by editing files |
| 5 | Excel busy: another job holds the Excel lock | wait and run again |

## What to tell the user

- `checks: H of N hold; alerts: S of A show a value` and the master check's value, or every failing check with
  its actual and expected values. A raised alert shows ALERT; it is not a failed check.
- "audited but not recalculated" when Excel was not available or `--no-excel` was given; never "the checks hold".
- "audited, not recalculated: functions only Google Sheets has" after `X-SHEETS-ONLY`, with the functions and
  cells it names; list the model under "Not verified".
- After `X-OPEN`: Excel could not open the workbook, so no check value was read; name the formula the strict
  audit reports.
- Uncovered rows and the fix, when `H-COVERAGE` appears; the helper rows below the raw rows and the fix, when
  `H-BEYOND` appears; the rows that repeat a key, when `D-KEY` appears.
- After `--accept-refresh`: which tables were accepted, or that none was and why (`R-CHANGED`, no refresh ran,
  `X-NO-QUERY`).

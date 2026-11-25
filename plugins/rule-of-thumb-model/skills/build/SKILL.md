---
name: build
description: "Invoked by the user only as /rule-of-thumb-model:build <spec or model folder>. Rule-of-thumb-model command: build a model workbook from a model specification (the full procedure P1 to P7 when no specification exists yet)."
disable-model-invocation: true
argument-hint: "<spec or model folder>"
---

# build: compile the model specification into the workbook (P2 to P7)

## Names

- `SKILL_DIR`: two folders above this skill's base directory, then `skills/rule-of-thumb-model` (Claude Code prints
  "Base directory for this skill: ..." when it loads a skill; write the path with forward slashes under Git Bash).
- `ROTM`: `python -P -X utf8 -B "<SKILL_DIR>/scripts/rotm.py"` (never `-I`: openpyxl may live in the user
  site-packages, which `-I` hides).
- `MODEL`: the model folder (it holds `rotm_model.json`).

## Read first

The main skill `<SKILL_DIR>/SKILL.md`: the procedure P1 to P7, the hooks and the common mistakes apply to this
command too. Then `<SKILL_DIR>/references/model-spec-format.md` (every key, placeholder and block shape).

## Steps

1. No model folder or no agreed contracts yet: follow the main SKILL.md from step 0 (init) and P1 (contract)
   first. `build` refuses a raw table whose contract is not agreed.
2. Land each raw table: `ROTM land MODEL --table <t>` (writes `queries/<t>.sql` and `queries/<t>.pq.m`), then
   `ROTM land MODEL --table <t> --csv <extract.csv>` (checks the extract against the contract, writes `raw/<t>.csv`).
   Korean Excel CSVs: `--encoding cp949`.
3. Write or change `MODEL/model.spec.json`. Start from a template when one fits (`ROTM init --list-templates`).
4. `ROTM build MODEL --strict`.
   - Exit 2: the specification, a contract or a snapshot has a problem; every problem is listed with its JSON path
     and the fix; nothing was written. A formula over the depth limit names the part to move into a helper. A
     listed function with too few or too many arguments is `F-ARGS` (for example `IF` with 4 arguments; the 29
     functions and their counts are in `<SKILL_DIR>/references/model-spec-format.md`, "Argument counts").
     Expressions that read each other in a cycle are refused with the JSON path of each expression in the cycle.
     A helper capacity above the landed rows (`--capacity`, the contract's `refresh.capacity_rows`,
     `helper_spare_rows` above 0) is refused with its source.
   - Exit 1: built with warnings (`W-NO-CHECK`: a block no check of severity error reads; an alert does not count;
     `W-SELF-CHECK`: a specification check whose actual and expected compile to the same expression or to links to
     the same cell, so it can never fail: point one side at an independent total; `F-LENGTH`: over 100
     characters; `W-CAPACITY`; `W-PQ-NOT-ATTACHED`). Fix and build again.
   - Build owns a workbook only through the chain of writes from its own output. An existing workbook is replaced
     without `--force` only when it is the output of a build, or the output of a `rotm.py` write (check or recalc
     after a recalculation, extend, theme, compat, split, `land --power-query`) whose input was a workbook build
     owns, followed back step by step to a build. `MODEL/reports/writes.json` keeps these input and output
     hashes. A workbook edited in Excel and then checked (or audited) is still refused without `--force`; with
     `--force` the edited file is kept as `model.bak_<time>.xlsx`.
   - Inputs are changed in `model.spec.json`, then rebuilt. A value typed into the workbook's inputs sheet is not
     kept by a rebuild.
   - `"landing": "power_query"` on a raw sheet attaches the query through Excel after the build. On a rebuild
     over a workbook that holds a query table on `raw_<t>`, build first makes the tests the attach makes before
     Excel starts (the stamp of `queries/<t>.pq.m` is missing or names another contract, or a placeholder such
     as `<server>` is left in the file). When one fails, build refuses (`B-REFUSED`, exit 2) before anything is
     written, and prints the commands that put it right (`ROTM land MODEL --table <t>`, then build again). A
     first build with no query table yet writes static values and warns `W-PQ-NOT-ATTACHED`.
   - A raw sheet loaded by a query attached by hand (the manifest records `query_attach` `"hand"`, or the workbook
     holds a query table at `raw_<t>!A1` that no `rotm.py` command attached) is never given the generated query of
     `queries/<t>.pq.m`. With Excel, build reads the query's name and M text from the existing workbook, attaches
     the same query to the new workbook before it replaces the old one, keeps the M text in
     `queries/<t>.hand.pq.m` and records `query_attach` `"hand"`; the report says the query was carried over and
     names the file. Without Excel, or when the text cannot be read or attached again, build refuses (exit 2) and
     writes nothing: run it on a PC with Excel, or run `ROTM build MODEL --drop-hand-queries` to rebuild the sheet
     as a static table (W-PQ-NOT-ATTACHED: the query is removed and must be attached by hand again,
     `references/excel-power-query.md` "Attaching a query by hand"; `check --refresh` then reports X-NO-QUERY).
   - A raw table with zero rows (a header-only extract under a contract agreed with `--allow-empty`) gets zero
     helper rows: the helper sheet holds its header row only, every helper range in a formula ends at row 2, and
     the manifest keeps each helper column's formula, from which `ROTM extend MODEL --table <t>` writes the rows
     after a refresh that returns rows. The checks `<t>_cover` and `<t>_beyond` give 0.
   - A row or column header cell that shows an item of an input list takes a number format: the block's rows or
     cols `format`, else the list item's `format`, else the date format for a row list (the month format for a
     column list) whose first value is an ISO date (YYYY-MM-DD), else General. A list whose `format` is `text`
     gives its header cells the text format `@` (the audit accepts `@` or General there). A text label keeps General
     when it is not an item of an input list (for example `Total`). The audit expects the same format.
   - The manifest records for each raw table `contract_sha256` (the hash of the agreed contract build read;
     `check` and `audit` report `CT-VERSION` when the agreed contract changes) and `workbook_digest` (the digest of
     the rows build wrote, which `check --accept-refresh` compares with the landed snapshot's `digest`).
5. `ROTM audit MODEL --strict` (exit 0), `ROTM check MODEL --strict` (Excel recalculation; every check must hold).
   On the checks sheet a failing check shows FAIL in red; a raised alert shows ALERT in red (C-ALERT, info), never
   FAIL, and is not part of the master check.
   The folder commands and the hooks use the workbook `rotm_model.json` names; build without `-o` keeps it equal
   to the specification's `output`. After `ROTM build MODEL -o <file>` the marker is not changed: give that file
   to audit and check, as the build report's commands do.
6. `ROTM render <workbook>` with the workbook the build report names (`MODEL/<output>`, `model.xlsx` by default),
   then open every PNG in `MODEL/reports/render/` with the Read tool and look (main SKILL.md, P7). Fix the
   specification and rebuild until the sheets are right.

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

- The workbook path, the sheets, and the key results with their units.
- `checks: H of N hold; alerts: S of A show a value` from `check` (the alerts part only when the model has
  alerts), or "audited but not recalculated" when Excel was not available.
- That the renders were looked at, and anything still open.

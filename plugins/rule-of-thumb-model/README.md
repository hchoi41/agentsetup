# rule-of-thumb-model

A Claude Code plugin for spreadsheet models in Excel and Google Sheets that sit on raw data landed by SQL (Get Data /
Power Query in Excel, Connected Sheets in Google Sheets). It holds one skill of the same name, eleven commands, four
hooks and a library of model templates.

Version 0.9.0, built 2026-11-26.

## What it does

Max's method, in his words (spec, 2026-11-24):

- "after using the Connected Sheet or PowerQuery, the data should be loaded on Sheet as Raw files."
- "all the analysis after that should be done using the formulas and operations within the workbook"
- "coordinate and agree with the user, what columns will be pulled into in which shapes"
- "less than 3 nested functions at most. (if we require something more than that perhaps we need a helper column).
  and we would also want some chcekrs."
- "Arial 8 or Arial 9 ... light shade gray for the column headers and slightly less shade gray for row headers."

What the plugin makes an agent do (the procedure in `skills/rule-of-thumb-model/SKILL.md`):

| Step | What |
| --- | --- |
| P1 | agree a column contract per raw table with the user before any SQL; the agreement is recorded in the user's words, for one contract version; known defects of the raw data (a repeated key, a few empty cells) are agreed as known exceptions, so every delivered row is landed and still checked |
| P2 | land the raw data: the SQL only shapes it to the contract; each raw table alone on its own sheet |
| P3 | model with workbook formulas only: helper columns and helper cells, one step per helper |
| P4 | keep every formula short: at most 2 nested functions (one setting); deeper formulas are refused with the part to move |
| P5 | checks: raw data against its contract, each calculation block against an independent total, one master check |
| P6 | every cell styled from one style dictionary (Arial 8 or 9, grey headers, minimal colour) |
| P7 | strict audit, recalculation in Excel with every check read back, renders of every sheet that the agent looks at |

One script, `scripts/rotm.py`, owns the workbook: the agent writes a JSON model specification and `rotm.py build`
writes the layout, the cell references, the styles and the check wiring.

## Contents

```
rule-of-thumb-model/
  .claude-plugin/plugin.json
  README.md                         this file
  hooks/                            hooks.json and the four hook scripts (standard library only)
  templates/                        the template library: budget_vs_actual, rolling_forecast, price_volume_mix, ar_aging
  skills/
    rule-of-thumb-model/            the skill (complete on its own: copied alone into a skills/ folder it works)
      SKILL.md                      overview, inputs, procedure P1 to P7, commands, quick reference, common mistakes
      scripts/rotm.py               (the one command line; scripts/formula_audit.py is the audit alone)
      scripts/lib/                  the modules; scripts/tests/ the pytest suite
      references/                   style dictionary, specification format, functions, column contract, checks,
                                    helper patterns, Excel Power Query, Google Sheets, FAST rules
      examples/sales_vs_target/     the worked example: data, contracts, specification, model.xlsx, renders
      templates/starter/            the starter template
    contract/ build/ theme/ audit/ check/ compat/ trace/ split/ lift/ diff/ document/
                                    the eleven command skills
```

## Commands

Each command is a skill the user invokes by name; the main skill also answers plain requests ("build a model from
these extracts").

| Command | What it does |
| --- | --- |
| `/rule-of-thumb-model:contract` | proposes the destination columns per raw table and records the user's agreement before any SQL |
| `/rule-of-thumb-model:build` | builds the workbook from a model specification (the whole procedure when none exists) |
| `/rule-of-thumb-model:theme` | applies the style dictionary to an existing workbook; values and formulas compared before and after |
| `/rule-of-thumb-model:audit` | audits any workbook: depth, length, typed numbers, raw sheets, helper columns, checks, error values, styles, one-product functions |
| `/rule-of-thumb-model:check` | after a refresh: raw sheets against contracts, helper coverage, recalculation in Excel, every check read back |
| `/rule-of-thumb-model:compat` | makes a workbook work in both Excel and Google Sheets: replaces or reports one-product functions and features |
| `/rule-of-thumb-model:trace` | lists in words the chain from one cell back to the raw rows |
| `/rule-of-thumb-model:split` | rewrites one over-limit formula as helper cells, values unchanged after recalculation |
| `/rule-of-thumb-model:lift` | writes a Python script that reproduces every helper column and ties out to the workbook |
| `/rule-of-thumb-model:diff` | lists the formulas, inputs and outputs that changed between two versions; `diff` reports `saved_values_compared` (both files hold values saved by Excel) and `recalculated` (true only when `reports/status.json` of each file's model folder records a passing `check` or `recalc` of that file's current version); for a workbook set to manual calculation it prints a note that names the file; while that file's version has no passing `check` or `recalc` on record, the note says that its saved values may be out of date and to run `rotm.py recalc` on both files before the diff. |
| `/rule-of-thumb-model:document` | writes the model's map: sheets, inputs, helper columns, checks, contracts |
| `/rule-of-thumb-model:rule-of-thumb-model` | the main skill: the full procedure |

The command line behind them: `python -P -X utf8 -B "<plugin folder>/skills/rule-of-thumb-model/scripts/rotm.py"
<subcommand>` (`rotm.py -h` lists the subcommands; exit codes 0 OK, 1 findings, 2 refused, 3 not available or not verified,
4 internal error, 5 Excel busy).

## Hooks

The hooks act only inside a model folder: a folder holding `rotm_model.json`, which `rotm.py init` writes (and
`rotm.py contract propose` when it is absent). Outside a model folder every hook exits with no output.

| Hook | Event | Refuses or reports |
| --- | --- | --- |
| contract gate | PreToolUse on Write, Edit, MultiEdit, Bash, PowerShell | SQL or Power Query text (`queries/<t>.sql`, `queries/<t>.pq.m`) while table t's contract is not agreed; any other `.sql` or `.m` file in the model folder; a text edit of an agreed contract or one that would mark it agreed; `rotm.py contract agree` with a `--reply` that reads as a refusal (a negative first word, or a negated agreement anywhere), or holds a word that refuses or stops (reject, decline and refuse (also rejected, rejection, declined, refused, refusal); hold off and hold on; a negation before run, proceed, start, land, continue or go ahead (do not run the query yet); please wait, wait until and wait for (no need to wait is not a stop); 거절, 반려, 거부, 보류, 기다려, 하지 마; no list of words is required in an agreement, and the reply need not name the table), or asks for a change (change, rename, remove, drop, replace, switch, fix, correct, revise, modify; 바꿔, 변경해, 수정해, 고쳐; not when negated, such as "no changes"), or postpones the decision with no agreement (will review, review it later or tomorrow, get back to you, need to check or look first, not yet, let me check; 나중에, 내일, 검토 후, 확인해 볼게, 아직: no agreement was given, wait for the user's reply), or is only a question (every sentence a question and no statement: answer the question and wait for the user's reply), with a reply the user never typed (when the reply is part of a longer user message, the sentences that hold it and the whole message are read for a refusal in the same way; a refusing sentence that names another table than the one given with `--table` does not count against this table), or with a reply typed before the latest `contract propose` or edit of that table's contract (checked against the session transcript when it can be read); proposing and agreeing in one command |
| raw-sheet guard | PreToolUse on Write, Edit, MultiEdit, NotebookEdit, Bash, PowerShell | a text-tool edit of `raw/*` or of a workbook; a shell write into `raw/`; a shell command other than `rotm.py` that copies onto an existing workbook, redirects into or saves a model workbook. A copy command (`cp`, `copy`, `Copy-Item`, `xcopy`, `robocopy`, Python's `shutil` copy) to a new workbook name that does not exist yet is let through |
| audit on write | PostToolUse and PostToolUseFailure on Bash, PowerShell | runs `rotm.py audit <workbook> --strict` on each workbook a command changed; findings are shown to the agent (up to 15 lines), a clean audit is one line. The line says `(recalculated)` when the model's `reports/status.json` holds a passing `check` or `recalc` record that recalculated the workbook's current version (the same sha256), and otherwise `(the audit read the saved values; recalculation was not verified by this hook)` |
| finish check | Stop | when the session wrote a workbook that has no clean strict audit or no passing `check` of its current version, the first stop is refused once; the refusal of the recalculation part is lifted when the last message says "not recalculated"; the next stop always goes through, and while the workbook is still unverified it shows the user one line (`systemMessage`): `rule-of-thumb-model finish check: not verified at the end of the session: <workbook>: <what is missing>.` A stop let through because the last message says "not recalculated" is remembered for each such workbook's sha256: a later stop in the same session does not refuse again for a workbook unchanged since that message; a workbook written after it is judged again. A passing audit or check counts only while `raw/` and the agreed contracts of the model folder are as they were when it ran (each `audit`, `check` and `recalc` record in `reports/status.json` holds their fingerprint, `inputs`): after a landing or a new agreement the stop is refused once, and the refusal names the table and the command that brings the workbook up to date (`build`, or `check --refresh` for a table loaded by a query). |

- Not a security boundary: a hook whose own script fails lets the action through and prints one line,
  `rule-of-thumb-model hook <name>: internal error (<type>); the action was let through.`
- The hooks cannot see: a script that builds a workbook path at run time (the audit on write still audits the
  result), edits made in Excel, MCP tools.
- State: `%LOCALAPPDATA%/rule-of-thumb-model/` (`ROTM_STATE_DIR` moves it): hook session records (deleted after
  7 days), the Excel job log, `last_error.txt`, `hook_last_error.txt`. The Excel lock stays at
  `%LOCALAPPDATA%/rule-of-thumb-model/excel.lock` unless `ROTM_EXCEL_LOCK` is set.
- Hook commands run through bash; Claude Code on Windows runs them with Git Bash.

## Install

Requirements:

| Need | For | Tested |
| --- | --- | --- |
| Windows with Git Bash | the hooks | Windows 11 |
| Python 3.11 or later on `PATH` as `python` | every command: each one starts Python with `-P`, an option added in Python 3.11 | Python 3.14 |
| the Python package openpyxl | every command | openpyxl 3.1.5 |
| the Python package pytest | `rotm.py selftest` and the tests in `scripts/tests/` only; without it `rotm.py selftest` prints one line that names the missing package and exits 3, with no traceback | pytest 9.1.1 |
| Microsoft Excel | `check`, `recalc`, `render`, Power Query landing and the verified paths of `theme`, `compat` and `split` | Excel 16.0 |
| the Python package pandas | only the script that `lift` writes | pandas 3.1.0 |

Placeholders used below:

| Placeholder | What to write |
| --- | --- |
| `<plugin folder>` | the folder that holds this README and `.claude-plugin/plugin.json` |
| `<marketplace folder>` | a local folder that holds `.claude-plugin/marketplace.json` |
| `<owner>/<repo>` | a GitHub repository that holds `.claude-plugin/marketplace.json` at its root |
| `<marketplace name>` | the `name` field of that `marketplace.json` |

**For one session (nothing installed):**

```
claude --plugin-dir "<plugin folder>"
```

The commands then appear as `/rule-of-thumb-model:<command>` and the hooks are active in that session.

**Installed from a local marketplace:**

1. A marketplace is a folder whose `.claude-plugin/marketplace.json` lists plugins. Copy the plugin folder to
   `<marketplace folder>/rule-of-thumb-model/` and write `<marketplace folder>/.claude-plugin/marketplace.json`:
   ```json
   {"name": "<marketplace name>", "owner": {"name": "<your name>"},
    "plugins": [{"name": "rule-of-thumb-model", "source": "./rule-of-thumb-model",
                 "description": "Spreadsheet models on landed raw data: contract, build, theme, audit, check, ..."}]}
   ```
2. In Claude Code: `/plugin marketplace add <marketplace folder>`.
3. `/plugin install rule-of-thumb-model@<marketplace name>`, then restart Claude Code.

**Installed from a GitHub marketplace:**

1. The repository `<owner>/<repo>` holds `.claude-plugin/marketplace.json` at its root. Its entry for this plugin
   has `"name": "rule-of-thumb-model"` and a `source` that is the plugin folder's path inside the repository,
   starting with `./` (for example `"./plugins/rule-of-thumb-model"`).
2. In Claude Code: `/plugin marketplace add <owner>/<repo>`.
3. `/plugin install rule-of-thumb-model@<marketplace name>`, then restart Claude Code.

An installed plugin runs from the copy Claude Code keeps in its own plugin folder. Every path the plugin uses is
relative to its own folder, so it works from that copy:

- `hooks/hooks.json` starts each hook as `"${CLAUDE_PLUGIN_ROOT}/hooks/<hook>.py"`; Claude Code sets
  `${CLAUDE_PLUGIN_ROOT}` to the installed plugin's folder.
- The hooks find `skills/rule-of-thumb-model/scripts/rotm.py` from their own file's folder (`hooks/_hooklib.py`).
- The skills tell the agent to run `"<SKILL_DIR>/scripts/rotm.py"`, where `SKILL_DIR` comes from the base
  directory Claude Code prints when it loads a skill.
- The state folder and the Excel lock come from `%LOCALAPPDATA%` (see "Environment variables").

The skill alone (no commands, no hooks): copy `<plugin folder>/skills/rule-of-thumb-model/` into a `skills/` folder
Claude Code reads. The scripts' own refusals still apply there (`land` and `build` refuse an unagreed contract).

**Check the installation:**

1. First check, which needs neither Excel nor pytest (Python and openpyxl only):
   ```
   python -P -X utf8 -B "<plugin folder>/skills/rule-of-thumb-model/scripts/rotm.py" init --list-templates
   python -P -X utf8 -B "<plugin folder>/skills/rule-of-thumb-model/scripts/rotm.py" formula "=SUM(A1:A3)"
   ```
   The first command lists the five templates (`starter`, `ar_aging`, `budget_vs_actual`, `price_volume_mix`,
   `rolling_forecast`) and exits 0. The second prints `depth: 1 (limit 2): SUM` and exits 0.
2. The full check, which needs pytest and Excel:
   `python -P -X utf8 -B "<plugin folder>/skills/rule-of-thumb-model/scripts/rotm.py" selftest --strict`.
   `--quick` runs pytest without Excel, the strict builds and the `portable` scan only. The runs made on the build PC
   are listed under "What is and is not verified".

## Environment variables

| Variable | Effect |
| --- | --- |
| `ROTM_STATE_DIR` | the state folder (default `%LOCALAPPDATA%/rule-of-thumb-model/`); the Excel lock does not move with it |
| `ROTM_EXCEL_LOCK` | the Excel lock file (default `%LOCALAPPDATA%/rule-of-thumb-model/excel.lock`, one per Windows user) |
| `ROTM_EXCEL_TIMEOUT` | seconds to wait for the Excel lock (default 600) |
| `ROTM_NO_EXCEL=1` | behave as if Excel were not installed |
| `ROTM_PYTHON` | the interpreter the audit-on-write hook starts |
| `ROTM_HOOK_LOG=<file>` | one JSON line per hook run, for debugging |
| `ROTM_FOREIGN_DIR` | copies of workbooks the plugin did not make, for `selftest` (F08a) |
| `ROTM_EXAMPLE_ROOTS` | folders where the shipped examples' own agreements are accepted (tests only) |
| `ROTM_SOURCE_TREE` | the project's source tree (the folder that holds `research/` and `build/`); switches on the tests in `test_references.py` that compare the references with the design file and the research's claim checks. Without it those tests skip (tests only) |

## Excel

- Every Excel job starts its own hidden instance, records its process id, and closes only that process; an Excel
  the user has open is never attached to or closed.
- One Excel job at a time runs on the machine, behind one lock file per Windows user
  (`%LOCALAPPDATA%/rule-of-thumb-model/excel.lock`; `ROTM_EXCEL_LOCK` moves it, `ROTM_STATE_DIR` does not).
- A render copies each sheet to the clipboard (whatever the clipboard held is lost) and shows rows 1 to 200 and
  columns A to AN by default; a larger used range is reported as cut off. A blank render is retried twice.
- Paths longer than 200 characters are recalculated on a copy in the state folder (Excel cannot open paths longer
  than 218 characters).

## What is and is not verified

The build PC: Windows 11, Python 3.14, openpyxl 3.1.5, Excel 16.0. Version v9 of 2026-11-26.

- The runs made on the final v9 files are listed in the first subsection.
- An item of the verified list holds for v9 only when the tests it names passed in one of those runs.

### Runs on the final v9 files

- The whole pytest suite without Excel, from the skill folder (`ROTM_NO_EXCEL=1 python -P -X utf8 -B -m pytest
  scripts/tests -q -p no:cacheprovider -rfE`), three runs one after another: first with `--basetemp` set to a folder
  whose path is 150 characters long (removed from its place afterwards, before the next run started): exit 0; 3,383
  passed, 222 skipped, 0 failed; 2026-11-26, 20:51 to 21:03 KST.
- The same suite with the default temporary folder: exit 0; 3,383 passed, 222 skipped (213 Excel test items, 7 checks
  that need `ROTM_SOURCE_TREE` and the 2 document tests that wait for this list), 0 failed; 2026-11-26, 21:03 to 21:15
  KST.
- The same suite with `--basetemp` set to a folder inside the skill folder and `-rs`, last (removed from the skill
  folder afterwards by moving it out of the plugin; no other run of the suite ran at the same time): exit 0; 3,383
  passed, 222 skipped, 0 failed; 2026-11-26, 22:07 to 22:19 KST.
- The Excel tests of the 58 test files that hold Excel tests (the modules changed in v9 are used by almost every one
  of them: workbook, diff, schemas, controls, land, build, check, lift, criteria, compat, functions, audit,
  modelfolder, classify and the hooks) (`python -P -X utf8 -B -m pytest <the 58 files> -m excel -q -p no:cacheprovider
  -rfE`, Excel 16.0): exit 0; 213 passed, 0 failed; 2026-11-26, 20:53 to 22:07 KST.
- `rotm.py selftest --quick`: exit 0; steps pytest (3,383 passed, 222 skipped, without Excel), examples (6 folders
  built and audited in strict mode) and portable (361 files, no hit) passed; 2026-11-26, 20:53 to 21:06 KST.
- Selftest step `f02_skill_alone`, run alone (the skill folder copied alone into an empty folder; its `selftest
  --quick` without Excel; then `templates/starter` built and audited in strict mode there): passed; in the copy pytest
  gave 2,871 passed, 643 skipped, 0 failed; 2026-11-26, 20:53 to 21:02 KST.
- Selftest step `f27_power_query`, run alone with Excel (a Power Query table over a local CSV: 60 rows, then 75, then
  40, then a rebuild): passed in 137 s; 2026-11-26, 20:51 to 20:53 KST.
- `rotm.py lift <copy> --run` on copies of the six shipped folders: exit 0 each; 21,160 compared cells, each ties out;
  2026-11-26, 20:53:42 to 20:53:48 KST.
- Not run by the builder: the full `rotm.py selftest --strict` (its steps recalc, render, orphans and f08a_existing
  were not run); the orchestrator runs it on the snapshot.

### Verified, with the tests that show it

- Power Query over a local CSV: the strict audit exits 0 after the attach, after a refresh to more rows and `extend`,
  and after a refresh to fewer rows and `extend`; a refresh to fewer rows fails `<t>_beyond` and the master check
  (`H-BEYOND`) until `extend` clears the helper rows below the raw rows; after a refresh accepted with a new control
  total, `build --strict` and `check --strict` exit 0, the inputs sheet holds the accepted total, and the spend
  block's total equals the total computed in Python from the new CSV. Tests:
  `test_v2_core.py::test_power_query_raw_table_keeps_its_formats_through_attach_growth_and_shrink`,
  `test_v2_raw_layer.py` and selftest step `f27_power_query` (run alone on the final v9 files, in the runs above).
- The acceptance rules of a refresh (nothing is accepted while `raw/<t>.csv` was landed after the workbook was last
  written, nothing is accepted when `--refresh` did not run, the inputs sheet is never a control total, a contract
  agreed again fails `check` and `audit` with `CT-VERSION` until the rebuild):
  `test_v3_raw.py::test_accept_refresh_after_land_force_keeps_the_csv` (Excel),
  `::test_accept_refresh_without_excel_accepts_nothing`, `::test_inputs_sheet_value_is_not_a_control` (Excel),
  `::test_contract_agreed_again_fails_check_and_audit_until_rebuild`,
  `test_v8_check.py::test_acceptance_after_a_later_landing_is_refused` (Excel),
  `test_v8_check.py::test_acceptance_with_a_manifest_digest_mismatch_accepts_nothing_without_excel`.
- The empty table (a header-only CSV under a contract agreed with `--allow-empty`; through Power Query 1 row, then 0,
  then 1): `test_v3_raw.py::test_header_only_csv_builds_audits_and_checks`,
  `test_v3_raw.py::test_empty_table_through_power_query_one_zero_one` (Excel).
- The hand-attached query (a query table at `raw_<t>!A1` attached in Excel without `rotm.py` is accepted and
  recorded): `test_v3_raw.py::test_hand_attached_query_is_accepted` (Excel).
- A query attached by hand carried over a rebuild (v4: with Excel its M text is kept in `queries/<t>.hand.pq.m` and
  attached again with the same name, and a later refresh reads its source; without Excel the rebuild is refused and
  nothing is written): `test_v4_build.py::test_hand_attached_query_is_carried_over_a_rebuild` (Excel),
  `test_v4_build.py::test_rebuild_over_a_hand_attached_query_without_excel_is_refused`.
- The first acceptance of a query attached by hand (v4: recorded also when its first load equals the landed
  snapshot; the next strict audit exits 0): `test_v4_check.py::test_first_acceptance_of_a_hand_attached_query_records_it`
  (Excel).
- The criteria warning (v4: labels that Excel's criteria matching takes as equal, such as `001` and `1`, give
  `D-CRITERIA` in `build` and `check`): `test_v4_check.py::test_criteria_match_follows_excel_countifs` (Excel),
  `test_v4_check.py::test_check_warns_for_criteria_labels_that_excel_merges` (Excel),
  `test_v4_check.py::test_build_warns_for_criteria_labels_that_excel_merges`.
- The guarded-lookup split (v5: INDEX with MATCH inside IFERROR or IFNA is split into the H5 pattern with three
  helpers; with the key present, the key missing, the matched return cell changed to `=NA()` or `=1/0`, and the
  match position outside the INDEX range, the host's value is the original's and no helper holds an error value that
  the guard hid): `test_v5_tools.py::test_guarded_lookup_split_keeps_the_result_of_the_original` (Excel, 10 cases),
  `test_v4_split.py::test_guarded_lookup_split_keeps_the_missing_key_result` (Excel).
- `lift` after an accepted refresh (the control total accepted for the snapshot under the agreement in force, from
  `raw/<t>.controls.json`; `lift --run` ties out): `test_v3_lift.py::test_lift_ties_out_after_an_accepted_refresh_with_a_new_control` (Excel).
- `V-NO-VALUE` for a volatile formula (`=IF(TODAY()>1,"","later")` saved by Excel is not reported; a circular pair
  still is): `test_v3_core.py::test_volatile_empty_text_is_not_v_no_value` (Excel).
- Data that breaks its contract: rows the user accepts are landed as known exceptions and the workbook's key and null
  checks still count them (Excel; `test_v2_docs.py`); keys are compared exactly (case-sensitive, no wildcard, text
  never read as a number) in land, check, propose, the workbook and lift (`test_v2_core.py`, `test_v2_raw_layer.py`).
- `lift`: for the worked example and each template, the generated Python script recomputes every helper column, block
  and check from `raw/*.csv`, and every compared cell ties out (difference at most the check tolerance 0.001 plus
  1e-13 times the larger value; the largest difference is printed) (`rotm.py lift <copy> --run` on copies of the six
  folders, in the runs above; `test_v4_lift.py::test_shipped_models_tie_out_in_every_cell`; selftest step `recalc`
  runs the same).
- `lift` reproduces PMT, PV, FV and NPV (117 formulas equal Excel's values within a relative 1e-9) and names each
  function it does not reproduce before the script is written (`LIFT-UNSUPPORTED`):
  `test_v3_lift.py::test_financial_functions_equal_excel` (Excel), `::test_unsupported_function_is_named_before_the_tie_out`.
- The hook rules are unit-tested (`test_hooks.py`, `test_v2_contract_hooks.py`, `test_v3_contract.py`).
- Landing an export with a source total (v5: `land --force --control <t>.<column>=<source total>` lands an export
  with more rows and one with fewer rows, each with a changed total, and records the total for that snapshot; a
  workbook copy that stands in for a download then passes `check --strict`; a wrong total gives `L-TOTAL` and
  nothing is written; a later build under the same agreement takes the recorded total):
  `test_v5_land.py::test_export_with_more_rows_and_a_new_total_lands_and_checks`,
  `test_v5_land.py::test_export_with_fewer_rows_and_a_new_total_lands_and_checks`,
  `test_v5_land.py::test_wrong_control_is_l_total_and_writes_nothing`,
  `test_v5_land.py::test_later_build_takes_the_recorded_total`.
- The check recomputation rule (v5: a check result replaced by `=0` while its operands differ is `C-RECOMPUTE` in
  `check --strict` and `audit --strict`; the six shipped models give none):
  `test_v5_check.py::test_result_replaced_by_zero_is_c_recompute_in_check` (Excel),
  `test_v5_check.py::test_result_replaced_by_zero_is_c_recompute_in_audit`,
  `test_v5_check.py::test_shipped_models_give_no_c_recompute`.
- The query-text comparison at refresh (v5: `check --refresh` is refused before Excel starts when
  `queries/<t>.pq.m` differs from the query text attached to the workbook; after `land --power-query` the refresh
  runs): `test_v5_check.py::test_refresh_is_refused_when_the_query_file_differs_from_the_attached_text` (Excel),
  `test_v5_check.py::test_refresh_refusal_for_a_changed_query_file_without_excel`.
- The strict audit after helper rows below a shorter extract are cleared by hand (v6: on a copy of a built workbook
  changed with openpyxl, with fewer raw rows or none and the helper rows below the last raw row cleared,
  `check --strict` and `audit --strict` report no `H-GAP`, `H-COVERAGE` or `H-BEYOND`; a helper cell emptied above
  the last raw row is `H-GAP`; helper formulas left below it are `H-BEYOND`; a model refreshed through Power Query
  and extended audits in strict mode with exit 0):
  `test_v6_audit.py::test_shorter_extract_repair_passes_check_and_audit`,
  `test_v6_audit.py::test_zero_row_repair_passes_check_and_audit`,
  `test_v6_audit.py::test_helper_cell_emptied_inside_the_boundary_is_h_gap`,
  `test_v6_audit.py::test_helper_formulas_below_the_last_raw_row_are_h_beyond`,
  `test_v6_audit.py::test_refresh_and_extend_cycle_audits_strict` (Excel).
- The query read back at refresh (v6: the attach records the hash of the query formula Excel gives back
  (`query_formula_sha256`); a query changed through Excel without `rotm.py` makes `check --refresh` report
  `X-QUERY-CHANGED`, refresh no table and accept nothing; an unchanged query refreshes as before; a manifest without
  the hash gets a note): `test_v6_check.py::test_attach_records_the_formula_hash` (Excel),
  `test_v6_check.py::test_query_changed_in_excel_is_found_at_refresh` (Excel),
  `test_v6_check.py::test_unchanged_query_refresh_cycle_exits_0` (Excel),
  `test_v6_check.py::test_manifest_without_formula_hash_gets_a_note`.
- The argument-count rule (v6: `F-ARGS` for a listed function with too few or too many arguments, at the counts of
  `skills/rule-of-thumb-model/references/model-spec-format.md` "Argument counts"; build refuses it with the JSON
  path; the audit reports it at the cell; the shipped workbooks give none):
  `test_v6_formula.py::test_if_with_four_arguments_is_f_args`,
  `test_v6_formula.py::test_listed_functions_pass_at_their_smallest_and_largest_count`,
  `test_v6_formula.py::test_build_refuses_if_with_four_arguments_at_its_json_path`,
  `test_v6_formula.py::test_audit_strict_reports_f_args_at_the_cell`,
  `test_v6_formula.py::test_shipped_and_foreign_formulas_give_no_f_args`.
- The number-format comparison (v6: one function compares number formats for audit `S-NUMFMT` and for theme; a
  percent sign in quotes differs from a bare one; a date format whose hyphens Excel saved with a backslash before
  each equals the plain one; `theme --number-formats --strict` after an Excel save leaves no `T-RESIDUAL`):
  `test_v6_audit.py::test_quoted_percent_sign_is_s_numfmt`,
  `test_v6_audit.py::test_escaped_hyphens_in_a_date_format_are_equal`,
  `test_v6_audit.py::test_theme_number_formats_after_an_excel_save_leaves_no_residual` (Excel),
  `test_v6_audit.py::test_shipped_models_audit_strict`.
- Landing a CSV by its source headers (v7): a CSV whose header holds the sample headers the contract recorded
  (`Order ID,Order Date,Net Sales`) lands as it is, and `raw/<t>.csv` gets the destination names in the contract's
  order; a missing, extra or repeated header is `L-COLUMNS` and nothing is written; plain headers and contracts
  without `csv_header` behave as in v6; the shipped contracts keep their hash and snapshot, and each `.md` is the
  text rendered from its JSON:
  `test_v7_contract.py::test_csv_with_spaced_headers_lands_by_its_sample_headers`,
  `test_v7_contract.py::test_sample_header_landing_refuses_a_missing_extra_or_repeated_header`,
  `test_v7_contract.py::test_plain_headers_land_as_before`,
  `test_v7_contract.py::test_contract_without_csv_header_refuses_a_differing_header`,
  `test_v7_contract.py::test_shipped_contracts_keep_their_hash_md_and_snapshot`.
- The ar_aging over-90 measure (v7): with the last bucket changed to start at 121 days, the "Over 90 days" total
  still equals the open amounts more than 90 days past due computed from the CSV, and `build --strict` and
  `check --strict` (Excel) exit 0; the shipped bucket lists give the shipped value; the cutoff input is fixed at
  90 days: `test_v7_templates.py::test_ar_aging_over90_follows_the_cutoff_when_the_last_bucket_changes` (Excel),
  `test_v7_templates.py::test_ar_aging_shipped_lists_give_the_shipped_over90` (Excel),
  `test_v7_templates.py::test_ar_aging_cutoff_input_is_fixed_at_90`.
- The hooks on a bare folder name (v7): a model folder named by a bare folder name, by `./name` or by its full
  path gets the same contract gate, audit on write and finish check; a bare word that names no model folder gives
  no hook output: `test_v7_hooks.py::test_agreement_guard_refuses_an_untyped_reply`,
  `test_v7_hooks.py::test_audit_on_write_reports_after_a_build`,
  `test_v7_hooks.py::test_finish_check_refuses_once_for_an_unverified_workbook`,
  `test_v7_hooks.py::test_a_bare_word_that_is_no_model_folder_gives_no_output`.
- The kept copy of a workbook with a live query (v7): a rebuild whose query source cannot be read keeps the earlier
  workbook with its query table and its manifest as `.bak_` copies, which `W-PQ-NOT-ATTACHED` names; a successful
  rebuild leaves no copy; a rebuild without Excel keeps them; the hooks do not act on them:
  `test_v7_build.py::test_failed_attach_keeps_the_earlier_workbook_and_manifest` (Excel),
  `test_v7_build.py::test_successful_rebuild_leaves_no_kept_copy` (Excel),
  `test_v7_build.py::test_rebuild_without_excel_keeps_the_copies` (Excel for the set-up),
  `test_v7_build.py::test_hooks_do_not_act_on_the_kept_copies`.
- The monthly roll check of rolling_forecast (v8): the shipped inputs hold every check, `closed_months_landed`
  included; with the inputs rolled to 4 or to 8 actual months and the landed rows unchanged, `check --strict` (Excel)
  exits 1 and `closed_months_landed` is the only failing error check; with rows for the closed month landed by the
  commands of the template's README section "The monthly roll", `build --strict` and `check --strict` exit 0 and the
  month's actual total equals the sum computed from the CSV; with `closed_months_no_sales` set to 1 and no new rows,
  `check --strict` exits 0: `test_v8_templates.py::test_rolling_forecast_shipped_inputs_hold_every_check` (Excel),
  `test_v8_templates.py::test_rolling_forecast_roll_without_new_rows_fails_the_landed_months_check` (Excel),
  `test_v8_templates.py::test_rolling_forecast_roll_with_new_rows_holds_and_shows_the_closed_month` (Excel),
  `test_v8_templates.py::test_rolling_forecast_closed_month_without_sales_passes_with_the_input_set` (Excel).
- Records of only delimiters and open quotes in a CSV (v8): a record that holds only delimiters is refused by
  `land --csv` with its line and nothing is written; a blank line is skipped as before; a quoted field still open at
  the end of the file is refused with the line where it starts; a CSV in the form Excel saves lands; the six shipped
  folders land with the same digests: `test_v8_land.py::test_record_of_only_delimiters_is_refused_with_its_line`,
  `test_v8_land.py::test_blank_line_is_skipped_as_before`,
  `test_v8_land.py::test_open_quote_at_the_end_is_refused_with_its_line`,
  `test_v8_land.py::test_csv_in_excel_form_lands`,
  `test_v8_land.py::test_shipped_folders_land_with_the_same_digests`.
- Saved inputs outside their valid range (v8): a display scale saved as 1 in a copy of the worked example makes
  `check --strict` and `audit --strict` exit 1 with `V-INPUT-RANGE`, which names the cell, the value and the range;
  the six shipped models give none: `test_v8_check.py::test_saved_scale_outside_its_range_fails_check_and_audit`
  (Excel), `test_v8_check.py::test_shipped_models_give_no_input_range_finding`.
- A query attached by hand that reads another query (v8): with Excel, a rebuild carries the staging query that the
  hand query reads as a connection-only query (its M text in `queries/<name>.hand.pq.m`, its name in the report), and
  after a change of the source a refresh reaches an output cell; a query attached by hand that holds all its steps
  itself is carried over as in v7: `test_v8_build.py::test_rebuild_carries_a_staging_query_a_hand_query_reads`
  (Excel), `test_v8_build.py::test_self_contained_hand_query_is_carried_as_in_v7` (Excel).
- An accepted control total bound to its agreement (v9): after the contract is agreed again with another total, a
  total recorded earlier for the snapshot (by `land --control` or by `check --accept-refresh --control`) is not
  used: landing the same extract without `--control` reports `L-TOTAL` with both totals and writes nothing, the
  rebuilt total check expects the new contract's total and `check --strict` does not exit 0; `land --force
  --control` with the rows' true sum records the new agreement's hash and the model passes again; an entry made
  under the agreement in force still applies; an entry written before v9 applies only when it is not earlier than
  the agreement; `contract show` and `contract agree` with unchanged content keep the entry:
  `test_v9_controls.py::test_land_control_record_is_not_used_after_a_new_agreement`,
  `::test_acceptance_record_is_not_used_after_a_new_agreement` (Excel),
  `::test_build_and_check_use_the_new_contract_total_after_a_new_agreement` (Excel),
  `::test_land_force_control_after_a_new_agreement_records_the_new_hash` (Excel),
  `::test_entry_under_the_agreement_in_force_still_applies`,
  `::test_entry_without_hash_applies_only_when_not_earlier_than_the_agreement`,
  `::test_show_and_agree_with_unchanged_content_keep_the_entry`.
- The self-mismatch finding for labels that hold ~* or ~? (v9): categories that hold `~*` and `~?`, each with
  amounts that add to zero over the months, make `build --strict` and `check --strict` (Excel) give `D-CRITERIA`
  (reason `self_mismatch`) with the label and its first raw row and not exit 0; the helper keys that replace `~`,
  `*` and `?` by fixed texts on both sides pass and give the sums computed in Python; a label with `~` and no
  wildcard, ordinary labels and the shipped models give none:
  `test_v9_criteria.py::test_tilde_wildcard_labels_netting_to_zero_fail_build_and_check` (Excel),
  `::test_helper_keys_for_tilde_labels_pass_and_equal_python` (Excel),
  `::test_tilde_without_wildcard_ordinary_labels_and_shipped_models_give_no_self_mismatch`.
- The refusal of --control for a column without a contract total (v9): `land --csv --force --control` and
  `check --accept-refresh --control` for a column whose contract names no control total exit 2 and write or record
  nothing; with the total in the agreed contract the same landing passes and build writes the check
  `<t>_total_<column>`: `test_v9_land.py::test_control_without_a_contract_total_is_refused_by_land`,
  `::test_control_without_a_contract_total_is_refused_by_accept_refresh`,
  `::test_contracted_total_lands_and_build_writes_its_check`.
- A pass made stale by a later landing in the finish check (v9): a landing or a new agreement after a passing audit
  and check makes the finish check refuse the stop once, naming the table and the command that brings the workbook
  up to date; after the rebuild, its audit and its check the stop goes through; a session without a landing after
  the pass behaves as in v8:
  `test_v9_hooks.py::test_landing_after_the_pass_is_refused_once_naming_the_table_and_the_rebuild` (Excel),
  `::test_contract_agreed_again_after_the_pass_makes_it_stale`,
  `::test_session_without_a_landing_after_the_pass_behaves_as_in_v8`.

### Not verified

- Google Sheets and Connected Sheets (no Google account was available): compatibility is checked by rule only. The
  Google Sheets procedures (the first extract before the build, the repair of the helper rows after a refresh to
  more, fewer or zero rows, the export route for a refresh made in Google Sheets, with `land --control` for a changed source total) were not
  run in Google Sheets; the export route was tested only without Google Sheets (`test_v4_docs.py`, `test_v5_land.py`).
  The repair of the helper rows after a refresh to fewer or zero rows was tested with a local stand-in (a copy of a
  built workbook changed with openpyxl), not in Google Sheets: after it, `check --strict` and `audit --strict` of
  the copy pass (`test_v6_audit.py::test_shorter_extract_repair_passes_check_and_audit`,
  `test_v6_audit.py::test_zero_row_repair_passes_check_and_audit`; with Excel,
  `test_v6_integrator.py::test_repaired_download_passes_check_with_excel`).
- The redirect of the formulas with Find and replace in Google Sheets
  (`skills/rule-of-thumb-model/references/google-sheets.md`, "Cutover when the extract is on a new sheet"): not run
  in Google Sheets.
- A model that uses a function only Google Sheets has: it is audited and not recalculated (`X-SHEETS-ONLY`); no
  Google Sheets session calculated it.
- The GoogleSQL that `land` writes to `queries/<t>.sql`, quoted identifiers included, was not run against BigQuery.
- A Power Query refresh against a database (only against a local file), and the `--pq-source database` template (it
  quotes column names for its connector; no database was reached).
- R output of `lift` (not written: no R was installed).
- The agreement guard of the contract gate in sessions that keep no transcript
  (`claude -p --no-session-persistence`): there it lets `contract agree` through. Its transcript rules are
  unit-tested only.
- The finish check's notice on a stop it lets through: seen in one live `--plugin-dir` session on the v2 files; not
  repeated on a later version.
- Details, and the other limits: `skills/rule-of-thumb-model/SKILL.md`, "What is and is not verified".

## Choices left to Max

Each is a default with its source; none of the alternatives is wrong. Where the research offered two conventions,
the other one is a named override.

| # | Choice | Default | Alternatives (named overrides or settings) | Sources |
| --- | --- | --- | --- | --- |
| 1 | input cells | black text on pale yellow FFF2CC | `input_blue_font` (blue 0000FF, no fill); `input_blue_on_fill` (blue on FFF2CC) | fill: Max's own files, ICAEW Code; blue font: books, CFI (design 10.3); the research synthesis v2 recommends blue font (D16) |
| 2 | formula length | 100 characters, a warning | `length_limit` | the plugin's own number; FAST 3.03-01 gives none |
| 3 | spare helper rows | 0: growth needs `extend` (the master check shows FAIL until then); a helper capacity above the landed rows (`--capacity`, the contract's `refresh.capacity_rows`, `helper_spare_rows` above 0) is refused by build, because helper rows below the last raw row read empty cells as 0 and feed the calculations; after a refresh to fewer rows the check `<t>_beyond` fails (`H-BEYOND`) until `extend` clears those rows | none | the plugin's own choice; v2 after the review (A-aera-6) |
| 4 | typed numbers allowed in formulas | 0 and 1 | `allowed_literals` | stricter than FAST 3.04-01; PwC allows 1, 0, -1, 12, 4 |
| 5 | check tolerance | 0.001 in the unit of the checked values | `check_tolerance` | the plugin's own choice; the synthesis computed differences of 0.012 to 0.23 between summation orders of 50,000 two-decimal amounts near 2e13 and suggests 1 currency unit (not re-checked) |
| 6 | the two greys | column headers D9D9D9, row headers F2F2F2 | dictionary `colors` | Max named grey, no hex; survey and researcher 2 |
| 7 | unit column fill | F2F2F2 (the row-header band continues) | `unit_unfilled` | the synthesis v2 recommends unfilled (D18) |
| 8 | total row rule | thin black 000000 | `total_grey_rule` (808080) | the synthesis v2 recommends grey (D8) |
| 9 | check display | `OK` / red `FAIL` by number format (alert rows: `OK` / red `ALERT`, never `FAIL`), no conditional format | none yet | design 9.5; the synthesis v2 recommends a conditional format (D7) |
| 10 | links between sheets | black, like calculations | `links_green`, `fast_import_export` | survey (75.8% of links black); Max's prior work 38.9% green; FAST blue imports |
| 11 | templates | budget_vs_actual, rolling_forecast, price_volume_mix, ar_aging (+ starter, worked example sales_vs_target) | the synthesis v2 also proposes three_statement_from_tb and cohort_unit_economics | design 17; synthesis D12 |
| 12 | state folder | `%LOCALAPPDATA%/rule-of-thumb-model/` | `ROTM_STATE_DIR` | one fixed path per Windows user for the Excel lock |
| 13 | what the SQL may do | shape only: select, rename, cast, filter, join and aggregate to the contract's grain, remove duplicate keys when the user agreed it | | the synthesis's reading of Max's words (D14); whether joins and aggregation count as shaping is for Max |
| 14 | defective raw rows (a repeated key, empty cells in a never-null column) | landed and agreed as known exceptions; the check holds while the count is at most the agreed one; `contract propose` proposes them when the key given with `--key` repeats in the sample, or when a column is empty in at most 1% of 100 or more sample rows | remove them in the SQL (when the user agrees), or `null_rule` `allowed` (no null check) | the plugin's own rule after live test 1 (2026-11-25); the 1% and 100-row thresholds are the plugin's numbers |
| 15 | how raw number columns show | the contract column's `format`, which `contract propose` writes for each integer and number column from the sample (whole numbers: the type's default; decimals: `amount2`; values mostly below 1: `factor` or `general`); the user agrees it with the contract. Without a `format`: two decimals (`amount2`) for a `number` column whose landed values hold a fraction, General when a value has more than two decimals, `amount` (no decimals) for whole numbers | `format` per contract column; `contract_type_formats` in `style_overrides.json` | BOOKS B-44 (two decimals as a general rule); the fraction rule and the sample rule are the plugin's own, after live test 1 and the review (J-4) |
| 16 | how keys are compared | exactly: text equal only when every character is equal, upper and lower case included; `*`, `?` and `~` as plain characters; text never read as a number; the same in land, check, contract propose, the workbook's key check (EXACT) and lift | none | the review's rule (A-aera-9): one definition of key equality. SUMIFS, COUNTIFS and MATCH in a model's own blocks still do not tell `T1` from `t1` |
| 17 | font size of a sheet | by the sheet's role: 8 on raw, helper and calculation sheets, 9 on the others; a sheet entry may set `"font_size": 9` (a calculation sheet with white space) or `8`; build prints the information note `I-SPARSE-SHEET` for a calculation sheet that takes size 8 from its role and holds fewer than 100 populated cells | a size chosen automatically from the sheet's measured density (not built) | Max (spec 1.2): 8 for "a brick wall of data", 9 where "there are some white spaces"; the 100-cell threshold is the plugin's own number (`rules.sparse_sheet_cells` in the style dictionary); the synthesis lists the size by density as a choice for Max (D18) |

Open facts: the FAST sub-number of the own-sheet-prefix rule (cited as FAST 3.04-02 to 3.04-07) is not confirmed.

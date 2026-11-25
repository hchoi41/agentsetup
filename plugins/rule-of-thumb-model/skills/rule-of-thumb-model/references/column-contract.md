# The column contract: agree the raw columns before any SQL

Max: "it'd be best to coordinate and agree with the user, what columns will be pulled into in which shapes (here shapes mean the destination columns that we want to create using Connected Sheet or PowerQuery)" (Max, spec 1.2). He accepted a contract command and a contract gate that refuses SQL for a raw table until its contract is agreed (Max, spec 1.7).

This file is step P1 of the procedure in [SKILL.md](../SKILL.md). It says what a contract holds, how to propose it, how to record the user's agreement, what the SQL or Power Query may then do, and what the plugin checks against the contract later. Read it before you write any SQL or Power Query text, and whenever the user wants a raw table changed.

## Evidence labels

- "confirmed" and "not confirmed": as in [fast-standard-rules.md](fast-standard-rules.md#evidence-labels). "local guides L-77" is item L-77 of the local guides research report; "researcher 2 section 4" is section 4 of researcher 2's report.
- "design n": section n of the plugin's design (`rotm_design_v1.md`, kept in the project's build folder, not shipped). It describes what the plugin does.
- "Max, spec 1.x": Max's own words, quoted from Part 1 of the job's specification.

## What a contract is

- One contract per raw table, in the model folder: `contracts/<table>.contract.json` (the only source) and `contracts/<table>.contract.md` (a readable copy for the user, generated from the JSON) (design 5).
- The contract fixes the destination columns that the SQL or Power Query must deliver, in order, with their types, units and null rules; the grain of a row; the key; where the data comes from; and the expected row count or control totals.
- Nothing is landed, and no SQL file for the table is written, until the user has agreed to the contract. The contract gate hook refuses the SQL (design 13.3); `rotm.py land` refuses the data (design 15).

## The fields

| Field | What to write | Rule |
| --- | --- | --- |
| `table` | the table's short name, for example `sales` | lower case letter first, then lower case letters, digits or `_`; at most 20 characters; at most 12 advised: the name is part of the sheet names `raw_<table>` and `h_<table>` and of every formula that reads them, and counts toward the 100-character formula length limit, so `contract propose` prints a note for a longer name |
| `raw_sheet` | `raw_<table>` | always this name |
| `excel_table` | `tbl_raw_<table>` | always this name |
| `purpose` | one sentence: what the model uses this table for | |
| `source` | `connector` (`power_query`, `connected_sheets`, `csv` or `other`), `system`, `object`, `from`, `where` | the SQL is built from `from` and `where` |
| `grain` | what one row is, for example "one row per invoice line" | required |
| `key` | the list of columns that identify one row | at least one column; every name must be a column of the contract |
| `columns` | one entry per destination column, in the order the query delivers them | see the next table |
| `expect.rows` | `{"exact": N}` or `{"min": N, "max": M}` | `expect` needs `rows`, or at least one entry in `totals`, or both |
| `expect.totals` | `[{"column": ..., "value": ..., "tolerance": ..., "note": ...}]` | the column must be `integer` or `number`; the value comes from the source side |
| `expect.key_repeats` | `{"max": N, "note": "..."}` or absent | a known exception: at most N rows repeat an earlier key and are kept (see "Known exceptions") |
| `refresh` | `frequency`, `capacity_rows`, `allow_empty` | `capacity_rows` may set fewer helper rows than the landed rows (a warning, W-CAPACITY); more is refused by `build`, because a helper row below the last raw row reads empty raw cells as 0 |
| `version` | written by `contract propose`: 1, then one more on each `--force` | the agreement records the version it was given for |
| `agreement` | status `agreed` is written only by `rotm.py contract agree`; `contract propose` writes status `proposed`; `contract show` writes only its `shown_sha256` | see "The agreement record" |

Each column entry:

| Field | What to write | Rule |
| --- | --- | --- |
| `name` | the destination column name | starts with a letter or `_`; then letters, digits and `_` (Korean letters are allowed); at most 41 characters; no spaces; unique without regard to case; not the name of a helper column `build` adds (see the bullet "Names of helper columns" below) |
| `type` | `text`, `integer`, `number`, `date`, `datetime` or `boolean` | an identifier with leading zeros is `text` |
| `unit` | for example `KRW`, `count`, `date`, or null | every number carries its unit |
| `null_rule` | `never` or `allowed` | the first column must be `never` |
| `known_nulls` | `{"max": N, "note": "..."}` or absent | a known exception of a `never` column that is neither the first column nor a key column: at most N empty cells are kept (see "Known exceptions") |
| `allowed` | a list of allowed values, or null | `contract propose` fills it when a sample column has 12 or fewer distinct values |
| `source_expression` | one SQL expression that produces the column, for example `CAST(i.invoice_id AS STRING)` | used to write the SELECT list; no comma or `AS` outside brackets and no prose (put prose in `description`). `land` writes `NULL` and a comment for an expression that is not one SQL expression, with warning `L-SQL-EXPR`. `contract propose` writes the sample's header here as a GoogleSQL identifier (see the bullet "Source expressions from headers" below); an expression you write is used as written |
| `format` | a number-format key of the style dictionary (`number_formats` in `style_dictionary.json`), for example `amount2` | how build shows the column on the raw sheet and in its fetch column. `contract propose` writes it for each number column from the sample (see "The format key"). Without it, a `number` column whose landed values hold a fraction shows two decimals (`amount2`), or General when a value has more than two |
| `description` | one sentence | |

- Why the first column must never be null: the raw sheet's row count is `COUNTA` of column A minus the header, and the helper sheet's `in_raw` flag is 1 when column A of raw row r is not empty (design 7.1). An empty first cell would hide a whole raw row.
- Why totals come from the source side: "a total calculated twice from the landed cells is only an internal tie" (researcher 2 section 4, not confirmed). A check compares two independent paths (books B-32, confirmed). Ask the user for the source system's own row count or total (for example from its report or a separate count query), and write that number.
- Keep null apart from zero: a missing amount is not 0 (local guides L-83, not confirmed). Write `null_rule: "allowed"` for a column that may be empty, and say in its `description` what an empty value means.
- Source expressions from headers: `contract propose` writes each sample header (or each `--columns` name) as the column's `source_expression`. A header that matches `^[A-Za-z_][A-Za-z0-9_]*$` and is not a GoogleSQL reserved keyword stays bare (`amount`). Any other header is written in backticks, as a GoogleSQL quoted identifier: `` `order` `` for the reserved keyword `order`, `` `Order Date` `` for a header with a space. The `.md` shows the expression as the JSON holds it. `queries/<t>.sql` then reads `` `order` AS `order` `` and `` `Order Date` AS order_date ``; a column whose source expression is its own plain name is written as the name alone (`amount`). In the SQL inside a database Power Query (`land --pq-source database`), a source expression that is exactly one name in backticks gets that connector's quotes, like the column names: `[order] AS [order]` for SQL Server, `"order" AS "order"` for PostgreSQL. A source expression you write yourself is used as written, in both texts. The list of reserved keywords is the plugin's copy of the GoogleSQL lexical structure page (reserved keywords); it was not checked again for this version.
- Names of helper columns: the helper sheet `h_<t>` holds the columns `row`, `in_raw`, `dup_key` and `dup` that `build` adds, the fetched raw columns under their contract names, and a null flag `<c>_null` for each fetched `number`, `integer`, `date` or `datetime` column `<c>` that may be empty. A contract column with one of these names (`row`, `in_raw`, `dup_key`, `dup`, or `<c>_null` of another column `<c>` that gets a null flag) would give the helper sheet two columns with that name, and the generated checks could read the wrong one. `contract propose` prints a note for such a column ("has the name of a helper column build adds"), with the suggested destination name `<name>_src`, and lists it under "Points to confirm". `build` refuses it (exit 2, nothing written): rename the destination column in `contracts/<t>.contract.json` (keep its source expression), run `contract show`, and get the contract agreed again.

### The format key

`contract propose` reads the sample and writes `format` for each `integer` and `number` column:

| Sample values | `format` written | Shown as |
| --- | --- | --- |
| whole numbers only | the type's default key (`count` for `integer`, `amount` for `number`; `contract_type_formats` in `style_dictionary.json`) | `1,234` |
| values with decimals | `amount2` | `1,234.25` |
| values mostly below 1 in size: more than half of the values that are not 0 are below 1 in size | the number format with the fewest decimals that is at least 2 and shows the values in full: `amount2` for 2 decimals, `factor` for 3 or 4; `general` for 5 or more | `0.0123` (`factor`) |

- The contract's `.md` shows the key in its column table, column `Format` (`default` for a column without the key, meaning the format of the column's type), and lists the proposed keys under "Points to confirm". The user may ask for another key; it is part of the contract they agree.
- No key is added to the dictionary. Without a sample (`--columns`), no `format` is written.
- Change it like any other field: on a proposed contract edit the JSON; on an agreed one propose a new version.

## Known exceptions: landing data that breaks the contract

Raw data is often not clean: a transaction delivered twice, one row without a region. The rule is "land every delivered row, drop nothing silently, keep the check" (Max: the raw data is loaded on the sheet and the analysis is done with formulas, spec 1.2; P5: the landed data is checked against its contract). A contract records each defect the user knows about and accepts as a known exception:

| Defect | Contract entry | What land and check do | The workbook check |
| --- | --- | --- | --- |
| rows that repeat an earlier key | `expect.key_repeats: {"max": N, "note": ...}` | land the rows when at most N rows repeat an earlier key (info `L-KNOWN`); more is error `L-KEY` / `D-KEY` | `<t>_keys` counts the rows that repeat an earlier key and holds while the count is at most N (input cell `<t>_key_repeats`) |
| empty cells in a never-null column | `columns[i].known_nulls: {"max": N, "note": ...}` | land the rows when at most N cells are empty (info `L-KNOWN`); more is error `L-NULL` / `D-NULL` | `<t>_nulls_<col>` counts the empty cells and holds while the count is at most N (input cell `<t>_known_nulls_<col>`) |

- `contract propose` writes both from the sample, and says so under "Points to confirm": `expect.key_repeats` when the key you gave with `--key` repeats in the sample; `known_nulls` when a column is empty in at most 1% of 100 or more sample rows (the plugin's threshold). Set them yourself with `--key-repeats N` and `--known-nulls <column>=N`.
- Keys are compared exactly, and the same way in `land`, `check`, `contract propose`, the workbook's key check and `lift`: two text values are the same key only when every character is the same, upper and lower case included (`T1` and `t1` are two keys); `*`, `?` and `~` are plain characters, not wildcards; a text value is never read as a number (`001` and `1` are two keys, and so are two 20-digit identifiers that share their first 15 digits); numbers, dates and booleans are the same key when their values are equal.
- Show the user the defects and the known exceptions in the contract, and get their agreement like any other part of the contract. The row the source repeats stays on the raw sheet; whether a model counts it is a helper column and an input switch, not a deletion.
- Do not invent a key (for example a row number added to the extract) or set `null_rule` to `allowed` to get defective rows landed: the key check then always shows 0, the null check disappears, and the master check shows OK with the defects unreported (live test 1, 2026-11-25).
- When a refresh brings more defects than the contract agrees, the check fails and names the rows. Fix the source, or propose the contract again with the new count and ask the user again.

## When land refuses an extract

`rotm.py land` refuses an extract that breaks the agreed contract and writes nothing (`L-KEY`, `L-NULL`, `L-ROWS`, `L-TYPE`, `L-ALLOWED` and the other refusals). Each refusal line names the rows: the line numbers of the extract and, for a key, the key values.

1. Report to the user each refusal line as printed, with the rows it names. Then stop: land nothing for that table, write no SQL change, build nothing on it.
2. Do not get past the refusal yourself. Never add a made-up key column (a row number added to the extract), set a column's null rule to `allowed`, widen `expect.rows`, change a control total, or delete rows from the extract.
3. A change of the key, a null rule, the row count or a control total is a contract change. It is made only when the user asks for it: `rotm.py contract propose "<model>" --table <t> --force` with the new settings, then the new `.md` is shown to the user (its "Changes since version N" lists the change), and the reply the user gives to that version is recorded in their new words. The contract is agreed again, or the extract is not landed. A reply recorded for an earlier version is not an agreement of the changed contract.
4. If the user accepts the defect, the new version records it as a known exception (see "Known exceptions"): `--key-repeats N` for rows that repeat an earlier key, `--known-nulls <column>=N` for empty cells in a never-null column. `land` then lands the rows and reports info `L-KNOWN`, which names the lines. In the workbook the checks `<t>_keys` and `<t>_nulls_<col>` count the rows: their actual value is the count, and they hold while it is at most N. The defect stays visible on the checks sheet.
5. A defect with no known-exception entry (rows outside the agreed range, a control total that differs, a value outside the allowed list, a value of the wrong type) is corrected at the source, or the user agrees a changed contract (step 3) and the specification gets an alert (`"severity": "alert"`, [checks.md](checks.md)) that counts the affected rows.

## Procedure

1. Create the model folder: `rotm.py init "<model>" --name <name>`. This writes the marker `rotm_model.json`, which the hooks use to recognise a model folder (design 4.1).
2. Learn the source's columns. Ask the user for a sample export (a CSV of a few hundred rows) or the table's schema. If you must write a discovery query, keep its file outside the model folder: inside it, the contract gate refuses every `.sql` and `.m` file except `queries/<table>.sql` and `queries/<table>.pq.m` after agreement (design 13.3).
3. Propose the contract: `rotm.py contract propose "<model>" --table <t> --csv <sample.csv> --key <a,b> --grain "<one row per ...>" --source "<system and object>" --rows <MIN:MAX>`. It infers types from the sample (integer, number, ISO date `YYYY-MM-DD`, datetime, boolean, else text; identifiers with leading zeros stay text). A date and time written with a space or a `T` between date and time, also with a `Z` or a UTC offset such as `+09:00` (`2026-01-05T09:30:00+09:00`), is proposed as datetime; `land` lands it with the offset dropped and warns L-PRECISION. A text that `land` cannot read as a date and time is not proposed as datetime, counts nulls, lists allowed values when a column has 12 or fewer distinct values, tests the key for uniqueness, and writes the JSON with status `proposed` and the `.md` (design 15). Without a sample, use `--columns a,b,c` and fill in the types by hand.
4. Edit the proposed JSON where the sample could not know the answer: units, null rules, allowed values, source expressions, the control total from the source side. A proposed contract may be edited; an agreed one may not. After every edit of the JSON run `rotm.py contract show "<model>" --table <t>`: it writes `contracts/<t>.contract.md` again from the JSON and records `agreement.shown_sha256`.
5. Show the user `contracts/<t>.contract.md` in full, unchanged, as the last `contract propose` or `contract show` wrote it. Never show an `.md` written before the last edit of the JSON. It ends with the line "Reply 'agreed' or say what to change." Then stop and wait for the reply. Do not write SQL, do not land data, do not build.
6. If the user asks for changes, make them in the JSON, run `rotm.py contract show "<model>" --table <t>` to print the new `.md`, show it again, and wait again. `contract agree` refuses (exit 2, nothing recorded) when the `.md` is missing, when it differs from the text rendered from the current JSON, or when `agreement.shown_sha256` differs from the current content hash: the contract changed after it was last shown. Then run `contract show`, show the user the `.md` again and record their new reply. A contract proposed before v3 has no `agreement.shown_sha256`; its `.md` is compared with the rendered text only. The last line of the `.md` (it says whether and by whom the contract is agreed) is not compared: `rotm.py init --template` rewrites that line alone when it sets a copied contract back to proposed.
7. When the user agrees, record it with their own words: `rotm.py contract agree "<model>" --table <t> --reply "<the user's reply, word for word>" --by <their name>`. A reply that agrees some tables and asks for a change in another is recorded only for the tables it agrees without change, with `--reply` set to the user's sentence or sentences about those tables, copied exactly; the other table waits for the change, `contract show` and a new reply. When the plugin's hooks are active, the contract gate checks that the reply text occurs in a message from the user in this session, and refuses the command if it does not (design 13.3). The reply must also come after the latest `contract propose` of that table and after the latest edit of `contracts/<t>.contract.json` in this session; one reply given after several proposals counts for each of those tables. A skill folder copied without the plugin has no hooks; the rule still applies to you.
8. Only now write the SQL. `rotm.py land "<model>" --table <t> --csv <file>` checks a data file against the contract and writes `raw/<t>.csv`, `queries/<t>.sql` and `queries/<t>.pq.m` (design 15). For the product steps after that, read [excel-power-query.md](excel-power-query.md) or [google-sheets.md](google-sheets.md).
9. Run `rotm.py contract status "<model>"` to see the state of every contract.

### When a table is not wanted

A table proposed or landed under a name that is not wanted (for example a name too long for short formulas) is removed with `rotm.py contract remove "<model>" --table <t>`. It refuses (exit 2) while `model.spec.json` has a sheet with `"table": "<t>"`. Otherwise it moves each existing file of `contracts/<t>.contract.json`, `contracts/<t>.contract.md`, `queries/<t>.*` (with their `.bak_` copies), `raw/<t>.csv` and `raw/<t>.controls.json` into `removed/<t>_<YYYYMMDD_HHMMSS>/` in the model folder and lists what it moved. Then propose the table again under the new name.

### When nobody can reply (a headless test or a task file)

- If the task file itself carries the user's agreement in words, record it with `--channel task-file --source-file <task.md>`. `contract agree` refuses unless the reply text occurs word for word in that file (design 5).
- If no agreement exists anywhere, stop after step 5 and report: "The contract for <t> is proposed, not agreed. Nothing was landed." Do not record an agreement for the user.

### When the contract must change after agreement

1. Run `rotm.py contract propose "<model>" --table <t> --force` with the new settings. This resets the agreement to `proposed`, gives the contract the next version number and keeps the earlier agreement, with the content it agreed, in `agreement.history` (design 5).
2. Show the user the new `.md` and wait for the reply. Its section "Changes since version N" lists what changed against the last agreed version.
3. Record the new agreement with `contract agree`. It records the version, prints the changes again, and when the reply is the same text as an earlier version's reply, records `reply_reused_from` and says so. In a session, record the user's words only when they gave them after seeing the new version; with a task file, say in the report that the new version was recorded with the task file's words and list the changes.
4. Land the data again and rebuild.

Do not edit an agreed contract JSON by hand. The contract gate refuses the edit (design 13.3), and an agreed contract whose content changed afterwards has state `stale`, which counts as not agreed: `build` and `land` refuse it (design 5).

## Contract states

| State | Meaning | What works |
| --- | --- | --- |
| `missing` | no contract file | nothing for this table: propose one |
| `invalid` | the file fails the schema; the message names the field and what to change | fix the field |
| `proposed` | written, no agreement yet | edit, run `contract show`, show the user, record the agreement |
| `agreed` | agreed, and the content has not changed since | write SQL, land, build |
| `stale` | agreed, then changed | treated as not agreed: propose again with `--force` |

The state is computed from the file: `agreed` needs `agreement.status` `agreed` and a `contract_sha256` that matches the contract without its `agreement` member (design 5).

## The agreement record

| Field | Content |
| --- | --- |
| `status` | `proposed` or `agreed` |
| `contract_sha256` | the hash of the contract without `agreement`, at the time of agreement |
| `table` | the table the reply was recorded for (written by `contract agree` from v2) |
| `reply` | the user's reply, word for word |
| `by` | who agreed |
| `at` | when, with the time zone |
| `channel` | `session` (the user replied in this session), `task-file` (the reply is in the named task file) or `example` (shipped examples and templates only) |
| `source_file` | the task file, for channel `task-file` |
| `recorded_by` | `rotm.py contract agree` |
| `version` | the contract version the agreement was given for |
| `reply_reused_from` | when the reply is the same text as the reply recorded for an earlier version: that version |
| `history` | earlier agreements, each with its version, hash and the agreed content (`agreed_content`) |
| `shown_sha256` | written by `contract propose`, `contract show` and `contract agree`: the content hash (the hash `contract_sha256` uses) of the JSON that `contracts/<table>.contract.md` was last written from; the agreement record is not part of the hashed content, so writing this field changes no contract hash. `contract agree` refuses when it differs from the current content hash (see "Procedure", step 6) |

- `contract agree` refuses an empty `--reply` or `--by`. The tool never writes `agreed` by itself (design 5).
- It also refuses a reply that reads as a refusal: a reply whose first word is negative (no, not, don't, 아니요, ...) or that holds a negated agreement anywhere (not agreed, have not agreed, don't agree, cannot agree, disagree, 동의하지 않, 동의 안). A reply that starts "No need to" is not a refusal by its first word.
- It refuses a reply that holds a word that refuses: reject, decline and refuse (also rejected, rejection, declined, refused, refusal); a negation before run, proceed, start, land, continue or go ahead ("Do not run the query yet."); and the Korean forms 거절, 반려, 거부 and 하지 마. The message ends "no agreement was given; wait for the user's reply". Nothing is recorded.
- It refuses a reply that postpones the decision: will review, review it later or tomorrow, get back to you, need to check or look first, not yet, let me check, hold off and hold on, please wait, wait until and wait for (not when negated: no need to wait is not a stop), and the Korean forms 나중에, 내일, 검토 후, 확인해 볼게, 아직, 보류 and 기다려. The reason starts "postpones the decision", and the message says "no agreement was given; wait for the user's reply". Nothing is recorded. Wait for the user's reply to `contracts/<t>.contract.md`, then record it. A reply that says the review is done is recorded: "Reviewed, agreed."; so is "No need to wait, go ahead.".
- It refuses a reply that asks for a change: an imperative or a request with change, rename, remove, drop, replace, switch, fix, correct, revise or modify (also with "please", "can you", "should" or "needs to"), and the Korean forms 바꿔, 변경해, 수정해 and 고쳐. A negated form is not a change request: "no changes", "nothing to change", "change nothing", "without changes". The reason starts "asks for a change"; make the change, run `contract show`, show the user the contract again and record their reply to the new version.
- On the task-file channel, the sentence of the task file that holds the reply must not read as a refusal, a change request or a postponement.
- No list of words is required in an agreement, and the reply need not name the table; the user's approval in their own words is recorded. The contract gate hook refuses the same replies.
- Channel `example` is accepted by `build` only inside the plugin's own folders; `rotm.py init --template` resets it to `proposed`, so a model made from a template asks the user again (design 5, 17).

## What the SQL or Power Query may do

The skill's rule: the query shapes the data to the contract and lands it on the raw sheet; every analysis step after that is a workbook formula (Max: "after using the Connected Sheet or PowerQuery, the data should be loaded on Sheet as Raw files. and then, all the analysis after that should be done using the formulas and operations within the workbook", spec 1.2).

Shaping, which the query may do:

1. Select the contract's columns, in the contract's order.
2. Rename them to the contract's names.
3. Cast them to the contract's types.
4. Filter the rows to the agreed scope (periods, entities, statuses), as written in `source.where`.
5. Join and aggregate only as far as the contract's grain needs, as written in `source.from` and the grain.
6. Remove duplicate rows by the contract's key rule, when the user agreed that; otherwise land them and agree them as known exceptions (see "Known exceptions"), so the workbook shows them.

Analysis, which the query must not do: rates, allocations, ratios, flags, period logic, scenarios, currency translation, the mapping of codes to report lines. These are helper columns and calculation blocks in the workbook ([helper-patterns.md](helper-patterns.md)).

- This line between shaping and analysis is the research synthesis's reading of Max's words (research synthesis D14, for Max to confirm). Whether joins and aggregation to the contracted grain count as shaping is listed as a question for Max.
- Other practice: some sources say to transform as far upstream as possible (the Smart Method book, books B-51, confirmed; Microsoft's query folding guidance, platforms B1.6, not confirmed). This skill does not follow that practice; Max's rule is the skill's rule. Details: [excel-power-query.md](excel-power-query.md#a-different-practice-transform-before-landing).
- Agree the shape before importing: only the fields the model needs, aggregated to the reporting grain, with a type for every field (local guides L-77, not confirmed). Query a database view or a prepared query, not whole tables (local guides L-78, not confirmed).

## What the plugin does with the contract later

- `build` lays out `raw_<t>` from A1 in the contract's column order, with an Excel table `tbl_raw_<t>`, and adds the contract's expected rows and totals to the "Contract controls" block of the inputs sheet (design 6.4, 7.1).
- `build` writes the checks `<t>_rows`, `<t>_total_<col>`, `<t>_nulls_<col>`, `<t>_cover`, `<t>_beyond` and `<t>_keys` ([checks.md](checks.md)); `<t>_keys` reads the helper column `dup`, which compares keys with EXACT (`=--(SUMPRODUCT(--EXACT(C$2:C2,C2))>1)*B2`, 1 on a later copy of a key); a known exception makes its check an `at_most` check against the agreed count.
- `audit` reports R-CONTRACT (error) when the raw sheet's headers differ from the contract's names or order (design 14).
- `check` tests the raw snapshot against the contract in Python: rows, key uniqueness, nulls, types, allowed values and control totals (design 15).
- `check --accept-refresh` tests new raw rows from a refresh against the contract, and only then records them as the new landed data (design 7.3).
- `check --accept-refresh --control <t>.<column>=<value>` takes the source system's new total for the refreshed rows. After the rows pass, it records the accepted total in `raw/<t>.controls.json`, beside the snapshot `raw/<t>.csv`: one entry per accepted snapshot (keyed by the snapshot's digest), with the rows, the totals with the contract's tolerance, the words given and the time. The contract and its agreement are not changed. Running the same acceptance again gives the same entry.
- A new extract with a new source total, landed from a file (for example the export of an extract refreshed in Google Sheets, [google-sheets.md](google-sheets.md#models-kept-in-google-sheets) step 4): `rotm.py land "<model>" --table <t> --csv <file> --force --control <t>.<column>=<source total>`. `land` checks the file by the rules `check --accept-refresh` applies to refreshed rows (columns, types, nulls, keys, the row count) and against the totals given. When it fits, `land` writes `raw/<t>.csv` and records the given totals for that snapshot's digest in `raw/<t>.controls.json`, with the words given, as an acceptance does. When a given total does not equal the file's sum, `land` reports L-TOTAL and writes nothing. `--control` needs `--csv`, and its table must be the table landed. Without `--control`, `land` compares the file with the control total in force.
- The control total `check` uses for a table and column, in this order: `--control` given in this run; else the total recorded for this exact snapshot in `raw/<t>.controls.json`; else the contract's `expect.totals`. A value on the inputs sheet is never a control total, and `check` never records a value read from the workbook as an accepted total; when the inputs-sheet control cell holds another value than the control used, `check` warns D-CONTROL-INPUT. For a snapshot that has an entry in `raw/<t>.controls.json`, `build` and `land` use the accepted totals too; so a rebuild after an accepted refresh keeps the accepted total on the inputs sheet.
- `build` records in each raw entry of the manifest `contract_sha256`, the hash of the agreed contract it read. When the contract is agreed again with other content, `check` and `audit` report `CT-VERSION` (error) until the data are landed again (if the rows change) and the model is rebuilt.
- `land` warns L-PRECISION, naming the column, the first line and the count, when a value lands differently from its text: a number with more than 15 significant digits (Excel and the snapshot keep 15); a datetime whose fraction of a second was cut (never rounded up, so no time moves into the next second, day or period); a datetime whose `Z` or UTC offset was dropped (the time is landed as written: `2026-01-05T09:30:00+09:00` lands as 2026-01-05 09:30:00). `contract propose` and `land` read a date and time with the same rule, so every column that propose classifies as datetime lands. Decide with the user: round the values in the source, or land the column as text (change its type in the contract and get the contract agreed again).

## Example

`contracts/sales.contract.json` as `contract agree` leaves it (design 5):

```json
{
  "rotm_contract": 1,
  "table": "sales",
  "raw_sheet": "raw_sales",
  "excel_table": "tbl_raw_sales",
  "purpose": "Invoice lines for the sales-versus-target model",
  "source": {"connector": "power_query", "system": "BigQuery", "object": "proj.ds.invoice_lines",
             "from": "proj.ds.invoice_lines l JOIN proj.ds.invoices i USING (invoice_id)",
             "where": "i.posted AND i.inv_date BETWEEN '2026-01-01' AND '2026-06-30'"},
  "grain": "one row per invoice line",
  "key": ["invoice_id", "line_no"],
  "columns": [
    {"name": "invoice_id", "type": "text", "unit": null, "null_rule": "never", "allowed": null,
     "source_expression": "CAST(i.invoice_id AS STRING)", "description": "invoice number, leading zeros kept"},
    {"name": "line_no", "type": "integer", "unit": "count", "null_rule": "never", "allowed": null, "source_expression": "l.line_no"},
    {"name": "inv_date", "type": "date", "unit": "date", "null_rule": "never", "allowed": null, "source_expression": "i.inv_date"},
    {"name": "region", "type": "text", "unit": null, "null_rule": "never", "allowed": ["East", "North", "South", "West"], "source_expression": "i.region"},
    {"name": "product", "type": "text", "unit": null, "null_rule": "allowed", "allowed": null, "source_expression": "l.product_code"},
    {"name": "amount", "type": "number", "unit": "KRW", "null_rule": "never", "allowed": null, "source_expression": "l.net_amount"}
  ],
  "expect": {"rows": {"min": 1, "max": 100000},
             "totals": [{"column": "amount", "value": 1234567890, "tolerance": 0}]},
  "refresh": {"frequency": "monthly", "capacity_rows": null, "allow_empty": false},
  "agreement": {"status": "agreed", "contract_sha256": "<sha256 of this object without 'agreement'>",
                "reply": "ok, agreed. amount is net of VAT.", "by": "Max", "at": "2026-11-25T01:20:00+09:00",
                "channel": "session", "source_file": null, "recorded_by": "rotm.py contract agree"}
}
```

## Common mistakes

- Writing the SQL first and the contract afterwards. The gate refuses the SQL file; propose the contract first.
- Recording an agreement the user did not give, or a paraphrase of their reply. Pass their words exactly.
- Computing the control total from the landed data. It then always matches and checks nothing.
- Putting a rate, a flag or a mapping into the SQL "because it is easier there". It belongs in a helper column.
- Typing an identifier with leading zeros as `integer`. The zeros are lost; use `text`.
- Allowing nulls in the first column. Move a never-null column (usually the key) to the first position.
- Editing an agreed contract by hand. Use `contract propose --force` and ask again.
- Showing the user an `.md` written before the last edit of the JSON. Run `contract show` after every edit; `contract agree` refuses otherwise.
- Recording one reply for every table when it asks for a change in one of them. Record it only for the tables it agrees without change, with the user's sentences about those tables.
- Adding a row-number column as the key, or setting `null_rule` to `allowed`, to get defective rows landed. Agree them as known exceptions instead.
- Writing prose into `source_expression`. It goes into the SELECT list; prose belongs in `description`.

# Excel: landing raw data with Get Data (Power Query)

This file covers step P2 of the procedure in [SKILL.md](../SKILL.md) for Excel: how a SQL result or a file reaches a raw sheet through Get Data (Power Query), what a refresh does to the formulas that read it, and what to do after a refresh. Read it before you attach a query to a model, before you refresh one, and when `check` reports rows that are not covered.

The skill's rule comes first: the query shapes the data to the agreed column contract and lands it on the raw sheet; every analysis step after that is a workbook formula (Max, spec 1.2; [column-contract.md](column-contract.md)).

## Evidence labels

- "confirmed": a claim check in the research (`research/verify/`) found the claim in its source ("platforms B1.2" is item B1.2 of `platforms_standards_check_v1.json`; "researcher 2 #19" is item 19 of `researcher2_check_v1.json`; "local guides L-84" is item L-84 of `local_guides_check_v1.json`; "books B-51" is item B-51 of `books_check_v1.json`).
- "not confirmed": the platforms and standards research report states it from the cited Microsoft page, but no claim check read it.
- "local test": observed in Excel on this PC (Excel for Microsoft 365, version 16.0, build 20625). It describes that build, not a published document.
- "design n": section n of the plugin's design (`rotm_design_v1.md`, kept in the project's build folder, not shipped). It describes what the plugin does.
- "Max, spec 1.x": Max's own words, quoted from Part 1 of the job's specification.
- The research files are kept in the project folder and are not shipped with the plugin. The section [Sources](#sources) at the end of this file gives, for each research identifier this file cites, the publisher, the page title, the link and the date the research read the page.

## A different practice: transform before landing

- The Smart Method book says ETL is better than ELT ("Why is ETL better than ELT?", SM p. 429) and: "You could, of course, do this inside Excel but it is best practice to transform values within Get & Transform." (SM p. 458) (books B-51, confirmed).
- Microsoft's guidance: "Delegate as much processing to the data source as possible"; put as much logic as possible in the native SQL, because later steps cannot fold (platforms B1.6, not confirmed).
- Roche's maxim: "Data should be transformed as far upstream as possible, and as far downstream as necessary." (platforms B3.2, a secondary source, not confirmed).
- This skill follows a different rule, Max's: "after using the Connected Sheet or PowerQuery, the data should be loaded on Sheet as Raw files. and then, all the analysis after that should be done using the formulas and operations within the workbook" (Max, spec 1.2). His reason: "sometimes, it's very difficult to understand the data and difficult to audit the data analysis" (Max, spec 1.2).
- So Power Query steps only shape the data to the contract: select, rename, cast, filter to the agreed scope, join or aggregate to the contracted grain, remove duplicate keys when the user agreed it (otherwise land them as known exceptions) ([column-contract.md](column-contract.md#what-the-sql-or-power-query-may-do)). Aggregating in Power Query to the contracted grain fits both practices: "we are free to aggregate and summarize the data in Power Query, then load those results" (local guides L-82, confirmed).
- Neither practice is wrong. When a user asks to move analysis into Power Query, tell them the skill's rule, and do it only if they decide so; then record the step in the contract's `source_expression` or `source.where`, so the contract still describes what lands.

## How the plugin lands a table in Excel

1. The contract for the table is agreed ([column-contract.md](column-contract.md)).
2. `rotm.py land "<model>" --table <t> --csv <file>` checks the data file against the contract and writes `raw/<t>.csv`, `queries/<t>.sql` and `queries/<t>.pq.m` (design 15). For a database source, the first data file is an export of the query's result; it is the snapshot that `build` writes and that the attached query later replaces (my reading of design 7.3 and 15).
3. `rotm.py build "<model>/model.spec.json"` writes the workbook. For a raw sheet with `"landing": "static"` it writes the snapshot's values. For a raw sheet with `"landing": "power_query"` it then attaches the query through Excel (design 7.3).
4. To attach a query to a workbook already built: `rotm.py land "<model>" --table <t> --power-query`. Through Excel, it deletes the static table and values on `raw_<t>`, adds the query `q_<t>` with the M text, loads it as the table `tbl_raw_<t>` at `raw_<t>!$A$1`, sets BackgroundQuery off, refreshes, waits until the refresh is done, and saves (design 7.3).
5. Without Excel, `build` writes static values, warns W-PQ-NOT-ATTACHED (strict mode exits 1) and prints the `land --power-query` command to run later (design 7.3).
6. Run `rotm.py check "<model>"`.

The form of the M text for a local CSV (design 7.3; `land` writes the contract's columns and types). The plugin's own Excel test loads a query of this form (test `test_power_query_attach_then_refresh_with_more_and_fewer_rows` in `scripts/tests/test_excel.py`):

```
// rotm-stamp: contract sha256:<64 hex>; body sha256:<64 hex>
let
    Source = Csv.Document(File.Contents("<model folder>\raw\sales.csv"), [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    Selected = Table.SelectColumns(Promoted, {"invoice_id", "amount"}),
    Nulls = Table.ReplaceValue(Selected, "", null, Replacer.ReplaceValue, {"invoice_id", "amount"}),
    Typed = Table.TransformColumnTypes(Nulls, {{"invoice_id", type text}, {"amount", type number}}, "en-US")
in
    Typed
```

- The file holds the placeholder `<model folder>`, not the model folder's path, so a model folder can be moved or shared. `rotm.py land --power-query` (and build's attach) puts the model folder's path in the text it sends to Excel; the file keeps the placeholder. The query attached to the workbook keeps the path it was given (`check --refresh` does not change it): after moving the model folder, run `rotm.py land "<model>" --table <t> --power-query` again before a refresh. A query added by hand needs the path written in.
- The first line is the stamp: the sha256 of the agreed contract the file was written from and of the text below it. On every `land`, a file stamped with the current contract is kept (an edit made after this agreement stays); a file stamped with an earlier contract and not edited is written again, and a note says so; a file edited by hand for an earlier contract, or one without a stamp, is refused (exit 2, nothing written) and named. Then `--rewrite-queries` writes it again from the contract (the old file is kept as `<file>.bak_<time>`), or `--keep-queries` stamps it after you reconciled it with the agreed contract by hand. `queries/<t>.sql` carries the same stamp as a `--` comment.
- `--pq-source csv` or `--pq-source database` that names the other kind than `queries/<t>.pq.m` holds: a file that land wrote and that was not edited since is written again for the kind asked for, and a note says so (`queries/<t>.pq.m was written for a csv source and not edited since: written again for --pq-source database`). A file edited by hand, or one without a stamp, is refused (F-PQ-SWITCH, exit 2, nothing written, also with `--keep-queries`); the refusal names the command `rotm.py land "<model>" --table <t> --rewrite-queries --pq-source database` (the old file is kept as `queries/<t>.pq.m.bak_<time>`). So `land` never ends with exit 0 while `queries/<t>.pq.m` holds the other kind of query. Without `--pq-source`, an existing file keeps its kind.
- The attach (`land --power-query`, and build for a raw sheet with landing `power_query`) refuses a `queries/<t>.pq.m` whose stamp is missing or names another contract, or that still holds a placeholder such as `<server>`, before Excel starts. On a first build, when the workbook holds no query table on `raw_<t>` yet, build then writes static values and reports W-PQ-NOT-ATTACHED with that message. On a rebuild over a workbook that holds a query table on `raw_<t>`, build makes the same tests before it replaces the workbook, and refuses (B-REFUSED, exit 2) when one fails: `raw_<t> of <workbook name> is loaded by its Power Query table, and the query cannot be attached again: <reason>; nothing was written`. The workbook and the manifest stay as they were; the fix names `rotm.py land "<model>" --table <t>`, then the build again. `check --refresh` refuses to refresh a query written for another contract (exit 2, nothing refreshed).
- `land` and `land --keep-queries` change the query file only; the workbook keeps the query it was given until it is attached again (`land --power-query`, or a build that attaches it). The attach records in the manifest the hash of the query text it attached (`query_body_sha256`: the body of `queries/<t>.pq.m` below its stamp line, as the stamp hashes it). Before Excel starts, `check --refresh` compares that hash with the body of `queries/<t>.pq.m` and refuses (exit 2, nothing refreshed) when they differ: `raw_<t>: queries/<t>.pq.m differs from the query text attached to the workbook (...); a refresh would run the query the workbook holds, not the file; fix: rotm.py land "<model>" --table <t> --power-query (attaches the file's query)`. A missing file is refused the same way. A query attached by hand is not compared, with the note it has now. A manifest written before v5 records no hash: `check --refresh` says so in a note and refreshes.

- `Encoding=65001` reads the file as UTF-8, and the culture argument `"en-US"` reads dates and decimal points the en-US way (general product behaviour, not confirmed by the claim checks). The plugin writes every CSV snapshot as UTF-8 with ISO dates and `.` as the decimal point (design 4).
- Import a CSV with Power Query and a stated locale, not by opening it with a double click (local guides L-79, not confirmed).
- Set every column's type explicitly with the contract's types (local guides L-86, not confirmed). An identifier with leading zeros is `type text`.
- For a database (`land --pq-source database`), the M text is `Value.NativeQuery(<source>, <SQL>, null, [EnableFolding = true])`, with the server and database left as named placeholders for the user to fill in (design 7.3). The SQL inside it quotes every column name for the connector: `[name]` for SQL Server (`Sql.Database`, also for a system the plugin does not know), `"name"` for PostgreSQL, Snowflake, Redshift, SAP HANA and Oracle, and backticks for BigQuery and MySQL. A source expression that is exactly one name in backticks (as `contract propose` writes a header such as `` `order` `` or `` `Order Date` ``) gets the same quotes: `[order] AS [order]` for SQL Server, `"order" AS "order"` for PostgreSQL. Any other source expression is the contract's text as written. `queries/<t>.sql` stays GoogleSQL for Connected Sheets. No database was reached when this was built, so the database template was not run. The steps for a database source are in [Database source: the sequence](#database-source-the-sequence).

## Database source: the sequence

For a table that comes from a database (SQL Server, BigQuery, PostgreSQL, Snowflake and the other systems above). No database was reached when this plugin was built and tested: the database template, the approval prompt, the credentials and the refresh against a database were not run. Only queries over a local CSV were run (the plugin's tests). Give the user these steps in this order:

1. Agree the contract ([column-contract.md](column-contract.md)). Its `source.system` names the database (for example `SQL Server` or `BigQuery`): it chooses the connector of the Power Query template and how the SQL quotes column names. Show the user `contracts/<t>.contract.md` and record their reply with `rotm.py contract agree`.
2. Write the query texts: `rotm.py land "<model>" --table <t> --pq-source database`. It writes `queries/<t>.sql` (GoogleSQL) and `queries/<t>.pq.m` as a database template: `Value.NativeQuery` with the SQL quoted for the connector, and the connection left as placeholders such as `<server>` and `<database>`. A `queries/<t>.pq.m` that land wrote earlier for the local CSV is written again, and a note says so; one edited by hand is refused (F-PQ-SWITCH), and the refusal names the command with `--rewrite-queries --pq-source database`.
3. The first extract: the user runs the SQL in the database's own tool (the text of `queries/<t>.sql`, adapted to the database's SQL dialect; the SQL inside `queries/<t>.pq.m` already quotes names for the connector), or runs the query in Excel (Get Data, the connector, Advanced options > SQL statement), and saves the result as a CSV file (UTF-8).
4. Land it: `rotm.py land "<model>" --table <t> --csv <export>.csv`. It validates the file against the contract (columns, types, nulls, allowed values, keys, rows, control totals); when the file does not fit, it reports the findings, exits 1 and writes nothing. When it fits, it writes `raw/<t>.csv`. `queries/<t>.pq.m` keeps its database kind. Then build the model from this snapshot: `rotm.py build "<model>/model.spec.json"` (it needs no database).
5. The user fills in the placeholders of `queries/<t>.pq.m` (`<server>`, `<database>`, `<billing project>` or the others the connector line names) with the connection of their database. Then run `rotm.py land "<model>" --table <t> --keep-queries`. A file whose stamp line is still in place and names the agreed contract is kept as it is (the note says `kept queries/<t>.pq.m`). A file whose stamp line is gone (for example, replaced with the text of Excel's Advanced Editor) or names an earlier contract is stamped with the agreed contract; compare it with `contracts/<t>.contract.md` first. `land --keep-queries` changes the query file only: the workbook keeps the query it was given until step 6 attaches the file's query.
6. Attach the query to `raw_<t>`: `rotm.py land "<model>" --table <t> --power-query` (through Excel; it refuses a file that still holds a placeholder such as `<server>`, before Excel starts), or by hand in Excel ([Attaching a query by hand](#attaching-a-query-by-hand)), with the query named `q_<t>`.
7. The approval prompt: `Value.NativeQuery` is a native query entered outside the connector dialog, so Excel asks the user to approve it the first time it runs and each time its text changes (platforms B1.2, confirmed). The switch is "Require user approval for new native database queries" (Data > Get Data > Query Options > Global > Security); do not tell the user to turn it off. Excel also asks for the database's credentials. The user answers both in Excel; the plugin cannot. Whether `land --power-query` can finish while Excel waits for this approval or for credentials was not tested; if it fails, the user attaches the query by hand, answers the prompts, refreshes, saves and closes the workbook.
8. The first refresh: `rotm.py check "<model>" --refresh`. It is refused before Excel starts ("nothing was refreshed") while the contract of a raw table loaded by a query is not agreed. If Excel has no stored credentials for the source, the refresh cannot run unattended: the user refreshes in Excel and saves, and you run `rotm.py check "<model>"` without `--refresh`.
9. Accept the refreshed rows: `rotm.py check "<model>" --accept-refresh --control <t>.<column>=<source total>`, with the total that the source system reports for the same query (see [After a refresh](#after-a-refresh), item 3). When `check` reports H-COVERAGE or H-BEYOND, run `rotm.py extend "<model>" --table <t>`.
10. Run `rotm.py check "<model>" --strict` until it exits 0.

## Attaching a query by hand

Use `rotm.py land --power-query` when you can. If the user attaches the query in Excel themselves, give them these steps:

1. Save a new copy of the workbook first.
2. Keep the sheet `raw_<t>` under its name. Do not delete it: every formula that reads it turns into `#REF!`. Do not rename it either: Excel rewrites the formulas to the new name, so they keep reading the old static sheet with no error (local test of the round 2 review on this PC: after a rename, `=raw_lines!A1*2` read `=snapshot!A1*2` and still returned 34). Step 8 shows which sheet the formulas read.
3. Delete the static table on `raw_<t>` (select its cells and delete them), so the name `tbl_raw_<t>` is free. Leave the sheet in place.
4. In the Power Query editor, name the query `q_<t>`, then use Close & Load To, choose Table and Existing worksheet, and give `=raw_<t>!$A$1`. The plain Close & Load sends one query to a new worksheet (platforms B2.1, not confirmed); every formula would then go on reading the old sheet without an error.
5. Name the table `tbl_raw_<t>`. Set its table style to None, so the dictionary's formats show and no banded colours appear (design 7.1).
6. In the table's external data properties, untick Adjust column width and keep Preserve cell formatting on (platforms B2.3, not confirmed; the defaults are in "What a refresh does", local test).
7. Refresh, save and close.
8. Read which sheet the formulas read. On `h_<t>`, select row 2 of the first fetch column (column C in a model the plugin built, for example `=INDEX(raw_<t>!$A:$A,A2)&""`) and read the sheet name in its formula. On the checks sheet, read the formula of the Actual cell (column D) of the check `<t>_rows`. Both must name `raw_<t>`, the sheet that holds the query table. If they name another sheet, the model still reads the old static rows: report that, and report no number as computed from the query's rows.
9. Run `rotm.py check "<model>" --accept-refresh --strict`; add `--control <t>.<column>=<source total>` when the source total changed. `check` finds the query table at `raw_<t>!A1` whatever the query's name and whatever the manifest says, validates its rows against the contract, and records in the manifest that the table is loaded by a query attached by hand (`query_attach` `"hand"`, with the query's name). It records this also when the query's first load equals the landed snapshot (no row changed). No contract hash is recorded for such a query, so a later `rotm.py check "<model>" --refresh` says in a note that the query was not compared with the agreed contract.

Whether a Power Query load applies Excel's default banded table style is not confirmed (research synthesis section 5). Step 5 sets the style either way.

### What a rebuild does to a query attached by hand

`rotm.py build` treats `raw_<t>` as loaded by a query attached by hand when the manifest records `query_attach` `"hand"`, or when the workbook holds a query table at `raw_<t>!A1` that no `rotm.py` command attached. Build never attaches the generated query of `queries/<t>.pq.m` in its place.

- With Excel: before anything is written, build reads the query's name, its M text and the name of its query table from the existing workbook through Excel. It writes the new workbook to a temporary file in the same folder and attaches a query with that name and that M text to `raw_<t>` again. Only when every such attach succeeded does it replace the workbook, keep the M text in `queries/<t>.hand.pq.m` and write the manifest (`landing` `"power_query"`, `query_attach` `"hand"`, `query_name`, `query_file` `"queries/<t>.hand.pq.m"`, no contract hash). The build report says "the query <name> attached by hand was carried over" and names the file.
- `queries/<t>.hand.pq.m` holds the M text exactly as Excel gave it (UTF-8, no stamp line). `land` never writes this file. When a later build reads a different text, the older file is kept as `queries/<t>.hand.pq.m.bak_<YYYYMMDD_HHMMSS>`.
- Without Excel, or when the M text cannot be read or the query cannot be attached again: build refuses (B-REFUSED, exit 2) and writes nothing. The workbook, its manifest, `raw/<t>.csv`, `queries/` and `reports/status.json` stay as they were. The message names the query and the sheet. Two ways on: run the build on a PC with Excel, or pass `--drop-hand-queries`.
- `--no-power-query` with such a sheet is refused the same way, so the query is never removed without `--drop-hand-queries`.
- `rotm.py build "<model>" --drop-hand-queries` rebuilds `raw_<t>` as a static table. The manifest says `landing` `"static"`. The report warns W-PQ-NOT-ATTACHED: the query was removed and must be attached by hand again (steps 1 to 9). Until then, `rotm.py check "<model>" --refresh` reports X-NO-QUERY.
- When the carried-over query loads rows that differ from `raw/<t>.csv`, build warns W-PQ-DIGEST; accept the rows with step 9.

## Native SQL in Power Query

- Native SQL goes into the connector dialog under Advanced options > SQL statement. Google BigQuery, ODBC, OLE DB, SQL Server, PostgreSQL, Snowflake, Oracle, MySQL and others accept it (platforms B1.1, not confirmed).
- A native query typed in the connector dialog runs without asking. A native query entered anywhere else (the Advanced Editor, the formula bar, or a query another user wrote) asks for approval each time its text changes (platforms B1.2, confirmed). The switch is Data > Get Data > Query Options > Global > Security, "Require user approval for new native database queries" (platforms B1.2, confirmed). Tell the user about this prompt; do not tell them to turn it off.
- DDL statements (CREATE, ALTER, DROP TABLE) are not supported, and Power Query does not guarantee that a native query has no side effects (platforms B1.3, not confirmed). Ask the user to connect with a read-only account (platforms B1.6, not confirmed).
- A native query typed in the dialog stops query folding for all later steps (platforms B1.4, confirmed for this part). `Value.NativeQuery(target, query, parameters, [EnableFolding = true])` keeps folding on Amazon Redshift, Dataverse (only when using enhanced compute), Google BigQuery, PostgreSQL, SAP HANA, Snowflake and SQL Server (platforms B1.4, confirmed for this part: Microsoft's page names the Dataverse condition, which the research report left out).
- Put simple filters and aggregations in the first steps of a query, so they fold to the source (local guides L-87, confirmed).
- Privacy levels are None, Private, Organizational and Public. "Always ignore Privacy Level settings" removes that protection and could expose confidential data (platforms B1.7, not confirmed). Do not tell the user to choose it.
- A parameter from a worksheet cell: put the value in a one-cell Excel table, load it with From Table/Range, Drill Down to the value, load that query as Only Create Connection, and reference it in the main query (platforms B1.8, not confirmed). Load helper queries as Only Create Connection, and reference a query rather than duplicate it (local guides L-84, confirmed). In a model built by the plugin, the parameter cell belongs on the `inputs` sheet.

## What a refresh does

### Refresh commands

- Refresh All (Ctrl+Alt+F5) refreshes every connection; Refresh (Alt+F5) refreshes the selected one (platforms B2.7, not confirmed).
- Refresh in the Power Query editor updates only the editor's cache, not the sheet or the Data Model (researcher 2 #19, confirmed).
- A connection can refresh when the file opens, or every N minutes, and can remove the external data before the workbook is saved (researcher 2 #19, confirmed).
- The order in which Refresh All refreshes queries that depend on each other is not documented (platforms E7, not confirmed).

### Local test of a query table on its own sheet (Excel 16.0, build 20625)

A Power Query table `tblQ` was refreshed from 5 rows to 8 and then to 3 (platforms B2.4 to B2.6; the claim check re-ran the test in the same build and reproduced every figure: platforms B2.5 and B2.6, confirmed).

- Defaults of a query table: RefreshStyle 1 (insert cells for new data, delete unused cells), PreserveFormatting True, AdjustColumnWidth True, PreserveColumnInfo True, BackgroundQuery False (local test; platforms B2.5 check note, confirmed).
- A calculated column inside the table (`=[@Amount]*2`) kept its formula at 5, 8 and 3 rows (confirmed).
- `SUM(B2:B6)` became `SUM(B2:B9)` and then `SUM(B2:B4)`: Excel moved the range's edges with the table, and the totals stayed right (150, 360, 60) (confirmed).
- Row-by-row formulas beside the table broke. With `=B2*3` in E2:E6, one blank column away: after growing to 8 rows, E6 read `=B9*3`; after shrinking to 3 rows, E4 and E5 read `=#REF!*3` (confirmed).
- A cell below the table moved from A12 to A15 and then to A10, and `$B$2:$B$100` became `$B$2:$B$103` and then `$B$2:$B$98` (confirmed).

### The plugin's layout over a query table (local test, 2026-11-25)

The plugin's Excel test (`test_power_query_attach_then_refresh_with_more_and_fewer_rows` in `scripts/tests/test_excel.py`) loads a CSV through Power Query as `tbl_raw_sales` at `raw_sales!$A$1`, with position-based helper columns on a separate sheet `h_sales` and checks on a third sheet. It passed on this PC on 2026-11-25 (run by the M9 builder, 74 seconds).

- 5 raw rows: both checks read 5 rows.
- After the CSV grew to 8 rows and the query was refreshed: 8 covered rows, 8 raw rows, helper total 360, no error value.
- After the CSV shrank to 3 rows and the query was refreshed: 3 covered rows, helper total 60, no error value, no `#REF!` in any formula; the helper rows below the last raw row read `in_raw` 0.
- Since v2 (2026-11-25), helper rows below the last raw row fail the check `<t>_beyond` until `rotm.py extend` clears them, because a helper that tests a value (for example `--({this:amount}=0)`) counts each of those rows (see "After a refresh", item 5). Measured in `scripts/tests/test_v2_raw_layer.py`: a table refreshed from 72 to 50 rows gave H-BEYOND for `h_sales` rows 52 to 73 and a zero-amount count of 22; after `extend` the count was 0 and `check --strict` exited 0.
- An edit made through Excel afterwards kept the query, the table and every other part (counted by `excel.inventory`).

### What follows for the layout

1. Put the raw table alone on its sheet, header in row 1 from A1, with nothing below it and nothing beside it (design 7.1; platforms D1, the report's inference from B2.4 to B2.6). A refresh moves the cells below the table and breaks row-by-row formulas beside it (platforms B2.6, confirmed).
2. Put row-level helpers on their own sheet `h_<t>`. Helper row r reads raw row r by position: `=INDEX(raw_<t>!$X:$X,A2)`, where `A2` is `=ROW()` (design 7.1, D8). The whole-column reference does not move or break when the query table grows or shrinks (the plugin's test above).
3. Read helper and raw data in calculation blocks as whole columns (`h_sales!$F:$F`), so new rows are included without editing a formula (design 7.1).
4. Do not put calculated columns inside the query table, although they survive a refresh (platforms B2.5, confirmed): a raw sheet holds landed data only (spec 2.2 P2), and Google Sheets table references do not support `#This Row` (platforms A2.4, confirmed), so the model would not work in both products.
5. Do not rely on "fill down formulas in columns adjacent to data": FillAdjacentFormulas applies only to QueryTable objects, and data loaded through the user interface from other sources becomes a ListObject (researcher 2 #18, confirmed). Where Microsoft 365 shows this option for a ListObject is not confirmed (platforms E8).

### Number formats and the raw look through a load and a refresh (local test, 2026-11-25)

- When the plugin loads a query (`rotm.py land "<model>" --table <t> --power-query`, or `rotm.py build` with `"landing": "power_query"`) or refreshes it (`rotm.py check "<model>" --refresh`), it sets the query table's Preserve cell formatting on and Adjust column width off, and then applies the dictionary's number format and raw-cell look to every loaded row of every raw column and the column-header look to the header row. `audit --strict` then needs no rebuild (test `test_power_query_raw_table_keeps_its_formats_through_attach_growth_and_shrink` in `scripts/tests/test_v2_core.py`: strict audit exit 0 after the attach, after a refresh to more rows and `extend`, and after a refresh to fewer rows and `extend`).
- Before this was added, Excel's load reset the formats: 120 raw text cells became General instead of `@`, 60 dates `mm-dd-yy` instead of `yyyy-mm-dd`, and 60 amounts General, so the strict audit failed right after the build.
- A refresh done by hand in Excel (Data > Refresh All) keeps those formats: measured with 60 rows refreshed to 75, to 40 and back to 75 rows, every raw cell kept its number format, font and size, because Excel copies the column formats to new rows while Preserve cell formatting is on.
- A refresh by hand does not validate the new rows and does not record them. Run `rotm.py check "<model>" --refresh` (it refreshes again, applies the formats again to the whole loaded range, validates the rows against the contract and recalculates), then `rotm.py extend` and `rotm.py check "<model>" --accept-refresh` as the check's findings say.
- If the formats were changed by hand, `rotm.py check "<model>" --refresh` applies them again; `rotm.py build "<model>" --force` rebuilds the workbook and attaches the query again with the formats.

## After a refresh

1. Run `rotm.py check "<model>" --refresh`. It refreshes every query through Excel with BackgroundQuery off, waits until no query is refreshing, recalculates, saves, and reads every check (design 7.3). It waits because a sheet read while a background refresh is still running gives a false pass or a false fail.
   - Before Excel starts, `check --refresh` is refused (exit 2, "nothing was refreshed") while a raw table loaded by a query has a contract that is missing, invalid, proposed, stale, or agreed only on the example channel outside the plugin folder. The refusal names each table and the commands that get its contract agreed (`rotm.py contract show`, then `rotm.py contract agree` with the user's reply). The workbook is not changed.
   - A query attached by hand has no recorded contract hash: `check --refresh` says in a note that it was not compared with the agreed contract.
   - Before Excel starts, `check --refresh` is also refused (exit 2, nothing refreshed) when `queries/<t>.pq.m` differs from the query text attached to the workbook (section "How the plugin lands a table in Excel", the bullet on `land --keep-queries`). Run `rotm.py land "<model>" --table <t> --power-query`: it attaches the file's query, and the refresh then runs.
   - A query changed in Excel's Power Query editor (or in any other way than `rotm.py`) is found at the next `rotm.py check --refresh`. When `rotm.py` attaches a query (`land --power-query`, the attach of `build`, and the carry-over of a query attached by hand), it reads the query's formula back from the workbook through Excel and records its hash in the manifest (`query_formula_sha256`; line ends and trailing white space made uniform). `check --refresh` reads the formula again through Excel before the refresh and compares. On a difference it reports X-QUERY-CHANGED (a warning; an error with `--accept-refresh`), refreshes no table and accepts nothing: `raw/<t>.csv`, `raw/<t>.controls.json` and the manifest stay as they were. Put the change into `queries/<t>.pq.m` and run `rotm.py land "<model>" --table <t> --power-query`. A manifest written before v6 has no such hash: `check --refresh` notes that the workbook's query was not compared.
   - For a query attached by hand and carried over by build (`query_attach` `hand`), the fix of X-QUERY-CHANGED is different: to keep the change, rebuild (`rotm.py build "<model>" --force`; build reads the query from the workbook, attaches it again and records its formula); to drop the change, change the query back in Excel's Power Query editor.
   - The query is read back only by `check --refresh`. Run `check --refresh` before `check --accept-refresh` after any change of the source: `check --accept-refresh` alone does not read the query again.
   - A raw table that the manifest records as loaded by Power Query, whose sheet holds no query table at A1 (the query was removed in Excel): `check --refresh` reports X-NO-QUERY for that table, a warning (an error when `--accept-refresh` is given too): `raw_<t>: the manifest records that a Power Query query loads it, but the workbook holds no query table at A1 of raw_<t>; it was not refreshed and nothing was accepted for it`. The fix: `rotm.py land "<model>" --table <t> --power-query`, then `check --refresh` again.
   - A workbook with no Power Query query or connection: `check --refresh` reports X-NO-QUERY, a warning (strict mode exits 1; an error when `--accept-refresh` is given too). The raw sheets are static snapshots and nothing was refreshed. Land new rows and rebuild, or attach a query (`rotm.py land "<model>" --table <t> --power-query`). When the new rows come with a new source total, land them with `--control <t>.<column>=<source total>` (`rotm.py land "<model>" --table <t> --csv <file> --force --control <t>.<column>=<source total>`): `land` records that total for the new snapshot in `raw/<t>.controls.json`, and the rebuild writes it on the inputs sheet.
2. If the source needs credentials that Excel has not stored, the refresh cannot run unattended. The report says so. Ask the user to refresh in Excel, save, and then run `rotm.py check "<model>"` without `--refresh` (design 7.3).
3. If the raw data changed, `check` reports that the raw sheet's digest differs from the manifest. Run `rotm.py check "<model>" --accept-refresh`: it tests the new rows against the contract (rows, keys, nulls, types, allowed values, control totals) and only then records the new digest and snapshot (design 7.3). Give new control totals with `--control <t>.<column>=<value>`, taken from the source system. The accepted totals are kept with the accepted snapshot's digest in `raw/<t>.controls.json`; the contract is not changed, and a later `rotm.py build` of the same snapshot writes the accepted total on the inputs sheet ([column-contract.md](column-contract.md#what-the-plugin-does-with-the-contract-later)). The rules of the acceptance:
   - Changed rows are accepted only from a raw sheet loaded by a query: one that `rotm.py` attached, or a query table at `raw_<t>!A1` attached by hand.
   - The control total of a table and column is, in this order: `--control` of this run; else the total accepted for exactly these rows in `raw/<t>.controls.json`; else the agreed contract's total. It is never a number on the inputs sheet. When the inputs sheet's control cell holds another value than the control used, `check` reports D-CONTROL-INPUT (a warning, so strict mode fails) with the cell and both values, and that value is not recorded.
   - When `raw_<t>` still holds the rows the workbook had when `rotm.py` last wrote it, and `raw/<t>.csv` holds rows landed after that (for example with `land --force`), `check` reports R-CHANGED, accepts nothing and does not change `raw/<t>.csv`. Refresh the query (`rotm.py check "<model>" --refresh`) or rebuild from the snapshot (`rotm.py build "<model>/model.spec.json" --force`). A workbook built before v3 has no workbook digest in its manifest: R-CHANGED, nothing is accepted for that table; rebuild once.
   - When `--refresh` was asked for and no refresh ran (Excel missing or busy, the refresh failed, or the workbook has no query), no table is accepted in that run, and a note says so: `raw/`, the controls files and the manifest are not changed, and the run does not exit 0.
   - CT-VERSION: when the table's contract was agreed again after the build, `check` (and `audit` with the manifest) reports an error until the table is landed again (when its rows change) and the model is rebuilt.
4. If the raw table now has more rows than the helper sheet covers, `check` prints H-COVERAGE with the uncovered rows and the fix, and the in-workbook check `<t>_cover` shows FAIL (design 7.2). The fix: `rotm.py extend "<model>" --table <t>`. Through Excel it copies the last helper row down to the last raw row and keeps queries, charts and names (design 7.2). By hand in Excel: select the last helper row (column A to the last helper column), copy, select the rows down to the last raw row, paste. Every helper formula is position-based, so the copied rows are correct.
5. If the raw table now has fewer rows, no formula breaks (no `#REF!`, the plugin's test above), but the helper rows below the last raw row read empty raw cells as 0 and would feed every block that reads the helper columns (a helper such as `--({this:amount}=0)` would count each of them). `check` reports error H-BEYOND with the helper sheet and the first and last row below the raw sheet's last row, and the workbook's check `<t>_beyond` (helper rows whose `in_raw` is 0) shows FAIL, so the master check fails. The fix: `rotm.py extend "<model>" --table <t>`. It clears the helper rows below the last raw row; blocks read helper columns as whole columns, so the cleared rows read like rows that were never there.
   - A refresh that returns no rows (a contract agreed with `--allow-empty`): `extend` clears every helper row, row 2 included, and `h_<t>` keeps its header row only. The manifest keeps the formula of each helper column. When a later refresh returns rows again, `extend` writes the helper formulas into row 2 from the manifest and copies them down to the last raw row.
6. Run `rotm.py check "<model>"` again until it exits 0.

## Limits

- A worksheet holds 1,048,576 rows by 16,384 columns (researcher 2 #6, confirmed). With the header in row 1, a raw sheet holds at most 1,048,575 records.
- A formula holds at most 8,192 characters and 64 nested levels; a function takes at most 255 arguments; numbers keep 15 significant digits; a cell holds at most 32,767 characters (platforms B5.1, confirmed).
- Excel cannot open paths longer than 218 characters; the plugin recalculates a workbook whose path is longer than 200 characters on a copy in its state folder (measured on the build PC on 2026-11-25 when the Excel bridge was built; not a research claim).

## Not tested here

- A refresh against a database. Only refreshes of a query over a local CSV were run (the plugin's test above).
- The order of Refresh All for queries that depend on each other (platforms E7).
- The "overwrite existing cells" refresh option on a Power Query table (research synthesis section 5).

## Common mistakes

- Using Close & Load instead of Close & Load To an existing sheet: the query lands on a new sheet, and the model goes on reading the old snapshot without an error.
- Deleting the raw sheet to make room for the query: every reference to it becomes `#REF!`.
- Renaming the raw sheet to make room for the query. It gives no error: Excel rewrites the formulas to the new name, so they keep reading the old static sheet. After a hand attach, read the sheet name in a fetch formula on `h_<t>` and in the check `<t>_rows` ([Attaching a query by hand](#attaching-a-query-by-hand), step 8).
- Typing a new control total into the inputs sheet after a refresh. `check` does not use it (D-CONTROL-INPUT). Give the source system's total with `--control <t>.<column>=<value>`.
- Running `check --accept-refresh` after `land --force` without a refresh: the workbook still holds its older rows, and `check` refuses to write them over the newer snapshot (R-CHANGED).
- Writing helper formulas beside the query table, or a total below it. A refresh moves or breaks them (platforms B2.6, confirmed).
- Adding a calculated column or a custom step that computes a rate, a flag or a mapping in Power Query. That is analysis; it belongs in helper columns.
- Reading the sheet right after a background refresh has started: the values may still be the old ones. Use `check --refresh`, which waits.
- Telling the user to turn off the native-query approval prompt or to ignore privacy levels.
- Reporting check values from a workbook that Excel has not recalculated and saved: a file written by openpyxl holds formulas without values.

## Sources

One row per research identifier this file cites. The links and the reading dates come from the research reports: the platforms and standards report read every link on 2026-11-24, and the researcher 2, local guides and books reports are dated 2026-11-24. The reports give the links, not the page titles: the titles of the web pages below were read from the pages on 2026-11-25 by the v5 builder. A local Excel observation is marked "local test", and a row whose identifier has no public page says so. "design n" and "Max, spec 1.x" are the plugin's own design and the job's specification (see "Evidence labels"); they have no public page.

| Identifier | Publisher | Page title | Link | Read |
| --- | --- | --- | --- | --- |
| platforms A2.4 | Google Docs Editors Help | Use table references in Google Sheets | https://support.google.com/docs/answer/15637642 | 2026-11-24 |
| platforms B1.1 | Microsoft Learn, Power Query documentation | Import data from a database using native database query | https://learn.microsoft.com/en-us/power-query/native-database-query | 2026-11-24 |
| platforms B1.2 | Microsoft Learn, Power Query documentation | Import data from a database using native database query | https://learn.microsoft.com/en-us/power-query/native-database-query | 2026-11-24 |
| platforms B1.3 | Microsoft Learn, Power Query documentation | Import data from a database using native database query | https://learn.microsoft.com/en-us/power-query/native-database-query | 2026-11-24 |
| platforms B1.4 | Microsoft Learn, Power Query documentation | Query folding on native queries | https://learn.microsoft.com/en-us/power-query/native-query-folding | 2026-11-24 |
| platforms B1.6 | Microsoft Learn, Power BI documentation | Query folding guidance in Power BI Desktop | https://learn.microsoft.com/en-us/power-bi/guidance/power-query-folding | 2026-11-24 |
| platforms B1.7 | Microsoft Support | Set privacy levels (Power Query) | https://support.microsoft.com/en-us/office/set-privacy-levels-power-query-cc3ede4d-359e-4b28-bc72-9bee7900b540 | 2026-11-24 |
| platforms B1.8 | Microsoft Support | Create a parameter query (Power Query) | https://support.microsoft.com/en-us/office/create-a-parameter-query-power-query-5eb365bc-3982-4ab2-8830-b205a69e0f33 | 2026-11-24 |
| platforms B2.1 | Microsoft Support | Create, load, or edit a query in Excel (Power Query) | https://support.microsoft.com/en-us/office/create-load-or-edit-a-query-in-excel-power-query-ca69e0f0-3db1-4493-900c-6279bef08df4 | 2026-11-24 |
| platforms B2.3 | Microsoft Support | Manage external data ranges and their properties | https://support.microsoft.com/en-us/office/manage-external-data-ranges-and-their-properties-0091ae04-dfcb-44fc-ad47-3b16844e8173 | 2026-11-24 |
| platforms B2.4 | - | local test: observed by the platforms researcher in Excel for Microsoft 365, version 16.0, build 20625, on the build PC; no public page | - | 2026-11-24 |
| platforms B2.5 | - | local test: observed by the platforms researcher in Excel for Microsoft 365, version 16.0, build 20625, on the build PC; no public page | - | 2026-11-24 |
| platforms B2.6 | - | local test: observed by the platforms researcher in Excel for Microsoft 365, version 16.0, build 20625, on the build PC; no public page | - | 2026-11-24 |
| platforms B2.7 | Microsoft Support | Refresh an external data connection in Excel | https://support.microsoft.com/en-us/office/refresh-an-external-data-connection-in-excel-1524175f-777a-48fc-8fc7-c8514b984440 | 2026-11-24 |
| platforms B3.2 | ssbipolar.com, a blog (a secondary source); SQLBits, a conference session page | Roche's Maxim of Data Transformation; Roche's Maxim of Data Transformation - By Example | https://ssbipolar.com/2021/05/31/roches-maxim/ ; https://sqlbits.com/sessions/event2024/Roches_Maxim_of_Data_Transformation-By_Example | 2026-11-24 |
| platforms B5.1 | Microsoft Support | Excel specifications and limits | https://support.microsoft.com/en-us/office/excel-specifications-and-limits-1672b34d-7043-467e-8e27-269d656771c3 | 2026-11-24 |
| platforms D1 | - | no public page: the research report's own conclusion from its items B2.4 to B2.6, A1.3 and A1.4 | - | 2026-11-24 |
| platforms E7 | - | no public page: an item of the research report's list of points not confirmed | - | 2026-11-24 |
| platforms E8 | - | no public page: an item of the research report's list of points not confirmed | - | 2026-11-24 |
| researcher 2 #6 | Microsoft Support | Excel specifications and limits | https://support.microsoft.com/en-us/excel/excel-specifications-and-limits | 2026-11-24 |
| researcher 2 #18 | Microsoft Learn, Office VBA reference | QueryTable.FillAdjacentFormulas property (Excel) | https://learn.microsoft.com/en-us/office/vba/api/excel.querytable.filladjacentformulas | 2026-11-24 |
| researcher 2 #19 | Microsoft Support | Refresh an external data connection in Excel | https://support.microsoft.com/en-us/excel/refresh-an-external-data-connection-in-excel | 2026-11-24 |
| local guides L-79 | PerfectXL, a Coursera course | Excel Modeling for Professionals, reading 1.3 | no public page recorded: a course document read on the build PC | 2026-11-24 |
| local guides L-82 | not recorded | Five things Excel users should know about Power Query (a white paper) | no public page recorded: a document file read on the build PC | 2026-11-24 |
| local guides L-84 | Goodly | Power Query Tips and Tricks | no public page recorded: a document file read on the build PC | 2026-11-24 |
| local guides L-86 | Goodly | Power Query Tips and Tricks | no public page recorded: a document file read on the build PC | 2026-11-24 |
| local guides L-87 | not recorded | Power Query for Power BI and Excel (a book) | no public page: a book read on the build PC | 2026-11-24 |
| books B-51 | The Smart Method | Learn Excel 365 Expert Skills with The Smart Method (a book, January 2021) | no public page: a book read on the build PC | 2026-11-24 |
| research synthesis section 5 | - | no public page: the list of points not verified in the project's research synthesis, which is not shipped | - | - |

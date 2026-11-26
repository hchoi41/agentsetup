---
name: rule-of-thumb-model
description: Use when building, auditing, restyling, checking, tracing, splitting, documenting or comparing a spreadsheet model in Excel or Google Sheets, especially one on raw data landed by SQL through Power Query (Get Data) or Connected Sheets. Agrees the destination columns with the user before any SQL, keeps each raw table alone on its own sheet as landed data, builds every later step as short workbook formulas (at most 2 nested functions) with helper columns, checker cells and one master check, styles every cell from one dictionary (Arial 8 or 9, light grey headers, minimal colour), audits in strict mode, recalculates in Excel and renders the sheets to look at them. Also audits or restyles a workbook the user built by hand, makes a workbook work in both Excel and Google Sheets, and lifts a model into a Python script that ties out to the workbook.
---

# rule-of-thumb-model

## Overview

Max's method, in his words: "after using the Connected Sheet or PowerQuery, the data should be loaded on Sheet as
Raw files. and then, all the analysis after that should be done using the formulas and operations within the
workbook". This skill makes an agent work that way:

- SQL (Connected Sheets in Google Sheets, Get Data / Power Query in Excel) only shapes the data to an agreed
  column contract and lands it on a raw sheet.
- Every step after landing is a workbook formula a reader can click and follow: helper columns and helper cells,
  one step per helper.
- Checker cells test the landed data against its contract and each calculation block against an independent
  total; one master check reads every other check.
- Every cell gets its look from one style dictionary.

One script owns the workbook. `rotm.py build` compiles a JSON model specification into `model.xlsx`: it writes the
layout, every cell reference, every style and the check wiring, so two models built with it read the same way.
The agent writes the specification, never the workbook.

The thumb rule. Max: "the calculations for the entire process should hopefully not be longer than 1 thumb (this is
a metaphor); the realistic approach is having less than 3 nested functions at most. (if we require something more
than that perhaps we need a helper column)". FAST 3.03-01 says "Do not write a formula longer than your thumb" and
gives no number. This skill measures it:

- Nesting depth: at most 2 functions open at the same time in one formula (one setting, `depth_limit`). Deeper is
  an error; the message names the part to move into a helper.
- Length: at most 100 characters (a warning; the plugin's own number, `length_limit`).
- The counting rule, with examples, is in `references/functions.md` and `references/helper-patterns.md`:
  `IFERROR(VLOOKUP(...))` counts 2 and passes; `IFERROR(INDEX(..., MATCH(...)))` counts 3 and fails.

## Inputs

- The question: what the model must answer, in the user's words.
- Raw data: the SQL source (system, tables, filters) or a sample export (CSV) of each raw table.
- Product: `excel`, `sheets` or `both` (default `both`: only functions that exist in both products).
  - With `sheets`, a function that only Google Sheets has (for example JOIN, SPLIT or REGEXMATCH;
    `references/functions.md` lists them) cannot be calculated by Excel, so such a model cannot be verified in
    Excel. Prefer the alternative the catalogue names (for JOIN the `&` operator). Build prints `I-SHEETS-ONLY`
    (information) for each such function.
- A model folder: one folder per model (it holds the contracts, the SQL, the raw snapshots, the specification,
  the workbook and the reports).
- Optional: a template to start from (`rotm.py init --list-templates`).

## Names used below

- `SKILL_DIR`: this skill's base directory (Claude Code prints "Base directory for this skill: ..." when it loads
  the skill). Under Git Bash write it with forward slashes.
- `ROTM`: `python -P -X utf8 -B "<SKILL_DIR>/scripts/rotm.py"`. Never `-I`: openpyxl may live in the user
  site-packages, which `-I` hides.
- `MODEL`: the model folder.

## Procedure

0. **Set up the model folder.**
   - New model: `ROTM init MODEL --name <name>`.
   - From a template: `ROTM init MODEL --template <name>` (`ROTM init --list-templates` lists them). A template's
     contracts come back as `proposed`: its user must agree them again (P1). `ROTM init --list-templates` prints
     each template's name and its folder. `starter` lies in the skill folder (`<SKILL_DIR>/templates/starter`);
     `ar_aging`, `budget_vs_actual`, `price_volume_mix` and `rolling_forecast` lie in the plugin's `templates/`
     folder and are listed only when the whole plugin is installed. Read a template's files (its README, contracts
     and specification) in `MODEL` after `init` has copied them there.
   - `init` copies neither the template's `queries/` nor its `raw/` (nor its built workbook, manifest or
     reports). The template's sample extracts stay under `MODEL/data/`. After the user agrees a contract, land
     its table: `ROTM land MODEL --table <t> --csv MODEL/data/<t>.csv` writes `raw/<t>.csv` and the queries
     for the agreed contract. Then build (P3).
   - `init` writes `MODEL/rotm_model.json`. The plugin's hooks act only in a folder that holds this file.
   - `MODEL/raw/` holds only the snapshots `land` writes (`raw/<t>.csv` and `raw/<t>.controls.json`). A file of the
     user's found there is copied to `MODEL/data/` and landed from there; the report names the files left in
     `raw/`.

1. **P1 Agree the column contract** (before any SQL). Read `references/column-contract.md` first.
   1. Propose one contract per raw table: `ROTM contract propose MODEL --table <t> --csv <sample.csv> --key <a,b>
      --grain "one row per ..." --rows <MIN:MAX> --unit <col>=<unit> --total <col>=<source total>`.
      Without a sample: `--columns name:type,...`. With a sample, propose writes a display `format` for each
      number column (whole numbers: the type's default; decimals: `amount2`; values mostly below 1: `factor` or
      `general`) and lists it under "Points to confirm".
      - Choose a short table name: at most 12 characters is advised. The name is part of the sheet names `raw_<t>`
        and `h_<t>` and of every formula that reads them, and counts toward the 100-character formula length
        limit. `contract propose` prints a note for a longer name.
      - A header that is a GoogleSQL reserved word or not a plain identifier (letters, digits and `_`, not
        starting with a digit), for example `order` or `Order Date`, is proposed as a quoted identifier: the source
        expression is written in backticks (`` `order` ``, `` `Order Date` ``), the contract `.md` shows it, and
        `queries/<t>.sql` quotes it. A source expression you wrote is kept as written.
      - With `--csv`, `propose` records for each column the sample header it was read from (`csv_header` in the
        contract JSON). When a header differs from its destination name (for example `Order Date` proposed as
        `order_date`), the contract `.md` shows the column "CSV header" beside "Column", and "Points to confirm"
        lists each header with its destination name. After agreement, `land --csv` accepts that file as it is
        (P2).
      - A destination column may not have the name of a helper column build adds (`row`, `in_raw`, `dup_key`,
        `dup`, or the null flag `<column>_null` of another column). `contract propose` prints the note
        `N-HELPER-NAME` with a suggested destination name (`<name>_src`), and build refuses such a contract
        (exit 2). Rename the destination column in the contract, keep its source expression, then show the
        contract and get it agreed again.
      - A table proposed or landed under a name that is not wanted: `ROTM contract remove MODEL --table <t>`. It
        refuses (exit 2) while `model.spec.json` has a sheet with `"table": "<t>"`; otherwise it moves the table's
        contract (`.json` and `.md`), `queries/<t>.*`, `raw/<t>.csv` and `raw/<t>.controls.json` into
        `MODEL/removed/<t>_<YYYYMMDD_HHMMSS>/` and lists what it moved.
   2. Fix in the proposed JSON what a sample cannot know: units, null rules, allowed values, source expressions
      (one SQL expression each; prose goes in `description`), and the control total from the source side (the
      source system's own count or total, never a sum of the landed rows). After every edit of the JSON run
      `ROTM contract show MODEL --table <t>`: it writes `contracts/<t>.contract.md` again from the JSON.
   3. A defect the sample already shows (a repeated key, a few empty cells in a never-null column) goes into the
      proposal as a known exception: `--key-repeats N`, `--known-nulls <column>=N` (propose writes both from the
      sample and lists them under "Points to confirm"). The user agrees it with the rest of the contract. The
      workbook's check stays and counts the defect. Never add your own row-number key or set a null rule to
      `allowed` to get such rows past `land` (`references/column-contract.md`, "Known exceptions").
   4. Show the user `MODEL/contracts/<t>.contract.md` unchanged, as the last `contract propose` or
      `contract show` wrote it. Never show an `.md` written before the last edit of the JSON. It ends with
      "Reply 'agreed' or say what to change." Stop and wait. Write no SQL, land nothing, build nothing.
   5. On changes: edit the JSON, run `ROTM contract show MODEL --table <t>`, show the user what it writes, wait
      again. `contract agree` refuses (exit 2, nothing recorded) when the `.md` is missing, when it differs from
      the text rendered from the current JSON, or when the JSON changed after the `.md` was last written: "the
      contract changed after it was last shown". Then run `contract show`, show the user the `.md` again and
      record their new reply.
   6. On agreement: `ROTM contract agree MODEL --table <t> --reply "<the user's words, verbatim>" --by <name>`.
      The agreement records the contract version it was given for.
      - A reply that asks for a change is refused (its reason starts "asks for a change"): an imperative or a
        request with change, rename, remove, drop, replace, switch, fix, correct, revise or modify (also with
        "please", "can you", "should", "needs to"), or 바꿔, 변경해, 수정해, 고쳐. A negated form is not a change
        request ("no changes", "nothing to change", "change nothing", "without changes"). Make the change, run
        `contract show`, show the user the contract again and record their reply to the new version.
      - A reply that refuses is refused (exit 2, nothing recorded): reject, decline and refuse (also rejected,
        rejection, declined, refused, refusal); a negation before run, proceed, start, land, continue or go ahead
        ("Do not run the query yet."); 거절, 반려, 거부, 하지 마. The message ends: no agreement was given; wait for
        the user's reply.
      - A reply that postpones the decision and gives no agreement is refused (exit 2, nothing recorded; its
        reason starts "postpones the decision"): will review, review it later or tomorrow, get back to you, need
        to check or look first, not yet, let me check, hold off and hold on, please wait, wait until and wait for
        (not when negated: no need to wait is not a stop, so "No need to wait, go ahead." is recorded), and 나중에,
        내일, 검토 후, 확인해 볼게, 아직, 보류, 기다려. The message says: no agreement was given; wait for the
        user's reply. Land nothing and build nothing until the user replies, then record that reply.
      - A reply that is only a question is not an agreement: every sentence ends with a question mark (or a Korean
        question ending) and no sentence is a statement, for example "What does the amount column include?". It
        is refused (exit 2, nothing recorded; its reason starts "is a question"). Answer the question and wait
        for the user's reply. A reply with a statement and a question ("Looks good, can you go ahead?") is
        recorded.
      - A reply that agrees some tables and asks for a change in another is recorded only for the tables it
        agrees without change, with `--reply` set to the user's sentence or sentences about those tables, copied
        exactly. The other table waits for the change and a new reply.
      - No list of words is required in an agreement, and the reply need not name the table: the user's own
        approval in any words is recorded. On the task-file channel, the sentence of the task file that holds
        the reply must not read as a refusal or a change request.
   7. No user to ask (a task file, a headless run): if the task file carries the agreement in words, use
      `--channel task-file --source-file <task file>`; otherwise stop and report "the contract for <t> is
      proposed, not agreed; nothing was landed".
   8. A change after agreement: `contract propose ... --force` (the next version), show the user the new `.md`
      (its "Changes since version N" lists what changed), and record their new reply. When `contract agree` says
      the reply is the same text as an earlier version's, say in the report which version was agreed with which
      words, and list the changes.

2. **P2 Land raw data.** Read `references/excel-power-query.md` (Excel) or `references/google-sheets.md` (Google
   Sheets) first.
   - `ROTM land MODEL --table <t>` writes `queries/<t>.sql` (GoogleSQL for Connected Sheets) and `queries/<t>.pq.m`
     (Power Query M). It refuses until the contract is agreed.
   - The first line of each query file is a stamp: the sha256 of the agreed contract it was written for and of
     its own text. On every `land`: a file stamped with the current contract is kept, with any edit made since;
     a file stamped with an earlier contract and not edited is written again (a note says so); a file edited by
     hand that names an earlier contract, or has no stamp, is refused (exit 2). Then either `--rewrite-queries`
     (writes it again from the contract; the old file is kept as `<file>.bak_<time>`) or, after you made it
     match the current contract by hand, `--keep-queries` (stamps it with the current contract).
     `--pq-source csv` or `--pq-source database` naming another kind than `queries/<t>.pq.m` holds: a stamped file
     not edited since it was written is written again for the new kind (a note says so); a file edited by hand, or
     one without a stamp, is refused (exit 2, nothing written) with the command `ROTM land MODEL --table <t>
     --rewrite-queries --pq-source <kind>` (the old file is kept as `<file>.bak_<time>`). `land` never exits 0
     having kept a query of the other kind.
     The Power Query attach (`land --power-query`, and build for a raw sheet with landing `power_query`) refuses
     a `queries/<t>.pq.m` whose stamp is missing or names another contract, before Excel starts; `check --refresh`
     refuses to refresh it. `queries/<t>.pq.m` names the snapshot as `<model folder>\raw\<t>.csv`; the attach
     puts the model folder's path in the text it sends to Excel.
   - `ROTM land MODEL --table <t> --csv <extract.csv>` checks the extract against the contract (the header line,
     below; types, nulls, allowed values, key, row count, control totals) and writes `raw/<t>.csv`. A failed check
     writes nothing. Repeated keys and empty cells within the contract's known exceptions are landed and reported
     (`L-KNOWN`, which names the lines).
   - `land --csv` skips only a physically blank line. A record that holds only delimiters (every field empty, for
     example `,,`) is a row like any other: the null rule of the first column refuses it (`L-NULL`; `L-KEY` also
     names it when two such records repeat a key; a record with another number of fields than the header is
     `L-FIELDS`), the finding names its CSV line, and nothing is written; remove such records from the extract.
     A quoted field whose closing quote is missing at the end of the file is refused with the line where the field
     starts, and any other quoting error with its line; nothing is written. `contract propose` counts the records
     of only delimiters in a sample and names their lines under "Points to confirm".
   - The header line `land --csv` accepts: the destination names in the contract's order; or, when the contract
     records a sample header for every column (`csv_header`, P1 step 1), each of those headers exactly once, in any
     order, compared after trimming spaces, and no other column. In the second form `land` reads the columns by
     their headers, writes `raw/<t>.csv` with the destination names in the contract's order, and prints each header
     with its destination name. Any other header is `L-COLUMNS`: nothing is written and the fix names both forms;
     report it and stop, as below. A contract proposed with `--columns`, or before v7, records no sample headers:
     its CSV needs the destination names. A contract in which one column's sample header (`csv_header`) is another
     column's destination name is refused by `contract show`, `contract agree` (exit 2, nothing recorded) and
     `land` (nothing written), because a header line could be read either way. Rename that destination column, or
     remove the `csv_header` entries, then run `contract show` and get the contract agreed again.
     `contract propose` never writes such a contract.
   - `land` prints a note that lists the files in `raw/` that it did not write. `land --csv MODEL/raw/<t>.csv` (the
     snapshot path itself) reads the whole file before it writes anything: a refused landing leaves the file byte
     for byte, and an accepted one writes every row back, with the destination names in the header.
   - A new extract whose source total changed (a refreshed export, for example from Google Sheets):
     `ROTM land MODEL --table <t> --csv <file> --force --control <t>.<column>=<source total>`, with the total the
     source system reports for the same query. `land` checks the file by the rules `check --accept-refresh` applies
     to refreshed rows and against the total given, then writes `raw/<t>.csv` and records the given total for that
     snapshot in `raw/<t>.controls.json`; check and build then use it. A given total that does not equal the
     file's sum is `L-TOTAL`, and nothing is written. A total accepted under an earlier agreement (the contract was
     agreed again since, for example with another total) is not used: `land`, `build`, `check` and `lift` print a
     note that names it, and when the rows do not meet the contract's total, `L-TOTAL` (`land`) or `D-TOTAL`
     (`check`) names both totals. Give `--control` with the source's current total; it is recorded under the
     agreement in force. `--control` is accepted only for a column whose control total the agreed contract names: a
     control total is part of the agreement. For any other column `land` and `check --accept-refresh` refuse (exit
     2; nothing is written or recorded). Add the total to the contract (`contract propose ... --force ...
     --total <column>=<source total>`), show it and get it agreed; build then writes its check
     `<t>_total_<column>`.
   - `L-PRECISION` (a warning, one per column and reason, naming the first line and the count): a number with more
     than 15 significant digits (Excel and the snapshot keep 15); a datetime whose fraction of a second was cut
     (cut, never rounded up, so no time moves into the next second, day or period); a datetime whose `Z` or UTC
     offset was dropped (the time is landed as written). Decide with the user: round the values in the source, or
     land the column as text (change its type in the contract and get the contract agreed again). Key uniqueness
     is decided on the key values as `raw/<t>.csv` stores them: two number keys that differ only after the 15th
     significant digit are one key in the snapshot, and `land` refuses them with `L-KEY`, naming both lines and
     both values; nothing is written. Make such a key column type text in the contract.
   - An empty extract (a header row only) lands only under a contract agreed with `--allow-empty` (rows `0:N`).
     Build then writes zero helper rows: the helper sheet holds its header row only, and the manifest keeps each
     helper column's formula. When a later refresh returns rows, `ROTM extend MODEL --table <t>` writes the helper
     rows from those formulas.
   - A query attached by hand in Excel (step list in `references/excel-power-query.md`, "Attaching a query by
     hand"): `check` recognises a query-backed table at `raw_<t>!A1`, whatever the query's name and whatever the
     manifest says, and at its acceptance (`ROTM check MODEL --accept-refresh --strict`) records the table as loaded
     by a query attached by hand, with the query's name, also when its first load equals the landed snapshot. No
     contract hash is recorded for such a query, and `check --refresh` says so.
   - A rebuild over a query attached by hand (the manifest records `query_attach` `hand`, or the workbook holds a
     query table at `raw_<t>!A1` that no `rotm.py` command attached): build never puts the generated query of
     `queries/<t>.pq.m` in its place.
     - With Excel: build reads the query's name and its M text from the existing workbook, keeps the text in
       `queries/<t>.hand.pq.m` (`land` never writes that file), builds, and attaches the query again with the same
       name. The report says that the query attached by hand was carried over and names the file; the manifest
       keeps `query_attach` `hand`. A query attached by hand that reads other workbook queries by name (a staging
       query, directly or through others): build keeps the M text of each of them in `queries/<name>.hand.pq.m`,
       adds them again as connection-only queries before it attaches the raw query, and names them in the report.
       A query it reads that cannot be made again (for example one that reads an Excel table of the workbook) is
       named in the refusal (exit 2, nothing written). A query read whose file `queries/<name>.hand.pq.m` would be
       the file of a raw table's query attached by hand or of another query read (the names are equal without
       regard to case) is refused (exit 2, nothing written), and the refusal names both queries; rename one of them
       in Excel, or pass `--drop-hand-queries`.
     - Without Excel, or when the M text cannot be read or attached again: the rebuild is refused (exit 2) before
       anything is written; the message names the query and the sheet. Without Excel, run the build on a PC with
       Excel, or pass `--drop-hand-queries`. When Excel ran and a query cannot be carried over, the refusal gives
       `--drop-hand-queries` and says that the present workbook is kept until then.
       `ROTM build MODEL --drop-hand-queries` rebuilds the sheet as a static table (`landing` `static`), warns
       `W-PQ-NOT-ATTACHED` (attach the query by hand again), and `check --refresh` then reports `X-NO-QUERY`.
   - A rebuild of a workbook whose raw sheet holds a live query that `rotm.py` attached: before build replaces the
     workbook and its manifest, it copies both to `<stem>.bak_<YYYYMMDD_HHMMSS>.xlsx` and
     `<stem>.manifest.bak_<YYYYMMDD_HHMMSS>.json`. When every query is attached again, both copies are removed. When
     an attach fails (for example the source cannot be reached) or Excel is not available, the copies stay and
     `W-PQ-NOT-ATTACHED` names them: to go back, copy both over the new files; to go on, make the source reachable
     and run `ROTM land MODEL --table <t> --power-query`. The hooks do not audit the copies. The raw-sheet guard
     refuses a shell command that copies onto an existing workbook, so the user copies the two files back by hand.
   - When `land` refuses an extract (`L-KEY`, `L-NULL`, `L-ROWS`, `L-TOTAL`, `L-TYPE`, `L-ALLOWED`, `L-LENGTH` and
     the others):
     1. Report to the user each refusal line as printed: the rule id and the rows it names (line numbers and key
        values). Then stop: land nothing for that table and build nothing on it.
     2. Do not get past the refusal yourself. Never add a made-up key column (a row number), set a null rule to
        `allowed`, widen the row count, change the control total, or remove rows from the extract.
     3. A change of key, null rule, row count or control total is a contract change. Make it only after the user
        asks for it: `contract propose ... --force`, show the new `.md` (its "Changes since version N" lists the
        change), wait, and record the reply the user gives to the new version, in their new words (P1 step 8). It
        is agreed again or it is not landed.
     4. If the user accepts the defect, the new version records it as a known exception (`--key-repeats N`,
        `--known-nulls <column>=N`). `land` then lands the rows with `L-KNOWN`, and the workbook's checks
        `<t>_keys` and `<t>_nulls_<col>` count them (actual N, at most N agreed), so the defect stays visible.
        For a defect that has no known-exception entry (rows outside the agreed range, a total that differs, a
        value outside the allowed list), the source is corrected, or the user agrees the changed contract and the
        specification gets an alert (`"severity": "alert"`) that counts the affected rows.
   - The SQL may only shape: select, rename and cast the contract's columns; filter to the agreed scope; join and
     aggregate only as far as the contract's grain needs; remove duplicate keys only when the user agreed it.
     Rates, flags, mappings, period logic and allocations are workbook formulas.
   - A raw sheet holds the landed table alone, header in row 1 from A1, nothing below or beside it. No formula and
     no typed edit ever goes on a raw sheet.

3. **P3 Model with formulas only.** Read `references/model-spec-format.md`.
   - Write `MODEL/model.spec.json`: inputs, one raw sheet per table, a helper sheet per raw table (one helper
     column per step), calculation and output blocks, checks, notes.
   - Write expressions with placeholders (`{h:sales.amount}`, `{in:scale}`, `{row}`, `{col}`), never cell
     addresses. Build turns each placeholder into an A1 reference.
   - Every typed number goes on the inputs sheet; a formula may hold only the numbers 0 and 1 (`allowed_literals`).
   - An empty raw cell reads as 0 in a helper column. For each fetched `number`, `integer`, `date` or `datetime`
     column whose null rule is `allowed`, and for each such column whose contract agrees `known_nulls` for it (a
     known exception), build adds the null flag `<column>_null` (1 when the raw cell is empty); `lift` computes it
     the same way. COUNT and AVERAGE over such a column read the flag: `COUNTIFS({h:<t>.<column>_null},0)` and
     `AVERAGEIFS({h:<t>.<column>},{h:<t>.<column>_null},0)` (`references/helper-patterns.md`).
   - An empty date in a helper column shows as an empty cell: the fetch of a date or datetime column that the
     contract allows to be empty (null rule `allowed`, or agreed known nulls) has the number format `date_or_empty`
     or `datetime_or_empty`, which shows nothing for 0, so an empty raw date is not shown as 1900-01-00.
   - The fetched value is still 0. A calculation that must tell an empty date from a date reads the null flag
     `<column>_null` (1 where the raw cell is empty), not the fetched value.
   - A helper column, block, item or check that reads itself through others is refused by build (exit 2, with
     the JSON paths of the cycle).
   - A helper column may not read other rows of its own helper sheet: build refuses `{h:<table>.<column>}` there and
     its fix names `references/helper-patterns.md`, "Rank or percentile within a group", which gives the pattern for
     a rank or a percentile within a group.
   - `ROTM build MODEL --strict`. A refused specification lists every problem with its JSON path and what to
     change; nothing is written.

4. **P4 Keep every formula short.**
   - Build refuses a formula deeper than 2 and names the part to move, for example "move `MATCH(...)` into a
     helper column (`{this:<id>}`)".
   - Measure a formula before writing it: `ROTM formula "=IFERROR(INDEX(A:A,MATCH(B1,C:C,0)),0)"` (it prints the
     depth, the chain, the length, the typed numbers and the part to move; `--limit N`, `--max-length N`,
     `--product`, `--allow NAMES` (flagged functions to accept), `--allowed-literals 0,1,12`, `--sheet NAME` (checks
     the own-sheet prefix), `--strict`).
   - An expression that is not a complete formula (a trailing or doubled operator such as `=D39+`, an operand
     missing at the start or after a bracket or comma, an unclosed bracket or quote) is the error `F-PARSE`:
     `ROTM formula` exits 1, and build refuses it (exit 2, nothing written) with the JSON path of the expression in
     `model.spec.json` and the place in the text.
   - A listed function with too few or too many arguments (for example `IF` with 4 arguments; IF takes 2 or 3) is
     the error `F-ARGS`: `ROTM formula` exits 1, build refuses it (exit 2, nothing written) with the JSON path of the
     expression, and the audit reports it at the cell. The 29 functions and their counts are in
     `references/model-spec-format.md`, "Argument counts"; a function outside that list is not counted.
   - `references/helper-patterns.md` shows the ten common deep formulas (H1 to H10) and the helpers that replace
     them: a lookup with a fallback, a nested-IF ladder, a date window, a cap and a floor, and others.
   - `ROTM split` rewrites a formula over the depth limit, and a formula over the length limit only when it is a
     chain of `+` and `-` terms; any other over-long formula gets `SPLIT-SHAPE` (exit 1) and is rewritten by hand
     (H10). The cells split writes on `split_helpers` get the dictionary's look.
     - A part inside IFERROR or IFNA: for a lookup (INDEX with MATCH inside the guard) split writes the H5 pattern
       of `references/helper-patterns.md`: helper 1 holds the match position or 0, helper 2 a position INDEX can
       read, helper 3 holds 1 when the guard would replace the result at that position, and the host returns the
       guard's value when helper 1 is 0 or helper 3 is 1, so the guard stays on the INDEX part too (v5). A lookup
       inside IFNA that sits inside IFERROR gets `SPLIT-GUARD`. Any other part under IFERROR or IFNA gets
       `SPLIT-GUARD` (error, exit 1, nothing written) whatever the current values are; it names the guard and the
       part.
     - A part in the second or third argument of IF: the helper carries the IF's condition,
       `IF(<condition>,<part>,0)` or `IF(<condition>,0,<part>)`, so it is calculated only where the IF takes that
       branch (v6). When the helper cannot carry the condition within the depth and length limits (the depth limit
       is 1, the part lies in a branch of two or more IFs, the part is a guarded lookup, or a helper or the cell
       would be over the length limit), split writes nothing and reports `SPLIT-GUARD` (error, exit 1), which names
       the IF and the part. A part in the condition is moved as before.
     - A helper cell that would hold an error value which another function of the original formula (such as ISERROR
       or CHOOSE) kept out of the result also gets `SPLIT-GUARD` (nothing written).
     - Two parts share one helper only when their texts are equal with text literals compared exactly (`"x"` and
       `"X"` get two helpers).

5. **P5 Check.** Read `references/checks.md`.
   - Build writes, for every raw table: the row count against the contract, each control total, empty cells in
     never-null columns, raw rows not covered by helper rows (`<t>_cover`), helper rows below the last raw row
     (`<t>_beyond`), and repeated keys (`<t>_keys`, compared as exact text); and the master check (`checks!C2`,
     linked in C2 of every block sheet).
   - Add one check per calculation block against an independent total: the block's grand total against the raw
     column's sum, a second path through another dimension, or the input grid it reads. A block no check reads
     makes build warn `W-NO-CHECK` (strict exit 1). Give a check `"format"` (for example `amount2`, `rate2`) so
     its values do not show as General.
   - An alert does not count as a block's check: a block that only alerts read still gets `W-NO-CHECK`.
   - A check whose actual and expected compile to the same expression, or to links to the same cell, can never
     fail: build warns `W-SELF-CHECK` (strict exit 1), and `audit` with a manifest reports the same; point one side
     at an independent total (a raw column total, a control total or another block's total).
   - Counting a block's numeric cells (`COUNT`) is not an independent total: it holds whatever values the cells
     show.
   - A block of ratios gets a check of each cell. The summed product (`SUMPRODUCT(<ratio grid>,<denominator grid>)`
     against the sum of the numerator where the denominator is not 0) does not find a grid in which every cell holds
     the overall rate, because that grid gives the same sum. Add a block whose cell is the absolute difference
     between the ratio times its own denominator and that cell's numerator,
     `ABS({g:<ratio>}*{g:<denominator>}-({g:<denominator>}<>0)*{g:<numerator>})`, and an `at_most` check that its
     largest value (`MAX({blk:<that block>})`) is at most `{in:check_tolerance}`. A cell whose denominator is 0 shows
     0 % and gives 0 in that block, so it is left out, as in the summed product. The worked example has both checks
     (`references/checks.md`).
   - A check expression (actual or expected) that holds IFERROR or IFNA makes build warn `W-CHECK-GUARD` (strict
     exit 1), and `audit` reports `W-CHECK-GUARD` at the check cell: an error in an operand must show in the result
     (`references/checks.md`, rule 4).
   - A check expression (actual, expected, min, max or bound) that holds ROUND, ROUNDUP, ROUNDDOWN, MROUND, INT,
     TRUNC, CEILING or FLOOR (also CEILING.MATH, CEILING.PRECISE, ISO.CEILING, FLOOR.MATH, FLOOR.PRECISE) makes
     build warn `W-CHECK-ROUND` (strict exit 1), and `audit` with a manifest reports `W-CHECK-ROUND` at the check
     cell: a check compares the unrounded difference with the tolerance (`references/checks.md`, rule 3). When a
     business rule needs rounded values, round them in a block.
   - A check holds 0 (shown `OK`) or 1 (shown `FAIL` in red). Alerts (`"severity": "alert"`) are shown apart and
     are not part of the master check: an alert's Result shows `OK`, or `ALERT` in red when the alert is raised,
     never `FAIL`. `check` and `audit` report a raised alert as `C-ALERT` (info).
   - A block that sums or counts by label (SUMIFS, COUNTIFS and the other criteria functions) adds every row whose
     value Excel's criteria matching takes as equal to the label (`001` and `1`, `North` and `north`, a label
     with `*` or `?`). Build and check warn `D-CRITERIA` and name both values; make the labels unambiguous
     (`references/helper-patterns.md`, "Labels that Excel's criteria matching merges"). A label that Excel's
     criteria matching does not match with itself (for example the texts TRUE and FALSE in any case, which Excel
     reads as logical values; a label from a cell that starts with =, < or >, which Excel reads as a comparison; a
     label that holds `~*` or `~?`, which Excel reads as a literal * or ?) leaves the rows with that label out of
     the sum. Build and check warn `D-CRITERIA` with the reason `self_mismatch` and name the label and the first
     raw row. For TRUE, FALSE and a label that starts with an operator, use a helper key with a fixed prefix on both
     sides; for a label that holds ~, * or ?, use helper keys that replace these signs by fixed texts on both sides
     (`references/helper-patterns.md`, "A label that Excel's criteria matching does not match with itself").

6. **P6 Style from the dictionary.** Read `references/style-dictionary.md` when a style question comes up.
   - Build styles every cell by its cell type from `references/style_dictionary.json`: Arial everywhere; size 8 on
     raw, helper and calculation sheets ("a brick wall of data"), 9 on cover, inputs, output, checks and notes
     sheets; column headers bold on light grey D9D9D9; row headers on the lighter grey F2F2F2; inputs on pale
     yellow FFF2CC; no other fill.
   - A sheet may set `"font_size": 8` or `9`. No other size is accepted. Max: 8 for "a brick wall of data", 9
     where "there are some white spaces". So a calculation sheet with white space (few rows and columns) gets
     `"font_size": 9` in its sheet entry of `model.spec.json`; size 8 is for dense sheets.
   - Build prints the information note `I-SPARSE-SHEET` (it does not change the strict exit code) for a calculation
     sheet that takes size 8 from its role and holds fewer populated cells than `rules.sparse_sheet_cells` in the
     style dictionary (100 cells). The note names the sheet and the setting to write. Build does not choose the
     size from the sheet's density; that choice is left to Max.
   - The audit compares six style attributes of each cell with the dictionary: font name, size, font colour,
     bold, fill and number format (`references/style-dictionary.md` section 5). Borders and alignment are written
     by build, and by `theme` with `--borders` and `--align`, and are not audited: look at them in the renders
     (P7). The audit compares the fill stored on each cell; it does not read the colours of conditional-format rules
     (`theme` reports such rules as `T-CF`). With a manifest, any conditional-format rule is `S-CF` (a warning, one
     per sheet), because build writes none. A colour drawn by such a rule is seen only in the renders.
   - Raw columns show the contract column's `format` when it names one; a `number` column whose landed values hold
     a fraction shows two decimals (`amount2`), so 7.25 is not shown as 7. The audit checks the format build
     recorded for each raw column.
   - Never style a cell by hand. A project changes the look in `MODEL/style_overrides.json`
     (`{"rotm_dictionary_override": 1, "use": ["input_blue_font"], "set": {...}}`), then rebuilds.
   - A workbook the plugin did not build: `ROTM theme <workbook>` (values and formulas are compared before and after
     by code; the original is not changed).
   - theme, compat and split compare formulas and typed values exactly and calculated values within
     `--calc-tolerance` (relative, default 1e-9); a cell that holds a volatile function (RAND, RANDBETWEEN,
     RANDARRAY, NOW, TODAY, OFFSET, INDIRECT, CELL, INFO) or depends on one is left out of the value comparison,
     and the report says how many cells were left out and names the first.

7. **P7 Audit, recalculate, look.**
   - The folder commands (`audit MODEL`, `check MODEL`, `status MODEL`) and the hooks use the workbook named in
     `MODEL/rotm_model.json` (field `workbook`). Build keeps that field equal to the specification's `output`
     (default `model.xlsx`); `build -o <other file>` does not change it, so after such a build name that file in
     the commands. Below, `<workbook>` is the workbook the build report names.
   1. `ROTM audit MODEL --strict` must exit 0.
   2. `ROTM check MODEL --strict` recalculates the workbook in Excel, reads every check back and lists each one
      (`checks: H of N hold; alerts: S of A show a value`; `ROTM status MODEL` prints the same line). It must exit
      0. An alert counts as showing a value only when its cell holds a number other than 0. A check or alert cell
      with no saved value is counted as not calculated: a workbook with no saved values gives
      `checks: not calculated (0 of N have a value)`, some cells without a value give
      `checks: H of N hold, K not calculated (no value)`, and ` (cached values)` is added when the run did not
      recalculate and at least one cell holds a value. A formula cell that holds no value is the error
      `V-NO-VALUE`, in `check`, `recalc` and `audit`. Its fix says what to do:
      - `check` and `recalc`, a cell Excel names as circular (the message starts `no value (circular reference)`):
        a formula that refers to itself through other cells has no value: find the cycle with
        `ROTM trace <workbook> <sheet>!<cell>` and break it. Excel names one cell of a cycle per sheet; the
        other cells of that cycle get the same message and fix.
      - `check` and `recalc`, another formula Excel saved without a value (the message `no value`): recalculate
        the workbook (`ROTM check MODEL`) and look at the formula and its inputs (`ROTM trace`).
      - `audit` (the saved file does not say which cells are circular): `ROTM check MODEL` recalculates the
        workbook in Excel and names a circular reference when there is one; otherwise look at the formula and its
        inputs (`ROTM trace`).
      - A formula whose result is empty text and that holds a volatile function (RAND, RANDBETWEEN, RANDARRAY,
        NOW, TODAY, OFFSET, INDIRECT, CELL, INFO) or depends on one is not reported. A cell whose formulas'
        references show that it lies on a cycle of references (for example A1 `=B1+TODAY()*0` and B1 `=A1+1`) is
        still reported.
      - Excel started but could not open or calculate the workbook: `X-OPEN`, an error, exit 1. The check cells
        were not read and nothing was saved. The most likely cause is a formula Excel rejects. The strict audit
        finds two such causes: an incomplete formula (`F-PARSE`) and a listed function with too few or too many
        arguments (`F-ARGS`, `references/model-spec-format.md` "Argument counts"). Run
        `ROTM audit "<workbook>" --strict`, correct the expression in `model.spec.json`, rebuild, and run
        `ROTM check MODEL` again. For another cause, open the workbook in Excel and read the message Excel shows.
        This is not "not recalculated": exit 3 and
        those words are for Excel missing, `--no-excel` and a workbook with functions only Google Sheets has (below),
        and Excel busy is exit 5.
      - A workbook that holds a function only Google Sheets has: check reports `X-SHEETS-ONLY` (information) with the
        functions and their cells, does not open the workbook in Excel, leaves the file byte for byte and exits 3
        (not recalculated). Say in the report "audited, not recalculated: functions only Google Sheets has".
      - `check` (after a recalculation) and `audit` (on the saved values, with a manifest) compute every check
        again from the saved values of its own operand cells, and the master check again from the result cells.
        They also compare the formula of each result cell and of the master check with the formula build wrote
        (recorded in the manifest). A difference is `C-RECOMPUTE`, an error that names the check and its cell, for
        example a result cell replaced by `=0` while its actual and expected differ (`references/checks.md`). The
        fix: rebuild with `ROTM build MODEL --force`, then run `ROTM check MODEL` again.
   3. `ROTM render <workbook>` (the workbook the build report names, `MODEL/model.xlsx` by default) writes one PNG
      per sheet to `MODEL/reports/render/`. Open every PNG with the
      Read tool, one per sheet, not a sample, and look: Arial at the size the contents table on the cover gives
      for that sheet, column headers light grey, row headers a lighter grey, no colour the dictionary does not
      define, no text cut off (a header hidden under a filter button, a note row too short, a number shown as
      `###`), the layout readable. Fix the specification and rebuild until it is right.
      - A render covers rows 1 to 200 and columns A to AN (40 columns) of each sheet.
        `MODEL/reports/render/render.json` holds the list `files`, one entry per rendered sheet, with the fields
        `sheet`, `file`, `truncated` (true when the sheet's used range is larger than the render), `last_row` and
        `last_col` (the last row and column of the used range). The line render prints for such a sheet ends with
        "the sheet's used range ends at row <last_row>, column <last_col>: rows or columns beyond the rendered range
        are not shown".
      - For such a sheet, either render it again with `ROTM render <workbook> --sheets <sheet> --max-rows
        <last_row> --max-cols <last_col>` and look at that PNG, or say in the report which rows and columns were
        not looked at (for example "calc: columns AO to AZ not looked at").
      - Raw and helper sheets hold one landed value or one formula per column, the same on every row. For them the
        first 200 rows are enough; the report says so (for example "h_sales: rows 2 to 200 of 601 looked at; the
        rows below repeat the same formulas").
      - Build prints `I-TEXT-WIDTH` (information; the strict exit code does not change) for a label, unit or note text
        that is wider than the room its cell has (the column's width and, for text that may run on, the free cells to
        its right). The note names the JSON path, the length of the text and the room. Shorten the text or move the
        detail into a note and rebuild; then look at the render.
   4. Without Excel (`check` exits 3): say in the report "the workbook was audited but not recalculated"; do not
      say that the checks hold.

8. **After a refresh** (the raw data changed in Excel or Google Sheets).
   1. `ROTM check MODEL --refresh --strict` refreshes every query through Excel, waits until it has finished,
      applies the raw columns' number formats and the dictionary's raw-cell look to the loaded rows again,
      recalculates and reads every check.
      - Give `--strict`: without it a warning such as `X-NO-QUERY` leaves exit 0, so read the report's refreshed
        value (`data.refreshed` in `MODEL/reports/check.json`). `X-NO-QUERY` stays a warning (an error with
        `--accept-refresh`).
      - Before Excel starts it refuses (exit 2, "nothing was refreshed") while a raw table loaded by a query has a
        contract that is missing, invalid, proposed, stale, or agreed only on the example channel; it names each
        table with the commands that get the contract agreed (`ROTM contract show`, then `ROTM contract agree`
        with the user's reply). It also refuses a query written for another contract than the agreed one.
      - Before Excel starts it also refuses (exit 2, nothing refreshed) a raw table whose `queries/<t>.pq.m`
        differs from the query text attached to the workbook (the hash the attach recorded in the manifest):
        `land` and `land --keep-queries` change the query file only, and the workbook keeps the query it was
        given. Run `ROTM land MODEL --table <t> --power-query` (it attaches the file's query), then refresh.
      - A raw table that the manifest records as loaded by Power Query, whose sheet holds no query table at A1:
        `X-NO-QUERY` for that table, a warning (an error with `--accept-refresh`). It was not refreshed and
        nothing is accepted for it. Attach the query again (`ROTM land MODEL --table <t> --power-query`) and
        refresh.
      - A workbook with no Power Query query or connection: `X-NO-QUERY`, a warning (an error when
        `--accept-refresh` is given too): the raw sheets are static snapshots and nothing was refreshed. Land new
        rows (`ROTM land MODEL --table <t> --csv <file> --force`, with `--control <t>.<column>=<source total>`
        when the source total changed) and rebuild, or attach a query (`ROTM land MODEL --table <t>
        --power-query`).
      - A query changed in Excel's Power Query editor (or in any other way than `rotm.py`): the attach records the
        hash of the query formula Excel gives back (`query_formula_sha256` in the manifest), and `check --refresh`
        reads the formula again through Excel before the refresh and compares. On a difference it reports
        `X-QUERY-CHANGED` (a warning; an error with `--accept-refresh`), refreshes no table and accepts nothing.
        Put the change into `queries/<t>.pq.m` and run `ROTM land MODEL --table <t> --power-query`. A manifest
        written before v6 has no such hash: a note says that the workbook's query was not compared. For a query
        attached by hand and carried over by build (`query_attach` `hand`), the fix is different: to keep the
        change, rebuild (`ROTM build MODEL --force`; build reads the query from the workbook, attaches it again and
        records its formula); to drop the change, change the query back in Excel's Power Query editor. With Excel,
        `check --accept-refresh` reads the query back and compares it too, also when it is given without
        `--refresh`: on a difference it reports `X-QUERY-CHANGED` as an error, accepts nothing, and `raw/<t>.csv`,
        `raw/<t>.controls.json` and the manifest stay byte for byte. Without Excel, or for a manifest without the
        hash, the acceptance prints the note that the query was not compared. A value typed into a raw sheet that a
        query loads is not found by an acceptance alone: run `check --refresh` first, which loads the rows from the
        source again. With Excel, `check --refresh` and `check --accept-refresh` also compare each query that a query
        attached by hand reads (the connection-only queries build carried over, recorded in the manifest as
        `query_reads`) with the hash build recorded: a changed, missing or unreadable one is `X-QUERY-CHANGED` and
        names that query; the table is not refreshed, nothing is accepted, and `raw/<t>.csv`,
        `raw/<t>.controls.json` and the manifest stay byte for byte.
      - `I-HIDDEN-DECIMALS` (information; the exit code does not change): a number column of the new rows holds
        more decimals than its number format shows. The values are stored and calculated in full. A rebuild shows
        them when build chose the format; when the contract names the format, agree another format in the
        contract, then rebuild.
   2. More raw rows than helper rows: it reports `H-COVERAGE`. Run the fix it prints:
      `ROTM extend MODEL --table <t>` (copies the last helper row down; by hand: copy the last helper row down to
      the last raw row). From zero helper rows, `extend` writes the first helper row from the formulas the
      manifest keeps, then fills down.
   3. Fewer raw rows than helper rows: it reports `H-BEYOND` (an error) and the check `<t>_beyond` shows FAIL,
      because helper rows below the last raw row read empty cells as 0 and would feed the calculations. Run
      `ROTM extend MODEL --table <t>`: it clears those helper rows. After a refresh that returns no rows it clears
      every helper row, row 2 included.
   4. `ROTM check MODEL --accept-refresh --control <t>.<column>=<new source total>` checks the new rows against the
      contract and only then records them as the landed data. The accepted total is kept in
      `MODEL/raw/<t>.controls.json` for that snapshot, with the hash of the agreed contract in force; the contract is
      not changed. Later builds and checks of the same snapshot use that total while the contract is not agreed
      again.
      - The control total used for a table and column is, in this order: `--control` given in this run; else the
        total accepted for this snapshot in `raw/<t>.controls.json` under the agreement in force; else the agreed
        contract's total. A total accepted under an earlier agreement (the contract was agreed again since, for
        example with another total) is not used: `land`, `build`, `check` and `lift` print a note that names it,
        and when the rows do not meet the contract's total, `L-TOTAL` (`land`) or `D-TOTAL` (`check`) names both
        totals. Give `--control` with the source's current total; it is recorded under the agreement in force.
        Never the inputs sheet: a value typed into the inputs sheet's control cell is not a control total. When that
        cell holds another value than the control used, `check` warns `D-CONTROL-INPUT` (strict exit 1) and names
        the cell and both values.
      - A failing `<t>_total_<column>` check: find the missing or extra rows in `raw_<t>` (compare them with the
        source system); after a refresh with a new source total run `ROTM check MODEL --accept-refresh --control
        <t>.<column>=<source total>`.
      - Changed rows are accepted only when they differ from the rows the workbook held when `rotm.py` last wrote
        it. When `raw_<t>` still holds those rows and `raw/<t>.csv` holds rows landed after that (`land --force`),
        `check` reports `R-CHANGED`, accepts nothing and does not change `raw/<t>.csv`: refresh the query
        (`ROTM check MODEL --refresh --strict`) or rebuild from the snapshot (`ROTM build MODEL --force`). A
        manifest built before v3 records no workbook digest: nothing is accepted until one rebuild
        (`ROTM build MODEL --force`).
      - When `raw/<t>.csv` was landed after `rotm.py` last wrote rows into the workbook (the manifest's landed digest
        differs from its workbook digest, for example after `land --force`), `check --accept-refresh` accepts no
        changed rows, whatever `raw_<t>` holds: `R-CHANGED` says that `raw/<t>.csv` was landed after the workbook
        was last written, and `raw/<t>.csv`, `raw/<t>.controls.json` and the manifest stay byte for byte. Rebuild
        from the landed snapshot (`ROTM build MODEL --force`), then refresh (`ROTM check MODEL --refresh --strict`)
        and accept.
      - `--refresh` asked for and no refresh ran (Excel missing or busy, the refresh failed, or the workbook has no
        query): no table is accepted in that run; `raw/`, the controls files and the manifest are not changed, and
        the exit code is not 0.
      - Changed rows are accepted only from a live query table. When the manifest says `power_query` but the
        workbook holds no query table at A1 of `raw_<t>` (converted to a range or deleted), `check` reports
        `R-CHANGED`, which says so, and nothing is accepted: `raw/<t>.csv`, `raw/<t>.controls.json` and the
        manifest stay byte for byte. Attach the query again and refresh, or rebuild from the snapshot.
      - `CT-VERSION` (an error, in `check` and in `audit` with a manifest): the workbook was built from another
        version of its contract (the manifest's `contract_sha256` differs from the agreed contract's hash). Land
        again if the rows change (`ROTM land MODEL --table <t> --csv <file> --force`) and rebuild
        (`ROTM build MODEL --force`). At an acceptance nothing is accepted for that table in that run, and
        `raw/<t>.csv`, `raw/<t>.controls.json` and the manifest are not changed.
      - `D-TYPE` (an error; `R-TYPE` in `audit`): a cell of an integer, number, date, datetime or boolean column
        that the workbook stores as text (the text `1` is not the number 1), or a cell of a text column that the
        workbook stores as a number (the text `901` is not the number 901) or as a logical value (the text `TRUE`
        is not the logical value TRUE). Land the data again and rebuild; a
        raw sheet holds landed values only.
      - `D-KEY-SEPARATOR` (a warning): a key part of the raw rows holds `|`, the separator of the joined key in
        `h_<t>` column `dup_key`, so the workbook's key check may show a repeat that is none; the key check of
        `rotm.py check` stands. Rebuild (`ROTM build MODEL --force`): build then leaves the key check to
        `rotm.py check`.
   5. After a refresh by hand in Excel (Data > Refresh All), run `ROTM check MODEL --refresh --strict`: it applies the
      raw formats again and validates the new rows. A refresh by hand keeps the formats (measured: Excel copies the
      column formats to new rows while Preserve cell formatting is on), but it neither validates nor records the
      new rows.

## Commands

Max's commands are skills of the plugin, invoked by name. Each runs the matching `rotm.py` subcommand.

| Slash command | What it does | Subcommand |
| --- | --- | --- |
| `/rule-of-thumb-model:contract` | propose a contract per raw table; record the user's agreement in their words | `contract propose`, `agree`, `show`, `status` |
| `/rule-of-thumb-model:build` | build the workbook from the specification (the procedure above when none exists) | `init`, `land`, `build` |
| `/rule-of-thumb-model:theme` | apply the style dictionary to an existing workbook | `theme` |
| `/rule-of-thumb-model:audit` | audit any workbook against the rules | `audit` (also `scripts/formula_audit.py`) |
| `/rule-of-thumb-model:check` | raw sheets against contracts, coverage, Excel recalculation, every check read back | `check`, `extend` |
| `/rule-of-thumb-model:compat` | make a workbook work in both Excel and Google Sheets | `compat` |
| `/rule-of-thumb-model:trace` | the chain from one cell back to the raw rows, in words | `trace` |
| `/rule-of-thumb-model:split` | rewrite one over-limit formula as helper cells | `split` |
| `/rule-of-thumb-model:lift` | write a Python script that reproduces the helpers and ties out to the workbook | `lift` |
| `/rule-of-thumb-model:diff` | formulas, inputs and outputs that changed between two versions | `diff` |
| `/rule-of-thumb-model:document` | write the model map (`MODEL_MAP.md`) | `document` |

Other subcommands: `status MODEL` (state of contracts, build, audit, check), `recalc <workbook>`, `render
<workbook>`, `formula "=..."`, `selftest` (the plugin's own tests). `selftest` writes one line to stderr when each
step starts and when it ends (the step name and the elapsed time), and about every 5 minutes during the pytest step
(with the count of tests done when pytest's output gives it); stdout and the JSON report are as before. A step
that is skipped or not run writes one line only (`selftest: step <name> skipped in 0 s`).
`ROTM <subcommand> -h` lists every option.

Exit codes of every subcommand:

| Code | Meaning | What to do |
| --- | --- | --- |
| 0 | done; with `--strict`, no warning either | continue |
| 1 | findings: an error, or with `--strict` any warning (an output file is still written) | fix each finding; every finding line names the sheet, the cell and the fix |
| 2 | refused: bad input, nothing written | change what the message names |
| 3 | not available or not verified (Excel missing, so not recalculated) | say "not recalculated" in the report |
| 4 | internal error (one line; the traceback is in the state folder's `last_error.txt`) | report it; do not work around it by editing files |
| 5 | Excel busy: another job holds the Excel lock | wait and run again |

## Quick reference

| Want | Do |
| --- | --- |
| a new model from CSV extracts | `init`, then P1 to P7 |
| start from a known model | `ROTM init MODEL --template budget_vs_actual` (or `starter`, `rolling_forecast`, `price_volume_mix`, `ar_aging`) (`ROTM init --list-templates` prints each template's name and its folder. `starter` lies in the skill folder (`<SKILL_DIR>/templates/starter`); `ar_aging`, `budget_vs_actual`, `price_volume_mix` and `rolling_forecast` lie in the plugin's `templates/` folder and are listed only when the whole plugin is installed. Read a template's files (its README, contracts and specification) in `MODEL` after `init` has copied them there.) |
| to see a finished model first | `<SKILL_DIR>/examples/sales_vs_target/` (contracts, specification, `model.xlsx`, renders). The example lands its two CSV files as static snapshots that stand in for query results, so that it builds without Excel; its Power Query variant is in `examples/sales_vs_target/task.md` |
| to know whether a formula is too deep | `ROTM formula "=..."` |
| a formula is refused as too deep | move the named part into a helper column, helper grid, line item or measure (`references/helper-patterns.md`) |
| a typed number refused in a formula | add an input item and write `{in:<id>}` |
| to audit a workbook someone built by hand | `ROTM audit <workbook> --strict` (raw sheets, helper columns and checks are inferred and marked "(inferred)"; when the workbook has check cells, each calculation block that no check cell reads is `C-BLOCK` (a check that reads the block's total cell, or a link to it, counts as reading the block; at most two such cells are followed); a run of links (each cell only a reference to one cell) is not a block) |
| to restyle that workbook | `ROTM theme <workbook>` (writes `<stem>_theme.xlsx`; `--in-place` keeps a `.bak_` copy) |
| a workbook for Google Sheets too | `ROTM compat <workbook> --report-only`, then `ROTM compat <workbook>` |
| where a number comes from | `ROTM trace MODEL/model.xlsx summary!F11` |
| one deep formula in a workbook built by hand | `ROTM split <workbook> calc!D7` |
| the model as a Python script | `ROTM lift MODEL --run` (the script reproduces PMT, PV, FV and NPV; before the script is written, each function of the model it does not reproduce is `LIFT-UNSUPPORTED`, an error with its cell count and first cell; `references/functions.md`, column Lift). A number ties out when its absolute difference from Excel's value is at most the printed tolerance plus 1e-13 times the larger value (the rounding of double-precision arithmetic; it allows 0.1 at 1,000,000,000,000); the header line of the tie-out prints both numbers and the largest difference, also when every cell ties; the generated script's `--all` lists every compared cell and its `--json` holds one record per compared cell |
| what changed between two versions | `ROTM diff old.xlsx new.xlsx`; `diff` reports `saved_values_compared` (both files hold values saved by Excel) and `recalculated` (true only when `reports/status.json` of each file's model folder records a passing `check` or `recalc` of that file's current version); for a workbook set to manual calculation it prints a note that names the file; while that file's version has no passing `check` or `recalc` on record, the note says that its saved values may be out of date and to run `rotm.py recalc` on both files before the diff. |
| the model's map for a reader | `ROTM document MODEL` |
| blue input font instead of the yellow fill | `"use": ["input_blue_font"]` in `MODEL/style_overrides.json`, rebuild |
| the master check shows FAIL | `ROTM check MODEL`: it names each failing check with its actual and expected values |
| more or fewer raw rows after a refresh | "After a refresh" above |
| no Excel on the machine | build and audit work (a rebuild over a query attached by hand is refused: P2); `check`, `recalc` and `render` exit 3; report "audited but not recalculated" |

## Common mistakes

- **Analysis inside the SQL or the Power Query steps.** A rate, a flag or a mapping in the query cannot be clicked
  and followed in the workbook. The query shapes to the contract; the rest is helper columns.
- **SQL before the contract is agreed.** The contract gate hook refuses the SQL file; `land` refuses the data.
  Propose, show, wait, record the user's words.
- **Recording an agreement the user did not give**, or a paraphrase of it. Pass their words exactly. In a headless
  run without the user's words, stop at "proposed". An agreement covers the version it was given for; a changed
  contract needs the user's reply again.
- **Inventing a key or allowing nulls to get bad rows landed** (a row-number column as the key, `null_rule`
  `allowed` for a column that should be full). The key check then always shows 0, the null check disappears, and
  the master check shows OK with the defects unreported. Agree them as known exceptions (`--key-repeats`,
  `--known-nulls`).
- **Getting past a `land` refusal alone.** Report the rows the refusal names to the user and stop. No made-up key,
  no loosened null rule, no new row count or control total without the user: each is a contract change, shown
  again and agreed again in the user's new words. Recording the earlier reply for the changed contract is not an
  agreement.
- **Prose in `source_expression`.** It goes into the SELECT list; `land` writes `NULL` for it and warns
  `L-SQL-EXPR`. Prose belongs in `description`.
- **A control total computed from the landed rows.** It always matches and checks nothing. Ask for the source
  system's own total.
- **Formulas beside or below a query table.** A refresh moves or overwrites them. The raw table stays alone on its
  sheet; helper columns read it by row position from the helper sheet.
- **A typed number in a formula** (`*1.1`, `/1000`). Put it on the inputs sheet and reference it.
- **`IFERROR(INDEX(..., MATCH(...)))` in one cell.** Depth 3. Put the MATCH in a helper column, then INDEX it.
- **Editing `model.xlsx` with a script or in Excel**, or styling a cell by hand. The raw-sheet guard hook refuses
  such a command; the next build refuses to overwrite the changed file without `--force`, also after a `check` of
  the edited file. Change the specification or the dictionary override and rebuild.
- **Typing new values into the inputs sheet.** Inputs are changed in `model.spec.json` and the model is rebuilt.
  A value typed into the workbook's inputs sheet is not kept by a rebuild, and build refuses to overwrite such a
  file without `--force` (with `--force` the edited file is kept as `model.bak_<time>.xlsx`). `check` (after
  recalculation, and on the saved values without Excel) and `audit` (with a manifest) read each scalar input that
  declares a valid range or valid values from the workbook: a saved value outside it is `V-INPUT-RANGE` (error),
  which names the cell, the value and the range, and the input's note when it has one. No row is added to the
  checks sheet.
- **Appending rows to `raw/<t>.csv` by hand.** Land them with `ROTM land MODEL --table <t> --csv <file> --force`;
  add `--control <t>.<column>=<source total>` when the source's total changed.
- **An input that converts a unit on one side of a comparison only** (a display scale that divides the actual
  amounts while the budget is typed in KRW thousand). An input that converts a unit must apply to both sides of
  every comparison, or be fixed: give it `"valid": {"min": 1000, "max": 1000}` and say in its label that it is
  fixed. Build refuses an input value outside its own `valid` range (exit 2).
- **A display scale that the user can change while the unit labels are fixed text.** An input that scales
  displayed amounts is fixed, or every unit label reads it. A scale from 1 to 1,000,000 under labels that say "KRW
  thousand" shows amounts under a wrong unit with every check passing. Give such an input
  `"valid": {"min": 1000, "max": 1000}`, the label "Display scale (fixed at 1000)" and a note that every unit label
  is in KRW thousand; the worked example and every shipped template do so.
- **Claiming the checks hold without Excel.** Without recalculation the workbook holds no values; say "not
  recalculated".
- **Not looking at the renders.** The audit cannot see a header hidden under a filter button or a cut-off note.
  Open every PNG.

## Hooks (when the plugin is installed)

The plugin adds four hooks. They act only inside a model folder (one holding `rotm_model.json`); elsewhere they
print nothing. They are not a security boundary: a hook whose own script fails lets the action through and prints
one line.

| Hook | Event | Refuses or reports |
| --- | --- | --- |
| contract gate | before Write, Edit, Bash, PowerShell | SQL or Power Query text for a raw table whose contract is not agreed; any other `.sql` or `.m` file in the model folder; a text edit of an agreed contract; `contract agree` with a reply that reads as a refusal (its first word is negative, or it holds a negated agreement anywhere: not agreed, don't agree, cannot agree, disagree, 동의하지 않, 동의 안), or that holds a word that refuses or stops: reject, decline and refuse (also rejected, rejection, declined, refused, refusal); hold off and hold on; a negation before run, proceed, start, land, continue or go ahead (do not run the query yet); please wait, wait until and wait for (no need to wait is not a stop); 거절, 반려, 거부, 보류, 기다려, 하지 마 (no list of words is required in an agreement, and the reply need not name the table), that asks for a change, or that postpones the decision with no agreement (will review, get back to you, let me check, 나중에, 검토 후 and the other forms of P1 step 6: no agreement was given; wait for the user's reply), or that is only a question (every sentence a question and no statement: answer the question and wait for the user's reply); with a reply the user never typed; when the reply is part of a longer user message, the sentences that hold it and the whole message are read for a refusal in the same way, and a refusing sentence that names another table than the one given with `--table` does not count against this table; with a reply typed before the latest `contract propose` or edit of that table's contract in this session (with no proposal in the transcript: before the contract's `proposed_at`), when the session transcript can be read; proposing and agreeing in one command |
| raw-sheet guard | before Write, Edit, Bash, PowerShell | a text edit of `raw/*` or of a workbook; a shell command other than `rotm.py` that writes into `raw/` or saves a model workbook. A copy command (`cp`, `copy`, `Copy-Item`, `xcopy`, `robocopy`, Python's `shutil` copy) to a new workbook name that does not exist yet is let through; a copy over an existing workbook, and every write into `raw/`, are refused |
| audit on write | after Bash, PowerShell | runs `rotm.py audit <workbook> --strict` on each workbook the command changed and shows the findings. It writes `(recalculated)` when the model's `reports/status.json` holds a passing `check` or `recalc` record that recalculated the workbook's current version (the same sha256), and otherwise `(the audit read the saved values; recalculation was not verified by this hook)` |
| finish check | at the end of a turn | once per turn end: refuses "done" when a workbook this session wrote has no clean strict audit or no passing `check` of its current version, unless the last message says "not recalculated" (which excuses the recalculation only). A stop it lets through while a workbook is still unverified shows the user one line: `rule-of-thumb-model finish check: not verified at the end of the session: <workbook>: <what is missing>.` (seen in a live `--plugin-dir` session as the notice "Stop says: ..."). A stop let through because the last message says "not recalculated" is remembered for each such workbook's sha256: a later stop in the same session does not refuse again for a workbook unchanged since that message; a workbook written after it is judged again. A passing audit or check counts only while `raw/` and the agreed contracts of the model folder are as they were when it ran (each `audit`, `check` and `recalc` record in `reports/status.json` holds their fingerprint, `inputs`): after a landing or a new agreement the stop is refused once, and the refusal names the table and the command that brings the workbook up to date (`build`, or `check --refresh` for a table loaded by a query). |

A skill folder copied without the plugin has no hooks; the scripts' own refusals (`land` and `build` refuse an
unagreed contract) still apply, and the rules above still apply to you.

## What is and is not verified

The build PC: Windows 11, Python 3.14, openpyxl 3.1.5, Excel 16.0. Version v9 of 2026-11-26.

- The runs made on the final v9 files are listed in the first subsection.
- An item of the verified list holds for v9 only when the tests it names passed in one of those runs.
- One measurement was made on an earlier version and says so: the recalculation time of the `dup` column.

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
  and after a refresh to fewer rows and `extend` (the load and every refresh apply the raw formats again); a refresh
  to fewer rows fails `<t>_beyond` and the master check (`H-BEYOND`) until `extend` clears the helper rows below the
  raw rows; after a refresh accepted with a new control total, `build --strict` and `check --strict` exit 0 and the
  inputs sheet holds the accepted total (`raw/<t>.controls.json`); after each accepted refresh the spend block's
  total on `calc` equals the total computed in Python from the new CSV. Tests:
  `test_v2_core.py::test_power_query_raw_table_keeps_its_formats_through_attach_growth_and_shrink`,
  `test_v2_raw_layer.py` and selftest step `f27_power_query` (60 rows, then 75, then 40, then a rebuild; run alone on
  the final v9 files, in the runs above).
- The acceptance rules of a refresh: after `land --force` with one changed row, `check --accept-refresh --strict`
  exits 1 with `R-CHANGED` and `raw/<t>.csv` keeps the rows `land --force` wrote; with `--refresh` and no Excel
  nothing is accepted and `raw/<t>.csv`, `raw/<t>.controls.json` and the manifest stay byte for byte; a total typed
  into the inputs sheet's control cell is not taken as the control (`D-TOTAL` and `D-CONTROL-INPUT`, exit 1) and is
  not written to the controls file; a contract agreed again with another total fails `check --strict` and
  `audit --strict` with `CT-VERSION` until the data are landed again and the model is rebuilt; after a refresh
  that was not accepted and a later `land --force` of another CSV with the same row count and total,
  `check --accept-refresh --control <t>.<column>=<total> --strict` does not exit 0, `R-CHANGED` says that
  `raw/<t>.csv` was landed after the workbook was last written, and `raw/<t>.csv`, `raw/<t>.controls.json` and the
  manifest stay byte for byte. Tests:
  `test_v3_raw.py::test_accept_refresh_after_land_force_keeps_the_csv` (Excel),
  `::test_accept_refresh_without_excel_accepts_nothing`, `::test_inputs_sheet_value_is_not_a_control` (Excel),
  `::test_contract_agreed_again_fails_check_and_audit_until_rebuild`,
  `test_v8_check.py::test_acceptance_after_a_later_landing_is_refused` (Excel),
  `test_v8_check.py::test_acceptance_with_a_manifest_digest_mismatch_accepts_nothing_without_excel`.
- The empty table: a header-only CSV under a contract agreed with `--allow-empty` lands, and `build --strict`,
  `audit --strict` and `check --strict` exit 0 with zero helper rows; through Power Query, 1 row, then 0, then 1,
  with `extend` after each refresh, `check --strict` exits 0 each time. Tests:
  `test_v3_raw.py::test_header_only_csv_builds_audits_and_checks`,
  `test_v3_raw.py::test_empty_table_through_power_query_one_zero_one` (Excel).
- The hand-attached query: a query table `q_<t>` attached at `raw_<t>!A1` in Excel without `rotm.py` is accepted
  by `check --accept-refresh --control <t>.<column>=<total> --strict` (exit 0) and recorded as attached by hand; a
  static table with rows edited by hand is still refused. Test: `test_v3_raw.py::test_hand_attached_query_is_accepted`
  (Excel).
- A query attached by hand carried over a rebuild (v4): with Excel, `build --strict --force` keeps the query's M
  text in `queries/<t>.hand.pq.m` and attaches it again with the same name; after the second CSV changes,
  `check --refresh` shows its values on the raw sheet and in an output cell, and the manifest still records
  `query_attach` `hand`. Without Excel the rebuild is refused (exit 2) and the workbook, the manifest and
  `raw/<t>.csv` stay byte for byte. Tests: `test_v4_build.py::test_hand_attached_query_is_carried_over_a_rebuild`
  (Excel), `test_v4_build.py::test_rebuild_over_a_hand_attached_query_without_excel_is_refused`.
- The first acceptance of a query attached by hand (v4): a query whose first load equals the landed snapshot is
  recorded by `check --accept-refresh --strict` (exit 0) as attached by hand, with its name, and the next
  `audit --strict` exits 0. Test: `test_v4_check.py::test_first_acceptance_of_a_hand_attached_query_records_it`
  (Excel).
- The criteria warning (v4): labels that Excel's criteria matching takes as equal (`001` and `1` with amounts +100
  and -100) give `D-CRITERIA` from `build --strict` and `check --strict`, which do not exit 0, and the rules agree
  with Excel's COUNTIFS. Tests: `test_v4_check.py::test_criteria_match_follows_excel_countifs` (Excel),
  `test_v4_check.py::test_check_warns_for_criteria_labels_that_excel_merges` (Excel),
  `test_v4_check.py::test_build_warns_for_criteria_labels_that_excel_merges`.
- The guarded-lookup split (v5): `split` of `=IFERROR(INDEX(D2:D3,MATCH(A2,C2:C3,0)),0)` at limit 2 writes the H5
  pattern with three helpers; after the split the host's value equals the original's with the key present, the key
  missing, the matched return cell changed to `=NA()` or `=1/0`, and the match position outside the INDEX range,
  with IFERROR and with IFNA, and no helper cell holds an error value that the guard hid. Tests:
  `test_v5_tools.py::test_guarded_lookup_split_keeps_the_result_of_the_original` (Excel, 10 cases),
  `test_v4_split.py::test_guarded_lookup_split_keeps_the_missing_key_result` (Excel).
- `lift` after an accepted refresh: after a refresh accepted with a new control total, `lift` takes the control
  total accepted for the snapshot it reads under the agreement in force from `raw/<t>.controls.json` and
  `lift --run` ties out. Test:
  `test_v3_lift.py::test_lift_ties_out_after_an_accepted_refresh_with_a_new_control` (Excel).
- `V-NO-VALUE` for a volatile formula: `=IF(TODAY()>1,"","later")` saved by Excel gets no `V-NO-VALUE` from
  `recalc` and `audit`; a circular pair in the same workbook still does. Test:
  `test_v3_core.py::test_volatile_empty_text_is_not_v_no_value` (Excel).
- Known exceptions: a model with one agreed key repeat and one agreed empty amount is landed with `L-KNOWN` naming
  lines 22 and 74 and line 12; after recalculation in Excel the checks `sales_keys` and `sales_nulls_amount` show
  actual 1 each and hold (`scripts/tests/test_v2_docs.py`).
- Key equality: `001` and `1`, `Case` and `CASE`, `M*` and `Monday`, `A?` and `AB`, and two 20-digit texts that share
  their first 15 digits are different keys in land, check, propose, the workbook's `dup` column (Excel) and lift; a
  true repeat gives `dup` 1 on the later copy only (`test_v2_core.py`, `test_v2_raw_layer.py`).
- The `dup` column (`SUMPRODUCT(--EXACT(...))`) adds to one full recalculation in Excel 16 on the build PC: 0.10 s at
  5,000 raw rows, 0.44 s at 10,000 rows and 36.06 s at 50,000 rows (measured 2026-11-25 during the v2 round; measured
  again on the same `dup` formula during the v3 round: 0.13 s, 0.48 s and 36.18 s with the column, 0.03 s, 0.04 s
  and 0.12 s without it; v4 to v9 keep the same formula, which was not measured again). Build writes it for tables of at most 5,000 rows
  (`formula_key_check_max_rows`); `rotm.py check` checks larger tables in Python by the same rule.
- Circular references: build refuses a specification whose helper columns, blocks, items or checks read themselves
  through others, naming the JSON paths; a hand-made circular pair fails `check --strict`, `recalc` and
  `audit --strict` with `V-NO-VALUE` on both cells (`test_v2_raw_layer.py::test_a_hand_made_circular_pair_fails_check_strict`,
  `test_v2_core.py::test_recalc_reports_a_circular_pair_and_not_an_empty_text_result`,
  `test_v2_audit.py::test_a_circular_pair_saved_by_excel_is_reported`).
- Audit of a workbook without a manifest: a calculation block that no check cell reads is `C-BLOCK` (inferred),
  tested on a fixture like the T5 case (`Settlement!B2:B6`); in a built workbook a check result replaced by a typed 0
  is `C-MISSING` (error) (`test_v2_audit.py`). Columns and rows of links (each cell only a reference to one cell)
  get no `C-BLOCK`, and the T5-like block is still reported
  (`test_v3_audit.py::test_link_columns_are_not_calculation_blocks`).
- theme, compat and split on a workbook with RANDBETWEEN: the 3 volatile cells are left out of the value comparison
  and named in the statement; a changed typed value is still reported. split turned the 40-term sum `=A1+...+A40`
  into 4 helpers and kept its value in Excel (`test_v2_preservation.py`).
- Hooks: the contract gate's refusal reading and reply order (`scripts/tests/test_v2_contract_hooks.py`,
  `test_v3_contract.py`).
- `lift`: for the worked example and each template, the generated Python script recomputes every helper column, block
  and check from `raw/*.csv`, and every compared cell ties out (difference at most the check tolerance 0.001 plus
  1e-13 times the larger value; the largest difference is printed) (`rotm.py lift <copy> --run` on copies of the six
  folders, in the runs above; `test_v4_lift.py::test_shipped_models_tie_out_in_every_cell`; selftest step `recalc`
  runs the same).
- `lift` reproduces PMT, PV, FV and NPV: 117 formulas (rates 0, 0.05/12 and -0.01; payment timing 0 and 1; edge
  cases) equal Excel's values within a relative 1e-9, and a PMT helper ties out; a function the script does not
  reproduce is `LIFT-UNSUPPORTED` before the script is written. Tests:
  `test_v3_lift.py::test_financial_functions_equal_excel` (Excel), `::test_pmt_helper_ties_out` (Excel),
  `::test_unsupported_function_is_named_before_the_tie_out`.
- The worked example's key numbers equal `examples/sales_vs_target/expected.json`, computed from the CSV files
  without the workbook (`test_examples.py::test_worked_example_numbers_equal_the_independent_calculation`).
- Landing an export with a source total (v5): `land --force --control <t>.<column>=<source total>` lands an
  export with more rows and one with fewer rows, each with a changed total, and records the given total and words
  for that snapshot's digest in `raw/<t>.controls.json`; a workbook copy that holds those rows and the new total in
  its control cell passes `check --strict` with no `R-CHANGED`, `D-TOTAL`, `CT-VERSION` or `D-CONTROL-INPUT`; a
  wrong `--control` gives `L-TOTAL`, and `raw/`, the controls file and the manifest stay byte for byte; a later
  `build --strict` under the same agreement takes the recorded total. The workbook copy, changed with openpyxl,
  stands in for a download.
  Tests: `test_v5_land.py::test_export_with_more_rows_and_a_new_total_lands_and_checks`,
  `test_v5_land.py::test_export_with_fewer_rows_and_a_new_total_lands_and_checks`,
  `test_v5_land.py::test_wrong_control_is_l_total_and_writes_nothing`,
  `test_v5_land.py::test_later_build_takes_the_recorded_total`.
- The check recomputation rule (v5): a check result cell replaced by `=0` while its actual and expected differ
  makes `check --strict` (Excel) and `audit --strict` exit 1 with `C-RECOMPUTE` naming that cell; the six shipped
  models give no `C-RECOMPUTE`. Tests: `test_v5_check.py::test_result_replaced_by_zero_is_c_recompute_in_check`
  (Excel), `test_v5_check.py::test_result_replaced_by_zero_is_c_recompute_in_audit`,
  `test_v5_check.py::test_shipped_models_give_no_c_recompute`.
- The query-text comparison at refresh (v5): after an attach, an edit of `queries/<t>.pq.m` and
  `land --keep-queries`, `check --refresh` is refused (exit 2) before Excel starts and names
  `land --power-query`; after `land --power-query` the refresh runs. Tests:
  `test_v5_check.py::test_refresh_is_refused_when_the_query_file_differs_from_the_attached_text` (Excel),
  `test_v5_check.py::test_refresh_refusal_for_a_changed_query_file_without_excel`.
- The strict audit after helper rows below a shorter extract are cleared by hand (v6): on a copy of a built
  workbook changed with openpyxl (the raw sheet holds fewer rows, the helper rows below the last raw row are cleared
  and kept, the control cell holds the new total), with the export landed by `land --force --control`,
  `check --strict` and `audit --strict` report no `H-GAP`, `H-COVERAGE` or `H-BEYOND`, and `audit --strict` exits
  0; the same with zero rows under a contract agreed with `--allow-empty`; one helper cell emptied above the last
  raw row is `H-GAP` at that cell; helper formulas left below the last raw row are `H-BEYOND`; a model refreshed
  through Power Query to more and to fewer rows, extended and accepted (the commands of selftest step
  `f27_power_query`) audits in strict mode with exit 0. Tests:
  `test_v6_audit.py::test_shorter_extract_repair_passes_check_and_audit`,
  `test_v6_audit.py::test_zero_row_repair_passes_check_and_audit`,
  `test_v6_audit.py::test_helper_cell_emptied_inside_the_boundary_is_h_gap`,
  `test_v6_audit.py::test_helper_formulas_below_the_last_raw_row_are_h_beyond`,
  `test_v6_audit.py::test_refresh_and_extend_cycle_audits_strict` (Excel).
- The query read back at refresh (v6): after `land --power-query` the raw entry of the manifest holds
  `query_formula_sha256`, the hash of the query formula Excel gives back, and two later reads give the same hash;
  after the workbook's query formula is changed through Excel without `rotm.py`,
  `check --refresh --accept-refresh --strict` does not exit 0, names `raw_<t>` and `X-QUERY-CHANGED`, and
  `raw/<t>.csv`, `raw/<t>.controls.json` and `model.manifest.json` stay byte for byte; with an unchanged query the
  commands of selftest step `f27_power_query` exit as before; a manifest without the hash gets a note and no
  `X-QUERY-CHANGED`. Tests: `test_v6_check.py::test_attach_records_the_formula_hash` (Excel),
  `test_v6_check.py::test_query_changed_in_excel_is_found_at_refresh` (Excel),
  `test_v6_check.py::test_unchanged_query_refresh_cycle_exits_0` (Excel),
  `test_v6_check.py::test_manifest_without_formula_hash_gets_a_note`.
- The argument-count rule (v6): `=IF(A1>0,1,0,1)` gives one `F-ARGS` error that names IF, 4 and `2 or 3`; each of
  the 29 functions of `references/model-spec-format.md` "Argument counts" gives no `F-ARGS` at its smallest and its
  largest count and gives `F-ARGS` one below and one above; build refuses the helper expression
  `IF({this:amount}>0,1,0,1)` (exit 2, no workbook) and names its JSON path; `audit --strict` reports `F-ARGS` at
  the cell; the formulas of the six shipped workbooks (and, when `ROTM_FOREIGN_DIR` is set, of the workbooks in that
  folder) give no `F-ARGS`. Tests: `test_v6_formula.py::test_if_with_four_arguments_is_f_args`,
  `test_v6_formula.py::test_listed_functions_pass_at_their_smallest_and_largest_count`,
  `test_v6_formula.py::test_build_refuses_if_with_four_arguments_at_its_json_path`,
  `test_v6_formula.py::test_audit_strict_reports_f_args_at_the_cell`,
  `test_v6_formula.py::test_shipped_and_foreign_formulas_give_no_f_args`.
- The number-format comparison (v6): one function (`lib/styles.py`, `number_formats_equal`) compares number
  formats for audit `S-NUMFMT` and for theme; a rate input whose format is changed to `0.0"%"` or `0.0\%` gets
  `S-NUMFMT`; `yyyy\-mm\-dd` equals `yyyy-mm-dd`; after an Excel save of the worked example,
  `theme --number-formats --strict` exits 0 with no `T-RESIDUAL`, and a second run changes no cell; the six shipped
  models audit in strict mode with exit 0. Tests: `test_v6_audit.py::test_quoted_percent_sign_is_s_numfmt`,
  `test_v6_audit.py::test_escaped_hyphens_in_a_date_format_are_equal`,
  `test_v6_audit.py::test_theme_number_formats_after_an_excel_save_leaves_no_residual` (Excel),
  `test_v6_audit.py::test_shipped_models_audit_strict`.
- Landing a CSV by its source headers (v7): a CSV with the header `Order ID,Order Date,Net Sales` is proposed with
  `contract propose --csv`, agreed on the task-file channel and landed as it is: exit 0; `raw/orders.csv` has the
  header `order_id,order_date,net_sales` and the values of the file; the note names the three pairs; on a model
  built on it, `build --strict` exits 0 and `check --strict --no-excel` exits 3 (not recalculated) with no error and
  no warning; the same with the CSV's columns in another order. A header with one column missing, with one extra
  column or with one header given twice is `L-COLUMNS` (exit 1) and `raw/` stays byte for byte. A CSV with the
  destination names lands as in v6. A contract without `csv_header` refuses a differing header as in v6. The
  contracts of the six shipped folders keep their hash and their snapshot, and each `.md` is the text rendered from
  its JSON. Tests:
  `test_v7_contract.py::test_csv_with_spaced_headers_lands_by_its_sample_headers`,
  `test_v7_contract.py::test_sample_header_landing_refuses_a_missing_extra_or_repeated_header`,
  `test_v7_contract.py::test_plain_headers_land_as_before`,
  `test_v7_contract.py::test_contract_without_csv_header_refuses_a_differing_header`,
  `test_v7_contract.py::test_shipped_contracts_keep_their_hash_md_and_snapshot`.
- The ar_aging over-90 measure (v7): in a copy of `templates/ar_aging` whose last bucket starts at 121 days and is
  named `Over 120 days`, `build --strict` and `check --strict` (Excel) exit 0, the over-90 total equals the open
  amounts more than 90 days past due that the test computes from `data/open_items.csv`, and the not-due total equals
  the open amounts 0 or fewer days past due; with the shipped bucket lists the over-90 total equals the value of the
  v6 workbook; build of a copy with the cutoff input `over90_days` set to 120 exits 2 and writes no workbook (the
  input is fixed at 90). Tests:
  `test_v7_templates.py::test_ar_aging_over90_follows_the_cutoff_when_the_last_bucket_changes` (Excel),
  `test_v7_templates.py::test_ar_aging_shipped_lists_give_the_shipped_over90` (Excel),
  `test_v7_templates.py::test_ar_aging_cutoff_input_is_fixed_at_90`.
- The hooks on a bare folder name (v7): with payloads as Claude Code sends them, from the parent folder of a model
  folder, for a bare folder name, `./name` and the full path alike: the contract gate refuses a `contract agree`
  reply that is not in the transcript; the audit on write prints its audit status after a build; the finish check
  refuses the first stop once for an unverified workbook. A bare word that names no folder holding
  `rotm_model.json` gives no hook output. Tests: `test_v7_hooks.py::test_agreement_guard_refuses_an_untyped_reply`,
  `test_v7_hooks.py::test_audit_on_write_reports_after_a_build`,
  `test_v7_hooks.py::test_finish_check_refuses_once_for_an_unverified_workbook`,
  `test_v7_hooks.py::test_a_bare_word_that_is_no_model_folder_gives_no_output`.
- The kept copy of a workbook with a live query (v7): after an attach with `land --power-query`, a rebuild whose
  query source cannot be read exits 1 under `build --strict --force` with `W-PQ-NOT-ATTACHED`, which names both
  copies; the kept workbook `<stem>.bak_<time>.xlsx` still holds its query table and the kept manifest
  `<stem>.manifest.bak_<time>.json` equals the earlier manifest byte for byte; after a rebuild in which every query is
  attached again no copy is left; a rebuild without Excel keeps the copies; the hooks do not act on the copies.
  Tests: `test_v7_build.py::test_failed_attach_keeps_the_earlier_workbook_and_manifest` (Excel),
  `test_v7_build.py::test_successful_rebuild_leaves_no_kept_copy` (Excel),
  `test_v7_build.py::test_rebuild_without_excel_keeps_the_copies` (Excel for the set-up),
  `test_v7_build.py::test_hooks_do_not_act_on_the_kept_copies`.
- The monthly roll check of rolling_forecast (v8): in a copy of `templates/rolling_forecast` with the shipped sample
  landed, `build --strict` and `check --strict` (Excel) exit 0 and every check holds, `closed_months_landed`
  included; with `last_actual`, `actual_months` and `forecast_months` rolled to 4 actual and 8 forecast months, and
  to 8 actual and 4 forecast months, and the landed rows unchanged, `check --strict` exits 1 and
  `closed_months_landed` is the only failing error check; with rows for April 2026 landed by the commands of the
  template's README section "The monthly roll" (the contract changed and agreed again,
  `land --csv --force --control`) and the inputs rolled to 4 and 8 months, `build --strict` and `check --strict`
  exit 0 and the April actual total on `calc` equals the April sum of the CSV divided by 1000; with the landed rows
  unchanged, the inputs rolled and `closed_months_no_sales` set to 1, `check --strict` exits 0. Tests:
  `test_v8_templates.py::test_rolling_forecast_shipped_inputs_hold_every_check` (Excel),
  `test_v8_templates.py::test_rolling_forecast_roll_without_new_rows_fails_the_landed_months_check`
  (Excel, 2 cases),
  `test_v8_templates.py::test_rolling_forecast_roll_with_new_rows_holds_and_shows_the_closed_month` (Excel),
  `test_v8_templates.py::test_rolling_forecast_closed_month_without_sales_passes_with_the_input_set` (Excel).
- Records of only delimiters and open quotes in a CSV (v8): an extract with one record that holds only delimiters
  (`,,`) is refused by `land --csv` (exit not 0), the finding names its CSV line, and `raw/<t>.csv` stays byte for
  byte; the same file with a blank line in its place lands with the same digest as without that line; a quoted
  field whose closing quote is missing at the end of the file is refused with the line where the field starts; a
  CSV in the form Excel saves (CRLF line ends, a field with a comma in quotes, a doubled quote, a line break inside
  quotes) lands; the sample extract of each of the six shipped folders, landed in a copy, gives the shipped
  `raw/<t>.csv` byte for byte. Tests: `test_v8_land.py::test_record_of_only_delimiters_is_refused_with_its_line`,
  `test_v8_land.py::test_blank_line_is_skipped_as_before`,
  `test_v8_land.py::test_open_quote_at_the_end_is_refused_with_its_line`,
  `test_v8_land.py::test_csv_in_excel_form_lands`,
  `test_v8_land.py::test_shipped_folders_land_with_the_same_digests`.
- Saved inputs outside their valid range (v8): in a copy of the worked example saved by Excel with the display scale
  `inputs!D7` set to 1, `check --strict` and `audit --strict` exit 1 with `V-INPUT-RANGE`, which names the cell, the
  value 1, the range `1000 to 1000` and the input's note; the six shipped models give no `V-INPUT-RANGE` from
  `audit --strict` or from `check` without Excel. Tests:
  `test_v8_check.py::test_saved_scale_outside_its_range_fails_check_and_audit` (Excel),
  `test_v8_check.py::test_shipped_models_give_no_input_range_finding`.
- A query attached by hand that reads another query (v8): in a model from the starter whose raw query `raw_lines`
  reads the staging query `stage_source` by name, `build --strict --force` with Excel exits 0, keeps the M text of
  `stage_source` in `queries/stage_source.hand.pq.m`, adds `stage_source` again as a connection-only query, attaches
  `raw_lines` at `raw_lines!A1` again and names `stage_source` in the report; after one amount of the source CSV is
  changed, `check --refresh --strict` and the acceptance with `--control` run, and an output cell on `calc` equals
  the total the test computes from the changed CSV; a query attached by hand that holds all its steps itself is
  carried over as in v7. Tests: `test_v8_build.py::test_rebuild_carries_a_staging_query_a_hand_query_reads` (Excel),
  `test_v8_build.py::test_self_contained_hand_query_is_carried_as_in_v7` (Excel).
- An accepted control total bound to its agreement (v9): in a model from the starter, a total recorded for a
  snapshot by `land --control`, and in another by `check --accept-refresh --control`, then the contract proposed,
  shown and agreed again with another total: `land` of the same extract without `--control` reports `L-TOTAL` with
  both totals and does not exit 0, and `raw/<t>.csv`, `raw/<t>.controls.json` and the manifest stay byte for byte;
  `build --strict` writes the new contract's total as the expected value of the total check and `check --strict`
  does not exit 0; `land --force --control` with the rows' true sum then exits 0, its entry records the new
  agreement's `contract_sha256`, and `build --strict` and `check --strict` exit 0; an entry made under the agreement
  in force still applies; an entry written before v9 (without the hash) applies when it is later than the
  agreement and does not when it is earlier or a time is missing; `contract show` and `contract agree` with
  unchanged content keep the entry in force. Tests:
  `test_v9_controls.py::test_land_control_record_is_not_used_after_a_new_agreement`,
  `::test_acceptance_record_is_not_used_after_a_new_agreement` (Excel),
  `::test_build_and_check_use_the_new_contract_total_after_a_new_agreement` (Excel),
  `::test_land_force_control_after_a_new_agreement_records_the_new_hash` (Excel),
  `::test_entry_under_the_agreement_in_force_still_applies`,
  `::test_entry_without_hash_applies_only_when_not_earlier_than_the_agreement`,
  `::test_show_and_agree_with_unchanged_content_keep_the_entry`.
- The self-mismatch finding for labels that hold ~* or ~? (v9): in a model from the starter with a category that
  holds `~*` and one that holds `~?`, each with amounts that add to zero over the months, `build --strict` and
  `check --strict` (Excel) do not exit 0, and each `D-CRITERIA` finding (reason `self_mismatch`) names the label and
  its first raw row; with helper keys that replace `~`, `*` and `?` by fixed texts on both sides, `build --strict`,
  `audit --strict` and `check --strict` exit 0 and every sum per label and month equals the sum computed in Python
  from the CSV; a label with `~` and no wildcard, ordinary labels and the shipped models give no such finding.
  Tests: `test_v9_criteria.py::test_tilde_wildcard_labels_netting_to_zero_fail_build_and_check` (Excel),
  `::test_helper_keys_for_tilde_labels_pass_and_equal_python` (Excel),
  `::test_tilde_without_wildcard_ordinary_labels_and_shipped_models_give_no_self_mismatch`.
- The refusal of --control for a column without a contract total (v9): under a contract with a row count and no
  control total, `land --csv --force --control` exits 2 and `raw/` stays byte for byte, and
  `check --accept-refresh --control` exits 2 and records nothing; with the total in the agreed contract the same
  `land` command lands and `build` writes the check `<t>_total_<column>`. Tests:
  `test_v9_land.py::test_control_without_a_contract_total_is_refused_by_land`,
  `::test_control_without_a_contract_total_is_refused_by_accept_refresh`,
  `::test_contracted_total_lands_and_build_writes_its_check`.
- A pass made stale by a later landing in the finish check (v9): in one simulated hook session on a model from the
  starter, after `build`, `audit --strict` and `check` passed and `land --force --control` landed a changed
  extract, the stop with the last message "Done." is refused once, and the refusal names the table and the build
  command; after the rebuild, its audit and its check, the stop goes through; a contract agreed again after the
  pass makes the pass stale; a session without a landing after the pass, and a status record written before v9
  (without `inputs`), behave as in v8. Tests:
  `test_v9_hooks.py::test_landing_after_the_pass_is_refused_once_naming_the_table_and_the_rebuild` (Excel),
  `::test_contract_agreed_again_after_the_pass_makes_it_stale`,
  `::test_session_without_a_landing_after_the_pass_behaves_as_in_v8`.

### Not verified

- Anything in Google Sheets or Connected Sheets: no Google account was available. Compatibility is checked by rule
  against the documented function lists; how `[Red]` number formats, `_)` padding, Arial and the fills convert is not
  confirmed (`references/google-sheets.md`).
- A model that uses a function only Google Sheets has: it is audited and not recalculated (`X-SHEETS-ONLY`); no
  Google Sheets session calculated it.
- The Google Sheets procedures of `references/google-sheets.md` were not run in Google Sheets: the first extract
  before the build, the repair of the helper rows after a refresh to more, fewer or zero rows, and the export route
  for a refresh made in Google Sheets. The export route, with `land --control` for a changed source total, was
  tested only without Google Sheets, on a workbook copy changed with openpyxl in place of the download
  (`test_v4_docs.py::test_google_sheets_export_route_lands_and_checks` and the `test_v5_land.py` tests named in the
  verified list).
- The repair of the helper rows after a refresh to fewer or zero rows (`references/google-sheets.md`) was tested
  here with a local stand-in (a copy of a built workbook changed with openpyxl), not in Google Sheets: after the
  repair, `check --strict` and `audit --strict` of the copy pass
  (`test_v6_audit.py::test_shorter_extract_repair_passes_check_and_audit`,
  `test_v6_audit.py::test_zero_row_repair_passes_check_and_audit`; with Excel,
  `test_v6_integrator.py::test_repaired_download_passes_check_with_excel`).
- The redirect of the formulas with Find and replace in Google Sheets (`references/google-sheets.md`, "Cutover when
  the extract is on a new sheet"): not run in Google Sheets.
- The GoogleSQL that `land` writes to `queries/<t>.sql`, quoted identifiers included, was not run against BigQuery.
- A Power Query refresh against a database (only a local file). The `--pq-source database` template quotes column
  names for its connector (`[name]` SQL Server, `"name"` PostgreSQL, Snowflake, Redshift, SAP HANA and Oracle,
  backticks BigQuery and MySQL); no database was reached, so the template was not run.
- R output: `lift --lang r` exits 3 (no R was installed to test it).
- Renders are looked at by an agent, not by code; code checks only that each PNG exists and is not blank.
- The contract gate's transcript rules (the reply typed by the user, after the latest proposal or edit of that
  table's contract) need the session transcript; a session without one (`claude -p --no-session-persistence`) lets
  `contract agree` through, and the rules are unit-tested only.
- The finish check's notice on a stop it lets through: seen in one live `--plugin-dir` session on the v2 files
  (Sonnet 5.5); not repeated on a later version.
- A refresh by hand in a workbook whose Preserve cell formatting was turned off by hand.
- The orchestrator's own T5 test workbook and its clean twin: not run by the builder (they belong to QA).

## File map

| File | Read it when |
| --- | --- |
| `references/column-contract.md` | P1: before any SQL or Power Query text, and when a raw table must change |
| `references/excel-power-query.md` | P2 in Excel: before attaching or refreshing a query, and when `check` reports uncovered rows |
| `references/google-sheets.md` | P2 in Google Sheets: before giving Connected Sheets steps or claiming Google Sheets compatibility |
| `references/model-spec-format.md` | P3: writing or changing `model.spec.json` (every key, placeholder and block shape); when build or audit reports F-ARGS ("Argument counts") |
| `references/helper-patterns.md` | P3 and P4: when build or audit reports F-DEPTH, F-NESTED-IF, F-LITERAL or F-LENGTH |
| `references/functions.md` | which functions exist in both products, which are refused or flagged, and the counting rule |
| `references/checks.md` | P5 and P7: adding checks, a `W-NO-CHECK` warning, reporting check results |
| `references/style-dictionary.md` | P6: the cell types, their looks and sources, the overrides, and the choices left to Max |
| `references/style_dictionary.json` | the dictionary itself (machine-readable; every value has its source) |
| `references/fast-standard-rules.md` | the published modelling rules behind the audit rules, with FAST rule numbers (CC BY 4.0 attribution) |
| `examples/sales_vs_target/` | a complete model folder: task, data, contracts, specification, workbook, reports and renders |
| `templates/starter/` | the smallest complete model to copy with `init --template starter` |
| `scripts/rotm.py` | the one command line; `scripts/formula_audit.py` is the audit alone |

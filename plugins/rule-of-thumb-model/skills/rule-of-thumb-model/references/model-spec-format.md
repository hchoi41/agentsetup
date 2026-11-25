# Model specification format for `rotm.py build`

One JSON file, `model.spec.json` in the model folder, describes one workbook. `rotm.py build` compiles it into
`model.xlsx` and the sidecar `model.manifest.json`. The script owns the layout, every cell reference, every
style and the check wiring; you supply inputs, raw tables, helper columns, calculation blocks and checks, and
write expressions with placeholders instead of cell addresses. Two models built from two specifications
therefore read the same way (spec 2.4 S1).

```
python -P -X utf8 -B "<SKILL>/scripts/rotm.py" build "<model>"            # <model>/model.spec.json -> model.xlsx
python -P -X utf8 -B "<SKILL>/scripts/rotm.py" build "<model>" --strict   # exit 1 on any warning (the file is still written)
python -P -X utf8 -B "<SKILL>/scripts/rotm.py" formula "=IFERROR(INDEX(A:A,MATCH(B1,C:C,0)),0)"   # measure one formula first
```

The complete example used by the tests is `scripts/tests/data/build/model.spec.json` (one raw table of 72 rows,
every block shape, every placeholder kind except `{gcol:}`, `{g:}` and `{chk:}`). Section "Worked example" below
shows it.

## Top level

```json
{
  "rotm_spec": 1,
  "name": "sales_vs_target",
  "title": "Sales versus target by region, January to June 2026",
  "purpose": "Monthly sales by region against target, with variance.",
  "output": "model.xlsx",
  "product": "both",
  "settings": {"depth_limit": 2, "length_limit": 100},
  "dictionary": null,
  "limitations": ["Targets are monthly; no weekly view."],
  "sheets": [ ... ]
}
```

| Key | Required | Meaning |
| --- | --- | --- |
| `rotm_spec` | yes | always `1` |
| `name` | yes | the model's name, 1 to 64 characters, no `\ / : * ? " < > \|` |
| `title` | yes | shown in B1 of the cover sheet |
| `sheets` | yes | at least one sheet (section "Sheets") |
| `purpose` | no | shown on the cover under "Purpose" |
| `output` | no | the workbook file name, default `model.xlsx`; must end in `.xlsx`. The folder commands (`audit MODEL`, `check MODEL`, `status`, `render`, `land --power-query`) and the hooks use the workbook that `rotm_model.json` names in its `workbook` field; build without `-o` keeps that field equal to `output`. After `build -o <file>` the marker is not changed: name that file in the commands (`rotm.py check "<file>"`, `rotm.py audit "<file>"`), as the build report does |
| `product` | no | `both` (default), `excel` or `sheets`: the function profile (`references/functions.md`) |
| `settings` | no | section "Settings" |
| `dictionary` | no | path of a dictionary override file, relative to the model folder; `null` uses `<model>/style_overrides.json` when it exists (`references/style-dictionary.md`) |
| `limitations` | no | list of text lines shown on the cover under "Limitations" (FAST 2.06-02) |
| `description`, `note` | no | free text, not written to the workbook |

A key the schema does not know is refused with the nearest valid key ("did you mean ...?"). Every problem of a
specification is reported at once, each with its JSON path, for example
`sheets[4].blocks[0].cell: unknown block 'actuals' in {g:actuals}; did you mean 'actual'?`. Nothing is written
when any problem is found (exit 2).

## Settings

| Setting | Default | Source of the default | Meaning |
| --- | --- | --- | --- |
| `depth_limit` | 2 | Max (spec 1.2: "less than 3 nested functions at most") | most functions open at once in one formula (`references/functions.md`, counting rule). Build refuses a value below 2, because its own master check has depth 2 |
| `length_limit` | 100 characters | the plugin's own number (FAST 3.03-01 gives none) | a longer formula is a warning `F-LENGTH` |
| `allowed_literals` | `[0, 1]` | the plugin's own choice (stricter than FAST 3.04-01) | numbers that may be typed inside a formula; minus signs are ignored (`-1` is allowed). Build refuses a list without 0 and 1, because its check formulas use them |
| `check_tolerance` | 0.001 | the plugin's own choice | the default tolerance of `equal` checks, in the unit of the checked values; written as an input cell |
| `helper_spare_rows` | 0 | the plugin's own choice | helper rows added below the landed rows; a value above 0 is refused (section "Capacity") |
| `formula_key_check_max_rows` | 5,000 rows | the plugin's own choice | above this many raw rows, the key check (`dup`) moves out of the workbook into `rotm.py check` |
| `allow_functions` | `[]` | | flagged functions accepted for this model, for example `["VLOOKUP"]`; refused functions stay refused |

Precedence: command line, then spec `settings`, then the project's dictionary override, then the plugin
dictionary's `rules`.

## Sheets

| `type` | How many | Default font size | Freeze | Holds |
| --- | --- | --- | --- | --- |
| `cover` | 0 or 1 (added when absent) | 9 | none | title, master check, purpose, contents, cell-type legend, build record, limitations |
| `inputs` | 0 or 1 (added when absent) | 9 | D4 | every typed number: scalars, lists, grids, contract controls, the check tolerance |
| `raw` | one per raw table | 8 | A2 | one landed table, values only |
| `helpers` | 0 or 1 per raw table | 8 | C2 | one row per raw row: fetched raw columns and row-wise helper columns |
| `calc` | any | 8 | D4 | calculation blocks |
| `output` | any | 9 | D4 | key results |
| `checks` | 0 or 1 (added when absent) | 9 | D5 | every check and the master check |
| `notes` | 0 or 1 | 9 | none | notes with sources |

- Every sheet has `name` and `type`. Names match `^[a-z][a-z0-9_]{0,23}$`.
- A raw sheet is named `raw_<table>`; a helper sheet `h_<table>`. Other sheets may not use those prefixes.
- `"font_size": 8` or `9` on a sheet overrides the default (Max, spec 1.2: 8 for "a brick wall of data", 9 where
  there is white space). Any other size is refused. A calculation sheet with white space (few rows and columns)
  gets `"font_size": 9` in its sheet entry; size 8 is for dense sheets.
- Build prints the information note `I-SPARSE-SHEET` (it does not change the strict exit code) for a sheet of type
  `calc` without `font_size` in its entry that holds fewer populated cells than `rules.sparse_sheet_cells` of the
  style dictionary (100 cells): `sheet '<name>' takes size 8 from its role (calc) and holds <n> populated cells,
  fewer than rules.sparse_sheet_cells (<limit>); a sheet with white space takes size 9 (Max, spec 1.2)`. A
  populated cell holds a value or a formula when build saves the sheet. Raw and helper sheets get no such note.
  Build does not choose the size from the sheet's density; that choice is left to Max.
- Optional on every sheet: `title` (B1; default: the name with `_` as spaces, first letter upper case; the cover
  uses the spec's `title`), `purpose` (shown in the cover's contents), `note`.
- Build writes the sheets in this order: cover, inputs, raw, helpers, calc, output, checks, notes; within one type
  in the order of the spec. The cover is the active sheet.
- Gridlines are hidden and zoom is 100% on every sheet (dictionary `sheet_types`).

### Layout of block sheets (cover, inputs, calc, output, checks, notes)

| Place | What build writes |
| --- | --- |
| column A | spacer, width 2 |
| column B | row headers (labels) |
| column C | units (FAST 3.05 units designator) |
| column D | the first value column (FAST 1.01-02: the same column structure on every sheet) |
| B1 | the sheet title (cell type `title`, Arial 9 bold on every sheet) |
| B2, C2 | `Master check` and the master check: the formula on the checks sheet, `=checks!$C$2` elsewhere (FAST 2.03-05) |
| row 3 | blank |
| row 4 | the first block |

Each block takes: a heading row (the block title in B, cell type `heading`), a column-header row (`col_header`
from B to the last column: B holds the rows' `label`, C `Unit`, D onwards the column headers), a unit row for a
measures block whose measures have units, the body rows (B `row_header`, C `unit`, values from D), a total row
when asked, then one blank row before the next block.

Raw and helper sheets have no title and no master check: row 1 is their header row, so helper row r lines up
with raw row r (a recorded exception to FAST 2.03-05).

## Ids

- Inputs, blocks and checks share one set of ids. The ids build adds are in the same set: the input
  `check_tolerance`, and for every raw table t the checks `<t>_rows`, `<t>_cover`, `<t>_beyond`, `<t>_keys`,
  `<t>_total_<column>` (one per control total) and `<t>_nulls_<column>` (one per never-null column after the
  first).
- An id used twice in this set is refused when the specification is loaded (exit 2, nothing built), at the later
  JSON path: `id '<id>' is used by <kind A> at <path A> and by <kind B> at <path B>`, with the fix
  `rename one of them: inputs, blocks and checks share one set of ids (references/model-spec-format.md, "Ids")`. For
  example an input `bridge` and an items block `bridge` are refused, and no other line is printed for that id.
- A spec check with the id of a check build adds is refused with `check id '<id>' is the id of a check build adds
  for raw table '<t>'` (fix: choose another id).
- An input may not have the id that build gives a contract control on the inputs sheet: `<t>_rows_exact`,
  `<t>_rows_min`, `<t>_rows_max`, `<t>_tol_<column>`, `<t>_key_repeats` and `<t>_known_nulls_<column>`. build
  refuses such an input (exit 2, nothing written) at `<input path>.id`: `input id '<id>' is the id build gives a
  contract control of table '<t>' (from contracts/<t>.contract.json <expect path>)`. A block or a check may have
  such an id. The control total `<t>_total_<column>` is also the id of a check build adds, so an input with that id
  is refused with the message of the first bullet.
- A key output (`{cell:}`), a helper column (`{h:}`, `{this:}`) and a sheet name may have the same id as an input,
  a block, a check or each other: build looks each of them up in its own table.
- Items and measures have ids of their own block: two items (or two measures) of one block may not share an id,
  and an item may have the id of an item of another block (`{b:<block>.<item>}` and `{blk:<block>.<item>}` name the
  block).

## Inputs

```json
{"name": "inputs", "type": "inputs", "items": [
  {"id": "start_month", "label": "First month", "value": "2026-01-31", "type": "date", "unit": "date", "source": "task.md"},
  {"id": "scale", "label": "Display scale (fixed at 1000)", "value": 1000, "type": "number", "format": "count",
   "unit": "KRW per unit shown", "valid": {"min": 1000, "max": 1000},
   "note": "Fixed: the target grid is typed in KRW thousand."},
  {"id": "regions", "kind": "list", "label": "Regions", "values": ["East", "North", "South", "West"], "format": "text"},
  {"id": "target", "kind": "grid", "label": "Target", "unit": "KRW thousand", "format": "amount",
   "rows": {"values": ["East", "North", "South", "West"], "label": "Region"},
   "cols": {"values": ["2026-01-31", "2026-02-28"], "format": "month"},
   "values": [[1500, 1550], [1600, 1650], [1700, 1750], [1800, 1850]], "source": "budget file v3"}
]}
```

| Kind | Required | Optional | Written as |
| --- | --- | --- | --- |
| scalar (default) | `id`, `label`, `value` | `type` (`number`, `integer`, `date`, `text`, `boolean`), `format`, `unit`, `source`, `note`, `valid` | one row of the "Assumptions" block: label in B, unit in C, value in D (`input`), source and note in E (`note`; FAST 2.04-02) |
| `list` | `id`, `kind`, `label`, `values` | `format`, `unit`, `source`, `note`, `valid`, `type` | its own block: item number (as text) in B, unit in C, one value per row in D |
| `grid` | `id`, `kind`, `label`, `rows.values`, `cols.values`, `values` (one list per row) | `rows.label`, `rows.format`, `cols.format`, `format`, `unit`, `source`, `note`, `valid` | its own block: row headers in B, column headers in the column-header row from D, values in the body |

- Order on the sheet: the "Assumptions" block (all scalars, in spec order), then each list and grid in spec order,
  then the "Contract controls" block that build adds.
- A date is written as text `YYYY-MM-DD` in the JSON and becomes a date cell (`type: "date"`, or any ISO date
  in a list, a grid header or a block header whose format is not `text`).
- Default number formats when `format` is absent: `date` for dates, `count` for `integer`, `general` for
  `number`, `text` for `text`. Format keys are those of the dictionary's `number_formats` (`amount`, `amount1`,
  `amount2`, `rate`, `rate2`, `factor`, `count`, `integer`, `date`, `datetime`, `month`, `text`, `flag`, `check`,
  `general`).
- `valid`: `{"min": 0, "max": 1}` (either end may be left out) or `{"list": ["A", "B"]}` adds Excel data validation
  (books B-14). A list longer than 255 characters, or one holding a comma or a quote, is refused: use a list input.
- Build refuses (exit 2, nothing built) an input value outside its own `valid` range, at `<input path>.value` (a
  scalar), `<input path>.values[<i>]` (a list) or `<input path>.values[<r>][<c>]` (a grid): `input '<id>' (<label>)
  holds <value>, outside its valid range <min> to <max>` (`of at least <min>` or `of at most <max>` when one end
  is left out), or, for `valid.list`, `input '<id>' (<label>) holds <value>, which is not one of its valid values
  (<v1>, <v2>, ...)`. The fix says `write a value from <min> to <max>` (or `write one of the valid values`) and
  adds the input's `note` when it has one.
- An input that converts a unit must apply to both sides of every comparison, or be fixed. Examples of such an
  input: a display scale, an exchange rate. In the example above the helper column divides the raw amounts by
  `scale`, while the target grid is typed in KRW thousand: any other value of `scale` would compare two units, and
  every unit label would be wrong. So `scale` is fixed: `"valid": {"min": 1000, "max": 1000}`, and its label and
  note say so. A model in another unit needs the target retyped and the labels changed.
- An input that scales displayed amounts is fixed (valid range 1000 to 1000, as the worked example and every
  shipped template have it), or every label and note that names the unit reads the input. A scale the user can
  change under unit labels that are fixed text (`"unit": "KRW thousand"`) shows amounts under a wrong unit with
  every check passing.
- "Contract controls" (added by build, design 6.4): for every raw table, the contract's expected rows
  (`<table>: rows expected`, or `fewest rows` and `most rows`) and each control total with its tolerance; then
  `Check tolerance` (the `check_tolerance` setting). These cells are the inputs the generated checks compare
  against, so every typed number of the model sits on the inputs sheet or on a raw sheet.
- The ids `check_tolerance`, `<table>_rows_exact`, `<table>_rows_min`, `<table>_rows_max`,
  `<table>_total_<column>` and `<table>_tol_<column>` are taken by build.

## Raw sheets

```json
{"name": "raw_sales", "type": "raw", "table": "sales", "landing": "static"}
```

- `table` names a raw table whose contract `contracts/<table>.contract.json` is agreed (`references/column-contract.md`).
  Build refuses a missing, invalid, proposed or stale contract and says which command records the agreement.
- The rows come from `raw/<table>.csv`, written by `rotm.py land` (section "Landing"). Build refuses a model whose
  snapshot is missing and prints the `land` command.
- Layout: the contract's columns from A1 in contract order, header in row 1 (`col_header`), data from row 2
  (`raw`, number format from the column's `format` or its contract type: text `@`, integer `#,##0` (`count`), number `amount`,
  date `yyyy-mm-dd`, datetime `yyyy-mm-dd hh:mm:ss`, boolean `General`; a `number` column without `format` whose landed
  values hold a fraction gets `amount2` (two decimals, so 7.25 is not shown as 7), or `General` when a value has more
  than two decimals; the manifest records the format of every raw column as `formats`, and the audit's S-NUMFMT
  rule checks against it), an Excel table `tbl_raw_<table>` over the range without a table style, nothing below or
  beside it, frozen at A2 (design 7.1, D9).
- Values only: no formula is ever written on a raw sheet. A value of a `text` column is stored as a text cell
  whatever it looks like: `=1+1`, an error name such as `#N/A`, digits such as `0012` and the word `TRUE` stay text.
- `landing`: `static` (default) or `power_query`. With `power_query`, build writes the static values with openpyxl
  and then, through Excel, replaces them with the Power Query `q_<table>` loaded as `tbl_raw_<table>` at A1 from
  `queries/<table>.pq.m` (design 7.3). Without Excel, or with `--no-power-query`, the sheet keeps its static
  values and build warns `W-PQ-NOT-ATTACHED` with the command that attaches it later.
- A query attached earlier with `rotm.py land <model> --table <t> --power-query` is attached again on a rebuild, even
  when the spec says `static`; the build report says so. Write `"landing": "power_query"` to record it in the spec.
- A query attached by hand (the manifest records `query_attach` `"hand"`, or a query table at `raw_<t>!A1` that no
  `rotm.py` command attached) is never replaced by `queries/<t>.pq.m`. Build reads its name and M text through
  Excel, attaches it again to the new workbook, keeps the text in `queries/<t>.hand.pq.m` and records
  `query_attach` `"hand"`. Without Excel build refuses (exit 2) and writes nothing; `--drop-hand-queries` rebuilds
  the sheet as a static table (`references/excel-power-query.md`, "Attaching a query by hand").

## Helper sheets

```json
{"name": "h_sales", "type": "helpers", "table": "sales",
 "fetch": ["invoice_id", "line_no", "inv_date", "region", "amount"],
 "columns": [
   {"id": "month_end", "expr": "EOMONTH({this:inv_date},0)", "format": "month", "label": "Month end"},
   {"id": "amount_k", "expr": "{this:amount}/{in:scale}", "format": "amount1"}]}
```

Row r of the helper sheet belongs to raw row r (r ≥ 2). Build writes the columns in this fixed order (design 7.1):

| Column | Header | Formula in row 2 | Meaning |
| --- | --- | --- | --- |
| A | `row` | `=ROW()` | the row number the other columns read with |
| B | `in_raw` | `=--(INDEX(raw_sales!$A:$A,A2)<>"")` | 1 when raw row r exists (the contract's first column is never null) |
| then one per fetched column | the raw column name | `=INDEX(raw_sales!$C:$C,A2)`; for a `text` column `=INDEX(raw_sales!$A:$A,A2)&""` | the raw value of this row; `&""` keeps an ID such as `000101` as text and shows a missing value as empty; an empty number or date cell shows 0 |
| then one per fetched number, integer, date or datetime column that may be empty | `<column> empty` (id `<column>_null`) | `=--(INDEX(raw_sales!$F:$F,A2)="")` | the null flag: 1 when the raw cell is empty (for a column whose null rule is `allowed`, and for a column whose contract agrees `known_nulls` for it) |
| then one per `columns` item | its `id` | the compiled `expr` | one step per helper (P3) |
| next to last, for a key of several columns | `dup_key` | `=C2&"\|"&D2` | the key's parts joined by `\|` |
| last | `dup` | `=--(SUMPRODUCT(--EXACT(C$2:C2,C2))>1)*B2` (C: the key column, or `dup_key`) | 1 on a later copy of a key: EXACT compares exact text, case-sensitive, with no wildcard, and never reads text as a number; only when every key column is fetched, the table has at most `formula_key_check_max_rows` rows and, in a key of several columns, no landed key part holds `\|` |

- `fetch` lists the raw columns to fetch, in any order; default: every contract column, in contract order.
- A `columns` item has `id` and `expr`; optional `label` (kept in the manifest), `format`, `unit`, `type`
  (`text`, `integer`, `number`, `date`, `datetime`, `boolean`: sets the default format), `note`.
- In a helper expression, `{this:<column>}` is the same row of another column of this sheet (a fetched raw column
  by its raw name, or a helper column by its id). Whole raw columns (`{raw:t.c}`), inputs, block ranges and key
  outputs may be read too.
- Every row holds the same formula in the same relative form, so the audit's helper rule (H-INCONSISTENT) holds
  and a copied row is correct.
- Calculation blocks read helper columns as whole columns (`h_sales!$J:$J`), so helper rows added at the bottom
  are included without editing any formula.
- Depth cost: a fetch has depth 1 and lives in its own column, so a derived column has the whole depth limit for
  its own step.
- Keys are compared by one rule in `land`, `check`, the `dup` column and `lift`: exact text, case-sensitive, no
  wildcard, text never read as a number (`001` and `1` are two keys, `Case` and `CASE` are two keys, `M*` does not
  match `Monday`); numbers, dates and booleans compare by value. Without the `dup` column (I-KEYS-OUTSIDE),
  `rotm.py check` checks the keys in Python by the same rule.
- Null flags: an empty raw cell of a number or date column is fetched as 0, so COUNT and AVERAGE over the helper
  column count it. Build writes the flag for a nullable column and for a never-null column with agreed
  `known_nulls` (a known exception); `lift` computes it the same way. Read such a column through its flag: `COUNTIFS({h:t.<column>_null},0)` counts the rows that hold
  a value and `AVERAGEIFS({h:t.<column>},{h:t.<column>_null},0)` averages them. `{h:<table>.<column>_null}` and
  `{this:<column>_null}` read the flag; a `columns` item may not take the id of a flag.

### Capacity, growth and shrink (design 7.2)

- Helper rows = the landed rows. A table with zero landed rows (a header-only extract under a contract agreed with
  `--allow-empty`) gets zero helper rows: the helper sheet holds its header row only, every range on `h_<table>`
  in a formula ends at row 2, the manifest's helpers entry has `capacity` 0, `first_row` 2 and `last_row` 1, and
  `columns[].formula` keeps each column's row-2 formula. The checks `<table>_cover` and `<table>_beyond` give 0.
  When a later refresh returns rows, `rotm.py extend <model> --table <table>` writes row 2 from those formulas and
  fills down. `--capacity <table>=<rows>` and the contract's
  `refresh.capacity_rows` may set fewer rows (a warning, `W-CAPACITY`). A capacity above the landed rows is refused
  (exit 2), whatever its source (`--capacity`, `refresh.capacity_rows`, `helper_spare_rows` above 0), and the
  message names the source: a helper row below the last raw row reads empty raw cells as 0 and would feed the
  calculations.
- More raw rows after a refresh than helper rows: the generated check `<table>_cover` turns to 1 and the master check
  shows FAIL, in Excel and in Google Sheets. Fix: `rotm.py extend <model> --table <table>` copies the last helper
  row down (or copy that row down by hand, or rebuild with a new snapshot).
- Fewer raw rows after a refresh than helper rows: the helper rows below the last raw row read empty raw cells
  (`in_raw` 0, fetches 0 or empty text), so a helper such as `--({this:amount}=0)` counts them. The generated check
  `<table>_beyond` (`=COUNTIF(h_<table>!$B:$B,0)`) turns to 1 and the master check shows FAIL, and `rotm.py check`
  reports H-BEYOND with the first and last of those rows. Fix: `rotm.py extend <model> --table <table>` clears them
  (through Excel when it is available) and sets the manifest's helper rows to the raw rows. Blocks read helper
  columns as whole columns, so cleared rows read like rows that were never there. After a refresh that returns no
  rows, `extend` clears every helper row, row 2 included.

## Calculation blocks (`calc` and `output` sheets)

A `calc` or `output` sheet holds `"blocks": [...]`, at least one. A block has an `id` and one of three shapes,
told apart by its keys: `cell` (grid), `items` (items) or `measures` (measures). Common optional keys: `title`
(default: the id), `unit` (written in column C), `format` (default `amount`), `note`, `source` (written beside
the heading).

### Grid

Rows times columns, one expression for every body cell.

```json
{"id": "actual", "title": "Actual sales", "unit": "KRW thousand", "format": "amount",
 "rows": {"in": "regions"},
 "cols": {"timeline": {"start": "start_month", "periods": 6, "step": "month"}},
 "cell": "SUMIFS({h:sales.amount_k},{h:sales.region},{row},{h:sales.month_end},{col})",
 "total": {"row": true, "col": true}, "key": false}
```

- `total.row` adds a total row (`=SUM(D6:D9)` under each column, cell type `total`); `total.col` adds a column
  headed `Total` (`=SUM(D6:I6)` on each row). With both, the corner cell is the grand total (`{blk:<id>.total}`).
  Totals are display totals (FAST 3.01-04).
- `key: true` makes the body cells `output` (bold); the key output is the grand total cell (both totals), or the
  only cell of a 1 by 1 grid, or the total cell of a one-column grid with a total row (or a one-row grid with a
  total column). Any other key grid is refused.

### Items

Line items over shared columns, one expression per item (FAST 2.02-01 calculation block).

```json
{"id": "bridge", "title": "Variance", "cols": {"same_as": "actual"}, "items": [
  {"id": "act", "label": "Actual", "unit": "KRW thousand", "expr": "{b:actual.total}"},
  {"id": "tg",  "label": "Target", "unit": "KRW thousand", "expr": "{b:tgt.total}"},
  {"id": "var", "label": "Variance", "unit": "KRW thousand", "expr": "{r:act}-{r:tg}"},
  {"id": "cum", "label": "Cumulative variance", "unit": "KRW thousand", "expr": "{prev}+{r:var}", "first": "{r:var}",
   "key": true, "note": "running sum of the variance"}]}
```

- An item has `id`, `label`, `expr`; optional `unit` (else the block's), `format`, `first` (the expression of the
  first column, needed whenever `expr` uses `{prev}`), `key`, `note` (written in the column after the last).
- The first cell of an item with `first` differs from the rest of its row on purpose; the manifest marks it
  (`items[].first: true`).
- A key item's key output is its cell in the last column.

### Measures

Rows of one dimension times named measure columns.

```json
{"id": "ytd", "title": "Year to date by region", "rows": {"same_as": "actual"}, "unit": "KRW thousand", "measures": [
  {"id": "act", "label": "Actual", "format": "amount", "expr": "SUM({grow:actual})", "total": "sum"},
  {"id": "tg",  "label": "Target", "format": "amount", "expr": "SUM({grow:tgt})", "total": "sum"},
  {"id": "pct", "label": "Variance %", "format": "rate", "unit": "%", "expr": "IFERROR({m:act}/{m:tg}-1,0)",
   "total": "same", "key": true}]}
```

- A measure has `id`, `label`, `expr`; optional `unit` (shown in a unit row under the headers), `format`, `total`
  (`none` default, `sum`, or `same`: the same expression evaluated on the total row, where `{m:...}` reads the
  total row), `key`, `note`.
- A key measure's key output is its total-row cell, or its only cell when the block has one row; otherwise build
  refuses it ("give it total sum or same").

### Rows and columns

| `rows` / `cols` | Becomes |
| --- | --- |
| `{"values": [...], "format": "..."}` | typed labels or headers; ISO dates become dates |
| `{"in": "<list input id>"}` | each label or header a link to the list's cell (`=inputs!$D$11`) |
| `{"timeline": {"start": "<scalar date input>", "periods": N, "step": "month"}}` (cols only) | first header `=EOMONTH(inputs!$D$6,0)`, next headers `=EOMONTH(D5,1)`; format `month` (FAST 1.01-03: one time ruler) |
| `{"same_as": "<block id>"}` | links to that block's labels or headers (`=$B6`, `=calc!D5`) |
| `cols` left out | one column headed `Value` |

`rows` may also carry `label` (written in B of the column-header row). Items blocks have no `rows`; measures blocks
have no `cols`.

## Placeholders

An expression is the body of an Excel formula, without the leading `=`, with `{kind:argument}` placeholders.
Build replaces each placeholder with an A1 reference. The workbook therefore holds plain A1 formulas: no defined
names (FAST 3.03-08) and no structured references (Google Sheets does not support `#This Row`, platforms A2.4).

| Placeholder | Allowed in | Becomes (example) |
| --- | --- | --- |
| `{in:<id>}` scalar input | every expression | `inputs!$D$6` |
| `{in:<id>}` list / grid input | every expression | the value range `inputs!$D$11:$D$14` / the grid body `inputs!$D$18:$I$21` |
| `{in:<id>.rows}`, `{in:<id>.cols}` | every expression | a grid's row headers `inputs!$B$18:$B$21` / column headers `inputs!$D$17:$I$17` |
| `{raw:<table>.<column>}` | every expression | the whole raw column `raw_sales!$F:$F` |
| `{h:<table>.<column>}` | grid, items, measures, checks | the whole helper column `h_sales!$J:$J` (a fetched raw column by its raw name) |
| `{this:<column>}` | helper columns | the same row of this helper sheet `H2` |
| `{row}` | grid, measures | this row's label `$B6` |
| `{col}` | grid, items | this column's header `D$5` |
| `{r:<item>}` | items | this column, the row of that item `D$22` |
| `{prev}` / `{prev:<item>}` | items | the previous column, this row / the row of that item `D$25`; the first column uses the item's `first` |
| `{m:<measure>}` | measures | this row, that measure's column `$D7` |
| `{g:<block>}` | grid | the same row and column of a grid with the same rows and cols `calc!D6` |
| `{grow:<block>}` | grid, measures | that grid's row at this row's index `calc!$D6:$I6` (on a total row: its total row) |
| `{gcol:<block>}` | grid, items | that grid's column at this column's index `calc!D$6:D$9` |
| `{b:<block>.<item>}` / `{b:<block>.total}` | items, grid | this column, the row of that item / that grid's total row `D$10` (the blocks share their cols) |
| `{blk:<block>}` / `{blk:<block>.total}` | every expression | the body range `calc!$D$14:$I$17` / the grand total cell `calc!$J$10` |
| `{blk:<block>.<item>}` | every expression | in an items block, the row of that item over the block's columns, absolute `calc!$D$40:$I$40` |
| `{cell:<key output>}` | every expression | a key output cell `summary!$F$11`; write `{cell:<block>.<id>}` when two blocks have the same key id |
| `{chk:<check id>}` | grid, items, measures, checks | a check's result cell `checks!$I$9` |

- The current sheet is never written as a prefix (FAST 3.04-02 to 3.04-07, platforms C1.44, not confirmed for the individual number).
- Anchoring: same-row helper references are relative (`H2`); whole columns, inputs and block ranges are absolute;
  row labels and column headers are anchored on one side (`$B6`, `D$5`), so one formula copies across a block
  (FAST 3.02-01).
- Text in double quotes is not scanned for placeholders (`">0"`, `"North"`, `"a{b}"`). Array constants such as
  `{1,2;3,4}` are not placeholders (and are a warning, `F-ARRAY`).
- `{blk:<block>.<item>}` names one item's row of an items block from outside the block. Build finds the row from
  the layout, so the reference follows the item when the items are reordered, and a check needs no row number.
  The worked example's check `var_pct_all` reads the items `var_pct`, `tgt` and `var` of the block `bridge`: actual
  `SUMPRODUCT({blk:bridge.var_pct},{blk:bridge.tgt})`, expected `SUMPRODUCT(--({blk:bridge.tgt}<>0),{blk:bridge.var})`.
  `{blk:<grid block>.total}` keeps its meaning. For an items block, `.total` names an item with the id `total` when
  there is one, and is refused otherwise. An unknown item is refused: `unknown item '<item>' of block '<block>' in
  <placeholder>`, with the nearest item id ("did you mean ...?").
- `{g:}`, `{grow:}`, `{gcol:}` and `{b:}` need the same rows, cols or both: give the block `"rows": {"same_as": ...}`
  and `"cols": {"same_as": ...}`.

## The rules every compiled formula must pass (design 6.6, 8)

After the placeholders are replaced, build checks every formula with `lib/formula.py`, the same module the audit
uses, so a built workbook passes the audit's formula rules by construction (F05). Problems at build time:

| Rule | Result at build | Message names |
| --- | --- | --- |
| nesting depth over `depth_limit` (`F-DEPTH`) | refused | the chain (`IFERROR > INDEX > MATCH`) and the part of your expression to move, with where to put it: a new helper column (`{this:<id>}`), a helper grid (`{g:<id>}`), a line item (`{r:<id>}`), a measure (`{m:<id>}`) or a key output (`{cell:<id>}`) |
| a typed number outside `allowed_literals` (`F-LITERAL`) | refused | the number; put it in an input and write `{in:<id>}` |
| a plain A1 reference or a defined name in the expression | refused | the reference; write a placeholder |
| a refused function (`F-FUNC-REFUSED`: OFFSET, INDIRECT, LAMBDA, Google-only functions) | refused | the catalogue's replacement (`references/functions.md`) |
| IF inside IF (`F-NESTED-IF`, FAST 3.03-07) | refused | lookup, MIN or MAX, or flags (`references/helper-patterns.md`) |
| a reference to another workbook, `#REF!`, a structured reference, text the tokenizer cannot read | refused | what to write instead |
| a listed function with too few or too many arguments (`F-ARGS`) | refused | the function, the count found and the counts allowed (see "Argument counts" below) |
| formula longer than `length_limit` (`F-LENGTH`) | warning | the largest argument to move |
| a flagged function (`F-FUNC-FLAGGED`: VLOOKUP, CHOOSE, LET, IFS, FILTER ...), a volatile function (`F-VOLATILE`), an unknown function (`F-FUNC-UNKNOWN`), an array constant (`F-ARRAY`), a line break (`F-LINEBREAK`) | warning | the replacement, or `settings.allow_functions` |

One finding is reported per expression and rule, with the first cell and the count of cells. Functions added to
Excel after 2007 are written with their `_xlfn.` prefix (`formula.to_file_formula`), so Excel does not show
`#NAME?` (CORE-F, tested in Excel 16.0).

Counting examples (from `references/functions.md`): `IFERROR(VLOOKUP(...))` 2, passes;
`IFERROR(INDEX(..., MATCH(...)))` 3, refused, move the `MATCH(...)`; `INDEX(grid, MATCH(...), MATCH(...))` 2,
passes; `--(A1>0)` 0; `{1,2}` counts nothing.

## Argument counts

Excel does not open a workbook whose formula gives a function too few or too many arguments (the check then
reports `X-OPEN`). The rule `F-ARGS` (an error) counts the arguments of the functions in the table below.

- The count is the number of arguments between the function's parentheses. An empty argument counts: `IF(A1,,1)` has
  3. A call with nothing between the parentheses has 0.
- A function outside the list is not counted.
- Build refuses an expression with `F-ARGS` (exit 2, nothing built) and names its JSON path in `model.spec.json`,
  the function, the count found and the counts allowed: `<NAME> has <n> argument(s); it takes <allowed> (<the
  call>)`. The fix: `give <NAME> <allowed> arguments` (`argument` when the count allowed is exactly 1; for SUMIFS,
  AVERAGEIFS, MAXIFS, MINIFS and COUNTIFS the word `arguments` stands inside the parentheses, for example
  `give SUMIFS a range and then pairs of a criteria range and its criterion (3, 5, 7, ... up to 255 arguments)`).
- `rotm.py formula` reports `F-ARGS` for one formula; the audit reports `F-ARGS` at the cell.
- Source: Microsoft's function reference (support.microsoft.com, one page per function).

| Function | Arguments allowed | The counts F-ARGS names |
| --- | --- | --- |
| `IF` | 2 to 3 | `2 or 3` |
| `IFERROR`, `IFNA`, `ROUND`, `ROUNDUP`, `ROUNDDOWN`, `MOD`, `COUNTIF`, `EOMONTH`, `EDATE` | exactly 2 | `exactly 2` |
| `AND`, `OR` | 1 to 255 | `1 to 255` |
| `NOT`, `ABS`, `INT`, `YEAR`, `MONTH`, `DAY` | exactly 1 | `exactly 1` |
| `SUMIF`, `AVERAGEIF`, `MATCH` | 2 to 3 | `2 or 3` |
| `INDEX` | 2 to 4 | `2 to 4` |
| `XLOOKUP` | 3 to 6 | `3 to 6` |
| `DATE` | exactly 3 | `exactly 3` |
| `SUMIFS`, `AVERAGEIFS` | 3 to 255, an odd count: a range, then pairs of a criteria range and its criterion (up to 127 pairs) | `a range and then pairs of a criteria range and its criterion (3, 5, 7, ... up to 255)` |
| `MAXIFS`, `MINIFS` | 3 to 253, an odd count (up to 126 pairs) | `a range and then pairs of a criteria range and its criterion (3, 5, 7, ... up to 253)` |
| `COUNTIFS` | 2 to 254, an even count: pairs of a criteria range and its criterion (up to 127 pairs) | `pairs of a criteria range and its criterion (2, 4, 6, ... up to 254)` |

## Checks

```json
{"name": "checks", "type": "checks", "checks": [
  {"id": "actual_ties_raw", "kind": "equal", "label": "Actual block total equals the raw sales total",
   "actual": "{blk:actual.total}", "expected": "SUM({raw:sales.amount})/{in:scale}"},
  {"id": "neg_months", "kind": "zero", "severity": "alert", "label": "Months with a negative cumulative variance",
   "actual": "COUNTIF({blk:bridge},\"<0\")"}]}
```

| `kind` | Keys | Cells on the checks sheet (row r) | Result in I r | Passes when |
| --- | --- | --- | --- | --- |
| `equal` | `actual`, `expected`, optional `tolerance` (a scalar input id, default `check_tolerance`) | D actual, E expected, G `=Dr-Er`, H the tolerance (a link) | `=--(ABS(Gr)>Hr)` | the difference is at most the tolerance |
| `range` | `actual`, `min`, `max` | D actual, E minimum, F maximum | `=--(OR(Dr<Er,Dr>Fr))` | minimum ≤ actual ≤ maximum |
| `zero` | `actual` | D actual (a count of bad items) | `=--(Dr<>0)` | the actual is 0 |
| `at_most` | `actual`, `bound` | D actual, E bound | `=--(Dr>Er)` | the actual ≤ the bound |

- Columns of the checks sheet: B id, C what it checks, D actual, E expected or minimum, F maximum, G difference,
  H tolerance, I result, J what it checks (the targets). Header row 4, checks from row 5 (design 9.1).
- A passing check holds 0 and shows `OK`; a failing check holds 1 and shows `FAIL` in red, through the number
  format `[Red]"FAIL";[Red]"FAIL";"OK"` (no fill, no conditional format; design 9.5). An error value inside a
  check stays visible.
- `actual`, `expected`, `min`, `max`, `bound` are expressions, or the numbers 0 and 1. Any other typed number is
  refused: put it on the inputs sheet.
- Optional: `severity` (`error`, default, or `alert`), `note`, `targets` (text shown in column J; default: the
  blocks and raw tables the expressions read), `format` (a number-format key for the value cells D to G, for example
  `amount2` or `rate2`; default: the format of the block, line item, measure, raw column or helper column the
  `actual` expression reads first, else General).
- A check of dates: when the value format is `date` or `month`, the Difference cell (G) shows a count of days
  (format `count`); with `datetime` it shows days and fractions of a day (`amount2`). Actual and Expected keep the
  date format, and the tolerance cell stays General. A difference of 0 therefore shows 0, not a date in 1900.
- `severity: "alert"`: written in a separate "Alerts" table below, not part of the master check (books B-36,
  guides L-37). The Result cell of an alert row has the cell type `alert`: it holds 1 when the alert is raised and
  shows `ALERT` in red, and holds 0 and shows `OK` when it is not, through the number format
  `[Red]"ALERT";[Red]"ALERT";"OK"`. It never shows `FAIL`; `check` and `audit` report a raised alert as C-ALERT
  (info).
- Every block of a `calc` or `output` sheet must be read by at least one check of severity error, through
  `{blk:<id>}` or a key output of it (`{cell:...}`); otherwise build warns `W-NO-CHECK` (P5: each calculation block
  against an independent total). An alert does not count. `references/checks.md` lists independent totals that fit
  common blocks.
- No value may read itself through others: build refuses (exit 2) a specification whose helper columns, blocks,
  line items, measures or checks read each other in a cycle (for example `tax` = `{this:gross}*{this:amount}` and
  `gross` = `{this:tax}+{this:amount}`), and lists the JSON paths of the expressions in the cycle (for example
  `sheets[3].columns[1].expr` and `sheets[3].columns[2].expr`). Excel gives such formulas no value; `rotm.py check`
  reports a formula cell without a value after recalculation as V-NO-VALUE.

### Checks build adds for every raw table t (design 9.2)

| Id | Kind | Actual | Against |
| --- | --- | --- | --- |
| `t_rows` | `range` or `equal` | `=COUNTA(raw_t!$A:$A)-1` | the contract's rows (inputs sheet); left out when the contract gives no rows |
| `t_total_<column>` | `equal` | `=SUM(raw_t!$X:$X)` | the control total and its tolerance (inputs sheet) |
| `t_nulls_<column>` | `zero`; `at_most` with `known_nulls` | `=COUNTA(raw_t!$A:$A)-COUNTA(raw_t!$X:$X)` | one per never-null column after the first; with a known exception against its agreed count (input `t_known_nulls_<column>`) |
| `t_cover` | `at_most` 0 | `=COUNTA(raw_t!$A:$A)-1-COUNT(h_t!$A:$A)` (raw rows not covered by helper rows) | only with a helper sheet |
| `t_beyond` | `zero` | `=COUNTIF(h_t!$B:$B,0)` (helper rows below the last raw row) | only with a helper sheet |
| `t_keys` | `zero`; `at_most` with `expect.key_repeats` | `=SUM(h_t!$<dup>:$<dup>)` (rows that repeat an earlier key) | only with the `dup` helper column; otherwise listed under "Checked by rotm.py check" with no cell; with a known exception against its agreed count (input `t_key_repeats`) |

These ids are taken: a spec check may not use them.

### Master check (design 9.4)

- `checks!C2`: `=--(OR(SUM(I5:I<last>)>0,COUNT(I5:I<last>)<>COUNTA(B5:B<last>)))`, depth 2. It fails when any error
  check fails, and when a result cell is empty or not a number (a deleted check).
- Linked as `=checks!$C$2` in C2 of every block sheet (FAST 2.03-05).
- A model needs at least one error check; one without raw tables and without checks is refused.

## Notes and cover

- `{"name": "notes", "type": "notes", "notes": [{"text": "Amounts are net of VAT.", "source": "contract sales"}]}`:
  one row per note, the text in D and the source in E (`note` cells, wrapped).
- The cover is generated: title, master check, purpose, contents (each sheet with its type, font size and
  purpose), a legend of the cell types the workbook uses (each sample cell styled as that type, with its meaning
  from the dictionary), the build record (tool and version, the spec's sha256, the dictionary and its overrides,
  the rules) and the limitations. Values are not on the cover.

## Landing raw data (`rotm.py land`, P2)

```
rotm.py land "<model>" --table sales                       # after agreement: queries/sales.sql and queries/sales.pq.m
rotm.py land "<model>" --table sales --csv extract.csv     # validate and write raw/sales.csv
rotm.py land "<model>" --table sales --csv new.csv --force # replace the snapshot (then rebuild, or refresh the query)
rotm.py land "<model>" --table sales --power-query         # attach a Power Query table to the built workbook (Excel)
```

- `land` refuses (exit 2) unless the contract is agreed. It never writes SQL for an unagreed contract (the
  contract gate, P1).
- `queries/<t>.sql`: `SELECT <source_expression> AS <name>, ... FROM <source.from> WHERE <source.where> ORDER BY
  <key>` in GoogleSQL for Connected Sheets (design 7.4). Names that are SQL keywords or not ASCII are quoted with
  back-ticks.
- `queries/<t>.pq.m`: Power Query M that reads `<model folder>\raw\<t>.csv` (`Csv.Document`, UTF-8, header
  promoted, the contract's columns selected, empty fields made null, typed with the contract types in culture
  en-US). The file holds the placeholder `<model folder>`, not a path of this computer; the attach puts the model
  folder's path in the text it sends to Excel. With `--pq-source database` it is a template that runs the SQL
  through `Value.NativeQuery`, with `[EnableFolding = true]` for the systems Microsoft lists (platforms B1.4) and
  the server and database left as `<...>` placeholders. The SQL inside that template quotes every column name for
  its connector: `[name]` for SQL Server (also for a system the plugin does not know), `"name"` for PostgreSQL,
  Snowflake, Redshift, SAP HANA and Oracle, backticks for BigQuery and MySQL. The template is not tested here (no
  database is reachable) and cannot be attached until the placeholders are filled.
- The stamp: the first line of each query file is a comment, `rotm-stamp: contract sha256:<hash>; body
  sha256:<hash>` (after `--` in the `.sql`, after `//` in the `.pq.m`): the agreed contract the file was written
  from and the hash of the text below the line. Every `land` checks both files before it writes anything:
  - no file: it is written;
  - the stamp names the agreed contract: the file is kept (an edit made after this agreement stays);
  - the stamp names an earlier contract and the text below it is unchanged: the file is written again, and a note
    says so;
  - the stamp names an earlier contract and the text was edited by hand, or the file has no stamp: refused (exit
    2, nothing written), naming the file. Then `--rewrite-queries` writes it again from the agreed contract (the
    old file is kept as `<file>.bak_<time>`), or `--keep-queries` stamps it with the agreed contract after you
    reconciled it with that contract by hand.
- The attach (`land --power-query`, and build for a raw sheet with landing `power_query`) refuses a
  `queries/<t>.pq.m` whose stamp is missing or names another contract, before Excel starts; build then warns
  W-PQ-NOT-ATTACHED with that message. `check --refresh` refuses to refresh a query written for another contract
  (exit 2, nothing refreshed).
- `--csv`: the CSV must have the contract's header in the contract's order. Each value must fit its type (integer,
  number with `.` decimals and no thousands separator, date `YYYY-MM-DD` (also `YYYY/MM/DD`, `YYYY.MM.DD`, or with a
  zero time), datetime `YYYY-MM-DD HH:MM[:SS]`, also with a `T` between date and time (a `Z` or a UTC offset such as
  `+09:00` is accepted and dropped, with L-PRECISION), boolean
  `TRUE`/`FALSE`/`1`/`0`); never-null columns must be filled; allowed values must hold; the key must be unique
  (exact text, case-sensitive); a text value may hold at most 32,767 characters (an Excel cell's limit, counted
  in UTF-16 code units as Excel counts them); the row count must fit the contract, and each control total must
  match exactly within the contract's tolerance and nothing else (the landed values are added as decimals; the
  totals accepted for this snapshot in `raw/<t>.controls.json`, when there are any, take the place of the
  contract's). Each failure is an error finding (`L-COLUMNS`, `L-FIELDS`, `L-TYPE`, `L-NULL`, `L-ALLOWED`,
  `L-KEY`, `L-LENGTH`, `L-ROWS`, `L-EMPTY`, `L-TOTAL`), exit 1, and nothing is written: the earlier snapshot
  stays. Whole numbers of 16 or more digits warn `L-DIGITS` (Excel keeps 15 significant digits).
- `L-PRECISION` (a warning, one per column and reason): a number with more than 15 significant digits; a datetime
  whose fraction of a second was cut (cut, never rounded up); a datetime whose `Z` or UTC offset was dropped (the
  time is landed as written). Decide with the user: round the values in the source, or land the column as text.
- `--encoding` (default `utf-8-sig`; `cp949` for a file saved by Korean Excel) and `--delimiter` read other CSVs.
- `raw/<t>.csv` is written normalised: UTF-8 without BOM, comma, header row, ISO dates, `.` decimals, empty field
  = null, `\n` line ends. The raw digest of the manifest is computed over exactly these values, so the audit and
  `check` can tell whether the raw sheet still holds the landed rows (R-CHANGED).
- When the workbook was built, `land` records the new digest in the manifest; the workbook shows the new rows after
  a rebuild (static) or a refresh (`rotm.py check <model> --refresh`, Power Query).
- `--power-query` needs Excel (exit 3 without it, with the raw sheet left static), a built workbook, and a
  `queries/<t>.pq.m` without placeholders. Excel loads the query as `tbl_raw_<t>` at A1 with BackgroundQuery off
  and waits until the refresh has finished (CORE-X). The manifest then records `landing: power_query` and
  `query_name: q_<t>`. A loaded table whose rows differ from the snapshot warns `W-PQ-DIGEST`: run
  `rotm.py check <model> --accept-refresh`.
- The query attached to the workbook reads the snapshot by the model folder's path that the attach filled in
  (the file `queries/<t>.pq.m` keeps the placeholder): after moving the model folder, run `land --table <t>
  --power-query` again before a refresh.
- After a refresh, `rotm.py check <model> --accept-refresh` records the refreshed rows as the landed data
  ([checks.md](checks.md), "What `rotm.py check` does"):
  - The control total of a table and column is `--control` given in this run; else the total recorded for this
    exact snapshot in `raw/<t>.controls.json`; else the agreed contract's total. Never the inputs sheet: when its
    control cell holds another value, `check` warns `D-CONTROL-INPUT`. A failing `<t>_total_<column>` check: find
    the missing or extra rows in `raw_<t>`; after a refresh with a new source total, give it with `--control`.
  - The manifest's raw entry keeps `digest` (the landed snapshot, which `land` updates), `workbook_digest` (the
    rows the raw sheet held when a `rotm.py` command last wrote them) and `contract_sha256` (the agreed contract
    build read). Changed rows are accepted only when they differ from `workbook_digest`; when the sheet still
    holds those rows and `raw/<t>.csv` holds rows landed after them, `R-CHANGED`: nothing is accepted and
    `raw/<t>.csv` is not changed (refresh the query, or rebuild with `--force`). A manifest without
    `workbook_digest` (built before v3): nothing is accepted until one rebuild.
  - `--refresh` asked for and no refresh ran: nothing is accepted, `raw/`, the controls files and the manifest
    are not changed, and the exit code is not 0. A workbook with no query: `X-NO-QUERY` (a warning; an error with
    `--accept-refresh`). `check --refresh` is refused before Excel starts while a query-loaded table's contract
    is missing, invalid, proposed, stale, or agreed only on the example channel.
  - `CT-VERSION`: the agreed contract's hash differs from the manifest's `contract_sha256`; land again if the rows
    change, and rebuild.
  - `D-TYPE` (`R-TYPE` in `audit`): a raw cell of an integer, number, date, datetime or boolean column stored as
    text. `D-KEY-SEPARATOR`: a key part holds `|`, which the joined key `dup_key` cannot tell apart.
  - A refresh to zero rows: `extend` clears every helper row; a later refresh with rows: `extend` writes them from
    the formulas the manifest keeps.

## What build writes, and when it refuses to overwrite

- `model.xlsx` (atomic replace), `model.manifest.json` (design 6.8: sheets, blocks with their ranges and checks,
  helper columns with their formulas, inputs, key outputs, checks, the master check and its links, the cell type
  of every written range), `reports/build.json` and the `build` step in `reports/status.json`.
- The workbook holds no cached values: Excel computes them when the file is opened (`fullCalcOnLoad`) or when
  `rotm.py check` recalculates it. The build report says "not recalculated" every time.
- An existing workbook is replaced without `--force` only when build owns it through the chain of writes from its
  own output: the output of a build, or the output of a `rotm.py` write (check and recalc after a recalculation in
  Excel, extend, theme, `land --power-query`, compat and split) whose recorded input was itself a workbook build
  owns. `reports/writes.json` in the model folder keeps each write's input and output hashes. A write whose input hash is not
  recorded does not count. An audit or a render records the file it read, not a file it wrote. So a workbook
  edited in Excel stays an edit after an audit and after a `check`: build refuses it without `--force`; with
  `--force` the old file is kept as `model.bak_<YYYYMMDD_HHMMSS>.xlsx`.
- Inputs are changed in the specification, then rebuilt: a value typed into the workbook's inputs sheet is not kept
  by a rebuild.
- The manifest's raw entries record `contract_sha256` (the agreed contract build read) and `workbook_digest` (the
  rows build wrote) next to the landed `digest`.
- A row or column header cell that shows an item of an input list takes the block's rows or cols `format`, else
  the list item's `format`, else the date format for a row list (the month format for a column list) whose first
  value is an ISO date, else General. A list whose `format` is `text` gives its header cells the text format `@`
  (the audit accepts `@` or General there). A text label keeps General when it is not an item of an input list
  (for example `Total`).
- The control totals on the inputs sheet come from the contract, or, for a snapshot accepted after a refresh with
  `rotm.py check --accept-refresh --control`, from `raw/<t>.controls.json` (the totals recorded with that snapshot's
  digest). The cell's note names the source.
- A workbook open in Excel cannot be replaced: build refuses and asks you to close it.
- `-o <name>.xlsx` builds another file, with its own manifest `<name>.manifest.json` beside it.

| Exit | When |
| --- | --- |
| 0 | built; no warning (with `--strict`) or no error |
| 1 | built, with warnings under `--strict` (W-NO-CHECK, W-CAPACITY, W-PQ-NOT-ATTACHED, F-LENGTH, ...) |
| 2 | refused: the spec, a contract or a snapshot has a problem (also a dependency cycle, or a helper capacity above the landed rows); every problem listed; nothing written |
| 3 | not available (openpyxl missing) |
| 4 | internal error: one line, the traceback in `<state dir>/last_error.txt` |

Findings of build: `W-NO-CHECK` (a block no check reads), `W-SELF-CHECK` (a check whose actual and expected
compile to the same expression or to links to the same cell), `W-CAPACITY` (helper rows fewer than raw rows),
`W-PQ-NOT-ATTACHED`, `W-PQ-DIGEST`, the warnings of the formula rules, `I-KEYS-OUTSIDE` (info: the key check runs
in `rotm.py check` only), `I-NO-MARKER` (info: the folder has no `rotm_model.json`, so the hooks do not watch it), `I-SPARSE-SHEET` (info: a
calculation sheet with size 8 from its role and fewer populated cells than `rules.sparse_sheet_cells`).

## Styles

Every cell gets its look from the style dictionary by cell type, through `lib/styles.py` (P6): `title`, `label`,
`heading`, `col_header`, `row_header`, `unit`, `input`, `calc`, `link` (a pure reference on the same sheet),
`import` (a pure reference to another sheet), `output`, `total`, `check`, `master_check`, `note`, `raw`. The default
dictionary: Arial everywhere; size 8 on raw, helper and calc sheets, 9 elsewhere and on every title; column
headers bold on D9D9D9; row headers and units on F2F2F2; inputs on FFF2CC (the named overrides
`input_blue_font` and `input_blue_on_fill` give blue inputs); no other fill (`references/style-dictionary.md`).
Column widths are set from the content (labels up to 45 characters, values up to 22 characters, notes up to 60
characters wrapped); the Normal style is Arial 9, so a cell typed into later is Arial too. No cell is styled by
hand: change the dictionary or the project override and rebuild, or run `rotm.py theme`.

## Worked example

`scripts/tests/data/build/model.spec.json` with `contracts/sales.contract.json` (agreed) and the 72-row
`raw/sales.csv`. Build output: 8 sheets, 981 formula cells, exit 0 with `--strict`. After recalculation in
Excel 16.0: master check 0, all 12 error checks 0, the alert 0, no error value, and every cell of the actual
block equal to a Python sum of the CSV (`test_recalculated_in_excel_every_check_holds_and_values_tie`).
Shortened below: the file also has `source` keys, helper-column labels, a note on `cum`, the alert check
`neg_months` and measure formats.

```json
{"rotm_spec": 1, "name": "sales_test", "title": "Sales versus target, January to June 2026 (build test)",
 "purpose": "Monthly sales by region against target, with variance.", "output": "model.xlsx", "product": "both",
 "limitations": ["Test model for tests/test_build.py; targets are invented."],
 "sheets": [
  {"name": "inputs", "type": "inputs", "items": [
    {"id": "start_month", "label": "First month", "value": "2026-01-31", "type": "date", "unit": "date", "source": "test"},
    {"id": "scale", "label": "Display scale (fixed at 1000)", "value": 1000, "type": "number", "format": "count",
     "unit": "KRW per unit shown", "source": "test", "valid": {"min": 1000, "max": 1000},
     "note": "Fixed: the target grid is typed in KRW thousand."},
    {"id": "regions", "kind": "list", "label": "Regions", "values": ["East", "North", "South", "West"], "format": "text"},
    {"id": "target", "kind": "grid", "label": "Target", "unit": "KRW thousand", "format": "amount",
     "rows": {"values": ["East", "North", "South", "West"], "label": "Region", "format": "text"},
     "cols": {"values": ["2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30", "2026-05-31", "2026-06-30"], "format": "month"},
     "values": [[1500, 1550, 1600, 1650, 1700, 1750], [1600, 1650, 1700, 1750, 1800, 1850],
                [1700, 1750, 1800, 1850, 1900, 1950], [1800, 1850, 1900, 1950, 2000, 2050]]}]},
  {"name": "raw_sales", "type": "raw", "table": "sales"},
  {"name": "h_sales", "type": "helpers", "table": "sales", "columns": [
    {"id": "month_end", "expr": "EOMONTH({this:inv_date},0)", "format": "month"},
    {"id": "amount_k", "expr": "{this:amount}/{in:scale}", "format": "amount1"}]},
  {"name": "calc", "type": "calc", "blocks": [
    {"id": "actual", "title": "Actual sales", "unit": "KRW thousand", "rows": {"in": "regions"},
     "cols": {"timeline": {"start": "start_month", "periods": 6, "step": "month"}},
     "cell": "SUMIFS({h:sales.amount_k},{h:sales.region},{row},{h:sales.month_end},{col})", "total": {"row": true, "col": true}},
    {"id": "tgt", "title": "Target", "unit": "KRW thousand", "rows": {"same_as": "actual"}, "cols": {"same_as": "actual"},
     "cell": "INDEX({in:target},MATCH({row},{in:target.rows},0),MATCH({col},{in:target.cols},0))", "total": {"row": true, "col": true}},
    {"id": "bridge", "title": "Variance", "cols": {"same_as": "actual"}, "items": [
     {"id": "act", "label": "Actual", "unit": "KRW thousand", "expr": "{b:actual.total}"},
     {"id": "tg", "label": "Target", "unit": "KRW thousand", "expr": "{b:tgt.total}"},
     {"id": "var", "label": "Variance", "unit": "KRW thousand", "expr": "{r:act}-{r:tg}"},
     {"id": "cum", "label": "Cumulative variance", "unit": "KRW thousand", "expr": "{prev}+{r:var}", "first": "{r:var}", "key": true}]}]},
  {"name": "summary", "type": "output", "blocks": [
    {"id": "ytd", "title": "Year to date by region", "rows": {"same_as": "actual"}, "unit": "KRW thousand", "measures": [
     {"id": "act", "label": "Actual", "expr": "SUM({grow:actual})", "total": "sum"},
     {"id": "tg", "label": "Target", "expr": "SUM({grow:tgt})", "total": "sum"},
     {"id": "pct", "label": "Variance %", "format": "rate", "unit": "%", "expr": "IFERROR({m:act}/{m:tg}-1,0)", "total": "same", "key": true}]}]},
  {"name": "checks", "type": "checks", "checks": [
    {"id": "actual_ties_raw", "kind": "equal", "label": "Actual block total equals the raw sales total",
     "actual": "{blk:actual.total}", "expected": "SUM({raw:sales.amount})/{in:scale}"},
    {"id": "tgt_ties_input", "kind": "equal", "label": "Target block total equals the target grid",
     "actual": "{blk:tgt.total}", "expected": "SUM({in:target})"},
    {"id": "cum_ties", "kind": "equal", "label": "Cumulative variance equals actual minus target",
     "actual": "{cell:cum}", "expected": "{blk:actual.total}-{blk:tgt.total}"},
    {"id": "pct_ties", "kind": "equal", "label": "Variance % equals actual over target",
     "actual": "{cell:pct}", "expected": "{blk:actual.total}/{blk:tgt.total}-1"}]},
  {"name": "notes", "type": "notes", "notes": [{"text": "Amounts are net of VAT.", "source": "contract sales"}]}]}
```

What build makes of it (from the built file):

| Cell | Formula |
| --- | --- |
| `h_sales!I2` | `=EOMONTH(E2,0)` |
| `h_sales!J2` | `=H2/inputs!$D$7` |
| `calc!D5`, `calc!E5` | `=EOMONTH(inputs!$D$6,0)`, `=EOMONTH(D5,1)` |
| `calc!D6` | `=SUMIFS(h_sales!$J:$J,h_sales!$F:$F,$B6,h_sales!$I:$I,D$5)` |
| `calc!D14` | `=INDEX(inputs!$D$18:$I$21,MATCH($B14,inputs!$B$18:$B$21,0),MATCH(D$13,inputs!$D$17:$I$17,0))` |
| `calc!D25`, `calc!E25` | `=D$24` (first), `=D$25+E$24` |
| `summary!D7` | `=SUM(calc!$D6:$I6)` |
| `summary!F11` | `=IFERROR($D11/$E11-1,0)` (total "same") |
| `checks!C2` | `=--(OR(SUM(I5:I16)>0,COUNT(I5:I16)<>COUNTA(B5:B16)))` |

## Common mistakes

| Mistake | What build does | Do instead |
| --- | --- | --- |
| `IFERROR(INDEX(x,MATCH(...)),0)` in one cell | refuses (depth 3) and names `MATCH(...)` | put the MATCH in a helper column or helper grid, then INDEX it (`references/helper-patterns.md`) |
| typing `0.1` or `1000` in an expression | refuses (`F-LITERAL`) | add an input item and write `{in:<id>}` |
| writing `A1`, `inputs!D6` or a name in an expression | refuses | use a placeholder; the layout is build's |
| a per-row calculation written in a grid block over raw rows | works, but each grid cell scans the whole table | one helper column per step on `h_<table>`, then SUMIFS over helper columns |
| reading a raw column cell by cell (`raw_sales!F2`) | not possible: there is no placeholder for it | fetch it on the helper sheet; whole-column reads survive a refresh |
| a block without a check | warns `W-NO-CHECK`; `--strict` exits 1 | add an `equal` check against an independent total (raw sum, input grid sum, another block) |
| a block read only by an alert | warns `W-NO-CHECK` | add a check of severity error |
| two expressions that read each other (a cycle) | refuses and lists both JSON paths | compute one of them from the inputs or raw columns, not from the other |
| `--capacity` above the landed rows, or `helper_spare_rows` above 0 | refuses | leave the helper rows equal to the landed rows; run `rotm.py extend` after a refresh |
| `COUNT` or `AVERAGE` over the helper column of a nullable number column | counts the empty cells, which show 0 | use the null flag: `COUNTIFS({h:t.<column>_null},0)` |
| `{blk:x.total}` on a grid with only a total row | refuses: no single grand total cell | add `"col": true`, or write `SUM({blk:x})` |
| `{prev}` without `first` | refused by the spec loader | give the item `"first"` |
| styling a cell in Excel by hand, or editing `model.xlsx` with a script | the next build refuses without `--force` (the file changed) | change the spec or the dictionary and rebuild |
| editing `raw/<t>.csv` or the raw sheet by hand | the audit reports R-CHANGED | land the new extract with `rotm.py land --force` |
| landing a CSV in another column order | `L-COLUMNS` error, nothing written | change the SELECT list to the contract's order |

## Verified and not verified

Verified on the build PC (Windows 11, Python 3.14, openpyxl 3.1.5, Excel 16.0), by `scripts/tests/test_build.py`
and `scripts/tests/test_land.py`:
- the example above builds with `--strict` in about 3 seconds (Python start included); Excel recalculates it with no error value; every
  check holds; the actual block equals the Python sum of the CSV;
- every formula of the built workbook passes `formula.check_formula` with depth limit 2 and length limit
  100 characters, and every helper column holds one relative form;
- the raw sheet's digest equals the snapshot's digest;
- a Power Query table attached over `raw/sales.csv` loads 72 rows; after the snapshot grows to 82 rows and is
  refreshed, `sales_cover` shows 10 uncovered rows and the master check fails; after it shrinks to 50 rows, no
  error value appears, `sales_beyond` shows the 22 helper rows below the raw rows and the master check fails, and
  after `rotm.py extend` clears them every check holds (`test_v2_raw_layer.py`, a helper `--({this:amount}=0)`
  counted 22 rows before `extend` and 0 after); the query is attached again on a rebuild;
- after a refresh accepted with `check --accept-refresh --control`, `build --strict` and `check --strict` exit 0 and
  the inputs sheet holds the accepted total; the acceptance run twice leaves one record;
- keys `001` and `1`, `Case` and `CASE`, `M*` and `Monday`, `A?` and `AB`, and two 20-digit texts that share their
  first 15 digits are distinct for the `dup` column in Excel, for a key of one and of two columns;
- a nullable amount gives the same COUNT on the raw column and through its null flag, and `lift` ties out;
- a hand-made circular pair of formulas fails `check --strict` with V-NO-VALUE on both cells.

Not verified:
- anything in Google Sheets or Connected Sheets (no Google access): the conversion of the `[Red]` and `_)` number
  formats, the unary `--`, and whole-column INDEX after an extract refresh (platforms E2, E5);
- the `--pq-source database` M template against any database;
- column widths are estimates from the content; renders (`rotm.py render`) are the check that nothing is cut off.

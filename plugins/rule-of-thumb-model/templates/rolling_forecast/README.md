# Template: rolling_forecast

Rolling forecast 3+9 by product, FY2026.

Closed months from landed sales, open months from a run rate and a growth driver per product, switched at the last actual month; full-year outlook against budget, with a cumulative corkscrew.

## Use it

1. `rotm.py init <new model folder> --template rolling_forecast` copies this folder into a new model folder without its
   `queries/`, `raw/`, `reports/`, built workbook and manifest. The sample extract stays in `data/`. The contracts
   come back as `proposed`.
2. Show the user `contracts/sales.contract.md` and record the reply with `rotm.py contract agree <new model folder>
   --table sales --reply "<their words>" --by <name>` (SKILL.md P1).
3. After the agreement, land the sample extract (or your own extract of the same table):

   ```
   rotm.py land <new model folder> --table sales --csv <new model folder>/data/sales.csv
   ```

   Land writes `raw/sales.csv` and `queries/sales.sql` and `queries/sales.pq.m`, each stamped with the agreed contract's
   hash (P2).
4. Build, audit, check and render: `rotm.py build <new model folder> --strict`, then SKILL.md P7. Change
   `model.spec.json` for your own model first (P3 to P6).

## Raw tables (contracts)

- `sales`: one row per sales line; key line_id; columns line_id (text), sale_date (date, date), product (text), revenue (number, KRW).

The shipped contracts carry an agreement marked as the template's own (channel `example`, "no user gave it"); build
accepts it only inside the plugin's folders.

## Sheets

- `inputs` (inputs)
- `raw_sales` (raw): sales lines of the closed months as landed (contract sales)
- `h_sales` (helpers): one row per sales line: month end and revenue in KRW thousand
- `calc` (calc): actuals, drivers, the 3+9 forecast and the cumulative corkscrew
- `summary` (output): full-year outlook against budget: the key result
- `checks` (checks)
- `notes` (notes)

Build also writes the cover, the generated checks of every raw table (row count, control total, empty cells in
never-null columns, coverage by helper rows, repeated keys) and the master check.

## Display scale and units

- The display scale (input `scale`) is fixed at 1000: its valid range is 1000 to 1000.
- It is fixed because the budget values and every unit label of this template are in KRW thousand. The sales
  revenue is in KRW; the helper column `revenue_k` divides each revenue by the scale.
- With another scale, the outlook would be in another unit than the budget, the variance % would be wrong and every
  check would still hold. Measured in Excel on the v4 specification: a scale of 1 gave a full-year variance % of
  +100,296.5 % in place of +0.40 %, and 16 of 16 checks held.
- Build refuses another value with exit 2 and writes nothing: `input 'scale' (Display scale (fixed at 1000)) holds
  <value>, outside its valid range 1000 to 1000`, followed by the input's note.
- A model in another unit needs the budget retyped and the labels changed. In `model.spec.json`, change:
  - the budget values (input `budget`), typed in the new unit;
  - every `"unit": "KRW thousand"` (13 places: the input `budget`, the helper column `revenue_k`, the blocks
    `actual`, `runrate`, `forecast`, `runrate_cell_diff` and `fy`, the items `month_total` and `cum`, and the measures `ytd`, `outlook`,
    `bud` and `var`);
  - the labels with "(KRW thousand)" (the helper column `revenue_k`: "Revenue (KRW thousand)") and the purpose of
    `h_sales` ("revenue in KRW thousand");
  - the helper column `revenue_k` (its id and label name the unit);
  - the scale input: its value, its valid range (`"valid": {"min": <new value>, "max": <new value>}`), its label
    and its note.
- Then rebuild, check and render (SKILL.md P7).

## Checks written in the specification

- `actual_ties_raw`: Actual block total equals the raw sales total.
- `months_flagged`: Actual months input equals the months flagged actual on the timeline.
- `months_total`: Actual months plus forecast months equals the timeline length.
- `runrate_ties`: Run rates times actual months equal the actual total.
- `growth_ties`: Looked-up growth equals the growth inputs.
- `outlook_ties`: Outlook total equals actuals plus forecast months at run rate times (1 + growth).
- `cum_ties`: Cumulative outlook in December equals the outlook total.
- `fy_pct_ties`: Full-year variance % times budget equals outlook less budget (budget not 0). A budget total of 0
  gives 0 % (IFERROR in Variance %) and the check then compares 0 with 0.
- `runrate_cell_ties`: Largest per-product difference of run rate x actual months and actuals. It reads the block
  `runrate_cell_diff` (the block after `corkscrew` on `calc`): for each product, the absolute difference between its
  run rate times the actual months and its actual total. `runrate_ties` compares sums only, so a run rate that is
  the same for every product can pass it; `runrate_cell_ties` then fails.
- `closed_months_landed`: Actual months with no landed row equal the closed months confirmed with no sales. It
  reads the block `landed` (the last block of `calc`, "Landed sales rows per month"): the item `rows` counts the
  landed sales rows of each month (`COUNTIFS` on the helper column `month_end`), and the item `empty` is 1 for a
  month flagged actual that holds no landed row. The check compares the sum of `empty` with the input
  `closed_months_no_sales` (0 as shipped). It fails when the landed extract ends before `last_actual`: see "The
  monthly roll".

## Texts that follow an input

The inputs `actual_months` (3) and `forecast_months` (9) give the split of the year. The texts below name the split
as fixed text. At the monthly roll (for example 4 and 8, with `last_actual` 2026-04-30), change each text with
the inputs in `model.spec.json`, then rebuild, check and render: step 4 of "The monthly roll" below.

| Input | JSON path | Text |
| --- | --- | --- |
| `actual_months`, `forecast_months` | `title` | Rolling forecast 3+9 by product, FY2026 |
| `actual_months`, `forecast_months` | `sheets[3].purpose` | actuals, drivers, the 3+9 forecast and the cumulative corkscrew |

- The title is shown in `cover!B1` and is the first line of this README; the purpose of `calc` is listed under
  "Sheets".
- No check compares these texts with the inputs. Derived from the formulas, not measured in Excel: after the roll
  with the new rows landed, every check holds and the title still says 3+9 until it is changed.
- The contract's `source.where` also follows the inputs: it ends at the last actual month (shipped:
  `s.sale_date BETWEEN '2026-01-01' AND '2026-03-31'`). Step 1 of "The monthly roll" changes it.

## The monthly roll

After a month is closed, roll the model in these steps, in this order. The example closes April 2026: the new
closing date is 2026-04-30, with 4 actual months and 8 forecast months. The static path is run by
`scripts/tests/test_v8_templates.py::test_rolling_forecast_roll_with_new_rows_holds_and_shows_the_closed_month`
(Excel). The Power Query commands are checked against rotm.py's options only: no database was reached.

1. Move the end date of the extract in the contract and get the contract agreed again.
   - The rows of the extract are scoped by the contract's `source.where`; shipped:
     `s.sale_date BETWEEN '2026-01-01' AND '2026-03-31'`. It is part of the agreed contract, and an agreed contract
     JSON is not edited by hand (`references/column-contract.md`, "When the contract must change after agreement").
   - Propose the contract again, with the new closing date in place of `<new closing date>` (for April:
     `2026-04-30`):

     ```
     rotm.py contract propose "<model folder>" --table sales --force --csv "<model folder>/raw/sales.csv" --key line_id --grain "one row per sales line" --source "proj.sales.lines s" --connector power_query --system BigQuery --where "s.sale_date BETWEEN '2026-01-01' AND '<new closing date>'" --unit revenue=KRW --type revenue=number --rows 1:100000 --total revenue=307355000 --purpose "Sales lines of the closed months for the rolling forecast"
     ```

     `--csv` reads the columns from the landed snapshot. `--total` keeps the contract's control total as agreed;
     the source's total of the new extract is given to land in step 3.
   - Edit the proposed `contracts/sales.contract.json` (a proposed contract may be edited: `column-contract.md`,
     "Procedure", step 4), so that every value other than the end date is the agreed one again:

     | Field | Value |
     | --- | --- |
     | `source.object` | `proj.sales.lines` (propose writes none when `--source` holds a space) |
     | `source_expression` of the columns `line_id`, `sale_date`, `product`, `revenue` | `s.line_id`, `s.sale_date`, `s.product_name`, `s.net_revenue_krw` |
     | `description` of the same columns | `sales line id`, `date of the sale`, `product`, `net revenue in KRW` |
     | `format` of the column `revenue` | removed: the agreed contract has none, and the raw sheet shows the format of the type number |
     | `notes` | the note that starts "display formats proposed from the sample" removed |

   - Write the `.md` again from the edited JSON:

     ```
     rotm.py contract show "<model folder>" --table sales
     ```

     Its section "Changes since version <n>" lists one change: `source.where`, with the old and the new end date.
   - Show the user `contracts/sales.contract.md` and record their reply to this version in their new words, given
     after they saw it:

     ```
     rotm.py contract agree "<model folder>" --table sales --reply "<the user's new words>" --by <name>
     ```

2. Write the query texts again from the new contract:

   ```
   rotm.py land "<model folder>" --table sales
   ```

   It writes `queries/sales.sql` and `queries/sales.pq.m` again when land wrote them and they were not edited since;
   the `WHERE` line of `queries/sales.sql` then ends at the new closing date. A query file edited by hand is
   refused: put the new end date into it and run the same command with `--keep-queries`, or write it again with
   `--rewrite-queries` (the replaced file is kept as `<file>.bak_<time>`).
3. Land the new extract.
   - Static snapshot: run the new `queries/sales.sql` at the source, export the result as CSV UTF-8, and land it
     with the revenue total that the source reports for the same query:

     ```
     rotm.py land "<model folder>" --table sales --csv "<export.csv>" --force --control sales.revenue=<source total>
     ```

     land checks the file against the new contract and the given total, writes `raw/sales.csv`, and records the
     total for the new snapshot in `raw/sales.controls.json`; build and check then use it.
   - Power Query (the raw sheet loaded by a query): attach the new `queries/sales.pq.m`, refresh, cover the new
     rows with helper rows and accept them with the source's total (`references/excel-power-query.md`, "Database
     source: the sequence", steps 6 to 10):

     ```
     rotm.py land "<model folder>" --table sales --power-query
     rotm.py check "<model folder>" --refresh --strict
     rotm.py extend "<model folder>" --table sales
     rotm.py check "<model folder>" --accept-refresh --control sales.revenue=<source total> --strict
     ```

4. Change the inputs in `model.spec.json` (the items of the inputs sheet): `last_actual` to the new closing month
   end (2026-04-30), `actual_months` to one more (4) and `forecast_months` to one fewer (8). Change the two texts of
   "Texts that follow an input" with them (the title and the purpose of `calc`: 3+9 becomes 4+8). Then rebuild,
   check and render:

   ```
   rotm.py build "<model folder>" --strict --force
   rotm.py check "<model folder>" --strict
   rotm.py render "<model folder>/model.xlsx"
   ```

5. Read the results.
   - On `checks`, the check `closed_months_landed` holds: every month flagged actual holds at least one landed
     sales row (actual 0, expected 0, with `closed_months_no_sales` 0).
   - On `calc`, block "Actual revenue (closed months)", row Total: the value under the closed month (for April,
     column G) in KRW thousand, against the source's total of that month. The test above compares them: the April
     total equals the April revenue of the export divided by 1000.
6. When the extract was not moved (the inputs rolled, the landed rows still ending on 2026-03-31): the closed month
   shows 0 on `calc`; `check --strict` exits 1; `closed_months_landed` is FAIL with actual 1, expected 0 after a
   roll to 4 actual months (actual 5 after a roll to 8); the master check is FAIL; every other check holds
   ("checks: 17 of 18 hold"). check prints `check closed_months_landed (Actual months with no landed row equal the
   closed months confirmed with no sales) shows FAIL (1); actual 1, expected 0, tolerance 0.001` and `the master
   check checks!C2 shows FAIL (1): failing checks closed_months_landed`. Go back to step 1
   (`test_rolling_forecast_roll_without_new_rows_fails_the_landed_months_check`, Excel).
7. A closed month that truly had no sale also fails `closed_months_landed`. Raise `closed_months_no_sales` by 1 for
   each such month only after the source confirmed that the month had no sale, then rebuild and check
   (`test_rolling_forecast_closed_month_without_sales_passes_with_the_input_set`).

## How it was built and tested

- Data: `make_data.py` writes `data/*.csv` and `data/controls.json` (deterministic, seed 20261125). The data are
  generated, not copied from a course or book file.
- Built with `rotm.py build --strict` (exit 0) and audited with `rotm.py audit --strict` (exit 0).
- Recalculated in Excel 16.0 by `rotm.py check --strict`: checks: 18 of 18 hold; master check 0.
- Every sheet rendered to `reports/render/` and looked at.

## Limitations

- Template of the rule-of-thumb-model plugin: the sales data are generated by make_data.py (seed 20261125); growth and budget are invented.
- The run rate is the average of the actual months; the forecast month value is the run rate times (1 + monthly growth), without compounding.
- The variance % of each product (measure `pct` of the block `fy`) is checked on its total row only (`fy_pct_ties`).
  A check cannot address one measure column's cells (`{blk:<block>.<id>}` names a line item of an items block only).
- Inputs are changed in `model.spec.json` and the model is rebuilt. A value typed into the workbook's inputs sheet is not kept by a rebuild, and build refuses to overwrite such a file without `--force`.

## Why this template

FastCampus "3+9 Forecast"; CFI 12-month rolling forecast (local guides, style survey pass 2); 271 catalogue rows match budget, plan or forecast names (research synthesis D12).

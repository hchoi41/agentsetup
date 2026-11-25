# Task: sales versus target by region, January to June 2026

The worked example of the rule-of-thumb-model skill. The question, as a user would ask it:

> Our invoice lines and the FY2026 v3 sales targets come from BigQuery. Land both through Power Query, then build
> the model in the workbook: monthly net sales by region against target, the variance and variance %, the
> cumulative variance over the six months, and the year-to-date result by region. Show amounts in KRW thousand.
> Alert me when a region-month is more than 15% below target.

## Inputs given with the task

- `data/sales.csv`: invoice lines, January to June 2026 (600 rows): invoice_id, line_no, inv_date, region,
  product, amount (KRW, net of VAT).
- `data/target.csv`: one row per region and month (24 rows): region, month_end, target_amount (KRW).
- `data/controls.json`: the row counts and totals the source system's own reports give: these are the contracts'
  control totals.
- `make_data.py` writes the three files (seed 20261125).

## Display scale and units

- The display scale (input `scale`) is fixed at 1000 (valid range 1000 to 1000).
- It is fixed because the task asks for amounts in KRW thousand and every unit label of an amount in this example is
  the fixed text "KRW thousand". The sales and the targets are landed in KRW; the helper columns `amount_k` and
  `target_k` divide each amount by the scale.
- The checks `actual_ties_raw` and `target_ties_raw` divide the raw totals by the same scale, so with another scale
  every check would still hold and every amount would be shown in another unit under the label "KRW thousand".
  Measured in Excel on the v5 specification (valid range 1 to 1,000,000): a scale of 1 gave a year-to-date actual
  total of 264,697,350 in `summary!D11` (unit label "KRW thousand") in place of 264,697.35, and 24 of 24 checks
  held.
- Build refuses another value with exit 2 and writes nothing: `input 'scale' (Display scale (fixed at 1000)) holds
  <value>, outside its valid range 1000 to 1000`, followed by the input's note.
- A model in another unit needs the labels changed. In `model.spec.json`, change:
  - every `"unit": "KRW thousand"` (13 places: the helper columns `amount_k` and `target_k`, the blocks `actual`,
    `target`, `variance` and `ytd`, the items `act`, `tgt`, `var` and `cum` of the block `bridge`, and the measures
    `act`, `tgt` and `var` of the block `ytd`);
  - the labels with "(KRW thousand)" (the helper columns `amount_k`: "Amount (KRW thousand)" and `target_k`:
    "Target (KRW thousand)"), the purposes of `h_sales` ("amount in KRW thousand") and `h_target` ("target in KRW
    thousand"), and the first note of the notes sheet ("the model shows KRW thousand");
  - the helper columns `amount_k` and `target_k` (their ids and labels name the unit);
  - the scale input: its value, its valid range (`"valid": {"min": <new value>, "max": <new value>}`), its label
    and its note.
- Then rebuild, check and render (SKILL.md P7).

## How the shipped example lands the data

- The task asks for both tables through Power Query. The shipped example lands the two CSV files `data/sales.csv`
  and `data/target.csv` as static snapshots (`raw/sales.csv` and `raw/target.csv`). They stand in for the results
  of the two queries, so that the example builds without Excel and without a database.
- Its raw sheets have no `landing` key in `model.spec.json`, so they are static: the workbook holds no query, and
  `rotm.py check examples/sales_vs_target --refresh` refreshes nothing (warning X-NO-QUERY).
- `land` wrote the query texts all the same: `queries/sales.sql` and `queries/sales.pq.m`, and the same for
  `target`. Each `.pq.m` reads the local snapshot `raw/<t>.csv`.

## Power Query variant

The shipped folder stays static. To land the two tables through Power Query, use a model made from this folder
(see the last paragraph of this file), with Excel:

1. In `<new folder>/model.spec.json`, set `"landing": "power_query"` on the two raw sheets, `raw_sales` and
   `raw_target`.
2. `rotm.py build <new folder> --strict`: build writes the snapshots and then attaches the queries `q_sales` and
   `q_target` through Excel; they read `raw/sales.csv` and `raw/target.csv`. Without Excel, build reports
   W-PQ-NOT-ATTACHED (strict mode exits 1).
3. `rotm.py check <new folder> --refresh`: it refreshes both queries through Excel, recalculates and reads every
   check.
4. For a database in place of the local CSV files: `references/excel-power-query.md`, section "Database source:
   the sequence".

## Variance % and a zero target

- The variance % grid is `IFERROR(variance / target, 0)`: a region-month with a target of 0 shows 0 %.
- The check `pct_grid_ties` ties the grid to actual and target: the sum over the region-months of variance % times
  target equals the sum of the variance over the region-months whose target is not 0. A region-month with a target
  of 0 is left out of both sides.
- In this extract every region-month has a target above 0 (the smallest is 3,000,000 KRW), so the check compares
  the percentages with the whole variance total, -3,302.65 KRW thousand.
- A wrong percentage formula fails this check: replaced by `{g:variance}-{g:variance}` (every percentage 0), the
  check shows 0 against -3,302.65 after recalculation in Excel (`scripts/tests/test_v2_landing.py`).
- The check `ytd_pct_ties` ties the year-to-date variance % (the key output `pct`) to the blocks: the variance %
  times the target total equals the variance total. A target total of 0 gives 0 % (IFERROR), and the check then
  compares 0 with 0; the check divides by nothing. With every target set to 0, the model builds and every check
  holds (`scripts/tests/test_v5_build.py::test_example_with_every_target_zero_holds`).
- The block "All regions by month" (`bridge`) has its own item `var_pct` (`IFERROR(variance / target, 0)` per
  month). The check `var_pct_all` ties it to the block's variance and target rows: the sum over the months of
  variance % times target equals the sum of the variance over the months whose target is not 0. A month with a
  target of 0 shows 0 % and is left out of both sides.
- The check names the items of `bridge` by id: `{blk:bridge.var_pct}`, `{blk:bridge.tgt}` and `{blk:bridge.var}`.
  build reads each item's row from the layout, so the check follows the items when they are reordered
  (`scripts/tests/test_v5_build.py::test_reordered_bridge_items_keep_the_check`).
- With `var_pct` replaced by `0`, `var_pct_all` fails and the master check fails after recalculation in Excel
  (`scripts/tests/test_v4_build.py::test_example_var_pct_check_fails_when_the_item_is_zero`).

## How the example was made (the skill's procedure)

| Step | Command (from the skill folder) | Result |
| --- | --- | --- |
| 0 | `rotm.py init examples/sales_vs_target --name sales_vs_target` | `rotm_model.json` |
| P1 | `rotm.py contract propose ... --table sales --csv data/sales.csv --key invoice_id,line_no ...`, the same for `target` | `contracts/*.contract.json` and `.md`, status proposed |
| P1 | source expressions and descriptions filled in; `rotm.py contract agree ... --channel example` | agreed, marked as the example's own agreement: no user gave it |
| P2 | `rotm.py land ... --table sales --csv data/sales.csv`, the same for `target` | `raw/*.csv` (static snapshots); `queries/*.sql` and `queries/*.pq.m`, each stamped with the agreed contract's hash |
| P3 to P6 | `model.spec.json` written; `rotm.py build examples/sales_vs_target --strict` | `model.xlsx`, `model.manifest.json`, exit 0 |
| P7 | `rotm.py audit ... --strict`; `rotm.py check ... --strict`; `rotm.py render .../model.xlsx` | exit 0; checks: 24 of 24 hold; alerts: 0 of 1 show a value (Excel 16.0); `reports/render/*.png` looked at |

`expected.py` computes the key results from the two CSV files with the standard library alone, without the
workbook, and writes `expected.json`; `scripts/tests/test_examples.py` compares them with the values Excel saved
in `model.xlsx`.

A model made from this folder with `rotm.py init <new folder> --template <this folder>` gets its contracts back as
`proposed`, and no `queries/`, `raw/`, `reports/` or workbook: its user must agree the contracts, then
`rotm.py land <new folder> --table sales --csv <new folder>/data/sales.csv` (the same for `target`) writes the
snapshots and the queries from the agreed contracts.

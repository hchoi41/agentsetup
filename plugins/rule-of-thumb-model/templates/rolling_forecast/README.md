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
  - every `"unit": "KRW thousand"` (12 places: the input `budget`, the helper column `revenue_k`, the blocks
    `actual`, `runrate`, `forecast` and `fy`, the items `month_total` and `cum`, and the measures `ytd`, `outlook`,
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

## How it was built and tested

- Data: `make_data.py` writes `data/*.csv` and `data/controls.json` (deterministic, seed 20261125). The data are
  generated, not copied from a course or book file.
- Built with `rotm.py build --strict` (exit 0) and audited with `rotm.py audit --strict` (exit 0).
- Recalculated in Excel 16.0 by `rotm.py check --strict`: checks: 16 of 16 hold; master check 0.
- Every sheet rendered to `reports/render/` and looked at.

## Limitations

- Template of the rule-of-thumb-model plugin: the sales data are generated by make_data.py (seed 20261125); growth and budget are invented.
- The run rate is the average of the actual months; the forecast month value is the run rate times (1 + monthly growth), without compounding.
- After closing a month: land the new sales; change the last actual month, the actual months and the forecast months in `model.spec.json` (the items of the inputs sheet); rebuild.
- Inputs are changed in `model.spec.json` and the model is rebuilt. A value typed into the workbook's inputs sheet is not kept by a rebuild, and build refuses to overwrite such a file without `--force`.

## Why this template

FastCampus "3+9 Forecast"; CFI 12-month rolling forecast (local guides, style survey pass 2); 271 catalogue rows match budget, plan or forecast names (research synthesis D12).

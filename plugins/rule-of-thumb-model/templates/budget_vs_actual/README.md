# Template: budget_vs_actual

Budget versus actual by cost centre, January to June 2026.

Monthly expense actuals from the general ledger against the budget by cost centre: variance, variance % and year to date, with the same actuals by account as an independent path.

## Use it

1. `rotm.py init <new model folder> --template budget_vs_actual` copies this folder into a new model folder without its
   `queries/`, `raw/`, `reports/`, built workbook and manifest. The sample extract stays in `data/`. The contracts
   come back as `proposed`.
2. Show the user `contracts/gl_actuals.contract.md` and record the reply with `rotm.py contract agree <new model folder>
   --table gl_actuals --reply "<their words>" --by <name>` (SKILL.md P1).
3. After the agreement, land the sample extract (or your own extract of the same table):

   ```
   rotm.py land <new model folder> --table gl_actuals --csv <new model folder>/data/gl_actuals.csv
   ```

   Land writes `raw/gl_actuals.csv` and `queries/gl_actuals.sql` and `queries/gl_actuals.pq.m`, each stamped with the agreed contract's
   hash (P2).
4. Build, audit, check and render: `rotm.py build <new model folder> --strict`, then SKILL.md P7. Change
   `model.spec.json` for your own model first (P3 to P6).

## Raw tables (contracts)

- `gl_actuals`: one row per ledger posting; key entry_id; columns entry_id (text), posting_date (date, date), cost_centre (text), account (text), amount (number, KRW).

The shipped contracts carry an agreement marked as the template's own (channel `example`, "no user gave it"); build
accepts it only inside the plugin's folders.

## Sheets

- `inputs` (inputs)
- `raw_gl_actuals` (raw): ledger postings as landed (contract gl_actuals)
- `h_gl_actuals` (helpers): one row per posting: month end and amount in KRW thousand
- `calc` (calc): actual, budget and variance by cost centre and month; actual by account
- `summary` (output): year to date by cost centre: the key result
- `checks` (checks)
- `notes` (notes)

Build also writes the cover, the generated checks of every raw table (row count, control total, empty cells in
never-null columns, coverage by helper rows, repeated keys) and the master check.

## Display scale and units

- The display scale (input `scale`) is fixed at 1000: its valid range is 1000 to 1000.
- It is fixed because the budget values and every unit label of this template are in KRW thousand. The ledger
  amounts are in KRW; the helper column `amount_k` divides each amount by the scale.
- With another scale, the actuals would be in another unit than the budget, the variance % would be wrong and every
  check would still hold. Measured in Excel on the v4 specification: a scale of 1 gave a year-to-date variance % of
  +97,420.8 % in place of -2.48 %, and 14 of 14 checks held.
- Build refuses another value with exit 2 and writes nothing: `input 'scale' (Display scale (fixed at 1000)) holds
  <value>, outside its valid range 1000 to 1000`, followed by the input's note.
- A model in another unit needs the budget retyped and the labels changed. In `model.spec.json`, change:
  - the budget values (input `budget`), typed in the new unit;
  - every `"unit": "KRW thousand"` (10 places: the input `budget`, the helper column `amount_k`, the blocks
    `actual`, `budget_grid`, `variance`, `by_account` and `ytd`, and the measures `act`, `bud` and `var`);
  - the labels with "(KRW thousand)" (the helper column `amount_k`: "Amount (KRW thousand)") and the purpose of
    `h_gl_actuals` ("amount in KRW thousand");
  - the helper column `amount_k` (its id and label name the unit);
  - the scale input: its value, its valid range (`"valid": {"min": <new value>, "max": <new value>}`), its label
    and its note.
- Then rebuild, check and render (SKILL.md P7).

## Checks written in the specification

- `actual_ties_raw`: Actual block total equals the raw ledger total.
- `budget_ties_input`: Budget block total equals the budget input grid.
- `variance_ties`: Variance block total equals actual minus budget.
- `accounts_tie`: Actual by account equals actual by cost centre.
- `ytd_pct_ties`: YTD variance % times budget total equals variance total (budget not 0). A budget total of 0 gives
  0 % (IFERROR in Variance %) and the check then compares 0 with 0.

## Texts that follow an input

The input `start_month` (First month of the model, 2026-01-31) and the timeline's `periods` (6 months, block
`actual`) give the months of the model. When the months change, change each text below with them in
`model.spec.json`, then rebuild, check and render.

| Input | JSON path | Text |
| --- | --- | --- |
| `start_month` | `title` | Budget versus actual by cost centre, January to June 2026 |

- The title is shown in `cover!B1` and is the first line of this README.
- The contract `gl_actuals` scopes the rows of its extract by its `source.where`:
  `j.account BETWEEN '6100' AND '6499' AND j.posting_date BETWEEN '2026-01-01' AND '2026-06-30'`. Its posting dates
  go with `start_month` and the 6 months: when the months change, the where clause changes too. It is part of the
  agreed contract, so change it as `references/column-contract.md`, "When the contract must change after
  agreement", says:
  1. `rotm.py contract propose "<model folder>" --table gl_actuals --force --csv "<model folder>/raw/gl_actuals.csv"`
     with `--where "<new where clause>"` and the options that give the agreed contract's other settings back
     (`--key`, `--grain`, `--source`, `--connector`, `--system`, `--unit`, `--type`, `--rows`, `--total`,
     `--purpose`); then edit in the proposed JSON what propose cannot write (the source expressions and the
     descriptions). `templates/rolling_forecast/README.md`, "The monthly roll", step 1, gives the whole command and
     the edits for one contract.
  2. `rotm.py contract show "<model folder>" --table gl_actuals`: its section "Changes since version <n>" lists the
     where clause.
  3. Show the user `contracts/gl_actuals.contract.md` and record their agreement in their new words:
     `rotm.py contract agree "<model folder>" --table gl_actuals --reply "<the user's new words>" --by <name>`.
  4. Land again: `rotm.py land "<model folder>" --table gl_actuals` writes the query texts with the new where
     clause; then land the new extract: `rotm.py land "<model folder>" --table gl_actuals --csv "<export.csv>"
     --force --control gl_actuals.amount=<source total>`.
  5. Change `start_month`, the timeline's `periods`, the budget grid's months and the title, then rebuild
     (`rotm.py build "<model folder>" --strict --force`), check and render.

## How it was built and tested

- Data: `make_data.py` writes `data/*.csv` and `data/controls.json` (deterministic, seed 20261125). The data are
  generated, not copied from a course or book file.
- Built with `rotm.py build --strict` (exit 0) and audited with `rotm.py audit --strict` (exit 0).
- Recalculated in Excel 16.0 by `rotm.py check --strict`: checks: 14 of 14 hold; master check 0.
- Every sheet rendered to `reports/render/` and looked at.

## Limitations

- Template of the rule-of-thumb-model plugin: the ledger data are generated by make_data.py (seed 20261125); the budget is invented.
- Expenses are shown as positive amounts; a negative variance is spend below budget.
- Budget values are input items in `model.spec.json`, shown on the inputs sheet; a landed budget table would be a second raw table.
- The variance % of each cost centre (measure `pct` of the block `ytd`) is checked on its total row only
  (`ytd_pct_ties`). A check cannot address one measure column's cells (`{blk:<block>.<id>}` names a line item of an
  items block only). The template has no variance % grid.
- Inputs are changed in `model.spec.json` and the model is rebuilt. A value typed into the workbook's inputs sheet is not kept by a rebuild, and build refuses to overwrite such a file without `--force`.

## Why this template

Budget versus actual is a common model in the surveyed files: FastCampus "Budget vs actual" template; the FP&A Budget & LT model (local guides A2); 76 catalogue rows match budget-vs-actual or variance names (research synthesis D12).

# Template: price_volume_mix

Price, volume and mix bridge, prior year to current year.

Revenue change from the prior year to the current year split into volume, mix and price effects per product; the three effects add up to the change (a bridge check).

## Use it

1. `rotm.py init <new model folder> --template price_volume_mix` copies this folder into a new model folder without its
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

- `sales`: one row per order line; key line_id; columns line_id (text), period (text), product (text), qty (integer, units), revenue (number, KRW).

The shipped contracts carry an agreement marked as the template's own (channel `example`, "no user gave it"); build
accepts it only inside the plugin's folders.

## Sheets

- `inputs` (inputs)
- `raw_sales` (raw): order lines of both periods as landed (contract sales)
- `h_sales` (helpers): one row per order line: revenue in KRW thousand
- `calc` (calc): units, revenue, prices, mix and the three effects per product
- `summary` (output): the bridge from prior-period to current-period revenue: the key result
- `checks` (checks)
- `notes` (notes)

Build also writes the cover, the generated checks of every raw table (row count, control total, empty cells in
never-null columns, coverage by helper rows, repeated keys) and the master check.

## Display scale and units

- The display scale (input `scale`) is fixed at 1000 (valid range 1000 to 1000).
- It is fixed because every unit label of an amount in this template is the fixed text "KRW thousand" (for the
  prices "KRW thousand per unit"). The revenue is landed in KRW; the helper column `revenue_k` divides each revenue
  by the scale.
- The checks `r_py_ties_raw` and `r_cy_ties_raw` divide the raw totals by the same scale, so with another scale
  every check would still hold and every amount would be shown in another unit under the label "KRW thousand".
  Measured in Excel on the v5 specification (valid range 1 to 1,000,000): a scale of 1 gave a current-period
  revenue of 56,854,500 in `summary!D10` (unit label "KRW thousand") in place of 56,854.5, and 14 of 14 checks held.
- Build refuses another value with exit 2 and writes nothing: `input 'scale' (Display scale (fixed at 1000)) holds
  <value>, outside its valid range 1000 to 1000`, followed by the input's note.
- A model in another unit needs the labels changed. In `model.spec.json`, change:
  - every `"unit": "KRW thousand"` (11 places: the helper column `revenue_k`, the measures `r_py`, `r_cy`, `vol`,
    `mix` and `price` of the block `pvm`, and the items `b_start`, `b_vol`, `b_mix`, `b_price` and `b_end` of the
    block `bridge`);
  - the two `"unit": "KRW thousand per unit"` (the measures `p_py` and `p_cy`);
  - the labels with "(KRW thousand)" (the helper column `revenue_k`: "Revenue (KRW thousand)") and the purpose of
    `h_sales` ("revenue in KRW thousand");
  - the helper column `revenue_k` (its id and label name the unit);
  - the scale input: its value, its valid range (`"valid": {"min": <new value>, "max": <new value>}`), its label
    and its note.
- Then rebuild, check and render (SKILL.md P7).

## Checks written in the specification

- `r_py_ties_raw`: Prior-period revenue equals the raw prior-period total.
- `r_cy_ties_raw`: Current-period revenue equals the raw current-period total.
- `units_tie_raw`: Units of both periods equal the raw units total.
- `bridge_ties`: The bridge ends at current-period revenue: the three effects add up to the change.

## Texts that follow an input

The inputs `py_label` (Prior period code, PY) and `cy_label` (Current period code, CY) select the raw lines of each
period. The texts below name the codes or the two periods as fixed text. When the inputs change, change each text
with them in `model.spec.json`, then rebuild, check and render.

- `py_label` must hold the prior period's code and `cy_label` the current period's code. With the two codes
  swapped, every check holds, every "PY" label shows the current period's lines and every "prior period" text shows
  the current period.
- With other codes (for example FY2025 and FY2026), the contract's allowed values of `period` change too (SKILL.md
  P1), and the texts that name PY and CY no longer name the codes.
- The contract `sales` scopes the rows of its extract by its `source.where`:
  `o.fiscal_year IN (2025, 2026) AND o.status = 'invoiced'`. Its fiscal years go with `py_label` and `cy_label`,
  and so does the source expression of `period`, `IF(o.fiscal_year = 2025, 'PY', 'CY')`: when the two periods move
  (for example to 2026 and 2027), both change. They are part of the agreed contract, so change them as
  `references/column-contract.md`, "When the contract must change after agreement", says:
  1. `rotm.py contract propose "<model folder>" --table sales --force --csv "<model folder>/raw/sales.csv"` with
     `--where "<new where clause>"` and the options that give the agreed contract's other settings back (`--key`,
     `--grain`, `--source`, `--connector`, `--system`, `--unit`, `--type`, `--rows`, `--total`, `--purpose`); then
     edit in the proposed JSON what propose cannot write (the source expressions, among them the new one of
     `period`, and the descriptions). `templates/rolling_forecast/README.md`, "The monthly roll", step 1, gives the
     whole command and the edits for one contract.
  2. `rotm.py contract show "<model folder>" --table sales`: its section "Changes since version <n>" lists the
     where clause and the source expression.
  3. Show the user `contracts/sales.contract.md` and record their agreement in their new words:
     `rotm.py contract agree "<model folder>" --table sales --reply "<the user's new words>" --by <name>`.
  4. Land again: `rotm.py land "<model folder>" --table sales` writes the query texts with the new where clause;
     then land the new extract: `rotm.py land "<model folder>" --table sales --csv "<export.csv>" --force --control
     sales.qty=<source total> --control sales.revenue=<source total>`.
  5. Change `py_label`, `cy_label` and the texts below when the codes change, then rebuild
     (`rotm.py build "<model folder>" --strict --force`), check and render.

Texts that name the codes PY and CY:

| Input | JSON path | Text |
| --- | --- | --- |
| `py_label`, `cy_label` | `limitations[1]` | Volume effect = (total units CY - total units PY) x mix PY x price PY; mix effect = total units CY x (mix CY - mix PY) x price PY; price effect = units CY x (price CY - price PY). Per product they add up to revenue CY - revenue PY. |
| `py_label`, `cy_label` | `sheets[3].blocks[1].measures[0].label` | Units PY |
| `py_label`, `cy_label` | `sheets[3].blocks[1].measures[1].label` | Units CY |
| `py_label`, `cy_label` | `sheets[3].blocks[1].measures[2].label` | Revenue PY |
| `py_label`, `cy_label` | `sheets[3].blocks[1].measures[3].label` | Revenue CY |
| `py_label`, `cy_label` | `sheets[3].blocks[1].measures[4].label` | Price PY |
| `py_label`, `cy_label` | `sheets[3].blocks[1].measures[5].label` | Price CY |
| `py_label`, `cy_label` | `sheets[3].blocks[1].measures[6].label` | Mix PY |
| `py_label`, `cy_label` | `sheets[3].blocks[1].measures[7].label` | Mix CY |
| `py_label`, `cy_label` | `sheets[6].notes[1].text` | A period code other than PY and CY is outside the bridge: the raw-total checks still hold, the contract's allowed values refuse it at landing. |

Texts that name the prior and the current period (true while `py_label` is the prior period's code and `cy_label`
the current period's code):

| Input | JSON path | Text |
| --- | --- | --- |
| `py_label`, `cy_label` | `title` | Price, volume and mix bridge, prior year to current year |
| `py_label`, `cy_label` | `purpose` | Revenue change from the prior year to the current year split into volume, mix and price effects per product; the three effects add up to the change (a bridge check). |
| `py_label`, `cy_label` | `sheets[3].blocks[0].items[0].label` | Total units, prior period |
| `py_label`, `cy_label` | `sheets[3].blocks[0].items[1].label` | Total units, current period |
| `py_label`, `cy_label` | `sheets[4].purpose` | the bridge from prior-period to current-period revenue: the key result |
| `py_label`, `cy_label` | `sheets[4].blocks[0].items[0].label` | Revenue, prior period |
| `py_label`, `cy_label` | `sheets[4].blocks[0].items[4].label` | Revenue, current period |
| `py_label`, `cy_label` | `sheets[5].checks[0].label` | Prior-period revenue equals the raw prior-period total |
| `py_label`, `cy_label` | `sheets[5].checks[1].label` | Current-period revenue equals the raw current-period total |
| `py_label`, `cy_label` | `sheets[5].checks[3].label` | The bridge ends at current-period revenue: the three effects add up to the change |

- The title and the purpose are also the first two lines of this README; the purposes of the sheets are listed
  under "Sheets".

## How it was built and tested

- Data: `make_data.py` writes `data/*.csv` and `data/controls.json` (deterministic, seed 20261125). The data are
  generated, not copied from a course or book file.
- Built with `rotm.py build --strict` (exit 0) and audited with `rotm.py audit --strict` (exit 0).
- Recalculated in Excel 16.0 by `rotm.py check --strict`: checks: 14 of 14 hold; master check 0.
- Every sheet rendered to `reports/render/` and looked at.

## Limitations

- Template of the rule-of-thumb-model plugin: the order lines are generated by make_data.py (seed 20261125).
- Volume effect = (total units CY - total units PY) x mix PY x price PY; mix effect = total units CY x (mix CY - mix PY) x price PY; price effect = units CY x (price CY - price PY). Per product they add up to revenue CY - revenue PY.
- Prices are average revenue per unit of each period.
- The prices and the mix shares of each product (`p_py`, `p_cy`, `mix_py`, `mix_cy`) are not checked cell by cell.
  They are measure columns of the block `pvm`, and a check cannot address one measure column's cells
  (`{blk:<block>.<id>}` names a line item of an items block only). Derived from the formulas, not measured in
  Excel: `bridge_ties` still holds when every product shows the average price of all products, because the effects
  then still add up to the change.
- Inputs are changed in `model.spec.json` and the model is rebuilt. A value typed into the workbook's inputs sheet is not kept by a rebuild, and build refuses to overwrite such a file without `--force`.

## Why this template

Max's own Variance_FlashReport template with variance bridges (local guides C-1).

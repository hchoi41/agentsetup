# Template library

Each folder is a complete model folder built by `rotm.py build` from its specification, in the style dictionary,
with generated data (`make_data.py`, seed 20261125). None is a copy of a course or book file: those files were
evidence for the conventions, not content to ship (spec 2.1).

| Template | Raw tables | What it shows | Checks in Excel |
| --- | --- | --- | --- |
| `budget_vs_actual` | `gl_actuals` (ledger postings); the budget is an input grid | monthly actual against budget by cost centre, variance, variance %, year to date; the same actuals by account as an independent path | 14 of 14 hold |
| `rolling_forecast` | `sales` (sales lines of the closed months) | 3+9 forecast: actual months, then run rate times (1 + growth) per product; a cumulative corkscrew; full-year outlook against budget | 18 of 18 hold |
| `price_volume_mix` | `sales` (order lines of two periods) | volume, mix and price effects per product that add up to the revenue change (a bridge check) | 14 of 14 hold |
| `ar_aging` | `open_items` (open invoices) | days past due, the bucket by MATCH on bounds then INDEX (in place of a nested-IF ladder), amount and count by customer and bucket | 13 of 13 hold |

The skill's own starter template, `skills/rule-of-thumb-model/templates/starter`, is the smallest complete model
(one raw table, one helper sheet, one grid, its checks; 9 of 9 hold). The worked example is
`skills/rule-of-thumb-model/examples/sales_vs_target` (26 of 26 hold; alerts: 0 of 1 show a value).

## Use a template

```
python -P -X utf8 -B "<SKILL>/scripts/rotm.py" init --list-templates
python -P -X utf8 -B "<SKILL>/scripts/rotm.py" init "<new model folder>" --template budget_vs_actual
```

- `init --template` copies the folder without its `queries/`, `raw/`, `reports/`, `model.xlsx` and manifest. The
  sample extracts stay in `data/`.
- Every contract comes back as `proposed`: the shipped agreement is the template's own (channel `example`).
- Show the user each contract (`contracts/<t>.contract.md`) and record the reply with `rotm.py contract agree`
  (SKILL.md P1).
- After the agreement, land each table from the sample extract or from your own extract:
  `rotm.py land "<new model folder>" --table <t> --csv "<new model folder>/data/<t>.csv"`. Land writes
  `raw/<t>.csv` and `queries/<t>.sql` and `queries/<t>.pq.m`, each stamped with the agreed contract's hash (P2).
- Then change the specification, build, audit, check and render (SKILL.md, P3 to P7).
- Inputs are changed in `model.spec.json` and the model is rebuilt. A value typed into the workbook's inputs sheet
  is not kept by a rebuild, and build refuses to overwrite such a file without `--force`.

## How each template was tested

- `rotm.py build --strict` and `rotm.py audit --strict`: exit 0.
- `rotm.py check --strict`: recalculated in Excel 16.0, every check 0, master check 0, no error value.
- `rotm.py render`: one PNG per sheet in `reports/render/`, each looked at (Arial 8 or 9 per sheet as the cover's
  contents table says, grey headers, no colour outside the dictionary, nothing cut off).
- `scripts/tests/test_examples.py` repeats the strict build and audit on a copy, reads the values Excel saved and
  confirms every check holds; `rotm.py selftest` repeats all of it.

## Why these four

The choice is the design's (section 17), made before the research synthesis existed, and is listed for Max. The
synthesis v2 (D12) counts file names in the surveyed catalogue: budget, plan and forecast models 271 rows; three
statements 185; DCF and valuation 134; budget versus actual and variance 76; SaaS and cohort models 66 (37 of them
Max's own work). It proposes `budget_vs_actual`, `rolling_forecast`, `three_statement_from_tb` and
`cohort_unit_economics`. The last two are not built in this version.

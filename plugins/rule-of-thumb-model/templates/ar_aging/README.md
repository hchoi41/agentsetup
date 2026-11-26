# Template: ar_aging

Receivables aging by customer at 30 June 2026.

Open invoices aged by days past due into buckets per customer (the bucket bounds and names are inputs), with invoice counts, the overdue share and the amount more than 90 days past due. The bucket is found with MATCH on the bucket bounds and INDEX on the bucket names instead of a ladder of nested IFs.

## Use it

1. `rotm.py init <new model folder> --template ar_aging` copies this folder into a new model folder without its
   `queries/`, `raw/`, `reports/`, built workbook and manifest. The sample extract stays in `data/`. The contracts
   come back as `proposed`.
2. Show the user `contracts/open_items.contract.md` and record the reply with `rotm.py contract agree <new model folder>
   --table open_items --reply "<their words>" --by <name>` (SKILL.md P1).
3. After the agreement, land the sample extract (or your own extract of the same table):

   ```
   rotm.py land <new model folder> --table open_items --csv <new model folder>/data/open_items.csv
   ```

   Land writes `raw/open_items.csv` and `queries/open_items.sql` and `queries/open_items.pq.m`, each stamped with the agreed contract's
   hash (P2).
4. Build, audit, check and render: `rotm.py build <new model folder> --strict`, then SKILL.md P7. Change
   `model.spec.json` for your own model first (P3 to P6).

## Raw tables (contracts)

- `open_items`: one row per open invoice; key invoice_id; columns invoice_id (text), customer (text), invoice_date (date, date), due_date (date, date), open_amount (number, KRW).

The shipped contracts carry an agreement marked as the template's own (channel `example`, "no user gave it"); build
accepts it only inside the plugin's folders.

## Sheets

- `inputs` (inputs)
- `raw_open_items` (raw): open invoices as landed (contract open_items)
- `h_open_items` (helpers): one row per open invoice: days past due, bucket number, bucket name, the open, over-90 and not-due amount in KRW thousand
- `calc` (calc): open amount and invoice count by customer and bucket
- `summary` (output): overdue share per customer: the key result
- `checks` (checks)
- `notes` (notes)

Build also writes the cover, the generated checks of every raw table (row count, control total, empty cells in
never-null columns, coverage by helper rows, repeated keys) and the master check.

## Display scale and units

- The display scale (input `scale`) is fixed at 1000 (valid range 1000 to 1000).
- It is fixed because every unit label of an amount in this template is the fixed text "KRW thousand". The open
  amounts are landed in KRW; the helper columns `open_k`, `over90_k` and `not_due_k` divide open amounts by the
  scale.
- The check `aging_ties_raw` divides the raw total by the same scale, so with another scale every check would still
  hold and every amount would be shown in another unit under the label "KRW thousand". Measured in Excel on the v5
  specification (valid range 1 to 1,000,000): a scale of 1 gave an open total of 351,130,000 in `summary!D15`
  (unit label "KRW thousand") in place of 351,130, and 13 of 13 checks held.
- Build refuses another value with exit 2 and writes nothing: `input 'scale' (Display scale (fixed at 1000)) holds
  <value>, outside its valid range 1000 to 1000`, followed by the input's note.
- A model in another unit needs the labels changed. In `model.spec.json`, change:
  - every `"unit": "KRW thousand"` (9 places: the helper columns `open_k`, `over90_k` and `not_due_k`, the blocks
    `aging` and `overdue`, and the measures `total`, `not_due`, `past_due` and `over90`);
  - the labels with "(KRW thousand)" (the helper columns `open_k`: "Open amount (KRW thousand)", `over90_k`: "Open
    amount more than 90 days past due (KRW thousand)" and `not_due_k`: "Open amount 0 or fewer days past due (KRW
    thousand)") and the purpose of `h_open_items` ("the open, over-90 and not-due amount in KRW thousand");
  - the helper columns `open_k`, `over90_k` and `not_due_k` (their ids and labels name the unit);
  - the scale input: its value, its valid range (`"valid": {"min": <new value>, "max": <new value>}`), its label
    and its note.
- Then rebuild, check and render (SKILL.md P7).

## The over-90 cutoff and the bucket lists

- The summary column "Over 90 days" (measure `over90`) is the open amount of the invoices more than 90 days past
  due. It sums the helper column `over90_k`: the open amount when `days_past_due` is above the input `over90_days`,
  else 0. It does not read the buckets.
- The input `over90_days` ("Over-90 cutoff in days (fixed at 90)") is fixed at 90 (valid range 90 to 90), because
  the label "Over 90 days" and the check label name 90 as fixed text. Build refuses another value with exit 2 and
  writes nothing: `input 'over90_days' (Over-90 cutoff in days (fixed at 90)) holds <value>, outside its valid range
  90 to 90`, followed by the input's note.
- The summary column "Not due" (measure `not_due`) is the open amount of the invoices 0 or fewer days past due. It
  sums the helper column `not_due_k` and does not read the first bucket. "Past due" is the open amount less "Not
  due"; "Past due %" is "Past due" over the open amount.
- The check `over90_ties` compares the over-90 total with `SUMIFS` of `open_k` over the rows whose `days_past_due` is
  above the cutoff: another path than the measure. The check `pct_ties` takes the not-due amount from `open_k` and
  `days_past_due` (0 or fewer), not from the first bucket.
- A model with another cutoff (for example 120 days) changes, in `model.spec.json`: the input `over90_days` (its
  value, its valid range, its label and its note), the label of the measure `over90` ("Over 90 days"), the label of
  the check `over90_ties`, the label of the helper column `over90_k`, the purpose, the purpose of `h_open_items`
  and the last limitation of the specification (they name 90), and the last note of the notes sheet. The ids `over90_days`, `over90_k`, `over90` and `over90_ties` name
  90 too; the helper sheet shows `over90_k` as a column header and the checks sheet shows `over90_ties`. Rename them
  together with every placeholder that names them.

## Checks written in the specification

- `aging_ties_raw`: Aging grid total equals the raw open amount.
- `counts_tie_raw`: Invoice count grid total equals the raw row count.
- `pct_ties`: Past due % times total equals total less not-due amount (total not 0). The not-due amount is read
  from the days past due (0 or fewer), not from the first bucket. A total of 0 gives 0 % (IFERROR in Past due %), so
  the actual side is 0; the expected side is then 0 less the not-due amount: 0 when no amount is open, not 0 (the
  check fails) when not-due and past-due amounts cancel out.
- `over90_ties`: Over-90 total equals the open amount more than 90 days past due. It reads the helper columns
  `open_k` and `days_past_due` and the cutoff, not the column `over90_k` that the measure sums, and not the buckets.

## Texts that follow an input

These texts name a value of an input as fixed text. When the input changes, change each text with it in
`model.spec.json`, then rebuild, check and render.

| Input | JSON path | Text |
| --- | --- | --- |
| `as_of` (Aging date, 2026-06-30) | `title` | Receivables aging by customer at 30 June 2026 |

- The title is shown in `cover!B1` and is the first line of this README.
- No check compares the title with the as-of date: with another date every check holds and the title still says
  30 June 2026.
- The texts that name the over-90 cutoff are listed in "The over-90 cutoff and the bucket lists" above; that input
  is fixed at 90.
- The contract `open_items` scopes the rows of its extract by its `source.where`:
  `a.snapshot_date = '2026-06-30' AND a.open_amount_krw <> 0`. Its snapshot date goes with `as_of`: with another
  aging date, the where clause changes too, or the days past due are counted from a date the extract was not taken
  on. It is part of the agreed contract, so change it as `references/column-contract.md`, "When the contract must
  change after agreement", says:
  1. `rotm.py contract propose "<model folder>" --table open_items --force --csv "<model folder>/raw/open_items.csv"`
     with `--where "<new where clause>"` and the options that give the agreed contract's other settings back
     (`--key`, `--grain`, `--source`, `--connector`, `--system`, `--unit`, `--type`, `--rows`, `--total`,
     `--purpose`); then edit in the proposed JSON what propose cannot write (the source expressions and the
     descriptions). `templates/rolling_forecast/README.md`, "The monthly roll", step 1, gives the whole command and
     the edits for one contract.
  2. `rotm.py contract show "<model folder>" --table open_items`: its section "Changes since version <n>" lists the
     where clause.
  3. Show the user `contracts/open_items.contract.md` and record their agreement in their new words:
     `rotm.py contract agree "<model folder>" --table open_items --reply "<the user's new words>" --by <name>`.
  4. Land again: `rotm.py land "<model folder>" --table open_items` writes the query texts with the new where
     clause; then land the new extract: `rotm.py land "<model folder>" --table open_items --csv "<export.csv>"
     --force --control open_items.open_amount=<source total>`.
  5. Change `as_of` and the title, then rebuild (`rotm.py build "<model folder>" --strict --force`), check and
     render.

## How it was built and tested

- Data: `make_data.py` writes `data/*.csv` and `data/controls.json` (deterministic, seed 20261125). The data are
  generated, not copied from a course or book file.
- Built with `rotm.py build --strict` (exit 0) and audited with `rotm.py audit --strict` (exit 0).
- Recalculated in Excel 16.0 by `rotm.py check --strict`: checks: 13 of 13 hold; master check 0.
- Every sheet rendered to `reports/render/` and looked at.

## Limitations

- Template of the rule-of-thumb-model plugin: the open items are generated by make_data.py (seed 20261125).
- Days past due = the as-of date minus the due date; a negative number means not yet due.
- Bucket bounds and names are inputs: change them in `model.spec.json` (the items of the inputs sheet) and rebuild;
  the two lists pair by position and the bounds ascend. The first bound must be at or below the smallest days past
  due, or MATCH finds no bucket for an invoice, it drops out of the grids and `aging_ties_raw` fails.
- What a change of the bucket lists changes: the columns of the aging grid and of the invoice-count grid on `calc`,
  and their headers (the bucket names).
- What it does not change: the summary columns "Open", "Not due", "Past due", "Over 90 days" and "Past due %", and
  the checks `aging_ties_raw`, `counts_tie_raw`, `pct_ties` and `over90_ties`. "Not due" and "Over 90 days" read the
  days past due, and "Open" is the total of the aging grid.
- "Past due %" is checked on its total row only (`pct_ties`). No check reads the percentage of each customer: a
  check cannot address one measure column's cells (`{blk:<block>.<id>}` names a line item of an items block only).
- Inputs are changed in `model.spec.json` and the model is rebuilt. A value typed into the workbook's inputs sheet is not kept by a rebuild, and build refuses to overwrite such a file without `--force`.

## Why this template

The nested-IF ladder (IF > IF > IF > IF > AND, 77 formulas in 10 surveyed workbooks) is a top deep chain (style survey section D); this template replaces it with MATCH on bounds, then INDEX.

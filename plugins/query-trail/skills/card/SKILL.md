---
name: card
description: "Internal phase of query-trail. Do NOT use for general SQL, 'reconcile' or 'how many' questions. Phase P1 (Context and cards): drafts the question and metric cards for Max and hashes them at G1a; called only by the query-trail run orchestrator."
user-invocable: false
---

# card (P1)

Purpose: turn Max's question into a question card and a metric card whose intent is fixed before any data is read. You draft; Max freezes; `card_freeze.py` hashes.

Commands use `QT_PY`, `PLUGIN`, `RUN` as the run skill defines them.

## 1. State check (first action)

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/lib/state.py" --state "$RUN/run_state.json" check --phase P1` — exit 2: do nothing and return.

## 2. Context (optional)

If a dctx registry exists: `"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/registry.py" --run-dir "$RUN" read`. Exit 0: use `dctx_read.json` (approved entries; drafts are unverified hints). Exit 1: the registry lock failed, G6. Exit 2: no registry, continue.

## 3. Draft the cards

1. Read `RUN/00_question.txt`: Max's question in his own words, saved verbatim by the run skill before P1. If the file is missing, stop and tell the orchestrator that the question was never asked; never invent one.
2. Write `01_question_card.md` with YAML front matter. `question:` quotes `00_question.txt` word for word, and the body quotes it again under "Max's words". `card_freeze.py` refuses a question card that does not quote the file.
3. Write `02_metric_card.md` with YAML front matter: the fields below, from Max's words only. Where he said nothing, leave the field out or blank: `card_freeze.py` lists it and you ask him (step 4). You read no data before G1a, and table names are unknown before P2, so every `source` field (chain, each `mece` entry, `pop_bands`) takes one of three forms: `deliverable` (the final output), a run-relative CSV path, or `step:<name>` (a step of the query; the name must match a step name in plan.json, which the decompose phase writes; logic_tests.py resolves it at P9 and refuses a name plan.json does not have). Never a warehouse table. Write any identifier as a placeholder (`{{ds}}.table`), never a project ID. One exception: the quote of `00_question.txt` (in `question:` and in the quoted block under "Max's words") is copied word for word, identifiers included; the word for word wins there: the question is quoted verbatim; a quoted line is exempt from the identifier scan because it matches 00_question.txt word for word, and placeholders apply everywhere else.
4. Show Max the cards in plain words (use "What the fields mean") and ask what is wrong; change them only on his words.

Card completion (after `card_freeze.py` exit 3): a field Max's own text in `00_question.txt` already states is prefilled from it and not asked again. His "pooled" for the chain fills `chain.method`. When his text gives one weighting word (pooled, equal-weighted or an average of the ratios) and nothing in it contradicts that word, also prefill `aggregation_weight` with it and tell him at G1a that it came from his words. Ask only for what is still missing or invalid, one field at a time. `aggregation_weight: not_applicable` means one entity: when the chain names `entity` columns (for example `[order_month]`, nine months), `card_freeze.py` lists not_applicable as invalid (exit 3, "not_applicable is for a single entity; this metric has entities by order_month: choose pooled or equal_weighted"); ask him to choose between those two.

G1a fields: `metric`, `populations`, `ratio_type`, `chain`, `aggregation_weight`, `reference_type`, `tolerance`, `headline`, `confidence_level`, `author_mode`. Optional: `sample_n`, `mece`, `pop_bands`. `confidence_level`, `author_mode` and `reference_type` must equal run_config.json. A `ratio_type` other than none needs a chain block (`chain: none` is refused, exit 2). Without a `mece` list the MECE test is NOT_RUN and required; write `mece: none` only when Max says no split applies. `headline` needs both `field` and `claim`. At G1a name `aggregation_weight` explicitly with its value (and "prefilled from your words" when it was). Column names on the card (the headline field, the chain's columns, MECE part columns) are provisional until G2: tables are confirmed at G2 (P2), so tell Max at G1a that these names may still change at G1b, where the bindings and output contract fix them.

## What the fields mean

Plain words for Max, one example each. `card_freeze.py` prints the same meaning next to every missing or invalid key.

- **question** (question card): Max's question as he said it, quoted from `00_question.txt`. Example: `question: "Monthly revenue per active customer, Jan to Sep 2025."`
- **metric**: what is measured, in one line. Example: `metric: "revenue per active customer, by order month"`.
- **populations**: who or what is counted on top (numerator) and underneath (denominator). Example: `populations: {numerator: "completed order lines", denominator: "customers with a completed order in the month"}`.
- **ratio_type**: the kind of ratio.
  - `cohort`: a group fixed at a start, followed over time. Example: January sign-ups still active in March / January sign-ups.
  - `flow`: events in a period over a stock or another flow. Example: orders per day.
  - `share`: a part over its whole. Example: completed orders / all orders.
  - `per_entity`: an amount per counted thing. Example: revenue per active customer.
  - `none`: a plain total or count, no ratio (then `chain: none`).
- **chain** (the 톱니 사슬, "sawtooth chain"): the ratio written as links that multiply back to it, like gears: each link's denominator is the next link's numerator, so the middle terms cancel. Example: revenue / customers = (revenue / orders) x (orders / customers). Keys:
  - `source`: where the chain's columns are read: `deliverable` (the final output, the default), a run-relative CSV such as `aggregates/orders_detail.csv`, or `step:<name>` (a step name that must exist in plan.json).
  - `entity`: a list of the columns that name one entity. Example: `entity: [month]`.
  - `method`: how each link's ratio is formed: `pooled` (sum all tops and all bottoms, then divide) or `within_entity` (divide inside each entity first). Example: `method: pooled`.
  - `links`: one entry per link, each with `name` (a short label, e.g. `revenue_per_order`), `numerator` and `denominator` (the columns on top and underneath, e.g. `revenue`, `orders`), `numerator_population` and `denominator_population` (who is counted on top and underneath, in words, e.g. `"completed orders"`). A link's `denominator_population` must be word for word the next link's `numerator_population`.
  - `final`: the overall ratio as `{numerator, denominator}`: the first link's numerator column and the last link's denominator column. Example: `final: {numerator: revenue, denominator: customers}`. Optional; it defaults to exactly that.
  - `headline_column`: optional, the output column that holds the ratio per entity, recomputed as a check. Example: `rev_per_customer`.
- **aggregation_weight**: how the final metric combines entities, after the chain is checked. `pooled`: total over total, so big entities weigh more (revenue of all months / customers of all months). `equal_weighted`: the average of each entity's ratio, every entity counts once (mean of the monthly ratios). `not_applicable`: there is one entity (a card whose chain has `entity` columns has several, so `card_freeze.py` refuses not_applicable there). This is not `chain.method`: `method` says how each link is formed; `aggregation_weight` says how the final number is averaged. Example: `aggregation_weight: pooled`.
- **reference** (`reference_type`) and **independence**: the outside number the result is compared with, and how independent it is. `none`, `gl_trial_balance`, `control_total`, `bi_dashboard` or `synthetic`. Only an independent general-ledger trial balance or control total (IND-5) can make the report say "reference agrees within tolerance"; a BI dashboard reads the same source (same-source, "not independent"); `synthetic` is a demonstration, not evidence. The provenance (source, extraction method, as_of, grain, basis, tolerance, `independence`) lives in run_config.json `reference`. Example: `reference_type: none`.
- **tolerance**: how far two numbers may differ and still count as equal, fixed now, before any result. A number, or one number per test. Example: `tolerance: 0.01` or `tolerance: {chain: 0.0001, mece: 0.01}`.
- **headline**: `field` is the output column reported as the headline; `claim` is the strongest verdict Max wants to be able to say about it: one of the report's verdict words (`consistent`, `consistent (decomposition check)`, `reconciled (provisional)`, `reconciled`, `verified against reference`, `not reconciled`) or `none`. `claim` is required once `field` is set; a blank claim is listed as missing. The headline flag: when the verdict comes out weaker than the claim, the report flags "unsupported headline flag" at G16. Example: `headline: {field: rev_per_customer, claim: "reconciled"}`.

  Choose the claim from the words the run can reach (spec 4.4). The comparison is by strength (report.py `strength`): the leading word counts, so `consistent` is met by "consistent (decomposition check)"; "sampling incomplete" or "not reconciled" in the verdict lowers it below every positive claim, and a PROVISIONAL pin ranks a verdict one step below its FINAL form ("reconciled (provisional)" does not meet a claim of `reconciled`: G16 asks, "pin PROVISIONAL"; it still meets `consistent`). G16 asks only when the claim is stronger than the verdict.

  | author_mode | Independence | Strongest wording the run can reach (any confidence level) |
  |---|---|---|
  | `single` (the default) | IND-0 | "consistent (decomposition check)" at most, at level 60 as at 100; a reference only adds its suffix |
  | `second:codex` | IND-2 + IND-3 (isolation attested) | "reconciled (provisional)" with a PROVISIONAL pin (one step below "reconciled"; claim `consistent` or `reconciled (provisional)` to avoid G16); "reconciled" with a FINAL pin; "verified against reference" only with an independent reference (`gl_trial_balance`, `control_total`) that agrees, a FINAL pin and complete source coverage in sampling |
  | `second:max` | IND-4 (author-adjudicated) | as `second:codex` |

  Any unexplained failure gives "not reconciled". The confidence level changes which checks run and how much sampling can cover, not the top word, except that "verified against reference" needs every deliverable measure recomputed from source for every sampled unit.
- **confidence_level**: 60, 70, 80, 90 or 100, equal to run_config.json. Example: `confidence_level: 60`.
- **author_mode**: `single`, `second:codex` or `second:max`, equal to run_config.json. Example: `author_mode: single`.
- **sample_n** (optional): rows sampled per unit, a whole number. Example: `sample_n: 60`.
- **mece** (optional list): a split of the population into parts that do not overlap and leave nothing out ("mutually exclusive, collectively exhaustive"). Each entry: `name`, `source` (required: `deliverable`, a run-relative CSV, or `step:<name>`), `universe` (required: which rows are split, as a condition), and either `parts` (`{part name: condition}`) or `part_column` with `domain` (the allowed values); optional `measure` (a column whose parts must sum to the total) and `total`. Example: `mece: [{name: order_status, source: aggregates/orders_detail.csv, universe: "order_id IS NOT NULL", parts: {complete: "status = 'Complete'", other: "status <> 'Complete'"}, measure: amount}]`. **`mece: none`**: Max says no split applies; the test is listed as not applicable, not failed. **Conditions are SQL, not prose** (checked at G1a with sqlglot, BigQuery dialect; logic_tests.py refuses, exit 2, any that does not parse): `universe` and every part condition are SQL boolean expressions over the source step's columns. Universe: `TRUE` when the source step already holds the population (the usual case for `deliverable` or a step that already filtered it), else a condition such as `order_id IS NOT NULL` or `created_at >= '2025-01-01'`; never "all orders created in the month". Parts: `{complete: "status = 'Complete'", cancelled: "status = 'Cancelled'", other: "status NOT IN ('Complete', 'Cancelled') OR status IS NULL"}`. Translate Max's words into such a condition, read it back to him in plain words, and keep his words in the card's prose, not in the field.
- **pop_bands** (optional): an allowed period-over-period change in percent, fixed now. Keys `source` (required, the same three forms), `period_column`, `value_column`, optional `series_columns`, `lower_pct`, `upper_pct`. Example: `pop_bands: {source: deliverable, period_column: month, value_column: revenue, lower_pct: -30, upper_pct: 30}`. Optional `universe` and `condition`: SQL conditions like the MECE ones (checked the same way) that select the rows the bands judge; logic_tests.py applies both as a WHERE on the source before the comparison (a row where a condition is NULL is left out), and a condition naming a column the source does not have makes the check NOT_RUN (required), never PASS. Example: `pop_bands: {source: aggregates/monthly_by_channel.csv, period_column: month, value_column: revenue, series_columns: [channel], condition: "channel = 'online'", lower_pct: -30, upper_pct: 30}`.

Worked metric card (front matter):

```yaml
metric: "revenue per active customer, by order month"
populations: {numerator: "completed order lines", denominator: "customers with a completed order in the month"}
ratio_type: per_entity
chain:
  source: deliverable
  entity: [month]
  method: pooled
  links:
    - {name: revenue_per_order, numerator: revenue, denominator: orders,
       numerator_population: "completed orders", denominator_population: "completed orders"}
    - {name: orders_per_customer, numerator: orders, denominator: customers,
       numerator_population: "completed orders", denominator_population: "customers with a completed order"}
  final: {numerator: revenue, denominator: customers}
  headline_column: rev_per_customer
aggregation_weight: pooled
reference_type: none
tolerance: 0.01
headline: {field: rev_per_customer, claim: "reconciled"}
confidence_level: 60
author_mode: single
mece: none
```

## 4. Hash

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/card_freeze.py" --run-dir "$RUN" --gate G1a`

| Exit | Meaning | Next |
|---|---|---|
| 0 | intent hashed in `card_hashes.json`; a `gate_request.json` of G1a left by an earlier exit 3 is archived to `gates/stale_G1a_request_<time>.json` | hand G1a |
| 3 | card incomplete: `gate_request.json` (G1a) lists each missing or invalid key with its meaning, e.g. `02_metric_card.md:chain.links[1].denominator_population missing (who is counted underneath this link, ...)`; `card_completeness.csv` has the same rows | this is a question for Max, not a decision: ask him for exactly those keys through the orchestrator (one AskUserQuestion, the meanings as prompts), write his words into the card, rerun. Record nothing with decide.py; the G1a decision comes after exit 0 |
| 1 | the cards changed after Max approved them at G1a (decisions.csv has a G1a `approve` row in this attempt) | a card changed after its approval is a new run: tell the orchestrator |
| 2 | state or file missing, or a ratio card with `chain: none` (the message names the field) | report and stop |

The hash is provisional until Max approves. At G1a he may reject and say what to change: record his `reject` with decide.py, redraft on his words, run `card_freeze.py` again (exit 0: the new hash replaces the old one, which is kept under `card_hashes.json` `G1a.superseded`), and ask G1a again; his approve then needs `--supersede`, because G1a has one row per attempt. Once a G1a `approve` row exists in this attempt, a changed card exits 1.

## Bindings at G1b

At P2, after G2 confirms the tables, the metric card gets a `bindings` block and the run gets `03_output_contract.json`; `card_freeze.py --gate G1b` checks both and lists each missing or wrong item as one row of `card_completeness.csv` with its meaning (exit 3, `gate_request.json` G1b), as at G1a.

`bindings` (in `02_metric_card.md`):

- `tables`: the tables read, in placeholder form (`{{ds}}.qt_orders` or `ds.qt_orders`), never a project ID.
- `joins`: a list of `{left, right, on_keys: [[left column, right column], ...], type: inner or left}` (default inner; every key pair is applied); both tables must be in `tables`; `[]` for one table. Older cards may say `on`, which YAML reads as true unless quoted (`"on":`).
- `measures`: `{name: {expr, table, filter (optional)}}`, e.g. `revenue: {expr: SUM(sale_price), table: "{{ds}}.qt_order_items"}`; each `table` must be in `tables`.
- `derived`: `{name: expression over measure names}`, e.g. `rev_per_customer: revenue / NULLIF(customers, 0)`; `{}` when there are none.
- `date`: `{table, column, truncation, timezone}`: the date that places a row in a period; `truncation` is HOUR, DAY, WEEK, ISOWEEK, MONTH, QUARTER, YEAR or NONE; `table` must be in `tables`.
- `scd2_flags`, `semi_additive_columns`: lists (`[]` when there are none); `reference_extract` when `reference_type` is not none.

`03_output_contract.json` (checked against `config/output_contract.schema.json` when that file exists; otherwise this minimum):

- `grain`: the list of columns that name one output row.
- `columns`: the ordered output columns, each `{name, type, nullable}`; `type` is DATE, TIMESTAMP, STRING, INT64, FLOAT64, NUMERIC, BIGNUMERIC or BOOL; `nullable` is true or false.
- `key`: the key columns, each an output column (`[]` when the output has no key).
- `headline`: the output column reported as the headline.

Every output column must be a measure, a derived name or a grain column.

Weight against the grain (fix r14): when `03_output_contract.json` has a non-empty `grain` and the sealed card says `aggregation_weight: not_applicable`, G1b exits 3 with `02_metric_card.md:aggregation_weight invalid (not_applicable (sealed at G1a) but the output contract is grouped by <grain columns>; confirm on the record that no combined total is wanted, or start a new run with pooled or equal_weighted)`. This is a question for Max, not a card fix: the weight is in the G1a hash, so never edit the card. Ask him through the orchestrator (one AskUserQuestion, options confirm / new run): "The output is grouped by <cols> and the card says the final number is not averaged across them. Is that what you want? confirm / new run". On confirm, record his words with
`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/decide.py" --run-dir "$RUN" --gate G1b --outcome confirmed --check-id aggregation_weight:not_applicable --reason "<his words>" --by Max --answer "<his answer, verbatim>"`
and rerun `card_freeze.py --gate G1b` (exit 0; `card_hashes.json` G1b records `aggregation_weight_confirmed: decisions.csv#N`, and P9's `final_metric` row says "not_applicable confirmed at G1b (decisions.csv#N)"). `confirmed` is accepted at G1b with this check-id only. On new run: tell the orchestrator; a run with pooled or equal_weighted is a new run (the G1a hash differs). An ungrouped contract (`grain: []`) or a pooled / equal_weighted card raises no row.

`"$QT_PY" -I -X utf8 -B "$PLUGIN/scripts/card_freeze.py" --run-dir "$RUN" --gate G1b` — exit 0: bindings hashed, hand G1b; 3: ask Max for the listed items (one AskUserQuestion), write them, rerun (the `aggregation_weight` row is answered with decide.py as above, never by a card edit); 2: a blocking trap is unresolved, or a file is missing; 1: the G1a intent changed (a new run).

## Never

Freeze a card or answer a card question for Max. Tolerances, the reference and the level are his, fixed here before any result exists.

## Artifacts left

`01_question_card.md`, `02_metric_card.md`, `card_hashes.json` (G1a), `card_completeness.csv`, decisions.csv row G1a.

## Hands to

G1a (and G6 when a registry draft awaits approval), then P2.

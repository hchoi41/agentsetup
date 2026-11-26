# Helper patterns: rewriting deep formulas as helper cells

Max's rule: "the realistic approach is having less than 3 nested functions at most. (if we require something more than that perhaps we need a helper column)" (Max, spec 1.2). This file shows how to meet it. It gives the counting rule, the order in which to split a formula, where helpers go in a model built by this plugin, and before-and-after formulas for the deep formulas the research found most often.

Read it when `build` refuses an expression with F-DEPTH or F-NESTED-IF, when `build` refuses `{h:<table>.<column>}` in a helper column ([Rank or percentile within a group](#rank-or-percentile-within-a-group)), when `audit` reports F-DEPTH, F-NESTED-IF, F-LITERAL or F-LENGTH, when `build` or `check` warns D-CRITERIA, when `split` reports SPLIT-GUARD, or before you write a calculation that needs more than two nested functions.

## Evidence labels

- "confirmed": a claim check in the research (`research/verify/`) found the claim in its source; the id names the item ("style survey #4" is item 4 of `style_survey_check_v1.json`, "researcher 2 #7" is item 7 of `researcher2_check_v1.json`, "local guides L-98" is item L-98 of `local_guides_check_v1.json`, "books B-25" is item B-25 of `books_check_v1.json`, "platforms C1.39" is item C1.39 of `platforms_standards_check_v1.json`).
- "not confirmed": a research report states it, but no claim check confirmed it.
- "plugin": what this plugin does; "design n" is section n of the plugin's design (`rotm_design_v1.md`, kept in the project's build folder, not shipped).
- "my reading": this file's own analysis of a formula; check it with the test cases given.
- "Max, spec 1.x": Max's own words, quoted from Part 1 of the job's specification.
- Every formula in the "Before" and "After" tables is measured by `scripts/tests/test_references.py` with the plugin's own counter (`scripts/lib/formula.py`): the depth column must equal the measured depth, and every "After" formula must pass the plugin's formula rules.

## The counting rule

- Depth is the largest number of function calls open at the same time while you read the formula from left to right. It equals the number of functions on the deepest chain of calls nested inside one another (design 8.1).
- Every function call counts, including IF, IFERROR, IFNA, ROUND, VALUE, N, NOT, AND, OR, LET, LAMBDA and calls with no arguments such as `ROW()`. There is no exempt list (design 8.1).
- These do not count: operators (`+ - * / ^ & = <> < > <= >= %`, unary `-` and `--`), grouping parentheses, references, numbers, text, TRUE and FALSE, error literals, array constants, LET variable names, and the `_xlfn.` prefix that Excel stores in the file (design 8.1).
- Calls side by side do not add up: `=IF(A1>0,SUM(B1:B3),MAX(C1:C3))` has depth 2.
- The limit is 2 by default (Max, spec 1.2). It is one setting: `rules.depth_limit` in `style_dictionary.json`, `settings.depth_limit` in a model specification, or `--limit` on `audit` and `formula`.
- To measure one formula, run `python -P -X utf8 -B "<SKILL_DIR>/scripts/rotm.py" formula "=<the formula>"`. It prints the depth, the chain, the length, the typed numbers and the part to move.

| Formula | Depth | At limit 2 |
| --- | --- | --- |
| `=B5` | 0 | pass |
| `=A1+B1*C1` | 0 | pass |
| `=SUM(A1:A9)` | 1 | pass |
| `=ROW()` | 1 | pass |
| `=SUM(A1:A3)+MAX(B1:B3)` | 1 | pass |
| `=IFERROR(VLOOKUP(A1,B:C,2,0),"")` | 2 | pass on depth; VLOOKUP is flagged (F-FUNC-FLAGGED) and the typed 2 is F-LITERAL |
| `=IF(A1>0,SUM(B1:B3),MAX(C1:C3))` | 2 | pass |
| `=IF(A1="a(b",1,0)` | 1 | pass: brackets inside text do not count |
| `=--(A1>0)` | 0 | pass |
| `=LET(x,SUM(A1:A3),x*2)` | 2 | pass on depth; LET is flagged |
| `=IFERROR(INDEX(A:A,MATCH(B1,C:C,0)),"")` | 3 | fail; part to move: `MATCH(B1,C:C,0)` |
| `=ROUND(MAX(MIN(SUM(A1:A9),B1),0),2)` | 4 | fail; part to move: `MIN(SUM(A1:A9),B1)` |

- The part to move is the call at level limit + 1 (level 1 is the outermost call) on the first deepest chain. Moving it leaves the outer formula at depth 2. If the moved part is itself deeper than 2, split it again (design 8.1).
- Nested IF is a separate rule: an IF anywhere inside another IF is F-NESTED-IF (error), even at depth 2, because FAST 3.03-07 says "Never use nested IFs" (platforms C1.39, confirmed).

## How to split a formula, in order

1. Measure it with `rotm.py formula`. Note the chain and the part to move.
2. Move the inner call that the tool names into its own helper (MATCH, YEAR, MONTH, SUM, N are the usual ones). One helper holds one step.
3. Turn each condition into a 0/1 flag in its own helper, and multiply by the flag instead of wrapping the value in IF. FAST 3.03-05: "Use flags to limit use of IF function" (platforms C1.39, confirmed).
4. Replace a chain of nested IFs that picks one of several values with a table typed on the inputs sheet, read with MATCH and INDEX. FAST 3.03-06: "Use INDEX (or even CHOOSE) over IF to pick values" (platforms C1.39, confirmed).
5. Replace an error trap (IFERROR around a lookup) with a position cell, a missing flag and a check that counts the missing flags. PwC: "Always test for the specific error, e.g., ISNA" (local guides L-98, confirmed).
6. Move every typed number other than 0 and 1 into an input cell on the inputs sheet (F-LITERAL; design 8.3).
7. Measure every new formula again. Repeat until every formula has depth 2 or less and no IF inside an IF.
8. Compare the results before and after. Where a pattern below is marked "changes the result", tell the user which cells change and how, and replace the formula only after the user agrees.
9. Add a check for every flag that counts a problem, for example missing keys or values below every band ([checks.md](checks.md)).

- For a workbook the plugin did not build, `rotm.py split WORKBOOK REF` puts the moved part on a sheet `split_helpers` and compares the values after recalculation in Excel (design 15).
  - Two parts share one helper only when their texts are equal with text literals compared exactly; names outside quotes may differ in case. So `EXACT(A2,"x")` and `EXACT(A2,"X")` get two helpers (plugin, v4).
  - A part inside IFERROR or IFNA is not moved into a helper that can show an error the guard hid. For a guarded lookup, INDEX with MATCH inside IFERROR or IFNA, split writes the pattern of H5: helper 1 holds the match position or 0, helper 2 a position INDEX can read, helper 3 holds 1 when the guard would replace the result at that position, and the host returns the guard's value when helper 1 is 0 or helper 3 is 1 (plugin, v5). Any other part under IFERROR or IFNA, a lookup inside IFNA that sits inside IFERROR, and a guarded lookup whose pattern does not fit the depth limit get `SPLIT-GUARD` (an error, exit 1) whatever the current values are: the finding names the guard and the part, and nothing is written. Apply the pattern of this file by hand (plugin, v4 and v5).
  - A part in the second or third argument of IF is not moved into a helper that is calculated for every input. The helper carries the IF's condition, `IF(<condition>,<part>,0)` or `IF(<condition>,0,<part>)`; when it cannot carry the condition within the depth and length limits, split reports `SPLIT-GUARD` and writes nothing. A part in the first argument (the condition) is moved as any other part (plugin, v6; [A part in a branch of IF](#a-part-in-a-branch-of-if)).
  - The new helper cells are compared too: a helper holding an error value that another function of the original formula (such as ISERROR or CHOOSE) kept out of the result is `SPLIT-GUARD` as well, and nothing is written. This finds an error value with the current inputs only.
- For a model the plugin built, change the specification and run `rotm.py build` again (`rotm.py split --spec` is refused in this version). A model's workbook is written only by the plugin's commands.

## Where helpers go in a model built by this plugin

| What the step works on | Where the helper goes | In the specification | What `build` writes |
| --- | --- | --- | --- |
| each raw row | a helper column on the helper sheet `h_<table>` | an entry of the helper sheet's `columns`, with an `expr` that reads same-row cells as `{this:<column>}` | the same formula on every row, reading cells of its own row |
| each period | a line item of an items block on a `calc` sheet, above the item that uses it | an entry of the block's `items`, reading `{col}` (the period header) and `{r:<item>}` (another item, same column) | one formula copied across the period columns |
| each row label | a measure of a measures block | an entry of the block's `measures`, reading `{m:<measure>}` and `{grow:<block>}` | one formula copied down the rows |
| a typed number | an input on the `inputs` sheet | an entry of the inputs sheet's `items` | a value in column D, referenced as `inputs!$D$<n>` |

- The full placeholder grammar is in [model-spec-format.md](model-spec-format.md).
- The helper sheet `h_<table>` starts with three fixed parts: column A `row` (`=ROW()`), column B `in_raw` (1 when raw row r exists), then one fetch column per raw column, `=INDEX(raw_<table>!$<X>:$<X>,A2)`. Your helper columns come after them (design 7.1).
- A fetch column has depth 1. A helper column that reads the fetched cell (`C2`) starts again from depth 0, so it has the whole limit of 2 for its own step (design 7.1).
- Calculation blocks read helper columns as whole columns (`h_sales!$G:$G`), so rows added at the bottom of the helper sheet are included without editing a formula (design 7.1).
- Every helper row reads the raw row of the same number. Helper rows below the raw sheet's last row would read empty raw cells as 0 and feed every block that reads the helper column, so build writes no such rows (a helper capacity above the landed rows is refused), and after a refresh to fewer rows the check `<t>_beyond` fails (`H-BEYOND`) until `rotm.py extend` clears them ([excel-power-query.md](excel-power-query.md#after-a-refresh)).
- In the examples below, the raw table is `sales` with columns `invoice_id`, `line_no`, `inv_date`, `region`, `product`, `amount`, and the helper sheet fetches `inv_date` (column C), `region` (D), `product` (E) and `amount` (F). Your own helper columns start at G. Column letters and input rows are examples: `build` assigns them.

### Empty number and date cells: the null flag

- In Excel, INDEX of an empty cell returns 0, so a fetch column cannot tell an allowed empty value from a real 0. COUNT over the fetch column counts the empty cells too, and AVERAGE divides by them.
- Build adds the null flag `<column>_null` (header `<column> empty`) for each fetched `number`, `integer`, `date` or `datetime` column that may be empty:
  - a column whose null rule is `allowed`;
  - a column whose null rule is `never` and whose contract agrees known nulls for it (`known_nulls`, a known exception: [column-contract.md](column-contract.md)).
- The flag is 1 when the raw cell is empty, else 0. In a specification it is `{h:<table>.<column>_null}`, or `{this:<column>_null}` on the helper sheet. A helper column of your own may not take that id.
- COUNT and AVERAGE over such a column read the flag: `COUNTIFS(<flag>,0)` counts the filled cells and `AVERAGEIFS(<value>,<flag>,0)` averages them. SUM needs no flag: an empty cell adds 0. `lift` computes the flag and gives the same values.
- With agreed known nulls, the check `<t>_nulls_<column>` still counts the empty cells (at most the agreed number), so the exception stays visible; the flag keeps them out of averages and counts.
- An empty date in a helper column: the fetch of a date or datetime column that the contract allows to be empty (null rule allowed, or agreed known nulls) has the number format date_or_empty or datetime_or_empty, which shows nothing for 0, so an empty raw date is not shown as 1900-01-00. The fetched value is still 0. A calculation that must tell an empty date from a date reads the null flag column <column>_null (1 where the raw cell is empty), not the fetched value.

| After | Holds | Formula | Depth |
| --- | --- | --- | --- |
| `h_sales!K2` | null flag of `amount`: 1 when the raw cell is empty | `=--(INDEX(raw_sales!$F:$F,A2)="")` | 1 |
| `calc!D7` | count of the amounts that are present | `=COUNTIFS(h_sales!$K:$K,0)` | 1 |
| `calc!D8` | average of the amounts that are present | `=AVERAGEIFS(h_sales!$F:$F,h_sales!$K:$K,0)` | 1 |

```json
{"items": [
  {"id": "n_amount", "label": "Amounts present", "unit": "count", "expr": "COUNTIFS({h:sales.amount_null},0)"},
  {"id": "avg_amount", "label": "Average amount", "unit": "KRW", "expr": "AVERAGEIFS({h:sales.amount},{h:sales.amount_null},0)"}]}
```

## What the research found

- Formulas of depth 3 or more are rare and sit in a few workbooks. Pass 1 of the style survey: 5,271 of 360,703 formulas (1.5%), and 4,504 of them in 3 workbooks (style survey #1 and #3, confirmed). Pass 2: 6,772 of 354,180 (1.9%), and 5,388 of them in 3 workbooks, two of them Max's own files (style survey #2 and #3, confirmed).
- Researcher 2's sample: 4,893 of 138,647 formulas (3.5%) above depth 2, and 4,882 of them in two workbooks (researcher 2 #7, confirmed).
- The patterns below cover the chains the survey counted most often. The counts per chain carry their own labels.

## H1. A month bucket computed inside SUMIFS (SUMIFS > DATE > YEAR)

- Evidence: the plugin's own pattern for monthly totals from transaction dates (design 6.5 and 7.1). It is not one of the research's counted chains.
- Result: the same result for dates without a time of day. For a datetime on the last day of a month, the "After" version counts it in that month; the "Before" version drops it, because `"<="&D$6` compares with midnight (my reading).

| Before | Holds | Formula | Depth |
| --- | --- | --- | --- |
| `calc!D7` | sales of the region in `$B7` in the month that ends on `D$6` | `=SUMIFS(raw_sales!$F:$F,raw_sales!$D:$D,$B7,raw_sales!$C:$C,">="&DATE(YEAR(D$6),MONTH(D$6),1),raw_sales!$C:$C,"<="&D$6)` | 3 |

| After | Holds | Formula | Depth |
| --- | --- | --- | --- |
| `h_sales!G2` | month end of the invoice date | `=EOMONTH(C2,0)` | 1 |
| `calc!D7` | sales of the region in `$B7` in month `D$6` | `=SUMIFS(h_sales!$F:$F,h_sales!$D:$D,$B7,h_sales!$G:$G,D$6)` | 1 |

```json
{"helper_column": {"id": "month_end", "label": "Month end", "unit": "date", "format": "date", "expr": "EOMONTH({this:inv_date},0)"},
 "grid_cell": "SUMIFS({h:sales.amount},{h:sales.region},{row},{h:sales.month_end},{col})"}
```

- The period headers of a timeline are month ends (`=EOMONTH(<start>,0)`, design 6.5), so they match `month_end` exactly.

## H2. A flag for a date window (IF > IF > YEAR)

- Evidence: 3,850 formulas in 1 workbook, pass 1 of the style survey; the "Before" cell is `해외 LNG 사업성 검토.xlsx`, sheet Input, cell I25 (style survey #4, confirmed).
- Result: the same result (my reading: the "Before" returns the amount when the period year lies between the two years, else 0; so does the "After").
- Inputs: the operation start date `op_start` (inputs!D8), the operation end date `op_end` (inputs!D9) and the amount `annual_amount` (inputs!D10). Row 6 holds the period ends.

| Before | Holds | Formula | Depth |
| --- | --- | --- | --- |
| `Input!I25` | amount in operating years | `=IF(YEAR(I2)>YEAR(Control!$H$29),0,IF(YEAR(I2)<YEAR(Control!$H$26),0,$G$25))` | 3 |

| After | Holds | Formula | Depth |
| --- | --- | --- | --- |
| `calc!D12` | period year | `=YEAR(D$6)` | 1 |
| `calc!D13` | operating flag: 1 in an operating year, else 0 | `=(D$12>=YEAR(inputs!$D$8))*(D$12<=YEAR(inputs!$D$9))` | 1 |
| `calc!D14` | amount in operating years | `=inputs!$D$10*D$13` | 0 |

```json
{"id": "window", "title": "Operating window", "cols": {"timeline": {"start": "start_month", "periods": 24, "step": "month"}},
 "items": [
  {"id": "pyear", "label": "Period year", "unit": "year", "expr": "YEAR({col})"},
  {"id": "op_flag", "label": "Operating flag", "unit": "flag", "expr": "({r:pyear}>=YEAR({in:op_start}))*({r:pyear}<=YEAR({in:op_end}))"},
  {"id": "op_amount", "label": "Amount in operating years", "unit": "KRW", "expr": "{in:annual_amount}*{r:op_flag}"}]}
```

- Sources: flags instead of IF (FAST 3.03-05, platforms C1.39, confirmed); timing flags calculated once (FAST 2.02-06, platforms C1.23, not confirmed).

## H3. Days of operation in a period (IF > IF > IF > IF > YEAR)

- Evidence: 50 formulas of depth 5 in the same workbook as H2 (researcher 2 #7, confirmed). The "Before" text is sheet Operation, cell I8, as the research synthesis read it (not confirmed by a claim check).
- Result: changes the result in one case (my reading). When the operation starts and ends inside the same period, the "Before" counts the days from the start to the period end and ignores the end date; the "After" counts the days between the two dates. In every other case the two agree. Test periods where a date falls exactly on a period start or end.
- Inputs as in H2. Row 6 holds the period ends.

| Before | Holds | Formula | Depth |
| --- | --- | --- | --- |
| `Operation!I8` | operating days in the period | `=IF(YEAR(FS!I2)<YEAR(Control!$H$26), 0, IF(YEAR(FS!I2)>YEAR(Control!$H$29), 0, IF(YEAR(FS!I2)=YEAR(Control!$H$26), FS!I2-Control!$H$26+1, IF(YEAR(FS!I2)=YEAR(Control!$H$29), Control!$H$29-FS!H2, I6))))` | 5 |

| After | Holds | Formula | Depth |
| --- | --- | --- | --- |
| `calc!D12` | period start | `=EOMONTH(D$6,-1)+1` | 1 |
| `calc!D13` | overlap start | `=MAX(D$12,inputs!$D$8)` | 1 |
| `calc!D14` | overlap end | `=MIN(D$6,inputs!$D$9)` | 1 |
| `calc!D15` | operating days in the period | `=MAX(0,D$14-D$13+1)` | 1 |

```json
{"id": "op_days", "title": "Operating days", "cols": {"same_as": "window"},
 "items": [
  {"id": "pstart", "label": "Period start", "unit": "date", "expr": "EOMONTH({col},-1)+1"},
  {"id": "ostart", "label": "Overlap start", "unit": "date", "expr": "MAX({r:pstart},{in:op_start})"},
  {"id": "oend", "label": "Overlap end", "unit": "date", "expr": "MIN({col},{in:op_end})"},
  {"id": "days", "label": "Operating days", "unit": "days", "expr": "MAX(0,{r:oend}-{r:ostart}+1)"}]}
```

- Sources: MIN and MAX instead of IF for bands (books B-22, not confirmed); "Never use nested IFs" (FAST 3.03-07, platforms C1.39, confirmed).

## H4. A month flag (IF > OR > MONTH)

- Evidence: 884 formulas in 4 workbooks, pass 1 (style survey #4, confirmed). The "Before" cell is `예제_재무모델_3+9 Forecast.xlsm`, sheet Input, cell H18 (researcher 2 #7, confirmed).
- Result: changes the type of the result. TRUE and FALSE become 1 and 0. A formula that multiplies by the flag gives the same value; a formula that compares the flag with TRUE does not. Find the cells that read the flag and change any comparison with TRUE before you replace it.
- Inputs: the payment months 6 and 12 typed once, `pay_m1` (inputs!D11) and `pay_m2` (inputs!D12). The "Before" types them into the formula, which is F-LITERAL.

| Before | Holds | Formula | Depth |
| --- | --- | --- | --- |
| `Input!H18` | TRUE in a payment month | `=IF(OR(MONTH(H4)=6,MONTH(H4)=12),TRUE,FALSE)` | 3 |

| After | Holds | Formula | Depth |
| --- | --- | --- | --- |
| `calc!D12` | month number | `=MONTH(D$6)` | 1 |
| `calc!D13` | payment-month flag: 1 or 0 | `=--OR(D$12=inputs!$D$11,D$12=inputs!$D$12)` | 1 |

```json
{"id": "pay", "title": "Payment months", "cols": {"same_as": "window"},
 "items": [
  {"id": "mnum", "label": "Month number", "unit": "month", "expr": "MONTH({col})"},
  {"id": "pay_flag", "label": "Payment-month flag", "unit": "flag", "expr": "--OR({r:mnum}={in:pay_m1},{r:mnum}={in:pay_m2})"}]}
```

- Sources: flags (FAST 3.03-05, platforms C1.39, confirmed); return 1 and 0, not TRUE (books B-24, not confirmed); no typed numbers in formulas (FAST 3.04-01, platforms C1.43, not confirmed).

## H5. A lookup with a not-found fallback (IFERROR > INDEX > MATCH)

- Evidence: IFERROR > INDEX > MATCH in 465 formulas in 6 workbooks, and IFERROR > IF > INDEX > MATCH in 2,933 formulas in 1 workbook, pass 2 (style survey #5, confirmed). The second form is in Max's file `hyosung_corpplan_financials_workbook_20260917_v1.xlsx`, sheet 채택_재무제표, cell D12 (style survey #5, confirmed).
- Result: the same for keys that are found, and the same empty text for a missing key; the "After" also counts the missing keys, so with the check below the master check fails until the mapping is complete. It changes the result where the "Before" hid another error: a `#REF!` or `#VALUE!` now shows instead of an empty text.
- Inputs: two lists of the same length, the product codes `map_code` (inputs!D20:D40) and their groups `map_group` (inputs!D43:D63). `E2` is the fetched product code.

| Before | Holds | Formula | Depth |
| --- | --- | --- | --- |
| `h_sales!G2` | product group, empty when the code is missing | `=IFERROR(INDEX(inputs!$D$43:$D$63,MATCH(E2,inputs!$D$20:$D$40,0)),"")` | 3 |
| `h_sales!G2` | the same with VLOOKUP | `=IFERROR(VLOOKUP(E2,inputs!$B$20:$C$40,2,0),"")` | 2 |

The VLOOKUP form passes the depth limit, but VLOOKUP is flagged (F-FUNC-FLAGGED: "VLOOKUP and HLOOKUP should generally be avoided", PFM p. 134, books B-25, confirmed) and the typed column number 2 is F-LITERAL. Rewrite it the same way.

| After | Holds | Formula | Depth |
| --- | --- | --- | --- |
| `h_sales!G2` | position of the code in the mapping; 0 when it is missing | `=IFNA(MATCH(E2,inputs!$D$20:$D$40,0),0)` | 2 |
| `h_sales!H2` | unmapped flag: 1 for a raw row whose code is missing | `=B2*(G2=0)` | 0 |
| `h_sales!I2` | product group | `=IF(G2=0,"",INDEX(inputs!$D$43:$D$63,G2))` | 2 |

```json
{"helper_columns": [
  {"id": "map_pos", "label": "Mapping position", "unit": "position", "format": "integer", "expr": "IFNA(MATCH({this:product},{in:map_code},0),0)"},
  {"id": "unmapped", "label": "Unmapped flag", "unit": "flag", "format": "flag", "expr": "{this:in_raw}*({this:map_pos}=0)"},
  {"id": "group", "label": "Product group", "type": "text", "expr": "IF({this:map_pos}=0,\"\",INDEX({in:map_group},{this:map_pos}))"}],
 "check": {"id": "sales_unmapped", "kind": "zero", "label": "Sales lines whose product code is not in the mapping", "actual": "SUM({h:sales.unmapped})"}}
```

- `B2` is `in_raw`. Multiplying by it keeps the empty helper rows below the last raw row out of the count.
- `rotm.py split` writes this pattern for a lookup inside IFERROR or IFNA, without the unmapped flag and with the guard kept on the INDEX part too (plugin, v5). From `<G>(INDEX(<range>,MATCH(<key>,<keys>,0)),<value>)`, where `<G>` is IFERROR or IFNA and `<T>` is ISERROR or ISNA, it writes three helpers on the sheet `split_helpers` and replaces the guard call:

| Split | Holds | Formula | Depth |
| --- | --- | --- | --- |
| helper 1 | position of the key; 0 when it is missing | `=<G>(MATCH(<key>,<keys>,0),0)` | 2 |
| helper 2 | a position INDEX can read: helper 1, or 1 when helper 1 is 0 | `=MAX(<helper 1>,1)` | 1 |
| helper 3 | 1 when the guard would replace the result at that position (the return cell holds an error, or the position is outside the range), else 0 | `=--<T>(INDEX(<range>,<helper 2>))` | 2 |
| the guard call's place | the guard's value when the key is missing or helper 3 is 1, else the result | `IF(OR(<helper 1>=0,<helper 3>=1),<value>,INDEX(<range>,<helper 2>))` | 2 |

- Helper 1 is `IFNA(...)` only when every IFERROR or IFNA around the part is IFNA; else it is `IFERROR(...)`. With these, the host gives the original's result for the key present, the key missing, a matched return cell that holds an error after the split, and a match position outside the INDEX range, with IFERROR and with IFNA, and no helper cell holds an error value that the guard hid (test `test_v5_tools.py::test_guarded_lookup_split_keeps_the_result_of_the_original`, Excel; the key present and missing also `test_v4_split.py::test_guarded_lookup_split_keeps_the_missing_key_result`). With IFNA, an error of the key itself (such as `#DIV/0!`) shows in helpers 1 and 2 and in the host, as it shows in the original. The guard's value is written once, in the IF, as in v4. When the IF is too deep where the guard sat, it goes into a helper of its own.
- A lookup inside IFNA that sits inside IFERROR gets `SPLIT-GUARD` and nothing is written: a helper that keeps IFNA's result would show the error values that the IFERROR hid. Any other part under IFERROR or IFNA gets `SPLIT-GUARD` and nothing is written; apply this pattern by hand.
- A key with several parts: build the key in its own helper column first, for example `=D2&"_"&E2` (depth 0), type the mapping's keys the same way on the inputs sheet, and match on the key column.
- Excel stores IFNA in the file as `_xlfn.IFNA`; `build` writes the prefix, and Excel 16.0 on the build PC recalculated a prefixed IFNA without `#NAME?` (test `test_file_prefixes_recalculate_in_excel` in `scripts/tests/test_formula.py`, run on 2026-11-25).
- Another way, not used by `build`: `XLOOKUP(E2,<codes>,<groups>,"")` has depth 1. XLOOKUP needs Excel 2021 or Microsoft 365 ([functions.md](functions.md), not confirmed). It hides missing keys unless you count them, so keep the unmapped flag and its check with it.
- Sources: test for the specific error (local guides L-98, confirmed); use IFERROR sparingly (books B-23, not confirmed).

## H6. A chain of nested IFs that picks a rate (IF > IF > IF > AND)

- Evidence: IF > IF > IF > IF > AND in 77 formulas in 10 workbooks, and IF > IF > IF > AND in 42 formulas in 7 workbooks, pass 1 (style survey section D, not confirmed). The survey counts chains only; the "Before" is a representative form written for this file.
- Result: the same result for every amount with the bounds and rates below, including amounts below 0, which get 0 in both (my reading).
- Inputs: the band table typed once, lower bounds `band_low` = 0, 1000, 5000, 10000 in ascending order (inputs!D12:D15) and rates `band_rate` = 0.01, 0.02, 0.03, 0.04 (inputs!D18:D21). `F2` is the fetched amount. MATCH with match type 1 needs the bounds in ascending order (general product behaviour, not confirmed by the claim checks).

| Before | Holds | Formula | Depth |
| --- | --- | --- | --- |
| `h_sales!G2` | rate for the amount's band | `=IF(AND(F2>=0,F2<1000),0.01,IF(AND(F2>=1000,F2<5000),0.02,IF(AND(F2>=5000,F2<10000),0.03,IF(F2>=10000,0.04,0))))` | 4 |

| After | Holds | Formula | Depth |
| --- | --- | --- | --- |
| `h_sales!G2` | band position; 0 below the first bound | `=IFNA(MATCH(F2,inputs!$D$12:$D$15,1),0)` | 2 |
| `h_sales!H2` | below-bands flag | `=B2*(G2=0)` | 0 |
| `h_sales!I2` | rate | `=IF(G2=0,0,INDEX(inputs!$D$18:$D$21,G2))` | 2 |

```json
{"helper_columns": [
  {"id": "band_pos", "label": "Band position", "unit": "position", "format": "integer", "expr": "IFNA(MATCH({this:amount},{in:band_low},1),0)"},
  {"id": "below_bands", "label": "Below-bands flag", "unit": "flag", "format": "flag", "expr": "{this:in_raw}*({this:band_pos}=0)"},
  {"id": "rate", "label": "Rate", "unit": "rate", "format": "rate", "expr": "IF({this:band_pos}=0,0,INDEX({in:band_rate},{this:band_pos}))"}]}
```

- If an amount below every band is an error in the business, add a `zero` check on `SUM({h:sales.below_bands})`.
- Sources: INDEX instead of IF to pick values and "Never use nested IFs" (FAST 3.03-06 and 3.03-07, platforms C1.39, confirmed); lookup tables and MIN or MAX instead of IF chains (books B-22, not confirmed).

## H7. A cap and a floor (IF > MIN > SUM)

- Evidence: 311 formulas in 6 workbooks, pass 1, and 270 in 10 workbooks, pass 2 (style survey sections D and pass 2, not confirmed). The "Before" cell is `예제_재무모델_3+9 Forecast.xlsm`, sheet 'Dep & Tax', cell J66 (researcher 2 #7, confirmed).
- Result: the same in every case. Only the SUM moves out.
- The shorter form `=-MIN(J66,MAX(0,J57))` (depth 2) is not the same: when J57 is below 0 and the available amount J66 is also below 0, the original gives 0 and the shorter form gives J66 negated (my reading). Do not use it as a replacement.

| Before | Holds | Formula | Depth |
| --- | --- | --- | --- |
| `'Dep & Tax'!J66` | amount used, as a negative number | `=-IF(J57<0,0,MIN(SUM(J64:J65),J57))` | 3 |

| After | Holds | Formula | Depth |
| --- | --- | --- | --- |
| `J66` | available amount (a new row above the result) | `=SUM(J64:J65)` | 1 |
| `J67` | amount used, as a negative number | `=-IF(J57<0,0,MIN(J66,J57))` | 2 |

```json
{"items": [
  {"id": "avail", "label": "Available amount", "unit": "KRW", "expr": "{r:loss_a}+{r:loss_b}"},
  {"id": "used", "label": "Amount used", "unit": "KRW", "expr": "-IF({r:base}<0,0,MIN({r:avail},{r:base}))"}]}
```

- In a model built by the plugin, the new row is a line item above the item that uses it; `loss_a`, `loss_b` and `base` stand for the items that hold J64, J65 and J57.
- Sources: MIN and MAX for caps and floors (books B-22, not confirmed).

## H8. The same input converted many times (IF > IF > MIN > N)

- Evidence: IF > IF > N in 300 formulas in 1 workbook, Max's own file `20261022_analyze_cnc_utilization_failure_nodes_model_v1.xlsx`, pass 2 (style survey #5, confirmed). The "Before" text is sheet Step0_이월, cell G40, as the research synthesis read it (not confirmed by a claim check).
- Result: the same while the base, share and amount hold numbers or are empty (my reading). In a model built by the plugin they are raw columns typed `number` in the contract, and `land` refuses a CSV whose number column holds text (design 15), so the formula needs no N().
- Here the raw table is `usage`, and its helper sheet fetches `base` (C), `share` (D) and `amount` (E). In Excel, INDEX of an empty cell returns 0 (observed by the research synthesis in this PC's Excel 16.0, not confirmed by a claim check); what Google Sheets returns is not confirmed ([google-sheets.md](google-sheets.md)).

| Before | Holds | Formula | Depth |
| --- | --- | --- | --- |
| `Step0_이월!G40` | amount times the capped share | `=IF(A40="",0,IF(N(입력!J157)=0,0,N(입력!L157)*MIN(1,N(입력!K157)/N(입력!J157))))` | 4 |

| After | Holds | Formula | Depth |
| --- | --- | --- | --- |
| `h_usage!F2` | amount times the capped share | `=IF(C2=0,0,B2*E2*MIN(1,D2/C2))` | 2 |

```json
{"helper_column": {"id": "capped", "label": "Amount times capped share", "unit": "KRW", "format": "amount",
  "expr": "IF({this:base}=0,0,{this:in_raw}*{this:amount}*MIN(1,{this:share}/{this:base}))"}}
```

- `B2` (`in_raw`) replaces the test `A40=""`: it is 1 when the raw row exists.
- If a column can hold text, type it `text` in the contract and convert it once, in its own helper column (`=N(C2)`), then read that helper.
- Sources: convert once in a helper cell (style survey section F, a proposal, not confirmed); calculate once (FAST 1.01-07, platforms C1.10, not confirmed).

## H9. A text status with rounding (IF > IF > ROUND > MAX)

- Evidence: 347 formulas in 1 workbook, pass 2 (style survey pass 2, not confirmed). The "Before" text is Max's file `hyosung_corpplan_financials_workbook_20260917_v1.xlsx`, sheet 빈티지_매트릭스, cell M6, as the research synthesis read it (not confirmed by a claim check).
- Result: changes the result. One text cell with three states ("", "동일" same, "차이" different) becomes two 0/1 flags: comparable 0 means fewer than two values; comparable 1 and differs 0 means the values are the same; differs 1 means they differ. ROUND to 6 places becomes a comparison with a tolerance input `tol_same` (inputs!D14). Tell the user before you replace it.

| Before | Holds | Formula | Depth |
| --- | --- | --- | --- |
| `빈티지_매트릭스!M6` | whether the values in E6:J6 are the same | `=IF(COUNT(E6:J6)<=1,"",IF(ROUND(MAX(E6:J6)-MIN(E6:J6),6)=0,"동일","차이"))` | 4 |

| After | Holds | Formula | Depth |
| --- | --- | --- | --- |
| `K6` | values present | `=COUNT(E6:J6)` | 1 |
| `L6` | spread | `=MAX(E6:J6)-MIN(E6:J6)` | 1 |
| `M6` | comparable flag: 1 when two or more values | `=--(K6>1)` | 0 |
| `N6` | differs flag: 1 when the values differ | `=M6*(L6>inputs!$D$14)` | 0 |

```json
{"id": "vint_cmp", "title": "Vintage comparison", "rows": {"same_as": "vint"},
 "measures": [
  {"id": "n", "label": "Values present", "format": "count", "expr": "COUNT({grow:vint})"},
  {"id": "spread", "label": "Spread", "format": "amount", "expr": "MAX({grow:vint})-MIN({grow:vint})"},
  {"id": "comparable", "label": "Comparable flag", "format": "flag", "expr": "--({m:n}>1)"},
  {"id": "differs", "label": "Differs flag", "format": "flag", "expr": "{m:comparable}*({m:spread}>{in:tol_same})"}]}
```

- Sources: ROUND in checks may hide mistakes (local guides L-98, confirmed); compare with ABS or a difference against a named tolerance (local guides L-39, confirmed); results as 1 and 0 (books B-24, not confirmed).

## H10. A long chain of additions (depth 0, too long)

- Evidence: the longest formula in researcher 2's sample is 397 characters at depth 0, a chain of additions that holds 66 `#REF!` errors (researcher 2 #8, confirmed). Long additions pass the depth limit and fail the length limit (F-LENGTH, warning, over 100 characters).
- Result: the same result.

| Before | Holds | Formula | Depth |
| --- | --- | --- | --- |
| `Summary!B6` | total of eight plants | `='Plant A'!B6+'Plant B'!B6+'Plant C'!B6+'Plant D'!B6+'Plant E'!B6+'Plant F'!B6+'Plant G'!B6+'Plant H'!B6` | 0 |

| After | Holds | Formula | Depth |
| --- | --- | --- | --- |
| `Summary!B10` | Plant A, linked | `='Plant A'!B6` | 0 |
| `Summary!B11` | Plant B, linked | `='Plant B'!B6` | 0 |
| `Summary!B18` | total of rows 10 to 17, one linked row per plant | `=SUM(B10:B17)` | 1 |

- One link row per term, each labelled with its source, then one SUM. Rows 12 to 17 link plants C to H the same way.
- A `#REF!` inside the formula is F-REF-ERROR (error). Repair each broken reference first: a term that points nowhere cannot be moved.
- When the terms are rows of landed data, do not link them one by one: land the data as a raw table and use one SUMIFS over the helper or raw column.
- What `rotm.py split` does with this shape: when the over-long formula is a chain of `+` and `-` terms at its top level (no `&`, no comparison and no pair of parentheses around the whole formula), split moves the terms, in order, into helper cells on the sheet `split_helpers`. Each helper adds a few terms and stays within the length limit (100 characters, or the model's `settings.length_limit`); the cell then adds the helpers. A helper whose first term follows a minus holds its terms with the signs turned round, and the cell subtracts it, so every sign is kept. Both files are recalculated in Excel and every value must stay equal.
- Measured on the build PC (Excel 16, 2026-11-25): `Calc!B1 =A1+A2+...+A40` (151 characters) became `=split_helpers!B2+split_helpers!C2+split_helpers!D2+split_helpers!E2` (68 characters) and 4 helpers of 12, 11, 11 and 6 terms (99, 99, 99 and 54 characters); the value was unchanged after recalculation in Excel (test `test_excel_40_term_sum_is_split_with_its_value_unchanged`).
- Any other over-long formula (one function call with many arguments such as `=SUM(A1,A2,...,A40)`, a chain of `*` or `/`, `&` or a comparison at the top level, or one pair of parentheses around the whole formula): split writes nothing and exits 1 with the error `SPLIT-SHAPE`, which names the shape. Apply the pattern of this section by hand.
- Sources: ingredients as links above the calculation (FAST 2.02-01, platforms C1.20, not confirmed); length figures (researcher 2 #8, confirmed).

## A part in a branch of IF

- Evidence: plugin, v6. In round 5 a reviewer split `=IF(A1=0,0,ABS(SQRT(1/A1)))` with A1 = 1. After A1 was set to 0, the helper held `#DIV/0!` while the cell showed 0. Excel calculates the second argument of IF only where the condition is TRUE and the third argument only where it is FALSE; a helper cell is calculated for every input.
- Result: the same result in both branches, and no helper cell holds an error value that the IF kept out of the result. Measured in Excel 16 on the build PC with A1 = 1, 0 and 4 (test `test_v6_split.py::test_excel_branch_split_keeps_the_result_in_both_branches`).

| Before | Holds | Formula | Depth |
| --- | --- | --- | --- |
| `Model!B1` | 0 when A1 is 0, else the absolute value of the square root of 1/A1 | `=IF(A1=0,0,ABS(SQRT(1/A1)))` | 3 |

| After | Holds | Formula | Depth |
| --- | --- | --- | --- |
| `split_helpers!B2` | the square root of 1/A1 where the IF takes its third argument (A1 is not 0), else 0 | `=IF(Model!A1=0,0,SQRT(1/Model!A1))` | 2 |
| `Model!B1` | the same result as before | `=IF(A1=0,0,ABS(split_helpers!B2))` | 2 |

- The helper carries the IF's condition: `IF(<condition>,<part>,0)` for a part from the second argument, and `IF(<condition>,0,<part>)` for a part from the third. A part whose function always gives text (such as TEXT, LEFT, TRIM or UPPER) gets empty text `""` in place of 0. The cell reads the helper only in the branch the part came from, so the 0 (or the empty text) never reaches its result.
- `rotm.py split` writes this pattern (plugin, v6). The condition is written as it is after the split: a part of the condition that is over the limit goes to a helper of its own, and the condition reads that helper. A helper that is over the depth limit is split again, and its helpers carry the same condition.
- A part in the first argument of IF (the condition) is calculated whenever the IF is. `rotm.py split` moves it as any other part.
- `rotm.py split` writes nothing and reports `SPLIT-GUARD` (an error, exit 1) when the helper cannot carry the condition within the limits: the depth limit is 1 (IF and the part need depth 2), the part lies in a branch of two or more IFs, the part is the INDEX or MATCH call of a guarded lookup (H5, whose helpers take no condition), or a helper that carries the condition, or the cell itself, would be over the length limit. The finding names the cell, the part, the argument and the IF. Apply the pattern by hand.
- By hand, for a part in a branch of two IFs: put both conditions into one flag helper that tests the inner condition only where the outer IF takes its branch, and let the part's helper and the cell read the flag. Result: the same result in every case, also when B1 holds an error value (my reading).

| Before | Holds | Formula | Depth |
| --- | --- | --- | --- |
| `Model!B1` | 0 when A1 is 0; else, when B1 is above 0, the absolute value of the square root of 1/A1; else 0 | `=IF(A1=0,0,IF(B1>0,ABS(SQRT(1/A1)),0))` | 4 |

| After | Holds | Formula | Depth |
| --- | --- | --- | --- |
| `split_helpers!B2` | flag: 1 where A1 is not 0 and B1 is above 0, else 0 (B1 is tested only where A1 is not 0) | `=IF(Model!A1=0,0,--(Model!B1>0))` | 1 |
| `split_helpers!C2` | the square root of 1/A1 where the flag is 1, else 0 | `=IF(B2=1,SQRT(1/Model!A1),0)` | 2 |
| `Model!B1` | the same result as before | `=IF(split_helpers!B2=1,ABS(split_helpers!C2),0)` | 2 |

- The "After" cell holds no IF inside an IF, so it also passes F-NESTED-IF.

## Rank or percentile within a group

- Evidence: plugin, v7 (round 6 judge item J-1). In a user test the model needed the rank of each line within its group, and the agent wrote its own pattern with two extra typed numbers and three extra checks. This section gives one pattern that `build` accepts within the depth limit of 2 and without typed numbers other than 0 and 1, and one check that proves it.
- Result: each line gets its rank within its region (largest amount first), its rank from the other end (smallest first), the count of lines in its region, and its rank as a share of the region. Measured in Excel 16 on the build PC on a fixture of 21 lines in 4 groups with ties and decimals: every value equals the same calculation in Python (test `test_v7_patterns.py::test_rank_pattern_equals_python_in_excel`).

### Why a helper column may not read other rows of its own sheet

- The rule: `{h:<table>.<column>}` is allowed in calc, output and checks only, and a helper column reads its own helper sheet only as `{this:<column>}`, the cell of its own row (design 6.6, the placeholder table; `ALLOWED_IN` in `scripts/lib/spec.py`). Row r of a helper sheet belongs to raw row r, and every helper formula is the same on every row (design 7.1).
- `build` refuses `{h:...}` in a helper column (exit 2, nothing built); its fix names this section.
- The reason (my reading): `{h:<table>.<column>}` becomes the whole helper column with its sheet name, for example `h_sales!$G:$G`. On the helper sheet itself that is a reference to its own sheet (F-SELF-SHEET; design 6.6: the current sheet is never written as a prefix), and a helper column that reads its own column, or a column that reads it, refers to itself: a circular reference.
- The raw sheet holds the same rows: row r of `h_<table>` reads raw row r. So the pattern reads the other rows of the group from the raw columns, `{raw:<table>.<column>}`, which `build` allows in a helper column, and the row's own values from `{this:<column>}`.

### The pattern

- In the example below the raw table is `sales`; the helper sheet fetches `region` (column D) and `amount` (column F), as in the other examples of this file. Column letters are examples: `build` assigns them.
- The refused form reads the helper sheet itself: `{h:sales.region}` and `{h:sales.amount}` in a helper column of `h_sales`.

| After | Holds | Formula | Depth |
| --- | --- | --- | --- |
| `h_sales!G2` | rank of the amount in its region, largest first: 1 plus the lines of the same region with a larger amount | `=COUNTIFS(raw_sales!$D:$D,D2,raw_sales!$F:$F,">"&F2)+1` | 1 |
| `h_sales!H2` | rank of the amount in its region, smallest first: 1 plus the lines of the same region with a smaller amount | `=COUNTIFS(raw_sales!$D:$D,D2,raw_sales!$F:$F,"<"&F2)+1` | 1 |
| `h_sales!I2` | lines in the region | `=COUNTIFS(raw_sales!$D:$D,D2)` | 1 |
| `h_sales!J2` | rank as a share of the region: 1/n for the largest amount of a region of n lines, 1 for the smallest when it is not tied | `=G2/I2` | 0 |

```json
{"helper_columns": [
  {"id": "amount_rank", "label": "Rank in region, largest first", "unit": "rank", "format": "integer", "expr": "COUNTIFS({raw:sales.region},{this:region},{raw:sales.amount},\">\"&{this:amount})+1"},
  {"id": "amount_rank_up", "label": "Rank in region, smallest first", "unit": "rank", "format": "integer", "expr": "COUNTIFS({raw:sales.region},{this:region},{raw:sales.amount},\"<\"&{this:amount})+1"},
  {"id": "region_n", "label": "Lines in region", "unit": "count", "format": "count", "expr": "COUNTIFS({raw:sales.region},{this:region})"},
  {"id": "amount_share", "label": "Rank as share of region", "unit": "share", "format": "rate", "expr": "{this:amount_rank}/{this:region_n}"}],
 "check": {"id": "sales_rank_sums", "kind": "equal", "label": "Ranks in region: largest first and smallest first add up to the same total", "actual": "SUM({h:sales.amount_rank})", "expected": "SUM({h:sales.amount_rank_up})"}}
```

- The check goes into the `checks` list of the checks sheet; the four helper columns go into the `columns` of the helper sheet `h_sales`.
- A percentile in another form uses the same columns, for example the share of the region below the line, `({this:amount_rank_up}-1)/{this:region_n}`.
- Calculation blocks read the columns as any helper column, for example the lines that rank first in their region: `COUNTIFS({h:sales.amount_rank},1)`.

### How ties are ranked

- Equal amounts in one region get the same rank, the best of their places, and the next rank is skipped. Amounts 500, 300, 300 and 100 in one region rank 1, 2, 2 and 4 (largest first) and 4, 2, 2 and 1 (smallest first). RANK.EQ ranks ties the same way over a whole range (builder's reading of Microsoft's function reference, not confirmed).
- Tied lines get the same share: 0.25, 0.5, 0.5 and 1 in the example.

### The check that proves it

- In each region, the sum of (rank - 1) over its lines counts the pairs of lines whose amounts differ, once from the line with the smaller amount; the sum of (rank from the other end - 1) counts the same pairs from the line with the larger amount. Tied pairs add 0 to both. So the two rank columns have the same sum, in each region and over the table. In the example: 1 + 2 + 2 + 4 = 9 and 4 + 2 + 2 + 1 = 9.
- The check `sales_rank_sums` fails when one of the two rank columns counts lines of other regions (the region criterion left out), counts the line itself or its ties (`">="` in place of `">"`), or lacks the `+1`. Tested in Excel for the first case: the fixture with the region criterion left out of `amount_rank` fails `check --strict` (test `test_v7_patterns.py::test_rank_check_fails_for_a_rank_without_its_group`). The other two cases follow from the two sums (my reading).
- It does not find a mistake that both rank columns share, and it does not find labels that Excel's criteria matching merges ([Labels that Excel's criteria matching merges](#labels-that-excels-criteria-matching-merges)): both columns then count the same wrong rows.
- It has no typed number other than 0 and 1, and its tolerance is the input `check_tolerance`; both sums are whole numbers.

### Order functions in the catalogue

What `scripts/lib/functions.py` gives for the functions that rank or order values, in the profiles `both`, `excel` and `sheets` alike:

| Functions | Class | What `build` and `audit` do |
| --- | --- | --- |
| COUNTIFS, COUNTIF, SUMPRODUCT, LARGE, SMALL, MEDIAN | allowed | accept them; none of them ranks within a group by itself: COUNTIFS does it with a criterion per column, as above |
| MAXIFS, MINIFS | allowed | accept them; the catalogue gives Excel 2019 and later (stored with the prefix `_xlfn.`) |
| SORT, SORTBY | flagged | F-FUNC-FLAGGED (warning; strict mode exits 1): spilling arrays |
| RANK, RANK.EQ, RANK.AVG, PERCENTRANK, PERCENTRANK.INC, PERCENTRANK.EXC, PERCENTILE, PERCENTILE.INC, PERCENTILE.EXC, QUARTILE, QUARTILE.INC, QUARTILE.EXC | not in the catalogue | F-FUNC-UNKNOWN (warning; strict mode exits 1); each works on one whole range and takes no group criterion |

### What `">"&` does to a number

- In a criterion, `">"&F2` joins the text `>` with the number in F2, so Excel turns the number into text first. Excel writes at most 15 significant digits there and compares to that precision: two amounts that agree in their first 15 significant digits rank as ties, also when they differ after it.
- Measured in Excel 16 on the build PC (2026-11-26; test `test_v7_patterns.py::test_rank_criteria_compare_numbers_to_15_significant_digits`): `">"&(0.1+0.2)` gave the text `>0.3`, and 0.30000000000000004 and 0.3 in one group both got rank 1 from each end (Python's exact comparison gives 1 and 2). The same held for 1/3 and 0.333333333333333, and for 1234567890123456 and 1234567890123457 (the criterion was `>1234567890123460`). Amounts of 15 significant digits or fewer ranked exactly: 1234.5678 (twice), 99.125, -0.5, 0 and 2.75 got Python's ranks.
- Landed amounts with 15 significant digits or fewer (whole won, or two decimals below 10,000,000,000,000) are not affected. Rank a value computed in a helper column, such as a ratio, only when ties at 15 significant digits are acceptable.

## Labels that Excel's criteria matching merges

- Evidence: plugin, v4. Excel's COUNTIFS counted the text keys 00123, 0123 and 123 as one key (measured in Excel 16, 2026-11-25; [checks.md](checks.md)). The plugin's rules below are compared with Excel's COUNTIFS by the test `test_v4_check.py::test_criteria_match_follows_excel_countifs` (Excel).
- SUMIFS, COUNTIFS, AVERAGEIFS, MAXIFS, MINIFS, SUMIF, COUNTIF and AVERAGEIF do not compare their criterion with each cell of the criteria range as exact text. Four rules:
  1. A criterion that reads as a number matches a number cell of that value and every text that reads as the same number: `'001'`, `'1'` and `'1.0'` match one another.
  2. Upper and lower case are not told apart: `'North'` matches `'north'`. The comparison ignores case.
  3. `*` matches any run of characters and `?` any one character; `~` before either makes it a plain character. A label `A*` matches `AB` and `A-1`. In a criterion that holds `*` or `?`, a `~` makes the next character plain whatever it is: `~~` is a plain `~`, and the `~` of `A~b*` is dropped, so `A~b*` matches `Abc` and not `A~bc`. In a criterion without `*` or `?`, a `~` is an ordinary character: `A~b` matches `A~b` (plugin, v9: measured in Excel 16 on the build PC, 2026-11-26).
  4. A criterion that starts with `=`, `<` or `>` is read as a comparison: a label `<5` counts the numbers below 5.
- So a block that sums by label adds the rows of every value that Excel takes as equal to the label. Example: a text column `category` holds `'001'` on a row with amount +100 and `'1'` on a row with amount -100 in the same month. `SUMIFS(<amounts>,<categories>,"001")` and `SUMIFS(<amounts>,<categories>,"1")` both return 0: Excel adds both rows under each label.
- `build` and `check` report it with the warning D-CRITERIA (strict mode exits 1). The warning names the block or the cell, the label, the other value, the column and its first row, and the reason (both read as the same number; they differ only in case; the label holds a wildcard; the label starts with an operator). `build` and `check` test the landed rows in Python, for criteria ranges that are fetch columns of a raw text column. `check` also tests the values Excel saved after a completed recalculation, for criteria ranges on other helper columns.
- The fix: make the labels unambiguous before they reach the criteria range. Change the contract's source expression of the column (for example keep a code at a fixed length with its leading zeros, so that `1` and `001` become one code, or put a letter in front of it), or match on a helper column that holds an unambiguous key. Then land again, show the contract and get it agreed, and rebuild.
- Keys are not affected: the key checks compare exact text with EXACT, not with COUNTIFS or MATCH ([checks.md](checks.md)).
- A label that is also a value of its column but that Excel does not match with itself is the opposite case: Excel leaves its rows out ([A label that Excel's criteria matching does not match with itself](#a-label-that-excels-criteria-matching-does-not-match-with-itself)).

### A label that Excel's criteria matching does not match with itself

- Evidence: plugin, v8 (round 7 review C-sol-1). A reviewer gave the starter the text category `TRUE` with +300 in January and -300 in February, and `East` with +200 in January. Excel's SUMIFS returned 0 for `TRUE` in both months, and every check held, because the rows left out add up to 0 over the months. The test `test_v8_criteria.py::test_true_label_whose_amounts_net_to_zero_fails_build_and_check` repeats this case in Excel 16 on the build PC.
- Evidence: plugin, v9 (round 8 reviews A-aera-1 and C-sol-2). Two reviewers gave the starter the categories `A~*` (+300 in January, -300 in February) and `A~?` (+400 in January, -400 in February). Excel's SUMIFS returned 0 for both labels in both months, and `build`, `audit` and `check` exited 0 in strict mode. The test `test_v9_criteria.py::test_tilde_wildcard_labels_netting_to_zero_fail_build_and_check` repeats this case in Excel 16 on the build PC.
- Three named cases:
  1. The texts `TRUE` and `FALSE`, in any case (`true`, `False`): Excel reads the criterion as the logical value TRUE or FALSE. A logical value matches only a cell that holds a logical value, not a cell that holds the text. A fetch column of a raw text column (`INDEX(...)&""`) always holds text.
  2. A label taken from a cell (not typed in the formula) that starts with `=`, `<` or `>`: Excel reads the criterion as a comparison. The label `<100` counts the numbers below 100, not the cells that hold the text `<100`. A criterion typed in the formula that starts with an operator is taken as meant and is not compared.
  3. A label that holds `~*` or `~?`: Excel reads `~*` and `~?` in a criterion as a literal `*` and `?`. The criterion `A~*` matches the cell that holds `A*`, not the cell that holds `A~*`. A criterion typed in the formula that holds `*` or `?` is taken as meant and is not compared.
- Every other label that Excel's criteria matching does not match with itself is reported too, with the general reason "Excel's criteria matching does not match this text with itself". Examples: `A~b*` (Excel drops the `~`, so the criterion matches `Abc`) and `A~~?` (Excel reads `~~` as one `~`, so the criterion matches `A~x`). A label that holds `~` and no `*` or `?` matches itself (`A~b`, `A~~`, `~`), and so does a label with `*` or `?` and no `~` (`A*` matches the text `A*`; it can also match other values: rule 3 above).
- Measured in Excel 16 on the build PC (2026-11-26): COUNTIF, COUNTIFS and SUMIFS over one cell gave the same answer as the plugin's rules on 81 pairs of a criterion and a cell value, among them every label above against itself (test `test_v9_criteria.py::test_tilde_rules_follow_excel_countif`).
- Why the rows are left out: the block's label is the text `TRUE`, and the criteria column holds the text `TRUE` on those rows. Excel compares the column with the logical value TRUE, so no row matches, and the label's sum and count are 0. The label `A~*` is the same: Excel looks for the text `A*`, which no row holds. When the rows left out add up to 0, the block total still equals the raw total and every check holds.
- `build` and `check` warn D-CRITERIA (strict mode exits 1) with the reason key `self_mismatch`. The warning names the block, the label, the column and its first raw row, gives the reason, and says that the function leaves out the rows with this label (plugin, v8 and v9). The finding's data field `case` is `logical`, `operator`, `tilde` or `other`.
- The fix names the pattern that fits the label: the helper key with a fixed prefix for `TRUE`, `FALSE` and a label that starts with an operator; the helper keys that replace `~`, `*` and `?` for a label that holds `~` together with `*` or `?` ([Helper keys for a label that holds a tilde](#helper-keys-for-a-label-that-holds-a-tilde)). A fixed prefix does not change how Excel reads `~`, `*` and `?`: `k_A~*` still does not match itself.

#### The helper key with a fixed prefix (TRUE, FALSE and operators)

- The fix, in formulas only: give both sides a helper key with a fixed prefix. A helper column holds `"k_"&` the label column, and the block's criterion is `"k_"&` the label. Then the criterion is `k_TRUE` or `k_<100`: a text that is neither a logical value nor a comparison, so it matches the key cells that hold the same text.
- The prefix also keeps numbers apart (`k_001` and `k_1` are two keys). It does not change rules 2 and 3 above: `k_North` still matches `k_north`, a `*` or `?` in a label is still a wildcard, and a `~` before a character still makes it plain when the label holds `*` or `?`.
- The labels on the block stay as they are (`TRUE`, `East`); only the criterion adds the prefix. Measured in Excel 16 on the build PC with the reviewer's data: `build --strict`, `audit --strict` and `check --strict` exit 0, and every category-month sum equals the sum computed in Python from the CSV (test `test_v8_criteria.py::test_helper_key_form_passes_and_equals_python`).

| After | Holds | Formula | Depth |
| --- | --- | --- | --- |
| `h_sales!K2` | the key of the row: `k_` followed by its region | `="k_"&D2` | 0 |
| `calc!D7` | the amount of the region in B7 for the month in D6, matched on the key | `=SUMIFS(h_sales!$F:$F,h_sales!$K:$K,"k_"&$B7,h_sales!$G:$G,D$6)` | 1 |

```json
{"helper_columns": [
  {"id": "region_key", "label": "Region key with a fixed prefix", "unit": "key", "format": "text", "expr": "\"k_\"&{this:region}"}],
 "block": {"cell": "SUMIFS({h:sales.amount},{h:sales.region_key},\"k_\"&{row},{h:sales.month_end},{col})"}}
```

- The helper column goes into the `columns` of the helper sheet `h_sales`; the cell replaces the `cell` of the block that sums by region. Column letters are examples: `build` assigns them.

#### Helper keys for a label that holds a tilde

- For a label that holds `~` together with `*` or `?` (case 3 and the other labels above, for example `A~*`, `A~?` and `A~b*`): give both sides helper keys that replace `~`, `*` and `?` by fixed texts, one SUBSTITUTE per helper column. The key holds none of the three signs, so Excel compares it as plain text, and the key of each label matches the key cells of its own rows.
- One SUBSTITUTE per helper column: three SUBSTITUTE calls inside one another have depth 3, and the limit is 2. Each step reads the step before it.
- The steps, on both sides: step 1 writes `k_` in front and replaces `~` by `_t_`; step 2 replaces `*` by `_s_`; step 3 replaces `?` by `_q_`. `A~*` becomes `k_A_t__s_` and `A~?` becomes `k_A_t__q_`. The prefix `k_` makes the same key fit `TRUE`, `FALSE` and labels that start with an operator, for a column that holds both kinds.
- The texts `_t_`, `_s_` and `_q_` must not occur in a label of the column: `A~` and `A_t_` would get the same key. The check `region_keys_rows` below then fails, because the rows of both labels are counted twice.
- The row side: three helper columns on `h_sales`. The label side: three grids of one column on the `calc` sheet, before the block that sums. The first grid reads the label (`{row}`), the second and the third read the grid before them (`{g:<grid>}`), and the block that sums reads the key of its row with `{grow:region_keys}` (the one cell of that row of the third grid). Every block needs a check of severity error (W-NO-CHECK): the check `region_keys_signs` reads the three grids.
- Two checks prove the keys:
  - `region_keys_rows` (equal): the rows counted under the keys of the block equal the rows of the raw table. It fails when the keys leave a row out, also when the amounts of the rows left out add up to 0, and when two labels get the same key. It also fails when a raw row holds a label that the block does not list.
  - `region_keys_signs` (zero): the keys of the three grids that still hold the sign their step replaces (`"*~~*"` counts the texts that hold a plain `~`, `"*~**"` those that hold a plain `*`, `"*~?*"` those that hold a plain `?`).
- The labels on the block stay as they are (`A~*`, `East`). Measured in Excel 16 on the build PC with the reviewers' data: `build --strict`, `audit --strict` and `check --strict` exit 0, Excel's sums for `A~*` are +300 in January and -300 in February, and every category-month sum equals the sum computed in Python from the CSV (test `test_v9_criteria.py::test_helper_keys_for_tilde_labels_pass_and_equal_python`). Not tested in Google Sheets.

| After | Holds | Formula | Depth |
| --- | --- | --- | --- |
| `h_sales!K2` | step 1 of the row's key: `k_` and the region with `~` replaced | `="k_"&SUBSTITUTE(D2,"~","_t_")` | 1 |
| `h_sales!L2` | step 2: `*` replaced | `=SUBSTITUTE(K2,"*","_s_")` | 1 |
| `h_sales!M2` | the row's key: `?` replaced | `=SUBSTITUTE(L2,"?","_q_")` | 1 |
| `calc!D6` | step 1 of the key of the region in B6 | `="k_"&SUBSTITUTE($B6,"~","_t_")` | 1 |
| `calc!D13` | step 2 | `=SUBSTITUTE(D6,"*","_s_")` | 1 |
| `calc!D20` | the key of the region in B20 | `=SUBSTITUTE(D13,"?","_q_")` | 1 |
| `calc!D27` | the amount of the region in B27 for the month in D26, matched on the key in D20 | `=SUMIFS(h_sales!$F:$F,h_sales!$M:$M,$D20:$D20,h_sales!$G:$G,D$26)` | 1 |
| `checks!D14` | the rows counted under the keys | `=SUMPRODUCT(COUNTIFS(h_sales!$M:$M,calc!$D$20:$D$23))` | 2 |
| `checks!D15` | the keys that still hold a replaced sign | `=COUNTIF(calc!$D$6:$D$9,"*~~*")+COUNTIF(calc!$D$13:$D$16,"*~**")+COUNTIF(calc!$D$20:$D$23,"*~?*")` | 1 |

```json
{"helper_columns": [
  {"id": "region_k1", "label": "Region key, step 1: k_ and ~ replaced", "unit": "key", "format": "text", "expr": "\"k_\"&SUBSTITUTE({this:region},\"~\",\"_t_\")"},
  {"id": "region_k2", "label": "Region key, step 2: * replaced", "unit": "key", "format": "text", "expr": "SUBSTITUTE({this:region_k1},\"*\",\"_s_\")"},
  {"id": "region_key", "label": "Region key: ? replaced", "unit": "key", "format": "text", "expr": "SUBSTITUTE({this:region_k2},\"?\",\"_q_\")"}],
 "blocks": [
  {"id": "region_keys_1", "title": "Region key, step 1", "unit": "key", "format": "text", "rows": {"in": "regions", "label": "Region"}, "cell": "\"k_\"&SUBSTITUTE({row},\"~\",\"_t_\")"},
  {"id": "region_keys_2", "title": "Region key, step 2", "unit": "key", "format": "text", "rows": {"same_as": "region_keys_1"}, "cols": {"same_as": "region_keys_1"}, "cell": "SUBSTITUTE({g:region_keys_1},\"*\",\"_s_\")"},
  {"id": "region_keys", "title": "Region key", "unit": "key", "format": "text", "rows": {"same_as": "region_keys_1"}, "cols": {"same_as": "region_keys_1"}, "cell": "SUBSTITUTE({g:region_keys_2},\"?\",\"_q_\")"}],
 "checks": [
  {"id": "region_keys_rows", "kind": "equal", "label": "Rows counted under the region keys equal the raw rows", "actual": "SUMPRODUCT(COUNTIFS({h:sales.region_key},{blk:region_keys}))", "expected": "COUNT({raw:sales.amount})"},
  {"id": "region_keys_signs", "kind": "zero", "label": "Region keys that still hold ~, * or ?", "actual": "COUNTIF({blk:region_keys_1},\"*~~*\")+COUNTIF({blk:region_keys_2},\"*~**\")+COUNTIF({blk:region_keys},\"*~?*\")"}],
 "block": {"cell": "SUMIFS({h:sales.amount},{h:sales.region_key},{grow:region_keys},{h:sales.month_end},{col})"}}
```

- The helper columns go into the `columns` of the helper sheet `h_sales`, the three grids into the `blocks` of the `calc` sheet before the block that sums by region, and the checks into the `checks` of the checks sheet; the cell replaces the `cell` of the block that sums by region. Column letters and rows are examples: `build` assigns them.

## Common mistakes

- Wrapping INDEX and MATCH in IFERROR in one cell: depth 3. Use H5.
- Moving a part out of the second or third argument of IF into a helper without the IF's condition: the helper is calculated for every input and can hold an error value (such as `#DIV/0!`) where the original showed none. Carry the condition ([A part in a branch of IF](#a-part-in-a-branch-of-if)).
- Moving a step into LET: LET is flagged (F-FUNC-FLAGGED) and keeps the step out of the grid. Use a helper cell.
- Replacing nested IFs with IFS, SWITCH or CHOOSE: all three are flagged. Use a table and MATCH and INDEX (H6).
- Writing a helper column beside a raw table or on the raw sheet: R-FORMULA (error), and a Power Query refresh moves and breaks such formulas (platforms B2.6, confirmed; [excel-power-query.md](excel-power-query.md)). Helpers go on `h_<table>`.
- Building a range with OFFSET or INDIRECT: both are refused (F-FUNC-REFUSED). Read whole columns instead.
- Typing a rate, a month or a bound into the helper formula: F-LITERAL. Put it on the inputs sheet.
- Replacing a "changes the result" pattern (H3, H4, H5, H9) without telling the user which values change.
- Leaving a flag that counts a problem without a check: nothing then fails when the problem occurs.
- Summing by a code that the data write with and without leading zeros, or in two cases: Excel's criteria matching adds both under one label (D-CRITERIA). Make the labels unambiguous in the contract's source expression or in a helper column.
- Summing by a label that is the text TRUE or FALSE, a label from a cell that starts with =, < or >, or a label that holds ~* or ~?: Excel leaves its rows out (D-CRITERIA, reason `self_mismatch`). Use a helper key with a fixed prefix on both sides for TRUE, FALSE and the operators, and helper keys that replace ~, * and ? on both sides for a label that holds ~ together with * or ? ([A label that Excel's criteria matching does not match with itself](#a-label-that-excels-criteria-matching-does-not-match-with-itself)).

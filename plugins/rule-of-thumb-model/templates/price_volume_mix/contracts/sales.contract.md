# Column contract: sales

- Purpose: Order lines of the prior and the current period for the price-volume-mix bridge
- Source: connector power_query; system BigQuery; object proj.sales.order_lines; from proj.sales.order_lines o; where o.fiscal_year IN (2025, 2026) AND o.status = 'invoiced'
- Grain: one row per order line
- Key: line_id (unique on every row)
- Raw sheet: raw_sales (Excel table tbl_raw_sales): header in row 1 from A1, one record per row from row 2, landed values only
- Proposal: proposed from the sample sales.csv (250 rows, 5 columns)

| # | Column | Type | Unit | Format | Null rule | Allowed values | Source expression | Description |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | line_id | text | - | default | never | - | o.line_id | order line id |
| 2 | period | text | - | default | never | CY, PY | IF(o.fiscal_year = 2025, 'PY', 'CY') | PY = prior year, CY = current year (a label, not a calculation: the contract's scope has these two years) |
| 3 | product | text | - | default | never | Basic, Max, Plus, Pro | o.product_name | product |
| 4 | qty | integer | units | default | never | - | o.quantity | units on the line |
| 5 | revenue | number | KRW | default | never | - | o.net_revenue_krw | net revenue of the line in KRW |

- Format: the style dictionary key of the column's display format on the raw sheet; default means the format of the column's type.
- Expected rows: from 1 to 200,000
- Control total: sum of qty = 2,607 (tolerance 0)
- Control total: sum of revenue = 102,662,100 (tolerance 0)
- Refresh: an empty result is not allowed

Sample profile (describes the sample only; these numbers are not controls):

| Column | Rows | Nulls | Distinct | Min | Max | Sum | Examples |
| --- | --- | --- | --- | --- | --- | --- | --- |
| line_id | 250 | 0 | 250 | - | - | - | O00001, O00002, O00003 |
| period | 250 | 0 | 2 | - | - | - | PY, CY |
| product | 250 | 0 | 4 | - | - | - | Pro, Plus, Basic |
| qty | 250 | 0 | 20 | 1 | 20 | 2,607 | 1, 2, 16 |
| revenue | 250 | 0 | 220 | 19500 | 2082400 | 102,662,100 | 59300, 70000, 324800 |

Points to confirm:

- key candidates in the sample (unique and never null): line_id

Agreed by template (plugin author) on 2026-11-25T03:13:09+09:00 through example: 'agreed: example agreement shipped with this template; no user gave it. A model made from this folder asks its user again.'

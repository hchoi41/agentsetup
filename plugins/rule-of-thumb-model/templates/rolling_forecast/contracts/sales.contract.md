# Column contract: sales

- Purpose: Sales lines of the closed months for the rolling forecast
- Source: connector power_query; system BigQuery; object proj.sales.lines; from proj.sales.lines s; where s.sale_date BETWEEN '2026-01-01' AND '2026-03-31'
- Grain: one row per sales line
- Key: line_id (unique on every row)
- Raw sheet: raw_sales (Excel table tbl_raw_sales): header in row 1 from A1, one record per row from row 2, landed values only
- Proposal: proposed from the sample sales.csv (305 rows, 4 columns)

| # | Column | Type | Unit | Format | Null rule | Allowed values | Source expression | Description |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | line_id | text | - | default | never | - | s.line_id | sales line id |
| 2 | sale_date | date | date | default | never | - | s.sale_date | date of the sale |
| 3 | product | text | - | default | never | Alpha, Beta, Delta, Gamma | s.product_name | product |
| 4 | revenue | number | KRW | default | never | - | s.net_revenue_krw | net revenue in KRW |

- Format: the style dictionary key of the column's display format on the raw sheet; default means the format of the column's type.
- Expected rows: from 1 to 100,000
- Control total: sum of revenue = 307,355,000 (tolerance 0)
- Refresh: an empty result is not allowed

Sample profile (describes the sample only; these numbers are not controls):

| Column | Rows | Nulls | Distinct | Min | Max | Sum | Examples |
| --- | --- | --- | --- | --- | --- | --- | --- |
| line_id | 305 | 0 | 305 | - | - | - | S00001, S00002, S00003 |
| sale_date | 305 | 0 | 81 | 2026-01-01 | 2026-03-27 | - | 2026-01-28, 2026-01-15, 2026-01-17 |
| product | 305 | 0 | 4 | - | - | - | Delta, Beta, Alpha |
| revenue | 305 | 0 | 214 | 10,000 | 1,995,000 | 307,355,000 | 815000, 100000, 1720000 |

Points to confirm:

- key candidates in the sample (unique and never null): line_id

Agreed by template (plugin author) on 2026-11-25T03:10:47+09:00 through example: 'agreed: example agreement shipped with this template; no user gave it. A model made from this folder asks its user again.'

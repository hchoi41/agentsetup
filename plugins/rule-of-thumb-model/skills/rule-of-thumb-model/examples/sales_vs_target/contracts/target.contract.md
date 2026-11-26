# Column contract: target

- Purpose: Monthly sales target per region
- Source: connector power_query; system BigQuery; object proj.plan.sales_targets; from proj.plan.sales_targets t; where t.version = 'FY2026 v3' AND t.month_end BETWEEN '2026-01-31' AND '2026-06-30'
- Grain: one row per region and month
- Key: region, month_end (unique on every row)
- Raw sheet: raw_target (Excel table tbl_raw_target): header in row 1 from A1, one record per row from row 2, landed values only
- Proposal: proposed from the sample target.csv (24 rows, 3 columns)

| # | Column | Type | Unit | Format | Null rule | Allowed values | Source expression | Description |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | region | text | - | default | never | East, North, South, West | t.region | sales region |
| 2 | month_end | date | date | default | never | - | LAST_DAY(t.month) | last day of the target month |
| 3 | target_amount | number | KRW | default | never | - | t.target_net_krw | sales target for the region and month, net of VAT, in KRW |

- Format: the style dictionary key of the column's display format on the raw sheet; default means the format of the column's type.
- Expected rows: exactly 24
- Control total: sum of target_amount = 268,000,000 (tolerance 0)
- Refresh: an empty result is not allowed

Sample profile (describes the sample only; these numbers are not controls):

| Column | Rows | Nulls | Distinct | Min | Max | Sum | Examples |
| --- | --- | --- | --- | --- | --- | --- | --- |
| region | 24 | 0 | 4 | - | - | - | East, North, South |
| month_end | 24 | 0 | 6 | 2026-01-31 | 2026-06-30 | - | 2026-01-31, 2026-02-28, 2026-03-31 |
| target_amount | 24 | 0 | 14 | 3,000,000 | 23,000,000 | 268,000,000 | 12000000, 15000000, 13000000 |

Points to confirm:

- key candidates in the sample (unique and never null): region + month_end

Agreed by worked example (plugin author) on 2026-11-25T02:57:13+09:00 through example: 'agreed: example agreement shipped with the worked example; no user gave it. A model made from this folder asks its user again.'

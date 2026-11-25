# Column contract: lines

- Purpose: Expense lines for the starter model
- Source: connector power_query; system BigQuery; object proj.fin.expense_lines; from proj.fin.expense_lines e; where e.line_date BETWEEN '2026-01-01' AND '2026-03-31'
- Grain: one row per expense line
- Key: line_id (unique on every row)
- Raw sheet: raw_lines (Excel table tbl_raw_lines): header in row 1 from A1, one record per row from row 2, landed values only
- Proposal: proposed from the sample lines.csv (60 rows, 4 columns)

| # | Column | Type | Unit | Format | Null rule | Allowed values | Source expression | Description |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | line_id | text | - | default | never | - | e.line_id | expense line id |
| 2 | line_date | date | date | default | never | - | e.line_date | date of the expense |
| 3 | category | text | - | default | never | Rent, Staff, Supplies, Travel | e.category | expense category |
| 4 | amount | number | KRW | default | never | - | e.amount_krw | amount in KRW |

- Format: the style dictionary key of the column's display format on the raw sheet; default means the format of the column's type.
- Expected rows: from 1 to 10,000
- Control total: sum of amount = 57,514,500 (tolerance 0)
- Refresh: an empty result is not allowed

Sample profile (describes the sample only; these numbers are not controls):

| Column | Rows | Nulls | Distinct | Min | Max | Sum | Examples |
| --- | --- | --- | --- | --- | --- | --- | --- |
| line_id | 60 | 0 | 60 | - | - | - | L0001, L0002, L0003 |
| line_date | 60 | 0 | 43 | 2026-01-02 | 2026-03-31 | - | 2026-01-02, 2026-02-27, 2026-02-01 |
| category | 60 | 0 | 4 | - | - | - | Staff, Supplies, Travel |
| amount | 60 | 0 | 59 | 49000 | 1981500 | 57,514,500 | 655000, 1158000, 1938500 |

Points to confirm:

- key candidates in the sample (unique and never null): line_id

Agreed by template (plugin author) on 2026-11-25T03:07:53+09:00 through example: 'agreed: example agreement shipped with this template; no user gave it. A model made from this folder asks its user again.'

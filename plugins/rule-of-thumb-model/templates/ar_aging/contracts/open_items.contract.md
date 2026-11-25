# Column contract: open_items

- Purpose: Open receivables at the aging date
- Source: connector power_query; system BigQuery; object proj.ar.open_items; from proj.ar.open_items a; where a.snapshot_date = '2026-06-30' AND a.open_amount_krw <> 0
- Grain: one row per open invoice
- Key: invoice_id (unique on every row)
- Raw sheet: raw_open_items (Excel table tbl_raw_open_items): header in row 1 from A1, one record per row from row 2, landed values only
- Proposal: proposed from the sample open_items.csv (240 rows, 5 columns)

| # | Column | Type | Unit | Format | Null rule | Allowed values | Source expression | Description |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | invoice_id | text | - | default | never | - | a.invoice_id | invoice number |
| 2 | customer | text | - | default | never | Arden, Birch, Cobalt, Dunmore, Elm, Fairview, Granite, Harbor | a.customer_name | customer |
| 3 | invoice_date | date | date | default | never | - | a.invoice_date | invoice date |
| 4 | due_date | date | date | default | never | - | a.due_date | due date under the payment terms |
| 5 | open_amount | number | KRW | default | never | - | a.open_amount_krw | amount still open at the snapshot date, in KRW |

- Format: the style dictionary key of the column's display format on the raw sheet; default means the format of the column's type.
- Expected rows: from 1 to 100,000
- Control total: sum of open_amount = 351,130,000 (tolerance 0)
- Refresh: an empty result is not allowed

Sample profile (describes the sample only; these numbers are not controls):

| Column | Rows | Nulls | Distinct | Min | Max | Sum | Examples |
| --- | --- | --- | --- | --- | --- | --- | --- |
| invoice_id | 240 | 0 | 240 | - | - | - | INV00001, INV00002, INV00003 |
| customer | 240 | 0 | 8 | - | - | - | Arden, Granite, Elm |
| invoice_date | 240 | 0 | 118 | 2026-01-18 | 2026-06-28 | - | 2026-04-09, 2026-03-16, 2026-06-19 |
| due_date | 240 | 0 | 128 | 2026-02-17 | 2026-08-27 | - | 2026-05-09, 2026-05-15, 2026-08-18 |
| open_amount | 240 | 0 | 237 | 17000 | 2995000 | 351,130,000 | 161000, 515000, 231000 |

Points to confirm:

- key candidates in the sample (unique and never null): invoice_id

Agreed by template (plugin author) on 2026-11-25T03:13:41+09:00 through example: 'agreed: example agreement shipped with this template; no user gave it. A model made from this folder asks its user again.'

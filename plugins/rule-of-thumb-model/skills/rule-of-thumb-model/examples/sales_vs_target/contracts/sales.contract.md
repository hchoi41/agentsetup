# Column contract: sales

- Purpose: Invoice lines for the sales-versus-target model
- Source: connector power_query; system BigQuery; object proj.ds.invoice_lines; from proj.ds.invoice_lines l JOIN proj.ds.invoices i USING (invoice_id); where i.posted AND i.inv_date BETWEEN '2026-01-01' AND '2026-06-30'
- Grain: one row per invoice line
- Key: invoice_id, line_no (unique on every row)
- Raw sheet: raw_sales (Excel table tbl_raw_sales): header in row 1 from A1, one record per row from row 2, landed values only
- Proposal: proposed from the sample sales.csv (600 rows, 6 columns)

| # | Column | Type | Unit | Format | Null rule | Allowed values | Source expression | Description |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | invoice_id | text | - | default | never | - | CAST(i.invoice_id AS STRING) | invoice number, six digits, leading zeros kept |
| 2 | line_no | integer | count | default | never | - | l.line_no | line number within the invoice, from 1 |
| 3 | inv_date | date | date | default | never | - | i.inv_date | invoice date |
| 4 | region | text | - | default | never | East, North, South, West | i.region | sales region of the customer |
| 5 | product | text | - | default | allowed | P1, P2, P3, P4, P5, P6 | l.product_code | product code; empty when the line has no product (service lines) |
| 6 | amount | number | KRW | default | never | - | l.net_amount | line amount net of VAT, in KRW |

- Format: the style dictionary key of the column's display format on the raw sheet; default means the format of the column's type.
- Expected rows: from 1 to 5,000
- Control total: sum of amount = 264,697,350 (tolerance 0)
- Refresh: an empty result is not allowed

Sample profile (describes the sample only; these numbers are not controls):

| Column | Rows | Nulls | Distinct | Min | Max | Sum | Examples |
| --- | --- | --- | --- | --- | --- | --- | --- |
| invoice_id | 600 | 0 | 322 | - | - | - | 000101, 000102, 000103 |
| line_no | 600 | 0 | 3 | 1 | 3 | 954 | 1, 2, 3 |
| inv_date | 600 | 0 | 152 | 2026-01-01 | 2026-06-30 | - | 2026-01-03, 2026-06-09, 2026-06-13 |
| region | 600 | 0 | 4 | - | - | - | North, West, South |
| product | 600 | 27 | 6 | - | - | - | P1, P6, P3 |
| amount | 600 | 0 | 590 | 10750 | 897700 | 264,697,350 | 858800, 791050, 492100 |

Points to confirm:

- key candidates in the sample (unique and never null): invoice_id + line_no

Agreed by worked example (plugin author) on 2026-11-25T02:57:11+09:00 through example: 'agreed: example agreement shipped with the worked example; no user gave it. A model made from this folder asks its user again.'

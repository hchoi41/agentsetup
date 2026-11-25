-- rotm-stamp: contract sha256:8498b10038384772cc814369b50538a6fda636bd0ebdadac63e5b4958d846e12; body sha256:180d6385ae1ee9737ad8391a9b68034ae8641bb4fc5c15afbcba305b7a4be3cb
-- rule-of-thumb-model: SQL for raw table 'sales' (contracts/sales.contract.json).
-- Contract agreed by worked example (plugin author) on 2026-11-25T02:57:11+09:00.
-- Shape only (spec 2.2 P2): select, rename, cast and filter to the contract. Every calculation after
-- landing is a workbook formula. Column names and order are the contract's.
-- Dialect: GoogleSQL (BigQuery, Connected Sheets). For Power Query, adapt it to the source's SQL.
SELECT
  CAST(i.invoice_id AS STRING) AS invoice_id,
  l.line_no AS line_no,
  i.inv_date AS inv_date,
  i.region AS region,
  l.product_code AS product,
  l.net_amount AS amount
FROM proj.ds.invoice_lines l JOIN proj.ds.invoices i USING (invoice_id)
WHERE i.posted AND i.inv_date BETWEEN '2026-01-01' AND '2026-06-30'
ORDER BY invoice_id, line_no

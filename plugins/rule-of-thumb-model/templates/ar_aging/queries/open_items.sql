-- rotm-stamp: contract sha256:646c2d36ebd3520e4d8344a7a1ad0a7a611b66223193a9dee3760118216abb8f; body sha256:5144cd68c2bfab0fce0161bbf5f5bf164286024cae773ddcf2fca0a48cff1e0c
-- rule-of-thumb-model: SQL for raw table 'open_items' (contracts/open_items.contract.json).
-- Contract agreed by template (plugin author) on 2026-11-25T03:13:41+09:00.
-- Shape only (spec 2.2 P2): select, rename, cast and filter to the contract. Every calculation after
-- landing is a workbook formula. Column names and order are the contract's.
-- Dialect: GoogleSQL (BigQuery, Connected Sheets). For Power Query, adapt it to the source's SQL.
SELECT
  a.invoice_id AS invoice_id,
  a.customer_name AS customer,
  a.invoice_date AS invoice_date,
  a.due_date AS due_date,
  a.open_amount_krw AS open_amount
FROM proj.ar.open_items a
WHERE a.snapshot_date = '2026-06-30' AND a.open_amount_krw <> 0
ORDER BY invoice_id

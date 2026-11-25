-- rotm-stamp: contract sha256:5a0d75bc1dc92d3bb27c43735848d3bf3d03d7065f674adf7f663c42571d0d46; body sha256:6a85e243144bfcaef1f76ed8181c72254ae9d897f9a65459116021047b02f725
-- rule-of-thumb-model: SQL for raw table 'sales' (contracts/sales.contract.json).
-- Contract agreed by template (plugin author) on 2026-11-25T03:13:09+09:00.
-- Shape only (spec 2.2 P2): select, rename, cast and filter to the contract. Every calculation after
-- landing is a workbook formula. Column names and order are the contract's.
-- Dialect: GoogleSQL (BigQuery, Connected Sheets). For Power Query, adapt it to the source's SQL.
SELECT
  o.line_id AS line_id,
  IF(o.fiscal_year = 2025, 'PY', 'CY') AS period,
  o.product_name AS product,
  o.quantity AS qty,
  o.net_revenue_krw AS revenue
FROM proj.sales.order_lines o
WHERE o.fiscal_year IN (2025, 2026) AND o.status = 'invoiced'
ORDER BY line_id

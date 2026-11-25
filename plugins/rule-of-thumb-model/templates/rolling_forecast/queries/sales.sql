-- rotm-stamp: contract sha256:498ce1b97012145d6805e01f37a9844f5471a8d1133942d2ea115740d72bfcf9; body sha256:9625f842fc310d4816b7a2442fd1b444318272f84d9c3a9d5788988be5597bf5
-- rule-of-thumb-model: SQL for raw table 'sales' (contracts/sales.contract.json).
-- Contract agreed by template (plugin author) on 2026-11-25T03:10:47+09:00.
-- Shape only (spec 2.2 P2): select, rename, cast and filter to the contract. Every calculation after
-- landing is a workbook formula. Column names and order are the contract's.
-- Dialect: GoogleSQL (BigQuery, Connected Sheets). For Power Query, adapt it to the source's SQL.
SELECT
  s.line_id AS line_id,
  s.sale_date AS sale_date,
  s.product_name AS product,
  s.net_revenue_krw AS revenue
FROM proj.sales.lines s
WHERE s.sale_date BETWEEN '2026-01-01' AND '2026-03-31'
ORDER BY line_id

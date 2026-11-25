-- rotm-stamp: contract sha256:20d21eb3ac6f06197c90dff9a243f0e5211b11beec73b5761ce302c914c20b0d; body sha256:5eaaf430057bd840667ab019098fc28deb39bfd822812554eb95a19cf4a874df
-- rule-of-thumb-model: SQL for raw table 'target' (contracts/target.contract.json).
-- Contract agreed by worked example (plugin author) on 2026-11-25T02:57:13+09:00.
-- Shape only (spec 2.2 P2): select, rename, cast and filter to the contract. Every calculation after
-- landing is a workbook formula. Column names and order are the contract's.
-- Dialect: GoogleSQL (BigQuery, Connected Sheets). For Power Query, adapt it to the source's SQL.
SELECT
  t.region AS region,
  LAST_DAY(t.month) AS month_end,
  t.target_net_krw AS target_amount
FROM proj.plan.sales_targets t
WHERE t.version = 'FY2026 v3' AND t.month_end BETWEEN '2026-01-31' AND '2026-06-30'
ORDER BY region, month_end

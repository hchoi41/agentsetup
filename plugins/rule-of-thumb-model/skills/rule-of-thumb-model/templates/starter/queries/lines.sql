-- rotm-stamp: contract sha256:faec27f3b5574f095e80c760c494156ab84ae6dd8793e5fc01c8575f87b5314c; body sha256:189f82faf2c347fbe1ec8ea1b58a8f5ee3410b921309af4587614e866d42414b
-- rule-of-thumb-model: SQL for raw table 'lines' (contracts/lines.contract.json).
-- Contract agreed by template (plugin author) on 2026-11-25T03:07:53+09:00.
-- Shape only (spec 2.2 P2): select, rename, cast and filter to the contract. Every calculation after
-- landing is a workbook formula. Column names and order are the contract's.
-- Dialect: GoogleSQL (BigQuery, Connected Sheets). For Power Query, adapt it to the source's SQL.
SELECT
  e.line_id AS line_id,
  e.line_date AS line_date,
  e.category AS category,
  e.amount_krw AS amount
FROM proj.fin.expense_lines e
WHERE e.line_date BETWEEN '2026-01-01' AND '2026-03-31'
ORDER BY line_id

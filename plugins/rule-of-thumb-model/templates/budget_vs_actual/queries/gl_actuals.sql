-- rotm-stamp: contract sha256:876d191e002f95cbea3a42e438822a7c4f8dd453dcdc9b1781bcbf6eff2ed650; body sha256:4ab3d3e5653e45ff9478ca9698bf6fb0ac059788397b9477d83067f979a256d2
-- rule-of-thumb-model: SQL for raw table 'gl_actuals' (contracts/gl_actuals.contract.json).
-- Contract agreed by template (plugin author) on 2026-11-25T03:09:14+09:00.
-- Shape only (spec 2.2 P2): select, rename, cast and filter to the contract. Every calculation after
-- landing is a workbook formula. Column names and order are the contract's.
-- Dialect: GoogleSQL (BigQuery, Connected Sheets). For Power Query, adapt it to the source's SQL.
SELECT
  j.entry_id AS entry_id,
  j.posting_date AS posting_date,
  j.cost_centre AS cost_centre,
  CAST(j.account AS STRING) AS account,
  j.debit_krw - j.credit_krw AS amount
FROM proj.gl.journal_lines j
WHERE j.account BETWEEN '6100' AND '6499' AND j.posting_date BETWEEN '2026-01-01' AND '2026-06-30'
ORDER BY entry_id

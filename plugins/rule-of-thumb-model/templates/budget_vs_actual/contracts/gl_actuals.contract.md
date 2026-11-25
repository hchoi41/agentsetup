# Column contract: gl_actuals

- Purpose: Expense postings for the budget-versus-actual model
- Source: connector power_query; system BigQuery; object proj.gl.journal_lines; from proj.gl.journal_lines j; where j.account BETWEEN '6100' AND '6499' AND j.posting_date BETWEEN '2026-01-01' AND '2026-06-30'
- Grain: one row per ledger posting
- Key: entry_id (unique on every row)
- Raw sheet: raw_gl_actuals (Excel table tbl_raw_gl_actuals): header in row 1 from A1, one record per row from row 2, landed values only
- Proposal: proposed from the sample gl_actuals.csv (419 rows, 5 columns)

| # | Column | Type | Unit | Format | Null rule | Allowed values | Source expression | Description |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | entry_id | text | - | default | never | - | j.entry_id | journal entry line id |
| 2 | posting_date | date | date | default | never | - | j.posting_date | posting date |
| 3 | cost_centre | text | - | default | never | CC100, CC200, CC300, CC400 | j.cost_centre | cost centre code |
| 4 | account | text | - | default | never | 6100, 6200, 6300, 6400 | CAST(j.account AS STRING) | expense account code, kept as text |
| 5 | amount | number | KRW | default | never | - | j.debit_krw - j.credit_krw | debit minus credit in KRW; reversals are negative |

- Format: the style dictionary key of the column's display format on the raw sheet; default means the format of the column's type.
- Expected rows: from 1 to 100,000
- Control total: sum of amount = 171,636,600 (tolerance 0)
- Refresh: an empty result is not allowed

Sample profile (describes the sample only; these numbers are not controls):

| Column | Rows | Nulls | Distinct | Min | Max | Sum | Examples |
| --- | --- | --- | --- | --- | --- | --- | --- |
| entry_id | 419 | 0 | 419 | - | - | - | JE00001, JE00002, JE00003 |
| posting_date | 419 | 0 | 156 | 2026-01-02 | 2026-06-28 | - | 2026-01-11, 2026-01-06, 2026-01-04 |
| cost_centre | 419 | 0 | 4 | - | - | - | CC100, CC200, CC300 |
| account | 419 | 0 | 4 | - | - | - | 6400, 6300, 6100 |
| amount | 419 | 0 | 405 | -554900 | 1322400 | 171,636,600 | 167900, 366600, 653800 |

Points to confirm:

- key candidates in the sample (unique and never null): entry_id

Agreed by template (plugin author) on 2026-11-25T03:09:14+09:00 through example: 'agreed: example agreement shipped with this template; no user gave it. A model made from this folder asks its user again.'
